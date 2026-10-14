// Lock tracking: the list of locked contracts and, per contract, what it looked
// like when locked (t0), how it has changed since, and how the sampling went.
// The cost of the book (payout at expiry, maximum loss) is read here, after a
// contract is locked, rather than on the candidate cards.
import { h, s, get, isNum, usd, clock, dur } from "./format.js";

const SIDE_CN = { put: "Put", call: "Call" };
const STATUS_CN = { active: "追踪中", expired: "已到期", stopped: "已停止" };
const STOP_CN = {
  expired: "合约到期",
  observation_stopped_not_position_close: "手动停止观察（不是平仓）",
};
const GAP_CN = {
  locked_packet_unavailable: "该时刻没有可用的冻结快照",
  locked_sampling_paused: "采样暂停",
  locked_quote_unavailable: "锁定合约报价不可用",
  locked_capture_failed: "采样失败",
  contract_expired_settlement_unverified: "合约已到期，结算结果未核实",
  close_fee_unavailable: "平仓费不可用",
  close_fee_unknown: "平仓费未知",
  close_fee_missing: "平仓费缺失",
  close_debit_missing: "平仓报价缺失",
  entry_short_missing: "锁定时短腿成交价缺失",
  entry_long_missing: "锁定时长腿成交价缺失",
  short_mark_missing: "短腿标记价缺失",
  long_mark_missing: "长腿标记价缺失",
  quantity_missing: "数量缺失",
  long_depth_insufficient: "保护腿盘口深度不足",
  short_depth_insufficient: "短腿盘口深度不足",
  baseline_entry_after_fee_missing: "锁定时扣费后信用缺失",
  baseline_entry_credit_missing: "锁定时信用缺失",
  baseline_entry_fee_missing: "锁定时入场费缺失",
  quote_clock_missing: "报价时钟缺失",
  risk_identity_missing: "风险参考身份缺失",
};
export const gapCn = (code) => {
  const base = String(code || "").split(":")[0];
  return GAP_CN[base] || (base ? "其他采样问题" : "原因未记录");
};
const ROW_STATUS_CN = {
  changed: "有变化", unchanged: "未变", same_clock: "来源未更新", not_precise: "口径不同，只并列",
  missing: "两端缺失", new_fact: "锁定后才出现", latest_missing: "最新缺失", reference_only: "参考估算", unknown: "无法估算",
};
const TREND_CN = { strong_bullish_bias: "强偏多", bullish_bias: "偏多", neutral: "中性", bearish_bias: "偏空", strong_bearish_bias: "强偏空" };
const FLOW_DIR_CN = { bullish: "主动买方占优", bearish: "主动卖方占优", neutral: "均衡" };
const GAMMA_CN = { POSITIVE_GAMMA_PINNING: "正 Gamma 钉住", NEGATIVE_GAMMA_AMPLIFYING: "负 Gamma 放大", TRANSITION: "翻转位附近过渡", UNKNOWN: "未知" };
const MACRO_CN = { "Mild Tailwind": "温和顺风", "Strong Tailwind": "强顺风", "Mild Headwind": "温和逆风", "Strong Headwind": "强逆风", Neutral: "中性" };
const GATE_CN = { STRUCTURE_BLOCKED: "FMZ 空间门关闭（仅参考）", STRUCTURE_ELIGIBLE: "FMZ 空间门放行（仅参考）" };

const btcText = (v, d = 5) => (isNum(v) ? `${v < 0 ? "−" : ""}${Math.abs(v).toFixed(d).replace(/0+$/, "").replace(/\.$/, "")} BTC` : "—");
const signedBtc = (v, d = 5) => (isNum(v) ? `${v > 0 ? "+" : ""}${btcText(v, d)}` : "—");
const signedPct = (v, d = 2) => (isNum(v) ? `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}%` : "—");

export function contractTitle(c = {}) {
  const side = SIDE_CN[c.side] || "价差";
  return `${side} 卖 ${usd(c.short_strike_usd)} / 买 ${usd(c.long_strike_usd)}`;
}

function episodeState(e) {
  const text = STATUS_CN[e.status] || "状态未知";
  const tone = e.status === "active" ? "ok" : e.status in STATUS_CN ? "" : "bad";
  const why = e.status === "stopped" && e.stop_reason !== "expired" ? STOP_CN[e.stop_reason] || "" : "";
  return { text, tone, why };
}

// ── list ─────────────────────────────────────────
/** Most recent first. By default only the latest `limit` are listed; a tracking (active)
 * contract and the open one are always shown, so a long history never crowds the page. */
export const LOCK_LIST_LIMIT = 5;

export function visibleLocks(episodes, { selected, showAll = false, limit = LOCK_LIST_LIMIT } = {}) {
  const all = episodes.slice().sort((a, b) => (b.created_at_ms || 0) - (a.created_at_ms || 0));
  if (showAll || all.length <= limit) return { rows: all, hidden: 0 };
  const recent = new Set(all.slice(0, limit).map((e) => e.episode_id));
  const rows = all.filter((e) => recent.has(e.episode_id) || e.status === "active" || e.episode_id === selected);
  return { rows, hidden: all.length - rows.length };
}

export function lockList(episodes, { selected, onOpen, renderDetail, showAll = false, onToggleAll }) {
  const { rows, hidden } = visibleLocks(episodes, { selected, showAll });
  const t = h("table", { class: "t locks" }, h("thead", {}, h("tr", {},
    // headers align with their cells: times and counts are right-aligned (td.n)
    ...[["合约"], ["到期", "n"], ["状态"], ["盈亏"], ["锁定于", "n"], ["采样", "n"], ["最近一点", "n"]]
      .map(([x, cls]) => h("th", { class: cls || null, text: x })))));
  const tb = h("tbody");
  for (const e of rows) {
    const c = e.contract || {};
    const st = episodeState(e);
    const open = selected === e.episode_id;
    const tr = h("tr", { class: `lock-row${open ? " sel" : ""}`, tabindex: 0, "aria-expanded": String(open),
      "aria-label": `${contractTitle(c)}，${open ? "收起" : "展开"}锁定对照`,
      onclick: () => onOpen(e.episode_id),
      onkeydown: (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); onOpen(e.episode_id); } } },
      h("td", {}, h("span", { class: `caret${open ? " open" : ""}`, "aria-hidden": "true" }), h("b", { text: contractTitle(c) })),
      h("td", { class: "n", text: clock(c.expiry_ms) }),
      h("td", {}, h("span", { class: `chip ${st.tone}`, text: st.text }), st.why ? h("span", { class: "muted small", text: ` ${st.why}` }) : null),
      h("td", {}, outcomeChip(e.outcome)),
      h("td", { class: "n", text: clock(e.created_at_ms) }),
      h("td", { class: "n", text: `${e.point_count ?? 0} 点${e.gap_count ? `，缺口 ${e.gap_count}` : ""}` }),
      h("td", { class: "n", text: clock(e.latest_decision_asof_ms) }));
    tb.append(tr);
    if (open) tb.append(h("tr", { class: "lock-detail-row" }, h("td", { colspan: 7 }, renderDetail(e))));
  }
  if (onToggleAll && (hidden || showAll) && episodes.length > LOCK_LIST_LIMIT) {
    tb.append(h("tr", { class: "lock-more-row" }, h("td", { colspan: 7 },
      h("button", { class: "link", type: "button", onclick: onToggleAll,
        text: showAll ? `只看最近 ${LOCK_LIST_LIMIT} 条` : `还有 ${hidden} 条更早的记录，显示全部` }))));
  }
  t.append(tb);
  return h("div", { class: "scroll-x" }, t);
}

