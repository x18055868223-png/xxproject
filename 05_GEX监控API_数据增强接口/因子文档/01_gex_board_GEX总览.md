# 01 · gex_board（GEX 总览）

> 模块：⑤ GEX Monitor API · 数据增强接口
> canonical：`src/gexmonitorapi/parsers.py` + `src/gexmonitorapi/models.py`
> 最后核对：2026-06-19（r2.2 文档收纳）

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
