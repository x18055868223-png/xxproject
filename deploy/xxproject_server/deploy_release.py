"""Deploy, verify, and roll back unified xxproject server releases safely.

Strictly separates immutable code releases (``/opt/xxproject/releases/<id>``)
from mutable runtime state (``/var/lib/xxproject/``) and host secrets
(``/etc/signal-audit/``). Supports ``--root-prefix`` and ``--no-systemd`` for
hermetic offline and Linux ``/tmp`` rehearsal drills.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
from typing import Any

RELEASE_SCHEMA = "xxproject_server_release@1.0.0"

CORE_IMPORT_MODULES = [
    "astra_signal_producer_v1",
    "astra_workbench_v20",
    "astra_map_data_server_v24",
    "astra_kpf_light_v1",
    "astra_research_archive_v1",
    "astra_player_tracking_v13",
    "materialize_signal_cards",
    "xxproject_server_health_v1",
]

ENV_TEMPLATES = {
    "deploy/signal_producer/signal-producer.env.example": "etc/signal-audit/signal-producer.env",
    "deploy/signal_audit/workbench-v20.env.example": "etc/signal-audit/workbench-v20.env",
    "deploy/signal_audit/signal-audit-llm.env.example": "etc/signal-audit/llm.env",
    "deploy/signal_audit/signal-audit-materialize.env.example": "etc/signal-audit/signal-audit-materialize.env",
    "deploy/kpf_light/astra-kpf-light.env.example": "etc/astra-kpf-light.env",
    "deploy/gexmonitorapi/gexmonitorapi.env.example": "etc/gexmonitorapi.env",
}


def _is_system_root(prefix: Path) -> bool:
    return os.name != "nt" and prefix.resolve() == Path("/")


def _validate_deploy_window(
    prefix: Path,
    *,
    allow_window_override: bool = False,
    now_ms: int | None = None,
) -> None:
    """Refuse live root switches during 08:00 UTC settlement or 16:00 UTC deal windows unless overridden."""
    if allow_window_override or not _is_system_root(prefix):
        return
    utc_now = (
        dt.datetime.now(dt.timezone.utc)
        if now_ms is None
        else dt.datetime.fromtimestamp(now_ms / 1000.0, tz=dt.timezone.utc)
    )
    mins = utc_now.hour * 60 + utc_now.minute
    in_settle = (7 * 60 + 50) <= mins < (8 * 60 + 35)
    in_deal = (15 * 60 + 50) <= mins < (16 * 60 + 35)
    if in_settle or in_deal:
        window_name = "08:00 UTC settlement" if in_settle else "16:00 UTC auto-deal"
        raise RuntimeError(
            f"UNSAFE_DEPLOY_WINDOW_UTC: {utc_now.strftime('%H:%M UTC')} falls inside {window_name} protection window"
        )


def _validate_systemd_prereqs(prefix: Path, *, apply_units: bool, no_systemd: bool) -> None:
    """Check host runtime prerequisites before mutating release symlinks or unit files."""
    if not apply_units or no_systemd or os.name == "nt":
        return
    gex_venv_python = prefix / "opt" / "xxproject-runtime" / "venv" / "bin" / "python"
    if not gex_venv_python.is_file():
        raise RuntimeError(f"MISSING_HOST_GEX_RUNTIME_VENV: {gex_venv_python}")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _app_root(prefix: Path) -> Path:
    return prefix / "opt" / "xxproject"


def _pointers_path(app_root: Path) -> Path:
    return app_root / "release_pointers.json"


def _read_pointers(app_root: Path) -> dict[str, Any]:
    path = _pointers_path(app_root)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    out: dict[str, Any] = {}
    for name in ("current", "previous"):
        link = app_root / name
        if link.is_symlink():
            out[name] = link.resolve().name
    return out


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _append_receipt(app_root: Path, receipt: dict[str, Any]) -> None:
    ledger = app_root / "deployments.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _safe_extract_tar(pkg_path: Path, stage_dir: Path) -> dict[str, Any]:
    stage_resolved = stage_dir.resolve()
    with tarfile.open(pkg_path, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise ValueError(f"UNSAFE_TAR_MEMBER_TYPE: {member.name}")
            if not member.isfile():
                continue
            rel = Path(member.name)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError(f"UNSAFE_TAR_PATH: {member.name}")
            dest = (stage_resolved / rel).resolve()
            dest.relative_to(stage_resolved)
            dest.parent.mkdir(parents=True, exist_ok=True)
            src_file = archive.extractfile(member)
            if src_file is None:
                raise ValueError(f"UNREADABLE_TAR_MEMBER: {member.name}")
            with src_file:
                dest.write_bytes(src_file.read())
            os.chmod(dest, 0o755 if member.name.endswith(".sh") else 0o644)
    manifest_path = stage_resolved / "RELEASE_MANIFEST.json"
    if not manifest_path.is_file():
        raise ValueError("RELEASE_MANIFEST_MISSING")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _verify_release_dir(release_dir: Path) -> dict[str, Any]:
    manifest_path = release_dir / "RELEASE_MANIFEST.json"
    if not manifest_path.is_file():
        raise ValueError(f"RELEASE_MANIFEST_MISSING: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != RELEASE_SCHEMA:
        raise ValueError(f"RELEASE_SCHEMA_MISMATCH: {manifest.get('schema')}")
    inv = manifest.get("security_invariants") or {}
    if (
        inv.get("fmz_robot_bundled") is not False
        or inv.get("signal_core_read_only_demo") is not True
        or inv.get("ai_player_mode") != "SHADOW_ONLY"
        or inv.get("ai_player_execution_allowed") is not False
    ):
        raise ValueError("SECURITY_INVARIANTS_FAILED")

    files = manifest.get("files") or {}
    if not isinstance(files, dict) or not files:
        raise ValueError("RELEASE_FILES_EMPTY")
    for rel, meta in files.items():
        target = release_dir / rel
        if not target.is_file():
            raise ValueError(f"RELEASE_FILE_MISSING: {rel}")
        actual = _sha256(target.read_bytes())
        if actual != meta.get("sha256"):
            raise ValueError(f"RELEASE_FILE_SHA_MISMATCH: {rel}")

    plan = json.loads((release_dir / "deploy/player_dossier/plan.daily_r16.v2.json").read_text(encoding="utf-8-sig"))
    if plan.get("mode") != "SHADOW_ONLY" or plan.get("execution_allowed") is not False:
        raise ValueError("PLAYER_PLAN_SHADOW_ONLY_FAILED")
    core_text = (release_dir / "signal_core/neutral_regulation_demo_fmz.py").read_text(encoding="utf-8")
    if '"read_only_demo": True' not in core_text:
        raise ValueError("SIGNAL_CORE_READ_ONLY_FAILED")
    return manifest


def _run_import_smoke(release_dir: Path) -> None:
    tools_dir = release_dir / "tools"
    script = "; ".join(f"import {mod}" for mod in CORE_IMPORT_MODULES)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(tools_dir),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(tools_dir)},
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"IMPORT_SMOKE_FAILED: {proc.stderr.strip()}")


def _switch_link(link_path: Path, target_dir: Path) -> str:
    if os.name != "nt":
        tmp_link = link_path.with_name(f".{link_path.name}.tmp-{os.getpid()}")
        tmp_link.unlink(missing_ok=True)
        os.symlink(target_dir, tmp_link)
        os.replace(tmp_link, link_path)
        return "symlink"
    try:
        tmp_link = link_path.with_name(f".{link_path.name}.tmp-{os.getpid()}")
        tmp_link.unlink(missing_ok=True)
        os.symlink(target_dir, tmp_link, target_is_directory=True)
        os.replace(tmp_link, link_path)
        return "symlink"
    except OSError:
        # Unprivileged Windows fallback for local test drills only.
        if link_path.is_symlink() or link_path.is_file():
            link_path.unlink()
        elif link_path.is_dir():
            shutil.rmtree(link_path)
        shutil.copytree(target_dir, link_path)
        return "win_copy_fallback"


def _resolve_active_release_dir(app_root: Path, slot: str = "current") -> Path | None:
    pointers = _read_pointers(app_root)
    rel_id = pointers.get(slot)
    if isinstance(rel_id, str) and rel_id:
        cand = app_root / "releases" / rel_id
        if cand.is_dir():
            return cand
    link = app_root / slot
    if link.is_symlink():
        resolved = link.resolve()
        if resolved.is_dir():
            return resolved
    return None


def cmd_preflight(pkg: Path, prefix: Path, *, min_free_bytes: int = 512 * 1024 * 1024) -> dict[str, Any]:
    if sys.version_info < (3, 12):
        raise RuntimeError(f"PYTHON_3_12_REQUIRED: {sys.version}")
    check_path = prefix if prefix.exists() else Path("/")
    if os.name == "nt" and not prefix.exists():
        check_path = Path(prefix.anchor or ".")
    free_bytes = shutil.disk_usage(check_path).free
    if free_bytes < min_free_bytes:
        raise RuntimeError(f"INSUFFICIENT_DISK_FREE: {free_bytes}")

    if pkg.is_dir():
        manifest = _verify_release_dir(pkg)
    else:
        with tarfile.open(pkg, "r:gz") as archive:
            manifest_member = archive.extractfile("RELEASE_MANIFEST.json")
            if manifest_member is None:
                raise ValueError("RELEASE_MANIFEST_MISSING")
            manifest = json.loads(manifest_member.read().decode("utf-8"))
            if manifest.get("schema") != RELEASE_SCHEMA:
                raise ValueError("RELEASE_SCHEMA_MISMATCH")
            files = manifest.get("files") or {}
            for rel, meta in files.items():
                stream = archive.extractfile(rel)
                if stream is None:
                    raise ValueError(f"MISSING_TAR_FILE: {rel}")
                with stream:
                    if _sha256(stream.read()) != meta.get("sha256"):
                        raise ValueError(f"TAR_SHA_MISMATCH: {rel}")
    gex_venv_python = prefix / "opt" / "xxproject-runtime" / "venv" / "bin" / "python"
    return {
        "ok": True,
        "action": "preflight",
        "release_id": manifest.get("release_id"),
        "source_commit": manifest.get("source_commit"),
        "file_count": manifest.get("file_count"),
        "free_bytes": free_bytes,
        "gex_runtime_venv_present": gex_venv_python.is_file(),
    }


def _install_env_examples(release_dir: Path, prefix: Path) -> list[str]:
    created: list[str] = []
    for src_rel, dst_rel in ENV_TEMPLATES.items():
        src = release_dir / src_rel
        dst = prefix / dst_rel
        if src.is_file() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            if os.name != "nt":
                os.chmod(dst, 0o600 if "llm" in dst.name or "workbench" in dst.name else 0o640)
            created.append(dst_rel)
    return created


def _install_systemd_units(
    release_dir: Path,
    prefix: Path,
    app_root: Path,
    release_id: str,
    *,
    apply_units: bool,
    no_systemd: bool,
) -> tuple[list[str], list[str]]:
    if not apply_units:
        return [], []
    _validate_systemd_prereqs(prefix, apply_units=apply_units, no_systemd=no_systemd)
    unit_src_dir = release_dir / "deploy" / "xxproject_server" / "systemd_units_unified"
    unit_dst_dir = prefix / "etc" / "systemd" / "system"
    unit_dst_dir.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    backed_up_dropins: list[str] = []
    for src in sorted(unit_src_dir.iterdir()):
        if src.is_file() and src.suffix in {".service", ".timer"}:
            dropin_dir = unit_dst_dir / f"{src.name}.d"
            if dropin_dir.is_dir():
                for dropin_file in sorted(p for p in dropin_dir.iterdir() if p.is_file()):
                    backup_dir = app_root / "unit_backups" / release_id / f"{src.name}.d"
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dropin_file), str(backup_dir / dropin_file.name))
                    backed_up_dropins.append(f"{src.name}.d/{dropin_file.name}")
                if not any(dropin_dir.iterdir()):
                    dropin_dir.rmdir()
            dst = unit_dst_dir / src.name
            shutil.copy2(src, dst)
            installed.append(src.name)
    if not no_systemd and os.name != "nt":
        subprocess.run(["systemctl", "daemon-reload"], check=True)
    return installed, backed_up_dropins


def _prune_old_releases(app_root: Path, *, keep_ids: set[str], keep_count: int) -> list[str]:
    releases_dir = app_root / "releases"
    if not releases_dir.is_dir():
        return []
    candidates = [
        d for d in releases_dir.iterdir()
        if d.is_dir() and not d.is_symlink() and not d.name.startswith(".staging-")
    ]
    candidates.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    kept = set(keep_ids)
    for d in candidates:
        if len(kept) >= max(2, keep_count):
            break
        kept.add(d.name)
    removed: list[str] = []
    for d in candidates:
        if d.name not in kept:
            shutil.rmtree(d)
            removed.append(d.name)
    return removed


def cmd_install(
    pkg: Path,
    prefix: Path,
    *,
    apply_units: bool = False,
    no_systemd: bool = False,
    keep_releases: int = 3,
    min_free_bytes: int = 512 * 1024 * 1024,
    allow_window_override: bool = False,
    now_ms: int | None = None,
) -> dict[str, Any]:
    _validate_deploy_window(prefix, allow_window_override=allow_window_override, now_ms=now_ms)
    _validate_systemd_prereqs(prefix, apply_units=apply_units, no_systemd=no_systemd)
    cmd_preflight(pkg, prefix, min_free_bytes=min_free_bytes)
    app_root = _app_root(prefix)
    releases_dir = app_root / "releases"
    releases_dir.mkdir(parents=True, exist_ok=True)

    stage_dir = releases_dir / f".staging-{os.getpid()}-{time.time_ns()}"
    stage_dir.mkdir(parents=True, exist_ok=True)
    try:
        if pkg.is_dir():
            shutil.copytree(pkg, stage_dir, dirs_exist_ok=True)
        else:
            _safe_extract_tar(pkg, stage_dir)
        manifest = _verify_release_dir(stage_dir)
        _run_import_smoke(stage_dir)
        release_id = str(manifest["release_id"])
        target_dir = releases_dir / release_id
        if target_dir.exists():
            shutil.rmtree(stage_dir)
        else:
            os.replace(stage_dir, target_dir)
    except Exception:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
        raise

    pointers = _read_pointers(app_root)
    old_current_id = pointers.get("current")
    prev_id = pointers.get("previous")
    if isinstance(old_current_id, str) and old_current_id and old_current_id != release_id:
        old_dir = releases_dir / old_current_id
        if old_dir.is_dir():
            _switch_link(app_root / "previous", old_dir)
            prev_id = old_current_id

    link_mode = _switch_link(app_root / "current", target_dir)
    _write_json_atomic(_pointers_path(app_root), {"current": release_id, "previous": prev_id})

    created_envs = _install_env_examples(target_dir, prefix)
    units, backed_up_dropins = _install_systemd_units(
        target_dir, prefix, app_root, release_id, apply_units=apply_units, no_systemd=no_systemd
    )
    keep_set = {x for x in (release_id, prev_id) if x}
    pruned = _prune_old_releases(app_root, keep_ids=keep_set, keep_count=keep_releases)

    receipt = {
        "action": "install",
        "at_ms": int(time.time() * 1000),
        "release_id": release_id,
        "previous_release_id": prev_id,
        "source_commit": manifest.get("source_commit"),
        "link_mode": link_mode,
        "created_env_templates": created_envs,
        "installed_units": units,
        "backed_up_dropins": backed_up_dropins,
        "pruned_releases": pruned,
        "ok": True,
    }
    _append_receipt(app_root, receipt)
    return receipt


def cmd_verify(prefix: Path, *, expected_core_version: str = "2.0.6") -> dict[str, Any]:
    app_root = _app_root(prefix)
    cur_dir = _resolve_active_release_dir(app_root, "current")
    if cur_dir is None:
        raise RuntimeError(f"NO_ACTIVE_RELEASE: {app_root}")
    manifest = _verify_release_dir(cur_dir)
    _run_import_smoke(cur_dir)
    actual_core = (manifest.get("module_versions") or {}).get("signal_core")
    if expected_core_version and actual_core != expected_core_version:
        raise RuntimeError(f"CORE_VERSION_MISMATCH: {actual_core} != {expected_core_version}")
    return {
        "ok": True,
        "action": "verify",
        "release_id": manifest.get("release_id"),
        "source_commit": manifest.get("source_commit"),
        "core_version": actual_core,
        "file_count": manifest.get("file_count"),
        "fmz_robot_bundled": False,
        "ai_player_mode": "SHADOW_ONLY",
    }


def cmd_rollback(
    prefix: Path,
    *,
    to_release: str | None = None,
    apply_units: bool = False,
    no_systemd: bool = False,
    allow_window_override: bool = False,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Roll back to previous (or named) release without touching /var/lib or /etc."""
    _validate_deploy_window(prefix, allow_window_override=allow_window_override, now_ms=now_ms)
    _validate_systemd_prereqs(prefix, apply_units=apply_units, no_systemd=no_systemd)
    app_root = _app_root(prefix)
    pointers = _read_pointers(app_root)
    cur_id = pointers.get("current")
    target_id = to_release or pointers.get("previous")
    if not target_id:
        raise RuntimeError("NO_ROLLBACK_TARGET")
    target_dir = app_root / "releases" / str(target_id)
    if not target_dir.is_dir():
        raise RuntimeError(f"ROLLBACK_TARGET_MISSING: {target_dir}")

    manifest = _verify_release_dir(target_dir)
    _run_import_smoke(target_dir)

    prev_after = cur_id if (cur_id and cur_id != target_id and (app_root / "releases" / cur_id).is_dir()) else None
    if prev_after:
        _switch_link(app_root / "previous", app_root / "releases" / prev_after)
    link_mode = _switch_link(app_root / "current", target_dir)
    _write_json_atomic(_pointers_path(app_root), {"current": str(target_id), "previous": prev_after})
    units, backed_up_dropins = _install_systemd_units(
        target_dir, prefix, app_root, str(target_id), apply_units=apply_units, no_systemd=no_systemd
    )

    receipt = {
        "action": "rollback",
        "at_ms": int(time.time() * 1000),
        "rolled_back_from": cur_id,
        "release_id": str(target_id),
        "previous_release_id": prev_after,
        "source_commit": manifest.get("source_commit"),
        "link_mode": link_mode,
        "installed_units": units,
        "backed_up_dropins": backed_up_dropins,
        "runtime_state_preserved": True,
        "etc_configs_preserved": True,
        "ok": True,
    }
    _append_receipt(app_root, receipt)
    return receipt


