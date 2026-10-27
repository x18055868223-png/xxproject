// Decision Path: verify one frozen policy in a fixed reading order (the order is defined on
// the server and versioned). The cards choose the object; this section checks it.
// Readings are the server's program readings in a fixed vocabulary; details stay in the
// components they come from (map, five elements, events, audit) and are reached by a link,
// never redrawn here. Locking is one possible conclusion; the card's direct lock stays.
import { h, clear, isNum, usd, clock, ago } from "./format.js";

export const MARK_CN = { toward_underwrite: "更倾向承保", unchanged: "没变", toward_decline: "更倾向不承保" };
const CHOICE_CN = { lock: "锁定追踪", decline: "不承保", watch: "继续观察", unconcluded: "到期未结论" };
// Where each step's facts are drawn in full: [section heading id, label].
const LINKS = {
  T: [["map-h", "时段条"]],
  D2: [["map-h", "价格地图"], ["factor-h", "空间位置"], ["src-h", "锚点效用变迁"]],
  D3: [["factor-h", "量价路径与主动成交"]],
  D4: [["event-h", "外源性事件"]],
  D5: [["src-h", "锚点效用变迁"]],          // sessions started on path v1
  AU: [["audit-h", "推理审计"]],
  // dp-2026-10-27.1: R reads D2, T, D4 and D3 (V and P read the card's own facts)
  R: [["map-h", "价格地图"], ["map-h", "时段条"], ["factor-h", "量价路径与主动成交"], ["event-h", "外源性事件"]],
};
const TONE = { bad: "bad", warn: "warn", ok: "ok" };
const CHIP_PREFIX = { D2: "缓冲" };

const stepsOf = (session) => session?.steps || [];
/** Whether a step takes marks, by the steps the session started with (server rule, docs/opus/14). */
const markable = (step) => (typeof step.markable === "boolean" ? step.markable : !step.on_demand);
const MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
/** Deribit expiry code (15OCT26) from an expiry time, so a session reads like its instruments. */
const expiryCode = (ms) => {
  if (!isNum(ms)) return "";
  const d = new Date(ms);
  return `${d.getUTCDate()}${MON[d.getUTCMonth()]}${String(d.getUTCFullYear()).slice(2)}`;
};
const titleOf = (session, id) => stepsOf(session).find((s) => s.id === id)?.title || id;
/** The step after which a conclusion can be given: flagged by the server (dp-2026-10-27.1 on), Q2 before. */
export function gateStepId(session) {
  const steps = stepsOf(session);
  return (steps.find((s) => s.conclusion_gate) || steps.find((s) => s.id === "Q2"))?.id || null;
}
const latestReading = (session) => session?.readings?.now || session?.readings?.start || null;
const BJ_HM = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false });
const hm = (ms) => (isNum(ms) ? BJ_HM.format(new Date(ms)) : "—");
const BJ_DAY = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit" });
// HH:MM, with the Beijing date when it is not the day of `from`
const until = (ms, from) => (isNum(ms) && isNum(from) && BJ_DAY.format(new Date(ms)) !== BJ_DAY.format(new Date(from))
  ? `${BJ_DAY.format(new Date(ms))} ${hm(ms)}` : hm(ms));
const btc4 = (v) => (isNum(v) ? `${v.toFixed(4)} BTC` : "—");

/** Index of the last step the user has opened (steps open one after another). */
export function revealedIndex(session) {
  const opened = session?.opened || {};
  const ids = stepsOf(session).map((s) => s.id);
  let last = -1;
  ids.forEach((id, i) => { if (id in opened) last = Math.max(last, i); });
  return last;
}

function chip(verdict, prefix = "") {
  if (!verdict || !verdict.cn) return null;
  return h("span", { class: `chip ${TONE[verdict.tone] || ""}`, text: prefix + verdict.cn });
}

// the lines the P box already shows (保留价 against the bid and the mid)
const BOX_KEYS = new Set(["保留价", "现在可得", "两腿中间价", "保留价可靠性"]);
const boxed = (lines, drop) => (drop ? (lines || []).filter((l) => !BOX_KEYS.has(l?.k)) : lines);

/** One step's lines; with a "now" reading, start and now side by side, matched by label. */
function linesView(session, id, start, now, drop = false) {
  const rows = new Map();
  const keyed = (lines) => {
    const seen = {};
    return (lines || []).map((l) => [`${l.k}#${(seen[l.k] = (seen[l.k] ?? -1) + 1)}`, l]);
  };
  for (const [key, l] of keyed(boxed(start?.lines, drop))) rows.set(key, { k: l.k, a: l, b: null });
  for (const [key, l] of keyed(boxed(now?.lines, drop))) {
    const r = rows.get(key);
    if (r) r.b = l; else rows.set(key, { k: l.k, a: null, b: l });
  }
  const isStep = (k) => stepsOf(session).some((s) => s.id === k);
  const label = (k) => (id === "Q1" && isStep(k) ? `${k} ${titleOf(session, k)}` : k);
  const table = h("div", { class: `dp-lines${now ? " two" : ""}` });
  if (now) table.append(h("span", { class: "dp-col", text: "" }), h("span", { class: "dp-col", text: "开始时" }), h("span", { class: "dp-col", text: "现在" }));
  for (const r of rows.values()) {
    table.append(h("span", { class: "dp-k", text: label(r.k) }));
    // the grade and source of a reading (path v2, docs/opus/20), under its value
    const basis = (r.a || r.b)?.basis;
    table.append(h("span", { class: `dp-v ${TONE[r.a?.tone] || ""}`, title: basis ? `依据：${basis}` : null },
      r.a ? r.a.v : "—", basis && !now ? h("small", { class: "dp-basis", text: basis }) : null));
    if (now) {
      const same = r.a && r.b && r.a.v === r.b.v;
      table.append(h("span", { class: `dp-v ${TONE[r.b?.tone] || ""}${same ? " same" : ""}`, text: r.b ? (same ? "同左" : r.b.v) : "—" }));
    }
  }
  return table;
}

