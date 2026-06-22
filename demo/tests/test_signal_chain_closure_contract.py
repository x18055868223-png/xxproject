import importlib.util
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
SIGNAL_FILE = ROOT / "demo" / "最新交付物" / "neutral_regulation_demo_fmz.py"
FRONTEND_APP = ROOT / "deploy" / "signal_audit" / "frontend" / "app.js"


def load_signal_module():
    spec = importlib.util.spec_from_file_location("nrd_signal_chain_closure", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def signal_inputs(mod):
    flow = {
        "direction": mod.DIRECTION_BULLISH,
        "tmv_blend": 0.45,
        "window_conflict": False,
        "tmvf_24h": {
            "tmv_final": 0.42,
        },
        "tmvf_48h": {
            "tmv_final": 0.48,
            "funding": {
                "funding_norm": 0.80,
            },
        },
        "tmvf_funding_effect": "overcrowded",
        "last_funding_rate": 0.00035,
        "micro_flow": {
            "fast_4h": {
                "data_ready": True,
                "cvd_norm": 0.90,
                "cvd_sum": 3200.0,
                "price_return_pct": 0.80,
            },
            "slow_12h": {
                "data_ready": True,
                "cvd_norm": 0.75,
                "cvd_sum": 5100.0,
                "price_return_pct": 0.55,
            },
        },
    }
    macro = {
        "macro_score": 0.0,
        "macro_regime": "Neutral",
        "macro_data_confidence": 1.0,
        "data_status": "full_live",
    }
    nr = {
        "state": "NR_REPAIR_CONFIRMED",
        "is_active": True,
        "event_context": {
            "episode_id": "CHAIN-CLOSURE",
            "episode_direction": "DOWN",
            "peak_m_die": -0.92,
            "event_count_merged": 3,
        },
        "anchor_context": {
            "anchor_score": 74.0,
            "normalized_deviation": -0.28,
        },
    }
    skew = {
        "data_state": "OK",
        "vote": -0.12,
        "vote_confidence": 0.4,
        "rr_blend": -0.03,
        "rr_z": -0.2,
    }
    gamma = {
        "regime": "POSITIVE_GAMMA_PINNING",
        "regime_strength": 0.75,
        "confidence_multiplier": 1.0,
        "veto": False,
        "spatial_vote": 0.25,
        "spatial_weight": 1.0,
        "pin": {
            "pin_strike": 65000,
            "distance_to_pin_pct": 1.2,
            "pin_pull_direction": "UP",
        },
        "flip_point": 63000,
        "call_wall": 66000,
        "put_wall": 61000,
    }
    history = {
        "4h": [0.10, 0.20, 0.30, 0.40, 0.50],
        "12h": [0.10, 0.20, 0.30, 0.40, 0.50],
    }
    return flow, macro, nr, skew, gamma, history


def main():
    mod = load_signal_module()
    config = dict(mod.CONFIG)
    config["edb_cvd_strength_min_history"] = 3
    flow, macro, nr, skew, gamma, history = signal_inputs(mod)

    edb = mod.evaluate_edb(
        flow,
        macro,
        nr,
        skew=skew,
        gamma_regime=gamma,
        cvd_history=history,
        config=config,
    )
    keys = [item["key"] for item in edb["evidence"]]
    assert_true("FLOW_CONFIRM" in keys, "CVD windows should merge into FLOW_CONFIRM")
    assert_true("CVD_4h" not in keys and "CVD_12h" not in keys,
                "legacy CVD window votes should not remain active evidence")
    assert_true("FUNDING" not in keys, "funding direction vote should default off")
    assert_true("GGR_SPATIAL" not in keys, "GGR spatial vote should default off")

    flow_row = next(item for item in edb["evidence"] if item["key"] == "FLOW_CONFIRM")
    assert_true(flow_row["weight"] <= config["edb_base_weights"]["TMV"],
                "FLOW_CONFIRM configured weight should not exceed TMV")
    detail = flow_row["detail"]
    assert_true(detail["schema_name"] == "FlowConfirmPackage",
                "flow detail should be a FlowConfirmPackage")
    assert_true(detail["agreement"] == "ALIGNED", "both CVD windows should align")
    assert_true(detail["combined_weight"] == flow_row["weight"],
                "detail should expose the consumed combined weight")

    factor_snapshot = {
        "edb": edb,
        "flow": flow,
        "macro_pressure": macro,
        "neutral_repair_signal": nr,
        "skew": skew,
        "gamma_regime": gamma,
    }
    card = mod.build_signal_review_card(
        factor_snapshot,
        runtime_facts={"current_price": 64000},
        neutral_repair_signal=nr,
        config=config,
    )
    record = mod.build_audit_record(card, config)
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True)
    assert_true("final_total_confidence" not in encoded,
                "closure schema must not introduce final_total_confidence")
    matrix = record.get("decision_matrix")
    assert_true(isinstance(matrix, dict), "audit record should include SignalDecisionMatrix")
    assert_true(matrix["flow_confirm"] == "ALIGNED",
                "decision matrix should surface flow confirmation state")
    assert_true(matrix["decision_state"] in {
        "BLOCKED", "WAIT_CONFIRMATION", "REVIEW_REQUIRED", "APPROVABLE",
    }, "decision matrix should use the closure state enum")
    assert_true("FLOW_CONFIRM" in record["reasoning"]["participants"],
                "frontend reasoning participants should expose FLOW_CONFIRM")

    app = FRONTEND_APP.read_text(encoding="utf-8")
    assert_true("${renderDecisionMatrix(doc)}" in app,
                "frontend should render the decision matrix")
    assert_true("封板决策矩阵" in app,
                "frontend should label the closure matrix in Chinese")

    print("signal_chain_closure_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_chain_closure_contract: FAIL - " + str(exc))
        sys.exit(1)
