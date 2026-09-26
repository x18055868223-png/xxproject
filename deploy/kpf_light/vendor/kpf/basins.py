from __future__ import annotations

from .models import BasinCandidate, DensityPoint


class BasinExtractor:
    def extract(self, points: list[DensityPoint], source_window: str) -> list[BasinCandidate]:
        if len(points) < 3:
            return []
        peaks: list[int] = []
        for idx in range(1, len(points) - 1):
            left = points[idx - 1].smooth_density
            center = points[idx].smooth_density
            right = points[idx + 1].smooth_density
            if center > left and center >= right and center > 0:
                peaks.append(idx)
        if not peaks:
            return []
        total_mass = sum(p.smooth_density for p in points)
        candidates: list[BasinCandidate] = []
        prominence_values: list[float] = []
        excess_values: list[float] = []
        for idx in peaks:
            left_idx = self._left_valley(points, idx)
            right_idx = self._right_valley(points, idx)
            saddle = max(points[left_idx].smooth_density, points[right_idx].smooth_density)
            prominence_raw = points[idx].smooth_density - saddle
            basin_slice = points[left_idx : right_idx + 1]
            excess = sum(max(0.0, p.smooth_density - saddle) for p in basin_slice)
            basin_mass = sum(p.smooth_density for p in basin_slice)
            prominence_values.append(prominence_raw)
            excess_values.append(excess)
            candidates.append(
                BasinCandidate(
                    raw_center=points[idx].price_bin,
                    raw_basin_low=points[left_idx].price_bin,
                    raw_basin_high=points[right_idx].price_bin,
                    raw_density_peak=points[idx].smooth_density,
                    saddle_level=saddle,
                    prominence_norm=0.0,
                    excess_mass_norm=0.0,
                    participating_volume_bars=sum(
                        p.participating_volume_bars for p in basin_slice
                    ),
                    basin_width_usd=points[right_idx].price_bin - points[left_idx].price_bin,
                    source_window=source_window,
                    volume_share=round(basin_mass / total_mass, 6) if total_mass > 0 else 0.0,
                    prominence_raw=prominence_raw,
                )
            )
        # Normalize prominence and excess against the max of the SAME quantity so
        # the two legacy gates share a comparable [0,1] scale. (Pre-v1.0.4 divided
        # prominence by peak HEIGHT, which capped legacy prominence far below 1.0
        # and made A_STANDARD / B_STANDARD mathematically unreachable.)
        max_prominence = max(prominence_values) if prominence_values else 0.0
        max_excess = max(excess_values) if excess_values else 0.0
        for candidate, prominence, excess in zip(candidates, prominence_values, excess_values):
            prominence_norm = round(prominence / max_prominence, 6) if max_prominence > 0 else 0.0
            excess_norm = round(excess / max_excess, 6) if max_excess > 0 else 0.0
            candidate.prominence_norm = prominence_norm
            candidate.prominence_norm_legacy = prominence_norm
            candidate.excess_mass_raw = excess
            candidate.excess_mass_norm = excess_norm
            candidate.excess_mass_norm_legacy = excess_norm
        return candidates

    def _left_valley(self, points: list[DensityPoint], peak_idx: int) -> int:
        idx = peak_idx
        while idx > 0 and points[idx - 1].smooth_density <= points[idx].smooth_density:
            idx -= 1
        return idx

    def _right_valley(self, points: list[DensityPoint], peak_idx: int) -> int:
        idx = peak_idx
        while idx < len(points) - 1 and points[idx + 1].smooth_density <= points[idx].smooth_density:
            idx += 1
        return idx
