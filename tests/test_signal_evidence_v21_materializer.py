import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import materialize_signal_cards as materializer
import signal_review_v2 as review_tool
from signal_evidence_v2 import build_evidence_packet
from signal_review_v2 import _browser_canonical_json
from test_signal_evidence_v2 import base_card
from test_signal_review_v2 import payload_with_bias, price_bias, side


def answer_v21():
    answer = payload_with_bias(
        side("B", ["structure.gamma.regime"], counter_refs=["pressure.tmv.direction"]),
        side("B", ["structure.gamma.regime"], counter_refs=["pressure.tmv.direction"]),
        price_bias("BULLISH", ["pressure.tmv.direction"]),
    )
    for key, item in answer["side_evidence_ratings"].items():
        item["mechanism_cn"] = "空间位置与价格压力共同解释约束，承接仍需观察。"
        item["evidence_roles"] = [
            {"ref": "structure.gamma.regime", "role": "supports_fit", "claim_cn": "空间结构提供缓冲背景。"},
            {"ref": "pressure.tmv.direction", "role": "counters_fit" if key == "call_credit" else "context_only", "claim_cn": "量价方向提供当前压力背景。"},
        ]
        item["strengthen_if_cn"] = ["不利方向推进受到约束时增强适配解释。"]
        item["weaken_if_cn"] = [item.pop("invalid_if_cn")]
        item.pop("evidence_refs")
        item.pop("counter_evidence_refs")
    answer["side_comparison"] = {
        "relative_side": "put_credit",
        "basis_cn": "两侧同级，但上行压力使下行侵入的竞争解释暂时较弱。",
        "evidence_refs": ["pressure.tmv.direction"],
        "flip_if_cn": "若出现有效下行推进，需要重新比较两侧。",
    }
    return answer


class MaterializerV21Tests(unittest.TestCase):
    def materialize(self, source):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "source.jsonl"
            path.write_text(json.dumps(source, ensure_ascii=False) + "\n", encoding="utf-8")
            materializer.materialize(path, root / "public", max_cards=0)
            manifest = json.loads((root / "public/signal_cards/index.json").read_text(encoding="utf-8"))
            entry = manifest["cards"][0]
            detail = json.loads((root / "public" / entry["path"]).read_text(encoding="utf-8"))
            return entry, detail

    def reviewed(self, answer=None):
        source = base_card()
        source["llm_review"] = review_tool.build_review(source, answer or answer_v21(), build_evidence_packet(source))
        return source

    def test_new_review_roundtrip_and_read_projection_binding(self):
        source = self.reviewed()
        before = copy.deepcopy(source["llm_review"])
        self.assertEqual(before["status"], "OK")
        entry, detail = self.materialize(source)
        self.assertEqual(detail["llm_review"], before)
        projection = detail["signal_evidence_summary"]
        self.assertEqual(entry["summary"]["signal_evidence_summary"], projection)
        self.assertEqual(projection["display_projection_version"], "2.1.0")
        self.assertEqual(projection["market_snapshot"]["price"], 100.0)
        self.assertEqual(projection["market_snapshot"]["unit"], "USDT")
        self.assertEqual(projection["side_comparison"]["relative_side"], "put_credit")
        unsigned = {k: v for k, v in projection.items() if k != "display_projection_hash"}
        digest = "sha256:" + hashlib.sha256(_browser_canonical_json(unsigned).encode("utf-8")).hexdigest()
        self.assertEqual(projection["display_projection_hash"], digest)

    def test_old_review_remains_exact_with_new_display_only(self):
        source = base_card()
        answer = payload_with_bias(side("B", ["structure.gamma.regime"]), side("B", ["structure.gamma.regime"]),
                                   price_bias("BULLISH", ["pressure.tmv.direction"]))
        source["llm_review"] = review_tool.build_review(source, answer, build_evidence_packet(source),
                                                       prompt_version="signal_llm_review_prompt@2.0.1")
        before = copy.deepcopy(source["llm_review"])
        _, detail = self.materialize(source)
        self.assertEqual(detail["llm_review"], before)
        self.assertEqual(before["schema_version"], "signal_llm_review@2.0.0")
        projection = detail["signal_evidence_summary"]
        self.assertEqual(projection["assessment_hash"], before["integrated_trade_advisory"]["validation"]["assessment_hash"])
        self.assertEqual(projection["side_comparison"]["status"], "UNAVAILABLE")
        self.assertIn("旧版", json.dumps(projection["side_comparison"], ensure_ascii=False))
        self.assertNotIn("C 级", projection["display_action_summary_cn"])

    def test_comparison_failure_does_not_erase_valid_sides(self):
        answer = answer_v21()
        answer["side_comparison"]["evidence_refs"] = ["made.up.reference"]
        source = self.reviewed(answer)
        _, detail = self.materialize(source)
        advisory = detail["llm_review"]["integrated_trade_advisory"]
        self.assertEqual(advisory["side_comparison"]["status"], "UNAVAILABLE")
        self.assertEqual([s["grade"] for s in advisory["side_evidence_ratings"].values()], ["B", "B"])
        self.assertEqual(advisory["price_bias"]["status"], "ASSESSED")

    def test_invalid_side_stays_local_and_cannot_win_comparison(self):
        answer = answer_v21()
        answer["side_evidence_ratings"]["call_credit"]["evidence_roles"][1]["role"] = "supports_fit"
        source = self.reviewed(answer)
        _, detail = self.materialize(source)
        advisory = detail["llm_review"]["integrated_trade_advisory"]
        self.assertEqual(advisory["side_evidence_ratings"]["put_credit"]["grade"], "B")
        self.assertEqual(advisory["side_evidence_ratings"]["call_credit"]["status"], "UNRATED")
        self.assertEqual(advisory["side_comparison"]["status"], "UNAVAILABLE")

    def test_later_legacy_sidecar_cannot_shadow_new_protocol(self):
        source = self.reviewed()
        older = copy.deepcopy(source["llm_review"])
        older["schema_version"] = "signal_llm_review@2.0.0"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reviews.jsonl"
            records = [{"card_id": source["identity"]["card_id"], "llm_review": review}
                       for review in [source["llm_review"], older]]
            path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
            reviews = materializer._read_llm_reviews(path)
        self.assertEqual(reviews[source["identity"]["card_id"]]["schema_version"], review_tool.OUTPUT_SCHEMA_VERSION)

    def test_intact_persisted_bad_comparison_is_isolated_after_source_check(self):
        for explanation in ("价格全程向下推进，所以本侧相对有利。", "当前 put_credit 更有依据。"):
            with self.subTest(explanation=explanation):
                source = self.reviewed()
                advisory = source["llm_review"]["integrated_trade_advisory"]
                advisory["side_comparison"]["basis_cn"] = explanation
                advisory["validation"]["assessment_hash"] = None
                advisory["validation"]["assessment_hash"] = review_tool._assessment_hash(advisory)
                original = copy.deepcopy(source["llm_review"])
                _, detail = self.materialize(source)
                result = detail["llm_review"]["integrated_trade_advisory"]
                self.assertEqual(detail["invalid_llm_review_archive"], original)
                self.assertEqual(result["side_evidence_ratings"], advisory["side_evidence_ratings"])
                self.assertEqual(result["price_bias"], advisory["price_bias"])
                self.assertEqual(result["local_action_state"], advisory["local_action_state"])
                self.assertEqual(result["side_comparison"]["status"], "UNAVAILABLE")
                self.assertNotIn(explanation, detail["signal_evidence_summary"]["side_comparison"]["basis_cn"])


if __name__ == "__main__":
    unittest.main()
