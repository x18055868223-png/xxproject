"""Read-only unified health verification for the server-native xxproject stack.

Never mutates runtime state, never triggers LLM or trading calls, and never
prints credentials or tokens.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable, Mapping
import urllib.error
import urllib.request

if __package__:
    from . import astra_evidence_qual_v1 as evidence_qual
    from . import astra_underwriting_fact_context_v20 as fact_context
else:
    import astra_evidence_qual_v1 as evidence_qual
    import astra_underwriting_fact_context_v20 as fact_context

SCHEMA = "xxproject_server_health@1.0.0"
PRODUCER_HEALTH_SCHEMA = "astra_signal_producer_health@1.0.0"
MAP_SCHEMA = "astra_map_data_server@2.4.2"
RELEASE_SCHEMA = "xxproject_server_release@1.0.0"


def _http_get_json(url: str, *, headers: Mapping[str, str] | None = None, timeout_s: float = 5.0) -> tuple[int, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json", **dict(headers or {})}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        status = getattr(resp, "status", 200)
        return int(status), json.loads(resp.read().decode("utf-8"))


def _read_env_key(env_path: Path | None, key: str) -> str:
    if env_path and env_path.is_file():
        try:
            prefix = f"{key}="
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith(prefix):
                    return line.split("=", 1)[1].strip()
        except OSError:
            pass
    return ""


def _read_workbench_token(env_path: Path | None) -> str:
    direct = (os.environ.get("ASTRA_WORKBENCH_TOKEN") or "").strip()
    return direct or _read_env_key(env_path, "ASTRA_WORKBENCH_TOKEN")


def _read_gex_token(env_path: Path | None) -> str:
    val = _read_env_key(env_path, "API_TOKEN")
    if not val or val.startswith("REPLACE_WITH_"):
        return ""
    return val


def _read_json_file(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"invalid_json:{type(exc).__name__}"
    if not isinstance(data, dict):
        return None, "not_object"
    return data, None


def _check_signal_producer(
    logs_dir: Path,
    state_dir: Path,
    *,
    now_ms: int,
    max_producer_age_ms: int,
    max_snapshot_age_ms: int,
    expected_core_version: str | None,
) -> dict[str, Any]:
    health_path = state_dir / "producer_health.json"
    health, err = _read_json_file(health_path)
    if health is None:
        return {"ok": False, "reason": f"producer_health_{err}", "path": str(health_path)}
    if health.get("schema") != PRODUCER_HEALTH_SCHEMA:
        return {"ok": False, "reason": "producer_health_schema_mismatch"}
    updated_at = health.get("updated_at_ms")
    if isinstance(updated_at, bool) or not isinstance(updated_at, int):
        return {"ok": False, "reason": "producer_health_timestamp_missing"}
    health_age = now_ms - updated_at
    if not 0 <= health_age <= max_producer_age_ms:
        return {"ok": False, "reason": "producer_health_stale", "age_ms": health_age}
    if health.get("fmz_platform_used") is not False:
        return {"ok": False, "reason": "fmz_platform_flag_must_be_false"}
    if health.get("read_only_demo") is not True:
        return {"ok": False, "reason": "read_only_demo_must_be_true"}
    if health.get("state") != "running":
        return {"ok": False, "reason": f"producer_not_running:{health.get('state')}"}
    if expected_core_version and health.get("core_version") != expected_core_version:
        return {
            "ok": False,
            "reason": "core_version_mismatch",
            "actual": health.get("core_version"),
            "expected": expected_core_version,
        }

    snapshots_path = logs_dir / "snapshots.jsonl"
    ctx = fact_context.latest_from_jsonl(snapshots_path, now_ms, max_age_ms=max_snapshot_age_ms)
    if ctx.get("status") != "current":
        return {
            "ok": False,
            "reason": "snapshot_not_current",
            "status": ctx.get("status"),
            "gap_reasons": ctx.get("gap_reasons") or [],
        }

    review_path = logs_dir / "signal_review.jsonl"
    review_ok = True
    review_reason = "absent_or_empty"
    if review_path.is_file() and review_path.stat().st_size > 0:
        lines = [ln for ln in review_path.read_bytes().splitlines() if ln.strip()]
        if not lines:
            review_reason = "empty"
        else:
            try:
                last_card = json.loads(lines[-1].decode("utf-8"))
                card_id = (last_card.get("identity") or {}).get("card_id")
                schema_name = (last_card.get("schema") or {}).get("name")
                if not card_id or schema_name != "signal_review_card":
                    review_ok = False
                    review_reason = "invalid_signal_review_card"
                else:
                    review_reason = "ok"
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                review_ok = False
                review_reason = "corrupt_signal_review_tail"
    if not review_ok:
        return {"ok": False, "reason": review_reason}

    return {
        "ok": True,
        "producer_age_ms": health_age,
        "snapshot_age_ms": ctx.get("age_ms"),
        "core_version": health.get("core_version"),
        "runtime_mode": ctx.get("runtime_mode"),
        "signal_review": review_reason,
    }


def _check_gex_api(
    gex_url: str, *, token: str = "", http_get: Callable[..., tuple[int, Any]]
) -> dict[str, Any]:
    base = gex_url.rstrip("/")
    info_headers = {"Authorization": f"Bearer {token}"} if token else None
    try:
        st_h, health = http_get(f"{base}/health", timeout_s=5.0)
        if info_headers:
            st_i, info = http_get(f"{base}/v1/info", headers=info_headers, timeout_s=8.0)
        else:
            st_i, info = http_get(f"{base}/v1/info", timeout_s=8.0)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "reason": f"gexmonitorapi_unreachable:{type(exc).__name__}"}
    if st_h != 200 or not isinstance(health, dict) or health.get("status") != "ok":
        return {"ok": False, "reason": "gexmonitorapi_health_not_ok", "status": st_h}
    if st_i != 200 or not isinstance(info, dict):
        return {"ok": False, "reason": "gexmonitorapi_info_invalid", "status": st_i}
    if info.get("availability") != "ready" or info.get("stale") is not False:
        return {
            "ok": False,
            "reason": "gexmonitorapi_not_ready_or_stale",
            "availability": info.get("availability"),
            "stale": info.get("stale"),
        }
    return {"ok": True, "availability": info.get("availability"), "data_age_ms": info.get("data_age_ms")}


def _evaluate_workbench_evidence(view: Mapping[str, Any], *, now_ms: int | None = None) -> dict[str, Any]:
    mc = view.get("market_context") if isinstance(view.get("market_context"), Mapping) else {}
    facts = mc.get("market_facts") if isinstance(mc.get("market_facts"), Mapping) else {}
    vp = facts.get("volume_price") if isinstance(facts.get("volume_price"), Mapping) else {}
    flow = vp.get("micro_flow") if isinstance(vp.get("micro_flow"), Mapping) else {}
    fast_4h = flow.get("fast_4h") if isinstance(flow.get("fast_4h"), Mapping) else None

    cutoff_ms: int | None = None
    for cand_clock in (view.get("served_at_ms"), now_ms, mc.get("snapshot_ts_ms")):
        if evidence_qual.is_clock(cand_clock):
            cutoff_ms = cand_clock
            break

    spot_qual = evidence_qual.qualify_spot_window(
        fast_4h,
        cutoff_ms=cutoff_ms,
        extra_scopes=(vp, flow),
        window_name="fast_4h",
    )

    eg = view.get("expiry_gamma") if isinstance(view.get("expiry_gamma"), Mapping) else {}
    by_expiry = eg.get("by_expiry") if isinstance(eg.get("by_expiry"), Mapping) else {}
    candidates = view.get("candidates") if isinstance(view.get("candidates"), list) else []
    cand_expiry_ms: int | None = None
    for item in candidates:
        if not isinstance(item, Mapping):
            continue
        exp = ((item.get("snapshot") or {}).get("contract") or {}).get("expiry_ms")
        if evidence_qual.is_clock(exp):
            cand_expiry_ms = exp
            break

    pin_record: Mapping[str, Any] | None = None
    if cand_expiry_ms is not None:
        for row in by_expiry.values():
            if isinstance(row, Mapping) and row.get("expiry_ms") == cand_expiry_ms:
                pin_record = row
                break
    if pin_record is None and by_expiry:
        for row in by_expiry.values():
            if isinstance(row, Mapping):
                pin_record = row
                break
    pin_expiry_ms = cand_expiry_ms
    if pin_expiry_ms is None and isinstance(pin_record, Mapping) and evidence_qual.is_clock(pin_record.get("expiry_ms")):
        pin_expiry_ms = pin_record.get("expiry_ms")

    pin_qual = evidence_qual.qualify_live_pin(
        pin_record,
        expiry_ms=pin_expiry_ms,
        cutoff_ms=cutoff_ms,
    )
    rv_raw = eg.get("realized_vol") if isinstance(eg.get("realized_vol"), Mapping) else None
    rv_qual = evidence_qual.qualify_expiry_realized_vol(rv_raw, cutoff_ms=cutoff_ms)

    spot_reasons = set(spot_qual.get("reason_codes") or ())
    warmup_only_reasons = {
        "degraded:window_backfill",
        "degraded:backfill",
        "degraded:has_backfill",
        "degraded:backfill_mixed",
        "not_ready",
    }
    spot_backfill_warming = bool(
        isinstance(fast_4h, Mapping)
        and spot_qual.get("status") == evidence_qual.QUAL_UNALIGNED
        and spot_reasons
        and spot_reasons.issubset(warmup_only_reasons)
    )
    spot_qual_for_health = (
        {**spot_qual, "capability": evidence_qual.CAP_WARMING_UP}
        if spot_backfill_warming
        else spot_qual
    )

    cap_summary = evidence_qual.summarize_channel_capabilities(
        spot_qual=spot_qual_for_health,
        pin_qual=pin_qual,
        rv_qual=rv_qual,
    )
    broken_spot = (
        isinstance(fast_4h, Mapping)
        and fast_4h.get("data_ready") is True
        and spot_qual_for_health.get("capability") == evidence_qual.CAP_BLIND_UNALIGNED
    )
    broken_pin = isinstance(pin_record, Mapping) and pin_qual.get("capability") == evidence_qual.CAP_BLIND_UNALIGNED
    broken_rv = (
        isinstance(rv_raw, Mapping)
        and rv_raw.get("status") == "ok"
        and rv_qual.get("capability") == evidence_qual.CAP_BLIND_UNALIGNED
    )
    return {
        "evidence_channels": cap_summary["channels"],
        "blind_channels": cap_summary["blind_channels"],
        "evidence_contract_ok": not (broken_spot or broken_pin or broken_rv),
    }


def _check_workbench(
    workbench_url: str,
    *,
    token: str,
    expected_core_version: str | None,
    http_get: Callable[..., tuple[int, Any]],
    now_ms: int | None = None,
    require_evidence_channels: bool = False,
    check_execution_live: bool = False,
) -> dict[str, Any]:
    base = workbench_url.rstrip("/")
    try:
        st_s, sess = http_get(f"{base}/api/workbench/session", timeout_s=5.0)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "reason": f"workbench_unreachable:{type(exc).__name__}"}
    if st_s != 200 or not isinstance(sess, dict):
        return {"ok": False, "reason": "workbench_session_endpoint_failed", "status": st_s}
    if not token:
        return {"ok": True, "mode": "session_endpoint_only", "authenticated_check": False}
    try:
        st_w, view = http_get(
            f"{base}/api/workbench",
            headers={"Authorization": f"Bearer {token}"},
            timeout_s=20.0,
        )
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "reason": f"workbench_api_failed:{type(exc).__name__}"}
    if st_w != 200 or not isinstance(view, dict):
        return {"ok": False, "reason": "workbench_api_bad_status", "status": st_w}
    mc = view.get("market_context") if isinstance(view.get("market_context"), dict) else {}
    cands = (view.get("discovery") or {}).get("candidates") or {}
    exec_allowed = any(
        isinstance(c, dict) and c.get("execution_allowed") is True
        for c in cands.values()
    )
    if exec_allowed:
        return {"ok": False, "reason": "workbench_execution_allowed_must_be_false"}
    if mc.get("status") != "current":
        return {
            "ok": False,
            "reason": "workbench_market_context_not_current",
            "status": mc.get("status"),
        }
    if expected_core_version and mc.get("strategy_version") != expected_core_version:
        return {
            "ok": False,
            "reason": "workbench_strategy_version_mismatch",
            "actual": mc.get("strategy_version"),
            "expected": expected_core_version,
        }
    evidence = _evaluate_workbench_evidence(view, now_ms=now_ms)
    if require_evidence_channels and (not evidence["evidence_contract_ok"] or evidence["blind_channels"]):
        return {
            "ok": False,
            "reason": (
                "workbench_evidence_contract_broken"
                if not evidence["evidence_contract_ok"]
                else "workbench_evidence_channels_blind"
            ),
            "authenticated_check": True,
            "market_context_status": mc.get("status"),
            "strategy_version": mc.get("strategy_version"),
            "execution_allowed": False,
            **evidence,
        }
    exec_live_cockpit_mode: str | None = None
    if check_execution_live:
        try:
            st_ex, ex_payload = http_get(
                f"{base}/api/workbench/execution/live",
                headers={"Authorization": f"Bearer {token}"},
                timeout_s=5.0,
            )
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
            return {"ok": False, "reason": f"workbench_execution_live_failed:{type(exc).__name__}"}
        if st_ex != 200 or not isinstance(ex_payload, dict) or ex_payload.get("cockpit_mode") != "READ_ONLY":
            return {
                "ok": False,
                "reason": "workbench_execution_live_contract_invalid",
                "status": st_ex,
                "cockpit_mode": ex_payload.get("cockpit_mode") if isinstance(ex_payload, dict) else None,
            }
        exec_live_cockpit_mode = str(ex_payload.get("cockpit_mode"))
    return {
        "ok": True,
        "authenticated_check": True,
        "market_context_status": mc.get("status"),
        "strategy_version": mc.get("strategy_version"),
        "execution_allowed": False,
        **({"execution_cockpit_mode": exec_live_cockpit_mode} if exec_live_cockpit_mode is not None else {}),
        **evidence,
    }


def _check_map(map_current: Path, *, now_ms: int, max_age_ms: int) -> dict[str, Any]:
    data, err = _read_json_file(map_current)
    if data is None:
        return {"ok": False, "reason": f"map_current_{err}", "path": str(map_current)}
    if data.get("schema") != MAP_SCHEMA:
        return {"ok": False, "reason": "map_schema_mismatch", "schema": data.get("schema")}
    gen = data.get("generated_at_ms")
    if isinstance(gen, bool) or not isinstance(gen, int):
        return {"ok": False, "reason": "map_timestamp_missing"}
    age_ms = now_ms - gen
    if not 0 <= age_ms <= max_age_ms:
        return {"ok": False, "reason": "map_stale", "age_ms": age_ms}
    fmz = data.get("fmz") if isinstance(data.get("fmz"), Mapping) else {}
    fmz_status = fmz.get("status")
    if fmz_status != "current":
        return {"ok": False, "reason": "map_fmz_not_current", "fmz_status": fmz_status, "age_ms": age_ms}
    return {
        "ok": True,
        "age_ms": age_ms,
        "complete": (data.get("completeness") or {}).get("complete"),
        "fmz_status": fmz_status,
    }


def _check_kpf(kpf_current: Path, *, now_ms: int, max_age_ms: int) -> dict[str, Any]:
    data, err = _read_json_file(kpf_current)
    if data is None:
        return {"ok": False, "reason": f"kpf_current_{err}", "path": str(kpf_current)}
    gen = data.get("generated_at_ms")
    if isinstance(gen, bool) or not isinstance(gen, int):
        return {"ok": False, "reason": "kpf_timestamp_missing"}
    age_ms = now_ms - gen
    if not 0 <= age_ms <= max_age_ms:
        return {"ok": False, "reason": "kpf_stale", "age_ms": age_ms}
    if data.get("status") != "READY":
        return {"ok": False, "reason": f"kpf_status_{data.get('status')}"}
    return {"ok": True, "age_ms": age_ms, "version_id": data.get("version_id")}


CRITICAL_ARCHIVE_STEPS = {"ledger", "ledger_fields", "structure", "outcomes"}


def _check_archive(archive_root: Path, *, now_ms: int, max_age_ms: int = 1_800_000) -> dict[str, Any]:
    if not archive_root.is_dir():
        return {"ok": False, "reason": "archive_root_missing", "path": str(archive_root)}
    required_dirs = ("cases", "structure", "levels", "band_snapshots")
    missing = [name for name in required_dirs if not (archive_root / name).is_dir()]
    if missing:
        return {"ok": False, "reason": "archive_subdirs_missing", "missing": missing}
    state_path = archive_root / "_state" / "state.json"
    state, err = _read_json_file(state_path)
    if state is None:
        return {"ok": False, "reason": f"archive_state_{err}", "path": str(state_path)}
    last_tick_ms = state.get("last_tick_ms")
    if isinstance(last_tick_ms, bool) or not isinstance(last_tick_ms, int):
        return {"ok": False, "reason": "archive_last_tick_missing"}
    tick_age_ms = now_ms - last_tick_ms
    if not 0 <= tick_age_ms <= max_age_ms:
        return {"ok": False, "reason": "archive_tick_stale", "age_ms": tick_age_ms}
    summary = state.get("last_tick_summary") if isinstance(state.get("last_tick_summary"), Mapping) else {}
    err_steps = [str(s) for s in (summary.get("error_steps") or []) if s]
    critical_errors = [s for s in err_steps if s in CRITICAL_ARCHIVE_STEPS]
    if int(summary.get("errors_count") or 0) > 0 and critical_errors:
        return {
            "ok": False,
            "reason": "archive_critical_tick_errors",
            "age_ms": tick_age_ms,
            "error_steps": critical_errors,
        }
    return {
        "ok": True,
        "subdirs": list(required_dirs),
        "age_ms": tick_age_ms,
        "last_tick_errors": int(summary.get("errors_count") or 0),
    }


MANAGED_DAEMON_SERVICES = (
    "gexmonitorapi.service",
    "astra-signal-producer.service",
    "astra-workbench-v20.service",
)
MANAGED_TIMERS = (
    "astra-research-archive.timer",
    "astra-player-sync.timer",
    "astra-player-prewarm.timer",
    "astra-player-deal.timer",
    "astra-player-settle.timer",
    "astra-map-data.timer",
    "astra-kpf-light.timer",
    "astra-log-retention.timer",
    "signal-audit-materialize.timer",
)


def _check_systemd_units(release_dir: Path | None, *, include_execution: bool = False) -> dict[str, Any]:
    if os.name == "nt":
        return {"ok": True, "skipped": "windows"}
    expected_release = release_dir.resolve() if release_dir and release_dir.exists() else None
    daemons: dict[str, Any] = {}
    timers: dict[str, str] = {}
    failures: list[str] = []
    daemon_list = list(MANAGED_DAEMON_SERVICES)
    if include_execution or Path("/etc/systemd/system/astra-execution-coordinator.service").exists():
        if "astra-execution-coordinator.service" not in daemon_list:
            daemon_list.append("astra-execution-coordinator.service")
    for unit in daemon_list:
        res = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, check=False)
        status = res.stdout.strip() or "unknown"
        pid_out = subprocess.run(
            ["systemctl", "show", unit, "-p", "MainPID", "--value"],
            capture_output=True,
            text=True,
            check=False,
        )
        raw_pid = pid_out.stdout.strip()
        pid = int(raw_pid) if raw_pid.isdigit() else 0
        cwd_ok = True
        cwd_str = None
        if status != "active" or pid <= 0:
            failures.append(f"daemon_inactive:{unit}:{status}")
            cwd_ok = False
        elif expected_release is not None:
            try:
                proc_cwd = Path(f"/proc/{pid}/cwd").resolve(strict=True)
                cwd_str = str(proc_cwd)
                cwd_ok = proc_cwd == expected_release or expected_release in proc_cwd.parents
            except OSError:
                cwd_ok = False
            if not cwd_ok:
                failures.append(f"daemon_release_mismatch:{unit}")
        daemons[unit] = {"status": status, "pid": pid, "cwd": cwd_str, "cwd_ok": cwd_ok}
    for timer in MANAGED_TIMERS:
        res = subprocess.run(["systemctl", "is-active", timer], capture_output=True, text=True, check=False)
        status = res.stdout.strip() or "unknown"
        timers[timer] = status
        if status != "active":
            failures.append(f"timer_inactive:{timer}:{status}")
    return {
        "ok": not failures,
        "failures": failures,
        "daemons": daemons,
        "timers": timers,
    }


def _check_player_plan(plan_path: Path) -> dict[str, Any]:
    data, err = _read_json_file(plan_path)
    if data is None:
        return {"ok": False, "reason": f"player_plan_{err}", "path": str(plan_path)}
    if data.get("mode") != "SHADOW_ONLY" or data.get("execution_allowed") is not False:
        return {
            "ok": False,
            "reason": "player_plan_safety_boundary_violated",
            "mode": data.get("mode"),
            "execution_allowed": data.get("execution_allowed"),
        }
    return {"ok": True, "plan_id": data.get("plan_id"), "mode": data.get("mode"), "execution_allowed": False}


def check_release_manifest(release_root: Path) -> dict[str, Any]:
    """Verify every file recorded in ``<release_root>/RELEASE_MANIFEST.json``."""
    manifest_path = release_root / "RELEASE_MANIFEST.json"
    data, err = _read_json_file(manifest_path)
    if data is None:
        return {"ok": False, "reason": f"release_manifest_{err}", "path": str(manifest_path)}
    if data.get("schema") != RELEASE_SCHEMA:
        return {"ok": False, "reason": "release_manifest_schema_mismatch"}
    files = data.get("files")
    if not isinstance(files, Mapping) or not files:
        return {"ok": False, "reason": "release_manifest_files_empty"}
    mismatches: list[str] = []
    for rel, meta in files.items():
        if not isinstance(meta, Mapping) or not isinstance(rel, str):
            mismatches.append(str(rel))
            continue
        target = release_root / rel
        if not target.is_file():
            mismatches.append(f"missing:{rel}")
            continue
        actual_sha = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual_sha != meta.get("sha256"):
            mismatches.append(f"sha_mismatch:{rel}")
    if mismatches:
        return {"ok": False, "reason": "release_integrity_failed", "mismatches": mismatches[:20]}
    return {
        "ok": True,
        "release_id": data.get("release_id"),
        "source_commit": data.get("source_commit"),
        "worktree_state": data.get("worktree_state"),
        "file_count": len(files),
    }


EXEC_CRITICAL_ALERTS = frozenset({
    "NAKED_SHORT_BREACH",
    "EXTERNAL_UNATTRIBUTED_POSITION",
    "UNEXPECTED_LONG_IN_SHORT_LEG",
    "UNEXPECTED_SHORT_IN_LONG_LEG",
    "HOLD_REVIEW_LLM_UNAVAILABLE",
    "WRITES_DISABLED_OPEN_ORDERS_OR_RISK_MANUAL_TAKEOVER",
})
EXEC_CRITICAL_PREFIXES = (
    "UNKNOWN_UNVERIFIABLE_PAST_HOT_CACHE:",
    "ORDER_RECONCILE_CONFLICT_",
    "ORDER_TRADES_EXCEED_ORDER_FILLED:",
)
DEFAULT_EXEC_STATUS_PATH = Path("/var/lib/xxproject/execution/public_status.json")


def _check_execution_status(
    status_path: Path,
    *,
    now_ms: int,
    max_age_ms: int,
    max_facts_age_ms: int = 45_000,
    require_shadow_only: bool = True,
    expected_mode: str | None = None,
) -> dict[str, Any]:
    if not status_path.is_file():
        return {"ok": False, "reason": "execution_status_missing", "path": str(status_path)}
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": f"execution_status_unreadable:{exc}", "path": str(status_path)}
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "execution_status_invalid_json", "path": str(status_path)}
    gen_ms = payload.get("generated_at_ms")
    if isinstance(gen_ms, bool) or not isinstance(gen_ms, (int, float)):
        return {"ok": False, "reason": "execution_status_missing_timestamp", "path": str(status_path)}
    age_ms = now_ms - int(gen_ms)
    if age_ms < -5_000:
        return {"ok": False, "reason": "execution_status_clock_future", "age_ms": age_ms}
    if age_ms > max_age_ms:
        return {"ok": False, "reason": "execution_status_stale", "age_ms": age_ms, "max_age_ms": max_age_ms}
    if payload.get("stale") is True:
        return {"ok": False, "reason": "execution_status_marked_stale", "age_ms": age_ms}

    active = payload.get("active_episode") if isinstance(payload.get("active_episode"), Mapping) else None
    facts_age_ms: int | None = None
    if active is not None and not bool(active.get("cashflow_settled")):
        facts_asof_ms = payload.get("facts_asof_ms")
        if isinstance(facts_asof_ms, bool) or not isinstance(facts_asof_ms, (int, float)):
            return {"ok": False, "reason": "execution_facts_timestamp_missing", "path": str(status_path)}
        facts_age_ms = now_ms - int(facts_asof_ms)
        if facts_age_ms > max_facts_age_ms:
            return {
                "ok": False,
                "reason": "execution_facts_stale",
                "facts_age_ms": facts_age_ms,
                "max_facts_age_ms": max_facts_age_ms,
            }

    health = str(payload.get("health") or "")
    if health in {"DEGRADED", "STALE", "NAKED_SHORT_BREACH", "DISCONNECTED", "WRITE_DISABLED", "HALTED"}:
        return {"ok": False, "reason": f"execution_unhealthy:{health}", "health": health}
    if health not in {"OK", "SHADOW_IDLE", "HEALTHY"}:
        return {"ok": False, "reason": f"execution_health_unrecognized:{health or 'empty'}", "health": health}

    ep_alerts = [str(a) for a in ((active or {}).get("alerts") or ())]
    for raw_alert in ep_alerts:
        if raw_alert in EXEC_CRITICAL_ALERTS or any(raw_alert.startswith(px) for px in EXEC_CRITICAL_PREFIXES):
            return {"ok": False, "reason": f"execution_critical_alert:{raw_alert}", "health": health}
    if (
        "RX_MARGIN_OR_CASH_EXHAUSTED_SPREAD_HEDGED_MANUAL_DECISION" in ep_alerts
        and ((active or {}).get("revoke") or {}).get("cash_wall_10009") is not False
    ):
        return {
            "ok": False,
            "reason": "execution_critical_alert:RX_MARGIN_OR_CASH_EXHAUSTED_SPREAD_HEDGED_MANUAL_DECISION",
            "health": health,
        }

    if payload.get("manual_takeover_required") is True:
        return {"ok": False, "reason": "execution_manual_takeover_required", "health": health}

    mode = str(payload.get("mode") or "")
    writes_enabled = bool(payload.get("writes_enabled"))
    want_mode = expected_mode or ("SHADOW_ONLY" if require_shadow_only else None)
    if require_shadow_only and want_mode == "SHADOW_ONLY" and (mode != "SHADOW_ONLY" or writes_enabled):
        return {
            "ok": False,
            "reason": "execution_shadow_only_violated",
            "mode": mode,
            "writes_enabled": writes_enabled,
        }
    if want_mode is not None and mode != want_mode:
        return {
            "ok": False,
            "reason": "execution_mode_mismatch",
            "mode": mode,
            "expected_mode": want_mode,
        }
    return {
        "ok": True,
        "mode": mode,
        "cockpit_mode": payload.get("cockpit_mode"),
        "writes_enabled": writes_enabled,
        "health": health,
        "age_ms": age_ms,
        "facts_age_ms": facts_age_ms,
        "path": str(status_path),
    }


def run_health_check(
    *,
    signal_logs_dir: Path,
    signal_state_dir: Path,
    gex_url: str,
    workbench_url: str,
    workbench_env: Path | None,
    map_current: Path,
    kpf_current: Path,
    archive_root: Path,
    player_plan: Path,
    release_dir: Path | None,
    expected_core_version: str | None = "2.0.7",
    skip_network: bool = False,
    skip_systemd: bool = False,
    require_systemd: bool = False,
    require_workbench_auth: bool = False,
    require_release_manifest: bool = False,
    require_evidence_channels: bool = False,
    gex_env: Path | None = None,
    exec_status_path: Path | None = None,
    require_execution: bool = False,
    max_exec_status_age_ms: int = 120_000,
    max_exec_facts_age_ms: int = 45_000,
    require_exec_shadow_only: bool = True,
    expected_exec_mode: str | None = None,
    max_producer_age_ms: int = 300_000,
    max_snapshot_age_ms: int = 600_000,
    max_map_age_ms: int = 1_800_000,
    max_kpf_age_ms: int = 172_800_000,
    max_archive_age_ms: int = 1_800_000,
    now_ms: int | None = None,
    http_get: Callable[..., tuple[int, Any]] = _http_get_json,
) -> dict[str, Any]:
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    checks: dict[str, dict[str, Any]] = {
        "signal_producer": _check_signal_producer(
            signal_logs_dir,
            signal_state_dir,
            now_ms=now,
            max_producer_age_ms=max_producer_age_ms,
            max_snapshot_age_ms=max_snapshot_age_ms,
            expected_core_version=expected_core_version,
        ),
        "map_data": _check_map(map_current, now_ms=now, max_age_ms=max_map_age_ms),
        "kpf_light": _check_kpf(kpf_current, now_ms=now, max_age_ms=max_kpf_age_ms),
        "research_archive": _check_archive(archive_root, now_ms=now, max_age_ms=max_archive_age_ms),
        "player_plan": _check_player_plan(player_plan),
    }
    exec_path_eff = exec_status_path
    if exec_path_eff is None and (require_execution or DEFAULT_EXEC_STATUS_PATH.is_file()):
        exec_path_eff = DEFAULT_EXEC_STATUS_PATH
    if exec_path_eff is not None:
        eff_shadow_only = require_exec_shadow_only and (expected_exec_mode in (None, "SHADOW_ONLY"))
        checks["execution"] = _check_execution_status(
            exec_path_eff,
            now_ms=now,
            max_age_ms=max_exec_status_age_ms,
            max_facts_age_ms=max_exec_facts_age_ms,
            require_shadow_only=eff_shadow_only,
            expected_mode=expected_exec_mode,
        )
    if not skip_network:
        checks["gexmonitorapi"] = _check_gex_api(gex_url, token=_read_gex_token(gex_env), http_get=http_get)
        token = _read_workbench_token(workbench_env)
        if require_workbench_auth and not token:
            checks["workbench"] = {
                "ok": False,
                "reason": "workbench_token_unreadable_or_missing",
                "env_path": str(workbench_env) if workbench_env else None,
            }
        else:
            checks["workbench"] = _check_workbench(
                workbench_url,
                token=token,
                expected_core_version=expected_core_version,
                http_get=http_get,
                now_ms=now,
                require_evidence_channels=require_evidence_channels,
                check_execution_live=bool(exec_path_eff is not None and token),
            )
    if release_dir is not None and (require_release_manifest or (release_dir / "RELEASE_MANIFEST.json").exists()):
        checks["release_manifest"] = check_release_manifest(release_dir)
    if require_systemd and not skip_systemd:
        checks["systemd_units"] = _check_systemd_units(release_dir, include_execution=bool(exec_path_eff is not None))

    ok = all(item.get("ok") is True for item in checks.values())
    return {
        "schema": SCHEMA,
        "ok": ok,
        "checked_at_ms": now,
        "skip_network": bool(skip_network),
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signal-logs-dir", type=Path, default=Path("/var/lib/xxproject/signal_native/logs"))
    parser.add_argument("--signal-state-dir", type=Path, default=Path("/var/lib/xxproject/signal_native/state"))
    parser.add_argument("--gex-url", default="http://127.0.0.1:8000")
    parser.add_argument("--gex-env", type=Path, default=Path("/etc/gexmonitorapi.env"))
    parser.add_argument("--workbench-url", default="http://127.0.0.1:8895")
    parser.add_argument("--workbench-env", type=Path, default=Path("/etc/signal-audit/workbench-v20.env"))
    parser.add_argument("--map-current", type=Path, default=Path("/opt/astra-map-data/state/current_map.json"))
    parser.add_argument("--kpf-current", type=Path, default=Path("/var/lib/astra-kpf-light/published/current.json"))
    parser.add_argument("--archive-root", type=Path, default=Path("/var/lib/xxproject/research_archive"))
    parser.add_argument(
        "--player-plan",
        type=Path,
        default=Path("/opt/xxproject/current/deploy/player_dossier/plan.daily_r16.v3_p5.json"),
    )
    parser.add_argument("--release-dir", type=Path, default=Path("/opt/xxproject/current"))
    parser.add_argument("--expected-core-version", default="2.0.7")
    parser.add_argument("--skip-network", action="store_true")
    parser.add_argument("--skip-systemd", action="store_true")
    parser.add_argument("--require-systemd", action="store_true")
    parser.add_argument(
        "--require-workbench-auth",
        action="store_true",
        help="Fail instead of falling back to session-only when ASTRA_WORKBENCH_TOKEN cannot be read",
    )
    parser.add_argument("--require-release-manifest", action="store_true")
    parser.add_argument(
        "--require-evidence-channels",
        action="store_true",
        help="Fail when workbench evidence channels report blind channels or broken producer provenance",
    )
    parser.add_argument("--exec-status-path", type=Path, default=None)
    parser.add_argument("--require-execution", action="store_true")
    parser.add_argument("--expected-exec-mode", choices=("SHADOW_ONLY", "DRY_RUN", "TESTNET", "LIVE"), default="SHADOW_ONLY")
    parser.add_argument("--max-exec-status-age-ms", type=int, default=120_000)
    parser.add_argument("--max-exec-facts-age-ms", type=int, default=45_000)
    parser.add_argument("--max-producer-age-ms", type=int, default=300_000)
    parser.add_argument("--max-snapshot-age-ms", type=int, default=600_000)
    parser.add_argument("--max-map-age-ms", type=int, default=1_800_000)
    parser.add_argument("--max-kpf-age-ms", type=int, default=172_800_000)
    parser.add_argument("--max-archive-age-ms", type=int, default=1_800_000)
    args = parser.parse_args(argv)

    auto_systemd = (
        os.name != "nt"
        and not args.skip_network
        and not args.skip_systemd
        and args.release_dir == Path("/opt/xxproject/current")
    )
    report = run_health_check(
        signal_logs_dir=args.signal_logs_dir,
        signal_state_dir=args.signal_state_dir,
        gex_url=args.gex_url,
        gex_env=args.gex_env,
        workbench_url=args.workbench_url,
        workbench_env=args.workbench_env,
        map_current=args.map_current,
        kpf_current=args.kpf_current,
        archive_root=args.archive_root,
        player_plan=args.player_plan,
        release_dir=args.release_dir,
        expected_core_version=args.expected_core_version,
        skip_network=args.skip_network,
        skip_systemd=args.skip_systemd,
        require_systemd=bool(args.require_systemd or auto_systemd),
        require_workbench_auth=args.require_workbench_auth,
        require_release_manifest=args.require_release_manifest,
        require_evidence_channels=args.require_evidence_channels,
        exec_status_path=args.exec_status_path,
        require_execution=args.require_execution,
        max_exec_status_age_ms=args.max_exec_status_age_ms,
        max_exec_facts_age_ms=args.max_exec_facts_age_ms,
        require_exec_shadow_only=(args.expected_exec_mode == "SHADOW_ONLY"),
        expected_exec_mode=args.expected_exec_mode,
        max_producer_age_ms=args.max_producer_age_ms,
        max_snapshot_age_ms=args.max_snapshot_age_ms,
        max_map_age_ms=args.max_map_age_ms,
        max_kpf_age_ms=args.max_kpf_age_ms,
        max_archive_age_ms=args.max_archive_age_ms,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
