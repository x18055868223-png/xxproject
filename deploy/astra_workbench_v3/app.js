// 中性回路承保决策工作台 — reading view renderer and the user actions
// (observation review, lock, stop, re-reference). Everything else is display only.
import { api, idempotencyKey } from "./api.js";
import { h, s, clear, get, isNum, usd, btc, pct, ratioPct, compactUsd, ago, dur, clock } from "./format.js";
import { sideModel, mapModel, sourceHealth, reasonCn, statusCn, applicability, codeCn, underwriting, productionReady,
  consumerCn, sourceStatusCn } from "./model.js";
import { renderMap, legend } from "./map.js";
import { coreFactors, backgroundItems, relationFor } from "./factors.js";
import { lockList, lockDetail } from "./locks.js";

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
  pending: null, error: null, loadedAt: null, busy: false,
  notices: [], open: new Set(), signature: null, reviewPoll: null,
};
const $ = (id) => document.getElementById(id);

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
  } finally {
    state.busy = false;
  }
  if (needsLogin()) { renderBanners(); openLogin("会话已过期，请重新输入工作台令牌。"); return; }
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
  try { state.session = await api.session(); } catch { state.session = { authenticated: false }; }
  if (isNum(state.session.expires_at_ms) && state.session.expires_at_ms <= Date.now()) state.session = { authenticated: false };
  renderSession();
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
  renderStrip();
  renderBanners();
  renderMapBand();
  renderVerdict();
  renderDecks();
  renderFactors();
  renderAudit();
  renderLocks();
  renderContext();
  renderRecords();
  renderFoot();
}

/**
 * How long the gate has been closed. FMZ's own clock restarts with FMZ, so the
 * workbench's records are used too: the last transition into the closed state,
 * or, with none recorded, the run of consecutive closed daily snapshots.
 */
function gateClosed(view) {
  const gate = view?.space_gate || {};
  if (gate.eligible !== false) return null;
  let since = isNum(gate.since_ts_ms) ? gate.since_ts_ms : null, atLeast = false;
  const transitions = view.transitions || [];
  const last = transitions[transitions.length - 1];
  if (last && last.current_state === "STRUCTURE_BLOCKED" && isNum(last.transition_ts_ms)) {
    since = Math.min(since ?? Infinity, last.transition_ts_ms);
  } else if (!transitions.length) {
    const days = (view.daily_snapshots || []).slice().reverse();
    let earliest = null;
    for (const d of days) {
      if (get(d, "space_gate.state") !== "STRUCTURE_BLOCKED") break;
      earliest = d.bjt_day;
    }
    const t = earliest ? Date.parse(`${earliest}T23:00:00+08:00`) : NaN;
    if (isNum(t) && t < (since ?? Infinity)) { since = t; atLeast = true; }
  }
  return isNum(since) ? { since, atLeast, text: `${atLeast ? "至少" : ""}${dur(Date.now() - since)}` } : null;
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
  // A closed gate is the normal resting state, not an alarm: amber, with how long it has been closed.
  setLamp($("gate-state"), gate.eligible ? "ok" : gate.state ? "warn" : "off",
    gate.eligible ? "空间门放行" : gate.state
      ? (gateClosed(v) ? `空间门已关闭 ${gateClosed(v).text}` : "空间门关闭") : "空间门未知");
  $("gate-state").title = gateReasons.join("；");
  const fact = mc.status === "current" ? "ok" : mc.status ? "warn" : "off";
  // background_only: facts are fresh but the space input was rejected, so they only serve as background.
  const factNote = !mc.status || mc.status === "current" ? "" : mc.status === "background_only" ? "，仅作背景" : "，事实不是当前";
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
  $("refreshed").textContent = state.error ? "读取失败" : `更新于 ${ago(v.updated_at_ms)}`;
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
  if (state.error) box.append(h("div", { class: "banner" }, h("b", { text: "读取失败。" }),
    ` ${state.error.message}。${state.view ? "页面保留上一次成功读取的内容。" : ""}`));
  const fail = v.collector_failure;
  if (fail) box.append(h("div", { class: "banner" }, h("b", { text: "后台刷新失败。" }),
    ` ${clock(fail.failed_at_ms)} 的刷新没有完成，候选与地图停留在 ${clock(v.updated_at_ms)}。`));
  const mc = v.market_context || {};
  if (mc.status === "background_only") box.append(h("div", { class: "banner info" }, h("b", { text: "FMZ 事实只作背景。" }),
    ` 事实本身是新的，但空间输入未通过用途检查（${spaceInputWhy(v)}），空间门按关闭处理；四要素与背景读数仍可参考。`));
  else if (mc.status && mc.status !== "current") box.append(h("div", { class: "banner" }, h("b", { text: "FMZ 事实不是当前状态。" }),
    ` ${(mc.gap_reasons || []).length ? `${mc.gap_reasons.map(reasonCn).join("、")}。` : ""}候选与地图只反映最后一次冻结。`));
  const errs = (v.candidates || []).filter((c) => c.status === "error");
  if (errs.length && !FIXTURE) box.append(h("div", { class: "banner" }, h("b", { text: "候选未生成。" }),
    ` ${errs.map((c) => `${c.side === "put" ? "Put" : "Call"}：${reasonCn((get(c, "review_gate.reasons") || [])[0] || "discovery_refresh_error")}`).join("；")}`));
}

