// Normalize the workbench reading view into what the page draws.
// Display only: no value here is used for permission or accounting.
import { get, isNum, kusd, usd, pct } from "./format.js";
import { TERM_CN, evidenceLabels } from "./glossary.js";

const REASONS = {
  space_gate_not_eligible: "空间门未放行（旧口径）",
  // quote and fee qualification of a frozen snapshot (e.g. two legs' books read too far apart)
  data_engine_quote_usage_rejected: "报价未通过用途检查",
  data_engine_required_source_usage_rejected: "必需数据源未通过用途检查",
  fee_missing_or_incomplete: "费用缺失或不完整",
  quote_invalid_or_stale: "报价无效或过期",
  quote_held_not_current: "展示的是暂用读数，锁定需要最新报价",
  leg_book_time_skew: "两腿盘口读取时间相差过大",
  // Decision Path requests
  delta_population_capture_incomplete: "Delta 采集不完整，本轮没有选出候选",
  verification_closed: "这条核验已结束",
  verification_not_found: "这条核验已清理（合约到期）",
  verification_concluding: "结论正在提交",
  verification_unreadable: "核验记录读取失败",
  too_many_open_verifications: "进行中的核验过多，请先给旧的下结论",
  bad_step_request: "请求不完整",
  bad_verification_request: "请求不完整",
  bad_conclusion: "结论无效",
  invalid_verification_id: "核验编号无效",
  server_busy: "工作台繁忙",
  memory_pressure: "工作台内存紧张",
  candidate_projection_failed: "本侧展示数据处理失败",
  bad_lock_request: "锁定请求不完整",
  bad_request: "请求不完整",
  evidence_not_retained: "证据未保留",
  exact_pair_required: "需要同时指定对照的两个点",
  idempotency_race: "同一请求正在处理",
  index_integrity_error: "锁定索引校验失败",
  invalid_episode_id: "追踪编号无效",
  locked_packet_unavailable: "锁定数据包不可用",
  locked_review_target_changed: "锁定对象已变化",
  snapshot_id_invalid: "快照编号无效",
  wpem_evidence_not_current: "时间证据不是当前快照",
  interrupted_no_automatic_recall: "服务重启时中断，未自动重调模型",
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
  fmz_fact_not_current: "FMZ 事实不是当前版本",
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
  upstream_deribit_maintenance: "Deribit 交易所维护中",
  upstream_deribit_unavailable: "Deribit 接口暂时不可用",
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
export const hasReason = (code) => Object.prototype.hasOwnProperty.call(REASONS, code);

const STATUS_CN = {
  RESEARCH_OK: "已通过校验", OK: "已通过校验", rejected: "未执行", ERROR: "调用失败", SEMANTIC_REJECTED: "未通过程序校验",
  CURRENT: "对应当前快照", STALE: "已过期", REJECTED: "被拒绝", NOT_RUN: "未运行",
  interrupted_no_automatic_recall: "服务重启时中断", interrupted: "已中断", incomplete: "未完成", running: "进行中", done: "已完成",
};
// An unknown machine code is named as such, never shown raw in a reading area.
export const statusCn = (code) => STATUS_CN[code] || (code ? (/^[A-Za-z0-9_.]+$/.test(String(code)) ? "其他状态" : String(code)) : "—");

export function applicability(code) {
  const c = String(code || "").toUpperCase();
  if (!c) return { cls: "none", text: "尚无模型判断" };
  if (c === "EXPLAINED") return { cls: "fit", text: "模型只排序有限支持项" };
  if (c.includes("CONTEST")) return { cls: "contested", text: "支持与反证并存" };
  if (c.includes("UNABLE") || c.includes("UNKNOWN")) return { cls: "none", text: "无法判断" };
  if (c.includes("NOT") || c.includes("UNFIT") || c.includes("INAPPLIC")) return { cls: "unfit", text: "不适配" };
  if (c.includes("FIT") || c.includes("APPLIC") || c.includes("SUPPORT")) return { cls: "fit", text: "适配" };
  return { cls: "none", text: "其他判断" };
}

function horizonIv(info, expiryToken, horizonDays, own = null) {
  const rows = (info.term_structure || []).filter((r) => isNum(r && r.atm_iv));
  const exact = rows.find((r) => expiryToken && String(r.expiry || "").toUpperCase() === expiryToken);
  if (exact) return { iv: exact.atm_iv, expiry: exact.expiry, match: "同到期" };
  // gexmonitor's term structure can skip the nearest expiry; Deribit marks of that expiry do not
  if (own && isNum(own.atm_iv_pct)) return { iv: own.atm_iv_pct, expiry: own.expiry, match: own.stale ? "同到期（Deribit 标记，数据偏旧）" : "同到期（Deribit 标记）" };
  if (rows.length && isNum(horizonDays) && rows.every((r) => isNum(r.days_to_expiry))) {
    const r = rows.reduce((a, b) => (Math.abs(b.days_to_expiry - horizonDays) < Math.abs(a.days_to_expiry - horizonDays) ? b : a));
    return { iv: r.atm_iv, expiry: r.expiry, match: "最近期限" };
  }
  if (isNum(info.atm_iv)) return { iv: info.atm_iv, expiry: null, match: "近月 ATM" };
  return null;
}

/** This-expiry walls, pin and ATM IV computed from Deribit marks and open interest. */
export function expiryProfile(view, token) {
  return token ? get(view, `expiry_gamma.by_expiry.${String(token).toUpperCase()}`) || null : null;
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
  // candidate_readings@0.1 states one expected move for this contract (its own ATM IV) with its
  // source; the old per-page computation stays for cached views without it (docs/opus/23 §1.3).
  const readings = candidate.readings && typeof candidate.readings === "object" ? candidate.readings : null;
  const em = (readings && readings.expected_move) || {};
  let iv = horizonIv(info, token ? token.toUpperCase() : null, isNum(hours) ? hours / 24 : null, expiryProfile(view, token));
  let sigmaPct = iv && isNum(hours) && hours > 0 ? iv.iv * Math.sqrt(hours / 8760) : null;
  if (isNum(em.move_pct) && em.move_pct > 0) {
    sigmaPct = em.move_pct;
    iv = { iv: isNum(em.iv_pct) ? em.iv_pct : iv?.iv ?? null, expiry: token ? token.toUpperCase() : iv?.expiry ?? null,
      match: "readings", source: em.iv_source_cn || null };
  }
  const width = isNum(c.width_usd) ? c.width_usd : (isNum(c.short_strike_usd) && isNum(c.long_strike_usd) ? Math.abs(c.short_strike_usd - c.long_strike_usd) : null);
  const credit = eco.visible_credit_btc;
  const creditToWidth = isNum(credit) && isNum(spot) && isNum(width) && width > 0 ? (credit * spot) / width : null;
  const disc = candidate.discovery || {};
  const quote = get(snap, "market.quote") || {};
  // Remaining time counts from the server read, not from the frozen valuation.
  const served = get(view, "served_at_ms");
  const remainingMs = isNum(c.expiry_ms) && isNum(served) ? c.expiry_ms - served
    : isNum(candidate.remaining_to_expiry_ms) ? candidate.remaining_to_expiry_ms : null;
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
    beMovePct: isNum(em.breakeven_in_moves) ? em.breakeven_in_moves
      : isNum(sigmaPct) && sigmaPct > 0 && isNum(be) ? outside(be) / sigmaPct : null,
    readings,
    creditToWidth, credit,
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
    // An upstream reason (e.g. exchange maintenance) is the whole story; its consequences are not repeated.
    const upstream = reasons.some((r) => r.startsWith("upstream_"));
    perSide[side] = [];
    for (const r of reasons) {
      if (SPACE.has(r)) spaceBlocked = true;
      else if (upstream && r === "discovery_refresh_error") continue;
      else if (!CONSEQUENCE.has(r)) perSide[side].push(reasonCn(r));
    }
    if (m && !m.snap && !upstream) perSide[side].push(m.error ? "候选未生成" : "没有冻结快照");
  }
  // Since 2026-10-13 the space structure is evaluated, never gated; old codes are ignored here.
  void spaceBlocked; void anchor; void spaceReasons;
  const open = !shared.length && ["put", "call"].every((k) => sides[k]?.snap && !perSide[k].length);
  return { open, shared, perSide };
}

