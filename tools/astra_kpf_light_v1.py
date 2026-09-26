from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = REPO_ROOT / "deploy" / "kpf_light" / "vendor"
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

from kpf.basins import BasinExtractor
from kpf.config import config_from_dict
from kpf.density import DensityBuilder
from kpf.evidence import EvidenceAssigner
from kpf.models import FACTOR_VERSION, DensityPoint, FilePlan, FileStatus, KpfConfig, ParseAudit, TradeRecord, VolumeBar
from kpf.parser import AggTradesParser
from kpf.targets import TargetRanker
from kpf.volume_bars import VolumeBarBuilder


SCHEMA_MANIFEST = "astra_kpf_bundle_manifest@1"
SCHEMA_SNAPSHOT = "astra_kpf_snapshot@1"
SCHEMA_AUDIT = "astra_kpf_audit@1"
SCHEMA_ZONES = "astra_kpf_raw_zones@1"
PRODUCT_ID = "btc.kpf.map.readonly.v1"
MARKET = "BINANCE_USD_M_FUTURES"
SYMBOL = "BTCUSDT"
QUOTE = "USDT"
PRICE_BASIS = "BINANCE_USDM_BTCUSDT_AGGTRADES"
DEFAULT_MIN_FREE_BYTES = 6 * 1024**3
DEFAULT_STORAGE_BUDGET_BYTES = 5 * 1024**3
DEFAULT_MAX_ZONES = 128
DEFAULT_QUOTE_MAX_AGE_MS = 300_000
DEFAULT_UM_DAILY_BASE = "https://data.binance.vision/data/futures/um/daily/aggTrades"
DEFAULT_USDM_QUOTE_URL = "https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT"
SOURCE_CACHE_FILE_RE = re.compile(r"^BTCUSDT-aggTrades-(\d{4}-\d{2}-\d{2})\.zip(?:\.CHECKSUM|\.source\.json)?$")


class KpfLightError(RuntimeError):
    pass


class SingleRunLocked(KpfLightError):
    pass


class ZoneCountRejected(KpfLightError):
    pass


@dataclass(frozen=True)
class SourceDay:
    day: date
    zip_path: Path
    checksum_path: Path
    verified: bool
    sha256: str | None
    bytes: int
    source_acquired_at_ms: int | None
    first_full_checksum_verified_at_ms: int | None = None
    last_full_checksum_verified_at_ms: int | None = None
    error: str = ""
    downloaded: bool = False
    checksum_failed: bool = False
    checksum_missing: bool = False
    schema_failed: bool = False


@dataclass(frozen=True)
class ParsedWindow:
    bars: list[Any]
    latest_trade: TradeRecord | None
    parse_audits: list[ParseAudit]
    duplicate_count: int
    duplicate_conflict_count: int
    schema_failed_dates: list[str]


@dataclass(frozen=True)
class StreamingWindow:
    density_90: list[DensityPoint]
    density_60: list[DensityPoint]
    latest_trade: TradeRecord | None
    parse_audits: list[ParseAudit]
    duplicate_count: int
    duplicate_conflict_count: int
    schema_failed_dates: list[str]
    volume_bar_count: int

    @classmethod
    def empty(cls) -> "StreamingWindow":
        return cls([], [], None, [], 0, 0, [], 0)


@dataclass(frozen=True)
class CurrentQuote:
    price: Decimal
    observed_ms: int
    acquired_at_ms: int
    source_url: str
    raw: dict[str, Any]


class RunLock:
    def __init__(self, path: Path, use_flock: bool | None = None) -> None:
        self.path = path
        self.use_flock = (os.name != "nt") if use_flock is None else use_flock
        self._handle: Any = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"pid": os.getpid(), "created_at_ms": now_ms(), "lock": str(self.path)}
        if self.use_flock:
            self._handle = self.path.open("a+", encoding="utf-8")
            try:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                self._handle.close()
                self._handle = None
                raise SingleRunLocked(f"kpf light worker is already locked: {self.path}") from exc
            self._handle.seek(0)
            self._handle.truncate()
            json.dump(payload, self._handle, sort_keys=True)
            self._handle.flush()
            os.fsync(self._handle.fileno())
            return self

        self._remove_stale_pid_file()
        try:
            with self.path.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True)
        except FileExistsError as exc:
            raise SingleRunLocked(f"kpf light worker is already locked: {self.path}") from exc
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._handle is not None:
            try:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            finally:
                self._handle.close()
                self._handle = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _remove_stale_pid_file(self) -> None:
        try:
            payload = read_json(self.path)
        except FileNotFoundError:
            return
        except Exception:
            return
        pid = payload.get("pid")
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            return
        if not process_is_alive(pid_int):
            self.path.unlink(missing_ok=True)


def now_ms() -> int:
    return int(time.time() * 1000)


