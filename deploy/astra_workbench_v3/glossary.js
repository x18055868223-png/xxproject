// Chinese names for the machine words that reach the page: evidence ids the
// model cites, enum values inside facts and model text, fact field names,
// data-source ids and release components. Raw ids stay available in titles
// and in the "原始证据" trace blocks; reading areas show these names instead.

/** Evidence ids, refined from each receipt's own label_cn where that label is half English. */
export const EVIDENCE_CN = {
  contract: "本保单合同", quote: "冻结盘口报价", fee: "费用假设", risk: "风险参考",
  flow: "主动成交", tmv: "量价路径（TMV）", funding: "资金费背景", "funding.current": "冻结时的最新资金费率",
  skew: "期权偏斜", macro: "宏观慢背景", "space.gate": "空间门", "space.flip": "Gamma 翻转位",
  "wpem.level": "同保单当前水平", "wpem.delta": "同保单严格变化", "wpem.path": "同保单窗口路径",
  "wpem.fragments": "同保单历史观察片段", "wpem.window": "同保单观察窗口", "wpem.compensation": "同保单补偿变化",
  "map.etf": "ETF 净流", "map.cost": "链上持有成本", "map.realized": "已实现盈亏", "map.oi": "合约持仓量",
  "map.funding": "资金费结算", "options.structure": "期权结构位", "options.vol": "隐含波动与距离", "options.flow": "期权成交与大单",
};

/** Enum values and stable codes found in facts, program text and model text. */
export const TERM_CN = {
  // direction and pressure
  micro_bullish_pressure: "短时买盘偏强", micro_bearish_pressure: "短时卖盘偏强", mild_bullish_bias: "温和偏多",
  mild_bearish_bias: "温和偏空", strong_bullish_bias: "强偏多", strong_bearish_bias: "强偏空", bullish_bias: "偏多",
  bearish_bias: "偏空", healthy_short_bias: "空头结构健康", early_bull: "早期偏多", bullish: "偏多", bearish: "偏空",
  neutral: "中性", noise: "噪声", mild: "温和", increased: "增加", decreased: "减少", fast: "快窗", slow: "慢窗", daily: "日度",
  // liability geometry
  outside: "在责任区外", outside_liability: "在责任区外", inside: "在责任区内", short_to_long: "卖出腿与保护腿之间",
  beyond_long: "越过保护腿", above_source_price: "高于来源价", below_source_price: "低于来源价",
  // relations and qualification
  unknown: "未知", missing: "缺失", gap: "缺口", decision_relation: "决策关系", contract_relation: "与合同的关系",
  path_relation: "路径关系", positive_ev_not_determinable: "正期望无法判定", frozen_risk_reference_missing: "冻结风险参考缺失",
  qualified_window_not_covered: "合格窗口未覆盖", qualified_same_candidate_path_missing: "缺少同保单合格路径",
  unqualified_or_mixed: "未合格或混合", insufficient_window: "窗口不足", configured_assumption: "配置假设",
  sequential_leg_book_estimate: "分腿盘口顺序估算", public_taker_capped_daily_delivery_zero: "按公开吃单费率封顶，日交割费为 0",
  public_taker_capped_daily_delivery_zero_at_mark: "按标记价估算的公开吃单费率（封顶），日交割费为 0",
  quote_held_not_current: "展示的是暂用读数，锁定需要最新报价",
  deribit_index: "Deribit 指数", exchange_time: "交易所时间", source_defined: "按来源定义", producer_top_level: "生产端顶层",
  current_fact: "当前事实", full_live: "全部实时", nearest_listed_tenor: "最近挂牌期限",
  official_expiry_last_30m_observation_window: "官方到期前 30 分钟观察窗", deribit_official_expiry_observation_rule: "Deribit 官方到期观察规则",
  // upper-case program codes
  BINANCE_USDM_BTCUSDT_AGGTRADES: "币安 U 本位 BTCUSDT 逐笔成交", BINANCE_USD_M_FUTURES: "币安 U 本位合约",
  BITVIEW_SAME_SOURCE_SAME_DAY_ONLY: "仅 Bitview 同源同日", KPF_BASIN: "KPF 成交盆地", LONG_FEE_BIAS: "多头付费偏向",
  SHORT_FEE_BIAS: "空头付费偏向", NOMINAL_ONLY: "仅名义值", NON_VOTING: "不参与表决", NOT_CROWDED: "未拥挤", CROWDED: "拥挤",
  NO_PATH_DUE_TO_WINDOW_GAP: "窗口有缺口，无法给出路径", NO_STRICT_DELTA: "无严格变化", NO_PATH: "无路径",
  OBSERVED_SETTLEMENTS_ONLY_NO_FIXED_FREQUENCY_ASSUMPTION: "只按已观察的结算，不假设固定频率", OBSERVED_ZONE: "已观察区间",
  PRICE_ABOVE_FLIP: "现价在翻转位上方", PRICE_BELOW_FLIP: "现价在翻转位下方",
  SKEW_TILTING_BULLISH_VS_BASELINE: "偏斜相对基线转向看涨", SKEW_TILTING_BEARISH_VS_BASELINE: "偏斜相对基线转向看跌",
  STRUCTURE_BLOCKED: "结构关闭", STRUCTURE_ELIGIBLE: "结构放行", INSUFFICIENT_WINDOW: "窗口不足", INSUFFICIENT: "不足",
  NOT_RETAINED: "未保留", RETAINED: "已保留", CONFIRMING_SAME_BLOCK: "同源确认", CONTEXT_ONLY: "仅作背景",
  COUNTER_EVIDENCE: "反证", NOT_QUALIFIED_FINAL_FOR_THIS_ROUND: "本轮未合格", REFERENCE_ANALYSIS_MISSING: "缺少参考分析",
  M_ref: "补偿余量 M", mu_ref: "风险参考 μ", QUALIFIED: "合格",
  // single words and limitation codes that producers write into prose
  taker: "吃单", maker: "挂单", canonical: "标准口径", option: "期权", "readings.last_rate": "最新费率读数",
  kpf_local_read_only_same_version: "KPF 为本地只读同版本数据",
  kpf_not_strict_without_native_deribit_or_qualified_mapping: "没有 Deribit 原生或合格映射时，KPF 不作严格依据",
  nominal_only_not_contract_comparable: "仅名义值，不与合约直接比较", PARTIAL: "部分可用", USABLE: "可用", MISSING: "缺失", CURRENT: "当前", UNKNOWN: "未知", OK: "正常",
};

