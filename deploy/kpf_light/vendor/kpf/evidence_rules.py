from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from .models import BasinCandidate


@dataclass(frozen=True)
class GradeRule:
    grade_path: str
    grade_score_min: float = 0.0
    prominence_min: float = 0.0
    excess_mass_min: float = 0.0
    legacy_prominence_min: float = 0.0
    legacy_excess_mass_min: float = 0.0
    participation_bars_min: int = 0
    basin_width_min: int = 0
    basin_width_max: int = 0


@dataclass(frozen=True)
class EvidenceV102Rules:
    version: str = "v1.02"
    robust_percentile: float = 95.0
    robust_min_sample_count: int = 5
    distance_min_usd: int = 250
    grade_score_prominence_weight: float = 0.45
    grade_score_excess_weight: float = 0.40
    grade_score_participation_weight: float = 0.15
    eligible: GradeRule = GradeRule(
        grade_path="ELIGIBLE",
        prominence_min=0.20,
        excess_mass_min=0.25,
        participation_bars_min=8,
        basin_width_min=100,
        basin_width_max=750,
    )
    a_standard: GradeRule = GradeRule(
        grade_path="A_STANDARD",
        grade_score_min=0.78,
        prominence_min=0.50,
        excess_mass_min=0.60,
        legacy_prominence_min=0.50,
        legacy_excess_mass_min=0.60,
        participation_bars_min=25,
        basin_width_max=500,
    )
    b_standard: GradeRule = GradeRule(
        grade_path="B_STANDARD",
        prominence_min=0.35,
        excess_mass_min=0.40,
        legacy_prominence_min=0.35,
        legacy_excess_mass_min=0.40,
        participation_bars_min=12,
        basin_width_max=750,
    )
    b_compensated: GradeRule = GradeRule(
        grade_path="B_COMPENSATED",
        grade_score_min=0.60,
        prominence_min=0.25,
        excess_mass_min=0.60,
        participation_bars_min=25,
        basin_width_max=750,
    )
    c_weak: GradeRule = GradeRule(
        grade_path="C_WEAK",
        prominence_min=0.20,
        excess_mass_min=0.25,
        participation_bars_min=8,
        basin_width_min=100,
        basin_width_max=750,
    )

    def safe_norm(self, raw: float, denominator: float | None) -> float:
        if denominator is None or denominator <= 0 or not math.isfinite(denominator):
            return 0.0
        if not math.isfinite(raw) or raw <= 0:
            return 0.0
        return round(min(1.0, raw / denominator), 6)

    def percentile_or_max(self, values: Iterable[float]) -> float:
        finite = sorted(value for value in values if math.isfinite(value) and value > 0)
        if not finite:
            return 0.0
        if len(finite) < self.robust_min_sample_count:
            return finite[-1]
        rank = (self.robust_percentile / 100.0) * (len(finite) - 1)
        low = math.floor(rank)
        high = math.ceil(rank)
        if low == high:
            return finite[int(rank)]
        fraction = rank - low
        return finite[low] + (finite[high] - finite[low]) * fraction

    def participation_norm(self, participating_volume_bars: int, denominator: float | None) -> float:
        if denominator is None or denominator <= 0 or not math.isfinite(denominator):
            return 0.0
        if participating_volume_bars <= 0:
            return 0.0
        return round(min(1.0, math.log1p(participating_volume_bars) / math.log1p(denominator)), 6)

    def grade_score(self, candidate: BasinCandidate) -> float:
        score = (
            self.grade_score_prominence_weight * candidate.prominence_norm_robust
            + self.grade_score_excess_weight * candidate.excess_mass_norm_robust
            + self.grade_score_participation_weight * candidate.participation_norm
        )
        return round(score, 6)

    def eligibility_failure_reason(self, candidate: BasinCandidate, data_quality_state: str) -> str:
        if data_quality_state not in {"OK", "DEGRADED_MINOR"}:
            return f"data_quality_{data_quality_state}"
        if candidate.basin_width_usd < self.eligible.basin_width_min:
            return "basin_width_too_narrow"
        if candidate.basin_width_usd > self.eligible.basin_width_max:
            return "basin_width_too_wide"
        if candidate.distance_usd is not None and candidate.distance_usd < self.distance_min_usd:
            return "too_close_to_current"
        if candidate.prominence_norm_robust < self.eligible.prominence_min:
            return "prominence_below_v102_base"
        if candidate.excess_mass_norm_robust < self.eligible.excess_mass_min:
            return "excess_mass_below_v102_base"
        if candidate.participating_volume_bars < self.eligible.participation_bars_min:
            return "participating_bars_below_v102_base"
        return ""

    def is_a_standard(self, candidate: BasinCandidate, data_quality_state: str) -> bool:
        return (
            data_quality_state == "OK"
            and candidate.evidence_type != "RECENT_ONLY"
            and candidate.grade_score >= self.a_standard.grade_score_min
            and candidate.prominence_norm_robust >= self.a_standard.prominence_min
            and candidate.excess_mass_norm_robust >= self.a_standard.excess_mass_min
            and candidate.prominence_norm_legacy >= self.a_standard.legacy_prominence_min
            and candidate.excess_mass_norm_legacy >= self.a_standard.legacy_excess_mass_min
            and candidate.participating_volume_bars >= self.a_standard.participation_bars_min
            and candidate.basin_width_usd <= self.a_standard.basin_width_max
        )

    def is_b_standard(self, candidate: BasinCandidate, data_quality_state: str) -> bool:
        return (
            data_quality_state in {"OK", "DEGRADED_MINOR"}
            and candidate.prominence_norm_robust >= self.b_standard.prominence_min
            and candidate.excess_mass_norm_robust >= self.b_standard.excess_mass_min
            and candidate.prominence_norm_legacy >= self.b_standard.legacy_prominence_min
            and candidate.excess_mass_norm_legacy >= self.b_standard.legacy_excess_mass_min
            and candidate.participating_volume_bars >= self.b_standard.participation_bars_min
            and candidate.basin_width_usd <= self.b_standard.basin_width_max
        )

    def is_b_compensated(self, candidate: BasinCandidate, data_quality_state: str) -> bool:
        return (
            data_quality_state in {"OK", "DEGRADED_MINOR"}
            and candidate.grade_score >= self.b_compensated.grade_score_min
            and candidate.prominence_norm_robust >= self.b_compensated.prominence_min
            and candidate.excess_mass_norm_robust >= self.b_compensated.excess_mass_min
            and candidate.participating_volume_bars >= self.b_compensated.participation_bars_min
            and candidate.basin_width_usd <= self.b_compensated.basin_width_max
            and candidate.evidence_type in {"FRESH_CONFIRMED", "MEMORY_VALID"}
        )

    def is_c_weak(self, candidate: BasinCandidate, data_quality_state: str) -> bool:
        return (
            data_quality_state in {"OK", "DEGRADED_MINOR"}
            and candidate.prominence_norm_robust >= self.c_weak.prominence_min
            and candidate.excess_mass_norm_robust >= self.c_weak.excess_mass_min
            and candidate.participating_volume_bars >= self.c_weak.participation_bars_min
            and self.c_weak.basin_width_min <= candidate.basin_width_usd <= self.c_weak.basin_width_max
        )


EVIDENCE_V102_RULES = EvidenceV102Rules()
