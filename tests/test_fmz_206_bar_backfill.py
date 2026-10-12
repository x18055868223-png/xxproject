"""FMZ 2.0.6: after a restart, volume bars are backfilled from spot 1m klines."""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SIGNAL_FILE = ROOT / "demo" / "最新交付物" / "neutral_regulation_demo_fmz.py"
NOW = 1_791_790_000_000


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location("nrd_signal_fmz_206", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def klines(n, volume=7.0, taker_buy=4.0, end_ms=NOW - 120_000, price=84000.0):
    rows = []
    for i in range(n):
        open_ts = end_ms - (n - i) * 60_000
        rows.append([open_ts, str(price), str(price + 10), str(price - 10), str(price + i),
                     str(volume), open_ts + 59_999, "0", 100, str(taker_buy), "0", "0"])
    return rows


def assembler(mod):
    return mod.BarAssembler(lambda **_: {"quality": mod.QUALITY_OK, "data": []}, mod.CONFIG, None)


def test_version_is_206(mod):
    assert mod.CONFIG["demo_version"] == "2.0.6"
    assert mod.CONFIG["volume_bar_backfill_enabled"] is True


def test_backfill_conserves_signed_flow_and_volume(mod):
    bars = assembler(mod)
    rows = klines(30, volume=7.0, taker_buy=4.0)
    out = bars.backfill_from_klines(rows, NOW)
    assert out["applied"] and out["kline_count"] == 30
    made = list(bars.completed_bars)
    # 210 BTC at 10 BTC per bar -> 21 full bars, no partial bar carried into live trading
    assert len(made) == 21 and bars.current_open is None
    assert sum(b["total_volume"] for b in made) == pytest.approx(210.0)
    assert sum(b["cvd_delta"] for b in made) == pytest.approx(30 * (2 * 4.0 - 7.0))
    assert all(b["source"] == "kline_backfill_1m" for b in made)
    ts = [b["complete_ts_ms"] for b in made]
    assert ts == sorted(ts)


def test_backfill_ignores_unclosed_and_invalid_klines(mod):
    bars = assembler(mod)
    rows = klines(5) + [[NOW - 30_000, "1", "1", "1", "1", "10", NOW + 29_999, "0", 1, "5", "0", "0"],
                        ["bad"], [NOW - 600_000, "1", "1", "1", "1", "10", NOW - 540_001, "0", 1, "11", "0", "0"]]
    out = bars.backfill_from_klines(rows, NOW - 60_000)
    assert out["kline_count"] == 5


def test_backfill_never_overwrites_existing_history(mod):
    bars = assembler(mod)
    bars.completed_bars.append({"complete_ts_ms": NOW, "cvd_delta": 1.0})
    assert bars.backfill_from_klines(klines(30), NOW) == {"applied": False, "reason": "history_not_empty"}
    assert len(bars.completed_bars) == 1


def test_backfilled_bars_make_micro_flow_ready_immediately(mod):
    bars = assembler(mod)
    bars.backfill_from_klines(klines(12 * 60, volume=12.0, taker_buy=7.0), NOW)
    flow = mod.compute_micro_flow_context(list(bars.completed_bars), mod.CONFIG)
    assert flow["fast_4h"]["data_ready"] and flow["slow_12h"]["data_ready"]
    assert flow["slow_12h"]["cvd_sum"] > 0


def test_runtime_backfills_once_and_survives_kline_failure(mod):
    runtime = mod.DemoRuntime(dict(mod.CONFIG))
    calls = []

    def failing(interval="1m", limit=720):
        calls.append(limit)
        return {"quality": mod.QUALITY_MISSING, "data": None}

    runtime.binance.fetch_spot_klines = failing
    out = runtime._backfill_volume_bars()
    assert out == {"applied": False, "reason": "klines_" + str(mod.QUALITY_MISSING)}
    assert calls == [12 * 60]
