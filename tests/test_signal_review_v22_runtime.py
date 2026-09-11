"""Real protocol builders with a transport that must never be called on upgrade."""
import json
from pathlib import Path
import tempfile
import unittest
from test_signal_evidence_v21_materializer import answer_v21
from test_signal_evidence_v2 import base_card
import signal_review_v2 as review
import signal_review_v2_runtime as runtime
from signal_evidence_v2 import build_evidence_packet, packet_hash


class UpgradeBudgetTests(unittest.TestCase):
    def check_old_attempt(self, settled):
        card = base_card()
        old_packet = build_evidence_packet(card, packet_schema="signal_evidence_packet@2.0.0")
        old_prompt = "signal_llm_review_prompt@2.1.0"
        old_review = review.build_review(card, answer_v21(), old_packet, prompt_version=old_prompt)
        original_record = {"card_id": card["identity"]["card_id"], "llm_review": old_review}
        state = {"packet": old_packet, "packet_hash": packet_hash(old_packet), "prompt": old_prompt,
                 "mode": runtime.MODE, "model": review.DEFAULT_MODEL,
                 "attempts": [{"number": 1, "status": "OK" if settled else "RESERVED"}]}
        if settled:
            state.update(settled=True, record=original_record)
        with tempfile.TemporaryDirectory() as directory:
            states = Path(directory)
            path = states / (runtime._state_key(card["identity"]["card_id"], old_prompt, runtime.MODE, review.DEFAULT_MODEL) + ".json")
            original = json.dumps(state)
            path.write_text(original, encoding="utf-8")
            resolved = runtime._state_path(states, card["identity"]["card_id"], review.DEFAULT_MODEL)
            self.assertEqual(resolved, path)
            def no_http(*args, **kwargs):
                self.fail("Prompt upgrade must not create HTTP allowance")
            record, called = runtime._run_card(card, build_evidence_packet(card), resolved, None,
                                              review.DEFAULT_MODEL, 240, "unused", None, no_http, None)
            self.assertFalse(called)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertEqual(len(list(states.glob("*.json"))), 1)
            if settled:
                self.assertEqual(record, original_record)
            else:
                current_review = record["llm_review"]
                self.assertEqual(current_review["retry_budget"]["used"], 1)
                self.assertEqual(current_review["schema_version"], "signal_llm_review@2.2.0")
                self.assertEqual(current_review["evidence_context"]["schema"], "signal_evidence_packet@2.0.0")
                review.validate_persisted_review(current_review)

    def test_settled_v21_is_preserved(self):
        self.check_old_attempt(True)

    def test_unfinished_v21_stays_frozen(self):
        self.check_old_attempt(False)


if __name__ == "__main__":
    unittest.main()
