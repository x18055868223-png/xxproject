# 04 · flow（期权资金流）

> 模块：⑤ GEX Monitor API · 数据增强接口
> canonical：`src/gexmonitorapi/parsers.py` + `/v1/info.flow`
> 最后核对：2026-06-19（r2.2 文档收纳）

## 0. 轻量因子卡

| 字段 | 内容 |
|---|---|
| 因子 | flow（期权资金流） |
| 所属回路 | ⑤ GEX 数据增强接口 |
| 作用层 | 触发 / 审计 |
| 理论机制 | 将外部期权 call/put premium、put/call ratio 与异常摘要作为期权资金流背景，辅助解释风险偏好。 |
| 预期符号 | OPTION_FLOW_CONTEXT |
| 适用周期 | GEX API 刷新轮 / 审计卡展示轮。 |
| 与现有因子重叠 | 与信号层 micro flow/CVD 和 SRD 重叠，但不替代现货主动流，也不单独投票。 |
| 主要失效条件 | 页面文本不可解析、call premium 被卖方结构误读、期权 flow 与现货 CVD 混用。 |
| 改变的决策 | 改变审计页资金流解释和 rank 显示，不改变系统方向或交易许可。 |
| 当前状态 | ACTIVE |

## 1. 一句话定位

`flow` 用于观察近期期权权利金流向，是 GEX 页面给出的资金行为背景，不等同于现货主动买卖流。

## 2. 当前字段

| 字段 | 语义 | 审计解释 |
| --- | --- | --- |
| `call_premium` | Call 侧权利金 | 与 put premium 对比观察偏向 |
| `put_premium` | Put 侧权利金 | 保护需求或下行押注的一种外部信号 |
| `call_put_bias` | 页面展示的 Call share 文本 | 例如 `38% Call`，应解析为 `flow.call_share_pct` rank |
| `put_call_ratio` | flow P/C | 资金流的 put/call 相对强度 |
| `abnormal_signal` | 页面异常/摘要文本 | 只作解释，不作为硬信号 |

## 3. 整合路径

信号层状态栏可展示 Call share 与 Flow P/C；审计页在 GEX Rank 区展示 `flow.call_share_pct` 和 `flow.put_call_ratio` 的历史分位。

## 4. 边界与陷阱

- 期权 flow 与现货 CVD 不同，不能互相替代。
- Call premium 高并不必然看涨，可能对应卖出 call 或结构性仓位。
- 页面文本若不可解析，应保留原文并标注缺失。
