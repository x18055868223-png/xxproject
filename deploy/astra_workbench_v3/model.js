// Normalize the workbench reading view into what the page draws.
// Display only: no value here is used for permission or accounting.
import { get, isNum } from "./format.js";
import { TERM_CN, evidenceLabels } from "./glossary.js";

const REASONS = {
  space_gate_not_eligible: "空间门未放行",
  SPACE_GATE_ANCHOR_SCORE_LOW: "锚点评分偏低",
  SPACE_GATE_NET_GAMMA_NOT_FRESH: "净 Gamma 不新鲜",
  cached_quote_not_current: "报价已过 5 秒",
  candidate_gate_version_changed_during_refresh: "数据刷新中，稍后可用",
  candidate_refresh_error: "候选刷新失败",
  snapshot_missing: "没有冻结快照",
  wpem_evidence_unavailable: "时间证据未就绪",
  manual_review_cooldown_active: "复核冷却中",
  manual_review_inflight: "已有复核进行中",
  manual_review_budget_exhausted: "今日额度已用完",
  manual_review_service_not_configured: "复核服务未配置",
  risk_reference_missing: "风险参考 μ 缺失",
  frozen_risk_reference_missing: "风险参考 μ 缺失",
  producer_map_background_incomplete: "MAP 背景不完整",
  data_collector_refresh_failed: "数据采集刷新失败",
  data_engine_space_input_usage_rejected: "空间输入未通过用途检查",
  fmz_fact_not_current: "FMZ 事实不可用于空间判断",
  SPACE_GATE_GGR_CONFLICT: "Gamma 体制来源冲突",
  contract_expired: "合同已到期",
  active_budget_exhausted: "锁定名额已满",
  lock_snapshot_not_retained: "快照未保留",
  fmz_snapshot_stale: "FMZ 快照过期",
  fmz_price_stale: "FMZ 价格过期",
  fmz_not_live: "FMZ 不在实时模式",
  fmz_fact_not_current: "FMZ 事实不是当前",
  producer_native_space_gate_missing: "缺少原生空间门",
  space_gate_clock_invalid_or_stale: "空间门时钟过期",
  idempotency_key_conflict: "请求键冲突",
  review_cooldown: "复核冷却中",
  review_inflight: "已有复核进行中",
  unauthorized: "需要登录",
  origin_required: "需要同源请求",
  origin_not_allowed: "来源页面不被允许",
  insecure_transport: "需要 HTTPS",
  host_not_allowed: "主机名不被允许",
  llm_not_configured: "模型未配置",
  candidate_not_current: "候选已更新，请重试",
  observation_review_unavailable: "当前不可复核",
  structure_gate_not_eligible: "结构门未放行",
  m_ref_null: "补偿余量 M 无法计算",
  risk_reference_missing_or_unqualified: "风险参考 μ 缺失",
  snapshot_not_reviewable: "快照不可用于生产复核",
  discovery_refresh_error: "候选发现失败",
  SPACE_GATE_ANCHOR_NOT_VALID: "锚点无效",
  SPACE_GATE_ANCHOR_SCORE_MISSING: "锚点评分缺失",
  SPACE_GATE_GGR_TRANSITION: "Gamma 处于翻转过渡",
  SPACE_GATE_NET_GAMMA_NEGATIVE: "净 Gamma 为负",
  SPACE_GATE_NET_GAMMA_CONFLICT: "净 Gamma 来源冲突",
  SPACE_GATE_GEX_INFO_AVAILABILITY_NOT_OK: "GEX 数据不可用",
  data_collector_warming_up: "后台采集预热中",
  fmz_producer_version_mismatch: "FMZ 版本不匹配",
  new_reference_required: "需要明确为新参照",
  source_reference_mismatch: "所选记录点不属于该追踪",
  lock_candidate_mismatch: "快照与合同不一致",
  locked_quote_unavailable: "锁定报价不可用",
  locked_sampling_paused: "锁定采样暂停",
  episode_not_found: "找不到该追踪",
  request_key_reused: "请求键已被使用",
  // transport, session and generic server answers
  preview_read_only: "本地预览只读，不发送任何操作；请在线上页面登录和操作",
  server_token_unconfigured: "服务器未配置工作台令牌",
  method_not_allowed: "请求方式不被接受",
  rate_limited: "尝试过于频繁，请稍等一分钟再试",
  network_error: "无法连接服务器，请检查网络或稍后再试",
  upstream_unreachable: "页面没等到服务器答复（网络中断或等待超时）；操作可能已在服务器完成，请刷新查看",
  bad_json: "服务器返回了非预期的内容",
  not_found: "请求的内容不存在",
  refresh_failed: "工作台刷新失败",
  review_failed: "复核执行失败",
  receipt_not_accepted: "回执未被接受",
  request_size_invalid: "请求大小不合规",
  locked_projection_unavailable: "锁定追踪读数暂不可用",
  locked_series_unavailable: "锁定过程序列暂不可用",
  chain_audit_disabled: "审计日志未启用",
  audit_file_not_found: "找不到该审计日志文件",
  invalid_audit_file: "审计日志文件名无效",
  evidence_export_busy: "证据导出正忙，请稍后再试",
  unknown: "原因未知",
};
/** Unmapped codes are rare; they stay visible as a code, never passed off as prose. */
export const reasonCn = (code) => REASONS[code] || (/^[A-Za-z0-9_.]+$/.test(String(code)) ? `其他原因（代码 ${code}）` : String(code));

