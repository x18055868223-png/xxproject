# 历史计划稿：GEX Monitor BTC 分析页摘要 API 计划

> 当前口径提示（r2.2 / 2026-06-19）：本文是早期需求草稿，保留用于追溯，不再作为当前接口、部署或信号层接入口径。当前权威接口是 `GET /v1/info`、`POST /v1/refresh?section=...`，当前语义入口见 `README.md` 与 `因子文档/00_GEX监控API总览.md`。本文中 `/v1/gexmonitor/...` 等旧端点不要再用于部署或开发。

## Summary
在空仓库 `C:\Users\Xu\Documents\gexmonitorapi` 新建一个 Python/FastAPI 服务，用 Scrapling 抓取并结构化 GEX Monitor 公开分析页面的可见内容。第一版只覆盖 BTC 的四个分析子页：`gex`、`gamma`、`volatility`、`flow`。服务本地 Windows 调试通过后，部署到 AWS 轻量 Linux 服务器，使用单个 Bearer token 保护摘要和刷新端点。

合规默认：只抓公开页面可见内容，不抓登录页、后台页、`/_next/` 静态包、未公开接口，也不包装 GEX Monitor `/api/` 数据接口。参考来源：[Scrapling docs](https://scrapling.readthedocs.io/en/latest/)、[Scrapling GitHub](https://github.com/D4Vinci/Scrapling)、[GEX API docs](https://gexmonitor.com/api-docs)、[GEX terms](https://gexmonitor.com/terms)、[robots.txt](https://gexmonitor.com/robots.txt)、[sitemap](https://gexmonitor.com/sitemap.xml)。

## Key Changes
- 技术栈：Python 3.12、FastAPI、Uvicorn、Scrapling `fetchers` extra、Pydantic settings、pytest。
- 页面范围：
  - `https://gexmonitor.com/btc/options/analytics?tab=gex`
  - `https://gexmonitor.com/btc/options/analytics?tab=gamma`
  - `https://gexmonitor.com/btc/options/analytics?tab=volatility`
  - `https://gexmonitor.com/btc/options/analytics?tab=flow`
- 抓取策略：优先用 Scrapling 动态/浏览器 fetcher 等待页面渲染，提取主内容区可见文本；如果渲染失败，返回上一次成功缓存并标记 `stale=true`、`availability=partial`。
- 缓存策略：后台每 10 分钟刷新四个 tab；保留每个 tab 的最近成功摘要、抓取时间、来源 URL、错误信息和页面文本哈希。
- 鉴权：除 `/health` 外，所有业务端点要求 `Authorization: Bearer <API_TOKEN>`；本地和服务器都从 `.env` 或 systemd 环境变量读取。

## API Interfaces
- `GET /health`
  - 无需 token。
  - 返回服务启动状态、版本、当前时间。
- `GET /v1/gexmonitor/btc/analytics`
  - 需要 token。
  - Query：`tab=gex|gamma|volatility|flow`，默认 `gex`。
  - 返回：`asset`、`tab`、`source_url`、`fetched_at`、`stale`、`availability`、`title`、`sections`、`raw_excerpt`、`content_hash`。
- `GET /v1/gexmonitor/btc/analytics/all`
  - 需要 token。
  - 返回四个 tab 的缓存摘要和统一 freshness 元数据。
- `POST /v1/gexmonitor/btc/refresh`
  - 需要 token。
  - Query：`tab=gex|gamma|volatility|flow|all`，默认 `all`。
  - 立即触发重抓，返回刷新结果；失败时不清空旧缓存。
- `GET /v1/gexmonitor/cache/status`
  - 需要 token。
  - 返回每个 tab 的最近成功时间、最近失败时间、错误摘要、缓存年龄。

## Implementation Plan
- 初始化项目：创建 `pyproject.toml`、`.env.example`、`README.md`、`src/gexmonitorapi/`、`tests/`，本地 Windows 先安装或定位 Python 3.12。
- 实现配置层：`API_TOKEN`、抓取间隔 600 秒、请求超时、User-Agent、目标 URL manifest。
- 实现抓取层：Scrapling fetcher 负责渲染页面、等待 loading 消失、提取主内容文本；解析器把文本归并为标题、导航状态、关键区块和短摘要。
- 实现缓存层：进程内缓存加 JSON 文件持久化，服务重启后先加载最近成功缓存。
- 实现 FastAPI 路由和 Bearer token 依赖；后台启动定时刷新任务。
- 本地验证通过后，在 Linux 服务器上用 `venv + systemd` 部署，Uvicorn 监听本机端口；公网可直接暴露端口或用 Nginx 反代，安全边界以 Bearer token 为主。

## Test Plan
- 单元测试：token 缺失/错误返回 401；正确 token 可访问业务端点。
- 解析测试：用本地 fixture 验证四个 tab 能输出稳定 JSON envelope，空页面或 loading 页面会降级。
- 缓存测试：抓取失败时保留旧缓存并返回 `stale=true`。
- 集成测试：本地启动服务后用 `curl` 验证 `/health`、单 tab、all、manual refresh。
- 部署验收：Linux 上 `systemctl status gexmonitorapi` 正常；服务器公网 URL 带 Bearer token 能返回 BTC 四个分析页摘要；无 token 请求被拒绝。

## Assumptions
- 第一版只做 BTC，不扩展 ETH/SOL。
- 第一版只做结构化摘要，不返回完整 HTML，也不把 GEX Monitor 官方 `/api/` 数据接口二次包装。
- 刷新间隔固定 10 分钟，后续可通过环境变量调整。
- API token 使用单静态 Bearer token，不做用户系统、多 key 管理或 JWT。
- 当前 Windows PATH 没有 Python、pip、AWS CLI、Docker；实施时先解决本地 Python 运行环境。

# GEX Monitor BTC 指标字典 API v1 计划

## Summary
第一版做 BTC 分析板块的“重点指标抓取 + 结构化 JSON 中转”，不做图表解析、不抓登录/后台/非公开接口。当前静态 HTML 主要是 `加载中...`，指标文本在前端渲染后才出现，所以实现默认使用 Scrapling 动态抓取能力等待页面渲染，再从可见 DOM 中提取指标。

参考来源：[GEX Monitor](https://gexmonitor.com/btc/options/analytics?tab=gex)、[sitemap](https://gexmonitor.com/sitemap.xml)、[robots.txt](https://gexmonitor.com/robots.txt)、[Scrapling docs](https://scrapling.readthedocs.io/en/latest/)。

## 需求列表
- 抓取范围：BTC 分析页四个 tab：
  - `gex`：GEX 看板
  - `gamma`：Gamma Exposure
  - `volatility`：波动率
  - `flow`：资金流向
- 指标字典：
  - `gex_board`：`total_net_gex`、`dvol`、`market_state`
  - `gamma_exposure`：`n2`、`n1`、`flip_point`、`volatility_trigger`、`spot_price`、`magnet_price`、`p1`、`p2`
  - `volatility`：`iv_rv_ratio`、`pcr`、`term_structure`
  - `flow`：`call_premium`、`put_premium`、`call_put_bias`、`put_call_ratio`、`abnormal_signal`
- 每个字段都带采集状态：成功返回数值/文本；失败返回 `null`，并在 `missing_fields` 和 `field_status` 中说明未成功获取。
- API 只交付一个总 info 接口，内部按不同字典分组；保留手动刷新接口用于调试和恢复。

## API 交付
- `GET /health`
  - 无需 token，返回服务状态。
- `GET /v1/info`
  - 需要 `Authorization: Bearer <API_TOKEN>`。
  - 返回 BTC 四组字典、抓取时间、缓存状态、缺失字段列表。
- `POST /v1/refresh`
  - 需要 token。
  - Query：`section=gex_board|gamma_exposure|volatility|flow|all`，默认 `all`。
  - 触发重抓；失败时保留上一次成功缓存，并反馈本次失败字段。

返回形态示例：
```json
{
  "asset": "BTC",
  "fetched_at": "2026-06-03T09:00:00Z",
  "stale": false,
  "availability": "ready",
  "gex_board": {
    "total_net_gex": -62730587.7,
    "dvol": 43.1,
    "market_state": "negative_gamma"
  },
  "gamma_exposure": {
    "n2": null,
    "n1": null,
    "flip_point": 67388.83,
    "volatility_trigger": null,
    "spot_price": 66950.91,
    "magnet_price": null,
    "p1": null,
    "p2": null
  },
  "volatility": {
    "iv_rv_ratio": null,
    "pcr": null,
    "term_structure": []
  },
  "flow": {
    "call_premium": null,
    "put_premium": null,
    "call_put_bias": null,
    "put_call_ratio": null,
    "abnormal_signal": null
  },
  "missing_fields": [
    "gamma_exposure.n2",
    "gamma_exposure.n1",
    "flow.abnormal_signal"
  ],
  "field_status": {
    "gamma_exposure.n2": {
      "status": "missing",
      "reason": "not_found_in_rendered_page"
    }
  }
}
```

## 实施计划
- 新建 Python/FastAPI 项目，配置 `.env.example`：`API_TOKEN`、刷新间隔、超时、目标 URL。
- 用 Scrapling 动态抓取四个 BTC tab，等待 loading 消失，提取页面主内容区可见文本。
- 为每个 section 写独立解析器：先用明确标签/近邻文本匹配，再用数字格式归一化；不解析 canvas/svg 图表。
- 加缓存层：10 分钟自动刷新；失败时保留最近成功结果并标记 `stale=true`、`availability=partial`。
- 加缺失字段审计：每次抓取产出 `missing_fields`、`field_status`、`last_error`，让 API 自动反馈哪些数据没拿到。
- 本地 Windows 调试通过后，用 Linux `venv + systemd` 部署到 AWS 轻量服务器；Uvicorn 可直接开放端口或经 Nginx 反代，鉴权靠 Bearer token。

## 测试与验收
- 本地测试：`/health` 正常；无 token 访问 `/v1/info` 返回 401；正确 token 返回结构化 JSON。
- 抓取测试：四个 section 都能运行；图表解析缺失不算失败，但必须列入 `missing_fields`。
- 缓存测试：模拟目标页失败时，接口仍返回旧缓存并标记 `stale=true`。
- 交付验收：服务器公网请求 `/v1/info` 能返回一个总 info JSON，包含四个指标字典和缺失字段反馈。
