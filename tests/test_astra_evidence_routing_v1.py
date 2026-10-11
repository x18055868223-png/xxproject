from __future__ import annotations

from copy import deepcopy
import inspect
import json
from pathlib import Path

import pytest

from test_astra_underwriting_v20 import T, sample
from tools import astra_evidence_routing_v1 as routing
from tools import astra_underwriting_v20 as core
from tools import astra_wpem_review_evidence_v1 as review_evidence


def _deep_merge(dst, src):
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_merge(dst[key], value)
        else:
            dst[key] = value
    return dst


def _fact_context(overrides=None):
    facts = {
        "schema": "astra_fmz_fact_context@2.0.0",
        "status": "current",
        "underlying": "BTC",
        "source_hash": "a" * 64,
        "snapshot_ts_ms": T,
        "valuation_ts_ms": T,
        "price_observed_ms": T,
        "runtime_mode": "live_public_read_only",
        "current_price_usd": 100000,
        "current_price_source": "synthetic_fixture",
        "source_quality": {"status": "OK"},
        "gap_reasons": [],
        "market_facts": {
            "anchor": {
                "ready": True,
                "effective_flip_point": 98500,
                "gex_source_ts_ms": T - 10_000,
                "gex_freshness": "FRESH",
            },
            "space_gate": {
                "state": "STRUCTURE_ELIGIBLE",
                "observed_at_ms": T - 9_000,
                "net_gamma": {"source_ts_ms": T - 9_000, "valid": True},
            },
            "gamma_regime": {
                "regime": "POSITIVE_GAMMA_PINNING",
                "data_state": "OK",
                "max_gamma_oi_share": 0.18,
            },
            "space_context": {
                "gex_info": {
                    "quality": "OK",
                    "availability": "OK",
                    "fetched_at_ms": T - 8_000,
                    "flip_point": 98500,
                    "support_walls": [97000],
                    "resistance_walls": [103000],
                }
            },
            "volume_price": {
                "direction": "DOWN",
                "tmv_blend": -0.2,
                "tmvf_24h": {"data_state": "OK", "core": {"kline_open_time": T - 86_400_000}},
                "tmvf_48h": {"data_state": "OK", "core": {"kline_open_time": T - 172_800_000}},
                "micro_flow": {
                    "combined": {
                        "direction": "bearish",
                        "data_ready": True,
                        "ready_horizons": ["4h", "12h"],
                        "max_coverage_hours": 12,
                    }
                },
                "funding": {"tmvf_24h": {"data_state": "OK", "window_end_time": T - 3_600_000}},
            },
            "skew": {"data_state": "OK", "greeks_epoch_ms": T - 7_000, "rr_blend": -0.1},
            "macro_pressure": {"data_status": "OK", "last_data_time": T - 60_000, "macro_shock": {"block": False}},
            "neutral_repair": {"is_active": False, "state": "NR_DISPLACEMENT_ACTIVE"},
            "edb": {"edb_score": -0.1},
            "source_trace": {"data_state": "OK"},
        },
    }
    if overrides:
        _deep_merge(facts, deepcopy(overrides))
    return facts


def _btc_map():
    return {
        "schema": "btc_map@1",
        "cutoff_at_ms": T,
        "reference_pool": [
            {
                "reference_id": "kpf-acceptance-zone",
                "family": "TRADED_ACCEPTANCE",
                "role": "KPF_ZONE",
                "source_family": "kpf_light",
                "quality": "OK",
                "coordinate_qualification": "NOMINAL_ONLY",
                "usage_decision": {"usage": "background", "status": "USABLE", "can_use": True},
            },
            {
                "reference_id": "contract_short_leg",
                "family": "CONTRACT_LIABILITY",
                "role": "SHORT_LEG",
                "source_family": "contract",
                "quality": "OK",
                "coordinate_qualification": "COMPARABLE",
                "usage_decision": {"usage": "direct", "status": "USABLE", "can_use": True},
            },
        ],
        "background": {
            "allocation": {"usage_qualification": {"status": "USABLE"}},
            "inventory": {"usage_qualification": {"status": "USABLE"}},
            "financing": {"usage_qualification": {"status": "USABLE"}},
            "external_conditions": {"usage_qualification": {"status": "USABLE"}},
        },
        "source_manifest": {"source_records": [{"record_id": "kpf.fixture"}]},
    }


