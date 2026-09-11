import copy
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import signal_evidence_v2 as evidence
from test_signal_evidence_v2 import AS_OF_MS, base_card, clone, transition_for


TOOL = TOOLS / "signal_change_display.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("signal_change_display", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def rows_by_key(display):
    return {item["key"]: item for item in display["rows"]}


def set_funding(card, raw_rate):
    funding = card["factor_cross_section"]["funding"]
    funding["last_rate"] = raw_rate
    funding["canonical_funding_semantics"]["raw_funding_rate"] = raw_rate
    funding["canonical_funding_semantics"]["canonical_text_cn"] = (
        f"资金费率 {raw_rate * 100:+.4f}%：按原始费率记录。"
    )


def event_to_fixed_pair():
    previous = base_card("PREV-DISPLAY", AS_OF_MS - 600000, price=100.0)
    current = base_card("CURR-DISPLAY", AS_OF_MS, price=101.0)
    previous["schema"]["record_type"] = "confirmed_signal_event_audit"
    current["schema"]["record_type"] = "fixed_analysis_round_audit"
    return previous, current


def test_same_version_event_fixed_rows_cover_core_facts(tool):
    previous, current = event_to_fixed_pair()
    previous["factor_cross_section"]["tmvf"]["tmv_blend"] = -0.2
    current["factor_cross_section"]["tmvf"]["tmv_blend"] = 0.15
    previous["factor_cross_section"]["gex_info"]["net_gamma_notional_usd"] = 210000000.0
    current["factor_cross_section"]["gex_info"]["net_gamma_notional_usd"] = 220000000.0
    previous["factor_cross_section"]["micro_flow"]["combined"]["direction"] = "bearish"
    current["factor_cross_section"]["micro_flow"]["combined"]["direction"] = "bullish"
    previous["factor_cross_section"]["macro_pressure"]["macro_score"] = -0.1
    current["factor_cross_section"]["macro_pressure"]["macro_score"] = 0.2
    set_funding(previous, 0.00002)
    set_funding(current, -0.00003)
    previous["factor_cross_section"]["gamma_regime"]["pin"]["pin_strike"] = 100.0
    current["factor_cross_section"]["gamma_regime"]["pin"]["pin_strike"] = 102.0

    display = tool.build_change_display(
        current, previous, transition_for(evidence, previous, current))
    rows = rows_by_key(display)

    assert_true(len(display["rows"]) == 6, "core display row count")
    assert_true(display["reasons_cn"][0].startswith("前后卡身份"),
                "card-level comparison reason")
    assert_true(rows["tmv_blend"]["usable"] is True, "TMV row usable")
    assert_true(rows["tmv_blend"]["delta"] == 0.35, "TMV absolute delta")
    assert_true(rows["net_gamma_notional_usd"]["delta"] == 10000000.0,
                "net Gamma USD delta")
    assert_true(rows["active_flow"]["delta"] is None
                and "不计算幅度差" in rows["active_flow"]["summary_cn"],
                "direction row does not invent a delta")
    assert_true(rows["macro_score"]["delta"] == 0.3,
                "macro original-scale delta")
    assert_true(rows["funding_raw_rate"]["delta"] == -0.00005,
                "funding raw decimal delta")
    assert_true(rows["pin_strike"]["delta"] == 2.0,
                "Pin absolute strike migration")


def test_transition_identity_failure_returns_no_rows(tool):
    previous, current = event_to_fixed_pair()
    current["identity"]["strategy_version"] = "1.6.2"
    display = tool.build_change_display(
        current, previous, transition_for(evidence, previous, current))

    assert_true(display["rows"] == [], "bad card identity must suppress rows")
    assert_true(any("策略版本不同" in item for item in display["reasons_cn"]),
                "strategy mismatch reason")


def test_raw_source_change_closes_only_that_row(tool):
    previous, current = event_to_fixed_pair()
    previous["factor_cross_section"]["tmvf"]["source_ref"] = "TMV_A"
    current["factor_cross_section"]["tmvf"]["source_ref"] = "TMV_B"
    previous["factor_cross_section"]["gex_info"]["net_gamma_notional_usd"] = 0.0
    current["factor_cross_section"]["gex_info"]["net_gamma_notional_usd"] = -5.0

    display = tool.build_change_display(
        current, previous, transition_for(evidence, previous, current))
    rows = rows_by_key(display)

    assert_true(rows["tmv_blend"]["usable"] is False,
                "raw source change closes only TMV")
    assert_true("原始来源身份或窗口不同" in rows["tmv_blend"]["gap_cn"],
                "TMV raw-source gap")
    assert_true(rows["net_gamma_notional_usd"]["usable"] is True,
                "GEX row remains usable")
    assert_true(rows["net_gamma_notional_usd"]["previous"] == 0.0
                and rows["net_gamma_notional_usd"]["current"] == -5.0
                and rows["net_gamma_notional_usd"]["delta"] == -5.0,
                "zero and negative GEX values are valid")


