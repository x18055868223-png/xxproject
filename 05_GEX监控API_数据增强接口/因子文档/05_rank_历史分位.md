# 05 · rank（历史分位）

> 模块：⑤ GEX Monitor API · 数据增强接口
> canonical：`src/gexmonitorapi/cache.py` + `/v1/info.rank`
> 最后核对：2026-06-19（r2.2 文档收纳）

## 0. 轻量因子卡

| 字段 | 内容 |
|---|---|
| 因子 | rank（历史分位） |
| 所属回路 | ⑤ GEX 数据增强接口 |
| 作用层 | 风险门 / 审计 |
| 理论机制 | 把当前外部 GEX、DVOL、IV/RV、PCR、Call share、Flow P/C 转成滚动窗口内的相对位置，避免只看绝对值。 |
| 预期符号 | RELATIVE_CONTEXT_ONLY |
| 适用周期 | GEX API 刷新轮 / rank cache 更新轮。 |
| 与现有因子重叠 | 与数据质量、LLM caution、GEX/Gamma 展示重叠，但 rank_pct 不等于胜率或置信度。 |
| 主要失效条件 | 样本冷启动、历史缓存迁移、窗口过短或外部字段口径变化。 |
| 改变的决策 | 改变审计页分位解释和人工复核强度，不改写 EDB 权重、blocking 或交易许可。 |
| 当前状态 | ACTIVE |

## 1. 一句话定位

`rank` 把当前截面中的 netGEX、DVOL、IV/RV、PCR、Call share、Flow P/C 转成当前样本窗口内的相对位置，解决“只看数值不知高低”的问题。

## 2. 当前窗口

| 字段 | 语义 |
| --- | --- |
| `window.mode` | 当前为 `rolling_30d_or_available` |
| `lookback_days` | 当前分位计算窗口，默认 30 天 |
| `sample_count` | 当前参与 rank 的样本数量 |
| `history_retained_count` | 本地保留的总历史样本数量 |
| `window_days` | 当前窗口覆盖天数 |

不满 30 天时，用已有样本计算；超过 30 天后，保留全量历史，但当前 rank 只用最近 30 天。

## 3. 当前指标

| rank key | 解释 |
| --- | --- |
| `gex_board.total_net_gex` | netGEX 方向分位，同时提供 `abs_rank_pct` 表示绝对强度分位 |
| `gex_board.dvol` | DVOL 分位 |
| `volatility.iv_rv_ratio` | IV/RV 分位 |
| `volatility.pcr` | PCR 分位 |
| `flow.call_share_pct` | Call share 分位 |
| `flow.put_call_ratio` | Flow P/C 分位 |

## 4. 整合路径

rank 会进入信号层状态栏、审计卡 `factor_cross_section.gex_info.rank` 和前端 GEX Rank 区。它是解释层，不改变 EDB 权重、blocking 或交易许可。

## 5. 边界与陷阱

- `rank_pct` 不是胜率。
- `quality=warming_up` 应原样展示，避免冷启动样本被误读为稳定统计。
- 对 netGEX 需要同时看方向分位和绝对值分位。
- rank 样本来自服务本地历史，服务迁移或清缓存会影响冷启动状态。
