"""The user asked for no "默认不做" label (2026-10-27, docs/opus/25): unsuitable situations are
flagged one by one, never with a blanket default."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [*(ROOT / "deploy" / "astra_workbench_v3").glob("*.js"),
         ROOT / "tools" / "astra_unsuitable_v1.py", ROOT / "tools" / "astra_decision_path_v1.py",
         ROOT / "tools" / "astra_decision_path_narrative_v1.py"]


def test_no_default_decline_wording():
    assert FILES and all(path.exists() for path in FILES)
    for path in FILES:
        assert "默认不做" not in path.read_text(encoding="utf-8"), path.name
