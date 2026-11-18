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
from research.gfri_v11.core import confirm_cycle_observations, evaluate_single_observation
from research.gfri_v11.history import ShadowLedger, compute_long_term_ref, compute_near_term_ref
from research.gfri_v11.policy import build_audit_record, evaluate_shadow_policy, render_minimal_console


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
    """Fetch HTTP JSON payload alongside receive timestamp and SHA-256 of raw bytes."""
    req = urllib.request.Request(url, headers={"User-Agent": "xxproject-gfri-v11-shadow/1.0"})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        raw_bytes = resp.read()
        received_ms = int(time.time() * 1000)
        return {
            "status": getattr(resp, "status", 200),
            "received_at_ms": received_ms,
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


def _load_policy_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _save_raw_cycle_bundle(
    raw_capture_dir: Path,
    *,
    freeze_at_ms: int,
    gex_capture: dict[str, Any] | None,
    deribit_inst: dict[str, Any] | None,
    book_captures: list[dict[str, Any] | None],
    okx_inst: dict[str, Any] | None,
    okx_captures: list[dict[str, Any] | None],
) -> None:
    """Persist raw cycle captures outside Git when GFRI_RAW_CAPTURE_DIR is explicitly configured."""
    try:
        cycle_dir = raw_capture_dir / f"cycle_{freeze_at_ms}"
        cycle_dir.mkdir(parents=True, exist_ok=True)
        if gex_capture and isinstance(gex_capture.get("payload"), dict):
            _atomic_write_json(cycle_dir / "gex_latest.json", gex_capture["payload"])
        if deribit_inst and isinstance(deribit_inst.get("payload"), dict):
            _atomic_write_json(cycle_dir / "deribit_instrument.json", deribit_inst["payload"])
        if okx_inst and isinstance(okx_inst.get("payload"), dict):
            _atomic_write_json(cycle_dir / "okx_instrument.json", okx_inst["payload"])
        for idx, b_cap in enumerate(book_captures):
            if b_cap and isinstance(b_cap.get("payload"), dict):
                _atomic_write_json(cycle_dir / f"deribit_book_{idx + 1}.json", b_cap["payload"])
        for idx, o_cap in enumerate(okx_captures):
            if o_cap and isinstance(o_cap.get("payload"), dict):
                _atomic_write_json(cycle_dir / f"okx_book_{idx + 1}.json", o_cap["payload"])
    except Exception:
        # Raw archive write errors must never block shadow evaluation or crash timer.
        pass


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

    if raw_capture_dir is not None:
        _save_raw_cycle_bundle(
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
        merged_reasons = list(dict.fromkeys([*fetch_errors, *(confirmed.get("reasons") or [])]))
        confirmed["reasons"] = merged_reasons

    history_records: list[dict[str, Any]] = []
    prior_policy_state: dict[str, Any] | None = None
    ledger_path: Path | None = None
    policy_state_path: Path | None = None
    if state_dir is not None:
        ledger_path = state_dir / "gfri_shadow_ledger.jsonl"
        policy_state_path = state_dir / "gfri_policy_state.json"
        history_records = ShadowLedger.load_records(ledger_path)
        prior_policy_state = _load_policy_state(policy_state_path)

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
        git_commit=(git_commit if git_commit is not None else resolve_git_commit()),
        cfg=cfg,
    )
    console_text = render_minimal_console(audit_record)

    if append_ledger and state_dir is not None and ledger_path is not None and policy_state_path is not None:
        ShadowLedger(ledger_path).append_record(audit_record)
        _atomic_write_json(policy_state_path, policy_res["next_policy_state"])

    return {
        "audit_record": audit_record,
        "console_text": console_text,
        "confirmed_cycle": confirmed,
        "observations": observations,
        "freeze_at_ms": freeze_at_ms,
        "fetch_errors": fetch_errors,
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
