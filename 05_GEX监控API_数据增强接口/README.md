# ⑤ GEX Monitor API · 数据增强接口

> 当前模块口径（r2.2 / 2026-06-19）：本目录是当前运行资产，服务版本 `gexmonitorapi=0.2.1`。当前权威接口是 Bearer 保护的 `/v1/info`，包含 GEX/Gamma/分桶墙位/IV/P-C/大单流/到期 与本地历史 rank（2026-10 起数据源为 gex-latest + dashboard-bundle）。本文作为工程模块入口；因子语义先读 [`因子文档/00_GEX监控API总览.md`](因子文档/00_GEX监控API总览.md)。

## 0. 工程收纳

| 路径 | 用途 |
| --- | --- |
| `因子文档/` | 按 00-04 模块惯例整理的中文语义入口，解释 `/v1/info` 如何进入信号层和审计卡 |
| `docs/` | 接口字段语义、样例响应、测试记录 |
| `deploy/` | 服务器部署、systemd、Nginx 与环境变量模板 |
| `src/gexmonitorapi/` | FastAPI 服务源码 |
| `tests/` | API、cache、parser 合同测试 |
| `readme.txt` | 历史需求草稿，非当前接口口径 |

本轮 r2.2 不移动源码、测试或部署文件，只补齐工程索引和中文因子文档，避免破坏已部署服务路径。

## 1. 服务简介

一个用 **FastAPI** 实现的、单 Bearer Token 保护的 BTC 指标字典 API。
默认使用 GEX Monitor 的公开 JSON 接口，不依赖登录态、Cookie、storage state、Scrapling 或 Playwright 页面渲染。
页面抓取实现仍保留为显式回滚路径（`GEXMONITOR_SOURCE_MODE=page`），但不是默认运行路径。

> 合规说明：仅以低频、带正常 User-Agent/Referer 的方式读取公开 JSON 接口；不抓登录页、后台、Cookie、
> storage state 或未公开接口。服务只在 `/v1/info` 兼容边界内整理公开数据，并保留来源和派生标记。

### 1.1 公开 JSON 数据源

| 源接口 | 用途 |
| --- | --- |
| `/api/gex-latest?asset=BTC&exchange=all&lite=true` | 总 GEX、flip、现价、DVOL、四个墙位、磁吸位、vol trigger、0DTE/周度分桶、对冲流情景 |
| `/api/dashboard-bundle?asset=BTC` | PCR（成交量/持仓）、ATM IV、IV 分位、期限结构、25Δ RR、大单流 1h/24h、最大痛点、最近到期摘要；现价/DVOL 备用 |
| `/api/options-chain?asset=BTC` | 期权链墙位交叉校验（`OPTIONS_CHAIN_CROSSCHECK=true`，失败不置 stale） |

2026-10 上游改版后不再调用 `/api/volatility-metrics`（已变为大体积时间序列）与 `/api/price`。
`/v1/info` 为六段结构：`gex_board`、`gamma_exposure`、`gamma_profiles`、`volatility`、`flow`、`expiry`。
`iv_rv_ratio`、`call_premium`、`put_premium`、`call_put_bias`、`abnormal_signal` 及 rank 指标 `flow.call_share_pct` 已移除。

## 2. 接口

