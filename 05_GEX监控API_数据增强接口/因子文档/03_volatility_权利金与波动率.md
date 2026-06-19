# 03 · volatility（权利金与波动率）

> 模块：⑤ GEX Monitor API · 数据增强接口
> canonical：`src/gexmonitorapi/parsers.py` + `/v1/info.volatility`
> 最后核对：2026-06-19（r2.2 文档收纳）

## 1. 一句话定位

`volatility` 描述期权权利金和波动率截面，帮助判断“当前期权是否贵、偏斜是否极端、期限结构是否异常”。

## 2. 当前字段

| 字段 | 语义 | 审计解释 |
| --- | --- | --- |
| `iv_rv_ratio` | 隐含波动率相对实现波动率 | 大于 1 通常代表权利金相对更贵；不能直接推导收益 |
| `pcr` | Put/Call ratio | 用于观察保护需求或看跌拥挤度 |
| `term_structure` | 各到期 ATM IV 与 25D skew | 辅助识别近端/远端波动结构和偏斜 |

## 3. 整合路径

该区块进入 `factor_cross_section.gex_info.volatility`，可与 SRD、funding、TMV、macro 一起解释信号截面。rank 中的 `volatility.iv_rv_ratio` 和 `volatility.pcr` 用于显示当前相对历史位置。

## 4. 边界与陷阱

- IV/RV 不是胜率，也不是直接交易 edge。
- PCR 高低需要结合价格行为和 flow 解释。
- term structure 的 skew 为外部页面截面，应保留原始数值和到期标签。
