from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools.astra_map_presentation_v241 import build_presentation


T = 1_790_323_200_000


def record(record_id, product_id, values, *, first_seen=T - 1_000, obs_end=T - 2_000,
           currency="USD", unit="USD", asset="BTC", can_use=True, status="USABLE", simulation_only=False):
    return {
        "record_id": record_id,
        "identity": {"product_id": product_id, "currency": currency, "unit": unit, "asset": asset},
        "collector": {"synthetic_fixture": simulation_only},
        "time": {"first_seen_at_ms": first_seen, "observation_end_ms": obs_end},
        "content": {"values": values},
        "usage_qualification": {"can_use": can_use, "status": status, "qualified_at_ms": T, "simulation_only": simulation_only},
    }


def snapshot(side="put"):
    if side == "put":
        short, long, be = 97000.0, 95000.0, 96680.0
    else:
        short, long, be = 103000.0, 105000.0, 103320.0
    return {
        "schema": "astra_underwriting_snapshot@2.0.0",
        "snapshot_id": f"snap-{side}",
        "candidate_id": f"cand-{side}",
        "contract": {
            "side": side,
            "short_strike_usd": short,
            "long_strike_usd": long,
            "quantity_btc": 1.0,
            "expiry_ms": T + 86_400_000,
            "settlement_currency": "BTC",
            "quote_currency": "BTC",
        },
        "data_identity": {"kind": "live_public_capture"},
        "market": {"valuation_ts_ms": T, "reference_price_usd": 100000.0, "reference_source": "deribit_index"},
        "fee": {"basis": "configured_assumption", "covers": ["entry", "delivery"]},
        "economics": {
            "net_credit_after_entry_fee_btc": 0.00028,
            "net_credit_after_fee_btc": 0.00028,
            "visible_credit_btc": 0.0003,
            "theoretical_two_leg_fee_btc": 0.00002,
            "reference_margin_btc": 0.00005,
        },
        "liability": {"breakeven_usd": [be]},
        "llm_review": {"status": "not_requested", "reviewed_snapshot_id": None},
    }


