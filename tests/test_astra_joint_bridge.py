import copy
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
TESTS = ROOT / "tests"
for item in (TOOLS, TESTS, ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from astra_joint_bridge import BRIDGE_SCHEMA, attach_projection, context_for_card, load_registry, prompt_context
from astra_joint_projection import build_joint_projection, compute_assessment_hash
from test_astra_joint_projection import AS_OF_MS, make_assessment, make_card


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def attach_quote(assessment, side, net_credit=0.004, checked_delta_ms=3000, max_age_ms=2000, skew_ms=1000):
    payout = assessment["sides"][side]["expected_payout_btc"]
    assessment["sides"][side]["quote"] = {
        "status": "available",
        "net_credit_btc": net_credit,
        "expected_net_btc": net_credit - payout,
        "observed_at_ms": AS_OF_MS + checked_delta_ms - max_age_ms,
        "checked_at_ms": AS_OF_MS + checked_delta_ms,
        "max_age_ms": max_age_ms,
        "leg_timestamp_skew_ms": skew_ms,
    }


def registry_item(assessment=None, available_delta_ms=5000):
    return {
        "card_id": "CARD-STAT-1",
        "available_at_ms": AS_OF_MS + available_delta_ms,
        "assessment": assessment or make_assessment(),
    }


def test_research_only_tail_is_explained_without_a_probability():
    assessment=make_assessment()
    for side in assessment['sides'].values():
        side.update(tail_probability=None,tail_probability_status='research_only',
                    tail_probability_note_cn='尾部概率未通过一致性验收，暂不用于逐卡建议。')
    assessment['assessment_hash']=compute_assessment_hash(assessment)
    context=context_for_card(make_card(),{'CARD-STAT-1':registry_item(assessment)},available_before_ms=AS_OF_MS+10000)
    compact=prompt_context(context)
    for side in compact['sides'].values():
        assert side['tail_probability'] is None
        assert side['tail_probability_status']=='research_only'
        assert '未通过' in side['tail_probability_note_cn']
    projection=build_joint_projection(assessment,make_card())
    assert projection['detail']['sides']['put']['tail_probability'] is None
    assert '未通过' in projection['detail']['sides']['put']['tail_probability_note_cn']


def test_registry_loads_and_context_uses_freeze_time():
    assessment = make_assessment()
    attach_quote(assessment, "put")
    attach_quote(assessment, "call")
    assessment["assessment_hash"] = compute_assessment_hash(assessment)
    item = registry_item(assessment)
    with tempfile.TemporaryDirectory() as folder:
        path = pathlib.Path(folder) / "registry.jsonl"
        path.write_text(json.dumps(item, ensure_ascii=False) + "\n", encoding="utf-8")
        registry = load_registry(path)
    assert_true("CARD-STAT-1" in registry, "registry should load card keyed item")
    assert_true(context_for_card(make_card(), registry, available_before_ms=AS_OF_MS + 4000) is None,
                "late statistical context must not enter an earlier review")
    context = context_for_card(make_card(), registry, available_before_ms=AS_OF_MS + 6000)
    assert_true(context and context["schema"] == BRIDGE_SCHEMA, "available frozen context should be returned")
    prompt = prompt_context(context)
    assert_true(prompt["assessment_hash"] == assessment["assessment_hash"], "prompt context should bind assessment hash")
    assert_true("scope_cn" in prompt and "sides" in prompt, "prompt context should carry only frozen assessment fields")


def test_attach_projection_uses_joint_research_fields_and_preserves_hashes():
    assessment = make_assessment()
    attach_quote(assessment, "put")
    attach_quote(assessment, "call")
    assessment["assessment_hash"] = compute_assessment_hash(assessment)
    registry = {"CARD-STAT-1": registry_item(assessment)}
    record = make_card()
    record["llm_review"] = {"status": "OK", "statistical_context": {"assessment": {"assessment_hash": assessment["assessment_hash"]}}}
    expected = build_joint_projection(assessment, record)

    assert_true(attach_projection(record, registry) is True, "valid registry item should attach projection")
    assert_true("joint_research_detail" in record and "joint_research_summary" in record, "bridge should use frontend field names")
    assert_true("astra_joint_display" not in record and "astra_joint_summary" not in record, "legacy bridge field names should not be written")
    assert_true(record["joint_research_detail"] == expected["detail"], "detail projection should not receive extra fields")
    assert_true(record["joint_research_summary"]["detail_projection_hash"] == record["joint_research_detail"]["display_projection_hash"],
                "summary should bind detail projection hash")
    assert_true(record["joint_research_review_included"] is True, "review included flag should be a separate top-level marker")


def test_bad_or_late_registry_item_does_not_pollute_existing_card():
    old_summary = {"schema": "signal_evidence_summary@2.2.0", "display_projection_hash": "keep"}
    record = make_card()
    record["signal_evidence_summary"] = copy.deepcopy(old_summary)
    record["llm_review"] = {"status": "OK", "content": "keep"}

    bad_assessment = make_assessment()
    bad_assessment["provenance"]["source_record_hash"] = "sha256:" + "f" * 64
    bad_assessment["assessment_hash"] = compute_assessment_hash(bad_assessment)
    registry = {"CARD-STAT-1": registry_item(bad_assessment)}
    before = copy.deepcopy(record)
    assert_true(attach_projection(record, registry) is False, "bad source hash should fail isolated")
    assert_true(record == before, "bad joint item must not alter existing card fields")

    assessment = make_assessment()
    attach_quote(assessment, "put", checked_delta_ms=7000)
    attach_quote(assessment, "call", checked_delta_ms=7000)
    assessment["assessment_hash"] = compute_assessment_hash(assessment)
    late_quote = {"CARD-STAT-1": registry_item(assessment, available_delta_ms=5000)}
    assert_true(context_for_card(make_card(), late_quote, available_before_ms=AS_OF_MS + 6000) is None,
                "quote checked after registry freeze must be excluded from prompt context")


def test_unavailable_stat_does_not_add_ui_or_prompt_context_but_can_attach_projection():
    assessment = make_assessment(status="unavailable")
    assessment["reason_cn"] = "模型制品暂不可用。"
    assessment["sides"] = {"put": {"status": "unavailable"}, "call": {"status": "unavailable"}}
    assessment["assessment_hash"] = compute_assessment_hash(assessment)
    registry = {"CARD-STAT-1": registry_item(assessment)}
    assert_true(context_for_card(make_card(), registry, available_before_ms=AS_OF_MS + 6000) is None,
                "unavailable statistical assessment should not be included in model prompt")
    record = make_card()
    assert_true(attach_projection(record, registry) is True, "unavailable display projection may still be attached for reader transparency")
    assert_true(record["joint_research_summary"]["status"] == "unavailable", "reader should carry unavailable status")
    assert_true(record["joint_research_review_included"] is False, "display-only projection should not pretend review included it")


def main():
    test_registry_loads_and_context_uses_freeze_time()
    test_attach_projection_uses_joint_research_fields_and_preserves_hashes()
    test_bad_or_late_registry_item_does_not_pollute_existing_card()
    test_unavailable_stat_does_not_add_ui_or_prompt_context_but_can_attach_projection()
    print("astra_joint_bridge: PASS")


if __name__ == "__main__":
    main()
