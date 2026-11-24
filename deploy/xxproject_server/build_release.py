"""Build a reproducible, single-authority xxproject server release package (.tar.gz).

Stdlib-only. Excludes secrets, runtime data, .artifacts, and FMZ bot files,
enforces SHADOW_ONLY and read_only_demo=True invariants, and records every file's
SHA-256 and deterministic host-path adaptation inside RELEASE_MANIFEST.json.
"""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
from typing import Any

RELEASE_SCHEMA = "xxproject_server_release@1.0.0"

ENTRYPOINT_ROOTS = [
    "astra_signal_producer_v1",
    "astra_workbench_v20",
    "astra_map_data_server_v24",
    "astra_kpf_light_v1",
    "astra_research_archive_v1",
    "astra_player_tracking_v13",
    "astra_player_dossier_v1",
    "materialize_signal_cards",
    "signal_llm_review",
    "signal_llm_review_entry",
    "signal_review_v2_runtime",
    "xxproject_server_health_v1",
]

EXTRA_TOOLS = [
    "astra_data_retention_v23.py",
    "astra_data_server_v23.py",
    "astra_decision_path_report.py",
    "astra_experience_cards_v1.json",
    "astra_inquiry_terms_v1.json",
    "astra_joint_v11_model.py",
    "astra_player_rulebook.md",
    "astra_release_check_v1.py",
    "astra_structural_player_view_v1.py",
    "astra_underwriting_card_bridge_v20.py",
    "astra_underwriting_cli_v20.py",
    "astra_underwriting_review_cli_v20.py",
    "astra_wpem_reference_v1.py",
    "astra_wpem_results_v1.py",
    "server_self_check_signal_stack.sh",
    "signal_llm_review_canary_release.sh",
]

OFFLINE_PATH_SANITIZATIONS: dict[str, list[tuple[str, str]]] = {
    "tools/astra_joint_v11_data.py": [
        (
            r'DEFAULT_SOURCE_ROOT = Path(r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\astra-joint-v1-20260914")',
            'DEFAULT_SOURCE_ROOT = Path(os.environ.get("ASTRA_JOINT_V11_SOURCE_ROOT", "offline/astra-joint-v1"))',
        ),
        (
            r'DEFAULT_OUTPUT_ROOT = Path(r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\astra-joint-v11-20260915")',
            'DEFAULT_OUTPUT_ROOT = Path(os.environ.get("ASTRA_JOINT_V11_OUTPUT_ROOT", "offline/astra-joint-v11"))',
        ),
    ],
    "tools/astra_joint_sources.py": [
        (".artifacts/astra-local-validation-20260914", "offline/astra-local-validation-20260914"),
        (".artifacts/astra-light-study-20260913", "offline/astra-light-study-20260913"),
    ],
    "tools/astra_workbench_v20.py": [
        ('".artifacts/astra-workbench-v20"', '"state/astra-workbench-v20"'),
    ],
    "tools/astra_observation_replay_v1.py": [
        (".artifacts/observation_lineage_20261007_01", "offline/observation_lineage_20261007_01"),
        (".artifacts/semantic_contract_closure_20261007_01", "offline/semantic_contract_closure_20261007_01"),
        (".artifacts/semantic_synthesis_20261007_01", "offline/semantic_synthesis_20261007_01"),
        (".artifacts/consumption_ui_audit_20261007_01", "offline/consumption_ui_audit_20261007_01"),
    ],
    "tools/astra_underwriting_real_replay_v20.py": [
        (".artifacts/astra-credit-space-20260914", "offline/astra-credit-space-20260914"),
    ],
}

