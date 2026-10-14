"""WPEM Core thesis ledger for one locked policy (draft approved 2026-10-14).

At lock (t0) the reasons a policy looked acceptable are written as rules a program can
decide; every later sample re-decides them. Pure functions over the lock series rows
(locked_candidate_series metrics); no LLM, no market access, no probability meaning.
Design: docs/astra/WPEM_THESIS_LEDGER_DRAFT_20261014.md.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Sequence

SCHEMA = "astra_thesis_ledger@1.0.0"

HOLDS, WEAKENED, BROKEN = "HOLDS", "WEAKENED", "BROKEN"
UNVERIFIABLE, NOT_RECORDED = "UNVERIFIABLE", "NOT_RECORDED"
SEVERITY = {HOLDS: 0, WEAKENED: 1, BROKEN: 2}
STATE_CN = {HOLDS: "成立", WEAKENED: "减弱", BROKEN: "失效", UNVERIFIABLE: "无法核验", NOT_RECORDED: "未入账"}

# Sampling and persistence rules (draft §3)
GAP_MS = 10 * 60_000             # a gap longer than this is "unverifiable", never interpolated
PERSIST_MS = 30 * 60_000         # a categorical state must hold this long ...
PERSIST_WINDOW_MS = 60 * 60_000  # ... or be this share of the last hour
PERSIST_SHARE = 0.70
PERSIST_MIN_SAMPLES = 10
BAND = 0.10                      # numeric hysteresis: leaving a worse state needs 10% extra

SCALES = (("15m", 15 * 60_000), ("1h", 3_600_000), ("3h", 3 * 3_600_000), ("since_t0", None))
# Each lane says its state in its own words; the generic HOLDS/WEAKENED/BROKEN stay machine keys.
STATUS_CN = {
    "L1": {HOLDS: "缓冲充足", WEAKENED: "缓冲变薄", BROKEN: "已进入责任区"},
    "L2": {HOLDS: "余量充足", WEAKENED: "余量变薄", BROKEN: "已越过"},
    "L3": {HOLDS: "未被侵蚀", WEAKENED: "浮亏扩大", BROKEN: "浮亏超过入场信用"},
    "L4": {HOLDS: "未变差", WEAKENED: "转为过渡", BROKEN: "转为负 Gamma"},
    "L5": {HOLDS: "有效", WEAKENED: "松动", BROKEN: "脱锚"},
    "L6": {HOLDS: "未压向卖出侧", WEAKENED: "出现不利流向", BROKEN: "持续不利流向"},
}
TONE = {HOLDS: "ok", WEAKENED: "warn", BROKEN: "bad", UNVERIFIABLE: "muted", NOT_RECORDED: "muted"}
BJT_MS = 8 * 3_600_000

SHAPE_CN = {"FLAT": "平稳", "JUMP": "单次跳变", "SUSTAINED": "持续单向", "CHOPPY": "反复", "INSUFFICIENT": "不足"}

GAMMA_RANK = {"POSITIVE_GAMMA_PINNING": 2, "TRANSITION": 1, "NEGATIVE_GAMMA_AMPLIFYING": 0}
GAMMA_CN = {"POSITIVE_GAMMA_PINNING": "正 Gamma（钉住）", "TRANSITION": "过渡",
            "NEGATIVE_GAMMA_AMPLIFYING": "负 Gamma（放大）"}
FLOW_CN = {"bullish": "偏买", "neutral_to_bullish": "中性偏买", "neutral": "中性",
           "neutral_to_bearish": "中性偏卖", "bearish": "偏卖"}
ADVERSE_FLOW = {"put": {"bearish", "neutral_to_bearish"}, "call": {"bullish", "neutral_to_bullish"}}
STRONG_ADVERSE_FLOW = {"put": "bearish", "call": "bullish"}
ADVERSE_MDIE = {"put": "DOWN", "call": "UP"}


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _rows(points: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """(t, metrics) in time order; a point's own asof never precedes its record time."""
    out = []
    for row in points or ():
        metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else row
        t = row.get("recorded_at_ms")
        if isinstance(t, int) and isinstance(metrics, Mapping):
            out.append({"t": t, "point_id": row.get("point_id"), "m": metrics})
    out.sort(key=lambda r: r["t"])
    return out


