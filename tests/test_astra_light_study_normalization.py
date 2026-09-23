import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from astra_light_study import normalize_archive, normalize_card


def card():
    return {
        "identity": {"card_id": "sample", "episode_id": "UP", "event_type": "NR_REPAIR_CONFIRMED",
                     "confirmed_time_ms": 100000, "strategy_version": "old", "symbol": "BTC"},
        "signal_window": {"nr_state": "NR_REPAIR_CONFIRMED", "episode_direction": "UP"},
        "decision": {"lean": "BEARISH_WEAK"},
        "market_context": {"price": 100, "price_source": "BINANCE_SPOT", "quote_currency": "USDT"},
        "factor_cross_section": {
            "anchor": {"ready": True, "band_clamped": False, "effective_flip_point": 100, "band_half": 2},
            "gamma_regime": {"regime": "TRANSITION", "net_gamma_notional_usd": .001},
            "gex_info": {"net_gamma_notional_usd": 1000000},
            "tmvf": {"direction": "Neutral-to-Bearish"},
            "micro_flow": {"combined": {"data_ready": True, "direction": "bearish"}},
        },
    }


class NormalizationTests(unittest.TestCase):
    def test_original_lean_not_shock(self):
        row = normalize_card(card(), 1, "hash")
        self.assertEqual(row["direction"], "BEARISH")
        self.assertEqual(row["episode_shock_direction_not_prediction"], "UP")

    def test_gamma_sources_not_coalesced(self):
        row = normalize_card(card(), 1, "hash")
        self.assertEqual(row["gamma_regime"], "TRANSITION")
        self.assertEqual(row["board_net_gex_usd"], 1000000)
        self.assertEqual(row["flow_alignment"], "aligned")

    def test_clamp_and_absent_facts_remain_unknown(self):
        value = card()
        value["factor_cross_section"]["anchor"]["band_clamped"] = True
        value["factor_cross_section"].pop("gex_info")
        row = normalize_card(value, 1, "hash")
        self.assertEqual(row["anchor_position"], "unknown")
        self.assertIsNone(row["board_net_gex_usd"])

    def test_fixed_synthetic_duplicates_and_bad_line_retained(self):
        first = card()
        fixed = copy.deepcopy(first)
        fixed["identity"].update(card_id="fixed", event_type="FIXED_ANALYSIS_ROUND")
        synthetic = copy.deepcopy(first)
        synthetic["identity"].update(card_id="synthetic", is_synthetic="true")
        raw = ("bad\n" + "\n".join(json.dumps(x) for x in [first, first, fixed, synthetic])).encode()
        rows, excluded = normalize_archive(raw, "hash")
        self.assertEqual(len(rows), 1)
        self.assertEqual({x["reason"] for x in excluded}, {"malformed_record", "exact_duplicate", "fixed_round", "synthetic"})

    def test_conflicting_duplicate_is_not_silently_selected(self):
        first, other = card(), card()
        other["decision"]["lean"] = "BULLISH_WEAK"
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate"):
            normalize_archive("\n".join(json.dumps(x) for x in [first, other]).encode(), "hash")

    def test_unknown_lean_cannot_become_neutral(self):
        value = card()
        value["decision"]["lean"] = "UNKNOWN_NEW_ENUM"
        with self.assertRaisesRegex(ValueError, "Unmapped"):
            normalize_card(value, 1, "hash")


if __name__ == "__main__":
    unittest.main()
