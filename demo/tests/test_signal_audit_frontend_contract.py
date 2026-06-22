import importlib.util
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEPLOY_FRONTEND = ROOT / "deploy" / "signal_audit" / "frontend"
SIGNAL_FILE = ROOT / "demo" / "最新交付物" / "neutral_regulation_demo_fmz.py"


def load_signal_module():
    spec = importlib.util.spec_from_file_location("nrd_signal", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def get_path(obj, path):
    current = obj
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def main():
    mod = load_signal_module()
    config = dict(mod.CONFIG)
    card = mod.build_sample_review_card(config)
    record = mod.build_audit_record(card, config)
    brief = mod.render_push_brief(card, config)

    assert_true(record["schema"]["name"] == "signal_review_card", "schema name")
    assert_true(record["schema"]["version"] == "1.0.0", "schema version")
    assert_true(record["schema"].get("status") == "FINAL", "schema status")
    assert_true(record["schema"].get("frontend_profile") == "signal_audit_static_v1",
                "frontend profile")
    assert_true(record["identity"]["short_id"], "short id")
    assert_true(record["identity"]["event_type"] == "NR_REPAIR_CONFIRMED",
                "event type")
    assert_true(record["identity"]["is_synthetic"] is False,
                "runtime record must not be synthetic")
    assert_true("provenance" in record, "provenance section")
    assert_true("sources" in record["quality"], "quality sources")
    assert_true(isinstance(record["quality"]["sources"], dict), "quality sources map")
    assert_true("price" in record["quality"]["sources"], "price source status")
    for name, source in record["quality"]["sources"].items():
        assert_true(source.get("source_ref"), name + " source ref")
        if source.get("status") == "OK":
            assert_true(source.get("observed_at"),
                        name + " OK source observed_at")
            assert_true(source.get("age_ms") is not None,
                        name + " OK source age_ms")
    assert_true(record["decision"]["confidence_semantics"]
                == "EVIDENCE_QUALITY_NOT_WIN_RATE", "confidence semantics")
    session_context = record["signal_window"].get("session_context")
    assert_true(isinstance(session_context, dict), "session context")
    assert_true(session_context["validation_basis"]["research_grade"]
                == "MARKET_PRIOR_VALIDATED", "session validation grade")
    assert_true(session_context["affects_confidence"] is False,
                "session context must not change confidence")
    assert_true(record["decision_matrix"]["temporal_durability"]
                == session_context["premise_durability"],
                "matrix temporal durability should mirror session context")
    assert_true("directional_bias" in record["decision"], "directional bias")
    assert_true("evidence_strength" in record["decision"], "evidence strength")
    assert_true("headline" in record["display_layers"], "display headline")
    assert_true(isinstance(record["display_layers"]["operator_focus"], list),
                "operator focus list")
    assert_true(isinstance(record["blocking"]["soft_gates"], list),
                "soft gates list for app.js")
    assert_true(isinstance(record["blocking"]["unblock_conditions"], list),
                "unblock condition list for app.js")
    assert_true(isinstance(record["reasoning"]["agreement"], dict),
                "agreement object")
    assert_true(isinstance(record["reasoning"]["coverage"], dict),
                "coverage object")
    assert_true(record["reasoning"]["score"].get("weighted_vote_sum") is not None,
                "weighted vote sum")

    evidence = record["reasoning"]["evidence"]
    assert_true(len(evidence) >= 6, "full evidence ledger includes active/excluded/gate rows")
    for row in evidence:
        for key in ("key", "participation_status", "configured_weight",
                    "reliability", "information", "effective_weight",
                    "weighted_contribution", "absolute_share_pct", "source_ref"):
            assert_true(key in row, "evidence missing " + key)
    by_key = {str(row["key"]).upper(): row for row in evidence}
    funding = by_key.get("FUNDING")
    assert_true(isinstance(funding, dict), "funding evidence row should exist")
    assert_true(funding.get("participation_status") == "NON_VOTING",
                "funding should remain a non-voting crowding modifier")
    assert_true(funding.get("auxiliary_role") == "FUTURES_FUNDING_CROWDING",
                "funding row should expose its futures-side auxiliary role")
    assert_true(funding.get("auxiliary_lean") in ("BULLISH", "BEARISH", "NEUTRAL"),
                "funding row should expose directional crowding tendency")
    assert_true(get_path(funding, "raw_values.last_rate") is not None,
                "funding row should list raw last funding rate")
    assert_true(get_path(funding, "raw_values.effect") is not None,
                "funding row should list funding effect")

    srd = by_key.get("SRD")
    assert_true(isinstance(srd, dict), "SRD evidence row should exist")
    assert_true(srd.get("auxiliary_role") == "OPTION_SKEW_DIRECTION",
                "SRD row should expose option-skew auxiliary role")
    assert_true(get_path(srd, "raw_values.rr_blend") is not None,
                "SRD row should list risk-reversal blend")
    assert_true(get_path(srd, "raw_values.delta_rr") is not None,
                "SRD row should list delta risk reversal")

    flow_confirm = by_key.get("FLOW_CONFIRM")
    assert_true(isinstance(flow_confirm, dict),
                "flow confirm evidence row should exist")
    assert_true(get_path(flow_confirm, "raw_values.combined_vote") is not None,
                "flow confirm row should preserve its merged raw vote")

    ggr = by_key.get("GGR_SPATIAL")
    assert_true(isinstance(ggr, dict), "GGR spatial evidence row should exist")
    assert_true(ggr.get("participation_status") == "GATE_ONLY",
                "GGR spatial row should remain gate-only by default")
    assert_true(ggr.get("auxiliary_role") == "OPTION_GAMMA_STRUCTURE",
                "GGR row should expose option gamma structure role")
    assert_true(get_path(ggr, "raw_values.regime") is not None,
                "GGR row should list gamma regime")
    assert_true(get_path(ggr, "raw_values.confidence_multiplier") is not None,
                "GGR row should list confidence multiplier")

    assert_true("dissent_keys" in record["conflict"], "conflict dissent keys")
    assert_true("dominant_conflict" in record["conflict"], "dominant conflict")
    assert_true(record["delivery"]["fmz_push_summary"] == brief,
                "delivery push summary matches renderer")
    assert_true(len(brief) <= 140, "brief target length <= 140")
    assert_true("\n" not in brief and "\r" not in brief, "brief single line")

    for path in (
        "market_context.price",
        "factor_cross_section.gamma_regime.regime",
        "factor_cross_section.gex_info.market_state",
        "integrity.record_hash",
        "integrity.redaction.contains_secret",
    ):
        assert_true(get_path(record, path) is not None, "required frontend path " + path)

    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True)
    assert_true("render_review_card_push" not in encoded,
                "old renderer must not leak into records")
    app = (DEPLOY_FRONTEND / "app.js").read_text(encoding="utf-8")
    assert_true("function evidenceRawValues" in app,
                "frontend should render raw evidence values inside the ledger")
    assert_true("function evidenceAuxiliaryLean" in app,
                "frontend should derive auxiliary evidence tendency for old cards")
    for marker in ("Raw values", "Aux tendency", "FUTURES_FUNDING_CROWDING",
                   "OPTION_SKEW_DIRECTION", "OPTION_GAMMA_STRUCTURE"):
        assert_true(marker in app, "frontend evidence ledger missing " + marker)
    print("signal_audit_frontend_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_audit_frontend_contract: FAIL - " + str(exc))
        sys.exit(1)
