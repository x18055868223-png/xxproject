// Four core elements of the underwriting decision, plus slow background.
// Readings come from the FMZ producer's own models (TMV, CVD, M-DIE, macro)
// and the MAP background. The per-side relation is a fixed display rule:
// an upward lean favours a Put book and works against a Call book.
// Each metric row is {label, value, viz?, gap?}; viz is a small inline chart.
import { get, isNum, usd, pct, compactUsd } from "./format.js";
import { diverging, gauge, split, positions } from "./viz.js";

const TREND = { strong_bullish_bias: 2, bullish_bias: 1, neutral: 0, bearish_bias: -1, strong_bearish_bias: -2 };
const TREND_CN = { strong_bullish_bias: "强偏多", bullish_bias: "偏多", neutral: "中性", bearish_bias: "偏空", strong_bearish_bias: "强偏空" };
const FLOW_CN = { micro_bullish_pressure: "主动买压", micro_bullish_lean: "买方略强", micro_neutral: "均衡",
  micro_bearish_lean: "卖方略强", micro_bearish_pressure: "主动卖压" };
const GAMMA_CN = { POSITIVE_GAMMA_PINNING: "正 Gamma 钉住", NEGATIVE_GAMMA_AMPLIFYING: "负 Gamma 放大", NEGATIVE_GAMMA: "负 Gamma 放大",
  POSITIVE_GAMMA: "正 Gamma", TRANSITION: "翻转位附近过渡", UNKNOWN: "未知" };
const GAMMA_NOTE = {
  POSITIVE_GAMMA_PINNING: "正 Gamma 下做市商反向对冲，抑制波动。",
  NEGATIVE_GAMMA_AMPLIFYING: "负 Gamma 下做市商顺向对冲，放大波动。",
  TRANSITION: "现价贴近翻转位，Gamma 方向不稳定，波动特征可能切换。",
};
const MACRO_CN = { "Mild Tailwind": "温和顺风", "Strong Tailwind": "强顺风", "Mild Headwind": "温和逆风", "Strong Headwind": "强逆风", Neutral: "中性" };
const MACRO_KEY = { VOLQ: "纳指波动率 VXN", DXY: "美元指数 DXY", US10Y: "美债 10Y" };
const GATE_WHY_CN = {
  SPACE_GATE_NET_GAMMA_NOT_POSITIVE: "净 Gamma 不为正", NET_GAMMA_NON_POSITIVE: "净 Gamma 不为正",
  SPACE_GATE_NET_GAMMA_NOT_FRESH: "数据不新鲜", SPACE_GATE_NET_GAMMA_MISSING: "缺数据",
  SPACE_GATE_GGR_TRANSITION: "Gamma 体制过渡中", SPACE_GATE_GGR_CONFLICT: "体制与净 Gamma 冲突",
  SPACE_GATE_GGR_DATA_NOT_OK: "体制数据异常", SPACE_GATE_NET_GAMMA_NOT_OK: "净 Gamma 未通过",
  SPACE_GATE_ANCHOR_NOT_VALID: "锚点未成立", SPACE_GATE_ANCHOR_SCORE_LOW: "锚点评分低于 60",
  SPACE_GATE_ANCHOR_NOT_FRESH: "锚点数据不新鲜", SPACE_GATE_ANCHOR_SCORE_MISSING: "锚点评分缺失",
};
const ANCHOR_LABEL_CN = { Strong: "强", Tight: "紧", Moderate: "中等", Loose: "松散", Weak: "弱", Lost: "失锚" };
const SHAPE_CN = { NO_MOVE: "无明显移动", IMPULSE_SHIFT: "脉冲跳变", DRIFT_RUN: "持续推进", CHOPPY_DRIFT: "震荡漂移" };

const sign = (v) => (v > 0 ? 1 : v < 0 ? -1 : 0);
export const signed = (v, d = 2) => (isNum(v) ? `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}` : "—");
const row = (label, value, viz = null) => ({ label, value: value ?? "缺口", viz, gap: value == null });
/** A reading that is not wrong, only not available yet (warm-up, nothing to measure). */
const pending = (label, value) => ({ label, value, viz: null, pending: true });

