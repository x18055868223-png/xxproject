"""Historical shadow ledger, bounded crash-safe index, near-term & long-term references.

All historical thresholds and cold-start windows are labeled UNVALIDATED_CANDIDATE_PARAMS.
Maintains strict separation between REPLAYABLE research samples (archived raw HTTP captures
verified on disk) and NON_REPLAYABLE monitoring-only samples.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any

try:
    import fcntl  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

from research.gfri_v11.config import DEFAULT_CONFIG, GfriConfig

MS_PER_DAY = 86_400_000
INDEX_SCHEMA = "gfri_history_index_v11"
INDEX_META_SCHEMA = "gfri_history_index_meta_v11"
COMPARABILITY_KEYS = (
    "methodology_version",
    "universe_policy",
    "primary_method",
    "depth_venue",
    "code_version",
)
DRIVER_CHANGE_THRESHOLD = 0.10
VALID_QUALITIES = ("VALID", "DEGRADED")
KNOWN_ARCHIVE_LABELS = ("SAVED", "PARTIAL", "FAILED", "NOT_CONFIGURED", "UNKNOWN")


def _line_entry_id(raw_line_bytes: bytes) -> str:
    """Compute stable 16-hex digest from stripped raw JSONL line bytes."""
    return hashlib.sha256(raw_line_bytes.rstrip(b"\r\n")).hexdigest()[:16]


def _resolve_replay_metadata(record: dict[str, Any]) -> tuple[str, bool, str]:
    """Derive (raw_archive_status_label, complete_replayable, sample_replay_class)."""
    raw_archive = record.get("raw_archive_status")
    if isinstance(raw_archive, dict):
        label_raw = str(raw_archive.get("status") or "UNKNOWN").upper()
        complete = label_raw in ("SAVED", "COMPLETE") and raw_archive.get("complete_replayable") is True
    elif "raw_archive_status_label" in record or "complete_replayable" in record:
        label_raw = str(record.get("raw_archive_status_label") or "NOT_CONFIGURED").upper()
        complete = label_raw in ("SAVED", "COMPLETE") and record.get("complete_replayable") is True
    else:
        label_raw = "NOT_CONFIGURED"
        complete = False
    replay_class = "REPLAYABLE" if complete else "NON_REPLAYABLE"
    return sys.intern(label_raw), complete, sys.intern(replay_class)


def extract_index_entry(
    record: dict[str, Any],
    *,
    entry_id: str | None = None,
) -> dict[str, Any]:
    """Project a full audit/eval record into a compact ~400B history index entry."""
    comp_raw = record.get("comparability")
    if isinstance(comp_raw, dict) and comp_raw:
        comparability = {
            k: sys.intern(val) if isinstance(val, str) else val
            for k in COMPARABILITY_KEYS
            for val in (comp_raw.get(k),)
        }
    else:
        comparability = {}

    def _compact_side(side_key: str) -> dict[str, Any]:
        sb = record.get(side_key)
        if not isinstance(sb, dict):
            return {
                "status": "UNKNOWN",
                "gfri_confirmed": None,
                "liquidity_depth_usd": None,
                "raw_hedge_flow_usd": None,
            }
        status_raw = sb.get("status", "UNKNOWN")
        status = sys.intern(status_raw) if isinstance(status_raw, str) else status_raw
        return {
            "status": status,
            "gfri_confirmed": sb.get("gfri_confirmed"),
            "liquidity_depth_usd": sb.get("liquidity_depth_usd"),
            "raw_hedge_flow_usd": sb.get("raw_hedge_flow_usd"),
        }

    freeze_val = record.get("freeze_at_ms")
    if isinstance(freeze_val, int) and not isinstance(freeze_val, bool):
        freeze_at_ms: int | None = freeze_val
    elif _is_finite_number(freeze_val):
        freeze_at_ms = int(freeze_val)
    else:
        freeze_at_ms = None

    dq_raw = record.get("data_quality")
    if isinstance(dq_raw, str):
        data_quality = sys.intern(dq_raw)
    elif dq_raw is None:
        data_quality = "UNKNOWN"
    else:
        data_quality = str(dq_raw)

    gdq_raw = record.get("global_data_quality")
    if gdq_raw is None:
        global_data_quality: str | None = None
    elif isinstance(gdq_raw, str):
        global_data_quality = sys.intern(gdq_raw)
    else:
        global_data_quality = str(gdq_raw)

    archive_label, complete_replayable, replay_class = _resolve_replay_metadata(record)
    resolved_eid = entry_id if entry_id is not None else record.get("entry_id")

    entry: dict[str, Any] = {
        "schema": INDEX_SCHEMA,
        "freeze_at_ms": freeze_at_ms,
        "data_quality": data_quality,
        "global_data_quality": global_data_quality,
        "raw_archive_status_label": archive_label,
        "complete_replayable": complete_replayable,
        "sample_replay_class": replay_class,
        "comparability": comparability,
        "put": _compact_side("put"),
        "call": _compact_side("call"),
    }
    if isinstance(resolved_eid, str) and resolved_eid:
        entry["entry_id"] = resolved_eid
    return entry


def _encode_index_line(entry: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            entry,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            process_query_limited = 0x1000
            still_active = 259
            handle = kernel32.OpenProcess(process_query_limited, False, int(pid))
            if not handle:
                return kernel32.GetLastError() == 5
            try:
                exit_code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == still_active
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


class _LoadRecordsDescriptor:
    """Allow `ShadowLedger.load_records(path)`, `ledger.load_records(path)`, and `ledger.load_records()`."""

    def __get__(self, instance: ShadowLedger | None, owner: type[ShadowLedger]):
        if instance is None:
            return owner._load_records_from_path

        def _bound_load(path: str | Path | None = None) -> list[dict[str, Any]]:
            target = instance.path if path is None else path
            return owner._load_records_from_path(target)

        return _bound_load


class _LoadHistoryWindowDescriptor:
    """Allow both `ShadowLedger.load_history_window(path, ...)` and `ledger.load_history_window(...)`."""

    def __get__(self, instance: ShadowLedger | None, owner: type[ShadowLedger]):
        if instance is None:
            def _class_load(
                path_or_index: str | Path | None = None,
                *,
                reference_ms: int | None = None,
                max_age_ms: int | None = None,
            ) -> list[dict[str, Any]]:
                if path_or_index is None:
                    return []
                return owner._load_history_window_from_path(
                    path_or_index,
                    reference_ms=reference_ms,
                    max_age_ms=max_age_ms,
                )

            return _class_load

        def _bound_load(
            path_or_index: str | Path | None = None,
            *,
            reference_ms: int | None = None,
            max_age_ms: int | None = None,
        ) -> list[dict[str, Any]]:
            if path_or_index is not None:
                return owner._load_history_window_from_path(
                    path_or_index,
                    reference_ms=reference_ms,
                    max_age_ms=max_age_ms,
                )
            if instance.path.exists() and instance.path.stat().st_size > 0:
                if instance._needs_reconcile_fast():
                    instance.reconcile_index()
            if instance.index_path.exists() and instance.index_path.stat().st_size > 0:
                target: Path = instance.index_path
            else:
                target = instance.path
            return owner._load_history_window_from_path(
                target,
                reference_ms=reference_ms,
                max_age_ms=max_age_ms,
            )

        return _bound_load


class ShadowLedger:
    """Append-only JSONL ledger with self-healing companion index, flock/stale-lock & fsync."""

    load_records = _LoadRecordsDescriptor()
    load_history_window = _LoadHistoryWindowDescriptor()

    def __init__(
        self,
        path: str | Path,
        lock_timeout_sec: float = 5.0,
        *,
        index_path: str | Path | None = None,
        stale_lock_timeout_ms: int = DEFAULT_CONFIG.stale_lock_timeout_ms,
    ) -> None:
        self.path = Path(path)
        self.index_path = (
            Path(index_path)
            if index_path is not None
            else self.path.with_name("gfri_history_index.jsonl")
        )
        self.index_meta_path = Path(f"{self.index_path}.meta.json")
        self.lock_path = Path(f"{self.path}.lock")
        self.lock_timeout_sec = lock_timeout_sec
        self.stale_lock_timeout_ms = int(stale_lock_timeout_ms)

    def _is_stale_lockfile(self) -> bool:
        try:
            stat = self.lock_path.stat()
        except FileNotFoundError:
            return False
        except OSError:
            return False

        now_ms = int(time.time() * 1000)
        if now_ms - int(stat.st_mtime * 1000) > self.stale_lock_timeout_ms:
            return True

        try:
            raw_text = self.lock_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return False
        if not raw_text:
            return False
        try:
            payload = json.loads(raw_text)
        except (json.JSONDecodeError, ValueError):
            return False
        if isinstance(payload, dict):
            created_at_ms = payload.get("created_at_ms")
            if _is_finite_number(created_at_ms) and (
                now_ms - int(created_at_ms) > self.stale_lock_timeout_ms
            ):
                return True
            pid = payload.get("pid")
            if (
                isinstance(pid, int)
                and not isinstance(pid, bool)
                and pid > 0
                and not _is_pid_alive(pid)
            ):
                return True
        return False

    def _acquire_lock(self) -> int:
        deadline = time.monotonic() + self.lock_timeout_sec
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        if fcntl is not None:
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o644)
            try:
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(f"Timed out acquiring ledger lock: {self.lock_path}")
                        time.sleep(0.01)
                try:
                    os.ftruncate(fd, 0)
                    os.lseek(fd, 0, os.SEEK_SET)
                    meta = json.dumps(
                        {"pid": os.getpid(), "created_at_ms": int(time.time() * 1000)}
                    ).encode("ascii")
                    os.write(fd, meta)
                except OSError:
                    pass
                return fd
            except Exception:
                os.close(fd)
                raise

        while True:
            try:
                fd = os.open(
                    str(self.lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o644,
                )
                meta = json.dumps(
                    {"pid": os.getpid(), "created_at_ms": int(time.time() * 1000)}
                ).encode("ascii")
                os.write(fd, meta)
                return fd
            except FileExistsError:
                if self._is_stale_lockfile():
                    try:
                        os.unlink(self.lock_path)
                        continue
                    except FileNotFoundError:
                        continue
                    except PermissionError:
                        pass
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out acquiring ledger lock: {self.lock_path}")
                time.sleep(0.01)

    def _release_lock(self, fd: int) -> None:
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
            return
        try:
            os.close(fd)
        finally:
            try:
                os.unlink(self.lock_path)
            except FileNotFoundError:
                pass

    @staticmethod
    def _append_bytes(target: Path, payload: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
        fd = os.open(str(target), flags, 0o644)
        try:
            written = 0
            while written < len(payload):
                written += os.write(fd, payload[written:])
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _atomic_write_bytes(target: Path, payload: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(f"{target}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
        fd = os.open(str(tmp), flags, 0o644)
        try:
            written = 0
            while written < len(payload):
                written += os.write(fd, payload[written:])
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, target)

    def _read_meta(self) -> dict[str, Any] | None:
        if not self.index_meta_path.exists():
            return None
        try:
            data = json.loads(self.index_meta_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict) or data.get("schema") != INDEX_META_SCHEMA:
            return None
        for key in ("ledger_offset", "index_offset", "entry_count"):
            val = data.get(key)
            if not isinstance(val, int) or isinstance(val, bool) or val < 0:
                return None
        return data

    def _write_meta(
        self,
        *,
        ledger_offset: int,
        index_offset: int,
        entry_count: int,
        last_freeze_at_ms: int | None,
        last_entry_id: str | None,
    ) -> None:
        meta = {
            "schema": INDEX_META_SCHEMA,
            "ledger_offset": int(ledger_offset),
            "index_offset": int(index_offset),
            "entry_count": int(entry_count),
            "last_freeze_at_ms": last_freeze_at_ms,
            "last_entry_id": last_entry_id,
        }
        raw = (
            json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        self._atomic_write_bytes(self.index_meta_path, raw)

    def _needs_reconcile_fast(self) -> bool:
        if not self.path.exists():
            return False
        ledger_size = self.path.stat().st_size
        if ledger_size == 0:
            return False
        if not self.index_path.exists():
            return True
        meta = self._read_meta()
        if meta is None:
            return True
        index_size = self.index_path.stat().st_size
        return ledger_size != meta["ledger_offset"] or index_size != meta["index_offset"]

    def _rebuild_index_streaming_locked(self) -> dict[str, Any]:
        """Rebuild companion index streaming from immutable main ledger without full RAM load."""
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = Path(f"{self.index_path}.rebuild.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
        count = 0
        last_freeze_ms: int | None = None
        last_eid: str | None = None
        idx_fd = os.open(str(tmp_path), flags, 0o644)
        try:
            with self.path.open("rb") as fh:
                for raw_line in fh:
                    stripped = raw_line.strip()
                    if not stripped:
                        continue
                    try:
                        parsed = json.loads(stripped.decode("utf-8", errors="replace"))
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(parsed, dict):
                        continue
                    eid = _line_entry_id(raw_line)
                    entry = (
                        dict(parsed, entry_id=eid)
                        if parsed.get("schema") == INDEX_SCHEMA
                        else extract_index_entry(parsed, entry_id=eid)
                    )
                    line_bytes = _encode_index_line(entry)
                    written = 0
                    while written < len(line_bytes):
                        written += os.write(idx_fd, line_bytes[written:])
                    count += 1
                    last_freeze_ms = entry.get("freeze_at_ms")
                    last_eid = eid
            os.fsync(idx_fd)
        finally:
            os.close(idx_fd)

        os.replace(tmp_path, self.index_path)
        ledger_size = self.path.stat().st_size if self.path.exists() else 0
        index_size = self.index_path.stat().st_size if self.index_path.exists() else 0
        self._write_meta(
            ledger_offset=ledger_size,
            index_offset=index_size,
            entry_count=count,
            last_freeze_at_ms=last_freeze_ms,
            last_entry_id=last_eid,
        )
        return {"action": "REBUILT_FROM_LEDGER", "entry_count": count}

    def _reconcile_index_locked(self) -> dict[str, Any]:
        """Detect ledger/index drift and repair missing tail or corrupt index without duplication."""
        if not self.path.exists():
            return {"action": "NO_LEDGER", "entry_count": 0}
        ledger_size = self.path.stat().st_size
        if ledger_size == 0:
            return {"action": "EMPTY_LEDGER", "entry_count": 0}

        meta = self._read_meta()
        index_exists = self.index_path.exists()
        index_size = self.index_path.stat().st_size if index_exists else 0

        if (
            meta is not None
            and index_exists
            and ledger_size == meta["ledger_offset"]
            and index_size == meta["index_offset"]
        ):
            return {"action": "IN_SYNC", "entry_count": meta["entry_count"]}

        # Try O(tail) incremental heal when prefix offsets are intact
        if (
            meta is not None
            and index_exists
            and ledger_size >= meta["ledger_offset"]
            and index_size >= meta["index_offset"]
        ):
            tail_ids: set[str] = set()
            tail_corrupt = False
            tail_count = 0
            last_freeze_ms = meta.get("last_freeze_at_ms")
            last_eid = meta.get("last_entry_id")

            try:
                with self.index_path.open("rb") as idx_fh:
                    if meta["index_offset"] > 0:
                        idx_fh.seek(meta["index_offset"] - 1)
                        if idx_fh.read(1) != b"\n":
                            tail_corrupt = True
                    if not tail_corrupt and index_size > meta["index_offset"]:
                        idx_tail_bytes = idx_fh.read()
                        if not idx_tail_bytes.endswith(b"\n"):
                            tail_corrupt = True
                        else:
                            for raw_line in idx_tail_bytes.splitlines():
                                s = raw_line.strip()
                                if not s:
                                    continue
                                parsed_idx = json.loads(s.decode("utf-8"))
                                if not isinstance(parsed_idx, dict) or parsed_idx.get("schema") != INDEX_SCHEMA:
                                    tail_corrupt = True
                                    break
                                eid_i = parsed_idx.get("entry_id")
                                if not isinstance(eid_i, str) or not eid_i:
                                    tail_corrupt = True
                                    break
                                tail_ids.add(eid_i)
                                tail_count += 1
                                last_freeze_ms = parsed_idx.get("freeze_at_ms")
                                last_eid = eid_i
            except Exception:
                tail_corrupt = True

            if not tail_corrupt:
                appended = 0
                to_append = bytearray()
                with self.path.open("rb") as leg_fh:
                    leg_fh.seek(meta["ledger_offset"])
                    for raw_line in leg_fh:
                        stripped = raw_line.strip()
                        if not stripped:
                            continue
                        try:
                            parsed = json.loads(stripped.decode("utf-8", errors="replace"))
                        except (json.JSONDecodeError, ValueError):
                            continue
                        if not isinstance(parsed, dict):
                            continue
                        eid = _line_entry_id(raw_line)
                        if eid in tail_ids:
                            continue
                        entry = extract_index_entry(parsed, entry_id=eid)
                        to_append.extend(_encode_index_line(entry))
                        tail_ids.add(eid)
                        appended += 1
                        last_freeze_ms = entry.get("freeze_at_ms")
                        last_eid = eid

                if to_append:
                    self._append_bytes(self.index_path, bytes(to_append))
                new_index_size = self.index_path.stat().st_size
                total_count = int(meta["entry_count"]) + tail_count + appended
                self._write_meta(
                    ledger_offset=ledger_size,
                    index_offset=new_index_size,
                    entry_count=total_count,
                    last_freeze_at_ms=last_freeze_ms,
                    last_entry_id=last_eid,
                )
                return {
                    "action": "TAIL_HEALED",
                    "appended": appended,
                    "entry_count": total_count,
                }

        return self._rebuild_index_streaming_locked()

    def reconcile_index(self) -> dict[str, Any]:
        """Public locked entry point to verify and repair companion index consistency."""
        lock_fd = self._acquire_lock()
        try:
            return self._reconcile_index_locked()
        finally:
            self._release_lock(lock_fd)

    def verify_consistency(self) -> dict[str, Any]:
        """Audit full-ledger vs companion-index row count and tail alignment cleanly."""
        ledger_count = 0
        ledger_last_ts: int | None = None
        if self.path.exists():
            with self.path.open("rb") as fh:
                for raw_line in fh:
                    s = raw_line.strip()
                    if not s:
                        continue
                    try:
                        obj = json.loads(s.decode("utf-8", errors="replace"))
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if isinstance(obj, dict):
                        ledger_count += 1
                        ts = obj.get("freeze_at_ms")
                        if isinstance(ts, int) and not isinstance(ts, bool):
                            ledger_last_ts = ts

        index_count = 0
        index_last_ts: int | None = None
        index_malformed = 0
        if self.index_path.exists():
            with self.index_path.open("rb") as fh:
                for raw_line in fh:
                    s = raw_line.strip()
                    if not s:
                        continue
                    try:
                        obj = json.loads(s.decode("utf-8"))
                    except (json.JSONDecodeError, ValueError):
                        index_malformed += 1
                        continue
                    if isinstance(obj, dict):
                        index_count += 1
                        ts = obj.get("freeze_at_ms")
                        if isinstance(ts, int) and not isinstance(ts, bool):
                            index_last_ts = ts

        meta = self._read_meta()
        ledger_size = self.path.stat().st_size if self.path.exists() else 0
        index_size = self.index_path.stat().st_size if self.index_path.exists() else 0
        meta_synced = (
            meta is not None
            and meta["ledger_offset"] == ledger_size
            and meta["index_offset"] == index_size
            and meta["entry_count"] == index_count
        )
        consistent = (
            index_malformed == 0
            and ledger_count == index_count
            and ledger_last_ts == index_last_ts
            and (ledger_count == 0 or meta_synced)
        )
        return {
            "consistent": consistent,
            "ledger_count": ledger_count,
            "index_count": index_count,
            "index_malformed": index_malformed,
            "ledger_last_freeze_at_ms": ledger_last_ts,
            "index_last_freeze_at_ms": index_last_ts,
            "meta_synced": meta_synced,
        }

    def append_record(self, record: dict[str, Any]) -> None:
        """Strictly append a single JSONL record and its compact companion index entry."""
        if not isinstance(record, dict):
            raise TypeError("ShadowLedger record must be a dict")
        payload = (
            json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")
        eid = _line_entry_id(payload)
        index_entry = extract_index_entry(record, entry_id=eid)
        index_payload = _encode_index_line(index_entry)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = self._acquire_lock()
        try:
            heal_info = self._reconcile_index_locked()
            meta_before = self._read_meta()
            base_count = int(meta_before["entry_count"]) if meta_before else int(heal_info.get("entry_count", 0))
            self._append_bytes(self.path, payload)
            self._append_bytes(self.index_path, index_payload)
            self._write_meta(
                ledger_offset=self.path.stat().st_size,
                index_offset=self.index_path.stat().st_size,
                entry_count=base_count + 1,
                last_freeze_at_ms=index_entry.get("freeze_at_ms"),
                last_entry_id=eid,
            )
        finally:
            self._release_lock(lock_fd)

    @staticmethod
    def _load_records_from_path(path: str | Path) -> list[dict[str, Any]]:
        """Load valid JSON object lines in insertion order, skipping malformed lines."""
        target = Path(path)
        if not target.exists():
            return []
        records: list[dict[str, Any]] = []
        with target.open("r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(parsed, dict):
                    records.append(parsed)
        return records

    @staticmethod
    def _load_history_window_from_path(
        path: str | Path,
        *,
        reference_ms: int | None = None,
        max_age_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """Stream JSONL line-by-line into bounded index entries, filtering old records early."""
        target = Path(path)
        if not target.exists():
            return []
        cutoff_ms: int | None = (
            reference_ms - max_age_ms
            if reference_ms is not None and max_age_ms is not None
            else None
        )
        entries: list[dict[str, Any]] = []
        with target.open("r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(parsed, dict):
                    continue
                ts = parsed.get("freeze_at_ms")
                if cutoff_ms is not None:
                    if not isinstance(ts, int) or isinstance(ts, bool) or ts < cutoff_ms:
                        continue
                entry = (
                    parsed
                    if parsed.get("schema") == INDEX_SCHEMA
                    else extract_index_entry(parsed)
                )
                entries.append(entry)
        return entries


def select_snapshot_asof_episode(
    records: list[dict[str, Any]],
    episode_freeze_ms: int,
    *,
    require_replayable_archive: bool = False,
) -> dict[str, Any] | None:
    """Return latest snapshot strictly with freeze_at_ms <= episode_freeze_ms."""
    best: dict[str, Any] | None = None
    best_ts: int | None = None
    for rec in records:
        if not isinstance(rec, dict):
            continue
        ts = rec.get("freeze_at_ms")
        if not isinstance(ts, int) or isinstance(ts, bool) or ts > episode_freeze_ms:
            continue
        if require_replayable_archive:
            _, is_replayable, _ = _resolve_replay_metadata(rec)
            if not is_replayable:
                continue
        if best_ts is None or ts >= best_ts:
            best = rec
            best_ts = ts
    return best


def _is_finite_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(float(val))


def _comparability_signature(record: dict[str, Any]) -> tuple[tuple[str, Any], ...] | None:
    comp = record.get("comparability")
    if not isinstance(comp, dict) or not comp:
        return None
    return tuple((k, comp.get(k)) for k in COMPARABILITY_KEYS)


def _extract_side_block(record: dict[str, Any], side: str) -> dict[str, Any] | None:
    side_block = record.get(side)
    if isinstance(side_block, dict):
        return side_block
    return None


def _is_usable_side_record(record: dict[str, Any], side: str) -> tuple[bool, float | None]:
    global_dq = record.get("global_data_quality")
    eff_quality = global_dq if global_dq is not None else record.get("data_quality")
    if eff_quality not in VALID_QUALITIES:
        return False, None
    side_block = _extract_side_block(record, side)
    if side_block is None:
        return False, None
    if side_block.get("status") not in VALID_QUALITIES:
        return False, None
    gfri_val = side_block.get("gfri_confirmed")
    if not _is_finite_number(gfri_val):
        return False, None
    return True, float(gfri_val)


def _collect_comparable_history(
    history_records: list[dict[str, Any]],
    current_cycle: dict[str, Any],
    side: str,
    window_start_ms: int,
    current_ts_ms: int,
    *,
    require_replayable_only: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    current_sig = _comparability_signature(current_cycle)
    empty_stats: dict[str, Any] = {
        "replayable_count": 0,
        "non_replayable_count": 0,
        "non_replayable_excluded_count": 0,
        "by_archive_status": {lbl: 0 for lbl in KNOWN_ARCHIVE_LABELS},
    }
    if current_sig is None:
        return [], empty_stats

    matched: list[dict[str, Any]] = []
    by_status = {lbl: 0 for lbl in KNOWN_ARCHIVE_LABELS}
    replayable_count = 0
    non_replayable_count = 0
    non_replayable_excluded = 0

    for rec in history_records:
        if not isinstance(rec, dict):
            continue
        ts = rec.get("freeze_at_ms")
        if not isinstance(ts, int) or isinstance(ts, bool):
            continue
        if not (window_start_ms <= ts < current_ts_ms):
            continue
        if _comparability_signature(rec) != current_sig:
            continue
        usable, gfri_val = _is_usable_side_record(rec, side)
        if not usable or gfri_val is None:
            continue

        archive_label, complete_replayable, replay_class = _resolve_replay_metadata(rec)
        by_status[archive_label] = by_status.get(archive_label, 0) + 1
        if complete_replayable:
            replayable_count += 1
        else:
            non_replayable_count += 1
            if require_replayable_only:
                non_replayable_excluded += 1
                continue

        side_block = _extract_side_block(rec, side) or {}
        depth = side_block.get("liquidity_depth_usd")
        flow = side_block.get("raw_hedge_flow_usd")
        matched.append(
            {
                "freeze_at_ms": ts,
                "gfri_confirmed": gfri_val,
                "liquidity_depth_usd": float(depth) if _is_finite_number(depth) else None,
                "raw_hedge_flow_usd": float(flow) if _is_finite_number(flow) else None,
                "sample_replay_class": replay_class,
                "raw_archive_status_label": archive_label,
                "complete_replayable": complete_replayable,
            }
        )

    matched.sort(key=lambda item: item["freeze_at_ms"])
    stats = {
        "replayable_count": replayable_count,
        "non_replayable_count": (0 if require_replayable_only else non_replayable_count),
        "non_replayable_excluded_count": non_replayable_excluded,
        "by_archive_status": by_status,
    }
    return matched, stats


def _count_consecutive_from_end(values: list[float], threshold: float) -> int:
    count = 0
    for val in reversed(values):
        if val >= threshold:
            count += 1
        else:
            break
    return count


def _decompose_driver(
    hist_samples: list[dict[str, Any]],
    current_side: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(current_side, dict):
        return {
            "depth_change_pct": None,
            "abs_flow_change_pct": None,
            "driver": "STABLE",
            "coincided_with_liquidity_drop": False,
            "baseline_depth_usd": None,
            "baseline_abs_flow_usd": None,
        }

    curr_depth_raw = current_side.get("liquidity_depth_usd")
    curr_flow_raw = current_side.get("raw_hedge_flow_usd")
    curr_depth = float(curr_depth_raw) if _is_finite_number(curr_depth_raw) else None
    curr_abs_flow = abs(float(curr_flow_raw)) if _is_finite_number(curr_flow_raw) else None

    hist_depths = [
        s["liquidity_depth_usd"]
        for s in hist_samples
        if s["liquidity_depth_usd"] is not None and s["liquidity_depth_usd"] > 0
    ]
    hist_abs_flows = [
        abs(s["raw_hedge_flow_usd"])
        for s in hist_samples
        if s["raw_hedge_flow_usd"] is not None
    ]

    baseline_depth = statistics.median(hist_depths) if hist_depths else None
    baseline_abs_flow = statistics.median(hist_abs_flows) if hist_abs_flows else None

    depth_change_pct: float | None = None
    if curr_depth is not None and baseline_depth is not None and baseline_depth > 0:
        depth_change_pct = (curr_depth - baseline_depth) / baseline_depth

    abs_flow_change_pct: float | None = None
    if curr_abs_flow is not None and baseline_abs_flow is not None:
        if baseline_abs_flow > 0:
            abs_flow_change_pct = (curr_abs_flow - baseline_abs_flow) / baseline_abs_flow
        elif curr_abs_flow == 0:
            abs_flow_change_pct = 0.0

    liquidity_dropped = (
        depth_change_pct is not None and depth_change_pct <= -DRIVER_CHANGE_THRESHOLD
    )
    flow_rose = (
        (abs_flow_change_pct is not None and abs_flow_change_pct >= DRIVER_CHANGE_THRESHOLD)
        or (
            baseline_abs_flow == 0
            and curr_abs_flow is not None
            and curr_abs_flow > 0
        )
    )

    if liquidity_dropped and flow_rose:
        driver = "BOTH"
    elif liquidity_dropped:
        driver = "LIQUIDITY_DROP"
    elif flow_rose:
        driver = "HEDGE_FLOW_RISE"
    else:
        driver = "STABLE"

    return {
        "depth_change_pct": depth_change_pct,
        "abs_flow_change_pct": abs_flow_change_pct,
        "driver": driver,
        "coincided_with_liquidity_drop": liquidity_dropped,
        "baseline_depth_usd": baseline_depth,
        "baseline_abs_flow_usd": baseline_abs_flow,
    }


def _compute_side_near_ref(
    history_records: list[dict[str, Any]],
    current_cycle: dict[str, Any],
    side: str,
    current_ts_ms: int,
    cfg: GfriConfig,
    *,
    require_replayable_only: bool = False,
) -> dict[str, Any]:
    window_start_ms = current_ts_ms - cfg.near_window_ms
    hist_samples, quality_stats = _collect_comparable_history(
        history_records,
        current_cycle,
        side,
        window_start_ms,
        current_ts_ms,
        require_replayable_only=require_replayable_only,
    )
    sample_count = len(hist_samples)
    current_usable, current_val = _is_usable_side_record(current_cycle, side)
    current_side = _extract_side_block(current_cycle, side)

    hist_values = [s["gfri_confirmed"] for s in hist_samples]
    if current_usable and current_val is not None:
        series_with_current = hist_values + [current_val]
        persistence_elevated = _count_consecutive_from_end(
            series_with_current, cfg.elevated_abs_gfri
        )
        persistence_trigger = _count_consecutive_from_end(
            series_with_current, cfg.trigger_abs_gfri
        )
    else:
        persistence_elevated = 0
        persistence_trigger = 0

    quality_meta = {
        "require_replayable_only": require_replayable_only,
        "replayable_sample_count": quality_stats["replayable_count"],
        "non_replayable_sample_count": quality_stats["non_replayable_count"],
        "contains_non_replayable_samples": quality_stats["non_replayable_count"] > 0,
        "sample_quality_breakdown": quality_stats,
    }

    if sample_count < cfg.min_near_samples:
        return {
            "side": side.upper(),
            "state": "WARMING_UP",
            "insufficient_history": True,
            "sample_count": sample_count,
            "min_near_samples": cfg.min_near_samples,
            "current_gfri": current_val,
            "recent_median": None,
            "delta_abs": None,
            "delta_ratio": None,
            "persistence_count_elevated": persistence_elevated,
            "persistence_count_trigger": persistence_trigger,
            "depth_change_pct": None,
            "abs_flow_change_pct": None,
            "driver": "STABLE",
            "coincided_with_liquidity_drop": False,
            "baseline_depth_usd": None,
            "baseline_abs_flow_usd": None,
            **quality_meta,
        }

    recent_median = float(statistics.median(hist_values))
    if current_usable and current_val is not None:
        delta_abs = current_val - recent_median
        delta_ratio = (current_val / recent_median) if recent_median > 0 else None
        driver_info = _decompose_driver(hist_samples, current_side)
    else:
        delta_abs = None
        delta_ratio = None
        driver_info = {
            "depth_change_pct": None,
            "abs_flow_change_pct": None,
            "driver": "STABLE",
            "coincided_with_liquidity_drop": False,
            "baseline_depth_usd": None,
            "baseline_abs_flow_usd": None,
        }

    return {
        "side": side.upper(),
        "state": "READY",
        "insufficient_history": False,
        "sample_count": sample_count,
        "min_near_samples": cfg.min_near_samples,
        "current_gfri": current_val,
        "recent_median": recent_median,
        "delta_abs": delta_abs,
        "delta_ratio": delta_ratio,
        "persistence_count_elevated": persistence_elevated,
        "persistence_count_trigger": persistence_trigger,
        **driver_info,
        **quality_meta,
    }


def compute_near_term_ref(
    history_records: list[dict[str, Any]],
    current_cycle: dict[str, Any],
    *,
    cfg: GfriConfig = DEFAULT_CONFIG,
    require_replayable_only: bool = False,
) -> dict[str, Any]:
    """Compute per-side near-term (default 4h) reference and driver decomposition."""
    current_ts_ms = int(current_cycle["freeze_at_ms"])
    return {
        "schema_version": "gfri_near_ref_v11",
        "param_calibration_status": "UNVALIDATED_CANDIDATE_PARAMS",
        "freeze_at_ms": current_ts_ms,
        "window_ms": cfg.near_window_ms,
        "min_near_samples": cfg.min_near_samples,
        "require_replayable_only": require_replayable_only,
        "put": _compute_side_near_ref(
            history_records,
            current_cycle,
            "put",
            current_ts_ms,
            cfg,
            require_replayable_only=require_replayable_only,
        ),
        "call": _compute_side_near_ref(
            history_records,
            current_cycle,
            "call",
            current_ts_ms,
            cfg,
            require_replayable_only=require_replayable_only,
        ),
    }


def _classify_coarse_band(value: float, thresholds: tuple[float, float, float]) -> str:
    low_max, normal_max, elevated_max = thresholds
    if value < low_max:
        return "LOW"
    if value < normal_max:
        return "NORMAL"
    if value < elevated_max:
        return "ELEVATED"
    return "HIGH"


def _weighted_median(pairs: list[tuple[float, float]]) -> float | None:
    if not pairs:
        return None
    sorted_pairs = sorted(pairs, key=lambda item: item[0])
    total_w = sum(w for _, w in sorted_pairs)
    if total_w <= 0:
        return None
    half_w = 0.5 * total_w
    cum_w = 0.0
    for idx, (val, w) in enumerate(sorted_pairs):
        cum_w += w
        if cum_w > half_w:
            return val
        if math.isclose(cum_w, half_w, rel_tol=1e-12, abs_tol=1e-15):
            if idx + 1 < len(sorted_pairs):
                return 0.5 * (val + sorted_pairs[idx + 1][0])
            return val
    return sorted_pairs[-1][0]


def _weighted_percentile(pairs: list[tuple[float, float]], target: float) -> float | None:
    if not pairs:
        return None
    total_w = sum(w for _, w in pairs)
    if total_w <= 0:
        return None
    w_below = sum(w for val, w in pairs if val < target)
    w_equal = sum(w for val, w in pairs if val == target)
    pct = 100.0 * (w_below + 0.5 * w_equal) / total_w
    return max(0.0, min(100.0, pct))


def _compute_side_long_ref(
    history_records: list[dict[str, Any]],
    current_cycle: dict[str, Any],
    side: str,
    current_ts_ms: int,
    cfg: GfriConfig,
    *,
    require_replayable_only: bool = False,
) -> dict[str, Any]:
    window_start_ms = current_ts_ms - cfg.long_window_days * MS_PER_DAY
    hist_samples, quality_stats = _collect_comparable_history(
        history_records,
        current_cycle,
        side,
        window_start_ms,
        current_ts_ms,
        require_replayable_only=require_replayable_only,
    )
    sample_count = len(hist_samples)
    current_usable, current_val = _is_usable_side_record(current_cycle, side)

    day_keys: list[str] = []
    day_counts: dict[str, int] = {}
    for sample in hist_samples:
        day_key = datetime.fromtimestamp(
            sample["freeze_at_ms"] / 1000.0, tz=timezone.utc
        ).strftime("%Y-%m-%d")
        day_keys.append(day_key)
        day_counts[day_key] = day_counts.get(day_key, 0) + 1

    distinct_days = len(day_counts)
    weighted_pairs: list[tuple[float, float]] = []
    sum_w = 0.0
    sum_w_sq = 0.0

    for sample, day_key in zip(hist_samples, day_keys):
        age_days = (current_ts_ms - sample["freeze_at_ms"]) / float(MS_PER_DAY)
        decay_w = 0.5 ** (age_days / cfg.long_half_life_days)
        w = decay_w / day_counts[day_key]
        weighted_pairs.append((sample["gfri_confirmed"], w))
        sum_w += w
        sum_w_sq += w * w

    n_eff = (sum_w * sum_w / sum_w_sq) if sum_w_sq > 0 else 0.0
    weighted_median = _weighted_median(weighted_pairs)

    regime_drift_warning = (
        weighted_median is not None and weighted_median >= cfg.regime_drift_high_floor
    )
    regime_drift_note = (
        "REGIME_DRIFT_WARNING: low relative percentile in a persistently stressed regime "
        "must NEVER mask elevated absolute pressure."
        if regime_drift_warning
        else None
    )

    coarse_band = (
        _classify_coarse_band(current_val, cfg.band_thresholds)
        if current_usable and current_val is not None
        else None
    )

    non_rep_note = (
        "NON_REPLAYABLE_INCLUDED_MONITORING_ONLY"
        if quality_stats["non_replayable_count"] > 0
        else None
    )
    quality_meta = {
        "require_replayable_only": require_replayable_only,
        "replayable_sample_count": quality_stats["replayable_count"],
        "non_replayable_sample_count": quality_stats["non_replayable_count"],
        "contains_non_replayable_samples": quality_stats["non_replayable_count"] > 0,
        "non_replayable_disclosure": non_rep_note,
        "sample_quality_breakdown": quality_stats,
    }

    if distinct_days < cfg.min_days_for_band:
        return {
            "side": side.upper(),
            "state": "ACCUMULATING",
            "distinct_days": distinct_days,
            "sample_count": sample_count,
            "n_eff": n_eff,
            "percentile": None,
            "coarse_band": None,
            "weighted_median": weighted_median,
            "regime_drift_warning": regime_drift_warning,
            "regime_drift_note": regime_drift_note,
            "display_label": f"积累中 ({distinct_days}/{cfg.min_days_for_percentile}天)",
            **quality_meta,
        }

    if (
        distinct_days < cfg.min_days_for_percentile
        or n_eff < cfg.min_effective_samples_for_percentile
    ):
        band_text = coarse_band if coarse_band is not None else "N/A"
        return {
            "side": side.upper(),
            "state": "COARSE_BAND_ONLY",
            "distinct_days": distinct_days,
            "sample_count": sample_count,
            "n_eff": n_eff,
            "percentile": None,
            "coarse_band": coarse_band,
            "weighted_median": weighted_median,
            "regime_drift_warning": regime_drift_warning,
            "regime_drift_note": regime_drift_note,
            "display_label": f"粗粒度:{band_text} ({distinct_days}/{cfg.min_days_for_percentile}天)",
            **quality_meta,
        }

    percentile = (
        _weighted_percentile(weighted_pairs, current_val)
        if current_usable and current_val is not None
        else None
    )
    display_label = f"P{percentile:.0f}" if percentile is not None else "P--"
    return {
        "side": side.upper(),
        "state": "READY",
        "distinct_days": distinct_days,
        "sample_count": sample_count,
        "n_eff": n_eff,
        "percentile": percentile,
        "coarse_band": coarse_band,
        "weighted_median": weighted_median,
        "regime_drift_warning": regime_drift_warning,
        "regime_drift_note": regime_drift_note,
        "display_label": display_label,
        **quality_meta,
    }


def compute_long_term_ref(
    history_records: list[dict[str, Any]],
    current_cycle: dict[str, Any],
    *,
    cfg: GfriConfig = DEFAULT_CONFIG,
    require_replayable_only: bool = False,
) -> dict[str, Any]:
    """Compute per-side 60d exponentially decayed, intraday-normalized reference."""
    current_ts_ms = int(current_cycle["freeze_at_ms"])
    return {
        "schema_version": "gfri_long_ref_v11",
        "param_calibration_status": "UNVALIDATED_CANDIDATE_PARAMS",
        "freeze_at_ms": current_ts_ms,
        "window_days": cfg.long_window_days,
        "half_life_days": cfg.long_half_life_days,
        "require_replayable_only": require_replayable_only,
        "put": _compute_side_long_ref(
            history_records,
            current_cycle,
            "put",
            current_ts_ms,
            cfg,
            require_replayable_only=require_replayable_only,
        ),
        "call": _compute_side_long_ref(
            history_records,
            current_cycle,
            "call",
            current_ts_ms,
            cfg,
            require_replayable_only=require_replayable_only,
        ),
    }
