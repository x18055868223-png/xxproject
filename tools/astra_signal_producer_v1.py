"""Server-native signal producer wrapping the canonical neutral-loop core.

Runs the exact factor, state-machine, EvaluationSnapshot, and signal_review_card
algorithms from ``neutral_regulation_demo_fmz.py`` directly under systemd / Python
with zero FMZ platform dependency.
"""

from __future__ import annotations

import argparse
from collections import deque
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import sys
import threading
import time
from typing import Any, Callable, Mapping

if __package__:
    from . import astra_deribit_cooldown_v1 as deribit_cooldown
else:
    import astra_deribit_cooldown_v1 as deribit_cooldown

PRODUCER_ID = "astra-signal-producer@1.0.0"
PRODUCER_PLATFORM = "xxproject_server_native"
PRODUCER_SERVICE = "astra-signal-producer"
IDENTITY_SCHEMA = "xxproject_signal_producer_identity@1.0.0"
CHECKPOINT_SCHEMA = "astra_signal_runtime_checkpoint@1.0.0"
HEALTH_SCHEMA = "astra_signal_producer_health@1.0.0"

DEFAULT_DERIBIT_TICKER_INTERVAL_MS = 200
DEFAULT_DERIBIT_429_COOLDOWN_SEC = 60
DEFAULT_DERIBIT_WALL_TIME_MS = 25_000
DEFAULT_CHECKPOINT_MAX_AGE_MS = 86_400_000  # 24h
DEFAULT_BARS_RESTORE_MAX_AGE_MS = 90_000    # 90s: fast restarts keep partial bar, longer gaps rebuild via 1m backfill


def _repo_or_release_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_core_path(core_path: str | Path | None = None, *, env: Mapping[str, str] | None = None) -> Path:
    """Resolve the canonical signal core file path without relying on FMZ."""
    env_map = os.environ if env is None else env
    candidates: list[Path] = []
    if core_path is not None and str(core_path).strip():
        candidates.append(Path(str(core_path).strip()))
    env_core = (env_map.get("ASTRA_SIGNAL_CORE_PATH") or "").strip()
    if env_core:
        candidates.append(Path(env_core))
    root = _repo_or_release_root()
    candidates.append(root / "signal_core" / "neutral_regulation_demo_fmz.py")
    candidates.append(root / "demo" / "最新交付物" / "neutral_regulation_demo_fmz.py")
    for cand in candidates:
        if cand.is_file():
            return cand.resolve()
    searched = ", ".join(str(p) for p in candidates)
    raise FileNotFoundError(f"signal core file not found; searched: {searched}")


def compute_lf_sha256(path: Path) -> str:
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(raw).hexdigest()


def _neutralize_fmz_hooks(core_mod: Any, *, logger: Callable[[str], None] | None = None) -> None:
    """Replace optional FMZ globals so the core never expects an FMZ host."""
    log_fn = logger or (lambda line: sys.stderr.write(line + "\n"))

    def _log(*parts: Any) -> None:
        text = " ".join(str(part) for part in parts if part is not None)
        if text:
            log_fn(f"[astra-signal] {text}")

    core_mod.fmz_log = _log
    core_mod.fmz_status = lambda text: None
    core_mod.fmz_push = lambda text: None
    core_mod.fmz_sleep = lambda ms: time.sleep(max(0.0, float(ms) / 1000.0))