/** Profit / loss / flat only, no amount: expired = settlement result, otherwise the latest
 * floating PnL (at stop, or now while tracking). Within ±10 USD counts as flat. */
const OUTCOME_CN = { profit: "盈利", loss: "亏损", flat: "持平" };
const OUTCOME_TONE = { profit: "ok", loss: "bad", flat: "" };
const OUTCOME_BASIS = { settlement: "按到期结算", floating_at_stop: "按停止时浮动盈亏", floating_now: "按当前浮动盈亏（未到期）",
  floating_at_expiry: "按到期前最后浮动盈亏" };
const FALLBACK_CN = { credit_missing: "到期结算无法计算（锁定时信用缺失）", delivery_unavailable: "交割价未取得",
  settlement_pending: "到期结算尚未完成" };
function outcomeChip(o) {
  if (!o || !o.verdict) return h("span", { class: "muted small", text: "—", title: o?.basis === "settlement" ? "到期结算结果不可用" : "盈亏不可用" });
  return h("span", {}, h("span", { class: `chip ${OUTCOME_TONE[o.verdict]}`, text: OUTCOME_CN[o.verdict],
    title: `${o.fallback_reason ? `${FALLBACK_CN[o.fallback_reason] || ""}，` : ""}${OUTCOME_BASIS[o.basis] || ""}；±${o.flat_band_usd ?? 10} 美元以内算持平` }),
    o.basis === "floating_now" ? h("span", { class: "muted small", text: " 浮动" }) : null);
}

// ── detail ───────────────────────────────────────
/** Static payout of an inverse (BTC-settled) vertical at settlement price S, in BTC. */
function payoutBtc(side, ks, kl, S) {
  const intrinsic = side === "put" ? Math.max(ks - S, 0) - Math.max(kl - S, 0) : Math.max(S - ks, 0) - Math.max(S - kl, 0);
  return intrinsic / S;
}

/** Spot implied by the backend's signed boundary distance, which is a percent of the strike
 * (tools/astra_underwriting_v20.py signed_boundary_distance): put (S − K)/K, call (K − S)/K. */
function spotFrom(side, strike, dPct) {
  if (!isNum(strike) || !isNum(dPct)) return null;
  const k = dPct / 100;
  return side === "put" ? strike * (1 + k) : strike * (1 - k);
}

