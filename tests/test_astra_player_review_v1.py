"""Tests for Track 2: short buyback quote, flow/EM metrics, 4-category triggers, debounce scheduler, and hold review AI protocol."""
from copy import deepcopy
import datetime as dt
import json
import urllib.error

import pytest

from tools import astra_locked_candidate_compare_v1 as compare
from tools import astra_player_dossier_v1 as dossier
from tools import astra_player_review_v1 as review
from tools import signal_llm_review
from tests.test_astra_locked_candidate_compare_v1 import payload_with_components, point_from_payload
from tests.test_astra_player_dossier_v1 import Clock, EXCERPTS as DOSSIER_EXCERPTS, M as DOSSIER_M, SLOT, plan as dossier_plan, raw as dossier_raw, source as dossier_source
from tests.test_astra_underwriting_v20 import T

M = 60_000


def _make_series_point(
    *,
    side="put",
    spot=100_000.0,
    short_strike=97_000.0,
    long_strike=95_000.0,
    asof_ms=T,
    long_bids=None,
    long_asks=None,
    short_asks=None,
    short_ts=None,
    taker_commission=0.0003,
    expected_move_usd=1800.0,
    flow_ready=True,
    flow_direction="neutral",
    flow_cvd_4h=15.0,
    flow_cvd_12h=40.0,
    flow_mom_4h=0.10,
    flow_mom_12h=0.25,
    flow_asof_ms=None,
    expiry_ms=None,
    pin=100_000.0,
    pin_status="ok",
    pin_method="astra_expiry_gamma_v1@1.0.0:profile.expected_move_max_unsigned_gamma",
    pin_source="deribit_public/get_book_summary_by_currency",
    computed_at_ms=None,
    available_at_ms=None,
):
    payload = payload_with_components(side=side)
    payload["metadata"]["short"]["strike"] = short_strike
    payload["metadata"]["long"]["strike"] = long_strike
    payload["market"]["reference_price_usd"] = spot
    payload["market"]["valuation_ts_ms"] = asof_ms
    payload["market"]["received_ts_ms"] = asof_ms
    payload["market"]["short_book"]["timestamp"] = short_ts if short_ts is not None else asof_ms
    payload["market"]["long_book"]["timestamp"] = asof_ms
    payload["market"]["short_book"]["index_price"] = spot
    payload["market"]["long_book"]["index_price"] = spot
    if short_asks is not None:
        payload["market"]["short_book"]["asks"] = short_asks
    if long_bids is not None:
        payload["market"]["long_book"]["bids"] = long_bids
    if long_asks is not None:
        payload["market"]["long_book"]["asks"] = long_asks
    if taker_commission is None:
        payload["metadata"]["short"].pop("taker_commission", None)
    else:
        payload["metadata"]["short"]["taker_commission"] = taker_commission
    payload["risk"]["input_ts_ms"] = asof_ms
    payload["risk"]["feature_end_ts_ms"] = asof_ms - 60_000
    payload["risk"]["reference_price_usd"] = spot
    payload["risk"]["horizon_hours"] = (payload["metadata"]["short"]["expiration_timestamp"] - asof_ms) / 3_600_000

    point = point_from_payload(payload, point_id=f"p_{asof_ms}", recorded_at_ms=asof_ms + 50)
    exp_ms = payload["metadata"]["short"]["expiration_timestamp"] if expiry_ms is None else expiry_ms
    comp_ms = asof_ms if computed_at_ms is None else computed_at_ms
    avail_ms = (asof_ms + 50) if available_at_ms is None else available_at_ms
    point["candidate"]["expiry_levels"] = {
        "schema": "lock_levels@0.1",
        "expected_move_usd": expected_move_usd,
        "expiry_ms": exp_ms,
        "pin": pin,
        "pin_status": pin_status,
        "pin_method": pin_method,
        "pin_source": pin_source,
        "computed_at_ms": comp_ms,
        "available_at_ms": avail_ms,
    }
    flow_end = flow_asof_ms if flow_asof_ms is not None else asof_ms - 10_000
    vp = {
        "tmv_state": "SUPPORTED",
        "asof_ms": flow_end,
        "micro_flow": {
            "fast_4h": {
                "source": "binance",
                "symbol": "BTCUSDT",
                "cvd_unit": "BTC",
                "bar_complete": True,
                "coverage_frac": 1.0,
                "window_start_ms": flow_end - 4 * 3_600_000,
                "first_ts_ms": flow_end - 4 * 3_600_000,
                "end_ts_ms": flow_end,
                "available_at_ms": flow_end + 100,
                "direction": flow_direction,
                "cvd_sum": flow_cvd_4h,
                "momentum_return_pct": flow_mom_4h,
                "data_ready": flow_ready,
            },
            "slow_12h": {
                "source": "binance",
                "symbol": "BTCUSDT",
                "cvd_unit": "BTC",
                "bar_complete": True,
                "coverage_frac": 1.0,
                "window_start_ms": flow_end - 12 * 3_600_000,
                "first_ts_ms": flow_end - 12 * 3_600_000,
                "end_ts_ms": flow_end,
                "available_at_ms": flow_end + 100,
                "direction": flow_direction,
                "cvd_sum": flow_cvd_12h,
                "momentum_return_pct": flow_mom_12h,
                "data_ready": flow_ready,
            },
            "combined": {
                "direction": flow_direction,
                "data_ready": flow_ready,
            },
        },
    }
    for snap in (point["candidate"]["snapshot"], point["packet"]["snapshot"]):
        snap["fact_context"]["market_facts"] = {"volume_price": deepcopy(vp)}
    return point


def test_short_buyback_quote_works_with_empty_long_book_and_handles_gaps():
    base = _make_series_point(asof_ms=T)
    empty_long = _make_series_point(
        asof_ms=T + M,
        long_bids=[],
        long_asks=[],
        short_asks=[[0.010, 0.05], [0.012, 0.10]],  # qty=0.1 -> 0.05*0.010 + 0.05*0.012 = 0.0011
        taker_commission=0.0003,
    )
    quote = compare.short_buyback_quote(empty_long)
    assert quote["short_buyback_gaps"] == []
    assert quote["short_buyback_ask_btc"] == pytest.approx(0.0011)
    # fee = min(0.0003, 0.125*0.010)*0.05 + min(0.0003, 0.125*0.012)*0.05 = 0.00003
    assert quote["short_buyback_fee_btc"] == pytest.approx(0.00003)
    assert quote["short_buyback_total_btc"] == pytest.approx(0.00113)
    assert quote["short_quote_asof_ms"] == T + M

    metrics = compare.point_metrics(empty_long, base)
    assert metrics["metrics_version"] == compare.SERIES_METRICS_VERSION == 5
    assert metrics["close_debit_btc"] is None  # two-leg close still requires long_book
    assert metrics["short_buyback_ask_btc"] == pytest.approx(0.0011)
    assert metrics["short_buyback_fee_btc"] == pytest.approx(0.00003)
    assert metrics["short_buyback_gaps"] == []
    assert metrics["expected_move_usd"] == pytest.approx(1800.0)
    assert metrics["flow_cvd_4h"] == pytest.approx(15.0)
    assert metrics["flow_cvd_12h"] == pytest.approx(40.0)
    assert metrics["flow_momentum_4h_pct"] == pytest.approx(0.10)
    assert metrics["flow_momentum_12h_pct"] == pytest.approx(0.25)
    assert metrics["flow_asof_ms"] == T + M - 10_000

    # Fallback expected_move_usd from candidate.expected_move_usd when expiry_levels omits it
    fallback_pt = _make_series_point(asof_ms=T + 2 * M)
    fallback_pt["candidate"]["expiry_levels"] = {"schema": "lock_levels@0.1"}
    fallback_pt["candidate"]["expected_move_usd"] = 1450.5
    assert compare.point_metrics(fallback_pt, base)["expected_move_usd"] == pytest.approx(1450.5)

    # Gap: empty short_book asks -> short_depth_insufficient
    no_short = _make_series_point(asof_ms=T + M, short_asks=[])
    q_no_short = compare.short_buyback_quote(no_short)
    assert "short_depth_insufficient" in q_no_short["short_buyback_gaps"]
    assert q_no_short["short_buyback_ask_btc"] is None
    assert q_no_short["short_buyback_total_btc"] is None

    # Gap: stale short_book -> short_book_stale
    stale_short = _make_series_point(asof_ms=T + M, short_ts=T + M - 10_000)
    q_stale = compare.short_buyback_quote(stale_short)
    assert "short_book_stale" in q_stale["short_buyback_gaps"]
    assert q_stale["short_buyback_ask_btc"] is None

    # Gap: missing taker_commission -> missing_taker_commission (ask still populated on fresh book)
    no_comm = _make_series_point(asof_ms=T + M, taker_commission=None)
    q_no_comm = compare.short_buyback_quote(no_comm)
    assert q_no_comm["short_buyback_gaps"] == ["missing_taker_commission"]
    assert q_no_comm["short_buyback_ask_btc"] == pytest.approx(0.0008)
    assert q_no_comm["short_buyback_fee_btc"] is None
    assert q_no_comm["short_buyback_total_btc"] is None