const STATUS_CN = {
  RESEARCH_OK: "已通过校验", OK: "已通过校验", rejected: "未执行", ERROR: "调用失败", SEMANTIC_REJECTED: "未通过程序校验",
  CURRENT: "对应当前快照", STALE: "已过期", REJECTED: "被拒绝", NOT_RUN: "未运行",
};
export const statusCn = (code) => STATUS_CN[code] || code || "—";

export function applicability(code) {
  const c = String(code || "").toUpperCase();
  if (!c) return { cls: "none", text: "尚无模型判断" };
  if (c.includes("CONTEST")) return { cls: "contested", text: "支持与反证并存" };
  if (c.includes("UNABLE") || c.includes("UNKNOWN")) return { cls: "none", text: "无法判断" };
  if (c.includes("NOT") || c.includes("UNFIT") || c.includes("INAPPLIC")) return { cls: "unfit", text: "不适配" };
  if (c.includes("FIT") || c.includes("APPLIC") || c.includes("SUPPORT")) return { cls: "fit", text: "适配" };
  return { cls: "none", text: c };
}

function horizonIv(info, expiryToken, horizonDays) {
  const rows = (info.term_structure || []).filter((r) => isNum(r && r.atm_iv));
  const exact = rows.find((r) => expiryToken && String(r.expiry || "").toUpperCase() === expiryToken);
  if (exact) return { iv: exact.atm_iv, expiry: exact.expiry, match: "同到期" };
  if (rows.length && isNum(horizonDays) && rows.every((r) => isNum(r.days_to_expiry))) {
    const r = rows.reduce((a, b) => (Math.abs(b.days_to_expiry - horizonDays) < Math.abs(a.days_to_expiry - horizonDays) ? b : a));
    return { iv: r.atm_iv, expiry: r.expiry, match: "最近期限" };
  }
  if (isNum(info.atm_iv)) return { iv: info.atm_iv, expiry: null, match: "近月 ATM" };
  return null;
}

export function gexInfo(view, candidate) {
  return get(candidate, "snapshot.fact_context.market_facts.space_context.gex_info")
    || get(view, "market_context.market_facts.space_context.gex_info") || {};
}

