# -*- coding: utf-8 -*-
"""Artifact manifest utilities for VRP simulation deliverables."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Dict, Iterable, List


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(root: str, artifact_paths: Iterable[str], label: str) -> Dict:
    root_abs = os.path.abspath(root)
    artifacts: List[Dict] = []
    missing = 0
    for path in artifact_paths:
        abs_path = os.path.abspath(path)
        rel = os.path.relpath(abs_path, root_abs)
        exists = os.path.exists(abs_path)
        item = {
            "relative_path": rel.replace("\\", "/"),
            "absolute_path": abs_path,
            "exists": exists,
            "size_bytes": None,
            "sha256": None,
            "modified_utc": None,
        }
        if exists:
            st = os.stat(abs_path)
            item["size_bytes"] = st.st_size
            item["sha256"] = _sha256(abs_path)
            item["modified_utc"] = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        else:
            missing += 1
        artifacts.append(item)

    return {
        "schema_name": "VrpArtifactManifest",
        "schema_version": "nrd.integration.vrp.artifact_manifest.v1.0",
        "label": label,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "root": root_abs,
        "summary": {
            "total": len(artifacts),
            "present": len(artifacts) - missing,
            "missing": missing,
        },
        "artifacts": artifacts,
    }


def _markdown(manifest: Dict) -> str:
    lines = [
        "# VRP Artifact Manifest",
        "",
        f"Label: `{manifest['label']}`  ",
        f"Generated UTC: `{manifest['generated_utc']}`  ",
        f"Root: `{manifest['root']}`",
        "",
        "| Artifact | Size bytes | SHA256 |",
        "| --- | ---: | --- |",
    ]
    for item in manifest["artifacts"]:
        size = "" if item["size_bytes"] is None else str(item["size_bytes"])
        sha = "" if item["sha256"] is None else item["sha256"]
        exists = "" if item["exists"] else "MISSING: "
        lines.append(f"| `{exists}{item['relative_path']}` | {size} | `{sha}` |")
    lines.append("")
    return "\n".join(lines)


def write_manifest(manifest: Dict, output_dir: str, stem: str = "vrp_artifact_manifest") -> Dict[str, str]:
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, stem + ".json")
    md_path = os.path.join(output_dir, stem + ".md")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(_markdown(manifest))
    return {"json": json_path, "md": md_path}