def map_obj():
    return {
        "schema": "btc_map@1",
        "map_id": "map-1",
        "cutoff_at_ms": T,
        "current_price_usd": 100000.0,
        "price_basis": "deribit_index",
        "source_manifest": {
            "cutoff_at_ms": T,
            "source_records": [
                record("etf-1", "btc.etf.flow.daily.v1", {"flow_3d_usd_mn": 300.0}, unit="US$m"),
                record("brk-1", "btc.brk.cost_basis.v1", {"cp_sth_usd": 98000.0}, first_seen=T - 3_000, obs_end=T - 10_000),
                record("funding-1", "binance.um.btcusdt.funding.settled.v1", {"funding": 0.00018}),
                record("macro-1", "macro.usd.broad.close.v1", {"usd_5d_pct": 0.6}),
                record("kpf-1", "btc.kpf.map.readonly.v1", {"raw_zone": [94450.0, 95550.0]}),
                record("price-1", "deribit.btc.index.map_context.v1", {"price_usd": 100000.0, "price_basis": "deribit_index"}, unit="USD"),
            ]
        },
        "background": {
            "allocation": {
                "summary_cn": "ETF净配置仍正，近期速度较低。",
                "metrics": {
                    "flow_3d_usd_mn": 300.0,
                    "flow_7d_usd_mn": 1500.0,
                    "daily_avg_3d_usd_mn": 100.0,
                    "daily_avg_7d_usd_mn": 214.285,
                },
                "cutoff_at_ms": T - 100,
                "source_record_ids": ["etf-1"],
                "fact_state": "usable",
                "missing": [],
            },
            "inventory": {
                "summary_cn": "价格高于短期成本参考，亏损兑现也在增加。",
                "metrics": {
                    "cp_sth_usd": 98000.0,
                    "gap_sth_now_pct": 2.04,
                    "loss_share_current_pct": 25.0,
                    "loss_share_previous_pct": 20.0,
                    "realized_loss_current_usd_mn": 300.0,
                    "realized_loss_previous_usd_mn": 500.0,
                },
                "cutoff_at_ms": T - 200,
                "source_record_ids": ["brk-1"],
                "fact_state": "usable",
                "missing": [],
            },
            "financing": {
                "summary_cn": "融资暴露扩张，但借款热度历史尚不完整。",
                "metrics": {
                    "oi_24h_pct": 4.2,
                    "funding_24h": 0.00018,
                    "missing": ["borrow_history_missing"],
                },
                "cutoff_at_ms": T - 300,
                "source_record_ids": ["funding-1"],
                "fact_state": "partial",
                "missing": ["borrow_history_missing"],
            },
            "external_conditions": {
                "summary_cn": "美元与利率在同一窗口上行。",
                "metrics": {"usd_5d_pct": 0.6, "nominal_10y_5d_bp": 15.0, "real_10y_5d_bp": 12.0, "btc_5d_pct": 2.0},
                "cutoff_at_ms": T - 400,
                "source_record_ids": ["macro-1"],
                "fact_state": "usable",
                "missing": [],
            },
        },
        "reference_pool": [
            {
                "reference_id": "cp-sth",
                "family": "ONCHAIN_COST",
                "role": "CP_STH",
                "geometry": "POINT",
                "raw_price": 98000.0,
                "coordinate_qualification": "NOMINAL_ONLY",
                "selected_for_display": True,
                "source_record_ids": ["brk-1"],
            },
            {
                "reference_id": "kpf-zone",
                "family": "TRADED_ACCEPTANCE",
                "role": "KPF_BASIN",
                "geometry": "OBSERVED_ZONE",
                "raw_low": 94450.0,
                "raw_high": 95550.0,
                "coordinate_qualification": "COMPARABLE",
                "selected_for_display": True,
                "source_record_ids": ["kpf-1"],
            },
            {
                "reference_id": "binance-zone",
                "family": "TRADED_ACCEPTANCE",
                "role": "KPF_BASIN",
                "geometry": "OBSERVED_ZONE",
                "raw_low": 94450.0,
                "raw_high": 95550.0,
                "coordinate_qualification": "NOMINAL_ONLY",
                "selected_for_display": True,
                "source_record_ids": [],
            },
            {
                "reference_id": "contract-near-be",
                "family": "CONTRACT_LIABILITY",
                "role": "NEAR_BREAKEVEN",
                "geometry": "POINT",
                "raw_price": 96680.0,
                "coordinate_qualification": "COMPARABLE",
                "eligible_for_display": True,
                "source_record_ids": [],
            },
        ],
        "response_events": [
            {
                "event_type": "POSITION_OBSERVED",
                "reference_id": "kpf-zone",
                "observed_at_ms": T - 500,
                "known_at_ms": T - 400,
                "method": "PRICE_SAMPLE",
                "coverage": "COMPLETE",
                "price": 95000.0,
                "current_position": "INSIDE",
            },
            {
                "event_type": "REFERENCE_REVISION_CHANGED",
                "reference_id": "kpf-zone",
                "previous_revision_id": "old",
                "revision_id": "new",
                "event_at_ms": T - 300,
            },
        ],
        "response_state": {"segments": {"kpf-zone": {"last_position": "INSIDE"}}},
        "policy_link": {
            "candidate_id": "cand-put",
            "candidate_version": "snap-put-original",
            "valuation_version": "valuation-original",
            "M_ref_btc": 0.00005,
            "contract": {"side": "put", "short_strike_usd": 97000.0, "long_strike_usd": 95000.0, "quantity_btc": 1.0, "expiry_ms": T + 86_400_000},
            "region_liability_rows": [
                {
                    "region_id": "kpf-zone",
                    "coordinate_qualification": "COMPARABLE",
                    "relation_to_short_long_be": ["partial_payout", "beyond_protection"],
                    "terminal_payout_range": {"min_btc": 0.0, "max_btc": 0.01},
                    "static_net_range": {"min_btc": -0.0097, "max_btc": 0.00028},
                    "cost_scope": {"title": "hold_to_expiry_static_result_with_listed_fees", "covers": ["entry", "delivery"], "fee_basis": "configured_assumption"},
                },
                {
                    "region_id": "binance-zone",
                    "coordinate_qualification": "NOMINAL_ONLY",
                    "terminal_payout_range": {"min_btc": 0.0, "max_btc": 0.01},
                    "static_net_range": {"min_btc": -0.0097, "max_btc": 0.00028},
                    "terminal_scenario_basis": "nominal_number_if_official_settlement_equaled_it",
                    "limitations": ["nominal_scenario_not_precise_contract_mapping"],
                },
            ],
            "region_liability_full_rows": [],
        },
    }


