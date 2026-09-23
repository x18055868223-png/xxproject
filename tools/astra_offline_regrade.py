#!/usr/bin/env python3
"""Freeze and run isolated Astra v2.2.x historical evidence reviews.

The tool is research-only.  ``prepare`` reads the frozen signal archive and
stores the exact cards, evidence packets and request bodies needed for later
review.  ``run`` consumes only that frozen bundle and writes an isolated
sidecar, so the production signal-audit state is never touched.
"""

from __future__ import annotations

import argparse
import csv
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import astra_light_study as light_study  # noqa: E402
import astra_light_study_analysis as light_analysis  # noqa: E402
import signal_evidence_v2 as evidence_v2  # noqa: E402
import signal_llm_review as core  # noqa: E402
import signal_review_v2 as review_v2  # noqa: E402
import signal_review_v2_runtime as runtime_v2  # noqa: E402


SCHEMA_VERSION = "astra_offline_regrade@1.0.0"
FREEZE_SCHEMA_VERSION = "astra_offline_regrade_freeze@1.0.0"
BUDGET_SCHEMA_VERSION = "astra_offline_regrade_budget@1.0.0"
SEAL_SCHEMA_VERSION = "astra_offline_regrade_seal@1.0.0"
DEFAULT_DTE_MIN_HOURS = 8.0
DEFAULT_DTE_MAX_HOURS = 24.0
DEFAULT_EXPECTED_COUNT = 114
DEFAULT_EXPECTED_EVENT_COUNT = 176
DEFAULT_EXPECTED_FIXED_COUNT = 47
DEFAULT_TOTAL_HTTP_LIMIT = 228
DEFAULT_DAILY_HTTP_LIMIT = 228
DEFAULT_CONCURRENCY = 2
SIDES = ("put_credit", "call_credit")
GRADES = ("D", "C", "B", "A", "S")


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def _hash_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    core._write_json_atomic(path, value)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(_canonical_json(row) + "\n")
    tmp.replace(path)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: _canonical_json(value) if isinstance(value, (dict, list)) else value
                for key, value in row.items()
            })
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _safe_stem(card_id: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(card_id)).strip("_")
    if len(text) > 72:
        text = text[:72]
    digest = hashlib.sha256(str(card_id).encode("utf-8")).hexdigest()[:12]
    return f"{text or 'card'}-{digest}"