function marksView(session, id, ctx) {
  const current = session.marks?.[id]?.lean;
  const box = h("div", { class: "dp-marks", role: "group", "aria-label": "这一步对倾向的影响",
    title: "这一步是否改变了你的倾向；不标记视为没变。用于检验哪些步骤真正有增量。" },
    h("span", { class: "muted small", text: "这一步让你：" }));
  for (const [lean, text] of Object.entries(MARK_CN)) {
    box.append(h("button", { class: `mark${current === lean ? " on" : ""} ${lean}`, type: "button", "aria-pressed": String(current === lean),
      disabled: !ctx.canAct || ctx.busy || session.status !== "open",
      onclick: () => ctx.onStep(id, lean) }, text));
  }
  return box;
}

// ── narrative view (dp_narrative@0.1): the same readings as sentences, the table stays as 明细 ──
const VIEW_KEY = "astra.dp.view";
const NOTE_MAX = 300;
const REASONS_MAX = 8;
const GRADE_CLS = { A: "strong", B: "strong", C: "c" };
const DIR_CN = { underwrite: "偏承保", decline: "偏不承保", neutral: "中性" };
// Per verification and step: what is open on screen and the unsaved draft, so a redraw keeps them.
const ui = new Map();
const uiOf = (session, id) => {
  const key = `${session.verification_id}/${id}`;
  if (!ui.has(key)) ui.set(key, { id, fnOpen: new Set(), fnAll: false, editor: false, draft: null, reasons: null });
  return ui.get(key);
};

export function viewMode() {
  try { return localStorage.getItem(VIEW_KEY) === "table" ? "table" : "narrative"; } catch { return "narrative"; }
}
function setViewMode(mode) {
  try { localStorage.setItem(VIEW_KEY, mode); } catch { /* private window: the choice lasts this page only */ }
  memoMode = mode;
}
let memoMode = null;
const currentMode = () => memoMode || viewMode();
// The last box and context drawn: local toggles redraw with the newest state the page passed in.
let last = null;
// A redraw is held while the note box has the keyboard or an IME is composing, and done afterwards.
const redraw = (force = false) => { if (last) renderDecision(last.box, last.ctx, { force }); };
let composing = false;
let pressing = false;
let deferred = false;
const typing = () => {
  const el = typeof document !== "undefined" ? document.activeElement : null;
  return composing || (!!el?.dataset?.dpNote && el.tagName === "TEXTAREA") || (!!el?.dataset?.dpLimit && el.tagName === "INPUT");
};
const watched = new WeakSet();
function watchTyping(box) {
  if (watched.has(box) || !box.addEventListener) return;
  watched.add(box);
  box.addEventListener("compositionstart", () => { composing = true; });
  box.addEventListener("compositionend", () => { composing = false; });
  // a press inside the section is followed by its own click handler, which redraws if it needs to
  box.addEventListener("pointerdown", () => { pressing = true; });
  box.addEventListener("pointercancel", () => { pressing = false; });
  box.addEventListener("click", () => { pressing = false; if (deferred && !typing()) setTimeout(() => deferred && redraw(), 0); });
  box.addEventListener("focusout", (e) => {
    if (!(e.target?.dataset?.dpNote || e.target?.dataset?.dpLimit) || pressing) return;
    setTimeout(() => { if (deferred && !typing()) redraw(); }, 0);
  });
}

const narrativeOf = (session, which, id) => session.readings?.[which]?.narrative?.steps?.[id] || null;
const hasNarrative = (session) => Object.keys(session.readings?.start?.narrative?.steps || {}).length > 0;
const unsupported = (ev) => /研究不支持/.test(ev?.verdict_cn || "");

function viewToggle() {
  const mode = currentMode();
  const seg = h("div", { class: "dp-view", role: "group", "aria-label": "核验路径的显示方式" });
  for (const [m, text, tip] of [["narrative", "叙述", "按句子读，每句可展开依据"], ["table", "明细", "逐项读数，开始时与现在并列"]]) {
    seg.append(h("button", { type: "button", class: `dp-view-btn${mode === m ? " on" : ""}`, "aria-pressed": String(mode === m), title: tip, dataset: { fk: `view:${m}` },
      onclick: () => { if (currentMode() !== m) { setViewMode(m); redraw(); } } }, text));
  }
  return seg;
}

/** Superscript footnote markers; each toggles its footnote inline under the paragraph. */
function fnMarks(fns, st) {
  return (fns || []).map((n) => h("button", { type: "button", class: `dp-fn${st.fnOpen.has(n) ? " on" : ""}`,
    "aria-expanded": String(st.fnOpen.has(n)), "aria-label": `依据 ${n}`, dataset: { fn: String(n), fk: `${st.id}:fn:${n}` },
    onclick: () => { if (st.fnOpen.has(n)) st.fnOpen.delete(n); else st.fnOpen.add(n); redraw(); } }, String(n)));
}

function sentencesOf(n, st, only = null) {
  const out = [];
  if (n.lead) out.push(h("span", { class: "dp-lead", text: n.lead }), " ");
  for (const s of n.sentences || []) {
    if (only && !only(s)) continue;
    out.push(h("span", { class: `dp-sent${s.tone === "warn" || s.tone === "bad" ? ` ${s.tone}` : ""}`, text: s.text }),
      ...fnMarks(s.fn, st), " ");
  }
  return out;
}

