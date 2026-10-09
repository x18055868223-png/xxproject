"""Sparse candidate feature binding for the sealed Astra v1.1 research model.

This module exports a read-only catalog from the admitted research asset and
builds a partial model-input row from an already frozen underwriting snapshot.
It never imports the inference code, never scores the row, and never grants a
risk reference.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

if __package__:
    from . import astra_joint_v11_data as v11_data
    from . import astra_risk_qualification_v244 as risk_qualification
    from . import astra_underwriting_v20 as underwriting
    import astra_joint_data as joint_data
else:
    import astra_joint_data as joint_data
    import astra_joint_v11_data as v11_data
    import astra_risk_qualification_v244 as risk_qualification
    import astra_underwriting_v20 as underwriting

CATALOG_SCHEMA = "astra_candidate_feature_catalog@1.0.0"
FEATURE_INPUT_SCHEMA = "astra_candidate_feature_input@1.0.0"
SCRIPT_DIR = Path(__file__).resolve().parent


def _read_json_asset(path: str | Path) -> tuple[dict[str, Any], str]:
    raw = Path(path).read_bytes()
    if len(raw) > risk_qualification.MAX_ASSET_BYTES:
        raise ValueError("feature_asset_too_large")
    value = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("feature_asset_not_object")
    return value, hashlib.sha256(raw).hexdigest()


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _find_symbol(tree: ast.AST, dotted_name: str) -> ast.AST | None:
    parts = dotted_name.split(".")
    body = getattr(tree, "body", [])
    found: ast.AST | None = None
    for part in parts:
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


def _source_ref(
    relative: str,
    symbol: str | None = None,
    source_cache: dict[str, tuple[str, str]] | None = None,
) -> dict[str, Any]:
    path = SCRIPT_DIR / relative
    display_path = f"tools/{relative}"
    if source_cache is not None and display_path in source_cache:
        source, file_sha = source_cache[display_path]
    else:
        raw = path.read_bytes()
        source = raw.decode("utf-8")
        file_sha = hashlib.sha256(raw).hexdigest()
        if source_cache is not None:
            source_cache[display_path] = (source, file_sha)
    ref: dict[str, Any] = {
        "file": display_path,
        "file_sha256": file_sha,
    }
    if symbol is None:
        return ref
    tree = ast.parse(source)
    node = _find_symbol(tree, symbol)
    if node is None:
        raise ValueError(f"source symbol not found: {relative}:{symbol}")
    ref.update(
        {
            "symbol": symbol,
            "start_line": getattr(node, "lineno", None),
            "end_line": getattr(node, "end_lineno", None),
            "ast_sha256": hashlib.sha256(
                ast.dump(node, annotate_fields=True, include_attributes=False).encode("utf-8")
            ).hexdigest(),
            "source_definition": ast.get_source_segment(source, node),
        }
    )
    return ref


def _source_refs() -> dict[str, list[dict[str, Any]]]:
    source_cache: dict[str, tuple[str, str]] = {}
    refs = {
        "contract_time": [
            _source_ref("astra_underwriting_v20.py", "evaluate", source_cache),
            _source_ref("astra_risk_qualification_v244.py", "diagnose", source_cache),
        ],
        "contract_side": [
            _source_ref("astra_joint_v11_data.py", "_side_sign", source_cache),
            _source_ref("astra_joint_v11_data.py", "side_features", source_cache),
        ],
        "price_geometry": [
            _source_ref("astra_joint_v11_data.py", "side_features", source_cache),
            _source_ref("astra_joint_v11_data.py", "reference_spot_close", source_cache),
        ],
        "um_closed_bar_window": [
            _source_ref("astra_joint_data.py", "HistoricalBars.closed_window", source_cache),
            _source_ref("astra_joint_data.py", "HistoricalBars.feature_snapshot", source_cache),
            _source_ref("astra_joint_data.py", "_summarize_rows", source_cache),
            _source_ref("astra_joint_data.py", "_net_flow_ratio", source_cache),
        ],
        "side_aligned_um_window": [
            _source_ref("astra_joint_v11_data.py", "side_features", source_cache),
            _source_ref("astra_joint_data.py", "HistoricalBars.feature_snapshot", source_cache),
        ],
        "utc_clock": [
            _source_ref("astra_joint_data.py", "_hour_fraction", source_cache),
            _source_ref("astra_joint_data.py", "_hour_sin", source_cache),
            _source_ref("astra_joint_data.py", "_hour_cos", source_cache),
        ],
        "protocol": [_source_ref("astra_joint_v11_contract.py", source_cache=source_cache)],
        "risk_admission": [_source_ref("astra_risk_qualification_v244.py", "load_profile", source_cache)],
    }
    seen: dict[str, str] = {}
    for values in refs.values():
        for ref in values:
            file_name = str(ref["file"])
            file_sha = str(ref["file_sha256"])
            if file_name in seen and seen[file_name] != file_sha:
                raise ValueError("source_reference_version_changed")
            seen[file_name] = file_sha
    return refs


def _source_hashes(refs: Mapping[str, list[Mapping[str, Any]]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for values in refs.values():
        for ref in values:
            out[str(ref["file"])] = str(ref["file_sha256"])
    return dict(sorted(out.items()))


def _window_minutes(name: str) -> int | None:
    suffix = name.rsplit("_", 1)[-1]
    return int(suffix) if suffix.isdigit() else None


def _feature_kind(name: str) -> str:
    if name == "dte_hours":
        return "contract_time"
    if name == "side_sign":
        return "contract_side"
    if name in {"short_distance_fraction", "width_fraction"}:
        return "price_geometry"
    if name in {"hour_sin", "hour_cos"}:
        return "utc_clock"
    if name.startswith("adverse_return_") or name.startswith("adverse_flow_"):
        return "side_aligned_um_window"
    if name.startswith("ret_") or name.startswith("vol_") or name.startswith("net_flow_"):
        return "um_closed_bar_window"
    return "unknown"


def _unit_for(name: str, kind: str) -> str:
    if name == "dte_hours":
        return "hours"
    if name == "side_sign":
        return "put=-1_call=1"
    if kind == "utc_clock":
        return "unit_circle"
    return "fraction"


def _split_missing_policy(model: Mapping[str, Any], feature_names: list[str]) -> dict[str, dict[str, Any]]:
    policies: dict[str, dict[str, Any]] = {name: {"split_count": 0, "missing_goes_right": {}} for name in feature_names}
    for key in ("catboost_classifier", "catboost_regressor", "catboost_tail"):
        section = model.get(key)
        if not isinstance(section, Mapping):
            continue
        for tree in section.get("oblivious_trees") or []:
            if not isinstance(tree, Mapping):
                continue
            for split in tree.get("splits") or []:
                if not isinstance(split, Mapping):
                    continue
                raw_name = split.get("feature_name")
                if raw_name is None:
                    index = split.get("feature_index")
                    if isinstance(index, int) and 0 <= index < len(feature_names):
                        raw_name = feature_names[index]
                name = str(raw_name) if raw_name is not None else ""
                if name not in policies:
                    continue
                policy = policies[name]
                policy["split_count"] += 1
                flag = str(bool(split.get("missing_goes_right", False))).lower()
                policy["missing_goes_right"][flag] = int(policy["missing_goes_right"].get(flag, 0)) + 1
                treatment = split.get("nan_value_treatment")
                if treatment:
                    treatments = policy.setdefault("nan_value_treatments", {})
                    treatments[str(treatment)] = int(treatments.get(str(treatment), 0)) + 1
    return policies


def _missing_policy_summary(policies: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    split_count = 0
    for policy in policies.values():
        split_count += int(policy.get("split_count") or 0)
        for key, value in (policy.get("missing_goes_right") or {}).items():
            counts[str(key)] = counts.get(str(key), 0) + int(value)
    return {"split_count": split_count, "missing_goes_right": dict(sorted(counts.items()))}


def _feature_specs(
    artifact: Mapping[str, Any],
    protocol: Mapping[str, Any],
    profile: Mapping[str, Any],
    refs: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    group = str(artifact["selected_feature_group"])
    selected = artifact["models"][group]
    feature_names = [str(name) for name in selected["feature_names"]]
    required = {str(name) for name in selected["required_feature_names"]}
    support = {
        str(item.get("name")): dict(item)
        for item in ((selected.get("feature_support") or {}).get("features") or [])
        if isinstance(item, Mapping) and item.get("name") is not None
    }
    split_policy = _split_missing_policy(selected, feature_names)
    specs: list[dict[str, Any]] = []
    for name in feature_names:
        kind = _feature_kind(name)
        minutes = _window_minutes(name)
        window = None
        if kind in {"um_closed_bar_window", "side_aligned_um_window"}:
            window = {
                "minutes": minutes,
                "market": protocol.get("feature_market"),
                "closed_bar_rule": "HistoricalBars.closed_window",
            }
        elif kind == "price_geometry":
            window = {
                "reference_price": protocol.get("reference_price"),
                "spot_reference_market": protocol.get("spot_reference_market"),
            }
        elif kind == "contract_time":
            window = {
                "min_exclusive_hours": protocol.get("ordinary_dte_min_exclusive_hours"),
                "max_inclusive_hours": protocol.get("ordinary_dte_max_inclusive_hours"),
            }
        spec = {
            "name": name,
            "required": name in required,
            "feature_kind": kind,
            "definition": {
                "kind": kind,
                "model_feature_group": profile.get("feature_group"),
                "protocol_schema": protocol.get("schema"),
                "source_symbols_are_authoritative": True,
                "source_definitions": refs.get(kind) or refs.get("protocol") or [],
            },
            "window": window,
            "unit": _unit_for(name, kind),
            "source_refs": refs.get(kind) or refs.get("protocol") or [],
            "training_support": support.get(name),
            "missing_policy": {
                "row_required": name in required,
                "row_qualified_if_missing": name not in required,
                "catboost_missing_route": split_policy.get(name, {}),
                "training_missing_rate": (support.get(name) or {}).get("missing_rate"),
            },
        }
        specs.append(spec)
    return specs


def export_feature_catalog(model_path: str | Path, protocol_path: str | Path) -> dict[str, Any]:
    """Return the admitted model's feature catalog without scoring anything."""
    profile = risk_qualification.load_profile(model_path, protocol_path)
    artifact, artifact_sha = _read_json_asset(model_path)
    protocol, protocol_sha = _read_json_asset(protocol_path)
    if artifact_sha != profile.get("artifact_sha256") or protocol_sha != profile.get("protocol_sha256"):
        raise ValueError("risk_asset_changed_after_admission")
    group = str(artifact["selected_feature_group"])
    selected = artifact["models"][group]
    refs = _source_refs()
    split_policy = _split_missing_policy(selected, [str(name) for name in selected["feature_names"]])
    catalog = {
        "schema": CATALOG_SCHEMA,
        "asset": {
            "path": str(Path(model_path)),
            "sha256": artifact_sha,
            "schema": artifact.get("schema"),
            "source_artifact_hash": artifact.get("source_artifact_hash"),
        },
        "protocol": {
            "path": str(Path(protocol_path)),
            "sha256": protocol_sha,
            "schema": protocol.get("schema"),
        },
        "source_hashes": _source_hashes(refs),
        "source_refs": refs,
        "catboost_missing_policy": _missing_policy_summary(split_policy),
        "model": {
            "selected_feature_group": group,
            "model_id": selected.get("model_id"),
            "model_hash": selected.get("model_hash"),
            "model_family": selected.get("model_family"),
            "training_cutoff": artifact.get("training_cutoff"),
            "scope": artifact.get("scope"),
            "publication_policy": artifact.get("publication_policy"),
        },
        "price_identity": {
            "model_reference_price_basis": profile.get("reference_price_basis"),
            "spot_reference_market": protocol.get("spot_reference_market"),
            "candidate_snapshot_reference_is_not_a_substitute": True,
        },
        "feature_group_protocol": {
            "feature_market": protocol.get("feature_market"),
            "feature_windows_minutes": protocol.get("feature_windows_minutes"),
            "natural_training_observation": protocol.get("natural_training_observation"),
            "ordinary_dte_min_exclusive_hours": protocol.get("ordinary_dte_min_exclusive_hours"),
            "ordinary_dte_max_inclusive_hours": protocol.get("ordinary_dte_max_inclusive_hours"),
            "primary_width": protocol.get("primary_width"),
            "widths": protocol.get("widths"),
            "feature_group_names": (protocol.get("feature_groups") or {}).get(group),
        },
        "features": _feature_specs(artifact, protocol, profile, refs),
        "inference_calls": 0,
        "llm_http_calls": 0,
        "production_risk_admitted": False,
    }
    catalog["catalog_hash"] = _stable_hash(catalog)
    return catalog