def _snapshot(side="put", *, risk=True, fact_overrides=None, attach_map=False):
    payload = sample(side, risk=risk)
    payload["fmz_fact_context"] = _fact_context(fact_overrides)
    snap = core.evaluate(payload)
    if attach_map:
        snap["data_identity"]["btc_map"] = _btc_map()
    return snap


def _package(snapshot):
    kwargs = {"include_routing": False} if "include_routing" in inspect.signature(review_evidence.build_review_evidence).parameters else {}
    review_snapshot = deepcopy(snapshot)
    if isinstance(review_snapshot.get("data_identity"), dict):
        review_snapshot["data_identity"].pop("btc_map", None)
    review_snapshot.pop("btc_map", None)
    review_snapshot.pop("map", None)
    return review_evidence.build_review_evidence(review_snapshot, **kwargs)


def _route(snapshot, package=None):
    package = deepcopy(package) if package is not None else _package(snapshot)
    return routing.build_routing(snapshot, package), package


def _unit(route, asset):
    found = [unit for unit in route["units"] if unit["source_asset"] == asset]
    assert found, asset
    return found[0]


def _assets(route):
    return {unit["source_asset"] for unit in route["units"]}


def _binding_matches(route, snapshot):
    binding = route["binding"]
    return (
        binding["candidate_id"] == snapshot["candidate_id"]
        and binding["snapshot_id"] == snapshot["snapshot_id"]
        and binding["snapshot_hash"] == routing._hash(snapshot)
        and binding["decision_asof_ms"] == snapshot["market"]["valuation_ts_ms"]
    )


def _is_selector(ref):
    return "[" in ref and "]" in ref


def _resolve_ref(snapshot, package, ref):
    root = None
    parts = ref.split(".")
    if parts[0] == "snapshot":
        root = snapshot
        parts = parts[1:]
    elif parts[0] == "fact_context":
        root = snapshot.get("fact_context", {})
        parts = parts[1:]
    elif parts[0] in package:
        root = package
    elif parts[0] == "source_bindings":
        return "source_bindings" in package
    else:
        return False
    cur = root
    for part in parts:
        if "*" in part or _is_selector(part):
            return True
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return cur is not None


def test_contract_schema_binding_and_replay_validation():
    snapshot = _snapshot()
    route, package = _route(snapshot)
    schema = json.loads(Path("docs/astra/evidence_routing_20261006/EVIDENCE_ROUTING_SCHEMA.json").read_text(encoding="utf-8"))

    assert route["schema"] == routing.SCHEMA
    assert schema["$id"] == routing.SCHEMA
    assert set(schema["$defs"]["system_role"]["enum"]) == routing.SYSTEM_ROLES
    assert set(route["selection_summary"]) == {"claims", "counters", "gaps", "reassess"}
    assert route["binding"]["snapshot_hash"] == routing._hash(snapshot)
    assert {"candidate_id", "snapshot_id", "snapshot_hash", "decision_asof_ms"}.issubset(route["binding"])
    assert all({"candidate_id", "snapshot_id", "snapshot_hash", "decision_asof_ms"}.issubset(unit["binding"]) for unit in route["units"])
    assert all({"q1_reason_cn", "q2_reason_cn"}.issubset(unit) for unit in route["units"])
    problem_map = {row["domain"]: row for row in route["problem_map"]}
    assert problem_map["PATH_DYNAMICS"]["representative_id"] == _unit(route, "wpem_window_path")["evidence_id"]
    assert problem_map["QUALIFICATION_DATA"]["representative_id"] == _unit(route, "wpem_coverage")["evidence_id"]
    assert problem_map["COMPENSATION"]["representative_id"] == _unit(route, "visible_credit")["evidence_id"]
    assert "窗口" in problem_map["QUALIFICATION_DATA"]["summary_cn"]
    assert "INSUFFICIENT_WINDOW" in problem_map["QUALIFICATION_DATA"]["summary_cn"]

    validated = routing.validate_routing(snapshot, package, route)
    assert validated["status"] == "VALID"

    tampered = deepcopy(route)
    tampered["binding"]["snapshot_id"] = "forged"
    with pytest.raises(routing.RoutingValidationError):
        routing.validate_routing(snapshot, package, tampered)


