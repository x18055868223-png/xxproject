"""Append-only local store for Astra v2.0 underwriting snapshots.

The store is deliberately outside FMZ execution. It only freezes local
read-only candidate snapshots, deduplicates repeated entry origins, and binds
optional already-produced reviews to the exact snapshot they reviewed.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Mapping, Sequence

if __package__:
    from . import astra_underwriting_v20 as core
else:  # direct execution/import from tools/
    import astra_underwriting_v20 as core


EVENT_SCHEMA = "astra_underwriting_store_event@2.0.0"
STATE_SCHEMA = "astra_underwriting_store_state@2.0.0"
CARD_ANNEX_SCHEMA = "astra_underwriting_card_annex@2.0.0"

SNAPSHOT_EVENT = "snapshot_recorded"
ORIGIN_EVENT = "snapshot_origins_merged"
INVALIDATION_EVENT = "snapshot_invalidated"
REVIEW_EVENT = "review_attached"

ATTENTION_STATUSES = {"expired_or_stale", "information_gap", "reference_insufficient"}
FORBIDDEN_REVIEW_FIELDS = {
    "contract",
    "market",
    "fee",
    "risk",
    "economics",
    "liability",
    "execution_allowed",
    "snapshot",
    "candidate",
    "metadata",
}

_APPEND_LOCK = threading.Lock()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now_ms() -> int:
    return int(time.time() * 1000)


@contextmanager
def _exclusive_file_lock(target_path: str | Path):
    lock_path = Path(str(target_path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _event(event_type: str, body: Mapping[str, Any], now_ms: int | None) -> dict[str, Any]:
    recorded_ms = _now_ms() if now_ms is None else int(now_ms)
    event = {
        "schema": EVENT_SCHEMA,
        "event_type": event_type,
        "recorded_ts_ms": recorded_ms,
        **dict(body),
    }
    event["event_id"] = _hash({"event": event, "time_ns": time.time_ns()})
    return event


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path: str | Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_canonical(value) + "\n", encoding="utf-8")


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path} line {line_no} is not valid JSON") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path} line {line_no} is not a JSON object")
            rows.append(value)
    return rows


def append_jsonl(path: str | Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with _APPEND_LOCK:
        with _exclusive_file_lock(path):
            _append_jsonl_unlocked(path, rows)


def _append_jsonl_unlocked(path: str | Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(_canonical(dict(row)) + "\n" for row in rows).encode("utf-8")
    with path.open("ab") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _origins(snapshot: Mapping[str, Any]) -> list[str]:
    origins = sorted(set(snapshot.get("origins") or []))
    if not origins or set(origins) - core.ORIGINS:
        raise ValueError("snapshot origins are invalid")
    return origins


def _validate_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    if snapshot.get("schema") != core.SCHEMA:
        raise ValueError("unsupported underwriting snapshot schema")
    core.llm_applicability_packet(snapshot)
    return deepcopy(dict(snapshot))


def _empty_state() -> dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "snapshots": {},
        "order": [],
        "candidate_order": {},
        "active_by_candidate": {},
    }


def rebuild_state(path: str | Path) -> dict[str, Any]:
    return _advance_state(_empty_state(), read_jsonl(path))


def _advance_state(base: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Apply ledger rows on top of an already replayed state.

    `base` is never mutated: containers are copied and an entry is copied before
    it changes, so readers still holding `base` keep a consistent object. Frozen
    snapshot bodies are shared. Replaying rows A then rows B equals replaying A+B.
    """
    state = {
        "schema": STATE_SCHEMA,
        "snapshots": dict(base["snapshots"]),
        "order": list(base["order"]),
        "candidate_order": {key: list(value) for key, value in base["candidate_order"].items()},
        "active_by_candidate": dict(base["active_by_candidate"]),
    }
    copied: set[str] = set()

    def _entry(snapshot_id: Any) -> dict[str, Any] | None:
        entry = state["snapshots"].get(snapshot_id)
        if entry is not None and snapshot_id not in copied:
            entry = {**entry, "origins": set(entry["origins"])}
            state["snapshots"][snapshot_id] = entry
            copied.add(snapshot_id)
        return entry

    for row in rows:
        if row.get("schema") != EVENT_SCHEMA:
            raise ValueError("unsupported underwriting ledger event schema")
        event_type = row.get("event_type")
        if event_type == SNAPSHOT_EVENT:
            snapshot = _validate_snapshot(row.get("snapshot") or {})
            snapshot_id = snapshot["snapshot_id"]
            origins = set(row.get("origins") or _origins(snapshot))
            entry = _entry(snapshot_id)
            if entry is None:
                candidate_id = snapshot["candidate_id"]
                candidate_order = state["candidate_order"].setdefault(candidate_id, [])
                candidate_order.append(snapshot_id)
                entry = {
                    "snapshot": snapshot,
                    "origins": set(),
                    "candidate_id": candidate_id,
                    "version": len(candidate_order),
                    "first_event_id": row.get("event_id"),
                    "first_recorded_ts_ms": row.get("recorded_ts_ms"),
                    "latest_event_id": row.get("event_id"),
                    "latest_recorded_ts_ms": row.get("recorded_ts_ms"),
                    "invalidated_by_snapshot_id": None,
                    "invalidated_reason": None,
                    "invalidated_ts_ms": None,
                    "review": None,
                }
                state["snapshots"][snapshot_id] = entry
                copied.add(snapshot_id)
                state["order"].append(snapshot_id)
                state["active_by_candidate"][candidate_id] = snapshot_id
            entry["origins"].update(origins)
            entry["latest_event_id"] = row.get("event_id")
            entry["latest_recorded_ts_ms"] = row.get("recorded_ts_ms")
        elif event_type == ORIGIN_EVENT:
            entry = _entry(row.get("snapshot_id"))
            if entry is None:
                raise ValueError("origin merge references an unknown snapshot")
            entry["origins"].update(row.get("origins") or [])
            entry["latest_event_id"] = row.get("event_id")
            entry["latest_recorded_ts_ms"] = row.get("recorded_ts_ms")
        elif event_type == INVALIDATION_EVENT:
            entry = _entry(row.get("snapshot_id"))
            if entry is None:
                raise ValueError("invalidation references an unknown snapshot")
            entry["invalidated_by_snapshot_id"] = row.get("invalidated_by_snapshot_id")
            entry["invalidated_reason"] = row.get("reason")
            entry["invalidated_ts_ms"] = row.get("recorded_ts_ms")
            entry["latest_event_id"] = row.get("event_id")
            entry["latest_recorded_ts_ms"] = row.get("recorded_ts_ms")
        elif event_type == REVIEW_EVENT:
            entry = _entry(row.get("snapshot_id"))
            if entry is None:
                raise ValueError("review references an unknown snapshot")
            entry["review"] = {
                "status": "attached",
                "reviewed_snapshot_id": row.get("snapshot_id"),
                "review_hash": row.get("review_hash"),
                "review": deepcopy(row.get("review")),
                "recorded_ts_ms": row.get("recorded_ts_ms"),
                "event_id": row.get("event_id"),
            }
            entry["latest_event_id"] = row.get("event_id")
            entry["latest_recorded_ts_ms"] = row.get("recorded_ts_ms")
        else:
            raise ValueError(f"unsupported underwriting ledger event type: {event_type}")

    for snapshot_id, entry in state["snapshots"].items():
        if entry["invalidated_by_snapshot_id"] is not None:
            continue
        state["active_by_candidate"][entry["candidate_id"]] = snapshot_id
    return state


