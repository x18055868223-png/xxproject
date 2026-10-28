"""Local locked-candidate observation store for Astra packets.

The store is deliberately thin: a small JSON index plus immutable gzip point
files.  It validates existing candidate-asof packets and records observation
attempts; it does not call an LLM, fetch market data, infer risk, or export a
public view.
"""
from __future__ import annotations

from copy import deepcopy
import gzip
import hashlib
import json
import shutil
import os
from pathlib import Path
import re
import threading
import zlib
from typing import Any, Mapping

if __package__:
    from . import astra_candidate_asof_v1 as packets
    from . import astra_underwriting_store_v20 as store
else:
    import astra_candidate_asof_v1 as packets
    import astra_underwriting_store_v20 as store


SCHEMA = "astra_locked_candidate_store@1.0.0"
POINT_SCHEMA = "astra_locked_candidate_point@1.0.0"
POINT_DELTA_SCHEMA = "astra_locked_candidate_delta@1.0.0"
POINT_KIND = "point"
GAP_KIND = "gap"
ACTIVE = "active"
PAUSED = "paused"
ERROR = "error"
STOPPED = "stopped"
EXPIRED = "expired"
MAX_REQUEST_KEY_BYTES = 256
MAX_STORED_GAPS_PER_EPISODE = 128
MAX_REQUEST_KEYS = 4096
SERIES_SCHEMA = "astra_locked_candidate_series_row@1.0.0"

# Retention of full evidence files. Every sampled point keeps its small metrics row in
# series/<episode>.jsonl for the process chart; full point files are thinned by age:
# all within FULL_WINDOW of the newest sample of an active policy, then one per DENSE
# bucket; after a policy ends, one per DENSE bucket for DENSE_UNTIL, one per SPARSE
# bucket until SPARSE_UNTIL, then only t0 and the last point. Thinned index rows of a
# policy ended for COLLAPSE_AFTER are folded away; a policy ended for DROP_AFTER is
# removed (its settlement and audit-log events are kept elsewhere).
RETENTION_DEFAULTS = {
    "full_window_ms": 2 * 3_600_000,
    "dense_bucket_ms": 10 * 60_000,
    "dense_until_ms": 3 * 86_400_000,
    "sparse_bucket_ms": 60 * 60_000,
    "sparse_until_ms": 30 * 86_400_000,
    "collapse_after_ms": 7 * 86_400_000,
    "drop_after_ms": 60 * 86_400_000,
}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_VALID_STATUSES = {ACTIVE, PAUSED, ERROR, STOPPED, EXPIRED}


class LockedCandidateError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _clock(value: Any, code: str = "invalid_clock") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LockedCandidateError(code, code)
    return value


