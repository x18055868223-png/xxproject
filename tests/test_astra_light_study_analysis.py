from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import astra_light_study_analysis as study


def ms(iso_text):
    return int(datetime.fromisoformat(iso_text.replace("Z", "+00:00")).timestamp() * 1000)


def sample(card_id="card-1", direction="BULLISH", event_time_ms=None):
    return {
        "card_id": card_id,
        "episode_id": f"ep-{card_id}",
        "event_time_ms": event_time_ms if event_time_ms is not None else ms("2026-09-10T07:58:30Z"),
        "direction": direction,
        "version": "1.6.2",
        "card_price": 100000.0,
        "anchor_position": "inside",
        "gamma_regime": "positive_gamma",
        "flow_alignment": "aligned",
    }


def kline(open_ms, open_price=100000.0, high=None, low=None, close=None):
    return {
        "open_time_ms": str(open_ms),
        "open": str(open_price),
        "high": str(high if high is not None else open_price),
        "low": str(low if low is not None else open_price),
        "close": str(close if close is not None else open_price),
        "close_time_ms": str(open_ms + 59999),
        "volume": "1.0",
    }


def instrument(expiry_ms, strike, option_type, creation_ms=None, currency="BTC", contract_size=1, name=None):
    creation = creation_ms if creation_ms is not None else expiry_ms - 86_400_000 * 10
    code = "C" if option_type == "call" else "P"
    return {
        "instrument_name": f"BTC-{expiry_ms}-{strike:g}-{code}" if name is None else name,
        "creation_timestamp": creation,
        "expiration_timestamp": expiry_ms,
        "strike": strike,
        "option_type": option_type,
        "settlement_currency": currency,
        "contract_size": contract_size,
    }


def instruments_for(expiry_ms, entry_ms=None):
    creation = (entry_ms or expiry_ms) - 1
    strikes = [
        97000, 97500, 98000, 98500, 99500, 100000, 100500,
        101000, 102000, 102500, 103000,
    ]
    rows = []
    for strike in strikes:
        rows.append(instrument(expiry_ms, strike, "put", creation))
        rows.append(instrument(expiry_ms, strike, "call", creation))
    if entry_ms is not None:
        rows.append(instrument(expiry_ms, 100250, "call", entry_ms + 60_000))
        rows.append(instrument(expiry_ms, 99750, "put", entry_ms + 60_000))
    return rows


