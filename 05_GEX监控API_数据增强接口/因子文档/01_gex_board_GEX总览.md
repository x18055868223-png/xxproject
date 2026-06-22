# 01 · gex_board（GEX 总览）

> 模块：⑤ GEX Monitor API · 数据增强接口
> canonical：`src/gexmonitorapi/parsers.py` + `src/gexmonitorapi/models.py`
> 最后核对：2026-06-19（r2.2 文档收纳）

## 0. 轻量因子卡

| 字段 | 内容 |
|---|---|
| 因子 | gex_board（GEX 总览） |
| 所属回路 | ⑤ GEX 数据增强接口 |
| 作用层 | 风险门 / 审计 |
| 理论机制 | 把外部 GEX 页面中的净 Gamma、DVOL 与市场状态整理为只读背景，帮助识别当前 Gamma 环境。 |
| 预期符号 | NEUTRAL_CONTEXT / RISK_OVERLAY |
| 适用周期 | GEX API 刷新轮 / 审计卡物料化轮。 |
| 与现有因子重叠 | 与信号层 GGR、Gamma regime、LLM Gamma lens 重叠，但只提供外部背景，不覆盖内部门控。 |
| 主要失效条件 | 页面结构变化、样本冷启动、标的价格与期权链不匹配、缓存陈旧。 |
| 改变的决策 | 改变审计页 Gamma/GEX 展示与人工复核重点，不改变方向、置信、阻断或交易许可。 |
| 当前状态 | ACTIVE |

## 1. 一句话定位

`gex_board` 给出当前市场的 Gamma 背景摘要，主要用于回答“当前是否处在偏正 Gamma、偏负 Gamma、还是过渡区”的只读上下文问题。

## 2. 当前字段

| 字段 | 语义 | 审计解释 |
| --- | --- | --- |
| `total_net_gex` | 净 Gamma 名义额 | 方向和绝对强度都重要；负值通常表示更容易放大波动 |
| `dvol` | Deribit implied volatility 指标 | 可与 IV/RV 和权利金贵贱判断交叉参考 |
| `market_state` | 页面归纳出的市场状态 | 只作外部状态标签，不覆盖内部 GGR |

## 3. 整合路径

`/v1/info.gex_board` 会进入信号审计 JSON 的 `factor_cross_section.gex_info`，并在前端 Gamma/GEX 重点区展示。若字段缺失，应显示缺失原因，而不是用旧值冒充当前值。

## 4. 边界与陷阱

- `total_net_gex` 的正负不是交易方向。
- `market_state` 是外部页面语义，不能直接等价于系统内部 `gamma_regime.regime`。
- netGEX 的 rank 需要同时看方向分位和绝对值分位。