export function sideModel(view, candidate) {
  if (!candidate) return null;
  const snap = candidate.snapshot;
  const side = candidate.side;
  const base = { side, name: side === "put" ? "Put" : "Call", status: candidate.status, candidate, error: candidate.error };
  if (!snap) return base;
  const c = snap.contract || {};
  const eco = snap.economics || {};
  const liab = snap.liability || {};
  const spot = get(snap, "market.reference_price_usd");
  const valuation = get(snap, "market.valuation_ts_ms");
  const hours = isNum(c.expiry_ms) && isNum(valuation) ? (c.expiry_ms - valuation) / 3.6e6 : null;
  const outside = (k) => (isNum(k) && isNum(spot) ? (side === "put" ? (spot - k) / spot : (k - spot) / spot) * 100 : null);
  const be = (liab.breakeven_usd || []).find(isNum);
  const info = gexInfo(view, candidate);
  const token = String(c.short_instrument || "").split("-")[1];
  const iv = horizonIv(info, token ? token.toUpperCase() : null, isNum(hours) ? hours / 24 : null);
  const sigmaPct = iv && isNum(hours) && hours > 0 ? iv.iv * Math.sqrt(hours / 8760) : null;
  const width = isNum(c.width_usd) ? c.width_usd : (isNum(c.short_strike_usd) && isNum(c.long_strike_usd) ? Math.abs(c.short_strike_usd - c.long_strike_usd) : null);
  const credit = eco.visible_credit_btc;
  const creditToWidth = isNum(credit) && isNum(spot) && isNum(width) && width > 0 ? (credit * spot) / width : null;
  const disc = candidate.discovery || {};
  const quote = get(snap, "market.quote") || {};
  // Remaining time counts from the server read, not from the frozen valuation.
  const served = get(view, "served_at_ms");
  const remainingMs = isNum(candidate.remaining_to_expiry_ms) ? candidate.remaining_to_expiry_ms
    : isNum(c.expiry_ms) && isNum(served) ? c.expiry_ms - served : null;
  return {
    ...base,
    snap, spot, hours,
    remainingMs, servedAt: served,
    short: c.short_strike_usd, long: c.long_strike_usd, width, expiry: c.expiry_ms,
    shortName: c.short_instrument,
    breakeven: be,
    // [] means net credit <= 0 (no breakeven exists); null means it could not be computed.
    breakevenState: Array.isArray(liab.breakeven_usd) ? (isNum(be) ? "ok" : "none") : "unknown",
    netCredit: eco.net_credit_after_fee_btc, netCreditUsd: eco.net_credit_after_fee_usd_at_spot,
    fee: get(snap, "fee.amount_btc"), feeAssumed: get(snap, "fee.basis") === "configured_assumption",
    mu: eco.mu_ref_btc,
    shortOutsidePct: outside(c.short_strike_usd), beOutsidePct: outside(be),
    iv, sigmaPct,
    beMovePct: isNum(sigmaPct) && sigmaPct > 0 && isNum(be) ? outside(be) / sigmaPct : null,
    creditToWidth,
    delta: isNum(disc.short_delta) ? disc.short_delta : null,
    deltaTarget: disc.short_delta_target_abs,
    quoteAt: quote.received_ts_ms,
    quoteGaps: quote.gap_reasons || [],
    review: reviewModel(candidate),
  };
}

// ── underwriting preconditions ──────────────────
// review_gate lists every reason the production review is closed. Several are
// consequences or refresh artefacts, so they are folded into the few that a
// person can act on or must accept.
const CONSEQUENCE = new Set(["snapshot_not_reviewable", "m_ref_null", "cached_quote_not_current",
  "candidate_gate_version_changed_during_refresh", "manual_review_cooldown_active", "manual_review_inflight",
  "manual_review_budget_exhausted", "review_cooldown", "review_inflight"]);
const SPACE = new Set(["space_gate_not_eligible", "structure_gate_not_eligible"]);

/** Production review is open for this side once only refresh artefacts remain:
 * the backend re-freezes the quote when the review is requested. */
