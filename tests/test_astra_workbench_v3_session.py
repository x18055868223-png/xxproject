"""v3 session strip and entry warnings: what the page says (docs/opus/13)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SESSION = (ROOT / "deploy" / "astra_workbench_v3" / "session.js").as_uri()


def run(expr, payload):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = (f'import * as S from "{SESSION}";\n'
              'import fs from "node:fs";\n'
              'const p = JSON.parse(fs.readFileSync(0, "utf8"));\n'
              f'console.log(JSON.stringify({expr}));')
    result = subprocess.run([node, "--input-type=module", "-e", script], input=json.dumps(payload),
                            text=True, encoding="utf-8", capture_output=True, check=False, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def block(*triggered, active=None):
    codes = ["term_inversion", "premium_thickening"]
    out = {"rules": "entry_warnings@0.2", "warnings": [
        {"code": c, "label_cn": c, "triggered": t, "evidence": {}, "threshold_cn": {"阈值": 1}, "reason_cn": "r"}
        for c, t in zip(codes, triggered)]}
    out["notes"] = [{"code": "busy_window", "label_cn": "高波动时段提示", "role": "note", "active": active,
                     "reason_cn": "美股开盘约 1.5 小时后。这是持有中的盯盘提示，不是否决。"}]
    return out


def test_warning_model_never_reads_missing_data_as_no_warning():
    out = run("S.warningModel(p.c)", {"c": {"side": "put", "entry_warnings": block(None, None)}})
    assert out["fired"] == [] and len(out["unknown"]) == 2 and out["total"] == 2
    fired = run("S.warningModel(p.c)", {"c": {"side": "put", "entry_warnings": block(True, None)}})
    assert [w["code"] for w in fired["fired"]] == ["term_inversion"] and len(fired["unknown"]) == 1
    assert run("S.warningModel(p.c)", {"c": {"side": "put", "entry_warnings": {"error_type": "X"}}})["available"] is False
    assert run("S.warningModel(p.c)", {"c": {"side": "put"}})["available"] is False


def test_the_busy_window_note_is_never_counted_as_a_veto():
    on = run("S.warningModel(p.c)", {"c": {"side": "put", "entry_warnings": block(False, False, active=True)}})
    assert on["fired"] == [] and on["unknown"] == [] and on["total"] == 2
    assert on["note"] == {"active": True, "reason": "美股开盘约 1.5 小时后。这是持有中的盯盘提示，不是否决。"}
    assert run("S.warningModel(p.c)", {"c": {"entry_warnings": block(False, False, active=False)}})["note"]["active"] is False
    # an @0.1 block (cached view or saved session): the fired busy-window item reads as the note
    legacy = {"rules": "entry_warnings@0.1", "warnings": [
        {"code": "term_inversion", "label_cn": "近端倒挂", "triggered": False},
        {"code": "premium_thickening", "label_cn": "保费突然变厚", "triggered": False},
        {"code": "busy_window_thin_cushion", "label_cn": "高波动窗口临近、缓冲不足", "triggered": True,
         "evidence": {"next_window": {"label_cn": "美股开盘", "in_hours": -0.3}, "busy_now": True}}]}
    old = run("S.warningModel(p.c)", {"c": {"side": "put", "entry_warnings": legacy}})
    assert old["fired"] == [] and old["total"] == 2 and old["note"]["active"] is True
    assert old["note"]["reason"].startswith("美股开盘正在进行。") and "不是否决" in old["note"]["reason"]
    assert run("S.busyNote(p.b)", {"b": {"warnings": []}}) is None
    source = (ROOT / "deploy" / "astra_workbench_v3" / "session.js").read_text(encoding="utf-8")
    assert "时段提示（不是否决）：" in source


def test_clock_model_counts_down_and_flags_long_expiries():
    view = {"session_clock": {"phase": {"code": "pre_us", "label_cn": "美股开盘前"}, "hour_level": "normal",
                              "expiry_ms": 1_792_569_600_000, "hours_to_expiry": 30.0, "beyond_cycle": True,
                              "expiry_source": "contract", "remaining_time_share": 1.0, "remaining_variance_share": 1.0,
                              "calendar_gaps": [], "windows": [
                                  {"kind": "us_open", "label_cn": "美股开盘", "at_ms": 1_792_495_800_000},
                                  {"kind": "macro", "label_cn": "CPI", "at_ms": 1_792_499_400_000, "tentative": True}]}}
    out = run("S.clockModel(p.v, p.now)", {"v": view, "now": 1_792_494_000_000})
    assert out["beyond"] is True and out["fromContract"] is True
    assert out["windows"][0]["until"] == "30 分钟后" and out["windows"][1]["label"].endswith("（日期待确认）")
    assert run("S.clockModel(p.v, 0)", {"v": {}})["available"] is False
    assert run("S.untilText(p.a, p.b)", {"a": 1000, "b": 2000}) == "进行中"


def test_density_tiers_and_no_cost_performance_wording():
    assert run("[S.densityTier(1.25), S.densityTier(1.2), S.densityTier(0.8), S.densityTier(0.79), S.densityTier(null)]", {})         == ["busy", "mid", "mid", "quiet", "na"]
    source = (ROOT / "deploy" / "astra_workbench_v3" / "session.js").read_text(encoding="utf-8")
    assert "性价比" not in source
