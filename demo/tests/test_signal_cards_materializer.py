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


def record(card_id, confirmed_at, symbol="BTC", quality="OK"):
    return {
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


def main():
    tool = load_tool()
    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "signal_review.jsonl"
        output = root / "public"
        records = [
            record("20260618T160000+0800-BTC-A", "2026-06-18T16:00:00+08:00"),
            record("20260618T160100+0800-BTC-B", "2026-06-18T16:01:00+08:00",
                   quality="DEGRADED"),
        ]
        source.write_text("\n".join(json.dumps(item, ensure_ascii=False)
                                    for item in records) + "\n",
                          encoding="utf-8")

        chmod_calls = []
        original_chmod = tool.os.chmod
        tool.os.chmod = lambda path, mode: chmod_calls.append(
            (pathlib.Path(path).name, mode))
        result = tool.materialize(source, output, max_cards=20)
        tool.os.chmod = original_chmod

        manifest_path = output / "signal_cards" / "index.json"
        fallback_path = output / "signal_cards" / "fallback.js"
        assert_true(manifest_path.exists(), "manifest should be written")
        assert_true(fallback_path.exists(), "fallback.js should be written")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert_true(manifest["schema"]["name"] == "signal_cards_manifest",
                    "manifest schema name")
        assert_true(len(manifest["cards"]) == 2, "manifest card count")
        assert_true(manifest["cards"][0]["card_id"].endswith("-B"),
                    "newest card should be first")
        assert_true(manifest["cards"][0]["quality"] == "DEGRADED",
                    "quality summary")
        assert_true(manifest["cards"][0]["path"].startswith("signal_cards/"),
                    "frontend relative path")
        for item in manifest["cards"]:
            card_path = output / item["path"]
            assert_true(card_path.exists(), "card json exists " + item["path"])
            saved = json.loads(card_path.read_text(encoding="utf-8"))
            assert_true(saved["identity"]["card_id"] == item["card_id"],
                        "card identity preserved")
        fallback = fallback_path.read_text(encoding="utf-8")
        assert_true(fallback.startswith("window.SIGNAL_CARD_FIXTURES = "),
                    "fallback global")
        assert_true(result["written_cards"] == 2, "result written count")
        assert_true(result["skipped_lines"] == 0, "no skipped lines")
        chmod_names = {name for name, mode in chmod_calls if mode == 0o644}
        assert_true("index.json" in chmod_names, "manifest should be chmod 0644")
        assert_true("fallback.js" in chmod_names, "fallback should be chmod 0644")
        assert_true("20260618T160100+0800-BTC-B.json" in chmod_names,
                    "card json should be chmod 0644")
    print("signal_cards_materializer: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_cards_materializer: FAIL - " + str(exc))
        sys.exit(1)