def row_by_key(presentation, key):
    return next(row for row in presentation["background_rows"] if row["key"] == key)


def response_by_region(presentation, region):
    return next(row for row in presentation["response_by_region"] if row["region_id"] == region)


def real_put_map_and_snapshot():
    path = Path(__file__).parent / "fixtures/astra_map_presentation_real_v241.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["map"], data["snapshot"]


def test_contract_identity_and_price_axis_do_not_confuse_cp_sth_with_put_call():
    p = build_presentation(map_obj(), snapshot())

    assert p["schema"] == "btc_map_presentation@2.4.2"
    assert p["rule_version"] == "astra_map_presentation_rules@2.4.2"
    assert p["mode"] == "LIVE"
    identity = p["candidate_identity"]
    assert identity["side"] == "put"
    assert identity["short_strike_usd"] == 97000.0
    assert identity["long_strike_usd"] == 95000.0
    assert identity["net_credit_btc"] == 0.00028
    assert identity["gross_credit_btc"] == 0.0003
    assert identity["entry_fee_btc"] == 0.00002
    assert identity["near_breakeven_usd"] == 96680.0
    labels = {item["label_cn"] for item in p["price_axis"]["contract_markers"]}
    assert "卖Put Ks" in labels
    assert "买Put Kl" in labels
    current = p["price_axis"]["current"]
    assert current["price_usd"] == 100000.0
    assert current["time_ms"] == T - 2_000
    assert current["source_record_id"] == "price-1"
    assert current["source_cn"] == "Deribit BTC USD指数"
    cp = next(ref for ref in p["price_axis"]["references"] if ref["reference_id"] == "cp-sth")
    assert cp["label_cn"] == "短期持有者资本化价格"
    assert cp["track_cn"] == "持有者成本"


def test_u09_etf_still_inflow_but_speed_down_is_explicit():
    p = build_presentation(map_obj(), snapshot())
    allocation = row_by_key(p, "allocation")

    assert "净流入" in allocation["comparison_cn"]
    assert "不写成流出" in allocation["comparison_cn"]
    labels = {item["label"] for item in allocation["primary_metrics"]}
    assert {"3交易日净配置", "7交易日净配置", "3交易日净配置名义折合", "7交易日净配置名义折合", "近期日均", "对照日均"}.issubset(labels)
    metrics = {item["label"]: item for item in allocation["primary_metrics"]}
    assert metrics["3交易日净配置名义折合"]["value"] == pytest.approx(3000.0)
    assert metrics["3交易日净配置名义折合"]["conversion_price_usd"] == 100000.0
    assert "不是ETF实际买卖BTC数量" in metrics["3交易日净配置名义折合"]["measure_note_cn"]
    assert "Farside" in allocation["source_explanation"]["source_cn"]
    assert "不是实际BTC" in allocation["source_explanation"]["measure_cn"]


def test_u10_loss_share_up_but_loss_amount_down_keeps_both_facts():
    p = build_presentation(map_obj(), snapshot())
    inventory = row_by_key(p, "inventory")

    assert "亏损份额上升" in inventory["comparison_cn"]
    assert "绝对亏损金额下降" in inventory["comparison_cn"]
    assert "抛压金额增加" in inventory["comparison_cn"]
    assert inventory["first_seen_at_ms"] == T - 3_000
    assert inventory["source_observation_end_ms"] == T - 10_000


def test_u11_funding_positive_without_borrow_history_does_not_create_heat_label():
    p = build_presentation(map_obj(), snapshot())
    financing = row_by_key(p, "financing")

    assert "USDT" in financing["comparison_cn"]
    assert "Funding/OI" in financing["comparison_cn"]
    assert "过热" in financing["comparison_cn"]
    assert any("借款" in item for item in financing["missing_cn"])
    assert all("热度" not in str(item.get("label")) for item in financing["primary_metrics"])
    assert "背景缺口另列" in financing["source_explanation"]["qualification_cn"]
    labels = {item["label"] for item in financing["primary_metrics"]}
    assert "币安 BTCUSDT USD-M 原生OI变化" in labels
    assert "币安 BTCUSDT USD-M 已结算Funding" in labels
    assert "币安 BTCUSDT USD-M 永续" in financing["source_explanation"]["market_cn"]


