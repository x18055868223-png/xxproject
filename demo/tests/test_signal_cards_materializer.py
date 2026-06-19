import importlib.util
import json
import pathlib
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOL_FILE = ROOT / "tools" / "materialize_signal_cards.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("materialize_signal_cards", TOOL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def record(card_id, confirmed_at, symbol="BTC", quality="OK",
           with_llm_review=True):
    item = {
        "schema": {"name": "signal_review_card", "version": "1.0.0",
                   "status": "FINAL"},
        "identity": {"card_id": card_id, "short_id": card_id[-4:],
                     "confirmed_at": confirmed_at, "symbol": symbol,
                     "is_synthetic": True},
        "quality": {"overall": quality},
        "market_context": {"price": 64000, "quote_currency": "USDT"},
        "decision": {"lean": "NEUTRAL", "support_label": "WAIT_CONFIRMATION"},
        "delivery": {"fmz_push_summary": "sample"},
    }
    if with_llm_review:
        item["llm_review"] = {
            "status": "OK",
            "summary_cn": "样例复核",
            "agreement_with_system": "部分支持",
            "main_supporting_factors": ["保留未知顶层字段"],
            "main_risks_or_conflicts": [],
            "operator_focus": [],
            "invalid_if": [],
            "caution_level": "MEDIUM",
            "not_trading_advice": True,
        }
    return item


def main():
    tool = load_tool()
    tool_source = TOOL_FILE.read_text(encoding="utf-8")
    assert_true("max_records" in tool_source and "deque(maxlen=max_records)" in tool_source,
                "materializer should stream JSONL into a bounded tail buffer")
    assert_true("_read_llm_reviews(llm_reviews, max_records=tail_limit)" in tool_source,
                "LLM sidecar should use the same bounded tail limit")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "signal_review.jsonl"
        reviews = root / "signal_llm_reviews.jsonl"
        output = root / "public"
        records = [
            record("20260618T160000+0800-BTC-A", "2026-06-18T16:00:00+08:00",
                   with_llm_review=False),
            record("20260618T160000+0800-BTC-A", "2026-06-18T16:00:00+08:00",
                   with_llm_review=False),
            record("20260618T160100+0800-BTC-B", "2026-06-18T16:01:00+08:00",
                   quality="DEGRADED"),
            record("20260618T160200+0800-BTC-C", "2026-06-18T16:02:00+08:00"),
        ]
        source.write_text("\n".join(json.dumps(item, ensure_ascii=False)
                                    for item in records) + "\n",
                          encoding="utf-8")
        reviews.write_text(json.dumps({
            "card_id": "20260618T160000+0800-BTC-A",
            "llm_review": {
                "status": "OK",
                "provider": "gemini",
                "summary_cn": "sidecar review",
                "agreement_with_system": "PARTIAL_SUPPORT",
                "main_supporting_factors": ["sidecar attached"],
                "main_risks_or_conflicts": [],
                "operator_focus": [],
                "invalid_if": [],
                "caution_level": "MEDIUM",
                "not_trading_advice": True,
            }
        }, ensure_ascii=False) + "\n" + json.dumps({
            "card_id": "20260618T160200+0800-BTC-C",
            "llm_review": {
                "status": "ERROR",
                "provider": "gemini",
                "summary_cn": "sidecar error",
                "agreement_with_system": "UNABLE_TO_JUDGE",
                "main_supporting_factors": [],
                "main_risks_or_conflicts": [],
                "operator_focus": [],
                "invalid_if": [],
                "caution_level": "HIGH",
                "not_trading_advice": True,
            }
        }, ensure_ascii=False) + "\n", encoding="utf-8")

        chmod_calls = []
        original_chmod = tool.os.chmod
        tool.os.chmod = lambda path, mode: chmod_calls.append(
            (pathlib.Path(path).name, mode))
        result = tool.materialize(source, output, max_cards=20,
                                  llm_reviews=reviews)
        tool.os.chmod = original_chmod

        manifest_path = output / "signal_cards" / "index.json"
        fallback_path = output / "signal_cards" / "fallback.js"
        assert_true(manifest_path.exists(), "manifest should be written")
        assert_true(fallback_path.exists(), "fallback.js should be written")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert_true(manifest["schema"]["name"] == "signal_cards_manifest",
                    "manifest schema name")
        assert_true(len(manifest["cards"]) == 3,
                    "manifest should dedupe repeated card_id records")
        assert_true(manifest["cards"][0]["card_id"].endswith("-C"),
                    "newest card should be first")
        assert_true(manifest["cards"][1]["quality"] == "DEGRADED",
                    "quality summary")
        assert_true(manifest["cards"][0]["path"].startswith("signal_cards/"),
                    "frontend relative path")
        for item in manifest["cards"]:
            card_path = output / item["path"]
            assert_true(card_path.exists(), "card json exists " + item["path"])
            saved = json.loads(card_path.read_text(encoding="utf-8"))
            assert_true(saved["identity"]["card_id"] == item["card_id"],
                        "card identity preserved")
            if item["card_id"].endswith("-A"):
                assert_true(saved["llm_review"]["provider"] == "gemini",
                            "sidecar llm_review should attach to base card")
            elif item["card_id"].endswith("-C"):
                assert_true(saved["llm_review"]["status"] == "OK",
                            "sidecar ERROR should not replace inline OK review")
            else:
                assert_true(saved["llm_review"]["status"] == "OK",
                            "inline llm_review top-level extension preserved")
        assert_true(result["merged_review_count"] == 1,
                    "only sidecar OK/base-card merge should count")
        fallback = fallback_path.read_text(encoding="utf-8")
        assert_true(fallback.startswith("window.SIGNAL_CARD_FIXTURES = "),
                    "fallback global")
        assert_true(result["written_cards"] == 3, "result written count")
        assert_true(result["skipped_lines"] == 0, "no skipped lines")
        chmod_names = {name for name, mode in chmod_calls if mode == 0o644}
        assert_true("index.json" in chmod_names, "manifest should be chmod 0644")
        assert_true("fallback.js" in chmod_names, "fallback should be chmod 0644")
        assert_true("20260618T160200+0800-BTC-C.json" in chmod_names,
                    "card json should be chmod 0644")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "signal_review.jsonl"
        output = root / "public"
        many_records = [
            record("20260618T{:06d}+0800-BTC-X".format(idx),
                   "2026-06-18T{:02d}:{:02d}:00+08:00".format(
                       idx // 60, idx % 60),
                   with_llm_review=False)
            for idx in range(750)
        ]
        source.write_text("\n".join(json.dumps(item, ensure_ascii=False)
                                    for item in many_records) + "\n",
                          encoding="utf-8")
        result = tool.materialize(source, output, max_cards=20)
        manifest = json.loads((output / "signal_cards" / "index.json")
                              .read_text(encoding="utf-8"))
        assert_true(result["written_cards"] == 20,
                    "materializer should publish only requested newest cards")
        assert_true(manifest["cards"][0]["card_id"].endswith("000749+0800-BTC-X"),
                    "streaming tail should preserve newest record")
        assert_true(manifest["cards"][-1]["card_id"].endswith("000730+0800-BTC-X"),
                    "streaming tail should keep only the newest window")
    print("signal_cards_materializer: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_cards_materializer: FAIL - " + str(exc))
        sys.exit(1)