export function lockDetail(d, { loading, error, session, confirm, onStop, onAskStop, onCancel, onRelock, series, seriesError, refresh, onHoldReview, holdBusy }) {
  const box = h("div", { class: "lock-detail" });
  if (!d) { box.append(h("p", { class: "empty", text: loading ? "正在读取锁定对照…" : error ? `读取失败：${error}` : "" })); return box; }
  if (error) box.append(h("p", { class: "banner", text: `刷新失败，显示的是上一次读取的结果（${error}）。` }));

  const e = d.episode || {};
  const c = d.fixed_contract || e.contract || {};
  const side = c.side;
  const rows = Object.fromEntries((d.rows || []).map((r) => [r.key, r]));
  const ov = d.overview || {};
  const ref = ov.reference_result || {};
  const st = episodeState(e);

  // header
  const remaining = isNum(ov.remaining_to_expiry_ms) && ov.remaining_to_expiry_ms > 0 ? `剩余 ${dur(ov.remaining_to_expiry_ms)}` : "已到期";
  box.append(h("div", { class: "ld-head" },
    h("h4", { text: `${contractTitle(c)}，宽 ${usd(c.width_usd)} 美元，${c.quantity_btc ?? 1} BTC` }),
    h("span", { class: "muted", text: `锁定 ${clock(get(d, "baseline.asof_ms"))} → 最新 ${clock(get(d, "latest.asof_ms"))}，${remaining}` }),
    loading ? h("span", { class: "muted", text: "更新中…" }) : null));

  // 0. settlement at expiry, from the exchange delivery price
  const settlement = d.settlement;
  if (settlement) box.append(settlementView(settlement, c));

  // Now first: floating P&L, share of max profit and time left; the lock-time row follows.
  box.append(section("现在", floatView(series, seriesError)));

  // The lock-time reasons, re-decided by fixed rules at every sample, and the hold review.
  if (series?.thesis_ledger) box.append(section("锁定时的依据，现在怎样",
    ledgerView(series.thesis_ledger, series.hold_review, { session, busy: holdBusy, onHoldReview })));

  // 1. at lock (t0)
  const sd = rows["liability.short_distance"] || {};
  const t0Spot = spotFrom(side, c.short_strike_usd, get(sd, "t0.value"));
  const t1Spot = spotFrom(side, c.short_strike_usd, get(sd, "t1.value"));
  const be = (ov.baseline_breakeven_usd || []).find(isNum);
  const beRow = rows["liability.baseline_breakeven_1_distance"] || {};
  const netT0 = ref.entry_after_fee_btc;
  const width = c.width_usd;
  const lossAtLongBtc = isNum(netT0) && isNum(c.long_strike_usd) ? netT0 - payoutBtc(side, c.short_strike_usd, c.long_strike_usd, c.long_strike_usd) : null;
  const maxLossUsd = isNum(netT0) && isNum(width) && isNum(t0Spot) ? width - netT0 * t0Spot : null;
  const figs = h("div", { class: "figures ld-figs" },
    fig(signedPct(get(sd, "t0.value")), "锁定时短腿距现价", isNum(t0Spot) ? `现价约 ${usd(t0Spot)}` : ""),
    fig(isNum(be) ? usd(be) : "—", "盈亏平衡价（固定）", isNum(get(beRow, "t0.value")) ? `锁定时距现价 ${signedPct(beRow.t0.value)}` : "锁定时未形成"),
    fig(btcText(netT0), "扣入场费后信用", isNum(ref.entry_credit_btc)
      ? `信用 ${btcText(ref.entry_credit_btc)}，入场费 ${isNum(ref.entry_fee_btc) ? btcText(ref.entry_fee_btc) : "缺失"}` : "锁定时信用缺失"),
    fig(isNum(maxLossUsd) ? `${usd(maxLossUsd)} 美元` : "—", "最坏到期亏损",
      isNum(lossAtLongBtc) ? (side === "put"
        ? `美元口径近似封顶；按 BTC 计在保护腿处亏 ${btcText(Math.abs(lossAtLongBtc), 4)}，继续下跌 BTC 数还会增加`
        : `美元口径近似封顶；按 BTC 计在保护腿处亏 ${btcText(Math.abs(lossAtLongBtc), 4)}，为 BTC 口径最大值`) : ""));
  box.append(section("锁定时", figs, payoffChart({ side, ks: c.short_strike_usd, kl: c.long_strike_usd, net: netT0, be, t0Spot, t1Spot,
    settle: settlement && settlement.status === "settled" ? settlement.delivery.delivery_price_usd : null })));

  // 1b. the whole path since lock, not just the two ends
  box.append(section("锁定后过程", series ? processView(series, c, { be })
    : h("p", { class: "muted small", text: seriesError ? `过程读取失败：${seriesError}` : "正在读取过程…" })));

  // 2. since lock
  const keyRows = ["liability.short_distance", "liability.protection_distance", "liability.baseline_breakeven_1_distance",
    "liability.baseline_breakeven_2_distance", "quote.visible_credit_btc", "quote.net_credit_after_entry_fee_btc",
    "fee.entry_fee_btc"].map((k) => rows[k]).filter(Boolean);
  const t = h("table", { class: "t compact" }, h("thead", {}, h("tr", {}, ...["项目", "锁定时", "最新", "变化"].map((x) => h("th", { text: x })))));
  const tb = h("tbody");
  for (const r of keyRows) tb.append(changeRow(r));
  t.append(tb);
  box.append(section("之后的变化（明细）", h("div", { class: "scroll-x" }, t)));

  // 3. market context and the program's two-question summary
  const ctx = (d.rows || []).filter((r) => r.group === "context" || r.group === "opinion" || r.group === "risk");
  if (ctx.length) {
    const changed = ctx.filter((r) => r.status === "changed").length;
    const det = h("details", { class: "ld-more" }, h("summary", { text: `市场背景与两问摘要（${changed} 项有变化）` }));
    const ct = h("table", { class: "t compact" }, h("thead", {}, h("tr", {}, ...["项目", "锁定时", "最新", "状态"].map((x) => h("th", { text: x })))));
    const cb = h("tbody");
    for (const r of ctx) {
      const a = ctxValue(r.key, r.t0), b = ctxValue(r.key, r.t1);
      const st = r.status === "changed" && a === b ? "细节有变化" : ROW_STATUS_CN[r.status] || "—";
      cb.append(h("tr", {}, h("td", { text: ctxLabel(r) }), h("td", { text: a }), h("td", { text: b }),
        h("td", { class: `st ${r.status}`, text: st })));
    }
    ct.append(cb);
    det.append(h("div", { class: "scroll-x" }, ct));
    box.append(det);
  }

  // 4. sampling
  const p = d.process || {};
  const gapsByReason = {};
  for (const g of p.gaps || []) { const k = gapCn(get(g, "gap.reason")); gapsByReason[k] = (gapsByReason[k] || 0) + 1; }
  const gapText = Object.entries(gapsByReason).map(([k, n]) => `${k} ${n} 次`).join("；");
  const span = isNum(p.post_lock_span_ms) ? `，锁定后覆盖 ${dur(p.post_lock_span_ms)}` : "";
  const sampling = [
    `共记录 ${p.observed_count ?? e.point_count ?? 0} 个点${span}`,
    isNum(p.max_gap_ms) ? `最长间隔 ${dur(p.max_gap_ms)}` : null,
    e.gap_count ? `缺口 ${e.gap_count} 次${gapText ? `（${gapText}${e.gap_storage_truncated ? "，部分明细未保留" : ""}）` : ""}` : "没有缺口",
    get(d, "sampling.collector_error") ? "后台采样出错，最新点可能滞后" : null,
  ].filter(Boolean).join("；");
  // With its own sampler thread the cadence is the setting; without it, sampling rides on the refresh cycle.
  const every = dur(get(d, "sampling.interval_ms") ?? 60000);
  const cycleMs = isNum(refresh?.total_ms) ? refresh.total_ms + (refresh.loop_wait_s ?? 30) * 1000 : null;
  const cadence = get(d, "sampling.sampler_thread")
    ? `由独立采样线程每 ${every}采一次（同时追踪多条或抓取较慢时会略长）`
    : `设定最短每 ${every}一次，实际随后台刷新进行${cycleMs ? `，最近一轮约 ${Math.round(cycleMs / 1000)} 秒` : ""}`;
  const r = get(d, "sampling.retention");
  const keep = r ? `完整证据保留：最近 ${dur(r.full_window_ms)}逐个保留，更早每 ${dur(r.dense_bucket_ms)}留 1 个；结束后 ${dur(r.dense_until_ms)}内每 ${dur(r.dense_bucket_ms)}、${dur(r.sparse_until_ms)}内每 ${dur(r.sparse_bucket_ms)}留 1 个，之后只留锁定时与最后一点，${dur(r.drop_after_ms)}后移除。过程曲线保留每个采样点。`
    : "";
  box.append(section("采样过程", h("p", { class: "muted", text: `${sampling}。${cadence}。` }),
    keep ? h("p", { class: "muted small", text: keep }) : null));

  // 5. how to read
  if ((d.limitations_cn || []).length) {
    box.append(h("details", { class: "ld-more" }, h("summary", { text: "读法说明" }),
      h("ul", { class: "q" }, ...d.limitations_cn.map((x) => h("li", { text: x.replace(/（[a-z_, ]+）/g, "")
        .replace(/[a-z]+(?:_[a-z]+)+(?:、[a-z]+(?:_[a-z]+)+)*/g, (m) => m.split("、").map(gapCn).join("、")) })))));
  }

  // 6. actions
  const acts = h("div", { class: "actions" });
  if (session) {
    if (e.status === "active") {
      if (confirm === "stop") acts.append(h("span", { class: "confirm", text: "停止后这条追踪不再采样；之后可以用已记录的点重新开始一条新的追踪。" }),
        h("button", { class: "btn", type: "button", onclick: onStop }, "确认停止"),
        h("button", { class: "btn secondary", type: "button", onclick: onCancel }, "取消"));
      else acts.append(h("button", { class: "btn secondary", type: "button", onclick: onAskStop }, "停止追踪"));
    } else if (e.status === "stopped" && e.stop_reason !== "expired" && isNum(c.expiry_ms) && c.expiry_ms > Date.now() && e.latest_snapshot_id) {
      if (confirm === "relock") acts.append(h("span", { class: "confirm", text: `以最近一点（${clock(e.latest_decision_asof_ms)}）为新的锁定时，开始一条新追踪。` }),
        h("button", { class: "btn", type: "button", onclick: onRelock }, "确认重新追踪"),
        h("button", { class: "btn secondary", type: "button", onclick: onCancel }, "取消"));
      else acts.append(h("button", { class: "btn secondary", type: "button", onclick: () => onRelock("ask") }, "以最近一点重新追踪"));
    }
  } else if (e.status === "active") acts.append(h("span", { class: "why", text: "登录后可停止追踪" }));
  if (acts.childNodes.length) box.append(acts);
  return box;
}

