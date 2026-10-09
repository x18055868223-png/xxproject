"""Canonical candidate protocol for WPEM evidence; legacy 2.4.7 remains readable.

This adapter adds bounded observations to the existing financial validator. It
does not change candidate selection, accounting, review gates or permissions.
"""
from __future__ import annotations
from copy import deepcopy
import math
import re
from typing import Any, Mapping

if __package__:
    from . import astra_underwriting_review_v20 as legacy
    from . import astra_wpem_review_evidence_v1 as evidence_core
else:
    import astra_underwriting_review_v20 as legacy
    import astra_wpem_review_evidence_v1 as evidence_core

RESPONSE_PROTOCOL = "bounded_claim_ids@2.5.0"
ROUTING_RESPONSE_PROTOCOL = "bounded_claim_ids@2.6.2"
EVIDENCE_POINTS_RESPONSE_PROTOCOL = "evidence_points@2.6.4"
PROMPT_VERSION = "wpem_bounded_prompt@1.0.1"
ROUTING_PROMPT_VERSION_260 = "wpem_routing_bounded_prompt@1.0.0"
ROUTING_PROMPT_VERSION_261 = "wpem_routing_bounded_prompt@1.0.1"
ROUTING_PROMPT_VERSION = "wpem_routing_grounded_prompt@1.0.3"
EVIDENCE_SYNTHESIS_PROMPT_VERSION_264 = "evidence_synthesis_prompt@1.0.0"
EVIDENCE_SYNTHESIS_PROMPT_VERSION_265 = "evidence_synthesis_prompt@1.0.2"
EVIDENCE_SYNTHESIS_PROMPT_VERSION_266 = "evidence_synthesis_prompt@1.0.3"
EVIDENCE_SYNTHESIS_PROMPT_VERSION_267 = "evidence_synthesis_prompt@1.0.4"
EVIDENCE_SYNTHESIS_PROMPT_VERSION_268 = "evidence_synthesis_prompt@1.0.5"
EVIDENCE_SYNTHESIS_PROMPT_VERSION = "evidence_synthesis_prompt@2.1-candidate"
PROVIDER_CONFIG_VERSION = "wpem_json_delivery@1.0.1"
ROUTING_SCHEMA = "evidence_routing@1.0.0"
REVIEW_INPUT_SCHEMA = "review_input@2.5.2"
ROUTING_REVIEW_INPUT_SCHEMA_260 = "review_input@2.6.0"
ROUTING_REVIEW_INPUT_SCHEMA_261 = "review_input@2.6.1"
ROUTING_REVIEW_INPUT_SCHEMA_262 = "review_input@2.6.2"
ROUTING_REVIEW_INPUT_SCHEMA_263 = "review_input@2.6.3"
ROUTING_REVIEW_INPUT_SCHEMA_264 = "review_input@2.6.4"
ROUTING_REVIEW_INPUT_SCHEMA_265 = "review_input@2.6.5"
ROUTING_REVIEW_INPUT_SCHEMA_266 = "review_input@2.6.6"
ROUTING_REVIEW_INPUT_SCHEMA_267 = "review_input@2.6.7"
ROUTING_REVIEW_INPUT_SCHEMA_268 = "review_input@2.6.8"
ROUTING_REVIEW_INPUT_SCHEMA = "review_input@2.6.9"
ROUTING_REVIEW_INPUT_SCHEMAS = {
    ROUTING_REVIEW_INPUT_SCHEMA_260,
    ROUTING_REVIEW_INPUT_SCHEMA_261,
    ROUTING_REVIEW_INPUT_SCHEMA_262,
    ROUTING_REVIEW_INPUT_SCHEMA_263,
    ROUTING_REVIEW_INPUT_SCHEMA_264,
    ROUTING_REVIEW_INPUT_SCHEMA_265,
    ROUTING_REVIEW_INPUT_SCHEMA_266,
    ROUTING_REVIEW_INPUT_SCHEMA_267,
    ROUTING_REVIEW_INPUT_SCHEMA_268,
    ROUTING_REVIEW_INPUT_SCHEMA,
}
LEGACY_REVIEW_INPUT_SCHEMAS = {"review_input@2.5.0"}
COMPAT_REVIEW_INPUT_SCHEMAS = {"review_input@2.5.1", REVIEW_INPUT_SCHEMA}
REVIEW_STATE_SCHEMA = "astra_underwriting_review_attempts@2.5.0"
ROUTING_REVIEW_STATE_SCHEMA_260 = "astra_underwriting_review_attempts@2.6.0"
ROUTING_REVIEW_STATE_SCHEMA_261 = "astra_underwriting_review_attempts@2.6.1"
ROUTING_REVIEW_STATE_SCHEMA = "astra_underwriting_review_attempts@2.6.2"
RESEARCH_REVIEW_SCHEMA = "research_review@2.5.0"
ROUTING_RESEARCH_REVIEW_SCHEMA_260 = "research_review@2.6.0"
ROUTING_RESEARCH_REVIEW_SCHEMA_261 = "research_review@2.6.1"
ROUTING_RESEARCH_REVIEW_SCHEMA = "research_review@2.6.2"
CATALOG_SCHEMA = "wpem_product_claim_catalog@1.0.0"
ROUTING_CATALOG_SCHEMA_260 = "wpem_product_claim_catalog@1.1.0"
ROUTING_CATALOG_SCHEMA_261 = "wpem_product_claim_catalog@1.1.1"
ROUTING_CATALOG_SCHEMA = "wpem_product_claim_catalog@1.1.2"
ROLE = "candidate_not_deployed"
EXTENSION_FIELDS = {"wpem_evidence", "review_validity"}
EXACT_OUTPUT_KEYS = [
    "response_protocol",
    "reviewed_snapshot_id",
    "projection_hash",
    "claim_catalog_hash",
    "applicability",
    "selected_claim_ids",
    "selected_counter_ids",
    "selected_gap_ids",
    "selected_reassess_condition_ids",
]
GROUNDED_EXPLANATION_OUTPUT_KEYS = [*EXACT_OUTPUT_KEYS, "explanation"]
EVIDENCE_POINTS_OUTPUT_KEYS = ["response_protocol", "q1", "q2"]
ROUTING_ALLOWED_REFS_PREFIX = "evidence_routing."
ROUTING_INFORMATION_COUNTERS = {"COUNTER_EVIDENCE", "QUALIFICATION_ONLY", "MISSING", "INCONCLUSIVE"}
ROUTING_COUNTER_ROLES = {"COUNTER", "LIMITATION"}
ROUTING_SUPPORT_ROLES = {"PRIMARY", "SUPPORT"}
ROUTING_REQUIRED_LIMITS = {"claim_ids": 2, "counter_ids": 3, "gap_ids": 2, "reassess_condition_ids": 2}
ROUTING_ROLE_KEYS = ("claims", "counterclaims", "gaps", "reassess_conditions")
ROUTING_MECHANISM_BLOCKS = {"block.flow", "block.option_space", "block.option_demand", "block.macro_risk"}
ROUTING_LOW_VALUE_SUPPORT_BLOCKS = {"block.compensation", "block.price_location", "block.risk_reference", "block.data_quality"}
ROUTING_LIMITATION_BLOCKS = {
    "block.flow", "block.option_space", "block.option_demand", "block.macro_risk",
    "block.risk_reference", "block.volatility_structure", "block.data_quality",
}
OBSERVED_POINTS_REF = "wpem.same_candidate_window.observed_points"
_EXPLANATION_ITEM_KEYS = {"text_cn", "evidence_refs"}
_EVIDENCE_POINT_KEYS = {"text_cn", "evidence_refs"}
_BANNED_EXPLANATION_TEXT = re.compile(
    r"(?:[$＄]\s*\d|(?:\d+(?:\.\d+)?)\s*(?:BTC|USD|USDT|美元|美金))"
    r"|(?:[μµ]\s*[=＝]|(?:^|[^A-Za-z])M(?:_?ref)?\s*[=＝]|M_ref\s*[=＝]|m_ref\s*[=＝])"
    r"|(?:保证收益)"
)
_AFFIRMATIVE_EXPLANATION_PHRASES = (
    "概率",
    "胜率",
    "允许下单",
    "立即开仓",
    "确认正期望",
    "正期望成立",
    "确认补偿足够",
    "补偿足够成立",
    "足以覆盖",
)
_NEGATION_CUES = ("不", "未", "无", "非", "不能", "不可", "不得", "尚不能", "无法", "不足以", "资料不足以")
_EVIDENCE_POINT_INPUT_SCHEMAS = {
    ROUTING_REVIEW_INPUT_SCHEMA_264,
    ROUTING_REVIEW_INPUT_SCHEMA_265,
    ROUTING_REVIEW_INPUT_SCHEMA_266,
    ROUTING_REVIEW_INPUT_SCHEMA_267,
    ROUTING_REVIEW_INPUT_SCHEMA_268,
    ROUTING_REVIEW_INPUT_SCHEMA,
}
_RELATION_OWNED_INPUT_SCHEMAS = {
    ROUTING_REVIEW_INPUT_SCHEMA_265,
    ROUTING_REVIEW_INPUT_SCHEMA_266,
    ROUTING_REVIEW_INPUT_SCHEMA_267,
    ROUTING_REVIEW_INPUT_SCHEMA_268,
    ROUTING_REVIEW_INPUT_SCHEMA,
}
_CONTEXTUAL_INPUT_SCHEMAS = {
    ROUTING_REVIEW_INPUT_SCHEMA_261,
    ROUTING_REVIEW_INPUT_SCHEMA_262,
    ROUTING_REVIEW_INPUT_SCHEMA_263,
    ROUTING_REVIEW_INPUT_SCHEMA_264,
    ROUTING_REVIEW_INPUT_SCHEMA_265,
    ROUTING_REVIEW_INPUT_SCHEMA_266,
    ROUTING_REVIEW_INPUT_SCHEMA_267,
    ROUTING_REVIEW_INPUT_SCHEMA_268,
    ROUTING_REVIEW_INPUT_SCHEMA,
}
_CONTEXTUAL_REF_INPUT_SCHEMAS = {
    ROUTING_REVIEW_INPUT_SCHEMA_262,
    ROUTING_REVIEW_INPUT_SCHEMA_263,
    ROUTING_REVIEW_INPUT_SCHEMA_264,
    ROUTING_REVIEW_INPUT_SCHEMA_265,
    ROUTING_REVIEW_INPUT_SCHEMA_266,
    ROUTING_REVIEW_INPUT_SCHEMA_267,
    ROUTING_REVIEW_INPUT_SCHEMA_268,
    ROUTING_REVIEW_INPUT_SCHEMA,
}
_MAP_CONTEXT_INPUT_SCHEMAS = {
    ROUTING_REVIEW_INPUT_SCHEMA_263,
    ROUTING_REVIEW_INPUT_SCHEMA_264,
    ROUTING_REVIEW_INPUT_SCHEMA_265,
    ROUTING_REVIEW_INPUT_SCHEMA_266,
    ROUTING_REVIEW_INPUT_SCHEMA_267,
    ROUTING_REVIEW_INPUT_SCHEMA_268,
    ROUTING_REVIEW_INPUT_SCHEMA,
}


def _window(evidence):
    return evidence["source_bindings"]["window_evidence"]


def _routing(evidence: Mapping[str, Any]) -> Mapping[str, Any] | None:
    routing = evidence.get("evidence_routing") if isinstance(evidence, Mapping) else None
    return routing if isinstance(routing, Mapping) else None


def has_routing(evidence: Mapping[str, Any]) -> bool:
    return _routing(evidence) is not None


def _routing_units(evidence: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    routing = _routing(evidence)
    if routing is None:
        return []
    units = routing.get("evidence_units")
    if units is None:
        units = routing.get("units")
    if not isinstance(units, list):
        raise legacy.ReviewValidationError("evidence routing units missing")
    clean: list[Mapping[str, Any]] = []
    for unit in units:
        if not isinstance(unit, Mapping):
            raise legacy.ReviewValidationError("evidence routing unit must be object")
        if not isinstance(unit.get("evidence_id"), str) or not unit.get("evidence_id"):
            raise legacy.ReviewValidationError("evidence routing unit id missing")
        clean.append(unit)
    return clean


def routing_identity(evidence: Mapping[str, Any]) -> dict[str, Any] | None:
    routing = _routing(evidence)
    if routing is None:
        return None
    schema = routing.get("schema")
    if schema != ROUTING_SCHEMA:
        raise legacy.ReviewValidationError("evidence routing schema missing")
    routing_hash = routing.get("routing_hash") or routing.get("evidence_routing_hash")
    if not isinstance(routing_hash, str) or not routing_hash:
        raise legacy.ReviewValidationError("evidence routing hash missing")
    return {
        "schema": schema,
        "routing_hash": routing_hash,
    }


def prompt_version_for_input(review_input: Mapping[str, Any]) -> str:
    schema = review_input.get("schema")
    if schema == ROUTING_REVIEW_INPUT_SCHEMA_264:
        return EVIDENCE_SYNTHESIS_PROMPT_VERSION_264
    if schema == ROUTING_REVIEW_INPUT_SCHEMA_265:
        return EVIDENCE_SYNTHESIS_PROMPT_VERSION_265
    if schema == ROUTING_REVIEW_INPUT_SCHEMA_266:
        return EVIDENCE_SYNTHESIS_PROMPT_VERSION_266
    if schema == ROUTING_REVIEW_INPUT_SCHEMA_267:
        return EVIDENCE_SYNTHESIS_PROMPT_VERSION_267
    if schema == ROUTING_REVIEW_INPUT_SCHEMA_268:
        return EVIDENCE_SYNTHESIS_PROMPT_VERSION_268
    if schema in _EVIDENCE_POINT_INPUT_SCHEMAS:
        return EVIDENCE_SYNTHESIS_PROMPT_VERSION
    if schema in {ROUTING_REVIEW_INPUT_SCHEMA_262, ROUTING_REVIEW_INPUT_SCHEMA_263}:
        return ROUTING_PROMPT_VERSION
    if schema == ROUTING_REVIEW_INPUT_SCHEMA_261:
        return ROUTING_PROMPT_VERSION_261
    if schema == ROUTING_REVIEW_INPUT_SCHEMA_260:
        return ROUTING_PROMPT_VERSION_260
    return PROMPT_VERSION


def review_state_schema_for_input(review_input: Mapping[str, Any]) -> str:
    schema = review_input.get("schema")
    if schema in {
        ROUTING_REVIEW_INPUT_SCHEMA_262,
        ROUTING_REVIEW_INPUT_SCHEMA_263,
        ROUTING_REVIEW_INPUT_SCHEMA_264,
        ROUTING_REVIEW_INPUT_SCHEMA_265,
        ROUTING_REVIEW_INPUT_SCHEMA_266,
        ROUTING_REVIEW_INPUT_SCHEMA_267,
        ROUTING_REVIEW_INPUT_SCHEMA_268,
        ROUTING_REVIEW_INPUT_SCHEMA,
    }:
        return ROUTING_REVIEW_STATE_SCHEMA
    if schema == ROUTING_REVIEW_INPUT_SCHEMA_261:
        return ROUTING_REVIEW_STATE_SCHEMA_261
    if schema == ROUTING_REVIEW_INPUT_SCHEMA_260:
        return ROUTING_REVIEW_STATE_SCHEMA_260
    return REVIEW_STATE_SCHEMA


def research_review_schema_for_input(review_input: Mapping[str, Any]) -> str:
    schema = review_input.get("schema")
    if schema in {
        ROUTING_REVIEW_INPUT_SCHEMA_262,
        ROUTING_REVIEW_INPUT_SCHEMA_263,
        ROUTING_REVIEW_INPUT_SCHEMA_264,
        ROUTING_REVIEW_INPUT_SCHEMA_265,
        ROUTING_REVIEW_INPUT_SCHEMA_266,
        ROUTING_REVIEW_INPUT_SCHEMA_267,
        ROUTING_REVIEW_INPUT_SCHEMA_268,
        ROUTING_REVIEW_INPUT_SCHEMA,
    }:
        return ROUTING_RESEARCH_REVIEW_SCHEMA
    if schema == ROUTING_REVIEW_INPUT_SCHEMA_261:
        return ROUTING_RESEARCH_REVIEW_SCHEMA_261
    if schema == ROUTING_REVIEW_INPUT_SCHEMA_260:
        return ROUTING_RESEARCH_REVIEW_SCHEMA_260
    return RESEARCH_REVIEW_SCHEMA


def _routing_catalog_schema_for_input_schema(schema: Any) -> str:
    if schema in {
        ROUTING_REVIEW_INPUT_SCHEMA_262,
        ROUTING_REVIEW_INPUT_SCHEMA_263,
        ROUTING_REVIEW_INPUT_SCHEMA_264,
        ROUTING_REVIEW_INPUT_SCHEMA_265,
        ROUTING_REVIEW_INPUT_SCHEMA_266,
        ROUTING_REVIEW_INPUT_SCHEMA_267,
        ROUTING_REVIEW_INPUT_SCHEMA_268,
        ROUTING_REVIEW_INPUT_SCHEMA,
    }:
        return ROUTING_CATALOG_SCHEMA
    if schema == ROUTING_REVIEW_INPUT_SCHEMA_261:
        return ROUTING_CATALOG_SCHEMA_261
    if schema == ROUTING_REVIEW_INPUT_SCHEMA_260:
        return ROUTING_CATALOG_SCHEMA_260
    return ROUTING_CATALOG_SCHEMA_261


def response_protocol_for_input(review_input: Mapping[str, Any]) -> str:
    schema = review_input.get("schema")
    if schema in _EVIDENCE_POINT_INPUT_SCHEMAS:
        return EVIDENCE_POINTS_RESPONSE_PROTOCOL
    if schema in {ROUTING_REVIEW_INPUT_SCHEMA_262, ROUTING_REVIEW_INPUT_SCHEMA_263}:
        return ROUTING_RESPONSE_PROTOCOL
    return RESPONSE_PROTOCOL


def exact_output_keys_for_input(review_input: Mapping[str, Any]) -> list[str]:
    if _supports_evidence_points(review_input):
        return list(EVIDENCE_POINTS_OUTPUT_KEYS)
    if response_protocol_for_input(review_input) == ROUTING_RESPONSE_PROTOCOL:
        return list(GROUNDED_EXPLANATION_OUTPUT_KEYS)
    return list(EXACT_OUTPUT_KEYS)


def _supports_grounded_explanation(review_input: Mapping[str, Any]) -> bool:
    return response_protocol_for_input(review_input) == ROUTING_RESPONSE_PROTOCOL


def _supports_evidence_points(review_input: Mapping[str, Any]) -> bool:
    return response_protocol_for_input(review_input) == EVIDENCE_POINTS_RESPONSE_PROTOCOL


def _validate_review_evidence(evidence: Mapping[str, Any], snapshot: Mapping[str, Any]) -> None:
    try:
        evidence_core.validate_review_evidence(evidence, snapshot)
    except ValueError as exc:
        raise legacy.ReviewValidationError(str(exc)) from exc


def _retarget_review_input_schema(review_input: Mapping[str, Any], schema: str) -> dict[str, Any]:
    data = deepcopy(dict(review_input))
    data["schema"] = schema
    data["projection_hash"] = legacy._hash({key: value for key, value in data.items() if key != "projection_hash"})
    return data


def _without_request_contract(review_input: Mapping[str, Any]) -> dict[str, Any]:
    data = deepcopy(dict(review_input))
    data.pop("request_contract", None)
    return data


def _rebuilt_review_input_for_schema(
    snapshot: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    research_only: bool,
    model: Any,
    schema: Any,
) -> dict[str, Any]:
    if schema in LEGACY_REVIEW_INPUT_SCHEMAS:
        if has_routing(evidence):
            raise legacy.ReviewValidationError("routing evidence cannot replay legacy WPEM review input")
        if evidence.get("schema") not in evidence_core.LEGACY_SCHEMAS:
            raise legacy.ReviewValidationError("legacy WPEM review input requires legacy evidence shape")
        rebuilt = _build_review_input(snapshot, evidence, research_only=research_only, model=model,
                                      routing_review_input_schema=ROUTING_REVIEW_INPUT_SCHEMA)
        return _retarget_review_input_schema(_without_request_contract(rebuilt), str(schema))
    if schema == "review_input@2.5.1":
        if has_routing(evidence):
            raise legacy.ReviewValidationError("routing evidence cannot replay legacy WPEM review input")
        rebuilt = _build_review_input(snapshot, evidence, research_only=research_only, model=model,
                                      routing_review_input_schema=ROUTING_REVIEW_INPUT_SCHEMA)
        return _retarget_review_input_schema(_without_request_contract(rebuilt), str(schema))
    if schema == REVIEW_INPUT_SCHEMA:
        if has_routing(evidence):
            raise legacy.ReviewValidationError("routing evidence cannot replay non-routing WPEM review input")
        rebuilt = _build_review_input(snapshot, evidence, research_only=research_only, model=model,
                                      routing_review_input_schema=ROUTING_REVIEW_INPUT_SCHEMA)
        return _retarget_review_input_schema(rebuilt, str(schema))
    if schema in ROUTING_REVIEW_INPUT_SCHEMAS:
        if not has_routing(evidence):
            raise legacy.ReviewValidationError("routing WPEM review input requires evidence routing")
        return _build_review_input(snapshot, evidence, research_only=research_only, model=model,
                                   routing_review_input_schema=str(schema))
    if schema != REVIEW_INPUT_SCHEMA:
        raise legacy.ReviewValidationError("WPEM review input schema mismatch")
    return _build_review_input(snapshot, evidence, research_only=research_only, model=model,
                               routing_review_input_schema=ROUTING_REVIEW_INPUT_SCHEMA)


def _entry(identifier, ref, text):
    return {"id": identifier, "ref": ref, "claim_cn": text, "text_cn": text}


def _safe_id(value: Any) -> str:
    raw = str(value or "").strip()
    safe = "".join(ch if ch.isalnum() else "_" for ch in raw)
    return "_".join(part for part in safe.split("_") if part) or "unit"


def _unit_text(unit: Mapping[str, Any]) -> str:
    for key in ("reason_cn", "text_cn", "claim_cn", "summary_cn"):
        value = unit.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:350]
    return "路由证据已进入有限目录；只可按其来源、依赖和资格读取。"


def _unit_question_text(unit: Mapping[str, Any], key: str) -> str:
    value = unit.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()[:350]
    return _unit_text(unit)


def _unit_evidence_ids(unit: Mapping[str, Any]) -> list[str]:
    evidence_id = str(unit.get("evidence_id"))
    values = unit.get("evidence_ids")
    if not isinstance(values, list):
        values = []
    out = [evidence_id]
    for value in values:
        if isinstance(value, str) and value and value not in out:
            out.append(value)
    return out


