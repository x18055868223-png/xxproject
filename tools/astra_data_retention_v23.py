"""Retain compact Astra v2.3 FMZ data snapshots from rotated raw logs.

The tool is deliberately narrow: it only recognizes FMZ workbench archives named
``snapshots.jsonl-YYYYMMDD-HHMMSS.gz`` or ``decisions.jsonl-YYYYMMDD-HHMMSS.gz``.
It freezes the last complete family-allowed native snapshot row into a compact
retained record before unlinking an expired raw gzip.  It never edits live JSONL
files, never touches LLM/budget/sidecar ledgers, and defaults to dry-run.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


RETENTION_SCHEMA = "astra_data_retention@2.3.0"
FREEZE_SCHEMA = "astra_retained_evaluation_snapshot@2.3.0"
RECEIPT_SCHEMA = "astra_data_retention_receipt@2.3.0"
LATEST_SCHEMA = "astra_latest_evaluation_snapshot@2.3.0"
RAW_ARCHIVE_RE = re.compile(r"^(snapshots|decisions)\.jsonl-(\d{8})-(\d{6})\.gz$")
DEFAULT_RAW_PROTECT_HOURS = 48
DEFAULT_LATEST_RAW_PROTECT = 2
DEFAULT_MAX_RETAINED_INDEX = 12
FAMILY_RECORD_KINDS = {
    "snapshots": ("EvaluationSnapshot",),
    "decisions": ("DecisionSnapshot", "EvaluationSnapshot"),
}


class RetentionError(RuntimeError):
    """Raised for unsafe input paths or retention failures."""


@dataclass(frozen=True)
class FileIdentity:
    st_dev: int
    st_ino: int
    st_size: int
    st_mtime_ns: int

    @classmethod
    def from_stat(cls, st: os.stat_result) -> "FileIdentity":
        return cls(
            st_dev=int(getattr(st, "st_dev", 0)),
            st_ino=int(getattr(st, "st_ino", 0)),
            st_size=int(st.st_size),
            st_mtime_ns=int(st.st_mtime_ns),
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "st_dev": self.st_dev,
            "st_ino": self.st_ino,
            "st_size": self.st_size,
            "st_mtime_ns": self.st_mtime_ns,
        }


@dataclass(frozen=True)
class ArchiveFile:
    path: Path
    family: str
    archive_dt: datetime
    archive_ts_ms: int
    identity: FileIdentity


def _now_ms() -> int:
    return int(time.time() * 1000)


def _parse_archive_name(path: Path) -> tuple[str, datetime] | None:
    match = RAW_ARCHIVE_RE.match(path.name)
    if not match:
        return None
    family, ymd, hms = match.groups()
    parsed = datetime.strptime(ymd + hms, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return family, parsed


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hash_json(value: Any) -> str:
    return _hash_bytes(_json_dumps(value).encode("utf-8"))


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _assert_no_symlink_existing_parts(path: Path) -> None:
    current = path.anchor
    probe = Path(current) if current else Path()
    parts = path.parts[1:] if current else path.parts
    for part in parts:
        probe = probe / part
        if not probe.exists():
            continue
        try:
            mode = probe.lstat().st_mode
        except OSError as exc:
            raise RetentionError(f"cannot stat path component: {probe}") from exc
        if stat.S_ISLNK(mode):
            raise RetentionError(f"symlink path component rejected: {probe}")


def _resolve_existing_dir(path: Path, name: str) -> Path:
    if not path.is_absolute():
        raise RetentionError(f"{name} must be an absolute path")
    _assert_no_symlink_existing_parts(path)
    if not path.is_dir():
        raise RetentionError(f"{name} must exist and be a directory: {path}")
    return path.resolve(strict=True)


def _resolve_output_dir(path: Path, name: str, *, apply: bool) -> Path:
    if not path.is_absolute():
        raise RetentionError(f"{name} must be an absolute path")
    _assert_no_symlink_existing_parts(path)
    if path.exists() and not path.is_dir():
        raise RetentionError(f"{name} must be a directory: {path}")
    parent = path if path.exists() else path.parent
    _assert_no_symlink_existing_parts(parent)
    if not parent.is_dir():
        raise RetentionError(f"{name} parent must exist: {parent}")
    resolved_parent = parent.resolve(strict=True)
    resolved = path.resolve(strict=False) if path.exists() else resolved_parent / path.name
    if apply:
        path.mkdir(parents=True, exist_ok=True)
        _assert_no_symlink_existing_parts(path)
    return resolved


def _validate_dirs(fmz_log_dir: Path, retained_dir: Path, *, apply: bool) -> tuple[Path, Path]:
    fmz_resolved = _resolve_existing_dir(fmz_log_dir, "fmz-log-dir")
    retained_resolved = _resolve_output_dir(retained_dir, "retained-dir", apply=apply)
    if fmz_resolved == retained_resolved:
        raise RetentionError("fmz-log-dir and retained-dir must not be the same directory")
    if _is_relative_to(fmz_resolved, retained_resolved) or _is_relative_to(retained_resolved, fmz_resolved):
        raise RetentionError("fmz-log-dir and retained-dir must not overlap")
    return fmz_resolved, retained_resolved


def _safe_lstat_file(path: Path) -> FileIdentity:
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode):
        raise RetentionError(f"symlink archive rejected: {path}")
    if not stat.S_ISREG(st.st_mode):
        raise RetentionError(f"non-file archive rejected: {path}")
    return FileIdentity.from_stat(st)


def _same_identity(path: Path, expected: FileIdentity) -> bool:
    try:
        current = _safe_lstat_file(path)
    except (OSError, RetentionError):
        return False
    return current == expected


def discover_archives(fmz_log_dir: Path) -> list[ArchiveFile]:
    archives: list[ArchiveFile] = []
    for item in fmz_log_dir.iterdir():
        parsed = _parse_archive_name(item)
        if parsed is None or not item.is_file():
            continue
        identity = _safe_lstat_file(item)
        family, archive_dt = parsed
        archives.append(
            ArchiveFile(
                path=item,
                family=family,
                archive_dt=archive_dt,
                archive_ts_ms=int(archive_dt.timestamp() * 1000),
                identity=identity,
            )
        )
    return sorted(archives, key=lambda item: (item.family, item.archive_ts_ms, item.path.name))


def _current_snapshots_empty(fmz_log_dir: Path) -> bool:
    current = fmz_log_dir / "snapshots.jsonl"
    try:
        st = current.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    return stat.S_ISREG(st.st_mode) and st.st_size == 0


def _record_from_row(row: Any, family: str) -> tuple[str, dict[str, Any]] | None:
    allowed = set(FAMILY_RECORD_KINDS.get(family, ()))
    if isinstance(row, dict) and row.get("schema_name") in allowed:
        return str(row.get("schema_name")), row
    if not isinstance(row, Mapping):
        return None
    wrapped_keys = []
    if "EvaluationSnapshot" in allowed:
        wrapped_keys.extend(("evaluation_snapshot", "snapshot", "fmz_snapshot", "record"))
    if "DecisionSnapshot" in allowed:
        wrapped_keys.extend(("decision_snapshot", "native_decision_snapshot"))
    for key in wrapped_keys:
        value = row.get(key)
        if isinstance(value, dict) and value.get("schema_name") in allowed:
            return str(value.get("schema_name")), value
    return None


def _record_id(record: Mapping[str, Any], fallback: str) -> str:
    for key in ("id", "snapshot_id", "decision_id", "evaluation_id", "card_id"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    decision = record.get("decision")
    if isinstance(decision, Mapping):
        for key in ("id", "card_id", "evaluation_id", "decision_id"):
            value = decision.get(key)
            if isinstance(value, str) and value:
                return value
        ts = decision.get("ts_ms")
        if isinstance(ts, int) and not isinstance(ts, bool):
            return f"ts_ms:{ts}"
    return fallback


def _source_clock(record: Mapping[str, Any]) -> dict[str, Any]:
    clock: dict[str, Any] = {}
    source_clock = record.get("source_clock")
    if isinstance(source_clock, Mapping):
        clock["source_clock"] = dict(source_clock)
    for key in ("ts_ms", "asof_ms", "observed_at_ms", "source_ts_ms", "created_at_ms"):
        value = record.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            clock[key] = value
    decision = record.get("decision")
    if isinstance(decision, Mapping):
        for key in ("ts_ms", "asof_ms", "observed_at_ms", "source_ts_ms"):
            value = decision.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                clock[f"decision.{key}"] = value
    runtime = decision.get("runtime_facts") if isinstance(decision, Mapping) else None
    if isinstance(runtime, Mapping):
        value = runtime.get("current_price_observed_ms")
        if isinstance(value, int) and not isinstance(value, bool):
            clock["decision.runtime_facts.current_price_observed_ms"] = value
    return clock


def scan_last_retained_record(path: Path, family: str) -> dict[str, Any]:
    """Return details for the last complete family-allowed native snapshot row."""
    last_record: dict[str, Any] | None = None
    last_kind: str | None = None
    last_raw = b""
    last_line_no = 0
    rows_seen = 0
    retained_rows = 0
    bad_lines = 0
    incomplete_tail_lines = 0
    bytes_read = 0
    try:
        with path.open("rb") as raw_handle:
            before = FileIdentity.from_stat(os.fstat(raw_handle.fileno()))
            expected = _safe_lstat_file(path)
            if before != expected:
                raise RetentionError(f"archive changed before read: {path}")
            with gzip.GzipFile(fileobj=raw_handle, mode="rb") as stream:
                for line_no, raw in enumerate(stream, 1):
                    bytes_read += len(raw)
                    stripped = raw.strip()
                    if not stripped:
                        continue
                    rows_seen += 1
                    if not raw.endswith(b"\n"):
                        incomplete_tail_lines += 1
                    try:
                        row = json.loads(stripped)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        bad_lines += 1
                        continue
                    retained = _record_from_row(row, family)
                    if retained is None:
                        continue
                    last_kind, last_record = retained
                    last_raw = stripped
                    last_line_no = line_no
                    retained_rows += 1
    except (OSError, EOFError, gzip.BadGzipFile, zlib.error) as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "rows_seen": rows_seen,
            "retained_rows": retained_rows,
            "bad_lines": bad_lines,
            "incomplete_tail_lines": incomplete_tail_lines,
            "bytes_read": bytes_read,
        }
    if last_record is None or last_kind is None:
        return {
            "ok": False,
            "error": "no_complete_family_allowed_snapshot",
            "allowed_record_kinds": list(FAMILY_RECORD_KINDS.get(family, ())),
            "rows_seen": rows_seen,
            "retained_rows": retained_rows,
            "bad_lines": bad_lines,
            "incomplete_tail_lines": incomplete_tail_lines,
            "bytes_read": bytes_read,
        }
    row_hash = _hash_json(last_record)
    tail_hash = _hash_bytes(last_raw)
    return {
        "ok": True,
        "record": last_record,
        "record_kind": last_kind,
        "record_id": _record_id(last_record, f"tail_sha256:{tail_hash[:16]}"),
        "source_schema_name": last_record.get("schema_name"),
        "source_schema_version": last_record.get("schema_version"),
        "source_clock": _source_clock(last_record),
        "row_canonical_sha256": row_hash,
        "source_tail_sha256": tail_hash,
        "source_line_no": last_line_no,
        "rows_seen": rows_seen,
        "retained_rows": retained_rows,
        "bad_lines": bad_lines,
        "incomplete_tail_lines": incomplete_tail_lines,
        "bytes_read": bytes_read,
    }


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _json_dumps(dict(value)) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if loaded != dict(value):
            raise RetentionError(f"atomic write readback mismatch: {path}")
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        finally:
            raise


def _receipt_path(retained_dir: Path, archive: ArchiveFile) -> Path:
    return retained_dir / "receipts" / f"{archive.path.name}.receipt.json"


def _freeze_path(retained_dir: Path, archive: ArchiveFile) -> Path:
    return retained_dir / "snapshots" / f"{archive.path.name}.snapshot.json"


def _protected_identity_hash(archive: ArchiveFile) -> str:
    identity = {
        "name": archive.path.name,
        "size": archive.identity.st_size,
        "mtime_ns": archive.identity.st_mtime_ns,
        "archive_ts_ms": archive.archive_ts_ms,
    }
    return _hash_json(identity)


def _archive_plan(
    archives: Iterable[ArchiveFile],
    *,
    now_ms: int,
    protect_hours: int,
    latest_raw_protect: int,
    snapshot_current_empty: bool,
) -> list[dict[str, Any]]:
    by_family: dict[str, list[ArchiveFile]] = {"snapshots": [], "decisions": []}
    for archive in archives:
        by_family.setdefault(archive.family, []).append(archive)
    latest_names: set[str] = set()
    for items in by_family.values():
        for archive in sorted(items, key=lambda item: item.archive_ts_ms, reverse=True)[:latest_raw_protect]:
            latest_names.add(archive.path.name)
    cutoff_ms = now_ms - protect_hours * 3600 * 1000
    plan: list[dict[str, Any]] = []
    for archive in archives:
        reasons: list[str] = []
        if archive.path.name in latest_names:
            reasons.append("latest_raw_protect")
        if archive.archive_ts_ms >= cutoff_ms:
            reasons.append("within_raw_protect_hours")
        if snapshot_current_empty and archive.family == "snapshots":
            reasons.append("current_snapshots_jsonl_empty")
        action = "protect_raw" if reasons else "freeze_and_unlink"
        plan.append({
            "archive": archive,
            "action": action,
            "protected_reasons": reasons,
            "protected_identity_sha256": _protected_identity_hash(archive) if reasons else None,
        })
    return plan


def _update_index(retained_dir: Path, *, max_entries: int) -> None:
    snapshots_dir = retained_dir / "snapshots"
    entries: list[dict[str, Any]] = []
    if snapshots_dir.is_dir():
        for path in snapshots_dir.glob("*.snapshot.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            entries.append({
                "path": str(path),
                "source_file_name": value.get("source_file_name"),
                "source_family": value.get("source_family"),
                "archive_ts_ms": value.get("source_archive_ts_ms"),
                "snapshot_id": value.get("snapshot_id"),
                "row_canonical_sha256": value.get("row_canonical_sha256"),
                "retained_record_kind": value.get("retained_record_kind"),
            })
    entries.sort(key=lambda item: (item.get("archive_ts_ms") or 0, item.get("source_file_name") or ""), reverse=True)
    index = {
        "schema": "astra_retained_snapshot_index@2.3.0",
        "max_entries": max_entries,
        "entries": entries[:max_entries],
        "entry_count": min(len(entries), max_entries),
        "available_receipts_note": "Per-file receipts are retained separately; this index is only a bounded latest-cache view.",
    }
    _atomic_write_json(retained_dir / "retained_index.json", index)


def freeze_archive(
    archive: ArchiveFile,
    retained_dir: Path,
    *,
    now_ms: int,
    apply: bool,
) -> dict[str, Any]:
    scan = scan_last_retained_record(archive.path, archive.family)
    base_receipt = {
        "schema": RECEIPT_SCHEMA,
        "source_file_name": archive.path.name,
        "source_family": archive.family,
        "source_archive_ts_ms": archive.archive_ts_ms,
        "source_size_bytes": archive.identity.st_size,
        "source_identity": archive.identity.as_dict(),
        "receipt_ts_ms": now_ms,
        "zero_llm": True,
        "llm_calls": 0,
    }
    if not scan.get("ok"):
        receipt = {
            **base_receipt,
            "action": "skip_unreadable_or_no_snapshot",
            "applied": False,
            "unlinked": False,
            "receipt_path": str(_receipt_path(retained_dir, archive)),
            "scan": {k: v for k, v in scan.items() if k != "record"},
        }
        if apply:
            receipt["applied"] = True
            _atomic_write_json(_receipt_path(retained_dir, archive), receipt)
        return receipt
    freeze_record = {
        "schema": FREEZE_SCHEMA,
        "source_file_name": archive.path.name,
        "source_family": archive.family,
        "source_archive_ts_ms": archive.archive_ts_ms,
        "source_size_bytes": archive.identity.st_size,
        "source_identity": archive.identity.as_dict(),
        "source_line_no": scan["source_line_no"],
        "source_tail_sha256": scan["source_tail_sha256"],
        "snapshot_id": scan["record_id"],
        "retained_record_kind": scan["record_kind"],
        "source_schema_name": scan["source_schema_name"],
        "source_schema_version": scan["source_schema_version"],
        "source_clock": scan["source_clock"],
        "row_canonical_sha256": scan["row_canonical_sha256"],
        "retained_at_ms": now_ms,
        "zero_llm": True,
        "normalized_full_row": scan["record"],
    }
    if scan["record_kind"] == "EvaluationSnapshot":
        freeze_record["normalized_evaluation_snapshot"] = scan["record"]
    elif scan["record_kind"] == "DecisionSnapshot":
        freeze_record["normalized_decision_snapshot"] = scan["record"]
    receipt = {
        **base_receipt,
        "action": "freeze_and_unlink",
        "snapshot_id": scan["record_id"],
        "retained_record_kind": scan["record_kind"],
        "source_schema_name": scan["source_schema_name"],
        "source_schema_version": scan["source_schema_version"],
        "source_clock": scan["source_clock"],
        "source_tail_sha256": scan["source_tail_sha256"],
        "row_canonical_sha256": scan["row_canonical_sha256"],
        "scan": {k: v for k, v in scan.items() if k != "record"},
        "freeze_path": str(_freeze_path(retained_dir, archive)),
        "receipt_path": str(_receipt_path(retained_dir, archive)),
        "applied": bool(apply),
        "unlinked": False,
    }
    if not apply:
        return receipt
    if not _same_identity(archive.path, archive.identity):
        raise RetentionError(f"archive changed before freeze unlink: {archive.path}")
    freeze_path = _freeze_path(retained_dir, archive)
    receipt_path = _receipt_path(retained_dir, archive)
    _atomic_write_json(freeze_path, freeze_record)
    receipt["freeze_readback_sha256"] = _hash_json(json.loads(freeze_path.read_text(encoding="utf-8")))
    if not _same_identity(archive.path, archive.identity):
        raise RetentionError(f"archive changed after freeze write: {archive.path}")
    _atomic_write_json(receipt_path, receipt)
    if not _same_identity(archive.path, archive.identity):
        raise RetentionError(f"archive changed after receipt write: {archive.path}")
    archive.path.unlink()
    receipt["unlinked"] = True
    _atomic_write_json(receipt_path, receipt)
    return receipt


def maintain(
    *,
    fmz_log_dir: Path,
    retained_dir: Path,
    apply: bool = False,
    now_ms: int | None = None,
    protect_hours: int = DEFAULT_RAW_PROTECT_HOURS,
    latest_raw_protect: int = DEFAULT_LATEST_RAW_PROTECT,
    max_retained_index: int = DEFAULT_MAX_RETAINED_INDEX,
    refresh_current: bool = False,
) -> dict[str, Any]:
    if apply and (fmz_log_dir is None or retained_dir is None):
        raise RetentionError("--apply requires explicit --fmz-log-dir and --retained-dir")
    now_ms = _now_ms() if now_ms is None else int(now_ms)
    fmz_dir, retain_dir = _validate_dirs(fmz_log_dir, retained_dir, apply=apply)
    archives = discover_archives(fmz_dir)
    snapshot_empty = _current_snapshots_empty(fmz_dir)
    plan = _archive_plan(
        archives,
        now_ms=now_ms,
        protect_hours=protect_hours,
        latest_raw_protect=latest_raw_protect,
        snapshot_current_empty=snapshot_empty,
    )
    receipts: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []
    for item in plan:
        archive: ArchiveFile = item["archive"]
        if item["action"] == "protect_raw":
            receipt = {
                "schema": RECEIPT_SCHEMA,
                "source_file_name": archive.path.name,
                "source_family": archive.family,
                "source_archive_ts_ms": archive.archive_ts_ms,
                "source_size_bytes": archive.identity.st_size,
                "source_identity": archive.identity.as_dict(),
                "action": "protect_raw",
                "protected_reasons": item["protected_reasons"],
                "protected_identity_sha256": item["protected_identity_sha256"],
                "applied": bool(apply),
                "unlinked": False,
                "receipt_path": str(_receipt_path(retain_dir, archive)),
                "zero_llm": True,
                "llm_calls": 0,
            }
            if apply:
                _atomic_write_json(_receipt_path(retain_dir, archive), receipt)
            protected.append(receipt)
            continue
        receipts.append(freeze_archive(archive, retain_dir, now_ms=now_ms, apply=apply))
    if apply:
        _update_index(retain_dir, max_entries=max_retained_index)
    refresh_result = None
    if refresh_current:
        refresh_result = refresh_latest_current(
            fmz_log_dir=fmz_dir,
            retained_dir=retain_dir,
            now_ms=now_ms,
            apply=apply,
        )
    summary = {
        "schema": RETENTION_SCHEMA,
        "mode": "apply" if apply else "dry-run",
        "fmz_log_dir": str(fmz_dir),
        "retained_dir": str(retain_dir),
        "now_ms": now_ms,
        "recognized_archives": len(archives),
        "protected_raw_count": len(protected),
        "freeze_candidate_count": len(receipts),
        "deleted_count": sum(1 for receipt in receipts if receipt.get("unlinked")),
        "snapshot_current_empty": snapshot_empty,
        "zero_llm": True,
        "llm_calls": 0,
        "protected": protected,
        "receipts": receipts,
    }
    if refresh_result is not None:
        summary["refresh_current"] = refresh_result
    return summary


def _iter_current_jsonl(path: Path) -> Iterable[tuple[bytes, Any]]:
    with path.open("rb") as handle:
        for raw in handle:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                yield stripped, json.loads(stripped)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue


def _is_live_nonfuture_snapshot(snapshot: Mapping[str, Any], now_ms: int) -> bool:
    decision = snapshot.get("decision")
    if not isinstance(decision, Mapping):
        return False
    ts_ms = decision.get("ts_ms")
    if not isinstance(ts_ms, int) or isinstance(ts_ms, bool) or ts_ms > now_ms:
        return False
    runtime = decision.get("runtime_facts")
    if not isinstance(runtime, Mapping):
        return False
    if runtime.get("runtime_mode") != "live_public_read_only":
        return False
    return True


def refresh_latest_current(
    *,
    fmz_log_dir: Path,
    retained_dir: Path,
    now_ms: int | None = None,
    latest_json: Path | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    now_ms = _now_ms() if now_ms is None else int(now_ms)
    fmz_dir, retain_dir = _validate_dirs(fmz_log_dir, retained_dir, apply=apply)
    source = fmz_dir / "snapshots.jsonl"
    target = latest_json if latest_json is not None else retain_dir / "latest.json"
    if not target.is_absolute():
        target = retain_dir / target
    if target.exists() and stat.S_ISLNK(target.lstat().st_mode):
        raise RetentionError(f"latest-json symlink rejected: {target}")
    result = {
        "schema": "astra_data_retention_refresh_current@2.3.0",
        "mode": "apply" if apply else "dry-run",
        "source": str(source),
        "latest_json": str(target),
        "now_ms": now_ms,
        "bridge_written": False,
        "zero_llm": True,
        "llm_calls": 0,
    }
    try:
        identity = _safe_lstat_file(source)
    except (FileNotFoundError, RetentionError):
        result.update({"status": "source_missing_or_unsafe", "protected_current": True})
        return result
    if identity.st_size <= 0:
        result.update({"status": "source_empty", "protected_current": True})
        return result
    chosen: tuple[bytes, dict[str, Any]] | None = None
    chosen_ts = -1
    for raw, row in _iter_current_jsonl(source):
        retained = _record_from_row(row, "snapshots")
        snapshot = retained[1] if retained and retained[0] == "EvaluationSnapshot" else None
        if snapshot is None or not _is_live_nonfuture_snapshot(snapshot, now_ms):
            continue
        decision = snapshot["decision"]
        ts_ms = int(decision["ts_ms"])
        if ts_ms >= chosen_ts:
            chosen = (raw, snapshot)
            chosen_ts = ts_ms
    if chosen is None:
        result.update({"status": "no_live_nonfuture_snapshot", "protected_current": True})
        return result
    row_hash = _hash_json(chosen[1])
    tail_hash = _hash_bytes(chosen[0])
    latest_record = {
        "schema": LATEST_SCHEMA,
        "source": "current_snapshots_jsonl",
        "source_file_name": source.name,
        "source_identity": identity.as_dict(),
        "source_tail_sha256": tail_hash,
        "row_canonical_sha256": row_hash,
        "snapshot_id": _record_id(chosen[1], f"tail_sha256:{tail_hash[:16]}"),
        "snapshot_ts_ms": chosen_ts,
        "refreshed_at_ms": now_ms,
        "zero_llm": True,
        "normalized_evaluation_snapshot": chosen[1],
    }
    result.update({
        "status": "ready",
        "snapshot_id": latest_record["snapshot_id"],
        "snapshot_ts_ms": chosen_ts,
        "source_tail_sha256": tail_hash,
        "row_canonical_sha256": row_hash,
    })
    if apply:
        if not _same_identity(source, identity):
            raise RetentionError(f"current snapshots.jsonl changed before latest write: {source}")
        _atomic_write_json(target, latest_record)
        if not _same_identity(source, identity):
            raise RetentionError(f"current snapshots.jsonl changed after latest write: {source}")
        result["bridge_written"] = True
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Astra v2.3 FMZ data retention maintenance.")
    subparsers = parser.add_subparsers(dest="command")

    def add_common(target: argparse.ArgumentParser) -> None:
        target.add_argument("--fmz-log-dir", type=Path, required=True)
        target.add_argument("--retained-dir", type=Path, required=True)
        target.add_argument("--apply", action="store_true", help="Actually write retained records and unlink expired raw gzip files.")
        target.add_argument("--now-ms", type=int, default=None, help="Testing override for current epoch milliseconds.")

    maintain_parser = subparsers.add_parser("maintain", help="Freeze expired raw gzip archives and optionally refresh latest.json.")
    add_common(maintain_parser)
    maintain_parser.add_argument("--retention-hours", type=int, default=DEFAULT_RAW_PROTECT_HOURS)
    maintain_parser.add_argument("--latest-raw-protect", type=int, default=DEFAULT_LATEST_RAW_PROTECT)
    maintain_parser.add_argument("--max-retained-index", type=int, default=DEFAULT_MAX_RETAINED_INDEX)
    maintain_parser.add_argument("--refresh-current", action="store_true")

    refresh_parser = subparsers.add_parser("refresh-current", help="Write latest.json from the live current snapshots.jsonl only.")
    add_common(refresh_parser)
    refresh_parser.add_argument("--latest-json", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "maintain"
    try:
        if command == "maintain":
            result = maintain(
                fmz_log_dir=args.fmz_log_dir,
                retained_dir=args.retained_dir,
                apply=args.apply,
                now_ms=args.now_ms,
                protect_hours=args.retention_hours,
                latest_raw_protect=args.latest_raw_protect,
                max_retained_index=args.max_retained_index,
                refresh_current=args.refresh_current,
            )
        elif command == "refresh-current":
            result = refresh_latest_current(
                fmz_log_dir=args.fmz_log_dir,
                retained_dir=args.retained_dir,
                latest_json=args.latest_json,
                apply=args.apply,
                now_ms=args.now_ms,
            )
        else:
            parser.error(f"unknown command: {command}")
            return 2
    except RetentionError as exc:
        print(json.dumps({"schema": RETENTION_SCHEMA, "ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
