"""Configuration and locked shadow-isolation constants for GFRI v1.1.

All policy/history thresholds are marked UNVALIDATED_CANDIDATE_PARAMS until
prospective economic calibration against >=30-50 settled outcomes completes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

CODE_VERSION = "1.1.0"
SCHEMA_EVAL = "gfri_eval_v11"
SCHEMA_AUDIT = "gfri_audit_v11"

# Locked authority flags (never flipped in v1.1 research/shadow release)
LOCKED_DECISION_AUTHORITY = "SHADOW_ONLY"
LOCKED_PLAYER_INTEGRATION_ENABLED = False
LOCKED_LIVE_VETO_ENABLED = False

KNOWN_OI_SOURCE_LAG_HOURS = 8
KNOWN_OI_LAG_NOTE = "deribit_public_oi_reported_with_8h_delay_per_docs"
KNOWN_DEALER_MODEL = "oi_proxy_call_long_put_short"


@dataclass(frozen=True)
class ShadowFlags:
    """Execution & isolation switches; collector defaults OFF."""

    collector_enabled: bool = False
    decision_authority: str = LOCKED_DECISION_AUTHORITY
    player_integration_enabled: bool = LOCKED_PLAYER_INTEGRATION_ENABLED
    live_veto_enabled: bool = LOCKED_LIVE_VETO_ENABLED

    def validate_locked(self) -> None:
        """Fail fast if any caller attempts to grant execution authority in v1.1."""
        if self.decision_authority != LOCKED_DECISION_AUTHORITY:
            raise ValueError(f"v1.1 forbids decision_authority={self.decision_authority!r}")
        if self.player_integration_enabled:
            raise ValueError("v1.1 forbids player_integration_enabled=True")
        if self.live_veto_enabled:
            raise ValueError("v1.1 forbids live_veto_enabled=True")


@dataclass(frozen=True)
class GfriConfig:
    """Deterministic calculation, data quality, history, and candidate policy config."""

    # Endpoints
    gex_latest_url: str = "https://gexmonitor.com/api/gex-latest?asset=BTC&exchange=all&lite=true"
    deribit_instrument_url: str = "https://www.deribit.com/api/v2/public/get_instrument?instrument_name=BTC-PERPETUAL"
    deribit_book_url: str = "https://www.deribit.com/api/v2/public/get_order_book?instrument_name=BTC-PERPETUAL&depth=1000"
    okx_instrument_url: str = "https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId=BTC-USDT-SWAP"
    okx_book_url: str = "https://www.okx.com/api/v5/market/books-full?instId=BTC-USDT-SWAP&sz=5000"

    # Quality & timing gates (G2)
    requested_deribit_depth: int = 1000
    min_deribit_depth_param: int = 1000
    min_book_levels_per_side: int = 10
    instrument_cache_ttl_ms: int = 24 * 3600_000    # 24h versioned verified spec cache TTL
    deribit_spec_schema_version: int = 1
    max_ob_skew_ms: int = 15 * 60_000               # 900,000 ms (15 min GEX vs OB skew)
    max_ob_age_ms: int = 60_000                     # 60,000 ms (60s candidate OB age vs freeze; UNVALIDATED)
    max_ob_receive_lag_ms: int = 30_000             # 30,000 ms (30s candidate exchange->HTTP receive lag; UNVALIDATED)
    max_source_age_ms: int = 60 * 60_000            # 3,600,000 ms (60 min GEX source age)
    max_future_drift_ms: int = 5_000                # 5,000 ms NTP allowance vs freeze_at_ms
    max_price_divergence_degraded_pct: float = 0.002   # 0.20%
    max_price_divergence_fail_pct: float = 0.005       # 0.50%
    max_spread_pct: float = 0.01                    # 1.00% corrupt/dislocated spread guard

    # Confirmation channel (G2.3) - 3 pre-freeze OB samples spaced ~15s apart (<60s OB age)
    confirm_sample_count: int = 3
    confirm_interval_sec: float = 15.0
    confirm_window_ms: int = 180_000                # 3 min max span across confirmation samples
    min_confirm_valid_samples: int = 2
    max_confirm_spread_ratio: float = 2.0           # max/min flag threshold inside confirmation trio
    max_two_sample_divergence_ratio: float = 2.0    # conflict guard when only 2 valid samples exist

    # Bounded storage & crash-recovery locks (P0-6 / P1-1)
    stale_lock_timeout_ms: int = 60_000             # Break stale lockfiles older than 60s
    raw_capture_retention_days: int = 7             # Non-destructive cold compression / archive horizon

    # History reference & cold start (G3) - UNVALIDATED_CANDIDATE_PARAMS
    near_window_ms: int = 4 * 3600_000         # 4 hours
    min_near_samples: int = 4
    long_window_days: int = 60
    long_half_life_days: float = 14.0
    min_days_for_band: int = 7
    min_days_for_percentile: int = 30
    min_effective_samples_for_percentile: int = 30
    regime_drift_high_floor: float = 2.50
    band_thresholds: tuple[float, float, float] = (1.00, 2.00, 3.00)  # LOW/NORMAL/ELEVATED/HIGH

    # Candidate shadow veto policy (G4) - UNVALIDATED_CANDIDATE_PARAMS
    policy_version: str = "gfri_shadow_policy_v11_candidate_1"
    trigger_abs_gfri: float = 2.50
    elevated_abs_gfri: float = 2.00
    trigger_deterioration_ratio: float = 1.50
    trigger_consecutive_cycles: int = 2
    release_abs_gfri: float = 2.00
    release_consecutive_cycles: int = 2
    sensitivity_presets: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            "sensitive": {"trigger_abs_gfri": 2.00, "release_abs_gfri": 1.60},
            "baseline": {"trigger_abs_gfri": 2.50, "release_abs_gfri": 2.00},
            "conservative": {"trigger_abs_gfri": 3.50, "release_abs_gfri": 2.80},
        }
    )

    # Lock safety switches
    flags: ShadowFlags = field(default_factory=ShadowFlags)

    def to_audit_dict(self) -> dict[str, Any]:
        """Serialize non-secret config parameters for deterministic audit replay."""
        return {
            **asdict(self),
            "param_calibration_status": "UNVALIDATED_CANDIDATE_PARAMS",
        }


DEFAULT_CONFIG = GfriConfig()
