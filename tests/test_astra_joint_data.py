import math
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from astra_joint_data import (
    HistoricalBars,
    MINUTE_MS,
    RollingHistoricalBars,
    is_complete_minute_bar,
    normalize_kline,
    read_binance_klines,
)


BASE_MS = 1_700_000_040_000


def kline(index, *, price=None, volume=10.0, buy=6.0):
    open_ms = BASE_MS + index * MINUTE_MS
    base = float(price if price is not None else 100.0 + index)
    close = base + 0.5
    return {
        "open_time_ms": open_ms,
        "open": base,
        "high": close + 0.2,
        "low": base - 0.2,
        "close": close,
        "volume": volume,
        "close_time_ms": open_ms + MINUTE_MS - 1,
        "quote_volume": volume * ((base + close) / 2.0),
        "taker_buy_base_volume": buy,
    }


class JointDataTests(unittest.TestCase):
    def test_stale_complete_window_is_not_current(self):
        rows = [kline(i) for i in range(30)]
        history = HistoricalBars(rows)
        rolling = RollingHistoricalBars()
        for row in rows:
            rolling.append(row)
        last = rows[-1]['close_time_ms']
        for source in (history, rolling):
            self.assertIsNotNone(source.closed_window(last + 1, 15))
            self.assertIsNone(source.closed_window(last, 15))
            self.assertIsNone(source.closed_window(last + MINUTE_MS, 15))
            self.assertIsNone(source.feature_snapshot(last + 86400000)['ret_15'])

    def test_normalizes_microsecond_timestamps_and_taker_buy_volume(self):
        row = normalize_kline(
            [
                "1700000040000000",
                "100",
                "101",
                "99",
                "100.5",
                "12",
                "1700000099999000",
                "1206",
                "3",
                "7",
                "704",
            ]
        )
        self.assertEqual(row["open_time_ms"], BASE_MS)
        self.assertEqual(row["close_time_ms"], BASE_MS + MINUTE_MS - 1)
        self.assertEqual(row["taker_buy_base_volume"], 7.0)
        self.assertEqual(row["quote_volume"], 1206.0)

    def test_rejects_misaligned_or_non_complete_minute_shapes(self):
        legal = kline(0)
        self.assertTrue(is_complete_minute_bar(legal, as_of_ms=legal["close_time_ms"] + 1))
        self.assertFalse(is_complete_minute_bar(legal, as_of_ms=legal["close_time_ms"]))
        self.assertFalse(is_complete_minute_bar(legal, as_of_ms=float("inf")))
        self.assertFalse(is_complete_minute_bar({**legal, "open_time_ms": True}))
        misaligned = {**legal, "open_time_ms": legal["open_time_ms"] + 1, "open_time": legal["open_time_ms"] + 1}
        bad_close = {**legal, "close_time_ms": legal["open_time_ms"] + MINUTE_MS, "close_time": legal["open_time_ms"] + MINUTE_MS}

        self.assertIsNone(normalize_kline(misaligned))
        self.assertIsNone(normalize_kline(bad_close))
        self.assertIsNone(normalize_kline({**legal, "close_time_ms": float("inf")}))
        self.assertIsNone(normalize_kline({**legal, "open_time_ms": True}))
        self.assertIsNone(HistoricalBars([*([kline(i) for i in range(15)]), bad_close]).closed_window(bad_close["close_time_ms"] + 1, 15))

    def test_forming_flags_are_not_lost_by_historical_normalization(self):
        forming = {**kline(0), "forming_open_only": True, "path_fields_usable": False}
        self.assertIsNone(normalize_kline(forming))
        history = HistoricalBars([forming, *[kline(i) for i in range(1, 16)]])
        rolling = RollingHistoricalBars()

        self.assertIsNone(history.row_at(forming["open_time_ms"]))
        self.assertIsNone(rolling.append(forming))
        for row in [kline(i) for i in range(1, 16)]:
            rolling.append(row)

        as_of = kline(15)["close_time_ms"] + 1
        self.assertIsNotNone(history.closed_window(as_of, 15))
        self.assertIsNotNone(rolling.closed_window(as_of, 15))

    def test_reads_binance_zip_without_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bars.zip"
            csv_body = "1700000040000,100,101,99,100.5,12,1700000099999,1206,3,7,704,0\n"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("BTCUSDT-1m.csv", csv_body)
            rows = read_binance_klines(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["open"], 100.0)

    def test_feature_snapshot_uses_only_closed_rows_not_future_extreme(self):
        rows = [kline(i) for i in range(31)]
        rows[30]["high"] = 10_000.0
        bars = HistoricalBars(rows)
        as_of = rows[29]["close_time_ms"] + 1
        features = bars.feature_snapshot(as_of)
        expected_return = rows[29]["close"] / rows[15]["open"] - 1.0
        self.assertAlmostEqual(features["ret_15"], expected_return)
        self.assertAlmostEqual(features["net_flow_15"], 0.2)
        self.assertLess(features["range_position_15"], 1.01)
        self.assertGreater(features["vwap_migration_15"], 0.0)

    def test_feature_snapshot_does_not_cross_missing_minute(self):
        rows = [kline(i) for i in range(20) if i != 12]
        bars = HistoricalBars(rows)
        features = bars.feature_snapshot(rows[-1]["close_time_ms"] + 1)
        self.assertIsNone(features["ret_15"])
        self.assertEqual(features["window_status"]["15"], "insufficient_or_gap")

    def test_missing_taker_buy_volume_does_not_impute_sell_pressure(self):
        rows = [kline(i) for i in range(20)]
        for row in rows[-15:]:
            row.pop("taker_buy_base_volume")
        features = HistoricalBars(rows).feature_snapshot(rows[-1]["close_time_ms"] + 1)
        self.assertEqual(features["window_status"]["15"], "available")
        self.assertIsNone(features["net_flow_15"])
        self.assertIsNone(features["pressure_response_15"])
        self.assertIsNotNone(features["ret_15"])

    def test_partially_missing_taker_buy_volume_keeps_window_flow_unknown(self):
        rows = [kline(i) for i in range(20)]
        rows[-1].pop("taker_buy_base_volume")
        features = HistoricalBars(rows).feature_snapshot(rows[-1]["close_time_ms"] + 1)
        self.assertIsNone(features["net_flow_15"])
        self.assertIsNone(features["pressure_response_15"])

    def test_missing_quote_volume_does_not_create_vwap_proxy(self):
        rows = [kline(i) for i in range(40)]
        for row in rows[-30:]:
            row.pop("quote_volume")
        features = HistoricalBars(rows).feature_snapshot(rows[-1]["close_time_ms"] + 1)
        self.assertEqual(features["window_status"]["15"], "available")
        self.assertIsNone(features["vwap_deviation_15"])
        self.assertIsNone(features["vwap_deviation_30"])
        self.assertIsNone(features["vwap_migration_15"])
        self.assertIsNotNone(features["ret_15"])

    def test_partially_missing_quote_volume_keeps_vwap_unknown(self):
        rows = [kline(i) for i in range(40)]
        rows[-1].pop("quote_volume")
        features = HistoricalBars(rows).feature_snapshot(rows[-1]["close_time_ms"] + 1)
        self.assertIsNone(features["vwap_deviation_15"])
        self.assertIsNone(features["vwap_deviation_30"])

    def test_zero_volume_window_leaves_flow_and_vwap_unknown(self):
        rows = [kline(i, volume=0.0, buy=0.0) for i in range(20)]
        for row in rows:
            row["quote_volume"] = 0.0
        features = HistoricalBars(rows).feature_snapshot(rows[-1]["close_time_ms"] + 1)
        self.assertEqual(features["window_status"]["15"], "available")
        self.assertIsNone(features["net_flow_15"])
        self.assertIsNone(features["vwap_deviation_15"])
        self.assertIsNone(features["pressure_response_15"])

    def test_moves_since_extends_to_current_as_of(self):
        rows = [kline(i, price=100.0 + i) for i in range(25)]
        bars = HistoricalBars(rows)
        shock_as_of = rows[10]["close_time_ms"] + 1
        current_as_of = rows[20]["close_time_ms"] + 1
        features = bars.feature_snapshot(current_as_of, shock_as_of_ms=shock_as_of, shock_direction=1)
        self.assertGreater(features["up_move_since_shock"], 0.08)
        self.assertGreaterEqual(features["down_move_since_shock"], 0.0)
        self.assertEqual(features["favorable_move_since_shock"], features["up_move_since_shock"])

    def test_rolling_bars_bound_memory_and_match_historical_snapshot(self):
        rows = [kline(i, price=100.0 + math.sin(i / 10.0)) for i in range(1500)]
        rolling = RollingHistoricalBars(max_minutes=1441)
        for item in rows:
            rolling.append(item)
        self.assertLessEqual(len(rolling), 1441)
        historical = HistoricalBars(rows[-1441:])
        as_of = rows[-1]["close_time_ms"] + 1
        expected = historical.feature_snapshot(as_of)
        actual = rolling.feature_snapshot(as_of)
        self.assertAlmostEqual(actual["ret_15"], expected["ret_15"])
        self.assertAlmostEqual(actual["ret_1440"], expected["ret_1440"])
        self.assertAlmostEqual(actual["vwap_deviation_30"], expected["vwap_deviation_30"])


if __name__ == "__main__":
    unittest.main()
