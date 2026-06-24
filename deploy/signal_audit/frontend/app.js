(function () {
  "use strict";

  const embedded = JSON.parse(document.getElementById("signal-data").textContent);
  let documents = [];
  let loadState = { mode: "pending", error: "" };
  const state = {
    currentId: null,
    query: "",
    direction: "",
    action: "",
    quality: ""
  };

  const enumLabels = {
    ACTIVE: "参与计票",
    ADVERSE: "不利",
    ALLOWED: "允许",
    ALIGNED: "一致",
    APPROVABLE: "可提交人工批准",
    AUDIT_ONLY: "仅审计",
    BEARISH: "偏空",
    BEARISH_LEAN: "理论偏空",
    BEARISH_CONFIRMED: "偏空已确认",
    BEARISH_WITH_DISAGREEMENT: "偏空但存在分歧",
    BULLISH: "偏多",
    BULLISH_LEAN: "理论偏多",
    BULLISH_CONFIRMED: "偏多已确认",
    BULLISH_WITH_DISAGREEMENT: "偏多但存在分歧",
    CONFIDENCE_GATE_NOT_DIRECTIONAL_VOTE: "仅调制置信，不参与方向计票",
    CONFLICT: "冲突",
    DECISION: "决策",
    DEGRADED: "降级",
    DECREASE: "降低前提耐久",
    DECREASE_TENTATIVE: "轻度降低前提耐久",
    DO_NOT_SUPPORT: "不支持系统结论",
    DO_NOT_MULTIPLY_CONFIDENCE: "不乘进置信",
    ERROR: "错误",
    EXCLUDED: "已排除",
    FINAL: "定稿",
    FULL_LIVE: "完整实时",
    FUNDING: "资金费率",
    GATE_ONLY: "仅门控",
    GEMINI: "Gemini",
    gemini: "Gemini",
    GAMMA: "Gamma",
    GAMMA_TRANSITION: "Gamma 过渡",
    CONFIRMED: "已确认",
    CONFIRMED_60M_LOCAL: "60m局部确认",
    CONSTRAINT: "空间约束",
    CONSERVATIVE_LOWER_TIER: "缓冲带就低不就高",
    CROWDED: "拥挤",
    DEEP: "深",
    EDT: "美国夏令时",
    EST: "美国冬令时",
    EVENT_BLACKOUT: "事件黑名单",
    HIGH: "高",
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
    MEDIUM: "中",
    MEDIUM_TO_HIGH_BUFFER: "中转高缓冲带",
    MEDIUM_TO_LOW_BUFFER: "中转低缓冲带",
    MARKET_PRIOR_VALIDATED: "市场先验已验证",
    MARKET_PRIOR_VALIDATED_NOT_SIGNAL_CALIBRATED: "市场先验已验证 / 非信号校准",
    MILD_CROWDED: "轻度拥挤",
    MILD_HEADWIND: "轻度逆风",
    MACRO: "宏观",
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
    NON_VOTING: "不计票",
    NOT_CONFIRMED: "未确认",
    NOT_IMPLEMENTED_SHADOW: "未落地影子项",
    NOT_READY: "未就绪",
    NO_TRADE_BLOCKED: "无交易/阻断",
    OBSERVE: "观察",
    OBSERVE_LONG_BIAS: "观察偏多",
    OBSERVE_SHORT_BIAS: "观察偏空",
    OK: "正常",
    PARTIAL: "部分可用",
    PARTIAL_SUPPORT: "部分支持系统结论",
    PHASE_0_OBSERVE_ONLY: "观察层（不改信号）",
    PENDING_LLM: "等待 LLM 复核",
    POSITIVE_GAMMA: "正 Gamma",
    POSITIVE_GAMMA_PINNING: "正 Gamma 钉住",
    PREPARE_LONG: "准备做多",
    PREPARE_SHORT: "准备做空",
    CALL_SKEW: "看涨偏斜",
    PUT_SKEW: "看跌偏斜",
    RAISE_DURABILITY_TENTATIVE: "暂定升耐久",
    RISK_CONSTRAINT: "空间风险约束",
    SKIPPED: "已跳过",
    THIN: "薄",
    SHORT_GAMMA_AMPLIFYING: "短 Gamma 放大/反身",
    SHORT_BIAS: "空头偏好",
    SKEW: "偏斜",
    SOFT_GATE: "软门控",
    SOURCE_AGE_EXCEEDED: "数据时效超限",
    STALE: "陈旧",
    SUPPORT: "支持系统结论",
    SUPPORTIVE: "支持",
    TENTATIVE: "暂定",
    TRADE_SUPPORT_STRONG: "强交易支持",
    TRADE_SUPPORT_WEAK: "弱交易支持",
    UNCALIBRATED: "未校准",
    UNKNOWN: "未知",
    UNABLE_TO_JUDGE: "无法判断",
    VALID: "有效",
    WAIT_CONFIRMATION: "等待确认",
    WAIT_FOR_EVIDENCE: "等待证据",
    BLOCKED: "已被阻断",
    CONTINUING: "延续",
    DETERIORATING: "恶化",
    IMPROVING: "改善",
    REVERSING: "反转",
    STABLE: "稳定",
    NEUTRALIZED: "中和",
    DECISION_SUPPORT_COLLAPSE: "决策支持塌缩",
    FUNDING_CROWDING_ESCALATION: "资金拥挤升温",
    FUNDING_CROWDING_UP: "资金拥挤上升",
    GAMMA_REGIME_SHIFT: "Gamma 体制切换",
    MACRO_SHOCK: "宏观压力冲击",
    MULTI_DOMAIN_RISK_DETERIORATION: "多域风险恶化",
    RISK_HEADWIND_SIGN_FLIP: "逆风符号翻转",
    SKEW_REVERSAL: "偏斜反转",
    CRITICAL: "关键"
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
    affects_blocking: "是否影响门控",
    affects_confidence: "是否影响置信",
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
    next_action: "下一步动作",
    not_trading_advice: "非交易建议",
    operator_hint_cn: "操作提示",
    headline_horizon_min: "主口径未来窗(分钟)",
    observed_at: "观测时间",
    participation: "参与状态",
    participation_status: "参与状态",
    phase: "阶段",
    pin_strike: "钉住行权价",
    pin: "钉住点",
    positioning_assumption_cn: "持仓符号假设",
    previous_card_id: "上一状态",
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
    spatial_safety: "空间安全",
    status: "状态",
    input_packet_hash: "输入包哈希",
    prompt_version: "提示词版本",
    provider: "模型服务商",
    research_grade: "研究等级",
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
    fact_cn: "事实说明",
    invalid_if: "失效条件",
    materiality: "材料性",
    materiality_score: "材料性分数",
    no_external_data: "不使用外部数据",
    no_trading_instruction: "不含交易建议",
    observed_changes: "观察到的变化",
    operator_focus: "人工观察重点",
    previous: "上一值",
    recent_5_trajectory: "最近 5 次轨迹",
    role: "字段角色",
    signal_continuity: "连续性",
    trajectory_state: "轨迹状态"
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
    if (isNullish(value)) return "暂缺 (null)";
    const raw = rawEnum(value);
    const translated = enumLabels[raw];
    return translated ? `${translated} (${raw})` : raw;
  };
  const semanticCompact = (value) => enumLabels[rawEnum(value)] || rawEnum(value);
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
    return translated ? `${translated} (${raw})` : raw;
  };
  const isEnum = (value) => typeof value === "string" && /^[A-Z][A-Z0-9_/-]*$/.test(value);
  const number = (value, digits = 3) => {
    if (isNullish(value)) return "暂缺 (null)";
    if (typeof value !== "number") return String(value);
    return new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(value);
  };
  const percent = (value, digits = 1) => isNullish(value) ? "暂缺 (null)" : `${number(value * 100, digits)}%`;
  const booleanText = (value) => isNullish(value) ? "暂缺 (null)" : value ? "是 (true)" : "否 (false)";
  const scalarText = (value, options = {}) => {
    if (isNullish(value)) return "暂缺 (null)";
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
    if (isNullish(value)) return "暂缺 (null)";
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
    if (!iso) return "暂缺 (null)";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return String(iso);
    const options = mode === "short"
      ? { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }
      : { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false };
    return new Intl.DateTimeFormat("zh-CN", options).format(date);
  };
  const ageText = (ageMs) => {
    if (isNullish(ageMs)) return "暂缺 (null)";
    if (ageMs < 1000) return `${number(ageMs, 0)} ms`;
    if (ageMs < 60000) return `${number(ageMs / 1000, 1)} 秒`;
    if (ageMs < 3600000) return `${number(ageMs / 60000, 1)} 分钟`;
    return `${number(ageMs / 3600000, 1)} 小时`;
  };
  const firstPresent = (...values) => values.find((value) => !isNullish(value) && value !== "");
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
  const kv = (key, value, options = {}) => `
    <div class="kv">
      <dt>${escapeHtml(fieldLabel(key))}</dt>
      <dd>${valueHtml(value, options)}</dd>
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
    return `<ul class="plain-list">${values.map((item) => `<li>${valueHtml(item, { translate: false })}</li>`).join("")}</ul>`;
  };

  async function loadDocuments() {
    const fallback = sortByTimeDesc(window.SIGNAL_CARD_FIXTURES || embedded);
    if (window.location.protocol === "file:") {
      loadState = { mode: "file_fallback", error: "" };
      return fallback;
    }
    try {
      const manifestResponse = await fetch("signal_cards/index.json", { cache: "no-store" });
      if (!manifestResponse.ok) throw new Error(`manifest ${manifestResponse.status}`);
      const manifest = await manifestResponse.json();
      const loaded = await Promise.all(asArray(manifest.cards).map(async (card) => {
        const response = await fetch(card.path, { cache: "no-store" });
        if (!response.ok) throw new Error(`${card.path} ${response.status}`);
        return response.json();
      }));
      loadState = { mode: "materialized", error: "" };
      return sortByTimeDesc(loaded);
    } catch (error) {
      console.warn("Signal card load failed:", error);
      loadState = {
        mode: "load_error",
        error: error && error.message ? error.message : String(error)
      };
      return [];
    }
  }

  function uniqueValues(path) {
    return [...new Set(documents.map((doc) => get(doc, path)).filter((value) => !isNullish(value)))].sort();
  }

  function populateSelect(selector, placeholder, values) {
    const select = $(selector);
    const current = select.value;
    select.innerHTML = `<option value="">${escapeHtml(placeholder)}</option>`;
    values.forEach((value) => {
      select.insertAdjacentHTML("beforeend", `<option value="${escapeHtml(value)}">${escapeHtml(semanticLabel(value))}</option>`);
    });
    select.value = current;
  }

  function populateFilters() {
    populateSelect("#directionFilter", "全部方向", uniqueValues("decision.lean"));
    populateSelect("#actionFilter", "全部动作", uniqueValues("decision.support_label"));
    populateSelect("#qualityFilter", "全部质量状态", uniqueValues("quality.overall"));
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
  }

  function filteredDocuments() {
    return documents.filter((doc) => {
      const haystack = [
        cardId(doc), symbol(doc), get(doc, "identity.strategy_name"),
        lean(doc), semanticLabel(lean(doc)), support(doc), semanticLabel(support(doc)),
        qualityOverall(doc), semanticLabel(qualityOverall(doc)), get(doc, "display_layers.headline")
      ].join(" ").toLowerCase();
      return (!state.query || haystack.includes(state.query))
        && (!state.direction || lean(doc) === state.direction)
        && (!state.action || support(doc) === state.action)
        && (!state.quality || qualityOverall(doc) === state.quality);
    }).sort((a, b) => new Date(confirmedAt(b)) - new Date(confirmedAt(a)));
  }

  function renderIndex(list) {
    $("#resultCount").textContent = `${list.length} / ${documents.length}`;
    if (!list.length) {
      $("#indexList").innerHTML = `<div class="empty">没有匹配的信号文档</div>`;
      return;
    }
    if (!list.some((doc) => cardId(doc) === state.currentId)) state.currentId = cardId(list[0]);
    $("#indexList").innerHTML = list.map((doc) => {
      const active = cardId(doc) === state.currentId ? "is-active" : "";
      const currentDecision = decision(doc);
      return `
        <button class="index-item ${active}" type="button" data-card-id="${escapeHtml(cardId(doc))}">
          <div class="index-topline">
            <span class="index-symbol">${escapeHtml(symbol(doc))} #${escapeHtml(shortId(doc))}</span>
            <span class="index-time">${escapeHtml(dateText(confirmedAt(doc), "short"))}</span>
          </div>
          <p class="index-summary">${escapeHtml(get(doc, "display_layers.headline", currentDecision.final_conclusion_cn || ""))}</p>
          <div class="mini-stats">
            <span>${escapeHtml(semanticCompact(lean(doc)))}</span>
            <span>${escapeHtml(semanticCompact(support(doc)))}</span>
            <span>置信 ${escapeHtml(scalarText(currentDecision.confidence, { translate: false }))}</span>
            <span>${escapeHtml(semanticCompact(qualityOverall(doc)))}</span>
          </div>
          ${renderIndexTransitionBadges(doc)}
        </button>
      `;
    }).join("");
    document.querySelectorAll(".index-item").forEach((button) => {
      button.addEventListener("click", () => {
        state.currentId = button.dataset.cardId;
        render();
      });
    });
  }

  function transitionContext(doc) {
    return asObject(get(doc, "transition_context", {}));
  }

  function renderIndexTransitionBadges(doc) {
    const ctx = transitionContext(doc);
    if (!Object.keys(ctx).length) return "";
    const flags = asArray(ctx.cross_domain_flags);
    return `
      <div class="transition-badges">
        <span>${escapeHtml(ctx.comparison_quality || get(ctx, "relation.comparison_quality", "UNKNOWN"))}</span>
        <span>${escapeHtml(flags[0] || "AUDIT_ONLY")}</span>
      </div>
    `;
  }

  function renderDecision(doc) {
    const current = decision(doc);
    return section("决策结论", "将方向、证据强度、置信语义和执行许可分开呈现。", `
      <p class="summary-text">${valueHtml(current.final_conclusion_cn, { translate: false })}</p>
      <dl class="kv-grid" style="margin-top: 18px;">
        ${kv("Directional bias", current.directional_bias)}
        ${kv("Side hint", current.side_hint)}
        ${kv("Confidence semantics", current.confidence_semantics)}
        ${kv("Model trade support", current.model_trade_support)}
        ${kv("Execution allowed", current.execution_allowed)}
        ${kv("Execution permission note", current.execution_permission_note)}
        ${kv("Trade allowed", current.trade_allowed)}
        ${kv("Next action", current.next_action)}
      </dl>
    `);
  }

  function renderDecisionMatrix(doc) {
    const matrix = asObject(get(doc, "decision_matrix", {}));
    if (!Object.keys(matrix).length) return "";
    const warnings = asArray(matrix.context_warnings);
    const reasonCodes = asArray(matrix.reason_codes);
    return section("封板决策矩阵", "角色收口后的最终信号合成视图；LLM 与人工仍只做审计/批准，不回写系统结论。", `
      <div class="status-stack" style="align-items: flex-start; margin-bottom: 16px;">
        ${statusBadge("决策状态", matrix.decision_state || "UNKNOWN")}
        ${statusBadge("主动流确认", matrix.flow_confirm || "MISSING")}
        ${statusBadge("结构稳定性", matrix.structure_stability || "UNKNOWN")}
        ${statusBadge("空间安全", matrix.spatial_safety || "UNKNOWN")}
      </div>
      <dl class="kv-grid">
        ${kv("Window", matrix.window)}
        ${kv("Direction", matrix.direction)}
        ${kv("Temporal durability", matrix.temporal_durability)}
        ${kv("Audit dissent", matrix.audit_dissent)}
        ${kv("Model trade support", matrix.model_trade_support)}
        ${kv("Execution allowed", matrix.execution_allowed)}
      </dl>
      <div class="two-column-notes" style="margin-top: 16px;">
        <div><h3 class="subsection-title">上下文警示</h3>${listHtml(warnings, "无")}</div>
        <div><h3 class="subsection-title">Reason codes</h3>${listHtml(reasonCodes, "无")}</div>
      </div>
    `);
  }

  function renderLlmReview(doc) {
    const review = asObject(get(doc, "llm_review", {}));
    const hasReview = Object.keys(review).length > 0;
    const matrix = asObject(get(doc, "decision_matrix", {}));
    const content = Object.assign({}, asObject(review.content), review);
    const status = hasReview ? (review.status || "UNKNOWN") : (matrix.audit_dissent || "PENDING_LLM");
    const failed = hasReview && rawEnum(status) !== "OK";
    const panelClass = failed ? "llm-review-panel is-error" : (hasReview ? "llm-review-panel" : "llm-review-panel is-pending");
    const summary = content.summary_cn || "LLM 复核尚未生成或尚未被 sidecar 合并；本区保留为发布前必查板块，不改变系统方向、置信、门控或交易许可。";
    return section("LLM 复核意见", "外部模型只做审计建议，不改变系统方向、置信、门控或交易许可。", `
      <div class="${panelClass}">
        <div class="llm-review-topline">
          ${statusBadge("状态", status)}
          ${statusBadge("谨慎等级", content.caution_level || (hasReview ? "UNKNOWN" : "PENDING_LLM"))}
          ${review.provider ? statusBadge("模型服务", review.provider) : ""}
        </div>
        <p class="llm-review-summary">${valueHtml(summary, { translate: false })}</p>
      </div>
      ${renderTheoreticalActiveView(content.theoretical_active_view)}
      ${renderGammaRegimeLens(content.gamma_regime_lens)}
      <dl class="kv-grid llm-review-grid" style="margin-top: 16px;">
        ${kv("agreement_with_system", content.agreement_with_system)}
        ${kv("reviewed_at", review.reviewed_at, { translate: false })}
        ${kv("not_trading_advice", content.not_trading_advice)}
        ${kv("model", review.model, { translate: false })}
        ${kv("prompt_version", review.prompt_version, { translate: false })}
        ${kv("input_packet_hash", review.input_packet_hash, { translate: false })}
      </dl>
      ${content.data_quality_note ? `<div class="llm-review-quality">${escapeHtml(content.data_quality_note)}</div>` : ""}
      <div class="two-column-notes llm-review-lists">
        <div><h3 class="subsection-title">支持系统结论的因素</h3>${listHtml(content.main_supporting_factors, "无")}</div>
        <div><h3 class="subsection-title">主要风险或冲突</h3>${listHtml(content.main_risks_or_conflicts, "无")}</div>
        <div><h3 class="subsection-title">人工观察重点</h3>${listHtml(content.operator_focus, "无")}</div>
        <div><h3 class="subsection-title">复核失效条件</h3>${listHtml(content.invalid_if, "无")}</div>
      </div>
      ${review.error ? `<div class="llm-review-error">${escapeHtml(review.error)}</div>` : ""}
    `, "llm-review");
  }

  function renderTransitionContext(doc) {
    const ctx = transitionContext(doc);
    if (!Object.keys(ctx).length) return "";
    const decisionTransition = asObject(ctx.decision_transition);
    const flags = asArray(ctx.cross_domain_flags);
    const quality = ctx.comparison_quality || get(ctx, "relation.comparison_quality", "UNKNOWN");
    const elapsed = isNullish(ctx.elapsed_ms) ? null : ageText(ctx.elapsed_ms);
    const previousState = [
      semanticLabel(decisionTransition.lean_before),
      semanticLabel(decisionTransition.support_before),
      isNullish(decisionTransition.confidence_before) ? "" : `置信 ${decisionTransition.confidence_before}`
    ].filter(Boolean).join(" / ");
    const currentState = [
      semanticLabel(decisionTransition.lean_after),
      semanticLabel(decisionTransition.support_after),
      isNullish(decisionTransition.confidence_after) ? "" : `置信 ${decisionTransition.confidence_after}`
    ].filter(Boolean).join(" / ");
    return section("状态转移审计", "AUDIT_ONLY 变化链由 materializer 生成；前端只展示已物化字段，不计算 delta。", `
      <div class="transition-panel">
        <div class="llm-review-topline">
          ${statusBadge("AUDIT_ONLY", ctx.audit_scope || "AUDIT_ONLY")}
          ${statusBadge("比较质量", quality)}
          ${flags.slice(0, 3).map((flag) => statusBadge("", flag)).join("")}
        </div>
        <dl class="kv-grid transition-grid">
          ${kv("上一状态", previousState || ctx.previous_card_id, { translate: false })}
          ${kv("当前状态", currentState || ctx.current_card_id, { translate: false })}
          ${kv("elapsed_ms", elapsed, { translate: false })}
          ${kv("comparison_quality", quality)}
          ${kv("previous_card_id", ctx.previous_card_id, { translate: false })}
          ${kv("current_card_id", ctx.current_card_id, { translate: false })}
          ${kv("materiality_score", ctx.materiality_score, { translate: false })}
          ${kv("llm_review_required", ctx.llm_review_required)}
        </dl>
      </div>
      ${renderTransitionLlmReview(doc)}
      <div class="transition-raw-block">
        <h3 class="subsection-title">原始字段变化</h3>
        ${renderTransitionChanges(doc, asArray(ctx.top_material_changes))}
        ${renderTransitionComparisonTabs(ctx)}
      </div>
    `, "transition-context");
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
            ${change.sign_flip ? `<span class="chip">${escapeHtml(semanticCompact(change.sign_flip))}</span>` : ""}
            ${change.meaning ? `<span class="chip">${escapeHtml(semanticCompact(change.meaning))}</span>` : ""}
            ${source}
          </div>
        </div>
      `;
    }).join("")}</div>`;
  }

  function transitionDomainLabel(domain) {
    const labels = {
      DECISION: "决策",
      FUNDING: "资金费率",
      GAMMA: "Gamma",
      MACRO: "宏观",
      QUALITY: "数据质量",
      SKEW: "偏斜"
    };
    const raw = rawEnum(domain || "UNKNOWN");
    return labels[raw] ? `${labels[raw]} (${raw})` : semanticCompact(raw);
  }

  function transitionValueText(value) {
    if (Array.isArray(value) || (value && typeof value === "object")) return rawValueTextLabeled(value);
    if (isEnum(value)) return semanticLabel(value);
    return scalarText(value, { translate: false, digits: 4 });
  }

  function rawValueTextLabeled(value, depth = 0) {
    if (isNullish(value) || value === "") return "null";
    if (Array.isArray(value)) {
      if (!value.length) return "[]";
      return value.map((item) => rawValueTextLabeled(item, depth + 1)).join(" ; ");
    }
    if (typeof value === "object") {
      const entries = Object.entries(value)
        .filter(([, child]) => child !== undefined && child !== null && child !== "");
      if (!entries.length) return "{}";
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
    return `
      <div class="transition-llm">
        <h3 class="subsection-title">LLM 变化链解释</h3>
        <div class="llm-review-topline">
          ${statusBadge("状态", review.status || "UNKNOWN")}
          ${statusBadge("禁止交易指令", guard.no_trading_instruction ? "VALID" : "UNKNOWN")}
          ${statusBadge("不使用外部数据", guard.no_external_data ? "VALID" : "UNKNOWN")}
          ${statusBadge("区分观察与因果", guard.distinguishes_observation_from_causality ? "VALID" : "UNKNOWN")}
          ${statusBadge("不含交易建议", review.not_trading_advice ? "VALID" : "UNKNOWN")}
        </div>
        <p class="llm-review-summary">${valueHtml(review.transition_summary_cn || "暂无解释", { translate: false })}</p>
        <dl class="kv-grid llm-review-grid" style="margin-top: 12px;">
          ${kv("trajectory_state", review.trajectory_state)}
          ${kv("signal_continuity", review.signal_continuity)}
          ${kv("model", review.model, { translate: false })}
          ${kv("input_packet_hash", review.input_packet_hash, { translate: false })}
        </dl>
        <div class="two-column-notes llm-review-lists">
          <div><h3 class="subsection-title">观察到的变化</h3>${listHtml(asArray(review.observed_changes).map((item) => rawValueTextLabeled(item)), "无")}</div>
          <div><h3 class="subsection-title">跨因子相互作用</h3>${listHtml(review.cross_factor_interactions, "无")}</div>
          <div><h3 class="subsection-title">人工观察重点</h3>${listHtml(review.operator_focus, "无")}</div>
          <div><h3 class="subsection-title">失效条件</h3>${listHtml(review.invalid_if, "无")}</div>
        </div>
      </div>
    `;
  }

  function renderTheoreticalActiveView(view) {
    const active = asObject(view);
    if (!Object.keys(active).length) return "";
    return `
      <div class="llm-active-view">
        <div class="llm-review-topline">
          ${statusBadge("理论主动倾向", active.bias || "UNABLE_TO_JUDGE")}
          ${statusBadge("定性把握度", active.conviction || "LOW")}
        </div>
        <p class="llm-active-basis"><strong>理论依据</strong> ${escapeHtml(active.basis_cn || "暂无")}</p>
        <div class="two-column-notes llm-active-lists">
          <div><h3 class="subsection-title">关键驱动</h3>${listHtml(active.key_drivers, "无")}</div>
          <div><h3 class="subsection-title">反向证据</h3>${listHtml(active.counter_evidence, "无")}</div>
        </div>
        ${active.boundary_cn ? `<p class="llm-active-boundary">${escapeHtml(active.boundary_cn)}</p>` : ""}
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
          <p><strong>体制动力学</strong> ${escapeHtml(gamma.dynamics_cn || "暂无")}</p>
          <p><strong>主要尾部风险</strong> ${escapeHtml(gamma.dominant_tail_risk_cn || "暂无")}</p>
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
            <p>${escapeHtml(gamma.positioning_assumption_cn || "暂无")}</p>
          </div>
          <div>
            <h3 class="subsection-title">数据质量说明</h3>
            <p>${escapeHtml(gamma.data_quality_cn || "暂无")}</p>
          </div>
        </div>
        <p class="llm-gamma-boundary">全局 Gamma 体制分析只解释分布、尾部与反身性风险；它是风险叠加，不是方向信号，不改变系统结论、EDB、门控或交易许可。</p>
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
      return section("期权 Gamma / GEX 重点", "优先位保留给期权 gamma 状态与关键点位。", `<div class="empty">暂无 gex_info 或 gamma_regime</div>`);
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
    ].filter(Boolean);
    return section("GEX Rank 分位", "把 netGEX、IV/RV、P/C 等裸数值转换为当前样本窗口里的相对位置；冷启动期 quality 会保留显示。", `
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
      ctx.affects_confidence === false ? "不改置信" : "",
      ctx.affects_blocking === false ? "不改门控" : "",
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
    return section("信号时区置信度 / 前提耐久度", "展示信号成立时的时间先验；只读提示，不改变系统方向、置信、门控或交易许可。", `
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
      if (field === "vote") return benignNullHtml("不计票");
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
      FUNDING: "FUTURES_FUNDING_CROWDING",
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
      const rate = firstNumberFrom([detail, factor], ["funding_norm", "last_rate", "last_funding_rate"]);
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
      MACRO: ["macro_score", "score", "macro_regime", "regime", "verdict", "data_status", "data_confidence", "macro_data_confidence", "components", "component_scores", "macro_components_cn", "source_ref"]
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

  function hasRawTraceTarget(ref, doc) {
    const root = rawTraceRoot(ref);
    if (!root.startsWith("factor_cross_section.")) return false;
    const value = get(doc, root);
    if (isNullish(value)) return false;
    if (typeof value === "object") return Object.keys(asObject(value)).length > 0;
    return true;
  }

  function sourceRefLink(ref, doc, label = null) {
    if (isNullish(ref) || ref === "") return "";
    const root = rawTraceRoot(ref);
    const text = label || textClip(ref, 96);
    if (!hasRawTraceTarget(root, doc)) {
      return `<span class="chip">${escapeHtml(text)}</span>`;
    }
    return `<a class="source-ref-link chip" href="#${escapeHtml(rawTraceId(root))}" data-source-ref="${escapeHtml(root)}">${escapeHtml(text)}</a>`;
  }

  function sourceRefList(refs, doc) {
    return asArray(refs).map((ref) => sourceRefLink(ref, doc)).join("");
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
    const text = textClip(value);
    return `<span class="evidence-fact"><span>${escapeHtml(label)}</span><strong title="${escapeHtml(text)}">${escapeHtml(text)}</strong></span>`;
  }

  function macroComponentLabel(component) {
    const key = rawEnum(component && component.key).toUpperCase();
    if (key === "VOLQ" || key === "VXN" || key === "VIX") return `纳斯达克代理(${key})`;
    if (key === "DXY") return "美元(DXY)";
    if (key === "US10Y" || key === "TNX") return "美债10Y(US10Y)";
    return key || "macro_proxy";
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

  function fundingAssessment(evidence, doc, ctx) {
    const rate = evidenceFirstNumber(evidence, ctx, ["last_rate", "last_funding_rate"]);
    const threshold = 0.0001;
    let tendency = "中性";
    let note = "资金费率暂缺，仅保留为不计票辅助项。";
    let reflexive = "NEUTRAL";
    if (rate !== null) {
      if (rate > 0) tendency = `${Math.abs(rate) >= threshold ? "拥挤" : "温和"}多头倾向`;
      else if (rate < 0) tendency = `${Math.abs(rate) >= threshold ? "拥挤" : "温和"}空头倾向`;
      reflexive = signedLean(-rate) || "NEUTRAL";
      note = `${pairSymbol(doc)} 永续资金费率 ${ratePctText(rate)}，阈值 0.01%，当前为${tendency}。`;
    }
    return {
      stanceLabel: "费率端倾向",
      stance: tendency,
      sentence: `${note} 反身性辅助倾向为${semanticCompact(reflexive) || reflexive}，本项不直接改变 EDB 方向票。`,
      facts: [
        evidenceFact("funding", ratePctText(rate)),
        evidenceFact("threshold", "0.01%"),
        evidenceFact("reflexive", semanticCompact(reflexive) || reflexive),
        evidenceFact("effect", firstPresent(evidenceFirstValue(evidence, ctx, ["effect", "tmvf_funding_effect"]), "n/a"))
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
      sentence: `期权斜率投票 ${scalarText(vote, { translate: false, digits: 4 })}，风险逆转 ${scalarText(rrBlend, { translate: false, digits: 4 })}，当前判断为${leanText}。`,
      facts: [
        evidenceFact("rr_blend", rrBlend),
        evidenceFact("rr_z", rrZ),
        evidenceFact("delta_rr", deltaRr),
        evidenceFact("confidence", evidenceFirstValue(evidence, ctx, ["vote_confidence"]))
      ]
    };
  }

  function ggrAssessment(evidence, ctx) {
    const regime = evidenceFirstValue(evidence, ctx, ["regime", "market_state"]);
    const multiplier = evidenceFirstNumber(evidence, ctx, ["confidence_multiplier"]);
    const veto = evidenceFirstValue(evidence, ctx, ["veto"]);
    const flipDistance = evidenceFirstNumber(evidence, ctx, ["distance_to_flip_pct"]);
    const pinDistance = evidenceFirstNumber(evidence, ctx, ["distance_to_pin_pct"]);
    const stanceCode = evidenceAuxiliaryLean(evidence, { factor_cross_section: { gamma_regime: ctx.factor } });
    const stance = semanticCompact(stanceCode) || "空间约束";
    return {
      stanceLabel: "空间状态",
      stance,
      sentence: `Gamma 结构 ${scalarText(regime, { translate: false })}，置信乘子 ${scalarText(multiplier, { translate: false, digits: 4 })}，这是期权空间安全/门控约束，不作为偏多或偏空方向票。`,
      facts: [
        evidenceFact("netGEX", evidenceFirstValue(evidence, ctx, ["net_gamma_notional_usd", "net_gamma_notional"])),
        evidenceFact("flip", evidenceFirstValue(evidence, ctx, ["flip_point"])),
        evidenceFact("dist_flip", flipDistance === null ? null : pctPoint(flipDistance)),
        evidenceFact("dist_pin", pinDistance === null ? null : pctPoint(pinDistance)),
        evidenceFact("spatial_role", stance),
        evidenceFact("veto", veto)
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
      sentence: `量价主干 blend ${scalarText(blend, { translate: false, digits: 4 })}，窗口冲突 ${scalarText(windowConflict, { translate: false })}，作为主方向骨架。`,
      facts: [
        evidenceFact("tmv_blend", blend),
        evidenceFact("direction", direction),
        evidenceFact("24h", evidenceFirstValue(evidence, ctx, ["tmvf_24h_final"])),
        evidenceFact("48h", evidenceFirstValue(evidence, ctx, ["tmvf_48h_final"]))
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
      sentence: `主动流合成 vote ${scalarText(combinedVote, { translate: false, digits: 4 })}，一致性 ${scalarText(agreement, { translate: false })}，用于确认或削弱主方向。`,
      facts: [
        evidenceFact("absorption", absorption),
        evidenceFact("fast_4h", fast.verdict),
        evidenceFact("slow_12h", slow.verdict),
        evidenceFact("data", evidenceFirstValue(evidence, ctx, ["data_quality", "data_status"]))
      ]
    };
  }

  function macroAssessment(evidence, ctx) {
    const score = firstNumberFrom([ctx.raw, ctx.detail, ctx.factor], ["macro_score", "score"]);
    const regime = evidenceFirstValue(evidence, ctx, ["macro_regime", "regime"]);
    const leanText = semanticCompact(evidence.lean || signedLean(evidence.vote) || macroLeanFromScore(score)) || "UNKNOWN";
    const voteMissing = isNullish(evidence.vote);
    return {
      stance: leanText,
      sentence: `宏观背景 ${scalarText(regime, { translate: false })}，分数 ${scalarText(score, { translate: false, digits: 4 })}；score > 0 表示风险资产逆风。${voteMissing ? "本行 vote 为空代表本轮不作为方向投票或被门控排除，不代表 raw 宏观数据缺失。" : ""}`,
      facts: [
        evidenceFact("regime", regime),
        evidenceFact("score", score),
        evidenceFact("confidence", evidenceFirstValue(evidence, ctx, ["data_confidence", "macro_data_confidence"])),
        ...macroComponentFacts(ctx)
      ]
    };
  }

  function cvdAssessment(evidence, ctx) {
    const cvdSum = evidenceFirstNumber(evidence, ctx, ["cvd_sum"]);
    const strength = evidenceFirstNumber(evidence, ctx, ["normalized_strength", "strength"]);
    const verdict = evidenceFirstValue(evidence, ctx, ["verdict"]);
    const leanText = semanticCompact(evidence.lean || signedLean(evidence.vote)) || "UNKNOWN";
    return {
      stance: leanText,
      sentence: `CVD 分量 ${scalarText(cvdSum, { translate: false, digits: 4 })}，强度 ${scalarText(strength, { translate: false, digits: 4 })}，结论 ${scalarText(verdict, { translate: false })}。`,
      facts: [
        evidenceFact("cvd_sum", cvdSum),
        evidenceFact("strength", strength),
        evidenceFact("verdict", verdict)
      ]
    };
  }

  function defaultEvidenceAssessment(evidence, ctx) {
    const leanText = semanticCompact(evidence.lean || evidenceAuxiliaryLean(evidence, { factor_cross_section: {} })) || "UNKNOWN";
    return {
      stance: leanText,
      sentence: `${evidence.key || "Evidence"} 投票 ${scalarText(evidence.vote, { translate: false, digits: 4 })}，有效权重 ${scalarText(evidence.effective_weight, { translate: false, digits: 4 })}。`,
      facts: [
        evidenceFact("source", evidence.source_ref),
        evidenceFact("role", evidenceAuxiliaryRole(evidence))
      ]
    };
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
            <strong class="evidence-key">${escapeHtml(evidence.key || "N/A")}</strong>
            <span class="evidence-gloss">${escapeHtml(evidence.gloss_cn || "")}</span>
          </div>
          <div class="evidence-status">${statusBadge("", evidence.participation_status)}</div>
        </div>
        <div class="evidence-body">
          <div class="evidence-stance">
            <span>${escapeHtml(assessment.stanceLabel || "模块倾向")}</span>
            <strong>${escapeHtml(assessment.stance)}</strong>
          </div>
          ${evidenceImpactHtml(evidence)}
          <p class="evidence-judgement">${escapeHtml(assessment.sentence)}</p>
          ${evidenceFactsHtml(assessment.facts)}
          <div class="evidence-foot">
            <span>${escapeHtml(evidenceAuxiliaryRole(evidence) || "EDB_MODULE")}</span>
            ${evidence.source_ref ? sourceRefLink(evidence.source_ref, doc) : `<span>原始截面见下方</span>`}
            ${evidence.exclusion_reason ? `<span>${escapeHtml(textClip(evidence.exclusion_reason, 96))}</span>` : ""}
          </div>
        </div>
      </article>
    `;
  }

  function renderReasoning(doc) {
    const reasoning = asObject(get(doc, "reasoning", {}));
    const score = asObject(reasoning.score);
    const agreement = asObject(reasoning.agreement);
    const coverage = asObject(reasoning.coverage);
    const decomposition = asObject(reasoning.confidence_decomposition);
    const evidenceRows = asArray(reasoning.evidence)
      .map((evidence) => evidenceLedgerItem(evidence, doc))
      .join("");
    return section("完整证据账本", reasoning.summary_cn || "保留所有参与、排除、不计票和门控证据。", `
      <dl class="kv-grid" style="margin-bottom: 16px;">
        ${kv("Engine", reasoning.engine, { translate: false })}
        ${kv("Engine version", reasoning.engine_version, { translate: false })}
        ${kv("Score final", score.final, { translate: false })}
        ${kv("Agreement", agreement.value, { translate: false })}
        ${kv("Coverage", coverage.value, { translate: false })}
        ${kv("Confidence final", decomposition.confidence_final, { translate: false })}
      </dl>
      <div class="formula-block"><strong>Score</strong><span>${escapeHtml(score.method || "")}</span><span>weighted sum ${escapeHtml(scalarText(score.weighted_vote_sum, { translate: false }))} / effective weight ${escapeHtml(scalarText(score.effective_weight_sum, { translate: false }))}</span></div>
      <div class="evidence-ledger" style="margin-top: 16px;">${evidenceRows || `<div class="empty-inline">No evidence rows</div>`}</div>
      <h3 class="subsection-title">Confidence decomposition</h3>
      <dl class="kv-grid">
        ${kv("Strength", decomposition.strength, { translate: false })}
        ${kv("Agreement factor", decomposition.agreement_factor, { translate: false })}
        ${kv("Coverage factor", decomposition.coverage_factor, { translate: false })}
        ${kv("GGR multiplier", decomposition.ggr_multiplier, { translate: false })}
        ${kv("Confidence pre-veto", decomposition.confidence_pre_veto, { translate: false })}
        ${kv("Veto applied", decomposition.veto_applied)}
        ${kv("Confidence final", decomposition.confidence_final, { translate: false })}
      </dl>
    `);
  }

  function renderConflict(doc) {
    const conflict = asObject(get(doc, "conflict", {}));
    const dominant = asObject(conflict.dominant_conflict);
    return section("冲突解释", conflict.explanation_cn || "", `
      <dl class="kv-grid">
        ${kv("Method", conflict.method, { translate: false })}
        ${kv("Ratio", isNullish(conflict.ratio) ? null : percent(conflict.ratio), { translate: false })}
        ${kv("Level", conflict.level)}
        ${kv("Dominant aligned", dominant.aligned_key, { translate: false })}
        ${kv("Dominant dissent", dominant.dissent_key, { translate: false })}
      </dl>
      <div class="conflict-sides" style="margin-top: 16px;">
        <div class="side"><span class="side-label">Aligned keys</span><span class="side-value">${escapeHtml(asArray(conflict.aligned_keys).join(", ") || "无")}</span></div>
        <div class="side"><span class="side-label">Dissent keys</span><span class="side-value">${escapeHtml(asArray(conflict.dissent_keys).join(", ") || "无")}</span></div>
      </div>
      <div class="text-block"><p>${valueHtml(dominant.explanation_cn, { translate: false })}</p></div>
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

  function renderFactorCrossSection(doc) {
    const crossSection = asObject(get(doc, "factor_cross_section", {}));
    const navEntries = [];
    const blocks = Object.entries(crossSection).map(([key, value]) => {
      const ref = `factor_cross_section.${key}`;
      navEntries.push({ ref, label: key });
      const rows = flatten(value).map(([path, scalar]) => {
        const renderedValue = Array.isArray(scalar) || (scalar && typeof scalar === "object")
          ? `<code>${escapeHtml(JSON.stringify(scalar))}</code>`
          : valueHtmlByPath(path, scalar, { scope: key });
        return `<tr><td class="field-path">${escapeHtml(fieldLabel(path))}</td><td>${renderedValue}</td></tr>`;
      }).join("");
      const status = get(value, "data_status", get(value, "status"));
      return `
        <details id="${escapeHtml(rawTraceId(ref))}" class="factor-detail raw-trace-group" ${["tmvf", "micro_flow", "gamma_regime"].includes(key) ? "open" : ""}>
          <summary><span>${escapeHtml(key)}</span>${!isNullish(status) ? statusBadge("", status) : ""}</summary>
          <div class="table-wrap"><table class="field-table"><thead><tr><th>${escapeHtml(fieldLabel("Field"))}</th><th>${escapeHtml(fieldLabel("Value"))}</th></tr></thead><tbody>${rows}</tbody></table></div>
        </details>
      `;
    }).join("");
    const evidenceRawBlocks = asArray(get(doc, "reasoning.evidence", []))
      .map((evidence) => {
        const raw = asObject(evidence && evidence.raw_values);
        if (!Object.keys(raw).length) return "";
        const ref = `evidence_raw_values.${evidenceKey(evidence)}`;
        navEntries.push({ ref, label: ref });
        const rows = flatten(raw).map(([path, scalar]) => {
          const renderedValue = Array.isArray(scalar) || (scalar && typeof scalar === "object")
            ? `<code>${escapeHtml(JSON.stringify(scalar))}</code>`
            : valueHtmlByPath(path, scalar, { scope: evidenceKey(evidence), translate: false });
          return `<tr><td class="field-path">${escapeHtml(fieldLabel(`${evidenceKey(evidence)}.${path}`))}</td><td>${renderedValue}</td></tr>`;
        }).join("");
        return `
          <details id="${escapeHtml(rawTraceId(ref))}" class="factor-detail evidence-raw-detail raw-trace-group">
            <summary><span>${escapeHtml(`evidence_raw_values.${evidenceKey(evidence)}`)}</span>${statusBadge("", evidence.participation_status)}</summary>
            <div class="table-wrap"><table class="field-table"><thead><tr><th>${escapeHtml(fieldLabel("Field"))}</th><th>${escapeHtml(fieldLabel("Value"))}</th></tr></thead><tbody>${rows}</tbody></table></div>
          </details>
        `;
      })
      .join("");
    const nav = navEntries.length
      ? `<nav class="raw-trace-nav" aria-label="原始截面快速跳转"><span class="raw-trace-nav-title">原始截面跳转</span>${navEntries.map((item) => `<a href="#${escapeHtml(rawTraceId(item.ref))}">${escapeHtml(item.label)}</a>`).join("")}</nav>`
      : "";
    return section("因子原始截面", "按 JSON 分组保留完整字段；账本只显示决策摘要，完整原始值在这里追溯。", `${nav}<div class="factor-list">${blocks || `<div class="empty">暂无 factor_cross_section</div>`}${evidenceRawBlocks}</div>`);
  }

  function renderProvenance(doc) {
    const provenance = asObject(get(doc, "provenance", {}));
    const sourceSnapshot = asObject(provenance.source_snapshot);
    const configSnapshot = asObject(provenance.config_snapshot);
    const versions = asObject(provenance.component_versions);
    const integrity = asObject(get(doc, "integrity", {}));
    const delivery = asObject(get(doc, "delivery", {}));
    const hasSourceSnapshot = Object.values(sourceSnapshot).some((value) => !isBlank(value));
    const hasConfigSnapshot = Object.values(configSnapshot).some((value) => !isBlank(value));
    const deliveryRows = Object.entries(delivery)
      .filter(([key]) => key !== "fmz_push_summary");
    const sourceSnapshotBlock = hasSourceSnapshot ? `
      <dl class="kv-grid">
        ${kv("Source snapshot id", sourceSnapshot.snapshot_id, { translate: false, nullText: "未提供", nullClass: "benign-null-value" })}
        ${kv("Source snapshot hash", sourceSnapshot.hash, { translate: false, nullText: "未提供", nullClass: "benign-null-value" })}
        ${!isBlank(sourceSnapshot.local_ref) ? kv("local_ref", sourceSnapshot.local_ref, { translate: false }) : ""}
      </dl>
    ` : `<div class="empty-inline">当前卡未提供独立源快照；以组件版本、交付路径和完整性字段作为留档线索。</div>`;
    const configSnapshotBlock = hasConfigSnapshot ? `
      <dl class="kv-grid">
        ${kv("Config id", configSnapshot.config_id, { translate: false, nullText: "未提供", nullClass: "benign-null-value" })}
        ${kv("Config hash", configSnapshot.hash, { translate: false, nullText: "未提供", nullClass: "benign-null-value" })}
      </dl>
    ` : `<div class="empty-inline">当前卡未提供配置快照；这不是行情数据缺失。</div>`;
    return section("来源、交付与完整性", "只展示可追溯的运行模式、版本、交付路径和完整性字段；未启用或当前版本不产出的快照项不再按缺失处理。", `
      <h3 class="subsection-title">Provenance</h3>
      <dl class="kv-grid">
        ${kv("Runtime mode", provenance.runtime_mode)}
      </dl>
      <h3 class="subsection-title">Source snapshot</h3>
      ${sourceSnapshotBlock}
      <h3 class="subsection-title">Config snapshot</h3>
      ${configSnapshotBlock}
      <h3 class="subsection-title">Component versions</h3>
      <ul class="audit-list">${Object.entries(versions).map(([key, value]) => `<li><span class="audit-key">${escapeHtml(key)}</span><span class="audit-value">${valueHtml(value, { translate: false })}</span></li>`).join("") || `<li class="empty-inline">暂无组件版本</li>`}</ul>
      <h3 class="subsection-title">Delivery</h3>
      <div class="push-summary">${valueHtml(stripConfidenceCalibrationReminder(delivery.fmz_push_summary), { translate: false })}</div>
      <ul class="audit-list" style="margin-top: 12px;">${deliveryRows.map(([key, value]) => `<li><span class="audit-key">${escapeHtml(key)}</span><span class="audit-value">${valueHtmlByPath(key, value, { scope: "delivery", translate: false })}</span></li>`).join("") || `<li class="empty-inline">暂无交付路径</li>`}</ul>
      ${isBlank(delivery.static_web_url) ? `<div class="empty-inline">未启用静态深链；当前可通过 FMZ Log、本地 JSONL 或 materialized card 路径定位。</div>` : ""}
      <h3 class="subsection-title">Integrity</h3>
      <ul class="audit-list">${Object.entries(integrity).map(([key, value]) => `<li><span class="audit-key">${escapeHtml(key)}</span><span class="audit-value">${value && typeof value === "object" ? `<code>${escapeHtml(JSON.stringify(value))}</code>` : valueHtmlByPath(key, value, { scope: "integrity", translate: false })}</span></li>`).join("")}</ul>
    `);
  }

  function renderDocument(doc) {
    if (!doc) {
      $("#documentView").innerHTML = `${renderLoadNotice()}<div class="empty">请选择一份信号文档</div>`;
      return;
    }
    const currentDecision = decision(doc);
    const schema = asObject(get(doc, "schema", {}));
    const identity = asObject(get(doc, "identity", {}));
    const quality = asObject(get(doc, "quality", {}));
    const conflict = asObject(get(doc, "conflict", {}));
    const price = get(doc, "market_context.price", get(doc, "market_price"));
    $("#documentView").innerHTML = `
      ${renderLoadNotice()}
      <header class="doc-header">
        <div>
          <p class="eyebrow">${escapeHtml(`${schema.name || "signal_review_card"}@${schema.version || "unknown"} / ${schema.status || ""}`)}</p>
          <h1 class="doc-title">${escapeHtml(symbol(doc))} 信号审计卡</h1>
          <p class="doc-subtitle">${escapeHtml(identity.strategy_name || "")}${identity.strategy_version ? ` ${escapeHtml(identity.strategy_version)}` : ""} · ${escapeHtml(dateText(confirmedAt(doc)))} · ${escapeHtml(cardId(doc))}</p>
        </div>
        <div class="status-stack">
          ${statusBadge("Direction", lean(doc), true)}
          ${statusBadge("Action", support(doc))}
          ${statusBadge("Quality", quality.overall)}
          ${identity.is_synthetic ? statusBadge("Record", "SYNTHETIC") : ""}
        </div>
      </header>
      <div class="metric-strip" aria-label="信号关键指标">
        ${metric("Market price", price, get(doc, "market_context.quote_currency", ""))}
        ${metric("Evidence strength", currentDecision.evidence_strength)}
        ${metric("Confidence", currentDecision.confidence)}
        ${metric("Conflict ratio", isNullish(conflict.ratio) ? null : `${number(conflict.ratio * 100, 1)}%`, semanticCompact(conflict.level))}
        ${metric("Data quality", semanticCompact(quality.overall), quality.all_required_sources_ready ? "required ready" : "requires review")}
      </div>
      ${renderGammaOverview(doc)}
      ${renderGexRank(doc)}
      ${renderSignalSessionContext(doc)}
      ${renderTransitionContext(doc)}
      ${renderLlmReview(doc)}
      ${renderDecision(doc)}
      ${renderDecisionMatrix(doc)}
      ${renderDisplayLayers(doc)}
      ${renderQuality(doc)}
      ${renderBlocking(doc)}
      ${renderReasoning(doc)}
      ${renderConflict(doc)}
      ${renderFactorCrossSection(doc)}
      ${renderProvenance(doc)}
    `;
  }

  function renderLoadNotice() {
    if (!loadState.error) return "";
    return `
      <div class="load-alert" role="alert">
        <strong>静态信号卡加载失败</strong>
        <p>未展示内置样例；请检查 signal_cards/index.json 与单卡 JSON 是否已由 materialize 任务生成并发布。错误：${escapeHtml(loadState.error)}</p>
      </div>
    `;
  }

  function render() {
    const list = filteredDocuments();
    renderIndex(list);
    renderDocument(documents.find((doc) => cardId(doc) === state.currentId));
  }

  async function start() {
    setupFilterEvents();
    documents = await loadDocuments();
    state.currentId = documents[0] ? cardId(documents[0]) : null;
    populateFilters();
    render();
  }

  start();
})();
