import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import astra_offline_regrade as regrade
import signal_evidence_v2 as evidence
import signal_review_v2 as review


def ms(iso_text):
    return int(datetime.fromisoformat(iso_text.replace("Z", "+00:00")).timestamp() * 1000)


def producer_hash(card_id):
    return "sha256:" + hashlib.sha256(card_id.encode("utf-8")).hexdigest()


def card(card_id, iso_text, *, event_type="NR_REPAIR_CONFIRMED", lean="BULLISH_WEAK"):
    event_ms = ms(iso_text)
    return {
        "schema_version": "signal_audit_card@unit-test",
        "identity": {
            "card_id": card_id,
            "symbol": "BTC",
            "strategy_version": "1.6.2",
            "confirmed_time_ms": event_ms,
            "confirmed_at": iso_text,
            "event_type": event_type,
        },
        "producer_integrity": {"record_hash": producer_hash(card_id)},
        "market_context": {"price": 100000.0, "quote_currency": "USDT"},
        "signal_window": {"nr_state": "NR_REPAIR_CONFIRMED", "state": "ACTIVE"},
        "decision": {"lean": lean, "support_label": "MODEL_SUPPORT", "trade_allowed": False},
        "decision_matrix": {"direction": "BULLISH", "decision_state": "MODEL_SUPPORT", "execution_allowed": False},
        "blocking": {"has_block": False, "soft_gates": []},
        "factor_cross_section": {
            "anchor": {
                "ready": True,
                "effective_flip_point": 100000.0,
                "band_half": 1000.0,
                "band_clamped": False,
                "freshness": "fresh",
            },
            "tmvf": {"direction": "BULLISH", "tmv_blend": 0.2},
            "micro_flow": {"combined": {"data_ready": False}},
        },
    }


class AstraOfflineRegradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.study = self.root / "study"
        self.ratings = self.root / "ratings"
        self.study.mkdir()
        self.records = [
            card("fixed-prev", "2026-09-09T22:00:00Z", event_type="FIXED_ANALYSIS_ROUND"),
            card("short-dte", "2026-09-10T00:01:00Z"),
            card("selected-1", "2026-09-10T08:01:00Z"),
            card("selected-2", "2026-09-10T12:00:00Z"),
        ]
        raw = (
            "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in self.records[:1])
            + "\n{broken historical line\n"
            + "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in self.records[1:])
            + "\n"
        )
        raw_path = self.study / "raw_signal_review.jsonl"
        raw_path.write_text(raw, encoding="utf-8")
        (self.study / "study_config.json").write_text(json.dumps({
            "source_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            "source_card_count": 4,
            "event_count": 3,
            "fixed_excluded": 1,
        }), encoding="utf-8")

    def prepare(self):
        return regrade.prepare_freeze(
            self.study,
            self.ratings,
            model=review.DEFAULT_MODEL,
            base_url="https://example.invalid/chat/completions",
            timeout=240,
            expected_count=2,
            expected_event_count=3,
            expected_fixed_count=1,
            force=True,
        )

    def test_prepare_freezes_only_8_to_24h_events_without_market_results(self):
        result = self.prepare()
        self.assertEqual(result["prepared"], 2)
        index = regrade._read_jsonl(self.ratings / "freeze" / "index.jsonl")
        self.assertEqual([row["card_id"] for row in index], ["selected-1", "selected-2"])
        self.assertTrue(all(8.0 < row["dte_hours"] <= 24.0 for row in index))
        self.assertTrue(all(row["packet_schema"] == evidence.PACKET_SCHEMA_VERSION for row in index))
        self.assertTrue(all(row["request_prompt_version"] == review.PROMPT_VERSION for row in index))
        text = (self.ratings / "freeze" / "index.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("net_pnl_btc", text)
        self.assertNotIn("delivery_price", text)
        self.assertTrue((self.ratings / "input_manifest.json").exists())

    def test_run_uses_fake_transport_and_restart_does_not_repeat(self):
        self.prepare()
        calls = []

        def fake_transport(*args, **kwargs):
            calls.append(1)
            return {
                "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }

        def fake_review(card_arg, payload_arg, packet_arg, **kwargs):
            return {
                "schema_version": review.OUTPUT_SCHEMA_VERSION,
                "prompt_version": review.PROMPT_VERSION,
                "status": "OK",
                "model": kwargs.get("model"),
                "input_packet_hash": evidence.packet_hash(packet_arg),
                "integrated_trade_advisory": {"side_evidence_ratings": {}},
            }

        with patch.object(regrade.runtime_v2, "build_review", side_effect=fake_review):
            first = regrade.run_reviews(
                self.ratings,
                api_key="test-only",
                total_limit=2,
                daily_limit=2,
                concurrency=2,
                transport=fake_transport,
            )
            second = regrade.run_reviews(
                self.ratings,
                api_key="test-only",
                total_limit=2,
                daily_limit=2,
                concurrency=2,
                transport=fake_transport,
            )
        self.assertEqual(first["written"], 2)
        self.assertEqual(first["budget"]["total_http_calls_used"], 2)
        self.assertEqual(second["skipped_existing"], 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(regrade.status_report(self.ratings)["cards"]["pending"], 0)

    def test_corrupt_total_budget_ledger_fails_closed_before_http(self):
        self.prepare()
        (self.ratings / "budget.json").write_text("{not-json", encoding="utf-8")
        calls = []
        with self.assertRaises(RuntimeError):
            regrade.run_reviews(
                self.ratings,
                api_key="test-only",
                transport=lambda *a, **k: calls.append(1),
            )
        self.assertEqual(calls, [])
        self.assertFalse((self.ratings / "reviews.jsonl").exists())

    def test_force_prepare_refuses_existing_review_state(self):
        self.prepare()
        (self.ratings / "reviews.jsonl").write_text("{}", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.prepare()

    def test_export_keeps_two_side_rows_for_pending_and_error_cards(self):
        self.prepare()
        index = regrade._read_jsonl(self.ratings / "freeze" / "index.jsonl")
        first = index[0]
        raw_card = regrade._read_json(self.ratings / first["card_path"])
        packet = regrade._read_json(self.ratings / first["packet_path"])
        error_review = review.build_error_review(
            raw_card,
            packet,
            "unit test failure",
            model=review.DEFAULT_MODEL,
            prompt_version=review.PROMPT_VERSION,
            require_price_bias=True,
        )
        regrade._write_jsonl(self.ratings / "reviews.jsonl", [{
            "card_id": first["card_id"],
            "llm_review": error_review,
        }])
        exported = regrade.export_ratings(self.ratings)
        self.assertEqual(exported["cards"], 2)
        self.assertEqual(exported["sides"], 4)
        side_rows = regrade._read_jsonl(self.ratings / "ratings_by_side.jsonl")
        self.assertEqual(len(side_rows), 4)
        self.assertEqual({row["side"] for row in side_rows}, {"put_credit", "call_credit"})
        self.assertIn("PENDING", {row["review_status"] for row in side_rows})
        self.assertIn("ERROR", {row["review_status"] for row in side_rows})


if __name__ == "__main__":
    unittest.main()