def test_sealed_and_unsealed_evidence_build_identical_lineage_route():
    snapshot = _snapshot()
    sealed = _package(snapshot)
    unsealed = {k: deepcopy(v) for k, v in sealed.items() if k not in {"evidence_hash", "review_evidence_hash"}}

    assert routing.build_routing(snapshot, sealed) == routing.build_routing(snapshot, unsealed)


def test_all_retained_assets_route_with_map_kpf_nr_and_edb_context():
    snapshot = _snapshot(attach_map=True)
    route, _ = _route(snapshot)

    expected = {
        "current_reference_price",
        "responsibility_distance",
        "visible_credit",
        "fee",
        "risk_reference",
        "anchor_effective_flip",
        "space_gate_gamma_regime",
        "gamma_walls_flip",
        "tmv_price_path",
        "active_flow_cvd",
        "funding_context",
        "srd_skew_aggregate",
        "macro_pressure",
        "neutral_repair_state",
        "edb_legacy_derived_signal",
        "map_reference_pool",
        "kpf_nominal_reference",
        "map_background_allocation",
        "map_background_inventory",
        "map_background_financing",
        "map_background_external_conditions",
        "map_source_manifest",
        "term_structure",
        "iv_atm",
        "rv_realized",
        "dvol",
    }
    assert expected.issubset(_assets(route))
    assert _unit(route, "kpf_nominal_reference")["q1_role"] == "LIMITATION"
    assert _unit(route, "edb_legacy_derived_signal")["information_increment"] == "REDUNDANT"
    assert _unit(route, "edb_legacy_derived_signal")["loop_relations"][0]["loop"] == "OBSERVATION_ONLY"
    assert _unit(route, "funding_context")["information_increment"] == "CONTEXT_ONLY"
    assert _unit(route, "funding_context")["q1_role"] == "NONE"
    assert _unit(route, "funding_context")["q2_role"] == "NONE"
    assert _unit(route, "srd_skew_aggregate")["information_increment"] == "CONTEXT_ONLY"
    assert _unit(route, "srd_skew_aggregate")["q1_role"] == "NONE"
    assert _unit(route, "srd_skew_aggregate")["q2_role"] == "NONE"


def test_r1_option_space_deduplicates_anchor_gamma_walls_into_one_block():
    route, _ = _route(_snapshot())
    block = next(block for block in route["dependency_blocks"] if block["dependency_group"] == "OPTION_SPACE")
    ids = set(block["evidence_ids"])

    assert _unit(route, "anchor_effective_flip")["evidence_id"] in ids
    assert _unit(route, "space_gate_gamma_regime")["evidence_id"] in ids
    assert _unit(route, "gamma_walls_flip")["evidence_id"] in ids
    assert len(block["support_ids"]) <= 2
    assert len(block["counter_ids"]) <= 2
    assert _unit(route, "anchor_effective_flip")["independence_status"] == "SAME_BLOCK"