def cmd_status(prefix: Path) -> dict[str, Any]:
    app_root = _app_root(prefix)
    pointers = _read_pointers(app_root)
    releases_dir = app_root / "releases"
    available = sorted(d.name for d in releases_dir.iterdir() if d.is_dir()) if releases_dir.is_dir() else []
    return {
        "ok": True,
        "app_root": str(app_root),
        "current": pointers.get("current"),
        "previous": pointers.get("previous"),
        "available_releases": available,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--root-prefix", type=Path, default=Path("/"))

    p_pre = sub.add_parser("preflight")
    add_common(p_pre)
    p_pre.add_argument("--package", type=Path, required=True)
    p_pre.add_argument("--min-free-bytes", type=int, default=512 * 1024 * 1024)

    p_inst = sub.add_parser("install")
    add_common(p_inst)
    p_inst.add_argument("--package", type=Path, required=True)
    p_inst.add_argument("--apply-units", action="store_true")
    p_inst.add_argument("--no-systemd", action="store_true")
    p_inst.add_argument("--keep-releases", type=int, default=3)
    p_inst.add_argument("--min-free-bytes", type=int, default=512 * 1024 * 1024)
    p_inst.add_argument("--allow-window-override", action="store_true")

    p_ver = sub.add_parser("verify")
    add_common(p_ver)
    p_ver.add_argument("--expected-core-version", default="2.0.6")

    p_rb = sub.add_parser("rollback")
    add_common(p_rb)
    p_rb.add_argument("--to-release", default=None)
    p_rb.add_argument("--apply-units", action="store_true")
    p_rb.add_argument("--no-systemd", action="store_true")
    p_rb.add_argument("--allow-window-override", action="store_true")

    p_st = sub.add_parser("status")
    add_common(p_st)

    args = parser.parse_args(argv)
    prefix = args.root_prefix.resolve()
    if args.command == "preflight":
        out = cmd_preflight(args.package.resolve(), prefix, min_free_bytes=args.min_free_bytes)
    elif args.command == "install":
        out = cmd_install(
            args.package.resolve(),
            prefix,
            apply_units=args.apply_units,
            no_systemd=args.no_systemd,
            keep_releases=args.keep_releases,
            min_free_bytes=args.min_free_bytes,
            allow_window_override=args.allow_window_override,
        )
    elif args.command == "verify":
        out = cmd_verify(prefix, expected_core_version=args.expected_core_version)
    elif args.command == "rollback":
        out = cmd_rollback(
            prefix,
            to_release=args.to_release,
            apply_units=args.apply_units,
            no_systemd=args.no_systemd,
            allow_window_override=args.allow_window_override,
        )
    elif args.command == "status":
        out = cmd_status(prefix)
    else:
        raise SystemExit(2)

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