# ── per-sample rules ───────────────────────────────────────────────────────

def _buffer_state(value: float | None, start: float | None, prev: str | None) -> str:
    """L1/L2: >= half the t0 buffer holds, inside it weakens, <= 0 breaks. Entering a worse
    state uses the plain threshold; leaving it needs BAND x t0 more (no flicker at the line)."""
    if value is None or start is None or start <= 0:
        return UNVERIFIABLE
    half, band = 0.5 * start, BAND * start
    if value <= 0:
        return BROKEN
    if prev == BROKEN and value < band:
        return BROKEN
    if value < half:
        return WEAKENED
    if prev in {WEAKENED, BROKEN} and value < half + band:
        return WEAKENED
    return HOLDS


def _credit_state(pnl: float | None, p0: float | None, cap: float | None, prev: str | None) -> str:
    """L3 against the t0 floating PnL (already slightly negative at lock: the bid/ask spread).
    Holds while no worse than p0 - 10% of max profit; broken once the loss exceeds the credit."""
    if pnl is None or p0 is None or cap is None or cap <= 0:
        return UNVERIFIABLE
    line, band = p0 - BAND * cap, BAND * cap
    if pnl <= -cap:
        return BROKEN
    if prev == BROKEN and pnl < -cap + band:
        return BROKEN
    if pnl < line:
        return WEAKENED
    if prev in {WEAKENED, BROKEN} and pnl < line + band:
        return WEAKENED
    return HOLDS


def _gamma_state(regime: Any, base: Any) -> str:
    rank, start = GAMMA_RANK.get(regime), GAMMA_RANK.get(base)
    if rank is None:
        return UNVERIFIABLE
    if start is None:
        start = 2
    if rank == 0:
        return BROKEN
    return WEAKENED if rank < start else HOLDS


def _anchor_state(score: float | None) -> str:
    if score is None:
        return UNVERIFIABLE
    return HOLDS if score >= 60 else WEAKENED if score >= 30 else BROKEN


def _flow_state(m: Mapping[str, Any], side: str) -> str:
    if m.get("flow_ready") is False or m.get("flow_direction") not in FLOW_CN:
        return UNVERIFIABLE
    adverse = ADVERSE_FLOW.get(side, set())
    combined = m.get("flow_direction")
    parts = [m.get("flow_fast_4h"), m.get("flow_slow_12h")]
    drift = m.get("mdie_direction") == ADVERSE_MDIE.get(side) and m.get("mdie_shape") == "DRIFT_RUN"
    if drift or (combined in adverse and all(p in adverse for p in parts)):
        return BROKEN
    if combined in adverse or any(p in adverse for p in parts):
        return WEAKENED
    return HOLDS


# ── persistence and gaps ───────────────────────────────────────────────────

def _effective(rows: Sequence[Mapping[str, Any]], raw: Sequence[str], persist: bool) -> list[str]:
    """Effective state per sample. Categorical lanes change only after the new state lasted
    PERSIST_MS or filled PERSIST_SHARE of the last hour; a sample after a gap starts fresh."""
    out: list[str] = []
    run_start = left = 0
    for i, state in enumerate(raw):
        while rows[i]["t"] - rows[left]["t"] > PERSIST_WINDOW_MS:
            left += 1
        if i and rows[i]["t"] - rows[i - 1]["t"] > GAP_MS:
            run_start = i
            out.append(state)
            continue
        if i == 0 or not persist:
            out.append(state)
            continue
        current = out[-1]
        if state == current:
            out.append(current)
            continue
        if current == UNVERIFIABLE:
            # first real reading after missing data is adopted at once; persistence only
            # damps flicker between readings (and a reading briefly going missing)
            out.append(state)
            continue
        if raw[i - 1] != state:
            run_start = i
        lasted = rows[i]["t"] - rows[run_start]["t"] >= PERSIST_MS
        window = raw[left:i + 1]
        share = window.count(state) / len(window)
        # the share rule needs most of the hour on record, not 10 minutes of a young lock
        covered = rows[i]["t"] - rows[left]["t"] >= 0.75 * PERSIST_WINDOW_MS
        if lasted or (covered and len(window) >= PERSIST_MIN_SAMPLES and share >= PERSIST_SHARE):
            out.append(state)
        else:
            out.append(current)
    return out


