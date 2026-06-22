# 02 · static_frontend（静态审计页面）

> 模块：审计静态页面部署模块
> canonical：`deploy/signal_audit/frontend/index.html` + `app.js`
> 最后核对：2026-06-19（r2.2 文档收纳）

## 0. 轻量因子卡

| 字段 | 内容 |
|---|---|
| 因子 | static_frontend（静态审计页面） |
| 所属回路 | 审计部署链路 |
| 作用层 | 审计 |
| 理论机制 | 将结构化信号审计卡、GEX rank、时区前提耐久度和 LLM 复核意见渲染成人工可读页面。 |
| 预期符号 | HUMAN_REVIEW_SURFACE |
| 适用周期 | 浏览器审计 / 服务器静态页面刷新后。 |
| 与现有因子重叠 | 与 materializer 输出、signal_review_card schema、LLM sidecar 重叠，但只做展示。 |
| 主要失效条件 | manifest 断链、旧样例卡未删除、字段缺失未降级、浏览器 file/http 模式差异。 |
| 改变的决策 | 改变人工审查效率和风险可见性，不改变系统信号与交易授权。 |
| 当前状态 | ACTIVE |

## 1. 一句话定位

静态前端负责把单张信号审计 JSON 以中文审计卡形式展示，方便人工复核信号、数据质量、GEX rank 和 LLM 旁路意见。

## 2. 当前读取合同

| 文件 | 用途 |
| --- | --- |
| `signal_cards/index.json` | 列表、过滤、排序入口 |
| `signal_cards/*.json` | 单卡详细内容 |
| `signal_cards/fallback.js` | 直接文件打开或索引失败时的备用样例 |
| `VERSION.json` | committed fixture / 前端版本元数据 |

## 3. 展示原则

- 中文语义优先，原始 enum 和数值保留。
- GEX/Gamma、rank、quality、blocking、reasoning、LLM 复核分区展示。
- LLM 已解释过的同质证据，上方区块应减少重复。
- `confidence` 不是胜率，`rank_pct` 不是胜率。

## 4. 边界与陷阱

- 前端不读取生产 JSONL，只读取 materializer 产物。
- committed cards 是 fixture，不代表服务器实时数据。
- 没有 `llm_review` 时页面必须继续兼容旧卡。
