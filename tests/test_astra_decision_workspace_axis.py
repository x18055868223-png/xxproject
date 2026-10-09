import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent))
from astra_workbench_frontend_harness import render_workbench  # noqa: E402
from test_astra_wpem_frontend import _candidate, _workbench  # noqa: E402


def _axis_candidate(side="put", *, cutoff=1790323200000, policy_position=None, nominal=True):
    candidate = _candidate(side, with_wpem=False)
    is_call = side == "call"
    short = 103000 if is_call else 97000
    long = 105000 if is_call else 95000
    be = 103200 if is_call else 96600
    candidate_id = candidate["candidate_id"]
    snapshot_id = candidate["snapshot"]["snapshot_id"]
    presentation = {
        "mode": "CURRENT",
        "candidate_identity": {
            "candidate_id": candidate_id,
            "snapshot_id": snapshot_id,
            "short_strike_usd": short,
            "long_strike_usd": long,
            "near_breakeven_usd": be,
            "expiry_ms": 1790400000000,
            "reference_time_ms": cutoff,
            "current_price_usd": 100000,
            "quantity_btc": 0.1,
            "net_credit_after_entry_fee_btc": 0.00039,
        },
        "price_axis": {
            "current": {
                "price_usd": 100000,
                "source": "DERIBIT_BTC_USD_INDEX",
                "time_ms": cutoff,
            },
        },
        "decision_brief": {
            "pushes": [],
            "conflicts": [],
            "watch": {"qualified": False, "reason_cn": "没有合格观察区。"},
        },
    }
    if policy_position is not None:
        presentation["decision_brief"]["policy_position"] = policy_position
    references = [
        {
            "reference_id": "acceptance-zone",
            "family": "TRADED_ACCEPTANCE",
            "role": "OBSERVED_ZONE",
            "label_cn": "历史接受区",
            "display_low_usd": 96000,
            "display_high_usd": 98000,
            "coordinate_qualification": "COMPARABLE",
            "price_basis": "DERIBIT_BTC_USD_INDEX",
            "eligible_for_display": True,
        },
        {
            "reference_id": "flip-point",
            "family": "OPTION_STRUCTURE",
            "role": "EFFECTIVE_FLIP",
            "label_cn": "Effective Flip",
            "display_price_usd": 101500,
            "coordinate_qualification": "COMPARABLE",
            "price_basis": "DERIBIT_BTC_USD_INDEX",
            "eligible_for_display": True,
        },
        {
            "reference_id": f"{side}-short",
            "family": "CONTRACT_LIABILITY",
            "role": "SHORT_LEG",
            "display_price_usd": short,
            "coordinate_qualification": "COMPARABLE",
            "eligible_for_display": True,
            "expiry_scope": 1790400000000,
        },
        {
            "reference_id": f"{side}-be",
            "family": "CONTRACT_LIABILITY",
            "role": "NEAR_BREAKEVEN",
            "display_price_usd": be,
            "coordinate_qualification": "COMPARABLE",
            "eligible_for_display": True,
            "expiry_scope": 1790400000000,
        },
        {
            "reference_id": f"{side}-long",
            "family": "CONTRACT_LIABILITY",
            "role": "PROTECTIVE_LEG",
            "display_price_usd": long,
            "coordinate_qualification": "COMPARABLE",
            "eligible_for_display": True,
            "expiry_scope": 1790400000000,
        },
    ]
    if nominal:
        references.extend([
            {
                "reference_id": "cp-sth",
                "family": "ONCHAIN_COST",
                "role": "CP_STH",
                "label_cn": "链上成本 CP_STH",
                "display_price_usd": 98000,
                "raw_price": 98123.45,
                "coordinate_qualification": "NOMINAL_ONLY",
                "price_basis": "ONCHAIN_COST_CP_STH",
                "notional_reference_cn": "名义成本线，仅作背景。",
                "eligible_for_display": True,
            },
            {
                "reference_id": "chain-cost",
                "family": "ONCHAIN_COST",
                "role": "HOLDER_CAPITALIZATION",
                "label_cn": "持有者资本化参考",
                "display_low_usd": 93000,
                "display_high_usd": 94000,
                "coordinate_qualification": "NOMINAL_ONLY",
                "price_basis": "ONCHAIN_COST_CP_STH",
                "notional_reference_cn": "完整区间值保留。",
                "eligible_for_display": True,
            },
        ])
    candidate["btc_map"] = {
        "map_id": f"axis-{side}",
        "data_identity": "CURRENT",
        "cutoff_at_ms": cutoff,
        "current_price_usd": 100000,
        "policy_link": {
            "candidate_id": candidate_id,
            "snapshot_id": snapshot_id,
            "candidate_version": f"{side}@axis-test",
            "contract": {
                "side": side,
                "short_strike_usd": short,
                "long_strike_usd": long,
                "expiry_ms": 1790400000000,
            },
            "region_liability_full_rows": [
                {
                    "region_id": "distance-trap",
                    "region_label_cn": "错误距离陷阱",
                    "price_range_cn": "绝不应进入轴摘要：距现价 +42,424 USD / +42.42%",
                    "relationship_cn": "trap_only",
                    "coordinate_qualification": "COMPARABLE",
                    "terminal_scenario_basis": "official_settlement_inside_contract_comparable_coordinate",
                },
            ],
        },
        "presentation": presentation,
        "reference_pool": references,
    }
    return candidate


