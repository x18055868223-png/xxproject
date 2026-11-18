"""Preflight safety check for GFRI v1.1 shadow collector deployment."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify GFRI v1.1 shadow isolation env flags")
    parser.add_argument("--env-file", required=True, help="Path to gfri_v11 env file")
    parser.add_argument("--require-shadow-only", action="store_true")
    parser.add_argument("--require-disabled", action="store_true")
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
    print(f"PREFLIGHT_OK: {env_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