// ── data sources ─────────────────────────────────
// Deribit quote products are written only when a review or lock freezes a
// quote; their age says when that last happened, not that a feed stopped.
// Grouping follows who collects each product (its registry collector), not whether a
// record happens to be present right now, so a restart or a late source cannot move a row.
const COLLECTOR_KIND = {
  server_deribit_public_discovery: "continuous", server_gex_cache: "continuous", fmz_snapshots_jsonl_bridge: "continuous",
  server_deribit_public_quote: "on_demand",
  astra_map_sources_v24: "map_service", astra_map_data_server_v24: "map_service",
  "astra_map_projection_v24.collect_kpf_artifacts": "map_service",
  fmz_binance_adapter: "fmz",
};
// Registered but deliberately not consumed (no public source exists).
const NOT_CONSUMED = new Set(["cex.usdt.borrow_rate.v1"]);
// Registered under a running collector that does not produce them today.
const NOT_PRODUCED = new Set(["gex.pending.candidate.v1", "gex.raw.board.v1"]);
// Why a registered product is not consumed.
const IDLE_REASON = {
  "cex.usdt.borrow_rate.v1": "没有可用的公开来源，已登记但不消费",
  "gex.pending.candidate.v1": "GEX 缓存当前不产出该读数",
  "gex.raw.board.v1": "GEX 缓存当前不产出原始看板",
  "risk.astra.frozen_mu.reference.v1": "风险均值 μ 没有生产来源；μ 门已移除，不消费",
  "risk.astra.qualified_model.domain.v1": "合格风险模型尚未建立，不消费",
  "macro.dxy.close.v1": "宏观采集适配器尚未接入；宏观读数由 FMZ 事实提供",
  // not idle: explains how an old-looking but valid source is read
  "btc.kpf.map.readonly.v1": "按已收盘的每日成交归档计算，数据截至最近一个完整 UTC 日；成交密集区一天内变化很小，7 天内正常消费",
  "macro.vxn.close.v1": "宏观采集适配器尚未接入；宏观读数由 FMZ 事实提供",
};
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

