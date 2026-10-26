// Question sheets of the inquirer (docs/opus/14): a short block in the Decision Path audit step
// and the full sheet in the reasoning audit. The inquirer only asks: nothing here weighs,
// scores or decides, and every sheet says it is unvalidated reference.
import { h, isNum, clock, ago, dur } from "./format.js";
import { cnText, refWord, fieldWord, valueWord } from "./inquiry_terms.js";

/** Text attributes for the inquirer's prose: Chinese reading shown, the original in the title. */
const cn = (text, entries, prefix = "") => {
  const shown = cnText(text, entries);
  return { text: text ? prefix + shown : "—", title: text && shown !== text ? `原文：${text}` : null };
};

export const TYPE_CN = { conflict: "证据冲突", collinear: "是否同源", temporal: "持续还是噪声", gap: "数据缺口",
  mechanism: "机制", compensation: "补偿错配", scenario: "到期路径" };
export const BEARS_CN = { touch_prob: "触界", expiry_loss_prob: "到期亏损", tail_loss: "深尾", compensation: "补偿" };
export const SOURCE_CN = { case_file: "答案在案卷内", history: "需查历史存档", external: "需外部数据", unobtainable: "无法获得" };
export const PRIORITY_CN = { high: "高", medium: "中", low: "低" };
const PRIORITY_TONE = { high: "warn", medium: "", low: "" };
export const DIRECTION_CN = { raises: ["风险↑", "bad"], lowers: ["风险↓", "ok"], unclear: ["不明", ""] };
export const ANOMALY_CN = { conflict: "冲突", outlier: "离群", gap: "缺口", shift: "变化" };
export const LAYER_CN = { L0: "资料", L1: "合同与赔付", L2: "报价、基线与补偿", L3: "结构", L4: "波动", L5: "运动与流向",
  L6: "时间", L7: "外部", L8: "程序读数" };
export const LAYER_STATUS_CN = { material: ["有实质信息", "warn"], quiet: ["无实质信息", ""], unknown: ["缺失或不可用", "bad"] };
export const STATUS_CN = { OK: ["通过校验", "ok"], PARTIAL: ["部分通过校验（有删改）", "warn"], REJECTED: ["未通过校验", "bad"],
  ERROR: ["运行失败", "bad"], unreadable: ["无法读取", "bad"] };
export const TRIGGER_CN = { scheduled: "定时", new_contract: "新合约即时" };
export const UNVALIDATED_CN = "未验证，只作参考，不是承保理由";
export const SCHEDULE_CN = "定时：北京时间 10:00 / 22:00；新合约首次出现 2 小时内在下一个整点补问一次";

const label = (map, code) => (map[code] ?? code ?? "—");
const statusOf = (code) => STATUS_CN[code] || [code ? `状态 ${code}` : "状态未知", "warn"];

/** Archive key of a contract, as the server lists it. */
export const contractKey = (c) => (c?.short_instrument && c?.long_instrument ? `${c.short_instrument}|${c.long_instrument}` : null);

/** The per-text marks the validator attached, in words. */
export function flagsOf(item) {
  const out = [];
  if (item?.forbidden_wording) out.push(`措辞越界：“${item.forbidden_wording}”`);
  if (Array.isArray(item?.untraced_numbers) && item.untraced_numbers.length) out.push(`含案卷外数字：${item.untraced_numbers.join("、")}`);
  return out;
}

/** One line of identity: slot and trigger, case time and age, validation status. */
export function identityText(meta, now = Date.now()) {
  if (!meta) return "";
  const trig = label(TRIGGER_CN, meta.trigger) + (meta.trigger_inferred ? "（按时刻推断）" : "");
  const parts = [`${clock(meta.slot_ms)} ${trig}`];
  if (isNum(meta.asof_ms)) parts.push(`案卷 ${clock(meta.asof_ms)}，${ago(meta.asof_ms, now)}`);
  parts.push(statusOf(meta.status)[0]);
  return parts.join(" · ");
}

