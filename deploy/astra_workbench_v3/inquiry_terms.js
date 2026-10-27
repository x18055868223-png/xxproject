// Chinese reading of the inquirer's text (docs/opus/14): the model writes case refs, field
// names and enum codes into its prose; the page shows them as Chinese meaning. Numbers,
// contract names and market abbreviations (IV, Gamma, OI, GEX, KPF, TMV, CVD, EDB…) stay.
// The original text stays in the element's title.
import { TERM_CN, FIELD_CN } from "./glossary.js";
import { clock, dur } from "./format.js";

/** Short names of the case refs the inquirer cites most (others use the case entry's label). */
export const REF_CN = {
  "L0.health.products": "数据健康", "L0.gaps": "资料缺口", "L0.fmz_fact": "信号层事实", "L0.release": "版本身份",
  "L1.contract": "合约", "L1.liability": "责任区与距离", "L1.economics": "收支", "L1.payout_table": "赔付表", "L1.breakeven": "盈亏平衡",
  "L2.quote": "报价", "L2.fee": "费用", "L2.book.short": "短腿盘口", "L2.book.long": "保护腿盘口", "L2.compensation": "补偿档",
  "L2.decision_reduction": "程序补偿读数", "L2.baseline": "市场基线", "L2.baseline.basis": "基线口径",
  "L2.baseline.p_loss": "到期亏损概率基线", "L2.baseline.p_touch_short": "触及短腿概率基线", "L2.baseline.p_max_loss": "最大亏损概率基线",
  "L2.baseline.p_itm_short": "短腿到期价内概率基线", "L2.selection": "选取依据",
  "L3.space.gate": "空间门", "L3.space.anchor": "锚定与 Gamma 体制", "L3.space.transitions": "空间门变迁", "L3.gex.structure": "GEX 结构",
  "L3.expiry_walls": "本期墙", "L3.spatial_position": "空间位置（周度 / 0DTE）", "L3.map.references": "地图参考位",
  "L3.map.liability_regions": "地图责任区",
  "L4.iv": "期限波动", "L4.expiry_vol": "本期波动与实现波动", "L4.skew": "偏斜",
  "L5.volume_price": "量价", "L5.flow": "主动成交", "L5.m_die": "M-DIE 运动判读", "L5.edb": "EDB 读数", "L5.edb_reasoning": "EDB 推理",
  "L5.options_flow": "期权流", "L5.conflicts": "信号冲突",
  "L6.daily": "每日快照", "L6.session_clock": "日内时段", "L6.entry_warnings": "不建仓警示",
  "L7.macro": "宏观", "L7.events": "事件", "L7.cross_asset": "跨资产", "L7.background.allocation": "配置背景",
  "L7.background.financing": "资金背景", "L7.background.inventory": "持仓背景", "L7.background.external_conditions": "外部资本条件",
  "L8.decision_path": "核验路径读数", "L8.guidance": "程序读法", "L8.signal_recommendation": "信号层建议", "L8.research_readings": "研究读数",
};
const LAYER_WORD = { L0: "资料层", L1: "合同层", L2: "报价与补偿层", L3: "结构层", L4: "波动层", L5: "运动层", L6: "时间层",
  L7: "外部层", L8: "程序读数层", L9: "历史层" };
const STEP_WORD = { D0: "D0 资料", D1: "D1 责任", D2: "D2 位置", D3: "D3 运动", D4: "D4 外部", D5: "D5 变化",
  Q1: "Q1 风险质量", Q2: "Q2 风险补偿",
  // dp-2026-10-27.1 steps; single letters only where the text names them as steps (see CHAIN_RE)
  V: "V 否决", R: "R 风险", P: "P 价格", PL: "PL 预案" };

