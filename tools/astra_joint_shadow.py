"""Read-only shadow collector and immutable forward research ledger.

The module is deliberately one-shot. It does not install a timer, open orders,
send notifications, call an LLM, or mutate FMZ production records.
"""

from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable
import urllib.parse
import urllib.request

from astra_joint_contract import ASSESSMENT_SCHEMA, FEATURE_GROUPS, PROTOCOL
from astra_joint_data import is_complete_minute_bar, normalize_kline
from astra_joint_dataset import canonical_hash, expiry_ms, payout, select_legs
from astra_joint_events import StreamingReplay
from astra_joint_projection import build_joint_projection, compute_assessment_hash, validate_assessment
from astra_joint_sources import save


MINUTE = 60_000
MAX_CLOSED_CACHE_MINUTES = 3_000
MAX_QUOTE_AGE_MS = 10_000
MAX_LEG_SKEW_MS = 5_000
PRIMARY_WIDTH = int(PROTOCOL.get("primary_width", 2000))
DEFAULT_MARGIN_LAMBDA = 0.951659
DAILY_PERIODS = {"day", "daily", "1d"}

Transport = Callable[[str, dict[str, Any]], tuple[Any, dict[str, Any], bytes]]


def now_ms() -> int:
    return int(time.time() * 1000)


