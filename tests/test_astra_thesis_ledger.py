"""WPEM Core thesis ledger (draft approved 2026-10-14)."""
import pytest

from tools import astra_thesis_ledger_v1 as L
from tools import astra_locked_candidate_compare_v1 as compare
from test_astra_locked_followup import lock, setup

MIN = 60_000
T0 = 1_791_000_000_000


def rows(values, *, key="short_distance_pct", step=MIN, start=T0, **extra):
    out = []
    for i, v in enumerate(values):
        m = {"short_distance_pct": 1.0, "breakeven_distance_pct": 1.2, "float_pnl_btc": -0.0003,
             "max_profit_btc": 0.002, "gamma_regime": "POSITIVE_GAMMA_PINNING", "anchor_score": 70.0,
             "flow_direction": "neutral", "flow_ready": True, "flow_fast_4h": "neutral",
             "flow_slow_12h": "neutral", "quote_gaps": [], "reference_gaps": [], "float_gaps": []}
        m.update(extra)
        m[key] = v
        out.append({"point_id": f"p{i}", "recorded_at_ms": start + i * step, "metrics": m})
    return out


def thesis(ledger, tid):
    return next(t for t in ledger["theses"] if t["id"] == tid)


def build(rs, **kw):
    kw.setdefault("side", "put")
    kw.setdefault("now_ms", rs[-1]["recorded_at_ms"] + MIN)
    return L.build(rs, **kw)


def test_short_buffer_states_worst_and_recovery():
    led = build(rows([1.0, 0.8, 0.4, -0.2, -0.5, 0.2, 0.7, 0.9]))
    l1 = thesis(led, "L1")
    assert l1["state_t0"] == "HOLDS" and l1["state_worst"] == "BROKEN"
    assert l1["state_now"] == "HOLDS"  # states recover; worst is kept separately
    assert "持有以来最差失效" in " ".join(led["projection_cn"]["theses_cn"])


def test_numeric_hysteresis_stops_flicker_at_the_line():
    # half of d0=1.0 is 0.5; values hover around it inside the 10% band
    led = build(rows([1.0, 0.49, 0.52, 0.48, 0.55, 0.47, 0.58]))
    raw_flips = thesis(led, "L1")["samples"]
    assert raw_flips == {"HOLDS": 1, "WEAKENED": 6}
    assert thesis(led, "L1")["state_now"] == "WEAKENED"
    led = build(rows([1.0, 0.49, 0.61]))
    assert thesis(led, "L1")["state_now"] == "HOLDS"  # clearly back above half + band


def test_credit_is_judged_against_the_t0_float_pnl():
    # at lock the floating PnL is already -0.0003 (the spread): that is not "weakened"
    led = build(rows([-0.0003, -0.0004, -0.0007, -0.0021, 0.001], key="float_pnl_btc"))
    l3 = thesis(led, "L3")
    assert l3["state_t0"] == "HOLDS"
    assert l3["state_worst"] == "BROKEN" and l3["state_now"] == "HOLDS"
    assert l3["max_profit_btc"] == pytest.approx(0.002)


def test_categorical_change_needs_to_persist():
    brief = ["TRANSITION"] * 10 + ["POSITIVE_GAMMA_PINNING"] * 30
    led = build(rows([1.0] * 41, gamma_regime="POSITIVE_GAMMA_PINNING")[:1]
                + [{**r, "metrics": {**r["metrics"], "gamma_regime": g}}
                   for r, g in zip(rows([1.0] * 41)[1:], brief)])
    l4 = thesis(led, "L4")
    assert l4["state_worst"] == "HOLDS"  # 10 minutes of TRANSITION is noise
    lasting = ["TRANSITION"] * 40
    led = build(rows([1.0])[:1] + [{**r, "metrics": {**r["metrics"], "gamma_regime": g}}
                                   for r, g in zip(rows([1.0] * 41)[1:], lasting)])
    assert thesis(led, "L4")["state_now"] == "WEAKENED"


def test_t0_adverse_lane_is_a_known_counter_tracked_for_easing():
    base = rows([1.0] * 50, gamma_regime="NEGATIVE_GAMMA_AMPLIFYING")
    for r in base[10:]:
        r["metrics"]["gamma_regime"] = "TRANSITION"
    l4 = thesis(build(base), "L4")
    assert l4["t0_adverse"] is True and l4["state_t0"] == "BROKEN"
    assert l4["eased"] is True
    assert "锁定时即不利" in " ".join(build(base)["projection_cn"]["theses_cn"])