def test_r2_put_safe_location_and_bearish_flow_are_separate_roles():
    route, _ = _route(_snapshot("put"))
    flow = _unit(route, "active_flow_cvd")
    price = _unit(route, "responsibility_distance")

    assert price["q1_role"] == "PRIMARY"
    assert price["loop_relations"][0]["loop"] == "OBSERVATION_ONLY"
    assert flow["q1_role"] == "COUNTER"
    assert flow["q2_role"] == "COUNTER"
    assert flow["horizon"]["source"] == "INTRADAY"
    assert flow["independence_status"] == "PARTIALLY_DEPENDENT"
    assert route["loop_digest"]["bearish_loop"]["support_blocks"] == ["block.flow"]
    assert "block.price_location" not in route["loop_digest"]["bearish_loop"]["support_blocks"]


def test_r3_neutral_pin_and_bearish_flow_coexist_without_winner():
    route, _ = _route(_snapshot("put"))
    conflicts = route["loop_digest"]["cross_loop_conflicts"]

    assert "block.option_space" in route["loop_digest"]["neutral_stabilizing_loop"]["support_blocks"]
    assert "block.flow" in route["loop_digest"]["bearish_loop"]["support_blocks"]
    assert any(row.get("block_id") == "cross.neutral_bearish_coexist" for row in conflicts)
    assert all("routing" not in row.get("reason_cn", "") for row in conflicts)


def test_r4_wpem_delta_keeps_q1_separate_and_asks_q2_reassessment_without_direction():
    snapshot = _snapshot()
    package = _package(snapshot)
    package["same_candidate_window"]["LevelDeltaPath"]["delta"]["changes"] = {
        "visible_credit_btc_delta": -0.0001,
        "M_ref_btc_delta": 0.00002,
    }
    route, _ = _route(snapshot, package)
    delta = _unit(route, "wpem_delta")

    assert delta["q1_role"] == "NONE"
    # P0: a credit/margin move is neither counter nor support by itself.
    assert delta["q2_role"] == "REASSESS"
    assert delta["information_increment"] == "NOVEL"
    assert "反证" not in delta["reason_cn"]
    assert "可收信用减少0.00010000BTC" in delta["reason_cn"]
    assert "M变化=None" not in delta["reason_cn"]
    assert delta["q1_reason_cn"] is None
    assert any(delta["evidence_id"] in item["evidence_ids"] for item in route["selection_summary"]["reassess"])
    assert not any(delta["evidence_id"] in item["evidence_ids"] for item in route["selection_summary"]["counters"])
    forbidden = {"score", "probability", "prob", "vote", "votes", "vote_count", "confidence"}
    assert not any(key.lower() in forbidden for unit in route["units"] for key in unit)


def test_r5_macro_block_is_cross_loop_constraint_not_bearish_vote():
    route, _ = _route(_snapshot(fact_overrides={"market_facts": {"macro_pressure": {"macro_shock": {"block": True}}}}))
    macro = _unit(route, "macro_pressure")

    assert macro["loop_relations"] == [{"loop": "CROSS_LOOP", "relation": "CONSTRAIN", "mechanism_id": "macro_cross_loop_disturbance"}]
    assert macro["q1_role"] == "LIMITATION"
    assert "block.macro_risk" not in route["loop_digest"]["bearish_loop"]["support_blocks"]


def test_r6_missing_risk_reference_is_q2_limitation_and_does_not_mutate_snapshot():
    snapshot = _snapshot(risk=False)
    route, _ = _route(snapshot)
    risk = _unit(route, "risk_reference")

    assert snapshot["economics"]["mu_ref_btc"] is None
    assert snapshot["economics"]["reference_margin_btc"] is None
    assert risk["q2_role"] == "LIMITATION"
    assert risk["loop_relations"][0]["loop"] == "OBSERVATION_ONLY"


