import json
from pathlib import Path

import pytest

from tools import astra_underwriting_review_v20 as review
from tools import astra_wpem_product_review_v1 as product


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_sample(relative: str):
    root = REPO_ROOT / relative
    if not root.exists():
        pytest.skip(f"sample missing: {relative}")
    return (
        json.loads((root / "snapshot.json").read_text(encoding="utf-8")),
        json.loads((root / "evidence.json").read_text(encoding="utf-8")),
        json.loads((root / "review_input.json").read_text(encoding="utf-8")),
    )


def _build_current_from(relative: str):
    snapshot, evidence, old_input = _load_sample(relative)
    current = product.build_review_input(
        snapshot,
        evidence,
        research_only=(old_input.get("research_scope") or {}).get("research_only") is True,
        model=(old_input.get("llm_identity") or {}).get("requested_model"),
    )
    return snapshot, evidence, old_input, current


def _by_ref(review_input):
    return {
        item["ref"]: item
        for item in (review_input.get("evidence_input") or {}).get("evidence") or []
        if isinstance(item, dict) and isinstance(item.get("ref"), str)
    }


def _valid_payload(q2_text="冻结报价不是成交回执。", q2_refs=None):
    return {
        "response_protocol": product.EVIDENCE_POINTS_RESPONSE_PROTOCOL,
        "q1": [{"text_cn": "本单责任方向按合同身份读取。", "evidence_refs": ["contract"]}],
        "q2": [{"text_cn": q2_text, "evidence_refs": q2_refs or ["quote"]}],
    }


@pytest.mark.parametrize(
    "relative,schema,prompt",
    [
        (
            ".artifacts/auditable_observation_20261006_01/samples/clean_followup1",
            product.ROUTING_REVIEW_INPUT_SCHEMA_264,
            product.EVIDENCE_SYNTHESIS_PROMPT_VERSION_264,
        ),
        (
            ".artifacts/topology_realignment_20261006_01/samples/semantic_followup",
            product.ROUTING_REVIEW_INPUT_SCHEMA_265,
            product.EVIDENCE_SYNTHESIS_PROMPT_VERSION_265,
        ),
        (
            ".artifacts/semantic_synthesis_20261007_01/samples/call_later",
            product.ROUTING_REVIEW_INPUT_SCHEMA_266,
            product.EVIDENCE_SYNTHESIS_PROMPT_VERSION_266,
        ),
        (
            ".artifacts/semantic_synthesis_20261007_01/samples/put_later_v104",
            product.ROUTING_REVIEW_INPUT_SCHEMA_267,
            product.EVIDENCE_SYNTHESIS_PROMPT_VERSION_267,
        ),
        (
            ".artifacts/semantic_synthesis_20261007_01/samples/call_early_v105",
            product.ROUTING_REVIEW_INPUT_SCHEMA_268,
            product.EVIDENCE_SYNTHESIS_PROMPT_VERSION_268,
        ),
    ],
)
def test_264_through_268_rebuild_frozen_inputs_byte_equivalent(relative, schema, prompt):
    snapshot, evidence, old_input = _load_sample(relative)

    rebuilt = product._rebuilt_review_input_for_schema(
        snapshot,
        evidence,
        research_only=(old_input.get("research_scope") or {}).get("research_only") is True,
        model=(old_input.get("llm_identity") or {}).get("requested_model"),
        schema=old_input["schema"],
    )

    assert old_input["schema"] == schema
    assert old_input["request_contract"]["prompt_version"] == prompt
    assert rebuilt == old_input


def test_current_269_identity_and_268_prompt_are_separate():
    _snapshot, _evidence, old_input, current = _build_current_from(
        ".artifacts/semantic_synthesis_20261007_01/samples/call_early_v105"
    )

    assert old_input["schema"] == product.ROUTING_REVIEW_INPUT_SCHEMA_268
    assert old_input["request_contract"]["prompt_version"] == product.EVIDENCE_SYNTHESIS_PROMPT_VERSION_268
    assert product.ROUTING_REVIEW_INPUT_SCHEMA_268 == "review_input@2.6.8"
    assert product.EVIDENCE_SYNTHESIS_PROMPT_VERSION_268 == "evidence_synthesis_prompt@1.0.5"
    assert current["schema"] == "review_input@2.6.9"
    assert current["request_contract"]["prompt_version"] == "evidence_synthesis_prompt@2.1-candidate"
    assert current["response_protocol"] == product.EVIDENCE_POINTS_RESPONSE_PROTOCOL