def _feature_value(name: str, snapshot: Mapping[str, Any]) -> tuple[Any, str, list[str]]:
    contract = snapshot["contract"]
    market = snapshot["market"]
    if name == "dte_hours":
        value = (int(contract["expiry_ms"]) - int(market["valuation_ts_ms"])) / 3_600_000
        return value, "available_from_snapshot", []
    if name == "side_sign":
        return v11_data._side_sign(str(contract["side"])), "available_from_snapshot", []
    if name == "hour_sin":
        return joint_data._hour_sin(int(market["valuation_ts_ms"])), "available_from_snapshot", []
    if name == "hour_cos":
        return joint_data._hour_cos(int(market["valuation_ts_ms"])), "available_from_snapshot", []
    kind = _feature_kind(name)
    if kind == "price_geometry":
        return None, "semantic_mismatch", [
            "model_price_basis_binding_missing",
            "binance_spot_closed_1m_reference_missing",
        ]
    if kind in {"um_closed_bar_window", "side_aligned_um_window"}:
        return None, "source_not_connected", ["binance_um_closed_1m_window_missing"]
    return None, "definition_not_supported", ["feature_definition_not_supported"]


def _source_clock_binding(snapshot: Mapping[str, Any], category: str) -> dict[str, Any]:
    market = snapshot["market"]
    decision_asof = market.get("valuation_ts_ms")
    contract = snapshot["contract"]
    base = {
        "decision_asof_ms": decision_asof,
        "source_observation_ms": None,
        "first_available_ms": None,
        "retrieved_ms": None,
        "source_snapshot_id": snapshot.get("snapshot_id"),
        "source_contract": {
            key: contract.get(key)
            for key in (
                "candidate_id",
                "side",
                "short_strike_usd",
                "long_strike_usd",
                "quantity_btc",
                "expiry_ms",
                "width_usd",
            )
        },
    }
    if category == "available_from_snapshot":
        base["source_observation_ms"] = decision_asof
        base["retrieved_ms"] = (market.get("quote") or {}).get("received_ts_ms")
    return base


