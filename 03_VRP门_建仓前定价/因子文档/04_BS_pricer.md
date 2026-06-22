# 04 · BS pricer（black_scholes_price_usd）

> 模块：②-门 VRP · 候选门重定价引擎
> canonical：`系统总纲\VRP\src\vrp_model.py:black_scholes_price_usd`（:240）
> 最后核对：2026-06-02（源码）

## 0. 轻量因子卡

| 字段 | 内容 |
|---|---|
| 因子 | BS pricer（black_scholes_price_usd） |
| 所属回路 | ②-门 VRP · 建仓前定价 |
| 作用层 | 风险门 / 审计 |
| 理论机制 | 用零利率短 DTE Black-Scholes 近似把两腿在 forward_vol_hurdle 下的应值重定价为候选门基准。 |
| 预期符号 | 无方向符号；输出价格为非负，净 credit 差用于计算 VRP edge。 |
| 适用周期 | VRP 候选门评估每条垂直候选时。 |
| 与现有因子重叠 | 与触界概率共用正态分布近似思想，但这里只服务重定价，不估计持仓风险。 |
| 主要失效条件 | sigma 百分比/小数混用、spot/strike/dte 缺失、长 DTE 或利率影响被忽略。 |
| 改变的决策 | 改变 hurdle 净 credit、candidate edge 和候选是否 PASS。 |
| 当前状态 | ACTIVE |

## 1. 一句话定位
零利率、短 DTE 近似的欧式期权 USD 定价器。候选门用它把"两腿在 forward_vol_hurdle 波动下应值多少"算成 ccy，从而得到 hurdle 净 credit 基准。

## 2. 当前具体实现（`vrp_model.py:black_scholes_price_usd`）
```
t = max(dte_hours / (24 × annualization_days), 1e-9)
d1 = (ln(spot/strike) + 0.5·σ²·t) / (σ·√t)
d2 = d1 − σ·√t
call = max(0, spot·N(d1) − strike·N(d2))
put  = max(0, strike·N(−d2) − spot·N(−d1))
```
- `N` = `_norm_cdf`（erf 实现）。
- 零利率近似（短 crypto DTE 合理）；`spot/strike/dte/σ` 任一 ≤0 → 返 0。
- 候选门里用法：`black_scholes_price_usd(...) / spot` 转成结算币口径，两腿之差 × amount = `hurdle_net_credit`。

## 3. 关键参数
`annualization_days=365`（`ScenarioConfig`）。无独立校准阈值（它是定价原语，非门控）。

## 4. 整合中的路径修改（收口）
- `_norm_cdf` 收口到 `hedge_risk._norm_cdf`（删 VRP 本地副本）。
- **BS pricer 本体是 VRP 带给整合的唯一保留新能力**——执行层 `plans.py`/`hedge_risk.py` 此前没有完整 BS 定价器，候选门的 hurdle 重定价依赖它。整合时保留 `black_scholes_price_usd` 作执行层共享原语。

## 5. 当前目标 / 待办
- 与对冲模块的触界概率（IV-based BS 障碍模型）共享 `_norm_cdf` 等数学原语，整合后统一来源。
- Phase 2 edge 验证会用它做成交级到期结果重算。

## 6. 边界与陷阱
- 零利率近似仅对短 DTE 成立——它服务 24–72h 卖方腿，不要用于长期限。
- 它是**定价原语**，不含任何门控阈值；门控在窗口门/候选门。
- σ 必须是分数口径（`normalise_iv` 归一后），传百分比会严重错价。
