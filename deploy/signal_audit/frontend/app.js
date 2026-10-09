(() => {
  "use strict";

  const API_WORKBENCH = "/api/workbench";
  const API_REVIEW = "/api/workbench/review";
  const API_REVIEW_STATE = "/api/workbench/review-state";
  const API_WORKBENCH_SESSION = "/api/workbench/session";
  const API_LOCKED_CANDIDATES = "/api/workbench/locked-candidates";
  const HISTORY_MANIFEST = "signal_cards/index.json";
  const POLL_MS = 60_000;
  const REVIEW_STATE_POLL_MS = 3_000;
  const MAX_DAILY_SNAPSHOTS = 15;
  const LOCKED_DETAIL_CACHE_LIMIT = 4;
  const RAW_FOLD_CACHE_LIMIT = 256;
  const RENDERER_IDENTITY = String(document.currentScript?.src || document.querySelector('script[src*="app.js"]')?.src || "").split("?v=")[1]?.split("&")[0] || "not_recorded";
  const loopbackHosts = ["localhost", "127.0.0.1", "[::1]", "::1"];
  const isLoopbackHost = () => loopbackHosts.includes(window.location.hostname);
  const secureReviewOrigin = () => window.location.protocol === "https:" || isLoopbackHost();
  const deferUi = (callback, delay = 0) => {
    const scheduler = typeof window.setTimeout === "function"
      ? window.setTimeout.bind(window)
      : (typeof setTimeout === "function" ? setTimeout : null);
    if (!scheduler) {
      callback();
      return 0;
    }
    try {
      return scheduler(callback, delay);
    } catch (_error) {
      callback();
      return 0;
    }
  };

  const state = {
    apiOnline: false,
    loading: true,
    lastError: "",
    lastLoadedAt: null,
    workbench: null,
    historyLoaded: false,
    historyLoading: false,
    historyError: "",
    historyItems: [],
    historyFilter: "",
    rawCard: null,
    reviewBusy: false,
    reviewMessage: "",
    session: { authenticated: false, expires_at_ms: null },
    sessionLoading: false,
    sessionMessage: "",
    pendingCandidateId: "",
    pendingReviewPurpose: "review",
    pendingTokenAction: "",
    pendingLockCandidateId: "",
    pendingLockOptions: {},
    pendingLockedEpisodeId: "",
    pendingLockedReviewSnapshotId: "",
    activeReviewKey: "",
    lockedEpisodes: [],
    lockedComparisons: {},
    lockedComparisonOrder: [],
    lockedEvidenceCache: {},
    lockedEvidenceOrder: [],
    lockedLoading: false,
    lockedMutationBusy: false,
    lockedError: "",
    lockedMessage: "",
    activeLockedEpisodeId: "",
    lockedDetailRequestSeq: 0,
    selectedMapCandidateKey: "",
    selectedMapRegionId: "",
    selectedMapRegionContextKey: "",
    mapAxisMode: "focus",
    observationRenderedAt: null,
    pendingHashOpen: Boolean(window.location.hash),
    pendingHashScroll: Boolean(window.location.hash && window.location.hash !== "#candidatesTitle"),
  };

  const rawFoldCache = new Map();
  let rawFoldSeq = 0;
  let renderingOfflineExport = false;
  let reviewStateTimer = null;
  let reviewPollSawInflight = false;
  let reviewPollActive = false;

  const $ = (selector) => document.querySelector(selector);
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

  const asObject = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const asArray = (value) => Array.isArray(value) ? value : [];
  const firstPresent = (...values) => values.find((value) => value !== undefined && value !== null && value !== "");
  const safeNumber = (value) => {
    if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
    const numeric = typeof value === "number" ? value : Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  };
  const shortId = (value, size = 12) => {
    const text = String(value ?? "").trim();
    return text.length > size ? `${text.slice(0, size)}…` : (text || "未提供");
  };

  function label(value, fallback = "未提供") {
    const raw = String(value ?? "").trim();
    if (!raw) return fallback;
    const lower = raw.toLowerCase();
    const labels = {
      STRUCTURE_ELIGIBLE: "空间约束：可建立候选",
      STRUCTURE_BLOCKED: "空间约束：当前不准入",
      producer_native: "FMZ 原生来源",
      producer_native_space_gate: "FMZ 原生 SpaceGate",
      producer_native_space_gate_missing: "缺少 FMZ 原生 SpaceGate",
      simulated_space_gate: "本地模拟 SpaceGate",
      synthetic_fixture: "本地模拟场景",
      deribit_index: "Deribit BTC USD 指数",
      "gexmonitorapi.gex_board.total_net_gex": "GEX 看板净 Gamma（USD）",
      _missing: "缺少 FMZ 原生 SpaceGate",
      missing_or_invalid: "缺原生 SpaceGate",
      put: "Put",
      call: "Call",
      PUT: "Put",
      CALL: "Call",
      valid: "有效",
      Valid: "有效",
      usable: "可用",
      USABLE: "可用",
      partial: "局部缺口",
      available: "可用",
      unavailable: "不可用",
      invalid: "无效",
      stale: "过期",
      missing: "缺失",
      unknown: "未知",
      FRESH: "新鲜",
      OK: "完成",
      UP: "上行",
      DOWN: "下行",
      RANGE_UP: "区间内偏上行",
      SUPPORTIVE: "支持",
      NEUTRAL: "中性",
      Neutral: "中性",
      MIXED: "混合",
      NONE: "无",
      LOW: "低",
      IDLE: "空闲",
      POSITIVE_GAMMA_PINNING: "正 Gamma 钉扎",
      MILD_TREND: "温和趋势",
      BUY_CONFIRMS_UP: "主动买盘与上涨一致",
      SELL_CONFIRMS_DOWN: "主动卖盘与下跌一致",
      BASELINE: "资金费无额外方向提示",
      "Mild Tailwind": "温和顺风",
      "Mild Headwind": "温和逆风",
      MILD_BULLISH: "温和偏多",
      MILD_BEARISH: "温和偏空",
      CALL_SIDE_CONTEXT: "Call 侧背景",
      PUT_SIDE_CONTEXT: "Put 侧背景",
      WEAK_SUPPORT: "弱支持",
      UNCALIBRATED: "未校准（非胜率）",
      local_synthetic_preview: "本地模拟运行",
      synthetic_local_preview: "本地模拟价格",
      synthetic_preview: "本地模拟资料",
      binance_spot: "Binance BTC 现货",
      DERIBIT: "Deribit",
      interrupted_no_automatic_recall: "上次评审中断，未自动重发",
      synthetic_preview_not_production: "本地模拟，不作生产事实",
      fmz_runtime_not_live_public: "非 FMZ 实时公开只读模式",
      "fmz.flow": "FMZ 量价事实",
      "fmz.macro_pressure": "FMZ 宏观事实",
      "fmz.space_context": "FMZ 空间事实",
      "fmz.edb_reasoning": "FMZ 信号推理",
      volume_price: "量价",
      macro_pressure: "宏观",
      space: "空间",
      space_gate: "空间门",
      edb: "EDB",
      flow_window_conflict: "量价窗口冲突",
      macro_shock_block: "宏观冲击阻断",
      gamma_regime_veto: "Gamma 体制否决",
      edb_conflict_medium: "EDB 中等冲突",
      edb_conflict_material: "EDB 明显冲突",
      edb_conflict_severe: "EDB 严重冲突",
      edb_conflict_mild: "EDB 轻微分歧",
      MILD: "轻微",
      MATERIAL: "明显",
      SEVERE: "严重",
      fmz_price_or_source_missing: "FMZ 现价或来源缺失、无效",
      positive: "正",
      negative: "负",
      POSITIVE: "正",
      NEGATIVE: "负",
      true: "是",
      false: "否",
      ok: "完成",
      rejected: "拒绝",
      REJECTED: "拒绝",
      failed: "失败",
      FAILED: "失败",
      pending: "等待中",
      inflight: "正在推理",
      SIMULATED: "模拟回执",
      EXPLAINED: "适用性可解释",
      CONTESTED: "适用性存在反证",
      UNABLE_TO_JUDGE: "适配暂不能判定",
      not_requested: "未请求",
      unavailable: "不可用",
      reviewable: "资料可复核",
      information_gap: "资料缺口",
      reference_insufficient: "参考补偿不足",
      expired_or_stale: "过期或报价陈旧",
      live_public_read_only: "实时公开只读",
      sequential_leg_book_estimate: "公开双腿顺序盘口估算，未成交",
      configured_assumption: "配置费用假设",
      not_attached: "未绑定同版评审",
      supported: "参考支持域已核验",
      stale_protocol_review: "旧协议回执，仅保留历史",
      RESEARCH_OK: "研究复核完成，禁止生产附着",
      not_configured: "通知未配置",
      not_sent: "未发送",
      attempt_budget_exhausted: "单版评审额度已用尽",
      daily_budget_deferred: "北京日共享额度不足",
      persistence_failed: "评审结果持久化失败",
      snapshot_changed_no_automatic_recall: "快照变化，未自动重发",
      current: "当前",
      background_only: "仅背景",
      fmz_snapshot_file_missing: "FMZ 连续事实文件不可用",
      fmz_fact_not_current: "FMZ 当前事实不可得",
      fmz_nonfuture_snapshot_missing: "FMZ 非未来快照缺失",
      historical_replay_not_current: "冻结回放不是当前事实",
      gex_effective_flip_unavailable: "GEX 有效 Flip 不可得",
      kpf_cached_artifact_unavailable: "KPF 缓存制品不可得",
      fmz_producer_version_mismatch: "FMZ 版本与当前工作台不一致",
      space_gate_producer_version_mismatch: "空间门来自旧版 FMZ，不能准入",
      space_gate_missing: "缺少 FMZ 原生 SpaceGate",
      space_gate_clock_invalid_or_stale: "SpaceGate 时间无效或陈旧",
      space_gate_eligible_facts_inconsistent: "SpaceGate 与 Anchor/Gamma 事实不一致",
      SPACE_GATE_MISSING: "缺少 FMZ 原生 SpaceGate",
      SPACE_GATE_CLOCK_INVALID_OR_STALE: "SpaceGate 时间无效或陈旧",
      SPACE_GATE_ELIGIBLE_FACTS_INCONSISTENT: "SpaceGate 与 Anchor/Gamma 事实不一致",
      SPACE_GATE_SOURCE_MISSING: "SpaceGate 来源缺失",
      SPACE_GATE_FMZ_FACT_MISSING: "FMZ 当前事实不可得",
      SPACE_GATE_BLOCKED: "空间门阻断",
      SPACE_GATE_NOT_ELIGIBLE: "空间门当前不准入",
      SPACE_GATE_ANCHOR_NOT_FRESH: "Anchor 来源已过期",
      SPACE_GATE_ANCHOR_NOT_VALID: "Anchor 当前无效",
      SPACE_GATE_ANCHOR_SCORE_LOW: "Anchor 评分低于 60",
      SPACE_GATE_ANCHOR_SCORE_MISSING: "Anchor 评分缺失",
      SPACE_GATE_ANCHOR_SOURCE_TS_MISSING: "Anchor 来源时点缺失",
      SPACE_GATE_GEX_INFO_ASSET_MISMATCH: "Gamma 来源不是当前 BTC 标的",
      SPACE_GATE_GEX_INFO_AVAILABILITY_NOT_OK: "Gamma 来源不可用",
      SPACE_GATE_GEX_INFO_DATA_STATE_NOT_OK: "Gamma 数据状态不合格",
      SPACE_GATE_GEX_INFO_MISSING: "Gamma 来源缺失",
      SPACE_GATE_GEX_INFO_QUALITY_NOT_OK: "Gamma 来源质量不合格",
      SPACE_GATE_GEX_INFO_STALE: "Gamma 来源已过期",
      SPACE_GATE_GGR_CONFLICT: "聚合与局部 Gamma 方向冲突",
      SPACE_GATE_GGR_DATA_NOT_OK: "Gamma 状态数据不合格",
      SPACE_GATE_GGR_MISSING: "Gamma 状态事实缺失",
      SPACE_GATE_GGR_REGIME_MISSING: "Gamma 状态区间缺失",
      SPACE_GATE_GGR_TRANSITION: "Gamma 正在过渡",
      SPACE_GATE_NET_GAMMA_MISSING: "净 Gamma 数值缺失",
      SPACE_GATE_NET_GAMMA_NOT_FRESH: "净 Gamma 已过期",
      SPACE_GATE_NET_GAMMA_NOT_POSITIVE: "净 Gamma 非正",
      SPACE_GATE_NET_GAMMA_SOURCE_TS_FUTURE: "净 Gamma 来源时间晚于当前截面",
      SPACE_GATE_NET_GAMMA_SOURCE_TS_MISSING: "净 Gamma 来源时点缺失",
      anchor_below_60: "Anchor 低于 60",
      anchor_not_valid: "Anchor 非 Valid",
      ANCHOR_BELOW_60: "Anchor 低于 60",
      ANCHOR_NOT_VALID: "Anchor 非 Valid",
      gamma_non_positive: "NetGamma 非正",
      gamma_stale: "Gamma 数据过期",
      gamma_transition: "Gamma 过渡状态",
      GAMMA_NON_POSITIVE: "NetGamma 非正",
      NET_GAMMA_NON_POSITIVE: "NetGamma 非正",
      GAMMA_STALE: "Gamma 数据过期",
      GAMMA_TRANSITION: "Gamma 过渡状态",
      source_conflict: "来源冲突",
      SOURCE_CONFLICT: "来源冲突",
      frozen_risk_reference_missing: "冻结赔付参考缺失",
      risk_support_not_verified: "风险参考支持未核验",
      quote_invalid: "报价无效",
      contract_invalid: "合同无效",
      m_ref_not_positive: "M_ref 不为正",
      m_ref_null: "M_ref 缺失",
      m_ref_non_positive: "M_ref 非正",
      m_ref_invalid: "M_ref 数值无效",
      quote_invalid_or_stale: "报价无效或已过期",
      current_quality_missing: "当前价源质量缺口",
      CURRENT_QUALITY_MISSING: "当前价源质量缺口",
      source_quality_missing: "来源质量缺口",
      SOURCE_QUALITY_MISSING: "来源质量缺口",
      quality_missing: "质量缺口",
      QUALITY_MISSING: "质量缺口",
      short_book_stale_or_future: "卖出腿盘口时间无效或陈旧",
      long_book_stale_or_future: "保护腿盘口时间无效或陈旧",
      leg_book_time_skew: "两腿盘口不同步",
      leg_index_mismatch: "两腿指数价格与估值现价不一致",
      leg_index_missing: "盘口指数价格缺失",
      short_depth_insufficient: "卖出腿可见深度不足",
      long_depth_insufficient: "保护腿可见深度不足",
      fee_unknown: "理论费用缺失",
      fee_components_invalid: "费用分项与总额不一致",
      fee_coverage_invalid: "费用覆盖范围无效",
      fee_coverage_incomplete: "建仓或交割费用未覆盖",
      short_delta_missing_or_invalid: "卖出腿 Delta 缺失或无效",
      short_delta_time_missing: "卖出腿 Delta 来源时间缺失",
      short_delta_timestamp_invalid: "卖出腿 Delta 时间格式无效",
      short_delta_stale_or_future: "卖出腿 Delta 已过期或时间超前",
      short_delta_sign_mismatch: "卖出腿 Delta 方向与 Put/Call 不符",
      short_delta_abs_above_max: "卖出腿绝对 Delta 超过 0.5",
      short_delta_candidate_missing: "没有合格卖出腿 Delta 候选",
      protection_leg_missing_exact_width: "缺少精确 2,000 USD 宽度的保护腿",
      short_put_strict_otm_missing: "没有严格价外 Put 卖出腿",
      short_call_strict_otm_missing: "没有严格价外 Call 卖出腿",
      fee_missing_or_incomplete: "费用资料不完整",
      snapshot_missing: "当前保单快照缺失",
      candidate_gate_version_changed_during_refresh: "保单刷新期间空间门版本变化",
      manual_review_service_not_configured: "手动评审服务未配置",
      manual_review_cooldown_active: "手动评审冷却中",
      manual_review_inflight: "同版手动评审正在推理",
      manual_review_budget_exhausted: "手动评审额度已用尽",
      MANUAL_REVIEW_COOLDOWN_ACTIVE: "手动评审冷却中",
      MANUAL_REVIEW_INFLIGHT: "同版手动评审正在推理",
      MANUAL_REVIEW_BUDGET_EXHAUSTED: "手动评审额度已用尽",
      source_clock_missing: "源钟缺少观察截止或首次可得",
      map_cutoff_missing: "MAP截止缺失",
      source_manifest_cutoff_missing: "来源清单截止缺失",
      source_manifest_cutoff_mismatch: "来源清单截止与MAP截止不一致",
      observation_after_first_seen: "观察截止晚于首次可得",
      observation_after_cutoff: "观察截止晚于MAP截止",
      first_seen_after_cutoff: "首次可得晚于MAP截止",
      published_after_cutoff: "发布时间晚于MAP截止",
      qualified_after_cutoff: "资格判定晚于MAP截止",
      observation_start_invalid: "观察起点非正",
      observation_start_after_end: "观察起点晚于观察截止",
      observation_start_after_cutoff: "观察起点晚于MAP截止",
      risk_reference_missing_or_unqualified: "冻结风险参考缺失或不合格",
      snapshot_not_reviewable: "当前保单资料未达到评审条件",
      structure_gate_not_eligible: "空间门当前不准入",
      cooldown_active: "600 秒冷却中",
      auth_required: "需要访问会话",
      sent: "已发送",
      failed: "失败",
      pending_transition: "等待下一次合格发送",
      contract: "本单合同",
      quote: "公开盘口",
      fee: "理论费用",
      risk: "冻结风险参考",
      economics: "补偿账",
      allocation: "配置",
      inventory: "库存",
      financing: "融资",
      external_conditions: "外部",
      OPTION_STRUCTURE: "期权结构",
      TRADED_ACCEPTANCE: "历史接受区",
      ONCHAIN_COST: "链上成本",
      CONTRACT_LIABILITY: "合同责任",
      MARKET_REFERENCE: "市场参照",
      RESPONSE_EVENT: "区域响应",
      PUT_REFERENCE: "Put 结构参考",
      CALL_REFERENCE: "Call 结构参考",
      PUT_WALL: "Put 墙",
      CALL_WALL: "Call 墙",
      EFFECTIVE_FLIP: "有效 Flip",
      KPF: "KPF 历史接受区",
      KPF_BASIN: "KPF 接受区",
      CP_STH: "短期持有者资本化价格",
      CP_LTH: "长期持有者资本化价格",
      CP_ALL: "全链资本化价格",
      PIN: "钉扎位",
      SHORT_LEG: "卖出腿",
      PROTECTIVE_LEG: "保护腿",
      NEAR_BREAKEVEN: "近端盈亏平衡",
      POINT: "单价位",
      OBSERVED_ZONE: "观察区间",
      BACKGROUND_USABLE: "可作背景",
      COORDINATE_USABLE: "可映射价格轴",
      COMPARABLE: "可比坐标",
      SAME_BASIS: "同价系",
      QUALIFIED: "已核验",
      NOMINAL_ONLY: "名义参考",
      GLOBAL_ONLY: "全局参考",
      NOT_COMPARABLE: "不可精确映射",
      DISPLAY_ONLY: "仅展示参考",
      HISTORICAL_ONLY: "仅历史",
      STALE: "过期",
      CURRENT: "当前",
      NOT_RUN: "未运行",
      REFERENCE_ANALYSIS_MISSING: "参考分析缺失",
      RISK_REFERENCE_NOT_QUALIFIED: "风险参考未合格",
      MU_REFERENCE_MISSING: "μ_ref 缺失",
      M_REFERENCE_MISSING: "M_ref 缺失",
      PRODUCT_REVIEW_EVIDENCE_DOES_NOT_WRITE_PRODUCTION_MU_M: "证据包不写入生产 μ/M",
      CATALOG_GAP: "有限目录容量不足",
      REQUIRED_IDS_EXCEED_BOUNDED_SELECTION_CAPACITY: "必选依据超过本版目录容量",
      PATH_OBSERVED: "已观察同候选路径",
      deterministic_program_no_llm: "程序事实归因（无 LLM）",
      NO_PATH_DUE_TO_WINDOW_GAP: "窗口不足",
      NO_PATH: "完整路径尚不可确认",
      OBSERVED: "已观察",
      INSUFFICIENT: "覆盖不足",
      NATIVE_WINDOW_QUALIFIED: "原生窗口合格",
      SYNTHETIC_WINDOW_COVERED: "模拟窗口覆盖",
      INSUFFICIENT_WINDOW: "窗口不足",
      STRICT_SAME_CANDIDATE_DELTA: "同候选增量",
      NO_STRICT_DELTA: "严格同窗变化尚不可确认",
      outside_liability: "责任区外侧",
      strong_bullish_bias: "强上行偏向",
      "wpem.level": "同保单位置读数",
      "wpem.delta": "同保单严格变化",
      "wpem.window": "同保单窗口",
      "wpem.fragments": "同保单已观察片段",
      "wpem.compensation": "补偿变化",
      "wpem.path": "同保单路径",
      single_direction_favorable_relief: "单向责任缓解",
      single_direction_adverse_pressure: "单向责任加压",
      repeated_cross_zone: "反复跨区",
      persistent_same_zone: "持续同区",
      PARTIAL: "部分可用",
      GAP: "缺口",
      FACTS_UNTIL_MANUAL_REVIEW: "仅事实，待人工审查",
      before_payout: "赔付开始前",
      partial_payout: "部分赔付区",
      beyond_protection: "保护腿之外",
      partial_payout_at_near_be: "跨近端 BE",
      before_payout_at_short: "触及卖出腿",
      beyond_protection_at_long: "触及保护腿",
      latest_adjacent_quote_pair: "相邻同合同观察",
      liability_zone: "责任位置",
      micro_neutral: "微观主动流中性",
      source_clock_status: "来源时钟状态",
      consumed_at_ms: "消费时间",
      produced_refs: "系统生成证据引用",
      source_records: "来源记录",
      NOT_RETAINED: "未留存",
      BELOW_CURRENT: "当前价下方",
      ABOVE_CURRENT: "当前价上方",
      CONTAINS_CURRENT: "覆盖当前价",
      official_settlement_inside_contract_comparable_coordinate: "可比坐标内官方交割价情景",
      nominal_number_if_official_settlement_equaled_it: "名义数值情景，非精确合同映射",
      nominal_scenario_not_precise_contract_mapping: "名义数值不作精确合同映射",
      synthetic_fixture_not_current_source: "本地模拟资料，非当前真实来源",
      nominal_only_not_contract_comparable: "名义参考，不是合同可比坐标",
      early_exit_cashflow: "未覆盖提前退出现金流",
      slippage: "未覆盖滑点",
      margin_liquidation: "未覆盖保证金与强平",
      hedge_cashflow: "未覆盖对冲现金流",
      no_contract_comparable_coordinate: "缺少可比合同坐标",
      display_only_no_strict_geometry: "仅展示带，不作严格几何",
      acceptance_basin_unknown: "当前接受区未知",
      static_net_unavailable_fee_or_credit_missing: "费用或信用缺失，静态净结果不可得",
      hold_to_expiry_static_result_with_listed_fees: "持有到期静态账，含列明费用",
      entry_only_static_result_not_full_actual_net: "仅建仓费用，不是完整实际净收益",
      touch_is_not_terminal_settlement: "盘中触区不是到期交割",
      POSITION_OBSERVED: "价格位置已记录",
      PRICE_SAMPLE: "价格样本",
      COMPLETE: "完整覆盖",
      INSIDE: "位于区间内",
      OUTSIDE: "位于区间外",
      ABOVE: "高于区域",
      BELOW: "低于区域",
      TOUCHING: "接近区域",
    };
    if (labels[raw] || labels[lower]) return labels[raw] || labels[lower];
    return /^[A-Za-z0-9_]+$/.test(raw) ? "未归类资料状态（见原始记录）" : raw;
  }

  function normalizeInlineText(value) {
    return String(value ?? "")
      .replace(/\s+/g, " ")
      .replace(/[;；]\s*[;；]+/g, "；")
      .replace(/[。．]\s*[;；]+/g, "。")
      .replace(/[;；]\s*[。．]+/g, "。")
      .replace(/[。．]\s*[。．]+/g, "。")
      .trim();
  }

  function trimSentenceEdge(value) {
    return normalizeInlineText(value).replace(/^[,，、;；。．\s]+|[,，、;；。．\s]+$/g, "");
  }

  const displayTokenLabels = {
    outside_liability: "责任区外侧",
    strong_bullish_bias: "强上行偏向",
    neutral_to_bullish: "中性至偏多",
    NOT_CROWDED: "未显示拥挤",
    independence_status: "独立性状态",
    NO_PATH_DUE_TO_WINDOW_GAP: "窗口不足，不能证明完整路径",
    NO_PATH: "完整路径尚不可确认",
    NO_STRICT_DELTA: "严格同窗变化尚不可确认",
    INSUFFICIENT: "覆盖不足",
    PARTIAL: "部分可用",
    contract: "本单合同",
    "wpem.level": "同保单位置读数",
    "wpem.delta": "同保单严格变化",
    "wpem.window": "同保单窗口",
    "wpem.fragments": "同保单已观察片段",
    "wpem.compensation": "补偿变化",
    "wpem.path": "同保单路径",
    latest_adjacent_quote_pair: "相邻同合同观察",
    liability_zone: "责任位置",
    micro_neutral: "微观主动流中性",
    source_clock_status: "来源时钟状态",
    consumed_at_ms: "消费时间",
    consumed_at: "消费时间",
    produced_refs: "系统生成证据引用",
    source_records: "来源记录",
    flow: "量价主动流",
    Flow: "量价主动流",
    tmv: "TMV 价格路径读数",
    risk: "冻结风险参考",
    Risk: "冻结风险参考",
    NOT_RETAINED: "未留存",
    current_contract: "当前冻结合同",
    aggregate_direction: "聚合方向",
    "semantics.raw_funding_rate": "Funding 原始费率语义",
    raw_funding_rate: "Funding 原始费率",
    last_rate: "最新费率",
    "funding.current": "Funding 当前读数",
    snapshot_id: "快照身份",
    null: "空值",
    q1: "Q1 风险质量",
    q2: "Q2 风险价格",
  };

  function displayToken(value, fallback = "依据") {
    const raw = trimSentenceEdge(value);
    if (!raw) return fallback;
    if (displayTokenLabels[raw]) return displayTokenLabels[raw];
    const labelled = label(raw, "");
    if (labelled && labelled !== "未归类资料状态（见原始记录）" && labelled !== raw) return labelled;
    if (/^[A-Za-z0-9_.-]+$/.test(raw)) return fallback;
    return raw;
  }

  function displayText(value) {
    let text = trimSentenceEdge(value);
    if (!text) return "";
    Object.entries(displayTokenLabels).forEach(([token, replacement]) => {
      const escaped = token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      text = text.replace(new RegExp(`(^|[^A-Za-z0-9_.-])${escaped}(?=$|[^A-Za-z0-9_.-])`, "g"), `$1${replacement}`);
    });
    return text;
  }

  function rememberRawFoldValue(value) {
    rawFoldSeq += 1;
    const id = `raw-${rawFoldSeq}`;
    rawFoldCache.set(id, value);
    while (rawFoldCache.size > RAW_FOLD_CACHE_LIMIT) {
      const oldest = rawFoldCache.keys().next().value;
      rawFoldCache.delete(oldest);
    }
    return id;
  }

  function rawFoldPreview(value) {
    const object = asObject(value);
    const preview = {};
    ["array_index_0based", "trace_index_1based", "snapshot_id", "point_id", "schema"].forEach((key) => {
      if (object[key] !== undefined) preview[key] = object[key];
    });
    return Object.keys(preview).length
      ? Object.entries(preview).map(([key, item]) => `"${key}": ${typeof item === "number" ? item : JSON.stringify(item)}`).join(", ")
      : "";
  }

  function rawFold(disclosureKey, value, title = "查看原始信息") {
    if (renderingOfflineExport) return "";
    const rawId = rememberRawFoldValue(value);
    const key = disclosureKey ? ` data-disclosure-key="${escapeHtml(disclosureKey)}"` : "";
    const preview = rawFoldPreview(value);
    return `<details class="ia-raw"${key} data-raw-fold-id="${escapeHtml(rawId)}"><summary>${escapeHtml(title)}</summary><p class="muted raw-placeholder">展开后生成原始 JSON；关闭状态不构建隐藏 DOM。${preview ? ` ${escapeHtml(preview)}` : ""}</p></details>`;
  }

  function remoteRawFold(disclosureKey, url, title = "查看完整原件") {
    if (renderingOfflineExport) return "";
    if (!url) return "";
    const key = disclosureKey ? ` data-disclosure-key="${escapeHtml(disclosureKey)}"` : "";
    return `<details class="ia-raw"${key} data-remote-json-url="${escapeHtml(url)}"><summary>${escapeHtml(title)}</summary><p class="muted raw-placeholder">展开后按当前身份读取完整原件；不会回落到新的候选。</p></details>`;
  }

  function evidenceGroupLabel(value) {
    const text = String(value ?? "").toLowerCase();
    if (/map|kpf|basin|region|policy|liability|anchor|gamma|wall|space/.test(text)) return "MAP";
    if (/macro|etf|dxy|yield|treasury|vix/.test(text)) return "宏观";
    if (/on.?chain|cp_|sth|lth|realized|cost/.test(text)) return "链上";
    if (/flow|tmv|funding|cvd|oi|skew|volume|price|market/.test(text)) return "量价";
    if (/option|contract|quote|fee|risk|wpem|delta|path|fragment|compensation/.test(text)) return "期权";
    return "其他";
  }

  function groupedCountSummary(items, keyFn = (item) => item) {
    const counts = new Map();
    asArray(items).forEach((item) => {
      const key = keyFn(item);
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    return Array.from(counts.entries()).map(([key, count]) => `${key} ${fmtNumber(count, 0)} 条`).join("；");
  }

  function joinTextParts(parts, separator = "；") {
    const cleaned = asArray(parts).map(trimSentenceEdge).filter(Boolean);
    return normalizeInlineText(cleaned.join(separator));
  }

  function splitReadableParts(value) {
    return normalizeInlineText(value).split(/[;；]/).map(trimSentenceEdge).filter(Boolean);
  }

  function fmtTime(value) {
    const direct = typeof value === "string" && value.trim() ? value : null;
    const numeric = safeNumber(value);
    const date = numeric === null ? (direct ? new Date(direct) : null) : new Date(numeric);
    if (!date || Number.isNaN(date.getTime())) return direct || "未提供";
    return date.toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
  }

  function fmtNumber(value, digits = 4, fallback = "未提供") {
    const numeric = safeNumber(value);
    if (numeric === null) return fallback;
    return numeric.toLocaleString("en-US", { maximumFractionDigits: digits });
  }

  function fmtBtc(value) {
    const numeric = safeNumber(value);
    return numeric === null ? "未提供" : `${fmtNumber(numeric, 8)} BTC`;
  }

  function fmtUsd(value) {
    const numeric = safeNumber(value);
    return numeric === null ? "未提供" : `${fmtNumber(numeric, 2)} USD`;
  }

  function fmtBtcWithUsd(btcValue, usdValue) {
    const btc = fmtBtc(btcValue);
    if (btc === "未提供") return "未提供";
    const usd = safeNumber(usdValue) === null ? "折合 USD 未提供" : `按本次估值现价折合 ${fmtUsd(usdValue)}`;
    return `${btc}（${usd}）`;
  }

  function fmtDelta(value) {
    const numeric = safeNumber(value);
    if (numeric === null) return "未提供";
    return numeric.toFixed(4);
  }

  function fmtMsAge(value) {
    const numeric = safeNumber(value);
    if (numeric === null) return "未提供";
    if (numeric < 1000) return `${fmtNumber(numeric, 0)} ms`;
    if (numeric < 120_000) return `${fmtNumber(numeric / 1000, 1)} 秒`;
    if (numeric < 7_200_000) return `${fmtNumber(numeric / 60_000, 1)} 分钟`;
    return `${fmtNumber(numeric / 3_600_000, 1)} 小时`;
  }

  function pathGet(object, path) {
    return String(path).split(".").reduce((current, key) =>
      current && typeof current === "object" ? current[key] : undefined, object);
  }

  function valueFrom(object, paths) {
    for (const path of paths) {
      const value = pathGet(object, path);
      if (value !== undefined && value !== null && value !== "") return value;
    }
    return undefined;
  }

  function badge(text, tone = "") {
    return `<span class="badge ${tone}">${escapeHtml(text)}</span>`;
  }

  function empty(message) {
    return `<div class="empty">${escapeHtml(message)}</div>`;
  }

  function kv(labelText, valueText, note = "") {
    return `<div class="kv">
      <span>${escapeHtml(labelText)}</span>
      <strong>${escapeHtml(valueText)}</strong>
      ${note ? `<p class="muted">${escapeHtml(note)}</p>` : ""}
    </div>`;
  }

  function metric(labelText, valueText, note = "") {
    return `<div class="metric">
      <span>${escapeHtml(labelText)}</span>
      <strong>${escapeHtml(valueText)}</strong>
      ${note ? `<p>${escapeHtml(note)}</p>` : ""}
    </div>`;
  }

  function metricClass(className, labelText, valueText, note = "") {
    return `<div class="metric ${escapeHtml(className)}">
      <span>${escapeHtml(labelText)}</span>
      <strong>${escapeHtml(valueText)}</strong>
      ${note ? `<p>${escapeHtml(note)}</p>` : ""}
    </div>`;
  }

  function previewInfo() {
    return asObject(state.workbench && state.workbench.preview);
  }

  function previewMode() {
    const preview = previewInfo();
    return String(firstPresent(preview.mode, preview.kind, preview.type, "") ?? "").toUpperCase();
  }

  function isSyntheticPreview() {
    return previewInfo().kind === "synthetic_fixture";
  }

  function isReplayPreview() {
    const mode = previewMode();
    return mode === "REPLAY" || mode === "FROZEN_REPLAY" || mode === "PUBLIC_FROZEN_REPLAY";
  }

  function previewReviewAllowed() {
    return isSyntheticPreview() && isLoopbackHost();
  }

  function manualReviewState() {
    return asObject(state.workbench && state.workbench.manual_review_state);
  }

  function reviewInflight() {
    return asObject(manualReviewState().inflight);
  }

  function manualReviewBusy() {
    return state.reviewBusy || Object.keys(reviewInflight()).length > 0;
  }

  function queryString(params) {
    return Object.entries(params)
      .filter(([, value]) => value !== undefined && value !== null && value !== "")
      .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
      .join("&");
  }

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, {
      cache: "no-store",
      credentials: "same-origin",
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.headers || {}),
      },
    });
    if (!response.ok) {
      throw new Error(`${response.status} ${response.statusText}`.trim());
    }
    try {
      return await response.json();
    } catch (_error) {
      return {};
    }
  }

  function sessionAuthenticated() {
    return asObject(state.session).authenticated === true;
  }

  function sessionExpiryText() {
    const expires = firstPresent(asObject(state.session).expires_at_ms, asObject(state.session).expires_at, "");
    return expires ? `会话有效至 ${fmtTime(expires)}` : "会话有效期由服务端管理";
  }

  async function loadSession() {
    state.sessionLoading = true;
    try {
      const data = await fetchJson(API_WORKBENCH_SESSION);
      state.session = asObject(data);
      state.sessionMessage = sessionAuthenticated() ? `私人访问会话已确认；${sessionExpiryText()}。` : "当前没有有效访问会话；读取公开投影仍按服务端策略处理，写操作需先验证。";
    } catch (error) {
      state.session = { authenticated: false, expires_at_ms: null };
      state.sessionMessage = `访问会话状态暂未读到：${error && error.message ? error.message : String(error)}`;
    } finally {
      state.sessionLoading = false;
      renderManualReviewState();
      renderLockedCandidates();
    }
  }

  async function exchangeSessionSecret(secret) {
    const value = String(secret || "").trim();
    if (!value) return false;
    state.sessionLoading = true;
    try {
      const data = await fetchJson(API_WORKBENCH_SESSION, {
        method: "POST",
        headers: { Authorization: `Bearer ${value}` },
      });
      state.session = asObject(data);
      state.sessionMessage = sessionAuthenticated() ? `访问会话已建立；${sessionExpiryText()}。` : "服务端没有建立访问会话。";
      return sessionAuthenticated();
    } catch (error) {
      state.session = { authenticated: false, expires_at_ms: null };
      state.sessionMessage = `访问验证失败：${error && error.message ? error.message : String(error)}`;
      return false;
    } finally {
      state.sessionLoading = false;
      renderManualReviewState();
      renderLockedCandidates();
    }
  }

  async function deleteSession() {
    state.sessionLoading = true;
    try {
      await fetchJson(API_WORKBENCH_SESSION, { method: "DELETE" });
      state.sessionMessage = "已退出当前访问会话。";
    } catch (error) {
      state.sessionMessage = `退出访问会话未确认：${error && error.message ? error.message : String(error)}`;
    } finally {
      state.session = { authenticated: false, expires_at_ms: null };
      state.sessionLoading = false;
      renderManualReviewState();
      renderLockedCandidates();
    }
  }

  function lockedErrorLabel(code) {
    const key = String(code || "").trim();
    const labels = {
      active_budget_exhausted: "活跃跟踪数量已达上限",
      episode_budget_exhausted: "单张保单跟踪预算已达上限",
      sample_budget_exhausted: "采样数量已达上限",
      byte_budget_exhausted: "存储字节预算已达上限",
      point_byte_budget_exhausted: "单点记录字节预算已达上限",
      request_key_budget_exhausted: "请求键预算已达上限",
      lock_snapshot_not_retained: "锁定快照未留存",
      lock_candidate_mismatch: "候选身份与锁定快照不一致",
      contract_expired: "合同已到期",
      unauthorized: "访问会话无效或权限不足",
    };
    return labels[key] || key;
  }

  async function fetchLockedJson(url, options = {}) {
    const response = await fetch(url, {
      cache: "no-store",
      credentials: "same-origin",
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.headers || {}),
      },
    });
    const readBody = async () => {
      try {
        return typeof response.json === "function" ? await response.json() : {};
      } catch (_jsonError) {
        try {
          return typeof response.text === "function" ? { message: await response.text() } : {};
        } catch (_textError) {
          return {};
        }
      }
    };
    const body = await readBody();
    if (!response.ok) {
      const code = firstPresent(body.code, body.error_code, body.error, "");
      const detail = firstPresent(body.message_cn, body.detail_cn, body.reason_cn, body.message, body.detail, body.reason, "");
      const mapped = lockedErrorLabel(code);
      const error = new Error(`${response.status} ${response.statusText || ""}${mapped ? ` · ${mapped}` : ""}${detail ? `：${detail}` : ""}`.trim());
      error.code = code;
      throw error;
    }
    return body;
  }

  function lockedEpisodeId(value) {
    const object = asObject(value);
    return String(firstPresent(object.episode_id, object.id, object.locked_episode_id, "") ?? "").trim();
  }

  function lockedRouteEpisodeId() {
    const rawHash = String(window.location.hash || "");
    if (!rawHash.startsWith("#lockedEpisode-")) return "";
    const raw = rawHash.slice("#lockedEpisode-".length);
    try {
      return decodeURIComponent(raw);
    } catch (_error) {
      return raw;
    }
  }

  function lockedEpisodeHash(episodeId) {
    const id = String(episodeId || "");
    return id ? `#lockedEpisode-${encodeURIComponent(id)}` : "#lockedCandidates";
  }

  function lockedEpisodeDomId(episodeId) {
    return `lockedEpisode-${String(episodeId || "")}`;
  }

  function lockedRouteMode() {
    const hash = String(window.location.hash || "");
    if (hash === "#lockedCandidates") return "list";
    if (lockedRouteEpisodeId()) return "detail";
    return "compact";
  }

  function lockedEpisodeSummary(episodeId) {
    return state.lockedEpisodes.find((item) => lockedEpisodeId(item) === String(episodeId || "")) || {};
  }

  function lockedCandidateSnapshotId(candidate) {
    return firstPresent(asObject(candidate).snapshot_id, asObject(asObject(candidate).snapshot).snapshot_id, "");
  }

  function lockedSummaryForCandidate(candidate) {
    const id = candidateId(candidate);
    const contract = asObject(firstPresent(asObject(candidate).contract, asObject(asObject(candidate).snapshot).contract, {}));
    const shortLeg = firstPresent(contract.short_instrument, "");
    const longLeg = firstPresent(contract.long_instrument, "");
    return state.lockedEpisodes.find((episode) => (
      firstPresent(episode.candidate_id, asObject(episode.baseline).candidate_id, asObject(episode.episode).candidate_id, "") === id
      && (!shortLeg || firstPresent(episode.short_instrument, asObject(episode.fixed_contract).short_instrument, "") === shortLeg)
      && (!longLeg || firstPresent(episode.long_instrument, asObject(episode.fixed_contract).long_instrument, "") === longLeg)
      && !["STOPPED", "EXPIRED"].includes(String(firstPresent(episode.status, asObject(episode.episode).status, "")).toUpperCase())
    ));
  }

  function selectedLockedComparison() {
    const id = state.activeLockedEpisodeId || lockedRouteEpisodeId();
    return id ? asObject(state.lockedComparisons[id]) : {};
  }

  function rememberLockedComparison(id, comparison) {
    const key = String(id || "").trim();
    if (!key) return;
    state.lockedComparisons[key] = asObject(comparison);
    state.lockedComparisonOrder = [key, ...state.lockedComparisonOrder.filter((item) => item !== key)].slice(0, LOCKED_DETAIL_CACHE_LIMIT);
    Object.keys(state.lockedComparisons).forEach((stored) => {
      if (!state.lockedComparisonOrder.includes(stored)) delete state.lockedComparisons[stored];
    });
  }

  function rememberLockedEvidence(key, evidence) {
    const id = String(key || "").trim();
    if (!id) return;
    state.lockedEvidenceCache[id] = asObject(evidence);
    state.lockedEvidenceOrder = [id, ...state.lockedEvidenceOrder.filter((item) => item !== id)].slice(0, LOCKED_DETAIL_CACHE_LIMIT);
    Object.keys(state.lockedEvidenceCache).forEach((stored) => {
      if (!state.lockedEvidenceOrder.includes(stored)) delete state.lockedEvidenceCache[stored];
    });
  }

  function showTokenDialogFor(action, message = "请输入一次性访问密钥，用于建立本设备私人会话。") {
    state.pendingTokenAction = action;
    const dialog = $("#tokenDialog");
    const input = $("#tokenInput");
    const title = $("#tokenDialogTitle");
    const note = $("#tokenDialogNote");
    const submit = $("#tokenSubmitButton");
    if (title) title.textContent = (action === "lock" || action === "stopLock") ? "私人访问会话 · 本单跟踪" : "私人访问会话 · 综合评审";
    if (note) note.textContent = message;
    if (submit) submit.textContent = action === "stopLock" ? "验证并停止跟踪" : (action === "lock" ? "验证并锁定" : "验证并评审");
    if (dialog && typeof dialog.showModal === "function") {
      if (input) input.value = "";
      dialog.showModal();
      input?.focus();
    } else {
      const supplied = window.prompt(message);
      if (supplied) {
        continueTokenAction(supplied);
      }
    }
  }

  async function continueTokenAction(secret = "") {
    if (!sessionAuthenticated()) {
      const ok = await exchangeSessionSecret(secret);
      if (!ok) return;
    }
    const action = state.pendingTokenAction;
    if (action === "stopLock") {
      stopLockedEpisode(asObject(state.pendingLockOptions).stop_episode_id);
      return;
    }
    if (action === "lock") {
      postLockCandidate();
      return;
    }
    postReview();
  }

  async function loadLockedCandidates() {
    state.lockedLoading = true;
    state.lockedError = "";
    renderLockedCandidates();
    try {
      const data = await fetchLockedJson(API_LOCKED_CANDIDATES);
      state.lockedEpisodes = asArray(data.episodes);
      if (safeNumber(data.llm_call_count) === 0) {
        state.lockedMessage = "已读取锁定列表；读取不会调用 LLM。";
      }
      const routeId = lockedRouteEpisodeId();
      if (routeId) {
        state.activeLockedEpisodeId = routeId;
        await loadLockedEpisode(routeId, { render: false, makeActive: false });
      }
    } catch (error) {
      state.lockedError = error && error.message ? error.message : String(error);
    } finally {
      state.lockedLoading = false;
      renderLockedCandidates();
    }
  }

  async function loadLockedEpisode(episodeId, { render = true, makeActive = true } = {}) {
    const id = String(episodeId || "").trim();
    if (!id) return {};
    const seq = state.lockedDetailRequestSeq + 1;
    state.lockedDetailRequestSeq = seq;
    state.lockedLoading = true;
    state.lockedError = "";
    if (makeActive) state.activeLockedEpisodeId = id;
    if (render) renderLockedCandidates();
    try {
      const data = await fetchLockedJson(`${API_LOCKED_CANDIDATES}/${encodeURIComponent(id)}`);
      rememberLockedComparison(id, data);
      if (state.lockedDetailRequestSeq === seq && (!lockedRouteEpisodeId() || lockedRouteEpisodeId() === id)) {
        state.lockedMessage = safeNumber(data.llm_call_count) === 0
          ? "已读取本单跟踪详情。"
          : "已读取本单跟踪详情；请核对服务端返回的 LLM 调用计数。";
      }
      return state.lockedComparisons[id];
    } catch (error) {
      if (state.lockedDetailRequestSeq === seq && (!lockedRouteEpisodeId() || lockedRouteEpisodeId() === id)) {
        state.lockedError = error && error.message ? error.message : String(error);
      }
      return {};
    } finally {
      if (state.lockedDetailRequestSeq === seq) state.lockedLoading = false;
      if (render && state.lockedDetailRequestSeq === seq) renderLockedCandidates();
    }
  }

  function beginLockCandidate(candidateIdValue, options = {}) {
    if (state.lockedMutationBusy || state.pendingTokenAction === "lock") return;
    if (!candidateIdValue || !secureReviewOrigin()) {
      state.lockedMessage = "锁定参考需要 HTTPS 或本机页面；不会降级为浏览器临时记录。";
      renderLockedCandidates();
      return;
    }
    const selected = sortedCandidates().find((candidate) => candidateId(candidate) === candidateIdValue);
    const snapshotId = firstPresent(asObject(options).snapshot_id, asObject(options).snapshotId, lockedCandidateSnapshotId(selected), "");
    state.pendingLockCandidateId = candidateIdValue;
    state.pendingLockOptions = {
      ...asObject(options),
      snapshot_id: snapshotId,
      idempotency_key: firstPresent(asObject(options).idempotency_key, asObject(options).idempotencyKey, makeIdempotencyKey()),
    };
    state.lockedMessage = "准备保存锁定参考；该动作不调用 LLM，也不等于成交。";
    if (!sessionAuthenticated()) {
      showTokenDialogFor("lock", "需要先建立私人访问会话，才能保存锁定参考。访问密钥只用于本次交换，不会写入页面存储。");
      return;
    }
    return postLockCandidate();
  }

  async function postLockCandidate() {
    if (state.lockedMutationBusy) return;
    const candidateIdValue = state.pendingLockCandidateId;
    const options = asObject(state.pendingLockOptions);
    if (!candidateIdValue && !options.source_episode_id && !options.sourceEpisodeId) return;
    if (!sessionAuthenticated()) {
      showTokenDialogFor("lock", "访问会话已过期，请重新验证后再保存锁定参考。");
      return;
    }
    const snapshotId = firstPresent(options.snapshot_id, options.snapshotId, "");
    state.lockedMutationBusy = true;
    state.lockedLoading = true;
    state.lockedMessage = options.new_reference || options.newReference ? "正在另存一份新观察参考；不会覆盖旧基线。" : "正在保存锁定参考；不调用 LLM。";
    renderLockedCandidates();
    try {
      const payload = {
        candidate_id: candidateIdValue,
        snapshot_id: snapshotId,
        idempotency_key: firstPresent(options.idempotency_key, options.idempotencyKey, makeIdempotencyKey()),
        new_reference: Boolean(options.new_reference || options.newReference),
      };
      const sourceEpisodeId = firstPresent(options.source_episode_id, options.sourceEpisodeId, "");
      if (sourceEpisodeId) payload.source_episode_id = sourceEpisodeId;
      const result = await fetchLockedJson(API_LOCKED_CANDIDATES, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const episode = asObject(result.episode);
      const episodeId = lockedEpisodeId(episode) || lockedEpisodeId(result);
      if (episodeId) {
        state.activeLockedEpisodeId = episodeId;
        if (!state.lockedEpisodes.some((item) => lockedEpisodeId(item) === episodeId)) {
          state.lockedEpisodes = [episode, ...state.lockedEpisodes].filter((item) => Object.keys(asObject(item)).length);
        }
        window.location.hash = lockedEpisodeHash(episodeId);
      }
      state.lockedMessage = `${result.created === false ? "已存在锁定参考，已切换查看。" : "锁定参考已保存。"}服务端返回 LLM 调用 ${fmtNumber(firstPresent(result.llm_call_count, 0), 0)} 次。`;
      await loadLockedCandidates();
      if (episodeId) await loadLockedEpisode(episodeId);
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      if (/401|403/.test(message)) state.session = { authenticated: false, expires_at_ms: null };
      state.lockedError = message;
      const unknownMessage = `锁定请求状态不明：${message}。正在重新读取列表核对；不要重复点击。`;
      state.lockedMessage = unknownMessage;
      try {
        await loadLockedCandidates();
        state.lockedError = message;
        state.lockedMessage = unknownMessage;
      } catch (_stateError) {
        /* loadLockedCandidates records its own error */
      }
    } finally {
      state.lockedMutationBusy = false;
      state.lockedLoading = false;
      state.pendingLockCandidateId = "";
      state.pendingLockOptions = {};
      if (state.pendingTokenAction === "lock") state.pendingTokenAction = "";
      renderLockedCandidates();
      renderCandidates();
    }
  }

  async function stopLockedEpisode(episodeId) {
    const id = String(episodeId || "").trim();
    if (state.lockedMutationBusy) return;
    if (!id) return;
    if (!sessionAuthenticated()) {
      state.pendingLockCandidateId = "";
      state.pendingLockOptions = { stop_episode_id: id };
      showTokenDialogFor("stopLock", "需要先建立私人访问会话，才能停止跟踪。停止跟踪不是平仓。");
      return;
    }
    state.lockedMutationBusy = true;
    state.lockedLoading = true;
    state.lockedMessage = "正在停止跟踪；这不是平仓，也不会更改原锁定基线。";
    renderLockedCandidates();
    try {
      const result = await fetchLockedJson(`${API_LOCKED_CANDIDATES}/${encodeURIComponent(id)}/stop`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ idempotency_key: makeIdempotencyKey() }),
      });
      const episode = asObject(result.episode);
      state.lockedEpisodes = state.lockedEpisodes.map((item) => lockedEpisodeId(item) === id ? { ...item, ...episode } : item);
      state.lockedMessage = `已停止跟踪；服务端返回 LLM 调用 ${fmtNumber(firstPresent(result.llm_call_count, 0), 0)} 次。停止跟踪不代表平仓或实际成交。`;
      await loadLockedEpisode(id);
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      if (/401|403/.test(message)) state.session = { authenticated: false, expires_at_ms: null };
      state.lockedError = message;
    } finally {
      state.lockedMutationBusy = false;
      state.lockedLoading = false;
      renderLockedCandidates();
    }
  }

  async function loadWorkbench() {
    state.loading = true;
    renderStatus();
    try {
      const scenario = isSyntheticPreview() ? asObject(state.workbench.preview).scenario_id : "";
      const params = queryString({ view: "reading", scenario });
      const data = await fetchJson(`${API_WORKBENCH}?${params}`);
      state.workbench = asObject(data);
      const embeddedLocked = asArray(Array.isArray(data.locked_candidates) ? data.locked_candidates : asObject(data.locked_candidates).episodes);
      if (embeddedLocked.length) state.lockedEpisodes = embeddedLocked;
      const routeId = lockedRouteEpisodeId();
      if (routeId) {
        state.activeLockedEpisodeId = routeId;
        await loadLockedEpisode(routeId, { render: false, makeActive: false });
      }
      state.apiOnline = true;
      state.lastError = "";
      state.lastLoadedAt = Date.now();
      if (Object.keys(asObject(asObject(data.manual_review_state).inflight)).length) {
        state.reviewBusy = true;
        state.reviewMessage = "正在推理；已恢复服务端运行中评审，自动等候回执。";
        startReviewPolling();
      }
    } catch (error) {
      state.apiOnline = false;
      state.lastError = error && error.message ? error.message : String(error);
    } finally {
      state.loading = false;
      renderAll();
    }
  }

  async function loadReviewStateOnce() {
    const data = await fetchJson(API_REVIEW_STATE);
    if (!state.workbench) state.workbench = {};
    const reviewState = asObject(data.manual_review_state);
    state.workbench.manual_review_state = reviewState;
    renderCandidates();
    renderManualReviewState();
    return reviewState;
  }

  function stopReviewPolling() {
    if (reviewStateTimer !== null) {
      window.clearInterval(reviewStateTimer);
      reviewStateTimer = null;
    }
    reviewPollSawInflight = false;
  }

  async function pollReviewState() {
    if (reviewPollActive) return;
    reviewPollActive = true;
    try {
      const review = await loadReviewStateOnce();
      const inflight = asObject(review.inflight);
      if (Object.keys(inflight).length) {
        reviewPollSawInflight = true;
        state.reviewBusy = true;
        state.reviewMessage = "正在推理；页面每 3 秒只读轮询服务端回执。";
        renderCandidates();
        renderManualReviewState();
        return;
      }
      const last = asObject(review.last_result);
      if (state.activeReviewKey && last.idempotency_key !== state.activeReviewKey) {
        state.reviewMessage = "正在等待本次冻结保单的服务端回执；不会补发评审请求。";
        renderManualReviewState();
        return;
      }
      if (state.reviewBusy || reviewPollSawInflight) {
        state.reviewBusy = false;
        stopReviewPolling();
        state.reviewMessage = "评审回执已返回；正在核对当前保单版本。";
        await loadWorkbench();
        state.activeReviewKey = "";
        state.reviewMessage = "回执已自动读取；该结论只适用于其冻结保单，当前候选以最新门禁为准。";
        renderManualReviewState();
      }
    } catch (error) {
      state.reviewMessage = `评审回执暂未读到：${error && error.message ? error.message : String(error)}`;
      renderManualReviewState();
    } finally {
      reviewPollActive = false;
    }
  }

  function startReviewPolling() {
    if (reviewStateTimer !== null) return;
    reviewPollSawInflight = true;
    reviewStateTimer = window.setInterval(pollReviewState, REVIEW_STATE_POLL_MS);
  }

  async function loadHistory() {
    state.historyLoading = true;
    state.historyError = "";
    renderHistory();
    try {
      const manifest = await fetchJson(HISTORY_MANIFEST);
      const cards = asArray(manifest.cards || manifest.items || manifest.documents);
      state.historyItems = cards.map((item) => ({
        id: firstPresent(item.card_id, item.id, item.identity && item.identity.card_id, ""),
        path: firstPresent(item.path, item.card_path, item.href, ""),
        time: firstPresent(item.confirmed_at, item.updated_at, item.time, ""),
        symbol: firstPresent(item.symbol, item.underlying, ""),
        quality: firstPresent(item.quality, item.status, ""),
      })).filter((item) => item.id || item.path);
      state.historyLoaded = true;
    } catch (error) {
      state.historyError = error && error.message ? error.message : String(error);
    } finally {
      state.historyLoading = false;
      renderHistory();
    }
  }

  function gateObject() {
    return asObject(state.workbench && state.workbench.space_gate);
  }

  function gateStateText(gate) {
    const status = firstPresent(gate.state, gate.status, "STRUCTURE_BLOCKED");
    return label(status);
  }

  function gateTone(gate) {
    const status = String(firstPresent(gate.state, gate.status, "")).toUpperCase();
    if (status === "STRUCTURE_ELIGIBLE") return "good";
    if (status === "STRUCTURE_BLOCKED") return "bad";
    return "warn";
  }

  function gateSource(gate) {
    return firstPresent(gate.provenance, gate.source, gate.space_gate_provenance, gate.source_kind, "missing_or_invalid");
  }

  function gateReasons(gate) {
    return [
      ...asArray(gate.reason_codes),
      ...asArray(gate.reasons),
      ...asArray(gate.blocking_reasons),
      ...asArray(gate.gap_reasons),
    ].filter(Boolean);
  }

  function renderPreviewBanner() {
    const target = $("#previewBanner");
    if (!target) return;
    const preview = previewInfo();
    if (!preview.kind) {
      target.className = "";
      target.innerHTML = "";
      return;
    }
    const loopback = isLoopbackHost();
    const replay = isReplayPreview();
    const shortIdentity = replay
      ? "冻结回放 · 非当前交易 · 查看不调用 LLM"
      : (loopback ? "本地模拟 · 查看不调用 LLM · 执行关闭" : "模拟标记 · 非本机入口 · 演示关闭");
    const message = replay
      ? "本机冻结回放来自真实公开来源的历史截面；它只保留原始身份，不授予当前 FMZ 事实、生产 CURRENT 评审资格或交易许可；查看与切换不会调用 LLM。"
      : (loopback
        ? "本机模拟服务只演示工作台资料层级与手动回执流程；不可作为当前 FMZ 事实、账户允许或可交易判断；评审来源以回执标识为准，查看不会调用 LLM。"
        : "页面不在本机 loopback，手动评审演示入口关闭；不要把模拟标记当作可运行环境。");
    target.className = "preview-banner";
    target.innerHTML = `<strong>${escapeHtml(firstPresent(preview.label, shortIdentity))}</strong>
      <span>${escapeHtml(shortIdentity)}</span>
      <details><summary>查看完整边界</summary><p>${escapeHtml(message)}</p></details>`;
  }

  function marketContext() {
    return asObject(state.workbench && state.workbench.market_context);
  }

  function marketFacts() {
    return asObject(marketContext().market_facts);
  }

  function factValue(fact, names, formatter = (value) => String(value)) {
    const value = firstPresent(...names.map((name) => String(name).includes(".") ? pathGet(fact, name) : fact[name]));
    if (value === undefined) return "";
    if (Array.isArray(value)) return value.length ? value.map((item) => label(item)).join("；") : "";
    if (value && typeof value === "object") return "";
    return formatter(value);
  }

  function factLine(fact, title, names, formatter) {
    const value = factValue(fact, names, formatter);
    return value ? `<li><span>${escapeHtml(title)}</span><strong>${escapeHtml(value)}</strong></li>` : "";
  }

  function factLineOrGap(fact, title, names, formatter) {
    const value = factValue(fact, names, formatter);
    return `<li><span>${escapeHtml(title)}</span><strong>${escapeHtml(value || "未提供")}</strong></li>`;
  }

  function factDetailLine(title, value, note = "") {
    return `<li><span>${escapeHtml(title)}</span><strong>${escapeHtml(value)}</strong>${note ? `<small>${escapeHtml(note)}</small>` : ""}</li>`;
  }

  function factFor(keys) {
    for (const key of asArray(keys).length ? keys : [keys]) {
      const fact = asObject(String(key).includes(".") ? pathGet(marketFacts(), key) : marketFacts()[key]);
      if (Object.keys(fact).length) return fact;
    }
    return {};
  }

  function layerFallbackReading(layerKey) {
    const rows = {
      volume_price: {
        source: isSyntheticPreview()
          ? "本地合成量价窗口、TMV、主动净量与价格变化；用于检查页面表达，不是当前 FMZ 事实；EDB 若引用这些字段，不构成独立确认。"
          : "FMZ 量价窗口、TMV、主动净量与价格变化；EDB 若引用这些字段，不构成独立确认。",
        interpretation: "只说明当前量价倾向和窗口冲突，不把上涨/下跌标签直接转成卖 Put 或卖 Call 许可。",
        contract: "用于提醒哪一侧卖出腿更可能承压；不改写净信用、M_ref、冻结风险或交易权限。",
      },
      macro_pressure: {
        source: "美元、美债十年名义/实际利率与 VXN/VOLQ/VIX 类波动率分量；分量时钟独立。",
        interpretation: "宏观只作背景压力和反证，不从综合分反推未展示的市场变化。",
        contract: "若宏观冲击阻断，候选应先降级审查；未阻断也不代表可以建仓。",
      },
      price_path: {
        source: "源端价格路径、主动净量与 Binance BTCUSDT U本位永续已结算资金费率。",
        interpretation: "Funding 是永续合约背景，不是期权费用；持仓量和资金费率不能单独判定方向。",
        contract: "用于核查卖出腿附近是否正在被价格路径逼近；不计入垂直价差净信用。",
      },
      edb: {
        source: "EDB 汇总同源量价、空间、宏观和反证线索；同源意见不是独立确认。",
        interpretation: "偏多、偏空或 Call/Put 侧背景只是来源意见；side_hint=CALL 本身不能单独推出偏多。若另有上行事实，对卖 Call 反而是不利。",
        contract: "只给人工复核看点，不生成概率、EV 排名、选边结论或交易许可。",
      },
      space: {
        source: "空间锚、墙位、Gamma 体制和 GEX 净 Gamma 来自同一空间证据链。",
        interpretation: "空间门通过只表示可继续核算候选，不代表补偿充分或经济有效。",
        contract: "用于判断保单合同线是否落在可审查空间；不替代具体保单账。",
      },
    };
    return rows[layerKey] || {
      source: "服务端事实字段。",
      interpretation: "仅按源端字段解释，不由前端补造结论。",
      contract: "不生成交易许可。",
    };
  }

  function fieldText(fact, names) {
    const value = firstPresent(...names.map((name) => String(name).includes(".") ? pathGet(fact, name) : fact[name]));
    if (Array.isArray(value)) return joinTextParts(value.map(fmtMapValue));
    if (value && typeof value === "object") return "";
    return value ? normalizeInlineText(value) : "";
  }

  function scoreOnlyGap(fact, layerKey) {
    const hasScore = ["score", "macro_score", "edb_score", "confidence", "quality_score"].some((key) => safeNumber(fact[key]) !== null);
    if (!hasScore) return "";
    const evidenceFields = [
      fact.component_summaries,
      fact.source_values,
      fact.evidence_rows,
      fact.supporting_facts,
      fact.counter_evidence_cn,
      fact.conflicts_cn,
      fact.drivers_cn,
      fact.reason_codes,
    ];
    const hasEvidence = evidenceFields.some((value) => Array.isArray(value) ? value.length : Object.keys(asObject(value)).length);
    if (hasEvidence) return "";
    return joinTextParts([layerFallbackReading(layerKey).source, "当前只返回分数或标签，缺少可验分量，按缺口展示。"]);
  }

  function renderFactReading(fact, layerKey) {
    const fallback = layerFallbackReading(layerKey);
    const source = firstPresent(
      fieldText(fact, ["source_summary_cn", "source_cn", "data_source_cn", "source_basis_cn"]),
      fallback.source,
    );
    const explanation = firstPresent(
      fieldText(fact, ["explanation_cn", "interpretation_cn", "summary_cn", "summary_label_cn"]),
      fallback.interpretation,
    );
    const counter = firstPresent(
        fieldText(fact, ["counter_evidence_cn", "counter_cn", "conflicts_cn", "gap_cn", "gaps_cn"]),
        scoreOnlyGap(fact, layerKey),
        "未返回独立反证明细；不能把本层读成独立确认。",
    );
    const contract = firstPresent(
      fieldText(fact, ["contract_impact_cn", "policy_impact_cn", "candidate_guidance_cn", "reading_guidance_cn"]),
      fallback.contract,
    );
    return `<div class="fact-reading-grid" aria-label="事实阅读分层">
      <div><span>源事实</span><p>${escapeHtml(source)}</p></div>
      <div><span>解释 / 反证</span><p>${escapeHtml(joinTextParts([explanation, counter]))}</p></div>
      <div><span>合同影响</span><p>${escapeHtml(contract)}</p></div>
    </div>`;
  }

  function renderFactCard(key, title, purpose, lines, variant = "", layerKey = "") {
    const fact = factFor(key);
    const className = `fact-card${variant ? ` ${variant}` : ""}`;
    if (!Object.keys(fact).length) {
      return `<article class="${className} is-gap">
        <div class="fact-card-head"><h3>${escapeHtml(title)}</h3><p>${escapeHtml(purpose)}</p></div>
        <div class="fact-card-body"><div class="callout warn"><strong>资料缺口</strong><p>服务端未提供本层事实，工作台不使用历史卡或前端推断补齐。</p></div></div>
      </article>`;
    }
    const body = lines(fact).filter(Boolean).join("");
    return `<article class="${className}">
      <div class="fact-card-head"><h3>${escapeHtml(title)}</h3><p>${escapeHtml(purpose)}</p></div>
      <div class="fact-card-body"><ul class="fact-lines">${body || `<li><span>状态</span><strong>本层有资料，但无可展示字段</strong></li>`}</ul>${renderFactReading(fact, layerKey)}</div>
    </article>`;
  }

  function renderSpaceEvidence() {
    return renderFactCard(["space_context", "anchor"], "空间层 · 位置证据", "先看锚位、墙位与 Gamma 体制；这些是同一空间证据链，不重复计票。", (fact) => [
      factLine(fact, "Flip / 锚位", ["anchor.effective_flip_point", "effective_flip_point"], fmtUsd),
      factLine(fact, "最近支撑墙", ["gex_info.support_walls.0"], fmtUsd),
      factLine(fact, "最近压力墙", ["gex_info.resistance_walls.0"], fmtUsd),
      factLine(fact, "磁吸位", ["gex_info.magnet_price"], fmtUsd),
      factLine(fact, "钉扎位", ["gamma_regime.pin_strike"], fmtUsd),
      factLine(fact, "Gamma 体制", ["gamma_regime.regime"], (value) => label(value)),
      factLine(fact, "距 Flip", ["gamma_regime.distance_to_flip_pct"], (value) => `${fmtNumber(value, 2)}%`),
      factLineOrGap(fact, "GEX 净 Gamma（USD）", ["gex_info.total_net_gex"], fmtUsd),
      factLine(fact, "GEX 时点", ["gex_info.fetched_at_ms"], fmtTime),
    ], "space-facts", "space");
  }

  function renderConflictSummary() {
    const conflicts = asArray(marketFacts().conflicts);
    if (!conflicts.length) return "";
    const rows = conflicts.map((item) => {
      const object = asObject(item);
      return `${label(firstPresent(object.layer, "来源"))}：${label(firstPresent(object.code, object.reason, "未提供"))}`;
    }).join("；");
    return `<div class="callout warn" style="margin-top: 12px;">
      <strong>冲突摘要</strong>
      <p>${escapeHtml(rows)}</p>
    </div>`;
  }

  function fmtSigned(value, digits = 2) {
    const number = safeNumber(value);
    return number === null ? "未提供" : `${number > 0 ? "+" : ""}${fmtNumber(number, digits)}`;
  }

  function renderMacroComponents(fact) {
    const items = asArray(fact.component_summaries);
    if (!items.length) return factDetailLine("美元 / 美债 / 波动率分量", "未提供", "未提供分量时，不由综合评分反推变化。");
    return items.map((item) => {
      const component = asObject(item);
      const key = String(firstPresent(component.key, component.component, ""));
      const symbol = String(firstPresent(component.source_symbol, "来源未提供"));
      const title = key === "DXY" ? "美元指数 DXY" : (key === "US10Y" ? "美债十年期收益率" : `波动率 ${symbol.replace(/^\^/, "")}`);
      const basis = component.scoring_unit === "bps" ? "收益率变化" : (component.scoring_unit === "pct" ? "相对变化" : "变化口径未提供");
      const bps = safeNumber(component.scoring_bps) === null ? "未提供 bp" : `${fmtSigned(component.scoring_bps)} ${basis === "相对变化" ? "相对 bp" : "bp"}`;
      const pct = component.scoring_unit === "pct" && safeNumber(component.change_pct_3d) !== null ? `（${fmtSigned(component.change_pct_3d)}%）` : "";
      const levels = `源原始读数 ${fmtNumber(component.reference_close, 3)} → ${fmtNumber(component.current_close, 3)}`;
      const note = `${symbol} · ${label(firstPresent(component.source_status, "来源状态未提供"))}；${levels}；参考 ${fmtTime(component.reference_ts_ms)} → 观测 ${fmtTime(component.current_ts_ms)}。日线近三日参考，不是实时三日滚动盘口。`;
      return factDetailLine(title, `${basis} ${bps}${pct}`, note);
    }).join("");
  }

  function renderPricePath(fact, key, title) {
    const window = asObject(pathGet(fact, key));
    const core = asObject(window.core);
    const direction = core.trend_direction === 1 ? "上行" : (core.trend_direction === -1 ? "下行" : (core.trend_direction === 0 ? "中性" : "方向未提供"));
    return factDetailLine(title, `${direction} · 趋势强度 ${fmtNumber(core.trend_strength_pct, 2)}%`,
      `源端趋势量，不等同于端点涨跌；窗口价 ${fmtUsd(core.price)}；K线开盘 ${fmtTime(core.kline_open_time)}；资料就绪 ${label(core.data_ready)}`);
  }

  function renderMicroPath(fact, key, title) {
    const micro = asObject(pathGet(fact, key));
    return factDetailLine(title, `${fmtSigned(micro.momentum_return_pct)}%`,
      `实际覆盖 ${fmtNumber(micro.coverage_hours, 2)} 小时；主动净量 ${fmtNumber(micro.cvd_sum, 2)} ${firstPresent(micro.cvd_unit, "单位未提供")}；资料就绪 ${label(micro.data_ready)}`);
  }

  function fmtFundingRate(value) {
    const number = safeNumber(value);
    return number === null ? "未提供" : `${fmtSigned(number * 100, 4)}%（${fmtSigned(number * 10000, 2)} bp）`;
  }

  function renderFundingWindow(fact, key, title) {
    const funding = asObject(pathGet(fact, key));
    return factDetailLine(title, fmtFundingRate(funding.funding_cum),
      `${fmtNumber(funding.funding_count, 0)} 次记录；${fmtTime(funding.window_start_time)} → ${fmtTime(funding.window_end_time)}；末条年龄 ${fmtMsAge(funding.age_ms)}；资料就绪 ${label(funding.data_ready)}`);
  }

  function renderMarketContext() {
    const target = $("#marketContext");
    if (!target) return;
    const context = marketContext();
    if (!Object.keys(context).length) {
      target.innerHTML = empty("工作台服务尚未返回 FMZ 事实骨架；页面不会用旧审计卡补足当前市场层。");
      return;
    }
    const hasProjectionGap = (value) => {
      if (!value || typeof value !== "object") return false;
      if (asArray(value._projection_gaps).length) return true;
      return Object.values(value).some(hasProjectionGap);
    };
    const gaps = [
      ...asArray(context.gap_reasons),
      ...asArray(context.space_gate_gap_reasons),
      ...(hasProjectionGap(context) ? ["部分源端数值无效，已按缺口展示；原始证据保留"] : []),
    ].filter(Boolean).map((item) => label(item)).join("；");
    const preview = isSyntheticPreview() || context.status === "synthetic_fixture";
    const top = `<div class="metric-grid">
      ${metric("事实身份", preview ? "本地模拟场景（非当前事实）" : label(firstPresent(context.status, "unknown")), preview ? "查看不调用 LLM、非交易；只演示资料层级" : "来自 FMZ fact_context")}
      ${metric("来源时点", fmtTime(firstPresent(context.snapshot_ts_ms, context.valuation_ts_ms)), `资料年龄 ${fmtMsAge(context.age_ms)}`)}
      ${metric("BTC 现价", fmtUsd(context.current_price_usd), preview ? "本地模拟价格" : label(firstPresent(context.current_price_source, "来源未提供")))}
      ${metric("冲突与缺口", preview ? "模拟资料与生产隔离" : (gaps || "未记录全局缺口"), "缺口只阻断或提示，不在前端补造事实")}
    </div>`;
    const cards = [
      renderFactCard(["volume_price", "flow"], "量价倾向", "只读展示 TMV/主动流方向，不把它转成选边票。", (fact) => [
        factLine(fact, "方向", ["direction", "market_state"], (value) => label(value)),
        factLine(fact, "TMV", ["tmv_blend"], (value) => fmtNumber(value, 4)),
        factLine(fact, "主动流", ["micro_flow_effect"], (value) => label(value)),
        factLine(fact, "24h TMV", ["tmvf_24h.tmv_final"], (value) => fmtNumber(value, 4)),
        factLine(fact, "48h TMV", ["tmvf_48h.tmv_final"], (value) => fmtNumber(value, 4)),
        factLine(fact, "窗口冲突", ["window_conflict"], (value) => label(value)),
        factLine(fact, "4h CVD 标准化量", ["micro_flow.fast_4h.cvd_norm"], (value) => fmtNumber(value, 4)),
        factLine(fact, "12h CVD 标准化量", ["micro_flow.slow_12h.cvd_norm"], (value) => fmtNumber(value, 4)),
      ], "", "volume_price"),
      renderFactCard("macro_pressure", "宏观层", "宏观压力只作为环境反证或背景，不改写保单数值。", (fact) => [
        factLine(fact, "摘要", ["summary_label_cn", "interpretation_cn"]),
        factValue(fact, ["interpretation_cn", "macro_components_cn"]) === factValue(fact, ["summary_label_cn", "interpretation_cn"])
          ? "" : factLine(fact, "推理说明", ["interpretation_cn", "macro_components_cn"]),
        factLine(fact, "状态", ["macro_regime", "regime"], (value) => label(value)),
        factLine(fact, "评分", ["macro_score", "score"], (value) => fmtNumber(value, 4)),
        renderMacroComponents(fact),
        factLine(fact, "数据状态", ["data_status"], (value) => label(value)),
        factLine(fact, "资料时点", ["last_data_time"], fmtTime),
        factLine(fact, "冲击阻断", ["macro_shock.block"], (value) => label(value)),
        factLine(fact, "原因", ["reason_codes"]),
      ], "", "macro_pressure"),
      renderFactCard(["volume_price", "flow"], "价格路径与资金费率", "价格与主动流来自源端窗口；资金费率是永续合约背景，不计入期权净信用。", (fact) => [
        renderMicroPath(fact, "micro_flow.fast_4h", "4h 价格变化"),
        renderMicroPath(fact, "micro_flow.slow_12h", "12h 价格变化"),
        renderPricePath(fact, "tmvf_24h", "24h 价格路径"),
        renderPricePath(fact, "tmvf_48h", "48h 价格路径"),
        factLineOrGap(fact, "最近资金费率", ["funding.last_rate"], fmtFundingRate),
        factLine(fact, "资金费影响", ["funding.effect"], (value) => label(value)),
        renderFundingWindow(fact, "funding.tmvf_24h", "24h 累计资金费率"),
        renderFundingWindow(fact, "funding.tmvf_48h", "48h 累计资金费率"),
      ], "is-wide", "price_path"),
      renderFactCard(["edb_reasoning", "edb"], "因果链与反证", "EDB 只汇总同源证据，质量分未校准为胜率；侧别提示不是许可。", (fact) => [
        factLine(fact, "因果摘要", ["summary_cn"]),
        factLineOrGap(fact, "价格倾向", ["direction", "lean"], (value) => label(value)),
        factLine(fact, "侧别提示", ["side_hint"], (value) => label(value)),
        factLine(fact, "支持标签", ["support_label"], (value) => label(value)),
        factLine(fact, "EDB 分", ["edb_score"], (value) => fmtNumber(value, 4)),
        factLine(fact, "证据质量分", ["confidence"], (value) => `${fmtNumber(value, 0)} / 100（非胜率）`),
        factLine(fact, "校准", ["calibration_state"], (value) => label(value)),
        factLine(fact, "冲突", ["conflict_level"], (value) => label(value)),
        factLine(asObject(marketFacts().skew), "Skew RR", ["rr_blend"], (value) => fmtNumber(value, 4)),
        factLine(asObject(marketFacts().neutral_repair), "NR 背景", ["state"], (value) => label(value)),
      ], "is-wide", "edb"),
    ].join("");
    target.innerHTML = `${top}
      ${renderConflictSummary()}
      <div class="callout ${preview ? "warn" : ""}" style="margin-top: 12px;">
        <strong>${escapeHtml(preview ? "本地模拟场景" : "非 LLM 事实骨架")}</strong>
        <p>${escapeHtml(preview ? "本栏用于展示正常数据层级和评审流程；它不是 FMZ 当前事实、真 LLM 回执或交易许可。" : "本栏只汇总开仓前已知市场事实、冲突与缺口；Put/Call 仍并列，不在页面选边，也不重复计票。")}</p>
      </div>
      <div class="fact-grid">${cards}</div>`;
  }

  function globalBtcMap() {
    const view = asObject(state.workbench && state.workbench.view);
    return asObject(firstPresent(state.workbench && state.workbench.btc_map, view.btc_map, {}));
  }

  function candidateBtcMap(candidate) {
    const snapshot = asObject(candidate.snapshot);
    return asObject(firstPresent(candidate.btc_map, snapshot.btc_map, asObject(snapshot.data_identity).btc_map, {}));
  }

  function mapCandidateKey(candidate, index) {
    const side = String(firstPresent(candidate.side, asObject(candidate.snapshot).side, "")).toLowerCase();
    return side === "put" || side === "call" ? side : `candidate-${index}`;
  }

  function mapCandidateOptions() {
    return sortedCandidates().map((candidate, index) => {
      const map = candidateBtcMap(candidate);
      return {
        key: mapCandidateKey(candidate, index),
        candidate,
        side: label(firstPresent(candidate.side, asObject(candidate.snapshot).side, "候选")),
        hasMap: Object.keys(map).length > 0,
      };
    });
  }

  function selectedMapContext() {
    const options = mapCandidateOptions();
    const selected = options.find((item) => item.key === state.selectedMapCandidateKey);
    const globalMap = globalBtcMap();
    if (!selected) return { map: globalMap, candidate: null, label: "未选保单", source: "global", options };
    const map = candidateBtcMap(selected.candidate);
    return {
      map: Object.keys(map).length ? map : globalMap,
      candidate: selected.candidate,
      label: `${selected.side} 保单`,
      source: Object.keys(map).length ? "candidate" : "global_fallback",
      options,
    };
  }

  function mapSelectionContextKey(context) {
    if (!context || !context.candidate) return "";
    const map = asObject(context.map);
    const policy = asObject(map.policy_link);
    const presentation = mapPresentation(map);
    const identity = asObject(presentation.candidate_identity);
    const snapshot = asObject(context.candidate.snapshot);
    return [
      context.source,
      state.selectedMapCandidateKey,
      candidateId(context.candidate),
      firstPresent(snapshot.snapshot_id, snapshot.version, ""),
      firstPresent(policy.candidate_id, identity.candidate_id, ""),
      firstPresent(policy.candidate_version, identity.candidate_version, ""),
      firstPresent(map.map_id, presentation.map_id, ""),
      firstPresent(mapCutoff(map), identity.reference_time_ms, ""),
    ].map((item) => String(item ?? "")).join("|");
  }

  function selectCandidate(id) {
    const selected = mapCandidateOptions().find((item) => candidateId(item.candidate) === id);
    if (!selected) return false;
    if (state.selectedMapCandidateKey !== selected.key) {
      state.selectedMapCandidateKey = selected.key;
      state.selectedMapRegionId = "";
      state.selectedMapRegionContextKey = "";
    }
    renderBtcMap();
    renderManualReviewState();
    return true;
  }

  function mapExactIdentityKey(map) {
    const object = asObject(map);
    const presentation = mapPresentation(object);
    const identity = asObject(presentation.candidate_identity);
    const mapId = firstPresent(object.map_id, presentation.map_id, identity.map_id);
    if (!mapId) return "";
    const cutoff = mapCutoff(object);
    return ["map", mapId, cutoff || ""].map((item) => String(item ?? "")).join("|");
  }

  function mapDisclosureKey(context, section) {
    const mapIdentity = mapExactIdentityKey(context && context.map);
    if (!mapIdentity || !section) return "";
    if (!context.candidate) return ["global", context.source, mapIdentity, section].map((item) => String(item ?? "")).join("|");
    const snapshot = asObject(context.candidate.snapshot);
    const id = candidateId(context.candidate);
    const snapshotId = firstPresent(snapshot.snapshot_id, "");
    if (!id || !snapshotId) return "";
    return [
      "candidate",
      context.source,
      state.selectedMapCandidateKey,
      id,
      snapshotId,
      mapIdentity,
      section,
    ].map((item) => String(item ?? "")).join("|");
  }

  function mapDisclosureAttribute(context, section) {
    const key = mapDisclosureKey(context, section);
    return key ? ` data-map-disclosure-key="${escapeHtml(key)}"` : "";
  }

  function mapLabelForKey(key) {
    const labels = {
      allocation: "配置",
      inventory: "库存与兑现",
      financing: "杠杆与资金成本",
      external_conditions: "外部资本条件",
      etf: "ETF",
      f3_usd_m: "近3个交易日净流",
      f7_usd_m: "近7个交易日净流",
      q_abs_usd_m_prior60: "前置60日平均绝对净流",
      u3: "3日强度 U3",
      u7: "7日强度 U7",
      latest_date: "最新共同观察日",
      common_dates: "共同观察端点",
      cp: "资本化价格",
      cp_sth_usd: "短期持有者资本化价格",
      cp_lth_usd: "长期持有者资本化价格",
      cp_all_usd: "全链资本化价格",
      source_phase: "源资本相位",
      source_price_usd: "源价",
      latest_observation_date: "最新完整观察日",
      gap_sth_now_pct: "同源价格距短期资本化价格",
      gap_sth_source_price_pct: "同源价格距短期资本化价格",
      profit_loss: "兑现",
      profit_14d_usd: "14日已实现利润",
      loss_14d_usd: "14日已实现亏损",
      loss_share_14d: "14日亏损占比",
      realized_cap_start_usd: "窗口起点 Realized Cap",
      window_days: "窗口日期",
      mode: "数据模式",
      oi: "币安 BTCUSDT U本位持仓量（OI）",
      "币安 BTCUSDT USD M 原生OI变化": "币安 BTCUSDT U本位持仓量变化（OI）",
      "币安 BTCUSDT USD M 原生OI变化率": "币安 BTCUSDT U本位持仓量变化率（OI）",
      "币安 BTCUSDT USD-M 原生OI变化": "币安 BTCUSDT U本位持仓量变化（OI）",
      "币安 BTCUSDT USD-M 已结算Funding": "币安 BTCUSDT U本位已结算资金费率",
      "币安 BTCUSDT USD-M 前段Funding": "币安 BTCUSDT U本位前一窗口资金费率",
      open_interest_native_btc: "币安 BTCUSDT U本位原生持仓量（OI）",
      native_change_btc: "币安 BTCUSDT U本位持仓量变化（OI）",
      native_change_pct: "币安 BTCUSDT U本位持仓量变化率（OI）",
      notional_change_usd: "按价格折算的美元名义持仓量变化",
      notional_change_pct: "按价格折算的美元名义持仓量变化率",
      native_oi_unchanged: "原生持仓量是否不变",
      notional_is_price_dependent: "名义值是否受价格影响",
      funding: "币安 BTCUSDT U本位已结算资金费率",
      "已结算Funding": "已结算资金费率",
      "实际Funding": "已结算资金费率",
      settled_count: "已结算资金费率条数",
      settled_funding_rate_sum: "已结算资金费率合计",
      predicted_count_excluded: "剔除预测资金费率条数",
      prediction_mixed_with_settlement: "是否混入预测费率",
      usdt_borrow: "USDT 借款",
      hourly_rate_decimal: "小时利率",
      simple_apr_pct: "简单 APR",
      dxy: "美元",
      dollar: "广义美元 DTWEXBGS",
      dollar_source_values: "广义美元源值",
      latest: "最新值",
      change_5obs: "5个共同观察变化",
      change_20obs: "20个共同观察变化",
      change_5obs_bp: "5个共同观察变化",
      change_20obs_bp: "20个共同观察变化",
      us10y: "美债十年",
      nominal_10y: "美国十年名义利率",
      real_10y: "美国十年实际利率",
      dfii10: "实际利率",
      vxn: "波动率",
      volq: "波动率",
      vix: "波动率",
      kpf: "历史接受区",
      KPF: "KPF 历史接受区",
      gex: "GEX",
      anchor: "Anchor",
      flip: "Flip",
      CP_STH: "短期持有者资本化价格",
      CP_LTH: "长期持有者资本化价格",
      CP_ALL: "全链资本化价格",
      pin: "Pin",
      contract: "合同",
      breakeven: "近端 BE",
      short_leg: "卖出腿",
      protection_leg: "保护腿",
      missing: "缺口",
    };
    const raw = String(key ?? "").trim();
    if (!raw) return "资料";
    const lower = raw.toLowerCase();
    if (labels[raw] || labels[lower]) return labels[raw] || labels[lower];
    return raw
      .replace(/[_-]+/g, " ")
      .replace(/\b\w/g, (char) => char.toUpperCase())
      .slice(0, 40);
  }

  function fmtMapValue(value) {
    if (Array.isArray(value)) return value.map(fmtMapValue).filter(Boolean).join(" / ");
    if (value && typeof value === "object") {
      const object = asObject(value);
      const direct = firstPresent(object.display_value, object.text_cn, object.summary_cn, object.value_cn);
      if (direct !== undefined) return String(direct);
      if (object.value !== undefined || object.unit !== undefined || object.unit_cn !== undefined) {
        const numeric = safeNumber(object.value);
        const unit = String(firstPresent(object.unit_cn, object.unit) ?? "").trim();
        const windowText = firstPresent(object.window_cn, object.window) ?? "";
        let rendered = "";
        if (numeric === null) {
          rendered = String(object.value ?? "");
        } else if (unit === "rate_decimal") {
          rendered = fmtRateDecimal(numeric);
        } else if (unit === "US$m/日") {
          rendered = `${fmtNumber(numeric, 2)} 百万美元/日`;
        } else if (unit === "US$m") {
          rendered = `${fmtNumber(numeric, 2)} 百万美元`;
        } else if (unit === "%") {
          rendered = `${fmtSigned(numeric, 2)}%`;
        } else if (unit === "bp") {
          rendered = `${fmtSigned(numeric, 2)} bp`;
        } else if (unit === "USD") {
          rendered = fmtUsd(numeric);
        } else if (unit === "BTC") {
          rendered = fmtBtc(numeric);
        } else if (unit === "倍") {
          rendered = `${fmtNumber(numeric, 3)} 倍`;
        } else if (unit) {
          rendered = `${fmtNumber(numeric, 4)} ${unit}`;
        } else {
          rendered = fmtNumber(numeric, 4);
        }
        return `${rendered}${windowText ? `（${windowText}）` : ""}`;
      }
      if (safeNumber(object.min_btc) !== null || safeNumber(object.max_btc) !== null) {
        const low = safeNumber(object.min_btc);
        const high = safeNumber(object.max_btc);
        if (low !== null && high !== null && Math.abs(low - high) < 1e-12) return fmtBtc(low);
        return `${fmtBtc(object.min_btc)} — ${fmtBtc(object.max_btc)}`;
      }
      if (safeNumber(object.min_usd) !== null || safeNumber(object.max_usd) !== null) {
        const low = safeNumber(object.min_usd);
        const high = safeNumber(object.max_usd);
        if (low !== null && high !== null && Math.abs(low - high) < 1e-9) return fmtUsd(low);
        return `${fmtUsd(object.min_usd)} — ${fmtUsd(object.max_usd)}`;
      }
      if (safeNumber(object.low) !== null || safeNumber(object.high) !== null) {
        const low = safeNumber(object.low);
        const high = safeNumber(object.high);
        if (low !== null && high !== null && Math.abs(low - high) < 1e-9) return fmtUsd(low);
        return `${fmtUsd(object.low)} — ${fmtUsd(object.high)}`;
      }
      const number = firstPresent(object.value, object.amount, object.price_usd, object.bps, object.pct);
      if (safeNumber(number) !== null) {
        const unit = firstPresent(object.unit, object.unit_cn, "");
        return `${fmtNumber(number, String(unit).toLowerCase().includes("btc") ? 8 : 4)}${unit ? ` ${unit}` : ""}`;
      }
      return "";
    }
    if (safeNumber(value) !== null) return fmtNumber(value, 4);
    return String(value ?? "");
  }

  function fmtPct(value, digits = 2) {
    const number = safeNumber(value);
    return number === null ? "" : `${fmtSigned(number, digits)}%`;
  }

  function fmtRateDecimal(value, digits = 4) {
    const number = safeNumber(value);
    return number === null ? "" : `${fmtSigned(number * 100, digits)}%（${fmtSigned(number * 10000, 2)} bp）`;
  }

  function fmtMacroChange(container, key) {
    const value = safeNumber(container[key]);
    if (value === null) return "";
    const bp = safeNumber(container[`${key}_bp`]);
    if (bp !== null) return `${fmtSigned(bp, 2)} bp`;
    const pct = safeNumber(container[`${key}_pct`]);
    if (pct !== null) return `${fmtSigned(pct, 3)}%`;
    return `${fmtSigned(value, 3)} 指数点`;
  }

  function metricScalarValue(key, value, parentKey = "") {
    if (value === null || value === undefined || value === "") return "";
    if (Array.isArray(value)) {
      if (!value.length) return "";
      if (key === "common_dates" || key === "window_days" || key === "settled_times_ms") {
        return value.slice(-3).map((item) => String(key === "settled_times_ms" ? fmtTime(item) : item)).join(" / ");
      }
      return value.slice(0, 4).map(fmtMapValue).filter(Boolean).join(" / ");
    }
    if (typeof value === "boolean") return value ? "是" : "否";
    if (typeof value === "string") return label(value);
    if (safeNumber(value) === null) return fmtMapValue(value);
    if (/_usd_m$/.test(key)) return `${fmtSigned(value, 2)} 百万美元`;
    if (/_usd$/.test(key)) return fmtUsd(value);
    if (/_btc$/.test(key)) return fmtBtc(value);
    if (/_pct$/.test(key)) return fmtPct(value, 2);
    if (key === "loss_share_14d") return fmtPct(Number(value) * 100, 2);
    if (key === "settled_funding_rate_sum" || key === "hourly_rate_decimal") return fmtRateDecimal(value);
    if (key === "simple_apr_pct") return `${fmtSigned(value, 3)}%`;
    if (key === "u3" || key === "u7") return `${fmtNumber(value, 3)} 倍常态日均`;
    if (/_bp$/.test(key)) return `${fmtSigned(value, 2)} bp`;
    if ((parentKey === "nominal_10y" || parentKey === "real_10y") && key === "latest") return `${fmtNumber(value, 3)}%`;
    if (parentKey === "dollar" && key === "latest") return `${fmtNumber(value, 3)} 指数点`;
    return fmtNumber(value, 4);
  }

  function mapMetricRows(value, prefix = "") {
    const skip = new Set([
      "schema", "status", "source_ref", "request_audit", "source_record_ids",
      "payload_hash", "rows", "observations", "settled_records", "funds",
      "skipped_rows", "source_values", "cost_basis_source_values",
      "realized_pnl_source_values", "oi_source_values", "funding_source_values",
      "borrow_source_values", "dollar_source_values", "nominal_10y_source_values",
      "real_10y_source_values", "formula", "price_basis",
    ]);
    const object = asObject(value);
    const rows = [];
    Object.entries(object).forEach(([key, raw]) => {
      if (skip.has(key)) return;
      if (raw && typeof raw === "object" && !Array.isArray(raw)) {
        if (key === "dollar" || key === "nominal_10y" || key === "real_10y") {
          const latest = metricScalarValue("latest", raw.latest, key);
          const change5 = fmtMacroChange(raw, "change_5obs");
          const change20 = fmtMacroChange(raw, "change_20obs");
          rows.push({
            name: mapLabelForKey(key),
            value: [latest ? `最新 ${latest}` : "", change5 ? `5观察 ${change5}` : "", change20 ? `20观察 ${change20}` : ""].filter(Boolean).join("；"),
          });
          return;
        }
        rows.push(...mapMetricRows(raw, key));
        return;
      }
      const scalar = metricScalarValue(key, raw, prefix);
      if (scalar) rows.push({ name: mapLabelForKey(key), value: scalar });
    });
    return rows;
  }

  function renderMapMetrics(metrics) {
    const rows = Array.isArray(metrics) ? mapMetricArrayRows(metrics) : mapMetricRows(metrics);
    const visibleRows = rows.filter((item) => item.value);
    if (!visibleRows.length) return `<p class="muted">未提供展开数值。</p>`;
    return `<ul>${visibleRows.slice(0, 10).map((item) =>
      `<li><span>${escapeHtml(item.name)}</span><strong>${escapeHtml(item.value)}</strong></li>`).join("")}</ul>`;
  }

  function mapMetricArrayRows(metrics) {
    return metrics.map((item, index) => {
      const object = asObject(item);
      const rawName = firstPresent(
        object.label_cn,
        object.name_cn,
        object.metric_cn,
        object.title_cn,
        object.display_label_cn,
        object.label,
        object.key,
        object.name,
        `指标 ${index + 1}`,
      );
      return {
        name: mapLabelForKey(rawName),
        value: fmtMapValue(object),
      };
    });
  }

  function mapMetricVisibleRows(metrics, maxRows = 4) {
    const rows = Array.isArray(metrics) ? mapMetricArrayRows(metrics) : mapMetricRows(metrics);
    return rows.filter((item) => item.value).slice(0, maxRows);
  }

  function renderMapMetricHighlights(metrics, maxRows = 4) {
    const rows = mapMetricVisibleRows(metrics, maxRows);
    if (!rows.length) return "";
    return `<dl class="map-metric-strip">${rows.map((item) =>
      `<div><dt>${escapeHtml(item.name)}</dt><dd>${escapeHtml(item.value)}</dd></div>`).join("")}</dl>`;
  }

  function mapFactTone(fact) {
    const stateText = String(firstPresent(fact.fact_state, fact.data_state, fact.status) ?? "").toUpperCase();
    if (stateText.includes("MISSING") || stateText.includes("GAP") || stateText.includes("BLOCK") || stateText.includes("FAIL")) return "bad";
    if (stateText.includes("STALE") || stateText.includes("HISTORICAL") || stateText.includes("DISPLAY") || stateText.includes("PARTIAL")) return "warn";
    return "blue";
  }

  function mapFactStateLabel(fact, map) {
    const raw = String(firstPresent(fact.fact_state, fact.data_state, fact.status, "CURRENT") ?? "CURRENT").toLowerCase();
    const identity = String(mapDataIdentity(map) ?? "").toUpperCase();
    if ((raw === "usable" || raw === "available") && (identity.includes("FIXTURE") || identity.includes("SYNTHETIC") || identity.includes("合成") || identity.includes("模拟"))) return "合成·结构有效";
    if (raw === "usable") return "有资格子事实";
    if (raw === "available") return "可用";
    if (raw === "partial") return "局部缺口";
    if (raw === "missing" || raw === "unavailable") return "缺失";
    return label(raw);
  }

  function mapPrimaryMetricLimit(key) {
    return String(key || "").toLowerCase() === "inventory" ? 7 : 6;
  }

  function mapBackgroundTimingText(row, missing, fallbackCutoff) {
    const computedCutoff = firstPresent(fallbackCutoff, row.cutoff_at_ms, row.cutoff_at, row.observation_end_ms, row.updated_at_ms);
    const sourceObserved = firstPresent(row.source_observation_end_ms, row.source_observation_end, row.source_observed_at_ms);
    const parts = [`计算截止 ${fmtTime(computedCutoff)}`];
    if (sourceObserved != null) parts.push(`来源最晚观察 ${fmtTime(sourceObserved)}`);
    if (missing) parts.push(`缺口 ${missing}`);
    return parts.join("；");
  }

  function mapBackgroundSourceFallback(rowKey) {
    const key = String(rowKey || "").toLowerCase();
    const fallbacks = {
      allocation: {
        headline: "Farside 等第三方 / 美国现货 BTC ETF",
        measure: "净美元流量汇总；BTC 等值按冻结指数价折合，非实际 ETF 买入 BTC 数量。",
        window: "交易日窗口依服务端冻结资料",
        qualification: "背景参考，不改写保单数值",
      },
      etf: {
        headline: "Farside 等第三方 / 美国现货 BTC ETF",
        measure: "净美元流量汇总；BTC 等值按冻结指数价折合，非实际 ETF 买入 BTC 数量。",
        window: "交易日窗口依服务端冻结资料",
        qualification: "背景参考，不改写保单数值",
      },
      inventory: {
        headline: "Bitview / BTC 链上",
        measure: "短期、长期与全链持有者资本化价格；CP 仅作来源缩写，不等于盘口成本。",
        window: "链上日线或服务端冻结窗口",
        qualification: "名义背景，不能当合同可比价格",
      },
      cp: {
        headline: "Bitview / BTC 链上",
        measure: "短期、长期与全链持有者资本化价格；CP 仅作来源缩写，不等于盘口成本。",
        window: "链上日线或服务端冻结窗口",
        qualification: "名义背景，不能当合同可比价格",
      },
      financing: {
        headline: "Binance / BTCUSDT U本位永续",
        measure: "原生持仓量与已结算资金费率；Funding 不是期权费用，持仓量不单独给方向。",
        window: "永续结算与行情窗口依服务端登记",
        qualification: "背景参考，不是选边票",
      },
      oi: {
        headline: "Binance / BTCUSDT U本位永续",
        measure: "原生持仓量与已结算资金费率；Funding 不是期权费用，持仓量不单独给方向。",
        window: "永续结算与行情窗口依服务端登记",
        qualification: "背景参考，不是选边票",
      },
      funding: {
        headline: "Binance / BTCUSDT U本位永续",
        measure: "原生持仓量与已结算资金费率；Funding 不是期权费用，持仓量不单独给方向。",
        window: "永续结算与行情窗口依服务端登记",
        qualification: "背景参考，不是选边票",
      },
      external_conditions: {
        headline: "FRED 与波动率指数 / 宏观背景",
        measure: "广义美元、美债十年名义/实际利率与 VXN 类波动率分量。",
        window: "各分量时钟独立",
        qualification: "背景压力，不改写保单数值",
      },
      macro: {
        headline: "FRED 与波动率指数 / 宏观背景",
        measure: "广义美元、美债十年名义/实际利率与 VXN 类波动率分量。",
        window: "各分量时钟独立",
        qualification: "背景压力，不改写保单数值",
      },
    };
    return fallbacks[key] || {};
  }

  function mapBackgroundSourceParts(row, rowKey) {
    const source = asObject(row.source_explanation);
    const direct = firstPresent(source.text_cn, source.summary_cn, row.source_explanation_cn, row.source_cn);
    const fallback = mapBackgroundSourceFallback(rowKey);
    const directParts = splitReadableParts(direct);
    const headline = joinTextParts([
      firstPresent(source.source_cn, source.source),
      firstPresent(source.market_cn, source.market),
    ], " / ") || fallback.headline || (directParts.length > 1 ? directParts[0] : "");
    const measure = joinTextParts([
      firstPresent(source.measure_cn, source.measure),
      directParts.length > 1 ? directParts.slice(1, 3).join("；") : directParts[0],
    ]) || fallback.measure || "";
    const window = firstPresent(source.window_cn, source.window, fallback.window, "");
    const qualification = firstPresent(source.qualification_cn, source.qualification, fallback.qualification, "");
    return {
      headline: normalizeInlineText(headline),
      measure: normalizeInlineText(measure),
      window: normalizeInlineText(window),
      qualification: normalizeInlineText(qualification),
    };
  }

  function renderMapBackgroundSource(row, rowKey) {
    const source = mapBackgroundSourceParts(row, rowKey);
    if (!source.headline && !source.measure) return "";
    return `<div class="map-source-explanation">
      ${source.headline ? `<p><strong>${escapeHtml(source.headline)}</strong></p>` : ""}
      ${source.measure ? `<p>${escapeHtml(source.measure)}</p>` : ""}
    </div>`;
  }

  function renderMapBackgroundSourceDetails(row, rowKey) {
    const source = mapBackgroundSourceParts(row, rowKey);
    const rows = [
      ["来源 / 市场", source.headline],
      ["口径", source.measure],
      ["时窗", source.window],
      ["资格", source.qualification],
    ].filter(([, value]) => value);
    const clockRows = renderMapSourceClockRows(row);
    if (!rows.length) return clockRows;
    return `<dl class="map-source-detail">${rows.map(([name, value]) =>
      `<div><dt>${escapeHtml(name)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>${clockRows}`;
  }

  function renderMapSourceClockRows(row) {
    const clocks = asArray(row.source_clock_rows).map(asObject).filter((item) => Object.keys(item).length);
    if (!clocks.length) return "";
    const clockText = (value, missing) => {
      const numeric = safeNumber(value);
      return numeric !== null && numeric > 0 ? fmtTime(numeric) : missing;
    };
    return `<div class="map-source-clock-list">
      <strong>分产品源钟</strong>
      <ul>${clocks.map((clock) => {
        const product = firstPresent(clock.product_id, clock.product, clock.source_id, "未知产品");
        const source = firstPresent(clock.source_cn, clock.source_name_cn, "");
        const productName = sourceClockProductLabel(product);
        const observed = `${clockText(firstPresent(clock.observation_start_ms, clock.observation_start), "缺少观察起点")} → ${clockText(firstPresent(clock.observation_end_ms, clock.observation_end), "缺少观察截止")}`;
        const firstSeen = clockText(firstPresent(clock.first_seen_at_ms, clock.first_available_at_ms, clock.known_at_ms), "缺少首次可得");
        const eligible = clock.eligible === true ? "用途合格" : (clock.eligible === false ? "用途不合格" : "用途资格未给出");
        const status = sourceClockStatusText(firstPresent(clock.usage_status, clock.status, clock.state, ""));
        const reasons = asArray(firstPresent(clock.reasons, clock.reason_codes, [])).map((item) => label(item)).filter(Boolean).join("；");
        const details = [
          `观察 ${observed}`,
          `首次可得 ${firstSeen}`,
          status,
          eligible,
          reasons,
          product ? `产品ID ${product}` : "",
        ].filter(Boolean).join("；");
        return `<li><span>${escapeHtml(source || productName)}</span><em>${escapeHtml(productName)}</em><strong>${escapeHtml(details)}</strong></li>`;
      }).join("")}</ul>
    </div>`;
  }

  function sourceClockProductLabel(value) {
    const raw = String(value ?? "").trim();
    const key = raw.toLowerCase();
    if (!key) return "未知产品";
    const labels = {
      "btc.etf.flow.daily.v1": "现货 BTC ETF 日净流量",
      "btc.brk.cost_basis.v1": "Bitview 持有者资本化价格",
      "btc.brk.realized_pnl.daily.v1": "Bitview 日已实现盈亏",
      "binance.um.btcusdt.oi.native.v1": "Binance BTCUSDT U本位持仓量",
      "binance.um.btcusdt.funding.settled.v1": "Binance BTCUSDT 已结算 Funding",
      "cex.usdt.borrow_rate.v1": "USDT 借款利率",
      "macro.usd.broad.close.v1": "广义美元 DTWEXBGS",
      "macro.us10y.nominal.v1": "美国十年名义利率",
      "macro.us10y.real.v1": "美国十年实际利率",
      "deribit.btc.index.map_context.v1": "Deribit BTC USD 指数",
      "btc.kpf.map.readonly.v1": "KPF 历史接受区",
      etf_flow_daily: "现货 BTC ETF 日净流量",
      macro_usd_broad: "广义美元 DTWEXBGS",
    };
    if (labels[key]) return labels[key];
    if (key.includes("btc.etf.flow")) return "现货 BTC ETF 日净流量";
    if (key.includes("btc.brk.cost_basis")) return "Bitview 持有者资本化价格";
    if (key.includes("btc.brk.realized_pnl")) return "Bitview 日已实现盈亏";
    if (key.includes("binance.um.btcusdt.oi")) return "Binance BTCUSDT U本位持仓量";
    if (key.includes("binance.um.btcusdt.funding")) return "Binance BTCUSDT 已结算 Funding";
    if (key.includes("cex.usdt.borrow")) return "USDT 借款利率";
    if (key.includes("macro.usd.broad")) return "广义美元 DTWEXBGS";
    if (key.includes("macro.us10y.nominal")) return "美国十年名义利率";
    if (key.includes("macro.us10y.real")) return "美国十年实际利率";
    if (key.includes("deribit.btc.index") || key.includes("btc.index")) return "Deribit BTC USD 指数";
    return "来源产品";
  }

  function sourceClockStatusText(value) {
    const raw = String(value ?? "").trim();
    const normalized = raw.toLowerCase();
    const labels = {
      current: "当前",
      usable: "可用",
      available: "可用",
      partial: "部分可用",
      stale: "过期",
      missing: "缺失",
      unavailable: "不可用",
      missing_or_invalid: "缺失或无效",
      unknown: "未知",
    };
    return labels[normalized] || label(raw || "未知");
  }

  function renderMapBackground(map) {
    const presentation = mapPresentation(map);
    const presentationRows = asArray(firstPresent(presentation.background_rows, presentation.bgrows, presentation.background, []))
      .map(asObject)
      .filter((row) => Object.keys(row).length);
    if (presentationRows.length) {
      return `<div class="map-background-grid">${presentationRows.slice(0, 4).map((row, index) => {
        const rowKey = firstPresent(row.key, `row_${index + 1}`);
        const title = firstPresent(row.title_cn, row.label_cn, row.name_cn, mapLabelForKey(rowKey));
        const metrics = firstPresent(row.primary_metrics, row.metrics, row.main_numbers, row.numbers, []);
        const missing = asArray(firstPresent(row.missing_cn, row.missing_summary_cn, [])).map((item) => String(item)).join("；");
        return `<article class="map-card">
          <div class="map-card-head"><div><h3>${escapeHtml(title)}</h3>${renderMapBackgroundSource(row, rowKey)}</div>${badge(mapFactStateLabel(row, map), mapFactTone(row))}</div>
          <p><strong>${escapeHtml(firstPresent(row.summary_cn, row.summary, "服务端未给摘要"))}</strong></p>
          ${renderMapMetricHighlights(metrics, mapPrimaryMetricLimit(rowKey))}
          <p class="muted">${escapeHtml(mapBackgroundTimingText(row, missing))}</p>
          <details class="map-details"><summary>展开完整数值</summary>${renderMapBackgroundSourceDetails(row, rowKey)}${renderMapMetrics(metrics)}</details>
        </article>`;
      }).join("")}</div>`;
    }
    const background = asObject(map.background);
    const rows = [
      ["allocation", "配置", "ETF与配置速度"],
      ["inventory", "库存", "链上成本与兑现"],
      ["financing", "融资", "杠杆、资金费率与借款成本"],
      ["external_conditions", "外部", "美元、美债与波动率"],
    ];
    return `<div class="map-background-grid">${rows.map(([key, title, purpose]) => {
      const fact = asObject(background[key]);
      if (!Object.keys(fact).length) {
        return `<article class="map-card is-gap">
          <div class="map-card-head"><div><h3>${escapeHtml(title)}</h3>${renderMapBackgroundSource({}, key)}</div>${badge("未提供", "warn")}</div>
          <p class="muted">${escapeHtml(purpose)}：服务端未返回本行资料。</p>
        </article>`;
      }
      const missing = asArray(firstPresent(fact.missing_cn, fact.missing_summary_cn, [])).map((item) => String(item)).join("；");
      const cutoff = firstPresent(fact.cutoff_at_ms, fact.cutoff_at, fact.observation_end, fact.updated_at_ms);
      return `<article class="map-card">
        <div class="map-card-head"><div><h3>${escapeHtml(title)}</h3>${renderMapBackgroundSource(fact, key)}</div>${badge(mapFactStateLabel(fact, map), mapFactTone(fact))}</div>
        <p><strong>${escapeHtml(firstPresent(fact.summary_cn, fact.summary, "服务端未给摘要"))}</strong></p>
        ${renderMapMetricHighlights(fact.metrics, mapPrimaryMetricLimit(key))}
        <p class="muted">${escapeHtml(mapBackgroundTimingText(fact, missing, cutoff))}</p>
        <details class="map-details">
          <summary>展开完整数值</summary>
          ${renderMapBackgroundSourceDetails(fact, key)}
          ${renderMapMetrics(fact.metrics)}
        </details>
      </article>`;
    }).join("")}</div>`;
  }

  function refId(ref) {
    return firstPresent(ref.reference_id, ref.id, ref.ref_id, "");
  }

  function refPrice(ref) {
    return firstPresent(ref.display_center, ref.display_price_usd, ref.display_value_usd, ref.mapped_price, ref.raw_price, ref.price_usd, ref.value_usd, ref.center_usd, ref.level_usd);
  }

  function refRange(ref) {
    const band = Array.isArray(ref.display_band) && ref.display_band.length === 2 ? ref.display_band : null;
    const mapped = asObject(ref.mapped_zone);
    const low = firstPresent(ref.display_low_usd, band && band[0], mapped.low, ref.raw_low, ref.low_usd, ref.region_low_usd, ref.range_low_usd);
    const high = firstPresent(ref.display_high_usd, band && band[1], mapped.high, ref.raw_high, ref.high_usd, ref.region_high_usd, ref.range_high_usd);
    if (safeNumber(low) !== null && safeNumber(high) !== null) {
      return safeNumber(low) === safeNumber(high) ? fmtUsd(low) : `${fmtUsd(low)} — ${fmtUsd(high)}`;
    }
    return fmtUsd(refPrice(ref));
  }

  function displayedReferences(map) {
    const presentation = mapPresentation(map);
    const axis = asObject(firstPresent(presentation.price_axis, presentation.axis, {}));
    const presentationRefs = asArray(firstPresent(axis.references, axis.markers, axis.items, axis.coordinates, []))
      .map(asObject)
      .filter((ref) => Object.keys(ref).length)
      .map((ref, index) => ({
        ...ref,
        reference_id: firstPresent(ref.reference_id, ref.id, `presentation_axis_${index}`),
        family: firstPresent(ref.family, ref.layer, ref.kind, "PRESENTATION_AXIS"),
        role: firstPresent(ref.role, ref.reference_role, ref.geometry, "POINT"),
        display_price_usd: firstPresent(ref.display_price_usd, ref.price_usd, ref.value_usd, ref.raw_price, ref.price),
        display_low_usd: firstPresent(ref.display_low_usd, ref.low_usd, ref.raw_low, ref.low),
        display_high_usd: firstPresent(ref.display_high_usd, ref.high_usd, ref.raw_high, ref.high),
        eligible_for_display: firstPresent(ref.eligible_for_display, ref.selected_for_display, true),
      }));
    const combined = [
      ...asArray(map.reference_pool).map(asObject),
      ...presentationRefs,
    ].filter((ref) => Object.keys(ref).length && ref.family !== "CONTRACT_LIABILITY");
    const unique = new Map();
    combined.forEach((ref) => unique.set(String(refId(ref)), {...asObject(unique.get(String(refId(ref)))), ...ref}));
    const pool = [...unique.values()];
    const ids = new Set(asArray(map.display_reference_ids).map(String));
    const chosen = ids.size
      ? pool.filter((ref) => ids.has(String(refId(ref))) && ref.eligible_for_display === true)
      : pool.filter((ref) => ref.eligible_for_display === true && ref.selected_for_display !== false);
    return chosen.slice().sort((a, b) => (safeNumber(refPrice(b)) ?? -Infinity) - (safeNumber(refPrice(a)) ?? -Infinity)).slice(0, 6);
  }

  function referenceFamily(ref) {
    return firstPresent(ref.family, ref.source_family, ref.layer, ref.kind, ref.type, "MARKET_REFERENCE");
  }

  function referenceRole(ref) {
    return firstPresent(ref.role, ref.reference_role, ref.geometry, referenceFamily(ref));
  }

  function knownLabel(value) {
    const raw = String(value ?? "").trim();
    const text = label(value, "");
    if (text === raw && /^[A-Z0-9_ ./|:-]+$/.test(raw) && (raw.includes("_") || /[\s/|]/.test(raw))) return "";
    return text && text !== "未归类资料状态（见原始记录）" ? text : "";
  }

  function readableReferenceText(value) {
    const raw = String(value ?? "").trim();
    if (!raw) return "";
    const direct = knownLabel(raw);
    if (direct) return direct;
    if (/^[A-Z0-9_ ./|:-]+$/.test(raw) && (raw.includes("_") || /[\s/|]/.test(raw))) {
      const parts = raw.split(/[\s/|]+/).filter(Boolean);
      const converted = parts.map((part) => knownLabel(part) || mapLabelForKey(part)).filter(Boolean);
      if (converted.length && converted.join(" ").toUpperCase() !== raw.toUpperCase()) return converted.join(" · ");
    }
    return raw;
  }

  function referenceDisplayTitle(ref) {
    const explicit = firstPresent(ref.label_cn, ref.name_cn, ref.title_cn);
    if (explicit) return readableReferenceText(explicit);
    return firstPresent(knownLabel(referenceRole(ref)), knownLabel(referenceFamily(ref)), readableReferenceText(referenceRole(ref)), readableReferenceText(referenceFamily(ref)));
  }

  function priceBasisText(value, market = "") {
    const raw = String(value ?? "").trim();
    if (!raw) return market ? `${label(market)} 价系未说明` : "价系未提供";
    const upper = raw.toUpperCase();
    if (upper === "DERIBIT_BTC_USD_INDEX" || upper === "DERIBIT_INDEX") return "Deribit BTC USD 指数";
    if (upper === "SYNTHETIC_FIXTURE") return "合成 BTC/USD 参考";
    if (upper === "ONCHAIN_COST_CP_STH") return "链上短期持有者资本化价格口径";
    if (upper === "GLOBAL_ONLY") return "全市场口径";
    if (upper.includes("BTC") && upper.includes("USDT")) return "BTC/USDT 价系";
    if (upper.includes("BTC") && upper.includes("USD")) return "BTC/USD 价系";
    if (upper === "USD") return "USD 价系";
    if (upper === "USDT") return "USDT 价系";
    return /^[A-Za-z0-9_.:-]+$/.test(raw) ? "来源价系已记录，页面未归类" : raw;
  }

  function expiryScopeText(value) {
    if (value === null || value === undefined || value === "") return "期限未提供";
    const raw = String(value).trim();
    const upper = raw.toUpperCase();
    if (upper === "GLOBAL_ONLY") return "全市场期限未绑定";
    if (upper === "CURRENT_ONLY") return "仅当前截面";
    const numeric = safeNumber(value);
    if (numeric !== null && numeric > 1_000_000_000_000) return `绑定到期 ${fmtTime(numeric)}`;
    return /^[A-Za-z0-9_.:-]+$/.test(raw) ? "期限身份已记录，页面未归类" : raw;
  }

  function referenceSourceNote(ref) {
    const raw = firstPresent(ref.original_price_usd, ref.raw_price_usd, ref.source_price_usd, ref.raw_price);
    const basis = priceBasisText(firstPresent(ref.price_basis_cn, ref.price_basis, ref.quote_basis), ref.market);
    const term = expiryScopeText(firstPresent(ref.tenor_cn, ref.term_cn, ref.expiry_label, ref.expiry_scope, ref.expiry, ref.scope_cn));
    return [
      safeNumber(raw) !== null ? `原价 ${fmtUsd(raw)}` : "",
      basis,
      term,
      firstPresent(ref.notional_reference_cn, ref.notional_reference, ""),
    ].filter(Boolean).join("；");
  }

  function renderReferenceSourceDetails(ref) {
    const limitations = asArray(ref.limitations).map((item) => label(item)).filter(Boolean).join("；");
    const rows = [
      ["来源实体", firstPresent(ref.source_entity_cn, ref.source_name_cn, label(ref.market), label(referenceFamily(ref)))],
      ["来源类别", label(firstPresent(ref.source_family, referenceFamily(ref)))],
      ["几何身份", label(firstPresent(ref.geometry, referenceRole(ref)))],
      ["观察截止", fmtTime(firstPresent(ref.observation_end_ms, ref.source_cutoff_at_ms, ref.cutoff_at_ms))],
      ["首次可得", fmtTime(firstPresent(ref.known_at_ms, ref.first_seen_at_ms))],
      ["生效时间", fmtTime(firstPresent(ref.active_from_ms, ref.valid_from_ms))],
      ["资料质量", label(firstPresent(ref.quality, ref.status, "CURRENT"))],
      ["限制说明", limitations],
    ].filter(([, value]) => value && value !== "未提供");
    if (!rows.length) return "";
    return `<details class="map-details map-source-details">
      <summary>来源详情</summary>
      <ul>${rows.map(([name, value]) => `<li><span>${escapeHtml(name)}</span><strong>${escapeHtml(value)}</strong></li>`).join("")}</ul>
    </details>`;
  }

  function referenceMapById(map) {
    const entries = asArray(map.reference_pool).map(asObject)
      .filter((ref) => refId(ref))
      .map((ref) => [String(refId(ref)), ref]);
    return new Map(entries);
  }

  function contractReferences(map) {
    return asArray(map.reference_pool)
      .map(asObject)
      .filter((ref) => ref.family === "CONTRACT_LIABILITY")
      .slice()
      .sort((a, b) => (safeNumber(refPrice(a)) ?? Infinity) - (safeNumber(refPrice(b)) ?? Infinity));
  }

  function mapCutoff(map) {
    const presentation = mapPresentation(map);
    const identity = asObject(presentation.candidate_identity);
    return firstPresent(
      map.cutoff_at_ms,
      map.cutoff_at,
      presentation.reference_time_ms,
      presentation.cutoff_at_ms,
      identity.reference_time_ms,
      map.updated_at_ms,
    );
  }

  function mapExplicitCutoff(map) {
    const presentation = mapPresentation(map);
    return firstPresent(
      map.cutoff_at_ms,
      map.cutoff_at,
      presentation.cutoff_at_ms,
      presentation.cutoff_at,
    );
  }

  function mapPresentation(map) {
    return asObject(map.presentation);
  }

  function mapDataIdentity(map) {
    const presentation = mapPresentation(map);
    return firstPresent(presentation.mode, presentation.mode_cn, presentation.data_identity, presentation.presentation_mode, map.data_identity, map.mode, "");
  }

  function mapIdentityText(map) {
    if (isReplayPreview()) return "冻结回放";
    if (isSyntheticPreview()) return "合成演示";
    const text = String(mapDataIdentity(map) ?? "");
    const raw = text.toUpperCase();
    if (text.includes("合成") || text.includes("模拟")) return "合成演示";
    if (text.includes("回放") || text.includes("历史")) return "冻结回放";
    if (text.includes("实盘") || text.includes("当前")) return "当前资料";
    if (raw.includes("FIXTURE") || raw.includes("SYNTHETIC")) return "合成演示";
    if (raw.includes("REPLAY") || raw.includes("HISTORICAL")) return "冻结回放";
    if (raw.includes("LIVE") || raw.includes("CURRENT")) return "当前资料";
    return "身份未确认";
  }

  function mapCurrentPriceLabel(map) {
    const identity = mapIdentityText(map);
    if (identity === "冻结回放") return "冻结现价";
    if (identity === "合成演示") return "示例现价";
    if (identity === "身份未确认") return "资料现价";
    return "当前现价";
  }

  function mapCurrentPrice(map) {
    const presentation = mapPresentation(map);
    const axis = asObject(firstPresent(presentation.price_axis, presentation.axis, {}));
    const mapping = asObject(firstPresent(presentation.mapping, presentation.readonly_mapping, map.mapping, map.readonly_mapping, map.source_map, {}));
    const priceIdentity = asObject(map.price_identity);
    const currentFact = asObject(firstPresent(
      axis.current_price_fact,
      presentation.current_price_fact,
      map.current_price_fact,
      mapping.current_price_fact,
      priceIdentity.current_price_fact,
      {},
    ));
    const current = asObject(firstPresent(axis.current, presentation.current_price, presentation.price_cursor, currentFact, priceIdentity, {}));
    const identity = asObject(presentation.candidate_identity);
    const hasPresentationCurrent = Object.keys(asObject(axis.current)).length > 0
      || Object.keys(asObject(presentation.current_price)).length > 0
      || Object.keys(currentFact).length > 0
      || Object.keys(identity).length > 0;
    return {
      price: firstPresent(
        current.price_usd,
        current.current_price_usd,
        current.index_price_usd,
        currentFact.price_usd,
        currentFact.current_price_usd,
        currentFact.index_price_usd,
        identity.current_price_usd,
        map.current_price_usd,
      ),
      source: firstPresent(
        current.source_cn,
        current.source,
        current.manifest_cn,
        current.manifest,
        currentFact.source_cn,
        currentFact.source,
        currentFact.manifest_cn,
        currentFact.manifest,
        identity.reference_source,
        priceIdentity.source,
        "来源未提供",
      ),
      basis: priceBasisText(firstPresent(current.price_basis, current.basis, currentFact.price_basis, currentFact.basis, map.price_basis)),
      observedAt: firstPresent(
        current.time_ms,
        current.observed_at_ms,
        current.cutoff_at_ms,
        current.observation_end_ms,
        currentFact.time_ms,
        currentFact.observed_at_ms,
        currentFact.cutoff_at_ms,
        currentFact.observation_end_ms,
        identity.reference_time_ms,
        hasPresentationCurrent ? undefined : mapCutoff(map),
      ),
    };
  }

  function referenceBounds(ref) {
    const band = Array.isArray(ref.display_band) && ref.display_band.length === 2 ? ref.display_band : null;
    const mapped = asObject(ref.mapped_zone);
    const low = firstPresent(ref.display_low_usd, band && band[0], mapped.low, ref.raw_low, ref.low_usd, ref.region_low_usd, ref.range_low_usd, refPrice(ref));
    const high = firstPresent(ref.display_high_usd, band && band[1], mapped.high, ref.raw_high, ref.high_usd, ref.region_high_usd, ref.range_high_usd, refPrice(ref));
    const lo = safeNumber(low);
    const hi = safeNumber(high);
    if (lo === null && hi === null) return null;
    const a = lo ?? hi;
    const b = hi ?? lo;
    return { low: Math.min(a, b), high: Math.max(a, b), point: Math.abs(a - b) < 1e-9 };
  }

  function coordinateQualification(ref) {
    return String(firstPresent(ref.coordinate_qualification, ref.mapping_quality, ref.mapping_state, "")).toUpperCase();
  }

  function hasNativeOrQualifiedBasis(ref) {
    const qualification = coordinateQualification(ref);
    const allowedQualifications = ["COMPARABLE", "SAME_BASIS", "QUALIFIED_CONVERSION", "QUALIFIED"];
    const basisState = String(firstPresent(
      ref.price_basis_qualification,
      ref.basis_qualification,
      ref.coordinate_basis_qualification,
      ref.basis_mapping_quality,
    ) ?? "").toUpperCase();
    if (["NATIVE", "SAME_BASIS", "QUALIFIED", "QUALIFIED_CONVERSION", "COMPARABLE"].includes(basisState)) return true;
    if (["GLOBAL_ONLY", "UNKNOWN", "DISPLAY_ONLY", "NOMINAL_ONLY", "NOT_COMPARABLE"].includes(basisState)) return false;
    const basis = String(firstPresent(ref.price_basis, ref.quote_basis, ref.market) ?? "").toUpperCase();
    if (basis.includes("USDT")) return false;
    if (!basis && !basisState) return allowedQualifications.includes(qualification);
    return basis.includes("DERIBIT_BTC_USD_INDEX")
      || basis.includes("BTC_USD")
      || basis.includes("BTC-USD")
      || basis.includes("INDEX")
      || qualification === "QUALIFIED"
      || qualification === "QUALIFIED_CONVERSION";
  }

  function isComparableReference(ref) {
    const qualification = coordinateQualification(ref);
    const allowed = new Set(["COMPARABLE", "SAME_BASIS", "QUALIFIED_CONVERSION", "QUALIFIED"]);
    return firstPresent(ref.eligible_for_display, true) === true
      && allowed.has(qualification)
      && hasNativeOrQualifiedBasis(ref);
  }

  function selectedPolicyLink(context) {
    return asObject(asObject(context.map).policy_link);
  }

  function contractGroupFromMap(map, option, activeKey, currentCutoff) {
    const refs = contractReferences(map);
    if (!refs.length) return null;
    const policy = asObject(map.policy_link);
    const explicitCutoff = mapExplicitCutoff(map);
    const hasCutoff = explicitCutoff !== undefined && explicitCutoff !== null && explicitCutoff !== "";
    const hasCurrentCutoff = currentCutoff !== undefined && currentCutoff !== null && currentCutoff !== "";
    const side = firstPresent(option && option.side, policy.contract && policy.contract.side, "保单");
    const expiry = firstPresent(
      policy.contract && policy.contract.expiry_ms,
      refs.map((ref) => firstPresent(ref.expiry_scope, ref.expiry)).find(Boolean),
    );
    return {
      key: option ? option.key : "selected",
      side,
      active: option ? option.key === activeKey : true,
      cutoff: mapCutoff(map),
      sameCutoff: hasCutoff && hasCurrentCutoff && String(explicitCutoff) === String(currentCutoff),
      expiry,
      candidateVersion: firstPresent(policy.candidate_version, policy.candidate_id, ""),
      refs,
    };
  }

  function contractGroupsForAxis(context) {
    if (context.source === "global" && !context.candidate) return [];
    const currentCutoff = mapExplicitCutoff(context.map);
    const activeKey = state.selectedMapCandidateKey;
    const groups = [];
    context.options.forEach((option) => {
      const map = candidateBtcMap(option.candidate);
      if (!Object.keys(map).length) return;
      const group = contractGroupFromMap(map, option, activeKey, currentCutoff);
      if (!group) return;
      groups.push(group);
    });
    if (!groups.length) {
      const group = contractGroupFromMap(context.map, null, activeKey, currentCutoff);
      if (group) groups.push(group);
    }
    return groups;
  }

  function axisDomain(values) {
    const numeric = values.map(safeNumber).filter((item) => item !== null);
    if (!numeric.length) return { min: 0, max: 1, width: 1 };
    let min = Math.min(...numeric);
    let max = Math.max(...numeric);
    if (Math.abs(max - min) < 1e-9) {
      min -= 3000;
      max += 3000;
    }
    const pad = Math.max(600, (max - min) * 0.08);
    min -= pad;
    max += pad;
    return { min, max, width: max - min };
  }

  function buildMapAxisModel(context) {
    const map = asObject(context.map);
    const current = mapCurrentPrice(map);
    const currentPrice = safeNumber(current.price);
    const marketRefsAll = displayedReferences(map);
    const comparableRefs = marketRefsAll.filter((ref) => isComparableReference(ref) && referenceBounds(ref));
    const nominalRefs = marketRefsAll.filter((ref) => !isComparableReference(ref) && referenceBounds(ref));
    const contractGroups = contractGroupsForAxis(context);
    const activeGroup = contractGroups.find((item) => item.active) || contractGroups[0] || null;
    const activeValues = [];
    if (currentPrice !== null) activeValues.push(currentPrice);
    const contractDomainGroups = state.mapAxisMode === "all"
      ? contractGroups.filter((item) => item.active || item.sameCutoff)
      : (activeGroup ? [activeGroup] : contractGroups);
    contractDomainGroups.flatMap((item) => item.refs).forEach((ref) => {
      const bounds = referenceBounds(ref);
      if (bounds) activeValues.push(bounds.low, bounds.high);
    });
    comparableRefs.forEach((ref) => {
      const bounds = referenceBounds(ref);
      if (state.mapAxisMode === "all" && bounds) activeValues.push(bounds.low, bounds.high);
      if (state.mapAxisMode !== "all" && bounds && currentPrice !== null) {
        const distance = Math.min(Math.abs(bounds.low - currentPrice), Math.abs(bounds.high - currentPrice));
        if (distance <= Math.max(2500, Math.abs(currentPrice) * 0.04)) activeValues.push(bounds.low, bounds.high);
      }
    });
    const domain = axisDomain(activeValues);
    const toX = (price, left = 126, width = 900) => left + (safeNumber(price) - domain.min) / domain.width * width;
    const groupOutsideDomain = (group) => group.refs.some((ref) => {
      const bounds = referenceBounds(ref);
      return bounds && (bounds.low < domain.min || bounds.high > domain.max);
    });
    const axisContractGroups = contractGroups.filter((item) =>
      item.active || (item.sameCutoff && (state.mapAxisMode === "all" || !groupOutsideDomain(item))));
    const offAxisContractGroups = contractGroups.filter((item) =>
      !item.active && (!item.sameCutoff || (state.mapAxisMode !== "all" && groupOutsideDomain(item))));
    const familyTrack = (ref) => {
      const family = String(referenceFamily(ref)).toUpperCase();
      const role = String(referenceRole(ref)).toUpperCase();
      if (family === "OPTION_STRUCTURE" || role.includes("FLIP") || role.includes("WALL")) return "option";
      if (family === "TRADED_ACCEPTANCE" || role.includes("KPF") || role.includes("BASIN") || role.includes("ZONE")) return "acceptance";
      if (family === "ONCHAIN_COST" || role.includes("CP")) return "onchain";
      return "other";
    };
    const markerForRef = (ref, track, source = "market") => {
      const bounds = referenceBounds(ref);
      if (!bounds) return null;
      const id = refId(ref);
      const labelText = referenceDisplayTitle(ref);
      const lowIn = bounds.low >= domain.min && bounds.low <= domain.max;
      const highIn = bounds.high >= domain.min && bounds.high <= domain.max;
      const offscreen = !lowIn || !highIn;
      return {
        id,
        source,
        track,
        label: labelText,
        family: referenceFamily(ref),
        role: referenceRole(ref),
        low: bounds.low,
        high: bounds.high,
        point: bounds.point,
        x1: toX(Math.min(Math.max(bounds.low, domain.min), domain.max)),
        x2: toX(Math.min(Math.max(bounds.high, domain.min), domain.max)),
        rawX1: toX(bounds.low),
        rawX2: toX(bounds.high),
        offscreen,
        edge: bounds.high < domain.min ? "left" : bounds.low > domain.max ? "right" : "",
        comparable: isComparableReference(ref),
        familyTrack: familyTrack(ref),
        note: referenceSourceNote(ref),
      };
    };
    const markers = comparableRefs.map((ref) => markerForRef(ref, familyTrack(ref))).filter(Boolean);
    const nominalReferences = nominalRefs.map((ref) => {
      const bounds = referenceBounds(ref);
      return {
        id: refId(ref),
        label: referenceDisplayTitle(ref),
        family: referenceFamily(ref),
        role: referenceRole(ref),
        low: bounds.low,
        high: bounds.high,
        point: bounds.point,
        comparable: false,
        qualification: coordinateQualification(ref),
        note: referenceSourceNote(ref),
      };
    });
    const contracts = axisContractGroups.map((group) => ({
      ...group,
      refs: group.refs.map((ref) => markerForRef(ref, group.active ? "contract-active" : "contract-peer", "contract")).filter(Boolean),
    }));
    return {
      identity: mapIdentityText(map),
      currentLabel: mapCurrentPriceLabel(map),
      cutoff: mapCutoff(map),
      current,
      currentPrice,
      domain,
      toX,
      markers,
      nominalReferences,
      contracts,
      offAxisContractGroups,
      selectedRegionId: state.selectedMapRegionId,
      mode: state.mapAxisMode,
      hasComparableRefs: comparableRefs.length > 0,
      hasNominalRefs: nominalRefs.length > 0,
    };
  }

  function roleShortText(role) {
    const upper = String(role || "").toUpperCase();
    if (upper === "SHORT_LEG") return "卖出腿";
    if (upper === "PROTECTIVE_LEG") return "保护腿";
    if (upper === "NEAR_BREAKEVEN") return "近端BE";
    return label(role);
  }

  function svgText(value) {
    return escapeHtml(String(value ?? ""));
  }

  function renderAxisMarker(marker, y, selected, lane = 0) {
    const labelText = `${marker.label} ${marker.point ? fmtUsd(marker.low) : `${fmtUsd(marker.low)}—${fmtUsd(marker.high)}`}`;
    const className = ["map-axis-pick", marker.point ? "is-point" : "is-zone", selected ? "is-selected" : "", marker.comparable ? "is-comparable" : "is-nominal"].filter(Boolean).join(" ");
    const actualWidth = Math.abs(marker.x2 - marker.x1);
    const hitX = Math.min(marker.x1, marker.x2) - Math.max(0, (12 - actualWidth) / 2);
    const hitWidth = Math.max(12, actualWidth);
    const visual = marker.point
      ? `<circle cx="${marker.x1.toFixed(2)}" cy="${y}" r="6"></circle><circle class="map-axis-hitbox" cx="${marker.x1.toFixed(2)}" cy="${y}" r="13"></circle>`
      : `<rect x="${Math.min(marker.x1, marker.x2).toFixed(2)}" y="${y - 7}" width="${actualWidth.toFixed(2)}" height="14" rx="2"></rect><rect class="map-axis-hitbox" x="${hitX.toFixed(2)}" y="${y - 13}" width="${hitWidth.toFixed(2)}" height="26" rx="3"></rect>`;
    const textX = marker.edge === "left" ? 132 : marker.edge === "right" ? 1018 : (marker.point ? marker.x1 : (marker.x1 + marker.x2) / 2);
    const anchor = marker.edge === "left" ? "start" : marker.edge === "right" ? "end" : "middle";
    const textY = y + 28 + lane * 15;
    const edge = marker.edge ? `<text x="${textX.toFixed(2)}" y="${y - 18}" text-anchor="${anchor}" class="map-axis-edge">${svgText(marker.edge === "left" ? "左侧越界" : "右侧越界")} · ${svgText(marker.point ? fmtUsd(marker.low) : `${fmtUsd(marker.low)}—${fmtUsd(marker.high)}`)}</text>` : "";
    return `<g class="${className}" tabindex="0" role="button" data-map-region-id="${escapeHtml(marker.id)}" aria-label="${escapeHtml(labelText)}">
      <title>${escapeHtml(`${labelText}；${marker.note || "来源口径未提供"}`)}</title>
      ${visual}
      ${edge}
      ${marker.edge ? "" : `<path class="map-axis-leader" d="M${textX.toFixed(2)},${y + 10} L${textX.toFixed(2)},${textY - 12}"></path>`}
      <text x="${textX.toFixed(2)}" y="${textY}" text-anchor="${anchor}">${svgText(marker.label)}</text>
    </g>`;
  }

  function renderContractGroup(group, y, selectedRegionId) {
    const short = group.refs.find((ref) => String(ref.role).toUpperCase() === "SHORT_LEG");
    const long = group.refs.find((ref) => String(ref.role).toUpperCase() === "PROTECTIVE_LEG");
    const pointY = y + 40;
    const bandWidth = short && long ? Math.abs(short.x1 - long.x1) : 0;
    const bandHitX = short && long ? Math.min(short.x1, long.x1) - Math.max(0, (12 - bandWidth) / 2) : 0;
    const band = short && long ? `<rect class="map-contract-band ${group.active ? "is-active" : ""}" x="${Math.min(short.x1, long.x1).toFixed(2)}" y="${pointY - 7}" width="${bandWidth.toFixed(2)}" height="14" rx="2"></rect><rect class="map-contract-hitbox" x="${bandHitX.toFixed(2)}" y="${pointY - 14}" width="${Math.max(12, bandWidth).toFixed(2)}" height="28" rx="3"></rect>` : "";
    const labelLanes = new Map();
    group.refs.slice().sort((a, b) => a.x1 - b.x1).forEach((marker) => {
      const used = new Set();
      group.refs.forEach((other) => {
        if (other === marker) return;
        if (labelLanes.has(other.id) && Math.abs(other.x1 - marker.x1) < 76) used.add(labelLanes.get(other.id));
      });
      let lane = 0;
      while (used.has(lane)) lane += 1;
      labelLanes.set(marker.id, Math.min(lane, 3));
    });
    return `<g class="map-contract-group ${group.active ? "is-active" : ""}">
      <text x="18" y="${y}" class="map-axis-rowlabel">${svgText(`${group.side} 合同`)}</text>
      <text x="18" y="${y + 18}" class="map-axis-small">${svgText(`${group.sameCutoff ? "同MAP截止" : "冻结时点不同"} ${fmtTime(group.cutoff)}`)}</text>
      ${band}
      ${group.refs.map((marker) => {
        const selected = marker.id && marker.id === selectedRegionId;
        const role = roleShortText(marker.role);
        const symbol = String(marker.role).toUpperCase() === "NEAR_BREAKEVEN" ? "diamond" : String(marker.role).toUpperCase() === "PROTECTIVE_LEG" ? "square" : "circle";
        const shape = symbol === "diamond"
          ? `<path d="M${marker.x1.toFixed(2)},${pointY - 8} L${(marker.x1 + 7).toFixed(2)},${pointY} L${marker.x1.toFixed(2)},${pointY + 8} L${(marker.x1 - 7).toFixed(2)},${pointY} Z"></path><circle class="map-axis-hitbox" cx="${marker.x1.toFixed(2)}" cy="${pointY}" r="13"></circle>`
          : symbol === "square"
            ? `<rect x="${(marker.x1 - 6).toFixed(2)}" y="${pointY - 6}" width="12" height="12"></rect><circle class="map-axis-hitbox" cx="${marker.x1.toFixed(2)}" cy="${pointY}" r="13"></circle>`
            : `<circle cx="${marker.x1.toFixed(2)}" cy="${pointY}" r="6"></circle><circle class="map-axis-hitbox" cx="${marker.x1.toFixed(2)}" cy="${pointY}" r="13"></circle>`;
        const lane = labelLanes.get(marker.id) || 0;
        const labelY = pointY + 24 + lane * 15;
        const labelAnchor = marker.x1 > 980 ? "end" : "middle";
        return `<g class="map-axis-pick is-contract ${selected ? "is-selected" : ""}" tabindex="0" role="button" data-map-region-id="${escapeHtml(marker.id)}" aria-label="${escapeHtml(`${group.side} ${role} ${fmtUsd(marker.low)}`)}">
          <title>${escapeHtml(`${group.side} ${role} ${fmtUsd(marker.low)}；合同线不是独立市场确认`)}</title>
          ${shape}
          <path class="map-axis-leader" d="M${marker.x1.toFixed(2)},${pointY + 10} L${marker.x1.toFixed(2)},${labelY - 12}"></path>
          <text x="${marker.x1.toFixed(2)}" y="${labelY}" text-anchor="${labelAnchor}">${svgText(`${role} ${fmtNumber(marker.low, 0)}`)}</text>
        </g>`;
      }).join("")}
    </g>`;
  }

  function markerForLayout(marker, model, left, plotWidth) {
    const low = safeNumber(marker.low);
    const high = safeNumber(marker.high);
    if (low === null && high === null) return marker;
    const lo = low ?? high;
    const hi = high ?? low;
    const clampedLow = Math.min(Math.max(Math.min(lo, hi), model.domain.min), model.domain.max);
    const clampedHigh = Math.min(Math.max(Math.max(lo, hi), model.domain.min), model.domain.max);
    return {
      ...marker,
      x1: model.toX(clampedLow, left, plotWidth),
      x2: model.toX(clampedHigh, left, plotWidth),
      rawX1: model.toX(Math.min(lo, hi), left, plotWidth),
      rawX2: model.toX(Math.max(lo, hi), left, plotWidth),
      edge: Math.max(lo, hi) < model.domain.min ? "left" : (Math.min(lo, hi) > model.domain.max ? "right" : ""),
    };
  }

  function contractGroupForLayout(group, model, left, plotWidth) {
    return { ...group, refs: group.refs.map((marker) => markerForLayout(marker, model, left, plotWidth)) };
  }

  function renderAxisMarkerCompact(marker, y, selected) {
    const labelText = `${marker.label} ${marker.point ? fmtUsd(marker.low) : `${fmtUsd(marker.low)}—${fmtUsd(marker.high)}`}`;
    const className = ["map-axis-pick", "is-compact", marker.point ? "is-point" : "is-zone", selected ? "is-selected" : "", marker.comparable ? "is-comparable" : "is-nominal"].filter(Boolean).join(" ");
    const actualWidth = Math.abs(marker.x2 - marker.x1);
    const hitX = Math.min(marker.x1, marker.x2) - Math.max(0, (30 - actualWidth) / 2);
    const hitWidth = Math.max(30, actualWidth);
    const valueText = marker.point ? fmtNumber(marker.low, 0) : `${fmtNumber(marker.low, 0)}—${fmtNumber(marker.high, 0)}`;
    const visual = marker.point
      ? `<circle cx="${marker.x1.toFixed(2)}" cy="${y}" r="6"></circle><circle class="map-axis-hitbox" cx="${marker.x1.toFixed(2)}" cy="${y}" r="18"></circle>`
      : `<rect x="${Math.min(marker.x1, marker.x2).toFixed(2)}" y="${y - 7}" width="${actualWidth.toFixed(2)}" height="14" rx="2"></rect><rect class="map-axis-hitbox" x="${hitX.toFixed(2)}" y="${y - 17}" width="${hitWidth.toFixed(2)}" height="34" rx="4"></rect>`;
    const edge = marker.edge ? `<text x="${marker.edge === "left" ? 24 : 374}" y="${y - 14}" text-anchor="${marker.edge === "left" ? "start" : "end"}" class="map-axis-edge">${svgText(marker.edge === "left" ? "左侧越界" : "右侧越界")} ${svgText(valueText)}</text>` : "";
    return `<g class="${className}" tabindex="0" role="button" data-map-region-id="${escapeHtml(marker.id)}" aria-label="${escapeHtml(labelText)}">
      <title>${escapeHtml(`${labelText}；${marker.note || "来源口径未提供"}`)}</title>
      ${visual}
      ${edge}
    </g>`;
  }

  function compactRegionOptions(model) {
    const marketRows = model.markers.map((item) => ({
      id: item.id,
      title: item.label,
      value: item.point ? fmtUsd(item.low) : `${fmtUsd(item.low)} — ${fmtUsd(item.high)}`,
      note: item.note || label(item.familyTrack),
    }));
    const contractRows = model.contracts.flatMap((group) => group.refs.map((item) => ({
      id: item.id,
      title: `${group.side} ${roleShortText(item.role)}`,
      value: fmtUsd(item.low),
      note: "本单合同责任点",
    })));
    return [...contractRows, ...marketRows].filter((item) => item.id);
  }

  function renderCompactRegionSelector(model) {
    const rows = compactRegionOptions(model);
    if (!rows.length) return "";
    const currentId = state.selectedMapRegionId || "";
    return `<div class="map-axis-mobile-controls" aria-label="移动端价格轴读数">
      <label><span>选择区域</span><select class="map-region-select" data-map-region-select aria-label="选择价格轴区域">
        <option value="">未选区域</option>
        ${rows.map((row) => `<option value="${escapeHtml(row.id)}" ${row.id === currentId ? "selected" : ""}>${escapeHtml(`${row.title} · ${row.value}`)}</option>`).join("")}
      </select></label>
      <ul>${rows.slice(0, 8).map((row) => `<li><button type="button" data-map-region-id="${escapeHtml(row.id)}">${escapeHtml(row.title)}</button><span>${escapeHtml(row.value)}</span><small>${escapeHtml(row.note)}</small></li>`).join("")}</ul>
    </div>`;
  }

  function renderContractGroupCompact(group, y, selectedRegionId, model, left, plotWidth) {
    const compactGroup = contractGroupForLayout(group, model, left, plotWidth);
    const short = compactGroup.refs.find((ref) => String(ref.role).toUpperCase() === "SHORT_LEG");
    const long = compactGroup.refs.find((ref) => String(ref.role).toUpperCase() === "PROTECTIVE_LEG");
    const pointY = y + 39;
    const bandWidth = short && long ? Math.abs(short.x1 - long.x1) : 0;
    const bandHitX = short && long ? Math.min(short.x1, long.x1) - Math.max(0, (30 - bandWidth) / 2) : 0;
    const band = short && long ? `<rect class="map-contract-band ${compactGroup.active ? "is-active" : ""}" x="${Math.min(short.x1, long.x1).toFixed(2)}" y="${pointY - 6}" width="${bandWidth.toFixed(2)}" height="12" rx="2"></rect><rect class="map-contract-hitbox" x="${bandHitX.toFixed(2)}" y="${pointY - 17}" width="${Math.max(30, bandWidth).toFixed(2)}" height="34" rx="4"></rect>` : "";
    const labelLanes = new Map();
    compactGroup.refs.slice().sort((a, b) => a.x1 - b.x1).forEach((marker) => {
      const used = new Set();
      compactGroup.refs.forEach((other) => {
        if (other === marker) return;
        if (labelLanes.has(other.id) && Math.abs(other.x1 - marker.x1) < 140) used.add(labelLanes.get(other.id));
      });
      let lane = 0;
      while (used.has(lane)) lane += 1;
      labelLanes.set(marker.id, Math.min(lane, 3));
    });
    return `<g class="map-contract-group is-compact ${compactGroup.active ? "is-active" : ""}">
      <text x="24" y="${y}" class="map-axis-rowlabel">${svgText(`${compactGroup.side} 合同`)}</text>
      <text x="24" y="${y + 17}" class="map-axis-small">${svgText(`${compactGroup.sameCutoff ? "同MAP截止" : "冻结时点不同"} ${fmtTime(compactGroup.cutoff)}`)}</text>
      ${band}
      ${compactGroup.refs.map((marker) => {
        const selected = marker.id && marker.id === selectedRegionId;
        const role = roleShortText(marker.role);
        const upperRole = String(marker.role).toUpperCase();
        const symbol = upperRole === "NEAR_BREAKEVEN" ? "diamond" : upperRole === "PROTECTIVE_LEG" ? "square" : "circle";
        const shape = symbol === "diamond"
          ? `<path d="M${marker.x1.toFixed(2)},${pointY - 8} L${(marker.x1 + 7).toFixed(2)},${pointY} L${marker.x1.toFixed(2)},${pointY + 8} L${(marker.x1 - 7).toFixed(2)},${pointY} Z"></path><circle class="map-axis-hitbox" cx="${marker.x1.toFixed(2)}" cy="${pointY}" r="18"></circle>`
          : symbol === "square"
            ? `<rect x="${(marker.x1 - 6).toFixed(2)}" y="${pointY - 6}" width="12" height="12"></rect><circle class="map-axis-hitbox" cx="${marker.x1.toFixed(2)}" cy="${pointY}" r="18"></circle>`
            : `<circle cx="${marker.x1.toFixed(2)}" cy="${pointY}" r="6"></circle><circle class="map-axis-hitbox" cx="${marker.x1.toFixed(2)}" cy="${pointY}" r="18"></circle>`;
        const lane = labelLanes.get(marker.id) || 0;
        const labelY = pointY + 30 + lane * 22;
        const labelAnchor = marker.x1 < 100 ? "start" : marker.x1 > 300 ? "end" : "middle";
        return `<g class="map-axis-pick is-contract ${selected ? "is-selected" : ""}" tabindex="0" role="button" data-map-region-id="${escapeHtml(marker.id)}" aria-label="${escapeHtml(`${compactGroup.side} ${role} ${fmtUsd(marker.low)}`)}">
          <title>${escapeHtml(`${compactGroup.side} ${role} ${fmtUsd(marker.low)}；合同线不是独立市场确认`)}</title>
          ${shape}
          <path class="map-axis-leader" d="M${marker.x1.toFixed(2)},${pointY + 10} L${marker.x1.toFixed(2)},${labelY - 12}"></path>
          <text x="${marker.x1.toFixed(2)}" y="${labelY}" text-anchor="${labelAnchor}">${svgText(`${role} ${fmtNumber(marker.low, 0)}`)}</text>
        </g>`;
      }).join("")}
    </g>`;
  }

  function renderMapAxisSvg(model, compact = false) {
    if (compact) return renderMapAxisSvgCompact(model);
    const width = 1060;
    const left = 126;
    const plotWidth = 900;
    const tickStep = Math.max(500, Math.ceil(model.domain.width / 6 / 500) * 500);
    const firstTick = Math.ceil(model.domain.min / tickStep) * tickStep;
    const ticks = [];
    for (let price = firstTick; price <= model.domain.max + 1e-9; price += tickStep) ticks.push(price);
    const currentX = model.currentPrice === null ? null : model.toX(model.currentPrice, left, plotWidth);
    const selectedId = state.selectedMapRegionId;
    const trackDefs = [
      ["option", "期权结构"],
      ["acceptance", "历史接受区"],
      ["onchain", "链上成本"],
      ["other", "其他可比参照"],
    ];
    let nextTrackY = 83;
    const emptyTrackTitles = [];
    const tracks = trackDefs.map(([key, title]) => {
      const items = model.markers.filter((item) => item.familyTrack === key)
        .sort((a, b) => ((a.x1 + a.x2) / 2) - ((b.x1 + b.x2) / 2));
      if (!items.length) {
        emptyTrackTitles.push(title);
        return null;
      }
      const y = nextTrackY;
      nextTrackY += 59;
      let previousX = -Infinity;
      const withLane = items.map((item) => {
        const center = (item.x1 + item.x2) / 2;
        const lane = center - previousX < 86 ? 1 : 0;
        previousX = center;
        return { item, lane };
      });
      return { key, title, y, items: withLane };
    }).filter(Boolean);
    const emptyTrackY = emptyTrackTitles.length ? nextTrackY : null;
    if (emptyTrackTitles.length) nextTrackY += 38;
    const contractStartY = nextTrackY + 36;
    const contractRowHeight = 96;
    const svgHeight = Math.max(430, contractStartY + Math.max(1, model.contracts.length) * contractRowHeight + 72);
    return `<svg class="map-price-svg" viewBox="0 0 ${width} ${svgHeight}" role="img" aria-label="${escapeHtml(`BTC MAP 数值价格轴，线性刻度显示${model.currentLabel}、市场参照与保单合同位置`)}">
      <defs><pattern id="stripe" width="7" height="7" patternUnits="userSpaceOnUse"><path d="M0,7 L7,0" stroke="#aeb8c4" stroke-width="1"/></pattern></defs>
      <line class="map-axis-line" x1="${left}" x2="${left + plotWidth}" y1="${svgHeight - 48}" y2="${svgHeight - 48}"></line>
      ${ticks.map((price) => {
        const x = model.toX(price, left, plotWidth);
        return `<g><line class="map-axis-grid" x1="${x.toFixed(2)}" x2="${x.toFixed(2)}" y1="42" y2="${svgHeight - 48}"></line><text x="${x.toFixed(2)}" y="${svgHeight - 20}" text-anchor="middle" class="map-axis-tick">${svgText(fmtNumber(price, 0))}</text></g>`;
      }).join("")}
      <text x="${left + plotWidth}" y="${svgHeight - 5}" text-anchor="end" class="map-axis-note">BTC USD 标的价格 · 线性比例</text>
      ${currentX === null ? "" : `<line class="map-current-line" x1="${currentX.toFixed(2)}" x2="${currentX.toFixed(2)}" y1="30" y2="${svgHeight - 42}"></line><rect class="map-current-pill" x="${(currentX - 75).toFixed(2)}" y="7" width="150" height="26" rx="4"></rect><text x="${currentX.toFixed(2)}" y="25" text-anchor="middle" class="map-current-text">${svgText(model.currentLabel)} ${svgText(fmtNumber(model.currentPrice, 0))}</text>`}
      ${tracks.map((track) => `<g>
        <text x="18" y="${track.y - 20}" class="map-axis-rowlabel">${svgText(track.title)}</text>
        ${track.items.map(({ item, lane }) => renderAxisMarker(item, track.y, item.id === selectedId, lane)).join("")}
      </g>`).join("")}
      ${emptyTrackTitles.length ? `<g>
        <text x="18" y="${emptyTrackY - 14}" class="map-axis-rowlabel">市场参照缺口</text>
        <text x="${left}" y="${emptyTrackY + 6}" class="map-axis-empty">暂无合格可比参照：${svgText(emptyTrackTitles.join("、"))}</text>
      </g>` : ""}
      ${model.contracts.map((group, index) => renderContractGroup(group, contractStartY + index * contractRowHeight, selectedId)).join("")}
    </svg>`;
  }

  function renderMapAxisSvgCompact(model) {
    const width = 400;
    const left = 24;
    const plotWidth = 350;
    const selectedId = state.selectedMapRegionId;
    const tickCount = 4;
    const ticks = Array.from({ length: tickCount + 1 }, (_, index) => model.domain.min + (model.domain.width * index / tickCount));
    const currentX = model.currentPrice === null ? null : model.toX(model.currentPrice, left, plotWidth);
    const currentPillX = currentX === null ? null : Math.max(left, Math.min(currentX - 76, width - left - 152));
    const remappedMarkers = model.markers.map((item) => markerForLayout(item, model, left, plotWidth));
    const trackDefs = [
      ["option", "期权"],
      ["acceptance", "接受区"],
      ["onchain", "链上"],
      ["other", "其他"],
    ];
    let nextTrackY = 88;
    const tracks = trackDefs.map(([key, title]) => {
      const items = remappedMarkers.filter((item) => item.familyTrack === key)
        .sort((a, b) => ((a.x1 + a.x2) / 2) - ((b.x1 + b.x2) / 2));
      if (!items.length) return null;
      const y = nextTrackY;
      nextTrackY += 46;
      return { key, title, y, items };
    }).filter(Boolean);
    const contractStartY = nextTrackY + 42;
    const contractRowHeight = 154;
    const svgHeight = Math.max(360, contractStartY + Math.max(1, model.contracts.length) * contractRowHeight + 70);
    return `<svg class="map-price-svg is-compact" viewBox="0 0 ${width} ${svgHeight}" role="img" aria-label="${escapeHtml(`BTC MAP 移动端数值价格轴，线性刻度显示${model.currentLabel}与本单合同位置`)}">
      <defs><pattern id="stripeCompact" width="7" height="7" patternUnits="userSpaceOnUse"><path d="M0,7 L7,0" stroke="#aeb8c4" stroke-width="1"/></pattern></defs>
      <line class="map-axis-line" x1="${left}" x2="${left + plotWidth}" y1="${svgHeight - 45}" y2="${svgHeight - 45}"></line>
      ${ticks.map((price) => {
        const x = model.toX(price, left, plotWidth);
        return `<g><line class="map-axis-grid" x1="${x.toFixed(2)}" x2="${x.toFixed(2)}" y1="44" y2="${svgHeight - 45}"></line><text x="${x.toFixed(2)}" y="${svgHeight - 18}" text-anchor="middle" class="map-axis-tick">${svgText(fmtNumber(price, 0))}</text></g>`;
      }).join("")}
      <text x="${left}" y="24" class="map-axis-note">移动端主轴 · BTC USD 线性比例</text>
      ${currentX === null ? "" : `<line class="map-current-line" x1="${currentX.toFixed(2)}" x2="${currentX.toFixed(2)}" y1="38" y2="${svgHeight - 42}"></line><rect class="map-current-pill" x="${currentPillX.toFixed(2)}" y="31" width="152" height="24" rx="4"></rect><text x="${(currentPillX + 76).toFixed(2)}" y="48" text-anchor="middle" class="map-current-text">${svgText(model.currentLabel)} ${svgText(fmtNumber(model.currentPrice, 0))}</text>`}
      ${tracks.map((track) => `<g>
        <text x="24" y="${track.y - 15}" class="map-axis-rowlabel">${svgText(track.title)}</text>
        ${track.items.map((item) => renderAxisMarkerCompact(item, track.y, item.id === selectedId)).join("")}
      </g>`).join("")}
      ${model.contracts.map((group, index) => renderContractGroupCompact(group, contractStartY + index * contractRowHeight, selectedId, model, left, plotWidth)).join("")}
    </svg>${renderCompactRegionSelector(model)}`;
  }

  function renderMapAxisPriceReadout(model) {
    const groups = asArray(model.contracts).map(asObject).filter((group) => asArray(group.refs).length);
    if (!groups.length) return "";
    const roleOrder = { SHORT_LEG: 0, NEAR_BREAKEVEN: 1, PROTECTIVE_LEG: 2 };
    const rows = groups.map((group) => {
      const refs = asArray(group.refs).slice().sort((a, b) =>
        (roleOrder[String(a.role).toUpperCase()] ?? 9) - (roleOrder[String(b.role).toUpperCase()] ?? 9));
      return `<li class="${group.active ? "is-active" : ""}">
        <strong>${escapeHtml(`${label(group.side)}${group.active ? " 当前选择" : " 同版对照"}`)}</strong>
        <span>${refs.map((ref) => `${label(ref.role)} ${fmtUsd(ref.low)}`).map(escapeHtml).join(" / ")}</span>
      </li>`;
    }).join("");
    return `<div class="map-axis-readout" aria-label="合同价格完整读数">
      <section>
        <strong>合同价格读数</strong>
        <ul>${rows}</ul>
      </section>
    </div>`;
  }

  function renderAxisPolicyPositionSummary(context, model) {
    const currentLabel = model.currentLabel || "现价";
    const currentValue = fmtUsd(model.currentPrice);
    const map = asObject(context.map);
    const candidate = asObject(context.candidate);
    const snapshot = asObject(candidate.snapshot);
    const identities = [asObject(map.policy_link), asObject(mapPresentation(map).candidate_identity)];
    const candidateIds = identities.map((item) => item.candidate_id).filter(Boolean);
    const snapshotIds = identities.map((item) => item.snapshot_id).filter(Boolean);
    const bound = context.source === "candidate" && candidateIds.length > 0
      && candidateIds.every((id) => id === candidateId(candidate))
      && snapshotIds.every((id) => id === snapshot.snapshot_id);
    if (!context.candidate || !bound) {
      return `<section class="decision-position-summary" aria-label="当前位置摘要">
        <strong>当前位置</strong>
        <p class="muted">${context.candidate ? "尚无已核对的本单 MAP 位置摘要；此处只保留地图参考价，不套用 Put/Call 合同距离。" : "未选保单：价格轴只显示全局参考和当前价格，不套用 Put/Call 合同距离。"}</p>
        <dl><div><dt>${escapeHtml(currentLabel)}</dt><dd>${escapeHtml(currentValue)}</dd></div></dl>
      </section>`;
    }
    const brief = asObject(asObject(mapPresentation(context.map).decision_brief).policy_position);
    const checkpoints = asArray(brief.checkpoints).map(asObject).filter((item) => Object.keys(item).length);
    const summary = firstPresent(brief.summary_cn, brief.text_cn, "");
    const rows = checkpoints.map((item) => {
      const price = safeNumber(item.price_usd) !== null ? fmtUsd(item.price_usd) : "价格未提供";
      const distanceUsd = safeNumber(item.distance_usd) !== null ? `距${currentLabel} ${fmtSigned(item.distance_usd, 0)} USD` : "距离 USD 未提供";
      const distancePct = safeNumber(item.distance_pct) !== null ? fmtPct(item.distance_pct, 2) : "距离比例未提供";
      return `<div><dt>${escapeHtml(firstPresent(item.label_cn, item.label, "检查点"))}</dt><dd>${escapeHtml(`${price}；${distanceUsd}；${distancePct}`)}</dd></div>`;
    }).join("");
    return `<section class="decision-position-summary" aria-label="本单当前位置摘要">
      <strong>当前位置</strong>
      <p>${escapeHtml(summary || "尚未生成同版保单位置摘要；页面不从区域责任行补造本单距离。")}</p>
      <dl>
        <div><dt>${escapeHtml(currentLabel)}</dt><dd>${escapeHtml(currentValue)}</dd></div>
        ${rows || `<div><dt>检查点</dt><dd>服务端未提供检查点距离；页面不从区域责任行补造距离。</dd></div>`}
      </dl>
    </section>`;
  }

  function renderDecisionAxisLegend() {
    const items = [
      ["│", "当前 / 冻结价"],
      ["○—□", "本单两腿与合同间隔"],
      ["○", "可比市场点"],
      ["▱", "历史区域"],
      ["⋯", "辅助参考 · 折叠保留"],
    ];
    return `<ul class="decision-axis-legend" aria-label="价格轴图例">
      ${items.map(([title, text]) => `<li><span>${escapeHtml(title)}</span><small>${escapeHtml(text)}</small></li>`).join("")}
    </ul>`;
  }

  function renderNominalReferenceList(model, context) {
    const rows = [
      ...model.nominalReferences.map((item) => ({
        id: item.id,
        title: item.label,
        value: item.point ? fmtUsd(item.low) : `${fmtUsd(item.low)} — ${fmtUsd(item.high)}`,
        note: `${label(item.qualification)}；${item.note || "来源口径未提供"}`,
      })),
      ...model.offAxisContractGroups.map((group) => ({
        id: group.key,
        title: `${group.side} 对侧合同`,
        value: [
          group.refs.map((ref) => `${roleShortText(referenceRole(ref))} ${fmtUsd(refPrice(ref))}`).join(" / "),
          group.sameCutoff ? "" : `冻结时点 ${fmtTime(group.cutoff)}`,
        ].filter(Boolean).join("；"),
        note: group.sameCutoff
          ? "聚焦当前保单时位于轴域外；保留原始腿价，可切换该侧查看，不用边界夹点冒充价格。"
          : "对侧冻结时点不同；可切换查看，不在当前数值轴精确叠层。",
      })),
    ];
    if (!rows.length) return "";
    const titles = rows.slice(0, 3).map((row) => row.title).join("、");
    const rest = rows.length > 3 ? `等 ${rows.length} 项` : `${rows.length} 项`;
    return `<details class="map-details decision-nominal-details"${context ? mapDisclosureAttribute(context, "axis-nominal") : ""}>
      <summary><span>名义 / 异源 / 非同刻对照（${rows.length}项）</span><small>${escapeHtml(`${titles}${titles ? "，" : ""}${rest}`)}</small></summary>
      <div class="map-nominal-list" aria-label="名义异源与非同刻对照">
      <ul>${rows.map((row) => `<li>
        <button type="button" data-map-region-id="${escapeHtml(row.id)}">${escapeHtml(row.title)}</button>
        <span>${escapeHtml(row.value)}</span>
        <small>${escapeHtml(row.note)}</small>
      </li>`).join("")}</ul>
      </div>
    </details>`;
  }

  function renderReferenceAuditTable(map) {
    const refs = displayedReferences(map);
    if (!refs.length) return `<div class="empty">服务端未返回默认市场参照；无候选时仍应返回全局背景轴，当前只能显示缺口。</div>`;
    const rows = refs.map((ref) => {
      const family = referenceFamily(ref);
      const role = referenceRole(ref);
      const title = referenceDisplayTitle(ref);
      const note = referenceSourceNote(ref);
      const stateText = [
        label(firstPresent(ref.coordinate_qualification, ref.mapping_quality, ref.mapping_state, "CURRENT")),
        ref.eligible_for_display === false ? "不可作为默认参照" : "",
      ].filter(Boolean).join("；");
      const highlighted = ref.highlighted === true || asArray(map.policy_link && map.policy_link.highlighted_reference_ids).map(String).includes(String(refId(ref)));
      return `<tr class="${highlighted ? "is-highlighted" : ""}">
        <th scope="row"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(`${label(family)} · ${label(role)}`)}</span></th>
        <td>${escapeHtml(refRange(ref))}</td>
        <td>${escapeHtml(note || "来源口径未提供")}${renderReferenceSourceDetails(ref)}</td>
        <td>${escapeHtml(stateText)}</td>
      </tr>`;
    }).join("");
    return `<div class="map-table-wrap" role="region" aria-label="数值价格轴" tabindex="0">
      <table class="map-table">
        <caption>默认最多六个市场参照，按显示价排序。结构线、历史接受区、成本线与合同线分层阅读。</caption>
        <thead><tr><th scope="col">参照</th><th scope="col">显示价</th><th scope="col">原价 / 价系 / 期限</th><th scope="col">状态</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>${renderContractOverlay(map)}`;
  }

  function renderReferenceAxis(context) {
    const map = asObject(context.map);
    const model = buildMapAxisModel(context);
    const priceClock = `${model.current.basis}；源钟 ${fmtTime(model.current.observedAt)}；${label(model.current.source)}`;
    const selected = selectedPolicyLink(context);
    const identity = `${model.identity}；MAP 截止 ${fmtTime(model.cutoff)}；${context.label}`;
    return `<div class="map-axis-workbench" data-axis-mode="${escapeHtml(model.mode)}">
      <div class="map-axis-topline">
        <div>
          <strong>${escapeHtml(identity)}</strong>
          <p class="muted">${escapeHtml(`${model.currentLabel} ${fmtUsd(model.currentPrice)}；${priceClock}`)}</p>
        </div>
        <div class="action-row">
          <button type="button" class="map-axis-mode ${model.mode === "focus" ? "primary" : ""}" data-map-axis-mode="focus">本单＋关键参考</button>
          <button type="button" class="map-axis-mode ${model.mode === "all" ? "primary" : ""}" data-map-axis-mode="all">全部参考</button>
        </div>
      </div>
      ${renderAxisPolicyPositionSummary(context, model)}
      ${renderDecisionAxisLegend()}
      ${renderMapAxisSvg(model, typeof window !== "undefined" && safeNumber(window.innerWidth) !== null && safeNumber(window.innerWidth) <= 620)}
      ${renderMapAxisPriceReadout(model)}
      ${renderNominalReferenceList(model, context)}
      <p class="map-footnote">合同与当前所选保单同版；费用假设只影响到期 BE 与责任账。名义、异源或非同刻项目列在图下，不参与主价格轴精确共位。</p>
      <details class="map-details map-axis-audit"${mapDisclosureAttribute(context, "axis-audit")}>
        <summary>展开价格轴审计表</summary>
        ${renderReferenceAuditTable(map)}
      </details>
    </div>`;
  }

  if (typeof window !== "undefined") {
    window.__astraMapAxisV241 = {
      buildMapAxisModel,
      renderMapAxisSvg,
      referenceBounds,
      isComparableReference,
      renderMapBackground,
      referenceDisplayTitle,
      responseFactsForRegion,
      mapFactStateLabel,
      renderMapPolicyIdentity,
      renderMapFactSummary,
    };
  }

  function renderContractOverlay(map) {
    const refs = contractReferences(map);
    if (!refs.length) return "";
    return `<div class="map-contract-lines">
      <strong>合同线独立叠层</strong>
      <p class="muted">卖出腿、保护腿和近端 BE 不占默认六个市场参照名额，也不作为独立市场确认。</p>
      <ul>${refs.map((ref) => `<li><span>${escapeHtml(label(firstPresent(ref.role, ref.family)))}</span><strong>${escapeHtml(refRange(ref))}</strong></li>`).join("")}</ul>
    </div>`;
  }

  function renderMapResponses(map) {
    const events = asArray(map.response_events).map(asObject).filter((item) => Object.keys(item).length).slice(0, 4);
    if (!events.length) return `<div class="empty">尚无按本版区域记录的价格响应；不会用后来移动的线解释过去。</div>`;
    const refsById = referenceMapById(map);
    return `<ul class="map-event-list">${events.map((event) => {
      const ref = refsById.get(String(firstPresent(event.reference_id, event.region_id, ""))) || {};
      const strength = label(firstPresent(event.evidence_strength, event.proof_strength, event.coverage, event.method, event.state, "未定"));
      const region = firstPresent(event.region_label_cn, event.reference_label_cn, event.label_cn, referenceDisplayTitle(ref), "未命名区域");
      const price = firstPresent(event.price_usd, event.observed_price_usd, event.close_usd, event.price, event.close, event.last_price);
      const summary = firstPresent(event.summary_cn, event.interpretation_cn, label(firstPresent(event.event_type, event.type, "RESPONSE_EVENT")));
      return `<li>
        <strong>${escapeHtml(region)}</strong>
        <span>${escapeHtml(summary)}</span>
        <small>${escapeHtml(`价格 ${fmtUsd(price)}；时点 ${fmtTime(firstPresent(event.observed_at_ms, event.event_at_ms, event.closed_at_ms))}；证明强度 ${strength}`)}</small>
      </li>`;
    }).join("")}</ul>`;
  }

  function renderPolicyLiabilityRow(mapContext, row) {
    const ref = asObject(asArray(asObject(mapContext.map).reference_pool).find((item) => refId(asObject(item)) === row.region_id));
    const region = firstPresent(row.region_label_cn, row.region_cn, row.label_cn, ref.label_cn, label(firstPresent(row.role, ref.role, row.family, "区域")));
    const relationRaw = firstPresent(row.relationship_cn, row.liability_relation_cn, row.relation_to_short_long_be, row.position_vs_current, "未提供");
    const relation = Array.isArray(relationRaw) ? relationRaw.map((item) => label(item)).join(" / ") : label(relationRaw);
    const payout = firstPresent(row.payout_range_btc_cn, row.payout_btc_cn, row.payout_range_cn, row.payout_range_btc, row.terminal_payout_range);
    const net = firstPresent(row.static_net_result_range_btc_cn, row.static_net_btc_cn, row.net_result_range_cn, row.static_net_result_btc, row.static_net_range);
    const cost = asObject(row.cost_scope);
    const note = firstPresent(row.cost_identity_cn, row.compensation_identity_cn, row.m_ref_identity_cn, row.gap_cn, label(cost.title || ""));
    const scenario = label(firstPresent(row.terminal_scenario_basis, "未提供"));
    const limitations = asArray(row.limitations).map((item) => label(item)).filter(Boolean).join("；");
    const cutoffs = asObject(row.source_cutoffs);
    const cutoffText = [
      firstPresent(cutoffs.observation_end_ms, row.observation_end_ms) ? `来源截止 ${fmtTime(firstPresent(cutoffs.observation_end_ms, row.observation_end_ms))}` : "",
      firstPresent(cutoffs.known_at_ms, row.known_at_ms) ? `可得 ${fmtTime(firstPresent(cutoffs.known_at_ms, row.known_at_ms))}` : "",
    ].filter(Boolean).join("；");
    return `<tr class="${row.highlighted === true ? "is-highlighted" : ""}">
      <th scope="row"><strong>${escapeHtml(region)}</strong><span>${escapeHtml(firstPresent(row.price_range_cn, row.settlement_range_usd_cn, refRange(ref)))}</span></th>
      <td>${escapeHtml(relation)}</td>
      <td>${escapeHtml(fmtMapValue(payout) || "未提供")}</td>
      <td>${escapeHtml(fmtMapValue(net) || "未提供")}${note ? `<span>${escapeHtml(note)}</span>` : ""}</td>
      <td>${escapeHtml(scenario)}${limitations ? `<span>${escapeHtml(limitations)}</span>` : ""}${cutoffText ? `<span>${escapeHtml(cutoffText)}</span>` : ""}</td>
    </tr>`;
  }

  function renderPolicyLiabilityTable(mapContext, rows, caption, ariaLabel) {
    return `<div class="map-table-wrap" role="region" aria-label="${escapeHtml(ariaLabel)}" tabindex="0">
      <table class="map-table">
        <caption>${escapeHtml(caption)}</caption>
        <thead><tr><th scope="col">区域</th><th scope="col">责任关系</th><th scope="col">到期支付范围</th><th scope="col">静态净结果</th><th scope="col">场景身份</th></tr></thead>
        <tbody>${rows.map((row) => renderPolicyLiabilityRow(mapContext, row)).join("")}</tbody>
      </table>
    </div>`;
  }

  function renderPolicyRows(mapContext) {
    const policy = asObject(asObject(mapContext.map).policy_link);
    const rows = asArray(policy.region_liability_rows).map(asObject).filter((item) => Object.keys(item).length).slice(0, 3);
    const fullRows = asArray(policy.region_liability_full_rows).map(asObject).filter((item) => Object.keys(item).length);
    const fullCount = fullRows.length;
    if (!mapContext.candidate) {
      return `<div class="empty">未选保单：只显示全局 MAP，合同责任表保持空。</div>`;
    }
    if (!rows.length) {
      return `<div class="empty">${escapeHtml(mapContext.label)} 尚未返回同版区域责任表；页面不把上一份保单的含义套到当前候选。</div>`;
    }
    const main = renderPolicyLiabilityTable(
      mapContext,
      rows,
      "若官方交割价位于区内的合同支付与静态净结果；不是盘中触区盈亏、平仓或退出账。",
      "保单责任映射",
    );
    const full = fullCount > rows.length ? `<details class="map-details map-full-liability">
      <summary>展开完整责任池（${escapeHtml(String(fullCount))} 条）</summary>
      ${renderPolicyLiabilityTable(
        mapContext,
        fullRows,
        "完整责任池来自同版 policy_link.region_liability_full_rows；只展示后端已冻结行，不在前端复算。",
        "完整保单责任池",
      )}
    </details>` : "";
    return `${main}${full}`;
  }

  function selectedPolicySide(context) {
    if (!context.candidate) return "";
    return String(firstPresent(context.candidate.side, asObject(context.candidate.snapshot).side, context.label, "") ?? "");
  }

  function sideAdverseNote(item, context) {
    const side = selectedPolicySide(context).toLowerCase();
    if (!side) return "";
    const text = [
      item.title_cn,
      item.text_cn,
      item.basis_cn,
      item.source_state_cn,
    ].filter(Boolean).join(" ");
    if (/call/.test(side) && /(上行|偏多|上涨|买盘|顺风)/i.test(text)) {
      return "对卖 Call：上行或偏多背景会增加触及卖出腿的压力；这里只是来源意见，不是自动选边。";
    }
    if (/put/.test(side) && /(下行|偏空|下跌|卖盘|逆风|PUT_SIDE_CONTEXT|Put 侧背景)/i.test(text)) {
      return "对卖 Put：下行或偏空背景会增加触及卖出腿的压力；这里只是来源意见，不是自动选边。";
    }
    if (/call/.test(side) && /(CALL_SIDE_CONTEXT|Call 侧背景)/i.test(text)) {
      return "Call 侧背景只是来源侧别；没有独立价格倾向时，不能单独推出偏多、卖 Call 许可或选边。";
    }
    if (/put/.test(side) && /(PUT_SIDE_CONTEXT|Put 侧背景)/i.test(text)) {
      return "Put 侧背景只是来源侧别；没有独立价格倾向时，不能单独推出偏空、卖 Put 许可或选边。";
    }
    return "";
  }

  function briefNeedsQualification(text) {
    return /(缺|不足|不可|未|过期|失效|冲突|反证|阻断|不利|限制|降级|非当前|非实时|不支持|需|gap|missing|stale|conflict|block|adverse|unavailable|insufficient|limited|not current)/i
      .test(String(text || ""));
  }

  function sourceMethodLike(text) {
    return /(Farside|Bitview|Binance|FRED|ETF|BTCUSDT|U本位|净美元流量|资本化价格|资金费率|价格折算|非实买|source|market|measure|window|来源|市场|口径|时窗)/i
      .test(String(text || "")) && !briefNeedsQualification(text);
  }

  function briefQualificationText(item) {
    const candidates = [
      item.source_state_cn,
      item.qualification_cn,
      item.status_cn,
      item.gap_cn,
      item.limit_cn,
    ];
    const selected = [];
    for (const candidate of candidates) {
      const text = normalizeInlineText(candidate);
      if (!text) continue;
      if (sourceMethodLike(text)) continue;
      if (selected.includes(text)) continue;
      selected.push(text);
    }
    return joinTextParts(selected.slice(0, 2));
  }

  function renderBriefRows(items, emptyText, context = null) {
    const rows = asArray(items).map(asObject).filter((item) => Object.keys(item).length);
    if (!rows.length) return `<p class="muted">${escapeHtml(emptyText)}</p>`;
    return `<ul class="map-brief-list">${rows.map((item) => {
      const title = firstPresent(item.title_cn, item.kind_cn, item.label_cn, "未命名来源");
      const body = firstPresent(item.text_cn, item.summary_cn, item.reason_cn,
        item.basis_cn ? "服务端仅返回依据摘要；详细来源见下方背景明细。" : "未提供正文");
      const basis = briefQualificationText(item);
      const adverse = context ? sideAdverseNote(item, context) : "";
      return `<li>
        <strong>${escapeHtml(title)}</strong>
        <span>${escapeHtml(normalizeInlineText(body))}</span>
        ${basis ? `<small>${escapeHtml(basis)}</small>` : ""}
        ${adverse ? `<small class="warn-text">${escapeHtml(adverse)}</small>` : ""}
      </li>`;
    }).join("")}</ul>`;
  }

  function renderBriefConflicts(items) {
    const rows = asArray(items).map(asObject).filter((item) => Object.keys(item).length);
    const empty = "尚未生成矛盾与缺口摘要；无法把不同来源分歧写成结论。";
    const critical = rows.filter((item) => /(FMZ|SpaceGate|GEX|Gamma|KPF|原生空间门)/i.test(String(item.text_cn || "")));
    const mainRows = critical.length ? critical : rows.slice(0, 2);
    const otherRows = rows.filter((item) => !mainRows.includes(item));
    return `${renderBriefRows(mainRows, empty)}${otherRows.length
      ? `<details class="map-details"><summary>其他分歧与限制（${otherRows.length}项）</summary>${renderBriefRows(otherRows, empty)}</details>`
      : ""}`;
  }

  function renderPolicyPositionBrief(position, context) {
    if (!context.candidate) {
      return `<p class="muted">未选保单：当前只显示全局背景，不套用 Put/Call 合同金额解释。</p>`;
    }
    const object = asObject(position);
    if (!Object.keys(object).length) {
      return `<p class="muted">尚未生成同版保单位置摘要；页面不从旧摘要或区域首行补造本单位置。</p>`;
    }
    const currentLabel = context ? mapCurrentPriceLabel(context.map) : "现价";
    const checkpoints = asArray(object.checkpoints).map(asObject).filter((item) => Object.keys(item).length).slice(0, 4);
    return `<p>${escapeHtml(firstPresent(object.summary_cn, object.text_cn, "本单位置摘要未提供。"))}</p>${
      checkpoints.length ? `<dl class="map-brief-checkpoints">${checkpoints.map((item) => {
        const distanceParts = [
          safeNumber(item.price_usd) !== null ? fmtUsd(item.price_usd) : "",
          safeNumber(item.distance_usd) !== null ? `距${currentLabel} ${fmtSigned(item.distance_usd, 0)} USD` : "",
          safeNumber(item.distance_pct) !== null ? fmtPct(item.distance_pct, 2) : "",
        ].filter(Boolean).join("；");
        return `<div><dt>${escapeHtml(firstPresent(item.label_cn, item.label, "检查点"))}</dt><dd>${escapeHtml(distanceParts || "未提供")}</dd></div>`;
      }).join("")}</dl>` : `<p class="muted">服务端未提供价格检查点。</p>`
    }`;
  }

  function renderWatchBrief(watch, context) {
    if (!context.candidate) {
      return `<p class="muted">未选保单：当前没有绑定保单观察重点；不借入合成合同或上一张候选区域。</p>`;
    }
    const object = asObject(watch);
    if (!Object.keys(object).length) {
      return `<p class="muted">尚未生成优先观察重点；页面不把合同区域顺序写成市场优先观察区。</p>`;
    }
    const qualified = object.qualified === true;
    const regionId = String(firstPresent(object.primary_region_id, object.region_id, "") ?? "");
    return `<p>${escapeHtml(firstPresent(object.title_cn, object.status_cn, qualified ? "存在合格观察区" : "暂无合格观察区"))}</p>
      <dl class="map-brief-checkpoints">
        <div><dt>原因</dt><dd>${escapeHtml(firstPresent(object.reason_cn, "未提供"))}</dd></div>
        <div><dt>怎么核查</dt><dd>${escapeHtml(firstPresent(object.check_cn, "未提供"))}</dd></div>
        <div><dt>改变判断的事实</dt><dd>${escapeHtml(firstPresent(object.change_cn, "未提供"))}</dd></div>
      </dl>
      ${qualified && regionId ? `<button type="button" class="map-brief-region-button" data-map-region-id="${escapeHtml(regionId)}" aria-label="${escapeHtml(`定位优先观察区域 ${firstPresent(object.title_cn, regionId)}`)}">定位该区域</button>` : `<p class="muted">未返回可定位区域；不自动选择价格轴区域。</p>`}`;
  }

  function renderMapFactSummary(context) {
    const map = asObject(context.map);
    // Reads map.presentation.decision_brief; visible fallback text stays semantic Chinese.
    const brief = asObject(mapPresentation(map).decision_brief);
    const missing = !Object.keys(brief).length;
    return `<section class="map-fact-summary map-decision-brief" aria-label="MAP 四问摘要">
      <div class="map-brief-head">
        <strong>本轮四问摘要</strong>
        ${badge(missing ? "尚未生成四问摘要" : "服务端结构化摘要", missing ? "warn" : "blue")}
      </div>
      <div class="map-brief-grid">
        <article class="map-brief-card">
          <h3>供需与背景推动</h3>
          ${renderBriefRows(brief.pushes, "尚未生成背景推动摘要；页面只在下方展示原始背景数字。", context)}
        </article>
        <article class="map-brief-card">
          <h3>主要矛盾和缺口</h3>
          ${renderBriefConflicts(brief.conflicts)}
        </article>
        <article class="map-brief-card">
          <h3>本单所在位置</h3>
          ${renderPolicyPositionBrief(brief.policy_position, context)}
        </article>
        <article class="map-brief-card">
          <h3>优先观察及原因</h3>
          ${renderWatchBrief(brief.watch, context)}
        </article>
      </div>
      <p class="muted">本栏只复述服务端同版摘要与缺口；不生成概率、评分、EV 排名、交易许可或新 LLM 结论。</p>
    </section>`;
  }

  function frozenRemainingText(expiryMs, cutoffMs) {
    const expiry = safeNumber(expiryMs);
    const cutoff = safeNumber(cutoffMs);
    if (expiry === null || cutoff === null) return "缺口";
    const remaining = expiry - cutoff;
    return remaining > 0 ? fmtMsAge(remaining) : "已到期或非正";
  }

  function reviewBrief(candidate) {
    const snapshot = asObject(candidate && candidate.snapshot);
    const review = asObject(firstPresent(candidate && candidate.review, snapshot.review, snapshot.llm_review, {}));
    const status = firstPresent(review.status, review.state, review.result, "未发生");
    const at = firstPresent(review.reviewed_at_ms, review.reviewed_at, review.created_at_ms);
    return {
      value: label(status),
      note: at ? `时间 ${fmtTime(at)}` : "未返回评审时间",
    };
  }

  function renderMapPolicyIdentity(context, selectedItem = null) {
    const map = asObject(context.map);
    const identity = asObject(mapPresentation(map).candidate_identity);
    const current = mapCurrentPrice(map);
    if (!context.candidate) {
      return `<section class="map-policy-identity is-unselected" aria-label="MAP 当前选择">
        <div class="map-policy-identity-head">
          <strong>未选保单</strong>
          <p class="muted">选择 Put 或 Call 保单，查看本单合同位置、补偿与责任映射。</p>
        </div>
        <div class="metric-grid">
          ${metric(mapCurrentPriceLabel(map), fmtUsd(current.price), `${current.basis}；源钟 ${fmtTime(current.observedAt)}；${label(current.source)}`)}
          ${metric("选择提示", "先选择 Put / Call", "选择只改变本单身份条与责任映射，不改写源事实")}
        </div>
      </section>`;
    }
    const candidate = selectedItem || context.candidate;
    const side = label(firstPresent(candidate.side, asObject(candidate.snapshot).side, context.label, "保单"));
    const shortStrike = identity.short_strike_usd;
    const longStrike = identity.long_strike_usd;
    const expiry = identity.expiry_ms;
    const cutoff = firstPresent(map.cutoff_at_ms, map.cutoff_at, identity.reference_time_ms);
    const review = reviewBrief(candidate);
    const mRef = firstPresent(identity.M_ref_btc, identity.m_ref_btc);
    return `<section class="map-policy-identity" aria-label="选定保单身份">
      <div class="map-policy-identity-head">
        <strong>${escapeHtml(`${side} 保单身份`)}</strong>
        <p class="muted">两腿、补偿与费用假设属于同一冻结版本；评审保留原时间。</p>
      </div>
      <div class="metric-grid">
        ${metric(`卖${side} Ks`, fmtUsd(shortStrike), "卖出腿行权价")}
        ${metric(`买${side} Kl`, fmtUsd(longStrike), "保护腿行权价")}
        ${metric("数量", fmtBtc(identity.quantity_btc), "合约数量")}
        ${metric("到期", fmtTime(expiry), `冻结时点剩余 ${frozenRemainingText(expiry, cutoff)}`)}
        ${metric("冻结现价", fmtUsd(identity.current_price_usd), `冻结时点 ${fmtTime(identity.reference_time_ms)}`)}
        ${metric("净信用（入场费后）", fmtBtc(identity.net_credit_after_entry_fee_btc), "服务端已扣双腿入场理论费用")}
        ${safeNumber(mRef) !== null ? metric("参考补偿余量", fmtBtc(mRef), "服务端冻结补偿余量") : ""}
        ${metric("近端 BE", fmtUsd(identity.near_breakeven_usd), "来自服务端合格 BE 字段")}
        ${metric("最近评审", review.value, review.note)}
      </div>
    </section>`;
  }

  function rowRegionId(row) {
    return String(firstPresent(row.region_id, row.reference_id, row.id, ""));
  }

  function selectedPolicyRegion(context) {
    const map = asObject(context.map);
    const policy = asObject(map.policy_link);
    const fullRows = asArray(policy.region_liability_full_rows).map(asObject).filter((item) => Object.keys(item).length);
    const contextKey = mapSelectionContextKey(context);
    if (!fullRows.length) {
      state.selectedMapRegionId = "";
      state.selectedMapRegionContextKey = "";
      return { row: null, fullRows, selectedWithoutRow: false };
    }
    if (!contextKey || state.selectedMapRegionContextKey !== contextKey) {
      state.selectedMapRegionId = "";
      state.selectedMapRegionContextKey = contextKey;
    }
    const requested = String(state.selectedMapRegionId || "");
    let row = requested ? fullRows.find((item) => rowRegionId(item) === requested) : null;
    if (requested && row) return { row, fullRows, selectedWithoutRow: false, missingDefault: false };
    if (requested && mapHasRegionReference(map, requested)) {
      return { row: null, fullRows, selectedWithoutRow: true, missingDefault: false };
    }
    if (requested) state.selectedMapRegionId = "";
    const recommended = recommendedPolicyRegionId(map, fullRows);
    if (recommended) {
      row = fullRows.find((item) => rowRegionId(item) === recommended) || null;
      if (row) {
        state.selectedMapRegionId = recommended;
        state.selectedMapRegionContextKey = contextKey;
        return { row, fullRows, selectedWithoutRow: false, missingDefault: false };
      }
    }
    state.selectedMapRegionId = "";
    state.selectedMapRegionContextKey = contextKey;
    return { row: null, fullRows, selectedWithoutRow: false, missingDefault: true };
  }

  function recommendedPolicyRegionId(map, fullRows) {
    const watch = asObject(asObject(mapPresentation(map).decision_brief).watch);
    if (watch.qualified === false) return "";
    const requested = String(firstPresent(watch.primary_region_id, watch.region_id, "") ?? "");
    if (!requested) return "";
    return fullRows.some((item) => rowRegionId(item) === requested) ? requested : "";
  }

  function mapHasRegionReference(map, regionId) {
    const id = String(regionId || "");
    if (!id) return false;
    return asArray(map.reference_pool).map(asObject).some((ref) => String(refId(ref)) === id)
      || displayedReferences(map).some((ref) => String(refId(ref)) === id);
  }

  function responseFactsForRegion(map, row) {
    const regionId = rowRegionId(row);
    const responseRows = asArray(mapPresentation(map).response_by_region).map(asObject);
    const responseRow = responseRows.find((item) => String(firstPresent(item.region_id, item.reference_id, "")) === regionId);
    if (responseRow) {
      const events = asArray(responseRow.events).map(asObject).filter((item) => Object.keys(item).length);
      const normalized = events.map((event) => ({
        ...event,
        summary_cn: firstPresent(event.event_cn, event.summary_cn, responseRow.summary_cn),
        price: firstPresent(event.price, event.price_usd),
        observed_at_ms: firstPresent(event.time_ms, event.observed_at_ms),
      })).filter(isVerifiableResponseEvent);
      if (normalized.length) return normalized;
      if (responseRow.summary_cn) {
        const summaryOnly = { summary_cn: responseRow.summary_cn, observed_at_ms: firstPresent(responseRow.time_ms, responseRow.updated_at_ms) };
        return isVerifiableResponseEvent(summaryOnly) ? [summaryOnly] : [];
      }
    }
    const direct = asArray(row.response_facts).map(asObject).filter((item) => Object.keys(item).length);
    const directEvents = direct.filter(isVerifiableResponseEvent);
    if (directEvents.length) return directEvents;
    return asArray(map.response_events).map(asObject).filter((event) =>
      String(firstPresent(event.reference_id, event.region_id, "")) === regionId).filter(isVerifiableResponseEvent);
  }

  function isVerifiableResponseEvent(event) {
    const summary = String(firstPresent(event.event_cn, event.summary_cn, event.interpretation_cn, event.event_type, event.type, "") ?? "");
    if (/尚无可验证区域响应|暂无可验证|暂无路径|仅位置|没有响应事实|无可验证/.test(summary)) return false;
    const at = firstPresent(event.time_ms, event.observed_at_ms, event.event_at_ms, event.closed_at_ms);
    const marker = firstPresent(event.event_type, event.type, event.method, event.coverage, event.evidence_strength, event.proof_strength);
    return at !== undefined || marker !== undefined;
  }

  function renderResponseFact(event) {
    const price = firstPresent(event.price_usd, event.observed_price_usd, event.close_usd, event.price, event.close, event.last_price);
    const at = firstPresent(event.time_ms, event.observed_at_ms, event.event_at_ms, event.closed_at_ms);
    const method = label(firstPresent(event.method, event.coverage, event.evidence_strength, event.proof_strength, "未定"));
    const summary = firstPresent(event.event_cn, event.summary_cn, event.interpretation_cn, label(firstPresent(event.event_type, event.type, "位置样本")));
    return `<li><strong>${escapeHtml(summary)}</strong><span>${escapeHtml(`价格 ${fmtUsd(price)}；时点 ${fmtTime(at)}；方法 ${method}`)}</span></li>`;
  }

  function renderSelectedRegion(context) {
    const map = asObject(context.map);
    if (!context.candidate) return `<div class="empty">先选择 Put 或 Call 保单，才显示区域责任。全局 MAP 不套用上一张保单的金额解释。</div>`;
    const { row, selectedWithoutRow, missingDefault } = selectedPolicyRegion(context);
    if (!row && selectedWithoutRow) return `<div class="empty">选中的是合同线或非责任区域；同版完整责任池没有对应行。页面不前端复算支付或净结果，请选择价格区域查看责任。</div>`;
    if (!row && missingDefault) return `<div class="empty">${escapeHtml(context.label)} 未返回有效优先观察区域；请点选价格轴或责任池区域查看同版责任。页面不把完整责任池首行假称为优先。</div>`;
    if (!row) return `<div class="empty">${escapeHtml(context.label)} 尚未返回完整责任池；页面不前端复算支付或净结果。</div>`;
    const refsById = referenceMapById(map);
    const ref = refsById.get(rowRegionId(row)) || {};
    const name = firstPresent(row.region_label_cn, row.region_cn, row.label_cn, referenceDisplayTitle(ref), "未命名区域");
    const relationRaw = firstPresent(row.relationship_cn, row.liability_relation_cn, row.relation_to_short_long_be, "未提供");
    const relation = Array.isArray(relationRaw) ? relationRaw.map((item) => label(item)).join(" / ") : label(relationRaw);
    const payout = firstPresent(row.payout_range_btc_cn, row.payout_btc_cn, row.payout_range_cn, row.payout_range_btc, row.terminal_payout_range);
    const net = firstPresent(row.static_net_result_range_btc_cn, row.static_net_btc_cn, row.net_result_range_cn, row.static_net_result_btc, row.static_net_range);
    const responseFacts = responseFactsForRegion(map, row).slice(-3);
    const scenario = label(firstPresent(row.terminal_scenario_basis, "未提供"));
    const limitations = asArray(row.limitations).map((item) => label(item)).filter(Boolean).join("；");
    return `<div class="map-region-grid">
      <article class="map-region-card">
        <div class="map-region-title">
          <div>
            <strong>${escapeHtml(name)}</strong>
            <p class="muted">${escapeHtml(firstPresent(row.price_range_cn, row.settlement_range_usd_cn, refRange(ref)))}</p>
          </div>
          ${badge(label(firstPresent(row.coordinate_qualification, ref.coordinate_qualification, "区域")), row.coordinate_qualification === "COMPARABLE" ? "good" : "warn")}
        </div>
        <dl class="map-region-kv">
          <div><dt>当前价格关系</dt><dd>${escapeHtml(label(firstPresent(row.position_vs_current, "未提供")))}</dd></div>
          <div><dt>对本单</dt><dd>${escapeHtml(relation)}</dd></div>
          <div><dt>假设到期赔付</dt><dd>${escapeHtml(fmtMapValue(payout) || "未提供")}</dd></div>
          <div><dt>静态净结果</dt><dd>${escapeHtml(fmtMapValue(net) || "未提供")}</dd></div>
          <div><dt>场景身份</dt><dd>${escapeHtml(scenario)}</dd></div>
          <div><dt>限制</dt><dd>${escapeHtml(limitations || "未记录额外限制")}</dd></div>
        </dl>
        <p class="map-footnote">金额只来自同版完整责任池；触区不是立即盈亏、买回成本或执行许可。</p>
      </article>
      <article class="map-region-card">
        <div class="map-region-title"><strong>响应与复核重点</strong>${badge(responseFacts.length ? "有可核对响应记录" : "仅位置/暂无路径", responseFacts.length ? "blue" : "warn")}</div>
        ${responseFacts.length ? `<ul class="map-region-events">${responseFacts.map(renderResponseFact).join("")}</ul>` : `<p class="muted">仅位置/暂无路径：尚无可证明的进入、跨区、返回过程。</p>`}
        <p class="callout">复核重点：检查该区域是否改变冻结保单责任解释；没有响应事实时，不写成“承接有效”。</p>
      </article>
    </div>`;
  }

  function renderMapReviewDelta(map, suppliedDelta = null) {
    const delta = asObject(firstPresent(suppliedDelta, map.manual_review_delta, map.llm_delta, map.review_delta, {}));
    const items = [
      ...asArray(delta.differences_cn),
      ...asArray(delta.facts_changed_cn),
      ...asArray(delta.gaps_cn),
    ].filter(Boolean).slice(0, 4);
    if (!Object.keys(delta).length && !items.length) {
      return `<div class="callout"><strong>冻结评审差异</strong><p>服务端未返回最近冻结 LLM 与当前 MAP 的差异；页面不会因此补发 LLM。</p></div>`;
    }
    const summary = firstPresent(delta.summary_cn, delta.status_cn, "当前 MAP 与最近冻结评审的差异如下。");
    return `<div class="callout warn"><strong>冻结评审差异</strong><p>${escapeHtml(summary)}</p>${
      items.length ? `<ul>${items.map((item) => `<li>${escapeHtml(fmtMapValue(item))}</li>`).join("")}</ul>` : ""
    }<p class="muted">${escapeHtml(`最近冻结评审 ${fmtTime(firstPresent(delta.reviewed_at_ms, delta.frozen_at_ms))}；只提示差异，不自动请求新 LLM。`)}</p></div>`;
  }

  function renderBtcMap() {
    const target = $("#btcMap");
    if (!target) return;
    const expanded = new Set(Array.from(
      target.querySelectorAll("details[open][data-map-disclosure-key]"),
      (item) => item.dataset.mapDisclosureKey,
    ).filter(Boolean));
    if (!state.apiOnline || !state.workbench) {
      target.innerHTML = empty("工作台服务尚未返回 BTC MAP；页面不会用历史卡拼接当前 MAP。");
      return;
    }
    const context = selectedMapContext();
    const map = asObject(context.map);
    const options = context.options;
    if (!context.candidate) {
      state.selectedMapRegionId = "";
      state.selectedMapRegionContextKey = "";
    }
    if (!Object.keys(map).length) {
      state.selectedMapRegionId = "";
      state.selectedMapRegionContextKey = "";
      target.innerHTML = empty("服务端尚未返回 btc_map 附属对象；无法展示四行背景、价格轴和保单责任映射。");
      return;
    }
    const optionHtml = [
      `<option value="">未选保单</option>`,
      ...options.map((item) => `<option value="${escapeHtml(item.key)}" ${state.selectedMapCandidateKey === item.key ? "selected" : ""}>${escapeHtml(`${item.side} 保单${item.hasMap ? "" : "（使用全局 MAP）"}`)}</option>`),
    ].join("");
    const cutoff = firstPresent(map.cutoff_at_ms, map.cutoff_at, map.updated_at_ms);
    const mode = label(firstPresent(map.interpretation_mode, "FACTS_UNTIL_MANUAL_REVIEW"));
    const sourceText = context.source === "candidate" ? "候选同版 MAP" : (context.source === "global_fallback" ? "候选未带专项 MAP，当前显示全局 MAP" : "全局 MAP");
    const selectedItem = asArray(state.workbench.candidates).find((item) =>
      asObject(item.snapshot).candidate_id === asObject(map.policy_link).candidate_id);
    const currentUsage = asObject(selectedItem ? selectedItem.btc_map_current_usage : state.workbench.btc_map_current_usage);
    if (context.candidate) selectedPolicyRegion(context);
    const qualificationNote = currentUsage.status === "REQUIRES_RECHECK"
      ? `<p class="map-note">来源已变更或失效。下表${selectedItem ? "仍是候选冻结时的事实与账，不能当当前有效参照" : "按当前缓存重新展示"}；冷却期间也立即提示，不自动补发评审。</p>` : "";
    target.innerHTML = `
      <div class="map-toolbar">
        <div>
          <strong>${escapeHtml(`${mapIdentityText(map)} · ${sourceText}`)}</strong>
          <p class="muted">${escapeHtml(`截止 ${fmtTime(cutoff)}；${mode}；只读观察与反证，不代表交易许可。`)}</p>
        </div>
        <label>
          <span>保单责任映射</span>
          <select id="mapCandidateSelect" aria-label="选择 BTC MAP 保单责任映射">${optionHtml}</select>
        </label>
      </div>
      <details class="map-details map-policy-context"${mapDisclosureAttribute(context, "policy-context")}>
        <summary>查看保单身份与冻结补偿</summary>
        ${renderMapPolicyIdentity(context, selectedItem)}
      </details>
      ${qualificationNote}
      <div class="map-layout">
        <section class="map-panel" aria-labelledby="mapAxisTitle">
          <div class="map-panel-head"><h3 id="mapAxisTitle">数值价格轴</h3><p>线性数值坐标；显示价、原价、价系和期限分开，合成/回放/当前资料固定标识。</p></div>
          ${renderReferenceAxis(context)}
        </section>
        ${renderMapFactSummary(context)}
        <section class="map-panel" aria-labelledby="mapPolicyTitle">
          <div class="map-panel-head"><h3 id="mapPolicyTitle">区域响应与保单责任关联</h3><p>点选图中区域后，责任金额只读取同版完整责任池；不自动选边、不重算金额。</p></div>
          ${renderSelectedRegion(context)}
          <details class="map-details map-policy-audit"${mapDisclosureAttribute(context, "policy-audit")}>
            <summary>展开保单责任审计表</summary>
            ${renderPolicyRows(context)}
          </details>
          <details class="map-details map-response-audit"${mapDisclosureAttribute(context, "response-audit")}>
            <summary>展开区域响应记录</summary>
            ${renderMapResponses(map)}
          </details>
        </section>
        <details class="map-panel decision-background"${mapDisclosureAttribute(context, "research-background")}>
          <summary id="mapBackgroundTitle">研究背景 · 配置、库存、融资与外部条件</summary>
          <p class="muted">四行背景完整保留，各自查看来源时钟、完整数值与缺口。</p>
          ${renderMapBackground(map)}
        </details>
      </div>
      ${renderMapReviewDelta(map, selectedItem && selectedItem.btc_map_review_delta)}`;
    target.querySelectorAll("details[data-map-disclosure-key]").forEach((item) => {
      item.open = expanded.has(item.dataset.mapDisclosureKey);
    });
    const select = $("#mapCandidateSelect");
    if (select) {
      select.addEventListener("change", (event) => {
        state.selectedMapCandidateKey = event.target.value;
        state.selectedMapRegionId = "";
        state.selectedMapRegionContextKey = "";
        renderBtcMap();
        renderManualReviewState();
      });
    }
    target.querySelectorAll("[data-map-region-select]").forEach((selectEl) => {
      selectEl.addEventListener("change", (event) => {
        state.selectedMapRegionId = event.target.value || "";
        state.selectedMapRegionContextKey = mapSelectionContextKey(selectedMapContext());
        renderBtcMap();
      });
    });
    target.querySelectorAll("[data-map-region-id]").forEach((item) => {
      const choose = () => {
        state.selectedMapRegionId = item.getAttribute("data-map-region-id") || "";
        state.selectedMapRegionContextKey = mapSelectionContextKey(selectedMapContext());
        renderBtcMap();
      };
      item.addEventListener("click", choose);
      item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          choose();
        }
      });
    });
    target.querySelectorAll("[data-map-axis-mode]").forEach((item) => {
      item.addEventListener("click", () => {
        state.mapAxisMode = item.getAttribute("data-map-axis-mode") || "focus";
        renderBtcMap();
      });
    });
  }

  function renderStatus() {
    const target = $("#apiStatus");
    if (!target) return;
    target.classList.remove("is-live", "is-partial", "is-offline", "is-preview");
    if (state.loading) {
      target.textContent = "正在读取当前工作台。";
      return;
    }
    if (state.apiOnline) {
      const gate = gateObject();
      const preview = isSyntheticPreview();
      const replay = isReplayPreview();
      const factCurrent = !preview && gate.fmz_fact_status === "current" && gate.source === "producer_native_space_gate";
      const factReadable = asObject(asObject(state.workbench.market_context).data_engine_usage).can_use === true;
      target.classList.add((preview || replay) ? "is-preview" : (factCurrent ? "is-live" : "is-partial"));
      const updated = fmtTime(firstPresent(state.workbench.updated_at_ms, state.workbench.updated_at, state.lastLoadedAt));
      if (preview) {
        target.textContent = `本地模拟场景已连接：${updated}。`;
        return;
      }
      if (replay) {
        target.textContent = `本机冻结回放已连接：${updated}。`;
        return;
      }
      target.textContent = factCurrent
        ? `工作台 API 与 FMZ 当前事实已连接：${updated}。60 秒轮询，不自动调用 LLM。`
        : (factReadable
          ? `FMZ 原生截面已读取，结构用途仍有限制：${updated}。可继续观测已有资料；60 秒轮询，不自动调用 LLM。`
          : `工作台 API 已连接，FMZ 当前事实或原生 SpaceGate 有缺口：${updated}。已有合同与其他资料按各自时点阅读；60 秒轮询，不自动调用 LLM。`);
      return;
    }
    target.classList.add("is-offline");
    target.textContent = `当前数据不可得／仅能查看本地历史。工作台服务未返回可用实时资料：${state.lastError || "未知错误"}。`;
  }

  function renderCurrentConclusion() {
    const target = $("#currentConclusion");
    if (!target) return;
    if (!state.apiOnline || !state.workbench) {
      target.innerHTML = `<div class="gate-headline">
        <div>
          <div class="gate-state is-bad">当前数据不可得</div>
          <p class="muted">本页不会用旧静态卡代替当前工作台。下方历史区只用于打开原始资料。</p>
        </div>
      </div>`;
      return;
    }
    const gate = gateObject();
    const tone = gateTone(gate);
    const candidates = sortedCandidates();
    const enabledCount = candidates.filter((candidate) => reviewGate(candidate).enabled === true).length;
    const preview = isSyntheticPreview();
    const factCurrent = !preview && gate.fmz_fact_status === "current" && gateSource(gate) === "producer_native_space_gate";
    const factReadable = asObject(asObject(state.workbench.market_context).data_engine_usage).can_use === true;
    let headline = "当前不准入";
    let basis = "结构门或部分来源条件未满足；已有事实、变化及合同责任仍可阅读，限制只适用于依赖它的结论。";
    if (candidates.some(candidate => Object.keys(asObject(candidate.snapshot)).length)) headline = "结构未准入，已有资料可观测";
    if (preview) {
      headline = gateStateText(gate).includes("不准入") || gate.state === "STRUCTURE_BLOCKED" ? "模拟结构门关闭：仅研究诊断" : "本地模拟候选：可进行受限分析";
      basis = "模拟输入用于比较本单适配、补偿与等待取舍；数值可核算，生产采用与执行许可保持关闭。";
    } else if (tone === "good" && enabledCount > 0) {
      headline = "可进入人工锁定评审";
      basis = "结构门打开，且至少一侧候选由服务端判定可手动综合评审。";
    } else if (tone === "good") {
      headline = "结构可研究，保单账仍未放行";
      basis = "空间门打开，但 μ_ref、M_ref、报价、冷却或其他服务端条件尚未允许主评审。";
    }
    const candidateSummary = candidates.length
      ? candidates.map((candidate) => {
        const snapshot = asObject(candidate.snapshot);
        const contract = asObject(snapshot.contract);
        const economics = asObject(snapshot.economics);
        const side = label(firstPresent(candidate.side, snapshot.side, "候选"));
        const legs = [contract.short_instrument, contract.long_instrument].filter(Boolean).join(" / ") || "合约未提供";
        const c = economics.premium_spread_btc;
        const mRef = firstPresent(economics.m_ref_btc, economics.reference_margin_btc);
        const status = reviewGate(candidate).enabled === true ? (preview ? "可演示" : "可评审") : (preview ? "只读比较" : (asObject(candidate.observation_review).enabled === true ? "可观测解释" : "主评审未准入"));
        const values = [`信用 ${fmtBtc(c)}`, ...(safeNumber(mRef) !== null ? [`参考余量 ${fmtBtc(mRef)}`] : []), status];
        return `<p><strong>${escapeHtml(side)}</strong> ${escapeHtml(legs)}<br>${escapeHtml(values.join("；"))}</p>`;
      }).join("")
      : `<p>未返回 Put/Call 候选</p>`;
    target.innerHTML = `
      <div class="gate-headline">
        <div>
          <div class="gate-state is-${tone}">${escapeHtml(headline)}</div>
          <p class="muted">${escapeHtml(basis)}</p>
        </div>
        <div class="badge-row">
          ${badge(preview ? "本地模拟场景" : (factCurrent ? "FMZ 当前事实可用" : (factReadable ? "FMZ 截面可读 · 结构用途受限" : "FMZ 当前事实缺口")), preview ? "warn" : (factCurrent ? "good" : "warn"))}
          ${badge(enabledCount ? `${enabledCount} 侧${preview ? "可演示" : "可评审"}` : (preview && candidates.length ? `${candidates.length} 侧只读比较` : `暂无${preview ? "模拟" : "可评审"}候选`), preview ? "warn" : (enabledCount ? "good" : "warn"))}
        </div>
      </div>
      <div class="current-candidate-summary" aria-label="当前候选摘要">${candidateSummary}</div>
      <p class="muted">${escapeHtml(`${gateStateText(gate)}；${fmtTime(firstPresent(state.workbench.updated_at_ms, state.workbench.updated_at, state.lastLoadedAt))}；两侧并列`)}。</p>
      <p><strong>下一步</strong> ${escapeHtml(preview ? "选择模拟保单，核对两问" : (enabledCount ? "人工决定是否锁定候选评审" : "先看本单事实与两问，按需展开变化、反证和来源；主评审仍按原条件开放"))}。</p>
      <details><summary>查看资料时间与结构来源</summary><div class="metric-grid">
        ${metric("资料时间", fmtTime(firstPresent(state.workbench.updated_at_ms, state.workbench.updated_at, state.lastLoadedAt)), "来自工作台 API，不来自历史卡")}
        ${metric("空间门", gateStateText(gate), label(gateSource(gate)))}
      </div></details>`;
  }

  function renderGate() {
    const target = $("#spaceGate");
    const badges = $("#gateBadges");
    if (!target || !badges) return;
    const gate = gateObject();
    const spaceEvidence = renderSpaceEvidence();
    if (!Object.keys(gate).length) {
      badges.innerHTML = badge("缺原生 SpaceGate", "bad");
      target.innerHTML = `<div class="gate-headline">
        <div>
          <div class="gate-state is-bad">空间约束：当前不准入</div>
          <p class="muted">当前工作台没有 FMZ 原生 SpaceGate。页面不从旧静态卡、GGR 文案或历史 NR 事件推导准入。</p>
        </div>
      </div><div class="space-gate-grid">
        ${spaceEvidence}
        <article class="fact-card is-gap">
          <div class="fact-card-head"><h3>结构门核对</h3><p>Anchor 与 NetGamma 的原生状态必须同时可核。</p></div>
          <div class="fact-card-body"><div class="callout warn">等待工作台服务返回当前结构门。</div></div>
        </article>
      </div>`;
      return;
    }
    const tone = gateTone(gate);
    const anchor = asObject(firstPresent(gate.anchor, gate.anchor_gate, {}));
    const gamma = asObject(firstPresent(gate.net_gamma, gate.gamma, gate.gamma_gate, {}));
    const observed = firstPresent(gate.observed_at_ms, gate.observed_at, gate.updated_at_ms);
    const since = firstPresent(gate.since_ts_ms, gate.since_ms, gate.since);
    const reasons = gateReasons(gate);
    const source = gateSource(gate);
    const preview = isSyntheticPreview();
    const gateChecks = `<article class="fact-card gate-checks">
      <div class="fact-card-head"><h3>结构门核对</h3><p>同一空间事实的有效性与阈值校验；通过只表示可继续核算保单。</p></div>
      <div class="fact-card-body"><ul class="fact-lines">
        ${factDetailLine("Anchor", `${fmtNumber(firstPresent(anchor.score, anchor.value), 2)} / 100`, `状态 ${label(firstPresent(anchor.state, anchor.status))}；门槛 60；源时点 ${fmtTime(anchor.source_ts_ms)}`)}
        ${factDetailLine("Anchor 新鲜度", label(firstPresent(anchor.freshness, anchor.data_state, anchor.status)), `资料年龄 ${fmtMsAge(firstPresent(anchor.age_ms, anchor.data_age_ms))}`)}
        ${factDetailLine("NetGamma（门槛输入）", `${fmtNumber(firstPresent(gamma.value, gamma.net_gamma, gamma.net_gamma_notional, gamma.net_gamma_notional_usd), 2)} ${firstPresent(gamma.unit, "单位未提供")}`, `符号 ${label(firstPresent(gamma.sign, gamma.net_gex_sign))}`)}
        ${factDetailLine("Gamma 来源与状态", label(firstPresent(gamma.source, gamma.source_ref, "未提供")), `有效 ${label(firstPresent(gamma.valid, "unknown"))}；过渡 ${label(firstPresent(gamma.transition, false))}；冲突 ${label(firstPresent(gamma.conflict, false))}`)}
        ${factDetailLine("当前资料时间", fmtTime(firstPresent(state.workbench.updated_at_ms, state.workbench.updated_at, observed)), `状态时点 ${fmtTime(observed)}；状态开始 ${fmtTime(since)}`)}
        ${factDetailLine("空间门来源", preview ? "本地模拟场景（非当前事实）" : label(source), preview ? "模拟字段只用于预览" : (source === "producer_native_space_gate" ? "FMZ 原生字段" : "缺原生则按阻断展示"))}
      </ul></div>
    </article>`;
    badges.innerHTML = [
      badge(preview ? "本地模拟 SpaceGate" : label(source), preview ? "warn" : (source === "producer_native_space_gate" ? "good" : "bad")),
      badge(`状态时点 ${fmtTime(observed)}`, "blue"),
      badge(`状态开始 ${fmtTime(since)}`, "blue"),
    ].join("");
    target.innerHTML = `
      <div class="gate-headline">
        <div>
          <div class="gate-state is-${tone}">${escapeHtml(gateStateText(gate))}</div>
          <p class="muted">${escapeHtml(preview ? "本地模拟空间门只用于页面演示；不是 FMZ 当前事实，不代表真 LLM 支持、账户允许或经济有效。" : "结构门只是 G1：可继续核算候选，不代表补偿充分、LLM 支持、账户允许或经济有效。")}</p>
        </div>
        <div class="badge-row">
          ${badge(`Anchor ${anchor.ok === true ? "通过" : "未通过"}`, anchor.ok === true ? "good" : "bad")}
          ${badge(`NetGamma ${gamma.ok === true ? "通过" : "未通过"}`, gamma.ok === true ? "good" : "bad")}
        </div>
      </div>
      <div class="space-gate-grid">
        ${spaceEvidence}
        ${gateChecks}
      </div>
      <div class="callout ${tone === "good" ? "good" : "bad"}" style="margin-top: 14px;">
        ${escapeHtml(reasons.length ? reasons.map((item) => label(item)).join("；") : "未记录阻断理由。若状态为可建立候选，也仍需进入具体保单核算。")}
      </div>`;
  }

  function sortedCandidates() {
    const candidates = asArray(state.workbench && state.workbench.candidates);
    const sideRank = { put: 1, PUT: 1, call: 2, CALL: 2 };
    return candidates.slice().sort((a, b) => (sideRank[a.side] || 9) - (sideRank[b.side] || 9));
  }

  function candidateId(candidate) {
    const snapshot = asObject(candidate.snapshot);
    return firstPresent(candidate.candidate_id, snapshot.candidate_id, snapshot.id, candidate.id, "");
  }

  function candidateReview(candidate) {
    const snapshot = asObject(candidate.snapshot);
    return asObject(firstPresent(candidate.review, snapshot.review, snapshot.llm_review, {}));
  }

  function candidateReviewDisplay(candidate) {
    return asObject(candidate.review_display);
  }

  function reviewResultPayload(result) {
    const object = asObject(result);
    const payload = asObject(firstPresent(object.review, object.receipt, {}));
    return Object.keys(payload).length ? payload : object;
  }

  function reviewDisplayBinding(result, payload = {}) {
    return asObject(firstPresent(asObject(result).display_binding, asObject(payload).display_binding, {}));
  }

  function currentEvidenceInputHash(candidate) {
    const snapshot = asObject(candidate.snapshot);
    const evidence = asObject(candidate.wpem_evidence);
    const reviewInput = asObject(firstPresent(candidate.review_input, snapshot.review_input, evidence.review_input, {}));
    const review = candidateReview(candidate);
    return firstPresent(
      candidate.evidence_input_hash,
      snapshot.evidence_input_hash,
      reviewInput.evidence_input_hash,
      evidence.evidence_input_hash,
      review.evidence_input_hash,
      "",
    );
  }

  function reviewReceiptIdentity(result, payload = {}) {
    const object = asObject(result);
    const review = asObject(payload);
    const binding = reviewDisplayBinding(object, review);
    return {
      receiptId: firstPresent(object.receipt_id, review.receipt_id, binding.receipt_id, ""),
      candidateId: firstPresent(binding.candidate_id, object.candidate_id, review.candidate_id, ""),
      snapshotId: firstPresent(binding.snapshot_id, object.snapshot_id, review.reviewed_snapshot_id, review.snapshot_id, review.reviewed_snapshot, ""),
    };
  }

  function sameReviewReceipt(left, leftPayload, right, rightPayload) {
    const leftId = reviewReceiptIdentity(left, leftPayload);
    const rightId = reviewReceiptIdentity(right, rightPayload);
    if (leftId.receiptId && rightId.receiptId) return leftId.receiptId === rightId.receiptId;
    return Boolean(leftId.candidateId && rightId.candidateId && leftId.snapshotId && rightId.snapshotId
      && leftId.candidateId === rightId.candidateId && leftId.snapshotId === rightId.snapshotId);
  }

  function reviewMatchesCurrentCandidate(candidate, result, payload, { strictDisplay = false } = {}) {
    const snapshot = asObject(candidate.snapshot);
    const currentCandidateId = firstPresent(candidateId(candidate), snapshot.candidate_id);
    const currentSnapshotId = snapshot.snapshot_id;
    const resultCandidateId = firstPresent(asObject(result).candidate_id, asObject(payload).candidate_id);
    const resultSnapshotId = firstPresent(
      asObject(result).snapshot_id,
      asObject(payload).reviewed_snapshot_id,
      asObject(payload).snapshot_id,
      asObject(payload).reviewed_snapshot,
    );
    const binding = reviewDisplayBinding(result, payload);
    if (strictDisplay) {
      if (!Object.keys(binding).length) return false;
      if (!binding.candidate_id || !binding.snapshot_id) return false;
      if (binding.candidate_id !== currentCandidateId || binding.snapshot_id !== currentSnapshotId) return false;
      if (binding.same_input !== true) return false;
      const currentHash = currentEvidenceInputHash(candidate);
      if (binding.evidence_input_hash && currentHash && binding.evidence_input_hash !== currentHash) return false;
      return true;
    }
    if (!resultSnapshotId || resultSnapshotId !== currentSnapshotId) return false;
    if (resultCandidateId && currentCandidateId && resultCandidateId !== currentCandidateId) return false;
    return true;
  }

  function reviewDisplayRelation(result, payload = {}) {
    const object = asObject(result);
    const review = asObject(payload);
    if (!Object.keys(object).length && !Object.keys(review).length) return { relation: "none", reason_cn: "" };
    let historical = null;
    sortedCandidates().forEach((candidate) => {
      const display = candidateReviewDisplay(candidate);
      const current = asObject(display.current);
      const currentPayload = reviewResultPayload(current);
      if (!historical && Object.keys(current).length
        && sameReviewReceipt(object, review, current, currentPayload)
        && reviewMatchesCurrentCandidate(candidate, current, currentPayload, { strictDisplay: true })) {
        historical = {
          relation: "current",
          candidate,
          reason_cn: firstPresent(display.reason_cn, current.reason_cn, currentPayload.reason_cn, "当前输入严格匹配。"),
        };
        return;
      }
      const old = asObject(display.historical);
      const oldPayload = reviewResultPayload(old);
      if (!historical && Object.keys(old).length && sameReviewReceipt(object, review, old, oldPayload)) {
        historical = {
          relation: "historical",
          candidate,
          reason_cn: reviewReceiptIdentity(old, oldPayload).snapshotId !== asObject(candidate.snapshot).snapshot_id
            ? "当前 snapshot 已更新；旧回执只解释此前冻结资料，不能回填当前。"
            : firstPresent(display.reason_cn, old.reason_cn, oldPayload.reason_cn, "当前输入身份已变化；旧回执只作历史参考。"),
        };
      }
    });
    return historical || { relation: "unbound", reason_cn: "未被当前 review_display 标为同版 current。" };
  }

  function reviewStatusInfo(result, payload = {}) {
    const outer = firstPresent(asObject(result).status, asObject(result).state, "");
    const inner = firstPresent(asObject(payload).status, asObject(payload).state, "");
    const accepted = ["OK", "attached", "SIMULATED", "RESEARCH_OK"];
    const outerFailed = Boolean(outer && !accepted.includes(outer));
    return {
      status: firstPresent(outer, inner, "未提供"),
      outer,
      inner,
      outerFailed,
      accepted: !outerFailed && (accepted.includes(outer) || (!outer && accepted.includes(inner))),
      researchOk: !outerFailed && (outer === "RESEARCH_OK" || inner === "RESEARCH_OK"),
    };
  }

  function displayReviewValidity(candidate) {
    const candidateValidity = asObject(candidate.review_validity);
    const reviewValidity = asObject(candidateReview(candidate).review_validity);
    const merged = { ...candidateValidity, ...reviewValidity };
    ["status", "state", "checked_at_ms", "checked_at", "checked_at_utc", "reason", "reason_cn"].forEach((key) => {
      if (candidateValidity[key] !== undefined && candidateValidity[key] !== null && candidateValidity[key] !== "") merged[key] = candidateValidity[key];
    });
    return merged;
  }

  function candidateDisclosureKey(candidate) {
    const snapshot = asObject(candidate.snapshot);
    const id = candidateId(candidate);
    const snapshotId = firstPresent(snapshot.snapshot_id, "");
    return id && snapshotId ? `${id}::${snapshotId}::candidate-details` : "";
  }

  function reviewGate(candidate) {
    return asObject(candidate.review_gate);
  }

  function candidateField(candidate, paths) {
    const snapshot = asObject(candidate.snapshot);
    return valueFrom({ candidate, snapshot }, paths);
  }

  function renderReadingGuidance(candidate) {
    const guidance = asObject(candidate.reading_guidance);
    const notes = asArray(guidance.notes).filter((item) => item && typeof item.text_cn === "string");
    if (!notes.length) return "";
    const identity = { current: "同截面只读提示", synthetic: "模拟保单阅读提示", background: "背景资料阅读提示", gap: "缺口下的阅读提示" };
    return `<div class="reading-guidance callout"><strong>${escapeHtml(identity[guidance.status] || "保单阅读提示")}</strong>
      <ul>${notes.map((item) => `<li>${escapeHtml(item.text_cn)}</li>`).join("")}</ul>
      <p class="muted">按服务端已有账与同源证据解释；不生成统计数值、选侧结论或交易许可。</p></div>`;
  }

  function sameCandidateCurrentReview(candidate) {
    const snapshot = asObject(candidate.snapshot);
    const display = candidateReviewDisplay(candidate);
    if (Object.prototype.hasOwnProperty.call(candidate, "review_display") || Object.keys(display).length) {
      const result = asObject(display.current);
      const review = reviewResultPayload(result);
      const q1 = asObject(review.q1);
      const q2 = asObject(review.q2);
      const status = firstPresent(result.status, review.status, review.state, "");
      if (!Object.keys(q1).length || !Object.keys(q2).length) return {};
      if (!["OK", "attached", "SIMULATED", "RESEARCH_OK"].includes(status)) return {};
      if (!reviewMatchesCurrentCandidate(candidate, result, review, { strictDisplay: true })) return {};
      const binding = reviewDisplayBinding(result, review);
      return {
        ...review,
        status,
        display_binding: binding,
        display_reason_cn: firstPresent(display.reason_cn, result.reason_cn, review.reason_cn, ""),
        display_data_asof_ms: firstPresent(display.data_asof_ms, result.data_asof_ms, review.data_asof_ms, ""),
      };
    }
    const review = candidateReview(candidate);
    const validity = displayReviewValidity(candidate);
    const q1 = asObject(review.q1);
    const q2 = asObject(review.q2);
    if (!Object.keys(q1).length || !Object.keys(q2).length) return {};
    const reviewedSnapshotId = firstPresent(review.reviewed_snapshot_id, review.snapshot_id, review.reviewed_snapshot);
    const currentByValidity = validity.status === "CURRENT";
    const sameSnapshot = reviewedSnapshotId && reviewedSnapshotId === snapshot.snapshot_id;
    const status = firstPresent(review.status, review.state, "attached");
    if (status === "RESEARCH_OK" || review.production_attach_allowed === false) return {};
    const usableStatus = ["OK", "attached", "SIMULATED"].includes(status);
    return currentByValidity && sameSnapshot && usableStatus ? review : {};
  }

  function compactTextItems(value) {
    if (Array.isArray(value)) {
      return value.map((item) => {
        if (typeof item === "string") return trimSentenceEdge(item);
        const object = asObject(item);
        return trimSentenceEdge(firstPresent(object.text_cn, object.summary_cn, object.reason_cn, object.label_cn, item));
      }).filter(Boolean);
    }
    return splitReadableParts(value);
  }

  function concreteWpemLimitItems(value) {
    return compactTextItems(value).filter((item) => {
      const text = String(item || "").trim();
      if (!text) return false;
      if (/^(none|null|not_available|not_assessed|CURRENT|STALE|NOT_RUN|INSUFFICIENT_WINDOW|NO_STRICT_DELTA)$/i.test(text)) return false;
      if (/^(无|暂无|未提供|不适用|未评估|没有)$/i.test(text)) return false;
      return true;
    });
  }

  function renderQuestionSourceTitle(title, fixedTitle) {
    const text = trimSentenceEdge(title);
    const normalized = text.replace(/\s+/g, "");
    const fixed = trimSentenceEdge(fixedTitle).replace(/\s+/g, "");
    if (!text || normalized === fixed) return "";
    return `<p class="muted">${escapeHtml(text)}</p>`;
  }

  const Q1_DECISION_TITLE = "Q1 · 当前相对适配在哪里，主要瑕疵是什么？";
  const Q2_DECISION_TITLE = "Q2 · 当前补偿是否值得交换这些风险？";

  function renderReviewBindingMeta(candidate, review) {
    const binding = reviewDisplayBinding(review, review);
    if (binding.same_input === false) return `<p class="muted review-binding-meta">这份解释不是同版输入；只在审计层保留。</p>`;
    return `<p class="muted review-binding-meta">同版输入已核对；冻结时间、回执时间和 hash 在审计层。</p>`;
  }

  function wpemSummaryStatusText(summary) {
    return [
      summary.path_classification ? `路径 ${label(summary.path_classification)}` : "",
      summary.window_status ? `窗口 ${label(summary.window_status)}` : "",
      summary.delta_status ? `增量 ${label(summary.delta_status)}` : "",
    ].filter(Boolean).join("；");
  }

  function releaseComponentLabel(value, index, keyHint = "", options = {}) {
    const raw = options.raw === true;
    const object = asObject(value);
    if (Object.keys(object).length) {
      const name = firstPresent(object.name, object.component, object.id, object.role, keyHint, `component_${index + 1}`);
      const version = firstPresent(object.candidate_version, object.version, object.schema_or_protocol, object.release_id);
      const sourceHash = firstPresent(object.source_hash, object.hash, object.sha256, object.source_sha256, object.manifest_hash);
      const status = firstPresent(object.status, "");
      if (raw) {
        return [
          String(name),
          version ? `candidate_version=${version}` : "",
          sourceHash ? `source_hash=${sourceHash}` : "",
          object.source_of_truth ? `source=${object.source_of_truth}` : "",
          status ? `status=${status}` : "",
        ].filter(Boolean).join(" ");
      }
      const marker = firstPresent(version, sourceHash, status);
      return [marker ? `${name}=${shortId(marker, 10)}` : String(name), status && status !== marker ? label(status) : ""].filter(Boolean).join(" ");
    }
    const scalar = trimSentenceEdge(value);
    if (!scalar) return "";
    return keyHint ? `${keyHint}=${shortId(scalar, 10)}` : scalar;
  }

  function releaseComponentLabels(components, options = {}) {
    if (Array.isArray(components)) return components.map((item, index) => releaseComponentLabel(item, index, "", options)).filter(Boolean);
    const object = asObject(components);
    if (Object.keys(object).length) {
      return Object.entries(object).map(([key, value], index) => releaseComponentLabel(value, index, key, options)).filter(Boolean);
    }
    const scalar = trimSentenceEdge(components);
    return scalar ? [scalar] : [];
  }

  function workbenchReleaseIdentity() {
    const workbench = asObject(state.workbench);
    const view = asObject(workbench.view);
    return asObject(firstPresent(workbench.release_identity, view.release_identity, {}));
  }

  function auditValue(value, options = {}) {
    const raw = options.raw === true;
    if (value === undefined || value === null || value === "") return "";
    if (typeof value === "boolean") return label(value);
    if (typeof value === "number") return String(value);
    if (typeof value === "string") return raw ? value : (value.length >= 32 && /^[0-9a-f]+$/i.test(value) ? shortId(value, 12) : label(value));
    if (Array.isArray(value)) {
      const rendered = value.map((item) => auditValue(item, options)).filter(Boolean);
      return (raw ? rendered : rendered.slice(0, 6)).join("；");
    }
    const object = asObject(value);
    const rendered = Object.entries(object).filter(([key]) => {
      if (!raw) return true;
      return !/(^|_)(point_refs|price_sequence|raw_source|source_bytes|private_blob|private_blobs|proof_bytes|proof|payload|raw_blob|raw_bytes)($|_)/i.test(key);
    }).map(([key, item]) => {
      const rendered = auditValue(item, options);
      return rendered ? `${key}=${rendered}` : "";
    }).filter(Boolean);
    return (raw ? rendered : rendered.slice(0, 6)).join("；");
  }

  function auditLine(labelText, value, note = "", options = {}) {
    const rendered = auditValue(value, options);
    if (!rendered) return "";
    return `<li><span>${escapeHtml(labelText)}</span><strong>${escapeHtml(rendered)}</strong>${note ? `<small>${escapeHtml(note)}</small>` : ""}</li>`;
  }

  function auditBlock(title, lines) {
    const body = lines.filter(Boolean).join("");
    if (!body) return "";
    return `<div class="wpem-audit-block"><p><strong>${escapeHtml(title)}</strong></p><ul class="wpem-audit-list">${body}</ul></div>`;
  }

  function coverageItems(candidateWindow, sourceBindings) {
    const sourceWindow = asObject(asObject(sourceBindings).window_evidence);
    const candidateSummary = firstPresent(candidateWindow.coverage_summary, candidateWindow.coverage);
    const sourceSummary = firstPresent(sourceWindow.coverage_summary, sourceWindow.coverage);
    const summary = firstPresent(candidateSummary, sourceSummary, {});
    const object = asObject(summary);
    if (Array.isArray(object.windows)) {
      return object.windows.map((item) => ({
        ...asObject(item),
        _schema: object.schema,
        _source_field: object.source_field,
        _policy: object.policy,
      })).filter((item) => Object.keys(item).length > 3);
    }
    if (Array.isArray(summary)) return summary.map(asObject).filter((item) => Object.keys(item).length);
    if (!Object.keys(object).length) return [];
    return [object];
  }

  function renderCoverageAudit(candidateWindow, sourceBindings) {
    const rows = coverageItems(candidateWindow, sourceBindings).map((item, index) => {
      const requested = item.requested_window_ms;
      const labelText = safeNumber(requested) !== null ? fmtMsAge(requested) : firstPresent(item.evidence_kind, `coverage_${index + 1}`);
      const start = item.coverage_start_ms;
      const end = item.coverage_end_ms;
      const cutoff = item.cutoff_ms;
      const span = item.actual_coverage_ms;
      const count = item.point_count;
      const qualified = item.qualified_points;
      const nativeQualified = item.native_qualified_points;
      const maxGap = item.max_gap_ms;
      const status = item.status;
      return auditLine(
        labelText,
        status,
        [
          start || end ? `${fmtTime(start)} → ${fmtTime(end)}` : "",
          cutoff ? `cutoff ${fmtTime(cutoff)}` : "",
          safeNumber(span) !== null ? `实际跨度 ${fmtMsAge(span)}` : "",
          safeNumber(count) !== null ? `点数 ${fmtNumber(count, 0)}` : "",
          safeNumber(qualified) !== null ? `合格点 ${fmtNumber(qualified, 0)}` : "",
          safeNumber(nativeQualified) !== null ? `原生合格点 ${fmtNumber(nativeQualified, 0)}` : "",
          safeNumber(maxGap) !== null ? `max gap ${fmtMsAge(maxGap)}` : "",
          item.evidence_kind ? `kind ${label(item.evidence_kind)}` : "",
          item._source_field ? `source ${item._source_field}` : "",
        ].filter(Boolean).join("；"),
      );
    });
    return auditBlock("15m / 1h 覆盖诊断", rows);
  }

  function renderWpemWindowReadout(candidateWindow, sourceBindings) {
    const rows = coverageItems(candidateWindow, sourceBindings).map((item) => {
      const requested = safeNumber(item.requested_window_ms);
      const title = requested === 900000 ? "15m" : (requested === 3600000 ? "1h" : (requested !== null ? fmtMsAge(requested) : "窗口"));
      const status = label(firstPresent(item.status, asObject(candidateWindow.qualification).status));
      const synthetic = /synthetic/i.test(String(firstPresent(item.evidence_kind, item.status, ""))) ? "本地模拟窗口" : "";
      return `${title} ${status}${synthetic && !status.includes("模拟") ? `（${synthetic}）` : ""}`;
    }).filter(Boolean);
    if (rows.length) return rows.slice(0, 2).join("；");
    const fallback = firstPresent(asObject(candidateWindow.qualification).status, candidateWindow.window_status);
    return fallback ? label(fallback) : "";
  }

  function renderCrossSectionRows(crossSection) {
    const rows = asArray(crossSection.cross_section_rows).map((row, index) => {
      const object = asObject(row);
      const role = firstPresent(object.role, object.row_role, object.row_type, `row_${index + 1}`);
      const blocked = firstPresent(object.blocked_reasons, object.blocked_reason, object.excluded_reason, object.exclusion_reason, object.comparability_reason, object.reason);
      const note = [
        object.candidate_id ? `candidate ${object.candidate_id}` : "",
        object.snapshot_id ? `snapshot ${object.snapshot_id}` : "",
        object.decision_asof_ms ? `asof ${fmtTime(object.decision_asof_ms)}` : "",
        object.packet_hash ? `packet ${object.packet_hash}` : "",
        object.comparability ? `comparability ${object.comparability}` : "",
        object.same_decision_asof !== undefined ? `same_asof ${label(object.same_decision_asof)}` : "",
        object.not_used_for_window !== undefined ? `not_used_for_window ${label(object.not_used_for_window)}` : "",
        object.not_ranked_or_scored !== undefined ? `not_ranked_or_scored ${label(object.not_ranked_or_scored)}` : "",
        blocked ? `blocked/excluded ${auditValue(blocked, { raw: true })}` : "",
      ].filter(Boolean).join("；");
      return auditLine(`截面行 ${index + 1}`, role, note, { raw: true });
    });
    return auditBlock("截面行身份与可比性", rows);
  }

  function renderWpemEvidenceAudit(candidate, evidence, summary, candidateWindow, sourceBindings) {
    const snapshot = asObject(candidate.snapshot);
    const crossSection = asObject(evidence.current_cross_section);
    const ldp = asObject(candidateWindow.LevelDeltaPath);
    const level = asObject(ldp.level);
    const delta = asObject(ldp.delta);
    const path = asObject(ldp.path);
    const responsibility = asObject(ldp.responsibility);
    const dependency = asObject(candidateWindow.dependency_diagnostics);
    const breaks = asObject(candidateWindow.source_method_breaks);
    const qualification = asObject(candidateWindow.qualification);
    const rows = [
      auditLine("候选", firstPresent(evidence.candidate_id, candidateId(candidate)), `snapshot ${firstPresent(evidence.snapshot_id, snapshot.snapshot_id)}`, { raw: true }),
      auditLine("截面对照", firstPresent(crossSection.cross_section_hash, crossSection.raw_cross_section_hash), `${fmtNumber(asArray(crossSection.cross_section_rows).length, 0)} 行；${auditValue(crossSection.source_kind)}`, { raw: true }),
      auditLine("Level", level, "来自 same_candidate_window.LevelDeltaPath.level", { raw: true }),
      auditLine("Delta", delta, "来自 same_candidate_window.LevelDeltaPath.delta", { raw: true }),
      auditLine("Path", path, "来自 same_candidate_window.LevelDeltaPath.path", { raw: true }),
      auditLine("责任关系", responsibility, "来自 same_candidate_window.LevelDeltaPath.responsibility", { raw: true }),
      auditLine("窗口资格", qualification, "只描述资料完整性和用途资格", { raw: true }),
      auditLine("窗口/增量状态", wpemSummaryStatusText(summary), "来自 wpem_summary"),
    ];
    return [
      auditBlock("WPEM 截面、Level / Delta / Path", rows),
      renderCrossSectionRows(crossSection),
      auditBlock("依赖与断点", [
        auditLine("依赖诊断", dependency, "同源读取与独立更新只作审计，不重复计票", { raw: true }),
        auditLine("source/method break", breaks, "断点不拼接趋势或候选轨迹", { raw: true }),
        auditLine("限制文本", summary.breaks_cn, "已有中文限制；主卡只显示具体限制"),
      ]),
    ].join("");
  }

  function renderWpemBindingAudit(candidate, evidence, validity, riskStatus, sourceBindings) {
    const snapshot = asObject(candidate.snapshot);
    const review = candidateReview(candidate);
    const candidateWindow = asObject(evidence.same_candidate_window);
    const catalog = asObject(evidence.counter_catalog_gap_reassess);
    const release = workbenchReleaseIdentity();
    const sourceRecords = firstPresent(sourceBindings.source_records, sourceBindings.source_record_ids, asObject(evidence.current_cross_section).source_records);
    const manifestHashes = firstPresent(sourceBindings.source_manifest_hashes, asObject(evidence.current_cross_section).source_manifest_hashes);
    const sourceWindow = asObject(sourceBindings.window_evidence);
    const reviewCatalogHash = firstPresent(review.claim_catalog_hash, validity.claim_catalog_hash);
    const sourceCatalogHash = firstPresent(catalog.source_catalog_hash, catalog.catalog_hash, evidence.claim_catalog_hash);
    const bindingWindowHash = validity.window_evidence_hash;
    const currentWindowHash = firstPresent(candidateWindow.source_window_evidence_hash, sourceWindow.window_evidence_hash);
    const bindingReviewHash = validity.review_evidence_hash;
    const currentReviewHash = firstPresent(evidence.evidence_hash, evidence.review_evidence_hash);
    return auditBlock("候选、回执与来源绑定", [
      auditLine("Release", firstPresent(release.release_id, release.status, release.manifest_hash), `manifest ${release.manifest_hash || ""}`, { raw: true }),
      auditLine("Candidate", firstPresent(evidence.candidate_id, candidateId(candidate)), `snapshot ${firstPresent(evidence.snapshot_id, snapshot.snapshot_id)}`, { raw: true }),
      auditLine("Quote", validity.quote_hash, `quote clock ${fmtTime(validity.quote_timestamp_ms)}`, { raw: true }),
      auditLine("Risk", validity.risk_hash, auditValue(firstPresent(riskStatus.mu_M_null_reason, riskStatus.terminal_status)), { raw: true }),
      auditLine("Review binding window", bindingWindowHash, "来自 attached review.review_validity.window_evidence_hash", { raw: true }),
      auditLine("Current window evidence", currentWindowHash, "来自 current same_candidate_window.source_window_evidence_hash", { raw: true }),
      auditLine("Review binding evidence", bindingReviewHash, "来自 attached review.review_validity.review_evidence_hash", { raw: true }),
      auditLine("Current review evidence", currentReviewHash, "来自 current wpem_evidence.evidence_hash", { raw: true }),
      auditLine("Projection", validity.projection_hash, auditValue(validity.review_input_version, { raw: true }), { raw: true }),
      auditLine("Protocol / model", [validity.llm_protocol, validity.model, review.response_protocol, review.requested_model], "600 秒冷却不延长有效期", { raw: true }),
      auditLine("Review catalog", reviewCatalogHash, "来自 normalized review.claim_catalog_hash", { raw: true }),
      auditLine("Source catalog", sourceCatalogHash, auditValue(firstPresent(catalog.source_response_protocol, catalog.status, catalog.gap_reason)), { raw: true }),
      auditLine("Source records", sourceRecords, "只显示来源身份摘要，不公开原生全文或 proof bytes", { raw: true }),
      auditLine("Source manifests", manifestHashes, "", { raw: true }),
    ]);
  }

  function renderWpemSummary(candidate, renderedReview = {}) {
    const summary = asObject(candidate.wpem_summary);
    if (!Object.keys(summary).length) return "";
    const shown = shownQuestionTexts(candidate, renderedReview);
    const breaks = concreteWpemLimitItems(summary.breaks_cn).filter(item => !shown.has(normalizeInlineText(item)));
    if (!breaks.length) return "";
    return `<div class="wpem-summary" role="note"><p><strong>影响当前判断的限制</strong></p><ul>${breaks.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>`;
  }

  function signedBtc(value) {
    const number = safeNumber(value);
    if (number === null) return "";
    return `${number > 0 ? "+" : ""}${fmtBtc(number)}`;
  }

  function renderRiskReferenceUnavailableNotice(candidate) {
    const evidence = asObject(candidate.wpem_evidence);
    const riskStatus = asObject(evidence.risk_status);
    const reason = riskStatus.risk_reference_provided === false ? riskStatus.mu_M_null_reason : "";
    if (!reason) return "";
    return `<p class="candidate-risk-note">${escapeHtml(`风险参考未提供，μ/M 未知：${label(reason)}。`)}</p>`;
  }

  function wpemReadoutRow(name, value, note = "") {
    if (!value) return "";
    return `<div><dt>${escapeHtml(name)}</dt><dd>${escapeHtml(value)}</dd>${note ? `<small>${escapeHtml(note)}</small>` : ""}</div>`;
  }

  function shownQuestionTexts(candidate, renderedReview = {}) {
    const usingReview = Object.keys(asObject(renderedReview.q1)).length && Object.keys(asObject(renderedReview.q2)).length;
    const questions = usingReview
      ? renderedReview
      : asObject(candidate.current_questions);
    const q1 = asObject(questions.q1);
    const q2 = asObject(questions.q2);
    return new Set([
      q1.summary_cn,
      usingReview ? q1.window_attribution_cn : firstPresent(q1.window_attribution_cn, asObject(candidate.wpem_summary).path_cn),
      q1.competing_explanation_cn,
      q2.summary_cn,
      usingReview ? q2.window_tradeoff_cn : firstPresent(q2.window_tradeoff_cn, asObject(candidate.wpem_summary).compensation_cn),
      q2.exchange_reason_cn,
      q2.coverage_cn,
      q2.waiting_cost_cn,
      ...compactTextItems(q2.observable_changes_cn),
    ].map(normalizeInlineText).filter(Boolean));
  }

  function renderWpemCompactReadout(candidate, renderedReview = {}) {
    const summary = asObject(candidate.wpem_summary);
    const evidence = asObject(candidate.wpem_evidence);
    const candidateWindow = asObject(evidence.same_candidate_window);
    const sourceBindings = asObject(firstPresent(evidence.source_bindings, candidate.source_bindings, {}));
    const ldp = asObject(candidateWindow.LevelDeltaPath);
    const level = asObject(ldp.level);
    const delta = asObject(ldp.delta);
    const shownTexts = shownQuestionTexts(candidate, renderedReview);
    const uniqueDirect = (value) => {
      const text = normalizeInlineText(value);
      return text && !shownTexts.has(text) ? text : "";
    };
    const rows = [];
    const levelParts = [
      safeNumber(level.reference_price_usd) !== null ? `参考价 ${fmtUsd(level.reference_price_usd)}` : "",
      safeNumber(level.signed_distance_to_short_pct) !== null ? `距卖出腿 ${fmtPct(level.signed_distance_to_short_pct, 2)}` : "",
      firstPresent(level.current_level, level.status) ? label(firstPresent(level.current_level, level.status)) : "",
    ].filter(Boolean);
    if (levelParts.length) rows.push(wpemReadoutRow("截面", levelParts.join("；")));
    const deltaStatus = firstPresent(delta.status, summary.delta_status);
    const strictDelta = String(deltaStatus || "").toUpperCase() === "STRICT_SAME_CANDIDATE_DELTA";
    const deltaChanges = asObject(delta.changes);
    const referencePriceUsdDelta = deltaChanges.reference_price_usd_delta;
    const visibleCreditBtcDelta = deltaChanges.visible_credit_btc_delta;
    const deltaParts = strictDelta ? [
      safeNumber(referencePriceUsdDelta) !== null ? `参考价变化 ${fmtSigned(referencePriceUsdDelta, 2)} USD` : "",
      safeNumber(visibleCreditBtcDelta) !== null ? `可见信用变化 ${signedBtc(visibleCreditBtcDelta)}` : "",
    ].filter(Boolean) : [];
    if (deltaParts.length) rows.push(wpemReadoutRow("已观察片段", deltaParts.join("；"), "局部同合同相邻比较，不等于完整 Path"));
    else if (deltaStatus) rows.push(wpemReadoutRow("变化", label(deltaStatus), "未返回严格同候选增量字段"));
    const programPath = asObject(asObject(candidate.current_questions).q1).window_attribution_cn;
    const pathText = uniqueDirect(candidateWindow.status === "INSUFFICIENT" && programPath ? programPath : summary.path_cn);
    const compensationText = uniqueDirect(summary.compensation_cn);
    if (pathText) rows.push(wpemReadoutRow("路径", pathText));
    if (compensationText) rows.push(wpemReadoutRow("补偿", compensationText));
    const windowText = renderWpemWindowReadout(candidateWindow, sourceBindings);
    if (windowText) rows.push(wpemReadoutRow("窗口", windowText));
    if (!rows.length) return "";
    return `<div class="wpem-readout" aria-label="WPEM 关键读数">
      <p><strong>WPEM · 本单窗口</strong></p>
      <dl>${rows.join("")}</dl>
    </div>`;
  }

  function renderDecisionResearch(candidate, renderedReview = {}, contractDetails = "") {
    const evidence = asObject(candidate.wpem_evidence);
    const window = asObject(evidence.same_candidate_window);
    const ldp = asObject(window.LevelDeltaPath);
    const level = asObject(ldp.level);
    const delta = asObject(ldp.delta);
    const path = asObject(ldp.path);
    const dependency = asObject(window.dependency_diagnostics);
    const breaks = asObject(window.source_method_breaks);
    const summary = asObject(candidate.wpem_summary);
    const bindings = asObject(firstPresent(evidence.source_bindings, candidate.source_bindings, {}));
    if (!Object.keys(evidence).length) return "";
    const shownTexts = shownQuestionTexts(candidate, renderedReview);
    const uniqueDirect = (value) => {
      const text = normalizeInlineText(value);
      return text && !shownTexts.has(text) ? text : "";
    };
    const numberRow = (name, value, formatter) => safeNumber(value) === null ? "" : wpemReadoutRow(name, formatter(value));
    const changes = delta.status === "STRICT_SAME_CANDIDATE_DELTA" ? asObject(delta.changes) : {};
    const deltaParts = [
      safeNumber(changes.reference_price_usd_delta) !== null ? `参考价变化 ${fmtSigned(changes.reference_price_usd_delta, 2)} USD` : "",
      safeNumber(changes.visible_credit_btc_delta) !== null ? `可见信用变化 ${signedBtc(changes.visible_credit_btc_delta)}` : "",
      safeNumber(changes.fee_btc_delta) !== null ? `费用变化 ${signedBtc(changes.fee_btc_delta)}` : "",
    ].filter(Boolean);
    const positionRows = [
      numberRow("距卖出腿 · 有向距离", level.signed_distance_to_short_pct, (value) => fmtPct(value, 2)),
      numberRow("距保护腿 · 有向距离", level.signed_distance_to_long_pct, (value) => fmtPct(value, 2)),
      numberRow("窗口内距卖出腿变化", changes.signed_distance_to_short_pct_delta, (value) => `${fmtSigned(value, 2)} 个百分点`),
      numberRow("窗口内距保护腿变化", changes.signed_distance_to_long_pct_delta, (value) => `${fmtSigned(value, 2)} 个百分点`),
    ].join("");
    const pathObserved = path.status === "PATH_OBSERVED";
    const pathRows = pathObserved ? [
      numberRow("向本侧不利方向移动", path.adverse_steps, (value) => `${fmtNumber(value, 0)} 步`),
      numberRow("向本侧有利方向移动", path.favorable_steps, (value) => `${fmtNumber(value, 0)} 步`),
      numberRow("责任区域切换", path.zone_crossings, (value) => `${fmtNumber(value, 0)} 次`),
    ].join("") : "";
    const zoneNames = { outside_liability: "责任区外侧", liability_band: "卖出腿与保护腿之间", protected_tail: "保护腿外侧", unknown: "位置未知" };
    const zones = pathObserved ? asArray(path.zone_sequence).map((zone) => zoneNames[zone] || "未识别的区域（见审计）") : [];
    const coverage = coverageItems(window, bindings).map((item) => {
      const title = item.requested_window_ms === 900000 ? "15m" : (item.requested_window_ms === 3600000 ? "1h" : "窗口");
      const values = [
        label(item.status),
        safeNumber(item.point_count) !== null ? `${fmtNumber(item.point_count, 0)} 个点` : "",
        safeNumber(item.qualified_points) !== null ? `合格 ${fmtNumber(item.qualified_points, 0)} 个` : "",
        safeNumber(item.native_qualified_points) !== null ? `原生合格 ${fmtNumber(item.native_qualified_points, 0)} 个` : "",
        safeNumber(item.actual_coverage_ms) !== null ? `跨度 ${fmtMsAge(item.actual_coverage_ms)}` : "",
        safeNumber(item.max_gap_ms) !== null ? `最大间隔 ${fmtMsAge(item.max_gap_ms)}` : "",
      ].filter(Boolean).join("；");
      return wpemReadoutRow(title, values, `${fmtTime(item.coverage_start_ms)} → ${fmtTime(item.coverage_end_ms)}`);
    }).join("");
    const continuity = [
      breaks.candidate_rotation_observed === true ? "窗口记录存在候选更换" : (breaks.candidate_rotation_observed === false ? "未记录候选更换" : ""),
      breaks.method_break_observed === true ? "存在来源或方法断点" : (breaks.method_break_observed === false ? "未记录来源或方法断点" : ""),
    ].filter(Boolean).join("；");
    const dependencyRows = [
      numberRow("读取次数", dependency.read_count, (value) => fmtNumber(value, 0)),
      numberRow("去重更新组", dependency.unique_update_group_count, (value) => fmtNumber(value, 0)),
    ].join("");
    const group = asObject(level.anchor_gex_wall_group);
    const groupNote = group.evidence_group === "anchor_gex_wall_dependent_group"
      ? "Anchor、GEX、wall 属于同一依赖组，不构成多条独立确认。" : "";
    const rows = asArray(asObject(evidence.current_cross_section).cross_section_rows).map((item) => {
      const contract = asObject(item.contract);
      const identity = item.candidate_id === candidateId(candidate) ? "本单截面" : `${label(contract.side || "PEER")} 参照截面`;
      const details = [
        safeNumber(contract.short_strike_usd) !== null ? `卖出腿 ${fmtUsd(contract.short_strike_usd)}` : "",
        safeNumber(contract.long_strike_usd) !== null ? `保护腿 ${fmtUsd(contract.long_strike_usd)}` : "",
        item.decision_asof_ms ? `截面时点 ${fmtTime(item.decision_asof_ms)}` : "",
      ].filter(Boolean).join("；");
      const comparability = { CURRENT: "本单截面", COMPARABLE_PEER: "同决策时点 · 可比参照", NON_COMPARABLE_SNAPSHOT_ASOF: "截面时点不同 · 不可比" };
      return wpemReadoutRow(identity, comparability[item.comparability] || label(item.comparability || "未提供可比资格"), details);
    }).join("");
    const notes = asArray(asObject(candidate.reading_guidance).notes).filter((item) => item.text_cn);
    const reassess = asArray(asObject(evidence.counter_catalog_gap_reassess).reassess_conditions).filter((item) => item.claim_cn);
    const compensationResearchText = uniqueDirect(summary.compensation_cn);
    const fragmentPoints = [
      ...asArray(firstPresent(delta.observed_points, asObject(delta.fragments).observed_points)),
      ...asArray(firstPresent(path.observed_points, asObject(path.fragments).observed_points)),
    ];
    const adjacentFragment = asObject(firstPresent(delta.latest_adjacent_quote_pair, path.latest_adjacent_quote_pair, asObject(delta.relations).latest_adjacent_quote_pair, asObject(path.relations).latest_adjacent_quote_pair));
    const hasObservedFragments = fragmentPoints.length > 0 || Object.keys(adjacentFragment).length > 0 || deltaParts.length > 0;
    const fragmentStatusText = deltaParts.length
      ? "可用，且包含严格同候选增量读数"
      : (hasObservedFragments ? "可用，但不能升级为严格 Delta / Path" : "未提供");
    const frozenTrace = asObject(firstPresent(renderedReview.decision_trace, asObject(renderedReview.normalized).decision_trace));
    const frozenFragmentRow = asObject(asArray(frozenTrace.input_evidence).find(row => row.ref === "wpem.fragments"));
    const frozenFragment = Object.keys(asObject(frozenFragmentRow.provided)).length ? frozenFragmentRow.provided : frozenFragmentRow;
    const frozenPointCount = asArray(asObject(frozenFragment.facts).observed_points).length;
    const pathScopeRows = [
      wpemReadoutRow("完整 Path", path.status ? label(path.status) : "不可用", pathObserved ? "已观察同候选路径" : "窗口或路径资料不足时不升级为完整路径"),
      wpemReadoutRow("严格 Delta", delta.status ? label(delta.status) : "不可用", deltaParts.length ? "有相邻同候选变化读数" : "严格同候选增量不足"),
      wpemReadoutRow("局部片段", `当前窗口：${fragmentStatusText}${frozenPointCount ? `；这份解释的冻结输入另留存 ${fmtNumber(frozenPointCount, 0)} 个点` : ""}`, "局部片段只说明相邻观察，不能替代完整 Path"),
    ].join("");
    const fragmentRelationBits = [
      adjacentFragment.reference_price_relation ? `标的价${traceRelationLabel(adjacentFragment.reference_price_relation)}` : "",
      adjacentFragment.visible_credit_relation ? `信用${traceRelationLabel(adjacentFragment.visible_credit_relation)}` : "",
      adjacentFragment.fee_relation ? `费用${traceRelationLabel(adjacentFragment.fee_relation)}` : "",
      adjacentFragment.net_credit_after_fee_relation ? `净信用${traceRelationLabel(adjacentFragment.net_credit_after_fee_relation)}` : "",
    ].filter(Boolean);
    const fragmentNote = deltaParts.length
      ? deltaParts.join("；")
      : (fragmentRelationBits.length ? `相邻观察：${fragmentRelationBits.join("；")}` : (hasObservedFragments ? `${fmtNumber(fragmentPoints.length, 0)} 个局部观察点` : ""));
    return `<div class="decision-research-grid">
        <section><h4>责任位置</h4>
          <dl>${positionRows}${pathRows}</dl>
          ${zones.length ? `<p><strong>已观察区域顺序</strong><br>${escapeHtml(zones.join(" → "))}</p>` : ""}
          ${!pathObserved && path.status ? `<p class="muted">${escapeHtml(label(path.status))}</p>` : ""}
        </section>
        <section><h4>时间变化</h4><p class="muted">先看能说到什么程度，再看窗口明细。</p><dl>${pathScopeRows}${coverage}</dl>
          ${continuity ? `<p>${escapeHtml(continuity)}</p>` : ""}
          ${compactTextItems(summary.breaks_cn).length ? `<ul>${compactTextItems(summary.breaks_cn).map((text) => `<li>${escapeHtml(text)}</li>`).join("")}</ul>` : ""}
          ${fragmentNote ? `<p><strong>已观察片段</strong><br>${escapeHtml(fragmentNote)}</p>` : ""}
          ${frozenPointCount ? `<details data-disclosure-key="${escapeHtml(`${candidateDisclosureKey(candidate)}::frozen-fragments`)}"><summary>这份解释所见的局部片段 · 冻结输入</summary>${renderTraceCleanItem(frozenFragmentRow, "wpem.fragments", `${candidateDisclosureKey(candidate)}::research-fragments`)}</details>` : ""}
          ${renderConsumerResearch(candidate, "time")}
          <details data-disclosure-key="${escapeHtml(`${candidateDisclosureKey(candidate)}::window-readout`)}"><summary>窗口关键读数</summary>${renderWpemCompactReadout(candidate, renderedReview)}</details>
        </section>
        <section><h4>市场压力</h4>
          ${renderConsumerResearch(candidate, "market")}
          ${groupNote ? `<p>${escapeHtml(groupNote)}</p>` : ""}
          <details data-disclosure-key="${escapeHtml(`${candidateDisclosureKey(candidate)}::dependencies`)}"><summary>本单关系、依赖与可比资格</summary><dl>${dependencyRows}${rows}</dl>${dependency.reread_is_not_update === true ? `<p>重复读取不是一次新的更新；去重更新组也不是独立支持票数。</p>` : ""}<p>价格与距离描述同一位置事实；LLM 负责解释已有证据，不是新增的数据源。</p>${renderEvidenceRouting(candidate)}</details>
        </section>
        <section><h4>外部背景</h4>
          ${renderConsumerResearch(candidate, "external")}
          ${asObject(asObject(candidate.current_questions).research_context).independence_note_cn ? `<p class="muted">${escapeHtml(displayText(asObject(asObject(candidate.current_questions).research_context).independence_note_cn))}</p>` : ""}
          ${notes.length ? `<ul>${notes.map((item) => `<li>${escapeHtml(item.text_cn)}</li>`).join("")}</ul>` : `<p class="muted">外部背景在量价、宏观、链上与 MAP 子层中按来源时点阅读。</p>`}
        </section>
        <section><h4>补偿</h4>
          <dl>${wpemReadoutRow("当前权利金差", fmtBtc(asObject(asObject(candidate.snapshot).economics).premium_spread_btc))}${wpemReadoutRow("建仓净信用", fmtBtc(asObject(asObject(candidate.snapshot).economics).net_credit_after_entry_fee_btc), "扣建仓费用，未成交；到期费用另列")}${wpemReadoutRow("冻结风险参考 μ", fmtBtc(level.mu_ref_btc))}${wpemReadoutRow("参考补偿余量 M", fmtBtc(level.M_ref_btc))}</dl>
          ${compensationResearchText ? `<p>${escapeHtml(compensationResearchText)}</p>` : `<p class="muted">补偿只读取冻结信用、费用与风险参考；不生成新 EV 或方向判断。</p>`}
          ${reassess.length ? `<p><strong>已有失效与重评条件</strong></p><ul>${reassess.map((item) => `<li>${escapeHtml(item.claim_cn)}</li>`).join("")}</ul>` : ""}
          ${contractDetails}
        </section>
      </div>`;
  }

  function renderProgramFeeRemainder(candidate) {
    const account = asObject(asObject(candidate.wpem_evidence).quote_fee_deterministic_responsibility);
    if (safeNumber(account.c_minus_f_btc) === null) return "";
    return `<p class="decision-question-evidence"><strong>扣持有至到期费用后的信用 c−f</strong><br>${escapeHtml(fmtBtc(account.c_minus_f_btc))} · 冻结费用假设下的补偿账</p>`;
  }

  function renderRoutingObservations(question) {
    const rows = compactTextItems(asObject(question).routing_observations_cn);
    return rows.length ? `<ul class="routing-question-observations">${rows.map(text => `<li>${escapeHtml(text)}</li>`).join("")}</ul>` : "";
  }

  function consumerReadingText(item) {
    const r = asObject(item.readings);
    const sourceLabel = value => { const text = label(value); return text === "未归类资料状态（见原始记录）" ? String(value) : text; };
    const number = (value, digits = 3) => safeNumber(value) === null ? "未提供" : fmtNumber(value, digits);
    if (item.source_asset === "tmv_price_path") return `原方向 ${sourceLabel(r.direction)}；TMV总读数 ${number(r.tmv_blend)}；TMV 24h ${number(asObject(r.tmvf_24h).tmv_final)}；TMV 48h ${number(asObject(r.tmvf_48h).tmv_final)}。价格路径背景与主动流共享来源，不重复增信。`;
    if (item.source_asset === "funding_context") return `Funding当前最新原费率 ${number(r.last_rate, 8)}；Funding历史窗口24h累计 ${number(asObject(r.tmvf_24h).funding_cum, 8)}；Funding历史窗口48h累计 ${number(asObject(r.tmvf_48h).funding_cum, 8)}；已结算/窗口口径保留原记录；原拥挤标签 ${sourceLabel(asObject(r.semantics).crowding_state)}。不同统计口径保留，不按符号自动给方向。`;
    if (item.source_asset === "srd_skew_aggregate") return `RR 混合值 ${number(r.rr_blend)}；Skew 归一值 ${number(r.skew_norm_blend)}；原数据状态 ${sourceLabel(r.data_state)}。期权需求背景，未换算为概率或赔付。`;
    if (item.source_asset === "macro_pressure") {
      const rows = (Array.isArray(r.component_summaries) ? r.component_summaries : []).map(row => `${row.key || row.source_symbol} ${number(row.current_close)}（原参考 ${number(row.reference_close)}）；原变化 ${number(row.scoring_value)} ${row.scoring_unit || "单位未提供"}；来源 ${fmtTime(row.current_ts_ms)}`);
      return rows.length ? rows.join("。") + "。按各自原时点阅读；慢源不要求与分钟行情同步。" : item.text_cn;
    }
    if (["active_flow_cvd", "active_flow_cvd_unqualified"].includes(item.source_asset)) {
      const combined = asObject(r.combined);
      const rows = [["4h", asObject(r.fast_4h)], ["12h", asObject(r.slow_12h)]].map(([name, row]) => `${name} 原就绪 ${sourceLabel(row.data_ready)}；CVD ${number(row.cvd_sum)} ${row.cvd_unit || "单位未提供"}；原动量 ${number(row.momentum_return_pct)}%`);
      return `原方向 ${sourceLabel(combined.direction)}；${rows.join("。")}。未就绪的期限保持局部限制，不推出完整回路。`;
    }
    if (item.source_asset === "space_gate_gamma_regime") return `原空间门 ${sourceLabel(asObject(r.space_gate).state)}；GGR ${sourceLabel(asObject(r.gamma_regime).regime)}；翻转位 ${number(asObject(r.space_context_anchor).effective_flip_point, 2)} USD。Anchor 支持与同源结构限制同时保留。`;
    return item.text_cn;
  }

  function renderConsumerResearch(candidate, group = "all") {
    const context = asObject(asObject(candidate.current_questions).research_context);
    const sourceGroup = item => item.source_asset === "tmv_price_path" ? "time" : (["funding_context", "srd_skew_aggregate", "active_flow_cvd", "active_flow_cvd_unqualified", "space_gate_gamma_regime"].includes(item.source_asset) ? "market" : "external");
    const items = (Array.isArray(context.items) ? context.items : []).filter(item => item.text_cn && (group === "all" || sourceGroup(item) === group));
    if (!items.length) return "";
    const key = candidateDisclosureKey(candidate);
    const disclosure = key ? ` data-disclosure-key="${escapeHtml(`${key}::context`)}"` : "";
    const content = `<dl>${items.map(item => `<div><dt>${escapeHtml(displayToken(item.label_cn || item.source_asset, "补充资料"))}</dt><dd>${escapeHtml(displayText(consumerReadingText(item)))}<small>来源时点 ${escapeHtml(fmtTime(item.source_observed_ms))} · 资料时点 ${escapeHtml(fmtTime(item.asof_ms))}</small>${rawFold(`${key}::research::${item.source_asset}`, {source_ref:item.source_ref,producer_label_cn:item.producer_label_cn,readings:item.readings}, "原读数与来源")}</dd></div>`).join("")}</dl>`;
    return group === "all" ? `<details class="consumer-research"${disclosure}><summary>展开量价、期权与宏观补充 · 本单读数</summary>${content}<p class="muted">${escapeHtml(context.independence_note_cn || "")}</p></details>` : `<div class="consumer-research">${content}</div>`;
  }

  function renderEvidenceRouting(candidate) {
    const routing = asObject(asObject(candidate.wpem_evidence).evidence_routing);
    if (routing.schema !== "evidence_routing@1.0.0") return "";
    const binding = asObject(routing.binding);
    const snapshot = asObject(candidate.snapshot);
    if (binding.candidate_id !== snapshot.candidate_id || binding.snapshot_id !== snapshot.snapshot_id) return "";
    const units = Array.isArray(routing.units) ? routing.units : [];
    const blocks = Array.isArray(routing.dependency_blocks) ? routing.dependency_blocks : [];
    const blockMap = new Map(blocks.map(block => [block.block_id, block]));
    const unitMap = new Map(units.map(unit => [unit.evidence_id, unit]));
    const blockText = (item, loopName, relation) => {
      const value = typeof item === "string" ? {block_id:item} : asObject(item);
      const block = asObject(blockMap.get(value.block_id));
      const facet = loopName ? (Array.isArray(block.evidence_ids) ? block.evidence_ids : []).map(id=>unitMap.get(id)).find(unit=>unit && (unit.loop_relations || []).some(row=>row.loop===loopName && (relation==='SUPPORT' ? row.relation==='SUPPORT' : ['CONTRADICT','CONSTRAIN'].includes(row.relation)))) : null;
      const unit = asObject(facet || unitMap.get(value.evidence_id || block.representative_id));
      return firstPresent(value.reason_cn, value.summary_cn, block.reason_cn, block.summary_cn, unit.reason_cn, "");
    };
    const mapRows = Array.isArray(routing.problem_map) ? routing.problem_map : [];
    const primaryOrder = ["RESPONSIBILITY_SPACE", "FEEDBACK_STABILITY", "FLOW_REFLEXIVITY", "PATH_DYNAMICS", "COMPENSATION", "QUALIFICATION_DATA"];
    const primaryDomains = new Set(primaryOrder);
    const rowHtml = (row, expanded = false) => `<div><dt>${escapeHtml(row.label_cn || row.domain)}</dt><dd>${escapeHtml(row.summary_cn || "当前没有可用证据")}${(expanded ? compactTextItems(row.limitations) : []).map(text => `<small>${escapeHtml(text)}</small>`).join("")}</dd></div>`;
    const map = `<section class="routing-problem-map" aria-label="本单问题地图"><h4>本单问题地图</h4><dl>${mapRows.filter(row=>primaryDomains.has(row.domain)).sort((a,b)=>primaryOrder.indexOf(a.domain)-primaryOrder.indexOf(b.domain)).map(row=>rowHtml(row)).join("")}</dl></section>`;
    const digest = asObject(routing.loop_digest);
    const loopRows = [["bullish_loop","多头回路"],["neutral_stabilizing_loop","中性稳定回路"],["bearish_loop","空头回路"]];
    const loops = loopRows.map(([key,title]) => {
      const loop = asObject(digest[key]);
      const loopName = {bullish_loop:"BULLISH_LOOP",neutral_stabilizing_loop:"NEUTRAL_STABILIZING_LOOP",bearish_loop:"BEARISH_LOOP"}[key];
      const supports = (Array.isArray(loop.support_blocks) ? loop.support_blocks : []).map(item=>blockText(item,loopName,"SUPPORT")).filter(Boolean);
      const counters = (Array.isArray(loop.counter_blocks) ? loop.counter_blocks : []).map(item=>blockText(item,loopName,"COUNTER")).filter(Boolean);
      const limits = (Array.isArray(loop.limitations) ? loop.limitations : []).map(item=>typeof item === 'string' ? item : blockText(item)).filter(Boolean);
      return `<div class="routing-loop"><h5>${title}</h5>${supports.length ? `<p>${escapeHtml([...new Set(supports)].join(" "))}</p>` : '<p class="muted">当前没有合格支持块</p>'}${counters.length ? `<p class="routing-counter"><strong>反证</strong> ${escapeHtml([...new Set(counters)].join(" "))}</p>` : ""}${limits.length ? `<p class="muted">${escapeHtml([...new Set(limits)].join(" "))}</p>` : ""}</div>`;
    }).join("");
    const conflicts = (Array.isArray(digest.cross_loop_conflicts) ? digest.cross_loop_conflicts : []).map(item=>typeof item==='string'?item:firstPresent(item.reason_cn,item.summary_cn,"")).filter(Boolean);
    const unitLabel = unit => displayToken(firstPresent(unit.label_cn, unit.source_asset, unit.evidence_id), "证据单元");
    const unitSmall = unit => [unit.system_role, unit.dependency_group, unit.independence_status, unit.qualification_status, unit.candidate_relevance]
      .map(value => displayToken(value, "未整理状态")).filter(Boolean).join(" · ");
    const unitRole = value => displayToken(value, "未标注");
    const full = units.map(unit=>`<li><strong>${escapeHtml(unitLabel(unit))}</strong><p>${escapeHtml(displayText(unit.reason_cn))}</p><small>${escapeHtml(unitSmall(unit))}</small><p>Q1 ${escapeHtml(unitRole(unit.q1_role))} · Q2 ${escapeHtml(unitRole(unit.q2_role))}</p><details class="ia-raw"><summary>来源与窗口原始信息</summary><pre>${escapeHtml(JSON.stringify({source_ref:unit.source_ref,asof:unit.asof,asof_basis:unit.asof_basis,source_observed_ms:unit.source_observed_ms,source_clock_status:unit.source_clock_status,retained_snapshot_asof_ms:unit.retained_snapshot_asof_ms,horizon:unit.horizon,derived_from:unit.derived_from,trace_refs:unit.trace_refs,wpem_binding:unit.wpem_binding},null,2))}</pre></details></li>`).join("");
    return `<details class="evidence-routing" data-routing-hash="${escapeHtml(routing.routing_hash)}" data-disclosure-key="${escapeHtml(`${candidateDisclosureKey(candidate)}::routing::${routing.routing_hash}`)}"><summary>展开本单证据关系 · 量价、结构与路径</summary>${map}<section class="routing-loop-section" aria-label="调节回路截面"><h4>调节回路截面</h4><p class="muted">同源资料共享引用；互补与反证保留，回路可以并存。来源不同不等于独立确认。</p><div class="routing-loop-grid">${loops}</div>${conflicts.length ? `<p class="routing-conflict"><strong>尚未消解的分歧</strong><br>${escapeHtml(conflicts.join(" "))}</p>` : ""}</section><details class="routing-details"><summary>证据职责与依赖关系</summary><p class="muted">依赖分组沿用既有标注；来源依赖枚举不代表已验证独立性。</p><p>来源身份：${escapeHtml(displayToken(asObject(routing.binding).source_kind, "来源身份未整理"))}</p>${compactTextItems(routing.limitations).map(text=>`<p class="muted">${escapeHtml(displayText(text))}</p>`).join("")}<dl>${mapRows.map(row=>rowHtml(row,true)).join("")}</dl><ul class="routing-unit-list">${full}</ul>${rawFold(`${candidateDisclosureKey(candidate)}::routing::raw`, { schema: routing.schema, routing_hash: routing.routing_hash }, "查看 routing 协议与 hash")}</details></details>`;
  }

  function renderCandidateResearchLibrary(candidate, parts) {
    const body = asArray(parts).filter(Boolean).join("");
    if (!body) return "";
    const key = candidateDisclosureKey(candidate);
    const disclosure = key ? ` data-disclosure-key="${escapeHtml(`${key}::research-library`)}"` : "";
    return `<details class="ia-research candidate-research-library"${disclosure}><summary>研究本单 · 五类问题与市场事实<small>责任位置 / 时间变化 / 市场压力 / 外部背景 / 补偿</small></summary>${body}</details>`;
  }

  function renderCandidateAuditLibrary(candidate, parts) {
    const body = asArray(parts).filter(Boolean).join("");
    if (!body) return "";
    const key = candidateDisclosureKey(candidate);
    const disclosure = key ? ` data-disclosure-key="${escapeHtml(`${key}::audit-library`)}"` : "";
    return `<details class="candidate-audit-library"${disclosure}><summary id="candidate-audit-${escapeHtml(candidateId(candidate))}">审计与复盘 · 复盘这版判断<small>原文、完整依据与历史回执在此复核</small></summary>${body}</details>`;
  }

  function renderCandidateEvidenceFold(candidate) {
    const url = firstPresent(candidate.evidence_url, asObject(candidate.links).evidence_url, "");
    if (!url) return "";
    const key = candidateDisclosureKey(candidate);
    return remoteRawFold(key ? `${key}::candidate-evidence` : "", url, "完整快照与来源 · 按需读取");
  }

  function renderProgramQuestions(candidate) {
    const questions = asObject(candidate.current_questions);
    const q1 = asObject(questions.q1);
    const q2 = asObject(questions.q2);
    if (!Object.keys(q1).length || !Object.keys(q2).length) return "";
    const summary = asObject(candidate.wpem_summary);
    const q1Path = firstPresent(q1.window_attribution_cn, summary.path_cn);
    const q2Tradeoff = firstPresent(q2.window_tradeoff_cn, summary.compensation_cn);
    const observable = compactTextItems(q2.observable_changes_cn).slice(0, 3);
    return `<div class="two-question-grid wpem-program-questions" aria-label="程序事实两问">
      <section><h3>${escapeHtml(Q1_DECISION_TITLE)}</h3>
        ${q1.summary_cn ? `<p>${escapeHtml(displayText(q1.summary_cn))}</p>` : ""}${renderRoutingObservations(q1)}
        ${q1Path ? `<p class="decision-question-evidence"><strong>严格窗口证据</strong><br><span class="muted">用于判断严格 Delta / Path 是否成立。</span><br>${escapeHtml(displayText(q1Path))}</p>` : ""}
      </section>
      <section><h3>${escapeHtml(Q2_DECISION_TITLE)}</h3>
        ${q2.summary_cn ? `<p class="review-summary">${escapeHtml(displayText(q2.summary_cn))}</p>` : ""}${renderRoutingObservations(q2)}
        ${renderProgramFeeRemainder(candidate)}
        ${q2Tradeoff ? `<p class="decision-question-evidence"><strong>报价补偿取舍</strong><br>${escapeHtml(displayText(q2Tradeoff))}</p>` : ""}
        ${observable.length ? `<p><strong>可观察变化</strong></p><ul>${observable.map((item) => `<li>${escapeHtml(displayText(item))}</li>`).join("")}</ul>` : ""}
        ${q2.waiting_cost_cn ? `<p><strong>等待成本</strong><br>${escapeHtml(displayText(q2.waiting_cost_cn))}</p>` : ""}
      </section>
    </div>`;
  }

  function publicObservationValue(value) {
    // Downloads are an explicit public projection, never a provider/debug dump.
    const privateKey = /reasoning|(?:raw.*(?:provider|response)|(?:provider|response).*raw)|^provider_response$|^(?:provider_usage|private_usage|usage|usage_metadata|billing|headers)$|(?:^|[_-])(?:api[_-]?key|authorization|auth|token|password|secret|credentials?|private_key)(?:$|[_-])|^(?:composition_inputs|native_sources_b64|source_bindings|request_http_body|response_http_body)$/i;
    if (Array.isArray(value)) return value.map(publicObservationValue).filter(item => item !== undefined);
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value)
      .filter(([key]) => !privateKey.test(key))
      .map(([key, item]) => [key, publicObservationValue(item)]).filter(([, item]) => item !== undefined));
    if (typeof value === "string" && /^(?:[a-z]:[\\/]|\/(?:home|var|srv|tmp|Users|opt)\/)/i.test(value)) return undefined;
    return value;
  }

  function candidateEvidenceUrl(candidate) {
    return firstPresent(asObject(candidate).evidence_url, asObject(asObject(candidate).links).evidence_url, "");
  }

  function candidateSnapshotId(candidate) {
    return firstPresent(asObject(candidate).snapshot_id, asObject(asObject(candidate).snapshot).snapshot_id, "");
  }

  function publicCandidateFromEvidence(payload) {
    const object = asObject(payload);
    const point = asObject(object._public_point);
    return asObject(firstPresent(object.public_candidate, point.public_candidate, object.candidate, {}));
  }

  function assertCandidateEvidenceIdentity(candidate, payload) {
    const expected = candidateSnapshotId(candidate);
    const object = asObject(payload);
    const point = asObject(object._public_point);
    const actual = firstPresent(object.snapshot_id, point.snapshot_id, candidateSnapshotId(publicCandidateFromEvidence(payload)), "");
    if (!expected || actual !== expected || candidateSnapshotId(publicCandidateFromEvidence(payload)) !== expected) {
      throw new Error(`完整候选证据 snapshot_id 不一致：${actual}；已停止保存复盘包。`);
    }
  }

  async function buildObservationCapture(candidate, renderedText = "") {
    const evidenceUrl = candidateEvidenceUrl(candidate);
    if (!evidenceUrl) return observationCapture(candidate, renderedText);
    const payload = await fetchJson(evidenceUrl);
    assertCandidateEvidenceIdentity(candidate, payload);
    return observationCapture(candidate, renderedText, payload);
  }

  function observationCapture(candidate, renderedText = "", evidencePayload = null) {
    const evidenceCandidate = evidencePayload ? publicCandidateFromEvidence(evidencePayload) : {};
    const sourceCandidate = Object.keys(evidenceCandidate).length ? { ...candidate, ...evidenceCandidate } : candidate;
    const display = candidateReviewDisplay(candidate);
    const receipt = asObject(display.consumer_binding_receipt);
    const keptCandidate = Object.fromEntries(["candidate_id", "side", "status", "snapshot", "current_questions", "wpem_summary", "wpem_evidence", "review_display", "review_validity", "btc_map", "gaps", "reading_guidance"]
      .filter(key => sourceCandidate[key] !== undefined).map(key => [key, sourceCandidate[key]]));
    keptCandidate.review_gate = { enabled: false, reasons: ["公开消费快照，只读复盘"] };
    keptCandidate.observation_review = { enabled: false, reasons: ["公开消费快照不调用模型"] };
    const evidencePoint = asObject(asObject(evidencePayload)._public_point);
    return publicObservationValue({
      schema: "observation_consumption_capture@1",
      captured_at_ms: Date.now(),
      rendered_at_ms: state.observationRenderedAt,
      renderer: RENDERER_IDENTITY,
      original_consumed_at_ms: null,
      user_read_or_traded: "not_recorded",
      scope_cn: "本包留存公开消费投影和页面文本；完整 Data 到 input 复算仍需原始私有冻结 bundle。本次保存/渲染时间不是历史消费时间。",
      consumer_binding_receipt: receipt,
      visible_primary_text: renderedText,
      displayed: {primary_source: receipt.primary_source || "not_recorded", research_and_audit: "folded_reachable", user_expanded: "not_recorded"},
      public_view: {
        schema: asObject(state.workbench).schema,
        updated_at_ms: asObject(state.workbench).updated_at_ms,
        preview: {kind: "frozen_real_fixture", mode: "FROZEN_REPLAY", note_cn: "保存的公开消费快照；非实时行情"},
        candidates: [keptCandidate],
        observation_replay: asObject(state.workbench).observation_replay,
        execution_allowed: false, production_attach_allowed: false,
      },
      retained_candidate_evidence: evidencePayload ? {
        schema: asObject(evidencePayload).schema,
        snapshot_id: firstPresent(asObject(evidencePayload).snapshot_id, evidencePoint.snapshot_id, candidateSnapshotId(evidenceCandidate)),
        asof_ms: firstPresent(asObject(evidencePayload).asof_ms, evidencePoint.asof_ms),
        recorded_at_ms: firstPresent(asObject(evidencePayload).recorded_at_ms, evidencePoint.recorded_at_ms),
        public_packet: firstPresent(asObject(evidencePayload).public_packet, evidencePoint.public_packet, null),
      } : { deferred_cn: "本次保存仅使用页面阅读投影；完整快照未由服务端 evidence_url 提供。" },
      execution_allowed: false, production_attach_allowed: false,
    });
  }

  function publicLockedValue(value) {
    const privateReasoningKey = /^(?:reasoning|provider_reasoning|reasoning_content|raw_reasoning|model_reasoning|private_reasoning|hidden_reasoning|chain_of_thoughts?|cot|provider_cot)$/i;
    const privateKey = /(?:raw.*(?:provider|response)|(?:provider|response).*raw)|^provider_response$|^(?:provider_usage|private_usage|usage|usage_metadata|billing|headers)$|(?:^|[_-])(?:api[_-]?key|authorization|auth|token|password|secret|credentials?|private_key)(?:$|[_-])|^(?:composition_inputs|native_sources_b64|source_bindings|request_http_body|response_http_body|private_packet|raw_packet|packet_body|wire_request|account|margin|liquidation)(?:$|[_-])/i;
    if (Array.isArray(value)) return value.map(publicLockedValue).filter(item => item !== undefined);
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value)
      .filter(([key]) => !["packet", "provider_request", "request_body", "request_payload"].includes(key.toLowerCase()) && !privateReasoningKey.test(key) && !privateKey.test(key))
      .map(([key, item]) => [key, publicLockedValue(item)]).filter(([, item]) => item !== undefined));
    if (typeof value === "string" && /^(?:[a-z]:[\\/]|\/(?:home|var|srv|tmp|Users|opt)\/)/i.test(value)) return undefined;
    return value;
  }

  function lockedPointId(point) {
    const object = asObject(point);
    return String(firstPresent(object.point_id, object.id, object.baseline_point_id, object.latest_point_id, "") ?? "").trim();
  }

  function lockedEvidenceCacheKey(comparison) {
    const object = asObject(comparison);
    const episodeId = lockedEpisodeId(object.episode) || lockedEpisodeId(object);
    const baselinePointId = lockedPointId(object.baseline);
    const latestPointId = lockedPointId(object.latest);
    return [episodeId, baselinePointId, latestPointId].filter(Boolean).join("::");
  }

  function lockedEvidenceUrl(comparison) {
    const object = asObject(comparison);
    const direct = firstPresent(object.evidence_url, asObject(object.links).evidence_url, asObject(object.evidence).url, "");
    if (direct) return direct;
    const episodeId = lockedEpisodeId(object.episode) || lockedEpisodeId(object);
    const baselinePointId = lockedPointId(object.baseline);
    const latestPointId = lockedPointId(object.latest);
    if (!episodeId || !baselinePointId || !latestPointId) return "";
    const params = queryString({ baseline_point_id: baselinePointId, latest_point_id: latestPointId });
    return `${API_LOCKED_CANDIDATES}/${encodeURIComponent(episodeId)}/evidence?${params}`;
  }

  function comparableEvidencePayload(value) {
    const object = asObject(value);
    return asObject(firstPresent(object.comparison, object.detail, object.locked_comparison, object));
  }

  function assertLockedEvidenceIdentity(thinComparison, fullPayload) {
    const thin = asObject(thinComparison);
    const full = comparableEvidencePayload(fullPayload);
    const thinEpisode = lockedEpisodeId(thin.episode) || lockedEpisodeId(thin);
    const fullEpisode = lockedEpisodeId(full.episode) || lockedEpisodeId(full);
    const thinBaseline = lockedPointId(thin.baseline);
    const fullBaseline = lockedPointId(full.baseline);
    const thinLatest = lockedPointId(thin.latest);
    const fullLatest = lockedPointId(full.latest);
    const mismatch = [
      !thinEpisode || thinEpisode !== fullEpisode ? "episode_id" : "",
      !thinBaseline || thinBaseline !== fullBaseline ? "baseline_point_id" : "",
      !thinLatest || thinLatest !== fullLatest ? "latest_point_id" : "",
    ].filter(Boolean);
    if (mismatch.length) throw new Error(`完整证据身份不一致：${mismatch.join(", ")}。已停止导出，未回落到当前 latest。`);
  }

  async function fetchLockedEvidence(comparison) {
    const key = lockedEvidenceCacheKey(comparison);
    if (key && state.lockedEvidenceCache[key]) return state.lockedEvidenceCache[key];
    const url = lockedEvidenceUrl(comparison);
    if (!url) throw new Error("服务端未提供可精确读取的完整证据地址。");
    const payload = await fetchLockedJson(url);
    assertLockedEvidenceIdentity(comparison, payload);
    if (key) rememberLockedEvidence(key, payload);
    return payload;
  }

  function lockedCapture(comparison, fullEvidence = null) {
    return publicLockedValue({
      schema: "locked_candidate_followup_export@1",
      captured_at_ms: Date.now(),
      renderer: RENDERER_IDENTITY,
      source_schema: asObject(comparison).schema,
      scope_cn: "本包固定导出点击时已显示的锁定参考 t0、最近更新 t1、对照 rows 与一份精确完整公开证据；离线阅读不新增采样、不调用 LLM。",
      displayed_comparison: comparison,
      full_evidence: fullEvidence,
      execution_allowed: false,
      actual_trade_recorded: false,
      user_read_or_traded: "not_recorded",
    });
  }

  function lockedPointText(point) {
    const object = asObject(point);
    const direct = firstPresent(object.text_cn, object.summary_cn, object.label_cn, "");
    if (direct) return displayText(direct);
    const value = firstPresent(object.value_cn, object.value, "");
    const unit = firstPresent(object.unit, "");
    if (value !== "") return lockedUnitValueText(value, unit);
    return "未提供";
  }

  function lockedUnitValueText(value, unit = "") {
    const numeric = safeNumber(value);
    const unitText = String(unit || "").trim();
    const lower = unitText.toLowerCase();
    if (numeric === null) return [value, unitText].filter(Boolean).join(" ");
    if (["pct", "percent", "%"].includes(lower)) return lockedPercentText(numeric);
    if (["pp", "percentage_point", "percentage_points"].includes(lower)) return `${fmtNumber(numeric, 3)} 个百分点`;
    if (lower === "btc") return fmtBtc(numeric);
    if (lower === "usd") return fmtUsd(numeric);
    return [fmtNumber(numeric, lower === "bp" || lower === "bps" ? 3 : 4), unitText].filter(Boolean).join(" ");
  }

  function lockedPercentText(value) {
    if (value !== 0 && Math.abs(value) < 0.001) return `${value < 0 ? "负值" : "正值"}（绝对值小于 0.001%）`;
    return `${fmtNumber(Object.is(value, -0) ? 0 : value, 3)}%`;
  }

  function lockedPointDeltaText(value) {
    const numeric = safeNumber(value);
    if (numeric === null) return "";
    if (Object.is(numeric, -0) || numeric === 0) return "未变化";
    const direction = numeric > 0 ? "增加" : "减少";
    const abs = Math.abs(numeric);
    if (abs < 0.001) return numeric > 0 ? "微增（小于 0.001 个百分点）" : "微降（小于 0.001 个百分点）";
    return `${direction} ${fmtNumber(abs, 3)} 个百分点`;
  }

  function normalizeLockedPrecisionText(value) {
    // Model and producer prose is preserved; precision is applied only to
    // typed numeric fields by lockedUnitValueText / lockedPointDeltaText.
    return displayText(value);
  }

  function lockedPointClock(point) {
    const object = asObject(point);
    const asof = firstPresent(object.asof_ms, object.asof, "");
    return lockedTime(asof, "时点未提供");
  }

  function lockedEpisodeTitle(episode) {
    const object = asObject(episode);
    const side = label(firstPresent(object.side, asObject(object.fixed_contract).side, asObject(object.contract).side, ""));
    const shortLeg = firstPresent(object.short_instrument, asObject(object.fixed_contract).short_instrument, asObject(object.contract).short_instrument, "卖出腿未提供");
    const longLeg = firstPresent(object.long_instrument, asObject(object.fixed_contract).long_instrument, asObject(object.contract).long_instrument, "保护腿未提供");
    return `${side || "本单"} · ${shortLeg} / ${longLeg}`;
  }

  function lockedTime(value, fallback = "未提供") {
    const numeric = safeNumber(value);
    if (numeric !== null && numeric <= 0) return fallback;
    return firstPresent(value, "") ? fmtTime(value) : fallback;
  }

  function lockedStatusLabel(status) {
    const text = String(status || "").toUpperCase();
    if (/PAUSE|SUSPEND/.test(text)) return "已暂停";
    if (/STOP/.test(text)) return "已停止";
    if (/EXPIRE/.test(text)) return "已到期";
    if (/ERROR|FAILED|FAIL/.test(text)) return "异常";
    if (/MISSING/.test(text)) return "资料缺失";
    if (/GAP/.test(text)) return "采样有缺口";
    if (/ACTIVE|CURRENT|OK|OBSERVED/.test(text)) return "持续观察中";
    return label(status, "状态未提供");
  }

  function lockedStatusTone(status) {
    const text = String(status || "").toUpperCase();
    if (/STOP|EXPIRE|MISSING|GAP|UNKNOWN|FAILED|FAIL|ERROR|PAUSE|SUSPEND/.test(text)) return "warn";
    if (/ACTIVE|CURRENT|OK|OBSERVED/.test(text)) return "good";
    return "blue";
  }

  function renderLockedSummaryCard(episode) {
    const id = lockedEpisodeId(episode);
    const status = firstPresent(episode.status, asObject(episode.episode).status, "ACTIVE");
    const baselineAt = firstPresent(episode.baseline_decision_asof_ms, episode.baseline_asof_ms, asObject(episode.baseline).decision_asof_ms, asObject(episode.baseline).asof_ms);
    const lockedAt = firstPresent(episode.created_at_ms, episode.locked_at_ms, episode.recorded_at_ms, asObject(episode.episode).created_at_ms);
    const latestAt = firstPresent(episode.latest_decision_asof_ms, episode.latest_asof_ms, asObject(episode.latest).decision_asof_ms, asObject(episode.latest).asof_ms, episode.last_observed_ms, episode.updated_at_ms);
    const count = firstPresent(episode.point_count, episode.observed_count, asObject(episode.process).observed_count, "");
    const sampling = asObject(episode.sampling);
    const gapReason = firstPresent(episode.last_gap_reason, sampling.last_gap_reason, asObject(episode.process).last_gap_reason, "");
    const collectorError = firstPresent(episode.collector_error, sampling.collector_error, "");
    return `<article class="locked-summary-card">
      <div>
        <h3>${escapeHtml(lockedEpisodeTitle(episode))}</h3>
        <p class="muted">t0 资料 ${escapeHtml(lockedTime(baselineAt))}；锁定保存 ${escapeHtml(lockedTime(lockedAt))}；最近更新 ${escapeHtml(latestAt ? lockedTime(latestAt) : "暂无 t1")}。</p>
        <p class="muted">实际成交未录入；停止跟踪不是平仓。${count !== "" ? ` 已观察 ${escapeHtml(fmtNumber(count, 0))} 次。` : ""}</p>
        ${gapReason ? `<p class="muted">最近缺口：${escapeHtml(displayText(gapReason))}</p>` : ""}
        ${collectorError ? `<p class="muted">采样异常：${escapeHtml(displayText(collectorError))}</p>` : ""}
      </div>
      <div class="locked-summary-actions">
        ${badge(lockedStatusLabel(status), lockedStatusTone(status))}
        <a href="${escapeHtml(lockedEpisodeHash(id))}" data-locked-open="${escapeHtml(id)}">查看跟踪</a>
      </div>
    </article>`;
  }

  function renderLockedIdentity(comparison) {
    const fixed = asObject(comparison.fixed_contract);
    const episode = asObject(comparison.episode);
    const baseline = asObject(comparison.baseline);
    const latest = asObject(comparison.latest);
    const baselineDecisionAt = firstPresent(comparison.baseline_decision_asof_ms, baseline.decision_asof_ms, baseline.asof_ms, episode.baseline_decision_asof_ms, episode.baseline_asof_ms);
    const latestDecisionAt = firstPresent(comparison.latest_decision_asof_ms, latest.decision_asof_ms, latest.asof_ms, episode.latest_decision_asof_ms, episode.latest_asof_ms);
    const legs = [
      firstPresent(fixed.short_instrument, episode.short_instrument, "卖出腿未提供"),
      firstPresent(fixed.long_instrument, episode.long_instrument, "保护腿未提供"),
    ].join(" / ");
    const rows = [
      metric("固定合同", legs, `${label(firstPresent(fixed.side, episode.side, ""))}；数量 ${firstPresent(fixed.quantity_text, fixed.quantity_btc === undefined ? null : `${fmtNumber(fixed.quantity_btc)} BTC`, episode.quantity_text, "未提供")}`),
      metric("t0 资料时点", lockedTime(baseline.asof_ms), "锁定时保存的资料；完整身份见解释与审计"),
      metric("资料存档时间", lockedTime(firstPresent(baseline.recorded_at_ms, baseline.saved_at_ms)), "公开原件/投影写入时间，不等于行情发生时间"),
      metric("锁定时间", lockedTime(firstPresent(episode.created_at_ms, episode.locked_at_ms, episode.recorded_at_ms)), "用户锁定保存时间，不替代 t0 资料时点"),
      metric("最近资料时点", Object.keys(latest).length ? lockedTime(latest.asof_ms) : "暂无 t1", Object.keys(latest).length ? "最近成功记录的资料，来源时钟各行另列" : "等待后台刷新"),
      metric("实际成交", "未录入", "本页是锁定参考与公开观察，不是账户持仓或成交确认"),
    ].join("");
    return `<section class="locked-identity"><h3>固定身份与时钟</h3><div class="metric-grid">${rows}</div></section>`;
  }

  function lockedOverviewValue(value, fallback = "未提供") {
    if (Array.isArray(value)) {
      const parts = value.map((item) => lockedOverviewValue(item, "")).filter(Boolean);
      return parts.length ? parts.join("；") : fallback;
    }
    const object = asObject(value);
    if (Object.keys(object).length) {
      const direct = firstPresent(object.text_cn, object.summary_cn, object.value_cn, object.label_cn, "");
      if (direct) return direct;
      const nested = firstPresent(object.t1, object.current, object.latest, object.value, "");
      if (nested && nested !== value) return lockedOverviewValue(nested, fallback);
      const numeric = firstPresent(object.value, object.amount, "");
      if (numeric !== "") return firstPresent(object.unit, "") ? `${numeric} ${object.unit}` : String(numeric);
      return fallback;
    }
    return firstPresent(value, fallback);
  }

  function lockedUsdList(value, fallback = "未提供") {
    if (Array.isArray(value)) {
      const parts = value.map((item) => {
        const numeric = safeNumber(item);
        return numeric === null ? lockedOverviewValue(item, "") : fmtUsd(numeric);
      }).filter(Boolean);
      return parts.length ? parts.join("；") : fallback;
    }
    const numeric = safeNumber(value);
    if (numeric !== null) return fmtUsd(numeric);
    return lockedOverviewValue(value, fallback);
  }

  function lockedBreakevenText(overview) {
    const object = asObject(overview);
    return lockedUsdList(firstPresent(
      object.baseline_breakeven_roots_usd,
      object.baseline_breakeven_roots,
      object.baseline_breakeven_usd,
      object.baseline_breakeven,
      ""
    ), "未提供");
  }

  function lockedBreakevenDistanceText(overview) {
    const value = asObject(overview).breakeven_distances;
    if (Array.isArray(value)) {
      const parts = value.map((item) => {
        const object = asObject(item);
        const latest = asObject(object.t1);
        if (latest.unit === "pct" && safeNumber(latest.value) !== null) return lockedPercentText(Number(latest.value));
        return firstPresent(
          object.text_cn,
          object.summary_cn,
          object.distance_cn,
          asObject(object.t1).text_cn,
          asObject(object.t1).summary_cn,
          object.t1,
          ""
        );
      }).map((item) => lockedOverviewValue(item, "")).filter(Boolean);
      return parts.length ? parts.join("；") : "未提供";
    }
    return lockedOverviewValue(value);
  }

  function lockedCloseEstimateText(overview) {
    const object = asObject(overview);
    const close = [object.close, object.close_estimate, object.reference_result, object.reference_close_result]
      .map(asObject)
      .find((item) => Object.keys(item).length) || {};
    const parts = [];
    const reference = asObject(object.reference_result);
    const summary = firstPresent(close.text_cn, close.summary_cn, close.reference_result_cn, asObject(close.reference_result).text_cn, reference.text_cn, reference.summary_cn, "");
    if (summary) parts.push(displayText(summary));
    const debit = firstPresent(close.debit_btc, close.close_debit_btc, "");
    const fee = firstPresent(close.close_fee_btc, close.fee_btc, close.fee_gap_btc, "");
    const net = firstPresent(close.net_debit_after_close_fee_btc, close.net_debit_btc, "");
    const referenceResultBtc = firstPresent(close.reference_result_btc, object.reference_result_btc, reference.reference_result_btc, "");
    if (safeNumber(referenceResultBtc) !== null) parts.push(`参考结果 ${fmtBtc(referenceResultBtc)}`);
    if (safeNumber(debit) !== null) parts.push(`参考借记 ${fmtBtc(debit)}`);
    if (safeNumber(fee) !== null) parts.push(`参考平仓费用 ${fmtBtc(fee)}`);
    if (safeNumber(net) !== null) parts.push(`含费用净借记 ${fmtBtc(net)}`);
    if (safeNumber(fee) === null) parts.push("平仓费用未知");
    if (safeNumber(referenceResultBtc) === null) parts.push("费用或盘口不全，参考结果未知");
    return parts.join("；");
  }

  function renderLockedOverview(comparison) {
    const overview = asObject(comparison.overview);
    const limitations = compactTextItems(comparison.limitations_cn).slice(0, 3);
    const rows = [
      metric("短腿距离", safeNumber(asObject(overview.short_distance).value) === null ? lockedOverviewValue(overview.short_distance) : lockedPercentText(Number(overview.short_distance.value)), "正值在不利边界外，负值已越过；不表示安全度"),
      metric("锁定参考 BE", lockedBreakevenText(overview), "t0 参考线固定，不随 t1 新信用移动"),
      metric("BE 距离", lockedBreakevenDistanceText(overview), "如无原生口径则保留原因，不硬造线性公式"),
      metric("剩余到期", fmtMsAge(overview.remaining_to_expiry_ms), "自然缩短不自动解释为利好或恶化"),
      metric("平仓估计", lockedCloseEstimateText(overview), "可得时使用同合同参考口径；费用缺口与参考结果不是实际 P&L"),
    ].join("");
    return `<section class="locked-overview"><h3>责任与退出概览</h3>
      <div class="metric-grid locked-overview-grid">${rows}</div>
      <p class="muted">t0/t1 是两个端点对照；除非过程样本另有证明，本页不宣称完整 Path。</p>
      ${limitations.length ? `<ul class="locked-limitations">${limitations.map((item) => `<li>${escapeHtml(displayText(item))}</li>`).join("")}</ul>` : ""}
    </section>`;
  }

  function lockedRowPointText(row, side) {
    const point = asObject(row[side]);
    const raw = asObject(asObject(row.raw)[side]);
    if (raw.source_asset && raw.readings) return lockedRowMeaning(consumerReadingText(raw));
    if (row.key === "context.routing_relations") {
      const summary = asObject(raw.selection_summary);
      const supports = asArray(summary.claims).filter(item => item.q1_role === "SUPPORT");
      const counters = asArray(summary.counters);
      const text = item => displayText(firstPresent(asObject(item).q1_reason_cn, asObject(item).text_cn, asObject(item).claim_cn, ""));
      return [supports.length ? `已有支持：${text(supports[0])}` : "未列出合格支持", counters.length ? `已有反证：${counters.map(text).filter(Boolean).join("；")}` : "未列出反证", "完整依赖与限制见本行原件"].join("；");
    }
    if (point.unit === "pct" && safeNumber(point.value) !== null) return `${lockedPercentText(Number(point.value))}${safeNumber(raw.boundary_usd) === null ? "" : `；边界 ${fmtUsd(raw.boundary_usd)}`}`;
    if (row.key === "risk.status") return `风险参考：${displayToken(point.value, "状态未提供")}`;
    return lockedPointText(point);
  }

  function lockedRowMeaning(text) {
    return normalizeLockedPrecisionText(text).replaceAll("research_context", "既有研究资料").replaceAll("精确 delta", "精确差值").replaceAll("short ask", "短腿卖方报价").replaceAll("long bid", "保护腿买方报价").replaceAll("delivery fee", "交割费用").replaceAll("unknown", "未知").replaceAll("直接 refs", "直接引用").replace(/\bBearish\b/gi, "偏空").replace(/\bBullish\b/gi, "偏多").replaceAll("NEGATIVE_GAMMA_AMPLIFYING", "负 Gamma 放大").replaceAll("POSITIVE_GAMMA_DAMPENING", "正 Gamma 缓冲").replace(/\bpct\b/g, "%").replace(/\bbps\b/g, "基点");
  }

  function lockedRowChangeText(row, status) {
    const object = asObject(row);
    if (String(status).toLowerCase() === "changed" && asObject(object.t0).unit === "pct" && asObject(object.t1).unit === "pct"
        && safeNumber(object.t0.value) !== null && safeNumber(object.t1.value) !== null) {
      return lockedPointDeltaText(Number(object.t1.value) - Number(object.t0.value));
    }
    const change = asObject(object.change);
    const pp = firstPresent(object.change_pp, object.delta_pp, object.change_percentage_points, change.pp, change.delta_pp, change.percentage_points, "");
    const ppText = lockedPointDeltaText(pp);
    if (ppText) return ppText;
    return lockedRowMeaning(firstPresent(object.change_cn, change.text_cn, change.summary_cn, label(status), "未给出变化"));
  }

  function lockedRowBasisHtml(item, key, episodeId) {
    const basis = [
      ...compactTextItems(firstPresent(item.basis_cn, item.reason_cn, item.comparison_basis_cn, "")),
      ...compactTextItems(item.limitations_cn),
      ...compactTextItems(asObject(item.meta).limitations_cn),
      ...compactTextItems(asObject(item.meta).basis_cn),
    ].map(lockedRowMeaning).filter(Boolean);
    const unique = [...new Set(basis)].slice(0, 4);
    return `<details class="locked-row-detail" data-disclosure-key="${escapeHtml(`locked::${episodeId}::row::${key}`)}">
      <summary>依据</summary>
      ${unique.length ? `<ul>${unique.map((text) => `<li>${escapeHtml(text)}</li>`).join("")}</ul>` : ""}
      ${rawFold("", publicLockedValue({ key, group: item.group, status: item.status, change_cn: item.change_cn, meaning_cn: item.meaning_cn, t0: item.t0, t1: item.t1, meta: item.meta, raw: item.raw }), "查看本行两端公开字段")}
    </details>`;
  }

  function renderLockedRows(comparison) {
    const rows = asArray(comparison.rows);
    if (!rows.length) return empty("服务端尚未返回可比对照行；锁定基线仍可查。");
    return `<p class="muted">对比锁定参考与最近可用资料；锁定不等于成交。</p>
    <details class="locked-comparison-rules" data-disclosure-key="${escapeHtml(`locked::${lockedEpisodeId(comparison.episode)}::rules`)}"><summary>比较口径与限制</summary>
      <p>固定同一合同的 t0 与最近可读 t1；缺失、来源切换、方法或单位不可比时限制结论，不回填原边界，不把最后有效记录续成当前事实。</p>
    </details>
    <div class="locked-table-wrap" role="region" tabindex="0" aria-label="本单锁定前后对照表">
      <table class="locked-table">
        <caption>表格可横向滚动；每行只说明事实变化和对本单的范围，不给持有、对冲或平仓指令。</caption>
        <thead><tr><th>项目</th><th>锁定时</th><th>最近更新</th><th>变化</th><th>本单含义</th></tr></thead>
        <tbody>${rows.map((row, index) => {
          const item = asObject(row);
          const key = firstPresent(item.key, item.label_cn, `row-${index}`);
          const t0 = asObject(item.t0);
          const t1 = asObject(item.t1);
          const status = firstPresent(item.status, "");
          const unknownBE = key === "liability.baseline_breakeven_unknown";
          return `<tr class="locked-row locked-row-${escapeHtml(lockedStatusTone(status))}">
            <th scope="row"><strong>${escapeHtml(lockedRowMeaning(firstPresent(item.label_cn, key)))}</strong><span>${escapeHtml(({liability:"责任位置",quote_fee:"报价与费用",context:"已有市场事实",opinion:"原观点与依据",risk:"风险参考"})[item.group] || "跟踪项目")}</span></th>
            <td>${unknownBE ? "未形成原生 BE" : escapeHtml(lockedRowPointText(item, "t0"))}<span>${escapeHtml(lockedPointClock(t0))}</span></td>
            <td>${unknownBE ? "无可沿用基线" : Object.keys(t1).length ? escapeHtml(lockedRowPointText(item, "t1")) : "当前不可核验"}<span>${unknownBE ? "" : Object.keys(t1).length ? escapeHtml(lockedPointClock(t1)) : "缺失不等于保持不变"}</span></td>
            <td>${unknownBE ? "不可计算" : escapeHtml(lockedRowChangeText(item, status))}</td>
            <td>${unknownBE ? "后续新信用不补写原 BE；原始原因见依据。" : escapeHtml(lockedRowMeaning(firstPresent(item.meaning_cn, "仅作本单观察，不生成交易结论")))}
              ${lockedRowBasisHtml(item, key, lockedEpisodeId(comparison.episode))}
            </td>
          </tr>`;
        }).join("")}</tbody>
      </table>
    </div>`;
  }

  function renderLockedProcess(comparison) {
    const process = asObject(comparison.process);
    const observations = asArray(process.observations);
    const gaps = asArray(process.gaps);
    const otherEvents = asArray(process.other_events);
    if (!Object.keys(process).length && !observations.length && !gaps.length && !otherEvents.length) return "";
    const eventText = (item) => {
      const row = asObject(item);
      const direct = firstPresent(row.text_cn, row.summary_cn, row.reason_cn, row.label_cn, "");
      if (direct) return displayText(direct);
      if (row.kind === "point") return `资料时点 ${lockedTime(row.decision_asof_ms)}；存档 ${lockedTime(row.recorded_at_ms)}`;
      if (row.kind === "gap") return `${lockedTime(row.recorded_at_ms)}：${lockedGapReason(firstPresent(asObject(row.gap).reason, row.reason, ""))}`;
      return `${lockedTime(row.recorded_at_ms)}：状态记录，详见公开原文`;
    };
    const range = asObject(process.index_range);
    const rangeText = range.baseline_decision_asof_ms || range.latest_decision_asof_ms
      ? `${lockedTime(range.baseline_decision_asof_ms)} → ${lockedTime(range.latest_decision_asof_ms)}`
      : lockedOverviewValue(process.index_range);
    const rows = [
      metric("已观察样本", fmtNumber(process.observed_count, 0), "不是无间断 Path"),
      metric("锁定后新增", fmtNumber(process.post_lock_observed_count, 0), `后续样本实际跨度 ${fmtMsAge(process.post_lock_span_ms)}；不含锁定前等待`),
      metric("样本索引范围", rangeText, "中间包未逐一重放；样本索引不等于完整路径"),
      metric("首个资料源时点", lockedTime(process.first_ms), "可能早于用户锁定保存时间"),
      metric("最近资料源时点", lockedTime(process.last_ms), "页面读取不会新增采样"),
      metric("最大间隙", fmtMsAge(process.max_gap_ms), "有间隙时不能宣称连续过程"),
    ].join("");
    return `<details class="locked-process" data-disclosure-key="${escapeHtml(`locked::${lockedEpisodeId(comparison.episode)}::process`)}"><summary>已观察过程与覆盖</summary>
      ${process.summary_cn ? `<p>${escapeHtml(displayText(process.summary_cn))}</p>` : ""}
      <div class="metric-grid">${rows}</div>
      ${observations.length ? `<p><strong>最近观察样本</strong></p><ul>${observations.slice(-8).map((item) => `<li>${escapeHtml(eventText(item))}</li>`).join("")}</ul>` : `<p class="muted">没有足够过程样本；仍只按 t0/t1 端点阅读。</p>`}
      ${gaps.length ? `<p><strong>采样缺口</strong></p><ul>${gaps.slice(-8).map((item) => `<li>${escapeHtml(eventText(item))}</li>`).join("")}</ul>` : ""}
      ${otherEvents.length ? `<p><strong>其他事件</strong></p><ul>${otherEvents.slice(0, 6).map((item) => `<li>${escapeHtml(eventText(item))}</li>`).join("")}</ul>` : ""}
    </details>`;
  }

  function lockedExplanationText(value) {
    const object = asObject(value);
    if (!Object.keys(object).length) return "";
    const accepted = asObject(object.accepted_review);
    const questions = asObject(object.current_questions);
    const wpem = asObject(object.wpem_summary);
    return joinTextParts([
      firstPresent(object.summary_cn, object.text_cn, accepted.summary_cn, accepted.text_cn, ""),
      firstPresent(asObject(accepted.q1).summary_cn, asObject(accepted.current_questions).q1_summary_cn, ""),
      firstPresent(asObject(accepted.q2).summary_cn, asObject(accepted.current_questions).q2_summary_cn, ""),
      firstPresent(asObject(questions.q1).summary_cn, asObject(questions.q1).text_cn, questions.q1_summary_cn, ""),
      firstPresent(asObject(questions.q2).summary_cn, asObject(questions.q2).text_cn, questions.q2_summary_cn, ""),
      firstPresent(wpem.summary_cn, wpem.text_cn, wpem.q1_summary_cn, wpem.q2_summary_cn, ""),
    ].filter(Boolean));
  }

  function lockedExplanationClock(value) {
    const object = asObject(value);
    const accepted = asObject(object.accepted_review);
    const binding = asObject(firstPresent(accepted.display_binding, object.display_binding, {}));
    return firstPresent(
      accepted.decision_asof_ms,
      accepted.reviewed_at_ms,
      accepted.accepted_at_ms,
      accepted.data_asof_ms,
      object.decision_asof_ms,
      object.asof_ms,
      binding.frozen_at_ms,
      binding.reviewed_at_ms,
      binding.consumed_at_ms,
      ""
    );
  }

  function renderLockedExplanationPanel(title, value, emptyText) {
    const object = asObject(value);
    const accepted = asObject(object.accepted_review);
    const hasAccepted = Object.keys(accepted).length > 0;
    const text = lockedExplanationText(object);
    const acceptedAt = lockedExplanationClock(object);
    const questions = asObject(object.current_questions);
    const wpem = asObject(object.wpem_summary);
    const q1 = firstPresent(asObject(questions.q1).summary_cn, asObject(questions.q1).text_cn, questions.q1_summary_cn, "");
    const q2 = firstPresent(asObject(questions.q2).summary_cn, asObject(questions.q2).text_cn, questions.q2_summary_cn, "");
    const wpemText = firstPresent(wpem.summary_cn, wpem.text_cn, wpem.path_cn, wpem.compensation_cn, "");
    return `<section>
      <h3>${escapeHtml(title)}</h3>
      ${text ? `<p>${escapeHtml(displayText(text))}</p>` : `<p class="muted">${escapeHtml(emptyText)}</p>`}
      ${hasAccepted ? `<p class="muted">已有与该点绑定的合格解释；评审时点 ${escapeHtml(lockedTime(acceptedAt, "未提供"))}。</p>` : `<p class="muted">该点没有合格模型解释；下方为程序两问与公开原文。</p>`}
      ${q1 || q2 ? `<ul>${q1 ? `<li>Q1：${escapeHtml(displayText(q1))}</li>` : ""}${q2 ? `<li>Q2：${escapeHtml(displayText(q2))}</li>` : ""}</ul>` : ""}
      ${wpemText ? `<p class="muted">WPEM：${escapeHtml(displayText(wpemText))}</p>` : ""}
      ${rawFold("", publicLockedValue(object), `查看${title}公开原文`)}
    </section>`;
  }

  function renderLockedExplanations(comparison) {
    const explanations = asObject(comparison.explanations);
    if (!Object.keys(explanations).length) return "";
    const baseline = asObject(explanations.baseline);
    const latest = asObject(explanations.latest);
    return `<details class="locked-explanations" data-disclosure-key="${escapeHtml(`locked::${lockedEpisodeId(comparison.episode)}::explanations`)}"><summary>解释与审计</summary>
      <div class="locked-explanation-grid">
        ${renderLockedExplanationPanel("锁定时观点", baseline, "锁定时未综合评审；程序对照仍可显示。")}
        ${renderLockedExplanationPanel("最近观点", latest, "暂无当前模型解释；不拿旧观点当新回答。")}
      </div>
    </details>`;
  }

  function renderLockedFullEvidenceFold(comparison) {
    const episodeId = lockedEpisodeId(comparison.episode);
    const evidenceUrl = lockedEvidenceUrl(comparison);
    return remoteRawFold(`locked::${episodeId}::payload`, evidenceUrl, "完整快照与来源 · 按需读取");
  }

  function lockedGapReason(reason) {
    const labels = {
      contract_expired_settlement_unverified: "合同已到期，交割结果未核验",
      sample_budget_exhausted: "已达到样本保留上限，暂停新增样本",
      byte_budget_exhausted: "已达到存储容量上限，暂停新增样本",
      gap_index_budget_exhausted: "缺口索引容量已满，保留累计记录并暂停",
      point_budget_exhausted: "单份资料容量超过上限",
      index_write_failed: "本地记录写入失败，最近成功资料仍保留",
      capture_failed: "本轮未能取得原合同资料",
    };
    return labels[reason] || (/^[a-z_]+$/i.test(String(reason)) ? "本轮资料未完整取得，具体原因见审计原文" : displayText(reason));
  }

  function renderLockedStatusNotes(comparison) {
    const episode = asObject(comparison.episode);
    const process = asObject(comparison.process);
    const sampling = asObject(firstPresent(episode.sampling, process.sampling, comparison.sampling, {}));
    const gapReason = firstPresent(episode.last_gap_reason, process.last_gap_reason, sampling.last_gap_reason, "");
    const collectorError = firstPresent(episode.collector_error, process.collector_error, sampling.collector_error, "");
    const notes = [
      gapReason ? `最近缺口：${lockedGapReason(gapReason)}` : "",
      collectorError ? `采样异常：${lockedGapReason(collectorError)}` : "",
    ].filter(Boolean);
    return notes.length ? `<div class="callout warn"><strong>采样状态</strong><p>${escapeHtml(notes.join("；"))}</p></div>` : "";
  }

  function renderLockedEpisodeDetail(comparison, { exportMode = false } = {}) {
    const object = asObject(comparison);
    const episode = asObject(object.episode);
    const id = lockedEpisodeId(episode) || lockedEpisodeId(object);
    if (!Object.keys(object).length) return empty("尚未读取这张锁定保单的对照详情。");
    const status = firstPresent(episode.status, object.status, "ACTIVE");
    const stopped = /STOP/i.test(String(status));
    const expired = /EXPIRE/i.test(String(status));
    const buttonsDisabled = state.lockedMutationBusy ? " disabled" : "";
    return `<article class="locked-detail" id="${escapeHtml(lockedEpisodeDomId(id))}">
      <header class="locked-detail-head">
        <div>
          <p class="eyebrow">本单跟踪 · Locked Candidate</p>
          <h2>${escapeHtml(lockedEpisodeTitle({ ...episode, fixed_contract: object.fixed_contract }))}</h2>
          <p class="muted">锁定参考不等于成交、开仓批准或账户持仓；停止跟踪也不是平仓。</p>
        </div>
        <div class="badge-row">${badge(lockedStatusLabel(status), lockedStatusTone(status))}${badge("显式操作才评审", "warn")}</div>
      </header>
      ${stopped ? `<div class="callout warn"><strong>已停止跟踪</strong><p>停止记录只表示不再常规更新，不代表已经平仓或已核验到期结果。</p></div>` : ""}
      ${expired ? `<div class="callout warn"><strong>已到期或接近到期</strong><p>未核验交割结果时只显示“结果未核验”，不补造实时零值。</p></div>` : ""}
      ${renderLockedStatusNotes(object)}
      ${renderLockedIdentity(object)}
      ${renderLockedOverview(object)}
      <section class="locked-rows"><h3>前后对照表</h3>${renderLockedRows(object)}</section>
      ${renderLockedProcess(object)}
      ${renderLockedExplanations(object)}
      ${renderLockedFullEvidenceFold(object)}
      ${exportMode ? "" : `<div class="action-row locked-actions">
        <button type="button" class="locked-export-button" data-locked-export="${escapeHtml(id)}"${buttonsDisabled}>导出当前两端点</button>
        <button type="button" class="locked-review-button" data-locked-review="${escapeHtml(id)}"${buttonsDisabled}>综合评审最新点</button>
        <button type="button" class="locked-new-reference-button" data-locked-new-reference="${escapeHtml(id)}"${buttonsDisabled}>另存一份参考</button>
        <button type="button" class="locked-stop-button" data-locked-stop="${escapeHtml(id)}"${buttonsDisabled}>停止跟踪</button>
      </div>`}
    </article>`;
  }

  function lockedExportCss() {
    return `body{margin:0;padding:24px;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;color:#111;background:#fff;line-height:1.6}h1,h2,h3,p{margin-top:0}.muted{color:#666}.badge{display:inline-flex;border:1px solid #ddd;border-radius:999px;padding:3px 9px;margin:2px;color:#555}.metric-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));border:1px solid #ddd;border-radius:8px;overflow:hidden;margin:10px 0}.metric{padding:12px;border-right:1px solid #ddd;border-bottom:1px solid #ddd}.metric span{display:block;color:#666;font-size:12px;font-weight:700}.metric strong{display:block;margin-top:4px}.locked-table-wrap{overflow:auto;border:1px solid #ddd;border-radius:8px}.locked-table{width:100%;min-width:760px;border-collapse:collapse}.locked-table th,.locked-table td{border-top:1px solid #ddd;padding:9px;text-align:left;vertical-align:top}.locked-table span{display:block;color:#666;font-size:12px;margin-top:3px}.callout{border-left:3px solid #bbb;background:#f7f7f7;padding:10px 12px;margin:12px 0}details{border-top:1px solid #ddd;margin-top:12px;padding-top:8px}summary{cursor:pointer;font-weight:700}pre{white-space:pre-wrap;overflow:auto;background:#f7f7f7;padding:12px}`;
  }

  function lockedExportHtml(comparison, fullEvidence = null) {
    const capture = lockedCapture(comparison, fullEvidence);
    const publicComparison = asObject(capture.displayed_comparison);
    const title = lockedEpisodeTitle({ ...asObject(publicComparison.episode), fixed_contract: asObject(publicComparison.fixed_contract) });
    let content;
    renderingOfflineExport = true;
    try { content = renderLockedEpisodeDetail(publicComparison, { exportMode: true }); }
    finally { renderingOfflineExport = false; }
    const jsonText = JSON.stringify(capture).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
    return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${escapeHtml(title)} · 本单跟踪导出</title><style>${lockedExportCss()}</style></head><body>
      <h1>${escapeHtml(title)} · 本单跟踪导出</h1>
      <p class="muted">离线包固定导出点击时的 t0/t1 与一份完整公开证据；离线阅读不会新增采样、不会调用 LLM 或账户操作。</p>
      ${content}
      <details><summary>完整公开 JSON</summary><pre>${jsonText}</pre></details>
    </body></html>`;
  }

  async function downloadLockedExport(episodeId) {
    const comparison = asObject(state.lockedComparisons[episodeId]);
    if (!Object.keys(comparison).length) {
      state.lockedMessage = "请先打开该本单跟踪详情，再导出当前两端点。";
      renderLockedCandidates();
      return;
    }
    state.lockedMessage = "正在读取导出所需的精确完整证据；不会回落到新的 latest。";
    renderLockedCandidates();
    try {
      const fullEvidence = await fetchLockedEvidence(comparison);
      const html = lockedExportHtml(comparison, fullEvidence);
      const url = URL.createObjectURL(new Blob([html], { type: "text/html;charset=utf-8" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `locked-candidate-${String(episodeId).replace(/[^a-z0-9_-]/gi, "_").slice(0, 48)}-${Date.now()}.html`;
      link.click();
      deferUi(() => URL.revokeObjectURL(url), 1000);
      state.lockedMessage = "已生成离线 HTML；导出固定当前 t0/t1 精确证据，不新增采样、不会调用 LLM。";
    } catch (error) {
      state.lockedError = error && error.message ? error.message : String(error);
      state.lockedMessage = "导出失败；为避免混入不同 latest，未生成离线包。";
    } finally {
      renderLockedCandidates();
    }
  }

  function beginLockedReview(episodeId) {
    const comparison = asObject(state.lockedComparisons[episodeId]);
    if (!Object.keys(comparison).length) {
      state.lockedMessage = "请先读取本单跟踪详情，再对最新点显式综合评审。";
      renderLockedCandidates();
      return;
    }
    const latest = asObject(comparison.latest);
    const baseline = asObject(comparison.baseline);
    const candidate = asObject(firstPresent(latest.public_candidate, baseline.public_candidate, {}));
    const candidateIdValue = firstPresent(candidate.candidate_id, asObject(comparison.episode).candidate_id, "");
    const snapshotId = firstPresent(latest.snapshot_id, candidate.snapshot_id, baseline.snapshot_id, "");
    if (!candidateIdValue || !snapshotId) {
      state.lockedMessage = "本单跟踪缺少可评审的 candidate/snapshot 身份；不会拼接旧候选。";
      renderLockedCandidates();
      return;
    }
    state.pendingCandidateId = candidateIdValue;
    state.pendingReviewPurpose = "review";
    state.pendingLockedEpisodeId = episodeId;
    state.pendingLockedReviewSnapshotId = snapshotId;
    state.reviewMessage = "准备对已锁定保单的最新截面做显式综合评审；这不会覆盖 t0 基线。";
    if (!sessionAuthenticated()) {
      showTokenDialogFor("review", "需要先建立私人访问会话，才能对已锁定保单最新点做显式综合评审。");
      return;
    }
    return postReview();
  }

  function beginNewLockedReference(episodeId) {
    const comparison = asObject(state.lockedComparisons[episodeId]);
    const latest = asObject(comparison.latest);
    const baseline = asObject(comparison.baseline);
    const candidate = asObject(firstPresent(latest.public_candidate, baseline.public_candidate, {}));
    const candidateIdValue = firstPresent(candidate.candidate_id, asObject(comparison.episode).candidate_id, "");
    const snapshotId = firstPresent(latest.snapshot_id, candidate.snapshot_id, baseline.snapshot_id, "");
    if (state.lockedMutationBusy || state.pendingTokenAction === "lock") return;
    state.pendingLockCandidateId = candidateIdValue;
    state.pendingLockOptions = { source_episode_id: episodeId, snapshot_id: snapshotId, new_reference: true, idempotency_key: makeIdempotencyKey() };
    if (!sessionAuthenticated()) {
      showTokenDialogFor("lock", "需要先建立私人访问会话，才能从旧单最新截面另存新参考。");
      return;
    }
    postLockCandidate();
  }

  function renderObservationLineage(candidate) {
    const display = candidateReviewDisplay(candidate);
    const receipt = asObject(display.consumer_binding_receipt);
    const lineage = asObject(display.observation_lineage);
    if (!Object.keys(receipt).length) return "";
    const key = `${candidateDisclosureKey(candidate)}::observation-lineage`;
    const primaryModel = receipt.primary_source === "current_accepted";
    const primaryTitle = primaryModel ? "主卡采用当前同版综合解释" : "主卡采用程序事实底座";
    const binding = asObject(receipt.current_binding);
    const historical = asObject(receipt.historical_binding);
    const rejected = asObject(receipt.rejected_binding);
    const replay = asObject(asObject(state.workbench).observation_replay);
    const analysis = asObject(receipt.analysis_identity);
    const originalAnalysis = asObject(replay.original_analysis_identity);
    const selectedAnalysisSnapshot = firstPresent(binding.snapshot_id, historical.snapshot_id, rejected.snapshot_id);
    const originalAnalysisSnapshot = firstPresent(originalAnalysis.snapshot_id, originalAnalysis.historical_snapshot_id);
    const sameOriginalAnalysis = originalAnalysisSnapshot && originalAnalysisSnapshot === selectedAnalysisSnapshot;
    const analysisTitle = primaryModel ? "主卡同版解释" : (Object.keys(historical).length ? "历史参考解释" : "拒绝尝试");
    const sourceName = item => firstPresent(item.label_cn, sourceClockProductLabel(item.product_id), sourceClockProductLabel(asObject(item.identity).product_id), item.source, "来源记录");
    const sourceRows = asArray(lineage.source_records).map(item => {
      const mapping = asObject(item.source_to_input_mapping);
      const refs = asArray(mapping.input_refs);
      const forward = refs.length ? `${fmtNumber(refs.length, 0)} 条资料进入本次分析输入` : "未留精确输入对应；不能据此判作未使用";
      const observedAt = asObject(item.time).observation_end_ms;
      const sourceTime = Number.isFinite(Number(observedAt)) && Number(observedAt) > 0 ? fmtTime(observedAt) : "未留存";
      const purpose = firstPresent(item.usage_cn, item.purpose_cn, item.note_cn, item.proof_limit_cn, "冻结来源指针；不据此证明外部源准确");
      return `<li><strong>${escapeHtml(sourceName(item))}</strong><small>原观察 ${escapeHtml(sourceTime)} · ${escapeHtml(forward)}</small><small>${escapeHtml(displayText(purpose))}</small>${rawFold("", item)}</li>`;
    }).join("");
    const evidenceItems = asArray(lineage.evidence).map(raw => {
      const item = asObject(raw);
      const ref = firstPresent(item.ref, item.evidence_ref, "未记录引用");
      const referenced = item.referenced === true || asArray(item.referenced_by).length > 0;
      const lane = firstPresent(item.lane, lineage.lane, "");
      const historyText = lane.includes("historical") ? "历史分析输入" : (lane.includes("reject") ? "拒绝回执输入，正文未采纳" : "实际模型输入");
      return { item, ref, referenced, historyText, group: evidenceGroupLabel(ref) };
    });
    const evidenceRows = evidenceItems.filter(row => row.referenced).map(({ item, ref, historyText }) =>
      `<li><strong>${escapeHtml(displayToken(firstPresent(item.label_cn, ref), "证据"))}</strong><small>${escapeHtml(historyText)} · 被本次解释引用 · 已显示或折叠可达</small>${rawFold("", item)}</li>`
    ).join("");
    const unreferenced = evidenceItems.filter(row => !row.referenced);
    const unreferencedSummary = unreferenced.length ? groupedCountSummary(unreferenced, row => row.group) : "";
    const unreferencedRows = unreferenced.map(({ item, ref, historyText }) =>
      `<li><strong>${escapeHtml(displayToken(firstPresent(item.label_cn, ref), "未引用资料"))}</strong><small>${escapeHtml(historyText)} · 进入输入但未被本次解释直接引用</small>${rawFold("", item)}</li>`
    ).join("");
    const programRows = asArray(lineage.program_points).map(item => `<li><strong>${escapeHtml(String(item.question || "").toUpperCase())}</strong> ${escapeHtml(firstPresent(item.text_cn, item.text, "程序字段"))}<details><summary>程序 owner、冻结字段与边界</summary><pre>${escapeHtml(JSON.stringify(item, null, 2))}</pre></details></li>`).join("");
    const checkRows = asArray(replay.checks).map(row => `<li><strong>${escapeHtml(displayToken(row.edge, "重放核对"))}</strong> · ${escapeHtml(label(row.status))}<small>${escapeHtml(row.reason_cn || "")}</small>${rawFold("", row)}</li>`).join("");
    const limits = [...new Set([...asArray(lineage.proof_limits), ...asArray(replay.proof_limits)].map(item => typeof item === "string" ? displayText(item) : firstPresent(item.reason_cn, item.note_cn, "原始限制见技术信息")))];
    const inputOmissions = asArray(lineage.input_omissions);
    return `<section class="observation-lineage ia-audit">
      <p><strong>${escapeHtml(primaryTitle)}</strong><br>${escapeHtml(receipt.reason_cn || display.reason_cn)}</p>
      <p class="muted">资料时间 ${escapeHtml(fmtTime(receipt.data_asof_ms))}；当前页面已生成主卡，研究与审计折叠可达。未记录用户是否已阅读；折叠可达不等于用户已阅读。</p>
      ${Object.keys(rejected).length ? `<p>最近拒绝尝试仅保留输入审计与失败原因；失败正文不在主卡。${escapeHtml(displayText(firstPresent(rejected.reason_cn, "")))}</p>` : ""}
      ${Object.keys(historical).length ? `<p>旧解释在“此前有效评审”折叠区；${escapeHtml(displayText(receipt.historical_reason_cn || "与当前输入不同，不回填当前判断。"))}</p>` : ""}
      <p>事实与来源 → 并列的 Space / WPEM / Economics / Context → 本单关系 → 实际输入 → 原始判断与验收 → 本页消费。该顺序用于追查信息，不表示严格串行依赖。</p>
      <details class="ia-raw" data-disclosure-key="${escapeHtml(`${key}::identity`)}"><summary>查看技术身份</summary>
        ${primaryModel ? "" : '<p>当前主卡为程序事实底座，不对应 LLM 请求。历史解释与拒绝尝试各自保留原身份。</p>'}
        ${Object.keys(analysis).length ? `<p>${escapeHtml(analysisTitle)}的原模型：${escapeHtml(firstPresent(analysis.requested_model, analysis.model, "未在该回执留存"))}；原 Prompt：${escapeHtml(firstPresent(analysis.prompt_version, analysis.prompt_id, sameOriginalAnalysis ? originalAnalysis.prompt : null, "未在这份回执留存，不能用当前 Prompt 回填"))}。</p>` : ""}
        <p>本次 renderer：${escapeHtml(RENDERER_IDENTITY)}；主卡 LLM 输入：${escapeHtml(primaryModel ? shortId(binding.evidence_input_hash) : "无（程序底座）")}。</p>
        <pre>${escapeHtml(JSON.stringify(receipt, null, 2))}</pre>
        ${Object.keys(originalAnalysis).length ? `<details><summary>原冻结请求的模型、Prompt 与协议</summary><p class="muted">身份属于下列原快照与原验收结果；不以本次 renderer 的配置替代历史分析。</p><pre>${escapeHtml(JSON.stringify({analysis: originalAnalysis, archived_renderer: replay.original_renderer, original_consumed_at_ms: replay.original_consumed_at_ms}, null, 2))}</pre></details>` : ""}
      </details>
      ${primaryModel ? "" : (programRows ? `<details data-disclosure-key="${escapeHtml(`${key}::program`)}"><summary>程序 Q1/Q2 如何连接冻结事实</summary><ul class="wpem-audit-list">${programRows}</ul></details>` : '<p class="muted">本包未留存程序逐点来源映射；可从合同、费用与 WPEM 原字段核对，不能伪称已有逐点证据。</p>')}
      ${evidenceItems.length ? `<details data-disclosure-key="${escapeHtml(`${key}::evidence`)}"><summary>证据进入与引用情况</summary><p class="muted">系统已生成、已进入本次分析、被本次解释引用、已显示在当前页面分开记录。未引用不等于没有参与推理，也不表示不重要。</p>${evidenceRows ? `<ul class="wpem-audit-list">${evidenceRows}</ul>` : ""}${unreferenced.length ? `<details><summary>另有 ${escapeHtml(fmtNumber(unreferenced.length, 0))} 条输入未被直接引用 · ${escapeHtml(unreferencedSummary)}</summary><ul class="wpem-audit-list">${unreferencedRows}</ul></details>` : ""}</details>` : '<p class="muted">本单没有可绑定的实际模型输入；程序资料可阅读，不能把已有资料标成已交给模型。</p>'}
      ${inputOmissions.length ? rawFold(`${key}::omissions`, inputOmissions, "查看未进入输入的原始记录") : ""}
      ${sourceRows ? `<details data-disclosure-key="${escapeHtml(`${key}::sources`)}"><summary>冻结来源目录 · 向前追查</summary><p class="muted">目录只证明这些来源记录被留存；是否进入本次输入以记录中的精确关联为准。未留字段映射时不推断整份原件被使用或删除。</p><ul class="wpem-audit-list">${sourceRows}</ul></details>` : ""}
      ${checkRows ? `<details data-disclosure-key="${escapeHtml(`${key}::replay`)}"><summary>离线重放核对结果</summary><ul class="wpem-audit-list">${checkRows}</ul><p>重放不调用 provider 或实时行情。${replay.first_divergence ? escapeHtml(`首个不一致：${JSON.stringify(replay.first_divergence)}`) : "未报告冻结派生不一致；这不证明原始市场来源或解释正确。"}</p></details>` : ""}
      <details><summary>反馈出现偏差时，从哪一层查起</summary><ol><li>先保存本版：核对 candidate、snapshot、主卡回执或程序底座身份，确认不是拿旧解释比较新行情。</li><li>从有疑问的 Q1/Q2 点展开依据：原事实或时点不对，先查来源与 canonical；事实对但关系不对，查对应 Space/WPEM/Economics/Attribution。</li><li>关系已生成却未进入输入，查整理与序列化；输入充分但解释无依据，查模型 final 与原验收。</li><li>已采纳原文与主卡不同，查 selector 和 renderer。事后结果不佳本身不能证明哪层失真。</li></ol></details>
      ${limits.length ? `<details><summary>追溯边界</summary><ul>${limits.map(text => `<li>${escapeHtml(text)}</li>`).join("")}</ul></details>` : ""}
      ${rawFold(`${key}::hash-dictionary`, lineage.hash_dictionary || {}, "查看 Hash 名称与可比较对象")}
      <div class="action-row"><button type="button" class="observation-download" data-candidate-id="${escapeHtml(candidateId(candidate))}">保存这版公开复盘包</button><span class="observation-download-status" role="status"></span></div>
      <p class="muted">本地 JSON 保留本版消费投影、公开事实与引用；不自动上传或记录行为。完整来源复算使用私有冻结 bundle；本包不含 provider 原始响应、私有推理或凭据。</p>
    </section>`;
  }

  function renderWpemAuditDetails(candidate, modeLabel) {
    const release = workbenchReleaseIdentity();
    const summary = asObject(candidate.wpem_summary);
    const evidence = asObject(candidate.wpem_evidence);
    const validity = displayReviewValidity(candidate);
    const questions = asObject(candidate.current_questions);
    const audit = asObject(questions.audit);
    const riskStatus = asObject(firstPresent(evidence.risk_status, evidence.risk_reference_status, candidate.risk_reference_status, audit.risk, {}));
    const window = asObject(evidence.same_candidate_window);
    const qualification = asObject(window.qualification);
    const sourceBindings = asObject(firstPresent(evidence.source_bindings, candidate.source_bindings, {}));
    const windowEvidence = asObject(sourceBindings.window_evidence);
    const summaryStatus = wpemSummaryStatusText(summary);
    const catalog = asObject(evidence.counter_catalog_gap_reassess);
    const catalogReason = firstPresent(
      catalog.gap_reason,
      catalog.status,
      asObject(catalog.catalog_policy).status,
      audit.catalog_gap_reason,
      audit.catalog_status,
    );
    const rows = [
      release.release_id || release.status || release.manifest_hash ? metric("Release 身份", shortId(release.release_id), `${label(release.status)}；manifest ${shortId(release.manifest_hash)}`) : "",
      validity.status ? metric("Review 有效性", label(validity.status), firstPresent(validity.reason_cn, validity.reason, validity.checked_at_ms ? fmtTime(validity.checked_at_ms) : "")) : "",
      evidence.schema || evidence.evidence_hash ? metric("WPEM 证据包", shortId(evidence.evidence_hash || evidence.review_evidence_hash), label(evidence.schema)) : "",
      qualification.status || window.source_window_evidence_hash ? metric("窗口身份", label(qualification.status || window.window_status), `source ${shortId(window.source_window_evidence_hash || windowEvidence.window_evidence_hash)}`) : "",
      summaryStatus ? metric("窗口/增量状态", "审计记录", summaryStatus) : "",
      riskStatus.mu_M_null_reason || audit.mu_M_null_reason ? metric("μ/M 审计", label(firstPresent(riskStatus.mu_M_null_reason, audit.mu_M_null_reason)), "主视图不把缺失 μ/M 显示为数值") : "",
      catalogReason ? metric("目录审计", label(catalogReason), "只显示有限目录已有状态；页面不重试 LLM") : "",
    ].filter(Boolean).join("");
    const componentLabels = releaseComponentLabels(release.components, { raw: true });
    const componentRows = componentLabels.length
      ? `<p class="muted">组件：${escapeHtml(componentLabels.join("；"))}</p>`
      : "";
    const coverageAudit = renderCoverageAudit(window, sourceBindings);
    const bindingAudit = renderWpemBindingAudit(candidate, evidence, validity, riskStatus, sourceBindings);
    const evidenceAudit = renderWpemEvidenceAudit(candidate, evidence, summary, window, sourceBindings);
    const rawAudit = componentRows || bindingAudit || evidenceAudit;
    if (!rows && !componentRows && !bindingAudit && !evidenceAudit && !coverageAudit && !modeLabel) return "";
    return `<details class="wpem-audit"><summary>Audit · WPEM 证据、窗口与风险审计</summary>
      ${modeLabel ? `<p class="muted">${escapeHtml(modeLabel)}</p>` : ""}
      <div class="metric-grid">${rows}</div>
      ${coverageAudit}
      ${rawAudit ? `<details class="wpem-raw-audit ia-raw"><summary>完整来源与协议记录</summary>${componentRows}${bindingAudit}${evidenceAudit}</details>` : ""}
    </details>`;
  }

  function renderStaleReviewFold(candidate) {
    const display = candidateReviewDisplay(candidate);
    const historical = asObject(display.historical);
    if (Object.keys(historical).length) {
      const reason = firstPresent(reviewDisplayRelation(historical, reviewResultPayload(historical)).reason_cn, display.reason_cn, historical.reason_cn, "当前 snapshot 已更新；旧回执只作历史参考。");
      const payload = { ...historical, historical_only: true, review: { ...reviewResultPayload(historical), historical_only: true } };
      const identity = reviewReceiptIdentity(historical, reviewResultPayload(historical));
      return `<details class="wpem-stale-review" data-disclosure-key="${escapeHtml(`${candidateDisclosureKey(candidate)}::history::${identity.snapshotId || ""}::${identity.receiptId || ""}`)}"><summary>此前有效评审 · 历史参考</summary>
        ${renderReviewResultDetail(payload, { inlineBody: true, reason_cn: reason })}
      </details>`;
    }
    const review = candidateReview(candidate);
    const validity = asObject(candidate.review_validity);
    if (validity.status !== "STALE" || !Object.keys(review).length) return "";
    const title = review.status === "RESEARCH_OK" ? "此前有效评审 · 历史参考" : "旧评审已过期，仅折叠保留";
    return `<details class="wpem-stale-review" data-disclosure-key="${escapeHtml(`${candidateDisclosureKey(candidate)}::stale-history`)}"><summary>${escapeHtml(title)}</summary>
      <p class="historical-reading-context">历史参考 · 原冻结资料 · 非当前判断</p>
      <p class="muted">${escapeHtml(firstPresent(validity.reason_cn, validity.reason, "旧回执不解释当前候选。"))}</p>
      ${review.summary_cn ? `<p>${escapeHtml(review.summary_cn)}</p>` : ""}
      ${renderTwoQuestions(review, { historical: true })}
    </details>`;
  }

  function renderCandidateWpemPanel(candidate, contractDetails = "") {
    const review = sameCandidateCurrentReview(candidate);
    const questions = asObject(candidate.current_questions);
    const validity = displayReviewValidity(candidate);
    const hasProgramQuestions = Object.keys(asObject(questions.q1)).length && Object.keys(asObject(questions.q2)).length;
    const hasCandidateWpem = Object.keys(asObject(candidate.wpem_summary)).length
      || Object.keys(asObject(candidate.wpem_evidence)).length
      || Object.keys(questions).length
      || Object.keys(validity).length;
    if (!hasCandidateWpem && !Object.keys(review).length) return "";
    const summary = renderWpemSummary(candidate, review);
    const riskUnavailable = renderRiskReferenceUnavailableNotice(candidate);
    const research = renderDecisionResearch(candidate, review, contractDetails);
    const researchLibrary = renderCandidateResearchLibrary(candidate, [research || (renderConsumerResearch(candidate) + contractDetails)]);
    const audit = renderWpemAuditDetails(candidate, Object.keys(review).length ? "当前同版 LLM 回执；证据包只作审计回放。" : (hasProgramQuestions ? "程序事实归因；无 LLM、无胜率、无交易许可。" : ""));
    const auditLibrary = renderCandidateAuditLibrary(candidate, [renderObservationLineage(candidate), renderTwoQuestions(review, { auditOnly: true }), renderStaleReviewFold(candidate), audit, renderCandidateEvidenceFold(candidate)]);
    if (Object.keys(review).length) {
      return `<div class="wpem-panel is-current-review">
        <p class="muted review-binding-meta">当前同版综合解释；下方为阅读显示，原文与完整依据在审计中可查。</p>
        ${renderTwoQuestions(review, { primaryOnly: true })}${summary}${riskUnavailable}${researchLibrary}${auditLibrary}
      </div>`;
    }
    if (hasProgramQuestions) {
      return `<div class="wpem-panel is-program">
        <div class="badge-row">${badge(label(questions.source_kind || "deterministic_program_no_llm"), "blue")}</div>
        <div class="callout warn"><strong>当前无合格模型综合解释</strong><p>以下为程序事实底座；历史 accepted 只在折叠区作为参考，rejected 正文不会进入判断区。</p></div>
        ${renderProgramQuestions(candidate)}${summary}${riskUnavailable}${researchLibrary}${auditLibrary}
      </div>`;
    }
    if (validity.status === "STALE") {
      return `<div class="wpem-panel is-stale">${summary}${researchLibrary}${auditLibrary}</div>`;
    }
    return summary || auditLibrary || researchLibrary ? `<div class="wpem-panel">${summary}${researchLibrary}${auditLibrary}</div>` : "";
  }

  function renderSelectedReviewReceiptSummary(result) {
    if (!result || !Object.keys(result).length) return "";
    const matched = reviewDisplayRelation(result, reviewResultPayload(result));
    if (matched.relation === "current" && matched.candidate) return renderCurrentReceiptLocation(matched.candidate);
    const review = reviewPayload(result);
    const status = firstPresent(result.status, review.status, "未提供");
    const reviewedAt = firstPresent(result.reviewed_at_ms, review.reviewed_at_ms, result.recorded_ts_ms, review.recorded_ts_ms);
    const key = ["selected-review", firstPresent(result.candidate_id, review.candidate_id, ""), firstPresent(result.snapshot_id, review.reviewed_snapshot_id, ""), reviewedAt || ""].filter(Boolean).join("::");
    return `<details class="review-receipt-audit" data-disclosure-key="${escapeHtml(key)}"><summary>查看当前保单同版回执原文与目录</summary>
      <p class="muted">状态 ${escapeHtml(label(status))}；时间 ${escapeHtml(fmtTime(reviewedAt))}。主视图已显示两问，这里只作回执审计。</p>
      ${renderReviewResultDetail(result)}
    </details>`;
  }

  function renderCurrentReceiptLocation(candidate) {
    return `<p class="review-receipt-location">当前同版回执已归入该保单的审计区。<a href="#${encodeURIComponent(`candidate-audit-${candidateId(candidate)}`)}">查看本单回执与原文</a>；Q1/Q2 在主卡阅读。</p>`;
  }

  function reviewFrozenContractSummary(result, review) {
    const trace = asObject(firstPresent(review.decision_trace, asObject(asObject(review.normalized).decision_trace), result.decision_trace, {}));
    const contractInput = asObject(asArray(trace.input_evidence).find((item) => traceEvidenceRef(item) === "contract"));
    const facts = asObject(contractInput.facts);
    const frozenEvidence = asObject(review.wpem_evidence);
    const snapshotId = firstPresent(result.snapshot_id, review.reviewed_snapshot_id, result.reviewed_snapshot_id);
    const frozenRow = asObject(asArray(asObject(frozenEvidence.current_cross_section).cross_section_rows).find(row => row.snapshot_id === snapshotId));
    const contract = Object.keys(facts).length ? facts : asObject(frozenRow.contract);
    if (!Object.keys(contract).length) return "";
    const binding = reviewDisplayBinding(result, review);
    const frozenAt = firstPresent(binding.frozen_at_ms, review.decision_asof_ms, frozenEvidence.decision_asof_ms, contract.decision_asof_ms);
    const shortLeg = firstPresent(contract.short_instrument, safeNumber(firstPresent(contract.short_strike_usd, contract.short_strike)) !== null ? fmtUsd(firstPresent(contract.short_strike_usd, contract.short_strike)) : "");
    const longLeg = firstPresent(contract.long_instrument, safeNumber(firstPresent(contract.long_strike_usd, contract.long_strike)) !== null ? fmtUsd(firstPresent(contract.long_strike_usd, contract.long_strike)) : "");
    return [
      contract.side ? `${label(contract.side)} 旧保单` : "旧保单",
      `卖出腿 ${firstPresent(shortLeg, "未提供")} / 保护腿 ${firstPresent(longLeg, "未提供")}`,
      `到期 ${fmtTime(contract.expiry_ms)}`,
      frozenAt ? `frozen ${fmtTime(frozenAt)}` : "",
    ].filter(Boolean).join(" · ");
  }

  function renderCandidate(candidate) {
    const snapshot = asObject(candidate.snapshot);
    const contract = asObject(firstPresent(snapshot.contract, candidate.contract, {}));
    const market = asObject(snapshot.market);
    const quote = asObject(firstPresent(market.quote, snapshot.quote, candidate.quote, {}));
    const fee = asObject(snapshot.fee);
    const discovery = asObject(firstPresent(snapshot.discovery, candidate.discovery, market.discovery, {}));
    const economics = asObject(firstPresent(snapshot.economics, snapshot.accounting, candidate.economics, {}));
    const risk = asObject(firstPresent(snapshot.risk, candidate.risk, {}));
    const liability = asObject(firstPresent(snapshot.liability, candidate.liability, {}));
    const review = asObject(firstPresent(candidate.review, snapshot.review, snapshot.llm_review, {}));
    const candidatePreview = isSyntheticPreview() || asObject(snapshot.data_identity).kind === "synthetic_fixture";
    const snapshotIdentity = asObject(snapshot.data_identity);
    const candidateReplay = isReplayPreview() || String(firstPresent(snapshotIdentity.mode, snapshotIdentity.kind, snapshotIdentity.source_identity, "") ?? "").toUpperCase().includes("REPLAY");
    const gate = reviewGate(candidate);
    const reviewBusy = manualReviewBusy();
    const gateEnabled = gate.enabled === true && secureReviewOrigin();
    const enabled = gateEnabled && !reviewBusy;
    const reasons = asArray(gate.reasons || gate.reason_codes).slice();
    if (!secureReviewOrigin()) reasons.push("访问验证与写操作只允许在 HTTPS 或本机页面提交");
    const id = candidateId(candidate);
    const disclosureKey = candidateDisclosureKey(candidate);
    const side = label(firstPresent(candidate.side, snapshot.side, contract.side, "候选"));
    const mRef = firstPresent(
      economics.m_ref_btc,
      economics.reference_margin_btc,
      economics.margin_btc,
      snapshot.m_ref_btc,
    );
    const valuationSpotUsd = firstPresent(economics.valuation_spot_usd, market.valuation_spot_usd, snapshot.valuation_spot_usd);
    const shortPremiumBtc = economics.short_premium_btc;
    const shortPremiumUsd = economics.short_premium_usd_at_spot;
    const protectionCostBtc = economics.long_premium_btc;
    const protectionCostUsd = economics.long_premium_usd_at_spot;
    const premiumSpreadBtc = economics.premium_spread_btc;
    const premiumSpreadUsd = economics.premium_spread_usd_at_spot;
    const entryFeeBtc = firstPresent(economics.theoretical_two_leg_fee_btc, fee.entry_fee_btc);
    const netFeeUsd = economics.theoretical_two_leg_fee_usd_at_spot;
    const netCreditAfterFeeBtc = economics.net_credit_after_entry_fee_btc;
    const netCreditAfterFeeUsd = economics.net_credit_after_entry_fee_usd_at_spot;
    const totalHoldFeeBtc = fee.amount_btc;
    const totalHoldFeeUsd = economics.total_hold_fee_usd_at_spot;
    const deliveryFeeBtc = fee.delivery_fee_btc;
    const deliveryFeeUsd = economics.delivery_fee_usd_at_spot;
    const holdingNetBtc = economics.net_credit_after_fee_btc;
    const holdingNetUsd = economics.net_credit_after_fee_usd_at_spot;
    const expiryBreakeven = firstPresent(liability.expiry_breakeven_usd, economics.expiry_breakeven_usd, liability.breakeven_expiry_usd);
    const breakevenRoots = safeNumber(expiryBreakeven) !== null
      ? [expiryBreakeven]
      : (Array.isArray(liability.breakeven_usd)
        ? liability.breakeven_usd.filter((item) => safeNumber(item) !== null)
        : (safeNumber(liability.breakeven_usd) !== null ? [liability.breakeven_usd] : []));
    const breakevenText = breakevenRoots.length ? breakevenRoots.map(fmtUsd).join(" / ") : "未提供";
    const breakevenNote = breakevenRoots.length > 1
      ? "两处均为本组合到期盈亏平衡价。"
      : "本组合到期盈亏平衡价；不列非本组合冗余点。";
    const remainingText = firstPresent(
      contract.remaining_to_expiry_text,
      snapshot.remaining_to_expiry_text,
      market.remaining_to_expiry_text,
      safeNumber(firstPresent(candidate.remaining_to_expiry_ms, contract.remaining_to_expiry_ms, snapshot.remaining_to_expiry_ms, market.remaining_to_expiry_ms)) === null
        ? undefined
        : fmtMsAge(firstPresent(candidate.remaining_to_expiry_ms, contract.remaining_to_expiry_ms, snapshot.remaining_to_expiry_ms, market.remaining_to_expiry_ms)),
    );
    const shortDelta = firstPresent(
      contract.short_leg_delta_abs,
      contract.short_abs_delta,
      quote.short_leg_delta_abs,
      market.short_leg_delta_abs,
      discovery.short_abs_delta,
      discovery.short_leg_delta_abs,
      snapshot.short_leg_delta_abs,
      contract.short_delta_abs,
    );
    const shortDeltaTs = firstPresent(
      contract.short_leg_delta_ts_ms,
      quote.short_leg_delta_ts_ms,
      market.short_leg_delta_ts_ms,
      discovery.short_leg_delta_ts_ms,
      discovery.short_delta_observed_at_ms,
      snapshot.short_leg_delta_ts_ms,
    );
    const reviewStatus = firstPresent(review.status, review.state, review.result, "not_requested");
    const reviewAt = firstPresent(review.reviewed_at_ms, review.reviewed_at, review.created_at_ms);
    const gaps = [...new Set([
      ...asArray(candidate.gaps),
      ...asArray(snapshot.gaps),
      ...asArray(snapshot.gap_reasons),
      ...asArray(discovery.gap_reasons),
      ...asArray(risk.gap_reasons),
    ].filter(Boolean).map((item) => typeof item === "string" ? item : JSON.stringify(item)))];
    const tailNote = String(contract.side || candidate.side).toLowerCase() === "put"
      && liability.btc_contract_cap == null
      ? "币本位 Put 深尾赔付没有固定 BTC 上限；本页仅展示开仓前可计算的到期理论责任。"
      : `Call 币本位赔付在保护腿附近达到峰值，保护腿外随交割价上涨下降；合同 BTC 上限 ${fmtBtc(liability.btc_contract_cap)}。`;
    const payoutRows = asArray(liability.payout_table).slice(0, 6).map((row) =>
      `${fmtUsd(row.settlement_usd)} → 赔付 ${fmtBtc(row.payout_btc)}`).join("；");
    const remainingLabel = candidateReplay
      ? "冻结时点剩余到期时间"
      : (candidatePreview ? "模拟时点剩余到期时间" : "当前剩余到期时间");
    const buttonTitle = reviewBusy ? "同版因果评审正在推理，双侧按钮暂时禁用" : (enabled ? (candidatePreview ? "本地模拟评审回执" : "显式综合评审当前候选；不建立持续跟踪基线") : (reasons.length ? reasons.map((item) => label(item)).join("；") : "服务端未允许评审"));
    const buttonText = reviewBusy ? "正在推理" : (candidatePreview ? "模拟评审回执" : "综合评审");
    const observation = asObject(candidate.observation_review);
    const canObserve = observation.enabled === true && secureReviewOrigin() && !reviewBusy && !candidateReplay && !candidatePreview;
    const observationTitle = canObserve ? "基于本单已有资料解释事实、变化与冲突；研究回执不授予交易许可" : asArray(observation.reasons).map(label).join("；");
    const riskMuRef = firstPresent(economics.mu_ref_btc, economics.risk_mu_ref_btc, risk.mu_ref_btc);
    const expiryAt = firstPresent(contract.expiry_ms, contract.expiry, snapshot.expiry_ms);
    const display = candidateReviewDisplay(candidate);
    const displayCurrent = reviewResultPayload(asObject(display.current));
    const displayBinding = reviewDisplayBinding(asObject(display.current), displayCurrent);
    const dataAsOfBrief = firstPresent(display.data_asof_ms, snapshot.decision_asof_ms, snapshot.asof_ms, market.decision_asof_ms);
    const explanationAtBrief = firstPresent(displayBinding.frozen_at_ms, displayCurrent.decision_asof_ms, displayCurrent.data_asof_ms, asObject(displayCurrent.wpem_evidence).decision_asof_ms);
    const currentReviewForBadge = sameCandidateCurrentReview(candidate);
    const hasProgramQuestionsForBadge = Object.keys(asObject(asObject(candidate.current_questions).q1)).length && Object.keys(asObject(asObject(candidate.current_questions).q2)).length;
    const badgeText = Object.keys(currentReviewForBadge).length
      ? "当前综合解释"
      : (hasProgramQuestionsForBadge ? "程序事实底座" : (candidatePreview ? (enabled ? "可演示评审" : "只读模拟比较") : (enabled ? "可手动评审" : (canObserve ? "可观测解释" : "评审入口关闭"))));
    const badgeTone = Object.keys(currentReviewForBadge).length ? "good" : (hasProgramQuestionsForBadge ? "blue" : (candidatePreview || canObserve ? "warn" : (enabled ? "good" : "bad")));
    const sameTimeBrief = dataAsOfBrief && explanationAtBrief && fmtTime(dataAsOfBrief) === fmtTime(explanationAtBrief);
    const timeBrief = sameTimeBrief
      ? `资料与解释依据时点：${fmtTime(dataAsOfBrief)}`
      : [
        `资料时点：${fmtTime(dataAsOfBrief)}`,
        Object.keys(currentReviewForBadge).length ? `解释依据时点：${fmtTime(explanationAtBrief)}` : "",
      ].filter(Boolean).join("；");
    const briefMetrics = [
      metricClass("is-net-credit", "建仓净信用", fmtBtc(netCreditAfterFeeBtc), "权利金差 − 建仓费用；未成交"),
      metric("权利金差", fmtBtc(premiumSpreadBtc), "顺序腿估值，未成交"),
      safeNumber(entryFeeBtc) !== null ? metric("建仓费用", fmtBtc(entryFeeBtc), "两腿理论成交手续费，未成交") : "",
    ].filter(Boolean).join("");
    const lockedSummary = lockedSummaryForCandidate(candidate);
    const lockedSummaryId = lockedEpisodeId(lockedSummary);
    const lockBusy = state.lockedMutationBusy || state.pendingTokenAction === "lock";
    const lockAction = lockedSummaryId
      ? `<a class="locked-candidate-link" href="${escapeHtml(lockedEpisodeHash(lockedSummaryId))}" data-locked-open="${escapeHtml(lockedSummaryId)}">查看已锁定跟踪</a>`
      : `<button type="button" class="lock-candidate-button" data-candidate-id="${escapeHtml(id)}" ${id && secureReviewOrigin() && !lockBusy ? "" : "disabled"} title="保存当前候选为锁定参考；不等于成交或开仓批准">锁定参考并跟踪</button>`;
    const contractDetails = `<details class="candidate-details" data-disclosure-key="${escapeHtml(disclosureKey)}"><summary>展开合同、费用拆分、责任表与来源说明</summary>
      <div class="candidate-body">
        <p class="muted">${escapeHtml(candidatePreview ? "本地模拟候选；来源是合成或预登记输入，不授予当前事实、账户或交易许可；评审来源以回执标识为准。" : (candidateReplay ? "冻结回放候选；真实公开来源的历史截面，非当前交易或执行许可，原始标识只保存在底层证据中。" : (candidate.status === "snapshot" ? "当前服务端候选；原始标识只保存在底层证据中。" : "本侧尚未形成合格保单；请看缺口与反证。")))}</p>
        <div class="kv-grid" aria-label="${escapeHtml(side)} 合同责任">
          ${kv("卖出腿", firstPresent(contract.short_instrument, contract.short_leg, "未提供"), `行权价 ${fmtUsd(firstPresent(contract.short_strike_usd, contract.short_strike))}`)}
          ${kv("保护腿", firstPresent(contract.long_instrument, contract.long_leg, "未提供"), `行权价 ${fmtUsd(firstPresent(contract.long_strike_usd, contract.long_strike))}`)}
          ${kv("数量", fmtBtc(firstPresent(contract.quantity_btc, snapshot.quantity_btc, candidate.quantity_btc)), `腿宽 ${fmtUsd(firstPresent(contract.width_usd, contract.width))}`)}
          ${kv("到期", fmtTime(firstPresent(contract.expiry_ms, contract.expiry, snapshot.expiry_ms)), `${remainingLabel}：${remainingText || "未提供"}`)}
          ${kv("估值现价", fmtUsd(valuationSpotUsd), "用于服务端 USD 折合；前端不自行换算")}
          ${kv("卖出腿 Delta", fmtDelta(shortDelta), safeNumber(shortDelta) === null ? "未提供；本卡无合格卖出腿 Delta 证据" : `采集 ${fmtTime(shortDeltaTs)}；目标 |Delta|≈0.35，且不超过 0.5`)}
          ${kv("责任起点", fmtUsd(firstPresent(liability.starts_at_usd, liability.start_payoff_usd, contract.short_strike_usd)), `保护腿 ${fmtUsd(firstPresent(liability.protection_at_usd, contract.long_strike_usd))}`)}
          ${kv("到期盈亏平衡价", breakevenText, breakevenNote)}
        </div>

        <div class="kv-grid" aria-label="${escapeHtml(side)} 补偿账">
          ${kv("卖出腿权利金", fmtBtcWithUsd(shortPremiumBtc, shortPremiumUsd), "服务端字段；理论盘口，未成交")}
          ${kv("保护腿成本", fmtBtcWithUsd(protectionCostBtc, protectionCostUsd), "服务端字段；理论盘口，未成交")}
          ${kv("权利金差", fmtBtcWithUsd(premiumSpreadBtc, premiumSpreadUsd), "卖出腿权利金 − 保护腿权利金")}
          ${kv("建仓费用 f_entry", fmtBtcWithUsd(entryFeeBtc, netFeeUsd), "两腿理论成交手续费；来自服务端 entry 字段")}
          ${kv("净信用", fmtBtcWithUsd(netCreditAfterFeeBtc, netCreditAfterFeeUsd), "权利金差 − 双腿理论成交手续费；Deribit 顺序分腿盘口只是理论估算，未成交")}
          ${kv("持有到期总费用 f_total", fmtBtcWithUsd(totalHoldFeeBtc, totalHoldFeeUsd), "fee.amount_btc；含已列明建仓与交割费用")}
          ${safeNumber(deliveryFeeBtc) !== null && deliveryFeeBtc !== 0 ? kv("到期交割费用假设", fmtBtcWithUsd(deliveryFeeBtc, deliveryFeeUsd), "与双腿理论成交手续费分列；持有到期账仍需扣除") : ""}
          ${safeNumber(deliveryFeeBtc) !== null && deliveryFeeBtc !== 0 ? kv("持有到期扣全费用信用", fmtBtcWithUsd(holdingNetBtc, holdingNetUsd), "用于持有到期责任账和 M_ref") : ""}
          ${kv("冻结 μ_ref", fmtBtc(firstPresent(economics.mu_ref_btc, economics.risk_mu_ref_btc, risk.mu_ref_btc)), candidatePreview ? (safeNumber(economics.mu_ref_btc) === null ? "模拟参考缺失" : "模拟参考已提供，非真实支持资格") : label(firstPresent(risk.status, risk.support, "风险参考状态未知")))}
          ${kv("参考所需权利金", fmtBtc(firstPresent(economics.required_credit_btc, economics.minimum_reference_credit_btc)), "冻结 μ_ref + 建仓及交割总费用")}
          ${kv("参考补偿余量 M_ref", fmtBtc(mRef), "权利金差 − 冻结 μ_ref − 建仓及交割总费用；前端不复算")}
          ${kv("同版评审", label(reviewStatus), reviewAt ? `时间 ${fmtTime(reviewAt)}；由服务端绑定当前快照` : "尚无绑定当前快照的人工评审")}
        </div>

        ${renderReadingGuidance(candidate)}

        <div class="callout ${enabled ? "good" : "warn"}">
          <strong>${escapeHtml(reviewBusy ? "手动评审正在推理" : (enabled ? (candidatePreview ? "本地模拟评审演示" : "服务端允许手动综合评审") : "主评审入口关闭"))}</strong>
          <p>${escapeHtml(reviewBusy ? "服务端已有运行中回执，页面只读轮询；Put/Call 两侧按钮同步禁用。" : (enabled ? (candidatePreview ? "点击只会调用本机模拟回执；零真实 LLM、非交易、不会产生当前事实许可。" : "综合评审只请求同版一次因果审查，不建立持续跟踪基线；LLM 不改写 c、μ_ref、f 或 M_ref。") : buttonTitle))}</p>
        </div>

        <div class="callout"><strong>责任与尾部</strong><p>${escapeHtml(tailNote)}</p>${payoutRows ? `<p class="muted">支付表：${escapeHtml(payoutRows)}</p>` : ""}</div>

        ${gaps.length ? `<div class="callout bad"><strong>缺口/反证</strong><p>${escapeHtml(gaps.map((item) => label(item)).join("；"))}</p></div>` : ""}

      </div>
      </details>`;
    const wpemPanel = renderCandidateWpemPanel(candidate, contractDetails);
    return `<article class="candidate" data-candidate-id="${escapeHtml(id)}">
      <div class="candidate-header">
        <div class="candidate-title">
          <h3>${escapeHtml(side)} 候选</h3>
          ${badge(badgeText, badgeTone)}
        </div>
        <p class="muted">${escapeHtml(candidatePreview ? "本地模拟 · 合成或预登记输入，评审来源见回执。" : (candidateReplay ? "真实公开资料的冻结回放 · 非当前行情。" : (candidate.status === "snapshot" ? "当前服务端候选" : "本侧尚未形成合格保单；请看缺口与反证。")))}</p>
        ${timeBrief ? `<p class="candidate-time muted">${escapeHtml(timeBrief)} · UTC+8</p>` : ""}
      </div>
      <div class="candidate-brief">
        <p class="candidate-position"><strong>卖出腿 ${escapeHtml(firstPresent(contract.short_instrument, "未提供"))}</strong><br>保护腿 ${escapeHtml(firstPresent(contract.long_instrument, "未提供"))}<br><span class="muted">${escapeHtml(fmtBtc(contract.quantity_btc))} · 到期 ${escapeHtml(fmtTime(expiryAt))} · ${escapeHtml(remainingLabel)} ${escapeHtml(remainingText || "未提供")}</span></p>
        <div class="metric-grid candidate-compensation" aria-label="候选核心参数">
          ${briefMetrics}
        </div>
        <p class="candidate-liability-position"><strong>本单责任位置</strong>：资料参考价 ${escapeHtml(fmtUsd(market.reference_price_usd))}；卖出腿 ${escapeHtml(fmtUsd(firstPresent(contract.short_strike_usd, contract.short_strike)))}；保护腿 ${escapeHtml(fmtUsd(firstPresent(contract.long_strike_usd, contract.long_strike)))}；到期盈亏平衡${breakevenRoots.length > 1 ? "（两处均属于本组合）" : ""} ${escapeHtml(breakevenText)}。位置不表示安全或执行许可。</p>
        ${!Object.keys(asObject(candidate.current_questions)).length && !Object.keys(asObject(candidate.wpem_evidence)).length && asObject(candidate.review_validity).status !== "STALE" ? `<p class="muted">${escapeHtml(`同版评审：${label(reviewStatus)}`)}${review.summary_cn ? `；${escapeHtml(review.summary_cn)}` : ""}</p>` : ""}
        ${wpemPanel}
        <p class="muted">${escapeHtml(tailNote)} ${escapeHtml(liability.account_margin_btc == null ? "账户保证金与维护约束未进入本账。" : "账户保证金参考不证明维护约束已满足。")}执行许可关闭。</p>
        <div class="action-row candidate-research-actions">
          ${lockAction}
          ${!candidatePreview && !candidateReplay && Object.keys(observation).length ? `<button type="button" class="review-button" data-review-purpose="observation" data-candidate-id="${escapeHtml(id)}" ${canObserve && id ? "" : "disabled"} title="${escapeHtml(observationTitle)}">解释本单观测资料</button>` : ""}
          <button type="button" class="primary review-button" data-candidate-id="${escapeHtml(id)}" ${enabled && id ? "" : "disabled"} title="${escapeHtml(buttonTitle)}">${escapeHtml(buttonText)}</button>
          <button type="button" class="candidate-map-button" data-candidate-id="${escapeHtml(id)}">查看本单地图</button>
        <a class="candidate-review-link" data-candidate-id="${escapeHtml(id)}" href="#reviewStateTitle">查看两问与回执</a>
        </div>
      </div>
      ${wpemPanel ? "" : contractDetails}
    </article>`;
  }

  const DISCLOSURE_STORAGE_KEY = "astra-v261-disclosures";
  const DISCLOSURE_STORAGE_FORBIDDEN = /(?:api[_-]?key|authorization|auth|token|password|secret|credentials?|private[_-]?key|source[_-]?code|source-code|_source|source_bindings|source_records)/i;

  function disclosureStorageSafeText(value) {
    const text = String(value ?? "").trim();
    if (!text || DISCLOSURE_STORAGE_FORBIDDEN.test(text)) return "";
    return text.length <= 4096 ? text : "";
  }

  function safeDisclosureKeys(keys) {
    return Array.from(keys).map(disclosureStorageSafeText).filter(Boolean);
  }

  function disclosureScopeKey() {
    const lockedId = lockedRouteEpisodeId();
    if (lockedId) return `locked::${disclosureStorageSafeText(lockedId)}`;
    if (lockedRouteMode() === "list") return "locked-list";
    const preview = asObject(state.workbench && state.workbench.preview);
    const candidates = sortedCandidates();
    if (!candidates.length) return "";
    const ids = candidates.map((candidate) => {
      const snapshot = asObject(candidate.snapshot);
      return [candidateId(candidate), snapshot.snapshot_id].map(disclosureStorageSafeText);
    });
    if (ids.some(identity => identity.some(value => !value))) return "";
    return JSON.stringify([firstPresent(preview.scenario_id, preview.mode, preview.kind, ""), ids]);
  }

  function disclosureStorageRead() {
    try {
      const storage = window.sessionStorage;
      if (!storage) return { scope: "", keys: [] };
      return asObject(JSON.parse(storage.getItem(DISCLOSURE_STORAGE_KEY) || "{}"));
    } catch (_error) {
      return { scope: "", keys: [] };
    }
  }

  function disclosureStorageWrite(keys) {
    try {
      const storage = window.sessionStorage;
      const scope = disclosureScopeKey();
      if (!storage || !scope) return;
      storage.setItem(DISCLOSURE_STORAGE_KEY, JSON.stringify({ scope, keys: safeDisclosureKeys(keys).slice(0, 300), scrollY: window.scrollY || 0, hash: disclosureStorageSafeText(window.location.hash) }));
    } catch (_error) {
      /* ignore storage failures; DOM state remains authoritative for this render */
    }
  }

  function storedDisclosureKeys() {
    const stored = disclosureStorageRead();
    const scope = disclosureScopeKey();
    return scope && stored.scope === scope ? new Set(safeDisclosureKeys(asArray(stored.keys))) : new Set();
  }

  function openDisclosureKeys(root) {
    return new Set(safeDisclosureKeys(Array.from(root.querySelectorAll("details[open][data-disclosure-key]"), (item) => item.dataset.disclosureKey)));
  }

  function restoreDisclosureState(root, previousKeys) {
    const scope = disclosureScopeKey();
    const prior = scope && (!state.disclosureScope || state.disclosureScope === scope) ? previousKeys : [];
    const keys = new Set([...storedDisclosureKeys(), ...prior]);
    root.querySelectorAll("details[data-disclosure-key]").forEach((item) => {
      item.open = keys.has(item.dataset.disclosureKey);
    });
  }

  function replaceRawPlaceholder(details, html) {
    const placeholder = details.querySelector?.(".raw-placeholder");
    if (placeholder) placeholder.remove();
    details.insertAdjacentHTML?.("beforeend", html);
  }

  async function hydrateRawFold(details) {
    if (!details || !details.open || details.dataset.rawHydrated === "true" || details.dataset.rawLoading === "true") return;
    const rawId = details.dataset.rawFoldId || "";
    const remoteUrl = details.dataset.remoteJsonUrl || "";
    if (!rawId && !remoteUrl) return;
    details.dataset.rawLoading = "true";
    try {
      let value;
      if (remoteUrl) {
        value = await fetchLockedJson(remoteUrl);
      } else if (rawFoldCache.has(rawId)) {
        value = rawFoldCache.get(rawId);
      } else {
        value = { unavailable_cn: "本次渲染的原始对象已从前端有界缓存回收；请刷新或重新打开该详情。" };
      }
      const json = JSON.stringify(publicLockedValue(value), null, 2);
      const previewLimit = 65536;
      const large = json.length > previewLimit;
      const access = large ? `<p class="muted">原件较大，页面仅预览前 ${fmtNumber(previewLimit, 0)} 个字符；预览不是完整 JSON。</p>${remoteUrl
        ? `<a href="${escapeHtml(remoteUrl)}" download="astra-public-evidence.json">下载完整 JSON</a>`
        : '<button type="button" data-save-full-json>下载完整 JSON</button>'}` : "";
      replaceRawPlaceholder(details, `${access}<pre tabindex="0" aria-label="${large ? "原始信息预览" : "原始信息"}">${escapeHtml(large ? json.slice(0, previewLimit) : json)}</pre>`);
      const save = details.querySelector?.("[data-save-full-json]");
      if (save) save.addEventListener("click", () => {
        const url = URL.createObjectURL(new Blob([json], {type: "application/json;charset=utf-8"}));
        const link = document.createElement("a");
        link.href = url;
        link.download = "astra-public-evidence.json";
        document.body.appendChild(link);
        link.click();
        link.remove();
        deferUi(() => URL.revokeObjectURL(url), 1000);
      });
      details.dataset.rawHydrated = "true";
    } catch (error) {
      replaceRawPlaceholder(details, `<p class="muted">完整原件读取失败：${escapeHtml(error && error.message ? error.message : String(error))}</p>`);
    } finally {
      delete details.dataset.rawLoading;
    }
  }

  function hydrateOpenRawFolds(root) {
    root.querySelectorAll?.("details.ia-raw[open]").forEach((item) => {
      hydrateRawFold(item);
    });
  }

  function bindDisclosurePersistence(root) {
    if (typeof document.querySelectorAll === "function") {
      const liveIds = new Set(Array.from(document.querySelectorAll("[data-raw-fold-id]"), node => node.dataset.rawFoldId));
      for (const key of rawFoldCache.keys()) if (!liveIds.has(key)) rawFoldCache.delete(key);
    }
    root.querySelectorAll("details[data-disclosure-key]").forEach((item) => {
      hydrateRawFold(item);
      item.addEventListener("toggle", () => {
        hydrateRawFold(item);
        const stored = storedDisclosureKeys();
        const rootKeys = Array.from(root.querySelectorAll("details[data-disclosure-key]"), (node) => node.dataset.disclosureKey).filter(Boolean);
        rootKeys.forEach((key) => stored.delete(key));
        openDisclosureKeys(root).forEach((key) => stored.add(key));
        disclosureStorageWrite(stored);
      });
    });
    root.querySelectorAll?.("details.ia-raw:not([data-disclosure-key])").forEach((item) => {
      hydrateRawFold(item);
      item.addEventListener("toggle", () => hydrateRawFold(item));
    });
    hydrateOpenRawFolds(root);
  }

  function restoreGlobalDisclosures() {
    const keys = storedDisclosureKeys();
    [$("#researchLibrary"), $("#auditLibrary")].filter(Boolean).forEach(item => {
      item.open = keys.has(item.dataset.disclosureKey);
      if (item.dataset.persistenceBound) return;
      item.dataset.persistenceBound = "true";
      item.addEventListener("toggle", () => {
        const current = storedDisclosureKeys();
        if (item.open) current.add(item.dataset.disclosureKey);
        else current.delete(item.dataset.disclosureKey);
        disclosureStorageWrite(current);
      });
    });
  }

  function openAncestorDetails(target) {
    let node = target && target.parentElement;
    while (node) {
      if (node.tagName && node.tagName.toLowerCase() === "details") node.open = true;
      node = node.parentElement;
    }
  }

  function openAnchorTarget(target, { scroll = false } = {}) {
    if (!target) return false;
    openAncestorDetails(target);
    target.classList?.add("ia-anchor-target");
    if (scroll && typeof target.scrollIntoView === "function") {
      deferUi(() => target.scrollIntoView({ block: "start" }), 0);
    }
    return true;
  }

  function findHashTarget() {
    const rawHash = String(window.location.hash || "");
    if (!rawHash || rawHash === "#") return null;
    let id = rawHash.slice(1);
    try {
      id = decodeURIComponent(id);
    } catch (_error) {
      id = rawHash.slice(1);
    }
    const escaped = window.CSS && typeof window.CSS.escape === "function" ? window.CSS.escape(id) : id.replace(/"/g, '\\"');
    try {
      return document.getElementById(id) || document.querySelector(`[name="${escaped}"]`);
    } catch (_error) {
      return document.getElementById(id);
    }
  }

  function syncHashTarget({ scroll = false } = {}) {
    const target = findHashTarget();
    const opened = openAnchorTarget(target, { scroll });
    if (opened) {
      state.pendingHashOpen = false;
      if (scroll) state.pendingHashScroll = false;
    }
    return opened;
  }

  function renderLockedCandidates() {
    const target = $("#lockedCandidatesArea");
    if (!target) return;
    const expanded = openDisclosureKeys(target);
    const mode = lockedRouteMode();
    document.body?.classList.toggle("is-locked-route", mode !== "compact");
    const routeId = lockedRouteEpisodeId();
    if (routeId && state.activeLockedEpisodeId !== routeId) state.activeLockedEpisodeId = routeId;
    const activeId = state.activeLockedEpisodeId;
    const previousDetail = target.querySelector(".locked-detail");
    const sameDetail = mode === "detail" && previousDetail?.id === lockedEpisodeDomId(activeId);
    const previousTable = sameDetail ? previousDetail.querySelector(".locked-table-wrap") : null;
    const tablePosition = previousTable ? { top: previousTable.scrollTop, left: previousTable.scrollLeft } : null;
    const readingY = sameDetail ? window.scrollY : null;
    const comparison = activeId ? asObject(state.lockedComparisons[activeId]) : {};
    const summaries = asArray(state.lockedEpisodes);
    const compactHtml = `<div class="locked-compact">
      <div>
        <strong>${escapeHtml(fmtNumber(summaries.length, 0))} 张已锁定保单</strong>
        <p class="muted">锁定参考单独保存，综合评审仍需显式发起。</p>
      </div>
      <a href="#lockedCandidates">查看本单跟踪</a>
    </div>`;
    if (mode === "compact") {
      target.innerHTML = `${compactHtml}
        ${state.lockedMessage ? `<p class="muted">${escapeHtml(state.lockedMessage)}</p>` : ""}
        ${state.lockedError ? `<div class="callout bad"><strong>本单跟踪状态</strong><p>${escapeHtml(state.lockedError)}</p></div>` : ""}`;
      restoreDisclosureState(target, expanded);
      bindDisclosurePersistence(target);
      return;
    }
    const listHtml = summaries.length
      ? `<div class="locked-summary-list">${summaries.map(renderLockedSummaryCard).join("")}</div>`
      : empty("尚未锁定任何保单。点击候选卡里的“锁定参考并跟踪”会保存 t0，不调用 LLM。");
    const activeSummary = activeId ? lockedEpisodeSummary(activeId) : {};
    const activeHtml = activeId
      ? (Object.keys(comparison).length
        ? renderLockedEpisodeDetail(comparison)
        : `<article class="locked-detail" id="${escapeHtml(lockedEpisodeDomId(activeId))}">${empty(`本单跟踪 ${shortId(activeId)} 尚未读取。`)}</article>`)
      : "";
    const switcher = summaries.length ? `<nav class="locked-switcher" aria-label="切换本单跟踪">
      <a href="#lockedCandidates">返回跟踪列表</a>
      <a href="#candidatesTitle">返回当前候选</a>
      ${summaries.slice(0, 8).map((item) => {
        const id = lockedEpisodeId(item);
        return id && id !== activeId ? `<a href="${escapeHtml(lockedEpisodeHash(id))}" data-locked-open="${escapeHtml(id)}">${escapeHtml(label(firstPresent(item.side, asObject(item.contract).side, "本单")))} · ${escapeHtml(lockedTime(item.created_at_ms))}</a>` : "";
      }).join("")}
    </nav>` : "";
    const head = `<div class="locked-headline">
      <div>
        <h3>锁定参考与持续观察</h3>
        <p class="muted">详情固定同一合同的 t0/t1，不把锁定参考写成成交或持仓。</p>
      </div>
      <div class="badge-row">
        ${badge(`${fmtNumber(summaries.length, 0)} 个已锁定`, summaries.length ? "blue" : "warn")}
        ${state.lockedLoading ? badge("读取中", "warn") : badge("只读查看", "blue")}
      </div>
    </div>`;
    target.innerHTML = `${mode === "list" ? head : ""}
      ${state.lockedMessage && !state.lockedMessage.startsWith("已读取") ? `<div class="callout">${escapeHtml(state.lockedMessage)}</div>` : ""}
      ${state.lockedError ? `<div class="callout bad"><strong>本单跟踪读取失败</strong><p>${escapeHtml(state.lockedError)}</p></div>` : ""}
      ${activeId && Object.keys(activeSummary).length && !Object.keys(comparison).length ? `<p class="muted">当前路由指向 ${escapeHtml(lockedEpisodeTitle(activeSummary))}；点击“查看跟踪”会读取公开对照。</p>` : ""}
      ${mode === "list" ? listHtml : ""}
      ${mode === "detail" ? `${switcher}${activeHtml}` : ""}`;
    restoreDisclosureState(target, expanded);
    const nextTable = tablePosition ? target.querySelector(".locked-table-wrap") : null;
    if (nextTable) {
      nextTable.scrollTop = tablePosition.top;
      nextTable.scrollLeft = tablePosition.left;
    }
    if (Number.isFinite(readingY) && typeof window.scrollTo === "function") deferUi(() => window.scrollTo(0, readingY), 0);
    bindDisclosurePersistence(target);
    target.querySelectorAll("[data-locked-open]").forEach((item) => {
      item.addEventListener("click", (event) => {
        const id = item.getAttribute("data-locked-open") || "";
        if (!id) return;
        state.activeLockedEpisodeId = id;
        if (!state.lockedComparisons[id]) {
          event.preventDefault();
          window.location.hash = lockedEpisodeHash(id);
          loadLockedEpisode(id);
        }
      });
    });
    target.querySelectorAll("[data-locked-export]").forEach((item) => {
      item.addEventListener("click", () => downloadLockedExport(item.getAttribute("data-locked-export") || ""));
    });
    target.querySelectorAll("[data-locked-review]").forEach((item) => {
      item.addEventListener("click", () => beginLockedReview(item.getAttribute("data-locked-review") || ""));
    });
    target.querySelectorAll("[data-locked-new-reference]").forEach((item) => {
      item.addEventListener("click", () => beginNewLockedReference(item.getAttribute("data-locked-new-reference") || ""));
    });
    target.querySelectorAll("[data-locked-stop]").forEach((item) => {
      item.addEventListener("click", () => stopLockedEpisode(item.getAttribute("data-locked-stop") || ""));
    });
  }

  function renderCandidates() {
    const target = $("#candidateArea");
    if (!target) return;
    const candidates = sortedCandidates();
    if (!candidates.length) {
      target.innerHTML = empty("工作台服务尚未返回 Put/Call 候选。结构门失败时仍应返回可解释的缺口。");
      return;
    }
    const expanded = openDisclosureKeys(target);
    state.observationRenderedAt = Date.now();
    target.innerHTML = `<div class="candidate-grid ${candidates.length === 1 ? "is-single" : ""}">${candidates.map(renderCandidate).join("")}</div>`;
    restoreDisclosureState(target, expanded);
    bindDisclosurePersistence(target);
    document.querySelectorAll(".review-button").forEach((button) => {
      button.addEventListener("click", () => beginReview(button.getAttribute("data-candidate-id"), button.getAttribute("data-review-purpose") || "review"));
    });
    target.querySelectorAll(".lock-candidate-button").forEach((button) => {
      button.addEventListener("click", () => beginLockCandidate(button.getAttribute("data-candidate-id") || ""));
    });
    target.querySelectorAll("[data-locked-open]").forEach((item) => {
      item.addEventListener("click", (event) => {
        const id = item.getAttribute("data-locked-open") || "";
        if (!id) return;
        state.activeLockedEpisodeId = id;
        if (!state.lockedComparisons[id]) {
          event.preventDefault();
          window.location.hash = lockedEpisodeHash(id);
          loadLockedEpisode(id);
        }
      });
    });
    target.querySelectorAll(".candidate-map-button").forEach((button) => {
      button.addEventListener("click", () => {
        if (!selectCandidate(button.getAttribute("data-candidate-id"))) return;
        openAnchorTarget($("#btcMapTitle"), { scroll: true });
      });
    });
    target.querySelectorAll(".candidate-review-link").forEach((link) => {
      link.addEventListener("click", () => {
        selectCandidate(link.getAttribute("data-candidate-id"));
        state.pendingHashOpen = true;
        state.pendingHashScroll = true;
        deferUi(() => syncHashTarget({ scroll: true }), 0);
      });
    });
    target.querySelectorAll(".observation-download").forEach(button => {
      button.addEventListener("click", async () => {
        const candidate = candidates.find(item => candidateId(item) === button.getAttribute("data-candidate-id"));
        const status = button.parentElement.querySelector(".observation-download-status");
        if (!candidate) return;
        if (status) status.textContent = candidateEvidenceUrl(candidate) ? "正在读取完整快照证据…" : "正在保存当前阅读投影…";
        try {
          const primary = button.closest(".candidate").querySelector(".llm-grounded-questions, .wpem-program-questions");
          const capture = await buildObservationCapture(candidate, primary?.innerText || "");
          const url = URL.createObjectURL(new Blob([JSON.stringify(capture, null, 2)], {type: "application/json"}));
          const link = document.createElement("a");
          link.href = url;
          link.download = `observation-${String(asObject(candidate.snapshot).snapshot_id || candidateId(candidate)).replace(/[^a-z0-9_-]/gi, "_").slice(0, 24)}-${capture.captured_at_ms}.json`;
          link.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
          if (status) status.textContent = "已生成本地复盘包，请保留用于后续反馈。";
        } catch (_error) {
          if (status) status.textContent = "保存失败；页面事实仍可阅读，请使用下方身份与引用记录反馈。";
        }
      });
    });
  }

  function currentSnapshotIds() {
    return new Set(sortedCandidates()
      .map((candidate) => asObject(candidate.snapshot).snapshot_id)
      .filter(Boolean));
  }

  function reviewPayload(result) {
    const review = asObject(result.review);
    return Object.keys(review).length ? review : result;
  }

  function reviewList(items, emptyText) {
    const rows = asArray(items).filter(Boolean);
    if (!rows.length) return `<li><span class="muted">${escapeHtml(emptyText)}</span></li>`;
    return rows.map((item) => {
      if (typeof item === "string") return `<li>${escapeHtml(item)}</li>`;
      const object = asObject(item);
      const ref = firstPresent(object.ref, object.source, "依据");
      const sourceNames = {
        "fact_context.market_facts.flow.direction": "量价方向事实",
        "fact_context.market_facts.volume_price.direction": "量价方向事实",
        "fact_context.market_facts.edb_reasoning.lean": "同源因果倾向",
      };
      const source = sourceNames[ref] || (String(ref).startsWith("fmz.") && label(ref) === ref ? "FMZ 截面事实" : label(ref));
      return `<li><strong>${escapeHtml(source)}</strong><span>${escapeHtml(firstPresent(object.claim_cn, object.summary_cn, object.text, ""))}</span></li>`;
    }).join("");
  }

  function traceReadableValue(value) {
    if (value === undefined || value === null || value === "") return "";
    if (typeof value === "string") return displayText(value);
    if (typeof value === "number") return String(value);
    if (typeof value === "boolean") return label(value);
    if (Array.isArray(value)) return value.map(traceReadableValue).filter(Boolean).join("；");
    const object = asObject(value);
    const direct = firstPresent(
      object.text_cn,
      object.summary_cn,
      object.claim_cn,
      object.reason_cn,
      object.label_cn,
      object.value_cn,
      object.path_cn,
      object.compensation_cn,
      object.qualification_cn,
      object.limitations_cn,
    );
    if (direct) return traceReadableValue(direct);
    const status = firstPresent(object.status, object.state, object.qualification_status, object.window_status, object.delta_status, object.path_status);
    return status ? displayText(label(status)) : "";
  }

  function traceClockText(value) {
    if (value === undefined || value === null || value === "") return "";
    const numeric = safeNumber(value);
    if (numeric !== null && numeric <= 0) return "未提供";
    return numeric !== null && numeric > 1_000_000_000_000 ? fmtTime(numeric) : traceReadableValue(value);
  }

  function traceRefs(...groups) {
    const refs = [];
    groups.forEach((group) => {
      asArray(group).forEach((item) => {
        const text = String(item ?? "").trim();
        if (text && !refs.includes(text)) refs.push(text);
      });
    });
    return refs;
  }

  function traceEvidenceRef(item, fallback = "") {
    const object = asObject(item);
    const provided = asObject(object.provided);
    return firstPresent(object.ref, provided.ref, fallback, "");
  }

  function traceEvidenceIndex(items) {
    const index = new Map();
    asArray(items).forEach((item) => {
      const ref = traceEvidenceRef(item);
      if (ref && !index.has(ref)) index.set(ref, item);
    });
    return index;
  }

  function traceField(labelText, value) {
    const text = traceReadableValue(value);
    return text ? `<li><span>${escapeHtml(labelText)}</span><strong>${escapeHtml(text)}</strong></li>` : "";
  }

  function traceTimeField(labelText, value) {
    const text = traceClockText(value);
    return text ? `<li><span>${escapeHtml(labelText)}</span><strong>${escapeHtml(text)}</strong></li>` : "";
  }

  function traceContractLine(facts) {
    const contract = asObject(firstPresent(facts.contract, facts.policy_contract, {}));
    const object = Object.keys(contract).length ? contract : facts;
    const side = firstPresent(object.side, facts.side);
    const shortStrike = firstPresent(object.short_strike_usd, object.short_strike);
    const longStrike = firstPresent(object.long_strike_usd, object.long_strike);
    const shortLeg = firstPresent(object.short_instrument, object.short_leg, safeNumber(shortStrike) !== null ? fmtUsd(shortStrike) : "");
    const longLeg = firstPresent(object.long_instrument, object.long_leg, safeNumber(longStrike) !== null ? fmtUsd(longStrike) : "");
    const parts = [
      side ? label(side) : "",
      shortLeg ? `卖出腿 ${shortLeg}` : "",
      longLeg ? `保护腿 ${longLeg}` : "",
      safeNumber(object.expiry_ms) !== null ? `到期 ${fmtTime(object.expiry_ms)}` : "",
    ].filter(Boolean);
    return parts.length ? parts.join("；") : "";
  }

  function traceRelationLine(facts, relations) {
    const adverse = firstPresent(facts.adverse_direction_cn, relations.adverse_direction_cn, facts.adverse_direction, relations.adverse_direction);
    const zone = firstPresent(facts.liability_zone, relations.liability_zone, asObject(facts.level).liability_zone, asObject(relations.level).liability_zone);
    const status = firstPresent(facts.status, relations.status, facts.path_status, relations.path_status, facts.delta_status, relations.delta_status);
    return [
      adverse ? `不利方向 ${traceReadableValue(adverse)}` : "",
      zone ? `责任位置 ${traceReadableValue(zone)}` : "",
      status ? `状态 ${traceReadableValue(status)}` : "",
    ].filter(Boolean).join("；");
  }

  function traceScopeText(item, object, facts, relations, mapRecord) {
    return firstPresent(
      facts.scope_cn,
      item.scope_cn,
      object.scope_cn,
      relations.scope_cn,
      item.scope,
      object.scope,
      mapRecord.scope_cn,
      mapRecord.scope,
    );
  }

  function traceQualificationLine(item, facts) {
    const qualification = asObject(firstPresent(item.qualification, facts.qualification, {}));
    const fields = [
      firstPresent(item.qualification_cn, facts.qualification_cn),
      firstPresent(item.qualification_status, facts.qualification_status),
      firstPresent(qualification.status, qualification.window_status),
      qualification.delta_status ? `Delta ${label(qualification.delta_status)}` : "",
      qualification.path_status ? `Path ${label(qualification.path_status)}` : "",
      qualification.risk_status ? `风险 ${label(qualification.risk_status)}` : "",
    ].filter(Boolean);
    return fields.length ? [...new Set(fields.map(traceReadableValue).filter(Boolean))].join("；") : "";
  }

  function traceSourceClockLine(item) {
    const records = asArray(firstPresent(item.source_records, asObject(item.provided).source_records));
    const rows = records.slice(0, 2).map((source) => {
      const object = asObject(source);
      const time = asObject(object.time);
      const usage = asObject(object.usage_qualification);
      const observed = firstPresent(time.observation_end_ms, object.source_observed_ms, object.observed_at_ms);
      const observedNumber = safeNumber(observed);
      return [
        sourceClockProductLabel(firstPresent(object.product_id, object.source_asset, object.source)),
        observedNumber !== null && observedNumber > 0 ? `原观察 ${fmtTime(observed)}` : "",
        firstPresent(usage.status, object.source_clock_status) ? `用途 ${label(firstPresent(usage.status, object.source_clock_status))}` : "",
      ].filter(Boolean).join(" · ");
    }).filter(Boolean);
    return rows.join("；");
  }

  function traceEvidenceSummaryLines(item, object, ref) {
    const facts = asObject(item.facts);
    const relations = asObject(item.relations);
    const mapRecord = asObject(item.record);
    const sourceRecords = asArray(item.source_records);
    const contractLine = traceContractLine(facts);
    const relationLine = traceRelationLine(facts, relations);
    const scopeText = traceScopeText(item, object, facts, relations, mapRecord);
    const qualificationText = traceQualificationLine(item, facts);
    const sourceClockText = traceSourceClockLine(item);
    const lines = [
      traceField("证据", firstPresent(item.text_cn, item.summary_cn, item.claim_cn, item.reason_cn, item.fact, facts.scope_cn, facts.summary_cn, mapRecord.summary_cn)),
      traceField("本单事实", contractLine),
      traceField("责任关系", relationLine),
      traceField("对本单的含义", firstPresent(item.path_cn, item.compensation_cn, item.relationship_cn, relations.scope_cn, relations.comparison_scope, item.basis)),
      traceField("时间尺度", firstPresent(item.horizon, item.window, item.time_horizon, asObject(item.scope).horizon, asObject(scopeText).horizon)),
      traceField("范围", typeof scopeText === "string" ? displayToken(scopeText, "本次冻结范围") : scopeText),
      traceTimeField("资料时点", firstPresent(item.asof, item.asof_ms, item.decision_asof_ms, sourceRecords[0] && asObject(sourceRecords[0].time).observation_end_ms)),
      traceField("资格", qualificationText),
      traceField("来源时钟", sourceClockText),
      traceField("限制", firstPresent(item.limitations_cn, item.limitations, item.breaks_cn, mapRecord.limitations)),
    ].filter(Boolean);
    if (!lines.length) {
      return [traceField("证据", displayToken(ref)), traceField("限制", firstPresent(object.reason_cn, object.reason, "仅保留引用，需展开原始信息核对。"))].filter(Boolean);
    }
    return lines;
  }

  function traceRelationLabel(value) {
    const text = String(value ?? "").toLowerCase();
    return ({
      increased: "增加",
      decreased: "下降",
      unchanged: "不变",
      same: "相同",
      different: "不同",
      true: "是",
      false: "否",
    })[text] || label(value);
  }

  function renderTraceObservedFragments(item) {
    const facts = asObject(item.facts);
    const relations = asObject(item.relations);
    const points = asArray(firstPresent(facts.observed_points, item.observed_points));
    const latest = asObject(firstPresent(relations.latest_adjacent_quote_pair, item.latest_adjacent_quote_pair, facts.latest_adjacent_quote_pair, {}));
    if (!points.length && !Object.keys(latest).length) return "";
    const firstPoint = asObject(points[0]);
    const contract = asObject(firstPoint.contract);
    const contractText = Object.keys(contract).length
      ? `卖出腿 ${firstPresent(contract.short_instrument, "未提供")} / 保护腿 ${firstPresent(contract.long_instrument, "未提供")} · 到期 ${fmtTime(contract.expiry_ms)}`
      : "";
    const pointRows = points.map((rawPoint) => {
      const point = asObject(rawPoint);
      const economics = asObject(point.economics);
      const market = asObject(point.market);
      const clock = asObject(point.source_clock);
      const asof = firstPresent(point.decision_asof_ms, market.valuation_ts_ms, clock.decision_asof_ms, clock.quote_received_ts_ms);
      return `<tr>
        <td>${escapeHtml(traceClockText(asof))}</td>
        <td>${escapeHtml(fmtUsd(market.reference_price_usd))}</td>
        <td>${escapeHtml(fmtBtc(economics.visible_credit_btc))}</td>
        <td>${escapeHtml(fmtBtc(economics.fee_btc))}</td>
        <td>${escapeHtml(fmtBtc(firstPresent(economics.net_credit_after_entry_fee_btc, economics.net_credit_after_fee_btc)))}</td>
      </tr>`;
    }).join("");
    const relationBits = [
      latest.reference_price_relation ? `标的价${traceRelationLabel(latest.reference_price_relation)}` : "",
      latest.visible_credit_relation ? `可见信用${traceRelationLabel(latest.visible_credit_relation)}` : "",
      latest.fee_relation ? `费用${traceRelationLabel(latest.fee_relation)}` : "",
      latest.net_credit_after_fee_relation ? `净信用${traceRelationLabel(latest.net_credit_after_fee_relation)}` : "",
      latest.same_contract_legs !== undefined ? `同合同腿：${traceRelationLabel(latest.same_contract_legs)}` : "",
      latest.same_price_and_quote_basis !== undefined ? `同价格/quote 基础：${traceRelationLabel(latest.same_price_and_quote_basis)}` : "",
    ].filter(Boolean).join("；");
    const adjacentText = Object.keys(latest).length
      ? `相邻观察：${traceClockText(latest.prev_decision_asof_ms)} → ${traceClockText(latest.curr_decision_asof_ms)}；${relationBits || "关系字段未提供"}。`
      : "";
    return `<div class="trace-fragments-readout">
      <p><strong>已观察片段</strong>${contractText ? `<br><span class="muted">${escapeHtml(contractText)}</span>` : ""}</p>
      <p class="muted">局部同合同相邻比较不等于完整 Path；已观察片段≠严格 Delta/Path。</p>
      ${adjacentText ? `<p>${escapeHtml(adjacentText)}</p>` : ""}
      ${pointRows ? `<div class="map-table-wrap" role="region" aria-label="模型所见已观察片段" tabindex="0"><table class="map-table"><thead><tr><th>时间</th><th>标的价</th><th>信用</th><th>费用</th><th>净信用</th></tr></thead><tbody>${pointRows}</tbody></table></div>` : ""}
    </div>`;
  }

  function traceFragmentQualificationText(value) {
    const qualification = asObject(value);
    const rows = [
      qualification.window_status ? `窗口 ${label(qualification.window_status)}` : "",
      qualification.delta_status ? `Delta ${label(qualification.delta_status)}` : "",
      qualification.path_status ? `Path ${label(qualification.path_status)}` : "",
      qualification.risk_status ? `风险 ${label(qualification.risk_status)}` : "",
      qualification.snapshot_status ? `快照 ${label(qualification.snapshot_status)}` : "",
    ].filter(Boolean);
    return rows.length ? rows.join("；") : traceReadableValue(value);
  }

  function renderTraceCleanItem(row, ref, disclosureKey) {
    const object = asObject(row);
    const provided = asObject(object.provided);
    const item = Object.keys(provided).length ? provided : object;
    const itemRef = firstPresent(object.ref, item.ref, ref, "依据");
    const title = firstPresent(
      item.label_cn,
      object.label_cn,
      item.group ? `${label(item.group)} · ${itemRef}` : "",
      itemRef,
    );
    const sourcePath = firstPresent(object.source_path, item.source_path);
    const facts = asObject(item.facts);
    const isWpemFragments = itemRef === "wpem.fragments" || String(sourcePath || "").includes("observed_points") || asArray(facts.observed_points).length > 0;
    const fragmentReadout = isWpemFragments ? renderTraceObservedFragments(item) : "";
    const rows = traceEvidenceSummaryLines(item, object, itemRef).join("");
    const locator = {
      ref: itemRef,
      source_path: sourcePath,
      field_path: firstPresent(object.field_path, item.field_path),
      source: firstPresent(object.source, item.source),
      hash: firstPresent(object.hash, item.hash, object.source_hash, item.source_hash),
      lineage: firstPresent(object.lineage, item.lineage),
      public_input: item,
    };
    return `<div class="trace-cleanitem ia-evidence-summary"><dt>${escapeHtml(displayToken(title, "证据"))}</dt><dd>
      ${fragmentReadout}
      ${rows ? `<ul class="wpem-audit-list">${rows}</ul>` : `<p class="muted">保留本次输入的引用供核对。</p>`}
      ${rawFold(disclosureKey, locator)}
    </dd></div>`;
  }

  function traceLayerRefs(layer, key, refSet) {
    return asArray(asObject(layer)[key]).map((item) => String(item ?? "").trim()).filter((item) => item && refSet.has(item));
  }

  function renderTraceLayerFlow(trace, refs) {
    const refSet = new Set(refs);
    const inputRefs = traceEvidenceIndex(asObject(trace).input_evidence);
    if (!refSet.size) return `<p class="muted">本点未返回独立依据引用；页面不会把段落总引用拆成逐句依据。</p>`;
    const rows = asArray(asObject(trace).layers).map((rawLayer) => {
      const layer = asObject(rawLayer);
      const produced = traceLayerRefs(layer, "produced_refs", refSet);
      const consumed = traceLayerRefs(layer, "consumed_refs", refSet);
      const included = [...new Set([...produced, ...consumed])].filter((ref) => inputRefs.has(ref));
      const omitted = asArray(layer.omitted).map(asObject).filter((item) => refSet.has(String(item.ref ?? "").trim()));
      if (!produced.length && !consumed.length && !omitted.length) return "";
      const title = firstPresent(layer.title_cn, layer.layer, "层级");
      return `<li><strong>${escapeHtml(displayToken(title, "证据层"))}</strong>
        <small>系统已生成 ${escapeHtml(fmtNumber(produced.length, 0))} 条；进入本次分析 ${escapeHtml(fmtNumber(included.length, 0))} 条；被本点引用 ${escapeHtml(fmtNumber(consumed.length, 0))} 条。</small>
        <small>未被本点引用：${escapeHtml(omitted.length ? groupedCountSummary(omitted, (item) => evidenceGroupLabel(item.ref)) : "无")}</small>
        ${rawFold("", { produced_refs: produced, included_refs: included, referenced_refs: consumed, omitted })}
      </li>`;
    }).filter(Boolean).join("");
    return rows
      ? `<p><strong>证据流转</strong></p><ul class="wpem-audit-list">${rows}</ul>`
      : `<p class="muted">Decision Trace 未返回这些引用的层级流转；仅按本点 refs 展示输入。</p>`;
  }

  function renderTracePoint(point, question, index, explanationEvidence, trace, receiptKey) {
    const tracePoint = asArray(asObject(trace).points).map(asObject).find((item) => {
      const q = String(firstPresent(item.question, item.q, "")).toLowerCase();
      return q === question && safeNumber(item.index) === index + 1;
    }) || {};
    const text = firstPresent(point.text_cn, tracePoint.text_cn, point.summary_cn, tracePoint.summary_cn, "");
    const refs = traceRefs(point.evidence_refs);
    const pointIndex = traceEvidenceIndex(point.evidence);
    const inputIndex = traceEvidenceIndex(asObject(trace).input_evidence);
    const explanationIndex = traceEvidenceIndex(explanationEvidence);
    const evidenceRows = refs.map((ref) => {
      const row = pointIndex.get(ref) || inputIndex.get(ref) || explanationIndex.get(ref) || { ref };
      return renderTraceCleanItem(row, ref, `${receiptKey}::${question}::point${index}::${ref}`);
    }).join("");
    const tagRows = refs.slice(0, 4).map((ref) => {
      const row = asObject(pointIndex.get(ref) || inputIndex.get(ref) || explanationIndex.get(ref));
      const provided = asObject(row.provided);
      return displayToken(firstPresent(provided.label_cn, row.label_cn, provided.source_asset, provided.group, ref), "证据");
    }).filter(Boolean);
    return `<article class="trace-point" data-point-question="${escapeHtml(question)}" data-point-array-index="${index}">
      <p class="review-summary"><strong>${escapeHtml(`模型点 ${index + 1}`)}</strong><br>${escapeHtml(displayText(text) || "本点未返回正文。")}</p>
      ${tagRows.length ? `<p class="trace-evidence-tags"><strong>依据标签</strong> ${tagRows.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</p>` : ""}
      <details data-disclosure-key="${escapeHtml(`${receiptKey}::${question}::point${index}`)}"><summary>本点依据</summary>
        <p class="muted">以下为本点引用的冻结事实；展开“查看原始信息”可核对完整原件。</p>
        ${evidenceRows ? `<dl>${evidenceRows}</dl>` : `<p class="muted">本点没有可公开展示的输入证据。</p>`}
        <details data-disclosure-key="${escapeHtml(`${receiptKey}::${question}::point${index}::flow`)}"><summary>细审计：证据如何进入本点</summary>${renderTraceLayerFlow(trace, refs)}</details>
        ${rawFold(`${receiptKey}::${question}::point${index}::raw`, { point, trace_point: tracePoint, evidence_refs: refs, array_index_0based: index, trace_index_1based: index + 1 })}
      </details>
    </article>`;
  }

  function renderDecisionTraceOverview(review, trace, receiptKey, options = {}) {
    const traceObject = asObject(trace);
    const hasTrace = Object.keys(traceObject).length > 0;
    const guardrailSelection = review.selection_origin === "program_guardrails";
    if (!hasTrace && !guardrailSelection) return "";
    const summaryText = firstPresent(options.summary_cn, options.summary, "审计摘要：输入、引用与显示关系");
    const binding = asObject(traceObject.binding);
    const inputRefs = traceEvidenceIndex(traceObject.input_evidence);
    const layerRows = asArray(traceObject.layers).map((rawLayer) => {
      const layer = asObject(rawLayer);
      const title = firstPresent(layer.title_cn, layer.layer, "层级");
      const omitted = asArray(layer.omitted).map(asObject).map((item) => `${item.ref || "ref"}（${firstPresent(item.reason_cn, item.reason, "未说明")}）`);
      const included = [...new Set([...asArray(layer.produced_refs), ...asArray(layer.consumed_refs)])].filter((ref) => inputRefs.has(ref));
      return `<li><strong>${escapeHtml(displayToken(title, "证据层"))}</strong>
        <small>系统已生成 ${escapeHtml(fmtNumber(asArray(layer.produced_refs).length, 0))} 条；已进入本次分析 ${escapeHtml(fmtNumber(included.length, 0))} 条；被本次解释引用 ${escapeHtml(fmtNumber(asArray(layer.consumed_refs).length, 0))} 条。</small>
        <small>未被引用：${escapeHtml(omitted.length ? groupedCountSummary(omitted, (item) => evidenceGroupLabel(item)) : "无")}</small>
        ${rawFold("", layer)}
      </li>`;
    }).join("");
    const pointRefs = new Set(asArray(traceObject.points).flatMap((point) => traceRefs(asObject(point).evidence_refs)));
    const inputRows = asArray(traceObject.input_evidence).map((row, index) => {
      const ref = traceEvidenceRef(row, `input_${index + 1}`);
      const stateText = pointRefs.has(ref) ? "已进入本次分析，并被本次解释引用。" : "已进入本次分析，本次解释没有直接引用。";
      return `<div class="trace-input-consumption"><p class="muted">${escapeHtml(stateText)}</p>${renderTraceCleanItem(row, ref, `${receiptKey}::trace::input${index}`)}</div>`;
    }).join("");
    const auditOnlyRows = asArray(traceObject.audit_only).map(asObject).map((item) =>
      `<li><strong>${escapeHtml(firstPresent(item.field, "审计字段"))}</strong><span>${escapeHtml(firstPresent(item.reason_cn, item.reason, "仅审计，不进入模型综合。"))}</span></li>`
    ).join("");
    const producedCount = asArray(traceObject.layers).reduce((total, layer) => total + asArray(asObject(layer).produced_refs).length, 0);
    const inputCount = asArray(traceObject.input_evidence).length;
    const referencedCount = pointRefs.size;
    const unreferenced = asArray(traceObject.input_evidence).filter((row, index) => !pointRefs.has(traceEvidenceRef(row, `input_${index + 1}`)));
    return `<details class="decision-trace-overview ia-audit" data-disclosure-key="${escapeHtml(`${receiptKey}::decision_trace`)}"><summary>${escapeHtml(summaryText)}</summary>
      <p class="ia-layer-title"><strong>这版解释的证据消费</strong></p>
      <p>系统已生成 ${escapeHtml(fmtNumber(producedCount, 0))} 条证据项；${escapeHtml(fmtNumber(inputCount, 0))} 条进入本次分析；${escapeHtml(fmtNumber(referencedCount, 0))} 条被 Q1/Q2 直接引用。折叠可达不等于用户已阅读。</p>
      ${unreferenced.length ? `<p class="muted">另有 ${escapeHtml(fmtNumber(unreferenced.length, 0))} 条进入输入但未被本次解释直接引用：${escapeHtml(groupedCountSummary(unreferenced, (row) => evidenceGroupLabel(traceEvidenceRef(row))))}。</p>` : ""}
      ${traceObject.scope_cn ? `<p class="muted">${escapeHtml(traceObject.scope_cn)}</p>` : ""}
      ${guardrailSelection ? `<p class="muted">目录选择含程序安全要求；selected catalog IDs 是安全与边界约束，不代表模型主动选边，也不授予交易许可。</p>` : ""}
      ${Object.keys(binding).length ? rawFold(`${receiptKey}::decision_trace::binding`, binding, "查看回执绑定原始信息") : ""}
      ${layerRows ? `<p><strong>证据流转总览</strong></p><ul class="wpem-audit-list">${layerRows}</ul>` : ""}
      ${auditOnlyRows ? `<p><strong>只保留审计的字段</strong></p><ul>${auditOnlyRows}</ul>` : ""}
      ${inputRows ? `<details data-disclosure-key="${escapeHtml(`${receiptKey}::decision_trace::inputs`)}"><summary>实际模型输入证据清单</summary><dl>${inputRows}</dl></details>` : ""}
    </details>`;
  }

  function renderTwoQuestions(review, options = {}) {
    const q1 = asObject(review.q1);
    const q2 = asObject(review.q2);
    const q1Title = options.historical ? "历史 Q1 · 原冻结时点相对适配在哪里，主要瑕疵是什么？" : Q1_DECISION_TITLE;
    const q2Title = options.historical ? "历史 Q2 · 原冻结时点的补偿是否值得交换这些风险？" : Q2_DECISION_TITLE;
    if (!Object.keys(q1).length || !Object.keys(q2).length) {
      if (options.auditOnly) return "";
      return `<p class="muted">本回执未包含现行两问，不由页面补写结论。</p>`;
    }
    const rootExplanations = asObject(review.llm_explanation);
    const q1Explanation = asObject(q1.llm_explanation);
    const q2Explanation = asObject(q2.llm_explanation);
    const explanation1 = Object.keys(q1Explanation).length ? q1Explanation : asObject(rootExplanations.q1);
    const explanation2 = Object.keys(q2Explanation).length ? q2Explanation : asObject(rootExplanations.q2);
    const hasExplanation1 = explanation1.text_cn || asArray(explanation1.points).length;
    const hasExplanation2 = explanation2.text_cn || asArray(explanation2.points).length;
    if (hasExplanation1 && hasExplanation2) {
      const receiptKey = asObject(review.display_binding).receipt_id || `review::${review.reviewed_snapshot_id || ""}::${review.projection_hash || ""}`;
      const directTrace = asObject(review.decision_trace);
      const trace = Object.keys(directTrace).length ? directTrace : asObject(asObject(review.normalized).decision_trace);
      const explanationHtml = (explanation, question) => {
        const evidence = asArray(explanation.evidence);
        const points = asArray(explanation.points).map((point, index) => ({point: asObject(point), index})).filter(({point}) => (
          firstPresent(point.text_cn, point.summary_cn) || asArray(point.evidence_refs).length
        ));
        if (points.length) {
          return `<div class="trace-point-list">${points.map(({point, index}) =>
            renderTracePoint(point, question, index, evidence, trace, receiptKey)
          ).join("")}</div>`;
        }
        const rows = asArray(explanation.evidence_refs).map(ref => {
          const row = asObject(evidence.find(item => item.ref === ref));
          const provided = asObject(row.provided);
          const points = asArray(provided.observed_points);
          const mapRecord = asObject(provided.record);
          const mapTitle = mapRecord.name ? mapLabelForKey(mapRecord.name) : (mapRecord.reference_id ? referenceDisplayTitle(mapRecord) : "");
          const title = provided.label_cn || provided.source_asset || mapTitle || (points.length ? "同保单已观察片段" : ({"wpem.window": "同保单窗口", "wpem.compensation": "补偿变化", "quote": "冻结报价", "fee": "冻结费用", "contract": "保单责任", "risk": "风险参考"})[ref]) || ref;
          let text = provided.source_asset ? consumerReadingText(provided) : [provided.scope_cn, provided.path_cn, provided.compensation_cn, mapRecord.summary_cn, ...asArray(provided.breaks_cn), ...asArray(mapRecord.limitations), ...asArray(mapRecord.missing), ...asArray(provided.catalog_items).map(item => firstPresent(item.text_cn, item.q1_reason_cn, item.q2_reason_cn))].filter(Boolean).join("\n");
          const account = asObject(asObject(review.deterministic_financials).account_basis);
          const fee = asObject(asObject(provided.source_fee).fee);
          if (ref === "quote" && Object.keys(account).length) {
            text = `冻结权利金差 ${fmtBtc(account.visible_credit_btc)}；扣冻结费用后 ${fmtBtc(account.net_credit_after_fee_btc)}。${text}`;
          }
          if (ref === "fee" && Object.keys(fee).length) {
            text = `冻结费用 ${fmtBtc(fee.amount_btc)}；建仓费用 ${fmtBtc(fee.entry_fee_btc)}；交割费用 ${fmtBtc(fee.delivery_fee_btc)}。沿用本次输入口径。`;
          }
          const pointRows = points.map(point => `<tr><td>${escapeHtml(traceClockText(point.decision_asof_ms))}</td><td>${escapeHtml(fmtUsd(asObject(point.market).reference_price_usd))}</td><td>${escapeHtml(fmtBtc(asObject(point.economics).visible_credit_btc))}</td><td>${escapeHtml(fmtBtc(asObject(point.economics).fee_btc))}</td></tr>`).join("");
          const sourceClocks = asArray(provided.source_records).map(source => {
            const observed = asObject(source.time).observation_end_ms;
            const observedNumber = safeNumber(observed);
            const observedText = observedNumber !== null && observedNumber > 0 ? `原观察 ${fmtTime(observed)}` : "原观察未留存";
            return `<small>${escapeHtml(sourceClockProductLabel(source.product_id))} · ${escapeHtml(observedText)} · 冻结用途 ${escapeHtml(sourceClockStatusText(asObject(source.usage_qualification).status))}</small>`;
          }).join("");
          return `<div class="ia-evidence-summary"><dt>${escapeHtml(displayToken(title, "证据"))}</dt><dd>${text ? escapeHtml(displayText(text)) : "保留本次输入的引用供核对。"}
            ${mapRecord.metrics ? renderMapMetrics(mapRecord.metrics) : ""}
            ${mapRecord.reference_id ? `<p>${escapeHtml(referenceSourceNote(mapRecord))}；原价 ${escapeHtml(fmtUsd(mapRecord.raw_price))}</p>` : ""}
            ${sourceClocks}
            ${pointRows ? `<div class="map-table-wrap" role="region" aria-label="模型所见同保单逐点读数" tabindex="0"><table class="map-table"><thead><tr><th>原冻结时点</th><th>参考价</th><th>原信用</th><th>原费用</th></tr></thead><tbody>${pointRows}</tbody></table></div>` : ""}
            ${provided.source_observed_ms || provided.asof_ms ? `<small>来源时点 ${escapeHtml(fmtTime(provided.source_observed_ms))} · 资料时点 ${escapeHtml(fmtTime(provided.asof_ms))}</small>` : ""}
            ${rawFold(`${receiptKey}::${question}::${ref}`, Object.keys(row).length ? row : {ref})}</dd></div>`;
        }).join("");
        return `<p class="review-summary">${escapeHtml(displayText(explanation.text_cn))}</p><details data-disclosure-key="${escapeHtml(`${receiptKey}::${question}`)}"><summary>本点依据</summary><dl>${rows}</dl></details>`;
      };
      const programReview = {...review, llm_explanation: null, normalized: {...asObject(review.normalized), llm_explanation: null}, q1: {...q1, llm_explanation: null}, q2: {...q2, llm_explanation: null}};
      const auditHtml = `${renderDecisionTraceOverview(review, trace, receiptKey)}
        <details class="grounded-program-basis" data-disclosure-key="${escapeHtml(`${receiptKey}::program`)}"><summary>程序事实底座</summary>${renderTwoQuestions(programReview, { historical: options.historical })}</details>`;
      if (options.auditOnly) return auditHtml;
      return `<div class="two-question-grid llm-grounded-questions" aria-label="模型依据解释">
        <section><h3>${escapeHtml(q1Title)}</h3>${explanationHtml(explanation1, "q1")}</section>
        <section><h3>${escapeHtml(q2Title)}</h3>${explanationHtml(explanation2, "q2")}
          ${q2.coverage_cn ? `<p class="muted"><strong>风险参考边界</strong><br>${escapeHtml(displayText(q2.coverage_cn))}</p>` : ""}</section>
        </div><p class="muted">以上为模型基于本次冻结资料的解释；引用可追溯不等于解释已被证明。合同数值与权限仍以程序核算为准。</p>
        ${options.primaryOnly ? "" : auditHtml}`;
    }
    if (options.auditOnly) return "";
    const strongest = asObject(q2.strongest_counter);
    const compensationCounter = strongest.id === "counter.compensation.nonpositive_margin";
    const strongestText = firstPresent(
      strongest.claim_cn,
      strongest.text_cn,
      typeof q2.strongest_counter === "string" ? q2.strongest_counter : undefined,
      "未提供",
    );
    const exchangeText = firstPresent(
      q2.exchange_reason_cn,
      strongestText === "未提供" ? "" : `反证：${strongestText}`,
      "未提供",
    );
    const reassessText = compactTextItems(q2.reassess_if_cn)[0];
    const observableChanges = asArray(q2.observable_changes_cn).filter(Boolean).slice(0, 2);
    const catalogueIds = [
      ...asArray(review.selected_claim_ids),
      ...asArray(review.selected_counter_ids),
      ...asArray(review.selected_gap_ids),
      ...asArray(review.selected_reassess_condition_ids),
    ].filter(Boolean);
    return `<div class="two-question-grid" aria-label="同版保单两问">
      <section><h3>${escapeHtml(q1Title)}</h3>
        ${renderQuestionSourceTitle(q1.title_cn, Q1_DECISION_TITLE)}
        ${q1.summary_cn ? `<p>${escapeHtml(displayText(q1.summary_cn))}</p>` : ""}${renderRoutingObservations(q1)}
        ${q1.window_attribution_cn ? `<p><strong>严格窗口证据</strong><br><span class="muted">用于判断严格 Delta / Path 是否成立。</span><br>${escapeHtml(q1.window_attribution_cn)}</p>` : ""}
        ${compensationCounter ? `<p class="muted">补偿侧反证归入 Q2；不代表上述环境与位置无法解释。</p>` : (q1.competing_explanation_cn ? `<p><strong>主要竞争解释</strong><br>${escapeHtml(q1.competing_explanation_cn)}</p>` : "")}
      </section>
      <section><h3>${escapeHtml(q2Title)}</h3>
        ${renderQuestionSourceTitle(q2.title_cn, Q2_DECISION_TITLE)}
        ${q2.summary_cn ? `<p class="review-summary">${escapeHtml(displayText(q2.summary_cn))}</p>` : ""}${renderRoutingObservations(q2)}
        ${q2.window_tradeoff_cn ? `<p><strong>报价补偿取舍</strong><br>${escapeHtml(q2.window_tradeoff_cn)}</p>` : ""}
        ${exchangeText !== "未提供" ? `<p><strong>当前交换与最强反证</strong><br>${escapeHtml(exchangeText)}</p>` : ""}
        ${q2.coverage_cn ? `<p><strong>风险覆盖边界</strong><br>${escapeHtml(displayText(q2.coverage_cn))}</p>` : ""}
        ${observableChanges.length ? `<p><strong>可观察变化</strong></p><ul>${observableChanges.map((item) => `<li>${escapeHtml(label(item))}</li>`).join("")}</ul>` : ""}
        ${q2.waiting_cost_cn ? `<p><strong>等待代价</strong><br>${escapeHtml(q2.waiting_cost_cn)}</p>` : ""}
        ${reassessText ? `<p class="decision-reassess"><strong>什么改变才值得再看</strong><br>${escapeHtml(reassessText)}</p>` : ""}
        <details><summary>展开责任与重审条件</summary>
          ${q2.liability_cn ? `<p><strong>合同责任边界</strong><br>${escapeHtml(q2.liability_cn)}</p>` : ""}
          ${catalogueIds.length ? `<p><strong>有限目录来源</strong><br>${escapeHtml(catalogueIds.map((item) => label(item)).join("；"))}</p>` : ""}
          <strong>什么改变才值得再看</strong><ul>${reviewList(q2.reassess_if_cn, "未提供改变条件。")}</ul>
        </details>
      </section>
    </div>`;
  }

  function renderReviewResultDetail(result, options = {}) {
    if (!Object.keys(result).length) return "";
    const settings = asObject(options);
    const review = reviewPayload(result);
    const statusInfo = reviewStatusInfo(result, review);
    const resultStatus = statusInfo.status;
    const researchOk = statusInfo.researchOk;
    if (!statusInfo.accepted) {
      const directTrace = asObject(review.decision_trace);
      const trace = Object.keys(directTrace).length ? directTrace : asObject(firstPresent(asObject(review.normalized).decision_trace, result.decision_trace, {}));
      const outerReason = firstPresent(result.reason_cn, result.reason, "");
      const innerReason = firstPresent(review.reason_cn, review.reason, "");
      const reason = [outerReason, innerReason && innerReason !== outerReason ? innerReason : ""].filter(Boolean).join("；");
      const receiptKey = `review-failed::${firstPresent(result.candidate_id, review.candidate_id, "")}::${firstPresent(result.snapshot_id, review.reviewed_snapshot_id, result.reviewed_snapshot_id, "")}`;
      const frozenContract = asObject(asObject(asArray(trace.input_evidence).find(item => item.ref === "contract")).facts);
      const frozenIdentity = [review.decision_asof_ms ? `输入冻结于 ${fmtTime(review.decision_asof_ms)}` : "", frozenContract.short_instrument, frozenContract.long_instrument].filter(Boolean).join(" · ");
      return `<div class="callout warn" style="margin-top: 14px;"><strong>本次无合格评审正文：${escapeHtml(label(resultStatus))}</strong>${frozenIdentity ? `<p class="muted">${escapeHtml(frozenIdentity)}</p>` : ""}<p>${escapeHtml(reason ? `失败原因：${reason}` : "保留失败或中断回执；页面不会自动补发 LLM，也不会把旧结论附着到当前保单。")}</p>${renderDecisionTraceOverview(review, trace, receiptKey, { summary_cn: "本次冻结输入审计（解释未采纳）" })}</div>`;
    }
    const snapshotId = firstPresent(result.snapshot_id, review.reviewed_snapshot_id, result.reviewed_snapshot_id);
    const sameCandidate = sortedCandidates().find((candidate) => candidateId(candidate) === firstPresent(result.candidate_id, review.candidate_id));
    const attachedReview = asObject(sameCandidate && sameCandidate.review);
    const forcedHistorical = result.historical_only === true || review.historical_only === true;
    const sameVersion = !forcedHistorical && sameCandidate && snapshotId === asObject(sameCandidate.snapshot).snapshot_id
      && (!result.map_id || result.map_id === candidateBtcMap(sameCandidate).map_id)
      && ["OK", "attached", "SIMULATED"].includes(attachedReview.status)
      && attachedReview.reviewed_snapshot_id === snapshotId
      && attachedReview.response_protocol === review.response_protocol;
    const simulated = isSyntheticPreview() || firstPresent(result.status, review.status) === "SIMULATED";
    const currentCandidate = sortedCandidates().find((candidate) => candidateId(candidate) === result.candidate_id);
    const side = firstPresent(result.side, currentCandidate && currentCandidate.side);
    const receiptAt = firstPresent(result.reviewed_at_ms, review.reviewed_at_ms, result.recorded_ts_ms, review.recorded_ts_ms);
    const binding = reviewDisplayBinding(result, review);
    const displayRelation = reviewDisplayRelation(result, review);
    const historicalReceipt = settings.forceHistorical === true || forcedHistorical || displayRelation.relation === "historical" || (!researchOk && !sameVersion && displayRelation.relation !== "current");
    const currentReceipt = !historicalReceipt && (displayRelation.relation === "current" || sameVersion);
    if (currentReceipt && displayRelation.relation === "current" && displayRelation.candidate) return renderCurrentReceiptLocation(displayRelation.candidate);
    const receiptTitle = historicalReceipt ? "此前有效评审 · 历史参考" : (currentReceipt ? "当前同版回执 · 审计入口" : (researchOk ? "研究复核完成 · 未生产附着" : "评审回执 · 审计入口"));
    const receiptReason = firstPresent(
      settings.reason_cn,
      displayRelation.reason_cn,
      review.display_reason_cn,
      result.reason_cn,
      review.reason_cn,
      historicalReceipt ? "当前 snapshot 已更新；旧回执只作历史参考。" : "主卡是唯一当前解释；此处只作回执审计。",
    );
    const frozenEvidence = asObject(review.wpem_evidence);
    const frozenRow = asObject(asArray(asObject(frozenEvidence.current_cross_section).cross_section_rows).find(row => row.snapshot_id === snapshotId));
    const frozenContract = asObject(frozenRow.contract);
    const frozenContractText = firstPresent(
      reviewFrozenContractSummary(result, review),
      Object.keys(frozenContract).length ? `${label(frozenContract.side)} · 卖出腿 ${fmtUsd(frozenContract.short_strike_usd)} / 保护腿 ${fmtUsd(frozenContract.long_strike_usd)} · 到期 ${fmtTime(frozenContract.expiry_ms)}` : "",
    );
    const identity = reviewReceiptIdentity(result, review);
    const identityText = [
      identity.candidateId ? `候选 ${shortId(identity.candidateId)}` : "",
      identity.snapshotId ? `快照 ${shortId(identity.snapshotId)}` : "",
      identity.receiptId ? `回执 ${shortId(identity.receiptId)}` : "",
      binding.same_input === true ? "同版输入" : (binding.same_input === false ? "非同版输入" : ""),
      binding.frozen_at_ms ? `frozen ${fmtTime(binding.frozen_at_ms)}` : "",
      binding.reviewed_at_ms || receiptAt ? `reviewed ${fmtTime(firstPresent(binding.reviewed_at_ms, receiptAt))}` : "",
    ].filter(Boolean).join(" · ");
    const receiptDisclosureKey = ["review-detail", identity.candidateId, identity.snapshotId, identity.receiptId || receiptAt || resultStatus].filter(Boolean).join("::");
    const detail = `<div class="review-detail ${simulated ? "is-simulated" : ""}">
      ${historicalReceipt ? `<p class="historical-reading-context">历史参考 · ${escapeHtml(fmtTime(firstPresent(binding.frozen_at_ms, frozenEvidence.decision_asof_ms)))} 冻结资料 · 非当前判断</p>` : ""}
      <div class="badge-row">
        ${badge(researchOk ? "研究回执：禁止生产附着" : (simulated ? "模拟回执：非真实 LLM" : "服务端回执"), (simulated || researchOk) ? "warn" : "blue")}
        ${side ? badge(`${label(side)} 保单`, "blue") : ""}
        ${badge(historicalReceipt ? "历史冻结保单" : (researchOk ? "冻结研究保留" : (simulated ? "模拟同版保单" : "同版当前保单")), historicalReceipt || researchOk ? "warn" : "good")}
        ${badge(`综合复核状态：${({ EXPLAINED: "依据可解释", CONTESTED: "存在反证", UNABLE_TO_JUDGE: "暂不能判定" })[review.applicability] || label(firstPresent(review.applicability, result.status, review.status, "未提供"))}`, "blue")}
      </div>
      ${researchOk ? `<p class="muted">这份解释对应冻结资料，仅供观测研究，不授予交易或生产附着许可。</p>` : ""}
      ${frozenContractText ? `<p><strong>解释对应保单</strong><br>${escapeHtml(frozenContractText)}<br>资料时点 ${escapeHtml(fmtTime(frozenEvidence.decision_asof_ms))}</p>` : ""}
      ${Object.keys(binding).length ? `<p class="muted">绑定：${escapeHtml(identityText)}</p>` : ""}
      ${receiptAt ? `<p class="muted">本回执时间 ${escapeHtml(fmtTime(receiptAt))}</p>` : ""}
      ${!asObject(asObject(review.q1).llm_explanation).text_cn && firstPresent(review.summary_cn, result.summary_cn) ? `<p class="review-summary">${escapeHtml(firstPresent(review.summary_cn, result.summary_cn))}</p>` : ""}
      ${renderTwoQuestions(review, { historical: historicalReceipt })}
      <details data-disclosure-key="${escapeHtml(`${receiptDisclosureKey}::selection`)}"><summary>展开本次选择的支持、反证与来源</summary>
      <div class="review-columns">
        <section>
          <h3>支持</h3>
          <ul>${reviewList(review.supports, "未返回支持项。")}</ul>
        </section>
        <section>
          <h3>反证</h3>
          <ul>${reviewList(review.counters, "未返回反证项。")}</ul>
        </section>
        <section>
          <h3>缺口</h3>
          <ul>${reviewList(review.gaps_cn, "未返回新增缺口。")}</ul>
        </section>
        <section>
          <h3>改变判断的事实</h3>
          <ul>${reviewList(review.reassess_if_cn, "未返回重新审查条件。")}</ul>
        </section>
      </div></details>
    </div>`;
    if (settings.inlineBody) return `<p class="muted">这份回执只解释此前冻结资料。${escapeHtml(displayText(receiptReason))}</p>${detail}`;
    return `<div class="callout ${historicalReceipt || researchOk ? "warn" : "good"} review-receipt-summary">
      <strong>${escapeHtml(receiptTitle)}</strong>
      <p class="muted">${identityText ? `${escapeHtml(identityText)}。` : ""}${historicalReceipt && !researchOk ? `${escapeHtml("历史回执 · 当前候选尚无同版结论。")}` : ""}${escapeHtml(`原因：${receiptReason}`)}</p>
      <details data-disclosure-key="${escapeHtml(receiptDisclosureKey)}"><summary>${escapeHtml(historicalReceipt ? "此前有效评审 · 历史参考" : "展开 accepted 正文与来源")}</summary>${detail}</details>
    </div>`;
  }

  function renderManualReviewState() {
    const target = $("#manualReviewState");
    if (!target) return;
    const expanded = openDisclosureKeys(target);
    const review = manualReviewState();
    const inflight = asObject(review.inflight);
    const busy = manualReviewBusy() ? badge("正在推理", "warn") : "";
    const previewToken = previewReviewAllowed() ? badge("本地模拟无需访问会话", "warn") : "";
    const token = previewReviewAllowed() ? "" : (sessionAuthenticated() ? badge("访问会话有效", "blue") : badge("未验证访问会话", "warn"));
    const budget = asObject(review.daily_http_budget);
    const lastResult = asObject(review.last_result);
    const lastPayload = reviewPayload(lastResult);
    const lastStatusInfo = reviewStatusInfo(lastResult, lastPayload);
    const lastStatus = lastStatusInfo.status;
    const lastSucceeded = lastStatusInfo.accepted;
    const previousValidResult = asObject(review.previous_valid_result);
    const previousValidDetail = !lastSucceeded && Object.keys(previousValidResult).length
      ? `<details data-disclosure-key="${escapeHtml(`previous-valid-review::${previousValidResult.snapshot_id || reviewPayload(previousValidResult).reviewed_snapshot_id || ""}`)}"><summary>此前有效评审 · 历史参考</summary><p class="muted">这份回执绑定此前冻结输入，只能作为历史参考；页面不会把它回填为当前结果，也不会自动发起新请求。</p>${renderReviewResultDetail(previousValidResult, { forceHistorical: true, reason_cn: "当前 snapshot 已更新；旧回执只作历史参考。" })}</details>`
      : "";
    const selected = selectedMapContext().candidate;
    const attached = asObject(selected && selected.review);
    const selectedDisplayCurrent = asObject(selected && candidateReviewDisplay(selected).current);
    const selectedDisplayPayload = reviewResultPayload(selectedDisplayCurrent);
    const selectedDisplayStatus = firstPresent(selectedDisplayCurrent.status, selectedDisplayPayload.status, selectedDisplayPayload.state, "");
    const selectedDisplayResult = selected && Object.keys(selectedDisplayCurrent).length
      && ["OK", "attached", "SIMULATED", "RESEARCH_OK"].includes(selectedDisplayStatus)
      && reviewMatchesCurrentCandidate(selected, selectedDisplayCurrent, selectedDisplayPayload, { strictDisplay: true })
      ? selectedDisplayCurrent
      : null;
    const selectedAttachedResult = selected && ["OK", "attached", "SIMULATED"].includes(attached.status)
      ? { ...attached, candidate_id: candidateId(selected), side: selected.side, review: attached }
      : null;
    const selectedResult = selectedDisplayResult || selectedAttachedResult;
    const contract = asObject(selected && firstPresent(asObject(selected.snapshot).contract, selected.contract, {}));
    const selectedDetail = selected
      ? `<h3>当前查看 · ${escapeHtml(label(selected.side))} 保单</h3><p class="muted">${escapeHtml(firstPresent(contract.short_instrument, "卖出腿未提供"))} / ${escapeHtml(firstPresent(contract.long_instrument, "保护腿未提供"))}</p>${selectedResult ? renderSelectedReviewReceiptSummary(selectedResult) : empty("该候选尚无同版回执；查看已有结果不会发起评审。")}`
      : renderReviewResultDetail(lastResult);
    const isSelectedLast = selectedResult && sameReviewReceipt(selectedResult, reviewResultPayload(selectedResult), lastResult, lastPayload);
    const rows = [
      metric("全局冷却", review.cooldown_remaining_ms > 0 ? "冷却中" : "当前无冷却", `剩余 ${fmtMsAge(review.cooldown_remaining_ms)}`),
      metric("共享 HTTP 预算", Object.keys(budget).length ? `${fmtNumber(firstPresent(budget.http_calls_used, budget.used), 0)} / ${fmtNumber(firstPresent(budget.http_calls_limit, budget.cap), 0)}` : "未配置", `北京日 ${firstPresent(budget.date_bjt, budget.bjt_date, budget.date, "未提供")}`),
      metric("运行中任务", Object.keys(inflight).length ? "正在推理" : "无", "任务身份由服务端持久化，前端不展示原始标识"),
      metric("全局最近评审", label(firstPresent(lastResult.status, lastResult.state, typeof review.last_result === "string" ? review.last_result : undefined, "未发生")), fmtTime(firstPresent(review.last_completed_ts_ms, lastResult.reviewed_at_ms))),
    ].join("");
    const operationOpen = manualReviewBusy() || safeNumber(review.cooldown_remaining_ms) > 0;
    const operationDetails = `<details class="review-operation-details"${operationOpen ? " open" : ""}>
      <summary>评审运行状态与调用限制</summary>
      <div class="badge-row" style="margin-bottom: 12px;">${token}${previewToken}${busy}</div>
      <div class="metric-grid">${rows}</div>
    </details>`;
    target.innerHTML = `
      ${selectedDetail}
      ${previousValidDetail}
      <div class="callout" style="margin-top: 12px;">${escapeHtml(state.reviewMessage || state.sessionMessage || (previewReviewAllowed() ? "当前为本地模拟服务：按钮只演示回执流程，不调用真实 LLM，也不传访问凭据。" : "模型调用需手动发起；观测解释保留局部资料限制，主评审沿用原条件。两者共用访问会话、冷却和调用预算。"))}</div>
      ${selected && Object.keys(lastResult).length && !isSelectedLast ? `<details data-disclosure-key="${escapeHtml(`global-review::${lastResult.snapshot_id || reviewPayload(lastResult).reviewed_snapshot_id || ""}`)}"><summary>查看全局最近一次回执（独立于当前选侧）</summary>${renderReviewResultDetail(lastResult)}</details>` : ""}
      ${operationDetails}`;
    restoreDisclosureState(target, expanded);
    bindDisclosurePersistence(target);
  }

  function renderDailySnapshots() {
    const target = $("#dailySnapshots");
    if (!target) return;
    const snapshots = asArray(state.workbench && state.workbench.daily_snapshots).slice(0, MAX_DAILY_SNAPSHOTS);
    if (!snapshots.length) {
      target.innerHTML = empty("暂无 23:00 快照。");
      return;
    }
    target.innerHTML = `<ul class="list">${snapshots.map((item) => {
      const snapshot = asObject(item);
      const gate = asObject(snapshot.space_gate);
      const candidates = asArray(snapshot.candidates).map((candidate) => label(candidate.side)).join(" / ");
      const reviews = asArray(snapshot.candidates).map((candidate) => `${label(candidate.side)} ${label(asObject(candidate.review).status || "未发生")}`).join(" / ");
      return `<li>
        <strong>${escapeHtml(fmtTime(firstPresent(snapshot.recorded_ts_ms, snapshot.snapshot_at_ms, snapshot.bjt_day)))}</strong>
        <span class="muted">${escapeHtml(label(firstPresent(gate.state, snapshot.space_gate_state, "STRUCTURE_BLOCKED")))}；候选 ${escapeHtml(candidates || "未记录")}；同版评审 ${escapeHtml(reviews || "未发生")}</span>
      </li>`;
    }).join("")}</ul>`;
  }

  function renderTransitions() {
    const target = $("#transitions");
    if (!target) return;
    const transitions = asArray(state.workbench && state.workbench.transitions);
    if (!transitions.length) {
      target.innerHTML = empty("暂无空间门状态迁移。");
      return;
    }
    target.innerHTML = `<ul class="list">${transitions.map((item) => {
      const transition = asObject(item);
      const transitionGate = asObject(transition.space_gate);
      const fromState = label(firstPresent(transition.from_state, transition.previous_state, "未知"));
      const toState = label(firstPresent(transition.current_state, transition.to_state, transition.state, "未知"));
      const reasons = [
        ...asArray(transition.reason_codes),
        ...asArray(transition.reasons),
        ...asArray(transitionGate.reason_codes),
        ...asArray(transitionGate.gap_reasons),
      ].map((reason) => label(reason)).join("；");
      const notification = asObject(state.workbench && state.workbench.notification_state);
      const attempt = asObject(notification.last_attempt);
      const mail = transition.transition_id === attempt.transition_id
        ? firstPresent(attempt.status, notification.last_status, "未记录")
        : transition.transition_id === notification.pending_transition_id ? "待发送或重试" : "历史发送状态未单独留存";
      return `<li>
        <strong>${escapeHtml(fromState)} → ${escapeHtml(toState)}</strong>
        <span class="muted">${escapeHtml(fmtTime(firstPresent(transition.transition_ts_ms, transition.transition_at_ms, transition.at_ms)))}；邮件 ${escapeHtml(mail === "failed" ? "发送失败" : label(mail))}</span>
        ${reasons ? `<p class="muted">${escapeHtml(reasons)}</p>` : ""}
      </li>`;
    }).join("")}</ul>`;
  }

  function renderGaps() {
    const target = $("#gapArea");
    if (!target) return;
    const gaps = [...new Set([
      ...asArray(state.workbench && state.workbench.gaps),
      ...gateReasons(gateObject()),
    ].filter(Boolean).map((item) => typeof item === "string" ? item : JSON.stringify(item)))];
    if (!gaps.length) {
      target.innerHTML = empty("服务端未返回全局缺口；仍需查看每张候选的具体阻断理由。");
      return;
    }
    target.innerHTML = `<ul class="list">${gaps.map((gap) => {
      let object = {};
      if (gap.startsWith("{")) {
        try {
          object = asObject(JSON.parse(gap));
        } catch (_error) {
          object = {};
        }
      }
      const code = firstPresent(object.code, object.reason_code, object.reason, gap);
      const text = firstPresent(object.message_cn, object.summary_cn, object.detail_cn, object.basis_cn);
      return `<li><strong>${escapeHtml(label(code))}</strong><span class="muted">${escapeHtml(text)}</span></li>`;
    }).join("")}</ul>`;
  }

  function historyPath(item) {
    const path = String(item.path || "").trim();
    if (!path) return "";
    if (/^(signal_cards|joint-shadow)\//.test(path) && /\.json(?:$|\?)/.test(path)) return path;
    return "";
  }

  function filteredHistoryItems() {
    const query = state.historyFilter.trim().toLowerCase();
    if (!query) return state.historyItems;
    return state.historyItems.filter((item) => [item.id, item.path, item.time, item.symbol, item.quality]
      .join(" ")
      .toLowerCase()
      .includes(query));
  }

  async function openHistoryRaw(item) {
    const path = historyPath(item);
    if (!path) {
      state.rawCard = { title: "历史原始 JSON", text: "历史索引路径不可安全读取。" };
      renderHistory();
      return;
    }
    try {
      const raw = await fetchJson(path);
      const reviewStatus = firstPresent(pathGet(raw, "llm_review.status"), pathGet(raw, "decision_matrix.audit_dissent"), "未提供");
      state.rawCard = {
        title: `历史原始 JSON · LLM ${label(reviewStatus)}`,
        text: JSON.stringify(raw, null, 2),
      };
    } catch (error) {
      state.rawCard = {
        title: "历史原始 JSON",
        text: `读取失败：${error && error.message ? error.message : String(error)}`,
      };
    }
    renderHistory();
  }

  function renderHistory() {
    const target = $("#historyArea");
    if (!target) return;
    if (state.historyLoading) {
      target.innerHTML = empty("正在读取历史索引。");
      return;
    }
    if (state.historyError) {
      target.innerHTML = empty(`历史索引不可得：${state.historyError}`);
      return;
    }
    if (!state.historyLoaded) {
      target.innerHTML = empty("尚未读取历史索引。历史卡只用于原始资料查看。");
      return;
    }
    const items = filteredHistoryItems();
    const list = items.length ? `<div class="history-list">${items.map((item, index) => `
      <button type="button" class="history-item" data-history-index="${index}">
        <strong>${escapeHtml(`历史卡 ${index + 1}`)}</strong>
        <span class="muted">${escapeHtml([item.time, item.symbol, label(item.quality)].filter(Boolean).join("；"))}</span>
      </button>`).join("")}</div>` : empty("没有匹配的历史卡。");
    const raw = state.rawCard ? `<div class="raw-panel">
      <header><strong>${escapeHtml(state.rawCard.title)}</strong><span>原始 JSON</span></header>
      <pre>${escapeHtml(state.rawCard.text)}</pre>
    </div>` : "";
    target.innerHTML = `${list}${raw}`;
    document.querySelectorAll("[data-history-index]").forEach((button) => {
      button.addEventListener("click", () => {
        const item = items[Number(button.getAttribute("data-history-index"))];
        if (item) openHistoryRaw(item);
      });
    });
  }

  function renderDataHealth() {
    const target = $("#dataHealth");
    if (!target) return;
    const health = asObject(state.workbench && state.workbench.data_health);
    const products = asObject(health.products);
    if (!Object.keys(products).length) {
      target.innerHTML = empty("数据引擎尚未报告；不能沿用旧健康状态判断当前资料。");
      return;
    }
    const names = {
      "fmz.bridge.snapshots.v1": "FMZ 连续事实与传递",
      "fmz.spacegate.current.v1": "原生空间门",
      "gex.raw.board.v1": "原始 GEX 看板",
      "gex.effective.accepted.v1": "已接受的空间锚",
      "gex.pending.candidate.v1": "待接受 GEX 候选",
      "gex.netgamma.proxy.v1": "净 Gamma 门控输入",
      "deribit.btc.discovery.current.v1": "期权候选发现",
      "deribit.btc.option.instrument.v1": "期权合同资料",
      "deribit.btc.option.universe.v1": "期权全链合同目录",
      "deribit.btc.option.order_book.v1": "单腿公开盘口",
      "deribit.btc.index.v1": "Deribit BTC 指数",
      "deribit.btc.option.greeks_oi.v1": "期权 Delta 与持仓量",
      "deribit.btc.vertical.two_leg_book.v1": "两腿盘口及数量深度",
      "deribit.btc.option.fee_schedule.v1": "理论费用口径",
      "macro.risk_state.components.v1": "宏观分量与各自时钟",
      "macro.dxy.close.v1": "DXY 美元指数（未接通保留项）",
      "macro.usd.broad.close.v1": "FRED 广义美元 DTWEXBGS",
      "macro.us10y.nominal.v1": "美国十年期名义利率",
      "macro.us10y.real.v1": "美国十年期实际利率",
      "macro.vxn.close.v1": "纳斯达克波动背景",
      "risk.astra.qualified_model.domain.v1": "冻结模型与支持范围",
      "risk.astra.frozen_mu.reference.v1": "同候选冻结赔付参考",
      "btc.etf.flow.daily.v1": "ETF 完整交易日净流",
      "btc.brk.cost_basis.v1": "链上成本与群体",
      "btc.brk.realized_pnl.daily.v1": "链上已实现日损益",
      "cex.usdt.borrow_rate.v1": "USDT 借款参考",
      "btc.kpf.map.readonly.v1": "KPF 历史接受区与只读 MAP",
      "binance.spot.btcusdt.trade.v1": "币安现货成交",
      "binance.spot.btcusdt.book_ticker.v1": "币安现货盘口",
      "binance.spot.btcusdt.kline_1m.v1": "币安现货闭合分钟线",
      "binance.um.btcusdt.kline_1m.v1": "币安永续闭合分钟线",
      "binance.um.btcusdt.funding.settled.v1": "实际已结算资金费率",
      "binance.um.btcusdt.funding.predicted.v1": "下一期预测资金费率",
      "binance.um.btcusdt.oi.native.v1": "原生合约持仓量",
      "binance.um.btcusdt.oi.usd_notional.v1": "美元名义持仓量"
    };
    const states = { OK: "数据合格", USABLE: "可用于登记用途", PARTIAL: "部分资料",
      STALE: "数据过期", INVALID: "数据不合格", ERROR: "采集失败", MISSING: "未报告资料",
      NOT_CONNECTED: "尚未接入", CLOCK_UNTRUSTED: "时钟不可信", NOT_CURRENT: "非当前版本",
      HISTORICAL_IMPORT_ONLY: "仅历史背景", USAGE_LIMITED: "部分用途已不可用", SIMULATION: "模拟展示，非真实资料",
      FIRST_SEEN_AFTER_NOW: "首次可得时间异常" };
    const usages = {
      health: "技术健康",
      fetch: "采集复用",
      current_fact: "当前事实",
      contract_definition: "合同边界",
      underwriting_accounting: "保单核算",
      freeze: "证据冻结",
      default: "默认用途",
      history: "历史查看",
      data_health: "健康面板",
      space_gate: "空间门",
      space_layer: "空间层",
      gamma_regime: "Gamma 体制",
      candidate_quote: "候选报价",
      discovery: "候选发现",
      contract: "合同边界",
      quote: "公开盘口",
      fee: "理论费用",
      risk: "冻结风险",
      economics: "补偿账",
      macro_layer: "宏观层",
      macro_context: "宏观背景",
      price_path: "价格路径",
      funding: "资金费率",
      option_structure: "期权结构",
      background: "背景参考",
      diagnostic_only: "诊断参考"
    };
    const groups = [
      {
        title: "FMZ 与空间门",
        purpose: "连续事实、原生空间门、Anchor 与 Gamma 门控",
        ids: ["fmz.bridge.snapshots.v1", "fmz.spacegate.current.v1", "gex.raw.board.v1",
          "gex.effective.accepted.v1", "gex.pending.candidate.v1", "gex.netgamma.proxy.v1"],
        critical: ["fmz.bridge.snapshots.v1", "fmz.spacegate.current.v1", "gex.raw.board.v1",
          "gex.effective.accepted.v1", "gex.netgamma.proxy.v1"]
      },
      {
        title: "价格量价与永续",
        purpose: "BTC 现货路径、盘口、闭合分钟、永续资金费与持仓",
        ids: ["binance.spot.btcusdt.trade.v1", "binance.spot.btcusdt.book_ticker.v1",
          "binance.spot.btcusdt.kline_1m.v1", "binance.um.btcusdt.kline_1m.v1",
          "binance.um.btcusdt.funding.settled.v1", "binance.um.btcusdt.funding.predicted.v1",
          "binance.um.btcusdt.oi.native.v1", "binance.um.btcusdt.oi.usd_notional.v1"],
        critical: ["binance.spot.btcusdt.trade.v1", "binance.spot.btcusdt.book_ticker.v1",
          "binance.spot.btcusdt.kline_1m.v1", "binance.um.btcusdt.kline_1m.v1"]
      },
      {
        title: "期权合同与保单报价",
        purpose: "同到期合同、Delta、单腿盘口、两腿估算与费用",
        ids: ["deribit.btc.discovery.current.v1", "deribit.btc.option.instrument.v1",
          "deribit.btc.option.universe.v1", "deribit.btc.option.order_book.v1",
          "deribit.btc.index.v1", "deribit.btc.option.greeks_oi.v1",
          "deribit.btc.vertical.two_leg_book.v1", "deribit.btc.option.fee_schedule.v1"],
        critical: ["deribit.btc.discovery.current.v1", "deribit.btc.option.instrument.v1",
          "deribit.btc.option.order_book.v1", "deribit.btc.index.v1",
          "deribit.btc.vertical.two_leg_book.v1", "deribit.btc.option.fee_schedule.v1"]
      },
      {
        title: "冻结风险参考",
        purpose: "合格模型支持域与同候选冻结赔付参考",
        ids: ["risk.astra.qualified_model.domain.v1", "risk.astra.frozen_mu.reference.v1"],
        critical: ["risk.astra.qualified_model.domain.v1", "risk.astra.frozen_mu.reference.v1"]
      },
      {
        title: "宏观背景",
        purpose: "FRED广义美元、美债实际与名义利率、DXY保留项、VXN及综合压力分量",
        ids: ["macro.risk_state.components.v1", "macro.usd.broad.close.v1", "macro.dxy.close.v1",
          "macro.us10y.nominal.v1", "macro.us10y.real.v1", "macro.vxn.close.v1"],
        critical: []
      },
      {
        title: "BTC 资金成本与 MAP",
        purpose: "ETF、链上成本、实现盈亏、借款与历史接受区",
        ids: ["btc.etf.flow.daily.v1", "btc.brk.cost_basis.v1",
          "btc.brk.realized_pnl.daily.v1", "cex.usdt.borrow_rate.v1", "btc.kpf.map.readonly.v1"],
        critical: []
      }
    ];
    const knownIds = new Set(groups.flatMap((group) => group.ids));
    const extraIds = Object.keys(products).filter((id) => !knownIds.has(id));
    if (extraIds.length) {
      groups.push({ title: "其他登记来源", purpose: "已登记但未归入当前工作台主类的来源", ids: extraIds, critical: [] });
    }
    const generatedAt = safeNumber(health.generated_at_ms);
    const summaryAge = generatedAt === null ? null : Date.now() - generatedAt;
    const summaryMissing = generatedAt === null;
    const summaryFuture = summaryAge !== null && summaryAge < 0;
    const healthMode = String(firstPresent(health.mode, health.kind, health.data_identity, health.identity, "") ?? "").toLowerCase();
    const historicalReplay = health.historical_replay === true || isReplayPreview() || /historical|replay|回放/.test(healthMode);
    const staleSummary = !health.simulation_only && !historicalReplay && summaryAge !== null && summaryAge > 600_000;
    const summaryBlocksGreen = Boolean(summaryMissing || summaryFuture || staleSummary || health.simulation_only);
    const duration = (value) => {
      const numeric = safeNumber(value);
      if (numeric === null) return "未提供";
      if (numeric < 0) return `未来 ${fmtMsAge(Math.abs(numeric))}`;
      return fmtMsAge(numeric);
    };
    const usageLabel = (value) => usages[String(value || "")] || "登记用途";
    const statusLabel = (value) => states[String(value || "").toUpperCase()] || "需要核验";
    const sourceOwner = (id) => {
      if (id.startsWith("fmz.") || id.startsWith("gex.")) return "FMZ/空间数据链路与后端接收";
      if (id.startsWith("binance.")) return "Binance 公开行情适配器";
      if (id.startsWith("deribit.")) return "Deribit 公开期权适配器";
      if (id.startsWith("risk.")) return "Astra 冻结风险账";
      if (id.startsWith("macro.")) return "宏观背景适配器";
      if (id.startsWith("btc.") || id.startsWith("cex.")) return "BTC 背景与资金成本适配器";
      return "已登记适配器";
    };
    const itemTone = (status, failedBusiness) => {
      if (health.simulation_only || status === "SIMULATION") return "sim";
      if (["OK", "USABLE"].includes(status)) return failedBusiness.length ? "warn" : "good";
      if (["USAGE_LIMITED", "PARTIAL", "HISTORICAL_IMPORT_ONLY", "NOT_CONNECTED"].includes(status)) return "warn";
      if (["MISSING", "STALE", "INVALID", "ERROR", "CLOCK_UNTRUSTED", "NOT_CURRENT", "FIRST_SEEN_AFTER_NOW"].includes(status)) return "bad";
      return "warn";
    };
    const visibleTone = (tone) => (summaryBlocksGreen && tone === "good" ? "warn" : tone);
    const decisionText = (decision) => {
      const item = asObject(decision);
      const ttl = safeNumber(item.ttl_ms);
      const ttlText = ttl === null ? "用途期限未限制" : `用途期限 ${duration(ttl)}`;
      const useText = item.can_use === true ? "可用" : item.can_use === false ? "不可用" : "资格未报告";
      return `${usageLabel(item.usage)}：${statusLabel(item.status)}，${useText}，${ttlText}`;
    };
    const modelFor = (id) => {
      const product = asObject(products[id]);
      const current = asArray(product.current);
      const latest = asObject(current[current.length - 1]);
      const healthDecision = asObject(latest.usage_decision || product.usage_decision);
      const decisions = current.flatMap((entry) =>
        Object.values(asObject(entry.usage_decisions)).map(asObject).filter((item) => item.usage));
      const business = decisions.filter((item) => item.usage !== "health");
      const failedBusiness = business.filter((item) => item.can_use === false);
      const status = String(product.status || healthDecision.status || (current.length ? "USABLE" : "MISSING")).toUpperCase();
      const tone = itemTone(status, failedBusiness);
      const problemText = failedBusiness.length ? `${failedBusiness.length} 个用途不可用` :
        current.length ? "当前用途按登记期限核验" : "未收到当前版本";
      return { id, product, current, latest, healthDecision, business, failedBusiness, status, tone, problemText };
    };
    const groupTone = (group, items) => {
      if (health.simulation_only) return "sim";
      const critical = items.filter((item) => group.critical.includes(item.id));
      const hard = critical.length ? critical : [];
      if (hard.some((item) => item.tone === "bad")) return "bad";
      if (hard.some((item) => item.tone !== "good")) return "warn";
      if (items.some((item) => item.tone !== "good")) return "warn";
      if (summaryBlocksGreen) return "warn";
      return "good";
    };
    const groupStatus = (group, items, tone) => {
      const critical = items.filter((item) => group.critical.includes(item.id));
      const criticalIssues = critical.filter((item) => item.tone !== "good");
      const issueCount = items.filter((item) => item.tone !== "good").length;
      if (health.simulation_only) return "本地模拟";
      if (criticalIssues.some((item) => item.tone === "bad")) return "关键缺口";
      if (criticalIssues.length) return "用途受限";
      if (summaryFuture) return "汇总时间异常";
      if (summaryMissing) return "汇总时间缺失";
      if (staleSummary && tone === "warn") return "汇总需刷新";
      if (historicalReplay && !issueCount) return "冻结时点可用";
      if (issueCount) return "局部缺口";
      return "可用";
    };
    const groupBrief = (group, items) => {
      if (health.simulation_only) return group.purpose;
      const issueCount = items.filter((item) => item.tone !== "good").length;
      const base = issueCount ? `${group.purpose}；${issueCount} 项需处理。` : `${group.purpose}；当前用途均可用。`;
      if (summaryFuture) return `${base} 健康汇总时间在未来，需刷新后确认。`;
      if (summaryMissing) return `${base} 健康汇总时间缺失，需刷新后确认。`;
      if (staleSummary) return `${base} 汇总已超过页面技术守门时间，需刷新后确认。`;
      if (historicalReplay) return `${base} 冻结回放按资料时点展示资格；不代表当前可用，刷新不会把它变成当前事实。`;
      return base;
    };
    const renderMeta = (item) => {
      const latest = item.latest;
      const healthDecision = item.healthDecision;
      const visibleDecisions = (item.failedBusiness.length ? item.failedBusiness : item.business).slice(0, 3);
      const usageLine = visibleDecisions.length ? visibleDecisions.map(decisionText).join("；") : "仅有技术健康或尚无业务用途决策。";
      const extra = Math.max(0, item.business.length - visibleDecisions.length);
      const rows = [
        `来源责任：${sourceOwner(item.id)}`,
        `当前版本：${item.current.length ? `${item.current.length} 组` : "未收到"}`,
        `源观察时间：${fmtTime(latest.observation_end_ms)}`,
        `首次可得时间：${fmtTime(latest.first_seen_at_ms)}`,
        `技术健康年龄：${duration(healthDecision.age_ms)}`,
        `技术守门期限：${safeNumber(healthDecision.ttl_ms) === null ? "未限制" : duration(healthDecision.ttl_ms)}`,
        `用途决策：${usageLine}${extra ? `；另有 ${extra} 个用途折叠` : ""}`
      ];
      return `<details class="data-health-meta" data-health-path="${escapeHtml(`product/${item.id}`)}"><summary>来源 / 时钟 / 用途期限</summary><ul>${
        rows.map((rowText) => `<li>${escapeHtml(rowText)}</li>`).join("")
      }</ul></details>`;
    };
    const renderChild = (item) => {
      const tone = visibleTone(item.tone);
      const statusText = summaryBlocksGreen && item.tone === "good" ? "需刷新后确认" :
        (item.failedBusiness.length ? "部分用途不可用" : statusLabel(item.status));
      return `<div class="data-health-child ${tone}">
        <div>
          <strong>${escapeHtml(names[item.id] || "已登记数据产品")}</strong>
          <small>${escapeHtml(`${duration(item.healthDecision.age_ms)} · ${item.problemText}`)}</small>
        </div>
        <span class="data-health-state">${escapeHtml(statusText)}</span>
        ${renderMeta(item)}
      </div>`;
    };
    const renderGroup = (group) => {
      const items = group.ids.map(modelFor);
      const tone = groupTone(group, items);
      return `<details class="data-health-group ${tone}" data-health-path="${escapeHtml(`group/${group.title}`)}">
        <summary>
          <span class="data-health-title">
            <strong>${escapeHtml(group.title)}</strong>
            <small>${escapeHtml(groupBrief(group, items))}</small>
          </span>
          <span class="data-health-state">${escapeHtml(groupStatus(group, items, tone))}</span>
        </summary>
        <div class="data-health-children">${items.map(renderChild).join("")}</div>
      </details>`;
    };
    const topTone = health.simulation_only || historicalReplay ? "warn" : (summaryFuture || summaryMissing ? "bad" : (staleSummary ? "warn" : "good"));
    const topText = health.simulation_only
      ? "本区读取本机模拟资料；不能证明真实来源接通。"
      : historicalReplay
        ? `冻结回放资料 · ${fmtTime(health.generated_at_ms)}。按冻结时点展示资格，不代表当前可用。`
      : `${summaryFuture ? "汇总时间异常" : staleSummary ? "汇总需刷新" : summaryMissing ? "汇总时间缺失" : "汇总已更新"} · ${fmtTime(health.generated_at_ms)}`;
    const expanded = new Set(Array.from(target.querySelectorAll("details[open][data-health-path]"), (item) => item.dataset.healthPath));
    target.innerHTML = `<div class="data-health-ledger">
      <div class="callout ${topTone}">${escapeHtml(topText)}</div>
      <details class="data-health-meta" data-health-path="rules"><summary>统一时效与状态规则</summary><p>各项以数据引擎登记的用途期限与可用判断为准；关键输入失效不能由其他健康来源抵消。当前资料模式下，健康汇总超过 10 分钟或来自未来时需刷新后确认；冻结回放只按冻结时点展示资格，不代表当前可用。按日发布资料保留自己的发布与观察时钟。</p></details>
      <div class="data-health-grid">${groups.map(renderGroup).join("")}</div>
    </div>`;
    target.querySelectorAll("details[data-health-path]").forEach((item) => {
      item.open = expanded.has(item.dataset.healthPath);
    });
  }

  function renderScenarioControl() {
    const target = $("#scenarioControl");
    if (!target) return;
    const preview = asObject(state.workbench && state.workbench.preview);
    const scenarios = asArray(preview.scenarios);
    target.hidden = !isSyntheticPreview() || !scenarios.length;
    if (target.hidden) { target.innerHTML = ""; return; }
    target.innerHTML = `<label for="scenarioSelect">本地预登记场景</label> <select id="scenarioSelect">${scenarios.map((item) => `<option value="${escapeHtml(item.id)}" ${preview.scenario_id === item.id ? "selected" : ""}>${escapeHtml(item.label_cn)}</option>`).join("")}</select><span class="muted"> 切换只读模拟输入；回执按冻结版本区分，切换不调用 LLM。</span>`;
    $("#scenarioSelect")?.addEventListener("change", async (event) => {
      try {
        const data = await fetchJson(`${API_WORKBENCH}?scenario=${encodeURIComponent(event.target.value)}`);
        state.workbench = asObject(data);
        state.apiOnline = true;
        state.reviewMessage = "已切换预登记模拟输入；同版回执按当前候选读取，其他版本仅供回看。";
        renderAll();
      } catch (error) {
        state.reviewMessage = `模拟场景读取失败：${error.message || error}`;
        renderManualReviewState();
      }
    });
  }

  function renderAll() {
    const scope = disclosureScopeKey();
    const stored = disclosureStorageRead();
    const sameScope = scope && scope === state.disclosureScope;
    const resume = scope && !state.disclosureScope && stored.scope === scope && (stored.hash || "") === (window.location.hash || "");
    const readingY = sameScope ? window.scrollY : (resume ? stored.scrollY : null);
    renderStatus();
    renderPreviewBanner();
    renderScenarioControl();
    renderCurrentConclusion();
    renderMarketContext();
    renderGate();
    renderBtcMap();
    renderCandidates();
    renderLockedCandidates();
    renderManualReviewState();
    renderDataHealth();
    renderDailySnapshots();
    renderTransitions();
    renderGaps();
    renderHistory();
    if (scope) restoreGlobalDisclosures();
    if (resume && Number.isFinite(readingY)) {
      state.pendingHashOpen = false;
      state.pendingHashScroll = false;
    }
    if (scope && (state.pendingHashOpen || state.pendingHashScroll)) syncHashTarget({ scroll: state.pendingHashScroll });
    else if (Number.isFinite(readingY) && typeof window.scrollTo === "function") deferUi(() => window.scrollTo(0, readingY), 0);
    if (scope) state.disclosureScope = scope;
  }

  function beginReview(candidateIdValue, purpose = "review") {
    if (!candidateIdValue || state.reviewBusy) return;
    if (!secureReviewOrigin()) {
      state.reviewMessage = "访问验证与写操作只能通过 HTTPS 或本机页面提交。";
      renderManualReviewState();
      return;
    }
    state.pendingCandidateId = candidateIdValue;
    state.pendingReviewPurpose = purpose === "observation" ? "observation" : "review";
    state.pendingLockedEpisodeId = "";
    state.pendingLockedReviewSnapshotId = "";
    state.pendingTokenAction = "review";
    state.reviewMessage = "准备冻结当前候选。";
    if (!selectCandidate(candidateIdValue)) return;
    if (previewReviewAllowed()) {
      postReview();
      return;
    }
    if (!sessionAuthenticated()) {
      showTokenDialogFor("review", "需要先建立私人访问会话，才能发起本次显式评审。访问密钥只用于会话交换。");
      return;
    }
    postReview();
  }

  function makeIdempotencyKey() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return `review-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  async function postReview() {
    const candidateIdValue = state.pendingCandidateId;
    const previewMode = previewReviewAllowed();
    if (!candidateIdValue || (!sessionAuthenticated() && !previewMode)) return;
    state.reviewBusy = true;
    state.reviewMessage = previewMode
      ? "正在推理：本地模拟服务将返回模拟回执，零真实 LLM、非交易。"
      : (state.pendingReviewPurpose === "observation" ? "正在解释本单冻结观测资料；研究回执不改变主评审门或执行许可。" : (state.pendingLockedEpisodeId ? "正在对已锁定保单最新截面执行显式综合评审；不会覆盖 t0 基线。" : "正在推理：服务端正在冻结当前候选并执行同版因果评审。"));
    renderCandidates();
    renderManualReviewState();
    startReviewPolling();
    try {
      const key = makeIdempotencyKey();
      state.activeReviewKey = key;
      const payload = {
        candidate_id: candidateIdValue,
        idempotency_key: key,
      };
      if (state.pendingLockedEpisodeId) {
        payload.locked_episode_id = state.pendingLockedEpisodeId;
        payload.snapshot_id = state.pendingLockedReviewSnapshotId;
      }
      if (state.pendingReviewPurpose === "observation" && !previewMode) payload.purpose = "observation";
      if (previewMode) {
        payload.scenario = asObject(state.workbench.preview).scenario_id;
        const selected = sortedCandidates().find((candidate) => candidateId(candidate) === candidateIdValue);
        payload.snapshot_id = asObject(selected && selected.snapshot).snapshot_id;
      }
      const headers = { "Content-Type": "application/json" };
      const result = await fetchJson(API_REVIEW, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });
      const status = firstPresent(result.status, result.state, "ERROR");
      if (["OK", "SIMULATED", "RESEARCH_OK"].includes(status)) {
        state.reviewMessage = `${previewMode ? "本地模拟回执" : "本次冻结快照评审"}：${label(status)}。该结果只适用于提交时的保单，不代替刷新后的当前结构门或候选判断。`;
        await pollReviewState();
      } else {
        state.reviewBusy = false;
        stopReviewPolling();
        await loadWorkbench();
        state.reviewMessage = `本次未得到合格评审正文：${label(status)}。失败原样保留，不自动补发；${label(firstPresent(result.reason, asObject(result.review).status, "请查看缺口"))}。`;
      }
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      if (/401|403/.test(message)) state.session = { authenticated: false, expires_at_ms: null };
      try {
        const review = await loadReviewStateOnce();
        const inflight = asObject(review.inflight);
        const last = asObject(review.last_result);
        if (Object.keys(inflight).length) {
          state.reviewBusy = true;
          state.reviewMessage = "服务端仍在推理；页面继续只读等待回执，不补发请求。";
          startReviewPolling();
        } else if (state.activeReviewKey && last.idempotency_key === state.activeReviewKey) {
          state.reviewBusy = false;
          stopReviewPolling();
          await loadWorkbench();
          state.reviewMessage = "已从服务端读回本次冻结保单回执；没有补发 LLM。";
        } else {
          state.reviewBusy = false;
          stopReviewPolling();
          state.reviewMessage = `评审请求未完成：${message}。服务端未找到本次回执，请先核查状态。`;
        }
      } catch (_stateError) {
        state.reviewBusy = false;
        stopReviewPolling();
        state.reviewMessage = `评审请求状态不明：${message}。请核查服务端回执，不要重复点击。`;
      }
    } finally {
      renderCandidates();
      renderManualReviewState();
    }
  }

  function wireEvents() {
    $("#refreshButton")?.addEventListener("click", () => {
      loadWorkbench();
      loadLockedCandidates();
    });
    $("#reloadHistoryButton")?.addEventListener("click", loadHistory);
    $("#historySearch")?.addEventListener("input", (event) => {
      state.historyFilter = event.target.value;
      renderHistory();
    });
    $("#forgetTokenButton")?.addEventListener("click", () => {
      deleteSession();
    });
    $("#cancelTokenButton")?.addEventListener("click", () => {
      $("#tokenDialog")?.close();
      state.pendingCandidateId = "";
      state.pendingLockCandidateId = "";
      state.pendingTokenAction = "";
      state.reviewMessage = "已取消本次手动评审。";
      state.lockedMessage = "已取消需要访问验证的本单跟踪操作。";
      renderManualReviewState();
      renderLockedCandidates();
    });
    $("#tokenForm")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const input = $("#tokenInput");
      const value = input ? input.value.trim() : "";
      if (!value) return;
      $("#tokenDialog")?.close();
      continueTokenAction(value);
    });
    window.addEventListener("hashchange", () => {
      state.pendingHashOpen = true;
      state.pendingHashScroll = true;
      const lockedId = lockedRouteEpisodeId();
      if (lockedId) {
        state.activeLockedEpisodeId = lockedId;
        loadLockedEpisode(lockedId);
      }
      renderLockedCandidates();
      syncHashTarget({ scroll: true });
    });
    window.addEventListener("pagehide", () => disclosureStorageWrite(storedDisclosureKeys()));
  }

  function boot() {
    wireEvents();
    renderAll();
    loadSession();
    loadWorkbench();
    loadLockedCandidates();
    loadHistory();
    window.setInterval(loadWorkbench, POLL_MS);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  window.__astraWorkbenchTest = {
    constants: { API_WORKBENCH, API_REVIEW, API_REVIEW_STATE, API_WORKBENCH_SESSION, API_LOCKED_CANDIDATES, HISTORY_MANIFEST, POLL_MS, REVIEW_STATE_POLL_MS, MAX_DAILY_SNAPSHOTS },
    state,
    renderAll,
    renderCandidates,
    renderLockedCandidates,
    loadWorkbench,
    loadLockedCandidates,
    beginLockCandidate,
    beginLockedReview,
    loadLockedEpisode,
    downloadLockedExport,
    fetchLockedEvidence,
    lockedCapture,
    lockedExportHtml,
    hydrateRawFold,
    observationCapture,
    buildObservationCapture,
    publicObservationValue,
    label,
    setInMemoryToken(value) {
      state.session = { authenticated: Boolean(value), expires_at_ms: null };
      state.sessionMessage = Boolean(value) ? "测试会话已建立。" : "测试会话已清除。";
    },
  };
})();
