from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from astra_edge_features import HistoricalBars


MINUTE_MS = 60_000
BASE_MS = 1_800_000_000_000


def bar(
    offset_minutes: int,
    open_price: float,
    close_price: float | None = None,
    *,
    high: float | None = None,
    low: float | None = None,
) -> dict[str, str]:
    close = open_price if close_price is None else close_price
    return {
        "open_time_ms": str(BASE_MS + offset_minutes * MINUTE_MS),
        "close_time_ms": str(BASE_MS + offset_minutes * MINUTE_MS + MINUTE_MS - 1),
        "open": str(open_price),
        "high": str(max(open_price, close) if high is None else high),
        "low": str(min(open_price, close) if low is None else low),
        "close": str(close),
    }


def test_window_uses_only_bars_closed_at_or_before_asof_and_excludes_signal_minute() -> None:
    rows = [
        bar(0, 100, 101),
        bar(1, 101, 102),
        bar(2, 102, 103),
        bar(3, 103, 999, high=2_000, low=10),
    ]
    signal_minute_open_ms = BASE_MS + 3 * MINUTE_MS

    window = HistoricalBars(rows).window(signal_minute_open_ms, n=3)

    assert window["history_4h_status"] == "available"
    assert window["history_first_open_ms"] == BASE_MS
    assert window["history_last_close_ms"] == BASE_MS + 3 * MINUTE_MS - 1
    assert window["history_4h_n"] == 3
    assert window["history_4h_return"] == pytest.approx(103 / 100 - 1)
    assert window["history_4h_range_fraction"] == pytest.approx(103 / 100 - 1)
    assert window["history_4h_midpoint"] == pytest.approx(101.5)


def test_future_extreme_bar_does_not_change_current_window_features() -> None:
    asof_ms = BASE_MS + 3 * MINUTE_MS
    normal_future = [bar(0, 100, 101), bar(1, 101, 102), bar(2, 102, 103), bar(3, 103, 104)]
    extreme_future = [
        bar(0, 100, 101),
        bar(1, 101, 102),
        bar(2, 102, 103),
        bar(3, 103, 1_000_000, high=1_000_000, low=1),
    ]

    normal = HistoricalBars(normal_future).window(asof_ms, n=3)
    extreme = HistoricalBars(extreme_future).window(asof_ms, n=3)

    assert extreme == normal


def test_window_reports_insufficient_history_with_last_closed_bar_identity() -> None:
    rows = [bar(0, 100, 101), bar(1, 101, 102)]

    window = HistoricalBars(rows).window(BASE_MS + 2 * MINUTE_MS, n=3)

    assert window == {
        "history_4h_status": "insufficient",
        "history_last_close_ms": BASE_MS + 2 * MINUTE_MS - 1,
    }


def test_window_reports_gap_when_selected_history_is_not_minute_contiguous() -> None:
    rows = [bar(0, 100, 101), bar(1, 101, 102), bar(3, 102, 103)]

    window = HistoricalBars(rows).window(BASE_MS + 4 * MINUTE_MS - 1, n=3)

    assert window == {
        "history_4h_status": "gap",
        "history_last_close_ms": BASE_MS + 4 * MINUTE_MS - 1,
    }


def test_window_reports_stale_when_latest_closed_bar_is_older_than_one_minute() -> None:
    rows = [bar(0, 100, 101), bar(1, 101, 102), bar(2, 102, 103)]

    window = HistoricalBars(rows).window(BASE_MS + 4 * MINUTE_MS, n=3)

    assert window == {
        "history_4h_status": "stale",
        "history_last_close_ms": BASE_MS + 3 * MINUTE_MS - 1,
    }


def test_flat_window_has_zero_return_range_and_efficiency() -> None:
    rows = [bar(0, 100), bar(1, 100), bar(2, 100)]

    window = HistoricalBars(rows).window(BASE_MS + 3 * MINUTE_MS - 1, n=3)

    assert window["history_4h_status"] == "available"
    assert window["history_4h_return"] == 0
    assert window["history_4h_range_fraction"] == 0
    assert window["history_4h_rv"] == 0
    assert window["history_4h_efficiency"] == 0