export function productionReady(m) {
  const gate = get(m, "candidate.review_gate") || {};
  return !!m?.snap && (gate.enabled || (gate.reasons || []).every((r) => CONSEQUENCE.has(r) && r !== "snapshot_not_reviewable"));
}

/** One shared statement for the round plus anything specific to one side. */
export function underwriting(view, sides) {
  const gate = view.space_gate || {};
  const anchor = get(gate, "anchor.score");
  const spaceReasons = [...new Set([...(gate.reason_codes || []), ...(gate.gap_reasons || [])])];
  const shared = [];
  const perSide = {};
  let spaceBlocked = false;
  for (const side of ["put", "call"]) {
    const m = sides[side];
    const reasons = get(m, "candidate.review_gate.reasons") || [];
    perSide[side] = [];
    for (const r of reasons) {
      if (SPACE.has(r)) spaceBlocked = true;
      else if (!CONSEQUENCE.has(r)) perSide[side].push(reasonCn(r));
    }
    if (m && !m.snap) perSide[side].push(m.error ? "候选未生成" : "没有冻结快照");
  }
  if (spaceBlocked || gate.eligible === false) {
    const since = isNum(gate.since_ts_ms) ? gate.since_ts_ms : null;
    const first = spaceReasons[0];
    const detail = first === "SPACE_GATE_ANCHOR_SCORE_LOW" && isNum(anchor) ? `锚点 ${anchor.toFixed(1)}，需 ≥60` : first ? reasonCn(first) : "";
    shared.push({ key: "space", text: `空间门关闭${detail ? `（${detail}）` : ""}`, since,
      title: spaceReasons.map(reasonCn).join("；") });
  }
  const open = !shared.length && ["put", "call"].every((k) => sides[k]?.snap && !perSide[k].length);
  return { open, shared, perSide };
}

// ── data sources ─────────────────────────────────
// Deribit quote products are written only when a review or lock freezes a
// quote; their age says when that last happened, not that a feed stopped.
const ON_DEMAND_COLLECTORS = new Set(["server_deribit_public_quote"]);
// Registered but deliberately not consumed (no public source exists).
const NOT_CONSUMED = new Set(["cex.usdt.borrow_rate.v1"]);
const CONSUMER_CN = {
  anchor: "锚点", btc_background: "慢背景", candidate_discovery: "候选发现", candidate_meta: "合约元数据", candidate_quote: "候选报价",
  current_price: "现价", daily_snapshot: "每日快照", data_health: "健康检查", delta_filter: "Delta 选约", derivatives_context: "衍生品背景",
  diagnostic_only: "仅诊断", fee_model: "费用模型", flow: "主动成交", funding_context: "资金费", gamma_regime: "Gamma 体制",
  legacy_state_diagnostic: "旧状态诊断", macro_context: "宏观", macro_layer: "宏观", manual_review_packet: "复核资料包",
  map_readonly: "价格地图", metadata_cache: "元数据缓存", price_path: "量价路径", risk_reference: "风险参考 μ", space_gate: "空间门",
  space_layer: "空间层", tmv: "TMV", underwriting_accounting: "承保核算", usd_conversion: "美元换算", "workbench.current": "工作台当前读数",
};
export const consumerCn = (c) => CONSUMER_CN[c] || "其他环节";
const SOURCE_STATUS_CN = { OK: "正常", USAGE_LIMITED: "正常", STALE: "超时效", MISSING: "缺失", PARTIAL: "部分可用",
  NOT_CONNECTED: "未接入", PLANNED: "计划中", INVALID: "来源报告无效" };
export const sourceStatusCn = (s) => SOURCE_STATUS_CN[String(s || "").toUpperCase()] || (s ? "其他状态" : "—");

