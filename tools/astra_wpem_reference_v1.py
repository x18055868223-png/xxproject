"""Shadow-only WPEM reference qualification for the sealed Astra v1.1 asset.

This module terminates the old-risk-asset qualification attempt when the
strict as-of feature row or target/native-payout binding is not proven.  It
never imports the v1.1 model or inference modules, never scores a row, and
never creates a production risk reference.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

if __package__:
    from . import astra_candidate_features_v1 as candidate_features
    from . import astra_candidate_asof_v1 as packets
    from . import astra_wpem_native_v1 as native
else:
    import astra_candidate_features_v1 as candidate_features
    import astra_candidate_asof_v1 as packets
    import astra_wpem_native_v1 as native

QUALIFICATION_SCHEMA = "wpem_reference_qualification@1.0.0-shadow"
FEATURE_BINDING_SCHEMA = "wpem_reference_feature_binding@1.0.0-shadow"
TARGET_BINDING_SCHEMA = "wpem_reference_target_price_basis@1.0.0-shadow"
RESULT_SCHEMA = "wpem_reference_analysis@1.0.0-shadow"

ADMITTED_ASSET_SHA256 = "71fb23ff9cbb55a789cc1c95862e7e242d8647ce8ac883047d10bded01fd5860"
ADMITTED_PROTOCOL_SHA256 = "5f007cb512763a17bf475da38bee642c171ab7502e816c74a40091cde7ec828e"
TAIL_POLICY_EVIDENCE_SHA256 = "9a71f02de12f770cf634879b176e9087ce0e676636acdc287f909e8aa6afe560"
SCRIPT_DIR = Path(__file__).resolve().parent


def _read_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_bytes().decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("wpem_reference_json_object_required")
    return value


def _file_sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _find_symbol(tree: ast.AST, dotted_name: str) -> ast.AST | None:
    body = getattr(tree, "body", [])
    found: ast.AST | None = None
    for part in dotted_name.split("."):
        found = next(
            (
                node
                for node in body
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == part
            ),
            None,
        )
        if found is None:
            return None
        body = getattr(found, "body", [])
    return found


def _source_ref(relative: str, symbol: str, *, asset_sha256: str, protocol_sha256: str) -> dict[str, Any]:
    path = SCRIPT_DIR / relative
    raw = path.read_bytes()
    source = raw.decode("utf-8")
    tree = ast.parse(source)
    node = _find_symbol(tree, symbol)
    if node is None:
        raise ValueError("wpem_reference_source_symbol_missing:" + relative + ":" + symbol)
    source_definition = ast.get_source_segment(source, node)
    if not source_definition:
        raise ValueError("wpem_reference_source_definition_missing:" + relative + ":" + symbol)
    return {
        "file": "tools/" + relative,
        "symbol": symbol,
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "start_line": getattr(node, "lineno", None),
        "end_line": getattr(node, "end_lineno", None),
        "ast_sha256": hashlib.sha256(
            ast.dump(node, annotate_fields=True, include_attributes=False).encode("utf-8")
        ).hexdigest(),
        "source_definition_sha256": hashlib.sha256(source_definition.encode("utf-8")).hexdigest(),
        "source_definition": source_definition,
        "bound_asset_sha256": asset_sha256,
        "bound_protocol_sha256": protocol_sha256,
    }


def _tail_policy_evidence(model_path: str | Path) -> dict[str, Any]:
    evidence = Path(model_path).resolve().parents[1] / "validation" / "tail_probability_order.json"
    if not evidence.exists():
        return {"status": "missing", "expected_sha256": TAIL_POLICY_EVIDENCE_SHA256}
    sha = _file_sha(evidence)
    rows = json.loads(evidence.read_text(encoding="utf-8-sig"))
    selected = next((row for row in rows if row.get("candidate_id") == "catboost__statistical__D4"), None)
    if selected is None:
        raise ValueError("wpem_reference_tail_evidence_missing_statistical_d4")
    return {
        "status": "available" if sha == TAIL_POLICY_EVIDENCE_SHA256 else "hash_mismatch",
        "path": str(evidence),
        "sha256": sha,
        "expected_sha256": TAIL_POLICY_EVIDENCE_SHA256,
        "candidate_id": selected.get("candidate_id"),
        "rows": selected.get("rows"),
        "inconsistent_rows": selected.get("inconsistent_rows"),
        "max_excess": selected.get("max_excess"),
    }


def _require_catalog_identity(catalog: Mapping[str, Any]) -> dict[str, Any]:
    native.require_hash(catalog, "catalog_hash")
    asset = catalog.get("asset") or {}
    protocol = catalog.get("protocol") or {}
    if asset.get("sha256") != ADMITTED_ASSET_SHA256 or protocol.get("sha256") != ADMITTED_PROTOCOL_SHA256:
        raise ValueError("wpem_reference_catalog_identity_not_admitted")
    for section, expected in ((asset, ADMITTED_ASSET_SHA256), (protocol, ADMITTED_PROTOCOL_SHA256)):
        path = section.get("path")
        if not path or not Path(path).exists():
            raise ValueError("wpem_reference_catalog_asset_path_missing")
        if _file_sha(path) != expected or section.get("sha256") != expected:
            raise ValueError("wpem_reference_catalog_asset_hash_mismatch")
    exported = candidate_features.export_feature_catalog(asset["path"], protocol["path"])
    if dict(catalog) != exported:
        raise ValueError("wpem_reference_catalog_not_actual_export")
    return exported


def _require_packet_and_input(packet: Mapping[str, Any], catalog: Mapping[str, Any]) -> dict[str, Any]:
    packets.replay_packet(packet)
    model_input = packet.get("model_input")
    if not isinstance(model_input, Mapping):
        raise ValueError("wpem_reference_model_input_missing")
    rebuilt = candidate_features.build_feature_input(packet["snapshot"], catalog)
    if dict(model_input) != rebuilt:
        raise ValueError("wpem_reference_model_input_not_actual_rebuild")
    if model_input.get("catalog_hash") != catalog.get("catalog_hash"):
        raise ValueError("wpem_reference_catalog_packet_mismatch")
    if model_input.get("asset_sha256") != (catalog.get("asset") or {}).get("sha256"):
        raise ValueError("wpem_reference_asset_packet_mismatch")
    if model_input.get("protocol_sha256") != (catalog.get("protocol") or {}).get("sha256"):
        raise ValueError("wpem_reference_protocol_packet_mismatch")
    if packet.get("versions", {}).get("research_asset_sha256") != model_input.get("asset_sha256"):
        raise ValueError("wpem_reference_packet_asset_version_mismatch")
    if packet.get("versions", {}).get("research_protocol_sha256") != model_input.get("protocol_sha256"):
        raise ValueError("wpem_reference_packet_protocol_version_mismatch")
    identity = {
        "row": model_input.get("row"),
        "snapshot_id": packet["snapshot"]["snapshot_id"],
        "candidate_id": packet["snapshot"]["candidate_id"],
        "decision_asof_ms": packet["cutoff"]["decision_asof_ms"],
        "catalog_hash": catalog.get("catalog_hash"),
        "asset_sha256": (catalog.get("asset") or {}).get("sha256"),
        "protocol_sha256": (catalog.get("protocol") or {}).get("sha256"),
        "source_hashes": catalog.get("source_hashes"),
    }
    if model_input.get("input_row_hash") != packets.core._hash(identity):
        raise ValueError("wpem_reference_input_row_hash_mismatch")
    return dict(model_input)


def _feature_by_name(model_input: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    features = model_input.get("features") or []
    result = {str(item.get("name")): item for item in features if isinstance(item, Mapping) and item.get("name")}
    if len(result) != len(features):
        raise ValueError("wpem_reference_feature_list_invalid")
    return result


def _required_feature_names(catalog: Mapping[str, Any]) -> list[str]:
    return [str(item["name"]) for item in catalog.get("features") or [] if item.get("required")]


def _feature_support(catalog: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    item = next((row for row in catalog.get("features") or [] if row.get("name") == name), None)
    return (item or {}).get("training_support") or {}


def _classify_missing(feature: Mapping[str, Any]) -> list[str]:
    gaps = set(feature.get("gaps") or [])
    categories: list[str] = []
    if "model_price_basis_binding_missing" in gaps or "binance_spot_closed_1m_reference_missing" in gaps:
        categories.extend(["PRICE_BASIS_MISMATCH", "SOURCE_MISSING"])
    if "binance_um_closed_1m_window_missing" in gaps:
        categories.extend(["SOURCE_MISSING", "HISTORY_INSUFFICIENT"])
    if not categories:
        categories.append("SOURCE_MISSING")
    return sorted(set(categories))


def _available_category(name: str) -> str:
    if name in {"dte_hours", "side_sign"}:
        return "DERIVABLE_EXACT" if name == "dte_hours" else "SOURCE_AVAILABLE_EXACT"
    return "SOURCE_AVAILABLE_EXACT"


def _validate_feature_clocks_and_values(packet: Mapping[str, Any], model_input: Mapping[str, Any], catalog: Mapping[str, Any]) -> None:
    cutoff = packet["cutoff"]["decision_asof_ms"]
    row = model_input.get("row") or {}
    required = set(_required_feature_names(catalog))
    for feature in model_input.get("features") or []:
        if not isinstance(feature, Mapping):
            raise ValueError("wpem_reference_feature_shape_invalid")
        name = str(feature.get("name"))
        if row.get(name) != feature.get("value"):
            raise ValueError("wpem_reference_feature_row_value_mismatch:" + name)
        for clock_name in ("decision_asof_ms", "source_observation_ms", "first_available_ms", "retrieved_ms"):
            clock = feature.get(clock_name)
            if clock is not None and packets._clock(clock) > cutoff:
                raise ValueError("wpem_reference_future_feature_clock:" + name + ":" + clock_name)
        if name in required and feature.get("value") is not None and feature.get("category") in {
            "semantic_mismatch", "source_not_connected", "definition_not_supported"
        }:
            raise ValueError("wpem_reference_feature_substitution_rejected:" + name)
        if name in required and feature.get("value") is not None and feature.get("gaps"):
            raise ValueError("wpem_reference_feature_gap_substitution_rejected:" + name)


def _feature_binding_manifest(packet: Mapping[str, Any], catalog: Mapping[str, Any], model_input: Mapping[str, Any]) -> dict[str, Any]:
    _validate_feature_clocks_and_values(packet, model_input, catalog)
    by_name = _feature_by_name(model_input)
    required = _required_feature_names(catalog)
    required_missing = list(model_input.get("required_missing_features") or [])
    rows = []
    for name in required:
        feature = by_name[name]
        value = feature.get("value")
        missing = name in required_missing or value is None
        classification = _classify_missing(feature) if missing else [_available_category(name)]
        rows.append({
            "name": name,
            "required": True,
            "value": value,
            "accepted_for_model_row": value is not None and not missing,
            "classification": classification,
            "category": feature.get("category"),
            "gaps": list(feature.get("gaps") or []),
            "source_observation_ms": feature.get("source_observation_ms"),
            "first_available_ms": feature.get("first_available_ms"),
            "retrieved_ms": feature.get("retrieved_ms"),
            "missing_clocks_are_not_fabricated": feature.get("first_available_ms") is None,
            "training_support": dict(_feature_support(catalog, name)),
            "substitution_allowed": False,
        })
    coverage = len([row for row in rows if row["accepted_for_model_row"]])
    body = {
        "schema": FEATURE_BINDING_SCHEMA,
        "shadow_only": True,
        "status": "REQUIRED_FEATURES_MISSING" if required_missing else "FEATURES_BOUND",
        "terminal_status": "NOT_QUALIFIED_FINAL_FOR_THIS_ROUND" if required_missing else "FEATURES_ONLY_NOT_TARGET_BOUND",
        "packet_hash": packet["packet_hash"],
        "catalog_hash": catalog["catalog_hash"],
        "input_row_hash": model_input.get("input_row_hash"),
        "candidate_id": packet["snapshot"]["candidate_id"],
        "snapshot_id": packet["snapshot"]["snapshot_id"],
        "decision_asof_ms": packet["cutoff"]["decision_asof_ms"],
        "feature_count": len(catalog.get("features") or []),
        "required_feature_count": len(required),
        "required_feature_accepted_count": coverage,
        "required_missing_features": required_missing,
        "required_features": rows,
        "row_qualified": False,
        "inference_allowed": False,
        "inference_calls": 0,
        "llm_http_calls": 0,
    }
    return native.seal(body, "feature_binding_manifest_hash")


def _target_source_refs(asset_sha: str, protocol_sha: str) -> list[dict[str, Any]]:
    return [
        _source_ref("astra_joint_v11_data.py", "_candidate_row", asset_sha256=asset_sha, protocol_sha256=protocol_sha),
        _source_ref("astra_joint_v11_model.py", "target_loss", asset_sha256=asset_sha, protocol_sha256=protocol_sha),
        _source_ref("astra_joint_v11_model.py", "fast_predictions", asset_sha256=asset_sha, protocol_sha256=protocol_sha),
        _source_ref("astra_joint_v11_inference.py", "width_value_btc", asset_sha256=asset_sha, protocol_sha256=protocol_sha),
        _source_ref("astra_joint_dataset.py", "payout", asset_sha256=asset_sha, protocol_sha256=protocol_sha),
        _source_ref("astra_commercial_math_v17.py", "inverse_spread_payout", asset_sha256=asset_sha, protocol_sha256=protocol_sha),
    ]


def _support_domain(catalog: Mapping[str, Any]) -> dict[str, Any]:
    support = {str(item.get("name")): item.get("training_support") or {} for item in catalog.get("features") or []}
    names = ("dte_hours", "short_distance_fraction", "width_fraction", "side_sign")
    return {
        "horizon_hours": {
            "min_exclusive": (catalog.get("feature_group_protocol") or {}).get("ordinary_dte_min_exclusive_hours"),
            "max_inclusive": (catalog.get("feature_group_protocol") or {}).get("ordinary_dte_max_inclusive_hours"),
        },
        "primary_width_usd": (catalog.get("feature_group_protocol") or {}).get("primary_width"),
        "geometry_training_ranges": {name: {key: support.get(name, {}).get(key) for key in ("min", "max", "mean")}
                                     for name in names},
        "required_feature_support": {name: {key: support.get(name, {}).get(key) for key in ("row_count", "missing_count", "missing_rate", "min", "max")}
                                     for name in _required_feature_names(catalog)},
    }


def _target_price_basis_binding(packet: Mapping[str, Any], catalog: Mapping[str, Any]) -> dict[str, Any]:
    model_path = (catalog.get("asset") or {}).get("path")
    protocol_path = (catalog.get("protocol") or {}).get("path")
    model = _read_json(model_path)
    protocol = _read_json(protocol_path)
    selected = model["models"][model["selected_feature_group"]]
    tail_evidence = _tail_policy_evidence(model_path)
    asset_sha = (catalog.get("asset") or {}).get("sha256")
    protocol_sha = (catalog.get("protocol") or {}).get("sha256")
    body = {
        "schema": TARGET_BINDING_SCHEMA,
        "shadow_only": True,
        "status": "TARGET_BINDING_UNPROVEN",
        "terminal_status": "NOT_QUALIFIED_FINAL_FOR_THIS_ROUND",
        "packet_hash": packet["packet_hash"],
        "catalog_hash": catalog["catalog_hash"],
        "asset_sha256": asset_sha,
        "protocol_sha256": protocol_sha,
        "model_id": selected.get("model_id"),
        "model_hash": selected.get("model_hash"),
        "model_target": protocol.get("model_targets"),
        "actual_target_symbols": ["_candidate_row", "target_loss", "fast_predictions", "width_value_btc", "payout"],
        "model_reference_price_basis": protocol.get("reference_price"),
        "spot_reference_market": protocol.get("spot_reference_market"),
        "feature_market": protocol.get("feature_market"),
        "candidate_price_basis": (packet.get("binding") or {}).get("candidate_price_basis"),
        "candidate_binding_status": (packet.get("binding") or {}).get("status"),
        "binding_reason": "Binance_model_target_to_Deribit_native_payout_not_validated",
        "quantity_scale": {
            "training_payout_quantity": "q=1 implicit in astra_joint_dataset.payout(side, short, long, settlement)",
            "normalized_scale_B": "actual_width / entry_price",
            "current_native_quantity_btc": packet["snapshot"]["contract"].get("quantity_btc"),
            "current_native_formula_uses_quantity": True,
            "quantity_mapping_status": "UNPROVEN_FOR_CURRENT_DERIBIT_NATIVE_CONTRACT",
        },
        "loss_over_B": {
            "clipped_to_one": False,
            "source": "actual source refs define payout divided by actual_width / entry_price and contain no cap-to-one branch",
        },
        "tail_publication_limit": {
            "policy": (model.get("publication_policy") or {}).get("tail_probability"),
            "evidence": tail_evidence,
            "meaning": "independent tail probability publication restriction; not the first B-round qualification break",
        },
        "support_domain": _support_domain(catalog),
        "binding_checks": [
            {"check": "10_required_features_strict_asof", "status": "FAIL"},
            {"check": "asset_protocol_hash", "status": "PASS"},
            {"check": "horizon_side_geometry_price_basis_target_binding", "status": "FAIL"},
            {"check": "provider_llm_account_free", "status": "PASS"},
            {"check": "shadow_reference_label", "status": "NOT_REACHED"},
        ],
        "mu_ref_btc": None,
        "M_ref_btc": None,
        "m_ref_btc": None,
        "inference_allowed": False,
        "inference_calls": 0,
        "llm_http_calls": 0,
        "source_refs": _target_source_refs(asset_sha, protocol_sha),
    }
    return native.seal(body, "target_price_basis_binding_hash")

def _qualification_report(
    packet: Mapping[str, Any],
    catalog: Mapping[str, Any],
    model_input: Mapping[str, Any],
    feature_manifest: Mapping[str, Any],
    target_binding: Mapping[str, Any],
) -> dict[str, Any]:
    required_missing = list(model_input.get("required_missing_features") or [])
    required_total = len(_required_feature_names(catalog))
    required_available = required_total - len(required_missing)
    body = {
        "schema": QUALIFICATION_SCHEMA,
        "shadow_only": True,
        "terminal_status": "NOT_QUALIFIED_FINAL_FOR_THIS_ROUND",
        "earliest_breakpoint": "REQUIRED_FEATURES_ASOF_NOT_QUALIFIED" if required_missing else "TARGET_BINDING_UNPROVEN",
        "status": "NOT_QUALIFIED_FINAL_FOR_THIS_ROUND",
        "packet_hash": packet["packet_hash"],
        "catalog_hash": catalog["catalog_hash"],
        "input_row_hash": model_input.get("input_row_hash"),
        "asset_sha256": (catalog.get("asset") or {}).get("sha256"),
        "protocol_sha256": (catalog.get("protocol") or {}).get("sha256"),
        "candidate_id": packet["snapshot"]["candidate_id"],
        "snapshot_id": packet["snapshot"]["snapshot_id"],
        "decision_asof_ms": packet["cutoff"]["decision_asof_ms"],
        "model_id": (catalog.get("model") or {}).get("model_id"),
        "model_hash": (catalog.get("model") or {}).get("model_hash"),
        "feature_count": len(catalog.get("features") or []),
        "required_feature_count": required_total,
        "required_missing_count": len(required_missing),
        "required_missing_features": required_missing,
        "required_coverage": {
            "available": required_available,
            "total": required_total,
            "missing": len(required_missing),
        },
        "feature_binding_manifest_hash": feature_manifest.get("feature_binding_manifest_hash"),
        "target_price_basis_binding_hash": target_binding.get("target_price_basis_binding_hash"),
        "gap_reasons": sorted(set((model_input.get("gap_reasons") or []) + [
            "TARGET_BINDING_UNPROVEN",
            "SHADOW_INFERENCE_NOT_ALLOWED",
        ])),
        "shadow_inference_allowed": False,
        "mu_ref_btc": None,
        "M_ref_btc": None,
        "m_ref_btc": None,
        "row_qualified": False,
        "production_risk_admitted": False,
        "execution_allowed": False,
        "inference_calls": 0,
        "llm_http_calls": 0,
    }
    return native.seal(body, "qualification_report_hash")


def analyze_reference(packet: Mapping[str, Any], catalog: Mapping[str, Any]) -> dict[str, Any]:
    """Return a terminal, shadow-only B qualification diagnosis.

    The returned dictionaries are sealed and suitable for parent-side evidence
    materialization.  A failure here never grants inference permission.
    """

    catalog = _require_catalog_identity(catalog)
    model_input = _require_packet_and_input(packet, catalog)
    feature_manifest = _feature_binding_manifest(packet, catalog, model_input)
    target_binding = _target_price_basis_binding(packet, catalog)
    qualification = _qualification_report(packet, catalog, model_input, feature_manifest, target_binding)
    body = {
        "schema": RESULT_SCHEMA,
        "shadow_only": True,
        "terminal_status": qualification["terminal_status"],
        "qualification_report": qualification,
        "feature_binding_manifest": feature_manifest,
        "target_price_basis_binding": target_binding,
        "inference_calls": 0,
        "llm_http_calls": 0,
        "mu_ref_btc": None,
        "M_ref_btc": None,
        "m_ref_btc": None,
    }
    return native.seal(body, "reference_analysis_hash")
