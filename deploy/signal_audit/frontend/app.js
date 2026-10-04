(() => {
  "use strict";

  const API_WORKBENCH = "/api/workbench";
  const API_REVIEW = "/api/workbench/review";
  const API_REVIEW_STATE = "/api/workbench/review-state";
  const HISTORY_MANIFEST = "signal_cards/index.json";
  const POLL_MS = 60_000;
  const REVIEW_STATE_POLL_MS = 3_000;
  const MAX_DAILY_SNAPSHOTS = 15;
  const loopbackHosts = ["localhost", "127.0.0.1", "[::1]", "::1"];
  const isLoopbackHost = () => loopbackHosts.includes(window.location.hostname);
  const secureReviewOrigin = () => window.location.protocol === "https:" || isLoopbackHost();

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
    pendingCandidateId: "",
    activeReviewKey: "",
    selectedMapCandidateKey: "",
    selectedMapRegionId: "",
    selectedMapRegionContextKey: "",
    mapAxisMode: "focus",
  };

  let inMemoryToken = "";
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
      auth_required: "需要服务端令牌",
      sent: "已发送",
      failed: "失败",
      pending_transition: "等待下一次合格发送",
      contract: "合同边界",
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
      PARTIAL: "部分可用",
      GAP: "缺口",
      FACTS_UNTIL_MANUAL_REVIEW: "仅事实，待人工审查",
      before_payout: "赔付开始前",
      partial_payout: "部分赔付区",
      beyond_protection: "保护腿之外",
      partial_payout_at_near_be: "跨近端 BE",
      before_payout_at_short: "触及卖出腿",
      beyond_protection_at_long: "触及保护腿",
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
    return date.toLocaleString("zh-CN", { hour12: false });
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

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, {
      cache: "no-store",
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.headers || {}),
      },
    });
    if (!response.ok) {
      throw new Error(`${response.status} ${response.statusText}`.trim());
    }
    return response.json();
  }

  async function loadWorkbench() {
    state.loading = true;
    renderStatus();
    try {
      const scenario = isSyntheticPreview() ? asObject(state.workbench.preview).scenario_id : "";
      const data = await fetchJson(scenario ? `${API_WORKBENCH}?scenario=${encodeURIComponent(scenario)}` : API_WORKBENCH);
      state.workbench = asObject(data);
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
      ? "冻结回放 · 非当前交易 · 零真实 LLM"
      : (loopback ? "本地模拟 · 零真实 LLM · 执行关闭" : "模拟标记 · 非本机入口 · 演示关闭");
    const message = replay
      ? "本机冻结回放来自真实公开来源的历史截面；它只保留原始身份，不授予当前 FMZ 事实、真实 LLM 复核或交易许可。"
      : (loopback
        ? "本机模拟服务只演示工作台资料层级与手动回执流程；不可作为当前 FMZ 事实、真实 LLM 结果、账户允许或可交易判断。"
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
      ${metric("事实身份", preview ? "本地模拟场景（非当前事实）" : label(firstPresent(context.status, "unknown")), preview ? "零真实 LLM、非交易；只演示资料层级" : "来自 FMZ fact_context")}
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
    if (!rows.length) return `<p class="muted">本行未提供可展示主数字。</p>`;
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
    return firstPresent(presentation.mode, presentation.mode_cn, presentation.data_identity, presentation.presentation_mode, map.data_identity, map.mode, "FIXTURE");
  }

  function mapIdentityText(map) {
    const text = String(mapDataIdentity(map) ?? "");
    const raw = text.toUpperCase();
    if (text.includes("合成") || text.includes("模拟")) return "合成演示";
    if (text.includes("回放") || text.includes("历史")) return "冻结回放";
    if (text.includes("实盘") || text.includes("当前")) return "当前资料";
    if (raw.includes("FIXTURE") || raw.includes("SYNTHETIC")) return "合成演示";
    if (raw.includes("REPLAY") || raw.includes("HISTORICAL")) return "冻结回放";
    return "当前资料";
  }

  function mapCurrentPriceLabel(map) {
    const identity = mapIdentityText(map);
    if (identity === "冻结回放") return "冻结现价";
    if (identity === "合成演示") return "示例现价";
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

  function renderNominalReferenceList(model) {
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
    return `<div class="map-nominal-list" aria-label="名义异源与非同刻对照">
      <strong>名义 / 异源 / 非同刻对照</strong>
      <ul>${rows.map((row) => `<li>
        <button type="button" data-map-region-id="${escapeHtml(row.id)}">${escapeHtml(row.title)}</button>
        <span>${escapeHtml(row.value)}</span>
        <small>${escapeHtml(row.note)}</small>
      </li>`).join("")}</ul>
    </div>`;
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
          <button type="button" class="map-axis-mode ${model.mode === "focus" ? "primary" : ""}" data-map-axis-mode="focus">聚焦本单</button>
          <button type="button" class="map-axis-mode ${model.mode === "all" ? "primary" : ""}" data-map-axis-mode="all">显示全部</button>
        </div>
      </div>
      ${renderMapAxisSvg(model, typeof window !== "undefined" && safeNumber(window.innerWidth) !== null && safeNumber(window.innerWidth) <= 620)}
      ${renderNominalReferenceList(model)}
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
          <p class="muted">请在右侧选择 Put 或 Call 保单后查看两腿、净信用、M_ref 与近端 BE；未选时不借用任一侧数值。</p>
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
    const mRefText = safeNumber(mRef) === null ? "M_ref 缺口" : fmtBtc(mRef);
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
        ${metric("M_ref / 缺口", mRefText, safeNumber(mRef) === null ? "服务端未返回 M_ref" : "服务端冻结补偿余量")}
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
      ${renderMapPolicyIdentity(context, selectedItem)}
      ${qualificationNote}
      ${renderMapFactSummary(context)}
      <div class="map-layout">
        <section class="map-panel" aria-labelledby="mapBackgroundTitle">
          <div class="map-panel-head"><h3 id="mapBackgroundTitle">四行背景</h3><p>配置、库存、融资和外部条件各自保留来源时钟与缺口。</p></div>
          ${renderMapBackground(map)}
        </section>
        <section class="map-panel" aria-labelledby="mapAxisTitle">
          <div class="map-panel-head"><h3 id="mapAxisTitle">数值价格轴</h3><p>线性数值坐标；显示价、原价、价系和期限分开，合成/回放/当前资料固定标识。</p></div>
          ${renderReferenceAxis(context)}
        </section>
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
        : `工作台 API 已连接，FMZ 当前事实或原生 SpaceGate 不可得：${updated}。候选仅供缺口审计；60 秒轮询，不自动调用 LLM。`;
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
          <p class="muted">本页不会用旧静态卡代替当前工作台。左侧历史区只用于打开原始资料。</p>
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
    let headline = "当前不准入";
    let basis = "结构门或 FMZ 当前事实未满足，候选只能作为缺口审计。";
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
        const status = reviewGate(candidate).enabled === true ? (preview ? "可演示" : "可评审") : (preview ? "不可演示" : "不可评审");
        return `<p><strong>${escapeHtml(side)}</strong> ${escapeHtml(legs)}<br>${escapeHtml(`c ${fmtBtc(c)}；M ${fmtBtc(mRef)}；${status}`)}</p>`;
      }).join("")
      : `<p>未返回 Put/Call 候选</p>`;
    target.innerHTML = `
      <div class="gate-headline">
        <div>
          <div class="gate-state is-${tone}">${escapeHtml(headline)}</div>
          <p class="muted">${escapeHtml(basis)}</p>
        </div>
        <div class="badge-row">
          ${badge(preview ? "本地模拟场景" : (factCurrent ? "FMZ 当前事实可用" : "FMZ 当前事实缺口"), preview ? "warn" : (factCurrent ? "good" : "bad"))}
          ${badge(enabledCount ? `${enabledCount} 侧${preview ? "可演示" : "可评审"}` : `暂无${preview ? "可演示" : "可评审"}候选`, preview ? "warn" : (enabledCount ? "good" : "warn"))}
        </div>
      </div>
      <div class="current-candidate-summary" aria-label="当前候选摘要">${candidateSummary}</div>
      <p class="muted">${escapeHtml(`${gateStateText(gate)}；${fmtTime(firstPresent(state.workbench.updated_at_ms, state.workbench.updated_at, state.lastLoadedAt))}；两侧并列`)}。</p>
      <p><strong>下一步</strong> ${escapeHtml(preview ? "选择模拟保单，核对两问" : (enabledCount ? "人工决定是否锁定候选评审" : "先处理阻断或缺口"))}。</p>
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
    if (!secureReviewOrigin()) reasons.push("手动评审令牌只允许在 HTTPS 或本机页面提交");
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
    const netFeeBtc = economics.theoretical_two_leg_fee_btc;
    const netFeeUsd = economics.theoretical_two_leg_fee_usd_at_spot;
    const netCreditAfterFeeBtc = economics.net_credit_after_entry_fee_btc;
    const netCreditAfterFeeUsd = economics.net_credit_after_entry_fee_usd_at_spot;
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
    const buttonTitle = reviewBusy ? "同版因果评审正在推理，双侧按钮暂时禁用" : (enabled ? (candidatePreview ? "本地模拟评审回执" : "锁定候选并综合评审") : (reasons.length ? reasons.map((item) => label(item)).join("；") : "服务端未允许评审"));
    const buttonText = reviewBusy ? "正在推理" : (candidatePreview ? "模拟评审回执" : "锁定候选并综合评审");
    return `<article class="candidate" data-candidate-id="${escapeHtml(id)}">
      <div class="candidate-header">
        <div class="candidate-title">
          <h3>${escapeHtml(side)} 候选</h3>
          ${badge(candidatePreview ? (enabled ? "可演示评审" : "模拟入口关闭") : (enabled ? "可手动评审" : "不可评审"), candidatePreview ? "warn" : (enabled ? "good" : "bad"))}
        </div>
        <p class="muted">${escapeHtml(candidatePreview ? "本地模拟候选；用于比较预登记场景与回执流程，不代表真实盘口、真实 LLM 或交易许可。" : (candidateReplay ? "冻结回放候选；真实公开来源的历史截面，非当前交易或执行许可，原始标识只保存在底层证据中。" : (candidate.status === "snapshot" ? "当前服务端候选；原始标识只保存在底层证据中。" : "本侧尚未形成合格保单；请看缺口与反证。")))}</p>
      </div>
      <div class="candidate-brief">
        <p><strong>${escapeHtml(firstPresent(contract.short_instrument, "卖出腿未提供"))}</strong> / ${escapeHtml(firstPresent(contract.long_instrument, "保护腿未提供"))}<br><span class="muted">${escapeHtml(fmtBtc(contract.quantity_btc))} · ${escapeHtml(remainingLabel)} ${escapeHtml(remainingText || "未提供")}</span></p>
        <div class="metric-grid" aria-label="本单核心补偿">
          ${metric("权利金差 c", fmtBtc(premiumSpreadBtc), "顺序分腿估值，未成交")}
          ${metric("总费用 f", fmtBtc(fee.amount_btc), "按冻结的持有口径")}
          ${metric("冻结赔付 μ_ref", fmtBtc(economics.mu_ref_btc), candidatePreview ? (safeNumber(economics.mu_ref_btc) === null ? "本场景未提供模拟参考" : "模拟数值已提供，非真实模型资格") : label(risk.status || "未提供"))}
          ${metric("参考余量 M_ref", fmtBtc(mRef), "参考账，非执行许可")}
        </div>
        <p class="muted">${escapeHtml(tailNote)} ${escapeHtml(liability.account_margin_btc == null ? "账户保证金与维护约束未进入本账。" : "账户保证金参考不证明维护约束已满足。")}执行许可关闭。</p>
        <p class="muted">${escapeHtml(`同版评审：${label(reviewStatus)}`)}${review.summary_cn ? `；${escapeHtml(review.summary_cn)}` : ""}</p>
        <div class="action-row">
          <button type="button" class="primary review-button" data-candidate-id="${escapeHtml(id)}" ${enabled && id ? "" : "disabled"} title="${escapeHtml(buttonTitle)}">${escapeHtml(buttonText)}</button>
          <button type="button" class="candidate-map-button" data-candidate-id="${escapeHtml(id)}">查看本单地图</button>
        <a class="candidate-review-link" data-candidate-id="${escapeHtml(id)}" href="#reviewStateTitle">查看两问与回执</a>
        </div>
      </div>
      <details class="candidate-details" data-disclosure-key="${escapeHtml(disclosureKey)}"><summary>展开合同、费用拆分、责任表与来源说明</summary>
      <div class="candidate-body">
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
          ${kv("净费用", fmtBtcWithUsd(netFeeBtc, netFeeUsd), "两腿理论成交手续费")}
          ${kv("净信用", fmtBtcWithUsd(netCreditAfterFeeBtc, netCreditAfterFeeUsd), "权利金差 − 双腿理论成交手续费；Deribit 顺序分腿盘口只是理论估算，未成交")}
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
          <p>${escapeHtml(reviewBusy ? "服务端已有运行中回执，页面只读轮询；Put/Call 两侧按钮同步禁用。" : (enabled ? (candidatePreview ? "点击只会调用本机模拟回执；零真实 LLM、非交易、不会产生当前事实许可。" : "点击后仅冻结该候选并请求同版一次因果审查；LLM 不改写 c、μ_ref、f 或 M_ref。") : buttonTitle))}</p>
        </div>

        <div class="callout"><strong>责任与尾部</strong><p>${escapeHtml(tailNote)}</p>${payoutRows ? `<p class="muted">支付表：${escapeHtml(payoutRows)}</p>` : ""}</div>

        ${gaps.length ? `<div class="callout bad"><strong>缺口/反证</strong><p>${escapeHtml(gaps.map((item) => label(item)).join("；"))}</p></div>` : ""}

      </div>
      </details>
    </article>`;
  }

  function renderCandidates() {
    const target = $("#candidateArea");
    if (!target) return;
    const candidates = sortedCandidates();
    if (!candidates.length) {
      target.innerHTML = empty("工作台服务尚未返回 Put/Call 候选。结构门失败时仍应返回可解释的缺口。");
      return;
    }
    const expanded = new Set(Array.from(target.querySelectorAll("details[open][data-disclosure-key]"), (item) => item.dataset.disclosureKey).filter(Boolean));
    target.innerHTML = `<div class="candidate-grid">${candidates.map(renderCandidate).join("")}</div>`;
    target.querySelectorAll("details[data-disclosure-key]").forEach((item) => {
      item.open = expanded.has(item.dataset.disclosureKey);
    });
    document.querySelectorAll(".review-button").forEach((button) => {
      button.addEventListener("click", () => beginReview(button.getAttribute("data-candidate-id")));
    });
    target.querySelectorAll(".candidate-map-button").forEach((button) => {
      button.addEventListener("click", () => {
        if (!selectCandidate(button.getAttribute("data-candidate-id"))) return;
        $("#btcMapTitle")?.scrollIntoView({ block: "start" });
      });
    });
    target.querySelectorAll(".candidate-review-link").forEach((link) => {
      link.addEventListener("click", () => selectCandidate(link.getAttribute("data-candidate-id")));
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

  function renderTwoQuestions(review) {
    const q1 = asObject(review.q1);
    const q2 = asObject(review.q2);
    if (!Object.keys(q1).length || !Object.keys(q2).length) {
      return `<p class="muted">本回执未包含现行两问，不由页面补写结论。</p>`;
    }
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
    const observableChanges = asArray(q2.observable_changes_cn).filter(Boolean).slice(0, 2);
    const catalogueIds = [
      ...asArray(review.selected_claim_ids),
      ...asArray(review.selected_counter_ids),
      ...asArray(review.selected_gap_ids),
      ...asArray(review.selected_reassess_condition_ids),
    ].filter(Boolean).slice(0, 8);
    return `<div class="two-question-grid" aria-label="同版保单两问">
      <section><h3>Q1 · 环境与责任位置</h3>
        <p>${escapeHtml(q1.summary_cn || "未提供适配判断")}</p>
        ${compensationCounter ? `<p class="muted">补偿侧反证归入 Q2；不代表上述环境与位置无法解释。</p>` : `<p><strong>主要竞争解释</strong><br>${escapeHtml(q1.competing_explanation_cn || "未提供，不能视为排除竞争解释")}</p>`}
      </section>
      <section><h3>Q2 · ${escapeHtml(q2.title_cn || "这份补偿是否值得继续评估")}</h3>
        <p class="review-summary">${escapeHtml(q2.summary_cn || "未提供联合取舍")}</p>
        <p><strong>当前交换与最强反证</strong><br>${escapeHtml(exchangeText)}</p>
        <p><strong>风险覆盖边界</strong><br>${escapeHtml(q2.coverage_cn || "未提供风险与 μ 的覆盖证据；页面不补写逐项归因。")}</p>
        <p><strong>可观察变化</strong></p>
        ${observableChanges.length ? `<ul>${observableChanges.map((item) => `<li>${escapeHtml(label(item))}</li>`).join("")}</ul>` : `<p class="muted">服务端未给出本单化可观察变化；页面不自行补写。</p>`}
        <p><strong>等待代价</strong><br>${escapeHtml(q2.waiting_cost_cn || "未知，不估算未来保费或机会成本")}</p>
        <details><summary>展开责任与重审条件</summary>
          ${q2.liability_cn ? `<p><strong>合同责任边界</strong><br>${escapeHtml(q2.liability_cn)}</p>` : ""}
          ${catalogueIds.length ? `<p><strong>有限目录来源</strong><br>${escapeHtml(catalogueIds.map((item) => label(item)).join("；"))}</p>` : ""}
          <strong>什么改变才值得再看</strong><ul>${reviewList(q2.reassess_if_cn, "未提供改变条件。")}</ul>
        </details>
      </section>
    </div>`;
  }

  function renderReviewResultDetail(result) {
    if (!Object.keys(result).length) return "";
    const review = reviewPayload(result);
    const resultStatus = firstPresent(result.status, review.status, "未提供");
    if (!["OK", "attached", "SIMULATED"].includes(resultStatus)) {
      return `<div class="callout warn" style="margin-top: 14px;"><strong>本次无合格评审正文：${escapeHtml(label(resultStatus))}</strong><p>保留失败或中断回执；页面不会自动补发 LLM，也不会把旧结论附着到当前保单。</p></div>`;
    }
    const snapshotId = firstPresent(result.snapshot_id, review.reviewed_snapshot_id, result.reviewed_snapshot_id);
    const sameCandidate = sortedCandidates().find((candidate) => candidateId(candidate) === firstPresent(result.candidate_id, review.candidate_id));
    const attachedReview = asObject(sameCandidate && sameCandidate.review);
    const sameVersion = sameCandidate && snapshotId === asObject(sameCandidate.snapshot).snapshot_id
      && (!result.map_id || result.map_id === candidateBtcMap(sameCandidate).map_id)
      && ["OK", "attached", "SIMULATED"].includes(attachedReview.status)
      && attachedReview.reviewed_snapshot_id === snapshotId
      && attachedReview.response_protocol === review.response_protocol;
    const simulated = isSyntheticPreview() || firstPresent(result.status, review.status) === "SIMULATED";
    const currentCandidate = sortedCandidates().find((candidate) => candidateId(candidate) === result.candidate_id);
    const side = firstPresent(result.side, currentCandidate && currentCandidate.side);
    const receiptAt = firstPresent(result.reviewed_at_ms, review.reviewed_at_ms, result.recorded_ts_ms, review.recorded_ts_ms);
    const detail = `<div class="review-detail ${simulated ? "is-simulated" : ""}">
      <div class="badge-row">
        ${badge(simulated ? "模拟回执：非真实 LLM" : "服务端回执", simulated ? "warn" : "blue")}
        ${side ? badge(`${label(side)} 保单`, "blue") : ""}
        ${badge(simulated ? (sameVersion ? "模拟同版保单" : "上一次模拟保单") : (sameVersion ? "同版当前保单" : "上一次冻结保单"), sameVersion ? "good" : "warn")}
        ${badge(`综合复核状态：${({ EXPLAINED: "依据可解释", CONTESTED: "存在反证", UNABLE_TO_JUDGE: "暂不能判定" })[review.applicability] || label(firstPresent(review.applicability, result.status, review.status, "未提供"))}`, "blue")}
      </div>
      ${receiptAt ? `<p class="muted">本回执时间 ${escapeHtml(fmtTime(receiptAt))}</p>` : ""}
      ${firstPresent(review.summary_cn, result.summary_cn) ? `<p class="review-summary">${escapeHtml(firstPresent(review.summary_cn, result.summary_cn))}</p>` : ""}
      ${renderTwoQuestions(review)}
      <details><summary>展开本次选择的支持、反证与来源</summary>
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
    return sameVersion ? detail : `<div class="callout warn"><strong>历史回执 · 当前候选尚无同版结论</strong><p>以下回执绑定旧冻结输入，不能解释当前候选的补偿或环境适配；需显式评审当前版本。</p><details><summary>查看旧版结论与来源</summary>${detail}</details></div>`;
  }

  function renderManualReviewState() {
    const target = $("#manualReviewState");
    if (!target) return;
    const review = manualReviewState();
    const inflight = asObject(review.inflight);
    const busy = manualReviewBusy() ? badge("正在推理", "warn") : "";
    const previewToken = previewReviewAllowed() ? badge("本地模拟无需真实 Bearer", "warn") : "";
    const token = previewReviewAllowed() ? "" : (inMemoryToken ? badge("令牌在本页内存中", "blue") : badge("未输入令牌", "warn"));
    const budget = asObject(review.daily_http_budget);
    const lastResult = asObject(review.last_result);
    const selected = selectedMapContext().candidate;
    const attached = asObject(selected && selected.review);
    const selectedResult = selected && ["OK", "attached", "SIMULATED"].includes(attached.status)
      ? { ...attached, candidate_id: candidateId(selected), side: selected.side, review: attached }
      : null;
    const contract = asObject(selected && firstPresent(asObject(selected.snapshot).contract, selected.contract, {}));
    const selectedDetail = selected
      ? `<h3>当前查看 · ${escapeHtml(label(selected.side))} 保单</h3><p class="muted">${escapeHtml(firstPresent(contract.short_instrument, "卖出腿未提供"))} / ${escapeHtml(firstPresent(contract.long_instrument, "保护腿未提供"))}</p>${selectedResult ? renderReviewResultDetail(selectedResult) : empty("该候选尚无同版回执；查看已有结果不会发起评审。")}`
      : renderReviewResultDetail(lastResult);
    const isSelectedLast = selectedResult && candidateId(selected) === lastResult.candidate_id
      && attached.reviewed_snapshot_id === firstPresent(lastResult.snapshot_id, asObject(lastResult.review).reviewed_snapshot_id)
      && ["OK", "attached", "SIMULATED"].includes(lastResult.status)
      && attached.response_protocol === reviewPayload(lastResult).response_protocol;
    const rows = [
      metric("全局冷却", review.cooldown_remaining_ms > 0 ? "冷却中" : "当前无冷却", `剩余 ${fmtMsAge(review.cooldown_remaining_ms)}`),
      metric("共享 HTTP 预算", Object.keys(budget).length ? `${fmtNumber(firstPresent(budget.http_calls_used, budget.used), 0)} / ${fmtNumber(firstPresent(budget.http_calls_limit, budget.cap), 0)}` : "未配置", `北京日 ${firstPresent(budget.date_bjt, budget.bjt_date, budget.date, "未提供")}`),
      metric("运行中任务", Object.keys(inflight).length ? "正在推理" : "无", "任务身份由服务端持久化，前端不展示原始标识"),
      metric("全局最近评审", label(firstPresent(lastResult.status, lastResult.state, typeof review.last_result === "string" ? review.last_result : undefined, "未发生")), fmtTime(firstPresent(review.last_completed_ts_ms, lastResult.reviewed_at_ms))),
    ].join("");
    target.innerHTML = `
      <div class="badge-row" style="margin-bottom: 12px;">${token}${previewToken}${busy}</div>
      <div class="metric-grid">${rows}</div>
      <div class="callout" style="margin-top: 12px;">${escapeHtml(state.reviewMessage || (previewReviewAllowed() ? "当前为本地模拟服务：按钮只演示回执流程，不调用真实 LLM，也不传生产令牌。" : "只有服务端确认本候选满足全部条件时，按钮才会发起一次综合评审。重复请求、冷却、预算和鉴权由服务端裁定。"))}</div>
      ${selectedDetail}
      ${selected && Object.keys(lastResult).length && !isSelectedLast ? `<details><summary>查看全局最近一次回执（独立于当前选侧）</summary>${renderReviewResultDetail(lastResult)}</details>` : ""}`;
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
    target.innerHTML = `<label for="scenarioSelect">本地预登记场景</label> <select id="scenarioSelect">${scenarios.map((item) => `<option value="${escapeHtml(item.id)}" ${preview.scenario_id === item.id ? "selected" : ""}>${escapeHtml(item.label_cn)}</option>`).join("")}</select><span class="muted"> 切换只读模拟输入；回执按冻结版本区分，零真实 LLM。</span>`;
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
    renderStatus();
    renderPreviewBanner();
    renderScenarioControl();
    renderCurrentConclusion();
    renderMarketContext();
    renderGate();
    renderBtcMap();
    renderCandidates();
    renderManualReviewState();
    renderDataHealth();
    renderDailySnapshots();
    renderTransitions();
    renderGaps();
    renderHistory();
  }

  function beginReview(candidateIdValue) {
    if (!candidateIdValue || state.reviewBusy) return;
    if (!secureReviewOrigin()) {
      state.reviewMessage = "手动评审令牌只能通过 HTTPS 或本机页面提交。";
      renderManualReviewState();
      return;
    }
    state.pendingCandidateId = candidateIdValue;
    state.reviewMessage = "准备冻结当前候选。";
    if (!selectCandidate(candidateIdValue)) return;
    if (previewReviewAllowed()) {
      postReview();
      return;
    }
    if (!inMemoryToken) {
      const dialog = $("#tokenDialog");
      const input = $("#tokenInput");
      if (dialog && typeof dialog.showModal === "function") {
        input.value = "";
        dialog.showModal();
        input.focus();
      } else {
        const supplied = window.prompt("请输入服务端 Bearer Token。本页只保存在内存中。");
        if (supplied) {
          inMemoryToken = supplied.trim();
          postReview();
        }
      }
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
    if (!candidateIdValue || (!inMemoryToken && !previewMode)) return;
    state.reviewBusy = true;
    state.reviewMessage = previewMode
      ? "正在推理：本地模拟服务将返回模拟回执，零真实 LLM、非交易。"
      : "正在推理：服务端正在冻结当前候选并执行同版因果评审。";
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
      if (previewMode) {
        payload.scenario = asObject(state.workbench.preview).scenario_id;
        const selected = sortedCandidates().find((candidate) => candidateId(candidate) === candidateIdValue);
        payload.snapshot_id = asObject(selected && selected.snapshot).snapshot_id;
      }
      const headers = { "Content-Type": "application/json" };
      if (!previewMode) headers.Authorization = `Bearer ${inMemoryToken}`;
      const result = await fetchJson(API_REVIEW, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });
      const status = firstPresent(result.status, result.state, "ERROR");
      if (["OK", "SIMULATED"].includes(status)) {
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
      if (/401|403/.test(message)) inMemoryToken = "";
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
    $("#refreshButton")?.addEventListener("click", loadWorkbench);
    $("#reloadHistoryButton")?.addEventListener("click", loadHistory);
    $("#historySearch")?.addEventListener("input", (event) => {
      state.historyFilter = event.target.value;
      renderHistory();
    });
    $("#forgetTokenButton")?.addEventListener("click", () => {
      inMemoryToken = "";
      state.reviewMessage = "已清除本页内存中的服务端令牌。";
      renderManualReviewState();
    });
    $("#cancelTokenButton")?.addEventListener("click", () => {
      $("#tokenDialog")?.close();
      state.pendingCandidateId = "";
      state.reviewMessage = "已取消本次手动评审。";
      renderManualReviewState();
    });
    $("#tokenForm")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const input = $("#tokenInput");
      const value = input ? input.value.trim() : "";
      if (!value) return;
      inMemoryToken = value;
      $("#tokenDialog")?.close();
      postReview();
    });
  }

  function boot() {
    wireEvents();
    renderAll();
    loadWorkbench();
    loadHistory();
    window.setInterval(loadWorkbench, POLL_MS);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  window.__astraWorkbenchTest = {
    constants: { API_WORKBENCH, API_REVIEW, API_REVIEW_STATE, HISTORY_MANIFEST, POLL_MS, REVIEW_STATE_POLL_MS, MAX_DAILY_SNAPSHOTS },
    state,
    renderAll,
    renderCandidates,
    label,
  };
})();
