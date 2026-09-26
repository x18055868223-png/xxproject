from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP

from .models import BasinCandidate, KpfConfig


GRADE_RANK = {"A": 3, "B": 2, "C": 1, "WEAK": 0}


def display_center_for_raw(config: KpfConfig, raw_center: int) -> int:
    step = Decimal(config.output_price_step_usd)
    scaled = (Decimal(raw_center) / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(scaled) * config.output_price_step_usd


class TargetRanker:
    def rank(
        self,
        config: KpfConfig,
        candidates: list[BasinCandidate],
        current_price: Decimal,
        data_quality_state: str,
    ) -> tuple[list[BasinCandidate], list[BasinCandidate], list[BasinCandidate]]:
        prepared = [self._prepare(config, candidate, current_price) for candidate in candidates]
        debug = [deepcopy(candidate) for candidate in prepared]
        if data_quality_state in {"DEGRADED_MAJOR", "INVALID"}:
            for candidate in debug:
                candidate.filtered_reason = f"data_quality_{data_quality_state}"
            return [], [], debug
        eligible: list[BasinCandidate] = []
        for candidate in prepared:
            reason = self._filter_reason(config, candidate)
            if reason:
                candidate.filtered_reason = reason
            else:
                eligible.append(candidate)
        merged = self._merge_same_display(eligible)
        above = self._pick_side(config, [c for c in merged if c.side == "above"])
        below = self._pick_side(config, [c for c in merged if c.side == "below"])
        selected_ids = {id(c) for c in above + below}
        debug = []
        for candidate in prepared:
            if not candidate.filtered_reason and all(not self._same_public(candidate, chosen) for chosen in above + below):
                candidate.filtered_reason = "not_selected"
            debug.append(candidate)
        return above, below, debug

    def _prepare(self, config: KpfConfig, candidate: BasinCandidate, current_price: Decimal) -> BasinCandidate:
        item = deepcopy(candidate)
        center = display_center_for_raw(config, item.raw_center)
        item.display_center = center
        item.display_band = (
            center - config.display_band_half_width_usd,
            center + config.display_band_half_width_usd,
        )
        current = float(current_price)
        item.distance_usd = abs(center - current)
        if center > current:
            item.side = "above"
        elif center < current:
            item.side = "below"
        else:
            item.side = "at_price"
        return item

    def _filter_reason(self, config: KpfConfig, candidate: BasinCandidate) -> str:
        if candidate.side not in {"above", "below"}:
            return "not_above_or_below"
        if candidate.distance_usd is None or candidate.distance_usd < config.output_price_step_usd:
            return "too_close_to_current"
        if candidate.evidence_grade == "WEAK":
            return candidate.filtered_reason or "weak_evidence"
        if candidate.evidence_type == "RECENT_ONLY" and candidate.evidence_grade == "A":
            return "recent_only_cannot_be_A"
        return ""

    def _merge_same_display(self, candidates: list[BasinCandidate]) -> list[BasinCandidate]:
        best: dict[tuple[str, int], BasinCandidate] = {}
        for candidate in candidates:
            key = (candidate.side or "", candidate.display_center or 0)
            incumbent = best.get(key)
            if incumbent is None or self._sort_strength(candidate) > self._sort_strength(incumbent):
                best[key] = candidate
        return list(best.values())

    def _pick_side(self, config: KpfConfig, candidates: list[BasinCandidate]) -> list[BasinCandidate]:
        strong = [c for c in candidates if c.evidence_grade in {"A", "B"}]
        strong.sort(key=lambda c: (c.distance_usd or 0, -self._sort_strength(c)[0], -self._sort_strength(c)[1]))
        if strong:
            for candidate in strong:
                candidate.weak_only = False
            return strong[: config.max_targets_each_side]
        weak = [c for c in candidates if c.evidence_grade == "C"]
        weak.sort(key=lambda c: c.distance_usd or 0)
        if weak:
            weak[0].weak_only = True
            return [weak[0]]
        return []

    def _sort_strength(self, candidate: BasinCandidate) -> tuple[int, float, float, float, float]:
        return (
            GRADE_RANK.get(candidate.evidence_grade, 0),
            candidate.grade_score,
            candidate.excess_mass_norm_robust or candidate.excess_mass_norm,
            candidate.prominence_norm_robust or candidate.prominence_norm,
            -(candidate.distance_usd or 0.0),
        )

    def _same_public(self, left: BasinCandidate, right: BasinCandidate) -> bool:
        return left.side == right.side and left.display_center == right.display_center