def _snapshot_with_store_fields(entry: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = deepcopy(entry["snapshot"])
    snapshot["origins"] = sorted(entry["origins"])
    if entry.get("review"):
        review = entry["review"]
        snapshot["llm_review"] = {
            "status": "attached",
            "reviewed_snapshot_id": review["reviewed_snapshot_id"],
            "review_hash": review["review_hash"],
        }
    return snapshot


def _attention(entry: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = entry["snapshot"]
    reasons: list[str] = []
    if entry.get("invalidated_by_snapshot_id"):
        reasons.append("snapshot_invalidated_by_newer_version")
    if snapshot.get("status") in ATTENTION_STATUSES:
        reasons.append(f"snapshot_status_{snapshot.get('status')}")
    should_surface = bool(reasons)
    return {
        "should_surface": should_surface,
        "suppression_allowed": not should_surface,
        "reminder_cooldown_can_hide": False if should_surface else True,
        "reasons": reasons,
    }


def build_card_annex(state: Mapping[str, Any], snapshot_id: str) -> dict[str, Any]:
    entry = state["snapshots"].get(snapshot_id)
    if entry is None:
        raise KeyError(f"unknown snapshot_id: {snapshot_id}")
    active = state["active_by_candidate"].get(entry["candidate_id"]) == snapshot_id
    return {
        "schema": CARD_ANNEX_SCHEMA,
        "snapshot": _snapshot_with_store_fields(entry),
        "store": {
            "ledger_schema": EVENT_SCHEMA,
            "snapshot_id": snapshot_id,
            "candidate_id": entry["candidate_id"],
            "version": entry["version"],
            "is_active_version": active,
            "invalidated_by_snapshot_id": entry.get("invalidated_by_snapshot_id"),
            "invalidated_reason": entry.get("invalidated_reason"),
            "origins": sorted(entry["origins"]),
            "origin_count_collapsed": len(entry["origins"]),
            "sample_unit": "candidate_snapshot",
            "dedupe_basis": "snapshot_id_excluding_origin",
            "first_recorded_ts_ms": entry.get("first_recorded_ts_ms"),
            "latest_recorded_ts_ms": entry.get("latest_recorded_ts_ms"),
        },
        "review_annex": deepcopy(entry.get("review")) or {
            "status": "not_attached",
            "reviewed_snapshot_id": None,
        },
        "operator_attention": _attention(entry),
        "execution_allowed": False,
    }


def transient_card_annex(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = _validate_snapshot(snapshot)
    entry = {
        "snapshot": snapshot,
        "origins": set(_origins(snapshot)),
        "candidate_id": snapshot["candidate_id"],
        "version": 1,
        "first_recorded_ts_ms": None,
        "latest_recorded_ts_ms": None,
        "invalidated_by_snapshot_id": None,
        "invalidated_reason": None,
        "review": None,
    }
    state = _empty_state()
    state["snapshots"][snapshot["snapshot_id"]] = entry
    state["active_by_candidate"][snapshot["candidate_id"]] = snapshot["snapshot_id"]
    return build_card_annex(state, snapshot["snapshot_id"])


def _bound_review_id(review: Mapping[str, Any], explicit_snapshot_id: str | None) -> str:
    if not isinstance(review, Mapping):
        raise ValueError("review must be a JSON object")
    review_ids = [
        review.get("reviewed_snapshot_id"),
        review.get("snapshot_id"),
        review.get("source_snapshot_id"),
    ]
    bound = next((str(item) for item in review_ids if item), None)
    if not bound:
        raise ValueError("review must contain a reviewed_snapshot_id")
    if explicit_snapshot_id and str(explicit_snapshot_id) != bound:
        raise ValueError("review snapshot identifiers disagree")
    for item in review_ids:
        if item and str(item) != bound:
            raise ValueError("review snapshot identifiers disagree")
    return bound


def _validate_review_payload(review: Mapping[str, Any]) -> dict[str, Any]:
    clean = deepcopy(dict(review))
    forbidden = sorted(FORBIDDEN_REVIEW_FIELDS.intersection(clean))
    if forbidden:
        raise ValueError("review cannot replace snapshot fields: " + ",".join(forbidden))
    return clean


def _validate_same_snapshot_review(snapshot: Mapping[str, Any], review: Mapping[str, Any]) -> None:
    """The ledger itself accepts only a validated, current v2 causal review."""
    if __package__:
        from . import astra_underwriting_review_v20 as review_core
    else:
        import astra_underwriting_review_v20 as review_core

    expected = {"schema", "status", "response_protocol", "reviewed_snapshot_id",
                "candidate_id", "packet_hash", "projection_hash", "claim_catalog_hash",
                "applicability", "selected_claim_ids", "selected_counter_ids",
                "selected_gap_ids", "selected_reassess_condition_ids", "supports",
                "counters", "q1", "q2", "gaps_cn", "reassess_if_cn", "summary_cn",
                "deterministic_financials", "http_calls"}
    wpem_evidence = None
    review_input = None
    if review.get("response_protocol") in {"bounded_claim_ids@2.5.0", "bounded_claim_ids@2.6.2", "evidence_points@2.6.4"}:
        expected.update({"wpem_evidence", "review_validity"})
        if review.get("response_protocol") in {"bounded_claim_ids@2.6.2", "evidence_points@2.6.4"}:
            expected.add("llm_explanation")
        if review.get("response_protocol") == "evidence_points@2.6.4":
            expected.update({"decision_trace", "selection_origin"})
        wpem_evidence = review.get("wpem_evidence")
        if not isinstance(wpem_evidence, Mapping):
            raise ValueError("WPEM review requires its frozen evidence")
        if __package__:
            from . import astra_wpem_product_review_v1 as product
        else:
            import astra_wpem_product_review_v1 as product
        validity = review.get("review_validity") or {}
        # Replay the recorded contract, never silently upgrade a historical input.
        review_input = product._rebuilt_review_input_for_schema(
            snapshot, wpem_evidence, research_only=False,
            model=validity.get("model"), schema=validity.get("review_input_version"))
    if set(review) != expected or review.get("schema") != review_core.SCHEMA or review.get("status") != "OK":
        raise ValueError("only a complete v2 causal review may be attached")
    if (snapshot["data_identity"]["kind"] in {"historical_replay", "synthetic_fixture"}
            or snapshot["market"]["reference_source"] == "synthetic_fixture"):
        raise ValueError("synthetic or historical snapshot cannot receive a live review")
    allowed_http_calls = {1} if review.get("response_protocol") == "evidence_points@2.6.4" else {1, 2}
    if (review.get("candidate_id") != snapshot["candidate_id"]
            or review.get("packet_hash") != core._hash(core.llm_applicability_packet(snapshot))
            or type(review.get("http_calls")) is not int
            or review["http_calls"] not in allowed_http_calls):
        raise ValueError("review packet or candidate identity does not match the frozen snapshot")
    payload = {key: review[key] for key in (
        "response_protocol", "reviewed_snapshot_id", "projection_hash",
        "claim_catalog_hash", "applicability", "selected_claim_ids",
        "selected_counter_ids", "selected_gap_ids",
        "selected_reassess_condition_ids")}
    if review.get("response_protocol") == "bounded_claim_ids@2.6.2":
        explanation = review.get("llm_explanation") if isinstance(review.get("llm_explanation"), Mapping) else {}
        payload["explanation"] = {
            key: {
                "text_cn": (explanation.get(key) or {}).get("text_cn"),
                "evidence_refs": (explanation.get(key) or {}).get("evidence_refs"),
            }
            for key in ("q1", "q2")
        }
    elif review.get("response_protocol") == "evidence_points@2.6.4":
        explanation = review.get("llm_explanation") or {}
        payload = {"response_protocol": review["response_protocol"]}
        for key in ("q1", "q2"):
            payload[key] = [
                {"text_cn": point.get("text_cn"), "evidence_refs": point.get("evidence_refs")}
                for point in (explanation.get(key) or {}).get("points", [])
                if isinstance(point, Mapping)
            ]
    normalized = review_core.validate_wire_response(snapshot, payload, review_input=review_input,
                                                    wpem_evidence=wpem_evidence)
    for key in expected - {"http_calls"}:
        if review.get(key) != normalized.get(key):
            raise ValueError("review normalized fields do not match the bounded catalog")


_TAIL_CHECK_BYTES = 256


class UnderwritingLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        # (file identity, consumed offset, consumed line count, bytes before offset, state)
        self._cache: tuple[Any, int, int, bytes, dict[str, Any]] | None = None
        self._cache_lock = threading.Lock()

    def drop_cache(self) -> None:
        with self._cache_lock:
            self._cache = None

    def state(self) -> dict[str, Any]:
        """The replayed ledger. Append-only growth is replayed incrementally (only the new
        complete lines); any other change of the file replays it whole. The returned state is
        shared: callers only read it."""
        with self._cache_lock:
            try:
                st = self.path.stat()
            except FileNotFoundError:
                self._cache = None
                return _empty_state()
            identity = (st.st_dev, st.st_ino)
            cache = self._cache
            with self.path.open("rb") as handle:
                if (cache is not None and cache[0] == identity and st.st_size >= cache[1]
                        and self._tail_matches(handle, cache[1], cache[3])):
                    _identity, offset, line_no, _tail, state = cache
                else:
                    offset, line_no, state = 0, 0, _empty_state()
                handle.seek(offset)
                rows: list[dict[str, Any]] = []
                unterminated: dict[str, Any] | None = None
                while True:
                    line = handle.readline()
                    if not line:
                        break
                    text = line.decode("utf-8-sig" if offset == 0 else "utf-8").strip()
                    if not line.endswith(b"\n"):
                        # The last line without its newline: used when it already parses (as the
                        # full replay did), but never cached, so it is replayed again once complete.
                        try:
                            value = json.loads(text) if text else None
                        except json.JSONDecodeError:
                            value = None  # still being written
                        unterminated = value if isinstance(value, dict) else None
                        break
                    line_no += 1
                    offset += len(line)
                    if not text:
                        continue
                    try:
                        value = json.loads(text)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"{self.path} line {line_no} is not valid JSON") from exc
                    if not isinstance(value, dict):
                        raise ValueError(f"{self.path} line {line_no} is not a JSON object")
                    rows.append(value)
                if rows:
                    state = _advance_state(state, rows)
                start = max(0, offset - _TAIL_CHECK_BYTES)
                handle.seek(start)
                tail = handle.read(offset - start)
            self._cache = (identity, offset, line_no, tail, state)
            return _advance_state(state, [unterminated]) if unterminated is not None else state

    @staticmethod
    def _tail_matches(handle: Any, offset: int, tail: bytes) -> bool:
        handle.seek(offset - len(tail))
        return handle.read(len(tail)) == tail

    def record_payload(self, payload: Mapping[str, Any], now_ms: int | None = None) -> dict[str, Any]:
        snapshot = core.evaluate(payload)
        return self.record_snapshot(snapshot, now_ms=now_ms)

    def record_snapshot(self, snapshot: Mapping[str, Any], now_ms: int | None = None) -> dict[str, Any]:
        snapshot = _validate_snapshot(snapshot)
        origins = _origins(snapshot)
        snapshot_id = snapshot["snapshot_id"]
        with _APPEND_LOCK:
            with _exclusive_file_lock(self.path):
                state = self.state()
                existing = state["snapshots"].get(snapshot_id)
                events: list[dict[str, Any]] = []
                if existing is None:
                    candidate_id = snapshot["candidate_id"]
                    events.append(
                        _event(
                            SNAPSHOT_EVENT,
                            {
                                "snapshot_id": snapshot_id,
                                "candidate_id": candidate_id,
                                "origins": origins,
                                "snapshot": snapshot,
                            },
                            now_ms,
                        )
                    )
                    for old_id in state["candidate_order"].get(candidate_id, []):
                        old = state["snapshots"][old_id]
                        if old.get("invalidated_by_snapshot_id") is None:
                            events.append(
                                _event(
                                    INVALIDATION_EVENT,
                                    {
                                        "snapshot_id": old_id,
                                        "candidate_id": candidate_id,
                                        "invalidated_by_snapshot_id": snapshot_id,
                                        "reason": "superseded_by_changed_quote_or_input",
                                    },
                                    now_ms,
                                )
                            )
                else:
                    missing = sorted(set(origins) - set(existing["origins"]))
                    if missing:
                        events.append(
                            _event(
                                ORIGIN_EVENT,
                                {
                                    "snapshot_id": snapshot_id,
                                    "candidate_id": existing["candidate_id"],
                                    "origins": missing,
                                },
                                now_ms,
                            )
                        )
                _append_jsonl_unlocked(self.path, events)
        return build_card_annex(self.state(), snapshot_id)

    def attach_review(
        self,
        review: Mapping[str, Any],
        snapshot_id: str | None = None,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        clean = _validate_review_payload(review)
        bound_id = _bound_review_id(review, snapshot_id)
        with _APPEND_LOCK:
            with _exclusive_file_lock(self.path):
                state = self.state()
                if bound_id not in state["snapshots"]:
                    raise ValueError("review references a snapshot that is not in the ledger")
                snapshot = _snapshot_with_store_fields(state["snapshots"][bound_id])
                core.llm_applicability_packet(snapshot)
                _validate_same_snapshot_review(snapshot, clean)
                prior = state["snapshots"][bound_id].get("review")
                if prior is not None:
                    if prior.get("review_hash") != _hash(clean):
                        raise ValueError("a different review is already frozen for this snapshot")
                    return build_card_annex(state, bound_id)
                event = _event(
                    REVIEW_EVENT,
                    {
                        "snapshot_id": bound_id,
                        "candidate_id": snapshot["candidate_id"],
                        "review_hash": _hash(clean),
                        "review": clean,
                    },
                    now_ms,
                )
                _append_jsonl_unlocked(self.path, [event])
        return build_card_annex(self.state(), bound_id)

    def llm_packet(self, snapshot_id: str) -> dict[str, Any]:
        state = self.state()
        entry = state["snapshots"].get(snapshot_id)
        if entry is None:
            raise KeyError(f"unknown snapshot_id: {snapshot_id}")
        return core.llm_applicability_packet(_snapshot_with_store_fields(entry))


def load_payload(path: str | Path) -> dict[str, Any]:
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError("input payload must be a JSON object")
    return payload


def add_origins(payload: Mapping[str, Any], origins: Sequence[str]) -> dict[str, Any]:
    merged = deepcopy(dict(payload))
    current = set(merged.get("origins") or [])
    current.update(origins)
    if set(current) - core.ORIGINS:
        raise ValueError("invalid entry origin")
    merged["origins"] = sorted(current)
    return merged