/** Fact field names, one path segment at a time. */
export const FIELD_CN = {
  side: "侧别", short_instrument: "卖出腿", long_instrument: "保护腿", short_strike_usd: "卖出腿行权价", long_strike_usd: "保护腿行权价",
  expiry_ms: "到期", expiry: "到期", quantity_btc: "数量", remaining_horizon_hours: "剩余期限", remaining_dte_ms: "剩余期限", dte_ms: "剩余期限",
  adverse_direction_cn: "不利方向", liability_zone: "责任区关系", on_adverse_side_of_spot: "在现价不利一侧", price: "价格",
  kind: "类型", credit_btc: "可见信用", reference_price_usd: "参考价", reference_source: "参考价来源", reference_id: "参考编号",
  executable_or_filled: "可成交或已成交", frozen_quote_not_filled: "冻结报价未成交", execution_allowed: "允许执行",
  amount_btc: "费用合计", entry_fee_btc: "入场费", delivery_fee_btc: "交割费", basis: "口径", assumption_basis: "假设依据",
  status: "状态", state: "状态", mu_btc: "风险均值 μ", mu_ref_btc: "风险参考 μ", M_ref_btc: "补偿余量 M", overall_mu_status: "μ 状态",
  gap_reasons: "缺口", positive_ev_not_determinable: "正期望无法判定", reason: "原因", reason_codes: "原因", blocking_flags: "阻断项",
  horizon_sigma_move_pct: "到期前隐含预期波动", short_strike_distance_in_sigma: "短腿距离相对预期波动",
  breakeven_distance_in_sigma: "盈亏平衡距离相对预期波动", credit_to_width_at_spot: "信用占宽度",
  short_strike_distance_pct_outside_positive: "短腿距离（正值在外）", breakeven_distance_pct_outside_positive: "盈亏平衡距离（正值在外）",
  signed_distance_to_short_pct: "距卖出腿", signed_distance_to_long_pct: "距保护腿",
  atm_iv_for_horizon: "本期限 ATM IV", atm_iv_pct: "ATM IV", match: "匹配方式", days_to_expiry: "期限（天）",
  iv_percentile: "IV 分位", dvol: "DVOL", rr_25d_front: "近月 25Δ 风险逆转", put_25d_iv: "25Δ Put IV", call_25d_iv: "25Δ Call IV",
  rr: "风险逆转", chg_1d: "1 日变化", chg_1w: "1 周变化", tenor: "期限", rr_blend: "风险逆转合成", rr_z: "风险逆转 z 值",
  delta_rr: "风险逆转变化", skew_norm_blend: "偏斜合成",
  pcr: "成交 PCR", pcr_oi: "持仓 PCR", call_oi: "Call 持仓", put_oi: "Put 持仓", call_volume: "Call 成交", put_volume: "Put 成交",
  effective_flip_point: "有效翻转位", price_vs_flip: "现价相对翻转位", same_dependency_as_flip: "与翻转位同一依赖",
  all_expiry_walls: "全期限墙", near_term_walls: "近端墙", support_walls: "支撑墙", resistance_walls: "阻力墙", walls: "墙",
  weekly: "周度", zero_dte: "当日到期", magnet_price: "磁吸位", volatility_trigger: "波动触发位", max_pain: "最大痛点",
  next_expiry_max_pain: "下一到期痛点", same_expiry_as_contract: "与本单同到期", target_expiry: "目标到期", hours_to_expiry: "距到期",
  dealer_hedging_adverse_scenarios: "做市对冲不利情景", move_to_short_strike_pct: "到卖出腿所需涨跌", scenarios: "情景", sign_convention: "符号约定",
  contains_spot: "包含现价", contains_short_strike: "包含卖出腿", contains_long_strike: "包含保护腿",
  low: "下沿", high: "上沿", raw_low: "下沿", raw_high: "上沿", display_center: "中心", high_low_baseline: "高低基线",
  context_readings: "背景读数", combined: "合成", fast_4h: "4h 快窗", slow_12h: "12h 慢窗", data_ready: "数据就绪", direction: "方向",
  direction_cn: "方向", max_coverage_hours: "最大覆盖时长", ready_horizons: "就绪窗口", score: "评分", coverage_frac: "覆盖率",
  coverage_hours: "覆盖时长", cvd_sum: "CVD 合计", cvd_unit: "CVD 单位", horizon_hours: "窗口时长", momentum_return_pct: "动量收益",
  window_role: "窗口角色", micro_flow_effect: "短时流影响", aggregate_direction: "汇总方向", aggregate_source: "汇总来源",
  aggregate_state: "汇总状态", does_not_override_horizon_states: "不覆盖各窗口状态", effect: "影响",
  decision_relation: "决策关系", contract_relation: "与合同的关系", path_relation: "路径关系", relation: "关系", relation_cn: "关系",
  source_qualification: "来源资格", source_ref: "来源引用", coordinate_qualification: "坐标资格", geometry: "几何关系",
  tmv_blend: "TMV 合成", tmv_state: "TMV 状态", tmvf_24h: "TMV 24h", tmvf_48h: "TMV 48h", tmv_core: "TMV 核心", tmv_final: "TMV 最终",
  core: "核心", kline_open_time: "K 线开盘时间", trend_strength_pct: "趋势强度", final_state: "最终状态", age_ms: "数据年龄",
  funding_count: "资金费次数", funding_cum: "累计资金费", funding_effect: "资金费影响", funding_norm: "资金费归一值", funding_state: "资金费状态",
  window_start_time: "窗口开始", window_end_time: "窗口结束", window_conflict: "窗口冲突", window_days: "窗口天数", windows: "窗口",
  semantics: "语义", crowding_state: "拥挤状态", crowding_threshold_abs: "拥挤阈值", edb_participation: "EDB 参与", edb_vote_allowed: "EDB 可表决",
  fee_bias: "付费偏向", is_crowded: "是否拥挤", raw_funding_rate: "原始资金费率", last_rate: "最新费率", settled_count: "结算次数",
  settled_rate_sum: "结算费率合计", settled_funding_rate_sum: "结算资金费率合计", settlements_only: "仅结算记录",
  previous_24h: "前 24h", window_24h: "近 24h", coverage: "覆盖", start_ms: "开始", end_ms: "结束",
  previous_settled_sum_vs_zero: "前 24h 结算合计相对 0", recent_settled_sum_vs_zero: "近 24h 结算合计相对 0",
  recent_vs_previous_settled_sum: "近 24h 相对前 24h", adjacent_equal_windows: "相邻等长窗口",
  borrow_gap_does_not_delete_funding: "借贷缺口不删除资金费", predicted_count_excluded: "已排除预测条数",
  prediction_mixed_with_settlement: "预测与结算混合",
  settlement_observation: "结算观察", descriptive_only_no_gate: "仅描述、不设门", identity: "身份", source: "来源",
  ms_until_observation_start: "距观察开始", observation_start_ms: "观察开始", observation_end_ms: "观察结束",
  oi_24h_window: "24h 持仓窗口", change_native_btc: "变化（BTC）", change_native_pct: "变化幅度", current_native_btc: "当前（BTC）",
  previous_native_btc: "之前（BTC）", matched_ms: "匹配时点", sample_count: "样本数", target_ms: "目标时点",
  open_interest_native_btc: "持仓量（BTC）", has_24h_delta: "有 24h 变化", 
  same_metric_current_vs_previous: "同口径当前对比之前", same_metric_v3_vs_v7: "同口径 3 日对比 7 日",
  cp_all_usd: "全体持有成本", cp_lth_usd: "长期持有者成本", cp_sth_usd: "短期持有者成本", cp_all: "全体", cp_lth: "长期持有者", cp_sth: "短期持有者",
  cp_all_usd_vs_source_price: "全体成本相对来源价", cp_lth_usd_vs_source_price: "长期持有者成本相对来源价",
  cp_sth_usd_vs_source_price: "短期持有者成本相对来源价", cp_source_price_observation_dates_match: "成本与来源价同日",
  observation_dates: "观察日期", phase: "阶段", source_price: "来源价", source_price_usd: "来源价", source_phase: "来源阶段",
  price_basis: "价格口径", comparison_basis: "比较口径", not_compared_to_deribit_live_quote: "未与 Deribit 实时报价比较",
  latest_complete_date: "最近完整日期", latest_observation_date: "最近观察日期", source_time_basis: "来源时间口径",
  loss_14d_usd: "14 日亏损", profit_14d_usd: "14 日盈利", loss_share_14d: "14 日亏损占比", mode: "模式", missing: "缺失",
  f3_usd_m: "近 3 日净流（百万美元）", f7_usd_m: "近 7 日净流（百万美元）", u3: "近 3 日", u7: "近 7 日",
  v3_usd_m_per_day: "近 3 日日均（百万美元）", v7_usd_m_per_day: "近 7 日日均（百万美元）", last3: "近 3 日", last7: "近 7 日",
  prior60_start: "前 60 日开始", prior60_end: "前 60 日结束",
  macro_data_confidence: "宏观数据可信度", macro_regime: "宏观体制", macro_score: "宏观评分", data_age_ms: "数据年龄",
  data_state: "数据状态", data_status: "数据状态", last_data_time: "最近数据时间", component_summaries: "分项摘要", summary_label_cn: "摘要",
  vote: "表决", vote_confidence: "表决置信度", classification: "分类", count: "数量", quality: "质量", quote_currency: "计价币",
  role: "角色", scope: "范围", scope_cn: "范围", symbol: "交易对", market: "市场", strict_same_definition: "严格同定义",
  greeks_epoch_ms: "希腊值时点", decision_asof_ms: "决策时点", observed_at_ms: "观察时点", since_ts_ms: "起始",
  last_transition_ts_ms: "最近变迁", previous_state: "之前状态", observed_points: "观察点", position_fields: "位置字段",
  win_probability: "胜率", market_trend_claim_allowed: "可作趋势结论",
  source_method_breaks: "来源方法断点", candidate_ids_seen: "见到的候选", candidate_rotation_observed: "观察到候选轮换",
  candidate_rotation_or_method_break_is_not_market_trend: "候选轮换或方法断点不代表市场趋势", method_break_observed: "观察到方法断点",
  whale_1h: "大单 1h", whale_24h: "大单 24h", active_strike: "最活跃行权价", buy_call_count: "买 Call 笔数", buy_put_count: "买 Put 笔数",
  sell_call_count: "卖 Call 笔数", sell_put_count: "卖 Put 笔数", trade_count: "成交笔数", execution_net_flow_usd: "成交净流",
  net_flow_usd: "净流", lead_expiry: "主导到期", covers: "覆盖", exit_policy: "退出方式", settlement_currency: "结算币种", gex_usd: "GEX",
};

