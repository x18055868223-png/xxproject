import importlib.util
import json
import pathlib
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[2]
SIGNAL_FILE = ROOT / "demo" / "最新交付物" / "neutral_regulation_demo_fmz.py"


def load_signal_module():
    spec = importlib.util.spec_from_file_location("nrd_signal", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def component(key, scoring_bps, pressure=None):
    if pressure is None:
        pressure = scoring_bps / 100.0
    return {
        "key": key,
        "source_status": "live",
        "scoring_bps": scoring_bps,
        "normalized_pressure": pressure,
        "component_score": 0.0,
    }


def macro(score, regime, volq, dxy=0.0, us10y=0.0, status="full_live"):
    return {
        "macro_score": score,
        "macro_regime": regime,
        "data_status": status,
        "macro_data_confidence": 1.0 if status != "unavailable" else 0.0,
        "components": [
            component("VOLQ", volq, volq / 1000.0),
            component("DXY", dxy, dxy / 100.0),
            component("US10Y", us10y, us10y / 100.0),
        ],
    }


def ggr(veto=False):
    return {
        "veto": veto,
        "confidence_multiplier": 1.0,
        "regime": "NEGATIVE_GAMMA" if veto else "POSITIVE_GAMMA_PINNING",
        "spatial_vote": 0.0,
        "spatial_weight": 0.0,
    }


def neutral_repair(active=True):
    return {"is_active": active, "state": "ACTIVE" if active else "IDLE"}


def flow():
    return {
        "direction": "Bearish",
        "tmv_blend": -0.38,
        "tmvf_24h": {"final": -0.32, "tmv_final": -0.32},
        "tmvf_48h": {"final": -0.44, "tmv_final": -0.44},
        "window_conflict": False,
        "last_funding_rate": 0.00001,
        "funding_norm": 0.02,
        "funding_state": "normal",
    }


def main():
    mod = load_signal_module()
    config = dict(mod.CONFIG)
    config["macro_dual_axis_shadow"] = True
    config["macro_volq_shock_delta_bps"] = 150
    config["macro_volq_single_factor_blocking"] = False

    previous_headwind = macro(0.52, "Headwind", 580, dxy=24, us10y=26)
    current_headwind = macro(0.54, "Headwind", 600, dxy=25, us10y=27)
    stable_shock = mod.evaluate_macro_shock(current_headwind, previous_headwind, config)
    assert_true(stable_shock["block"] is False,
                "stable high macro headwind should not be a hard shock block")
    assert_true(stable_shock["state"] == "CLEAR",
                "stable high macro headwind should clear the shock gate")
    current_headwind["macro_shock"] = stable_shock
    current_headwind["blocking_flags"] = mod.macro_blocking_flags(
        current_headwind["components"], current_headwind["macro_score"],
        config, macro_shock=stable_shock)
    verdict = mod.evaluate_macro_verdict(current_headwind, config)
    assert_true(verdict["verdict"] == "MACRO_ADVERSE",
                "stable headwind should stay directional adverse, not MACRO_BLOCKING")
    assert_true("MACRO_HEADWIND_BLOCK" not in current_headwind["blocking_flags"],
                "macro_score >= 0.46 alone must not create MACRO_HEADWIND_BLOCK")

    with tempfile.TemporaryDirectory() as temp_dir:
        cache_file = pathlib.Path(temp_dir) / "macro_cache.json"
        cache_file.write_text(json.dumps({
            "components": {},
            "nrd_macro_previous_snapshot_v1": {
                "ts_ms": 123,
                "macro_score": 0.52,
                "macro_regime": "Headwind",
                "volq_bps": 580,
            },
        }), encoding="utf-8")
        cache_config = dict(config)
        cache_config["macro_cache_file"] = str(cache_file)
        factor = mod.MacroPressureFactor(http_client=None, config=cache_config)
        assert_true(factor.last_snapshot["macro_score"] == 0.52,
                    "macro previous snapshot should load from nrd_macro_previous_snapshot_v1")
        persisted_shock = mod.evaluate_macro_shock(
            current_headwind, factor.last_snapshot, cache_config)
        assert_true(persisted_shock["state"] == "CLEAR",
                    "persisted previous snapshot should prevent repeated bootstrap shock")
        factor._write_previous_snapshot(current_headwind)
        updated = json.loads(cache_file.read_text(encoding="utf-8"))
        stored = updated["nrd_macro_previous_snapshot_v1"]
        assert_true(stored["macro_score"] == 0.54 and stored["volq_bps"] == 600.0,
                    "macro previous snapshot should persist only the minimum summary")

    previous_neutral = macro(0.0309, "Neutral", 150.5, dxy=7.6, us10y=-1.8)
    current_shock = macro(0.4588, "Mild Headwind", 592.9, dxy=14.7, us10y=22.0)
    shock = mod.evaluate_macro_shock(current_shock, previous_neutral, config)
    assert_true(shock["block"] is True,
                "VOLQ jump plus rate/dollar confirmation should trigger shock blocking")
    assert_true(shock["state"] == "BLOCK",
                "confirmed macro shock should expose BLOCK state")
    assert_true("MACRO_SHOCK_BLOCKING" in shock["reason_codes"],
                "confirmed macro shock should expose MACRO_SHOCK_BLOCKING reason")
    current_shock["macro_shock"] = shock
    current_shock["blocking_flags"] = mod.macro_blocking_flags(
        current_shock["components"], current_shock["macro_score"],
        config, macro_shock=shock)
    shock_verdict = mod.evaluate_macro_verdict(current_shock, config)
    assert_true(shock_verdict["verdict"] == "MACRO_SHOCK_BLOCKING",
                "confirmed shock should be the only macro hard veto verdict")

    unconfirmed = mod.evaluate_macro_shock(
        macro(0.42, "Mild Headwind", 593, dxy=5, us10y=6),
        previous_neutral,
        config)
    assert_true(unconfirmed["block"] is False,
                "VOLQ jump without DXY/US10Y confirmation should stay audit watch")
    assert_true(unconfirmed["state"] == "WATCH",
                "unconfirmed VOLQ shock should render WATCH")
    assert_true("VOLQ_SHOCK_UNCONFIRMED" in unconfirmed["reason_codes"],
                "unconfirmed VOLQ shock should be explicit for audit")

    bootstrap = mod.evaluate_macro_shock(
        macro(0.54, "Headwind", 610, dxy=25, us10y=4),
        None,
        config)
    assert_true(bootstrap["block"] is True,
                "missing previous snapshot should conservatively block one confirmed shock cycle")
    assert_true("MACRO_SHOCK_BOOTSTRAP_UNCERTAIN" in bootstrap["reason_codes"],
                "bootstrap block should be distinguishable from ordinary delta shock")

    edb_macro_shock = dict(current_shock)
    edb_macro_shock["macro_shock"] = shock
    edb_macro_shock["blocking_flags"] = ["MACRO_SHOCK_BLOCKING"]
    edb = mod.evaluate_edb(flow(), edb_macro_shock, neutral_repair(),
                           gamma_regime=ggr(False), config=config)
    assert_true(edb["veto_reason"] == "MACRO_SHOCK_BLOCKING",
                "EDB should use the new macro shock veto reason")
    assert_true(edb["lean_pre_gate"] != "NEUTRAL",
                "shock veto should preserve pre-gate direction for audit")
    assert_true(edb["support_label"] == "NO_TRADE_BLOCKED",
                "shock veto should still block downstream support")
    assert_true(edb["confidence_decomposition"]["veto_applied"] is True,
                "macro shock should remain an explicit veto in confidence decomposition")
    conclusion = mod._build_conclusion(edb)
    assert_true(conclusion["lean_pre_gate"] == edb["lean_pre_gate"],
                "signal review conclusion should expose pre-gate lean for audit")
    audit = mod.build_audit_record({
        "card_id": "MACRO-DUAL-AXIS",
        "confirmed_time": 1781770200000,
        "episode_id": "MACRO-DUAL-AXIS",
        "price": 100000.0,
        "conclusion": conclusion,
        "window": {"session_context": mod.classify_signal_session_context(1781770200000, config)},
        "reasoning": {"confidence_decomposition": edb["confidence_decomposition"], "evidence": edb["evidence"]},
        "conflict": {"ratio": 0.2, "level": "LOW"},
        "blocking": {"hard_veto": {"reason_code": "MACRO_SHOCK_BLOCKING"}},
        "factor_cross_section": {"macro_pressure": edb_macro_shock},
        "final_conclusion_cn": "fixture",
    }, config)
    assert_true(audit["decision"]["lean_pre_gate"] == edb["lean_pre_gate"],
                "audit record decision should preserve pre-gate lean")

    edb_ggr = mod.evaluate_edb(flow(), edb_macro_shock, neutral_repair(),
                               gamma_regime=ggr(True), config=config)
    assert_true(edb_ggr["veto_reason"] == "GGR_NEGATIVE_GAMMA_VETO",
                "GGR hard veto should remain independent and not be overwritten by macro")

    unavailable = macro(None, None, 0.0, status="unavailable")
    unavailable["macro_shock"] = mod.evaluate_macro_shock(unavailable, previous_neutral, config)
    unavailable_verdict = mod.evaluate_macro_verdict(unavailable, config)
    unavailable_vote = mod._macro_vote(unavailable_verdict, config)
    assert_true(unavailable_verdict["verdict"] == "MACRO_UNAVAILABLE",
                "unavailable macro should keep unavailable verdict")
    assert_true(unavailable_vote["weight"] == 0.0,
                "unavailable macro should not vote")

    print("signal_macro_dual_axis_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_macro_dual_axis_contract: FAIL - " + str(exc))
        sys.exit(1)
