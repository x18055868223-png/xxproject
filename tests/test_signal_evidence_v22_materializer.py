import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import test_signal_evidence_v21_materializer as historical
from test_signal_evidence_v21_materializer import answer_v21
from test_signal_evidence_v2 import base_card
import materialize_signal_cards as materializer
import signal_review_v2 as review
from signal_evidence_v2 import build_evidence_packet


def answer_v22():
    value = answer_v21()
    for side in value["side_evidence_ratings"].values():
        side["mechanism"] = {"summary_cn": side.pop("mechanism_cn"), "refs": ["structure.gamma.regime"]}
        side.pop("market_counter_cn", None)
        side["primary_counter_ref"] = next((r["ref"] for r in side["evidence_roles"] if r["role"] == "counters_fit"), None)
    value["advisory_guidance"] = {
        "summary_cn": "可以研究 Put 侧，重点权衡空间与补偿。",
        "tradeoffs_cn": ["更远位置可能增加空间，也可能减少权利金；先比较实际报价。"],
        "evidence_refs": ["structure.gamma.regime", "pressure.tmv.direction"],
        "outlooks": [{"horizon_hours": hours, "scenario_cn": "结构不变时继续观察当前压力。",
                      "watch_cn": "若出现反向推进，重新比较两侧。", "evidence_refs": ["pressure.tmv.direction"]}
                     for hours in (4, 24)]}
    return value


class MaterializerV22Tests(unittest.TestCase):
    materialize = historical.MaterializerV21Tests.materialize

    def test_new_review_roundtrip_preserves_advice_and_source_hash(self):
        source = base_card()
        source["llm_review"] = review.build_review(source, answer_v22(), build_evidence_packet(source), require_price_bias=True)
        original = copy.deepcopy(source["llm_review"])
        self.assertEqual(original["status"], "OK", original["integrated_trade_advisory"]["validation"])
        entry, detail = self.materialize(source)
        self.assertEqual(detail["llm_review"], original)
        projection = detail["signal_evidence_summary"]
        self.assertEqual(projection, entry["summary"]["signal_evidence_summary"])
        self.assertEqual(projection["advisory_guidance"], original["integrated_trade_advisory"]["advisory_guidance"])
        self.assertEqual(projection["source_boundary"], original["integrated_trade_advisory"]["source_boundary"])
        self.assertEqual(projection["source_record_hash"], source["producer_integrity"]["record_hash"])

    def test_old_event_fixed_changes_are_separate_read_projection(self):
        previous = base_card("PREVIOUS", price=99)
        current = base_card("CURRENT", ts_ms=previous["identity"]["confirmed_time_ms"] + 60000, price=100)
        previous["schema"]["record_type"] = "confirmed_signal_event_audit"
        current["schema"]["record_type"] = "fixed_analysis_round_audit"
        previous["integrity"] = copy.deepcopy(previous["producer_integrity"])
        current["integrity"] = copy.deepcopy(current["producer_integrity"])
        transition = materializer._transition_record(previous, current, [previous, current], None)
        old_packet = build_evidence_packet(current, previous, transition, packet_schema="signal_evidence_packet@2.0.0")
        current["llm_review"] = review.build_review(current, answer_v21(), old_packet, prompt_version="signal_llm_review_prompt@2.1.0")
        original = copy.deepcopy(current["llm_review"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            source.write_text("".join(json.dumps(c) + "\n" for c in (previous, current)), encoding="utf-8")
            materializer.materialize(source, root / "public", max_cards=0)
            manifest = json.loads((root / "public/signal_cards/index.json").read_text(encoding="utf-8"))
            entry = next(c for c in manifest["cards"] if c["card_id"] == "CURRENT")
            detail = json.loads((root / "public" / entry["path"]).read_text(encoding="utf-8"))
        self.assertEqual(detail["llm_review"], original)
        projection = detail["local_change_projection"]
        self.assertIn("未进入当时模型评审", projection["label_cn"])
        self.assertEqual(projection["assessment_hash"], original["integrated_trade_advisory"]["validation"]["assessment_hash"])
        self.assertTrue(any(f["id"] == "change.price.delta_pct" and f["usable"] for f in projection["facts"]))
        self.assertEqual(original["evidence_context"]["schema"], "signal_evidence_packet@2.0.0")


if __name__ == "__main__":
    unittest.main()