def _text(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise LockedCandidateError(code, code)
    return value


def _request_key(value: Any) -> str:
    text = _text(value, "invalid_request_key")
    if len(text.encode("utf-8")) > MAX_REQUEST_KEY_BYTES:
        raise LockedCandidateError("request_key_too_long", "request_key exceeds 256 UTF-8 bytes")
    return text


def _point_id(value: Any) -> str:
    text = _text(value, "invalid_point_id")
    if not _HEX64.fullmatch(text):
        raise LockedCandidateError("invalid_point_id", "point_id must be exactly 64 lowercase hex characters")
    return text


def _hash(value: Any) -> str:
    return hashlib.sha256(store._canonical(value).encode("utf-8")).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return (store._canonical(value) + "\n").encode("utf-8")


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _state_bytes(state: Mapping[str, Any]) -> bytes:
    return (store._canonical(dict(state)) + "\n").encode("utf-8")


def _empty_state() -> dict[str, Any]:
    return {"schema": SCHEMA, "episodes": {}, "order": [], "request_keys": {}}


def _contract_key(snapshot: Mapping[str, Any]) -> str:
    return _hash({"candidate_id": snapshot["candidate_id"], "contract": snapshot["contract"]})


def _is_delta_envelope(value: Any) -> bool:
    return isinstance(value, Mapping) and value.get("schema") == POINT_DELTA_SCHEMA


def _pointer_escape(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _pointer_unescape(value: str) -> str:
    result = []
    index = 0
    while index < len(value):
        char = value[index]
        if char != "~":
            result.append(char)
            index += 1
            continue
        if index + 1 >= len(value) or value[index + 1] not in {"0", "1"}:
            raise LockedCandidateError("delta_patch_invalid", "invalid JSON pointer escape")
        result.append("~" if value[index + 1] == "0" else "/")
        index += 2
    return "".join(result)


def _pointer_join(path: str, part: Any) -> str:
    return path + "/" + _pointer_escape(part)


def _build_delta_patch(base: Any, target: Any, path: str = "") -> tuple[dict[str, Any], list[str]]:
    sets: dict[str, Any] = {}
    deletes: list[str] = []
    if isinstance(base, dict) and isinstance(target, dict):
        for key in sorted(base.keys() - target.keys()):
            deletes.append(_pointer_join(path, key))
        for key in sorted(target.keys()):
            next_path = _pointer_join(path, key)
            if key not in base:
                sets[next_path] = deepcopy(target[key])
            else:
                child_sets, child_deletes = _build_delta_patch(base[key], target[key], next_path)
                sets.update(child_sets)
                deletes.extend(child_deletes)
        return sets, deletes
    if isinstance(base, list) and isinstance(target, list) and len(base) == len(target):
        for index, (left, right) in enumerate(zip(base, target)):
            child_sets, child_deletes = _build_delta_patch(left, right, _pointer_join(path, index))
            sets.update(child_sets)
            deletes.extend(child_deletes)
        return sets, deletes
    if base != target or type(base) is not type(target):
        if not path:
            raise LockedCandidateError("delta_patch_invalid", "delta patch cannot replace the root")
        sets[path] = deepcopy(target)
    return sets, deletes


def _pointer_tokens(path: str) -> list[str]:
    if not isinstance(path, str) or not path.startswith("/") or path == "/":
        raise LockedCandidateError("delta_patch_invalid", "invalid JSON pointer path")
    return [_pointer_unescape(part) for part in path[1:].split("/")]


def _list_index(token: str, length: int) -> int:
    if not token.isdecimal():
        raise LockedCandidateError("delta_patch_invalid", "list path segment must be a non-negative integer")
    index = int(token)
    if index >= length:
        raise LockedCandidateError("delta_patch_invalid", "list path segment is out of range")
    return index


def _apply_pointer_set(root: Any, path: str, value: Any) -> None:
    tokens = _pointer_tokens(path)
    current = root
    for token in tokens[:-1]:
        if isinstance(current, dict):
            if token not in current:
                raise LockedCandidateError("delta_patch_invalid", "patch parent path is missing")
            current = current[token]
        elif isinstance(current, list):
            current = current[_list_index(token, len(current))]
        else:
            raise LockedCandidateError("delta_patch_invalid", "patch parent path is not a container")
    leaf = tokens[-1]
    if isinstance(current, dict):
        current[leaf] = deepcopy(value)
        return
    if isinstance(current, list):
        current[_list_index(leaf, len(current))] = deepcopy(value)
        return
    raise LockedCandidateError("delta_patch_invalid", "patch target parent is not a container")


def _apply_pointer_delete(root: Any, path: str) -> None:
    tokens = _pointer_tokens(path)
    current = root
    for token in tokens[:-1]:
        if isinstance(current, dict):
            if token not in current:
                raise LockedCandidateError("delta_patch_invalid", "delete parent path is missing")
            current = current[token]
        elif isinstance(current, list):
            current = current[_list_index(token, len(current))]
        else:
            raise LockedCandidateError("delta_patch_invalid", "delete parent path is not a container")
    leaf = tokens[-1]
    if not isinstance(current, dict) or leaf not in current:
        raise LockedCandidateError("delta_patch_invalid", "delete path is missing or not an object key")
    del current[leaf]


def _apply_delta_patch(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, Mapping):
        raise LockedCandidateError("delta_patch_invalid", "delta patch must be an object")
    sets = patch.get("set", {})
    deletes = patch.get("delete", [])
    if not isinstance(sets, Mapping) or not isinstance(deletes, list):
        raise LockedCandidateError("delta_patch_invalid", "delta patch set/delete shape is invalid")
    output = deepcopy(dict(base))
    for path in sorted(deletes, key=lambda item: len(str(item)), reverse=True):
        _apply_pointer_delete(output, path)
    for path, value in sorted(sets.items(), key=lambda item: item[0]):
        _apply_pointer_set(output, path, value)
    return output


def validate_point(point: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a read-only point object and return its normalized identity.

    This helper performs packet replay and candidate/packet binding checks but
    does not write, gzip, or mutate the supplied object.
    """
    if not isinstance(point, Mapping):
        raise LockedCandidateError("invalid_point", "point must be a JSON object")
    candidate = point.get("candidate")
    packet = point.get("packet")
    if not isinstance(candidate, Mapping) or not isinstance(packet, Mapping):
        raise LockedCandidateError("invalid_point", "point requires candidate and packet objects")
    recorded_at_ms = _clock(point.get("recorded_at_ms"), "invalid_recorded_at_ms")
    try:
        packets.replay_packet(packet)
    except Exception as exc:  # noqa: BLE001 - normalize validation surface for callers.
        raise LockedCandidateError("invalid_packet", str(exc)) from exc
    snapshot = packet["snapshot"]
    if candidate.get("snapshot") != snapshot:
        raise LockedCandidateError("candidate_packet_snapshot_mismatch",
                                   "candidate.snapshot must equal packet.snapshot")
    decision_asof_ms = _clock(snapshot["market"].get("valuation_ts_ms"), "invalid_decision_asof_ms")
    if recorded_at_ms < decision_asof_ms:
        raise LockedCandidateError("recorded_before_asof", "recorded_at_ms is before packet as-of time")
    clean = {"candidate": deepcopy(dict(candidate)), "packet": deepcopy(dict(packet)),
             "recorded_at_ms": recorded_at_ms}
    point_id = _hash(clean)
    return {
        "schema": POINT_SCHEMA,
        "point": clean,
        "point_id": point_id,
        "snapshot": snapshot,
        "candidate_id": snapshot["candidate_id"],
        "contract": deepcopy(snapshot["contract"]),
        "contract_key": _contract_key(snapshot),
        "snapshot_id": snapshot["snapshot_id"],
        "decision_asof_ms": decision_asof_ms,
        "recorded_at_ms": recorded_at_ms,
    }


def _prepare_point_for_write(point: Mapping[str, Any]) -> dict[str, Any]:
    checked = validate_point(point)
    checked["raw"] = _canonical_bytes(checked["point"])
    checked["gz"] = gzip.compress(checked["raw"], compresslevel=9, mtime=0)
    return checked


class LockedCandidateStore:
    def __init__(self, root: str | Path, *, max_active: int = 4, max_episodes: int = 64,
                 max_bytes: int = 268_435_456, max_samples: int = 1440,
                 max_point_bytes: int = 33_554_432):
        self.root = Path(root)
        self.index_path = self.root / "index.json"
        self.points_dir = self.root / "points"
        self.max_active = _clock(max_active, "invalid_max_active")
        self.max_episodes = _clock(max_episodes, "invalid_max_episodes")
        self.max_bytes = _clock(max_bytes, "invalid_max_bytes")
        self.max_samples = _clock(max_samples, "invalid_max_samples")
        self.max_point_bytes = _clock(max_point_bytes, "invalid_max_point_bytes")
        self._control_reserve_bytes = min(4096, max(1, self.max_bytes // 8))
        if not all((self.max_active, self.max_episodes, self.max_bytes,
                    self.max_samples, self.max_point_bytes)):
            raise LockedCandidateError("invalid_budget", "store budgets must be positive")
        self._cache_lock = threading.Lock()
        self._series_lock = threading.Lock()
        self.series_dir = self.root / "series"
        self._point_meta_cache: dict[str, tuple[tuple[int, int, int], dict[str, Any]]] = {}
        self._point_verified_cache: dict[str, tuple[tuple[int, int, int], dict[str, Any]]] = {}
        # validated full t0 bases for delta writes, keyed by point id + file stat (one per episode)
        self._delta_base_cache: dict[str, tuple[tuple[int, int, int], dict[str, Any]]] = {}

    def lock(self, point: Mapping[str, Any], *, request_key: str, now_ms: int,
             new_reference: bool = False, source_episode_id: str | None = None) -> dict[str, Any]:
        request_key = _request_key(request_key)
        now_ms = _clock(now_ms, "invalid_now_ms")
        checked = _prepare_point_for_write(point)
        with store._APPEND_LOCK, store._exclusive_file_lock(self.index_path):
            state = self._read_state_unlocked()
            prior = state["request_keys"].get(request_key)
            if prior is not None:
                if prior.get("source_episode_id") != source_episode_id:
                    raise LockedCandidateError("request_key_reused", "request_key belongs to a different source episode")
                if prior.get("operation") != "lock" or prior.get("contract_key") != checked["contract_key"]:
                    raise LockedCandidateError("request_key_reused",
                                               "request_key cannot be reused for a different contract or operation")
                if prior.get("point_id") != checked["point_id"] or bool(prior.get("new_reference")) != bool(new_reference):
                    raise LockedCandidateError("request_key_reused",
                                               "request_key cannot be reused for a different lock point")
                return {"created": False, "episode": self._summary(state["episodes"][prior["episode_id"]])}

            if len(state["request_keys"]) >= MAX_REQUEST_KEYS:
                raise LockedCandidateError("request_key_budget_exhausted", "durable request key budget exhausted")
            if source_episode_id:
                source = self._episode(state, source_episode_id)
                if not new_reference or not any(row.get("kind") == POINT_KIND and row.get("point_id") == checked["point_id"]
                                                and not row.get("thinned") for row in source["observations"]):
                    raise LockedCandidateError("source_reference_mismatch", "explicit new reference must use a retained source point")

            if not new_reference:
                existing = self._latest_active_for_contract(state, checked["contract_key"])
                if existing is not None:
                    state["request_keys"][request_key] = {
                        "operation": "lock",
                        "contract_key": checked["contract_key"],
                        "episode_id": existing["episode_id"],
                        "point_id": checked["point_id"],
                        "candidate_id": checked["candidate_id"],
                        "snapshot_id": checked["snapshot_id"],
                        "new_reference": False,
                    }
                    self._save_state_with_budget_unlocked(state, extra_point_bytes=self._control_reserve_bytes)
                    return {"created": False, "episode": self._summary(existing)}

            if len(state["episodes"]) >= self.max_episodes:
                raise LockedCandidateError("episode_budget_exhausted", "locked candidate episode budget exhausted")
            active_count = 0
            for item in state["episodes"].values():
                if not isinstance(item, Mapping) or item.get("status") != ACTIVE:
                    continue
                try:
                    self._validate_episode_index(item)
                except LockedCandidateError:
                    continue
                active_count += 1
            if active_count >= self.max_active:
                raise LockedCandidateError("active_budget_exhausted", "active locked candidate budget exhausted")
            if self._point_count(state) >= self.max_samples:
                raise LockedCandidateError("sample_budget_exhausted", "locked candidate sample budget exhausted")
            self._check_point_size(checked)

            episode_id = "lce_" + _hash({"contract_key": checked["contract_key"],
                                           "point_id": checked["point_id"],
                                           "request_key": request_key,
                                           "now_ms": now_ms,
                                           "ordinal": len(state["order"])})[:24]
            event = self._point_event(checked, now_ms, step=0)
            episode = {
                "schema": SCHEMA,
                "episode_id": episode_id,
                "status": ACTIVE,
                "candidate_id": checked["candidate_id"],
                "contract_key": checked["contract_key"],
                "contract": checked["contract"],
                "created_at_ms": now_ms,
                "updated_at_ms": now_ms,
                "last_attempt_at_ms": now_ms,
                "stopped_at_ms": None,
                "stop_reason": None,
                "request_key": request_key,
                "source_episode_id": source_episode_id,
                "t0_baseline_point_id": checked["point_id"],
                "latest_point_id": checked["point_id"],
                "observations": [event],
                "last_gap_reason": None,
            }
            state["episodes"][episode_id] = episode
            state["order"].append(episode_id)
            state["request_keys"][request_key] = {
                "operation": "lock",
                "contract_key": checked["contract_key"],
                "episode_id": episode_id,
                "point_id": checked["point_id"],
                "candidate_id": checked["candidate_id"],
                "snapshot_id": checked["snapshot_id"],
                "new_reference": bool(new_reference),
                "source_episode_id": source_episode_id,
            }
            self._save_point_and_state_unlocked(checked, state)
            return {"created": True, "episode": self._summary(episode)}

    def lookup_request(self, request_key: str, *, candidate_id: str, snapshot_id: str,
                       new_reference: bool = False, source_episode_id: str | None = None) -> dict[str, Any] | None:
        request_key = _request_key(request_key)
        candidate_id = _text(candidate_id, "invalid_candidate_id")
        snapshot_id = _text(snapshot_id, "invalid_snapshot_id")
        state = self._read_state()
        prior = state["request_keys"].get(request_key)
        if prior is None:
            return None
        if (prior.get("operation") != "lock" or bool(prior.get("new_reference")) != bool(new_reference)
                or prior.get("source_episode_id") != source_episode_id):
            raise LockedCandidateError("request_key_reused",
                                       "request_key cannot be reused for a different operation")
        bound_candidate, bound_snapshot = self._request_candidate_snapshot(state, prior)
        if bound_candidate != candidate_id or bound_snapshot != snapshot_id:
            raise LockedCandidateError("request_key_reused",
                                       "request_key cannot be reused for a different candidate snapshot")
        return {"created": False, "episode": self._summary(self._episode(state, prior["episode_id"]))}

    def append(self, episode_id: str, point: Mapping[str, Any], *, now_ms: int) -> dict[str, Any]:
        episode_id = _text(episode_id, "invalid_episode_id")
        now_ms = _clock(now_ms, "invalid_now_ms")
        checked = _prepare_point_for_write(point)
        return {**self._append_checked(episode_id, checked, now_ms=now_ms), "point_id": checked["point_id"]}

    def _append_checked(self, episode_id: str, checked: Mapping[str, Any], *, now_ms: int) -> dict[str, Any]:
        with store._APPEND_LOCK, store._exclusive_file_lock(self.index_path):
            state = self._read_state_unlocked()
            episode = self._episode(state, episode_id)
            self._require_appendable(episode)
            if episode["contract_key"] != checked["contract_key"]:
                raise LockedCandidateError("contract_mismatch", "observation point belongs to a different contract")
            if any(row.get("kind") == POINT_KIND and row.get("point_id") == checked["point_id"]
                   for row in episode["observations"]):
                return self._summary(episode)
            newest = max((row.get("recorded_at_ms") for row in episode["observations"]
                          if row.get("kind") == POINT_KIND and isinstance(row.get("recorded_at_ms"), int)), default=None)
            if newest is not None and isinstance(checked.get("recorded_at_ms"), int) and checked["recorded_at_ms"] <= newest:
                return {**self._summary(episode), "stale_point": True}
            write_plan = self._point_write_plan(checked, episode)
            reason = self._budget_gap_reason(state, checked, write_plan)
            next_state = deepcopy(state)
            next_episode = next_state["episodes"][episode_id]
            next_episode["observations"].append(self._point_event(checked, now_ms, step=len(episode["observations"])))
            next_episode["latest_point_id"] = checked["point_id"]
            next_episode["last_attempt_at_ms"] = now_ms
            next_episode["updated_at_ms"] = now_ms
            next_episode["last_gap_reason"] = None
            if reason is None:
                extra = 0 if write_plan["exists"] else len(write_plan["gz"])
                try:
                    self._check_total_budget(_state_bytes(next_state), extra + self._control_reserve_bytes)
                except LockedCandidateError:
                    reason = "byte_budget_exhausted"
            if reason is not None:
                self._gap_in_state(episode, now_ms, reason, status=PAUSED)
                self._save_state_with_budget_unlocked(state, extra_point_bytes=0)
                return self._summary(episode)
            try:
                self._ensure_point_file(checked, write_plan=write_plan)
            except OSError as exc:
                self._gap_in_state(episode, now_ms, "point_write_failed:" + exc.__class__.__name__, status=ERROR)
                self._save_state_with_budget_unlocked(state, extra_point_bytes=0)
                return self._summary(episode)
            try:
                self._save_state_with_budget_unlocked(next_state, extra_point_bytes=0)
            except OSError as exc:
                self._gap_in_state(episode, now_ms, "index_write_failed:" + exc.__class__.__name__, status=ERROR)
                self._save_state_with_budget_unlocked(state, extra_point_bytes=0)
                return self._summary(episode)
            return self._summary(next_episode)

    def record_gap(self, episode_id: str, *, now_ms: int, reason: str,
                   expired: bool = False) -> dict[str, Any]:
        episode_id = _text(episode_id, "invalid_episode_id")
        now_ms = _clock(now_ms, "invalid_now_ms")
        reason = _text(reason, "invalid_gap_reason")
        with store._APPEND_LOCK, store._exclusive_file_lock(self.index_path):
            state = self._read_state_unlocked()
            episode = self._episode(state, episode_id)
            # An in-flight collector may finish after a user's stop. Do not
            # rewrite that terminal record or append a spurious sampling gap.
            if episode["status"] in {STOPPED, EXPIRED}:
                return self._summary(episode)
            prior_state = deepcopy(state)
            status = EXPIRED if expired else episode["status"]
            self._gap_in_state(episode, now_ms, reason, status=status, expired=expired)
            if expired:
                episode["stopped_at_ms"] = now_ms
                episode["stop_reason"] = "expired"
            try:
                self._save_state_with_budget_unlocked(state, extra_point_bytes=self._control_reserve_bytes)
            except LockedCandidateError as exc:
                if exc.code != "byte_budget_exhausted":
                    raise
                # Preserve every existing row. The reserved control space can
                # publish the terminal state even when another row cannot fit.
                episode = prior_state["episodes"][episode_id]
                episode.update(status=EXPIRED if expired else PAUSED,
                    last_attempt_at_ms=now_ms, updated_at_ms=now_ms,
                    last_gap_reason=reason if expired else "gap_index_budget_exhausted")
                stats = episode.setdefault("gap_stats", {"count": 0, "stored_count": 0})
                stats["count"] = int(stats.get("count") or 0) + 1
                stats["last"] = {"recorded_at_ms": now_ms, "reason": reason, "expired": expired}
                stats["first"] = stats.get("first") or deepcopy(stats["last"])
                if expired:
                    episode.update(stopped_at_ms=now_ms, stop_reason="expired")
                self._save_state_with_budget_unlocked(prior_state, extra_point_bytes=0)
            return self._summary(episode)

    def stop(self, episode_id: str, *, now_ms: int) -> dict[str, Any]:
        episode_id = _text(episode_id, "invalid_episode_id")
        now_ms = _clock(now_ms, "invalid_now_ms")
        with store._APPEND_LOCK, store._exclusive_file_lock(self.index_path):
            state = self._read_state_unlocked()
            episode = self._episode(state, episode_id)
            if episode["status"] != EXPIRED:
                episode["status"] = STOPPED
                episode["stop_reason"] = "observation_stopped_not_position_close"
            episode["stopped_at_ms"] = episode["stopped_at_ms"] or now_ms
            episode["updated_at_ms"] = now_ms
            episode["last_attempt_at_ms"] = now_ms
            self._save_state_with_budget_unlocked(state, extra_point_bytes=0)
            return self._summary(episode)

    def list(self) -> list[dict[str, Any]]:
        state = self._read_state()
        return [self._summary_or_diagnostic(episode_id, state["episodes"].get(episode_id))
                for episode_id in state["order"]]

    def active(self) -> list[dict[str, Any]]:
        return [summary for summary in self.list() if summary["status"] == ACTIVE]

    def detail(self, episode_id: str, *, baseline_point_id: str | None = None,
               latest_point_id: str | None = None) -> dict[str, Any]:
        episode_id = _text(episode_id, "invalid_episode_id")
        state = self._read_state()
        episode = self._episode(state, episode_id)
        explicit_pair = baseline_point_id is not None or latest_point_id is not None
        return self._detail_for_pair(
            episode,
            baseline_point_id=baseline_point_id or episode["t0_baseline_point_id"],
            latest_point_id=latest_point_id or episode["latest_point_id"],
            truncate_after_latest=explicit_pair,
        )

    def detail_pair(self, episode_id: str, baseline_point_id: str,
                    latest_point_id: str) -> dict[str, Any]:
        """Read one explicit t0/t1 pair from the same locked episode.

        The baseline must be the immutable t0 for this episode.  The latest
        point must be one of this episode's retained point observations; the
        returned process index stops at that selected t1 instead of drifting to
        a newer sample or another policy.
        """
        return self.detail(
            episode_id,
            baseline_point_id=baseline_point_id,
            latest_point_id=latest_point_id,
        )

    def _detail_for_pair(self, episode: Mapping[str, Any], *, baseline_point_id: str,
                         latest_point_id: str, truncate_after_latest: bool) -> dict[str, Any]:
        baseline_point_id = _point_id(baseline_point_id)
        latest_point_id = _point_id(latest_point_id)
        if baseline_point_id != episode["t0_baseline_point_id"]:
            raise LockedCandidateError("baseline_point_mismatch", "baseline point is not this episode's immutable t0")
        latest_step = None
        for row in episode["observations"]:
            if row.get("kind") == POINT_KIND and row.get("point_id") == latest_point_id:
                if row.get("thinned"):
                    raise LockedCandidateError("point_thinned",
                                               "this sample's full evidence was thinned by retention; its metrics remain")
                latest_step = row["step"]
                break
        if latest_step is None:
            if episode.get("collapsed") and latest_point_id in self.read_series(episode["episode_id"]):
                raise LockedCandidateError("point_thinned",
                                           "this sample's full evidence was thinned by retention; its metrics remain")
            raise LockedCandidateError("latest_point_not_retained", "latest point is not retained by this episode")
        baseline = self._read_point(baseline_point_id, include_point=True)["point"]
        latest = self._read_point(latest_point_id, include_point=True)["point"]
        episode_for_pair = deepcopy(dict(episode))
        if truncate_after_latest:
            episode_for_pair["observations"] = deepcopy(episode["observations"][:latest_step + 1])
        else:
            episode_for_pair["observations"] = deepcopy(episode["observations"])
        episode_for_pair["latest_point_id"] = latest_point_id
        observations = []
        for row in episode_for_pair["observations"]:
            compact = {key: deepcopy(row.get(key)) for key in (
                "step", "kind", "recorded_at_ms", "decision_asof_ms",
                "point_id", "snapshot_id", "gap", "thinned"
            ) if key in row}
            observations.append(compact)
        return {"episode": self._summary(episode_for_pair), "baseline": baseline,
                "latest": latest, "observations": observations,
                "selected_latest_point_id": latest_point_id,
                "selected_latest_step": latest_step,
                "intermediate_packets_replayed": False}

    def retained_point(self, episode_id: str, snapshot_id: str) -> dict[str, Any] | None:
        """Read the exact displayed source point even if a later sample arrived."""
        state = self._read_state()
        episode = self._episode(state, _text(episode_id, "invalid_episode_id"))
        for row in reversed(episode["observations"]):
            if row.get("kind") == POINT_KIND and row.get("snapshot_id") == snapshot_id and not row.get("thinned"):
                return self._read_point(row["point_id"], include_point=True)["point"]
        return None

    def episode_observations(self, episode_id: str) -> dict[str, Any]:
        """The episode summary plus its compact observation index (points and gaps)."""
        state = self._read_state()
        episode = self._episode(state, _text(episode_id, "invalid_episode_id"))
        rows = [{key: deepcopy(row.get(key)) for key in (
            "step", "kind", "recorded_at_ms", "decision_asof_ms", "point_id", "snapshot_id", "gap", "thinned") if key in row}
            for row in episode["observations"]]
        return {"episode": self._summary(episode), "baseline_point_id": episode["t0_baseline_point_id"],
                "observations": rows}

    def iter_points(self, point_ids: list[str]):
        """Yield (point_id, point, error_code) for read-only projections.

        Every point is checked against its content hash; a delta's full base is
        read and hash-checked once for the whole batch instead of per point.
        """
        bases: dict[str, Any] = {}
        for raw_id in point_ids:
            try:
                point_id = _point_id(raw_id)
                stored, _stat = self._read_stored_json(point_id)
                if _is_delta_envelope(stored):
                    base_id = _point_id(stored.get("base_point_id"))
                    if base_id not in bases:
                        base, _base_stat = self._read_stored_json(base_id)
                        if _is_delta_envelope(base) or _hash(base) != base_id:
                            raise LockedCandidateError("delta_base_hash_mismatch", "delta base does not match its id")
                        bases[base_id] = base
                    point = _apply_delta_patch(bases[base_id], stored.get("patch"))
                else:
                    point = stored
                if _hash(point) != point_id:
                    raise LockedCandidateError("point_hash_mismatch", "stored point content hash does not match filename")
                yield point_id, point, None
            except LockedCandidateError as exc:
                yield raw_id, None, exc.code

    # ── metrics series and retention ─────────────────────
    def _series_path(self, episode_id: str) -> Path:
        if not re.fullmatch(r"lce_[0-9a-f]{24}", str(episode_id)):
            raise LockedCandidateError("invalid_episode_id", "invalid episode id")
        return self.series_dir / (episode_id + ".jsonl")

    @staticmethod
    def _series_line(entry: Mapping[str, Any]) -> str:
        return json.dumps(entry, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    @staticmethod
    def _series_version(row: Mapping[str, Any]) -> int:
        version = (row.get("metrics") or {}).get("metrics_version")
        return version if isinstance(version, int) and not isinstance(version, bool) else 0

    @staticmethod
    def _series_order(row: Mapping[str, Any]) -> tuple[int, str]:
        ts = row.get("recorded_at_ms")
        return (ts if isinstance(ts, int) else 0, str(row.get("point_id")))

    def _series_rows_unlocked(self, path: Path) -> list[dict[str, Any]]:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        rows = []
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("schema") == SERIES_SCHEMA and isinstance(row.get("metrics"), dict):
                rows.append(row)
        return rows

    def _rewrite_series_unlocked(self, path: Path, by_id: Mapping[str, Mapping[str, Any]]) -> None:
        ordered = sorted(by_id.values(), key=self._series_order)
        tmp = path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write("".join(self._series_line(row) + "\n" for row in ordered))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)

    def append_series(self, episode_id: str, rows: list[Mapping[str, Any]]) -> None:
        """Upsert metrics rows (point_id, recorded_at_ms, metrics): one row per point, kept in time
        order. The common case (a new point newer than the file's last row) is a plain append; a
        metrics-version upgrade or an older row rewrites the file sorted. A row never replaces
        one of a higher metrics version."""
        if not rows:
            return
        path = self._series_path(episode_id)
        entries = [{"schema": SERIES_SCHEMA, "point_id": _point_id(row.get("point_id")),
                    "step": row.get("step"), "recorded_at_ms": row.get("recorded_at_ms"),
                    "metrics": dict(row.get("metrics") or {})} for row in rows]
        with self._series_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            existing = self._series_rows_unlocked(path)
            by_id: dict[str, dict[str, Any]] = {}
            for row in existing:
                prior = by_id.get(str(row.get("point_id")))
                if prior is None or self._series_version(row) >= self._series_version(prior):
                    by_id[str(row.get("point_id"))] = row
            last = max((self._series_order(r) for r in existing), default=None)
            entries.sort(key=self._series_order)
            plain = (len(by_id) == len(existing)
                     and all(e["point_id"] not in by_id for e in entries)
                     and (last is None or self._series_order(entries[0]) > last))
            if plain:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write("".join(self._series_line(e) + "\n" for e in entries))
                return
            for e in entries:
                prior = by_id.get(e["point_id"])
                if prior is None or self._series_version(e) >= self._series_version(prior):
                    by_id[e["point_id"]] = e
            self._rewrite_series_unlocked(path, by_id)

    def normalize_series(self, episode_id: str, *, backup_dir: Path | None = None) -> dict[str, int]:
        """One-time repair of a series file written by the old append: duplicates removed (the
        highest metrics version wins, then the last written), rows sorted by time. A copy of the
        original goes to backup_dir first. Idempotent: a clean file is left untouched."""
        path = self._series_path(episode_id)
        with self._series_lock:
            existing = self._series_rows_unlocked(path)
            by_id: dict[str, dict[str, Any]] = {}
            for row in existing:
                prior = by_id.get(str(row.get("point_id")))
                if prior is None or self._series_version(row) >= self._series_version(prior):
                    by_id[str(row.get("point_id"))] = row
            ordered = [self._series_order(r) for r in existing]
            clean = len(by_id) == len(existing) and ordered == sorted(ordered)
            if clean or not existing:
                return {"rows": len(existing), "removed": 0, "rewritten": 0}
            if backup_dir is not None:
                backup_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup_dir / path.name)
            self._rewrite_series_unlocked(path, by_id)
            return {"rows": len(existing), "removed": len(existing) - len(by_id), "rewritten": 1}

    def read_series(self, episode_id: str) -> dict[str, dict[str, Any]]:
        """point_id -> series row; a damaged line is skipped, the newest entry for a point wins."""
        path = self._series_path(episode_id)
        out: dict[str, dict[str, Any]] = {}
        with self._series_lock:
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return out
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("schema") == SERIES_SCHEMA and isinstance(row.get("metrics"), dict):
                out[str(row.get("point_id"))] = row
        return out

    def compact(self, now_ms: int, *, retention: Mapping[str, int] | None = None,
                series_current: Any = None) -> dict[str, Any]:
        """Thin, fold and drop by RETENTION_DEFAULTS. A point's full file is only removed once
        its metrics row is in the series file; t0 and latest of every policy, and any delta
        base still in use, are never removed. Index first, files after, so a crash can only
        leave an orphan file (removed on the next pass), never a dangling reference."""
        now_ms = _clock(now_ms, "invalid_now_ms")
        policy = {**RETENTION_DEFAULTS, **dict(retention or {})}
        report = {"thinned": 0, "folded_rows": 0, "dropped_episodes": [], "deleted_files": 0, "freed_bytes": 0}
        with store._APPEND_LOCK, store._exclusive_file_lock(self.index_path):
            state = self._read_state_unlocked()
            next_state = deepcopy(state)
            episodes = next_state["episodes"]
            # 1. drop policies ended long ago
            for episode_id in list(next_state["order"]):
                episode = episodes.get(episode_id)
                ended = episode.get("stopped_at_ms") if isinstance(episode, Mapping) else None
                if (isinstance(episode, Mapping) and episode.get("status") in {STOPPED, EXPIRED}
                        and isinstance(ended, int) and now_ms - ended > policy["drop_after_ms"]):
                    del episodes[episode_id]
                    next_state["order"].remove(episode_id)
                    for key in [k for k, r in next_state["request_keys"].items() if r.get("episode_id") == episode_id]:
                        del next_state["request_keys"][key]
                    report["dropped_episodes"].append(episode_id)
            # 2. thin and fold
            for episode_id in next_state["order"]:
                episode = episodes[episode_id]
                try:
                    self._validate_episode_index(episode)
                except LockedCandidateError:
                    continue
                series = self.read_series(episode_id)
                keep = self._retained_point_ids(episode, now_ms, policy)
                for row in episode["observations"]:
                    if (row.get("kind") == POINT_KIND and not row.get("thinned")
                            and row["point_id"] not in keep and row["point_id"] in series
                            and (series_current is None or series_current(series[row["point_id"]]))):
                        row["thinned"] = True
                        report["thinned"] += 1
                ended = episode.get("stopped_at_ms")
                if (episode.get("status") in {STOPPED, EXPIRED} and isinstance(ended, int)
                        and now_ms - ended > policy["collapse_after_ms"]):
                    kept_rows = [row for row in episode["observations"]
                                 if not (row.get("kind") == POINT_KIND and row.get("thinned"))]
                    folded = len(episode["observations"]) - len(kept_rows)
                    if folded:
                        for index, row in enumerate(kept_rows):
                            row.setdefault("original_step", row["step"])
                            row["step"] = index
                        episode["observations"] = kept_rows
                        collapsed = episode.setdefault("collapsed", {"rows": 0, "points": 0})
                        collapsed["rows"] = int(collapsed.get("rows") or 0) + folded
                        collapsed["points"] = int(collapsed.get("points") or 0) + folded
                        collapsed["at_ms"] = now_ms
                        report["folded_rows"] += folded
            try:
                referenced = self._referenced_point_ids(next_state)
            except LockedCandidateError:
                referenced = None  # a kept file could not be read: delete nothing this pass
            # No byte-budget check here: compaction is how an exhausted budget is relieved,
            # and the index only changes by a few flags while the files below are removed.
            _atomic_write_bytes(self.index_path, _state_bytes(next_state))
            # 3. remove files nothing references any more (thinned, dropped, or orphaned)
            if referenced is not None and self.points_dir.exists():
                for path in self.points_dir.glob("*.json.gz"):
                    point_id = path.name[:-len(".json.gz")]
                    if point_id in referenced:
                        continue
                    try:
                        size = path.stat().st_size
                        path.unlink()
                        report["deleted_files"] += 1
                        report["freed_bytes"] += size
                    except OSError:
                        continue
            for episode_id in report["dropped_episodes"]:
                try:
                    self._series_path(episode_id).unlink()
                except (OSError, LockedCandidateError):
                    pass
        with self._cache_lock:
            self._point_meta_cache.clear()
            self._point_verified_cache.clear()
        return report

    @staticmethod
    def _retained_point_ids(episode: Mapping[str, Any], now_ms: int, policy: Mapping[str, int]) -> set[str]:
        rows = [row for row in episode["observations"] if row.get("kind") == POINT_KIND]
        keep = {episode["t0_baseline_point_id"], episode["latest_point_id"]}
        ended = episode.get("stopped_at_ms") if episode.get("status") in {STOPPED, EXPIRED} else None
        if isinstance(ended, int):
            since_end = now_ms - ended
            bucket = (policy["dense_bucket_ms"] if since_end <= policy["dense_until_ms"]
                      else policy["sparse_bucket_ms"] if since_end <= policy["sparse_until_ms"] else None)
            newest = None
        else:
            bucket = policy["dense_bucket_ms"]
            newest = max((int(row.get("recorded_at_ms") or 0) for row in rows), default=now_ms)
        seen: set[int] = set()
        for row in rows:
            at = int(row.get("recorded_at_ms") or 0)
            if newest is not None and newest - at <= policy["full_window_ms"]:
                keep.add(row["point_id"])
                continue
            if bucket is None:
                continue
            slot = at // bucket
            if slot not in seen:
                seen.add(slot)
                keep.add(row["point_id"])
        return keep

    def _referenced_point_ids(self, state: Mapping[str, Any]) -> set[str]:
        """Every point file the index still needs, including the full base of a delta t0."""
        referenced: set[str] = set()
        for episode in state["episodes"].values():
            if not isinstance(episode, Mapping):
                continue
            for key in ("t0_baseline_point_id", "latest_point_id"):
                if isinstance(episode.get(key), str):
                    referenced.add(episode[key])
            for row in episode.get("observations") or []:
                if isinstance(row, Mapping) and row.get("kind") == POINT_KIND and not row.get("thinned"):
                    referenced.add(str(row.get("point_id")))
        for request in state["request_keys"].values():
            if isinstance(request, Mapping) and isinstance(request.get("point_id"), str) and request.get("episode_id") in state["episodes"]:
                episode = state["episodes"][request["episode_id"]]
                if isinstance(episode, Mapping) and request["point_id"] in {episode.get("t0_baseline_point_id"), episode.get("latest_point_id")}:
                    referenced.add(request["point_id"])
        # Point files are content-addressed and may be shared between policies, so a kept
        # file can be a delta on another policy's point: keep the base of every kept delta.
        return referenced | {base for point_id in referenced
                             if (base := self._delta_base_id(point_id)) is not None}

    def _delta_base_id(self, point_id: str) -> str | None:
        """Base of a stored delta, read from the file head: a canonical delta envelope
        starts with its sorted first key, base_point_id. A missing or corrupt file has no
        usable base; any other read failure raises, so the caller deletes nothing."""
        try:
            path = self._point_path(_point_id(point_id))
        except LockedCandidateError:
            return None
        try:
            with gzip.open(path, "rb") as handle:
                head = handle.read(96)
        except (FileNotFoundError, EOFError, gzip.BadGzipFile, zlib.error):
            return None
        except OSError as exc:
            raise LockedCandidateError("point_read_failed", str(exc)) from exc
        match = re.match(rb'\{"base_point_id":"([0-9a-f]{64})"', head)
        return match.group(1).decode("ascii") if match else None

    def read_point(self, point_id: str) -> dict[str, Any]:
        """One retained point, hash-checked (delta points are reconstructed)."""
        return self._read_point(point_id, include_point=True)["point"]

    def point_read_signature(self, point_id: str) -> dict[str, Any]:
        point_id = _point_id(point_id)
        stat = self._point_file_stat(point_id)
        cached = self._cached_full(point_id, stat)
        if cached is None:
            cached = self._read_point(point_id, include_point=False)
        return {
            "point": {
                "point_id": point_id,
                "stat": list(stat),
                "storage": cached.get("storage") or "full",
            },
            "dependencies": deepcopy(cached.get("dependencies") or []),
        }

    def _read_state(self) -> dict[str, Any]:
        return self._read_state_unlocked()

    def _read_state_unlocked(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return _empty_state()
        try:
            state = store.read_json(self.index_path)
        except Exception as exc:  # noqa: BLE001
            raise LockedCandidateError("index_read_failed", str(exc)) from exc
        if state.get("schema") != SCHEMA or not isinstance(state.get("episodes"), dict):
            raise LockedCandidateError("index_schema_mismatch", "unsupported locked candidate index schema")
        state.setdefault("order", list(state["episodes"]))
        state.setdefault("request_keys", {})
        if not isinstance(state["order"], list) or not isinstance(state["request_keys"], dict):
            raise LockedCandidateError("index_schema_mismatch", "unsupported locked candidate index shape")
        for key, request in state["request_keys"].items():
            _request_key(key)
            if not isinstance(request, Mapping):
                raise LockedCandidateError("index_integrity_error", "request key entry is invalid")
            _point_id(request.get("point_id"))
            if "candidate_id" in request:
                _text(request.get("candidate_id"), "invalid_candidate_id")
            if "snapshot_id" in request:
                _text(request.get("snapshot_id"), "invalid_snapshot_id")
        for episode_id in state["order"]:
            if episode_id not in state["episodes"]:
                raise LockedCandidateError("index_integrity_error", "episode order references missing episode")
        return state

    def _save_point_and_state_unlocked(self, checked: Mapping[str, Any], state: Mapping[str, Any]) -> None:
        self._check_point_size(checked)
        point_exists = self._point_path(checked["point_id"]).exists()
        state_data = _state_bytes(state)
        extra = 0 if point_exists else len(checked["gz"])
        self._check_total_budget(state_data, extra + self._control_reserve_bytes)
        self._ensure_point_file(checked)
        _atomic_write_bytes(self.index_path, state_data)

    def _save_state_with_budget_unlocked(self, state: Mapping[str, Any], *, extra_point_bytes: int) -> None:
        state_data = _state_bytes(state)
        self._check_total_budget(state_data, extra_point_bytes)
        _atomic_write_bytes(self.index_path, state_data)

    def _point_write_plan(self, checked: Mapping[str, Any],
                          episode: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if self._point_path(checked["point_id"]).exists():
            return {"exists": True, "kind": "existing", "gz": b"", "base_point_id": None}
        if episode is not None and checked["point_id"] != episode["t0_baseline_point_id"]:
            delta = self._delta_write_plan(checked, episode["t0_baseline_point_id"])
            if delta is not None and len(delta["gz"]) < len(checked["gz"]):
                return delta
        return {"exists": False, "kind": "full", "gz": checked["gz"], "base_point_id": None}

    def _delta_write_plan(self, checked: Mapping[str, Any], base_point_id: str) -> dict[str, Any] | None:
        base_point_id = _point_id(base_point_id)
        base_stat = self._point_file_stat(base_point_id)
        with self._cache_lock:
            cached = self._delta_base_cache.get(base_point_id)
        if cached is not None and cached[0] == base_stat:
            base_checked = cached[1]
        else:
            base_payload, base_stat = self._read_stored_json(base_point_id)
            if _is_delta_envelope(base_payload):
                return None
            base_checked = validate_point(base_payload)
            if base_checked["point_id"] != base_point_id:
                raise LockedCandidateError("point_hash_mismatch", "stored base point validates to a different point_id")
            with self._cache_lock:
                self._delta_base_cache[base_point_id] = (base_stat, base_checked)
                while len(self._delta_base_cache) > 4:
                    self._delta_base_cache.pop(next(iter(self._delta_base_cache)))
        if (base_checked["candidate_id"] != checked["candidate_id"]
                or base_checked["contract_key"] != checked["contract_key"]):
            return None
        sets, deletes = _build_delta_patch(base_checked["point"], checked["point"])
        envelope = {
            "schema": POINT_DELTA_SCHEMA,
            "point_id": checked["point_id"],
            "base_point_id": base_point_id,
            "candidate_id": checked["candidate_id"],
            "contract_key": checked["contract_key"],
            "patch": {"set": sets, "delete": deletes},
        }
        reconstructed = _apply_delta_patch(base_checked["point"], envelope["patch"])
        # Same content hash as the point just validated: validating it again proves nothing new.
        if _hash(reconstructed) != checked["point_id"]:
            return None
        raw = _canonical_bytes(envelope)
        gz = gzip.compress(raw, compresslevel=9, mtime=0)
        return {"exists": False, "kind": "delta", "gz": gz, "base_point_id": base_point_id}

    def _ensure_point_file(self, checked: Mapping[str, Any],
                           write_plan: Mapping[str, Any] | None = None) -> None:
        path = self._point_path(checked["point_id"])
        if path.exists():
            existing = self._read_point(checked["point_id"], include_point=True)
            if store._canonical(existing["point"]) != store._canonical(checked["point"]):
                raise LockedCandidateError("point_hash_collision", "different point already exists for point_id")
            return
        plan = write_plan or {"gz": checked["gz"]}
        _atomic_write_bytes(path, plan["gz"])

    def _read_point(self, point_id: str, *, include_point: bool = False) -> dict[str, Any]:
        point_id = _point_id(point_id)
        stored, stat = self._read_stored_json(point_id)
        dependencies: list[dict[str, Any]] = []
        storage_kind = "full"
        if _is_delta_envelope(stored):
            point, dependencies = self._reconstruct_delta_point(point_id, stored)
            storage_kind = "delta"
        else:
            point = stored
        if _hash(point) != point_id:
            raise LockedCandidateError("point_hash_mismatch", "stored point content hash does not match filename")
        cached = self._cached_full(point_id, stat)
        if cached is None:
            cached = validate_point(point)
            if cached["point_id"] != point_id:
                raise LockedCandidateError("point_hash_mismatch", "stored point validates to a different point_id")
            cached["storage"] = storage_kind
            cached["dependencies"] = dependencies
            self._remember_full(point_id, stat, cached)
        result = dict(cached)
        if include_point:
            result["point"] = point
        return result

    def _read_stored_json(self, point_id: str) -> tuple[Any, tuple[int, int, int]]:
        point_id = _point_id(point_id)
        path = self._point_path(point_id)
        stat = self._point_file_stat(point_id)
        try:
            with gzip.open(path, "rb") as handle:
                raw = handle.read(self.max_point_bytes + 1)
        except Exception as exc:  # noqa: BLE001
            raise LockedCandidateError("point_read_failed", str(exc)) from exc
        if len(raw) > self.max_point_bytes:
            raise LockedCandidateError("point_byte_budget_exhausted", "stored point exceeds byte budget")
        try:
            return store.json.loads(raw.decode("utf-8-sig")), stat
        except Exception as exc:  # noqa: BLE001
            raise LockedCandidateError("point_json_invalid", str(exc)) from exc

    def _reconstruct_delta_point(self, point_id: str, envelope: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if envelope.get("point_id") != point_id:
            raise LockedCandidateError("point_hash_mismatch", "delta envelope point_id does not match filename")
        base_point_id = _point_id(envelope.get("base_point_id"))
        if base_point_id == point_id:
            raise LockedCandidateError("delta_cycle", "delta point cannot reference itself")
        try:
            base_payload, base_stat = self._read_stored_json(base_point_id)
        except LockedCandidateError as exc:
            if exc.code == "point_missing":
                raise LockedCandidateError("delta_base_missing", "delta base point is missing") from exc
            raise
        if _is_delta_envelope(base_payload):
            raise LockedCandidateError("delta_base_not_full", "delta base point must be a full point")
        if _hash(base_payload) != base_point_id:
            raise LockedCandidateError("delta_base_hash_mismatch", "delta base content hash does not match filename")
        base_checked = validate_point(base_payload)
        if base_checked["point_id"] != base_point_id:
            raise LockedCandidateError("delta_base_hash_mismatch", "delta base validates to a different point_id")
        point = _apply_delta_patch(base_checked["point"], envelope.get("patch"))
        if _hash(point) != point_id:
            raise LockedCandidateError("point_hash_mismatch", "delta patch does not reconstruct the stored point_id")
        checked = validate_point(point)
        if (checked["candidate_id"] != base_checked["candidate_id"]
                or checked["contract_key"] != base_checked["contract_key"]):
            raise LockedCandidateError("delta_base_mismatch", "delta base belongs to a different candidate or contract")
        return point, [{"role": "base_point", "point_id": base_point_id,
                        "point_hash": base_point_id, "stat": list(base_stat)}]

    def _point_path(self, point_id: str) -> Path:
        point_id = _point_id(point_id)
        return self.points_dir / (point_id + ".json.gz")

    def _point_file_stat(self, point_id: str) -> tuple[int, int, int]:
        path = self._point_path(point_id)
        try:
            stat = path.stat()
        except OSError as exc:
            raise LockedCandidateError("point_missing", str(exc)) from exc
        return stat.st_size, stat.st_mtime_ns, getattr(stat, "st_ctime_ns", 0)

    def _check_point_file_light(self, point_id: str) -> dict[str, Any]:
        point_id = _point_id(point_id)
        stat = self._point_file_stat(point_id)
        cached = self._cached_meta(point_id, stat)
        if cached is not None:
            return cached
        meta = {"point_id": point_id, "compressed_bytes": stat[0], "mtime_ns": stat[1],
                "ctime_ns": stat[2]}
        self._remember_meta(point_id, stat, meta)
        return meta

    def _cached_meta(self, point_id: str, stat: tuple[int, int, int]) -> dict[str, Any] | None:
        with self._cache_lock:
            cached = self._point_meta_cache.get(point_id)
            return deepcopy(cached[1]) if cached and cached[0] == stat else None

    def _remember_meta(self, point_id: str, stat: tuple[int, int, int], meta: Mapping[str, Any]) -> None:
        with self._cache_lock:
            self._point_meta_cache[point_id] = (stat, deepcopy(dict(meta)))
            while len(self._point_meta_cache) > 256:
                self._point_meta_cache.pop(next(iter(self._point_meta_cache)))

    def _cached_full(self, point_id: str, stat: tuple[int, int, int]) -> dict[str, Any] | None:
        with self._cache_lock:
            cached = self._point_verified_cache.get(point_id)
            if not cached or cached[0] != stat:
                return None
            body = cached[1]
            dependencies = body.get("dependencies") or []
        for item in dependencies:
            if not isinstance(item, Mapping) or item.get("role") != "base_point":
                return None
            base_id = item.get("point_id")
            expected = item.get("stat")
            if not isinstance(expected, list):
                return None
            try:
                actual = list(self._point_file_stat(base_id))
            except LockedCandidateError:
                return None
            if actual != expected:
                return None
        return deepcopy(body)

    def _remember_full(self, point_id: str, stat: tuple[int, int, int], checked: Mapping[str, Any]) -> None:
        compact = {key: deepcopy(checked[key]) for key in (
            "schema", "point_id", "candidate_id", "contract_key",
            "snapshot_id", "decision_asof_ms", "recorded_at_ms",
            "storage", "dependencies"
        )}
        with self._cache_lock:
            self._point_verified_cache[point_id] = (stat, compact)
            while len(self._point_verified_cache) > 64:
                self._point_verified_cache.pop(next(iter(self._point_verified_cache)))

    def _validate_episode_index(self, episode: Mapping[str, Any]) -> None:
        if episode.get("schema") != SCHEMA or episode.get("status") not in _VALID_STATUSES:
            raise LockedCandidateError("index_integrity_error", "episode schema or status is invalid")
        _point_id(episode.get("t0_baseline_point_id"))
        _point_id(episode.get("latest_point_id"))
        observations = episode.get("observations")
        if not isinstance(observations, list) or not observations:
            raise LockedCandidateError("index_integrity_error", "episode observations are invalid")
        point_ids: set[str] = set()
        stored_gaps = 0
        for index, row in enumerate(observations):
            if not isinstance(row, Mapping) or row.get("step") != index:
                raise LockedCandidateError("index_integrity_error", "observation step sequence is invalid")
            kind = row.get("kind")
            if kind == POINT_KIND:
                point_ids.add(_point_id(row.get("point_id")))
            elif kind == GAP_KIND:
                stored_gaps += 1
            else:
                raise LockedCandidateError("index_integrity_error", "observation kind is invalid")
        if episode["t0_baseline_point_id"] not in point_ids or episode["latest_point_id"] not in point_ids:
            raise LockedCandidateError("index_integrity_error", "episode point references are invalid")
        if stored_gaps > MAX_STORED_GAPS_PER_EPISODE:
            raise LockedCandidateError("index_integrity_error", "stored gap observations exceed limit")
        return None

    def _summary_or_diagnostic(self, episode_id: str, episode: Any) -> dict[str, Any]:
        try:
            if not isinstance(episode, Mapping):
                raise LockedCandidateError("index_integrity_error", "episode entry is invalid")
            return self._summary(episode)
        except LockedCandidateError as exc:
            body = episode if isinstance(episode, Mapping) else {}
            return {
                "episode_id": str(body.get("episode_id") or episode_id),
                "status": ERROR,
                "candidate_id": body.get("candidate_id"),
                "contract_key": body.get("contract_key"),
                "contract": deepcopy(body.get("contract")) if isinstance(body.get("contract"), Mapping) else None,
                "source_episode_id": body.get("source_episode_id"),
                "created_at_ms": body.get("created_at_ms"),
                "updated_at_ms": body.get("updated_at_ms"),
                "last_attempt_at_ms": body.get("last_attempt_at_ms"),
                "stopped_at_ms": body.get("stopped_at_ms"),
                "stop_reason": body.get("stop_reason"),
                "t0_baseline_point_id": body.get("t0_baseline_point_id"),
                "latest_point_id": body.get("latest_point_id"),
                "point_count": 0,
                "gap_count": 0,
                "stored_gap_count": 0,
                "gap_storage_truncated": False,
                "last_gap_reason": body.get("last_gap_reason"),
                "index_error": {"code": exc.code, "message": exc.message},
            }

    def _request_candidate_snapshot(self, state: Mapping[str, Any],
                                    request: Mapping[str, Any]) -> tuple[str, str]:
        candidate_id = request.get("candidate_id")
        snapshot_id = request.get("snapshot_id")
        if candidate_id is not None and snapshot_id is not None:
            return candidate_id, snapshot_id
        episode = self._episode(state, request["episode_id"])
        summary = self._summary(episode)
        return episode["candidate_id"], summary["baseline_snapshot_id"]

    def _total_bytes(self) -> int:
        if not self.root.exists():
            return 0
        total = 0
        for path in self.root.rglob("*"):
            if path.is_file() and not path.name.endswith((".lock", ".tmp")):
                total += path.stat().st_size
        return total

    def _check_total_budget(self, next_index: bytes, extra_point_bytes: int) -> None:
        current_index = self.index_path.stat().st_size if self.index_path.exists() else 0
        projected = self._total_bytes() - current_index + len(next_index) + extra_point_bytes
        if projected > self.max_bytes:
            raise LockedCandidateError("byte_budget_exhausted", "locked candidate byte budget exhausted")

    def _check_point_size(self, checked: Mapping[str, Any]) -> None:
        if len(checked["raw"]) > self.max_point_bytes:
            raise LockedCandidateError("point_byte_budget_exhausted", "single point exceeds max_point_bytes")

    def _budget_gap_reason(self, state: Mapping[str, Any], checked: Mapping[str, Any],
                           write_plan: Mapping[str, Any] | None = None) -> str | None:
        if self._point_count(state) >= self.max_samples:
            return "sample_budget_exhausted"
        if len(checked["raw"]) > self.max_point_bytes:
            return "point_byte_budget_exhausted"
        if self._point_path(checked["point_id"]).exists():
            return None
        point_bytes = len((write_plan or {}).get("gz") or checked["gz"])
        if self._total_bytes() + point_bytes > self.max_bytes:
            return "byte_budget_exhausted"
        return None

    @staticmethod
    def _point_count(state: Mapping[str, Any]) -> int:
        total = 0
        for episode in state["episodes"].values():
            if not isinstance(episode, Mapping):
                continue
            rows = episode.get("observations")
            if not isinstance(rows, list):
                continue
            total += sum(1 for row in rows if isinstance(row, Mapping) and row.get("kind") == POINT_KIND
                         and not row.get("thinned"))
        return total

    @staticmethod
    def _point_event(checked: Mapping[str, Any], now_ms: int, *, step: int) -> dict[str, Any]:
        return {"kind": POINT_KIND, "step": step, "point_id": checked["point_id"],
                "snapshot_id": checked["snapshot_id"], "candidate_id": checked["candidate_id"],
                "recorded_at_ms": checked["recorded_at_ms"],
                "decision_asof_ms": checked["decision_asof_ms"], "added_at_ms": now_ms,
                "gap": None}

    @staticmethod
    def _gap_in_state(episode: dict[str, Any], now_ms: int, reason: str, *,
                      status: str, expired: bool = False) -> None:
        stats = episode.setdefault("gap_stats", {"count": 0, "by_reason": {}, "first": None,
                                                 "last": None, "max_gap": None,
                                                 "stored_count": 0})
        gap = {"reason": reason, "recorded_at_ms": now_ms, "expired": bool(expired)}
        stats["count"] = int(stats.get("count") or 0) + 1
        by_reason = stats.setdefault("by_reason", {})
        by_reason[reason] = int(by_reason.get(reason) or 0) + 1
        stats["first"] = stats.get("first") or deepcopy(gap)
        stats["last"] = deepcopy(gap)
        max_reason, max_count = max(by_reason.items(), key=lambda item: (item[1], item[0]))
        stats["max_gap"] = {"reason": max_reason, "count": max_count}
        stored_count = len([row for row in episode.get("observations", []) if row.get("kind") == GAP_KIND])
        if stored_count < MAX_STORED_GAPS_PER_EPISODE:
            episode["observations"].append({"kind": GAP_KIND, "step": len(episode["observations"]),
                                            "recorded_at_ms": now_ms, "decision_asof_ms": None,
                                            "point_id": None, "snapshot_id": None,
                                            "gap": {"reason": reason, "expired": bool(expired)}})
            stored_count += 1
        stats["stored_count"] = stored_count
        if stored_count >= MAX_STORED_GAPS_PER_EPISODE and status == ACTIVE and not expired:
            status = PAUSED
            reason = "gap_storage_exhausted"
        episode["status"] = status
        episode["last_attempt_at_ms"] = now_ms
        episode["updated_at_ms"] = now_ms
        episode["last_gap_reason"] = reason

    @staticmethod
    def _require_appendable(episode: Mapping[str, Any]) -> None:
        if episode["status"] != ACTIVE:
            raise LockedCandidateError("episode_not_active", "episode is not accepting observations")

    def _episode(self, state: Mapping[str, Any], episode_id: str) -> dict[str, Any]:
        try:
            episode = state["episodes"][episode_id]
        except KeyError as exc:
            raise LockedCandidateError("episode_not_found", "locked candidate episode not found") from exc
        self._validate_episode_index(episode)
        return episode

    def _latest_active_for_contract(self, state: Mapping[str, Any], contract_key: str) -> dict[str, Any] | None:
        for episode_id in reversed(state["order"]):
            episode = state["episodes"][episode_id]
            try:
                self._validate_episode_index(episode)
            except LockedCandidateError:
                continue
            if episode["contract_key"] == contract_key and episode["status"] == ACTIVE:
                return episode
        return None

    def _summary(self, episode: Mapping[str, Any]) -> dict[str, Any]:
        self._validate_episode_index(episode)
        points = [row for row in episode.get("observations", []) if row.get("kind") == POINT_KIND]
        gaps = [row for row in episode.get("observations", []) if row.get("kind") == GAP_KIND]
        latest_point = points[-1]
        gap_stats = episode.get("gap_stats") or {}
        gap_count = int(gap_stats.get("count") if gap_stats else len(gaps))
        stored_gap_count = int(gap_stats.get("stored_count") if gap_stats else len(gaps))
        return {
            "episode_id": episode["episode_id"],
            "status": episode["status"],
            "candidate_id": episode["candidate_id"],
            "contract_key": episode["contract_key"],
            "contract": deepcopy(episode["contract"]),
            "source_episode_id": episode.get("source_episode_id"),
            "created_at_ms": episode["created_at_ms"],
            "updated_at_ms": episode["updated_at_ms"],
            "last_attempt_at_ms": episode["last_attempt_at_ms"],
            "stopped_at_ms": episode.get("stopped_at_ms"),
            "stop_reason": episode.get("stop_reason"),
            "t0_baseline_point_id": episode["t0_baseline_point_id"],
            "latest_point_id": episode["latest_point_id"],
            "baseline_snapshot_id": points[0]["snapshot_id"],
            "latest_snapshot_id": latest_point["snapshot_id"],
            "baseline_recorded_at_ms": points[0]["recorded_at_ms"],
            "baseline_decision_asof_ms": points[0]["decision_asof_ms"],
            "latest_recorded_at_ms": latest_point["recorded_at_ms"],
            "latest_decision_asof_ms": latest_point["decision_asof_ms"],
            "point_count": len(points) + int((episode.get("collapsed") or {}).get("points") or 0),
            "retained_point_count": sum(1 for row in points if not row.get("thinned")),
            "gap_count": gap_count,
            "stored_gap_count": stored_gap_count,
            "first_gap": deepcopy(gap_stats.get("first")),
            "last_gap": deepcopy(gap_stats.get("last")),
            "max_gap": deepcopy(gap_stats.get("max_gap")),
            "gap_storage_truncated": gap_count > stored_gap_count,
            "last_gap_reason": episode.get("last_gap_reason"),
        }