/** What a response says, independent of the DOM: which sheet, what failed after it. */
export function sheetState(resp) {
  const sheets = resp?.sheets || [];
  const sel = resp?.selected || null;
  const failedAfter = sel ? sheets.filter((s) => !s.has_sheet && s.slot_ms > sel.slot_ms).pop() || null
    : sheets.filter((s) => !s.has_sheet).pop() || null;
  const newestOk = sheets.filter((s) => s.has_sheet).pop() || null;
  if (!sel) return { kind: sheets.length ? "failed_only" : "none", sheets, selected: null, failedAfter, newer: null };
  // a sheet chosen earlier (or pinned from the audit step) while a newer good one exists
  const newer = newestOk && newestOk.file !== sel.file && newestOk.slot_ms > sel.slot_ms ? newestOk : null;
  return { kind: "ok", sheets, selected: sel, failedAfter, newer };
}

const chip = (text, cls = "", title = "") => h("span", { class: `chip ${cls}`.trim(), text, title: title || null });

function bearsChips(q) {
  const list = Array.isArray(q.bears_on) ? q.bears_on : q.bears_on ? [q.bears_on] : [];
  return list.map((b) => chip(`影响${label(BEARS_CN, b)}`, "", "这个问题的答案会改变哪一项风险判断"));
}

function questionChips(q) {
  return [chip(`${label(PRIORITY_CN, q.priority)}优先`, PRIORITY_TONE[q.priority] || ""), chip(label(TYPE_CN, q.type)),
    ...bearsChips(q), chip(label(SOURCE_CN, q.answer_source), "muted-chip")];
}

function flagLine(item) {
  const flags = flagsOf(item);
  return flags.length ? h("span", { class: "iq-flag", title: "程序校验的标记：不删除原文，只提示这里需要核对", text: flags.join("；") }) : null;
}

// ── the Decision Path audit step ──────────────────
/**
 * The audit step's block. `resp` is the summary response (or null while loading), `ctx` has
 * onOpen(file) to jump to the full sheet and `error` for a failed read.
 */
export function auSummary(resp, ctx = {}) {
  const box = h("div", { class: "iq-au" });
  box.append(h("div", { class: "iq-au-head" }, h("b", { text: "提问单" }), chip(UNVALIDATED_CN, "warn")));
  if (ctx.error) { box.append(h("p", { class: "muted small", text: `提问单暂时读取失败：${ctx.error}` })); return box; }
  if (!resp) { box.append(h("p", { class: "muted small", text: "正在读取提问单…" })); return box; }
  const st = sheetState(resp);
  if (st.kind === "none") { box.append(h("p", { class: "muted small", text: `本合约还没有提问单（${SCHEDULE_CN}）。` })); return box; }
  if (st.failedAfter) box.append(h("p", { class: "muted small warn-text", text: `最近一次提问失败（${clock(st.failedAfter.slot_ms)}，${statusOf(st.failedAfter.status)[0]}）${st.selected ? "，下面是之前一张成功的提问单" : ""}。` }));
  if (st.kind === "failed_only") return box;
  const sel = st.selected;
  box.append(h("p", { class: "muted small", text: identityText(sel) }));
  const sum = sel.summary;
  if (sum) {
    const list = h("ol", { class: "iq-au-list" });
    for (const q of sum.questions || []) {
      list.append(h("li", {}, h("p", cn(q.question_cn)), h("div", { class: "iq-chips" }, ...questionChips(q))));
    }
    box.append(list);
    const c = sum.by_priority || {};
    box.append(h("p", { class: "muted small", text: `共 ${sum.question_count} 问（高 ${c.high ?? 0} / 中 ${c.medium ?? 0} / 低 ${c.low ?? 0}）· 异常 ${sum.anomaly_count} 条` }));
  }
  box.append(h("div", { class: "dp-links" }, h("span", { class: "muted small", text: "完整提问单在：" }),
    h("a", { href: "#audit-h", class: "dp-jump", title: "跳到推理审计，打开这张合约的这张提问单",
      onclick: (e) => { e.preventDefault(); ctx.onOpen?.(sel.file); } }, "推理审计 · 提问单")));
  return box;
}