/** Where the σ of the map comes from, said in words: "本期 Deribit 标记 ATM IV 35.6%". */
export function sigmaSource(m) {
  const iv = m && m.iv;
  if (!iv || !isNum(m.sigmaPct)) return null;
  const value = isNum(iv.iv) ? ` ${pct(iv.iv)}` : "";
  return iv.source ? `${iv.source}${value}` : `${iv.expiry || "近月"} ATM IV${value}`;
}

const signedUsd = (v) => (isNum(v) ? `${v > 0 ? "+" : v < 0 ? "−" : ""}${usd(Math.abs(v))}` : "—");
// the reference is the median of the first readings of the cycle, named by its hour ("08:00")
const hhUtc = (ms) => (isNum(ms) ? `${String(new Date(ms).getUTCHours()).padStart(2, "0")}:00` : null);

/**
 * The card's four rows from candidate_readings@0.1 (docs/opus/23 §2.2 ① ② ④): buffer in expected
 * moves, compensation against the market's own price split into what execution can and cannot
 * recover, the worst case against the compensation, and the price of a unit of remaining risk.
 * Each row is {key, label, big, chip?, chipCls?, subs: [{text, muted?}], title?}; a part the
 * backend did not provide is left out. null when the candidate carries no readings (old views).
 */
export function deckRows(m) {
  const rd = m && m.readings;
  if (!rd || typeof rd !== "object") return null;
  const rows = [];
  const em = rd.expected_move;
  if (em && (isNum(em.distance_in_moves) || isNum(em.short_distance_pct))) {
    const short = [isNum(em.short_distance_pct) ? `短腿 ${pct(em.short_distance_pct, 2)}` : null,
      isNum(em.short_distance_usd) ? `${usd(Math.abs(em.short_distance_usd))} 美元` : null,
      isNum(m.delta) ? `Delta ${Math.abs(m.delta).toFixed(2)}` : null].filter(Boolean).join(" · ");
    const be = isNum(em.breakeven_distance_pct)
      ? `盈亏平衡 ${pct(em.breakeven_distance_pct, 2)}${isNum(em.breakeven_in_moves) ? `（${em.breakeven_in_moves.toFixed(1)} 倍）` : ""}`
      : m.breakevenState === "none" ? "盈亏平衡：无（净信用不为正）" : null;
    const source = (em.iv_source_cn || isNum(em.iv_pct)) ? `σ 来源：${em.iv_source_cn || "ATM IV"}${isNum(em.iv_pct) ? ` ${pct(em.iv_pct)}` : ""}` : null;
    rows.push({ key: "buffer", label: "缓冲",
      big: isNum(em.distance_in_moves) ? `${em.distance_in_moves.toFixed(2)} 倍预期波动` : `短腿距现价 ${pct(em.short_distance_pct, 2)}`,
      chip: em.band_cn || null, chipCls: em.band === "narrow" || em.band === "inside" ? "warn" : "",
      subs: [{ text: [short, be, source].filter(Boolean).join("；") }].filter((x) => x.text) });
  }
  const p = rd.pricing;
  if (p && typeof p === "object") {
    if (!isNum(p.net_credit_usd)) {
      const feeUnknown = !isNum(p.fee_usd) && !isNum(p.fee_btc);
      rows.push({ key: "compensation", label: "补偿", big: feeUnknown ? "费用未知" : "净信用未能计算",
        subs: [{ text: (feeUnknown ? "费用未知，补偿不计算" : "报价不足，补偿不计算")
          + (isNum(p.credit_usd) ? `；可见信用 ${usd(p.credit_usd)} 美元（未扣费）` : "") }] });
    } else {
      const subs = [];
      if (isNum(p.edge_usd)) {
        const parts = isNum(p.spread_cost_usd) && isNum(p.fee_cost_usd)
          ? ` = 买卖价差 ${signedUsd(p.spread_cost_usd)}（挂单接近中间价可部分收回）+ 费用 ${signedUsd(p.fee_cost_usd)}${p.fee_assumed ? "（费用按公开上限假设）" : ""}`
          : p.fee_assumed ? "（费用按公开上限假设）" : "";
        subs.push({ text: `比市场估价 ${signedUsd(p.edge_usd)} 美元${parts}` });
      } else {
        subs.push({ text: `两腿标记价缺失，市场估价不计算${p.fee_assumed ? "（费用按公开上限假设）" : ""}` });
      }
      if (isNum(p.skew_cost_usd)) {
        const pts = isNum(p.skew_vol_pts) ? `保护腿 mark IV 比短腿${p.skew_vol_pts >= 0 ? "高" : "低"} ${Math.abs(p.skew_vol_pts).toFixed(1)} 点` : "两腿 mark IV 不同";
        subs.push({ text: p.skew_cost_usd > 0
          ? `市场估价已含偏斜：${pts}，约 ${usd(p.skew_cost_usd)} 美元（结构性，执行收不回，不在上面的差额里）`
          : `市场估价已含偏斜：${pts}，没有带来额外的保护成本` });
      }
      rows.push({ key: "compensation", label: "补偿", big: `净信用 ${usd(p.net_credit_usd)} 美元`, subs,
        title: p.fee_basis_cn || null });
    }
    if (isNum(p.loss_at_protection_usd)) {
      const text = [isNum(p.worst_to_net_ratio) ? `约为净信用的 ${Math.abs(p.worst_to_net_ratio).toFixed(1)} 倍` : null, p.tail_cn || null]
        .filter(Boolean).join("；");
      rows.push({ key: "worst", label: "最坏", big: `保护腿处亏 ${usd(Math.abs(p.loss_at_protection_usd))} 美元`,
        subs: text ? [{ text }] : [] });
    }
  }
  const cp = rd.clock_price;
  if (cp && isNum(cp.iv_clock_adjusted)) {
    const ref = isNum(cp.ref_cycle_start) ? `本周期 ${hhUtc(cp.ref_at_ms) || "08:00"} UTC 为 ${pct(cp.ref_cycle_start)}` : null;
    const rv = isNum(cp.rv24_clock_adjusted) ? `近 24 小时实现（同口径）${pct(cp.rv24_clock_adjusted)}`
      : isNum(cp.rv24_pct) ? `近 24 小时实现 ${pct(cp.rv24_pct)}（未经时钟校正）` : null;
    const subs = [];
    if (ref || rv) subs.push({ text: [ref, rv].filter(Boolean).join("；") });
    if (cp.basis_cn) subs.push({ text: cp.basis_cn, muted: true });
    rows.push({ key: "clock", label: "单位风险价格", big: `时钟校正 IV ${pct(cp.iv_clock_adjusted)}`, subs, title: cp.meaning_cn || null });
  }
  return rows.length ? rows : null;
}

