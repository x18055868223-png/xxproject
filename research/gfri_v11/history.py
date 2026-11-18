"""Historical shadow ledger, near-term reference, and long-term reference for GFRI v1.1.

All historical thresholds and cold-start windows are labeled UNVALIDATED_CANDIDATE_PARAMS.
"""

from __future__ import annotations

from datetime import datetime, timezone
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
COMPARABILITY_KEYS = (
    "methodology_version",
    "universe_policy",
    "primary_method",
    "depth_venue",
    "code_version",
)
DRIVER_CHANGE_THRESHOLD = 0.10
VALID_QUALITIES = ("VALID", "DEGRADED")


def extract_index_entry(record: dict[str, Any]) -> dict[str, Any]:
    """Project a full audit/eval record into the compact ~350B history index entry."""
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

    return {
        "schema": INDEX_SCHEMA,
        "freeze_at_ms": freeze_at_ms,
        "data_quality": data_quality,
        "global_data_quality": global_data_quality,
        "comparability": comparability,
        "put": _compact_side("put"),
        "call": _compact_side("call"),
    }


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
                target: str | Path = path_or_index
            elif instance.index_path.exists() and instance.index_path.stat().st_size > 0:
                target = instance.index_path
            else:
                target = instance.path
            return owner._load_history_window_from_path(
                target,
                reference_ms=reference_ms,
                max_age_ms=max_age_ms,
            )

        return _bound_load


class ShadowLedger:
    """Pure append-only JSONL ledger reader/writer with companion index, flock/stale-lock & fsync."""

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
        mtime_age_ms = now_ms - int(stat.st_mtime * 1000)
        if mtime_age_ms > self.stale_lock_timeout_ms:
            return True

        try:
            raw_text = self.lock_path.read_text(encoding="utf-8", errors="replace").strip()
        except FileNotFoundError:
            return False
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

    def _backfill_index_if_needed(self) -> None:
        if self.index_path.exists() or not self.path.exists() or self.path.stat().st_size == 0:
            return
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
        idx_fd = os.open(str(self.index_path), flags, 0o644)
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as fh:
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
                    entry = (
                        parsed
                        if parsed.get("schema") == INDEX_SCHEMA
                        else extract_index_entry(parsed)
                    )
                    line_bytes = (
                        json.dumps(
                            entry,
                            ensure_ascii=False,
                            sort_keys=True,
                            allow_nan=False,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode("utf-8")
                    written = 0
                    while written < len(line_bytes):
                        written += os.write(idx_fd, line_bytes[written:])
            os.fsync(idx_fd)
        finally:
            os.close(idx_fd)

    def append_record(self, record: dict[str, Any]) -> None:
        """Strictly append a single JSONL record and its compact companion index entry."""
        if not isinstance(record, dict):
            raise TypeError("ShadowLedger record must be a dict")
        payload = (
            json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")
        index_entry = extract_index_entry(record)
        index_payload = (
            json.dumps(
                index_entry,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = self._acquire_lock()
        try:
            self._backfill_index_if_needed()
            self._append_bytes(self.path, payload)
            self._append_bytes(self.index_path, index_payload)
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
            archive = rec.get("raw_archive_status")
            if not isinstance(archive, dict) or archive.get("complete_replayable") is not True:
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
) -> list[dict[str, Any]]:
    current_sig = _comparability_signature(current_cycle)
    if current_sig is None:
        return []

    matched: list[dict[str, Any]] = []
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
        side_block = _extract_side_block(rec, side) or {}
        depth = side_block.get("liquidity_depth_usd")
        flow = side_block.get("raw_hedge_flow_usd")
        matched.append(
            {
                "freeze_at_ms": ts,
                "gfri_confirmed": gfri_val,
                "liquidity_depth_usd": float(depth) if _is_finite_number(depth) else None,
                "raw_hedge_flow_usd": float(flow) if _is_finite_number(flow) else None,
            }
        )

    matched.sort(key=lambda item: item["freeze_at_ms"])
    return matched


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
) -> dict[str, Any]:
    window_start_ms = current_ts_ms - cfg.near_window_ms
    hist_samples = _collect_comparable_history(
        history_records,
        current_cycle,
        side,
        window_start_ms,
        current_ts_ms,
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
    }


def compute_near_term_ref(
    history_records: list[dict[str, Any]],
    current_cycle: dict[str, Any],
    *,
    cfg: GfriConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Compute per-side near-term (default 4h) reference and driver decomposition."""
    current_ts_ms = int(current_cycle["freeze_at_ms"])
    return {
        "schema_version": "gfri_near_ref_v11",
        "param_calibration_status": "UNVALIDATED_CANDIDATE_PARAMS",
        "freeze_at_ms": current_ts_ms,
        "window_ms": cfg.near_window_ms,
        "min_near_samples": cfg.min_near_samples,
        "put": _compute_side_near_ref(history_records, current_cycle, "put", current_ts_ms, cfg),
        "call": _compute_side_near_ref(history_records, current_cycle, "call", current_ts_ms, cfg),
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
) -> dict[str, Any]:
    window_start_ms = current_ts_ms - cfg.long_window_days * MS_PER_DAY
    hist_samples = _collect_comparable_history(
        history_records,
        current_cycle,
        side,
        window_start_ms,
        current_ts_ms,
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
    }


def compute_long_term_ref(
    history_records: list[dict[str, Any]],
    current_cycle: dict[str, Any],
    *,
    cfg: GfriConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Compute per-side 60d exponentially decayed, intraday-normalized reference."""
    current_ts_ms = int(current_cycle["freeze_at_ms"])
    return {
        "schema_version": "gfri_long_ref_v11",
        "param_calibration_status": "UNVALIDATED_CANDIDATE_PARAMS",
        "freeze_at_ms": current_ts_ms,
        "window_days": cfg.long_window_days,
        "half_life_days": cfg.long_half_life_days,
        "put": _compute_side_long_ref(history_records, current_cycle, "put", current_ts_ms, cfg),
        "call": _compute_side_long_ref(history_records, current_cycle, "call", current_ts_ms, cfg),
    }
