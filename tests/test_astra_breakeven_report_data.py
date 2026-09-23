from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import astra_breakeven_report_data as report


def ms(iso_text: str) -> int:
    return int(datetime.fromisoformat(iso_text.replace("Z", "+00:00")).timestamp() * 1000)


def base_row(**overrides):
    row = {
        "card_id": "card-1",
        "episode_id": "ep-1",
        "version": "1.6.2",
        "direction": "BULLISH",
        "side": "put_credit",
        "relation": "directional",
        "entry_ms": str(ms("2026-09-10T07:58:00Z")),
        "entry_price": "100000",
        "sample_card_price": "100000",
        "expiry_ms": str(ms("2026-09-10T08:00:00Z")),
        "delivery_date": "2026-09-10",
        "delivery_price": "100000",
        "dte_hours": "0.03333333333333333",
        "dte_bucket": "(0,4]",
        "short_instrument": "S",
        "long_instrument": "L",
        "short_strike": "100000",
        "long_strike": "98000",
        "target_width": "2000",
        "actual_width": "2000",
        "credit_fraction": "0.1",
        "net_credit_btc": "0.002",
        "spread_intrinsic_usd": "0",
        "payout_btc": "0",
        "net_pnl_btc": "0.002",
        "result": "win",
        "short_otm_at_expiry": "True",
        "payout_category": "zero",
        "quantity_basis": "1 BTC inverse option group",
        "quote_basis": "synthetic",
    }
    row.update({key: str(value) for key, value in overrides.items()})
    return row


class BreakevenProjectionTests(unittest.TestCase):
    def test_put_breakeven_and_intrusion_use_credit_not_short_strike(self):
        credit = 0.002
        settlement = 100000 / (1 + credit)
        tie = base_row(
            delivery_price=settlement,
            spread_intrinsic_usd=100000 - settlement,
            payout_btc=credit,
            net_pnl_btc=0,
            short_otm_at_expiry=False,
        )
        projected = report.project_spread_row(tie)
        self.assertAlmostEqual(projected["breakeven_price"], settlement)
        self.assertEqual(projected["breakeven_intrusion_status"], "at_breakeven")
        self.assertTrue(projected["strike_intruded"])

        loss = base_row(
            delivery_price=99000,
            spread_intrinsic_usd=1000,
            payout_btc=1000 / 99000,
            net_pnl_btc=credit - 1000 / 99000,
            short_otm_at_expiry=False,
        )
        self.assertEqual(report.project_spread_row(loss)["breakeven_intrusion_status"], "intruded")

    def test_call_breakeven_keeps_far_tail_inverse_reentry(self):
        credit = 0.002
        near_be = 100000 / (1 - credit)
        ordinary_loss = base_row(
            direction="BEARISH",
            side="call_credit",
            relation="directional",
            delivery_price=100500,
            long_strike=102000,
            spread_intrinsic_usd=500,
            payout_btc=500 / 100500,
            net_pnl_btc=credit - 500 / 100500,
            short_otm_at_expiry=False,
        )
        projected_loss = report.project_spread_row(ordinary_loss)
        self.assertAlmostEqual(projected_loss["breakeven_price"], near_be)
        self.assertEqual(projected_loss["breakeven_intrusion_status"], "intruded")

        far_settlement = 2_000_000
        far_win = base_row(
            direction="BEARISH",
            side="call_credit",
            relation="directional",
            delivery_price=far_settlement,
            long_strike=102000,
            spread_intrinsic_usd=2000,
            payout_btc=2000 / far_settlement,
            net_pnl_btc=credit - 2000 / far_settlement,
            short_otm_at_expiry=False,
            payout_category="full_width",
        )
        projected_far = report.project_spread_row(far_win)
        self.assertEqual(projected_far["breakeven_intrusion_status"], "not_intruded")
        self.assertAlmostEqual(projected_far["call_far_reentry_price"], 1_000_000)
        self.assertGreater(far_settlement, near_be)

    def test_report_data_keeps_all_176_chronological_cards(self):
        with tempfile.TemporaryDirectory() as tmp:
            study_dir = Path(tmp)
            raw = b"frozen archive\n"
            source_hash = hashlib.sha256(raw).hexdigest()
            (study_dir / "raw_signal_review.jsonl").write_bytes(raw)
            _write_json(study_dir / "study_config.json", {
                "source_sha256": source_hash,
                "source_bytes": len(raw),
                "event_count": 176,
                "fixed_excluded": 47,
                "widths": [1500, 2000, 2500],
                "credit_ratios": [0.05, 0.1, 0.2],
                "entry_basis": "next_minute_open_BINANCE_SPOT_BTCUSDT",
                "expiry_basis": "next_08UTC_after_entry_max24h",
                "main_width": 2000,
            })
            _write_json(study_dir / "research_overview.json", {
                "paired_day_sensitivity": [],
                "main_actual_width_counts": {},
                "fixed_side_benchmarks": [],
                "width_sensitivity": [],
            })

            samples = []
            rows = []
            start = ms("2026-09-10T00:00:00Z")
            directions = ["BULLISH", "BEARISH", "NEUTRAL"]
            for index in range(176):
                card_id = f"card-{index:03d}"
                event_time = start + index * 60_000
                direction = directions[index % len(directions)]
                samples.append({
                    "card_id": card_id,
                    "episode_id": f"ep-{index:03d}",
                    "event_time_ms": event_time,
                    "version": "1.6.2",
                    "direction": direction,
                    "original_lean": direction,
                    "card_price": 100000,
                    "card_price_unit": "USDT",
                    "anchor_position": "inside",
                    "gamma_regime": "TRANSITION",
                    "board_net_gex_usd": 1_000_000,
                    "tmv_direction": "Neutral",
                    "flow_direction": "neutral",
                    "flow_alignment": "neutral_or_unknown",
                    "source_archive_sha256": source_hash,
                })
                for width in (1500, 2000, 2500):
                    for credit in (0.05, 0.1, 0.2):
                        for side in ("put_credit", "call_credit"):
                            relation = _relation(direction, side)
                            rows.append(_scenario_row(card_id, index, direction, side, relation, width, credit, event_time))

            _write_csv(study_dir / "standard_signal_samples.csv", samples)
            _write_csv(study_dir / "light_study_results.csv", rows)

            data, ledger_rows = report.build_report_data(study_dir)

        self.assertEqual(data["overview"]["signal_count"], 176)
        self.assertEqual(len(data["chronological_ledger"]), 176)
        self.assertEqual(len(ledger_rows), 176)
        self.assertEqual(data["chronological_ledger"][0]["seq"], 1)
        self.assertEqual(data["chronological_ledger"][-1]["seq"], 176)
        self.assertEqual(data["overview"]["event_interval_minutes"]["n"], 175)
        self.assertEqual(data["overview"]["event_interval_minutes"]["median"], 1)
        self.assertEqual(data["summaries"]["main_q10_directional_not_neutral"]["n"], 118)
        self.assertEqual(data["summaries"]["main_q10_neutral_put"]["n"], 58)
        self.assertEqual(data["summaries"]["main_q10_neutral_call"]["n"], 58)

    def test_hash_mismatch_is_rejected_before_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            study_dir = Path(tmp)
            (study_dir / "raw_signal_review.jsonl").write_bytes(b"actual")
            _write_json(study_dir / "study_config.json", {
                "source_sha256": hashlib.sha256(b"expected").hexdigest(),
                "source_bytes": len(b"actual"),
                "event_count": 176,
                "fixed_excluded": 47,
                "widths": [1500, 2000, 2500],
                "credit_ratios": [0.05, 0.1, 0.2],
                "main_width": 2000,
            })
            with self.assertRaisesRegex(ValueError, "sha256"):
                report.build_report_data(study_dir)


