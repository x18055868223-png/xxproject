import json
from copy import deepcopy
from pathlib import Path

import pytest

from tools import astra_underwriting_review_v20 as review
from tools import astra_wpem_product_review_v1 as product


REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_SAMPLE_ROOT = REPO_ROOT / ".artifacts" / "auditable_observation_20261006_01" / "samples" / "clean_followup1"


def _load_real_sample():
    if not REAL_SAMPLE_ROOT.exists():
        pytest.skip("auditable observation clean_followup1 sample missing")
    return (
        json.loads((REAL_SAMPLE_ROOT / "snapshot.json").read_text(encoding="utf-8")),
        json.loads((REAL_SAMPLE_ROOT / "evidence.json").read_text(encoding="utf-8")),
        json.loads((REAL_SAMPLE_ROOT / "review_input.json").read_text(encoding="utf-8")),
    )


def _build_current():
    snapshot, evidence, old_input = _load_real_sample()
    current = product.build_review_input(
        snapshot,
        evidence,
        research_only=(old_input.get("research_scope") or {}).get("research_only") is True,
        model=(old_input.get("llm_identity") or {}).get("requested_model"),
    )
    return snapshot, evidence, old_input, current


def _points():
    return {
        "response_protocol": product.EVIDENCE_POINTS_RESPONSE_PROTOCOL,
        "q1": [{"text_cn": "本保单应按给出的不利方向理解已有证据；适配解释不替代程序责任账。", "evidence_refs": ["contract"]}],
        "q2": [{"text_cn": "当前报价不代表实际成交，补偿取舍应以本次冻结报价为依据。", "evidence_refs": ["quote"]}],
    }


def test_current_265_places_relations_before_clean_input():
    _snapshot, _evidence, old_input, current = _build_current()

    assert old_input["schema"] == product.ROUTING_REVIEW_INPUT_SCHEMA_264
    assert current["schema"] == product.ROUTING_REVIEW_INPUT_SCHEMA
    assert current["response_protocol"] == product.EVIDENCE_POINTS_RESPONSE_PROTOCOL
    assert "decision_evidence" not in current

    relations = current["decision_relations"]["evidence_relations"]
    assert {
        "contract",
        "quote",
        "risk",
        "flow",
        "wpem.fragments",
        "space.flip",
        "space.gate",
        "map.etf",
        "map.cost",
        "map.realized",
        "map.oi",
        "map.funding",
    }.issubset(relations)
    assert current["wpem"]["same_candidate_window"]["observed_fragment_relations"] == relations["wpem.fragments"]

    clean_by_ref = {item["ref"]: item for item in current["evidence_input"]["evidence"]}
    for ref in ("contract", "quote", "risk", "flow", "wpem.fragments", "space.flip",
                "space.gate", "map.etf", "map.cost", "map.realized", "map.oi", "map.funding"):
        assert clean_by_ref[ref]["relations"] == relations[ref]


def test_current_265_clean_pack_does_not_recompute_missing_owned_relation():
    snapshot, _evidence, _old_input, current = _build_current()
    tampered = deepcopy(current)
    tampered["decision_relations"]["evidence_relations"].pop("map.cost")

    with pytest.raises(review.ReviewValidationError, match="decision evidence relation missing: map.cost"):
        product._clean_evidence_package(snapshot, tampered)


def test_cost_basis_relation_keeps_local_unknown_without_deleting_other_series():
    metrics = {
        "cost_comparison_basis": "BITVIEW_SAME_SOURCE_SAME_DAY_ONLY",
        "cost_basis": {
            "cp_sth_usd": 90.0,
            "cp_lth_usd": 110.0,
            "cp_all_usd": 100.0,
            "source_price_usd": 100.0,
            "observation_dates": {
                "cp_sth": "2026-10-05",
                "cp_lth": "2026-10-04",
                "cp_all": "2026-10-05",
                "source_price": "2026-10-05",
            },
        },
    }

    relations = product._cost_basis_relations(metrics)

    assert relations["cp_source_price_observation_dates_match"] is False
    assert relations["cp_sth_usd_vs_source_price"] == "below_source_price"
    assert relations["cp_lth_usd_vs_source_price"] == "unknown_not_same_source_day"
    assert relations["cp_all_usd_vs_source_price"] == "equal_source_price"


def test_264_real_sample_rebuilds_byte_equivalent():
    snapshot, evidence, old_input = _load_real_sample()

    rebuilt = product._rebuilt_review_input_for_schema(
        snapshot,
        evidence,
        research_only=(old_input.get("research_scope") or {}).get("research_only") is True,
        model=(old_input.get("llm_identity") or {}).get("requested_model"),
        schema=old_input["schema"],
    )

    assert old_input["schema"] == product.ROUTING_REVIEW_INPUT_SCHEMA_264
    assert rebuilt == old_input


def test_relation_tamper_still_rejected_after_projection_hash_recomputed():
    snapshot, evidence, _old_input, current = _build_current()
    tampered = deepcopy(current)
    tampered["decision_relations"]["evidence_relations"]["map.cost"]["cp_sth_usd_vs_source_price"] = "above_source_price"
    tampered["projection_hash"] = review._hash({key: value for key, value in tampered.items() if key != "projection_hash"})

    with pytest.raises(review.ReviewValidationError, match="replay mismatch"):
        review.validate_wire_response(snapshot, _points(), review_input=tampered, wpem_evidence=evidence)
