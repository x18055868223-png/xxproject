# ⑤ GEX Monitor API · 数据增强接口 —— 总览

> 当前模块口径（r2.2 / 2026-06-19）：当前服务版本 `gexmonitorapi=0.2.0`，权威接口为 `/v1/info`。本模块是信号层的只读外部上下文增强，不决定方向、不进入交易授权、不解锁执行层。

## 0. 模块定位

GEX Monitor API 位于信号层外侧，为 FMZ 信号层和审计页面提供结构化的期权/GEX 截面：

- 当前 Gamma/GEX 体制背景。
- flip、pin、call wall、put wall、magnet 等关键价位。
- IV/RV、PCR、term structure 等权利金与波动率信息。
- Call/Put premium 与资金流向。
- 最近 30 天或可用历史内的 rank 分位。

它解决的是“只看单次数值难以判断相对位置”的问题，不替代 EDB、GGR、SRD 或执行层判断。

## 1. 因子/字段清单

| # | 区块 | 当前接口字段 | 一句话 |
| --- | --- | --- | --- |
| 01 | GEX 总览 | `gex_board` | netGEX、DVOL、market_state，描述当前 Gamma 背景 |
| 02 | 关键价位 | `gamma_exposure` | flip、pin、wall、magnet，给价格空间约束参考 |
| 03 | 权利金与波动率 | `volatility` | IV/RV、PCR、期限结构，判断权利金是否偏贵或偏便宜 |
| 04 | 期权资金流 | `flow` | call/put premium、call share、flow P/C，观察近期期权买卖倾向 |
| 05 | 历史分位 | `rank` | 把 netGEX、DVOL、IV/RV、PCR、flow 等转成当前样本窗口内的相对位置 |
| meta | 抓取审计 | `sections`、`missing_fields`、`field_status` | 说明数据是否新鲜、缺失和可用 |

## 2. 当前实现

| 文件 | 职责 |
| --- | --- |
| `src/gexmonitorapi/app.py` | FastAPI 入口、鉴权、`/health`、`/v1/info`、`/v1/refresh` |
| `src/gexmonitorapi/scraper.py` | 动态抓取公开页面可见文本 |
| `src/gexmonitorapi/parsers.py` | 把页面文本解析为结构化字段 |
| `src/gexmonitorapi/cache.py` | 缓存、历史样本、rank 计算 |
| `src/gexmonitorapi/models.py` | 响应模型 |
| `deploy/` | systemd、Nginx、环境变量模板和部署说明 |

## 3. 与信号层/审计页的关系

- FMZ 信号层读取 `/v1/info` 后，把结果写入 `factor_cross_section.gex_info`。
- GEX 中的 Gamma 价位可与内部 GGR 结果交叉验证，但不覆盖内部 GGR。
- rank 会在信号层状态栏和审计页的 GEX Rank 区展示。
- 数据质量字段进入审计卡的 `quality.sources` 或展示层摘要，用于解释“暂缺”“降级”“冷启动”。

## 4. 边界与陷阱

- `rank_pct` 是历史窗口相对位置，不是胜率。
- `abs_rank_pct` 只适合 netGEX 这类正负方向和绝对强度都重要的字段。
- `quality=warming_up` 表示样本未满或冷启动，不代表数值错误。
- GEX Monitor 公开页面可能改版；字段缺失应通过 `missing_fields` 呈现，不应静默编造。
- 真实 token 只放 `/etc/gexmonitorapi.env` 或本地 `.env`，不得提交。

## 5. 当前目标 / 待办

1. 保持 `/v1/info` 合同稳定。
2. 继续积累 rank 历史样本，满 30 天后仍保留全量历史，但当前分位只用最近 30 天。
3. 前端展示保持中文语义优化，并区分方向分位与绝对强度分位。
4. 部署更新仍走 Git + systemd，避免手工复制漂移。
