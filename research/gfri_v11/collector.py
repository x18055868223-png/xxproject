"""Read-only prospective shadow collector & one-off live evaluation runner for GFRI v1.1.

Scheduled execution is gated behind `collector_enabled` (default False) and
`decision_authority` is locked to `SHADOW_ONLY`. Upstream HTTP/JSON failures fail
closed to an UNKNOWN audit record and exit 0 cleanly without crashing timers.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable
import urllib.request

from research.gfri_v11.config import DEFAULT_CONFIG, GfriConfig, ShadowFlags
from research.gfri_v11.core import (
    check_deribit_instrument_unit,
    confirm_cycle_observations,
    evaluate_single_observation,
)
from research.gfri_v11.history import (
    MS_PER_DAY,
    ShadowLedger,
    compute_long_term_ref,
    compute_near_term_ref,
)
from research.gfri_v11.policy import (
    build_audit_record,
    evaluate_shadow_policy,
    reconstruct_policy_state_from_ledger,
    render_minimal_console,
)


def _env_truthy(val: str | None) -> bool:
    if val is None:
        return False
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw.strip())


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return float(raw.strip())


def resolve_flags_from_env(base: ShadowFlags | None = None) -> ShadowFlags:
    """Build ShadowFlags from environment and assert v1.1 safety locks."""
    flags = base or ShadowFlags()
    collector_enabled = flags.collector_enabled or _env_truthy(os.environ.get("GFRI_COLLECTOR_ENABLED"))
    authority = os.environ.get("GFRI_DECISION_AUTHORITY", flags.decision_authority).strip()
    player_int = flags.player_integration_enabled or _env_truthy(
        os.environ.get("GFRI_PLAYER_INTEGRATION_ENABLED")
    )
    live_veto = flags.live_veto_enabled or _env_truthy(os.environ.get("GFRI_LIVE_VETO_ENABLED"))
    resolved = ShadowFlags(
        collector_enabled=collector_enabled,
        decision_authority=authority,
        player_integration_enabled=player_int,
        live_veto_enabled=live_veto,
    )
    resolved.validate_locked()
    return resolved


def resolve_runtime_from_env(
    base_cfg: GfriConfig = DEFAULT_CONFIG,
) -> tuple[GfriConfig, bool, Path | None, Path | None]:
    """Resolve GfriConfig + runtime flags/dirs from environment variables cleanly."""
    flags = resolve_flags_from_env(base_cfg.flags)
    cfg = replace(
        base_cfg,
        flags=flags,
        confirm_sample_count=max(1, _env_int("GFRI_CONFIRM_SAMPLES", base_cfg.confirm_sample_count)),
        confirm_interval_sec=max(0.0, _env_float("GFRI_CONFIRM_INTERVAL_SEC", base_cfg.confirm_interval_sec)),
        max_ob_skew_ms=max(1_000, _env_int("GFRI_OB_SKEW_MAX_MS", base_cfg.max_ob_skew_ms)),
        max_ob_age_ms=max(1_000, _env_int("GFRI_OB_AGE_MAX_MS", base_cfg.max_ob_age_ms)),
        max_ob_receive_lag_ms=max(1_000, _env_int("GFRI_OB_RECV_LAG_MAX_MS", base_cfg.max_ob_receive_lag_ms)),
        max_source_age_ms=max(1_000, _env_int("GFRI_SOURCE_AGE_MAX_MS", base_cfg.max_source_age_ms)),
    )
    include_okx = (
        _env_truthy(os.environ.get("GFRI_INCLUDE_OKX_REF"))
        if os.environ.get("GFRI_INCLUDE_OKX_REF") is not None
        else True
    )
    state_raw = os.environ.get("GFRI_STATE_DIR")
    raw_dir_env = os.environ.get("GFRI_RAW_CAPTURE_DIR")
    return (
        cfg,
        include_okx,
        Path(state_raw.strip()) if (state_raw and state_raw.strip()) else None,
        Path(raw_dir_env.strip()) if (raw_dir_env and raw_dir_env.strip()) else None,
    )


def fetch_json_with_hash(url: str, *, timeout_sec: float = 12.0) -> dict[str, Any]:
    """Fetch HTTP JSON payload alongside receive timestamp, exact wire bytes, and SHA-256."""
    req = urllib.request.Request(url, headers={"User-Agent": "xxproject-gfri-v11-shadow/1.1"})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        raw_bytes = resp.read()
        received_ms = int(time.time() * 1000)
        return {
            "status": getattr(resp, "status", 200),
            "received_at_ms": received_ms,
            "raw_bytes": raw_bytes,
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "byte_len": len(raw_bytes),
            "payload": json.loads(raw_bytes.decode("utf-8")),
        }


def _safe_fetch(
    url: str,
    *,
    label: str,
    fetcher: Callable[[str], dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    """Fetch JSON safely; never raise to caller, returning structured error label instead."""
    try:
        cap = fetcher(url)
        if not isinstance(cap, dict) or "payload" not in cap:
            return None, f"{label}_fetch_invalid_envelope"
        return cap, None
    except Exception as exc:
        return None, f"{label}_fetch_failed:{type(exc).__name__}"


def resolve_git_commit(repo_root: Path | None = None) -> str | None:
    """Resolve current HEAD short hash cleanly without failing if git is absent."""
    cwd = repo_root or Path(__file__).resolve().parents[2]
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=str(cwd),
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return out.decode("utf-8").strip() or None
    except Exception:
        return None


def _atomic_write_bytes(path: Path, raw_bytes: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = os.open(str(tmp), flags, 0o644)
    try:
        written = 0
        while written < len(raw_bytes):
            written += os.write(fd, raw_bytes[written:])
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _atomic_write_json(path: Path, data: Any) -> None:
    raw = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _atomic_write_bytes(path, raw)


def _load_json_dict(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _load_tail_audit_records(ledger_path: Path, *, max_lines: int = 8) -> list[dict[str, Any]]:
    """Read only the last few valid lines from ledger_path without loading whole history."""
    if not ledger_path.exists():
        return []
    try:
        size = ledger_path.stat().st_size
        read_chunk = min(size, 512 * 1024)  # Last 512 KiB comfortably covers ~20+ full audit lines
        with ledger_path.open("rb") as fh:
            if size > read_chunk:
                fh.seek(size - read_chunk)
            chunk_text = fh.read().decode("utf-8", errors="replace")
        lines = chunk_text.splitlines()
        if size > read_chunk and lines:
            lines = lines[1:]  # Drop potentially partial first line from mid-file seek
        parsed_rows: list[dict[str, Any]] = []
        for raw_line in lines[-max_lines:]:
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    parsed_rows.append(obj)
            except ValueError:
                continue
        return parsed_rows
    except Exception:
        return []


def load_or_recover_policy_state(
    policy_state_path: Path,
    ledger_path: Path,
) -> tuple[dict[str, Any] | None, bool]:
    """Load policy_state_path or reconstruct from latest ledger audit record after crash."""
    saved_state = _load_json_dict(policy_state_path)
    tail_records = _load_tail_audit_records(ledger_path)
    recovered_state = reconstruct_policy_state_from_ledger(tail_records)
    if saved_state is None:
        return recovered_state, recovered_state is not None

    saved_ts = saved_state.get("last_freeze_at_ms")
    rec_ts = recovered_state.get("last_freeze_at_ms") if recovered_state else None
    if isinstance(rec_ts, int) and (not isinstance(saved_ts, int) or rec_ts > saved_ts):
        return recovered_state, True
    return saved_state, False


def _save_verified_deribit_spec_cache(
    cache_path: Path,
    *,
    inst_payload: dict[str, Any],
    verified_at_ms: int,
    cfg: GfriConfig,
) -> str | None:
    ok, unit, _ = check_deribit_instrument_unit(inst_payload)
    if not ok or unit is None:
        return None
    res = inst_payload.get("result") if isinstance(inst_payload.get("result"), dict) else inst_payload
    entry = {
        "schema_version": cfg.deribit_spec_schema_version,
        "instrument_name": res.get("instrument_name"),
        "quote_currency": res.get("quote_currency"),
        "instrument_type": res.get("instrument_type"),
        "contract_size": float(res.get("contract_size", 10.0)),
        "depth_unit": unit,
        "verified_at_ms": verified_at_ms,
        "expires_at_ms": verified_at_ms + cfg.instrument_cache_ttl_ms,
    }
    try:
        _atomic_write_json(cache_path, entry)
        return None
    except Exception as exc:
        return f"deribit_spec_cache_write_failed:{type(exc).__name__}"


def _write_archive_entry(
    target_path: Path,
    cap: dict[str, Any] | None,
    *,
    label: str,
    required: bool,
) -> tuple[dict[str, Any] | None, str | None, bool]:
    """Write raw HTTP bytes atomically and verify disk SHA-256 readback."""
    if not isinstance(cap, dict) or "payload" not in cap:
        if required:
            return None, f"raw_archive_missing_capture:{label}", False
        return None, None, True

    raw_bytes = cap.get("raw_bytes")
    if isinstance(raw_bytes, (bytes, bytearray)):
        bytes_to_write = bytes(raw_bytes)
        bytes_origin = "raw_http_wire"
    else:
        bytes_to_write = (json.dumps(cap["payload"], ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        bytes_origin = "reserialized_envelope"

    raw_http_sha256 = cap.get("sha256") or hashlib.sha256(bytes_to_write).hexdigest()
    reserialized_json_sha256 = hashlib.sha256(
        json.dumps(cap["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    try:
        _atomic_write_bytes(target_path, bytes_to_write)
        disk_bytes = target_path.read_bytes()
        verified_disk_sha256 = hashlib.sha256(disk_bytes).hexdigest()
    except Exception as exc:
        return (
            {
                "path": str(target_path),
                "bytes": 0,
                "raw_http_sha256": raw_http_sha256,
                "reserialized_json_sha256": reserialized_json_sha256,
                "verified_disk_sha256": None,
                "bytes_origin": bytes_origin,
            },
            f"raw_archive_write_failed:{label}:{type(exc).__name__}",
            False,
        )

    if verified_disk_sha256 != hashlib.sha256(bytes_to_write).hexdigest():
        return (
            {
                "path": str(target_path),
                "bytes": len(disk_bytes),
                "raw_http_sha256": raw_http_sha256,
                "reserialized_json_sha256": reserialized_json_sha256,
                "verified_disk_sha256": verified_disk_sha256,
                "bytes_origin": bytes_origin,
            },
            f"raw_archive_checksum_mismatch:{label}",
            False,
        )

    return (
        {
            "path": str(target_path),
            "bytes": len(disk_bytes),
            "raw_http_sha256": raw_http_sha256,
            "reserialized_json_sha256": reserialized_json_sha256,
            "verified_disk_sha256": verified_disk_sha256,
            "bytes_origin": bytes_origin,
        },
        None,
        True,
    )


def _save_raw_cycle_bundle(
    raw_capture_dir: Path | None,
    *,
    freeze_at_ms: int,
    gex_capture: dict[str, Any] | None,
    deribit_inst: dict[str, Any] | None,
    book_captures: list[dict[str, Any] | None],
    okx_inst: dict[str, Any] | None,
    okx_captures: list[dict[str, Any] | None],
) -> dict[str, Any]:
    """Persist exact raw HTTP bytes outside Git and return auditable status."""
    if raw_capture_dir is None:
        return {
            "status": "NOT_CONFIGURED",
            "complete_replayable": False,
            "errors": [],
            "files": {},
            "cycle_dir": None,
        }

    cycle_dir = raw_capture_dir / f"cycle_{freeze_at_ms}"
    errors: list[str] = []
    files: dict[str, Any] = {}
    targets: list[tuple[str, Path, dict[str, Any] | None, bool]] = [
        ("gex_latest", cycle_dir / "gex_latest.json", gex_capture, True),
        ("deribit_instrument", cycle_dir / "deribit_instrument.json", deribit_inst, False),
    ]
    for idx, b_cap in enumerate(book_captures):
        targets.append((f"deribit_book_{idx + 1}", cycle_dir / f"deribit_book_{idx + 1}.json", b_cap, True))
    if okx_inst is not None:
        targets.append(("okx_instrument", cycle_dir / "okx_instrument.json", okx_inst, False))
    for idx, o_cap in enumerate(okx_captures):
        if o_cap is not None:
            targets.append((f"okx_book_{idx + 1}", cycle_dir / f"okx_book_{idx + 1}.json", o_cap, False))

    written_ok = 0
    for key, fpath, cap, required in targets:
        meta, err, ok = _write_archive_entry(fpath, cap, label=key, required=required)
        if meta is not None:
            files[key] = meta
        if err is not None:
            errors.append(err)
        elif ok and meta is not None:
            written_ok += 1

    if errors:
        status = "PARTIAL" if written_ok > 0 else "FAILED"
        complete = False
    else:
        status = "SAVED"
        complete = (
            "gex_latest" in files
            and any(k.startswith("deribit_book_") for k in files)
        )

    return {
        "status": status,
        "complete_replayable": complete,
        "errors": errors,
        "files": files,
        "cycle_dir": str(cycle_dir),
    }


def _write_cycle_manifest(archive_status: dict[str, Any], audit_record: dict[str, Any]) -> None:
    cycle_dir_raw = archive_status.get("cycle_dir")
    if not cycle_dir_raw or archive_status.get("status") == "FAILED":
        return
    manifest_path = Path(cycle_dir_raw) / "manifest.json"
    manifest = {
        "schema": "gfri_raw_bundle_manifest_v11",
        "freeze_at_ms": audit_record.get("freeze_at_ms"),
        "cycle_asof_ms": audit_record.get("cycle_asof_ms"),
        "archive_status": archive_status,
        "computed_summary": {
            "data_quality": audit_record.get("data_quality"),
            "global_data_quality": audit_record.get("global_data_quality"),
            "shadow_decision": audit_record.get("shadow_decision"),
            "put_gfri_confirmed": (audit_record.get("put") or {}).get("gfri_confirmed"),
            "call_gfri_confirmed": (audit_record.get("call") or {}).get("gfri_confirmed"),
            "put_liquidity_depth_usd": (audit_record.get("put") or {}).get("liquidity_depth_usd"),
            "call_liquidity_depth_usd": (audit_record.get("call") or {}).get("liquidity_depth_usd"),
        },
    }
    try:
        _atomic_write_json(manifest_path, manifest)
    except Exception as exc:
        err = f"raw_archive_manifest_failed:{type(exc).__name__}"
        archive_status["errors"].append(err)
        archive_status["status"] = "PARTIAL" if archive_status.get("files") else "FAILED"
        archive_status["complete_replayable"] = False
        if isinstance(audit_record.get("raw_archive_status"), dict):
            audit_record["raw_archive_status"]["errors"] = list(archive_status["errors"])
            audit_record["raw_archive_status"]["status"] = archive_status["status"]
            audit_record["raw_archive_status"]["complete_replayable"] = False
        audit_record["raw_archive_status_label"] = archive_status["status"]
        audit_record["sample_replay_class"] = "NON_REPLAYABLE"
        if err not in audit_record["failure_reasons"]:
            audit_record["failure_reasons"].append(err)


def run_shadow_cycle(
    *,
    cfg: GfriConfig = DEFAULT_CONFIG,
    state_dir: Path | None = None,
    raw_capture_dir: Path | None = None,
    append_ledger: bool = False,
    include_okx: bool = True,
    interval_sec: float | None = None,
    fetcher: Callable[[str], dict[str, Any]] = fetch_json_with_hash,
    sleeper: Callable[[float], None] = time.sleep,
    git_commit: str | None = None,
) -> dict[str, Any]:
    """Execute one pre-freeze 3-sample capture and full G1->G5 evaluation chain."""
    cfg.flags.validate_locked()
    sleep_s = float(cfg.confirm_interval_sec if interval_sec is None else interval_sec)
    fetch_errors: list[str] = []

    gex_capture, gex_err = _safe_fetch(cfg.gex_latest_url, label="gex_latest", fetcher=fetcher)
    if gex_err:
        fetch_errors.append(gex_err)

    deribit_inst, inst_err = _safe_fetch(cfg.deribit_instrument_url, label="deribit_instrument", fetcher=fetcher)
    if inst_err:
        fetch_errors.append(inst_err)

    spec_cache_path = (state_dir / "deribit_spec_cache.json") if state_dir is not None else None
    verified_spec_cache: dict[str, Any] | None = None
    if deribit_inst is not None and spec_cache_path is not None:
        cache_err = _save_verified_deribit_spec_cache(
            spec_cache_path,
            inst_payload=deribit_inst["payload"],
            verified_at_ms=int(deribit_inst.get("received_at_ms") or time.time() * 1000),
            cfg=cfg,
        )
        if cache_err:
            fetch_errors.append(cache_err)
    elif deribit_inst is None and spec_cache_path is not None:
        verified_spec_cache = _load_json_dict(spec_cache_path)

    okx_inst: dict[str, Any] | None = None
    if include_okx:
        okx_inst, _ = _safe_fetch(cfg.okx_instrument_url, label="okx_instrument", fetcher=fetcher)

    book_captures: list[dict[str, Any] | None] = []
    okx_captures: list[dict[str, Any] | None] = []
    for idx in range(cfg.confirm_sample_count):
        if idx > 0 and sleep_s > 0.0:
            sleeper(sleep_s)
        b_cap, b_err = _safe_fetch(cfg.deribit_book_url, label=f"deribit_book_s{idx + 1}", fetcher=fetcher)
        if b_err:
            fetch_errors.append(b_err)
        book_captures.append(b_cap)

        if include_okx and okx_inst is not None:
            o_cap, _ = _safe_fetch(cfg.okx_book_url, label=f"okx_book_s{idx + 1}", fetcher=fetcher)
            okx_captures.append(o_cap)
        else:
            okx_captures.append(None)

    all_rec_ts = [
        int(cap["received_at_ms"])
        for cap in [gex_capture, deribit_inst, *book_captures, *(c for c in okx_captures if c is not None)]
        if isinstance(cap, dict) and isinstance(cap.get("received_at_ms"), (int, float))
    ]
    freeze_at_ms = (max(all_rec_ts) + 50) if all_rec_ts else int(time.time() * 1000)

    archive_status = _save_raw_cycle_bundle(
        raw_capture_dir,
        freeze_at_ms=freeze_at_ms,
        gex_capture=gex_capture,
        deribit_inst=deribit_inst,
        book_captures=book_captures,
        okx_inst=okx_inst,
        okx_captures=okx_captures,
    )

    observations: list[dict[str, Any]] = []
    for idx, b_cap in enumerate(book_captures):
        okx_cap = okx_captures[idx]
        obs = evaluate_single_observation(
            gex_capture["payload"] if gex_capture else None,
            b_cap["payload"] if b_cap else None,
            freeze_at_ms=freeze_at_ms,
            gex_received_at_ms=(gex_capture["received_at_ms"] if gex_capture else None),
            ob_received_at_ms=(b_cap["received_at_ms"] if b_cap else None),
            gex_sha256=(gex_capture["sha256"] if gex_capture else None),
            ob_sha256=(b_cap["sha256"] if b_cap else None),
            deribit_instrument=(deribit_inst["payload"] if deribit_inst else None),
            verified_spec_cache=verified_spec_cache,
            okx_book=(okx_cap["payload"] if okx_cap else None),
            okx_instrument=(okx_inst["payload"] if okx_inst else None),
            okx_sha256=(okx_cap["sha256"] if okx_cap else None),
            depth_unit="usd",
            requested_depth=cfg.requested_deribit_depth,
            cfg=cfg,
        )
        observations.append(obs)

    confirmed = confirm_cycle_observations(observations, freeze_at_ms=freeze_at_ms, cfg=cfg)
    if fetch_errors:
        confirmed["reasons"] = list(dict.fromkeys([*fetch_errors, *(confirmed.get("reasons") or [])]))

    history_records: list[dict[str, Any]] = []
    prior_policy_state: dict[str, Any] | None = None
    recovered_from_ledger = False
    ledger_path: Path | None = None
    policy_state_path: Path | None = None
    if state_dir is not None:
        ledger_path = state_dir / "gfri_shadow_ledger.jsonl"
        policy_state_path = state_dir / "gfri_policy_state.json"
        ledger = ShadowLedger(ledger_path, stale_lock_timeout_ms=cfg.stale_lock_timeout_ms)
        history_records = ledger.load_history_window(
            reference_ms=freeze_at_ms,
            max_age_ms=cfg.long_window_days * MS_PER_DAY,
        )
        prior_policy_state, recovered_from_ledger = load_or_recover_policy_state(
            policy_state_path,
            ledger_path,
        )

    near_ref = compute_near_term_ref(history_records, confirmed, cfg=cfg)
    long_ref = compute_long_term_ref(history_records, confirmed, cfg=cfg)
    policy_res = evaluate_shadow_policy(
        confirmed,
        near_ref,
        long_ref,
        prior_state=prior_policy_state,
        cfg=cfg,
    )
    audit_record = build_audit_record(
        confirmed,
        near_ref,
        long_ref,
        policy_res,
        raw_archive_status=archive_status,
        git_commit=(git_commit if git_commit is not None else resolve_git_commit()),
        cfg=cfg,
    )
    _write_cycle_manifest(archive_status, audit_record)
    console_text = render_minimal_console(audit_record)

    if append_ledger and state_dir is not None and ledger_path is not None and policy_state_path is not None:
        ShadowLedger(ledger_path, stale_lock_timeout_ms=cfg.stale_lock_timeout_ms).append_record(audit_record)
        _atomic_write_json(policy_state_path, policy_res["next_policy_state"])

    return {
        "audit_record": audit_record,
        "console_text": console_text,
        "confirmed_cycle": confirmed,
        "observations": observations,
        "freeze_at_ms": freeze_at_ms,
        "fetch_errors": fetch_errors,
        "raw_archive_status": archive_status,
        "recovered_policy_state": recovered_from_ledger,
    }


def sanitize_audit_for_repo(audit_record: dict[str, Any]) -> dict[str, Any]:
    """Return a clean sanitized copy suitable for committed validation samples."""
    clean = json.loads(json.dumps(audit_record))
    clean["sanitized"] = True
    clean["sanitization_note"] = "Derived purely from public market feeds and computed audit fields; no credentials or secrets."
    return clean


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GFRI v1.1 Shadow Collector & Dry-Run CLI")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--scheduled",
        action="store_true",
        help="Scheduled daemon invocation; exits cleanly unless GFRI_COLLECTOR_ENABLED=1",
    )
    group.add_argument(
        "--once",
        action="store_true",
        help="Run a single live validation dry-run cycle and exit",
    )
    parser.add_argument("--state-dir", type=Path, default=None, help="Optional isolated directory for ledger/state")
    parser.add_argument("--raw-capture-dir", type=Path, default=None, help="Optional external dir for raw cycle captures")
    parser.add_argument("--append-ledger", action="store_true", help="Append audit record to --state-dir ledger")
    parser.add_argument("--interval-sec", type=float, default=None, help="Override spacing between confirmation samples")
    parser.add_argument("--no-okx", action="store_true", help="Skip optional OKX secondary reference")
    parser.add_argument("--save-sanitized-audit", type=Path, default=None, help="Write sanitized audit JSON")
    args = parser.parse_args(argv)

    cfg, env_include_okx, env_state_dir, env_raw_dir = resolve_runtime_from_env(DEFAULT_CONFIG)
    state_dir = args.state_dir if args.state_dir is not None else env_state_dir
    raw_dir = args.raw_capture_dir if args.raw_capture_dir is not None else env_raw_dir
    include_okx = False if args.no_okx else env_include_okx

    if args.scheduled:
        if not cfg.flags.collector_enabled:
            print("COLLECTOR_DISABLED (GFRI_COLLECTOR_ENABLED=0; SHADOW_ONLY)")
            return 0
        if state_dir is None:
            print("ERROR: scheduled run requires --state-dir or GFRI_STATE_DIR", file=sys.stderr)
            return 2

    res = run_shadow_cycle(
        cfg=cfg,
        state_dir=state_dir,
        raw_capture_dir=raw_dir,
        append_ledger=bool(args.append_ledger or (args.scheduled and state_dir is not None)),
        include_okx=include_okx,
        interval_sec=args.interval_sec,
    )
    print(res["console_text"])
    if args.save_sanitized_audit is not None:
        _atomic_write_json(args.save_sanitized_audit, sanitize_audit_for_repo(res["audit_record"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