/** Registry data sources. */
export const SOURCE_CN = {
  "binance.spot.btcusdt.book_ticker.v1": "币安现货 BTCUSDT 最优报价", "binance.spot.btcusdt.kline_1m.v1": "币安现货 BTCUSDT 1 分钟 K 线",
  "binance.spot.btcusdt.trade.v1": "币安现货 BTCUSDT 逐笔成交", "binance.um.btcusdt.funding.predicted.v1": "币安合约预测资金费",
  "binance.um.btcusdt.funding.settled.v1": "币安合约已结算资金费", "binance.um.btcusdt.kline_1m.v1": "币安合约 BTCUSDT 1 分钟 K 线",
  "binance.um.btcusdt.oi.native.v1": "币安合约持仓量（BTC）", "binance.um.btcusdt.oi.usd_notional.v1": "币安合约持仓量（美元名义）",
  "btc.brk.cost_basis.v1": "链上持有成本（BRK）", "btc.brk.realized_pnl.daily.v1": "链上每日已实现盈亏（BRK）",
  "btc.etf.flow.daily.v1": "BTC ETF 每日净流", "btc.kpf.map.readonly.v1": "KPF 成交接受区地图",
  "cex.usdt.borrow_rate.v1": "交易所 USDT 借贷利率", "deribit.btc.discovery.current.v1": "Deribit 候选发现",
  "deribit.btc.index.map_context.v1": "Deribit 指数（地图用）", "deribit.btc.index.v1": "Deribit BTC 指数",
  "deribit.btc.option.fee_schedule.v1": "Deribit 期权费率表", "deribit.btc.option.greeks_oi.v1": "Deribit 期权希腊值与持仓",
  "deribit.btc.option.instrument.v1": "Deribit 期权合约信息", "deribit.btc.option.order_book.v1": "Deribit 期权盘口",
  "deribit.btc.option.universe.v1": "Deribit 期权合约全集", "deribit.btc.vertical.two_leg_book.v1": "Deribit 垂直价差两腿盘口",
  "fmz.bridge.snapshots.v1": "FMZ 信号快照", "fmz.spacegate.current.v1": "FMZ 当前空间门",
  "gex.effective.accepted.v1": "GEX 已采纳读数", "gex.netgamma.proxy.v1": "GEX 净 Gamma 代理", "gex.pending.candidate.v1": "GEX 待定读数",
  "gex.raw.board.v1": "GEX 原始看板", "macro.dxy.close.v1": "美元指数 DXY 收盘", "macro.risk_state.components.v1": "宏观风险分项",
  "macro.us10y.nominal.v1": "美国 10 年期名义利率", "macro.us10y.real.v1": "美国 10 年期实际利率",
  "macro.usd.broad.close.v1": "广义美元指数收盘", "macro.vxn.close.v1": "纳指波动率 VXN 收盘",
  "risk.astra.frozen_mu.reference.v1": "冻结风险均值 μ 参考", "risk.astra.qualified_model.domain.v1": "合格风险模型适用域",
};