def install_gex_time(card, generated_ms):
    gex = card["factor_cross_section"]["gex_info"]
    gex["net_gamma_notional_usd"] = 210000000.0
    gex["gex_time_semantics"] = {
        "schema_version": "gex_time_semantics@1.0.0",
        "fields": {
            "gex_board.total_net_gex": {
                "source_ref": "gex-board.total-net-gex",
                "observed_at_ms": None,
                "generated_at_ms": generated_ms,
                "fetched_at_ms": generated_ms,
                "available_at_ms": generated_ms,
                "time_basis": "upstream_result_time",
                "time_errors": [],
            },
        },
    }


def test_future_gex_clock_closes_only_net_gamma_row(tool):
    previous, current = event_to_fixed_pair()
    install_gex_time(previous, previous["identity"]["confirmed_time_ms"] - 1000)
    install_gex_time(current, current["identity"]["confirmed_time_ms"] + 1000)

    display = tool.build_change_display(
        current, previous, transition_for(evidence, previous, current))
    rows = rows_by_key(display)

    assert_true(rows["net_gamma_notional_usd"]["usable"] is False,
                "future GEX source time closes net Gamma row")
    assert_true("当前卡事实不可用" in rows["net_gamma_notional_usd"]["gap_cn"]
                or "晚于卡片时点" in rows["net_gamma_notional_usd"]["gap_cn"],
                "future-time gap is visible")
    assert_true(rows["macro_score"]["usable"] is True,
                "unrelated macro row remains usable")


def test_missing_previous_value_stays_gap_without_zero_backfill(tool):
    previous, current = event_to_fixed_pair()
    previous["factor_cross_section"]["tmvf"].pop("tmv_blend", None)
    current["factor_cross_section"]["tmvf"]["tmv_blend"] = -0.42

    display = tool.build_change_display(
        current, previous, transition_for(evidence, previous, current))
    row = rows_by_key(display)["tmv_blend"]

    assert_true(row["usable"] is False, "missing previous closes TMV row")
    assert_true(row["previous"] is None and row["current"] == -0.42,
                "missing previous is not backfilled to zero")
    assert_true(row["delta"] is None, "missing previous has no delta")
    assert_true("前卡缺少该事实" in row["gap_cn"],
                "missing previous gap")


def test_inputs_and_llm_review_payload_are_not_modified(tool):
    previous, current = event_to_fixed_pair()
    current["integrated_trade_advisory"] = {
        "side_evidence_ratings": {
            "put_credit": {"grade": "S", "summary_cn": "不要进入变化投影"},
        },
        "advisory_guidance": {"summary_cn": "旧评审输入不应被复制"},
    }
    before_current = copy.deepcopy(current)
    before_previous = copy.deepcopy(previous)
    transition = transition_for(evidence, previous, current)
    before_transition = copy.deepcopy(transition)

    display = tool.build_change_display(current, previous, transition)
    payload = json.dumps(display, ensure_ascii=False)

    assert_true(current == before_current, "current card not mutated")
    assert_true(previous == before_previous, "previous card not mutated")
    assert_true(transition == before_transition, "transition not mutated")
    assert_true("不要进入变化投影" not in payload
                and "旧评审输入不应被复制" not in payload,
                "LLM review content stays out of deterministic projection")


def run_all():
    tool = load_tool()
    previous, current = event_to_fixed_pair()
    previous['factor_cross_section']['tmvf']['window'] = '4小时'
    current['factor_cross_section']['tmvf']['window'] = '12小时'
    rows = rows_by_key(tool.build_change_display(current, previous, transition_for(evidence, previous, current)))
    assert_true(not rows['tmv_blend']['usable'], 'different TMV windows cannot form an endpoint delta')
    previous, current = event_to_fixed_pair()
    previous['market_context']['quote_currency'] = 'USDT'
    current['market_context']['quote_currency'] = 'USD'
    rows = rows_by_key(tool.build_change_display(current, previous, transition_for(evidence, previous, current)))
    assert_true(not rows['pin_strike']['usable'], 'different point quote units cannot form a migration')
    test_same_version_event_fixed_rows_cover_core_facts(tool)
    test_transition_identity_failure_returns_no_rows(tool)
    test_raw_source_change_closes_only_that_row(tool)
    test_future_gex_clock_closes_only_net_gamma_row(tool)
    test_missing_previous_value_stays_gap_without_zero_backfill(tool)
    test_inputs_and_llm_review_payload_are_not_modified(tool)
    print("signal_change_display: PASS")


if __name__ == "__main__":
    run_all()