/** Which space source the data engine refused, in words. */
function spaceInputWhy(v) {
  const bad = sourceHealth(v).filter((p) => p.kind === "continuous" && p.level !== "ok"
    && p.consumers.some((c) => c === "space_gate" || c === "gamma_regime" || c === "anchor"));
  return bad.length ? bad.map((p) => `${p.id} ${sourceStatusCn(p.status)}`).join("、") : "原因见数据与审计记录";
}

// ── map band ─────────────────────────────────────
function renderMapBand() {
  if (!state.view) return;
  const model = mapModel(state.view, state.sides);
  renderMap($("map"), $("map-tip"), model, { focus: state.focus, onPick: (side) => select(side) });
  legend($("map-legend"));
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

function select(side) {
  if (!state.sides[side]) return;
  state.focus = side;
  state.auditRef = null; state.auditPoint = null;
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
    box.append(h("b", { text: "满足承保前置条件" }), h("span", { text: "空间门放行，两侧快照可用；可做模型复核，是否承保由你按下面两张保单判断。" }));
    return;
  }
  box.className = "verdict blocked";
  box.append(h("b", { text: "本轮不满足承保前置条件" }));
  for (const item of u.shared) {
    box.append(h("span", { class: "why-item", title: item.title || "",
      text: item.key === "space" && gateSince(gate) ? `${item.text}，${gateSince(gate)}` : item.text }));
  }
  box.append(h("span", { class: "then", text: "仍可做观察复核与锁定追踪，用于了解责任与补偿如何变化。" }));
}

// ── four core elements (shared market layer) ────
function currentSpot() {
  return get(state.view, "market_context.current_price_usd") ?? state.sides.put?.spot ?? state.sides.call?.spot;
}

function renderFactors() {
  const box = clear($("factors"));
  if (!state.view) return;
  for (const f of coreFactors(state.view, currentSpot())) {
    const card = h("div", { class: `sheet factor${f.missing ? " missing" : ""}` },
      h("div", { class: "factor-head" }, h("h4", { text: f.title }), h("b", { class: f.headCls || "", text: f.missing ? "数据缺口" : f.headline })));
    card.append(metricRows(f.rows));
    if (f.note) card.append(h("p", { class: "factor-note", text: f.note }));
    box.append(card);
  }
}

