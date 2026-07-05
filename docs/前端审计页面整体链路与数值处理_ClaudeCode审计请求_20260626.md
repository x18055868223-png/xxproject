# 信号审计前端页面整体链路与数值处理审计请求（Claude Code）

生成时间：2026-06-26
目标读者：Claude Code / 外部代码审计模型
审计目标：复核当前前端审计页面的整体数据链路、页面渲染边界、数值单位处理、LLM sidecar 消费与降级展示是否存在细微缺口。

## 1. 审计任务

请围绕 `deploy/signal_audit/frontend/` 静态审计页面，以及它依赖的 materializer、LLM sidecar 和测试，做一次只读审计。重点不是重写页面，而是确认：

- 从 FMZ 信号审计 JSONL 到 `signal_cards/` 静态卡片，再到浏览器页面的链路是否一致。
- 页面主阅读区是否只展示人读中文语义，不泄漏 raw field path、raw enum、`source_ref` 机器追踪文本。
- Funding、Gamma/GEX、Macro、TMV、Skew、P/C、Conflict、Confidence、时间等数值是否存在单位误读、重复缩放、漏缩放、历史兼容字段误用。
- transition LLM v1.2.2 的展示、策略校验、降级隐藏和 legacy fallback 是否符合只读审计边界。
- 页面是否继续满足“重点清晰、逻辑贯通、关键内容全面”，同时保持信息密度克制。

请不要修改 FMZ producer、执行层、交易开关、部署脚本的运行行为，也不要 commit/push。若发现问题，先输出审计 findings 和建议补测点。

## 2. 明确边界

本轮审计范围：

- 前端静态页面资产：
  - `deploy/signal_audit/frontend/index.html`
  - `deploy/signal_audit/frontend/app.js`
  - `deploy/signal_audit/frontend/VERSION.json`
  - `deploy/signal_audit/frontend/README.md`
  - `deploy/signal_audit/frontend/signal_cards/index.json`
  - `deploy/signal_audit/frontend/signal_cards/*.json`
  - `deploy/signal_audit/frontend/signal_cards/fallback.js`
  - `deploy/signal_audit/frontend/signal_cards/trajectory/*.json`
- 物化链路：
  - `tools/materialize_signal_cards.py`
- LLM review / sidecar 链路：
  - `tools/gemini_signal_llm_review.py`
  - card 级 `llm_review`
  - transition 级 `transition_llm_review`
- 回归测试：
  - `tests/test_signal_audit_frontend_render_contract.py`
  - `tests/test_materializer_tail_window.py`
  - `tests/test_signal_llm_review_pipeline.py`
- 当前真实调用预览产物：
  - `.preview_signal_audit/real_llm_transition_v122_20260626_104207/`
  - 最新 transition sidecar：`.preview_signal_audit/real_llm_transition_v122_20260626_104207/signal_transition_llm_reviews_v122_rerun11.jsonl`

本轮禁止范围：

- 不改 FMZ producer。
- 不改执行层、下单、交易开关、风控开关。
- 不把本地预览产物当作生产已部署状态。
- 不提交、不推送、不部署。
- 不在文档或日志中记录 API key、token、密码等敏感信息。

## 3. 资产职责

### 3.1 前端入口

`deploy/signal_audit/frontend/index.html`

- 静态页面壳和 CSS。
- 提供左侧索引、筛选栏、右侧文档阅读区。
- `script#signal-data` 当前为空数组，主要用于内嵌样例的历史兼容。
- 当前加载：
  - `signal_cards/fallback.js?v=20260626-transition-v1.2.2`
  - `app.js?v=20260626-transition-v1.2.2`
- 页面本身不生成信号、不计算交易判断，只渲染已物化 JSON。

### 3.2 前端主逻辑

`deploy/signal_audit/frontend/app.js`

主要职责：

