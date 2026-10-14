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
  D2: [["map-h", "价格地图"], ["factor-h", "空间位置"]],
  D3: [["factor-h", "量价路径与主动成交"]],
  D4: [["event-h", "外源性事件"], ["ctx-h", "资金与慢背景"]],
  D5: [["src-h", "锚点效用变迁"]],
  AU: [["audit-h", "推理审计"]],
};
const TONE = { bad: "bad", warn: "warn", ok: "ok" };
const CHIP_PREFIX = { D2: "缓冲" };

const stepsOf = (session) => session?.steps || [];
const MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
/** Deribit expiry code (15OCT26) from an expiry time, so a session reads like its instruments. */
const expiryCode = (ms) => {
  if (!isNum(ms)) return "";
  const d = new Date(ms);
  return `${d.getUTCDate()}${MON[d.getUTCMonth()]}${String(d.getUTCFullYear()).slice(2)}`;
};
const titleOf = (session, id) => stepsOf(session).find((s) => s.id === id)?.title || id;

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

/** One step's lines; with a "now" reading, start and now side by side, matched by label. */
function linesView(session, id, start, now) {
  const rows = new Map();
  const keyed = (lines) => {
    const seen = {};
    return (lines || []).map((l) => [`${l.k}#${(seen[l.k] = (seen[l.k] ?? -1) + 1)}`, l]);
  };
  for (const [key, l] of keyed(start?.lines)) rows.set(key, { k: l.k, a: l, b: null });
  for (const [key, l] of keyed(now?.lines)) {
    const r = rows.get(key);
    if (r) r.b = l; else rows.set(key, { k: l.k, a: null, b: l });
  }
  const label = (k) => (id === "Q1" ? `${k} ${titleOf(session, k)}` : k);
  const table = h("div", { class: `dp-lines${now ? " two" : ""}` });
  if (now) table.append(h("span", { class: "dp-col", text: "" }), h("span", { class: "dp-col", text: "开始时" }), h("span", { class: "dp-col", text: "现在" }));
  for (const r of rows.values()) {
    table.append(h("span", { class: "dp-k", text: label(r.k) }));
    table.append(h("span", { class: `dp-v ${TONE[r.a?.tone] || ""}`, text: r.a ? r.a.v : "—" }));
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

function stepView(session, step, i, ctx) {
  const start = session.readings?.start?.steps?.[step.id];
  const now = session.readings?.now?.steps?.[step.id];
  const box = h("li", { class: "dp-step", id: `dp-${step.id}` });
  const head = h("div", { class: "dp-step-head" },
    h("span", { class: "dp-id", text: step.id }),
    h("b", { text: step.title }),
    h("span", { class: "dp-q", text: step.question }),
    chip(start?.verdict, (now ? "开始时 " : "") + (CHIP_PREFIX[step.id] || "")),
    now && now.verdict?.code !== start?.verdict?.code ? chip(now.verdict, "现在 " + (CHIP_PREFIX[step.id] || "")) : null);
  box.append(head);
  if (step.id === "AU") {
    box.append(h("p", { class: "muted small", text: start?.note || "按需触发" }));
    if (ctx.extras?.AU) box.append(ctx.extras.AU);
  } else {
    box.append(linesView(session, step.id, start, now));
    if (step.id === "D2" && ctx.extras?.D2) box.append(ctx.extras.D2);
  }
  const links = LINKS[step.id];
  if (links) {
    box.append(h("div", { class: "dp-links" }, h("span", { class: "muted small", text: "细节在：" }),
      ...links.map(([target, text]) => h("a", { href: `#${target}`, class: "dp-jump", title: `跳到“${text}”查看完整内容`,
        onclick: (e) => { e.preventDefault(); ctx.onJump(target, session.side); } }, text))));
  }
  if (step.id !== "AU") box.append(marksView(session, step.id, ctx));
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

function conclusionView(session, ctx) {
  const box = h("div", { class: "dp-conclude" });
  if (session.status !== "open") {
    const c = session.conclusion || {};
    box.append(h("b", { text: `结论：${CHOICE_CN[c.choice] || "—"}` }),
      h("span", { class: "muted small", text: c.at_ms ? `${clock(c.at_ms)}` : "" }));
    if (c.episode_id) box.append(h("a", { href: "#lock-h", class: "dp-jump", onclick: (e) => { e.preventDefault(); ctx.onOpenLock(c.episode_id); } }, "在锁定追踪查看"));
    if (session.outcome?.status === "settled") {
      const r = session.outcome.result_usd;
      box.append(h("span", { class: `chip ${isNum(r) ? (r > 10 ? "ok" : r < -10 ? "bad" : "") : ""}`,
        text: `到期结果（按核验开始时报价）：${isNum(r) ? `${r > 0 ? "+" : r < 0 ? "−" : ""}${usd(Math.abs(r))} 美元` : "—"}` }));
    }
    return box;
  }
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

export function renderDecision(box, ctx) {
  clear(box);
  const session = ctx.session;
  if (ctx.error) box.append(h("p", { class: "dp-error", role: "alert", text: ctx.error }));
  if (!session) {
    if (ctx.overview?.error_type) box.append(h("p", { class: "dp-error", role: "alert", text: "核验列表暂时读取失败，稍后自动重试。" }));
    if (ctx.overview?.unreadable_sessions) box.append(h("p", { class: "dp-error", text: `有 ${ctx.overview.unreadable_sessions} 条核验记录文件损坏，已跳过。` }));
    box.append(indexView(ctx.overview, ctx));
    return;
  }
  box.append(header(session, ctx));
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
      next.premium ? h("span", { class: "muted small", text: "从这一步起才出现权利金金额。" }) : null));
  }
  const q2 = steps.findIndex((s) => s.id === "Q2");
  if (session.status !== "open" || shown >= q2) box.append(conclusionView(session, ctx));
}
