import importlib.util
import json
import pathlib
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def test_funding_threshold_matrix(semantics):
    cases = (
        (0.00003, "TEMPERATE_LONG_FUNDING", False, "NOISE"),
        (-0.000004, "TEMPERATE_SHORT_FUNDING", False, "NOISE"),
        (0.0001, "TEMPERATE_LONG_FUNDING", False, "NOISE"),
        (-0.0001, "TEMPERATE_SHORT_FUNDING", False, "NOISE"),
        (0.0001001, "CROWDED_LONG_FUNDING", True, "CANDIDATE"),
        (-0.0001001, "CROWDED_SHORT_FUNDING", True, "CANDIDATE"),
    )
    for raw, code, crowded, reflexivity in cases:
        fact = semantics.build_funding_semantics(
            raw,
            funding_norm=1.0,
            effect="extreme_overcrowded",
            funding_state="crowded_short",
            compat_backfill_applied=False,
        )
        assert_true(fact["semantic_code"] == code, f"{raw} semantic code")
        assert_true(fact["is_crowded"] is crowded, f"{raw} crowded")
        assert_true(
            fact["reflexivity_importance"] == reflexivity,
            f"{raw} reflexivity",
        )
        assert_true(
            semantics.validate_funding_semantics(fact),
            f"{raw} contract completeness",
        )

    missing = semantics.build_funding_semantics(
        None, funding_norm=1.0, effect="extreme_overcrowded")
    assert_true(missing["semantic_code"] == "UNABLE_TO_JUDGE", "missing raw")
    assert_true(missing["edb_vote_allowed"] is False, "missing raw non-voting")
    assert_true(
        semantics.validate_funding_semantics(missing),
        "missing raw contract must fail closed but remain structurally valid",
    )

    corrupted = dict(semantics.build_funding_semantics(0.0001001))
    corrupted["is_crowded"] = False
    assert_true(
        not semantics.validate_funding_semantics(corrupted),
        "crowded raw value with non-crowded semantics must be rejected",
    )


def test_producer_and_shared_funding_semantics_match(semantics, producer):
    cases = (None, 0.0, 0.00003, -0.000004, 0.0001, -0.0001,
             0.0001001, -0.0001001)
    fields = (
        "schema_name", "schema_version", "raw_funding_rate",
        "raw_funding_rate_pct", "crowding_threshold_abs",
        "crowding_threshold_pct", "semantic_code", "raw_available",
        "fee_bias", "fee_bias_cn", "crowding_state", "is_crowded",
        "reflexivity_state", "reflexivity_importance",
        "edb_participation", "edb_vote_allowed", "canonical_text_cn",
    )
    for raw in cases:
        producer_fact = producer.build_funding_canonical_semantics(
            raw, funding_norm=1.0, effect="extreme_overcrowded",
            funding_state="crowded_short")
        shared_fact = semantics.build_funding_semantics(
            raw, funding_norm=1.0, effect="extreme_overcrowded",
            funding_state="crowded_short")
        for field in fields:
            assert_true(
                producer_fact[field] == shared_fact[field],
                f"producer/shared Funding mismatch at {raw}: {field}",
            )


def legacy_card():
    return {
        "identity": {
            "card_id": "canonical-fact-semantics-test",
            "symbol": "BTC",
            "confirmed_at": "2026-07-23T19:36:12+08:00",
            "is_synthetic": False,
        },
        "market_context": {"price": 65609.02, "quote_currency": "USDT"},
        "decision": {
            "lean": "NEUTRAL",
            "support_label": "WAIT_CONFIRMATION",
            "confidence": 39,
            "trade_allowed": False,
        },
        "decision_matrix": {"execution_allowed": False},
        "reasoning": {"evidence": [{"key": "FUNDING", "vote": 0.2}]},
        "conflict": {"ratio": 0.342, "level": "MILD"},
        "quality": {"overall": "OK"},
        "factor_cross_section": {
            "funding": {
                "last_rate": -0.000004,
                "funding_norm": -1.0,
                "effect": "extreme_overcrowded",
            },
            "gex_info": {
                "rank": {
                    "window": {"window_days": 29.9, "sample_count": 1375},
                    "metrics": {
                        "gex_board.total_net_gex": {
                            "rank_pct": 93.1,
                            "quality": "warming_up",
                        },
                    },
                },
            },
        },
    }


