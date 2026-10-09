"""Bounded immutable input cache and display-only projections.

Qualification and accounting always run on original inputs before projection.
No clock, value, binding, or permission is synthesized here.
"""
from collections.abc import MutableMapping
import gzip
import io
import json


def gzip_json(value, *, compresslevel=1):
    """Serialize without materializing a second full JSON string/byte pair."""
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=compresslevel, mtime=0) as zipped:
        with io.TextIOWrapper(zipped, encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return buffer.getvalue()


class CompressedPoints(MutableMapping):
    def __init__(self, max_items=12, max_bytes=16 * 1024 * 1024):
        self.entries = {}
        self.max_items = max_items
        self.max_bytes = max_bytes

    @property
    def stored_bytes(self):
        return sum(len(value) for value in self.entries.values())

    def __getitem__(self, key):
        with gzip.GzipFile(fileobj=io.BytesIO(self.entries[key]), mode="rb") as zipped:
            return json.load(io.TextIOWrapper(zipped, encoding="utf-8"))

    def __setitem__(self, key, value):
        packed = gzip_json(value)
        if len(packed) > self.max_bytes:
            raise ValueError("lockable_input_size_budget_exceeded")
        self.entries[key] = packed
        while len(self.entries) > self.max_items or self.stored_bytes > self.max_bytes:
            del self.entries[next(iter(self.entries))]

    def __delitem__(self, key):
        del self.entries[key]

    def __iter__(self):
        return iter(self.entries)

    def __len__(self):
        return len(self.entries)

    def __contains__(self, key):
        return key in self.entries


def reading_value(value):
    """Omit source bodies, retain their identities, clocks and qualifications."""
    if isinstance(value, list):
        return [reading_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    source_record = "record_id" in value and "identity" in value and "content" in value
    result = {key: reading_value(item) for key, item in value.items()
              if not (source_record and key == "content") and key != "source_dependencies"}
    if source_record:
        result["content_deferred"] = True
    if "source_dependencies" in value:
        result["source_dependencies_deferred"] = True
    return result


def daily_summaries(values):
    return [{**{key: item.get(key) for key in ("schema", "snapshot_id", "recorded_ts_ms", "bjt_day", "space_gate")},
             "candidates": [{key: candidate.get(key) for key in ("side", "candidate_id", "snapshot_id", "review")}
                            for candidate in item.get("candidates", [])]}
            for item in values or []]


def workbench_reading(view):
    source = {key: value for key, value in view.items() if key not in {"btc_map_projection", "daily_snapshots"}}
    source["daily_snapshots"] = daily_summaries(view.get("daily_snapshots"))
    result = reading_value(source)
    result["display_projection"] = "workbench_reading@1.0.0"
    for candidate in result.get("candidates", []):
        identity = (candidate.get("snapshot") or {}).get("snapshot_id")
        if identity:
            candidate["evidence_url"] = "/api/workbench/candidate-evidence/" + identity
    return result
