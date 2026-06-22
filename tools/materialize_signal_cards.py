#!/usr/bin/env python3
"""Materialize signal_review.jsonl into the finalized static frontend layout.

Output layout:
  <output>/signal_cards/index.json
  <output>/signal_cards/<card_id>.json
  <output>/signal_cards/fallback.js
"""

import argparse
from collections import deque
import datetime as _dt
import json
import os
from pathlib import Path
import re
import tempfile


DEFAULT_FMZ_JSONL = "/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl"
MANIFEST_SCHEMA = {
    "name": "signal_cards_manifest",
    "version": "1.0.0",
    "card_schema": "signal_review_card@1.0.0",
}


def materialize(source, output, max_cards=200, llm_reviews=None):
    source = Path(source)
    output = Path(output)
    cards_dir = output / "signal_cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    _chmod_public_dir(output)
    _chmod_public_dir(cards_dir)

    tail_limit = _read_tail_limit(max_cards)
    records, skipped = _read_jsonl(source, max_records=tail_limit)
    records = _dedupe_by_card_id(records)
    review_map = _read_llm_reviews(llm_reviews, max_records=tail_limit)
    merged_review_count = 0
    if review_map:
        for record in records:
            card_id = _identity(record).get("card_id") or record.get("card_id")
            review = review_map.get(card_id)
            if review:
                existing = record.get("llm_review")
                if (_review_status(existing) == "OK"
                        and _review_status(review) != "OK"):
                    continue
                record["llm_review"] = review
                merged_review_count += 1
    records = sorted(records, key=_sort_key, reverse=True)
    if max_cards and max_cards > 0:
        records = records[:max_cards]

    manifest_cards = []
    expected_card_files = set()
    for record in records:
        identity = _identity(record)
        card_id = identity.get("card_id") or record.get("card_id")
        filename = _filename_for_card(card_id)
        expected_card_files.add(filename)
        rel_path = "signal_cards/" + filename
        _write_json(cards_dir / filename, record)
        manifest_cards.append({
            "card_id": card_id,
            "confirmed_at": identity.get("confirmed_at") or record.get("created_at"),
            "symbol": identity.get("symbol") or record.get("symbol"),
            "quality": _quality(record),
            "path": rel_path,
        })

    manifest = {
        "schema": dict(MANIFEST_SCHEMA),
        "generated_at": _now_iso(),
        "cards": manifest_cards,
    }
    _prune_stale_card_json(cards_dir, expected_card_files)
    _write_json(cards_dir / "index.json", manifest)
    _write_fallback(cards_dir / "fallback.js", records)
    return {
        "source": str(source),
        "output": str(output),
        "written_cards": len(records),
        "skipped_lines": skipped,
        "manifest": str(cards_dir / "index.json"),
        "fallback": str(cards_dir / "fallback.js"),
        "llm_reviews": str(llm_reviews) if llm_reviews else "",
        "merged_review_count": merged_review_count,
    }


def _read_tail_limit(max_cards):
    if not max_cards or max_cards <= 0:
        return None
    return max(500, max_cards * 5)


def _read_jsonl(source, max_records=None, require_identity=True):
    if max_records and max_records > 0:
        records = deque(maxlen=max_records)
    else:
        records = []
    skipped = 0
    if not source.exists():
        return [], skipped
    with source.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if (isinstance(value, dict)
                    and (not require_identity
                         or _identity(value).get("card_id"))):
                records.append(value)
            else:
                skipped += 1
    return list(records), skipped


def _dedupe_by_card_id(records):
    by_id = {}
    for record in records:
        by_id[_identity(record).get("card_id")] = record
    return list(by_id.values())


def _read_llm_reviews(path, max_records=None):
    if not path:
        return {}
    path = Path(path)
    if not path.exists():
        return {}
    reviews = {}
    records, _skipped = _read_jsonl(path, max_records=max_records,
                                    require_identity=False)
    for value in records:
        card_id = value.get("card_id") or _identity(value).get("card_id")
        review = value.get("llm_review")
        if card_id and isinstance(review, dict):
            reviews[card_id] = review
    return reviews


def _review_status(review):
    if not isinstance(review, dict):
        return ""
    return str(review.get("status") or "").upper()


def _identity(record):
    identity = record.get("identity")
    return identity if isinstance(identity, dict) else {}


def _quality(record):
    quality = record.get("quality")
    if isinstance(quality, dict):
        return quality.get("overall")
    return quality


def _sort_key(record):
    identity = _identity(record)
    ms = identity.get("confirmed_time_ms") or record.get("confirmed_time_ms")
    timestamp = _timestamp_sort_value(ms)
    if timestamp == 0.0:
        timestamp = _timestamp_sort_value(
            identity.get("confirmed_at") or record.get("created_at"))
    return (timestamp, identity.get("card_id") or "")


def _timestamp_sort_value(value):
    if isinstance(value, bool) or value in ("", None):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = _dt.datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_dt.timezone.utc)
            return parsed.timestamp() * 1000.0
        except ValueError:
            return 0.0
    return 0.0


def _filename_for_card(card_id):
    safe = re.sub(r"[^A-Za-z0-9_.+@=-]+", "_", str(card_id or "").strip())
    safe = safe.strip("._")
    if not safe:
        safe = "card"
    return safe + ".json"


def _prune_stale_card_json(cards_dir, expected_card_files):
    expected = set(expected_card_files)
    for path in cards_dir.glob("*.json"):
        if path.name == "index.json" or path.name in expected:
            continue
        path.unlink()


def _write_json(path, payload):
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    _atomic_write_text(path, text + "\n")


def _write_fallback(path, records):
    text = ("window.SIGNAL_CARD_FIXTURES = "
            + json.dumps(records, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
            + ";\n")
    _atomic_write_text(path, text)


def _atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _chmod_public_dir(path.parent)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                                    dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
        os.chmod(path, 0o644)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _chmod_public_dir(path):
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass


def _now_iso():
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build signal_cards/ data for the finalized signal audit page.")
    parser.add_argument("--source", default=DEFAULT_FMZ_JSONL,
                        help="Path to FMZ signal_review.jsonl.")
    parser.add_argument("--output", required=True,
                        help="Static frontend root containing index.html/app.js.")
    parser.add_argument("--max-cards", type=int, default=200,
                        help="Maximum newest cards to publish; <=0 publishes all.")
    parser.add_argument("--llm-reviews", default="",
                        help="Optional sidecar JSONL generated by gemini_signal_llm_review.py.")
    args = parser.parse_args(argv)
    result = materialize(args.source, args.output, args.max_cards,
                         llm_reviews=args.llm_reviews)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