- 加载 manifest 和 card JSON。
- 构建左侧索引、搜索和筛选。
- 渲染单张信号审计卡。
- 格式化数值、时间、状态、中文语义。
- 展示 Gamma/GEX、session context、transition audit、LLM review、display layers、quality、blocking、完整证据账本、conflict、raw trace、provenance。
- 把 `source_ref` 映射到 raw trace 分组锚点。
- 对 transition LLM 文案做前端展示层兜底清洗。

主渲染顺序在 `renderDocument(doc)` 中：

1. load notice
2. header
3. metric strip
4. Gamma overview
5. GEX rank
6. Signal session context
7. Transition context
8. Card LLM review
9. Display layers
10. Quality
11. Blocking
12. Reasoning / evidence ledger
13. Conflict
14. Factor cross-section raw trace
15. Provenance

### 3.3 前端版本元数据

`deploy/signal_audit/frontend/VERSION.json`

当前声明：

- `frontend_profile`: `signal_audit_static_v1`
- `card_schema`: `signal_review_card@1.0.0`
- `manifest_schema`: `signal_cards_manifest@1.0.0`
- `llm_review_schema`: `signal_llm_review@1.3.0`
- `llm_prompt_version`: `gemini_signal_review_prompt@1.3.0`
- committed `signal_cards/` 是本地 preview fixture。
- 生产卡片由 materializer 从 FMZ `signal_review.jsonl` 和可选 LLM sidecar 生成。
- live materializer source 记录为 `/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl`。
- live output root 记录为 `/opt/signal-audit`。

### 3.4 前端 README 约束

`deploy/signal_audit/frontend/README.md`

关键约束：

- 前端只展示审计数据，不生成信号。
- 中文 label 不覆盖原始 JSON 字段。
- LLM 复核是发布前必查板块；缺 sidecar 时也必须显示 `PENDING_LLM` 占位。
- 默认交付 manifest / fallback 不发布 synthetic / local preview 卡。
- 完整 raw trace 留在下方原始截面，不堆叠到主阅读区。
- `source_ref` 应可跳转到对应 raw trace 分组。

## 4. 数据工作流

### 4.1 源数据

FMZ producer 写出 `signal_review.jsonl`。producer 提供审计卡原始记录、系统决策、证据、质量、阻断、原始因子截面、producer anchor 等信息。

前端和 materializer 不应修改系统方向、置信、门控、交易许可，也不应生成新的交易判断。

### 4.2 materializer 输入

`tools/materialize_signal_cards.py` 的 `materialize(...)` 接收：

- `source`: FMZ `signal_review.jsonl`
- `output`: 静态页面输出目录
- `max_cards`: 最大卡片数
- `llm_reviews`: 可选 card 级 LLM sidecar
- `include_synthetic`: 是否包含 synthetic/local preview 卡，默认 false
- `transition_ledger`: 可选 transition ledger JSONL 输出路径
- `transition_state`: 可选 transition state 输出路径
- `transition_reviews`: 可选 transition LLM sidecar

读取逻辑：

- `_read_jsonl()` 读取 tail，JSON decode 失败或缺 identity 的行会计入 skipped。
- `_dedupe_by_card_id()` 按 `card_id` 去重。
- `_read_llm_reviews()` 读取 card 级 sidecar。
- `_read_transition_reviews()` 按 `transition_id` 读取 transition sidecar。
- 默认过滤 synthetic card，只有显式 `include_synthetic=true` 才包含。

### 4.3 materializer 输出

materializer 写出：

- `signal_cards/<card_id>.json`
- `signal_cards/index.json`
- `signal_cards/fallback.js`
- `signal_cards/trajectory/<symbol>.json`
- 可选 transition ledger/state 文件

card 级 LLM sidecar 合并规则：

- 如果 card 中已有 `llm_review.status == OK`，且 sidecar 不是 OK，不覆盖。
- 否则按 `card_id` 合并 sidecar 到 `record["llm_review"]`。

transition sidecar 合并规则：

- `_build_transition_records()` 按 symbol 串联前后卡。
- 生成 transition record 后，把 `transition_context` 写入当前卡。
- 如果 `review_map[transition_id]` 存在，则把 sidecar 原样写入当前卡 `transition_llm_review`。
- materializer 不补造 LLM 结论，只透传 sidecar。

