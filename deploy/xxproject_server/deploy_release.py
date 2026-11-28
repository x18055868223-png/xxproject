"""Deploy, verify, and roll back unified xxproject server releases safely.

Strictly separates immutable code releases (``/opt/xxproject/releases/<id>``)
from mutable runtime state (``/var/lib/xxproject/``) and host secrets
(``/etc/signal-audit/``). Supports ``--root-prefix`` and ``--no-systemd`` for
hermetic offline and Linux ``/tmp`` rehearsal drills.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
import datetime as dt
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:
    msvcrt = None  # type: ignore[assignment]

RELEASE_SCHEMA = "xxproject_server_release@1.0.0"
DEPLOY_LOCK_FILENAME = ".deploy.lock"

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
OPTIONAL_EXEC_IMPORT_MODULES = ("astra_execution_coordinator_v1",)

ENV_TEMPLATES = {
    "deploy/signal_producer/signal-producer.env.example": "etc/signal-audit/signal-producer.env",
    "deploy/signal_audit/workbench-v20.env.example": "etc/signal-audit/workbench-v20.env",
    "deploy/signal_audit/signal-audit-llm.env.example": "etc/signal-audit/llm.env",
    "deploy/signal_audit/signal-audit-materialize.env.example": "etc/signal-audit/signal-audit-materialize.env",
    "deploy/kpf_light/astra-kpf-light.env.example": "etc/astra-kpf-light.env",
    "deploy/gexmonitorapi/gexmonitorapi.env.example": "etc/gexmonitorapi.env",
    "deploy/astra_map_data/config.example.json": "opt/astra-map-data/config.json",
}

MANAGED_DAEMON_SERVICES = (
    "gexmonitorapi.service",
    "astra-signal-producer.service",
    "astra-workbench-v20.service",
)

MANAGED_TIMERS = (
    "astra-kpf-light.timer",
    "astra-log-retention.timer",
    "astra-map-data.timer",
    "astra-player-deal.timer",
    "astra-player-prewarm.timer",
    "astra-player-settle.timer",
    "astra-player-sync.timer",
    "astra-research-archive.timer",
    "signal-audit-materialize.timer",
)

WINDOW_GUARD_UNITS = (
    "astra-player-deal.service",
    "astra-player-settle.service",
)


def _chmod_posix(path: Path, mode: int) -> None:
    if os.name != "nt":
        os.chmod(path, mode)


def _chmod_release_tree(release_dir: Path) -> None:
    if os.name == "nt":
        return
    for cur_root, dirnames, filenames in os.walk(release_dir):
        _chmod_posix(Path(cur_root), 0o755)
        for d in dirnames:
            _chmod_posix(Path(cur_root) / d, 0o755)
        for f in filenames:
            _chmod_posix(Path(cur_root) / f, 0o755 if f.endswith(".sh") else 0o644)


def _is_system_root(prefix: Path) -> bool:
    return os.name != "nt" and prefix.resolve() == Path("/")


def _validate_deploy_window(
    prefix: Path,
    *,
    allow_window_override: bool = False,
    now_ms: int | None = None,
    no_systemd: bool = False,
) -> None:
    """Refuse live root switches during 08:00 UTC settlement, 16:00 UTC deal, or active deal/settle units."""
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
    if os.name != "nt" and not no_systemd and Path("/run/systemd/system").is_dir():
        for unit in WINDOW_GUARD_UNITS:
            res = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, check=False)
            if res.stdout.strip() in {"active", "activating"}:
                raise RuntimeError(f"UNSAFE_ACTIVE_WINDOW_UNIT: {unit} ({res.stdout.strip()})")


def _read_workbench_env_token(prefix: Path) -> str:
    env_path = prefix / "etc" / "signal-audit" / "workbench-v20.env"
    if not env_path.is_file():
        return ""
    try:
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("ASTRA_WORKBENCH_TOKEN="):
                val = stripped.split("=", 1)[1].strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in {"'", '"'}:
                    val = val[1:-1]
                return val
    except OSError:
        return ""
    return ""


def _validate_execution_os_isolation(prefix: Path) -> None:
    """Enforce mandatory OS UID/group and token separation before activating execution units on POSIX."""
    import grp
    import pwd

    ctrl_tok_file = prefix / "etc" / "signal-audit" / "exec-control.token"
    if not ctrl_tok_file.is_file():
        raise RuntimeError(f"MISSING_HOST_EXEC_CONTROL_TOKEN: {ctrl_tok_file}")
    ctrl_tok = ctrl_tok_file.read_text(encoding="utf-8").strip()
    if not ctrl_tok:
        raise RuntimeError("EMPTY_EXEC_ISOLATION_TOKEN")
    wb_tok = _read_workbench_env_token(prefix)
    if wb_tok and wb_tok == ctrl_tok:
        raise RuntimeError("UNISOLATED_EXEC_TOKENS_REJECTED")
    legacy_op_file = prefix / "etc" / "signal-audit" / "exec-operator.token"
    if legacy_op_file.is_file():
        op_tok = legacy_op_file.read_text(encoding="utf-8").strip()
        if op_tok and (op_tok == ctrl_tok or (wb_tok and op_tok == wb_tok)):
            raise RuntimeError("UNISOLATED_EXEC_TOKENS_REJECTED")

    try:
        exec_pw = pwd.getpwnam("astra-exec")
    except KeyError as exc:
        raise RuntimeError("MISSING_HOST_EXEC_USER: astra-exec") from exc
    try:
        wb_pw = pwd.getpwnam("astra-workbench")
    except KeyError as exc:
        raise RuntimeError("MISSING_HOST_WORKBENCH_USER: astra-workbench") from exc
    try:
        bitnami_pw = pwd.getpwnam("bitnami")
        if wb_pw.pw_uid == exec_pw.pw_uid:
            raise RuntimeError("UNISOLATED_WORKBENCH_UID_EQUALS_EXEC")
        if wb_pw.pw_uid == bitnami_pw.pw_uid or exec_pw.pw_uid == bitnami_pw.pw_uid:
            raise RuntimeError("UNISOLATED_WORKBENCH_UID_EQUALS_BITNAMI")
        try:
            exec_grp = grp.getgrnam("astra-exec")
            if (
                bitnami_pw.pw_gid == exec_grp.gr_gid
                or wb_pw.pw_gid == exec_grp.gr_gid
                or "bitnami" in (exec_grp.gr_mem or ())
                or "astra-workbench" in (exec_grp.gr_mem or ())
            ):
                raise RuntimeError("UNSAFE_GROUP_MEMBERSHIP_IN_ASTRA_EXEC")
        except KeyError:
            pass
    except KeyError:
        pass


def _validate_systemd_prereqs(
    prefix: Path,
    *,
    apply_units: bool,
    no_systemd: bool,
    with_execution: bool = False,
) -> None:
    """Check host runtime prerequisites before mutating release symlinks or unit files."""
    if not apply_units or no_systemd or os.name == "nt":
        return
    gex_venv_python = prefix / "opt" / "xxproject-runtime" / "venv" / "bin" / "python"
    if not gex_venv_python.is_file():
        raise RuntimeError(f"MISSING_HOST_GEX_RUNTIME_VENV: {gex_venv_python}")
    exec_active = bool(with_execution or (prefix / "etc" / "systemd" / "system" / "astra-execution-coordinator.service").exists())
    if exec_active:
        _validate_execution_os_isolation(prefix)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _app_root(prefix: Path) -> Path:
    return prefix / "opt" / "xxproject"


def _read_lock_holder(lock_path: Path) -> str:
    try:
        text = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return "active deploy in progress"
    return text or "active deploy in progress"


@contextmanager
def _deploy_lock(app_root: Path, *, action: str) -> Iterator[Path]:
    """Acquire a non-blocking exclusive host lock serializing installs and rollbacks."""
    app_root.mkdir(parents=True, exist_ok=True)
    _chmod_posix(app_root.parent, 0o755)
    _chmod_posix(app_root, 0o755)
    lock_path = app_root / DEPLOY_LOCK_FILENAME
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    fd = os.open(str(lock_path), flags, 0o644)
    _chmod_posix(lock_path, 0o644)
    locked = False
    try:
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError as exc:
                raise RuntimeError(f"DEPLOY_LOCK_HELD: {lock_path} ({_read_lock_holder(lock_path)})") from exc
        elif msvcrt is not None:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                locked = True
            except OSError as exc:
                raise RuntimeError(f"DEPLOY_LOCK_HELD: {lock_path} ({_read_lock_holder(lock_path)})") from exc
        payload = (
            json.dumps(
                {
                    "pid": os.getpid(),
                    "action": action,
                    "started_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "app_root": str(app_root),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, payload)
        os.ftruncate(fd, len(payload))
        os.fsync(fd)
        yield lock_path
    finally:
        try:
            if locked:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                elif msvcrt is not None:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(fd)


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
    _chmod_posix(path.parent, 0o755)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    _chmod_posix(tmp, 0o644)
    os.replace(tmp, path)


def _append_receipt(app_root: Path, receipt: dict[str, Any]) -> None:
    ledger = app_root / "deployments.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    _chmod_posix(ledger.parent, 0o755)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    _chmod_posix(ledger, 0o644)


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
        or inv.get("execution_default_mode", "SHADOW_ONLY") != "SHADOW_ONLY"
        or inv.get("execution_live_writes_enabled", False) is not False
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

    dossier_dir = release_dir / "deploy" / "player_dossier"
    plan_files = sorted(dossier_dir.glob("plan.daily_r16.*.json"))
    if not plan_files:
        raise ValueError("PLAYER_PLAN_MISSING")
    plans_by_name: dict[str, dict[str, Any]] = {}
    for pf in plan_files:
        p_obj = json.loads(pf.read_text(encoding="utf-8-sig"))
        if p_obj.get("mode") != "SHADOW_ONLY" or p_obj.get("execution_allowed") is not False:
            raise ValueError(f"PLAYER_PLAN_SHADOW_ONLY_FAILED: {pf.name}")
        plans_by_name[pf.name] = p_obj
    active_plan_name = (
        "plan.daily_r16.v3_p5.json" if "plan.daily_r16.v3_p5.json" in plans_by_name
        else "plan.daily_r16.v2.json"
    )
    if active_plan_name not in plans_by_name:
        raise ValueError(f"PLAYER_PLAN_MISSING: {active_plan_name}")
    core_text = (release_dir / "signal_core/neutral_regulation_demo_fmz.py").read_text(encoding="utf-8")
    if '"read_only_demo": True' not in core_text:
        raise ValueError("SIGNAL_CORE_READ_ONLY_FAILED")
    return manifest


def _run_import_smoke(release_dir: Path) -> None:
    tools_dir = release_dir / "tools"
    modules = list(CORE_IMPORT_MODULES) + [
        mod for mod in OPTIONAL_EXEC_IMPORT_MODULES if (tools_dir / f"{mod}.py").is_file()
    ]
    script = "; ".join(f"import {mod}" for mod in modules)
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
        "worktree_state": manifest.get("worktree_state"),
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
            _chmod_posix(dst.parent, 0o755)
            shutil.copy2(src, dst)
            if os.name != "nt":
                if dst.name.endswith(".json"):
                    mode = 0o644
                elif "llm" in dst.name or "workbench" in dst.name:
                    mode = 0o600
                else:
                    mode = 0o640
                os.chmod(dst, mode)
            created.append(dst_rel)
    return created


def _sync_workbench_frontend_link(prefix: Path, app_root: Path) -> dict[str, Any]:
    """Point /opt/signal-audit/workbench-v3 at current/deploy/astra_workbench_v3 for Nginx static serving."""
    frontend_target = app_root / "current" / "deploy" / "astra_workbench_v3"
    if not (frontend_target / "index.html").is_file():
        return {"synced": False, "reason": "frontend_index_missing"}
    audit_dir = prefix / "opt" / "signal-audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    _chmod_posix(audit_dir.parent, 0o755)
    _chmod_posix(audit_dir, 0o755)
    link_path = audit_dir / "workbench-v3"
    backup_path = None
    if link_path.exists() and not link_path.is_symlink():
        backup = link_path.with_name(f"workbench-v3.pre-release-backup-{int(time.time())}")
        shutil.move(str(link_path), str(backup))
        backup_path = str(backup)
    link_mode = _switch_link(link_path, frontend_target)
    if not (link_path / "index.html").is_file():
        raise RuntimeError(f"WORKBENCH_FRONTEND_LINK_BROKEN: {link_path}")
    return {
        "synced": True,
        "link_mode": link_mode,
        "path": str(link_path),
        "target": str(frontend_target),
        "backup": backup_path,
    }


def _chown_safe(path: Path, *, user: str | None = None, group: str | None = None) -> None:
    kwargs: dict[str, str] = {}
    if user is not None:
        kwargs["user"] = user
    if group is not None:
        kwargs["group"] = group
    if not kwargs:
        return
    try:
        shutil.chown(path, **kwargs)
    except (OSError, LookupError):
        pass


def _add_mode_bits(path: Path, extra_bits: int) -> None:
    try:
        cur = path.stat().st_mode & 0o7777
    except OSError:
        return
    _chmod_posix(path, cur | extra_bits)


def _walk_tree_no_symlinks(root: Path, *, on_dir: Any, on_file: Any) -> None:
    if root.is_symlink():
        return
    if root.is_dir():
        on_dir(root)
        for cur_root, dirnames, filenames in os.walk(root, followlinks=False):
            cur_p = Path(cur_root)
            dirnames[:] = [d for d in dirnames if not (cur_p / d).is_symlink()]
            for d in dirnames:
                on_dir(cur_p / d)
            for f in filenames:
                fp = cur_p / f
                if not fp.is_symlink():
                    on_file(fp)
    elif root.is_file():
        on_file(root)


def _resolve_research_group() -> str:
    try:
        import grp

        grp.getgrnam("research")
        return "research"
    except Exception:
        return "bitnami"


def repair_shadow_dossier_permissions(
    dossier_root: Path,
    *,
    group: str | None = None,
) -> dict[str, Any]:
    """Idempotently ensure dossier directories (2750) and files (g+r, o-rwx) are shared-group readable."""
    if not dossier_root.exists():
        return {"ok": True, "exists": False, "dossier_root": str(dossier_root), "dirs": 0, "files": 0}
    eff_group = group or _resolve_research_group()
    counts = {"dirs": 0, "files": 0}

    def on_dir(d: Path) -> None:
        _chown_safe(d, group=eff_group)
        _chmod_posix(d, 0o2750)
        counts["dirs"] += 1

    def on_file(f: Path) -> None:
        _chown_safe(f, group=eff_group)
        try:
            cur = f.stat().st_mode & 0o7777
            _chmod_posix(f, (cur & 0o700) | 0o040)
        except OSError:
            pass
        counts["files"] += 1

    _walk_tree_no_symlinks(dossier_root, on_dir=on_dir, on_file=on_file)
    return {
        "ok": True,
        "exists": True,
        "dossier_root": str(dossier_root),
        "group": eff_group,
        **counts,
    }


def _migrate_exec_shared_permissions(prefix: Path) -> None:
    """Migrate existing /var/lib/xxproject/{workbench,llm} ownership/permissions for astra-workbench."""
    research_grp = _resolve_research_group()

    llm_dir = prefix / "var" / "lib" / "xxproject" / "llm"
    if llm_dir.is_dir():
        _walk_tree_no_symlinks(
            llm_dir,
            on_dir=lambda d: (_chown_safe(d, group=research_grp), _chmod_posix(d, 0o2770)),
            on_file=lambda f: (_chown_safe(f, group=research_grp), _chmod_posix(f, 0o660)),
        )
        _chown_safe(llm_dir, user="bitnami", group=research_grp)

    wb_dir = prefix / "var" / "lib" / "xxproject" / "workbench"
    if wb_dir.is_dir():
        _chown_safe(wb_dir, user="bitnami", group=research_grp)
        _chmod_posix(wb_dir, 0o2775)
        for child in sorted(wb_dir.iterdir()):
            if child.is_symlink():
                continue
            name = child.name
            if name == "sessions.json":
                _chown_safe(child, user="astra-workbench", group="astra-workbench")
                _chmod_posix(child, 0o600)
            elif name == "deribit_cooldown.json":
                _chown_safe(child, group=research_grp)
                _chmod_posix(child, 0o664)
            elif name == "shadow_dossiers":
                repair_shadow_dossier_permissions(child, group=research_grp)
            elif name.startswith(("snapshots.jsonl", "decisions.jsonl", "signal_review.jsonl")):
                _walk_tree_no_symlinks(
                    child,
                    on_dir=lambda d: (_chown_safe(d, group=research_grp), _add_mode_bits(d, 0o050)),
                    on_file=lambda f: (_chown_safe(f, group=research_grp), _add_mode_bits(f, 0o040)),
                )
            else:
                _walk_tree_no_symlinks(
                    child,
                    on_dir=lambda d: (_chown_safe(d, user="astra-workbench", group=research_grp), _chmod_posix(d, 0o2750)),
                    on_file=lambda f: (_chown_safe(f, user="astra-workbench", group=research_grp), _chmod_posix(f, 0o640)),
                )


def _restore_pre_execution_workbench_permissions(prefix: Path) -> list[str]:
    """Restore bitnami ownership on /var/lib/xxproject/workbench & archive public_status.json when rolling back across --with-execution."""
    changes: list[str] = []
    wb_dir = prefix / "var" / "lib" / "xxproject" / "workbench"
    if wb_dir.is_dir():
        _chown_safe(wb_dir, user="bitnami", group="bitnami")
        _chmod_posix(wb_dir, 0o755)
        changes.append("restored:var/lib/xxproject/workbench")
        for child in sorted(wb_dir.iterdir()):
            if child.is_symlink():
                continue
            name = child.name
            if name == "sessions.json":
                _chown_safe(child, user="bitnami", group="bitnami")
                _chmod_posix(child, 0o600)
                changes.append("restored:var/lib/xxproject/workbench/sessions.json")
            elif name == "deribit_cooldown.json":
                _chown_safe(child, user="bitnami", group="bitnami")
                _chmod_posix(child, 0o644)
                changes.append("restored:var/lib/xxproject/workbench/deribit_cooldown.json")
            else:
                _walk_tree_no_symlinks(
                    child,
                    on_dir=lambda d: (_chown_safe(d, user="bitnami", group="bitnami"), _chmod_posix(d, 0o755)),
                    on_file=lambda f: (_chown_safe(f, user="bitnami", group="bitnami"), _chmod_posix(f, 0o644)),
                )
    llm_dir = prefix / "var" / "lib" / "xxproject" / "llm"
    if llm_dir.is_dir():
        _walk_tree_no_symlinks(
            llm_dir,
            on_dir=lambda d: (_chown_safe(d, user="bitnami", group="bitnami"), _chmod_posix(d, 0o755)),
            on_file=lambda f: (_chown_safe(f, user="bitnami", group="bitnami"), _chmod_posix(f, 0o644)),
        )
        changes.append("restored:var/lib/xxproject/llm")
    pub_status = prefix / "var" / "lib" / "xxproject" / "execution" / "public_status.json"
    if pub_status.is_file() and not pub_status.is_symlink():
        archived = pub_status.with_name(f"public_status.pre_rollback.{int(time.time())}.json")
        pub_status.replace(archived)
        changes.append(f"archived:{archived.name}")
    return changes


def _ensure_materialize_bitnami_writable(prefix: Path, *, with_execution: bool = False) -> None:
    if os.name == "nt" or not _is_system_root(prefix):
        return
    import pwd

    try:
        pwd.getpwnam("bitnami")
        has_bitnami = True
    except Exception:
        has_bitnami = False
    exec_active = bool(with_execution or (prefix / "etc" / "systemd" / "system" / "astra-execution-coordinator.service").exists())
    if has_bitnami:
        for rel in (
            "opt/signal-audit",
            "opt/signal-audit/signal_cards",
            "var/lib/xxproject/signal_audit",
            "opt/astra-map-data",
            "opt/astra-map-data/state",
        ):
            d = prefix / rel
            d.mkdir(parents=True, exist_ok=True)
            _chmod_posix(d, 0o755)
            _chown_safe(d, user="bitnami", group="bitnami")
        for rel in (
            "var/lib/xxproject/signal_native",
            "var/lib/xxproject/signal_native/logs",
            "var/lib/xxproject/signal_native/state",
            "var/lib/xxproject/signal_native/retained",
            "var/lib/xxproject/workbench",
            "var/lib/xxproject/llm",
            "var/lib/xxproject/gexmonitorapi",
            "var/lib/xxproject/research_archive",
            "var/lib/astra-kpf-light",
        ):
            d = prefix / rel
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                _chmod_posix(d, 0o755)
                _chown_safe(d, user="bitnami", group="bitnami")
    if exec_active:
        _migrate_exec_shared_permissions(prefix)
    exec_dir = prefix / "var" / "lib" / "xxproject" / "execution"
    exec_dir.mkdir(parents=True, exist_ok=True)
    _chmod_posix(exec_dir, 0o755)
    _chown_safe(exec_dir, user="astra-exec", group="astra-exec")


def _active_release_ids_in_use(app_root: Path, prefix: Path) -> set[str]:
    in_use: set[str] = set()
    if os.name == "nt" or not _is_system_root(prefix) or not Path("/run/systemd/system").is_dir():
        return in_use
    releases_root = (app_root / "releases").resolve()
    for unit in (*MANAGED_DAEMON_SERVICES, "astra-execution-coordinator.service"):
        show = subprocess.run(
            ["systemctl", "show", unit, "-p", "MainPID", "--value"],
            capture_output=True,
            text=True,
            check=False,
        )
        raw_pid = show.stdout.strip()
        if not raw_pid.isdigit() or int(raw_pid) <= 0:
            continue
        cwd_link = Path(f"/proc/{int(raw_pid)}/cwd")
        try:
            resolved = cwd_link.resolve(strict=True)
            rel = resolved.relative_to(releases_root)
            if rel.parts:
                in_use.add(rel.parts[0])
        except (OSError, ValueError):
            continue
    return in_use


def _restart_managed_daemons(
    app_root: Path,
    release_id: str,
    *,
    prefix: Path | None = None,
    with_execution: bool = False,
) -> tuple[list[str], dict[str, int]]:
    expected_release = (app_root / "releases" / release_id).resolve()
    root_prefix = Path("/") if prefix is None else prefix
    units_to_restart = list(MANAGED_DAEMON_SERVICES)
    has_exec_in_release = (
        expected_release / "deploy" / "xxproject_server" / "systemd_units_execution" / "astra-execution-coordinator.service"
    ).is_file()
    if has_exec_in_release and (
        with_execution or (root_prefix / "etc" / "systemd" / "system" / "astra-execution-coordinator.service").exists()
    ):
        units_to_restart.append("astra-execution-coordinator.service")
    restarted: list[str] = []
    verified_pids: dict[str, int] = {}
    for unit in units_to_restart:
        subprocess.run(["systemctl", "enable", unit], check=True)
        subprocess.run(["systemctl", "restart", unit], check=True)
        st = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, check=False)
        if st.stdout.strip() != "active":
            raise RuntimeError(f"SERVICE_NOT_ACTIVE_AFTER_RESTART: {unit} ({st.stdout.strip()})")
        show = subprocess.run(
            ["systemctl", "show", unit, "-p", "MainPID", "--value"],
            capture_output=True,
            text=True,
            check=True,
        )
        raw_pid = show.stdout.strip()
        if not raw_pid.isdigit() or int(raw_pid) <= 0:
            raise RuntimeError(f"SERVICE_MAINPID_MISSING: {unit} ({raw_pid!r})")
        pid = int(raw_pid)
        proc_cwd = Path(f"/proc/{pid}/cwd").resolve(strict=True)
        if proc_cwd != expected_release and expected_release not in proc_cwd.parents:
            raise RuntimeError(f"SERVICE_CWD_MISMATCH: {unit} pid={pid} cwd={proc_cwd} expected={expected_release}")
        restarted.append(unit)
        verified_pids[unit] = pid
    return restarted, verified_pids


def _sync_managed_timers(installed_units: list[str], release_dir: Path) -> list[str]:
    synced: list[str] = []
    units_dir = release_dir / "deploy" / "xxproject_server" / "systemd_units_unified"
    for timer in sorted(u for u in installed_units if u.endswith(".timer")):
        subprocess.run(["systemctl", "enable", timer], check=True)
        cur = subprocess.run(["systemctl", "is-active", timer], capture_output=True, text=True, check=False)
        active_state = cur.stdout.strip()
        unit_file = units_dir / timer
        is_monotonic = unit_file.is_file() and ("OnUnitInactiveSec=" in unit_file.read_text(encoding="utf-8"))
        if active_state != "active":
            subprocess.run(["systemctl", "start", timer], check=True)
        elif is_monotonic:
            # Re-arm OnBootSec/OnUnitInactiveSec without spuriously re-triggering active OnCalendar deal/settle timers.
            subprocess.run(["systemctl", "restart", timer], check=True)
        post = subprocess.run(["systemctl", "is-active", timer], capture_output=True, text=True, check=False)
        post_state = post.stdout.strip()
        if post_state != "active":
            raise RuntimeError(f"TIMER_NOT_ACTIVE_AFTER_SYNC: {timer} ({post_state})")
        synced.append(timer)
    return synced


def _install_systemd_units(
    release_dir: Path,
    prefix: Path,
    app_root: Path,
    release_id: str,
    *,
    apply_units: bool,
    no_systemd: bool,
    with_execution: bool = False,
) -> tuple[list[str], list[str]]:
    if not apply_units:
        return [], []
    _validate_systemd_prereqs(prefix, apply_units=apply_units, no_systemd=no_systemd, with_execution=with_execution)
    unit_src_dir = release_dir / "deploy" / "xxproject_server" / "systemd_units_unified"
    unit_dst_dir = prefix / "etc" / "systemd" / "system"
    unit_dst_dir.mkdir(parents=True, exist_ok=True)
    exec_active = bool(with_execution or (unit_dst_dir / "astra-execution-coordinator.service").exists())
    installed: list[str] = []
    backed_up_dropins: list[str] = []
    for src in sorted(unit_src_dir.iterdir()):
        if src.is_file() and src.suffix in {".service", ".timer"}:
            dropin_dir = unit_dst_dir / f"{src.name}.d"
            if dropin_dir.is_dir():
                for dropin_file in sorted(p for p in dropin_dir.iterdir() if p.is_file()):
                    if exec_active and src.name == "astra-workbench-v20.service" and dropin_file.name == "60-exec-control.conf":
                        continue
                    backup_dir = app_root / "unit_backups" / release_id / f"{src.name}.d"
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dropin_file), str(backup_dir / dropin_file.name))
                    backed_up_dropins.append(f"{src.name}.d/{dropin_file.name}")
                if not any(dropin_dir.iterdir()):
                    dropin_dir.rmdir()
            dst = unit_dst_dir / src.name
            shutil.copy2(src, dst)
            installed.append(src.name)
    exec_src_dir = release_dir / "deploy" / "xxproject_server" / "systemd_units_execution"
    coord_unit_src = exec_src_dir / "astra-execution-coordinator.service"
    wb_dropin_dst = unit_dst_dir / "astra-workbench-v20.service.d" / "60-exec-control.conf"
    if exec_active and coord_unit_src.is_file():
        shutil.copy2(coord_unit_src, unit_dst_dir / "astra-execution-coordinator.service")
        installed.append("astra-execution-coordinator.service")
        wb_dropin_src = exec_src_dir / "60-exec-control.conf"
        if wb_dropin_src.is_file():
            wb_dropin_dir = unit_dst_dir / "astra-workbench-v20.service.d"
            wb_dropin_dir.mkdir(parents=True, exist_ok=True)
            _chmod_posix(wb_dropin_dir, 0o755)
            shutil.copy2(wb_dropin_src, wb_dropin_dir / "60-exec-control.conf")
        exec_env_src = exec_src_dir / "exec-coordinator.env.example"
        exec_env_dst = prefix / "etc" / "signal-audit" / "exec-coordinator.env"
        if exec_env_src.is_file() and not exec_env_dst.exists():
            exec_env_dst.parent.mkdir(parents=True, exist_ok=True)
            _chmod_posix(exec_env_dst.parent, 0o755)
            shutil.copy2(exec_env_src, exec_env_dst)
            _chmod_posix(exec_env_dst, 0o600)
    elif exec_active and not with_execution and not coord_unit_src.is_file():
        # Rolling back to a pre-execution release: cleanly deactivate and remove orphaned execution unit & drop-in.
        if not no_systemd and os.name != "nt" and _is_system_root(prefix):
            subprocess.run(["systemctl", "stop", "astra-execution-coordinator.service"], check=False)
            subprocess.run(["systemctl", "disable", "astra-execution-coordinator.service"], check=False)
        coord_dst = unit_dst_dir / "astra-execution-coordinator.service"
        if coord_dst.exists():
            coord_dst.unlink()
            backed_up_dropins.append("removed:astra-execution-coordinator.service")
        if wb_dropin_dst.exists():
            wb_dropin_dst.unlink()
            backed_up_dropins.append("removed:astra-workbench-v20.service.d/60-exec-control.conf")
            if wb_dropin_dst.parent.is_dir() and not any(wb_dropin_dst.parent.iterdir()):
                wb_dropin_dst.parent.rmdir()
        backed_up_dropins.extend(_restore_pre_execution_workbench_permissions(prefix))
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


class _UnixControlHTTPConnection(http.client.HTTPConnection):
    def __init__(self, sock_path: str, timeout: float = 5.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self._sock_path = sock_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._sock_path)
        self.sock = sock


def _load_exec_control_token(prefix: Path, token_file: Path | None = None) -> str:
    candidates: list[Path] = []
    if token_file is not None:
        candidates.append(token_file)
    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if cred_dir:
        candidates.append(Path(cred_dir) / "exec_control_token")
    env_file = os.environ.get("ASTRA_EXEC_CONTROL_TOKEN_FILE")
    if env_file:
        candidates.append(Path(env_file))
    candidates.append(prefix / "etc/signal-audit/exec-control.token")
    for cand in candidates:
        try:
            if cand.is_file():
                val = cand.read_text(encoding="utf-8").strip()
                if val:
                    return val
        except OSError:
            continue
    return ""


def _http_get_control_json(url: str, *, token: str, timeout_s: float = 5.0) -> tuple[int, Any]:
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    if url.startswith("unix://"):
        sock_part = url[len("unix://"):]
        if ":" in sock_part and not (len(sock_part) >= 2 and sock_part[1] == ":" and sock_part.endswith(".sock")):
            sock_path, req_path = sock_part.split(":", 1)
        else:
            sock_path, req_path = sock_part, "/health-fresh"
        if not req_path.startswith("/"):
            req_path = "/" + req_path
        conn = _UnixControlHTTPConnection(sock_path, timeout=timeout_s)
        try:
            conn.request("GET", req_path, headers=headers)
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8")
            return int(resp.status), json.loads(raw)
        finally:
            conn.close()
    target = url.rstrip("/")
    if not target.endswith("/health-fresh"):
        target = f"{target}/health-fresh"
    req = Request(target, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            return int(getattr(resp, "status", 200)), json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return int(exc.code), json.loads(body)
        except json.JSONDecodeError:
            return int(exc.code), {"ok": False, "error": body}


def _is_clean_settled_post_rollback_store(db_path: Path) -> bool:
    """True only when a retained post-rollback SQLite DB proves 0 unsettled episodes and 0 open/unknown commands."""
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=2.0)
    except Exception:
        return False
    try:
        unsettled = int(conn.execute("SELECT COUNT(*) FROM episodes WHERE cashflow_settled=0").fetchone()[0])
        open_cmd = int(
            conn.execute(
                "SELECT COUNT(*) FROM commands WHERE status IN ('PREPARED_UNKNOWN','SUBMIT_UNKNOWN','OPEN','CANCEL_UNKNOWN')"
            ).fetchone()[0]
        )
        return unsettled == 0 and open_cmd == 0
    except Exception:
        return False
    finally:
        conn.close()


def _has_no_in_flight_commands(db_path: Path) -> bool:
    """True only when SQLite proves 0 commands in UNKNOWN/OPEN states."""
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=2.0)
    except Exception:
        return False
    try:
        open_cmd = int(
            conn.execute(
                "SELECT COUNT(*) FROM commands WHERE status IN ('PREPARED_UNKNOWN','SUBMIT_UNKNOWN','OPEN','CANCEL_UNKNOWN')"
            ).fetchone()[0]
        )
        return open_cmd == 0
    except Exception:
        return False
    finally:
        conn.close()


def _check_execution_deploy_safety(
    prefix: Path,
    *,
    exec_control_url: str | None = None,
    exec_control_token_file: Path | None = None,
    no_systemd: bool = False,
    allow_active_episode_hotfix: bool = False,
) -> dict[str, Any] | None:
    """Fail-closed pre-deploy / pre-rollback safety check against live execution coordinator /health-fresh."""
    sock = Path("/run/astra-exec/control.sock") if _is_system_root(prefix) else (prefix / "run" / "astra-exec" / "control.sock")
    db_path = prefix / "var" / "lib" / "xxproject" / "execution" / "exec_store.sqlite"
    pub_status_path = prefix / "var" / "lib" / "xxproject" / "execution" / "public_status.json"
    exec_unit_path = prefix / "etc" / "systemd" / "system" / "astra-execution-coordinator.service"
    has_live_footprint = bool(sock.exists() or pub_status_path.exists() or exec_unit_path.exists())
    has_footprint = has_live_footprint or db_path.exists()
    url = exec_control_url or os.environ.get("ASTRA_EXEC_CONTROL_URL")
    if not url and not no_systemd and sock.exists():
        url = f"unix://{sock}"
    if not url:
        if has_footprint:
            # Post-rollback retained exec_store.sqlite with no coordinator unit/socket/public_status is safe ONLY if DB proves completely settled.
            if (not has_live_footprint) and db_path.is_file() and _is_clean_settled_post_rollback_store(db_path):
                return {"ok": True, "safe_for_deploy": True, "post_rollback_settled_db": True}
            raise RuntimeError("UNSAFE_EXECUTION_STATE: execution_coordinator_unreachable_with_active_execution_footprint")
        return None
    token = _load_exec_control_token(prefix, exec_control_token_file)
    try:
        status, data = _http_get_control_json(url, token=token)
    except Exception as exc:
        raise RuntimeError(f"UNSAFE_EXECUTION_STATE: health_fresh_unreachable:{exc}") from exc
    if status != 200 or not isinstance(data, dict) or data.get("ok") is not True:
        raise RuntimeError(f"UNSAFE_EXECUTION_STATE: health_fresh_failed status={status} payload={data}")
    if (
        data.get("safe_for_deploy") is not True
        or int(data.get("open_positions_count") or 0) > 0
        or int(data.get("open_orders_count") or 0) > 0
        or int(data.get("active_or_unknown_commands_count") or 0) > 0
        or bool(data.get("active_unsettled_episode"))
    ):
        if (
            allow_active_episode_hotfix
            and type(data.get("active_or_unknown_commands_count")) is int
            and data["active_or_unknown_commands_count"] == 0
            and type(data.get("open_orders_count")) is int
            and data["open_orders_count"] == 0
            and db_path.is_file()
            and _has_no_in_flight_commands(db_path)
        ):
            return {**data, "active_episode_hotfix_override": True}
        raise RuntimeError(f"UNSAFE_EXECUTION_STATE: active_live_exposure_or_commands payload={data}")
    return data


def _validate_clean_release_manifest(
    manifest: dict[str, Any],
    *,
    prefix: Path,
    allow_dirty_release: bool = False,
    strict_clean: bool | None = None,
) -> None:
    enforce = _is_system_root(prefix) if strict_clean is None else bool(strict_clean)
    if enforce and not allow_dirty_release and manifest.get("worktree_state") != "clean":
        raise RuntimeError(
            f"DIRTY_RELEASE_PACKAGE_REJECTED: worktree_state={manifest.get('worktree_state')!r} (rebuild from clean commit or pass --allow-dirty-release)"
        )


def cmd_install(
    pkg: Path,
    prefix: Path,
    *,
    apply_units: bool = False,
    no_systemd: bool = False,
    restart_services: bool = True,
    with_execution: bool = False,
    keep_releases: int = 3,
    min_free_bytes: int = 512 * 1024 * 1024,
    allow_window_override: bool = False,
    allow_dirty_release: bool = False,
    allow_active_episode_hotfix: bool = False,
    strict_clean: bool | None = None,
    now_ms: int | None = None,
    exec_control_url: str | None = None,
    exec_control_token_file: Path | None = None,
) -> dict[str, Any]:
    _validate_deploy_window(
        prefix, allow_window_override=allow_window_override, now_ms=now_ms, no_systemd=no_systemd
    )
    _validate_systemd_prereqs(prefix, apply_units=apply_units, no_systemd=no_systemd, with_execution=with_execution)
    app_root = _app_root(prefix)
    with _deploy_lock(app_root, action="install") as lock_path:
        exec_safety = _check_execution_deploy_safety(
            prefix,
            exec_control_url=exec_control_url,
            exec_control_token_file=exec_control_token_file,
            no_systemd=no_systemd,
            allow_active_episode_hotfix=allow_active_episode_hotfix,
        )
        preflight = cmd_preflight(pkg, prefix, min_free_bytes=min_free_bytes)
        _validate_clean_release_manifest(
            preflight, prefix=prefix, allow_dirty_release=allow_dirty_release, strict_clean=strict_clean
        )
        releases_dir = app_root / "releases"
        releases_dir.mkdir(parents=True, exist_ok=True)
        _chmod_posix(app_root.parent, 0o755)
        _chmod_posix(app_root, 0o755)
        _chmod_posix(releases_dir, 0o755)

        stage_dir = releases_dir / f".staging-{os.getpid()}-{time.time_ns()}"
        stage_dir.mkdir(parents=True, exist_ok=True)
        _chmod_posix(stage_dir, 0o755)
        try:
            if pkg.is_dir():
                shutil.copytree(pkg, stage_dir, dirs_exist_ok=True)
            else:
                _safe_extract_tar(pkg, stage_dir)
            _chmod_release_tree(stage_dir)
            manifest = _verify_release_dir(stage_dir)
            _run_import_smoke(stage_dir)
            release_id = str(manifest["release_id"])
            target_dir = releases_dir / release_id
            if target_dir.exists():
                shutil.rmtree(stage_dir)
            else:
                os.replace(stage_dir, target_dir)
            _chmod_posix(target_dir, 0o755)
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
        frontend_link = _sync_workbench_frontend_link(prefix, app_root)

        created_envs = _install_env_examples(target_dir, prefix)
        units, backed_up_dropins = _install_systemd_units(
            target_dir,
            prefix,
            app_root,
            release_id,
            apply_units=apply_units,
            no_systemd=no_systemd,
            with_execution=with_execution,
        )
        if apply_units and not no_systemd:
            _ensure_materialize_bitnami_writable(prefix, with_execution=with_execution)
        restarted_services: list[str] = []
        verified_service_pids: dict[str, int] = {}
        synced_timers: list[str] = []
        if apply_units and restart_services and not no_systemd and _is_system_root(prefix):
            restarted_services, verified_service_pids = _restart_managed_daemons(
                app_root, release_id, prefix=prefix, with_execution=with_execution
            )
            synced_timers = _sync_managed_timers(units, target_dir)
        keep_set = {x for x in (release_id, prev_id) if x} | _active_release_ids_in_use(app_root, prefix)
        pruned = _prune_old_releases(app_root, keep_ids=keep_set, keep_count=keep_releases)

        receipt = {
            "action": "install",
            "at_ms": int(time.time() * 1000),
            "deploy_lock": str(lock_path),
            "release_id": release_id,
            "previous_release_id": prev_id,
            "source_commit": manifest.get("source_commit"),
            "worktree_state": manifest.get("worktree_state"),
            "link_mode": link_mode,
            "frontend_link": frontend_link,
            "created_env_templates": created_envs,
            "installed_units": units,
            "backed_up_dropins": backed_up_dropins,
            "restarted_services": restarted_services,
            "verified_service_pids": verified_service_pids,
            "synced_timers": synced_timers,
            "pruned_releases": pruned,
            "ok": True,
        }
        if isinstance(exec_safety, dict) and exec_safety.get("active_episode_hotfix_override") is True:
            receipt["active_episode_hotfix_override"] = True
        _append_receipt(app_root, receipt)
        return receipt


def cmd_verify(prefix: Path, *, expected_core_version: str = "2.0.7") -> dict[str, Any]:
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
        "worktree_state": manifest.get("worktree_state"),
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
    restart_services: bool = True,
    with_execution: bool = False,
    allow_window_override: bool = False,
    now_ms: int | None = None,
    exec_control_url: str | None = None,
    exec_control_token_file: Path | None = None,
) -> dict[str, Any]:
    """Roll back to previous (or named) release without touching /var/lib or /etc."""
    _validate_deploy_window(
        prefix, allow_window_override=allow_window_override, now_ms=now_ms, no_systemd=no_systemd
    )
    _validate_systemd_prereqs(prefix, apply_units=apply_units, no_systemd=no_systemd, with_execution=with_execution)
    app_root = _app_root(prefix)
    with _deploy_lock(app_root, action="rollback") as lock_path:
        _check_execution_deploy_safety(
            prefix,
            exec_control_url=exec_control_url,
            exec_control_token_file=exec_control_token_file,
            no_systemd=no_systemd,
        )
        pointers = _read_pointers(app_root)
        cur_id = pointers.get("current")
        target_id = to_release or pointers.get("previous")
        if not target_id:
            raise RuntimeError("NO_ROLLBACK_TARGET")
        target_dir = app_root / "releases" / str(target_id)
        if not target_dir.is_dir():
            raise RuntimeError(f"ROLLBACK_TARGET_MISSING: {target_dir}")

        _chmod_release_tree(target_dir)
        manifest = _verify_release_dir(target_dir)
        _run_import_smoke(target_dir)

        prev_after = cur_id if (cur_id and cur_id != target_id and (app_root / "releases" / cur_id).is_dir()) else None
        if prev_after:
            _switch_link(app_root / "previous", app_root / "releases" / prev_after)
        link_mode = _switch_link(app_root / "current", target_dir)
        _write_json_atomic(_pointers_path(app_root), {"current": str(target_id), "previous": prev_after})
        frontend_link = _sync_workbench_frontend_link(prefix, app_root)
        units, backed_up_dropins = _install_systemd_units(
            target_dir,
            prefix,
            app_root,
            str(target_id),
            apply_units=apply_units,
            no_systemd=no_systemd,
            with_execution=with_execution,
        )
        if apply_units and not no_systemd:
            _ensure_materialize_bitnami_writable(prefix, with_execution=with_execution)
        restarted_services: list[str] = []
        verified_service_pids: dict[str, int] = {}
        synced_timers: list[str] = []
        if apply_units and restart_services and not no_systemd and _is_system_root(prefix):
            restarted_services, verified_service_pids = _restart_managed_daemons(
                app_root, str(target_id), prefix=prefix, with_execution=with_execution
            )
            synced_timers = _sync_managed_timers(units, target_dir)

        receipt = {
            "action": "rollback",
            "at_ms": int(time.time() * 1000),
            "deploy_lock": str(lock_path),
            "rolled_back_from": cur_id,
            "release_id": str(target_id),
            "previous_release_id": prev_after,
            "source_commit": manifest.get("source_commit"),
            "worktree_state": manifest.get("worktree_state"),
            "link_mode": link_mode,
            "frontend_link": frontend_link,
            "installed_units": units,
            "backed_up_dropins": backed_up_dropins,
            "restarted_services": restarted_services,
            "verified_service_pids": verified_service_pids,
            "synced_timers": synced_timers,
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
    p_inst.add_argument("--no-restart", action="store_true")
    p_inst.add_argument("--with-execution", action="store_true")
    p_inst.add_argument("--keep-releases", type=int, default=3)
    p_inst.add_argument("--min-free-bytes", type=int, default=512 * 1024 * 1024)
    p_inst.add_argument("--allow-window-override", action="store_true")
    p_inst.add_argument("--allow-dirty-release", action="store_true")
    p_inst.add_argument("--allow-active-episode-hotfix", action="store_true")
    p_inst.add_argument("--exec-control-url", default=None)
    p_inst.add_argument("--exec-control-token-file", type=Path, default=None)

    p_ver = sub.add_parser("verify")
    add_common(p_ver)
    p_ver.add_argument("--expected-core-version", default="2.0.7")

    p_rb = sub.add_parser("rollback")
    add_common(p_rb)
    p_rb.add_argument("--to-release", default=None)
    p_rb.add_argument("--apply-units", action="store_true")
    p_rb.add_argument("--no-systemd", action="store_true")
    p_rb.add_argument("--no-restart", action="store_true")
    p_rb.add_argument("--with-execution", action="store_true")
    p_rb.add_argument("--allow-window-override", action="store_true")
    p_rb.add_argument("--exec-control-url", default=None)
    p_rb.add_argument("--exec-control-token-file", type=Path, default=None)

    p_st = sub.add_parser("status")
    add_common(p_st)

    p_rep = sub.add_parser("repair-dossier-perms")
    add_common(p_rep)
    p_rep.add_argument("--dossier-root", type=Path, default=None)
    p_rep.add_argument("--group", default=None)

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
            restart_services=not args.no_restart,
            with_execution=args.with_execution,
            keep_releases=args.keep_releases,
            min_free_bytes=args.min_free_bytes,
            allow_window_override=args.allow_window_override,
            allow_dirty_release=args.allow_dirty_release,
            allow_active_episode_hotfix=args.allow_active_episode_hotfix,
            exec_control_url=args.exec_control_url,
            exec_control_token_file=args.exec_control_token_file,
        )
    elif args.command == "verify":
        out = cmd_verify(prefix, expected_core_version=args.expected_core_version)
    elif args.command == "rollback":
        out = cmd_rollback(
            prefix,
            to_release=args.to_release,
            apply_units=args.apply_units,
            no_systemd=args.no_systemd,
            restart_services=not args.no_restart,
            with_execution=args.with_execution,
            allow_window_override=args.allow_window_override,
            exec_control_url=args.exec_control_url,
            exec_control_token_file=args.exec_control_token_file,
        )
    elif args.command == "status":
        out = cmd_status(prefix)
    elif args.command == "repair-dossier-perms":
        dossier_root = (
            args.dossier_root.resolve()
            if args.dossier_root is not None
            else (prefix / "var" / "lib" / "xxproject" / "workbench" / "shadow_dossiers")
        )
        out = repair_shadow_dossier_permissions(dossier_root, group=args.group)
    else:
        raise SystemExit(2)

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
