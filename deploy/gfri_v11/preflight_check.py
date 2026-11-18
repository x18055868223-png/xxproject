"""Host & configuration preflight check for GFRI v1.1 shadow collector.

Supports:
1) Env lock validation (--env-file --require-shadow-only --require-disabled)
2) Target host readiness (--host-check): Python >=3.12, dir/user permissions,
   RAM vs 128M cap, disk capacity for 60d+ retention, NTP sync, optional --network-probe.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request

REQUIRED_MIN_DISK_BYTES = 512 * 1024 * 1024  # 512 MiB minimum headroom
SYSTEMD_MEMORY_CAP_MIB = 128


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip()
    return values


def verify_env_safety(env_values: dict[str, str], *, require_disabled: bool = False) -> list[str]:
    errors: list[str] = []
    if env_values.get("GFRI_DECISION_AUTHORITY", "SHADOW_ONLY") != "SHADOW_ONLY":
        errors.append("GFRI_DECISION_AUTHORITY must be SHADOW_ONLY")
    if env_values.get("GFRI_PLAYER_INTEGRATION_ENABLED", "0").lower() not in ("0", "false", "no"):
        errors.append("GFRI_PLAYER_INTEGRATION_ENABLED must remain 0/false")
    if env_values.get("GFRI_LIVE_VETO_ENABLED", "0").lower() not in ("0", "false", "no"):
        errors.append("GFRI_LIVE_VETO_ENABLED must remain 0/false")
    if require_disabled and env_values.get("GFRI_COLLECTOR_ENABLED", "0").lower() not in ("0", "false", "no"):
        errors.append("GFRI_COLLECTOR_ENABLED must default to 0 before acceptance")
    return errors


def _check_writable_dir(target: Path) -> tuple[bool, str]:
    try:
        target.mkdir(parents=True, exist_ok=True)
        fd, probe_name = tempfile.mkstemp(prefix=".gfri_perm_", dir=str(target))
        os.close(fd)
        Path(probe_name).unlink(missing_ok=True)
        return True, f"writable:{target}"
    except Exception as exc:
        return False, f"not_writable:{target}:{type(exc).__name__}:{exc}"


def _read_linux_mem_available_mib() -> float | None:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return None
    for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("MemAvailable:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) / 1024.0
    return None


def _check_ntp_sync() -> dict[str, str]:
    if platform.system().lower() != "linux":
        return {"status": "SKIPPED_NON_LINUX", "detail": platform.system()}
    try:
        out = subprocess.check_output(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).decode("utf-8").strip()
        return {"status": "SYNCED" if out == "yes" else "UNSYNCED", "detail": f"timedatectl={out}"}
    except Exception as exc:
        return {"status": "UNVERIFIED", "detail": type(exc).__name__}


def run_host_checks(
    env_values: dict[str, str],
    *,
    state_dir: Path | None = None,
    raw_dir: Path | None = None,
    probe_network: bool = False,
) -> dict[str, object]:
    """Run non-invasive target host readiness checks without enabling any timer."""
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, object] = {}

    py_ok = sys.version_info >= (3, 12)
    checks["python_version"] = {"ok": py_ok, "version": sys.version.split()[0]}
    if not py_ok:
        errors.append(f"python>=3.12 required, found {sys.version.split()[0]}")

    repo_root = str(Path(__file__).resolve().parents[2])
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    try:
        import research.gfri_v11.core  # noqa: F401
        import research.gfri_v11.history  # noqa: F401
        import research.gfri_v11.policy  # noqa: F401
        checks["module_imports"] = {"ok": True}
    except Exception as exc:
        checks["module_imports"] = {"ok": False, "error": repr(exc)}
        errors.append(f"module_import_failed:{type(exc).__name__}")

    eff_state = state_dir or Path(env_values.get("GFRI_STATE_DIR", "") or ".")
    eff_raw = raw_dir or Path(env_values.get("GFRI_RAW_CAPTURE_DIR", "") or ".")
    for label, p in (("state_dir", eff_state), ("raw_capture_dir", eff_raw)):
        ok, detail = _check_writable_dir(p)
        checks[label] = {"ok": ok, "detail": detail}
        if not ok:
            errors.append(detail)
        check_path = p
        while not check_path.exists() and check_path != check_path.parent:
            check_path = check_path.parent
        if check_path.exists():
            usage = shutil.disk_usage(check_path)
            checks[f"{label}_disk_free_mb"] = round(usage.free / (1024 * 1024), 1)
            if usage.free < REQUIRED_MIN_DISK_BYTES:
                errors.append(f"insufficient_disk_space:{label}:{usage.free}")

    mem_avail = _read_linux_mem_available_mib()
    if mem_avail is not None:
        checks["linux_mem_available_mib"] = round(mem_avail, 1)
        if mem_avail < SYSTEMD_MEMORY_CAP_MIB * 2:
            warnings.append(f"low_host_mem_available:{mem_avail:.1f}MiB")

    ntp_info = _check_ntp_sync()
    checks["clock_sync"] = ntp_info
    if ntp_info["status"] == "UNSYNCED":
        errors.append("ntp_not_synchronized")

    if probe_network:
        net_results: dict[str, object] = {}
        for name, url in (
            ("gexmonitor", "https://gexmonitor.com/api/gex-latest?asset=BTC&exchange=all&lite=true"),
            ("deribit_inst", "https://www.deribit.com/api/v2/public/get_instrument?instrument_name=BTC-PERPETUAL"),
            ("okx_inst", "https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId=BTC-USDT-SWAP"),
        ):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "xxproject-gfri-preflight/1.1"})
                with urllib.request.urlopen(req, timeout=8.0) as resp:
                    net_results[name] = {"ok": resp.status == 200, "status": resp.status}
            except Exception as exc:
                net_results[name] = {"ok": False, "error": type(exc).__name__}
                if name != "okx_inst":
                    errors.append(f"network_probe_failed:{name}:{type(exc).__name__}")
                else:
                    warnings.append(f"optional_okx_probe_failed:{type(exc).__name__}")
        checks["network_probe"] = net_results

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings, "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify GFRI v1.1 shadow isolation env & host readiness")
    parser.add_argument("--env-file", required=True, help="Path to gfri_v11 env file")
    parser.add_argument("--require-shadow-only", action="store_true")
    parser.add_argument("--require-disabled", action="store_true")
    parser.add_argument("--host-check", action="store_true", help="Run host Python/perm/disk/clock checks")
    parser.add_argument("--network-probe", action="store_true", help="Probe upstream GEX/Deribit/OKX (with --host-check)")
    parser.add_argument("--state-dir", type=Path, default=None, help="Override writable state dir for host-check")
    parser.add_argument("--raw-dir", type=Path, default=None, help="Override writable raw dir for host-check")
    args = parser.parse_args()

    env_path = Path(args.env_file)
    vals = parse_env_file(env_path)
    if not vals:
        print(f"ERROR: empty or missing env file: {env_path}", file=sys.stderr)
        return 2
    errs = (
        verify_env_safety(vals, require_disabled=args.require_disabled)
        if args.require_shadow_only or args.require_disabled
        else []
    )
    if errs:
        for err in errs:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    if args.host_check:
        host_res = run_host_checks(
            vals,
            state_dir=args.state_dir,
            raw_dir=args.raw_dir,
            probe_network=args.network_probe,
        )
        if not host_res["ok"]:
            for err in host_res["errors"]:  # type: ignore[attr-defined]
                print(f"HOST_ERROR: {err}", file=sys.stderr)
            return 1
        print(f"HOST_PREFLIGHT_OK: {host_res['checks']}")

    print(f"PREFLIGHT_OK: {env_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