// ── experience reading (docs/opus/19) ─────────────
export const LENS_CATEGORY_CN = { execution: "执行相关", observe: "持有观察点", not_a_reason: "不构成承保理由",
  scope: "口径说明", declared_gap: "已声明缺口", unclassified: "未归类" };
const LENS_ORDER = ["execution", "observe", "not_a_reason", "scope", "declared_gap", "unclassified"];

/** One line under a question: how this kind of question is used, on what graded basis, with this
 *  contract's numbers where they share the basis's measurement. Null when there is no reading. */
export function lensLine(reading) {
  if (!reading) return null;
  const box = h("div", { class: "iq-lens" });
  box.append(h("div", { class: "iq-lens-head" }, h("span", { class: "iq-lens-tag", text: "经验读法" }),
    chip(reading.category_cn || LENS_CATEGORY_CN[reading.category] || reading.category, "lens", reading.phrase_cn || ""),
    h("b", { text: reading.title_cn || "" }),
    (reading.also_cn || []).length ? h("span", { class: "muted small", text: `同时涉及：${reading.also_cn.join("、")}` }) : null));
  box.append(h("p", { class: "iq-lens-read", text: reading.reading_cn || "" }));
  const live = (reading.live || []).filter((r) => r && r.label_cn);
  if (live.length) {
    box.append(h("dl", { class: "iq-lens-live" }, ...live.flatMap((r) => [
      h("dt", { text: r.label_cn }), h("dd", {}, h("span", { class: "num", text: r.value ?? "—" }),
        r.note_cn ? h("span", { class: "muted small", text: ` ${r.note_cn}` }) : null)])));
  }
  const basis = reading.basis || [];
  if (basis.length) {
    box.append(h("details", { class: "iq-lens-basis" }, h("summary", { text: "依据" }),
      h("ul", {}, ...basis.map((b) => h("li", {}, chip(b.grade || "—", "muted-chip", "证据等级（17 号经验体系）"), ` ${b.text_cn}`,
        h("span", { class: "muted small", text: `（${b.source}）` }))))));
  }
  return box;
}

/** The sheet's reading at the top: counts by category and the suggested order, or why there is none. */
export function lensHead(lens) {
  if (!lens) return null;
  if (lens.error) return h("p", { class: "muted small", text: "经验读法暂时无法生成。" });
  if (!lens.enabled) return h("p", { class: "muted small", text: `经验读法：${lens.reason_cn || "不显示"}` });
  const counts = lens.counts || {};
  const parts = LENS_ORDER.filter((c) => counts[c]).map((c) => `${LENS_CATEGORY_CN[c]} ${counts[c]}`);
  const order = (lens.order || []).map((c) => LENS_CATEGORY_CN[c]).join(" → ");
  return h("div", { class: "iq-lens-sum" },
    h("p", {}, h("b", { text: "本单读法：" }), parts.join(" · ") || "无", order ? `。建议阅读顺序：${order}。` : "。"),
    h("p", { class: "muted small", text: lens.note_cn || "" }));
}

/** Holding observation points gathered from the sheet's "持有观察点" questions. */
export function lensObserve(lens) {
  const items = lens?.enabled ? lens.observe || [] : [];
  if (!items.length) return null;
  const ul = h("ul", { class: "iq-observe" });
  for (const it of items) {
    const pts = (it.points || []).map((p) => `${p.label_cn}：${p.value}`);
    ul.append(h("li", {}, h("b", { text: `${it.question} ${it.title_cn}` }), pts.length ? `　${pts.join("；")}` : h("span", { class: "muted small", text: "　（这一类没有可给出的时刻或数字）" })));
  }
  return h("details", { class: "iq-brief", open: true },
    h("summary", { text: `持有观察点（${items.length}）：观察点，不是平仓点` }), ul,
    h("p", { class: "muted small", text: "日内平仓点约 20 个到期日后再议（17 号 §5）；持有与平仓的对照见研究存档 scorecards/intraday_exit.json。" }));
}

