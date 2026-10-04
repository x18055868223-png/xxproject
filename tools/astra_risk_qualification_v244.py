"""Read-only admission diagnosis for the existing frozen research model.

This is deliberately not a risk provider: the current research artifact has
no accepted v2.0 contract/feature binding. No inference, HTTP or state writes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

if __package__:
    from . import astra_underwriting_v20 as underwriting
else:
    import astra_underwriting_v20 as underwriting

SCHEMA = "astra_risk_consumption_diagnosis@2.4.4"
MODEL_SHA256 = "71fb23ff9cbb55a789cc1c95862e7e242d8647ce8ac883047d10bded01fd5860"
PROTOCOL_SHA256 = "5f007cb512763a17bf475da38bee642c171ab7502e816c74a40091cde7ec828e"
MAX_ASSET_BYTES = 4 * 1024 * 1024


def _read_asset(path: str | Path) -> tuple[dict, str]:
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_ASSET_BYTES + 1)
    if len(raw) > MAX_ASSET_BYTES:
        raise ValueError("risk_asset_too_large")
    value = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("risk_asset_not_object")
    return value, hashlib.sha256(raw).hexdigest()


def load_profile(model_path: str | Path, protocol_path: str | Path) -> dict[str, Any]:
    """Recognize exact reviewed bytes; never trust a file's own qualified flag."""
    artifact, model_hash = _read_asset(model_path)
    protocol, protocol_hash = _read_asset(protocol_path)
    if model_hash != MODEL_SHA256 or protocol_hash != PROTOCOL_SHA256:
        raise ValueError("risk_asset_or_protocol_hash_not_admitted")
    if artifact.get("schema") != "astra_joint_v11_model_artifact@1.0.0":
        raise ValueError("risk_model_schema_not_supported")
    group = artifact["selected_feature_group"]
    selected = artifact["models"][group]
    return {
        "schema": SCHEMA,
        "artifact_status": "verified_research_asset",
        "artifact_sha256": model_hash,
        "protocol_sha256": protocol_hash,
        "model_id": selected["model_id"],
        "model_hash": selected["model_hash"],
        "feature_group": group,
        "feature_names": list(selected["feature_names"]),
        "required_feature_names": list(selected["required_feature_names"]),
        "training_cutoff": artifact["training_cutoff"],
        "scope": artifact["scope"],
        "tail_probability": artifact["publication_policy"]["tail_probability"],
        "horizon_min_exclusive_hours": protocol["ordinary_dte_min_exclusive_hours"],
        "horizon_max_inclusive_hours": protocol["ordinary_dte_max_inclusive_hours"],
        "primary_width_usd": protocol["primary_width"],
        "reference_price_basis": protocol["reference_price"],
        "training_observation": protocol["natural_training_observation"],
        "production_risk_admitted": False,
        "admission_reason": "v20_contract_feature_binding_not_validated",
    }


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(float(value)) else None


def diagnose(snapshot: Mapping[str, Any], profile: Mapping[str, Any] | None) -> dict[str, Any]:
    """Derive a diagnosis only; never manufacture a supported risk record.

    The frozen underwriting snapshot intentionally has no verified v1.1
    rolling-feature input row. Listing that gap is safer than substituting
    summary scores or silently accepting the model's missing-value defaults.
    """
    packet = underwriting.llm_applicability_packet(snapshot)
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "snapshot_id": packet["snapshot_id"],
        "candidate_id": packet["candidate_id"],
        "status": "not_qualified",
        "production_risk_admitted": False,
        "mu_ref_btc": None,
        "inference_calls": 0,
        "llm_http_calls": 0,
        "gap_reasons": [],
    }
    if profile is None:
        result["gap_reasons"] = ["risk_model_not_configured"]
        return result
    # Profiles are internal, but checking identity here prevents accidental use
    # of a synthetic dict as an admitted model in a later integration.
    if (profile.get("schema") != SCHEMA or profile.get("artifact_sha256") != MODEL_SHA256
            or profile.get("protocol_sha256") != PROTOCOL_SHA256):
        raise ValueError("risk_profile_identity_invalid")
    contract = snapshot["contract"]
    valuation = _number(snapshot["market"].get("valuation_ts_ms"))
    expiry = _number(contract.get("expiry_ms"))
    hours = (expiry - valuation) / 3_600_000 if expiry is not None and valuation is not None else None
    lower = profile["horizon_min_exclusive_hours"]
    upper = profile["horizon_max_inclusive_hours"]
    horizon_supported = hours is not None and lower < hours <= upper
    reasons = ["model_scope_research_only", "v20_contract_feature_binding_not_validated",
               "model_input_row_missing", "model_price_basis_binding_missing"]
    if not horizon_supported:
        reasons.append("risk_horizon_outside_research_domain" if hours is not None else "risk_horizon_unknown")
    facts = snapshot.get("fact_context") or {}
    if facts.get("status") != "current":
        reasons.append("risk_current_fmz_fact_missing")
    if (snapshot.get("data_identity") or {}).get("kind") == "synthetic_fixture":
        reasons.append("synthetic_candidate_not_production_evidence")
    result.update({
        "artifact": {key: profile[key] for key in (
            "artifact_status", "artifact_sha256", "protocol_sha256", "model_id", "model_hash",
            "feature_group", "training_cutoff", "scope", "tail_probability")},
        "contract_domain": {
            "side": contract["side"],
            "expiry_ms": contract["expiry_ms"],
            "valuation_ts_ms": snapshot["market"]["valuation_ts_ms"],
            "quantity_btc": contract["quantity_btc"],
            "width_usd": contract["width_usd"],
            "primary_research_width_usd": profile.get("primary_width_usd"),
            "source_fact_hash": facts.get("source_hash"),
            "remaining_hours": hours,
            "research_horizon_min_exclusive_hours": lower,
            "research_horizon_max_inclusive_hours": upper,
            "horizon_in_research_range": horizon_supported,
            "candidate_reference_source": snapshot["market"].get("reference_source"),
            "model_reference_price_basis": profile["reference_price_basis"],
            "meaning": "期限落在研究区间不等于同合同消费资格通过",
        },
        "feature_input": {
            "status": "missing_verified_row",
            "feature_names": list(profile["feature_names"]),
            "required_feature_names": list(profile["required_feature_names"]),
            "substitution_allowed": False,
        },
        "gap_reasons": sorted(set(reasons)),
    })
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    args = parser.parse_args(argv)
    snapshot, _ = _read_asset(args.snapshot)
    profile = load_profile(args.model, args.protocol)
    print(json.dumps(diagnose(snapshot, profile), ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