### 4.4 transition context

每张当前卡的 `transition_context` 由前后相邻卡生成，核心字段包括：

- `audit_scope`: 固定 `AUDIT_ONLY`
- `transition_id`
- previous/current card id 和 timestamp
- `elapsed_ms`
- `comparison_quality`
- `producer_anchor`
- `compat_backfill_applied`
- `compat_backfill_source`
- `compat_source_fields`
- `producer_record_hashes`
- `relation`
- `decision_transition`
- `core_skeleton`
- `core_transition_display`
- `domain_change_summaries`
- `raw_change_groups`
- `top_material_changes`
- `recent_5_trajectory`
- `baseline_24h`
- `episode_anchor`
- `trajectory`
- `domain_states`
- `cross_domain_flags`
- `materiality_score`
- `llm_review_required`
- `hash_chain`
- `record_hash`

`llm_review_required` 当前由 `flags` 和 `materiality_score >= 25.0` 决定。

### 4.5 浏览器加载

`loadDocuments()` 行为：

- `file://` 模式：使用 `window.SIGNAL_CARD_FIXTURES || embedded`。
- HTTP 模式：fetch `signal_cards/index.json`，再按 manifest 的 `card.path` fetch 单卡 JSON。
- HTTP 加载失败时不回退到 fixture，而是设置 `load_error` 并返回空列表。
- 页面通过 `renderLoadNotice()` 显示加载失败说明。

请审计这个行为是否符合生产安全策略，还是可能造成静态站短暂空白时缺少可读兜底。

## 5. 前端主阅读区与 raw trace 分层

页面目标是“高信号解释在前，机器追踪在后”。

主阅读区应该展示：

- 系统状态和信号摘要。
- 关键数值的中文解释。
- transition LLM 的中文事实、影响、倾向性。
- 完整核心骨架：TMV、宏观、Funding、Skew、Gamma/GEX、P/C 等。
- 审计状态、降级状态、必要的模型/hash 元数据。

主阅读区不应出现：

- `factor_cross_section.*`
- `macro_pressure.components.*`
- `source_ref`
- `primary_fields`
- `主要字段`
- `来源`
- `核心前后值已入包`
- 字段清单式表达
- raw enum 堆叠
- `[object Object]`

raw trace 区应该保留：

- `factor_cross_section.*`
- `evidence_raw_values.*`
- JSON object / array 的 code 展示
- source trace 锚点
- field path table

`sourceRefLink(ref, doc)` 只在 raw trace target 存在时生成锚点链接，否则降级为 chip。

## 6. 数值与单位处理规则

### 6.1 通用显示函数

需要重点审计：

- `number(value, digits)`
- `percent(value)`
- `pctPoint(value)`
- `scalarText(value, options)`
- `valueHtml(value, options)`
- `valueHtmlByPath(path, value, options)`
- `dateText(value, style)`
- `ageText(ms)`

现行约定：

- `percent(value)` 把 fraction 乘 100 后显示 `%`。
- `pctPoint(value)` 不乘 100，直接按百分点显示 `%`。
- `number()` 只做数字格式化，不改变单位。
- 时间展示使用 `zh-CN`，transition timeline 的 `timeOnly()` 固定 `Asia/Shanghai`。
- `ageText()` 输入是毫秒，按 ms/秒/分钟/小时输出。

Claude 请重点查：同一字段是否在 materializer 和 frontend 各乘一次，或一个路径当 fraction、另一路径当 percentage point。

### 6.2 Funding

字段来源：

- `factor_cross_section.funding.last_rate`
- `factor_cross_section.funding.last_funding_rate`
- 兼容字段：`funding_state`、`funding_norm`、`effect`

规则：

