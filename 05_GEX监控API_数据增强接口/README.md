# ⑤ GEX Monitor API · 数据增强接口

> 当前模块口径（r2.2 / 2026-06-19）：本目录是当前运行资产，服务版本 `gexmonitorapi=0.2.0`。当前权威接口是 Bearer 保护的 `/v1/info`，包含 GEX/Gamma/IV-RV/P-C/flow 与本地历史 rank。本文作为工程模块入口；因子语义先读 [`因子文档/00_GEX监控API总览.md`](因子文档/00_GEX监控API总览.md)。

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

一个用 **FastAPI + Scrapling** 实现的、单 Bearer Token 保护的 BTC 指标字典 API。
它用动态（浏览器）抓取 GEX Monitor 公开分析页 BTC 的四个 tab（`gex` / `gamma` / `volatility` / `flow`），
把可见内容结构化成一个总 `info` JSON，并在抓取失败时保留上一次成功缓存。

> 合规说明：仅抓取公开页面的可见内容；不抓登录页 / 后台 / `/_next/` 静态包 / 未公开接口，
> 也不二次包装 GEX Monitor 官方 `/api/` 数据接口。

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
- `rank`：每次全量刷新追加一行本地 JSONL 历史；不足 30 天时用已有样本，超过 30 天后只用最近 30 天计算当前分位，但保留全量历史。

> 每个字段的单位与真实语义见 [`docs/info接口语义文档.md`](docs/info接口语义文档.md)；完整响应示例见 [`docs/info.sample.json`](docs/info.sample.json)。

## 3. 本地开发（Windows）

需要 Python 3.12。仓库已带 `.venv`，也可自建：

```powershell
# 1. 创建并激活虚拟环境（如已存在 .venv 可跳过创建）
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 安装依赖（含浏览器抓取与开发依赖）
pip install -e ".[dev]"

# 3. 安装 Scrapling 的浏览器内核（动态抓取必需，首次较大）
scrapling install

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
`API_TOKEN`、`REFRESH_INTERVAL_SECONDS`、`REQUEST_TIMEOUT_SECONDS`、`CACHE_FILE`、
`HISTORY_FILE`、`RANK_LOOKBACK_DAYS`、`USER_AGENT`、`ENABLE_BACKGROUND_REFRESH`、`REFRESH_ON_STARTUP`。

## 5. 部署到 AWS 轻量服务器

完整步骤见 [deploy/README.md](deploy/README.md)（Ubuntu + venv + systemd，含浏览器系统依赖、
内存/swap 建议、防火墙端口与可选 Nginx 反代）。