// ── the reasoning audit ───────────────────────────
function refChip(ref, entries, ctx) {
  const e = entries?.[ref];
  const full = refWord(ref, entries);
  const name = full.length > 14 ? full.slice(0, 13) + "…" : full;
  return h("button", { class: `chip${ctx.activeRef === ref ? " on" : ""}`, type: "button", title: `${e?.label_cn || ref}（${ref}）`,
    onclick: (ev) => { ev.stopPropagation(); ctx.onRef?.(ref); } }, name);
}

function hypotheses(q, E) {
  const table = h("div", { class: "iq-hyp" });
  for (const hy of q.hypotheses || []) {
    const [dir, tone] = DIRECTION_CN[hy.direction] || DIRECTION_CN.unclear;
    table.append(h("div", { class: "iq-h" },
      h("div", { class: "iq-h-head" }, chip(dir, tone, "这个假设成立时，这张保单的风险是升高还是降低"), h("span", cn(hy.h_cn, E)), flagLine(hy)),
      hy.mechanism_cn ? h("p", { class: "iq-mech", ...cn(hy.mechanism_cn, E, "机制：") }) : null,
      h("dl", { class: "iq-test" },
        h("dt", { text: "到期前支持" }), h("dd", cn(hy.would_support_cn, E)),
        h("dt", { text: "到期前推翻" }), h("dd", cn(hy.would_refute_cn, E)))));
  }
  return table;
}

function questionCard(q, sel, ctx) {
  const on = ctx.activeQuestion === q.id;
  const card = h("div", { class: `point iq-q${on ? " on" : ""}`, tabindex: 0, role: "button", "aria-pressed": String(on),
    onclick: () => ctx.onQuestion?.(q.id),
    onkeydown: (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === card) { e.preventDefault(); ctx.onQuestion?.(q.id); } } },
  h("div", { class: "iq-q-head" }, h("b", { text: q.id || "" }), h("div", { class: "iq-chips" }, ...questionChips(q),
    ...(q.from_anomalies || []).map((a) => chip(`来自 ${a}`, "muted-chip")))),
  h("p", { class: "iq-question", ...cn(q.question_cn, sel.entries) }), flagLine(q),
  q.why_material_cn ? h("p", { class: "iq-why", ...cn(q.why_material_cn, sel.entries, "为何重要：") }) : null,
  sel.lens?.enabled ? lensLine(sel.lens.questions?.[q.id]) : null,
  hypotheses(q, sel.entries));
  const premises = (q.premises || []).filter((p) => p && typeof p === "object");
  if (premises.length) {
    card.append(h("div", { class: "iq-sub", text: "前提（案卷事实）" }));
    const ul = h("ul", { class: "iq-prem" });
    for (const p of premises) {
      ul.append(h("li", {}, h("span", cn(p.claim_cn, sel.entries)), flagLine(p),
        h("span", { class: "refs" }, ...(p.refs || []).map((r) => refChip(r, sel.entries, ctx)))));
    }
    card.append(ul);
  }
  return card;
}

/** Refs the questions and anomalies cite (the fact brief's own refs are secondary). */
export function mainRefs(sheet) {
  return new Set([...(sheet?.questions || []).flatMap((q) => (q?.premises || []).flatMap((p) => p?.refs || [])),
    ...(sheet?.anomalies || []).flatMap((a) => a?.refs || [])]);
}

/** Refs a question cites (premises), for the ledger highlight. */
export function questionRefs(sheet, id) {
  const q = (sheet?.questions || []).find((x) => x.id === id);
  return new Set((q?.premises || []).flatMap((p) => p?.refs || []));
}

