from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import astra_light_study_analysis as light
import astra_second_study as second


BJT = timezone(timedelta(hours=8))


def ms(iso_text: str) -> int:
    return int(datetime.fromisoformat(iso_text.replace("Z", "+00:00")).timestamp() * 1000)


def bjt_ms(iso_text: str) -> int:
    return int(datetime.fromisoformat(iso_text).replace(tzinfo=BJT).timestamp() * 1000)


def config() -> dict:
    return {
        "ordinary_card_count": 2,
        "excluded_at_most_8_hours": 1,
        "ordinary_dte_hours": {"greater_than": 8, "at_most": 24},
        "sessions_bjt": [
            {"name": "post_us", "start": "04:00", "end": "08:00", "expected": 1},
            {"name": "asia", "start": "08:00", "end": "15:00", "expected": 1},
            {"name": "europe", "start": "15:00", "end": "20:00", "expected": 0},
            {"name": "us_pre", "start": "20:00", "end": "21:30", "expected": 0},
            {"name": "us_regular", "start": "21:30", "end": "04:00", "expected": 0},
        ],
        "ny_calendar": {"study_holidays": ["2026-06-19"]},
        "target_width_usd": [1500, 2000, 2500],
        "net_credit_fractions": [0.05, 0.1, 0.2],
        "margin": {"lambda": 0.9516590272, "lambda_sensitivity_multipliers": [0.75, 1, 1.25]},
        "weekend": {
            "observations": 1,
            "mature_at_original_cutoff": 0,
            "pending": 1,
            "age_sensitivity_hours": 24,
        },
        "original_cutoff_ms": bjt_ms("2026-06-21T09:00:00"),
    }


def sample(card_id: str, direction: str, event_ms: int) -> dict:
    return {
        "card_id": card_id,
        "episode_id": f"ep-{card_id}",
        "direction": direction,
        "version": "1.6.2",
        "event_time_ms": event_ms,
        "card_price": 100000.0,
    }


def spread_row(card_id: str, direction: str, side: str, entry_ms: int, dte_hours: float) -> dict:
    expiry_ms = entry_ms + int(dte_hours * second.HOUR_MS)
    return {
        "card_id": card_id,
        "episode_id": f"ep-{card_id}",
        "version": "1.6.2",
        "direction": direction,
        "side": side,
        "relation": "directional" if (direction == "BULLISH" and side == "put_credit") else "opposite",
        "entry_ms": entry_ms,
        "entry_price": 100000.0,
        "sample_card_price": 100000.0,
        "expiry_ms": expiry_ms,
        "delivery_date": "2026-06-22",
        "delivery_price": 100000.0,
        "dte_hours": dte_hours,
        "dte_bucket": "(8,16]" if dte_hours <= 16 else "(16,24]",
        "short_instrument": "short",
        "long_instrument": "long",
        "short_strike": 99500.0 if side == "put_credit" else 100500.0,
        "long_strike": 97500.0 if side == "put_credit" else 102500.0,
        "target_width": 2000.0,
        "actual_width": 2000.0,
        "credit_fraction": 0.1,
        "net_credit_btc": 0.002,
        "spread_intrinsic_usd": 0.0,
        "payout_btc": 0.0,
        "net_pnl_btc": 0.002,
        "result": "win",
        "short_otm_at_expiry": True,
        "payout_category": "zero",
    }


def kline(open_ms: int, price: float = 100000.0) -> dict:
    return {
        "open_time_ms": str(open_ms),
        "open": str(price),
        "high": str(price),
        "low": str(price),
        "close": str(price),
        "close_time_ms": str(open_ms + 59_999),
    }