def test_call_chat_llm_normalizes_endpoint_and_retries_transient_errors():
    calls = []

    def transport(api_key, model, body, timeout, endpoint=None):
        calls.append((endpoint, body))
        if len(calls) == 1:
            raise ConnectionResetError("transient reset")
        return {
            "model": model,
            "choices": [{"finish_reason": "stop", "message": {"content": "回答正文"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    out = review.call_chat_llm(
        [{"role": "user", "content": "hi"}],
        api_key="k",
        model="deepseek-reasoner",
        endpoint="https://example.invalid/v1/",
        retries=1,
        transport=transport,
    )
    assert out["text"] == "回答正文"
    assert out["endpoint"] == "https://example.invalid/v1/chat/completions"
    assert out["usage"]["total_tokens"] == 15
    assert out["finish_reason"] == "stop"
    assert out["http_status"] == 200
    assert out["attempts"] == 2
    assert calls[0][1]["thinking"] == {"type": "enabled"}

    # Non-retryable 400 error raises immediately without retrying
    bad_calls = []

    def bad_transport(*args, **kwargs):
        bad_calls.append(1)
        raise signal_llm_review.LlmApiError(400, "invalid model")

    with pytest.raises(signal_llm_review.LlmApiError):
        review.call_chat_llm(
            [{"role": "user", "content": "hi"}],
            api_key="k",
            model="bad",
            retries=2,
            transport=bad_transport,
        )
    assert len(bad_calls) == 1


def test_decide_initial_hand_parses_accept_and_reject_cleanly():
    excerpts = {
        "put": "Put 路径清晰且距短腿仍有安全余量。",
        "call": "Call 上方压力较近，余量相对更薄。",
        "comparison": "综合比较 Put 余量更足且未见连续破位。",
        "put_counter": "Put 最强反证为近 4h 出现过一次回踩。",
        "call_counter": "Call 最强反证为上方墙位更接近现价。",
        "historical_facts": "历史事实：读取前 24 小时短腿未被严格进入。",
        "accepted_conditions": "承担条件：入场后不出现严格进入短腿责任区且未返回的情况。",
        "confirmation_excerpt": "确认口径：UTC 整分钟采样，每 5 分钟检查一次。",
    }
    public_body = "## Accept / Put\n\n" + "\n".join(excerpts.values())
    ann_payload = {
        "put": excerpts["put"],
        "call": excerpts["call"],
        "comparison": excerpts["comparison"],
        "put_counter": excerpts["put_counter"],
        "call_counter": excerpts["call_counter"],
        "depends_on_ard": False,
        "condition_plan": {
            "invalidation": "entry_not_returned",
            "historical_facts": excerpts["historical_facts"],
            "accepted_conditions": excerpts["accepted_conditions"],
            "confirmation_excerpt": excerpts["confirmation_excerpt"],
        },
    }
    full_reply = public_body + "\n\n<ANNOTATION_JSON>\n" + json.dumps(ann_payload, ensure_ascii=False) + "\n</ANNOTATION_JSON>"

    def transport(api_key, model, body, timeout, endpoint=None):
        return {
            "choices": [{"finish_reason": "stop", "message": {"content": full_reply}}],
            "usage": {"total_tokens": 120},
        }

    res = review.decide_initial_hand(
        "# Frozen Hand\n...",
        contract_pair={"put": {"short_strike_usd": 97000}, "call": {"short_strike_usd": 103000}},
        api_key="k",
        model="deepseek-chat",
        transport=transport,
    )
    assert b"<ANNOTATION_JSON>" not in res["raw_bytes"]
    assert res["decision"]["action"] == "Accept"
    assert res["decision"]["side"] == "put"
    assert res["condition_plan"]["check_interval_ms"] == 300_000
    assert res["condition_plan"]["require_outside_at_t0"] is False

    # Reject returns condition_plan=None
    reject_reply = (
        "## Reject\n\n"
        + "\n".join(excerpts[k] for k in ("put", "call", "comparison", "put_counter", "call_counter"))
        + "\n```json\n"
        + json.dumps({k: excerpts[k] for k in ("put", "call", "comparison", "put_counter", "call_counter")} | {"depends_on_ard": False}, ensure_ascii=False)
        + "\n```"
    )
    res_rej = review.decide_initial_hand(
        "# Frozen Hand",
        contract_pair={},
        api_key="k",
        model="deepseek-chat",
        transport=lambda *a, **k: {"choices": [{"finish_reason": "stop", "message": {"content": reject_reply}}], "usage": {}},
    )
    assert res_rej["decision"]["action"] == "Reject"
    assert res_rej["condition_plan"] is None

    # Accept / Call with depends_on_ard=True requires verbatim ARD condition fields
    ard_meaning = "ARD 意义：Pin 位于 100,500 靠近 Call 短腿上方，作为同口径复核参照。"
    ard_review = "ARD 复核：当同口径 Pin 迁移或价格进入邻域时触发持仓复核。"
    ard_body = "## Accept / Call\n\n" + "\n".join([*excerpts.values(), ard_meaning, ard_review])
    ard_payload = {
        **ann_payload,
        "depends_on_ard": True,
        "condition_plan": {
            **ann_payload["condition_plan"],
            "ard_responsibility_meaning": ard_meaning,
            "ard_review_on": ard_review,
        },
    }
    res_ard = review.decide_initial_hand(
        "# Frozen Hand",
        contract_pair={"put": {"short_strike_usd": 97000}, "call": {"short_strike_usd": 103000}},
        api_key="k",
        model="deepseek-chat",
        transport=lambda *a, **k: {
            "choices": [{"finish_reason": "stop", "message": {"content": ard_body + f"\n<ANNOTATION_JSON>{json.dumps(ard_payload, ensure_ascii=False)}</ANNOTATION_JSON>"}}],
            "usage": {},
        },
    )
    assert res_ard["decision"]["side"] == "call" and res_ard["decision"]["depends_on_ard"] is True
    assert res_ard["condition_plan"]["ard_responsibility_meaning"] == ard_meaning
    assert res_ard["condition_plan"]["ard_review_on"] == ard_review


def test_detect_hold_triggers_all_four_categories_positive_and_anti_noise():
    frozen = {
        "invalidation": "entry_not_returned",
        "ard_basis": {
            "facts": {
                "last": {"pin": 100_000.0},
                "band": {"half_width_usd": 500.0},
            }
        },
    }
    t0 = {
        "asof_ms": T,
        "spot_usd": 100_000.0,
        "short_distance_pct": 3.0,
        "expected_move_usd": 2000.0,
        "flow_ready": True,
        "flow_direction": "neutral",
        "flow_momentum_4h_pct": 0.15,
        "flow_asof_ms": T,
        "flow_window_basis": {"source": "binance", "symbol": "BTCUSDT", "start_ms": T - 4 * 3_600_000},
    }

    # --- Category A: CONDITION_TRIGGERED event fires even when latest price bounced outside ---
    rows_ok = [
        {"asof_ms": T + M, "spot_usd": 99_500.0, "short_distance_pct": 2.5},
        {"asof_ms": T + 2 * M, "spot_usd": 99_600.0, "short_distance_pct": 2.6},
    ]
    trig_a1 = review.detect_hold_triggers(
        frozen_condition=frozen,
        side="put",
        short_strike_usd=97_000.0,
        entry_at_ms=T,
        t0_metrics=t0,
        series_rows=rows_ok,
        condition_events=[{"event_id": "ev1", "kind": "CONDITION_TRIGGERED", "observed_at_ms": T + 2 * M, "body": {}}],
        now_ms=T + 3 * M,
    )
    assert [t["category"] for t in trig_a1] == ["A"]
    assert trig_a1[0]["code"] == "CONDITION_TRIGGERED" and trig_a1[0]["severity"] == 3

    # --- Category A: continuous post-entry strict interior entry vs >10m stale gap ---
    rows_breach = [
        {"asof_ms": T + M, "spot_usd": 97_200.0, "short_distance_pct": 0.2},
        {"asof_ms": T + 2 * M, "spot_usd": 96_800.0, "short_distance_pct": -0.206},
    ]
    trig_a2 = review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0, entry_at_ms=T,
        t0_metrics=t0, series_rows=rows_breach, now_ms=T + 3 * M,
    )
    assert any(t["category"] == "A" and t["code"] == "BOUNDARY_BREACH" for t in trig_a2)
    # Stale sample (>=10m after latest) must NOT fire A2
    assert review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0, entry_at_ms=T,
        t0_metrics=t0, series_rows=rows_breach, now_ms=T + 12 * M,
    ) == []

    # --- Category B: Margin deterioration positive + anti-noise (round number / single row / 10m gap) ---
    rows_deteriorated = [
        {"asof_ms": T + M, "spot_usd": 98_400.0, "short_distance_pct": 1.44, "expected_move_usd": 2000.0},
        {"asof_ms": T + 2 * M, "spot_usd": 98_200.0, "short_distance_pct": 1.24, "expected_move_usd": 2000.0},
    ]
    trig_b = review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0, entry_at_ms=T,
        t0_metrics=t0, series_rows=rows_deteriorated, now_ms=T + 3 * M,
    )
    assert [t["category"] for t in trig_b] == ["B"]

    # Expected move ratio drop >= 50% even with modest distance change
    rows_em_drop = [
        {"asof_ms": T + M, "spot_usd": 99_500.0, "short_distance_pct": 2.57, "expected_move_usd": 3600.0},
        {"asof_ms": T + 2 * M, "spot_usd": 99_400.0, "short_distance_pct": 2.47, "expected_move_usd": 3600.0},
    ]
    assert [t["category"] for t in review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0, entry_at_ms=T,
        t0_metrics=t0, series_rows=rows_em_drop, now_ms=T + 3 * M,
    )] == ["B"]

    # Anti-noise: single row, >=10m gap, or round-number proximity (99,000 / 100,000) without material drop
    assert review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0, entry_at_ms=T,
        t0_metrics=t0, series_rows=[rows_deteriorated[-1]], now_ms=T + 3 * M,
    ) == []
    assert review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0, entry_at_ms=T,
        t0_metrics=t0, series_rows=[
            {"asof_ms": T + M, "spot_usd": 99_000.0, "short_distance_pct": 2.06},
            {"asof_ms": T + 12 * M, "spot_usd": 98_200.0, "short_distance_pct": 1.24},
        ], now_ms=T + 13 * M,
    ) == []
    assert review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0, entry_at_ms=T,
        t0_metrics=t0, series_rows=[
            {"asof_ms": T + M, "spot_usd": 99_800.0, "short_distance_pct": 2.88},
            {"asof_ms": T + 2 * M, "spot_usd": 99_600.0, "short_distance_pct": 2.68},
        ], now_ms=T + 3 * M,
    ) == []

    # --- Category C: ARD structure shift positive + strict guards ---
    ard_bad_measure = {
        "same_measure": False, "pin_status": "ok", "pin_price_changed": True,
        "old_pin": 100_000.0, "new_pin": 98_000.0, "new_neighborhood_events": [{"kind": "leave", "at_ms": T + 2 * M}],
    }
    assert review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0, entry_at_ms=T,
        t0_metrics=t0, series_rows=rows_ok, ard_comparison=ard_bad_measure, now_ms=T + 3 * M,
    ) == []

    ard_bad_status = {"same_measure": True, "pin_status": "unavailable", "pin_price_changed": True, "old_pin": 100_000.0, "new_pin": 98_000.0}
    assert review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0, entry_at_ms=T,
        t0_metrics=t0, series_rows=rows_ok, ard_comparison=ard_bad_status, now_ms=T + 3 * M,
    ) == []

    ard_cosmetic = {"same_measure": True, "pin_status": "ok", "pin_price_changed": False, "new_neighborhood_events": [{"kind": "steady", "at_ms": T + 2 * M}]}
    assert review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0, entry_at_ms=T,
        t0_metrics=t0, series_rows=rows_ok, ard_comparison=ard_cosmetic, now_ms=T + 3 * M,
    ) == []

    ard_material = {"same_measure": True, "pin_status": "ok", "pin_price_changed": True, "old_pin": 100_000.0, "new_pin": 99_400.0}
    trig_c1 = review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0, entry_at_ms=T,
        t0_metrics=t0, series_rows=rows_ok, ard_comparison=ard_material, now_ms=T + 3 * M,
    )
    assert [t["category"] for t in trig_c1] == ["C"]

    ard_event = {"same_measure": True, "pin_status": "ok", "pin_price_changed": False, "new_neighborhood_events": [{"kind": "inside_band", "at_ms": T + 2 * M}]}
    trig_c2 = review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0, entry_at_ms=T,
        t0_metrics=t0, series_rows=rows_ok, ard_comparison=ard_event, now_ms=T + 3 * M,
    )
    assert [t["category"] for t in trig_c2] == ["C"]

    # --- Category D: Flow reversal positive + anti-noise (flat/favorable price with CVD flip) ---
    flow_flip_flat = [
        {"asof_ms": T + M, "spot_usd": 100_000.0, "short_distance_pct": 3.0, "flow_ready": True, "flow_direction": "bearish", "flow_cvd_4h": -50.0, "flow_momentum_4h_pct": -0.25, "flow_asof_ms": T + M},
        {"asof_ms": T + 2 * M, "spot_usd": 100_100.0, "short_distance_pct": 3.1, "flow_ready": True, "flow_direction": "bearish", "flow_cvd_4h": -60.0, "flow_momentum_4h_pct": -0.30, "flow_asof_ms": T + 2 * M},
    ]
    assert review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0, entry_at_ms=T,
        t0_metrics=t0, series_rows=flow_flip_flat, now_ms=T + 3 * M,
    ) == []

    flow_reversal_coincident = [
        {"asof_ms": T + M, "spot_usd": 99_900.0, "short_distance_pct": 2.98, "flow_ready": True, "flow_direction": "neutral", "flow_cvd_4h": -10.0, "flow_momentum_4h_pct": -0.05, "flow_asof_ms": T + M, "flow_window_basis": {"source": "binance", "symbol": "BTCUSDT", "start_ms": T + M - 4 * 3_600_000}},
        {"asof_ms": T + 2 * M, "spot_usd": 99_600.0, "short_distance_pct": 2.68, "flow_ready": True, "flow_direction": "bearish", "flow_cvd_4h": -120.0, "flow_momentum_4h_pct": -0.45, "flow_asof_ms": T + 2 * M, "flow_window_basis": {"source": "binance", "symbol": "BTCUSDT", "start_ms": T + 2 * M - 4 * 3_600_000}},
    ]
    trig_d = review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0, entry_at_ms=T,
        t0_metrics=t0, series_rows=flow_reversal_coincident, now_ms=T + 3 * M,
    )
    assert [t["category"] for t in trig_d] == ["D"]

    # Missing flow_window_basis blocks D cleanly
    flow_no_basis = [{k: v for k, v in r.items() if k != "flow_window_basis"} for r in flow_reversal_coincident]
    assert review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0, entry_at_ms=T,
        t0_metrics=t0, series_rows=flow_no_basis, now_ms=T + 3 * M,
    ) == []

    # flow_ready=False blocks D
    flow_unready = deepcopy(flow_reversal_coincident)
    flow_unready[-1]["flow_ready"] = False
    assert review.detect_hold_triggers(
        frozen_condition=frozen, side="put", short_strike_usd=97_000.0, entry_at_ms=T,
        t0_metrics=t0, series_rows=flow_unready, now_ms=T + 3 * M,
    ) == []

    # Call side mirror: bullish flow + upward price toward 103,000 triggers D; bearish flow does not
    call_t0 = {**t0, "short_distance_pct": 2.91, "flow_direction": "neutral", "flow_momentum_4h_pct": -0.10}
    call_bull_rows = [
        {"asof_ms": T + M, "spot_usd": 100_100.0, "short_distance_pct": 2.81, "flow_ready": True, "flow_direction": "neutral", "flow_momentum_4h_pct": 0.05, "flow_asof_ms": T + M, "flow_window_basis": {"source": "binance", "symbol": "BTCUSDT", "start_ms": T + M - 4 * 3_600_000}},
        {"asof_ms": T + 2 * M, "spot_usd": 100_500.0, "short_distance_pct": 2.43, "flow_ready": True, "flow_direction": "bullish", "flow_momentum_4h_pct": 0.40, "flow_asof_ms": T + 2 * M, "flow_window_basis": {"source": "binance", "symbol": "BTCUSDT", "start_ms": T + 2 * M - 4 * 3_600_000}},
    ]
    call_trig = review.detect_hold_triggers(
        frozen_condition=frozen, side="call", short_strike_usd=103_000.0, entry_at_ms=T,
        t0_metrics=call_t0, series_rows=call_bull_rows, now_ms=T + 3 * M,
    )
    assert [t["category"] for t in call_trig] == ["D"]
    call_bear_rows = [{**r, "flow_direction": "bearish", "flow_momentum_4h_pct": -0.40} for r in call_bull_rows]
    assert review.detect_hold_triggers(
        frozen_condition=frozen, side="call", short_strike_usd=103_000.0, entry_at_ms=T,
        t0_metrics=call_t0, series_rows=call_bear_rows, now_ms=T + 3 * M,
    ) == []