function footnotesView(n, st, title = "依据") {
  const notes = n.footnotes || [];
  if (!notes.length) return null;
  const box = h("div", { class: "dp-fns" });
  const opened = notes.filter((f) => st.fnOpen.has(f.n));
  if (opened.length && !st.fnAll) {
    box.append(h("ol", { class: "dp-fn-list open" }, ...opened.map((f) =>
      h("li", { dataset: { fn: String(f.n) } }, h("sup", { text: String(f.n) }), " ", h("span", { text: f.text })))));
  }
  box.append(h("button", { type: "button", class: "dp-fn-all", "aria-expanded": String(st.fnAll), dataset: { fk: `${st.id}:fnall` },
    onclick: () => { st.fnAll = !st.fnAll; redraw(); } }, `${st.fnAll ? "收起" : ""}${title} ${notes.length} 条`));
  if (st.fnAll) {
    box.append(h("ol", { class: "dp-fn-list" }, ...notes.map((f) =>
      h("li", { dataset: { fn: String(f.n) } }, h("sup", { text: String(f.n) }), " ", h("span", { text: f.text })))));
  }
  return box;
}

/** Readings of the table that no sentence covers: listed so the narrative loses nothing. */
function uncoveredView(n, start) {
  const lines = start?.lines || [];
  if (!Array.isArray(n.covered) || !lines.length) return null;
  const covered = new Set(n.covered);
  const rest = lines.filter((_, i) => !covered.has(i));
  if (!rest.length) return null;
  return h("p", { class: "dp-rest muted small", text: `另有读数：${rest.map((l) => `${l.k} ${l.v}`).join("；")}` });
}

/** What changed since the start, as the server lists it; markers resolve against the NOW footnotes. */
function nowView(nStart, nNow, stNow) {
  if (!nNow) return null;
  const changes = nNow.changes;
  if (Array.isArray(changes) && !changes.length) return h("p", { class: "dp-now muted small", text: "现在：与开始时相同。" });
  const p = h("p", { class: "dp-now" }, h("b", { text: "现在：" }));
  if (nNow.lead && (nNow.lead !== nStart?.lead || !Array.isArray(changes))) p.append(h("span", { class: "dp-lead", text: nNow.lead }), " ");
  // an old server sends no change list: show the whole now reading rather than claim nothing changed
  const items = Array.isArray(changes) ? changes : (nNow.sentences || []).map((s) => ({ kind: "changed", ...s }));
  for (const c of items) {
    const tone = c.kind === "removed" ? " muted" : c.tone === "warn" || c.tone === "bad" ? ` ${c.tone}` : "";
    p.append(h("span", { class: `dp-sent dp-chg ${c.kind || ""}${tone}`, text: c.text }), ...fnMarks(c.fn, stNow), " ");
  }
  return h("div", { class: "dp-now-box" }, p, footnotesView(nNow, stNow, "现在的依据"));
}

/** A cited reason: a key (old notes) or {key, label, grade, verdict_cn} (new notes). */
function reasonLabels(reasons, evidence) {
  return (reasons || []).map((r) => {
    const key = typeof r === "string" ? r : r?.key;
    const known = evidence.find((e) => e.key === key);
    const ev = r && typeof r === "object" ? { ...known, ...r } : known;
    const label = ev?.label || key || "—";
    return unsupported(ev) ? `${label}（个人判断，研究不支持）` : label;
  });
}
const reasonKey = (r) => (typeof r === "string" ? r : r?.key);
const reasonsOf = (session, id, st) => st.reasons ?? (session.notes?.[id]?.reasons || []).map(reasonKey).filter(Boolean);

function evidenceView(session, id, n, st, ctx) {
  const evidence = n.evidence || [];
  if (!evidence.length) return null;
  const live = session.status === "open" && ctx.canAct;
  const list = h("ul", { class: "dp-ev" });
  const boxes = [];
  // ticks change the state and the controls in place: no redraw under the cursor
  const sync = () => {
    const now = reasonsOf(session, id, st);
    for (const [b, key] of boxes) { b.checked = now.includes(key); b.disabled = !live || (!b.checked && now.length >= REASONS_MAX); }
    if (st.citeLine) st.citeLine.textContent = now.length ? `将引用：${reasonLabels(now, evidence).join("、")}` : "";
  };
  for (const ev of evidence) {
    const reasons = reasonsOf(session, id, st);
    const on = reasons.includes(ev.key);
    const grade = ev.grade || "未检验";
    const box = h("input", { type: "checkbox", checked: on || null, disabled: !live || (!on && reasons.length >= REASONS_MAX) || null,
      dataset: { ev: ev.key, fk: `${id}:ev:${ev.key}` }, "aria-label": `引用：${ev.label}`,
      onchange: (e) => {
        const next = new Set(reasonsOf(session, id, st));
        if (e.target.checked) next.add(ev.key); else next.delete(ev.key);
        st.reasons = [...next].slice(0, REASONS_MAX);
        if (!st.editor) { st.editor = true; redraw(true); return; }   // the editor appears so the citation can be saved
        sync();
      } });
    boxes.push([box, ev.key]);
    list.append(h("li", { class: `dp-ev-item${unsupported(ev) ? " unsupported" : ""}` },
      h("label", { class: "dp-ev-cite" }, box, h("span", { text: "引用" })),
      h("span", { class: "dp-ev-label", text: ev.label }),
      ev.value != null && ev.value !== "" ? h("span", { class: "dp-ev-value", text: String(ev.value) }) : null,
      h("span", { class: `dp-grade ${GRADE_CLS[grade] || "x"}`, title: "研究分级", text: grade }),
      h("span", { class: `dp-ev-verdict${unsupported(ev) ? " no" : ""}`, text: ev.verdict_cn || "" }),
      ev.direction && DIR_CN[ev.direction] ? h("span", { class: "dp-ev-dir muted small", text: DIR_CN[ev.direction] }) : null,
      ev.source ? h("small", { class: "dp-ev-src", text: ev.source }) : null));
  }
  return h("div", { class: "dp-evidence" }, h("div", { class: "muted small", text: "系统给出的可引用理由（勾选“引用”随备注保存）：" }), list);
}

