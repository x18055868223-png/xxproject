// 中性回路承保决策工作台 — reading view renderer and the user actions
// (observation review, lock, stop, re-reference). Everything else is display only.
import { api, idempotencyKey } from "./api.js";
import { h, s, clear, get, isNum, usd, btc, pct, ratioPct, compactUsd, ago, dur, clock } from "./format.js";
import { sideModel, mapModel, sourceHealth, reasonCn, hasReason, statusCn, applicability, codeCn, underwriting, productionReady,
  consumerCn, sourceStatusCn } from "./model.js";
import { renderMap, legend } from "./map.js";
import { coreFactors, backgroundItems } from "./factors.js";
import { lockList, lockDetail, LOCK_LIST_LIMIT } from "./locks.js";
import { eventBand } from "./events.js";
import { sessionStrip, warningBlock } from "./session.js";
import { renderDecision as renderDecisionView } from "./decision.js";
import { auSummary, inquiryAudit, contractKey, toSummary } from "./inquiry.js";
import { humanize, evidenceName, fieldName, termName, SOURCE_CN, COMPONENT_CN } from "./glossary.js";

const FIXTURE = new URLSearchParams(location.search).has("fixture");
const POLL_MS = 30_000;
/**
 * The public HTTPS entry reads data only with a workbench session, so the page
 * asks for the token before it reads anything (no browser password dialog).
 * The private listener and the local preview read without one (`?gate=1`
 * previews the public behaviour there).
 */
const SESSION_GATED = !FIXTURE && (location.protocol === "https:" || new URLSearchParams(location.search).has("gate"));
const needsLogin = () => SESSION_GATED && !state.session.authenticated;
const state = {
  view: null, sides: {}, focus: "put", auditRef: null, auditPoint: null,
  session: { authenticated: false },
  lock: { id: null, details: {}, series: {}, seriesErrors: {}, loading: null, errors: {}, confirm: null, seq: 0, autoOpened: false },
  audit: null, auditAt: 0,
  // Decision Path: the session on screen (a frozen policy being verified)
  dp: { session: null, busy: null, error: null, confirm: null },
  // question sheets (docs/opus/14): responses by contract, the audit tab, a contract pinned from the audit step
  iq: { cache: {}, last: {}, mode: "review", pin: null, file: {}, q: null, ref: null, reveal: new Set() },
  pending: null, error: null, loadedAt: null, busy: false,
  notices: [], open: new Set(), signature: null, reviewPoll: null,
};
const $ = (id) => document.getElementById(id);

/** A failed request in words: a known code in Chinese, else the server's own Chinese message,
 *  else the HTTP status; never a bare code or a browser's English network message. */
function errorText(err) {
  const code = err?.body?.code;
  if (code && hasReason(code)) return reasonCn(code);
  const message = err?.body?.message;
  if (message && /[\u4e00-\u9fa5]/.test(message)) return message;
  if (code) return reasonCn(code);
  if (err?.status) return `服务器返回 HTTP ${err.status}`;
  return /fetch|network/i.test(err?.message || "") ? "网络连接失败" : (err?.message || "未知错误");
}
const busy = (err) => err?.status === 503;

// ── data ─────────────────────────────────────────
function signatureOf(view) {
  const ms = view.manual_review_state || {};
  const eps = get(view, "locked_candidates.episodes") || [];
  return JSON.stringify([view.updated_at_ms, ms.last_completed_ts_ms, !!ms.inflight, get(ms, "daily_http_budget.http_calls_used"),
    eps.map((e) => [e.episode_id, e.status, e.point_count]), !!view.collector_failure]);
}

async function load(force = false) {
  if (needsLogin()) { renderBanners(); return; }
  while (force && state.busy) await new Promise((r) => setTimeout(r, 200));
  if (state.busy) return;
  state.busy = true;
  let changed = true;
  try {
    const view = FIXTURE ? await fetchFixture() : await api.view();
    const sig = signatureOf(view);
    changed = force || sig !== state.signature || !!state.error;
    state.signature = sig;
    state.view = view;
    state.sides = Object.fromEntries((view.candidates || []).map((c) => [c.side, sideModel(view, c)]));
    if (!state.sides[state.focus]?.snap) state.focus = state.sides.put?.snap ? "put" : state.sides.call?.snap ? "call" : state.focus;
    state.error = null;
    state.loadedAt = Date.now();
  } catch (err) {
    state.error = err;
    if (SESSION_GATED && err.status === 401) { state.error = null; state.session = { authenticated: false }; renderSession(); }
    // Busy or memory-tight: retry when the server says, instead of waiting a whole poll.
    if (busy(err) && !state.retryTimer) {
      state.retryTimer = setTimeout(() => { state.retryTimer = null; load(); }, Math.min(60, Math.max(3, err.retryAfter || 5)) * 1000);
    }
  } finally {
    state.busy = false;
  }
  if (needsLogin()) { renderBanners(); openLogin("会话已过期，请重新输入工作台令牌。"); return; }
  // an unchanged view leaves the Decision Path alone, so a poll never re-draws under the cursor
  if (changed || state.error) render(); else { renderStrip(); renderBanners(); renderDecks(); }
  // The newest active lock opens by itself once; an open, active lock stays current with the page.
  const L = state.lock;
  if (!L.autoOpened && state.view && !FIXTURE) {
    L.autoOpened = true;
    const active = (get(state.view, "locked_candidates.episodes") || []).filter((e) => e.status === "active")
      .sort((a, b) => (b.created_at_ms || 0) - (a.created_at_ms || 0))[0];
    if (active && !L.id) { L.id = active.episode_id; renderLocks(); fetchLock(active.episode_id); }
  }
  const open = openEpisode();
  if (open && open.status === "active" && !FIXTURE && L.loading !== open.episode_id) fetchLock(open.episode_id, { quiet: true });
  if (!FIXTURE && Date.now() - state.auditAt > 5 * 60_000) loadAudit();
  watchReview();
}

async function fetchFixture() {
  const r = await fetch("fixtures/reading.json");
  if (!r.ok) throw new Error("示例数据只在本地预览提供，线上不发布");
  return r.json();
}

async function loadSession() {
  if (FIXTURE) return;
  // Only a 401 means logged out; a busy or unreachable server keeps the session it had.
  try { state.session = await api.session(); } catch (err) {
    if (err?.status === 401 || !state.session) state.session = { authenticated: false };
  }
  if (isNum(state.session.expires_at_ms) && state.session.expires_at_ms <= Date.now()) state.session = { authenticated: false };
  renderSession();
  renderSides();  // the first view may have rendered before the session was known
  if (needsLogin()) renderBanners();
}

/** While a review runs anywhere, follow it through the small review-state endpoint. */
function watchReview() {
  const inflight = !!get(state.view, "manual_review_state.inflight");
  if (!inflight || FIXTURE) { if (state.reviewPoll) { clearInterval(state.reviewPoll); state.reviewPoll = null; } return; }
  if (state.reviewPoll) return;
  state.reviewPoll = setInterval(async () => {
    try {
      const rs = await api.reviewState();
      if (!rs.manual_review_state?.inflight) { clearInterval(state.reviewPoll); state.reviewPoll = null; await load(true); }
    } catch { /* the regular poll still runs */ }
  }, 5_000);
}

// ── render root ──────────────────────────────────
function render() {
  // one section's failure must not blank the rest of the page
  for (const section of [renderStrip, renderBanners, renderMapBand, renderVerdict, renderDecks, renderDecision, renderFactors,
    renderEvents, renderAudit, renderLocks, renderContext, renderRecords, renderFoot]) {
    try { section(); } catch (err) { console.error(section.name, err); }
  }
}

/**
 * How long the gate has been closed. FMZ's own clock restarts with FMZ, so the
 * workbench's records are used too: the last transition into the closed state,
 * or, with none recorded, the run of consecutive closed daily snapshots.
 */
function gateClosed(view) {
  // Admission is net gamma; its since comes with the gate. The transition log follows the
  // flip anchor's utility and older daily snapshots used the anchor rule, so neither is used here.
  const gate = view?.space_gate || {};
  if (gate.eligible !== false) return null;
  const since = isNum(gate.since_ts_ms) ? gate.since_ts_ms : null;
  return isNum(since) ? { since, atLeast: false, text: dur(Date.now() - since) } : null;
}

function gateSince(gate) {
  const closed = gateClosed(state.view);
  return closed ? `已${closed.text}` : "";
}

function renderStrip() {
  const v = state.view || {};
  const mc = v.market_context || {};
  $("spot").textContent = usd(mc.current_price_usd ?? get(v, "btc_map.current_price_usd"));
  $("spot-age").textContent = isNum(mc.age_ms) ? `事实 ${dur(mc.age_ms)}前` : "";
  const gate = v.space_gate || {};
  const gateReasons = [...new Set([...(gate.reason_codes || []), ...(gate.gap_reasons || [])])].map(reasonCn);
  // The space structure is evaluated, not gated: the strip shows the anchor score.
  const anchorScore = get(gate, "anchor.score");
  const factCurrent = mc.status === "current";
  setLamp($("gate-state"), isNum(anchorScore) ? (factCurrent ? "ok" : "warn") : "off",
    isNum(anchorScore) ? `锚点 ${anchorScore.toFixed(0)} / 100` : "锚点缺失");
  $("gate-state").title = [factCurrent ? null : "FMZ 事实不是当前状态，锚点只作参考", ...gateReasons].filter(Boolean).join("；");
  const fact = mc.status === "current" ? "ok" : mc.status ? "warn" : "off";
  // background_only: the FMZ fact has a gap (a stale snapshot most often), so it only serves as background.
  const factNote = !mc.status || mc.status === "current" ? ""
    : (mc.gap_reasons || []).includes("fmz_snapshot_stale") && isNum(mc.age_ms) ? `，快照已停 ${dur(mc.age_ms)}`
    : mc.status === "background_only" ? "，仅作背景" : "，事实不是当前";
  setLamp($("fact-state"), fact, `FMZ ${mc.strategy_version || "—"}${factNote}`);
  const health = sourceHealth(v).filter((p) => p.collected);
  const okCount = health.filter((p) => p.level === "ok").length;
  const worst = health.some((p) => p.level === "bad") ? "bad" : health.some((p) => p.level === "warn") ? "warn" : health.length ? "ok" : "off";
  const src = $("sources");
  setLamp(src, worst, health.length ? `持续来源 ${okCount}/${health.length} 正常` : "来源未知");
  src.title = health.filter((p) => p.level !== "ok").map((p) => `${p.id}：${sourceStatusCn(p.status)}`).join("；") || "持续采集的来源全部正常";
  const activeLocks = (get(v, "locked_candidates.episodes") || []).filter((e) => e.status === "active").length;
  const lk = $("lock-state");
  lk.hidden = !activeLocks;
  if (activeLocks) setLamp(lk, "ok", `追踪中 ${activeLocks}`);
  const b = get(v, "manual_review_state.daily_http_budget") || {};
  $("budget").textContent = `今日复核 ${b.http_calls_used ?? 0}/${b.http_calls_limit ?? "—"}`;
  $("refreshed").textContent = state.error ? (busy(state.error) ? "繁忙，稍后重试" : "读取失败") : `更新于 ${ago(v.updated_at_ms)}`;
}

function setLamp(el, level, text) {
  clear(el).append(h("i", { class: `lamp ${level}` }), h("span", { text }));
}