/** Field names and codes the inquirer writes (on top of the page glossary). */
export const IQ_TERMS = {
  // probabilities, baseline and compensation
  p_loss: "到期亏损概率", p_touch: "触及概率", p_touch_short: "触及短腿概率", p_max_loss: "最大亏损概率", p_itm_short: "短腿到期价内概率",
  touch_prob: "触界概率", expiry_loss_prob: "到期亏损概率", tail_loss: "深尾损失", compensation: "补偿",
  tier: "补偿档", tier_provisional: "补偿档暂定", fair: "公允", thin: "偏薄", rich: "偏厚", below_market: "低于市场估价", above_market: "高于市场估价",
  net_credit: "净信用", net_credit_to_mark: "净信用÷mark 价差", net_credit_after_fee_btc: "扣费后净信用（BTC）", credit: "信用", credit_btc: "信用（BTC）",
  edge: "相对市场估价的差额", edge_usd: "相对市场估价的差额（美元）", edge_btc: "相对市场估价的差额（BTC）",
  market_payout: "市场估价赔付", market_payout_btc: "市场估价赔付（BTC）", mark_spread: "mark 价差", mark_spread_btc: "mark 价差（BTC）",
  payout: "赔付", breakeven: "盈亏平衡", static_net: "静态净值", M_ref: "补偿余量 M", mu_ref: "风险参考 μ",
  risk_reference_gap: "风险参照缺口", frozen_risk_reference_missing: "冻结风险参照缺失", "risk.astra.frozen_mu.reference.v1": "冻结风险参照 μ",
  executable_or_filled: "可成交或已成交", validated: "已验证", EV: "期望值",
  // quotes and greeks
  bid: "买价", ask: "卖价", best_bid: "最优买价", best_ask: "最优卖价", best_bid_price: "最优买价", best_ask_price: "最优卖价",
  bid_iv: "买价 IV", ask_iv: "卖价 IV", mark_iv: "标记 IV", mark_price: "标记价", atm_iv: "ATM IV", atm_iv_pct: "ATM IV（%）",
  index_price: "指数价", underlying_price: "标的价", forward: "远期价", forward_usd: "远期价（美元）", spot: "现价", index: "指数",
  delta: "Delta", vega: "Vega", theta: "Theta", short_abs_delta: "短腿 |Δ|", short_iv_pct: "短腿 IV（%）", long_iv_pct: "保护腿 IV（%）",
  order_book: "盘口", two_leg_book: "两腿盘口", taker: "吃单", short: "短腿", long: "保护腿", side: "侧别", put: "Put", call: "Call",
  "deribit.btc.index.v1": "Deribit 指数", "deribit.btc.option.fee_schedule.v1": "Deribit 期权费率表", "deribit.btc.option.greeks_oi": "Deribit 期权希腊值与持仓",
  // volatility and time
  expected_move_usd: "到期前预期波动（美元）", rv_24h: "近 24 小时实现波动", rv_7d: "近 7 日实现波动", rv_24h_pct: "近 24 小时实现波动（%）",
  rv_7d_pct: "近 7 日实现波动（%）", realized_vol: "实现波动", iv_percentile: "IV 分位", dvol: "DVOL", term_structure: "期限结构",
  term_front: "近端期限", term_inversion: "期限倒挂", premium_thickening: "保费突然变厚", busy_window_thin_cushion: "高波动窗口临近且缓冲不足",
  busy_window: "高波动时段提示", in_progress: "进行中", ahead: "窗口即将到来",
  skew: "偏斜", skew_25d: "25Δ 偏斜", rr: "风险逆转", RR: "风险逆转", rr_skew: "风险逆转偏斜", delta_rr: "Delta 风险逆转", rr_blend: "风险逆转合成",
  rr_z: "风险逆转 Z 值", put_25d_iv: "25Δ Put IV", call_25d_iv: "25Δ Call IV", vol: "波动",
  hours_to_expiry: "距到期小时数", DTE: "剩余天数", remaining_clock_factor: "剩余时段风险倍数", remaining_variance_share: "剩余风险占比",
  hour_ratio_now: "当前小时风险倍数", us_open: "美股开盘", weekday: "工作日", weekend: "周末", contract_window: "合约有效期",
  contract_expiry_term: "本期期限", next_expiry: "下一个到期", lead_expiry: "主到期", distance_in_moves: "以预期波动计的距离",
  distance_in_moves_contract_expiry: "按本期 IV 计的距离", "close-to-close": "收盘到收盘", roll: "展期",
  // structure
  gamma: "Gamma", net_gamma: "净 Gamma", net_gamma_notional: "净 Gamma 名义值", total_net_gex: "总净 GEX", total_gamma_usd: "总 Gamma（美元）",
  gex_usd: "GEX（美元）", gex: "GEX", gamma_call: "Call 侧 Gamma 墙", gamma_put: "Put 侧 Gamma 墙", oi_call: "Call 持仓墙", oi_put: "Put 持仓墙",
  put_oi: "Put 持仓", call_oi: "Call 持仓", open_interest: "持仓量", pin: "Pin", pin_strike: "Pin 行权价", flip: "翻转位", flip_point: "翻转位",
  max_pain: "最大痛点", max_gamma_strike: "最大 Gamma 行权价", max_gamma_oi_share: "最大 Gamma 持仓占比", magnet: "磁吸位",
  walls: "墙位", weekly: "周度", zero_dte: "0DTE", support: "支撑", resistance: "阻力", dealer: "做市商", unsigned: "不分方向",
  regime: "Gamma 体制", regime_strength: "体制强度", regime_at_short: "短腿处体制", market_state: "市场状态", positive_gamma: "正 Gamma",
  negative_gamma: "负 Gamma", POSITIVE_GAMMA_PINNING: "正 Gamma 钉住", POSITIVE_GAMMA: "正 Gamma", NEGATIVE_GAMMA_AMPLIFYING: "负 Gamma 放大",
  NEGATIVE_GAMMA: "负 Gamma", TRANSITION: "翻转过渡", POSITIVE: "正", NEGATIVE: "负", positive: "正", negative: "负", sign: "符号",
  anchor: "锚点", score: "评分", space_gate: "空间门", gating: "门控", eligible: "放行", state: "状态", producer: "生产端", producer_state: "生产端状态",
  evaluation_only: "只作评估", gate_action: "门判读", spatial: "空间评价", favorable: "有利", unfavorable: "不利", co_location: "同位",
  distance_to_flip_pct: "距翻转位（%）", GGR_SPATIAL: "空间评价（GGR）", GGR_AGG_NET_GEX_DISAGREES: "汇总净 GEX 与之不一致",
  SPACE_GATE_ANCHOR_NOT_VALID: "锚点无效", SPACE_GATE_ANCHOR_SCORE_LOW: "锚点评分偏低", ANCHOR_INEFFECTIVE: "锚点失效", ANCHOR_EFFECTIVE: "锚点有效",
  spacegate_signature: "空间门签名", SUPPORT: "支持", Space: "空间",
  // motion, flow and the signal layer
  NO_MOVE: "无明显运动", NO_DIRECTIONAL_MOVE: "无方向运动", move: "运动", move_shape: "运动形态", m_die: "M-DIE", direction: "方向",
  tmvf_24h: "TMV 24h", tmvf_48h: "TMV 48h", tmv_blend: "TMV 合成", tmv_core: "TMV 核心", micro_flow: "微观流", net_flow: "净流",
  CVD_4h: "CVD 4h", CVD_12h: "CVD 12h", CVD_STRENGTH_NOT_ACTIVE: "CVD 强度未激活", whale_24h: "24h 大单", whale_1h: "1h 大单",
  vote: "投票", weight: "权重", confidence: "置信度", agreement: "一致度", lean: "倾向", BULLISH: "偏多", BEARISH: "偏空", Bullish: "偏多",
  Bearish: "偏空", Neutral: "中性", NEUTRAL: "中性", edb_score: "EDB 评分", support_label: "支持标签", next_action: "下一步",
  NO_TRADE_BLOCKED: "不交易（受阻）", WAIT_DIE_ANCHOR_WINDOW: "等待 M-DIE 与锚定窗口", calibration_state: "校准状态", UNCALIBRATED: "未校准",
  REFERENCE_BLOCKED: "参照受阻", REFERENCE_BLOCKED_NO_TRAINING: "参照受阻（无训练样本）", EXCLUDED: "已排除", NOT_COMPARABLE: "不可比",
  conflict: "冲突", window_conflict: "窗口冲突", combined: "合并", persistent: "持续", current: "当前", Drift: "漂移", Mild: "温和", Weak: "弱",
  Strong: "强", Tailwind: "顺风", Headwind: "逆风", strong_bullish_bias: "强偏多", strong_bearish_bias: "强偏空", crowded_long: "多头拥挤",
  crowded_short: "空头拥挤", funding: "资金费", FUNDING: "资金费", funding_state: "资金费状态", MACRO: "宏观", macro: "宏观", macro_score: "宏观评分",
  US10Y: "美债 10Y", gold: "黄金", qqq: "纳指 QQQ", beta: "Beta",
  // quality and bookkeeping
  STALE: "过期", MISSING: "缺失", ok: "正常", OK: "正常", null: "空", true: "是", false: "否", unknown: "未知", quality: "质量",
  quality_reason: "质量原因", source_quality: "来源质量", reason: "原因", reason_codes: "原因码", gaps: "缺口", gap: "缺口", evidence: "证据",
  walls_not_contract_expiry: "墙位不属于本期", iv_not_contract_expiry: "IV 不属于本期", configured_assumption: "配置假设",
  sequential_leg_book_estimate: "两腿顺序估算", snapshot: "快照", view: "读视图", summary: "摘要", basis: "口径", units: "单位", ref: "引用",
  source_record_id: "来源记录", time_ms: "时刻", capture: "采集", risk_qualification: "风险资格", entry_warnings: "不建仓警示", events: "事件",
  scenario: "情景", mechanism: "机制", temporal: "时间持续性", collinear: "同源", case_file: "案卷", history: "历史", external: "外部",
  readings: "读数", type: "类型", value: "数值", age: "时效", expiry: "到期", strategy: "策略", note_cn: "说明", source_cn: "来源",
  "gex.raw.board": "GEX 原始看板", "gex.netgamma.proxy": "净 Gamma 代理值", "gex.effective.accepted": "GEX 采纳值", "gex.pending.candidate": "GEX 待定候选",
  // second batch: fields of the case entries the inquirer quotes
  GGR: "GGR 空间评价", payout_btc: "赔付", away: "远离", toward: "朝向", top: "最大处", level: "水平", phase: "时段", signal: "信号",
  none: "无", distance_pct: "距离（%）", short_distance_pct: "短腿距离（%）", verdict: "判读", kind: "类型", net: "净值", strategy_code: "策略码",
  quote: "报价", joint_r2: "联合 R²", risk: "风险", now_hour_utc: "当前 UTC 小时", Binance: "币安", binance: "币安", deribit: "Deribit",
  conflict_level: "冲突程度", pcr: "PCR", VOLQ: "VOLQ 纳指波动", per: "每", contract_atm_iv: "本期 ATM IV", healthy_long_bias: "多头结构健康",
  windows: "高波动窗口", valuation_spot_usd: "估值时现价", max_pain_price: "最大痛点价", Directional: "有方向", trend: "趋势", fading: "减弱",
  status: "状态", at_ms: "时刻", expected_move_pct: "预期波动（%）", expected_move: "预期波动", remaining_time_share: "剩余时间占比",
  triggered: "已触发", any_triggered: "任一触发", loss_at_protection: "保护腿处亏损", fmz_fact: "信号层事实", net_gamma_conflict: "净 Gamma 冲突",
  usd_intrinsic_cap: "美元内在价值上限", tail: "尾部", gamma_regime: "Gamma 体制", final: "最终", n_bars: "K 线数", coverage: "覆盖",
  not_disclosed: "未披露", stance: "立场", atm_strike: "ATM 行权价", PRICE_ABOVE_FLIP_POSITIVE_GAMMA: "现价在翻转位上方、正 Gamma",
  term_front_is_contract_expiry: "近端期限即本期", rr_1d_is_contract_expiry: "1 日风险逆转即本期", rr_1d: "1 日风险逆转", delivery_fee: "交割费",
  short_sale: "短腿卖出所得", long_purchase: "保护腿买入成本", capped: "封顶", MATERIAL: "有实质信息", dxy: "美元指数", reading_guidance: "程序读法",
  spread: "价差", space: "空间", credit_to_width_pct: "信用占宽度（%）", answer_source: "答案来源", liability_direction: "责任方向",
  us_session: "美股时段", gate_basis: "门口径", now_day_type: "日类型", transitions_24h: "24 小时内迁移次数", entry_fee: "开仓费",
  reference_margin: "参考余量", wpem_reference_qualification: "WPEM 参考资格", short_iv: "短腿 IV", long_iv: "保护腿 IV", net_gex_sign: "净 GEX 符号",
  GLOBAL_ONLY: "仅全期限口径", execution_net_flow: "成交净流", spot_price: "现价", note: "说明", NOT_CONNECTED: "未接入", asset_price: "资产价格",
  busy_now: "当前为高波动小时", window: "窗口", next_window: "下一个窗口", latest: "最新", eff_weight: "有效权重",
  "deribit.btc.option.order_book.v1": "Deribit 期权盘口", SPACE_GATE_GGR_CONFLICT: "空间门与 GGR 冲突", breakeven_usd: "盈亏平衡价",
  remaining_hours: "剩余小时", put_call_ratio_oi: "持仓 Put/Call 比", spatial_position: "空间位置", NO_DIRECTION: "无方向", candidate_id: "候选编号",
  overlap_ms: "重叠时长", component_score: "分项评分", summary_cn: "摘要", gap_sth_source_price_pct: "距短期持有者成本（%）", attention: "关注",
  coordinate_basis_not_qualified: "坐标口径未合格", hold_to_expiry: "持有到期", starts_at: "开始时刻", veto: "否决", hedging_flows: "对冲流",
  "risk.astra.frozen_mu.reference": "冻结风险参照 μ", greeks_oi: "希腊值与持仓", path_efficiency: "路径效率", layer: "层", states_stale: "状态过期",
  strike: "行权价", instrument: "合约", gap_vol_pts: "差距（波动点）", transition: "迁移", "risk.astra.qualified_model.domain.v1": "合格模型域",
  "deribit.btc.vertical.two_leg_book.v1": "Deribit 价差两腿盘口", "deribit.btc.option.greeks_oi.v1": "Deribit 期权希腊值与持仓",
  net_gamma_sign: "净 Gamma 符号", net_credit_after_entry_fee: "扣开仓费后净信用", theoretical_two_leg_fee: "两腿理论费用",
  normalized_deviation: "标准化偏离", GAMMA_FLIP_TRANSITION_ZONE: "Gamma 翻转过渡区", target: "目标", dense_on_way: "途中密集",
  btc_pct: "BTC 自身（%）", explained_pct: "可解释部分（%）", own_pct: "自身部分（%）", observations: "观测数", settled: "已结算",
  realized_pnl: "已实现盈亏", max: "最大", fees: "费用", spread_cost: "价差成本", totals: "合计", DIE_ANCHOR_WINDOW_NOT_ACTIVE: "M-DIE 锚定窗口未激活",
  gexmonitor: "gexmonitor", CLEAR_DIRECTIONAL_MOVE: "明确方向运动", open: "开盘", distance_in_moves_below: "下方距离（预期波动倍数）",
  relations: "关系", high: "高", medium: "中", low: "低", day: "日", anchor_ok: "锚点有效", protection_at_usd: "保护腿价", short_premium: "短腿权利金",
  long_premium: "保护腿权利金", exchange_times_ms: "交易所时刻", adverse_count: "不利次数", expected: "预期", public: "公开", gex_info_agrees: "GEX 信息一致",
  accelerate: "加速", underlying: "标的", failure_count: "失败次数", greeks: "希腊值", source: "来源", spot_to_max_pain_pct: "现价距最大痛点（%）",
  stale: "过期", COMPARABLE: "可比", stable: "稳定", readable: "可读", "relations.expiry_alignment": "到期口径对齐", CHOPPY_DRIFT: "震荡漂移",
  code: "代码", BTCUSDT: "BTCUSDT", compensation_reference: "补偿口径", edb_conflict: "EDB 方向冲突", visible_credit: "可见信用", week_atm_iv: "周度 ATM IV",
  research_readings: "研究读数", in_hours: "小时后", zero: "零", total_usd_m: "合计（百万美元）", core: "核心", error_room: "误差空间", notional: "名义值",
  delivery: "交割", codes: "代码", near_breakeven: "接近盈亏平衡", asof: "时点", trade: "交易", term: "期限", BRK: "BRK",
  USDm: "百万美元", phase_cn: "时段",
  // third batch
  "gex-effective-flip": "GEX 采纳翻转位", realized: "已实现", export_health: "导出健康", write_count: "写入次数", spacegate: "空间门",
  loss_budget: "亏损预算", available_depth: "可用深度", "get_order_book.greeks.delta": "盘口 Delta", tick: "最小价位", global_only_not_expiry_specific: "仅全期限、不分到期",
  put_25d: "25Δ Put", call_25d: "25Δ Call", DTWEXBGS: "美元广义指数", exact_expiry: "精确到期", "macro.dxy.close": "美元指数收盘", "macro.vxn.close": "VXN 收盘",
  abs_delta_short: "短腿 |Δ|", abs_delta: "|Δ|", timestamp: "时间戳", LOCAL_CANDIDATE: "本地候选", contract_expiry: "本期到期", atm_jump_vol_pts: "ATM 跳变（波动点）",
  allocation: "配置", clear: "清晰", book: "盘口", price: "价格", flow: "主动成交", change: "变化", date: "日期", us_evening: "美股晚间",
  last3: "最近 3 次", total_state: "合计状态", pinning: "钉住", kline: "K 线", kline_1m: "1 分钟 K 线", quote_old: "报价偏旧", financial_math: "金融计算",
  UNCHANGED_FINANCIAL_RULES: "金融规则未变", btc_contract_cap: "BTC 赔付上限", received_ts_ms: "接收时刻", fee: "费用", micro_bearish_lean: "短时偏空",
  micro_bullish_lean: "短时偏多", edb_conflict_material: "EDB 冲突（实质）", tmvf: "TMV", as_of_date: "截至日期", loss_share: "亏损占比", pain: "痛点",
  data_engine: "数据引擎", protection_at: "保护腿处", best: "最优", INEFFECTIVE: "无效", EFFECTIVE: "有效", Detached: "脱离", expiry_scope: "到期口径",
  gate: "门", strong: "强", ibit: "IBIT", "DX-Y.NYB": "美元指数", methodology: "方法", affected_consumers: "受影响的使用方", GLOBAL: "全期限",
  fee_schedule: "费率表", "vertical.two_leg_book": "价差两腿盘口", spatial_vote: "空间投票", sparse_on_way: "途中稀疏", micro: "微观",
  zero_dte_walls_are_contract_expiry: "0DTE 墙即本期", snapshot_ts: "快照时刻", premium_spread: "权利金价差", short_delta_target_abs: "短腿目标 |Δ|",
  tag: "标签", contract_near_breakeven: "合约接近盈亏平衡", BLOCKED: "受阻", term_front_expiry: "近端期限到期", busy: "高波动", market: "市场",
  contract: "合约", expiry_walls: "本期墙", NONE: "无", attribution: "归因", m_ref: "补偿余量 M", active: "活跃", NOT_NEWLY_DEPLOYED: "非新部署",
  cached_quote_not_current: "缓存报价非当前", valuation: "估值", SPACE_GATE_GGR_TRANSITION: "空间门与 GGR 迁移", entry: "开仓", boundary: "边界",
  payout_at_protection: "保护腿处赔付", payout_at_deep: "深尾处赔付", provisional: "暂定", since: "起始", expiry_oi_share_pct: "本期持仓占比（%）",
  CP_STH: "短期持有者成本", short_leg: "短腿", protective_leg: "保护腿", contract_short_leg: "本合约短腿", window_return_pct: "窗口收益（%）",
  close_ms: "收盘时刻", nominal: "名义", real: "实际", real_10y: "10Y 实际利率", external_conditions: "外部资本条件", partial: "部分", mid: "中间价",
  reason_code: "原因码", enabled: "已启用", risk_reference_adapter: "风险参照适配器", btc_reference_scale_at_spot: "现价处 BTC 参考刻度",
  valuation_spot: "估值现价", freshness: "新鲜度", FRESH: "新鲜", valid: "有效", raw: "原始", strength: "强度", EVIDENCE: "证据", overlap: "重叠",
  "brk-cp-sth": "短期持有者成本（BRK）", narrow: "窄", order_layer: "订单层", distance_to_flip: "距翻转位", usd_conversion: "美元换算",
  book_summary: "盘口汇总", question_cn: "问题", observed: "已观测", bar: "K 线", close: "收盘", explained: "可解释", own: "自身", from_ms: "起点",
  fetch: "抓取", settlement: "结算", settlement_usd: "结算价", max_pain_target_expiry: "本期最大痛点", hour_level: "小时级别", change_pct_3d: "3 日变化（%）",
  cross_asset: "跨资产", fetched_at_ms: "抓取时刻", rr_1d_expiry: "1 日风险逆转到期", wall: "墙", "gexmonitor.gex_info": "gexmonitor GEX 信息",
  book_ticker: "盘口行情", "funding.predicted": "预测资金费率", "oi.usd_notional": "持仓美元名义值", protection: "保护腿", short_in_zone: "短腿在区内",
  inventory: "持仓", risk_ratio: "风险倍数", window_within_hours: "窗口在几小时内", structure: "结构", liability: "责任", components: "构成",
  participation_status: "参与状态", jump: "跳变", observation: "观测", release: "版本", grows_in_btc: "按 BTC 计继续增加", ELIGIBLE: "放行",
  ACTIVE: "活跃", next: "下一个", detail: "细节", folded: "折叠", near: "接近", covers: "覆盖", inputs: "输入", market_price: "市场价",
  selection_basis: "选取依据", expires_at: "到期于", references: "参考位", GATE_ONLY: "仅作门", price_change_pct: "价格变化（%）", lines: "行",
  signal_recommendation: "信号层建议", Decision: "Decision", Path: "Path",
  asks: "卖盘", bids: "买盘", best_ask_amount: "最优卖量", best_bid_amount: "最优买量", instrument_name: "合约名", rho: "Rho",
  last_bar_end_ms: "最后一根 K 线时刻", instrument: "合约", bar: "K 线", label: "标签", amount: "数量", stats: "统计", volume: "成交量",
  settlement_price: "结算价", estimated_delivery_price: "预估交割价", open_interest_btc: "持仓量（BTC）", interest_rate: "利率",
};