def instruments(expiry_ms: int, creation_ms: int) -> list[dict]:
    rows = []
    for strike in (97000, 97500, 99500, 100500, 102500, 103000):
        rows.append({
            "instrument_name": f"BTC-{expiry_ms}-{strike}-P",
            "creation_timestamp": creation_ms,
            "expiration_timestamp": expiry_ms,
            "strike": strike,
            "option_type": "put",
            "settlement_currency": "BTC",
            "contract_size": 1,
        })
        rows.append({
            "instrument_name": f"BTC-{expiry_ms}-{strike}-C",
            "creation_timestamp": creation_ms,
            "expiration_timestamp": expiry_ms,
            "strike": strike,
            "option_type": "call",
            "settlement_currency": "BTC",
            "contract_size": 1,
        })
    return rows


class AstraSecondStudyTests(unittest.TestCase):
    def test_ordinary_filters_strictly_above_8h_and_keeps_zero_count_sessions(self):
        cfg = config()
        rows = [
            spread_row("short", "BULLISH", "put_credit", bjt_ms("2026-06-20T07:59:00"), 8.0),
            spread_row("long-a", "BULLISH", "put_credit", bjt_ms("2026-06-20T07:59:00"), 8.01),
            spread_row("long-b", "BULLISH", "put_credit", bjt_ms("2026-06-20T08:00:00"), 16.0),
        ]
        samples = [
            sample("short", "BULLISH", bjt_ms("2026-06-20T07:58:30")),
            sample("long-a", "BULLISH", bjt_ms("2026-06-20T07:58:30")),
            sample("long-b", "BULLISH", bjt_ms("2026-06-20T07:59:30")),
        ]
        result = second.build_ordinary_outputs(samples, rows, cfg)
        self.assertEqual(result["eligibility"]["ordinary_cards"], 2)
        self.assertEqual(result["eligibility"]["excluded_by_reason"], {"dte_at_most_8_hours": 1})
        self.assertEqual(result["session_counts"], {"post_us": 1, "asia": 1, "europe": 0, "us_pre": 0, "us_regular": 0})

    def test_apr_uses_lambda_width_entry_price_and_capital_time(self):
        cfg = config()
        row = spread_row("card", "BULLISH", "put_credit", ms("2026-06-20T00:00:00Z"), 12.0)
        row["net_pnl_btc"] = 0.01
        decorated = second._decorate_trade(row, cfg, cohort="ordinary")
        expected_margin = 0.9516590272 * 2000 / 100000
        self.assertAlmostEqual(decorated["estimated_margin_btc_lambda_1x"], expected_margin)
        self.assertAlmostEqual(decorated["holding_return_on_margin_1x"], 0.01 / expected_margin)
        metrics = second._metrics([decorated], cfg)
        self.assertAlmostEqual(metrics["scenario_apr_lambda_1x"], 365 * 0.01 / (expected_margin * 0.5))

    def test_weekend_uses_latest_prior_signal_and_keeps_pending_out_of_scores(self):
        cfg = config()
        saturday = bjt_ms("2026-06-20T09:00:00")
        prior = sample("prior", "BULLISH", saturday - 60_000)
        later = sample("later", "BEARISH", saturday + 60_000)
        entry_ms = bjt_ms("2026-06-20T09:01:00")
        expiry_ms = bjt_ms("2026-06-22T16:00:00")
        kline_by_open = light._index_klines([kline(entry_ms)])
        instrument_index = light._index_instruments(instruments(expiry_ms, entry_ms - 1))
        result = second.build_weekend_outputs([prior, later], kline_by_open, instrument_index, {}, cfg, {"prior"})
        self.assertEqual(result["eligibility"]["observations"], 1)
        self.assertEqual(result["eligibility"]["pending_observations"], 1)
        self.assertEqual(result["observations"][0]["source_card_id"], "prior")
        self.assertEqual(result["observations"][0]["status"], "pending_not_matured")
        self.assertEqual(result["eligibility"]["matured_trade_rows"], 0)
        self.assertEqual(result["eligibility"]["pending_trade_rows"], 18)


if __name__ == "__main__":
    unittest.main()