function renderBanners() {
  const box = clear($("banners"));
  const v = state.view || {};
  state.notices = state.notices.filter((n) => n.until > Date.now());
  for (const n of state.notices) box.append(h("div", { class: "banner info" }, n.text));
  if (needsLogin()) {
    box.append(h("div", { class: "banner info gate" }, h("b", { text: "请先登录。" }),
      " 公网入口需要工作台令牌，登录后才读取数据；令牌只用于换取会话。",
      h("button", { class: "btn", type: "button", text: "输入令牌", onclick: () => openLogin("") })));
    return;
  }
  if (FIXTURE && !state.error) box.append(h("div", { class: "banner info" }, h("b", { text: "示例数据。" }),
    " 页面使用 2026-10-12 的真实冻结快照与已通过校验的模型回执拼成，用于检查版式；数值不代表当前市场。"));
  if (state.error && busy(state.error)) box.append(h("div", { class: "banner info" }, h("b", { text: `${errorText(state.error)}。` }),
    ` 约 ${Math.min(60, Math.max(3, state.error.retryAfter || 5))} 秒后自动重试${state.view ? "，页面保留上一次成功读取的内容" : ""}。`));
  else if (state.error) box.append(h("div", { class: "banner" }, h("b", { text: "读取失败。" }),
    ` ${errorText(state.error)}。${state.view ? "页面保留上一次成功读取的内容。" : ""}`));
  const fail = v.collector_failure;
  if (fail) box.append(h("div", { class: "banner" }, h("b", { text: "后台刷新失败。" }),
    ` ${clock(fail.failed_at_ms)} 的刷新没有完成，候选与地图停留在 ${clock(v.updated_at_ms)}。`));
  const mc = v.market_context || {};
  const factGaps = mc.gap_reasons || [];
  if (factGaps.includes("fmz_snapshot_stale")) box.append(h("div", { class: "banner" },
    h("b", { text: `FMZ 快照停在 ${clock(mc.snapshot_ts_ms)}（已 ${dur(mc.age_ms)}）。` }),
    " FMZ 策略可能没有在运行（例如服务器重启后机器人未恢复），请到 FMZ 检查机器人状态。"
    + "经 FMZ 转发的读数（锚点与空间结构、GEX、宏观）一同停更；候选与地图按最后一次冻结的事实核算。"));
  else if (mc.status === "background_only") box.append(h("div", { class: "banner info" }, h("b", { text: "FMZ 事实只作背景。" }),
    ` ${factGaps.length ? `${factGaps.map(reasonCn).join("、")}。` : ""}候选照常核算，涉及 FMZ 的读数只作参考。`));
  else if (mc.status && mc.status !== "current") box.append(h("div", { class: "banner" }, h("b", { text: "FMZ 事实不是当前状态。" }),
    ` ${(mc.gap_reasons || []).length ? `${mc.gap_reasons.map(reasonCn).join("、")}。` : ""}候选与地图只反映最后一次冻结。`));
  const errs = (v.candidates || []).filter((c) => c.status === "error");
  if (errs.length && !FIXTURE) box.append(candidateErrorBanner(errs));
}

/** Why no candidate was produced: the upstream's own reason when known, said once for both sides. */
function candidateErrorBanner(errs) {
  const why = (c) => {
    const reasons = get(c, "review_gate.reasons") || [];
    return reasons.find((r) => r.startsWith("upstream_")) || reasons[0] || "discovery_refresh_error";
  };
  const codes = [...new Set(errs.map(why))];
  const lastOk = Math.max(...errs.map((c) => get(c, "upstream.last_success_ms")).filter(isNum), 0) || null;
  const since = lastOk ? `上次成功抓取在 ${clock(lastOk)}，之后的报价与候选暂缺。` : "";
  if (codes.length === 1 && codes[0] === "upstream_deribit_maintenance") {
    return h("div", { class: "banner info" }, h("b", { text: "Deribit 交易所维护中。" }),
      ` 官方接口暂停服务，两侧候选暂不生成；接口恢复后自动继续。${since}锁定追踪在维护期间记为采样缺口。`);
  }
  if (codes.length === 1 && codes[0] === "upstream_deribit_unavailable") {
    return h("div", { class: "banner" }, h("b", { text: "Deribit 接口暂时不可用。" }),
      ` 两侧候选暂不生成，后台会按退避间隔重试。${since}`);
  }
  const text = codes.length === 1 ? `两侧：${reasonCn(codes[0])}`
    : errs.map((c) => `${c.side === "put" ? "Put" : "Call"}：${reasonCn(why(c))}`).join("；");
  return h("div", { class: "banner" }, h("b", { text: "候选未生成。" }), ` ${text}。${since}`);
}

// ── map band ─────────────────────────────────────
function renderMapBand() {
  if (!state.view) return;
  renderSessionClock();   // before the map: a map failure must not hide the strip
  const model = mapModel(state.view, state.sides);
  renderMap($("map"), $("map-tip"), model, { focus: state.focus, onPick: (side) => select(side) });
  legend($("map-legend"), model);
  const scroller = $("map").parentElement;
  if (scroller.scrollWidth > scroller.clientWidth && !scroller.dataset.centered) {
    scroller.scrollLeft = (scroller.scrollWidth - scroller.clientWidth) / 2;
    scroller.dataset.centered = "1";
  }
  const ref = state.sides[state.focus];
  $("map-sub").textContent = isNum(model.sigmaPct)
    ? `到期前隐含预期波动 ±${pct(model.sigmaPct, 2)}，约 ±${usd(model.spot * model.sigmaPct / 100)} 美元（${ref?.iv?.expiry || "近月"} ATM IV ${pct(ref?.iv?.iv)}）`
    : "现价、两侧责任区与市场结构位放在同一价格轴上";
}

function renderSessionClock() {
  const box = $("session-strip");
  if (!box) return;
  clear(box);
  if (state.view) box.append(sessionStrip(state.view));
}

function select(side) {
  if (!state.sides[side]) return;
  state.focus = side;
  state.auditRef = null; state.auditPoint = null; state.iq.q = null; state.iq.ref = null;
  renderMapBand(); renderDecks(); renderAudit();
}

// ── round verdict: the preconditions, stated once ─
const warming = (v) => !v?.updated_at_ms || (v.gaps || []).includes("data_collector_warming_up");

function renderVerdict() {
  const box = clear($("verdict"));
  if (!state.view) return;
  if (warming(state.view)) {
    box.className = "verdict blocked";
    box.append(h("b", { text: "后台采集预热中" }), h("span", { text: "服务刚重启，首轮刷新完成前（约 1 分钟）还没有候选与前置条件判断。" }));
    return;
  }
  const u = underwriting(state.view, state.sides);
  const gate = state.view.space_gate || {};
  if (u.open) {
    box.className = "verdict open";
    box.append(h("b", { text: "满足承保前置条件" }), h("span", { text: "两侧快照与报价可用，没有硬门槛阻止；是否承保由你在下面的保单与核验中判断。" }));
    return;
  }
  box.className = "verdict blocked";
  box.append(h("b", { text: "本轮不满足承保前置条件" }));
  for (const item of u.shared) {
    box.append(h("span", { class: "why-item", title: item.title || "",
      text: item.key === "space" && gateSince(gate) ? `${item.text}，${gateSince(gate)}` : item.text }));
  }
  // nothing shared: say which side is held back and why, never a bare "不满足"
  if (!u.shared.length) {
    for (const side of ["put", "call"]) {
      const why = u.perSide[side] || [];
      if (why.length) box.append(h("span", { class: "why-item", text: `${side === "put" ? "Put" : "Call"}：${why.join("、")}` }));
    }
  }
  box.append(h("span", { class: "then", text: "仍可核验与锁定追踪，用于了解责任与补偿如何变化。" }));
}

// ── four core elements (shared market layer) ────
function currentSpot() {
  return get(state.view, "market_context.current_price_usd") ?? state.sides.put?.spot ?? state.sides.call?.spot;
}

function renderFactors() {
  const box = clear($("factors"));
  if (!state.view) return;
  for (const f of coreFactors(state.view, currentSpot())) {
    const card = h("div", { class: `sheet factor${f.lines ? " wide" : ""}${f.missing ? " missing" : ""}` },
      h("div", { class: "factor-head" }, h("h4", { text: f.title }), h("b", { class: f.headCls || "", text: f.missing ? "数据缺口" : f.headline })));
    card.append(f.lines ? positionLines(f.lines) : metricRows(f.rows));
    if (f.note) card.append(h("p", { class: "factor-note", text: f.note }));
    box.append(card);
  }
}

/** Spatial position: one short sentence per side, with the KPF description beneath it. */
function positionLines(lines) {
  const box = h("div", { class: "position-lines" });
  for (const l of lines) {
    box.append(h("div", { class: "pos-line" },
      h("div", { class: "pos-head" },
        h("b", { text: `${l.name} 短腿 ${usd(l.short)}` }),
        h("span", { class: "muted small", text: isNum(l.distance) ? `距现价 ${pct(l.distance, 2)}${isNum(l.moves) ? `，${l.moves.toFixed(1)} 倍预期波动` : ""}` : "" }),
        h("span", { class: `chip ${l.cls}`, text: l.text })),
      h("p", { class: "pos-reason", text: l.reason }),
      l.kpf ? h("p", { class: "pos-kpf", text: `KPF（只描述）：${l.kpf}` }) : null));
  }
  return box;
}

/** label | inline chart | value. A gap is a missing value; pending is "not measurable yet". */
function metricRows(rows) {
  const box = h("div", { class: "metrics" });
  for (const r of rows) {
    box.append(h("div", { class: `metric${r.gap ? " gap" : r.pending ? " pending" : ""}${r.viz ? " has-viz" : ""}` },
      h("span", { class: "m-label", text: r.label }),
      h("span", { class: "m-value", text: r.value }),
      r.viz ? h("span", { class: "m-viz" }, r.viz) : null));
  }
  return box;
}

// ── decision cards ───────────────────────────────
function renderDecks() {
  const box = clear($("decks"));
  if (!state.view) return;
  if (warming(state.view)) { box.append(h("div", { class: "sheet deck skeleton" }), h("div", { class: "sheet deck skeleton" })); return; }
  const u = underwriting(state.view, state.sides);
  for (const side of ["put", "call"]) box.append(deck(state.sides[side], side, u.perSide[side] || []));
}

function remainingText(m) {
  if (!isNum(m.remainingMs)) return "—";
  const left = m.remainingMs - (Date.now() - (state.loadedAt || Date.now()));
  return left > 0 ? `剩余 ${(left / 3.6e6).toFixed(1)} 小时` : "已到期";
}

function deck(m, side, blockers) {
  const card = h("article", { class: `sheet deck ${side}${state.focus === side ? " selected" : ""}`, tabindex: 0,
    "aria-label": `${side === "put" ? "Put" : "Call"} 候选，选中后地图与推理审计跟随`, "aria-pressed": String(state.focus === side),
    onclick: () => select(side),
    onkeydown: (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === card) { e.preventDefault(); select(side); } } });
  const name = side === "put" ? "Put 价差" : "Call 价差";
  if (!m || !m.snap) {
    card.append(h("div", { class: "deck-head" }, h("h3", { text: name }), h("span", { class: "seal none", text: "无候选" })));
    card.append(h("p", { class: "empty", text: blockers.length ? `本轮没有可展示的候选：${blockers.join("、")}。` : "本轮没有可展示的候选。" }));
    return card;
  }
  card.append(h("div", { class: "deck-head" },
    h("h3", { text: name }),
    h("span", { class: "legs", text: `${(m.shortName || "").split("-")[1] || ""}  卖 ${usd(m.short)} / 买 ${usd(m.long)}，${remainingText(m)}` })));
  card.append(warningBlock(m.candidate, { isOpen: (key) => state.open.has(key), keepOpen }));
  if (blockers.length) card.append(h("p", { class: "side-block", text: `本侧另有：${blockers.join("、")}` }));

  const move = m.sigmaPct;
  // How close the short leg is, relative to the move the market prices until expiry.
  const tone = isNum(move) && isNum(m.shortOutsidePct)
    ? (m.shortOutsidePct < 0.25 * move ? "bad" : m.shortOutsidePct < 0.5 * move ? "warn" : "") : "";
  const usdOf = (p) => (isNum(p) && isNum(m.spot) ? usd(m.spot * p / 100) : "—");
  const beValue = m.breakevenState === "ok" ? pct(m.beOutsidePct, 2) : m.breakevenState === "none" ? "无" : "未知";
  const beSub = m.breakevenState === "ok" ? `${usd(m.breakeven)} 美元` : m.breakevenState === "none" ? "净信用不为正，不存在盈亏平衡" : "费用或报价不足，未能计算";
  const quoteAge = (isNum(m.quoteAt) ? `报价 ${ago(m.quoteAt)}` : "报价时间未知")
    + (m.quoteGaps.length ? `，${[...new Set(m.quoteGaps.map((g) => (/depth/.test(g) ? "盘口深度不足" : "报价有缺口")))].join("、")}` : "");
  const absDelta = isNum(m.delta) ? Math.abs(m.delta).toFixed(2) : null;
  const distanceSub = !isNum(m.shortOutsidePct) ? "距离未能计算"
    : `${usdOf(m.shortOutsidePct)} 美元，${m.shortOutsidePct >= 0 ? (tone === "bad" ? "贴近责任区" : "在责任区外") : "已进入责任区"}${absDelta ? `；短腿 Delta ${absDelta}` : ""}`;
  const events = get(m, "candidate.exogenous_events.events") || [];
  // Premium amounts start at Decision Path Q2; the card keeps one ratio for comparing the two sides.
  card.append(h("div", { class: "figures" },
    fig(pct(m.shortOutsidePct, 2), "短腿距现价", tone, distanceSub),
    fig(beValue, "盈亏平衡距现价", "", beSub),
    fig(ratioPct(m.creditToWidth), "毛信用占宽度", "", quoteAge),
    fig(get(m, "candidate.exogenous_events") ? `${events.length} 项` : "—", "到期前事件", events.length ? "warn" : "",
      events.length ? events.map((e) => e.name_cn).join("、")
        : (get(m, "candidate.exogenous_events.gaps") || []).length ? "日历未完全覆盖本期" : "无已排期的重要宏观发布"),
  ));
  card.append(actions(m));
  return card;
}