const NOTE_ERR = { note_required: "这个选项需要写下理由" };
const resetNote = (st) => Object.assign(st, { editor: false, draft: null, reasons: null, pending: null, error: null });

function noteView(session, id, n, st, ctx, ask) {
  const saved = session.notes?.[id];
  const evidence = n?.evidence || [];
  const live = session.status === "open" && ctx.canAct;
  const box = h("div", { class: "dp-notes" });
  st.citeLine = null;
  if (saved && (saved.text || saved.reasons?.length)) {
    const p = h("p", { class: "dp-saved" }, h("span", { class: "muted small", text: `备注${saved.at_ms ? `（${clock(saved.at_ms)}）` : ""}：` }));
    if (saved.text) p.append(h("span", { class: "dp-saved-text", text: saved.text }));
    if (saved.reasons?.length) p.append(h("span", { class: "dp-saved-cite muted small", text: `引用：${reasonLabels(saved.reasons, evidence).join("、")}` }));
    box.append(p);
  }
  if (!live) return box.childNodes.length ? box : null;
  if (!st.editor) {
    box.append(h("button", { type: "button", class: "dp-link", dataset: { fk: `${id}:note` }, onclick: () => { st.editor = true; st.focus = true; redraw(true); } },
      saved?.text ? "改备注" : "加备注"));
    return box;
  }
  const draft = st.draft ?? (saved?.text || "");
  const pending = (ask?.options || []).find((o) => o.key === st.pending) || null;
  const count = h("span", { class: "muted small", text: `${draft.length}/${NOTE_MAX}` });
  const area = h("textarea", { class: "dp-note-input", rows: 3, maxlength: NOTE_MAX,
    placeholder: pending ? `“${pending.label}”需要写下理由（最多 300 字）` : "为什么这样判断（最多 300 字）",
    "aria-label": `${id} 备注`, dataset: { dpNote: id, fk: `${id}:area` },
    oninput: (e) => { st.draft = e.target.value; count.textContent = `${st.draft.length}/${NOTE_MAX}`; } });
  area.value = draft;
  const save = async () => {
    const note = String(st.draft ?? draft).slice(0, NOTE_MAX);
    if (pending && !note.trim()) { st.error = NOTE_ERR.note_required; redraw(true); return; }
    st.error = null;
    area.blur?.();
    const body = { note, reasons: reasonsOf(session, id, st).slice(0, REASONS_MAX),
      ...(pending ? { lean: pending.lean, choice: pending.key } : {}) };
    // the draft stays until the server has it: a failed save keeps every word
    const res = await ctx.onNote?.(id, body);
    if (res && res.ok === false) st.error = NOTE_ERR[res.code] || res.message || "保存未完成";
    else resetNote(st);
    redraw(true);
  };
  const cite = h("span", { class: "muted small dp-cite-line" });
  st.citeLine = cite;
  const reasons = reasonsOf(session, id, st);
  if (reasons.length) cite.textContent = `将引用：${reasonLabels(reasons, evidence).join("、")}`;
  box.append(area, h("div", { class: "dp-note-actions" },
    h("button", { type: "button", class: "btn", disabled: !!ctx.busy, onclick: save }, pending ? "保存选择与理由" : "保存备注"),
    h("button", { type: "button", class: "btn secondary", onclick: () => { area.blur?.(); resetNote(st); redraw(true); } }, "取消"),
    count, cite),
  st.error ? h("p", { class: "dp-error", role: "alert", text: st.error }) : null);
  if (st.focus) { st.focus = false; area.dataset.focus = "1"; }
  return box;
}

function askView(session, id, n, st, ctx) {
  const ask = n.ask;
  const mark = session.marks?.[id];
  const box = h("div", { class: "dp-ask" }, h("b", { class: "dp-ask-q", text: ask.question }));
  const chips = h("div", { class: "dp-ask-opts", role: "group", "aria-label": ask.question });
  const disabled = !ctx.canAct || !!ctx.busy || session.status !== "open";
  const chosen = st.pending ?? mark?.choice;
  for (const o of ask.options || []) {
    const on = chosen === o.key;
    chips.append(h("button", { type: "button", class: `dp-opt${on ? " on" : ""}${st.pending === o.key ? " pending" : ""} ${o.lean || ""}`,
      "aria-pressed": String(on), title: `记为：${MARK_CN[o.lean] || "—"}${o.needs_note ? "（需要写下理由）" : ""}`, disabled,
      dataset: { opt: o.key, fk: `${id}:opt:${o.key}` },
      onclick: () => {
        // a choice that needs a reason is held here and posted with its note, in one request
        if (o.needs_note) { Object.assign(st, { pending: o.key, editor: true, focus: true, error: null }); redraw(true); return; }
        st.pending = null;
        redraw(true);   // first, so the server round trip redraws over it with the busy state
        ctx.onStep(id, o.lean, { choice: o.key });
      } }, o.label));
  }
  box.append(chips);
  const pending = (ask.options || []).find((o) => o.key === st.pending);
  const picked = (ask.options || []).find((o) => o.key === mark?.choice);
  box.append(h("div", { class: "dp-ask-hint muted small", text: pending
    ? `记为：${MARK_CN[pending.lean] || "—"}（写下理由并保存后才记录）`
    : picked ? `记为：${MARK_CN[picked.lean] || "—"}`
      : mark?.lean && MARK_CN[mark.lean] ? `记为：${MARK_CN[mark.lean]}`
        : `选项记为：${(ask.options || []).map((o) => `${o.label} → ${MARK_CN[o.lean] || "—"}`).join("；")}` }));
  return box;
}

