import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import signal_llm_review as core
import signal_review_v2_runtime as runtime


class RuntimeTests(unittest.TestCase):
    def test_v21_recovers_settled_v201_state_without_http(self):
        states = self.output.with_suffix(self.output.suffix + ".v2_attempts")
        states.mkdir()
        prompt = "signal_llm_review_prompt@2.0.1"
        key = runtime._state_key("case-1", prompt, runtime.MODE, core.DEFAULT_MODEL)
        record = {"card_id": "case-1", "llm_review": {
            "schema_version": "signal_llm_review@2.0.0", "prompt_version": prompt,
            "status": "OK", "retry_budget": {"persistent": True, "used": 1, "limit": 2}}}
        state = {"packet": self.packet, "prompt": prompt, "attempts": [{"number": 1}],
                 "settled": True, "record": record}
        original = json.dumps(state)
        path = states / (key + ".json")
        path.write_text(original, encoding="utf-8")
        with patch.object(runtime, "PROMPT", "signal_llm_review_prompt@2.1.0"), patch.object(
                runtime, "ACCEPTED_PROMPT_VERSIONS", ("signal_llm_review_prompt@2.0.0", prompt,
                                                     "signal_llm_review_prompt@2.1.0")):
            self.run_review()
        self.assertEqual(self.calls, [])
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8")), record)

    def test_v21_freezes_unfinished_v201_attempt_without_new_allowance(self):
        states = self.output.with_suffix(self.output.suffix + ".v2_attempts")
        states.mkdir()
        prompt = "signal_llm_review_prompt@2.0.1"
        path = states / (runtime._state_key("case-1", prompt, runtime.MODE, core.DEFAULT_MODEL) + ".json")
        state = {"packet": self.packet, "prompt": prompt,
                 "attempts": [{"number": 1, "status": "RESERVED"}]}
        original = json.dumps(state)
        path.write_text(original, encoding="utf-8")
        with patch.object(runtime, "PROMPT", "signal_llm_review_prompt@2.1.0"), patch.object(
                runtime, "ACCEPTED_PROMPT_VERSIONS", (prompt, "signal_llm_review_prompt@2.1.0")):
            self.run_review()
        self.assertEqual(self.calls, [])
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        review = json.loads(self.output.read_text(encoding="utf-8"))["llm_review"]
        self.assertEqual(review["retry_budget"]["used"], 1)
        self.assertEqual(len(list(states.glob("*.json"))), 1)

    def test_multiple_prompt_states_fail_closed_without_http(self):
        states = self.output.with_suffix(self.output.suffix + ".v2_attempts")
        states.mkdir()
        prompts = ("signal_llm_review_prompt@2.0.1", "signal_llm_review_prompt@2.1.0")
        for prompt in prompts:
            path = states / (runtime._state_key("case-1", prompt, runtime.MODE, core.DEFAULT_MODEL) + ".json")
            path.write_text(json.dumps({"packet": self.packet, "prompt": prompt,
                                        "attempts": [{"number": 1}]}), encoding="utf-8")
        with patch.object(runtime, "PROMPT", prompts[-1]), patch.object(runtime, "ACCEPTED_PROMPT_VERSIONS", prompts):
            self.run_review()
        self.assertEqual(self.calls, [])
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8"))["llm_review"]["status"], "ERROR")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.jsonl"
        self.output = self.root / "reviews.jsonl"
        self.source.write_text(json.dumps({"identity": {"card_id": "case-1"}}), encoding="utf-8")
        self.packet = {"schema": "signal_evidence_packet@2.0.0", "identity": {"card_id": "case-1"}, "facts": []}
        self.calls = []
        self.budget = core.DailyHttpBudget(self.root / "budget.json", limit=60)
        for name, value in (
            ("build_evidence_packet", self.packet),
            ("build_request", {"messages": [{"role": "user", "content": "facts"}]}),
            ("build_review", {"schema_version": "signal_llm_review@2.0.0", "status": "OK"}),
            ("build_error_review", {"schema_version": "signal_llm_review@2.0.0", "status": "ERROR"}),
        ):
            p = patch.object(runtime, name, side_effect=lambda *a, _v=value, **k: json.loads(json.dumps(_v)))
            p.start()
            self.addCleanup(p.stop)

    def run_review(self, transport=None):
        def normal(*args, **kwargs):
            self.calls.append(1)
            return {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 8}}
        return runtime.generate_reviews(self.source, self.output, api_key="test-only",
                                        budget=self.budget, transport=transport or normal,
                                        only_card_id="case-1")

    def write_source_cards(self, *card_ids):
        records = []
        for index, card_id in enumerate(card_ids):
            records.append({
                "identity": {
                    "card_id": card_id,
                    "confirmed_time_ms": 1000 + index,
                },
            })
        self.source.write_text(
            "\n".join(json.dumps(record) for record in records) + "\n",
            encoding="utf-8",
        )

    def write_exclusions(self, card_ids, *, path=None):
        path = path or self.root / "automatic_exclusions.json"
        path.write_text(json.dumps({
            "schema_version": runtime.AUTOMATIC_EXCLUSIONS_SCHEMA_VERSION,
            "card_ids": list(card_ids),
            "metadata": {"source": "test"},
        }), encoding="utf-8")
        return path

    def transport(self, *args, **kwargs):
        self.calls.append(1)
        return {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8}}

    def test_one_call_and_restarting_does_not_repeat(self):
        self.assertEqual(self.run_review()["written"], 1)
        self.assertEqual(self.run_review()["skipped"], 1)
        self.assertEqual(len(self.calls), 1)
        review = json.loads(self.output.read_text())["llm_review"]
        self.assertEqual(review["llm_http_calls"], 1)
        self.assertEqual(review["call_audit"][0]["usage"]["prompt_tokens"], 12)
        self.assertEqual(runtime.PROMPT, runtime.PROMPT_VERSION)

    def test_runtime_requires_price_bias_without_extra_retry(self):
        with patch.object(runtime, "build_review") as mocked:
            mocked.return_value = {"schema_version": "signal_llm_review@2.0.0",
                                   "status": "PARTIAL"}
            self.assertEqual(self.run_review()["errors"], 0)
            self.run_review()
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(mocked.call_args.kwargs["require_price_bias"])

    def test_transport_and_format_share_two_attempts(self):
        def broken(*args, **kwargs):
            self.calls.append(1)
            if len(self.calls) == 1:
                raise ConnectionResetError("reset")
            return {"choices": [{"message": {"content": "{"}, "finish_reason": "stop"}]}
        self.assertEqual(self.run_review(broken)["errors"], 1)
        self.run_review(broken)
        self.assertEqual(len(self.calls), 2)
        review = json.loads(self.output.read_text())["llm_review"]
        self.assertEqual(review["retry_budget"]["used"], 2)
        self.assertEqual(len(review["call_audit"]), 2)

    def test_empty_recovers_once(self):
        def answer(*args, **kwargs):
            self.calls.append(1)
            return {"choices": [{"message": {"content": "" if len(self.calls) == 1 else "{}"}}]}
        self.assertEqual(self.run_review(answer)["errors"], 0)
        self.assertEqual(len(self.calls), 2)

    def test_auth_failure_is_terminal(self):
        def auth(*args, **kwargs):
            self.calls.append(1)
            raise core.LlmApiError(401, "secret must not be persisted")
        self.run_review(auth)
        self.run_review(auth)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("secret must", self.output.read_text())

    def test_semantic_side_error_is_final(self):
        with patch.object(runtime, "build_review", return_value={"schema_version": "signal_llm_review@2.0.0", "status": "PARTIAL"}):
            self.run_review()
            self.run_review()
        self.assertEqual(len(self.calls), 1)

    def test_deleted_sidecar_reuses_durable_completed_record(self):
        self.run_review()
        self.output.unlink()
        self.run_review()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(json.loads(self.output.read_text())["llm_review"]["status"], "OK")

    def test_new_facts_do_not_reset_a_frozen_assessment(self):
        self.run_review()
        self.output.unlink()
        changed = {**self.packet, "facts": [{"id": "later-arriving-fact"}]}
        with patch.object(runtime, "build_evidence_packet", return_value=changed):
            self.run_review()
        self.assertEqual(len(self.calls), 1)
        state = json.loads(next(self.root.glob("reviews.jsonl.v2_attempts/*.json")).read_text())
        self.assertEqual(state["packet"], self.packet)
        self.assertEqual(state["packet_hash"], runtime.packet_hash(self.packet))

    def test_successful_legacy_sidecar_is_not_automatically_reassessed(self):
        old = {"card_id": "case-1", "llm_review": {
            "schema_version": "signal_llm_review@1.6.0", "status": "OK"}}
        self.output.write_text(json.dumps(old) + "\n", encoding="utf-8")
        result = self.run_review()
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(len(self.calls), 0)
        self.assertEqual(json.loads(self.output.read_text()), old)

    def test_deleted_sidecar_reuses_legacy_prompt_state(self):
        states = self.output.with_suffix(self.output.suffix + ".v2_attempts")
        states.mkdir(parents=True, exist_ok=True)
        key = runtime._state_key("case-1", runtime.LEGACY_PROMPT_VERSION,
                                 runtime.MODE, core.DEFAULT_MODEL)
        legacy_record = {"card_id": "case-1", "llm_review": {
            "schema_version": "signal_llm_review@2.0.0",
            "prompt_version": runtime.LEGACY_PROMPT_VERSION,
            "status": "OK",
        }}
        (states / (key + ".json")).write_text(json.dumps({
            "schema": "signal_review_attempts@2.0.0",
            "packet": self.packet,
            "packet_hash": "sha256:old",
            "prompt": runtime.LEGACY_PROMPT_VERSION,
            "mode": runtime.MODE,
            "model": core.DEFAULT_MODEL,
            "attempts": [{"number": 1, "status": "OK"}],
            "settled": True,
            "record": legacy_record,
        }), encoding="utf-8")
        result = self.run_review()
        self.assertEqual(result["written"], 1)
        self.assertEqual(len(self.calls), 0)
        self.assertEqual(json.loads(self.output.read_text()), legacy_record)

    def test_daily_budget_no_http_no_card_attempt_consumed(self):
        self.budget = core.DailyHttpBudget(self.root / "budget.json", limit=1)
        self.budget.reserve(1, role="other", packet_hash="x", provider="test", model="test")
        self.run_review()
        self.assertEqual(len(self.calls), 0)
        state = json.loads(next(self.root.glob("reviews.jsonl.v2_attempts/*.json")).read_text())
        self.assertEqual(state["attempts"], [])
        self.assertFalse(state.get("settled"))
        self.run_review()
        self.assertFalse(self.output.exists(), "daily deferral must not duplicate error sidecars every timer round")

    def test_corrupt_attempt_ledger_fails_closed(self):
        self.run_review()
        self.output.unlink()
        state = next(self.root.glob("reviews.jsonl.v2_attempts/*.json"))
        state.write_text("broken", encoding="utf-8")
        self.assertEqual(self.run_review()["errors"], 1)
        self.assertEqual(len(self.calls), 1)

    def test_automatic_exclusion_skips_history_and_reviews_next_card(self):
        self.write_source_cards("case-new", "case-old")
        exclusions = self.write_exclusions(["case-old"])
        result = runtime.generate_reviews(
            self.source,
            self.output,
            api_key="test-only",
            budget=self.budget,
            transport=self.transport,
            limit=1,
            automatic_exclusions=exclusions,
        )
        self.assertEqual(result["automatic_excluded"], 1)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["written"], 1)
        self.assertEqual(result["attempted"], 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(json.loads(self.output.read_text())["card_id"], "case-new")

    def test_automatic_exclusion_persists_across_restart_without_sidecar_or_http(self):
        exclusions = self.write_exclusions(["case-1"])
        first = runtime.generate_reviews(
            self.source,
            self.output,
            api_key="test-only",
            budget=self.budget,
            transport=self.transport,
            automatic_exclusions=exclusions,
        )
        second = runtime.generate_reviews(
            self.source,
            self.output,
            api_key="test-only",
            budget=self.budget,
            transport=self.transport,
            automatic_exclusions=exclusions,
        )
        self.assertEqual(first["automatic_excluded"], 1)
        self.assertEqual(second["automatic_excluded"], 1)
        self.assertEqual(first["written"], 0)
        self.assertEqual(second["written"], 0)
        self.assertEqual(len(self.calls), 0)
        self.assertFalse(self.output.exists())

    def test_only_card_id_bypasses_automatic_exclusion(self):
        exclusions = self.write_exclusions(["case-1"])
        result = runtime.generate_reviews(
            self.source,
            self.output,
            api_key="test-only",
            budget=self.budget,
            transport=self.transport,
            only_card_id="case-1",
            automatic_exclusions=exclusions,
        )
        self.assertEqual(result["automatic_excluded"], 0)
        self.assertEqual(result["written"], 1)
        self.assertEqual(result["attempted"], 1)
        self.assertEqual(result["target"]["status"], "OK")
        self.assertEqual(len(self.calls), 1)

    def test_invalid_automatic_exclusions_fail_before_http(self):
        cases = {
            "missing": self.root / "missing.json",
            "broken_json": self.root / "broken.json",
            "wrong_schema": self.root / "wrong_schema.json",
            "non_string_id": self.root / "non_string_id.json",
        }
        cases["broken_json"].write_text("{", encoding="utf-8")
        cases["wrong_schema"].write_text(json.dumps({
            "schema_version": "signal_review_automatic_exclusions@9.9.9",
            "card_ids": ["case-1"],
        }), encoding="utf-8")
        cases["non_string_id"].write_text(json.dumps({
            "schema_version": runtime.AUTOMATIC_EXCLUSIONS_SCHEMA_VERSION,
            "card_ids": ["case-1", 2],
        }), encoding="utf-8")
        for name, path in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    runtime.generate_reviews(
                        self.source,
                        self.output,
                        api_key="test-only",
                        budget=self.budget,
                        transport=self.transport,
                        automatic_exclusions=path,
                    )
        self.assertEqual(len(self.calls), 0)
        self.assertFalse(self.output.exists())

    def test_interrupted_reservations_still_count_after_restart(self):
        self.run_review()
        self.output.unlink()
        path = next(self.root.glob("reviews.jsonl.v2_attempts/*.json"))
        state = json.loads(path.read_text())
        state.pop("record")
        state["settled"] = False
        state["attempts"] = [{"number": 1, "status": "RESERVED"},
                             {"number": 2, "status": "RESERVED"}]
        path.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.run_review()["errors"], 1)
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
