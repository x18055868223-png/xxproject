"""Main-chain audit log: a compact, hash-chained, LLM-friendly record of what
the underwriting workbench saw and did.

One JSONL file per Beijing day (``chain-YYYYMMDD.jsonl``) holds periodic
snapshots (default every 15 minutes) plus events (space gate transitions,
reviews, locks, stops). Each file starts with a header that carries the
schema and a Chinese field guide, so a file can be handed to a person or a
model on its own. ``chain-daily.jsonl`` keeps one 23:00 Beijing snapshot per
day and is never pruned; day files older than the retention window are.

Records are derived from the workbench view only: no network, no LLM, no
writes to any other store. A write failure never affects the workbench.

CLI:  python tools/astra_chain_audit_v1.py summarize FILE...
      python tools/astra_chain_audit_v1.py compare --a FILE... --b FILE...
      python tools/astra_chain_audit_v1.py verify FILE...
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA = "astra_chain_audit@1.0.0"
DEFAULT_INTERVAL_MS = 15 * 60_000
DEFAULT_RETENTION_DAYS = 30
DAILY_FILE = "chain-daily.jsonl"
BJT = timezone(timedelta(hours=8))
_DAY_FILE = re.compile(r"^chain-(\d{8})\.jsonl$")

FIELD_GUIDE_CN = {
    "kind": "记录类型：header 文件头；periodic 周期快照；daily 每日 23:00 快照；space_gate_transition 空间门变迁；review 复核；lock 锁定；lock_stop 停止追踪",
    "ts_ms / bjt": "记录时间（毫秒时间戳 / 北京时间）",
    "seq / prev_hash / record_hash": "文件内序号与哈希链：record_hash = sha256(去掉 record_hash 后的规范 JSON)，prev_hash 指向上一条",
    "versions": "版本身份：FMZ 生产端版本、工作台读视图、发布清单、复核提示词与协议",
    "market": "现价（美元）、FMZ 事实状态与年龄、事实缺口原因",
    "refresh": "本轮后台刷新的总耗时与分步耗时（毫秒）；锁定采样节奏约等于总耗时加等待间隔",
    "space_gate": "空间门：state 状态、eligible 是否放行、reasons 原因码、anchor_score 锚点评分（需 ≥60）、net_gamma 净 Gamma（美元）、gamma_regime Gamma 体制、since_ms 当前状态起点",
    "elements": "四要素：tmv 量价趋势（24h/48h 状态与值）、momentum 15 分钟 M-DIE（分数、方向、形态、路径效率、同向占比）、flow 主动成交（4h/12h CVD 与价格涨跌、是否就绪、合成状态）、macro 宏观（体制与得分）",
    "background": "慢背景：资金费率、永续 OI 24h 变化、ETF 近 3 日净流（百万美元）、链上成本价、ATM IV 与 IV 分位",
    "sources_not_ok": "持续采集但当前不正常的数据来源（id、状态、距今毫秒）",
    "candidates": "两侧候选：合约（卖/买行权价、到期、宽度）、短腿 Delta、现价、短腿与盈亏平衡距离（%，按边界价计，正值在责任区外）、信用与扣费后净信用（BTC）、费用及口径、μ（无生产来源时为 null）、快照状态、生产复核门原因、观察复核是否可用、模型解释（类型、状态、适配判断、回执、模型、协议、用途）、程序两问摘要",
    "locks": "锁定追踪：追踪 id、合约、状态、点数、缺口数、最近缺口原因、最近观察时间",
    "llm": "模型复核额度：今日已用 / 上限、是否有复核进行中",
    "event": "事件详情（仅事件记录）",
}

_GATE_KEYS = ("state", "eligible", "reason_codes", "since_ts_ms")


def _num(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(value, 8) if isinstance(value, float) else value


def _get(obj: Any, path: str, default: Any = None) -> Any:
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, Mapping):
            return default
        cur = cur.get(part)
    return default if cur is None else cur


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def record_hash(record: Mapping[str, Any]) -> str:
    body = {k: v for k, v in record.items() if k != "record_hash"}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def bjt_day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, BJT).strftime("%Y%m%d")


def bjt_text(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, BJT).strftime("%Y-%m-%d %H:%M:%S")


def _signed_distance_pct(side: Any, spot: Any, boundary: Any) -> float | None:
    """Same convention as core.signed_boundary_distance: percent of the boundary, positive = outside."""
    spot, boundary = _num(spot), _num(boundary)
    if spot is None or boundary is None or boundary <= 0 or side not in ("put", "call"):
        return None
    sign = 1.0 if side == "put" else -1.0
    return round(sign * (spot - boundary) / boundary * 100, 6)


def _review_summary(candidate: Mapping[str, Any]) -> dict[str, Any]:
    display = candidate.get("review_display") if isinstance(candidate.get("review_display"), Mapping) else {}
    for kind in ("current", "historical"):
        receipt = display.get(kind)
        if isinstance(receipt, Mapping):
            body = receipt.get("review") if isinstance(receipt.get("review"), Mapping) else receipt
            return {
                "kind": kind,
                "status": body.get("status") or receipt.get("status"),
                "applicability": body.get("applicability"),
                "receipt": receipt.get("idempotency_key") or _get(receipt, "display_binding.receipt_id"),
                "reviewed_snapshot_id": body.get("reviewed_snapshot_id"),
                "model": body.get("response_model") or body.get("requested_model"),
                "protocol": body.get("response_protocol"),
                "purpose": receipt.get("purpose"),
            }
    return {"kind": "program", "reason_cn": display.get("reason_cn")}


def _candidate(item: Mapping[str, Any]) -> dict[str, Any]:
    snap = item.get("snapshot") if isinstance(item.get("snapshot"), Mapping) else None
    out: dict[str, Any] = {"side": item.get("side"), "status": item.get("status")}
    if snap is None:
        out["error"] = str(item.get("error") or "")[:200] or None
        out["review_gate"] = sorted(_get(item, "review_gate.reasons", []) or [])
        return out
    contract = snap.get("contract") or {}
    eco = snap.get("economics") or {}
    side = contract.get("side")
    spot = _get(snap, "market.reference_price_usd")
    breakevens = [v for v in (_get(snap, "liability.breakeven_usd", []) or []) if _num(v) is not None]
    questions = item.get("current_questions") if isinstance(item.get("current_questions"), Mapping) else {}
    out.update({
        "candidate_id": snap.get("candidate_id"),
        "snapshot_id": snap.get("snapshot_id"),
        "contract": {"short_usd": _num(contract.get("short_strike_usd")), "long_usd": _num(contract.get("long_strike_usd")),
                     "width_usd": _num(contract.get("width_usd")), "expiry_ms": contract.get("expiry_ms"),
                     "short_instrument": contract.get("short_instrument")},
        "short_delta": _num(_get(item, "discovery.short_delta")),
        "spot_usd": _num(spot),
        "short_distance_pct": _signed_distance_pct(side, spot, contract.get("short_strike_usd")),
        "breakeven_usd": _num(breakevens[0]) if breakevens else None,
        "breakeven_distance_pct": _signed_distance_pct(side, spot, breakevens[0]) if breakevens else None,
        "visible_credit_btc": _num(eco.get("visible_credit_btc")),
        "net_credit_after_fee_btc": _num(eco.get("net_credit_after_fee_btc")),
        "net_credit_after_fee_usd": _num(eco.get("net_credit_after_fee_usd_at_spot")),
        "fee_btc": _num(_get(snap, "fee.amount_btc")),
        "fee_basis": _get(snap, "fee.basis"),
        "mu_ref_btc": _num(eco.get("mu_ref_btc")),
        "snapshot_status": snap.get("status"),
        "quote_gaps": sorted(_get(snap, "market.quote.gap_reasons", []) or []),
        "review_gate": sorted(_get(item, "review_gate.reasons", []) or []),
        "observation_review_enabled": bool(_get(item, "observation_review.enabled", False)),
        "review": _review_summary(item),
        "program_q1": _get(questions, "q1.summary_cn"),
        "program_q2": _get(questions, "q2.summary_cn"),
    })
    return out


def _elements(facts: Mapping[str, Any]) -> dict[str, Any]:
    vp = facts.get("volume_price") or {}
    mdie = facts.get("m_die") or {}
    flow = vp.get("micro_flow") or {}
    macro = facts.get("macro_pressure") or {}

    def tmv(window: Mapping[str, Any]) -> dict[str, Any]:
        return {"state": window.get("final_state"), "value": _num(window.get("tmv_final", window.get("tmv_core"))),
                "ready": window.get("data_ready")}

    def win(window: Mapping[str, Any]) -> dict[str, Any]:
        return {"ready": window.get("data_ready"), "cvd_btc": _num(window.get("cvd_sum")),
                "price_change_pct": _num(window.get("momentum_return_pct")), "coverage_hours": _num(window.get("coverage_hours"))}

    return {
        "tmv": {"24h": tmv(vp.get("tmvf_24h") or {}), "48h": tmv(vp.get("tmvf_48h") or {}),
                "window_conflict": vp.get("window_conflict")},
        "momentum": {"score": _num(mdie.get("score")), "direction": mdie.get("direction"), "shape": mdie.get("move_shape"),
                     "path_efficiency": _num(_get(mdie, "components.path_efficiency.raw.efficiency")),
                     "same_direction_ratio": _num(_get(mdie, "components.directional_persistence.raw.same_direction_ratio"))},
        "flow": {"4h": win(flow.get("fast_4h") or {}), "12h": win(flow.get("slow_12h") or {}),
                 "state": _get(flow, "combined.state"), "direction": _get(flow, "combined.direction"),
                 "ready": _get(flow, "combined.data_ready")},
        "macro": {"regime": macro.get("macro_regime"), "score": _num(macro.get("macro_score")),
                  "data_status": macro.get("data_status")},
    }


def _background(view: Mapping[str, Any], facts: Mapping[str, Any]) -> dict[str, Any]:
    bg = _get(view, "btc_map.background", {}) or {}
    info = _get(facts, "space_context.gex_info", {}) or {}
    oi = _get(bg, "financing.metrics.oi.oi_24h_window") or _get(bg, "financing.metrics.oi_source_values.oi_24h_window") or {}
    etf = _get(bg, "allocation.metrics", {}) or {}
    if _num(etf.get("f3_usd_m")) is None:
        etf = _get(etf, "source_values.metrics", {}) or etf
    cost = _get(bg, "inventory.metrics.cost_basis", {}) or {}
    return {
        "funding_rate": _num(_get(facts, "volume_price.funding.last_rate")),
        "oi_change_24h_pct": _num(oi.get("change_native_pct")),
        "etf_3d_usd_m": _num(etf.get("f3_usd_m")),
        "etf_latest_day": etf.get("latest_complete_date"),
        "cost_basis_usd": {"sth": _num(cost.get("cp_sth_usd")), "lth": _num(cost.get("cp_lth_usd")), "all": _num(cost.get("cp_all_usd"))},
        "atm_iv_pct": _num(info.get("atm_iv")),
        "iv_percentile": _num(info.get("iv_percentile")),
        "fact_states": {k: _get(bg, f"{k}.fact_state") for k in ("allocation", "inventory", "financing", "external_conditions")},
    }


def _sources_not_ok(view: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for pid, product in sorted((_get(view, "data_health.products", {}) or {}).items()):
        status = str(product.get("status") or "")
        current = product.get("current") or []
        if status in ("OK", "USAGE_LIMITED") or not current:
            continue  # healthy, or never collected on this host
        if product.get("collector_id") == "server_deribit_public_quote":
            continue  # written on demand when a quote is frozen; age is not an outage
        usage = product.get("usage_decision") or (current[0] or {}).get("usage_decision") or {}
        out.append({"id": pid, "status": status, "age_ms": _num(usage.get("age_ms"))})
    return out


def _locks(view: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for e in _get(view, "locked_candidates.episodes", []) or []:
        c = e.get("contract") or {}
        out.append({"episode_id": e.get("episode_id"), "status": e.get("status"), "side": c.get("side"),
                    "short_usd": _num(c.get("short_strike_usd")), "long_usd": _num(c.get("long_strike_usd")),
                    "expiry_ms": c.get("expiry_ms"), "points": e.get("point_count"), "gaps": e.get("gap_count"),
                    "last_gap_reason": e.get("last_gap_reason"), "latest_asof_ms": e.get("latest_decision_asof_ms"),
                    "stop_reason": e.get("stop_reason")})
    return out


def snapshot_record(view: Mapping[str, Any], *, kind: str, now_ms: int,
                    prompt_versions: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The full periodic/daily body built from one workbench view."""
    mc = view.get("market_context") or {}
    facts = mc.get("market_facts") or {}
    gate = view.get("space_gate") or {}
    release = view.get("release_identity") or {}
    budget = _get(view, "manual_review_state.daily_http_budget", {}) or {}
    return {
        "kind": kind,
        "ts_ms": int(now_ms),
        "bjt": bjt_text(now_ms),
        "view_updated_at_ms": view.get("updated_at_ms"),
        "refresh": {"total_ms": _num(_get(view, "refresh_timing.total_ms")),
                    "steps_ms": dict(_get(view, "refresh_timing.steps_ms", {}) or {})},
        "versions": {
            "fmz": mc.get("strategy_version"),
            "producer_schema": mc.get("producer_schema_version"),
            "workbench_view": view.get("schema"),
            "manifest_release": release.get("release_id"),
            "manifest_hash": release.get("manifest_hash"),
            **(dict(prompt_versions) if prompt_versions else {}),
        },
        "market": {"spot_usd": _num(mc.get("current_price_usd")), "fact_status": mc.get("status"),
                   "fact_age_ms": _num(mc.get("age_ms")), "fact_gaps": sorted(mc.get("gap_reasons") or [])},
        "space_gate": {
            "state": gate.get("state"), "eligible": gate.get("eligible"), "since_ms": gate.get("since_ts_ms"),
            "reasons": sorted(set((gate.get("reason_codes") or []) + (gate.get("gap_reasons") or []))),
            "anchor_score": _num(_get(gate, "anchor.score")), "anchor_state": _get(gate, "anchor.state"),
            "net_gamma_usd": _num(_get(gate, "net_gamma.value")), "net_gamma_ok": _get(gate, "net_gamma.ok"),
            "gamma_regime": _get(facts, "gamma_regime.regime"),
        },
        "elements": _elements(facts),
        "background": _background(view, facts),
        "sources_not_ok": _sources_not_ok(view),
        "candidates": [_candidate(item) for item in view.get("candidates") or [] if isinstance(item, Mapping)],
        "locks": _locks(view),
        "llm": {"used": budget.get("http_calls_used"), "limit": budget.get("http_calls_limit"),
                "inflight": bool(_get(view, "manual_review_state.inflight"))},
        "gaps": sorted(view.get("gaps") or []),
    }