/** One step as a paragraph; returns null when the server sent no narrative for it. */
function narrativeBody(session, step, start, now, ctx, drop = false) {
  const n = narrativeOf(session, "start", step.id);
  if (!n) return null;
  const st = uiOf(session, step.id);
  // sentences made only of lines the P box shows are left to the box
  const only = drop ? (s) => !(s.keys?.length && s.keys.every((k) => BOX_KEYS.has(k))) : null;
  const read = [h("p", { class: "dp-para" }, ...sentencesOf(n, st, only))];
  if (n.background?.length) {
    // each background item already says what kind of background it is ("背景（研究不支持作为依据）：…")
    read.push(h("p", { class: "dp-bg" },
      ...n.background.flatMap((b) => [h("span", { text: b.text }), ...fnMarks(b.fn, st), " "])));
  }
  read.push(footnotesView(n, st), uncoveredView(n, start));
  if (now) read.push(nowView(n, narrativeOf(session, "now", step.id), uiOf(session, `${step.id}@now`)));
  const act = [];
  if (markable(step)) act.push(n.ask ? askView(session, step.id, n, st, ctx) : marksView(session, step.id, ctx));
  act.push(evidenceView(session, step.id, n, st, ctx));
  if (markable(step) || n.ask || n.evidence?.length) act.push(noteView(session, step.id, n, st, ctx, n.ask));
  return { read: read.filter(Boolean), act: act.filter(Boolean) };
}

// ── V (veto summary) and P (reservation price), dp-2026-10-27.1 ──
/** Whether a step's reading is the unsuitable list (a failed V keeps its own lines). */
const isVetoList = (step, r) => !!step.veto_summary && Array.isArray(r?.items) && r?.verdict?.code !== "error";
const itemCodes = (r) => (r?.items || []).map((i) => `${i.kind}:${i.code}`).join(",");

/** One reading's unsuitable items, prominent; then the checks line. No item: the plain sentence. */
function vetoItems(r) {
  const box = h("div", { class: "dp-veto" });
  const items = (r?.items || []).filter((i) => i && i.title_cn);
  if (items.length) {
    box.append(h("ul", { class: "dp-veto-list" }, ...items.map((i) => h("li", {
      class: `dp-veto-item ${i.kind === "veto" ? "bad" : "warn"}` },
    h("div", { class: "dp-veto-head" }, h("span", { class: `chip ${i.kind === "veto" ? "bad" : "warn"}`, text: i.kind === "veto" ? "否决" : "提醒" }),
      h("b", { class: "dp-veto-title", text: i.title_cn })),
    i.evidence_cn ? h("span", { class: "dp-veto-ev num", text: i.evidence_cn }) : null,
    i.why_cn ? h("span", { class: "dp-veto-why", text: i.why_cn }) : null,
    i.clears_cn ? h("span", { class: "dp-veto-clears muted small", text: `解除条件：${i.clears_cn}` }) : null,
    i.basis ? h("small", { class: "dp-basis", text: i.basis }) : null))));
  } else {
    box.append(h("p", { class: "dp-veto-none", text: r?.verdict?.cn || "未见否决项（这不是做的理由）" }));
  }
  const checks = (r?.lines || []).find((l) => l && l.k === "检查项");
  if (checks) box.append(h("p", { class: "dp-veto-checks muted small", text: `检查项：${checks.v}` }));
  const unknown = (r?.unknown_cn || []).filter(Boolean);
  if (unknown.length) box.append(h("p", { class: "dp-veto-checks muted small", text: `无法判断：${unknown.join("、")}` }));
  return box;
}

function vetoView(start, now) {
  const out = [vetoItems(start)];
  if (!now) return out;
  if (itemCodes(now) === itemCodes(start) && now.verdict?.code === start?.verdict?.code) {
    out.push(h("p", { class: "dp-now muted small", text: "现在：与开始时相同。" }));
  } else {
    out.push(h("div", { class: "dp-now-box" }, h("p", { class: "dp-now" }, h("b", { text: "现在：" })), vetoItems(now)));
  }
  return out;
}

/** The reservation price against the credit at the bid and at the two legs' mid (latest reading). */
function reservationBox(session, step) {
  const latest = latestReading(session);
  const res = latest?.steps?.[step.id]?.reservation;
  if (!res || typeof res !== "object") return null;
  const amount = (btc, u) => (isNum(u) ? `约 ${usd(Math.abs(u))} 美元` : isNum(btc) ? `${Math.abs(btc).toFixed(4)} BTC` : "—");
  const gap = (g, gu) => (!isNum(g) ? "" : g > 0 ? `（差 ${amount(g, gu)}）` : `（不低于保留价，多 ${amount(g, gu)}）`);
  const reserve = res.reservation_btc;
  const box = h("div", { class: "dp-reserve", title: "保留价 = 两腿标记价差 + 费用：挂单不低于它，净收才不低于市场估价" });
  if (!isNum(reserve)) {
    box.append(h("p", { class: "dp-reserve-main warn" }, h("b", { text: "保留价" }), " 算不出（两腿标记价或费用缺失）"));
    return box;
  }
  box.append(h("p", { class: "dp-reserve-main" }, h("b", { text: "保留价" }), " ",
    h("span", { class: "num", text: btc4(reserve) }), isNum(res.reservation_usd) ? `（约 ${usd(res.reservation_usd)} 美元）` : "",
    session.readings?.now ? h("span", { class: "muted small", text: "　现在的读数" }) : null));
  const bid = res.visible_credit_btc;
  box.append(h("p", { class: `dp-reserve-row${isNum(res.bid_gap_btc) && res.bid_gap_btc > 0 ? " warn" : ""}` },
    "按买价可得 ", h("span", { class: "num", text: btc4(bid) }), gap(res.bid_gap_btc, res.bid_gap_usd)));
  const mid = res.mid_credit_btc;
  box.append(h("p", { class: `dp-reserve-row${isNum(res.mid_gap_btc) && res.mid_gap_btc > 0 ? " warn" : ""}` },
    "两腿中间价 ", isNum(mid) ? h("span", { class: "num", text: btc4(mid) }) : "不可算（缺对侧盘口）", isNum(mid) ? gap(res.mid_gap_btc, res.mid_gap_usd) : ""));
  if (res.reservation_reliable === false) {
    box.append(h("p", { class: "dp-reserve-row warn", text: "保护腿盘口空或过宽，标记价可能失真，保留价仅供参考。" }));
  }
  return box;
}

