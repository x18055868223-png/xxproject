"""Lightweight data identity and cache layer for Astra v2.3.

This module standardizes records that existing FMZ/server collectors already
obtain. It is intentionally small: current cache, bounded health events, usage
qualification, singleflight fetch, and immutable freeze manifests.
"""

from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import math
import os
import re
import threading
import time
from typing import Any, Callable, Mapping


ENGINE_SCHEMA = "astra_data_engine@2.3.0"
RECORD_SCHEMA = "astra_data_record@2.3.0"
FREEZE_SCHEMA = "astra_data_freeze_manifest@2.3.0"
REGISTRY_SCHEMA = "astra_data_product_registry@2.3.0"
RULE_VERSION = "astra_data_engine_rules@2.3.0"
NORMALIZER_VERSION = "astra_data_engine_normalizer@2.3.0"
STATE_FILE = "data_engine_state_v23.json"
DEFAULT_HEALTH_EVENT_LIMIT = 80
MAX_KNOWN_RECORDS = 512
MAX_CURRENT_SCOPES_PER_PRODUCT = 128
STATE_LOCK_TIMEOUT_S = 5.0
STALE_LOCK_MS = 30_000
DEFAULT_TTL_MS = 300_000
DEFAULT_FETCH_BUDGET_PER_MINUTE = 12
CLOCK_SKEW_MS = 5_000

DATA_OK_STATES = {"OK", "PARTIAL"}
DATA_BAD_STATES = {"STALE", "INVALID", "MISSING", "ERROR", "PLANNED_NOT_CONNECTED"}
QUALITY_STATES = DATA_OK_STATES | DATA_BAD_STATES

SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|secret|password|passwd|private[_-]?key|authorization|"
    r"bearer|cookie|credential|token|signature)",
    re.IGNORECASE,
)
SECRET_VALUE_RE = re.compile(
    r"(-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[A-Za-z0-9_\-]{16,}|"
    r"Bearer\s+[A-Za-z0-9_\-.]{12,})",
    re.IGNORECASE,
)