def public_get(base: str, params: dict[str, Any]) -> tuple[Any, dict[str, Any], bytes]:
    url = base + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "Astra-readonly-shadow/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read()
    payload = json.loads(raw)
    if isinstance(payload, dict) and payload.get("error"):
        raise ValueError("public source error")
    return payload, {"url": url, "fetched_at_ms": now_ms(), "sha256": _sha256_bytes(raw)}, raw


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _ms_from_training_cutoff(value: Any) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    text = str(value or "2021-12-31").strip()
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _finite(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def estimate_margin_btc(
    width_usd: float,
    entry_price: float,
    *,
    quantity_btc: float = 1.0,
    margin_lambda: float = DEFAULT_MARGIN_LAMBDA,
) -> float:
    """Single-case calibrated margin scenario, scaled with quantity."""
    if width_usd <= 0 or entry_price <= 0 or quantity_btc <= 0 or margin_lambda <= 0:
        raise ValueError("margin inputs must be positive")
    return margin_lambda * width_usd / entry_price * quantity_btc


def return_on_margin(
    net_result_btc_per_unit: float,
    width_usd: float,
    entry_price: float,
    *,
    quantity_btc: float = 1.0,
    margin_lambda: float = DEFAULT_MARGIN_LAMBDA,
) -> float:
    margin = estimate_margin_btc(width_usd, entry_price, quantity_btc=quantity_btc, margin_lambda=margin_lambda)
    return (net_result_btc_per_unit * quantity_btc) / margin


class Ledger:
    """SQLite-backed append ledger."""

    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.folder / "state.sqlite", timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS requests(
                market TEXT,
                minute INTEGER,
                request_hash TEXT,
                status TEXT,
                requested_at_ms INTEGER,
                completed_at_ms INTEGER,
                error TEXT,
                PRIMARY KEY(market, minute)
            );
            CREATE TABLE IF NOT EXISTS bars(
                market TEXT,
                opened INTEGER,
                payload TEXT,
                PRIMARY KEY(market, opened)
            );
            CREATE TABLE IF NOT EXISTS decisions(
                id TEXT PRIMARY KEY,
                asof INTEGER,
                payload TEXT,
                hash TEXT
            );
            CREATE TABLE IF NOT EXISTS outcomes(
                id TEXT PRIMARY KEY,
                payload TEXT,
                hash TEXT
            );
            CREATE TABLE IF NOT EXISTS process_records(
                process_id TEXT,
                revision INTEGER,
                kind TEXT,
                asof INTEGER,
                phase TEXT,
                payload TEXT,
                hash TEXT,
                recorded_at_ms INTEGER,
                PRIMARY KEY(process_id, revision),
                UNIQUE(process_id, hash)
            );
            CREATE TABLE IF NOT EXISTS meta(
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        self._migrate_existing_tables()
        self.db.commit()

    def _migrate_existing_tables(self) -> None:
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(requests)")}
        wanted = {
            "request_hash": "TEXT",
            "requested_at_ms": "INTEGER",
            "completed_at_ms": "INTEGER",
            "error": "TEXT",
        }
        for name, column_type in wanted.items():
            if name not in columns:
                self.db.execute(f"ALTER TABLE requests ADD COLUMN {name} {column_type}")

    def reserve_request(self, market: str, minute: int, params: dict[str, Any], requested_at_ms: int) -> bool:
        request_hash = canonical_hash({"market": market, "minute": int(minute), "params": params})
        try:
            with self.db:
                self.db.execute(
                    """
                    INSERT INTO requests(market, minute, request_hash, status, requested_at_ms)
                    VALUES(?,?,?,?,?)
                    """,
                    (market, int(minute), request_hash, "reserved", int(requested_at_ms)),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def finish_request(self, market: str, minute: int, status: str, completed_at_ms: int, error: str | None = None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE requests SET status=?, completed_at_ms=?, error=? WHERE market=? AND minute=?",
                (status, int(completed_at_ms), error, market, int(minute)),
            )

    def request_rows(self) -> list[dict[str, Any]]:
        cursor = self.db.execute(
            "SELECT market, minute, status, request_hash, requested_at_ms, completed_at_ms, error FROM requests ORDER BY minute, market"
        )
        return [
            {
                "market": row[0],
                "minute": row[1],
                "status": row[2],
                "request_hash": row[3],
                "requested_at_ms": row[4],
                "completed_at_ms": row[5],
                "error": row[6],
            }
            for row in cursor
        ]

    def newest_bar_open(self, market: str) -> int | None:
        row = self.db.execute("SELECT MAX(opened) FROM bars WHERE market=?", (market,)).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def newest_closed_bar_open(self, market: str, at_ms: int) -> int | None:
        for opened, payload in self.db.execute("SELECT opened,payload FROM bars WHERE market=? ORDER BY opened DESC", (market,)):
            row = json.loads(payload)
            if is_complete_minute_bar(row, as_of_ms=int(at_ms)):
                return int(opened)
        return None

    def insert_bar(self, market: str, row: dict[str, Any]) -> None:
        opened = int(row["open_time_ms"])
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        existing = self.db.execute("SELECT payload FROM bars WHERE market=? AND opened=?", (market, opened)).fetchone()
        if existing and existing[0] != payload:
            raise ValueError(f"market archive revision detected for {market}:{opened}")
        self.db.execute("INSERT OR IGNORE INTO bars VALUES(?,?,?)", (market, opened, payload))

    def trim_bars(self, market: str, lower_open_ms: int) -> None:
        self.db.execute("DELETE FROM bars WHERE market=? AND opened<?", (market, int(lower_open_ms)))

    def bars(self, market: str) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT payload FROM bars WHERE market=? ORDER BY opened", (market,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def open_price(self, market: str, open_ms: int) -> float | None:
        row = self.db.execute("SELECT payload FROM bars WHERE market=? AND opened=?", (market, int(open_ms))).fetchone()
        if not row:
            return None
        return _finite(json.loads(row[0]).get("open"))

    def has_decision(self, identity: str) -> bool:
        return self.db.execute("SELECT 1 FROM decisions WHERE id=?", (identity,)).fetchone() is not None

    def record_decision(self, identity: str, asof: int, record: dict[str, Any]) -> bool:
        raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        prior = self.db.execute("SELECT hash FROM decisions WHERE id=?", (identity,)).fetchone()
        if prior:
            if prior[0] != digest:
                raise ValueError("immutable decision differs")
            return False
        with self.db:
            self.db.execute("INSERT INTO decisions VALUES(?,?,?,?)", (identity, int(asof), raw, digest))
        self.export_decisions()
        return True

    def decision(self, identity: str) -> dict[str, Any]:
        row = self.db.execute("SELECT payload FROM decisions WHERE id=?", (identity,)).fetchone()
        if not row:
            raise KeyError(identity)
        return json.loads(row[0])

    def record_outcome(self, identity: str, record: dict[str, Any]) -> bool:
        raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        prior = self.db.execute("SELECT hash FROM outcomes WHERE id=?", (identity,)).fetchone()
        if prior:
            if prior[0] != digest:
                raise ValueError("immutable outcome differs")
            return False
        with self.db:
            self.db.execute("INSERT INTO outcomes VALUES(?,?,?)", (identity, raw, digest))
        self.export_outcomes()
        return True

    def export_decisions(self) -> None:
        self._export_table("SELECT payload FROM decisions ORDER BY asof,id", "decisions.jsonl")

    def export_outcomes(self) -> None:
        self._export_table("SELECT payload FROM outcomes ORDER BY id", "outcomes.jsonl")

    def record_process(
        self,
        process_id: str,
        kind: str,
        asof: int,
        payload: dict[str, Any],
        recorded_at_ms: int,
        *,
        phase: str,
        allow_revision: bool = False,
        export: bool = True,
    ) -> dict[str, Any]:
        """Append a deterministic forward-process record without overwriting prior revisions."""
        base = {
            "schema": "astra_joint_forward_process_record@1.0.0",
            "process_id": str(process_id),
            "kind": str(kind),
            "as_of_ms": int(asof),
            "phase": str(phase),
            "payload": payload,
        }
        digest = hashlib.sha256(_json_bytes(base)).hexdigest()
        prior = self.db.execute(
            "SELECT revision FROM process_records WHERE process_id=? AND hash=?",
            (base["process_id"], digest),
        ).fetchone()
        if prior:
            return {"status": "duplicate", "revision": int(prior[0]), "hash": digest}
        row = self.db.execute(
            "SELECT MAX(revision) FROM process_records WHERE process_id=?",
            (base["process_id"],),
        ).fetchone()
        revision = int(row[0] or 0) + 1
        if revision > 1 and not allow_revision:
            return {"status": "identity_conflict_ignored", "revision": revision - 1, "hash": digest}
        record = {
            **base,
            "revision": revision,
            "recorded_at_ms": int(recorded_at_ms),
            "process_record_hash": digest,
            "append_only_note_cn": "前向影子过程记录；相同内容去重，后续过程变化追加为新版本，不覆盖旧记录。",
        }
        raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self.db:
            self.db.execute(
                """
                INSERT INTO process_records(process_id, revision, kind, asof, phase, payload, hash, recorded_at_ms)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (base["process_id"], revision, base["kind"], int(asof), base["phase"], raw, digest, int(recorded_at_ms)),
            )
        if export:
            self.export_process_records()
        return {
            "status": "inserted" if revision == 1 else "appended_revision",
            "revision": revision,
            "hash": digest,
        }

    def has_process_id(self, process_id: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM process_records WHERE process_id=? LIMIT 1",
            (str(process_id),),
        ).fetchone() is not None

    def process_rows(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT payload FROM process_records ORDER BY asof, kind, process_id, revision"
        ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def export_process_records(self) -> None:
        self._export_table(
            "SELECT payload FROM process_records ORDER BY asof, kind, process_id, revision",
            "process_ledger.jsonl",
        )

    def replay_snapshot(self) -> dict[str, Any] | None:
        value = self.db.execute("SELECT value FROM meta WHERE key=?", ("streaming_replay_snapshot",)).fetchone()
        return json.loads(value[0]) if value else None

    def save_replay_snapshot(self, snapshot: dict[str, Any]) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                ("streaming_replay_snapshot", json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
            )

    def _export_table(self, query: str, filename: str) -> None:
        dest = self.folder / filename
        temp = dest.with_suffix(dest.suffix + ".partial")
        with temp.open("w", encoding="utf-8", newline="\n") as out:
            for row in self.db.execute(query):
                out.write(row[0] + "\n")
        os.replace(temp, dest)

    def window(self, start_ms: int | None = None) -> dict[str, Any] | None:
        value = self.db.execute("SELECT value FROM meta WHERE key=?", ("forward_window",)).fetchone()
        if value:
            return json.loads(value[0])
        if start_ms is None:
            return None
        end = int(start_ms) + 90 * 86_400_000
        data = {
            "schema": "astra_joint_forward_window@1.0.0",
            "start_ms": int(start_ms),
            "end_ms": end,
            "quality_check_ms": int(start_ms) + 30 * 86_400_000,
            "min_delivery_days": 60,
            "min_complete_weeks": 12,
            "rule": "no performance-based extension or in-place model replacement",
        }
        with self.db:
            self.db.execute("INSERT INTO meta VALUES(?,?)", ("forward_window", json.dumps(data, sort_keys=True)))
        return data


def _raw_bytes(raw: Any, payload: Any) -> bytes:
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, str):
        return raw.encode("utf-8")
    return _json_bytes(payload)


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)[:160]


def _fetch_once(
    ledger: Ledger,
    raw_dir: Path,
    market: str,
    minute: int,
    base: str,
    params: dict[str, Any],
    transport: Transport,
    checked_at_ms: int,
) -> tuple[bool, Any | None, dict[str, Any] | None, str | None]:
    if not ledger.reserve_request(market, minute, params, checked_at_ms):
        return False, None, None, "already_attempted"
    try:
        payload, meta, raw = transport(base, params)
        raw_dir.mkdir(parents=True, exist_ok=True)
        stem = _safe_name(f"{market}-{minute}-{canonical_hash(params)[:12]}")
        (raw_dir / f"{stem}.json").write_bytes(_raw_bytes(raw, payload))
        save(raw_dir / f"{stem}.receipt.json", meta if isinstance(meta, dict) else {"meta": meta}, True)
        ledger.finish_request(market, minute, "complete", now_ms())
        return True, payload, meta if isinstance(meta, dict) else {"meta": meta}, None
    except Exception as exc:
        ledger.finish_request(market, minute, type(exc).__name__, now_ms(), str(exc))
        return True, None, None, type(exc).__name__


def _market_params(market: str, at_ms: int, last_open: int | None) -> dict[str, Any]:
    params: dict[str, Any] = {"symbol": "BTCUSDT", "interval": "1m"}
    if market == "um":
        params["limit"] = 1500 if last_open is None else 1000
        if last_open is not None:
            params["startTime"] = int(last_open) + MINUTE
    else:
        params["limit"] = 3
        start = (int(at_ms) // MINUTE - 2) * MINUTE
        if last_open is not None:
            start = max(start, int(last_open) + MINUTE)
        params["startTime"] = start
    return params


def _store_market_payload(ledger: Ledger, market: str, payload: Any, at_ms: int, closed_only=False) -> int:
    if not isinstance(payload, list):
        raise ValueError(f"{market} kline payload is not a list")
    inserted = 0
    with ledger.db:
        for source in payload:
            row = normalize_kline(source)
            if row is None:
                if closed_only:
                    raise ValueError(f"{market} kline payload includes an invalid closed-minute shape")
                continue
            if int(row["close_time_ms"]) >= int(at_ms):
                if closed_only:
                    continue
            if market == "um" and int(row["close_time_ms"]) >= int(at_ms):
                continue
            if market == "spot" and int(row["close_time_ms"]) >= int(at_ms):
                if closed_only:
                    continue
                row = {
                    "open_time_ms": int(row["open_time_ms"]),
                    "open_time": int(row["open_time_ms"]),
                    "close_time_ms": int(row["close_time_ms"]),
                    "close_time": int(row["close_time_ms"]),
                    "open": float(row["open"]),
                    "date_utc": row.get("date_utc"),
                    "forming_open_only": True,
                    "path_fields_usable": False,
                    "available_at_ms": int(at_ms),
                    "source_note_cn": "形成中分钟柱只用于读取本分钟开盘价；不得作为闭合OHLC路径证据。",
                }
            else:
                row["forming_open_only"] = False
                row["path_fields_usable"] = True
            if closed_only:
                prior=ledger.db.execute("SELECT payload FROM bars WHERE market=? AND opened=?",(market,row["open_time_ms"])).fetchone()
                if prior:
                    previous=json.loads(prior[0])
                    if previous.get("forming_open_only"):
                        if previous.get("open") != row.get("open"):
                            raise ValueError("forming price changed; cache upgrade refused")
                        # Replace only the incomplete local cache observation;
                        # original HTTP bytes and past decisions remain intact.
                        ledger.db.execute("DELETE FROM bars WHERE market=? AND opened=?",(market,row["open_time_ms"]))
            ledger.insert_bar(market, row)
            inserted += 1
        ledger.trim_bars(market, int(at_ms) - MAX_CLOSED_CACHE_MINUTES * MINUTE)
    return inserted


def _dte_hours(entry_ms: int) -> float:
    return (expiry_ms(int(entry_ms)) - int(entry_ms)) / 3_600_000.0


def _ordinary_dte(entry_ms: int) -> bool:
    dte = _dte_hours(entry_ms)
    return float(PROTOCOL["dte_min_exclusive_hours"]) < dte <= float(PROTOCOL["dte_max_inclusive_hours"])


def _delivery_fee_status(short: dict[str, Any], long: dict[str, Any]) -> dict[str, Any]:
    periods = {str(item.get("settlement_period") or "").strip().lower() for item in (short, long)}
    if periods and periods <= DAILY_PERIODS:
        return {
            "status": "exempt_daily_option",
            "fee_btc": 0.0,
            "basis_cn": "Deribit daily options delivery fee exempt; entry quote remains separate from final settlement.",
        }
    return {
        "status": "unknown",
        "fee_btc": None,
        "basis_cn": "非日周期或缺少周期身份，本轮不估算到期交割费用。",
    }


def _entry_fee_btc(short: dict[str, Any], long: dict[str, Any], bid: float, ask: float) -> float | None:
    rates = [_finite(short.get("taker_commission")), _finite(long.get("taker_commission"))]
    if any(rate is None or rate < 0 for rate in rates):
        return None
    return min(float(rates[0]), 0.125 * bid) + min(float(rates[1]), 0.125 * ask)


def _book_stamp(book: dict[str, Any]) -> float | None:
    return _finite(book.get("timestamp"))


def validate_quote_group(expected_names: set[str], books: dict[str, dict[str, Any]], checked_at_ms: int) -> str | None:
    if not expected_names:
        return "参考四腿尚未确认，未抓取报价。"
    missing = expected_names - set(books)
    if missing:
        return "四腿组报价不完整，未进入严格报价组。"
    stamps = [_book_stamp(books[name]) for name in expected_names]
    if any(stamp is None for stamp in stamps):
        return "四腿组报价缺少毫秒时间。"
    assert all(stamp is not None for stamp in stamps)
    if any(stamp > checked_at_ms for stamp in stamps):
        return "报价时间晚于检查时点，未采纳。"
    if any(checked_at_ms - stamp > MAX_QUOTE_AGE_MS for stamp in stamps):
        return "四腿组报价超过10秒新鲜度，未进入严格报价组。"
    if max(stamps) - min(stamps) > MAX_LEG_SKEW_MS:
        return "四腿组腿间时间差超过5秒，未进入严格报价组。"
    return None


def quote_side(
    side: str,
    short: dict[str, Any],
    long: dict[str, Any],
    books: dict[str, dict[str, Any]],
    checked_at_ms: int,
    *,
    unit: float = 1.0,
    group_error_cn: str | None = None,
) -> dict[str, Any]:
    del side
    if group_error_cn:
        return {"status": "insufficient", "reason_cn": group_error_cn}
    result = {"status": "insufficient", "reason_cn": "同期两腿报价尚未通过完整性检查。"}
    short_book = books.get(short["instrument_name"])
    long_book = books.get(long["instrument_name"])
    if not short_book or not long_book:
        return result
    bids = short_book.get("bids") or []
    asks = long_book.get("asks") or []
    if not bids or not asks:
        return result
    stamps = [_book_stamp(short_book), _book_stamp(long_book)]
    if any(stamp is None for stamp in stamps):
        return result
    assert all(stamp is not None for stamp in stamps)
    if any(stamp > checked_at_ms or checked_at_ms - stamp > MAX_QUOTE_AGE_MS for stamp in stamps):
        result["reason_cn"] = "报价陈旧或晚于检查时点，未进入严格净表现。"
        return result
    if max(stamps) - min(stamps) > MAX_LEG_SKEW_MS:
        result["reason_cn"] = "两腿报价时点差过大，未进入严格净表现。"
        return result
    if _finite(bids[0][1]) is None or _finite(asks[0][1]) is None or float(bids[0][1]) < unit or float(asks[0][1]) < unit:
        result["reason_cn"] = "参考数量缺少足够盘口深度。"
        return result
    bid = _finite(bids[0][0])
    ask = _finite(asks[0][0])
    if bid is None or ask is None or bid < 0 or ask < 0:
        return result
    fee = _entry_fee_btc(short, long, bid, ask)
    if fee is None:
        result["reason_cn"] = "盘口可用，但缺少可追溯的入场手续费参数。"
        return result
    gross_credit = bid - ask
    net_credit = gross_credit - fee
    credit_eligible = net_credit > 0
    delivery_fee = _delivery_fee_status(short, long)
    return {
        "status": "available",
        "observed_at_ms": int(max(stamps)),
        "checked_at_ms": int(checked_at_ms),
        "gross_credit_btc": gross_credit,
        "entry_fee_btc": fee,
        "net_credit_btc": net_credit,
        "net_credit_after_entry_fee_btc": net_credit,
        "expected_net_btc": None,
        "bid_short_btc": bid,
        "ask_long_btc": ask,
        "depth_short": float(bids[0][1]),
        "depth_long": float(asks[0][1]),
        "leg_timestamp_skew_ms": int(max(stamps) - min(stamps)),
        "max_age_ms": int(checked_at_ms - min(stamps)),
        "fee_basis": "public instrument taker rate with 12.5% premium cap; separate legs; no combo discount invented",
        "delivery_fee_status": delivery_fee,
        "strict_entry_quote": True,
        "credit_eligible": credit_eligible,
        "credit_eligibility_reason_cn": "扣除入场费用后仍为正净信用，可进入严格信用价差报价组。"
        if credit_eligible
        else "扣除入场费用后没有正净信用；盘口事实保留，但不进入严格信用价差净表现组。",
        "strict_net_result_ready": False,
        "net_scope": "after entry fees and before any expiry delivery fee",
    }


def _base_assessment(
    observation: dict[str, Any],
    artifact: dict[str, Any],
    source_hash: str,
    status: str,
    reason_cn: str | None = None,
) -> dict[str, Any]:
    asof = int(observation["as_of_ms"])
    cutoff = _ms_from_training_cutoff(artifact.get("training_cutoff") or artifact.get("training_cutoff_ms") or "2021-12-31")
    assessment: dict[str, Any] = {
        "schema": ASSESSMENT_SCHEMA,
        "event_id": observation.get("observation_id"),
        "event_family": observation.get("event_family"),
        "episode_id": observation.get("episode_id"),
        "observation_kind": observation.get("observation_kind"),
        "symbol": "BTC",
        "status": status,
        "provenance": {
            "as_of_ms": asof,
            "source_record_hash": source_hash,
            "input_hash": canonical_hash(observation),
            "model_id": artifact.get("model_version", "unavailable"),
            "model_hash": canonical_hash(artifact),
            "training_cutoff_ms": cutoff,
        },
        "sides": {},
        "scope_cn": "本地联合研究估计；历史检验与经济有效性分别记录。赔付概率不是净胜率，数字不替代D-S或下单权限。",
        "uncertainty": {
            "kind": "grouped historical validation",
            "individual_prediction_interval": None,
            "note_cn": "日期分块检验用于整体差异；本卡未提供经验证的个体置信区间。",
        },
        "out_of_distribution_note_cn": "模型适用范围由训练与封存检验决定；本卡预测不提供个体概率保证。",
    }
    if reason_cn:
        assessment["reason_cn"] = reason_cn
    return assessment


def _insufficient_assessment(
    observation: dict[str, Any],
    artifact: dict[str, Any],
    source_hash: str,
    reason_cn: str,
) -> dict[str, Any]:
    assessment = _base_assessment(observation, artifact, source_hash, "insufficient", reason_cn)
    for side in ("put", "call"):
        assessment["sides"][side] = {
            "status": "insufficient",
            "reason_cn": reason_cn,
            "quote": {"status": "not_collected"},
        }
    assessment["assessment_hash"] = compute_assessment_hash(assessment)
    return validate_assessment(assessment)


def _selected_legs(
    contracts: list[dict[str, Any]],
    entry: int,
    price: float,
) -> tuple[dict[str, tuple[dict[str, Any], dict[str, Any]]], set[str]]:
    eligible = [contract for contract in contracts if contract.get("expiration_timestamp") == expiry_ms(entry)]
    selected: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    names: set[str] = set()
    for side in ("put", "call"):
        legs = select_legs(eligible, side, price, PRIMARY_WIDTH, entry)
        if legs:
            selected[side] = legs
            names.update(leg["instrument_name"] for leg in legs)
    return selected, names


def make_assessment(
    observation: dict[str, Any],
    entry_price: float | None,
    contracts: list[dict[str, Any]],
    artifact: dict[str, Any],
    source_hash: str,
    books: dict[str, dict[str, Any]] | None = None,
    checked_at_ms: int | None = None,
    *,
    quote_group_error_cn: str | None = None,
) -> dict[str, Any]:
    from astra_joint_inference import predict_row

    entry = int(observation["entry_ms"])
    dte = _dte_hours(entry)
    checked = int(checked_at_ms or now_ms())
    if entry_price is None or not _ordinary_dte(entry):
        reason = "不在普通轮8至24小时期限内，或缺少次分钟入场价格；本观察保留但不抓取期权报价。"
        return _insufficient_assessment(observation, artifact, source_hash, reason)

    selected, _names = _selected_legs(contracts, entry, float(entry_price))
    if len(selected) != 2:
        return _insufficient_assessment(observation, artifact, source_hash, "缺少当时有效的Put/Call参考合约。")

    assessment = _base_assessment(observation, artifact, source_hash, "available")
    for side in ("put", "call"):
        short, long = selected[side]
        width = abs(float(short["strike"]) - float(long["strike"]))
        row_id = canonical_hash([observation.get("observation_id"), side, PRIMARY_WIDTH])
        features = {key: observation.get(key) for key in FEATURE_GROUPS["joint"]}
        features.update(
            {
                "row_id": row_id,
                "side": side,
                "side_sign": -1 if side == "put" else 1,
                "dte_hours": dte,
                "short_distance_fraction": abs(float(short["strike"]) - float(entry_price)) / float(entry_price),
                "width_fraction": width / float(entry_price),
                "adverse_move_since_shock": observation.get(
                    "down_move_since_shock" if side == "put" else "up_move_since_shock"
                ),
                "favorable_move_since_shock": observation.get(
                    "up_move_since_shock" if side == "put" else "down_move_since_shock"
                ),
            }
        )
        prediction = predict_row(artifact, features)
        expected_loss = _finite(prediction.get("expected_loss_normalized"))
        result: dict[str, Any] = {
            "status": str(prediction.get("status") or "unavailable"),
            "row_id": row_id,
            "side": side,
            "model_side": prediction.get("side"),
            "model_version": prediction.get("model_version"),
            "model_kind": prediction.get("model_kind"),
            "feature_group": prediction.get("feature_group"),
            "prediction_hash": prediction.get("prediction_hash"),
            "probability_positive": prediction.get("probability_positive"),
            "conditional_positive_loss": prediction.get("conditional_positive_loss"),
            "expected_loss_normalized": prediction.get("expected_loss_normalized"),
            "expected_payout_btc": expected_loss * width / float(entry_price) if expected_loss is not None else None,
            "reference": {
                "short_strike": float(short["strike"]),
                "long_strike": float(long["strike"]),
                "width": width,
                "entry_price": float(entry_price),
                "expiry_ms": expiry_ms(entry),
                "short_name": short["instrument_name"],
                "long_name": long["instrument_name"],
            },
            "scope": prediction.get("scope", {}),
            "scope_cn": "本地影子评估；只估计参考价差的赔付风险，不改变生产D-S评级或交易权限。",
            "uncertainty_cn": assessment['uncertainty']['note_cn'],
        }
        support = prediction.get('scope', {}).get('training_feature_support', {})
        missing = [name for name in support if _finite(features.get(name)) is None]
        outside = [name for name, bounds in support.items() if _finite(features.get(name)) is not None
                   and bounds.get('min') is not None and bounds.get('max') is not None
                   and not bounds['min'] <= float(features[name]) <= bounds['max']]
        result['input_support'] = {'missing_features': missing, 'outside_training_range': outside}
        if support:
            result['scope_cn'] = (f"本卡使用 {len(support)} 项特征，其中 {len(missing)} 项缺失、{len(outside)} 项超出训练取值范围。"
                                  "范围内不代表预测可靠，范围外需按外推解释。")
        if books is not None:
            quote = quote_side(side, short, long, books, checked, group_error_cn=quote_group_error_cn)
            if quote.get("status") == "available" and result["expected_payout_btc"] is not None:
                quote["expected_net_btc"] = quote["net_credit_btc"] - result["expected_payout_btc"]
            elif quote.get("status") == "available":
                quote["status"] = "insufficient"
                quote["reason_cn"] = "模型赔付估计不可用，报价不进入纸面净值。"
            result["quote"] = quote
        else:
            result["quote"] = {"status": "not_collected"}
        assessment["sides"][side] = result
        if result["status"] != "available":
            assessment["status"] = "insufficient"

    assessment["assessment_hash"] = compute_assessment_hash(assessment)
    return validate_assessment(assessment)


def _fetch_market(ledger: Ledger, raw_dir: Path, market: str, at: int, minute: int, transport: Transport, closed_only=False) -> tuple[int, int]:
    base = "https://fapi.binance.com/fapi/v1/klines" if market == "um" else "https://api.binance.com/api/v3/klines"
    last = ledger.newest_closed_bar_open(market, at) if closed_only else ledger.newest_bar_open(market)
    params = _market_params(market, at, last)
    attempted, payload, _meta, _error = _fetch_once(ledger, raw_dir, f"binance_{market}", minute, base, params, transport, at)
    if payload is not None:
        try:
            _store_market_payload(ledger, market, payload, at, closed_only=closed_only)
        except (ValueError, KeyError, TypeError) as exc:
            if not closed_only:
                raise
            ledger.finish_request(f"binance_{market}", minute, "invalid_market_payload", at, type(exc).__name__)
            return (1 if attempted else 0), 0
        if closed_only and ledger.newest_closed_bar_open(market, at) != (at // MINUTE - 1) * MINUTE:
            # A transport response is not evidence of an available closed bar.
            # Keep the raw response and fail only this research minute.
            ledger.finish_request(f"binance_{market}", minute, "latest_closed_minute_missing", at)
            return (1 if attempted else 0), 0
    return (1 if attempted else 0), (1 if payload is not None else 0)


def _fetch_instruments(ledger: Ledger, raw_dir: Path, minute: int, transport: Transport, at: int) -> tuple[int, list[dict[str, Any]] | None]:
    params = {"currency": "BTC", "kind": "option", "expired": "false"}
    attempted, payload, _meta, _error = _fetch_once(
        ledger,
        raw_dir,
        "deribit_instruments",
        minute,
        "https://www.deribit.com/api/v2/public/get_instruments",
        params,
        transport,
        at,
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
        return (1 if attempted else 0), None
    return (1 if attempted else 0), payload["result"]


def _fetch_books(
    ledger: Ledger,
    raw_dir: Path,
    names: set[str],
    minute: int,
    transport: Transport,
    at: int,
) -> tuple[int, dict[str, dict[str, Any]], dict[str, str]]:
    books: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    attempts = 0

    def load(name: str) -> tuple[str, bool, dict[str, Any] | None, str | None]:
        params = {"instrument_name": name, "depth": 5}
        attempted, payload, _meta, error = _fetch_once(
            ledger,
            raw_dir,
            f"deribit_book:{name}",
            minute,
            "https://www.deribit.com/api/v2/public/get_order_book",
            params,
            transport,
            at,
        )
        if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
            return name, attempted, payload["result"], None
        return name, attempted, None, error or "invalid_payload"

    with ThreadPoolExecutor(max_workers=4) as pool:
        for name, attempted, data, error in pool.map(load, sorted(names)):
            attempts += 1 if attempted else 0
            if data is not None:
                books[name] = data
            elif error != "already_attempted":
                errors[name] = str(error)
    return attempts, books, errors


def _incremental_replay(ledger: Ledger, at: int, *, mdie_func: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    emitted = {"events": [], "observations": [], "clock_rows": []}
    snapshot = ledger.replay_snapshot()
    kwargs = {"mdie_func": mdie_func} if mdie_func is not None else {}
    if snapshot:
        runner = StreamingReplay.from_snapshot(
            snapshot,
            as_of_upper_ms=at,
            on_event=emitted["events"].append,
            on_observation=emitted["observations"].append,
            on_clock=emitted["clock_rows"].append,
            **kwargs,
        )
        last_open = runner.previous_open
        warmup = False
    else:
        runner = StreamingReplay(
            as_of_upper_ms=at,
            on_event=emitted["events"].append,
            on_observation=emitted["observations"].append,
            on_clock=emitted["clock_rows"].append,
            **kwargs,
        )
        last_open = None
        warmup = True
    source_rows = ledger.bars("um")
    if last_open is not None:
        source_rows = [row for row in source_rows if int(row["open_time_ms"]) > int(last_open)]
    processed = 0
    for row in source_rows:
        if not is_complete_minute_bar(row, as_of_ms=at):
            continue
        runner.push(row)
        processed += 1
    runner.finish()
    emitted["streaming_snapshot"] = {
        "schema": "astra_joint_shadow_streaming_snapshot_run@1.0.0",
        "warmup_run": warmup,
        "input_rows_processed": processed,
        "last_processed_open_ms": runner.previous_open,
        "last_processed_as_of_ms": runner.previous_as_of,
        "note_cn": "首次运行用于暖机并建立状态；之后仅处理新增闭合分钟，避免滑动缓存起点改变事件身份。",
    }
    emitted["next_streaming_replay_snapshot"] = runner.to_snapshot()
    return emitted


def _process_identity(kind: str, row: dict[str, Any]) -> str:
    if kind in {"observation", "clock"} and row.get("observation_id"):
        return f"{kind}:{row['observation_id']}"
    if kind == "event":
        parts = [
            row.get("event_family") or "price_rebalance_candidate",
            row.get("episode_id") or "unknown_episode",
            row.get("event_type") or row.get("status") or "event",
            row.get("status") or "unknown_status",
            row.get("as_of_ms"),
        ]
        return "event:" + ":".join(str(part) for part in parts)
    return f"{kind}:{canonical_hash(row)}"


def _record_replay_process(
    ledger: Ledger,
    replayed: dict[str, Any],
    checked_at_ms: int,
    window: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = {
        "status": "no_forward_window",
        "inserted": 0,
        "duplicates": 0,
        "appended_revisions": 0,
        "outside_window_ignored": 0,
        "missing_asof_ignored": 0,
        "prior_window_replayed": 0,
        "late_or_recovered_gaps": 0,
        "identity_conflicts_ignored": 0,
        "by_kind": {"event": 0, "observation": 0, "clock": 0},
    }
    if not window:
        return summary
    start = int(window["start_ms"])
    end = int(window["end_ms"])
    upper = min(int(checked_at_ms), end)
    summary["status"] = "recorded_forward_window"
    summary["window_start_ms"] = start
    summary["window_observed_upper_ms"] = upper
    if upper <= start:
        return summary

    current_lower = max(start, upper - MINUTE)
    changed = False
    for source_key, kind in (("events", "event"), ("observations", "observation"), ("clock_rows", "clock")):
        rows = replayed.get(source_key) or []
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict):
                continue
            try:
                asof = int(item["as_of_ms"])
            except (KeyError, TypeError, ValueError):
                summary["missing_asof_ignored"] += 1
                continue
            if not (start <= asof < upper):
                summary["outside_window_ignored"] += 1
                continue
            process_id = _process_identity(kind, item)
            event_type = str(item.get("event_type") or "")
            event_status = str(item.get("status") or "")
            terminal_event = kind == "event" and (
                event_status in {"closed", "terminated"} or event_type.startswith("closed_") or event_type.startswith("terminated_")
            )
            if asof < current_lower and not terminal_event:
                summary["prior_window_replayed"] += 1
                if not ledger.has_process_id(process_id):
                    gap_payload = {
                        "schema": "astra_joint_forward_process_gap@1.0.0",
                        "gap_type": "replayed_inside_window_outside_current_minute",
                        "missed_kind": kind,
                        "missed_process_id": process_id,
                        "as_of_ms": asof,
                        "reason_cn": "此过程行在冻结窗口内，但已早于本轮当前分钟；仅记录为迟到或恢复缺口，不作为新增前向事实。",
                    }
                    outcome = ledger.record_process(
                        f"gap:{process_id}",
                        "gap",
                        asof,
                        gap_payload,
                        int(checked_at_ms),
                        phase="formal_forward",
                        export=False,
                    )
                    if outcome["status"] != "duplicate":
                        changed = True
                        summary["inserted"] += 1
                        summary["late_or_recovered_gaps"] += 1
                continue
            payload = {
                **item,
                "forward_process_context": {
                    "schema": "astra_joint_forward_process_context@1.0.0",
                    "window_start_ms": start,
                    "window_end_ms": end,
                    "source": "sliding_replay_cache",
                    "max_closed_cache_minutes": MAX_CLOSED_CACHE_MINUTES,
                    "boundary_note_cn": "仅记录冻结前向窗口内已经闭合并可得的过程事实；窗口前暖机缓存不计作前向观测。",
                },
            }
            outcome = ledger.record_process(
                process_id,
                kind,
                asof,
                payload,
                int(checked_at_ms),
                phase="formal_forward",
                export=False,
            )
            if outcome["status"] == "duplicate":
                summary["duplicates"] += 1
            elif outcome["status"] == "identity_conflict_ignored":
                summary["identity_conflicts_ignored"] += 1
            elif outcome["status"] == "appended_revision":
                summary["appended_revisions"] += 1
                summary["inserted"] += 1
                summary["by_kind"][kind] += 1
                changed = True
            else:
                summary["inserted"] += 1
                summary["by_kind"][kind] += 1
                changed = True
    if changed:
        ledger.export_process_records()
    return summary


def _write_decision(
    ledger: Ledger,
    observation: dict[str, Any],
    assessment: dict[str, Any],
    checked: int,
    window: dict[str, Any] | None,
    quote_errors: dict[str, str] | None = None,
) -> int:
    record = {
        "schema": "astra_joint_forward_decision@1.0.0",
        "observation": observation,
        "assessment": assessment,
        "recorded_at_ms": int(checked),
        "formal_window": window,
        "quote_errors": quote_errors or {},
        "phase": "formal_forward" if window else "local_qualification",
        "immutable_note_cn": "影子研究事前记录；不会触发FMZ信号、LLM或下单。",
    }
    return int(ledger.record_decision(str(observation["observation_id"]), int(observation["as_of_ms"]), record))


def collect_once(
    folder: str | Path,
    artifact_path: str | Path,
    transport: Transport = public_get,
    clock: Callable[[], int] = now_ms,
) -> dict[str, Any]:
    ledger = Ledger(folder)
    at = int(clock())
    minute = at // MINUTE
    window = ledger.window()
    if window and at >= int(window["end_ms"]):
        return {"status": "window_finished", "http_attempts": 0, "window": window}

    artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
    if window:
        # A forward window has one immutable model, independent of its filename.
        model_identity = canonical_hash(artifact)
        previous = ledger.db.execute("SELECT value FROM meta WHERE key='forward_model_sha256'").fetchone()
        if previous and previous[0] != model_identity:
            raise ValueError("forward model changed; use a separately registered version and window")
        with ledger.db:
            ledger.db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('forward_model_sha256',?)", (model_identity,))
    raw_dir = Path(folder) / "raw"
    attempts = successes = 0
    for market in ("um", "spot"):
        tried, ok = _fetch_market(ledger, raw_dir, market, at, minute, transport,
            closed_only=artifact.get("schema") == "astra_joint_v11_model_artifact@1.0.0")
        attempts += tried
        successes += ok

    bars = ledger.bars("um")
    if not bars:
        return {"status": "market_unavailable", "http_attempts": attempts, "http_successes": successes}

    replayed = _incremental_replay(ledger, at)
    process_ledger = _record_replay_process(ledger, replayed, at, window)
    next_snapshot = replayed.get("next_streaming_replay_snapshot")
    current = [item for item in replayed.get("observations", []) if at - MINUTE <= int(item["as_of_ms"]) < at]
    if window:
        start = int(window["start_ms"])
        upper = min(at, int(window["end_ms"]))
        current = [item for item in current if start <= int(item["as_of_ms"]) < upper]
    new = [item for item in current if not ledger.has_decision(str(item["observation_id"]))]
    if artifact.get("schema") == "astra_joint_v11_model_artifact@1.0.0":
        # The new model belongs to common natural-card observations. Preserve
        # price-event processes, without applying a different model domain.
        # A shared hourly catalogue also serves natural cards in quiet markets.
        tried, catalogue = _fetch_instruments(ledger, raw_dir, minute - minute % 60, transport, at)
        recorded = 0
        for observation in new:
            assessment = _insufficient_assessment(observation, artifact, canonical_hash(observation),
                "本价格事件保留独立影子身份；自然卡共同截面模型未作为该事件的已验证模型。")
            recorded += _write_decision(ledger, observation, assessment, at, window)
        if isinstance(next_snapshot, dict):
            ledger.save_replay_snapshot(next_snapshot)
        return {"status": "natural_card_cache_ready", "http_attempts": attempts + tried,
            "http_successes": successes + int(catalogue is not None), "new_decisions": recorded,
            "options_http_attempts": tried, "process_ledger": process_ledger}
    if not new:
        if isinstance(next_snapshot, dict):
            ledger.save_replay_snapshot(next_snapshot)
        return {
            "status": "no_new_observation",
            "http_attempts": attempts,
            "http_successes": successes,
            "new_decisions": 0,
            "options_http_attempts": 0,
            "process_ledger": process_ledger,
        }

    source_hashes = {str(item["observation_id"]): canonical_hash(item) for item in new}
    needs_contracts = [
        item
        for item in new
        if _ordinary_dte(int(item["entry_ms"])) and ledger.open_price("spot", int(item["entry_ms"])) is not None
    ]
    contracts: list[dict[str, Any]] | None = None
    options_attempts = 0
    if needs_contracts:
        tried, contracts = _fetch_instruments(ledger, raw_dir, minute, transport, at)
        attempts += tried
        options_attempts += tried
        successes += 1 if contracts is not None else 0

    recorded = 0
    excluded = 0
    insufficient = 0
    for observation in new:
        price = ledger.open_price("spot", int(observation["entry_ms"]))
        source_hash = source_hashes[str(observation["observation_id"])]
        if not _ordinary_dte(int(observation["entry_ms"])) or price is None or contracts is None:
            if not _ordinary_dte(int(observation["entry_ms"])):
                excluded += 1
                reason = "普通轮只研究8至24小时期限；本观察已登记为期限外，不抓取期权或报价。"
            elif price is None:
                insufficient += 1
                reason = "次分钟入场价格尚不可得；本观察已登记，不抓取期权或报价。"
            else:
                insufficient += 1
                reason = "期权合约列表未取得；本观察保留为资料不足。"
            assessment = _insufficient_assessment(observation, artifact, source_hash, reason)
            recorded += _write_decision(ledger, observation, assessment, at, window)
            continue

        selected, names = _selected_legs(contracts, int(observation["entry_ms"]), price)
        books: dict[str, dict[str, Any]] = {}
        quote_errors: dict[str, str] = {}
        if len(selected) == 2 and len(names) == 4:
            tried, books, quote_errors = _fetch_books(ledger, raw_dir, names, minute, transport, at)
            attempts += tried
            options_attempts += tried
            successes += len(books)
        checked = int(clock())
        group_error = validate_quote_group(names, books, checked)
        assessment = make_assessment(
            observation,
            price,
            contracts,
            artifact,
            source_hash,
            books,
            checked,
            quote_group_error_cn=group_error,
        )
        recorded += _write_decision(ledger, observation, assessment, checked, window, quote_errors)

    status = "recorded" if recorded else "no_new_decision_recorded"
    if isinstance(next_snapshot, dict):
        ledger.save_replay_snapshot(next_snapshot)
    return {
        "status": status,
        "http_attempts": attempts,
        "http_successes": successes,
        "market_http_attempts": attempts - options_attempts,
        "options_http_attempts": options_attempts,
        "new_decisions": recorded,
        "excluded_observations": excluded,
        "insufficient_observations": insufficient,
        "process_ledger": process_ledger,
    }


def settle_decision(
    folder: str | Path,
    decision_id: str,
    settlement_price: float,
    *,
    recorded_at_ms: int | None = None,
    source_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if settlement_price <= 0:
        raise ValueError("settlement_price must be positive")
    ledger = Ledger(folder)
    prior = ledger.db.execute("SELECT payload FROM outcomes WHERE id=?", (decision_id,)).fetchone()
    if prior:
        return json.loads(prior[0])
    decision = ledger.decision(decision_id)
    assessment = decision["assessment"]
    recorded = int(recorded_at_ms or now_ms())
    expiries = [
        int(item["reference"]["expiry_ms"])
        for item in assessment.get("sides", {}).values()
        if isinstance(item, dict) and item.get("reference")
    ]
    if expiries and recorded < max(expiries):
        raise ValueError("decision has not reached expiry")
    source = dict(source_provenance) if isinstance(source_provenance, dict) else {
        "kind": "manual_research_input",
        "basis_cn": "手工传入结算价，仅用于研究复核；不是官方Deribit交割来源。",
    }
    official_delivery = source.get("kind") == "official_deribit_delivery"
    sides: dict[str, Any] = {}
    for side, item in assessment.get("sides", {}).items():
        reference = item.get("reference") or {}
        if not reference:
            sides[side] = {"status": "insufficient", "reason_cn": "参考两腿缺失，不能结算赔付。"}
            continue
        paid = payout(side, float(reference["short_strike"]), float(reference["long_strike"]), float(settlement_price))
        quote = item.get("quote") or {}
        entry_net = _finite(quote.get("net_credit_btc")) if quote.get("status") == "available" else None
        delivery_status = (quote.get("delivery_fee_status") or {}).get("status")
        delivery_fee = 0.0 if delivery_status == "exempt_daily_option" else None
        credit_eligible = quote.get("credit_eligible") is True
        strict_net = official_delivery and entry_net is not None and delivery_fee is not None and credit_eligible
        sides[side] = {
            "status": "settled",
            "payout_btc": paid,
            "loss_normalized": paid / (float(reference["width"]) / float(reference["entry_price"])),
            "entry_net_credit_btc": entry_net,
            "provisional_net_before_delivery_fee_btc": entry_net - paid if entry_net is not None else None,
            "credit_eligible": credit_eligible,
            "delivery_fee_status": delivery_status or "unknown",
            "delivery_fee_btc": delivery_fee,
            "strict_net_result_btc": entry_net - paid - delivery_fee if strict_net else None,
            "strict_net_result_ready": strict_net,
            "strict_net_result_reason_cn": "官方交割价、正净信用与到期费用口径均可用。"
            if strict_net
            else "未满足官方交割来源、正净信用或到期费用条件；不进入严格官方净表现。",
        }
    outcome = {
        "schema": "astra_joint_forward_outcome@1.0.0",
        "decision_id": decision_id,
        "source_decision_hash": hashlib.sha256(_json_bytes(decision)).hexdigest(),
        "settlement_price": float(settlement_price),
        "recorded_at_ms": recorded,
        "source_provenance": source,
        "sides": sides,
        "immutable_note_cn": "到期结果追加保存，不覆盖事前决策。",
    }
    ledger.record_outcome(decision_id, outcome)
    return outcome


def publish_shadow(folder: str | Path, output: str | Path) -> dict[str, Any]:
    folder = Path(folder)
    output = Path(output)
    target = output / "joint-shadow"
    target.mkdir(parents=True, exist_ok=True)
    source = folder / "decisions.jsonl"
    cards = []
    if not source.exists():
        save(target / "manifest.json", {"schema_version": "astra_joint_shadow_manifest@1.0.0", "cards": []})
        return {"cards": 0}
    recent_lines: deque[str] = deque(maxlen=200)
    total = 0
    with source.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            total += 1
            recent_lines.append(line)
    for line in reversed(recent_lines):
        if not line:
            continue
        row = json.loads(line)
        projection = build_joint_projection(row["assessment"])
        identifier = row["observation"]["observation_id"]
        filename = canonical_hash(identifier) + ".json"
        save(target / filename, projection["detail"])
        save(target / filename.replace(".json", ".audit.json"), row)
        cards.append(
            {
                "card_id": identifier,
                "path": "joint-shadow/" + filename,
                "summary": projection["summary"],
                "confirmed_at": projection["detail"]["identity"]["confirmed_at"],
                "symbol": "BTC",
                "detail_projection_hash": projection["detail"]["display_projection_hash"],
                "assessment_hash": projection["summary"]["assessment_hash"],
                "research_identity_cn": "联合研究影子记录；未触发FMZ信号。",
            }
        )
    save(
        target / "manifest.json",
        {
            "schema_version": "astra_joint_shadow_manifest@1.0.0",
            "projection_schema_version": "astra_joint_display@1.0.0",
            "manifest_limit": 200,
            "source_decisions": total,
            "cards": cards,
        },
    )
    return {"cards": len(cards), "source_decisions": total, "manifest_limit": 200}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", choices=["once", "publish", "start-window", "settle"])
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--decision-id")
    parser.add_argument("--settlement-price", type=float)
    args = parser.parse_args()

    if args.job == "once":
        if args.artifact is None:
            raise SystemExit("--artifact is required for once")
        result = collect_once(args.folder, args.artifact)
    elif args.job == "publish":
        if args.output is None:
            raise SystemExit("--output is required for publish")
        result = publish_shadow(args.folder, args.output)
    elif args.job == "settle":
        if not args.decision_id or args.settlement_price is None:
            raise SystemExit("--decision-id and --settlement-price are required for settle")
        result = settle_decision(args.folder, args.decision_id, args.settlement_price)
    else:
        result = Ledger(args.folder).window(now_ms())
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