/** Direction lean (+1 up, -1 down, 0 none) turned into a relation for one book. */
export function relationFor(lean, side, { background = false } = {}) {
  if (lean == null) return { cls: "gap", text: "缺口" };
  if (lean === 0) return { cls: "", text: "中性" };
  const good = (lean > 0) === (side === "put");
  return { cls: good ? "ok" : "bad", text: `${good ? "有利" : "不利"}${background ? "（背景）" : ""}` };
}

export function coreFactors(view, spot) {
  const mf = get(view, "market_context.market_facts") || {};
  const vp = mf.volume_price || {};
  const gate = view.space_gate || {};
  const space = mf.space_context || {};
  const info = space.gex_info || {};
  const gamma = mf.gamma_regime || {};
  const flip = get(space, "anchor.effective_flip_point") ?? get(mf, "anchor.effective_flip_point");
  const anchor = get(gate, "anchor.score") ?? get(mf, "anchor.anchor_gravity_ref_score");

  // 1. space structure
  const nearUp = [], nearDown = [];
  for (const b of ["zero_dte", "weekly"]) for (const w of Object.values(get(info, `near_term_walls.${b}.walls`) || {})) {
    if (!isNum(w?.strike) || !isNum(spot)) continue;
    (w.strike >= spot ? nearUp : nearDown).push(w.strike);
  }
  const flipOff = isNum(flip) && isNum(spot) ? (spot - flip) / flip * 100 : null;
  const reasons = [...(gate.reason_codes || [])];
  // The flip anchor (spot on the flip axis within a radius), described as it is.
  const netV = get(gate, "net_gamma.value");
  const anchorLabel = ANCHOR_LABEL_CN[get(space, "anchor.anchor_gravity_ref_label")] || null;
  const dev = get(space, "anchor.normalized_deviation");
  const anchorText = isNum(anchor)
    ? `${anchor.toFixed(1)} / 100${anchorLabel ? `（${anchorLabel}）` : ""}${isNum(dev) ? `，现价在翻转位${dev < 0 ? "下方" : "上方"}约 ${Math.abs(dev).toFixed(2)} 个锚定半径` : ""}`
    : null;
  // Evaluation only (2026-10-13): the space structure is described and scored, never gated.
  // A transition near the flip is not a failure; it may as well be price pinned by the flip.
  const nativeState = gate.producer_state;
  const nativeWhy = [...new Set((gate.producer_reason_codes || []).map((c) => GATE_WHY_CN[c]).filter(Boolean))];
  const regimeText = {
    POSITIVE_GAMMA_PINNING: "正 Gamma 钉住（对冲逆势，波动倾向收敛）",
    NEGATIVE_GAMMA_AMPLIFYING: "负 Gamma 放大（对冲顺势，波动倾向放大）",
    TRANSITION: "翻转位附近（可能被翻转位锚住，也可能正在切换体制）",
  };
  const spaceF = {
    key: "space", title: "空间结构", lean: 0,
    headline: isNum(anchor) ? `锚点 ${anchor.toFixed(0)} / 100` : "锚点缺失",
    headCls: "",
    rows: [
      reasons.includes("SPACE_GATE_ANCHOR_SCORE_MISSING") && !isNum(anchor) ? pending("锚点评分", "预热中，重启后需积累样本")
        : row("锚点评分", anchorText, isNum(anchor) ? gauge(anchor, 100) : null),
      row("净 Gamma", isNum(netV) ? `${compactUsd(netV)}（${netV > 0 ? "为正" : netV < 0 ? "为负" : "为零"}）` : null),
      row("Gamma 体制", gamma.regime ? (regimeText[gamma.regime] || GAMMA_CN[gamma.regime] || "其他状态") : null),
      row("相对翻转位", isNum(flipOff) ? `现价在翻转位 ${usd(flip)} ${flipOff < 0 ? "下方" : "上方"} ${Math.abs(flipOff).toFixed(2)}%` : null,
        isNum(flipOff) ? diverging(flipOff, 3) : null),
      row("最近墙位", nearUp.length || nearDown.length
        ? `上 ${nearUp.length ? usd(Math.min(...nearUp)) : "—"}，下 ${nearDown.length ? usd(Math.max(...nearDown)) : "—"}` : null),
    ],
    note: `空间结构只作评价，不作准入（2026-10-13 起）。锚点评分衡量现价被翻转位锚住的程度（越高越贴近）。${nativeState ? `FMZ 原生空间门此刻${nativeState === "STRUCTURE_ELIGIBLE" ? "放行" : `关闭${nativeWhy.length ? `（${nativeWhy.join("、")}）` : ""}`}，仅作参考。` : ""}空间不提供方向。`,
    missing: !gate.state,
  };

  // 2. volume-price path
  const t24 = vp.tmvf_24h || {}, t48 = vp.tmvf_48h || {};
  const mdie = mf.m_die || {};
  const pe = get(mdie, "components.path_efficiency.raw.efficiency");
  const persist = get(mdie, "components.directional_persistence.raw.same_direction_ratio");
  const mSigned = isNum(mdie.score) ? (mdie.direction === "DOWN" ? -mdie.score : mdie.score) : null;
  const noMove = mdie.move_shape === "NO_MOVE" || mdie.level === "NO_DIRECTIONAL_MOVE";
  const tLean = [t24.final_state, t48.final_state].filter((x) => x in TREND).map((x) => TREND[x]);
  const tmv = (t) => (t.data_ready ? (t.tmv_final ?? t.tmv_core) : null);
  const pathF = {
    key: "path", title: "量价路径",
    lean: tLean.length ? sign(tLean.reduce((a, b) => a + b, 0)) : null,
    headline: `${TREND_CN[t24.final_state] || "未就绪"} / ${TREND_CN[t48.final_state] || "未就绪"}`,
    rows: [
      row("TMV 24h", isNum(tmv(t24)) ? signed(tmv(t24)) : null, isNum(tmv(t24)) ? diverging(tmv(t24), 1) : null),
      row("TMV 48h", isNum(tmv(t48)) ? signed(tmv(t48)) : null, isNum(tmv(t48)) ? diverging(tmv(t48), 1) : null),
      row("15 分钟动量", isNum(mSigned) ? `${signed(mSigned)}${SHAPE_CN[mdie.move_shape] ? `，${SHAPE_CN[mdie.move_shape]}` : ""}` : null,
        isNum(mSigned) ? diverging(mSigned, 1) : null),
      // M-DIE measures path shape only after a move; with no move there is nothing to measure.
      noMove ? pending("路径效率", "近 15 分钟无明显移动，不计算")
        : row("路径效率", isNum(pe) ? `${(pe * 100).toFixed(0)}%${pe < 0.35 ? "，来回多" : pe > 0.85 ? "，单向干净" : ""}` : null,
          isNum(pe) ? gauge(pe, 1, { threshold: 0.35 }) : null),
      noMove ? pending("同向占比", "无移动，不计算")
        : row("同向占比", isNum(persist) ? `${(persist * 100).toFixed(0)}%` : null, isNum(persist) ? gauge(persist, 1, { threshold: 0.7 }) : null),
    ],
    note: vp.window_conflict ? "24h 与 48h 窗口方向冲突。"
      : (isNum(mSigned) && tLean.length && sign(mSigned) !== sign(tLean[0]) && sign(mSigned) !== 0 ? "短线 15 分钟动量与 24/48h 趋势方向相反。" : "路径效率 = 15 分钟净位移 ÷ 路径总长。"),
    missing: !t24.data_ready && !t48.data_ready,
  };

  // 3. active trades (real flow)
  const mfl = vp.micro_flow || {};
  const f4 = mfl.fast_4h || {}, f12 = mfl.slow_12h || {}, comb = mfl.combined || {};
  const flowDir = String(comb.direction || "");
  const cvdSpan = Math.max(Math.abs(f4.cvd_sum || 0), Math.abs(f12.cvd_sum || 0), 1) * 1.15;
  const warming = (f, h) => (isNum(f.coverage_hours) && !f.data_ready ? `积累中，已覆盖 ${f.coverage_hours.toFixed(1)} / ${h} 小时` : null);
  const flowRow = (label, f, hrs, text, viz, short = false) => (f.data_ready ? row(label, text(), viz())
    : warming(f, hrs) ? pending(label, short ? "积累中" : warming(f, hrs)) : row(label, null));
  const backed = (f) => (isNum(f.cvd_sum) && isNum(f.momentum_return_pct) && sign(f.cvd_sum) !== 0 && sign(f.momentum_return_pct) !== 0
    ? (sign(f.cvd_sum) === sign(f.momentum_return_pct) ? "同向" : "背离") : "");
  const flowF = {
    key: "flow", title: "主动成交",
    lean: comb.data_ready ? (flowDir.includes("bull") ? 1 : flowDir.includes("bear") ? -1 : 0) : null,
    headline: comb.data_ready ? (FLOW_CN[comb.state] || "方向不明") : "积累中",
    rows: [
      flowRow("4h 主动净买", f4, 4, () => `${signed(f4.cvd_sum, 0)} BTC`, () => diverging(f4.cvd_sum, cvdSpan)),
      flowRow("4h 价格", f4, 4, () => `${signed(f4.momentum_return_pct)}% ${backed(f4)}`, () => diverging(f4.momentum_return_pct, 1.5), true),
      flowRow("12h 主动净买", f12, 12, () => `${signed(f12.cvd_sum, 0)} BTC`, () => diverging(f12.cvd_sum, cvdSpan)),
      flowRow("12h 价格", f12, 12, () => `${signed(f12.momentum_return_pct)}% ${backed(f12)}`, () => diverging(f12.momentum_return_pct, 1.5), true),
    ],
    note: "永续主动成交净额；与价格同向表示推动有成交支撑，背离表示价格走在成交前面。",
    missing: !comb.data_ready && !isNum(f4.coverage_hours),
  };

  // 4. macro beta
  const mp = mf.macro_pressure || {};
  const regime = String(mp.macro_regime || "");
  const comp = (c) => {
    const bps = c.scoring_unit === "bps";
    const v = bps ? c.scoring_value : c.change_pct_3d;
    return row(MACRO_KEY[c.key] || c.key,
      `${isNum(c.current_close) ? c.current_close.toFixed(2) : "—"}，3 日 ${bps ? `${signed(v, 1)}bp` : `${signed(v)}%`}`,
      isNum(v) ? diverging(v, bps ? 15 : c.key === "DXY" ? 1 : 5, { good: -1 }) : null);
  };
  const macroF = {
    key: "macro", title: "宏观环境",
    lean: regime ? (regime.includes("Tailwind") ? 1 : regime.includes("Headwind") ? -1 : 0) : null,
    background: true,
    headline: MACRO_CN[regime] || (regime ? "其他状态" : "缺口"),
    rows: [
      row("综合得分", isNum(mp.macro_score) ? `${signed(mp.macro_score)}，负值为压力缓和` : null,
        isNum(mp.macro_score) ? diverging(mp.macro_score, 1, { good: -1 }) : null),
      ...(mp.component_summaries || []).map(comp),
    ],
    note: mp.data_status && mp.data_status !== "full_live" ? "宏观数据不完整，读数只作参考。" : "宏观环境只调整背景读数，不单独确认方向。绿色为风险压力下降。",
    missing: !regime,
  };
  // Spatial position is per side: where each short leg sits on its own adverse path.
  // Computed by the program from each frozen snapshot; Gamma decides, KPF is described only.
  const STATE_SHORT = { favorable: "有利", unfavorable: "不利", unknown: "不明" };
  const STATE_CLS = { favorable: "ok", unfavorable: "bad", unknown: "" };
  const lines = ["put", "call"].map((side) => {
    const sp = (view.candidates || []).find((c) => c.side === side)?.spatial_position;
    if (!sp || sp.error) return null;
    return { side, name: side === "put" ? "Put" : "Call", state: sp.state, cls: STATE_CLS[sp.state] ?? "",
      text: STATE_SHORT[sp.state] || "不明", short: sp.short_strike_usd, reason: sp.reason_cn,
      kpf: sp.kpf?.description_cn || null,
      moves: isNum(sp.distance_in_moves) ? sp.distance_in_moves : null, distance: sp.distance_pct };
  }).filter(Boolean);
  const positionF = {
    key: "position", title: "空间位置",
    lean: null, perSide: Object.fromEntries(lines.map((l) => [l.side, l])),
    headline: lines.length ? lines.map((l) => `${l.name} ${l.text}`).join(" · ") : "缺口",
    lines, rows: [],
    note: "沿各侧不利方向看价格先遇到什么：Gamma 结构（墙、Flip）决定判断，KPF 成交区只作描述，链上成本不进入。只描述保单责任位置，不预测方向；尚未经交割数据验证。",
    missing: !lines.length,
  };
  return [spaceF, pathF, flowF, macroF, positionF];
}

