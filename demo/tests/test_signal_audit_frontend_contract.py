import importlib.util
import json
import pathlib
import sys


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

    assert_true(config["demo_version"] == "1.5.6",
                "FMZ signal deliverable version should match r3.3.10 producer")
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
    durability = record.get("signal_durability")
    assert_true(isinstance(durability, dict), "signal durability layer")
    assert_true(durability["audit_scope"] == "AUDIT_ONLY",
                "durability layer is audit-only")
    assert_true(durability["score_semantics"]
                == "STRUCTURE_HEALTH_INDEX_NOT_PROBABILITY",
                "durability score semantics")
    assert_true(durability["policy"]["not_direction_factor"] is True,
                "durability not direction factor")
    assert_true(durability["policy"]["not_execution_gate"] is True,
                "durability not execution gate")
    assert_true(durability["policy"]["not_confidence_multiplier"] is True,
                "durability not confidence multiplier")
    assert_true(record.get("comfort_window") == durability["comfort_window"],
                "comfort alias mirrors canonical durability object")
    assert_true(record.get("price_anchor_durability")
                == durability["price_anchor_durability"],
                "price anchor alias mirrors canonical durability object")
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

    assert_true("dissent_keys" in record["conflict"], "conflict dissent keys")
    assert_true("dominant_conflict" in record["conflict"], "dominant conflict")
    assert_true(record["delivery"]["fmz_push_summary"] == brief,
                "delivery push summary matches renderer")
    assert_true(len(brief) <= 140, "brief target length <= 140")
    assert_true("\n" not in brief and "\r" not in brief, "brief single line")
    assert_true("耐" in brief and any(token in brief for token in (
                "/DUR ", "/WEAK ", "/BRK ", "/GAP ")),
                "brief contains compact durability token")
    long_config = dict(config)
    long_config["audit_static_base_url"] = "https://example.com/" + ("x" * 120)
    long_brief = mod.render_push_brief(card, long_config)
    assert_true(len(long_brief) <= 140,
                "long-url fallback brief target length <= 140")
    assert_true("\n" not in long_brief and "\r" not in long_brief,
                "long-url fallback brief single line")
    assert_true("耐" in long_brief and any(token in long_brief for token in (
                "/DUR ", "/WEAK ", "/BRK ", "/GAP ")),
                "fallback brief keeps durability token")

    for path in (
        "market_context.price",
        "signal_durability.price_anchor_durability.state",
        "signal_durability.price_anchor_durability.durability_state",
        "comfort_window.window_code",
        "price_anchor_durability.score",
        "price_anchor_durability.durability_score",
        "factor_cross_section.gamma_regime.regime",
        "factor_cross_section.gex_info.market_state",
        "integrity.record_hash",
        "integrity.redaction.contains_secret",
    ):
        assert_true(get_path(record, path) is not None, "required frontend path " + path)

    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True)
    assert_true("render_review_card_push" not in encoded,
                "old renderer must not leak into records")
    print("signal_audit_frontend_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_audit_frontend_contract: FAIL - " + str(exc))
        sys.exit(1)