def load_signal_core(
    core_path: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    unique_module: bool = False,
) -> tuple[Any, Path, str]:
    """Load and validate the unmodified signal core module."""
    resolved = resolve_core_path(core_path, env=env)
    core_sha = compute_lf_sha256(resolved)
    mod_name = "astra_signal_core_nrd" if not unique_module else f"astra_signal_core_nrd_{time.time_ns()}"
    spec = importlib.util.spec_from_file_location(mod_name, resolved)
    if spec is None or spec.loader is None:
        raise ImportError(f"unable to load signal core module from {resolved}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cfg = getattr(module, "CONFIG", None)
    if not isinstance(cfg, dict):
        raise ValueError("signal core missing CONFIG dict")
    version = str(cfg.get("demo_version") or "")
    if not version.startswith("2."):
        raise ValueError(f"unsupported signal core version: {version!r}")
    if cfg.get("read_only_demo") is not True:
        raise ValueError("signal core must preserve read_only_demo=True")
    _neutralize_fmz_hooks(module)
    return module, resolved, core_sha


def _atomic_write_json(path: Path, payload: Any, *, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    fd: int | None = None
    replaced = False
    try:
        flags = os.O_CREAT | os.O_TRUNC | os.O_WRONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        fd = os.open(str(tmp_path), flags, mode)
        raw = encoded.encode("utf-8")
        written = 0
        while written < len(raw):
            chunk = os.write(fd, raw[written:])
            if chunk <= 0:
                raise OSError("atomic json short write")
            written += chunk
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(tmp_path, path)
        replaced = True
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if not replaced:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _parse_int(value: Any, default: int, *, minimum: int | None = None) -> int:
    if value is None or str(value).strip() == "":
        parsed = default
    else:
        parsed = int(str(value).strip())
    if minimum is not None and parsed < minimum:
        return minimum
    return parsed


def build_native_config(
    core_mod: Any,
    *,
    logs_dir: str | Path,
    state_dir: str | Path,
    env: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a server-native CONFIG dictionary preserving all algorithm thresholds."""
    env_map = os.environ if env is None else env
    logs_path = Path(logs_dir)
    state_path = Path(state_dir)
    cfg = dict(core_mod.CONFIG)

    # Headless server defaults (never emit FMZ status/chart/push noise).
    cfg.update({
        "read_only_demo": True,
        "log_status_enabled": False,
        "chart_enabled": False,
        "chart_reset_on_start": False,
        "signal_review_push_enabled": False,
        "signal_review_push_test": False,
        "decision_snapshot_diagnostic_log_enabled": False,
        "logs_dir": str(logs_path),
        "data_export_snapshot_file": str(logs_path / "snapshots.jsonl"),
        "space_gate_state_file": str(logs_path / "space_gate_state.json"),
        "fixed_analysis_round_state_file": str(logs_path / "fixed_analysis_round_state.json"),
        "gex_info_cache_file": str(logs_path / "gex_info_cache.json"),
        "macro_cache_file": str(logs_path / "macro_factor_cache.json"),
        "deribit_option_refresh_wall_time_ms": _parse_int(
            env_map.get("ASTRA_SIGNAL_DERIBIT_WALL_TIME_MS"),
            DEFAULT_DERIBIT_WALL_TIME_MS,
            minimum=1000,
        ),
        "native_state_dir": str(state_path),
        "native_deribit_ticker_interval_ms": _parse_int(
            env_map.get("ASTRA_SIGNAL_DERIBIT_TICKER_INTERVAL_MS"),
            DEFAULT_DERIBIT_TICKER_INTERVAL_MS,
            minimum=0,
        ),
        "native_deribit_429_cooldown_sec": _parse_int(
            env_map.get("ASTRA_SIGNAL_DERIBIT_429_COOLDOWN_SEC"),
            DEFAULT_DERIBIT_429_COOLDOWN_SEC,
            minimum=5,
        ),
        "native_checkpoint_max_age_ms": _parse_int(
            env_map.get("ASTRA_SIGNAL_CHECKPOINT_MAX_AGE_MS"),
            DEFAULT_CHECKPOINT_MAX_AGE_MS,
            minimum=0,
        ),
        "native_bars_restore_max_age_ms": _parse_int(
            env_map.get("ASTRA_SIGNAL_BARS_RESTORE_MAX_AGE_MS"),
            DEFAULT_BARS_RESTORE_MAX_AGE_MS,
            minimum=0,
        ),
    })

    if (env_map.get("ASTRA_SIGNAL_GEX_INFO_URL") or "").strip():
        cfg["gex_info_base_url"] = env_map["ASTRA_SIGNAL_GEX_INFO_URL"].strip()
    if (env_map.get("ASTRA_SIGNAL_LOOP_SLEEP_MS") or "").strip():
        cfg["loop_sleep_ms"] = _parse_int(env_map.get("ASTRA_SIGNAL_LOOP_SLEEP_MS"), 60_000, minimum=100)
    if (env_map.get("ASTRA_SIGNAL_AUDIT_STATIC_BASE_URL") or "").strip():
        cfg["audit_static_base_url"] = env_map["ASTRA_SIGNAL_AUDIT_STATIC_BASE_URL"].strip()

    if overrides:
        if overrides.get("read_only_demo") is False:
            raise ValueError("READ_ONLY_DEMO_REQUIRED")
        cfg.update(overrides)
    if cfg.get("read_only_demo") is not True:
        raise ValueError("READ_ONLY_DEMO_REQUIRED")
    return cfg


def ensure_log_hygiene(logs_dir: Path, *, now_ms: int | None = None) -> dict[str, Any]:
    """Ensure startup log files are well-formed without destroying valid history."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"signal_review_newline_repaired": False, "snapshots_quarantined": None}

    review_path = logs_dir / "signal_review.jsonl"
    if review_path.is_file() and review_path.stat().st_size > 0:
        with review_path.open("rb+") as handle:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                handle.seek(0, os.SEEK_END)
                handle.write(b"\n")
                handle.flush()
                os.fsync(handle.fileno())
                report["signal_review_newline_repaired"] = True

    snapshots_path = logs_dir / "snapshots.jsonl"
    if snapshots_path.is_file() and snapshots_path.stat().st_size > 0:
        raw_lines = [ln for ln in snapshots_path.read_bytes().splitlines() if ln.strip()]
        valid = 0
        for line in raw_lines:
            try:
                obj = json.loads(line.decode("utf-8"))
                if isinstance(obj, dict):
                    valid += 1
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        if raw_lines and valid == 0:
            ts = int(time.time() * 1000) if now_ms is None else int(now_ms)
            quarantine = snapshots_path.with_name(f"snapshots.jsonl.corrupt-{ts}")
            os.replace(snapshots_path, quarantine)
            report["snapshots_quarantined"] = str(quarantine)
    return report


def _sd_notify(message: str) -> bool:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr or not hasattr(socket, "AF_UNIX"):
        return False
    sock_addr: str | bytes = "\0" + addr[1:] if addr.startswith("@") else addr
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(message.encode("utf-8"), sock_addr)
        return True
    except OSError:
        return False


def _is_rate_limit_response(result: Any) -> bool:
    if isinstance(result, Mapping):
        if result.get("quality") == "OK":
            return False
        err = str(result.get("error") or "").lower()
        if any(tok in err for tok in ("429", "too many requests", "too_many_requests", "10028")):
            return True
        data = result.get("data")
        if isinstance(data, Mapping):
            d_err = data.get("error")
            if isinstance(d_err, Mapping):
                if d_err.get("code") == 10028 or "too_many_requests" in str(d_err.get("message") or "").lower():
                    return True
    return False


class NativeSignalRuntime:
    """Server-native execution harness around ``DemoRuntime``."""

    def __init__(
        self,
        *,
        logs_dir: str | Path,
        state_dir: str | Path,
        core_path: str | Path | None = None,
        core_mod: Any | None = None,
        core_sha256: str | None = None,
        env: Mapping[str, str] | None = None,
        overrides: Mapping[str, Any] | None = None,
        force_deribit_pacing: bool = False,
        cooldown_file: str | Path | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        env_map = os.environ if env is None else env
        self.logs_dir = Path(logs_dir)
        self.state_dir = Path(state_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)

        if core_mod is None:
            self.core_mod, self.core_path, self.core_source_sha256 = load_signal_core(
                core_path, env=env_map
            )
        else:
            self.core_mod = core_mod
            self.core_path = Path(core_path) if core_path is not None else Path(
                getattr(core_mod, "__file__", "unknown")
            )
            self.core_source_sha256 = core_sha256 or (
                compute_lf_sha256(self.core_path) if self.core_path.is_file() else ""
            )

        self.stop_event = stop_event or threading.Event()
        self.instance_id = (
            (env_map.get("ASTRA_SIGNAL_INSTANCE_ID") or "").strip()
            or socket.gethostname()
            or PRODUCER_SERVICE
        )
        self.cooldown_file = (
            str(cooldown_file) if cooldown_file is not None else (env_map.get("ASTRA_DERIBIT_COOLDOWN_FILE") or "").strip() or None
        )
        self.config = build_native_config(
            self.core_mod,
            logs_dir=self.logs_dir,
            state_dir=self.state_dir,
            env=env_map,
            overrides=overrides,
        )
        self.core_version = str(self.config.get("demo_version") or "")
        self.hygiene_report = ensure_log_hygiene(self.logs_dir, now_ms=self._now_ms())

        self.runtime = self.core_mod.DemoRuntime(self.config)
        self._orig_http_get_json = self.runtime.http.get_json
        self._force_deribit_pacing = bool(force_deribit_pacing)
        self._deribit_ticker_interval_ms = int(self.config["native_deribit_ticker_interval_ms"])
        self._deribit_429_cooldown_ms = int(self.config["native_deribit_429_cooldown_sec"]) * 1000
        self._deribit_429_until_ms = 0
        self._deribit_429_count = 0
        self._last_ticker_mono: float | None = None

        self.started_at_ms = self._now_ms()
        self.loop_count = 0
        self.last_snapshot_exported_ms: int | None = None
        self.checkpoint_saved_ms: int | None = None
        self.checkpoint_restored: dict[str, Any] = {"restored": False}
        self._pending_bars_restore_catchup_check: bool = False

        self._install_runtime_hooks()
        self.checkpoint_restored = self.restore_checkpoint()

    def _now_ms(self) -> int:
        return int(self.core_mod.now_ms())

    @property
    def checkpoint_path(self) -> Path:
        return self.state_dir / "runtime_checkpoint.json"

    @property
    def health_path(self) -> Path:
        return self.state_dir / "producer_health.json"

    def _sleep_interruptible(self, ms: float) -> None:
        seconds = max(0.0, float(ms) / 1000.0)
        if seconds > 0.0:
            self.stop_event.wait(seconds)

    def _install_runtime_hooks(self) -> None:
        rt = self.runtime
        core = self.core_mod

        # 1. Atomic caches for gex_info and macro_factor
        def _gex_write_cache_atomic(snapshot: Any) -> bool:
            path = rt.gex_info.config.get("gex_info_cache_file")
            if not path:
                return False
            try:
                _atomic_write_json(
                    Path(path),
                    {"updated_at_ms": self._now_ms(), "snapshot": snapshot},
                    mode=0o644,
                )
                return True
            except Exception:
                return False

        def _macro_write_cache_atomic(components: Any) -> bool:
            path = rt.macro_factor.config.get("macro_cache_file")
            if not path:
                return False
            try:
                payload = rt.macro_factor._read_cache_payload()
                payload["updated_at_ms"] = self._now_ms()
                payload["components"] = components
                _atomic_write_json(Path(path), payload, mode=0o644)
                return True
            except Exception:
                return False

        def _macro_write_prev_atomic(snapshot: Any) -> bool:
            path = rt.macro_factor.config.get("macro_cache_file")
            if not path:
                return False
            summary = core.macro_previous_snapshot_summary(snapshot)
            if not summary:
                return False
            try:
                payload = rt.macro_factor._read_cache_payload()
                payload[core.MACRO_PREVIOUS_SNAPSHOT_KEY] = summary
                payload["previous_snapshot_updated_at_ms"] = self._now_ms()
                _atomic_write_json(Path(path), payload, mode=0o644)
                return True
            except Exception:
                return False

        rt.gex_info._write_cache = _gex_write_cache_atomic
        rt.macro_factor._write_cache = _macro_write_cache_atomic
        rt.macro_factor._write_previous_snapshot = _macro_write_prev_atomic

        # 1b. Preserve honest LKGV staleness & enrichment staleness on gex_info snapshots
        orig_gex_cached_or_missing = rt.gex_info._cached_or_missing
        orig_gex_fetch_snapshot = rt.gex_info._fetch_snapshot

        def _mark_gex_snapshot_flags(snap: Any, enrichment_meta: Mapping[str, Any] | None = None) -> Any:
            if not isinstance(snap, dict):
                return snap
            out = dict(snap)
            quality = str(out.get("quality") or "").upper()
            data_state = str(out.get("data_state") or "").lower()
            if data_state == "lkgv_cache" or quality == "STALE":
                out["stale"] = True
            if enrichment_meta:
                out["enrichment_stale"] = bool(enrichment_meta.get("enrichment_stale", False))
                if enrichment_meta.get("enrichment_availability") is not None:
                    out["enrichment_availability"] = enrichment_meta.get("enrichment_availability")
            return out

        def _native_gex_cached_or_missing(reasons: Any, error: Any = None) -> Any:
            return _mark_gex_snapshot_flags(orig_gex_cached_or_missing(reasons, error=error))

        def _native_gex_fetch_snapshot() -> Any:
            meta: dict[str, Any] = {}
            cur_http_get_json = rt.gex_info.http.get_json

            def _capturing_gex_get_json(*args: Any, **kwargs: Any) -> Any:
                res = cur_http_get_json(*args, **kwargs)
                if isinstance(res, Mapping) and res.get("quality") == core.QUALITY_OK:
                    data = res.get("data")
                    if isinstance(data, Mapping):
                        if "enrichment_stale" in data:
                            meta["enrichment_stale"] = bool(data.get("enrichment_stale"))
                        if data.get("enrichment_availability") is not None:
                            meta["enrichment_availability"] = data.get("enrichment_availability")
                return res

            rt.gex_info.http.get_json = _capturing_gex_get_json
            try:
                snap = orig_gex_fetch_snapshot()
            finally:
                rt.gex_info.http.get_json = cur_http_get_json
            if isinstance(snap, dict) and str(snap.get("quality") or "").upper() == "OK" and meta:
                updated = _mark_gex_snapshot_flags(snap, meta)
                rt.gex_info._write_cache(updated)
                return updated
            return _mark_gex_snapshot_flags(snap)

        rt.gex_info._cached_or_missing = _native_gex_cached_or_missing
        rt.gex_info._fetch_snapshot = _native_gex_fetch_snapshot

        # 1c. Post-restore catchup guard: fall back to 1m kline backfill if restored bars stay backlogged
        orig_poll_with_drain = rt.bars.poll_with_drain

        def _native_poll_with_drain() -> list[Any]:
            bars = orig_poll_with_drain()
            if self._pending_bars_restore_catchup_check:
                self._pending_bars_restore_catchup_check = False
                if (rt.bars.last_cycle_metrics or {}).get("backlogged"):
                    rt.bars.completed_bars.clear()
                    rt.bars.last_trade_id = None
                    rt.bars.bar_index = 0
                    rt.bars.current_open = None
                    rt.bars.current_high = None
                    rt.bars.current_low = None
                    rt.bars.current_close = None
                    rt.bars.current_volume = 0.0
                    rt.bars.current_cvd = 0.0
                    rt.bars.current_last_trade_ts_ms = None
                    rt.bars.cvd_degraded = False
                    rt.last_anchor_bar_index = None
                    rt.last_edb_cvd_bar_index = None
                    rt.anchor_nd_window.clear()
                    rt.bar_backfill = rt._backfill_volume_bars()
                    bars = orig_poll_with_drain()
            return bars

        rt.bars.poll_with_drain = _native_poll_with_drain

        # 2. Enrich EvaluationSnapshot with truthful server-native identity
        orig_maybe_write = rt.snapshot_exporter.maybe_write

        def _native_maybe_write(payload: Mapping[str, Any], factor_snapshot: Any = None) -> bool:
            enriched = dict(payload or {})
            decision = dict(enriched.get("decision") or {})
            runtime_facts = dict(decision.get("runtime_facts") or {})
            runtime_facts["producer_platform"] = PRODUCER_PLATFORM
            runtime_facts["producer_service"] = PRODUCER_SERVICE
            runtime_facts["fmz_platform_used"] = False
            runtime_facts["core_source_sha256"] = self.core_source_sha256
            decision["runtime_facts"] = runtime_facts
            enriched["decision"] = decision
            enriched["producer_identity"] = {
                "schema": IDENTITY_SCHEMA,
                "platform": PRODUCER_PLATFORM,
                "producer_id": PRODUCER_ID,
                "service": PRODUCER_SERVICE,
                "instance_id": self.instance_id,
                "core_algorithm_version": self.core_version,
                "core_source_sha256": self.core_source_sha256,
                "host_runtime": "systemd_python",
                "fmz_platform_used": False,
                "contract_compat": "fmz.evaluation_snapshot.v2",
            }
            wrote = bool(orig_maybe_write(enriched, factor_snapshot))
            if wrote and rt.snapshot_exporter.last_success_ms is not None:
                self.last_snapshot_exported_ms = int(rt.snapshot_exporter.last_success_ms)
            return wrote

        rt.snapshot_exporter.maybe_write = _native_maybe_write

        # 3. Enrich signal_review.jsonl cards truthfully + recompute integrity hash
        orig_recorder_write = rt.recorder.write
        review_name = str(self.config.get("signal_review_recorder_name", "signal_review"))

        def _native_recorder_write(name: str, payload: Mapping[str, Any]) -> bool:
            if name == review_name and isinstance(payload, Mapping):
                record = dict(payload)
                prov = dict(record.get("provenance") or {})
                prov["producer_platform"] = PRODUCER_PLATFORM
                prov["producer_service"] = PRODUCER_SERVICE
                prov["fmz_platform_used"] = False
                prov["core_source_sha256"] = self.core_source_sha256
                record["provenance"] = prov

                identity = record.get("identity") or {}
                card_id = identity.get("card_id")
                native_ref = f"server_native:{name} card_id={card_id}"
                delivery = dict(record.get("delivery") or {})
                delivery["delivery_channel"] = "server_native_jsonl"
                delivery["native_log_ref"] = native_ref
                delivery["fmz_log_ref"] = native_ref
                delivery["local_jsonl"] = str(self.logs_dir / f"{name}.jsonl")
                record["delivery"] = delivery

                record.pop("integrity", None)
                record["integrity"] = core._audit_integrity(record)
                return bool(orig_recorder_write(name, record))
            return bool(orig_recorder_write(name, payload))

        rt.recorder.write = _native_recorder_write

        # 4. Deribit pacing & HTTP 429 shared circuit breaker
        orig_deribit_public_get = rt.deribit.public_get
        orig_refresh_greeks = rt._refresh_option_greeks

        def _shared_or_local_cooldown_active(now: int) -> bool:
            if self._deribit_429_until_ms > now:
                return True
            if self.cooldown_file:
                return deribit_cooldown.remaining_ms(self.cooldown_file, now_ms=now) > 0
            return False

        def _native_deribit_public_get(
            method: str, params: dict[str, Any] | None = None, timeout_sec: float | None = None, retries: int | None = None
        ) -> dict[str, Any]:
            now = self._now_ms()
            if self.stop_event.is_set():
                return core.add_schema(
                    {"quality": core.QUALITY_ERROR, "data": None, "error": "shutdown_requested"},
                    core.SCHEMA_HTTP_RESULT,
                    self.config,
                )
            if _shared_or_local_cooldown_active(now):
                return core.add_schema(
                    {"quality": core.QUALITY_ERROR, "data": None, "error": "deribit_429_cooldown_active"},
                    core.SCHEMA_HTTP_RESULT,
                    self.config,
                )
            current_get_json = rt.http.get_json
            orig_func = getattr(self._orig_http_get_json, "__func__", None)
            cur_func = getattr(current_get_json, "__func__", None)
            cur_self = getattr(current_get_json, "__self__", None)
            is_unpatched_http = (
                current_get_json == self._orig_http_get_json
                or (orig_func is not None and cur_func is orig_func and cur_self is rt.http)
            )
            is_live_http = is_unpatched_http or self._force_deribit_pacing
            method_norm = str(method or "").lstrip("/")
            if method_norm.startswith("public/"):
                effective_retries = 0
                if is_live_http and self._deribit_ticker_interval_ms > 0:
                    if self._last_ticker_mono is not None:
                        elapsed_ms = (time.monotonic() - self._last_ticker_mono) * 1000.0
                        wait_ms = self._deribit_ticker_interval_ms - elapsed_ms
                        if wait_ms > 0:
                            self._sleep_interruptible(wait_ms)
                            if self.stop_event.is_set():
                                return core.add_schema(
                                    {"quality": core.QUALITY_ERROR, "data": None, "error": "shutdown_requested"},
                                    core.SCHEMA_HTTP_RESULT,
                                    self.config,
                                )
                    self._last_ticker_mono = time.monotonic()
            else:
                effective_retries = retries

            try:
                result = orig_deribit_public_get(method, params=params, timeout_sec=timeout_sec, retries=effective_retries)
            except Exception as exc:
                msg = str(exc).lower()
                if any(tok in msg for tok in ("429", "too_many_requests", "too many requests", "10028")):
                    self._trip_deribit_429(self._now_ms())
                raise

            if _is_rate_limit_response(result):
                self._trip_deribit_429(self._now_ms())
            return result

        def _native_refresh_option_greeks() -> None:
            now = self._now_ms()
            if _shared_or_local_cooldown_active(now):
                return
            orig_refresh_greeks()

        rt.deribit.public_get = _native_deribit_public_get
        rt._refresh_option_greeks = _native_refresh_option_greeks

    def _trip_deribit_429(self, now_ms: int) -> None:
        self._deribit_429_count += 1
        until_ms = now_ms + self._deribit_429_cooldown_ms
        self._deribit_429_until_ms = max(self._deribit_429_until_ms, until_ms)
        if self.cooldown_file:
            deribit_cooldown.record_cooldown(
                self.cooldown_file,
                cooldown_until_ms=until_ms,
                source=PRODUCER_SERVICE,
                reason="DERIBIT_HTTP_429",
                now_ms=now_ms,
            )

    def save_checkpoint(self, *, now_ms: int | None = None) -> Path:
        """Persist rolling in-memory runtime state atomically for restart recovery."""
        rt = self.runtime
        now = self._now_ms() if now_ms is None else int(now_ms)
        payload = {
            "schema": CHECKPOINT_SCHEMA,
            "saved_at_ms": now,
            "core_version": self.core_version,
            "core_source_sha256": self.core_source_sha256,
            "rr_hist": list(rt.rr_hist),
            "last_edb_rr_success_ms": rt.last_edb_rr_success_ms,
            "cvd_hist": {
                "4h": list(rt.cvd_hist["4h"]),
                "12h": list(rt.cvd_hist["12h"]),
            },
            "neutral_repair_context": deepcopy(rt.neutral_repair_tracker.context),
            "gex_state": {
                "effective": deepcopy(rt.gex_state.effective),
                "raw": deepcopy(rt.gex_state.raw),
                "pending": deepcopy(rt.gex_state.pending),
                "last_acceptance_event": rt.gex_state.last_acceptance_event,
                "last_effective_accept_ms": rt.gex_state.last_effective_accept_ms,
                "last_raw_ingest_ms": rt.gex_state.last_raw_ingest_ms,
                "prev_band_half": rt.gex_state.prev_band_half,
            },
            "signal_events": {
                "seen_episode_ids": sorted(str(x) for x in rt.signal_events.seen_episode_ids),
                "seen_fixed_round_slots": sorted(str(x) for x in rt.signal_events.seen_fixed_round_slots),
                "events": deepcopy(list(rt.signal_events.events[: rt.signal_events.max_events])),
            },
            "pending_signal_reviews": deepcopy(list(rt.pending_signal_reviews)),
            "bars_tail": {
                "completed_bars": deepcopy(list(rt.bars.completed_bars)),
                "last_trade_id": rt.bars.last_trade_id,
                "bar_index": rt.bars.bar_index,
                "current_open": rt.bars.current_open,
                "current_high": rt.bars.current_high,
                "current_low": rt.bars.current_low,
                "current_close": rt.bars.current_close,
                "current_volume": rt.bars.current_volume,
                "current_cvd": rt.bars.current_cvd,
                "current_last_trade_ts_ms": rt.bars.current_last_trade_ts_ms,
                "last_trade_price": rt.bars.last_trade_price,
                "last_trade_ts_ms": rt.bars.last_trade_ts_ms,
                "cvd_degraded": rt.bars.cvd_degraded,
                "anchor_nd_window": list(rt.anchor_nd_window),
                "last_anchor_bar_index": rt.last_anchor_bar_index,
                "last_edb_cvd_bar_index": rt.last_edb_cvd_bar_index,
            },
        }
        _atomic_write_json(self.checkpoint_path, payload, mode=0o640)
        self.checkpoint_saved_ms = now
        return self.checkpoint_path

    def restore_checkpoint(self, *, now_ms: int | None = None) -> dict[str, Any]:
        """Restore checkpoint state honestly without fabricating stale freshness."""
        path = self.checkpoint_path
        if not path.is_file():
            return {"restored": False, "reason": "checkpoint_missing"}
        now = self._now_ms() if now_ms is None else int(now_ms)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {"restored": False, "reason": "checkpoint_corrupt"}
        if not isinstance(data, dict) or data.get("schema") != CHECKPOINT_SCHEMA:
            return {"restored": False, "reason": "checkpoint_schema_mismatch"}
        saved_at = data.get("saved_at_ms")
        if isinstance(saved_at, bool) or not isinstance(saved_at, int) or saved_at > now:
            return {"restored": False, "reason": "checkpoint_clock_invalid"}

        age_ms = now - saved_at
        max_age_ms = int(self.config["native_checkpoint_max_age_ms"])
        bars_max_age_ms = int(self.config["native_bars_restore_max_age_ms"])
        rt = self.runtime

        # Always restore idempotent signal dedup sets so restarts never duplicate cards.
        sig = data.get("signal_events") or {}
        if isinstance(sig, Mapping):
            rt.signal_events.seen_episode_ids.update(
                str(x) for x in (sig.get("seen_episode_ids") or []) if x
            )
            rt.signal_events.seen_fixed_round_slots.update(
                str(x) for x in (sig.get("seen_fixed_round_slots") or []) if x
            )
            if age_ms <= max_age_ms and isinstance(sig.get("events"), list):
                rt.signal_events.events = [
                    dict(item) for item in sig["events"][: rt.signal_events.max_events] if isinstance(item, Mapping)
                ]

        if isinstance(data.get("pending_signal_reviews"), list) and age_ms <= max_age_ms:
            rt.pending_signal_reviews = [
                dict(item) for item in data["pending_signal_reviews"] if isinstance(item, Mapping)
            ]

        if age_ms > max_age_ms:
            return {"restored": False, "reason": "checkpoint_expired", "dedup_restored": True, "age_ms": age_ms}

        # Restore rolling baseline histories
        rr_items = data.get("rr_hist")
        if isinstance(rr_items, list):
            rt.rr_hist = deque(
                (float(x) for x in rr_items if isinstance(x, (int, float)) and not isinstance(x, bool)),
                maxlen=rt.rr_hist.maxlen,
            )
        if isinstance(data.get("last_edb_rr_success_ms"), int) and not isinstance(data.get("last_edb_rr_success_ms"), bool):
            rt.last_edb_rr_success_ms = data["last_edb_rr_success_ms"]

        cvd = data.get("cvd_hist")
        if isinstance(cvd, Mapping):
            for role in ("4h", "12h"):
                vals = cvd.get(role)
                if isinstance(vals, list):
                    rt.cvd_hist[role] = deque(
                        (float(x) for x in vals if isinstance(x, (int, float)) and not isinstance(x, bool)),
                        maxlen=rt.cvd_hist[role].maxlen,
                    )

        # Restore Neutral Repair state machine only if within NR context TTL
        nr_ctx = data.get("neutral_repair_context")
        if isinstance(nr_ctx, Mapping):
            ttl_min = float(self.config.get("nr_repair_context_ttl_min", 360))
            start_ms = nr_ctx.get("event_start_ms") or nr_ctx.get("first_event_ms")
            if isinstance(start_ms, (int, float)) and not isinstance(start_ms, bool):
                if (now - float(start_ms)) / 60000.0 <= ttl_min:
                    rt.neutral_repair_tracker.context = deepcopy(dict(nr_ctx))

        # Restore GEX effective state keeping original source_ts_ms intact (so stale GEX still ages out!)
        gex_saved = data.get("gex_state")
        if isinstance(gex_saved, Mapping):
            if isinstance(gex_saved.get("effective"), Mapping):
                rt.gex_state.effective = deepcopy(dict(gex_saved["effective"]))
            if isinstance(gex_saved.get("raw"), Mapping):
                rt.gex_state.raw = deepcopy(dict(gex_saved["raw"]))
            if isinstance(gex_saved.get("pending"), Mapping):
                rt.gex_state.pending = deepcopy(dict(gex_saved["pending"]))
            rt.gex_state.last_acceptance_event = gex_saved.get("last_acceptance_event")
            rt.gex_state.last_effective_accept_ms = gex_saved.get("last_effective_accept_ms")
            rt.gex_state.last_raw_ingest_ms = gex_saved.get("last_raw_ingest_ms")
            if isinstance(gex_saved.get("prev_band_half"), (int, float)) and not isinstance(
                gex_saved.get("prev_band_half"), bool
            ):
                rt.gex_state.prev_band_half = float(gex_saved["prev_band_half"])

        # Restore volume bars ONLY across short restarts; longer outages fall back to 1m kline backfill
        bars_restored = False
        bars_tail = data.get("bars_tail")
        if age_ms <= bars_max_age_ms and isinstance(bars_tail, Mapping):
            completed = bars_tail.get("completed_bars")
            has_partial = bars_tail.get("current_open") is not None
            if isinstance(completed, list) and (completed or has_partial):
                rt.bars.completed_bars = deque(
                    (dict(b) for b in completed if isinstance(b, Mapping)),
                    maxlen=rt.bars.completed_bars.maxlen,
                )
                rt.bars.last_trade_id = bars_tail.get("last_trade_id")
                rt.bars.bar_index = int(bars_tail.get("bar_index") or len(rt.bars.completed_bars))
                rt.bars.current_open = bars_tail.get("current_open")
                rt.bars.current_high = bars_tail.get("current_high")
                rt.bars.current_low = bars_tail.get("current_low")
                rt.bars.current_close = bars_tail.get("current_close")
                rt.bars.current_volume = float(bars_tail.get("current_volume") or 0.0)
                rt.bars.current_cvd = float(bars_tail.get("current_cvd") or 0.0)
                rt.bars.current_last_trade_ts_ms = bars_tail.get("current_last_trade_ts_ms")
                rt.bars.last_trade_price = bars_tail.get("last_trade_price")
                rt.bars.last_trade_ts_ms = bars_tail.get("last_trade_ts_ms")
                rt.bars.cvd_degraded = bool(bars_tail.get("cvd_degraded", False))
                nd_vals = bars_tail.get("anchor_nd_window")
                if isinstance(nd_vals, list):
                    rt.anchor_nd_window = deque(
                        (float(x) for x in nd_vals if isinstance(x, (int, float)) and not isinstance(x, bool)),
                        maxlen=rt.anchor_nd_window.maxlen,
                    )
                rt.last_anchor_bar_index = bars_tail.get("last_anchor_bar_index")
                rt.last_edb_cvd_bar_index = bars_tail.get("last_edb_cvd_bar_index")
                bars_restored = True
                self._pending_bars_restore_catchup_check = True

        return {
            "restored": True,
            "age_ms": age_ms,
            "bars_restored": bars_restored,
            "rr_hist_len": len(rt.rr_hist),
            "cvd_hist_4h_len": len(rt.cvd_hist["4h"]),
            "nr_context_restored": rt.neutral_repair_tracker.context is not None,
            "gex_effective_restored": rt.gex_state.effective is not None,
        }

    def write_health(self, *, state: str, decision_snapshot: Mapping[str, Any] | None = None) -> Path:
        """Persist current runtime health atomically."""
        now = self._now_ms()
        rt = self.runtime
        snap = decision_snapshot if isinstance(decision_snapshot, Mapping) else {}
        factors = snap.get("factor_snapshot") if isinstance(snap.get("factor_snapshot"), Mapping) else {}
        sg = factors.get("space_gate") if isinstance(factors.get("space_gate"), Mapping) else {}
        rt_sg_state = getattr(rt.space_gate, "state", None)
        space_gate_state = sg.get("state") or (
            rt_sg_state.get("state")
            if isinstance(rt_sg_state, Mapping)
            else (rt_sg_state if isinstance(rt_sg_state, str) else None)
        )
        payload = {
            "schema": HEALTH_SCHEMA,
            "pid": os.getpid(),
            "started_at_ms": self.started_at_ms,
            "updated_at_ms": now,
            "loop_count": self.loop_count,
            "state": state,
            "core_version": self.core_version,
            "core_source_sha256": self.core_source_sha256,
            "producer_id": PRODUCER_ID,
            "producer_platform": PRODUCER_PLATFORM,
            "producer_service": PRODUCER_SERVICE,
            "instance_id": self.instance_id,
            "fmz_platform_used": False,
            "read_only_demo": bool(self.config.get("read_only_demo", True)),
            "last_tick_ok": rt.last_tick_error is None,
            "last_tick_error": rt.last_tick_error,
            "last_snapshot_exported_ms": self.last_snapshot_exported_ms,
            "last_price": rt.current_price,
            "last_price_source": rt.current_price_source,
            "source_quality": dict(rt.last_source_quality or {}),
            "space_gate_state": space_gate_state,
            "deribit_429_count": self._deribit_429_count,
            "deribit_429_cooldown_active": (
                self._deribit_429_until_ms > now
                or (bool(self.cooldown_file) and deribit_cooldown.remaining_ms(self.cooldown_file, now_ms=now) > 0)
            ),
            "checkpoint_restored": dict(self.checkpoint_restored),
            "checkpoint_saved_ms": self.checkpoint_saved_ms,
        }
        _atomic_write_json(self.health_path, payload, mode=0o644)
        return self.health_path

    def tick(self, *, live_fetch: bool | None = None) -> tuple[dict[str, Any], list[Any]]:
        """Execute one signal evaluation tick and flush checkpoint + health."""
        self.loop_count += 1
        decision_snapshot, module_results = self.runtime.tick(live_fetch=live_fetch)
        self.save_checkpoint()
        self.write_health(state="running", decision_snapshot=decision_snapshot)
        return decision_snapshot, module_results

    def run(
        self,
        *,
        live_fetch: bool | None = None,
        max_loops: int | None = None,
        install_signals: bool = False,
    ) -> int:
        """Run the signal producer loop until ``max_loops`` or shutdown signal."""
        if install_signals:
            self._install_signal_handlers()

        loops = int(self.config.get("max_main_loops", 0)) if max_loops is None else int(max_loops)
        self.core_mod.fmz_sleep = self._sleep_interruptible
        _sd_notify("READY=1\nSTATUS=astra-signal-producer running")

        try:
            while not self.stop_event.is_set():
                decision_snapshot: Mapping[str, Any] | None = None
                try:
                    decision_snapshot, _modules = self.tick(live_fetch=live_fetch)
                    _sd_notify("WATCHDOG=1")
                except Exception as exc:
                    self.runtime.last_tick_error = str(exc)
                    self.core_mod.fmz_log(f"循环异常 轮次={self.loop_count}: {exc}")
                    try:
                        self.write_health(state="degraded")
                    except OSError:
                        pass
                    if not self.config.get("continue_on_tick_error", True):
                        raise
                if loops > 0 and self.loop_count >= loops:
                    break
                if self.stop_event.is_set():
                    break
                sleep_ms = int(self.config.get("loop_sleep_ms", 60_000))
                if self.runtime.last_tick_error:
                    sleep_ms = int(self.config.get("error_sleep_ms", sleep_ms))
                self._sleep_interruptible(sleep_ms)
        finally:
            _sd_notify("STOPPING=1")
            try:
                self.save_checkpoint()
            except Exception:
                pass
            try:
                self.write_health(state="stopped")
            except Exception:
                pass
        return 0

    def _install_signal_handlers(self) -> None:
        def _handle(signum: int, _frame: Any) -> None:
            self.core_mod.fmz_log(f"收到退出信号 signal={signum}，正在安全保存检查点并退出")
            self.stop_event.set()

        for sig_name in ("SIGTERM", "SIGINT"):
            sig = getattr(signal, sig_name, None)
            if sig is not None:
                signal.signal(sig, _handle)


def check_health_file(state_dir: Path, *, max_age_ms: int = 180_000, now_ms: int | None = None) -> dict[str, Any]:
    """Validate producer_health.json freshness and invariants for CLI --health-check."""
    health_path = Path(state_dir) / "producer_health.json"
    if not health_path.is_file():
        return {"ok": False, "reason": "health_file_missing", "path": str(health_path)}
    try:
        data = json.loads(health_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": f"health_file_invalid:{type(exc).__name__}", "path": str(health_path)}
    if not isinstance(data, dict) or data.get("schema") != HEALTH_SCHEMA:
        return {"ok": False, "reason": "health_schema_mismatch", "path": str(health_path)}
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    updated = data.get("updated_at_ms")
    if isinstance(updated, bool) or not isinstance(updated, int):
        return {"ok": False, "reason": "health_updated_at_missing"}
    age_ms = now - updated
    ok = (
        0 <= age_ms <= max_age_ms
        and data.get("fmz_platform_used") is False
        and data.get("read_only_demo") is True
        and data.get("state") in {"running", "degraded"}
    )
    return {
        "ok": ok,
        "age_ms": age_ms,
        "state": data.get("state"),
        "core_version": data.get("core_version"),
        "fmz_platform_used": data.get("fmz_platform_used"),
        "read_only_demo": data.get("read_only_demo"),
        "last_snapshot_exported_ms": data.get("last_snapshot_exported_ms"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="xxproject server-native signal producer")
    parser.add_argument(
        "--logs-dir",
        default=os.environ.get("ASTRA_SIGNAL_LOGS_DIR", "/var/lib/xxproject/signal_native/logs"),
        help="Directory for snapshots.jsonl, signal_review.jsonl, and factor caches",
    )
    parser.add_argument(
        "--state-dir",
        default=os.environ.get("ASTRA_SIGNAL_STATE_DIR", "/var/lib/xxproject/signal_native/state"),
        help="Directory for runtime_checkpoint.json and producer_health.json",
    )
    parser.add_argument("--core-path", default=None, help="Explicit path to neutral_regulation_demo_fmz.py")
    parser.add_argument("--max-loops", type=int, default=None, help="Stop after N ticks (0 = run continuously)")
    parser.add_argument("--once", action="store_true", help="Run exactly one tick and exit")
    parser.add_argument("--offline-fixture", action="store_true", help="Run using deterministic built-in fixture")
    parser.add_argument("--loop-sleep-ms", type=int, default=None, help="Override main loop sleep interval (ms)")
    parser.add_argument(
        "--deribit-ticker-interval-ms",
        type=int,
        default=None,
        help="Minimum spacing between Deribit public/ticker requests (ms)",
    )
    parser.add_argument("--gex-info-url", default=None, help="Override gexmonitorapi base URL")
    parser.add_argument("--health-check", action="store_true", help="Inspect producer_health.json and exit")
    args = parser.parse_args(argv)

    if args.health_check:
        report = check_health_file(Path(args.state_dir))
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report.get("ok") else 1

    overrides: dict[str, Any] = {}
    if args.offline_fixture:
        overrides["live_fetch_enabled"] = False
        overrides["offline_fixture_enabled"] = True
    if args.loop_sleep_ms is not None:
        overrides["loop_sleep_ms"] = max(100, int(args.loop_sleep_ms))
    if args.deribit_ticker_interval_ms is not None:
        overrides["native_deribit_ticker_interval_ms"] = max(0, int(args.deribit_ticker_interval_ms))
    if args.gex_info_url:
        overrides["gex_info_base_url"] = args.gex_info_url.strip()

    loops = 1 if args.once else args.max_loops
    runner = NativeSignalRuntime(
        logs_dir=args.logs_dir,
        state_dir=args.state_dir,
        core_path=args.core_path,
        overrides=overrides,
    )
    return runner.run(max_loops=loops, install_signals=True)


if __name__ == "__main__":
    raise SystemExit(main())