function stepView(session, step, i, ctx) {
  const start = session.readings?.start?.steps?.[step.id];
  const now = session.readings?.now?.steps?.[step.id];
  const box = h("li", { class: "dp-step", id: `dp-${step.id}` });
  let told = null;   // the narrative body, when this step has one and the narrative view is on
  const head = h("div", { class: "dp-step-head", tabindex: -1 },
    h("span", { class: "dp-id", text: step.id }),
    h("b", { text: step.title }),
    h("span", { class: "dp-q", text: step.question }),
    chip(start?.verdict, (now ? "开始时 " : "") + (CHIP_PREFIX[step.id] || "")),
    now && now.verdict?.code !== start?.verdict?.code ? chip(now.verdict, "现在 " + (CHIP_PREFIX[step.id] || "")) : null);
  box.append(head);
  if (step.id === "AU") {
    box.append(h("p", { class: "muted small", text: start?.note || "按需触发" }));
    if (ctx.extras?.AU) box.append(ctx.extras.AU);
  } else if (isVetoList(step, start)) {
    // the items are the reading in both views; the narrative adds only its question, evidence and note
    told = ctx.mode === "narrative" ? narrativeBody(session, step, start, now, ctx) : null;
    box.append(...vetoView(start, now));
  } else {
    const reserve = step.premium ? reservationBox(session, step) : null;   // only readings that carry one (P)
    if (reserve) box.append(reserve);
    // the paragraph replaces the table; the question, reasons and note replace the three lean buttons
    told = ctx.mode === "narrative" ? narrativeBody(session, step, start, now, ctx, !!reserve) : null;
    box.append(...(told ? told.read : [linesView(session, step.id, start, now, !!reserve)]));
    if (step.id === "D2" && ctx.extras?.D2) box.append(ctx.extras.D2);
  }
  const links = LINKS[step.id];
  if (links) {
    box.append(h("div", { class: "dp-links" }, h("span", { class: "muted small", text: "细节在：" }),
      ...links.map(([target, text]) => h("a", { href: `#${target}`, class: "dp-jump", title: `跳到“${text}”查看完整内容`,
        onclick: (e) => { e.preventDefault(); ctx.onJump(target, session.side); } }, text))));
  }
  if (told) box.append(...told.act);
  else if (markable(step)) box.append(marksView(session, step.id, ctx));
  return box;
}

function header(session, ctx) {
  const c = session.contract || {};
  const name = session.side === "put" ? "Put" : "Call";
  const token = String(c.short_instrument || "").split("-")[1] || "";
  const left = isNum(c.expiry_ms) ? c.expiry_ms - Date.now() : null;
  const box = h("div", { class: "dp-head" },
    h("div", { class: "dp-title" },
      h("b", { class: session.side, text: `${token} ${name}  卖 ${usd(c.short_strike_usd)} / 买 ${usd(c.long_strike_usd)}`.trim() }),
      h("span", { class: "muted small", title: `读序版本 ${session.path_version}`,
        text: `开始于 ${clock(session.started_at_ms)}（${ago(session.started_at_ms)}）`
        + (isNum(left) ? `，${left > 0 ? `距到期 ${(left / 3.6e6).toFixed(1)} 小时` : "已到期"}` : "") })));
  const actions = h("div", { class: "dp-actions" });
  if (session.status === "open") {
    actions.append(h("button", { class: "btn secondary", type: "button", disabled: !ctx.canAct || ctx.busy,
      title: "按同一合同重新取一次报价与读数，与开始时并列；不是换对象",
      onclick: ctx.onRefresh }, ctx.busy === "refresh" ? "刷新中…" : "刷新到现在"));
  }
  actions.append(h("button", { class: "btn secondary", type: "button", onclick: ctx.onClose }, "收起"));
  box.append(actions);
  if (ctx.replaced) box.append(h("p", { class: "dp-note", text: `保单卡这一侧已换为 ${ctx.replaced}；本次核验仍只针对上面这张保单。` }));
  const refreshed = session.readings?.now?.at_ms;
  if (refreshed) {
    const changes = session.changes || [];
    box.append(h("p", { class: `dp-note${changes.length ? " warn" : ""}`,
      text: `现在 = ${clock(refreshed)} 的同合同读数；` + (changes.length
        ? `变化：${changes.map((c) => `${c.step === "spot" ? "现价" : `${c.step} ${titleOf(session, c.step)}`} ${c.from || "—"} → ${c.to || "—"}`).join("；")}`
        : "各步判断与开始时相同") }));
  }
  return box;
}

// ── the lock conclusion's own limit price (dp-2026-10-27.1): a record kept with the conclusion, not an order ──
const LIMIT_MAX_BTC = 0.1;
const LIMIT_MAX_MINUTES = 1440;
const LIMIT_MINUTES = 60;
const hasGateFlag = (session) => stepsOf(session).some((s) => s.conclusion_gate);
/** Rounded up to 4 decimals, so a prefilled price is never below the reservation price. */
export const ceil4 = (v) => (isNum(v) && v > 0 ? Math.ceil(v * 1e4 - 1e-7) / 1e4 : null);