def test_source_explanation_keeps_mixed_source_qualification_as_partial():
    m = map_obj()
    m["source_manifest"]["source_records"].append(
        record("borrow-1", "cex.usdt.borrow_rate.v1", {}, can_use=False, status="STALE")
    )
    m["background"]["financing"]["source_record_ids"] = ["funding-1", "borrow-1"]
    p = build_presentation(m, snapshot())
    financing = row_by_key(p, "financing")

    assert "仅部分来源通过冻结用途资格" in financing["source_explanation"]["qualification_cn"]
    assert "来源记录通过同版冻结用途资格" not in financing["source_explanation"]["qualification_cn"]


def test_u12_single_price_sample_says_path_unknown_not_position_recorded():
    p = build_presentation(map_obj(), snapshot())
    response = response_by_region(p, "kpf-zone")
    first = response["events"][0]

    assert "只有一个价格样本" in first["event_cn"]
    assert "进入路径未知" in first["event_cn"]
    assert "价格位置已记录" not in first["event_cn"]
    assert first["method"] == "PRICE_SAMPLE"
    assert first["coverage"] == "COMPLETE"


def test_u13_reference_revision_change_is_not_a_breakout():
    p = build_presentation(map_obj(), snapshot())
    response = response_by_region(p, "kpf-zone")
    revision = response["events"][1]

    assert revision["event_type"] == "REFERENCE_REVISION_CHANGED"
    assert "区域版本更新" in revision["event_cn"]
    assert "不记作价格突破" in revision["event_cn"]


def test_u15_nominal_cross_basis_region_is_preserved_but_limited():
    p = build_presentation(map_obj(), snapshot())
    nominal = next(row for row in p["liability_by_region"] if row["region_id"] == "binance-zone")

    assert nominal["coordinate_qualification"] == "NOMINAL_ONLY"
    assert "名义参考" in nominal["summary_cn"]
    assert "不能当作精确价格共位" in nominal["summary_cn"]


def test_fact_summary_has_at_most_three_evidence_backed_items():
    p = build_presentation(map_obj(), snapshot())

    assert len(p["fact_summary"]) <= 3
    assert [item["kind"] for item in p["fact_summary"]] == ["main_background", "key_divergence", "policy_focus"]
    assert p["fact_summary"][0]["evidence_ids"] == ["etf-1"]
    assert p["fact_summary"][1]["evidence_ids"] == ["brk-1"]
    assert p["fact_summary"][2]["evidence_ids"] == ["kpf-zone"]
    text = " ".join(item["text_cn"] for item in p["fact_summary"])
    assert "partial_payout" not in text
    assert "kpf-zone" not in text


def test_decision_brief_answers_four_workbench_questions_without_scoring():
    p = build_presentation(map_obj(), snapshot())
    brief = p["decision_brief"]

    assert [item["title_cn"] for item in brief["pushes"]] == ["配置资金", "持有者成本与兑现", "杠杆与资金成本", "外部压力"]
    assert all("source_state_cn" in item and "basis_cn" in item for item in brief["pushes"])
    assert any(item["kind_cn"] == "时窗限制" for item in brief["conflicts"])
    assert any(item["kind_cn"] == "数据缺口" for item in brief["conflicts"])
    assert "评分" not in json.dumps(brief, ensure_ascii=False)
    assert brief["watch"]["qualified"] is True
    assert brief["watch"]["primary_region_id"] == "kpf-zone"
    assert "不含方向预测" in brief["watch"]["reason_cn"]
    assert "合同责任排序" in brief["watch"]["reason_cn"]
    assert "部分赔付区" in brief["watch"]["reason_cn"]
    assert "保护腿之外" in brief["watch"]["reason_cn"]
    assert "区域距卖出腿 -2550 至 -1450 USD" in brief["watch"]["reason_cn"]
    assert "尚未形成触达、进入或离开" in brief["watch"]["check_cn"]


def test_fixture_mode_is_explicit_when_snapshot_missing_or_forced():
    p = build_presentation(map_obj())
    forced = build_presentation(map_obj(), snapshot(), presentation_mode="fixture")

    assert p["mode"] == "FIXTURE"
    assert p["mode_cn"] == "合成测试"
    assert forced["mode"] == "FIXTURE"
    assert forced["mode_cn"] == "合成测试"
    assert forced["decision_brief"]["watch"]["status_cn"] == "本地模拟观察区"