const UNIT_CN = { btc: "BTC", usd: "美元", pct: "%", ms: "毫秒", usd_m: "百万美元" };
const direct = (tok) => IQ_TERMS[tok] ?? TERM_CN[tok] ?? (tok.includes("_") ? FIELD_CN[tok] : undefined);
/** A code in words: directly, else a known name with a unit suffix (_btc, _usd, _pct…), else
 *  each dotted part when every part is known. */
function lookup(tok) {
  const hit = direct(tok);
  if (hit !== undefined) return hit;
  const versioned = tok.match(/^(.+)\.v\d+$/);                 // data product ids: gex.raw.board.v1
  if (versioned) { const base = lookup(versioned[1]); if (base !== undefined) return base; }
  const scoped = tok.match(/^(.+?)_(contract_expiry|is_contract_expiry)$/);
  if (scoped) { const base = direct(scoped[1]); if (base !== undefined) return `${base}（本期）`; }
  const unit = tok.match(/^(.+?)_(usd_m|btc|usd|pct|ms)$/);
  if (unit) { const base = direct(unit[1]); if (base !== undefined) return `${base}（${UNIT_CN[unit[2]]}）`; }
  if (tok.includes(".")) {
    const parts = tok.split(".").map((p) => direct(p));
    if (parts.every((p) => p !== undefined)) return parts.join(" · ");
  }
  return undefined;
}