/** {ok, limit} from the two inputs; an empty price is a lock without a limit. */
export function parseLimit(priceText, minutesText) {
  const p = String(priceText ?? "").trim();
  if (!p) return { ok: true, limit: null };
  const price = Number(p);
  if (!Number.isFinite(price) || price < 0.00001 || price > LIMIT_MAX_BTC) return { ok: false, error: "挂单价需在 0.00001 到 0.1 BTC 之间" };
  const m = String(minutesText ?? "").trim();
  const minutes = Number(m);
  if (!m || !Number.isInteger(minutes) || minutes < 1 || minutes > LIMIT_MAX_MINUTES) {
    return { ok: false, error: "有效时长需为 1–1440 分钟的整数" };
  }
  return { ok: true, limit: { credit_btc: price, valid_minutes: minutes } };
}

function limitConfirm(session, ctx, box) {
  const st = uiOf(session, "#limit");
  const gate = gateStepId(session);
  const reserve = latestReading(session)?.steps?.[gate]?.reservation_btc;
  if (st.price == null) { const r = ceil4(reserve); st.price = r != null ? r.toFixed(4) : ""; }
  if (st.minutes == null) st.minutes = String(LIMIT_MINUTES);
  const warn = h("p", { class: "dp-limit-warn small" });
  const err = h("p", { class: "dp-error", role: "alert" });
  let go = null;
  const update = () => {
    const parsed = parseLimit(st.price, st.minutes);
    const below = parsed.ok && !!parsed.limit && isNum(reserve) && parsed.limit.credit_btc < reserve;
    warn.textContent = below ? "低于保留价：即使成交，净收也低于市场估价" : "";
    warn.hidden = !below;
    const text = parsed.ok ? (ctx.error || "") : parsed.error;
    err.textContent = text;
    err.hidden = !text;
    if (go) go.disabled = !parsed.ok || !!ctx.busy;
    return parsed;
  };
  const input = (key, label, value, attrs) => {
    const el = h("input", { type: "text", class: "dp-limit-input", "aria-label": label, dataset: { dpLimit: key, fk: `limit:${key}` },
      readOnly: !!ctx.busy, oninput: (e) => { st[key] = e.target.value; ctx.clearError?.(); update(); }, ...attrs });
    el.value = value;
    return el;
  };
  const price = input("price", "挂单价（两腿合计信用，BTC）", st.price, { inputmode: "decimal", placeholder: "留空 = 不记挂单价" });
  const minutes = input("minutes", "有效（分钟）", st.minutes, { inputmode: "numeric" });
  go = h("button", { class: "btn", type: "button", onclick: () => {
    price.blur?.(); minutes.blur?.();
    const parsed = update();
    if (!parsed.ok) return;
    ctx.onConclude("lock", parsed.limit ? { limit: parsed.limit } : {});
  } }, ctx.busy === "lock" ? "锁定中…" : "确认锁定");
  box.append(h("span", { class: "confirm", text: "以现在的报价为锁定起点，每分钟采样同一合同直到到期；锁定记录附带本次核验和你的挂单价。页面不下单。" }),
    h("div", { class: "dp-limit" },
      h("label", { class: "dp-limit-field" }, h("span", { text: "挂单价（两腿合计信用，BTC）" }), price),
      h("label", { class: "dp-limit-field" }, h("span", { text: "有效（分钟）" }), minutes),
      h("span", { class: "muted small", text: `${isNum(reserve) ? `保留价 ${btc4(reserve)}（已向上取整填入）` : "保留价未知"}；清空挂单价则只锁定、不记挂单价。` })),
    warn, err, go,
    h("button", { class: "btn secondary", type: "button", onclick: () => { Object.assign(st, { price: null, minutes: null }); ctx.onCancel(); } }, "取消"));
  update();
  return box;
}

function conclusionView(session, ctx) {
  const box = h("div", { class: "dp-conclude" });
  if (session.status !== "open") {
    const c = session.conclusion || {};
    box.append(h("b", { text: `结论：${CHOICE_CN[c.choice] || "—"}` }),
      h("span", { class: "muted small", text: c.at_ms ? `${clock(c.at_ms)}` : "" }));
    if (c.limit && isNum(c.limit.credit_btc)) {
      const expired = isNum(c.limit.valid_until_ms) && c.limit.valid_until_ms <= Date.now();
      box.append(h("span", { class: "dp-limit-line" }, "挂单价 ", h("span", { class: "num", text: btc4(c.limit.credit_btc) }),
        `，有效至 ${until(c.limit.valid_until_ms, c.limit.set_at_ms)}${expired ? "（已过期）" : ""}`,
        `（保留价 ${isNum(c.limit.reservation_btc) ? btc4(c.limit.reservation_btc) : "未知"}）`));
    }
    if (c.episode_id) box.append(h("a", { href: "#lock-h", class: "dp-jump", onclick: (e) => { e.preventDefault(); ctx.onOpenLock(c.episode_id); } }, "在锁定追踪查看"));
    if (session.outcome?.status === "settled") {
      const r = session.outcome.result_usd;
      box.append(h("span", { class: `chip ${isNum(r) ? (r > 10 ? "ok" : r < -10 ? "bad" : "") : ""}`,
        text: `到期结果（按核验开始时报价）：${isNum(r) ? `${r > 0 ? "+" : r < 0 ? "−" : ""}${usd(Math.abs(r))} 美元` : "—"}` }));
    }
    return box;
  }
  if (ctx.confirm === "lock" && hasGateFlag(session)) return limitConfirm(session, ctx, box);
  if (ctx.confirm === "lock") {
    box.append(h("span", { class: "confirm", text: "以现在的报价为锁定起点，每分钟采样同一合同直到到期；锁定记录附带本次核验。页面不下单。" }),
      h("button", { class: "btn", type: "button", disabled: ctx.busy, onclick: () => ctx.onConclude("lock") }, ctx.busy === "lock" ? "锁定中…" : "确认锁定"),
      h("button", { class: "btn secondary", type: "button", onclick: ctx.onCancel }, "取消"));
    return box;
  }
  box.append(h("b", { text: "结论" }));
  for (const [choice, text] of [["lock", "锁定追踪"], ["decline", "不承保"], ["watch", "继续观察"]]) {
    box.append(h("button", { class: `btn${choice === "lock" ? "" : " secondary"}`, type: "button", disabled: !ctx.canAct || !!ctx.busy,
      onclick: () => (choice === "lock" ? ctx.onAskLock() : ctx.onConclude(choice)) }, text));
  }
  const watched = (session.decisions || []).filter((d) => d.choice === "watch").length;
  box.append(h("span", { class: "muted small", text: watched ? `已继续观察 ${watched} 次；到期仍未结论的记为“到期未结论”。` : "继续观察会保留本次核验，稍后从保单卡“继续核验”回到这里。" }));
  return box;
}