def test_visible_credit_is_not_relabelled_as_net_credit():
    snap = snapshot()
    snap["economics"].pop("net_credit_after_entry_fee_btc")
    snap["economics"].pop("net_credit_after_fee_btc")

    p = build_presentation(map_obj(), snap)

    assert p["candidate_identity"]["gross_credit_btc"] == 0.0003
    assert p["candidate_identity"]["net_credit_btc"] is None


def test_real_workbench_schema_uses_frozen_accounting_and_reference_axis():
    m, snap = real_put_map_and_snapshot()
    p = build_presentation(m, snap)
    identity = p["candidate_identity"]

    assert identity["net_credit_btc"] == pytest.approx(snap["economics"]["net_credit_after_entry_fee_btc"])
    assert identity["gross_credit_btc"] == pytest.approx(snap["economics"]["visible_credit_btc"])
    assert identity["entry_fee_btc"] == pytest.approx(snap["economics"]["theoretical_two_leg_fee_btc"])
    assert identity["net_credit_after_fee_btc"] == pytest.approx(snap["economics"]["net_credit_after_fee_btc"])
    assert identity["near_breakeven_usd"] == pytest.approx(83852.21047903072)
    assert identity["reference_time_ms"] == snap["market"]["quote"]["exchange_times_ms"]["short"]

    allocation = row_by_key(p, "allocation")
    alloc_labels = {item["label"]: item for item in allocation["primary_metrics"]}
    assert alloc_labels["3交易日净配置"]["value"] == pytest.approx(
        m["background"]["allocation"]["metrics"]["f3_usd_m"]
    )
    assert alloc_labels["7交易日净配置"]["value"] == pytest.approx(
        m["background"]["allocation"]["metrics"]["f7_usd_m"]
    )
    assert alloc_labels["近期日均"]["value"] == pytest.approx(
        m["background"]["allocation"]["metrics"]["v3_usd_m_per_day"]
    )

    inventory = row_by_key(p, "inventory")
    inv_labels = {item["label"]: item for item in inventory["primary_metrics"]}
    assert inv_labels["短期持有者资本化价格"]["value"] == pytest.approx(73552.76)
    assert "距短期持有者资本化价格" not in inv_labels
    assert "Bitview同源价距短期资本化价格" in inv_labels

    financing = row_by_key(p, "financing")
    fin_labels = {item["label"]: item for item in financing["primary_metrics"]}
    assert fin_labels["币安 BTCUSDT USD-M 已结算Funding"]["value"] == pytest.approx(
        m["background"]["financing"]["metrics"]["funding"]["settled_funding_rate_sum"]
    )
    assert "USDT" in financing["comparison_cn"]
    assert "Binance USD-M BTCUSDT" in financing["source_explanation"]["source_cn"]

    external = row_by_key(p, "external_conditions")
    ext_labels = {item["label"]: item for item in external["primary_metrics"]}
    assert len(ext_labels) == len(external["primary_metrics"])
    assert ext_labels["名义10Y·长窗"]["window_cn"] == "20观察"
    assert ext_labels["名义10Y·长窗"]["value"] == pytest.approx(
        m["background"]["external_conditions"]["metrics"]["nominal_10y"]["change_20obs_bp"]
    )
    assert ext_labels["名义10Y"]["value"] == pytest.approx(
        m["background"]["external_conditions"]["metrics"]["nominal_10y"]["change_5obs_bp"]
    )
    assert ext_labels["实际10Y"]["value"] == pytest.approx(
        m["background"]["external_conditions"]["metrics"]["real_10y"]["change_5obs_bp"]
    )

    public_text = json.dumps(
        {
            "facts": [row["text_cn"] for row in p["fact_summary"]],
            "liability": [row["summary_cn"] for row in p["liability_by_region"]],
        },
        ensure_ascii=False,
    )
    assert "brk-cp-sth" not in public_text
    assert "partial_payout" not in public_text
    assert "beyond_protection" not in public_text
    assert "短期持有者资本化价格" in public_text


def test_stale_audit_values_never_fill_current_primary_metrics_or_source_clock():
    m = map_obj()
    m["background"]["financing"]["metrics"] = {
        "oi_source_values": {"oi_24h_window": {"native_change_pct": 3}},
        "funding_source_values": {"metrics": {"settled_funding_rate_sum": .1}},
    }
    m["background"]["inventory"]["metrics"] = {
        "realized_pnl_source_values": {"metrics": {"loss_14d_usd": 1000000}},
    }
    snap = snapshot()
    p = build_presentation(m, snap)
    assert row_by_key(p, "financing")["primary_metrics"] == []
    assert row_by_key(p, "inventory")["primary_metrics"] == []
    assert p["candidate_identity"]["reference_time_ms"] is None