class ChainAuditLog:
    """Append-only day files with a per-file hash chain; safe to call from any thread."""

    def __init__(self, root: str | Path, *, interval_ms: int = DEFAULT_INTERVAL_MS,
                 retention_days: int = DEFAULT_RETENTION_DAYS,
                 prompt_versions: Mapping[str, Any] | None = None):
        self.root = Path(root)
        self.interval_ms = max(60_000, int(interval_ms))
        self.retention_days = max(1, int(retention_days))
        self.prompt_versions = dict(prompt_versions or {})
        self._lock = threading.Lock()
        self._tail: dict[str, tuple[int, str]] = {}  # file name -> (seq, last hash)
        self._last_slot: int | None = None
        self._last_gate: Any = None
        self._last_daily_day: str | None = None
        self.last_error: str | None = None
        self.last_record_ms: int | None = None

    # ── reading ─────────────────────────────────
    def _tail_of(self, path: Path) -> tuple[int, str] | None:
        if path.name in self._tail:
            return self._tail[path.name]
        if not path.exists():
            return None
        last = None
        with path.open("rb") as handle:
            for line in handle:
                if line.strip():
                    last = line
        if last is None:
            return None
        row = json.loads(last)
        tail = (int(row.get("seq", 0)), str(row.get("record_hash")))
        self._tail[path.name] = tail
        return tail

    def _restore(self) -> None:
        """After a restart, recover the slot, gate and daily markers from the files."""
        if self._last_slot is not None:
            return
        self._last_slot = -1
        days = sorted(p for p in self.root.glob("chain-*.jsonl") if _DAY_FILE.match(p.name))
        for path in reversed(days[-2:]):
            for row in reversed(read_records(path)):
                if row.get("kind") in ("periodic", "daily", "space_gate_transition"):
                    if self._last_gate is None:
                        self._last_gate = _get(row, "space_gate.state")
                    if row.get("kind") == "periodic" and self._last_slot == -1:
                        self._last_slot = int(row["ts_ms"]) // self.interval_ms
                if self._last_gate is not None and self._last_slot != -1:
                    break
        daily = read_records(self.root / DAILY_FILE)
        if daily:
            self._last_daily_day = bjt_day(int(daily[-1]["ts_ms"]))

    # ── writing ─────────────────────────────────
    def _append(self, path: Path, body: dict[str, Any], *, header_ts: int) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        tail = self._tail_of(path)
        lines = []
        if tail is None:
            header = {"schema": SCHEMA, "kind": "header", "ts_ms": header_ts, "bjt": bjt_text(header_ts), "seq": 0,
                      "prev_hash": None, "file": path.name, "field_guide_cn": FIELD_GUIDE_CN,
                      "note_cn": "本文件由工作台按读视图生成，只读；不调用模型、不下单。字段为空表示当时没有该数据。"}
            header["record_hash"] = record_hash(header)
            lines.append(header)
            tail = (0, header["record_hash"])
        record = {"schema": SCHEMA, **body, "seq": tail[0] + 1, "prev_hash": tail[1]}
        record["record_hash"] = record_hash(record)
        lines.append(record)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            for row in lines:
                handle.write(_canonical(row) + "\n")
        self._tail[path.name] = (record["seq"], record["record_hash"])
        return record

    def _guard(self, fn, *args, **kwargs) -> dict[str, Any] | None:
        try:
            with self._lock:
                out = fn(*args, **kwargs)
            self.last_error = None
            return out
        except Exception as exc:  # the audit log must never break the workbench
            self.last_error = type(exc).__name__
            return None

    def observe(self, view: Mapping[str, Any], now_ms: int) -> list[dict[str, Any]]:
        """Called after each refresh: periodic slot, gate transition and the 23:00 daily record."""
        return self._guard(self._observe, view, int(now_ms)) or []

    def _observe(self, view: Mapping[str, Any], now_ms: int) -> list[dict[str, Any]]:
        self._restore()
        written = []
        path = self.root / f"chain-{bjt_day(now_ms)}.jsonl"
        gate_state = _get(view, "space_gate.state")
        if self._last_gate is not None and gate_state != self._last_gate:
            written.append(self._append(path, {"kind": "space_gate_transition", "ts_ms": now_ms, "bjt": bjt_text(now_ms),
                "event": {"from": self._last_gate, "to": gate_state,
                          "reasons": sorted(_get(view, "space_gate.reason_codes", []) or []),
                          "anchor_score": _num(_get(view, "space_gate.anchor.score")),
                          "spot_usd": _num(_get(view, "market_context.current_price_usd"))}}, header_ts=now_ms))
        if gate_state is not None:
            self._last_gate = gate_state
        slot = now_ms // self.interval_ms
        if slot != self._last_slot:
            body = snapshot_record(view, kind="periodic", now_ms=now_ms, prompt_versions=self.prompt_versions)
            written.append(self._append(path, body, header_ts=now_ms))
            self._last_slot = slot
            self._prune(now_ms)
        hour = datetime.fromtimestamp(now_ms / 1000, BJT).hour
        if hour == 23 and self._last_daily_day != bjt_day(now_ms):
            body = snapshot_record(view, kind="daily", now_ms=now_ms, prompt_versions=self.prompt_versions)
            written.append(self._append(self.root / DAILY_FILE, body, header_ts=now_ms))
            self._last_daily_day = bjt_day(now_ms)
        if written:
            self.last_record_ms = now_ms
        return written

    def event(self, kind: str, now_ms: int, detail: Mapping[str, Any], view: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        """A user action (review, lock, stop) with just enough context to place it."""
        def write() -> dict[str, Any]:
            self._restore()
            context = {}
            if isinstance(view, Mapping):
                context = {"spot_usd": _num(_get(view, "market_context.current_price_usd")),
                           "space_gate": _get(view, "space_gate.state"),
                           "fmz": _get(view, "market_context.strategy_version")}
            record = self._append(self.root / f"chain-{bjt_day(int(now_ms))}.jsonl",
                                  {"kind": kind, "ts_ms": int(now_ms), "bjt": bjt_text(int(now_ms)),
                                   "context": context, "event": json.loads(_canonical(detail))}, header_ts=int(now_ms))
            self.last_record_ms = int(now_ms)
            return record
        return self._guard(write)

    def _prune(self, now_ms: int) -> None:
        cutoff = bjt_day(now_ms - self.retention_days * 86_400_000)
        for path in self.root.glob("chain-*.jsonl"):
            match = _DAY_FILE.match(path.name)
            if match and match.group(1) < cutoff:
                path.unlink(missing_ok=True)
                self._tail.pop(path.name, None)

    # ── serving ─────────────────────────────────
    def index(self) -> dict[str, Any]:
        files = []
        if self.root.exists():
            for path in sorted(self.root.glob("chain-*.jsonl"), reverse=True):
                if not (_DAY_FILE.match(path.name) or path.name == DAILY_FILE):
                    continue
                rows = read_records(path)
                kinds: dict[str, int] = {}
                for row in rows:
                    kinds[row.get("kind")] = kinds.get(row.get("kind"), 0) + 1
                files.append({"name": path.name, "bytes": path.stat().st_size, "records": len(rows),
                              "kinds": kinds, "first_ts_ms": rows[0]["ts_ms"] if rows else None,
                              "last_ts_ms": rows[-1]["ts_ms"] if rows else None,
                              "chain_ok": verify_rows(rows)["ok"]})
        return {"schema": SCHEMA + ":index", "interval_ms": self.interval_ms, "retention_days": self.retention_days,
                "last_record_ms": self.last_record_ms, "last_error": self.last_error, "files": files,
                "guide_doc": "docs/astra/CHAIN_AUDIT_LOG.md"}

    def read_file(self, name: str) -> bytes:
        if not (_DAY_FILE.match(name) or name == DAILY_FILE):
            raise ValueError("invalid_audit_file")
        path = self.root / name
        if not path.is_file():
            raise FileNotFoundError(name)
        return path.read_bytes()


def read_records(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def verify_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    prev = None
    for i, row in enumerate(rows):
        if row.get("record_hash") != record_hash(row):
            return {"ok": False, "at": i, "error": "record_hash_mismatch"}
        if row.get("prev_hash") != prev:
            return {"ok": False, "at": i, "error": "prev_hash_mismatch"}
        prev = row.get("record_hash")
    return {"ok": True, "records": len(rows)}


# ── offline analysis for iteration comparisons ───────────────────────────────
def summarize(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Window statistics from periodic/daily snapshots plus event counts."""
    snaps = [r for r in rows if r.get("kind") in ("periodic", "daily")]
    events: dict[str, int] = {}
    for r in rows:
        if r.get("kind") not in ("periodic", "daily", "header"):
            events[r["kind"]] = events.get(r["kind"], 0) + 1
    if not snaps:
        return {"snapshots": 0, "events": events}

    def share(pred) -> float:
        return round(sum(1 for r in snaps if pred(r)) / len(snaps), 4)

    def stats(values: list[float]) -> dict[str, Any] | None:
        values = [v for v in values if isinstance(v, (int, float))]
        if not values:
            return None
        return {"n": len(values), "min": round(min(values), 4), "median": round(statistics.median(values), 4),
                "max": round(max(values), 4)}

    reasons: dict[str, int] = {}
    for r in snaps:
        for code in _get(r, "space_gate.reasons", []) or []:
            reasons[code] = reasons.get(code, 0) + 1
    per_side = {}
    for side in ("put", "call"):
        cands = [c for r in snaps for c in r.get("candidates") or [] if c.get("side") == side and c.get("snapshot_id")]
        per_side[side] = {
            "with_snapshot": len(cands),
            "short_distance_pct": stats([c.get("short_distance_pct") for c in cands]),
            "breakeven_distance_pct": stats([c.get("breakeven_distance_pct") for c in cands]),
            "short_delta": stats([c.get("short_delta") for c in cands]),
            "net_credit_after_fee_usd": stats([c.get("net_credit_after_fee_usd") for c in cands]),
            "review_gate_open_share": round(sum(1 for c in cands if not c.get("review_gate")) / len(cands), 4) if cands else None,
        }
    versions = sorted({json.dumps(r.get("versions") or {}, sort_keys=True) for r in snaps})
    return {
        "snapshots": len(snaps),
        "from": snaps[0].get("bjt"), "to": snaps[-1].get("bjt"),
        "space_gate_open_share": share(lambda r: _get(r, "space_gate.eligible") is True),
        "space_gate_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "anchor_score": stats([_get(r, "space_gate.anchor_score") for r in snaps]),
        "fact_current_share": share(lambda r: _get(r, "market.fact_status") == "current"),
        "flow_ready_share": share(lambda r: _get(r, "elements.flow.ready") is True),
        "sources_not_ok_share": share(lambda r: bool(r.get("sources_not_ok"))),
        "sides": per_side,
        "events": events,
        "versions_seen": [json.loads(v) for v in versions],
    }


def compare(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    """Side-by-side of two summaries, for 'before vs after an iteration'."""
    keys = ("snapshots", "space_gate_open_share", "fact_current_share", "flow_ready_share", "sources_not_ok_share")
    out = {k: {"a": a.get(k), "b": b.get(k)} for k in keys}
    out["anchor_score_median"] = {"a": _get(a, "anchor_score.median"), "b": _get(b, "anchor_score.median")}
    for side in ("put", "call"):
        for metric in ("short_distance_pct", "breakeven_distance_pct", "net_credit_after_fee_usd"):
            out[f"{side}.{metric}.median"] = {"a": _get(a, f"sides.{side}.{metric}.median"),
                                              "b": _get(b, f"sides.{side}.{metric}.median")}
        out[f"{side}.review_gate_open_share"] = {"a": _get(a, f"sides.{side}.review_gate_open_share"),
                                                 "b": _get(b, f"sides.{side}.review_gate_open_share")}
    out["events"] = {"a": a.get("events"), "b": b.get("events")}
    out["versions"] = {"a": a.get("versions_seen"), "b": b.get("versions_seen")}
    return out


def _rows_from(paths: Sequence[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in paths:
        rows.extend(read_records(p))
    return sorted((r for r in rows if r.get("kind") != "header"), key=lambda r: r.get("ts_ms") or 0)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Astra main-chain audit log tools (read-only).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s1 = sub.add_parser("summarize"); s1.add_argument("files", nargs="+")
    s2 = sub.add_parser("compare"); s2.add_argument("--a", nargs="+", required=True); s2.add_argument("--b", nargs="+", required=True)
    s3 = sub.add_parser("verify"); s3.add_argument("files", nargs="+")
    args = parser.parse_args(argv)
    if args.cmd == "summarize":
        out: Any = summarize(_rows_from(args.files))
    elif args.cmd == "compare":
        out = compare(summarize(_rows_from(args.a)), summarize(_rows_from(args.b)))
    else:
        out = {p: verify_rows(read_records(p)) for p in args.files}
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