/** label | inline chart | value. A gap is a missing value; pending is "not measurable yet". */
function metricRows(rows) {
  const box = h("div", { class: "metrics" });
  for (const r of rows) {
    box.append(h("div", { class: `metric${r.gap ? " gap" : r.pending ? " pending" : ""}` },
      h("span", { class: "m-label", text: r.label }),
      h("span", { class: "m-viz" }, r.viz || ""),
      h("span", { class: "m-value", text: r.value })));
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
  const left = m.remainingMs - (Date.now() - (m.servedAt || Date.now()));
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
  const src = m.review.kind === "current" ? ["模型解释：对应本快照", "ok"]
    : m.review.kind === "historical" ? ["模型解释：仅有历史回执", "warn"] : ["模型解释：未请求", ""];
  card.append(h("div", { class: "deck-head" },
    h("h3", { text: name }),
    h("span", { class: "legs", text: `${(m.shortName || "").split("-")[1] || ""}  卖 ${usd(m.short)} / 买 ${usd(m.long)}，${remainingText(m)}` }),
    h("span", { class: `chip ${src[1]} deck-src`, text: src[0] })));
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
  card.append(h("div", { class: "figures" },
    fig(pct(m.shortOutsidePct, 2), "短腿距现价", tone,
      `${usdOf(m.shortOutsidePct)} 美元，${m.shortOutsidePct >= 0 ? (tone === "bad" ? "贴近责任区" : "在责任区外") : "已进入责任区"}`),
    fig(beValue, "盈亏平衡距现价", "", beSub),
    fig(isNum(m.delta) ? m.delta.toFixed(2) : "—", "短腿 Delta", "",
      isNum(m.delta) ? `按 Delta ${m.deltaTarget ?? 0.35} 附近选约，宽 ${usd(m.width)} 美元` : "选约 Delta 未记录"),
    fig(ratioPct(m.creditToWidth), "可见信用占宽度", "",
      isNum(m.netCredit) ? `净 ${btc(m.netCredit)} BTC ≈ ${usd(m.netCreditUsd)} 美元${m.feeAssumed ? "（费用按假设）" : ""}，${quoteAge}` : quoteAge),
  ));
  card.append(distanceRule(m));
  card.append(sideFactors(m));

  const qa = h("div", { class: "qa" });
  qa.append(question("Q1 风险质量", m.review.kind === "program" ? programLines(m.review.programQ1) : m.review.q1, m.review.kind));
  qa.append(question("Q2 风险价格", m.review.kind === "program" ? programLines(m.review.programQ2) : m.review.q2, m.review.kind));
  card.append(qa);
  card.append(actions(m));
  return card;
}

function fig(value, label, cls = "", sub = "") {
  return h("div", { class: `fig ${cls}` }, h("div", { class: "v", text: value }), h("div", { class: "k", text: label }),
    sub ? h("div", { class: "k", text: sub }) : null);
}

function chip(text, cls = "") { return h("span", { class: `chip ${cls}`, text }); }

function distanceRule(m) {
  // From spot toward this book's adverse side, in % of price; the blue part is
  // the implied expected move, so a short leg inside it is within one move.
  const wrap = h("div", { class: "sigma-rule", title: "从现价朝本单不利方向的距离（%）；蓝色为到期前隐含预期波动范围" });
  if (!isNum(m.shortOutsidePct)) return wrap;
  const move = isNum(m.sigmaPct) ? m.sigmaPct : null;
  const max = Math.max(move ? 2 * move : 0, (m.beOutsidePct || 0) * 1.3, m.shortOutsidePct * 1.3, 1);
  const svg = s("svg", { viewBox: "0 0 400 40", preserveAspectRatio: "none" });
  const X = (v) => 8 + Math.max(0, Math.min(max, v)) / max * 384;
  svg.append(s("rect", { x: 8, y: 16, width: 384, height: 6, rx: 3, fill: "var(--paper-2)" }));
  if (move) svg.append(s("rect", { x: 8, y: 16, width: X(move) - 8, height: 6, rx: 3, fill: "var(--depth-soft)" }));
  svg.append(s("rect", { x: X(Math.max(0, m.shortOutsidePct)), y: 16, width: Math.max(0, X(max) - X(Math.max(0, m.shortOutsidePct))), height: 6, fill: "var(--liability-soft)" }));
  const ticks = move ? [0, move / 2, move, 2 * move] : [0, max / 2, max];
  for (const t of ticks) svg.append(s("text", { x: X(t), y: 36, "text-anchor": t === 2 * move ? "end" : "middle", "font-size": 9, fill: "var(--ink-3)",
    text: t === 0 ? "现价" : move && Math.abs(t - move) < 1e-9 ? `${pct(t, 2)} 预期波动` : pct(t, 2) }));
  svg.append(s("line", { x1: X(m.shortOutsidePct), x2: X(m.shortOutsidePct), y1: 8, y2: 26, stroke: `var(--${m.side})`, "stroke-width": 2.5 }));
  svg.append(s("text", { x: X(m.shortOutsidePct), y: 7, "text-anchor": "middle", "font-size": 9, fill: `var(--${m.side})`, text: "短腿" }));
  if (isNum(m.beOutsidePct)) {
    svg.append(s("line", { x1: X(m.beOutsidePct), x2: X(m.beOutsidePct), y1: 12, y2: 26, stroke: `var(--${m.side})`, "stroke-dasharray": "2 2" }));
  }
  wrap.append(svg);
  return wrap;
}

function sideFactors(m) {
  const box = h("div", { class: "side-factors", title: "方向对照是固定规则：偏多读数对 Put 有利、对 Call 不利；空间看结构门" });
  for (const f of coreFactors(state.view || {}, currentSpot())) {
    const rel = f.key === "space"
      ? { cls: f.missing ? "gap" : f.headCls === "ok" ? "ok" : "warn", text: f.missing ? "缺口" : f.headline }
      : relationFor(f.missing ? null : f.lean, m.side, { background: f.background });
    box.append(h("div", { class: "sf" }, h("span", { text: f.title }), h("span", { class: `chip ${rel.cls}`, text: rel.text })));
  }
  return box;
}

function programLines(q) {
  if (!q) return [];
  return [q.summary_cn, ...(q.routing_observations_cn || []).slice(0, 2)].filter(Boolean).map((t) => ({ text_cn: t, evidence_refs: [] }));
}

function question(title, points, kind) {
  const box = h("div", { class: "q" }, h("h4", { text: title + (kind === "program" ? "（程序摘要，未经模型）" : "") }));
  if (!points.length) { box.append(h("p", { class: "muted", text: "暂无。" })); return box; }
  const ul = h("ul");
  for (const p of points.slice(0, 3)) ul.append(h("li", { text: p.text_cn }));
  box.append(ul);
  return box;
}

function cooldownLeft() {
  const until = get(state.view, "manual_review_state.cooldown_until_ms");
  return isNum(until) ? until - Date.now() : 0;
}

function actions(m) {
  const wrap = h("div", { class: "actions", onclick: (e) => e.stopPropagation() });
  const obs = m.candidate.observation_review || {};
  const lock = m.candidate.lock_availability || {};
  const pending = state.pending && state.pending.side === m.side ? state.pending : null;
  const production = productionReady(m);
  if (pending && pending.stage === "confirm") {
    const b = get(state.view, "manual_review_state.daily_http_budget") || {};
    const text = pending.kind === "review"
      ? (production
        ? `模型复核：用当时报价重新冻结本保单后调用一次模型（今日 ${b.http_calls_used ?? 0}/${b.http_calls_limit ?? "—"}），约 1–2 分钟；回执附着本快照，仍不构成交易许可。`
        : `观察复核：调用一次模型解释本快照（今日 ${b.http_calls_used ?? 0}/${b.http_calls_limit ?? "—"}），约 1–2 分钟；空间门未放行，回执不附着生产。`)
      : "锁定后每分钟采样同一合同，直到到期或手动停止；锁定时与之后的赔付、代价变化在“锁定追踪”里查看。";
    wrap.append(h("span", { class: "confirm", text }),
      h("button", { class: "btn", type: "button", onclick: () => run(pending.kind, m) }, pending.kind === "review" ? "确认复核" : "确认锁定"),
      h("button", { class: "btn secondary", type: "button", onclick: () => { state.pending = null; renderDecks(); } }, "取消"));
    return wrap;
  }
  if (pending && pending.stage === "running") {
    wrap.append(h("span", { class: "confirm", text: pending.kind === "review" ? "模型复核进行中，完成后自动刷新。" : "正在锁定…" }));
    return wrap;
  }
  const needLogin = !state.session.authenticated;
  const reviewOpen = production ? !(get(m, "candidate.review_gate.reasons") || []).some((r) => /cooldown|inflight|budget/.test(r)) : obs.enabled;
  const reviewBtn = h("button", { class: "btn", type: "button", disabled: !reviewOpen || needLogin || FIXTURE,
    title: production ? "空间门放行：回执附着本快照，仍不构成交易许可" : "观察用途：模型只解释冻结快照，回执不附着生产",
    onclick: () => { state.pending = { side: m.side, kind: "review", stage: "confirm" }; renderDecks(); } }, production ? "模型复核" : "观察复核");
  const lockBtn = h("button", { class: "btn secondary", type: "button", disabled: !lock.enabled || needLogin || FIXTURE,
    onclick: () => { state.pending = { side: m.side, kind: "lock", stage: "confirm" }; renderDecks(); } }, "锁定追踪");
  wrap.append(reviewBtn, lockBtn);
  const why = [];
  if (needLogin && !FIXTURE) why.push("登录后可操作");
  const gateReasons = production ? (get(m, "candidate.review_gate.reasons") || []).filter((r) => /cooldown|inflight|budget/.test(r))
    : obs.enabled ? [] : obs.reasons || [];
  for (const r of gateReasons) {
    const left = cooldownLeft();
    why.push((r === "manual_review_cooldown_active" || r === "review_cooldown") && left > 0 ? `复核冷却中，还剩 ${dur(left)}` : reasonCn(r));
  }
  if (!lock.enabled && lock.reason) why.push(`锁定：${reasonCn(lock.reason)}`);
  if (pending && pending.stage === "done") why.unshift(pending.message);
  if (why.length) wrap.append(h("span", { class: "why", text: [...new Set(why)].join("；") }));
  return wrap;
}

async function run(kind, m) {
  state.pending = { side: m.side, kind, stage: "running" };
  renderDecks();
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
    state.pending = { side: m.side, kind, stage: "done", message: `未完成：${err.body?.code ? reasonCn(err.body.code) : err.message}` };
  }
  await load(true);
  if (kind === "lock" && state.lock.id) fetchLock(state.lock.id);
}