function settlementView(st, c) {
  if (st.status !== "settled") {
    return h("div", { class: "ld-result" }, h("b", { text: "到期结算" }),
      h("span", { text: "交易所未公布可用的交割价，无法计算到期结果。" }));
  }
  const res = st.settlement_result_btc;
  const usdText = isNum(st.settlement_result_usd) ? `，约 ${st.settlement_result_usd < 0 ? "−" : ""}${usd(Math.abs(st.settlement_result_usd))} 美元` : "";
  const where = c.side === "put"
    ? (st.delivery.delivery_price_usd >= c.short_strike_usd ? "在短腿之上，未进入责任区" : st.delivery.delivery_price_usd <= c.long_strike_usd ? "越过保护腿" : "落在短腿与保护腿之间")
    : (st.delivery.delivery_price_usd <= c.short_strike_usd ? "在短腿之下，未进入责任区" : st.delivery.delivery_price_usd >= c.long_strike_usd ? "越过保护腿" : "落在短腿与保护腿之间");
  return h("div", { class: `ld-result settle${isNum(res) ? (res >= 0 ? " ok" : " bad") : ""}` },
    h("b", { text: "到期结算" }),
    h("span", { class: "num", text: isNum(res) ? `${btcText(res)}${usdText}` : "结果无法计算（锁定时信用缺失）" }),
    h("small", { text: `交割价 ${usd(st.delivery.delivery_price_usd)}（${st.delivery.date}，${where}）；到期赔付 ${btcText(st.payout_btc)}；锁定时扣费后信用 ${isNum(st.entry_after_fee_btc) ? btcText(st.entry_after_fee_btc) : "缺失"}。按交易所交割价计算的静态结果，不是成交记录。` }));
}

// ── lock-time reasons (thesis ledger) and the hold review ─────────
const TONE_CLASS = { ok: "ok", warn: "warn", bad: "bad", muted: "skip" };
const HOLD_BLOCK_CN = {
  manual_review_service_not_configured: "模型复核未配置",
  lock_not_active: "锁定已结束",
  hold_review_inflight: "复核进行中",
  hold_review_cooldown: "刚复核过",
  hold_review_too_early: "锁定不足 15 分钟，过程太短",
};
const HOLD_FAIL_CN = {
  ERROR: "复核出错，未完成",
  INVALID_RESPONSE: "模型回答未通过校验，未采用",
  CALL_FAILED: "模型调用失败",
  BUDGET_EXHAUSTED: "今日模型额度已用完",
};

/** The lock-time reasons, each judged by fixed rules at every sample: what was assumed,
 * where it stands now, and what happened in between (lowest point, time in a worse state). */
export function ledgerView(led, hold, { session, busy, onHoldReview } = {}) {
  const box = h("div", { class: "ledger" });
  if (!led || led.status !== "OK") {
    box.append(h("p", { class: "muted small", text: led?.status === "NO_SAMPLES" ? "还没有采样。" : "依据暂不可用。" }));
    return box;
  }
  const p = led.projection_cn || {};
  box.append(h("p", { class: "ledger-head", text: p.headline_cn || "" }));
  const lines = [["位置", p.where_cn], ["节奏", (p.rhythm_cn || []).join("；")]].filter(([, v]) => v);
  box.append(h("dl", { class: "ledger-qa" }, ...lines.flatMap(([k, v]) => [h("dt", { text: k }), h("dd", { text: v })])));

  const t = h("table", { class: "t compact ledger-t" }, h("thead", {}, h("tr", {},
    ...["依据", "锁定时", "现在", "期间"].map((x) => h("th", { text: x })))));
  const tb = h("tbody");
  for (const th of led.theses || []) {
    const recorded = th.state_now !== "NOT_RECORDED";
    tb.append(h("tr", { class: recorded ? "" : "muted" },
      h("td", { title: th.rule_cn || "" }, h("b", { text: th.label_cn })),
      h("td", { text: th.claim_cn || "" }),
      h("td", {},
        h("span", { class: `chip ${TONE_CLASS[th.tone] || "skip"}`, text: th.state_now_cn || "—" }),
        recorded && th.now_value_cn && th.now_value_cn !== "—" ? h("span", { class: "n small", text: ` ${th.now_value_cn}` }) : null,
        th.pending_cn ? h("div", { class: "muted small", text: th.pending_cn }) : null),
      h("td", { class: "small", text: recorded ? th.experience_cn || "" : "" })));
  }
  t.append(tb);
  box.append(h("div", { class: "scroll-x" }, t));

  const gaps = led.gap_ledger || {};
  const unknown = [...(p.unknown_cn || [])];
  if ((gaps.new || []).length) unknown.push(`锁定后新出现的数据缺口：${gaps.new.map(gapCn).join("、")}`);
  if (unknown.length) box.append(h("ul", { class: "ledger-unknown muted small" }, ...unknown.map((x) => h("li", { text: x }))));
  const rules = (led.theses || []).filter((x) => x.rule_cn);
  box.append(h("details", { class: "ctx-note" }, h("summary", { text: "判定规则" }),
    h("ul", { class: "small" }, ...rules.map((x) => h("li", { text: `${x.label_cn}：${x.rule_cn}` }))),
    h("p", { class: "small", text: `${p.note_cn || ""} 分类依据（Gamma、锚定、流向）的新状态需持续 30 分钟才改判；数值依据回到更好状态需多越过 10%。` })));

  if (hold) box.append(holdView(led, hold, { session, busy, onHoldReview }));
  return box;
}