def _gaps(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, int]]:
    return [{"from_ms": a["t"], "to_ms": b["t"], "length_ms": b["t"] - a["t"]}
            for a, b in zip(rows, rows[1:]) if b["t"] - a["t"] > GAP_MS]


# ── path shape (draft §4) ──────────────────────────────────────────────────

def path_shape(series: Sequence[tuple[int, float]], *, span_ms: int | None, interval_ms: int,
               flat: float) -> dict[str, Any]:
    """FLAT / JUMP / SUSTAINED / CHOPPY over one window, or INSUFFICIENT."""
    values = [(t, v) for t, v in series if v is not None]
    if len(values) < 2:
        return {"shape": "INSUFFICIENT", "points": len(values), "net": None}
    covered = values[-1][0] - values[0][0]
    expected = (span_ms if span_ms else covered) / max(1, interval_ms)
    max_gap = max(b[0] - a[0] for a, b in zip(values, values[1:]))
    net = values[-1][1] - values[0][1]
    base = {"points": len(values), "net": net, "max_gap_ms": max_gap,
            "from_ms": values[0][0], "to_ms": values[-1][0]}
    if len(values) < expected / 2 or max_gap > 3 * interval_ms:
        return {"shape": "INSUFFICIENT", **base}
    steps = [b[1] - a[1] for a, b in zip(values, values[1:])]
    if abs(net) < flat:
        return {"shape": "FLAT", **base}
    if max(abs(s) for s in steps) >= 0.6 * abs(net):
        return {"shape": "JUMP", **base}
    moving = [s for s in steps if s]
    if moving and sum(1 for s in moving if (s > 0) == (net > 0)) / len(moving) >= 0.7:
        return {"shape": "SUSTAINED", **base}
    return {"shape": "CHOPPY", **base}


def _paths(rows: Sequence[Mapping[str, Any]], key: str, *, interval_ms: int, flat: float) -> dict[str, Any]:
    if not rows:
        return {}
    end = rows[-1]["t"]
    out = {}
    for name, span in SCALES:
        start = rows[0]["t"] if span is None else end - span
        window = [(r["t"], _num(r["m"].get(key))) for r in rows if r["t"] >= start]
        out[name] = path_shape(window, span_ms=span, interval_ms=interval_ms, flat=flat)
    return out


# ── the ledger ─────────────────────────────────────────────────────────────

def _bjt(ms: int | None) -> str:
    if not isinstance(ms, int):
        return "—"
    import time as _time
    return _time.strftime("%m/%d %H:%M", _time.gmtime((ms + BJT_MS) / 1000))


def _dur(ms: int) -> str:
    minutes = int(round(ms / 60_000))
    if minutes < 1:
        return "不到 1 分钟"
    if minutes < 60:
        return f"{minutes} 分钟"
    hours, rest = divmod(minutes, 60)
    return f"{hours} 小时" + (f" {rest} 分" if rest else "")


def _time_in(rows: Sequence[Mapping[str, Any]], effective: Sequence[str]) -> dict[str, int]:
    """Time spent in each state: sample-to-sample intervals, never across a gap."""
    out: dict[str, int] = {}
    for i in range(len(rows) - 1):
        step = rows[i + 1]["t"] - rows[i]["t"]
        if step <= GAP_MS:
            out[effective[i]] = out.get(effective[i], 0) + step
    return out

def _signed(value: float | None, digits: int = 2, unit: str = "%") -> str:
    return "—" if value is None else f"{value:+.{digits}f}{unit}"


def _btc(value: float | None) -> str:
    return "—" if value is None else f"{value:+.4f} BTC"