/** A case ref in words: the curated short name, else the case entry's label, else its layer. */
export function refWord(ref, entries) {
  if (REF_CN[ref]) return REF_CN[ref];
  const label = entries?.[ref]?.label_cn;
  if (label) return String(label).replace(/（.*?）/g, "").trim();
  const layer = ref.split(".")[0];
  return LAYER_WORD[layer] || ref;
}

const REF_RE = /(?<![A-Za-z0-9_.])L[0-9](?:\.[A-Za-z0-9_]+)+/g;
const LAYER_RE = /(?<![A-Za-z0-9_.])L[0-9](?![A-Za-z0-9_.])/g;
const STEP_RE = /(?<![A-Za-z0-9_.-])(D[0-5]|Q[12])(?![A-Za-z0-9_.])/g;
// V / R / P / PL are words too often ("P&L", "R:R", "V 形"): only "PL", or a letter followed by "步",
// reads as a step
const CHAIN_RE = /(?<![A-Za-z0-9_.&\/-])(?:PL(?![A-Za-z0-9_.&\/=-])|[VRP]\s*步)/g;
const PAIR_RE = /(?<![A-Za-z0-9_.])([A-Za-z][A-Za-z0-9_.]*)=([A-Za-z0-9_.\-]+)/g;
const TOKEN_RE = /(?<![A-Za-z0-9_.\-])[A-Za-z][A-Za-z0-9_]*(?:[.\-][A-Za-z0-9_]+)*(?![A-Za-z0-9_])/g;