function holdView(led, hold, { session, busy, onHoldReview }) {
  const box = h("div", { class: "hold" });
  box.append(h("h6", { text: "持有中复核（模型解读）" }));
  const last = hold.last;
  if (last && last.answer) {
    const a = last.answer;
    const byId = Object.fromEntries((led.theses || []).map((x) => [x.id, x.label_cn]));
    box.append(h("p", { class: "hold-sum", text: a.summary }));
    if ((a.points || []).length) box.append(h("ul", { class: "hold-points" },
      ...a.points.map((pt) => h("li", {}, h("b", { text: `${byId[pt.thesis_id] || pt.thesis_id}：` }), pt.text))));
    if ((a.watch || []).length) box.append(h("div", { class: "hold-watch" }, h("span", { class: "muted small", text: "到期前盯住" }),
      h("ul", {}, ...a.watch.map((w) => h("li", { text: w })))));
    const moved = isNum(last.ledger_at_ms) && isNum(get(led, "coverage.latest_ms")) && led.coverage.latest_ms - last.ledger_at_ms > 10 * 60000;
    box.append(h("p", { class: "muted small", text: `基于 ${clock(last.ledger_at_ms)} 的依据，${clock(last.finished_at_ms)} 生成。${moved ? "之后依据已有更新。" : ""}只解读上面的判定，不改判、不给概率或交易建议。` }));
  } else if ((hold.reasons || []).includes("lock_not_active")) {
    box.append(h("p", { class: "muted small", text: "锁定已结束；持有中复核只用于仍在追踪的锁定。" }));
  } else {
    box.append(h("p", { class: "muted small", text: "还没有复核。复核让模型用几句话说明上面的依据现在怎样、到期前要盯什么，每次消耗 1 次模型额度。" }));
  }
  const failed = hold.last_failed;
  if (failed && (!last || (failed.finished_at_ms || 0) > (last.finished_at_ms || 0))) {
    box.append(h("p", { class: "muted small", text: `${clock(failed.finished_at_ms)} 的复核：${HOLD_FAIL_CN[failed.status] || "未完成"}。` }));
  }
  const reasons = hold.reasons || [];
  if (reasons.includes("lock_not_active")) return box;
  const why = !session ? "登录后可复核" : busy ? "复核中…（约半分钟）"
    : reasons.includes("hold_review_too_early") ? `锁定满 15 分钟后可复核（还差约 ${Math.max(1, Math.ceil((hold.history_needed_ms || 0) / 60000))} 分钟）`
    : reasons.includes("hold_review_cooldown") ? `${Math.max(1, Math.ceil((hold.cooldown_remaining_ms || 0) / 60000))} 分钟后可再次复核`
    : reasons.map((r) => HOLD_BLOCK_CN[r] || "暂不可用").join("、");
  const enabled = session && !busy && hold.enabled;
  box.append(h("div", { class: "hold-act" },
    h("button", { class: "btn", type: "button", disabled: !enabled, onclick: () => onHoldReview && onHoldReview(),
      text: last ? "重新复核" : "请求持有中复核" }),
    !enabled && why ? h("span", { class: "muted small", text: ` ${why}` }) : null));
  return box;
}

function section(title, ...children) {
  return h("div", { class: "ld-sec" }, h("h5", { text: title }), ...children);
}

function fig(value, label, sub = "") {
  return h("div", { class: "fig" }, h("div", { class: "v", text: value }), h("div", { class: "k", text: label }),
    sub ? h("div", { class: "k", text: sub }) : null);
}

// Rows of the main change table: values come from value+unit, never raw text.
const LABEL = {
  "liability.short_distance": "短腿距现价", "liability.protection_distance": "保护腿距现价",
  "liability.baseline_breakeven_1_distance": "盈亏平衡距现价", "liability.baseline_breakeven_2_distance": "第二盈亏平衡距现价",
  "quote.visible_credit_btc": "同合约新开信用", "quote.net_credit_after_entry_fee_btc": "新开扣费后信用",
  "close.debit_btc": "平仓借记", "close.net_debit_after_close_fee_btc": "平仓借记（含平仓费）", "fee.entry_fee_btc": "入场费",
};
// Direction that is worse for the seller: distances shrinking, close cost rising.
const WORSE = { "liability.short_distance": -1, "liability.protection_distance": -1, "liability.baseline_breakeven_1_distance": -1,
  "liability.baseline_breakeven_2_distance": -1, "close.debit_btc": 1, "close.net_debit_after_close_fee_btc": 1 };

function cell(point, unit) {
  const v = point && point.value;
  if (!point) return "未记录";
  if (!isNum(v)) return v == null ? "缺失" : "—";
  return unit === "pct" ? signedPct(v) : unit === "BTC" ? btcText(v) : unit === "USD" ? usd(v) : String(v);
}

function changeRow(r) {
  const unit = get(r, "t1.unit") || get(r, "t0.unit");
  const a = get(r, "t0.value"), b = get(r, "t1.value");
  let text = ROW_STATUS_CN[r.status] || "—", tone = "";
  if (isNum(a) && isNum(b)) {
    const dlt = b - a;
    text = dlt === 0 ? "未变" : unit === "pct" ? `${dlt > 0 ? "+" : "−"}${Math.abs(dlt).toFixed(2)} 个百分点` : `${dlt > 0 ? "+" : "−"}${btcText(Math.abs(dlt))}`;
    const worse = WORSE[r.key];
    if (worse && dlt !== 0) tone = Math.sign(dlt) === worse ? "bad" : "ok";
  }
  return h("tr", { title: r.meaning_cn || "" },
    h("td", { text: LABEL[r.key] || r.label_cn || "其他项目" }),
    h("td", { class: "n", text: cell(r.t0, unit) }), h("td", { class: "n", text: cell(r.t1, unit) }),
    h("td", { class: `n ${tone}`, text }));
}

const CTX_LABEL = {
  "context.latest_window": "同一保单观察窗口", "context.tmv_price_path": "TMV 价格路径", "context.funding": "资金费",
  "context.skew": "期权偏斜", "context.active_flow": "主动成交", "context.option_space": "空间门与 Gamma",
  "context.macro": "宏观", "context.routing_relations": "证据关系", "opinion.q1.direct_refs": "Q1 程序摘要",
  "opinion.q2.direct_refs": "Q2 程序摘要", "risk.status": "风险参考 μ",
};
const ctxLabel = (r) => CTX_LABEL[r.key] || r.label_cn || "其他背景";