def _render_axis(candidates, selected="put", mode="focus"):
    view = _workbench(candidates)
    view["__test_state"] = {"selectedMapCandidateKey": selected, "mapAxisMode": mode}
    return render_workbench(view)["areas"]["btcMap"]


def _section(html, start_marker, end_marker):
    start = html.index(start_marker)
    end = html.index(end_marker, start)
    return html[start:end]


def test_policy_position_summary_uses_service_checkpoint_distances_before_svg():
    put = _axis_candidate(policy_position={
        "summary_cn": "服务端位置摘要：本单仍在当前价外侧。",
        "checkpoints": [
            {"label_cn": "卖出腿", "price_usd": 97000, "distance_usd": -3000, "distance_pct": -3.0},
            {"label_cn": "保护腿", "price_usd": 95000, "distance_usd": -5000, "distance_pct": -5.0},
        ],
    })
    html = _render_axis([put])
    assert html.index("decision-position-summary") < html.index("map-price-svg")
    summary = _section(html, 'class="decision-position-summary"', 'class="decision-axis-legend"')

    assert "服务端位置摘要：本单仍在当前价外侧。" in summary
    assert "当前现价" in summary
    assert "100,000 USD" in summary
    assert "卖出腿" in summary
    assert "97,000 USD；距当前现价 -3,000 USD；-3%" in summary
    assert "保护腿" in summary
    assert "95,000 USD；距当前现价 -5,000 USD；-5%" in summary
    assert "42,424" not in summary
    assert "+42.42%" not in summary


def test_missing_policy_position_does_not_invent_distances_from_liability_rows():
    put = _axis_candidate(policy_position=None)
    html = _render_axis([put])
    summary = _section(html, 'class="decision-position-summary"', 'class="decision-axis-legend"')

    assert "尚未生成同版保单位置摘要" in summary
    assert "不从区域责任行补造本单距离" in summary
    assert "服务端未提供检查点距离" in summary
    assert "42,424" not in summary
    assert "+42.42%" not in summary

    unselected = _workbench([put])
    unselected["btc_map"] = put["btc_map"]
    unselected_html = render_workbench(unselected)["areas"]["btcMap"]
    unselected_summary = _section(unselected_html, 'class="decision-position-summary"', 'class="decision-axis-legend"')
    assert "未选保单" in unselected_summary
    assert "不套用 Put/Call 合同距离" in unselected_summary
    assert "卖出腿" not in unselected_summary


@pytest.mark.parametrize("mode", ["global_fallback", "wrong_candidate", "wrong_snapshot", "missing_identity"])
def test_selected_candidate_cannot_borrow_global_or_mismatched_policy_position(mode):
    put = _axis_candidate(policy_position={"summary_cn": "GLOBAL_OR_WRONG_IDENTITY_TRAP", "checkpoints": [{"label_cn": "卖出腿", "price_usd": 97000, "distance_usd": 77777, "distance_pct": 77}]})
    map_data = put["btc_map"]
    view = _workbench([put])
    if mode == "global_fallback":
        del put["btc_map"]
        view["btc_map"] = map_data
    elif mode == "wrong_candidate":
        map_data["presentation"]["candidate_identity"]["candidate_id"] = "another-candidate"
    elif mode == "wrong_snapshot":
        map_data["policy_link"]["snapshot_id"] = "another-snapshot"
    else:
        for identity in [map_data["policy_link"], map_data["presentation"]["candidate_identity"]]:
            identity.pop("candidate_id", None)
    view["__test_state"] = {"selectedMapCandidateKey": "put"}
    html = render_workbench(view)["areas"]["btcMap"]
    summary = _section(html, 'class="decision-position-summary"', 'class="decision-axis-legend"')
    assert "GLOBAL_OR_WRONG_IDENTITY_TRAP" not in summary
    assert "77,777" not in summary
    assert "尚无已核对的本单 MAP 位置摘要" in summary
    assert "不套用 Put/Call 合同距离" in summary


def test_folded_nominal_details_preserves_complete_source_and_contract_items():
    put = _axis_candidate(policy_position={
        "summary_cn": "服务端位置摘要。",
        "checkpoints": [],
    })
    call = _axis_candidate("call", cutoff=1790326800000, policy_position={
        "summary_cn": "Call 对侧摘要。",
        "checkpoints": [],
    }, nominal=False)
    html = _render_axis([put, call])
    details = _section(html, 'class="map-details decision-nominal-details"', 'class="map-footnote"')

    assert "data-map-disclosure-key=" in details
    assert "axis-nominal" in details
    assert "名义 / 异源 / 非同刻对照（3项）" in details
    assert "链上成本 CP_STH" in details
    assert 'data-map-region-id="cp-sth"' in details
    assert "98,000 USD" in details
    assert "原价 98,123.45 USD" in details
    assert "名义成本线，仅作背景。" in details
    assert "持有者资本化参考" in details
    assert 'data-map-region-id="chain-cost"' in details
    assert "93,000 USD — 94,000 USD" in details
    assert "完整区间值保留。" in details
    assert "Call 对侧合同" in details
    assert 'data-map-region-id="call"' in details
    assert "卖出腿 103,000 USD" in details
    assert "近端BE 103,200 USD" in details
    assert "保护腿 105,000 USD" in details
    assert "对侧冻结时点不同" in details