/** Which explanation to show: current accepted receipt, then history, then program answer. */
export function reviewModel(candidate) {
  const d = candidate.review_display || {};
  const pick = (receipt, kind) => {
    const r = receipt.review || receipt;
    return {
      kind, receipt, review: r,
      status: r.status || receipt.status,
      applicability: r.applicability,
      q1: get(r, "q1.llm_explanation.points") || [],
      q2: get(r, "q2.llm_explanation.points") || [],
      evidence: allEvidence(r),
      labels: evidenceLabels(allEvidence(r).map((x) => ({ ref: x.ref, label_cn: get(x, "provided.label_cn") }))),
      trace: r.decision_trace || null,
      binding: receipt.display_binding || null,
      reason: d.reason_cn,
    };
  };
  const mine = (receipt) => {
    const r = receipt && (receipt.review || receipt);
    const cid = get(candidate, "snapshot.candidate_id");
    return receipt && (receipt.side == null || receipt.side === candidate.side) && (!cid || !r.candidate_id || r.candidate_id === cid);
  };
  if (mine(d.current)) return pick(d.current, "current");
  if (mine(d.historical)) return pick(d.historical, "historical");
  const q = candidate.current_questions;
  return {
    kind: "program", review: null, receipt: null,
    status: null, applicability: null,
    programQ1: q && q.q1, programQ2: q && q.q2,
    q1: [], q2: [], evidence: [], trace: null,
    reason: d.reason_cn || get(d, "consumer_binding_receipt.reason_cn"),
  };
}

/** Everything the model received, cited items first; each row as {ref, cited, provided, lineage}. */
function allEvidence(r) {
  const cited = new Set([...(get(r, "q1.llm_explanation.points") || []), ...(get(r, "q2.llm_explanation.points") || [])]
    .flatMap((p) => p.evidence_refs || []));
  const input = get(r, "decision_trace.input_evidence");
  const rows = Array.isArray(input) && input.length
    ? input.map((x) => ({ lineage: x.lineage, provided: x }))
    : dedupeEvidence([...(get(r, "q1.llm_explanation.evidence") || []), ...(get(r, "q2.llm_explanation.evidence") || [])]);
  return rows.map((row) => {
    const ref = get(row, "lineage.ref") || get(row, "provided.ref");
    return { ...row, ref, cited: cited.has(ref) };
  }).sort((a, b) => Number(b.cited) - Number(a.cited));
}

function dedupeEvidence(rows) {
  const seen = new Map();
  for (const row of rows) {
    const ref = get(row, "lineage.ref") || get(row, "provided.ref");
    if (ref && !seen.has(ref)) seen.set(ref, row);
  }
  return [...seen.values()];
}