/** Plain-language reading of one context value; unknown shapes show nothing rather than raw fields. */
function ctxValue(key, point) {
  if (!point) return "未记录";
  const v = point.value;
  if (v == null) return "缺失";
  switch (key) {
    case "context.tmv_price_path": return TREND_CN[v.tmv_state] || TREND_CN[get(v, "tmvf_24h.final_state")] || "—";
    case "context.funding": return isNum(v.last_rate) ? `${signedPct(v.last_rate * 100, 4)}${get(v, "semantics.is_crowded") ? "，拥挤" : ""}` : "—";
    case "context.active_flow": { const dir = get(v, "combined.direction"); return get(v, "combined.data_ready") === false ? "未就绪" : FLOW_DIR_CN[dir] || "—"; }
    case "context.option_space": {
      const g = GAMMA_CN[get(v, "gamma_regime.regime")]; const gate = GATE_CN[get(v, "space_gate.state")];
      return [gate, g].filter(Boolean).join("，") || "—";
    }
    case "context.macro": return MACRO_CN[v.macro_regime] || (isNum(v.macro_score) ? `得分 ${v.macro_score.toFixed(2)}` : "—");
    case "context.skew": return isNum(v.rr_blend) ? (v.rr_blend < 0 ? "看跌期权更贵" : v.rr_blend > 0 ? "看涨期权更贵" : "均衡") : "—";
    case "risk.status": return v === "missing" ? "缺失" : v === "qualified" ? "合格" : "其他状态";
    case "opinion.q1.direct_refs": case "opinion.q2.direct_refs": return (v.summary_cn || "—").replace(/(\d+\.\d{4})\d+/g, "$1");
    case "context.latest_window": return point.text_cn && !/[=_]/.test(point.text_cn) ? point.text_cn : "—";
    default: return "—";
  }
}

/** Static payout at expiry for the locked book: x = settlement price, y = net BTC. */
function payoffChart({ side, ks, kl, net, be, t0Spot, t1Spot, settle }) {
  const wrap = h("div", { class: "payoff" });
  if (![ks, kl, net].every(isNum)) { wrap.append(h("p", { class: "muted small", text: "锁定时信用缺失，无法画出到期赔付。" })); return wrap; }
  const W = 560, H = 150, PL = 56, PR = 12, PT = 30, PB = 22;
  const w = Math.abs(ks - kl);
  const pts = [ks, kl, t0Spot, t1Spot, be, settle].filter(isNum);
  let lo = Math.min(...pts) - 0.6 * w, hi = Math.max(...pts) + 0.6 * w;
  const N = 80, data = [];
  for (let i = 0; i <= N; i++) { const S = lo + (hi - lo) * i / N; data.push([S, net - payoutBtc(side, ks, kl, S)]); }
  const ys = data.map((d) => d[1]);
  const yMax = Math.max(net, 0) * 1.15, yMin = Math.min(...ys) * 1.1;
  const X = (p) => PL + (p - lo) / (hi - lo) * (W - PL - PR);
  const Y = (v) => PT + (yMax - v) / (yMax - yMin) * (H - PT - PB);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "payoff-svg", role: "img", "aria-label": "到期赔付" });
  const zero = Y(0);
  const path = data.map(([S, v], i) => `${i ? "L" : "M"}${X(S).toFixed(1)} ${Y(v).toFixed(1)}`).join(" ");
  svg.append(s("clipPath", { id: "pay-up" }, s("rect", { x: 0, y: 0, width: W, height: zero })));
  svg.append(s("clipPath", { id: "pay-dn" }, s("rect", { x: 0, y: zero, width: W, height: H })));
  const area = `${path} L${X(hi)} ${zero} L${X(lo)} ${zero} Z`;
  svg.append(s("path", { d: area, fill: "var(--ok)", "fill-opacity": 0.14, "clip-path": "url(#pay-up)" }));
  svg.append(s("path", { d: area, fill: "var(--liability)", "fill-opacity": 0.12, "clip-path": "url(#pay-dn)" }));
  svg.append(s("line", { x1: PL, x2: W - PR, y1: zero, y2: zero, stroke: "var(--rule)" }));
  svg.append(s("path", { d: path, fill: "none", stroke: `var(--${side})`, "stroke-width": 2 }));
  for (const [v, lab] of [[net, btcText(net, 4)], [Math.min(...ys), btcText(Math.min(...ys), 4)]]) {
    svg.append(s("text", { x: PL - 6, y: Y(v) + 4, "text-anchor": "end", class: "tick", text: lab }));
  }
  // Strikes label below the plot; breakeven and prices label above, on two rows when close.
  const rows = [[], []];
  const mark = (p, lab, cls, top) => {
    if (!isNum(p) || p < lo || p > hi) return;
    const x = X(p);
    svg.append(s("line", { x1: x, x2: x, y1: top ? PT - 4 : PT, y2: H - PB, class: cls }));
    let y = H - 6;
    if (top) {
      const row = rows[0].every((o) => Math.abs(o - x) > 64) ? 0 : 1;
      rows[row].push(x);
      y = row === 0 ? 10 : 22;
    }
    svg.append(s("text", { x, y, "text-anchor": "middle", class: "tick", text: lab }));
  };
  mark(ks, `短腿 ${usd(ks)}`, "pm strike", false);
  mark(kl, `保护腿 ${usd(kl)}`, "pm strike", false);
  mark(t0Spot, "锁定时价格", "pm t0", true);
  if (isNum(t1Spot) && Math.abs(t1Spot - (t0Spot || 0)) > (hi - lo) * 0.01) mark(t1Spot, "最新价格", "pm t1", true);
  mark(be, "盈亏平衡", "pm be", true);
  mark(settle, "交割价", "pm settle", true);
  wrap.append(svg, h("p", { class: "muted small", text: `到期静态赔付（币本位，按锁定时扣费后信用）：到期价在短腿外收下全部信用；${side === "put"
    ? "越过保护腿后美元亏损封顶，但 BTC 数随价格下行继续增加。" : "越过保护腿后美元亏损封顶，BTC 数随价格上行逐步减少。"}` }));
  return wrap;
}