def test_build_presentation_does_not_mutate_inputs_or_financial_values():
    m = map_obj()
    snap = snapshot()
    before_m = deepcopy(m)
    before_snap = deepcopy(snap)

    p = build_presentation(m, snap)

    assert m == before_m
    assert snap == before_snap
    assert p["candidate_identity"]["net_credit_btc"] == before_snap["economics"]["net_credit_after_entry_fee_btc"]
    assert p["candidate_identity"]["gross_credit_btc"] == before_snap["economics"]["visible_credit_btc"]
    assert p["liability_by_region"][0]["static_net_range"] == before_m["policy_link"]["region_liability_rows"][0]["static_net_range"]


def test_etf_negative_flow_keeps_sign_when_price_source_is_qualified():
    m = map_obj()
    m["background"]["allocation"]["metrics"]["flow_3d_usd_mn"] = -120.0
    p = build_presentation(m, snapshot())
    allocation = row_by_key(p, "allocation")
    metrics = {item["label"]: item for item in allocation["primary_metrics"]}

    assert metrics["3交易日净配置"]["value"] == -120.0
    assert metrics["3交易日净配置名义折合"]["value"] == pytest.approx(-1200.0)
    assert metrics["3交易日净配置名义折合"]["conversion_qualified"] is True


def test_etf_btc_equivalent_rejects_missing_or_future_price_clock():
    m = map_obj()
    m["source_manifest"]["source_records"] = [record for record in m["source_manifest"]["source_records"] if record["record_id"] != "price-1"]
    p = build_presentation(m, snapshot())
    metrics = {item["label"]: item for item in row_by_key(p, "allocation")["primary_metrics"]}
    assert "不能折算" in metrics["3交易日净配置名义折合"]["value"]
    assert metrics["3交易日净配置名义折合"]["conversion_qualified"] is False
    assert p["price_axis"]["current"]["time_ms"] is None
    assert p["price_axis"]["current"]["source_record_id"] is None
    assert "源钟未提供" in p["price_axis"]["current"]["source_cn"]

    future = map_obj()
    price = next(record for record in future["source_manifest"]["source_records"] if record["record_id"] == "price-1")
    price["time"]["observation_end_ms"] = T + 1
    p_future = build_presentation(future, snapshot())
    future_metrics = {item["label"]: item for item in row_by_key(p_future, "allocation")["primary_metrics"]}
    assert "冻结截止" in future_metrics["3交易日净配置名义折合"]["value"]
    assert "cutoff" not in future_metrics["3交易日净配置名义折合"]["value"]
    assert p_future["price_axis"]["current"]["time_ms"] is None


def test_missing_source_list_does_not_claim_source_acquired_or_convert_etf():
    m = map_obj()
    m.pop("source_manifest")
    p = build_presentation(m, snapshot())
    allocation = row_by_key(p, "allocation")
    metrics = {item["label"]: item for item in allocation["primary_metrics"]}

    assert "缺同版来源清单" in allocation["source_explanation"]["qualification_cn"]
    public_text = json.dumps([item for item in p["decision_brief"]["pushes"]] + [p["decision_brief"]["watch"]], ensure_ascii=False)
    assert "source_manifest" not in public_text
    assert "manifest" not in public_text
    assert "freeze" not in public_text
    assert "cutoff" not in public_text
    assert "policy" not in public_text
    assert "不能折算" in metrics["3交易日净配置名义折合"]["value"]


