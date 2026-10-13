"""v3 lock list: the latest five by default; tracking and open contracts are always shown."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOCKS = (ROOT / "deploy" / "astra_workbench_v3" / "locks.js").as_uri()


def visible(episodes, **opts):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = (f'import {{ visibleLocks }} from "{LOCKS}";\n'
              'import fs from "node:fs";\n'
              'const p = JSON.parse(fs.readFileSync(0, "utf8"));\n'
              'const out = visibleLocks(p.episodes, p.opts);\n'
              'console.log(JSON.stringify({ ids: out.rows.map((e) => e.episode_id), hidden: out.hidden }));')
    result = subprocess.run([node, "--input-type=module", "-e", script], input=json.dumps({"episodes": episodes, "opts": opts}),
                            text=True, encoding="utf-8", capture_output=True, check=False, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def eps(n, active=()):
    return [{"episode_id": f"e{i}", "created_at_ms": i, "status": "active" if i in active else "expired"} for i in range(n)]


def test_at_most_the_latest_five_by_default():
    out = visible(eps(8))
    assert out == {"ids": ["e7", "e6", "e5", "e4", "e3"], "hidden": 3}
    assert visible(eps(4))["hidden"] == 0


def test_tracking_and_open_contracts_stay_visible_and_show_all_lists_everything():
    out = visible(eps(8, active=(1,)), selected="e0")
    assert out["ids"] == ["e7", "e6", "e5", "e4", "e3", "e1", "e0"] and out["hidden"] == 1
    assert visible(eps(8), showAll=True) == {"ids": [f"e{i}" for i in range(7, -1, -1)], "hidden": 0}
