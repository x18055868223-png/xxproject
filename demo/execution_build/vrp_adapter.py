# -*- coding: utf-8 -*-
"""VRP snapshot adapter for demo v0.3.

The adapter reads the in-project VRP delivery snapshot and wraps its pure
functions into the execution-layer contract package. It keeps VRP as a filter:
no direction choice, no order permission, no ranking weight.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[2]
VRP_SRC = ROOT / "03_VRP门_建仓前定价" / "交付物快照" / "src"


def _load_vrp_modules():
    if str(VRP_SRC) not in sys.path:
        sys.path.insert(0, str(VRP_SRC))
    model_path = VRP_SRC / "vrp_model.py"
    policy_path = VRP_SRC / "vrp_policy.py"

    model_spec = importlib.util.spec_from_file_location("demo_vrp_model_snapshot", model_path)
    if model_spec is None or model_spec.loader is None:
        raise ImportError("Cannot load VRP model snapshot")
    model = importlib.util.module_from_spec(model_spec)
    sys.modules[model_spec.name] = model
    model_spec.loader.exec_module(model)

    # vrp_policy imports "vrp_model", so make the loaded snapshot available under
    # that canonical module name for this adapter call.
    sys.modules["vrp_model"] = model
    policy_spec = importlib.util.spec_from_file_location("demo_vrp_policy_snapshot", policy_path)
    if policy_spec is None or policy_spec.loader is None:
        raise ImportError("Cannot load VRP policy snapshot")
    policy = importlib.util.module_from_spec(policy_spec)
    sys.modules[policy_spec.name] = policy
    policy_spec.loader.exec_module(policy)
    return model, policy


def _side_from_signal(signal_package: Dict[str, Any]) -> str:
    lean = signal_package["direction_evidence"]["edb"].get("lean")
    if lean in ("SHORT_CALL", "SHORT_PUT"):
        return lean
    side_hint = signal_package["strategy_recommendation"].get("side_hint")
    if side_hint == "CALL":
        return "SHORT_CALL"
    if side_hint == "PUT":
        return "SHORT_PUT"
    raise ValueError("Signal package does not provide a supported VRP side")


def evaluate_demo_vrp_gate(
    signal_package: Dict[str, Any],
    market_context: Dict[str, Any],
    candidate_quote: Dict[str, Any],
) -> Dict[str, Any]:
    model, policy = _load_vrp_modules()
    config = policy.selected_policy_config()

    side = _side_from_signal(signal_package)
    expiry_hours = float(signal_package["strategy_recommendation"]["expiry_hours"])
    window_id = "%s-%sh" % (side, int(expiry_hours))
    window = model.WindowInput(
        window_id=window_id,
        expiry="%sh" % int(expiry_hours),
        dte_hours=expiry_hours,
        side=side,
        front_anchor_iv=market_context["front_anchor_iv"],
        atm_front_iv=market_context.get("atm_front_iv"),
        term_reference_iv_5_10d=market_context.get("term_reference_iv_5_10d"),
        rv_24h=market_context["rv_24h"],
        rv_72h=market_context["rv_72h"],
        rv_7d=market_context["rv_7d"],
        rv_percentile=market_context.get("rv_percentile"),
        history_days=int(market_context.get("history_days", 0)),
    )
    window_assessment = model.assess_window(window, config)

    candidate_payload = dict(candidate_quote)
    candidate_payload.setdefault("window_id", window_id)
    candidate_payload.setdefault("side", side)
    candidate_payload.setdefault("spot", market_context["spot"])
    candidate_payload.setdefault("dte_hours", expiry_hours)
    candidate_payload.setdefault("forward_vol_hurdle", window_assessment["forward_vol_hurdle"])
    candidate = model.CandidateQuote(**candidate_payload)
    candidate_assessment = model.assess_candidate(candidate, config)

    window_pass = window_assessment.get("window_vrp_gate") == model.PASS
    candidate_pass = candidate_assessment.get("candidate_vrp_gate") == model.PASS
    return {
        "schema_name": "VrpGatePackage",
        "schema_version": "nrd.integration.vrp_gate.v0.3",
        "factor_version": model.VRP_FACTOR_VERSION,
        "policy": policy.implementation_policy_package(),
        "window": window_assessment,
        "candidate": candidate_assessment,
        "pass": bool(window_pass and candidate_pass),
        "reason_codes": sorted(set(
            (window_assessment.get("reason_codes") or [])
            + (candidate_assessment.get("reason_codes") or [])
        )),
    }


def evaluate_demo_vrp_gate_from_quote_snapshot(
    signal_package: Dict[str, Any],
    quote_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    gate = evaluate_demo_vrp_gate(
        signal_package=signal_package,
        market_context=quote_snapshot["market_context"],
        candidate_quote=quote_snapshot["candidate_quote"],
    )
    gate["schema_version"] = "nrd.integration.vrp_gate.v0.13"
    gate["input_snapshot"] = {
        "schema_name": quote_snapshot.get("schema_name"),
        "schema_version": quote_snapshot.get("schema_version"),
        "snapshot_id": quote_snapshot.get("snapshot_id"),
        "source": quote_snapshot.get("source"),
        "signal_package_id": quote_snapshot.get("lineage", {}).get("signal_package_id"),
    }
    return gate