/** Release-manifest components. */
export const COMPONENT_CN = {
  fmz_producer: "FMZ 信号生产端", fmz_native_schema: "FMZ 原生数据格式", data_engine: "数据引擎", candidate_discovery: "候选发现",
  deterministic_accounting: "承保核算", financial_math: "金融计算规则", candidate_ledger: "候选账本", candidate_archive: "候选时点归档",
  wpem_native_qualification: "WPEM 原生资格", wpem_window_projection: "WPEM 窗口投影", wpem_product_evidence: "WPEM 产品证据",
  wpem_review_evidence: "WPEM 复核证据", wpem_product_protocol: "WPEM 产品协议", review_input: "复核输入", risk_qualification: "风险资格诊断",
  risk_reference_adapter: "风险参考适配", llm_transport: "模型调用通道", workbench_api: "工作台接口", workbench_wpem_adapter: "工作台 WPEM 适配",
  map_source_consumer: "地图数据消费端", map_source_producer: "地图数据生产端", native_receiver: "FMZ 原生接收端",
  result_attachment: "复核回执附着", frontend: "前端页面", frontend_identity: "前端身份", evidence_routing: "证据路由",
  locked_candidate_store: "锁定存储", locked_candidate_comparison: "锁定对照", locked_candidate_followup: "锁定追踪",
  public_observation_scrub: "公开观察脱敏", workbench_session: "工作台会话", workbench_reading: "工作台读视图",
  legacy_product_protocol: "旧版产品协议", release_identity_reader: "版本身份读取", frontend_document: "前端页面文档",
};