def test_debounce_scheduler_coalescing_inflight_cooldown_priority_a_and_revoke():
    trig_b = {"category": "B", "code": "MARGIN_DETERIORATION", "signature": "MARGIN_DETERIORATION:step_5", "depth_step": 5, "severity": 2, "observed_at_ms": T}
    trig_d = {"category": "D", "code": "FLOW_REVERSAL", "signature": "FLOW_REVERSAL:bearish", "severity": 2, "observed_at_ms": T + 4 * M}

    # Coalesces B and D within 12-min debounce window
    st, act = review.schedule_review_action({}, [trig_b], now_ms=T)
    assert act is None and len(st["pending"]) == 1
    st, act = review.schedule_review_action(st, [trig_d], now_ms=T + 4 * M)
    assert act is None and len(st["pending"]) == 2
    st, act = review.schedule_review_action(st, [], now_ms=T + 11 * M)
    assert act is None  # still inside 12m debounce
    st, act = review.schedule_review_action(st, [], now_ms=T + 12 * M)
    assert act is not None and act["should_call"] is True and len(act["triggers"]) == 2
    assert st["inflight"] is True and st["pending"] == []

    # While inflight=True, new triggers append to pending instead of dropping or double-calling
    trig_c = {"category": "C", "code": "ARD_STRUCTURE_SHIFT", "signature": "ARD_PIN:100000->99000", "severity": 2, "observed_at_ms": T + 13 * M}
    st, act = review.schedule_review_action(st, [trig_c], now_ms=T + 13 * M)
    assert act is None and len(st["pending"]) == 1

    # Duplicate of already-dispatched B (same step) is deduplicated; escalated depth_step=7 is accepted
    st, act = review.schedule_review_action(st, [trig_b], now_ms=T + 13 * M)
    assert len(st["pending"]) == 1
    trig_b_esc = {**trig_b, "signature": "MARGIN_DETERIORATION:step_7", "depth_step": 7}
    st, act = review.schedule_review_action(st, [trig_b_esc], now_ms=T + 13 * M)
    assert len(st["pending"]) == 2

    # Failure sets retry backoff (5m)
    st_fail = review.complete_review_action(st, now_ms=T + 14 * M, success=False)
    assert st_fail["inflight"] is False
    st_fail, act = review.schedule_review_action(st_fail, [], now_ms=T + 18 * M)
    assert act is None  # blocked by 5m backoff

    # Success sets cooldown (30m), blocking normal triggers during cooldown
    st_ok = review.complete_review_action({**st, "pending": []}, now_ms=T + 14 * M, success=True, verdict="HOLD")
    st_ok, act = review.schedule_review_action(st_ok, [trig_c], now_ms=T + 15 * M)
    assert act is None
    st_ok, act = review.schedule_review_action(st_ok, [], now_ms=T + 30 * M)
    assert act is None  # still inside 30m cooldown (until T+44m)

    # Priority-A CONDITION_TRIGGERED bypasses cooldown when last_review_was_before_trigger
    trig_a = {"category": "A", "code": "CONDITION_TRIGGERED", "signature": f"CONDITION_TRIGGERED:{T + 31 * M}", "severity": 3, "observed_at_ms": T + 31 * M}
    st_ok, act = review.schedule_review_action(st_ok, [trig_a], now_ms=T + 31 * M)
    assert act is not None and act["reason"] == "condition_triggered_priority_a"

    # Once revoked=True, no further AI hold calls ever trigger
    st_revoked = review.complete_review_action(st_ok, now_ms=T + 32 * M, success=True, verdict="REVOKE")
    assert st_revoked["revoked"] is True
    _, act = review.schedule_review_action(st_revoked, [trig_a], now_ms=T + 100 * M)
    assert act is None


def test_same_boundary_check_does_not_repeat_but_new_entry_is_eligible():
    def boundary(entered, checked):
        return review._detect_category_a(
            entry_at_ms=T, short_strike_usd=103_000, t0={}, all_rows=[], fresh_run=[],
            condition_events=[{'kind': 'CONDITION_TRIGGERED', 'observed_at_ms': checked,
                               'body': {'result': {'latest_new_entry_ms': entered}}}],
        )

    first = boundary(T + M, T + 5 * M)
    state, action = review.schedule_review_action({}, [first], now_ms=T + 5 * M)
    assert action['should_call']
    state = review.complete_review_action(state, now_ms=T + 6 * M, success=True, verdict='HOLD')
    state, action = review.schedule_review_action(state, [boundary(T + M, T + 10 * M)], now_ms=T + 10 * M)
    assert action is None
    _, action = review.schedule_review_action(state, [boundary(T + 12 * M, T + 15 * M)], now_ms=T + 15 * M)
    assert action['should_call']