/**
 * A number for reading (user, 2026-10-21): millisecond timestamps as Beijing time, amounts of a
 * million and more in M (USD figures: GEX, Gamma, hedge flows), numbers written in scientific notation
 * (tiny rates) in ‱ / ‰; plain decimals such as BTC amounts stay as written, long decimals of prices cut to two places. The original is in the title.
 */
export function numWord(x, sci = typeof x === "number" && /e/i.test(String(x))) {
  if (typeof x !== "number" || !Number.isFinite(x)) return x;
  const a = Math.abs(x);
  if (Number.isInteger(x) && a >= 1e12 && a < 1e13 * 2) return clock(x);
  if (a >= 1e6) return `${(x / 1e6).toFixed(2)}M`;
  if (sci && a > 0 && a < 1e-3) return a >= 1e-4 ? `${+(x * 1e3).toPrecision(3)}‰` : `${+(x * 1e4).toPrecision(3)}‱`;
  if (a >= 100 && !Number.isInteger(x) && String(x).split(".")[1]?.length > 2) return x.toFixed(2);
  return x;
}
const NUM_RE = /(?<![A-Za-z0-9_.]|[A-Za-z0-9]-)(-?)(\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)(?![A-Za-z0-9_]|\.\d)/g;
const numText = (text) => text.replace(NUM_RE, (all, sign, body) => {
  const x = Number(sign + body);
  const shown = numWord(x, /[eE]/.test(body));   // only scientific notation becomes ‰ / ‱ (BTC amounts stay)
  return typeof shown === "string" ? shown : (/[eE]/.test(body) ? String(shown) : all);
});

