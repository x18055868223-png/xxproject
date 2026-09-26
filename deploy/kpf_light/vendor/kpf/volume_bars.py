from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from .models import KpfConfig, TradeRecord, VolumeBar


def price_bin(price: Decimal, width: int) -> int:
    scaled = (price / Decimal(width)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(scaled) * width


class VolumeBarBuilder:
    def __init__(self, config: KpfConfig):
        self.config = config
        self._index = 0
        self._qty = Decimal("0")
        self._bins: dict[int, Decimal] = {}
        self._end_ts_ms = 0
        self.dropped_tail_qty = Decimal("0")

    def add_trade(self, trade: TradeRecord) -> list[VolumeBar]:
        if trade.qty <= 0:
            return []
        closed: list[VolumeBar] = []
        remaining = trade.qty
        bin_key = price_bin(trade.price, self.config.internal_bin_width_usd)
        while remaining > 0:
            capacity = self.config.volume_bar_size_btc - self._qty
            take = min(capacity, remaining)
            self._bins[bin_key] = self._bins.get(bin_key, Decimal("0")) + take
            self._qty += take
            self._end_ts_ms = trade.ts_ms
            remaining -= take
            if self._qty == self.config.volume_bar_size_btc:
                closed.append(self._close_bar())
        return closed

    def build(self, trades: Iterable[TradeRecord]) -> list[VolumeBar]:
        bars: list[VolumeBar] = []
        for trade in trades:
            bars.extend(self.add_trade(trade))
        self.dropped_tail_qty = self._qty
        return bars

    @property
    def tail_qty(self) -> Decimal:
        return self._qty

    def to_state(self) -> dict:
        return {
            "index": self._index,
            "tail_qty": str(self._qty),
            "tail_bins": {str(k): str(v) for k, v in self._bins.items()},
            "end_ts_ms": self._end_ts_ms,
        }

    @classmethod
    def from_state(cls, config: KpfConfig, state: dict | None = None) -> "VolumeBarBuilder":
        builder = cls(config)
        if not state:
            return builder
        builder._index = int(state.get("index", 0))
        builder._qty = Decimal(str(state.get("tail_qty", "0")))
        builder._bins = {int(k): Decimal(str(v)) for k, v in state.get("tail_bins", {}).items()}
        builder._end_ts_ms = int(state.get("end_ts_ms", 0))
        return builder

    def _close_bar(self) -> VolumeBar:
        bar = VolumeBar(
            index=self._index,
            end_ts_ms=self._end_ts_ms,
            total_qty=self._qty,
            bin_qty=dict(self._bins),
        )
        self._index += 1
        self._qty = Decimal("0")
        self._bins = {}
        self._end_ts_ms = 0
        return bar