- 主视觉和 transition core 优先使用 raw funding rate：`last_rate` / `last_funding_rate`。
- 不允许把 `funding_norm` 冒充为真实资金费率。
- 前端 `ratePctText(rate)` 显示 `rate * 100`，保留 4 位小数，例如 `0.0001` 显示为 `0.01%`。
- `fundingAssessment()` 阈值是 `0.0001 = 0.01%`。
- 正 funding rate 代表多头付费/拥挤倾向；前端会用 `signedLean(-rate)` 表示反身性辅助倾向。
- Funding 只作为审计辅助，不直接改变 EDB 方向票。

请重点查：

- 是否还有路径使用 `funding_norm` 作为费率。
- 是否出现 `0.012%` 被再次乘成 `1.2%` 的风险。
- 是否存在“资金费率从 0.012% 到 0.012%”但实际值来自小数和百分数字符串混用。

### 6.3 Gamma / GEX

字段来源：

- `factor_cross_section.gamma_regime`
- `factor_cross_section.gex_info`
- `net_gamma_notional_usd`
- `net_gamma_notional`
- `distance_to_flip_pct`
- `distance_to_pin_pct`
- `pin_price`
- `flip_point`

规则：

- transition core 优先使用 `net_gamma_notional_usd`，再 fallback 到 `distance_to_flip_pct`、`distance_to_pin_pct`、`regime`。
- materializer 对大额 `net_gamma_notional_usd` 使用 `$M/$B` 展示。
- 小量级历史兼容 Gamma 不应被显示为 `$0M` 或 `-$0M`。
- 如果无法确认是真实 USD notional，应保留兼容 metric 语义，避免伪装成美元名义额。
- `distance_to_flip_pct` / `distance_to_pin_pct` 是百分点，前端应走 `pctPoint()`，不乘 100。
- GGR / Gamma 是空间安全和门控约束，不应被展示成简单偏多/偏空方向票。

GEX rank：

- `rank_pct` / `abs_rank_pct` 是 0-100 百分位，不应再次乘 100。
- GEX 页面展示包含 `netGEX`、`DVOL`、`IV-RV`、`PCR`、`Call share`、`Flow P-C`。

请重点查：

- Gamma/GEX 字段是否有美元名义额和兼容 metric 混用。
- `distance_to_pin_pct` 是否被错误按 fraction 处理。
- rank percentile 是否被重复乘 100。
- 可选 GEX 缺失时是否显示“未提供/不可评估”，而不是输出对象或误导性零值。

### 6.4 P/C

字段来源：

- `factor_cross_section.gex_info.put_call_ratio`
- `pc_ratio`
- `pcr`
- `call_put_ratio`

规则：

- P/C 是非负比率。
- 不允许使用“正负符号翻转”叙事。
- 数据缺失时应显示缺失、不可评估或背景信息，不能反推方向。
- 主阅读区标签应显示 `P/C` 或中文“期权需求”，不要泄漏 `P_C_RATIO` raw enum。

请重点查：

- transition top changes 是否仍可能对 P/C 产生 `sign_flip` 语义。
- 前端 fallback 是否还会把 ratio 变化误解成带符号方向变化。

### 6.5 Macro

字段来源：

- `factor_cross_section.macro_pressure`
- `macro_score`
- `macro_regime`
- `macro_shock.state`
- `macro_shock.block`
- `macro_shock.volq_bps_delta`
- `macro_pressure.components.*`

规则：

- `macro_score` 是方向背景/风险资产逆风信息。
- `score > 0` 表示风险资产逆风。
- `macro_shock.block/state` 是冲击门或硬阻断状态，只能按 producer 原生字段展示。
- `volq_bps_delta` 和 components 的 `scoring_bps/change_bps/change_3d_bps` 以 `bp` 显示，不是 `%`。
- `change_pct_3d` 才应乘 100 显示百分比。
- 主阅读区不应列出 `macro_pressure.components.US10Y.scoring_bps` 等原始路径。
- 不允许引入外部新闻、外部行情或确定性因果。

请重点查：

- bps 是否被误显示为百分比。
- `macro_shock.block` 是否被展示成交易建议。
- 宏观解释是否只说实时观察、影响、倾向性，而不是“因为某新闻导致”。

### 6.6 TMV

