# ⑤ GEX Monitor API · 数据增强接口

> 当前模块口径（r2.2 / 2026-06-19）：本目录是当前运行资产，服务版本 `gexmonitorapi=0.2.1`。当前权威接口是 Bearer 保护的 `/v1/info`，包含 GEX/Gamma/IV-RV/P-C/flow 与本地历史 rank。本文作为工程模块入口；因子语义先读 [`因子文档/00_GEX监控API总览.md`](因子文档/00_GEX监控API总览.md)。

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
| `/api/gex-latest?asset=BTC&exchange=all&lite=true` | 中轴、现价、总 GEX、DVOL、四个墙位、磁吸位、vol trigger |
| `/api/volatility-metrics?asset=BTC` | PCR、Call/Put 成交量和 OI、IV rank/percentile、IV/RV、DVOL |
| `/api/options-chain?asset=BTC` | 期权链交叉校验与墙位备用重算（`OPTIONS_CHAIN_CROSSCHECK=true`） |
| `/api/price?asset=BTC` | 现价备用源 |

`/v1/info` 保持 `gex_board`、`gamma_exposure`、`volatility`、`flow` 四段结构。公开 JSON 无可靠权利金流时，
`call_premium`、`put_premium`、`abnormal_signal` 保持 `null`，不会用成交量伪造权利金；`call_put_bias` 明确标记为派生的成交量占比。

## 2. 接口

| Method | Path | 鉴权 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/health` | 否 | 服务存活与版本 |
| `GET` | `/v1/info` | Bearer | 返回四组指标字典 + rank 分位上下文 + 抓取时间 + 缓存状态 + 缺失字段 |
| `POST` | `/v1/refresh?section=all` | Bearer | 立即重抓。`section` ∈ `gex_board\|gamma_exposure\|volatility\|flow\|all` |

鉴权：除 `/health` 外所有端点都要求请求头 `Authorization: Bearer <API_TOKEN>`，否则返回 `401`。

`/v1/info` 返回形态（节选）：

```json
{
  "asset": "BTC",
  "fetched_at": "2026-06-03T09:00:00+00:00",
  "stale": false,
  "availability": "ready",
  "gex_board":      { "total_net_gex": -62730587.7, "dvol": 43.1, "market_state": "negative_gamma" },
  "gamma_exposure": { "n2": null, "n1": null, "flip_point": 67388.83, "spot_price": 66950.91, "...": null },
  "volatility":     { "iv_rv_ratio": null, "pcr": null, "term_structure": [] },
  "flow":           { "call_premium": null, "put_premium": null, "call_put_bias": null, "...": null },
  "missing_fields": ["gamma_exposure.n2", "..."],
  "field_status":   { "gamma_exposure.n2": { "status": "missing", "reason": "not_found_in_rendered_page" } },
  "rank": {
    "window": { "mode": "rolling_30d_or_available", "lookback_days": 30, "sample_count": 96 },
    "metrics": {
      "gex_board.total_net_gex": { "value": -62730587.7, "percentile": 0.22, "rank_pct": 22.0, "abs_percentile": 0.81 },
      "volatility.iv_rv_ratio": { "value": 0.83, "percentile": 0.18, "rank_pct": 18.0 },
      "flow.call_share_pct": { "value": 38.0, "percentile": 0.44, "rank_pct": 44.0 }
    }
  }
}
```

- `availability`：`ready`（全部命中）/ `partial`（部分缺失或有错误）/ `missing`（从未成功）。
- `stale`：上一次刷新是否有失败；失败不会清空旧缓存。
- 抓不到的字段不会让请求失败，而是记入 `missing_fields` 与 `field_status`。
- `rank`：每次全量刷新追加一行本地 JSONL 历史；超过 30 天后只用最近 30 天计算当前分位，但保留全量历史；`quality` 在窗口覆盖满 15 天时直接进入 `ok`，15 天以内为 `warming_up`。

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