// ── process over time ────────────────────────────
const FLOW_SIGN = { bullish: 1, neutral_to_bullish: 1, bearish: -1, neutral_to_bearish: -1 };
const GAMMA_TONE = { POSITIVE_GAMMA_PINNING: "var(--depth)", NEGATIVE_GAMMA_AMPLIFYING: "var(--neg-gamma)", TRANSITION: "var(--warn)" };
const hhmm = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false });
const mdhm = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });

/**
 * Floating PnL the way Deribit shows an open option position: per leg (mark − entry)
 * × signed size, summed; no fees, no close cost (a held spread is settled, not closed).
 * A credit spread's profit is capped at its entry credit, so what is left to earn is
 * the spread's current mark value.
 */
function floatView(series, seriesError) {
  const pts = series?.points || [];
  const p = [...pts].reverse().find((x) => isNum(x.float_pnl_btc));
  if (!p) {
    const last = pts[pts.length - 1];
    const why = last?.float_gaps?.length ? [...new Set(last.float_gaps.map(gapCn))].join("、")
      : series ? "标记价不可用" : seriesError ? `过程读取失败：${seriesError}` : "正在读取…";
    return h("div", { class: "ld-result" }, h("b", { text: "锁定以来浮动盈亏" }), h("span", { text: why }));
  }
  const v = p.float_pnl_btc, spot = p.spot_usd;
  const captured = isNum(p.max_profit_btc) && p.max_profit_btc > 0 ? v / p.max_profit_btc : null;
  return h("div", { class: `ld-result${v >= 0 ? " ok" : " bad"}` },
    h("b", { text: "锁定以来浮动盈亏" }),
    h("span", { class: "num", text: `${signedBtc(v)}${isNum(spot) ? `，约 ${v < 0 ? "−" : "+"}${usd(Math.abs(v * spot))} 美元` : ""}` }),
    h("span", { class: "ld-cap", text: [
      `最大盈利 ${btcText(p.max_profit_btc)}（锁定时开仓信用）`,
      `剩余可赚 ${btcText(p.remaining_credit_btc)}`,
      isNum(captured) ? `浮动盈亏占最大盈利 ${(captured * 100).toFixed(0)}%` : null].filter(Boolean).join(" · ") }),
    h("small", { text: `＝ 每条腿（当前标记价 − 锁定时成交价）× 方向，相加；与 Deribit 组合浮动盈亏同口径，不含手续费和平仓价差。截至 ${clock(p.asof_ms ?? p.recorded_at_ms)}；持有到期按交割价结算。` }));
}

/** Median of the sampling intervals, so the page states the real cadence. */
export function cadence(points) {
  const t = points.map((p) => p.asof_ms ?? p.recorded_at_ms).filter(isNum).sort((a, b) => a - b);
  const gaps = t.slice(1).map((v, i) => v - t[i]).sort((a, b) => a - b);
  return gaps.length ? gaps[Math.floor(gaps.length / 2)] : null;
}

/**
 * Three lanes sharing one time axis: price against the fixed liability lines,
 * the floating PnL (Deribit marks) under its capped maximum, and state strips
 * (space gate, active flow relative to this side, gamma regime).
 */
