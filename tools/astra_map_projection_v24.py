"""Workbench projection glue for BTC MAP v2.4.

This layer has no network authority.  Slow source collection is triggered by
the workbench refresh loop only; GET and manual review paths reuse cached data
engine records and the current in-memory projection.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import hashlib
import json
import math
from typing import Any, Callable, Mapping

if __package__:
    from . import astra_data_engine_v23 as data_engine
    from . import astra_map_sources_v24 as map_sources
    from . import astra_map_v24 as map_core
    from . import astra_underwriting_v20 as underwriting
else:  # pragma: no cover - direct script use
    import astra_data_engine_v23 as data_engine
    import astra_map_sources_v24 as map_sources
    import astra_map_v24 as map_core
    import astra_underwriting_v20 as underwriting


SCHEMA = "astra_map_projection@2.4.0"
PRODUCER_CURRENT_SCHEMA = "astra_map_data_server@2.4.2"
SOURCE_MODE_COLLECTOR = "collector"
SOURCE_MODE_PRODUCER_MANIFEST = "producer_manifest"
DEFAULT_PRODUCER_MAX_AGE_MS = 600_000
MAX_PRODUCER_CURRENT_BYTES = 8 * 1024 * 1024
GLOBAL_PRODUCT = "gex.effective.accepted.v1"
CP_PRODUCT = getattr(map_sources, "BRK_COST", "btc.brk.cost_basis.v1")
KPF_PRODUCT = "btc.kpf.map.readonly.v1"
KPF_MAX_POOL = 128
KPF_DEFAULT_DISPLAY_LIMIT = 2
KPF_MAX_ARTIFACT_BYTES = 4 * 1024 * 1024


class SourceConsumerError(ValueError):
    """Raised when a producer MAP freeze cannot be consumed as a read-only source."""

    def __init__(self, code: str, message: str, *, detail: Any | None = None):
        super().__init__(message)
        self.code = code
        self.detail = deepcopy(detail)

    def as_dict(self) -> dict[str, Any]:
        out = {"code": self.code, "message": str(self)}
        if self.detail is not None:
            out["detail"] = deepcopy(self.detail)
        return out


def _canonical_scope(scope: Any) -> str:
    if scope is None:
        return "__global__"
    if isinstance(scope, str):
        return scope
    return "scope:" + _stable_hash(scope)[:20]


def _record_scope_key(record: Mapping[str, Any]) -> str:
    identity = record.get("identity") if isinstance(record.get("identity"), Mapping) else {}
    if identity.get("scope_key"):
        return str(identity["scope_key"])
    return _canonical_scope(identity.get("scope"))


def _product_key(product_id: str, scope: Any) -> str:
    return product_id + "|" + _canonical_scope(scope)


def _record_key(record: Mapping[str, Any]) -> str | None:
    product_id = _product_id(record)
    if not product_id:
        return None
    return product_id + "|" + _record_scope_key(record)


def _manifest_hash(manifest: Mapping[str, Any]) -> str:
    return data_engine._hash({key: value for key, value in manifest.items() if key != "manifest_hash"})


def _valid_manifest_hash(manifest: Mapping[str, Any]) -> bool:
    digest = manifest.get("manifest_hash")
    return isinstance(digest, str) and digest == _manifest_hash(manifest)


def _immutable_record_body(record: Mapping[str, Any]) -> dict[str, Any]:
    out = {key: deepcopy(record.get(key)) for key in (
        "record_id", "identity", "time", "version", "content", "quality", "collector"
    )}
    return out


class ProducerFreezeSourceEngine:
    """Read-only DataEngine-shaped adapter over a producer current_map freeze."""

    def __init__(
        self,
        current_map: Mapping[str, Any],
        *,
        path: Path,
        now_ms: int,
        max_age_ms: int | None,
        qualifier_engine: Any,
        expected_manifest_hash: str | None = None,
    ):
        self.path = Path(path)
        self.now_ms = int(now_ms)
        self.qualifier_engine = qualifier_engine
        self.products = getattr(qualifier_engine, "products", None)
        if not isinstance(self.products, Mapping):
            raise SourceConsumerError("PRODUCER_QUALIFIER_ENGINE_INVALID", "producer source consumer requires a DataEngine qualifier")
        self.current_map = deepcopy(dict(current_map))
        self.legacy_manifest_duplicates = False
        self.manifest = self._validate_current_map(max_age_ms, expected_manifest_hash)
        self.records = [deepcopy(dict(row)) for row in self.manifest["source_records"]]
        self._by_key: dict[str, dict[str, Any]] = {}
        self._by_id: dict[str, dict[str, Any]] = {}
        self._index_records()

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        now_ms: int,
        qualifier_engine: Any,
        max_age_ms: int | None = DEFAULT_PRODUCER_MAX_AGE_MS,
        expected_manifest_hash: str | None = None,
    ) -> "ProducerFreezeSourceEngine":
        source_path = Path(path)
        try:
            with source_path.open("rb") as stream:
                raw = stream.read(MAX_PRODUCER_CURRENT_BYTES + 1)
            size = len(raw)
            if size > MAX_PRODUCER_CURRENT_BYTES:
                raise SourceConsumerError(
                    "PRODUCER_CURRENT_TOO_LARGE",
                    "producer current_map exceeds the configured read limit",
                    detail={"path": str(source_path), "bytes": size, "max_bytes": MAX_PRODUCER_CURRENT_BYTES},
                )
            payload = json.loads(raw.decode("utf-8-sig"))
        except FileNotFoundError as exc:
            raise SourceConsumerError("PRODUCER_CURRENT_MISSING", f"producer current_map is missing: {source_path}") from exc
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise SourceConsumerError("PRODUCER_CURRENT_INVALID_JSON", f"producer current_map is not valid JSON: {source_path}") from exc
        except OSError as exc:
            raise SourceConsumerError("PRODUCER_CURRENT_UNREADABLE", f"producer current_map cannot be read: {source_path}") from exc
        if not isinstance(payload, Mapping):
            raise SourceConsumerError("PRODUCER_CURRENT_INVALID", "producer current_map must be a JSON object")
        return cls(
            payload,
            path=source_path,
            now_ms=now_ms,
            max_age_ms=max_age_ms,
            qualifier_engine=qualifier_engine,
            expected_manifest_hash=expected_manifest_hash,
        )

    def _validate_current_map(self, max_age_ms: int | None, expected_manifest_hash: str | None) -> dict[str, Any]:
        if self.current_map.get("schema") != PRODUCER_CURRENT_SCHEMA:
            raise SourceConsumerError("PRODUCER_CURRENT_SCHEMA_MISMATCH", f"producer current_map schema must be {PRODUCER_CURRENT_SCHEMA}")
        generated = _int_from(self.current_map.get("generated_at_ms"))
        if generated is None:
            raise SourceConsumerError("PRODUCER_CURRENT_CLOCK_MISSING", "producer current_map generated_at_ms is missing")
        if generated > self.now_ms + data_engine.CLOCK_SKEW_MS:
            raise SourceConsumerError("PRODUCER_CURRENT_IN_FUTURE", "producer current_map generated_at_ms is in the future",
                                      detail={"generated_at_ms": generated, "now_ms": self.now_ms})
        if max_age_ms is not None and self.now_ms - generated > int(max_age_ms):
            raise SourceConsumerError("PRODUCER_CURRENT_STALE", "producer current_map is older than the configured consumer age budget",
                                      detail={"generated_at_ms": generated, "now_ms": self.now_ms, "max_age_ms": int(max_age_ms)})
        btc_map = self.current_map.get("btc_map")
        if not isinstance(btc_map, Mapping):
            raise SourceConsumerError("PRODUCER_BTC_MAP_MISSING", "producer current_map must include btc_map")
        manifest = self.current_map.get("freeze_manifest")
        if not isinstance(manifest, Mapping):
            raise SourceConsumerError("PRODUCER_FREEZE_MANIFEST_MISSING", "producer current_map must include freeze_manifest")
        if manifest.get("schema") != data_engine.FREEZE_SCHEMA:
            raise SourceConsumerError("PRODUCER_FREEZE_SCHEMA_MISMATCH", f"producer freeze_manifest schema must be {data_engine.FREEZE_SCHEMA}")
        if not _valid_manifest_hash(manifest):
            raise SourceConsumerError("PRODUCER_FREEZE_HASH_MISMATCH", "producer freeze_manifest hash does not match its body")
        manifest_hash = manifest.get("manifest_hash")
        if expected_manifest_hash is not None and manifest_hash != expected_manifest_hash:
            raise SourceConsumerError("PRODUCER_FREEZE_CHANGED", "producer freeze_manifest changed while a frozen workbench view was being reused",
                                      detail={"expected": expected_manifest_hash, "actual": manifest_hash})
        cutoff = _int_from(manifest.get("cutoff_at_ms"))
        if cutoff is None:
            raise SourceConsumerError("PRODUCER_FREEZE_CUTOFF_MISSING", "producer freeze_manifest cutoff_at_ms is missing")
        if cutoff > self.now_ms + data_engine.CLOCK_SKEW_MS:
            raise SourceConsumerError("PRODUCER_FREEZE_CUTOFF_IN_FUTURE", "producer freeze_manifest cutoff is in the future",
                                      detail={"cutoff_at_ms": cutoff, "now_ms": self.now_ms})
        records = manifest.get("source_records")
        if not isinstance(records, list) or not records:
            raise SourceConsumerError("PRODUCER_SOURCE_RECORDS_MISSING", "producer freeze_manifest must contain source_records")
        supplied = btc_map.get("source_manifest") if isinstance(btc_map.get("source_manifest"), Mapping) else {}
        self._validate_btc_map_source_manifest(manifest, supplied)
        return deepcopy(dict(manifest))

    def _validate_btc_map_source_manifest(self, manifest: Mapping[str, Any], supplied: Mapping[str, Any]) -> None:
        if not isinstance(supplied, Mapping) or not supplied:
            raise SourceConsumerError("PRODUCER_BTC_MAP_SOURCE_MANIFEST_MISSING", "producer btc_map must include its source_manifest")
        if not supplied.get("manifest_hash"):
            raise SourceConsumerError("PRODUCER_BTC_MAP_SOURCE_HASH_MISSING", "producer btc_map source_manifest must include manifest_hash")
        if not _valid_manifest_hash(supplied):
            raise SourceConsumerError("PRODUCER_BTC_MAP_SOURCE_HASH_MISMATCH", "producer btc_map source_manifest hash does not match its body")
        checks = {
            "manifest_hash": (manifest.get("manifest_hash"), supplied.get("manifest_hash")),
            "cutoff_at_ms": (manifest.get("cutoff_at_ms"), supplied.get("cutoff_at_ms")),
            "candidate_id": (manifest.get("candidate_id"), supplied.get("candidate_id")),
            "source_records": (manifest.get("source_records"), supplied.get("source_records")),
        }
        mismatched = [name for name, (expected, actual) in checks.items() if actual != expected]
        if not mismatched:
            return
        if self._legacy_exact_duplicate_source_manifest(manifest, supplied, mismatched):
            self.legacy_manifest_duplicates = True
            return
        raise SourceConsumerError(
            "PRODUCER_BTC_MAP_SOURCE_MANIFEST_MISMATCH",
            "producer btc_map source_manifest is not the same freeze_manifest",
            detail={"mismatched_fields": mismatched},
        )

    def _legacy_exact_duplicate_source_manifest(
        self,
        manifest: Mapping[str, Any],
        supplied: Mapping[str, Any],
        mismatched: list[str],
    ) -> bool:
        if sorted(mismatched) != ["manifest_hash", "source_records"]:
            return False
        records = supplied.get("source_records")
        if not isinstance(records, list):
            return False
        deduped: list[Any] = []
        seen: set[str] = set()
        for record in records:
            digest = data_engine._hash(record)
            if digest in seen:
                continue
            seen.add(digest)
            deduped.append(record)
        normalized = {**supplied, "source_records": deduped}
        return (len(deduped) < len(records) and deduped == manifest.get("source_records")
                and _manifest_hash(normalized) == _manifest_hash(manifest))

    def _index_records(self) -> None:
        seen_hashes: dict[str, str] = {}
        seen_key_hashes: dict[str, str] = {}
        for row in self.records:
            record_id = str(row.get("record_id") or "")
            if not record_id:
                raise SourceConsumerError("PRODUCER_SOURCE_RECORD_ID_MISSING", "producer source record is missing record_id")
            key = _record_key(row)
            product_id = _product_id(row)
            if not key or not product_id:
                raise SourceConsumerError("PRODUCER_SOURCE_PRODUCT_MISSING", "producer source record is missing identity.product_id",
                                          detail={"record_id": record_id})
            if product_id not in self.products:
                raise SourceConsumerError("PRODUCER_SOURCE_PRODUCT_UNKNOWN", "producer source record product is not in the local registry",
                                          detail={"record_id": record_id, "product_id": product_id})
            fingerprint = data_engine._hash(_immutable_record_body(row))
            if record_id in seen_hashes and seen_hashes[record_id] != fingerprint:
                raise SourceConsumerError("PRODUCER_SOURCE_RECORD_CONFLICT", "producer freeze has conflicting duplicate source records",
                                          detail={"duplicate": "record_id", "record_id": record_id})
            seen_hashes[record_id] = fingerprint
            if key in seen_key_hashes and seen_key_hashes[key] != fingerprint:
                raise SourceConsumerError("PRODUCER_SOURCE_RECORD_CONFLICT", "producer freeze has conflicting duplicate source records",
                                          detail={"duplicate": "product_scope", "record_id": record_id, "product_scope_key": key})
            seen_key_hashes[key] = fingerprint
            if key not in self._by_key:
                self._by_key[key] = deepcopy(row)
            self._by_id[record_id] = deepcopy(row)

    @property
    def manifest_hash(self) -> str:
        return str(self.manifest.get("manifest_hash") or "")

    def source_summary(self, now_ms: int | None = None) -> dict[str, Any]:
        checked = self.now_ms if now_ms is None else int(now_ms)
        return {
            "status": "OK",
            "mode": SOURCE_MODE_PRODUCER_MANIFEST,
            "current_path": str(self.path),
            "producer_schema": self.current_map.get("schema"),
            "producer_generated_at_ms": self.current_map.get("generated_at_ms"),
            "producer_freeze_cutoff_at_ms": self.manifest.get("cutoff_at_ms"),
            "producer_manifest_hash": self.manifest_hash,
            "producer_record_count": len(self.records),
            "legacy_exact_duplicate_manifest": self.legacy_manifest_duplicates,
            "checked_at_ms": checked,
            "note_cn": "只读消费producer冻结source records；当前用途资格按本次checked_at重新核对。",
        }

    def _product(self, product_id: str) -> dict[str, Any]:
        try:
            return self.qualifier_engine._product(product_id)
        except Exception as exc:
            raise SourceConsumerError("PRODUCER_SOURCE_PRODUCT_UNKNOWN", f"unknown data product in producer source: {product_id}") from exc

    def read(self, product_id: str, *, now_ms: int, usage: str, scope: Any = None) -> dict[str, Any]:
        product = self._product(product_id)
        key = _product_key(product_id, scope)
        record = self._by_key.get(key)
        if record is None:
            return {
                "schema": data_engine.RECORD_SCHEMA,
                "identity": self.qualifier_engine._missing_identity(product, scope),
                "usage_decision": self.qualifier_engine._missing_decision(product, now_ms, usage),
            }
        out = deepcopy(record)
        out.setdefault("schema", data_engine.RECORD_SCHEMA)
        out["usage_decision"] = self.qualifier_engine._usage_decision(product, out, int(now_ms), usage)
        return out

    def freeze(
        self,
        records: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
        *,
        cutoff_at_ms: int,
        candidate_id: str | None = None,
    ) -> dict[str, Any]:
        cutoff = int(cutoff_at_ms)
        prepared_records: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in records:
            if not isinstance(item, Mapping):
                excluded.append({"reason": "not_a_data_record"})
                continue
            record_id = str(item.get("record_id") or "")
            original = self._by_id.get(record_id)
            if original is None:
                excluded.append({"record_id": item.get("record_id"), "reason": "not_in_producer_freeze"})
                continue
            if record_id in seen:
                continue
            if data_engine._hash(_immutable_record_body(item)) != data_engine._hash(_immutable_record_body(original)):
                raise SourceConsumerError("PRODUCER_SOURCE_RECORD_HASH_MISMATCH", "caller supplied a changed producer source record",
                                          detail={"record_id": record_id})
            seen.add(record_id)
            prepared_records.append(self._record_for_qualifier_freeze(original))
        body = self.qualifier_engine.freeze(prepared_records, cutoff_at_ms=cutoff, candidate_id=candidate_id)
        if excluded:
            body["excluded_records"] = list(body.get("excluded_records") or []) + excluded
        body["producer_manifest_hash"] = self.manifest_hash
        body["manifest_hash"] = _manifest_hash(body)
        return body

    def _record_for_qualifier_freeze(self, record: Mapping[str, Any]) -> dict[str, Any]:
        prepared = deepcopy(dict(record))
        prepared.setdefault("schema", data_engine.RECORD_SCHEMA)
        if not isinstance(prepared.get("usage_decision"), Mapping):
            qualification = prepared.get("usage_qualification")
            if isinstance(qualification, Mapping):
                prepared["usage_decision"] = deepcopy(dict(qualification))
        return prepared

    def health(self, now_ms: int) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in self.records:
            product_id = _product_id(record)
            if product_id and product_id in self.products:
                grouped.setdefault(product_id, []).append(self._record_for_qualifier_freeze(record))
        products: dict[str, Any] = {}
        for product_id, records in sorted(grouped.items()):
            products[product_id] = self.qualifier_engine._health_product(
                self._product(product_id),
                records,
                int(now_ms),
            )
        return {
            "schema": "astra_producer_source_health@2.4.4",
            "generated_at_ms": now_ms,
            "producer_manifest_hash": self.manifest_hash,
            "products": products,
        }

    def fetch(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise SourceConsumerError("PRODUCER_SOURCE_FETCH_FORBIDDEN", "producer_manifest source mode forbids slow fetch")

    def reserve_request(self, *args: Any, **kwargs: Any) -> None:
        raise SourceConsumerError("PRODUCER_SOURCE_HTTP_FORBIDDEN", "producer_manifest source mode forbids HTTP reservation")

    def ingest(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise SourceConsumerError("PRODUCER_SOURCE_INGEST_FORBIDDEN", "producer_manifest source mode is read-only")


def load_producer_source_engine(
    path: str | Path,
    *,
    now_ms: int,
    qualifier_engine: Any,
    max_age_ms: int | None = DEFAULT_PRODUCER_MAX_AGE_MS,
    expected_manifest_hash: str | None = None,
) -> ProducerFreezeSourceEngine:
    return ProducerFreezeSourceEngine.from_path(
        path,
        now_ms=now_ms,
        qualifier_engine=qualifier_engine,
        max_age_ms=max_age_ms,
        expected_manifest_hash=expected_manifest_hash,
    )


def read_cached_sources(engine: Any, now_ms: int, config: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Read cached source records. This must not issue HTTP."""

    if engine is None:
        return []
    cfg = dict(config or {})
    cfg.setdefault("usage", "background")
    if hasattr(map_sources, "read_sources"):
        records = list(map_sources.read_sources(engine, now_ms, cfg))
        kpf = _read_record(engine, KPF_PRODUCT, now_ms, "background")
        if kpf and _values(kpf).get("artifact_hashes") and not any(
            item.get("record_id") == kpf.get("record_id") for item in records
        ):
            records.append(kpf)
        return records
    return []