def test_materializer_compat_backfill(materializer):
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        source = root / "signal_review.jsonl"
        output = root / "frontend"
        source.write_text(
            json.dumps(legacy_card(), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        result = materializer.materialize(source, output, max_cards=10)
        assert_true(result["written_cards"] == 1, "materialized card count")
        card_path = next(
            path for path in (output / "signal_cards").glob("*.json")
            if path.name != "index.json")
        card = json.loads(card_path.read_text(encoding="utf-8"))
        funding = card["factor_cross_section"]["funding"]
        fact = funding["canonical_funding_semantics"]
        assert_true(fact["compat_backfill_applied"] is True, "Funding compat flag")
        assert_true(fact["is_crowded"] is False, "small raw remains not crowded")
        assert_true(fact["reflexivity_importance"] == "NOISE", "small reflexivity")
        rank = card["factor_cross_section"]["gex_info"]["rank"]
        assert_true(rank["compat_backfill_applied"] is True, "GEX compat flag")
        assert_true(
            rank["metrics"]["gex_board.total_net_gex"]["quality"] == "ok",
            "29.9-day legacy rank becomes robust usable",
        )


def test_llm_rejects_conflicting_funding_and_raw_transition(tool):
    sample = {"identity": {"card_id": "canonical-funding-llm-test"},
              "market_context": {"price": 101500},
              "factor_cross_section": {"funding": {
        "last_rate": -0.000004,
        "funding_norm": -1.0,
        "effect": "extreme_overcrowded",
    }}}
    packet = tool.build_review_packet(sample)
    funding = tool._packet_funding_semantics(packet)
    assert_true(funding, "canonical Funding semantics should be present")
    assert_true(tool.funding_text_conflicts(
        "资金费率显示空头拥挤，反身性风险升温。", funding),
        "conflicting Funding LLM text must be detected")

    transition = {
        "transition_id": "T-CANONICAL", "previous_card_id": "CARD-A",
        "current_card_id": "CARD-B", "symbol": "BTC",
        "llm_review_required": True,
    }
    packet = tool.build_transition_review_packet(transition)
    review = {
        "observed_changes": [{
            "domain": "FUNDING", "fact_cn": (
                "资金费率: {'funding_state':'crowded_short','last_rate':-4e-06}"),
            "impact_cn": "只读检查。", "tendency": "NEUTRAL",
            "evidence_refs": [],
        }],
        "cross_factor_interactions": [], "cross_factor_assessments": [],
        "candidate_explanations": [], "anomaly_assessment": {},
        "operator_focus": [], "invalid_if": [], "operator_checks": [],
        "language_guard": {"no_external_data": True,
                           "no_trading_instruction": True,
                           "distinguishes_observation_from_causality": True},
        "not_trading_advice": True,
    }
    policy = tool._transition_policy_validation(review, packet)
    assert_true(
        policy["render_state"] == "DEGRADED_LLM_TEXT",
        "raw Funding dict must fall back to deterministic transition text",
    )
    assert_true(
        "raw_field_path_leak" in policy["issue_codes"],
        "raw Funding dict leak issue code",
    )


def main():
    semantics = load(
        ROOT / "tools" / "signal_fact_semantics.py",
        "signal_fact_semantics_contract",
    )
    materializer = load(
        ROOT / "tools" / "materialize_signal_cards.py",
        "materializer_canonical_fact_contract",
    )
    tool = load(
        ROOT / "tools" / "signal_llm_review.py",
        "gemini_canonical_fact_contract",
    )
    producer = load(
        ROOT / "demo" / "最新交付物" / "neutral_regulation_demo_fmz.py",
        "producer_canonical_fact_contract",
    )
    test_funding_threshold_matrix(semantics)
    test_producer_and_shared_funding_semantics_match(semantics, producer)
    test_materializer_compat_backfill(materializer)
    test_llm_rejects_conflicting_funding_and_raw_transition(tool)
    print("canonical_fact_semantics_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("canonical_fact_semantics_contract: FAIL - " + str(exc))
        sys.exit(1)