const ID_CHAR = "[A-Za-z0-9_.]";

/** Whole English phrases producers write into otherwise Chinese text. */
const PHRASE_CN = [
  ["Bitview source series; no local CP/RP substitution", "Bitview 来源序列，本地不替换 CP/RP"],
  ["same context item only", "仅同一背景条目"],
  ["Review risk status", "复核风险状态"],
  ["source clock", "来源时钟"],
  ["Deribit option OI", "Deribit 期权持仓"],
  ["源端 reason code，不反向改写 flip 关系", "源端原因码，不反向改写与翻转位的关系"],
  ["Deribit live 冻结盘口报价", "Deribit 实时冻结盘口报价"],
  ["gexmonitor ", "GEX 监测源的"], ["clean evidence", "清洗后的证据"],
  // a code echoed in brackets right after its own Chinese reading
  ["窗口不足（INSUFFICIENT_WINDOW）", "窗口不足"], ["窗口枚举INSUFFICIENT_WINDOW", "窗口状态为窗口不足"],
];

/**
 * Labels for the evidence a receipt carried: the refined name, else the receipt's
 * own label_cn; repeated KPF zones are numbered in order.
 */
export function evidenceLabels(rows) {
  const labels = new Map();
  let zone = 0;
  for (const row of rows || []) {
    const ref = row.ref || row?.lineage?.ref || row?.provided?.ref;
    if (!ref || labels.has(ref)) continue;
    const own = row.label_cn || row?.provided?.label_cn;
    if (ref.startsWith("map.kpf.")) labels.set(ref, `KPF 成交接受区 ${++zone}`);
    else labels.set(ref, EVIDENCE_CN[ref] || own || ref);
  }
  return labels;
}

