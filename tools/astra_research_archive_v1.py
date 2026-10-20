"""Research archive E0: frozen samples for the long-horizon researcher (docs/opus/08, 2026-10-20).

One tick (every 10 minutes from a systemd timer) does what is due, in this order:

- cases:    once per UTC hour per side, the displayed candidate's frozen case file
- ledger:   for every contract seen since it first appeared until expiry, one compact row
            every 30 minutes, plus a row on a material change, plus every tick in the
            last 3 hours before expiry
- outcomes: after expiry, the delivery price, the payout, each frozen case's result, and
            the 1-minute index path statistics from first sighting to expiry
- inquiry:  at the scheduled hours (UTC 02, 14), the inquirer's output for that hour's case
            (last, so a slow model call never costs the ledger or outcomes their tick)
- inbox:    researcher submissions are validated, stamped and registered (E2)

Everything is append-only. Each written file or line is recorded in chain.jsonl with its
sha256 and the previous entry's hash; verify() re-walks the chain and every recorded line.
A sample is one contract (two legs + expiry) from first sighting to expiry; contracts of
one expiry share the delivery price, so statistics should cluster by expiry day.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import time
import urllib.parse
from contextlib import contextmanager
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping

if __package__:
    from . import astra_case_file_v1 as case_file_core
    from . import astra_commercial_math_v17 as commercial_math
    from . import astra_inquiry_v1 as inquiry
    from . import astra_market_baseline_v1 as market_baseline
    from . import astra_research_inbox_v1 as research_inbox
    from . import astra_research_validation_v1 as research_validation
    from . import astra_research_promotion_v1 as research_promotion
    from . import astra_research_scorecards_v1 as research_scorecards
    from . import astra_event_calendar_v1 as event_calendar
    from . import astra_session_clock_v1 as session_clock
else:
    import astra_case_file_v1 as case_file_core
    import astra_commercial_math_v17 as commercial_math
    import astra_inquiry_v1 as inquiry
    import astra_market_baseline_v1 as market_baseline
    import astra_research_inbox_v1 as research_inbox
    import astra_research_validation_v1 as research_validation
    import astra_research_promotion_v1 as research_promotion
    import astra_research_scorecards_v1 as research_scorecards
    import astra_event_calendar_v1 as event_calendar
    import astra_session_clock_v1 as session_clock

SCHEMA_ROW = "ledger_row@0.1"
# research fields added to ledger rows (user-approved 2026-10-20, Cici's pp-ledger-fields); additive
LEDGER_FIELDS = "ledger_research_fields@0.2"   # 0.2: session clock and entry warnings
TAKER_FEE_BTC = 0.0003            # Deribit BTC option taker fee per contract, capped at 12.5% of the price
TAKER_FEE_PRICE_CAP = 0.125
EVENT_LOOKAHEAD_MS = 7 * 24 * 3_600_000
SCHEMA_OUTCOME = "episode_outcome@0.1"
SCHEMA_CHAIN = "archive_chain@0.1"
SCHEMA_DECISION = "decision_event@0.1"
SCHEMA_INDEX = "episode_index@0.1"
SCHEMA_PROTOCOL = "protocol_version@0.1"
HOUR_MS = 3_600_000
YEAR_MS = 365.0 * 24 * HOUR_MS
BASE_INTERVAL_MS = 30 * 60_000
NEAR_EXPIRY_MS = 3 * HOUR_MS
DISTANCE_TRIGGER_PP = 0.25
TRIGGER_STATES = ("space_gate", "m_die_level", "gamma_regime")
INQUIRY_HOURS_UTC = (2, 14)
INQUIRY_WINDOW_MS = 45 * 60_000
INQUIRY_TIMEOUT_S = 240
NEW_CONTRACT_INQUIRY_MS = 2 * HOUR_MS   # a contract with no questions yet gets them in its first hours
NEW_CONTRACT_INQUIRY_DAILY_MAX = 8       # unscheduled runs per UTC day, both sides together
OUTCOME_DELAY_MS = 10 * 60_000
OUTCOME_GIVE_UP_MS = 12 * HOUR_MS
VALIDATION_AT_UTC = (9, 30)   # after the 08:00 settlement and its outcomes
INDEX_KEEP_CLOSED_MS = 3 * 24 * HOUR_MS
LINE_KINDS = {"ledger", "outcome", "submission", "submission_rejected", "decision", "protocol", "validation",
              "promotion"}
FILE_KINDS = {"case", "inquiry", "legacy", "path"}
PUBLIC = "https://www.deribit.com/api/v2/"
PUBLIC_PATHS = frozenset({"public/get_order_book", "public/get_delivery_prices", "public/get_index_chart_data"})


def _utc(ms: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


class Archive:
    """Append-only files under root, each file or line recorded in a sha256 hash chain."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.state_path = self.root / "_state" / "state.json"
        self.head_path = self.root / "_state" / "chain_head"
        self.chain_path = self.root / "chain.jsonl"

    def load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {"contracts": {}, "cases": {}, "inquiry": {}, "outcomes_done": []}

    def save_state(self, state: Mapping[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name("state.json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _scan_head(self) -> str:
        last = None
        if self.chain_path.exists():
            with self.chain_path.open("rb") as handle:
                for line in handle:
                    if line.strip():
                        last = line
        return json.loads(last)["entry_sha256"] if last else "0" * 64

    def _last_chain_hash(self) -> str:
        # the head file is a cache of the chain's last entry; the chain itself stays authoritative
        if self.head_path.exists() and self.chain_path.exists():
            head = self.head_path.read_text(encoding="utf-8").split()
            if len(head) == 2 and head[1] == str(self.chain_path.stat().st_size):
                return head[0]
        return self._scan_head()

    def _chain(self, rel: str, content_sha: str, at_ms: int, kind: str) -> None:
        prev = self._last_chain_hash()
        entry = {"schema": SCHEMA_CHAIN, "at_ms": at_ms, "kind": kind, "path": rel,
                 "content_sha256": content_sha, "prev_sha256": prev}
        entry["entry_sha256"] = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()
        self.chain_path.parent.mkdir(parents=True, exist_ok=True)
        with self.chain_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        self.head_path.parent.mkdir(parents=True, exist_ok=True)
        self.head_path.write_text(f"{entry['entry_sha256']} {self.chain_path.stat().st_size}", encoding="utf-8")

    def _chained_paths(self) -> set[str]:
        if not self.chain_path.exists():
            return set()
        return {json.loads(l)["path"] for l in self.chain_path.read_text(encoding="utf-8").splitlines() if l.strip()}

    def write_new(self, rel: str, data: bytes, *, at_ms: int, kind: str) -> str:
        """Write a new file; an existing path is never overwritten. A file left unchained by a
        crash between write and chain is adopted only when its bytes are identical."""
        path = self.root / rel
        sha = hashlib.sha256(data).hexdigest()
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() == sha and rel not in self._chained_paths():
                self._chain(rel, sha, at_ms, kind)
                return sha
            raise FileExistsError(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        os.chmod(path, 0o640)
        self._chain(rel, sha, at_ms, kind)
        return sha

    def append_line(self, rel: str, row: Mapping[str, Any], *, at_ms: int, kind: str) -> str:
        line = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with path.open("ab") as handle:
            handle.write(line)
        if new:
            os.chmod(path, 0o640)
        sha = hashlib.sha256(line).hexdigest()
        self._chain(rel, sha, at_ms, kind)
        return sha

    def verify(self) -> dict[str, Any]:
        """Re-walk the chain: links, entry hashes, whole files, and every appended line in order."""
        prev, count, bad = "0" * 64, 0, []
        if not self.chain_path.exists():
            return {"ok": True, "entries": 0, "problems": []}
        expected_lines: dict[str, list[str]] = {}
        for line in self.chain_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            body = {k: v for k, v in entry.items() if k != "entry_sha256"}
            if entry.get("prev_sha256") != prev:
                bad.append({"path": entry.get("path"), "problem": "broken_link"})
            if hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() != entry.get("entry_sha256"):
                bad.append({"path": entry.get("path"), "problem": "entry_hash"})
            if entry.get("kind") in FILE_KINDS:
                path = self.root / entry["path"]
                if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["content_sha256"]:
                    bad.append({"path": entry["path"], "problem": "content_changed_or_missing"})
            elif entry.get("kind") in LINE_KINDS:
                expected_lines.setdefault(entry["path"], []).append(entry["content_sha256"])
            prev = entry.get("entry_sha256")
            count += 1
        for rel, hashes in expected_lines.items():
            path = self.root / rel
            lines = path.read_bytes().splitlines(keepends=True) if path.exists() else []
            for i, expected in enumerate(hashes):
                if i >= len(lines) or hashlib.sha256(lines[i]).hexdigest() != expected:
                    bad.append({"path": rel, "line": i, "problem": "line_changed_or_missing"})
                    break
            if len(lines) > len(hashes):
                bad.append({"path": rel, "problem": "unchained_extra_lines", "count": len(lines) - len(hashes)})
        return {"ok": not bad, "entries": count, "problems": bad}


def contract_key(contract: Mapping[str, Any]) -> str:
    return f"{contract.get('short_instrument')}|{contract.get('long_instrument')}"


def _valid_contract(contract: Mapping[str, Any]) -> bool:
    return (contract.get("side") in {"put", "call"} and _num(contract.get("short_strike_usd"))
            and _num(contract.get("long_strike_usd")) and isinstance(contract.get("expiry_ms"), int)
            and bool(contract.get("short_instrument")) and bool(contract.get("long_instrument")))


def _program_states(view: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if view is None:
        return None
    facts = ((view.get("market_context") or {}).get("market_facts")) or {}
    gex = ((facts.get("space_context") or {}).get("gex_info")) or {}
    vp = facts.get("volume_price") or {}
    gate = view.get("space_gate") or {}
    return {"space_gate": gate.get("producer_state") or gate.get("state"),
            "m_die_level": (facts.get("m_die") or {}).get("level"),
            "gamma_regime": (facts.get("gamma_regime") or {}).get("regime"),
            "gex_market_state": gex.get("market_state"), "flip_usd": _num(gex.get("flip_point")),
            "tmv_24h": (vp.get("tmvf_24h") or {}).get("final_state"),
            "tmv_48h": (vp.get("tmvf_48h") or {}).get("final_state"),
            "micro_flow": vp.get("micro_flow_effect") or (facts.get("flow") or {}).get("micro_flow_effect")}


def ledger_row(record: Mapping[str, Any], short_book: Mapping[str, Any], long_book: Mapping[str, Any],
               states: Mapping[str, Any] | None, *, now_ms: int, displayed: bool | None,
               locked: bool | None = None) -> dict[str, Any]:
    side, ks, kl = record["side"], float(record["short_strike_usd"]), float(record["long_strike_usd"])
    index = _num(short_book.get("index_price")) or _num(long_book.get("index_price"))
    forward = _num(short_book.get("underlying_price")) or _num(long_book.get("underlying_price"))
    bid, ask = _num(short_book.get("best_bid_price")), _num(long_book.get("best_ask_price"))
    credit = bid - ask if bid is not None and ask is not None else None
    sm, lm = _num(short_book.get("mark_price")), _num(long_book.get("mark_price"))
    base = market_baseline.baseline(
        side=side, short_strike=ks, long_strike=kl, forward_usd=forward, valuation_ms=now_ms,
        expiry_ms=int(record["expiry_ms"]), short_iv_pct=short_book.get("mark_iv"), long_iv_pct=long_book.get("mark_iv"),
        short_delta=(short_book.get("greeks") or {}).get("delta"), long_delta=(long_book.get("greeks") or {}).get("delta"),
        short_mark_btc=sm, long_mark_btc=lm, net_credit_btc=credit if credit and credit > 0 else None,
        width_usd=abs(ks - kl))
    probs = base.get("probabilities") or {}
    boundaries = probs.get("loss_boundaries_usd") or []
    pct = (lambda k: (k / index - 1.0) * 100.0 if index and k else None)
    leg = lambda b: {k: b.get(k) for k in ("best_bid_price", "best_ask_price", "mark_price", "mark_iv")} | {
        "delta": (b.get("greeks") or {}).get("delta"), "timestamp": b.get("timestamp")}
    return {
        "schema": SCHEMA_ROW, "at_ms": now_ms, "contract_key": record["key"], "side": side,
        "expiry_ms": record["expiry_ms"], "displayed": displayed, "locked": locked,
        "short": leg(short_book), "long": leg(long_book),
        "credit_bid_ask_btc": credit, "mark_spread_btc": (sm - lm) if sm is not None and lm is not None else None,
        "index_usd": index, "forward_usd": forward,
        "short_from_index_pct": pct(ks), "long_from_index_pct": pct(kl),
        "breakeven_from_index_pct": pct(boundaries[0]) if boundaries else None,
        "baseline_status": base.get("status"),
        "p_itm_short": probs.get("p_itm_short"), "p_touch_short": probs.get("p_touch_short"),
        "p_loss": probs.get("p_loss"), "p_max_loss": probs.get("p_max_loss"),
        "states": dict(states) if states is not None else None,
        "view_available": states is not None,
    }


def _m(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def market_context(view: Mapping[str, Any] | None, index_points: Any, *, now_ms: int | None = None) -> dict[str, Any]:
    """View-level inputs shared by every contract's research fields in one tick; tolerant of
    any shape (a malformed input only leaves its fields None)."""
    view = _m(view)
    facts = _m(_m(view.get("market_context")).get("market_facts"))
    gex = _m(_m(facts.get("space_context")).get("gex_info"))
    expiry_gamma = _m(view.get("expiry_gamma"))
    by_expiry = {}
    for row in _m(expiry_gamma.get("by_expiry")).values():
        if isinstance(row, Mapping) and isinstance(row.get("expiry_ms"), int):
            by_expiry[row["expiry_ms"]] = row
    points = []
    for point in index_points if isinstance(index_points, list) else []:
        if isinstance(point, (list, tuple)) and len(point) == 2 and _num(point[0]) is not None and _num(point[1]):
            points.append((int(point[0]), float(point[1])))
    warnings = {}
    for item in view.get("candidates") or []:
        contract = _m(_m(_m(item).get("snapshot")).get("contract"))
        if contract.get("short_instrument") and contract.get("long_instrument"):
            warnings[f"{contract['short_instrument']}|{contract['long_instrument']}"] = _m(item).get("entry_warnings")
    upcoming = [e for e in event_calendar.events() if now_ms is None or e["at_ms"] > now_ms]
    return {"by_expiry": by_expiry, "realized_vol": _m(expiry_gamma.get("realized_vol")), "gex": gex,
            "index_points": sorted(points), "upcoming_events": upcoming, "warnings": warnings,
            "calendar_complete_7d": not event_calendar.coverage_gaps(now_ms + EVENT_LOOKAHEAD_MS, now_ms)
            if now_ms is not None else False}


def _index_change(points: list[tuple[int, float]], now_ms: int, back_ms: int, current: float | None) -> float | None:
    """Percent change of the index from back_ms ago to now; None without a point within 5 minutes."""
    target = now_ms - back_ms
    best = None
    for t, p in points:
        if abs(t - target) <= 5 * 60_000 and (best is None or abs(t - target) < abs(best[0] - target)):
            best = (t, p)
    return (current / best[1] - 1.0) * 100.0 if best and current else None


def research_fields(record: Mapping[str, Any], row: Mapping[str, Any], ctx: Mapping[str, Any], *,
                    now_ms: int) -> dict[str, Any]:
    """Mechanism fields for hypotheses (σ distance, IV vs RV, compensation after fee, this
    expiry's walls, macro events, recent index moves), all from data the tick already holds.
    A field without its inputs is None."""
    side, ks = record["side"], float(record["short_strike_usd"])
    expiry = int(record["expiry_ms"])
    adverse = 1.0 if side == "call" else -1.0
    hours = (expiry - now_ms) / HOUR_MS
    pct = row.get("short_from_index_pct")
    cushion = adverse * pct if pct is not None else None          # > 0: the short leg is still out of the money
    erow = ctx["by_expiry"].get(expiry) or {}
    atm = _num(erow.get("atm_iv_pct"))
    move = atm * math.sqrt(hours / 8760.0) if atm and hours > 0 else None
    rv = ctx["realized_vol"]
    rv24, rv7 = _num(rv.get("rv_24h_pct")), _num(rv.get("rv_7d_pct"))
    gex = ctx["gex"]
    label = case_file_core.expiry_label(expiry)
    rr = next((_num(r.get("rr")) for r in gex.get("rr_skew") or []
               if isinstance(r, Mapping) and label and r.get("expiry") == label), None)
    short, long = row.get("short") or {}, row.get("long") or {}
    bid, ask = _num(short.get("best_bid_price")), _num(long.get("best_ask_price"))
    fee = (min(TAKER_FEE_BTC, TAKER_FEE_PRICE_CAP * bid) + min(TAKER_FEE_BTC, TAKER_FEE_PRICE_CAP * ask)) \
        if bid is not None and ask is not None else None
    credit, spread = row.get("credit_bid_ask_btc"), row.get("mark_spread_btc")
    after_fee = credit - fee if credit is not None and fee is not None else None
    # the case file's L2.compensation is only defined for a positive credit
    ratio = after_fee / spread if after_fee is not None and after_fee > 0 and spread and spread > 0 else None
    tier = None if ratio is None else "rich" if ratio >= market_baseline.COMPENSATION_RICH_AT else \
        "thin" if ratio < market_baseline.COMPENSATION_THIN_BELOW else "fair"
    walls = erow.get("walls") or {}
    wall = _num(walls.get("gamma_call" if side == "call" else "gamma_put"))
    pin, index = _num(walls.get("pin")), row.get("index_usd")
    upcoming = [e for e in ctx.get("upcoming_events") or [] if e["at_ms"] > now_ms]
    nxt = upcoming[0] if upcoming and upcoming[0]["at_ms"] - now_ms <= EVENT_LOOKAHEAD_MS else None
    # nothing within 7 days of a complete calendar is a value (capped at 168 h, kind "none"), not missing
    quiet = nxt is None and ctx.get("calendar_complete_7d")
    gaps_to_expiry = event_calendar.coverage_gaps(expiry, now_ms)
    return {
        "fields_version": LEDGER_FIELDS,
        "expiry_atm_iv_pct": atm, "expected_move_pct": move, "short_cushion_pct": cushion,
        "distance_in_moves": cushion / move if cushion is not None and move else None,
        "rv_24h_pct": rv24, "rv_7d_pct": rv7,
        "iv_minus_rv_24h": atm - rv24 if atm is not None and rv24 is not None else None,
        "dvol": _num(gex.get("dvol")), "iv_percentile": _num(gex.get("iv_percentile")), "rr_25d_expiry": rr,
        "entry_fee_btc": fee, "credit_after_fee_btc": after_fee, "comp_ratio_after_fee": ratio, "comp_tier": tier,
        "gamma_wall_usd": wall,
        "gamma_wall_vs_short_pct": adverse * (wall - ks) / ks * 100.0 if wall else None,
        "pin_from_index_pct": (pin / index - 1.0) * 100.0 if pin and index else None,
        "hours_to_next_event": (nxt["at_ms"] - now_ms) / HOUR_MS if nxt else EVENT_LOOKAHEAD_MS / HOUR_MS if quiet else None,
        "next_event_kind": nxt["kind"] if nxt else "none" if quiet else None,
        "event_before_expiry": None if gaps_to_expiry else any(now_ms < e["at_ms"] < expiry for e in upcoming),
        "index_chg_1h_pct": _index_change(ctx["index_points"], now_ms, HOUR_MS, index),
        "index_chg_4h_pct": _index_change(ctx["index_points"], now_ms, 4 * HOUR_MS, index),
        **_session_fields(now_ms, expiry),
        **_warning_fields((ctx.get("warnings") or {}).get(record.get("key"))),
    }


def _session_fields(now_ms: int, expiry: int) -> dict[str, Any]:
    try:
        clock = session_clock.reading(now_ms, expiry)
        return {"session_phase": clock["phase"]["code"], "remaining_clock_factor": clock["remaining_clock_factor"],
                "session_clock": session_clock.SCHEMA}
    except Exception:
        return {"session_phase": None, "remaining_clock_factor": None, "session_clock": None}


def _warning_fields(block: Any) -> dict[str, Any]:
    """The displayed candidate's entry warnings (docs/opus/13), copied from the read view;
    contracts not on the page get nulls."""
    out = {"warning_any": None, "warning_term_inversion": None, "warning_premium_thickening": None,
           "warning_busy_window": None, "warning_rules": None}
    if not isinstance(block, Mapping) or not isinstance(block.get("warnings"), list):
        return out
    out["warning_rules"] = block.get("rules")   # threshold versions stay separable in later validation
    by_code = {w.get("code"): w.get("triggered") for w in block["warnings"] if isinstance(w, Mapping)}
    out.update({"warning_term_inversion": by_code.get("term_inversion"),
                "warning_premium_thickening": by_code.get("premium_thickening"),
                "warning_busy_window": by_code.get("busy_window_thin_cushion")})
    known = [v for v in by_code.values() if v is not None]
    out["warning_any"] = True if any(known) else (False if len(known) == len(by_code) and by_code else None)
    return out


def _crossed(prev: float | None, cur: float | None) -> bool:
    return prev is not None and cur is not None and (prev > 0) != (cur > 0)


def change_detail(last: Mapping[str, Any] | None, row: Mapping[str, Any]) -> list[str]:
    """What changed since the last written row: crossed_<leg>, distance, state:<name>,
    displayed, locked. Empty when nothing material changed."""
    if last is None:
        return []
    detail = []
    for key, name in (("short_from_index_pct", "crossed_short"), ("breakeven_from_index_pct", "crossed_breakeven"),
                      ("long_from_index_pct", "crossed_long")):
        if _crossed(last.get(key), row.get(key)):
            detail.append(name)
    a, b = last.get("short_from_index_pct"), row.get("short_from_index_pct")
    if a is not None and b is not None and abs(a - b) > DISTANCE_TRIGGER_PP:
        detail.append("distance")
    # program states and display only compare when both sides read the view
    if last.get("states") is not None and row.get("states") is not None:
        detail += [f"state:{k}" for k in TRIGGER_STATES if last["states"].get(k) != row["states"].get(k)]
        if last.get("displayed") is not None and row.get("displayed") is not None \
                and last["displayed"] != row["displayed"]:
            detail.append("displayed")
    if last.get("locked") is not None and row.get("locked") is not None and last["locked"] != row["locked"]:
        detail.append("locked")
    return detail


def row_trigger(last: Mapping[str, Any] | None, row: Mapping[str, Any], *, now_ms: int) -> str | None:
    if last is None:
        return "first"
    if int(row["expiry_ms"]) - now_ms <= NEAR_EXPIRY_MS:
        return "near_expiry"
    if now_ms - int(last["at_ms"]) >= BASE_INTERVAL_MS:
        return "base"
    return "change" if change_detail(last, row) else None


def path_stats(record: Mapping[str, Any], points: list[list[float]], delivery: float) -> dict[str, Any]:
    """Index from first sighting to expiry: touch, closest approach, adverse excursion, RV."""
    start, end = int(record["first_seen_ms"]), int(record["expiry_ms"])
    series = sorted((int(t), float(p)) for t, p in points if start <= int(t) <= end and p)
    if len(series) < 2:
        return {"status": "insufficient_path", "points": len(series)}
    ks, kl, side = float(record["short_strike_usd"]), float(record["long_strike_usd"]), record["side"]
    prices = [p for _, p in series]
    adverse = (lambda p: (ks - p) / ks * 100.0) if side == "put" else (lambda p: (p - ks) / ks * 100.0)
    beyond = (lambda p, k: p <= k) if side == "put" else (lambda p, k: p >= k)
    steps = [(b[0] - a[0], a, b) for a, b in zip(series, series[1:])]
    touched = [t for t, p in series if beyond(p, ks)]
    rets = [(math.log(b[1] / a[1]), d) for d, a, b in steps if a[1] > 0 and b[1] > 0 and d > 0]
    span = sum(d for _, d in rets)
    rv = math.sqrt(sum(r * r for r, _ in rets) / (span / YEAR_MS)) if rets and span else None
    dts = sorted(d for d, _, _ in steps)
    gap = series[0][0] - start
    return {"status": "ok", "points": len(series), "start_ms": series[0][0], "end_ms": series[-1][0],
            "median_step_ms": dts[len(dts) // 2], "coverage_gap_ms": gap,
            "path_complete": gap <= 2 * dts[len(dts) // 2] and end - series[-1][0] <= 2 * dts[len(dts) // 2],
            "min_usd": min(prices), "max_usd": max(prices),
            "touched_short": bool(touched), "first_touch_ms": touched[0] if touched else None,
            "ms_beyond_short": sum(d for d, a, _ in steps if beyond(a[1], ks)),
            "touched_long": any(beyond(p, kl) for p in prices),
            "max_adverse_vs_short_pct": max(adverse(p) for p in prices),
            "settle_vs_short_pct": adverse(delivery), "realized_vol_annual": rv}


class Deribit:
    def __init__(self, transport: Callable[[str, dict[str, Any]], Any] | None = None) -> None:
        self.transport = transport or self._get

    @staticmethod
    def _get(path: str, params: dict[str, Any]) -> Any:
        if path not in PUBLIC_PATHS:
            raise ValueError("only audited public read endpoints")
        url = PUBLIC + path + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as response:
            body = json.load(response)
        if not isinstance(body, dict) or body.get("error") or "result" not in body:
            raise ValueError("deribit public " + path + " invalid result")
        return body["result"]

    def book(self, name: str) -> dict[str, Any]:
        book = self.transport("public/get_order_book", {"instrument_name": name, "depth": 1})
        if not isinstance(book, dict) or book.get("instrument_name") != name:
            raise ValueError("book mismatch " + name)
        return book

    def delivery(self, expiry_ms: int) -> float | None:
        day = _utc(expiry_ms).strftime("%Y-%m-%d")
        for row in (self.transport("public/get_delivery_prices", {"index_name": "btc_usd", "count": 10}) or {}).get("data") or []:
            if row.get("date") == day and _num(row.get("delivery_price")):
                return float(row["delivery_price"])
        return None

    def index_path(self) -> list[list[float]]:
        return list(self.transport("public/get_index_chart_data", {"index_name": "btc_usd", "range": "2d"}) or [])


def instrument_key(contract: Mapping[str, Any]) -> str | None:
    short, long = contract.get("short_instrument"), contract.get("long_instrument")
    return f"{short}|{long}" if short and long else None


def collect_decisions(locked_episodes: list[Mapping[str, Any]], dp_records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """User-side decision events from the workbench: lock created / stopped, Decision Path rows."""
    events: list[dict[str, Any]] = []
    for ep in locked_episodes:
        contract = ep.get("contract") or {}
        key = instrument_key(contract)
        if not key or not ep.get("episode_id"):
            continue
        base = {"source": "lock", "contract_key": key, "side": contract.get("side"), "ref_id": ep["episode_id"]}
        if isinstance(ep.get("created_at_ms"), int):
            events.append({**base, "event": "lock_created", "at_ms": ep["created_at_ms"],
                           "detail": {"candidate_id": ep.get("candidate_id"),
                                      "source_episode_id": ep.get("source_episode_id")}})
        if ep.get("status") != "active" and isinstance(ep.get("stopped_at_ms"), int):
            events.append({**base, "event": "lock_stopped", "at_ms": ep["stopped_at_ms"],
                           "detail": {"status": ep.get("status"), "stop_reason": ep.get("stop_reason")}})
    for row in dp_records:
        contract = row.get("contract") or {}
        key = instrument_key(contract)
        if not key or not row.get("verification_id") or not isinstance(row.get("at_ms"), int):
            continue
        detail = {k: row.get(k) for k in ("conclusion", "verdicts_start", "verdicts_now", "q1_items", "marks",
                                          "settled_after", "entry_after_fee_btc", "outcome", "path_version")
                  if row.get(k) is not None}
        events.append({"source": "decision_path", "contract_key": key, "side": contract.get("side"),
                       "ref_id": row["verification_id"], "event": row.get("event"), "at_ms": row["at_ms"],
                       "detail": detail})
    return sorted(events, key=lambda e: (e["at_ms"], e["source"], e["ref_id"], e["event"] or ""))


def active_locked_keys(locked_episodes: list[Mapping[str, Any]]) -> set[str]:
    return {k for ep in locked_episodes if ep.get("status") == "active"
            for k in [instrument_key(ep.get("contract") or {})] if k}


def observed_versions() -> dict[str, str]:
    """Versions that the code in force carries; recorded whenever one changes."""
    return {"inquirer_prompt": inquiry.PROMPT_VERSION,
            "case_file_builder": case_file_core.BUILDER_VERSION,
            "ledger_row": SCHEMA_ROW, "episode_outcome": SCHEMA_OUTCOME,
            "submission_schemas": ",".join(sorted(research_inbox.SCHEMAS)),
            "scoring_rules": research_validation.SCORING_RULES,
            "scorecard_rules": research_scorecards.SCORECARD_RULES,
            "ledger_fields": LEDGER_FIELDS,
            "entry_warnings": _entry_warnings_rules(), "session_clock": session_clock.SCHEMA}


def _entry_warnings_rules() -> str | None:
    try:
        if __package__:
            from . import astra_entry_warnings_v1 as warnings_core
        else:
            import astra_entry_warnings_v1 as warnings_core
        return warnings_core.RULES
    except ImportError:
        return None


def _episode_summary(record: Mapping[str, Any], status: str, outcome_path: str | None = None) -> dict[str, Any]:
    return {"contract_key": record["key"], "side": record["side"],
            "short_strike_usd": record["short_strike_usd"], "long_strike_usd": record["long_strike_usd"],
            "expiry_ms": record["expiry_ms"], "first_seen_ms": record["first_seen_ms"], "status": status,
            "cases": [{k: c.get(k) for k in ("case_id", "path", "asof_ms")} for c in record.get("cases") or []],
            "inquiry": list(record.get("inquiry") or []), "ledger_rows": record.get("ledger_rows", 0),
            "last_row_at_ms": record.get("last_row_at_ms"), "locked": record.get("locked"),
            "decisions": list(record.get("decisions") or []),
            "outcome": {"path": outcome_path, "status": status} if outcome_path else None}


def reconcile_state(archive: Archive) -> dict[str, Any]:
    """Recount ledger rows and relink inquiry files for the open contracts from the chained
    files (counters that started after a contract was first seen lag behind). Idempotent."""
    state = archive.load_state()
    contracts = state.get("contracts") or {}
    rows: dict[str, int] = {}
    last_at: dict[str, int] = {}
    for path in sorted((archive.root / "ledger").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line) if line.strip() else None
                key, at = (row.get("contract_key"), int(row["at_ms"])) if isinstance(row, dict) else (None, 0)
            except (ValueError, KeyError, TypeError):
                continue
            if key in contracts:
                rows[key] = rows.get(key, 0) + 1
                last_at[key] = max(last_at.get(key, 0), at)
    case_to_key = {c["case_id"]: c["contract_key"] for c in (state.get("cases") or {}).values()
                   if c.get("case_id") and c.get("contract_key")}
    for record in contracts.values():
        for case in record.get("cases") or []:
            case_to_key.setdefault(case.get("case_id"), record["key"])
    links: dict[str, list[str]] = {}
    for path in sorted((archive.root / "inquiry").glob("*/*.json")):
        case_id = "cf_" + path.stem.rsplit("_cf_", 1)[-1] if "_cf_" in path.stem else None
        key = case_to_key.get(case_id)
        if key in contracts:
            links.setdefault(key, []).append(str(path.relative_to(archive.root)).replace("\\", "/"))
    changed = 0
    for key, record in contracts.items():
        new = {"ledger_rows": rows.get(key, 0), "inquiry": links.get(key, [])}
        if key in last_at:
            new["last_row_at_ms"] = last_at[key]
        if any(record.get(k) != v for k, v in new.items()):
            record.update(new)
            changed += 1
    archive.save_state(state)
    return {"contracts": len(contracts), "changed": changed}


def write_index(archive: Archive, state: Mapping[str, Any], *, now_ms: int) -> None:
    """Derived per-expiry-day index; rebuilt while a day has open or recently closed contracts.
    Not chained: it can always be rebuilt from the chained files."""
    by_day: dict[str, list[dict[str, Any]]] = {}
    for record in state["contracts"].values():
        by_day.setdefault(_utc(int(record["expiry_ms"])).strftime("%Y-%m-%d"), []).append(_episode_summary(record, "open"))
    for summary in (state.get("closed_recent") or {}).values():
        by_day.setdefault(_utc(int(summary["expiry_ms"])).strftime("%Y-%m-%d"), []).append(summary)
    chain_lines = 0
    if archive.chain_path.exists():
        with archive.chain_path.open("rb") as handle:
            chain_lines = sum(1 for line in handle if line.strip())
    for day, episodes in by_day.items():
        path = archive.root / "index" / "episodes" / f"{day}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {"schema": SCHEMA_INDEX, "expiry_day": day, "built_at_ms": now_ms, "chain_lines": chain_lines,
                "derived": True, "episodes": sorted(episodes, key=lambda e: (e["first_seen_ms"], e["contract_key"]))}
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.chmod(tmp, 0o640)
        os.replace(tmp, path)


def tick(archive: Archive, *, now_ms: int, view: Mapping[str, Any] | None,
         fetch_case: Callable[[str], Mapping[str, Any]], deribit: Deribit,
         run_inquiry: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
         inbox: tuple[Path, Path] | None = None,
         decision_source: Callable[[], tuple[list[Any], list[Any]]] | None = None) -> dict[str, Any]:
    state = archive.load_state()
    state.setdefault("closed_recent", {})
    state.setdefault("decisions_seen", [])
    state.setdefault("protocol_versions", {})
    done = set(state.get("outcomes_done") or [])
    report: dict[str, Any] = {"cases": 0, "inquiry": 0, "rows": 0, "outcomes": 0, "decisions": 0, "errors": []}

    for component, version in observed_versions().items():
        if state["protocol_versions"].get(component) != version:
            archive.append_line("protocols/versions.jsonl", {
                "schema": SCHEMA_PROTOCOL, "at_ms": now_ms, "component": component, "version": version,
                "source": "observed_in_code", "previous": state["protocol_versions"].get(component),
                "note_cn": "运行中的代码所带版本；研究协议与评分规则的版本由用户批准后登记"}, at_ms=now_ms, kind="protocol")
            state["protocol_versions"][component] = version
    archive.save_state(state)
    displayed: dict[str, dict[str, Any]] = {}
    for item in (view or {}).get("candidates") or []:
        snap = item.get("snapshot") if isinstance(item, Mapping) else None
        contract = (snap or {}).get("contract") or {}
        if not _valid_contract(contract):
            continue
        key = contract_key(contract)
        if key in done or contract["expiry_ms"] <= now_ms:
            continue   # never re-open a settled or expired contract
        displayed[key] = item
        state["contracts"].setdefault(key, {
            "key": key, "side": contract["side"], "short_instrument": contract["short_instrument"],
            "long_instrument": contract["long_instrument"], "short_strike_usd": contract["short_strike_usd"],
            "long_strike_usd": contract["long_strike_usd"], "expiry_ms": contract["expiry_ms"],
            "first_seen_ms": now_ms, "cases": [], "last_row": None})

    locked_keys: set[str] | None = None
    if decision_source is not None:
        try:
            locked_episodes, dp_records = decision_source()
            locked_keys = active_locked_keys(locked_episodes)
            seen = set(state["decisions_seen"])
            for event in collect_decisions(locked_episodes, dp_records):
                ident = f"{event['source']}|{event['ref_id']}|{event['event']}|{event['at_ms']}"
                if ident in seen:
                    continue
                archive.append_line(f"decisions/{_utc(event['at_ms']).strftime('%Y-%m-%d')}.jsonl",
                                    {"schema": SCHEMA_DECISION, **event}, at_ms=now_ms, kind="decision")
                state["decisions_seen"].append(ident)
                seen.add(ident)
                record = state["contracts"].get(event["contract_key"])
                if record is not None:
                    record.setdefault("decisions", []).append(
                        {k: event[k] for k in ("source", "event", "at_ms", "ref_id")})
                report["decisions"] += 1
            archive.save_state(state)
        except Exception as exc:
            report["errors"].append({"step": "decisions", "error": type(exc).__name__})

    hour = _utc(now_ms).strftime("%Y-%m-%dT%H")
    day = hour[:10]
    new_cases: list[tuple[str, Mapping[str, Any]]] = []
    for key, item in displayed.items():
        side, snap = item.get("side"), item["snapshot"]
        slot_key = f"{hour}|{side}"
        if slot_key in state["cases"]:
            continue
        try:
            case = fetch_case(snap["snapshot_id"])
            data = gzip.compress(json.dumps(case, ensure_ascii=False, sort_keys=True).encode("utf-8"), mtime=0)
            rel = f"cases/{day}/{hour[11:]}_{side}_{case['case_id']}.json.gz"
            archive.write_new(rel, data, at_ms=now_ms, kind="case")
            credit = (case_entry(case, "L1.economics") or {}).get("net_credit_after_fee_btc")
            state["cases"][slot_key] = {"case_id": case["case_id"], "path": rel, "contract_key": key}
            state["contracts"][key]["cases"].append({"case_id": case["case_id"], "asof_ms": case.get("asof_ms"),
                                                     "net_credit_after_fee_btc": credit, "path": rel})
            new_cases.append((side, case))
            report["cases"] += 1
        except Exception as exc:
            report["errors"].append({"step": "case", "side": side, "error": type(exc).__name__, "detail": str(exc)[:160]})
    archive.save_state(state)

    states = _program_states(view)
    index_points: list[list[float]] | None = None
    if any(int(r["expiry_ms"]) > now_ms for r in state["contracts"].values()):
        try:
            index_points = deribit.index_path()
        except Exception as exc:
            report["errors"].append({"step": "index_path", "error": type(exc).__name__})
    try:
        ctx = market_context(view, index_points, now_ms=now_ms)
    except Exception as exc:
        report["errors"].append({"step": "ledger_context", "error": type(exc).__name__})
        ctx = market_context(None, None)
    for key, record in list(state["contracts"].items()):
        if int(record["expiry_ms"]) <= now_ms:
            continue
        try:
            row = ledger_row(record, deribit.book(record["short_instrument"]), deribit.book(record["long_instrument"]),
                             states, now_ms=now_ms, displayed=(key in displayed) if view is not None else None,
                             locked=(key in locked_keys) if locked_keys is not None else None)
        except Exception as exc:
            report["errors"].append({"step": "ledger", "contract": key, "error": type(exc).__name__})
            continue
        try:
            row.update(research_fields(record, row, ctx, now_ms=now_ms))
        except Exception as exc:   # the row is still written with its original fields
            report["errors"].append({"step": "ledger_fields", "contract": key, "error": type(exc).__name__})
        trigger = row_trigger(record.get("last_row"), row, now_ms=now_ms)
        if trigger:
            row["trigger"] = trigger
            row["trigger_detail"] = change_detail(record.get("last_row"), row)
            archive.append_line(f"ledger/{_utc(now_ms).strftime('%Y-%m-%d')}.jsonl", row, at_ms=now_ms, kind="ledger")
            record["last_row"] = {k: row.get(k) for k in ("at_ms", "expiry_ms", "short_from_index_pct",
                                                          "breakeven_from_index_pct", "long_from_index_pct",
                                                          "states", "displayed", "locked")}
            record["ledger_rows"] = record.get("ledger_rows", 0) + 1
            record["last_row_at_ms"] = now_ms
            if row.get("locked") is not None:
                record["locked"] = row["locked"]
            archive.save_state(state)   # a crash right after never re-appends this row
            report["rows"] += 1

    # the same 2-day path fetched for the ledger serves the outcomes of this tick
    path_cache: list[list[float]] | None = index_points if isinstance(index_points, list) and len(index_points) >= 2 else None
    delivery_cache: dict[int, float | None] = {}
    for key, record in list(state["contracts"].items()):
        expiry = int(record["expiry_ms"])
        if now_ms < expiry + OUTCOME_DELAY_MS:
            continue
        rel = f"outcomes/{_utc(expiry).strftime('%Y-%m-%d')}.jsonl"
        try:
            if expiry not in delivery_cache:
                delivery_cache[expiry] = deribit.delivery(expiry)
            delivery = delivery_cache[expiry]
            if delivery is None:
                raise LookupError("delivery price not published")
            if path_cache is None:
                path_cache = deribit.index_path()
                if len(path_cache) < 2:
                    path_cache = None
                    raise LookupError("index path empty")
            path_rel = f"paths/{_utc(expiry).strftime('%Y-%m-%d')}.json.gz"
            if not (archive.root / path_rel).exists():
                # the minute index path is kept so outcomes after any entry time can be recomputed (E3)
                archive.write_new(path_rel, gzip.compress(json.dumps(
                    {"schema": "index_path@0.1", "index_name": "btc_usd", "expiry_ms": expiry,
                     "fetched_at_ms": now_ms, "points": path_cache}, separators=(",", ":")).encode(), mtime=0),
                    at_ms=now_ms, kind="path")
            payout = commercial_math.inverse_spread_payout(
                record["side"], float(record["short_strike_usd"]), float(record["long_strike_usd"]), delivery, 1.0)
            cases = [{**c, "result_btc": (c["net_credit_after_fee_btc"] - payout)
                      if _num(c.get("net_credit_after_fee_btc")) is not None else None} for c in record["cases"]]
            outcome = {"schema": SCHEMA_OUTCOME, "contract_key": key, "status": "settled", "side": record["side"],
                       "short_strike_usd": record["short_strike_usd"], "long_strike_usd": record["long_strike_usd"],
                       "expiry_ms": expiry, "first_seen_ms": record["first_seen_ms"],
                       "delivery_price_usd": delivery, "payout_btc": payout, "cases": cases,
                       "path": path_stats(record, path_cache, delivery),
                       "meaning_cn": "到期静态结果：各冻结点扣费后净信用 − 到期赔付（币本位）；不是实际成交"}
        except Exception as exc:
            if now_ms <= expiry + OUTCOME_GIVE_UP_MS:
                continue   # retry on a later tick
            outcome = {"schema": SCHEMA_OUTCOME, "contract_key": key, "status": "outcome_unavailable",
                       "reason": type(exc).__name__, "expiry_ms": expiry, "first_seen_ms": record["first_seen_ms"]}
        archive.append_line(rel, outcome, at_ms=now_ms, kind="outcome")
        state["outcomes_done"].append(key)
        state["closed_recent"][key] = _episode_summary(record, outcome["status"], rel)
        del state["contracts"][key]
        archive.save_state(state)   # a crash right after never writes a second outcome
        if outcome["status"] == "settled":
            report["outcomes"] += 1

    scheduled = _utc(now_ms).hour in INQUIRY_HOURS_UTC and now_ms % HOUR_MS < INQUIRY_WINDOW_MS
    if run_inquiry:
        for side, case in new_cases:
            ikey = f"{hour}|{side}"
            if ikey in state["inquiry"]:
                continue
            ckey = next((c["contract_key"] for c in state["cases"].values() if c.get("case_id") == case["case_id"]), None)
            record = state["contracts"].get(ckey) or {}
            fresh = not record.get("inquiry") and now_ms - int(record.get("first_seen_ms") or 0) <= NEW_CONTRACT_INQUIRY_MS
            fresh_runs = state.setdefault("inquiry_fresh", {})
            if not scheduled:
                if not fresh or fresh_runs.get(day, 0) >= NEW_CONTRACT_INQUIRY_DAILY_MAX:
                    continue
                fresh_runs[day] = fresh_runs.get(day, 0) + 1
                for old in sorted(fresh_runs)[:-7]:
                    del fresh_runs[old]
            # recorded before the call: a slow or failed run is never repeated within the slot
            state["inquiry"][ikey] = "running"
            archive.save_state(state)
            try:
                result = dict(run_inquiry(case_file_core.for_inquirer(case)))
            except Exception as exc:
                result = {"schema": inquiry.SCHEMA, "status": "ERROR", "error": type(exc).__name__,
                          "prompt_version": inquiry.PROMPT_VERSION}
            try:
                irel = f"inquiry/{day}/{hour[11:]}_{side}_{case['case_id']}.json"
                archive.write_new(irel, json.dumps({"case_id": case["case_id"], "asof_ms": case.get("asof_ms"), **result},
                                                   ensure_ascii=False, sort_keys=True).encode("utf-8"),
                                  at_ms=now_ms, kind="inquiry")
                ckey = next((c["contract_key"] for c in state["cases"].values() if c.get("case_id") == case["case_id"]), None)
                if ckey in state["contracts"]:
                    state["contracts"][ckey].setdefault("inquiry", []).append(irel)
                report["inquiry"] += 1
            except Exception as exc:
                report["errors"].append({"step": "inquiry_write", "side": side, "error": type(exc).__name__})
            state["inquiry"][ikey] = result.get("status")
            archive.save_state(state)

    if inbox is not None:
        try:
            report["inbox"] = research_inbox.ingest(
                archive, inbox[0], inbox[1], now_ms=now_ms,
                open_contracts={k: int(r["expiry_ms"]) for k, r in state["contracts"].items()})
        except Exception as exc:
            report["errors"].append({"step": "inbox", "error": type(exc).__name__})

    moment = _utc(now_ms)
    today = moment.strftime("%Y-%m-%d")
    if (moment.hour, moment.minute) >= VALIDATION_AT_UTC and state.get("validation_day") != today:
        try:
            report["validation"] = research_validation.run(archive, now_ms=now_ms)
            state["validation_day"] = today   # set at once: a later failure never repeats validation
            archive.save_state(state)
        except Exception as exc:
            report["errors"].append({"step": "validation", "error": type(exc).__name__, "detail": str(exc)[:160]})
        try:
            report["promotion_candidates"] = research_promotion.write_candidates(archive.root, now_ms=now_ms)
        except Exception as exc:
            report["errors"].append({"step": "promotion_candidates", "error": type(exc).__name__})
    if (moment.hour, moment.minute) >= VALIDATION_AT_UTC and state.get("scorecard_day") != today:
        state["scorecard_day"] = today   # once a day; a failure waits for the next day or the CLI
        archive.save_state(state)
        try:
            report["scorecards"] = research_scorecards.run(archive.root, now_ms=now_ms)
        except Exception as exc:
            report["errors"].append({"step": "scorecards", "error": type(exc).__name__, "detail": str(exc)[:160]})

    state["outcomes_done"] = state["outcomes_done"][-2000:]
    state["decisions_seen"] = state["decisions_seen"][-5000:]
    for name in ("cases", "inquiry"):   # slot keys sort by hour; keep the recent ones
        state[name] = dict(sorted(state[name].items())[-500:])
    state["closed_recent"] = {k: v for k, v in state["closed_recent"].items()
                              if now_ms - int(v["expiry_ms"]) <= INDEX_KEEP_CLOSED_MS}
    state["last_tick_ms"] = now_ms
    archive.save_state(state)
    try:
        write_index(archive, state, now_ms=now_ms)
    except Exception as exc:
        report["errors"].append({"step": "index", "error": type(exc).__name__})
    return report


LEGACY_SOURCES = (
    ("locked_candidates/index.json", "legacy/locked_candidates/index.json"),
    ("locked_settlements.json", "legacy/locked_settlements.json"),
    ("decision_path/records.jsonl", "legacy/decision_path_records.jsonl"),
    ("underwriting.jsonl", "legacy/underwriting.jsonl"),
)
LEGACY_NOTE = """# legacy/（存档上线前的历史，一次性导入）

导入时间见 chain.jsonl 中 kind=legacy 的记录。这些数据产生于 2026-10-20 研究存档上线之前：
- 格式与现行案卷、账本不同，冻结程度也不同（每日快照不含信号层市场事实；锁定点含完整冻结包）；
- 只用于熟悉问题域、形成假设；**不能用于前向验证**（前向验证只使用假设登记之后冻结的样本）。

内容：locked_candidates/（锁定库索引与冻结点）、locked_settlements.json（锁定到期静态结果）、
decision_path_records.jsonl（旧人读核验路径记录）、underwriting.jsonl、daily_snapshots/（每日快照与归档）。
"""


def backfill_legacy(archive: Archive, workbench_dir: str | Path, *, now_ms: int) -> dict[str, Any]:
    """One-time import of pre-archive history into legacy/, each file chained (kind legacy)."""
    src = Path(workbench_dir)
    if (archive.root / "legacy").exists():
        return {"skipped": "legacy_exists"}
    written = 0

    def put(rel: str, data: bytes) -> None:
        nonlocal written
        archive.write_new(rel, data, at_ms=now_ms, kind="legacy")
        written += 1

    put("legacy/README.md", LEGACY_NOTE.encode("utf-8"))
    for source, rel in LEGACY_SOURCES:
        path = src / source
        if path.exists():
            put(rel + ".gz", gzip.compress(path.read_bytes(), mtime=0))
    points = src / "locked_candidates" / "points"
    if points.is_dir():
        for path in sorted(points.iterdir()):
            if path.is_file():
                data = path.read_bytes()
                put(f"legacy/locked_candidates/points/{path.name}" + ("" if path.suffix == ".gz" else ".gz"),
                    data if path.suffix == ".gz" else gzip.compress(data, mtime=0))
    for path in sorted(src.glob("daily_snapshots*.jsonl*")):
        if path.name.endswith(".lock"):
            continue
        data = path.read_bytes()
        put(f"legacy/daily_snapshots/{path.name}" + ("" if path.suffix == ".gz" else ".gz"),
            data if path.suffix == ".gz" else gzip.compress(data, mtime=0))
    return {"legacy_files": written}


def case_entry(case: Mapping[str, Any], ref: str) -> Any:
    at = (case.get("refs") or {}).get(ref)
    if not at:
        return None
    return case["layers"][at["layer"]][at["index"]].get("values")


def _workbench_decisions(workbench_dir: Path) -> tuple[list[Any], list[Any]]:
    """Lock episodes and Decision Path rows, read from the workbench state the archiver can read."""
    index = json.loads((workbench_dir / "locked_candidates" / "index.json").read_text(encoding="utf-8"))
    episodes = index.get("episodes") or []
    episodes = list(episodes.values()) if isinstance(episodes, Mapping) else list(episodes)
    records_path = workbench_dir / "decision_path" / "records.jsonl"
    records = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()] \
        if records_path.exists() else []
    return episodes, records


def _http_json(url: str, token: str | None = None, timeout: int = 180) -> Any:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description="research archive tick")
    parser.add_argument("command", choices=["tick", "verify", "backfill-legacy", "protocol-register",
                                            "promote", "retire", "readings", "scorecards", "reconcile-state"])
    parser.add_argument("--hypothesis")
    parser.add_argument("--reading")
    parser.add_argument("--label", default="")
    parser.add_argument("--approval", default="", help="what the user approved, in their words")
    parser.add_argument("--allow-provisional", action="store_true")
    parser.add_argument("--workbench-dir", default=os.environ.get("ASTRA_WORKBENCH_STATE_DIR", "/var/lib/xxproject/workbench"))
    parser.add_argument("--component")
    parser.add_argument("--version")
    parser.add_argument("--note", default="")
    parser.add_argument("--root", default=os.environ.get("ASTRA_RESEARCH_ARCHIVE", "/var/lib/xxproject/research_archive"))
    parser.add_argument("--inbox", default=os.environ.get("ASTRA_RESEARCH_INBOX", "/var/lib/xxproject/research_inbox"))
    parser.add_argument("--receipts", default=os.environ.get("ASTRA_RESEARCH_RECEIPTS",
                                                             "/var/lib/xxproject/research_archive/receipts"))
    parser.add_argument("--workbench", default="http://127.0.0.1:8895")
    args = parser.parse_args()
    archive = Archive(args.root)
    if args.command == "verify":
        print(json.dumps(archive.verify(), ensure_ascii=False))
        return
    if args.command != "tick":
        # operator commands write the same state and chain as the timer tick: never alongside it
        with operator_lock(archive):
            return _operator(args, archive)
    _tick_main(args, archive)


@contextmanager
def operator_lock(archive: Archive):
    lock = archive.root / "_state" / "tick.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit("a tick or another command is running (_state/tick.lock); retry after it finishes")
    os.close(fd)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _operator(args: argparse.Namespace, archive: Archive) -> None:
    if args.command == "backfill-legacy":
        print(json.dumps(backfill_legacy(archive, args.workbench_dir, now_ms=int(time.time() * 1000))))
        return
    if args.command == "promote":
        print(json.dumps(research_promotion.promote(
            archive, args.hypothesis, now_ms=int(time.time() * 1000), label_cn=args.label,
            approval_cn=args.approval, allow_provisional=args.allow_provisional), ensure_ascii=False))
        return
    if args.command == "retire":
        print(json.dumps(research_promotion.retire(
            archive, args.reading, now_ms=int(time.time() * 1000), reason_cn=args.note), ensure_ascii=False))
        return
    if args.command == "reconcile-state":
        print(json.dumps(reconcile_state(archive), ensure_ascii=False))
        return
    if args.command == "scorecards":
        print(json.dumps(research_scorecards.run(archive.root, now_ms=int(time.time() * 1000)), ensure_ascii=False))
        return
    if args.command == "readings":
        print(json.dumps(research_promotion.active_readings(archive.root), ensure_ascii=False, indent=1))
        return
    if args.command == "protocol-register":
        if args.component not in {"research_protocol", "scoring_rules", "inquirer_prompt"} or not args.version:
            raise SystemExit("--component research_protocol|scoring_rules|inquirer_prompt and --version are required")
        now = int(time.time() * 1000)
        archive.append_line("protocols/versions.jsonl", {
            "schema": SCHEMA_PROTOCOL, "at_ms": now, "component": args.component, "version": args.version,
            "source": "approved_by_user", "note_cn": args.note}, at_ms=now, kind="protocol")
        print("registered")
        return
    raise SystemExit(f"unknown command {args.command}")


def _tick_main(args: argparse.Namespace, archive: Archive) -> None:
    token = os.environ.get("ASTRA_WORKBENCH_TOKEN")
    key = os.environ.get("LLM_API_KEY") or os.environ.get("ASTRA_WORKBENCH_LLM_API_KEY")
    model = os.environ.get("ASTRA_WORKBENCH_LLM_MODEL") or "deepseek-flash"
    lock = archive.root / "_state" / "tick.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            stale = time.time() - lock.stat().st_mtime >= 3 * 3600
        except FileNotFoundError:
            stale = True
        if not stale:
            print(json.dumps({"skipped": "previous_tick_running"}))
            return
        lock.unlink(missing_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.close(fd)
        try:
            view = _http_json(args.workbench + "/api/workbench")
        except Exception as exc:
            view = None
            print(json.dumps({"view_error": type(exc).__name__}))
        report = tick(archive, now_ms=int(time.time() * 1000), view=view,
                      fetch_case=lambda sid: _http_json(
                          args.workbench + "/api/workbench/case-file/" + urllib.parse.quote(sid, safe=""), token),
                      deribit=Deribit(),
                      run_inquiry=(lambda case: inquiry.run(case, api_key=key, model=model, timeout=INQUIRY_TIMEOUT_S))
                      if key else None,
                      inbox=(Path(args.inbox), Path(args.receipts)),
                      decision_source=lambda: _workbench_decisions(Path(args.workbench_dir)))
        print(json.dumps(report, ensure_ascii=False))
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