def _entry(thesis_id: str, question: str, label: str, rule: str, rows: Sequence[Mapping[str, Any]],
           raw: Sequence[str], *, persist: bool, t0_value: Any, value_key: str | None,
           fmt: Callable[[Any], str], stale: bool, claim: str, numeric: bool = False) -> dict[str, Any]:
    effective = _effective(rows, raw, persist)
    t0_state = effective[0]
    graded = [s for s in effective if s in SEVERITY]
    worst = max(graded, key=SEVERITY.get) if graded else UNVERIFIABLE
    now = UNVERIFIABLE if stale else effective[-1]
    since = None
    if now in SEVERITY:
        i = len(effective) - 1
        while i > 0 and effective[i - 1] == now and rows[i]["t"] - rows[i - 1]["t"] <= GAP_MS:
            i -= 1
        since = rows[i]["t"]
    t0_adverse = t0_state in {WEAKENED, BROKEN}
    entry = {
        "id": thesis_id, "question": question, "label_cn": label, "rule_cn": rule,
        "t0_value": t0_value, "now_value": rows[-1]["m"].get(value_key) if value_key else None,
        "t0_value_cn": fmt(t0_value), "now_value_cn": fmt(rows[-1]["m"].get(value_key)) if value_key else "—",
        "state_t0": t0_state, "state_now": now, "state_worst": worst, "state_since_ms": since,
        "t0_adverse": t0_adverse,
        # a t0-adverse lane is a known counter, not a thesis: track whether it eased
        "eased": (t0_adverse and now in SEVERITY and SEVERITY[now] < SEVERITY[t0_state]) if t0_adverse else None,
        "samples": {state: raw.count(state) for state in sorted(set(raw))},
    }
    words = STATUS_CN[thesis_id]
    if not stale and persist and raw[-1] != now and raw[-1] in SEVERITY and now in SEVERITY:
        latest = entry["now_value_cn"] if entry["now_value_cn"] not in {None, "—"} else words[raw[-1]]
        entry["pending_cn"] = f"最新读数已是{latest}，持续未满 30 分钟，暂按“{words[now]}”"
    entry["state_now_cn"] = words.get(now) or ("暂无数据" if now == UNVERIFIABLE else STATE_CN[now])
    if t0_adverse and now in SEVERITY:
        # a known counter at lock: what matters is whether it eased, in absolute terms
        entry["state_now_cn"] = "已缓解" if entry["eased"] else "仍不利"
    entry["state_worst_cn"] = words.get(worst) or "暂无数据"
    entry["tone"] = ("ok" if entry["eased"] else "warn") if t0_adverse and now in SEVERITY else TONE.get(now, "muted")
    entry["claim_cn"] = (f"锁定时已不利：{claim}" if t0_adverse else claim)
    # What happened while held: the extreme reading and the time spent in each worse state.
    spent = _time_in(rows, effective)
    entry["time_in_ms"] = {state: spent.get(state, 0) for state in (WEAKENED, BROKEN)}
    bits = []
    if numeric and value_key:
        values = [(r["t"], _num(r["m"].get(value_key))) for r in rows]
        values = [(t, v) for t, v in values if v is not None]
        if values:
            at, low = min(values, key=lambda tv: tv[1])
            entry["extreme"] = {"value": low, "at_ms": at}
            if _num(t0_value) is not None and low < _num(t0_value):
                bits.append(f"最低 {fmt(low)}（{_bjt(at)}）")
    for state in (BROKEN, WEAKENED):
        if spent.get(state) and not (t0_adverse and SEVERITY.get(state, 0) <= SEVERITY[t0_state]):
            bits.append(f"{words[state]}累计 {_dur(spent[state])}")
    if t0_adverse:
        bad = sum(v for k, v in spent.items() if k in {WEAKENED, BROKEN})
        if bad:
            bits.append(f"不利状态累计 {_dur(bad)}")
    entry["experience_cn"] = "；".join(bits) or "期间没有变差"
    return entry


