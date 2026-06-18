# VRP 执行层整合收口契约 v1.1

日期：2026-06-02  
适用路径：`C:\Users\Xu\Documents\系统总纲\VRP` → `C:\Users\Xu\Documents\Deribit期权交易执行层`  
定位：规定 VRP v1.1 阶段性封版包**如何并入执行层 PLAN 轮**。这是收口契约，不是实盘开关；落地时按本契约把 VRP 的独立原语收口到执行层 canonical 实现，避免两套真值源。本契约取代旧 `VRP执行层集成交接说明_v1.0.md` 中 §5 的宽松 baseline 配置（已退役）。

## 0. 一句话

VRP 以**只读前置过滤器**身份进 `ROUND_MODE="PLAN"`：窗口门先筛 expiry/侧，候选门再筛结构；它**复用执行层已有的费率/价差/记账原语**（不自带第二套），**只在 EDB 背书侧运行**，输出写入候选对象与 `EntryRiskAnchor`，**不进 ORDER、不改 `ALLOW_TRADING`、不进主排序权重**。

## 1. 重复原语收口（消除冗余的核心）

VRP 研究态 harness 为自洽而自带了一份原语；并入执行层时**必须**改绑下列 canonical 实现，删除本地副本，否则两套 Deribit 费率/IV 标准化会漂移：

| VRP 本地（`src/vrp_model.py`） | 执行层 canonical | 收口动作 | 校验点 |
| --- | --- | --- | --- |
| `normalise_iv` | `hedge_risk._normalise_iv` | 删本地，import canonical | percent/decimal 阈值一致（>3 判 percent） |
| `_norm_cdf` | `hedge_risk._norm_cdf` | 删本地，import canonical | 同 erf 实现 |
| `_option_fee`(min(0.0003,12.5%)) | `accounting.acct_option_fee_ccy` | 删本地，调用 canonical | **费率常量唯一来源 = accounting**；ScenarioConfig 不再硬编 fee_cap/fee_rate |
| `_spread_half_cost` | `accounting.acct_spread_cost` | 改调用 canonical | **核对半价差 vs 全价差口径**：VRP 用 (ask-bid)/2，须与 acct_spread_cost 对齐或显式换算 |
| 候选净 credit `(short_bid-protection_ask)*amt` | `plans.plan_effective_credit` | 用 canonical 算净 credit | VERTICAL 模式两腿同到期口径一致 |
| `full_round_trip_friction` | `accounting.acct_*` 组合 | 用 fee+spread+`acct_protection_realized_cost` 组装 | 与执行层 full-burn 记账同口径 |

**唯一保留为 VRP 自带的新能力**：`black_scholes_price_usd`（hurdle 重定价用）。执行层用市场 mark 报价、不重定价期权，故 BS pricer 由 VRP 引入；但其内部 `_norm_cdf` 仍改用 `hedge_risk._norm_cdf`。

> 收口后 VRP 变成"薄门层"：窗口/候选判定逻辑 + BS hurdle 重定价 + RV 缓冲，其余算术全部落在执行层 canonical 上。

## 2. PLAN 轮插入点

执行层 PLAN 现链路（`strategy.py`）：枚举候选 → `plan_assemble` → `plan_prelim_score`/`plan_rank` → 方案库。VRP 插入为：

```text
SignalEvidencePackage 放行(EDB/GGR/macro) + DIRECTION_BIAS 定侧
  → [VRP 窗口门] 对每个信号背书 expiry/侧 assess_window
        BLOCK / DISTORTED_REVIEW → 该 expiry 不进枚举
  → 只对 PASS 窗口枚举候选(既有 leg_selection，DTE/Delta 硬范围不变)
  → [VRP 候选门] 对每条垂直候选 assess_candidate(扣 full-burn 净 edge)
        BLOCK → 剔除该候选
  → 双门 PASS 的候选 → plan_assemble / 既有 PLAN 排序 / S:PM / 面板
```