function factBrief(sheet, sel, ctx) {
  const brief = sheet.fact_brief || {};
  const box = h("details", { class: "iq-brief" }, h("summary", { text: "事实底稿（九层，提问者对案卷的复述）" }));
  for (const layer of brief.layers || []) {
    const [st, tone] = LAYER_STATUS_CN[layer.status] || LAYER_STATUS_CN.unknown;
    const sec = h("div", { class: "iq-layer" }, h("div", { class: "iq-layer-head" },
      h("b", { text: `${layer.layer} ${LAYER_CN[layer.layer] || ""}`.trim() }), chip(st, tone)));
    for (const f of layer.facts || []) {
      sec.append(h("p", {}, h("span", cn(f.text_cn, sel.entries)), flagLine(f),
        h("span", { class: "refs" }, ...(f.refs || []).map((r) => refChip(r, sel.entries, ctx)))));
    }
    for (const r of layer.relations_cn || []) sec.append(h("p", { class: "muted small", ...cn(r, sel.entries, "关系：") }));
    if (layer.quality_cn) sec.append(h("p", { class: "muted small", ...cn(layer.quality_cn, sel.entries, "质量：") }));
    box.append(sec);
  }
  const cross = brief.cross_layer || [];
  if (cross.length) {
    const sec = h("div", { class: "iq-layer" }, h("b", { text: "跨层关系" }));
    const KIND = { corroborate: "互相印证", conflict: "互相矛盾", collinear: "其实同源" };
    for (const c of cross) {
      sec.append(h("p", {}, chip(KIND[c.kind] || c.kind || "—"), " ", h("span", cn(c.text_cn, sel.entries)), flagLine(c),
        h("span", { class: "refs" }, ...(c.refs || []).map((r) => refChip(r, sel.entries, ctx)))));
    }
    box.append(sec);
  }
  return box;
}

function traceView(resp, sel) {
  const box = h("details", { class: "trace iq-trace" }, h("summary", { text: "追溯：文件、版本与校验记录" }));
  const usage = (sel.usage || []).filter(Boolean).map((u) => u.total_tokens).filter(isNum);
  const rows = [["合约", resp.contract_key], ["提问单文件", sel.file], ["案卷", sel.case?.file || sel.case_error || "—"],
    ["case_id", sel.case_id], ["案卷生成器", sel.case?.builder || "—"], ["提问 prompt", sel.prompt_version || "—"],
    ["请求模型", sel.requested_model || "—"], ["token 用量", usage.length ? usage.join(" + ") : "—"]];
  box.append(h("dl", { class: "kv" }, ...rows.flatMap(([k, v]) => [h("dt", { text: k }), h("dd", {}, h("code", { text: String(v ?? "—") }))])));
  const issues = sel.issues || [];
  if (issues.length) {
    box.append(h("div", { class: "iq-sub", text: `校验记录（${issues.length} 条）` }),
      h("ul", { class: "iq-issues" }, ...issues.map((i) => h("li", {}, h("code", { text: i.code || "?" }), ` ${i.where || ""} `,
        h("span", { class: "muted", text: JSON.stringify(Object.fromEntries(Object.entries(i).filter(([k]) => !["code", "where"].includes(k)))) })))));
  }
  return box;
}

/** A millisecond timestamp field in Beijing time. */
const atField = (k, x) => (!/_ms$/.test(k) || !isNum(x) ? null : x > 1e12 ? clock(x) : dur(x));   // a time, else a duration
const clip = (s) => (s.length > 90 ? s.slice(0, 88) + "…" : s);
/** A value in one line, field names and codes in words; order-book levels as a count. */
const short = (v) => {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) {
    if (v.length && v.every((x) => Array.isArray(x))) return `${v.length} 档`;
    return clip(v.map((x) => (x && typeof x === "object" ? JSON.stringify(x) : String(valueWord(x)))).join("、"));
  }
  if (typeof v === "object") {
    return clip(Object.entries(v).map(([k, x]) => `${fieldWord(k)} ${x && typeof x === "object" ? "…" : atField(k, x) ?? valueWord(x)}`).join("，"));
  }
  return String(valueWord(v));
};

