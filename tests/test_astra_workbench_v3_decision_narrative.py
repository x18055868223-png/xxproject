"""Decision Path narrative view (dp_narrative@0.1): sentences, footnotes, ask chips, notes, evidence, 叙述/明细 toggle."""
import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "deploy" / "astra_workbench_v3"

# A small DOM: attributes, dataset, listeners, focus and attribute queries, enough for decision.js.
SHIM = r"""
class T { constructor(t) { this.text = String(t); } get textContent() { return this.text; } }
const dataAttr = (k) => "data-" + k.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());
class N {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.childNodes = []; this.attrs = {}; this.className = ""; this.dataset = {};
    this.style = {}; this.on = {}; this.value = ""; }
  append(...cs) { for (const c of cs) this.childNodes.push(c); }
  get firstChild() { return this.childNodes[0] || null; }
  removeChild(c) { this.childNodes.splice(this.childNodes.indexOf(c), 1); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  removeAttribute(k) { delete this.attrs[k]; for (const d of Object.keys(this.dataset)) if (dataAttr(d) === k) delete this.dataset[d]; }
  addEventListener(t, f) { (this.on[t] ||= []).push(f); }
  fire(t, target = this) { for (const f of this.on[t] || []) f({ target, preventDefault() {} }); }
  focus() { document.activeElement = this; }
  blur() { if (document.activeElement === this) document.activeElement = null; }
  setSelectionRange() {}
  querySelector(sel) {
    const m = /^\[([\w-]+)="([^"]*)"\]$/.exec(sel);
    return walk(this).find((x) => Object.entries(x.dataset).some(([k, v]) => dataAttr(k) === m[1] && String(v) === m[2])) || null;
  }
  set textContent(v) { this.childNodes = [new T(v)]; }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
}
globalThis.Node = N;
globalThis.document = { activeElement: null, createElement: (t) => new N(t), createElementNS: (_, t) => new N(t), createTextNode: (t) => new T(t) };
const store = {};
globalThis.localStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); } };
const walk = (n, out = []) => { if (n && n.attrs) { out.push(n); for (const c of n.childNodes) walk(c, out); } return out; };
const byCls = (n, c) => walk(n).filter((x) => (x.className || "").split(" ").includes(c));
const one = (n, c) => byCls(n, c)[0];
const step = (box, id) => walk(box).find((x) => x.attrs.id === `dp-${id}`);
const texts = (n, c) => byCls(n, c).map((x) => x.textContent);
"""