def test_269_single_fragment_scope_does_not_claim_comparison_language():
    _snapshot, _evidence, _old_input, current = _build_current_from(
        ".artifacts/semantic_synthesis_20261007_01/samples/call_early_v105"
    )
    fragments = _by_ref(current)["wpem.fragments"]
    text = " ".join(
        str(value or "")
        for value in (
            (fragments.get("facts") or {}).get("scope_cn"),
            fragments.get("scope"),
            (fragments.get("relations") or {}).get("scope_cn"),
        )
    )

    assert len((fragments.get("facts") or {}).get("observed_points") or []) == 1
    assert "latest_adjacent_quote_pair" not in (fragments.get("relations") or {})
    for forbidden in ("相邻", "变化", "增加", "减少", "趋势"):
        assert forbidden not in text


def test_269_multi_fragment_keeps_only_verified_adjacent_pair_relation():
    _snapshot, _evidence, _old_input, current = _build_current_from(
        ".artifacts/semantic_synthesis_20261007_01/samples/put_later_v104"
    )
    fragments = _by_ref(current)["wpem.fragments"]
    pair = (fragments.get("relations") or {}).get("latest_adjacent_quote_pair") or {}

    assert len((fragments.get("facts") or {}).get("observed_points") or []) >= 2
    assert pair["comparison_scope"] == "adjacent_frozen_quotes_only"
    assert pair["visible_credit_relation"] in {"increased", "decreased", "unchanged"}
    assert pair["net_credit_after_fee_relation"] in {"increased", "decreased", "unchanged"}


def test_269_tmv_basis_preserves_mixed_horizons_and_marks_aggregate_source():
    _snapshot, _evidence, _old_input, current = _build_current_from(
        ".artifacts/semantic_synthesis_20261007_01/samples/call_early_v105"
    )
    tmv = _by_ref(current)["tmv"]

    assert tmv["facts"]["tmvf_24h"]["final_state"] == "mild_bullish_bias"
    assert tmv["facts"]["tmvf_48h"]["final_state"] == "neutral"
    assert "24h=mild_bullish_bias" in tmv["basis"]
    assert "48h=neutral" in tmv["basis"]
    assert "aggregate_direction" in tmv["basis"]
    assert "不覆盖24h/48h子窗" in tmv["basis"]
    assert tmv["relations"]["aggregate_source"] in {"producer_top_level", "not_provided"}
    assert tmv["relations"]["does_not_override_horizon_states"] is True


def test_269_preserves_quote_and_risk_direct_refs():
    _snapshot, _evidence, _old_input, current = _build_current_from(
        ".artifacts/semantic_synthesis_20261007_01/samples/call_early_v105"
    )
    refs = _by_ref(current)

    assert refs["quote"]["relations"]["frozen_quote_not_filled"] is True
    assert refs["risk"]["relations"]["positive_ev_not_determinable"] is True
    assert "quote" in current["allowed_refs"]
    assert "risk" in current["allowed_refs"]


def test_financial_validator_allows_not_filled_negations_but_rejects_affirmative_fills():
    _snapshot, _evidence, _old_input, current = _build_current_from(
        ".artifacts/semantic_synthesis_20261007_01/samples/call_early_v105"
    )

    accepted = _valid_payload(
        "冻结报价frozen_quote_not_filled、not_filled、frozen_quote_not_filled=true、not_filled=true、filled=false；未成交、没有成交、不得视为成交。",
        ["quote"],
    )
    assert product.validate_evidence_points(accepted, current)["q2"][0]["evidence_refs"] == ["quote"]

    for text in (
        "冻结报价已成交。",
        "冻结报价filled=true。",
        "冻结报价not_filled=false。",
        "冻结报价frozen_quote_not_filled=false。",
        "冻结报价未成交，但filled=true。",
        "不得视为成交，但后文又写已成交。",
    ):
        payload = _valid_payload(text, ["quote"])
        with pytest.raises(review.ReviewValidationError, match="filled or booked"):
            product.validate_evidence_points(payload, current)

    boundary = _valid_payload("fulfilled这个英文词不等于成交回执；中文后面的not_filled=true也仍是未成交。", ["quote"])
    assert product.validate_evidence_points(boundary, current)["q2"][0]["text_cn"].startswith("fulfilled")