class DataEngineError(ValueError):
    """Raised when a data record would violate the v2.3 data contract."""

    def __init__(self, code: str, message: str, *, detail: Any | None = None):
        super().__init__(message)
        self.code = code
        self.detail = detail

    def as_dict(self) -> dict[str, Any]:
        out = {"code": self.code, "message": str(self)}
        if self.detail is not None:
            out["detail"] = deepcopy(self.detail)
        return out


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_registry_path() -> Path:
    repo_registry = _repo_root() / "demo" / "shared" / "data_product_registry_v23.json"
    if repo_registry.exists():
        return repo_registry
    return Path(__file__).resolve().with_name("data_product_registry_v23.json")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _read_json(path: Path, default: Mapping[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return dict(default)
    return dict(value) if isinstance(value, Mapping) else dict(default)


def _read_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise DataEngineError("REGISTRY_MISSING", f"data product registry is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise DataEngineError("REGISTRY_INVALID", f"data product registry is not valid JSON: {path}") from exc
    except OSError as exc:
        raise DataEngineError("REGISTRY_UNREADABLE", f"data product registry cannot be read: {path}") from exc
    if not isinstance(value, Mapping):
        raise DataEngineError("REGISTRY_INVALID", "data product registry must be a JSON object")
    registry = dict(value)
    if registry.get("schema") != REGISTRY_SCHEMA:
        raise DataEngineError("REGISTRY_INVALID", f"data product registry schema must be {REGISTRY_SCHEMA}")
    products = _registry_products(registry)
    if not products:
        raise DataEngineError("REGISTRY_EMPTY", "data product registry has no products")
    return registry


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(_canonical(dict(value)) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _require_int_ms(name: str, value: Any, *, required: bool = True) -> int | None:
    if value is None:
        if required:
            raise DataEngineError("MISSING_TIME", f"{name} is required")
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise DataEngineError("INVALID_TIME", f"{name} must be an integer millisecond timestamp")
    if value < 0:
        raise DataEngineError("INVALID_TIME", f"{name} must be non-negative")
    return int(value)


def _scope_key(scope: Any) -> str:
    if scope is None:
        return "__global__"
    if isinstance(scope, str):
        return scope
    return "scope:" + _hash(scope)[:20]


def _state_key(product_id: str, scope: Any) -> str:
    return product_id + "|" + _scope_key(scope)


def _assert_safe_value(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if SECRET_KEY_RE.search(key_text):
                raise DataEngineError("SECRET_FIELD_REJECTED", f"secret-like field rejected at {path}.{key_text}")
            _assert_safe_value(item, f"{path}.{key_text}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_safe_value(item, f"{path}[{index}]")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DataEngineError("NON_FINITE_VALUE", f"non-finite numeric value rejected at {path}")
        return
    if isinstance(value, str):
        if SECRET_VALUE_RE.search(value):
            raise DataEngineError("SECRET_VALUE_REJECTED", f"secret-like value rejected at {path}")
        return
    if value is None or isinstance(value, (bool, int)):
        return
    raise DataEngineError("UNSUPPORTED_VALUE", f"unsupported value type at {path}: {type(value).__name__}")


def _sanitize_reason_codes(reason_codes: Any) -> list[str]:
    if reason_codes is None:
        return []
    if not isinstance(reason_codes, list):
        raise DataEngineError("INVALID_REASON_CODES", "reason_codes must be a list")
    out: list[str] = []
    for item in reason_codes:
        if not isinstance(item, str) or not item:
            raise DataEngineError("INVALID_REASON_CODES", "reason_codes must contain non-empty strings")
        if SECRET_VALUE_RE.search(item) or SECRET_KEY_RE.search(item):
            raise DataEngineError("SECRET_FIELD_REJECTED", "reason_codes cannot contain secrets")
        out.append(item)
    return out


def _registry_products(registry: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw = registry.get("products")
    products: dict[str, dict[str, Any]] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, Mapping) and isinstance(item.get("product_id"), str):
                products[item["product_id"]] = dict(item)
    elif isinstance(raw, Mapping):
        for product_id, item in raw.items():
            if isinstance(item, Mapping):
                entry = dict(item)
                entry.setdefault("product_id", str(product_id))
                products[str(product_id)] = entry
    return products


class DataEngine:
    """Small v2.3 cache, qualification, fetch and freeze helper."""

    _fetch_locks_guard = threading.Lock()
    _fetch_locks: dict[str, threading.Lock] = {}

    def __init__(self, state_dir: str | Path, registry_path: str | Path | None = None):
        self.state_dir = Path(state_dir)
        self.registry_path = Path(registry_path) if registry_path is not None else default_registry_path()
        self.state_path = self.state_dir / STATE_FILE
        self.registry = _read_registry(self.registry_path)
        self.products = _registry_products(self.registry)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._state_lock = threading.RLock()

    def ingest(
        self,
        product_id: str,
        values: Mapping[str, Any],
        *,
        observation_end_ms: int,
        retrieved_at_ms: int | None,
        scope: Any = None,
        source_revision: str | None = None,
        published_at_ms: int | None = None,
        observation_start_ms: int | None = None,
        data_state: str = "OK",
        reason_codes: list[str] | None = None,
        parent_record_ids: list[str] | None = None,
        source_ref: str | None = None,
        is_final: bool | None = None,
        historical_import: bool = False,
        ingested_at_ms: int | None = None,
    ) -> dict[str, Any]:
        product = self._product(product_id)
        if not isinstance(values, Mapping):
            raise DataEngineError("INVALID_VALUES", "values must be a mapping")
        _assert_safe_value(values)
        if source_ref is not None:
            _assert_safe_value({"source_ref": source_ref})
        data_state = str(data_state).upper()
        if data_state not in QUALITY_STATES:
            raise DataEngineError("INVALID_DATA_STATE", f"unsupported data_state {data_state}")
        reason_codes = _sanitize_reason_codes(reason_codes)
        parent_record_ids = self._sanitize_record_ids(parent_record_ids)
        retrieved_at_ms = _require_int_ms("retrieved_at_ms", retrieved_at_ms, required=False)
        ingested_at_ms = _require_int_ms("ingested_at_ms", ingested_at_ms, required=False)
        if retrieved_at_ms is None and ingested_at_ms is None:
            raise DataEngineError("MISSING_FIRST_SEEN_TIME", "retrieved_at_ms or ingested_at_ms is required")
        observation_end_ms = _require_int_ms("observation_end_ms", observation_end_ms) or 0
        observation_start_ms = _require_int_ms(
            "observation_start_ms", observation_start_ms, required=False
        )
        published_at_ms = _require_int_ms("published_at_ms", published_at_ms, required=False)
        if observation_start_ms is not None and observation_start_ms > observation_end_ms:
            raise DataEngineError("INVALID_TIME_RANGE", "observation_start_ms cannot exceed observation_end_ms")
        first_seen_source_ms = ingested_at_ms if ingested_at_ms is not None else retrieved_at_ms
        for name, ts in (("observation_end_ms", observation_end_ms), ("published_at_ms", published_at_ms)):
            if ts is not None and first_seen_source_ms is not None and ts > first_seen_source_ms + CLOCK_SKEW_MS:
                raise DataEngineError("SOURCE_TIME_IN_FUTURE", f"{name} is later than retrieval time")
        with self._state_guard():
            state = self._load_state_unlocked()
            scope_key = _scope_key(scope)
            state_key = _state_key(product_id, scope)
            first_seen_at_ms = first_seen_source_ms if first_seen_source_ms is not None else observation_end_ms
            source_ref = source_ref or product.get("source_ref")
            payload_basis = self._payload_basis(
                product,
                values=dict(values),
                scope=scope,
                source_ref=source_ref,
                observation_start_ms=observation_start_ms,
                observation_end_ms=observation_end_ms,
                published_at_ms=published_at_ms,
                source_revision=source_revision,
                is_final=is_final,
                parent_record_ids=parent_record_ids,
                data_state=data_state,
                reason_codes=reason_codes,
            )
            payload_hash = _hash(payload_basis)
            known = state.setdefault("known_records", {}).get(payload_hash)
            if isinstance(known, Mapping):
                first_seen_at_ms = int(known.get("first_seen_at_ms") or first_seen_at_ms)
            record = self._build_record(
                product,
                values=dict(values),
                scope=scope,
                scope_key=scope_key,
                source_ref=source_ref,
                observation_start_ms=observation_start_ms,
                observation_end_ms=observation_end_ms,
                published_at_ms=published_at_ms,
                first_seen_at_ms=first_seen_at_ms,
                retrieved_at_ms=retrieved_at_ms,
                ingested_at_ms=ingested_at_ms,
                source_revision=source_revision,
                is_final=is_final,
                parent_record_ids=parent_record_ids,
                payload_hash=payload_hash,
                data_state=data_state,
                reason_codes=reason_codes,
                historical_import=historical_import,
                accepted_as_current=True,
            )
            current = state.setdefault("current", {}).get(state_key)
            accept = True
            event_status = "CURRENT_ACCEPTED"
            if isinstance(current, Mapping):
                if current.get("version", {}).get("payload_hash") == payload_hash:
                    merged = deepcopy(dict(current))
                    merged.setdefault("collector", {})
                    merged["collector"]["last_checked_at_ms"] = first_seen_source_ms
                    if retrieved_at_ms is not None:
                        merged["collector"]["last_transport_success_at_ms"] = retrieved_at_ms
                    merged["collector"]["accepted_as_current"] = True
                    merged["quality"]["checked_at_ms"] = first_seen_source_ms
                    state["current"][state_key] = merged
                    self._remember_record(state, payload_hash, merged)
                    self._append_event(
                        state,
                        product_id,
                        scope_key,
                        "SAME_PAYLOAD_CHECKED",
                        first_seen_source_ms,
                        {"record_id": merged["record_id"], "payload_hash": payload_hash},
                    )
                    self._save_state_unlocked(state)
                    return deepcopy(merged)
                current_obs = int(current.get("time", {}).get("observation_end_ms") or -1)
                if observation_end_ms < current_obs:
                    accept = False
                    event_status = "OLDER_OBSERVATION_IGNORED"
                    record["collector"]["accepted_as_current"] = False
                    record["quality"]["reason_codes"] = sorted(set(record["quality"]["reason_codes"] + ["OLDER_THAN_CURRENT"]))
                elif observation_end_ms == current_obs and not source_revision:
                    accept = False
                    event_status = "UNVERSIONED_REVISION_REJECTED"
                    record["collector"]["accepted_as_current"] = False
                    record["quality"]["data_state"] = "INVALID"
                    record["quality"]["reason_codes"] = sorted(
                        set(record["quality"]["reason_codes"] + ["UNVERSIONED_REVISION_REJECTED"])
                    )
            self._remember_record(state, payload_hash, record)
            if accept:
                state["current"][state_key] = record
            self._append_event(
                state,
                product_id,
                scope_key,
                event_status,
                first_seen_source_ms,
                {"record_id": record["record_id"], "payload_hash": payload_hash},
            )
            self._save_state_unlocked(state)
            return deepcopy(record)

    def read(self, product_id: str, *, now_ms: int, usage: str, scope: Any = None) -> dict[str, Any]:
        product = self._product(product_id)
        now_ms = _require_int_ms("now_ms", now_ms) or 0
        with self._state_guard():
            state = self._load_state_unlocked()
            state_key = _state_key(product_id, scope)
            record = state.get("current", {}).get(state_key)
        if not isinstance(record, Mapping):
            return {
                "schema": RECORD_SCHEMA,
                "identity": self._missing_identity(product, scope),
                "usage_decision": self._missing_decision(product, now_ms, usage),
            }
        out = deepcopy(dict(record))
        out["usage_decision"] = self._usage_decision(product, out, now_ms, usage)
        return out

    def health(self, now_ms: int) -> dict[str, Any]:
        now_ms = _require_int_ms("now_ms", now_ms) or 0
        with self._state_guard():
            state = self._load_state_unlocked()
            current = state.get("current", {}) if isinstance(state.get("current"), Mapping) else {}
        products: dict[str, Any] = {}
        for product_id, product in sorted(self.products.items()):
            entries = [dict(v) for k, v in current.items() if k.startswith(product_id + "|") and isinstance(v, Mapping)]
            products[product_id] = self._health_product(product, entries, now_ms)
        return {
            "schema": "astra_data_engine_health@2.3.0",
            "generated_at_ms": now_ms,
            "rule_version": RULE_VERSION,
            "registry_path": str(self.registry_path),
            "products": products,
            "recent_events": deepcopy(state.get("health_events", [])[-DEFAULT_HEALTH_EVENT_LIMIT:]),
        }

    def freeze(
        self,
        records: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
        *,
        cutoff_at_ms: int,
        candidate_id: str | None = None,
    ) -> dict[str, Any]:
        cutoff_at_ms = _require_int_ms("cutoff_at_ms", cutoff_at_ms) or 0
        source_records: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for item in records:
            if not isinstance(item, Mapping) or item.get("schema") != RECORD_SCHEMA:
                excluded.append({"reason": "not_a_data_record"})
                continue
            identity = item.get("identity") if isinstance(item.get("identity"), Mapping) else {}
            product_id = str(identity.get("product_id") or "")
            first_seen = item.get("time", {}).get("first_seen_at_ms") if isinstance(item.get("time"), Mapping) else None
            if not isinstance(first_seen, int) or first_seen > cutoff_at_ms:
                excluded.append({
                    "product_id": product_id,
                    "record_id": item.get("record_id"),
                    "reason": "first_seen_clock_missing" if not isinstance(first_seen, int) else "first_seen_after_cutoff",
                    "first_seen_at_ms": first_seen,
                })
                continue
            if item.get("collector", {}).get("historical_import"):
                excluded.append({
                    "product_id": product_id,
                    "record_id": item.get("record_id"),
                    "reason": "historical_import_not_current",
                })
                continue
            record = self._freeze_record(item, cutoff_at_ms)
            source_records.append(record)
        body = {
            "schema": FREEZE_SCHEMA,
            "rule_version": RULE_VERSION,
            "cutoff_at_ms": cutoff_at_ms,
            "candidate_id": candidate_id,
            "source_records": source_records,
            "excluded_records": excluded,
            "immutable": True,
        }
        body["manifest_hash"] = _hash(body)
        return body

    def _freeze_record(self, record: Mapping[str, Any], cutoff_at_ms: int) -> dict[str, Any]:
        identity = record.get("identity") if isinstance(record.get("identity"), Mapping) else {}
        product_id = str(identity.get("product_id") or "")
        product = self._product(product_id)
        time_part = record.get("time") if isinstance(record.get("time"), Mapping) else {}
        version = record.get("version") if isinstance(record.get("version"), Mapping) else {}
        content = record.get("content") if isinstance(record.get("content"), Mapping) else {}
        quality = record.get("quality") if isinstance(record.get("quality"), Mapping) else {}
        collector = record.get("collector") if isinstance(record.get("collector"), Mapping) else {}
        usage = "freeze"
        existing_decision = record.get("usage_decision")
        if isinstance(existing_decision, Mapping) and existing_decision.get("usage"):
            usage = str(existing_decision.get("usage"))
        usage_qualification = self._usage_decision(product, record, cutoff_at_ms, usage)
        usage_qualification.pop("served_at_ms", None)
        usage_qualification["qualified_at_ms"] = cutoff_at_ms
        return {
            "record_id": record.get("record_id"),
            "identity": {
                "product_id": identity.get("product_id"),
                "provider_id": identity.get("provider_id"),
                "source_ref": identity.get("source_ref"),
                "definition_version": identity.get("definition_version"),
                "market": identity.get("market"),
                "asset": identity.get("asset"),
                "currency": identity.get("currency"),
                "scope": deepcopy(identity.get("scope")),
                "scope_key": identity.get("scope_key"),
                "aggregation": identity.get("aggregation"),
                "unit": identity.get("unit"),
            },
            "time": {
                "observation_start_ms": time_part.get("observation_start_ms"),
                "observation_end_ms": time_part.get("observation_end_ms"),
                "published_at_ms": time_part.get("published_at_ms"),
                "first_seen_at_ms": time_part.get("first_seen_at_ms"),
            },
            "version": {
                "source_revision": version.get("source_revision"),
                "normalized_version": version.get("normalized_version"),
                "payload_hash": version.get("payload_hash"),
                "normalizer_version": version.get("normalizer_version"),
                "is_final": version.get("is_final"),
                "parent_record_ids": deepcopy(version.get("parent_record_ids") or []),
            },
            "content": {
                "values": deepcopy(content.get("values") or {}),
                "missing_fields": deepcopy(content.get("missing_fields") or []),
                "coverage": deepcopy(content.get("coverage")),
            },
            "quality": {
                "data_state": quality.get("data_state"),
                "reason_codes": deepcopy(quality.get("reason_codes") or []),
                "rule_version": quality.get("rule_version"),
            },
            "collector": {
                "collector_id": collector.get("collector_id"),
                "provider_id": collector.get("provider_id"),
                "primary_collector": collector.get("primary_collector"),
                "scope_key": collector.get("scope_key"),
                "accepted_as_current": collector.get("accepted_as_current"),
                "historical_import": collector.get("historical_import"),
            },
            "usage_qualification": usage_qualification,
        }

    def reserve_request(self, product_id: str, *, now_ms: int) -> None:
        """Charge an additional wire request inside an already-budgeted loader."""
        product = self._product(product_id)
        now_ms = _require_int_ms("now_ms", now_ms) or 0
        if product.get("connection_status") == "PLANNED_NOT_CONNECTED":
            raise DataEngineError("PRODUCT_NOT_CONNECTED", "source is not connected")
        with self._state_guard():
            state = self._load_state_unlocked()
            self._consume_fetch_budget(state, product, now_ms)
            self._save_state_unlocked(state)

    def fetch(
        self,
        product_id: str,
        loader: Callable[[], Mapping[str, Any]],
        *,
        now_ms: int,
        scope: Any = None,
    ) -> dict[str, Any]:
        product = self._product(product_id)
        if product.get("connection_status") == "PLANNED_NOT_CONNECTED":
            raise DataEngineError("PRODUCT_NOT_CONNECTED", f"{product_id} is registered but not connected")
        now_ms = _require_int_ms("now_ms", now_ms) or 0
        lock_key = _state_key(product_id, scope)
        lock = self._fetch_lock(lock_key)
        with lock:
            cached = self.read(product_id, now_ms=now_ms, usage="fetch", scope=scope)
            if cached.get("usage_decision", {}).get("can_use"):
                cached["fetch_decision"] = {"status": "CACHE_HIT", "loader_called": False}
                return cached
            with self._state_guard():
                state = self._load_state_unlocked()
                # Polling cadence and economic observation age are different.
                # Reusing a daily record never refreshes its usage qualification.
                interval = self._fetch_policy(product).get("min_fetch_interval_ms", 0)
                last_success = (state.get("fetch_status", {}).get(lock_key) or {}).get("last_success_at_ms")
                if (isinstance(interval, int) and interval > 0 and isinstance(last_success, int)
                        and 0 <= now_ms - last_success < interval):
                    cached["fetch_decision"] = {"status": "AWAIT_SOURCE_CHECK", "loader_called": False,
                                                "next_check_at_ms": last_success + interval}
                    return cached
                self._check_fetch_backoff(state, product, lock_key, now_ms)
                self._consume_fetch_budget(state, product, now_ms)
                self._save_state_unlocked(state)
            try:
                loaded = loader()
                if not isinstance(loaded, Mapping):
                    raise DataEngineError("LOADER_RESULT_INVALID", "loader must return a mapping")
                payload = dict(loaded)
                payload.setdefault("retrieved_at_ms", now_ms)
                if "observation_end_ms" not in payload:
                    raise DataEngineError("LOADER_RESULT_INVALID", "loader result must include observation_end_ms")
                values = payload.pop("values", None)
                if not isinstance(values, Mapping):
                    raise DataEngineError("LOADER_RESULT_INVALID", "loader result must include mapping values")
                payload.pop("product_id", None)
                record = self.ingest(product_id, values, scope=scope, **payload)
                record["fetch_decision"] = {"status": "FETCHED", "loader_called": True}
                self._clear_fetch_error(lock_key, max(now_ms, int(record.get("time", {}).get("retrieved_at_ms") or now_ms)))
                return record
            except Exception as exc:
                self._record_fetch_error(product_id, scope, lock_key, product, now_ms, exc)
                fallback = self.read(product_id, now_ms=now_ms, usage="fetch", scope=scope)
                if fallback.get("record_id"):
                    fallback["fetch_decision"] = {
                        "status": "FETCH_ERROR_USING_CACHE",
                        "loader_called": True,
                        "error_code": getattr(exc, "code", type(exc).__name__),
                    }
                    return fallback
                if isinstance(exc, DataEngineError):
                    raise
                raise DataEngineError("FETCH_ERROR", str(exc)) from exc

    def _product(self, product_id: str) -> dict[str, Any]:
        product = self.products.get(product_id)
        if not product:
            raise DataEngineError("UNKNOWN_PRODUCT", f"unknown data product: {product_id}")
        return dict(product)

    @contextmanager
    def _state_guard(self):
        with self._state_lock:
            fd = self._acquire_file_lock()
            try:
                yield
            finally:
                os.close(fd)
                try:
                    self._lock_path().unlink()
                except FileNotFoundError:
                    pass

    def _lock_path(self) -> Path:
        return self.state_path.with_name(self.state_path.name + ".lock")

    def _acquire_file_lock(self) -> int:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self._lock_path()
        deadline = time.time() + STATE_LOCK_TIMEOUT_S
        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(fd, str(os.getpid()).encode("ascii", "ignore"))
                return fd
            except FileExistsError:
                try:
                    age_ms = (time.time() - lock_path.stat().st_mtime) * 1000
                    if age_ms > STALE_LOCK_MS:
                        lock_path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.time() >= deadline:
                    raise DataEngineError("STATE_LOCK_TIMEOUT", "timed out waiting for data engine state lock")
                time.sleep(0.01)

    def _load_state_unlocked(self) -> dict[str, Any]:
        return _read_json(
            self.state_path,
            {
                "schema": ENGINE_SCHEMA,
                "current": {},
                "known_records": {},
                "health_events": [],
                "fetch_status": {},
                "fetch_budget": {},
            },
        )

    def _save_state_unlocked(self, state: Mapping[str, Any]) -> None:
        state = dict(state)
        self._prune_state(state)
        _write_json_atomic(self.state_path, state)

    def _prune_state(self, state: dict[str, Any]) -> None:
        known = state.get("known_records")
        if isinstance(known, Mapping) and len(known) > MAX_KNOWN_RECORDS:
            ordered = sorted(
                known.items(),
                key=lambda item: (
                    int(item[1].get("first_seen_at_ms") or 0) if isinstance(item[1], Mapping) else 0,
                    str(item[0]),
                ),
            )
            keep = {key for key, _value in ordered[-MAX_KNOWN_RECORDS:]}
            state["known_records"] = {key: value for key, value in known.items() if key in keep}
        current = state.get("current")
        if not isinstance(current, Mapping):
            return
        grouped: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
        for key, record in current.items():
            product_id = str(key).split("|", 1)[0]
            if isinstance(record, Mapping):
                grouped.setdefault(product_id, []).append((key, record))
        drop: set[str] = set()
        for rows in grouped.values():
            if len(rows) <= MAX_CURRENT_SCOPES_PER_PRODUCT:
                continue
            rows.sort(
                key=lambda item: (
                    int(item[1].get("time", {}).get("observation_end_ms") or 0),
                    int(item[1].get("time", {}).get("first_seen_at_ms") or 0),
                    item[0],
                )
            )
            drop.update(key for key, _record in rows[:-MAX_CURRENT_SCOPES_PER_PRODUCT])
        if drop:
            state["current"] = {key: value for key, value in current.items() if key not in drop}

    def _payload_basis(
        self,
        product: Mapping[str, Any],
        *,
        values: Mapping[str, Any],
        scope: Any,
        source_ref: str | None,
        observation_start_ms: int | None,
        observation_end_ms: int,
        published_at_ms: int | None,
        source_revision: str | None,
        is_final: bool | None,
        parent_record_ids: list[str],
        data_state: str,
        reason_codes: list[str],
    ) -> dict[str, Any]:
        return {
            "identity": {
                "product_id": product.get("product_id"),
                "provider_id": product.get("provider_id"),
                "source_ref": source_ref,
                "definition_version": product.get("definition_version"),
                "market": product.get("market"),
                "asset": product.get("asset"),
                "currency": product.get("currency"),
                "scope": deepcopy(scope),
                "aggregation": product.get("aggregation"),
                "unit": product.get("unit"),
            },
            "time": {
                "observation_start_ms": observation_start_ms,
                "observation_end_ms": observation_end_ms,
                "published_at_ms": published_at_ms,
            },
            "version": {
                "source_revision": source_revision,
                "normalizer_version": NORMALIZER_VERSION,
                "is_final": is_final,
                "parent_record_ids": deepcopy(parent_record_ids),
            },
            "content": {"values": deepcopy(dict(values))},
            "quality": {"data_state": data_state, "reason_codes": deepcopy(reason_codes)},
        }

    def _build_record(
        self,
        product: Mapping[str, Any],
        *,
        values: Mapping[str, Any],
        scope: Any,
        scope_key: str,
        source_ref: str | None,
        observation_start_ms: int | None,
        observation_end_ms: int,
        published_at_ms: int | None,
        first_seen_at_ms: int,
        retrieved_at_ms: int | None,
        ingested_at_ms: int | None,
        source_revision: str | None,
        is_final: bool | None,
        parent_record_ids: list[str],
        payload_hash: str,
        data_state: str,
        reason_codes: list[str],
        historical_import: bool,
        accepted_as_current: bool,
    ) -> dict[str, Any]:
        return {
            "schema": RECORD_SCHEMA,
            "record_id": f"{product.get('product_id')}:{payload_hash[:24]}",
            "identity": {
                "product_id": product.get("product_id"),
                "provider_id": product.get("provider_id"),
                "source_ref": source_ref,
                "definition_version": product.get("definition_version"),
                "market": product.get("market"),
                "asset": product.get("asset"),
                "currency": product.get("currency"),
                "scope": deepcopy(scope),
                "scope_key": scope_key,
                "aggregation": product.get("aggregation"),
                "unit": product.get("unit"),
            },
            "time": {
                "observation_start_ms": observation_start_ms,
                "observation_end_ms": observation_end_ms,
                "published_at_ms": published_at_ms,
                "first_seen_at_ms": first_seen_at_ms,
                "retrieved_at_ms": retrieved_at_ms,
                "ingested_at_ms": ingested_at_ms,
            },
            "version": {
                "source_revision": source_revision,
                "normalized_version": product.get("definition_version"),
                "payload_hash": payload_hash,
                "raw_ref": None,
                "normalizer_version": NORMALIZER_VERSION,
                "is_final": is_final,
                "parent_record_ids": deepcopy(parent_record_ids),
            },
            "content": {"values": deepcopy(dict(values)), "missing_fields": [], "coverage": product.get("coverage")},
            "quality": {
                "data_state": data_state,
                "reason_codes": deepcopy(reason_codes),
                "checked_at_ms": ingested_at_ms if ingested_at_ms is not None else retrieved_at_ms,
                "rule_version": RULE_VERSION,
            },
            "collector": {
                "collector_id": product.get("collector_id"),
                "provider_id": product.get("provider_id"),
                "primary_collector": product.get("primary_collector"),
                "scope_key": scope_key,
                "accepted_as_current": bool(accepted_as_current),
                "historical_import": bool(historical_import),
                "last_checked_at_ms": ingested_at_ms if ingested_at_ms is not None else retrieved_at_ms,
                "last_transport_success_at_ms": retrieved_at_ms,
            },
        }

    def _usage_decision(self, product: Mapping[str, Any], record: Mapping[str, Any], now_ms: int, usage: str) -> dict[str, Any]:
        quality = record.get("quality") if isinstance(record.get("quality"), Mapping) else {}
        time_part = record.get("time") if isinstance(record.get("time"), Mapping) else {}
        collector = record.get("collector") if isinstance(record.get("collector"), Mapping) else {}
        data_state = str(quality.get("data_state") or "MISSING").upper()
        ttl_ms = self._ttl_ms(product, usage)
        observation_end_ms = time_part.get("observation_end_ms")
        first_seen_at_ms = time_part.get("first_seen_at_ms")
        age_ms = None if not isinstance(observation_end_ms, int) else now_ms - observation_end_ms
        reasons: list[str] = []
        can_use = True
        status = "USABLE"
        if product.get("connection_status") == "PLANNED_NOT_CONNECTED":
            can_use = False
            status = "NOT_CONNECTED"
            reasons.append("PRODUCT_NOT_CONNECTED")
        if collector.get("historical_import") and usage not in {"health", "history"}:
            can_use = False
            status = "HISTORICAL_IMPORT_ONLY"
            reasons.append("HISTORICAL_IMPORT_NOT_CURRENT")
        if (
            usage not in {"health", "history"}
            and isinstance(first_seen_at_ms, int)
            and first_seen_at_ms > now_ms + CLOCK_SKEW_MS
        ):
            can_use = False
            status = "FIRST_SEEN_AFTER_NOW"
            reasons.append("FIRST_SEEN_AFTER_NOW")
        if not collector.get("accepted_as_current", True):
            can_use = False
            status = "NOT_CURRENT"
            reasons.append("NOT_ACCEPTED_AS_CURRENT")
        if data_state not in self._allowed_data_states(product, usage):
            can_use = False
            status = "INVALID" if data_state in {"INVALID", "ERROR"} else data_state
            reasons.append("DATA_STATE_" + data_state)
        if isinstance(age_ms, int) and age_ms < -CLOCK_SKEW_MS:
            can_use = False
            status = "CLOCK_UNTRUSTED"
            reasons.append("OBSERVATION_IN_FUTURE")
        if ttl_ms is not None and isinstance(age_ms, int) and age_ms > ttl_ms:
            can_use = False
            status = "STALE"
            reasons.append("USAGE_TTL_EXCEEDED")
        if observation_end_ms is None:
            can_use = False
            status = "MISSING"
            reasons.append("OBSERVATION_TIME_MISSING")
        return {
            "usage": usage,
            "status": status,
            "can_use": bool(can_use),
            "age_ms": age_ms,
            "ttl_ms": ttl_ms,
            "served_at_ms": now_ms,
            "reason_codes": sorted(set(reasons)),
        }

    def _ttl_ms(self, product: Mapping[str, Any], usage: str) -> int | None:
        policies = product.get("usage_policies") if isinstance(product.get("usage_policies"), Mapping) else {}
        specific = policies.get(usage) if isinstance(policies.get(usage), Mapping) else {}
        if "ttl_ms" in specific:
            return specific["ttl_ms"]
        default = policies.get("default") if isinstance(policies.get("default"), Mapping) else {}
        if "ttl_ms" in default:
            return default["ttl_ms"]
        return DEFAULT_TTL_MS

    def _allowed_data_states(self, product: Mapping[str, Any], usage: str) -> set[str]:
        policies = product.get("usage_policies") if isinstance(product.get("usage_policies"), Mapping) else {}
        specific = policies.get(usage) if isinstance(policies.get(usage), Mapping) else {}
        allowed = specific.get("allowed_data_states")
        if isinstance(allowed, list) and allowed:
            return {str(item).upper() for item in allowed}
        default = policies.get("default") if isinstance(policies.get("default"), Mapping) else {}
        allowed = default.get("allowed_data_states")
        if isinstance(allowed, list) and allowed:
            return {str(item).upper() for item in allowed}
        return {"OK"}

    def _missing_identity(self, product: Mapping[str, Any], scope: Any) -> dict[str, Any]:
        return {
            "product_id": product.get("product_id"),
            "provider_id": product.get("provider_id"),
            "source_ref": product.get("source_ref"),
            "definition_version": product.get("definition_version"),
            "scope": deepcopy(scope),
            "scope_key": _scope_key(scope),
            "unit": product.get("unit"),
        }

    def _missing_decision(self, product: Mapping[str, Any], now_ms: int, usage: str) -> dict[str, Any]:
        status = "NOT_CONNECTED" if product.get("connection_status") == "PLANNED_NOT_CONNECTED" else "MISSING"
        reasons = ["PRODUCT_NOT_CONNECTED"] if status == "NOT_CONNECTED" else ["NO_CURRENT_RECORD"]
        return {
            "usage": usage,
            "status": status,
            "can_use": False,
            "age_ms": None,
            "ttl_ms": self._ttl_ms(product, usage),
            "served_at_ms": now_ms,
            "reason_codes": reasons,
        }

    def _health_product(self, product: Mapping[str, Any], entries: list[dict[str, Any]], now_ms: int) -> dict[str, Any]:
        if not entries:
            decision = self._missing_decision(product, now_ms, "health")
            return {
                "provider_id": product.get("provider_id"),
                "collector_id": product.get("collector_id"),
                "connection_status": product.get("connection_status"),
                "status": decision["status"],
                "usage_decision": decision,
                "affected_consumers": deepcopy(product.get("consumers") or []),
                "maintenance_ref": product.get("maintenance_ref"),
                "current": [],
            }
        current = []
        statuses = []
        limited = False
        for record in entries:
            decision = self._usage_decision(product, record, now_ms, "health")
            statuses.append(decision["status"])
            usage_decisions = self._usage_decisions_for_health(product, record, now_ms)
            if any(name != "health" and not item.get("can_use") for name, item in usage_decisions.items()):
                limited = True
            current.append({
                "scope": deepcopy(record.get("identity", {}).get("scope")),
                "record_id": record.get("record_id"),
                "payload_hash": record.get("version", {}).get("payload_hash"),
                "data_state": record.get("quality", {}).get("data_state"),
                "observation_end_ms": record.get("time", {}).get("observation_end_ms"),
                "first_seen_at_ms": record.get("time", {}).get("first_seen_at_ms"),
                "usage_decision": decision,
                "usage_decisions": usage_decisions,
            })
        status = "OK" if statuses and all(item == "USABLE" for item in statuses) else sorted(set(statuses))[0]
        if status == "OK" and limited:
            status = "USAGE_LIMITED"
        return {
            "provider_id": product.get("provider_id"),
            "collector_id": product.get("collector_id"),
            "connection_status": product.get("connection_status"),
            "status": status,
            "affected_consumers": deepcopy(product.get("consumers") or []),
            "maintenance_ref": product.get("maintenance_ref"),
            "current": current,
        }

    def _usage_decisions_for_health(self, product: Mapping[str, Any], record: Mapping[str, Any], now_ms: int) -> dict[str, Any]:
        policies = product.get("usage_policies") if isinstance(product.get("usage_policies"), Mapping) else {}
        names = {"health", "default"}
        names.update(str(name) for name in policies if isinstance(name, str))
        return {name: self._usage_decision(product, record, now_ms, name) for name in sorted(names)}

    def _remember_record(self, state: dict[str, Any], payload_hash: str, record: Mapping[str, Any]) -> None:
        state.setdefault("known_records", {})[payload_hash] = {
            "record_id": record.get("record_id"),
            "first_seen_at_ms": record.get("time", {}).get("first_seen_at_ms"),
        }

    def _append_event(
        self,
        state: dict[str, Any],
        product_id: str,
        scope_key: str,
        status: str,
        ts_ms: int,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        events = state.setdefault("health_events", [])
        events.append({
            "ts_ms": ts_ms,
            "product_id": product_id,
            "scope_key": scope_key,
            "status": status,
            "detail": deepcopy(dict(detail or {})),
        })
        del events[:-DEFAULT_HEALTH_EVENT_LIMIT]

    def _sanitize_record_ids(self, parent_record_ids: Any) -> list[str]:
        if parent_record_ids is None:
            return []
        if not isinstance(parent_record_ids, list):
            raise DataEngineError("INVALID_PARENT_RECORDS", "parent_record_ids must be a list")
        out: list[str] = []
        for item in parent_record_ids:
            if not isinstance(item, str) or SECRET_VALUE_RE.search(item):
                raise DataEngineError("INVALID_PARENT_RECORDS", "parent_record_ids must contain safe strings")
            out.append(item)
        return out

    def _fetch_lock(self, key: str) -> threading.Lock:
        with self._fetch_locks_guard:
            lock = self._fetch_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._fetch_locks[key] = lock
            return lock

    def _fetch_policy(self, product: Mapping[str, Any]) -> dict[str, Any]:
        policy = product.get("request_policy")
        return dict(policy) if isinstance(policy, Mapping) else {}

    def _product_budget_limit(self, product: Mapping[str, Any]) -> int:
        policy = self._fetch_policy(product)
        return int(policy.get("budget_per_minute") or DEFAULT_FETCH_BUDGET_PER_MINUTE)

    def _provider_budget_limit(self, provider_id: str) -> int:
        provider_policies = self.registry.get("provider_policies")
        if isinstance(provider_policies, Mapping):
            policy = provider_policies.get(provider_id)
            if isinstance(policy, Mapping) and policy.get("budget_per_minute"):
                return int(policy["budget_per_minute"])
        limits = [
            self._product_budget_limit(product)
            for product in self.products.values()
            if str(product.get("provider_id") or "unknown") == provider_id
        ]
        return max(limits) if limits else DEFAULT_FETCH_BUDGET_PER_MINUTE

    def _check_fetch_backoff(self, state: Mapping[str, Any], product: Mapping[str, Any], lock_key: str, now_ms: int) -> None:
        status = state.get("fetch_status", {}).get(lock_key)
        if isinstance(status, Mapping):
            next_retry = status.get("next_retry_at_ms")
            if isinstance(next_retry, int) and now_ms < next_retry:
                raise DataEngineError(
                    "FETCH_BACKOFF_ACTIVE",
                    f"fetch is in backoff until {next_retry}",
                    detail={"provider_id": product.get("provider_id"), "next_retry_at_ms": next_retry},
                )

    def _consume_fetch_budget(self, state: dict[str, Any], product: Mapping[str, Any], now_ms: int) -> None:
        product_id = str(product.get("product_id") or "unknown")
        provider_id = str(product.get("provider_id") or "unknown")
        minute = now_ms // 60_000
        product_limit = self._product_budget_limit(product)
        provider_limit = self._provider_budget_limit(provider_id)
        budgets = state.setdefault("fetch_budget", {})
        provider_budgets = budgets.setdefault("provider", {})
        product_budgets = budgets.setdefault("product", {})
        provider_key = f"{provider_id}|{minute}"
        product_key = f"{product_id}|{minute}"
        provider_used = int(provider_budgets.get(provider_key) or 0)
        product_used = int(product_budgets.get(product_key) or 0)
        if provider_used >= provider_limit:
            raise DataEngineError(
                "FETCH_BUDGET_EXHAUSTED",
                f"provider budget exhausted for {provider_id}",
                detail={"level": "provider", "provider_id": provider_id, "limit": provider_limit},
            )
        if product_used >= product_limit:
            raise DataEngineError(
                "FETCH_BUDGET_EXHAUSTED",
                f"product budget exhausted for {product_id}",
                detail={"level": "product", "product_id": product_id, "limit": product_limit},
            )
        provider_budgets[provider_key] = provider_used + 1
        product_budgets[product_key] = product_used + 1
        self._prune_budget_buckets(provider_budgets, minute)
        self._prune_budget_buckets(product_budgets, minute)

    def _prune_budget_buckets(self, budgets: dict[str, Any], minute: int) -> None:
        for key in list(budgets):
            try:
                bucket_minute = int(str(key).split("|")[-1])
            except ValueError:
                continue
            if bucket_minute < minute - 10:
                budgets.pop(key, None)

    def _record_fetch_error(
        self,
        product_id: str,
        scope: Any,
        lock_key: str,
        product: Mapping[str, Any],
        now_ms: int,
        exc: Exception,
    ) -> None:
        with self._state_guard():
            state = self._load_state_unlocked()
            policy = self._fetch_policy(product)
            backoff = policy.get("retry_backoff_ms") if isinstance(policy.get("retry_backoff_ms"), list) else [10_000, 30_000, 60_000]
            status = dict(state.setdefault("fetch_status", {}).get(lock_key) or {})
            attempts = int(status.get("attempts") or 0) + 1
            delay = int(backoff[min(attempts - 1, len(backoff) - 1)])
            status.update({
                "attempts": attempts,
                "last_error_at_ms": now_ms,
                "last_error_code": getattr(exc, "code", type(exc).__name__),
                "next_retry_at_ms": now_ms + delay,
            })
            state["fetch_status"][lock_key] = status
            self._append_event(
                state,
                product_id,
                _scope_key(scope),
                "FETCH_ERROR",
                now_ms,
                {"error_code": status["last_error_code"], "next_retry_at_ms": status["next_retry_at_ms"]},
            )
            self._save_state_unlocked(state)

    def _clear_fetch_error(self, lock_key: str, now_ms: int) -> None:
        with self._state_guard():
            state = self._load_state_unlocked()
            status = dict(state.setdefault("fetch_status", {}).get(lock_key) or {})
            status.update({"attempts": 0, "next_retry_at_ms": None, "last_success_at_ms": now_ms})
            state["fetch_status"][lock_key] = status
            self._save_state_unlocked(state)


__all__ = ["DataEngine", "DataEngineError", "default_registry_path"]