def test_build_hold_review_input_and_parse_response_protocol():
    frozen = {
        "invalidation": "entry_not_returned",
        "invalidation_meaning": "入场后严格进入责任区且至检查点未严格返回。",
        "accepted_conditions": ["短腿外锁定且未触发严格进入不返回规则。"],
        "confirmation_excerpt": "UTC 每分钟采样确认。",
        "entry_index_usd": 100_000.0,
    }
    md = review.build_hold_review_input(
        contract={"side": "put", "short_strike_usd": 97_000, "long_strike_usd": 95_000, "expiry_ms": T + 18 * 3_600_000},
        entry_at_ms=T,
        initial_decision={"judgment": {"action": "Accept", "side": "put"}},
        frozen_condition=frozen,
        triggers=[{"category": "B", "code": "MARGIN_DETERIORATION", "summary_cn": "短腿安全余量缩半。", "observed_at_ms": T + 5 * M}],
        t0_summary={"short_distance_pct": 3.0},
        post_entry_summary={"short_distance_pct": 1.4},
        condition_summary="未触发",
        ard_summary="同口径未迁移",
        flow_summary="中性转偏弱",
        quote_summary={"short_buyback_ask_btc": 0.0011},
        prior_reviews=[{"verdict": "HOLD", "public_reply": "此前余量仍充足。"}],
    )
    for heading in ("## 原始承诺", "## 新增事实", "## 条件对照", "## 更新判断要求"):
        assert heading in md
    assert "HOLD" in md and "REVOKE" in md and "UNKNOWN" in md
    assert "CONDITION_TRIGGERED" in md

    # Valid HOLD / REVOKE / UNKNOWN parsing
    for verdict in ("HOLD", "REVOKE", "UNKNOWN"):
        reply = (
            f"## {verdict}\n\n"
            "原短腿余量已从 3.0% 缩至 1.4%，对应原承担条件说明。\n\n"
            "<HOLD_REVIEW_JSON>\n"
            + json.dumps({
                "verdict": verdict,
                "affected_conditions": ["短腿外锁定且未触发严格进入不返回规则。"],
                "public_reply": f"{verdict}：已对照原条件给出结论。",
                "key_facts": ["short_distance_pct=1.4%"],
            }, ensure_ascii=False)
            + "\n</HOLD_REVIEW_JSON>"
        )
        parsed = review.parse_hold_review_response(reply, frozen_condition=frozen, program_triggered=False)
        assert parsed["verdict"] == verdict

    # Reject Put/Call re-selection
    reselect_reply = (
        "## REVOKE\n\n建议改选 Call 侧，Accept / Call。\n\n"
        "<HOLD_REVIEW_JSON>{\"verdict\": \"REVOKE\", \"affected_conditions\": [\"c1\"], "
        "\"public_reply\": \"建议改选 Call\", \"key_facts\": []}</HOLD_REVIEW_JSON>"
    )
    with pytest.raises(ValueError, match="side_reselection_forbidden"):
        review.parse_hold_review_response(reselect_reply, frozen_condition=frozen)

    # program_triggered=True cannot be marked untriggered, and attaches flag/notes
    overturn_reply = (
        "## HOLD\n\n程序边界条件未触发，继续持有。\n\n"
        "<HOLD_REVIEW_JSON>{\"verdict\": \"HOLD\", \"program_triggered\": false, \"affected_conditions\": [\"c1\"], "
        "\"public_reply\": \"条件未触发\", \"key_facts\": []}</HOLD_REVIEW_JSON>"
    )
    with pytest.raises(ValueError, match="cannot_overturn_program_triggered"):
        review.parse_hold_review_response(overturn_reply, frozen_condition=frozen, program_triggered=True)

    flagged = review.parse_hold_review_response(
        "## REVOKE\n\n确认边界失效规则已触发，撤销持有。\n\n"
        "<HOLD_REVIEW_JSON>{\"verdict\": \"REVOKE\", \"affected_conditions\": [\"entry_not_returned\"], "
        "\"public_reply\": \"边界失效规则已触发，撤销持有。\", \"key_facts\": [\"CONDITION_TRIGGERED\"]}</HOLD_REVIEW_JSON>",
        frozen_condition=frozen,
        program_triggered=True,
    )
    assert flagged["program_triggered"] is True
    assert flagged["notes"]


def test_closure_contract_conflict_discretionary_and_excerpt_whitespace_tolerance():
    # (d) Whitespace & full/half-width punctuation normalization in initial parse & condition_plan
    excerpts = {
        "put": "Put 路径：97,000（下方）尚未触及，短腿距离 3,000 USD。",
        "call": "Call 路径：103,000（上方）尚未触及，短腿距离 3,000 USD。",
        "comparison": "两侧相比，Call 距离更宽松且无紧贴反证，选 Call。",
        "put_counter": "Put 反证：近 4h 下方跌破过前支撑。",
        "call_counter": "Call 反证：上方 102,500 存在卖压待观察。",
        "hist": "历史事实：前窗未进入责任区。",
        "cond": "持续条件：维持 103,000 下方运行，每 5 分钟检查。",
        "conf": "确认口径：采用 entry_not_returned（300000 ms 固定 UTC 网格）。",
    }
    raw_body = (
        "## Accept / Call\n\n"
        + "Put 路径：97,000\n  (下方)  尚未触及,  短腿距离 3,000 USD.\n"
        + "Call 路径：103,000\n  (上方) 尚未触及，短腿距离 3,000 USD。\n"
        + excerpts["comparison"] + "\n"
        + excerpts["put_counter"] + "\n"
        + "Call\t反证: 上方 102,500 存在卖压待观察。\n"
        + "历史事实:  前窗\n未进入责任区。\n"
        + "持续条件：维持\n103,000 下方运行，每 5 分钟检查。\n"
        + "确认口径: 采用 entry_not_returned (300000 ms\n固定 UTC 网格)。\n"
    )
    ann_payload = {
        "put": excerpts["put"],
        "call": excerpts["call"],
        "comparison": excerpts["comparison"],
        "put_counter": excerpts["put_counter"],
        "call_counter": excerpts["call_counter"],
        "depends_on_ard": False,
        "condition_plan": {
            "invalidation": "entry_not_returned",
            "check_interval_ms": 300_000,
            "historical_facts": excerpts["hist"],
            "accepted_conditions": excerpts["cond"],
            "confirmation_excerpt": excerpts["conf"],
        },
    }
    res_init = review.decide_initial_hand(
        "# input",
        contract_pair={},
        api_key="k",
        model="m",
        transport=lambda *a, **k: {
            "choices": [{"finish_reason": "stop", "message": {"content": raw_body + f"<ANNOTATION_JSON>{json.dumps(ann_payload, ensure_ascii=False)}</ANNOTATION_JSON>"}}]
        },
    )
    assert res_init["decision"]["action"] == "Accept" and res_init["decision"]["side"] == "call"

    # (a) entry_decision_basis@1 inheritance & zero-English-key pure Chinese hold-review card
    frozen = {
        "contract": {"side": "call", "short_strike_usd": 103_000, "long_strike_usd": 105_000, "expiry_ms": T + 16 * 3_600_000},
        "invalidation": "entry_not_returned",
        "invalidation_meaning": "入场后严格进入责任区且至检查点未严格返回。",
        "check_clock": {"first_at_ms": T + 5 * M, "interval_ms": 5 * M},
        "historical_facts": excerpts["hist"],
        "accepted_conditions": excerpts["cond"],
        "confirmation_excerpt": excerpts["conf"],
        "entry_index_usd": 100_000.0,
    }
    basis = review.build_entry_decision_basis(
        hand_id="hand_1", episode_id="ep_1", initial_decision=res_init["decision"], frozen_condition=frozen,
    )
    assert basis["schema"] == "entry_decision_basis@1"
    assert basis["M1"]["invalidation"] == "entry_not_returned"
    assert basis["C"]["C1"] == excerpts["cond"]
    assert basis["S1"]["original_choice_reason"] == excerpts["comparison"]
    assert basis["S1"]["original_strongest_counterevidence"] == excerpts["call_counter"]
    assert basis["valid_condition_ids"] == ["M1", "C1", "H1", "S1"]

    md_cn = review.build_hold_review_input(
        contract=frozen["contract"],
        entry_at_ms=T,
        initial_decision=res_init["decision"],
        frozen_condition=frozen,
        triggers=[{"category": "B", "code": "MARGIN_DETERIORATION", "summary_cn": "短腿缓冲已缩减至入场 45%。", "observed_at_ms": T + 15 * M}],
        t0_summary={"locked_at": T, "entry_index_usd": 100_000.0, "short_distance_pct": 3.0, "expected_move_usd": 1800.0, "net_credit_after_fee_btc": 0.0042},
        post_entry_summary={"sample_count": 15, "latest_spot_usd": 101_600.0, "min_short_distance_pct": 1.35, "latest_short_distance_pct": 1.38},
        condition_summary={"program_triggered": False, "mechanical_condition_state": "INTACT", "checks_count": 3, "latest_check_at_ms": T + 15 * M},
        ard_summary={"same_measure": True, "pin_price_changed": False, "frozen_anchor": {"pin": 100_000}, "current_anchor": {"pin": 100_000}, "current_distance_to_frozen_pin_usd": 1600.0, "current_inside_frozen_neighborhood": False, "new_neighborhood_events": []},
        flow_summary={"flow_ready": True, "flow_asof_ms": T + 15 * M, "flow_direction": "neutral_to_bullish", "flow_fast_4h": "neutral_to_bullish", "flow_slow_12h": "neutral", "flow_cvd_4h": 42.5, "flow_momentum_4h_pct": 0.31},
        quote_summary={"short_buyback_ask_btc": 0.0055, "short_buyback_fee_btc": 0.0003, "short_buyback_total_btc": 0.0058, "short_quote_asof_ms": T + 15 * M, "short_buyback_gaps": []},
        entry_basis=basis,
    )
    assert "## 影子管理经济观察（仅作参考，不替代原条件承保结构判断）" in md_cn
    assert "[S1] 入场时已承认并承担的最强反证" in md_cn
    assert "{'same_measure'" not in md_cn and "latest_spot_usd:" not in md_cn

    # (b) program_triggered=True yet AI insists HOLD -> CONFLICT_WITH_FROZEN_INVALIDATION without altering text
    conflict_reply = (
        "## HOLD\n\n程序规则虽已达触发条件，但我主观建议暂时观察。\n"
        "<HOLD_REVIEW_JSON>{\"verdict\": \"HOLD\", \"affected_conditions\": [\"M1\", \"C1\"], "
        "\"public_reply\": \"程序虽已记触发，模型主观主张观察。\", \"key_facts\": [\"M1\"]}</HOLD_REVIEW_JSON>"
    )
    parsed_conflict = review.parse_hold_review_response(
        conflict_reply, frozen_condition=frozen, program_triggered=True, valid_condition_ids=basis["valid_condition_ids"],
    )
    assert parsed_conflict["mechanical_condition_state"] == "TRIGGERED"
    assert parsed_conflict["ai_management_verdict"] == "HOLD"
    assert parsed_conflict["protocol_status"] == "CONFLICT_WITH_FROZEN_INVALIDATION"
    assert parsed_conflict["condition_id_mapping_status"] == "MAPPED"
    assert "我主观建议暂时观察" in parsed_conflict["public_text"]

    # (c) program_triggered=False + AI outputs REVOKE -> DISCRETIONARY_REVOKE + INTACT
    disc_reply = (
        "## REVOKE\n\n入场后新增流向与余量双重恶化，建议主动撤销承保。\n"
        "<HOLD_REVIEW_JSON>{\"verdict\": \"REVOKE\", \"affected_conditions\": [\"C1\", \"S1\"], "
        "\"public_reply\": \"余量与流向已不足以支持继续持有，主动建议 REVOKE。\", \"key_facts\": [\"B\"]}</HOLD_REVIEW_JSON>"
    )
    parsed_disc = review.parse_hold_review_response(
        disc_reply, frozen_condition=frozen, program_triggered=False, valid_condition_ids=basis["valid_condition_ids"],
    )
    assert parsed_disc["mechanical_condition_state"] == "INTACT"
    assert parsed_disc["ai_management_verdict"] == "REVOKE"
    assert parsed_disc["protocol_status"] == "DISCRETIONARY_REVOKE"
    assert parsed_disc["condition_id_mapping_status"] == "MAPPED"


