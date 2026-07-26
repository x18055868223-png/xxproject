# 05 · rank（历史分位）

> 模块：⑤ GEX Monitor API · 数据增强接口
> canonical：`src/gexmonitorapi/cache.py` + `/v1/info.rank`
> 最后核对：2026-06-19（r2.2 文档收纳）

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

不满 30 天时，用已有样本计算；超过 30 天后，保留全量历史，但当前 rank 只用最近 30 天。`quality` 不等同于 30 天完整窗口：窗口覆盖 `<15` 天为 `warming_up`，`>=15` 天直接为 `ok`。

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
- `quality=warming_up` 应原样展示，表示窗口覆盖未满 15 天；`window_days>=15` 时应展示为 `ok`，不得继续按 30 天冷启动解释。
- 对 netGEX 需要同时看方向分位和绝对值分位。
- rank 样本来自服务本地历史，服务迁移或清缓存会影响冷启动状态。