/** Map model: spot, both books, σ, walls, markers and zones on one price axis. */
export function mapModel(view, sides) {
  const spot = get(view, "market_context.current_price_usd") ?? get(view, "btc_map.current_price_usd")
    ?? sides.put?.spot ?? sides.call?.spot;
  const ref = sides.put?.snap ? sides.put : sides.call;
  const info = gexInfo(view, ref?.candidate);
  const model = { spot, sigmaPct: ref?.sigmaPct ?? null, sides: {}, walls: [], markers: [], zones: [] };
  for (const k of ["put", "call"]) {
    const sm = sides[k];
    if (sm && sm.snap) model.sides[k] = { short: sm.short, long: sm.long, breakeven: sm.breakeven, beOutsidePct: sm.beOutsidePct, beMovePct: sm.beMovePct };
  }
  const near = info.near_term_walls || {};
  for (const [bucket, label] of [["weekly", "周度"], ["zero_dte", "0DTE"]]) {
    const walls = get(near, `${bucket}.walls`) || {};
    for (const key of ["n1", "n2", "p1", "p2"]) {
      const w = walls[key];
      if (w && isNum(w.strike)) model.walls.push({ price: w.strike, gex: w.gex_usd, kind: bucket,
        label: `${label} ${isNum(w.gex_usd) && w.gex_usd < 0 ? "负" : "正"} Gamma 墙`, short: label });
    }
  }
  const nearPrices = new Set(model.walls.map((w) => w.price));
  for (const [list, label] of [["support_walls", "全期限支撑墙"], ["resistance_walls", "全期限阻力墙"]]) {
    for (const p of info[list] || []) if (isNum(p) && !nearPrices.has(p)) model.walls.push({ price: p, gex: null, kind: "all", label, short: "全期" });
  }
  const pool = get(view, "btc_map.reference_pool") || [];
  const flip = pool.find((r) => r.role === "EFFECTIVE_FLIP");
  const flipPrice = flip ? (flip.raw_price ?? flip.display_center) : get(view, "market_context.market_facts.space_context.anchor.effective_flip_point");
  if (isNum(flipPrice)) model.markers.push({ price: flipPrice, label: "有效翻转位 Flip", short: "Flip", glyph: "ring", color: "var(--depth)",
    note: "价格在其上方为正 Gamma 区；名义坐标" });
  if (isNum(info.magnet_price)) model.markers.push({ price: info.magnet_price, label: "Gamma 磁吸位", short: "磁吸", glyph: "triangle", color: "var(--ink-2)" });
  if (isNum(info.max_pain_price)) model.markers.push({ price: info.max_pain_price, label: `最大痛点（${info.max_pain_target_expiry || "主到期"}）`, short: "痛点", glyph: "triangle", color: "var(--warn)" });
  const ne = info.next_expiry || {};
  if (isNum(ne.max_pain_price)) model.markers.push({ price: ne.max_pain_price, label: `下一到期最大痛点（${ne.expiry || ""}）`, short: "近痛点", glyph: "triangle", color: "var(--warn)",
    note: isNum(ne.hours_to_expiry) ? `${ne.hours_to_expiry} 小时后到期` : "" });
  const costLabel = { CP_STH: "短期持有者成本", CP_LTH: "长期持有者成本", CP_ALL: "全体持有者成本" };
  const costShort = { CP_STH: "短期成本", CP_LTH: "长期成本", CP_ALL: "全体成本" };
  // MAP's display pool may select only some cost levels; the background carries all three.
  const cb = get(view, "btc_map.background.inventory.metrics.cost_basis") || {};
  const cost = { CP_STH: cb.cp_sth_usd, CP_LTH: cb.cp_lth_usd, CP_ALL: cb.cp_all_usd };
  for (const r of pool) if (costLabel[r.role] && !isNum(cost[r.role])) cost[r.role] = r.raw_price ?? r.display_center;
  for (const [role, price] of Object.entries(cost)) {
    if (isNum(price)) model.markers.push({ price, label: costLabel[role], short: costShort[role], glyph: "diamond",
      color: "var(--ink-2)", track: "trade", note: `链上成本口径（${cb.latest_observation_date || "日期未知"}），与期权价格不同基准` });
  }
  for (const r of pool) {
    if (r.role === "KPF_BASIN" && isNum(r.raw_low) && isNum(r.raw_high)) {
      model.zones.push({ lo: r.raw_low, hi: r.raw_high, label: "KPF 成交接受区", note: "Binance 永续成交口径，名义坐标",
        containsShort: ["put", "call"].some((k) => model.sides[k] && model.sides[k].short >= r.raw_low && model.sides[k].short <= r.raw_high) });
    }
  }
  return model;
}