// ── reasoning audit ──────────────────────────────
function renderAudit() {
  const sw = clear($("audit-switch"));
  for (const side of ["put", "call"]) {
    const m = state.sides[side];
    sw.append(h("button", { class: `chip${state.focus === side ? " on" : ""}`, type: "button", disabled: !m?.snap,
      onclick: () => select(side) }, side === "put" ? "Put" : "Call"));
  }
  const box = clear($("audit"));
  if (!state.view) return;
  const m = state.sides[state.focus];
  if (!m || !m.snap) { box.append(h("p", { class: "empty pad", text: "没有可审计的候选。" })); return; }
  const r = m.review;
  const left = h("div", { class: "points" });
  const right = h("div", { class: "ledger" });
  box.append(h("div", { class: "audit-main" }, left, right));

  if (r.kind === "program") {
    left.append(h("p", { class: "muted", text: r.reason || "当前快照还没有通过校验的模型解释，下面是程序给出的读法与两问摘要。" }));
    left.append(programAudit(m));
    right.append(h("h4", { text: "证据" }), h("p", { class: "muted", text: "请求观察复核后，这里逐项列出模型收到并引用的证据。当前快照的程序证据可直接查看：" }),
      evidenceLinks(m));
    return;
  }

  if (r.kind === "historical") left.append(h("p", { class: "banner", text: "这是同一候选较早快照的回执，只作参考；当前快照尚未复核。" }));
  const ap = applicability(r.applicability);
  left.append(h("div", { class: "deck-head" }, h("h3", { text: `${m.name} 模型解释` }), h("span", { class: `seal ${ap.cls}`, text: ap.text })));
  for (const [title, pts, q] of [["Q1 风险质量", r.q1, "q1"], ["Q2 风险价格", r.q2, "q2"]]) {
    left.append(h("h4", { text: title }));
    pts.forEach((p, i) => {
      const key = `${q}.${i}`;
      const on = state.auditPoint === key;
      const toggle = () => { state.auditPoint = on ? null : key; state.auditRef = null; renderAudit(); };
      const el = h("div", { class: `point${on ? " on" : ""}`, tabindex: 0, role: "button", "aria-pressed": String(on),
        onclick: toggle, onkeydown: (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === el) { e.preventDefault(); toggle(); } } },
        h("p", { text: p.text_cn }),
        h("div", { class: "refs" }, ...(p.evidence_refs || []).map((ref) => h("button", { class: `chip${state.auditRef === ref ? " on" : ""}`, type: "button",
          onclick: (e) => { e.stopPropagation(); state.auditRef = state.auditRef === ref ? null : ref; state.auditPoint = null; renderAudit(); scrollToRef(ref); } }, ref))));
      left.append(el);
    });
  }
  left.append(guardrails(r.review || {}));
  left.append(metaView(m, r));
  box.append(traceView(r));

  const activeRefs = state.auditPoint
    ? new Set(get(r, state.auditPoint.startsWith("q1") ? "q1" : "q2")[Number(state.auditPoint.split(".")[1])]?.evidence_refs || [])
    : state.auditRef ? new Set([state.auditRef]) : null;
  const first = (rows) => (activeRefs ? rows.slice().sort((a, b) => Number(activeRefs.has(b.ref)) - Number(activeRefs.has(a.ref))) : rows);
  const cited = first(r.evidence.filter((x) => x.cited));
  const rest = first(r.evidence.filter((x) => !x.cited));
  right.append(h("h4", { text: `模型引用的证据（${cited.length} 项）` }));
  for (const row of cited) right.append(evidenceCard(row, activeRefs));
  if (rest.length) {
    const open = (activeRefs && rest.some((x) => activeRefs.has(x.ref))) || state.open.has("uncited");
    const more = keepOpen(h("details", { class: "uncited", open }, h("summary", { text: `收到但未引用（${rest.length} 项）` })), "uncited");
    for (const row of rest) more.append(evidenceCard(row, activeRefs));
    right.append(more);
  }
}

