import importlib.util
import json
import pathlib
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL_FILE = ROOT / "tools" / "materialize_signal_cards.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("materialize_signal_cards", TOOL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def record(idx):
    return {
        "identity": {
            "card_id": "20260618T{:06d}+0800-BTC-X".format(idx),
            "confirmed_at": "2026-06-18T{:02d}:{:02d}:00+08:00".format(
                idx // 60, idx % 60),
            "symbol": "BTC",
        },
        "quality": {"overall": "OK"},
    }


def mixed_time_record(card_id, confirmed_at=None, confirmed_time_ms=None):
    identity = {
        "card_id": card_id,
        "symbol": "BTC",
    }
    if confirmed_at is not None:
        identity["confirmed_at"] = confirmed_at
    if confirmed_time_ms is not None:
        identity["confirmed_time_ms"] = confirmed_time_ms
    return {
        "identity": identity,
        "quality": {"overall": "OK"},
    }


def main():
    tool = load_tool()
    source_text = TOOL_FILE.read_text(encoding="utf-8")
    assert_true("deque(maxlen=max_records)" in source_text,
                "main JSONL should be read through a bounded deque")
    assert_true("_read_llm_reviews(llm_reviews, max_records=tail_limit)" in source_text,
                "LLM sidecar should use the same bounded tail limit")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "signal_review.jsonl"
        reviews = root / "signal_llm_reviews.jsonl"
        output = root / "public"
        source.write_text("\n".join(json.dumps(record(idx), ensure_ascii=False)
                                    for idx in range(750)) + "\n",
                          encoding="utf-8")
        reviews.write_text(json.dumps({
            "card_id": "20260618T000749+0800-BTC-X",
            "llm_review": {"status": "OK", "summary_cn": "tail review"},
        }, ensure_ascii=False) + "\n", encoding="utf-8")

        result = tool.materialize(source, output, max_cards=20,
                                  llm_reviews=reviews)
        manifest = json.loads((output / "signal_cards" / "index.json")
                              .read_text(encoding="utf-8"))
        assert_true("source" not in manifest,
                    "public manifest should not expose server/local JSONL source path")
        assert_true(result["written_cards"] == 20,
                    "should publish requested newest cards")
        assert_true(manifest["cards"][0]["card_id"].endswith("000749+0800-BTC-X"),
                    "newest card should survive bounded tail")
        newest = json.loads((output / manifest["cards"][0]["path"])
                            .read_text(encoding="utf-8"))
        assert_true(newest["llm_review"]["summary_cn"] == "tail review",
                    "tail sidecar review should merge")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "mixed_signal_review.jsonl"
        output = root / "public"
        records = [
            mixed_time_record("CARD-ISO", confirmed_at="2026-06-18T16:00:00+08:00"),
            mixed_time_record("CARD-MS", confirmed_time_ms=1781770200000),
        ]
        source.write_text("\n".join(json.dumps(item, ensure_ascii=False)
                                    for item in records) + "\n",
                          encoding="utf-8")
        tool.materialize(source, output, max_cards=20)
        manifest = json.loads((output / "signal_cards" / "index.json")
                              .read_text(encoding="utf-8"))
        assert_true([item["card_id"] for item in manifest["cards"]]
                    == ["CARD-MS", "CARD-ISO"],
                    "mixed numeric/ISO timestamps should sort newest first")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "prune_signal_review.jsonl"
        output = root / "public"
        cards_dir = output / "signal_cards"
        cards_dir.mkdir(parents=True)
        stale = cards_dir / "STALE.json"
        stale.write_text("{}", encoding="utf-8")
        source.write_text(json.dumps(mixed_time_record("CURRENT"),
                                     ensure_ascii=False) + "\n",
                          encoding="utf-8")
        tool.materialize(source, output, max_cards=20)
        assert_true(not stale.exists(),
                    "materializer should remove stale card JSON files outside the current manifest")

    print("materializer_tail_window: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("materializer_tail_window: FAIL - " + str(exc))
        sys.exit(1)
