# 03 · forward_vol_hurdle（前向波动门槛）

> 模块：②-门 VRP · 两层门共用的核心标量
> canonical：`系统总纲\VRP\src\vrp_model.py:forward_vol_hurdle`（:118）
> 最后核对：2026-06-02（源码）

## 0. 轻量因子卡

| 字段 | 内容 |
|---|---|
| 因子 | forward_vol_hurdle（前向波动门槛） |
| 所属回路 | ②-门 VRP · 建仓前定价 |
| 作用层 | 风险门 |
| 理论机制 | 用多窗口 RV anchor、RV 分位修正和冷启动保护生成卖方至少需要拿到的前向 IV 门槛。 |
| 预期符号 | hurdle 越高越保守；front IV 高于 hurdle 才可能形成卖方 edge。 |
| 适用周期 | VRP 窗口门与候选门共用，每个 expiry/side 或候选评估时计算。 |
| 与现有因子重叠 | 是 assess_window 和 assess_candidate 的共同标量来源，避免各处自建 hurdle。 |
| 主要失效条件 | RV 缺失、历史天数不足、分位冷启动、波动 regime 突变导致门槛滞后。 |
| 改变的决策 | 改变窗口 PASS、候选重定价和 edge 阈值判断的基准。 |
| 当前状态 | ACTIVE |

## 1. 一句话定位
VRP 的核心标量门槛："卖方至少要拿到多高的 IV 才算够补偿"。窗口门拿它比 front IV、候选门拿它给两腿 BS 重定价。

## 2. 当前具体实现（`vrp_model.py:forward_vol_hurdle`）
```
rv_regime_anchor = 加权平均(rv_24h, rv_72h, rv_7d, 权重 0.45/0.35/0.20)
percentile_adjustment：
    rv_percentile ≤ low_threshold(0.25)  → low_multiplier(1.25)   （低分位 → 抬高门槛）
    rv_percentile ≥ high_threshold(0.75) → high_multiplier(0.92)  （高分位 → 放松门槛）
    缺失                                  → 1.0（记 RV_PERCENTILE_MISSING）
cold_start_multiplier：
    history_days < min_history_days(30)  → cold_start_multiplier(1.35)   （冷启动 → 抬高门槛）
forward_vol_hurdle = rv_regime_anchor × percentile_adjustment × cold_start_multiplier
```
- `rv_regime_anchor` 缺（全无 RV）→ 返回 None（`RV_REGIME_ANCHOR_MISSING`），窗口门 BLOCK。
- 自算 RV from perp 1h closes（`deribit_snapshot.py`），无外置重数据。
- **期限倒挂/加速/数据质量走 `DISTORTED_REVIEW` 路由，不进 hurdle 标量**（总纲 v0.4 §4.1）——hurdle 保持纯标量。

## 3. 关键阈值（选中策略 `strict_cost_cold_guard_v1_1`）
`rv_weights=(0.45,0.35,0.20)`、`low/high_percentile_threshold=0.25/0.75`、`low/high_percentile_multiplier=1.25/0.92`、`cold_start_multiplier=1.35`、`min_history_days=30`。均 PLACEHOLDER。

## 4. 整合中的路径修改
**不收口**（纯计算，无重复原语）。整合后窗口门/候选门都从同一处取 hurdle，保持单一标量来源。

## 5. 当前目标 / 待办（已验证的核心证据）
- 90 天 leak-guarded 回放证实：**低分位扩张不对称真实**（realized/anchor 低分位 1.26 vs 高分位 0.68）→ 低分位抬门槛(1.25)有据；冷启动守护(1.35)显著降 hurdle 击穿（72h 低桶 0.39 vs 无调节 0.79；冷启动段 0.08 vs 0.39）；mean realized/hurdle≈0.88<1（hurdle 平均保护性）。
- 待 Phase 2 多时点 IV：用真实 IV 入场验 hurdle 是否真能筛出正 edge。

## 6. 边界与陷阱
- hurdle 是 **vol（IV/RV 同口径分数）**，不是 ccy；窗口门用它算 vol-edge，候选门用它喂 BS 转 ccy。
- 低分位**抬高**门槛、高分位**放松**——方向别记反（低 vol 时更要求厚补偿，因低 vol 易不对称扩张）。
- 冷启动守护是"样本不足时更保守"，不是常态乘子。