const FIG_TIP = {
  "毛信用占宽度": "未扣费的可见信用 ÷ 价差宽度，用于比较两侧；金额、费用与市场估价在核验路径的 Q2。",
  "到期前事件": "合约有效期内已排期的美国宏观发布；时间与过滤在核验路径的 D4。",
};
function fig(value, label, cls = "", sub = "") {
  return h("div", { class: `fig ${cls}`, title: FIG_TIP[label] || null }, h("div", { class: "v", text: value }), h("div", { class: "k", text: label }),
    sub ? h("div", { class: "k", text: sub }) : null);
}

function chip(text, cls = "") { return h("span", { class: `chip ${cls}`, text }); }

function distanceRule(m) {
  // From spot toward this book's adverse side, in % of price; the blue part is
  // the implied expected move, so a short leg inside it is within one move.
  // Marks and labels are HTML placed by percent, so text is never stretched with the bar.
  const wrap = h("div", { class: "sigma-rule", title: "从现价朝本单不利方向的距离（%）；蓝色为到期前隐含预期波动范围" });
  if (!isNum(m.shortOutsidePct)) return wrap;
  const move = isNum(m.sigmaPct) ? m.sigmaPct : null;
  const max = Math.max(move ? 2 * move : 0, (m.beOutsidePct || 0) * 1.3, m.shortOutsidePct * 1.3, 1);
  const P = (v) => `${(Math.max(0, Math.min(max, v)) / max * 100).toFixed(2)}%`;
  const short = Math.max(0, m.shortOutsidePct);
  const track = h("div", { class: "sr-track" },
    move ? h("span", { class: "sr-move", style: `width:${P(move)}` }) : null,
    h("span", { class: "sr-liab", style: `left:${P(short)}` }));
  const marks = [h("span", { class: `sr-short ${m.side}`, style: `left:${P(short)}` }),
    h("span", { class: `sr-short-label ${m.side}`, style: `left:${P(short)}`, text: "短腿" })];
  if (isNum(m.beOutsidePct)) marks.push(h("span", { class: `sr-be ${m.side}`, style: `left:${P(m.beOutsidePct)}`, title: "盈亏平衡" }));
  const ticks = move ? [0, move / 2, move, 2 * move] : [0, max / 2, max];
  const labels = ticks.map((t, i) => h("span", {
    class: `sr-tick${i === 0 ? " first" : i === ticks.length - 1 ? " last" : ""}`, style: `left:${P(t)}`,
    text: t === 0 ? "现价" : move && Math.abs(t - move) < 1e-9 ? `${pct(t, 2)} 预期波动` : pct(t, 2) }));
  wrap.append(track, ...marks, ...labels);
  return wrap;
}

function question(title, points, kind, labels) {
  const box = h("div", { class: "q" }, h("h4", { text: title + (kind === "program" ? "（程序摘要，未经模型）" : "") }));
  if (!points.length) { box.append(h("p", { class: "muted", text: "暂无。" })); return box; }
  const ul = h("ul");
  for (const p of points.slice(0, 3)) ul.append(h("li", { text: humanize(p.text_cn, labels) }));
  box.append(ul);
  return box;
}

function cooldownLeft() {
  const until = get(state.view, "manual_review_state.cooldown_until_ms");
  return isNum(until) ? until - Date.now() : 0;
}

function verifyOf(m) {
  const cid = m?.snap?.candidate_id;
  return cid ? get(state.view, `decision_path.open.${cid}`) : null;
}

/** Card actions: start or continue verification, and the direct lock (both lead to tracking). */
function actions(m) {
  const wrap = h("div", { class: "actions", onclick: (e) => e.stopPropagation() });
  const lock = m.candidate.lock_availability || {};
  const pending = state.pending && state.pending.side === m.side && state.pending.kind === "lock" ? state.pending : null;
  if (pending && pending.stage === "confirm") {
    wrap.append(h("span", { class: "confirm", text: "不经核验直接锁定：每分钟采样同一合同，直到到期或手动停止；锁定时与之后的变化在“锁定追踪”里查看。" }),
      h("button", { class: "btn", type: "button", onclick: () => run("lock", m) }, "确认锁定"),
      h("button", { class: "btn secondary", type: "button", onclick: () => { state.pending = null; renderDecks(); } }, "取消"));
    return wrap;
  }
  if (pending && pending.stage === "running") { wrap.append(h("span", { class: "confirm", text: "正在锁定…" })); return wrap; }
  const needLogin = !state.session.authenticated;
  const open = verifyOf(m);
  const onScreen = state.dp.session && state.dp.session.candidate_id === m.snap.candidate_id;
  wrap.append(h("button", { class: "btn", type: "button", disabled: needLogin || FIXTURE || state.dp.busy === "start",
    title: "冻结这张保单，按固定读序逐步核验；结论可以是锁定追踪、不承保或继续观察",
    onclick: () => (open ? openVerification(open.verification_id) : startVerification(m)) },
  open ? (onScreen ? "查看核验" : "继续核验") : "开始核验"));
  wrap.append(h("button", { class: "btn secondary", type: "button", disabled: !lock.enabled || needLogin || FIXTURE,
    title: "不经核验直接锁定追踪",
    onclick: () => { state.pending = { side: m.side, kind: "lock", stage: "confirm" }; renderDecks(); } }, "直接锁定"));
  const why = [];
  if (needLogin && !FIXTURE) why.push("登录后可操作");
  if (!lock.enabled && lock.reason) why.push(`锁定：${reasonCn(lock.reason)}`);
  if (open && !onScreen) why.push(`核验进行中，${ago(open.started_at_ms)}开始`);
  if (pending && pending.stage === "done") why.unshift(pending.message);
  if (why.length) wrap.append(h("span", { class: "why", text: [...new Set(why)].join("；") }));
  return wrap;
}

/** The model review, now inside the Decision Path audit step (same request and states). */
function reviewActions(m) {
  const wrap = h("div", { class: "actions", onclick: (e) => e.stopPropagation() });
  const obs = m.candidate.observation_review || {};
  const pending = state.pending && state.pending.side === m.side && state.pending.kind === "review" ? state.pending : null;
  const production = productionReady(m);
  if (pending && pending.stage === "confirm") {
    const b = get(state.view, "manual_review_state.daily_http_budget") || {};
    const text = production
      ? `模型复核：用当时报价重新冻结本保单后调用一次模型（今日 ${b.http_calls_used ?? 0}/${b.http_calls_limit ?? "—"}），约 2–4 分钟，可离开页面，完成后自动刷新；回执附着本快照，仍不构成交易许可。`
      : `观察复核：调用一次模型解释本快照（今日 ${b.http_calls_used ?? 0}/${b.http_calls_limit ?? "—"}），约 2–4 分钟，可离开页面，完成后自动刷新；报价、费用或 FMZ 事实未达生产复核条件，回执不附着生产。`;
    wrap.append(h("span", { class: "confirm", text }),
      h("button", { class: "btn", type: "button", onclick: () => run("review", m) }, "确认复核"),
      h("button", { class: "btn secondary", type: "button", onclick: () => { state.pending = null; renderSides(); } }, "取消"));
    return wrap;
  }
  if (pending && pending.stage === "running") {
    wrap.append(h("span", { class: "confirm", text: "模型复核进行中，完成后自动刷新。" }));
    return wrap;
  }
  const needLogin = !state.session.authenticated;
  const reviewOpen = production ? !(get(m, "candidate.review_gate.reasons") || []).some((r) => /cooldown|inflight|budget/.test(r)) : obs.enabled;
  wrap.append(h("button", { class: "btn secondary", type: "button", disabled: !reviewOpen || needLogin || FIXTURE,
    title: production ? "生产复核：回执附着本快照，仍不构成交易许可" : "观察用途：模型只解释冻结快照，回执不附着生产",
    onclick: () => { state.pending = { side: m.side, kind: "review", stage: "confirm" }; renderSides(); } }, production ? "请求模型复核" : "请求观察复核"));
  const why = [];
  if (needLogin && !FIXTURE) why.push("登录后可操作");
  const gateReasons = production ? (get(m, "candidate.review_gate.reasons") || []).filter((r) => /cooldown|inflight|budget/.test(r))
    : obs.enabled ? [] : obs.reasons || [];
  for (const r of gateReasons) {
    const left = cooldownLeft();
    why.push((r === "manual_review_cooldown_active" || r === "review_cooldown") && left > 0 ? `复核冷却中，还剩 ${dur(left)}` : reasonCn(r));
  }
  if (pending && pending.stage === "done") why.unshift(pending.message);
  if (why.length) wrap.append(h("span", { class: "why", text: [...new Set(why)].join("；") }));
  return wrap;
}

function renderSides() { renderDecks(); renderDecision(); }

async function run(kind, m) {
  state.pending = { side: m.side, kind, stage: "running" };
  renderSides();
  try {
    if (kind === "review") {
      const purpose = productionReady(m) ? "review" : "observation";
      const res = await api.post("/review", { candidate_id: m.snap.candidate_id, idempotency_key: idempotencyKey("ui-review"),
        purpose, ...(purpose === "observation" ? { snapshot_id: m.snap.snapshot_id } : {}) });
      const st = res.status || get(res, "review.status");
      const message = st === "RESEARCH_OK" || st === "OK" ? "复核完成，已通过校验。"
        : st === "rejected" && res.llm_call_count === 0 ? `未发起模型调用：${reasonCn(res.reason)}`
        : st === "ERROR" ? `复核失败：${reasonCn(get(res, "error.code") || "unknown")}`
        : `复核已完成，但解释未通过程序校验（${statusCn(get(res, "review.status") || st)}），原文已留档。`;
      state.pending = { side: m.side, kind, stage: "done", message };
    } else {
      const res = await api.post("/locked-candidates", { candidate_id: m.snap.candidate_id, snapshot_id: m.snap.snapshot_id,
        idempotency_key: idempotencyKey("ui-lock") });
      state.pending = { side: m.side, kind, stage: "done", message: res.created ? "已锁定，在“锁定追踪”查看。" : "该合同已在追踪中。" };
      const id = get(res, "episode.episode_id");
      if (id) state.lock.id = id;
    }
  } catch (err) {
    authFailed(err);
    state.pending = { side: m.side, kind, stage: "done", message: `未完成：${errorText(err)}` };
  }
  await load(true);
  if (kind === "lock" && state.lock.id) fetchLock(state.lock.id);
}

// ── Decision Path ────────────────────────────────
/** The current card of the session's side, when it still shows the same contract. */
function sameContractSide(session) {
  const m = session && state.sides[session.side];
  return m?.snap?.candidate_id === session?.candidate_id ? m : null;
}

