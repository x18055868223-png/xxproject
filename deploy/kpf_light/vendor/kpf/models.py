from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

# Single source of truth for the factor/contract version. Imported by __init__,
# key_price_axis, reports, and audit so the version can never drift between them.
# v1.0.4: corrected legacy-prominence normalization + volume_share + re-anchor/staleness.
# v1.1.0: real walk-forward self-backtest (kpf.replay) added; it showed evidence grades
#   (A/B/C, volume_share) do NOT predict out-of-sample acceptance — only proximity does —
#   so the deliverable is ranked by proximity with an empirical proximity-acceptance tier
#   and grades are demoted to descriptive-only. See docs/CHANGELOG.md.
FACTOR_VERSION = "v1.1.0"


@dataclass(frozen=True)
class KpfConfig:
    symbol: str
    market: str
    data_type: str
    workspace_root: Path
    data_source_base_url: str
    history_days: int
    fresh_confirm_days: int
    internal_bin_width_usd: int
    output_price_step_usd: int
    display_band_half_width_usd: int
    volume_bar_size_btc: Decimal
    time_weights: dict[str, float]
    smooth_kernel: list[float]
    max_targets_each_side: int
    basin_width: dict[str, int]
    thresholds: dict[str, dict[str, float]]
    download: dict[str, int | float]
    report: dict[str, bool]


@dataclass(frozen=True)
class FilePlan:
    as_of_time: datetime
    effective_end_date: date
    required_dates: list[date]
    fresh_dates: list[date]


@dataclass
class FileStatus:
    date: date
    raw_path: Path
    checksum_path: Path
    verified_path: Path
    exists_raw: bool = False
    exists_checksum: bool = False
    verified: bool = False
    checksum_missing: bool = False
    checksum_failed: bool = False
    schema_failed: bool = False
    downloaded: bool = False
    error: str | None = None


@dataclass(frozen=True)
class TradeRecord:
    agg_id: int
    price: Decimal
    qty: Decimal
    first_trade_id: int
    last_trade_id: int
    ts_ms: int
    is_buyer_maker: bool
    source_file: str


@dataclass
class ParseAudit:
    source_file: str
    rows_total: int = 0
    rows_ok: int = 0
    bad_rows: list[dict[str, Any]] = field(default_factory=list)
    timestamp_units: set[str] = field(default_factory=set)
    schema_failed: bool = False


@dataclass(frozen=True)
class VolumeBar:
    index: int
    end_ts_ms: int
    total_qty: Decimal
    bin_qty: dict[int, Decimal]


@dataclass(frozen=True)
class DensityPoint:
    price_bin: int
    density: float
    smooth_density: float
    participating_volume_bars: int


@dataclass
class BasinCandidate:
    raw_center: int
    raw_basin_low: int
    raw_basin_high: int
    raw_density_peak: float
    saddle_level: float
    prominence_norm: float
    excess_mass_norm: float
    participating_volume_bars: int
    basin_width_usd: int
    source_window: str
    prominence_raw: float = 0.0
    excess_mass_raw: float = 0.0
    prominence_norm_legacy: float = 0.0
    excess_mass_norm_legacy: float = 0.0
    prominence_norm_robust: float = 0.0
    excess_mass_norm_robust: float = 0.0
    participation_norm: float = 0.0
    # Absolute, cross-time-comparable strength anchor: this basin's integrated
    # smoothed density as a fraction of the whole profile's mass. Unlike the
    # robust/legacy/grade_score measures (all normalized within a single
    # snapshot), volume_share keeps the same meaning day to day, so the
    # execution layer can threshold it consistently over time.
    volume_share: float = 0.0
    grade_score: float = 0.0
    grade_path: str = "UNASSIGNED"
    evidence_grade: str = "WEAK"
    evidence_type: str = "WEAK"
    display_center: int | None = None
    display_band: tuple[int, int] | None = None
    side: str | None = None
    distance_usd: float | None = None
    weak_only: bool = False
    filtered_reason: str = ""


@dataclass
class DataAudit:
    required_dates: list[str]
    verified_dates: list[str]
    missing_dates: list[str]
    downloaded_files: list[str] = field(default_factory=list)
    checksum_missing_files: list[str] = field(default_factory=list)
    checksum_failed_files: list[str] = field(default_factory=list)
    schema_failed_files: list[str] = field(default_factory=list)
    duplicate_count: int = 0
    duplicate_conflict_count: int = 0
    timestamp_unit_detected: str = "unknown"
    coverage_ratio_history: float = 0.0
    coverage_ratio_fresh: float = 0.0
    data_quality_state: str = "INVALID"
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "required_dates": self.required_dates,
            "verified_dates": self.verified_dates,
            "missing_dates": self.missing_dates,
            "downloaded_files": self.downloaded_files,
            "checksum_missing_files": self.checksum_missing_files,
            "checksum_failed_files": self.checksum_failed_files,
            "schema_failed_files": self.schema_failed_files,
            "duplicate_count": self.duplicate_count,
            "duplicate_conflict_count": self.duplicate_conflict_count,
            "timestamp_unit_detected": self.timestamp_unit_detected,
            "coverage_ratio_history": self.coverage_ratio_history,
            "coverage_ratio_fresh": self.coverage_ratio_fresh,
            "data_quality_state": self.data_quality_state,
            "warnings": self.warnings,
        }