def test_decide_initial_hand_tolerates_markdown_markers_in_verbatim_excerpts():
    raw_body = (
        "## Accept / Call\n\n"
        "- **Put路径**：现价 99,000 在 Put 责任区外，空间缓冲充足。\n"
        "- **Call路径**：现价距 Call 短腿 `103,000` 尚有 4,000 USD。\n"
        "对比结论：Call 路径阻力更小，因此选择 Call。\n"
        "1. Put 最强反证：下方 96,000 成交偏薄。\n"
        "2. Call 最强反证：上方 `102,500` 存在卖压待观察。\n"
        "- 冻结历史事实：前窗未进入责任区。\n"
        "- 承担条件：维持 `103,000` 下方运行。\n"
        "- 破坏口径：采用 `entry_not_returned`，每 5 分钟检查。\n"
    )
    ann_payload = {
        "put": "Put路径：现价 99,000 在 Put 责任区外，空间缓冲充足。",
        "call": "Call路径：现价距 Call 短腿 103,000 尚有 4,000 USD。",
        "comparison": "Call 路径阻力更小，因此选择 Call。",
        "put_counter": "Put 最强反证：下方 96,000 成交偏薄。",
        "call_counter": "Call 最强反证：上方 102,500 存在卖压待观察。",
        "depends_on_ard": False,
        "condition_plan": {
            "invalidation": "entry_not_returned",
            "check_interval_ms": 300_000,
            "historical_facts": "冻结历史事实：前窗未进入责任区。",
            "accepted_conditions": "承担条件：维持 103,000 下方运行。",
            "confirmation_excerpt": "破坏口径：采用 entry_not_returned，每 5 分钟检查。",
        },
    }
    captured = {}

    def fake_transport(api_key, model, payload, timeout_s, **_):
        captured["system"] = payload["messages"][0]["content"]
        return {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": raw_body + f"<ANNOTATION_JSON>{json.dumps(ann_payload, ensure_ascii=False)}</ANNOTATION_JSON>"},
            }]
        }

    res = review.decide_initial_hand("# input", contract_pair={}, api_key="k", model="m", transport=fake_transport)
    assert res["decision"]["action"] == "Accept"
    assert res["condition_plan"]["invalidation"] == "entry_not_returned"
    assert "原封不动逐字复制" in captured["system"]
    assert "不要删改" in captured["system"]

    bad_payload = deepcopy(ann_payload)
    bad_payload["put"] = "Put路径摘要：完全改写了原句内容。"
    with pytest.raises(ValueError, match="verbatim excerpts"):
        review.decide_initial_hand(
            "# input",
            contract_pair={},
            api_key="k",
            model="m",
            transport=lambda *a, **k: {
                "choices": [{"finish_reason": "stop", "message": {"content": raw_body + f"<ANNOTATION_JSON>{json.dumps(bad_payload, ensure_ascii=False)}</ANNOTATION_JSON>"}}]
            },
        )


def test_scheduler_stale_inflight_recovery_and_fresh_guard():
    trig_b = {"category": "B", "code": "MARGIN_DETERIORATION", "signature": "MARGIN_DETERIORATION:step_4", "depth_step": 4, "severity": 2, "observed_at_ms": T}
    trig_d = {"category": "D", "code": "FLOW_REVERSAL", "signature": "FLOW_REVERSAL:bearish", "severity": 2, "observed_at_ms": T + M}
    trig_c = {"category": "C", "code": "ARD_STRUCTURE_SHIFT", "signature": "ARD_PIN:100000->99000", "severity": 2, "observed_at_ms": T + 13 * M}

    st, _ = review.schedule_review_action({}, [trig_b, trig_d], now_ms=T)
    st, act = review.schedule_review_action(st, [], now_ms=T + 12 * M)
    assert act is not None and st["inflight"] is True
    assert st["inflight_started_at_ms"] == T + 12 * M

    # Fresh inflight (< 25m): buffers trig_c, clears stale recovery marker, returns None
    st_fresh, act_fresh = review.schedule_review_action({**st, "stale_inflight_recovered_at_ms": 1}, [trig_c], now_ms=T + 36 * M)
    assert act_fresh is None
    assert st_fresh["inflight"] is True
    assert st_fresh["inflight_started_at_ms"] == T + 12 * M
    assert [t["category"] for t in st_fresh["pending"]] == ["C"]
    assert "stale_inflight_recovered_at_ms" not in st_fresh

    # Stale inflight (>= 25m) blocked by cooldown: recovers inflight_batch to front in order, pops dispatched keys, sets pending_since_ms
    cooldown_st = {**st_fresh, "pending": [trig_b, trig_c], "pending_since_ms": None, "cooldown_until_ms": T + 50 * M}
    st_rec, act_rec = review.schedule_review_action(cooldown_st, [], now_ms=T + 37 * M)
    assert act_rec is None
    assert st_rec["inflight"] is False
    assert st_rec["inflight_started_at_ms"] is None
    assert st_rec["inflight_batch"] == []
    assert [t["category"] for t in st_rec["pending"]] == ["B", "D", "C"]
    assert "B:MARGIN_DETERIORATION:step_4" not in st_rec["dispatched_signatures"]
    assert "D:FLOW_REVERSAL:bearish" not in st_rec["dispatched_signatures"]
    assert st_rec["pending_since_ms"] == T + 37 * M - review.MAX_DEBOUNCE_MS
    assert st_rec["stale_inflight_recovered_at_ms"] == T + 37 * M

    # Stale legacy inflight without inflight_started_at_ms and no cooldown: self-heals & re-dispatches immediately
    legacy_stuck = {
        "revoked": False,
        "inflight": True,
        "inflight_batch": [trig_b, trig_d],
        "pending": [trig_c],
        "dispatched_signatures": {"B:MARGIN_DETERIORATION:step_4": {"severity": 2, "at_ms": T}},
    }
    st_healed, act_healed = review.schedule_review_action(legacy_stuck, [], now_ms=T + 40 * M)
    assert act_healed is not None and act_healed["should_call"] is True
    assert [t["category"] for t in act_healed["triggers"]] == ["B", "D", "C"]
    assert st_healed["inflight"] is True and st_healed["inflight_started_at_ms"] == T + 40 * M
    assert st_healed["stale_inflight_recovered_at_ms"] == T + 40 * M

    # complete_review_action clears inflight_started_at_ms on both success and failure
    st_done = review.complete_review_action(st_healed, now_ms=T + 41 * M, success=True, verdict="HOLD")
    assert st_done["inflight"] is False and st_done["inflight_started_at_ms"] is None
    st_fail = review.complete_review_action(st_healed, now_ms=T + 41 * M, success=False)
    assert st_fail["inflight"] is False and st_fail["inflight_started_at_ms"] is None


def test_call_chat_llm_budget_reserve_complete_and_non_retryable_exhaustion(tmp_path):
    fixed_now = dt.datetime(2026, 11, 16, 12, 0, 0, tzinfo=dt.timezone.utc)
    budget = signal_llm_review.DailyHttpBudget(tmp_path / "budget.json", limit=3, now_fn=lambda: fixed_now)

    assert review._is_transient_transport_error(signal_llm_review.DailyBudgetExceeded("exhausted")) is False

    def ok_transport(api_key, model, body, timeout, endpoint=None):
        return {
            "model": model,
            "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }

    res = review.call_chat_llm(
        [{"role": "user", "content": "hi"}],
        api_key="k",
        model="deepseek-chat",
        transport=ok_transport,
        budget=budget,
        budget_role="astra_player_hold",
        packet_hash="pkt_ok",
    )
    assert res["text"] == "ok"
    snap1 = budget.snapshot()
    assert snap1["http_calls_used"] == 1
    assert snap1["events"][-1]["role"] == "astra_player_hold"
    assert snap1["events"][-1]["card_hash"] == "pkt_ok"
    assert snap1["events"][-1]["provider"] == "deepseek"
    assert snap1["events"][-1]["status_category"] == "HTTP_OK"
    assert snap1["events"][-1]["usage"] == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}

    # Attempt 1 times out (URLError wrapping TimeoutError), attempt 2 fails with 503 LlmApiError -> TIMEOUT then HTTP_ERROR
    seq = [0]

    def failing_transport(*args, **kwargs):
        seq[0] += 1
        if seq[0] == 1:
            raise urllib.error.URLError(TimeoutError("read timed out"))
        raise signal_llm_review.LlmApiError(503, "upstream busy")

    with pytest.raises(signal_llm_review.LlmApiError):
        review.call_chat_llm(
            [{"role": "user", "content": "hi"}],
            api_key="k",
            model="deepseek-chat",
            retries=1,
            transport=failing_transport,
            budget=budget,
            budget_role="astra_player_hold",
            packet_hash="pkt_fail",
        )
    snap2 = budget.snapshot()
    assert snap2["http_calls_used"] == 3
    assert [ev["status_category"] for ev in snap2["events"][-2:]] == ["TIMEOUT", "HTTP_ERROR"]

    # Budget is now exhausted (3/3): raises DailyBudgetExceeded immediately without invoking transport or retrying
    called = [0]
    with pytest.raises(signal_llm_review.DailyBudgetExceeded):
        review.call_chat_llm(
            [{"role": "user", "content": "hi"}],
            api_key="k",
            model="deepseek-chat",
            retries=2,
            transport=lambda *a, **k: called.__setitem__(0, called[0] + 1),
            budget=budget,
        )
    assert called[0] == 0
    assert budget.snapshot()["http_calls_used"] == 3