export function processView(series, c, { be }) {
  const wrap = h("div", { class: "process" });
  const pts = (series?.points || []).filter((p) => isNum(p.asof_ms ?? p.recorded_at_ms));
  if (pts.length < 2) {
    wrap.append(h("p", { class: "muted small", text: pts.length ? "只有锁定时一个点，第二个采样点到来后显示过程。" : "还没有可读的采样点。" }));
    return wrap;
  }
  const side = c.side, ks = c.short_strike_usd, kl = c.long_strike_usd;
  const T = (p) => p.asof_ms ?? p.recorded_at_ms;
  const t0 = T(pts[0]), t1 = T(pts[pts.length - 1]);
  const W = 720, PL = 64, PR = 14;
  const lanes = { price: [14, 150], result: [182, 244], strips: [258, 300] };
  const H = 322;
  const X = (t) => PL + (t - t0) / Math.max(1, t1 - t0) * (W - PL - PR);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "process-svg", role: "img", "aria-label": "锁定后过程" });

  // lane 1: price vs fixed lines
  const prices = pts.map((p) => p.spot_usd).filter(isNum);
  const levels = [ks, kl, be].filter(isNum);
  let lo = Math.min(...prices, ...levels), hi = Math.max(...prices, ...levels);
  const pad = (hi - lo) * 0.08 || 50; lo -= pad; hi += pad;
  const [py0, py1] = lanes.price;
  const Y = (v) => py1 - (v - lo) / (hi - lo) * (py1 - py0);
  if (isNum(ks) && isNum(kl)) {
    const top = Y(Math.max(ks, kl)), bottom = Y(Math.min(ks, kl));
    svg.append(s("rect", { x: PL, y: top, width: W - PL - PR, height: bottom - top, fill: `var(--${side}-soft)` }));
  }
  const labelYs = [];
  for (const [v, dash] of [[ks, ""], [kl, "4 3"], [be, "2 3"]]) {
    if (!isNum(v)) continue;
    svg.append(s("line", { x1: PL, x2: W - PR, y1: Y(v), y2: Y(v), stroke: `var(--${side})`, "stroke-dasharray": dash, "stroke-width": 1.2 }));
    // Keep axis labels at least 11px apart when two lines sit close together.
    let ly = Y(v) + 3;
    while (labelYs.some((o) => Math.abs(o - ly) < 11)) ly += (ly < (py0 + py1) / 2 ? -11 : 11);
    labelYs.push(ly);
    svg.append(s("text", { x: PL - 4, y: ly, "text-anchor": "end", class: "tick", text: usd(v) }));
  }
  const line = (rows, val, Yf) => rows.filter((p) => isNum(val(p)))
    .map((p, i) => `${i ? "L" : "M"}${X(T(p)).toFixed(1)} ${Yf(val(p)).toFixed(1)}`).join(" ");
  svg.append(s("path", { d: line(pts, (p) => p.spot_usd, Y), fill: "none", stroke: "var(--depth)", "stroke-width": 1.8 }));
  svg.append(s("text", { x: PL, y: py0 - 4, class: "lane", text: "现价（蓝）与锁定时的短腿、保护腿（虚线）、盈亏平衡（点线）" }));

  // lane 2: reference result
  const [ry0, ry1] = lanes.result;
  const res = pts.filter((p) => isNum(p.float_pnl_btc));
  const cap = res.map((p) => p.max_profit_btc).find(isNum);
  svg.append(s("text", { x: PL, y: ry0 - 4, class: "lane", text: "锁定以来浮动盈亏（Deribit 标记价口径，BTC；虚线为最大盈利）" }));
  if (res.length) {
    const rv = res.map((p) => p.float_pnl_btc);
    const rmax = Math.max(0, ...rv, isNum(cap) ? cap : 0), rmin = Math.min(0, ...rv);
    const span = (rmax - rmin) || 0.001;
    const RY = (v) => ry1 - (v - rmin) / span * (ry1 - ry0);
    svg.append(s("line", { x1: PL, x2: W - PR, y1: RY(0), y2: RY(0), stroke: "var(--rule)" }));
    if (isNum(cap)) svg.append(s("line", { x1: PL, x2: W - PR, y1: RY(cap), y2: RY(cap), stroke: "var(--ok)", "stroke-dasharray": "4 3" }));
    svg.append(s("text", { x: PL - 4, y: RY(rmax) + 3, "text-anchor": "end", class: "tick", text: rmax.toFixed(4) }));
    if (rmin < 0) svg.append(s("text", { x: PL - 4, y: RY(rmin) + 3, "text-anchor": "end", class: "tick", text: rmin.toFixed(4) }));
    svg.append(s("path", { d: line(res, (p) => p.float_pnl_btc, RY), fill: "none", stroke: "var(--ink-2)", "stroke-width": 1.4 }));
    for (const p of res) svg.append(s("circle", { cx: X(T(p)), cy: RY(p.float_pnl_btc), r: 1.7,
      fill: p.float_pnl_btc >= 0 ? "var(--ok)" : "var(--liability)" }));
  } else {
    svg.append(s("text", { x: PL + 8, y: (ry0 + ry1) / 2, class: "tick", text: "两腿标记价不可用，无法计算浮动盈亏" }));
  }

  // lane 3: state strips
  const strips = [
    ["FMZ 原生门（参考）", (p) => (p.space_gate_state === "STRUCTURE_ELIGIBLE" ? "var(--ok)" : p.space_gate_state ? "var(--warn)" : null)],
    ["主动成交", (p) => {
      if (p.flow_ready === false || !p.flow_direction) return null;
      const sgn = FLOW_SIGN[p.flow_direction];
      if (!sgn) return "var(--ink-3)";
      return (sgn > 0) === (side === "put") ? "var(--ok)" : "var(--liability)";
    }],
    ["Gamma", (p) => GAMMA_TONE[p.gamma_regime] || (p.gamma_regime ? "var(--ink-3)" : null)],
  ];
  strips.forEach(([label, tone], i) => {
    const y = lanes.strips[0] + i * 14;
    svg.append(s("text", { x: PL - 4, y: y + 9, "text-anchor": "end", class: "tick", text: label }));
    pts.forEach((p, j) => {
      const color = tone(p);
      const x0 = X(T(p)), x1 = j + 1 < pts.length ? X(T(pts[j + 1])) : W - PR;
      svg.append(s("rect", { x: x0, y, width: Math.max(1, x1 - x0), height: 10, fill: color || "var(--paper-2)", "fill-opacity": color ? 0.75 : 1 }));
    });
  });

  // gaps and time axis
  for (const g of series.gaps || []) {
    if (!isNum(g.recorded_at_ms) || g.recorded_at_ms < t0 || g.recorded_at_ms > t1) continue;
    svg.append(s("line", { x1: X(g.recorded_at_ms), x2: X(g.recorded_at_ms), y1: py1 + 2, y2: py1 + 8,
      stroke: "var(--liability)", "stroke-width": 1.5 }));
  }
  const fmt = t1 - t0 > 20 * 3.6e6 ? mdhm : hhmm;
  for (let i = 0; i <= 5; i++) {
    const t = t0 + (t1 - t0) * i / 5;
    svg.append(s("text", { x: X(t), y: H - 2, "text-anchor": i === 0 ? "start" : i === 5 ? "end" : "middle", class: "tick",
      text: fmt.format(new Date(t)) }));
  }

  // hover read-out
  const cross = s("line", { y1: py0, y2: lanes.strips[1], stroke: "var(--ink-3)", "stroke-dasharray": "2 2", visibility: "hidden" });
  svg.append(cross);
  const tip = h("div", { class: "process-tip", hidden: true });
  const hit = s("rect", { x: PL, y: 0, width: W - PL - PR, height: H, fill: "transparent" });
  hit.addEventListener("mousemove", (ev) => {
    const box = svg.getBoundingClientRect();
    const vx = (ev.clientX - box.left) / box.width * W;
    const t = t0 + (vx - PL) / (W - PL - PR) * (t1 - t0);
    const p = pts.reduce((a, b) => (Math.abs(T(b) - t) < Math.abs(T(a) - t) ? b : a));
    cross.setAttribute("x1", X(T(p))); cross.setAttribute("x2", X(T(p))); cross.setAttribute("visibility", "visible");
    const gate = p.space_gate_state === "STRUCTURE_ELIGIBLE" ? "放行" : p.space_gate_state ? "关闭" : "未知";
    tip.textContent = [mdhm.format(new Date(T(p))),
      `现价 ${usd(p.spot_usd)}，短腿距离 ${signedPct(p.short_distance_pct)}`,
      isNum(p.float_pnl_btc) ? `浮动盈亏 ${signedBtc(p.float_pnl_btc)}，剩余可赚 ${btcText(p.remaining_credit_btc)}` : "浮动盈亏无法计算（早期样本已精简或缺标记价）",
      `FMZ 空间门${gate}（仅参考），${p.flow_ready === false ? "主动成交未就绪" : FLOW_DIR_CN[p.flow_direction] || "主动成交方向不明"}`].join("\n");
    tip.hidden = false;
    tip.style.left = `${Math.min(box.width - 230, Math.max(0, ev.clientX - box.left + 12))}px`;
    tip.style.top = `${ev.clientY - box.top + 12}px`;
  });
  hit.addEventListener("mouseleave", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); });
  svg.append(hit);

  const med = cadence(pts);
  wrap.append(svg, tip, h("p", { class: "muted small", text: [
    `${pts.length} 个点${series.thinned ? "（已均匀抽样显示）" : ""}`,
    isNum(med) ? `实测采样间隔中位数 ${dur(med)}` : null,
    (series.gaps || []).length ? `红色刻度为采样缺口（${series.gaps.length} 次）` : null,
    "状态条：绿为 FMZ 原生空间门放行或对本单有利，琥珀为原生门关闭或过渡（只作参考，不是准入），红为对本单不利，灰为中性或缺数据",
  ].filter(Boolean).join("；") + "。" }));
  return wrap;
}