硬边界：
- **只跑 EDB 背书侧**：`DIRECTION_BIAS=SHORT_CALL` 只评 call 侧窗口，反之亦然。研究态 harness 双侧都评是为压力遍历；执行层只评放行侧（交接说明 §6 下一步 2）。
- VRP 不扩 `SHORT_DTE_HOURS=(24,72)`、`SHORT_DELTA_RANGE=(0.15,0.45)`；只在其内过滤。
- VRP `BLOCK` 不可被"方向很强/权利金很肥"越过；VRP 也不可越过 `NO_TRADE_BLOCKED`/GGR veto。独立 AND 双门。

## 3. 候选字段 → ExecutionPlanPackage

执行层候选对象（进 `ExecutionPlanPackage.menu`）新增：

| 字段 | 来源 | 用途 |
| --- | --- | --- |
| `vrp_window_id` | `assess_window` | 窗口血缘 |
| `window_vrp_gate` | `assess_window` | PASS/BLOCK/DISTORTED_REVIEW |
| `forward_vol_hurdle` | `assess_window` | 入场 hurdle |
| `candidate_vrp_gate` | `assess_candidate` | PASS/BLOCK |
| `candidate_vrp_edge_ccy` | `assess_candidate` | 扣 full-burn 净 edge（结算币） |
| `full_round_trip_friction` | `assess_candidate` | 完整摩擦 |
| `vrp_reason_codes` | 两级门 | 面板/审计漏斗 |

## 4. 排序边界（不重复计权）

- v1 **不**把 `vrp_residual_score` 加进 `PLAN_WEIGHTS`。理由：`rr` 已含权利金/最大损失，`win_rate`(=`1-|delta|`) 与 IV surface 重叠，VRP 再进权重 = 同一信息三次计权（soul.md §2 禁）。
- 关键澄清：执行层 `plan_ev = win_rate×credit −(1−win_rate)×max_loss` 用 delta 当胜率 ≈ **风险中性期望、结构上近 0**；VRP 的 hurdle 净 edge 才是"IV 是否高过物理 RV 门槛"的有信息量度量。**面板上 VRP edge 与 plan_ev 并列展示时须标注：plan_ev 为风险中性参考、VRP edge 为物理补偿口径**，不可相加。
- VRP edge 是否进权重，待全链多时点回放联合标定后再议。

## 5. EntryRiskAnchor 血缘（与对冲模块共基线）

成交后写入（对齐对冲模块 §4.1）：

```json
{
  "entry_vrp_window_id": "",
  "entry_executable_short_iv": 0,
  "entry_forward_vol_hurdle": 0,
  "entry_candidate_vrp_edge_ccy": 0,
  "entry_vrp_reason_codes": []
}
```

收益：入场理由（VRP 认定够贵时的 IV/hurdle）与对冲模块的 `entry_iv`/`entry_touch_probability` **共用同一 IV/vol 基线**，避免两套真值源；对冲只读此血缘，不反向重做 VRP。

## 6. 配置 canonical 化

- 实现层 canonical 配置 = `src/vrp_policy.py` 的 `strict_cost_cold_guard_v1_1`（海量遍历选出：`cold_start 1.35`、`spread 3.0x`、`min_candidate_edge 0.00005`）。
- **退役** `VRP执行层集成交接说明_v1.0.md §5` 的宽松 baseline（`cold_start 1.1`/`min_window_vol_edge 0`），勿再引用。
- 所有阈值仍 `PLACEHOLDER_CALIBRATION_REQUIRED`，进权重/解 `ALLOW_TRADING` 前须多时点回放标定。

## 7. 落地前验收（与封版说明一致）

1. 重复原语已收口到 canonical，单测覆盖等价性。
2. 窗口/候选门只在 EDB 背书侧运行。
3. PLAN 面板显示 VRP 拒绝漏斗。
4. `ALLOW_TRADING=False` 下全链空跑、不产订单。
5. `EntryRiskAnchor` 保存 VRP 血缘，对冲模块读同一基线。
6. tests 覆盖窗口门、候选门、数据降级、EntryRiskAnchor 映射、收口等价性。
7. 多时点 snapshot 回放（非单快照）后才谈进排序权重或解闸。

## 8. 不可越界（同总纲 v0.4 / soul.md）

不接 KPF/SLRP、不引外置重数据、不进 ORDER/实盘开关、VRP 肥不降 EDB/GGR/宏观门、skew 不同时驱动 SRD 与 VRP、日历保护不复用同期垂直 full-burn 口径、不写死经验阈值。