def test_decide_initial_hand_timeout_300_single_attempt_default_and_budget(tmp_path):
    fixed_now = dt.datetime(2026, 11, 16, 12, 0, 0, tzinfo=dt.timezone.utc)
    budget = signal_llm_review.DailyHttpBudget(tmp_path / "init_budget.json", limit=5, now_fn=lambda: fixed_now)

    # timeout_s >= 300 defaults to retries=0 (single attempt)
    attempts_300 = [0]

    def timeout_transport(*args, **kwargs):
        attempts_300[0] += 1
        raise TimeoutError("slow deepseek")

    with pytest.raises(TimeoutError):
        review.decide_initial_hand(
            "# input",
            contract_pair={},
            api_key="k",
            model="deepseek-reasoner",
            timeout_s=300,
            transport=timeout_transport,
            budget=budget,
            packet_hash="hand_hash_1",
        )
    assert attempts_300[0] == 1
    snap = budget.snapshot()
    assert snap["http_calls_used"] == 1
    assert snap["events"][0]["role"] == "astra_player_initial"
    assert snap["events"][0]["card_hash"] == "hand_hash_1"
    assert snap["events"][0]["status_category"] == "TIMEOUT"

    # timeout_s < 300 still defaults to retries=1 (2 attempts)
    attempts_180 = [0]
    reject_reply = (
        "## Reject\n\n"
        "Put路径：安全。\nCall路径：较近。\n对比结论：均观望。\nPut反证：存在回踩。\nCall反证：上方承压。\n"
        "<ANNOTATION_JSON>"
        + json.dumps({
            "put": "Put路径：安全。",
            "call": "Call路径：较近。",
            "comparison": "对比结论：均观望。",
            "put_counter": "Put反证：存在回踩。",
            "call_counter": "Call反证：上方承压。",
            "depends_on_ard": False,
        }, ensure_ascii=False)
        + "</ANNOTATION_JSON>"
    )

    def flaky_180(*args, **kwargs):
        attempts_180[0] += 1
        if attempts_180[0] == 1:
            raise TimeoutError("first timeout")
        return {"choices": [{"finish_reason": "stop", "message": {"content": reject_reply}}], "usage": {"total_tokens": 50}}

    res = review.decide_initial_hand(
        "# input",
        contract_pair={},
        api_key="k",
        model="deepseek-chat",
        timeout_s=180,
        transport=flaky_180,
    )
    assert attempts_180[0] == 2
    assert res["decision"]["action"] == "Reject"


def test_build_hold_review_input_renders_both_4h_and_12h_flow():
    frozen = {
        "contract": {"side": "put", "short_strike_usd": 97_000, "long_strike_usd": 95_000, "expiry_ms": T + 16 * 3_600_000},
        "invalidation": "entry_not_returned",
        "invalidation_meaning": "入场后严格进入责任区且至检查点未严格返回。",
        "accepted_conditions": ["保持 97,000 上方运行。"],
        "confirmation_excerpt": "每 5 分钟检查。",
        "entry_index_usd": 100_000.0,
    }
    md = review.build_hold_review_input(
        contract=frozen["contract"],
        entry_at_ms=T,
        initial_decision={"judgment": {"action": "Accept", "side": "put", "comparison": "选 Put", "put_counter": "下方曾回踩"}},
        frozen_condition=frozen,
        triggers=[{"category": "D", "code": "FLOW_REVERSAL", "summary_cn": "主动流转弱。", "observed_at_ms": T + 5 * M}],
        t0_summary={"short_distance_pct": 3.0},
        post_entry_summary={"sample_count": 2, "latest_spot_usd": 99_200.0, "min_short_distance_pct": 2.2, "latest_short_distance_pct": 2.2},
        condition_summary={"program_triggered": False, "mechanical_condition_state": "INTACT", "checks_count": 1},
        ard_summary="同口径未迁移",
        flow_summary={
            "flow_ready": True,
            "flow_asof_ms": T + 5 * M,
            "flow_direction": "bearish",
            "flow_fast_4h": "bearish",
            "flow_slow_12h": "neutral_to_bearish",
            "flow_cvd_4h": -42.5,
            "flow_momentum_4h_pct": -0.31,
            "flow_cvd_12h": -128.75,
            "flow_momentum_12h_pct": -0.88,
        },
        quote_summary={"short_buyback_ask_btc": 0.0012, "short_buyback_fee_btc": 0.0001, "short_buyback_total_btc": 0.0013, "short_quote_asof_ms": T + 5 * M, "short_buyback_gaps": []},
    )
    assert "近 4h CVD 净额：-42.50" in md and "近 4h 动量变化：-0.31%" in md
    assert "近 12h CVD 净额：-128.75" in md and "近 12h 动量变化：-0.88%" in md


def test_parse_hold_review_allows_negated_prohibition_commentary():
    frozen = {
        "contract": {"side": "call", "short_strike_usd": 103_000, "long_strike_usd": 105_000, "expiry_ms": T + 16 * 3_600_000},
        "invalidation": "entry_not_returned",
        "invalidation_meaning": "入场后严格进入责任区且至检查点未严格返回。",
        "accepted_conditions": ["保持 103,000 下方运行。"],
        "confirmation_excerpt": "每 5 分钟检查。",
        "entry_index_usd": 100_000.0,
    }
    nuanced = (
        "## REVOKE\n\n程序规则并非未触发，严禁改选 Put 侧（原入场为禁止重新选择 Accept / Put）。确认撤销当前 Call 承保。\n\n"
        "<HOLD_REVIEW_JSON>{\"verdict\": \"REVOKE\", \"affected_conditions\": [\"M1\"], "
        "\"public_reply\": \"程序状态不可标为条件未触发，维持既定边界并建议 REVOKE。\", \"key_facts\": [\"M1\"]}</HOLD_REVIEW_JSON>"
    )
    parsed = review.parse_hold_review_response(nuanced, frozen_condition=frozen, program_triggered=True)
    assert parsed["verdict"] == "REVOKE"
    assert parsed["protocol_status"] == "ALIGNED"


def test_parse_hold_review_tolerates_trailing_text_and_historical_untriggered():
    frozen = {
        "contract": {"side": "call", "short_strike_usd": 103_000, "long_strike_usd": 105_000, "expiry_ms": T + 16 * 3_600_000},
        "invalidation": "new_entry",
        "invalidation_meaning": "入场后严格进入责任区即触发。",
        "accepted_conditions": ["保持 103,000 下方运行。"],
        "confirmation_excerpt": "每 5 分钟检查。",
        "entry_index_usd": 100_000.0,
    }
    reply = (
        "## REVOKE\n\n此前条件未触发，现已确认突破并触发规则；维持原判断，不重新选择 Put。\n\n"
        "<HOLD_REVIEW_JSON>{\"verdict\": \"REVOKE\", \"affected_conditions\": [\"M1\"], "
        "\"public_reply\": \"维持原 Call 路径判断，不重新选择 Accept / Put，确认 REVOKE。\", \"key_facts\": [\"M1\"]}</HOLD_REVIEW_JSON>\n。"
    )
    parsed = review.parse_hold_review_response(reply, frozen_condition=frozen, program_triggered=True)
    assert parsed["verdict"] == "REVOKE"
    assert parsed["protocol_status"] == "ALIGNED"

    # Mixed-clause bypass attempts must STILL fail
    bypass_reselect = (
        "## REVOKE\n\n严禁改选 Put，建议改选 Call。\n\n"
        "<HOLD_REVIEW_JSON>{\"verdict\": \"REVOKE\", \"affected_conditions\": [\"M1\"], "
        "\"public_reply\": \"禁止改选 Put，建议重新选择 Call\", \"key_facts\": [\"M1\"]}</HOLD_REVIEW_JSON>"
    )
    with pytest.raises(ValueError, match="side_reselection_forbidden"):
        review.parse_hold_review_response(bypass_reselect, frozen_condition=frozen)

    bypass_untriggered = (
        "## HOLD\n\n程序规则并非未触发，但目前仍认为条件未触发。\n\n"
        "<HOLD_REVIEW_JSON>{\"verdict\": \"HOLD\", \"affected_conditions\": [\"M1\"], "
        "\"public_reply\": \"此前条件未触发，目前仍认为条件未触发。\", \"key_facts\": [\"M1\"]}</HOLD_REVIEW_JSON>"
    )
    with pytest.raises(ValueError, match="cannot_overturn_program_triggered"):
        review.parse_hold_review_response(bypass_untriggered, frozen_condition=frozen, program_triggered=True)


def test_call_chat_llm_raises_clear_length_reason_when_empty():
    with pytest.raises(ValueError, match="finish_reason=length"):
        review.call_chat_llm(
            [{"role": "user", "content": "hi"}],
            api_key="k",
            model="deepseek-v4-pro",
            transport=lambda *a, **k: {"choices": [{"finish_reason": "length", "message": {"content": ""}}]},
        )


