# -*- coding: utf-8 -*-
"""KPF deletion policy helpers for demo v0.2.

These helpers do not mutate the source repositories. They encode the local
demo contract for Phase 1: KPF fields are removed, plan weights are renormalized,
and hedge persistence uses only EDB/GGR adverse evidence.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


def normalize_plan_weights_without_kpf(weights: Dict[str, float]) -> Dict[str, float]:
    kept = {
        key: float(value)
        for key, value in weights.items()
        if "kpf" not in key.lower()
    }
    total = sum(kept.values())
    if total <= 0:
        return {"win_rate": 0.375, "rr": 0.375, "signal": 0.25}
    return {key: value / total for key, value in kept.items()}


def strip_kpf_context(value: Any) -> Any:
    """Recursively remove keys whose names contain kpf."""
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            if "kpf" in str(key).lower():
                continue
            cleaned_value = strip_kpf_context(item)
            if cleaned_value == {}:
                continue
            cleaned[key] = cleaned_value
        return cleaned
    if isinstance(value, list):
        return [strip_kpf_context(item) for item in value]
    return deepcopy(value)


def persistence_level_from_adverse_flags(edb_adverse: bool, ggr_adverse: bool) -> Dict[str, Any]:
    score = int(bool(edb_adverse)) + int(bool(ggr_adverse))
    level = "LOW"
    if score == 1:
        level = "MEDIUM"
    elif score >= 2:
        level = "HIGH"
    return {"score": score, "level": level}