def test_r7_same_endpoint_with_different_path_classification_changes_route_hash():
    snapshot = _snapshot()
    package_a = _package(snapshot)
    package_b = deepcopy(package_a)
    package_a["same_candidate_window"]["LevelDeltaPath"]["path"]["classification"] = "single_direction_adverse_pressure"
    package_b["same_candidate_window"]["LevelDeltaPath"]["path"]["classification"] = "repeated_cross_zone"

    route_a = routing.build_routing(snapshot, package_a)
    route_b = routing.build_routing(snapshot, package_b)

    assert route_a["routing_hash"] != route_b["routing_hash"]
    assert _unit(route_a, "wpem_window_path")["wpem_binding"]["path_classification"] == "single_direction_adverse_pressure"
    assert _unit(route_b, "wpem_window_path")["wpem_binding"]["path_classification"] == "repeated_cross_zone"


def test_r8_binding_rejects_candidate_rotation_without_recomputing_frontend_hash():
    put = _snapshot("put")
    call = _snapshot("call")
    put_route, _ = _route(put)
    call_route, _ = _route(call)

    assert _binding_matches(put_route, put)
    assert not _binding_matches(put_route, call)
    assert _binding_matches(call_route, call)
    assert all(unit["binding"]["snapshot_hash"] == put_route["binding"]["snapshot_hash"] for unit in put_route["units"])


def test_missing_stale_unknown_and_method_break_cases_fail_closed():
    snapshot = _snapshot(fact_overrides={
        "market_facts": {
            "volume_price": {"micro_flow": {"combined": {"data_ready": False, "ready_horizons": ["4h", "12h"]}}},
            "macro_pressure": {"data_status": "STALE"},
            "space_context": {"gex_info": {"quality": "STALE"}},
        }
    })
    package = _package(snapshot)
    package["same_candidate_window"]["source_method_breaks"]["method_break_observed"] = True
    package["same_candidate_window"]["source_method_breaks"]["candidate_rotation_observed"] = True
    route, _ = _route(snapshot, package)

    assert "active_flow_cvd" not in _assets(route)
    assert _unit(route, "active_flow_cvd_unqualified")["q1_role"] == "LIMITATION"
    assert _unit(route, "macro_pressure")["loop_relations"][0]["loop"] == "OBSERVATION_ONLY"
    assert _unit(route, "gamma_walls_flip")["q1_role"] == "LIMITATION"
    assert _unit(route, "wpem_method_break")["q1_role"] == "REASSESS"
    assert _unit(route, "wpem_candidate_rotation")["q2_role"] == "REASSESS"
    assert len(route["selection_summary"]["gaps"]) <= 6
    assert {"term_structure", "iv_atm", "rv_realized", "dvol"}.issubset(_assets(route))


def test_supporting_source_refs_are_real_paths_or_declared_selectors():
    snapshot = _snapshot(attach_map=True)
    route, package = _route(snapshot)

    for unit in route["units"]:
        if unit["information_increment"] == "MISSING" or _is_selector(unit["source_ref"]):
            continue
        assert _resolve_ref(snapshot, package, unit["source_ref"]), unit["source_ref"]
    flow = _unit(route, "active_flow_cvd")
    assert flow["asof_basis"] == "PRODUCER_SNAPSHOT"
    assert flow["source_observed_ms"] is None
    assert flow["source_clock_status"] == "NOT_RETAINED"
    assert _unit(route, "tmv_price_path")["source_observed_ms"] == T - 86_400_000
    assert _unit(route, "tmv_price_path")["q1_role"] == "NONE"


def test_anchor_missing_raw_clock_exposes_producer_basis_without_inventing_clock():
    snapshot = _snapshot(fact_overrides={'market_facts': {'anchor': {'gex_source_ts_ms': None}}})
    route, _ = _route(snapshot)
    anchor = _unit(route, 'anchor_effective_flip')
    assert anchor['asof_basis'] == 'PRODUCER_SNAPSHOT'
    assert anchor['source_observed_ms'] is None
    assert anchor['source_clock_status'] == 'NOT_RETAINED'
    assert anchor['asof'] == snapshot['fact_context'].get('snapshot_ts_ms')