export function backgroundItems(view, spot) {
  const mf = get(view, "market_context.market_facts") || {};
  const vp = mf.volume_price || {};
  const bg = get(view, "btc_map.background") || {};
  const info = get(mf, "space_context.gex_info") || {};
  const items = [];
  const usable = (x) => x && x.fact_state === "usable";

  const fund = vp.funding || {};
  const fm = get(bg, "financing.metrics.funding") || {};
  const settled = fm.window_24h || fm.previous_24h || {};
  items.push({ title: "资金费", state: get(fund, "semantics.fee_bias_cn") || null,
    rows: [row("最新费率", isNum(fund.last_rate) ? `${signed(fund.last_rate * 100, 4)}%` : null,
        isNum(fund.last_rate) ? diverging(fund.last_rate, 0.0001) : null),
      row("24h 实际结算", isNum(settled.settled_rate_sum) ? `${signed(settled.settled_rate_sum * 100, 4)}%（${settled.settled_count} 次）` : null,
        isNum(settled.settled_rate_sum) ? diverging(settled.settled_rate_sum, 0.0003) : null),
      row("拥挤", get(fund, "semantics.is_crowded") ? "已拥挤" : "未拥挤，阈值 ±0.01%")],
    note: "刻度：单次费率 ±0.01% 为拥挤阈值。" });

  const oi = get(bg, "financing.metrics.oi.oi_24h_window") || get(bg, "financing.metrics.oi_source_values.oi_24h_window") || {};
  items.push({ title: "永续持仓 OI", state: isNum(oi.current_native_btc) ? `${usd(oi.current_native_btc)} BTC` : null,
    rows: [row("24h 变化", isNum(oi.change_native_pct) ? `${signed(oi.change_native_pct)}%（${signed(oi.change_native_btc, 0)} BTC）` : null,
      isNum(oi.change_native_pct) ? diverging(oi.change_native_pct, 3) : null)],
    note: "原生 BTC 口径，不受价格换算影响。" });

  const etfM = get(bg, "allocation.metrics") || {};
  const etf = isNum(etfM.f3_usd_m) ? etfM : (get(etfM, "source_values.metrics") || etfM);
  const etfSpan = Math.max(Math.abs(etf.f3_usd_m || 0), Math.abs(etf.f7_usd_m || 0), (etf.q_abs_usd_m_prior60 || 0) * 3, 1);
  // Trading days, USD (Farside). The latest day is shown on its own, as data sites lead with it.
  const etfRows = get(etfM, "source_values.rows") || [];
  const lastDay = etfRows.find((r) => r.date === etf.latest_complete_date && isNum(r.total_usd_m));
  // Farside publishes USD; BTC is converted at the current spot, so it is approximate.
  const asBtc = (usdM) => (isNum(usdM) && isNum(spot) && spot > 0 ? usdM * 1e6 / spot : null);
  const btcN = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${usd(Math.abs(v))}`;
  const flow = (usdM) => (isNum(asBtc(usdM)) ? `约 ${btcN(asBtc(usdM))} BTC（${signed(usdM, 1)} 百万美元）` : `${signed(usdM, 1)} 百万美元`);
  const perDay = (v) => (isNum(asBtc(v)) ? `，日均约 ${btcN(asBtc(v))} BTC` : isNum(v) ? `，日均 ${signed(v, 1)} 百万美元` : "");
  items.push({ title: "美国现货 BTC ETF 资金", state: isNum(etf.f3_usd_m) ? (etf.f3_usd_m < 0 ? "近 3 个交易日净流出" : "近 3 个交易日净流入") : null,
    stale: bg.allocation && !usable(bg.allocation) ? `用途时效未通过，最新完整日 ${etf.latest_complete_date || "—"}` : null,
    rows: [row(`最新交易日 ${String(etf.latest_complete_date || "").slice(5) || "—"}`, lastDay ? flow(lastDay.total_usd_m) : null,
        lastDay ? diverging(lastDay.total_usd_m, etfSpan) : null),
      row("近 3 个交易日", isNum(etf.f3_usd_m) ? `${flow(etf.f3_usd_m)}${perDay(etf.v3_usd_m_per_day)}` : null, isNum(etf.f3_usd_m) ? diverging(etf.f3_usd_m, etfSpan) : null),
      row("近 7 个交易日", isNum(etf.f7_usd_m) ? `${flow(etf.f7_usd_m)}${perDay(etf.v7_usd_m_per_day)}` : null, isNum(etf.f7_usd_m) ? diverging(etf.f7_usd_m, etfSpan) : null),
      row("比平时", isNum(etf.u3) && isNum(etf.q_abs_usd_m_prior60)
        ? `近 3 日日均是平时的 ${Math.abs(etf.u3 * 100).toFixed(0)}%（平时日均约 ${isNum(asBtc(etf.q_abs_usd_m_prior60)) ? `${usd(asBtc(etf.q_abs_usd_m_prior60))} BTC` : `${etf.q_abs_usd_m_prior60.toFixed(0)} 百万美元`}）` : null,
        isNum(etf.u3) ? diverging(etf.u3, 1.5) : null)],
    note: "按美股交易日计。Farside 发布美元金额，BTC 数量按现价折算（约数，与 Coinglass 逐日按当日价格的数量相差通常在 2% 内）；平时 = 前 60 个交易日的日均流量规模。" });

  const cb = get(bg, "inventory.metrics.cost_basis") || {};
  const rel = (p) => (isNum(p) && isNum(spot) ? `${usd(p)}，现价${spot >= p ? "高" : "低"} ${Math.abs((spot - p) / p * 100).toFixed(1)}%` : null);
  items.push({ title: "链上成本", state: cb.latest_observation_date ? `截至 ${cb.latest_observation_date}` : null,
    stale: bg.inventory && !usable(bg.inventory) ? "用途时效未通过" : null,
    wide: positions([{ price: cb.cp_sth_usd, label: "短期" }, { price: cb.cp_all_usd, label: "全体" }, { price: cb.cp_lth_usd, label: "长期" }], spot),
    rows: [row("短期持有者", rel(cb.cp_sth_usd)), row("全体持有者", rel(cb.cp_all_usd)), row("长期持有者", rel(cb.cp_lth_usd))],
    note: "链上口径与期权价格不同基准，只作位置参照。" });

  const rp = get(bg, "inventory.metrics.realized_pnl") || {};
  items.push({ title: "已实现盈亏（14 日）", state: isNum(rp.loss_share_14d) ? `亏损占 ${(rp.loss_share_14d * 100).toFixed(1)}%` : null,
    rows: [row("盈利 / 亏损", isNum(rp.profit_14d_usd) && isNum(rp.loss_14d_usd)
      ? `${(rp.profit_14d_usd / 1e9).toFixed(2)} / ${(rp.loss_14d_usd / 1e9).toFixed(2)} 十亿美元` : null, split(rp.profit_14d_usd, rp.loss_14d_usd))],
    note: "没有历史分位基准，不判断高低。" });

  const w = info.whale_24h;
  items.push({ title: "期权情绪", state: isNum(info.atm_iv) ? `ATM IV ${pct(info.atm_iv)}` : null,
    rows: [row("IV 分位", isNum(info.iv_percentile) ? `${info.iv_percentile}，DVOL ${info.dvol ?? "—"}` : null,
        isNum(info.iv_percentile) ? gauge(info.iv_percentile, 100) : null),
      row("成交 PCR", isNum(info.pcr) ? `${info.pcr}，持仓 ${info.pcr_oi ?? "—"}` : null, isNum(info.pcr) ? diverging(info.pcr - 1, 0.6) : null),
      row("24h 大宗", w ? `${w.trade_count} 笔，净 ${compactUsd(w.net_flow_usd)}，活跃 ${usd(w.active_strike)}` : null,
        w && isNum(w.net_flow_usd) ? diverging(w.net_flow_usd, 1e6) : null)],
    note: "PCR 以 1 为中线；PCR 与大宗没有历史基准，只读当前值。" });

  // Each series is read on its own: latest published value and date, and the change over its
  // own last 5 observations. A shared-date change would hold the daily rates back to the
  // weekly dollar index and can show a direction the rates no longer have.
  const ext = bg.external_conditions || {};
  const em = ext.metrics || {};
  const blocked = new Set((ext.missing || []).map((r) => String(r).split(":")[0]));
  const shortDate = (d) => (d ? String(d).slice(5) : "—");
  const own = (key) => {
    const obs = (get(em, `${key}_source_values.observations`) || []).filter((o) => isNum(o.value)).sort((a, b) => (a.date < b.date ? -1 : 1));
    const last = obs[obs.length - 1];
    const base = obs.length >= 6 ? obs[obs.length - 6] : null;
    return last ? { value: last.value, date: last.date, base } : null;
  };
  const extRow = (label, key, fmt, unit, scale) => {
    const o = own(key);
    if (!o) return row(label, null);
    const d = o.base ? (unit === "bp" ? (o.value - o.base.value) * 100 : (o.value / o.base.value - 1) * 100) : null;
    const change = isNum(d) ? `，比 ${shortDate(o.base.date)} ${unit === "bp" ? `${signed(d, 0)}bp` : `${signed(d)}%`}` : "";
    const text = `${fmt(o.value)}（${shortDate(o.date)}）${change}`;
    return blocked.has(key) ? row(label, `${text}，已超时效`) : row(label, text, isNum(d) ? diverging(d, scale, { good: -1 }) : null);
  };
  const dxy = (get(mf, "macro_pressure.component_summaries") || []).find((c) => c.key === "DXY") || {};
  const dxyDate = isNum(dxy.current_ts_ms) ? new Date(dxy.current_ts_ms).toISOString().slice(0, 10) : null;
  const dxyRow = isNum(dxy.current_close)
    ? row("美元指数 DXY", `${dxy.current_close.toFixed(2)}（${shortDate(dxyDate)}）${isNum(dxy.change_pct_3d) ? `，3 个交易日 ${signed(dxy.change_pct_3d)}%` : ""}`,
      isNum(dxy.change_pct_3d) ? diverging(dxy.change_pct_3d, 1, { good: -1 }) : null)
    : row("美元指数 DXY", null);
  const dates = [own("nominal_10y")?.date, dxyDate];
  const anyRow = ["nominal_10y", "real_10y"].some((k) => own(k)) || isNum(dxy.current_close);
  items.push({ title: "外部资本条件",
    state: anyRow ? `利率截至 ${shortDate(dates[0])} · 美元截至 ${shortDate(dates[1])}` : null,
    stale: blocked.size ? "部分序列超出使用时效，只展示。" : null,
    rows: anyRow ? [extRow("美债 10Y 名义", "nominal_10y", (v) => `${v.toFixed(2)}%`, "bp", 30),
      extRow("美债 10Y 实际", "real_10y", (v) => `${v.toFixed(2)}%`, "bp", 30),
      dxyRow] : [row("美元与美债", null)],
    note: "美债利率来自 FRED，按日发布、约滞后一个工作日，变化为各自最近 5 个观测日；DXY 为 ICE 美元指数（Yahoo DX-Y.NYB，与宏观环境卡同源），变化为 3 个交易日。红色为收紧（利率或美元上升），绿色为放松。" });
  return items;
}