SIGNAL_AUDIT_WHITELIST = [
    "deploy/signal_audit/signal-audit-materialize.service",
    "deploy/signal_audit/signal-audit-materialize.timer",
    "deploy/signal_audit/signal-audit-materialize.env.example",
    "deploy/signal_audit/workbench-v20.service",
    "deploy/signal_audit/workbench-v20.env.example",
    "deploy/signal_audit/release_candidate_manifest.json",
    "deploy/signal_audit/phase_release_manifest.json",
    "deploy/signal_audit/signal-audit-llm.env.example",
]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _normalize_lf(raw: bytes) -> bytes:
    return raw.replace(b"\r\n", b"\n")


def _compute_tool_closure(tools_dir: Path) -> list[Path]:
    all_py = {p.stem: p for p in tools_dir.glob("*.py")}
    seen: set[str] = set()
    queue = list(ENTRYPOINT_ROOTS)
    while queue:
        cur = queue.pop(0)
        if cur in seen or cur not in all_py:
            continue
        seen.add(cur)
        tree = ast.parse(all_py[cur].read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name.split(".")[-1]
                    if name in all_py and name not in seen:
                        queue.append(name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    name = node.module.split(".")[-1]
                    if name in all_py and name not in seen:
                        queue.append(name)
                for alias in node.names:
                    if alias.name in all_py and alias.name not in seen:
                        queue.append(alias.name)
    files = [all_py[name] for name in sorted(seen)]
    for extra in EXTRA_TOOLS:
        cand = tools_dir / extra
        if not cand.is_file():
            raise FileNotFoundError(f"missing required tool asset: {cand}")
        if cand not in files:
            files.append(cand)
    return sorted(set(files))


def _resolve_commit(root: Path, explicit_commit: str | None, *, allow_dirty: bool) -> tuple[str, str]:
    if explicit_commit is not None:
        commit = explicit_commit.strip().lower()
        if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
            raise ValueError(f"INVALID_SOURCE_COMMIT: {explicit_commit!r}")
    else:
        commit = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip().lower()
    dirty = subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain"], text=True
    ).strip()
    if dirty and not allow_dirty:
        raise ValueError("DIRTY_WORKTREE_REJECTED (pass --allow-dirty for test builds)")
    return commit, ("dirty" if dirty else "clean")


def _extract_version(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text, re.MULTILINE)
    return m.group(1) if m else None


def _collect_entries(root: Path) -> tuple[dict[str, bytes], list[dict[str, str]], dict[str, Any]]:
    content: dict[str, bytes] = {}
    transformations: list[dict[str, str]] = []

    def add_bytes(dest_rel: str, src: Path, *, lf_normalize: bool = True) -> None:
        if src.is_symlink():
            raise ValueError(f"SYMLINK_SOURCE_REJECTED: {src}")
        raw = src.read_bytes()
        data = _normalize_lf(raw) if lf_normalize else raw
        if dest_rel in OFFLINE_PATH_SANITIZATIONS:
            before_sha = _sha256(data)
            txt = data.decode("utf-8")
            for old, new in OFFLINE_PATH_SANITIZATIONS[dest_rel]:
                txt = txt.replace(old, new)
            data = txt.encode("utf-8")
            after_sha = _sha256(data)
            if before_sha != after_sha:
                transformations.append({
                    "path": dest_rel,
                    "source_sha256": before_sha,
                    "packaged_sha256": after_sha,
                    "reason": "portable host-safe offline default paths; algorithm logic unchanged",
                })
        content[dest_rel] = data

    # 1. Canonical signal core (read_only_demo=True verified below)
    core_src = root / "demo" / "最新交付物" / "neutral_regulation_demo_fmz.py"
    if not core_src.is_file():
        raise FileNotFoundError(f"missing signal core: {core_src}")
    add_bytes("signal_core/neutral_regulation_demo_fmz.py", core_src, lf_normalize=True)

    # 2. Tools closure + data_product_registry_v23.json
    for tool_path in _compute_tool_closure(root / "tools"):
        rel = f"tools/{tool_path.name}"
        add_bytes(rel, tool_path, lf_normalize=True)

    registry_src = root / "demo" / "shared" / "data_product_registry_v23.json"
    if not registry_src.is_file():
        raise FileNotFoundError(f"missing registry: {registry_src}")
    add_bytes("demo/shared/data_product_registry_v23.json", registry_src, lf_normalize=True)
    add_bytes("tools/data_product_registry_v23.json", registry_src, lf_normalize=True)

    # 3. Workbench v3 frontend
    for p in sorted((root / "deploy" / "astra_workbench_v3").rglob("*")):
        if p.is_file():
            add_bytes(p.relative_to(root).as_posix(), p, lf_normalize=True)

    # 4. GEXMonitorAPI source & deploy config
    gex_root = root / "05_GEX监控API_数据增强接口"
    for p in sorted((gex_root / "src" / "gexmonitorapi").rglob("*.py")):
        add_bytes(p.relative_to(root).as_posix(), p, lf_normalize=True)
    for name in ("gexmonitorapi.service", "gexmonitorapi.env.example"):
        p = gex_root / "deploy" / name
        if p.is_file():
            add_bytes(f"deploy/gexmonitorapi/{name}", p, lf_normalize=True)

    # 5. Deployment assets (signal_producer, astra_map_data, kpf_light, research_archive, player_dossier, eu_nginx, xxproject_server)
    deploy_dirs = [
        "deploy/signal_producer",
        "deploy/astra_map_data",
        "deploy/kpf_light",
        "deploy/research_archive",
        "deploy/player_dossier",
        "deploy/eu_nginx",
        "deploy/xxproject_server",
    ]
    for d in deploy_dirs:
        base = root / d
        if not base.is_dir():
            raise FileNotFoundError(f"missing deploy directory: {base}")
        for p in sorted(base.rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            rel = p.relative_to(root).as_posix()
            # Vendored KPF algorithms keep exact byte identity per source_manifest.json.
            is_vendor = rel.startswith("deploy/kpf_light/vendor/")
            add_bytes(rel, p, lf_normalize=not is_vendor)

    for rel in SIGNAL_AUDIT_WHITELIST:
        p = root / rel
        if p.is_file():
            add_bytes(rel, p, lf_normalize=True)

    # 6. Validate safety, vendor hashes, and exclusions
    vendor_manifest = json.loads(content["deploy/kpf_light/source_manifest.json"].decode("utf-8-sig"))
    for entry in vendor_manifest.get("files") or []:
        rel = str(entry["path"])
        if rel not in content:
            raise ValueError(f"KPF_VENDOR_FILE_MISSING: {rel}")
        if _sha256(content[rel]) != entry["sha256"]:
            raise ValueError(f"KPF_VENDOR_HASH_MISMATCH: {rel}")

    core_text = content["signal_core/neutral_regulation_demo_fmz.py"].decode("utf-8")
    core_version = _extract_version(core_text, r'^\s*"demo_version"\s*:\s*"([0-9]+\.[0-9]+\.[0-9]+)"')
    if not core_version or not core_version.startswith("2."):
        raise ValueError(f"INVALID_SIGNAL_CORE_VERSION: {core_version!r}")
    if '"read_only_demo": True' not in core_text:
        raise ValueError("SIGNAL_CORE_READ_ONLY_VIOLATED")

    plan_paths = sorted(
        name for name in content
        if name.startswith("deploy/player_dossier/plan.daily_r16.") and name.endswith(".json")
    )
    if not plan_paths:
        raise ValueError("PLAYER_PLAN_MISSING")
    plans_by_name: dict[str, dict[str, Any]] = {}
    for rel_plan in plan_paths:
        p_obj = json.loads(content[rel_plan].decode("utf-8-sig"))
        if p_obj.get("mode") != "SHADOW_ONLY" or p_obj.get("execution_allowed") is not False:
            raise ValueError(f"PLAYER_PLAN_SHADOW_ONLY_VIOLATED: {rel_plan}")
        plans_by_name[rel_plan] = p_obj
    active_plan_rel = (
        "deploy/player_dossier/plan.daily_r16.v3_p5.json"
        if "deploy/player_dossier/plan.daily_r16.v3_p5.json" in plans_by_name
        else "deploy/player_dossier/plan.daily_r16.v2.json"
    )
    if active_plan_rel not in plans_by_name:
        raise ValueError(f"PLAYER_PLAN_MISSING: {active_plan_rel}")
    plan = plans_by_name[active_plan_rel]

    for name in content:
        lower = name.lower()
        if (
            lower.endswith(".env")
            or lower.endswith(".db3")
            or lower.endswith(".jsonl")
            or ".artifacts" in lower
            or "spm_manual_gate_execution" in lower
            or "fmz-password" in lower
        ):
            raise ValueError(f"FORBIDDEN_PACKAGED_ARTIFACT: {name}")

    versions = {
        "signal_core": core_version,
        "signal_core_lf_sha256": _sha256(content["signal_core/neutral_regulation_demo_fmz.py"]),
        "signal_producer": "astra-signal-producer@1.0.0",
        "workbench": _extract_version(content["tools/astra_workbench_v20.py"].decode("utf-8"), r'^SCHEMA\s*=\s*"([^"]+)"'),
        "map_data_server": _extract_version(content["tools/astra_map_data_server_v24.py"].decode("utf-8"), r'^SCHEMA\s*=\s*"([^"]+)"'),
        "player_plan": plan.get("plan_id"),
        "health_check": _extract_version(content["tools/xxproject_server_health_v1.py"].decode("utf-8"), r'^SCHEMA\s*=\s*"([^"]+)"'),
    }
    return content, transformations, versions


def build_release(
    root: Path,
    output: Path,
    *,
    source_commit: str | None = None,
    release_tag: str | None = None,
    allow_dirty: bool = False,
) -> dict[str, Any]:
    commit, worktree_state = _resolve_commit(root, source_commit, allow_dirty=allow_dirty)
    content, transformations, versions = _collect_entries(root)
    files_meta = {
        name: {"sha256": _sha256(raw), "bytes": len(raw)}
        for name, raw in sorted(content.items())
    }
    files_digest = _sha256(json.dumps(files_meta, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    release_id = f"xxproject-server-{commit[:12]}-{files_digest[:12]}"

    manifest = {
        "schema": RELEASE_SCHEMA,
        "release_id": release_id,
        "release_tag": release_tag,
        "source_commit": commit,
        "worktree_state": worktree_state,
        "module_versions": versions,
        "security_invariants": {
            "fmz_robot_bundled": False,
            "fmz_execution_bundled": False,
            "signal_core_read_only_demo": True,
            "ai_player_mode": "SHADOW_ONLY",
            "ai_player_execution_allowed": False,
            "secrets_or_runtime_data_bundled": False,
        },
        "packaging_transformations": transformations,
        "file_count": len(files_meta),
        "files_sha256": files_digest,
        "files": files_meta,
    }
    content["RELEASE_MANIFEST.json"] = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    with (
        output.open("wb") as stream,
        gzip.GzipFile(filename="", mode="wb", fileobj=stream, mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w") as archive,
    ):
        for name in sorted(content):
            raw = content[name]
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            archive.addfile(info, io.BytesIO(raw))

    pkg_bytes = output.read_bytes()
    return {
        "schema": RELEASE_SCHEMA,
        "release_id": release_id,
        "source_commit": commit,
        "worktree_state": worktree_state,
        "package": str(output),
        "package_sha256": _sha256(pkg_bytes),
        "bytes": len(pkg_bytes),
        "file_count": len(content),
        "files_sha256": files_digest,
        "module_versions": versions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", default=None)
    parser.add_argument("--release-tag", default=None)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args(argv)
    summary = build_release(
        args.root.resolve(),
        args.output.resolve(),
        source_commit=args.source_commit,
        release_tag=args.release_tag,
        allow_dirty=args.allow_dirty,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