/** Display name for one evidence id. */
export const evidenceName = (ref, labels) => (labels && labels.get(ref)) || EVIDENCE_CN[ref] || ref;

/**
 * Replace machine words inside running text (model or program prose) with their
 * Chinese names. Only exact, whole tokens are replaced; everything else is left as written.
 */
export function humanize(text, labels) {
  if (typeof text !== "string" || !/[A-Za-z]/.test(text)) return text;
  for (const [en, cn] of PHRASE_CN) text = text.split(en).join(cn);
  const dict = new Map(Object.entries(TERM_CN));
  for (const [k, v] of Object.entries(EVIDENCE_CN)) dict.set(k, v);
  if (labels) for (const [k, v] of labels) dict.set(k, v);
  for (const [k, v] of Object.entries(FIELD_CN)) if (k.includes("_") && !dict.has(k)) dict.set(k, v);
  const re = new RegExp(`(?<!${ID_CHAR})[A-Za-z][A-Za-z0-9_]*(?:\\.[A-Za-z0-9_]+)*(?!${ID_CHAR})`, "g");
  return text.replace(re, (tok) => {
    if (dict.has(tok)) return dict.get(tok);
    // a dotted id whose head is known, e.g. map.kpf.kpf_zone_… without a numbered label
    if (tok.startsWith("map.kpf.")) return "KPF 成交接受区";
    return tok;
  });
}

/** A field path in Chinese, or null when any segment has no name (the field then stays in the raw evidence). */
export function fieldName(path) {
  const parts = String(path).split(".");
  const names = parts.map((p) => FIELD_CN[p]);
  if (names.some((n) => n == null)) return null;
  return names.filter(Boolean).join(" · ");
}

/** An enum value in Chinese, or null when unknown. */
export const termName = (value) => (value == null ? null : TERM_CN[value] ?? null);