def _unit_block_id(unit: Mapping[str, Any]) -> str:
    for key in ("block_id", "dependency_group", "primary_problem_domain"):
        value = unit.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(unit.get("evidence_id"))


def _routing_ref(value: Any) -> str:
    return ROUTING_ALLOWED_REFS_PREFIX + _safe_id(value)


def _routing_entry(role: str, unit: Mapping[str, Any], *, identifier_seed: Any | None = None,
                   evidence_ids: list[str] | None = None, text: str | None = None,
                   q1_reason_cn: str | None = None, q2_reason_cn: str | None = None,
                   q1_evidence_ids: list[str] | None = None, q2_evidence_ids: list[str] | None = None) -> dict[str, Any]:
    prefix = {"claims": "claim", "counterclaims": "counter", "gaps": "gap", "reassess_conditions": "reassess"}[role]
    evidence_id = str(identifier_seed or unit.get("evidence_id"))
    entry_text = (text or _unit_text(unit)).strip()[:350]
    all_evidence_ids = evidence_ids or _unit_evidence_ids(unit)
    q1_role = str(unit.get("q1_role") or "NONE")
    q2_role = str(unit.get("q2_role") or "NONE")
    q1_reason = (q1_reason_cn or _unit_question_text(unit, "q1_reason_cn")).strip()[:350]
    q2_reason = (q2_reason_cn or _unit_question_text(unit, "q2_reason_cn")).strip()[:350]
    if q1_role.upper() == "NONE" and q1_reason_cn is None:
        q1_reason = ""
    if q2_role.upper() == "NONE" and q2_reason_cn is None:
        q2_reason = ""
    return {
        "id": f"{prefix}.routing.{_safe_id(evidence_id)}",
        "ref": _routing_ref(evidence_id),
        "claim_cn": entry_text,
        "text_cn": entry_text,
        "q1_reason_cn": q1_reason,
        "q2_reason_cn": q2_reason,
        "block_id": _unit_block_id(unit),
        "evidence_ids": all_evidence_ids,
        "q1_evidence_ids": q1_evidence_ids if q1_evidence_ids is not None else (all_evidence_ids if q1_role.upper() != "NONE" else []),
        "q2_evidence_ids": q2_evidence_ids if q2_evidence_ids is not None else (all_evidence_ids if q2_role.upper() != "NONE" else []),
        "q1_role": q1_role,
        "q2_role": q2_role,
    }


def _routing_roles(unit: Mapping[str, Any]) -> tuple[str, str, str]:
    q1_role = str(unit.get("q1_role") or "NONE").upper()
    q2_role = str(unit.get("q2_role") or "NONE").upper()
    information = str(unit.get("information_increment") or "").upper()
    qualification = str(unit.get("qualification_status") or "").upper()
    return q1_role, q2_role, information if information else qualification


def _routing_selected_role(unit: Mapping[str, Any]) -> str | None:
    q1_role, q2_role, information = _routing_roles(unit)
    if "REASSESS" in {q1_role, q2_role}:
        return "reassess_conditions"
    if information == "MISSING":
        return "gaps"
    if q1_role in ROUTING_COUNTER_ROLES or q2_role in ROUTING_COUNTER_ROLES or information in ROUTING_INFORMATION_COUNTERS:
        return "counterclaims"
    if q1_role in ROUTING_SUPPORT_ROLES or q2_role in ROUTING_SUPPORT_ROLES:
        return "claims"
    return None


def _merged_text(units: list[Mapping[str, Any]]) -> str:
    texts: list[str] = []
    for unit in units:
        text = _unit_text(unit)
        if text not in texts:
            texts.append(text)
    return "；".join(texts)[:350]


def _merged_evidence_ids(units: list[Mapping[str, Any]], role_key: str | None = None) -> list[str]:
    out: list[str] = []
    for unit in units:
        if role_key is not None and str(unit.get(role_key) or "NONE").upper() == "NONE":
            continue
        for evidence_id in _unit_evidence_ids(unit):
            if evidence_id not in out:
                out.append(evidence_id)
    return out


def _merged_question_text(units: list[Mapping[str, Any]], reason_key: str, role_key: str) -> str:
    texts: list[str] = []
    for unit in units:
        if str(unit.get(role_key) or "NONE").upper() == "NONE":
            continue
        text = _unit_question_text(unit, reason_key)
        if text not in texts:
            texts.append(text)
    return "；".join(texts)[:350] if texts else ""


def _merged_role(units: list[Mapping[str, Any]], key: str) -> str:
    values = [str(unit.get(key) or "NONE").upper() for unit in units]
    for preferred in ("REASSESS", "COUNTER", "LIMITATION", "PRIMARY", "SUPPORT"):
        if preferred in values:
            return preferred
    return values[0] if values else "NONE"


def _routing_entry_priority(entry: Mapping[str, Any], role: str) -> tuple[int, int, str]:
    q1_role = str(entry.get("q1_role") or "NONE").upper()
    q2_role = str(entry.get("q2_role") or "NONE").upper()
    text = str(entry.get("text_cn") or entry.get("claim_cn") or "")
    evidence_count = len(entry.get("evidence_ids") or [])
    if role == "counterclaims":
        priority = 0 if "COUNTER" in {q1_role, q2_role} else 1
    elif role == "gaps":
        priority = 0 if "不得" in text or "不可用" in text or "missing" in text.lower() else 1
    elif role == "claims":
        priority = 0 if "PRIMARY" in {q1_role, q2_role} else 1
    else:
        priority = 0 if "REASSESS" in {q1_role, q2_role} else 1
    return (priority, -evidence_count, str(entry.get("id") or ""))