/** Map model: spot, both books, σ, walls, markers and zones on one price axis. */
export function mapModel(view, sides) {
  const spot = get(view, "market_context.current_price_usd") ?? get(view, "btc_map.current_price_usd")
    ?? sides.put?.spot ?? sides.call?.spot;
  const ref = sides.put?.snap ? sides.put : sides.call;
  const info = gexInfo(view, ref?.candidate);
  const model = { spot, sigmaPct: ref?.sigmaPct ?? null, sigmaSource: sigmaSource(ref), sides: {}, walls: [], markers: [], zones: [], levels: [] };
  for (const k of ["put", "call"]) {
    const sm = sides[k];
    if (sm && sm.snap) model.sides[k] = { short: sm.short, long: sm.long, breakeven: sm.breakeven, beOutsidePct: sm.beOutsidePct, beMovePct: sm.beMovePct };
    else {
      const gaps = get(sm, "candidate.discovery.gap_reasons") || [];
      model.missing = { ...model.missing, [k]: gaps.length ? gaps.map(reasonCn).join("、") : "本轮没有可展示的候选" };
    }
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
  // This expiry's own structure is drawn as full-height lines like the spot line (model.levels);
  // the other bases stay as glyphs in their tracks. Names follow one convention: 墙 = the largest
  // dollar Gamma strike of a side, 持仓峰 = the largest open-interest strike of a side.
  const prof = expiryProfile(view, String(ref?.shortName || "").split("-")[1]);
  model.expiry = prof ? { expiry: prof.expiry, stale: !!prof.stale, at: prof.computed_at_ms } : null;
  model.levels = [];
  if (prof) {
    const src = `Deribit 公开数据，${prof.expiry} 到期，${prof.stale ? "数据偏旧" : "5 分钟更新"}；Gamma 不区分做市商方向`;
    const w = prof.walls || {};
    if (isNum(w.pin)) model.levels.push({ price: w.pin, role: "pin", name: "Pin", label: `本期 Pin（${prof.expiry}）`,
      note: `到期前预期波动范围内两侧合计 Gamma 最大的行权价；若做市商净多 Gamma，价格易在附近钉住。${src}`, basis: "本期" });
    for (const [side, name] of [["put", "Put"], ["call", "Call"]]) {
      const g = w[`gamma_${side}`], o = w[`oi_${side}`];
      if (isNum(g)) model.levels.push({ price: g, role: `wall_${side}`, name: `${name} 墙${g === o ? "（持仓峰同位）" : ""}`,
        label: `本期 ${name} 墙（${prof.expiry}）`, basis: "本期",
        note: `美元 Gamma 最大的 ${name} 行权价：对冲压力所在${g === o ? "；持仓量也在这里最大" : ""}。${src}` });
      if (isNum(o) && o !== g) model.levels.push({ price: o, role: `oi_${side}`, name: `${name} 持仓峰`,
        label: `本期 ${name} 持仓峰（${prof.expiry}）`, basis: "本期", note: `持仓量最大的 ${name} 行权价：仓位堆积处。${src}` });
    }
  }
  // "This expiry" is judged against the contract's own token, so it holds even without a profile.
  const token = String(ref?.shortName || "").split("-")[1]?.toUpperCase() || null;
  const ofThis = (e) => !!token && String(e || "").toUpperCase() === token;
  const ne = info.next_expiry || {};
  const thisExpiry = ofThis(ne.expiry);
  if (isNum(ne.max_pain_price)) {
    const item = { price: ne.max_pain_price, label: `${thisExpiry ? "本期" : "下一到期"}最大痛点（${ne.expiry || ""}）`,
      note: `该到期所有期权买方合计价值最小的价格；gexmonitor 口径${isNum(ne.hours_to_expiry) ? `，${ne.hours_to_expiry} 小时后到期` : ""}` };
    if (thisExpiry) model.levels.push({ ...item, role: "max_pain", name: "最大痛点", basis: "本期" });
    else model.markers.push({ ...item, short: "近痛点", glyph: "triangle", color: "var(--warn)", basis: "下一到期" });
  }
  const flip = pool.find((r) => r.role === "EFFECTIVE_FLIP");
  const flipPrice = flip ? (flip.raw_price ?? flip.display_center) : get(view, "market_context.market_facts.space_context.anchor.effective_flip_point");
  if (isNum(flipPrice)) model.levels.push({ price: flipPrice, role: "flip", name: "Gamma 翻转位", basis: "全期限",
    label: "Gamma 翻转位（全期限）", note: "全部到期合计净 Gamma 由负转正的价格，价格在其上方为正 Gamma 区；全期限口径，不是本期，名义坐标" });
  if (isNum(info.magnet_price)) model.markers.push({ price: info.magnet_price, label: "全期限 Gamma 磁吸位（不是本期 Pin）", short: "全期限磁吸", glyph: "triangle", color: "var(--ink-3)", basis: "全期限" });
  const target = info.max_pain_target_expiry;
  if (isNum(info.max_pain_price) && ofThis(target)) {
    // gexmonitor's main expiry is this contract's: one 本期 level, never a second marker
    if (!model.levels.some((l) => l.role === "max_pain")) model.levels.push({ price: info.max_pain_price, role: "max_pain", name: "最大痛点",
      basis: "本期", label: `本期最大痛点（${target}）`, note: "该到期所有期权买方合计价值最小的价格；gexmonitor 口径" });
  } else if (isNum(info.max_pain_price)) {
    const day = (String(target || "").match(/^\d{1,2}[A-Z]{3}/i) || [])[0];
    model.markers.push({ price: info.max_pain_price, label: `最大痛点（${target || "主到期"}）`,
      short: `${day ? `${day.toUpperCase()} ` : "主到期"}痛点`, glyph: "triangle", color: "var(--warn)",
      note: "主到期（gexmonitor 选定）口径，不是本期", basis: "主到期" });
  }
  // This expiry's own profile for the map: the top dollar-Gamma strikes of each side (the largest
  // is the wall), the open-interest peaks, the pin and this expiry's max pain.
  model.profile = prof ? {
    expiry: prof.expiry, stale: !!prof.stale,
    put: (get(prof, "top.gamma_put") || []).filter((r) => isNum(r.strike) && isNum(r.value)).map((r) => ({ strike: r.strike, gex: r.value })),
    call: (get(prof, "top.gamma_call") || []).filter((r) => isNum(r.strike) && isNum(r.value)).map((r) => ({ strike: r.strike, gex: r.value })),
    wall: { put: get(prof, "walls.gamma_put"), call: get(prof, "walls.gamma_call") },
    oi: { put: get(prof, "walls.oi_put"), call: get(prof, "walls.oi_call") },
    pin: get(prof, "walls.pin"),
    maxPain: (model.levels.find((l) => l.role === "max_pain") || {}).price ?? null,
    note: `Deribit 公开数据，${prof.expiry} 到期，${prof.stale ? "数据偏旧" : "5 分钟更新"}；Gamma 不区分做市商方向`,
  } : null;
  const costLabel = { CP_STH: "短期持有者成本", CP_LTH: "长期持有者成本", CP_ALL: "全体持有者成本" };
  const costShort = { CP_STH: "短期成本", CP_LTH: "长期成本", CP_ALL: "全体成本" };
  // MAP's display pool may select only some cost levels; the background carries all three.
  const cb = get(view, "btc_map.background.inventory.metrics.cost_basis") || {};
  const cost = { CP_STH: cb.cp_sth_usd, CP_LTH: cb.cp_lth_usd, CP_ALL: cb.cp_all_usd };
  for (const r of pool) if (costLabel[r.role] && !isNum(cost[r.role])) cost[r.role] = r.raw_price ?? r.display_center;
  for (const [role, price] of Object.entries(cost)) {
    if (isNum(price)) model.markers.push({ price, label: costLabel[role], short: costShort[role], glyph: "diamond",
      color: "var(--ink-2)", track: "trade", basis: "链上", note: `链上成本口径（${cb.latest_observation_date || "日期未知"}），与期权价格不同基准` });
  }
  // KPF zones come from closed daily trade archives; their cutoff is the last closed UTC day.
  const kpfEnd = Math.max(...pool.filter((r) => r.role === "KPF_BASIN" && isNum(r.observation_end_ms)).map((r) => r.observation_end_ms));
  model.kpfAsOf = Number.isFinite(kpfEnd) ? kpfEnd : null;
  const kpfDate = model.kpfAsOf ? new Date(model.kpfAsOf).toISOString().slice(5, 10) : null;
  for (const r of pool) {
    if (r.role === "KPF_BASIN" && isNum(r.raw_low) && isNum(r.raw_high)) {
      model.zones.push({ lo: r.raw_low, hi: r.raw_high, label: "KPF 成交接受区",
        note: `Binance 永续成交口径，名义坐标；成交数据截至 ${kpfDate || "未知"}（UTC 日终）`,
        containsShort: ["put", "call"].some((k) => model.sides[k] && model.sides[k].short >= r.raw_low && model.sides[k].short <= r.raw_high) });
    }
  }
  model.ladder = ladder(model);
  return model;
}

const BASIS_CN = { 本期: "本期期权", 全期限: "全期限 GEX", 主到期: "主到期期权", 下一到期: "下一到期期权", 周度: "周度 GEX", "0DTE": "0DTE GEX", 链上: "链上（背景）", 成交: "成交（背景）" };

/** Every level on one list, split at the spot, with its distance in % and in expected moves. */
function ladder(model) {
  const rows = [];
  const add = (price, name, basis, extra = {}) => { if (isNum(price)) rows.push({ price, name, basis, basisCn: BASIS_CN[basis] || basis, ...extra }); };
  for (const l of model.levels) add(l.price, l.label, l.basis, { key: l.basis === "本期" });
  for (const m of model.markers) {
    add(m.price, m.label, m.basis || "全期限");
  }
  for (const w of model.walls) if (w.kind === "all") add(w.price, w.label, "全期限");
  // the largest weekly and 0DTE wall of each sign, the rest stay on the map
  for (const kind of ["weekly", "zero_dte"]) {
    for (const sign of [1, -1]) {
      const best = model.walls.filter((w) => w.kind === kind && isNum(w.gex) && Math.sign(w.gex) === sign)
        .sort((a, b) => Math.abs(b.gex) - Math.abs(a.gex))[0];
      if (best) add(best.price, `${best.label}（最大）`, kind === "weekly" ? "周度" : "0DTE", { gex: best.gex });
    }
  }
  for (const z of model.zones) add((z.lo + z.hi) / 2, `${z.label} ${kusd(z.lo)}–${kusd(z.hi)}`, "成交");
  const sig = isNum(model.sigmaPct) && model.sigmaPct > 0 ? model.sigmaPct : null;
  for (const r of rows) {
    r.distPct = (r.price / model.spot - 1) * 100;
    r.moves = sig ? r.distPct / sig : null;
  }
  return {
    below: rows.filter((r) => r.price < model.spot).sort((a, b) => b.price - a.price),
    above: rows.filter((r) => r.price >= model.spot).sort((a, b) => a.price - b.price),
  };
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
  public_taker_capped_daily_delivery_zero_at_mark: "公开 taker 费率（有上限，按标记价估算），交割费按 0",
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
    // continuous: the workbench collects it; map_service: the map data service on this host;
    // on_demand: written when a quote is frozen; fmz: collected inside FMZ and consumed through
    // its facts; idle: registered but not consumed (not connected, not produced, or no source).
    const kind = NOT_CONSUMED.has(id) || NOT_PRODUCED.has(id) || st.includes("NOT_CONNECTED") || st.includes("PLANNED") ? "idle"
      : COLLECTOR_KIND[p.collector_id] || (first ? "continuous" : "idle");
    const collected = kind === "continuous" || kind === "map_service";
    const shown = collected ? level : kind === "on_demand" ? "info" : "off";
    return { id, status: p.status, level: shown, kind, collected, age, ttl: ud.ttl_ms,
      // idle rows say why they are not consumed; a few live rows carry a reading note instead
      idleReason: IDLE_REASON[id] || (kind === "idle" ? "采集方尚未接入，不消费" : null),
      reasons: ud.reason_codes || [], consumers: p.affected_consumers || [], observed: first && first.observation_end_ms };
  }).sort((a, b) => rank[a.level] - rank[b.level] || a.id.localeCompare(b.id));
}