const CODES = {
  Bullish: "偏多", Bearish: "偏空", Neutral: "中性", UP: "上行", DOWN: "下行",
  POSITIVE_GAMMA_PINNING: "正 Gamma 钉住", NEGATIVE_GAMMA: "负 Gamma", POSITIVE_GAMMA: "正 Gamma",
  PRICE_ABOVE_FLIP_POSITIVE_GAMMA: "现价在翻转位上方，正 Gamma", PRICE_BELOW_FLIP_NEGATIVE_GAMMA: "现价在翻转位下方，负 Gamma",
  micro_bullish_pressure: "主动买压", micro_bullish_lean: "主动买方略强", micro_bearish_pressure: "主动卖压",
  micro_bearish_lean: "主动卖方略强", micro_neutral: "主动流中性",
  NOT_CROWDED: "未拥挤", CROWDED_LONG: "多头拥挤", CROWDED_SHORT: "空头拥挤", crowded_short: "空头拥挤", crowded_long: "多头拥挤",
  "Mild Tailwind": "温和顺风", "Mild Headwind": "温和逆风", "Strong Tailwind": "强顺风", "Strong Headwind": "强逆风",
  "Directional Drift": "方向漂移", full_live: "完整实时",
  STRUCTURE_BLOCKED: "结构关闭", STRUCTURE_ELIGIBLE: "结构放行",
  SKEW_TILTING_BULLISH_VS_BASELINE: "偏斜相对基准转向看涨", SKEW_TILTING_BEARISH_VS_BASELINE: "偏斜相对基准转向看跌",
  outside: "责任区外", short_to_long: "短腿至保护腿之间", beyond_long: "越过保护腿", outside_liability: "责任区外",
  favorable: "偏离不利方向", adverse: "指向不利方向", missing: "缺失", supported: "可用",
  NO_STRICT_DELTA: "无严格变化", NO_PATH_DUE_TO_WINDOW_GAP: "窗口缺口，无完整路径", INSUFFICIENT: "窗口不足",
  sequential_leg_book_estimate: "顺序腿盘口估算", deribit_index: "Deribit 指数",
  configured_assumption: "配置假设", public_taker_capped_daily_delivery_zero: "公开 taker 费率（有上限），交割费按 0",
  frozen_risk_reference_missing: "冻结风险参考缺失", risk_model_not_configured: "风险模型未配置", not_qualified: "未合格",
  delivery: "交割", entry: "入场", put: "Put", call: "Call", current: "当前", hold_to_expiry: "持有到期",
};
export const codeCn = (code) => (code == null ? "—" : CODES[code] || TERM_CN[code] || String(code));

/** Each registry product with how it is collected here and whether it is healthy. */
export function sourceHealth(view) {
  const products = get(view, "data_health.products") || {};
  const rank = { bad: 0, warn: 1, info: 2, off: 3, ok: 4 };
  return Object.entries(products).map(([id, p]) => {
    const first = (p.current || [])[0];
    const ud = p.usage_decision || (first && first.usage_decision) || {};
    const st = String(p.status || "").toUpperCase();
    const level = st === "OK" || st === "USAGE_LIMITED" ? "ok"
      : st.includes("STALE") || st.includes("PARTIAL") ? "warn"
      : st.includes("NOT_CONNECTED") || st.includes("PLANNED") ? "off" : st ? "bad" : "off";
    const age = isNum(ud.age_ms) && ud.age_ms < 365 * 86_400_000 ? ud.age_ms : null;
    // continuous: collected here on a schedule; on_demand: written when a quote is frozen;
    // elsewhere: consumed inside FMZ or not built yet; unused: registered, deliberately not consumed.
    const kind = NOT_CONSUMED.has(id) ? "unused"
      : ON_DEMAND_COLLECTORS.has(p.collector_id) ? "on_demand"
      : !first && (st === "MISSING" || st.includes("NOT_CONNECTED") || st.includes("PLANNED")) ? "elsewhere" : "continuous";
    const shown = kind === "continuous" ? level : kind === "on_demand" ? "info" : "off";
    return { id, status: p.status, level: shown, kind, collected: kind === "continuous", age, ttl: ud.ttl_ms,
      reasons: ud.reason_codes || [], consumers: p.affected_consumers || [], observed: first && first.observation_end_ms };
  }).sort((a, b) => rank[a.level] - rank[b.level] || a.id.localeCompare(b.id));
}