def _routing_required_from_entries(entries_by_role: Mapping[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    mapping = {
        "claims": "claim_ids",
        "counterclaims": "counter_ids",
        "gaps": "gap_ids",
        "reassess_conditions": "reassess_condition_ids",
    }
    required: dict[str, list[str]] = {value: [] for value in mapping.values()}
    for role, key in mapping.items():
        entries = sorted(entries_by_role.get(role) or [], key=lambda item: _routing_entry_priority(item, role))
        required[key] = [item["id"] for item in entries[:ROUTING_REQUIRED_LIMITS[key]]]
    return required


def _routing_unit_index(routing: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    units = routing.get("units")
    if units is None:
        units = routing.get("evidence_units")
    if not isinstance(units, list):
        return {}
    out: dict[str, Mapping[str, Any]] = {}
    for unit in units:
        if isinstance(unit, Mapping) and isinstance(unit.get("evidence_id"), str):
            out[unit["evidence_id"]] = unit
    return out


def _normalise_routing_summary_entry(role: str, item: Mapping[str, Any],
                                     units_by_id: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    if not isinstance(item.get("id"), str) or not item.get("id"):
        raise legacy.ReviewValidationError("routing selection summary id missing")
    text = item.get("text_cn") or item.get("claim_cn")
    if not isinstance(text, str) or not text.strip():
        raise legacy.ReviewValidationError("routing selection summary text missing")
    evidence_ids = item.get("evidence_ids")
    if not isinstance(evidence_ids, list) or not all(isinstance(value, str) and value for value in evidence_ids):
        raise legacy.ReviewValidationError("routing selection summary evidence ids missing")
    unit_rows = [units_by_id[value] for value in evidence_ids if value in units_by_id]
    q1_evidence_ids = item.get("q1_evidence_ids")
    if not isinstance(q1_evidence_ids, list):
        q1_evidence_ids = _merged_evidence_ids(unit_rows, "q1_role") if unit_rows else (
            evidence_ids if str(item.get("q1_role") or "NONE").upper() != "NONE" else []
        )
    q2_evidence_ids = item.get("q2_evidence_ids")
    if not isinstance(q2_evidence_ids, list):
        q2_evidence_ids = _merged_evidence_ids(unit_rows, "q2_role") if unit_rows else (
            evidence_ids if str(item.get("q2_role") or "NONE").upper() != "NONE" else []
        )
    block_id = item.get("block_id")
    if not isinstance(block_id, str) or not block_id.strip():
        raise legacy.ReviewValidationError("routing selection summary block id missing")
    ref = item.get("ref")
    if not isinstance(ref, str) or not ref:
        ref = _routing_ref(item["id"])
    q1_role = str(item.get("q1_role") or "NONE").upper()
    q2_role = str(item.get("q2_role") or "NONE").upper()
    q1_reason = str(item.get("q1_reason_cn") or (
        _merged_question_text(unit_rows, "q1_reason_cn", "q1_role") if unit_rows else text
    )).strip()[:350]
    q2_reason = str(item.get("q2_reason_cn") or (
        _merged_question_text(unit_rows, "q2_reason_cn", "q2_role") if unit_rows else text
    )).strip()[:350]
    if q1_role == "NONE" and item.get("q1_reason_cn") is None:
        q1_reason = ""
    if q2_role == "NONE" and item.get("q2_reason_cn") is None:
        q2_reason = ""
    return {
        "id": item["id"],
        "ref": ref,
        "claim_cn": text.strip()[:350],
        "text_cn": text.strip()[:350],
        "q1_reason_cn": q1_reason,
        "q2_reason_cn": q2_reason,
        "block_id": block_id.strip(),
        "evidence_ids": list(dict.fromkeys(evidence_ids)),
        "q1_evidence_ids": list(dict.fromkeys(str(value) for value in q1_evidence_ids if isinstance(value, str) and value)),
        "q2_evidence_ids": list(dict.fromkeys(str(value) for value in q2_evidence_ids if isinstance(value, str) and value)),
        "q1_role": q1_role,
        "q2_role": q2_role,
    }


def _merge_entry_text(entries: list[Mapping[str, Any]], key: str) -> str:
    texts: list[str] = []
    for entry in entries:
        text = str(entry.get(key) or ("" if key in {"q1_reason_cn", "q2_reason_cn"} else entry.get("text_cn")) or "")
        if text and text not in texts:
            texts.append(text)
    return "；".join(texts)[:350]


def _merge_entry_ids(entries: list[Mapping[str, Any]], key: str) -> list[str]:
    out: list[str] = []
    for entry in entries:
        for value in entry.get(key) or []:
            if isinstance(value, str) and value and value not in out:
                out.append(value)
    return out


def _merge_entry_role(entries: list[Mapping[str, Any]], key: str) -> str:
    values = [str(entry.get(key) or "NONE").upper() for entry in entries]
    for preferred in ("REASSESS", "COUNTER", "LIMITATION", "PRIMARY", "SUPPORT"):
        if preferred in values:
            return preferred
    return values[0] if values else "NONE"


def _aggregate_entry(role: str, identifier: str, ref: str, block_id: str,
                     entries: list[Mapping[str, Any]], label_cn: str) -> dict[str, Any]:
    text = _merge_entry_text(entries, "text_cn")
    q1_text = _merge_entry_text(entries, "q1_reason_cn")
    q2_text = _merge_entry_text(entries, "q2_reason_cn")
    if label_cn:
        text = (label_cn + "：" + text)[:350]
        q1_text = (label_cn + "：" + q1_text)[:350] if q1_text else text
        q2_text = (label_cn + "：" + q2_text)[:350] if q2_text else text
    return {
        "id": identifier,
        "ref": ref,
        "claim_cn": text,
        "text_cn": text,
        "q1_reason_cn": q1_text or text,
        "q2_reason_cn": q2_text or text,
        "block_id": block_id,
        "evidence_ids": _merge_entry_ids(entries, "evidence_ids"),
        "q1_evidence_ids": _merge_entry_ids(entries, "q1_evidence_ids"),
        "q2_evidence_ids": _merge_entry_ids(entries, "q2_evidence_ids"),
        "q1_role": _merge_entry_role(entries, "q1_role"),
        "q2_role": _merge_entry_role(entries, "q2_role"),
    }


def _merge_entries_by_block(entries_by_role: Mapping[str, list[dict[str, Any]]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    merged_by_role: dict[str, list[dict[str, Any]]] = {role: [] for role in ROUTING_ROLE_KEYS}
    aliases: dict[str, str] = {}
    for role in ROUTING_ROLE_KEYS:
        groups: dict[str, list[dict[str, Any]]] = {}
        for entry in entries_by_role.get(role) or []:
            groups.setdefault(str(entry["block_id"]), []).append(entry)
        for block_id, entries in sorted(groups.items(), key=lambda item: item[0]):
            first = deepcopy(entries[0])
            for entry in entries:
                aliases[entry["id"]] = first["id"]
            first["claim_cn"] = _merge_entry_text(entries, "claim_cn")
            first["text_cn"] = _merge_entry_text(entries, "text_cn")
            first["q1_reason_cn"] = _merge_entry_text(entries, "q1_reason_cn")
            first["q2_reason_cn"] = _merge_entry_text(entries, "q2_reason_cn")
            first["evidence_ids"] = _merge_entry_ids(entries, "evidence_ids")
            first["q1_evidence_ids"] = _merge_entry_ids(entries, "q1_evidence_ids")
            first["q2_evidence_ids"] = _merge_entry_ids(entries, "q2_evidence_ids")
            first["q1_role"] = _merge_entry_role(entries, "q1_role")
            first["q2_role"] = _merge_entry_role(entries, "q2_role")
            first["block_id"] = block_id
            merged_by_role[role].append(first)
    return merged_by_role, aliases


def _aggregate_routing_catalog(entries_by_role: Mapping[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    claims = list(entries_by_role.get("claims") or [])
    mechanism_claims = [entry for entry in claims if entry.get("block_id") in ROUTING_MECHANISM_BLOCKS]
    if not mechanism_claims:
        mechanism_claims = [entry for entry in claims if entry.get("block_id") not in ROUTING_LOW_VALUE_SUPPORT_BLOCKS]
    if not mechanism_claims and claims:
        mechanism_claims = [sorted(claims, key=lambda item: _routing_entry_priority(item, "claims"))[0]]

    counters = list(entries_by_role.get("counterclaims") or [])
    gaps = list(entries_by_role.get("gaps") or [])
    limitation_gaps = [entry for entry in gaps if entry.get("block_id") in ROUTING_LIMITATION_BLOCKS]
    if not limitation_gaps:
        limitation_gaps = gaps[:1]
    reassess = list(entries_by_role.get("reassess_conditions") or [])

    out = {role: [] for role in ROUTING_ROLE_KEYS}
    if mechanism_claims:
        out["claims"].append(_aggregate_entry(
            "claims",
            "claim.routing.mechanism_support",
            "evidence_routing.mechanism_support",
            "routing.mechanism_support",
            sorted(mechanism_claims, key=lambda item: _routing_entry_priority(item, "claims")),
            "路由机制支持",
        ))
    if counters:
        out["counterclaims"].append(_aggregate_entry(
            "counterclaims",
            "counter.routing.primary_counter",
            "evidence_routing.primary_counter",
            "routing.primary_counter",
            sorted(counters, key=lambda item: _routing_entry_priority(item, "counterclaims")),
            "路由主要反证",
        ))
    if limitation_gaps:
        out["gaps"].append(_aggregate_entry(
            "gaps",
            "gap.routing.key_limitations",
            "evidence_routing.key_limitations",
            "routing.key_limitations",
            sorted(limitation_gaps, key=lambda item: _routing_entry_priority(item, "gaps")),
            "路由关键限制",
        ))
    if reassess:
        out["reassess_conditions"].append(_aggregate_entry(
            "reassess_conditions",
            "reassess.routing.changed_evidence",
            "evidence_routing.changed_evidence",
            "routing.changed_evidence",
            sorted(reassess, key=lambda item: _routing_entry_priority(item, "reassess_conditions")),
            "路由复核条件",
        ))
    return out


def _normalise_routing_required(summary: Mapping[str, Any], entries_by_role: Mapping[str, list[dict[str, Any]]],
                                aliases: Mapping[str, str] | None = None) -> dict[str, list[str]]:
    required = summary.get("required_selection") if isinstance(summary.get("required_selection"), Mapping) else None
    if required is None:
        return _routing_required_from_entries(entries_by_role)
    role_to_key = {
        "claims": "claim_ids",
        "counterclaims": "counter_ids",
        "gaps": "gap_ids",
        "reassess_conditions": "reassess_condition_ids",
    }
    out: dict[str, list[str]] = {}
    for role, key in role_to_key.items():
        existing = {entry["id"] for entry in entries_by_role.get(role) or []}
        values = required.get(key) or []
        if not isinstance(values, list):
            raise legacy.ReviewValidationError("routing required selection must be list")
        selected = [str((aliases or {}).get(str(value), str(value))) for value in values
                    if str((aliases or {}).get(str(value), str(value))) in existing]
        if len(selected) > ROUTING_REQUIRED_LIMITS[key]:
            raise legacy.ReviewValidationError("routing required selection exceeds bounded capacity")
        out[key] = list(dict.fromkeys(selected))
    return out


def _catalog_from_routing_summary(summary: Mapping[str, Any], routing: Mapping[str, Any]) -> dict[str, Any] | None:
    if not isinstance(summary, Mapping):
        return None
    units_by_id = _routing_unit_index(routing)
    role_aliases = {
        "claims": ("claims",),
        "counterclaims": ("counterclaims", "counters"),
        "gaps": ("gaps",),
        "reassess_conditions": ("reassess_conditions", "reassess"),
    }
    entries_by_role: dict[str, list[dict[str, Any]]] = {}
    for role in ROUTING_ROLE_KEYS:
        values = None
        for source_key in role_aliases[role]:
            if summary.get(source_key) is not None:
                values = summary.get(source_key)
                break
        if values is None:
            values = []
        if not isinstance(values, list):
            raise legacy.ReviewValidationError("routing selection summary role must be list")
        entries_by_role[role] = [
            _normalise_routing_summary_entry(role, item, units_by_id)
            for item in values if isinstance(item, Mapping)
        ]
    entries_by_role, aliases = _merge_entries_by_block(entries_by_role)
    representatives: dict[str, str] = {}
    for role in ROUTING_ROLE_KEYS:
        for entry in entries_by_role[role]:
            representatives.setdefault(entry["block_id"], entry["id"])
    entries_by_role = _aggregate_routing_catalog(entries_by_role)
    representatives = {}
    for role in ROUTING_ROLE_KEYS:
        for entry in entries_by_role[role]:
            representatives.setdefault(entry["block_id"], entry["id"])
    return {
        "schema": str(summary.get("schema") or "evidence_routing_selection_summary@1.0.0"),
        "claims": entries_by_role["claims"],
        "counterclaims": entries_by_role["counterclaims"],
        "gaps": entries_by_role["gaps"],
        "reassess_conditions": entries_by_role["reassess_conditions"],
        "dependency_block_representatives": representatives,
        "required_selection": _routing_required_from_entries(entries_by_role),
    }


def _routing_catalog(evidence: Mapping[str, Any]) -> dict[str, Any]:
    routing = _routing(evidence) or {}
    summary_catalog = _catalog_from_routing_summary(routing.get("selection_summary"), routing)
    if summary_catalog is not None:
        return summary_catalog
    entries_by_role: dict[str, list[dict[str, Any]]] = {
        "claims": [],
        "counterclaims": [],
        "gaps": [],
        "reassess_conditions": [],
    }
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    first_seed: dict[tuple[str, str], str] = {}
    first_unit: dict[tuple[str, str], Mapping[str, Any]] = {}
    for unit in _routing_units(evidence):
        selected_role = _routing_selected_role(unit)
        if selected_role is None:
            continue
        block_id = _unit_block_id(unit)
        key = (block_id, selected_role)
        grouped.setdefault(key, []).append(unit)
        first_seed.setdefault(key, str(unit.get("evidence_id")))
        first_unit.setdefault(key, unit)
    for (block_id, selected_role), units in grouped.items():
        seed = first_seed[(block_id, selected_role)]
        base = dict(first_unit[(block_id, selected_role)])
        base["block_id"] = block_id
        base["q1_role"] = _merged_role(units, "q1_role")
        base["q2_role"] = _merged_role(units, "q2_role")
        entry = _routing_entry(
            selected_role,
            base,
            identifier_seed=seed,
            evidence_ids=_merged_evidence_ids(units),
            text=_merged_text(units),
            q1_reason_cn=_merged_question_text(units, "q1_reason_cn", "q1_role"),
            q2_reason_cn=_merged_question_text(units, "q2_reason_cn", "q2_role"),
            q1_evidence_ids=_merged_evidence_ids(units, "q1_role"),
            q2_evidence_ids=_merged_evidence_ids(units, "q2_role"),
        )
        entries_by_role[selected_role].append(entry)
    representatives: dict[str, str] = {}
    for role in ROUTING_ROLE_KEYS:
        entries_by_role[role].sort(key=lambda item: str(item.get("id") or ""))
        for entry in entries_by_role[role]:
            representatives.setdefault(entry["block_id"], entry["id"])
    entries_by_role = _aggregate_routing_catalog(entries_by_role)
    representatives = {}
    for role in ROUTING_ROLE_KEYS:
        for entry in entries_by_role[role]:
            representatives.setdefault(entry["block_id"], entry["id"])
    required = _routing_required_from_entries(entries_by_role)
    return {
        "schema": "evidence_routing_selection_summary@1.0.0",
        "claims": entries_by_role["claims"],
        "counterclaims": entries_by_role["counterclaims"],
        "gaps": entries_by_role["gaps"],
        "reassess_conditions": entries_by_role["reassess_conditions"],
        "dependency_block_representatives": representatives,
        "required_selection": required,
    }


def _catalog_refs(catalog: Mapping[str, Any]) -> set[str]:
    refs = set()
    for role in ("claims", "counterclaims", "gaps", "reassess_conditions"):
        for item in catalog.get(role) or []:
            if isinstance(item, Mapping) and isinstance(item.get("ref"), str):
                refs.add(item["ref"])
    return refs


def _append_text_prefix(value: Any, prefix: str, *, limit: int = 350) -> str:
    text = str(value or "").strip()
    if prefix in text:
        return text[:limit]
    if text:
        return (prefix + "；" + text)[:limit]
    return prefix[:limit]


def _space_gate_limitation_unit(routing: Mapping[str, Any]) -> Mapping[str, Any] | None:
    units = routing.get("units")
    if units is None:
        units = routing.get("evidence_units")
    if not isinstance(units, list):
        return None
    for unit in units:
        if not isinstance(unit, Mapping):
            continue
        asset = str(unit.get("source_asset") or "")
        evidence_id = str(unit.get("evidence_id") or "")
        group = str(unit.get("dependency_group") or "").upper()
        parents = unit.get("dependency_parent_refs") if isinstance(unit.get("dependency_parent_refs"), list) else []
        q_roles = {str(unit.get("q1_role") or "NONE").upper(), str(unit.get("q2_role") or "NONE").upper()}
        information = str(unit.get("information_increment") or "").upper()
        if asset != "space_gate_gamma_regime" and "space_gate_gamma_regime" not in evidence_id:
            continue
        if group != "OPTION_SPACE" and "anchor_effective_flip" not in parents:
            continue
        if q_roles & ROUTING_COUNTER_ROLES or information in ROUTING_INFORMATION_COUNTERS:
            return unit
    return None


def _supplement_space_gate_limitation(catalog: dict[str, Any], routing: Mapping[str, Any]) -> None:
    unit = _space_gate_limitation_unit(routing)
    if unit is None:
        return
    evidence_id = unit.get("evidence_id")
    if not isinstance(evidence_id, str) or not evidence_id:
        return
    gaps = catalog.get("gaps") if isinstance(catalog.get("gaps"), list) else []
    target = next((item for item in gaps if isinstance(item, dict) and item.get("id") == "gap.routing.key_limitations"), None)
    if target is None:
        return
    if evidence_id not in (target.get("evidence_ids") or []):
        target.setdefault("evidence_ids", []).append(evidence_id)
    if str(unit.get("q1_role") or "NONE").upper() != "NONE" and evidence_id not in (target.get("q1_evidence_ids") or []):
        target.setdefault("q1_evidence_ids", []).append(evidence_id)
    if str(unit.get("q2_role") or "NONE").upper() != "NONE" and evidence_id not in (target.get("q2_evidence_ids") or []):
        target.setdefault("q2_evidence_ids", []).append(evidence_id)
    prefix = "同源空间限制：SpaceGate/GGR 读数作为限制保留；同源 Anchor 支持不能当作独立确认。"
    unit_reason = _unit_question_text(unit, "q1_reason_cn")
    if unit_reason:
        prefix = _append_text_prefix(unit_reason, "同源空间限制")
    for key in ("claim_cn", "text_cn", "q1_reason_cn"):
        target[key] = _append_text_prefix(target.get(key), prefix)


def _contextual_market_evidence_ids(evidence: Mapping[str, Any]) -> list[str]:
    wanted = {
        "tmv_price_path",
        "funding_context",
        "srd_skew_aggregate",
        "macro_pressure",
        "active_flow_cvd",
        "active_flow_cvd_unqualified",
        "space_gate_gamma_regime",
    }
    out: list[str] = []
    for unit in _routing_units(evidence):
        if unit.get("source_asset") in wanted and isinstance(unit.get("evidence_id"), str):
            if unit["evidence_id"] not in out:
                out.append(unit["evidence_id"])
    return out


def _context_text_for_asset(source_asset: str, readings: Mapping[str, Any]) -> str:
    if source_asset == "tmv_price_path":
        h24 = readings.get("tmvf_24h") if isinstance(readings.get("tmvf_24h"), Mapping) else {}
        h48 = readings.get("tmvf_48h") if isinstance(readings.get("tmvf_48h"), Mapping) else {}
        return (
            "TMV原读数：" + _fmt_kv(
                ("direction", readings.get("direction")),
                ("blend", readings.get("tmv_blend")),
                ("24h", f"{h24.get('final_state')}/{h24.get('tmv_final')}" if h24 else None),
                ("48h", f"{h48.get('final_state')}/{h48.get('tmv_final')}" if h48 else None),
            ) + "；"
            "它是价格路径背景，不作为第二个主动流投票。"
        )[:350]
    if source_asset == "funding_context":
        semantics = readings.get("semantics") if isinstance(readings.get("semantics"), Mapping) else {}
        return (
            "Funding原读数：" + _fmt_kv(
                ("last_rate", readings.get("last_rate")),
                ("effect", readings.get("effect")),
                ("crowding", semantics.get("crowding_state")),
                ("raw_rate", semantics.get("raw_funding_rate")),
            ) + "；"
            "它是费用/拥挤背景，不自动给Q1方向或Q2补偿投票。"
        )[:350]
    if source_asset == "srd_skew_aggregate":
        return (
            "Skew原读数：" + _fmt_kv(
                ("data_state", readings.get("data_state")),
                ("rr_blend", readings.get("rr_blend")),
                ("skew_norm", readings.get("skew_norm_blend")),
                ("vote", readings.get("vote")),
                ("confidence", readings.get("vote_confidence")),
            ) + "；作为期权需求背景。"
        )[:350]
    if source_asset == "macro_pressure":
        components = readings.get("component_summaries") if isinstance(readings.get("component_summaries"), list) else []
        compact_components = ",".join(
            f"{row.get('key')} cur={row.get('current_close')}@{row.get('current_ts_ms')} ref={row.get('reference_close')}@{row.get('reference_ts_ms')} score={row.get('scoring_value')}{row.get('scoring_unit')}"
            for row in components if isinstance(row, Mapping)
        )
        return (
            "Macro原读数：" + _fmt_kv(
                ("data_status", readings.get("data_status")),
                ("last_data_time", readings.get("last_data_time")),
                ("score", readings.get("macro_score")),
                ("components", compact_components),
            ) + "；producer若未分类，不由此推出完整回路。"
        )[:350]
    if source_asset in {"active_flow_cvd", "active_flow_cvd_unqualified"}:
        combined = readings.get("combined") if isinstance(readings.get("combined"), Mapping) else {}
        fast = readings.get("fast_4h") if isinstance(readings.get("fast_4h"), Mapping) else {}
        slow = readings.get("slow_12h") if isinstance(readings.get("slow_12h"), Mapping) else {}
        return (
            "主动流原读数：" + _fmt_kv(
                ("combined.direction", combined.get("direction")),
                ("data_ready", combined.get("data_ready")),
                ("ready_horizons", combined.get("ready_horizons")),
                ("score", combined.get("score")),
                ("4h", f"ready={fast.get('data_ready')}/cvd={fast.get('cvd_sum')}{fast.get('cvd_unit')}/mom={fast.get('momentum_return_pct')}" if fast else None),
                ("12h", f"ready={slow.get('data_ready')}/cvd={slow.get('cvd_sum')}{slow.get('cvd_unit')}/mom={slow.get('momentum_return_pct')}" if slow else None),
            ) + "；按producer资格有限使用。"
        )[:350]
    if source_asset == "space_gate_gamma_regime":
        gate = readings.get("space_gate") if isinstance(readings.get("space_gate"), Mapping) else {}
        gamma = readings.get("gamma_regime") if isinstance(readings.get("gamma_regime"), Mapping) else {}
        anchor = readings.get("space_context_anchor") if isinstance(readings.get("space_context_anchor"), Mapping) else {}
        return (
            "空间门原读数：" + _fmt_kv(
                ("SpaceGate", gate.get("state")),
                ("GGR", gamma.get("regime")),
                ("flip", anchor.get("effective_flip_point")),
                ("reasons", gate.get("reason_codes")),
            ) + "；"
            "与Anchor同源时保留限制，不能当独立确认。"
        )[:350]
    return "背景原读数已保留；仅作条件解释，不新增概率、硬门或投票。"


def _append_contextual_catalog_entry(catalog: dict[str, Any], evidence: Mapping[str, Any],
                                     contextual_market_reads: Mapping[str, Any] | None) -> None:
    evidence_ids = _contextual_market_evidence_ids(evidence)
    if not evidence_ids:
        return
    claims = catalog.get("claims") if isinstance(catalog.get("claims"), list) else []
    if any(isinstance(item, Mapping) and item.get("id") == "claim.routing.contextual_market_readings" for item in claims):
        return
    items = contextual_market_reads.get("items") if isinstance(contextual_market_reads, Mapping) else []
    by_asset = {
        str(item.get("source_asset")): str(item.get("text_cn"))
        for item in items if isinstance(item, Mapping) and item.get("source_asset") and item.get("text_cn")
    }
    texts = [str(item.get("text_cn")) for item in items if isinstance(item, Mapping) and item.get("text_cn")]
    q1_assets = ("active_flow_cvd", "active_flow_cvd_unqualified", "tmv_price_path", "space_gate_gamma_regime", "macro_pressure")
    q2_assets = ("funding_context", "srd_skew_aggregate", "space_gate_gamma_regime", "macro_pressure")
    q1_parts = [by_asset[asset] for asset in q1_assets if asset in by_asset]
    q2_parts = [by_asset[asset] for asset in q2_assets if asset in by_asset]
    text = ("；".join(texts[:4]) or
            "背景读数保留原 source_ref、clock 与 producer label；不新增概率、门、评分或独立投票。")[:350]
    claims.append({
        "id": "claim.routing.contextual_market_readings",
        "ref": "evidence_routing.contextual_market_reads",
        "claim_cn": text,
        "text_cn": text,
        "q1_reason_cn": ("；".join(q1_parts) or text)[:350],
        "q2_reason_cn": ("；".join(q2_parts) or text)[:350],
        "block_id": "routing.contextual_market_reads",
        "evidence_ids": evidence_ids,
        "q1_evidence_ids": evidence_ids,
        "q2_evidence_ids": evidence_ids,
        "q1_role": "OBSERVATION",
        "q2_role": "OBSERVATION",
    })
    catalog["claims"] = claims


def _compact_routing_projection(evidence: Mapping[str, Any], *, include_contextual: bool = False,
                                contextual_market_reads: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    identity = routing_identity(evidence)
    if identity is None:
        return None
    catalog = _routing_catalog(evidence)
    if include_contextual:
        routing = _routing(evidence) or {}
        _supplement_space_gate_limitation(catalog, routing)
        _append_contextual_catalog_entry(catalog, evidence, contextual_market_reads)
    representatives = []
    entries = {}
    for role in ("claims", "counterclaims", "gaps", "reassess_conditions"):
        for item in catalog[role]:
            entries[item["id"]] = {
                "id": item["id"],
                "ref": item["ref"],
                "block_id": item["block_id"],
                "evidence_ids": deepcopy(item["evidence_ids"]),
                "q1_evidence_ids": deepcopy(item.get("q1_evidence_ids") or []),
                "q2_evidence_ids": deepcopy(item.get("q2_evidence_ids") or []),
                "q1_role": item["q1_role"],
                "q2_role": item["q2_role"],
                "role": role,
            }
    seen_blocks: set[tuple[str, str]] = set()
    for item in entries.values():
        representative_key = (item["block_id"], item["role"])
        if representative_key in seen_blocks:
            continue
        seen_blocks.add(representative_key)
        representatives.append(item)
    return {
        "schema": "evidence_routing_review_projection@1.0.0",
        "routing_schema": identity["schema"],
        "routing_hash": identity["routing_hash"],
        "dependency_representatives": representatives,
        "selection_summary": catalog,
    }


def _compact_dependencies_for_routing(dependencies: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "read_count": dependencies.get("read_count"),
        "unique_update_group_count": dependencies.get("unique_update_group_count"),
        "dependency_counts": deepcopy(dependencies.get("dependency_counts") or {}),
        "reread_is_not_update": dependencies.get("reread_is_not_update"),
    }


def _numeric_ms(value: Any) -> int | float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def _packet_decision_asof_ms(packet: Mapping[str, Any]) -> int | float | None:
    cutoff = packet.get("cutoff") if isinstance(packet.get("cutoff"), Mapping) else {}
    return _numeric_ms(cutoff.get("decision_asof_ms"))


def _current_decision_cutoff_ms(evidence: Mapping[str, Any]) -> int | float | None:
    current = _numeric_ms(evidence.get("decision_asof_ms"))
    if current is not None:
        return current
    bindings = evidence.get("source_bindings") if isinstance(evidence.get("source_bindings"), Mapping) else {}
    packet = bindings.get("current_packet") if isinstance(bindings.get("current_packet"), Mapping) else {}
    return _packet_decision_asof_ms(packet)


def _compact_source_receipt_times(sources: Mapping[str, Any]) -> list[dict[str, Any]]:
    receipts = sources.get("acquisition_receipts") if isinstance(sources.get("acquisition_receipts"), Mapping) else {}
    out: list[dict[str, Any]] = []
    for record_id in sorted(receipts):
        receipt = receipts.get(record_id)
        if not isinstance(receipt, Mapping):
            continue
        out.append({
            "record_id": record_id,
            "meaning": receipt.get("meaning"),
            "time": deepcopy(receipt.get("time") if isinstance(receipt.get("time"), Mapping) else {}),
        })
    return out


def _same_candidate_observed_points(evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    composition = evidence.get("composition_inputs") if isinstance(evidence.get("composition_inputs"), Mapping) else {}
    packets = composition.get("packet_sequence") if isinstance(composition.get("packet_sequence"), list) else []
    candidate_id = evidence.get("candidate_id")
    cutoff_ms = _current_decision_cutoff_ms(evidence)
    if not isinstance(candidate_id, str) or cutoff_ms is None:
        return []
    points: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any]] = set()
    for packet in packets:
        if not isinstance(packet, Mapping):
            continue
        snapshot = packet.get("snapshot") if isinstance(packet.get("snapshot"), Mapping) else {}
        if snapshot.get("candidate_id") != candidate_id:
            continue
        decision_asof_ms = _packet_decision_asof_ms(packet)
        if decision_asof_ms is None or decision_asof_ms > cutoff_ms:
            continue
        key = (snapshot.get("snapshot_id"), packet.get("packet_hash"), decision_asof_ms)
        if key in seen:
            continue
        seen.add(key)
        market = snapshot.get("market") if isinstance(snapshot.get("market"), Mapping) else {}
        quote = market.get("quote") if isinstance(market.get("quote"), Mapping) else {}
        contract = snapshot.get("contract") if isinstance(snapshot.get("contract"), Mapping) else {}
        short_leg = contract.get("short") if isinstance(contract.get("short"), Mapping) else {}
        long_leg = contract.get("long") if isinstance(contract.get("long"), Mapping) else {}
        economics = snapshot.get("economics") if isinstance(snapshot.get("economics"), Mapping) else {}
        fee = snapshot.get("fee") if isinstance(snapshot.get("fee"), Mapping) else {}
        risk = snapshot.get("risk") if isinstance(snapshot.get("risk"), Mapping) else {}
        fact = snapshot.get("fact_context") if isinstance(snapshot.get("fact_context"), Mapping) else {}
        sources = packet.get("sources") if isinstance(packet.get("sources"), Mapping) else {}
        versions = packet.get("versions") if isinstance(packet.get("versions"), Mapping) else {}
        points.append({
            "snapshot_id": snapshot.get("snapshot_id"),
            "packet_hash": packet.get("packet_hash"),
            "decision_asof_ms": decision_asof_ms,
            "market": {
                "reference_price_usd": market.get("reference_price_usd"),
                "reference_source": market.get("reference_source"),
                "valuation_ts_ms": market.get("valuation_ts_ms"),
                "quote": _pick_mapping(quote, (
                    "kind", "credit_btc", "short_sale_btc", "long_purchase_btc",
                    "received_ts_ms", "valuation_ts_ms", "exchange_times_ms",
                    "leg_received_ts_ms", "source_book_hash", "gap_reasons",
                    "executable_or_filled",
                )),
            },
            "contract": {
                "side": contract.get("side"),
                "quantity_btc": contract.get("quantity_btc"),
                "short_strike_usd": contract.get("short_strike_usd"),
                "long_strike_usd": contract.get("long_strike_usd"),
                "width_usd": contract.get("width_usd"),
                "expiry_ms": contract.get("expiry_ms"),
                "short_instrument": contract.get("short_instrument"),
                "long_instrument": contract.get("long_instrument"),
                "short": _pick_mapping(short_leg, (
                    "instrument_name", "strike_usd", "expiry_ms",
                    "option_type", "quantity_unit", "contract_size",
                )),
                "long": _pick_mapping(long_leg, (
                    "instrument_name", "strike_usd", "expiry_ms",
                    "option_type", "quantity_unit", "contract_size",
                )),
            },
            "economics": {
                "visible_credit_btc": economics.get("visible_credit_btc"),
                "fee_btc": fee.get("amount_btc"),
                "entry_fee_btc": fee.get("entry_fee_btc"),
                "delivery_fee_btc": fee.get("delivery_fee_btc"),
                "net_credit_after_entry_fee_btc": economics.get("net_credit_after_entry_fee_btc"),
                "net_credit_after_fee_btc": economics.get("net_credit_after_fee_btc"),
            },
            "source_identity": {
                "fact_source_hash": fact.get("source_hash"),
                "raw_payload_hash": sources.get("raw_payload_hash"),
                "source_manifest_hashes": deepcopy(versions.get("source_manifest_hashes") or []),
                "record_ids": deepcopy(sources.get("record_ids") or []),
            },
            "source_clock": {
                "decision_asof_ms": decision_asof_ms,
                "fact_snapshot_ts_ms": fact.get("snapshot_ts_ms"),
                "fact_price_observed_ms": fact.get("price_observed_ms"),
                "fact_valuation_ts_ms": fact.get("valuation_ts_ms"),
                "market_valuation_ts_ms": market.get("valuation_ts_ms"),
                "quote_received_ts_ms": quote.get("received_ts_ms"),
                "quote_valuation_ts_ms": quote.get("valuation_ts_ms"),
                "quote_exchange_times_ms": deepcopy(quote.get("exchange_times_ms")),
                "quote_leg_received_ts_ms": deepcopy(quote.get("leg_received_ts_ms")),
                "source_receipt_times": _compact_source_receipt_times(sources),
                "source_clock_gaps": deepcopy(sources.get("clock_gaps") or []),
            },
            "qualification": {
                "snapshot_status": snapshot.get("status"),
                "snapshot_gap_reasons": deepcopy(snapshot.get("gap_reasons") or []),
                "execution_allowed": snapshot.get("execution_allowed"),
                "packet_execution_allowed": packet.get("execution_allowed"),
                "packet_gaps": deepcopy(packet.get("gaps") or []),
                "quote_gap_reasons": deepcopy(quote.get("gap_reasons") or []),
                "fee_gap_reasons": deepcopy(fee.get("gap_reasons") or []),
                "risk_status": risk.get("status"),
                "risk_gap_reasons": deepcopy(risk.get("gap_reasons") or []),
            },
        })
    points.sort(key=lambda point: (
        point.get("decision_asof_ms") if point.get("decision_asof_ms") is not None else -1,
        str(point.get("snapshot_id") or ""),
        str(point.get("packet_hash") or ""),
    ))
    return points


def _compact_wpem_for_routing(evidence: Mapping[str, Any], interpretation: Mapping[str, Any],
                              *, include_observed_points: bool = False) -> dict[str, Any]:
    same_window = evidence.get("same_candidate_window") if isinstance(evidence.get("same_candidate_window"), Mapping) else {}
    current = evidence.get("current_cross_section") if isinstance(evidence.get("current_cross_section"), Mapping) else {}
    window = _window(evidence)
    compact_interpretation = deepcopy(dict(interpretation))
    dependencies = compact_interpretation.get("source_dependencies")
    if isinstance(dependencies, Mapping):
        compact_interpretation["source_dependencies"] = _compact_dependencies_for_routing(dependencies)
    compact_same_window = {
        "schema": same_window.get("schema"),
        "status": same_window.get("status"),
        "coverage_summary": deepcopy(same_window.get("coverage_summary")),
        "LevelDeltaPath": deepcopy(same_window.get("LevelDeltaPath")),
        "source_method_breaks": deepcopy(same_window.get("source_method_breaks")),
        "insufficient_reason": same_window.get("insufficient_reason"),
    }
    if include_observed_points:
        observed_points = _same_candidate_observed_points(evidence)
        if observed_points:
            compact_same_window["observed_points_scope_cn"] = (
                "这些点只是已严格回放的同 candidate 原始观测片段；保留原决策时点、来源和资格缺口，"
                "不能替代完整同窗 delta/path。缺时钟、换腿或来源定义不同处不得连成完整趋势。"
            )
            compact_same_window["observed_points"] = observed_points
    return {
        "schema": evidence["schema"],
        "evidence_hash": evidence["evidence_hash"],
        "candidate_id": evidence["candidate_id"],
        "snapshot_id": evidence["snapshot_id"],
        "current_cross_section": {
            "schema": current.get("schema"),
            "status": current.get("status"),
            "source_clock": deepcopy(current.get("source_clock")),
            "cross_section_hash": current.get("cross_section_hash"),
            "raw_cross_section_hash": current.get("raw_cross_section_hash"),
        },
        "same_candidate_window": compact_same_window,
        "risk_reference_status": deepcopy(evidence["risk_reference_status"]),
        "quote_fee_deterministic_responsibility": deepcopy(evidence["quote_fee_deterministic_responsibility"]),
        "release_identity": deepcopy(evidence["release_identity"]),
        "window_evidence_hash": window["window_evidence_hash"],
        "interpretation": compact_interpretation,
    }


def _pick_mapping(mapping: Mapping[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(mapping, Mapping):
        return {}
    return {key: deepcopy(mapping.get(key)) for key in keys if key in mapping}


def _fmt_kv(*pairs: tuple[str, Any]) -> str:
    return "，".join(f"{key}={value}" for key, value in pairs if value is not None)


def _market_facts(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    fact_context = snapshot.get("fact_context") if isinstance(snapshot.get("fact_context"), Mapping) else {}
    market_facts = fact_context.get("market_facts") if isinstance(fact_context.get("market_facts"), Mapping) else {}
    return market_facts


def _unit_by_source_asset(evidence: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for unit in _routing_units(evidence):
        asset = unit.get("source_asset")
        if isinstance(asset, str) and asset and asset not in out:
            out[asset] = unit
    return out


def _context_tmv_readings(market_facts: Mapping[str, Any]) -> dict[str, Any]:
    volume = market_facts.get("volume_price") if isinstance(market_facts.get("volume_price"), Mapping) else {}
    out = _pick_mapping(volume, ("direction", "tmv_blend", "tmv_state", "window_conflict"))
    for key in ("tmvf_24h", "tmvf_48h"):
        horizon = volume.get(key) if isinstance(volume.get(key), Mapping) else {}
        core = horizon.get("core") if isinstance(horizon.get("core"), Mapping) else {}
        compact = _pick_mapping(horizon, (
            "data_ready", "final_state", "funding_effect", "funding_state",
            "tmv_core", "tmv_final",
        ))
        compact["core"] = _pick_mapping(core, ("horizon_hours", "kline_open_time", "trend_strength_pct"))
        out[key] = compact
    return out


def _context_funding_readings(market_facts: Mapping[str, Any]) -> dict[str, Any]:
    volume = market_facts.get("volume_price") if isinstance(market_facts.get("volume_price"), Mapping) else {}
    funding = volume.get("funding") if isinstance(volume.get("funding"), Mapping) else {}
    out = _pick_mapping(funding, ("count", "effect", "last_rate"))
    semantics = funding.get("semantics") if isinstance(funding.get("semantics"), Mapping) else {}
    out["semantics"] = _pick_mapping(semantics, (
        "crowding_state", "crowding_threshold_abs", "edb_participation",
        "edb_vote_allowed", "fee_bias", "is_crowded", "raw_funding_rate",
    ))
    for key in ("tmvf_24h", "tmvf_48h"):
        out[key] = _pick_mapping(funding.get(key) if isinstance(funding.get(key), Mapping) else {}, (
            "age_ms", "funding_count", "funding_cum", "funding_norm",
            "funding_state", "horizon_hours", "window_end_time", "window_start_time",
        ))
    return out


def _context_skew_readings(market_facts: Mapping[str, Any]) -> dict[str, Any]:
    return _pick_mapping(market_facts.get("skew") if isinstance(market_facts.get("skew"), Mapping) else {}, (
        "data_state", "greeks_epoch_ms", "rr_blend", "skew_norm_blend",
        "vote", "vote_confidence", "delta_rr", "rr_z", "reason_codes",
    ))


def _context_macro_readings(market_facts: Mapping[str, Any]) -> dict[str, Any]:
    macro = market_facts.get("macro_pressure") if isinstance(market_facts.get("macro_pressure"), Mapping) else {}
    out = _pick_mapping(macro, (
        "data_status", "last_data_time", "data_age_ms", "macro_data_confidence",
        "macro_regime", "macro_score", "summary_label_cn", "blocking_flags", "reason_codes",
    ))
    summaries = macro.get("component_summaries")
    if isinstance(summaries, list):
        out["component_summaries"] = [
            _pick_mapping(row if isinstance(row, Mapping) else {}, (
                "key", "source_symbol", "source_status", "current_close",
                "current_ts_ms", "reference_close", "reference_ts_ms",
                "scoring_unit", "scoring_value", "scoring_bps",
                "tier", "impact",
            ))
            for row in summaries[:4]
        ]
    return out


def _context_flow_readings(market_facts: Mapping[str, Any]) -> dict[str, Any]:
    volume = market_facts.get("volume_price") if isinstance(market_facts.get("volume_price"), Mapping) else {}
    micro = volume.get("micro_flow") if isinstance(volume.get("micro_flow"), Mapping) else {}
    keys = (
        "data_ready", "direction", "max_coverage_hours", "ready_horizons",
        "score", "state", "coverage_hours", "coverage_frac", "horizon_hours", "window_role",
        "cvd_sum", "cvd_unit", "momentum_return_pct",
    )
    return {
        "micro_flow_effect": volume.get("micro_flow_effect"),
        "combined": _pick_mapping(micro.get("combined") if isinstance(micro.get("combined"), Mapping) else {}, keys),
        "fast_4h": _pick_mapping(micro.get("fast_4h") if isinstance(micro.get("fast_4h"), Mapping) else {}, keys),
        "slow_12h": _pick_mapping(micro.get("slow_12h") if isinstance(micro.get("slow_12h"), Mapping) else {}, keys),
    }


def _context_space_readings(market_facts: Mapping[str, Any]) -> dict[str, Any]:
    space_gate = market_facts.get("space_gate") if isinstance(market_facts.get("space_gate"), Mapping) else {}
    gamma = market_facts.get("gamma_regime") if isinstance(market_facts.get("gamma_regime"), Mapping) else {}
    space_context = market_facts.get("space_context") if isinstance(market_facts.get("space_context"), Mapping) else {}
    out = {
        "space_gate": _pick_mapping(space_gate, (
            "state", "observed_at_ms", "since_ts_ms", "last_transition_ts_ms",
            "previous_state", "reason_codes",
        )),
        "gamma_regime": _pick_mapping(gamma, (
            "regime", "data_state", "reason_codes", "net_gamma_notional", "net_gex_sign",
        )),
    }
    anchor = space_context.get("anchor") if isinstance(space_context.get("anchor"), Mapping) else {}
    regime = space_context.get("gamma_regime") if isinstance(space_context.get("gamma_regime"), Mapping) else {}
    out["space_context_anchor"] = _pick_mapping(anchor, (
        "effective_flip_point", "freshness", "ready", "anchor_gravity_ref_score",
    ))
    out["space_context_gamma"] = _pick_mapping(regime, (
        "distance_to_flip_pct", "pin_strike", "regime", "regime_strength",
        "gate_action", "veto",
    ))
    if isinstance(space_gate.get("anchor"), Mapping):
        out["space_gate_anchor"] = _pick_mapping(space_gate.get("anchor"), ("state", "score", "freshness", "source_ts_ms", "ok"))
    if isinstance(space_gate.get("net_gamma"), Mapping):
        out["space_gate_net_gamma"] = _pick_mapping(space_gate.get("net_gamma"), (
            "value", "unit", "sign", "source_ts_ms", "age_ms", "valid", "transition", "conflict",
        ))
    return out


def _contextual_observed_fragments(evidence: Mapping[str, Any]) -> list[str]:
    same_window = evidence.get("same_candidate_window") if isinstance(evidence.get("same_candidate_window"), Mapping) else {}
    coverage = same_window.get("coverage_summary") if isinstance(same_window.get("coverage_summary"), Mapping) else {}
    rows = coverage.get("windows") if isinstance(coverage.get("windows"), list) else []
    out: list[str] = []
    seen: set[tuple[Any, Any, Any, Any, Any]] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        key = (
            row.get("point_count"), row.get("actual_coverage_ms"),
            row.get("coverage_start_ms"), row.get("coverage_end_ms"), row.get("status"),
        )
        if key in seen:
            continue
        seen.add(key)
        requested_ms = row.get("requested_window_ms")
        actual_ms = row.get("actual_coverage_ms")
        insufficient = (
            str(row.get("status") or "").upper() == "INSUFFICIENT_WINDOW"
            or (isinstance(requested_ms, (int, float)) and isinstance(actual_ms, (int, float)) and actual_ms < requested_ms)
        )
        suffix = "已观察片段可读，但不足以概括完整同窗路径。" if insufficient else "已观察片段可读，按原窗口资格使用。"
        out.append(
            f"已观察片段：{row.get('point_count')}点/{actual_ms}ms，"
            f"{row.get('coverage_start_ms')}->{row.get('coverage_end_ms')}，"
            f"status={row.get('status')}；{suffix}"
        )
    return out


def _contextual_market_reads(snapshot: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any] | None:
    units = _unit_by_source_asset(evidence)
    market_facts = _market_facts(snapshot)
    specs = (
        ("tmv_price_path", "context.tmv_price_path", "TMV价格路径", _context_tmv_readings),
        ("funding_context", "context.funding", "Funding费用背景", _context_funding_readings),
        ("srd_skew_aggregate", "context.skew", "Skew期权需求", _context_skew_readings),
        ("macro_pressure", "context.macro", "宏观慢背景", _context_macro_readings),
        ("active_flow_cvd", "context.active_flow", "主动流", _context_flow_readings),
        ("active_flow_cvd_unqualified", "context.active_flow_unqualified", "主动流未完整分类", _context_flow_readings),
        ("space_gate_gamma_regime", "context.option_space", "SpaceGate/GGR空间限制", _context_space_readings),
    )
    items: list[dict[str, Any]] = []
    for source_asset, item_id, label_cn, reader in specs:
        unit = units.get(source_asset)
        if unit is None:
            continue
        readings = reader(market_facts)
        if not readings:
            continue
        text_cn = _context_text_for_asset(source_asset, readings)
        items.append({
            "id": item_id,
            "label_cn": label_cn,
            "source_asset": source_asset,
            "source_ref": unit.get("source_ref"),
            "evidence_id": unit.get("evidence_id"),
            "asof_ms": unit.get("asof"),
            "source_observed_ms": unit.get("source_observed_ms"),
            "source_clock_status": unit.get("source_clock_status"),
            "qualification_status": unit.get("qualification_status"),
            "freshness_status": unit.get("freshness_status"),
            "information_increment": unit.get("information_increment"),
            "producer_label_cn": unit.get("reason_cn"),
            "text_cn": text_cn,
            "readings": readings,
        })
    if not items:
        return None
    return {
        "scope_cn": "紧凑保留 TMV/Funding/skew/宏观/主动流/空间门的实际读数和原时点，用于研究折叠层阅读。",
        "independence_note_cn": "producer independence_status 是既有来源分组声明，不是经验独立性验证；共享来源或同组 facet 不能当作新的多票确认。",
        "observed_fragment_cn": _contextual_observed_fragments(evidence),
        "items": items,
    }


def _contextual_market_read_ref(item: Mapping[str, Any]) -> str | None:
    item_id = item.get("id")
    if not isinstance(item_id, str) or not item_id:
        return None
    return "source_facts.contextual_market_reads.items." + item_id


def _with_contextual_item_refs(contextual_market_reads: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(contextual_market_reads, Mapping):
        return None
    result = deepcopy(contextual_market_reads)
    for item in result.get("items") or []:
        if isinstance(item, dict):
            ref = _contextual_market_read_ref(item)
            if ref is not None:
                item["ref"] = ref
    return result


def _contextual_market_read_refs(contextual_market_reads: Mapping[str, Any] | None) -> list[str]:
    refs: list[str] = []
    if not isinstance(contextual_market_reads, Mapping):
        return refs
    for item in contextual_market_reads.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        ref = item.get("ref") or _contextual_market_read_ref(item)
        if isinstance(ref, str) and ref and ref not in refs:
            refs.append(ref)
    return refs


def _compact_map_value_for_routing(value: Any) -> Any:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if (
                key_text in {"request_audit", "series", "rows", "settled_records"}
                or key_text == "source_values"
                or key_text.endswith("_source_values")
            ):
                continue
            out[key_text] = _compact_map_value_for_routing(item)
        return out
    if isinstance(value, list):
        return [_compact_map_value_for_routing(item) for item in value]
    return deepcopy(value)


def _compact_map_context_for_routing(map_context: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(map_context, Mapping):
        return None
    result = {
        "map_id": map_context.get("map_id"),
        "cutoff_at_ms": map_context.get("cutoff_at_ms"),
        "current_price_usd": map_context.get("current_price_usd"),
        "price_basis": map_context.get("price_basis"),
        "source_manifest": _compact_map_value_for_routing(map_context.get("source_manifest") or {}),
        "usable_background": [
            _compact_map_value_for_routing(row)
            for row in map_context.get("usable_background") or []
            if isinstance(row, Mapping)
        ],
        "usable_references": [
            _compact_map_value_for_routing(row)
            for row in map_context.get("usable_references") or []
            if isinstance(row, Mapping)
        ],
        "gaps": deepcopy(map_context.get("gaps") or []),
    }
    if not result["usable_background"] and not result["usable_references"]:
        return None
    return result


def _compact_source_facts_for_routing(source_facts: Mapping[str, Any], routing_projection: Mapping[str, Any],
                                      contextual_market_reads: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result = {
        "quote": deepcopy((source_facts or {}).get("quote")),
        "fee": deepcopy((source_facts or {}).get("fee")),
        "risk": deepcopy((source_facts or {}).get("risk")),
        "routed_dependency_blocks": [
            {
                "selected_id": item.get("id"),
                "block_id": item.get("block_id"),
                "evidence_ids": deepcopy(item.get("evidence_ids") or []),
            }
            for item in routing_projection.get("dependency_representatives") or []
        ],
    }
    if contextual_market_reads is not None:
        result["contextual_market_reads"] = deepcopy(contextual_market_reads)
    return result


def _clean_ref_suffix(value: Any) -> str:
    return _safe_id(value).lower()


def _clean_add(
    items: list[dict[str, Any]],
    lineage: dict[str, dict[str, Any]],
    *,
    ref: str,
    group: str,
    label_cn: str,
    source_path: str,
    facts: Any | None = None,
    relations: Any | None = None,
    asof: Any | None = None,
    window: Any | None = None,
    scope: Any | None = None,
    basis: Any | None = None,
    qualification: Any | None = None,
    limitations: Any | None = None,
    source_refs: Any | None = None,
    shared_dependency: Any | None = None,
) -> None:
    if not ref:
        return
    item: dict[str, Any] = {
        "ref": ref,
        "group": group,
        "label_cn": label_cn,
    }
    for key, value in (
        ("facts", facts),
        ("relations", relations),
        ("asof", asof),
        ("window", window),
        ("scope", scope),
        ("basis", basis),
        ("qualification", qualification),
        ("limitations", limitations),
        ("shared_dependency", shared_dependency),
    ):
        if value not in (None, {}, []):
            item[key] = deepcopy(value)
    existing = next((idx for idx, row in enumerate(items) if row.get("ref") == ref), None)
    if existing is None:
        items.append(item)
    else:
        items[existing] = item
    lineage[ref] = {
        "ref": ref,
        "source_path": source_path,
        "source_refs": deepcopy(source_refs or []),
    }


def _source_lineage_for_context_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_asset": item.get("source_asset"),
        "source_ref": item.get("source_ref"),
        "evidence_id": item.get("evidence_id"),
        "asof_ms": item.get("asof_ms"),
        "source_observed_ms": item.get("source_observed_ms"),
        "source_clock_status": item.get("source_clock_status"),
        "qualification_status": item.get("qualification_status"),
        "freshness_status": item.get("freshness_status"),
    }


def _is_semantic_scope_schema(review_input: Mapping[str, Any]) -> bool:
    return review_input.get("schema") in {
        ROUTING_REVIEW_INPUT_SCHEMA_266,
        ROUTING_REVIEW_INPUT_SCHEMA_267,
        ROUTING_REVIEW_INPUT_SCHEMA_268,
        ROUTING_REVIEW_INPUT_SCHEMA,
    }


def _binance_futures_facts(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    fact_context = snapshot.get("fact_context") if isinstance(snapshot.get("fact_context"), Mapping) else {}
    source_quality = fact_context.get("source_quality") if isinstance(fact_context.get("source_quality"), Mapping) else {}
    details = source_quality.get("source_details") if isinstance(source_quality.get("source_details"), Mapping) else {}
    binance = details.get("binance") if isinstance(details.get("binance"), Mapping) else {}
    futures = binance.get("futures_facts") if isinstance(binance.get("futures_facts"), Mapping) else {}
    return futures


def _funding_context_facts(readings: Mapping[str, Any], *, split_current: bool) -> dict[str, Any]:
    facts = deepcopy(dict(readings))
    if split_current:
        facts.pop("last_rate", None)
    return facts


def _funding_current_scope(snapshot: Mapping[str, Any], item: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[str], list[dict[str, Any]]]:
    readings = item.get("readings") if isinstance(item.get("readings"), Mapping) else {}
    futures = _binance_futures_facts(snapshot)
    rate = readings.get("last_rate")
    source_rate = futures.get("last_funding_rate")
    matched = rate is not None and source_rate == rate
    facts = {"last_rate": rate}
    asof = {
        "source_time_ms": futures.get("time") if matched else None,
        "next_funding_time_ms": futures.get("next_funding_time") if matched else None,
    }
    qualification = {"source_fields_match": matched}
    limitations: list[str] = [
        "最新费率与TMVF语义窗口、MAP已结算窗口分开阅读；当前拥挤分类未由此项提供。",
    ]
    if not matched:
        limitations.append("原读数未与期货源字段匹配，本项来源时钟未知；不使用窗口时钟填充。")
    source_refs = [{
        "source_asset": item.get("source_asset"),
        "source_ref": item.get("source_ref"),
        "evidence_id": item.get("evidence_id"),
        "source_detail_ref": "snapshot.fact_context.source_quality.source_details.binance.futures_facts" if matched else None,
        "source_fields_match": matched,
        "source_time_ms": futures.get("time") if matched else None,
        "next_funding_time_ms": futures.get("next_funding_time") if matched else None,
    }]
    return facts, asof, qualification, limitations, source_refs


def _tmv_horizon_state(readings: Mapping[str, Any], key: str) -> str:
    horizon = readings.get(key) if isinstance(readings.get(key), Mapping) else {}
    if not horizon:
        return key.replace("tmvf_", "") + "=MISSING"
    hours = ((horizon.get("core") if isinstance(horizon.get("core"), Mapping) else {}) or {}).get("horizon_hours")
    if hours is None:
        hours = horizon.get("horizon_hours") or key.replace("tmvf_", "").replace("h", "")
    state = horizon.get("final_state") or horizon.get("tmv_state") or "UNKNOWN"
    return f"{hours}h={state}"


def _tmv_aggregate_relations(readings: Mapping[str, Any]) -> dict[str, Any]:
    aggregate_present = any(readings.get(key) is not None for key in ("direction", "tmv_state", "tmv_blend"))
    if aggregate_present:
        return {
            "aggregate_direction": readings.get("direction"),
            "aggregate_state": readings.get("tmv_state"),
            "aggregate_source": "producer_top_level",
            "does_not_override_horizon_states": True,
        }
    return {
        "aggregate_direction": "NOT_DEFINED",
        "aggregate_source": "not_provided",
        "does_not_override_horizon_states": True,
    }


def _tmv_basis_for_contract(readings: Mapping[str, Any]) -> str:
    horizons = "；".join(_tmv_horizon_state(readings, key) for key in ("tmvf_24h", "tmvf_48h"))
    relations = _tmv_aggregate_relations(readings)
    if relations.get("aggregate_source") == "producer_top_level":
        return (
            "TMV逐窗读取：" + horizons +
            "；aggregate_direction来自源端顶层聚合，只作聚合背景且不覆盖24h/48h子窗。"
        )
    return (
        "TMV逐窗读取：" + horizons +
        "；aggregate_direction=NOT_DEFINED，未提供源端聚合关系时不从多窗口合成整体方向。"
    )


def _clean_context_ref(item: Mapping[str, Any]) -> str | None:
    item_id = str(item.get("id") or "")
    if item_id == "context.tmv_price_path":
        return "tmv"
    if item_id == "context.funding":
        return "funding"
    if item_id == "context.skew":
        return "skew"
    if item_id == "context.macro":
        return "macro"
    if item_id in {"context.active_flow", "context.active_flow_unqualified"}:
        return "flow"
    if item_id == "context.option_space":
        return "space.gate"
    return None


def _map_source_refs(row: Mapping[str, Any], records_by_id: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    return _map_source_records_for(row, records_by_id)


def _map_background_by_name(map_context: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(row.get("name")): row
        for row in map_context.get("usable_background") or []
        if isinstance(row, Mapping) and row.get("name") is not None
    }


def _map_reference_items(map_context: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        row for row in map_context.get("usable_references") or []
        if isinstance(row, Mapping)
    ]


def _clean_observed_point(point: Mapping[str, Any]) -> dict[str, Any]:
    market = point.get("market") if isinstance(point.get("market"), Mapping) else {}
    contract = point.get("contract") if isinstance(point.get("contract"), Mapping) else {}
    economics = point.get("economics") if isinstance(point.get("economics"), Mapping) else {}
    clock = point.get("source_clock") if isinstance(point.get("source_clock"), Mapping) else {}
    qualification = point.get("qualification") if isinstance(point.get("qualification"), Mapping) else {}
    return {
        "decision_asof_ms": point.get("decision_asof_ms"),
        "market": {**_pick_mapping(market, ("reference_price_usd", "reference_source", "valuation_ts_ms")),
                   "quote_kind": (market.get("quote") or {}).get("kind")},
        "contract": _pick_mapping(contract, (
            "side", "short_instrument", "long_instrument", "short_strike_usd",
            "long_strike_usd", "expiry_ms", "quantity_btc",
        )),
        "economics": _pick_mapping(economics, (
            "visible_credit_btc", "fee_btc", "net_credit_after_entry_fee_btc",
            "net_credit_after_fee_btc",
        )),
        "source_clock": _pick_mapping(clock, (
            "decision_asof_ms", "fact_price_observed_ms", "fact_valuation_ts_ms",
            "market_valuation_ts_ms", "quote_received_ts_ms", "quote_valuation_ts_ms",
        )),
        "qualification": _pick_mapping(qualification, (
            "snapshot_status", "snapshot_gap_reasons", "execution_allowed",
            "packet_execution_allowed", "packet_gaps", "quote_gap_reasons",
            "fee_gap_reasons", "risk_status", "risk_gap_reasons",
        )),
    }


def _change_relation(prev: Any, curr: Any) -> str | None:
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
           for value in (prev, curr)):
        return None
    if curr > prev:
        return "increased"
    if curr < prev:
        return "decreased"
    return "unchanged"


def _observed_fragment_relations(
    points: list[dict[str, Any]],
    same_window: Mapping[str, Any],
    *,
    semantic_contract_closure: bool = False,
) -> dict[str, Any]:
    source_breaks = same_window.get("source_method_breaks") if isinstance(same_window.get("source_method_breaks"), Mapping) else {}
    result: dict[str, Any] = {
        "source_method_breaks": deepcopy(source_breaks),
        "market_trend_claim_allowed": False,
        "scope_cn": (
            "只保留单一观察；没有可比双点。"
            if semantic_contract_closure and len(points) < 2 else
            "只允许读取相邻quote点的原始变化；不得跨method/source break拼成完整趋势。"
        ),
    }
    if len(points) >= 2:
        prev, curr = points[-2], points[-1]
        prev_market = prev.get("market") if isinstance(prev.get("market"), Mapping) else {}
        curr_market = curr.get("market") if isinstance(curr.get("market"), Mapping) else {}
        prev_economics = prev.get("economics") if isinstance(prev.get("economics"), Mapping) else {}
        curr_economics = curr.get("economics") if isinstance(curr.get("economics"), Mapping) else {}
        prev_contract = prev.get("contract") if isinstance(prev.get("contract"), Mapping) else {}
        curr_contract = curr.get("contract") if isinstance(curr.get("contract"), Mapping) else {}
        same_legs = all(prev_contract.get(key) is not None and prev_contract.get(key) == curr_contract.get(key)
                        for key in ("short_instrument", "long_instrument", "expiry_ms", "side", "quantity_btc"))
        same_price_basis = bool(prev_market.get("reference_source") and
                                prev_market.get("reference_source") == curr_market.get("reference_source"))
        same_quote_basis = bool(prev_market.get("quote_kind") and
                                prev_market.get("quote_kind") == curr_market.get("quote_kind"))
        prev_time, curr_time = prev.get("decision_asof_ms"), curr.get("decision_asof_ms")
        ordered = isinstance(prev_time, (int, float)) and isinstance(curr_time, (int, float)) and 0 < prev_time < curr_time
        comparable = same_legs and same_price_basis and same_quote_basis and ordered
        result["latest_adjacent_quote_pair"] = {
            "prev_decision_asof_ms": prev.get("decision_asof_ms"),
            "curr_decision_asof_ms": curr.get("decision_asof_ms"),
            "same_contract_legs": same_legs,
            "same_price_and_quote_basis": same_price_basis and same_quote_basis,
            "comparison_scope": "adjacent_frozen_quotes_only" if comparable else "unknown_incomparable_quotes",
            "reference_price_relation": _change_relation(
                prev_market.get("reference_price_usd"),
                curr_market.get("reference_price_usd"),
            ) if comparable else None,
            "visible_credit_relation": _change_relation(
                prev_economics.get("visible_credit_btc"),
                curr_economics.get("visible_credit_btc"),
            ) if comparable else None,
            "fee_relation": _change_relation(
                prev_economics.get("fee_btc"),
                curr_economics.get("fee_btc"),
            ) if comparable else None,
            "net_credit_after_fee_relation": _change_relation(
                prev_economics.get("net_credit_after_fee_btc"),
                curr_economics.get("net_credit_after_fee_btc"),
            ) if comparable else None,
        }
    return result


def _wpem_fragment_relations_from_window(same_window: Mapping[str, Any]) -> dict[str, Any] | None:
    if not same_window.get("observed_points"):
        return None
    clean_points = [
        _clean_observed_point(point)
        for point in same_window.get("observed_points") or []
        if isinstance(point, Mapping)
    ]
    return _observed_fragment_relations(clean_points, same_window)


def _etf_speed_relations(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "same_metric_v3_vs_v7": "v3_greater_than_v7" if (
            isinstance(metrics.get("v3_usd_m_per_day"), (int, float))
            and isinstance(metrics.get("v7_usd_m_per_day"), (int, float))
            and metrics.get("v3_usd_m_per_day") > metrics.get("v7_usd_m_per_day")
        ) else None,
    }


def _cost_basis_relations(metrics: Mapping[str, Any]) -> dict[str, Any]:
    cost = metrics.get("cost_basis") if isinstance(metrics.get("cost_basis"), Mapping) else {}
    source_price = cost.get("source_price_usd")
    observation_dates = cost.get("observation_dates") if isinstance(cost.get("observation_dates"), Mapping) else {}
    cp_dates = [observation_dates.get(key) for key in ("cp_sth", "cp_lth", "cp_all")]
    source_price_date = observation_dates.get("source_price")
    same_day_basis = bool(source_price_date and all(date == source_price_date for date in cp_dates))
    relations_cost: dict[str, Any] = {
        "comparison_basis": metrics.get("cost_comparison_basis") or "BITVIEW_SAME_SOURCE_SAME_DAY_ONLY",
        "not_compared_to_deribit_live_quote": True,
        "cp_source_price_observation_dates_match": same_day_basis,
    }
    for name in ("cp_sth_usd", "cp_lth_usd", "cp_all_usd"):
        value = cost.get(name)
        comparable = bool(source_price_date and observation_dates.get(name.removesuffix("_usd")) == source_price_date)
        relation = "unknown_not_same_source_day"
        if comparable and isinstance(value, (int, float)) and isinstance(source_price, (int, float)):
            relation = "below_source_price" if value < source_price else "above_source_price" if value > source_price else "equal_source_price"
        relations_cost[name + "_vs_source_price"] = relation
    return relations_cost


def _oi_window_relations(oi: Mapping[str, Any]) -> dict[str, Any]:
    oi_window = oi.get("oi_24h_window") if isinstance(oi.get("oi_24h_window"), Mapping) else {}
    return {
        "has_24h_delta": oi_window.get("status") == "OK",
        "same_metric_current_vs_previous": _change_relation(
            oi_window.get("previous_native_btc"),
            oi_window.get("current_native_btc"),
        ) if oi_window.get("status") == "OK" else None,
    }


def _funding_window_relations(funding: Mapping[str, Any]) -> dict[str, Any]:
    recent = funding.get("window_24h") if isinstance(funding.get("window_24h"), Mapping) else {}
    previous = funding.get("previous_24h") if isinstance(funding.get("previous_24h"), Mapping) else {}
    times = [previous.get("start_ms"), previous.get("end_ms"), recent.get("start_ms"), recent.get("end_ms")]
    comparable_windows = (
        all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in times)
        and times[0] < times[1] == times[2] < times[3]
        and times[1] - times[0] == times[3] - times[2]
    )
    return {
        "settlements_only": True,
        "borrow_gap_does_not_delete_funding": True,
        "adjacent_equal_windows": comparable_windows,
        "recent_vs_previous_settled_sum": _change_relation(
            previous.get("settled_rate_sum"),
            recent.get("settled_rate_sum"),
        ) if comparable_windows else None,
        "recent_settled_sum_vs_zero": _change_relation(0, recent.get("settled_rate_sum")),
        "previous_settled_sum_vs_zero": _change_relation(0, previous.get("settled_rate_sum")),
    }


def _map_background_relations(map_context: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    backgrounds = _map_background_by_name(map_context)
    relations: dict[str, dict[str, Any]] = {}
    allocation = backgrounds.get("allocation")
    if allocation is not None:
        metrics = allocation.get("metrics") if isinstance(allocation.get("metrics"), Mapping) else {}
        relations["map.etf"] = _etf_speed_relations(metrics)
    inventory = backgrounds.get("inventory")
    if inventory is not None:
        metrics = inventory.get("metrics") if isinstance(inventory.get("metrics"), Mapping) else {}
        relations["map.cost"] = _cost_basis_relations(metrics)
        relations["map.realized"] = {"high_low_baseline": None}
    financing = backgrounds.get("financing")
    if financing is not None:
        metrics = financing.get("metrics") if isinstance(financing.get("metrics"), Mapping) else {}
        oi = metrics.get("oi") if isinstance(metrics.get("oi"), Mapping) else {}
        funding = metrics.get("funding") if isinstance(metrics.get("funding"), Mapping) else {}
        relations["map.oi"] = _oi_window_relations(oi)
        relations["map.funding"] = _funding_window_relations(funding)
    return relations


def _context_item_relations(contextual_market_reads: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    relations: dict[str, dict[str, Any]] = {}
    if not isinstance(contextual_market_reads, Mapping):
        return relations
    for item in contextual_market_reads.get("items") or []:
        if not isinstance(item, Mapping) or str(item.get("id") or "") != "context.option_space":
            continue
        readings = item.get("readings") if isinstance(item.get("readings"), Mapping) else {}
        gamma = readings.get("gamma_regime") if isinstance(readings.get("gamma_regime"), Mapping) else {}
        price_vs_flip = "PRICE_ABOVE_FLIP" if any(
            "PRICE_ABOVE_FLIP" in str(code)
            for code in (gamma.get("reason_codes") or [])
        ) else None
        relations["space.flip"] = {"price_vs_flip": price_vs_flip, "scope": "same context item only"}
        relations["space.gate"] = {"same_dependency_as_flip": True}
    return relations


def _program_fact_relations(review_input: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    contract = review_input.get("contract") if isinstance(review_input.get("contract"), Mapping) else {}
    source_facts = review_input.get("source_facts") if isinstance(review_input.get("source_facts"), Mapping) else {}
    decision_relations = review_input.get("decision_relations") if isinstance(review_input.get("decision_relations"), Mapping) else {}
    geometry = decision_relations.get("contract_geometry") if isinstance(decision_relations.get("contract_geometry"), Mapping) else {}
    wpem = review_input.get("wpem") if isinstance(review_input.get("wpem"), Mapping) else {}
    same_window = wpem.get("same_candidate_window") if isinstance(wpem.get("same_candidate_window"), Mapping) else {}
    ldp = same_window.get("LevelDeltaPath") if isinstance(same_window.get("LevelDeltaPath"), Mapping) else {}
    responsibility = ldp.get("responsibility") if isinstance(ldp.get("responsibility"), Mapping) else {}
    quote = source_facts.get("quote") if isinstance(source_facts.get("quote"), Mapping) else {}
    quote_body = quote.get("quote") if isinstance(quote.get("quote"), Mapping) else {}
    risk_source = source_facts.get("risk") if isinstance(source_facts.get("risk"), Mapping) else {}
    risk = risk_source.get("risk") if isinstance(risk_source.get("risk"), Mapping) else {}
    side = str(contract.get("side") or "").lower()
    adverse_direction = geometry.get("adverse_direction_cn") or ("上涨" if side == "call" else "下跌" if side == "put" else "未知")
    flow_relation = _strip_catalog_ids(decision_relations.get("flow")) if isinstance(decision_relations.get("flow"), Mapping) else {}
    return {
        "contract": {
            "adverse_direction_cn": adverse_direction,
            "liability_zone": responsibility.get("liability_zone"),
        },
        "quote": {"frozen_quote_not_filled": quote_body.get("executable_or_filled") is False},
        "risk": {"positive_ev_not_determinable": risk.get("mu_btc") is None},
        "flow": {
            "contract_relation": flow_relation.get("relation"),
            "relation_cn": flow_relation.get("relation_cn"),
        },
    }


def compose_decision_evidence(
    snapshot: Mapping[str, Any],
    evidence: Mapping[str, Any],
    review_input: Mapping[str, Any],
    *,
    contextual_market_reads: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    del snapshot, evidence
    wpem = review_input.get("wpem") if isinstance(review_input.get("wpem"), Mapping) else {}
    same_window = wpem.get("same_candidate_window") if isinstance(wpem.get("same_candidate_window"), Mapping) else {}
    relations = _program_fact_relations(review_input)
    fragment_relations = _observed_fragment_relations(
        [
            _clean_observed_point(point)
            for point in same_window.get("observed_points") or []
            if isinstance(point, Mapping)
        ],
        same_window,
        semantic_contract_closure=review_input.get("schema") == ROUTING_REVIEW_INPUT_SCHEMA,
    ) if same_window.get("observed_points") else None
    if fragment_relations is not None:
        relations["wpem.fragments"] = fragment_relations
        if isinstance(same_window, dict):
            same_window["observed_fragment_relations"] = deepcopy(fragment_relations)
    relations.update(_context_item_relations(contextual_market_reads))
    map_context = review_input.get("map_context") if isinstance(review_input.get("map_context"), Mapping) else {}
    relations.update(_map_background_relations(map_context))
    decision_relations = review_input.get("decision_relations")
    if isinstance(decision_relations, dict):
        decision_relations["evidence_relations"] = deepcopy(relations)
    return relations


def _relation_for_ref(
    review_input: Mapping[str, Any],
    ref: str,
    fallback: Any | None = None,
) -> Any:
    decision_relations = review_input.get("decision_relations") if isinstance(review_input.get("decision_relations"), Mapping) else {}
    relations = decision_relations.get("evidence_relations") if isinstance(decision_relations.get("evidence_relations"), Mapping) else {}
    if ref in relations:
        return deepcopy(relations[ref])
    if review_input.get("schema") in _RELATION_OWNED_INPUT_SCHEMAS:
        raise legacy.ReviewValidationError("decision evidence relation missing: " + ref)
    return fallback() if fallback is not None else {}


def _strip_catalog_ids(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(item)
        for key, item in value.items()
        if key not in {"claim_id", "counter_id", "gap_id", "reassess_condition_id"}
    }


def _clean_evidence_package(snapshot: Mapping[str, Any], review_input: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    contract = review_input.get("contract") if isinstance(review_input.get("contract"), Mapping) else {}
    source_facts = review_input.get("source_facts") if isinstance(review_input.get("source_facts"), Mapping) else {}
    relations = review_input.get("decision_relations") if isinstance(review_input.get("decision_relations"), Mapping) else {}
    geometry = relations.get("contract_geometry") if isinstance(relations.get("contract_geometry"), Mapping) else {}
    wpem = review_input.get("wpem") if isinstance(review_input.get("wpem"), Mapping) else {}
    same_window = wpem.get("same_candidate_window") if isinstance(wpem.get("same_candidate_window"), Mapping) else {}
    ldp = same_window.get("LevelDeltaPath") if isinstance(same_window.get("LevelDeltaPath"), Mapping) else {}
    level = ldp.get("level") if isinstance(ldp.get("level"), Mapping) else {}
    delta = ldp.get("delta") if isinstance(ldp.get("delta"), Mapping) else {}
    path = ldp.get("path") if isinstance(ldp.get("path"), Mapping) else {}
    responsibility = ldp.get("responsibility") if isinstance(ldp.get("responsibility"), Mapping) else {}
    map_context = review_input.get("map_context") if isinstance(review_input.get("map_context"), Mapping) else {}
    records_by_id = _map_source_records_by_id(map_context)
    semantic_scope = _is_semantic_scope_schema(review_input)
    semantic_contract_closure = review_input.get("schema") == ROUTING_REVIEW_INPUT_SCHEMA

    items: list[dict[str, Any]] = []
    lineage: dict[str, dict[str, Any]] = {}
    side = str(contract.get("side") or "").lower()
    adverse_direction = geometry.get("adverse_direction_cn") or ("上涨" if side == "call" else "下跌" if side == "put" else "未知")
    program_facts = {
        "candidate_id": review_input.get("candidate_id"),
        "side": contract.get("side"),
        "decision_asof_ms": geometry.get("decision_asof_ms") or level.get("decision_asof_ms"),
        "adverse_direction_cn": adverse_direction,
    }
    contract_facts = {
        "side": contract.get("side"),
        "short_instrument": contract.get("short_instrument"),
        "long_instrument": contract.get("long_instrument"),
        "short_strike_usd": contract.get("short_strike_usd"),
        "long_strike_usd": contract.get("long_strike_usd"),
        "expiry_ms": contract.get("expiry_ms"),
        "quantity_btc": contract.get("quantity_btc"),
        "remaining_horizon_hours": geometry.get("horizon_hours"),
        "adverse_direction_cn": adverse_direction,
    }
    _clean_add(
        items, lineage, ref="contract", group="program_facts", label_cn="本保单责任身份",
        source_path="contract/decision_relations.contract_geometry",
        facts=contract_facts,
        relations=_relation_for_ref(
            review_input,
            "contract",
            lambda: {"adverse_direction_cn": adverse_direction, "liability_zone": responsibility.get("liability_zone")},
        ),
        qualification={"execution_allowed": responsibility.get("execution_allowed")},
    )
    quote = source_facts.get("quote") if isinstance(source_facts.get("quote"), Mapping) else {}
    quote_body = quote.get("quote") if isinstance(quote.get("quote"), Mapping) else {}
    quote_source = quote.get("source") if isinstance(quote.get("source"), Mapping) else {}
    _clean_add(
        items, lineage, ref="quote", group="program_facts", label_cn="冻结盘口估算",
        source_path="source_facts.quote",
        facts={
            "kind": quote_body.get("kind"),
            "credit_btc": quote_body.get("credit_btc"),
            "reference_price_usd": quote_source.get("reference_price_usd"),
            "reference_source": quote_source.get("reference_source"),
            "executable_or_filled": quote_body.get("executable_or_filled"),
            "gap_reasons": deepcopy(quote_body.get("gap_reasons") or []),
        },
        relations=_relation_for_ref(
            review_input,
            "quote",
            lambda: {"frozen_quote_not_filled": quote_body.get("executable_or_filled") is False},
        ),
        asof={"valuation_ts_ms": quote_source.get("valuation_ts_ms"), "received_ts_ms": quote_body.get("received_ts_ms")},
        basis="冻结顺序腿盘口估算，不是成交回执。",
        qualification={"gap_reasons": deepcopy(quote_body.get("gap_reasons") or [])},
    )
    fee_source = source_facts.get("fee") if isinstance(source_facts.get("fee"), Mapping) else {}
    fee = fee_source.get("fee") if isinstance(fee_source.get("fee"), Mapping) else {}
    _clean_add(
        items, lineage, ref="fee", group="program_facts", label_cn="费用假设",
        source_path="source_facts.fee/deterministic_financials.account_basis",
        facts={
            "amount_btc": fee.get("amount_btc"),
            "entry_fee_btc": fee.get("entry_fee_btc"),
            "delivery_fee_btc": fee.get("delivery_fee_btc"),
            "basis": fee.get("basis"),
            "assumption_basis": fee.get("assumption_basis"),
            "gap_reasons": deepcopy(fee.get("gap_reasons") or []),
        },
        basis="费用由本地确定性账负责，LLM不重算。",
        qualification={"gap_reasons": deepcopy(fee.get("gap_reasons") or [])},
    )
    risk_source = source_facts.get("risk") if isinstance(source_facts.get("risk"), Mapping) else {}
    risk = risk_source.get("risk") if isinstance(risk_source.get("risk"), Mapping) else {}
    _clean_add(
        items, lineage, ref="risk", group="program_facts", label_cn="风险参考状态",
        source_path="source_facts.risk/decision_relations.risk_coverage",
        facts={
            "status": risk.get("status"),
            "mu_btc": risk.get("mu_btc"),
            "overall_mu_status": (relations.get("risk_coverage") or {}).get("overall_mu_status")
            if isinstance(relations.get("risk_coverage"), Mapping) else None,
            "gap_reasons": deepcopy(risk.get("gap_reasons") or []),
        },
        relations=_relation_for_ref(
            review_input,
            "risk",
            lambda: {"positive_ev_not_determinable": risk.get("mu_btc") is None},
        ),
        qualification=deepcopy(relations.get("risk_coverage") if isinstance(relations.get("risk_coverage"), Mapping) else {}),
        limitations=["μ/M 缺失只限制补偿/正期望结论，不删除其他已给事实。"],
    )
    flow_relation = _strip_catalog_ids(relations.get("flow")) if isinstance(relations.get("flow"), Mapping) else {}
    _clean_add(
        items, lineage, ref="flow", group="market_context", label_cn="方向流与本单责任关系",
        source_path="decision_relations.flow/source_facts.contextual_market_reads",
        facts=deepcopy(flow_relation),
        relations=_relation_for_ref(
            review_input,
            "flow",
            lambda: {"contract_relation": flow_relation.get("relation"), "relation_cn": flow_relation.get("relation_cn")},
        ),
        qualification={"source_qualification": flow_relation.get("source_qualification")},
    )
    _clean_add(
        items, lineage, ref="wpem.level", group="wpem", label_cn="当前同保单Level",
        source_path="wpem.same_candidate_window.LevelDeltaPath.level",
        facts=_pick_mapping(level, (
            "decision_asof_ms", "mu_ref_btc", "M_ref_btc", "dte_ms", "signed_distance_to_short_pct",
            "signed_distance_to_long_pct",
        )),
        relations=deepcopy(responsibility),
        qualification={"window_status": same_window.get("status")},
    )
    _clean_add(
        items, lineage, ref="wpem.delta", group="wpem", label_cn="同保单严格Delta状态",
        source_path="wpem.same_candidate_window.LevelDeltaPath.delta",
        facts=deepcopy(delta),
        scope=(
            "仅为同candidate且严格同定义窗口；缺少严格Delta不删除原观察片段，也不限制当前截面的解释。"
            if semantic_scope else
            "仅为同candidate且严格同定义窗口；NO_STRICT_DELTA不删除原观察片段。"
        ),
        qualification={"window_status": same_window.get("status")},
    )
    _clean_add(
        items, lineage, ref="wpem.path", group="wpem", label_cn="同保单Path状态",
        source_path="wpem.same_candidate_window.LevelDeltaPath.path",
        facts=deepcopy(path),
        scope=(
            "NO_PATH只限制完整路径结论，不代表所有事实未知；当前截面和片段仍按各自来源阅读。"
            if semantic_scope else
            "NO_PATH只限制完整路径结论，不代表所有事实未知。"
        ),
        qualification={"window_status": same_window.get("status")},
    )
    if same_window.get("observed_points"):
        clean_points = [
            _clean_observed_point(point)
            for point in same_window.get("observed_points") or []
            if isinstance(point, Mapping)
        ]
        single_observation_scope = semantic_contract_closure and len(clean_points) < 2
        _clean_add(
            items, lineage, ref="wpem.fragments", group="wpem", label_cn="同保单历史观察片段",
            source_path="wpem.same_candidate_window.observed_points",
            source_refs=[{"snapshot_id": point.get("snapshot_id"), "decision_asof_ms": point.get("decision_asof_ms")}
                         for point in same_window.get("observed_points") or [] if isinstance(point, Mapping)],
            facts={
                "scope_cn": (
                    "只保留单一观察；没有可比双点。"
                    if single_observation_scope else same_window.get("observed_points_scope_cn")
                ),
                "observed_points": clean_points,
            },
            relations=_relation_for_ref(
                review_input,
                "wpem.fragments",
                lambda: _observed_fragment_relations(
                    clean_points,
                    same_window,
                    semantic_contract_closure=semantic_contract_closure,
                ),
            ),
            scope=(
                "只保留单一观察；没有可比双点。"
                if single_observation_scope else
                "已严格回放的相同candidate片段；只能说明逐点原始变化，缺时钟、换腿或来源定义不同处不得串成完整趋势。"
                if semantic_scope else
                "已严格回放的相同candidate片段；缺时钟、换腿或来源定义不同处不得串成完整趋势。"
            ),
            qualification={"window_status": same_window.get("status"), "delta_status": delta.get("status"), "path_status": path.get("status")},
        )
    contextual = source_facts.get("contextual_market_reads") if isinstance(source_facts.get("contextual_market_reads"), Mapping) else {}
    for item in contextual.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        ref = _clean_context_ref(item)
        if ref is None:
            continue
        if ref == "flow":
            existing = next((row for row in items if row.get("ref") == "flow"), None)
            if existing is not None:
                existing["facts"] = {"decision_relation": deepcopy(flow_relation), "context_readings": deepcopy(item.get("readings") or {})}
                existing["asof"] = {"asof_ms": item.get("asof_ms"), "source_observed_ms": item.get("source_observed_ms")}
                existing["basis"] = item.get("producer_label_cn")
                existing["qualification"] = {key: value for key, value in _source_lineage_for_context_item(item).items()
                                             if key != "evidence_id"}
                lineage["flow"]["source_refs"] = [_source_lineage_for_context_item(item)]
            continue
        readings = item.get("readings") if isinstance(item.get("readings"), Mapping) else {}
        funding_split = semantic_scope and ref == "funding"
        tmv_contract_closure = semantic_contract_closure and ref == "tmv"
        _clean_add(
            items, lineage, ref=ref, group="market_context", label_cn=str(item.get("label_cn") or ref),
            source_path="source_facts.contextual_market_reads.items",
            facts=_funding_context_facts(readings, split_current=True) if funding_split else deepcopy(readings),
            relations=_tmv_aggregate_relations(readings) if tmv_contract_closure else None,
            asof={"asof_ms": item.get("asof_ms"), "source_observed_ms": item.get("source_observed_ms")},
            scope=("Funding主项承载源端canonical/TMVF窗口；semantics.raw_funding_rate的精确primary source clock未在clean input中确认，当前last_rate独立列在funding.current。" if funding_split else None),
            basis=(
                "Funding canonical/TMVF窗口读数；当前口径见funding.current。"
                if funding_split else
                _tmv_basis_for_contract(readings)
                if tmv_contract_closure else
                item.get("producer_label_cn") or item.get("text_cn")
            ),
            qualification={key: value for key, value in _source_lineage_for_context_item(item).items() if key != "evidence_id"},
            source_refs=[_source_lineage_for_context_item(item)],
            shared_dependency=item.get("source_asset") if funding_split else None,
        )
        if funding_split:
            current_facts, current_asof, current_qualification, current_limits, current_refs = _funding_current_scope(snapshot, item)
            _clean_add(
                items, lineage, ref="funding.current", group="market_context", label_cn="冻结时点的最新Funding费率",
                source_path="source_facts.contextual_market_reads.items.context.funding.readings.last_rate",
                facts=current_facts,
                asof=current_asof,
                scope="当前futures last_rate独立于TMVF语义窗口和MAP已结算窗口。",
                basis="只说明原funding readings.last_rate及可匹配source clock，不派生拥挤状态。",
                qualification=current_qualification,
                limitations=current_limits,
                source_refs=current_refs,
                shared_dependency=item.get("source_asset"),
            )
        if str(item.get("id") or "") == "context.option_space":
            readings = item.get("readings") if isinstance(item.get("readings"), Mapping) else {}
            anchor = readings.get("space_context_anchor") if isinstance(readings.get("space_context_anchor"), Mapping) else {}
            gamma = readings.get("gamma_regime") if isinstance(readings.get("gamma_regime"), Mapping) else {}
            space_gate = readings.get("space_gate") if isinstance(readings.get("space_gate"), Mapping) else {}
            _clean_add(
                items, lineage, ref="space.flip", group="market_context", label_cn="Flip相对位置",
                source_path="source_facts.contextual_market_reads.items.context.option_space.readings",
                facts={
                    "effective_flip_point": anchor.get("effective_flip_point"),
                    "reference_price_usd": quote_source.get("reference_price_usd"),
                    "reason_codes": deepcopy(gamma.get("reason_codes") or []),
                },
                relations=_relation_for_ref(
                    review_input,
                    "space.flip",
                    lambda: _context_item_relations({"items": [item]}).get("space.flip", {}),
                ),
                asof={"asof_ms": item.get("asof_ms"), "source_observed_ms": item.get("source_observed_ms")},
                basis="沿用源端 reason code，不反向改写 flip 关系。",
                qualification={key: value for key, value in _source_lineage_for_context_item(item).items() if key != "evidence_id"},
                source_refs=[_source_lineage_for_context_item(item)],
                shared_dependency="space_gate_gamma_regime",
            )
            _clean_add(
                items, lineage, ref="space.gate", group="market_context", label_cn="SpaceGate结构门",
                source_path="source_facts.contextual_market_reads.items.context.option_space.readings.space_gate",
                facts=deepcopy(space_gate),
                relations=_relation_for_ref(
                    review_input,
                    "space.gate",
                    lambda: _context_item_relations({"items": [item]}).get("space.gate", {}),
                ),
                asof={"asof_ms": item.get("asof_ms"), "source_observed_ms": item.get("source_observed_ms")},
                qualification={key: value for key, value in _source_lineage_for_context_item(item).items() if key != "evidence_id"},
                source_refs=[_source_lineage_for_context_item(item)],
                shared_dependency="space_gate_gamma_regime",
            )
    backgrounds = _map_background_by_name(map_context)
    allocation = backgrounds.get("allocation")
    if allocation is not None:
        metrics = allocation.get("metrics") if isinstance(allocation.get("metrics"), Mapping) else {}
        _clean_add(
            items, lineage, ref="map.etf", group="map_background", label_cn="ETF净流与速度窗口",
            source_path="map_context.usable_background[name=allocation].metrics",
            facts=_pick_mapping(metrics, (
                "f3_usd_m", "f7_usd_m", "v3_usd_m_per_day", "v7_usd_m_per_day",
                "u3", "u7", "latest_complete_date", "status", "windows",
            )),
            relations=_relation_for_ref(review_input, "map.etf", lambda: _etf_speed_relations(metrics)),
            asof={"cutoff_at_ms": allocation.get("cutoff_at_ms"), "known_at_ms": allocation.get("known_at_ms")},
            basis=allocation.get("summary_cn"),
            qualification={"fact_state": allocation.get("fact_state"), "missing": deepcopy(allocation.get("missing") or [])},
            limitations=deepcopy(allocation.get("limitations") or []),
            source_refs=_map_source_refs(allocation, records_by_id),
        )
    inventory = backgrounds.get("inventory")
    if inventory is not None:
        metrics = inventory.get("metrics") if isinstance(inventory.get("metrics"), Mapping) else {}
        cost = metrics.get("cost_basis") if isinstance(metrics.get("cost_basis"), Mapping) else {}
        relations_cost = _relation_for_ref(review_input, "map.cost", lambda: _cost_basis_relations(metrics))
        same_day_basis = (relations_cost or {}).get("cp_source_price_observation_dates_match") is True
        _clean_add(
            items, lineage, ref="map.cost", group="map_background", label_cn="链上成本CP",
            source_path="map_context.usable_background[name=inventory].metrics.cost_basis",
            facts=_pick_mapping(cost, (
                "cp_sth_usd", "cp_lth_usd", "cp_all_usd", "source_price_usd",
                "latest_observation_date", "observation_dates", "price_basis", "source_phase",
            )),
            relations=relations_cost,
            asof={"cutoff_at_ms": inventory.get("cutoff_at_ms"), "known_at_ms": inventory.get("known_at_ms")},
            basis=(
                "CP按Bitview成本口径与同日source_price比较；相对Deribit live quote不可比。"
                if same_day_basis else
                "逐项只在原观测日与source_price相同且数值齐备时比较；其余关系未知，也不能与Deribit live quote比较。"
            ),
            qualification={"fact_state": inventory.get("fact_state"), "missing": deepcopy(inventory.get("missing") or [])},
            limitations=deepcopy(inventory.get("limitations") or []),
            source_refs=_map_source_refs(inventory, records_by_id),
        )
        realized = metrics.get("realized_pnl") if isinstance(metrics.get("realized_pnl"), Mapping) else {}
        _clean_add(
            items, lineage, ref="map.realized", group="map_background", label_cn="已实现损益窗口",
            source_path="map_context.usable_background[name=inventory].metrics.realized_pnl",
            facts=_pick_mapping(realized, (
                "loss_14d_usd", "profit_14d_usd", "loss_share_14d",
                "window_days", "mode", "missing",
            )),
            relations=_relation_for_ref(review_input, "map.realized", lambda: {"high_low_baseline": None}),
            asof={"cutoff_at_ms": inventory.get("cutoff_at_ms"), "known_at_ms": inventory.get("known_at_ms")},
            basis="当前只给14日实现盈亏占比，未给高/低基准。",
            qualification={"fact_state": inventory.get("fact_state"), "missing": deepcopy(realized.get("missing") or [])},
            limitations=["未给出高低分位基准时，不得写成偏高或高位。"],
            source_refs=_map_source_refs(inventory, records_by_id),
        )
    financing = backgrounds.get("financing")
    if financing is not None:
        metrics = financing.get("metrics") if isinstance(financing.get("metrics"), Mapping) else {}
        oi = metrics.get("oi") if isinstance(metrics.get("oi"), Mapping) else {}
        funding = metrics.get("funding") if isinstance(metrics.get("funding"), Mapping) else {}
        _clean_add(
            items, lineage, ref="map.oi", group="map_background", label_cn="原生OI及24h变化",
            source_path="map_context.usable_background[name=financing].metrics.oi",
            facts=_pick_mapping(oi, (
                "open_interest_native_btc", "oi_24h_window", "symbol",
                "source_time_basis", "current_endpoint", "history_endpoint_24h",
            )),
            relations=_relation_for_ref(review_input, "map.oi", lambda: _oi_window_relations(oi)),
            asof={"cutoff_at_ms": financing.get("cutoff_at_ms"), "known_at_ms": financing.get("known_at_ms")},
            basis="Binance BTCUSDT原生OI；变化只比较已有24h同产品窗口，不能替代Deribit期权OI。",
            qualification={"fact_state": financing.get("fact_state"), "missing": deepcopy(financing.get("missing") or [])},
            limitations=deepcopy(financing.get("limitations") or []),
            source_refs=_map_source_refs(financing, records_by_id),
        )
        _clean_add(
            items, lineage, ref="map.funding", group="map_background", label_cn="实际Funding结算",
            source_path="map_context.usable_background[name=financing].metrics.funding",
            facts=_pick_mapping(funding, (
                "window_24h", "previous_24h", "settled_count",
                "settled_funding_rate_sum", "predicted_count_excluded",
                "prediction_mixed_with_settlement",
            )),
            relations=_relation_for_ref(review_input, "map.funding", lambda: _funding_window_relations(funding)),
            asof={"cutoff_at_ms": financing.get("cutoff_at_ms"), "known_at_ms": financing.get("known_at_ms")},
            scope=("只读已结算Funding窗口；不覆盖funding.current当前last_rate或funding的TMVF语义窗口。" if semantic_scope else None),
            basis="仅比较相邻同长度窗口内观察到的实际Funding结算和，不假定完整结算频率；借款缺口不删除已有结算事实。",
            qualification={"fact_state": financing.get("fact_state"), "missing": deepcopy(financing.get("missing") or [])},
            limitations=deepcopy(financing.get("limitations") or []),
            source_refs=_map_source_refs(financing, records_by_id),
        )
    for row in _map_reference_items(map_context):
        if str(row.get("source_family") or "").lower() != "kpf":
            continue
        ref = "map.kpf." + _clean_ref_suffix(row.get("reference_id") or row.get("ref"))
        _clean_add(
            items, lineage, ref=ref, group="map_reference", label_cn="KPF成交接受区",
            source_path="map_context.usable_references[source_family=kpf]",
            facts=_pick_mapping(row, (
                "reference_id", "role", "geometry", "raw_low", "raw_high", "display_center",
                "market", "quote_currency", "price_basis", "coordinate_qualification",
                "quality",
            )),
            asof={
                "known_at_ms": row.get("known_at_ms"),
                "active_from_ms": row.get("active_from_ms"),
                "observation_end_ms": row.get("observation_end_ms"),
            },
            basis="KPF为成交接受区背景，不是本到期实际保护或执行价格。",
            qualification={"coordinate_qualification": row.get("coordinate_qualification")},
            limitations=deepcopy(row.get("limitations") or []),
            source_refs=_map_source_refs(row, records_by_id),
        )
    omissions = [
        {"field": "claim_catalog", "reason_cn": "目录只供本地安全归一化和审计，模型只接收 clean evidence。"},
        {"field": "deterministic_financials.points", "reason_cn": "完整赔付/盈亏平衡表保留给程序账和页面，模型只收到必要语义边界。"},
        {"field": "wpem.same_candidate_window.observed_points.full", "reason_cn": "完整逐点hash和重复source lineage留本地审计，模型只收到原时点、价格、信用、费用和资格摘要。"},
        {"field": "source_bindings/composition_inputs", "reason_cn": "完整私有源绑定和packet序列留本地重放，模型只收到有限观察片段。"},
        {"field": "hash/debug/raw", "reason_cn": "hash、debug和原始长表只做身份校验，不作为模型解释正文。"},
        {"field": "legacy_llm/prewritten_q1q2", "reason_cn": "旧LLM文本或程序模板不发送，避免照抄。"},
    ]
    evidence_input = {
        "schema": "wpem_clean_evidence_input@1.0.0",
        "program_facts": program_facts,
        "evidence": items,
    }
    return evidence_input, lineage, omissions


def window_interpretation(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Finite descriptive attribution, never an inference of probability or EV."""
    w = _window(evidence)
    path, delta = w.get("path") or {}, w.get("delta") or {}
    classification = path.get("classification")
    path_text = {
        "single_direction_adverse_pressure": "同一保单窗口内，价格持续向不利责任区靠近。",
        "single_direction_favorable_relief": "同一保单窗口内，价格持续远离不利责任区。",
        "repeated_cross_zone": "同一保单窗口内，价格反复跨越责任区；末端位置不能概括这段路径。",
        "persistent_same_zone": "同一保单窗口内，价格主要停留在同一责任区；未形成单向缓解或承压路径。",
    }.get(classification, "同一保单连续窗口尚不足，当前归因只使用截面事实。")
    changes = delta.get("changes") or {}
    credit = changes.get("visible_credit_btc_delta")
    fee = changes.get("fee_btc_delta")
    compensation = ("同窗可收信用变薄，位置改善还需与当前补偿一起权衡。" if isinstance(credit, (int, float)) and credit < 0 else
                    "同窗可收信用增加；补偿增强需与责任区压力一起权衡。" if isinstance(credit, (int, float)) and credit > 0 else
                    "同窗可收信用没有明确变化。" if credit == 0 else
                    "补偿变化尚不可同口径比较，保留当前报价与费用账。")
    if isinstance(fee, (int, float)) and fee != 0:
        compensation += "费用假设也发生变化，信用变化不等同净补偿变化。"
    breaks = w.get("source_breaks") or {}
    attribution = []
    if breaks.get("candidate_rotation_observed"):
        attribution.append("换腿前后的资料分属不同保单，不能连接成适配改善轨迹。")
    if breaks.get("method_break_observed"):
        attribution.append("定义或来源边界已经变化，不能把断点解释成市场状态跃迁。")
    dependencies = w.get("source_dependency_diagnostics") or {}
    return {"path_cn": path_text, "compensation_cn": compensation,
            "breaks_cn": attribution, "path_classification": classification,
            "window_status": (w.get("evidence_strength") or {}).get("status"),
            "delta_status": delta.get("status"), "source_dependencies": deepcopy(dependencies),
            "win_probability": None, "financial_values_changed": False}


def build_review_input(snapshot: Mapping[str, Any], evidence: Mapping[str, Any], *, research_only=False, model=None):
    return _build_review_input(
        snapshot,
        evidence,
        research_only=research_only,
        model=model,
        routing_review_input_schema=ROUTING_REVIEW_INPUT_SCHEMA,
    )


def _build_review_input(snapshot: Mapping[str, Any], evidence: Mapping[str, Any], *, research_only=False, model=None,
                        routing_review_input_schema: str = ROUTING_REVIEW_INPUT_SCHEMA):
    _validate_review_evidence(evidence, snapshot)
    current_relation_owned_path = routing_review_input_schema in _RELATION_OWNED_INPUT_SCHEMAS and has_routing(evidence)
    if current_relation_owned_path:
        try:
            result = legacy._build_review_facts(snapshot, research_only=research_only)
        except AttributeError as exc:
            raise legacy.ReviewValidationError("current review input requires canonical review facts builder") from exc
    else:
        result = legacy.build_review_input(snapshot, research_only=research_only)
    include_contextual = routing_review_input_schema in _CONTEXTUAL_INPUT_SCHEMAS
    contextual_reads = _contextual_market_reads(snapshot, evidence) if include_contextual else None
    if routing_review_input_schema in _CONTEXTUAL_REF_INPUT_SCHEMAS:
        contextual_reads = _with_contextual_item_refs(contextual_reads)
    routing_projection = _compact_routing_projection(
        evidence,
        include_contextual=include_contextual,
        contextual_market_reads=contextual_reads,
    )
    routed = routing_projection is not None
    result["schema"] = routing_review_input_schema if routed else REVIEW_INPUT_SCHEMA
    result["allowed_refs"] = sorted(set(result["allowed_refs"] + ["wpem.window", "wpem.compensation", "wpem.source_method_break"]))
    result["response_protocol"] = response_protocol_for_input(result)
    result["protocol_role"] = ROLE
    result["llm_identity"] = {
        "requested_model": model or (evidence.get("release_identity") or {}).get("llm_model"),
        "protocol": result["response_protocol"],
    }
    w = _window(evidence)
    interpretation = window_interpretation(evidence)
    result["wpem"] = _compact_wpem_for_routing(
        evidence,
        interpretation,
        include_observed_points=routing_review_input_schema in _CONTEXTUAL_REF_INPUT_SCHEMAS,
    ) if routed else {
        "schema": evidence["schema"], "evidence_hash": evidence["evidence_hash"],
        "candidate_id": evidence["candidate_id"], "snapshot_id": evidence["snapshot_id"],
        "current_cross_section": deepcopy(evidence["current_cross_section"]),
        "same_candidate_window": deepcopy(evidence["same_candidate_window"]),
        "risk_reference_status": deepcopy(evidence["risk_reference_status"]),
        "quote_fee_deterministic_responsibility": deepcopy(evidence["quote_fee_deterministic_responsibility"]),
        "counter_catalog_gap_reassess": deepcopy(evidence["counter_catalog_gap_reassess"]),
        "release_identity": deepcopy(evidence["release_identity"]),
        "window_evidence_hash": w["window_evidence_hash"],
        "interpretation": interpretation,
    }
    if routed:
        result["evidence_routing"] = routing_projection
        result["routing_identity"] = {
            "schema": routing_projection["routing_schema"],
            "routing_hash": routing_projection["routing_hash"],
        }
        result["source_facts"] = _compact_source_facts_for_routing(
            result.get("source_facts") or {}, routing_projection, contextual_reads)
        refs = _catalog_refs(routing_projection["selection_summary"])
        result["allowed_refs"] = sorted(set(result["allowed_refs"]).union(refs))
        if _supports_grounded_explanation(result):
            result["allowed_refs"] = sorted(set(result["allowed_refs"]).union(
                _contextual_market_read_refs(contextual_reads)
            ))
            if (isinstance(result.get("wpem"), Mapping)
                    and isinstance((result["wpem"].get("same_candidate_window") or {}), Mapping)
                    and result["wpem"]["same_candidate_window"].get("observed_points")):
                result["allowed_refs"] = sorted(set(result["allowed_refs"]).union({OBSERVED_POINTS_REF}))
        if routing_review_input_schema in _MAP_CONTEXT_INPUT_SCHEMAS:
            compact_map_context = _compact_map_context_for_routing(result.get("map_context"))
            if compact_map_context is not None:
                result["map_context"] = compact_map_context
            else:
                result.pop("map_context", None)
        else:
            result.pop("map_context", None)
    if current_relation_owned_path and routed:
        compose_decision_evidence(
            snapshot,
            evidence,
            result,
            contextual_market_reads=contextual_reads,
        )
    catalog = result.get("claim_catalog")
    if not isinstance(catalog, Mapping):
        catalog = legacy._build_claim_catalog(snapshot, result)
        result["claim_catalog"] = catalog
    if routed:
        # Routing becomes the finite semantic summary, so legacy MAP background
        # prose is kept out of the selectable product catalog to avoid duplicate
        # slow-context restatement.
        catalog["claims"] = [
            item for item in catalog.get("claims") or []
            if not str(item.get("id") or "").startswith("claim.q1.background.")
        ]
        catalog["counterclaims"] = [
            item for item in catalog.get("counterclaims") or []
            if not str(item.get("id") or "").startswith("counter.q1.background.")
        ]
    catalog["claims"].append(_entry("claim.wpem.current_attribution", "wpem.window", interpretation["path_cn"]))
    catalog["counterclaims"].append(_entry("counter.wpem.joint_tradeoff", "wpem.compensation", interpretation["compensation_cn"]))
    catalog["reassess_conditions"].append(_entry("reassess.wpem.changed_evidence", "wpem.window", "同一保单的报价、责任位置或合格窗口路径发生变化后，重新核对两问的联合取舍。"))
    required = {"claim_ids": ["claim.wpem.current_attribution"],
                "counter_ids": ["counter.wpem.joint_tradeoff"],
                "gap_ids": [], "reassess_condition_ids": ["reassess.wpem.changed_evidence"]}
    if (w.get("path") or {}).get("status") != "PATH_OBSERVED":
        catalog["gaps"].append(_entry("gap.wpem.window_insufficient", "wpem.window", "尚无可比较的同一保单窗口，路径增量暂不参与当前归因。"))
        required["gap_ids"].append("gap.wpem.window_insufficient")
    for index, text in enumerate(interpretation["breaks_cn"]):
        identifier = "counter.wpem.break_" + str(index)
        catalog["counterclaims"].append(_entry(identifier, "wpem.source_method_break", text))
        required["counter_ids"].append(identifier)
    result["wpem_selection_requirements"] = required
    if routed:
        routing_summary = routing_projection["selection_summary"]
        for role in ("claims", "counterclaims", "gaps", "reassess_conditions"):
            catalog[role].extend(deepcopy(routing_summary[role]))
        result["routing_selection_requirements"] = deepcopy(routing_summary["required_selection"])
    if routed:
        result["allowed_refs"] = sorted(set(result["allowed_refs"]).union(_catalog_refs(catalog)))
    result["claim_catalog_schema"] = _routing_catalog_schema_for_input_schema(result["schema"]) if routed else CATALOG_SCHEMA
    result["claim_catalog_hash"] = legacy._hash(catalog)
    result["request_contract"] = {
        "prompt_version": prompt_version_for_input(result),
        "provider_config_version": PROVIDER_CONFIG_VERSION,
        "selection_authority": "claim_catalog",
        "nested_catalogs_selectable": False,
        "selectable_catalog_hash": result["claim_catalog_hash"],
        "output_protocol": result["response_protocol"],
        "exact_output_keys": exact_output_keys_for_input(result),
    }
    if routed:
        result["request_contract"]["routing_schema"] = routing_projection["routing_schema"]
        result["request_contract"]["routing_hash"] = routing_projection["routing_hash"]
    if _supports_evidence_points(result):
        evidence_input, input_lineage, input_omissions = _clean_evidence_package(snapshot, result)
        clean_refs = [item["ref"] for item in evidence_input.get("evidence") or [] if isinstance(item.get("ref"), str)]
        result["evidence_input"] = evidence_input
        result["input_lineage"] = input_lineage
        result["input_omissions"] = input_omissions
        result["allowed_refs"] = sorted(set(result["allowed_refs"]).union(clean_refs))
        result["request_contract"].update({
            "selection_authority": "program_guardrails",
            "model_input_field": "evidence_input",
            "point_fields": ["text_cn", "evidence_refs"],
            "max_points_per_question": 4,
            "max_evidence_refs_per_point": 4,
        })
    if _supports_grounded_explanation(result):
        explanation_refs = sorted(set(result["allowed_refs"]).intersection(_explanation_allowed_refs(result)))
        result["explanation_contract"] = {
            "schema": "grounded_policy_explanation_contract@1.0.0",
            "fields": ["q1", "q2"],
            "item_fields": ["text_cn", "evidence_refs"],
            "max_text_cn_chars": 1200,
            "max_evidence_refs": 12,
            "allowed_evidence_refs": explanation_refs,
            "scope_cn": (
                "只解释当前冻结输入中的关键事实、变化、冲突及其对本保单的含义；不新增金额、概率、mu/M、因果确定性或交易许可。"
                "若引用同保单 observed_points，只能说明有限已观察片段，不称完整窗口趋势。"
            ),
        }
    result["projection_hash"] = legacy._hash({k: v for k, v in result.items() if k != "projection_hash"})
    return result


def _explanation_allowed_refs(review_input: Mapping[str, Any]) -> set[str]:
    refs = {str(item) for item in review_input.get("allowed_refs") or [] if isinstance(item, str) and item}
    wpem = review_input.get("wpem") if isinstance(review_input.get("wpem"), Mapping) else {}
    same_window = wpem.get("same_candidate_window") if isinstance(wpem.get("same_candidate_window"), Mapping) else {}
    if same_window.get("observed_points"):
        refs.add(OBSERVED_POINTS_REF)
    source_facts = review_input.get("source_facts") if isinstance(review_input.get("source_facts"), Mapping) else {}
    contextual = source_facts.get("contextual_market_reads") if isinstance(source_facts.get("contextual_market_reads"), Mapping) else None
    if contextual is not None:
        refs.add("source_facts.contextual_market_reads")
        refs.update(_contextual_market_read_refs(contextual))
    return refs


def _validate_explanation_text(value: Any) -> str:
    if not isinstance(value, str):
        raise legacy.ReviewValidationError("grounded explanation text missing")
    text = value.strip()
    if not text or len(text) > 1200:
        raise legacy.ReviewValidationError("grounded explanation text is empty or too long")
    if _BANNED_EXPLANATION_TEXT.search(text):
        raise legacy.ReviewValidationError("grounded explanation contains unsupported financial or execution claims")
    for phrase in _AFFIRMATIVE_EXPLANATION_PHRASES:
        start = 0
        while True:
            index = text.find(phrase, start)
            if index < 0:
                break
            prefix = text[max(0, index - 8):index]
            if not any(cue in prefix for cue in _NEGATION_CUES):
                raise legacy.ReviewValidationError("grounded explanation contains unsupported financial or execution claims")
            start = index + len(phrase)
    return text


def _validate_explanation_item(value: Any, allowed_refs: set[str], question: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _EXPLANATION_ITEM_KEYS:
        raise legacy.ReviewValidationError("grounded explanation item fields mismatch")
    refs = value.get("evidence_refs")
    if (not isinstance(refs, list) or not refs
            or not all(isinstance(item, str) and item for item in refs)):
        raise legacy.ReviewValidationError("grounded explanation evidence refs missing")
    clean_refs = list(dict.fromkeys(refs))
    if len(clean_refs) > 12:
        raise legacy.ReviewValidationError("grounded explanation evidence refs too many")
    if any(ref not in allowed_refs for ref in clean_refs):
        raise legacy.ReviewValidationError("grounded explanation cites an unavailable fact")
    return {
        "text_cn": _validate_explanation_text(value.get("text_cn")),
        "evidence_refs": clean_refs,
    }


def _validate_grounded_explanation(payload: Mapping[str, Any], review_input: Mapping[str, Any]) -> dict[str, Any]:
    explanation = payload.get("explanation")
    if not isinstance(explanation, Mapping) or set(explanation) != {"q1", "q2"}:
        raise legacy.ReviewValidationError("grounded explanation is missing")
    allowed_refs = _explanation_allowed_refs(review_input)
    return {
        "q1": _validate_explanation_item(explanation.get("q1"), allowed_refs, "q1"),
        "q2": _validate_explanation_item(explanation.get("q2"), allowed_refs, "q2"),
    }


def _catalog_grounding_lookup(review_input: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    catalog = review_input.get("claim_catalog") if isinstance(review_input.get("claim_catalog"), Mapping) else {}
    for role in ("claims", "counterclaims", "gaps", "reassess_conditions"):
        for item in catalog.get(role) or []:
            if not isinstance(item, Mapping) or not isinstance(item.get("ref"), str):
                continue
            ref = item["ref"]
            target = lookup.setdefault(ref, {"ref": ref, "source_path": "claim_catalog", "provided": {"catalog_items": []}})
            target["provided"]["catalog_items"].append({
                "role": role,
                "id": item.get("id"),
                "text_cn": item.get("text_cn") or item.get("claim_cn"),
                "q1_reason_cn": item.get("q1_reason_cn"),
                "q2_reason_cn": item.get("q2_reason_cn"),
                "evidence_ids": deepcopy(item.get("evidence_ids") or []),
            })
    return lookup


def _map_source_records_by_id(map_context: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    manifest = map_context.get("source_manifest") if isinstance(map_context.get("source_manifest"), Mapping) else {}
    records: dict[str, Mapping[str, Any]] = {}
    for record in manifest.get("source_records") or []:
        if isinstance(record, Mapping) and isinstance(record.get("record_id"), str):
            records[record["record_id"]] = record
    return records


def _map_source_records_for(row: Mapping[str, Any], records_by_id: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for source_ref in row.get("source_refs") or []:
        if not isinstance(source_ref, Mapping):
            continue
        record_id = source_ref.get("record_id")
        if not isinstance(record_id, str):
            continue
        record = records_by_id.get(record_id)
        out.append(deepcopy(record) if record is not None else {"record_id": record_id, "missing_in_manifest": True})
    return out


def _attach_map_grounding(lookup: dict[str, dict[str, Any]], review_input: Mapping[str, Any]) -> None:
    map_context = review_input.get("map_context") if isinstance(review_input.get("map_context"), Mapping) else {}
    if not map_context:
        return
    records_by_id = _map_source_records_by_id(map_context)
    groups = (
        ("map_context.usable_background", "usable_background"),
        ("map_context.usable_references", "usable_references"),
    )
    for source_path, key in groups:
        for row in map_context.get(key) or []:
            if not isinstance(row, Mapping) or not isinstance(row.get("ref"), str):
                continue
            lookup[row["ref"]] = {
                "ref": row["ref"],
                "source_path": source_path,
                "provided": {
                    "record": deepcopy(row),
                    "source_records": _map_source_records_for(row, records_by_id),
                    "map_gaps": deepcopy(map_context.get("gaps") or []),
                    "map_id": map_context.get("map_id"),
                    "cutoff_at_ms": map_context.get("cutoff_at_ms"),
                    "price_basis": map_context.get("price_basis"),
                },
            }


def _attach_clean_evidence_grounding(lookup: dict[str, dict[str, Any]], review_input: Mapping[str, Any]) -> None:
    evidence_input = review_input.get("evidence_input") if isinstance(review_input.get("evidence_input"), Mapping) else {}
    lineage = review_input.get("input_lineage") if isinstance(review_input.get("input_lineage"), Mapping) else {}
    for item in evidence_input.get("evidence") or []:
        if not isinstance(item, Mapping) or not isinstance(item.get("ref"), str):
            continue
        ref = item["ref"]
        ref_lineage = lineage.get(ref) if isinstance(lineage.get(ref), Mapping) else {}
        lookup[ref] = {
            "ref": ref,
            "source_path": ref_lineage.get("source_path") or "evidence_input.evidence",
            "provided": deepcopy(item),
            "lineage": deepcopy(ref_lineage),
        }


def _grounding_lookup(review_input: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    lookup = _catalog_grounding_lookup(review_input)
    _attach_map_grounding(lookup, review_input)
    wpem = review_input.get("wpem") if isinstance(review_input.get("wpem"), Mapping) else {}
    interpretation = wpem.get("interpretation") if isinstance(wpem.get("interpretation"), Mapping) else {}
    same_window = wpem.get("same_candidate_window") if isinstance(wpem.get("same_candidate_window"), Mapping) else {}
    if interpretation:
        lookup["wpem.window"] = {
            "ref": "wpem.window",
            "source_path": "wpem.interpretation",
            "provided": {
                "path_cn": interpretation.get("path_cn"),
                "breaks_cn": deepcopy(interpretation.get("breaks_cn") or []),
                "window_status": interpretation.get("window_status"),
                "delta_status": interpretation.get("delta_status"),
            },
        }
        lookup["wpem.compensation"] = {
            "ref": "wpem.compensation",
            "source_path": "wpem.interpretation",
            "provided": {
                "compensation_cn": interpretation.get("compensation_cn"),
                "financial_values_changed": interpretation.get("financial_values_changed"),
            },
        }
        lookup["wpem.source_method_break"] = {
            "ref": "wpem.source_method_break",
            "source_path": "wpem.interpretation.breaks_cn",
            "provided": {
                "breaks_cn": deepcopy(interpretation.get("breaks_cn") or []),
                "source_dependencies": deepcopy(interpretation.get("source_dependencies") or {}),
            },
        }
    if same_window.get("observed_points"):
        lookup[OBSERVED_POINTS_REF] = {
            "ref": OBSERVED_POINTS_REF,
            "source_path": "wpem.same_candidate_window.observed_points",
            "provided": {
                "scope_cn": same_window.get("observed_points_scope_cn"),
                "window_status": same_window.get("status"),
                "delta_status": (((same_window.get("LevelDeltaPath") or {}).get("delta") or {}).get("status")
                                 if isinstance(same_window.get("LevelDeltaPath"), Mapping) else None),
                "path_status": (((same_window.get("LevelDeltaPath") or {}).get("path") or {}).get("status")
                                if isinstance(same_window.get("LevelDeltaPath"), Mapping) else None),
                "observed_points": deepcopy(same_window.get("observed_points") or []),
            },
        }
    source_facts = review_input.get("source_facts") if isinstance(review_input.get("source_facts"), Mapping) else {}
    deterministic = review_input.get("deterministic_financials") if isinstance(review_input.get("deterministic_financials"), Mapping) else {}
    deterministic_fee = deterministic.get("fee") if isinstance(deterministic.get("fee"), Mapping) else None
    source_fee = source_facts.get("fee") if isinstance(source_facts.get("fee"), Mapping) else None
    if deterministic_fee is not None or source_fee is not None:
        lookup["fee"] = {
            "ref": "fee",
            "source_path": "deterministic_financials.fee/source_facts.fee",
            "provided": {
                "deterministic_fee": deepcopy(deterministic_fee),
                "source_fee": deepcopy(source_fee),
            },
        }
    contextual = source_facts.get("contextual_market_reads") if isinstance(source_facts.get("contextual_market_reads"), Mapping) else None
    if contextual is not None:
        lookup["source_facts.contextual_market_reads"] = {
            "ref": "source_facts.contextual_market_reads",
            "source_path": "source_facts.contextual_market_reads",
            "provided": deepcopy(contextual),
        }
        lookup.setdefault("evidence_routing.contextual_market_reads", {
            "ref": "evidence_routing.contextual_market_reads",
            "source_path": "source_facts.contextual_market_reads",
            "provided": deepcopy(contextual),
        })
        for item in contextual.get("items") or []:
            if not isinstance(item, Mapping):
                continue
            ref = item.get("ref") or _contextual_market_read_ref(item)
            if isinstance(ref, str) and ref:
                lookup[ref] = {
                    "ref": ref,
                    "source_path": "source_facts.contextual_market_reads.items",
                    "provided": deepcopy(item),
                }
    # Current point receipts must show exactly what the model received. Historical
    # lookup entries remain available only when no clean item supersedes them.
    _attach_clean_evidence_grounding(lookup, review_input)
    return lookup


def _with_grounding_evidence(explanation: Mapping[str, Any], review_input: Mapping[str, Any]) -> dict[str, Any]:
    lookup = _grounding_lookup(review_input)
    result = deepcopy(dict(explanation))
    for key in ("q1", "q2"):
        item = result.get(key)
        if not isinstance(item, dict):
            continue
        item["evidence"] = [
            deepcopy(lookup[ref])
            for ref in item.get("evidence_refs") or []
            if ref in lookup
        ]
    return result


def _clean_evidence_refs(review_input: Mapping[str, Any]) -> set[str]:
    evidence_input = review_input.get("evidence_input") if isinstance(review_input.get("evidence_input"), Mapping) else {}
    return {
        item["ref"] for item in evidence_input.get("evidence") or []
        if isinstance(item, Mapping) and isinstance(item.get("ref"), str) and item.get("ref")
    }


def _clean_evidence_by_ref(review_input: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    evidence_input = review_input.get("evidence_input") if isinstance(review_input.get("evidence_input"), Mapping) else {}
    return {
        item["ref"]: item for item in evidence_input.get("evidence") or []
        if isinstance(item, Mapping) and isinstance(item.get("ref"), str)
    }


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any((re.search(r"(?<![a-z])" + re.escape(needle.lower()) + r"(?![a-z])", lowered)
                if needle.isascii() and needle.isalpha() else needle.lower() in lowered) for needle in needles)


def _negated_near(text: str, index: int, *, span: int = 12) -> bool:
    prefix = re.split(r"[。；;，,！？!?]", text[max(0, index - span):index])[-1]
    prefix = prefix.replace("不利", "").replace("不同", "")
    return any(cue in prefix for cue in _NEGATION_CUES)


def _execution_phrase_negated(text: str, index: int, phrase: str) -> bool:
    phrase_lower = phrase.lower()
    before = text[max(0, index - 24):index].lower()
    after = text[index + len(phrase):index + len(phrase) + 16].lower()
    if phrase_lower in {"filled", "executed"}:
        boolean = re.match(r"\s*[:=]\s*(true|false|1|0)\b", after)
        negated_identifier = re.search(r"(?:^|[^a-z])(?:not|non|un)[_\-\s]*$", before)
        if negated_identifier:
            if boolean is not None and boolean.group(1) in {"false", "0"}:
                return False
            return True
        if boolean is not None and boolean.group(1) in {"false", "0"}:
            return True
    if _negated_near(text, index, span=14):
        return True
    return False


def _is_ascii_token_match(text: str, index: int, phrase: str) -> bool:
    if not phrase.isascii() or not phrase.isalpha():
        return True
    before = text[index - 1] if index > 0 else ""
    after_index = index + len(phrase)
    after = text[after_index] if after_index < len(text) else ""
    return not (before.isascii() and before.isalpha()) and not (after.isascii() and after.isalpha())


def _affirmative_phrase(text: str, phrases: tuple[str, ...]) -> str | None:
    for phrase in phrases:
        start = 0
        while True:
            index = text.find(phrase, start)
            if index < 0:
                break
            if not _negated_near(text, index):
                return phrase
            start = index + len(phrase)
    return None


def _ref_matches(refs: list[str], *needles: str) -> bool:
    lowered = [ref.lower() for ref in refs]
    return any(any(needle.lower() in ref for needle in needles) for ref in lowered)


def _validate_topic_direct_refs(text: str, refs: list[str]) -> None:
    topics = (
        (("主动流", "方向流", "CVD"), ("flow",)),
        (("Funding", "funding", "资金费"), ("funding.current", "funding", "map.funding")),
        (("Skew", "skew", "偏斜", "SRD", "srd"), ("skew",)),
        (("ETF", "etf"), ("map.etf",)),
        (("OI", "open interest", "持仓"), ("map.oi",)),
        (("CP", "链上成本", "成本样本"), ("map.cost",)),
        (("RPL", "已实现损益", "已实现盈亏", "已实现利润", "已实现盈利", "实现亏损", "实现损益"), ("map.realized",)),
        (("flip", "Flip", "翻转"), ("space.flip",)),
        (("KPF", "成交接受区"), ("map.kpf.",)),
        (("macro", "宏观", "美元指数", "美债"), ("macro",)),
        (("风险参考", "支持域", "μ", "mu_ref"), ("risk",)),
        (("费用", "fee"), ("fee", "wpem.fragments", "wpem.delta")),
        (("报价", "权利金", "保费"), ("quote", "wpem.level", "wpem.fragments", "wpem.delta")),
    )
    for words, required_refs in topics:
        topic_text = text
        if required_refs == ("flow",):
            # A prohibition on double confirmation does not assert flow facts.
            # Any separate statement about flow must still carry its own ref.
            topic_text = re.sub(r"(?:不能|不得|不应|不)(?:与|和)主动流(?:交叉|共同|独立|相互)确认", "", text)
        if required_refs[0] == "quote" and "map.cost" in refs:
            # CP's explicit incomparable-price boundary is not a quote fact.
            # Any affirmative quote observation still needs its direct ref.
            topic_text = re.sub(r"(?:与|相对)(?:Deribit)?实时报价不可比", "", topic_text)
        if required_refs[0] == "fee" and _ref_matches(refs, "funding.current", "funding", "map.funding"):
            # Funding's cost background is not the option entry/delivery fee.
            # An additional assertion about 本单费用 still requires fee.
            topic_text = re.sub(r"(?<!本单)(?<!期权)(?<!入场)(?<!入局)(?<!交割)费用背景", "资金费背景", topic_text)
        if _has_any(topic_text, words) and not _ref_matches(refs, *required_refs):
            raise legacy.ReviewValidationError("evidence point mentions a topic without its direct evidence ref")


def _validate_cost_basis_text(text: str, refs: list[str], evidence_by_ref: Mapping[str, Mapping[str, Any]]) -> None:
    if "map.cost" not in refs or not _has_any(text, ("CP", "链上成本", "成本")):
        return
    cost_item = evidence_by_ref.get("map.cost") or {}
    relations = cost_item.get("relations") if isinstance(cost_item.get("relations"), Mapping) else {}
    source_relations = {key: value for key, value in relations.items() if str(key).endswith("_vs_source_price")}
    directions = {"高于": "above_source_price", "above": "above_source_price",
                  "低于": "below_source_price", "below": "below_source_price",
                  "等于": "equal_source_price", "equal": "equal_source_price"}
    for clause in re.split(r"[。；;，,]", text):
        for match in re.finditer(r"(高于|低于|等于|above|below|equal).{0,16}(源价|Bitview|source)", clause, re.IGNORECASE):
            if _negated_near(clause, match.start(1), span=14):
                continue
            # A missing long-term comparator must not silence an available short-term one.
            names = [name for name, words in (("sth", ("STH", "短期")), ("lth", ("LTH", "长期")),
                                              ("all", ("全体", "总体", "cp_all"))) if _has_any(clause, words)]
            compared = [source_relations.get("cp_" + name + "_usd_vs_source_price") for name in names] if names else list(source_relations.values())
            if not compared or any(value is None or str(value).startswith("unknown") for value in compared):
                raise legacy.ReviewValidationError("cost basis source-price comparison unavailable for this observation day")
            if any(value != directions[match.group(1).lower()] for value in compared):
                raise legacy.ReviewValidationError("cost basis comparison contradicts the provided source-price relation")
    comparison_terms = ("现价", "当前报价", "当前价格", "Deribit", "live", "实时", "冻结报价")
    safe_incomparability = ("不可比", "不能与", "不应与", "不得与", "不是相对", "无法与")
    if _has_any(text, comparison_terms) and not _has_any(text, safe_incomparability):
        raise legacy.ReviewValidationError("cost basis comparison must stay on its stated source basis")


def _validate_flip_text(text: str, refs: list[str], evidence_by_ref: Mapping[str, Mapping[str, Any]]) -> None:
    if "space.flip" not in refs:
        return
    flip = evidence_by_ref.get("space.flip") or {}
    relations = flip.get("relations") if isinstance(flip.get("relations"), Mapping) else {}
    if relations.get("price_vs_flip") != "PRICE_ABOVE_FLIP":
        return
    patterns = (
        r"(?P<op>低于|below).{0,8}(flip|翻转)",
        r"(flip|翻转位|翻转点|翻转).{0,12}(?P<op>高于|above).{0,12}(现价|当前|价格|报价)",
        r"(flip|翻转位|翻转点|翻转).{0,12}(?P<op>位于|处于|在).{0,8}(现价|当前价格|价格|报价).{0,3}上方",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if not _negated_near(text, match.start("op"), span=12):
                raise legacy.ReviewValidationError("flip direction contradicts the provided PRICE_ABOVE_FLIP relation")


def _validate_baseline_text(text: str, refs: list[str]) -> None:
    high_terms = ("偏高", "较高", "高企", "高位", "high")
    if "map.realized" not in refs:
        return
    realized_terms = ("RPL", "rpl", "已实现", "实现亏损", "实现损益", "亏损占比")
    clauses = re.split(r"[。；;，,]", text)
    for clause in clauses:
        if _has_any(clause, realized_terms) and _affirmative_phrase(clause, high_terms):
            raise legacy.ReviewValidationError("baseline comparison unavailable for realized-pnl evidence")


def _validate_known_status_and_dependency(text: str, refs: list[str], evidence_by_ref: Mapping[str, Mapping[str, Any]]) -> None:
    risk = (evidence_by_ref.get("risk") or {}).get("facts") or {}
    if risk.get("status") == "missing" and _has_any(text, ("风险参考", "支持域")):
        if _affirmative_phrase(text, ("未通过支持域校验", "未通过支持域验证", "支持域校验失败", "支持域验证失败")):
            raise legacy.ReviewValidationError("missing risk reference is not a failed support-domain validation")
    for clause in re.split(r"[。；;，,]", text):
        if not (_has_any(clause, ("Skew", "期权需求", "偏斜")) and _has_any(clause, ("空间", "Gamma", "flip"))):
            continue
        if _affirmative_phrase(clause, ("同源", "同一来源", "共享来源")):
            skew_group = (evidence_by_ref.get("skew") or {}).get("shared_dependency")
            space_groups = [(evidence_by_ref.get(ref) or {}).get("shared_dependency") for ref in refs if ref.startswith("space.")]
            if not skew_group or not space_groups or any(group != skew_group for group in space_groups):
                raise legacy.ReviewValidationError("shared source dependency is not provided for skew and option space")


def _validate_financial_execution_text(text: str) -> None:
    if re.search(r"(?:[μµ]\s*[=＝]|(?:^|[^A-Za-z])M(?:_?ref)?\s*[=＝]|M_ref\s*[=＝]|m_ref\s*[=＝])", text):
        raise legacy.ReviewValidationError("evidence point contains unsupported mu/M value")
    if re.search(r"[$＄]\s*\d", text) or re.search(r"\d+(?:\.\d+)?\s*(?:BTC|USD|USDT|美元|美金)", text, re.IGNORECASE):
        raise legacy.ReviewValidationError("evidence point contains unsupported financial value")
    if _affirmative_phrase(text, ("概率路径", "胜率路径")):
        raise legacy.ReviewValidationError("evidence point contains unsupported probability path")
    phrase = _affirmative_phrase(text, (
        "允许下单", "立即开仓", "确认正期望", "正期望成立", "确认补偿足够",
        "补偿足够成立", "足以覆盖", "风险已覆盖", "已经覆盖风险",
    ))
    if phrase is not None:
        raise legacy.ReviewValidationError("evidence point contains unsupported EV, coverage or execution claim")
    for phrase in ("已成交", "已经成交", "实际成交", "成交入账", "入账", "计入账户", "已到账", "filled", "executed"):
        start = 0
        while True:
            index = text.lower().find(phrase.lower(), start)
            if index < 0:
                break
            if not _is_ascii_token_match(text, index, phrase):
                start = index + len(phrase)
                continue
            if not _execution_phrase_negated(text, index, phrase):
                raise legacy.ReviewValidationError("evidence point rewrites a frozen quote as filled or booked")
            start = index + len(phrase)
    if _affirmative_phrase(text, ("盈亏平衡", "赔付表", "赔付曲线", "payout", "breakeven", "保护腿上方趋于平坦")):
        raise legacy.ReviewValidationError("evidence point must not rewrite the deterministic payoff curve")


def _validate_evidence_point_item(value: Any, review_input: Mapping[str, Any], question: str,
                                  index: int) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _EVIDENCE_POINT_KEYS:
        raise legacy.ReviewValidationError("evidence point fields mismatch")
    text_value = value.get("text_cn")
    if not isinstance(text_value, str):
        raise legacy.ReviewValidationError("evidence point text missing")
    text = text_value.strip()
    if not text or len(text) > 1200:
        raise legacy.ReviewValidationError("evidence point text is empty or too long")
    refs = value.get("evidence_refs")
    if (not isinstance(refs, list) or not refs
            or not all(isinstance(item, str) and item for item in refs)):
        raise legacy.ReviewValidationError("evidence point refs missing")
    clean_refs = list(dict.fromkeys(refs))
    if len(clean_refs) > 4:
        raise legacy.ReviewValidationError("evidence point refs too many")
    allowed = _clean_evidence_refs(review_input)
    if any(ref not in allowed for ref in clean_refs):
        raise legacy.ReviewValidationError("evidence point cites an unavailable clean evidence ref")
    evidence_by_ref = _clean_evidence_by_ref(review_input)
    _validate_topic_direct_refs(text, clean_refs)
    _validate_cost_basis_text(text, clean_refs, evidence_by_ref)
    _validate_flip_text(text, clean_refs, evidence_by_ref)
    _validate_baseline_text(text, clean_refs)
    _validate_known_status_and_dependency(text, clean_refs, evidence_by_ref)
    _validate_financial_execution_text(text)
    return {"text_cn": text, "evidence_refs": clean_refs}


def validate_evidence_points(payload: Mapping[str, Any], review_input: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    if not _supports_evidence_points(review_input):
        raise legacy.ReviewValidationError("evidence points protocol is not active for this input")
    if not isinstance(payload, Mapping) or set(payload) != set(EVIDENCE_POINTS_OUTPUT_KEYS):
        raise legacy.ReviewValidationError("evidence point response fields do not match the frozen schema")
    if payload.get("response_protocol") != EVIDENCE_POINTS_RESPONSE_PROTOCOL:
        raise legacy.ReviewValidationError("WPEM response protocol mismatch")
    result: dict[str, list[dict[str, Any]]] = {}
    for question in ("q1", "q2"):
        points = payload.get(question)
        if not isinstance(points, list) or not (1 <= len(points) <= 4):
            raise legacy.ReviewValidationError("evidence point question must contain one to four points")
        result[question] = [
            _validate_evidence_point_item(point, review_input, question, index)
            for index, point in enumerate(points, start=1)
        ]
    return result


def _with_evidence_point_grounding(points: Mapping[str, list[dict[str, Any]]],
                                   review_input: Mapping[str, Any]) -> dict[str, Any]:
    lookup = _grounding_lookup(review_input)
    out: dict[str, Any] = {}
    for question in ("q1", "q2"):
        question_points = []
        union_refs: list[str] = []
        union_evidence: list[dict[str, Any]] = []
        for point in points.get(question) or []:
            refs = list(point.get("evidence_refs") or [])
            evidence_rows = [
                deepcopy(lookup[ref])
                for ref in refs
                if ref in lookup
            ]
            for ref in refs:
                if ref not in union_refs:
                    union_refs.append(ref)
            for row in evidence_rows:
                if not any(existing.get("ref") == row.get("ref") for existing in union_evidence):
                    union_evidence.append(deepcopy(row))
            point_out = deepcopy(point)
            point_out["evidence"] = evidence_rows
            question_points.append(point_out)
        out[question] = {
            "points": question_points,
            "text_cn": "\n".join(point["text_cn"] for point in question_points),
            "evidence_refs": union_refs,
            "evidence": union_evidence,
        }
    return out


def _selection_payload_for_program_guardrails(review_input: Mapping[str, Any]) -> dict[str, Any]:
    payload = required_selection_payload(review_input)
    if _supports_evidence_points(review_input):
        payload = {key: deepcopy(value) for key, value in payload.items() if key != "explanation"}
    return payload


def build_decision_trace(review_input: Mapping[str, Any], normalized: Mapping[str, Any] | None = None) -> dict[str, Any]:
    evidence_input = review_input.get("evidence_input") if isinstance(review_input.get("evidence_input"), Mapping) else {}
    lineage = review_input.get("input_lineage") if isinstance(review_input.get("input_lineage"), Mapping) else {}
    evidence_items = [
        {
            **deepcopy(dict(item)),
            "lineage": deepcopy(lineage.get(item.get("ref")) if isinstance(lineage.get(item.get("ref")), Mapping) else {}),
        }
        for item in evidence_input.get("evidence") or []
        if isinstance(item, Mapping)
    ]
    produced_refs = [item["ref"] for item in evidence_items if isinstance(item.get("ref"), str)]
    consumed_refs: list[str] = []
    points: list[dict[str, Any]] = []
    if isinstance(normalized, Mapping):
        explanation = normalized.get("llm_explanation") if isinstance(normalized.get("llm_explanation"), Mapping) else {}
        for question in ("q1", "q2"):
            q_points = (explanation.get(question) or {}).get("points") if isinstance(explanation.get(question), Mapping) else []
            for index, point in enumerate(q_points or [], start=1):
                if not isinstance(point, Mapping):
                    continue
                refs = [ref for ref in point.get("evidence_refs") or [] if isinstance(ref, str)]
                for ref in refs:
                    if ref not in consumed_refs:
                        consumed_refs.append(ref)
                points.append({
                    "question": question,
                    "index": index,
                    "text_cn": point.get("text_cn"),
                    "evidence_refs": refs,
                })
    unreferenced_reason = "本次LLM点未引用该clean evidence。" if normalized is not None else "尚未调用LLM或尚未归一化。"
    source_groups = (
        ("data.contract", "本单合同责任", ("contract",)),
        ("data.economics", "并列经济事实：报价、费用与风险参考", ("quote", "fee", "risk")),
        ("data.wpem", "时间纵截面：WPEM窗口与片段", ("wpem.",)),
        ("data.context", "当前横截面：市场读数与本单关系", ("flow", "tmv", "funding", "skew", "macro")),
        ("data.space", "当前横截面：空间条件与flip", ("space.",)),
        ("data.map", "MAP慢背景与参考区", ("map.",)),
    )
    layers = []
    for layer, title, prefixes in source_groups:
        layer_refs = [
            ref for ref in produced_refs
            if any(ref == prefix or ref.startswith(prefix) for prefix in prefixes)
        ]
        layers.append({
            "layer": layer,
            "title_cn": title,
            "produced_refs": layer_refs,
            "consumed_refs": [ref for ref in layer_refs if ref in consumed_refs],
            "omitted": [
                {"ref": ref, "reason_cn": unreferenced_reason}
                for ref in layer_refs
                if ref not in consumed_refs
            ],
        })
    layers.append({
        "layer": "clean_input",
        "title_cn": "发送给模型的clean evidence",
        "produced_refs": produced_refs,
        "consumed_refs": consumed_refs,
        "omitted": [
            {"ref": ref, "reason_cn": unreferenced_reason}
            for ref in produced_refs
            if ref not in consumed_refs
        ],
    })
    layers.append({
        "layer": "llm",
        "title_cn": "LLM逐点综合",
        "produced_refs": [f"{point['question']}.{point['index']}" for point in points],
        "consumed_refs": consumed_refs,
        "omitted": [],
    })
    layers.append({
        "layer": "q1q2",
        "title_cn": "回执中的两问与引用",
        "produced_refs": [],
        "consumed_refs": consumed_refs if normalized is not None else [],
        "omitted": [],
    })
    binding = {
        "candidate_id": review_input.get("candidate_id"),
        "snapshot_id": review_input.get("snapshot_id"),
        "schema": review_input.get("schema"),
        "response_protocol": review_input.get("response_protocol"),
        "projection_hash": review_input.get("projection_hash"),
        "claim_catalog_hash": review_input.get("claim_catalog_hash"),
        "evidence_input_hash": legacy._hash(evidence_input) if evidence_input else None,
    }
    routing_identity_value = review_input.get("routing_identity") if isinstance(review_input.get("routing_identity"), Mapping) else None
    if routing_identity_value:
        binding["routing_identity"] = deepcopy(routing_identity_value)
    map_context = review_input.get("map_context") if isinstance(review_input.get("map_context"), Mapping) else {}
    if map_context.get("map_id") is not None:
        binding["map_id"] = map_context.get("map_id")
    return {
        "scope_cn": "按并列证据维度展示信息汇合，不表示组件间严格串行依赖。产出指本次提供的证据，采用指模型逐点引用；未引用不等于上游未消费。LLM Input只冻结与序列化，审计为只读旁路。",
        "binding": binding,
        "layers": layers,
        "input_evidence": evidence_items,
        "audit_only": deepcopy(review_input.get("input_omissions") or []),
        "points": points,
    }


def validate_wire_response(snapshot, payload, *, review_input, evidence):
    rebuilt = _rebuilt_review_input_for_schema(
        snapshot,
        evidence,
        research_only=(review_input.get("research_scope") or {}).get("research_only") is True,
        model=(review_input.get("llm_identity") or {}).get("requested_model"),
        schema=review_input.get("schema"),
    )
    if review_input != rebuilt:
        raise legacy.ReviewValidationError("WPEM review input replay mismatch")
    expected_protocol = response_protocol_for_input(rebuilt)
    if payload.get("response_protocol") != expected_protocol:
        raise legacy.ReviewValidationError("WPEM response protocol mismatch")
    expected_keys = set(exact_output_keys_for_input(rebuilt))
    if not isinstance(payload, Mapping) or set(payload) != expected_keys:
        if _supports_evidence_points(rebuilt):
            raise legacy.ReviewValidationError("evidence point response fields do not match the frozen schema")
        if _supports_grounded_explanation(rebuilt) and isinstance(payload, Mapping) and "explanation" not in payload:
            raise legacy.ReviewValidationError("grounded explanation is missing")
        raise legacy.ReviewValidationError("review fields do not match the frozen schema")
    if _supports_evidence_points(rebuilt):
        evidence_points = validate_evidence_points(payload, rebuilt)
        grounded_points = _with_evidence_point_grounding(evidence_points, rebuilt)
        guardrail_payload = _selection_payload_for_program_guardrails(rebuilt)
        normalized = legacy._normalize_bounded_selection(snapshot, guardrail_payload, review_input=rebuilt)
        interpretation = rebuilt["wpem"]["interpretation"]
        normalized["response_protocol"] = expected_protocol
        normalized["selection_origin"] = "program_guardrails"
        normalized["summary_cn"] = "LLM只对本次clean evidence做逐点综合；目录选择和金融账由本地程序守护，不代表交易许可。"
        normalized["q1"]["window_attribution_cn"] = interpretation["path_cn"]
        normalized["q1"]["window_breaks_cn"] = interpretation["breaks_cn"]
        normalized["q2"]["window_tradeoff_cn"] = interpretation["compensation_cn"]
        normalized["q2"]["joint_window_summary_cn"] = interpretation["path_cn"] + interpretation["compensation_cn"]
        _attach_routing_observations(normalized, rebuilt, guardrail_payload)
        normalized["llm_explanation"] = deepcopy(grounded_points)
        normalized["q1"]["llm_explanation"] = deepcopy(grounded_points["q1"])
        normalized["q2"]["llm_explanation"] = deepcopy(grounded_points["q2"])
        normalized["wpem_evidence"] = deepcopy(evidence)
        normalized["review_validity"] = review_binding(
            snapshot, evidence, rebuilt,
            model=rebuilt["llm_identity"]["requested_model"],
        )
        normalized["decision_trace"] = build_decision_trace(rebuilt, normalized)
        return normalized
    for required_key, selected_key in (("claim_ids", "selected_claim_ids"), ("counter_ids", "selected_counter_ids"),
                                       ("gap_ids", "selected_gap_ids"), ("reassess_condition_ids", "selected_reassess_condition_ids")):
        if not set(rebuilt["wpem_selection_requirements"][required_key]).issubset(payload.get(selected_key) or []):
            raise legacy.ReviewValidationError("WPEM required evidence selection missing")
    routing_requirements = rebuilt.get("routing_selection_requirements") if isinstance(rebuilt.get("routing_selection_requirements"), Mapping) else {}
    for required_key, selected_key in (("claim_ids", "selected_claim_ids"), ("counter_ids", "selected_counter_ids"),
                                       ("gap_ids", "selected_gap_ids"), ("reassess_condition_ids", "selected_reassess_condition_ids")):
        if not set(routing_requirements.get(required_key) or []).issubset(payload.get(selected_key) or []):
            raise legacy.ReviewValidationError("routing required evidence selection missing")
    grounded_explanation = _validate_grounded_explanation(payload, rebuilt) if _supports_grounded_explanation(rebuilt) else None
    if grounded_explanation is not None:
        grounded_explanation = _with_grounding_evidence(grounded_explanation, rebuilt)
    normalized = legacy._normalize_bounded_selection(snapshot, payload, review_input=rebuilt)
    interpretation = rebuilt["wpem"]["interpretation"]
    normalized["q1"]["window_attribution_cn"] = interpretation["path_cn"]
    normalized["q1"]["window_breaks_cn"] = interpretation["breaks_cn"]
    normalized["q2"]["window_tradeoff_cn"] = interpretation["compensation_cn"]
    normalized["q2"]["joint_window_summary_cn"] = interpretation["path_cn"] + interpretation["compensation_cn"]
    _attach_routing_observations(normalized, rebuilt, payload)
    if grounded_explanation is not None:
        normalized["llm_explanation"] = deepcopy(grounded_explanation)
        normalized["q1"]["llm_explanation"] = deepcopy(grounded_explanation["q1"])
        normalized["q2"]["llm_explanation"] = deepcopy(grounded_explanation["q2"])
    normalized["wpem_evidence"] = deepcopy(evidence)
    normalized["review_validity"] = review_binding(snapshot, evidence, rebuilt,
        model=rebuilt["llm_identity"]["requested_model"])
    return normalized


def _routing_catalog_entries(review_input: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    routing = review_input.get("evidence_routing") if isinstance(review_input.get("evidence_routing"), Mapping) else {}
    summary = routing.get("selection_summary") if isinstance(routing.get("selection_summary"), Mapping) else {}
    entries: dict[str, Mapping[str, Any]] = {}
    for role in ("claims", "counterclaims", "gaps", "reassess_conditions"):
        for item in summary.get(role) or []:
            if isinstance(item, Mapping) and isinstance(item.get("id"), str):
                entries[item["id"]] = item
    return entries


def _routing_trace_for_ids(entries: Mapping[str, Mapping[str, Any]], selected_ids: list[Any],
                           role_key: str) -> list[dict[str, Any]]:
    out = []
    reason_key = "q1_reason_cn" if role_key == "q1_role" else "q2_reason_cn"
    evidence_key = "q1_evidence_ids" if role_key == "q1_role" else "q2_evidence_ids"
    for item_id in selected_ids:
        entry = entries.get(str(item_id))
        if entry is None:
            continue
        if str(entry.get(role_key) or "NONE").upper() == "NONE":
            continue
        out.append({
            "selected_id": entry["id"],
            "text_cn": entry.get(reason_key) or entry.get("text_cn") or entry.get("claim_cn"),
            "block_id": entry.get("block_id"),
            "evidence_ids": deepcopy(entry.get(evidence_key) or entry.get("evidence_ids") or []),
        })
    return out


def _attach_routing_observations(normalized: dict[str, Any], review_input: Mapping[str, Any],
                                 payload: Mapping[str, Any]) -> None:
    entries = _routing_catalog_entries(review_input)
    if not entries:
        return
    all_ids = (
        list(payload.get("selected_claim_ids") or [])
        + list(payload.get("selected_counter_ids") or [])
        + list(payload.get("selected_gap_ids") or [])
        + list(payload.get("selected_reassess_condition_ids") or [])
    )
    for target, role_key in (("q1", "q1_role"), ("q2", "q2_role")):
        trace = _routing_trace_for_ids(entries, all_ids, role_key)
        normalized[target]["routing_observations_cn"] = [item["text_cn"] for item in trace if item.get("text_cn")]
        evidence_ids: list[str] = []
        for item in trace:
            for evidence_id in item.get("evidence_ids") or []:
                if evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)
        normalized[target]["routing_evidence_ids"] = evidence_ids
        normalized[target]["routing_observation_trace"] = trace


def review_binding(snapshot, evidence, review_input, *, model=None):
    routing = review_input.get("routing_identity") if isinstance(review_input.get("routing_identity"), Mapping) else {}
    result = {"schema": "wpem_product_review_validity@1.1.0" if routing else "wpem_product_review_validity@1.0.0", "candidate_id": snapshot["candidate_id"],
            "snapshot_id": snapshot["snapshot_id"], "quote_hash": legacy._hash(snapshot.get("market")),
            "quote_timestamp_ms": (snapshot.get("market") or {}).get("valuation_ts_ms"),
            "risk_hash": legacy._hash(snapshot.get("risk")),
            "window_evidence_hash": _window(evidence)["window_evidence_hash"],
            "review_evidence_hash": evidence["evidence_hash"], "review_input_version": review_input.get("schema"),
            "projection_hash": review_input["projection_hash"], "llm_protocol": response_protocol_for_input(review_input),
            "model": model, "valid_until_ms": (snapshot.get("contract") or {}).get("expiry_ms"),
            "cooldown_extends_validity": False}
    if routing:
        result["routing_schema"] = routing.get("schema")
        result["routing_hash"] = routing.get("routing_hash")
    return result


def review_status(review, snapshot, evidence, *, model=None, now_ms=None):
    if not review or review.get("status") not in {"OK", "RESEARCH_OK"}: return "NOT_RUN"
    binding = review.get("review_validity") or {}
    expected = review_binding(snapshot, evidence, build_review_input(snapshot, evidence,
        research_only=review.get("research_only") is True, model=model), model=model)
    for key in ("candidate_id", "snapshot_id", "quote_hash", "risk_hash", "window_evidence_hash", "review_evidence_hash", "review_input_version", "projection_hash", "llm_protocol", "routing_schema", "routing_hash"):
        if binding.get(key) != expected.get(key): return "STALE"
    if model is not None and (review.get("requested_model") or binding.get("model")) != model: return "STALE"
    if now_ms is not None:
        quote_ms=binding.get("quote_timestamp_ms")
        if type(quote_ms) is not int or not 0 <= now_ms-quote_ms <= legacy.underwriting.MAX_BOOK_AGE_MS: return "STALE"
        if now_ms > (binding.get("valid_until_ms") or 0): return "STALE"
    return "CURRENT"


def required_selection_payload(review_input):
    catalog = review_input["claim_catalog"]
    requirements = review_input["decision_relations"]["selection_requirements"]
    wpem = review_input["wpem_selection_requirements"]
    routing = review_input.get("routing_selection_requirements") if isinstance(review_input.get("routing_selection_requirements"), Mapping) else {}
    fields = (("claims", "claim_ids", "selected_claim_ids"), ("counterclaims", "counter_ids", "selected_counter_ids"),
              ("gaps", "gap_ids", "selected_gap_ids"), ("reassess_conditions", "reassess_condition_ids", "selected_reassess_condition_ids"))
    applicability = "UNABLE_TO_JUDGE"
    if _supports_evidence_points(review_input):
        catalog_ids = {
            item.get("id")
            for role in ("claims", "counterclaims", "gaps", "reassess_conditions")
            for item in (catalog.get(role) or [])
            if isinstance(item, Mapping)
        }
        hard_boundary_ids = {
            "counter.structure.opening_blocked",
            "counter.compensation.nonpositive_margin",
            "gap.compensation.mu_reference_missing",
        }
        facts = review_input.get("source_facts") or {}
        material_gap = legacy._material_gap({
            "market": facts.get("quote") or {},
            "fee": (facts.get("fee") or {}).get("fee") or {},
            "risk": (facts.get("risk") or {}).get("risk") or {},
        })
        applicability = "CONTESTED" if material_gap or catalog_ids.intersection(hard_boundary_ids) else "EXPLAINED"
    result = {"response_protocol": review_input.get("response_protocol", RESPONSE_PROTOCOL),
              "reviewed_snapshot_id": review_input["snapshot_id"],
              "projection_hash": review_input["projection_hash"], "claim_catalog_hash": review_input["claim_catalog_hash"], "applicability": applicability}
    for role, key, output in fields:
        ids = list(dict.fromkeys([*requirements.get(key, []), *wpem.get(key, []), *(routing.get(key, []) or [])]))
        existing = {r["id"] for r in catalog[role]}
        mandatory = {"gaps": ["gap.compensation.mu_reference_missing"],
                     "counterclaims": ["counter.structure.opening_blocked", "counter.compensation.nonpositive_margin"],
                     "reassess_conditions": ["reassess.structure_new_snapshot", "reassess.compensation_reprice"]}.get(role, [])
        ids = list(dict.fromkeys([*ids, *(identifier for identifier in mandatory if identifier in existing)]))
        if not ids and catalog[role]: ids.append(catalog[role][0]["id"])
        if not _supports_evidence_points(review_input) and len(ids) > legacy.MAX_SELECTED_IDS:
            raise legacy.ReviewValidationError("CATALOG_GAP: required IDs exceed bounded selection capacity")
        result[output] = ids
    if _supports_grounded_explanation(review_input):
        refs = _explanation_allowed_refs(review_input)
        context_refs = [ref for ref in sorted(refs) if ref.startswith("source_facts.contextual_market_reads.items.")]
        q1_refs = [ref for ref in ("wpem.window", "evidence_routing.contextual_market_reads") if ref in refs]
        q2_refs = [ref for ref in ("wpem.compensation", "evidence_routing.contextual_market_reads") if ref in refs]
        if OBSERVED_POINTS_REF in refs:
            q1_refs.append(OBSERVED_POINTS_REF)
            q2_refs.append(OBSERVED_POINTS_REF)
        if context_refs:
            q1_refs.append(context_refs[0])
            q2_refs.append(context_refs[-1])
        result["explanation"] = {
            "q1": {
                "text_cn": "结合窗口路径、路由机制和背景读数看，当前解释只能说明本保单附近的方向压力、竞争解释与资料缺口；μ未知时不能判断正期望或补偿足够。",
                "evidence_refs": list(dict.fromkeys(q1_refs or sorted(refs)[:1])),
            },
            "q2": {
                "text_cn": "结合补偿取舍、结构阻断和复核条件看，当前回执只给出研究解释；信用、费用和风险数字仍以旁边确定性账为准。",
                "evidence_refs": list(dict.fromkeys(q2_refs or sorted(refs)[:1])),
            },
        }
    return result