def collect_or_read_sources(
    engine: Any,
    now_ms: int,
    *,
    enabled: bool,
    config: Mapping[str, Any] | None = None,
    capture: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    """Optionally run the slow source collector, then read cached qualified rows."""

    cfg = dict(config or {})
    cfg.setdefault("usage", "background")
    if enabled:
        if capture is not None:
            capture(engine=engine, now_ms=now_ms, config=cfg)
        else:
            map_sources.collect_sources(engine, now_ms, config=cfg)
    return read_cached_sources(engine, now_ms, cfg)


def collect_kpf_artifacts(engine: Any, now_ms: int, config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Ingest the configured KPF three-file bundle into the data engine.

    This is a collector-only adapter.  It performs local file reads only when
    called explicitly by the refresh path.  GET/manual projection builders must
    use the cached ``btc.kpf.map.readonly.v1`` record through ``engine.read``.
    """

    cfg = dict(config or {})
    manifest_artifact = None
    expected_hashes = {}
    if cfg.get("kpf_manifest_path"):
        manifest_artifact = _read_kpf_artifact(cfg["kpf_manifest_path"])
        try:
            cfg, expected_hashes = _kpf_manifest_paths(cfg, manifest_artifact)
        except ValueError as exc:
            return _reject_kpf_bundle(engine, now_ms, {"manifest": manifest_artifact}, str(exc))
    paths = {name: cfg.get(name) for name in ("kpf_snapshot_path", "kpf_audit_path", "kpf_debug_path")}
    if not all(paths.values()):
        return {"record": None, "gaps": ["kpf_same_version_artifacts_missing"], "artifact_hashes": {}}
    artifacts: dict[str, dict[str, Any]] = {}
    gaps: list[str] = []
    for name, raw_path in paths.items():
        loaded = _read_kpf_artifact(raw_path)
        if loaded.get("error"):
            gaps.append(str(loaded["error"]))
        artifacts[name] = loaded
        if expected_hashes and loaded.get("sha256") != expected_hashes.get(name):
            gaps.append("kpf_manifest_hash_mismatch")
    if manifest_artifact is not None:
        artifacts["manifest"] = manifest_artifact
    artifact_hashes = {name: item.get("sha256") for name, item in artifacts.items() if item.get("sha256")}
    if gaps:
        if manifest_artifact is not None:
            return _reject_kpf_bundle(engine, now_ms, artifacts, sorted(set(gaps))[0])
        return {"record": None, "gaps": sorted(set(gaps)), "artifact_hashes": artifact_hashes}
    versions = {_kpf_version(item.get("payload")) for item in artifacts.values()}
    if len(versions) != 1 or None in versions:
        values = _kpf_record_values(
            version_id="VERSION_MISMATCH",
            artifacts=artifacts,
            zones=[],
            valid=False,
            reason_codes=["KPF_SAME_VERSION_ARTIFACTS_MISMATCH"],
        )
        record = _ingest_kpf_record(engine, values, now_ms, source_revision="VERSION_MISMATCH", data_state="INVALID", reason_codes=["KPF_SAME_VERSION_ARTIFACTS_MISMATCH"])
        return {"record": record, "gaps": ["kpf_same_version_artifacts_mismatch"], "artifact_hashes": artifact_hashes}
    version_id = str(next(iter(versions)))
    native_payload = artifacts["kpf_debug_path"].get("payload")
    native_reasons = []
    if isinstance(native_payload, Mapping) and native_payload.get("schema") == "astra_kpf_raw_zones@1":
        try:
            raw_zones, native_reasons = _native_kpf_zones(artifacts, now_ms)
        except ValueError as exc:
            return _reject_kpf_bundle(engine, now_ms, artifacts, str(exc))
    else:
        raw_zones = _extract_kpf_zones(native_payload)
    zones = [_normalize_kpf_zone(item, index) for index, item in enumerate(raw_zones)]
    finite_zones = [zone for zone in zones if zone.get("finite_geometry")]
    reason_codes: list[str] = list(native_reasons)
    valid = True
    data_state = "OK"
    if native_reasons:
        valid = False
        data_state = "PARTIAL"
    if not raw_zones:
        valid = False
        data_state = "PARTIAL"
        reason_codes.append("KPF_RAW_BASIN_GEOMETRY_MISSING")
    if len(finite_zones) > KPF_MAX_POOL:
        valid = False
        data_state = "PARTIAL"
        reason_codes.append("KPF_FINITE_POOL_TOO_LARGE")
    values = _kpf_record_values(
        version_id=version_id,
        artifacts=artifacts,
        zones=finite_zones if len(finite_zones) <= KPF_MAX_POOL else [],
        valid=valid,
        reason_codes=reason_codes,
        raw_zone_count=len(raw_zones),
        finite_zone_count=len(finite_zones),
    )
    record = _ingest_kpf_record(engine, values, now_ms, source_revision=version_id, data_state=data_state, reason_codes=reason_codes)
    out_gaps = [code.lower() for code in native_reasons]
    if not raw_zones:
        out_gaps.append("kpf_raw_basin_geometry_missing")
    if len(finite_zones) > KPF_MAX_POOL:
        out_gaps.append("kpf_finite_pool_too_large")
    return {"record": record, "gaps": out_gaps, "artifact_hashes": artifact_hashes}


def _native_kpf_zones(artifacts: Mapping[str, Mapping[str, Any]], now_ms: int) -> tuple[list[dict], list[str]]:
    """Decode the producer's declared nested contract, retaining original data."""
    snapshot = artifacts["kpf_snapshot_path"].get("payload")
    audit = artifacts["kpf_audit_path"].get("payload")
    zones = artifacts["kpf_debug_path"].get("payload")
    for item, expected in ((snapshot, "astra_kpf_snapshot@1"), (audit, "astra_kpf_audit@1"), (zones, "astra_kpf_raw_zones@1")):
        if not isinstance(item, Mapping) or item.get("schema") != expected or item.get("product_id") != KPF_PRODUCT:
            raise ValueError("kpf_native_contract_invalid")
    declared = zones.get("zones")
    if not isinstance(declared, list) or zones.get("zone_count") != len(declared):
        raise ValueError("kpf_native_zone_count_mismatch")
    if len(declared) > KPF_MAX_POOL:
        raise ValueError("kpf_native_zone_pool_too_large")
    ready = (snapshot.get("status") == audit.get("status") == zones.get("status") == "READY"
             and snapshot.get("supported_ready") is True and audit.get("supported_ready") is True
             and snapshot.get("self_audit_pass") is True and audit.get("self_audit_pass") is True
             and (audit.get("data_quality") or {}).get("data_quality_state") == "OK"
             and (audit.get("source_manifest") or {}).get("ok") is True)
    public_ids = {item.get("zone_id") for item in snapshot.get("public_targets") or [] if isinstance(item, Mapping)}
    clock = zones.get("clock") or {}
    observed = _int_from(clock.get("last_trade_observed_ms"))
    known = _int_from(zones.get("generated_at_ms"))
    valid_clock = observed is not None and known is not None and observed <= known <= now_ms
    reasons = [] if ready and valid_clock else ["KPF_NATIVE_AUDIT_OR_SOURCE_CLOCK_UNQUALIFIED"]
    output = []
    for item in declared:
        if not isinstance(item, Mapping):
            raise ValueError("kpf_native_zone_invalid")
        raw, market, usage, evidence = [item.get(key) or {} for key in ("raw", "market", "usage", "evidence")]
        if not all(isinstance(value, Mapping) for value in (raw, market, usage, evidence)):
            raise ValueError("kpf_native_zone_invalid")
        basis_ok = (market.get("market") == "BINANCE_USD_M_FUTURES" and market.get("symbol") == "BTCUSDT"
                    and market.get("quote") == "USDT" and market.get("basis") == "BINANCE_USDM_BTCUSDT_AGGTRADES"
                    and market.get("mapping") == "NOMINAL_ONLY")
        if not basis_ok:
            raise ValueError("kpf_native_market_identity_invalid")
        low, high = _number(raw.get("basin_low")), _number(raw.get("basin_high"))
        if low is None or high is None or low > high:
            raise ValueError("kpf_native_raw_geometry_invalid")
        allowed = (ready and valid_clock and usage.get("can_use") is True and item.get("zone_id") in public_ids
                   and evidence.get("grade") in {"A", "B"})
        output.append({"reference_id": item.get("zone_id"), "revision_id": zones["version_id"],
                       "raw_basin_low": raw.get("basin_low"), "raw_basin_high": raw.get("basin_high"),
                       "display_center": (item.get("display") or {}).get("center"),
                       "market": "BINANCE_USD_M_FUTURES", "quote_currency": "USDT",
                       "price_basis": "BINANCE_USDM_BTCUSDT_AGGTRADES", "expiry_scope": "GLOBAL_ONLY",
                       "coordinate_qualification": "NOMINAL_ONLY", "observation_end_ms": observed,
                       "known_at_ms": known, "active_from_ms": known,
                       "quality": "OK" if ready else "PARTIAL",
                       "usage_decision": {"can_use": allowed, "status": "USABLE" if allowed else "DISABLED",
                                          "reason_codes": [str(usage.get("reason") or "kpf_native_unqualified")]},
                       "source_record_ids": ["kpf_source_"+str(row["sha256"]) for row in audit.get("source_records") or [] if isinstance(row, Mapping) and row.get("sha256")],
                       "native_producer_zone": deepcopy(dict(item))})
    return output, reasons


def build_projection(
    engine: Any,
    *,
    now_ms: int,
    fmz_fact: Mapping[str, Any] | None = None,
    source_records: list[Mapping[str, Any]] | None = None,
    config: Mapping[str, Any] | None = None,
    prior_state: Mapping[str, Any] | None = None,
    current_market: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build cached background, references and a global no-policy MAP."""

    cfg = dict(config or {})
    records = list(source_records or [])
    kpf = _read_record(engine, KPF_PRODUCT, now_ms, "background")
    if kpf and _values(kpf).get("artifact_hashes") and not any(
        item.get("record_id") == kpf.get("record_id") for item in records
    ):
        records.append(kpf)
    current_price = _current_price(fmz_fact)
    current_basis = _current_price_basis(fmz_fact)
    if isinstance(current_market, Mapping):
        if current_market.get("can_use") is True:
            current_price = _number(current_market.get("price_usd"))
            current_basis = str(current_market.get("price_basis") or "") or None
        elif (fmz_fact or {}).get("status") != "current":
            current_price, current_basis = None, None
    background = _background_for_map(map_sources.build_background(
        records,
        now_ms,
        current_price_usd=current_price,
        previous=(prior_state or {}).get("background_wrapper") if isinstance(prior_state, Mapping) else None,
    ), records)
    references, gaps = build_references(engine, now_ms, fmz_fact=fmz_fact, source_records=records, config=cfg)
    observations = build_observations(fmz_fact, references)
    response_state = (prior_state or {}).get("response_state") if isinstance(prior_state, Mapping) else None
    btc_map = map_core.build_map(
        engine,
        now_ms=now_ms,
        background=background,
        references=references,
        observations=observations,
        prior_state=response_state,
        current_price_usd=current_price,
        current_price_basis=current_basis,
    )
    return {
        "schema": SCHEMA,
        "generated_at_ms": now_ms,
        "source_config": cfg,
        "background": background,
        "references": references,
        "observations": observations,
        "gaps": sorted(set(gaps)),
        "btc_map": btc_map,
        "source_records": [deepcopy(dict(record)) for record in records if isinstance(record, Mapping)],
    }


def candidate_map(engine: Any, projection: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild the same-version MAP for one frozen underwriting snapshot."""

    valuation_ms = int((snapshot.get("market") or {})["valuation_ts_ms"])
    source_config = projection.get("source_config") if isinstance(projection.get("source_config"), Mapping) else {}
    records = read_cached_sources(engine, valuation_ms, source_config)
    background = _background_for_map(map_sources.build_background(
        records,
        valuation_ms,
        current_price_usd=_snapshot_price(snapshot),
    ), records)
    references, _gaps = build_references(
        engine,
        valuation_ms,
        fmz_fact=None,
        source_records=records,
        config=dict(source_config),
    )
    mapping = map_core.build_map(
        engine,
        now_ms=valuation_ms,
        snapshot=snapshot,
        background=background,
        references=references,
        observations=_snapshot_observations(snapshot, references),
        current_price_usd=_snapshot_price(snapshot),
        current_price_basis=_snapshot_price_basis(snapshot),
    )
    if source_config.get("source_mode") == SOURCE_MODE_PRODUCER_MANIFEST and isinstance(projection.get("source_consumer"), Mapping):
        mapping["source_consumer"] = deepcopy(dict(projection["source_consumer"]))
        mapping["map_id"] = map_core._hash({key: value for key, value in mapping.items() if key != "map_id"})
    return mapping


def attach_candidate_map(engine: Any, projection: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Attach MAP to a snapshot without changing the original accounting fields."""

    mapping = candidate_map(engine, projection, snapshot)
    return underwriting.attach_btc_map(snapshot, mapping)


def persistable_state(btc_map: Mapping[str, Any], previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a bounded state object while deduping repeated routine observations."""

    previous = previous if isinstance(previous, Mapping) else {}
    prior_events = list((previous.get("response_state") or {}).get("events") or [])
    seen = {_event_key(event) for event in prior_events}
    events = prior_events[-map_core.MAX_RESPONSE_EVENTS :]
    for event in (btc_map.get("response_state") or {}).get("events") or []:
        key = _event_key(event)
        if key in seen:
            continue
        if key not in seen:
            events.append(deepcopy(dict(event)))
            seen.add(key)
    return {
        "schema": "astra_map_projection_state@2.4.0",
        "updated_at_ms": btc_map.get("cutoff_at_ms"),
        "response_state": {
            "schema": "btc_map_response_state@1",
            "segments": deepcopy(((btc_map.get("response_state") or {}).get("segments")) or {}),
            "events": events[-map_core.MAX_RESPONSE_EVENTS :],
            "event_limit": map_core.MAX_RESPONSE_EVENTS,
        },
        "background_wrapper": {"rows": deepcopy((btc_map.get("background") or {}))},
        "last_map_id": btc_map.get("map_id"),
    }


def _event_key(event: Mapping[str, Any]) -> str:
    return map_core._hash(event)


def _background_for_map(wrapper: Mapping[str, Any], records: list[Mapping[str, Any]]) -> dict[str, Any]:
    rows = wrapper.get("rows") if isinstance(wrapper.get("rows"), Mapping) else {}
    background = {key: _normalize_background_row(value) for key, value in rows.items() if isinstance(value, Mapping)}
    background["facts_changed_since"] = deepcopy(wrapper.get("facts_changed_since_previous") or [])
    missing: list[str] = []
    for row in background.values():
        if isinstance(row, Mapping):
            missing.extend(str(item) for item in row.get("missing") or [])
    background["missing_or_conflicting"] = sorted(set(missing))
    background["source_records"] = [deepcopy(dict(record)) for record in records if isinstance(record, Mapping)]
    return background


def _normalize_background_row(row: Mapping[str, Any]) -> dict[str, Any]:
    out = deepcopy(dict(row))
    fact_state = out.get("fact_state")
    if not fact_state:
        raw_status = str(out.get("status") or "").strip().lower()
        if raw_status in {"ok", "usable", "qualified", "ready"}:
            fact_state = "usable"
        elif raw_status in {"partial", "degraded", "warning"}:
            fact_state = "partial"
        else:
            missing = out.get("missing") if isinstance(out.get("missing"), list) else []
            fact_state = "partial" if missing else "missing"
        out["fact_state"] = fact_state
    out["status"] = out.get("status") or str(fact_state)
    return out


def build_references(
    engine: Any,
    now_ms: int,
    *,
    fmz_fact: Mapping[str, Any] | None,
    source_records: list[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    refs: list[dict[str, Any]] = []
    gaps: list[str] = []
    accepted = _read_record(engine, GLOBAL_PRODUCT, now_ms, "space_gate")
    flip = _accepted_flip_reference(accepted)
    if flip is not None:
        refs.append(flip)
    else:
        gaps.append("gex_effective_flip_unavailable")
    cp = _cp_sth_reference(source_records)
    if cp is not None:
        refs.append(cp)
    else:
        gaps.append("cp_sth_unavailable")
    current_price = _current_price(fmz_fact)
    kpf_refs, kpf_gaps = _kpf_references(engine, now_ms, current_price_usd=current_price)
    refs.extend(kpf_refs)
    gaps.extend(kpf_gaps)
    return refs, gaps


def build_observations(fmz_fact: Mapping[str, Any] | None, references: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    current = _current_price(fmz_fact)
    if current is None:
        return []
    observed = _current_price_observed(fmz_fact)
    if observed is None:
        return []
    source = _current_price_basis(fmz_fact)
    out = []
    for ref in references:
        if ref.get("coordinate_qualification") != "COMPARABLE":
            continue
        if not _price_basis_matches_reference(source, ref):
            continue
        out.append({
            "reference_id": ref.get("reference_id"),
            "revision_id": ref.get("revision_id"),
            "observed_at_ms": observed,
            "known_at_ms": (fmz_fact or {}).get("snapshot_ts_ms") or observed,
            "method": "PRICE_SAMPLE",
            "price": current,
            "market": ref.get("market"),
            "price_basis": ref.get("price_basis"),
            "price_source": "workbench_current_price_same_basis",
            "coverage": "COMPLETE",
        })
    return out


def _snapshot_observations(snapshot: Mapping[str, Any], references: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    market = snapshot.get("market") if isinstance(snapshot.get("market"), Mapping) else {}
    current = _snapshot_price(snapshot)
    observed = _snapshot_price_observed(snapshot)
    if current is None or observed is None:
        return []
    source = _snapshot_price_basis(snapshot)
    out = []
    for ref in references:
        if ref.get("coordinate_qualification") != "COMPARABLE":
            continue
        if not _price_basis_matches_reference(source, ref):
            continue
        out.append({
            "reference_id": ref.get("reference_id"),
            "revision_id": ref.get("revision_id"),
            "observed_at_ms": observed,
            "known_at_ms": observed,
            "method": "PRICE_SAMPLE",
            "price": current,
            "market": ref.get("market"),
            "price_basis": ref.get("price_basis"),
            "price_source": str(source or "snapshot_reference_price"),
            "coverage": "COMPLETE",
        })
    return out


def _accepted_flip_reference(record: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not _record_current(record):
        return None
    values = _values(record)
    anchor = values.get("anchor") if isinstance(values.get("anchor"), Mapping) else values
    price = _number(anchor.get("effective_flip_point") or anchor.get("raw_flip_point"))
    clock = _record_time(record, "observation_end_ms")
    if price is None or clock is None:
        return None
    return {
        "reference_id": "gex-effective-flip",
        "revision_id": str(record.get("record_id") or "gex-effective-flip"),
        "family": "OPTION_STRUCTURE",
        "role": "EFFECTIVE_FLIP",
        "geometry": "POINT",
        "raw_price": price,
        "display_center": price,
        "market": "GEX",
        "quote_currency": "USD",
        "price_basis": "GLOBAL_ONLY",
        "expiry_scope": "GLOBAL_ONLY",
        "observation_end_ms": clock,
        "known_at_ms": _record_time(record, "first_seen_at_ms") or clock,
        "active_from_ms": clock,
        "quality": _record_quality(record),
        "usage_decision": deepcopy(record.get("usage_decision")),
        "source_record_ids": [record.get("record_id")],
        "source_record": deepcopy(dict(record)),
        "source_family": "gex_effective_accepted",
        "coordinate_qualification": "GLOBAL_ONLY",
        "limitations": ["global_only_not_expiry_specific", "nominal_structure_reference"],
    }


def _cp_sth_reference(records: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    for record in sorted(records, key=lambda item: _record_time(item, "observation_end_ms") or 0, reverse=True):
        if _product_id(record) != CP_PRODUCT or not _record_current(record):
            continue
        values = _values(record)
        price = _number(values.get("cp_sth_usd"))
        if price is None:
            cost = values.get("cost_basis") if isinstance(values.get("cost_basis"), Mapping) else {}
            price = _number(cost.get("cp_sth_usd") or cost.get("cp_sth"))
        if price is None:
            continue
        clock = _record_time(record, "observation_end_ms")
        return {
            "reference_id": "brk-cp-sth",
            "revision_id": str(record.get("record_id") or "brk-cp-sth"),
            "family": "ONCHAIN_COST",
            "role": "CP_STH",
            "geometry": "POINT",
            "raw_price": price,
            "display_center": price,
            "market": "BRK",
            "quote_currency": "USD",
            "price_basis": "ONCHAIN_COST_CP_STH",
            "expiry_scope": "GLOBAL_ONLY",
            "observation_end_ms": clock,
            "known_at_ms": _record_time(record, "first_seen_at_ms") or clock,
            "active_from_ms": clock,
            "quality": _record_quality(record),
            "usage_decision": deepcopy(record.get("usage_decision")),
            "source_record_ids": [record.get("record_id")],
            "source_record": deepcopy(dict(record)),
            "source_family": "brk_cost_basis",
            "coordinate_qualification": "NOMINAL_ONLY",
            "limitations": ["onchain_cost_not_execution_price", "nominal_only_not_contract_comparable"],
        }
    return None


def _kpf_references(engine: Any, now_ms: int, *, current_price_usd: float | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    record = _read_record(engine, KPF_PRODUCT, now_ms, "background")
    if not _record_current(record):
        return [], ["kpf_cached_record_unavailable"]
    values = _values(record)
    if values.get("valid") is False:
        gaps = [str(item).lower() for item in values.get("reason_codes") or []]
        return [], gaps or ["kpf_cached_record_not_qualified"]
    zones = values.get("zones") if isinstance(values.get("zones"), list) else []
    selected = _select_kpf_zones([zone for zone in zones if isinstance(zone, Mapping)], current_price_usd)
    refs = []
    for zone in selected:
        ref = _kpf_reference_from_zone(record, values, zone)
        if ref is not None:
            refs.append(ref)
    return refs, ([] if refs else ["kpf_raw_basin_geometry_missing"])


def _int_from(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or not number.is_integer() or number < 0:
        return None
    return int(number)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _as_mapping_list(value: Any) -> list[Mapping[str, Any]]:
    return [item for item in _as_list(value) if isinstance(item, Mapping)]


def _first_key(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _first_number(mapping: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        number = _number(mapping.get(key))
        if number is not None:
            return number
    return None


def _stable_hash(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_kpf_artifact(path: Any) -> dict[str, Any]:
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(KPF_MAX_ARTIFACT_BYTES + 1)
    except (OSError, TypeError):
        return {"path": str(path), "error": "kpf_same_version_artifacts_unreadable"}
    if len(raw) > KPF_MAX_ARTIFACT_BYTES:
        return {"path": str(path), "size_bytes": Path(path).stat().st_size, "error": "kpf_artifact_too_large"}
    sha = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"path": str(path), "sha256": sha, "size_bytes": len(raw), "error": "kpf_same_version_artifacts_unreadable"}
    return {"path": str(path), "sha256": sha, "size_bytes": len(raw), "payload": payload}


def _kpf_manifest_paths(config: Mapping[str, Any], artifact: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    payload = artifact.get("payload")
    if artifact.get("error") or not isinstance(payload, Mapping):
        raise ValueError("kpf_manifest_unreadable")
    if payload.get("schema") != "astra_kpf_bundle_manifest@1" or not payload.get("version_id"):
        raise ValueError("kpf_manifest_identity_invalid")
    base = Path(config["kpf_manifest_path"]).resolve().parent
    configured = dict(config)
    hashes = {}
    entries = payload.get("artifacts")
    if not isinstance(entries, Mapping):
        raise ValueError("kpf_manifest_artifacts_missing")
    for label, key in (("snapshot", "kpf_snapshot_path"), ("audit", "kpf_audit_path"), ("zones", "kpf_debug_path")):
        entry = entries.get(label)
        if not isinstance(entry, Mapping) or not isinstance(entry.get("path"), str):
            raise ValueError("kpf_manifest_artifacts_missing")
        raw_path = Path(entry["path"])
        digest = entry.get("sha256")
        if raw_path.is_absolute() or ".." in raw_path.parts or not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("kpf_manifest_artifact_invalid")
        resolved = (base / raw_path).resolve()
        if not resolved.is_relative_to(base):
            raise ValueError("kpf_manifest_path_outside_root")
        configured[key] = str(resolved)
        hashes[key] = digest.lower()
    return configured, hashes


def _reject_kpf_bundle(engine: Any, now_ms: int, artifacts: Mapping[str, Mapping[str, Any]], reason: str) -> dict[str, Any]:
    values = _kpf_record_values(version_id="REJECTED", artifacts=artifacts, zones=[], valid=False,
                                reason_codes=[reason.upper()])
    record = _ingest_kpf_record(engine, values, now_ms, source_revision=_stable_hash(values),
                                data_state="INVALID", reason_codes=[reason.upper()])
    return {"record": record, "gaps": [reason], "artifact_hashes": values["artifact_hashes"]}


def _kpf_record_values(
    *,
    version_id: str,
    artifacts: Mapping[str, Mapping[str, Any]],
    zones: list[Mapping[str, Any]],
    valid: bool,
    reason_codes: list[str],
    raw_zone_count: int = 0,
    finite_zone_count: int = 0,
) -> dict[str, Any]:
    artifact_view = {
        name: {
            "sha256": item.get("sha256"),
            "size_bytes": item.get("size_bytes"),
            "path_name": Path(str(item.get("path") or name)).name,
        }
        for name, item in artifacts.items()
    }
    return {
        "schema": "btc_kpf_map_readonly_values@2.4.0",
        "version_id": version_id,
        "artifact_hashes": {name: item.get("sha256") for name, item in artifact_view.items()},
        "artifacts": artifact_view,
        "raw_zone_count": raw_zone_count,
        "finite_zone_count": finite_zone_count,
        "zone_count": len(zones),
        "max_finite_pool": KPF_MAX_POOL,
        "valid": bool(valid),
        "reason_codes": list(reason_codes),
        "zones": [deepcopy(dict(zone)) for zone in zones],
    }


def _ingest_kpf_record(
    engine: Any,
    values: Mapping[str, Any],
    now_ms: int,
    *,
    source_revision: str,
    data_state: str,
    reason_codes: list[str],
) -> dict[str, Any] | None:
    observation_end = _kpf_record_observation_end(values) or now_ms
    try:
        return engine.ingest(
            KPF_PRODUCT,
            values,
            observation_end_ms=observation_end,
            retrieved_at_ms=None,
            ingested_at_ms=now_ms,
            source_revision=source_revision,
            data_state=data_state,
            reason_codes=reason_codes,
            source_ref="local_same_version_kpf_artifacts",
        )
    except Exception:
        return None


def _kpf_record_observation_end(values: Mapping[str, Any]) -> int | None:
    clocks = [_int_from(zone.get("observation_end_ms")) for zone in _as_mapping_list(values.get("zones"))]
    clocks = [clock for clock in clocks if clock is not None]
    return max(clocks) if clocks else None


def _normalize_kpf_zone(item: Mapping[str, Any], index: int) -> dict[str, Any]:
    low = _first_number(item, "raw_basin_low", "raw_low", "low", "basin_low", "zone_low")
    high = _first_number(item, "raw_basin_high", "raw_high", "high", "basin_high", "zone_high")
    finite = low is not None and high is not None
    usage = item.get("usage_decision") if isinstance(item.get("usage_decision"), Mapping) else {}
    observation_end = _int_from(_first_key(item, "observation_end_ms", "source_observation_end_ms"))
    known_at = _int_from(_first_key(item, "known_at_ms", "source_known_at_ms"))
    active_from = _int_from(item.get("active_from_ms")) or observation_end
    ref_id = str(item.get("reference_id") or item.get("region_id") or f"kpf-zone-{index}")
    display_center = _first_number(item, "display_center")
    if display_center is None and finite:
        display_center = (low + high) / 2.0
    normalized = {
        "reference_id": ref_id,
        "revision_id": item.get("revision_id"),
        "raw_low": min(low, high) if finite else None,
        "raw_high": max(low, high) if finite else None,
        "display_center": display_center,
        "finite_geometry": bool(finite),
        "market": item.get("market"),
        "quote_currency": item.get("quote_currency") or "USD",
        "price_basis": item.get("price_basis"),
        "expiry_scope": item.get("expiry_scope") or "GLOBAL_ONLY",
        "observation_end_ms": observation_end,
        "known_at_ms": known_at,
        "active_from_ms": active_from,
        "quality": item.get("quality") or "OK",
        "usage_decision": deepcopy(dict(usage)) if usage else {},
        "source_record_ids": [str(value) for value in _as_list(item.get("source_record_ids")) if value],
        "mapping_policy": deepcopy(item.get("mapping_policy")) if isinstance(item.get("mapping_policy"), Mapping) else item.get("mapping_policy"),
        "mapping_quality": deepcopy(item.get("mapping_quality")) if isinstance(item.get("mapping_quality"), Mapping) else item.get("mapping_quality"),
        "coordinate_qualification": item.get("coordinate_qualification"),
        "original_zone": deepcopy(dict(item)),
    }
    normalized["qualification_complete"] = _kpf_zone_qualification_complete(normalized)
    normalized["coordinate_qualification"] = _kpf_coordinate_qualification(normalized)
    return normalized


def _kpf_zone_qualification_complete(zone: Mapping[str, Any]) -> bool:
    usage = zone.get("usage_decision") if isinstance(zone.get("usage_decision"), Mapping) else {}
    return (
        zone.get("finite_geometry") is True
        and usage.get("can_use") is True
        and bool(zone.get("market"))
        and bool(zone.get("price_basis"))
        and _int_from(zone.get("observation_end_ms")) is not None
        and _int_from(zone.get("known_at_ms")) is not None
    )


def _kpf_coordinate_qualification(zone: Mapping[str, Any]) -> str:
    if not _kpf_zone_qualification_complete(zone):
        return "UNKNOWN"
    if _native_deribit_basis(zone) or _has_qualified_mapping(zone):
        return "COMPARABLE"
    return "NOMINAL_ONLY"


def _native_deribit_basis(zone: Mapping[str, Any]) -> bool:
    market = str(zone.get("market") or "").upper()
    basis = str(zone.get("price_basis") or "").upper()
    return "DERIBIT" in market and "BTC" in basis and ("USD" in basis or "INDEX" in basis)


def _has_qualified_mapping(zone: Mapping[str, Any]) -> bool:
    quality = zone.get("mapping_quality")
    if isinstance(quality, Mapping):
        if quality.get("status") in {"OK", "QUALIFIED", "USABLE"} or quality.get("can_use") is True:
            return bool(zone.get("mapping_policy"))
    if str(quality or "").upper() in {"OK", "QUALIFIED", "QUALIFIED_CONVERSION"}:
        return bool(zone.get("mapping_policy"))
    return False


def _select_kpf_zones(zones: list[Mapping[str, Any]], current_price_usd: float | None) -> list[Mapping[str, Any]]:
    usable = [zone for zone in zones if zone.get("finite_geometry") and _kpf_zone_explicitly_usable(zone)]
    if current_price_usd is not None:
        usable = sorted(usable, key=lambda zone: (_zone_distance(zone, current_price_usd), str(zone.get("reference_id") or "")))
    else:
        usable = sorted(usable, key=lambda zone: str(zone.get("reference_id") or ""))
    # MAP core alone applies the two-reference display limit. Preserve the
    # complete qualified pool here for responsibility mapping and audit.
    return usable


def _kpf_zone_explicitly_usable(zone: Mapping[str, Any]) -> bool:
    usage = zone.get("usage_decision") if isinstance(zone.get("usage_decision"), Mapping) else {}
    return usage.get("can_use") is True


def _zone_distance(zone: Mapping[str, Any], price: float) -> float:
    low = _number(zone.get("raw_low"))
    high = _number(zone.get("raw_high"))
    if low is None or high is None:
        return math.inf
    if low <= price <= high:
        return 0.0
    return min(abs(price - low), abs(price - high))


def _kpf_reference_from_zone(record: Mapping[str, Any], values: Mapping[str, Any], zone: Mapping[str, Any]) -> dict[str, Any] | None:
    low, high = _number(zone.get("raw_low")), _number(zone.get("raw_high"))
    if low is None or high is None:
        return None
    usage = zone.get("usage_decision") if isinstance(zone.get("usage_decision"), Mapping) else {}
    complete = _kpf_zone_qualification_complete(zone)
    qual = _kpf_coordinate_qualification(zone)
    observation_end = _int_from(zone.get("observation_end_ms"))
    known_at = _int_from(zone.get("known_at_ms"))
    active_from = _int_from(zone.get("active_from_ms")) or observation_end
    return {
        "reference_id": zone.get("reference_id"),
        "revision_id": str(values.get("version_id") or record.get("record_id") or "kpf"),
        "family": "TRADED_ACCEPTANCE",
        "role": "KPF_BASIN",
        "geometry": "OBSERVED_ZONE",
        "raw_low": min(low, high),
        "raw_high": max(low, high),
        "display_center": (low + high) / 2.0,
        "market": zone.get("market"),
        "quote_currency": zone.get("quote_currency") or "USD",
        "price_basis": zone.get("price_basis"),
        "expiry_scope": zone.get("expiry_scope") or "GLOBAL_ONLY",
        "observation_end_ms": observation_end,
        "known_at_ms": known_at,
        "active_from_ms": active_from,
        "quality": zone.get("quality") or ("OK" if complete else "PARTIAL"),
        "usage_decision": deepcopy(dict(usage)) if complete else {
            "can_use": False,
            "status": "KPF_QUALIFICATION_INCOMPLETE",
            "reason_codes": ["KPF_NEEDS_EXPLICIT_USAGE_MARKET_BASIS_AND_CLOCK"],
        },
        "source_record_ids": [record.get("record_id")],
        "source_record": deepcopy(dict(record)),
        "source_family": "kpf",
        "coordinate_qualification": qual,
        "limitations": ["kpf_local_read_only_same_version"] + ([] if qual == "COMPARABLE" else ["kpf_not_strict_without_native_deribit_or_qualified_mapping"]),
    }


def _extract_kpf_zones(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        for key in ("zones", "regions", "basins", "kpf_regions"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
        for value in payload.values():
            found = _extract_kpf_zones(value)
            if found:
                return found
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    return []


def _kpf_version(payload: Any) -> str | None:
    if isinstance(payload, Mapping):
        for key in ("version_id", "snapshot_id", "version", "dataset_hash", "audit_hash", "debug_hash"):
            if payload.get(key):
                return str(payload[key])
        meta = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
        for key in ("snapshot_id", "version", "dataset_hash"):
            if meta.get(key):
                return str(meta[key])
    return None


def _read_record(engine: Any, product: str, now_ms: int, usage: str) -> dict[str, Any] | None:
    try:
        record = engine.read(product, now_ms=now_ms, usage=usage)
    except Exception:
        return None
    return record if isinstance(record, Mapping) else None


def _record_current(record: Mapping[str, Any] | None) -> bool:
    if not isinstance(record, Mapping):
        return False
    decision = record.get("usage_decision") if isinstance(record.get("usage_decision"), Mapping) else {}
    if decision.get("can_use") is False:
        return False
    if record.get("schema") != "astra_data_record@2.3.0":
        return False
    quality = record.get("quality") if isinstance(record.get("quality"), Mapping) else {}
    if quality.get("data_state") in {"INVALID", "STALE", "ERROR", "MISSING"}:
        return False
    return True


def _values(record: Mapping[str, Any]) -> dict[str, Any]:
    content = record.get("content") if isinstance(record.get("content"), Mapping) else {}
    values = content.get("values") if isinstance(content.get("values"), Mapping) else {}
    return dict(values)


def _record_time(record: Mapping[str, Any], key: str) -> int | None:
    time_part = record.get("time") if isinstance(record.get("time"), Mapping) else {}
    value = time_part.get(key)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _record_quality(record: Mapping[str, Any]) -> str:
    quality = record.get("quality") if isinstance(record.get("quality"), Mapping) else {}
    return str(quality.get("data_state") or "OK")


def _product_id(record: Mapping[str, Any]) -> str | None:
    identity = record.get("identity") if isinstance(record.get("identity"), Mapping) else {}
    product_id = identity.get("product_id")
    return str(product_id) if product_id else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _current_price(fmz_fact: Mapping[str, Any] | None) -> float | None:
    if not isinstance(fmz_fact, Mapping):
        return None
    return _number(fmz_fact.get("current_price_usd"))


def _current_price_basis(fmz_fact: Mapping[str, Any] | None) -> str | None:
    if not isinstance(fmz_fact, Mapping):
        return None
    source = fmz_fact.get("current_price_source")
    return str(source) if source else None


def _current_price_observed(fmz_fact: Mapping[str, Any] | None) -> int | None:
    if not isinstance(fmz_fact, Mapping):
        return None
    value = fmz_fact.get("price_observed_ms")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _snapshot_price(snapshot: Mapping[str, Any]) -> float | None:
    market = snapshot.get("market") if isinstance(snapshot.get("market"), Mapping) else {}
    return _number(market.get("reference_price_usd"))


def _snapshot_price_observed(snapshot: Mapping[str, Any]) -> int | None:
    market = snapshot.get("market") if isinstance(snapshot.get("market"), Mapping) else {}
    quote = market.get("quote") if isinstance(market.get("quote"), Mapping) else {}
    candidates = (
        _nested_int(quote, "exchange_times_ms", "short"),
        _nested_int(quote, "short_book", "timestamp"),
    )
    return next((item for item in candidates if item is not None), None)


def _snapshot_price_basis(snapshot: Mapping[str, Any]) -> str | None:
    market = snapshot.get("market") if isinstance(snapshot.get("market"), Mapping) else {}
    source = market.get("reference_source") or market.get("price_basis")
    return str(source) if source else None


def _nested_int(mapping: Mapping[str, Any], *keys: str) -> int | None:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return _int_from(current)


def _price_basis_matches_reference(source: Any, ref: Mapping[str, Any]) -> bool:
    if not source:
        return False
    source_text = str(source).strip().upper()
    ref_market = str(ref.get("market") or "").strip().upper()
    ref_basis = str(ref.get("price_basis") or "").strip().upper()
    if not ref_basis:
        return False
    if source_text == ref_basis:
        return True
    if source_text == "DERIBIT_INDEX":
        return "DERIBIT" in ref_market and ("BTC_USD" in ref_basis or "BTC-USD" in ref_basis or "INDEX" in ref_basis)
    return False