def _compact_source_ref(ref: Mapping[str, Any], definition_sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out = {key: value for key, value in ref.items() if key != "source_definition"}
    source_definition = ref.get("source_definition")
    if source_definition is None:
        return out
    definition_sha = hashlib.sha256(str(source_definition).encode("utf-8")).hexdigest()
    ref_id = f"{ref.get('ast_sha256')}:{definition_sha}"
    if ref_id not in definition_sources:
        definition_sources[ref_id] = {
            "file": ref.get("file"),
            "symbol": ref.get("symbol"),
            "file_sha256": ref.get("file_sha256"),
            "start_line": ref.get("start_line"),
            "end_line": ref.get("end_line"),
            "ast_sha256": ref.get("ast_sha256"),
            "source_definition_sha256": definition_sha,
            "source_definition": source_definition,
        }
    out["definition_source_ref"] = ref_id
    out["source_definition_sha256"] = definition_sha
    return out


def _compact_refs(
    refs: Any,
    definition_sources: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(refs, list):
        return []
    return [
        _compact_source_ref(ref, definition_sources)
        for ref in refs
        if isinstance(ref, Mapping)
    ]


def _compact_definition(
    definition: Any,
    definition_sources: dict[str, dict[str, Any]],
) -> Any:
    if not isinstance(definition, Mapping):
        return definition
    out = dict(definition)
    out["source_definitions"] = _compact_refs(out.get("source_definitions"), definition_sources)
    return out


def build_feature_input(snapshot: Mapping[str, Any], catalog: Mapping[str, Any]) -> dict[str, Any]:
    """Build a sparse feature row from a frozen snapshot; never infer missing data."""
    underwriting.llm_applicability_packet(snapshot)
    catalog_hash = catalog.get("catalog_hash")
    if catalog_hash != _stable_hash({k: v for k, v in catalog.items() if k != "catalog_hash"}):
        raise ValueError("feature_catalog_hash_mismatch")
    row: dict[str, Any] = {}
    features: list[dict[str, Any]] = []
    definition_sources: dict[str, dict[str, Any]] = {}
    required_missing: list[str] = []
    gap_reasons: set[str] = {
        "model_scope_research_only",
        "v20_contract_feature_binding_not_validated",
    }
    for spec in catalog.get("features") or []:
        if not isinstance(spec, Mapping) or spec.get("name") is None:
            raise ValueError("feature_catalog_entry_invalid")
        name = str(spec["name"])
        value, category, gaps = _feature_value(name, snapshot)
        row[name] = value
        if gaps:
            gap_reasons.update(gaps)
        if spec.get("required") and value is None:
            required_missing.append(name)
        source_refs = _compact_refs(spec.get("source_refs") or [], definition_sources)
        features.append(
            {
                "name": name,
                "required": bool(spec.get("required")),
                "definition": _compact_definition(spec.get("definition"), definition_sources),
                "window": spec.get("window"),
                "unit": spec.get("unit"),
                "value": value,
                "category": category,
                **_source_clock_binding(snapshot, category),
                "source_refs": source_refs,
                "missing_policy": spec.get("missing_policy"),
                "gaps": list(gaps),
            }
        )
    if required_missing:
        gap_reasons.add("required_features_missing")
    asset_sha = (catalog.get("asset") or {}).get("sha256")
    protocol_sha = (catalog.get("protocol") or {}).get("sha256")
    source_hashes = catalog.get("source_hashes")
    decision_asof = snapshot["market"].get("valuation_ts_ms")
    input_identity = {
        "row": row,
        "snapshot_id": snapshot["snapshot_id"],
        "candidate_id": snapshot["candidate_id"],
        "decision_asof_ms": decision_asof,
        "catalog_hash": catalog_hash,
        "asset_sha256": asset_sha,
        "protocol_sha256": protocol_sha,
        "source_hashes": source_hashes,
    }
    return {
        "schema": FEATURE_INPUT_SCHEMA,
        "status": "not_qualified",
        "snapshot_id": snapshot["snapshot_id"],
        "candidate_id": snapshot["candidate_id"],
        "catalog_hash": catalog_hash,
        "asset_sha256": asset_sha,
        "protocol_sha256": protocol_sha,
        "source_hashes": source_hashes,
        "price_identity": catalog.get("price_identity"),
        "feature_group_protocol": catalog.get("feature_group_protocol"),
        "definition_sources": definition_sources,
        "model_id": (catalog.get("model") or {}).get("model_id"),
        "model_hash": (catalog.get("model") or {}).get("model_hash"),
        "feature_group": (catalog.get("model") or {}).get("selected_feature_group"),
        "features": features,
        "row": row,
        "required_missing_features": required_missing,
        "input_row_hash": _stable_hash(input_identity),
        "input_row_hash_scope": "research_input_identity_only; does_not_grant_row_or_risk_qualification",
        "row_qualified": False,
        "gap_reasons": sorted(gap_reasons),
        "inference_calls": 0,
        "llm_http_calls": 0,
        "production_risk_admitted": False,
        "mu_ref_btc": None,
    }