def _iso_utc(ms: int | float | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _event_ms(card: dict[str, Any]) -> int | None:
    value = evidence_v2._event_time_ms(card)
    return int(value) if value is not None else None


def _card_id(card: dict[str, Any]) -> str:
    return str((card.get("identity") or {}).get("card_id") or card.get("card_id") or "")


def _symbol(card: dict[str, Any]) -> str:
    return str((card.get("identity") or {}).get("symbol") or card.get("symbol") or "")


def _load_study_config(study_root: Path) -> dict[str, Any]:
    path = study_root / "study_config.json"
    return _read_json(path) if path.exists() else {}


def _load_model_config(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    data = _read_json(path)
    return {
        "model": data.get("model"),
        "base_url": data.get("base_url") or data.get("endpoint"),
        "timeout": data.get("timeout"),
    }


def _load_raw_archive(raw_path: Path, expected_sha256: str | None = None) -> tuple[bytes, dict[str, dict[str, Any]], dict[str, int]]:
    raw = raw_path.read_bytes()
    actual_sha = _hash_bytes(raw)
    if expected_sha256 and actual_sha != expected_sha256:
        raise ValueError(f"source sha256 mismatch: expected {expected_sha256}, got {actual_sha}")
    by_id: dict[str, dict[str, Any]] = {}
    line_by_id: dict[str, int] = {}
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue
        card_id = _card_id(record)
        if not card_id:
            continue
        previous = by_id.get(card_id)
        if previous is not None and previous != record:
            raise ValueError(f"conflicting duplicate card: {card_id}")
        if previous is None:
            by_id[card_id] = record
            line_by_id[card_id] = line_number
    return raw, by_id, line_by_id


def _source_record_sha256(record: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _eligible_sample(sample: dict[str, Any], min_hours: float, max_hours: float) -> tuple[bool, dict[str, Any]]:
    event_ms = int(sample["event_time_ms"])
    entry_ms = light_analysis._entry_ms(event_ms)
    expiry_ms = light_analysis._next_delivery_ms(entry_ms)
    dte_hours = (expiry_ms - entry_ms) / light_analysis.HOUR_MS
    reason = ""
    if dte_hours <= min_hours:
        reason = "dte_le_min_hours"
    elif dte_hours > max_hours:
        reason = "dte_gt_max_hours"
    return not reason, {
        "entry_ms": entry_ms,
        "entry_utc": _iso_utc(entry_ms),
        "expiry_ms": expiry_ms,
        "expiry_utc": _iso_utc(expiry_ms),
        "dte_hours": dte_hours,
        "ordinary_selection_reason": reason,
    }


def _previous_card_for(card: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any] | None:
    current_ts = _event_ms(card)
    current_symbol = _symbol(card)
    if current_ts is None:
        return None
    previous = None
    previous_ts = None
    for candidate in records:
        candidate_ts = _event_ms(candidate)
        if candidate_ts is None or candidate_ts >= current_ts:
            continue
        if current_symbol and _symbol(candidate) != current_symbol:
            continue
        if previous_ts is None or candidate_ts > previous_ts:
            previous = candidate
            previous_ts = candidate_ts
    return previous


def _transition_record(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any] | None:
    if not previous:
        return None
    current_ts = _event_ms(current)
    previous_ts = _event_ms(previous)
    if current_ts is None or previous_ts is None or current_ts <= previous_ts:
        return None
    current_identity = current.get("identity") or {}
    previous_identity = previous.get("identity") or {}
    current_schema = evidence_v2._schema_fingerprint(current)
    previous_schema = evidence_v2._schema_fingerprint(previous)
    return {
        "schema_version": "signal_transition_record@1.0.0",
        "source": SCHEMA_VERSION,
        "current_card_id": _card_id(current),
        "previous_card_id": _card_id(previous),
        "symbol": _symbol(current),
        "current_ts_ms": current_ts,
        "previous_ts_ms": previous_ts,
        "elapsed_ms": current_ts - previous_ts,
        "producer_record_hashes": {
            "current": evidence_v2._record_hash(current),
            "previous": evidence_v2._record_hash(previous),
        },
        "current_strategy_version": current_identity.get("strategy_version"),
        "previous_strategy_version": previous_identity.get("strategy_version"),
        "current_card_schema": current_schema,
        "previous_card_schema": previous_schema,
        "card_versions": {
            "current_strategy_version": current_identity.get("strategy_version"),
            "previous_strategy_version": previous_identity.get("strategy_version"),
        },
        "source_schemas": {
            "current": current_schema,
            "previous": previous_schema,
        },
    }


def _change_status(packet: dict[str, Any]) -> tuple[str, list[str]]:
    for fact in packet.get("facts") or []:
        if fact.get("id") == "change.context.status":
            return str(fact.get("value") or ""), list(fact.get("limitations_cn") or [])
    return "missing", []


def _file_manifest(paths: list[Path], root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(paths):
        if path.exists() and path.is_file():
            rows.append({
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _hash_file(path),
            })
    return rows


def _has_attempt_state(ratings_dir: Path) -> bool:
    states = ratings_dir / "reviews.jsonl.v2_attempts"
    if not states.exists():
        return False
    return any(path.is_file() and path.suffix == ".json" for path in states.iterdir())


def _assert_prepare_can_refresh(ratings_dir: Path) -> None:
    blockers = []
    for name in ("reviews.jsonl", "budget.json"):
        if (ratings_dir / name).exists():
            blockers.append(name)
    if _has_attempt_state(ratings_dir):
        blockers.append("reviews.jsonl.v2_attempts")
    if blockers:
        raise RuntimeError(
            "refusing to refresh frozen inputs after review state exists: "
            + ", ".join(blockers)
        )


def prepare_freeze(
    study_root: Path,
    ratings_dir: Path,
    *,
    model: str,
    base_url: str,
    timeout: int,
    expected_count: int | None = DEFAULT_EXPECTED_COUNT,
    expected_event_count: int | None = DEFAULT_EXPECTED_EVENT_COUNT,
    expected_fixed_count: int | None = DEFAULT_EXPECTED_FIXED_COUNT,
    min_hours: float = DEFAULT_DTE_MIN_HOURS,
    max_hours: float = DEFAULT_DTE_MAX_HOURS,
    force: bool = False,
) -> dict[str, Any]:
    ratings_dir.mkdir(parents=True, exist_ok=True)
    freeze_dir = ratings_dir / "freeze"
    config_path = ratings_dir / "config.json"
    if config_path.exists() and not force:
        raise FileExistsError(f"{config_path} exists; pass --force to refresh the frozen bundle")
    if force:
        _assert_prepare_can_refresh(ratings_dir)

    study_config = _load_study_config(study_root)
    raw_path = study_root / "raw_signal_review.jsonl"
    raw, by_id, _line_by_id = _load_raw_archive(raw_path, study_config.get("source_sha256"))
    source_sha = _hash_bytes(raw)
    source_size = len(raw)
    samples, exclusions = light_study.normalize_archive(raw, source_sha)
    fixed_count = sum(1 for item in exclusions if item.get("reason") == "fixed_round")
    if expected_event_count is not None and len(samples) != expected_event_count:
        raise ValueError(f"event count mismatch: expected {expected_event_count}, got {len(samples)}")
    if expected_fixed_count is not None and fixed_count != expected_fixed_count:
        raise ValueError(f"fixed-round count mismatch: expected {expected_fixed_count}, got {fixed_count}")

    raw_records = sorted(by_id.values(), key=lambda row: (_event_ms(row) or 0, _card_id(row)))
    selected: list[dict[str, Any]] = []
    ordinary_exclusions: list[dict[str, Any]] = []
    input_files: list[Path] = []
    for sample in samples:
        ok, selection = _eligible_sample(sample, min_hours, max_hours)
        if not ok:
            ordinary_exclusions.append({
                "card_id": sample["card_id"],
                "direction": sample["direction"],
                "event_time_ms": sample["event_time_ms"],
                **selection,
            })
            continue
        card = by_id.get(str(sample["card_id"]))
        if card is None:
            raise ValueError(f"selected card not found in raw archive: {sample['card_id']}")
        actual_record_sha = _source_record_sha256(card)
        if sample.get("source_record_sha256") and actual_record_sha != sample["source_record_sha256"]:
            raise ValueError(f"source record hash mismatch for {sample['card_id']}")
        previous = _previous_card_for(card, raw_records)
        transition = _transition_record(card, previous)
        packet = evidence_v2.build_evidence_packet(card, previous, transition)
        request = review_v2.build_request(packet, model)
        stem = _safe_stem(str(sample["card_id"]))
        card_path = freeze_dir / "cards" / f"{stem}.json"
        previous_path = freeze_dir / "previous_cards" / f"{stem}.json"
        transition_path = freeze_dir / "transitions" / f"{stem}.json"
        packet_path = freeze_dir / "packets" / f"{stem}.json"
        request_path = freeze_dir / "requests" / f"{stem}.json"
        _write_json(card_path, card)
        if previous:
            _write_json(previous_path, previous)
            input_files.append(previous_path)
        if transition:
            _write_json(transition_path, transition)
            input_files.append(transition_path)
        _write_json(packet_path, packet)
        _write_json(request_path, request)
        input_files.extend([card_path, packet_path, request_path])
        status_value, status_reasons = _change_status(packet)
        selected.append({
            "schema": FREEZE_SCHEMA_VERSION,
            "card_id": sample["card_id"],
            "direction": sample["direction"],
            "version": sample.get("version"),
            "event_time_ms": sample["event_time_ms"],
            "event_utc": _iso_utc(sample["event_time_ms"]),
            "source_line": sample.get("source_line"),
            "source_record_sha256": actual_record_sha,
            "producer_record_hash": evidence_v2._record_hash(card),
            "entry_ms": selection["entry_ms"],
            "entry_utc": selection["entry_utc"],
            "expiry_ms": selection["expiry_ms"],
            "expiry_utc": selection["expiry_utc"],
            "dte_hours": selection["dte_hours"],
            "previous_card_id": _card_id(previous) if previous else None,
            "previous_event_time_ms": _event_ms(previous) if previous else None,
            "transition_status": status_value,
            "transition_limitations_cn": status_reasons,
            "packet_schema": packet.get("schema"),
            "packet_hash": evidence_v2.packet_hash(packet),
            "request_prompt_version": request.get("_local_prompt_version"),
            "request_review_mode": request.get("_local_review_mode"),
            "request_hash": _hash_value(request),
            "request_wire_hash": _hash_value(core._strip_local_request_fields(request)),
            "card_path": card_path.relative_to(ratings_dir).as_posix(),
            "previous_card_path": previous_path.relative_to(ratings_dir).as_posix() if previous else None,
            "transition_path": transition_path.relative_to(ratings_dir).as_posix() if transition else None,
            "packet_path": packet_path.relative_to(ratings_dir).as_posix(),
            "request_path": request_path.relative_to(ratings_dir).as_posix(),
        })

    selected.sort(key=lambda row: (row["event_time_ms"], row["card_id"]))
    ordinary_exclusions.sort(key=lambda row: (row["event_time_ms"], row["card_id"]))
    if expected_count is not None and len(selected) != expected_count:
        raise ValueError(f"ordinary candidate count mismatch: expected {expected_count}, got {len(selected)}")

    _write_jsonl(freeze_dir / "index.jsonl", selected)
    _write_jsonl(freeze_dir / "ordinary_exclusions.jsonl", ordinary_exclusions)
    input_files.extend([freeze_dir / "index.jsonl", freeze_dir / "ordinary_exclusions.jsonl"])
    config = {
        "schema": SCHEMA_VERSION,
        "created_at_utc": _now_iso(),
        "source": {
            "study_root": str(study_root),
            "raw_path": str(raw_path),
            "source_sha256": source_sha,
            "source_bytes": source_size,
            "source_card_count": len(by_id),
            "event_count": len(samples),
            "fixed_excluded_count": fixed_count,
        },
        "selection": {
            "min_dte_hours_open": min_hours,
            "max_dte_hours_closed": max_hours,
            "selected_count": len(selected),
            "excluded_event_count": len(ordinary_exclusions),
            "entry_rule": "next minute after card confirmed_time_ms",
            "expiry_rule": "next 08:00 UTC delivery after entry",
        },
        "review": {
            "model": model,
            "base_url": base_url,
            "timeout_seconds": timeout,
            "prompt_version": review_v2.PROMPT_VERSION,
            "output_schema_version": review_v2.OUTPUT_SCHEMA_VERSION,
            "packet_schema_version": evidence_v2.PACKET_SCHEMA_VERSION,
            "review_mode": review_v2.REVIEW_MODE,
            "normal_http_calls_per_card": 1,
            "max_http_calls_per_card": runtime_v2.MAX_ATTEMPTS,
            "max_concurrency": DEFAULT_CONCURRENCY,
            "daily_http_limit": DEFAULT_DAILY_HTTP_LIMIT,
            "total_http_limit": DEFAULT_TOTAL_HTTP_LIMIT,
        },
        "no_future_inputs": {
            "uses_market_results": False,
            "uses_delivery_prices": False,
            "uses_future_price_path": False,
            "uses_existing_llm_reviews": False,
        },
    }
    _write_json(config_path, config)
    input_files.append(config_path)
    manifest = {
        "schema": "astra_offline_regrade_input_manifest@1.0.0",
        "created_at_utc": _now_iso(),
        "root": str(ratings_dir),
        "files": _file_manifest(input_files, ratings_dir),
    }
    _write_json(ratings_dir / "input_manifest.json", manifest)
    return {
        "prepared": len(selected),
        "excluded_events": len(ordinary_exclusions),
        "fixed_excluded": fixed_count,
        "ratings_dir": str(ratings_dir),
        "input_manifest_sha256": _hash_file(ratings_dir / "input_manifest.json"),
    }


class StudyHttpBudget:
    """One persistent research budget with daily and total limits."""

    def __init__(
        self,
        state_path: Path,
        *,
        daily_limit: int = DEFAULT_DAILY_HTTP_LIMIT,
        total_limit: int = DEFAULT_TOTAL_HTTP_LIMIT,
        now_fn=None,
    ):
        self.state_path = Path(state_path)
        self.daily_limit = max(0, int(daily_limit))
        self.total_limit = max(0, int(total_limit))
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()

    def reserve(self, count=1, role="unknown", packet_hash=None, provider=core.PROVIDER, model=review_v2.DEFAULT_MODEL):
        count = int(count)
        if count <= 0:
            return self.snapshot()
        with self._lock:
            with core._exclusive_file_lock(self.state_path):
                state = self._read_state()
                today = _beijing_day(self.now_fn())
                daily = dict(state.get("daily") or {})
                if daily.get("date_bjt") != today:
                    daily = {"date_bjt": today, "http_calls_used": 0, "http_calls_limit": self.daily_limit}
                daily_used = int(daily.get("http_calls_used") or 0)
                total_used = int(state.get("total_http_calls_used") or 0)
                if daily_used + count > self.daily_limit:
                    raise core.DailyBudgetExceeded(
                        f"daily study LLM HTTP budget exhausted: used={daily_used} requested={count} limit={self.daily_limit}"
                    )
                if total_used + count > self.total_limit:
                    raise core.DailyBudgetExceeded(
                        f"total study LLM HTTP budget exhausted: used={total_used} requested={count} limit={self.total_limit}"
                    )
                now = self.now_fn().astimezone(timezone.utc).isoformat(timespec="seconds")
                reservation_id = hashlib.sha256(
                    f"{now}:{os.getpid()}:{threading.get_ident()}:{total_used}".encode("utf-8")
                ).hexdigest()[:24]
                daily["http_calls_used"] = daily_used + count
                daily["http_calls_limit"] = self.daily_limit
                events = list(state.get("events") or [])
                events.append({
                    "reservation_id": reservation_id,
                    "requested_at_utc": now,
                    "role": str(role)[:80],
                    "packet_hash": str(packet_hash or "")[:96],
                    "provider": provider,
                    "model": model,
                    "status_category": "RESERVED",
                    "usage": {},
                })
                state.update({
                    "schema": BUDGET_SCHEMA_VERSION,
                    "updated_at_utc": now,
                    "daily": daily,
                    "daily_http_limit": self.daily_limit,
                    "total_http_calls_used": total_used + count,
                    "total_http_calls_limit": self.total_limit,
                    "events": events[-self.total_limit:] if self.total_limit else [],
                })
                core._write_json_atomic(self.state_path, state)
                result = dict(state)
                result["reservation_id"] = reservation_id
                return result

    def complete(self, reservation_id, status_category, usage=None):
        if not reservation_id:
            return self.snapshot()
        with self._lock:
            with core._exclusive_file_lock(self.state_path):
                state = self._read_state()
                for event in state.get("events") or []:
                    if event.get("reservation_id") == reservation_id:
                        event["status_category"] = str(status_category)[:80]
                        event["usage"] = dict(usage or {})
                        break
                state["updated_at_utc"] = self.now_fn().astimezone(timezone.utc).isoformat(timespec="seconds")
                core._write_json_atomic(self.state_path, state)
                return dict(state)

    def snapshot(self):
        with self._lock:
            with core._exclusive_file_lock(self.state_path):
                state = self._read_state()
                today = _beijing_day(self.now_fn())
                daily = dict(state.get("daily") or {})
                if daily.get("date_bjt") != today:
                    daily = {"date_bjt": today, "http_calls_used": 0, "http_calls_limit": self.daily_limit}
                state.setdefault("schema", BUDGET_SCHEMA_VERSION)
                state.setdefault("total_http_calls_used", 0)
                state["total_http_calls_limit"] = self.total_limit
                state["daily_http_limit"] = self.daily_limit
                state["daily"] = daily
                return dict(state)

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"study budget ledger is unreadable: {self.state_path}") from exc
        if not isinstance(state, dict):
            raise RuntimeError(f"study budget ledger is invalid: {self.state_path}")
        if state.get("schema") != BUDGET_SCHEMA_VERSION:
            raise RuntimeError(f"study budget ledger schema is invalid: {self.state_path}")
        total_used = state.get("total_http_calls_used")
        total_limit = state.get("total_http_calls_limit")
        events = state.get("events")
        daily = state.get("daily")
        if not isinstance(total_used, int) or total_used < 0:
            raise RuntimeError(f"study budget total count is invalid: {self.state_path}")
        if not isinstance(total_limit, int) or total_limit < 0:
            raise RuntimeError(f"study budget total limit is invalid: {self.state_path}")
        if not isinstance(events, list) or total_used != len(events):
            raise RuntimeError(f"study budget events are inconsistent: {self.state_path}")
        if not isinstance(daily, dict):
            raise RuntimeError(f"study budget daily section is invalid: {self.state_path}")
        daily_used = daily.get("http_calls_used")
        daily_limit = daily.get("http_calls_limit")
        if not isinstance(daily_used, int) or daily_used < 0:
            raise RuntimeError(f"study budget daily count is invalid: {self.state_path}")
        if not isinstance(daily_limit, int) or daily_limit < 0:
            raise RuntimeError(f"study budget daily limit is invalid: {self.state_path}")
        for event in events:
            if not isinstance(event, dict) or not event.get("reservation_id") or not event.get("status_category"):
                raise RuntimeError(f"study budget event is invalid: {self.state_path}")
        return state


def _beijing_day(now_utc: datetime) -> str:
    return (now_utc.astimezone(timezone.utc) + timedelta(hours=8)).date().isoformat()


def _load_freeze_index(ratings_dir: Path) -> list[dict[str, Any]]:
    rows = _read_jsonl(ratings_dir / "freeze" / "index.jsonl")
    if not rows:
        raise FileNotFoundError("freeze/index.jsonl is missing or empty; run prepare first")
    return rows


def _done_reviews(path: Path) -> dict[str, dict[str, Any]]:
    done = {}
    for row in _read_jsonl(path):
        card_id = str(row.get("card_id") or "")
        review = row.get("llm_review") or {}
        if card_id and review:
            done[card_id] = row
    return done


def _load_frozen_json(ratings_dir: Path, relative_path: str, expected_hash: str | None = None) -> dict[str, Any]:
    path = ratings_dir / relative_path
    value = _read_json(path)
    if expected_hash and _hash_value(value) != expected_hash:
        raise ValueError(f"frozen json hash mismatch: {relative_path}")
    return value


def _auth_failed(record: dict[str, Any]) -> bool:
    audit = ((record.get("llm_review") or {}).get("call_audit") or [])
    if not audit:
        return False
    last = audit[-1]
    return last.get("error_type") == "LlmApiError" and "鉴权" in str(last.get("reason_cn") or "")


def run_reviews(ratings_dir: Path, **kwargs) -> dict[str, Any]:
    ratings_dir.mkdir(parents=True, exist_ok=True)
    with core._exclusive_file_lock(ratings_dir / "run.lock"):
        return _run_reviews_locked(ratings_dir, **kwargs)


def _run_reviews_locked(
    ratings_dir: Path,
    *,
    api_key: str,
    model: str | None = None,
    timeout: int | None = None,
    base_url: str | None = None,
    daily_limit: int | None = None,
    total_limit: int | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    only_card_id: str | None = None,
    transport=None,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    if not api_key:
        raise RuntimeError("LLM_API_KEY is required for run")
    config = _read_json(ratings_dir / "config.json")
    review_config = config.get("review") or {}
    if review_config.get("prompt_version") != review_v2.PROMPT_VERSION:
        raise RuntimeError("current prompt version does not match frozen prepare config")
    if review_config.get("output_schema_version") != review_v2.OUTPUT_SCHEMA_VERSION:
        raise RuntimeError("current output schema version does not match frozen prepare config")
    if review_config.get("packet_schema_version") != evidence_v2.PACKET_SCHEMA_VERSION:
        raise RuntimeError("current packet schema version does not match frozen prepare config")
    frozen_model = review_config.get("model") or review_v2.DEFAULT_MODEL
    run_model = model or frozen_model
    if run_model != frozen_model:
        raise ValueError(f"model mismatch: frozen={frozen_model}, requested={run_model}")
    run_timeout = int(timeout or review_config.get("timeout_seconds") or 240)
    frozen_endpoint = review_config.get("base_url") or core.OPENAI_CHAT_COMPLETIONS_ENDPOINT
    if base_url and base_url != frozen_endpoint:
        raise ValueError("base_url mismatch: run must use the frozen endpoint")
    endpoint = frozen_endpoint
    frozen_daily = int(review_config.get("daily_http_limit") or DEFAULT_DAILY_HTTP_LIMIT)
    frozen_total = int(review_config.get("total_http_limit") or DEFAULT_TOTAL_HTTP_LIMIT)
    if daily_limit is not None and int(daily_limit) > frozen_daily:
        raise ValueError("daily_http_limit cannot exceed the frozen prepare limit")
    if total_limit is not None and int(total_limit) > frozen_total:
        raise ValueError("total_http_limit cannot exceed the frozen prepare limit")
    worker_count = max(1, min(int(concurrency or DEFAULT_CONCURRENCY), DEFAULT_CONCURRENCY))
    rows = _load_freeze_index(ratings_dir)
    if only_card_id:
        rows = [row for row in rows if row["card_id"] == only_card_id]
        if not rows:
            raise ValueError(f"frozen card not found: {only_card_id}")
    output = ratings_dir / "reviews.jsonl"
    done = _done_reviews(output)
    pending = [row for row in rows if row["card_id"] not in done]
    states = output.with_suffix(output.suffix + ".v2_attempts")
    states.mkdir(parents=True, exist_ok=True)
    (states / "responses").mkdir(parents=True, exist_ok=True)
    budget = StudyHttpBudget(
        ratings_dir / "budget.json",
        daily_limit=int(daily_limit or frozen_daily),
        total_limit=int(total_limit or frozen_total),
    )
    budget.snapshot()

    def worker(freeze_row: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        card_id = str(freeze_row["card_id"])
        card = _load_frozen_json(ratings_dir, freeze_row["card_path"])
        if _source_record_sha256(card) != freeze_row.get("source_record_sha256"):
            raise ValueError(f"frozen card hash mismatch: {card_id}")
        packet = _load_frozen_json(ratings_dir, freeze_row["packet_path"], freeze_row["packet_hash"])
        if packet.get("schema") != evidence_v2.PACKET_SCHEMA_VERSION:
            raise ValueError(f"frozen packet schema mismatch: {card_id}")
        prepared_request = _load_frozen_json(ratings_dir, freeze_row["request_path"], freeze_row["request_hash"])
        current_request = review_v2.build_request(packet, run_model)
        if _hash_value(core._strip_local_request_fields(current_request)) != freeze_row.get("request_wire_hash"):
            raise ValueError(f"frozen request wire hash mismatch: {card_id}")
        if _hash_value(core._strip_local_request_fields(prepared_request)) != freeze_row.get("request_wire_hash"):
            raise ValueError(f"prepared request wire hash mismatch: {card_id}")
        card_lock = states / ("card-" + hashlib.sha256(card_id.encode("utf-8")).hexdigest())
        with core._exclusive_file_lock(card_lock):
            state_path = runtime_v2._state_path(states, card_id, run_model)
            return runtime_v2._run_card(
                card,
                packet,
                state_path,
                api_key,
                run_model,
                run_timeout,
                endpoint,
                budget,
                transport or core._post_chat_completion,
                reviewed_at,
            )

    written = 0
    errors = 0
    attempted = 0
    deferred = 0
    stopped_for_auth = False

    def handle(result: tuple[dict[str, Any], bool]) -> None:
        nonlocal written, errors, attempted, deferred, stopped_for_auth
        record, did_attempt = result
        if record.get("deferred"):
            deferred += 1
            return
        review = record.get("llm_review") or {}
        attempted += int(bool(did_attempt))
        errors += int(review.get("status") == "ERROR")
        core._append_jsonl(output, record)
        written += 1
        if _auth_failed(record):
            stopped_for_auth = True

    if pending:
        handle(worker(pending[0]))
        if not stopped_for_auth:
            with ThreadPoolExecutor(max_workers=worker_count) as pool:
                next_index = 1
                in_flight = set()
                while next_index < len(pending) and len(in_flight) < worker_count:
                    in_flight.add(pool.submit(worker, pending[next_index]))
                    next_index += 1
                while in_flight:
                    finished, in_flight = wait(in_flight, return_when=FIRST_COMPLETED)
                    for future in finished:
                        handle(future.result())
                    while not stopped_for_auth and next_index < len(pending) and len(in_flight) < worker_count:
                        in_flight.add(pool.submit(worker, pending[next_index]))
                        next_index += 1

    status = status_report(ratings_dir)
    return {
        "schema": "astra_offline_regrade_run_result@1.0.0",
        "ratings_dir": str(ratings_dir),
        "written": written,
        "errors": errors,
        "attempted_cards": attempted,
        "deferred": deferred,
        "skipped_existing": len(done),
        "pending_after_run": status["cards"]["pending"],
        "stopped_for_auth": stopped_for_auth,
        "budget": budget.snapshot(),
    }


def _state_attempt_counts(states_dir: Path) -> dict[str, int]:
    counts = {"state_files": 0, "attempts": 0, "settled": 0}
    if not states_dir.exists():
        return counts
    for path in states_dir.glob("*.json"):
        try:
            state = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(state, dict) or "attempts" not in state:
            continue
        counts["state_files"] += 1
        counts["attempts"] += len(state.get("attempts") or [])
        counts["settled"] += int(bool(state.get("settled")))
    return counts


def status_report(ratings_dir: Path) -> dict[str, Any]:
    config = _read_json(ratings_dir / "config.json") if (ratings_dir / "config.json").exists() else {}
    index = _read_jsonl(ratings_dir / "freeze" / "index.jsonl")
    reviews = _done_reviews(ratings_dir / "reviews.jsonl")
    statuses: dict[str, int] = {}
    for row in reviews.values():
        status = str((row.get("llm_review") or {}).get("status") or "UNKNOWN")
        statuses[status] = statuses.get(status, 0) + 1
    states_dir = ratings_dir / "reviews.jsonl.v2_attempts"
    budget_path = ratings_dir / "budget.json"
    budget = StudyHttpBudget(
        budget_path,
        daily_limit=int(((config.get("review") or {}).get("daily_http_limit") or DEFAULT_DAILY_HTTP_LIMIT)),
        total_limit=int(((config.get("review") or {}).get("total_http_limit") or DEFAULT_TOTAL_HTTP_LIMIT)),
    ).snapshot()
    return {
        "schema": "astra_offline_regrade_status@1.0.0",
        "ratings_dir": str(ratings_dir),
        "cards": {
            "frozen": len(index),
            "reviewed": len(reviews),
            "pending": max(0, len(index) - len(reviews)),
            "statuses": statuses,
        },
        "attempts": _state_attempt_counts(states_dir),
        "budget": budget,
    }


def _side_role_counts(side: dict[str, Any]) -> dict[str, int]:
    counts = {"supports_fit": 0, "counters_fit": 0, "context_only": 0}
    for role in side.get("evidence_roles") or []:
        key = role.get("role")
        if key in counts:
            counts[key] += 1
    return counts


def _review_summary(card: dict[str, Any], review: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return review_v2.build_summary(review, card)
    except Exception:
        return None


def export_ratings(ratings_dir: Path) -> dict[str, Any]:
    index = _load_freeze_index(ratings_dir)
    reviews = _done_reviews(ratings_dir / "reviews.jsonl")
    card_rows: list[dict[str, Any]] = []
    side_rows: list[dict[str, Any]] = []
    coverage = {
        "schema": "astra_offline_regrade_coverage@1.0.0",
        "generated_at_utc": _now_iso(),
        "frozen_cards": len(index),
        "review_status_counts": {},
        "side_status_counts": {side: {} for side in SIDES},
        "grade_counts": {side: {grade: 0 for grade in GRADES} for side in SIDES},
        "b_or_above_counts": {side: 0 for side in SIDES},
        "a_or_s_counts": {side: 0 for side in SIDES},
        "unrated_counts": {side: 0 for side in SIDES},
        "independent_event_dates_utc": 0,
    }
    event_dates = set()
    for freeze_row in index:
        card = _load_frozen_json(ratings_dir, freeze_row["card_path"])
        card_id = str(freeze_row["card_id"])
        event_dates.add(datetime.fromtimestamp(int(freeze_row["event_time_ms"]) / 1000, tz=timezone.utc).date().isoformat())
        review_record = reviews.get(card_id)
        review = (review_record or {}).get("llm_review") or {}
        review_status = str(review.get("status") or "PENDING")
        coverage["review_status_counts"][review_status] = coverage["review_status_counts"].get(review_status, 0) + 1
        advisory = review.get("integrated_trade_advisory") or {}
        sides = advisory.get("side_evidence_ratings") or {}
        price_bias = advisory.get("price_bias") or {}
        comparison = advisory.get("side_comparison") or {}
        guidance = advisory.get("advisory_guidance") or {}
        summary = _review_summary(card, review) if review else None
        card_row = {
            "card_id": card_id,
            "direction": freeze_row.get("direction"),
            "version": freeze_row.get("version"),
            "event_time_ms": freeze_row.get("event_time_ms"),
            "entry_ms": freeze_row.get("entry_ms"),
            "expiry_ms": freeze_row.get("expiry_ms"),
            "dte_hours": freeze_row.get("dte_hours"),
            "review_status": review_status,
            "review_schema_version": review.get("schema_version"),
            "prompt_version": review.get("prompt_version"),
            "model": review.get("model"),
            "input_packet_hash": review.get("input_packet_hash") or freeze_row.get("packet_hash"),
            "assessment_hash": (summary or {}).get("assessment_hash") or (advisory.get("validation") or {}).get("assessment_hash"),
            "price_bias": price_bias.get("bias"),
            "side_comparison": comparison.get("relative_side"),
            "comparison_status": comparison.get("status"),
            "advisory_summary_cn": guidance.get("summary_cn"),
            "put_grade": (sides.get("put_credit") or {}).get("grade"),
            "put_status": (sides.get("put_credit") or {}).get("status"),
            "call_grade": (sides.get("call_credit") or {}).get("grade"),
            "call_status": (sides.get("call_credit") or {}).get("status"),
            "summary_valid": bool(summary),
        }
        card_rows.append(card_row)
        for side_key in SIDES:
            side = sides.get(side_key) or {}
            side_status = str(side.get("status") or ("UNRATED" if review else "PENDING"))
            grade = side.get("grade")
            coverage["side_status_counts"][side_key][side_status] = coverage["side_status_counts"][side_key].get(side_status, 0) + 1
            if grade in GRADES:
                coverage["grade_counts"][side_key][grade] += 1
                if GRADES.index(grade) >= GRADES.index("B"):
                    coverage["b_or_above_counts"][side_key] += 1
                if grade in {"A", "S"}:
                    coverage["a_or_s_counts"][side_key] += 1
            else:
                coverage["unrated_counts"][side_key] += 1
            role_counts = _side_role_counts(side)
            side_rows.append({
                "card_id": card_id,
                "direction": freeze_row.get("direction"),
                "version": freeze_row.get("version"),
                "event_time_ms": freeze_row.get("event_time_ms"),
                "entry_ms": freeze_row.get("entry_ms"),
                "expiry_ms": freeze_row.get("expiry_ms"),
                "dte_hours": freeze_row.get("dte_hours"),
                "side": side_key,
                "review_status": review_status,
                "side_status": side_status,
                "grade": grade,
                "basis_cn": side.get("basis_cn"),
                "mechanism_cn": (side.get("mechanism") or {}).get("summary_cn"),
                "primary_counter_ref": side.get("primary_counter_ref"),
                "support_ref_count": role_counts["supports_fit"],
                "counter_ref_count": role_counts["counters_fit"],
                "context_ref_count": role_counts["context_only"],
                "comparison": comparison.get("relative_side"),
                "comparison_status": comparison.get("status"),
                "price_bias": price_bias.get("bias"),
                "input_packet_hash": review.get("input_packet_hash") or freeze_row.get("packet_hash"),
                "assessment_hash": card_row["assessment_hash"],
            })
    coverage["independent_event_dates_utc"] = len(event_dates)
    _write_jsonl(ratings_dir / "ratings_by_card.jsonl", card_rows)
    _write_jsonl(ratings_dir / "ratings_by_side.jsonl", side_rows)
    _write_csv(ratings_dir / "ratings_by_card.csv", card_rows)
    _write_csv(ratings_dir / "ratings_by_side.csv", side_rows)
    _write_json(ratings_dir / "coverage.json", coverage)
    return {
        "cards": len(card_rows),
        "sides": len(side_rows),
        "coverage": coverage,
    }


def seal_outputs(ratings_dir: Path) -> dict[str, Any]:
    paths = [
        path for path in ratings_dir.rglob("*")
        if path.is_file() and path.name not in {"seal.json"} and not path.name.endswith(".lock")
    ]
    seal = {
        "schema": SEAL_SCHEMA_VERSION,
        "sealed_at_utc": _now_iso(),
        "ratings_dir": str(ratings_dir),
        "files": _file_manifest(paths, ratings_dir),
    }
    seal["seal_hash"] = _hash_value(seal["files"])
    _write_json(ratings_dir / "seal.json", seal)
    return seal


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="freeze 114 cards, packets and request bodies")
    prepare.add_argument("--study-root", type=Path, required=True)
    prepare.add_argument("--ratings-dir", type=Path, required=True)
    prepare.add_argument("--model", default=None)
    prepare.add_argument("--base-url", default=None)
    prepare.add_argument("--timeout", type=int, default=None)
    prepare.add_argument("--model-config", type=Path, default=None)
    prepare.add_argument("--expected-count", type=int, default=DEFAULT_EXPECTED_COUNT)
    prepare.add_argument("--expected-event-count", type=int, default=DEFAULT_EXPECTED_EVENT_COUNT)
    prepare.add_argument("--expected-fixed-count", type=int, default=DEFAULT_EXPECTED_FIXED_COUNT)
    prepare.add_argument("--force", action="store_true")

    run = sub.add_parser("run", help="run isolated reviews from a prepared frozen bundle")
    run.add_argument("--ratings-dir", type=Path, required=True)
    run.add_argument("--api-key-env", default="LLM_API_KEY")
    run.add_argument("--model", default=None)
    run.add_argument("--base-url", default=None)
    run.add_argument("--timeout", type=int, default=None)
    run.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    run.add_argument("--daily-http-limit", type=int, default=None)
    run.add_argument("--total-http-limit", type=int, default=None)
    run.add_argument("--only-card-id", default=None)
    run.add_argument("--reviewed-at", default=None)

    status = sub.add_parser("status", help="print review progress and budget state")
    status.add_argument("--ratings-dir", type=Path, required=True)

    export = sub.add_parser("export", help="export card and side tables from isolated reviews")
    export.add_argument("--ratings-dir", type=Path, required=True)

    seal = sub.add_parser("seal", help="write a SHA-256 seal over the prepared/review/export files")
    seal.add_argument("--ratings-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "prepare":
        model_config = _load_model_config(args.model_config)
        model = args.model or model_config.get("model") or review_v2.DEFAULT_MODEL
        base_url = args.base_url or model_config.get("base_url") or core.OPENAI_CHAT_COMPLETIONS_ENDPOINT
        timeout = int(args.timeout or model_config.get("timeout") or 240)
        _print_json(prepare_freeze(
            args.study_root,
            args.ratings_dir,
            model=model,
            base_url=base_url,
            timeout=timeout,
            expected_count=args.expected_count,
            expected_event_count=args.expected_event_count,
            expected_fixed_count=args.expected_fixed_count,
            force=args.force,
        ))
    elif args.command == "run":
        api_key = os.environ.get(args.api_key_env, "")
        _print_json(run_reviews(
            args.ratings_dir,
            api_key=api_key,
            model=args.model,
            base_url=args.base_url,
            timeout=args.timeout,
            concurrency=args.concurrency,
            daily_limit=args.daily_http_limit,
            total_limit=args.total_http_limit,
            only_card_id=args.only_card_id,
            reviewed_at=args.reviewed_at,
        ))
    elif args.command == "status":
        _print_json(status_report(args.ratings_dir))
    elif args.command == "export":
        _print_json(export_ratings(args.ratings_dir))
    elif args.command == "seal":
        _print_json(seal_outputs(args.ratings_dir))
    else:
        parser.error("unknown command")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