def test_v11_category_d_anchor_flow_asof_and_continuation():
    frozen = {
        "contract": {"side": "call", "short_strike_usd": 65_500.0, "long_strike_usd": 67_500.0, "expiry_ms": T + 16 * 3_600_000},
        "invalidation": "new_entry",
        "invalidation_meaning": "严格入区即破坏",
        "accepted_conditions": ["保持 65,500 下方"],
        "entry_index_usd": 64_908.38,
    }
    t0_metrics = {
        "asof_ms": T,
        "spot_usd": 64_908.38,
        "short_distance_pct": 0.90,
        "flow_ready": True,
        "flow_asof_ms": T - 1_800_000,
        "flow_direction": "bullish",
        "flow_cvd_4h": 767.6,
        "flow_momentum_4h_pct": 1.43,
    }
    # 1. Spot is safer than t0 despite intrawindow bounce -> must NOT fire D!
    safer_rows = [
        {"asof_ms": T + 60_000, "spot_usd": 64_600.0, "short_distance_pct": 1.38, "flow_ready": True, "flow_asof_ms": T + 1_200_000, "flow_direction": "bullish", "flow_cvd_4h": 600.0, "flow_momentum_4h_pct": 0.50},
        {"asof_ms": T + 120_000, "spot_usd": 64_768.86, "short_distance_pct": 1.12, "flow_ready": True, "flow_asof_ms": T + 1_200_000, "flow_direction": "bullish", "flow_cvd_4h": 620.0, "flow_momentum_4h_pct": 0.65},
    ]
    assert review.detect_hold_triggers(
        frozen_condition=frozen, side="call", short_strike_usd=65_500.0, entry_at_ms=T,
        t0_metrics=t0_metrics, series_rows=safer_rows, now_ms=T + 180_000,
    ) == []

    # 2. Same flow_asof_ms after last_review -> must NOT refire D!
    stale_flow_rows = [
        {"asof_ms": T + 2_400_000, "spot_usd": 65_150.0, "short_distance_pct": 0.53, "flow_ready": True, "flow_asof_ms": T + 1_200_000, "flow_direction": "neutral_to_bullish", "flow_cvd_4h": 800.0, "flow_momentum_4h_pct": 1.50},
    ]
    trigs_stale = review.detect_hold_triggers(
        frozen_condition=frozen, side="call", short_strike_usd=65_500.0, entry_at_ms=T,
        t0_metrics=t0_metrics, series_rows=stale_flow_rows, now_ms=T + 2_500_000,
        last_review={"flow_asof_ms": T + 1_200_000, "flow_direction": "neutral_to_bullish", "flow_cvd_4h": 800.0, "spot_usd": 65_100.0, "short_distance_pct": 0.61},
    )
    assert all(t["category"] != "D" for t in trigs_stale)

    # 3. Genuine adverse continuation with advancing flow_asof_ms + >=0.25pp push -> FLOW_ADVERSE_CONTINUATION
    t0_metrics["flow_window_basis"] = {"source": "spot", "symbol": "BTCUSDT", "start_ms": T - 5 * 3_600_000}
    cont_rows = [
        {"asof_ms": T + 4_800_000, "spot_usd": 65_200.0, "short_distance_pct": 0.46, "flow_ready": True, "flow_asof_ms": T + 4_800_000, "flow_direction": "bullish", "flow_cvd_4h": 950.0, "flow_momentum_4h_pct": 1.80},
    ]
    cont_rows[0]["flow_window_basis"] = dict(t0_metrics["flow_window_basis"])
    trigs_cont = review.detect_hold_triggers(
        frozen_condition=frozen, side="call", short_strike_usd=65_500.0, entry_at_ms=T,
        t0_metrics=t0_metrics, series_rows=cont_rows, now_ms=T + 4_860_000,
    )
    d_cont = next(t for t in trigs_cont if t["category"] == "D")
    assert d_cont["code"] == "FLOW_ADVERSE_CONTINUATION"
    assert d_cont["signature"] == "FLOW_ADVERSE_CONTINUATION"
    cont_rows[0]["flow_window_basis"]["start_ms"] += M
    rolled = review.detect_hold_triggers(
        frozen_condition=frozen, side="call", short_strike_usd=65_500.0, entry_at_ms=T,
        t0_metrics=t0_metrics, series_rows=cont_rows, now_ms=T + 4_860_000,
    )
    assert all(t["category"] != "D" for t in rolled)


def test_v11_category_c_depends_on_ard_false_and_soft_budget():
    frozen = {
        "contract": {"side": "call", "short_strike_usd": 65_500.0, "long_strike_usd": 67_500.0, "expiry_ms": T + 16 * 3_600_000},
        "invalidation": "new_entry",
        "invalidation_meaning": "严格入区即破坏",
        "accepted_conditions": ["保持 65,500 下方"],
        "entry_index_usd": 64_908.38,
    }
    trigs = review.detect_hold_triggers(
        frozen_condition=frozen, side="call", short_strike_usd=65_500.0, entry_at_ms=T,
        t0_metrics={"asof_ms": T, "spot_usd": 64_908.38, "short_distance_pct": 0.90},
        series_rows=[{"asof_ms": T + 60_000, "spot_usd": 64_950.0, "short_distance_pct": 0.84}],
        ard_comparison={"old_pin": 64_000.0, "new_pin": 66_000.0, "pin_price_changed": True, "same_measure": True, "pin_status": "ok", "band_half": 400.0, "check_at_ms": T + 60_000},
        entry_pin_snapshot={"pin": 64_000.0, "pin_source": "deribit", "pin_method": "gex", "pin_status": "ok"},
        now_ms=T + 60_000,
    )
    assert [t["signature"] for t in trigs] == ["ARD_STRUCTURE_SHIFT:PIN"]

    # Soft budget=2 blocks ordinary trigger at count=2 -> BUDGET_DEFERRED; Priority A bypasses!
    trig_b = {"category": "B", "code": "MARGIN_DETERIORATION", "signature": "MARGIN_DETERIORATION", "depth_step": 5, "severity": 2, "observed_at_ms": T + 60_000, "summary_cn": "shrink"}
    st_deferred, act_deferred = review.schedule_review_action(
        {"ordinary_review_count": 2, "pending": [trig_b], "pending_since_ms": T},
        [], now_ms=T + 700_000, debounce_ms=600_000, cooldown_ms=0, max_ordinary_reviews=2,
    )
    assert act_deferred is None and st_deferred["last_skip"]["code"] == "BUDGET_DEFERRED"

    trig_a = {"category": "A", "code": "CONDITION_TRIGGERED", "signature": "CONDITION_TRIGGERED", "severity": 3, "observed_at_ms": T + 710_000, "summary_cn": "breach"}
    _, act_a = review.schedule_review_action(st_deferred, [trig_a], now_ms=T + 710_000, max_ordinary_reviews=2)
    assert act_a is not None and act_a["should_call"] is True and act_a["is_priority_a"] is True

    # Five-section prompt structure + M1=INTACT anti-boilerplate instruction
    md = review.build_hold_review_input(
        contract=frozen["contract"], entry_at_ms=T,
        initial_decision={"judgment": {"action": "Accept", "side": "call", "comparison": "选 Call", "call_counter": "反证"}},
        frozen_condition=frozen, triggers=trigs,
        t0_summary={"entry_index_usd": 64_908.38, "short_distance_pct": 0.90, "expected_move_usd": 1256.3},
        post_entry_summary={"sample_count": 2, "latest_spot_usd": 65_122.8, "min_short_distance_pct": 0.45, "latest_short_distance_pct": 0.58},
        condition_summary={"program_triggered": False, "mechanical_condition_state": "INTACT", "checks_count": 3},
        ard_summary={"old_pin": 64_000.0, "new_pin": 66_000.0, "pin_price_changed": True, "same_measure": True},
        flow_summary={"flow_ready": True, "flow_asof_ms": T, "flow_direction": "bullish", "flow_cvd_4h": 741.75, "flow_momentum_4h_pct": -0.49},
        quote_summary={"short_buyback_gaps": []},
        prior_reviews=[{"verdict": "HOLD", "spot_usd": 64_947.49, "short_distance_pct": 0.84, "requested_at_ms": T + 30_000, "public_reply": "ok"}],
    )
    for sec_title in ("## 1. 原承诺", "## 2. 上次 AI 已评估什么", "## 3. 这次真正新在哪里", "## 4. 程序的不可改写事实", "## 5. 唯一管理问题"):
        assert sec_title in md
    assert "禁止仅以“M1 未触发”或“D 仅作提醒”作为 `HOLD` 理由" in md


def test_canonical_prompt_constants_and_versions():
    assert review.MODEL_REQUEST_SCHEMA == "astra_player_model_request@1.0"
    assert review.INITIAL_SYSTEM_PROMPT_VERSION == "player_initial_system@1.0"
    assert review.HOLD_REVIEW_SYSTEM_PROMPT_VERSION == "player_hold_system@1.0"
    expected_initial = (
        "你是承保影子牌手。请完整阅读冻结牌面并用中文作答。\n"
        "【核心术语公理】\n"
        "- 责任区：卖方短腿与保护腿之间的亏损承受区间（触及边界不算入区，越过短腿进入区间才算严格入区）。\n"
        "- 本期尺度：指本期到期隐含波动给出的 `expected_move_usd` 预期波动尺度。\n"
        "- Pin / 墙：仅作当前空间结构位置参照，不带方向性，不构成价格吸附保证。\n"
        "- ARD：仅衡量过去 4 小时围绕同到期已知 Pin 的历史贴近、离返与迁移事实，不做未来方向预测。\n"
        "【作答与规则锁定】\n"
        "1. 在正文开头或明确独立标题行写出唯一结论：`## Accept / Put`、`## Accept / Call`、`## Reject` 或 `## Unknown`。\n"
        "2. 正文必须分别给出 Put 路径判断、Call 路径判断、Put 最强反证、Call 最强反证与两侧对比择优，并冻结历史事实、承担条件与破坏确认口径。\n"
        "3. 系统的机械有效破坏规则仅支持两选一：`\"new_entry\"`（入场后出现严格进入责任区的分钟采样即触发）或 `\"entry_not_returned\"`（入场后严格进入责任区且至固定检查点仍未严格返回责任外才触发）；固定检查钟默认每 5 分钟（`check_interval_ms = 300000`）按 UTC 网格执行。正文书写破坏确认口径时必须直接对齐该两选一规则与 5 分钟网格，严禁自行编造“每 60 分钟整点锚点”等其他检查时钟。\n"
        "4. 为便于程序做逐字子串核验，建议正文中 Put 路径、Call 路径、Put 最强反证、Call 最强反证、两侧对比择优，以及（若 Accept）冻结历史事实、承担条件、破坏确认口径（与 ARD 判读）各自先写一段连贯完整的中文整句段落，尽量不在摘录段落内使用分点列表或反引号。\n"
        "5. 在正文最后附带 `<ANNOTATION_JSON>...</ANNOTATION_JSON>`（必须是合法 JSON 对象）。其中 `put`、`call`、`comparison`、`put_counter`、`call_counter` 必须是**从上方中文正文原封不动逐字复制（verbatim substring）的非空连续片段**——不要删改、浓缩、跨段拼接或替换任何字词、数字、标点或符号；`depends_on_ard` 必须是显式布尔值。\n"
        "6. 若结论为 Accept，`<ANNOTATION_JSON>` 中必须包含 `condition_plan` 对象（不能是字符串），含：`invalidation`（仅限 `\"new_entry\"` 或 `\"entry_not_returned\"`）、`check_interval_ms`（默认 `300000`，整分钟毫秒）、`historical_facts`、`accepted_conditions`、`confirmation_excerpt`（三者均须是**从上方中文正文原封不动逐字复制的连续片段**，任意改写或浓缩都会导致解析失败作废），以及可选 `require_outside_at_t0`（布尔）；若 `depends_on_ard` 为 true，还须提供逐字复制自正文的 `ard_responsibility_meaning` 与 `ard_review_on`。"
    )
    assert review.INITIAL_SYSTEM_PROMPT == expected_initial
    assert review.HOLD_REVIEW_SYSTEM_PROMPT == (
        "你是中性回路影子持仓复核 AI。只可基于增量牌面输出 HOLD、REVOKE 或 UNKNOWN，严禁改选 Put/Call，严禁推翻已触发的 CONDITION_TRIGGERED。"
    )