def delivery(expiry_ms, price):
    return [{"date": datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d"), "delivery_price": price}]


class AstraLightStudyAnalysisTests(unittest.TestCase):
    def test_entry_is_strict_next_minute_and_exact_08utc_uses_next_day(self):
        event_ms = ms("2026-09-10T07:59:00Z")
        entry_ms = ms("2026-09-10T08:00:00Z")
        expiry_ms = ms("2026-09-11T08:00:00Z")
        result = study.analyze(
            [sample(event_time_ms=event_ms)],
            [kline(entry_ms)],
            instruments_for(expiry_ms, entry_ms),
            delivery(expiry_ms, 100000.0),
            cutoff_ms=expiry_ms,
        )
        rows = result["spread_rows"]
        self.assertTrue(rows)
        self.assertEqual(rows[0]["entry_ms"], entry_ms)
        self.assertEqual(rows[0]["expiry_ms"], expiry_ms)
        self.assertEqual(rows[0]["dte_bucket"], "(16,24]")

    def test_entry_before_08utc_uses_same_day_delivery_and_boundary_bucket(self):
        event_ms = ms("2026-09-10T03:58:30Z")
        entry_ms = ms("2026-09-10T03:59:00Z")
        expiry_ms = ms("2026-09-10T08:00:00Z")
        result = study.analyze(
            [sample(event_time_ms=event_ms)],
            [kline(entry_ms)],
            instruments_for(expiry_ms, entry_ms),
            delivery(expiry_ms, 100000.0),
            cutoff_ms=expiry_ms,
        )
        self.assertTrue(result["spread_rows"])
        self.assertEqual(result["spread_rows"][0]["entry_ms"], entry_ms)
        self.assertEqual(result["spread_rows"][0]["dte_bucket"], "(4,8]")

    def test_strict_otm_and_creation_time_filter_short_legs(self):
        entry_ms = ms("2026-09-10T07:59:00Z")
        expiry_ms = ms("2026-09-10T08:00:00Z")
        result = study.analyze(
            [sample(event_time_ms=ms("2026-09-10T07:58:30Z"))],
            [kline(entry_ms)],
            instruments_for(expiry_ms, entry_ms),
            delivery(expiry_ms, 100000.0),
            cutoff_ms=expiry_ms,
        )
        put_rows = [row for row in result["spread_rows"] if row["side"] == "put_credit" and row["target_width"] == 2000 and row["credit_fraction"] == 0.10]
        call_rows = [row for row in result["spread_rows"] if row["side"] == "call_credit" and row["target_width"] == 2000 and row["credit_fraction"] == 0.10]
        self.assertEqual(put_rows[0]["short_strike"], 99500.0)
        self.assertEqual(call_rows[0]["short_strike"], 100500.0)
        self.assertNotEqual(call_rows[0]["short_strike"], 100250.0)
        self.assertNotEqual(put_rows[0]["short_strike"], 99750.0)

    def test_instrument_index_requires_name_positive_strike_and_one_btc_contract(self):
        entry_ms = ms("2026-09-10T07:59:00Z")
        expiry_ms = ms("2026-09-10T08:00:00Z")
        rows = [
            instrument(expiry_ms, 99500, "put", entry_ms - 1, contract_size=2),
            instrument(expiry_ms, 99000, "put", entry_ms - 1),
            instrument(expiry_ms, 97000, "put", entry_ms - 1),
            instrument(expiry_ms, 100500, "call", entry_ms - 1, name=""),
            instrument(expiry_ms, 101000, "call", entry_ms - 1),
            instrument(expiry_ms, 103000, "call", entry_ms - 1),
            instrument(expiry_ms, 0, "call", entry_ms - 1),
            instrument(expiry_ms, 98500, "put", entry_ms - 1, contract_size=None),
        ]
        result = study.analyze(
            [sample(event_time_ms=ms("2026-09-10T07:58:30Z"))],
            [kline(entry_ms)],
            rows,
            delivery(expiry_ms, 100000.0),
            cutoff_ms=expiry_ms,
        )
        put = next(row for row in result["spread_rows"] if row["side"] == "put_credit" and row["target_width"] == 2000 and row["credit_fraction"] == 0.05)
        call = next(row for row in result["spread_rows"] if row["side"] == "call_credit" and row["target_width"] == 2000 and row["credit_fraction"] == 0.05)
        self.assertEqual(put["short_strike"], 99000.0)
        self.assertEqual(put["long_strike"], 97000.0)
        self.assertEqual(call["short_strike"], 101000.0)
        self.assertEqual(call["long_strike"], 103000.0)

    def test_long_leg_closest_width_and_inverse_payout(self):
        entry_ms = ms("2026-09-10T07:59:00Z")
        expiry_ms = ms("2026-09-10T08:00:00Z")
        result = study.analyze(
            [sample(event_time_ms=ms("2026-09-10T07:58:30Z"))],
            [kline(entry_ms)],
            instruments_for(expiry_ms, entry_ms),
            delivery(expiry_ms, 99000.0),
            cutoff_ms=expiry_ms,
        )
        row = next(row for row in result["spread_rows"] if row["side"] == "put_credit" and row["target_width"] == 2000 and row["credit_fraction"] == 0.05)
        self.assertEqual(row["short_strike"], 99500.0)
        self.assertEqual(row["long_strike"], 97500.0)
        self.assertEqual(row["actual_width"], 2000.0)
        self.assertAlmostEqual(row["spread_intrinsic_usd"], 500.0)
        self.assertAlmostEqual(row["payout_btc"], 500.0 / 99000.0)
        self.assertAlmostEqual(row["net_credit_btc"], 0.05 * 2000.0 / 100000.0)
        self.assertEqual(row["result"], "loss")
        self.assertFalse(row["short_otm_at_expiry"])
        self.assertEqual(row["payout_category"], "partial")

        call_full = study.analyze(
            [sample(card_id="bear", direction="BEARISH", event_time_ms=ms("2026-09-10T07:58:30Z"))],
            [kline(entry_ms)],
            instruments_for(expiry_ms, entry_ms),
            delivery(expiry_ms, 103000.0),
            cutoff_ms=expiry_ms,
        )
        call_row = next(row for row in call_full["spread_rows"] if row["side"] == "call_credit" and row["target_width"] == 2000 and row["credit_fraction"] == 0.20)
        self.assertEqual(call_row["short_strike"], 100500.0)
        self.assertEqual(call_row["long_strike"], 102500.0)
        self.assertAlmostEqual(call_row["spread_intrinsic_usd"], 2000.0)
        self.assertAlmostEqual(call_row["payout_btc"], 2000.0 / 103000.0)
        self.assertAlmostEqual(call_row["net_credit_btc"], 0.20 * 2000.0 / 100000.0)
        self.assertEqual(call_row["payout_category"], "full_width")

    def test_not_matured_delivery_is_excluded_even_if_delivery_row_exists(self):
        entry_ms = ms("2026-09-10T08:00:00Z")
        expiry_ms = ms("2026-09-11T08:00:00Z")
        result = study.analyze(
            [sample(event_time_ms=ms("2026-09-10T07:59:00Z"))],
            [kline(entry_ms)],
            instruments_for(expiry_ms, entry_ms),
            delivery(expiry_ms, 100000.0),
            cutoff_ms=expiry_ms - 1,
        )
        self.assertFalse(result["spread_rows"])
        self.assertEqual(result["exclusions"][0]["reason"], "not_matured")

    def test_missing_delivery_and_missing_legs_are_reported(self):
        entry_ms = ms("2026-09-10T07:59:00Z")
        expiry_ms = ms("2026-09-10T08:00:00Z")
        no_delivery = study.analyze(
            [sample(event_time_ms=ms("2026-09-10T07:58:30Z"))],
            [kline(entry_ms)],
            instruments_for(expiry_ms, entry_ms),
            [],
            cutoff_ms=expiry_ms,
        )
        self.assertFalse(no_delivery["spread_rows"])
        self.assertEqual(no_delivery["exclusions"][0]["reason"], "missing_delivery")

        missing_legs = study.analyze(
            [sample(event_time_ms=ms("2026-09-10T07:58:30Z"))],
            [kline(entry_ms)],
            [instrument(expiry_ms, 99500, "put", entry_ms - 1)],
            delivery(expiry_ms, 100000.0),
            cutoff_ms=expiry_ms,
        )
        reasons = {item["reason"] for item in missing_legs["exclusions"]}
        self.assertIn("missing_long_leg", reasons)
        self.assertIn("missing_short_leg", reasons)

    def test_missing_path_minutes_do_not_remove_expiry_payoff(self):
        entry_ms = ms("2026-09-10T07:50:00Z")
        expiry_ms = ms("2026-09-10T08:00:00Z")
        result = study.analyze(
            [sample(event_time_ms=ms("2026-09-10T07:49:30Z"))],
            [kline(entry_ms, high=100100, low=99900)],
            instruments_for(expiry_ms, entry_ms),
            delivery(expiry_ms, 100000.0),
            cutoff_ms=expiry_ms,
        )
        self.assertTrue(result["spread_rows"])
        self.assertEqual(len(result["path_rows"]), 1)
        self.assertEqual(result["path_rows"][0]["expected_minutes"], 10)
        self.assertEqual(result["path_rows"][0]["observed_minutes"], 1)
        self.assertFalse(result["path_rows"][0]["path_complete"])

    def test_invalid_path_candles_count_as_gaps(self):
        entry_ms = ms("2026-09-10T07:57:00Z")
        expiry_ms = ms("2026-09-10T08:00:00Z")
        valid = kline(entry_ms, high=100100, low=99900, close=100050)
        missing_high = kline(entry_ms + 60_000, high=100100, low=99900, close=100020)
        missing_high["high"] = ""
        late_close = kline(entry_ms + 120_000, high=100200, low=99800, close=100100)
        late_close["close_time_ms"] = str(expiry_ms + 1)
        result = study.analyze(
            [sample(event_time_ms=ms("2026-09-10T07:56:30Z"))],
            [valid, missing_high, late_close],
            instruments_for(expiry_ms, entry_ms),
            delivery(expiry_ms, 100000.0),
            cutoff_ms=expiry_ms,
        )
        path = result["path_rows"][0]
        self.assertTrue(result["spread_rows"])
        self.assertEqual(path["expected_minutes"], 3)
        self.assertEqual(path["observed_minutes"], 3)
        self.assertEqual(path["valid_minutes"], 1)
        self.assertEqual(path["invalid_minutes"], 2)
        self.assertEqual(path["missing_minutes"], 2)
        self.assertFalse(path["path_complete"])
        self.assertEqual(path["min_low"], 99900.0)
        self.assertEqual(path["max_high"], 100100.0)

    def test_neutral_keeps_both_sides_without_picking_winner(self):
        entry_ms = ms("2026-09-10T07:59:00Z")
        expiry_ms = ms("2026-09-10T08:00:00Z")
        result = study.analyze(
            [sample(card_id="neutral", direction="NEUTRAL", event_time_ms=ms("2026-09-10T07:58:30Z"))],
            [kline(entry_ms)],
            instruments_for(expiry_ms, entry_ms),
            delivery(expiry_ms, 100000.0),
            cutoff_ms=expiry_ms,
        )
        relations = {row["relation"] for row in result["spread_rows"]}
        self.assertEqual(relations, {"neutral_put", "neutral_call"})
        sides = {row["side"] for row in result["spread_rows"]}
        self.assertEqual(sides, {"put_credit", "call_credit"})

    def test_summary_denominator_is_not_multiplied_by_widths_or_credits(self):
        entry_ms = ms("2026-09-10T07:59:00Z")
        expiry_ms = ms("2026-09-10T08:00:00Z")
        result = study.analyze(
            [sample(event_time_ms=ms("2026-09-10T07:58:30Z"))],
            [kline(entry_ms)],
            instruments_for(expiry_ms, entry_ms),
            delivery(expiry_ms, 100000.0),
            cutoff_ms=expiry_ms,
        )
        self.assertEqual(result["summaries"]["input"]["unique_input_cards"], 1)
        self.assertEqual(len(result["spread_rows"]), 18)
        for row in result["summaries"]["overall"]:
            self.assertEqual(row["n"], 1)
        profitable = [row for row in result["summaries"]["overall"] if row["win_rate"] == 1.0]
        self.assertTrue(profitable)
        self.assertTrue(all(row["avg_abs_loss_btc"] is None for row in profitable))
        self.assertTrue(all(row["payoff_ratio"] is None for row in profitable))

    def test_paired_comparison_tracks_directional_side_against_same_card_opposite(self):
        entry_ms = ms("2026-09-10T07:59:00Z")
        expiry_ms = ms("2026-09-10T08:00:00Z")
        result = study.analyze(
            [
                sample(card_id="bull", direction="BULLISH", event_time_ms=ms("2026-09-10T07:58:30Z")),
                sample(card_id="bear", direction="BEARISH", event_time_ms=ms("2026-09-10T07:58:30Z")),
            ],
            [kline(entry_ms)],
            instruments_for(expiry_ms, entry_ms),
            delivery(expiry_ms, 99000.0),
            cutoff_ms=expiry_ms,
        )
        paired = result["summaries"]["paired_comparison"]
        bullish = next(row for row in paired["bullish_put_vs_call"] if row["target_width"] == 2000 and row["credit_fraction"] == 0.05)
        bearish = next(row for row in paired["bearish_call_vs_put"] if row["target_width"] == 2000 and row["credit_fraction"] == 0.05)
        pooled = next(row for row in paired["pooled_directional_vs_opposite"] if row["target_width"] == 2000 and row["credit_fraction"] == 0.05)

        self.assertEqual(bullish["n"], 1)
        self.assertEqual(bullish["net_pnl_preferred_losses"], 1)
        self.assertEqual(bullish["otm_preferred_losses"], 1)
        self.assertEqual(bearish["n"], 1)
        self.assertEqual(bearish["net_pnl_preferred_wins"], 1)
        self.assertEqual(bearish["otm_preferred_wins"], 1)
        self.assertEqual(pooled["n"], 2)
        self.assertEqual(pooled["net_pnl_preferred_wins"], 1)
        self.assertEqual(pooled["net_pnl_preferred_losses"], 1)


if __name__ == "__main__":
    unittest.main()
