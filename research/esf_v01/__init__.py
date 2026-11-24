"""ESF v0.1 (Episode Structural Fragility) offline research package.

Authority: RESEARCH_ONLY (live_veto_enabled=False, player_integration_enabled=False).
"""
from __future__ import annotations

from .esf_core import (
    DECISION_AUTHORITY,
    ESF_VERSION,
    LIVE_VETO_ENABLED,
    PARAM_CALIBRATION_STATUS,
    PLAYER_INTEGRATION_ENABLED,
)

__all__ = [
    "DECISION_AUTHORITY",
    "ESF_VERSION",
    "LIVE_VETO_ENABLED",
    "PARAM_CALIBRATION_STATUS",
    "PLAYER_INTEGRATION_ENABLED",
]
