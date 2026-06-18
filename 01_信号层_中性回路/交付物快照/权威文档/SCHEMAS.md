# nrd.schema.v0.5.3 公共输出契约

## DecisionSnapshot

顶层快照保留运行结论、模块状态、因子快照、运行事实与策略推荐。

不再包含候选组合或订单意图。旧版候选腿、报价、风险预算和下单预览字段已经删除，而不是置空兼容。

## StrategyRecommendation

`demo/strategy.py` 的公共输出。方向来自 EDB：当 EDB 有可交易 lean 时由 EDB 定向，否则回落 TMV-F 并显式标注为 legacy 预览。

字段：

- `signal`
- `strategy_code`
- `strategy_type`
- `expiry_24h`
- `expiry_48h`
- `summary`
- `selection_reason`（`EDB_DIRECTION` 或 `TMVF_LEGACY_PREVIEW`；无方向时为空）
- `direction_source`（`EDB` 或 `TMVF_LEGACY_PREVIEW`）
- `edb_lean` / `edb_support` / `edb_confidence`（透传 EDB 倾向、支持标签、置信）
- `market_state`
- `order_layer = external_execution_program`
- `execution_boundary = signal_and_strategy_only`

`expiry_24h` 与 `expiry_48h` 都是期号描述对象，包含目标小时数、Deribit 期号、到期时间戳、距离目标小时数的偏差和数据状态。
（注：`FactorSnapshot` 内嵌的是 `_strategy_factors` 投影子集——signal / strategy_code / strategy_type / expiry_24h / expiry_48h / summary / selection_reason / order_layer。）

禁止出现：

- concrete legs
- orders
- pricing
- quantity
- limit price
- risk snapshot
- cost snapshot

## FactorSnapshot

只公开前置信号与观察因子：

- `anchor`
- `flow`
- `macro_pressure`
- `m_die`
- `neutral_repair_signal`
- `edb`
- `skew`（SRD）
- `gamma_regime`（GGR）
- `signal_events`
- `strategy_recommendation`

不公开期权报价、组合风险或深度成本辅助字段。`bias_thesis` v0.51 起退役为 EDB 内部复用的 verdict helper，**不再单独进 FactorSnapshot**。

## MacroPressureFactor

MPF 每小时刷新一次。输出包含：

- `macro_score`
- `macro_regime`
- `summary_label_cn`
- `macro_data_confidence`
- `data_status`
- `last_data_time`
- `data_age_ms`
- `components`

组件包含当前值、参考值、3d 变化、计分贡献、中文真实含义和当前观测解读。

## M-DIE

M-DIE 使用最新闭合 1m K 线计算 15m 单向变化程度。前端展示以 `m_die` 带符号倾向值为主，不再把同一数值拆成方向与分数重复展示。

## NeutralRepairPreSignal

`neutral_repair_signal` 是 DIE+Anchor 修复时序状态机输出。它是**时序门**（决定窗口何时开），不作为主模块，也不直接触发交易信号，更不决定方向。

字段：

- `threshold_profile`
- `state`
- `is_active`
- `label`
- `confidence`
- `event_context`
- `anchor_context`
- `gating`
- `reason_codes`
- `interpretation_cn`

核心状态包括 `NR_IDLE`、`NR_DISPLACEMENT_ACTIVE`、`NR_WAIT_ANCHOR_DAMAGE`、`NR_WAIT_ANCHOR_REPAIR`、`NR_REPAIR_CANDIDATE`、`NR_REPAIR_CONFIRMED`、`NR_REPAIR_STALE`、`NR_DATA_INSUFFICIENT`。

## EDB（到期窗口方向合成层，权威方向层）

`edb` 把六条独立证据的有符号方向票合成后验方向，并据一致度/覆盖度/GGR 给出置信。它是前置信号层，不选腿/报价/下单，不进 `module_results`。

- `EDB_score = Σ(vote·eff_weight) / Σ eff_weight ∈ [-1,1]`，`eff_weight = weight·clamp(|vote|/informative_abs,0,1)`
- `置信 = 100·|EDB_score|·一致度·覆盖度·GGR乘子`（GGR 否决或宏观/资金硬阻断时置信归零）

字段：

- `precondition`（`nr_active` / `nr_state`，时序门状态）
- `edb_score` / `edb_score_raw`
- `agreement`（一致度）/ `coverage`（覆盖度）/ `confidence`
- `lean`（`BULLISH(_STRONG/_WEAK)` / `BEARISH(_STRONG/_WEAK)` / `NEUTRAL`）
- `side_hint` / `support_label`（`TRADE_SUPPORT_STRONG/WEAK` / `WAIT_CONFIRMATION` / `NO_TRADE_BLOCKED`）/ `next_action`
- `conflict_level`（`NONE/MILD/MATERIAL/SEVERE`）
- `ggr_gate`（`regime` / `multiplier` / `veto`）、`veto_reason`
- `evidence`（每条证据：`key` / `vote` / `weight` / `eff_weight` / `info` / `detail`）
- `reason_codes` / `summary_cn`

证据 key：`TMV` / `CVD_4h` / `CVD_12h` / `MACRO` / `FUNDING` / `SRD` / `GGR_SPATIAL`。

## SRD（25Δ 风险逆转方向票）

`skew` 纯算 Deribit greeks，输出有符号方向票供 EDB。方向取**相对偏斜**（`rr_z` vs 滚动基线）+ **动量**（`delta_rr`），**绝不取 `rr_blend` 原始符号**（BTC 25Δ 偏斜结构性为负）。

字段：`factor_name` / `factor_version` / `per_expiry` / `rr_blend` / `skew_norm_blend` / `rr_z` / `delta_rr` / `vote` / `vote_confidence` / `lean` / `data_state`（`OK/MISSING/INSUFFICIENT`）/ `reason_codes`。

## GGR（全局 Gamma 区制门 + 空间钉）

`gamma_regime` 纯算 gexmonitor flip/walls + Deribit 每档 gamma×OI。**首先是单边卖权安全门**（负 Gamma 放大→砍置信/否决），其次置信调制，最后在钉住区给受限空间票。

字段：`factor_name` / `factor_version` / `regime`（`POSITIVE_GAMMA_PINNING` / `NEGATIVE_GAMMA_AMPLIFYING` / `TRANSITION` / `UNKNOWN`）/ `regime_strength` / `flip_point` / `asset_price` / `distance_to_flip_pct` / `net_gex_sign` / `net_gamma_notional` / `max_gamma_strike` / `max_gamma_oi_share` / `pin` / `gate_action` / `confidence_multiplier` / `spatial_vote` / `spatial_weight` / `veto` / `data_state` / `reason_codes`。

## SignalEvents

`signal_events` 是运行内存中的最近 10 次成功信号事件表，只记录 `NR_REPAIR_CONFIRMED` 的 DIE+Anchor 修复信号。

每条事件包含：

- `episode_id` / `confirmed_time` / `price_at_confirmation` / `price_at_event`
- `episode_direction` / `peak_m_die` / `peak_abs_m_die` / `event_count_merged`
- `anchor_score_at_event` / `min_anchor_score_after_event` / `anchor_score_at_confirmation` / `anchor_nd_at_confirmation`
- `edb_lean` / `edb_support` / `edb_confidence` / `edb_score` / `conflict_level` / `side_hint`
- `tmv_blend` / `tmv_direction` / `macro_score` / `macro_regime`
- `fast_4h_cvd_btc` / `fast_4h_return_pct` / `slow_12h_cvd_btc`
- `srd_rr_blend` / `ggr_regime` / `funding_rate`

该列表用于前端全量观察和盘后复盘，不持久化到磁盘。
