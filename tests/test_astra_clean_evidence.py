import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from tools import astra_underwriting_review_v20 as review
from tools import astra_wpem_product_review_v1 as product


REPO_ROOT = Path(__file__).resolve().parents[1]
CURRENT_VALIDATED_SAMPLE_ROOT = (
    REPO_ROOT / ".artifacts" / "observation_baseline_20261006_01" / "samples" / "current_validated"
)
SHORTEST_PATH_SAMPLE_ROOT = REPO_ROOT / ".artifacts" / "data_shortest_path_20261006_01" / "samples"
BEFORE_NORMALIZED_PATH = REPO_ROOT / ".artifacts" / "data_shortest_path_20261006_01" / "before_normalized.json"
BEFORE_NORMALIZED_SHA256 = "ff916d019def79f5d86ebaf9ba4b40832f70bbe02b9d17dec857273cea8d38b8"


def _canon(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _load_current_validated():
    if not CURRENT_VALIDATED_SAMPLE_ROOT.exists():
        pytest.skip("current_validated sample missing")
    return (
        json.loads((CURRENT_VALIDATED_SAMPLE_ROOT / "snapshot.json").read_text(encoding="utf-8")),
        json.loads((CURRENT_VALIDATED_SAMPLE_ROOT / "evidence.json").read_text(encoding="utf-8")),
        json.loads((CURRENT_VALIDATED_SAMPLE_ROOT / "review_input.json").read_text(encoding="utf-8")),
        json.loads((CURRENT_VALIDATED_SAMPLE_ROOT / "actual_llm" / "parsed_wire_response.json").read_text(encoding="utf-8")),
    )


def _latest_review_input():
    snapshot, evidence, old_input, _old_payload = _load_current_validated()
    current = product.build_review_input(
        snapshot,
        evidence,
        research_only=(old_input.get("research_scope") or {}).get("research_only") is True,
        model=(old_input.get("llm_identity") or {}).get("requested_model"),
    )
    return snapshot, evidence, old_input, current


def _valid_points_payload():
    return {
        "response_protocol": product.EVIDENCE_POINTS_RESPONSE_PROTOCOL,
        "q1": [
            {
                "text_cn": "本保单应按给出的不利方向理解已有证据；适配解释不替代程序责任账。",
                "evidence_refs": ["contract"],
            }
        ],
        "q2": [
            {
                "text_cn": "当前报价不代表实际成交，补偿取舍应以本次冻结报价为依据。",
                "evidence_refs": ["quote"],
            }
        ],
    }


def test_264_builds_short_clean_evidence_with_core_market_refs():
    _snapshot, _evidence, old_input, current = _latest_review_input()

    assert old_input["schema"] == product.ROUTING_REVIEW_INPUT_SCHEMA_262
    assert current["schema"] == product.ROUTING_REVIEW_INPUT_SCHEMA
    assert current["response_protocol"] == product.EVIDENCE_POINTS_RESPONSE_PROTOCOL
    assert current["request_contract"]["prompt_version"] == product.EVIDENCE_SYNTHESIS_PROMPT_VERSION
    assert current["request_contract"]["exact_output_keys"] == product.EVIDENCE_POINTS_OUTPUT_KEYS
    assert current["request_contract"]["model_input_field"] == "evidence_input"
    assert "explanation_contract" not in current

    evidence_input = current["evidence_input"]
    refs = {item["ref"] for item in evidence_input["evidence"]}
    assert {
        "contract", "quote", "fee", "risk", "flow",
        "wpem.level", "wpem.delta", "wpem.path", "wpem.fragments",
        "tmv", "funding", "skew", "macro", "space.flip", "space.gate",
        "map.etf", "map.cost", "map.realized", "map.oi", "map.funding",
    }.issubset(refs)
    assert any(ref.startswith("map.kpf.") for ref in refs)

    clean_text = _canon(evidence_input)
    full_text = _canon(current)
    assert len(clean_text) < len(full_text) // 4
    for forbidden in ("claim_catalog", "source_bindings", "composition_inputs", "packet_hash", "fact_source_hash", "raw_payload_hash"):
        assert forbidden not in clean_text

    by_ref = {item["ref"]: item for item in evidence_input["evidence"]}
    assert by_ref["map.etf"]["facts"]["f3_usd_m"] == pytest.approx(202.8)
    assert by_ref["map.etf"]["facts"]["f7_usd_m"] == pytest.approx(285.8)
    assert by_ref["map.cost"]["facts"]["cp_sth_usd"] == pytest.approx(75091.67)
    assert by_ref["map.cost"]["relations"]["cp_sth_usd_vs_source_price"] == "below_source_price"
    assert by_ref["map.cost"]["relations"]["not_compared_to_deribit_live_quote"] is True
    assert by_ref["map.realized"]["relations"]["high_low_baseline"] is None
    assert by_ref["map.oi"]["facts"]["open_interest_native_btc"] == pytest.approx(94709.017)
    assert by_ref["map.oi"]["relations"]["same_metric_current_vs_previous"] == "decreased"
    assert "Funding" not in by_ref["map.oi"]["basis"]
    assert by_ref["map.funding"]["relations"]["borrow_gap_does_not_delete_funding"] is True
    assert by_ref["map.funding"]["relations"]["adjacent_equal_windows"] is True
    assert by_ref["map.funding"]["relations"]["recent_vs_previous_settled_sum"] == "decreased"
    assert '"selected_for_display"' not in clean_text
    assert '"eligible_for_display"' not in clean_text
    assert by_ref["space.flip"]["relations"]["price_vs_flip"] == "PRICE_ABOVE_FLIP"
    assert "claim_id" not in _canon(by_ref["flow"]["facts"])
    assert "counter_id" not in _canon(by_ref["flow"]["facts"])

    fragments = by_ref["wpem.fragments"]
    assert len(fragments["facts"]["observed_points"]) == 14
    point = fragments["facts"]["observed_points"][0]
    assert set(point) == {"decision_asof_ms", "market", "contract", "economics", "source_clock", "qualification"}
    assert "snapshot_id" not in point
    assert point["market"]["reference_price_usd"] is not None
    assert point["economics"]["visible_credit_btc"] is not None
    assert point["qualification"]["risk_gap_reasons"]

    assert current["input_lineage"]["map.cost"]["source_path"].endswith("metrics.cost_basis")
    assert any(item["field"] == "wpem.same_candidate_window.observed_points.full" for item in current["input_omissions"])


def test_264_evidence_points_normalize_with_program_guardrails_and_trace():
    snapshot, evidence, _old_input, current = _latest_review_input()
    payload = _valid_points_payload()

    normalized = product.validate_wire_response(snapshot, payload, review_input=current, evidence=evidence)

    assert normalized["response_protocol"] == product.EVIDENCE_POINTS_RESPONSE_PROTOCOL
    assert normalized["selection_origin"] == "program_guardrails"
    assert normalized["summary_cn"] == "LLM只对本次clean evidence做逐点综合；目录选择和金融账由本地程序守护，不代表交易许可。"
    assert normalized["llm_explanation"]["q1"]["points"][0]["evidence_refs"] == ["contract"]
    assert normalized["llm_explanation"]["q2"]["points"][0]["evidence_refs"] == ["quote"]
    assert normalized["q1"]["llm_explanation"] == normalized["llm_explanation"]["q1"]
    assert normalized["q2"]["llm_explanation"] == normalized["llm_explanation"]["q2"]

    trace = normalized["decision_trace"]
    assert trace["binding"]["response_protocol"] == product.EVIDENCE_POINTS_RESPONSE_PROTOCOL
    assert {item["ref"] for item in trace["input_evidence"]} >= {"contract", "quote", "map.cost", "wpem.fragments"}
    clean_layer = next(layer for layer in trace["layers"] if layer["layer"] == "clean_input")
    assert clean_layer["consumed_refs"] == ["contract", "quote"]
    assert any(item["ref"] == "map.cost" for item in clean_layer["omitted"])
    layers_by_name = {layer["layer"]: layer for layer in trace["layers"]}
    assert layers_by_name["data.contract"]["consumed_refs"] == ["contract"]
    assert layers_by_name["data.economics"]["consumed_refs"] == ["quote"]
    for layer_name in ("data.wpem", "data.context", "data.space", "data.map"):
        assert layers_by_name[layer_name]["consumed_refs"] == []
    assert "按并列证据维度展示信息汇合" in trace["scope_cn"]
    assert "不表示组件间严格串行依赖" in trace["scope_cn"]
    assert "未引用不等于上游未消费" in trace["scope_cn"]
    assert "page" not in layers_by_name
    assert trace["points"] == [
        {"question": "q1", "index": 1, "text_cn": payload["q1"][0]["text_cn"], "evidence_refs": ["contract"]},
        {"question": "q2", "index": 1, "text_cn": payload["q2"][0]["text_cn"], "evidence_refs": ["quote"]},
    ]


@pytest.mark.parametrize(
    "question,text,refs,match",
    [
        ("q1", "CP链上成本高于Bitview源价，说明成本在现价上方。", ["map.cost"], "source-price relation"),
        ("q1", "翻转位位于现价上方，价格低于flip。", ["space.flip"], "PRICE_ABOVE_FLIP"),
        ("q1", "RPL已实现亏损占比偏高，构成明确压力。", ["map.realized"], "baseline"),
        ("q1", "Funding转弱且Skew偏空，但这里只引用方向流。", ["flow"], "direct evidence ref"),
        ("q2", "当前报价已成交入账，确认正期望并允许下单。", ["quote", "risk"], "filled or booked|EV"),
    ],
)
def test_264_rejects_prior_observed_semantic_errors_with_direct_refs(question, text, refs, match):
    _snapshot, _evidence, _old_input, current = _latest_review_input()
    payload = _valid_points_payload()
    payload[question][0] = {"text_cn": text, "evidence_refs": refs}

    with pytest.raises(review.ReviewValidationError, match=match):
        product.validate_evidence_points(payload, current)


def test_264_negated_unknown_boundaries_are_allowed():
    _snapshot, _evidence, _old_input, current = _latest_review_input()
    payload = _valid_points_payload()
    payload["q1"][0] = {
        "text_cn": "μ未知，不能判断正期望；冻结报价未成交；CP只可与Bitview同日源价比较，不能与Deribit live quote比较。",
        "evidence_refs": ["risk", "quote", "map.cost"],
    }

    result = product.validate_evidence_points(payload, current)

    assert "μ未知" in result["q1"][0]["text_cn"]


def test_262_actual_replay_stays_byte_equivalent_to_shortest_path_baseline():
    if not BEFORE_NORMALIZED_PATH.exists():
        pytest.skip("before normalized baseline missing")
    snapshot, evidence, old_input, old_payload = _load_current_validated()
    before_normalized = json.loads(BEFORE_NORMALIZED_PATH.read_text(encoding="utf-8"))

    rebuilt = product._rebuilt_review_input_for_schema(
        snapshot,
        evidence,
        research_only=(old_input.get("research_scope") or {}).get("research_only") is True,
        model=(old_input.get("llm_identity") or {}).get("requested_model"),
        schema=old_input["schema"],
    )
    normalized = product.validate_wire_response(snapshot, old_payload, review_input=old_input, evidence=evidence)

    assert old_input["schema"] == product.ROUTING_REVIEW_INPUT_SCHEMA_262
    assert rebuilt == old_input
    assert "evidence_input" not in rebuilt
    assert hashlib.sha256(_canon(before_normalized).encode("utf-8")).hexdigest() == BEFORE_NORMALIZED_SHA256
    assert normalized == before_normalized
    assert hashlib.sha256(_canon(normalized).encode("utf-8")).hexdigest() == BEFORE_NORMALIZED_SHA256


def test_263_actual_replay_stays_bounded_and_does_not_gain_clean_protocol():
    sample_root = SHORTEST_PATH_SAMPLE_ROOT / "map_context_final"
    if not sample_root.exists():
        pytest.skip("2.6.3 sample missing")
    snapshot = json.loads((sample_root / "snapshot.json").read_text(encoding="utf-8"))
    evidence = json.loads((sample_root / "evidence.json").read_text(encoding="utf-8"))
    old_input = json.loads((sample_root / "review_input.json").read_text(encoding="utf-8"))
    old_payload = json.loads((sample_root / "actual_llm" / "parsed_wire_response.json").read_text(encoding="utf-8"))

    rebuilt = product._rebuilt_review_input_for_schema(
        snapshot,
        evidence,
        research_only=(old_input.get("research_scope") or {}).get("research_only") is True,
        model=(old_input.get("llm_identity") or {}).get("requested_model"),
        schema=old_input["schema"],
    )
    normalized = product.validate_wire_response(snapshot, old_payload, review_input=old_input, evidence=evidence)

    assert old_input["schema"] == product.ROUTING_REVIEW_INPUT_SCHEMA_263
    assert rebuilt == old_input
    assert normalized["response_protocol"] == product.ROUTING_RESPONSE_PROTOCOL
    assert "selection_origin" not in normalized
    assert "decision_trace" not in normalized
    assert "evidence_input" not in rebuilt

PRIOR_FAILURE_CASES = [
    pytest.param(
        "map_context",
        "q1",
        "冻结来源报告的有效翻转位位于现价上方，且被归入同源中性稳定反馈，只能作为背景解释，不能当第二个方向票。",
        ["space.flip"],
        "PRICE_ABOVE_FLIP|flip direction|现价上方",
        id="map_context_flip_above_current",
    ),
    pytest.param(
        "map_context_grounded",
        "q2",
        "局部限制：风险参考μ缺失、风险参考未通过支持域校验，DVOL未进入当前投影，因此不能判断补偿是否足够，也不能给出概率或胜率表述。",
        ["risk"],
        "support|支持域|missing",
        id="map_context_grounded_missing_not_support_failed",
    ),
    pytest.param(
        "map_context_grounded",
        "q2",
        "期权需求与空间结构同源，锚与空间门共享依赖，不能当作第二个确认。",
        ["skew", "space.gate"],
        "same source|同源|shared",
        id="map_context_grounded_skew_space_same_source",
    ),
    pytest.param(
        "map_context_precise",
        "q1",
        "近14日实现亏损占比偏高",
        ["map.realized"],
        "baseline|高低基准",
        id="map_context_precise_rpl_high_without_baseline",
    ),
    pytest.param(
        "map_context_selected",
        "q2",
        "保护腿位于卖出腿上方，赔付在保护腿上方趋于平坦，卖出腿附近不一定立即转负，盈亏平衡点由冻结公式决定；",
        ["contract"],
        "payoff curve|deterministic",
        id="map_context_selected_protection_flattening",
    ),
    pytest.param(
        "map_context_selected",
        "q2",
        "可见权利金差只是同源盘口的分腿估值，且已被计入账户，不代表净补偿或已成交保护。",
        ["quote", "fee"],
        "booked|filled|计入账户|入账",
        id="map_context_selected_accounted_quote",
    ),
    pytest.param(
        "map_context_final",
        "q1",
        "链上成本样本显示短期持有者成本明显低于现价、上游成本高于现价，属于同日同源口径下的条件性背景；",
        ["map.cost"],
        "source basis|cost basis|价系",
        id="map_context_final_cost_vs_current_price",
    ),
]


@pytest.fixture(scope="module")
def latest_review_input_for_prior_failures():
    _snapshot, _evidence, _old_input, current = _latest_review_input()
    return current


@pytest.mark.parametrize("sample_name,question,exact_text,refs,match", PRIOR_FAILURE_CASES)
def test_264_prior_real_failure_sentences_are_rejected_by_target_semantics(
    latest_review_input_for_prior_failures,
    sample_name,
    question,
    exact_text,
    refs,
    match,
):
    sample_path = SHORTEST_PATH_SAMPLE_ROOT / sample_name / "actual_llm" / "message_content.txt"
    if not sample_path.exists():
        pytest.skip(f"prior failure sample missing: {sample_path}")
    raw = sample_path.read_text(encoding="utf-8")
    decoded = json.loads(raw)["explanation"][question]["text_cn"]
    assert exact_text in decoded
    assert len(refs) <= 4

    payload = _valid_points_payload()
    payload[question][0] = {"text_cn": exact_text, "evidence_refs": refs}

    with pytest.raises(review.ReviewValidationError, match=match):
        product.validate_evidence_points(payload, latest_review_input_for_prior_failures)


def test_264_positive_counterexamples_for_relation_and_negation_boundaries(latest_review_input_for_prior_failures):
    current = deepcopy(latest_review_input_for_prior_failures)
    cost_item = next(item for item in current["evidence_input"]["evidence"] if item["ref"] == "map.cost")
    for key in list(cost_item["relations"]):
        if key.endswith("_vs_source_price"):
            cost_item["relations"][key] = "above_source_price"

    payload = _valid_points_payload()
    payload["q1"][0] = {
        "text_cn": "CP高于Bitview源价，仍只按Bitview成本口径说明。",
        "evidence_refs": ["map.cost"],
    }
    assert product.validate_evidence_points(payload, current)["q1"][0]["text_cn"].startswith("CP高于")

    payload["q1"][0] = {
        "text_cn": "不能说CP高于Bitview源价，也不能说翻转位位于现价上方；CP不能与Deribit live quote比较。",
        "evidence_refs": ["map.cost", "space.flip"],
    }
    assert "不能说" in product.validate_evidence_points(payload, latest_review_input_for_prior_failures)["q1"][0]["text_cn"]

    payload["q1"][0] = {
        "text_cn": "RPL没有高低基准；ETF近3日速度较高只按ETF自身窗口表达。",
        "evidence_refs": ["map.realized", "map.etf"],
    }
    assert "RPL" in product.validate_evidence_points(payload, latest_review_input_for_prior_failures)["q1"][0]["text_cn"]