/** The audit step: the model's reading of this contract and the review request, reused from the card era. */
function auditExtra(session) {
  const m = sameContractSide(session);
  const box = h("div", { class: "dp-audit" });
  if (!m) {
    box.append(h("p", { class: "muted small", text: state.sides[session.side]?.snap
      ? "保单卡已换为另一张合同；模型复核只对卡上当前这张保单发起。" : "这一侧当前没有保单，无法发起模型复核。" }));
  } else {
    const src = m.review.kind === "current" ? "模型解释：对应当前快照"
      : m.review.kind === "historical" ? `模型解释：${clock(reviewedAt(m.review))} 的回执，之后报价已更新` : "还没有模型回执；程序读数见上面各步。";
    box.append(h("p", { class: "muted small", text: src }));
    if (m.review.kind !== "program") {
      box.append(question("模型解释 · 风险质量", m.review.q1, m.review.kind, m.review.labels));
      box.append(question("模型解释 · 风险价格", m.review.q2, m.review.kind, m.review.labels));
    }
    box.append(reviewActions(m));
  }
  // the inquirer's questions on the frozen contract (sessions started before them do not show it)
  if (session.au_mode) {
    const got = iqEntry(session.contract, { summary: true });
    box.append(auSummary(got?.data || null, { error: got?.error, onOpen: (file) => openInquiryAudit(session, file) }));
  }
  return box;
}

// ── question sheets (docs/opus/14) ───────────────
const IQ_TTL_MS = 5 * 60_000;   // sheets change only at 10:00 / 22:00 and on a new contract
const IQ_RETRY_MS = 30_000;      // a failed read is retried sooner
const iqCacheKey = (contract, { file = null, summary = false } = {}) => `${contractKey(contract)}|${summary ? "s" : "f"}|${file || ""}`;
const iqEntry = (contract, opts) => (contractKey(contract) ? state.iq.cache[iqCacheKey(contract, opts)] : null);

/** Read a contract's sheets once per few minutes; redraw the parts that show them. */
async function ensureInquiry(contract, opts = {}) {
  if (!contractKey(contract)) return;
  const key = iqCacheKey(contract, opts);
  const got = state.iq.cache[key];
  if (got && (got.loading || Date.now() - got.at < (got.error ? IQ_RETRY_MS : IQ_TTL_MS))) return;
  state.iq.cache[key] = { ...(got || {}), loading: true, at: Date.now() };
  let entry;
  try {
    const data = FIXTURE ? await fetchInquiryFixture(opts) : await api.inquiry(contract, opts);
    entry = { data, at: Date.now() };
  } catch (err) {
    authFailed(err);
    // a chosen sheet that left the list (closed contracts are kept three days) falls back to the newest
    if (opts.file && err?.body?.code === "inquiry_file_not_listed") {
      delete state.iq.cache[key];
      const ck = contractKey(contract);
      if (state.iq.file[ck] === opts.file) delete state.iq.file[ck];
      renderAudit();
      return;
    }
    entry = { data: got?.data || null, error: errorText(err), at: Date.now() };
  }
  state.iq.cache[key] = entry;
  if (opts.summary) renderDecision(); else renderAudit();   // only the part that shows this response
}

async function fetchInquiryFixture({ summary = false } = {}) {
  const r = await fetch("fixtures/inquiry.json");
  if (!r.ok) throw Object.assign(new Error(`HTTP ${r.status}`), { status: r.status });
  const data = await r.json();
  return summary ? toSummary(data) : data;
}

/** What the audit step showed, sent with its opening and marks (the server checks the file). */
function auShown(session) {
  const m = sameContractSide(session);
  const file = iqEntry(session.contract, { summary: true })?.data?.selected?.file || null;
  const out = { inquiry_file: file };
  if (m) { out.review_kind = m.review.kind; const at = reviewedAt(m.review); if (isNum(at)) out.review_at_ms = at; }
  return out;
}

