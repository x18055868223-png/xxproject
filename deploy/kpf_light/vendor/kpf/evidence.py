from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

from .evidence_rules import EVIDENCE_V102_RULES, EvidenceV102Rules
from .models import BasinCandidate, KpfConfig
from .targets import display_center_for_raw


GRADE_RANK = {"A": 3, "B": 2, "C": 1, "WEAK": 0}


class EvidenceAssigner:
    def __init__(self, rules: EvidenceV102Rules = EVIDENCE_V102_RULES) -> None:
        self.rules = rules

    def assign(
        self,
        config: KpfConfig,
        candidates_90d: list[BasinCandidate],
        candidates_60d: list[BasinCandidate],
        data_quality_state: str,
        current_price: Decimal | None = None,
    ) -> list[BasinCandidate]:
        prepared_90 = [deepcopy(candidate) for candidate in candidates_90d]
        prepared_60 = [deepcopy(candidate) for candidate in candidates_60d]
        for candidate in prepared_90 + prepared_60:
            self._prepare_candidate(config, candidate, current_price)
        self._apply_v102_norms(prepared_90 + prepared_60)

        assigned: list[BasinCandidate] = []
        for item in prepared_90:
            match = self._find_fresh_match(config, item, prepared_60)
            if match:
                item.evidence_type = "FRESH_CONFIRMED"
            elif self._meets_noncompensated_strength(item, data_quality_state):
                item.evidence_type = "MEMORY_VALID"
            else:
                item.evidence_type = "WEAK"
            item.evidence_grade = self._grade(item, data_quality_state)
            assigned.append(item)

        for fresh in prepared_60:
            if self._find_fresh_match(config, fresh, prepared_90):
                continue
            item = fresh
            item.evidence_type = (
                "RECENT_ONLY" if self._meets_noncompensated_strength(item, data_quality_state) else "WEAK"
            )
            item.evidence_grade = self._grade(item, data_quality_state)
            assigned.append(item)
        return assigned

    def _prepare_candidate(
        self, config: KpfConfig, candidate: BasinCandidate, current_price: Decimal | None
    ) -> None:
        center = display_center_for_raw(config, candidate.raw_center)
        candidate.display_center = center
        candidate.display_band = (
            center - config.display_band_half_width_usd,
            center + config.display_band_half_width_usd,
        )
        if current_price is not None:
            current = float(current_price)
            candidate.distance_usd = abs(center - current)
            if center > current:
                candidate.side = "above"
            elif center < current:
                candidate.side = "below"
            else:
                candidate.side = "at_price"
        if candidate.prominence_norm_legacy <= 0:
            candidate.prominence_norm_legacy = candidate.prominence_norm
        if candidate.excess_mass_norm_legacy <= 0:
            candidate.excess_mass_norm_legacy = candidate.excess_mass_norm
        if candidate.prominence_raw <= 0:
            raw_prominence = candidate.raw_density_peak - candidate.saddle_level
            candidate.prominence_raw = raw_prominence if raw_prominence > 0 else candidate.prominence_norm_legacy
        if candidate.excess_mass_raw <= 0:
            candidate.excess_mass_raw = candidate.excess_mass_norm_legacy

    def _apply_v102_norms(self, candidates: list[BasinCandidate]) -> None:
        preeligible = [
            candidate
            for candidate in candidates
            if candidate.basin_width_usd >= self.rules.eligible.basin_width_min
            and candidate.basin_width_usd <= self.rules.eligible.basin_width_max
            and candidate.participating_volume_bars >= self.rules.eligible.participation_bars_min
            and (candidate.distance_usd is None or candidate.distance_usd >= self.rules.distance_min_usd)
            and candidate.prominence_raw > 0
            and candidate.excess_mass_raw > 0
        ]
        prominence_denominator = self.rules.percentile_or_max(c.prominence_raw for c in preeligible)
        excess_denominator = self.rules.percentile_or_max(c.excess_mass_raw for c in preeligible)
        participation_denominator = self.rules.percentile_or_max(
            float(c.participating_volume_bars) for c in preeligible
        )
        for candidate in candidates:
            candidate.prominence_norm_robust = self.rules.safe_norm(
                candidate.prominence_raw, prominence_denominator
            )
            candidate.excess_mass_norm_robust = self.rules.safe_norm(
                candidate.excess_mass_raw, excess_denominator
            )
            candidate.participation_norm = self.rules.participation_norm(
                candidate.participating_volume_bars, participation_denominator
            )
            candidate.grade_score = self.rules.grade_score(candidate)
            candidate.prominence_norm = candidate.prominence_norm_robust
            candidate.excess_mass_norm = candidate.excess_mass_norm_robust

    def _find_fresh_match(
        self, config: KpfConfig, candidate: BasinCandidate, others: list[BasinCandidate]
    ) -> BasinCandidate | None:
        center = display_center_for_raw(config, candidate.raw_center)
        for other in others:
            other_center = display_center_for_raw(config, other.raw_center)
            if center == other_center or abs(candidate.raw_center - other.raw_center) <= 250:
                return other
        return None

    def _meets_noncompensated_strength(self, candidate: BasinCandidate, data_quality_state: str) -> bool:
        return self.rules.is_a_standard(candidate, data_quality_state) or self.rules.is_b_standard(
            candidate, data_quality_state
        )

    def _grade(self, candidate: BasinCandidate, data_quality_state: str) -> str:
        failure_reason = self.rules.eligibility_failure_reason(candidate, data_quality_state)
        if failure_reason:
            candidate.grade_path = f"FILTERED_{failure_reason}"
            candidate.filtered_reason = failure_reason
            return "WEAK"
        if self.rules.is_a_standard(candidate, data_quality_state):
            candidate.grade_path = self.rules.a_standard.grade_path
            return "A"
        if self.rules.is_b_standard(candidate, data_quality_state):
            candidate.grade_path = self.rules.b_standard.grade_path
            return "B"
        if self.rules.is_b_compensated(candidate, data_quality_state):
            candidate.grade_path = self.rules.b_compensated.grade_path
            return "B"
        if self.rules.is_c_weak(candidate, data_quality_state):
            candidate.grade_path = self.rules.c_weak.grade_path
            return "C"
        candidate.grade_path = "WEAK_ONLY"
        candidate.filtered_reason = "weak_evidence"
        return "WEAK"
