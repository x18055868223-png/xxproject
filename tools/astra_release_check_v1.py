"""Release coherence check (read-only): the FMZ deliverable, the release manifest
and the running server must name the same producer version.

  python tools/astra_release_check_v1.py [--view VIEW.json | --view-url URL] [--root-deliverable PATH]

Checks:
  1. demo_version inside demo/最新交付物/neutral_regulation_demo_fmz.py
  2. release manifest fmz_producer candidate_version and source_hash (sha256 of that file)
  3. market_context.strategy_version observed by the workbench (when a view is given)
  4. the user-visible root deliverable is byte-identical (when given)
Exit code 0 only when every performed check passes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DELIVERABLE = ROOT / "demo" / "最新交付物" / "neutral_regulation_demo_fmz.py"
MANIFEST = ROOT / "deploy" / "signal_audit" / "release_candidate_manifest.json"
_VERSION = re.compile(rb'"demo_version":\s*"([^"]+)"')


def deliverable_version(path: Path) -> str | None:
    match = _VERSION.search(path.read_bytes())
    return match.group(1).decode() if match else None


def manifest_producer(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    return next((c for c in manifest.get("components") or [] if c.get("component") == "fmz_producer"), {})


def check(*, deliverable: Path = DELIVERABLE, manifest: Path = MANIFEST, view: dict | None = None,
          root_deliverable: Path | None = None) -> dict[str, Any]:
    results = []

    def add(name: str, ok: bool, detail: str) -> None:
        results.append({"check": name, "ok": bool(ok), "detail": detail})

    version = deliverable_version(deliverable)
    digest = hashlib.sha256(deliverable.read_bytes()).hexdigest()
    add("deliverable_version", version is not None, f"demo_version={version}")
    producer = manifest_producer(manifest)
    add("manifest_version", producer.get("candidate_version") == version,
        f"manifest={producer.get('candidate_version')} deliverable={version}")
    add("manifest_hash", producer.get("source_hash") == digest,
        f"manifest={str(producer.get('source_hash'))[:12]} file={digest[:12]}")
    if view is not None:
        observed = (view.get("market_context") or {}).get("strategy_version")
        add("server_observed_version", observed == version, f"server={observed} deliverable={version}")
    if root_deliverable is not None:
        same = root_deliverable.is_file() and root_deliverable.read_bytes() == deliverable.read_bytes()
        add("root_deliverable_synced", same, str(root_deliverable))
    return {"ok": all(r["ok"] for r in results), "version": version, "sha256": digest, "checks": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--view", help="workbench reading view JSON file")
    parser.add_argument("--view-url", help="workbench view URL, e.g. http://127.0.0.1:8895/api/workbench?view=page")
    parser.add_argument("--root-deliverable", help="user-visible copy of the FMZ deliverable to compare")
    args = parser.parse_args(argv)
    view = None
    if args.view:
        view = json.loads(Path(args.view).read_text(encoding="utf-8"))
    elif args.view_url:
        with urllib.request.urlopen(args.view_url, timeout=60) as response:
            view = json.load(response)
    out = check(view=view, root_deliverable=Path(args.root_deliverable) if args.root_deliverable else None)
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
