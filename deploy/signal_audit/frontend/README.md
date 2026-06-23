# Signal Audit Frontend Fixture

> 当前模块口径（r3.2 / 2026-06-23）：本目录提交的是静态审计页面运行资产和本地 fixture。生产服务器上的 `signal_cards/` 由 materializer 从 FMZ JSONL 生成，仓库内样例不代表实时生产数据。

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
- LLM 复核意见是发布前必查板块；有 `llm_review` 时展示复核结果，缺 sidecar 时也必须显示 `PENDING_LLM` 占位说明。
- 默认交付 manifest 与 fallback 不发布 synthetic/local preview 卡；只有显式预览物化才允许带 synthetic。
- 完整证据账本只展示各 EDB 模块关键判断、方向倾向、权重和必要原始字段；完整 raw trace 留在下方原始截面。
- `source_ref` 应可回跳到对应原始截面分组；原始截面应有分组导航，避免在账本中堆叠大段 raw dump。
- 后续涉及前端内容变更，默认同时进行中文语义优化、重复内容去重、桌面/移动样式审查、数据字段完整性审查和渲染契约测试。
