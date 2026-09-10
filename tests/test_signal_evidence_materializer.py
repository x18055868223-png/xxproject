import copy
import json
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import materialize_signal_cards as materializer
from signal_evidence_v2 import build_evidence_packet
import signal_review_v2 as review_tool
from test_signal_review_v2 import card, side, payload, payload_with_bias, price_bias, AS_OF_MS
from test_signal_evidence_v2 import base_card, transition_for
import signal_evidence_v2 as facts_tool


def build_legacy_review(card, payload, packet, **kwargs):
    # These regressions freeze the 2.0 historical protocol. New 2.1 scenarios
    # live alongside them and never weaken the current model output contract.
    kwargs.setdefault("prompt_version", "signal_llm_review_prompt@2.0.1"
                      if kwargs.get("require_price_bias") or "price_bias" in payload
                      else "signal_llm_review_prompt@2.0.0")
    return review_tool.build_review(card, payload, packet, **kwargs)

class MaterializerV2Tests(unittest.TestCase):
    def source(self):
        value = card()
        value["identity"].update(confirmed_time_ms=AS_OF_MS,
                                  confirmed_at="2026-09-07T11:59:50+08:00",
                                  event_type="NR_REPAIR_CONFIRMED")
        value["market_context"] = {"price": 80000.0}
        return value

    def build(self):
        value = self.source()
        packet = build_evidence_packet(value)
        refs = ["market.price.current"]
        answer = payload(side("C", refs, basis_cn="现价可读，但约束证据尚无明显优势。"),
                         side("C", refs, basis_cn="现价可读，但约束证据尚无明显优势。"))
        value["llm_review"] = build_legacy_review(value, answer, packet)
        return value

    def legacy_review(self, status="OK"):
        return {
            "schema_version": "signal_llm_review@1.6.0",
            "status": status,
            "integrated_trade_advisory": {
                "summary_cn": "旧版行动评级结果，仅用于历史卡阅读。",
            },
        }

    def write_jsonl(self, path, records):
        path.write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n"
                    for record in records),
            encoding="utf-8",
        )

    def test_real_modules_roundtrip_and_summary_binding(self):
        value = self.build()
        self.assertEqual(value["llm_review"]["status"], "OK")
        original_review = copy.deepcopy(value["llm_review"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            source.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
            materializer.materialize(source, root / "public")
            manifest = json.loads((root / "public/signal_cards/index.json").read_text())
            entry = manifest["cards"][0]
            detail = json.loads((root / "public" / entry["path"]).read_text())
            self.assertEqual(detail["llm_review"], original_review)
            self.assertEqual(entry["summary"]["signal_evidence_summary"], detail["signal_evidence_summary"])
            self.assertNotIn("decision", entry["summary"])
            self.assertNotIn("signal_comfort_summary", entry["summary"])

    def test_price_bias_roundtrip_and_summary_binding(self):
        current = base_card("C", AS_OF_MS, 102)
        packet = build_evidence_packet(current)
        answer = payload_with_bias(
            side("C", ["market.price.current"]),
            side("B", ["pressure.tmv.direction", "response.flow_price.relation"]),
            price_bias(
                "BULLISH",
                ["pressure.tmv.direction", "response.flow_price.relation"],
                basis_cn="量价主干方向与主动流价格响应共同显示价格偏多。",
                counter_cn="主要反证是上方空间结构仍可能吸收推进。",
                invalid_if_cn="若主动流回落或价格响应转弱，需要重新判断方向。",
                counter_refs=["structure.gamma.regime"],
            ),
        )
        current["llm_review"] = build_legacy_review(current, answer, packet)
        original_review = copy.deepcopy(current["llm_review"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            source.write_text(
                json.dumps(current, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            materializer.materialize(source, root / "public", max_cards=0)
            manifest = json.loads((root / "public/signal_cards/index.json").read_text())
            entry = next(item for item in manifest["cards"]
                         if item["card_id"] == current["identity"]["card_id"])
            detail = json.loads((root / "public" / entry["path"]).read_text())
            self.assertEqual(detail["llm_review"], original_review)
            self.assertEqual(entry["summary"]["signal_evidence_summary"]["price_bias"]["bias"], "BULLISH")
            self.assertEqual(detail["signal_evidence_summary"]["price_bias"]["status"], "ASSESSED")

    def test_sidecar_reader_keeps_v2_when_legacy_is_appended_later(self):
        value = self.build()
        card_id = value["identity"]["card_id"]
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "reviews.jsonl"
            self.write_jsonl(sidecar, [
                {"card_id": card_id, "llm_review": value["llm_review"]},
                {"card_id": card_id, "llm_review": self.legacy_review()},
            ])
            reviews = materializer._read_llm_reviews(sidecar)
        self.assertEqual(
            reviews[card_id]["schema_version"],
            "signal_llm_review@2.0.0",
        )

    def test_sidecar_reader_replaces_legacy_with_v2_and_keeps_same_protocol_order(self):
        value = self.build()
        card_id = value["identity"]["card_id"]
        newer_v2 = copy.deepcopy(value["llm_review"])
        newer_v2["reviewed_at"] = "2026-09-07T12:10:00+08:00"
        with tempfile.TemporaryDirectory() as directory:
            sidecar = Path(directory) / "reviews.jsonl"
            self.write_jsonl(sidecar, [
                {"card_id": card_id, "llm_review": self.legacy_review()},
                {"card_id": card_id, "llm_review": value["llm_review"]},
                {"card_id": card_id, "llm_review": newer_v2},
            ])
            reviews = materializer._read_llm_reviews(sidecar)
        self.assertEqual(reviews[card_id]["schema_version"], "signal_llm_review@2.0.0")
        self.assertEqual(reviews[card_id]["reviewed_at"], newer_v2["reviewed_at"])

    def test_embedded_v2_is_not_downgraded_by_legacy_sidecar(self):
        value = self.build()
        original_review = copy.deepcopy(value["llm_review"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            sidecar = root / "reviews.jsonl"
            self.write_jsonl(source, [value])
            self.write_jsonl(sidecar, [{
                "card_id": value["identity"]["card_id"],
                "llm_review": self.legacy_review(),
            }])
            materializer.materialize(source, root / "public", llm_reviews=sidecar)
            manifest = json.loads((root / "public/signal_cards/index.json").read_text())
            detail = json.loads((root / "public" / manifest["cards"][0]["path"]).read_text())
        self.assertEqual(detail["llm_review"], original_review)
        self.assertIn("signal_evidence_summary", detail)
        self.assertNotIn("signal_comfort_summary", detail)

    def test_embedded_legacy_is_replaced_by_v2_sidecar(self):
        value = self.source()
        value["llm_review"] = self.legacy_review()
        v2_review = self.build()["llm_review"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            sidecar = root / "reviews.jsonl"
            self.write_jsonl(source, [value])
            self.write_jsonl(sidecar, [{
                "card_id": value["identity"]["card_id"],
                "llm_review": v2_review,
            }])
            materializer.materialize(source, root / "public", llm_reviews=sidecar)
            manifest = json.loads((root / "public/signal_cards/index.json").read_text())
            detail = json.loads((root / "public" / manifest["cards"][0]["path"]).read_text())
        self.assertEqual(detail["llm_review"], v2_review)
        self.assertEqual(detail["signal_evidence_summary"]["put_credit"]["grade"], "C")

    def test_same_protocol_error_sidecar_does_not_replace_embedded_ok(self):
        value = self.build()
        packet = build_evidence_packet(value)
        error_review = review_tool.build_error_review(
            value,
            packet,
            "测试错误不应覆盖已有有效复核。",
            require_price_bias=True,
            prompt_version="signal_llm_review_prompt@2.0.1",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            sidecar = root / "reviews.jsonl"
            self.write_jsonl(source, [value])
            self.write_jsonl(sidecar, [{
                "card_id": value["identity"]["card_id"],
                "llm_review": error_review,
            }])
            materializer.materialize(source, root / "public", llm_reviews=sidecar)
            manifest = json.loads((root / "public/signal_cards/index.json").read_text())
            detail = json.loads((root / "public" / manifest["cards"][0]["path"]).read_text())
        self.assertEqual(detail["llm_review"]["status"], "OK")
        self.assertNotEqual(detail["llm_review"], error_review)

    def test_unsupported_new_evidence_review_schema_fails_closed(self):
        for schema_version in ("signal_llm_review@2.2.0", "signal_llm_review@3.0.0"):
            with self.subTest(schema_version=schema_version):
                value = self.build()
                original_review = copy.deepcopy(value["llm_review"])
                original_review["schema_version"] = schema_version
                original_review["prompt_version"] = "signal_llm_review_prompt@9.9.9"
                value["llm_review"] = original_review
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    source = root / "source.jsonl"
                    self.write_jsonl(source, [value])
                    materializer.materialize(source, root / "public")
                    manifest = json.loads((root / "public/signal_cards/index.json").read_text())
                    detail = json.loads((root / "public" / manifest["cards"][0]["path"]).read_text())
                self.assertEqual(detail["invalid_llm_review_archive"], original_review)
                self.assertEqual(detail["llm_review"]["schema_version"], review_tool.OUTPUT_SCHEMA_VERSION)
                self.assertEqual(detail["llm_review"]["status"], "ERROR")
                self.assertEqual(
                    detail["llm_review"]["materializer_revalidation"],
                    "unsupported_review_schema",
                )
                user_visible = json.dumps(
                    {
                        "action_summary_cn": detail["llm_review"][
                            "integrated_trade_advisory"
                        ]["action_summary_cn"],
                        "validation_reasons_cn": detail["llm_review"][
                            "integrated_trade_advisory"
                        ]["validation"]["validation_reasons_cn"],
                    },
                    ensure_ascii=False,
                )
                self.assertNotIn("signal_llm_review@", user_visible)
                self.assertIn(
                    "这张卡的综合评审格式尚未受当前版本支持",
                    user_visible,
                )
                summary = detail["signal_evidence_summary"]
                self.assertIsNone(summary["put_credit"]["grade"])
                self.assertIsNone(summary["call_credit"]["grade"])
                self.assertEqual(summary["price_bias"]["status"], "UNAVAILABLE")

    def test_tampered_source_facts_are_unrated_not_low_grade(self):
        value = self.build()
        value["market_context"]["price"] = 90000
        materializer._validate_evidence_v2(value)
        self.assertEqual(value["llm_review"]["status"], "ERROR")
        self.assertIn("invalid_llm_review_archive", value)
        self.assertIsNone(review_tool.build_summary(value["llm_review"])["put_credit"]["grade"])

    def test_browser_numeric_canonicalization(self):
        numbers = [1.0, -0.0, 1e-7, 1e-6, 1e20, 1e21, 0.12345678, 80000.0]
        script = "const x=JSON.parse(process.argv[1]); process.stdout.write(JSON.stringify(x));"
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is needed for the browser numeric contract")
        result = subprocess.check_output([node, "-e", script,
                                          json.dumps(numbers)], text=True)
        self.assertEqual(review_tool._browser_canonical_json(numbers), result)

    def test_quote_boundary_words_do_not_delete_market_counter(self):
        value = self.source()
        packet = build_evidence_packet(value)
        counter = "上行压力仍是主要反证；候选行权价和净权利金尚未评估。"
        answer = payload(side("C", ["market.price.current"], counter_cn=counter),
                         side("C", ["market.price.current"]))
        review = build_legacy_review(value, answer, packet)
        self.assertEqual(review["status"], "OK")
        self.assertEqual(review["integrated_trade_advisory"]["side_evidence_ratings"]["put_credit"]["market_counter_cn"], counter)

    def test_wrong_previous_changes_invalidate_only_dependent_side(self):
        previous = base_card("P", AS_OF_MS - 600000, 100)
        current = base_card("C", AS_OF_MS, 102)
        wrong = base_card("WRONG", AS_OF_MS - 1200000, 95)
        wrong_packet = build_evidence_packet(current, wrong, transition_for(facts_tool, wrong, current))
        answer = payload_with_bias(
            side("B", ["change.price.delta_pct"]),
            side("C", ["market.price.current"]),
            price_bias(
                "BULLISH",
                ["change.price.delta_pct", "pressure.tmv.direction"],
                basis_cn="现价相对前卡变化与量价方向共同显示价格偏多。",
                counter_cn="主要反证是变化来源需要重新核验。",
                invalid_if_cn="若前后变化无法对应，需要撤回方向判断。",
            ),
        )
        current["llm_review"] = build_legacy_review(current, answer, wrong_packet)
        expected = build_evidence_packet(current, previous, transition_for(facts_tool, previous, current))
        materializer._validate_evidence_v2(current, expected)
        reviewed = current["llm_review"]
        self.assertEqual(reviewed["status"], "PARTIAL")
        summary = review_tool.build_summary(reviewed)
        self.assertIsNone(summary["put_credit"]["grade"])
        self.assertEqual(summary["call_credit"]["grade"], "C")
        self.assertEqual(summary["price_bias"]["status"], "UNAVAILABLE")
        self.assertIn("invalid_llm_review_archive", current)

    def test_cross_version_changes_are_not_used(self):
        previous = base_card("P", AS_OF_MS - 600000, 100)
        current = base_card("C", AS_OF_MS, 102)
        previous["identity"]["strategy_version"] = "old"
        packet = build_evidence_packet(current, previous, transition_for(facts_tool, previous, current))
        self.assertFalse(any(f["usable"] for f in packet["facts"] if f["topic"] == "change_context"))

    def test_all_original_no_trade_boundaries_keep_evidence_but_block_preparation(self):
        from test_signal_review_v2 import packet as review_packet
        for boundary in ("NO_TRADE", "NO_TRADE_BLOCKED", "SOFT_GATE", "UNABLE_TO_JUDGE"):
            value = card(support_label=boundary, decision_state=boundary)
            reviewed = build_legacy_review(value, payload(side("A", ["F_STRUCTURE"]), side("B", ["F_STRUCTURE"])), review_packet())
            adv = reviewed["integrated_trade_advisory"]
            self.assertEqual(adv["side_evidence_ratings"]["put_credit"]["grade"], "A")
            self.assertNotEqual(adv["local_action_state"]["put_credit"]["state"], "PREPARE")

    def test_shared_real_api_array_shape_error_is_recoverable(self):
        value = self.source(); packet = build_evidence_packet(value)
        a = side("C", ["market.price.current"]); a["unresolved_conditions_cn"] = "尚需观察"
        with self.assertRaises(review_tool.EvidenceFormatError):
            build_legacy_review(value, payload(a, copy.deepcopy(a)), packet)
        result = build_legacy_review(value, payload(a, side("C", ["market.price.current"])), packet)
        self.assertEqual(result["status"], "PARTIAL")

    def test_explicit_current_price_contradiction_is_side_local(self):
        value = self.source(); packet = build_evidence_packet(value)
        a = side("C", ["market.price.current"], basis_cn="当前价格为90000，仍在观察。")
        result = build_legacy_review(value, payload(a, side("C", ["market.price.current"])), packet)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertIsNone(result["integrated_trade_advisory"]["side_evidence_ratings"]["put_credit"]["grade"])

    def test_aggregate_return_cannot_prove_entire_price_path(self):
        value = self.source(); packet = build_evidence_packet(value)
        answer = payload(side("C", ["market.price.current"]),
                         side("C", ["market.price.current"], basis_cn="价格始终没有向上方推进。"))
        result = build_legacy_review(value, answer, packet)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertIsNone(result["integrated_trade_advisory"]["side_evidence_ratings"]["call_credit"]["grade"])
        self.assertIn("Call 侧暂未评级", result["integrated_trade_advisory"]["action_summary_cn"])
        answer["side_evidence_ratings"]["call_credit"]["basis_cn"] = "按4小时汇总价格变化，未显示上行推进。"
        self.assertEqual(build_legacy_review(value, answer, packet)["status"], "OK")
        answer["side_evidence_ratings"]["call_credit"]["basis_cn"] = "价格始终没有向上方推进，但不能证明未来继续。"
        self.assertEqual(build_legacy_review(value, answer, packet)["status"], "PARTIAL")
        answer["side_evidence_ratings"]["call_credit"]["basis_cn"] = "无法证明价格始终没有向上方推进。"
        self.assertEqual(build_legacy_review(value, answer, packet)["status"], "OK")
        exact_source = {"response.price.path_source": {"usable": True, "value": "精确价格点"}}
        self.assertTrue(review_tool._fact_assertion_issues(
            {"basis_cn": "价格全程没有向上方推进。"}, exact_source))

    def test_saved_path_overclaim_is_revalidated_without_losing_other_side(self):
        value = self.source(); packet = build_evidence_packet(value)
        answer = payload(side("C", ["market.price.current"]),
                         side("C", ["market.price.current"], basis_cn="价格始终没有向上方推进。"))
        with patch.object(review_tool, "_fact_assertion_issues", return_value=[]):
            original = build_legacy_review(value, answer, packet)
        value["llm_review"] = copy.deepcopy(original)
        materializer._validate_evidence_v2(value)
        self.assertEqual(value["llm_review"]["status"], "PARTIAL")
        self.assertEqual(value["invalid_llm_review_archive"], original)
        self.assertEqual(value["llm_review"]["materializer_revalidation"], "claim_scope_unavailable")
        self.assertEqual(review_tool.build_summary(value["llm_review"])["put_credit"]["grade"], "C")
        self.assertIsNone(review_tool.build_summary(value["llm_review"])["call_credit"]["grade"])
        review_tool.revalidate_review(value, value["llm_review"])

    def test_unavailable_price_bias_stays_unavailable_after_materializer_rebuild(self):
        value = self.source(); packet = build_evidence_packet(value)
        answer = payload_with_bias(
            side("C", ["market.price.current"]),
            side("C", ["market.price.current"], basis_cn="价格始终没有向上方推进。"),
            price_bias(
                "BULLISH",
                ["market.price.current"],
                basis_cn="现价在空间结构内，因此价格偏多。",
                counter_cn="主要反证是没有主动流或价格响应事实。",
                invalid_if_cn="若补齐压力响应事实，需要重新判断方向。",
            ),
        )
        with patch.object(review_tool, "_fact_assertion_issues", return_value=[]):
            original = build_legacy_review(value, answer, packet)
        original_reasons = original["integrated_trade_advisory"]["price_bias"]["validation_reasons_cn"]
        self.assertIn("价格方向不能只由墙位、距离或净Gamma符号构成", original_reasons[0])
        value["llm_review"] = copy.deepcopy(original)
        materializer._validate_evidence_v2(value)
        summary = review_tool.build_summary(value["llm_review"])
        self.assertIsNone(summary["call_credit"]["grade"])
        self.assertEqual(summary["price_bias"]["status"], "UNAVAILABLE")
        self.assertEqual(summary["price_bias"]["bias"], "UNDETERMINED")
        final_bias = value["llm_review"]["integrated_trade_advisory"]["price_bias"]
        self.assertEqual(final_bias["validation_reasons_cn"], original_reasons)
        review_tool.revalidate_review(value, value["llm_review"])

    def test_change_rebuild_preserves_unavailable_price_bias_reason(self):
        previous = base_card("P", AS_OF_MS - 600000, 100)
        current = base_card("C", AS_OF_MS, 102)
        wrong = base_card("WRONG", AS_OF_MS - 1200000, 95)
        wrong_packet = build_evidence_packet(current, wrong, transition_for(facts_tool, wrong, current))
        answer = payload_with_bias(
            side("B", ["change.price.delta_pct"]),
            side("C", ["market.price.current"]),
            price_bias(
                "BULLISH",
                ["structure.gamma.regime"],
                basis_cn="现价仍在空间结构内，因此价格偏多。",
                counter_cn="主要反证是墙位距离本身不能证明方向。",
                invalid_if_cn="若空间结构迁移，需要重新判断。",
            ),
        )
        current["llm_review"] = build_legacy_review(current, answer, wrong_packet)
        original_reasons = current["llm_review"]["integrated_trade_advisory"]["price_bias"]["validation_reasons_cn"]
        self.assertIn("价格方向不能只由墙位、距离或净Gamma符号构成", original_reasons[0])
        expected = build_evidence_packet(current, previous, transition_for(facts_tool, previous, current))
        materializer._validate_evidence_v2(current, expected)
        reviewed = current["llm_review"]
        final_bias = reviewed["integrated_trade_advisory"]["price_bias"]
        self.assertEqual(final_bias["status"], "UNAVAILABLE")
        self.assertEqual(final_bias["validation_reasons_cn"], original_reasons)
        self.assertEqual(review_tool.build_summary(reviewed)["price_bias"]["status"], "UNAVAILABLE")
        review_tool.revalidate_review(current, reviewed)

    def test_unavailable_direction_with_lost_change_refs_does_not_invalidate_sides(self):
        for field in ("evidence_refs", "counter_evidence_refs"):
            with self.subTest(field=field):
                previous = base_card("P", AS_OF_MS - 600000, 100)
                current = base_card("C", AS_OF_MS, 102)
                wrong = base_card("WRONG", AS_OF_MS - 1200000, 95)
                wrong_packet = build_evidence_packet(current, wrong, transition_for(facts_tool, wrong, current))
                bias = price_bias("BEARISH", ["pressure.tmv.direction"],
                                  basis_cn="价格始终没有向上方推进，因此价格偏空。")
                bias[field] = ["change.price.delta_pct"]
                answer = payload_with_bias(side("C", ["market.price.current"]),
                                           side("C", ["market.price.current"]), bias)
                original = build_legacy_review(current, answer, wrong_packet)
                original_bias = original["integrated_trade_advisory"]["price_bias"]
                self.assertEqual(original_bias["status"], "UNAVAILABLE")
                self.assertEqual(original_bias[field], ["change.price.delta_pct"])
                current["llm_review"] = copy.deepcopy(original)
                expected = build_evidence_packet(current, previous, transition_for(facts_tool, previous, current))
                materializer._validate_evidence_v2(current, expected)
                reviewed = current["llm_review"]
                self.assertEqual(reviewed["status"], "PARTIAL")
                summary = review_tool.build_summary(reviewed)
                self.assertEqual(summary["put_credit"]["grade"], "C")
                self.assertEqual(summary["call_credit"]["grade"], "C")
                final_bias = reviewed["integrated_trade_advisory"]["price_bias"]
                self.assertEqual(final_bias["status"], "UNAVAILABLE")
                self.assertEqual(final_bias[field], [])
                for reason in original_bias["validation_reasons_cn"]:
                    self.assertIn(reason, final_bias["validation_reasons_cn"])
                self.assertIn("原方向引用的部分事实已不可核验，仅保留可用引用。", final_bias["validation_reasons_cn"])
                self.assertEqual(current["invalid_llm_review_archive"], original)
                review_tool.revalidate_review(current, reviewed)


if __name__ == "__main__":
    unittest.main()