/** Program reading when no model receipt applies: guidance notes, then the two questions in full. */
function programAudit(m) {
  const box = h("div", { class: "program" });
  const notes = get(m, "candidate.reading_guidance.notes") || [];
  const NOTE = { liability_direction: "责任方向", compensation_reference: "补偿口径", risk_reference_gap: "风险参考", edb_conflict: "方向意见" };
  if (notes.length) {
    box.append(h("h4", { text: "程序读法" }));
    box.append(h("dl", { class: "notes" }, ...notes.flatMap((n) => [h("dt", { text: NOTE[n.kind] || "说明" }), h("dd", { text: n.text_cn })])));
  }
  const q = m.review;
  const cq = get(m, "candidate.current_questions") || {};
  for (const [title, body, extra] of [["Q1 风险质量", q.programQ1, ["window_attribution_cn"]],
    ["Q2 风险价格", q.programQ2, ["window_tradeoff_cn", "observable_changes_cn"]]]) {
    if (!body) continue;
    const items = [body.summary_cn, ...(body.routing_observations_cn || []),
      ...extra.flatMap((k) => (Array.isArray(body[k]) ? body[k] : body[k] ? [body[k]] : []))].filter(Boolean);
    box.append(h("h4", { text: `${title}（程序摘要，未经模型）` }), h("ul", { class: "q" }, ...[...new Set(items)].map((t) => h("li", { text: t }))));
  }
  const ws = get(m, "candidate.wpem_summary") || {};
  const path = [ws.path_cn, ws.compensation_cn, ...(ws.breaks_cn || [])].filter(Boolean);
  if (path.length) box.append(h("h4", { text: "同一保单的窗口变化" }), h("ul", { class: "q" }, ...path.map((t) => h("li", { text: t }))));
  if (!notes.length && !cq.q1) box.append(h("p", { class: "muted", text: "程序没有给出摘要。" }));
  return box;
}

