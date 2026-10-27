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


# ── the card's unsuitable block (unsuitable@0.1, dp-2026-10-27.1) ──

DOM = r"""
class T { constructor(t) { this.text = String(t); } get textContent() { return this.text; } }
class N {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.childNodes = []; this.attrs = {}; this.className = ""; this.dataset = {}; this.on = {}; }
  append(...cs) { for (const c of cs) this.childNodes.push(c); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(t, f) { (this.on[t] ||= []).push(f); }
  set textContent(v) { this.childNodes = [new T(v)]; }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
}
globalThis.Node = N;
globalThis.document = { createElement: (t) => new N(t), createTextNode: (t) => new T(t) };
const walk = (n, out = []) => { if (n && n.attrs) { out.push(n); for (const c of n.childNodes) walk(c, out); } return out; };
const byCls = (n, c) => walk(n).filter((x) => (x.className || "").split(" ").includes(c));
const texts = (n, c) => byCls(n, c).map((x) => x.textContent);
"""


def run_dom(body, payload):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = (DOM + f'const S = await import("{SESSION}");\n'
              'import fs from "node:fs";\n'
              'const p = JSON.parse(fs.readFileSync(0, "utf8"));\n'
              f"const out = (() => {{ {body} }})();\nconsole.log(JSON.stringify(out));")
    result = subprocess.run([node, "--input-type=module", "-e", script], input=json.dumps(payload),
                            text=True, encoding="utf-8", capture_output=True, check=False, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def uv_item(code, kind, title):
    return {"code": code, "kind": kind, "title_cn": title, "evidence_cn": f"{title}的读数 0.18 倍",
            "why_cn": f"{title}的原因", "clears_cn": f"{title}消失后，这一项才解除", "basis": "未验证，只作否决"}


def card(unsuitable, **over):
    c = {"side": "put", "entry_warnings": block(True, False, active=True),
         "readings": {"pricing": {"reservation_btc": 0.0123}}}
    if unsuitable is not None:
        c["unsuitable"] = unsuitable
    c.update(over)
    return c


BLOCK = """const b = S.warningBlock(p.c);
  return { cls: b.className, head: texts(b, 'ew-head').join(''), titles: texts(b, 'uv-title'), ev: texts(b, 'uv-evidence'),
    evNum: byCls(b, 'uv-evidence').every((x) => x.className.includes('num')), why: texts(b, 'uv-why'), clears: texts(b, 'uv-clears'),
    items: byCls(b, 'uv-item').map((x) => x.className), unknown: texts(b, 'uv-unknown'), note: texts(b, 'ew-note'),
    more: texts(b, 'ew-more').join(''), text: b.textContent };"""


def test_vetoes_lead_the_card_with_numbers_why_and_what_clears_them():
    u = {"schema": "unsuitable@0.1", "items": [uv_item("mid_short", "caution", "中间价也不够市场估价"),
                                               uv_item("narrow", "veto", "缓冲窄")],
         "veto_count": 1, "caution_count": 1, "unknown_cn": ["缓冲无法计算"]}
    out = run_dom(BLOCK, {"c": card(u)})
    assert "uv" in out["cls"] and "veto" in out["cls"]
    assert out["head"].startswith("不适合做 · 否决 1 项 · 提醒 1 项")
    assert out["titles"] == ["缓冲窄", "中间价也不够市场估价"]                 # vetoes first
    assert out["ev"][0] == "缓冲窄的读数 0.18 倍" and out["evNum"]
    assert out["why"][0] == "缓冲窄的原因" and out["clears"][0] == "解除条件：缓冲窄消失后，这一项才解除"
    assert out["items"][0].endswith("veto") and out["items"][1].endswith("caution")
    assert out["unknown"] == ["1 项无法检查：缓冲无法计算。"]
    assert out["note"] and out["note"][0].startswith("时段提示（不是否决）")        # busy-window note kept
    assert out["more"].startswith("阈值与全部检查项")                              # thresholds folded below
    assert "默认不做" not in out["text"]


def test_caution_only_is_a_reminder_not_a_veto():
    u = {"items": [uv_item("long_book", "caution", "保护腿盘口空或过宽")], "veto_count": 0, "caution_count": 1, "unknown_cn": []}
    out = run_dom(BLOCK, {"c": card(u)})
    assert "caution" in out["cls"] and "veto" not in out["cls"].split()
    assert out["head"] == "提醒 1 项" and out["unknown"] == []


def test_no_item_is_never_a_reason_and_says_what_could_not_be_checked():
    u = {"items": [], "veto_count": 0, "caution_count": 0, "unknown_cn": ["市场估价缺失，保留价无法计算"]}
    out = run_dom("const b = S.warningBlock(p.c); return { cls: b.className, text: b.textContent, none: texts(b, 'ew-none') };",
                  {"c": card(u, entry_warnings=block(False, False))})
    # data, two warnings, buffer, two price checks = 6; one could not run
    assert out["none"] == ["未见否决项（这不是做的理由）：已检查 5 项，1 项无法检查：市场估价缺失，保留价无法计算"]
    assert out["cls"] == "ew-none muted small"


def test_missing_or_failed_unsuitable_falls_back_to_the_entry_warnings():
    old = run_dom(BLOCK, {"c": card(None)})
    assert old["head"].startswith("不建仓警示 1 项") and old["titles"] == [] and "uv" not in old["cls"].split()
    failed = run_dom(BLOCK, {"c": card({"items": [], "error_type": "KeyError"})})
    assert failed == old