function entryCard(e, active) {
  const cls = active ? (active.has(e.ref) ? " hl" : " cold") : "";
  const card = h("div", { class: `ev${cls}`, dataset: { ref: e.ref } },
    h("header", {}, h("b", { text: e.label_cn || refWord(e.ref) }), h("code", { text: e.ref })));
  const meta = [];
  if (e.quality && e.quality !== "ok") meta.push(`质量：${e.quality}${e.quality_reason ? `（${e.quality_reason}）` : ""}`);
  if (isNum(e.asof_ms)) meta.push(`时刻 ${clock(e.asof_ms)}`);
  if (meta.length) card.append(h("div", { class: `basis${e.quality && e.quality !== "ok" ? " warn-text" : ""}`, text: meta.join(" · ") }));
  const v = e.values;
  if (e.truncated) card.append(h("div", { class: "basis muted", text: "取值较大，只显示前一段，完整内容见原始取值。" }));
  else if (v && typeof v === "object" && !Array.isArray(v)) {
    const kv = h("dl", { class: "kv" });
    Object.entries(v).slice(0, 12).forEach(([k, x]) => kv.append(h("dt", { title: k, text: fieldWord(k) }), h("dd", { text: atField(k, x) ?? short(x) })));
    card.append(kv);
    if (Object.keys(v).length > 12) card.append(h("div", { class: "basis muted", text: `另有 ${Object.keys(v).length - 12} 项，见原始取值。` }));
  } else if (Array.isArray(v)) card.append(h("div", { class: "basis", text: `${v.length} 条记录，见原始取值。` }));
  else card.append(h("div", { class: "basis", text: short(v) }));
  card.append(h("details", { class: "raw" }, h("summary", { text: "原始取值与关系" }),
    h("pre", { text: e.truncated ? e.values_preview : JSON.stringify({ values: e.values, relations: e.relations, source: e.source }, null, 1) })));
  return card;
}

/**
 * The full sheet for the reasoning audit: `{left, right}` columns. `ctx` carries the active
 * question/ref, the pinned-contract note, the sheet picker and the handlers.
 */