/** The program guardrails that frame a receipt: its own summary, supports, counters, when to reassess. */
function guardrails(rv) {
  const box = h("div", { class: "guardrails" });
  const list = (title, items, cls) => {
    const xs = (items || []).map((x) => (typeof x === "string" ? x : x && x.claim_cn)).filter(Boolean);
    if (xs.length) box.append(h("h4", { text: title }), h("ul", { class: `q ${cls}` }, ...xs.map((t) => h("li", { text: t }))));
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

function evidenceCard(row, activeRefs) {
  const p = row.provided || {};
  const ref = get(row, "lineage.ref") || p.ref;
  const cls = activeRefs ? (activeRefs.has(ref) ? " hl" : " cold") : "";
  const card = h("div", { class: `ev${cls}`, dataset: { ref } },
    h("header", {}, h("b", { text: p.label_cn || ref }), h("code", { text: ref })));
  if (p.basis) card.append(h("div", { class: "basis", text: p.basis }));
  const kv = h("dl", { class: "kv" });
  for (const [k, v] of flatFacts(p.facts).slice(0, 14)) kv.append(h("dt", { title: k, text: fieldLabel(k) }), h("dd", { text: v }));
  const rel = flatFacts(p.relations).slice(0, 10);
  if (rel.length) kv.append(h("dt", { class: "sub", text: "程序关系" }));
  for (const [k, v] of rel) kv.append(h("dt", { title: k, text: fieldLabel(k) }), h("dd", { text: v }));
  card.append(kv);
  for (const lim of p.limitations || []) card.append(h("div", { class: "basis", text: `限制：${lim}` }));
  card.append(keepOpen(h("details", { class: "raw", open: state.open.has(`raw:${ref}`) }, h("summary", { text: "原始证据与来源路径" }),
    h("pre", { text: JSON.stringify({ lineage: row.lineage, ...p }, null, 1) })), `raw:${ref}`));
  return card;
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
    if (v.every((x) => typeof x !== "object")) return v.map((x) => (typeof x === "string" ? codeCn(x) : fmtVal(x, key))).join("，");
    return `${v.length} 项`;
  }
  if (typeof v === "object") return "见原始证据";
  return codeCn(v);
}

const FIELD = {
  side: "侧别", short_instrument: "卖出腿", long_instrument: "保护腿", short_strike_usd: "卖出腿行权价", long_strike_usd: "保护腿行权价",
  expiry_ms: "到期", quantity_btc: "数量", remaining_horizon_hours: "剩余期限", adverse_direction_cn: "不利方向", liability_zone: "责任区关系",
  kind: "类型", credit_btc: "可见信用", reference_price_usd: "参考价", reference_source: "参考价来源", executable_or_filled: "可成交或已成交",
  amount_btc: "费用合计", entry_fee_btc: "入场费", delivery_fee_btc: "交割费", basis: "口径", assumption_basis: "假设依据",
  status: "状态", mu_btc: "μ", overall_mu_status: "μ 状态", gap_reasons: "缺口", positive_ev_not_determinable: "正期望不可判定",
  horizon_sigma_move_pct: "到期前隐含预期波动", short_strike_distance_in_sigma: "短腿距离（相对预期波动）", breakeven_distance_in_sigma: "盈亏平衡距离（相对预期波动）",
  credit_to_width_at_spot: "信用占宽度", short_strike_distance_pct_outside_positive: "短腿距离（正值在外）",
  breakeven_distance_pct_outside_positive: "盈亏平衡距离（正值在外）",
  atm_iv_pct: "ATM IV", match: "匹配方式", days_to_expiry: "期限（天）", expiry: "到期", iv_percentile: "IV 分位", dvol: "DVOL",
  put_25d_iv: "25d Put IV", call_25d_iv: "25d Call IV", rr: "风险逆转",
  signed_distance_to_short_pct: "距卖出腿（原始符号）", signed_distance_to_long_pct: "距保护腿（原始符号）", dte_ms: "剩余期限",
  pcr: "成交 PCR", pcr_oi: "持仓 PCR", effective_flip_point: "有效翻转位", reason_codes: "原因码", state: "状态",
  atm_iv_for_horizon: "本期限 IV", rr_25d_front: "前端偏斜", all_expiry_walls: "全期限墙", near_term_walls: "近端墙",
  support_walls: "支撑", resistance_walls: "阻力", magnet_price: "磁吸位", volatility_trigger: "波动触发", max_pain: "最大痛点",
  next_expiry_max_pain: "下一到期痛点", weekly: "周度", zero_dte: "0DTE", walls: "", gex_usd: "GEX",
  dealer_hedging_adverse_scenarios: "不利方向对冲情景", move_to_short_strike_pct: "到短腿所需涨跌", same_expiry_as_contract: "与本单同到期",
  target_expiry: "目标到期", contains_spot: "包含现价", contains_short_strike: "包含卖出腿", contains_long_strike: "包含保护腿",
  low: "下沿", high: "上沿", raw_low: "下沿", raw_high: "上沿", display_center: "中心",
  frozen_quote_not_filled: "冻结报价未成交", covers: "覆盖", exit_policy: "退出方式", settlement_currency: "结算币种",
};
function fieldLabel(key) {
  const parts = key.split(".").map((p) => (FIELD[p] ?? p)).filter(Boolean);
  return parts.join(" ") || key;
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
    title: cls === "skip" ? "模型收到但未引用" : "模型引用",
    onclick: () => { state.auditRef = state.auditRef === ref ? null : ref; state.auditPoint = null; renderAudit(); scrollToRef(ref); } }, ref);
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
  if (gaps.length) box.append(h("h4", { text: "程序标注的资料缺口" }), h("ul", { class: "q" }, ...gaps.map((g) => h("li", { text: g }))));
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
  sum.textContent = list.length ? `${active} 条追踪中，共 ${list.length} 条。点击一行展开锁定时的关键内容与之后的变化。` : "锁定后按同一合同持续采样，比较锁定时与现在";
  if (!list.length) { box.append(h("p", { class: "empty pad", text: "还没有锁定的合同。在候选卡上选择“锁定追踪”开始。" })); return; }
  const L = state.lock;
  box.append(h("div", { class: "pad" }, lockList(list, {
    selected: L.id,
    onOpen: (id) => { if (L.id === id) { L.id = null; renderLocks(); } else { L.id = id; L.confirm = null; renderLocks(); fetchLock(id); } },
    renderDetail: (e) => lockDetail(L.details[e.episode_id], {
      loading: L.loading === e.episode_id, error: L.errors[e.episode_id], session: state.session.authenticated && !FIXTURE,
      series: L.series[e.episode_id], seriesError: L.seriesErrors[e.episode_id],
      confirm: L.confirm,
      onAskStop: () => { L.confirm = "stop"; renderLocks(); },
      onCancel: () => { L.confirm = null; renderLocks(); },
      onStop: () => stopLock(e.episode_id),
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
  else { authFailed(detail.reason); L.errors[id] = detail.reason.body?.code ? reasonCn(detail.reason.body.code) : detail.reason.message; }
  if (series.status === "fulfilled") { L.series[id] = series.value; delete L.seriesErrors[id]; }
  else L.seriesErrors[id] = series.reason.body?.code ? reasonCn(series.reason.body.code) : series.reason.message;
  if (L.id === id) renderLocks();
}

async function stopLock(id) {
  state.lock.confirm = null;
  try { await api.post(`/locked-candidates/${encodeURIComponent(id)}/stop`, {}); notice("已停止追踪。"); }
  catch (err) { authFailed(err); notice(`停止失败：${err.body?.code ? reasonCn(err.body.code) : err.message}`); }
  await load(true); await fetchLock(id);
}

async function relock(e) {
  state.lock.confirm = null;
  try {
    const res = await api.post("/locked-candidates", { candidate_id: e.candidate_id, snapshot_id: e.latest_snapshot_id,
      idempotency_key: idempotencyKey("ui-relock"), new_reference: true, source_episode_id: e.episode_id });
    notice("已开始新的追踪。");
    state.lock.id = get(res, "episode.episode_id") || state.lock.id;
  } catch (err) { authFailed(err); notice(`重新追踪失败：${err.body?.code ? reasonCn(err.body.code) : err.message}`); }
  await load(true);
  if (state.lock.id) fetchLock(state.lock.id);
}

async function loadAudit() {
  state.auditAt = Date.now();
  try { state.audit = await api.chainAudit(); } catch (err) { state.audit = { error: err.body?.code || err.message }; }
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
  box.append(h("p", { class: "muted small", text: `每 ${dur(a.interval_ms)}一条周期快照，另记空间门变迁、复核、锁定与停止；按北京时间分日存放，保留 ${a.retention_days} 天，每日 23:00 快照长期保留。每个文件开头附中文字段说明，可直接交给模型阅读。${a.last_error ? ` 最近一次写入失败（${a.last_error}）。` : ""}` }));
  const t = h("table", { class: "t compact" }, h("thead", {}, h("tr", {}, ...["文件", "记录", "其中事件", "哈希链", "时间范围"].map((x) => h("th", { text: x })))));
  const tb = h("tbody");
  for (const f of (a.files || []).slice(0, 10)) {
    const events = Object.entries(f.kinds || {}).filter(([k]) => !["header", "periodic", "daily"].includes(k)).reduce((n, [, c]) => n + c, 0);
    tb.append(h("tr", {},
      h("td", {}, h("a", { href: `/api/workbench/chain-audit/${encodeURIComponent(f.name)}`, target: "_blank", rel: "noopener", text: f.name })),
      h("td", { class: "n", text: String(f.records) }), h("td", { class: "n", text: String(events) }),
      h("td", {}, chip(f.chain_ok ? "完整" : "断裂", f.chain_ok ? "ok" : "bad")),
      h("td", { class: "small", text: `${clock(f.first_ts_ms)} – ${clock(f.last_ts_ms)}` })));
  }
  t.append(tb);
  box.append((a.files || []).length ? h("div", { class: "scroll-x" }, t) : h("p", { class: "muted", text: "还没有记录；后台下一轮刷新后写入第一条。" }));
}

function notice(text) { state.notices.push({ text, until: Date.now() + 60_000 }); renderBanners(); }

function authFailed(err) {
  if (err && err.status === 401) { state.session = { authenticated: false }; renderSession(); }
}

// ── market background ────────────────────────────
function renderContext() {
  const box = clear($("context"));
  if (!state.view) return;
  for (const it of backgroundItems(state.view, currentSpot())) {
    const card = h("div", { class: `sheet pad ctx${it.stale ? " stale" : ""}` }, h("h4", { text: it.title }),
      h("div", { class: "reading", text: it.state ?? "缺口" }));
    if (it.stale) card.append(h("p", { class: "stale-note", text: it.stale }));
    if (it.wide) card.append(h("div", { class: "wide-viz" }, it.wide));
    card.append(metricRows(it.rows));
    if (it.note) card.append(h("p", { class: "factor-note", text: it.note }));
    box.append(card);
  }
}

// ── records ──────────────────────────────────────
const KIND_CN = { continuous: "持续采集", on_demand: "按需记录（复核或锁定时写入）", elsewhere: "不在本机采集（FMZ 内部消费或尚未接入）", unused: "已登记但不消费" };
const GATE_STATE_CN = { STRUCTURE_BLOCKED: "关闭", STRUCTURE_ELIGIBLE: "放行" };
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
  for (const kind of ["continuous", "on_demand", "elsewhere", "unused"]) {
    const rows = src.filter((p) => p.kind === kind);
    if (!rows.length) continue;
    const t = h("table", { class: "t compact" }, h("thead", {}, h("tr", {}, ...["来源", "状态", "距今", "时效", "用于"].map((x) => h("th", { text: x })))));
    const tb = h("tbody");
    for (const p of rows) {
      const text = kind === "on_demand" ? "按需" : kind === "elsewhere" ? "本机不采集" : kind === "unused" ? "不消费" : sourceStatusCn(p.status);
      const tone = kind !== "continuous" ? "" : p.level === "ok" ? "ok" : p.level === "warn" ? "warn" : p.level === "bad" ? "bad" : "";
      tb.append(h("tr", { class: kind === "continuous" ? "" : "muted-row", title: p.reasons.map(reasonCn).join("；") },
        h("td", { class: "src-id", text: p.id }), h("td", {}, chip(text, tone)),
        h("td", { class: "n", text: isNum(p.age) ? dur(p.age) : "—" }), h("td", { class: "n", text: isNum(p.ttl) ? dur(p.ttl) : "—" }),
        h("td", { class: "small", text: [...new Set(p.consumers.map(consumerCn))].slice(0, 3).join("、") || "—" })));
    }
    t.append(tb);
    const det = keepOpen(h("details", { class: "src-group", open: kind === "continuous" || state.open.has(`src:${kind}`) },
      h("summary", { text: `${KIND_CN[kind]}（${rows.length}）` }), h("div", { class: "scroll-x" }, t)), `src:${kind}`);
    left.append(det);
  }
  box.append(left);

  const right = h("div", { class: "sheet pad" });
  const gate = v.space_gate || {};
  right.append(h("h4", { text: "空间门" }),
    h("p", { class: "muted", text: `${gate.eligible ? "放行" : "关闭"}，自 ${clock(gateClosed(v)?.since ?? gate.since_ts_ms)}${gateClosed(v)?.atLeast ? "（按每日快照推算的下限）" : ""}；锚点 ${isNum(get(gate, "anchor.score")) ? gate.anchor.score.toFixed(1) : "缺失"}，净 Gamma ${isNum(get(gate, "net_gamma.value")) ? compactUsd(gate.net_gamma.value) : "缺失"}${isNum(get(gate, "net_gamma.age_ms")) ? `（${dur(gate.net_gamma.age_ms)}前）` : ""}` }));
  const tr = v.transitions || [];
  right.append(h("h4", { text: "空间门变迁" }), tr.length
    ? h("ul", { class: "q" }, ...tr.slice(-8).reverse().map((x) => h("li", { text: `${clock(x.transition_ts_ms)}  ${GATE_STATE_CN[x.previous_state] || "未知"} → ${GATE_STATE_CN[x.current_state] || "未知"}` })))
    : h("p", { class: "muted", text: "最近没有变迁。" }));
  const days = v.daily_snapshots || [];
  const dayReview = (c) => (c.review?.status === "not_attached" ? "未复核" : c.review?.status === "attached" ? "已复核" : statusCn(c.review?.status));
  right.append(h("h4", { text: "每日快照（北京时间 23:00）" }), days.length
    ? h("ul", { class: "q" }, ...days.slice(-7).reverse().map((d) => h("li", { text: `${d.bjt_day}  空间门${GATE_STATE_CN[get(d, "space_gate.state")] || "未知"}  ${(d.candidates || []).map((c) => `${c.side === "put" ? "Put" : "Call"} ${dayReview(c)}`).join("，")}` })))
    : h("p", { class: "muted", text: "还没有每日快照。" }));
  const ns = v.notification_state || {};
  right.append(h("h4", { text: "邮件通知" }), h("p", { class: "muted",
    text: get(ns, "last_attempt.attempted_at_ms") ? `最近一次 ${clock(ns.last_attempt.attempted_at_ms)}，${ns.last_attempt.status === "sent" ? "已发送" : ns.last_attempt.status ? "未发送成功" : "状态未记录"}` : "还没有发送过空间门变迁通知。" }));
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
  box.append(h("p", { class: "muted", text: `每天 ${hours}，两侧各复核一次：空间门放行时为模型复核，否则为观察复核；另可随时手动观察复核。下一次 ${clock(sr.next_due_ms)}。` }));
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
        : o.status === "error" ? `失败（${reasonCn(o.reason)}）` : statusCn(o.status);
      return `${SIDE[side] || side} ${kind}${res}`;
    }).join("；");
    box.append(h("p", { class: "muted", text: `上一轮 ${clock(last.started_at_ms)}${last.status === "running" ? "（进行中）" : ""}：${text || "无结果"}` }));
  } else box.append(h("p", { class: "muted", text: "还没有定时复核记录。" }));
  return box;
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
  dl.append(h("dt", { text: "读视图" }), h("dd", { text: `${v.schema || "—"} / ${v.display_projection || "—"}` }));
  if (observed && manifestFmz && observed !== manifestFmz) dl.append(h("dt", { text: "注意" }),
    h("dd", { class: "warn-text", text: `发布清单记的 FMZ 是 ${manifestFmz}，与运行中的 ${observed} 不一致；清单需要更新。` }));
  box.append(dl);
  const det = keepOpen(h("details", { class: "src-group", open: state.open.has("versions") },
    h("summary", { text: `发布清单 ${rel.release_id || ""}（${comps.length} 个组件）` })), "versions");
  const t = h("table", { class: "t compact" }, h("thead", {}, h("tr", {}, ...["组件", "版本", "清单状态"].map((x) => h("th", { text: x })))));
  const tb = h("tbody");
  for (const c of comps) tb.append(h("tr", {}, h("td", { class: "src-id", text: c.component }), h("td", { class: "small", text: c.candidate_version || "—" }),
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
    await loadSession(); renderDecks(); renderLocks(); return;
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
    renderDecks(); renderLocks();
  } catch (err) {
    $("login-msg").textContent = loginFailure(err);
  }
});

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
setInterval(() => { renderStrip(); }, 10_000);
setInterval(() => { if (!document.hidden) loadSession(); }, 5 * 60_000);