/** The inquirer's text with refs, key=value pairs and codes read in Chinese. */
export function cnText(text, entries) {
  if (typeof text !== "string") return text;
  if (!/[A-Za-z]/.test(text)) return numText(text);
  // durations written in milliseconds ("age_ms 1460000", "1460000 ms") in minutes or hours
  text = text.replace(/(?<![A-Za-z0-9_.])(\d{4,11})(\s*)ms(?![A-Za-z])/g, (all, n) => dur(Number(n)));
  text = text.replace(/\b(age|overlap|gap|delay|lag)_ms(\s*[=:：]?\s*)(\d{4,11})(?!\d)/g, (all, k, sep, n) => `${k}_ms${sep}${dur(Number(n))}`);
  // the liability table's starts_at is the price where the payout starts; elsewhere a time
  text = text.replace(/starts_at(\s*[=:：]?\s*)(?=\d{4,6}(?:\.\d+)?(?!\d))/g, "起赔价$1");
  let out = text.replace(REF_RE, (ref) => `「${refWord(ref, entries)}」`);
  out = out.replace(LAYER_RE, (l) => LAYER_WORD[l] || l);
  out = out.replace(STEP_RE, (s) => STEP_WORD[s] || s);
  // "P 步" reads "P 价格步", not "P 价格 步"; "PL=…" stays a key=value pair for PAIR_RE
  out = out.replace(CHAIN_RE, (s) => { const id = s.replace(/\s*步$/, ""); return STEP_WORD[id] ? STEP_WORD[id] + (id === s ? "" : "步") : s; });
  out = out.replace(PAIR_RE, (all, k, v) => {
    const kk = lookup(k);
    const vv = lookup(v);
    return kk || vv ? `${kk || k}：${vv || v}` : all;
  });
  return numText(out.replace(TOKEN_RE, (tok) => lookup(tok) ?? tok));
}

/** A field name of a case entry's values in words, or the name itself. */
export const fieldWord = (key) => lookup(key) ?? key;
/** A short value: an enum code in words, else as is. */
export const valueWord = (v) => (typeof v === "string" ? lookup(v) ?? v : numWord(v));