def _not_recorded(thesis_id: str, question: str, label: str, rule: str, why: str) -> dict[str, Any]:
    return {"id": thesis_id, "question": question, "label_cn": label, "rule_cn": rule,
            "state_t0": NOT_RECORDED, "state_now": NOT_RECORDED, "state_worst": NOT_RECORDED,
            "state_now_cn": STATE_CN[NOT_RECORDED], "state_worst_cn": STATE_CN[NOT_RECORDED],
            "tone": "muted", "claim_cn": why, "experience_cn": "—",
            "t0_adverse": False, "eased": None, "not_recorded_reason_cn": why}


def build(points: Iterable[Mapping[str, Any]], *, side: str, now_ms: int,
          interval_ms: int = 60_000, baseline_point_id: str | None = None,
          expiry_ms: int | None = None, active: bool = True) -> dict[str, Any]:
    """The ledger of one locked policy from its series rows (any metrics version).

    For a stopped or expired policy "now" is its last sample (the final reading); only an
    active policy whose sampling went quiet reads as unverifiable now."""
    rows = _rows(points)
    if baseline_point_id:
        at = next((i for i, r in enumerate(rows) if r["point_id"] == baseline_point_id), 0)
        rows = rows[at:]
    if not rows:
        return {"schema": SCHEMA, "status": "NO_SAMPLES", "theses": [], "llm_call_count": 0}
    side = "call" if side == "call" else "put"
    t0 = rows[0]["m"]
    last_t = rows[-1]["t"]
    settled = expiry_ms is not None and now_ms >= expiry_ms and last_t >= expiry_ms - GAP_MS
    stale = active and now_ms - last_t > GAP_MS and not settled
    theses: list[dict[str, Any]] = []

    # L1 short-leg buffer
    d0 = _num(t0.get("short_distance_pct"))
    prev = None
    raw = []
    for r in rows:
        prev = _buffer_state(_num(r["m"].get("short_distance_pct")), d0, prev)
        raw.append(prev)
    pct = lambda v: _signed(_num(v))
    l1_rule = "距短腿 ≥ 锁定时的一半为成立，进入责任区（≤ 0）为失效"
    if d0 is None or d0 <= 0:
        theses.append(_not_recorded("L1", "Q1", "短腿缓冲", l1_rule, "锁定时短腿已在责任区内或距离未知"))
    else:
        theses.append(_entry("L1", "Q1", "短腿缓冲", l1_rule, rows, raw, persist=False,
                             t0_value=d0, value_key="short_distance_pct", fmt=pct, stale=stale,
                             claim=f"现价在短腿外 {d0:.2f}%", numeric=True))

    # L2 breakeven buffer
    b0 = _num(t0.get("breakeven_distance_pct"))
    l2_rule = "距盈亏平衡 ≥ 锁定时的一半为成立，越过（≤ 0）为失效"
    if b0 is None or b0 <= 0:
        theses.append(_not_recorded("L2", "Q1", "盈亏平衡", l2_rule,
                                    "锁定时盈亏平衡未知（净信用 ≤ 0 或费用未知）"))
    else:
        prev, raw = None, []
        for r in rows:
            prev = _buffer_state(_num(r["m"].get("breakeven_distance_pct")), b0, prev)
            raw.append(prev)
        theses.append(_entry("L2", "Q1", "盈亏平衡", l2_rule, rows, raw, persist=False,
                             t0_value=b0, value_key="breakeven_distance_pct", fmt=pct, stale=stale,
                             claim=f"现价在盈亏平衡外 {b0:.2f}%", numeric=True))

    # L3 collected credit (Deribit floating PnL)
    cap = next((_num(r["m"].get("max_profit_btc")) for r in rows if _num(r["m"].get("max_profit_btc"))), None)
    p0 = next((_num(r["m"].get("float_pnl_btc")) for r in rows if _num(r["m"].get("float_pnl_btc")) is not None), None)
    l3_rule = "浮动盈亏不差于锁定时减 10% 最大收益为成立，浮亏超过全部入场信用为失效"
    if cap is None or p0 is None:
        theses.append(_not_recorded("L3", "Q2", "补偿", l3_rule, "采样行没有浮动盈亏（旧版采样）"))
    else:
        prev, raw = None, []
        for r in rows:
            prev = _credit_state(_num(r["m"].get("float_pnl_btc")), p0, cap, prev)
            raw.append(prev)
        entry = _entry("L3", "Q2", "补偿", l3_rule, rows, raw, persist=False,
                       t0_value=p0, value_key="float_pnl_btc", fmt=lambda v: _btc(_num(v)), stale=stale,
                       claim=f"收下 {cap:.4f} BTC 信用（锁定时浮动 {p0:+.4f}，为买卖价差）", numeric=True)
        entry["max_profit_btc"] = cap
        theses.append(entry)

    # L4–L6 categorical lanes; v3 rows carry their inputs, older rows are unverifiable
    g0 = t0.get("gamma_regime")
    theses.append(_entry("L4", "Q1", "Gamma 体制",
                         "与锁定时相同或更有利为成立，正 → 过渡为减弱，转负（放大）为失效",
                         rows, [_gamma_state(r["m"].get("gamma_regime"), g0) for r in rows], persist=True,
                         t0_value=g0, value_key="gamma_regime", fmt=lambda v: GAMMA_CN.get(v, "—"), stale=stale,
                         claim=f"Gamma 体制为{GAMMA_CN.get(g0, '未知')}"))
    theses.append(_entry("L5", "Q1", "锚定", "≥ 60 成立，30–60 减弱，< 30（脱锚）失效",
                         rows, [_anchor_state(_num(r["m"].get("anchor_score"))) for r in rows], persist=True,
                         t0_value=_num(t0.get("anchor_score")), value_key="anchor_score",
                         fmt=lambda v: "—" if _num(v) is None else f"{_num(v):.0f}/100", stale=stale,
                         claim=("锚点评分（锁定时无数据）" if _num(t0.get("anchor_score")) is None
                                else f"锚点评分 {_num(t0.get('anchor_score')):.0f}/100")))
    theses.append(_entry("L6", "Q1", "流向",
                         "综合与 4h、12h 都朝不利方向（或 M-DIE 同向持续推进）为失效，任一不利为减弱",
                         rows, [_flow_state(r["m"], side) for r in rows], persist=True,
                         t0_value=t0.get("flow_direction"), value_key="flow_direction",
                         fmt=lambda v: FLOW_CN.get(v, "—"), stale=stale,
                         claim=(f"主动流向{FLOW_CN[t0.get('flow_direction')]}" if t0.get("flow_direction") in FLOW_CN
                                else "主动流向（锁定时无数据）")))

    # Paths of the numeric lanes
    paths = {
        "short_distance_pct": _paths(rows, "short_distance_pct", interval_ms=interval_ms, flat=0.10),
        "breakeven_distance_pct": _paths(rows, "breakeven_distance_pct", interval_ms=interval_ms, flat=0.10),
        "float_pnl_btc": _paths(rows, "float_pnl_btc", interval_ms=interval_ms, flat=0.1 * cap if cap else 0.0001),
    }

    # Known-gap ledger (draft §6)
    def gap_set(m: Mapping[str, Any]) -> set[str]:
        return {str(g) for key in ("quote_gaps", "reference_gaps", "float_gaps") for g in (m.get(key) or [])}
    initial, latest = gap_set(t0), gap_set(rows[-1]["m"])
    gap_ledger = {"initial": sorted(initial), "resolved": sorted(initial - latest),
                  "persisting": sorted(initial & latest), "new": sorted(latest - initial)}

    coverage = {"samples": len(rows), "t0_ms": rows[0]["t"], "latest_ms": last_t,
                "stale": stale, "settled_window": settled, "active": active, "gaps": _gaps(rows), "interval_ms": interval_ms}
    ledger = {"schema": SCHEMA, "status": "OK", "side": side, "theses": theses, "paths": paths,
              "gap_ledger": gap_ledger, "coverage": coverage, "llm_call_count": 0,
              "probability_meaning": False}
    ledger["projection_cn"] = project(ledger)
    return ledger


