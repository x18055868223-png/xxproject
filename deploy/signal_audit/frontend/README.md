# Signal Audit Frontend Fixture

> 当前模块口径（r2.2 / 2026-06-19）：本目录提交的是静态审计页面运行资产和本地 fixture。生产服务器上的 `signal_cards/` 由 materializer 从 FMZ JSONL 生成，仓库内样例不代表实时生产数据。

## 文件

| 文件 | 用途 |
| --- | --- |
| `index.html` | 静态页面入口 |
| `app.js` | 审计卡渲染逻辑 |
| `VERSION.json` | 前端样例资产版本元数据 |
| `signal_cards/index.json` | fixture manifest |
| `signal_cards/*.json` | fixture card |
| `signal_cards/fallback.js` | fallback fixture |

## 边界

- 前端只展示审计数据，不生成信号。
- 前端中文 label 不覆盖 JSON 原始字段。
- 有 `llm_review` 时展示复核区块；没有时保持旧卡兼容。
- 后续涉及前端内容变更，默认同时考虑中文语义优化和重复内容去重。
