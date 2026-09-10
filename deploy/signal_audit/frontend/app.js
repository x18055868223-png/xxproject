(function () {
  "use strict";

  const embedded = JSON.parse(document.getElementById("signal-data").textContent);
  const INITIAL_CARD_LIMIT = 15;
  const PREFETCH_CARD_LIMIT = 2;
  const MAX_CARD_LOAD_ATTEMPTS = 3;
  const CARD_FETCH_TIMEOUT_MS = Math.max(1, Number(window.SIGNAL_AUDIT_CARD_TIMEOUT_MS || 15000));
  let documents = [];
  let loadState = { mode: "pending", error: "" };
  const cardCache = new Map();
  const cardLoadStates = new Map();
  const state = {
    currentId: null,
    query: "",
    direction: "",
    action: "",
    quality: "",
    grade: ""
  };

  const enumLabels = {
    ACTIVE: "本次作为方向依据",
    ADVERSE: "不利",
    ALLOWED: "允许",
    ALIGNED: "一致",
    APPROVABLE: "可提交人工批准",
    AUDIT_ONLY: "仅审计",
    BEARISH: "偏空",
    BEARISH_BIAS: "偏空背景",
    BEARISH_STRONG: "强偏空",
    BEARISH_WEAK: "弱偏空",
    BEARISH_LEAN: "理论偏空",
    BEARISH_CONFIRMED: "偏空已确认",
    BEARISH_WITH_DISAGREEMENT: "偏空但存在分歧",
    BULLISH: "偏多",
    BULLISH_BIAS: "偏多背景",
    BULLISH_STRONG: "强偏多",
    BULLISH_WEAK: "弱偏多",
    BULLISH_LEAN: "理论偏多",
    BULLISH_CONFIRMED: "偏多已确认",
    BULLISH_WITH_DISAGREEMENT: "偏多但存在分歧",
    BUY_ABSORBED_BEARISH: "买盘被吸收，偏空",
    BUY_CONFIRMS_UP: "买方流确认上行",
    CALL_CREDIT: "Call 信用价差",
    CLEAR: "无冲击阻断",
    BLOCK: "阻断",
    CONFIDENCE_GATE_NOT_DIRECTIONAL_VOTE: "只影响原信号确认条件，本次未作为方向依据",
    CVD_DATA_NOT_READY: "CVD 数据未就绪",
    CVD_HISTORY_WARMING: "CVD 历史样本仍在积累",
    CVD_PRICE_CONFIRM_BOTH_REQUIRED: "CVD 与价格确认须同时有效",
    CVD_STRENGTH_NOT_ACTIVE: "CVD 强度未达激活阈值",
    CONFLICT: "冲突",
    DECISION: "决策",
    DEGRADED: "降级",
    DECREASE: "降低前提耐久",
    DECREASE_TENTATIVE: "轻度降低前提耐久",
    DO_NOT_SUPPORT: "不支持系统结论",
    DO_NOT_MULTIPLY_CONFIDENCE: "不作为独立加分",
    ERROR: "错误",
    EXCLUDED: "本次未采用",
    FINAL: "定稿",
    FRESH: "新鲜",
    FULL_LIVE: "完整实时",
    FUNDING: "资金费率",
    FUNDING_RAW_MISSING: "原始资金费率缺失，无法判断",
    FUNDING_RAW_SEMANTIC_NON_VOTING: "资金费率未达拥挤阈值，本次未作为方向依据",
    FUTURES_FUNDING_CROWDING: "资金费率拥挤语义",
    FUTURES_FUNDING_SEMANTICS: "资金费率规范语义",
    GATE_ONLY: "只作为当前限制",
    GEMINI: "Gemini",
    gemini: "Gemini",
    GAMMA: "Gamma",
    GAMMA_TRANSITION: "Gamma 过渡",
    CONFIRMED: "已确认",
    CONFIRMED_60M_LOCAL: "60m局部确认",
    CONSTRAINT: "空间约束",
    CONSERVATIVE_LOWER_TIER: "缓冲带就低不就高",
    CROWDED: "拥挤",
    CURRENT: "当前截面",
    DEEP: "深",
    EDT: "美国夏令时",
    EST: "美国冬令时",
    EVENT_BLACKOUT: "事件黑名单",
    HIGH: "高",
    HEADWIND: "逆风",
    INCREASE: "提高前提耐久",
    INSUFFICIENT_WINDOW_COVERAGE: "窗口覆盖不足",
    INVALID_OUTPUT: "输出无效",
    LOCKED: "锁定",
    LONG_GAMMA_STABILIZING: "长 Gamma 稳定/钉住",
    LONG: "多",
    LONG_BIAS: "多头偏好",
    LOW: "低",
    LOW_TO_MEDIUM_BUFFER: "低转中缓冲带",
    LOWER_DURABILITY_CONFIRMED: "确认降耐久",
    LOWER_DURABILITY_TENTATIVE: "暂定降耐久",
    LOWER: "下调方向把握",
    MATERIAL: "实质分歧",
    SEVERE: "严重分歧",
    MEDIUM: "中",
    MEDIUM_TO_HIGH_BUFFER: "中转高缓冲带",
    MEDIUM_TO_LOW_BUFFER: "中转低缓冲带",
    MARKET_PRIOR_VALIDATED: "市场先验已验证",
    MARKET_PRIOR_VALIDATED_NOT_SIGNAL_CALIBRATED: "市场先验已验证 / 非信号校准",
    MODEL_ESTIMATED: "模型估算",
    MILD_CROWDED: "轻度拥挤",
    MILD_HEADWIND: "轻度逆风",
    MILD_TAILWIND: "轻度顺风",
    MACRO: "宏观",
    MACRO_CONTEXT: "宏观背景",
    MODERATE: "中等",
    MIXED_UNCLEAR: "混合不明",
    MIXED_HIGH_CONFLICT: "混合信号 / 高冲突",
    MIXED_LOW_CONFIDENCE: "混合信号 / 低置信",
    NEUTRAL: "中性",
    NEUTRAL_CONSERVATIVE: "中性保守",
    NEUTRAL_OBSERVE: "中性观察",
    NEUTRALIZE: "中和方向把握",
    NEUTRAL_DEAD_ZONE: "中性死区",
    NEUTRAL_OR_RANGE: "理论中性/区间",
    NEGATIVE_GAMMA: "负 Gamma",
    NONE: "无",
    NON_VOTING: "本次未作为方向依据",
    NOT_CROWDED: "未拥挤",
    NOISE: "可忽略",
    NOT_CONFIRMED: "未确认",
    NOT_IMPLEMENTED_SHADOW: "未落地影子项",
    NOT_READY: "未就绪",
    NR_IDLE: "接管窗口空闲",
    NR_WAIT_ANCHOR_DAMAGE: "接管窗口等待锚损伤",
    NR_WAIT_ANCHOR_REPAIR: "接管窗口等待锚修复",
    NR_REPAIR_CANDIDATE: "接管窗口修复候选",
    NR_REPAIR_CONFIRMED: "接管窗口修复确认",
    NR_REPAIR_STALE: "接管窗口修复已陈旧",
    NR_NOT_CONFIRMED: "接管窗口未确认",
    NR_EXPIRED: "接管窗口已失效",
    NO_TRADE_BLOCKED: "无交易/阻断",
    OBSERVE: "观察",
    OBSERVE_LONG_BIAS: "观察偏多",
    OBSERVE_SHORT_BIAS: "观察偏空",
    OK: "正常",
    PARTIAL: "部分可用",
    PARTIAL_SUPPORT: "部分支持系统结论",
    PACKET_OBSERVED: "卡内观测",
    PHASE_0_OBSERVE_ONLY: "观察层（不改信号）",
    PENDING_LLM: "等待 LLM 复核",
    PUT_CREDIT: "Put 信用价差",
    POSITIVE_GAMMA: "正 Gamma",
    POSITIVE_GAMMA_PINNING: "正 Gamma 钉住",
    PRICE_CONFIRM_NOT_ACTIVE: "价格确认未达激活阈值",
    PRICE_ONLY: "仅价格有效，CVD 不确认",
    PREVIOUS_CARD: "前一卡",
    PREPARE_LONG: "准备做多",
    PREPARE_SHORT: "准备做空",
    CALL_SKEW: "看涨偏斜",
    PUT_SKEW: "看跌偏斜",
    RAISE_DURABILITY_TENTATIVE: "暂定升耐久",
    RISK_CONSTRAINT: "空间风险约束",
    SKIPPED: "已跳过",
    THIN: "薄",
    TAILWIND: "顺风",
    SHORT_GAMMA_AMPLIFYING: "短 Gamma 放大/反身",
    SHORT_BIAS: "空头偏好",
    SELL_ABSORBED_BULLISH: "卖盘被吸收，偏多",
    SELL_CONFIRMS_DOWN: "卖方流确认下行",
    SKEW: "偏斜",
    SOFT_GATE: "软性限制",
    SOURCE_AGE_EXCEEDED: "数据时效超限",
    STALE: "陈旧",
    SUPPORT: "支持系统结论",
    SUPPORTIVE: "支持",
    WATCH: "观察",
    WEAK: "弱",
    STRONG: "强",
    TRANSITION: "状态转移",
    TENTATIVE: "暂定",
    TRADE_SUPPORT_STRONG: "强交易支持",
    TRADE_SUPPORT_REVIEW: "结构复核支持",
    TRADE_SUPPORT_WEAK: "弱交易支持",
    UNCALIBRATED: "未校准",
    UNCLEAR: "不明",
    UNKNOWN: "未知",
    UNABLE_TO_JUDGE: "无法判断",
    VALID: "有效",
    WAIT_CONFIRMATION: "等待确认",
    WAIT_FOR_EVIDENCE: "等待证据",
    DIRECTION_OWNER: "方向主干",
    FLOW_CONFIRM_COMPONENT: "主动流确认分量",
    FLOW_CONFIRMATION: "主动流确认",
    OPTION_GAMMA_STRUCTURE: "期权 Gamma 结构",
    OPTION_SKEW_DIRECTION: "期权偏斜方向",
    BLOCKED: "已被阻断",
    CONTINUING: "延续",
    CONFLICTED: "有冲突",
    DETERIORATING: "恶化",
    IMPROVING: "改善",
    INSUFFICIENT: "依据不足",
    REVERSING: "反转",
    STABLE: "稳定",
    SUPPORTED: "有支持",
    OPPOSED: "有反对",
    NEUTRALIZED: "中和",
    DECISION_SUPPORT_COLLAPSE: "决策支持塌缩",
    FUNDING_CROWDING_ESCALATION: "资金拥挤升温",
    FUNDING_CROWDING_UP: "资金拥挤上升",
    GAMMA_REGIME_SHIFT: "Gamma 体制切换",
    MACRO_SHOCK: "宏观压力冲击",
    MULTI_DOMAIN_RISK_DETERIORATION: "多域风险恶化",
    RISK_HEADWIND_SIGN_FLIP: "逆风符号翻转",
    SKEW_REVERSAL: "偏斜反转",
    CRITICAL: "关键",
    ANCHORED: "锚定有效",
    COMFORTABLE: "舒适窗口",
    FRAGILE: "脆弱",
    SIGNAL_DURABLE: "信号耐用",
    US_T2_EARLY_REPRICE: "T2早段再定价",
    US_T2_CORE_COMFORT: "T2核心舒适区",
    US_T2_TIME_ONLY: "T2时间命中/日历降级",
    NORMAL_WINDOW: "非舒适窗口",
    ANCHOR_DURABLE: "锚耐用",
    ANCHOR_WEAK: "锚偏弱",
    ANCHOR_BROKEN: "锚失效",
    ANCHOR_DATA_GAP: "锚数据缺口",
    STRUCTURE_HEALTH_INDEX_NOT_PROBABILITY: "结构健康指数/非概率",
    LAYERED_HEURISTIC_V1: "分层启发式v1",
    PPE_ABSORPTIVE: "PPE吸收",
    PPE_MIXED_INSIDE_BAND: "PPE带内混合",
    PPE_FAVORABLE_EFFICIENT_NOT_AUTOPASS: "有利高效/不自动加分",
    PPE_ADVERSE_EFFICIENT: "不利高效移动",
    PPE_ADVERSE_BAND_BREAK: "不利高效脱锚",
    GEX_NEGATIVE_BUT_NOT_BREAKING: "负Gamma但未破坏",
    FUNDING_CROWDED_CONFIRMATION: "Funding拥挤确认",
    FUNDING_HEALTHY_CONFIRMATION: "Funding健康确认",
    HEALTHY_CONFIRMATION: "健康确认",
    CROWDED_CONFIRMATION: "拥挤确认",
    ADVERSE_LEVERAGE_BUILD: "不利杠杆堆积",
    DELEVERAGING_REPAIR: "去杠杆修复",
    NEUTRAL: "中性",
    DATA_GAP: "数据缺口",
    NO_CONFIDENCE_CHANGE: "原信号把握不变",
    NO_GATE_CHANGE: "当前限制不变",
    "signal_rating@1.0.0": "信号评级 v1",
    side_environment_v1: "环境与侧别支持",
    not_evaluated: "候选经济性未评估",
    not_established: "尚未建立"
  };

  const fieldLabels = {
    absolute_share_pct: "绝对贡献占比",
    action: "动作",
    adjustment_direction: "时区先验调整",
    age: "数据年龄",
    age_ms: "数据年龄(ms)",
    agreement: "一致性",
    agreement_factor: "一致性因子",
    agreement_raw: "原始一致性",
    agreement_with_system: "与系统结论关系",
    all_required_ready: "必需源全部就绪",
    affects_blocking: "是否影响阻断条件",
    affects_confidence: "是否影响方向把握",
    affects_trade_allowed: "是否影响交易许可",
    audit_scope: "审计边界",
    base_zone: "静态基础档位",
    block_kind: "阻断类型",
    boundary_buffer_min: "边界缓冲(分钟)",
    buffer_policy: "缓冲策略",
    call_wall: "看涨墙",
    backtest_delta_pp: "三年实测Δ复合(pp)",
    catalyst_exposure: "外源冲击暴露",
    caution_level: "谨慎等级",
    basis_cn: "理论依据",
    bias: "理论倾向",
    boundary_cn: "边界说明",
    calibrated: "是否校准",
    calibration_state: "校准状态",
    confidence: "置信度",
    confidence_calibration: "置信校准",
    confidence_final: "最终置信",
    confidence_multiplier: "置信乘子",
    confidence_policy: "置信红线",
    comfort_window: "舒适窗口",
    compat_backfill_applied: "兼容回填",
    compat_backfill_source: "兼容来源",
    confidence_pre_veto: "否决前置信",
    confidence_semantics: "置信语义",
    conviction: "定性把握度",
    counter_evidence: "反向证据",
    config_hash: "配置哈希",
    config_id: "配置 ID",
    configured: "配置权重",
    configured_weight: "配置权重",
    clock_window: "时段",
    conflict_ratio: "冲突比例",
    coverage: "覆盖率",
    coverage_factor: "覆盖因子",
    coverage_raw: "原始覆盖率",
    data_quality: "数据质量",
    data_quality_note: "数据质量说明",
    data_range: "数据区间",
    data_gaps: "数据缺口",
    data_status: "数据状态",
    comparison_quality: "比较质量",
    current_card_id: "当前状态",
    delta_abs: "变化量",
    delta_relative: "相对变化",
    decision_matrix: "决策矩阵",
    decision_state: "决策状态",
    direction: "方向",
    directional_bias: "方向偏向",
    dissent_keys: "反向证据键",
    display_label: "展示标签",
    distance_to_pin_pct: "距钉住点比例",
    dominant_aligned: "主要同向证据",
    dominant_dissent: "主要反向证据",
    dst_mode: "美国时制",
    effective: "有效权重",
    effective_zone: "有效时区档位",
    effective_weight: "有效权重",
    effective_weight_sum: "有效权重合计",
    evidence: "证据",
    evidence_level: "证据等级",
    evidence_strength: "证据强度",
    exclusion_reason: "排除原因",
    event_blackout: "事件黑名单",
    elapsed_ms: "经过时间",
    field: "字段",
    flow_confirm: "主动流确认",
    flip_point: "翻转点",
    flip: "翻转点",
    ggr_multiplier: "Gamma 置信乘子",
    gamma_regime_lens: "全局 Gamma 体制分析",
    hash: "哈希",
    hard_veto: "硬否决",
    has_block: "存在阻断",
    info: "信息量",
    integrity: "完整性",
    key_drivers: "关键驱动",
    lean: "方向倾向",
    level: "等级",
    liquidity_depth: "形成时流动性深度",
    llm_review_required: "需要 LLM 复核",
    london_dst_mode: "伦敦时制",
    magnet_level: "磁吸点位",
    market_price: "市场价格",
    market_state: "市场状态",
    method: "方法",
    missing_field_count: "缺失字段数",
    model: "模型",
    model_trade_support: "模型支持",
    net_gamma_notional_usd: "净 Gamma 名义额(USD)",
    net_gamma_notional: "净 Gamma 名义额",
    net_gex_sign: "Gamma 符号",
    gamma_regime: "Gamma 结构",
    gex_info: "GEX 空间",
    next_action: "下一步动作",
    not_trading_advice: "非交易建议",
    operator_hint_cn: "操作提示",
    headline_score: "耐久总分",
    headline_state: "耐久状态",
    durability_score: "耐用性分数",
    durability_state: "耐用性状态",
    headline_horizon_min: "主口径未来窗(分钟)",
    observed_at: "观测时间",
    participation: "参与状态",
    participation_status: "参与状态",
    phase: "阶段",
    pin_strike: "钉住行权价",
    pin: "钉住点",
    positioning_assumption_cn: "持仓符号假设",
    previous_card_id: "上一状态",
    price_anchor_durability: "价格锚耐久",
    anchor_native: "锚原生层",
    price_efficiency: "PPE路径效率层",
    options_gamma: "GEX/Gamma放大层",
    perp_funding: "Funding杠杆解释层",
    ppe: "PPE路径效率",
    ppe_source: "PPE来源",
    net_move_vs_signal: "净移动相对信号",
    inside_anchor_band: "收盘在锚带内",
    band_distance_ratio: "锚带距离倍数",
    risk_amplifier: "风险放大器",
    funding_aligns_with_signal: "Funding与信号同向",
    score_semantics: "分数语义",
    score_quality: "评分质量",
    score_method: "评分方法",
    premise_durability: "前提耐久度",
    put_wall: "看跌墙",
    quality: "质量",
    ratio: "比例",
    reason: "原因",
    rationale_code: "时段代码",
    record_hash: "记录哈希",
    reviewed_at: "复核时间",
    regime: "Gamma 状态",
    regime_extremity: "体制极端度",
    regime_strength: "状态强度",
    reliability: "可靠性",
    required: "是否必需",
    rank: "历史分位",
    rank_pct: "Rank 百分位",
    abs_rank_pct: "绝对值 Rank 百分位",
    sample_bars: "样本K线数",
    sample_count: "样本数",
    score_final: "最终得分",
    source: "数据源",
    source_ref: "来源引用",
    source_snapshot_hash: "源快照哈希",
    signal_durability: "信号耐用性层",
    layer_scores: "分层评分",
    spatial_safety: "空间安全",
    status: "状态",
    input_packet_hash: "输入包哈希",
    prompt_version: "提示词版本",
    provider: "模型服务商",
    audit_metadata: "审计元数据",
    research_grade: "研究等级",
    core_skeleton: "核心骨架",
    strength: "强度",
    threshold: "阈值",
    theoretical_active_view: "理论主动倾向",
    transition: "边界缓冲状态",
    conviction_effect_on_directional_view: "对方向把握的影响",
    data_quality_cn: "数据质量说明",
    derived_blind: "真盲读生成",
    dominant_tail_risk_cn: "主要尾部风险",
    dynamics_cn: "体制动力学",
    is_not_a_signal: "非信号",
    key_levels: "关键位",
    lens_is_risk_overlay_not_direction: "风险叠加，不是方向",
    audit_dissent: "审计异议",
    context_warnings: "上下文警示",
    execution_allowed: "执行许可",
    execution_permission_note: "执行许可说明",
    temporal_durability: "时间耐久",
    temporal_session: "时段前提",
    trade_allowed: "是否允许交易",
    utc8_time: "UTC+8 时间",
    validation_basis: "验证依据",
    validation_status: "验证状态",
    value: "值",
    veto: "否决",
    veto_applied: "是否应用否决",
    vote: "投票",
    weighted: "加权贡献",
    weighted_contribution: "加权贡献",
    weighted_vote_sum: "加权投票和",
    weekend_adjustment: "周末修正",
    baseline_24h: "24 小时基线",
    current: "当前值",
    cross_factor_interactions: "跨因子相互作用",
    distinguishes_observation_from_causality: "区分观察与因果",
    domain: "领域",
    domain_change_summaries: "领域变化摘要",
    fact_cn: "事实说明",
    invalid_if: "失效条件",
    materiality: "材料性",
    materiality_score: "材料性分数",
    no_external_data: "不使用外部数据",
    no_trading_instruction: "不含交易建议",
    observed_changes: "观察到的变化",
    operator_focus: "人工观察重点",
    previous: "上一值",
    raw_change_count: "原始变化数",
    recent_5_trajectory: "最近 5 次轨迹",
    role: "字段角色",
    signal_continuity: "连续性",
    trajectory_state: "轨迹状态"
  };

  const advisoryRecommendationLabels = {
    SELL_PUT_SPREAD_REVIEW: "复核卖出 Put 价差",
    SELL_CALL_SPREAD_REVIEW: "复核卖出 Call 价差",
    NEUTRAL_SINGLE_SIDE_REVIEW: "中性单侧结构复核",
    WAIT_FOR_CONFIRMATION: "等待确认",
    NO_TRADE: "不进入交易复核",
    UNABLE_TO_JUDGE: "无法判断"
  };
  const advisoryContainmentLabels = {
    ESTABLISHED: "成立",
    INCOMPLETE: "不完整",
    FAILED: "不成立",
    UNABLE_TO_JUDGE: "无法判断"
  };
  const advisoryPremiumFitLabels = {
    FIT: "适配",
    CONDITIONAL: "有条件适配",
    NOT_FIT: "不适配",
    UNABLE_TO_JUDGE: "无法判断"
  };
  const advisoryLiquidityLabels = {
    ALIGNED: "匹配",
    CAUTION: "谨慎",
    TIME_ONLY: "仅时段提醒",
    UNKNOWN: "未定"
  };
  const advisoryWarningLabels = {
    NONE: "无额外提醒",
    INFO: "信息提醒",
    CAUTION: "谨慎提醒",
    HIGH: "高提醒"
  };
  const future24hSourceLabels = {
    MODEL_ESTIMATED: "模型估算",
    PACKET_OBSERVED: "卡内观测"
  };

  const $ = (selector) => document.querySelector(selector);
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
  const asArray = (value) => Array.isArray(value) ? value : [];
  const asObject = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const get = (object, path, fallback = null) => {
    const value = path.split(".").reduce((current, key) => current == null ? undefined : current[key], object);
    return value === undefined ? fallback : value;
  };
  const isNullish = (value) => value === null || value === undefined;
  const isBlank = (value) => isNullish(value) || value === "";
  const rawEnum = (value) => String(value ?? "");
  const semanticLabel = (value) => {
    if (isNullish(value)) return "暂缺";
    const raw = rawEnum(value);
    const translated = enumLabels[raw] || enumLabels[raw.toUpperCase()];
    return translated || normalizeComfortText(raw, raw);
  };
  const semanticCompact = (value) => {
    if (typeof value === "boolean") return booleanText(value);
    const raw = rawEnum(value);
    return enumLabels[raw] || enumLabels[raw.toUpperCase()] || raw;
  };
  const normalizeFieldKey = (label) => String(label ?? "")
    .trim()
    .replace(/[A-Z]/g, (match) => `_${match.toLowerCase()}`)
    .toLowerCase()
    .replace(/^_/, "")
    .replace(/[.\s\-/]+/g, "_")
    .replace(/[^a-z0-9_]+/g, "")
    .replace(/_+/g, "_");
  const fieldLabel = (label) => {
    const raw = String(label ?? "");
    if (!raw) return raw;
    const normalized = normalizeFieldKey(raw);
    const last = normalizeFieldKey(raw.split(/[.\[\]]+/).filter(Boolean).at(-1) || raw);
    const translated = fieldLabels[normalized] || fieldLabels[last];
    if (translated) return translated;
    return /[._]/.test(raw) ? "字段" : raw;
  };
  const isEnum = (value) => typeof value === "string" && /^[A-Z][A-Z0-9_/-]*$/.test(value);
  const number = (value, digits = 3) => {
    if (isNullish(value)) return "暂缺";
    if (typeof value !== "number") return String(value);
    return new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(value);
  };
  const percent = (value, digits = 1) => isNullish(value) ? "暂缺" : `${number(value * 100, digits)}%`;
  const booleanText = (value) => isNullish(value) ? "暂缺" : value ? "是" : "否";
  const scalarText = (value, options = {}) => {
    if (isNullish(value)) return "暂缺";
    if (typeof value === "boolean") return booleanText(value);
    if (typeof value === "number") return number(value, options.digits ?? 4);
    if (Array.isArray(value) || typeof value === "object") return rawValueText(value);
    if (isEnum(value) && options.translate !== false) return semanticLabel(value);
    return String(value);
  };
  const valueHtml = (value, options = {}) => {
    const missing = isNullish(value);
    const text = missing ? (options.nullText || scalarText(value, options)) : scalarText(value, options);
    const className = missing ? (options.nullClass || "null-value") : "";
    return `<span class="${className}">${escapeHtml(text)}</span>`;
  };
  const benignNullHtml = (text) => valueHtml(null, {
    nullText: text,
    nullClass: "benign-null-value",
    translate: false
  });
  const pctPoint = (value, digits = 2) => {
    if (isNullish(value)) return "暂缺";
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return String(value);
    return `${number(numeric, digits)}%`;
  };
  const safeNumber = (value) => {
    if (isBlank(value)) return null;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  };
  const dateText = (iso, mode = "long") => {
    if (!iso) return "暂缺";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return String(iso);
    const options = mode === "short"
      ? { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Shanghai" }
      : { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: "Asia/Shanghai" };
    return new Intl.DateTimeFormat("zh-CN", options).format(date);
  };
  const ageText = (ageMs) => {
    if (isNullish(ageMs)) return "暂缺";
    if (ageMs < 1000) return `${number(ageMs, 0)} ms`;
    if (ageMs < 60000) return `${number(ageMs / 1000, 1)} 秒`;
    if (ageMs < 3600000) return `${number(ageMs / 60000, 1)} 分钟`;
    return `${number(ageMs / 3600000, 1)} 小时`;
  };
  const firstPresent = (...values) => values.find((value) => !isNullish(value) && value !== "");
  const firstTextValue = (...values) => values.find((value) => typeof value === "string" && value.trim() !== "");
  const firstBooleanValue = (...values) => values.find((value) => typeof value === "boolean");
  const boundaryHasBlock = (...values) => {
    if (values.some((value) => value === true)) return true;
    const value = firstBooleanValue(...values);
    return value === true;
  };
  const boundaryExecutionAllowed = (...values) => {
    const booleans = values.filter((value) => typeof value === "boolean");
    if (!booleans.length) return false;
    if (booleans.some((value) => value === false)) return false;
    return booleans.some((value) => value === true);
  };
  const nrStateText = (value) => {
    const raw = rawEnum(value).toUpperCase();
    const labels = {
      NR_IDLE: "空闲",
      NR_WAIT_ANCHOR_DAMAGE: "等待锚损伤",
      NR_WAIT_ANCHOR_REPAIR: "等待锚修复",
      NR_REPAIR_CANDIDATE: "修复候选",
      NR_REPAIR_CONFIRMED: "修复确认",
      NR_REPAIR_STALE: "修复已陈旧",
      NR_NOT_CONFIRMED: "未确认",
      NR_EXPIRED: "已失效",
      CONFIRMED: "已确认",
      ACTIVE: "激活",
      INACTIVE: "未激活",
      UNKNOWN: "未知"
    };
    return labels[raw] || semanticCompact(value) || "未声明";
  };
  const futureValidityText = (value) => {
    const raw = rawEnum(value).trim();
    if (!raw) return "未知";
    const labels = {
      unknown: "未知",
      UNKNOWN: "未知",
      not_evaluated: "未评估",
      NOT_EVALUATED: "未评估"
    };
    return labels[raw] || semanticCompact(raw) || raw;
  };
  const nrWindowStateInfo = (doc) => {
    const sources = [
      ["signal_window.neutral_repair", asObject(get(doc, "signal_window.neutral_repair", {}))],
      ["factor_cross_section.neutral_repair", asObject(get(doc, "factor_cross_section.neutral_repair", {}))],
      ["neutral_repair", asObject(get(doc, "neutral_repair", {}))],
      ["window", asObject(get(doc, "window", {}))],
      ["decision_matrix", asObject(get(doc, "decision_matrix", {}))]
    ];
    for (const [path, view] of sources) {
      const value = firstTextValue(view.state, view.nr_state, view.window_state, view.window, view.status);
      if (!isBlank(value)) return { value, source: path };
    }
    return { value: "UNKNOWN", source: "" };
  };
  const nrWindowMetric = (doc) => {
    const info = nrWindowStateInfo(doc);
    return {
      value: nrStateText(info.value),
      note: info.source ? "生命周期来源已记录" : "生命周期来源暂缺"
    };
  };
  const roundKindText = (doc) => {
    if (isFixedAnalysisRound(doc)) return "固定轮次";
    const eventType = firstTextValue(
      get(doc, "identity.event_type"),
      get(doc, "schema.record_type"),
      get(doc, "record_type")
    );
    return eventType ? semanticCompact(eventType) : "事件卡";
  };
  const indexSummaryText = (doc) => {
    if (hasSignalEvidenceV2Surface(doc)) {
      return [
        symbol(doc) || "N/A",
        roundKindText(doc),
        "总体证据评审"
      ].join("｜");
    }
    return [
      symbol(doc) || "N/A",
      semanticCompact(lean(doc)) || "方向未定",
      semanticCompact(support(doc)) || "边界未定",
      roundKindText(doc)
    ].join("｜");
  };
  const numericMs = (value) => {
    if (isNullish(value) || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.max(0, parsed) : null;
  };
  const isoFromEpochMs = (value) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return null;
    return new Date(parsed).toISOString();
  };
  const ageFromObservedAt = (doc, observedAt) => {
    if (!observedAt) return null;
    const confirmedMs = new Date(confirmedAt(doc)).getTime();
    const observedMs = new Date(observedAt).getTime();
    if (!Number.isFinite(confirmedMs) || !Number.isFinite(observedMs)) return null;
    return Math.max(0, confirmedMs - observedMs);
  };
  const observedAtFromAge = (doc, ageMs) => {
    const age = numericMs(ageMs);
    const confirmedMs = new Date(confirmedAt(doc)).getTime();
    if (age === null || !Number.isFinite(confirmedMs)) return null;
    return new Date(confirmedMs - age).toISOString();
  };
  const qualityFallbackPaths = {
    price: ["market_context"],
    neutral_repair: ["factor_cross_section.neutral_repair", "factor_cross_section.anchor", "signal_window"],
    tmvf: ["factor_cross_section.tmvf"],
    micro_flow: ["factor_cross_section.micro_flow"],
    macro_pressure: ["factor_cross_section.macro_pressure"],
    gamma_regime: ["factor_cross_section.gamma_regime"],
    gex_info: ["factor_cross_section.gex_info"],
    skew: ["factor_cross_section.skew"],
    funding: ["factor_cross_section.funding"]
  };
  function qualityFallbackObjects(doc, key) {
    return asArray(qualityFallbackPaths[key]).map((path) => asObject(get(doc, path, {})));
  }
  function firstFromObjects(objects, fields) {
    for (const object of objects) {
      for (const field of fields) {
        const value = object[field];
        if (!isNullish(value) && value !== "") return value;
      }
    }
    return null;
  }
  function qualityReasonText(source) {
    const view = asObject(source);
    if (!isBlank(view.reason)) return view.reason;
    if (Array.isArray(view.reasons) && view.reasons.length) return view.reasons.filter(Boolean).join("; ");
    if (!isBlank(view.fetch_error)) return view.fetch_error;
    if (!isBlank(view.last_error)) return view.last_error;
    const status = rawEnum(view.status).toUpperCase();
    if (status === "OK") return "OK";
    if (status === "LKGV_CACHE") return "缓存可用，主体数据完整";
    if (status.includes("WARMING_UP")) return "样本积累中";
    if (status.includes("CACHE")) return "缓存可用";
    return null;
  }
  function qualitySourceView(doc, key, source) {
    const original = asObject(source);
    const fallbacks = qualityFallbackObjects(doc, key);
    const sourceRef = firstPresent(
      original.source_ref,
      firstFromObjects(fallbacks, ["source_ref", "price_source", "source", "source_url"])
    );
    let ageMs = numericMs(firstPresent(
      original.age_ms,
      firstFromObjects(fallbacks, ["age_ms", "data_age_ms", "cache_age_ms", "source_age_ms", "fetch_age_ms"])
    ));
    let observedAt = firstPresent(
      original.observed_at,
      firstFromObjects(fallbacks, ["observed_at", "fetched_at", "last_success_at", "last_data_time", "last_data_at", "updated_at"])
    );
    const epochObserved = firstFromObjects(fallbacks, ["observed_time_ms", "fetched_at_ms", "last_data_ms", "last_refresh_ms"]);
    if (isNullish(observedAt) && !isNullish(epochObserved)) observedAt = isoFromEpochMs(epochObserved);
    if (isNullish(observedAt) && !isNullish(ageMs)) observedAt = observedAtFromAge(doc, ageMs);
    if (isNullish(ageMs) && !isNullish(observedAt)) ageMs = ageFromObservedAt(doc, observedAt);
    if (rawEnum(original.status).toUpperCase() === "OK" && ["price", "neutral_repair"].includes(key)) {
      ageMs = isNullish(ageMs) ? 0 : ageMs;
      observedAt = observedAt || confirmedAt(doc);
    }
    return {
      ...original,
      observed_at: observedAt,
      age_ms: ageMs,
      source_ref: sourceRef,
      reason: qualityReasonText(original)
    };
  }
  const cardId = (doc) => get(doc, "identity.card_id", get(doc, "card_id", "N/A"));
  const shortId = (doc) => get(doc, "identity.short_id", cardId(doc).slice(-4));
  const confirmedAt = (doc) => get(doc, "identity.confirmed_at", get(doc, "created_at"));
  const symbol = (doc) => get(doc, "identity.symbol", get(doc, "symbol", "N/A"));
  const analysisRound = (doc) => asObject(get(doc, "analysis_round", {}));
  const isFixedAnalysisRound = (doc) => {
    const tags = asArray(get(doc, "identity.tags", []));
    return get(doc, "identity.event_type") === "FIXED_ANALYSIS_ROUND"
      || tags.includes("FIXED_ROUND_ANALYSIS")
      || Object.keys(analysisRound(doc)).length > 0;
  };
  const decision = (doc) => asObject(get(doc, "decision", get(doc, "final_state", {})));
  const lean = (doc) => get(doc, "decision.lean", get(doc, "final_state.direction", "UNKNOWN"));
  const support = (doc) => get(doc, "decision.support_label", get(doc, "final_state.action", "UNKNOWN"));
  const qualityOverall = (doc) => get(doc, "quality.overall", "UNKNOWN");
  const sortByTimeDesc = (items) => [...items].sort((a, b) => new Date(confirmedAt(b)) - new Date(confirmedAt(a)));

  const badgeClass = (value) => {
    const normalized = rawEnum(value).toUpperCase();
    if (["OK", "ACTIVE", "ALLOWED", "BULLISH"].some((key) => normalized.includes(key))) return "is-good";
    if (["DEGRADED", "STALE", "MISSING", "HIGH", "BEARISH"].some((key) => normalized.includes(key))) return "is-bad";
    if (["WAIT", "NEUTRAL", "PARTIAL", "UNCALIBRATED", "EXCLUDED", "NON_VOTING", "LKGV_CACHE", "CACHE", "WARMING_UP"].some((key) => normalized.includes(key))) return "is-wait";
    return "";
  };
  const statusBadge = (label, value, solid = false) => (
    `<span class="badge ${solid ? "is-solid" : badgeClass(value)}">${label ? `${escapeHtml(label)}: ` : ""}${escapeHtml(semanticLabel(value))}</span>`
  );
  const statusBadgeCn = (label, value, solid = false) => (
    `<span class="badge ${solid ? "is-solid" : badgeClass(value)}">${label ? `${escapeHtml(label)}: ` : ""}${escapeHtml(semanticCompact(value))}</span>`
  );
  const metric = (label, value, note = "") => `
    <div class="metric">
      <span class="metric-label">${escapeHtml(fieldLabel(label))}</span>
      <span class="metric-value ${isNullish(value) ? "null-value" : ""}">${escapeHtml(scalarText(value, { translate: false, digits: 2 }))}</span>
      ${note ? `<span class="metric-note">${escapeHtml(note)}</span>` : ""}
    </div>
  `;
  function stripConfidenceCalibrationReminder(value) {
    if (typeof value !== "string") return value;
    return value
      .replace(/(置信度?\s*[0-9]+)\s*未校准/g, "$1")
      .replace(/(confidence\s*[0-9]+)\s*uncalibrated/gi, "$1");
  }
  function compactLongDecimalText(value) {
    return String(value ?? "").replace(/-?\d+\.\d{9,}/g, (match) => {
      const numeric = Number(match);
      return Number.isFinite(numeric) ? number(numeric, 4) : match;
    });
  }
  const kv = (key, value, options = {}) => `
    <div class="kv">
      <dt>${escapeHtml(fieldLabel(key))}</dt>
      <dd>${valueHtml(value, options)}</dd>
    </div>
  `;
  const kvCn = (key, value) => `
    <div class="kv">
      <dt>${escapeHtml(fieldLabel(key))}</dt>
      <dd>${valueHtml(semanticCompact(value || "UNKNOWN"), { translate: false })}</dd>
    </div>
  `;
  function gexKv(key, value) {
    return kv(key, value, {
      translate: false,
      nullText: "可选 GEX 未提供",
      nullClass: "benign-null-value"
    });
  }
  function pinDistanceText(doc, gamma, gex) {
    const explicit = firstPresent(
      gamma.distance_to_pin_pct,
      get(gamma, "pin.distance_to_pin_pct"),
      gex.distance_to_pin_pct
    );
    if (!isBlank(explicit)) {
      const direction = firstPresent(gamma.pin_pull_direction, get(gamma, "pin.pin_pull_direction"));
      const explicitPct = safeNumber(explicit);
      return `${pctPoint(explicitPct)}${direction ? ` (${semanticCompact(direction) || direction})` : ""}`;
    }
    const price = safeNumber(firstPresent(
      get(doc, "market_context.price"),
      gamma.spot_price,
      gamma.price,
      gex.spot_price,
      gex.price
    ));
    const pin = safeNumber(firstPresent(
      gamma.pin_strike,
      get(gamma, "pin.pin_strike"),
      gex.magnet_level,
      gex.magnet_price
    ));
    if (price === null || pin === null || price <= 0) return null;
    const diffPct = ((pin - price) / price) * 100;
    const direction = diffPct > 0 ? "UP" : (diffPct < 0 ? "DOWN" : "FLAT");
    return `${pctPoint(diffPct)} (${semanticCompact(direction) || direction})`;
  }
  function nullSemantics(path, scope = "") {
    const normalized = String(path || "").toLowerCase();
    const scoped = `${scope}.${normalized}`;
    if (/(^|\.)(fetch_error|last_error|error)$/.test(normalized)) return "无错误";
    if (/(^|\.)(warning|hard_warning)$/.test(normalized)) return "无警告";
    if (/(^|\.)(veto_reason)$/.test(normalized)) return "无否决原因";
    if (/(^|\.)(hard_veto)$/.test(normalized)) return "无硬否决";
    if (/(^|\.)(exclusion_reason)$/.test(normalized)) return "不适用";
    if (/(^|\.)(static_web_url)$/.test(scoped)) return "未配置";
    if (/(^|\.)(source_snapshot|config_snapshot)/.test(scoped)) return "当前卡未提供";
    return null;
  }
  const valueHtmlByPath = (path, value, options = {}) => {
    if (isNullish(value)) {
      const nullText = nullSemantics(path, options.scope);
      if (nullText) return benignNullHtml(nullText);
    }
    return valueHtml(value, options);
  };
  const rankPct = (metric, field = "rank_pct") => {
    const value = asObject(metric)[field];
    return isNullish(value) ? "暂缺 (null)" : `${number(value, 1)}%`;
  };
  const rankMetric = (rank, key) => asObject(asObject(asObject(rank).metrics)[key]);
  const rankValueLine = (metric) => {
    const value = asObject(metric).value;
    return isNullish(value) ? "" : `<span class="rank-meta">值 ${escapeHtml(scalarText(value, { translate: false, digits: 2 }))}</span>`;
  };
  const rankSampleLine = (metric) => {
    const sample = asObject(metric).sample_count;
    const quality = asObject(metric).quality;
    const parts = [];
    if (!isNullish(sample)) parts.push(`n=${scalarText(sample, { translate: false, digits: 0 })}`);
    if (!isNullish(quality)) parts.push(`quality=${scalarText(quality, { translate: false })}`);
    return parts.length ? `<span class="rank-meta">${escapeHtml(parts.join(" / "))}</span>` : "";
  };
  const rankKv = (label, metric, extra = "") => `
    <div class="kv rank-cell">
      <dt>${escapeHtml(label)}</dt>
      <dd>
        <span class="rank-primary">${escapeHtml(rankPct(metric))}</span>
        ${extra}
        ${rankValueLine(metric)}
        ${rankSampleLine(metric)}
      </dd>
    </div>
  `;
  const section = (title, purpose, content, id = "") => `
    <section class="section" ${id ? `id="${escapeHtml(id)}"` : ""}>
      <div class="section-header">
        <h2 class="section-title">${escapeHtml(title)}</h2>
        <p class="section-purpose">${escapeHtml(purpose || "")}</p>
      </div>
      ${content}
    </section>
  `;
  const listHtml = (items, emptyText = "无") => {
    const values = asArray(items);
    if (!values.length) return `<div class="empty-inline">${escapeHtml(emptyText)}</div>`;
    return `<ul class="plain-list">${values.map((item) => `<li>${escapeHtml(normalizeComfortText(item, "未说明"))}</li>`).join("")}</ul>`;
  };

  const isFileMode = () => window.location.protocol === "file:";
  const isHttpMode = () => window.location.protocol === "http:" || window.location.protocol === "https:";
  const publicLoadReason = (kind, status = "") => {
    if (kind === "manifest_http") return "信号卡索引暂时不可用，请稍后重试。";
    if (kind === "manifest_json") return "信号卡索引资料无法解析，请检查已生成的发布文件。";
    if (kind === "card_http") return "这张卡的资料暂时不可用，请稍后重试。";
    if (kind === "card_timeout") return "单卡资料响应超时，已停止等待；可稍后有限重试。";
    if (kind === "card_json") return "单卡资料格式非法，已阻止展示该卡详情。";
    if (kind === "missing_path") return "索引未提供单卡路径，无法加载详情。";
    if (kind === "fallback") return "本地回退预览资料未加载，请检查发布文件。";
    return "静态卡片暂不可用，请检查发布文件。";
  };
  function createLoadError(kind, status = "") {
    const error = new Error(kind);
    error.kind = kind;
    error.status = status;
    error.publicReason = publicLoadReason(kind, status);
    return error;
  }
  function normalizeManifestPath(item, summary, id) {
    return firstPresent(item.path, item.card_path, summary.path, summary.card_path, item.href, item.url, id ? `signal_cards/${id}.json` : "");
  }
  function normalizeManifestSummary(item, index = 0) {
    const raw = asObject(item);
    const summary = asObject(raw.summary);
    const identity = asObject(summary.identity);
    const decisionSummary = asObject(summary.decision);
    const qualitySummary = asObject(summary.quality);
    const displayLayers = asObject(summary.display_layers);
    const signalDurability = asObject(summary.signal_durability);
    const ratingSummary = asObject(firstPresent(summary.signal_rating_summary, raw.signal_rating_summary, {}));
    const comfortSummary = asObject(firstPresent(summary.signal_comfort_summary, raw.signal_comfort_summary, {}));
    const evidenceSummary = asObject(firstPresent(summary.signal_evidence_summary, raw.signal_evidence_summary, {}));
    const transitionContext = asObject(summary.transition_context);
    const id = firstPresent(identity.card_id, summary.card_id, raw.card_id, raw.id, raw.path, `manifest-card-${index + 1}`);
    const confirmed = firstPresent(identity.confirmed_at, summary.confirmed_at, raw.confirmed_at, raw.created_at);
    const path = normalizeManifestPath(raw, summary, id);
    const qualityValue = firstPresent(qualitySummary.overall, summary.quality, raw.quality, raw.quality_overall, "UNKNOWN");
    return {
      __partial: true,
      __card_path: path,
      __llm_review_status: firstPresent(summary.llm_review_status, raw.llm_review_status, "PENDING_LLM"),
      identity: {
        ...identity,
        card_id: id,
        short_id: firstPresent(identity.short_id, summary.short_id, raw.short_id, String(id).slice(-4)),
        confirmed_at: confirmed,
        symbol: firstPresent(identity.symbol, summary.symbol, raw.symbol, "N/A"),
        strategy_name: firstPresent(identity.strategy_name, summary.strategy_name, raw.strategy_name, ""),
        is_synthetic: firstPresent(identity.is_synthetic, summary.is_synthetic, raw.is_synthetic, false),
        event_type: firstPresent(identity.event_type, summary.event_type, raw.event_type, null)
      },
      decision: {
        ...decisionSummary,
        lean: firstPresent(decisionSummary.lean, summary.lean, raw.lean, raw.direction, "UNKNOWN"),
        support_label: firstPresent(decisionSummary.support_label, summary.support_label, raw.support_label, raw.action, "UNKNOWN"),
        confidence: firstPresent(decisionSummary.confidence, summary.confidence, raw.confidence, null)
      },
      quality: {
        ...qualitySummary,
        overall: qualityValue
      },
      display_layers: {
        ...displayLayers,
        headline: firstPresent(displayLayers.headline, summary.headline, raw.headline, "单卡详情待加载")
      },
      signal_rating_summary: ratingSummary,
      signal_comfort_summary: comfortSummary,
      signal_evidence_summary: evidenceSummary,
      signal_durability: signalDurability,
      transition_context: transitionContext
    };
  }
  function cacheDocument(doc) {
    const id = cardId(doc);
    if (!id || id === "N/A") return doc;
    const summary = documents.find((item) => cardId(item) === id);
    const summaryComfort = asObject(summary && summary.signal_comfort_summary);
    const docComfort = asObject(doc && doc.signal_comfort_summary);
    const summaryEvidence = asObject(summary && summary.signal_evidence_summary);
    const docEvidence = asObject(doc && doc.signal_evidence_summary);
    const enriched = summary
      ? {
          ...doc,
          ...((summary.__card_path && !doc.__card_path) ? { __card_path: summary.__card_path } : {}),
          ...((Object.keys(summaryComfort).length && !Object.keys(docComfort).length) ? { signal_comfort_summary: summaryComfort } : {}),
          ...((Object.keys(summaryEvidence).length && !Object.keys(docEvidence).length) ? { signal_evidence_summary: summaryEvidence } : {})
        }
      : doc;
    cardCache.set(id, enriched);
    documents = documents.map((item) => cardId(item) === id ? enriched : item);
    return enriched;
  }
  function loadFallbackScript() {
    if (Array.isArray(window.SIGNAL_CARD_FIXTURES) && window.SIGNAL_CARD_FIXTURES.length) {
      return Promise.resolve(window.SIGNAL_CARD_FIXTURES);
    }
    return new Promise((resolve, reject) => {
      if (typeof document.createElement !== "function") {
        reject(createLoadError("fallback"));
        return;
      }
      const script = document.createElement("script");
      script.src = "signal_cards/fallback.js";
      script.onload = () => resolve(asArray(window.SIGNAL_CARD_FIXTURES));
      script.onerror = () => reject(createLoadError("fallback"));
      const parent = document.head || document.body || document.documentElement;
      if (!parent || typeof parent.appendChild !== "function") {
        reject(createLoadError("fallback"));
        return;
      }
      parent.appendChild(script);
    });
  }
  async function fetchJsonWithTimeout(path, source) {
    let timerId = null;
    const timeout = new Promise((_, reject) => {
      timerId = setTimeout(() => reject(createLoadError(source === "manifest" ? "manifest_http" : "card_timeout")), CARD_FETCH_TIMEOUT_MS);
    });
    try {
      const response = await Promise.race([
        fetch(path, { cache: "no-store" }),
        timeout
      ]);
      clearTimeout(timerId);
      if (!response || response.ok !== true) {
        throw createLoadError(source === "manifest" ? "manifest_http" : "card_http", response && response.status);
      }
      try {
        return await response.json();
      } catch (_error) {
        throw createLoadError(source === "manifest" ? "manifest_json" : "card_json");
      }
    } finally {
      clearTimeout(timerId);
    }
  }
  async function loadDocuments() {
    if (isFileMode()) {
      try {
        const fallback = sortByTimeDesc(asArray(window.SIGNAL_CARD_FIXTURES || embedded));
        const loaded = fallback.length ? fallback : sortByTimeDesc(await loadFallbackScript());
        loaded.forEach((doc) => cardCache.set(cardId(doc), doc));
        loadState = { mode: "file_fallback", error: "" };
        return loaded;
      } catch (error) {
        loadState = { mode: "load_error", error: error.publicReason || publicLoadReason("fallback") };
        return [];
      }
    }
    if (!isHttpMode()) {
      const fallback = sortByTimeDesc(asArray(window.SIGNAL_CARD_FIXTURES || embedded));
      fallback.forEach((doc) => cardCache.set(cardId(doc), doc));
      loadState = { mode: "embedded", error: "" };
      return fallback;
    }
    try {
      const manifest = await fetchJsonWithTimeout("signal_cards/index.json", "manifest");
      const summaries = sortByTimeDesc(asArray(manifest.cards).map(normalizeManifestSummary)).slice(0, INITIAL_CARD_LIMIT);
      loadState = { mode: "manifest", error: "" };
      return summaries;
    } catch (error) {
      loadState = { mode: "load_error", error: error.publicReason || publicLoadReason("manifest_http") };
      return [];
    }
  }
  function cardLoadState(id) {
    return cardLoadStates.get(id) || { status: "idle", attempts: 0, publicReason: "" };
  }
  function canRetryCard(id) {
    return cardLoadState(id).attempts < MAX_CARD_LOAD_ATTEMPTS;
  }
  async function loadCardDetail(id, options = {}) {
    if (!id || cardCache.has(id)) return cardCache.get(id);
    const summary = documents.find((doc) => cardId(doc) === id);
    const current = cardLoadState(id);
    if (current.status === "loading" && current.promise) return current.promise;
    if (current.status === "error" && !options.retry && !(options.selected && current.prefetch && canRetryCard(id))) {
      throw createLoadError("card_http");
    }
    if (current.attempts >= MAX_CARD_LOAD_ATTEMPTS && (options.retry || options.selected)) {
      return Promise.reject(createLoadError("card_http"));
    }
    const path = summary && summary.__card_path;
    if (!path) {
      const error = createLoadError("missing_path");
      cardLoadStates.set(id, {
        status: "error",
        attempts: current.attempts + 1,
        publicReason: error.publicReason,
        prefetch: !!options.prefetch
      });
      throw error;
    }
    const attempts = current.attempts + 1;
    const promise = fetchJsonWithTimeout(path, "card")
      .then((doc) => {
        const object = asObject(doc);
        if (!Object.keys(object).length) throw createLoadError("card_json");
        const loaded = cacheDocument(object);
        cardLoadStates.set(id, {
          status: "loaded",
          attempts,
          publicReason: "",
          prefetch: !!options.prefetch
        });
        return loaded;
      })
      .catch((error) => {
        const publicReason = error.publicReason || publicLoadReason("card_http");
        cardLoadStates.set(id, {
          status: "error",
          attempts,
          publicReason,
          prefetch: !!options.prefetch
        });
        throw error;
      });
    cardLoadStates.set(id, {
      status: "loading",
      attempts,
      promise,
      publicReason: "",
      prefetch: !!options.prefetch
    });
    return promise;
  }

  function uniqueValues(path) {
    return [...new Set(documents.map((doc) => get(doc, path)).filter((value) => !isNullish(value)))].sort();
  }

  function allDocumentsUseSignalEvidenceV2() {
    return documents.length > 0 && documents.every((doc) => hasSignalEvidenceV2Surface(doc));
  }

  function populateSelect(selector, placeholder, values) {
    const select = $(selector);
    if (!select) return;
    const current = select.value;
    select.innerHTML = `<option value="">${escapeHtml(placeholder)}</option>`;
    values.forEach((value) => {
      select.insertAdjacentHTML("beforeend", `<option value="${escapeHtml(value)}">${escapeHtml(semanticLabel(value))}</option>`);
    });
    select.value = current;
  }

  function resetSelect(selector, placeholder) {
    const select = $(selector);
    if (!select) return;
    select.value = "";
    select.innerHTML = `<option value="">${escapeHtml(placeholder)}</option>`;
  }

  function setLegacyFilterGroupsVisible(visible) {
    [
      ["#directionFilterGroup", "#directionFilter", "全部方向", "direction"],
      ["#actionFilterGroup", "#actionFilter", "全部限制", "action"],
      ["#qualityFilterGroup", "#qualityFilter", "全部质量状态", "quality"]
    ].forEach(([groupSelector, filterSelector, placeholder, stateKey]) => {
      const group = $(groupSelector);
      if (group) group.hidden = !visible;
      if (!visible) {
        state[stateKey] = "";
        resetSelect(filterSelector, placeholder);
      }
    });
  }

  function populateFilters() {
    const showLegacyFilters = !allDocumentsUseSignalEvidenceV2();
    setLegacyFilterGroupsVisible(showLegacyFilters);
    if (!showLegacyFilters) return;
    populateSelect("#directionFilter", "全部方向", uniqueValues("decision.lean"));
    populateSelect("#actionFilter", "全部限制", uniqueValues("decision.support_label"));
    populateSelect("#qualityFilter", "全部质量状态", uniqueValues("quality.overall"));
  }

  function isMobileLayout() {
    return typeof window.matchMedia === "function" && window.matchMedia("(max-width: 980px)").matches;
  }

  function setMobileSidebarOpen(open) {
    const sidebar = $("#auditSidebar");
    const toggle = $("#mobileIndexToggle");
    if (!sidebar || !toggle) return;
    sidebar.classList.toggle("is-open", Boolean(open));
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    const label = toggle.querySelector ? toggle.querySelector("span") : null;
    if (label) label.textContent = open ? "收起信号/筛选" : "切换信号/筛选";
  }

  function setupMobileIndexToggle() {
    const toggle = $("#mobileIndexToggle");
    if (!toggle) return;
    toggle.addEventListener("click", () => {
      const sidebar = $("#auditSidebar");
      setMobileSidebarOpen(!sidebar || !sidebar.classList.contains("is-open"));
    });
    if (typeof window.matchMedia === "function") {
      const media = window.matchMedia("(max-width: 980px)");
      const sync = () => setMobileSidebarOpen(false);
      if (media.addEventListener) media.addEventListener("change", sync);
    }
  }

  function closeMobileSidebarAfterSelect() {
    if (!isMobileLayout()) return;
    setMobileSidebarOpen(false);
    const documentView = $("#documentView");
    if (documentView && typeof documentView.scrollIntoView === "function") {
      documentView.scrollIntoView({ block: "start" });
    } else if (typeof window.scrollTo === "function") {
      window.scrollTo({ top: 0 });
    }
  }

  function setupFilterEvents() {
    $("#searchInput").addEventListener("input", (event) => {
      state.query = event.target.value.trim().toLowerCase();
      render();
    });
    $("#directionFilter").addEventListener("change", (event) => {
      state.direction = event.target.value;
      render();
    });
    $("#actionFilter").addEventListener("change", (event) => {
      state.action = event.target.value;
      render();
    });
    $("#qualityFilter").addEventListener("change", (event) => {
      state.quality = event.target.value;
      render();
    });
    $("#gradeFilter").addEventListener("change", (event) => {
      state.grade = event.target.value;
      render();
    });
  }

  function filteredDocuments() {
    const useLegacyFilters = !allDocumentsUseSignalEvidenceV2();
    return documents.filter((doc) => {
      const isV2 = hasSignalEvidenceV2Surface(doc);
      const legacySearchTerms = isV2 ? [] : [
        lean(doc), semanticLabel(lean(doc)), support(doc), semanticLabel(support(doc)),
        qualityOverall(doc), semanticLabel(qualityOverall(doc)), get(doc, "display_layers.headline"),
        signalRatingSearchText(doc), signalComfortSearchText(doc)
      ];
      const haystack = [
        cardId(doc), symbol(doc), get(doc, "identity.strategy_name"),
        ...legacySearchTerms,
        signalEvidenceSearchText(doc)
      ].join(" ").toLowerCase();
      return (!state.query || haystack.includes(state.query))
        && (!useLegacyFilters || !state.direction || lean(doc) === state.direction)
        && (!useLegacyFilters || !state.action || support(doc) === state.action)
        && (!useLegacyFilters || !state.quality || qualityOverall(doc) === state.quality)
        && comfortFilterMatches(doc);
    }).sort((a, b) => new Date(confirmedAt(b)) - new Date(confirmedAt(a)));
  }

  function renderIndex(list) {
    const countText = `${list.length} / ${documents.length}`;
    $("#resultCount").textContent = countText;
    const mobileCount = $("#mobileResultCount");
    if (mobileCount) mobileCount.textContent = countText;
    if (!list.length) {
      state.currentId = null;
      $("#indexList").innerHTML = `<div class="empty">没有匹配的信号文档</div>`;
      return;
    }
    if (!list.some((doc) => cardId(doc) === state.currentId)) state.currentId = cardId(list[0]);
    $("#indexList").innerHTML = list.map((doc) => {
      const view = cardCache.get(cardId(doc)) || doc;
      const active = cardId(view) === state.currentId ? "is-active" : "";
      const ratingStats = signalEvidenceIndexStats(view) || signalComfortIndexStats(view);
      return `
        <button class="index-item ${active}" type="button" data-card-id="${escapeHtml(cardId(view))}">
          <div class="index-topline">
            <span class="index-symbol">${escapeHtml(symbol(view))}</span>
            <span class="index-time">${escapeHtml(dateText(confirmedAt(view), "short"))}</span>
          </div>
          <p class="index-summary">${escapeHtml(indexSummaryText(view))}</p>
          <div class="mini-stats">
            ${ratingStats.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}
          </div>
          ${isFixedAnalysisRound(view) ? `<div class="transition-badges"><span class="fixed-round-badge">固定轮次</span></div>` : ""}
          ${hasSignalEvidenceV2Surface(view) ? "" : renderIndexTransitionBadges(view)}
        </button>
      `;
    }).join("");
    document.querySelectorAll(".index-item").forEach((button) => {
      button.addEventListener("click", () => {
        state.currentId = button.dataset.cardId;
        render();
        closeMobileSidebarAfterSelect();
        prefetchRecentCards();
      });
    });
  }

  function transitionContext(doc) {
    return asObject(get(doc, "transition_context", {}));
  }

  function renderIndexTransitionBadges(doc) {
    const ctx = transitionContext(doc);
    if (!Object.keys(ctx).length) return "";
    const flags = asArray(ctx.cross_domain_flags)
      .filter((flag) => flag !== "FIXED_ROUND_ANALYSIS");
    return `
      <div class="transition-badges">
        <span>${escapeHtml(semanticCompact(ctx.comparison_quality || get(ctx, "relation.comparison_quality", "UNKNOWN")))}</span>
        <span>${escapeHtml(semanticCompact(flags[0] || "AUDIT_ONLY"))}</span>
      </div>
    `;
  }

  function llmReviewContent(doc) {
    const review = asObject(get(doc, "llm_review", {}));
    return Object.assign({}, asObject(review.content), review);
  }

  function advisoryLabel(labels, value, fallback = "未定") {
    const key = rawEnum(value).toUpperCase();
    return labels[key] || fallback;
  }

  function advisoryItemText(item) {
    if (typeof item === "string") return item;
    return asObject(item).premise_cn || "";
  }

  function advisoryTexts(items) {
    return asArray(items).map(advisoryItemText).filter(Boolean);
  }

  function advisoryTextList(items, emptyText) {
    const values = advisoryTexts(items);
    return listHtml(values, emptyText);
  }

  function advisoryReaderText(value, fallback = "未说明") {
    return normalizeComfortText(value, fallback);
  }

  function llmReviewStatus(doc) {
    const review = asObject(get(doc, "llm_review", {}));
    return rawEnum(firstPresent(review.status, get(doc, "__llm_review_status"), get(doc, "decision_matrix.audit_dissent", "PENDING_LLM"))).toUpperCase();
  }

  function llmReviewHasFailed(doc) {
    const status = llmReviewStatus(doc);
    return [
      "ERROR",
      "FAILED",
      "INVALID_OUTPUT",
      "SUPPRESS_LLM_TEXT"
    ].includes(status);
  }

  function llmReviewPublished(doc) {
    const review = asObject(get(doc, "llm_review", {}));
    return Object.keys(review).length > 0 && llmReviewStatus(doc) === "OK";
  }

  function llmReviewUnavailableReason(doc, invalidContent = false) {
    const review = asObject(get(doc, "llm_review", {}));
    if (!Object.keys(review).length) {
      return "尚无可用的深入复核内容；先保留本卡市场观察。";
    }
    if (llmReviewStatus(doc) !== "OK") {
      return "深入复核内容暂不可用；先保留本卡市场观察。";
    }
    if (invalidContent) {
      return "深入复核内容暂不可用；先看行动结论、市场事实和边界。";
    }
    return "尚无可用的深入复核内容；先保留本卡市场观察。";
  }

  function future24hMachineLeak(text) {
    return /\[object Object\]|EV_[A-Z0-9_]+|MODEL_ESTIMATED|PACKET_OBSERVED|\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b|\b[a-z]+(?:_[a-z0-9]+)+(?:\.[a-z0-9_]+)*\b/.test(text);
  }

  function future24hChineseParagraph(value) {
    if (typeof value !== "string") return "";
    const text = value
      .replaceAll("rr_blend", "期权偏斜融合指标")
      .replaceAll("gamma_regime.flip_point", "卡内 Gamma 翻转点")
      .replaceAll("gamma_regime.pin_strike", "卡内 Gamma 钉住位")
      .replaceAll("gamma_regime.max_gamma_strike", "卡内最大 Gamma 观察点位")
      .replaceAll("gex_info.call_wall", "卡内上方 Gamma 墙")
      .replaceAll("gex_info.put_wall", "卡内下方 Gamma 墙")
      .replaceAll("gex_info.magnet_level", "卡内 GEX 磁吸位")
      .replaceAll("gex_info.max_gamma_strike", "卡内最大 Gamma 观察点位")
      .replaceAll("max_gamma_strike", "最大 Gamma 观察点位")
      .replaceAll("gamma_regime", "Gamma 体制")
      .replaceAll("BINANCE_SPOT", "币安现货")
      .replaceAll("pin_strike", "Gamma 钉住位")
      .replaceAll("flip_point", "Gamma 翻转点")
      .replaceAll("call_wall", "上方 Gamma 墙")
      .replaceAll("put_wall", "下方 Gamma 墙")
      .replaceAll("magnet_level", "GEX 磁吸位")
      .replace(/\s+/g, " ").trim();
    if (!/[\u4e00-\u9fff]/.test(text)) return "";
    if (future24hMachineLeak(text)) return "";
    return text;
  }

  function future24hReaderSummary(value) {
    const text = future24hChineseParagraph(value);
    if (!text) return "";
    return compactLongDecimalText(text)
      .replace(/主观情景权重为[^；。]+[；。]?/g, "多情景细节已保留在完整资料；")
      .replace(/上涨\s*\d+%、\s*下跌\s*\d+%、\s*区间\s*\d+%[；。]?/g, "多情景细节已保留在完整资料；")
      .replace(/[，,]?\s*分布为区间\s*\d+%\s*[，,]\s*上行\s*\d+%\s*[，,]\s*下行\s*\d+%[；。]?/g, "。")
      .replace(/[，,]?\s*区间\s*\d+%\s*[，,]\s*上行\s*\d+%\s*[，,]\s*下行\s*\d+%[；。]?/g, "。")
      .replace(/权重/g, "多情景细节")
      .replace(/多情景细节为[；。]?/g, "")
      .replace(/基准情景为([^，。；;]+)[，,]\s*[；。]/g, "基准情景为$1。")
      .replace(/([；。]){2,}/g, "$1")
      .replace(/\s+/g, " ")
      .trim();
  }

  function future24hReportRows(value) {
    if (Array.isArray(value)) return value.map((item) => asObject(item)).filter((item) => Object.keys(item).length);
    return Object.entries(asObject(value))
      .map(([key, child]) => {
        const row = asObject(child);
        return Object.keys(row).length ? { key, ...row } : { key, value: child };
      })
      .filter((row) => Object.keys(row).length);
  }

  function future24hFirstRows(...values) {
    for (const value of values) {
      const rows = future24hReportRows(value);
      if (rows.length) return rows;
    }
    return [];
  }

  function future24hFirstObject(...values) {
    for (const value of values) {
      const object = asObject(value);
      if (Object.keys(object).length) return object;
    }
    return {};
  }

  function future24hRowLabel(row) {
    return future24hChineseParagraph(firstPresent(
      row.label_cn,
      row.name_cn,
      row.title_cn,
      row.factor_cn,
      row.domain_cn,
      row.point_cn,
      row.level_cn
    ));
  }

  function future24hSourceKind(row) {
    return rawEnum(firstPresent(
      row.source_kind,
      row.source_type,
      row.value_source,
      row.evidence_source
    )).toUpperCase();
  }

  function future24hSourceText(row) {
    return future24hSourceLabels[future24hSourceKind(row)] || "";
  }

  function future24hRowsAreComplete(rows) {
    return rows.length > 0 && rows.every((row) => future24hRowLabel(row) && future24hSourceText(row));
  }

  function future24hPointRowIsSafe(row) {
    const item = asObject(row);
    return Number.isFinite(Number(item.price))
      && future24hChineseParagraph(item.role_cn)
      && future24hChineseParagraph(item.basis_cn)
      && future24hSourceText(item);
  }

  function future24hReadableScalar(value) {
    if (isNullish(value) || value === "") return "";
    if (typeof value === "number") return number(value, 4);
    if (typeof value === "boolean") return value ? "是" : "否";
    if (Array.isArray(value) || typeof value === "object") return "";
    const text = rawEnum(value).replace(/\s+/g, " ").trim();
    const sourceText = future24hSourceLabels[text.toUpperCase()];
    if (sourceText) return sourceText;
    const compact = semanticCompact(text);
    if (compact !== text) return compact;
    if (future24hMachineLeak(text)) return "已记录";
    return text;
  }

  function future24hFirstReadable(row, fields, fallback = "未说明") {
    for (const field of fields) {
      const text = future24hReadableScalar(row[field]);
      if (text) return text;
    }
    return fallback;
  }

  function future24hValidationRows(validation) {
    return flatten(validation)
      .map(([path, value]) => [path, future24hReadableScalar(value)])
      .filter(([, value]) => value)
      .slice(0, 18);
  }

  function future24hReportCandidate(doc, advisory = null) {
    const review = asObject(get(doc, "llm_review", {}));
    const content = llmReviewContent(doc);
    const candidates = [
      get(advisory || {}, "future_24h_bayesian_report", null),
      get(content, "integrated_trade_advisory.future_24h_bayesian_report", null),
      get(review, "content.integrated_trade_advisory.future_24h_bayesian_report", null),
      get(content, "future_24h_bayesian_report", null),
      get(review, "content.future_24h_bayesian_report", null),
      get(review, "future_24h_bayesian_report", null),
      get(doc, "future_24h_bayesian_report", null)
    ];
    return asObject(candidates.find((candidate) => Object.keys(asObject(candidate)).length));
  }

  function future24hBayesianReport(doc, advisory = null) {
    const review = asObject(get(doc, "llm_review", {}));
    const content = llmReviewContent(doc);
    if (rawEnum(review.status).toUpperCase() !== "OK") return null;
    const report = future24hReportCandidate(doc, advisory);
    if (!Object.keys(report).length) return null;
    if (report.schema_version !== "future_24h_bayesian_report@1.0.0"
      || Number(report.horizon_hours) !== 24
      || report.input_scope !== "PACKET_FACTS_PLUS_MODEL_PRIOR_NO_LIVE_SEARCH"
      || report.live_external_data_used !== false
      || !["UP", "DOWN", "RANGE"].includes(rawEnum(report.base_case).toUpperCase())) return null;
    const parent = asObject(advisory || get(content, "integrated_trade_advisory", {}));
    if (parent.audit_only !== true || parent.trade_authorization !== false) return null;
    const summary = future24hReaderSummary(report.report_cn);
    const rawWeights = asObject(report.posterior_weights_pct);
    const weightValues = [rawWeights.up, rawWeights.down, rawWeights.range];
    if (!weightValues.every((value) => Number.isInteger(value) && value >= 0 && value <= 100)
      || weightValues.reduce((sum, value) => sum + value, 0) !== 100) return null;
    const weights = [
      { label_cn: "上行情景", weight: `${rawWeights.up}%` },
      { label_cn: "下行情景", weight: `${rawWeights.down}%` },
      { label_cn: "区间情景", weight: `${rawWeights.range}%` }
    ];
    const rawPointSources = asArray(report.key_levels);
    const pointSources = rawPointSources.map((row) => asObject(row)).filter(future24hPointRowIsSafe);
    const hiddenPointSourceCount = rawPointSources.length - pointSources.length;
    const validation = asObject(report.policy_validation);
    if (!summary || summary.length > 900 || rawPointSources.length > 4
      || validation.passed !== true
      || !future24hValidationRows(validation).length) {
      return null;
    }
    return { summary, weights, pointSources, hiddenPointSourceCount, validation };
  }

  function renderFuture24hBayesianSummary(doc, advisory) {
    const report = future24hBayesianReport(doc, advisory);
    if (!report) return "";
    return `<div class="future-24h-summary"><strong>未来 24 小时第一性推断</strong><p>${escapeHtml(report.summary)}</p></div>`;
  }

  function future24hSourceCell(row, doc) {
    const label = future24hSourceText(row);
    const ref = firstPresent(row.source_ref, row.source_path, row.packet_path);
    if (future24hSourceKind(row) === "PACKET_OBSERVED" && ref) {
      return sourceRefLink(ref, doc, label);
    }
    return `<span class="chip">${escapeHtml(label)}</span>`;
  }

  function renderFuture24hWeightRows(rows, doc) {
    return rows.map((row) => `
      <tr>
        <td>${escapeHtml(future24hRowLabel(row))}</td>
        <td><span class="chip">模型主观情景权重</span></td>
        <td class="num">${escapeHtml(future24hFirstReadable(row, ["weight", "effective_weight", "configured_weight", "posterior_weight", "probability"], "未提供"))}</td>
        <td>${escapeHtml(future24hFirstReadable(row, ["tendency_cn", "lean_cn", "direction_cn", "effect_cn", "basis_cn"], "未说明"))}</td>
      </tr>
    `).join("");
  }

  function renderFuture24hPointRows(rows, doc) {
    if (!rows.length) {
      return `<tr><td colspan="4">暂无可安全显示点位</td></tr>`;
    }
    return rows.map((row) => `
      <tr>
        <td>${escapeHtml(future24hChineseParagraph(row.role_cn))}</td>
        <td>${future24hSourceCell(row, doc)}</td>
        <td class="num">${escapeHtml(future24hFirstReadable(row, ["value_cn", "price_cn", "level_value", "value", "price", "level"], "未提供"))}</td>
        <td>${escapeHtml(future24hChineseParagraph(firstPresent(
          row.basis_cn,
          row.reason_cn,
          row.note_cn,
          row.description_cn
        )) || "未说明")}</td>
      </tr>
    `).join("");
  }

  function renderFuture24hPointDegradeNote(report) {
    const hidden = Number(report.hiddenPointSourceCount || 0);
    if (!Number.isFinite(hidden) || hidden <= 0) return "";
    return `<p class="future-24h-degrade-note">有 ${escapeHtml(number(hidden, 0))} 个点位因名称、依据或来源不可安全显示，已在低层列表中隐藏。</p>`;
  }

  function renderFuture24hValidationRows(validation) {
    return future24hValidationRows(validation).map(([path, value]) => `
      <tr>
        <td class="field-path">${escapeHtml(fieldLabel(path))}</td>
        <td>${escapeHtml(value)}</td>
      </tr>
    `).join("");
  }

  function renderFuture24hBayesianTrace(doc) {
    const report = future24hBayesianReport(doc);
    if (!report) return "";
    return `
      <details id="${escapeHtml(rawTraceId("future_24h_bayesian_report"))}" class="factor-detail future-24h-trace raw-trace-group">
        <summary><span>未来24小时贝叶斯报告追溯</span><span class="badge is-wait">只读辅助</span></summary>
        <div class="future-24h-trace-block">
          <h3 class="subsection-title">结构化权重</h3>
          <div class="table-wrap"><table class="future-24h-table"><thead><tr><th>维度</th><th>来源性质</th><th>权重</th><th>含义</th></tr></thead><tbody>${renderFuture24hWeightRows(report.weights, doc)}</tbody></table></div>
        </div>
        <div class="future-24h-trace-block">
          <h3 class="subsection-title">点位来源</h3>
          ${renderFuture24hPointDegradeNote(report)}
          <div class="table-wrap"><table class="future-24h-table"><thead><tr><th>点位</th><th>来源性质</th><th>数值</th><th>依据</th></tr></thead><tbody>${renderFuture24hPointRows(report.pointSources, doc)}</tbody></table></div>
        </div>
        <div class="future-24h-trace-block">
          <h3 class="subsection-title">验证信息</h3>
          <div class="table-wrap"><table class="future-24h-table"><thead><tr><th>字段</th><th>可读值</th></tr></thead><tbody>${renderFuture24hValidationRows(report.validation)}</tbody></table></div>
        </div>
      </details>
    `;
  }

  function integratedTradeAdvisory(doc) {
    const review = asObject(get(doc, "llm_review", {}));
    if (rawEnum(review.status).toUpperCase() !== "OK") return null;
    const content = llmReviewContent(doc);
    const direct = asObject(content.integrated_trade_advisory);
    const nested = asObject(get(review, "content.integrated_trade_advisory", {}));
    const advisory = Object.keys(direct).length ? direct : nested;
    if (!Object.keys(advisory).length) return null;

    const required = [
      "recommendation",
      "final_conclusion_cn",
      "cross_loop_rationale_cn",
      "containment_assessment",
      "premium_selling_fit",
      "side_basis_cn",
      "dominant_conflict_cn",
      "key_premises",
      "invalid_if",
      "next_observation_cn",
      "session_advisory",
      "source_alignment",
      "audit_only",
      "trade_authorization",
      "policy_validation"
    ];
    if (required.some((key) => !(key in advisory))) return null;
    if (advisory.audit_only !== true || advisory.trade_authorization !== false) return null;
    if (asObject(advisory.policy_validation).passed !== true) return null;
    if (!advisoryRecommendationLabels[rawEnum(advisory.recommendation).toUpperCase()]) return null;

    const containment = asObject(advisory.containment_assessment);
    const premiumFit = asObject(advisory.premium_selling_fit);
    const session = asObject(advisory.session_advisory);
    if (isBlank(advisory.final_conclusion_cn)
      || isBlank(advisory.cross_loop_rationale_cn)
      || isBlank(advisory.side_basis_cn)
      || isBlank(advisory.dominant_conflict_cn)
      || isBlank(advisory.next_observation_cn)
      || isBlank(advisory.source_alignment)
      || isBlank(containment.state)
      || isBlank(containment.basis_cn)
      || isBlank(premiumFit.state)
      || isBlank(premiumFit.basis_cn)
      || isBlank(session.liquidity_assessment)
      || isBlank(session.warning_level)
      || isBlank(session.basis_cn)
      || session.does_not_change_recommendation !== true
      || !advisoryTexts(advisory.key_premises).length
      || !advisoryTexts(advisory.invalid_if).length) {
      return null;
    }
    return advisory;
  }

  function renderIntegratedTradeAdvisory(doc) {
    const advisory = integratedTradeAdvisory(doc);
    if (!advisory) {
      const reason = llmReviewUnavailableReason(doc, llmReviewPublished(doc));
      return section("既有深入分析", "已有深入分析缺失时不补造建议；先看市场观察与边界。", `
        <div class="integrated-advisory-panel is-unavailable">
          <div class="integrated-advisory-head">
            <div class="integrated-advisory-conclusion">
              <span>结论</span>
              <strong>尚无可用的深入复核内容</strong>
              <p>${escapeHtml(reason)}</p>
            </div>
            <div class="integrated-advisory-recommendation">
              <span>旧综合建议</span>
              <strong>暂不展示</strong>
              <em>本区暂不展示</em>
            </div>
          </div>
        </div>
      `, "integrated-advisory");
    }
    const containment = asObject(advisory.containment_assessment);
    const premiumFit = asObject(advisory.premium_selling_fit);
    const session = asObject(advisory.session_advisory);
    const recommendation = advisoryLabel(advisoryRecommendationLabels, advisory.recommendation, "无法判断");
    const containmentState = advisoryLabel(advisoryContainmentLabels, containment.state, "无法判断");
    const premiumState = advisoryLabel(advisoryPremiumFitLabels, premiumFit.state, "无法判断");
    const liquidity = advisoryLabel(advisoryLiquidityLabels, session.liquidity_assessment, "未定");
    const warning = advisoryLabel(advisoryWarningLabels, session.warning_level, "提醒");
    return section("既有深入分析", "解释结构审计和下一观察，不覆盖本卡行动结论。", `
      <div class="integrated-advisory-panel">
        <div class="integrated-advisory-head">
          <div class="integrated-advisory-conclusion">
            <span>结论</span>
            <strong>${escapeHtml(advisoryReaderText(advisory.final_conclusion_cn))}</strong>
            <p>${escapeHtml(advisoryReaderText(advisory.cross_loop_rationale_cn))}</p>
          </div>
          <div class="integrated-advisory-recommendation">
            <span>旧综合建议</span>
            <strong>${escapeHtml(recommendation)}</strong>
            <em>只读辅助，不是交易许可</em>
          </div>
        </div>
        ${renderFuture24hBayesianSummary(doc, advisory)}
        <div class="integrated-advisory-grid">
          <div>
            <span>中性接管</span>
            <strong>${escapeHtml(containmentState)}</strong>
            <p>${escapeHtml(advisoryReaderText(containment.basis_cn))}</p>
          </div>
          <div>
            <span>卖方结构适配</span>
            <strong>${escapeHtml(premiumState)}</strong>
            <p>${escapeHtml(advisoryReaderText(premiumFit.basis_cn))}</p>
          </div>
          <div>
            <span>侧向依据</span>
            <p>${escapeHtml(advisoryReaderText(advisory.side_basis_cn))}</p>
          </div>
          <div>
            <span>主要冲突</span>
            <p>${escapeHtml(advisoryReaderText(advisory.dominant_conflict_cn))}</p>
          </div>
        </div>
        <div class="integrated-advisory-detail-grid">
          <div>
            <h3 class="subsection-title">关键前提</h3>
            ${advisoryTextList(advisory.key_premises, "暂无关键前提")}
          </div>
          <div>
            <h3 class="subsection-title">失效条件</h3>
            ${advisoryTextList(advisory.invalid_if, "暂无失效条件")}
          </div>
          <div>
            <h3 class="subsection-title">下一观察</h3>
            <p>${escapeHtml(advisoryReaderText(advisory.next_observation_cn))}</p>
          </div>
        </div>
        <div class="integrated-advisory-session">
          <div>
            <span>时段提醒</span>
            <strong>${escapeHtml(`${warning} / ${liquidity}`)}</strong>
          </div>
          <p>${escapeHtml(advisoryReaderText(session.basis_cn))}</p>
          <p class="integrated-advisory-session-note">只提醒，不改变信号方向或结构建议</p>
        </div>
      </div>
    `, "integrated-advisory");
  }

  function renderLlmReview(doc) {
    const review = asObject(get(doc, "llm_review", {}));
    const hasReview = Object.keys(review).length > 0;
    const matrix = asObject(get(doc, "decision_matrix", {}));
    const content = llmReviewContent(doc);
    const status = hasReview ? (review.status || "UNKNOWN") : (matrix.audit_dissent || "PENDING_LLM");
    const failed = hasReview && rawEnum(status).toUpperCase() !== "OK";
    if (failed || !hasReview) {
      const panelClass = failed ? "llm-review-panel is-error" : "llm-review-panel is-pending";
      return section("LLM 深入分析", "保留深入复核对本卡的补充解释；没有可用内容时先看市场事实。", `
        <div class="${panelClass}">
          <div class="llm-review-topline">
            <span class="badge ${failed ? "is-bad" : "is-wait"}">状态: ${failed ? "尚无可用内容" : "等待复核内容"}</span>
            <span class="badge is-wait">阅读状态: 暂缺</span>
          </div>
          <p class="llm-review-summary">${escapeHtml(llmReviewUnavailableReason(doc))}</p>
        </div>
      `, "llm-review");
    }
    const panelClass = failed ? "llm-review-panel is-error" : (hasReview ? "llm-review-panel" : "llm-review-panel is-pending");
    const summary = normalizeComfortText(content.summary_cn, "尚无可用的深入复核内容；先看行动结论和市场事实。");
    return section("LLM 深入分析", "保留深入复核对本卡的补充解释；没有可用内容时先看市场事实。", `
      <div class="${panelClass}">
        <div class="llm-review-topline">
          ${statusBadge("状态", status)}
          ${statusBadge("谨慎等级", content.caution_level || (hasReview ? "UNKNOWN" : "PENDING_LLM"))}
        </div>
        <p class="llm-review-summary">${escapeHtml(summary)}</p>
      </div>
      ${renderTheoreticalActiveView(content.theoretical_active_view)}
      ${renderGammaRegimeLens(content.gamma_regime_lens)}
      ${content.data_quality_note ? `<div class="llm-review-quality">${escapeHtml(normalizeComfortText(content.data_quality_note, ""))}</div>` : ""}
      <div class="two-column-notes llm-review-lists">
        <div><h3 class="subsection-title">支持系统结论的因素</h3>${listHtml(content.main_supporting_factors, "无")}</div>
        <div><h3 class="subsection-title">主要风险或冲突</h3>${listHtml(content.main_risks_or_conflicts, "无")}</div>
        <div><h3 class="subsection-title">人工观察重点</h3>${listHtml(content.operator_focus, "无")}</div>
        <div><h3 class="subsection-title">复核失效条件</h3>${listHtml(content.invalid_if, "无")}</div>
      </div>
    `, "llm-review");
  }

  function renderTransitionContext(doc) {
    const ctx = transitionContext(doc);
    if (!Object.keys(ctx).length) return "";
    const decisionTransition = asObject(ctx.decision_transition);
    const flags = asArray(ctx.cross_domain_flags)
      .filter((flag) => flag !== "FIXED_ROUND_ANALYSIS");
    const quality = ctx.comparison_quality || get(ctx, "relation.comparison_quality", "UNKNOWN");
    const elapsed = isNullish(ctx.elapsed_ms) ? null : ageText(ctx.elapsed_ms);
    const fixedSnapshotNote = isFixedAnalysisRound(doc)
      || get(ctx, "event_context.fixed_time_snapshot_diff") === true
      ? `<div class="llm-review-quality fixed-round-note">固定时间截面差分：基于北京时间 23:00 的固定轮次分析，不表示 Anchor+DIE 自然信号迁移。</div>`
      : "";
    return section("状态转移审计", "AUDIT_ONLY 变化链由 materializer 生成；前端只展示已物化字段，不计算 delta。", `
      <div class="transition-panel">
        <div class="llm-review-topline">
          ${statusBadge("AUDIT_ONLY", ctx.audit_scope || "AUDIT_ONLY")}
          ${statusBadge("比较质量", quality)}
          ${flags.slice(0, 3).map((flag) => statusBadge("", flag)).join("")}
        </div>
        <div class="transition-timeline">
          <span>状态路径</span>
          <strong>${escapeHtml(transitionTimelineText(ctx, doc))}</strong>
        </div>
        ${fixedSnapshotNote}
        ${renderTransitionLlmReview(doc)}
        ${renderTransitionCoreSummary(ctx)}
        ${renderTransitionAuditMetadata(ctx, elapsed)}
      </div>
    `, "transition-context");
  }

  function transitionTimelineText(ctx, doc) {
    const timeline = asObject(asObject(ctx.core_skeleton).timeline);
    const previousTime = timeOnly(firstPresent(
      timeline.previous_ts_ms,
      ctx.previous_ts_ms
    ));
    const currentTime = timeOnly(firstPresent(
      timeline.current_ts_ms,
      ctx.current_ts_ms,
      get(doc, "identity.confirmed_time_ms")
    ));
    const previousShort = shortCardRef(firstPresent(
      timeline.previous_short_id,
      ctx.previous_card_id
    ));
    const currentShort = shortCardRef(firstPresent(
      timeline.current_short_id,
      ctx.current_card_id
    ));
    return [
      [previousTime, currentTime].filter(Boolean).join(" -> "),
      ctx.elapsed_ms === null || ctx.elapsed_ms === undefined ? "" : ageText(ctx.elapsed_ms),
      [previousShort, currentShort].filter(Boolean).join(" -> ")
    ].filter(Boolean).join(" / ") || "暂缺 (null)";
  }

  function timeOnly(value) {
    if (isNullish(value) || value === "") return "";
    const numeric = Number(value);
    const date = Number.isFinite(numeric) ? new Date(numeric) : new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: "Asia/Shanghai"
    }).format(date);
  }

  function shortCardRef(value) {
    if (isNullish(value) || value === "") return "";
    const raw = String(value);
    const match = raw.match(/(?:FULLSIM-|LOCAL-|CARD-)?0*([0-9]{1,4})$/);
    if (match) return `#${match[1].padStart(Math.min(2, match[1].length), "0")}`;
    if (raw.length <= 8) return `#${raw}`;
    return `#${raw.slice(-4)}`;
  }

  function renderTransitionCoreSummary(ctx) {
    const rows = transitionCoreRows(ctx);
    if (!rows.length) return "";
    return `
      <div class="transition-core-summary">
        <h3 class="subsection-title">关键变化骨架 / Core transition</h3>
        <div class="transition-core-list">
          ${rows.map((row) => `
            <article class="transition-core-row">
              <div class="transition-core-main">
                <strong>${escapeHtml(row.title || transitionDomainSemanticTitle(row.domain))}</strong>
              </div>
              <div class="transition-core-values">${escapeHtml(row.valueText)}</div>
              <p>${escapeHtml(row.meaning)}</p>
            </article>
          `).join("")}
        </div>
      </div>
    `;
  }

  function transitionCoreRows(ctx) {
    const displayRows = transitionDisplayCoreRows(ctx);
    const fallbackRows = transitionFallbackCoreRows(ctx);
    if (!displayRows.length) return fallbackRows;
    const byDisplay = new Map(displayRows.map((row) => [rawEnum(row.domain).toUpperCase(), row]));
    const byFallback = new Map(fallbackRows.map((row) => [rawEnum(row.domain).toUpperCase(), row]));
    const domains = [];
    [...fallbackRows, ...displayRows].forEach((row) => {
      const domain = rawEnum(row.domain || "OTHER").toUpperCase();
      if (!domains.includes(domain)) domains.push(domain);
    });
    return domains.map((domain) => byDisplay.get(domain) || byFallback.get(domain)).filter(Boolean);
  }

  function transitionDisplayCoreRows(ctx) {
    return asArray(ctx.core_transition_display).map((item) => {
      const row = asObject(item);
      const domain = rawEnum(row.domain || "OTHER").toUpperCase();
      const previous = row.previous_display || "缺失";
      const current = row.current_display || "缺失";
      const materiality = transitionMaterialityFromGrade(row.grade_cn);
      return {
        domain,
        title: row.title_cn || transitionDomainSemanticTitle(domain),
        materiality,
        valueText: `${previous} → ${current}`,
        meaning: sanitizeTransitionNarrative(row.meaning_cn || row.source_note || "关键状态变化")
      };
    }).filter((row) => row.domain);
  }

  function transitionFallbackCoreRows(ctx) {
    const skeletonDomains = asArray(asObject(ctx.core_skeleton).domains);
    const summaries = transitionDomainSummaries(ctx);
    const topChanges = asArray(ctx.top_material_changes);
    const bySkeleton = new Map();
    const bySummary = new Map();
    const byChange = new Map();
    skeletonDomains.forEach((item) => {
      const domain = rawEnum(item.domain || "OTHER").toUpperCase();
      if (!bySkeleton.has(domain)) bySkeleton.set(domain, item);
    });
    summaries.forEach((item) => {
      const domain = rawEnum(item.domain || "OTHER").toUpperCase();
      if (!bySummary.has(domain)) bySummary.set(domain, item);
    });
    topChanges.forEach((item) => {
      const domain = rawEnum(item.domain || "OTHER").toUpperCase();
      if (!byChange.has(domain)) byChange.set(domain, item);
    });
    const domains = [];
    [...skeletonDomains, ...summaries, ...topChanges].forEach((item) => {
      const domain = rawEnum(item.domain || "OTHER").toUpperCase();
      if (!domains.includes(domain)) domains.push(domain);
    });
    return domains.map((domain) => transitionCoreRow(
      domain,
      asObject(bySkeleton.get(domain)),
      asObject(bySummary.get(domain)),
      asObject(byChange.get(domain))
    )).filter(Boolean);
  }

  function transitionMaterialityFromGrade(value) {
    const raw = rawEnum(value || "UNKNOWN").toUpperCase();
    const labels = {
      关键: "CRITICAL",
      高: "HIGH",
      中: "MEDIUM",
      低: "LOW",
      很低: "VERY_LOW",
      未定: "UNKNOWN",
      无: "NONE"
    };
    return labels[value] || raw;
  }

  function transitionCoreRow(domain, skeleton, summary, change) {
    const value = transitionCoreValue(domain, skeleton, summary, change);
    if (!value) return null;
    const materiality = firstPresent(summary.materiality, change.materiality, value.materiality, "UNKNOWN");
    return {
      domain,
      materiality,
      valueText: `${value.previous} → ${value.current}`,
      meaning: transitionCoreMeaning(domain, value)
    };
  }

  function transitionCoreValue(domain, skeleton, summary, change) {
    const previous = asObject(skeleton.previous);
    const current = asObject(skeleton.current);
    const preferred = transitionCorePreferredKeys(domain);
    for (const key of preferred) {
      if (!isNullish(previous[key]) || !isNullish(current[key])) {
        return {
          key,
          previous: transitionCoreValueText(domain, key, previous[key]),
          current: transitionCoreValueText(domain, key, current[key]),
          previousRaw: previous[key],
          currentRaw: current[key],
          materiality: summary.materiality
        };
      }
    }
    const children = asArray(summary.children);
    const child = children.find((item) => !isNullish(item.previous) || !isNullish(item.current));
    if (child) {
      const key = String(child.field || "").split(".").pop() || "value";
      return {
        key,
        previous: transitionCoreValueText(domain, key, child.previous),
        current: transitionCoreValueText(domain, key, child.current),
        previousRaw: child.previous,
        currentRaw: child.current,
        materiality: child.materiality || summary.materiality
      };
    }
    if (Object.keys(change).length && (!isNullish(change.previous) || !isNullish(change.current))) {
      const key = String(change.field || "").split(".").pop() || "value";
      return {
        key,
        previous: transitionCoreValueText(domain, key, change.previous),
        current: transitionCoreValueText(domain, key, change.current),
        previousRaw: change.previous,
        currentRaw: change.current,
        materiality: change.materiality
      };
    }
    return null;
  }

  function transitionCorePreferredKeys(domain) {
    const keys = {
      TMV: ["tmv_blend", "tmvf_24h_final", "tmvf_48h_final"],
      MACRO: ["macro_score", "us10y_scoring_bps", "dxy_scoring_bps", "volq_scoring_bps"],
      FUNDING: ["last_rate", "last_funding_rate", "funding_norm"],
      SKEW: ["rr_25d", "rr_blend", "skew_norm_blend"],
      GAMMA: ["net_gamma_notional_usd", "distance_to_flip_pct", "distance_to_pin_pct"],
      P_C_RATIO: ["put_call_ratio"],
      CONFLICT: ["ratio", "level"],
      DECISION: ["confidence", "support_label", "decision_state"],
      QUALITY: ["overall", "missing_field_count"]
    };
    return keys[rawEnum(domain).toUpperCase()] || [];
  }

  function transitionCoreValueText(domain, key, value) {
    if (isNullish(value) || value === "") return "缺失";
    if (typeof value === "number" && Number.isFinite(value)) {
      const lowered = String(key || "").toLowerCase();
      if (rawEnum(domain).toUpperCase() === "CONFLICT" && lowered === "ratio") {
        return `${Math.round(value * 100)}%`;
      }
      if (rawEnum(domain).toUpperCase() === "FUNDING" && ["last_rate", "last_funding_rate"].includes(lowered)) {
        return `${trimNumber(value * 100, 6)}%`;
      }
      if (lowered.includes("gamma_notional") || lowered.includes("notional_usd")) {
        if (Math.abs(value) < 1000) return trimNumber(value, 4);
        return compactUsdNotional(value);
      }
      if (Math.abs(value) > 0 && Math.abs(value) < 0.001) return trimNumber(value, 6);
      return trimNumber(value, 4);
    }
    if (isEnum(value)) return semanticCompact(value);
    return String(value);
  }

  function trimNumber(value, digits) {
    if (!Number.isFinite(value)) return String(value);
    const normalized = Math.abs(value) < 0.5 * Math.pow(10, -digits) ? 0 : value;
    return normalized.toFixed(digits).replace(/\.?0+$/, "");
  }

  function compactUsdNotional(value) {
    const sign = value < 0 ? "-" : "";
    const amount = Math.abs(value);
    if (amount >= 1_000_000_000) return `${sign}$${trimNumber(amount / 1_000_000_000, 2)}B`;
    if (amount >= 1_000_000) return `${sign}$${trimNumber(amount / 1_000_000, 2)}M`;
    if (amount >= 1000) return `${sign}$${trimNumber(amount / 1000, 2)}K`;
    return `${sign}$${trimNumber(amount, 2)}`;
  }

  function transitionCoreMeaning(domain, value) {
    const raw = rawEnum(domain).toUpperCase();
    const before = typeof value.previousRaw === "number" ? value.previousRaw : null;
    const after = typeof value.currentRaw === "number" ? value.currentRaw : null;
    const rising = before !== null && after !== null ? after > before : false;
    const falling = before !== null && after !== null ? after < before : false;
    if (raw === "TMV") {
      if (before !== null && after !== null && before >= 0 && after < 0) return "量价路径由正转负";
      return falling ? "量价路径转弱" : "量价路径改善";
    }
    if (raw === "MACRO") return rising ? "宏观压力上升" : "宏观压力回落";
    if (raw === "FUNDING") {
      if (after !== null && Math.abs(after) <= 0.0001) {
        if (after > 0) return "资金费率温和多头倾向";
        if (after < 0) return "资金费率温和空头倾向";
        return "资金费率基准/中性";
      }
      return rising ? "资金费率拥挤上升" : "资金费率拥挤缓和";
    }
    if (raw === "SKEW") return falling ? "期权偏斜压力加深" : "期权偏斜压力缓和";
    if (raw === "GAMMA") return after !== null && before !== null && after < before ? "净 Gamma 敞口加深" : "净 Gamma 敞口缓和";
    if (raw === "P_C_RATIO") return rising ? "期权保护需求上升" : "期权保护需求回落";
    if (raw === "CONFLICT") return rising ? "信号分歧升高" : "信号分歧缓和";
    if (raw === "DECISION") return falling ? "决策置信下降" : "决策状态变化";
    if (raw === "QUALITY") return "数据质量状态";
    return "关键状态变化";
  }

  function transitionDomainSummaries(ctx) {
    const summaries = asArray(ctx.domain_change_summaries);
    if (summaries.length) return summaries;
    return fallbackDomainSummaries(asArray(ctx.top_material_changes));
  }

  function fallbackDomainSummaries(changes) {
    const grouped = new Map();
    changes.forEach((change) => {
      const domain = rawEnum(change.domain || "OTHER").toUpperCase();
      if (!grouped.has(domain)) grouped.set(domain, []);
      grouped.get(domain).push(change);
    });
    return [...grouped.entries()].map(([domain, children]) => ({
      domain,
      materiality: children[0] && children[0].materiality,
      raw_change_count: children.length,
      primary_fields: children.map((item) => item.field).filter(Boolean).slice(0, 4),
      source_refs: children.map((item) => item.source_ref).filter(Boolean),
      children
    }));
  }

  function renderTransitionAuditMetadata(ctx, elapsed) {
    return `
      <details class="transition-metadata">
        <summary>审计元数据 / Audit metadata</summary>
        <dl class="kv-grid transition-grid">
          ${kv("transition_id", ctx.transition_id, { translate: false })}
          ${kv("previous_card_id", ctx.previous_card_id, { translate: false })}
          ${kv("current_card_id", ctx.current_card_id, { translate: false })}
          ${kv("elapsed_ms", elapsed, { translate: false })}
          ${kv("materiality_score", ctx.materiality_score, { translate: false })}
          ${kv("llm_review_required", ctx.llm_review_required)}
          ${kv("record_hash", ctx.record_hash, { translate: false })}
          ${kv("compat_backfill_applied", ctx.compat_backfill_applied)}
        </dl>
      </details>
    `;
  }

  function renderTransitionChanges(doc, changes) {
    if (!changes.length) return `<div class="empty-inline">暂无材料变化</div>`;
    return `<div class="transition-change-list">${changes.map((change) => {
      const source = change.source_ref ? sourceRefLink(change.source_ref, doc, change.source_ref) : "";
      return `
        <div class="transition-change">
          <div class="transition-change-topline">
            <strong>${escapeHtml(transitionDomainLabel(change.domain))}</strong>
            <span>${escapeHtml(semanticCompact(change.materiality || "UNKNOWN"))}</span>
          </div>
          <p class="transition-field">${escapeHtml(fieldLabel(change.field || "field"))}</p>
          <dl class="transition-mini-grid">
            ${kv("previous", transitionValueText(change.previous), { translate: false })}
            ${kv("current", transitionValueText(change.current), { translate: false })}
            ${kv("delta_abs", transitionValueText(change.delta_abs), { translate: false })}
            ${kv("role", `${semanticCompact(change.role_before || "UNKNOWN")} → ${semanticCompact(change.role_after || "UNKNOWN")}`, { translate: false })}
          </dl>
          <div class="source-ref-row">
            ${change.meaning ? `<span class="chip">${escapeHtml(semanticCompact(change.meaning))}</span>` : ""}
            ${source}
          </div>
        </div>
      `;
    }).join("")}</div>`;
  }

  function renderTransitionRawChanges(doc) {
    const ctx = transitionContext(doc);
    if (!Object.keys(ctx).length) return "";
    const groups = transitionRawGroups(ctx);
    if (!groups.length && !asArray(ctx.top_material_changes).length) return "";
    return section("状态转移原始字段变化", "按 domain 分组；宏观合并为一条，展开查看子字段。", `
      <div class="transition-raw-block">
        ${groups.map((group) => renderTransitionRawGroup(doc, group)).join("")}
        ${renderTransitionComparisonTabs(ctx)}
      </div>
    `, "transition-raw-changes");
  }

  function transitionRawGroups(ctx) {
    const groups = asArray(ctx.raw_change_groups);
    if (groups.length) return groups;
    return fallbackDomainSummaries(asArray(ctx.top_material_changes));
  }

  function renderTransitionRawGroup(doc, group) {
    const children = asArray(group.children);
    const label = transitionDomainLabel(group.domain);
    return `
      <details class="transition-raw-group">
        <summary>
          <span>${escapeHtml(label)}</span>
          <span>${escapeHtml(scalarText(group.raw_change_count ?? children.length, { translate: false, digits: 0 }))} 项原始变化</span>
        </summary>
        ${renderTransitionChanges(doc, children)}
      </details>
    `;
  }

  function transitionDomainLabel(domain) {
    const labels = {
      DECISION: "决策",
      FUNDING: "资金费率",
      GAMMA: "Gamma",
      MACRO: "宏观",
      P_C_RATIO: "P/C 比例",
      CONFLICT: "冲突",
      TMV: "TMV",
      QUALITY: "数据质量",
      SKEW: "偏斜"
    };
    const raw = rawEnum(domain || "UNKNOWN");
    return labels[raw] || semanticCompact(raw);
  }

  function transitionDomainSemanticTitle(domain) {
    const labels = {
      TMV: "TMV（量价路径）",
      MACRO: "宏观（利率/美元/波动率）",
      SKEW: "期权偏斜（Skew）",
      P_C_RATIO: "P/C（期权需求）",
      GAMMA: "Gamma（净 Gamma）",
      FUNDING: "Funding（期货资金费率）",
      CONFLICT: "冲突（信号分歧）",
      DECISION: "决策（状态/置信）",
      QUALITY: "数据质量（完整性）"
    };
    const raw = rawEnum(domain || "UNKNOWN");
    return labels[raw] || transitionDomainLabel(raw);
  }

  function transitionGradeLabel(value) {
    const labels = {
      CRITICAL: "关键",
      HIGH: "高",
      MEDIUM: "中",
      LOW: "低",
      VERY_LOW: "很低",
      UNKNOWN: "未定"
    };
    const raw = rawEnum(value || "UNKNOWN");
    return labels[raw] || semanticCompact(raw);
  }

  function transitionValueText(value) {
    if (Array.isArray(value) || (value && typeof value === "object")) return rawValueTextLabeled(value);
    if (isEnum(value)) return semanticLabel(value);
    return scalarText(value, { translate: false, digits: 4 });
  }

  function renderTransitionObservedChanges(changes, ctx = {}) {
    const items = asArray(changes);
    if (!items.length) return `<div class="empty-inline">无</div>`;
    const displayByDomain = new Map(transitionCoreRows(ctx).map((row) => [
      rawEnum(row.domain || "OTHER").toUpperCase(),
      row
    ]));
    return `<ul class="transition-observed-list">${items.map((item) => renderTransitionObservedChange(item, displayByDomain)).join("")}</ul>`;
  }

  function renderTransitionObservedChange(item, displayByDomain = new Map()) {
    const change = asObject(item);
    const domain = rawEnum(change.domain || "OTHER").toUpperCase();
    const title = transitionDomainSemanticTitle(domain);
    const displayRow = displayByDomain.get(domain);
    const deterministicFunding = domain === "FUNDING" && displayRow;
    const fundingFact = deterministicFunding
      ? [displayRow.valueText, displayRow.meaning].filter(Boolean).join("；")
      : "";
    const fact = stripTransitionMaterialityBoilerplate(
      sanitizeTransitionReadable(
        fundingFact || change.fact_cn || change.fact || change.summary_cn,
        displayRow && displayRow.meaning
      ));
    const impact = deterministicFunding ? "" : transitionObservedImpactText(change, displayRow, fact);
    const tendency = deterministicFunding
      ? ""
      : transitionObservedTendencyText(change, displayRow, `${fact} ${impact}`);
    const segments = [
      fact ? `<span class="transition-observed-segment transition-observed-fact">${escapeHtml(fact)}</span>` : "",
      impact ? `<span class="transition-observed-segment transition-observed-impact"><strong class="transition-observed-label">影响</strong>${escapeHtml(impact)}</span>` : "",
      tendency ? `<span class="transition-observed-segment transition-observed-tendency"><strong class="transition-observed-label">倾向</strong>${escapeHtml(tendency)}</span>` : ""
    ].filter(Boolean).join("");
    return `
      <li>
        <strong>${escapeHtml(title)}</strong>
        <span class="transition-observed-copy">${segments}</span>
        ${renderTransitionObservedMeta(change)}
      </li>
    `;
  }

  function renderTransitionObservedMeta(change) {
    const chips = [
      transitionMetaChip("证据状态", change.evidence_status, transitionEvidenceStatusLabel),
      transitionMetaChip("方向作用", change.directional_role, transitionDirectionalRoleLabel),
      transitionMetaChip("幅度判断", change.magnitude_verdict, transitionMagnitudeVerdictLabel),
      transitionMetaChip("关注影响", change.audit_attention_effect, transitionAuditAttentionLabel),
      transitionMetaChip("认知性质", change.epistemic_status, transitionEpistemicStatusLabel),
    ].filter(Boolean);
    if (!chips.length) return "";
    return `<div class="transition-observed-meta">${chips.join("")}</div>`;
  }

  function transitionMetaChip(label, value, formatter) {
    if (isNullish(value) || value === "") return "";
    if (isUninformativeTransitionMetaValue(value)) return "";
    return `<span class="chip">${escapeHtml(label)}: ${escapeHtml(formatter(value))}</span>`;
  }

  function isUninformativeTransitionMetaValue(value) {
    const raw = rawEnum(value).toUpperCase();
    return raw === "UNKNOWN" || raw === "UNDETERMINED" || raw === "INDETERMINATE";
  }

  function transitionEvidenceStatusLabel(value) {
    const labels = {
      SUFFICIENT: "充分",
      PARTIAL: "部分",
      NOT_COMPARABLE: "不可比",
      MISSING: "缺失"
    };
    const raw = rawEnum(value || "UNKNOWN").toUpperCase();
    return labels[raw] || semanticCompact(raw);
  }

  function transitionDirectionalRoleLabel(value) {
    const labels = {
      RISK_CONSTRAINT: "风险约束",
      SUPPORT: "支撑",
      NEUTRAL_OR_EASING: "中性/缓和",
      MIXED: "混合",
      UNDETERMINED: "未定"
    };
    const raw = rawEnum(value || "UNKNOWN").toUpperCase();
    return labels[raw] || semanticCompact(raw);
  }

  function transitionMagnitudeVerdictLabel(value) {
    const labels = {
      changes_judgment: "改变判断重点",
      background_only: "仅作背景",
      indeterminate: "不足判断"
    };
    const raw = rawEnum(value || "indeterminate");
    return labels[raw] || semanticCompact(raw);
  }

  function transitionAuditAttentionLabel(value) {
    const labels = {
      SHIFT_FOCUS: "转移关注",
      REINFORCE_VIEW: "强化原判断",
      WEAKEN_VIEW: "削弱原判断",
      BACKGROUND_ONLY: "背景信息",
      UNDETERMINED: "未定"
    };
    const raw = rawEnum(value || "UNKNOWN").toUpperCase();
    return labels[raw] || semanticCompact(raw);
  }

  function transitionEpistemicStatusLabel(value) {
    const labels = {
      OBSERVED: "直接观察",
      SUPPORTED_INFERENCE: "有证据推断",
      HYPOTHESIS: "假设",
      NOT_ASSESSABLE: "不可评估"
    };
    const raw = rawEnum(value || "UNKNOWN").toUpperCase();
    return labels[raw] || semanticCompact(raw);
  }

  function transitionCrossRelationLabel(value) {
    const labels = {
      REINFORCING: "共振",
      OFFSETTING: "抵消",
      CO_MOVEMENT: "同步",
      CONSTRAINT_INTERACTION: "约束互动"
    };
    const raw = rawEnum(value || "CO_MOVEMENT").toUpperCase();
    return labels[raw] || semanticCompact(raw);
  }

  function transitionObservedImpactText(change, displayRow, fact) {
    const explicit = stripTransitionMaterialityBoilerplate(
      sanitizeTransitionReadable(change.impact_cn || change.actual_impact_cn || change.meaning_cn, ""));
    if (explicit) return ensureSentenceEnd(explicit);
    const displayMeaning = stripTransitionMaterialityBoilerplate(
      sanitizeTransitionNarrative(displayRow && displayRow.meaning));
    if (displayMeaning) return ensureSentenceEnd(displayMeaning);
    return ensureSentenceEnd(transitionInferredImpact(change.domain, fact));
  }

  function transitionObservedTendencyText(change, displayRow, text) {
    const explicit = stripTransitionMaterialityBoilerplate(
      sanitizeTransitionReadable(change.tendency_cn, ""));
    if (explicit) return explicit;
    const domain = rawEnum(change.domain || (displayRow && displayRow.domain) || "OTHER").toUpperCase();
    return transitionInferredTendency(domain, text || "");
  }

  function transitionListHtml(items, emptyText = "无") {
    const values = asArray(items)
      .map((item) => sanitizeTransitionReadable(item, ""))
      .filter(Boolean);
    if (!values.length) return `<div class="empty-inline">${escapeHtml(emptyText)}</div>`;
    return `<ul class="plain-list">${values.map((item) => `<li>${valueHtml(item, { translate: false })}</li>`).join("")}</ul>`;
  }

  function renderTransitionCrossFactor(review) {
    const structured = asArray(asObject(review).cross_factor_assessments);
    if (!structured.length) {
      return transitionListHtml(asObject(review).cross_factor_interactions, "无");
    }
    return `<ul class="plain-list transition-assessment-list">${structured.map((item) => {
      const row = asObject(item);
      const domains = asArray(row.domains).map((domain) => transitionDomainLabel(domain)).join(" / ");
      return `
        <li>
          <strong>${escapeHtml([domains, transitionCrossRelationLabel(row.relation)].filter(Boolean).join(" · "))}</strong>
          <p>${escapeHtml(sanitizeTransitionReadable(row.assessment_cn, "暂无说明"))}</p>
        </li>
      `;
    }).join("")}</ul>`;
  }

  function renderTransitionOperatorChecks(review) {
    const checks = asArray(asObject(review).operator_checks);
    if (!checks.length) return transitionListHtml(asObject(review).operator_focus, "无");
    return `<ul class="plain-list transition-check-list">${checks.map((item) => {
      const row = asObject(item);
      return `
        <li>
          <strong>${escapeHtml(sanitizeTransitionReadable(row.focus_cn, "核验项"))}</strong>
          ${row.why_cn ? `<p>${escapeHtml(sanitizeTransitionReadable(row.why_cn, ""))}</p>` : ""}
          ${row.strengthens_if_cn ? `<p><span>增强条件：</span>${escapeHtml(sanitizeTransitionReadable(row.strengthens_if_cn, ""))}</p>` : ""}
          ${row.weakens_if_cn ? `<p><span>削弱条件：</span>${escapeHtml(sanitizeTransitionReadable(row.weakens_if_cn, ""))}</p>` : ""}
        </li>
      `;
    }).join("")}</ul>`;
  }

  function renderTransitionPolicyValidation(review) {
    const policy = asObject(asObject(review).policy_validation);
    if (!Object.keys(policy).length) {
      return `<div class="transition-policy-note"><strong>策略校验</strong><span>未按当前策略验证</span></div>`;
    }
    const issueCount = [
      policy.raw_enum_leaks,
      policy.trading_instruction_terms,
      policy.unit_mislabel_terms,
      policy.materiality_boilerplate_terms,
      policy.invalid_evidence_refs,
      policy.system_assertion_evidence_refs,
      policy.missing_evidence_refs,
      policy.causal_overclaim_terms
    ].reduce((sum, value) => sum + asArray(value).length, 0);
    const label = policy.passed ? "通过" : `需复核${issueCount ? ` · ${issueCount} 项` : ""}`;
    return `<div class="transition-policy-note"><strong>策略校验</strong><span>${escapeHtml(label)}</span></div>`;
  }

  function stripTransitionMaterialityBoilerplate(value) {
    let text = String(value ?? "");
    [
      "被评估为关键变化",
      "被评估为高材料性变化",
      "被评估为中材料性变化",
      "被评估为低材料性变化",
      "评估为关键变化",
      "评估为高材料性变化",
      "高材料性变化",
      "关键变化",
      "材料性变化",
      "材料性"
    ].forEach((phrase) => {
      text = text.split(phrase).join("");
    });
    return text
      .replace(/，{2,}/g, "，")
      .replace(/。{2,}/g, "。")
      .replace(/，。/g, "。")
      .replace(/；。/g, "。")
      .replace(/\s+/g, " ")
      .replace(/[；，。\s]+$/g, "")
      .trim();
  }

  function ensureSentenceEnd(value) {
    const text = String(value ?? "").trim();
    if (!text) return "";
    return /[。！？]$/.test(text) ? text : `${text}。`;
  }

  function transitionInferredImpact(domain, text) {
    const upper = rawEnum(domain || "OTHER").toUpperCase();
    const value = String(text || "");
    if (upper === "MACRO") return "宏观压力变化会影响风险资产估值背景和信号释放约束。";
    if (upper === "TMV" || upper === "TMVF") return "量价路径变化直接影响方向骨架的强弱。";
    if (upper === "FUNDING") return "资金费率变化反映永续端拥挤和杠杆付费压力。";
    if (upper === "SKEW") return "期权偏斜变化反映保护需求和尾部风险定价。";
    if (upper === "GAMMA") return "Gamma 变化影响价格波动放大或钉住约束。";
    if (upper === "P_C_RATIO") return "P/C 变化反映期权保护需求的相对强弱。";
    if (upper === "CONFLICT") return "分歧变化影响信号是否收敛。";
    if (upper === "DECISION") return "决策状态变化影响审计结论的释放条件。";
    return value || "该变化需要结合其他域共同判断。";
  }

  function transitionInferredTendency(domain, text) {
    const upper = rawEnum(domain || "OTHER").toUpperCase();
    const value = String(text || "");
    if (upper === "MACRO" && /上升|加深|恶化|转弱|压力|逆风/.test(value)) return "利空/风险约束";
    if ((upper === "TMV" || upper === "TMVF") && /下降|降至|转弱|走弱/.test(value)) return "偏空";
    if ((upper === "TMV" || upper === "TMVF") && /上升|回升|改善|支撑/.test(value)) return "偏多/支撑";
    if (upper === "FUNDING" && /转负|回落|消失|缓和/.test(value)) return "中性/拥挤缓和";
    if (upper === "FUNDING" && /上升|拥挤|升温/.test(value)) return "利空/拥挤升温";
    if (upper === "SKEW" && /转弱|下降|保护|尾部/.test(value)) return "利空/尾部保护升温";
    if (upper === "GAMMA" && /下降|走弱|负|放大|加深/.test(value)) return "利空/空间约束加深";
    if (upper === "GAMMA" && /回升|支撑|钉住|缓和/.test(value)) return "中性/波动约束缓和";
    if (upper === "P_C_RATIO" && /下降|降低|回落|缓和/.test(value)) return "中性/保护需求缓和";
    if (upper === "P_C_RATIO" && /上升|升高/.test(value)) return "利空/保护需求升温";
    if (upper === "CONFLICT" && /上升|升高|扩大/.test(value)) return "利空/分歧升高";
    if (upper === "CONFLICT" && /下降|回落|缓和/.test(value)) return "中性/分歧缓和";
    if (/利空|风险|压制|阻断|逆风/.test(value)) return "利空/风险约束";
    if (/利多|支撑|改善/.test(value)) return "利多/支撑";
    if (/缓和|回落/.test(value)) return "中性/缓和";
    return "中性/需结合其他维度";
  }

  function sanitizeTransitionNarrative(value) {
    let text = String(value ?? "");
    [
      ["Mild Headwind", "轻度逆风"],
      ["Strong Headwind", "强逆风"],
      ["MACRO_SHOCK_GATE_BLOCK", "宏观冲击限制状态"],
      ["MACRO_SHOCK_GATE_STATE", "宏观冲击限制状态"],
      ["MACRO_SHOCK_BLOCKING", "宏观冲击阻断"],
      ["MACRO_SHOCK", "宏观冲击"],
      ["MACRO_BLOCKING", "宏观硬阻断"],
      ["MACRO Headwind", "宏观逆风"],
      ["MACRO", "宏观"],
      ["Neutral", "中性"],
      ["Mild", "轻度"],
      ["Strong", "强"],
      ["Headwind", "逆风"],
      ["WAIT_CONFIRMATION", "等待确认"],
      ["POSITIVE_GAMMA_PINNING", "正 Gamma 钉住"],
      ["NEGATIVE_GAMMA", "负 Gamma"],
      ["BULLISH", "偏多"],
      ["BEARISH", "偏空"],
      ["Bullish", "偏多"],
      ["Bearish", "偏空"],
      ["CRITICAL", "关键"],
      ["HIGH", "高"],
      ["BLOCKED", "已被阻断"],
      ["WATCH", "观察"],
      ["BLOCK", "阻断"],
      ["CLEAR", "清除"],
      ["NONE", "无"],
      ["NEUTRAL", "中性"],
      ["FUNDING", "资金费率"],
      ["SKEW_REVERSAL", "偏斜反转"],
      ["TRANSITION", "过渡区"],
      ["P_C_RATIO", "P/C 比例"],
      ["macro_shock.state", "宏观冲击限制状态"],
      ["发生正负符号翻转", "出现结构变化"],
      ["正负符号翻转", "结构变化"],
    ].forEach(([from, to]) => {
      text = text.split(from).join(to);
    });
    return text
      .replace(/维持中性偏好，持续受宏观硬阻断阻碍/g, "维持中性偏好，持续受宏观硬阻断")
      .replace(/受宏观硬阻断阻碍/g, "受宏观硬阻断")
      .replace(/关键（关键）/g, "关键")
      .replace(/高（高）/g, "高")
      .replace(/观察（观察）/g, "观察")
      .replace(/阻断（阻断）/g, "阻断")
      .replace(/等待确认（等待确认）/g, "等待确认");
  }

  function sanitizeTransitionReadable(value, fallback = "") {
    if (value && typeof value === "object") {
      return sanitizeTransitionNarrative(fallback);
    }
    const text = sanitizeTransitionNarrative(value);
    if (!text || hasTransitionRawFieldLeak(text)) {
      return sanitizeTransitionNarrative(fallback);
    }
    return text;
  }

  function hasTransitionRawFieldLeak(value) {
    const text = String(value ?? "");
    return /factor_cross_section|macro_pressure\.components|source_ref|primary_fields|主要字段|核心前后值已入包|来源[:：]|原始变化\s*\d*\s*项/i.test(text)
      || /\b[a-z][a-z0-9_]*(?:\.[A-Za-z0-9_]+){2,}\b/.test(text)
      || /(?:^|[{,'"\s])(?:funding_state|last_rate|last_funding_rate|funding_norm|tmvf_funding_effect|effect)\s*['"]?\s*[:=]/i.test(text)
      || /[-+]?\d+(?:\.\d+)?e[-+]?\d+/i.test(text);
  }

  function rawValueTextLabeled(value, depth = 0) {
    if (isNullish(value) || value === "") return "暂缺";
    if (Array.isArray(value)) {
      if (!value.length) return "无";
      return value.map((item) => rawValueTextLabeled(item, depth + 1)).join(" ; ");
    }
    if (typeof value === "object") {
      const entries = Object.entries(value)
        .filter(([, child]) => child !== undefined && child !== null && child !== "");
      if (!entries.length) return "无";
      return entries
        .map(([name, child]) => `${fieldLabel(name)}: ${rawValueTextLabeled(child, depth + 1)}`)
        .join(" / ");
    }
    return transitionValueText(value);
  }

  function renderTransitionComparisonTabs(ctx) {
    const recent = asArray(ctx.recent_5_trajectory);
    const baseline = asObject(ctx.baseline_24h);
    const episode = asObject(ctx.episode_anchor);
    return `
      <div class="transition-comparison-tabs">
        <div>
          <h3 class="subsection-title">最近 5 次轨迹</h3>
          ${recent.length ? `<ol class="transition-trajectory">${recent.map((item) => `
            <li>
              <span>${escapeHtml(item.card_id || "UNKNOWN")}</span>
              <span>${escapeHtml([semanticCompact(item.lean), semanticCompact(item.support_label)].filter(Boolean).join(" / "))}</span>
              <span>${escapeHtml(rawValueText({
                macro_score: item.macro_score,
                funding_last_rate: item.funding_last_rate,
                gamma_regime: semanticCompact(item.gamma_regime)
              }))}</span>
            </li>
          `).join("")}</ol>` : `<div class="empty-inline">暂无最近轨迹</div>`}
        </div>
        <div>
          <h3 class="subsection-title">24 小时基线</h3>
          <p>${escapeHtml(rawValueTextLabeled(baseline))}</p>
        </div>
        <div>
          <h3 class="subsection-title">同片段锚点</h3>
          <p>${escapeHtml(rawValueTextLabeled(episode))}</p>
        </div>
      </div>
    `;
  }

  function renderTransitionLlmReview(doc) {
    const ctx = transitionContext(doc);
    const review = asObject(get(doc, "transition_llm_review", {}));
    if (!Object.keys(review).length) {
      if (!ctx.llm_review_required) return "";
      return `
        <div class="transition-llm is-pending">
          <h3 class="subsection-title">LLM 变化链解释</h3>
          <p>等待 transition sidecar 合并；程序化 ledger 已可用于审计。</p>
        </div>
      `;
    }
    const guard = asObject(review.language_guard);
    const policy = asObject(review.policy_validation);
    const renderState = String(policy.render_state || "").toUpperCase();
    const suppressLlmText = renderState === "SUPPRESS_LLM_TEXT";
    // Content-expression issues no longer gate display; the transition LLM area is a
    // neutral audit-bypass reference. Only SUPPRESS_LLM_TEXT (defensive) and unknown
    // render states still fail closed and hide the model text.
    const knownRenderStates = new Set([
      "",
      "DISPLAY_LLM_TEXT",
      "DEGRADED_LLM_TEXT",
      "SUPPRESS_LLM_TEXT"
    ]);
    const topline = `
      <div class="llm-review-topline">
        ${statusBadgeCn("状态", review.status || "UNKNOWN")}
        ${Object.keys(policy).length ? statusBadgeCn("策略校验", policy.passed ? "VALID" : "DEGRADED") : ""}
        ${statusBadgeCn("禁止交易指令", guard.no_trading_instruction ? "VALID" : "UNKNOWN")}
        ${statusBadgeCn("不使用外部数据", guard.no_external_data ? "VALID" : "UNKNOWN")}
        ${statusBadgeCn("区分观察与因果", guard.distinguishes_observation_from_causality ? "VALID" : "UNKNOWN")}
        ${statusBadgeCn("不含交易建议", review.not_trading_advice ? "VALID" : "UNKNOWN")}
      </div>
    `;
    if (!knownRenderStates.has(renderState)) {
      return `
        <div class="transition-llm is-degraded is-hard-degraded is-suppressed">
          <h3 class="subsection-title">LLM 变化链解释</h3>
          ${topline}
          ${renderTransitionPolicyValidation(review)}
          <p class="llm-review-summary">复核结果未通过当前客户端校验；请以程序化 transition ledger、证据目录和原始审计卡继续复核。</p>
          <dl class="kv-grid llm-review-grid" style="margin-top: 12px;">
            ${kv("model", review.model, { translate: false })}
            ${kv("input_packet_hash", review.input_packet_hash, { translate: false })}
          </dl>
        </div>
      `;
    }
    if (suppressLlmText) {
      return `
        <div class="transition-llm is-degraded is-hard-degraded is-suppressed">
          <h3 class="subsection-title">LLM 变化链解释</h3>
          ${topline}
          ${renderTransitionPolicyValidation(review)}
          <p class="llm-review-summary">LLM 正文因策略校验未通过已降级隐藏；请以程序化 transition ledger、证据目录和原始审计卡继续复核。</p>
          <dl class="kv-grid llm-review-grid" style="margin-top: 12px;">
            ${kv("model", review.model, { translate: false })}
            ${kv("input_packet_hash", review.input_packet_hash, { translate: false })}
          </dl>
        </div>
      `;
    }
    return `
      <div class="transition-llm">
        <h3 class="subsection-title">LLM 变化链解释</h3>
        ${topline}
        <div class="transition-llm-reference-note">LLM 变化链解释为审计旁路参考，仅用于信息整理与人工复核，不影响置信度、因子、放行或执行；如有内容表述问题仅作 metadata 记录。</div>
        <p class="llm-review-summary">${valueHtml(sanitizeTransitionReadable(
          review.transition_summary_cn,
          "LLM 解释含原始字段路径，已在主阅读区隐藏；请以核心骨架和策略校验继续复核。"
        ), { translate: false })}</p>
        ${renderTransitionPolicyValidation(review)}
        <dl class="kv-grid llm-review-grid" style="margin-top: 12px;">
          ${kvCn("trajectory_state", review.trajectory_state)}
          ${kvCn("signal_continuity", review.signal_continuity)}
          ${kv("model", review.model, { translate: false })}
          ${kv("input_packet_hash", review.input_packet_hash, { translate: false })}
        </dl>
        <div class="two-column-notes llm-review-lists">
          <div><h3 class="subsection-title">观察到的变化</h3>${renderTransitionObservedChanges(review.observed_changes, ctx)}</div>
          <div><h3 class="subsection-title">跨因子相互作用</h3>${renderTransitionCrossFactor(review)}</div>
          <div><h3 class="subsection-title">人工核验方案</h3>${renderTransitionOperatorChecks(review)}</div>
          <div><h3 class="subsection-title">人工观察重点</h3>${transitionListHtml(review.operator_focus, "无")}</div>
          <div><h3 class="subsection-title">失效条件</h3>${transitionListHtml(review.invalid_if, "无")}</div>
        </div>
      </div>
    `;
  }

  function renderTheoreticalActiveView(view) {
    const active = asObject(view);
    if (!Object.keys(active).length) return "";
    const basis = normalizeComfortText(active.basis_cn, "暂无");
    const boundary = normalizeComfortText(active.boundary_cn, "");
    return `
      <div class="llm-active-view">
        <div class="llm-review-topline">
          ${statusBadge("理论主动倾向", active.bias || "UNABLE_TO_JUDGE")}
          ${statusBadge("定性把握度", active.conviction || "LOW")}
        </div>
        <p class="llm-active-basis"><strong>理论依据</strong> ${escapeHtml(basis)}</p>
        <div class="two-column-notes llm-active-lists">
          <div><h3 class="subsection-title">关键驱动</h3>${listHtml(active.key_drivers, "无")}</div>
          <div><h3 class="subsection-title">反向证据</h3>${listHtml(active.counter_evidence, "无")}</div>
        </div>
        ${boundary ? `<p class="llm-active-boundary">${escapeHtml(boundary)}</p>` : ""}
      </div>
    `;
  }

  function renderGammaRegimeLens(lens) {
    const gamma = asObject(lens);
    if (!Object.keys(gamma).length) return "";
    const levels = asObject(gamma.key_levels);
    return `
      <div class="llm-gamma-lens">
        <div class="llm-review-topline">
          ${statusBadge("全局 Gamma 体制分析", gamma.regime || "UNKNOWN")}
          ${statusBadge("体制极端度", gamma.regime_extremity || "UNKNOWN")}
          ${statusBadge("对方向把握的影响", gamma.conviction_effect_on_directional_view || "UNKNOWN")}
          ${statusBadge("风险叠加，不是方向", gamma.lens_is_risk_overlay_not_direction ? "VALID" : "UNKNOWN")}
        </div>
        <div class="llm-gamma-copy">
          <p><strong>体制动力学</strong> ${escapeHtml(normalizeComfortText(gamma.dynamics_cn, "暂无"))}</p>
          <p><strong>主要尾部风险</strong> ${escapeHtml(normalizeComfortText(gamma.dominant_tail_risk_cn, "暂无"))}</p>
        </div>
        <dl class="kv-grid llm-gamma-levels">
          ${kv("flip", levels.flip, { translate: false })}
          ${kv("call_wall", levels.call_wall, { translate: false })}
          ${kv("put_wall", levels.put_wall, { translate: false })}
          ${kv("pin", levels.pin, { translate: false })}
        </dl>
        <div class="two-column-notes llm-gamma-notes">
          <div>
            <h3 class="subsection-title">持仓符号假设</h3>
            <p>${escapeHtml(normalizeComfortText(gamma.positioning_assumption_cn, "暂无"))}</p>
          </div>
          <div>
            <h3 class="subsection-title">数据质量说明</h3>
            <p>${escapeHtml(normalizeComfortText(gamma.data_quality_cn, "暂无"))}</p>
          </div>
        </div>
        <p class="llm-gamma-boundary">全局 Gamma 体制分析只解释分布、尾部与反身性风险；它是风险叠加，不是方向信号，当前限制与交易许可仍然有效。</p>
      </div>
    `;
  }

  function llmGammaKeyLevels(doc) {
    const review = asObject(get(doc, "llm_review", {}));
    const content = Object.assign({}, asObject(review.content), review);
    return asObject(get(content, "gamma_regime_lens.key_levels", {}));
  }

  function hasLlmGammaKeyLevel(doc, key) {
    return !isNullish(llmGammaKeyLevels(doc)[key]);
  }

  function hasLlmGammaKeyLevels(doc) {
    return ["flip", "call_wall", "put_wall", "pin"].some((key) => hasLlmGammaKeyLevel(doc, key));
  }

  function renderGammaOverview(doc) {
    const gex = asObject(get(doc, "factor_cross_section.gex_info", {}));
    const gamma = asObject(get(doc, "factor_cross_section.gamma_regime", {}));
    const pinDistance = pinDistanceText(doc, gamma, gex);
    const showFlipPoint = !hasLlmGammaKeyLevel(doc, "flip");
    const showCallWall = !hasLlmGammaKeyLevel(doc, "call_wall");
    const showPutWall = !hasLlmGammaKeyLevel(doc, "put_wall");
    const showPinStrike = !hasLlmGammaKeyLevel(doc, "pin");
    const showMagnetLevel = !hasLlmGammaKeyLevel(doc, "pin");
    const mergedKeyLevels = [
      showFlipPoint,
      showCallWall,
      showPutWall,
      showPinStrike,
      showMagnetLevel,
    ].some((show) => !show);
    const hasGex = Object.keys(gex).length > 0;
    const hasGamma = Object.keys(gamma).length > 0;
    if (!hasGex && !hasGamma) {
      return section("期权 Gamma / GEX 重点", "优先位保留给期权 Gamma 状态与关键点位。", `<div class="empty">暂无 GEX 或 Gamma 结构资料</div>`);
    }
    return section("期权 Gamma / GEX 重点", "优先展示当前 Gamma 状态、净 Gamma 名义额与关键点位，方便一眼判断空间约束。", `
      <dl class="kv-grid gamma-grid">
        ${gexKv("market_state", gex.market_state)}
        ${kv("regime", gamma.regime)}
        ${kv("regime_strength", gamma.regime_strength, { translate: false })}
        ${gexKv("net_gamma_notional_usd", gex.net_gamma_notional_usd ?? gamma.net_gamma_notional_usd)}
        ${gexKv("distance_to_pin_pct", pinDistance)}
        ${kv("confidence_multiplier", gamma.confidence_multiplier, { translate: false })}
        ${kv("veto", gamma.veto)}
        ${showFlipPoint ? gexKv("flip_point", gex.flip_point ?? gamma.flip_point) : ""}
        ${showPinStrike ? kv("pin_strike", gamma.pin_strike, { translate: false }) : ""}
        ${showCallWall ? gexKv("call_wall", gex.call_wall) : ""}
        ${showPutWall ? gexKv("put_wall", gex.put_wall) : ""}
        ${showMagnetLevel ? gexKv("magnet_level", gex.magnet_level) : ""}
      </dl>
      ${mergedKeyLevels ? `<p class="merge-note">关键点位已在 LLM Gamma 体制分析栏合并展示；此处保留原始体制、强度、净 Gamma 与质量状态，避免重复阅读。</p>` : ""}
      <div class="source-ref-row">
        ${!isNullish(gex.source_ref) ? sourceRefLink("factor_cross_section.gex_info", doc, `gex_info: ${gex.source_ref}`) : ""}
        ${!isNullish(gamma.source_ref) ? sourceRefLink("factor_cross_section.gamma_regime", doc, `gamma_regime: ${gamma.source_ref}`) : ""}
        ${!isNullish(gex.observed_at) ? `<span class="chip">GEX observed ${escapeHtml(dateText(gex.observed_at))}</span>` : ""}
        ${!isNullish(gamma.observed_at) ? `<span class="chip">Gamma observed ${escapeHtml(dateText(gamma.observed_at))}</span>` : ""}
      </div>
    `, "gamma-overview");
  }

  function renderGexRank(doc) {
    const gex = asObject(get(doc, "factor_cross_section.gex_info", {}));
    const rank = asObject(gex.rank);
    const metrics = asObject(rank.metrics);
    if (!Object.keys(rank).length || !Object.keys(metrics).length) {
      return section("GEX Rank 分位", "展示 GEX Monitor 最近 30 日或已有样本内的历史分位，仅作只读上下文。", `<div class="empty">暂无 GEX rank 分位；等待 gexmonitorapi 累计样本。</div>`, "gex-rank");
    }
    const window = asObject(rank.window);
    const netGex = rankMetric(rank, "gex_board.total_net_gex");
    const dvol = rankMetric(rank, "gex_board.dvol");
    const ivrv = rankMetric(rank, "volatility.iv_rv_ratio");
    const pcr = rankMetric(rank, "volatility.pcr");
    const callShare = rankMetric(rank, "flow.call_share_pct");
    const flowPc = rankMetric(rank, "flow.put_call_ratio");
    const noteParts = [
      window.mode ? `窗口 ${window.mode}` : "窗口 rolling_30d_or_available",
      !isNullish(window.sample_count) ? `样本 ${scalarText(window.sample_count, { translate: false, digits: 0 })}` : "",
      !isNullish(window.history_retained_count) ? `保留 ${scalarText(window.history_retained_count, { translate: false, digits: 0 })}` : "",
      !isNullish(window.window_days) ? `覆盖 ${scalarText(window.window_days, { translate: false, digits: 2 })} 天` : "",
      "15 日起质量健壮可用",
    ].filter(Boolean);
    return section("GEX Rank 分位", "把 netGEX、IV/RV、P/C 等裸数值转换为当前样本窗口里的相对位置；quality=ok 表示已达到 15 日稳健可用阈值，窗口仍按最近 30 日滚动维护。", `
      <dl class="kv-grid rank-grid">
        ${rankKv("netGEX", netGex, `<span class="rank-meta">绝对值 ${escapeHtml(rankPct(netGex, "abs_rank_pct"))}</span>`)}
        ${rankKv("DVOL", dvol)}
        ${rankKv("IV/RV", ivrv)}
        ${rankKv("PCR", pcr)}
        ${rankKv("Call share", callShare)}
        ${rankKv("Flow P/C", flowPc)}
      </dl>
      <div class="rank-note">${escapeHtml(noteParts.join(" / ") || "rank window 暂缺")}</div>
    `, "gex-rank");
  }

  function signalDurability(doc) {
    const nativeLayer = asObject(get(doc, "signal_durability", {}));
    if (Object.keys(nativeLayer).length) return nativeLayer;
    const comfort = asObject(get(doc, "comfort_window", {}));
    const anchor = asObject(get(doc, "price_anchor_durability", {}));
    const sessionContext = asObject(get(doc, "signal_window.session_context", {}));
    if (!Object.keys(comfort).length
        && !Object.keys(anchor).length
        && !Object.keys(sessionContext).length) return {};
    const score = firstPresent(anchor.durability_score, anchor.headline_score, anchor.score);
    const state = firstPresent(anchor.durability_state, anchor.headline_state, anchor.state);
    return {
      schema_name: "SignalDurabilityLayer",
      schema_version: "nrd.signal.durability_layer.v1",
      audit_scope: "AUDIT_ONLY",
      compat_backfill_applied: true,
      compat_backfill_source: "frontend_top_level_alias_and_session_context_v1",
      headline_score: score,
      headline_state: state,
      score: score,
      state,
      comfort_window: comfort,
      temporal_session: sessionContext,
      session_context: sessionContext,
      price_anchor_durability: anchor,
      layer_scores: asObject(anchor.layer_scores),
      confidence_policy: "DO_NOT_MULTIPLY_CONFIDENCE"
    };
  }

  function durabilityMissingText() {
    return "未提供 / 旧卡兼容";
  }

  function durabilityScoreText(durability) {
    const layer = asObject(durability);
    const anchor = asObject(layer.price_anchor_durability);
    const score = firstPresent(
      layer.headline_score, layer.score, anchor.durability_score,
      anchor.headline_score, anchor.score
    );
    return isNullish(score) || score === ""
      ? durabilityMissingText()
      : scalarText(durabilityScoreNumber(score), { translate: false, digits: 2 });
  }

  function durabilityStateText(durability) {
    const layer = asObject(durability);
    const anchor = asObject(layer.price_anchor_durability);
    const state = firstPresent(
      layer.headline_state, layer.state, anchor.durability_state,
      anchor.headline_state, anchor.state
    );
    return isBlank(state) ? "旧卡兼容" : semanticCompact(state);
  }

  function durabilityComfortTag(durability) {
    const comfort = asObject(asObject(durability).comfort_window);
    return comfort.tag || durabilityMissingText();
  }

  function durabilityComfortBrief(durability) {
    const profile = durabilityComfortProfile(durabilityComfortTag(durability));
    return profile.sidebar;
  }

  function durabilityTimeWindow(durability) {
    const comfort = asObject(asObject(durability).comfort_window);
    return firstPresent(
      comfort.time_window, comfort.window, comfort.clock_window,
      comfort.brief_token, comfort.tag
    );
  }

  function durabilityScoreNumber(value) {
    const numeric = safeNumber(value);
    if (numeric === null) return null;
    return numeric <= 1 ? numeric * 100 : numeric;
  }

  function durabilityScoreLine(value) {
    const normalized = durabilityScoreNumber(value);
    return normalized === null
      ? durabilityMissingText()
      : `${scalarText(normalized, { translate: false, digits: 0 })}/100`;
  }

  function durabilityReadable(value, fallback = "未提供") {
    if (isBlank(value)) return fallback;
    const raw = rawEnum(value);
    const translated = semanticCompact(raw);
    if (translated && translated !== raw) return translated;
    return isEnum(raw) ? fallback : raw;
  }

  function durabilityComfortProfile(tag) {
    const raw = rawEnum(tag);
    const profiles = {
      US_T2_EARLY_REPRICE: {
        sidebar: "舒适窗 是",
        title: "舒适窗：是",
        value: "T2早段",
        detail: "处于美国盘 T2 早段再定价窗口，信号前提在主流动性重新接入前有较好的观察舒适度。"
      },
      US_T2_CORE_COMFORT: {
        sidebar: "舒适窗 是",
        title: "舒适窗：是",
        value: "T2核心",
        detail: "处于美国盘 T2 核心舒适窗口，时间前提对下一轮观察更友好。"
      },
      US_T2_TIME_ONLY: {
        sidebar: "舒适窗 是",
        title: "舒适窗：时间命中",
        value: "日历降级",
        detail: "时间落在 T2 区间，但周末/日历因素会降级解释，只作为观察舒适提示。"
      },
      NORMAL_WINDOW: {
        sidebar: "舒适窗 否",
        title: "舒适窗：否",
        value: "普通窗口",
        detail: "当前不处于 T2 舒适观察窗，按普通窗口解释，不额外抬高信号耐用性。"
      }
    };
    return profiles[raw] || {
      sidebar: "舒适窗 关闭",
      title: "舒适窗：关闭",
      value: "关闭",
      detail: "本卡没有可直接展示的舒适窗口结论；旧卡只展示已有耐用性信息。"
    };
  }

  function durabilityLayerScore(layer) {
    const score = firstPresent(asObject(layer).score_100, asObject(layer).score);
    return durabilityScoreLine(score);
  }

  function durabilityUsdText(value) {
    const numeric = safeNumber(value);
    if (numeric === null) return "未提供";
    const sign = numeric < 0 ? "-" : "";
    const absValue = Math.abs(numeric);
    const fixedOne = (scaled) => new Intl.NumberFormat("en-US", {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1
    }).format(scaled);
    if (absValue >= 1_000_000_000) return `${sign}${fixedOne(absValue / 1_000_000_000)}B USD`;
    if (absValue >= 1_000_000) return `${sign}${fixedOne(absValue / 1_000_000)}M USD`;
    if (absValue >= 1_000) return `${sign}${fixedOne(absValue / 1_000)}K USD`;
    return `${sign}${number(absValue, 0)} USD`;
  }

  function durabilityFirstNumber(values) {
    for (const value of values) {
      const numeric = safeNumber(value);
      if (numeric !== null) return numeric;
    }
    return null;
  }

  function durabilityGammaNotional(doc, layer) {
    const view = asObject(layer);
    const gexValue = durabilityFirstNumber([
      get(doc, "factor_cross_section.gex_info.net_gamma_notional_usd"),
      get(doc, "factor_cross_section.gex_info.total_net_gex"),
      get(doc, "factor_cross_section.gex_info.net_gamma_notional")
    ]);
    if (gexValue !== null) return gexValue === 0 ? null : gexValue;
    const nativeValue = durabilityFirstNumber([
      view.net_gamma_notional_usd,
      view.total_net_gex,
      view.net_gamma_notional,
      view.net_gex_usd
    ]);
    const nativeSource = rawEnum(view.net_gamma_source).toLowerCase();
    if (nativeValue !== null
        && (Math.abs(nativeValue) >= 1000
            || nativeSource.includes("gex_info")
            || nativeSource.includes("compat_usd"))) {
      return nativeValue === 0 ? null : nativeValue;
    }
    const gammaValue = durabilityFirstNumber([
      get(doc, "factor_cross_section.gamma_regime.net_gamma_notional_usd"),
      get(doc, "factor_cross_section.gamma_regime.total_net_gex"),
      get(doc, "factor_cross_section.gamma_regime.net_gamma_notional")
    ]);
    if (gammaValue !== null && Math.abs(gammaValue) >= 1000) {
      return gammaValue === 0 ? null : gammaValue;
    }
    return null;
  }

  function durabilityGammaPolarity(_doc, _layer, netGamma) {
    if (netGamma !== null && netGamma > 0) return "positive";
    if (netGamma !== null && netGamma < 0) return "negative";
    return "neutral";
  }

  function durabilityFundingRate(doc, layer) {
    const view = asObject(layer);
    return firstNumberFrom([
      view,
      get(doc, "factor_cross_section.funding", {}),
      get(doc, "factor_cross_section.tmvf.tmvf_48h.funding", {})
    ], ["funding_rate", "last_rate", "last_funding_rate", "rate"]);
  }

  function durabilityBandDistance(doc, layer, anchor) {
    return firstNumberFrom([
      layer,
      anchor,
      get(doc, "factor_cross_section.anchor", {})
    ], [
      "distance_ratio",
      "band_distance_ratio",
      "normalized_deviation",
      "anchor_normalized_deviation",
      "distance_to_anchor_band_half"
    ]);
  }

  function durabilityLayerNarrative(key, layer, anchor, doc) {
    const view = asObject(layer);
    if (!Object.keys(view).length) {
      return {
        conclusion: "旧卡未提供",
        validation: "本卡没有此子层的可验算输入。",
        basis: "只保留缺口提示，不回填或发明评分。"
      };
    }
    const state = durabilityReadable(firstPresent(view.interpretation, view.state), "待判");
    if (key === "anchor_native") {
      const score = durabilityLayerScore(view);
      const distance = durabilityBandDistance(doc, view, anchor);
      const distanceAbs = distance === null ? null : Math.abs(distance);
      const outsideBand = view.outside_anchor_band === true
        || (view.inside_anchor_band !== true && distanceAbs !== null && distanceAbs > 1);
      const distanceText = distance === null
        ? "本卡未提供可直接换算的锚带偏离度"
        : (outsideBand
          ? `现价已离开锚带，偏离约 ${number(distanceAbs, 2)} 个锚带半宽`
          : `现价仍在锚带内，偏离约 ${number(distanceAbs, 2)} 个锚带半宽`);
      const boundaryText = outsideBand ? "需要按破锚/弱锚边界解释。" : "未触发破锚边界。";
      return {
        conclusion: outsideBand && state === "锚耐用" ? "离锚风险" : state,
        validation: `系统锚分 ${score}；${distanceText}，${boundaryText}`,
        basis: "锚原生层是价格锚主干分，Gamma、Funding 只作为稳定性解释，不替代锚分。"
      };
    }
    if (key === "price_efficiency") {
      const ppe = safeNumber(view.ppe);
      const efficiency = ppe === null
        ? "路径效率未提供"
        : (ppe >= 0.75 ? "路径效率较高" : (ppe >= 0.45 ? "路径效率中等" : "路径效率偏低"));
      const source = rawEnum(firstPresent(view.method, view.ppe_source)).includes("OHLC")
        ? "OHLC 近似路径"
        : (rawEnum(view.method).includes("EXACT") ? "逐点价格路径" : "价格路径");
      const band = view.inside_anchor_band === true
        ? "仍留在锚带内"
        : (view.outside_anchor_band === true ? "已离开锚带" : "锚带位置未定");
      const conclusion = view.outside_anchor_band === true && ppe !== null && ppe >= 0.75
        ? "高效离锚风险"
        : state;
      return {
        conclusion,
        validation: `${source}显示本段${efficiency}，${band}；价格移动更直接，但没有单独推翻锚。`,
        basis: "路径效率解释价格移动是否顺滑或反身；高效且离锚才提示脆弱来源，留在锚带内时只是路径质量解释。"
      };
    }
    if (key === "options_gamma") {
      const netGamma = durabilityGammaNotional(doc, view);
      const polarity = durabilityGammaPolarity(doc, view, netGamma);
      if (polarity === "positive") {
        return {
          conclusion: "正 Gamma 稳定器",
          validation: `当前净 Gamma 约 ${durabilityUsdText(netGamma)}，为正 Gamma 环境；做市对冲更偏向抑制波动。`,
          basis: "正 Gamma 通常降低价格离锚后的反身放大风险，是缓冲项；Gamma 只解释稳定性，不单独决定方向。"
        };
      }
      if (polarity === "negative") {
        return {
          conclusion: "负 Gamma 放大风险",
          validation: `当前净 Gamma 约 ${durabilityUsdText(netGamma)}，为负 Gamma 环境；价格离锚后更容易被对冲流放大。`,
          basis: "负 Gamma 是锚脆弱来源提示，但仍不单独决定方向，也不直接改变交易许可。"
        };
      }
      return {
        conclusion: "Gamma 状态待判",
        validation: "本卡没有可直接确认的有效净 Gamma；只能保留为放大器缺口提示。",
        basis: "Gamma 是波动放大/稳定解释层，缺口时不替代价格锚结论。"
      };
    }
    if (key === "perp_funding") {
      const rate = durabilityFundingRate(doc, view);
      const threshold = 0.0001;
      const rateText = rate === null ? "Funding 未提供" : `Funding ${percent(rate, 3)}`;
      const thresholdText = percent(threshold, 2);
      const crowded = rate !== null && Math.abs(rate) > threshold;
      const relationText = rate === null
        ? `未提供可对比 ${thresholdText} 阈值的费率`
        : (crowded ? `超过 ${thresholdText} 初步拥挤阈值` : `不高于 ${thresholdText} 基准阈值`);
      const alignText = rate === null
        ? "方向关系待费率确认"
        : (view.funding_aligns_with_signal === true
          ? "温和同向"
          : (view.funding_against_signal === true ? "逆向" : "方向待判"));
      const crowdText = rate === null
        ? `杠杆拥挤状态待判，${alignText}`
        : (crowded ? `杠杆拥挤抬升，${alignText}且已超过基准阈值` : `杠杆拥挤未抬升，${alignText}且不拥挤`);
      return {
        conclusion: rate !== null && view.funding_aligns_with_signal === true && !crowded ? "温和同向 Funding" : state,
        validation: `当前 ${rateText}，${relationText}；${crowdText}。`,
        basis: "Funding 是杠杆健康度解释，用来说明拥挤或踩踏风险，不进入交易许可。"
      };
    }
    return {
      conclusion: state,
      validation: "本层没有独立的可读验算口径。",
      basis: "保留已有结论，不扩大解释范围。"
    };
  }

  function durabilityReasonSentence(code) {
    const labels = {
      ANCHOR_PRICE_FALLBACK_MARKET_PRICE: "锚价使用当前市场价兜底。",
      ANCHOR_PRICE_MISSING: "锚价缺失，结论按缺口处理。",
      ANCHOR_BAND_DEFAULT_HALF_PCT_0_004: "锚带使用默认 0.4% 半宽。",
      ANCHOR_NATIVE_DISTANCE_PROXY_USED: "锚原生分使用距离代理。",
      ANCHOR_NATIVE_SCORE_MISSING: "锚原生分缺失。",
      PRICE_OUTSIDE_ANCHOR_BAND: "价格已经离开锚带。",
      ANCHOR_NATIVE_BROKEN_CAP: "锚原生层触发破坏上限。",
      PPE_CANNOT_AUTOPASS_ANCHOR: "PPE 不能覆盖已破坏的锚。",
      PPE_ADVERSE_BAND_BREAK_HARD_BREAKER: "不利高效脱锚触发硬破坏。",
      PPE_PROXY_OHLC_USED: "PPE 使用 OHLC 近似路径。",
      PPE_FLAT_EXACT_PATH: "逐点路径显示净移动很小。",
      PPE_FLAT_PROXY_RANGE: "OHLC 区间显示净移动很小。",
      MARKET_PRICE_DATA_GAP: "市场价格缺口。",
      OPTIONS_GAMMA_DATA_GAP: "Gamma 输入缺口。",
      PERP_FUNDING_DATA_GAP: "Funding 输入缺口。",
      COMFORT_WINDOW_OK: "舒适窗口判断已完成。",
      PRICE_ANCHOR_OK: "价格锚层有可用结论。",
      NO_TRADE_GATE: "本层不作为交易准入依据。"
    };
    if (labels[code]) return labels[code];
    const readable = durabilityReadable(code, "");
    if (readable) return `${readable}。`;
    return "";
  }

  function durabilityReasonList(codes) {
    const values = [...new Set(asArray(codes).map(durabilityReasonSentence).filter(Boolean))];
    if (!values.length) return "";
    return `<ul class="durability-reason-list">${values.slice(0, 5).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
  }

  function durabilitySignedPp(value) {
    const numeric = safeNumber(value);
    if (numeric === null) return "";
    const sign = numeric > 0 ? "+" : "";
    return `${sign}${number(numeric, 2)}pp`;
  }

  function durabilityTemporalBasis(temporal, session) {
    const temporalView = asObject(temporal);
    const sessionView = asObject(session);
    const basis = asObject(firstPresent(
      temporalView.validation_basis,
      sessionView.validation_basis
    ));
    const rawDelta = durabilitySignedPp(firstPresent(
      temporalView.backtest_delta_pp,
      sessionView.backtest_delta_pp
    ));
    const rating = durabilityReadable(
      firstPresent(
        temporalView.display_label,
        temporalView.premise_durability,
        temporalView.effective_zone,
        temporalView.state,
        sessionView.display_label,
        sessionView.premise_durability,
        sessionView.effective_zone,
        sessionView.state
      ),
      "未提供 / 旧卡兼容"
    );
    const score = firstPresent(temporalView.score, sessionView.score);
    const headline = rawDelta ? `${rating} · ${rawDelta}` : rating;
    const parts = [];
    if (rawDelta) parts.push(`三年回测原始值 ${rawDelta}`);
    else if (!isBlank(score)) parts.push(`展示分 ${durabilityScoreLine(score)}`);
    else parts.push("三年回测原始值未提供");
    parts.push(`评级 ${rating}`);
    if (!isBlank(basis.data_range)) parts.push(`样本区间 ${basis.data_range}`);
    if (!isBlank(basis.sample_bars)) parts.push(`样本 ${scalarText(basis.sample_bars, { translate: false, digits: 0 })} 根`);
    if (!isBlank(basis.headline_horizon_min)) parts.push(`未来窗 ${scalarText(basis.headline_horizon_min, { translate: false, digits: 0 })} 分钟`);
    if (!isBlank(basis.research_grade)) parts.push(`研究等级 ${durabilityReadable(basis.research_grade, basis.research_grade)}`);
    return {
      headline,
      detail: `${parts.join("；")}。只作为解释，不进入价格锚评分。`
    };
  }

  function renderDurabilitySummaryCard(title, value, detail) {
    return `
      <div class="durability-summary-card">
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(value)}</span>
        <p>${escapeHtml(detail)}</p>
      </div>
    `;
  }

  function renderDurabilityLayerCard(definition, layers, anchor, doc) {
    const layer = asObject(asObject(layers)[definition.key]);
    const score = durabilityLayerScore(layer);
    const narrative = durabilityLayerNarrative(definition.key, layer, anchor, doc);
    return `
      <article class="durability-layer-card">
        <div class="durability-layer-title">
          <strong>${escapeHtml(definition.title)}</strong>
          <span>${escapeHtml(score)}</span>
        </div>
        <p class="durability-layer-state"><strong>结论</strong>：${escapeHtml(narrative.conclusion)}</p>
        <p class="durability-layer-validation"><strong>最小验算</strong>：${escapeHtml(narrative.validation)}</p>
        <p class="durability-layer-basis"><strong>评分依据</strong>：${escapeHtml(narrative.basis)}</p>
      </article>
    `;
  }

  function renderSignalDurability(doc) {
    const durability = signalDurability(doc);
    const hasDurability = Object.keys(durability).length > 0;
    const comfort = asObject(durability.comfort_window);
    const temporal = asObject(durability.temporal_session);
    const session = asObject(durability.session_context);
    const anchor = asObject(durability.price_anchor_durability);
    const layers = asObject(firstPresent(anchor.layer_scores, durability.layer_scores));
    const reasonCodes = asArray(durability.reason_codes);
    const dataGaps = asArray(durability.data_gaps);
    const layerDefs = [
      { key: "anchor_native", title: "锚原生层" },
      { key: "price_efficiency", title: "PPE路径效率" },
      { key: "options_gamma", title: "GEX / Gamma放大器" },
      { key: "perp_funding", title: "Funding杠杆解释" },
    ];
    const headlineScore = firstPresent(
      durability.headline_score, durability.score,
      anchor.durability_score, anchor.score
    );
    const headlineState = durabilityStateText(durability);
    const comfortProfile = durabilityComfortProfile(comfort.tag);
    const temporalBasis = durabilityTemporalBasis(temporal, session);
    const qualityText = durabilityReadable(
      firstPresent(durability.score_quality, anchor.score_quality),
      "评分质量未提供"
    );
    const dataGapText = dataGaps.length
      ? "存在输入缺口，结论按保守口径展示。"
      : "关键耐用性输入未报告缺口。";
    const compatText = durability.compat_backfill_applied
      ? "兼容回填已标记；旧卡只展示已有别名，不发明新分数。"
      : (hasDurability ? "本卡提供原生耐用性结论。" : "旧卡兼容：未提供耐用性字段，只保留空结论。");
    const scoreDetail = `${headlineState}；${qualityText}。耐用性是结构健康指数，不是胜率。`;
    return section("信号耐用性层", "说明舒适窗口、时区前提和价格锚健康度；交易许可仍看当前边界。", `
      <div class="signal-durability-panel">
        <p class="durability-policy"><strong>背景说明</strong>：本层合并舒适窗口、时区前提和价格锚耐用性，只展示结构健康结论；等待条件、阻断和执行权限仍然有效。</p>
        <div class="durability-summary-grid">
          ${renderDurabilitySummaryCard("合成耐用性", durabilityScoreLine(headlineScore), scoreDetail)}
          ${renderDurabilitySummaryCard("舒适窗口", comfortProfile.title, `${comfortProfile.value}；${comfortProfile.detail}`)}
          ${renderDurabilitySummaryCard("时区耐用性", temporalBasis.headline, temporalBasis.detail)}
        </div>
      </div>
      <details class="signal-durability-detail">
        <summary>展开耐用性推导与来源</summary>
        <div class="durability-layer-list">
          ${layerDefs.map((definition) => renderDurabilityLayerCard(definition, layers, anchor, doc)).join("")}
        </div>
        <div class="durability-conclusion-card">
          <strong>合成解释</strong>
          <p>合成值 ${escapeHtml(durabilityScoreLine(headlineScore))}，当前结论为 ${escapeHtml(headlineState)}。四层以现有锚分/偏离度为主干，PPE、Gamma 与 Funding 只解释锚的稳定或脆弱来源；${escapeHtml(dataGapText)}</p>
          ${durabilityReasonList(reasonCodes)}
          <p class="durability-compat-note">${escapeHtml(compatText)}</p>
        </div>
      </details>
    `, "signal-durability");
  }

  function renderSignalSessionContext(doc) {
    const ctx = asObject(get(doc, "signal_window.session_context", {}));
    if (!Object.keys(ctx).length) return "";
    const transition = asObject(ctx.transition);
    const weekend = asObject(ctx.weekend_adjustment);
    const event = asObject(ctx.event_blackout);
    const basis = asObject(ctx.validation_basis);
    const display = ctx.display_label || ctx.effective_zone || ctx.base_zone || "UNKNOWN";
    const legacyMissing = "旧卡未提供";
    const basisLine = basis.data_range
      ? `BTC_USDT 5m K线 ${basis.data_range}，${scalarText(basis.sample_bars, { translate: false, digits: 0 })} 根；主口径未来窗 ${scalarText(basis.headline_horizon_min, { translate: false, digits: 0 })} 分钟。`
      : "等待验证依据字段";
    const chips = [
      `展示档位: ${semanticLabel(display)}`,
      `有效档位: ${semanticLabel(ctx.effective_zone)}`,
      `时区调整: ${isNullish(ctx.adjustment_direction) ? legacyMissing : semanticLabel(ctx.adjustment_direction)}`,
      `证据等级: ${isNullish(ctx.evidence_level) ? legacyMissing : semanticLabel(ctx.evidence_level)}`,
      `校准: ${semanticCompact(ctx.calibration_state)}`,
      ctx.affects_confidence === false ? "原信号把握不变" : "",
      ctx.affects_blocking === false ? "当前限制不变" : "",
      ctx.affects_trade_allowed === false ? "不改交易许可" : "",
    ].filter(Boolean);
    const transitionText = transition.active
      ? `${semanticCompact(ctx.display_label)} / ${transition.boundary || ""} / ${scalarText(transition.minutes_from_boundary, { translate: false, digits: 1 })} 分钟`
      : "非边界缓冲";
    const eventText = event.active
      ? `${event.event || "HIGH_IMPACT"} ${event.phase || "WINDOW"}`
      : "无事件黑名单覆盖";
    const weekendText = weekend.applied
      ? `${semanticCompact(weekend.from_zone)} → ${semanticCompact(weekend.to_zone)}`
      : (weekend.reason ? semanticCompact(weekend.reason) : "未启用（待办）");
    const sessionKv = (key, value, options = {}) => kv(key, value, Object.assign({
      nullText: legacyMissing,
      nullClass: "benign-null-value"
    }, options));
    return section("信号时段与前提状态", "展示信号成立时的时间先验；当前方向、等待条件和交易许可仍看本卡边界。", `
      <div class="session-context-panel">
        <div class="session-context-topline">
          ${chips.map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("")}
        </div>
        <p class="session-context-summary">${valueHtml(ctx.rationale_cn, { translate: false })}</p>
      </div>
      <div class="text-block">
        <p><strong>本层结论</strong> ${escapeHtml(ctx.operator_hint_cn || "保持中性观察。")} 该结论来自三年 K 线代理验证，只影响前提耐久度提示和人工确认要求，不改变 evidence confidence。</p>
      </div>
      <dl class="kv-grid session-context-grid">
        ${sessionKv("rationale_code", ctx.rationale_code, { translate: false })}
        ${sessionKv("clock_window", ctx.clock_window, { translate: false })}
        ${sessionKv("adjustment_direction", ctx.adjustment_direction)}
        ${sessionKv("evidence_level", ctx.evidence_level)}
        ${sessionKv("backtest_delta_pp", ctx.backtest_delta_pp, { translate: false })}
        ${sessionKv("premise_durability", ctx.premise_durability || ctx.effective_zone)}
        ${sessionKv("base_zone", ctx.base_zone)}
        ${sessionKv("display_label", ctx.display_label)}
        ${sessionKv("liquidity_depth", ctx.liquidity_depth)}
        ${sessionKv("catalyst_exposure", ctx.catalyst_exposure)}
        ${sessionKv("boundary_buffer_min", ctx.boundary_buffer_min, { translate: false })}
        ${sessionKv("buffer_policy", ctx.buffer_policy)}
        ${sessionKv("phase", ctx.phase)}
        ${sessionKv("dst_mode", ctx.dst_mode)}
        ${sessionKv("london_dst_mode", ctx.london_dst_mode)}
        ${sessionKv("utc8_time", ctx.utc8_time, { translate: false })}
        ${sessionKv("affects_confidence", ctx.affects_confidence)}
      </dl>
      <div class="two-column-notes session-context-notes">
        <div>
          <h3 class="subsection-title">三年验证依据</h3>
          <p>${escapeHtml(basisLine)}研究等级：${escapeHtml(semanticCompact(basis.research_grade || ctx.calibration_state))}。</p>
        </div>
        <div>
          <h3 class="subsection-title">结论红线</h3>
          <p>本层衡量的是信号论证前提在下一轮主导流动性或外源事件到来前的耐久度，不是胜率，也不是 evidence confidence；${escapeHtml(semanticCompact(ctx.confidence_policy || "DO_NOT_MULTIPLY_CONFIDENCE"))}。</p>
        </div>
        <div>
          <h3 class="subsection-title">边界状态</h3>
          <p>${escapeHtml(transitionText)}</p>
        </div>
        <div>
          <h3 class="subsection-title">事件与周末修正</h3>
          <p>${escapeHtml(eventText)}；周末修正：${escapeHtml(weekendText)}。</p>
        </div>
      </div>
    `, "session-context");
  }

  function renderDisplayLayers(doc) {
    const layers = asObject(get(doc, "display_layers", {}));
    const layerKeys = ["background", "correction", "reasoning", "conflict"];
    const layerItems = layerKeys.map((key) => {
      const layer = asObject(layers[key]);
      if (!Object.keys(layer).length) return "";
      return `
        <li class="line-item">
          <p class="line-title">${escapeHtml(layer.title_cn || key)}</p>
          <p class="line-body">${valueHtml(layer.summary_cn, { translate: false })}</p>
          <div class="source-ref-row">${sourceRefList(layer.source_refs, doc)}</div>
        </li>
      `;
    }).join("");
    return section("展示层摘要", "由 JSON 自带的展示层文案驱动，避免前端自行猜测结论。", `
      <ul class="layer-list">${layerItems}</ul>
      <h3 class="subsection-title">Operator focus</h3>
      <ol class="focus-list">${asArray(layers.operator_focus).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ol>
    `);
  }

  function renderQuality(doc) {
    const quality = asObject(get(doc, "quality", {}));
    const sources = asObject(quality.sources);
    const rows = Object.entries(sources).map(([key, rawSource]) => {
      const source = qualitySourceView(doc, key, rawSource);
      return `
        <tr>
          <td><strong>${escapeHtml(key)}</strong></td>
          <td>${valueHtml(source.required)}</td>
          <td>${statusBadge("", source.status)}</td>
          <td>${isNullish(source.observed_at) ? benignNullHtml("未提供") : escapeHtml(dateText(source.observed_at))}</td>
          <td class="num">${isNullish(source.age_ms) ? benignNullHtml("未提供") : escapeHtml(ageText(source.age_ms))}</td>
          <td>${isNullish(source.source_ref) ? valueHtml(source.source_ref) : sourceRefLink((qualityFallbackPaths[key] || [key])[0], doc, textClip(source.source_ref, 96))}</td>
          <td>${valueHtml(source.reason, { translate: true, nullText: "无错误原因", nullClass: "benign-null-value" })}</td>
        </tr>
      `;
    }).join("");
    const degraded = asArray(quality.degraded_sources);
    return section("数据质量与时效", "按模块主体状态展示 required、status、observed_at、age_ms 和 source_ref；无错误、未配置、冷启动与真正缺失分开标记。", `
      <dl class="kv-grid" style="margin-bottom: 16px;">
        ${kv("Overall", quality.overall)}
        ${kv("All required ready", quality.all_required_sources_ready)}
        ${kv("Missing field count", asArray(quality.missing_fields).length, { translate: false })}
        ${kv("Degraded source count", degraded.length, { translate: false })}
      </dl>
      <div class="table-wrap">
        <table class="source-table">
          <thead><tr>${["Source", "Required", "Status", "Observed at", "Age", "Source ref", "Reason"].map((label) => `<th>${escapeHtml(fieldLabel(label))}</th>`).join("")}</tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <div class="two-column-notes">
        <div><h3 class="subsection-title">Missing fields</h3>${listHtml(quality.missing_fields, "无缺失字段")}</div>
        <div><h3 class="subsection-title">Degraded sources</h3>${degraded.length ? `<ul class="plain-list">${degraded.map((item) => `<li><strong>${escapeHtml(item.source)}</strong> · ${escapeHtml(semanticLabel(item.status))} · ${escapeHtml(item.reason ? semanticLabel(item.reason) : "未提供原因")}</li>`).join("")}</ul>` : `<div class="empty-inline">无降级数据源</div>`}</div>
      </div>
    `);
  }

  function renderBlocking(doc) {
    const blocking = asObject(get(doc, "blocking", {}));
    const gates = asArray(blocking.soft_gates);
    const conditions = asArray(blocking.unblock_conditions);
    return section("阻断与解除条件", "阻断原因和解除条件使用结构化字段，不从文案反推。", `
      <dl class="kv-grid" style="margin-bottom: 16px;">
        ${kv("Has block", blocking.has_block)}
        ${kv("Block kind", blocking.block_kind)}
        ${kv("Hard veto", blocking.hard_veto, { nullText: "无硬否决", nullClass: "benign-null-value" })}
      </dl>
      <ul class="adjustment-list">
        ${gates.map((gate) => `<li class="line-item"><p class="line-title">${escapeHtml(semanticLabel(gate.gate))}</p><div class="change"><span class="chip">${escapeHtml(semanticLabel(gate.reason_code))}</span></div><p class="line-body">${valueHtml(gate.reason_cn, { translate: false })}</p></li>`).join("") || `<li class="empty-inline">无 soft gate</li>`}
      </ul>
      <h3 class="subsection-title">Unblock conditions</h3>
      <ul class="adjustment-list">${conditions.map((item) => `<li class="line-item"><p class="line-title">${escapeHtml(item.condition_cn || item.metric)}</p><div class="change"><span class="chip">${escapeHtml(item.metric)}</span><span class="chip">${escapeHtml(item.operator)}</span><span class="chip">threshold: ${escapeHtml(scalarText(item.threshold, { translate: false }))}</span></div></li>`).join("") || `<li class="empty-inline">无解除条件</li>`}</ul>
    `);
  }

  function evidenceValueHtml(evidence, field, value) {
    if (!isNullish(value)) return valueHtml(value, { translate: field !== "source_ref" });
    const status = rawEnum(evidence.participation_status).toUpperCase();
    if (field === "exclusion_reason") {
      return ["ACTIVE"].includes(status) ? benignNullHtml("无排除") : benignNullHtml("不适用");
    }
    if (["EXCLUDED", "NON_VOTING", "GATE_ONLY"].includes(status)) {
      if (field === "vote") return benignNullHtml("本次未作为方向依据");
      if (field === "reliability" || field === "lean") return benignNullHtml("不适用");
    }
    return valueHtml(null);
  }

  function evidenceKey(evidence) {
    return rawEnum(evidence && evidence.key).toUpperCase();
  }

  function factorNodeForEvidence(doc, evidence) {
    const key = evidenceKey(evidence);
    const cross = asObject(get(doc, "factor_cross_section", {}));
    if (key === "FUNDING") {
      const funding = { ...asObject(cross.funding) };
      const tmvf48h = asObject(asObject(cross.tmvf).tmvf_48h);
      const funding48h = asObject(tmvf48h.funding);
      Object.entries(funding48h).forEach(([name, value]) => {
        if (funding[name] === undefined || funding[name] === null || funding[name] === "") funding[name] = value;
      });
      if ((funding.funding_state === undefined || funding.funding_state === null || funding.funding_state === "") && tmvf48h.funding_state !== undefined) funding.funding_state = tmvf48h.funding_state;
      if ((funding.last_rate === undefined || funding.last_rate === null || funding.last_rate === "") && funding.last_funding_rate !== undefined) funding.last_rate = funding.last_funding_rate;
      if ((funding.effect === undefined || funding.effect === null || funding.effect === "") && funding.tmvf_funding_effect !== undefined) funding.effect = funding.tmvf_funding_effect;
      return funding;
    }
    if (key === "SRD") return asObject(cross.skew);
    if (key === "GGR_SPATIAL") return asObject(cross.gamma_regime);
    if (key === "TMV") return asObject(cross.tmvf);
    if (key === "FLOW_CONFIRM") return asObject(cross.micro_flow);
    if (key === "MACRO") return asObject(cross.macro_pressure);
    if (key === "CVD_4H") return asObject(asObject(cross.micro_flow).fast_4h);
    if (key === "CVD_12H") return asObject(asObject(cross.micro_flow).slow_12h);
    return {};
  }

  function evidenceAuxiliaryRole(evidence) {
    const role = evidence && evidence.auxiliary_role;
    if (role) return role;
    return {
      FUNDING: "FUTURES_FUNDING_SEMANTICS",
      SRD: "OPTION_SKEW_DIRECTION",
      GGR_SPATIAL: "OPTION_GAMMA_STRUCTURE",
      TMV: "DIRECTION_OWNER",
      FLOW_CONFIRM: "FLOW_CONFIRMATION",
      MACRO: "MACRO_CONTEXT",
      CVD_4H: "FLOW_CONFIRM_COMPONENT",
      CVD_12H: "FLOW_CONFIRM_COMPONENT"
    }[evidenceKey(evidence)] || null;
  }

  function firstNumberFrom(objects, fields) {
    for (const object of objects) {
      const source = asObject(object);
      for (const field of fields) {
        const value = safeNumber(source[field]);
        if (value !== null) return value;
      }
    }
    return null;
  }

  function signedLean(value) {
    const numeric = safeNumber(value);
    if (numeric === null) return null;
    if (numeric > 0) return "BULLISH";
    if (numeric < 0) return "BEARISH";
    return "NEUTRAL";
  }

  function macroLeanFromScore(score) {
    const numeric = safeNumber(score);
    if (numeric === null) return null;
    if (numeric > 0) return "BEARISH";
    if (numeric < 0) return "BULLISH";
    return "NEUTRAL";
  }

  function evidenceAuxiliaryLean(evidence, doc) {
    if (evidence && evidence.auxiliary_lean) return evidence.auxiliary_lean;
    const key = evidenceKey(evidence);
    const detail = asObject(evidence && evidence.detail);
    const factor = factorNodeForEvidence(doc, evidence);
    if (key === "FUNDING") {
      const rate = firstNumberFrom([detail, factor], ["last_rate", "last_funding_rate", "funding_norm"]);
      return signedLean(rate === null ? null : -rate);
    }
    if (key === "SRD") {
      return signedLean(firstNumberFrom([evidence, detail, factor], ["vote"]));
    }
    if (key === "GGR_SPATIAL") {
      if (factor.veto) return "RISK_CONSTRAINT";
      const regime = rawEnum(factor.regime || detail.regime).toUpperCase();
      if (regime.includes("NEGATIVE") || regime.includes("AMPLIFY")) return "RISK_CONSTRAINT";
      if (regime.includes("POSITIVE") || regime.includes("PINNING")) return "SUPPORTIVE";
      const multiplier = firstNumberFrom([factor, detail], ["confidence_multiplier"]);
      if (multiplier !== null && multiplier > 1) return "SUPPORTIVE";
      if (multiplier !== null && multiplier < 1) return "CONSTRAINT";
      return "NEUTRAL";
    }
    if (key === "MACRO") {
      return signedLean(firstNumberFrom([evidence, detail, factor], ["vote"]))
        || macroLeanFromScore(firstNumberFrom([detail, factor], ["macro_score", "score"]));
    }
    return signedLean(firstNumberFrom([evidence, detail, factor], ["vote"]));
  }

  function evidenceRawFields(key, detail) {
    const defaults = {
      FUNDING: ["last_rate", "last_funding_rate", "funding_norm", "funding_cum", "funding_count", "funding_state", "effect", "tmvf_funding_effect", "verdict", "hard_warning", "history_points", "observed_at", "age_ms", "source_ref"],
      SRD: ["vote", "rr_blend", "rr_25d", "delta_rr", "rr_z", "skew_norm_blend", "skew_slope", "term_slope", "vote_confidence", "target_expiry_hours", "expiry_count", "data_status", "data_state", "observed_at", "age_ms", "source_ref"],
      GGR_SPATIAL: ["regime", "regime_strength", "confidence_multiplier", "veto", "veto_reason", "net_gamma_notional_usd", "net_gamma_notional", "flip_point", "distance_to_flip_pct", "pin_strike", "distance_to_pin_pct", "pin_pull_direction", "max_gamma_strike", "call_wall", "put_wall", "market_state", "observed_at", "age_ms", "source_ref"],
      MACRO: ["macro_score", "score", "macro_regime", "regime", "verdict", "data_status", "data_confidence", "macro_data_confidence", "components", "component_scores", "macro_components_cn", "macro_shock", "legacy_blocking_flags", "blocking_flags", "reason_codes", "source_ref"]
    };
    return defaults[key] || Object.keys(detail);
  }

  function evidenceRawValues(evidence, doc) {
    const key = evidenceKey(evidence);
    const existing = asObject(evidence && evidence.raw_values);
    const detail = asObject(evidence && evidence.detail);
    const factor = factorNodeForEvidence(doc, evidence);
    const raw = { ...existing };
    evidenceRawFields(key, detail).forEach((field) => {
      if (raw[field] !== undefined && raw[field] !== null && raw[field] !== "") return;
      const value = detail[field] !== undefined ? detail[field] : factor[field];
      if (value !== undefined && value !== null && value !== "") raw[field] = value;
    });
    const pin = asObject(factor.pin);
    if (key === "GGR_SPATIAL") {
      [["pin_strike", "pin_strike"], ["distance_to_pin_pct", "distance_to_pin_pct"], ["pin_pull_direction", "pin_pull_direction"]].forEach(([source, target]) => {
        if ((raw[target] === undefined || raw[target] === null || raw[target] === "") && pin[source] !== undefined && pin[source] !== null && pin[source] !== "") raw[target] = pin[source];
      });
    }
    if (key === "FUNDING") {
      if ((raw.last_rate === undefined || raw.last_rate === null || raw.last_rate === "") && raw.last_funding_rate !== undefined) raw.last_rate = raw.last_funding_rate;
      if ((raw.effect === undefined || raw.effect === null || raw.effect === "") && raw.tmvf_funding_effect !== undefined) raw.effect = raw.tmvf_funding_effect;
    }
    return raw;
  }

  function rawValueText(value, depth = 0) {
    if (isNullish(value) || value === "") return "null";
    if (Array.isArray(value)) {
      if (!value.length) return "[]";
      return value.map((item) => rawValueText(item, depth + 1)).join(" ; ");
    }
    if (typeof value === "object") {
      const entries = Object.entries(value)
        .filter(([, child]) => child !== undefined && child !== null && child !== "");
      if (!entries.length) return "{}";
      return entries
        .map(([name, child]) => `${name}: ${rawValueText(child, depth + 1)}`)
        .join(" / ");
    }
    return scalarText(value, { translate: false, digits: 4 });
  }

  function textClip(value, limit = 72) {
    const text = scalarText(value, { translate: false, digits: 4 });
    return text.length > limit ? `${text.slice(0, limit - 3)}...` : text;
  }

  function rawTraceRoot(ref) {
    const text = rawEnum(ref).trim();
    if (!text) return "";
    const parts = text.split(".").filter(Boolean);
    if (parts.length >= 2 && parts[0] === "factor_cross_section") return `${parts[0]}.${parts[1]}`;
    if (parts.length >= 2 && parts[0] === "evidence_raw_values") return `${parts[0]}.${parts[1]}`;
    return text;
  }

  function rawTraceId(ref) {
    const token = rawTraceRoot(ref)
      .replace(/[^A-Za-z0-9_-]+/g, "-")
      .replace(/-+/g, "-")
      .replace(/^-|-$/g, "");
    return `raw-${token || "trace"}`;
  }

  function evSourceRoots(ref) {
    const root = rawTraceRoot(ref).toUpperCase();
    if (!root.startsWith("EV_")) return [];
    const key = root.replace(/^EV_/, "");
    if (/ANCHOR|GGR|GAMMA|GEX|PIN/.test(key)) return ["factor_cross_section.gex_info", "factor_cross_section.gamma_regime", "factor_cross_section.anchor"];
    if (/SRD|SKEW/.test(key)) return ["factor_cross_section.skew"];
    if (/TMV|PRICE/.test(key)) return ["factor_cross_section.tmvf"];
    if (/FLOW|CVD/.test(key)) return ["factor_cross_section.micro_flow"];
    if (/FUNDING/.test(key)) return ["factor_cross_section.funding"];
    if (/MACRO/.test(key)) return ["factor_cross_section.macro_pressure"];
    if (/QUALITY/.test(key)) return ["quality"];
    if (/NR|REPAIR|WINDOW|EPISODE/.test(key)) return ["signal_window", "factor_cross_section.neutral_repair", "neutral_repair"];
    return [];
  }

  function hasRawTraceTarget(ref, doc) {
    const root = rawTraceRoot(ref);
    if (/^EV_[A-Z0-9_]+$/.test(root)) {
      const expected = root.replace(/^EV_/, "");
      const hasMappedFact = evSourceRoots(root).some((path) => {
        const value = get(doc, path);
        if (isNullish(value)) return false;
        return typeof value === "object" ? Object.keys(asObject(value)).length > 0 : true;
      });
      if (hasMappedFact) return true;
      return asArray(get(doc, "reasoning.evidence", [])).some((evidence) =>
        evidenceKey(evidence) === expected
        && Object.keys(asObject(evidence)).length > 0);
    }
    if (root.startsWith("factor_cross_section.")) {
      const value = get(doc, root);
      if (isNullish(value)) return false;
      if (typeof value === "object") return Object.keys(asObject(value)).length > 0;
      return true;
    }
    if (root.startsWith("evidence_raw_values.")) {
      const key = root.slice("evidence_raw_values.".length).toUpperCase();
      return asArray(get(doc, "reasoning.evidence", [])).some((evidence) =>
        evidenceKey(evidence) === key
        && Object.keys(asObject(evidence && evidence.raw_values)).length > 0);
    }
    return false;
  }

  function sourceRefSemanticKey(ref) {
    const root = rawTraceRoot(ref).toLowerCase();
    if (!root) return "";
    if (root.startsWith("ev_")) {
      if (root.includes("anchor") || root.includes("ggr") || root.includes("gamma") || root.includes("gex")
        || root.includes("pin") || root.includes("skew") || root.includes("srd")) return "options-structure";
      if (root.includes("tmv") || root.includes("price")) return "price-path";
      if (root.includes("flow") || root.includes("cvd")) return "active-flow";
      if (root.includes("funding")) return "funding-rate";
      if (root.includes("macro")) return "macro-background";
    }
    if (root.includes("anchor") || root.includes("gamma_regime") || root.includes("gex_info")
      || root.includes("pin") || root.includes("skew") || root.includes("srd")) return "options-structure";
    if (root.includes("tmvf") || root.includes("tmv")) return "price-path";
    if (root.includes("micro_flow") || root.includes("flow") || root.includes("cvd")) return "active-flow";
    if (root.includes("funding")) return "funding-rate";
    if (root.includes("macro")) return "macro-background";
    if (root.includes("neutral_repair") || root.includes("signal_window")) return "nr-window";
    if (root.includes("quality")) return "data-quality";
    if (root.includes("future_24h")) return "future-report";
    return "";
  }

  function sourceGroupLabel(value) {
    const raw = rawEnum(value).toUpperCase().replace(/^EV_/, "");
    const labels = {
      ANCHOR: "价格锚",
      GGR: "Gamma 结构",
      GAMMA: "Gamma 结构",
      GEX: "GEX 空间",
      PIN: "Gamma 钉住",
      SRD: "期权偏斜",
      SKEW: "期权偏斜",
      TMV: "量价主干",
      TMVF: "量价主干",
      FLOW: "主动买卖流",
      MICRO_FLOW: "主动买卖流",
      CVD: "主动买卖流",
      FUNDING: "资金费率",
      MACRO: "宏观背景",
      MACRO_PRESSURE: "宏观背景",
      QUALITY: "数据时效",
      PRICE: "价格",
      GAMMA_REGIME: "Gamma 结构",
      GEX_INFO: "GEX 空间",
      NEUTRAL_REPAIR: "接管窗口"
    };
    return labels[raw] || "";
  }

  function sideHintReadable(value) {
    const raw = rawEnum(value).toUpperCase();
    if (!raw || raw === "UNKNOWN") return "未知";
    if (raw.includes("PUT")) return "Put 信用价差";
    if (raw.includes("CALL")) return "Call 信用价差";
    if (raw.includes("BOTH") || raw.includes("TIE")) return "无单一优先侧";
    if (raw.includes("NONE")) return "无可用侧别";
    return normalizeComfortText(value, "未知");
  }

  function sourceRefLabel(ref, label = null) {
    const explicit = sourceGroupLabel(label);
    if (explicit) return explicit;
    const direct = sourceGroupLabel(ref);
    if (direct) return direct;
    const key = sourceRefSemanticKey(ref);
    const labels = {
      "options-structure": "期权空间结构",
      "price-path": "量价主干",
      "active-flow": "主动买卖流",
      "funding-rate": "资金费率",
      "macro-background": "宏观背景",
      "nr-window": "接管窗口",
      "data-quality": "数据时效",
      "future-report": "24 小时推断"
    };
    return labels[key] || "来源已记录";
  }

  function sourceRefAnchorId(ref) {
    const key = sourceRefSemanticKey(ref);
    return key ? `market-${key}` : "";
  }

  function sourceRefLink(ref, doc, label = null) {
    if (isNullish(ref) || ref === "") return "";
    const root = rawTraceRoot(ref);
    const text = sourceRefLabel(root, label);
    const target = sourceRefAnchorId(root);
    if (!hasRawTraceTarget(root, doc)) {
      return `<span class="chip">${escapeHtml(text)}</span>`;
    }
    return target
      ? `<a class="source-ref-link chip" href="#${escapeHtml(target)}">${escapeHtml(text)}</a>`
      : `<span class="chip">${escapeHtml(text)}</span>`;
  }

  function sourceRefList(refs, doc) {
    return asArray(refs).map((ref) => sourceRefLink(ref, doc)).join("");
  }

  const SIGNAL_EVIDENCE_REVIEW_SCHEMA = "signal_llm_review@2.1.0";
  const SIGNAL_EVIDENCE_REVIEW_SCHEMAS = new Set(["signal_llm_review@2.0.0", "signal_llm_review@2.1.0"]);
  const SIGNAL_EVIDENCE_PROMPT_VERSION = "signal_llm_review_prompt@2.1.0";
  const SIGNAL_EVIDENCE_PROMPT_VERSIONS = new Set([
    "signal_llm_review_prompt@2.0.0",
    "signal_llm_review_prompt@2.0.1",
    "signal_llm_review_prompt@2.1.0"
  ]);
  const SIGNAL_EVIDENCE_SUMMARY_SCHEMA = "signal_evidence_summary@2.1.0";
  const SIGNAL_EVIDENCE_SUMMARY_SCHEMAS = new Set(["signal_evidence_summary@2.0.0", "signal_evidence_summary@2.1.0"]);
  const SIGNAL_EVIDENCE_DISPLAY_PROJECTION_VERSION = "2.1.0";
  const SIGNAL_EVIDENCE_MODE = "single_evidence_v2";
  const SIGNAL_EVIDENCE_SIDE_STATUSES = new Set(["RATED", "UNRATED"]);
  const SIGNAL_EVIDENCE_ACTION_STATES = new Set(["PREPARE", "WATCH", "WAIT", "BLOCKED", "AVOID", "UNRATED"]);
  const SIGNAL_EVIDENCE_COMPARISON_STATUSES = new Set(["ASSESSED", "UNAVAILABLE"]);
  const SIGNAL_EVIDENCE_COMPARISON_SIDES = new Set(["put_credit", "call_credit", "tie", "not_comparable"]);
  const SIGNAL_EVIDENCE_ROLE_LABELS = {
    supports_fit: "支持适配",
    counters_fit: "反对适配",
    context_only: "背景说明"
  };

  function schemaToken(value) {
    return rawEnum(value).trim();
  }

  function isSignalEvidenceSummaryObject(value) {
    const view = asObject(value);
    const schema = schemaToken(firstPresent(view.schema_version, view.schema, view.version));
    return SIGNAL_EVIDENCE_SUMMARY_SCHEMAS.has(schema);
  }

  function isSignalEvidenceReviewObject(value) {
    const view = asObject(value);
    const schema = schemaToken(firstPresent(view.schema_version, view.schema));
    const prompt = schemaToken(firstPresent(view.prompt_version, view.prompt_schema));
    return SIGNAL_EVIDENCE_REVIEW_SCHEMAS.has(schema)
      || SIGNAL_EVIDENCE_PROMPT_VERSIONS.has(prompt);
  }

  function signalEvidenceSummaryCandidate(doc) {
    return firstObject(
      get(doc, "signal_evidence_summary", null),
      get(doc, "summary.signal_evidence_summary", null)
    );
  }

  function rawSignalEvidenceAdvisoryCandidate(doc) {
    const review = asObject(get(doc, "llm_review", {}));
    const content = llmReviewContent(doc);
    return firstObject(
      get(content, "integrated_trade_advisory", null),
      get(review, "content.integrated_trade_advisory", null),
      get(review, "integrated_trade_advisory", null),
      get(doc, "integrated_trade_advisory", null)
    );
  }

  function hasSignalEvidenceV2Surface(doc) {
    const summary = signalEvidenceSummaryCandidate(doc);
    const advisory = rawSignalEvidenceAdvisoryCandidate(doc);
    return isSignalEvidenceSummaryObject(summary)
      || Boolean(Object.keys(asObject(advisory.side_evidence_ratings)).length)
      || isSignalEvidenceReviewObject(get(doc, "llm_review", {}));
  }

  function canonicalJson(value) {
    if (value === null || value === undefined) return "null";
    if (typeof value === "number") return Number.isFinite(value) ? JSON.stringify(value) : "null";
    if (typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
    if (Array.isArray(value)) return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
    if (typeof value === "object") {
      const keys = Object.keys(value)
        .filter((key) => value[key] !== undefined)
        .sort();
      return `{${keys.map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
    }
    return JSON.stringify(String(value));
  }

  function utf8Bytes(value) {
    const text = String(value);
    const bytes = [];
    for (let i = 0; i < text.length; i += 1) {
      let code = text.codePointAt(i);
      if (code > 0xffff) i += 1;
      if (code <= 0x7f) {
        bytes.push(code);
      } else if (code <= 0x7ff) {
        bytes.push(0xc0 | (code >> 6), 0x80 | (code & 0x3f));
      } else if (code <= 0xffff) {
        bytes.push(0xe0 | (code >> 12), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
      } else {
        bytes.push(0xf0 | (code >> 18), 0x80 | ((code >> 12) & 0x3f), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
      }
    }
    return bytes;
  }

  function sha256Hex(value) {
    const k = [
      0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
      0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
      0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
      0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
      0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
      0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
      0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
      0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
    ];
    const h = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    const bytes = utf8Bytes(value);
    const bitLength = bytes.length * 8;
    bytes.push(0x80);
    while (bytes.length % 64 !== 56) bytes.push(0);
    const high = Math.floor(bitLength / 0x100000000);
    const low = bitLength >>> 0;
    bytes.push((high >>> 24) & 0xff, (high >>> 16) & 0xff, (high >>> 8) & 0xff, high & 0xff);
    bytes.push((low >>> 24) & 0xff, (low >>> 16) & 0xff, (low >>> 8) & 0xff, low & 0xff);
    const rotr = (x, n) => (x >>> n) | (x << (32 - n));
    const w = new Array(64);
    for (let offset = 0; offset < bytes.length; offset += 64) {
      for (let i = 0; i < 16; i += 1) {
        const j = offset + i * 4;
        w[i] = ((bytes[j] << 24) | (bytes[j + 1] << 16) | (bytes[j + 2] << 8) | bytes[j + 3]) >>> 0;
      }
      for (let i = 16; i < 64; i += 1) {
        const s0 = (rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3)) >>> 0;
        const s1 = (rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10)) >>> 0;
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
      }
      let [a, b, c, d, e, f, g, hh] = h;
      for (let i = 0; i < 64; i += 1) {
        const s1 = (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) >>> 0;
        const ch = ((e & f) ^ (~e & g)) >>> 0;
        const temp1 = (hh + s1 + ch + k[i] + w[i]) >>> 0;
        const s0 = (rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) >>> 0;
        const maj = ((a & b) ^ (a & c) ^ (b & c)) >>> 0;
        const temp2 = (s0 + maj) >>> 0;
        hh = g; g = f; f = e; e = (d + temp1) >>> 0; d = c; c = b; b = a; a = (temp1 + temp2) >>> 0;
      }
      h[0] = (h[0] + a) >>> 0;
      h[1] = (h[1] + b) >>> 0;
      h[2] = (h[2] + c) >>> 0;
      h[3] = (h[3] + d) >>> 0;
      h[4] = (h[4] + e) >>> 0;
      h[5] = (h[5] + f) >>> 0;
      h[6] = (h[6] + g) >>> 0;
      h[7] = (h[7] + hh) >>> 0;
    }
    return h.map((word) => word.toString(16).padStart(8, "0")).join("");
  }

  function advisoryWithoutAssessmentHash(advisory) {
    const copy = JSON.parse(JSON.stringify(asObject(advisory)));
    if (copy.validation && typeof copy.validation === "object") delete copy.validation.assessment_hash;
    return copy;
  }

  function assessmentHash(advisory) {
    return `sha256:${sha256Hex(canonicalJson(advisoryWithoutAssessmentHash(advisory)))}`;
  }

  function signalEvidenceFactKey(fact) {
    return rawEnum(asObject(fact).id).trim();
  }

  function evidenceFactTopicKey(fact) {
    const view = asObject(fact);
    const topic = rawEnum(view.topic).toLowerCase();
    const source = rawEnum(view.source_group).toLowerCase();
    const label = rawEnum(view.label_cn).toLowerCase();
    const primary = [topic, source, label].join(" ");
    if (/funding|资金/.test(primary)) return "funding-rate";
    if (/macro|宏观/.test(primary)) return "macro-background";
    if (/quality|fresh|stale|missing|change|transition|迁移|时效|缺口|变化|核验/.test(topic)) return "data-quality";
    if (/structure|anchor|ggr|gamma|gex|pin|skew|srd|空间|结构|锚|墙|翻转|偏斜/.test(topic)) return "options-structure";
    if (/price_response|price_path|price_pressure|side_progress|current_price|推进|响应|路径|价格表现/.test(topic)) return "price-path";
    if (topic === "adverse_pressure") {
      if (/options|skew|srd|期权|偏斜/.test(`${source} ${label}`)) return "options-structure";
      if (/tmv/.test(source) || source === "price" || /量价/.test(label)) return "price-path";
    }
    if (/active_flow|micro_flow|flow|cvd|trade_flow|adverse_pressure|主动|成交|成交流/.test(topic)) return "active-flow";
    if (/anchor|ggr|gamma|gex|pin|skew|srd|空间|结构|锚|墙|翻转|偏斜/.test(primary)) return "options-structure";
    if (/price|tmv|path|pressure|推进|响应|路径|价格表现|侵入/.test(primary)) return "price-path";
    if (/flow|cvd|主动|成交|成交流/.test(primary)) return "active-flow";
    if (/quality|fresh|stale|missing|change|transition|迁移|时效|缺口|变化|核验/.test(primary)) return "data-quality";
    const refs = asArray(view.source_refs).join(" ").toLowerCase();
    if (/funding|资金/.test(refs)) return "funding-rate";
    if (/macro|宏观/.test(refs)) return "macro-background";
    if (/anchor|ggr|gamma|gex|pin|skew|srd/.test(refs)) return "options-structure";
    if (/tmv|price/.test(refs)) return "price-path";
    if (/flow|cvd/.test(refs)) return "active-flow";
    return "data-quality";
  }

  function signalEvidenceTopicLabel(key) {
    const labels = {
      "options-structure": "空间结构",
      "price-path": "价格表现",
      "active-flow": "主动成交",
      "funding-rate": "资金费率",
      "macro-background": "宏观背景",
      "data-quality": "变化与时效"
    };
    return labels[key] || "资料来源";
  }

  function signalEvidenceFactAnchorId(fact) {
    const ids = {
      "options-structure": "market-options-structure",
      "price-path": "market-price-path",
      "active-flow": "market-active-flow",
      "funding-rate": "market-funding-rate",
      "macro-background": "market-macro-background",
      "data-quality": "market-data-quality"
    };
    return ids[evidenceFactTopicKey(fact)] || "market-data-quality";
  }

  function signalEvidenceFactsById(facts) {
    const map = new Map();
    asArray(facts).forEach((fact) => {
      const key = signalEvidenceFactKey(fact);
      if (key) map.set(key, asObject(fact));
    });
    return map;
  }

  function signalEvidenceRefChips(refs, view, roleLabel = "来源") {
    const factMap = signalEvidenceFactsById(view.market_facts);
    const groups = new Map();
    const details = [];
    asArray(refs).forEach((ref) => {
      const key = rawEnum(ref).trim();
      const fact = factMap.get(key);
      if (!fact) return;
      const topic = evidenceFactTopicKey(fact);
      const anchor = signalEvidenceFactAnchorId(fact);
      const label = signalEvidenceTopicLabel(topic);
      const groupKey = `${anchor}|${label}`;
      if (!groups.has(groupKey)) groups.set(groupKey, { anchor, label, count: 0 });
      groups.get(groupKey).count += 1;
      details.push(signalEvidenceFactLabel(fact));
    });
    const links = Array.from(groups.values()).map((item) => {
      const count = item.count > 1 ? `（${number(item.count, 0)}项）` : "";
      const text = `${item.label}${count}`;
      return item.anchor
        ? `<a class="source-ref-link chip" href="#${escapeHtml(item.anchor)}">${escapeHtml(text)}</a>`
        : `<span class="chip">${escapeHtml(text)}</span>`;
    }).join("");
    if (!links) return "";
    const detailText = [...new Set(details)].filter(Boolean).join("；");
    const detail = detailText
      ? `<details class="source-ref-detail"><summary>查看引用明细</summary><p>${escapeHtml(detailText)}</p></details>`
      : "";
    return `<div class="source-ref-row"><span class="source-ref-label">${escapeHtml(roleLabel)}</span>${links}</div>${detail}`;
  }

  function uniqueRefList(refs) {
    return [...new Set(asArray(refs).map((ref) => rawEnum(ref).trim()).filter(Boolean))];
  }

  function normalizeEvidenceTextList(value) {
    if (Array.isArray(value)) return normalizeComfortList(value);
    const text = normalizeComfortText(value, "");
    return text ? [text] : [];
  }

  function defaultEvidenceFitThesis(sideKey) {
    return sideKey === "call_credit"
      ? "当前事实是否支持上行侵入风险受到可解释约束。"
      : "当前事实是否支持下行侵入风险受到可解释约束。";
  }

  function normalizeEvidenceRoles(value) {
    return asArray(value).map((entry) => {
      const item = asObject(entry);
      const role = rawEnum(item.role).toLowerCase();
      const ref = rawEnum(item.ref).trim();
      return {
        ref,
        role: Object.prototype.hasOwnProperty.call(SIGNAL_EVIDENCE_ROLE_LABELS, role) ? role : "",
        claim_cn: normalizeComfortText(item.claim_cn, "")
      };
    }).filter((item) => item.ref || item.claim_cn || item.role);
  }

  function evidenceRefsFromRoles(roles, role) {
    return uniqueRefList(asArray(roles)
      .filter((item) => item.role === role)
      .map((item) => item.ref));
  }

  function evidenceRoleErrors(roles, factIds) {
    const errors = [];
    asArray(roles).forEach((role) => {
      if (!role.role) errors.push("证据角色存在未识别用途。");
      if (!role.ref) errors.push("证据角色缺少事实引用。");
      if (!role.claim_cn) errors.push("证据角色缺少中文作用说明。");
      if (factIds && factIds.size && role.ref && !factIds.has(role.ref)) {
        errors.push("证据角色存在无法核验的事实引用。");
      }
    });
    return [...new Set(errors)];
  }

  function renderEvidenceRoles(side, view) {
    const roles = asArray(side.evidence_roles);
    if (!roles.length) return "";
    const factMap = signalEvidenceFactsById(view.market_facts);
    const rows = roles.map((role) => {
      const fact = factMap.get(role.ref);
      const label = fact ? signalEvidenceFactLabel(fact) : "引用事实";
      const roleText = SIGNAL_EVIDENCE_ROLE_LABELS[role.role] || "作用未明";
      const anchor = fact ? signalEvidenceFactAnchorId(fact) : "";
      const source = anchor
        ? `<a class="source-ref-link" href="#${escapeHtml(anchor)}">${escapeHtml(label)}</a>`
        : `<span class="chip">${escapeHtml(label)}</span>`;
      return `<li><span>${escapeHtml(roleText)}</span>${source}<p>${escapeHtml(role.claim_cn || "未说明具体作用。")}</p></li>`;
    }).join("");
    return `<div class="evidence-role-list"><strong>证据角色</strong><ul class="plain-list">${rows}</ul></div>`;
  }

  function normalizeEvidenceMarketSnapshot(value) {
    const view = asObject(value);
    const rawStatus = rawEnum(view.status).toUpperCase();
    const price = safeNumber(view.price);
    const unit = rawEnum(view.unit).trim();
    const observed = firstPresent(view.observed_at_ms, view.observed_at);
    const observedMs = parseSignalRatingTimeMs(observed);
    const available = rawStatus === "AVAILABLE" && price !== null && unit && observedMs !== null;
    return {
      status: available ? "AVAILABLE" : "UNAVAILABLE",
      price,
      unit,
      observed_at_ms: observedMs,
      reason_cn: normalizeComfortText(view.reason_cn || view.summary_cn, "")
    };
  }

  function marketSnapshotValueText(snapshot) {
    const view = normalizeEvidenceMarketSnapshot(snapshot);
    if (view.status !== "AVAILABLE") return "卡时价格未提供";
    return `${number(view.price, 2)} ${view.unit}`;
  }

  function marketSnapshotMetaText(snapshot) {
    const view = normalizeEvidenceMarketSnapshot(snapshot);
    if (view.status !== "AVAILABLE") return view.reason_cn || "未从发布投影取得卡片时点价格。";
    return `观察时点 ${dateText(isoFromEpochMs(view.observed_at_ms))}`;
  }

  function normalizeEvidenceSide(side, sideKey = "") {
    const view = asObject(side);
    const grade = comfortGradeKey(view.grade);
    const rawStatus = rawEnum(view.status).toUpperCase();
    const status = SIGNAL_EVIDENCE_SIDE_STATUSES.has(rawStatus)
      ? rawStatus
      : (grade ? "RATED" : "UNRATED");
    const roles = normalizeEvidenceRoles(view.evidence_roles);
    const roleSupportRefs = evidenceRefsFromRoles(roles, "supports_fit");
    const roleCounterRefs = evidenceRefsFromRoles(roles, "counters_fit");
    const roleContextRefs = evidenceRefsFromRoles(roles, "context_only");
    const supportRefs = roles.length ? roleSupportRefs : uniqueRefList(view.evidence_refs);
    const counterRefs = roles.length ? roleCounterRefs : uniqueRefList(view.counter_evidence_refs);
    const weaken = normalizeEvidenceTextList(view.weaken_if_cn);
    const legacyInvalid = normalizeEvidenceTextList(view.invalid_if_cn);
    return {
      status,
      grade,
      fit_thesis: normalizeComfortText(firstPresent(view.fit_thesis_cn, view.fit_thesis), defaultEvidenceFitThesis(sideKey)),
      mechanism_cn: normalizeComfortText(view.mechanism_cn, grade ? "适配机制尚未单列说明，请结合等级依据阅读。" : "暂未完成有效适配机制说明。"),
      basis_cn: normalizeComfortText(view.basis_cn || view.summary_cn, grade ? "未说明等级依据。" : "暂未完成有效评级。"),
      market_counter_cn: normalizeComfortText(view.market_counter_cn || view.counter_evidence_cn, "未识别到主要市场反证。"),
      alternative_cn: normalizeComfortText(view.alternative_cn || view.competing_explanation_cn, "竞争解释暂未形成。"),
      next_observation_cn: normalizeComfortText(view.next_observation_cn, "等待下一条有效观察。"),
      strengthen_if_cn: normalizeEvidenceTextList(view.strengthen_if_cn),
      weaken_if_cn: weaken,
      legacy_invalid_if_cn: weaken.length ? [] : legacyInvalid,
      invalid_if_cn: normalizeComfortText(view.invalid_if_cn, ""),
      evidence_roles: roles,
      evidence_refs: supportRefs,
      counter_evidence_refs: counterRefs,
      context_evidence_refs: roleContextRefs,
      unresolved_conditions_cn: normalizeComfortList(view.unresolved_conditions_cn),
      validation_reasons_cn: normalizeComfortList(view.validation_reasons_cn)
    };
  }

  function normalizeEvidenceActionSide(value) {
    const view = asObject(value);
    const rawState = rawEnum(view.state).toUpperCase();
    const state = SIGNAL_EVIDENCE_ACTION_STATES.has(rawState) ? rawState : "UNRATED";
    return {
      state,
      label_cn: normalizeComfortText(view.label_cn || semanticCompact(state), semanticCompact(state) || "未评级"),
      reasons_cn: normalizeComfortList(view.reasons_cn)
    };
  }

  function normalizeEvidenceActionState(object) {
    return {
      put_credit: normalizeEvidenceActionSide(firstObject(get(object, "display_action_state.put_credit"), get(object, "local_action_state.put_credit"))),
      call_credit: normalizeEvidenceActionSide(firstObject(get(object, "display_action_state.call_credit"), get(object, "local_action_state.call_credit")))
    };
  }

  function normalizeEvidencePriceBiasObject(value) {
    const bias = asObject(value);
    return {
      schema: bias.schema,
      status: rawEnum(bias.status).toUpperCase(),
      bias: rawEnum(bias.bias).toUpperCase(),
      raw_basis_cn: rawEnum(bias.basis_cn),
      raw_counter_cn: rawEnum(bias.counter_cn),
      raw_invalid_if_cn: rawEnum(bias.invalid_if_cn),
      basis_cn: normalizeComfortText(bias.basis_cn, ""),
      counter_cn: normalizeComfortText(bias.counter_cn, ""),
      invalid_if_cn: normalizeComfortText(bias.invalid_if_cn, ""),
      evidence_refs: uniqueRefList(bias.evidence_refs),
      counter_evidence_refs: uniqueRefList(bias.counter_evidence_refs),
      validation_reasons_cn: normalizeComfortList(bias.validation_reasons_cn)
    };
  }

  function normalizeEvidenceSideComparison(value) {
    const view = asObject(value);
    const rawStatus = rawEnum(view.status).toUpperCase();
    const relativeSide = rawEnum(view.relative_side).toLowerCase();
    const flip = normalizeEvidenceTextList(view.flip_if_cn);
    return {
      status: SIGNAL_EVIDENCE_COMPARISON_STATUSES.has(rawStatus) ? rawStatus : "UNAVAILABLE",
      relative_side: SIGNAL_EVIDENCE_COMPARISON_SIDES.has(relativeSide) ? relativeSide : "",
      basis_cn: normalizeComfortText(view.basis_cn, ""),
      evidence_refs: uniqueRefList(view.evidence_refs),
      flip_if_cn: flip,
      validation_reasons_cn: normalizeComfortList(view.validation_reasons_cn)
    };
  }

  function signalEvidenceSummaryIsV21(summary) {
    const view = asObject(summary);
    const schema = schemaToken(firstPresent(view.schema_version, view.schema, view.version));
    return schema === SIGNAL_EVIDENCE_SUMMARY_SCHEMA;
  }

  function signalEvidenceProjectionHash(summary) {
    const copy = JSON.parse(JSON.stringify(asObject(summary)));
    delete copy.display_projection_hash;
    return `sha256:${sha256Hex(canonicalJson(copy))}`;
  }

  function signalEvidenceSummaryProjectionErrors(summary) {
    const view = asObject(summary);
    if (!signalEvidenceSummaryIsV21(view)) return [];
    const errors = [];
    if (view.display_projection_version !== SIGNAL_EVIDENCE_DISPLAY_PROJECTION_VERSION) {
      errors.push("发布投影版本未通过当前页面校验。");
    }
    if (!view.source_record_hash) {
      errors.push("发布投影缺少源记录绑定。");
    }
    if (!view.display_projection_hash || view.display_projection_hash !== signalEvidenceProjectionHash(view)) {
      errors.push("发布投影校验未通过。");
    }
    return errors;
  }

  function normalizeEvidenceSummary(summary) {
    const object = asObject(summary);
    const displayActionSummary = normalizeComfortText(object.display_action_summary_cn, "");
    return {
      source: "summary",
      schema_version: firstPresent(object.schema_version, object.schema, object.version),
      display_projection_version: object.display_projection_version,
      source_record_hash: object.source_record_hash,
      display_projection_hash: object.display_projection_hash,
      as_of_ms: object.as_of_ms,
      input_packet_hash: object.input_packet_hash,
      assessment_hash: object.assessment_hash,
      action_summary_cn: displayActionSummary || normalizeComfortText(object.action_summary_cn, ""),
      market_snapshot: normalizeEvidenceMarketSnapshot(object.market_snapshot),
      price_bias: normalizeEvidencePriceBiasObject(firstObject(object.price_bias, object.price_bias_summary)),
      side_comparison: normalizeEvidenceSideComparison(object.side_comparison),
      put_credit: normalizeEvidenceSide(object.put_credit, "put_credit"),
      call_credit: normalizeEvidenceSide(object.call_credit, "call_credit"),
      local_action_state: normalizeEvidenceActionState(object),
      market_facts: [],
      quote_boundary_cn: "",
      validation: {}
    };
  }

  function normalizeEvidenceView(advisory, summary, doc) {
    const object = asObject(advisory);
    const ratings = asObject(object.side_evidence_ratings);
    const validation = asObject(object.validation);
    const facts = asArray(object.market_facts);
    const summaryView = normalizeEvidenceSummary(summary);
    const displayActionSummary = normalizeComfortText(object.display_action_summary_cn, "");
    const view = {
      source: "full",
      schema_version: firstPresent(object.schema_version, get(doc, "llm_review.schema_version"), SIGNAL_EVIDENCE_REVIEW_SCHEMA),
      prompt_version: firstPresent(object.prompt_version, get(doc, "llm_review.prompt_version")),
      as_of_ms: firstPresent(summaryView.as_of_ms, get(doc, "llm_review.reviewed_at"), confirmedAt(doc)),
      input_packet_hash: firstPresent(summaryView.input_packet_hash, get(doc, "llm_review.input_packet_hash")),
      assessment_hash: firstPresent(summaryView.assessment_hash, validation.assessment_hash),
      action_summary_cn: displayActionSummary || summaryView.action_summary_cn || normalizeComfortText(object.action_summary_cn, ""),
      market_snapshot: normalizeEvidenceMarketSnapshot(firstObject(asObject(summary).market_snapshot, object.market_snapshot)),
      price_bias: normalizeEvidencePriceBiasObject(firstObject(object.price_bias, summaryView.price_bias)),
      side_comparison: normalizeEvidenceSideComparison(firstObject(object.side_comparison, summaryView.side_comparison)),
      put_credit: normalizeEvidenceSide(ratings.put_credit, "put_credit"),
      call_credit: normalizeEvidenceSide(ratings.call_credit, "call_credit"),
      local_action_state: normalizeEvidenceActionState({
        display_action_state: object.display_action_state,
        local_action_state: firstObject(object.local_action_state, summaryView.local_action_state)
      }),
      market_facts: facts,
      quote_boundary_cn: normalizeComfortText(object.quote_boundary_cn, ""),
      validation
    };
    return view;
  }

  function signalEvidenceSideErrors(side, label, factIds) {
    const errors = [];
    if (side.status === "RATED" && !side.grade) errors.push(`${label}声明已评级但缺少等级。`);
    if (side.status === "UNRATED" && side.grade) errors.push(`${label}声明未评级但带有等级。`);
    if (side.status === "RATED" && !side.basis_cn) errors.push(`${label}缺少等级依据。`);
    const refs = [...asArray(side.evidence_refs), ...asArray(side.counter_evidence_refs)]
      .map((ref) => rawEnum(ref).trim())
      .filter(Boolean);
    if (factIds.size && refs.some((ref) => !factIds.has(ref))) errors.push(`${label}存在无法核验的事实引用。`);
    errors.push(...evidenceRoleErrors(side.evidence_roles, factIds).map((error) => `${label}${error}`));
    return [...new Set(errors)];
  }

  function isolateSignalEvidenceSide(side, errors) {
    if (!errors.length) return side;
    return {
      ...side,
      status: "UNRATED",
      grade: null,
      validation_reasons_cn: [...side.validation_reasons_cn, ...errors]
    };
  }

  function sideComparisonLabel(value) {
    const labels = {
      put_credit: "Put 侧相对更有依据",
      call_credit: "Call 侧相对更有依据",
      tie: "两侧接近，暂无单一优先侧",
      not_comparable: "当前暂不可比较"
    };
    return labels[rawEnum(value).toLowerCase()] || "相对比较未采纳";
  }

  function signalEvidenceComparisonErrors(view, factIds) {
    const comparison = asObject(view.side_comparison);
    const errors = [];
    if (comparison.status !== "ASSESSED") return errors;
    if (!comparison.relative_side || !SIGNAL_EVIDENCE_COMPARISON_SIDES.has(comparison.relative_side)) {
      errors.push("比较侧别未通过校验。");
    }
    if (!comparison.basis_cn) errors.push("比较缺少中文理由。");
    if ((comparison.relative_side === "put_credit" || comparison.relative_side === "call_credit")) {
      const put = asObject(view.put_credit);
      const call = asObject(view.call_credit);
      if (put.status !== "RATED" || !put.grade || call.status !== "RATED" || !call.grade) {
        errors.push("有一侧未形成有效等级，不能宣布另一侧相对胜出。");
      } else if (comparison.relative_side === "put_credit" && comfortGradeRank(put.grade) < comfortGradeRank(call.grade)) {
        errors.push("比较侧别与两侧等级顺序矛盾。");
      } else if (comparison.relative_side === "call_credit" && comfortGradeRank(call.grade) < comfortGradeRank(put.grade)) {
        errors.push("比较侧别与两侧等级顺序矛盾。");
      }
    }
    if (comparison.relative_side === "tie") {
      const put = asObject(view.put_credit);
      const call = asObject(view.call_credit);
      if (put.status === "RATED" && call.status === "RATED" && put.grade && call.grade && put.grade !== call.grade) {
        errors.push("两侧等级不同，不能显示两侧接近。");
      }
    }
    const refs = asArray(comparison.evidence_refs).map((ref) => rawEnum(ref).trim()).filter(Boolean);
    if (factIds && factIds.size && refs.some((ref) => !factIds.has(ref))) {
      errors.push("比较存在无法核验的事实引用。");
    }
    return [...new Set(errors)];
  }

  function applySignalEvidenceComparisonValidation(view, factIds) {
    const comparison = normalizeEvidenceSideComparison(view.side_comparison);
    const hasComparison = Object.keys(asObject(view.side_comparison)).length > 0
      && asObject(view.side_comparison).status !== "UNAVAILABLE";
    const errors = signalEvidenceComparisonErrors({ ...view, side_comparison: comparison }, factIds);
    if (!hasComparison && comparison.status !== "ASSESSED") {
      comparison.validation_reasons_cn = comparison.validation_reasons_cn.length
        ? comparison.validation_reasons_cn
        : ["旧版未提供两侧比较。"];
      view.side_comparison = comparison;
      return;
    }
    if (errors.length) {
      view.side_comparison = {
        ...comparison,
        status: "UNAVAILABLE",
        validation_reasons_cn: [...comparison.validation_reasons_cn, ...errors]
      };
      return;
    }
    view.side_comparison = comparison;
  }

  function signalEvidenceSummaryAlignmentErrors(fullView, summaryView) {
    const errors = [];
    if (!summaryView || !isSignalEvidenceSummaryObject(summaryView)) {
      errors.push("发布摘要缺失或版本不匹配。");
      return errors;
    }
    errors.push(...signalEvidenceSummaryProjectionErrors(summaryView));
    if (!summaryView.assessment_hash) errors.push("发布摘要缺少结果校验标记。");
    if (summaryView.assessment_hash && fullView.assessment_hash && summaryView.assessment_hash !== fullView.assessment_hash) {
      errors.push("发布摘要与详情结果不一致。");
    }
    ["put_credit", "call_credit"].forEach((key) => {
      const fullSide = normalizeEvidenceSide(get(fullView, key));
      const summarySide = normalizeEvidenceSide(get(summaryView, key));
      if (fullSide.grade !== summarySide.grade || fullSide.status !== summarySide.status) {
        errors.push(`${key === "put_credit" ? "Put" : "Call"} 侧摘要与详情不一致。`);
      }
    });
    const summaryActionText = normalizeComfortText(summaryView.display_action_summary_cn || summaryView.action_summary_cn, "");
    if (summaryActionText && fullView.action_summary_cn !== summaryActionText) {
      errors.push("行动摘要与详情不一致。");
    }
    const summaryComparison = normalizeEvidenceSideComparison(get(summaryView, "side_comparison"));
    const fullComparison = normalizeEvidenceSideComparison(get(fullView, "side_comparison"));
    if (summaryComparison.status !== fullComparison.status
      || summaryComparison.relative_side !== fullComparison.relative_side) {
      errors.push("两侧比较摘要与详情不一致。");
    }
    return [...new Set(errors)];
  }

  function signalEvidenceState(doc) {
    const summary = signalEvidenceSummaryCandidate(doc);
    const summaryIsV2 = isSignalEvidenceSummaryObject(summary);
    const summaryView = summaryIsV2 ? normalizeEvidenceSummary(summary) : null;
    const advisory = rawSignalEvidenceAdvisoryCandidate(doc);
    const hasFull = Boolean(Object.keys(asObject(advisory.side_evidence_ratings)).length);
    if (hasFull) {
      const review = asObject(get(doc, "llm_review", {}));
      const view = normalizeEvidenceView(advisory, summary, doc);
      const errors = [];
      if (Object.keys(review).length && !isSignalEvidenceReviewObject(review)) errors.push("复核协议不是当前 v2。");
      if (!view.action_summary_cn) errors.push("行动摘要缺失。");
      if (!Array.isArray(advisory.market_facts)) errors.push("市场事实包缺失。");
      if (!signalRatingAsOfIsValid(parseSignalRatingTimeMs(view.as_of_ms))) errors.push("评级时点暂不可核验。");
      const computedHash = assessmentHash(advisory);
      if (!view.assessment_hash || computedHash !== view.assessment_hash) errors.push("详情结果校验未通过。");
      errors.push(...signalEvidenceSummaryAlignmentErrors(view, summary));
      const factIds = new Set(asArray(view.market_facts).map(signalEvidenceFactKey).filter(Boolean));
      view.put_credit = isolateSignalEvidenceSide(view.put_credit, signalEvidenceSideErrors(view.put_credit, "Put侧", factIds));
      view.call_credit = isolateSignalEvidenceSide(view.call_credit, signalEvidenceSideErrors(view.call_credit, "Call侧", factIds));
      applySignalEvidenceComparisonValidation(view, factIds);
      if (errors.length) return { state: "invalid", view, errors };
      return { state: "full", view, errors: [] };
    }
    if (summaryIsV2) {
      const errors = signalEvidenceSummaryProjectionErrors(summary);
      if (!summary.assessment_hash) errors.push("发布摘要缺少结果校验标记。");
      if (!signalRatingAsOfIsValid(parseSignalRatingTimeMs(summary.as_of_ms))) errors.push("评级时点暂不可核验。");
      const factIds = new Set();
      summaryView.put_credit = isolateSignalEvidenceSide(summaryView.put_credit, signalEvidenceSideErrors(summaryView.put_credit, "Put侧", factIds));
      summaryView.call_credit = isolateSignalEvidenceSide(summaryView.call_credit, signalEvidenceSideErrors(summaryView.call_credit, "Call侧", factIds));
      applySignalEvidenceComparisonValidation(summaryView, factIds);
      if (errors.length) return { state: "invalid", view: summaryView, errors };
      return { state: "summary", view: summaryView, errors: [] };
    }
    if (isSignalEvidenceReviewObject(get(doc, "llm_review", {}))) {
      return { state: "invalid", view: null, errors: ["v2 复核未形成可用结果。"] };
    }
    return { state: "missing", view: null, errors: [] };
  }

  function signalEvidenceHasUsableView(stateView) {
    return stateView && stateView.view && stateView.state !== "invalid";
  }

  function signalEvidenceAsOfMetricText(doc) {
    const stateView = signalEvidenceState(doc);
    if (stateView.state === "invalid") return "待完成";
    if (!stateView.view || stateView.state === "missing") return "未评级";
    return signalRatingAsOfText(stateView.view);
  }

  function signalEvidenceIndexStats(doc) {
    const stateView = signalEvidenceState(doc);
    if (stateView.state === "invalid") return ["暂未完成有效评级", `质量 ${semanticCompact(qualityOverall(doc))}`];
    if (!signalEvidenceHasUsableView(stateView)) return null;
    const view = stateView.view;
    const putAction = get(view, "local_action_state.put_credit.label_cn");
    const callAction = get(view, "local_action_state.call_credit.label_cn");
    const comparison = signalEvidenceComparisonIndexText(view);
    return [
      `价格 ${marketSnapshotValueText(view.market_snapshot)}`,
      signalEvidencePriceBiasIndexText(view),
      `Put ${comfortGradeText(view.put_credit.grade)}`,
      `Call ${comfortGradeText(view.call_credit.grade)}`,
      comparison,
      textClip(view.action_summary_cn || [putAction, callAction].filter(Boolean).join(" / ") || "行动状态已记录", 44)
    ].filter(Boolean);
  }

  function signalEvidenceFilterMatches(doc) {
    if (!state.grade) return true;
    const stateView = signalEvidenceState(doc);
    if (!signalEvidenceHasUsableView(stateView)) return false;
    const grades = [stateView.view.put_credit.grade, stateView.view.call_credit.grade];
    const bestRank = Math.max(...grades.map(comfortGradeRank));
    if (state.grade === "attention") return bestRank >= comfortGradeRank("B");
    if (state.grade === "admission") return bestRank >= comfortGradeRank("A");
    return true;
  }

  function signalEvidenceSearchText(doc) {
    const stateView = signalEvidenceState(doc);
    if (stateView.state === "invalid") return "暂未完成有效评级";
    if (!signalEvidenceHasUsableView(stateView)) return "";
    const view = stateView.view;
    return [
      comfortGradeText(view.put_credit.grade),
      comfortGradeText(view.call_credit.grade),
      marketSnapshotValueText(view.market_snapshot),
      signalEvidencePriceBiasIndexText(view),
      signalEvidenceComparisonIndexText(view),
      view.action_summary_cn,
      view.put_credit.basis_cn,
      view.call_credit.basis_cn,
      view.put_credit.mechanism_cn,
      view.call_credit.mechanism_cn,
      view.put_credit.market_counter_cn,
      view.call_credit.market_counter_cn,
      view.put_credit.alternative_cn,
      view.call_credit.alternative_cn,
      get(view, "local_action_state.put_credit.label_cn"),
      get(view, "local_action_state.call_credit.label_cn")
    ].join(" ");
  }

  function actionStateClass(value) {
    const stateValue = rawEnum(value).toUpperCase();
    if (stateValue === "PREPARE") return "is-good";
    if (stateValue === "AVOID" || stateValue === "BLOCKED") return "is-bad";
    if (stateValue === "WATCH" || stateValue === "WAIT") return "is-wait";
    return "";
  }

  function signalEvidenceActionPanelClass(view) {
    const states = SIGNAL_COMFORT_SIDES
      .map(({ key }) => rawEnum(get(view, `local_action_state.${key}.state`)).toUpperCase())
      .filter(Boolean);
    if (states.includes("PREPARE")) return "is-grade-a";
    if (states.some((state) => state === "AVOID" || state === "BLOCKED" || state === "BLOCK")) return "is-grade-d";
    if (states.some((state) => state === "WATCH" || state === "WAIT")) return "is-grade-b";
    return "is-unavailable";
  }

  function renderEvidenceSideAction(action) {
    const view = asObject(action);
    const reasons = normalizeComfortList(view.reasons_cn)
      .map((item) => signalEvidenceReaderText(item, ""))
      .filter(Boolean);
    return `
      <div class="evidence-side-action ${actionStateClass(view.state)}">
        <strong>行动状态：${escapeHtml(signalEvidenceReaderText(view.label_cn, "未评级"))}</strong>
        ${reasons.length
          ? reasons.map((item) => `<p>${escapeHtml(item)}</p>`).join("")
          : `<p>本地边界未提供额外限制说明。</p>`}
      </div>
    `;
  }

  function signalEvidencePriceBiasLabels() {
    return { BULLISH: "偏多", BEARISH: "偏空", NEUTRAL: "中性", MIXED: "多空分歧", UNDETERMINED: "方向依据不足" };
  }

  function signalEvidencePriceBiasResult(view, options = {}) {
    const raw = asObject(view.price_bias);
    const labels = signalEvidencePriceBiasLabels();
    const refs = [...asArray(raw.evidence_refs), ...asArray(raw.counter_evidence_refs)];
    const factMap = signalEvidenceFactsById(view.market_facts);
    const asOf = parseSignalRatingTimeMs(view.as_of_ms);
    const validRefs = options.relaxed === true
      || ((refs.length > 0 || raw.bias === "UNDETERMINED") && refs.every((ref) => {
        const fact = factMap.get(ref);
        return typeof ref === "string" && fact && fact.usable === true
          && Number.isFinite(fact.observed_at_ms) && fact.observed_at_ms > 0
          && fact.observed_at_ms <= asOf;
      }));
    const rawText = [raw.raw_basis_cn, raw.raw_counter_cn, raw.raw_invalid_if_cn].join(" ");
    const textFields = [raw.basis_cn, raw.counter_cn, raw.invalid_if_cn];
    const valid = raw.schema === "price_bias@1.0.0" && raw.status === "ASSESSED"
      && Object.prototype.hasOwnProperty.call(labels, raw.bias)
      && Array.isArray(raw.evidence_refs) && Array.isArray(raw.counter_evidence_refs)
      && Array.isArray(raw.validation_reasons_cn) && raw.validation_reasons_cn.length === 0
      && (options.relaxed === true || textFields.every((field) => typeof field === "string" && field.trim()))
      && !/(?:[a-z][a-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+|[A-Z]{2,}(?:_[A-Z0-9]+)+)/.test(rawText)
      && validRefs;
    const missing = !Object.keys(raw).length || !raw.schema;
    return {
      valid,
      missing,
      label: valid ? labels[raw.bias] : "暂未形成方向结论",
      note: missing
        ? "这张历史卡未单列 LLM 价格倾向，保留原有两侧论证。"
        : "价格倾向尚未通过有效核验；两侧评级与已核验事实仍可分别阅读。",
      bias: raw
    };
  }

  function signalEvidencePriceBiasIndexText(view) {
    const result = signalEvidencePriceBiasResult(view, { relaxed: view.source === "summary" });
    return result.valid ? `倾向 ${result.label}` : "倾向未提供";
  }

  function signalEvidenceComparisonDisplay(view) {
    const comparison = asObject(view.side_comparison);
    const reasons = normalizeComfortList(comparison.validation_reasons_cn);
    if (comparison.status === "ASSESSED") {
      return {
        adopted: true,
        label: sideComparisonLabel(comparison.relative_side),
        detail: comparison.basis_cn || "本次比较未单列理由。",
        refs: comparison.evidence_refs,
        flip: comparison.flip_if_cn
      };
    }
    const text = reasons.join("；") || "旧版未提供两侧比较。";
    return {
      adopted: false,
      label: text.includes("旧版未提供") ? "旧版未提供两侧比较" : "相对比较未采纳",
      detail: text,
      refs: [],
      flip: []
    };
  }

  function signalEvidenceComparisonIndexText(view) {
    const comparison = signalEvidenceComparisonDisplay(view);
    return comparison.adopted ? comparison.label : "";
  }

  function renderSignalEvidenceDecisionMeta(view) {
    const market = normalizeEvidenceMarketSnapshot(view.market_snapshot);
    const bias = signalEvidencePriceBiasResult(view, { relaxed: view.source === "summary" });
    const comparison = signalEvidenceComparisonDisplay(view);
    const pressureFacts = [["pressure.tmv.direction", "量价倾向"], ["pressure.cvd.combined_direction", "主动流倾向"]];
    const pressureHeadline = pressureFacts.map(([id, label]) => {
      const fact = signalEvidenceUsableFact(view, id);
      return fact ? `${label}：${signalEvidenceDisplayValue(fact)}` : "";
    }).filter(Boolean).join("；") || "量价与主动流摘要待完整卡加载。";
    const comparisonBody = comparison.adopted
      ? comparison.detail
      : (comparison.label === "旧版未提供两侧比较" ? comparison.detail : `相对比较未采纳：${comparison.detail}`);
    return `
      <div class="evidence-decision-meta" aria-label="本卡市场摘要">
        <div><span>卡时价格</span><strong>${escapeHtml(marketSnapshotValueText(market))}</strong><p>${escapeHtml(marketSnapshotMetaText(market))}</p></div>
        <div><span>LLM 价格倾向</span><strong>${escapeHtml(bias.label)}</strong><p>${escapeHtml(bias.valid ? "价格倾向与价差适配等级分开阅读。" : bias.note)}</p></div>
        <div><span>两侧比较</span><strong>${escapeHtml(comparison.label)}</strong><p>${escapeHtml(comparisonBody)}</p></div>
        <div><span>压力摘要</span><strong>${escapeHtml(pressureHeadline)}</strong><p>量价与主动流只作为依据，不直接替代价差评级。</p></div>
      </div>
    `;
  }

  function evidenceSideCard(side, label, view, sideKey) {
    const validation = asArray(side.validation_reasons_cn);
    const refs = signalEvidenceRefChips(side.evidence_refs, view, "支持来源");
    const counterRefs = signalEvidenceRefChips(side.counter_evidence_refs, view, "反对来源");
    const contextRefs = signalEvidenceRefChips(side.context_evidence_refs, view, "背景来源");
    const action = renderEvidenceSideAction(get(view, `local_action_state.${sideKey}`));
    return `
      <article class="comfort-side-card ${comfortGradeClass(side.grade)} evidence-report-side">
        <div class="comfort-side-head">
          <strong>${escapeHtml(label)}</strong>
          <span class="review-side-caption">${side.status === "UNRATED" || !side.grade ? "评审尚未采纳" : "适配论证"}</span>
        </div>
        ${side.status === "UNRATED" || !side.grade
          ? `<p><strong>未采纳的评审说明</strong>：${escapeHtml(side.basis_cn || "暂未完成有效评级。")}</p>`
          : `<p class="review-thesis-reference">${side.grade === "D" ? "回避依据" : "支持解释"}见上方<a href="#signal-comfort">本侧评级判断</a>。</p>`}
        <p><strong>评审命题</strong>：${escapeHtml(side.fit_thesis)}</p>
        <p><strong>适配机制</strong>：${escapeHtml(side.mechanism_cn)}</p>
        ${renderEvidenceRoles(side, view)}
        ${refs}
        ${contextRefs}
        <p><strong>主要反证</strong>：${escapeHtml(side.market_counter_cn || "未识别到主要市场反证。")}</p>
        <p><strong>竞争解释</strong>：${escapeHtml(side.alternative_cn)}</p>
        ${counterRefs}
        ${action}
        ${validation.length ? `<p><strong>评级缺口</strong>：${escapeHtml(validation.join("；"))}</p>` : ""}
      </article>
    `;
  }

  function signalEvidenceUsableFact(view, id) {
    const fact = signalEvidenceFactsById(view.market_facts).get(id);
    return fact && fact.usable === true ? fact : null;
  }

  function signalEvidenceSpatialReading(view) {
    const fact = (id) => signalEvidenceUsableFact(view, id);
    const numeric = (id) => {
      const item = fact(id);
      return item && typeof item.value === "number" && Number.isFinite(item.value) ? item.value : null;
    };
    const gamma = numeric("structure.gex.net_gamma_notional_usd");
    const regime = rawEnum(asObject(fact("structure.gamma.regime")).value).toUpperCase();
    const regimePositive = /POSITIVE|LONG_GAMMA|正\s*GAMMA/.test(regime);
    const regimeNegative = /NEGATIVE|SHORT_GAMMA|负\s*GAMMA/.test(regime);
    const transition = /TRANSITION|过渡/.test(regime);
    const conflict = gamma !== null && ((gamma > 0 && regimeNegative) || (gamma < 0 && regimePositive));
    let feedback = "波动反馈待确认";
    let mechanism = "缺少可用的净 Gamma 或明确体制依据，暂不能区分波动抑制与放大。";
    if (conflict) {
      feedback = "Gamma 反馈存在分歧";
      mechanism = "净 Gamma 符号与体制判断相反，抑制和放大两种解释并存；先核对来源及时间，不能选取有利的一项作为结论。";
    } else if (transition) {
      feedback = "过渡区：波动反馈未定";
      mechanism = gamma !== null && gamma !== 0
        ? `净 Gamma ${gamma > 0 ? "为正，提供缓冲背景" : "为负，提示放大风险"}；过渡体制尚未确认抑制或放大。`
        : "当前体制处于过渡区，平抑与放大尚未分明；需观察翻转位置附近的价格响应。";
    } else if ((regimePositive || regimeNegative) && gamma !== null && gamma !== 0) {
      feedback = regimePositive ? "波动反馈偏抑制" : "波动反馈偏放大";
      mechanism = regimePositive
        ? "按当前净 Gamma 的符号及模型对冲假设，逆向对冲可能缓冲价格偏移；它提供区间收敛的解释，但不等于墙位已经守住。"
        : "按当前净 Gamma 的符号及模型对冲假设，顺向对冲可能放大价格偏移；触及边界后的延伸风险比单看墙位更重要。";
    } else if (regimePositive || regimeNegative) {
      feedback = regimePositive ? "体制偏向抑制波动" : "体制偏向放大波动";
      mechanism = "现有体制支持这一反馈方向，但缺少可用净 Gamma 名义规模，不能进一步判断程度。";
    } else if (gamma !== null) {
      mechanism = "已记录净 Gamma 规模，但缺少明确的体制判断；仅凭名义净值不能确认平抑或放大。";
    }
    const call = numeric("structure.distance.call_wall_pct");
    const put = numeric("structure.distance.put_wall_pct");
    const price = numeric("market.price.current");
    const callWall = numeric("structure.gamma.call_wall");
    const putWall = numeric("structure.gamma.put_wall");
    let position = "两侧距离待核对";
    let geometry = "缺少现价或两侧有效墙位，暂不比较哪侧更先面临边界检验。";
    if (price !== null && callWall !== null && putWall !== null && putWall < callWall) {
      if (price > callWall) {
        position = "现价已越过 Call 墙";
        geometry = "现价已在上方 Call 墙之外，不能再用“双墙内”解释区间约束；需观察越界后的价格响应。";
      } else if (price < putWall) {
        position = "现价已越过 Put 墙";
        geometry = "现价已在下方 Put 墙之外，不能再用“双墙内”解释区间约束；需观察越界后的价格响应。";
      } else if (call !== null && put !== null && call >= 0 && put >= 0) {
        position = call === put ? "现价距两墙相当" : call < put ? "现价更靠近 Call 墙" : "现价更靠近 Put 墙";
        geometry = `距上方 Call 墙约 ${number(call, 2)}%，距下方 Put 墙约 ${number(put, 2)}%。${call === put ? "两侧触及距离相当。" : call < put ? "向上触及边界所需的价格移动更小，下方空间更宽。" : "向下触及边界所需的价格移动更小，上方空间更宽。"}距离比较不代表墙的承接强度。`;
      }
    }
    const response = fact("response.flow_price.relation");
    const responseText = response ? signalEvidenceReaderText(response.summary_cn, "") : "";
    const tmv = fact("pressure.tmv.direction");
    const direction = tmv ? signalEvidenceDisplayValue(tmv) : "";
    const progress = [fact("side.put.adverse_progress"), fact("side.call.adverse_progress")]
      .filter(Boolean).map((item) => `${item.id.includes(".put.") ? "Put" : "Call"} 侧：${signalEvidenceDisplayValue(item)}`).join("；");
    const pressure = [direction ? `量价主干${direction}。` : "", responseText, progress ? `本观察窗内，${progress}。` : ""].filter(Boolean).join(" ")
      || "当前缺少可配对的压力与价格响应，尚不能判断边界是否正在受到检验。";
    const magnitude = gamma !== null ? `${gamma > 0 ? "+" : gamma < 0 ? "−" : ""}$${number(Math.abs(gamma) / 1e6, 2)}M` : "未提供";
    const strength = transition
      ? "体制过渡，约束强度尚未明确。"
      : "缺少同口径强弱对照，不能仅凭净规模判断约束强度。";
    return { feedback, mechanism, position, geometry, pressure, magnitude, strength };
  }

  function renderSignalEvidenceSpatialDynamics(doc) {
    const stateView = signalEvidenceState(doc);
    if (!stateView.view || !asArray(stateView.view.market_facts).length) return "";
    if (stateView.state === "invalid") return section("空间约束动力学", "", `<p class="empty-inline">资料核验未通过，暂不形成空间解释；下方仍保留已归档事实。</p>`, "signal-spatial-dynamics");
    const reading = signalEvidenceSpatialReading(stateView.view);
    return section("空间约束动力学", "边界在哪里，波动如何反馈，压力有没有穿透。", `
      <div class="llm-gamma-lens evidence-spatial-report">
        <div class="llm-review-topline"><span class="badge">${escapeHtml(reading.feedback)}</span><span class="badge">${escapeHtml(reading.position)}</span></div>
        <div class="llm-gamma-copy">
          <p><strong>空间分布</strong> ${escapeHtml(reading.geometry)}</p>
          <p><strong>波动机制</strong> ${escapeHtml(reading.mechanism)}</p>
          <p><strong>压力检验</strong> ${escapeHtml(reading.pressure)}</p>
        </div>
        <p class="evidence-spatial-scale"><strong>净 Gamma ${escapeHtml(reading.magnitude)}</strong> · ${escapeHtml(reading.strength)}</p>
        <div class="source-ref-row"><a class="source-ref-link" href="#market-options-structure">核对空间位置与 Gamma</a><a class="source-ref-link" href="#market-price-path">核对压力与价格响应</a></div>
      </div>`, "signal-spatial-dynamics");
  }

  function renderEvidenceNextSide(view, sideKey, label) {
    const side = asObject(view[sideKey]);
    const scope = side.status === "UNRATED" ? "（原评审观察，尚未采纳）" : "";
    const strengthen = asArray(side.strengthen_if_cn);
    const weaken = asArray(side.weaken_if_cn);
    const legacyInvalid = asArray(side.legacy_invalid_if_cn);
    const currentConditionBlock = strengthen.length || weaken.length
      ? `<h4>增强该侧适配</h4>${listHtml(strengthen, "尚未声明增强条件。")}<h4>削弱该侧适配</h4>${listHtml(weaken, "尚未声明削弱条件。")}`
      : "";
    const legacyConditionBlock = !currentConditionBlock && legacyInvalid.length
      ? `<h4>旧版失效条件（评审对象未区分）</h4>${listHtml(legacyInvalid, "旧版未声明失效条件。")}`
      : "";
    const fallbackBlock = !currentConditionBlock && !legacyConditionBlock
      ? `<h4>增强该侧适配</h4>${listHtml([], "尚未声明增强条件。")}<h4>削弱该侧适配</h4>${listHtml([], "尚未声明削弱条件。")}`
      : "";
    return `
      <div class="evidence-next-side">
        <h3>${escapeHtml(label)}${escapeHtml(scope)}</h3>
        <h4>继续观察</h4>
        <p>${escapeHtml(signalEvidenceReaderText(side.next_observation_cn, "等待下一条有效观察。"))}</p>
        ${currentConditionBlock || legacyConditionBlock || fallbackBlock}
      </div>
    `;
  }

function renderSignalEvidenceDecision(doc) {
    const stateView = signalEvidenceState(doc);
    if (stateView.state === "invalid") return section("最高辅助交易决策", "合并本卡评级与行动判断；证据字母不代表交易胜率。", `
      <div class="comfort-panel evidence-report-panel is-unavailable"><div class="comfort-headline"><span>本卡行动结论</span><strong>暂未完成有效评级</strong><p>${escapeHtml(asArray(stateView.errors).join("；") || "本卡复核暂不能用于当前行动结论。")}</p></div></div>`, "signal-comfort");
    if (!signalEvidenceHasUsableView(stateView)) return "";
    const view = stateView.view;
    const quoteBoundary = view.quote_boundary_cn || "候选两腿、报价、费用、净补偿与退出条件仍在交易准备环节确认。";
    const sideSummary = (key, label) => {
      const side = asObject(view[key]);
      const action = asObject(get(view, `local_action_state.${key}`));
      const unavailable = side.status === "UNRATED" || !side.grade;
      return `<div class="evidence-decision-side">
        <span>${escapeHtml(label)}</span>
        <div class="evidence-decision-grade"><strong class="badge ${comfortGradeClass(side.grade)} evidence-grade">${escapeHtml(comfortGradeText(side.grade))}</strong><span class="badge ${unavailable ? "" : actionStateClass(action.state)}">${escapeHtml(unavailable ? "暂未评级" : signalEvidenceReaderText(action.label_cn, "待复核"))}</span></div>
        ${!unavailable ? `<p class="evidence-side-thesis"><strong>适配机制：</strong>${escapeHtml(side.mechanism_cn)}</p><p><strong>等级依据：</strong>${escapeHtml(side.basis_cn)}</p>` : ""}
        <p>${escapeHtml(unavailable ? "当前证据未形成有效等级，详见独立复核中的缺口。" : asArray(action.reasons_cn).map((item) => signalEvidenceReaderText(item, "")).filter(Boolean).join("；") || "按本卡有效证据与行动状态继续核对。")}</p>
      </div>`;
    };
    return section("最高辅助交易决策", "本卡结论、两侧评级与市场倾向。", `
      <div class="comfort-panel evidence-report-panel ${signalEvidenceActionPanelClass(view)}">
        <div class="comfort-headline"><span>本卡行动结论</span><strong>${escapeHtml(view.action_summary_cn || "本卡已形成分侧证据评级。")}</strong></div>
        ${renderSignalEvidenceDecisionMeta(view)}
        <div class="evidence-decision-grid">${sideSummary("put_credit", "Put 信用价差")}${sideSummary("call_credit", "Call 信用价差")}</div>
        <p class="evidence-decision-footnote">评级时点：${escapeHtml(signalRatingAsOfText(view))}。${escapeHtml(quoteBoundary)}</p>
      </div>`, "signal-comfort");
  }

  function renderSignalEvidencePriceBias(view) {
    const result = signalEvidencePriceBiasResult(view);
    const bias = result.bias;
    if (!result.valid) {
      return `<div class="evidence-price-bias"><div class="evidence-price-bias-head"><span>LLM 价格倾向</span><strong>暂未形成方向结论</strong></div><p>${escapeHtml(result.note)}</p></div>`;
    }
    return `<div class="evidence-price-bias">
      <div class="evidence-price-bias-head"><span>LLM 价格倾向</span><strong>${escapeHtml(result.label)}</strong></div>
      <p>${escapeHtml(signalEvidenceReaderText(bias.basis_cn, ""))}</p>
      <p><strong>主要反证：</strong>${escapeHtml(signalEvidenceReaderText(bias.counter_cn, ""))}</p>
      <p><strong>改变判断：</strong>${escapeHtml(signalEvidenceReaderText(bias.invalid_if_cn, ""))}</p>
      ${signalEvidenceRefChips(bias.evidence_refs, view, "方向依据")}
      ${signalEvidenceRefChips(bias.counter_evidence_refs, view, "反证来源")}
      <p class="market-fact-meta">对应本卡时点 ${escapeHtml(signalRatingAsOfText(view))}；价格倾向与价差适配等级分别判断。</p>
    </div>`;
  }

  function renderSignalEvidenceLlmReview(doc) {
    const stateView = signalEvidenceState(doc);
    if (!signalEvidenceHasUsableView(stateView) || stateView.state !== "full") return "";
    const view = stateView.view;
    return section("LLM 独立复核意见", "保留同一次综合评审的论证与异议；按原始事实核对，不替代最高层行动边界。", `
      <div class="evidence-review-panel">
        ${renderSignalEvidencePriceBias(view)}
        <div class="comfort-side-grid evidence-report-side-grid">${evidenceSideCard(view.put_credit, "Put 信用价差", view, "put_credit")}${evidenceSideCard(view.call_credit, "Call 信用价差", view, "call_credit")}</div>
      </div>`, "signal-llm-review");
  }

  function renderSignalEvidenceNextConditions(doc) {
    const stateView = signalEvidenceState(doc);
    if (!signalEvidenceHasUsableView(stateView)) return "";
    const view = stateView.view;
    return section("下一观察条件", "这些条件用于判断本卡是否增强、维持或失效。", `
      <div class="evidence-next-grid">
        ${renderEvidenceNextSide(view, "put_credit", "Put 信用价差")}
        ${renderEvidenceNextSide(view, "call_credit", "Call 信用价差")}
      </div>
    `, "signal-next-conditions");
  }

  function signalEvidenceDisplayValue(fact) {
    const view = asObject(fact);
    const value = view.value;
    if (isNullish(value) || value === "") return "";
    if (Array.isArray(value) || typeof value === "object") return "";
    const unitRaw = rawEnum(view.unit).trim().toLowerCase();
    if (typeof value === "number" && unitRaw === "decimal" && signalEvidenceFactIsFunding(view)) {
      return fundingDecimalText(value);
    }
    const base = typeof value === "number" ? number(value, 4) : signalEvidenceReaderText(semanticCompact(value), String(value));
    const unit = signalEvidenceUnitText(view.unit);
    return [base, unit].filter(Boolean).join(" ");
  }

  function signalEvidenceReaderText(value, fallback = "未说明") {
    const text = normalizeComfortText(value, fallback);
    return text
      .replace(/记录提供来源年龄，但\s*v2\s*不自行创造新的新鲜度阈值。?/gi, "已记录来源年龄；是否足够新鲜按原始采集口径复核。")
      .replace(/只记录显式带宽；不采用旧代码的默认\s*0\.4%\s*带宽。?/g, "仅记录本卡显式给出的锚带宽；缺少显式带宽时不补造空间边界。")
      .replace(/\bv2\b/gi, "本轮评级")
      .replace(/旧代码/g, "历史默认逻辑")
      .replace(/原始字段|字段路径|字段/g, "资料口径")
      .replace(/\s+/g, " ")
      .trim();
  }

  function signalEvidenceFactIsLegacyAnchorFitScore(fact) {
    const view = asObject(fact);
    const id = rawEnum(view.id).trim().toLowerCase();
    const label = rawEnum(view.label_cn);
    const topic = rawEnum(view.topic).toLowerCase();
    const sourceGroup = rawEnum(view.source_group).toLowerCase();
    const refs = asArray(view.source_refs).join(" ").toLowerCase();
    return id === "structure.anchor.score"
      || id.includes("anchor.score")
      || (label.includes("价格锚来源状态") && (topic.includes("anchor") || topic.includes("锚") || sourceGroup.includes("anchor") || sourceGroup.includes("锚") || refs.includes("anchor") || refs.includes("锚")));
  }

  function signalEvidenceFactLabel(fact) {
    if (signalEvidenceFactIsLegacyAnchorFitScore(fact)) return "历史锚贴合刻度（仅背景）";
    const view = asObject(fact);
    return signalEvidenceReaderText(view.label_cn || sourceGroupLabel(view.source_group) || "市场事实", "市场事实");
  }
  function signalEvidenceFactIsFunding(fact) {
    const view = asObject(fact);
    return [
      view.topic,
      view.label_cn,
      view.source_group,
      asArray(view.source_refs).join(" ")
    ].join(" ").toLowerCase().includes("funding")
      || [
        view.topic,
        view.label_cn,
        view.source_group,
        asArray(view.source_refs).join(" ")
      ].join(" ").includes("资金");
  }

  function signalEvidenceUnitText(value) {
    const raw = rawEnum(value).trim();
    if (!raw) return "";
    const lower = raw.toLowerCase();
    if (lower === "decimal") return "小数值";
    if (lower === "ratio") return "比值";
    if (lower === "bps") return "基点";
    return signalEvidenceReaderText(semanticCompact(raw), raw);
  }

  function fundingDecimalText(value) {
    const pct = Number(value) * 100;
    if (!Number.isFinite(pct)) return "";
    const abs = Math.abs(pct);
    const digits = abs > 0 && abs < 0.0001 ? 8 : (abs > 0 && abs < 0.01 ? 6 : (abs < 0.1 ? 4 : 3));
    const text = number(pct, digits);
    return `${pct > 0 ? "+" : ""}${text}%`;
  }

  function signalEvidenceWindowText(value) {
    const raw = rawEnum(value).trim();
    if (!raw) return "";
    const lower = raw.toLowerCase();
    const labels = {
      current: "当前截面",
      previous_card: "前一卡",
      previous: "前一卡"
    };
    if (labels[lower]) return labels[lower];
    const minutes = lower.match(/^(\d+)\s*(?:m|min|mins|minute|minutes)$/);
    if (minutes) return `${minutes[1]} 分钟`;
    const hours = lower.match(/^(\d+)\s*(?:h|hr|hrs|hour|hours)$/);
    if (hours) return `${hours[1]} 小时`;
    const days = lower.match(/^(\d+)\s*(?:d|day|days)$/);
    if (days) return `${days[1]} 天`;
    if (/^\d+$/.test(lower)) return `${lower} 个观察窗口`;
    return signalEvidenceReaderText(semanticCompact(raw), raw);
  }

  function renderSignalEvidenceFact(fact) {
    const view = asObject(fact);
    const metaLine = signalEvidenceFactMetaLine(view, { includeSource: true });
    const notes = signalEvidenceFactLimitations(view);
    const rawSummary = signalEvidenceReaderText(view.summary_cn, "");
    const summary = signalEvidenceSummaryIsMechanical(signalEvidenceFactLabel(view), signalEvidenceDisplayValue(view), rawSummary) ? "" : rawSummary;
    return `
      <div class="signal-evidence-fact">
        <strong>${escapeHtml(signalEvidenceFactLabel(view))}</strong>
        ${metaLine ? `<p>${escapeHtml(metaLine)}</p>` : ""}
        ${summary ? `<p>${escapeHtml(summary)}</p>` : ""}
        ${notes.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}
      </div>
    `;
  }

  function signalEvidenceSourceText(fact) {
    const view = asObject(fact);
    const source = sourceGroupLabel(view.source_group) || sourceRefLabel(asArray(view.source_refs)[0]);
    return source === "来源已记录" ? "" : source;
  }

  function signalEvidenceFactMetaLine(fact, options = {}) {
    const view = asObject(fact);
    const parts = [];
    const source = signalEvidenceSourceText(view);
    if (options.includeSource && source) parts.push(`来源：${source}`);
    const windowText = signalEvidenceWindowText(view.window);
    if (windowText) parts.push(`窗口：${windowText}`);
    const observed = firstPresent(view.observed_at_ms, view.observed_at);
    parts.push(`时点：${observed ? dateText(parseSignalRatingTimeMs(observed) || observed) : "未提供"}`);
    return parts.join("；");
  }

function signalEvidenceGroupMetaLine(facts) {
    const counts = new Map();
    asArray(facts).forEach((fact) => {
      const line = signalEvidenceFactMetaLine(fact);
      if (line) counts.set(line, (counts.get(line) || 0) + 1);
    });
    const common = [...counts.entries()].sort((a, b) => b[1] - a[1])[0];
    return common && common[1] > 1 ? common[0] : "";
  }

  function signalEvidenceCompactCompareText(value) {
    return String(value || "").replace(/[：:，,。；;、｜|\s]/g, "");
  }

  function signalEvidenceSummaryIsMechanical(label, value, summary) {
    const normalizedSummary = signalEvidenceCompactCompareText(summary);
    if (!normalizedSummary) return true;
    const normalizedLabel = signalEvidenceCompactCompareText(label);
    const normalizedValue = signalEvidenceCompactCompareText(value);
    const mechanicalSentences = [
      normalizedLabel,
      normalizedValue,
      `${normalizedLabel}${normalizedValue}`,
      `${normalizedLabel}为${normalizedValue}`,
      `${normalizedLabel}是${normalizedValue}`
    ];
    return normalizedSummary === normalizedLabel
      || mechanicalSentences.includes(normalizedSummary);
  }

  function signalEvidenceRowOnlyLimitation(text) {
    return /暂不可用|不可用于评级|陈旧|缺失|未知|时效不足|stale|missing|unknown/i.test(String(text || ""));
  }

  function signalEvidenceFactLimitations(fact) {
    const view = asObject(fact);
    const limitations = asArray(view.limitations_cn)
      .map((item) => signalEvidenceReaderText(item, ""))
      .filter(Boolean);
    if (view.usable === false) limitations.unshift("本项暂不可用于评级。");
    return limitations;
  }

  function signalEvidenceGroupNotes(facts) {
    const entries = new Map();
    const factList = asArray(facts);
    factList.forEach((fact) => {
      const label = signalEvidenceFactLabel(fact);
      signalEvidenceFactLimitations(fact).forEach((text) => {
        if (signalEvidenceRowOnlyLimitation(text)) return;
        if (!entries.has(text)) entries.set(text, []);
        entries.get(text).push(label);
      });
    });
    return Array.from(entries.entries())
      .filter(([, labels]) => labels.length > 1)
      .map(([text, labels]) => ({
        text,
        labels: [...new Set(labels)].filter(Boolean)
      }));
  }

  function renderSignalEvidenceGroupNotes(notes, factCount) {
    if (!notes.length) return "";
    return `
      <div class="evidence-group-notes">
        <strong>本组共同限制</strong>
        ${notes.map((note) => {
          const scope = note.labels.length && note.labels.length < factCount
            ? `适用于：${note.labels.join("、")}。`
            : "";
          return `<p>${escapeHtml(`${scope}${note.text}`)}</p>`;
        }).join("")}
      </div>
    `;
  }

  function signalEvidenceSortWeight(fact, groupId) {
    if (groupId !== "market-options-structure") return 100;
    const view = asObject(fact);
    const id = signalEvidenceFactKey(fact).toLowerCase();
    const label = rawEnum(view.label_cn).trim();
    const text = [
      rawEnum(view.topic),
      label
    ].join(" ").toLowerCase();
    if (id === "market.price.current" || /^(现价|当前现价|当前价格)$/.test(label)) return 10;
    if (id === "structure.gamma.call_wall") return 20;
    if (id === "structure.distance.call_wall_pct") return 21;
    if (id === "structure.gamma.put_wall") return 22;
    if (id === "structure.distance.put_wall_pct") return 23;
    if (id === "structure.gamma.flip_point") return 30;
    if (id === "structure.distance.flip_pct") return 31;
    if (id === "structure.gamma.pin_strike") return 40;
    if (id === "structure.distance.pin_pct") return 41;
    if (/call_wall.*distance|distance.*call_wall|距.*call.*墙|距.*看涨墙|距.*上方.*墙/.test(id)
      || /距.*call.*墙|距.*看涨墙|距.*上方.*墙/.test(text)) return 21;
    if (/call_wall|看涨墙|上方.*墙/.test(id) || /call.*墙|看涨墙|上方.*墙/.test(text)) return 20;
    if (/put_wall.*distance|distance.*put_wall|距.*put.*墙|距.*看跌墙|距.*下方.*墙/.test(id)
      || /距.*put.*墙|距.*看跌墙|距.*下方.*墙/.test(text)) return 23;
    if (/put_wall|看跌墙|下方.*墙/.test(id) || /put.*墙|看跌墙|下方.*墙/.test(text)) return 22;
    if (/flip|翻转/.test(id) || /flip|翻转/.test(text)) return 30;
    if (/pin|钉/.test(id) || /pin|钉/.test(text)) return 40;
    if (/net.?gamma|净.?gamma|gamma/.test(id) || /net.?gamma|净.?gamma|gamma/.test(text)) return 50;
    if (/anchor|锚|band|带宽/.test(id) || /anchor|锚|band|带宽/.test(text)) return 60;
    return 100;
  }

  function signalEvidenceSortedFacts(facts, groupId) {
    return asArray(facts)
      .map((fact, index) => ({ fact, index, weight: signalEvidenceSortWeight(fact, groupId) }))
      .sort((a, b) => (a.weight - b.weight) || (a.index - b.index))
      .map((item) => item.fact);
  }

  function renderSignalEvidenceFactOverview(fact, groupNoteSet) {
    const view = asObject(fact);
    const label = signalEvidenceFactLabel(view);
    const value = signalEvidenceDisplayValue(view);
    const rawSummary = signalEvidenceReaderText(view.summary_cn, "");
    const summary = signalEvidenceSummaryIsMechanical(label, value, rawSummary) ? "" : rawSummary;
    const limitations = signalEvidenceFactLimitations(view).filter((item) => !groupNoteSet.has(item));
    const numericDescription = typeof view.value === "number" && summary.includes(number(view.value, 4));
    const repeatsState = ["structure.gamma.regime", "structure.location.wall_zone"].includes(signalEvidenceFactKey(view));
    const readingItems = [
      numericDescription || repeatsState ? "" : summary,
      ...limitations.filter(signalEvidenceRowOnlyLimitation)
    ].filter(Boolean);
    return `
      <div class="market-fact-overview-item${signalEvidenceFactKey(view) === "market.price.current" ? " is-current-price" : ""}">
        <div class="market-fact-name"><strong>${escapeHtml(label)}</strong></div>
        <div class="market-fact-value">${escapeHtml(value || "未提供数值")}</div>
        <div class="market-fact-reading">
          ${readingItems.map((item, index) => `<p>${escapeHtml(item)}</p>`).join("")}
        </div>
      </div>
    `;
  }

  function renderSignalEvidenceFactCard(id, title, summary, facts) {
    const factList = signalEvidenceSortedFacts(
      asArray(facts).filter((fact) => !signalEvidenceFactIsLegacyAnchorFitScore(fact)),
      id
    );
    const groupNotes = signalEvidenceGroupNotes(factList);
    const groupNoteSet = new Set(groupNotes.map((note) => note.text));
    const detailHtml = factList.map(renderSignalEvidenceFact).join("");
    return `
      <article id="${escapeHtml(id)}" class="market-fact-card">
        <h3>${escapeHtml(title)}</h3>
        <p>${escapeHtml(summary)}</p>
        ${factList.length ? `
          <div class="market-fact-overview">
            ${factList.map((fact) => renderSignalEvidenceFactOverview(fact, groupNoteSet)).join("")}
          </div>
          <details class="signal-evidence-detail">
            <summary>来源时点与解释边界</summary>
            <div class="signal-evidence-detail-body">${renderSignalEvidenceGroupNotes(groupNotes, factList.length)}${detailHtml}</div>
          </details>
        ` : `<div class="empty-inline">本卡未提供该类事实。</div>`}
      </article>
    `;
  }

function signalEvidenceFactIsVerifiedChange(fact) {
    const topic = rawEnum(asObject(fact).topic).toLowerCase();
    return topic === "change_context" || topic === "verified_change";
  }

  function renderSignalEvidenceChangeRow(fact, context) {
    const view = asObject(fact);
    const summary = signalEvidenceReaderText(view.summary_cn, "");
    const samePeriod = context && view.window === context.window && view.observed_at_ms === context.observed_at_ms;
    return `<article class="transition-core-row evidence-change-row">
      <div class="transition-core-main"><strong>${escapeHtml(signalEvidenceFactLabel(view))}</strong></div>
      <div class="transition-core-values">${escapeHtml(signalEvidenceDisplayValue(view) || "未提供差值")}</div>
      <div class="market-fact-reading"><p>${escapeHtml(summary || "本卡仅记录该项变化，未声明进一步含义。")}</p>${signalEvidenceFactLimitations(view).map((text) => `<p>${escapeHtml(text)}</p>`).join("")}${samePeriod ? "" : `<p class="market-fact-meta">${escapeHtml(signalEvidenceFactMetaLine(view))}</p>`}</div>
    </article>`;
  }

  function signalEvidenceChangeUnavailableText(stateView, context) {
    if (stateView.state === "invalid") return "前后对照暂不可用：本卡资料核验未通过，变化摘要暂不采用。";
    if (!context) return "前后对照暂不可用：缺少可核验的前一卡对照；当前截面仍可阅读。";
    const text = [
      signalEvidenceReaderText(context.summary_cn, ""),
      ...signalEvidenceFactLimitations(context)
    ].join("；");
    if (/身份|品种|symbol|版本|version/i.test(text)) return "前后对照暂不可用：前后卡身份、品种或版本不一致；当前截面仍可阅读。";
    if (/hash|哈希|校验|核验/i.test(text)) return "前后对照暂不可用：前后记录来源未通过核验；当前截面仍可阅读。";
    if (/schema|协议|资料结构|结构不同|字段/i.test(text)) return "前后对照暂不可用：前后卡资料结构不同，暂不做变化推断；当前截面仍可阅读。";
    if (/时序|时间|先后|顺序/i.test(text)) return "前后对照暂不可用：前后卡时间顺序未通过核验；当前截面仍可阅读。";
    return "前后对照暂不可用：" + signalEvidenceReaderText(context.summary_cn, "变化判断暂不可用；当前截面仍可阅读。");
  }

  function renderSignalEvidenceKeyChanges(doc) {
    const stateView = signalEvidenceState(doc);
    if (!stateView.view || stateView.state === "summary") return "";
    const facts = asArray(stateView.view.market_facts).filter(signalEvidenceFactIsVerifiedChange);
    const context = facts.find((fact) => signalEvidenceFactKey(fact) === "change.context.status");
    const statusUsable = context && asObject(context).usable === true && asObject(context).value === "变化可用";
    const changes = statusUsable && stateView.state !== "invalid"
      ? facts.filter((fact) => fact !== context && asObject(fact).usable === true) : [];
    const reason = signalEvidenceChangeUnavailableText(stateView, context);
    if (!changes.length) return section("关键变化骨架", "", `<p class="evidence-change-unavailable">${escapeHtml(reason)} <a href="#market-data-quality">查看对照缺口</a></p>`, "signal-key-changes");
    return section("关键变化骨架", "区分价格移动与结构位迁移，保留变化含义及比较边界。", `
      <div class="evidence-changes-panel">
        <p><strong>${escapeHtml(context ? signalEvidenceFactLabel(context) : "前后对照")}</strong>：${escapeHtml(changes.length ? `${signalEvidenceFactMetaLine(context)}。以下呈现已记录的变化，未记录项不补算。` : reason)}</p>
        ${changes.length ? `<div class="transition-core-list">${changes.map((fact) => renderSignalEvidenceChangeRow(fact, context)).join("")}</div>` : ""}
        ${context ? signalEvidenceFactLimitations(context).map((text) => `<p>${escapeHtml(text)}</p>`).join("") : ""}
        ${statusUsable && !changes.length ? `<p>比较条件已满足，但本卡没有可用的变化数值；不从其他资料补算。</p>` : ""}
      </div>`, "signal-key-changes");
  }

  function signalEvidenceGroupedMarketFacts(view) {
    const grouped = {
      "options-structure": [],
      "price-path": [],
      "active-flow": [],
      "funding-rate": [],
      "macro-background": [],
      "data-quality": []
    };
    asArray(asObject(view).market_facts).forEach((fact) => {
      grouped[evidenceFactTopicKey(fact)].push(fact);
    });
    return grouped;
  }

  function renderSignalEvidenceMarketFacts(doc) {
    const stateView = signalEvidenceState(doc);
    if (!stateView.view || !asArray(stateView.view.market_facts).length) return "";
    const grouped = signalEvidenceGroupedMarketFacts(stateView.view);
    return section("中文市场事实", "完整证据面：空间位置、压力、响应与背景；同源派生描述不增加独立证明力。", `
      <div class="market-fact-grid">
        ${renderSignalEvidenceFactCard("market-options-structure", "空间结构", "先看结构位置，再解释倾向。期权偏斜属于方向背景，不等于墙位约束。", grouped["options-structure"])}
        ${renderSignalEvidenceFactCard("market-price-path", "价格路径与压力", "说明不利侧推进程度，平盘不解释成强趋势。", grouped["price-path"])}
        ${renderSignalEvidenceFactCard("market-active-flow", "主动成交与响应", "观察压力是否传导到价格，避免把同源流重复加票。", grouped["active-flow"])}
        ${renderSignalEvidenceFactCard("market-funding-rate", "资金费率", "只作为杠杆拥挤和反身性背景。", grouped["funding-rate"])}
        ${renderSignalEvidenceFactCard("market-macro-background", "宏观背景", "区分方向背景和冲击限制。", grouped["macro-background"])}
        ${renderSignalEvidenceFactCard("market-data-quality", "变化、时效与缺口", "前后卡不匹配只影响变化判断，不拖垮当前截面事实。", grouped["data-quality"])}
      </div>
    `, "market-evidence");
  }

function renderSignalEvidenceReaderNav(doc) {
    const stateView = signalEvidenceState(doc);
    const view = stateView.view || {};
    const items = [];
    if (stateView.state === "invalid" || signalEvidenceHasUsableView(stateView)) items.push({id:"signal-comfort",label:"决策与评级"});
    if (asArray(view.market_facts).length) items.push({id:"signal-spatial-dynamics",label:"空间约束动力学"});
    if (stateView.view && stateView.state !== "summary") items.push({id:"signal-key-changes",label:"关键变化"});
    if (stateView.state === "full") items.push({id:"signal-llm-review",label:"独立复核"});
    if (stateView.state === "full") items.push({id:"signal-next-conditions",label:"下一观察"});
    if (asArray(view.market_facts).length) items.push({id:"market-evidence",label:"数值与来源"});
    return items.length ? `<nav class="evidence-reader-nav" aria-label="本卡阅读导航">${items.map((item) => `<a href="#${item.id}">${item.label}</a>`).join("")}</nav>` : "";
  }

  function renderSignalEvidenceReader(doc, content) {
    return `<div class="evidence-reader">${renderLoadNotice()}${content}</div>`;
  }

  const SIGNAL_COMFORT_SCHEMA = "signal_comfort_ratings@1.0.0";
  const SIGNAL_COMFORT_SCOPE = "signal_side_admission";
  const SIGNAL_COMFORT_ECONOMICS = "not_evaluated";
  const SIGNAL_COMFORT_GRADES = ["D", "C", "B", "A", "S"];
  const SIGNAL_COMFORT_SIDES = [
    { key: "put_credit", label: "Put 信用价差" },
    { key: "call_credit", label: "Call 信用价差" }
  ];
  const SIGNAL_COMFORT_FOCUS_SIDES = new Set(["put_credit", "call_credit", "tie", "none"]);
  const SIGNAL_COMFORT_SIDE_STATUSES = new Set(["RATED", "UNRATED"]);

  function firstObject(...values) {
    for (const value of values) {
      const object = asObject(value);
      if (Object.keys(object).length) return object;
    }
    return {};
  }

  function comfortGradeKey(value) {
    const raw = rawEnum(value).toUpperCase();
    return SIGNAL_COMFORT_GRADES.includes(raw) ? raw : null;
  }

  function comfortGradeRank(value) {
    const grade = comfortGradeKey(value);
    return grade ? SIGNAL_COMFORT_GRADES.indexOf(grade) : -1;
  }

  function comfortGradeText(value) {
    const grade = comfortGradeKey(value);
    return grade ? `${grade}级` : "未评级";
  }

  function comfortGradeClass(value) {
    const grade = comfortGradeKey(value);
    return grade ? `is-grade-${grade.toLowerCase()}` : "is-unrated";
  }

  function comfortGradeAction(value) {
    const grade = comfortGradeKey(value);
    const actions = {
      S: "优先准入：先处理该侧信号层机会，再进入候选价差与风控确认。",
      A: "信号层准入：可以启动人工交易准备，但不代表具体价差值得成交。",
      B: "启动关注：有可说明机会，先跟踪关键条件与反证变化。",
      C: "普通观察：数据足以阅读，但本轮没有主动跟踪价值。",
      D: "本轮回避：存在明确不适配、前提失效或风险否决。"
    };
    return actions[grade] || "暂未完成有效评级：必要资料或复核尚未形成可用等级。";
  }

  function comfortFocusSideText(value) {
    const raw = rawEnum(value).toLowerCase();
    const labels = {
      put_credit: "Put 信用价差",
      call_credit: "Call 信用价差",
      tie: "无单一优先侧",
      none: "无可用侧别"
    };
    return labels[raw] || "无可用侧别";
  }

  function normalizeComfortText(value, fallback = "未说明") {
    if (isBlank(value) || Array.isArray(value) || typeof value === "object") return fallback;
    return compactLongDecimalText(String(value))
      .replace(/总耐用性评分(?:为|是|:|：)?\s*\d+(?:\.\d+)?(?:\/100)?[，,；;。]?/g, "")
      .replaceAll("factor_cross_section.anchor", "价格锚")
      .replaceAll("factor_cross_section.gamma_regime", "Gamma 结构")
      .replaceAll("factor_cross_section.gex_info", "GEX 空间")
      .replaceAll("factor_cross_section.tmvf", "量价主干")
      .replaceAll("factor_cross_section.micro_flow", "主动买卖流")
      .replaceAll("factor_cross_section.funding", "资金费率")
      .replaceAll("factor_cross_section.macro_pressure", "宏观背景")
      .replaceAll("bullish_bias", "偏多背景")
      .replaceAll("bearish_bias", "偏空背景")
      .replaceAll("net_gamma_notional", "净 Gamma 名义额")
      .replaceAll("net_gex_sign", "Gamma 符号")
      .replaceAll("gamma_regime", "Gamma 结构")
      .replaceAll("gex_info", "GEX 空间")
      .replace(/Mild\s+Tailwind/gi, "温和顺风")
      .replace(/Mild\s+Headwind/gi, "温和逆风")
      .replace(/Strong\s+Tailwind/gi, "强顺风")
      .replace(/Strong\s+Headwind/gi, "强逆风")
      .replace(/\bTailwind\b/gi, "顺风")
      .replace(/\bHeadwind\b/gi, "逆风")
      .replace(/\bWeak\b/gi, "弱")
      .replace(/\bCONFIDENCE_GATE_NOT_DIRECTIONAL_VOTE\b/gi, "置信门仅作限制，不提供方向依据")
      .replace(/\bMATERIAL\b/g, "实质分歧")
      .replace(/\bMISSING\b/g, "缺失")
      .replace(/\bSEVERE\b/g, "严重分歧")
      .replaceAll("WAIT_CONFIRMATION", "等待确认")
      .replaceAll("NO_TRADE_BLOCKED", "阻断观望")
      .replaceAll("TRADE_SUPPORT_STRONG", "强结构复核")
      .replaceAll("TRADE_SUPPORT_REVIEW", "结构复核")
      .replaceAll("NR_NOT_CONFIRMED", "接管窗口未确认")
      .replaceAll("execution_allowed", "执行许可")
      .replace(/confidence/gi, "旧置信")
      .replace(/durability/gi, "耐用背景")
      .replace(/方向计票/g, "方向依据")
      .replace(/参与计票/g, "本次作为方向依据")
      .replace(/不计票/g, "本次未作为方向依据")
      .replace(/门控/g, "当前限制")
      .replace(/已排除/g, "本次未采用")
      .replace(/\btrue\b/gi, "是")
      .replace(/\bfalse\b/gi, "否")
      .replace(/\bEDB\b/g, "证据账本")
      .replace(/\b[a-z]+(?:_[a-z0-9]+)+(?:\.[a-z0-9_]+)*\b/g, "未说明")
      .replace(/\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b/g, (token) => enumLabels[token] || enumLabels[token.toUpperCase()] || "已记录状态")
      .replace(/\s+/g, " ")
      .trim();
  }

  function normalizeComfortList(items) {
    return asArray(items)
      .map((item) => normalizeComfortText(item, ""))
      .filter(Boolean);
  }

  function normalizeComfortSide(side) {
    const view = asObject(side);
    const finalGrade = comfortGradeKey(view.final_grade);
    const modelGrade = comfortGradeKey(view.model_grade);
    const rawStatus = rawEnum(view.status).toUpperCase();
    const status = SIGNAL_COMFORT_SIDE_STATUSES.has(rawStatus) ? rawStatus : "";
    return {
      status,
      model_grade: modelGrade,
      final_grade: finalGrade,
      basis_cn: normalizeComfortText(view.basis_cn || view.summary_cn, status === "RATED" ? "未说明支持理由。" : "暂未完成有效评级。"),
      counter_evidence_cn: normalizeComfortText(view.counter_evidence_cn, "未识别到主要反对理由。"),
      next_observation_cn: normalizeComfortText(view.next_observation_cn, "等待下一条有效观察。"),
      unresolved_conditions_cn: normalizeComfortList(view.unresolved_conditions_cn),
      cap_reasons_cn: normalizeComfortList(view.cap_reasons_cn),
      evidence_refs: asArray(view.evidence_refs),
      counter_evidence_refs: asArray(view.counter_evidence_refs),
      s_upgrade_basis_cn: normalizeComfortText(view.s_upgrade_basis_cn, ""),
      s_upgrade_evidence_refs: asArray(view.s_upgrade_evidence_refs)
    };
  }

  function normalizeComfortHeadline(candidate) {
    const view = asObject(candidate);
    const rawFocus = rawEnum(firstPresent(view.focus_side, view.side, view.focus)).toLowerCase();
    const focusSide = SIGNAL_COMFORT_FOCUS_SIDES.has(rawFocus) ? rawFocus : "";
    return {
      final_grade: comfortGradeKey(view.final_grade),
      focus_side: focusSide,
      action_cn: normalizeComfortText(view.action_cn || view.summary_cn || view.headline_cn, "")
    };
  }

  function rawComfortCandidate(doc) {
    const review = asObject(get(doc, "llm_review", {}));
    const content = llmReviewContent(doc);
    return firstObject(
      get(content, "integrated_trade_advisory.side_comfort_ratings", null),
      get(review, "content.integrated_trade_advisory.side_comfort_ratings", null),
      get(review, "integrated_trade_advisory.side_comfort_ratings", null),
      get(doc, "integrated_trade_advisory.side_comfort_ratings", null),
      get(doc, "side_comfort_ratings", null)
    );
  }

  function comfortSummaryCandidate(doc) {
    return firstObject(
      get(doc, "signal_comfort_summary", null),
      get(doc, "summary.signal_comfort_summary", null)
    );
  }

  function buildComfortView(candidate, source) {
    const object = asObject(candidate);
    const put = normalizeComfortSide(object.put_credit);
    const call = normalizeComfortSide(object.call_credit);
    const headline = normalizeComfortHeadline(firstObject(object.headline, object));
    return {
      source,
      schema: object.schema,
      rating_scope: object.rating_scope,
      candidate_quote_economics: object.candidate_quote_economics,
      as_of_ms: object.as_of_ms,
      put_credit: put,
      call_credit: call,
      headline,
      raw_headline: asObject(object.headline)
    };
  }

  function comfortSideValidationErrors(side, label) {
    const errors = [];
    if (!side.status) errors.push(`${label}评级状态未声明。`);
    if (side.status === "RATED" && !side.final_grade) errors.push(`${label}声明已评级但缺少最终等级。`);
    if (side.status === "UNRATED" && side.final_grade) errors.push(`${label}声明未评级但带有最终等级。`);
    if (side.final_grade && !side.basis_cn) errors.push(`${label}缺少可读支持理由。`);
    return errors;
  }

  function comfortHeadlineValidationErrors(view) {
    const errors = [];
    const headline = asObject(view.headline);
    if (!headline.final_grade) errors.push("综合最终等级未声明。");
    if (!headline.focus_side) errors.push("综合适用侧别未声明。");
    if (!headline.action_cn) errors.push("综合行动结论未声明。");
    const putGrade = asObject(view.put_credit).final_grade;
    const callGrade = asObject(view.call_credit).final_grade;
    const headlineRank = comfortGradeRank(headline.final_grade);
    const highestSideRank = Math.max(comfortGradeRank(putGrade), comfortGradeRank(callGrade));
    if (headlineRank >= 0 && highestSideRank >= 0 && headlineRank !== highestSideRank) {
      errors.push("综合等级未使用通过校验的最高侧别等级。");
    }
    if (headline.focus_side === "put_credit" && headline.final_grade !== putGrade) {
      errors.push("综合等级与 Put 侧最终等级不一致。");
    }
    if (headline.focus_side === "call_credit" && headline.final_grade !== callGrade) {
      errors.push("综合等级与 Call 侧最终等级不一致。");
    }
    if (headline.focus_side === "tie" && (!putGrade || putGrade !== callGrade || headline.final_grade !== putGrade)) {
      errors.push("并列侧别与两侧最终等级不一致。");
    }
    if (headline.focus_side === "none" && headline.final_grade) {
      errors.push("无可用侧别不能带最终等级。");
    }
    return errors;
  }

  function comfortSummaryAlignmentErrors(fullView, summaryView) {
    const errors = [];
    if (!summaryView) {
      errors.push("发布摘要缺失，详情页不单独升级。");
      return errors;
    }
    const fullHeadline = asObject(fullView.headline);
    const summaryHeadline = asObject(summaryView.headline);
    if (!summaryHeadline.final_grade || !summaryHeadline.focus_side) {
      errors.push("列表摘要未给出有效综合评级，详情页不单独升级。");
      return errors;
    }
    if (summaryHeadline.final_grade !== fullHeadline.final_grade
      || summaryHeadline.focus_side !== fullHeadline.focus_side) {
      errors.push("列表摘要与详情综合评级不一致。");
    }
    SIGNAL_COMFORT_SIDES.forEach(({ key, label }) => {
      const fullGrade = asObject(fullView[key]).final_grade;
      const summaryGrade = asObject(summaryView[key]).final_grade;
      if (summaryGrade && fullGrade && summaryGrade !== fullGrade) {
        errors.push(`${label}列表摘要与详情等级不一致。`);
      }
    });
    return errors;
  }


  function comfortViewHasAnyGrade(view) {
    return Boolean(
      comfortGradeKey(get(view, "headline.final_grade"))
      || comfortGradeKey(get(view, "put_credit.final_grade"))
      || comfortGradeKey(get(view, "call_credit.final_grade"))
    );
  }

  function comfortViewRequiresAdmission(view) {
    return [
      get(view, "headline.final_grade"),
      get(view, "put_credit.final_grade"),
      get(view, "call_credit.final_grade")
    ].some((grade) => comfortGradeRank(grade) >= comfortGradeRank("A"));
  }

  function comfortFocusedSideKeys(focusSide) {
    if (focusSide === "put_credit") return ["put_credit"];
    if (focusSide === "call_credit") return ["call_credit"];
    if (focusSide === "tie") return ["put_credit", "call_credit"];
    return [];
  }

  function sourceBoundaryBlocksAdmission(doc, boundary) {
    const markers = [
      boundary.support_label,
      boundary.hard_veto,
      get(doc, "signal_rating.context.nr_state"),
      get(doc, "neutral_repair.state"),
      get(doc, "signal_window.state"),
      get(doc, "decision.nr_state"),
      get(doc, "decision_matrix.nr_state")
    ].map((item) => rawEnum(item).toUpperCase()).join(" ");
    return boundary.has_block === true
      || !isBlank(boundary.hard_veto)
      || /WAIT|BLOCK|NO_TRADE|VETO|EXPIRED|STALE|FAILED|INVALID|失效/.test(markers);
  }

  function comfortRefLooksFuture(ref) {
    const root = rawTraceRoot(ref).toLowerCase();
    return root.includes("future_24h") || root.includes("bayesian_report") || root.includes("future_report");
  }

  function comfortEvidenceRefErrorsForSide(doc, side, label) {
    const errors = [];
    const grade = comfortGradeKey(side.final_grade);
    if (comfortGradeRank(grade) < comfortGradeRank("A")) return errors;
    const evidenceRefs = asArray(side.evidence_refs).filter((ref) => !isBlank(ref));
    const counterRefs = asArray(side.counter_evidence_refs).filter((ref) => !isBlank(ref));
    const upgradeRefs = asArray(side.s_upgrade_evidence_refs).filter((ref) => !isBlank(ref));
    if (!evidenceRefs.length) errors.push(`${label}缺少可追溯支持来源。`);
    [...evidenceRefs, ...counterRefs, ...upgradeRefs].forEach((ref) => {
      if (comfortRefLooksFuture(ref)) {
        errors.push(`${label}引用了评级之后才能验证的资料。`);
      } else if (!hasRawTraceTarget(ref, doc)) {
        errors.push(`${label}存在无法在本卡核验的来源引用。`);
      }
    });
    if (grade === "S") {
      if (!side.s_upgrade_basis_cn || !upgradeRefs.length) {
        errors.push(`${label}声明 S 级但没有单列额外依据。`);
      }
      const supportRoots = new Set(evidenceRefs.map((ref) => rawTraceRoot(ref)));
      if (upgradeRefs.some((ref) => supportRoots.has(rawTraceRoot(ref)))) {
        errors.push(`${label}的 S 级额外依据与 A 级支持来源重复。`);
      }
    }
    return [...new Set(errors)];
  }

  function comfortSourceValidationErrors(doc, view) {
    const errors = [];
    if (!comfortViewHasAnyGrade(view)) return errors;
    if (comfortViewRequiresAdmission(view) && sourceBoundaryBlocksAdmission(doc, signalRatingBoundaryView(doc))) {
      errors.push("当前记录仍处在等待、阻断或失效边界，不能显示 A/S 准入。");
    }
    comfortFocusedSideKeys(view.headline.focus_side).forEach((sideKey) => {
      errors.push(...comfortEvidenceRefErrorsForSide(doc, asObject(view[sideKey]), SIGNAL_COMFORT_SIDES.find((item) => item.key === sideKey).label));
    });
    return [...new Set(errors)];
  }

  function signalComfortState(doc) {
    const summary = comfortSummaryCandidate(doc);
    const summaryView = Object.keys(summary).length ? buildComfortView(summary, "summary") : null;
    const full = rawComfortCandidate(doc);
    if (Object.keys(full).length) {
      const view = buildComfortView(full, "full");
      const errors = [];
      if (view.schema !== SIGNAL_COMFORT_SCHEMA) errors.push("综合评级版本未通过当前页面校验。");
      if (view.rating_scope !== SIGNAL_COMFORT_SCOPE) errors.push("综合评级用途不是本页的信号层准入。");
      if (view.candidate_quote_economics !== SIGNAL_COMFORT_ECONOMICS) errors.push("候选价差经济性边界未按未评估处理。");
      if (!signalRatingAsOfIsValid(view.as_of_ms)) errors.push("综合评级时点暂不可核验。");
      errors.push(...comfortSideValidationErrors(view.put_credit, "Put侧"));
      errors.push(...comfortSideValidationErrors(view.call_credit, "Call侧"));
      errors.push(...comfortHeadlineValidationErrors(view));
      errors.push(...comfortSummaryAlignmentErrors(view, summaryView));
      errors.push(...comfortSourceValidationErrors(doc, view));
      if (errors.length) {
        return { state: "invalid", view, errors };
      }
      return { state: "full", view, errors: [] };
    }
    if (summaryView) {
      return { state: "summary", view: summaryView, errors: [] };
    }
    if (llmReviewHasFailed(doc)) {
      return { state: "invalid", view: null, errors: ["复核未完成，暂不能形成综合评级。"] };
    }
    return { state: "missing", view: null, errors: [] };
  }

  function signalComfortMetricText(doc, side = "headline") {
    const evidence = signalEvidenceState(doc);
    if (evidence.state === "invalid") return "评级未完成";
    if (signalEvidenceHasUsableView(evidence)) {
      if (side === "headline") return [
        `Put ${comfortGradeText(evidence.view.put_credit.grade)}`,
        `Call ${comfortGradeText(evidence.view.call_credit.grade)}`
      ].join(" / ");
      return comfortGradeText(asObject(evidence.view[side]).grade);
    }
    const stateView = signalComfortState(doc);
    if (stateView.state === "invalid") return "评级未完成";
    if (!stateView.view || stateView.state === "missing") return "历史未综合评级";
    if (side === "headline") return [
      comfortGradeText(stateView.view.headline.final_grade),
      comfortFocusSideText(stateView.view.headline.focus_side)
    ].join(" / ");
    return comfortGradeText(asObject(stateView.view[side]).final_grade);
  }

  function signalComfortAsOfMetricText(doc) {
    if (hasSignalEvidenceV2Surface(doc)) return signalEvidenceAsOfMetricText(doc);
    const stateView = signalComfortState(doc);
    if (stateView.state === "invalid") return "待完成";
    if (!stateView.view || stateView.state === "missing") return "未评级";
    return signalRatingAsOfText(stateView.view);
  }

  function currentLimitMetricText(doc) {
    const boundary = signalRatingBoundaryView(doc);
    if (boundary.has_block === true) return "有阻断";
    if (boundary.execution_allowed !== true) return "只读复核";
    return "当前允许";
  }

  function signalComfortIndexStats(doc) {
    const evidenceStats = signalEvidenceIndexStats(doc);
    if (evidenceStats) return evidenceStats;
    if (hasSignalEvidenceV2Surface(doc)) return ["暂未完成有效评级", `质量 ${semanticCompact(qualityOverall(doc))}`];
    const stateView = signalComfortState(doc);
    if (stateView.state === "invalid") return ["暂未完成有效评级", `质量 ${semanticCompact(qualityOverall(doc))}`];
    if (!stateView.view || stateView.state === "missing") {
      return ["历史版本未综合评级", `质量 ${semanticCompact(qualityOverall(doc))}`];
    }
    return [
      `旧版行动评级 ${comfortGradeText(stateView.view.headline.final_grade)}`,
      comfortFocusSideText(stateView.view.headline.focus_side),
      `Put ${comfortGradeText(stateView.view.put_credit.final_grade)}`,
      `Call ${comfortGradeText(stateView.view.call_credit.final_grade)}`
    ];
  }

  function comfortFilterMatches(doc) {
    if (!state.grade) return true;
    if (hasSignalEvidenceV2Surface(doc)) return signalEvidenceFilterMatches(doc);
    return false;
  }

  function signalComfortSearchText(doc) {
    if (hasSignalEvidenceV2Surface(doc)) return "";
    const stateView = signalComfortState(doc);
    if (stateView.state === "invalid") return "暂未完成有效评级";
    if (!stateView.view) return "历史版本未综合评级";
    const view = stateView.view;
    return [
      comfortGradeText(view.headline.final_grade),
      comfortFocusSideText(view.headline.focus_side),
      view.headline.action_cn,
      view.put_credit.basis_cn,
      view.put_credit.counter_evidence_cn,
      view.call_credit.basis_cn,
      view.call_credit.counter_evidence_cn,
      ...view.put_credit.unresolved_conditions_cn,
      ...view.call_credit.unresolved_conditions_cn,
      ...view.put_credit.cap_reasons_cn,
      ...view.call_credit.cap_reasons_cn
    ].join(" ");
  }


  function containsAdmissionLanguage(text) {
    return /准入|优先处理|交易准备|值得成交|下单|开仓/.test(String(text || ""));
  }

  function comfortCurrentActionText(view) {
    const grade = comfortGradeKey(get(view, "headline.final_grade"));
    const action = get(view, "headline.action_cn", "") || comfortGradeAction(grade);
    if (comfortGradeRank(grade) >= comfortGradeRank("A") || !containsAdmissionLanguage(action)) return action;
    if (grade === "B") {
      return `当前最终等级为 B级，只启动关注；原复核中的准入性表述只作为局部支持依据，不能当作当前准入结论。`;
    }
    return `当前最终等级为 ${comfortGradeText(grade)}；原复核中的准入性表述只作为局部意见，不能当作当前准入结论。`;
  }

  function comfortSideBasisText(side) {
    const grade = comfortGradeKey(side.final_grade);
    const basis = side.basis_cn || "未说明支持理由。";
    if (comfortGradeRank(grade) >= comfortGradeRank("A") || !containsAdmissionLanguage(basis)) return basis;
    return `原复核理由：${basis} 当前最终等级为 ${comfortGradeText(grade)}，这只保留为支持依据，不代表当前准入。`;
  }

  function isCandidateEconomicsCondition(text) {
    return /候选经济性|not_evaluated|具体报价|候选报价|净补偿|权利金补偿|候选结构的经济性|经济性与风险|不构成评级依据|实际可执行性未评估|可执行性未评估/.test(String(text || ""));
  }

  function isMarketCounterSegment(text) {
    return /价格|空间|结构|Gamma|GEX|锚|墙|翻转|上行|下行|侵入|压力|主动|成交|CVD|量价|资金费率|宏观|波动|突破|失效|反证|拥挤|路径/.test(String(text || ""));
  }

  function stripCandidateEconomicsSegments(text) {
    const raw = String(text || "").trim();
    if (!raw) return "";
    const segments = raw.match(/[^。；;.!?\n]+[。；;.!?]?/g) || [raw];
    return segments
      .map((segment) => segment.trim())
      .filter((segment) => segment && (!isCandidateEconomicsCondition(segment) || isMarketCounterSegment(segment)))
      .join("");
  }

  function candidateLiquidityRiskText(text) {
    return /薄|流动性|liquidity|周末|末日|周末轮/i.test(String(text || ""))
      ? "执行层仍需复核薄流动性与成交风险；这不是信号评级的升级门。"
      : "";
  }

  function comfortSideCounterText(side) {
    const text = side.market_counter_cn || side.counter_evidence_cn || "未识别到主要反对理由。";
    const stripped = stripCandidateEconomicsSegments(text);
    if (stripped) return stripped;
    if (isCandidateEconomicsCondition(text)) {
      return candidateLiquidityRiskText(text) || "尚无可用反对说明。";
    }
    return text;
  }

  function sideComfortCard(side, label, doc) {
    const grade = comfortGradeText(side.final_grade);
    const gradeKey = comfortGradeKey(side.final_grade);
    const isUnrated = !gradeKey || side.status === "UNRATED";
    const refs = asArray(side.evidence_refs).map((ref) => sourceRefLink(ref, doc)).join("");
    const counterRefs = asArray(side.counter_evidence_refs).map((ref) => sourceRefLink(ref, doc)).join("");
    const upgradeRefs = comfortGradeKey(side.final_grade) === "S"
      ? asArray(side.s_upgrade_evidence_refs).map((ref) => sourceRefLink(ref, doc)).join("")
      : "";
    return `
      <article class="comfort-side-card ${comfortGradeClass(side.final_grade)}">
        <div class="comfort-side-head">
          <strong>${escapeHtml(label)}</strong>
          <span class="badge ${comfortGradeClass(side.final_grade)}">${escapeHtml(grade)}</span>
        </div>
        ${isUnrated
          ? `<p><strong>保留观察</strong>：模型尚未给出有效等级，以下为保留观察：${escapeHtml(side.basis_cn || "暂未完成有效评级。")}</p>`
          : `<p><strong>${gradeKey === "D" ? "回避理由" : "支持理由"}</strong>：${escapeHtml(comfortSideBasisText(side))}</p>`}
        <p><strong>主要反对</strong>：${escapeHtml(comfortSideCounterText(side))}</p>
        ${comfortGradeKey(side.final_grade) === "S" && side.s_upgrade_basis_cn ? `<p><strong>S级额外依据</strong>：${escapeHtml(side.s_upgrade_basis_cn)}</p>` : ""}
        ${refs ? `<div class="source-ref-row">${refs}</div>` : ""}
        ${counterRefs ? `<div class="source-ref-row">${counterRefs}</div>` : ""}
        ${upgradeRefs ? `<div class="source-ref-row">${upgradeRefs}</div>` : ""}
      </article>
    `;
  }

  function renderComfortLimitList(view) {
    const rows = [];
    let filteredEconomics = false;
    SIGNAL_COMFORT_SIDES.forEach(({ key, label }) => {
      const side = asObject(view[key]);
      asArray(side.cap_reasons_cn).forEach((text) => rows.push(`${label}：${text}`));
      asArray(side.unresolved_conditions_cn).forEach((text) => {
        if (isCandidateEconomicsCondition(text)) {
          const liquidityRisk = candidateLiquidityRiskText(text);
          if (liquidityRisk) rows.push(`${label}：${liquidityRisk}`);
          filteredEconomics = true;
          return;
        }
        rows.push(`${label}：${text}`);
      });
    });
    const economicsNote = filteredEconomics
      ? `<p class="comfort-economics-note">具体报价与补偿在交易准备时确认，不是信号评级的必要输入。</p>`
      : "";
    const list = rows.length
      ? `<ul class="plain-list">${rows.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
      : `<p>当前没有声明额外限级原因，继续看下一观察条件。</p>`;
    return `${list}${economicsNote}`;
  }

  function renderComfortNextList(view) {
    const rows = SIGNAL_COMFORT_SIDES.map(({ key, label }) => {
      const side = asObject(view[key]);
      return `${label}：${side.next_observation_cn || "等待下一条有效观察。"}`;
    });
    return `<ul class="plain-list">${rows.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
  }

  function renderSignalComfort(doc) {
    if (hasSignalEvidenceV2Surface(doc)) return renderSignalEvidenceDecision(doc);
    const stateView = signalComfortState(doc);
    if (stateView.state === "invalid") {
      return section("本卡行动结论", "本卡只显示通过核验的行动等级；资料不足时保留市场事实。", `
        <div class="comfort-panel is-unavailable">
          <div class="comfort-headline">
            <span>综合评级</span>
            <strong>暂未完成有效评级</strong>
            <p>评级资料暂不能用于当前行动结论；保留本卡市场观察和边界事实。</p>
          </div>
        </div>
      `, "signal-comfort");
    }
    if (!stateView.view || stateView.state === "missing") {
      return section("本卡行动结论", "本卡只说明当前行动优先级；旧卡不会用旧分数补造等级。", `
        <div class="comfort-panel is-history">
          <div class="comfort-headline">
            <span>综合评级</span>
            <strong>历史版本未综合评级</strong>
            <p>历史版本未综合评级；保留本卡市场观察、边界事实和已有深入分析。</p>
          </div>
        </div>
      `, "signal-comfort");
    }
    const view = stateView.view;
    const headline = view.headline;
    return section("本卡行动结论", "D 到 S 表示当前信号对末日垂直信用价差的行动优先级，不表示胜率。", `
      <div class="comfort-panel ${comfortGradeClass(headline.final_grade)}">
        <div class="comfort-headline">
          <span>综合评级</span>
          <strong>${escapeHtml(`${comfortGradeText(headline.final_grade)}｜${comfortFocusSideText(headline.focus_side)}`)}</strong>
          <p>${escapeHtml(comfortCurrentActionText(view))}</p>
          <p>评级时点：${escapeHtml(signalRatingAsOfText(view))}。候选两腿、报价、费用、净补偿与退出条件仍在候选交易层确认。</p>
        </div>
        <div class="comfort-side-grid">
          ${sideComfortCard(view.put_credit, "Put 信用价差", doc)}
          ${sideComfortCard(view.call_credit, "Call 信用价差", doc)}
        </div>
        <div class="comfort-limit-grid">
          <div>
            <h3 class="subsection-title">为什么停在这个等级</h3>
            ${renderComfortLimitList(view)}
          </div>
          <div>
            <h3 class="subsection-title">下一观察条件</h3>
            ${renderComfortNextList(view)}
          </div>
        </div>
      </div>
    `, "signal-comfort");
  }

  const SIGNAL_RATING_SCHEMA = "signal_rating@1.0.0";
  const SIGNAL_RATING_SCOPE = "side_environment_v1";
  const SIGNAL_RATING_ECONOMICS = "not_evaluated";
  const SIGNAL_RATING_CLAIMS = [
    { key: "structure", label: "结构支持" },
    { key: "put_pressure", label: "Put 信用价差环境" },
    { key: "call_pressure", label: "Call 信用价差环境" }
  ];
  const SIGNAL_RATING_STATUS_LABELS = {
    SUPPORTED: "有支持",
    CONFLICTED: "有冲突",
    OPPOSED: "有反对",
    INSUFFICIENT: "依据不足"
  };
  const SIGNAL_RATING_STATUS_SET = new Set(Object.keys(SIGNAL_RATING_STATUS_LABELS));

  function signalRatingStatusKey(value) {
    const raw = rawEnum(value || "INSUFFICIENT").toUpperCase();
    return SIGNAL_RATING_STATUS_SET.has(raw) ? raw : "INSUFFICIENT";
  }

  function signalRatingStatusLabel(value) {
    return SIGNAL_RATING_STATUS_LABELS[signalRatingStatusKey(value)];
  }

  function signalRatingStatusClass(value) {
    const status = signalRatingStatusKey(value).toLowerCase();
    return `is-${status}`;
  }

  function signalRatingBadgeClass(value) {
    const status = signalRatingStatusKey(value);
    if (status === "SUPPORTED") return "is-good";
    if (status === "OPPOSED") return "is-bad";
    return "is-wait";
  }

  function signalRatingScopeText(value) {
    const raw = rawEnum(value);
    const labels = {
      side_environment_v1: "环境/侧别支持"
    };
    return labels[raw] || semanticCompact(raw) || raw || "未声明用途";
  }

  function signalRatingAsOfIsValid(value) {
    const numeric = Number(value);
    return Number.isFinite(numeric) && numeric > 0 && !Number.isNaN(new Date(numeric).getTime());
  }

  function parseSignalRatingTimeMs(value) {
    if (isBlank(value)) return null;
    if (typeof value === "number" || (typeof value === "string" && /^-?\d+(\.\d+)?$/.test(value.trim()))) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric) || numeric <= 0) return null;
      return numeric < 100000000000 ? numeric * 1000 : numeric;
    }
    const parsed = new Date(value).getTime();
    return Number.isFinite(parsed) ? parsed : null;
  }

  function signalRatingActualSnapshotMs(doc) {
    const analysis = analysisRound(doc);
    const candidates = [
      get(doc, "runtime_facts.snapshot_collected_time_ms"),
      get(doc, "runtime_facts.snapshot_collected_time_utc8"),
      get(doc, "runtime_facts.snapshot_collected_at"),
      get(doc, "runtime.snapshot_collected_time_ms"),
      get(doc, "runtime.snapshot_collected_time_utc8"),
      get(doc, "runtime.snapshot_collected_at"),
      get(doc, "provenance.snapshot_collected_time_ms"),
      get(doc, "provenance.snapshot_collected_time_utc8"),
      get(doc, "provenance.actual_snapshot_time_ms"),
      get(doc, "provenance.actual_snapshot_time"),
      analysis.snapshot_collected_time_ms,
      analysis.snapshot_collected_ms,
      analysis.snapshot_collected_time_utc8,
      analysis.snapshot_collected_time,
      analysis.snapshot_collected_at,
      analysis.actual_snapshot_time_ms,
      analysis.actual_snapshot_time,
      get(doc, "snapshot_collected_time_ms"),
      get(doc, "snapshot_collected_time_utc8"),
      get(doc, "snapshot_collected_at")
    ];
    for (const candidate of candidates) {
      const parsed = parseSignalRatingTimeMs(candidate);
      if (parsed !== null) return parsed;
    }
    return null;
  }

  function signalRatingEventConfirmedMs(doc) {
    return parseSignalRatingTimeMs(confirmedAt(doc));
  }

  function signalRatingExpectedAsOfMs(doc) {
    return signalRatingActualSnapshotMs(doc) ?? signalRatingEventConfirmedMs(doc);
  }

  function signalRatingAsOfCardError(value, doc, label, options = {}) {
    if (!signalRatingAsOfIsValid(value)) return `${label}.as_of_ms 非法`;
    const observedMs = Number(value);
    if (options.checkExpected === false) return null;
    const expectedMs = signalRatingExpectedAsOfMs(doc);
    if (expectedMs !== null && Math.abs(observedMs - expectedMs) > 1000) {
      return `${label}.as_of_ms 与实际快照/确认时间不一致`;
    }
    return null;
  }

  function signalRatingClaimSummary(claim) {
    const view = asObject(claim);
    return {
      status: signalRatingStatusKey(view.status),
      summary_cn: isBlank(view.summary_cn) ? "该主张没有提供可读摘要。" : String(view.summary_cn)
    };
  }

  function signalRatingSummaryFromRating(rating) {
    const view = asObject(rating);
    const claims = asObject(view.claims);
    return {
      schema: view.schema,
      rating_scope: view.rating_scope,
      as_of_ms: view.as_of_ms,
      structure: signalRatingClaimSummary(claims.structure),
      put_pressure: signalRatingClaimSummary(claims.put_pressure),
      call_pressure: signalRatingClaimSummary(claims.call_pressure)
    };
  }

  function signalRatingSummaryValidation(summary, doc = null) {
    const view = asObject(summary);
    const errors = [];
    if (view.schema !== SIGNAL_RATING_SCHEMA) errors.push("signal_rating_summary.schema 不匹配");
    if (view.rating_scope !== SIGNAL_RATING_SCOPE) errors.push("signal_rating_summary.rating_scope 不匹配");
    const asOfError = signalRatingAsOfCardError(view.as_of_ms, doc || {}, "signal_rating_summary", { checkExpected: false });
    if (asOfError) errors.push(asOfError);
    SIGNAL_RATING_CLAIMS.forEach(({ key }) => {
      const claim = asObject(view[key]);
      if (!Object.keys(claim).length) {
        errors.push(`signal_rating_summary.${key} 缺失`);
      } else if (!SIGNAL_RATING_STATUS_SET.has(rawEnum(claim.status).toUpperCase())) {
        errors.push(`signal_rating_summary.${key}.status 非法`);
      }
    });
    return { valid: errors.length === 0, errors };
  }

  function signalRatingValidation(doc) {
    const rating = asObject(get(doc, "signal_rating", {}));
    if (!Object.keys(rating).length) return { state: "missing", valid: false, rating: null, errors: [], warnings: [] };
    const errors = [];
    const warnings = [];
    if (rating.schema !== SIGNAL_RATING_SCHEMA) errors.push("signal_rating.schema 不匹配");
    if (rating.rating_scope !== SIGNAL_RATING_SCOPE) errors.push("signal_rating.rating_scope 不匹配");
    const asOfError = signalRatingAsOfCardError(rating.as_of_ms, doc, "signal_rating");
    if (asOfError) errors.push(asOfError);
    if (rating.candidate_quote_economics !== SIGNAL_RATING_ECONOMICS) {
      errors.push("candidate_quote_economics 必须为 not_evaluated");
    }
    const claims = asObject(rating.claims);
    SIGNAL_RATING_CLAIMS.forEach(({ key }) => {
      const claim = asObject(claims[key]);
      if (!Object.keys(claim).length) {
        errors.push(`claims.${key} 缺失`);
        return;
      }
      if (!SIGNAL_RATING_STATUS_SET.has(rawEnum(claim.status).toUpperCase())) {
        errors.push(`claims.${key}.status 非法`);
      }
      signalRatingValidateClaimShape(key, claim, errors);
    });
    const refIssues = signalRatingSourceRefIssues(rating, doc);
    if (refIssues.errors.length) errors.push(`有 ${refIssues.errors.length} 个支持/反对 source_ref 不能解析到当前原始截面`);
    if (refIssues.warnings.length) warnings.push(`有 ${refIssues.warnings.length} 个预期缺失/上下文 source_ref 未命中原始截面，已按普通标记显示`);
    return { state: errors.length ? "invalid" : "valid", valid: errors.length === 0, rating, errors, warnings };
  }

  function signalRatingValidateClaimShape(key, claim, errors) {
    const status = signalRatingStatusKey(claim.status);
    ["required_inputs", "support", "opposition", "unknowns"].forEach((field) => {
      if (!Array.isArray(claim[field])) errors.push(`claims.${key}.${field} 必须为数组`);
    });
    const requiredInputs = asArray(claim.required_inputs);
    requiredInputs.forEach((entry, index) => {
      const item = asObject(entry);
      if (!Object.keys(item).length || Array.isArray(entry)) {
        errors.push(`claims.${key}.required_inputs[${index}] 必须为对象`);
        return;
      }
      if (isBlank(item.source_ref)) errors.push(`claims.${key}.required_inputs[${index}].source_ref 缺失`);
      if (isBlank(item.source_group)) errors.push(`claims.${key}.required_inputs[${index}].source_group 缺失`);
      if (isBlank(item.status)) errors.push(`claims.${key}.required_inputs[${index}].status 缺失`);
      if (typeof item.usable !== "boolean") errors.push(`claims.${key}.required_inputs[${index}].usable 必须为布尔值`);
      if (item.usable === false && isBlank(item.reason_cn)) {
        errors.push(`claims.${key}.required_inputs[${index}].reason_cn 缺失`);
      }
    });
    if (requiredInputs.some((entry) => asObject(entry).usable === false) && status !== "INSUFFICIENT") {
      errors.push(`claims.${key}.required_inputs 不可用时 status 必须为 INSUFFICIENT`);
    }
    const supportCount = asArray(claim.support).length;
    const oppositionCount = asArray(claim.opposition).length;
    if (status === "SUPPORTED" && (!supportCount || oppositionCount)) {
      errors.push(`claims.${key}.status=SUPPORTED 必须只有支持依据`);
    }
    if (status === "CONFLICTED" && (!supportCount || !oppositionCount)) {
      errors.push(`claims.${key}.status=CONFLICTED 必须同时有支持和反对依据`);
    }
    if (status === "OPPOSED" && (!oppositionCount || supportCount)) {
      errors.push(`claims.${key}.status=OPPOSED 必须只有反对依据`);
    }
    [
      ["support", "basis_cn"],
      ["opposition", "basis_cn"],
      ["unknowns", "reason_cn"]
    ].forEach(([field, reasonField]) => {
      asArray(claim[field]).forEach((entry, index) => {
        const item = asObject(entry);
        if (!Object.keys(item).length || Array.isArray(entry)) {
          errors.push(`claims.${key}.${field}[${index}] 必须为对象`);
          return;
        }
        if (isBlank(item.source_ref)) errors.push(`claims.${key}.${field}[${index}].source_ref 缺失`);
        if (isBlank(item[reasonField])) errors.push(`claims.${key}.${field}[${index}].${reasonField} 缺失`);
      });
    });
  }

  function signalRatingDocumentState(doc) {
    const rawRating = asObject(get(doc, "signal_rating", {}));
    if (Object.keys(rawRating).length) {
      const validation = signalRatingValidation(doc);
      if (!validation.valid) {
        return { state: "invalid", rating: rawRating, summary: {}, errors: validation.errors, warnings: validation.warnings };
      }
      return {
        state: "valid",
        rating: rawRating,
        summary: signalRatingSummaryFromRating(rawRating),
        errors: [],
        warnings: validation.warnings
      };
    }
    const summary = asObject(get(doc, "signal_rating_summary", {}));
    if (Object.keys(summary).length) {
      const validation = signalRatingSummaryValidation(summary, doc);
      return validation.valid
        ? { state: "summary", rating: null, summary, errors: [], warnings: [] }
        : { state: "invalid_summary", rating: null, summary, errors: validation.errors, warnings: [] };
    }
    return { state: "missing", rating: null, summary: {}, errors: [], warnings: [] };
  }

  function signalRatingMetricText(doc, claimKey) {
    const state = signalRatingDocumentState(doc);
    if (state.state === "valid" || state.state === "summary") {
      return signalRatingStatusLabel(get(state.summary, `${claimKey}.status`));
    }
    if (state.state === "invalid" || state.state === "invalid_summary") return "评级不可用";
    return "历史未评级";
  }

  function signalRatingIndexStats(doc) {
    const ratingState = signalRatingDocumentState(doc);
    const boundary = semanticCompact(support(doc)) || "未知边界";
    const durability = signalDurability(doc);
    const durabilityStats = [
      `耐用 ${durabilityScoreText(durability)}`,
      durabilityComfortBrief(durability),
      `边界 ${boundary}`
    ];
    if (ratingState.state === "valid" || ratingState.state === "summary") {
      return SIGNAL_RATING_CLAIMS.map(({ key, label }) =>
        `${label.replace(" 信用价差环境", "")} ${signalRatingStatusLabel(get(ratingState.summary, `${key}.status`))}`
      ).concat(durabilityStats);
    }
    if (ratingState.state === "invalid" || ratingState.state === "invalid_summary") {
      return ["评级不可用", `质量 ${semanticCompact(qualityOverall(doc))}`, "候选经济性未评估", ...durabilityStats];
    }
    return ["历史未评级", `质量 ${semanticCompact(qualityOverall(doc))}`, "候选经济性未评估", ...durabilityStats];
  }

  function signalRatingSearchText(doc) {
    const ratingState = signalRatingDocumentState(doc);
    if (!(ratingState.state === "valid" || ratingState.state === "summary")) return ratingState.state;
    return SIGNAL_RATING_CLAIMS.map(({ key, label }) => [
      label,
      signalRatingStatusLabel(get(ratingState.summary, `${key}.status`)),
      get(ratingState.summary, `${key}.summary_cn`, "")
    ].join(" ")).join(" ");
  }

  function signalRatingClaim(doc, rating, key) {
    return asObject(asObject(asObject(rating).claims)[key]);
  }

  function signalRatingSourceRow(entry, doc, reasonField = "basis_cn") {
    const item = asObject(entry);
    const reason = item[reasonField] || item.basis_cn || item.reason_cn || "未说明";
    const source = item.source_ref
      ? sourceRefLink(item.source_ref, doc, item.source_group || item.source_ref)
      : `<span class="chip">${escapeHtml(item.source_group || "未给 source_ref")}</span>`;
    return `<li>${source}<p>${escapeHtml(reason)}</p></li>`;
  }

  function signalRatingEntryList(items, doc, emptyText, reasonField = "basis_cn") {
    const rows = asArray(items).map((item) => signalRatingSourceRow(item, doc, reasonField)).join("");
    return rows ? `<ul class="signal-rating-entry-list">${rows}</ul>` : `<div class="empty-inline">${escapeHtml(emptyText)}</div>`;
  }

  function signalRatingRequiredInputView(input, index) {
    const item = asObject(input);
    if (Object.keys(item).length && !Array.isArray(input)) {
      return {
        source_ref: item.source_ref,
        source_group: item.source_group,
        status: item.status,
        usable: item.usable,
        reason_cn: item.reason_cn,
        impact_cn: item.impact_cn,
        index
      };
    }
    return {
      source_ref: String(input || ""),
      source_group: "旧格式",
      status: "UNKNOWN",
      usable: null,
      reason_cn: "旧格式必要输入；仅作兼容显示，不能通过 v1 原生校验。",
      impact_cn: "",
      index
    };
  }

  function signalRatingRequiredInputs(claim, doc) {
    const values = asArray(asObject(claim).required_inputs);
    if (!values.length) return `<div class="empty-inline">必要输入未声明</div>`;
    return `<div class="signal-rating-input-list">${values.map((item, index) => {
      const input = signalRatingRequiredInputView(item, index);
      const source = input.source_ref
        ? sourceRefLink(input.source_ref, doc, input.source_group || input.source_ref)
        : `<span class="chip">${escapeHtml(input.source_group || `input ${index + 1}`)}</span>`;
      const usable = input.usable === true ? "可用" : (input.usable === false ? "不可用" : "可用性未声明");
      return `
        <div class="signal-rating-input-row ${input.usable === false ? "is-bad" : ""}">
          ${source}
          <span class="chip">${escapeHtml(semanticCompact(input.status) || "状态未声明")}</span>
          <span class="chip">${escapeHtml(usable)}</span>
          ${input.reason_cn ? `<p>${escapeHtml(input.reason_cn)}</p>` : ""}
          ${input.impact_cn ? `<p><strong>影响</strong>：${escapeHtml(input.impact_cn)}</p>` : ""}
        </div>
      `;
    }).join("")}</div>`;
  }

  function renderSignalRatingClaim(doc, title, claim) {
    const view = asObject(claim);
    const status = signalRatingStatusKey(view.status);
    return `
      <article class="signal-rating-claim ${signalRatingStatusClass(status)}">
        <div class="signal-rating-claim-head">
          <strong>${escapeHtml(title)}</strong>
          <span class="badge ${signalRatingBadgeClass(status)}">${escapeHtml(signalRatingStatusLabel(status))}</span>
        </div>
        <p>${escapeHtml(view.summary_cn || "该侧没有提供可读摘要。")}</p>
        <details class="signal-rating-detail">
          <summary>证据、反证与未知</summary>
          <div class="signal-rating-inputs">${signalRatingRequiredInputs(view, doc)}</div>
          <h3 class="subsection-title">支持依据</h3>
          ${signalRatingEntryList(view.support, doc, "无支持依据")}
          <h3 class="subsection-title">反对依据</h3>
          ${signalRatingEntryList(view.opposition, doc, "无反对依据")}
          <h3 class="subsection-title">未知项</h3>
          ${signalRatingEntryList(view.unknowns, doc, "无未知项", "reason_cn")}
        </details>
      </article>
    `;
  }

  function signalRatingRefValue(entry) {
    if (typeof entry === "string" || typeof entry === "number") return String(entry);
    const view = asObject(entry);
    return firstTextValue(view.source_ref, view.ref, view.path, "");
  }

  function signalRatingUsedSourceRefs(rating) {
    const claims = asObject(asObject(rating).claims);
    const refs = [
      ...asArray(get(rating, "context.source_refs", [])).map(signalRatingRefValue)
    ];
    SIGNAL_RATING_CLAIMS.forEach(({ key }) => {
      const claim = asObject(claims[key]);
      asArray(claim.required_inputs).forEach((entry) => {
        const ref = asObject(entry).source_ref;
        if (!isBlank(ref)) refs.push(ref);
      });
      ["support", "opposition", "unknowns"].forEach((field) => {
        asArray(claim[field]).forEach((entry) => {
          const ref = asObject(entry).source_ref;
          if (!isBlank(ref)) refs.push(ref);
        });
      });
    });
    return [...new Set(refs.map((ref) => String(ref)).filter(Boolean))];
  }

  function signalRatingSourceRefIssues(rating, doc) {
    const claims = asObject(asObject(rating).claims);
    const hardRefs = [];
    const softRefs = [
      ...asArray(get(rating, "context.source_refs", [])).map(signalRatingRefValue)
    ];
    SIGNAL_RATING_CLAIMS.forEach(({ key }) => {
      const claim = asObject(claims[key]);
      asArray(claim.required_inputs).forEach((entry) => {
        const ref = asObject(entry).source_ref;
        if (!isBlank(ref)) softRefs.push(String(ref));
      });
      asArray(claim.unknowns).forEach((entry) => {
        const ref = asObject(entry).source_ref;
        if (!isBlank(ref)) softRefs.push(String(ref));
      });
      ["support", "opposition"].forEach((field) => {
        asArray(claim[field]).forEach((entry) => {
          const ref = asObject(entry).source_ref;
          if (!isBlank(ref)) hardRefs.push(String(ref));
        });
      });
    });
    const unresolvedHard = [...new Set(hardRefs)].filter((ref) => !hasRawTraceTarget(ref, doc));
    const unresolvedSoft = [...new Set(softRefs)]
      .filter((ref) => !hasRawTraceTarget(ref, doc))
      .filter((ref) => !unresolvedHard.includes(ref));
    return { errors: unresolvedHard, warnings: unresolvedSoft };
  }

  function renderSignalRatingKeyOpposition(rating, doc) {
    const claims = asObject(asObject(rating).claims);
    const rows = [];
    for (const { key, label } of SIGNAL_RATING_CLAIMS) {
      const item = asObject(asArray(asObject(claims[key]).opposition)[0]);
      if (!Object.keys(item).length) continue;
      rows.push({ label, item });
    }
    if (!rows.length) return `<p>评级对象内未识别到反对依据。</p>`;
    return `<ul class="plain-list signal-rating-opposition-list">${rows.map(({ label, item }) => `
      <li><strong>${escapeHtml(label)}</strong>${item.source_ref ? sourceRefLink(item.source_ref, doc, item.source_group || item.source_ref) : ""}<p>${escapeHtml(item.basis_cn || item.reason_cn || "存在反对依据")}</p></li>
    `).join("")}</ul><p class="signal-rating-order-note">按主张顺序展示，每项取首条反对依据；未声明强弱排序。</p>`;
  }

  function signalRatingUnknownList(rating, doc) {
    const rows = [];
    const claims = asObject(asObject(rating).claims);
    SIGNAL_RATING_CLAIMS.forEach(({ key, label }) => {
      asArray(asObject(claims[key]).unknowns).forEach((item) => {
        const view = asObject(item);
        rows.push({
          label,
          source_ref: view.source_ref,
          reason_cn: view.reason_cn || "未知原因未说明"
        });
      });
    });
    if (!rows.length) return `<div class="empty-inline">当前评级没有声明未知项。</div>`;
    return `<ul class="plain-list signal-rating-unknown-list">${rows.slice(0, 6).map((row) => `
      <li><strong>${escapeHtml(row.label)}</strong>${row.source_ref ? sourceRefLink(row.source_ref, doc) : ""}<p>${escapeHtml(row.reason_cn)}</p></li>
    `).join("")}</ul>`;
  }

  function signalRatingAsOfText(rating) {
    const ms = numericMs(asObject(rating).as_of_ms);
    if (ms !== null && ms > 0) return dateText(isoFromEpochMs(ms));
    const iso = firstPresent(rating.as_of, rating.as_of_iso, rating.observed_at);
    return isBlank(iso) ? "未提供评级时点" : dateText(iso);
  }

  function signalRatingMarketStateText(rating) {
    const market = asObject(asObject(rating).market_state);
    const legacy = rawEnum(market.legacy_label);
    const items = [];
    if (/Anchor\s+Mean-Reversion/i.test(legacy)) {
      items.push("旧 Anchor Mean-Reversion 在本页按“TMVF 方向中性，回归尚未证明”解释。");
    } else if (!isBlank(legacy)) {
      items.push(`旧标签：${semanticCompact(legacy) || legacy}。`);
    }
    if (!isBlank(market.interpretation_cn)) items.push(String(market.interpretation_cn));
    if (rawEnum(market.mean_reversion_validation).toLowerCase() === "not_established") {
      items.push("锚回归有效性尚未由评级时点之前的证据建立。");
    }
    if (market.trend_acceleration_available === false) {
      items.push("Trend Acceleration 未产出，不作为本卡检测结果。");
    }
    return items.length ? items.join(" ") : "未提供额外市场状态解释。";
  }

  function signalRatingContextLine(rating) {
    const context = asObject(asObject(rating).context);
    const windowText = isBlank(context.nr_state) ? "未声明" : nrStateText(context.nr_state);
    return `窗口：${windowText}；未来有效时长：${futureValidityText(context.future_validity)}`;
  }

  function signalRatingBoundaryView(doc) {
    const blocking = asObject(get(doc, "blocking", {}));
    const matrix = asObject(get(doc, "decision_matrix", {}));
    const currentDecision = decision(doc);
    return {
      support_label: firstTextValue(currentDecision.support_label, support(doc), matrix.support_label, matrix.model_trade_support, "UNKNOWN"),
      side_hint: firstTextValue(currentDecision.side_hint, matrix.side_hint, matrix.trade_side, matrix.model_trade_side, "UNKNOWN"),
      has_block: boundaryHasBlock(blocking.has_block, matrix.has_block, currentDecision.has_block),
      hard_veto: firstTextValue(blocking.hard_veto, matrix.hard_veto, currentDecision.hard_veto, null),
      execution_allowed: boundaryExecutionAllowed(matrix.execution_allowed, currentDecision.execution_allowed)
    };
  }

  function signalRatingConstraintMismatches(boundary, rating) {
    const constraints = asObject(asObject(rating).model_constraints);
    if (!Object.keys(constraints).length) return [];
    const checks = [
      ["support_label", "Support"],
      ["side_hint", "Side"],
      ["has_block", "Block"],
      ["hard_veto", "Hard veto"],
      ["execution_allowed", "Execution"]
    ];
    return checks
      .filter(([key]) => constraints[key] !== undefined && constraints[key] !== boundary[key])
      .map(([key, label]) => `${label}: 原字段 ${scalarText(boundary[key], { translate: false })} / 评级副本 ${scalarText(constraints[key], { translate: false })}`);
  }

  function renderSignalRatingBoundary(doc, rating) {
    const boundary = signalRatingBoundaryView(doc);
    const mismatches = rating ? signalRatingConstraintMismatches(boundary, rating) : [];
    const execution = boundary.execution_allowed === true ? "原字段显示允许" : "只读/未授权";
    return `
      <div class="signal-rating-boundary">
        <strong>原机器边界</strong>
        <div class="signal-rating-boundary-badges">
          ${statusBadge("Support", boundary.support_label)}
          ${statusBadge("Side", boundary.side_hint)}
          ${statusBadge("Block", boundary.has_block === true ? "BLOCKED" : "OK")}
          ${statusBadge("Execution", execution)}
          ${boundary.hard_veto ? statusBadge("Hard veto", boundary.hard_veto) : `<span class="badge is-good">硬否决: 无</span>`}
        </div>
        <p>评级只解释环境和侧别支持，不改变 WAIT、hard block、NR、trigger 或执行许可。原字段是权威来源，评级内 model_constraints 只做一致性核对。</p>
        ${rating ? `<details class="signal-rating-constraint-check"><summary>评级副本核对</summary>${mismatches.length ? `<ul class="plain-list">${mismatches.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : `<p>评级副本与原 decision / blocking / decision_matrix 一致。</p>`}</details>` : ""}
      </div>
    `;
  }

  function renderSignalRatingMetadataDetails(rating, doc) {
    const context = asObject(asObject(rating).context);
    const replay = asObject(get(doc, "provenance.research_replay", {}));
    const trigger = firstPresent(context.trigger, context.trigger_state, context.trigger_reason, rating.trigger);
    const sourcePath = firstPresent(context.producer_source_path, context.source_path, context.code_path, rating.producer_source_path);
    const round = firstPresent(context.analysis_round, rating.analysis_round);
    const refs = signalRatingUsedSourceRefs(rating);
    return `
      <details class="signal-rating-metadata">
        <summary>评级证据元数据</summary>
        <dl class="kv-grid">
          ${kv("schema", rating.schema, { translate: false })}
          ${kv("rating_scope", signalRatingScopeText(rating.rating_scope), { translate: false })}
          ${kv("as_of_ms", rating.as_of_ms, { translate: false })}
          ${kv("episode_id", context.episode_id, { translate: false, nullText: "未提供", nullClass: "benign-null-value" })}
          ${kv("trigger", trigger, { translate: false, nullText: "未提供", nullClass: "benign-null-value" })}
          ${kv("nr_state", context.nr_state, { translate: false, nullText: "未提供", nullClass: "benign-null-value" })}
          ${kv("nr_active", context.nr_active, { translate: false, nullText: "未提供", nullClass: "benign-null-value" })}
          ${kv("future_validity", context.future_validity, { translate: false, nullText: "未提供", nullClass: "benign-null-value" })}
          ${kv("analysis_round", round, { translate: false, nullText: "未提供", nullClass: "benign-null-value" })}
          ${kv("producer_source_path", sourcePath, { translate: false, nullText: "未提供", nullClass: "benign-null-value" })}
          ${kv("candidate_quote_economics", rating.candidate_quote_economics)}
          ${kv("research_replay.source_card_id", replay.source_card_id, { translate: false, nullText: "未提供", nullClass: "benign-null-value" })}
          ${kv("research_replay.source_record_hash", replay.source_record_hash, { translate: false, nullText: "未提供", nullClass: "benign-null-value" })}
        </dl>
        ${refs.length ? `<div class="source-ref-row signal-rating-source-row"><span class="chip">评级来源</span>${sourceRefList(refs, doc)}</div>` : ""}
      </details>
    `;
  }

  function renderSignalRatingResearchReplayNotice(doc) {
    const replay = asObject(get(doc, "provenance.research_replay", {}));
    if (!Object.keys(replay).length) return "";
    const notice = replay.notice_cn || "离线重评，不是当时原生评级。";
    return `
      <div class="signal-rating-replay-notice">
        <strong>研究重放边界</strong>
        <p>${escapeHtml(notice)}</p>
      </div>
    `;
  }

  function renderSignalRatingInvalid(doc, state) {
    return section("信号评级｜环境与侧别支持", "producer 原生评级；格式不合法时隔离显示，不前端补造结论。", `
      <div class="signal-rating-panel is-unavailable">
        <div class="signal-rating-topline">
          <div>
            <span class="signal-rating-kicker">评级不可用</span>
            <strong>检测到 signal_rating 对象，但格式未通过 v1 校验。</strong>
            <p>${escapeHtml(asArray(state.errors).join("；") || "未知格式问题。")}</p>
          </div>
          <span class="badge is-bad">不可作为评级阅读</span>
        </div>
        ${renderSignalRatingResearchReplayNotice(doc)}
        ${renderSignalRatingBoundary(doc, null)}
      </div>
    `, "signal-rating");
  }

  function renderSignalRatingMissing(doc) {
    return section("信号评级｜环境与侧别支持", "旧卡只说明历史边界，不回填新评级。", `
      <div class="signal-rating-panel is-history">
        <div class="signal-rating-topline">
          <div>
            <span class="signal-rating-kicker">历史版本未评级</span>
            <strong>这张卡没有原生环境评级。</strong>
            <p>页面保留已有市场事实、证据账本、LLM 和阻断边界；不会把旧分数换算成新评级。</p>
          </div>
          <span class="badge is-wait">环境评级缺失</span>
        </div>
        ${renderSignalRatingBoundary(doc, {})}
      </div>
    `, "signal-rating");
  }

  function renderSignalRating(doc) {
    const state = signalRatingDocumentState(doc);
    if (state.state === "missing") return renderSignalRatingMissing(doc);
    if (state.state === "invalid" || state.state === "invalid_summary") return renderSignalRatingInvalid(doc, state);
    if (state.state === "summary") {
      return section("信号评级｜环境与侧别支持", "已加载摘要；完整证据打开卡后展示。", `
        <div class="signal-rating-panel is-history">
          <div class="signal-rating-topline">
            <div>
              <span class="signal-rating-kicker">评级摘要</span>
              <strong>完整环境评级尚未加载。</strong>
              <p>打开这张卡后可查看支持、反对、未知和来源说明。</p>
            </div>
          </div>
          ${renderSignalRatingResearchReplayNotice(doc)}
          <div class="signal-rating-claims">
            ${SIGNAL_RATING_CLAIMS.map(({ key, label }) => renderSignalRatingClaim(doc, label, asObject(state.summary[key]))).join("")}
          </div>
        </div>
      `, "signal-rating");
    }
    const rating = state.rating;
    const claims = SIGNAL_RATING_CLAIMS.map(({ key, label }) =>
      renderSignalRatingClaim(doc, label, signalRatingClaim(doc, rating, key))).join("");
    const nextObservations = asArray(rating.next_observations_cn);
    return section("信号评级｜环境与侧别支持", "把当前信号解释成末日垂直信用价差的环境支持、反对和未知；不评估两腿报价或净补偿。", `
      <div class="signal-rating-panel">
        <div class="signal-rating-topline">
          <div>
            <span class="signal-rating-kicker">${escapeHtml(signalRatingScopeText(rating.rating_scope))}</span>
            <strong>Put / Call 信用价差分别阅读，不合成胜率。</strong>
            <p>评级时点：${escapeHtml(signalRatingAsOfText(rating))}；${escapeHtml(signalRatingContextLine(rating))}。</p>
          </div>
          <div class="signal-rating-economics">
            <span>候选经济性</span>
            <strong>未评估</strong>
            <p>环境支持不能代替两腿、报价、费用、DTE、净补偿与退出条件。</p>
          </div>
        </div>
        ${renderSignalRatingResearchReplayNotice(doc)}
        <div class="signal-rating-claims">${claims}</div>
        <div class="signal-rating-meta-grid">
          <div>
            <span>市场状态校正</span>
            <p>${escapeHtml(signalRatingMarketStateText(rating))}</p>
          </div>
          <div>
            <span>关键反对理由</span>
            ${renderSignalRatingKeyOpposition(rating, doc)}
          </div>
          <div>
            <span>未知项</span>
            ${signalRatingUnknownList(rating, doc)}
          </div>
          <div>
            <span>下一观察条件</span>
            ${nextObservations.length ? `<ul class="plain-list">${nextObservations.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : `<p>未声明下一观察。</p>`}
          </div>
        </div>
        ${state.warnings.length ? `<p class="signal-rating-warning">${escapeHtml(state.warnings.join("；"))}</p>` : ""}
        ${renderSignalRatingBoundary(doc, rating)}
        ${renderSignalRatingMetadataDetails(rating, doc)}
      </div>
    `, "signal-rating");
  }

  function evidenceContext(evidence, doc) {
    return {
      key: evidenceKey(evidence),
      detail: asObject(evidence && evidence.detail),
      raw: evidenceRawValues(evidence, doc),
      factor: factorNodeForEvidence(doc, evidence)
    };
  }

  function evidenceFirstValue(evidence, ctx, fields) {
    return firstFromObjects([ctx.raw, ctx.detail, evidence, ctx.factor], fields);
  }

  function evidenceFirstNumber(evidence, ctx, fields) {
    return firstNumberFrom([ctx.raw, ctx.detail, evidence, ctx.factor], fields);
  }

  function pairSymbol(doc) {
    const base = rawEnum(symbol(doc)).toUpperCase();
    const quote = rawEnum(get(doc, "market_context.quote_currency", "USDT")).toUpperCase();
    if (!base || base === "N/A") return "BTCUSDT";
    return base.endsWith(quote) ? base : `${base}${quote || "USDT"}`;
  }

  function ratePctText(rate) {
    const numeric = safeNumber(rate);
    return numeric === null ? "暂缺" : `${number(numeric * 100, 4)}%`;
  }

  function evidenceFact(label, value) {
    if (isNullish(value) || value === "") return "";
    const normalized = typeof value === "boolean"
      ? (value ? "是" : "否")
      : normalizeComfortText(semanticCompact(value), String(value));
    const text = textClip(normalized);
    return `<span class="evidence-fact"><span>${escapeHtml(label)}</span><strong title="${escapeHtml(text)}">${escapeHtml(text)}</strong></span>`;
  }

  function macroComponentLabel(component) {
    const key = rawEnum(component && component.key).toUpperCase();
    if (key === "VOLQ" || key === "VXN" || key === "VIX") return `纳斯达克代理(${key})`;
    if (key === "DXY") return "美元(DXY)";
    if (key === "US10Y" || key === "TNX") return "美债10Y(US10Y)";
    return key || "宏观代理";
  }

  function macroComponentValue(component) {
    const item = asObject(component);
    const bps = firstNumberFrom([item], ["scoring_bps", "change_bps", "change_3d_bps"]);
    if (bps !== null) return `${number(bps, 1)} bp`;
    const pct = safeNumber(item.change_pct_3d);
    if (pct !== null) return `${number(pct * 100, 2)}%`;
    return firstPresent(item.source_symbol, item.current_symbol, item.key, "n/a");
  }

  function macroComponentFacts(ctx) {
    const components = asArray(firstPresent(ctx.raw.components, ctx.factor.components, ctx.raw.component_scores, ctx.factor.component_scores));
    return components
      .filter((component) => Object.keys(asObject(component)).length)
      .map((component) => evidenceFact(macroComponentLabel(component), macroComponentValue(component)));
  }

  function macroDirectionBackgroundText(regime, score, leanText) {
    const scoreText = scalarText(score, { translate: false, digits: 4 });
    const regimeText = normalizeComfortText(semanticCompact(regime), "未知");
    return `${regimeText} / ${leanText} / 压力分数 ${scoreText}`;
  }

  function macroShockObject(ctx) {
    return asObject(firstPresent(ctx.raw.macro_shock, ctx.detail.macro_shock, ctx.factor.macro_shock));
  }

  function macroShockGateText(ctx) {
    const shock = macroShockObject(ctx);
    if (!Object.keys(shock).length) return "历史卡未提供冲击限制状态";
    const state = semanticCompact(shock.state || (shock.block ? "BLOCK" : "CLEAR")) || "未知";
    const delta = safeNumber(shock.volq_bps_delta);
    const deltaText = delta === null ? "VOLQ delta 暂缺" : `VOLQ ${number(delta, 1)}bp`;
    const confirmed = shock.direction_confirmed === true
      ? "DXY/US10Y 确认"
      : (shock.direction_confirmed === false ? "未获方向确认" : "确认状态暂缺");
    return `${state} / ${deltaText} / ${confirmed}`;
  }

  function evidenceFactsHtml(facts) {
    const html = facts.filter(Boolean).join("");
    return html ? `<div class="evidence-facts">${html}</div>` : `<div class="empty-inline">无关键事实</div>`;
  }

  function evidenceImpactHtml(evidence) {
    const share = isNullish(evidence.absolute_share_pct) ? "n/a" : `${number(evidence.absolute_share_pct, 2)}%`;
    const configured = scalarText(evidence.configured_weight, { translate: false, digits: 4 });
    const effective = scalarText(evidence.effective_weight, { translate: false, digits: 4 });
    const weighted = scalarText(evidence.weighted_contribution, { translate: false, digits: 4 });
    return `
      <div class="evidence-impact">
        <span><em>vote</em><strong>${escapeHtml(scalarText(evidence.vote, { translate: false, digits: 4 }))}</strong></span>
        <span><em>weight</em><strong>${escapeHtml(`${configured} / ${effective}`)}</strong></span>
        <span><em>contrib</em><strong>${escapeHtml(weighted)}</strong></span>
        <span><em>share</em><strong>${escapeHtml(share)}</strong></span>
      </div>
    `;
  }

  function canonicalFundingSemantics(evidence, doc, ctx) {
    const candidates = [
      asObject(ctx && ctx.detail && ctx.detail.canonical_funding_semantics),
      asObject(ctx && ctx.raw && ctx.raw.canonical_funding_semantics),
      asObject(evidence && evidence.canonical_funding_semantics),
      asObject(ctx && ctx.factor && ctx.factor.canonical_funding_semantics),
      asObject(get(doc, "factor_cross_section.funding.canonical_funding_semantics", {})),
      asObject(ctx && ctx.detail && ctx.detail.funding_semantics),
      asObject(ctx && ctx.raw && ctx.raw.funding_semantics),
      asObject(get(doc, "factor_cross_section.funding.funding_semantics", {}))
    ];
    return candidates.find((item) =>
      Object.keys(item).length
      && typeof item.canonical_text_cn === "string"
      && item.canonical_text_cn.trim()) || {};
  }

  function fundingAssessment(evidence, doc, ctx) {
    const semantics = canonicalFundingSemantics(evidence, doc, ctx);
    const canonicalText = String(semantics.canonical_text_cn || "").trim();
    if (!canonicalText) {
      return {
        stanceLabel: "费率规范语义",
        stance: "无法判断",
        sentence: "资金费率规范语义缺失，无法判断；本卡只保留已能确认的拥挤背景。",
        facts: [
          evidenceFact("语义状态", "无法判断"),
          evidenceFact("证据账本", "不参与方向判断")
        ]
      };
    }
    const ratePct = safeNumber(semantics.raw_funding_rate_pct);
    const thresholdPct = safeNumber(semantics.crowding_threshold_pct);
    return {
      stanceLabel: "费率规范语义",
      stance: semantics.fee_bias_cn || semanticCompact(semantics.fee_bias) || "无法判断",
      sentence: normalizeComfortText(canonicalText, "资金费率规范语义已记录。"),
      facts: [
        evidenceFact("资金费率", ratePct === null ? "暂缺" : `${number(ratePct, 4)}%`),
        evidenceFact("拥挤阈值", thresholdPct === null ? "0.01%" : `${number(thresholdPct, 4)}%`),
        evidenceFact("拥挤状态", semanticCompact(semantics.crowding_state) || semantics.crowding_state),
        evidenceFact("反身性", semanticCompact(semantics.reflexivity_importance) || semantics.reflexivity_importance),
        evidenceFact("证据账本", semanticCompact(semantics.edb_participation) || semantics.edb_participation)
      ]
    };
  }

  function srdAssessment(evidence, ctx) {
    const vote = evidenceFirstNumber(evidence, ctx, ["vote"]);
    const rrBlend = evidenceFirstNumber(evidence, ctx, ["rr_blend"]);
    const rrZ = evidenceFirstNumber(evidence, ctx, ["rr_z"]);
    const deltaRr = evidenceFirstNumber(evidence, ctx, ["delta_rr"]);
    const leanText = semanticCompact(evidence.lean || evidenceAuxiliaryLean(evidence, { factor_cross_section: { skew: ctx.factor } })) || "UNKNOWN";
    return {
      stance: leanText,
      sentence: `期权斜率数值 ${scalarText(vote, { translate: false, digits: 4 })}，风险逆转 ${scalarText(rrBlend, { translate: false, digits: 4 })}，当前判断为${leanText}。`,
      facts: [
        evidenceFact("风险逆转", rrBlend),
        evidenceFact("相对位置", rrZ),
        evidenceFact("变化", deltaRr)
      ]
    };
  }

  function ggrAssessment(evidence, ctx) {
    const regime = evidenceFirstValue(evidence, ctx, ["regime", "market_state"]);
    const veto = evidenceFirstValue(evidence, ctx, ["veto"]);
    const gammaNotional = evidenceFirstNumber(evidence, ctx, ["net_gamma_notional_usd", "net_gamma_notional"]);
    const netGammaFact = gammaNotional !== null && Math.abs(gammaNotional) >= 1000 ? durabilityUsdText(gammaNotional) : null;
    const gammaProxyFact = gammaNotional !== null && Math.abs(gammaNotional) < 1000
      ? `${gammaNotional > 0 ? "正向" : (gammaNotional < 0 ? "负向" : "中性")} ${number(gammaNotional, 4)}`
      : null;
    const flipDistance = evidenceFirstNumber(evidence, ctx, ["distance_to_flip_pct"]);
    const pinDistance = evidenceFirstNumber(evidence, ctx, ["distance_to_pin_pct"]);
    const stanceCode = evidenceAuxiliaryLean(evidence, { factor_cross_section: { gamma_regime: ctx.factor } });
    const stance = semanticCompact(stanceCode) || "空间约束";
    return {
      stanceLabel: "空间状态",
      stance,
      sentence: `Gamma 结构 ${normalizeComfortText(semanticCompact(regime), "未说明")}，这是期权空间安全与当前限制，不作为偏多或偏空方向依据。`,
      facts: [
        evidenceFact("净 Gamma", netGammaFact),
        evidenceFact("Gamma 代理", gammaProxyFact),
        evidenceFact("翻转点", evidenceFirstValue(evidence, ctx, ["flip_point"])),
        evidenceFact("距翻转点", flipDistance === null ? null : pctPoint(flipDistance)),
        evidenceFact("距钉住位", pinDistance === null ? null : pctPoint(pinDistance)),
        evidenceFact("空间作用", stance),
        evidenceFact("否决", veto)
      ]
    };
  }

  function tmvAssessment(evidence, ctx) {
    const blend = evidenceFirstNumber(evidence, ctx, ["tmv_blend"]);
    const windowConflict = evidenceFirstValue(evidence, ctx, ["window_conflict"]);
    const direction = evidenceFirstValue(evidence, ctx, ["direction"]);
    const leanText = semanticCompact(evidence.lean || signedLean(blend)) || "UNKNOWN";
    return {
      stance: leanText,
      sentence: `量价主干强度 ${scalarText(blend, { translate: false, digits: 4 })}，窗口冲突 ${normalizeComfortText(semanticCompact(windowConflict), "未说明")}，作为主方向骨架。`,
      facts: [
        evidenceFact("主干强度", blend),
        evidenceFact("路径方向", direction),
        evidenceFact("24小时", evidenceFirstValue(evidence, ctx, ["tmvf_24h_final"])),
        evidenceFact("48小时", evidenceFirstValue(evidence, ctx, ["tmvf_48h_final"]))
      ]
    };
  }

  function flowAssessment(evidence, ctx) {
    const combinedVote = evidenceFirstNumber(evidence, ctx, ["combined_vote", "vote"]);
    const agreement = evidenceFirstValue(evidence, ctx, ["agreement"]);
    const absorption = evidenceFirstValue(evidence, ctx, ["absorption_state"]);
    const fast = asObject(evidenceFirstValue(evidence, ctx, ["fast_4h"]));
    const slow = asObject(evidenceFirstValue(evidence, ctx, ["slow_12h"]));
    const leanText = semanticCompact(evidence.lean || signedLean(combinedVote)) || "UNKNOWN";
    return {
      stance: leanText,
      sentence: `主动流合成值 ${scalarText(combinedVote, { translate: false, digits: 4 })}，一致性 ${normalizeComfortText(semanticCompact(agreement), "未说明")}，用于确认或削弱主方向。`,
      facts: [
        evidenceFact("吸收关系", absorption),
        evidenceFact("快窗", fast.verdict),
        evidenceFact("慢窗", slow.verdict),
        evidenceFact("数据状态", evidenceFirstValue(evidence, ctx, ["data_quality", "data_status"]))
      ]
    };
  }

  function macroAssessment(evidence, ctx) {
    const score = firstNumberFrom([ctx.raw, ctx.detail, ctx.factor], ["macro_score", "score"]);
    const regime = evidenceFirstValue(evidence, ctx, ["macro_regime", "regime"]);
    const leanText = semanticCompact(evidence.lean || signedLean(evidence.vote) || macroLeanFromScore(score)) || "UNKNOWN";
    const voteMissing = isNullish(evidence.vote);
    const directionBackground = macroDirectionBackgroundText(regime, score, leanText);
    const shockGate = macroShockGateText(ctx);
    return {
      stance: leanText,
      sentence: `宏观背景 ${directionBackground}；冲击限制 ${shockGate}；分数 ${scalarText(score, { translate: false, digits: 4 })}。正值表示风险资产逆风。${voteMissing ? "本轮未作为方向依据，不代表宏观数据缺失。" : ""}`,
      facts: [
        evidenceFact("方向背景", directionBackground),
        evidenceFact("冲击限制", shockGate),
        evidenceFact("背景状态", regime),
        evidenceFact("压力分数", score),
        ...macroComponentFacts(ctx)
      ]
    };
  }

  function cvdAssessment(evidence, ctx) {
    const cvdSum = evidenceFirstNumber(evidence, ctx, ["cvd_sum"]);
    const strength = evidenceFirstValue(evidence, ctx, ["normalized_strength", "strength"]);
    const verdict = evidenceFirstValue(evidence, ctx, ["verdict"]);
    const strengthText = semanticCompact(strength) || "暂缺";
    const verdictText = semanticCompact(verdict) || "无法判断";
    const participation = semanticCompact(evidence.participation_status) || "未定";
    const active = rawEnum(evidence.participation_status).toUpperCase() === "ACTIVE";
    const leanText = semanticCompact(evidence.lean || signedLean(evidence.vote)) || "UNKNOWN";
    return {
      stance: leanText,
      sentence: `CVD 分量 ${scalarText(cvdSum, { translate: false, digits: 4 })}，强度 ${strengthText}，象限关系为${verdictText}。${active ? "该证据本次作为方向依据。" : "该证据未激活，本次未作为方向依据或冲突来源。"}`,
      facts: [
        evidenceFact("CVD 合计", cvdSum),
        evidenceFact("强度", strengthText),
        evidenceFact("象限关系", verdictText),
        evidenceFact("方向依据状态", participation)
      ]
    };
  }

  function defaultEvidenceAssessment(evidence, ctx) {
    const leanText = semanticCompact(evidence.lean || evidenceAuxiliaryLean(evidence, { factor_cross_section: {} })) || "UNKNOWN";
    return {
      stance: leanText,
      sentence: `${evidenceDisplayName(evidence)}已记录，当前只作为中文证据摘要展示。`,
      facts: [
        evidenceFact("来源", sourceRefLabel(evidence.source_ref)),
        evidenceFact("作用", evidenceAuxiliaryRole(evidence))
      ]
    };
  }

  function evidenceDisplayName(evidence) {
    const key = evidenceKey(evidence);
    const labels = {
      FUNDING: "资金费率",
      SRD: "期权偏斜",
      GGR_SPATIAL: "Gamma 空间结构",
      TMV: "量价主干",
      FLOW_CONFIRM: "主动买卖流确认",
      MACRO: "宏观背景",
      CVD_4H: "CVD 四小时",
      CVD_12H: "CVD 十二小时"
    };
    return labels[key] || asObject(evidence).gloss_cn || "证据模块";
  }

  function evidenceAssessment(evidence, doc) {
    const ctx = evidenceContext(evidence, doc);
    if (ctx.key === "FUNDING") return fundingAssessment(evidence, doc, ctx);
    if (ctx.key === "SRD") return srdAssessment(evidence, ctx);
    if (ctx.key === "GGR_SPATIAL") return ggrAssessment(evidence, ctx);
    if (ctx.key === "TMV") return tmvAssessment(evidence, ctx);
    if (ctx.key === "FLOW_CONFIRM") return flowAssessment(evidence, ctx);
    if (ctx.key === "MACRO") return macroAssessment(evidence, ctx);
    if (ctx.key === "CVD_4H" || ctx.key === "CVD_12H") return cvdAssessment(evidence, ctx);
    return defaultEvidenceAssessment(evidence, ctx);
  }

  function evidenceLedgerItem(evidence, doc) {
    const status = rawEnum(evidence.participation_status).toLowerCase() || "unknown";
    const assessment = evidenceAssessment(evidence, doc);
    return `
      <article class="evidence-item participation-${escapeHtml(status)}">
        <div class="evidence-item-head">
          <div class="evidence-title">
            <strong class="evidence-key">${escapeHtml(evidenceDisplayName(evidence))}</strong>
            <span class="evidence-gloss">${escapeHtml(evidence.gloss_cn || "")}</span>
          </div>
          <div class="evidence-status">${statusBadgeCn("", evidence.participation_status)}</div>
        </div>
        <div class="evidence-body">
          <div class="evidence-stance">
            <span>${escapeHtml(assessment.stanceLabel || "模块倾向")}</span>
            <strong>${escapeHtml(assessment.stance)}</strong>
          </div>
          <p class="evidence-judgement">${escapeHtml(assessment.sentence)}</p>
          ${evidenceFactsHtml(assessment.facts)}
          <div class="evidence-foot">
            <span>${escapeHtml(semanticCompact(evidenceAuxiliaryRole(evidence)) || "证据作用已记录")}</span>
            ${evidence.source_ref ? sourceRefLink(evidence.source_ref, doc) : `<span>来源已随完整资料保存</span>`}
            ${evidence.exclusion_reason ? `<span>${escapeHtml(textClip(normalizeComfortText(evidence.exclusion_reason, ""), 96))}</span>` : ""}
          </div>
        </div>
      </article>
    `;
  }

  function renderReasoning(doc) {
    const reasoning = asObject(get(doc, "reasoning", {}));
    const evidenceRows = asArray(reasoning.evidence)
      .map((evidence) => evidenceLedgerItem(evidence, doc))
      .join("");
    return section("证据账本摘要", normalizeComfortText(reasoning.summary_cn, "保留关键证据，以及它们为什么被采纳或排除。"), `
      <div class="evidence-ledger">${evidenceRows || `<div class="empty-inline">暂无证据行</div>`}</div>
    `);
  }

  function renderConflict(doc) {
    const conflict = asObject(get(doc, "conflict", {}));
    const dominant = asObject(conflict.dominant_conflict);
    const explanation = normalizeComfortText(firstPresent(
      conflict.explanation_cn,
      dominant.explanation_cn
    ), "本卡未提供可读冲突解释。");
    return section("冲突解释", "说明主要分歧、影响方向和需要继续观察的条件。", `
      <div class="text-block"><p>${escapeHtml(explanation)}</p></div>
    `);
  }

  function flatten(value, prefix = "", depth = 0, rows = []) {
    if (depth > 3 || isNullish(value) || typeof value !== "object") {
      rows.push([prefix || "value", value]);
      return rows;
    }
    if (Array.isArray(value)) {
      if (!value.length) rows.push([prefix || "value", []]);
      value.forEach((item, index) => flatten(item, `${prefix}[${index}]`, depth + 1, rows));
      return rows;
    }
    const entries = Object.entries(value);
    if (!entries.length) rows.push([prefix || "value", {}]);
    entries.forEach(([key, child]) => flatten(child, prefix ? `${prefix}.${key}` : key, depth + 1, rows));
    return rows;
  }

  function marketFact(label, value, options = {}) {
    if (isNullish(value) || value === "") return "";
    const text = typeof value === "boolean"
      ? booleanText(value)
      : (typeof value === "number"
        ? number(value, options.digits ?? 4)
        : normalizeComfortText(semanticCompact(value), String(value)));
    return `<span class="evidence-fact"><span>${escapeHtml(label)}</span><strong>${escapeHtml(text)}</strong></span>`;
  }

  function marketFactCard(id, title, summary, facts) {
    const factHtml = facts.filter(Boolean).join("");
    return `
      <article id="${escapeHtml(id)}" class="market-fact-card">
        <h3>${escapeHtml(title)}</h3>
        <p>${escapeHtml(summary)}</p>
        ${factHtml ? `<div class="evidence-facts">${factHtml}</div>` : `<div class="empty-inline">本卡未提供该类事实。</div>`}
      </article>
    `;
  }

  function observedOrAgeText(doc, node) {
    const source = qualitySourceView(doc, "price", node);
    const parts = [];
    if (source.observed_at) parts.push(`观测 ${dateText(source.observed_at)}`);
    if (!isNullish(source.age_ms)) parts.push(`年龄 ${ageText(source.age_ms)}`);
    return parts.join("；") || "";
  }

  function directObservedOrAgeText(node) {
    const view = asObject(node);
    const parts = [];
    if (view.observed_at) parts.push(`观测 ${dateText(view.observed_at)}`);
    const age = numericMs(firstPresent(view.age_ms, view.data_age_ms, view.source_age_ms));
    if (!isNullish(age)) parts.push(`年龄 ${ageText(age)}`);
    return parts.join("；") || "";
  }

  function optionStructureFacts(doc) {
    const gamma = asObject(get(doc, "factor_cross_section.gamma_regime", {}));
    const gex = asObject(get(doc, "factor_cross_section.gex_info", {}));
    const anchor = asObject(get(doc, "factor_cross_section.anchor", {}));
    const regime = firstPresent(gamma.regime, gex.market_state, anchor.regime);
    const pin = firstPresent(gamma.pin_strike, get(gamma, "pin.pin_strike"), gex.pin_strike, gex.magnet_level);
    const gexNetGamma = firstPresent(gex.net_gamma_notional_usd, gex.total_net_gex, gex.net_gamma_notional);
    const gammaNetGamma = firstPresent(gamma.net_gamma_notional_usd, gamma.net_gamma_notional);
    const gammaNetGammaNumber = safeNumber(gammaNetGamma);
    const netGamma = !isNullish(gexNetGamma)
      ? gexNetGamma
      : (gammaNetGammaNumber !== null && Math.abs(gammaNetGammaNumber) >= 1000 ? gammaNetGammaNumber : null);
    const gammaProxy = isNullish(gexNetGamma) && gammaNetGammaNumber !== null && Math.abs(gammaNetGammaNumber) < 1000
      ? `${gammaNetGammaNumber > 0 ? "正向" : (gammaNetGammaNumber < 0 ? "负向" : "中性")} ${number(gammaNetGammaNumber, 4)}`
      : null;
    const distance = firstPresent(gamma.distance_to_pin_pct, get(gamma, "pin.distance_to_pin_pct"), gex.distance_to_pin_pct);
    const anchorFlip = firstPresent(anchor.effective_flip_point, anchor.flip_point);
    const anchorBand = firstPresent(anchor.band_half, anchor.anchor_band_half);
    const anchorGaps = [];
    if (Object.keys(anchor).length) {
      if (isNullish(anchorFlip)) anchorGaps.push("有效翻转点暂缺");
      if (isNullish(anchorBand)) anchorGaps.push("锚带半宽暂缺");
      if (isNullish(anchor.freshness)) anchorGaps.push("新鲜度暂缺");
      if (isNullish(anchor.ready)) anchorGaps.push("可用状态暂缺");
    }
    const facts = [
      marketFact("结构状态", regime),
      marketFact("净 Gamma", isNullish(netGamma) ? null : durabilityUsdText(netGamma)),
      marketFact("Gamma 代理", gammaProxy),
      marketFact("钉住位", pin, { digits: 2 }),
      marketFact("距钉住", isNullish(distance) ? null : pctPoint(Number(distance))),
      marketFact("上方墙", firstPresent(gex.call_wall, gamma.call_wall), { digits: 2 }),
      marketFact("下方墙", firstPresent(gex.put_wall, gamma.put_wall), { digits: 2 }),
      marketFact("价格锚有效翻转点", anchorFlip, { digits: 2 }),
      marketFact("锚带半宽", anchorBand, { digits: 2 }),
      marketFact("锚新鲜度", anchor.freshness),
      marketFact("锚可用", anchor.ready),
      marketFact("锚缺口", anchorGaps.join("；")),
      directObservedOrAgeText(anchor) ? marketFact("锚时效", directObservedOrAgeText(anchor)) : ""
    ];
    return marketFactCard(
      "market-options-structure",
      "期权空间结构",
      Object.keys(gamma).length || Object.keys(gex).length || Object.keys(anchor).length
        ? "这里只说明空间约束和来源质量，不把锚或 Gamma 当作不可突破边界。"
        : "本卡没有可读的期权空间结构。",
      facts
    );
  }

  function pricePathFacts(doc) {
    const tmvf = asObject(get(doc, "factor_cross_section.tmvf", {}));
    const direction = firstPresent(tmvf.direction, tmvf.lean, tmvf.verdict);
    const blend = firstPresent(tmvf.tmv_blend, tmvf.blend, tmvf.score);
    return marketFactCard("market-price-path", "量价主干", "用于判断当前价格路径对上下侵入压力的方向，不单独给胜率。", [
      marketFact("路径方向", direction),
      marketFact("主干强度", blend),
      marketFact("24小时", firstPresent(tmvf.tmvf_24h_final, tmvf.tmv_24h)),
      marketFact("48小时", firstPresent(tmvf.tmvf_48h_final, tmvf.tmv_48h)),
      marketFact("窗口冲突", tmvf.window_conflict),
      observedOrAgeText(doc, tmvf) ? marketFact("时效", observedOrAgeText(doc, tmvf)) : ""
    ]);
  }

  function activeFlowFacts(doc) {
    const flow = asObject(get(doc, "factor_cross_section.micro_flow", {}));
    const cvdEvidence = asArray(get(doc, "reasoning.evidence", []))
      .filter((item) => ["CVD_4H", "CVD_12H", "FLOW_CONFIRM"].includes(evidenceKey(item)))
      .slice(0, 2);
    const cvdText = cvdEvidence.map((item) => {
      const ctx = evidenceContext(item, doc);
      const value = firstPresent(
        evidenceFirstValue(item, ctx, ["verdict", "agreement", "absorption_state"]),
        item.participation_status
      );
      return normalizeComfortText(semanticCompact(value), "");
    }).filter(Boolean).join("；");
    return marketFactCard("market-active-flow", "主动买卖流", "用于确认或反驳量价主干，和价格来源有重叠，不重复当作独立投票。", [
      marketFact("合成方向", firstPresent(flow.combined_vote, flow.vote)),
      marketFact("一致性", flow.agreement),
      marketFact("吸收状态", flow.absorption_state),
      marketFact("快窗", firstPresent(get(flow, "fast_4h.verdict"), get(flow, "fast.verdict"))),
      marketFact("慢窗", firstPresent(get(flow, "slow_12h.verdict"), get(flow, "slow.verdict"))),
      marketFact("CVD", cvdText)
    ]);
  }

  function fundingFacts(doc) {
    const funding = asObject(get(doc, "factor_cross_section.funding", {}));
    const semantics = asObject(firstPresent(
      funding.canonical_funding_semantics,
      funding.funding_semantics
    ));
    const rate = firstPresent(semantics.raw_funding_rate, funding.last_rate, funding.last_funding_rate);
    const sentence = normalizeComfortText(firstTextValue(semantics.canonical_text_cn),
      "资金费率只作为杠杆拥挤和反身性背景，不自行改写方向。");
    return marketFactCard("market-funding-rate", "资金费率", sentence, [
      marketFact("当前费率", ratePctText(rate)),
      marketFact("拥挤状态", firstPresent(semantics.crowding_state, funding.crowding_state)),
      marketFact("费率倾向", firstPresent(semantics.fee_bias_cn, semantics.fee_bias)),
      observedOrAgeText(doc, funding) ? marketFact("时效", observedOrAgeText(doc, funding)) : ""
    ]);
  }

  function macroFacts(doc) {
    const macro = asObject(get(doc, "factor_cross_section.macro_pressure", {}));
    const shock = asObject(macro.macro_shock);
    return marketFactCard("market-macro-background", "宏观背景", "宏观只区分方向背景与冲击限制；硬阻断仍以本卡当前边界为准。", [
      marketFact("背景方向", firstPresent(macro.macro_regime, macro.regime, macro.verdict)),
      marketFact("压力分数", firstPresent(macro.macro_score, macro.score)),
      marketFact("冲击限制", firstPresent(shock.state, shock.block === true ? "BLOCK" : (shock.block === false ? "CLEAR" : null))),
      marketFact("方向确认", shock.direction_confirmed),
      ...macroComponentFacts({ raw: macro, factor: macro }).slice(0, 4)
    ]);
  }

  function dataQualityFacts(doc) {
    const quality = asObject(get(doc, "quality", {}));
    const sources = asObject(quality.sources);
    const sourceRows = Object.entries(sources).slice(0, 6).map(([key, value]) => {
      const source = qualitySourceView(doc, key, value);
      return marketFact(sourceGroupLabel(key) || fieldLabel(key), semanticCompact(source.status || source.reason || "已记录"));
    });
    return marketFactCard("market-data-quality", "数据时效与缺口", "有效但不投票的数据和真正缺失分开阅读；缺失只影响依赖它的判断。", [
      marketFact("综合质量", quality.overall),
      marketFact("必需源", quality.all_required_sources_ready === true ? "全部就绪" : "需要复核"),
      marketFact("缺口数", asArray(quality.missing_fields).length, { digits: 0 }),
      marketFact("降级源数", asArray(quality.degraded_sources).length, { digits: 0 }),
      ...sourceRows
    ]);
  }

  function renderFactorCrossSection(doc) {
    return section("中文市场证据", "本卡事实按结构、压力、时效和缺口排列，方便先判断环境是否适配。", `
      <div class="market-fact-grid">
        ${optionStructureFacts(doc)}
        ${pricePathFacts(doc)}
        ${activeFlowFacts(doc)}
        ${fundingFacts(doc)}
        ${macroFacts(doc)}
        ${dataQualityFacts(doc)}
      </div>
    `, "market-evidence");
  }

  function renderProvenance(doc) {
    return section("完整审计资料", "供进一步核对。", `
      <div class="download-panel">
        <button class="card-retry audit-json-download" type="button" data-download-card-id="${escapeHtml(cardId(doc))}" aria-label="下载完整审计资料">下载完整审计资料</button>
      </div>
    `);
  }

  function renderSignalBoundaries(doc) {
    const boundary = signalRatingBoundaryView(doc);
    const blocking = asObject(get(doc, "blocking", {}));
    const gates = asArray(blocking.soft_gates);
    const conditions = asArray(blocking.unblock_conditions);
    const veto = firstPresent(boundary.hard_veto, blocking.hard_veto);
    const gateItems = gates.slice(0, 4).map((gate) => {
      const title = normalizeComfortText(firstPresent(gate.reason_cn, gate.gate, gate.reason_code), "已记录一条软阻断。");
      return `<li>${escapeHtml(title)}</li>`;
    }).join("");
    const conditionItems = conditions.slice(0, 4).map((item) => {
      const raw = firstPresent(item.condition_cn, item.metric, "");
      const rawText = rawEnum(raw);
      if (/置信|confidence|达档|阈值|>=|≥|support_label/i.test(rawText)) {
        return `<li>${escapeHtml("等待方向证据收敛并满足原信号确认条件。")}</li>`;
      }
      const title = normalizeComfortText(raw, "等待确认条件。");
      return `<li>${escapeHtml(title)}</li>`;
    }).join("");
    const sideText = sideHintReadable(boundary.side_hint);
    return section("系统边界与阻断", "当前等待条件、阻断、接管窗口与执行权限仍然有效。", `
      <div class="boundary-panel">
        <div class="llm-review-topline">
          ${statusBadge("当前限制", boundary.support_label)}
          <span class="badge">侧别: ${escapeHtml(sideText)}</span>
          ${statusBadge("阻断", boundary.has_block === true ? "BLOCKED" : "OK")}
          <span class="badge ${boundary.execution_allowed === true ? "is-good" : "is-wait"}">执行权限: ${escapeHtml(boundary.execution_allowed === true ? "当前允许" : "只读/未授权")}</span>
          <span class="badge ${veto ? "is-bad" : "is-good"}">硬否决: ${escapeHtml(veto ? normalizeComfortText(veto, "已记录") : "无")}</span>
        </div>
        <div class="comfort-limit-grid">
          <div>
            <h3 class="subsection-title">当前边界</h3>
            <p>综合等级只服务人工复核排序；等待、阻断、接管窗口、触发和执行许可仍然有效。</p>
          </div>
          <div>
            <h3 class="subsection-title">解除或复核条件</h3>
            ${conditionItems ? `<ul class="plain-list">${conditionItems}</ul>` : (gateItems ? `<ul class="plain-list">${gateItems}</ul>` : `<p>本卡未提供额外解除条件。</p>`)}
          </div>
        </div>
      </div>
    `, "system-boundaries");
  }

  function renderObservationContext(doc) {
    const analysis = analysisRound(doc);
    const ctx = transitionContext(doc);
    const isFixed = isFixedAnalysisRound(doc);
    const session = asObject(get(doc, "signal_window.session_context", {}));
    const basis = asObject(session.validation_basis);
    const replay = asObject(get(doc, "provenance.research_replay", {}));
    const horizon = firstPresent(
      analysis.headline_horizon_min,
      analysis.observation_horizon_min,
      basis.headline_horizon_min
    );
    const horizonNumber = safeNumber(horizon);
    const rows = [];
    if (isFixed) rows.push("固定时间截面差分：本卡属于固定轮次分析，基于北京时间 23:00 的固定时间截面，不表示自然事件触发迁移。");
    const snapshot = firstPresent(
      analysis.snapshot_collected_time_utc8,
      analysis.snapshot_collected_at,
      get(doc, "runtime_facts.snapshot_collected_time_utc8"),
      get(doc, "runtime_facts.snapshot_collected_at")
    );
    if (snapshot) rows.push(`实际采样时点：${dateText(snapshot)}。`);
    const planned = firstPresent(
      analysis.planned_time_utc8,
      analysis.scheduled_time_utc8,
      analysis.planned_at,
      get(ctx, "event_context.planned_time_utc8")
    );
    if (planned) rows.push(`计划观察时点：${dateText(planned)}。`);
    if (horizonNumber !== null) rows.push(`预设观察窗：${number(horizonNumber, 0)} 分钟；只作为诊断探针，不代表信号寿命。`);
    if (Object.keys(replay).length) rows.push(normalizeComfortText(replay.notice_cn, "离线重评：不是当时原生输出，仅用于本页与 API 链路验收。"));
    if (!rows.length) return "";
    return section("观察身份", "区分事件卡、固定轮次和诊断观察窗，避免把历史观察当成当前实时判断。", `
      <div class="text-block">
        ${rows.map((row) => `<p>${escapeHtml(row)}</p>`).join("")}
      </div>
    `, "observation-context");
  }

  function selectedSummary(id) {
    return documents.find((doc) => cardId(doc) === id) || null;
  }

  function renderSummaryHeader(doc, subtitle) {
    const currentDecision = decision(doc);
    const quality = asObject(get(doc, "quality", {}));
    return `
      <header class="doc-header">
        <div>
          <p class="eyebrow">信号审计</p>
          <h1 class="doc-title">${escapeHtml(symbol(doc))} 信号审计卡</h1>
          <p class="doc-subtitle">${escapeHtml(subtitle || get(doc, "identity.strategy_name", ""))} · ${escapeHtml(dateText(confirmedAt(doc)))}</p>
        </div>
        <div class="status-stack">
          ${statusBadge("方向", currentDecision.lean || lean(doc), true)}
          ${statusBadge("当前限制", currentDecision.support_label || support(doc))}
          ${statusBadge("质量", quality.overall || "UNKNOWN")}
        </div>
      </header>
    `;
  }

  function renderSignalEvidenceHeader(doc, subtitle = "") {
    const identity = asObject(get(doc, "identity", {}));
    const badges = [
      isFixedAnalysisRound(doc) ? `<span class="badge fixed-round-badge">固定轮次</span>` : "",
      identity.is_synthetic ? `<span class="badge is-wait">离线样例</span>` : "",
      Object.keys(asObject(get(doc, "provenance.research_replay", {}))).length ? `<span class="badge is-wait">离线重评</span>` : ""
    ].filter(Boolean).join("");
    const subtitleText = [
      subtitle || identity.strategy_name || "",
      dateText(confirmedAt(doc))
    ].filter(Boolean).join(" · ");
    const stateView = signalEvidenceState(doc);
    const quickline = signalEvidenceHasUsableView(stateView)
      ? [
        marketSnapshotValueText(stateView.view.market_snapshot),
        signalEvidencePriceBiasIndexText(stateView.view),
        signalEvidenceComparisonIndexText(stateView.view)
      ].filter(Boolean).join(" · ")
      : "";
    return `
      <header class="doc-header">
        <div>
          <p class="eyebrow">信号审计</p>
          <h1 class="doc-title">${escapeHtml(symbol(doc))} 信号审计卡</h1>
          <p class="doc-subtitle">${escapeHtml(subtitleText)}</p>
          ${quickline ? `<p class="evidence-header-quickline">${escapeHtml(quickline)}</p>` : ""}
        </div>
        ${badges ? `<div class="status-stack">${badges}</div>` : ""}
      </header>
    `;
  }

  function renderDocumentSkeleton(summary) {
    if (!summary) {
      $("#documentView").innerHTML = `${renderLoadNotice()}<div class="empty">请选择一份信号文档</div>`;
      return;
    }
    if (hasSignalEvidenceV2Surface(summary)) {
      $("#documentView").innerHTML = renderSignalEvidenceReader(summary, `
        ${renderSignalEvidenceHeader(summary, "单卡详情加载中")}
        ${renderSignalEvidenceReaderNav(summary)}
        ${renderSignalComfort(summary)}
        ${renderSignalEvidenceKeyChanges(summary)}
        <section class="section">
          <div class="section-header">
            <h2 class="section-title">单卡详情加载中</h2>
            <p class="section-purpose">正在读取完整资料；稍后显示结构与证据、下一观察条件和下载入口。</p>
          </div>
          <div class="card-load-state">正在读取这张卡的完整资料。</div>
        </section>
      `);
      return;
    }
    const nrWindow = nrWindowMetric(summary);
    $("#documentView").innerHTML = `
      ${renderLoadNotice()}
      ${renderSummaryHeader(summary, "单卡详情加载中")}
      ${renderSignalComfort(summary)}
      <div class="metric-strip" aria-label="信号关键指标加载中">
        ${metric("市场价格", null)}
        ${metric("评级时点", signalComfortAsOfMetricText(summary))}
        ${metric("接管窗口", nrWindow.value, nrWindow.note)}
        ${metric("当前限制", currentLimitMetricText(summary))}
        ${metric("数据质量", semanticCompact(qualityOverall(summary)))}
        ${metric("卡片身份", roundKindText(summary))}
      </div>
      <section class="section">
        <div class="section-header">
          <h2 class="section-title">单卡详情加载中</h2>
          <p class="section-purpose">正在读取这张卡的完整资料；稍后显示行动结论、中文市场证据和 LLM 深入分析。</p>
        </div>
        <div class="card-load-state">正在读取这张卡的完整资料。</div>
      </section>
    `;
  }

  function renderCardLoadError(summary) {
    const id = summary ? cardId(summary) : state.currentId;
    const stateForCard = cardLoadState(id);
    const attempts = Number(stateForCard.attempts || 0);
    const retry = attempts < MAX_CARD_LOAD_ATTEMPTS;
    const reason = stateForCard.publicReason || publicLoadReason("card_http");
    $("#documentView").innerHTML = `
      ${renderLoadNotice()}
      ${summary ? (hasSignalEvidenceV2Surface(summary) ? renderSignalEvidenceHeader(summary, "本卡资料暂不可用") : renderSummaryHeader(summary, "本卡资料暂不可用")) : ""}
      <section class="section">
        <div class="section-header">
          <h2 class="section-title">单卡详情暂不可显示</h2>
          <p class="section-purpose">这张卡暂时无法打开；可以切换其他信号，稍后重试。</p>
        </div>
        <div class="load-alert card-load-error" role="alert">
          <strong>单卡资料加载失败</strong>
          <p>${escapeHtml(reason)}</p>
          <div class="card-load-actions">
            <span>已尝试 ${escapeHtml(number(attempts, 0))} / ${escapeHtml(number(MAX_CARD_LOAD_ATTEMPTS, 0))}</span>
            <button class="card-retry" type="button" data-card-id="${escapeHtml(id)}" ${retry ? "" : "disabled"}>${retry ? "重试加载" : "重试次数已用完"}</button>
          </div>
        </div>
      </section>
    `;
    if (summary && hasSignalEvidenceV2Surface(summary)) {
      $("#documentView").innerHTML = renderSignalEvidenceReader(summary, `
        ${renderSignalEvidenceHeader(summary, "本卡资料暂不可用")}
        <section class="section">
          <div class="section-header">
            <h2 class="section-title">单卡详情暂不可显示</h2>
            <p class="section-purpose">这张卡暂时无法打开；可以切换其他信号，稍后重试。</p>
          </div>
          <div class="load-alert card-load-error" role="alert">
            <strong>单卡资料加载失败</strong>
            <p>${escapeHtml(reason)}</p>
            <div class="card-load-actions">
              <span>已尝试 ${escapeHtml(number(attempts, 0))} / ${escapeHtml(number(MAX_CARD_LOAD_ATTEMPTS, 0))}</span>
              <button class="card-retry" type="button" data-card-id="${escapeHtml(id)}" ${retry ? "" : "disabled"}>${retry ? "重试加载" : "重试次数已用完"}</button>
            </div>
          </div>
        </section>
      `);
    }
  }

  function renderDocumentById(id) {
    if (!id) {
      $("#documentView").innerHTML = `${renderLoadNotice()}<div class="empty">${documents.length ? "当前筛选没有匹配的信号卡，请调整筛选条件。" : "请选择一份信号文档"}</div>`;
      return;
    }
    const cached = cardCache.get(id);
    if (cached) {
      renderDocument(cached);
      return;
    }
    const summary = selectedSummary(id);
    const load = cardLoadState(id);
    if (load.status === "error") {
      renderCardLoadError(summary);
      return;
    }
    renderDocumentSkeleton(summary);
  }

  function setupDocumentActions() {
    document.querySelectorAll(".card-retry").forEach((button) => {
      button.addEventListener("click", () => {
        if (button.dataset.downloadCardId) {
          downloadAuditJson(button.dataset.downloadCardId);
          return;
        }
        const id = button.dataset.cardId;
        if (!id || !canRetryCard(id)) return;
        const retry = loadCardDetail(id, { selected: true, retry: true });
        render();
        retry.then(() => {
          if (state.currentId === id) {
            populateFilters();
            render();
          }
        }).catch(() => {
          if (state.currentId === id) render();
        });
      });
    });
  }

  function safeCardDownloadPath(path) {
    const text = String(path || "").trim().replace(/^\.\/+/, "");
    if (!text) return "";
    if (/^[a-z][a-z0-9+.-]*:/i.test(text) || text.startsWith("//") || text.startsWith("/") || text.includes("\\") || text.split("/").includes("..")) {
      return "";
    }
    return /^signal_cards\/[^?#]+\.json(?:[?#].*)?$/i.test(text) ? text : "";
  }

  function downloadAuditJson(id) {
    const doc = cardCache.get(id) || documents.find((item) => cardId(item) === id);
    if (!doc) return;
    const staticPath = isHttpMode() ? safeCardDownloadPath(doc.__card_path) : "";
    if (staticPath) {
      const link = document.createElement("a");
      link.href = staticPath;
      link.download = `${id || "signal-card"}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      return;
    }
    if (typeof Blob === "undefined" || !window.URL || typeof window.URL.createObjectURL !== "function") return;
    const blob = new Blob([JSON.stringify(doc, null, 2)], { type: "application/json;charset=utf-8" });
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${id || "signal-card"}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    if (typeof window.URL.revokeObjectURL === "function") {
      setTimeout(() => window.URL.revokeObjectURL(url), 30000);
    }
  }

  function maybeLoadSelectedCard() {
    const id = state.currentId;
    if (!isHttpMode() || !id || cardCache.has(id)) return;
    const load = cardLoadState(id);
    if (load.status === "loading") return;
    if (load.status === "error" && !(load.prefetch && canRetryCard(id))) return;
    loadCardDetail(id, { selected: true, retry: load.status === "error" })
      .then(() => {
        if (state.currentId === id) {
          populateFilters();
          render();
        }
      })
      .catch(() => {
        if (state.currentId === id) render();
      });
  }

  function prefetchRecentCards() {
    if (!isHttpMode()) return Promise.resolve([]);
    const ids = documents
      .map((doc) => cardId(doc))
      .filter((id) => id && id !== state.currentId && !cardCache.has(id))
      .filter((id) => {
        const load = cardLoadState(id);
        return load.status !== "loading" && load.status !== "error";
      })
      .slice(0, PREFETCH_CARD_LIMIT);
    return Promise.allSettled(ids.map((id) => loadCardDetail(id, { prefetch: true })));
  }

  let initialEvidenceSectionPositioned = false;

  function positionInitialEvidenceSection() {
    if (initialEvidenceSectionPositioned) return;
    const hash = window.location && window.location.hash;
    if (!hash || !/^#[a-z][a-z0-9-]+$/.test(hash)) return;
    const sectionTarget = document.querySelector(hash);
    if (sectionTarget && typeof sectionTarget.scrollIntoView === "function") {
      initialEvidenceSectionPositioned = true;
      sectionTarget.scrollIntoView({ behavior: "instant", block: "start" });
    }
  }

  function renderDocument(doc) {
    if (!doc) {
      $("#documentView").innerHTML = `${renderLoadNotice()}<div class="empty">请选择一份信号文档</div>`;
      return;
    }
    if (hasSignalEvidenceV2Surface(doc)) {
      $("#documentView").innerHTML = renderSignalEvidenceReader(doc, `
        ${renderSignalEvidenceHeader(doc)}
        ${renderSignalEvidenceReaderNav(doc)}
        ${renderSignalComfort(doc)}
        ${renderSignalEvidenceSpatialDynamics(doc)}
        ${renderSignalEvidenceKeyChanges(doc)}
        ${renderSignalEvidenceLlmReview(doc)}
        ${renderSignalEvidenceNextConditions(doc)}
        ${renderSignalEvidenceMarketFacts(doc)}
        ${renderProvenance(doc)}
      `);
      positionInitialEvidenceSection();
      return;
    }
    const identity = asObject(get(doc, "identity", {}));
    const quality = asObject(get(doc, "quality", {}));
    const price = get(doc, "market_context.price", get(doc, "market_price"));
    const nrWindow = nrWindowMetric(doc);
    $("#documentView").innerHTML = `
      ${renderLoadNotice()}
      <header class="doc-header">
        <div>
          <p class="eyebrow">信号审计</p>
          <h1 class="doc-title">${escapeHtml(symbol(doc))} 信号审计卡</h1>
          <p class="doc-subtitle">${escapeHtml(identity.strategy_name || "")} · ${escapeHtml(dateText(confirmedAt(doc)))}</p>
        </div>
        <div class="status-stack">
          ${statusBadge("方向", lean(doc), true)}
          ${statusBadge("当前限制", support(doc))}
          ${statusBadge("质量", quality.overall)}
          ${isFixedAnalysisRound(doc) ? `<span class="badge fixed-round-badge">固定轮次分析</span>` : ""}
          ${identity.is_synthetic ? `<span class="badge is-wait">离线样例</span>` : ""}
        </div>
      </header>
      ${renderSignalComfort(doc)}
      <div class="metric-strip" aria-label="信号关键指标">
        ${metric("市场价格", price, get(doc, "market_context.quote_currency", ""))}
        ${metric("评级时点", signalComfortAsOfMetricText(doc))}
        ${metric("接管窗口", nrWindow.value, nrWindow.note)}
        ${metric("当前限制", currentLimitMetricText(doc))}
        ${metric("数据质量", semanticCompact(quality.overall), quality.all_required_sources_ready ? "必需源就绪" : "需要复核")}
        ${metric("卡片身份", roundKindText(doc))}
      </div>
      ${renderFactorCrossSection(doc)}
      ${renderSignalBoundaries(doc)}
      ${renderObservationContext(doc)}
      ${renderIntegratedTradeAdvisory(doc)}
      ${renderLlmReview(doc)}
      ${renderReasoning(doc)}
      ${renderConflict(doc)}
      ${renderProvenance(doc)}
    `;
  }

  function renderLoadNotice() {
    if (!loadState.error) return "";
    return `
      <div class="load-alert" role="alert">
        <strong>静态信号卡加载失败</strong>
        <p>未能显示内置样例；请检查发布文件是否完整，或稍后重新打开页面。</p>
      </div>
    `;
  }

  function render() {
    const list = filteredDocuments();
    renderIndex(list);
    renderDocumentById(state.currentId);
    setupDocumentActions();
    maybeLoadSelectedCard();
  }

  async function start() {
    setupFilterEvents();
    setupMobileIndexToggle();
    documents = await loadDocuments();
    state.currentId = documents[0] ? cardId(documents[0]) : null;
    populateFilters();
    render();
    prefetchRecentCards();
  }

  start();
})();