/** Index of sessions when none is open on screen. */
function indexView(overview, ctx) {
  const box = h("div", { class: "dp-index" });
  box.append(h("p", { class: "muted", text: "在上方保单卡点“开始核验”。卡片负责比较和选择；这里按固定读序核验一张具体保单，结论可以是锁定追踪、不承保或继续观察。" }));
  const recent = overview?.recent || [];  // unexpired contracts only, newest first
  if (recent.length) {
    const list = h("ul", { class: "dp-recent" });
    for (const r of recent) {
      list.append(h("li", {},
        h("a", { href: "#decision-h", class: "dp-jump", onclick: (e) => { e.preventDefault(); ctx.onOpen(r.verification_id); } },
          `${expiryCode(r.expiry_ms)} ${r.side === "put" ? "Put" : "Call"}${isNum(r.short_strike_usd) ? ` 卖 ${usd(r.short_strike_usd)}` : ""} · ${clock(r.started_at_ms)}`.trim()),
        h("span", { class: `chip ${r.status === "open" ? "warn" : ""}`, text: r.status === "open" ? "进行中" : CHOICE_CN[r.conclusion] || "已结束" })));
    }
    box.append(h("div", { class: "muted small", text: "本期核验（最新在前；合约到期后回填结果并自动清理）：" }), list);
  }
  return box;
}

/** Which control had the keyboard, so a redraw can hand it back. */
const focusKey = () => {
  const el = typeof document !== "undefined" ? document.activeElement : null;
  return el?.dataset?.fk || null;
};
function restoreFocus(box, key) {
  const want = box.querySelector?.('[data-focus="1"]') || (key && box.querySelector?.(`[data-fk="${key}"]`));
  if (!want) return;
  want.removeAttribute?.("data-focus");
  want.focus?.({ preventScroll: true });
  if (want.tagName === "TEXTAREA" || want.tagName === "INPUT") want.setSelectionRange?.(want.value.length, want.value.length);
}

export function renderDecision(box, ctx, { force = false } = {}) {
  last = { box, ctx };
  watchTyping(box);
  // never rebuild under a half-typed note; a session that just closed always redraws
  if (!force && typing() && ctx?.session?.status === "open") { deferred = true; return; }
  deferred = false;
  const keep = focusKey();
  clear(box);
  const session = ctx.session;
  if (session) ctx = { ...ctx, mode: hasNarrative(session) ? currentMode() : "table" };
  // with the limit inputs open, the error is shown next to them (a refused limit is about those inputs)
  const nearLimit = session?.status === "open" && ctx.confirm === "lock" && hasGateFlag(session);
  if (ctx.error && !nearLimit) box.append(h("p", { class: "dp-error", role: "alert", text: ctx.error }));
  if (!session) {
    if (ctx.overview?.error_type) box.append(h("p", { class: "dp-error", role: "alert", text: "核验列表暂时读取失败，稍后自动重试。" }));
    if (ctx.overview?.unreadable_sessions) box.append(h("p", { class: "dp-error", text: `有 ${ctx.overview.unreadable_sessions} 条核验记录文件损坏，已跳过。` }));
    box.append(indexView(ctx.overview, ctx));
    return;
  }
  box.append(header(session, ctx));
  if (hasNarrative(session)) box.append(viewToggle());
  const steps = stepsOf(session);
  const shown = Math.max(0, revealedIndex(session));
  const list = h("ol", { class: "dp-steps" });
  steps.forEach((step, i) => { if (i <= shown) list.append(stepView(session, step, i, ctx)); });
  box.append(list);
  const next = steps[shown + 1];
  if (next && session.status === "open") {
    box.append(h("div", { class: "dp-next" },
      h("button", { class: "btn secondary", type: "button", disabled: !ctx.canAct || !!ctx.busy,
        onclick: () => ctx.onStep(next.id, null) }, `下一步：${next.id} ${next.title}${next.on_demand ? "（按需）" : ""}`),
      // old paths kept every premium number for Q2; from dp-2026-10-27.1 V may already show one (mid vs reservation)
      next.premium && !next.conclusion_gate ? h("span", { class: "muted small", text: "从这一步起才出现权利金金额。" }) : null));
  }
  // the conclusion opens with the gate step (P from dp-2026-10-27.1, Q2 before; none found: always)
  const gate = steps.findIndex((s) => s.id === gateStepId(session));
  if (session.status !== "open" || shown >= gate) box.append(conclusionView(session, ctx));
  restoreFocus(box, keep);
}
