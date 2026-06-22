# 02 · gamma_exposure（关键价位）

> 模块：⑤ GEX Monitor API · 数据增强接口
> canonical：`src/gexmonitorapi/parsers.py` + `/v1/info.gamma_exposure`
> 最后核对：2026-06-19（r2.2 文档收纳）

## 0. 轻量因子卡

| 字段 | 内容 |
|---|---|
| 因子 | gamma_exposure（关键价位） |
| 所属回路 | ⑤ GEX 数据增强接口 |
| 作用层 | 风险门 / 时间先验 / 审计 |
| 理论机制 | 将 flip、pin、call wall、put wall、magnet 等外部关键位映射为空间约束背景，观察现价与期权结构的贴合度。 |
| 预期符号 | NEUTRAL_SPATIAL_CONTEXT |
| 适用周期 | GEX API 刷新轮 / 信号审计展示轮。 |
| 与现有因子重叠 | 与内部 GGR 的 `flip_point`、`pin_strike` 和 `distance_to_pin_pct` 重叠，但外部来源需单独标注。 |
| 主要失效条件 | 关键位缺失、跨标的数据源混入、外部页面延迟或字段语义变化。 |
| 改变的决策 | 改变审计页空间约束解释和人工排查项，不改变执行层授权。 |
| 当前状态 | ACTIVE |

## 1. 一句话定位

`gamma_exposure` 提供 flip、pin、wall、magnet 等关键价位，用于观察价格与期权结构空间约束的贴合程度。

## 2. 当前字段

| 字段 | 语义 | 使用方式 |
| --- | --- | --- |
| `flip_point` | Gamma 体制可能切换的关键价位 | 观察现价是否接近正负 Gamma 分界 |
| `volatility_trigger` | 波动触发位 | 辅助判断是否进入更高波动环境 |
| `spot_price` | GEX 页面对应现价 | 用于校验数据截面时间和价格来源差异 |
| `magnet_price` / `magnet_level` | 磁吸/集中价位 | 观察钉住或回归倾向 |
| `n1` / `n2` | 下方关键层级 | 通常作为支撑/压力候选，不作为硬止损 |
| `p1` / `p2` | 上方关键层级 | 通常作为阻力/压力候选 |

## 3. 整合路径

信号层可把这些价位用于状态栏和图表观察；审计页优先展示 flip、pin、wall、magnet。它们用于解释空间背景，不参与执行层下单授权。

## 4. 边界与陷阱

- 关键价位不是保证触达或反转点。
- GEX 外部价位与内部 GGR 的 `flip_point` 可能存在差异，需标注来源。
- 当 `sections.gamma_exposure` 陈旧或缺失时，前端必须保留“暂缺/陈旧”状态。