字段来源：

- `factor_cross_section.tmvf`
- `tmv_blend`
- `tmvf_24h_final`
- `tmvf_48h_final`
- `window_conflict`
- `direction`

规则：

- TMV 是量价主干骨架。
- `tmv_blend`、24h、48h 多为原始小数，不应按百分比随意放大。
- `window_conflict` 是窗口冲突状态，不是交易信号。
- transition core 优先显示 `tmv_blend`，再 fallback 到 24h、48h、direction。

请重点查：

- TMV 在 transition LLM 说明中是否被完整覆盖。
- 是否有路径把 24h/48h 原始小数渲染成百分比。

### 6.7 Skew

字段来源：

- `factor_cross_section.skew`
- `rr_25d`
- `rr_blend`
- `skew_norm_blend`
- `vote`

规则：

- Skew 是期权偏斜/保护需求语义。
- 不应把 skew 投票值直接等同为交易方向。
- transition core fallback 优先 `rr_25d`、`rr_blend`、`skew_norm_blend`、`vote`。

请重点查：

- Skew 是否在综合论证中被覆盖。
- `rr_blend` 与 `vote` 是否有单位/语义混淆。

### 6.8 Conflict / Confidence / Quality

字段来源：

- `conflict.ratio`
- `conflict.level`
- `decision.confidence`
- `reasoning.confidence_decomposition.*`
- `quality.overall`
- `quality.sources.*`

规则：

- `conflict.ratio` 是 fraction，页面 metric strip 显示时乘 100。
- `absolute_share_pct` 已经是百分数，不应再乘 100。
- `decision.confidence` 是系统置信数值，不是胜率或概率承诺。
- quality source 是数据就绪/缺失状态，不应覆盖 raw factor 是否存在的事实。

请重点查：

- 是否存在 `quality.sources.gex_info` 显示 MISSING，但 `factor_cross_section.gex_info` 实际有数据的语义不一致。
- confidence 是否被误读为收益概率、胜率或交易强度。

### 6.9 时间与时区

字段来源：

- `identity.confirmed_at`
- `identity.confirmed_time_ms`
- `created_at`
- `observed_at`
- `age_ms`
- transition `previous_ts_ms/current_ts_ms/elapsed_ms`

规则：

- `dateText()` 用 `zh-CN`。
- transition timeline 的 `timeOnly()` 固定 `Asia/Shanghai`。
- `ageText()` 输入毫秒。
- materializer 的 elapsed 计算依赖 previous/current timestamp ms。

请重点查：

- 秒和毫秒是否混用。
- `observed_at` 与 `age_ms` 互推是否可能造成错误的新鲜度判断。
- 页面是否明确区分卡片确认时间与因子观察时间。

## 7. Transition LLM v1.2.2 消费边界

当前 transition LLM 配置：

- packet: `SignalTransitionReviewPacket@1.1.1`
- prompt: `gemini_signal_transition_review_prompt@1.2.2`
- review schema: `signal_transition_llm_review@1.2.2`
- 默认 blind mode: `single_call_evidence_first`
- 实验 blind mode: `two_call_strict`

本轮不启用两次调用真盲审为默认路径。

### 7.1 runner packet

`build_transition_review_packet()` 给模型的输入包含：

- `core_skeleton`
- `core_transition_display`
- `domain_change_summaries`
- `top_material_changes`
- `recent_5_trajectory`
- `baseline_24h`
- `episode_anchor`
- evidence catalog / hash
- transition id / metadata

不应把完整 audit card 直接交给模型重算系统结论。

### 7.2 prompt 边界

prompt 要求模型：

- 只解释程序已计算的 transition delta。
- 不重算字段、权重、置信、材料性、decision、blocking、trade_allowed。
- 不使用外部数据。
- 不给交易建议。
- 不做确定性因果断言。
- 不把 raw path 放入中文主解释。
- evidence 定位只允许进入 `evidence_refs`。

### 7.3 runner policy validation

`_transition_policy_validation()` 检查：