def test_flow_persistent_pressure_and_call_mirror():
    persistent = dict(flow_direction="bearish", flow_fast_4h="bearish", flow_slow_12h="neutral_to_bearish")
    assert thesis(build(rows([1.0, 1.0], **persistent)), "L6")["state_now"] == "BROKEN"
    one = dict(flow_direction="neutral", flow_fast_4h="bearish", flow_slow_12h="neutral")
    assert thesis(build(rows([1.0, 1.0], **one)), "L6")["state_now"] == "WEAKENED"
    assert thesis(build(rows([1.0, 1.0], **persistent), side="call"), "L6")["state_now"] == "HOLDS"
    drift = dict(mdie_direction="DOWN", mdie_shape="DRIFT_RUN")
    assert thesis(build(rows([1.0, 1.0], **drift)), "L6")["state_now"] == "BROKEN"
    assert thesis(build(rows([1.0, 1.0], flow_ready=False)), "L6")["state_now"] == "UNVERIFIABLE"


def test_gaps_quiet_active_lock_and_stopped_lock():
    rs = rows([1.0, 0.9, 0.8])
    late = rs[-1]["recorded_at_ms"] + 30 * MIN
    led = build(rs, now_ms=late)
    assert thesis(led, "L1")["state_now"] == "UNVERIFIABLE" and led["coverage"]["stale"]
    led = build(rs, now_ms=late, active=False)
    assert thesis(led, "L1")["state_now"] == "HOLDS"
    assert "锁定已结束" in " ".join(led["projection_cn"]["unknown_cn"])
    gapped = rows([1.0, 0.9]) + rows([0.8], start=T0 + 60 * MIN)
    assert len(build(gapped)["coverage"]["gaps"]) == 1


def test_not_recorded_when_t0_breakeven_unknown():
    led = build(rows([1.0, 1.0], breakeven_distance_pct=None))
    l2 = thesis(led, "L2")
    assert l2["state_now"] == "NOT_RECORDED" and "盈亏平衡未知" in l2["not_recorded_reason_cn"]


@pytest.mark.parametrize("values,shape", [
    ([1.0] * 16, "FLAT"),
    ([1.0] * 8 + [0.5] * 8, "JUMP"),
    ([1.0 - 0.04 * i for i in range(16)], "SUSTAINED"),
    ([1.0, 0.8, 1.0, 0.7, 0.9, 0.6, 0.8, 0.5, 0.7, 0.45, 0.6, 0.45, 0.55, 0.4, 0.5, 0.4], "CHOPPY"),
])
def test_path_shapes(values, shape):
    series = [(T0 + i * MIN, v) for i, v in enumerate(values)]
    assert L.path_shape(series, span_ms=15 * MIN, interval_ms=MIN, flat=0.10)["shape"] == shape


def test_path_insufficient_on_sparse_or_gapped_samples():
    sparse = [(T0 + i * 5 * MIN, 1.0 - 0.1 * i) for i in range(4)]
    assert L.path_shape(sparse, span_ms=15 * MIN, interval_ms=MIN, flat=0.1)["shape"] == "INSUFFICIENT"


def test_gap_ledger_tracks_resolved_and_new():
    rs = rows([1.0, 1.0], quote_gaps=["short_depth_insufficient"])
    rs[-1]["metrics"] = {**rs[-1]["metrics"], "quote_gaps": [], "float_gaps": ["long_mark_missing"]}
    gaps = build(rs)["gap_ledger"]
    assert gaps["resolved"] == ["short_depth_insufficient"] and gaps["new"] == ["long_mark_missing"]


def test_series_v3_reads_producer_facts_and_endpoint_carries_the_ledger(tmp_path):
    wb, _, clock, snapshot = setup(tmp_path)
    episode_id = lock(wb, snapshot)["episode"]["episode_id"]
    clock[0] += 120_000
    wb.refresh(now_ms=clock[0], record_daily=False)
    out = wb.locked_candidate_series(episode_id)
    point = out["points"][-1]
    assert point["metrics_version"] == compare.SERIES_METRICS_VERSION == 3
    for key in ("anchor_score", "net_gamma_sign", "flow_fast_4h", "flow_slow_12h",
                "mdie_direction", "mdie_shape", "remaining_ms"):
        assert key in point
    ledger = out["thesis_ledger"]
    assert ledger["schema"] == L.SCHEMA and ledger["status"] == "OK"
    assert [t["id"] for t in ledger["theses"]] == ["L1", "L2", "L3", "L4", "L5", "L6"]
    assert ledger["llm_call_count"] == 0 and ledger["probability_meaning"] is False
    assert ledger["projection_cn"]["note_cn"]


def test_first_reading_after_missing_data_is_adopted_at_once():
    rs = rows([1.0] * 40, anchor_score=None)
    for r in rs[-3:]:
        r["metrics"]["anchor_score"] = 35.0
    assert thesis(build(rs), "L5")["state_now"] == "WEAKENED"
    # a reading going missing for two samples does not flip it to unverifiable
    rs = rows([1.0] * 40, anchor_score=70.0)
    for r in rs[-2:]:
        r["metrics"]["anchor_score"] = None
    assert thesis(build(rs), "L5")["state_now"] == "HOLDS"