/** From the audit step to the full sheet of the session's contract. */
function openInquiryAudit(session, file) {
  const key = contractKey(session.contract);
  state.iq.mode = "inquiry";
  state.iq.pin = { side: session.side, contract: session.contract, key };
  if (file) state.iq.file[key] = file;
  state.iq.reveal.add(session.verification_id);   // reached from the audit step: D0–Q2 were read first
  state.iq.q = null; state.iq.ref = null;
  select(session.side);
  $("audit-h")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderDecision() {
  const box = $("decision");
  if (!box) return;
  const dp = state.dp;
  const session = dp.session;
  const m = session ? state.sides[session.side] : null;
  const same = sameContractSide(session);
  syncDecision();
  // Redraw only when something shown here changed: a poll must not rebuild buttons under the cursor.
  const key = JSON.stringify([session && [session.verification_id, session.status, session.opened, session.marks,
    session.readings?.now?.at_ms, session.decisions?.length, session.outcome?.status],
  dp.busy, dp.error, dp.confirm, state.session.authenticated, m?.snap?.candidate_id, same?.review?.kind,
  same && reviewedAt(same.review), state.pending, get(state.view, "decision_path.recent"),
  get(state.view, "manual_review_state.inflight"), Math.floor(cooldownLeft() / 60_000), Math.floor(Date.now() / 60_000),
  get(state.view, "decision_path.error_type"), get(state.view, "decision_path.unreadable_sessions"),
  session?.au_mode ? iqEntry(session.contract, { summary: true })?.at : null]);
  // fetched once Q2 is open (shown only when AU is), so the audit step's opening records the sheet on screen
  if (session?.au_mode && session.opened && ("Q2" in session.opened || "AU" in session.opened)) ensureInquiry(session.contract, { summary: true });
  if (key === dp.drawn) return;
  dp.drawn = key;
  renderDecisionView(box, {
    session, overview: get(state.view, "decision_path"), busy: dp.busy, error: dp.error, confirm: dp.confirm,
    canAct: state.session.authenticated && !FIXTURE,
    replaced: session && m?.snap && !same ? `${(m.shortName || "").split("-")[1] || ""} 卖 ${usd(m.short)} / 买 ${usd(m.long)}` : null,
    extras: session ? { D2: same ? distanceRule(same) : null, AU: auditExtra(session) } : null,
    onStep: (step, lean) => dpPost(`/${session.verification_id}/step`, { step, ...(lean ? { lean } : {}),
      ...(step === "AU" && session.au_mode ? { shown: auShown(session) } : {}) }, "step"),
    onRefresh: () => dpPost(`/${session.verification_id}/refresh`, {}, "refresh"),
    onAskLock: () => { dp.confirm = "lock"; renderDecision(); },
    onCancel: () => { dp.confirm = null; renderDecision(); },
    onConclude: (choice) => dpConclude(choice),
    onClose: () => { dp.session = null; dp.error = null; dp.confirm = null; renderSides(); },
    onOpen: (id) => openVerification(id),
    onJump: (target, side) => { if (target === "map-h" || target === "audit-h") select(side); $(target)?.scrollIntoView({ behavior: "smooth", block: "start" }); },
    onOpenLock: (id) => { state.lock.id = id; renderLocks(); fetchLock(id); $("lock-h").scrollIntoView({ behavior: "smooth", block: "start" }); },
  });
}

function showDecision() {
  renderSides();
  $("decision-h").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function startVerification(m) {
  const dp = state.dp;
  dp.busy = "start"; dp.error = null; renderSides();
  try {
    const res = await api.post("/decision-path", { candidate_id: m.snap.candidate_id, snapshot_id: m.snap.snapshot_id });
    dp.session = res.session; dp.confirm = null;
    // the first step opens at once
    if (!Object.keys(dp.session.opened || {}).length) {
      dp.session = (await api.post(`/decision-path/${dp.session.verification_id}/step`, { step: dp.session.steps[0].id })).session;
    }
  } catch (err) { authFailed(err); dp.error = `未能开始核验：${errorText(err)}`; }
  dp.busy = null;
  await load(true);
  showDecision();
}

async function openVerification(id, { scroll = true } = {}) {
  const dp = state.dp;
  const seq = (dp.seq = (dp.seq || 0) + 1);
  dp.error = null;
  try {
    const session = await api.verification(id);
    if (seq !== dp.seq) return;  // a later open wins
    dp.session = session; dp.confirm = null;
  } catch (err) {
    if (seq !== dp.seq) return;
    authFailed(err);
    if (err?.status === 404) { dp.session = null; notice("这条核验的合约已到期，结果已回填并清理。"); }
    else dp.error = `未能读取核验：${errorText(err)}`;
  }
  if (scroll) showDecision(); else renderSides();
}

/** The server closes sessions at expiry and deletes them after settlement: follow it. */
function syncDecision() {
  const s = state.dp.session;
  if (!s || s.status !== "open" || state.dp.busy || FIXTURE) return;
  const listed = (get(state.view, "decision_path.recent") || []).find((r) => r.verification_id === s.verification_id);
  const key = `${s.verification_id}@${get(state.view, "served_at_ms") ?? get(state.view, "updated_at_ms")}`;
  if (get(state.view, "decision_path.recent") && (!listed || listed.status !== "open") && state.dp.synced !== key) {
    state.dp.synced = key;  // at most one re-read per view
    openVerification(s.verification_id, { scroll: false });
  }
}

async function dpPost(path, body, kind) {
  const dp = state.dp;
  if (dp.busy) return;
  dp.busy = kind; dp.error = null; renderDecision();
  try { dp.session = (await api.post(`/decision-path${path}`, body)).session; }
  catch (err) {
    authFailed(err);
    dp.error = `${kind === "refresh" ? "刷新" : "记录"}未完成：${errorText(err)}`;
    const code = err?.body?.code;
    if (err?.status === 404 || ["verification_closed", "contract_expired"].includes(code)) {
      dp.busy = null;
      await openVerification(dp.session.verification_id, { scroll: false });
      return;
    }
  }
  dp.busy = null; renderDecision();
  if (kind === "step" && !body.lean) {
    const step = document.getElementById(`dp-${body.step}`);
    step?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    step?.querySelector(".dp-step-head")?.focus({ preventScroll: true });
  }
}

async function dpConclude(choice) {
  const dp = state.dp;
  const session = dp.session;
  if (dp.busy || !session) return;  // one conclusion at a time (the server refuses a second as well)
  dp.busy = choice; dp.error = null; renderDecision();
  try {
    const res = await api.post(`/decision-path/${session.verification_id}/conclude`,
      { choice, ...(choice === "lock" ? { idempotency_key: idempotencyKey("ui-dp-lock") } : {}) });
    dp.session = res.session; dp.confirm = null;
    const id = get(res, "lock.episode.episode_id");
    if (id) { state.lock.id = id; notice(res.lock.created ? "核验结论：已锁定，在“锁定追踪”查看。" : "该合同已在追踪中，核验已关联到它。"); }
    else notice(choice === "watch" ? "已记为继续观察，可从保单卡“继续核验”回来。" : "核验结论已记录：不承保。");
  } catch (err) { authFailed(err); dp.error = `结论未记录：${errorText(err)}`; }
  dp.busy = null;
  await load(true);
  renderDecision();
  if (state.lock.id && choice === "lock") fetchLock(state.lock.id);
}

// ── reasoning audit ──────────────────────────────
/** Whether this contract was under lock tracking at a moment (any episode covering it). */
function lockStateAt(candidateId, atMs) {
  if (!candidateId || !isNum(atMs)) return null;
  const eps = (get(state.view, "locked_candidates.episodes") || []).filter((e) => e.candidate_id === candidateId);
  const hit = eps.find((e) => {
    const end = e.stopped_at_ms ?? (e.status === "active" ? Infinity : get(e, "contract.expiry_ms") ?? Infinity);
    return isNum(e.created_at_ms) && e.created_at_ms <= atMs && atMs < end;
  });
  return { locked: !!hit, episode: hit || null };
}

function lockChip(ls) {
  if (!ls) return null;
  return h("span", { class: `chip ${ls.locked ? "locked" : ""}`, title: ls.locked ? "复核发生时这张合约正在锁定追踪" : "复核发生时这张合约没有被锁定",
    text: ls.locked ? "复核时：已锁定" : "复核时：未锁定" });
}

const LANE_CN = { L1: "短腿缓冲", L2: "盈亏平衡", L3: "补偿", L4: "Gamma 体制", L5: "锚定", L6: "流向" };

/** Hold reviews (made while locked) of the focused side, newest first. */
function holdAudit(side) {
  const rows = (get(state.view, "hold_reviews") || []).filter((r) => r.side === side).slice(0, 3);
  if (!rows.length) return null;
  const box = h("section", { class: "hold-audit" }, h("h4", { text: "持有中复核（锁定后，模型对照依据账本）" }));
  for (const r of rows) {
    const token = String(r.short_instrument || "").split("-")[1] || "";
    const a = r.answer || {};
    box.append(h("div", { class: "hold-audit-item" },
      h("div", { class: "hold-audit-head" },
        h("b", { text: `${token} ${side === "put" ? "Put" : "Call"} 卖 ${usd(r.short_strike_usd)} / 买 ${usd(r.long_strike_usd)}` }),
        h("span", { class: "muted small", text: `${clock(r.finished_at_ms)} 复核（锁定于 ${clock(r.locked_at_ms)}）` }),
        lockChip({ locked: true }),
        h("a", { href: "#lock-h", class: "dp-jump", onclick: (e) => { e.preventDefault(); state.lock.id = r.episode_id; renderLocks(); fetchLock(r.episode_id);
          $("lock-h").scrollIntoView({ behavior: "smooth", block: "start" }); } }, "锁定追踪")),
      a.summary ? h("p", { class: "hold-audit-sum", text: a.summary }) : null,
      (a.points || []).length ? h("ul", { class: "q" }, ...a.points.map((p) => h("li", {},
        h("span", { class: "chip", title: "依据账本中的这条依据", text: `${p.thesis_id} ${LANE_CN[p.thesis_id] || ""}`.trim() }), " ", p.text))) : null,
      (a.watch || []).length ? h("p", { class: "muted small", text: `到期前留意：${a.watch.join("；")}` }) : null));
  }
  return box;
}

function renderAudit() {
  renderAuditMain();
  const box = $("audit");
  const hold = state.view && state.iq.mode !== "inquiry" ? holdAudit(state.focus) : null;
  if (box && hold) box.append(hold);
}

function renderAuditMain() {
  const sw = clear($("audit-switch"));
  for (const [mode, text] of [["review", "模型复核"], ["inquiry", "提问单"]]) {
    sw.append(h("button", { class: `chip${state.iq.mode === mode ? " on" : ""}`, type: "button", "aria-pressed": String(state.iq.mode === mode),
      title: mode === "inquiry" ? "提问者对这张合约提出的问题（定时 10:00 / 22:00 与新合约），未验证，只作参考" : "模型对冻结快照的逐点解释",
      onclick: () => { state.iq.mode = mode; renderAudit(); } }, text));
  }
  sw.append(h("span", { class: "sep" }));
  for (const side of ["put", "call"]) {
    const m = state.sides[side];
    sw.append(h("button", { class: `chip${state.focus === side ? " on" : ""}`, type: "button", disabled: !m?.snap,
      onclick: () => select(side) }, side === "put" ? "Put" : "Call"));
  }
  const box = clear($("audit"));
  if (!state.view) return;
  if (state.iq.mode === "inquiry") { renderInquiryAudit(box); return; }
  const m = state.sides[state.focus];
  if (!m || !m.snap) { box.append(h("p", { class: "empty pad", text: "没有可审计的候选。" })); return; }
  const r = m.review;
  const left = h("div", { class: "points" });
  const right = h("div", { class: "ledger" });
  box.append(h("div", { class: "audit-main" }, left, right));

  if (r.kind === "program") {
    left.append(h("p", { class: "muted", text: r.reason || "当前快照还没有通过校验的模型解释，下面是程序读法。" }));
    left.append(programAudit(m));
    right.append(h("h4", { text: "证据" }), h("p", { class: "muted", text: "请求观察复核后，这里逐项列出模型收到并引用的证据。当前快照的程序证据可直接查看：" }),
      evidenceLinks(m));
    return;
  }

  if (r.kind === "historical") left.append(h("p", { class: "banner info", text:
    `回执时间 ${clock(reviewedAt(r))}（${ago(reviewedAt(r))}）。之后报价已刷新，下面的解释对应当时的报价与证据，仅作参考；数字以当前卡片为准。` }));
  const ap = applicability(r.applicability);
  const rcid = get(r, "review.candidate_id") || get(r, "receipt.candidate_id") || m.snap.candidate_id;
  left.append(h("div", { class: "deck-head" }, h("h3", { text: `${m.name} 模型解释` }), h("span", { class: `seal ${ap.cls}`, text: ap.text }),
    lockChip(lockStateAt(rcid, reviewedAt(r)))));
  for (const [title, pts, q] of [["Q1 风险质量", r.q1, "q1"], ["Q2 风险价格", r.q2, "q2"]]) {
    left.append(h("h4", { text: title }));
    pts.forEach((p, i) => {
      const key = `${q}.${i}`;
      const on = state.auditPoint === key;
      const toggle = () => { state.auditPoint = on ? null : key; state.auditRef = null; renderAudit(); };
      const el = h("div", { class: `point${on ? " on" : ""}`, tabindex: 0, role: "button", "aria-pressed": String(on),
        onclick: toggle, onkeydown: (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === el) { e.preventDefault(); toggle(); } } },
        h("p", { text: humanize(p.text_cn, r.labels) }),
        h("div", { class: "refs" }, ...(p.evidence_refs || []).map((ref) => h("button", { class: `chip${state.auditRef === ref ? " on" : ""}`, type: "button", title: ref,
          onclick: (e) => { e.stopPropagation(); state.auditRef = state.auditRef === ref ? null : ref; state.auditPoint = null; renderAudit(); scrollToRef(ref); } }, evidenceName(ref, r.labels)))));
      left.append(el);
    });
  }
  left.append(guardrails(r.review || {}, r.labels));
  left.append(metaView(m, r));
  state.labels = r.labels;
  box.append(traceView(r));

  const activeRefs = state.auditPoint
    ? new Set(get(r, state.auditPoint.startsWith("q1") ? "q1" : "q2")[Number(state.auditPoint.split(".")[1])]?.evidence_refs || [])
    : state.auditRef ? new Set([state.auditRef]) : null;
  const first = (rows) => (activeRefs ? rows.slice().sort((a, b) => Number(activeRefs.has(b.ref)) - Number(activeRefs.has(a.ref))) : rows);
  const cited = first(r.evidence.filter((x) => x.cited));
  const rest = first(r.evidence.filter((x) => !x.cited));
  right.append(h("h4", { text: `模型引用的证据（${cited.length} 项）` }));
  for (const row of cited) right.append(evidenceCard(row, activeRefs, r.labels));
  if (rest.length) {
    const open = (activeRefs && rest.some((x) => activeRefs.has(x.ref))) || state.open.has("uncited");
    const more = keepOpen(h("details", { class: "uncited", open }, h("summary", { text: `收到但未引用（${rest.length} 项）` })), "uncited");
    for (const row of rest) more.append(evidenceCard(row, activeRefs, r.labels));
    right.append(more);
  }
}

/** The question sheet tab: the card's contract, or the one pinned from the audit step. */
function renderInquiryAudit(box) {
  const m = state.sides[state.focus];
  const card = m?.snap?.contract || null;
  const pin = state.iq.pin && state.iq.pin.side === state.focus ? state.iq.pin : null;
  const contract = pin?.contract || card;
  const key = contractKey(contract);
  if (!key) { box.append(h("p", { class: "empty pad", text: "这一侧当前没有保单，没有可显示的提问单。" })); return; }
  const tokenOf = (c) => `${String(c?.short_instrument || "").split("-")[1] || ""} 卖 ${usd(c?.short_strike_usd)} / 买 ${usd(c?.long_strike_usd)}`;
  let pinNote = null;
  if (pin && contractKey(card) !== key) {
    pinNote = h("p", { class: "banner warn" }, `这是核验中的合约 ${tokenOf(pin.contract)}${card ? `；保单卡已换为 ${tokenOf(card)}` : ""}。`,
      card ? h("button", { class: "btn secondary", type: "button", onclick: () => { state.iq.pin = null; state.iq.q = null; state.iq.ref = null; renderAudit(); } },
        "回到卡上合约") : null);
  }
  // Anchoring guard: a verification of this contract still before its audit step keeps the sheet folded.
  // (remembered per verification: a later verification of the same contract folds it again)
  const dps = state.dp.session;
  const loaded = dps && contractKey(dps.contract) === key ? dps : null;
  const elsewhere = !loaded && m?.snap?.candidate_id && contractKey(card) === key
    ? get(state.view, `decision_path.open.${m.snap.candidate_id}`) : null;
  const vid = loaded?.verification_id || elsewhere?.verification_id || null;
  const before = (loaded && loaded.status === "open" && !("AU" in (loaded.opened || {}))) || !!elsewhere;
  if (before && vid && !state.iq.reveal.has(vid)) {
    box.append(h("div", { class: "pad" }, pinNote,
      h("p", { class: "muted", text: "这张合约的核验正在进行，还没到审计步。先读完 D0–Q2 再看提问单，可避免被模型的假设锚定。" }),
      h("button", { class: "btn secondary", type: "button", onclick: () => { state.iq.reveal.add(vid); renderAudit(); } }, "仍然展开")));
    return;
  }
  const opts = { file: state.iq.file[key] || null };
  const got = iqEntry(contract, opts);
  ensureInquiry(contract, opts);
  // while another sheet of the same contract loads, the one on screen stays (no jump in the page)
  const data = got?.data || state.iq.last[key] || null;
  if (got?.data) state.iq.last[key] = got.data;
  if (!data) {
    box.append(h("div", { class: "pad" }, pinNote, h("p", { class: "muted", text: got?.error ? `提问单读取失败：${got.error}` : "正在读取提问单…" })));
    return;
  }
  const { left, right } = inquiryAudit(data, {
    pinNote, activeQuestion: state.iq.q, activeRef: state.iq.ref,
    anomaliesOpen: state.open.has("iq:anomalies"), restOpen: state.open.has("iq:rest"),
    onToggle: (part, open) => { if (open) state.open.add(`iq:${part}`); else state.open.delete(`iq:${part}`); },
    onQuestion: (id) => { state.iq.q = state.iq.q === id ? null : id; state.iq.ref = null; renderAudit(); },
    onRef: (ref) => { state.iq.ref = state.iq.ref === ref ? null : ref; state.iq.q = null; renderAudit(); scrollToRef(ref); },
    onPick: (file) => { state.iq.file[key] = file; state.iq.q = null; state.iq.ref = null; renderAudit(); },
  });
  box.append(h("div", { class: "audit-main" }, left, right));
}

/** Program reading when no model receipt applies: guidance notes, then the two questions in full. */
function programAudit(m) {
  const box = h("div", { class: "program" });
  const notes = get(m, "candidate.reading_guidance.notes") || [];
  const NOTE = { liability_direction: "责任方向", compensation_reference: "补偿口径", risk_reference_gap: "风险参考", edb_conflict: "方向意见" };
  if (notes.length) {
    box.append(h("h4", { text: "程序读法" }));
    box.append(h("dl", { class: "notes" }, ...notes.flatMap((n) => [h("dt", { text: NOTE[n.kind] || "说明" }), h("dd", { text: humanize(n.text_cn) })])));
  }
  // The program's two-question readings live in the Decision Path steps (D1–Q2); WPEM no
  // longer runs on every refresh, so they are not repeated here.
  box.append(h("p", { class: "muted small" }, "两问的程序读数见核验路径各步（D1 责任至 Q2 风险补偿）。",
    h("a", { href: "#decision-h", class: "dp-jump", onclick: (e) => { e.preventDefault(); $("decision-h").scrollIntoView({ behavior: "smooth", block: "start" }); } }, "核验路径")));
  return box;
}

/** The program guardrails that frame a receipt: its own summary, supports, counters, when to reassess. */
function guardrails(rv, labels) {
  const box = h("div", { class: "guardrails" });
  const list = (title, items, cls) => {
    const xs = (items || []).map((x) => (typeof x === "string" ? x : x && x.claim_cn)).filter(Boolean);
    if (xs.length) box.append(h("h4", { text: title }), h("ul", { class: `q ${cls}` }, ...xs.map((t) => h("li", { text: humanize(t, labels) }))));
  };
  list("支持", rv.supports, "pro");
  list("反证", rv.counters, "con");
  list("何时重新评估", rv.reassess_if_cn, "");
  if (box.childNodes.length) box.prepend(h("p", { class: "muted small", text: "以下由程序护栏选出，模型在其范围内解释。" }));
  return box;
}

function evidenceLinks(m) {
  const links = [
    [m.candidate.evidence_url, "当前快照的程序证据（JSON）"],
    [get(m, "candidate.review_validity.public_evidence_url"), "时间证据（JSON）"],
  ].filter(([u]) => typeof u === "string" && u.startsWith("/api/workbench/"));
  return h("p", { class: "links" }, ...links.flatMap(([u, t], i) => [i ? " · " : "", h("a", { href: u, target: "_blank", rel: "noopener", text: t })]));
}

function scrollToRef(ref) {
  requestAnimationFrame(() => document.querySelector(`[data-ref="${CSS.escape(ref)}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
}

function evidenceCard(row, activeRefs, labels) {
  const p = row.provided || {};
  const ref = get(row, "lineage.ref") || p.ref;
  const cls = activeRefs ? (activeRefs.has(ref) ? " hl" : " cold") : "";
  const card = h("div", { class: `ev${cls}`, dataset: { ref } },
    h("header", {}, h("b", { text: evidenceName(ref, labels) }), h("code", { text: ref })));
  if (p.basis) card.append(h("div", { class: "basis", text: humanize(p.basis, labels) }));
  // Only named fields are listed; the rest stay in the raw evidence below, never as bare field names.
  const kv = h("dl", { class: "kv" });
  let unnamed = 0;
  const addRows = (pairs, limit) => {
    let shown = 0;
    for (const [k, v] of pairs) {
      const name = fieldName(k);
      if (!name) { unnamed += 1; continue; }
      if (shown >= limit) continue;
      kv.append(h("dt", { title: k, text: name }), factValue(v));
      shown += 1;
    }
  };
  addRows(flatFacts(p.facts), 14);
  const rel = flatFacts(p.relations);
  if (rel.some(([k]) => fieldName(k))) kv.append(h("dt", { class: "sub", text: "程序关系" }));
  addRows(rel, 10);
  card.append(kv);
  if (unnamed) card.append(h("div", { class: "basis muted", text: `另有 ${unnamed} 项细节字段，见下方原始证据。` }));
  for (const lim of p.limitations || []) card.append(h("div", { class: "basis", text: `限制：${humanize(lim, labels)}` }));
  card.append(keepOpen(h("details", { class: "raw", open: state.open.has(`raw:${ref}`) }, h("summary", { text: "原始证据与来源路径" }),
    h("pre", { text: JSON.stringify({ lineage: row.lineage, ...p }, null, 1) })), `raw:${ref}`));
  return card;
}

/** When the shown receipt was produced. */
function reviewedAt(r) {
  return get(r, "binding.reviewed_at_ms") ?? get(r, "review.decision_asof_ms") ?? get(r, "receipt.review.decision_asof_ms");
}

/** Remember which <details> the reader opened, across 30 s refreshes. */
function keepOpen(el, key) {
  el.addEventListener("toggle", () => { if (el.open) state.open.add(key); else state.open.delete(key); });
  return el;
}

const isPosition = (v) => v && typeof v === "object" && isNum(v.price) && "liability_zone" in v;
const posText = (v) => `${usd(v.price)}，${codeCn(v.liability_zone)}${v.on_adverse_side_of_spot ? "，在不利方向一侧" : ""}`;

function flatFacts(obj, prefix = "", out = [], depth = 0) {
  if (!obj || typeof obj !== "object") return out;
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (isPosition(v)) out.push([key, posText(v)]);
    else if (Array.isArray(v) && v.length && v.every(isPosition)) out.push([key, v.map(posText).join("；")]);
    else if (v && typeof v === "object" && !Array.isArray(v) && depth < 2) flatFacts(v, key, out, depth + 1);
    else out.push([key, fmtVal(v, k)]);
  }
  return out;
}

function fmtVal(v, key = "") {
  if (v == null) return "缺失";
  if (typeof v === "boolean") return v ? "是" : "否";
  if (typeof v === "number") {
    if (/_ms$/.test(key)) return v > 1e12 ? clock(v) : dur(v);
    if (/sigma/.test(key) && !/move/.test(key)) return `约为预期波动的 ${(v * 100).toFixed(0)}%`;
    if (/_pct|pct$|percentile/.test(key)) return pct(v, 2);
    if (/to_width|ratio|share/.test(key)) return ratioPct(v);
    if (key === "quantity_btc") return `${v} BTC`;
    if (/_btc$/.test(key)) return btc(v, 5);
    if (/hours/.test(key)) return `${v.toFixed(1)} 小时`;
    if (/_usd$|price|strike|_flow/.test(key) || Math.abs(v) >= 1000) return usd(v, Math.abs(v) >= 1000 ? 0 : 2);
    return String(Math.round(v * 1e4) / 1e4);
  }
  if (Array.isArray(v)) {
    if (!v.length) return "无";
    if (v.every((x) => typeof x === "string" && /^[0-9a-f]{16,}$/.test(x))) return `${v.length} 个编号`;
    if (v.every((x) => typeof x !== "object")) return v.map((x) => (typeof x === "string" ? codeCn(x) : fmtVal(x, key))).join("，");
    return `${v.length} 项`;
  }
  if (typeof v === "object") return "见原始证据";
  return codeCn(v);
}

const RAW_CODE = /^[A-Za-z][A-Za-z0-9]*(?:[_.][A-Za-z0-9]+)+$|^[A-Z][A-Z0-9]{2,}$|^[0-9a-f]{16,}$/;

function factValue(v) {
  if (typeof v === "string" && RAW_CODE.test(v) && !termName(v)) return h("dd", {}, h("code", { class: "val", text: v }));
  return h("dd", { text: humanize(v, state.labels) });
}

function traceView(r) {
  const layers = get(r, "trace.layers") || [];
  if (!layers.length) return h("div");
  const box = h("div", { class: "chain" }, h("h4", { text: "推理链路：每层证据里模型引用了什么、略过了什么" }));
  for (const l of layers) {
    const used = l.consumed_refs || [];
    const skipped = (l.omitted || []).map((o) => o.ref);
    box.append(h("div", { class: "chain-row" },
      h("div", { class: "chain-layer" }, h("b", { text: l.title_cn || "未命名层" }), h("span", { text: `引用 ${used.length}，略过 ${skipped.length}` })),
      h("div", { class: "refs" }, ...used.map((x) => refChip(x, "ok")), ...skipped.map((x) => refChip(x, "skip")))));
  }
  return box;
}

function refChip(ref, cls) {
  return h("button", { class: `chip ${cls}${state.auditRef === ref ? " on" : ""}`, type: "button",
    title: `${cls === "skip" ? "模型收到但未引用" : "模型引用"}：${ref}`,
    onclick: () => { state.auditRef = state.auditRef === ref ? null : ref; state.auditPoint = null; renderAudit(); scrollToRef(ref); } }, evidenceName(ref, state.labels));
}

function metaView(m, r) {
  const rv = r.review || {};
  const rec = r.receipt || {};
  const dl = h("dl", { class: "meta-grid" });
  const rows = [
    ["校验", statusCn(r.status)],
    ["绑定", r.binding ? `${statusCn(r.binding.selector_status)}${r.binding.same_input ? "，输入一致" : ""}` : "—"],
    ["复核时间", clock(get(r, "binding.reviewed_at_ms") ?? rv.decision_asof_ms)],
    ["模型", `${rv.requested_model || "—"}${rv.response_model && rv.response_model !== rv.requested_model ? ` → ${rv.response_model}` : ""}`],
    ["用途", rec.purpose === "observation" ? "观察研究，不附着生产" : rec.purpose === "review" ? "生产复核" : "未记录"],
    ["快照", (rv.reviewed_snapshot_id || rec.snapshot_id || "").slice(0, 16) || "—"],
  ];
  for (const [k, v] of rows) dl.append(h("dt", { text: k }), h("dd", { text: v }));
  const gaps = rv.gaps_cn || [];
  const box = h("div", {}, h("h4", { text: "回执身份" }), dl, evidenceLinks(m));
  if (gaps.length) box.append(h("h4", { text: "程序标注的资料缺口" }), h("ul", { class: "q" }, ...gaps.map((g) => h("li", { text: humanize(g, r.labels) }))));
  return box;
}

// ── lock tracking ────────────────────────────────
function openEpisode() {
  return (get(state.view, "locked_candidates.episodes") || []).find((e) => e.episode_id === state.lock.id) || null;
}

function renderLocks() {
  const box = clear($("locks"));
  if (!state.view) return;
  const list = get(state.view, "locked_candidates.episodes") || [];
  const sum = $("lock-sum");
  const active = list.filter((e) => e.status === "active").length;
  sum.textContent = list.length
    ? `${active} 条追踪中，共 ${list.length} 条（默认显示最近 ${LOCK_LIST_LIMIT} 条）。每条追踪到该期期权交割为止；点击一行展开锁定时与之后的变化。`
    : "锁定后按同一合同持续采样到该期期权交割，比较锁定时与现在";
  if (!list.length) { box.append(h("p", { class: "empty pad", text: "还没有锁定的合同。核验结论选“锁定追踪”，或在保单卡上直接锁定。" })); return; }
  const L = state.lock;
  box.append(h("div", { class: "pad" }, lockList(list, {
    selected: L.id,
    showAll: !!L.showAll,
    onToggleAll: () => { L.showAll = !L.showAll; renderLocks(); },
    onOpen: (id) => { if (L.id === id) { L.id = null; renderLocks(); } else { L.id = id; L.confirm = null; renderLocks(); fetchLock(id); } },
    renderDetail: (e) => lockDetail(L.details[e.episode_id], {
      loading: L.loading === e.episode_id, error: L.errors[e.episode_id], session: state.session.authenticated && !FIXTURE,
      series: L.series[e.episode_id], seriesError: L.seriesErrors[e.episode_id],
      confirm: L.confirm, refresh: get(state.view, "refresh_timing"),
      onAskStop: () => { L.confirm = "stop"; renderLocks(); },
      onCancel: () => { L.confirm = null; renderLocks(); },
      onStop: () => stopLock(e.episode_id),
      onHoldReview: () => holdReview(e.episode_id), holdBusy: !!(L.holdBusy || {})[e.episode_id],
      onRelock: (ask) => { if (ask === "ask") { L.confirm = "relock"; renderLocks(); } else relock(e); },
    }),
  })));
}

/** Fetch one lock detail; a newer request for any lock supersedes older responses. */
async function fetchLock(id, { quiet = false } = {}) {
  const L = state.lock;
  if (FIXTURE) { L.errors[id] = "示例模式不读取锁定明细"; renderLocks(); return; }
  const seq = ++L.seq;
  if (!quiet || !L.details[id]) { L.loading = id; renderLocks(); }
  const [detail, series] = await Promise.allSettled([api.lockedDetail(id), api.lockedSeries(id)]);
  if (seq !== L.seq) return;  // a newer request (for any lock) supersedes this one
  L.loading = null;
  if (detail.status === "fulfilled") { L.details[id] = detail.value; delete L.errors[id]; }
  else { authFailed(detail.reason); L.errors[id] = errorText(detail.reason); }
  if (series.status === "fulfilled") { L.series[id] = series.value; delete L.seriesErrors[id]; }
  else L.seriesErrors[id] = errorText(series.reason);
  if (L.id === id) renderLocks();
}

async function stopLock(id) {
  state.lock.confirm = null;
  try { await api.post(`/locked-candidates/${encodeURIComponent(id)}/stop`, {}); notice("已停止追踪。"); }
  catch (err) { authFailed(err); notice(`停止失败：${errorText(err)}`); }
  await load(true); await fetchLock(id);
}

async function holdReview(id) {
  const L = state.lock;
  L.holdBusy = { ...(L.holdBusy || {}), [id]: true }; renderLocks();
  try {
    const res = await api.post(`/locked-candidates/${encodeURIComponent(id)}/hold-review`,
      { idempotency_key: idempotencyKey("ui-hold") });
    if (res.status !== "OK") notice(res.status === "BUDGET_EXHAUSTED" ? "今日模型额度已用完。"
      : res.status === "INVALID_RESPONSE" ? "模型回答未通过校验，未采用。" : "持有中复核未完成。");
  } catch (err) { authFailed(err); notice(`持有中复核失败：${errorText(err)}`); }
  const busy = { ...(L.holdBusy || {}) }; delete busy[id]; L.holdBusy = busy;
  await fetchLock(id, { quiet: true });
}

async function relock(e) {
  state.lock.confirm = null;
  try {
    const res = await api.post("/locked-candidates", { candidate_id: e.candidate_id, snapshot_id: e.latest_snapshot_id,
      idempotency_key: idempotencyKey("ui-relock"), new_reference: true, source_episode_id: e.episode_id });
    notice("已开始新的追踪。");
    state.lock.id = get(res, "episode.episode_id") || state.lock.id;
  } catch (err) { authFailed(err); notice(`重新追踪失败：${errorText(err)}`); }
  await load(true);
  if (state.lock.id) fetchLock(state.lock.id);
}

async function loadAudit() {
  state.auditAt = Date.now();
  try { state.audit = await api.chainAudit(); } catch (err) { state.audit = { error: errorText(err) }; }
  renderAuditLog();
}

/** The main-chain audit log: files by day, record counts, hash-chain status, download links. */
function renderAuditLog() {
  const box = $("audit-log");
  if (!box) return;
  clear(box);
  const a = state.audit;
  box.append(h("h4", { text: "主链路审计日志" }));
  if (!a) { box.append(h("p", { class: "muted", text: FIXTURE ? "示例模式不读取审计日志。" : "正在读取…" })); return; }
  if (a.error) { box.append(h("p", { class: "muted", text: `读取失败：${reasonCn(a.error)}` })); return; }
  box.append(h("p", { class: "muted small", text: `每 ${dur(a.interval_ms)}一条周期快照，另记锚点效用变迁、复核、锁定与停止；按北京时间分日存放，保留 ${a.retention_days} 天，每日 23:00 快照长期保留。每个文件开头附中文字段说明，可直接交给模型阅读。${a.last_error ? ` 最近一次写入失败（${a.last_error}）。` : ""}` }));
  const t = h("table", { class: "t compact" }, h("thead", {}, h("tr", {}, ...["文件", "记录", "其中事件", "哈希链", "时间范围"].map((x) => h("th", { text: x })))));
  const tb = h("tbody");
  for (const f of (a.files || []).slice(0, 10)) {
    const events = Object.entries(f.kinds || {}).filter(([k]) => !["header", "periodic", "daily"].includes(k)).reduce((n, [, c]) => n + c, 0);
    tb.append(h("tr", {},
      h("td", {}, h("a", { href: `/api/workbench/chain-audit/${encodeURIComponent(f.name)}`, target: "_blank", rel: "noopener", title: f.name,
        text: auditFileName(f.name) })),
      h("td", { class: "n", text: String(f.records) }), h("td", { class: "n", text: String(events) }),
      h("td", {}, chip(f.chain_ok ? "完整" : "断裂", f.chain_ok ? "ok" : "bad")),
      h("td", { class: "small", text: `${clock(f.first_ts_ms)} – ${clock(f.last_ts_ms)}` })));
  }
  t.append(tb);
  box.append((a.files || []).length ? h("div", { class: "scroll-x" }, t) : h("p", { class: "muted", text: "还没有记录；后台下一轮刷新后写入第一条。" }));
}

/** chain-20261013.jsonl → 2026-10-13 日志；chain-daily.jsonl → 每日快照. */
function auditFileName(name) {
  const day = /^chain-(\d{4})(\d{2})(\d{2})\.jsonl$/.exec(name || "");
  if (day) return `${day[1]}-${day[2]}-${day[3]} 日志`;
  if (name === "chain-daily.jsonl") return "每日 23:00 快照（长期保留）";
  return name;
}

function notice(text) { state.notices.push({ text, until: Date.now() + 60_000 }); renderBanners(); }

function authFailed(err) {
  if (err && err.status === 401) { state.session = { authenticated: false }; renderSession(); }
}

// ── exogenous events ─────────────────────────────
function renderEvents() {
  const box = clear($("events"));
  if (!state.view) return;
  try {
    box.append(eventBand(state.view));
  } catch {  // a display module must never take the rest of the page down
    box.append(h("p", { class: "empty", text: "事件日历暂不可用。" }));
  }
}

// ── market background ────────────────────────────
function renderContext() {
  const box = clear($("context"));
  if (!state.view) return;
  // Three rows, each read on its own: derivatives positioning, holders and allocation, outside markets.
  const GROUPS = [["derivs", "衍生品与资金"], ["holders", "持有者与配置"], ["macro", "宏观与跨资产"]];
  const items = backgroundItems(state.view, currentSpot());
  for (const [key, label] of GROUPS) {
    const own = items.filter((it) => (it.group || "macro") === key);
    if (!own.length) continue;
    const grid = h("div", { class: "grid-3 ctx-row" });
    for (const it of own) {
      const card = h("div", { class: `sheet pad ctx${it.stale ? " stale" : ""}${it.span ? " span2" : ""}` },
        h("div", { class: "ctx-head" }, h("h4", { text: it.title }), h("div", { class: "reading", text: it.state ?? "缺口" })));
      if (it.source) card.append(h("p", { class: "ctx-source", text: `来源：${it.source}` }));
      if (it.stale) card.append(h("p", { class: "stale-note", text: it.stale }));
      if (it.wide) card.append(h("div", { class: "wide-viz" }, it.wide));
      card.append(metricRows(it.rows));
      if (it.note) card.append(h("details", { class: "ctx-note" }, h("summary", { text: "口径说明" }), h("p", { text: it.note })));
      grid.append(card);
    }
    box.append(h("section", { class: "ctx-group" }, h("h3", { class: "ctx-group-title", text: label }), grid));
  }
}

// ── records ──────────────────────────────────────
const KIND_CN = {
  continuous: "工作台持续采集", map_service: "地图数据服务采集（本机，约每 5 分钟）",
  on_demand: "按需记录（复核或锁定时写入）", fmz: "由 FMZ 采集，经 FMZ 事实消费",
  idle: "已登记、未消费（未接入或无来源）",
};
const KIND_NOTE = {
  fmz: "这些原始行情在 FMZ 策略内部采集和计算，工作台不直接读取；量价路径、主动成交、TMV 等读数随 FMZ 事实送达，时效看 FMZ 事实本身。",
  idle: "登记在数据目录中，但当前没有进入任何读数或判断。",
};
const GATE_STATE_CN = { STRUCTURE_BLOCKED: "关闭", STRUCTURE_ELIGIBLE: "放行" };
// anchor-utility transitions; older rows recorded the producer gate (anchor rule)
const ANCHOR_STATE_CN = { ANCHOR_EFFECTIVE: "锚点有效", ANCHOR_INEFFECTIVE: "锚点失效" };
const RUNTIME_CN = {
  USER_DEPLOYED_NATIVE_OBSERVED: "已部署，原生观察", NATIVE_OBSERVED: "原生观察", DEPLOYED_SEPARATE_DATA_SERVICE: "独立数据服务",
  LOCAL_CANDIDATE: "候选版本", LOCAL_NOT_DEPLOYED: "清单记为未部署", LOCAL_WPEM_PROFILE_NOT_DEPLOYED: "清单记为未部署",
  UNCHANGED_FINANCIAL_RULES: "金融规则未变", PRESERVED_NOT_PROMOTED: "保留，未启用", PRESERVED_DEFAULT_LEGACY_ENTRY: "保留的旧入口",
  PRESERVED: "保留", REFERENCE_BLOCKED: "参考受阻", REFERENCE_BLOCKED_NO_TRAINING: "参考受阻（无训练）",
  EXISTING_RUNTIME_UNCHANGED: "沿用现有运行时", PRODUCER_MANIFEST_DIAGNOSIS_RETAINED: "保留诊断", REMOTE_TWO_READERS_COMPAT_PATCH_APPLIED: "兼容补丁已应用",
};

function renderRecords() {
  const box = clear($("records"));
  if (!state.view) return;
  const v = state.view;
  const src = sourceHealth(v);
  const left = h("div", { class: "sheet pad" }, h("h4", { text: "数据来源时效" }));
  // FMZ-collected rows are consumed through FMZ's facts, so they carry the facts' own status and age.
  const fmzFact = src.find((p) => p.id === "fmz.bridge.snapshots.v1");
  for (const kind of ["continuous", "map_service", "on_demand", "fmz", "idle"]) {
    const rows = src.filter((p) => p.kind === kind);
    if (!rows.length) continue;
    const t = h("table", { class: "t compact" }, h("thead", {}, h("tr", {}, ...["来源", "消费情况", "距今", "时效", "用于"].map((x) => h("th", { text: x })))));
    const tb = h("tbody");
    for (const p of rows) {
      const collected = kind === "continuous" || kind === "map_service";
      const viaFmz = kind === "fmz";
      const level = viaFmz ? fmzFact?.level : collected ? p.level : null;
      const text = collected ? sourceStatusCn(p.status) : kind === "on_demand" ? "按需写入"
        : viaFmz ? (fmzFact?.level === "ok" ? "经 FMZ 消费中" : "FMZ 事实异常") : "未消费";
      const tone = level === "ok" ? "ok" : level === "warn" ? "warn" : level === "bad" ? "bad" : "";
      const age = viaFmz ? fmzFact?.age : p.age;
      tb.append(h("tr", { class: collected || viaFmz ? "" : "muted-row",
          title: p.idleReason || p.reasons.map(reasonCn).join("；") },
        h("td", { class: "src-id" }, h("span", { text: SOURCE_CN[p.id] || p.id }), SOURCE_CN[p.id] ? h("code", { class: "id", text: p.id }) : null),
        h("td", {}, chip(text, tone), p.idleReason ? h("small", { class: "idle-why", text: p.idleReason }) : null),
        h("td", { class: "n", text: isNum(age) ? dur(age) : "—" }), h("td", { class: "n", text: viaFmz || kind === "idle" ? "—" : isNum(p.ttl) ? dur(p.ttl) : "—" }),
        h("td", { class: "small", text: [...new Set(p.consumers.map(consumerCn))].slice(0, 3).join("、") || "—" })));
    }
    t.append(tb);
    const det = keepOpen(h("details", { class: "src-group", open: kind === "continuous" || kind === "map_service" || state.open.has(`src:${kind}`) },
      h("summary", { text: `${KIND_CN[kind]}（${rows.length}）` }),
      KIND_NOTE[kind] ? h("p", { class: "muted small", text: KIND_NOTE[kind] }) : null,
      h("div", { class: "scroll-x" }, t)), `src:${kind}`);
    left.append(det);
  }
  box.append(left);

  const right = h("div", { class: "sheet pad" });
  const gate = v.space_gate || {};
  right.append(h("h4", { text: "空间结构（评价）" }),
    h("p", { class: "muted", text: `只作评价，不作准入：锚点 ${isNum(get(gate, "anchor.score")) ? `${gate.anchor.score.toFixed(1)} / 100` : "缺失"}，净 Gamma ${isNum(get(gate, "net_gamma.value")) ? compactUsd(gate.net_gamma.value) : "缺失"}${isNum(get(gate, "net_gamma.age_ms")) ? `（${dur(gate.net_gamma.age_ms)}前）` : ""}；FMZ 原生空间门${gate.producer_state === "STRUCTURE_ELIGIBLE" ? "放行" : gate.producer_state ? "关闭" : "缺失"}（仅参考）` }));
  // Anchor-utility rows (since 2026-10-13) and the older gate rows are different things: list them apart.
  const tr = v.transitions || [];
  const anchorRows = tr.filter((x) => x.kind === "anchor_utility");
  const oldRows = tr.filter((x) => x.kind !== "anchor_utility");
  right.append(h("h4", { text: "锚点效用变迁" }), anchorRows.length
    ? h("ul", { class: "q" }, ...anchorRows.slice(-8).reverse().map((x) => h("li", { text: `${clock(x.transition_ts_ms)}  ${ANCHOR_STATE_CN[x.previous_state] || "未知"} → ${ANCHOR_STATE_CN[x.current_state] || "未知"}${isNum(get(x, "anchor.score")) ? `（评分 ${x.anchor.score.toFixed(1)}）` : ""}` })))
    : h("p", { class: "muted", text: "2026-10-13 起记录锚点从有效变为失效或反过来的时刻；目前还没有变化。" }),
    h("p", { class: "muted small", text: "锚点只作参考，它的变化不影响准入。" }));
  if (oldRows.length) right.append(h("details", { class: "old-gate" },
    h("summary", { text: `此前的空间门变迁（旧口径，锚点曾是准入条件，${oldRows.length} 条）` }),
    h("ul", { class: "q" }, ...oldRows.slice(-8).reverse().map((x) => h("li", { text: `${clock(x.transition_ts_ms)}  ${GATE_STATE_CN[x.previous_state] || "未知"} → ${GATE_STATE_CN[x.current_state] || "未知"}` })))));
  const days = v.daily_snapshots || [];
  // evaluation-era days show the anchor score; earlier days kept the old gate state
  const dayGate = (d) => (String(get(d, "space_gate.gate_basis") || "").startsWith("evaluation")
    ? `锚点 ${isNum(get(d, "space_gate.anchor.score")) ? d.space_gate.anchor.score.toFixed(0) : "缺失"}`
    : `空间门${GATE_STATE_CN[get(d, "space_gate.state")] || "未知"}（旧口径）`);
  const dayReview = (c) => (c.review?.status === "not_attached" ? "未复核" : c.review?.status === "attached" ? "已复核" : statusCn(c.review?.status));
  right.append(h("h4", { text: "每日快照（北京时间 23:00）" }), days.length
    ? h("div", {}, h("ul", { class: "q" }, ...days.slice(-7).reverse().map((d) => h("li", { text: `${d.bjt_day}  ${dayGate(d)} · ${(d.candidates || []).map((c) => `${c.side === "put" ? "Put" : "Call"} ${dayReview(c)}`).join(" · ")}` }))),
      h("p", { class: "muted small", text: "“旧口径”指 2026-10-13 之前空间门还是准入条件时的记录；之后空间结构只作评价，这里记锚点评分。" }))
    : h("p", { class: "muted", text: "还没有每日快照。" }));
  const ns = v.notification_state || {};
  right.append(h("h4", { text: "邮件通知" }), h("p", { class: "muted",
    text: (ns.last_status === "not_configured" || get(ns, "last_attempt.status") === "not_configured")
      ? "工作台邮件未配置；通知目前走 FMZ 推送。"
      : get(ns, "last_attempt.attempted_at_ms") ? `最近一次 ${clock(ns.last_attempt.attempted_at_ms)}，${ns.last_attempt.status === "sent" ? "已发送" : ns.last_attempt.status ? "未发送成功" : "状态未记录"}` : "还没有发送过锚点效用变迁通知。" }));
  right.append(scheduleView(v.scheduled_review));
  right.append(versionsView(v));
  box.append(right);
  box.append(h("div", { class: "sheet pad audit-log", id: "audit-log" }));
  renderAuditLog();
}

/** Fixed-time model reviews: when they run, when next, what the last one produced. */
function scheduleView(sr) {
  const box = h("div");
  box.append(h("h4", { text: "定时模型复核" }));
  if (!sr || !(sr.hours_utc || []).length) { box.append(h("p", { class: "muted", text: "未启用定时复核，只有手动观察复核。" })); return box; }
  const hours = sr.hours_utc.map((x) => `UTC ${String(x).padStart(2, "0")}:00（北京 ${String((x + 8) % 24).padStart(2, "0")}:00）`).join("、");
  box.append(h("p", { class: "muted", text: `每天 ${hours}，两侧各复核一次：报价、费用与 FMZ 事实当前时为模型复核，否则为观察复核；另可随时手动观察复核。下一次 ${clock(sr.next_due_ms)}。` }));
  const missed = missedSlot(sr);
  if (missed) box.append(h("p", { class: "muted", text: `${clock(missed)} 那一轮没有运行（服务当时不可用），已错过。` }));
  const last = sr.last;
  if (last) {
    const SIDE = { put: "Put", call: "Call" };
    const text = Object.entries(last.sides || {}).map(([side, o]) => {
      const kind = o.purpose === "review" ? "模型复核" : "观察复核";
      const res = o.status === "OK" || o.status === "RESEARCH_OK" ? "已通过校验"
        // rejected after a model call = the explanation failed the citation/semantic check (kept on file);
        // rejected with no call = the request itself was refused before any model call.
        : o.status === "rejected" && o.llm_call_count > 0 ? "已调用模型，但解释未通过引用与语义核对，原文已留档"
        : o.status === "rejected" ? `未执行（${o.reason ? reasonCn(o.reason) : "原因未记录"}）` : o.status === "skipped" ? "无候选，跳过"
        : o.status === "error" ? `失败（${hasReason(o.reason) ? reasonCn(o.reason) : "程序异常"}）` : statusCn(o.status);
      return `${SIDE[side] || side} ${kind}${res}`;
    }).join("；");
    const SLOT = { running: "（进行中）", interrupted: "（服务重启时中断）", incomplete: "（只因报价等临时原因未完成）" };
    const open = isNum(last.started_at_ms) && Date.now() < last.started_at_ms + (sr.window_ms || 45 * 60_000);
    const retry = last.retries ? "，窗口内已重跑一次"
      : (last.status === "interrupted" || last.status === "incomplete") ? (open ? "，窗口内会自动重跑一次" : "，窗口已过，未能重跑") : "";
    box.append(h("p", { class: "muted", text: `上一轮 ${clock(last.started_at_ms)}${SLOT[last.status] || ""}${retry}：${text || "无结果"}` }));
  } else box.append(h("p", { class: "muted", text: "还没有定时复核记录。" }));
  return box;
}

/** The newest slot whose window has closed without any record (e.g. the service was down). */
function missedSlot(sr) {
  const now = Date.now();
  const window = sr.window_ms || 45 * 60_000;
  for (let back = 0; back < 2; back += 1) {
    for (const hour of [...(sr.hours_utc || [])].sort((a, b) => b - a)) {
      const d = new Date(now - back * 86_400_000);
      const start = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), hour);
      if (start + window > now) continue;
      const key = new Date(start).toISOString().slice(0, 13);
      return sr.last_slot && sr.last_slot >= key ? null : start;
    }
  }
  return null;
}

/** Observed runtime first; the hand-kept release manifest second, with conflicts called out. */
function versionsView(v) {
  const box = h("div");
  const rel = v.release_identity || {};
  const comps = rel.components || [];
  const observed = get(v, "market_context.strategy_version");
  const manifestFmz = comps.find((c) => c.component === "fmz_producer")?.candidate_version;
  box.append(h("h4", { text: "版本身份" }));
  const dl = h("dl", { class: "meta-grid" });
  dl.append(h("dt", { text: "FMZ 运行中" }), h("dd", { text: observed || "未知" }));
  dl.append(h("dt", { text: "读视图" }), h("dd", { title: `${v.schema || "—"} / ${v.display_projection || "—"}`,
    text: v.display_projection === "workbench_page" ? "工作台页面投影（精简读视图）" : "工作台完整读视图" }));
  if (observed && manifestFmz && observed !== manifestFmz) dl.append(h("dt", { text: "注意" }),
    h("dd", { class: "warn-text", text: `发布清单记的 FMZ 是 ${manifestFmz}，与运行中的 ${observed} 不一致；清单需要更新。` }));
  box.append(dl);
  const det = keepOpen(h("details", { class: "src-group", open: state.open.has("versions") },
    h("summary", { text: `发布清单 ${rel.release_id || ""}（${comps.length} 个组件）` })), "versions");
  const t = h("table", { class: "t compact" }, h("thead", {}, h("tr", {}, ...["组件", "版本", "清单状态"].map((x) => h("th", { text: x })))));
  const tb = h("tbody");
  for (const c of comps) tb.append(h("tr", {}, h("td", { class: "src-id" }, h("span", { text: COMPONENT_CN[c.component] || c.component }),
      COMPONENT_CN[c.component] ? h("code", { class: "id", text: c.component }) : null),
    h("td", { class: "small" }, h("code", { text: c.candidate_version || "—" })),
    h("td", { class: "small", text: RUNTIME_CN[c.runtime_status] || "其他" })));
  t.append(tb);
  det.append(h("div", { class: "scroll-x" }, t));
  box.append(det);
  return box;
}

function renderFoot() {
  const v = state.view || {};
  clear($("foot")).append(
    h("span", { text: `更新 ${clock(v.updated_at_ms)}，读取 ${clock(state.loadedAt)}` }),
    h("span", { text: "本页不下单，不给交易许可；模型解释只针对冻结证据。" }));
}

// ── session ──────────────────────────────────────
function renderSession() {
  const btn = $("session-btn");
  btn.textContent = FIXTURE ? "示例模式" : state.session.authenticated ? "已登录，退出" : "登录";
  btn.disabled = FIXTURE;
}

function openLogin(message) {
  $("login-msg").textContent = message;
  if (!$("login").open) $("login").showModal();
  $("login-token").focus();
}

$("session-btn").addEventListener("click", async () => {
  if (state.session.authenticated) {
    try { await api.logout(); } catch {}
    // On the public entry the data was read under this session; leave nothing behind on screen.
    if (SESSION_GATED) { location.reload(); return; }
    await loadSession(); renderSides(); renderLocks(); return;
  }
  openLogin("");
});
$("login-cancel").addEventListener("click", () => $("login").close());

function loginFailure(err) {
  const code = err.body?.code;
  if (err.status === 401 && (!code || code === "unauthorized" || code === "bad_json")) return "令牌不正确。";
  if (err.status === 429) return `登录失败：${reasonCn("rate_limited")}`;
  if (!err.status) return `登录失败：${reasonCn("network_error")}`;
  return `登录失败：${reasonCn(code && code !== "bad_json" ? code : "unknown")}（HTTP ${err.status}）`;
}
$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("login-token");
  const token = input.value.trim();
  input.value = "";
  if (!token) return;
  try {
    await api.login(token);
    $("login").close();
    await loadSession();
    if (SESSION_GATED && !state.view) await load(true);
    renderSides(); renderLocks();
  } catch (err) {
    $("login-msg").textContent = loginFailure(err);
  }
});

// ── theme ────────────────────────────────────────
const THEME_KEY = "astra-theme";
const darkQuery = matchMedia("(prefers-color-scheme: dark)");
const systemTheme = () => (darkQuery.matches ? "dark" : "light");
const effectiveTheme = () => document.documentElement.dataset.theme || systemTheme();

function renderThemeBtn() {
  const dark = effectiveTheme() === "dark";
  const btn = $("theme-btn");
  btn.textContent = dark ? "☀ 浅色" : "☾ 深色";
  btn.title = dark ? "切换到浅色" : "切换到深色";
}

$("theme-btn").addEventListener("click", () => {
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  // Picking the system's own theme goes back to following the system.
  if (next === systemTheme()) delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = next;
  try {
    if (document.documentElement.dataset.theme) localStorage.setItem(THEME_KEY, next);
    else localStorage.removeItem(THEME_KEY);
  } catch { /* the choice then lasts for this page only */ }
  renderThemeBtn();
  if (state.view) renderMapBand();
});
darkQuery.addEventListener("change", renderThemeBtn);
renderThemeBtn();

// ── boot ─────────────────────────────────────────
let resizeTimer = null;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderMapBand, 150); });
$("banners").append(h("div", { class: "banner info" }, "正在读取工作台数据…"));
if (SESSION_GATED) {
  loadSession().then(() => (needsLogin() ? openLogin("") : load()));
} else {
  load();
  loadSession();
}
setInterval(() => { if (!document.hidden) load(); }, POLL_MS);
setInterval(() => { renderStrip(); renderEvents(); renderSessionClock(); }, 10_000);  // countdowns move between data refreshes
setInterval(() => { if (!document.hidden) loadSession(); }, 5 * 60_000);