- raw enum 泄漏
- 交易词 / 交易建议
- 材料性套话
- 单位误标
- 无效 evidence refs
- 缺失 evidence refs
- 系统标签锚定
- 外部数据
- raw field path leak
- missing core domain coverage
- fact/impact 方向冲突

输出 `policy_validation`，关键字段包括：

- `passed`
- `severity`
- `issue_codes`
- `render_state`
- `raw_field_path_leak`
- `missing_core_domain_coverage`

render state：

- `DISPLAY_LLM_TEXT`: 可显示 LLM 正文。
- `DEGRADED_LLM_TEXT`: 可显示经前端清洗后的降级正文，但应提示策略问题。
- `SUPPRESS_LLM_TEXT`: 隐藏正文，只保留状态、策略校验、model、hash。
- 未知 render_state：前端 fail-closed。

### 7.4 前端消费

`renderTransitionLlmReview(doc)` 当前行为：

- 无 review 且 `llm_review_required=true`：显示 pending。
- legacy review 无 `policy_validation`：显示未按当前策略验证。
- `SUPPRESS_LLM_TEXT`：隐藏正文、observed changes、cross-factor、operator checks。
- 未知 render_state：fail-closed。
- 展示 LLM 正文前调用 `sanitizeTransitionReadable()`。

前端清洗：

- `hasTransitionRawFieldLeak()` 检测 `factor_cross_section`、`macro_pressure.components`、`source_ref`、`primary_fields`、`主要字段`、`来源`、`核心前后值已入包`、dotted path 等。
- `sanitizeTransitionReadable()` 对对象或污染文本使用 fallback。
- `stripTransitionMaterialityBoilerplate()` 去掉“关键变化/高材料性变化”等低信息标签套话。
- `transitionMetaChip()` 隐藏 `UNKNOWN`、`UNDETERMINED`、`INDETERMINATE` 等无信息 chip。
- `renderTransitionCoreSummary()` 不再显示分级 badge，只显示域名、前后值、中文含义。

请重点查：

- `DEGRADED_LLM_TEXT` 是否应该更醒目地提示用户。
- raw field path 清洗是否只在前端表现层生效，没有伪造 LLM 结论。
- legacy v1.0-v1.2.1 sidecar 是否能安全降级可读。

## 8. 当前真实预览与已知验证

当前本地真实调用预览目录：

```text
.preview_signal_audit/real_llm_transition_v122_20260626_104207/
```

当前最新 transition sidecar：

```text
.preview_signal_audit/real_llm_transition_v122_20260626_104207/signal_transition_llm_reviews_v122_rerun11.jsonl
```

已知结果：

- `policy_validation.passed=true`
- `severity=OK`
- `render_state=DISPLAY_LLM_TEXT`
- `issue_codes=[]`
- 已覆盖 MACRO、TMV、Funding、Skew、Gamma/GEX、P/C。
- 页面审计时主解释区未见 raw path、external data、causal overclaim、交易建议。
- `关键变化骨架 / Core transition` badge 数为 0。
- 桌面和移动宽度未见横向 overflow。

当前本地页面曾用以下地址审计：

```text
http://127.0.0.1:8789/index.html
```

该地址是本地预览服务，不代表生产部署已更新。

## 9. 建议 Claude 执行的验证命令

在仓库根目录运行：

```powershell
python tests/test_signal_audit_frontend_render_contract.py
python tests/test_materializer_tail_window.py
python tests/test_signal_llm_review_pipeline.py
python -m py_compile tools/materialize_signal_cards.py tools/gemini_signal_llm_review.py
node --check deploy/signal_audit/frontend/app.js
```

如果当前环境 `python` 不在 PATH，可尝试：

```powershell
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe tests/test_signal_audit_frontend_render_contract.py
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe tests/test_materializer_tail_window.py
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe tests/test_signal_llm_review_pipeline.py
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe -m py_compile tools/materialize_signal_cards.py tools/gemini_signal_llm_review.py
```

可选本地预览服务：