def run(body, payload=None):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = (SHIM + 'import fs from "node:fs";\n'
              'const p = JSON.parse(fs.readFileSync(0, "utf8"));\n'
              f'const D = await import("{(PAGE / "decision.js").as_uri()}");\n'
              "const posts = [];\n"
              "const mkctx = (session, extra = {}) => ({ session, canAct: true, busy: null, extras: null,\n"
              "  onStep: (s, lean, x = {}) => posts.push({ step: s, ...(lean ? { lean } : {}), ...x }),\n"
              "  onNote: async (s, x) => { posts.push({ step: s, ...x }); return noteResult; }, onJump() {}, ...extra });\n"
              "let noteResult = { ok: true };\n"
              "const tick = () => new Promise((r) => setTimeout(r, 0));\n"
              f"const out = await (async () => {{ {body} }})();\nconsole.log(JSON.stringify(out));")
    result = subprocess.run([node, "--input-type=module", "-e", script], input=json.dumps(payload or {}),
                            text=True, encoding="utf-8", capture_output=True, check=False, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


D2_NARRATIVE = {
    "lead": "从现价下跌到短腿 78,712 的路上，先遇到周度正 Gamma 墙 79,000。",
    "sentences": [
        {"text": "短腿距现价 1.24%，约 0.63 倍预期波动，属于窄缓冲。", "fn": [1], "tone": "warn"},
        {"text": "路上没有成交密集区。", "fn": [2], "tone": ""},
        {"text": "短腿位于负 Gamma 区，对冲顺势放大波动。", "fn": [1, 3], "tone": "bad"},
    ],
    "background": [{"text": "背景（研究不支持作为依据）：宏观综合得分 −0.35，只作描述。", "fn": [3]}],
    "footnotes": [{"n": 1, "text": "本期 Deribit 标记 ATM IV 35.6%，剩余 16 小时。"},
                  {"n": 2, "text": "KPF 24h 成交分布。"},
                  {"n": 3, "text": "gexmonitor 周度 Gamma 墙与 Flip 80,035。"}],
    "ask": {"question": "这段缓冲够不够你承保？",
            "options": [{"key": "enough", "label": "够，墙在前面", "lean": "toward_underwrite", "needs_note": False},
                        {"key": "unsure", "label": "说不清", "lean": "unchanged", "needs_note": False},
                        {"key": "thin", "label": "太窄，要写原因", "lean": "toward_decline", "needs_note": True}]},
    "evidence": [
        {"key": "wall_ahead", "label": "短腿前有正 Gamma 墙", "value": "79,000", "grade": "B", "verdict_cn": "研究支持（弱）",
         "direction": "underwrite", "source": "docs/opus/20 §2"},
        {"key": "iv_pct_low", "label": "IV 分位低", "value": "12", "grade": "X", "verdict_cn": "研究不支持作为依据",
         "direction": "decline", "source": "docs/opus/20 §5"},
        {"key": "kpf_sparse", "label": "成交稀疏", "value": None, "grade": "未检验", "verdict_cn": "未检验",
         "direction": "neutral", "source": "KPF"},
    ],
    "covered": [0, 1],
}
Q2_NARRATIVE = {
    "lead": "净信用 131 美元，最坏在保护腿处亏 1,943 美元。",
    "sentences": [{"text": "约为净信用的 14.8 倍。", "fn": [1], "tone": "warn"}],
    "background": [], "footnotes": [{"n": 1, "text": "公开 taker 费率（有上限）。"}],
    "ask": None, "evidence": [], "covered": [0],
}
SESSION = {
    "verification_id": "v1", "status": "open", "side": "put", "path_version": 2, "started_at_ms": 1_792_494_000_000,
    "contract": {"short_instrument": "BTC-27OCT26-78712-P", "short_strike_usd": 78712, "long_strike_usd": 76712},
    "steps": [{"id": "T", "title": "时段", "question": "现在是什么时段？"},
              {"id": "D2", "title": "空间", "question": "缓冲在哪里？"},
              {"id": "Q2", "title": "补偿", "question": "补偿够吗？", "premium": True}],
    "opened": {"T": 1, "D2": 2, "Q2": 3},
    "marks": {}, "notes": {},
    "readings": {"start": {"at_ms": 1_792_494_000_000,
                           "steps": {"T": {"verdict": {"cn": "美股开盘前", "tone": ""}, "lines": [{"k": "时段", "v": "美股开盘前"}]},
                                     "D2": {"verdict": {"cn": "窄", "tone": "warn"},
                                            "lines": [{"k": "距离", "v": "1.24%"}, {"k": "路径", "v": "无密集区"},
                                                      {"k": "宏观", "v": "−0.35"}]},
                                     "Q2": {"verdict": {"cn": "偏薄", "tone": "warn"}, "lines": [{"k": "净信用", "v": "131 美元"}]}},
                           "narrative": {"schema": "dp_narrative@0.1", "steps": {"D2": D2_NARRATIVE, "Q2": Q2_NARRATIVE}}}},
}


def session(**over):
    s = copy.deepcopy(SESSION)
    s.update(over)
    return s


def test_paragraph_has_every_sentence_tone_and_background_muted():
    out = run("""const box = new N('div'); D.renderDecision(box, mkctx(p.s));
      const d2 = step(box, 'D2');
      return { para: one(d2, 'dp-para').textContent, warn: byCls(d2, 'dp-sent').filter((x) => x.className.includes('warn')).map((x) => x.textContent),
               bad: byCls(d2, 'dp-sent').filter((x) => x.className.includes('bad')).map((x) => x.textContent),
               bg: one(d2, 'dp-bg').textContent, table: byCls(d2, 'dp-lines').length, rest: texts(d2, 'dp-rest'),
               tTable: byCls(step(box, 'T'), 'dp-lines').length, toggle: texts(box, 'dp-view-btn'),
               fns: byCls(d2, 'dp-fn').map((x) => x.attrs['aria-label']), q2: one(step(box, 'Q2'), 'dp-para').textContent };""",
              {"s": SESSION})
    assert out["para"].startswith(D2_NARRATIVE["lead"])
    for s in D2_NARRATIVE["sentences"]:
        assert s["text"] in out["para"]
    assert out["warn"] == [D2_NARRATIVE["sentences"][0]["text"]] and out["bad"] == [D2_NARRATIVE["sentences"][2]["text"]]
    assert out["bg"].startswith("背景（研究不支持作为依据）：宏观综合得分 −0.35")
    assert out["table"] == 0 and out["rest"] == ["另有读数：宏观 −0.35"]      # uncovered line kept, nothing lost
    assert out["tTable"] == 1                                                   # a step without narrative keeps its table
    assert out["toggle"] == ["叙述", "明细"] and out["fns"][:2] == ["依据 1", "依据 2"]
    assert "约为净信用的 14.8 倍。" in out["q2"]


def test_footnote_marker_toggles_its_text_and_the_full_list_is_collapsed():
    out = run("""const box = new N('div'); D.renderDecision(box, mkctx(p.s));
      const lists = () => walk(step(box, 'D2')).filter((x) => x.tagName === 'OL' && (x.className || '').includes('dp-fn-list'));
      const before = lists().length;
      const all = one(step(box, 'D2'), 'dp-fn-all').textContent;
      byCls(step(box, 'D2'), 'dp-fn').find((x) => x.dataset.fn === '2').fire('click');
      const opened = lists().map((x) => x.textContent);
      const pressed = byCls(step(box, 'D2'), 'dp-fn').find((x) => x.dataset.fn === '2').attrs['aria-expanded'];
      byCls(step(box, 'D2'), 'dp-fn').find((x) => x.dataset.fn === '2').fire('click');
      const closed = lists().length;
      one(step(box, 'D2'), 'dp-fn-all').fire('click');
      const full = lists().map((x) => x.textContent);
      return { before, all, opened, pressed, closed, full };""", {"s": session(verification_id="v-fn")})
    assert out["before"] == 0 and out["all"] == "依据 3 条"
    assert out["opened"] == ["2 KPF 24h 成交分布。"] and out["pressed"] == "true"
    assert out["closed"] == 0
    assert len(out["full"]) == 1 and all(f["text"] in out["full"][0] for f in D2_NARRATIVE["footnotes"])


def test_chips_post_at_once_but_a_needs_note_choice_waits_for_its_reason():
    out = run("""const box = new N('div'); D.renderDecision(box, mkctx(p.s));
      const opt = (k) => walk(step(box, 'D2')).find((x) => x.dataset.opt === k);
      const hint0 = one(step(box, 'D2'), 'dp-ask-hint').textContent;
      opt('enough').fire('click');
      const noEditor = walk(step(box, 'D2')).filter((x) => x.tagName === 'TEXTAREA').length;
      const afterEnough = posts.length;
      opt('thin').fire('click');
      const area = walk(step(box, 'D2')).find((x) => x.tagName === 'TEXTAREA');
      return { posts, afterEnough, hint0, noEditor, area: !!area, focused: document.activeElement === area,
               pending: opt('thin').className, hint: one(step(box, 'D2'), 'dp-ask-hint').textContent };""",
              {"s": session(verification_id="v-chip")})
    assert out["afterEnough"] == 1
    assert out["posts"] == [{"step": "D2", "lean": "toward_underwrite", "choice": "enough"}]      # thin posts nothing yet
    assert "够，墙在前面 → 更倾向承保" in out["hint0"]
    assert out["noEditor"] == 0 and out["area"] and out["focused"]
    assert " on" in out["pending"] and "pending" in out["pending"]
    assert out["hint"].startswith("记为：更倾向不承保（写下理由")


def test_needs_note_save_is_one_request_and_cancel_posts_nothing():
    out = run("""const box = new N('div'); D.renderDecision(box, mkctx(p.s));
      const d2 = () => step(box, 'D2');
      const opt = (k) => walk(d2()).find((x) => x.dataset.opt === k);
      const btn = (t) => walk(d2()).find((x) => x.tagName === 'BUTTON' && x.textContent === t);
      opt('thin').fire('click');
      btn('取消').fire('click');
      const cancelled = { posts: posts.length, editor: walk(d2()).filter((x) => x.tagName === 'TEXTAREA').length,
                          on: byCls(d2(), 'dp-opt').filter((x) => x.className.includes(' on')).length };
      opt('thin').fire('click');
      btn('保存选择与理由').fire('click');                  // empty reason: refused locally, nothing posted
      const empty = { posts: posts.length, err: texts(d2(), 'dp-error') };
      walk(d2()).find((x) => x.tagName === 'INPUT' && x.dataset.ev === 'wall_ahead').fire('change', { checked: true });
      walk(d2()).find((x) => x.tagName === 'TEXTAREA').fire('input', { value: '缓冲只有 0.63 倍' });
      btn('保存选择与理由').fire('click');
      await tick();
      return { cancelled, empty, posts, editor: walk(d2()).filter((x) => x.tagName === 'TEXTAREA').length };""",
              {"s": session(verification_id="v-nn")})
    assert out["cancelled"] == {"posts": 0, "editor": 0, "on": 0}
    assert out["empty"] == {"posts": 0, "err": ["这个选项需要写下理由"]}
    assert out["posts"] == [{"step": "D2", "note": "缓冲只有 0.63 倍", "reasons": ["wall_ahead"],
                             "lean": "toward_decline", "choice": "thin"}]
    assert out["editor"] == 0


def test_failed_save_keeps_the_draft_and_shows_the_server_reason():
    out = run("""noteResult = { ok: false, code: 'note_required', message: 'x' };
      const box = new N('div'); D.renderDecision(box, mkctx(p.s));
      const d2 = () => step(box, 'D2');
      walk(d2()).find((x) => x.dataset.fk === 'D2:note').fire('click');
      walk(d2()).find((x) => x.tagName === 'INPUT' && x.dataset.ev === 'kpf_sparse').fire('change', { checked: true });
      walk(d2()).find((x) => x.tagName === 'TEXTAREA').fire('input', { value: '半句话' });
      walk(d2()).find((x) => x.tagName === 'BUTTON' && x.textContent === '保存备注').fire('click');
      await tick();
      const area = walk(d2()).find((x) => x.tagName === 'TEXTAREA');
      return { posts: posts.length, value: area && area.value, err: texts(d2(), 'dp-error'),
               cite: one(d2(), 'dp-cite-line').textContent };""", {"s": session(verification_id="v-fail")})
    assert out == {"posts": 1, "value": "半句话", "err": ["这个选项需要写下理由"], "cite": "将引用：成交稀疏"}


def test_a_redraw_waits_while_the_note_is_being_typed():
    out = run("""const box = new N('div'); const ctx = mkctx(p.s); D.renderDecision(box, ctx);
      walk(step(box, 'D2')).find((x) => x.dataset.fk === 'D2:note').fire('click');
      const area = walk(step(box, 'D2')).find((x) => x.tagName === 'TEXTAREA');
      const focused = document.activeElement === area;
      D.renderDecision(box, { ...ctx, busy: null });            // a periodic redraw while typing
      const kept = walk(step(box, 'D2')).find((x) => x.tagName === 'TEXTAREA') === area;
      area.blur(); D.renderDecision(box, ctx);
      const rebuilt = walk(step(box, 'D2')).find((x) => x.tagName === 'TEXTAREA') !== area;
      return { focused, kept, rebuilt };""", {"s": session(verification_id="v-typing")})
    assert out == {"focused": True, "kept": True, "rebuilt": True}


def test_selected_chip_is_highlighted_with_its_lean_hint():
    s = session(marks={"D2": {"lean": "toward_decline", "at_ms": 1, "choice": "thin"}})
    out = run("""const box = new N('div'); D.renderDecision(box, mkctx(p.s));
      const on = byCls(step(box, 'D2'), 'dp-opt').filter((x) => x.className.includes(' on'));
      return { on: on.map((x) => x.dataset.opt), pressed: on[0].attrs['aria-pressed'],
               hint: one(step(box, 'D2'), 'dp-ask-hint').textContent,
               q2marks: byCls(step(box, 'Q2'), 'mark').length };""", {"s": s})
    assert out == {"on": ["thin"], "pressed": "true", "hint": "记为：更倾向不承保", "q2marks": 3}   # no ask: lean buttons


def test_a_table_mode_mark_without_a_choice_still_shows_its_lean():
    s = session(marks={"D2": {"lean": "toward_underwrite", "at_ms": 1}})
    out = run("""const box = new N('div'); D.renderDecision(box, mkctx(p.s));
      return { hint: one(step(box, 'D2'), 'dp-ask-hint').textContent,
               on: byCls(step(box, 'D2'), 'dp-opt').filter((x) => x.className.includes(' on')).length };""", {"s": s})
    assert out == {"hint": "记为：更倾向承保", "on": 0}


def test_note_save_posts_note_and_reasons_and_unsupported_items_are_labelled():
    out = run("""const box = new N('div'); D.renderDecision(box, mkctx(p.s));
      const d2 = () => step(box, 'D2');
      const verdicts = texts(d2(), 'dp-ev-verdict');
      const unsupportedLabel = one(d2(), 'unsupported').textContent;
      const grades = byCls(d2(), 'dp-grade').map((x) => [x.textContent, x.className]);
      const cb = (k) => walk(d2()).find((x) => x.tagName === 'INPUT' && x.dataset.ev === k);
      cb('wall_ahead').fire('change', { checked: true });
      cb('iv_pct_low').fire('change', { checked: true });
      const area = walk(d2()).find((x) => x.tagName === 'TEXTAREA');
      area.fire('input', { value: '墙在前面，但分位低让我犹豫' });
      const pending = texts(d2(), 'small').concat(byCls(d2(), 'muted').map((x) => x.textContent)).find((t) => t.startsWith('将引用'));
      walk(d2()).find((x) => x.tagName === 'BUTTON' && x.textContent === '保存备注').fire('click');
      await tick();
      return { posts, verdicts, unsupportedLabel, grades, pending,
               editorAfter: walk(d2()).filter((x) => x.tagName === 'TEXTAREA').length };""",
              {"s": session(verification_id="v-note")})
    assert out["posts"] == [{"step": "D2", "note": "墙在前面，但分位低让我犹豫", "reasons": ["wall_ahead", "iv_pct_low"]}]
    assert out["verdicts"] == ["研究支持（弱）", "研究不支持作为依据", "未检验"]
    assert "研究不支持作为依据" in out["unsupportedLabel"] and "引用" in out["unsupportedLabel"]
    assert [g[0] for g in out["grades"]] == ["B", "X", "未检验"]
    assert "strong" in out["grades"][0][1] and "x" in out["grades"][1][1].split() and "x" in out["grades"][2][1].split()
    assert out["pending"] == "将引用：短腿前有正 Gamma 墙、IV 分位低（个人判断，研究不支持）"
    assert out["editorAfter"] == 0


def test_saved_note_shows_text_time_and_cited_reasons():
    s = session(notes={"D2": {"text": "分位低不算数", "reasons": ["iv_pct_low"], "at_ms": 1_792_494_600_000}})
    out = run("""const box = new N('div'); D.renderDecision(box, mkctx(p.s));
      const saved = one(step(box, 'D2'), 'dp-saved');
      return { text: saved.textContent, link: one(step(box, 'D2'), 'dp-link').textContent,
               checked: walk(step(box, 'D2')).find((x) => x.dataset.ev === 'iv_pct_low').attrs.checked };""", {"s": s})
    assert "分位低不算数" in out["text"] and "引用：IV 分位低（个人判断，研究不支持）" in out["text"]
    assert out["text"].startswith("备注（") and out["link"] == "改备注" and out["checked"] == ""


def test_saved_reasons_as_objects_carry_their_own_verdict():
    reasons = [{"key": "gone_item", "label": "已下线的依据", "grade": "X", "verdict_cn": "研究不支持作为依据"},
               {"key": "wall_ahead", "label": "短腿前有正 Gamma 墙", "grade": "B", "verdict_cn": "研究支持（弱）"}]
    s = session(notes={"D2": {"text": "", "reasons": reasons, "at_ms": 1_792_494_600_000}})
    out = run("""const box = new N('div'); D.renderDecision(box, mkctx(p.s));
      return { cite: one(step(box, 'D2'), 'dp-saved-cite').textContent,
               checked: walk(step(box, 'D2')).find((x) => x.dataset.ev === 'wall_ahead').attrs.checked ?? null };""", {"s": s})
    assert out["cite"] == "引用：已下线的依据（个人判断，研究不支持）、短腿前有正 Gamma 墙"
    assert out["checked"] == ""


def test_toggle_switches_to_the_table_and_back_and_is_remembered():
    out = run("""const box = new N('div'); D.renderDecision(box, mkctx(p.s));
      const btn = (t) => byCls(box, 'dp-view-btn').find((x) => x.textContent === t);
      btn('明细').fire('click');
      const table = { lines: byCls(step(box, 'D2'), 'dp-lines').length, para: byCls(step(box, 'D2'), 'dp-para').length,
                      marks: byCls(step(box, 'D2'), 'mark').length, stored: localStorage.getItem('astra.dp.view'),
                      pressed: btn('明细').attrs['aria-pressed'] };
      btn('叙述').fire('click');
      return { table, back: byCls(step(box, 'D2'), 'dp-para').length, stored: localStorage.getItem('astra.dp.view') };""",
              {"s": SESSION})
    assert out["table"] == {"lines": 1, "para": 0, "marks": 3, "stored": "table", "pressed": "true"}
    assert out["back"] == 1 and out["stored"] == "narrative"


def test_no_narrative_falls_back_to_the_table_without_a_toggle():
    s = session()
    del s["readings"]["start"]["narrative"]
    out = run("""const box = new N('div'); D.renderDecision(box, mkctx(p.s));
      return { toggle: byCls(box, 'dp-view').length, lines: byCls(box, 'dp-lines').length, para: byCls(box, 'dp-para').length,
               marks: byCls(step(box, 'D2'), 'mark').length };""", {"s": s})
    assert out == {"toggle": 0, "lines": 3, "para": 0, "marks": 3}


def now_session(changes, q2_changes=()):
    s = session()
    now_d2 = copy.deepcopy(D2_NARRATIVE)
    now_d2["lead"] = "现价回落到 79,100，离墙更近。"
    now_d2["footnotes"] = [{"n": 1, "text": "现在的 KPF：78,900 出现密集区。"}]
    if changes is not None:
        now_d2["changes"] = changes
    now_q2 = dict(copy.deepcopy(Q2_NARRATIVE), changes=list(q2_changes))
    s["readings"]["now"] = {"at_ms": 1_792_497_600_000, "steps": copy.deepcopy(s["readings"]["start"]["steps"]),
                            "narrative": {"schema": "dp_narrative@0.1", "steps": {"D2": now_d2, "Q2": now_q2}}}
    return s


NOW_BODY = """const box = new N('div'); D.renderDecision(box, mkctx(p.s));
  const nb = () => one(step(box, 'D2'), 'dp-now-box');
  const res = { d2: one(step(box, 'D2'), 'dp-now').textContent, q2: one(step(box, 'Q2'), 'dp-now').textContent,
                removed: byCls(step(box, 'D2'), 'removed').map((x) => [x.textContent, x.className]) };
  const mark = nb() && byCls(nb(), 'dp-fn')[0];
  if (mark) { mark.fire('click');
    res.nowList = walk(nb()).filter((x) => x.tagName === 'OL').map((x) => x.textContent);
    res.startList = walk(step(box, 'D2')).filter((x) => x.tagName === 'OL' && !walk(nb()).includes(x)).length; }
  return res;"""


def test_now_reading_lists_the_server_changes_with_its_own_footnotes():
    changes = [{"kind": "changed", "text": "路上出现成交密集区 78,900。", "fn": [1]},
               {"kind": "removed", "text": "不再有：短腿位于负 Gamma 区。", "fn": []}]
    out = run(NOW_BODY, {"s": now_session(changes)})
    assert out["d2"].startswith("现在：现价回落到 79,100") and "路上出现成交密集区 78,900。" in out["d2"]
    assert "短腿距现价 1.24%" not in out["d2"]
    assert out["removed"][0][0] == "不再有：短腿位于负 Gamma 区。" and "muted" in out["removed"][0][1]
    assert out["nowList"] == ["1 现在的 KPF：78,900 出现密集区。"] and out["startList"] == 0   # never the start list
    assert out["q2"] == "现在：与开始时相同。"


def test_now_reading_without_a_change_list_shows_the_whole_now_reading():
    out = run(NOW_BODY, {"s": now_session(None)})
    assert out["d2"].startswith("现在：现价回落到 79,100")
    for s in D2_NARRATIVE["sentences"]:
        assert s["text"] in out["d2"]
    assert "与开始时相同" not in out["d2"]


def test_closed_session_keeps_the_note_but_disables_input():
    s = session(status="closed", conclusion={"choice": "decline", "at_ms": 1},
                notes={"D2": {"text": "不做", "reasons": [], "at_ms": 1_792_494_600_000}})
    out = run("""const box = new N('div'); D.renderDecision(box, mkctx(p.s));
      const d2 = step(box, 'D2');
      return { disabled: byCls(d2, 'dp-opt').every((x) => 'disabled' in x.attrs), link: byCls(d2, 'dp-link').length,
               saved: one(d2, 'dp-saved').textContent, cbs: walk(d2).filter((x) => x.tagName === 'INPUT').every((x) => 'disabled' in x.attrs) };""",
              {"s": s})
    assert out["disabled"] and out["cbs"] and out["link"] == 0 and "不做" in out["saved"]


def test_narrative_text_never_goes_through_inner_html():
    src = (PAGE / "decision.js").read_text(encoding="utf-8")
    assert "innerHTML" not in src and "insertAdjacentHTML" not in src
