# ②-门 VRP · 建仓前权利金风险补偿定价门 —— 总览

> 最后核对：2026-06-02（基于 `src/vrp_model.py` / `src/vrp_policy.py` 源码）
> 版本：`VRP_FACTOR_VERSION="1.1.0"`（`src/vrp_model.py:18`）；**v1.1 阶段性封版**
> 落点：执行层 ② 的 **PLAN 轮建仓前**（不是信号层）；**尚未嵌入执行层**（按收口契约落地）

---

## 0. 模块定位（第一性）
回答 v0.3 三模块都没答的卖方主问——**"这个被信号层放行的 expiry/侧，现在是否值得卖 vol"**。补的是执行层真实缺口：`plan_ev` 用 delta 当胜率 ≈ 风险中性、近同义反复，缺"权利金贵不贵"的物理补偿判断。

与"取消 KPF/SLRP"**不冲突**：KPF/SLRP 试图在市场定价外**再造空间层**（已证无样本外边际）；VRP 相反，**直接量市场定价本身够不够贵**（可执行 IV vs forward 波动门槛，扣完整摩擦），全用 Deribit 实时链 + 自算 RV，无外置重数据。

## 1. 结构：两层门
```
窗口门 assess_window  ── 廉价 vol-space 预筛（front 锚 IV vs forward_vol_hurdle + 期限结构/数据质量路由）
   │ PASS 的 expiry/侧 才进枚举
   ▼
候选门 assess_candidate ── 权威 ccy full-burn（两腿在 hurdle 下 BS 重定价的净 credit − 完整 round-trip 摩擦）
   │ BLOCK → 剔除该候选
   ▼
双门 PASS → 进执行层 S:PM/既有 PLAN 排序
```

## 2. 因子清单（4）

| # | 因子 | canonical | 一句话 |
|---|---|---|---|
| 01 | 窗口门 assess_window | `vrp_model.py:163` | 廉价 vol-space 预筛：`representative_vol_edge = front_iv − hurdle` |
| 02 | 候选门 assess_candidate | `vrp_model.py:280` | 权威 ccy full-burn：`candidate_edge = 可执行净credit − hurdle净credit − 完整摩擦` |
| 03 | forward_vol_hurdle | `vrp_model.py:118` | `rv_regime_anchor × percentile_adjustment × cold_start_multiplier`（纯标量）|
| 04 | BS pricer | `vrp_model.py:240` | 零利率短 DTE 欧式期权 USD 定价（hurdle 下重定价两腿）|

## 3. 整合规则（总纲 v0.4 §2-4）
- **独立 AND 双门**：VRP 与 EDB/GGR/宏观都过才交易；**VRP 肥不降 EDB 门**（肥权利金常是恐惧的价格）。
- **只过滤不抢权重**：多窗口 PASS 由既有 PLAN 排序选，VRP 不抢期号；不判方向、不选期、不进 `PLAN_WEIGHTS`、不解 `ALLOW_TRADING`。
- **只跑 EDB 背书侧**，不双侧评估。
- 面板上 **VRP edge（物理补偿）须与 `plan_ev`（风险中性、近 0）区分标注，不可相加**。

## 4. 整合收口契约（落地时执行，`docs/VRP执行层整合收口契约_v1.1.md`）
4 个重复原语收口到执行层 canonical（`vrp_model.py:77-85` 已标 INTEGRATION-RECONCILE）：
| VRP 本地副本 | → canonical |
|---|---|
| `normalise_iv` | `hedge_risk._normalise_iv` |
| `_norm_cdf` | `hedge_risk._norm_cdf` |
| `_option_fee` | `accounting.acct_option_fee_ccy` |
| `_spread_half_cost` | `accounting.acct_spread_cost` |
- **BS pricer 是唯一保留的新能力**（执行层没有，VRP 带来）。
- 候选字段 → `ExecutionPlanPackage`；`EntryRiskAnchor` 加 VRP 血缘（与对冲共 IV/vol 基线）；退役旧交接说明 v1.0 §5 宽松配置，canonical = `vrp_policy`。

## 5. 已验证 / 未验证（诚实边界）
- **已验证（真实数据）**：268,800 次压力遍历选出 `strict_cost_cold_guard_v1_1`（危险/冷启动/薄 edge 通过全 = 0）；90 天 leak-guarded 回放——低分位扩张不对称真实（realized/anchor 1.26 vs 高分位 0.68）、低分位+冷启动守护砍半 hurdle 击穿、mean realized/hurdle≈0.88<1 保护性。
- **未验证 / blocked**：卖方 **IV-vs-RV edge 未证**，blocked on 多时点期权 IV（只有单一同日快照）。须 cron 前向采集 Deribit 快照（复用 `tools/fetch_deribit_snapshot.py`），把 `vrp_replay` 升级为"IV 入场→真实到期结果"成交级回放，才第一次真测 edge。**所有阈值 `PLACEHOLDER_CALIBRATION_REQUIRED`**。封的是安全门+回放机具+收口契约，**不是 edge**。

## 6. 选中策略参数（`vrp_policy.py:selected_policy_config`，覆盖 ScenarioConfig 默认）
`strict_cost_cold_guard_v1_1`：`low/high_percentile_multiplier=1.25/0.92`、`cold_start_multiplier=1.35`、`term_backwardation_ratio=1.18`、`min_candidate_edge_ccy=0.00005`、`min_window_vol_edge=0.02`、`spread_round_trip_multiplier=3.0`。
> 选择依据：268,800 评估、candidate_pass=300、danger_pass=0、cold_start_pass=0、avg_pass_edge≈0.0000949、robust_score≈33.98。

## 7. canonical 源与本工程快照
- **权威实现**：`C:\Users\Xu\Documents\系统总纲\VRP\src\*.py`（独立 harness）
- **本工程快照**：`03_VRP门_建仓前定价\交付物快照\`（src + README + docs：封版说明v1.1 / 收口契约v1.1 / 海量场景模拟v1.0 / 模拟成绩调试v1.0 / 产物清单v1.0）
- tests（25 通过）/ outputs（268k 遍历 csv）/ data（快照）仍在 canonical VRP 目录，未复制进本工程。

## 8. 数据增强（gexmonitorapi，2026-06-04）
- 新增独立 `gex_info_cross_check(window_assessment, gex_info, config)`：用 API `iv_rv_ratio` 对 VRP 自算 vol-edge 做"期权贵不贵"一致性校验、`term_structure` 对倒挂路由交叉校验、冷启动暴露 `iv_rv` 弱外部先验。**`applied_to_gate=False`，绝不改 sealed hurdle/gate、不构成第二道权利金门**。tests 现 **31 通过**（25 sealed + 6 新，含"cross-check 不改 sealed window gate"）。详见 [`00_总纲/gexmonitorapi数据增强引入说明_v1.0`](../../00_总纲/gexmonitorapi数据增强引入说明_v1.0.md)。
