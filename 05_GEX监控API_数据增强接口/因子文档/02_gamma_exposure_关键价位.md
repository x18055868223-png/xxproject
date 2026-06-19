# 02 · gamma_exposure（关键价位）

> 模块：⑤ GEX Monitor API · 数据增强接口
> canonical：`src/gexmonitorapi/parsers.py` + `/v1/info.gamma_exposure`
> 最后核对：2026-06-19（r2.2 文档收纳）

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