def test_decide_initial_hand_returns_leakage_free_exact_request_payload():
    sent_bodies = []
    reject_reply = (
        "## Reject\n\n"
        "Put路径：安全。\nCall路径：较近。\n对比结论：均观望。\nPut反证：存在回踩。\nCall反证：上方承压。\n"
        "<ANNOTATION_JSON>"
        + json.dumps({
            "put": "Put路径：安全。",
            "call": "Call路径：较近。",
            "comparison": "对比结论：均观望。",
            "put_counter": "Put反证：存在回踩。",
            "call_counter": "Call反证：上方承压。",
            "depends_on_ard": False,
        }, ensure_ascii=False)
        + "</ANNOTATION_JSON>"
    )

    def transport(api_key, model, body, timeout, endpoint=None):
        sent_bodies.append((endpoint, deepcopy(body)))
        return {"choices": [{"finish_reason": "stop", "message": {"content": reject_reply}}], "usage": {"total_tokens": 42}}

    res = review.decide_initial_hand(
        "# Frozen Hand\nBody text",
        contract_pair={"put": {"short_strike_usd": 97000}, "call": {"short_strike_usd": 103000}},
        api_key="super-secret-api-key",
        model="deepseek-reasoner",
        endpoint="https://example.invalid/v1",
        reasoning_effort="high",
        packet_hash="pkt_123",
        transport=transport,
    )
    assert len(sent_bodies) == 1
    req = res["request_payload"]
    assert req["schema"] == review.MODEL_REQUEST_SCHEMA
    assert req["stage"] == "initial"
    assert req["fidelity"] == "EXACT"
    assert req["prompt_version"] == review.INITIAL_SYSTEM_PROMPT_VERSION
    assert req["endpoint"] == "https://example.invalid/v1/chat/completions"
    assert req["packet_hash"] == "pkt_123"
    assert req["messages"] == sent_bodies[0][1]["messages"]
    assert req["messages"][0] == {"role": "system", "content": review.INITIAL_SYSTEM_PROMPT}
    assert "候选合约对：" in req["messages"][1]["content"]
    assert "# Frozen Hand\nBody text" in req["messages"][1]["content"]
    assert req["parameters"] == {
        "stream": False,
        "max_tokens": 32768,
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }

    serialized = json.dumps(req, ensure_ascii=False)
    assert "super-secret-api-key" not in serialized
    banned_keys = {
        "decision", "judgment", "action", "side", "raw_reply", "response",
        "public_reply", "verdict", "outcome", "condition", "locked_series", "api_key",
    }
    assert banned_keys.isdisjoint(req.keys())
    assert banned_keys.isdisjoint(req["metadata"].keys())

    # call_chat_llm also returns request_body without api_key
    chat_res = review.call_chat_llm(
        [{"role": "user", "content": "ping"}],
        api_key="secret-k",
        model="deepseek-chat",
        transport=lambda *a, **k: {"choices": [{"finish_reason": "stop", "message": {"content": "pong"}}], "usage": {}},
    )
    assert chat_res["request_body"]["messages"] == [{"role": "user", "content": "ping"}]
    assert "api_key" not in chat_res["request_body"]


def test_format_single_box_and_markdown_preserve_order_fencing_and_fidelity():
    payload = review.build_hold_review_request_payload(
        "牌面内容包含代码块：\n```json\n{\"k\": 1}\n```\n以及四反引号 ````fence```` 示例。",
        model="deepseek-reasoner",
        reasoning_effort="medium",
        endpoint="https://api.deepseek.com",
        packet_hash="hold_hash_1",
        requested_at_ms=T,
        fidelity="RECONSTRUCTED",
        fidelity_reason="早期试运行未落盘原生 request.json，按冻结牌面重建",
        metadata={
            "dossier_id": "Deribit_BTC_20261115T080000Z",
            "hand_id": "hand_abc",
            "review_id": "rev_01",
            "sample": "SCHEDULED",
            "expiry_ms": T + 16 * 3_600_000,
            "decision": "LEAK_DECISION",
            "verdict": "LEAK_VERDICT",
            "api_key": "LEAK_API_KEY",
            "nested": {"outcome": "LEAK_OUTCOME", "safe": "keep_me"},
        },
    )
    assert "decision" not in payload["metadata"]
    assert "verdict" not in payload["metadata"]
    assert "api_key" not in payload["metadata"]
    assert payload["metadata"]["nested"] == {"safe": "keep_me"}

    # Multi-turn (>2 messages) dynamic rendering
    multi = deepcopy(payload)
    multi["messages"].append({"role": "assistant", "content": "第一轮分析内容"})
    multi["messages"].append({"role": "user", "content": "补充追问内容"})

    single_box = review.format_single_box_input(multi)
    assert single_box.endswith("\n")
    idx_sys = single_box.index("【SYSTEM】\n" + review.HOLD_REVIEW_SYSTEM_PROMPT)
    idx_u1 = single_box.index("【USER】\n牌面内容包含代码块：")
    idx_ast = single_box.index("【ASSISTANT】\n第一轮分析内容")
    idx_u2 = single_box.index("【USER】\n补充追问内容")
    assert idx_sys < idx_u1 < idx_ast < idx_u2

    md = review.format_model_input_markdown(
        multi,
        context={"action": "LEAK_ACTION", "side": "LEAK_SIDE", "hand_id": "hand_abc"},
    )
    assert md.startswith("# AI 牌手完整模型输入\n")
    assert "RECONSTRUCTED（早期试运行未落盘原生 request.json，按冻结牌面重建）" in md
    assert "hold_review（持仓复核）" in md
    assert "Deribit_BTC_20261115T080000Z" in md
    assert "hand_abc" in md
    assert "rev_01" in md
    assert "SCHEDULED" in md
    assert "## 消息 1 · 系统提示词 (`role: system`)" in md
    assert "## 消息 2 · 用户牌面输入 (`role: user`)" in md
    assert "## 消息 3 · 历史助手消息 (`role: assistant`)" in md
    assert "## 消息 4 · 用户牌面输入 (`role: user`)" in md
    assert "## DeepSeek 官方网页单输入框可复制区块" in md
    # Body has 4 backticks ````fence````, so outer fence must use at least 5 backticks
    assert "\n`````text\n" in md
    assert "\n`````\n" in md
    for leak in ("LEAK_DECISION", "LEAK_VERDICT", "LEAK_API_KEY", "LEAK_OUTCOME", "LEAK_ACTION", "LEAK_SIDE"):
        assert leak not in md

    # EXACT fidelity label check
    exact_payload = review.build_initial_request_payload(
        "# Exact Hand",
        contract_pair={},
        model="deepseek-chat",
        fidelity="EXACT",
    )
    exact_md = review.format_model_input_markdown(exact_payload)
    assert "- 来源保真度：EXACT\n" in exact_md
    assert "````text\n" in exact_md


def test_dossier_respond_atomically_persists_request_json_and_preserves_compat(tmp_path):
    clock = Clock(SLOT + DOSSIER_M)
    d = dossier.Dossiers(tmp_path, clock)
    hand = d.tick(dossier_plan(), lambda *_: dossier_source(clock))[0]

    # Backward compatibility when request_payload is None
    rec_compat = d.respond(hand, "compat-1", "RECEIVED", dossier_raw(), DOSSIER_EXCERPTS)
    assert "request_sha256" not in rec_compat
    assert not (hand / "responses" / "compat-1" / "request.json").exists()

    # Persists request.json atomically and records request_sha256 when request_payload is provided
    req_payload = review.build_initial_request_payload(
        (hand / "evidence" / "input.md").read_text(encoding="utf-8"),
        contract_pair={"put": {"short_strike_usd": 97000}, "call": {"short_strike_usd": 103000}},
        model="deepseek-reasoner",
        requested_at_ms=clock(),
        fidelity="EXACT",
        metadata={"dossier_id": hand.parent.parent.name, "hand_id": hand.name, "sample": "SCHEDULED"},
    )
    rec = d.respond(
        hand,
        "with-req-1",
        "RECEIVED",
        dossier_raw("Reject"),
        DOSSIER_EXCERPTS,
        model="deepseek-reasoner",
        requested=clock(),
        decided=clock(),
        channel="API_AUTO",
        request_payload=req_payload,
    )
    req_file = hand / "responses" / "with-req-1" / "request.json"
    assert req_file.is_file()
    expected_bytes = dossier.encode(req_payload)
    assert req_file.read_bytes() == expected_bytes
    assert rec["request_sha256"] == dossier.sha(expected_bytes)
    assert dossier.read(hand / "responses" / "with-req-1" / "attempt.json")["request_sha256"] == rec["request_sha256"]

    # Idempotent retry with identical request_payload succeeds
    assert d.respond(
        hand,
        "with-req-1",
        "RECEIVED",
        dossier_raw("Reject"),
        DOSSIER_EXCERPTS,
        model="deepseek-reasoner",
        requested=clock(),
        decided=clock(),
        channel="API_AUTO",
        request_payload=req_payload,
    ) == rec

    # Reusing attempt identity with different request_payload raises
    changed_payload = deepcopy(req_payload)
    changed_payload["model"] = "deepseek-chat"
    with pytest.raises(ValueError, match="attempt identity reused"):
        d.respond(
            hand,
            "with-req-1",
            "RECEIVED",
            dossier_raw("Reject"),
            DOSSIER_EXCERPTS,
            model="deepseek-reasoner",
            requested=clock(),
            decided=clock(),
            channel="API_AUTO",
            request_payload=changed_payload,
        )