def test_etf_btc_equivalent_rejects_wrong_price_identity_and_clock_order():
    wrong_unit = map_obj()
    price = next(record for record in wrong_unit["source_manifest"]["source_records"] if record["record_id"] == "price-1")
    price["identity"]["unit"] = "USDT"
    p_unit = build_presentation(wrong_unit, snapshot())
    unit_metrics = {item["label"]: item for item in row_by_key(p_unit, "allocation")["primary_metrics"]}
    assert "不能折算" in unit_metrics["3交易日净配置名义折合"]["value"]

    wrong_basis = map_obj()
    price = next(record for record in wrong_basis["source_manifest"]["source_records"] if record["record_id"] == "price-1")
    price["content"]["values"]["price_basis"] = "BINANCE_LAST"
    p_basis = build_presentation(wrong_basis, snapshot())
    basis_metrics = {item["label"]: item for item in row_by_key(p_basis, "allocation")["primary_metrics"]}
    assert "价格基准不一致" in basis_metrics["3交易日净配置名义折合"]["value"]

    missing_record_basis = map_obj()
    price = next(record for record in missing_record_basis["source_manifest"]["source_records"] if record["record_id"] == "price-1")
    price["content"]["values"].pop("price_basis")
    p_missing_record_basis = build_presentation(missing_record_basis, snapshot())
    missing_record_basis_metrics = {item["label"]: item for item in row_by_key(p_missing_record_basis, "allocation")["primary_metrics"]}
    assert "折算价格缺少价系" in missing_record_basis_metrics["3交易日净配置名义折合"]["value"]

    missing_map_basis = map_obj()
    missing_map_basis.pop("price_basis")
    p_missing_map_basis = build_presentation(missing_map_basis, snapshot())
    missing_map_basis_metrics = {item["label"]: item for item in row_by_key(p_missing_map_basis, "allocation")["primary_metrics"]}
    assert "当前价格缺少价系" in missing_map_basis_metrics["3交易日净配置名义折合"]["value"]

    wrong_price = map_obj()
    price = next(record for record in wrong_price["source_manifest"]["source_records"] if record["record_id"] == "price-1")
    price["content"]["values"]["price_usd"] = 99999.0
    p_price = build_presentation(wrong_price, snapshot())
    price_metrics = {item["label"]: item for item in row_by_key(p_price, "allocation")["primary_metrics"]}
    assert "当前冻结价格不一致" in price_metrics["3交易日净配置名义折合"]["value"]

    wrong_clock = map_obj()
    price = next(record for record in wrong_clock["source_manifest"]["source_records"] if record["record_id"] == "price-1")
    price["time"]["first_seen_at_ms"] = price["time"]["observation_end_ms"] - 1
    p_clock = build_presentation(wrong_clock, snapshot())
    clock_metrics = {item["label"]: item for item in row_by_key(p_clock, "allocation")["primary_metrics"]}
    assert "观察时刻早于读取时刻" in clock_metrics["3交易日净配置名义折合"]["value"]
    assert p_clock["price_axis"]["current"]["time_ms"] is None


def test_etf_btc_equivalent_rejects_missing_or_mismatched_freeze_cutoff():
    missing_manifest_cutoff = map_obj()
    missing_manifest_cutoff["source_manifest"].pop("cutoff_at_ms")
    p_missing_manifest = build_presentation(missing_manifest_cutoff, snapshot())
    manifest_metrics = {item["label"]: item for item in row_by_key(p_missing_manifest, "allocation")["primary_metrics"]}
    assert "缺同版冻结截止" in manifest_metrics["3交易日净配置名义折合"]["value"]
    assert p_missing_manifest["price_axis"]["current"]["time_ms"] is None

    missing_map_cutoff = map_obj()
    missing_map_cutoff.pop("cutoff_at_ms")
    p_missing_map = build_presentation(missing_map_cutoff, snapshot())
    map_metrics = {item["label"]: item for item in row_by_key(p_missing_map, "allocation")["primary_metrics"]}
    assert "缺同版冻结截止" in map_metrics["3交易日净配置名义折合"]["value"]
    assert p_missing_map["price_axis"]["current"]["time_ms"] is None

    mismatched = map_obj()
    mismatched["source_manifest"]["cutoff_at_ms"] = T + 1
    p_mismatch = build_presentation(mismatched, snapshot())
    mismatch_metrics = {item["label"]: item for item in row_by_key(p_mismatch, "allocation")["primary_metrics"]}
    assert "缺同版冻结截止" in mismatch_metrics["3交易日净配置名义折合"]["value"]
    assert p_mismatch["price_axis"]["current"]["time_ms"] is None


def test_price_axis_current_does_not_use_short_leg_book_time_as_index_clock():
    snap = snapshot()
    snap["market"]["quote"] = {"exchange_times_ms": {"short": T - 123}}
    m = map_obj()
    m["source_manifest"]["source_records"] = [record for record in m["source_manifest"]["source_records"] if record["record_id"] != "price-1"]

    p = build_presentation(m, snap)

    assert p["candidate_identity"]["reference_time_ms"] == T - 123
    assert p["price_axis"]["current"]["time_ms"] is None
    assert p["price_axis"]["current"]["source_record_id"] is None
    assert "源钟未提供" in p["price_axis"]["current"]["source_cn"]


