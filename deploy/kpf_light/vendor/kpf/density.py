from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from .models import DensityPoint, KpfConfig, VolumeBar


class DensityBuilder:
    def time_weight(self, config: KpfConfig, as_of_time: datetime, bar: VolumeBar) -> float:
        end_time = datetime.fromtimestamp(bar.end_ts_ms / 1000, tz=UTC)
        age_days = max(0.0, (as_of_time - end_time).total_seconds() / 86400)
        if age_days <= 7:
            return config.time_weights["fresh_0_7d"]
        if age_days <= 30:
            return config.time_weights["mid_7_30d"]
        if age_days <= config.history_days:
            return config.time_weights["memory_30_90d"]
        return 0.0

    def build(self, config: KpfConfig, bars: list[VolumeBar], as_of_time: datetime, window_days: int) -> list[DensityPoint]:
        density: dict[int, float] = defaultdict(float)
        participation: dict[int, int] = defaultdict(int)
        for bar in bars:
            end_time = datetime.fromtimestamp(bar.end_ts_ms / 1000, tz=UTC)
            age_days = max(0.0, (as_of_time - end_time).total_seconds() / 86400)
            if age_days > window_days:
                continue
            weight = self.time_weight(config, as_of_time, bar)
            if weight == 0:
                continue
            total = float(bar.total_qty)
            for bin_key, qty in bar.bin_qty.items():
                share = float(qty) / total
                density[bin_key] += weight * share
                participation[bin_key] += 1
        return self._smooth(config, density, participation)

    def _smooth(self, config: KpfConfig, density: dict[int, float], participation: dict[int, int]) -> list[DensityPoint]:
        if not density:
            return []
        step = config.internal_bin_width_usd
        low = min(density)
        high = max(density)
        bins = list(range(low, high + step, step))
        left_w, center_w, right_w = config.smooth_kernel
        points: list[DensityPoint] = []
        for bin_key in bins:
            smooth = (
                left_w * density.get(bin_key - step, 0.0)
                + center_w * density.get(bin_key, 0.0)
                + right_w * density.get(bin_key + step, 0.0)
            )
            points.append(
                DensityPoint(
                    price_bin=bin_key,
                    density=round(density.get(bin_key, 0.0), 8),
                    smooth_density=round(smooth, 8),
                    participating_volume_bars=participation.get(bin_key, 0),
                )
            )
        return points