```powershell
python -m http.server 8789 --bind 127.0.0.1 --directory .preview_signal_audit/real_llm_transition_v122_20260626_104207
```

浏览器检查：

- 桌面宽度打开 `http://127.0.0.1:8789/index.html`
- 移动宽度打开同一页面
- 检查主阅读区无横向 overflow
- 检查主阅读区无 raw path / raw enum / `[object Object]`
- 检查 raw trace 分组锚点可跳转
- 检查 transition LLM panel 在正常、pending、legacy、degraded、suppressed 场景均可读

## 10. Claude 重点审计问题清单

请按以下问题逐项回答，发现问题时给出文件、函数、复现样本和建议测试。

1. `percent()`、`pctPoint()`、`ratePctText()` 是否存在重复乘 100 或漏乘 100？
2. Funding 主显示是否只使用 raw `last_rate/last_funding_rate`，没有把 `funding_norm` 当资金费率？
3. Gamma/GEX 是否正确区分 USD notional、兼容 metric、距离百分比点和 rank percentile？
4. P/C 是否完全避免“正负符号翻转”语义？
5. Macro 的 bps、percent、score、shock gate 是否单位清楚？
6. TMV、Funding、Skew、Gamma/GEX、P/C、Macro 是否都能进入 transition 综合论证或核心骨架？
7. Confidence 是否被展示为系统置信，而不是胜率、概率承诺或交易强度？
8. Conflict ratio 与 `absolute_share_pct` 是否单位不同但显示正确？
9. 时间字段是否存在秒/毫秒混用？
10. HTTP 加载失败不 fallback 到 fixture 是否符合生产预期？
11. `source_ref` 是否都能跳到 raw trace，缺 target 时是否降级合理？
12. 主阅读区是否仍可能出现 `factor_cross_section.*`、`macro_pressure.components.*`、`source_ref`、字段清单、`[object Object]`？
13. legacy sidecar 是否被明确标为未按当前策略验证？
14. `DEGRADED_LLM_TEXT` 是否有足够的可视提示，还是容易被用户当成正常 LLM 结论？
15. materializer 是否继续只透传 sidecar，不补造 LLM 判断？
16. synthetic/local preview 是否默认被排除在发布 manifest / fallback 外？
17. `index.html` 的 `?v=20260626-transition-v1.2.2` 是否与当前前端资产和预览语义一致？
18. 现有测试是否覆盖了所有关键单位路径？若不足，请列出最小新增测试。

## 11. 期望输出格式

请 Claude 使用代码审计格式输出，不要只给泛泛建议。

建议格式：

```text
Findings
1. [Severity] 标题
   - 文件/函数/行号：
   - 现象：
   - 复现或样本：
   - 期望行为：
   - 风险：
   - 建议修复：
   - 建议测试：

No-Issue Checks
- Funding 单位：
- Gamma/GEX 单位：
- Macro 单位：
- P/C 语义：
- Transition LLM 降级：
- source_ref/raw trace：

Open Questions
- ...
```

严重度建议：

- P0：会改变交易/执行边界、泄漏敏感信息、或把本地状态伪装成生产状态。
- P1：数值单位错误、主阅读区误导、LLM 降级失效、raw path 污染主结论。
- P2：历史兼容或边界场景可读性缺口。
- P3：文案、样式、测试覆盖的小幅改进。

## 12. 审计注意事项

- 当前页面是信号审计只读页面，不是交易 UI。
- 页面可以展示 `trade_allowed` 等原始审计字段，但不能暗示前端能执行交易。
- LLM review 只做复核/解释，不回写系统方向、置信、门控或交易许可。
- raw JSON 和 field path 应保留在 raw trace/provenance 区，但不要污染主解释。
- 若发现真实模型输出仍有污染，正确行为是 fail-closed 或降级展示，而不是静默当作通过。
- 如果需要提交本文件，注意 `docs/状态转移审计LLM复核Prompt_v1.1复评交付说明.md` 以及部分项目文档可能被本地 `.git/info/exclude` 排除；本轮仅生成审计请求文档，不执行 git add/commit/push。