| Method | Path | 鉴权 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/health` | 否 | 服务存活与版本 |
| `GET` | `/v1/info` | Bearer | 返回六组指标字典 + rank 分位上下文 + 抓取时间 + 缓存状态 + 缺失字段 |
| `POST` | `/v1/refresh?section=all` | Bearer | 立即重抓。`section` ∈ `gex_board\|gamma_exposure\|gamma_profiles\|volatility\|flow\|expiry\|all` |

鉴权：除 `/health` 外所有端点都要求请求头 `Authorization: Bearer <API_TOKEN>`，否则返回 `401`。

`/v1/info` 返回形态（节选）：

```json
{
  "asset": "BTC",
  "fetched_at": "2026-10-11T08:53:10+00:00",
  "stale": false,
  "availability": "ready",
  "gex_board":      { "total_net_gex": 158146534.89, "dvol": 36.89, "market_state": "positive_gamma" },
  "gamma_exposure": { "flip_point": 82725.44, "spot_price": 82997.28, "p1": 90000.0, "n1": 78000.0, "hedging_flows": { "...": "..." }, "...": null },
  "gamma_profiles": { "zero_dte": { "p1": 84000.0, "walls": { "...": "..." } }, "weekly": { "...": "..." } },
  "volatility":     { "pcr": 0.69, "pcr_oi": 0.696, "atm_iv": 26.88, "iv_percentile": 16.0, "term_structure": [], "rr_skew": [] },
  "flow":           { "put_call_ratio": 0.69, "call_volume": 69903.4, "whale_24h": { "net_flow_usd": 355603.68, "...": 0 }, "...": null },
  "expiry":         { "max_pain_price": 78000.0, "max_pain_target_expiry": "30OCT26", "next_expiry": { "expiry": "12OCT26", "...": null } },
  "missing_fields": [],
  "field_status":   {},
  "rank": {
    "window": { "mode": "rolling_30d_or_available", "lookback_days": 30, "sample_count": 96 },
    "metrics": {
      "gex_board.total_net_gex": { "value": 158146534.89, "percentile": 0.62, "rank_pct": 62.0, "abs_percentile": 0.81 },
      "volatility.pcr": { "value": 0.69, "percentile": 0.18, "rank_pct": 18.0 }
    }
  }
}
```

- `availability`：`ready`（必需字段齐全）/ `partial`（必需字段缺失，或 `gex`/`bundle` 请求出错）/ `missing`（从未成功）。
- `stale`：上一次刷新中 `gex` 或 `bundle` 失败；失败不会清空旧缓存，只保留失败来源对应的段。
- 抓不到的字段不会让请求失败，而是记入 `missing_fields` 与 `field_status`；可选字段（如 `gamma_profiles.zero_dte`）缺失只标 `optional_missing`。
- `rank`：每次全量刷新追加一行本地 JSONL 历史（含 `metric_sources`），只与同一上游字段的历史样本比较；超过 30 天后只用最近 30 天，`quality` 窗口满 15 天进入 `ok`。

> 每个字段的单位与真实语义见 [`docs/info接口语义文档.md`](docs/info接口语义文档.md)；完整响应示例见 [`docs/info.sample.json`](docs/info.sample.json)。

## 3. 本地开发（Windows）

需要 Python 3.12。仓库已带 `.venv`，也可自建：

```powershell
# 1. 创建并激活虚拟环境（如已存在 .venv 可跳过创建）
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 安装依赖（含浏览器抓取与开发依赖）
pip install -e ".[dev]"

# 3. 默认 JSON 模式无需浏览器；只有回滚到 SOURCE_MODE=page 时才需要 Scrapling/Playwright

# 4. 配置 token
copy .env.example .env   # 然后编辑 .env 改掉 API_TOKEN

# 5. 跑测试
pytest -q

# 6. 启动服务
python -m gexmonitorapi
# 或： uvicorn gexmonitorapi.app:app --host 0.0.0.0 --port 8000
```

冒烟测试（PowerShell）：

```powershell
curl http://127.0.0.1:8000/health
curl -H "Authorization: Bearer <你的TOKEN>" http://127.0.0.1:8000/v1/info
curl -X POST -H "Authorization: Bearer <你的TOKEN>" "http://127.0.0.1:8000/v1/refresh?section=gex_board"
```

## 4. 配置项

全部可经环境变量或 `.env` 覆盖，见 [.env.example](.env.example)：
`API_TOKEN`、`GEXMONITOR_SOURCE_MODE`、`GEXMONITOR_OPTIONS_CHAIN_CROSSCHECK`、
`REFRESH_INTERVAL_SECONDS`、`REQUEST_TIMEOUT_SECONDS`、`CACHE_FILE`、`HISTORY_FILE`、
`RANK_LOOKBACK_DAYS`、`USER_AGENT`、`ENABLE_BACKGROUND_REFRESH`、`REFRESH_ON_STARTUP`。

`GEXMONITOR_SOURCE_MODE=public_json` 为默认值；如需临时回滚旧页面抓取，设置为 `page` 并重启服务。
JSON 模式响应还包含 `source_mode`、`source_urls`、`source_metadata.cross_check`、`field_status`、`observed_at`、
`data_age_ms`、`stale` 与 `availability`，供前端和 LLM 区分原生值、派生值和交叉校验状态。

## 5. 部署到 AWS 轻量服务器

完整步骤见 [deploy/README.md](deploy/README.md)（Ubuntu + venv + systemd，含浏览器系统依赖、
内存/swap 建议、防火墙端口与可选 Nginx 反代）。