def test_synthetic_source_records_are_not_described_as_real_sources():
    m = map_obj()
    for source_record in m["source_manifest"]["source_records"]:
        source_record["collector"]["synthetic_fixture"] = True
        source_record["usage_qualification"]["simulation_only"] = True
    p = build_presentation(m, snapshot(), presentation_mode="FIXTURE")
    allocation = row_by_key(p, "allocation")

    assert "合成示例口径" in allocation["source_explanation"]["qualification_cn"]
    assert "真实来源已取得" in allocation["source_explanation"]["qualification_cn"]
    assert p["price_axis"]["current"]["source_cn"] == "本地合成指数"


def test_fmz_and_spacegate_gaps_are_not_rendered_as_generic_window_gap():
    m = map_obj()
    m["map_gaps"] = ["fmz_continuous_fact_missing", "spacegate_unavailable"]
    p = build_presentation(m, snapshot())
    conflict_texts = [item["text_cn"] for item in p["decision_brief"]["conflicts"]]

    assert "FMZ 原生连续事实或空间门缺失。" in conflict_texts
    assert "存在来源资格或共同窗口缺口。" not in conflict_texts


def test_decision_conflicts_keep_late_key_gate_gaps_without_truncation():
    m = map_obj()
    m["map_gaps"] = [f"自定义缺口{i}" for i in range(10)] + [
        "fmz_continuous_fact_missing",
        "gex_effective_flip_unavailable",
        "kpf_cached_record_unavailable",
    ]
    p = build_presentation(m, snapshot())
    conflict_texts = [item["text_cn"] for item in p["decision_brief"]["conflicts"]]

    assert len(conflict_texts) > 8
    assert "FMZ 原生连续事实或空间门缺失。" in conflict_texts
    assert "GEX/Gamma空间结构当前不可用，不能作为观察区来源。" in conflict_texts
    assert "KPF当前没有合格市场区域，不能生成市场观察区。" in conflict_texts


def test_policy_position_distances_are_signed_for_put_and_call():
    p_put = build_presentation(map_obj(), snapshot("put"))
    put_points = {item["label_cn"]: item for item in p_put["decision_brief"]["policy_position"]["checkpoints"]}

    assert put_points["卖出腿"]["distance_usd"] == pytest.approx(-3000.0)
    assert put_points["盈亏平衡价"]["distance_usd"] == pytest.approx(-3320.0)
    assert put_points["保护腿"]["distance_pct"] == pytest.approx(-5.0)

    m_call = map_obj()
    for ref in m_call["reference_pool"]:
        if ref.get("role") == "NEAR_BREAKEVEN":
            ref["raw_price"] = 103320.0
    p_call = build_presentation(m_call, snapshot("call"))
    call_points = {item["label_cn"]: item for item in p_call["decision_brief"]["policy_position"]["checkpoints"]}

    assert call_points["卖出腿"]["distance_usd"] == pytest.approx(3000.0)
    assert call_points["盈亏平衡价"]["distance_usd"] == pytest.approx(3320.0)
    assert call_points["保护腿"]["distance_pct"] == pytest.approx(5.0)


def test_nominal_only_region_does_not_become_watch_region():
    m = map_obj()
    m["policy_link"]["region_liability_rows"] = [m["policy_link"]["region_liability_rows"][1]]
    p = build_presentation(m, snapshot())
    watch = p["decision_brief"]["watch"]

    assert watch["qualified"] is False
    assert watch["status_cn"] == "当前无合格市场观察区"
    assert watch["primary_region_id"] is None
    assert "名义参考" in watch["reason_cn"]


def test_global_map_without_bound_policy_keeps_background_but_no_contract_watch():
    m = map_obj()
    m["policy_link"] = {"candidate_id": None, "candidate_version": None, "region_liability_rows": [], "region_liability_full_rows": []}
    m["reference_pool"] = [ref for ref in m["reference_pool"] if ref.get("family") != "CONTRACT_LIABILITY"]
    p = build_presentation(m, None, presentation_mode="LIVE")
    watch = p["decision_brief"]["watch"]

    assert p["decision_brief"]["pushes"]
    assert watch["qualified"] is False
    assert watch["title_cn"] == "保单未绑定"
    assert "尚未选择或未取得具体保单" in watch["reason_cn"]
    assert "卖出腿" in watch["check_cn"]