export function inquiryAudit(resp, ctx = {}) {
  const left = h("div", { class: "points iq" });
  const right = h("div", { class: "ledger" });
  const st = sheetState(resp);
  left.append(h("p", { class: "banner info", text: `${resp?.meaning_cn || "提问者只提问题、不下结论"}。` }));
  if (ctx.pinNote) left.append(ctx.pinNote);
  if (st.sheets.length > 1) {
    left.append(h("div", { class: "iq-picker" }, h("span", { class: "muted small", text: "同一合约的提问单：" }),
      ...st.sheets.map((s) => h("button", { class: `chip${st.selected?.file === s.file ? " on" : ""}${s.has_sheet ? "" : " bad"}`, type: "button",
        title: identityText(s), onclick: () => ctx.onPick?.(s.file) }, `${clock(s.slot_ms)} ${label(TRIGGER_CN, s.trigger)}${s.has_sheet ? "" : "（失败）"}`))));
  }
  if (st.kind === "none") { left.append(h("p", { class: "empty pad", text: `本合约还没有提问单（${SCHEDULE_CN}）。` })); return { left, right }; }
  if (!st.selected) { left.append(h("p", { class: "empty pad", text: "这张合约的提问都没有成功产出，可在上方选择查看失败记录。" })); return { left, right }; }
  const sel = st.selected;
  const sheet = sel.sheet;
  left.append(h("div", { class: "deck-head" }, h("h3", { text: "提问单" }), chip(statusOf(sel.status)[0], statusOf(sel.status)[1]),
    chip(UNVALIDATED_CN, "warn")));
  left.append(h("p", { class: "muted small", text: identityText(sel) }));
  if (st.failedAfter) left.append(h("p", { class: "muted small warn-text", text: `之后一次提问失败（${clock(st.failedAfter.slot_ms)}）。` }));
  if (st.newer) left.append(h("p", { class: "muted small warn-text" }, `这不是最新一张：${clock(st.newer.slot_ms)} 有更新的提问单。`,
    h("button", { class: "chip", type: "button", onclick: () => ctx.onPick?.(st.newer.file) }, "看最新一张")));
  if (!sheet) {
    left.append(h("p", { class: "muted", text: `这次提问没有产出可用的提问单（${statusOf(sel.status)[0]}${sel.error ? `：${sel.error}` : ""}）。` }));
    left.append(traceView(resp, sel));
    return { left, right };
  }
  // the questions are the product; the anomalies they come from follow, folded
  const questions = sheet.questions || [];
  left.append(h("h4", { text: `问题（${questions.length}，按提问者的优先级）` }));
  const head = lensHead(sel.lens);
  if (head) left.append(head);
  for (const q of questions) left.append(questionCard(q, sel, ctx));
  const observe = lensObserve(sel.lens);
  if (observe) left.append(observe);
  if (sheet.coverage_note_cn) left.append(h("p", { class: "muted small", ...cn(sheet.coverage_note_cn, sel.entries, "覆盖说明：") }));
  const anomalies = sheet.anomalies || [];
  if (anomalies.length) {
    const box = h("details", { class: "iq-brief", open: !!ctx.anomaliesOpen },
      h("summary", { text: `异常（${anomalies.length} 条，问题卡上的“来自 A…”指这里）` }));
    if (ctx.onToggle) box.addEventListener("toggle", () => ctx.onToggle("anomalies", box.open));
    const ul = h("ul", { class: "iq-anom" });
    for (const a of anomalies) {
      ul.append(h("li", {}, chip(`${a.id || ""} ${label(ANOMALY_CN, a.kind)}`.trim()), " ", h("span", cn(a.text_cn, sel.entries)), flagLine(a),
        h("span", { class: "refs" }, ...(a.refs || []).map((r) => refChip(r, sel.entries, ctx)))));
    }
    box.append(ul);
    left.append(box);
  }
  left.append(factBrief(sheet, sel, ctx));
  left.append(traceView(resp, sel));

  const active = ctx.activeQuestion ? questionRefs(sheet, ctx.activeQuestion) : ctx.activeRef ? new Set([ctx.activeRef]) : null;
  const entries = Object.values(sel.entries || {});
  const ordered = active ? entries.slice().sort((a, b) => Number(active.has(b.ref)) - Number(active.has(a.ref))) : entries;
  // entries the questions and anomalies cite come first; those only the fact brief cites are folded
  const main = mainRefs(sheet);
  const front = ordered.filter((e) => main.has(e.ref) || active?.has(e.ref));
  const rest = ordered.filter((e) => !front.includes(e));
  right.append(h("h4", { text: `问题与异常引用的案卷条目（${front.length} 项）` }));
  if (sel.case_error) right.append(h("p", { class: "muted", text: "案卷暂时读取失败，前提无法对照原值。" }));
  else if (!entries.length) right.append(h("p", { class: "muted", text: "没有可对照的案卷条目。" }));
  else right.append(h("p", { class: "muted small", text: "点击问题或引用标签，相关条目排到前面并高亮；前提里的数字可直接对照这里的原值。" }));
  for (const e of front) right.append(entryCard(e, active));
  if (rest.length) {
    const more = h("details", { class: "uncited", open: !!ctx.restOpen }, h("summary", { text: `只在事实底稿中引用（${rest.length} 项）` }));
    if (ctx.onToggle) more.addEventListener("toggle", () => ctx.onToggle("rest", more.open));
    for (const e of rest) more.append(entryCard(e, active));
    right.append(more);
  }
  return { left, right };
}

/** The summary shape of a full response (the server's summary=1), for the fixture page. */
export function toSummary(resp) {
  const sel = resp?.selected;
  if (!sel) return resp;
  const qs = (sel.sheet?.questions || []).filter((q) => q && typeof q === "object");
  const by = { high: 0, medium: 0, low: 0 };
  for (const q of qs) if (q.priority in by) by[q.priority] += 1;
  const { sheet, entries, issues, usage, case: _case, lens: _lens, ...rest } = sel;   // the summary has no reading (docs/opus/19 §5)
  return { ...resp, selected: { ...rest, summary: sel.sheet ? {
    questions: qs.slice(0, 3).map(({ id, priority, type, bears_on, answer_source, question_cn }) => ({ id, priority, type, bears_on, answer_source, question_cn })),
    question_count: qs.length, by_priority: by, anomaly_count: (sel.sheet.anomalies || []).length } : null } };
}
