from __future__ import annotations

import copy

from tools import astra_workbench_preview_v20 as preview

NOW = 1_790_323_200_000

W5_ALLOWED_MARKET_FACT_DIFFS = {
    "edb_reasoning.ggr_gate.multiplier",
    "edb_reasoning.ggr_gate.reason_codes.0",
    "edb_reasoning.ggr_gate.regime",
    "edb_reasoning.ggr_gate.veto",
    "edb_reasoning.reason_codes.0",
    "gamma_regime.data_state",
    "gamma_regime.gex_info_market_state",
    "gamma_regime.net_gamma_notional",
    "gamma_regime.net_gex_sign",
    "gamma_regime.reason_codes.0",
    "gamma_regime.regime",
    "gamma_regime.veto",
    "gex_info.market_state",
    "gex_info.total_net_gex",
    "space_context.gamma_regime.data_state",
    "space_context.gamma_regime.gex_info_market_state",
    "space_context.gamma_regime.net_gamma_notional",
    "space_context.gamma_regime.net_gex_sign",
    "space_context.gamma_regime.reason_codes.0",
    "space_context.gamma_regime.regime",
    "space_context.gamma_regime.veto",
    "space_context.gex_info.market_state",
    "space_context.gex_info.total_net_gex",
    "space_context.space_gate_net_gamma.ok",
    "space_context.space_gate_net_gamma.sign",
    "space_context.space_gate_net_gamma.value",
    "space_context.space_gate_reasons.0",
    "space_context.space_gate_state",
    "space_gate.eligible",
    "space_gate.fmz_fact_status",
    "space_gate.gap_reasons.0",
    "space_gate.net_gamma.ok",
    "space_gate.net_gamma.sign",
    "space_gate.net_gamma.value",
    "space_gate.reason_codes.0",
    "space_gate.source",
    "space_gate.state",
}


def _flatten(value, prefix=""):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            out.update(_flatten(item, child))
        return out
    if isinstance(value, list):
        out = {}
        for index, item in enumerate(value):
            out.update(_flatten(item, f"{prefix}.{index}"))
        return out
    return {prefix: value}


def _allowed_w5_market_fact_diff(path: str) -> bool:
    return path in W5_ALLOWED_MARKET_FACT_DIFFS


def _market_fact_diffs(left: dict, right: dict) -> dict[str, tuple[object, object]]:
    left_flat = _flatten(left)
    right_flat = _flatten(right)
    return {
        path: (left_flat.get(path), right_flat.get(path))
        for path in sorted(set(left_flat) | set(right_flat))
        if left_flat.get(path) != right_flat.get(path)
    }


def test_round2_etf_totals_windows_and_daily_averages_are_consistent():
    for scenario_id in preview.SCENARIOS:
        background, _, _ = preview._map_fixture(NOW, scenario_id)
        allocation = background["allocation"]
        metrics = allocation["metrics"]

        assert metrics["f3_usd_m"] == 210
        assert metrics["f7_usd_m"] == 560
        assert metrics["v3_usd_m_per_day"] == metrics["f3_usd_m"] / 3 == 70
        assert metrics["v7_usd_m_per_day"] == metrics["f7_usd_m"] / 7 == 80
        assert "3日合计210" in allocation["summary_cn"]
        assert "7日合计560" in allocation["summary_cn"]
        assert "日均70/80" in allocation["summary_cn"]


def test_round2_w4_down_flow_updates_edb_and_micro_evidence_once():
    facts = preview._context_for_scenario(NOW, "W4")["market_facts"]
    edb = facts["edb_reasoning"]
    volume = facts["volume_price"]

    assert facts["flow"]["direction"] == "DOWN"
    assert facts["flow"]["tmv_blend"] < 0
    assert volume["direction"] == "DOWN"
    assert volume["micro_flow"]["combined"]["direction"] == "bearish"
    assert volume["tmvf_24h"]["core"]["trend_direction"] == -1
    assert volume["tmvf_48h"]["core"]["trend_direction"] == -1
    assert edb["lean"] == "MILD_BEARISH"
    assert edb["edb_score"] < 0
    assert edb["evidence"][0]["detail"] == {"direction": "DOWN", "tmv_blend": volume["tmv_blend"]}
    assert edb["evidence"][0]["vote"] < 0
    assert "冲突" in edb["summary_cn"]
    assert "宏观" in edb["summary_cn"]


def test_round2_w5_only_differs_from_w2_by_gamma_gate_and_ggr_fields():
    w2 = preview._context_for_scenario(NOW, "W2")
    w5 = preview._context_for_scenario(NOW, "W5")

    assert w2["scenario_id"] == "W2"
    assert w5["scenario_id"] == "W5"
    assert w2["market_facts"]["flow"] == w5["market_facts"]["flow"]
    assert w2["market_facts"]["edb_reasoning"]["lean"] == w5["market_facts"]["edb_reasoning"]["lean"]
    assert w2["market_facts"]["edb_reasoning"]["conflict_level"] == w5["market_facts"]["edb_reasoning"]["conflict_level"]

    changed = _market_fact_diffs(w2["market_facts"], w5["market_facts"])
    disallowed = {path: pair for path, pair in changed.items() if not _allowed_w5_market_fact_diff(path)}

    assert not disallowed
    assert set(changed) == W5_ALLOWED_MARKET_FACT_DIFFS
    assert w5["market_facts"]["gamma_regime"]["net_gex_sign"] == "NEGATIVE"
    assert w5["market_facts"]["space_gate"]["state"] == "STRUCTURE_BLOCKED"
    assert w5["market_facts"]["edb_reasoning"]["ggr_gate"]["veto"] is True
    assert "negative_net_gamma_main_gate_closed" in w5["market_facts"]["space_gate"]["reason_codes"]
    # Gamma isolation also holds for the candidate's quote, fee and mu account.
    for side in ('put', 'call'):
        left = preview._payload(side, NOW, 'W2')
        right = preview._payload(side, NOW, 'W5')
        for field in ('short_book', 'long_book'):
            assert left['market'][field] == right['market'][field]
        assert left['fee'] == right['fee']
        assert left['risk']['mu_btc'] == right['risk']['mu_btc']


def test_round2_w5_diff_allowlist_rejects_non_gamma_market_fact_drift():
    w2_facts = preview._context_for_scenario(NOW, "W2")["market_facts"]
    mutated = copy.deepcopy(preview._context_for_scenario(NOW, "W5")["market_facts"])

    mutated["space_context"]["gex_info"]["asset"] = "ETH"
    mutated["space_context"]["gex_info"]["quality"] = "BAD"
    mutated["space_context"]["gex_info"]["stale"] = True
    mutated["space_context"]["gex_info"]["fetched_at_ms"] += 1
    mutated["space_context"]["anchor"]["effective_flip_point"] += 100
    mutated["space_context"]["gex_info"]["support_walls"][0] += 100
    mutated["source_trace"]["unexpected_round2_field"] = "must_not_pass_allowlist"

    changed = _market_fact_diffs(w2_facts, mutated)
    disallowed = {path for path in changed if not _allowed_w5_market_fact_diff(path)}

    assert "space_context.gex_info.asset" in disallowed
    assert "space_context.gex_info.quality" in disallowed
    assert "space_context.gex_info.stale" in disallowed
    assert "space_context.gex_info.fetched_at_ms" in disallowed
    assert "space_context.anchor.effective_flip_point" in disallowed
    assert "space_context.gex_info.support_walls.0" in disallowed
    assert "source_trace.unexpected_round2_field" in disallowed