# ── five-question projection (draft §8) ─────────────────────────────────────

def _rhythm(label: str, key: str, lane: Mapping[str, Any], unit: str) -> str | None:
    shape, net = lane.get("shape"), lane.get("net")
    if shape in {None, "INSUFFICIENT"}:
        return None
    if shape == "FLAT":
        return f"{label}平稳"
    amount = f"{abs(net):.2f} 个百分点" if unit == "pp" else f"{abs(net):.4f} BTC"
    if key == "float_pnl_btc":
        way = "回升" if net > 0 else "走低"
    else:
        way = "拉开" if net > 0 else "收窄"
    if shape == "JUMP":
        return f"{label}一次跳变{way} {amount}"
    if shape == "SUSTAINED":
        return f"{label}持续{way} {amount}"
    return f"{label}来回波动，净{way} {amount}"


def project(ledger: Mapping[str, Any]) -> dict[str, Any]:
    theses = list(ledger.get("theses") or [])
    by_id = {t["id"]: t for t in theses}
    graded = [t for t in theses if t["state_now"] in SEVERITY]
    held = [t["label_cn"] for t in graded if t["state_now"] == HOLDS and not t["t0_adverse"]]
    moved = [f"{t['label_cn']}{t['state_now_cn']}" for t in graded
             if t["state_now"] != HOLDS and not t["t0_adverse"]]
    adverse = [f"{t['label_cn']}（{'已缓解' if t['eased'] else '未缓解'}）" for t in graded if t["t0_adverse"]]
    blind = [t["label_cn"] for t in theses if t["state_now"] == UNVERIFIABLE]
    parts = []
    if held:
        parts.append("、".join(held) + "仍成立")
    if moved:
        parts.append("变化：" + "，".join(moved))
    headline = "；".join(parts) if parts else "暂无可判定的依据"
    tail = []
    if adverse:
        tail.append("锁定时已不利：" + "、".join(adverse))
    if blind:
        tail.append("暂无数据：" + "、".join(blind))
    where = []
    for key, label in (("L1", "距短腿"), ("L2", "距盈亏平衡"), ("L3", "浮动盈亏")):
        t = by_id.get(key)
        if t and t.get("state_now") != NOT_RECORDED:
            where.append(f"{label} {t['now_value_cn']}（锁定时 {t['t0_value_cn']}）")
    rhythm = []
    for label, key, unit in (("短腿距离", "short_distance_pct", "pp"), ("浮动盈亏", "float_pnl_btc", "btc")):
        lanes = (ledger.get("paths") or {}).get(key) or {}
        for name, span in (("1h", "近 1 小时"), ("3h", "近 3 小时")):
            text = _rhythm(label, key, lanes.get(name) or {}, unit)
            if text:
                rhythm.append(f"{span}{text}")
    coverage = ledger.get("coverage") or {}
    unknown_cn = []
    if coverage.get("stale"):
        unknown_cn.append("最近 10 分钟没有新采样，现在一栏暂无法核验")
    if not coverage.get("active", True):
        unknown_cn.append("锁定已结束，现在一栏为最后一次采样")
    if coverage.get("gaps"):
        longest = max(g["length_ms"] for g in coverage["gaps"])
        unknown_cn.append(f"采样中断 {len(coverage['gaps'])} 次（最长 {_dur(longest)}），中断期间不作判定")
    theses_cn = [f"{t['label_cn']}：{t['claim_cn']} → 现在{t['state_now_cn']}"
                 + (f"（{t['experience_cn']}）" if t.get("experience_cn") not in {None, "—", "期间没有变差"} else "")
                 for t in theses if t["state_now"] != NOT_RECORDED]
    return {"headline_cn": headline + ("。" + "；".join(tail) if tail else "") + "。",
            "where_cn": "；".join(where) or "—",
            "rhythm_cn": rhythm or ["采样不足以判断近 1–3 小时的节奏"],
            "theses_cn": theses_cn,
            "unknown_cn": unknown_cn,
            # kept for older readers
            "changed_cn": [f"{t['label_cn']} → {t['state_now_cn']}" for t in graded
                           if t["state_t0"] in SEVERITY and t["state_now"] != t["state_t0"]],
            "persistence_cn": rhythm,
            "note_cn": "依据由程序按固定规则判定，是描述不是概率；阈值未经结算校准。"}