def ms_to_utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sampled_file_sha256(path: Path, sample_size: int = 64 * 1024) -> str | None:
    try:
        size = path.stat().st_size
        digest = hashlib.sha256()
        digest.update(str(size).encode("ascii"))
        with path.open("rb") as handle:
            if size <= sample_size * 4:
                digest.update(handle.read())
            else:
                starts = sorted({0, max(0, size // 2 - sample_size // 2), max(0, size - sample_size)})
                for start in starts:
                    handle.seek(start)
                    digest.update(str(start).encode("ascii"))
                    digest.update(handle.read(sample_size))
        return digest.hexdigest()
    except OSError:
        return None


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes

            process_query_limited_information = 0x1000
            still_active = 259
            handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                return bool(ok) and exit_code.value == still_active
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
    tmp.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_closed_day(as_of_ms: int) -> date:
    return ms_to_utc(as_of_ms).date() - timedelta(days=1)


def required_dates(end_day: date, history_days: int) -> list[date]:
    return [end_day - timedelta(days=offset) for offset in range(history_days - 1, -1, -1)]


def build_config(workspace_root: Path) -> KpfConfig:
    return config_from_dict(
        {
            "symbol": SYMBOL,
            "market": MARKET,
            "data_type": "aggTrades",
            "workspace_root": str(workspace_root),
            "data_source_base_url": "https://data.binance.vision",
            "history_days": 90,
            "fresh_confirm_days": 60,
            "internal_bin_width_usd": 50,
            "output_price_step_usd": 250,
            "display_band_half_width_usd": 125,
            "volume_bar_size_btc": 100,
            "time_weights": {"fresh_0_7d": 1.0, "mid_7_30d": 0.75, "memory_30_90d": 0.35},
            "smooth_kernel": [0.25, 0.50, 0.25],
            "max_targets_each_side": 3,
            "basin_width": {"hard_min_usd": 100, "preferred_max_usd": 500, "hard_max_usd": 750},
            "thresholds": {
                "base": {"prominence_norm": 0.25, "excess_mass_norm": 0.25, "participating_volume_bars": 8},
                "A": {"prominence_norm": 0.55, "excess_mass_norm": 0.60, "participating_volume_bars": 25},
                "B": {"prominence_norm": 0.35, "excess_mass_norm": 0.40, "participating_volume_bars": 12},
                "C": {"prominence_norm": 0.25, "excess_mass_norm": 0.25, "participating_volume_bars": 8},
            },
            "download": {"max_retries": 1, "timeout_seconds": 45, "concurrent_downloads": 1, "batch_size": 1, "cache_batch_size": 1, "remote_missing_cooldown_hours": 24},
            "report": {"include_debug_density": False, "include_debug_candidates": True, "include_raw_center_debug": True},
        }
    )


def source_root(data_root: Path) -> Path:
    return data_root / "source" / "binance_usdm" / "aggTrades" / SYMBOL


def source_zip_path(root: Path, day: date) -> Path:
    return source_root(root) / f"{SYMBOL}-aggTrades-{day.isoformat()}.zip"


def checksum_path_for(zip_path: Path) -> Path:
    return zip_path.with_name(zip_path.name + ".CHECKSUM")


def parse_checksum_file(path: Path) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    for token in text.replace("\n", " ").split():
        token = token.strip()
        if len(token) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in token):
            return token.lower()
    return None


def data_root_size_bytes(data_root: Path) -> int:
    total = 0
    if not data_root.exists():
        return 0
    for path in data_root.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return total


def source_acquired_ms(path: Path) -> int | None:
    meta_path = path.with_name(path.name + ".source.json")
    if meta_path.exists():
        try:
            meta = read_json(meta_path)
            value = meta.get("source_acquired_at_ms")
            return int(value) if value is not None else None
        except Exception:
            return None
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return None


def source_verification_times(path: Path) -> tuple[int | None, int | None]:
    meta_path = path.with_name(path.name + ".source.json")
    if not meta_path.exists():
        return None, None
    try:
        meta = read_json(meta_path)
    except Exception:
        return None, None
    first = meta.get("first_full_checksum_verified_at_ms")
    last = meta.get("last_full_checksum_verified_at_ms")
    return (int(first) if first is not None else None, int(last) if last is not None else None)


def record_full_checksum_verification(path: Path, sha256: str) -> tuple[int, int]:
    meta_path = path.with_name(path.name + ".source.json")
    try:
        meta = read_json(meta_path) if meta_path.exists() else {}
    except Exception:
        meta = {}
    stamp = now_ms()
    first = int(meta.get("first_full_checksum_verified_at_ms") or stamp)
    meta.update(
        {
            "sha256": sha256,
            "first_full_checksum_verified_at_ms": first,
            "last_full_checksum_verified_at_ms": stamp,
        }
    )
    write_json_atomic(meta_path, meta)
    return first, stamp


def path_stat_fingerprint(path: Path) -> dict[str, Any] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return {
        "name": path.name,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "ctime_ns": getattr(stat, "st_ctime_ns", None),
        "inode": getattr(stat, "st_ino", None),
        "sample_sha256": sampled_file_sha256(path),
        "is_symlink": path.is_symlink(),
    }


def source_meta_payload(path: Path) -> dict[str, Any] | None:
    meta_path = path.with_name(path.name + ".source.json")
    if not meta_path.exists():
        return None
    try:
        payload = read_json(meta_path)
    except Exception:
        return {"unreadable": True}
    return {
        "source": payload.get("source"),
        "source_acquired_at_ms": payload.get("source_acquired_at_ms"),
        "url": payload.get("url"),
        "checksum_url": payload.get("checksum_url"),
        "sha256": payload.get("sha256"),
        "first_full_checksum_verified_at_ms": payload.get("first_full_checksum_verified_at_ms"),
        "last_full_checksum_verified_at_ms": payload.get("last_full_checksum_verified_at_ms"),
    }


def source_day_fingerprint(data_root: Path, day: date, actual_sha256: str | None = None) -> dict[str, Any]:
    zip_path = source_zip_path(data_root, day)
    checksum_path = checksum_path_for(zip_path)
    expected = parse_checksum_file(checksum_path)
    return {
        "date": day.isoformat(),
        "zip": path_stat_fingerprint(zip_path),
        "checksum": path_stat_fingerprint(checksum_path),
        "source_meta": path_stat_fingerprint(zip_path.with_name(zip_path.name + ".source.json")),
        "source_meta_payload": source_meta_payload(zip_path),
        "sha256": actual_sha256 or expected,
        "checksum_expected_sha256": expected,
    }


def source_input_fingerprint_from_statuses(data_root: Path, statuses: list[SourceDay]) -> dict[str, Any]:
    return {
        "schema": "astra_kpf_source_input_fingerprint@1",
        "symbol": SYMBOL,
        "basis": PRICE_BASIS,
        "days": [source_day_fingerprint(data_root, status.day, status.sha256) for status in statuses],
    }


def source_input_fingerprint_fast(data_root: Path, days: list[date]) -> dict[str, Any]:
    return {
        "schema": "astra_kpf_source_input_fingerprint@1",
        "symbol": SYMBOL,
        "basis": PRICE_BASIS,
        "days": [source_day_fingerprint(data_root, day) for day in days],
    }


def algorithm_fingerprint() -> dict[str, Any]:
    manifest = read_json(vendor_manifest_path()) if vendor_manifest_path().exists() else {"files": []}
    files: list[dict[str, Any]] = []
    for item in manifest.get("files") or []:
        rel_path = str(item.get("path", ""))
        path = REPO_ROOT / rel_path
        files.append(
            {
                "path": rel_path,
                "manifest_sha256": item.get("sha256"),
                "actual_sha256": sha256_file(path) if path.exists() else None,
            }
        )
    ok, errors = verify_vendor_manifest(manifest) if manifest.get("files") is not None else (False, ["source_manifest_missing"])
    return {
        "schema": "astra_kpf_algorithm_fingerprint@1",
        "factor_version": FACTOR_VERSION,
        "source_manifest_sha256": sha256_file(vendor_manifest_path()) if vendor_manifest_path().exists() else None,
        "vendor_ok": ok,
        "vendor_errors": errors,
        "files": files,
    }


def input_fingerprint(data_root: Path, statuses: list[SourceDay]) -> dict[str, Any]:
    return {
        "schema": "astra_kpf_light_input_fingerprint@1",
        "algorithm": algorithm_fingerprint(),
        "source": source_input_fingerprint_from_statuses(data_root, statuses),
    }


def manifest_snapshot(output_root: Path, manifest: dict[str, Any]) -> dict[str, Any] | None:
    snapshot_ref = (manifest.get("artifacts") or {}).get("snapshot") or {}
    rel_path = snapshot_ref.get("path")
    if not rel_path:
        return None
    path = output_root / rel_path
    try:
        return read_json(path)
    except Exception:
        return None


def verified_manifest_artifacts(output_root: Path, manifest: dict[str, Any]) -> bool:
    artifacts = manifest.get("artifacts") or {}
    if set(artifacts) != {"snapshot", "audit", "zones"}:
        return False
    for ref in artifacts.values():
        rel_path = ref.get("path")
        expected = ref.get("sha256")
        if not rel_path or not expected:
            return False
        path = output_root / rel_path
        try:
            if not path.exists() or sha256_file(path) != expected:
                return False
        except OSError:
            return False
    return True


def ready_window_dates_from_current(output_root: Path, end_day: date, history_days: int) -> list[date]:
    retained: set[date] = set()
    for name in ("current.json", "previous.json"):
        manifest_path = output_root / name
        if not manifest_path.exists():
            continue
        try:
            manifest = read_json(manifest_path)
        except Exception:
            continue
        if not verified_manifest_artifacts(output_root, manifest):
            continue
        snapshot = manifest_snapshot(output_root, manifest)
        if not snapshot or snapshot.get("status") != "READY":
            continue
        previous_end_text = ((snapshot.get("clock") or {}).get("latest_closed_day"))
        if not previous_end_text:
            continue
        try:
            previous_end = date.fromisoformat(previous_end_text)
        except ValueError:
            continue
        if previous_end != end_day:
            retained.update(required_dates(previous_end, history_days))
    return sorted(retained)


def source_cache_file_date(path: Path) -> date | None:
    match = SOURCE_CACHE_FILE_RE.match(path.name)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def cleanup_source_retention(data_root: Path, output_root: Path, end_day: date, history_days: int, *, keep_previous_ready: bool) -> dict[str, Any]:
    root = source_root(data_root)
    current_required = set(required_dates(end_day, history_days))
    retained = set(current_required)
    if keep_previous_ready:
        retained.update(ready_window_dates_from_current(output_root, end_day, history_days))
    summary: dict[str, Any] = {
        "schema": "astra_kpf_source_retention@1",
        "source_root": str(root),
        "latest_closed_day": end_day.isoformat(),
        "retained_date_count": len(retained),
        "deleted": [],
        "unknown_kept": [],
        "symlink_skipped": [],
        "skipped_reason": None,
    }
    if not root.exists():
        return summary
    if root.is_symlink():
        summary["skipped_reason"] = "source_root_symlink"
        return summary
    try:
        root_resolved = root.resolve(strict=True)
    except OSError:
        summary["skipped_reason"] = "source_root_unresolvable"
        return summary
    for path in list(root.iterdir()):
        if not path.is_file():
            continue
        file_day = source_cache_file_date(path)
        if file_day is None:
            summary["unknown_kept"].append(path.name)
            continue
        if path.is_symlink():
            summary["symlink_skipped"].append(path.name)
            continue
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            summary["unknown_kept"].append(path.name)
            continue
        if resolved.parent != root_resolved:
            summary["symlink_skipped"].append(path.name)
            continue
        if file_day not in retained:
            path.unlink()
            summary["deleted"].append(path.name)
    return summary


def inspect_sources(data_root: Path, days: list[date]) -> list[SourceDay]:
    inspected: list[SourceDay] = []
    for day in days:
        zip_path = source_zip_path(data_root, day)
        checksum_path = checksum_path_for(zip_path)
        if not zip_path.exists():
            inspected.append(SourceDay(day=day, zip_path=zip_path, checksum_path=checksum_path, verified=False, sha256=None, bytes=0, source_acquired_at_ms=None, error="missing_source_zip"))
            continue
        expected = parse_checksum_file(checksum_path)
        size = zip_path.stat().st_size
        actual = sha256_file(zip_path)
        if expected is None:
            inspected.append(SourceDay(day=day, zip_path=zip_path, checksum_path=checksum_path, verified=False, sha256=actual, bytes=size, source_acquired_at_ms=source_acquired_ms(zip_path), error="checksum_missing", checksum_missing=True))
            continue
        if actual.lower() != expected.lower():
            inspected.append(SourceDay(day=day, zip_path=zip_path, checksum_path=checksum_path, verified=False, sha256=actual, bytes=size, source_acquired_at_ms=source_acquired_ms(zip_path), error="checksum_failed", checksum_failed=True))
            continue
        first_verified, last_verified = record_full_checksum_verification(zip_path, actual)
        inspected.append(SourceDay(day=day, zip_path=zip_path, checksum_path=checksum_path, verified=True, sha256=actual, bytes=size, source_acquired_at_ms=source_acquired_ms(zip_path), first_full_checksum_verified_at_ms=first_verified, last_full_checksum_verified_at_ms=last_verified))
    return inspected


def stream_http_to_part(
    url: str,
    part_path: Path,
    *,
    timeout_seconds: int,
    min_free_bytes: int,
    storage_budget_bytes: int,
    data_root: Path,
    chunk_size: int = 1024 * 1024,
) -> dict[str, Any]:
    if not url.startswith("https://"):
        raise KpfLightError(f"refusing non-HTTPS source: {url}")
    part_path.parent.mkdir(parents=True, exist_ok=True)
    part_path.unlink(missing_ok=True)
    bytes_written = 0
    base_size = data_root_size_bytes(data_root)
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            with part_path.open("xb") as handle:
                while True:
                    free_before = shutil.disk_usage(part_path.parent).free
                    if free_before < min_free_bytes:
                        raise KpfLightError(f"disk free below hard stop before chunk: {free_before}")
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    bytes_written += len(chunk)
                    if base_size + bytes_written > storage_budget_bytes:
                        raise KpfLightError("storage budget would be exceeded during streaming download")
                    handle.write(chunk)
                    digest.update(chunk)
                    free_after = shutil.disk_usage(part_path.parent).free
                    if free_after < min_free_bytes:
                        raise KpfLightError(f"disk free below hard stop after chunk: {free_after}")
    except Exception:
        part_path.unlink(missing_ok=True)
        raise
    return {"bytes": bytes_written, "sha256": digest.hexdigest(), "url": url}


def download_one_day(
    data_root: Path,
    day: date,
    timeout_seconds: int = 45,
    *,
    min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
    storage_budget_bytes: int = DEFAULT_STORAGE_BUDGET_BYTES,
) -> SourceDay:
    base = f"{DEFAULT_UM_DAILY_BASE}/{SYMBOL}"
    filename = f"{SYMBOL}-aggTrades-{day.isoformat()}.zip"
    zip_url = f"{base}/{filename}"
    checksum_url = f"{zip_url}.CHECKSUM"
    target = source_zip_path(data_root, day)
    checksum_target = checksum_path_for(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_checksum = checksum_target.with_name(checksum_target.name + ".part")
    tmp_zip = target.with_name(target.name + ".part")
    try:
        stream_http_to_part(
            checksum_url,
            tmp_checksum,
            timeout_seconds=timeout_seconds,
            min_free_bytes=min_free_bytes,
            storage_budget_bytes=storage_budget_bytes,
            data_root=data_root,
        )
        expected = parse_checksum_file(tmp_checksum)
        if expected is None:
            raise KpfLightError(f"downloaded checksum is unreadable for {day.isoformat()}")
        stream_http_to_part(
            zip_url,
            tmp_zip,
            timeout_seconds=timeout_seconds,
            min_free_bytes=min_free_bytes,
            storage_budget_bytes=storage_budget_bytes,
            data_root=data_root,
        )
        actual = sha256_file(tmp_zip)
        if actual.lower() != expected.lower():
            quarantine_bad_file(data_root, tmp_zip, f"checksum_failed_{day.isoformat()}_{actual[:12]}.zip")
            raise KpfLightError(f"downloaded checksum mismatch for {day.isoformat()}")
        tmp_zip.replace(target)
        tmp_checksum.replace(checksum_target)
        stamp = now_ms()
        write_json_atomic(
            target.with_name(target.name + ".source.json"),
            {"source": "binance_data_vision", "source_acquired_at_ms": stamp, "url": zip_url, "checksum_url": checksum_url, "sha256": actual},
        )
    finally:
        tmp_zip.unlink(missing_ok=True)
        tmp_checksum.unlink(missing_ok=True)
    return inspect_sources(data_root, [day])[0]


def maybe_download_one_missing_day(
    data_root: Path,
    days: list[date],
    allow_download: bool,
    timeout_seconds: int,
    *,
    min_free_bytes: int,
    storage_budget_bytes: int,
) -> SourceDay | None:
    if not allow_download:
        return None
    for item in inspect_sources(data_root, days):
        if not item.verified and item.error == "missing_source_zip":
            return download_one_day(
                data_root,
                item.day,
                timeout_seconds=timeout_seconds,
                min_free_bytes=min_free_bytes,
                storage_budget_bytes=storage_budget_bytes,
            )
    return None


def quarantine_bad_file(data_root: Path, path: Path, name: str) -> None:
    if not path.exists():
        return
    quarantine = data_root / "quarantine"
    quarantine.mkdir(parents=True, exist_ok=True)
    target = quarantine / name
    if target.exists():
        target = quarantine / f"{int(time.time())}_{name}"
    shutil.move(str(path), str(target))


def capacity_preflight(data_root: Path, min_free_bytes: int, storage_budget_bytes: int) -> dict[str, Any]:
    data_root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(data_root)
    used_under_root = data_root_size_bytes(data_root)
    ok = usage.free >= min_free_bytes and used_under_root <= storage_budget_bytes
    return {
        "schema": "astra_kpf_light_capacity@1",
        "ok": ok,
        "disk_free_bytes": usage.free,
        "disk_total_bytes": usage.total,
        "data_root_bytes": used_under_root,
        "min_free_bytes": min_free_bytes,
        "storage_budget_bytes": storage_budget_bytes,
        "memory_max_bytes": 240 * 1024 * 1024,
        "cpu_quota": "50%",
        "nice": 10,
    }


def to_file_status(day: SourceDay) -> FileStatus:
    return FileStatus(
        date=day.day,
        raw_path=day.zip_path,
        checksum_path=day.checksum_path,
        verified_path=day.zip_path,
        exists_raw=day.zip_path.exists(),
        exists_checksum=day.checksum_path.exists(),
        verified=day.verified,
        checksum_missing=day.checksum_missing,
        checksum_failed=day.checksum_failed,
        schema_failed=day.schema_failed,
        downloaded=day.downloaded,
        error=day.error or None,
    )


def classify_data_quality(plan: FilePlan, statuses: list[SourceDay], parsed: Any | None = None) -> dict[str, Any]:
    verified = [item.day.isoformat() for item in statuses if item.verified]
    missing = [item.day.isoformat() for item in statuses if not item.verified]
    fresh_set = {d.isoformat() for d in plan.fresh_dates}
    verified_set = set(verified)
    checksum_failed = [str(item.zip_path) for item in statuses if item.checksum_failed]
    checksum_missing = [str(item.zip_path) for item in statuses if item.checksum_missing]
    schema_failed = list(parsed.schema_failed_dates if parsed else [])
    history_coverage = len(verified) / len(plan.required_dates) if plan.required_dates else 0.0
    fresh_coverage = len(fresh_set & verified_set) / len(plan.fresh_dates) if plan.fresh_dates else 0.0
    duplicate_conflict_count = parsed.duplicate_conflict_count if parsed else 0
    max_consecutive = max_consecutive_missing(missing)
    if (
        history_coverage < 0.75
        or fresh_coverage < 0.75
        or max_consecutive > 5
        or checksum_failed
        or schema_failed
    ):
        state = "INVALID"
    elif (
        history_coverage >= 0.98
        and fresh_coverage >= 0.98
        and not (set(missing) & {d.isoformat() for d in plan.required_dates[-7:]})
        and not checksum_failed
        and not schema_failed
        and duplicate_conflict_count == 0
        and not checksum_missing
    ):
        state = "OK"
    elif (
        history_coverage >= 0.90
        and fresh_coverage >= 0.90
        and not (set(missing) & {d.isoformat() for d in plan.required_dates[-3:]})
        and not checksum_failed
        and not schema_failed
        and duplicate_conflict_count == 0
        and max_consecutive <= 2
    ):
        state = "DEGRADED_MINOR"
    elif history_coverage >= 0.75 and fresh_coverage >= 0.75:
        state = "DEGRADED_MAJOR"
    else:
        state = "INVALID"
    return {
        "data_quality_state": state,
        "required_dates": [d.isoformat() for d in plan.required_dates],
        "verified_dates": verified,
        "missing_dates": missing,
        "coverage_ratio_history": round(history_coverage, 6),
        "coverage_ratio_fresh": round(fresh_coverage, 6),
        "checksum_missing_files": checksum_missing,
        "checksum_failed_files": checksum_failed,
        "schema_failed_dates": schema_failed,
        "duplicate_count": parsed.duplicate_count if parsed else 0,
        "duplicate_conflict_count": duplicate_conflict_count,
        "max_consecutive_missing_dates": max_consecutive,
    }


def max_consecutive_missing(missing: list[str]) -> int:
    if not missing:
        return 0
    days = sorted(date.fromisoformat(item) for item in missing)
    best = current = 1
    for left, right in zip(days, days[1:]):
        if (right - left).days == 1:
            current += 1
        else:
            current = 1
        best = max(best, current)
    return best


def parse_window(config: KpfConfig, statuses: list[SourceDay]) -> ParsedWindow:
    parser = AggTradesParser()
    builder = VolumeBarBuilder(config)
    parse_audits: list[ParseAudit] = []
    latest_trade: TradeRecord | None = None
    previous_day_last_agg_id: int | None = None
    previous_day_last_ts: int | None = None
    global_duplicate_count = 0
    global_duplicate_conflict_count = 0
    schema_failed_dates: list[str] = []
    bars: list[Any] = []

    for status in statuses:
        if not status.verified:
            continue
        audit = ParseAudit(source_file=str(status.zip_path))
        day_last_ts = -1
        day_duplicate_count = 0
        day_duplicate_conflict_count = 0
        day_last_agg_id: int | None = None
        day_last_trade: TradeRecord | None = None
        accepted_any = False
        for trade in parser.iter_trades(status.zip_path, audit):
            if ms_to_utc(trade.ts_ms).date() != status.day:
                day_duplicate_conflict_count += 1
                audit.bad_rows.append({"reason": "source_day_timestamp_out_of_range", "agg_id": trade.agg_id, "ts_ms": trade.ts_ms, "source_day": status.day.isoformat()})
                audit.schema_failed = True
                break
            if not accepted_any and previous_day_last_ts is not None:
                if trade.ts_ms <= previous_day_last_ts:
                    day_duplicate_conflict_count += 1
                    audit.bad_rows.append({"reason": "cross_day_time_overlap", "agg_id": trade.agg_id, "ts_ms": trade.ts_ms})
                    audit.schema_failed = True
                    break
                if previous_day_last_agg_id is not None and trade.agg_id <= previous_day_last_agg_id:
                    day_duplicate_conflict_count += 1
                    audit.bad_rows.append({"reason": "cross_day_agg_id_overlap", "agg_id": trade.agg_id, "previous_agg_id": previous_day_last_agg_id})
                    audit.schema_failed = True
                    break
            if trade.ts_ms < day_last_ts:
                audit.schema_failed = True
                break
            day_last_ts = trade.ts_ms
            if day_last_agg_id is not None and trade.agg_id <= day_last_agg_id:
                day_duplicate_count += 1
                if trade.agg_id < day_last_agg_id or trade_conflicts(day_last_trade, trade):
                    day_duplicate_conflict_count += 1
                continue
            day_last_agg_id = trade.agg_id
            day_last_trade = trade
            accepted_any = True
            bars.extend(builder.add_trade(trade))
            if latest_trade is None or trade.ts_ms > latest_trade.ts_ms:
                latest_trade = trade
        audit.duplicate_count = day_duplicate_count  # type: ignore[attr-defined]
        audit.duplicate_conflict_count = day_duplicate_conflict_count  # type: ignore[attr-defined]
        if audit.schema_failed:
            schema_failed_dates.append(status.day.isoformat())
        parse_audits.append(audit)
        global_duplicate_count += day_duplicate_count
        global_duplicate_conflict_count += day_duplicate_conflict_count
        if accepted_any:
            previous_day_last_agg_id = day_last_agg_id
            previous_day_last_ts = day_last_ts

    return ParsedWindow(
        bars=bars,
        latest_trade=latest_trade,
        parse_audits=parse_audits,
        duplicate_count=global_duplicate_count,
        duplicate_conflict_count=global_duplicate_conflict_count,
        schema_failed_dates=schema_failed_dates,
    )


def trade_conflicts(existing: TradeRecord | None, trade: TradeRecord) -> bool:
    if existing is None:
        return True
    return existing.price != trade.price or existing.qty != trade.qty or existing.ts_ms != trade.ts_ms


class StreamingDensityAccumulator:
    def __init__(self, config: KpfConfig, as_of_time: datetime) -> None:
        self.config = config
        self.as_of_time = as_of_time
        self.builder = DensityBuilder()
        self.density_90: dict[int, float] = defaultdict(float)
        self.participation_90: dict[int, int] = defaultdict(int)
        self.density_60: dict[int, float] = defaultdict(float)
        self.participation_60: dict[int, int] = defaultdict(int)
        self.volume_bar_count = 0

    def add_bar(self, bar: VolumeBar) -> None:
        self.volume_bar_count += 1
        end_time = datetime.fromtimestamp(bar.end_ts_ms / 1000, tz=UTC)
        age_days = max(0.0, (self.as_of_time - end_time).total_seconds() / 86400)
        if age_days > self.config.history_days:
            return
        weight = self.builder.time_weight(self.config, self.as_of_time, bar)
        if weight == 0:
            return
        self._add_to(self.density_90, self.participation_90, bar, weight)
        if age_days <= self.config.fresh_confirm_days:
            self._add_to(self.density_60, self.participation_60, bar, weight)

    def build_points(self) -> tuple[list[DensityPoint], list[DensityPoint]]:
        return (
            self.builder._smooth(self.config, self.density_90, self.participation_90),
            self.builder._smooth(self.config, self.density_60, self.participation_60),
        )

    def _add_to(
        self,
        density: dict[int, float],
        participation: dict[int, int],
        bar: VolumeBar,
        weight: float,
    ) -> None:
        total = float(bar.total_qty)
        for bin_key, qty in bar.bin_qty.items():
            density[bin_key] += weight * (float(qty) / total)
            participation[bin_key] += 1


def stream_window(config: KpfConfig, statuses: list[SourceDay], as_of_time: datetime) -> StreamingWindow:
    parser = AggTradesParser()
    bar_builder = VolumeBarBuilder(config)
    accumulator = StreamingDensityAccumulator(config, as_of_time)
    parse_audits: list[ParseAudit] = []
    latest_trade: TradeRecord | None = None
    previous_day_last_agg_id: int | None = None
    previous_day_last_ts: int | None = None
    global_duplicate_count = 0
    global_duplicate_conflict_count = 0
    schema_failed_dates: list[str] = []

    for status in statuses:
        if not status.verified:
            continue
        audit = ParseAudit(source_file=str(status.zip_path))
        day_last_ts = -1
        day_duplicate_count = 0
        day_duplicate_conflict_count = 0
        day_last_agg_id: int | None = None
        day_last_trade: TradeRecord | None = None
        accepted_any = False
        for trade in parser.iter_trades(status.zip_path, audit):
            if ms_to_utc(trade.ts_ms).date() != status.day:
                day_duplicate_conflict_count += 1
                audit.bad_rows.append({"reason": "source_day_timestamp_out_of_range", "agg_id": trade.agg_id, "ts_ms": trade.ts_ms, "source_day": status.day.isoformat()})
                audit.schema_failed = True
                break
            if not accepted_any and previous_day_last_ts is not None:
                if trade.ts_ms <= previous_day_last_ts:
                    day_duplicate_conflict_count += 1
                    audit.bad_rows.append({"reason": "cross_day_time_overlap", "agg_id": trade.agg_id, "ts_ms": trade.ts_ms})
                    audit.schema_failed = True
                    break
                if previous_day_last_agg_id is not None and trade.agg_id <= previous_day_last_agg_id:
                    day_duplicate_conflict_count += 1
                    audit.bad_rows.append({"reason": "cross_day_agg_id_overlap", "agg_id": trade.agg_id, "previous_agg_id": previous_day_last_agg_id})
                    audit.schema_failed = True
                    break
            if trade.ts_ms < day_last_ts:
                audit.schema_failed = True
                break
            day_last_ts = trade.ts_ms
            if day_last_agg_id is not None and trade.agg_id <= day_last_agg_id:
                day_duplicate_count += 1
                if trade.agg_id < day_last_agg_id or trade_conflicts(day_last_trade, trade):
                    day_duplicate_conflict_count += 1
                continue
            day_last_agg_id = trade.agg_id
            day_last_trade = trade
            accepted_any = True
            for bar in bar_builder.add_trade(trade):
                accumulator.add_bar(bar)
            if latest_trade is None or trade.ts_ms > latest_trade.ts_ms:
                latest_trade = trade
        audit.duplicate_count = day_duplicate_count  # type: ignore[attr-defined]
        audit.duplicate_conflict_count = day_duplicate_conflict_count  # type: ignore[attr-defined]
        if audit.schema_failed:
            schema_failed_dates.append(status.day.isoformat())
        parse_audits.append(audit)
        global_duplicate_count += day_duplicate_count
        global_duplicate_conflict_count += day_duplicate_conflict_count
        if accepted_any:
            previous_day_last_agg_id = day_last_agg_id
            previous_day_last_ts = day_last_ts

    density_90, density_60 = accumulator.build_points()
    return StreamingWindow(
        density_90=density_90,
        density_60=density_60,
        latest_trade=latest_trade,
        parse_audits=parse_audits,
        duplicate_count=global_duplicate_count,
        duplicate_conflict_count=global_duplicate_conflict_count,
        schema_failed_dates=schema_failed_dates,
        volume_bar_count=accumulator.volume_bar_count,
    )


def fetch_current_quote(timeout_seconds: int = 10, url: str = DEFAULT_USDM_QUOTE_URL, max_age_ms: int = DEFAULT_QUOTE_MAX_AGE_MS) -> CurrentQuote | None:
    if url != DEFAULT_USDM_QUOTE_URL or not url.startswith("https://fapi.binance.com/"):
        raise KpfLightError("refusing non-standard Binance USD-M quote source")
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        acquired_at_ms = now_ms()
    except Exception:
        return None
    try:
        if str(payload.get("symbol", "")).upper() != SYMBOL:
            return None
        price = Decimal(str(payload["price"]))
        observed_raw = payload.get("time") if payload.get("time") is not None else payload.get("closeTime")
        if observed_raw is None:
            return None
        observed_ms = int(observed_raw)
    except Exception:
        return None
    if not price.is_finite() or price <= 0:
        return None
    if observed_ms > acquired_at_ms:
        return None
    if acquired_at_ms - observed_ms > max_age_ms:
        return None
    return CurrentQuote(
        price=price,
        observed_ms=observed_ms,
        acquired_at_ms=acquired_at_ms,
        source_url=url,
        raw={"symbol": payload.get("symbol"), "price": str(payload.get("price")), "time": payload.get("time"), "closeTime": payload.get("closeTime")},
    )


def quote_to_json(quote: CurrentQuote | None, attempted: bool) -> dict[str, Any]:
    if quote is None:
        return {
            "status": "missing",
            "source": "binance_usdm_public_ticker_price",
            "url": DEFAULT_USDM_QUOTE_URL,
            "http_budget_used": 1 if attempted else 0,
        }
    source = "manual_argument" if quote.source_url == "manual_argument" else "binance_usdm_public_ticker_price"
    return {
        "status": "available",
        "source": source,
        "url": quote.source_url,
        "price": str(quote.price),
        "observed_ms": quote.observed_ms,
        "acquired_at_ms": quote.acquired_at_ms,
        "http_budget_used": 1,
        "raw": quote.raw,
    }


def compute_candidates(config: KpfConfig, parsed: ParsedWindow, as_of_time: datetime, data_quality_state: str, current_price: Decimal | None) -> tuple[list[Any], list[Any]]:
    density_builder = DensityBuilder()
    extractor = BasinExtractor()
    points_90 = density_builder.build(config, parsed.bars, as_of_time, config.history_days)
    points_60 = density_builder.build(config, parsed.bars, as_of_time, config.fresh_confirm_days)
    candidates_90 = extractor.extract(points_90, "90d")
    candidates_60 = extractor.extract(points_60, "60d")
    assigned = EvidenceAssigner().assign(config, candidates_90, candidates_60, data_quality_state, current_price)
    if current_price is None:
        return [], assigned
    above, below, debug = TargetRanker().rank(config, assigned, current_price, data_quality_state)
    return above + below, debug


def compute_candidates_from_density(config: KpfConfig, window: StreamingWindow, data_quality_state: str, current_price: Decimal | None) -> tuple[list[Any], list[Any]]:
    extractor = BasinExtractor()
    candidates_90 = extractor.extract(window.density_90, "90d")
    candidates_60 = extractor.extract(window.density_60, "60d")
    assigned = EvidenceAssigner().assign(config, candidates_90, candidates_60, data_quality_state, current_price)
    if current_price is None:
        return [], assigned
    above, below, debug = TargetRanker().rank(config, assigned, current_price, data_quality_state)
    return above + below, debug


def zone_identity(candidate: Any) -> str:
    payload = {
        "raw_center": candidate.raw_center,
        "raw_basin_low": candidate.raw_basin_low,
        "raw_basin_high": candidate.raw_basin_high,
        "source_window": candidate.source_window,
        "display_center": candidate.display_center,
        "evidence_grade": candidate.evidence_grade,
        "evidence_type": candidate.evidence_type,
        "grade_path": candidate.grade_path,
    }
    return "kpf_zone_" + sha256_bytes(canonical_json(payload))[:16]


def zone_payload(candidate: Any, selected_public_ids: set[str], data_quality_state: str, self_audit_pass: bool) -> dict[str, Any]:
    zid = zone_identity(candidate)
    public_ab = zid in selected_public_ids and candidate.evidence_grade in {"A", "B"}
    can_use = bool(public_ab and data_quality_state == "OK" and self_audit_pass)
    reason = "public_A_or_B_dataquality_ok_selfaudit_pass" if can_use else "readonly_debug_or_unqualified"
    if zid in selected_public_ids and candidate.evidence_grade not in {"A", "B"}:
        reason = "public_target_not_A_or_B"
    if data_quality_state != "OK":
        reason = f"data_quality_{data_quality_state}"
    if not self_audit_pass:
        reason = "self_audit_not_passed"
    return {
        "zone_id": zid,
        "raw": {
            "center": candidate.raw_center,
            "basin_low": candidate.raw_basin_low,
            "basin_high": candidate.raw_basin_high,
            "density_peak": candidate.raw_density_peak,
            "saddle_level": candidate.saddle_level,
            "basin_width_usd": candidate.basin_width_usd,
        },
        "display": {
            "center": candidate.display_center,
            "band": list(candidate.display_band) if candidate.display_band else None,
            "side": candidate.side,
            "distance_usd": candidate.distance_usd,
        },
        "evidence": {
            "grade": candidate.evidence_grade,
            "type": candidate.evidence_type,
            "grade_path": candidate.grade_path,
            "grade_score": candidate.grade_score,
            "filtered_reason": candidate.filtered_reason,
            "source_window": candidate.source_window,
            "prominence_norm": candidate.prominence_norm,
            "prominence_norm_legacy": candidate.prominence_norm_legacy,
            "prominence_norm_robust": candidate.prominence_norm_robust,
            "excess_mass_norm": candidate.excess_mass_norm,
            "excess_mass_norm_legacy": candidate.excess_mass_norm_legacy,
            "excess_mass_norm_robust": candidate.excess_mass_norm_robust,
            "participating_volume_bars": candidate.participating_volume_bars,
            "volume_share": candidate.volume_share,
        },
        "market": {
            "exchange": "Binance",
            "market": MARKET,
            "symbol": SYMBOL,
            "quote": QUOTE,
            "basis": PRICE_BASIS,
            "price_basis": PRICE_BASIS,
            "mapping": "NOMINAL_ONLY",
            "deribit_mapping": "none",
        },
        "usage": {
            "can_use": can_use,
            "reason": reason,
            "product_id": PRODUCT_ID,
        },
    }


def source_records(statuses: list[SourceDay]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for status in statuses:
        if not status.verified:
            continue
        records.append(
            {
                "date": status.day.isoformat(),
                "path": str(status.zip_path),
                "sha256": status.sha256,
                "bytes": status.bytes,
                "zip_stat": path_stat_fingerprint(status.zip_path),
                "checksum_stat": path_stat_fingerprint(status.checksum_path),
                "source_meta_stat": path_stat_fingerprint(status.zip_path.with_name(status.zip_path.name + ".source.json")),
                "source_acquired_at_ms": status.source_acquired_at_ms,
                "first_full_checksum_verified_at_ms": status.first_full_checksum_verified_at_ms,
                "last_full_checksum_verified_at_ms": status.last_full_checksum_verified_at_ms,
            }
        )
    return records


def build_artifacts(
    *,
    config: KpfConfig,
    as_of_ms: int,
    quote: CurrentQuote | None,
    quote_attempted: bool,
    data_root: Path,
    output_root: Path,
    statuses: list[SourceDay],
    window: StreamingWindow,
    capacity: dict[str, Any],
    max_zones: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    end_day = latest_closed_day(as_of_ms)
    plan = FilePlan(
        as_of_time=ms_to_utc(as_of_ms),
        effective_end_date=end_day,
        required_dates=required_dates(end_day, config.history_days),
        fresh_dates=required_dates(end_day, config.fresh_confirm_days),
    )
    quality = classify_data_quality(plan, statuses, window)
    verified_complete = len(quality["verified_dates"]) == len(plan.required_dates)
    current_price = quote.price if quote is not None else None
    selected, debug = compute_candidates_from_density(config, window, quality["data_quality_state"], current_price)
    if len(debug) > max_zones:
        digest = sha256_bytes(canonical_json([candidate.__dict__ for candidate in debug]))
        raise ZoneCountRejected(f"raw zone pool has {len(debug)} zones, max {max_zones}, sha256 {digest}")

    status_code = "READY"
    supported_ready = True
    self_audit_pass = True
    reasons: list[str] = []
    if not capacity["ok"]:
        status_code = "CAPACITY_PREFLIGHT"
        supported_ready = False
        self_audit_pass = False
        reasons.append("capacity_preflight_failed")
    if not verified_complete:
        status_code = "WARMUP"
        supported_ready = False
        self_audit_pass = False
        reasons.append("history_window_not_complete")
    if current_price is None:
        status_code = "NO_QUOTES" if supported_ready else status_code
        supported_ready = False
        self_audit_pass = False
        reasons.append("current_price_missing")
    if quality["data_quality_state"] != "OK":
        status_code = "DATA_NOT_OK" if status_code == "READY" else status_code
        supported_ready = False
        self_audit_pass = False
        reasons.append(f"data_quality_{quality['data_quality_state']}")
    if window.schema_failed_dates:
        status_code = "SCHEMA_FAILED"
        supported_ready = False
        self_audit_pass = False
        reasons.append("schema_failed")

    selected_ids = {zone_identity(candidate) for candidate in selected}
    zones = [zone_payload(candidate, selected_ids, quality["data_quality_state"], self_audit_pass) for candidate in debug]
    public_targets = [zone for zone in zones if zone["zone_id"] in selected_ids]
    usable_count = sum(1 for zone in zones if zone["usage"]["can_use"])
    generated_at_ms = now_ms()
    version_seed = {
        "generated_at_ms": generated_at_ms,
        "as_of_ms": as_of_ms,
        "factor_version": FACTOR_VERSION,
        "current_price": str(current_price) if current_price is not None else None,
        "source_hashes": [(s.day.isoformat(), s.sha256) for s in statuses if s.verified],
        "zone_ids": [z["zone_id"] for z in zones],
        "status_code": status_code,
    }
    version_id = "kpf-light-v1-" + sha256_bytes(canonical_json(version_seed))[:20]
    latest_trade = window.latest_trade
    clock = {
        "as_of_ms": as_of_ms,
        "latest_closed_day": end_day.isoformat(),
        "last_trade_observed_ms": latest_trade.ts_ms if latest_trade else None,
        "last_trade_source_file": latest_trade.source_file if latest_trade else None,
        "source_known": latest_trade is not None,
    }
    quote_payload = quote_to_json(quote, quote_attempted)
    input_fp = input_fingerprint(data_root, statuses)
    snapshot = {
        "schema": SCHEMA_SNAPSHOT,
        "version_id": version_id,
        "generated_at_ms": generated_at_ms,
        "product_id": PRODUCT_ID,
        "factor_version": FACTOR_VERSION,
        "input_fingerprint": input_fp,
        "status": status_code,
        "supported_ready": supported_ready,
        "self_audit_pass": self_audit_pass,
        "reasons": reasons,
        "market": {
            "exchange": "Binance",
            "market": MARKET,
            "symbol": SYMBOL,
            "quote": QUOTE,
            "basis": PRICE_BASIS,
            "price_basis": PRICE_BASIS,
            "mapping": "NOMINAL_ONLY",
        },
        "clock": clock,
        "quote": quote_payload,
        "current_price": str(current_price) if current_price is not None else None,
        "public_targets": public_targets,
        "usage": {
            "can_use": usable_count > 0,
            "usable_zone_count": usable_count,
            "rule": "only_public_A_or_B_with_dataquality_OK_and_selfaudit_pass",
        },
    }
    audit = {
        "schema": SCHEMA_AUDIT,
        "version_id": version_id,
        "generated_at_ms": generated_at_ms,
        "product_id": PRODUCT_ID,
        "factor_version": FACTOR_VERSION,
        "input_fingerprint": input_fp,
        "status": status_code,
        "supported_ready": supported_ready,
        "self_audit_pass": self_audit_pass,
        "reasons": reasons,
        "capacity": capacity,
        "data_quality": quality,
        "parse": {
            "bar_count": window.volume_bar_count,
            "parse_audit_count": len(window.parse_audits),
            "latest_trade": trade_to_json(window.latest_trade),
            "duplicate_count": window.duplicate_count,
            "duplicate_conflict_count": window.duplicate_conflict_count,
            "schema_failed_dates": window.schema_failed_dates,
            "tail_phase_policy": "single_volume_bar_builder_across_all_verified_days",
            "stored_cache_policy": "source_zip_and_checksum_only_streaming_density_no_full_parsed_csv_no_window_bar_array",
            "production_density_policy": "stream_volume_bars_into_density_accumulator_no_90d_bar_list",
        },
        "source_records": source_records(statuses),
        "quote": quote_payload,
        "source_manifest": vendor_manifest_summary(),
        "output_root": str(output_root),
        "data_root": str(data_root),
    }
    raw_zones = {
        "schema": SCHEMA_ZONES,
        "version_id": version_id,
        "generated_at_ms": generated_at_ms,
        "product_id": PRODUCT_ID,
        "factor_version": FACTOR_VERSION,
        "input_fingerprint": input_fp,
        "status": status_code,
        "clock": clock,
        "quote": quote_payload,
        "zone_count": len(zones),
        "zones": zones,
    }
    return snapshot, audit, raw_zones


def trade_to_json(trade: TradeRecord | None) -> dict[str, Any] | None:
    if trade is None:
        return None
    return {
        "agg_id": trade.agg_id,
        "price": str(trade.price),
        "qty": str(trade.qty),
        "first_trade_id": trade.first_trade_id,
        "last_trade_id": trade.last_trade_id,
        "ts_ms": trade.ts_ms,
        "is_buyer_maker": trade.is_buyer_maker,
        "source_file": trade.source_file,
    }


def vendor_manifest_path() -> Path:
    return REPO_ROOT / "deploy" / "kpf_light" / "source_manifest.json"


def vendor_manifest_summary() -> dict[str, Any]:
    path = vendor_manifest_path()
    if not path.exists():
        return {"ok": False, "error": "source_manifest_missing"}
    manifest = read_json(path)
    ok, errors = verify_vendor_manifest(manifest)
    return {
        "schema": manifest.get("schema"),
        "vendor_source": manifest.get("vendor_source"),
        "ok": ok,
        "errors": errors,
        "module_count": len(manifest.get("files") or []),
        "manifest_sha256": sha256_file(path),
    }


def verify_vendor_manifest(manifest: dict[str, Any] | None = None) -> tuple[bool, list[str]]:
    manifest = manifest or read_json(vendor_manifest_path())
    errors: list[str] = []
    for item in manifest.get("files") or []:
        path = REPO_ROOT / item["path"]
        if not path.exists():
            errors.append(f"missing:{item['path']}")
            continue
        actual = sha256_file(path)
        if actual.lower() != str(item["sha256"]).lower():
            errors.append(f"hash_mismatch:{item['path']}")
    return not errors, errors


def publish_bundle(output_root: Path, snapshot: dict[str, Any], audit: dict[str, Any], zones: dict[str, Any]) -> dict[str, Any]:
    version_ids = {snapshot.get("version_id"), audit.get("version_id"), zones.get("version_id")}
    if len(version_ids) != 1:
        raise KpfLightError("artifact version_id mismatch")
    version_id = str(snapshot["version_id"])
    if not re.fullmatch(r"kpf-light-v1-[0-9a-f]{20}", version_id):
        raise KpfLightError("invalid artifact version_id")
    output_root.mkdir(parents=True, exist_ok=True)
    version_dir = output_root / "versions" / version_id
    version_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "snapshot": ("snapshot.json", snapshot),
        "audit": ("audit.json", audit),
        "zones": ("raw_zones.json", zones),
    }
    artifact_refs: dict[str, dict[str, Any]] = {}
    for key, (name, payload) in artifacts.items():
        path = version_dir / name
        if path.exists() and read_json(path) != payload:
            raise KpfLightError("immutable artifact version conflict")
        write_json_atomic(path, payload)
        artifact_refs[key] = {
            "path": str(path.relative_to(output_root)).replace("\\", "/"),
            "sha256": sha256_file(path),
        }
    manifest = {
        "schema": SCHEMA_MANIFEST,
        "version_id": version_id,
        "generated_at_ms": snapshot["generated_at_ms"],
        "status": snapshot["status"],
        "supported_ready": snapshot["supported_ready"],
        "product_id": PRODUCT_ID,
        "input_fingerprint": snapshot.get("input_fingerprint"),
        "artifacts": artifact_refs,
    }
    immutable_manifest = version_dir / "manifest.json"
    write_json_atomic(immutable_manifest, manifest)
    current = output_root / "current.json"
    previous = output_root / "previous.json"
    old = read_json(current) if current.exists() else None
    prior = read_json(previous) if previous.exists() else None
    # Keep the last complete result across repeated warmup/failure updates.
    # It remains historical; current always reports the current qualification.
    if old and old.get("version_id") != version_id:
        if old.get("status") == "READY" or not prior or prior.get("status") != "READY":
            write_json_atomic(previous, old)
    write_json_atomic(current, manifest)
    keep = {version_id}
    if previous.exists():
        keep.add(read_json(previous).get("version_id"))
    owned_names = {"snapshot.json", "audit.json", "raw_zones.json", "manifest.json"}
    for candidate in (output_root / "versions").iterdir():
        if candidate.name in keep or not re.fullmatch(r"kpf-light-v1-[0-9a-f]{20}", candidate.name):
            continue
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        children = list(candidate.iterdir())
        if any(child.is_symlink() or not child.is_file() or child.name not in owned_names for child in children):
            continue
        for child in children:
            child.unlink()
        candidate.rmdir()
    return manifest


def current_snapshot_summary(output_root: Path) -> dict[str, Any] | None:
    current = output_root / "current.json"
    if not current.exists():
        return None
    try:
        manifest = read_json(current)
        snapshot_ref = (manifest.get("artifacts") or {}).get("snapshot") or {}
        snapshot_path = output_root / snapshot_ref.get("path", "")
        snapshot = read_json(snapshot_path)
    except Exception:
        return None
    return {
        "manifest": manifest,
        "snapshot": snapshot,
        "latest_closed_day": ((snapshot.get("clock") or {}).get("latest_closed_day")),
        "status": snapshot.get("status"),
    }


def can_skip_same_cutoff(output_root: Path, data_root: Path, end_day: date, days: list[date]) -> bool:
    current = output_root / "current.json"
    if not current.exists():
        return False
    try:
        manifest = read_json(current)
    except Exception:
        return False
    if manifest.get("status") != "READY":
        return False
    if not verified_manifest_artifacts(output_root, manifest):
        return False
    snapshot = manifest_snapshot(output_root, manifest)
    if not snapshot:
        return False
    if ((snapshot.get("clock") or {}).get("latest_closed_day")) != end_day.isoformat():
        return False
    if snapshot.get("status") != "READY":
        return False
    expected = manifest.get("input_fingerprint") or snapshot.get("input_fingerprint")
    if not expected:
        return False
    current_fp = {
        "schema": "astra_kpf_light_input_fingerprint@1",
        "algorithm": algorithm_fingerprint(),
        "source": source_input_fingerprint_fast(data_root, days),
    }
    return current_fp == expected


def record_failure(output_root: Path, error: str, details: dict[str, Any]) -> Path:
    failures = output_root / "failures"
    failures.mkdir(parents=True, exist_ok=True)
    payload = {"schema": "astra_kpf_light_failure@1", "recorded_at_ms": now_ms(), "error": error, "details": details}
    path = failures / f"{payload['recorded_at_ms']}_{sha256_bytes(canonical_json(payload))[:12]}.json"
    write_json_atomic(path, payload)
    keep_latest_files(failures, 50)
    return path


def keep_latest_files(root: Path, keep: int) -> None:
    files = sorted([p for p in root.glob("*.json") if p.is_file()], key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[keep:]:
        path.unlink(missing_ok=True)


def run_oneshot(
    *,
    data_root: Path,
    output_root: Path,
    as_of_ms: int | None = None,
    current_price: Decimal | None = None,
    fetch_quote: bool = False,
    quote_timeout_seconds: int = 10,
    allow_download: bool = False,
    min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
    storage_budget_bytes: int = DEFAULT_STORAGE_BUDGET_BYTES,
    max_zones: int = DEFAULT_MAX_ZONES,
) -> dict[str, Any]:
    as_of_ms = as_of_ms or now_ms()
    data_root = data_root.resolve()
    output_root = output_root.resolve()
    config = build_config(data_root)
    with RunLock(data_root / "run.lock"):
        end_day = latest_closed_day(as_of_ms)
        days = required_dates(end_day, config.history_days)
        retention_before = cleanup_source_retention(
            data_root,
            output_root,
            end_day,
            config.history_days,
            keep_previous_ready=True,
        )
        capacity = capacity_preflight(data_root, min_free_bytes, storage_budget_bytes)
        capacity["source_retention"] = retention_before
        if capacity["ok"] and can_skip_same_cutoff(output_root, data_root, end_day, days):
            return {
                "ok": True,
                "unchanged": True,
                "reason": "same_latest_closed_day_ready_bundle",
                "current_manifest_path": str(output_root / "current.json"),
            }
        if capacity["ok"]:
            maybe_download_one_missing_day(
                data_root,
                days,
                allow_download=allow_download,
                timeout_seconds=int(config.download.get("timeout_seconds", 45)),
                min_free_bytes=min_free_bytes,
                storage_budget_bytes=storage_budget_bytes,
            )
        statuses = inspect_sources(data_root, days)
        window_complete = all(item.verified for item in statuses)
        quote = None
        quote_attempted = False
        window = StreamingWindow.empty()
        if capacity["ok"] and window_complete:
            window = stream_window(config, statuses, ms_to_utc(as_of_ms))
            if current_price is not None:
                quote = CurrentQuote(
                    price=current_price,
                    observed_ms=as_of_ms,
                    acquired_at_ms=now_ms(),
                    source_url="manual_argument",
                    raw={"source": "manual_argument", "price": str(current_price)},
                )
            elif fetch_quote:
                quote_attempted = True
                quote = fetch_current_quote(timeout_seconds=quote_timeout_seconds)
        try:
            snapshot, audit, zones = build_artifacts(
                config=config,
                as_of_ms=as_of_ms,
                quote=quote,
                quote_attempted=quote_attempted,
                data_root=data_root,
                output_root=output_root,
                statuses=statuses,
                window=window,
                capacity=capacity,
                max_zones=max_zones,
            )
            manifest = publish_bundle(output_root, snapshot, audit, zones)
            retention_after = None
            if snapshot.get("status") == "READY":
                retention_after = cleanup_source_retention(
                    data_root,
                    output_root,
                    end_day,
                    config.history_days,
                    keep_previous_ready=False,
                )
            return {
                "ok": True,
                "manifest": manifest,
                "current_manifest_path": str(output_root / "current.json"),
                "source_retention_after": retention_after,
            }
        except Exception as exc:
            failure_path = record_failure(
                output_root,
                str(exc),
                {
                    "as_of_ms": as_of_ms,
                    "latest_closed_day": end_day.isoformat(),
                    "verified_count": sum(1 for item in statuses if item.verified),
                    "required_count": len(statuses),
                },
            )
            if isinstance(exc, ZoneCountRejected):
                return {"ok": False, "error": str(exc), "failure_path": str(failure_path)}
            raise


def status(output_root: Path) -> dict[str, Any]:
    current = output_root / "current.json"
    if not current.exists():
        return {"ok": False, "status": "NO_CURRENT_MANIFEST", "current_manifest_path": str(current)}
    manifest = read_json(current)
    artifacts = manifest.get("artifacts") or {}
    errors: list[str] = []
    for key, ref in artifacts.items():
        path = output_root / ref.get("path", "")
        if not path.exists():
            errors.append(f"missing:{key}")
            continue
        actual = sha256_file(path)
        if actual != ref.get("sha256"):
            errors.append(f"hash_mismatch:{key}")
    return {"ok": not errors, "status": manifest.get("status"), "manifest": manifest, "errors": errors}


def parse_decimal(value: str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Astra KPF light one-shot producer")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-root", default=os.environ.get("ASTRA_KPF_LIGHT_DATA_ROOT", "/var/lib/astra-kpf-light"))
    common.add_argument("--output-root", default=os.environ.get("ASTRA_KPF_LIGHT_OUTPUT_ROOT", "/var/lib/astra-kpf-light/published"))

    one = sub.add_parser("oneshot", parents=[common])
    one.add_argument("--as-of-ms", type=int, default=None)
    one.add_argument("--current-price", default=None)
    one.add_argument("--fetch-quote", action="store_true", default=os.environ.get("ASTRA_KPF_LIGHT_FETCH_QUOTE", "0") == "1")
    one.add_argument("--quote-timeout-seconds", type=int, default=10)
    one.add_argument("--allow-download", action="store_true", default=os.environ.get("ASTRA_KPF_LIGHT_ALLOW_DOWNLOAD", "0") == "1")
    one.add_argument("--min-free-bytes", type=int, default=DEFAULT_MIN_FREE_BYTES)
    one.add_argument("--storage-budget-bytes", type=int, default=DEFAULT_STORAGE_BUDGET_BYTES)
    one.add_argument("--max-zones", type=int, default=DEFAULT_MAX_ZONES)

    preflight = sub.add_parser("preflight", parents=[common])
    preflight.add_argument("--min-free-bytes", type=int, default=DEFAULT_MIN_FREE_BYTES)
    preflight.add_argument("--storage-budget-bytes", type=int, default=DEFAULT_STORAGE_BUDGET_BYTES)

    sub.add_parser("verify-vendor", parents=[common])
    sub.add_parser("status", parents=[common])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    data_root = Path(args.data_root)
    output_root = Path(args.output_root)
    try:
        if args.command == "oneshot":
            result = run_oneshot(
                data_root=data_root,
                output_root=output_root,
                as_of_ms=args.as_of_ms,
                current_price=parse_decimal(args.current_price),
                fetch_quote=bool(args.fetch_quote),
                quote_timeout_seconds=int(args.quote_timeout_seconds),
                allow_download=bool(args.allow_download),
                min_free_bytes=int(args.min_free_bytes),
                storage_budget_bytes=int(args.storage_budget_bytes),
                max_zones=int(args.max_zones),
            )
        elif args.command == "preflight":
            result = capacity_preflight(data_root, int(args.min_free_bytes), int(args.storage_budget_bytes))
        elif args.command == "verify-vendor":
            ok, errors = verify_vendor_manifest()
            result = {"ok": ok, "errors": errors, "manifest_path": str(vendor_manifest_path())}
        elif args.command == "status":
            result = status(output_root)
        else:
            raise KpfLightError(f"unsupported command: {args.command}")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("ok", True) else 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