def _relation(direction: str, side: str) -> str:
    if direction == "BULLISH":
        return "directional" if side == "put_credit" else "opposite"
    if direction == "BEARISH":
        return "directional" if side == "call_credit" else "opposite"
    return "neutral_put" if side == "put_credit" else "neutral_call"


def _scenario_row(card_id: str, index: int, direction: str, side: str, relation: str, width: float, credit: float, event_time: int):
    entry_price = 100000.0
    entry_ms = event_time + 60_000
    expiry_ms = ms("2026-09-10T08:00:00Z")
    short = 99000.0 if side == "put_credit" else 101000.0
    long = short - width if side == "put_credit" else short + width
    net_credit = credit * width / entry_price
    return {
        "card_id": card_id,
        "episode_id": f"ep-{index:03d}",
        "version": "1.6.2",
        "direction": direction,
        "side": side,
        "relation": relation,
        "entry_ms": entry_ms,
        "entry_price": entry_price,
        "sample_card_price": entry_price,
        "expiry_ms": expiry_ms,
        "delivery_date": "2026-09-10",
        "delivery_price": entry_price,
        "dte_hours": (expiry_ms - entry_ms) / 3_600_000,
        "dte_bucket": "(4,8]",
        "short_instrument": f"S-{side}",
        "long_instrument": f"L-{side}",
        "short_strike": short,
        "long_strike": long,
        "target_width": width,
        "actual_width": width,
        "credit_fraction": credit,
        "net_credit_btc": net_credit,
        "spread_intrinsic_usd": 0,
        "payout_btc": 0,
        "net_pnl_btc": net_credit,
        "result": "win",
        "short_otm_at_expiry": True,
        "payout_category": "zero",
        "quantity_basis": "1 BTC inverse option group",
        "quote_basis": "synthetic",
    }


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows) -> None:
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
