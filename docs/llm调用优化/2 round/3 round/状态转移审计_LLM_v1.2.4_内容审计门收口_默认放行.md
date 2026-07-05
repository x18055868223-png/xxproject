# 状态转移审计 LLM v1.2.4 内容表达审计门收口（默认放行）

> 日期：2026-06-26
> 范围：`tools/gemini_signal_llm_review.py`、`tools/server_self_check_signal_stack.sh`、`tools/materialize_signal_cards.py`、`deploy/signal_audit/frontend/app.js`、`deploy/signal_audit/frontend/index.html`、相关测试与项目记忆。
> 边界：不触碰 FMZ producer、执行层交易开关、系统 `decision / confidence / blocking / trade_allowed`；不改 prompt 规则文本。

## 1. 产品口径调整

LLM 转移审计与复核意见本身**不影响置信度、因子、是否放行或执行**，它只是一个整理收纳与参考信息维度。因此本轮把内容表达审计从“展示门”降级为“调试 metadata”：

- **默认放行所有 LLM 正文。** 内容表达类 issue 不再决定 `passed / render_state`。
- `causal_overclaim`、`external_data_claim`、`fact_impact_direction_conflict`、`trading_instruction`（含“开仓/平仓/止损”等词）、`raw_field_path_leak`、`raw_enum_leak`、`unit_semantic_mislabel`、`materiality_boilerplate` 等**仅记录到 `policy_validation` 的 issue 字段作为调试线索**，不再触发标黄/标红/隐藏正文。
- 真正阻断展示的只剩**格式 / 结构问题**——因为格式才会影响前端审计页面的正常显示。

> 决策背景：r3.3.4 部署后服务器最近 8 条 sidecar 全部 `policy_passed != True`、且多条 `SUPPRESS_LLM_TEXT`。这不是“可长期接受的最终体验”，而是闸门过窄。本轮不走“收紧 prompt + validator 降噪”的长路，直接把内容门拆掉，只留格式门。

## 2. 仍然作为展示门保留的（结构 / 格式）

`_transition_policy_validation` 中以下问题仍 `passed=False` 且 `render_state=DEGRADED_LLM_TEXT`，因为它们破坏审计结构或证据可追溯性、影响前端正常渲染：

- `missing_observed_changes`（空正文无可展示内容）
- `invalid_evidence_ref` / `system_assertion_evidence_ref` / `missing_evidence_ref`（证据引用断链或指向系统断言）
- `missing_core_domain_coverage`（核心骨架覆盖缺失）
- `system_assertion_observed_change`（系统断言伪装成独立观察）
- `invalid_effect_target_for_domain`（domain × effect_target 矩阵违例）
- `sufficient_evidence_understated` / `incompatible_epistemic_state` / `partial_evidence_changes_judgment`（证据-断言状态机不自洽）
- observed_change 缺 `fact_cn / impact_cn / tendency_cn`（结构不完整）

`SUPPRESS_LLM_TEXT`（FATAL）与“内容类 WARN→DEGRADED”分支已删除：runner 不再因内容主动隐藏正文。

## 3. 代码改动

| 文件 | 改动 |
|---|---|
| `tools/gemini_signal_llm_review.py` | 版本升至 `signal_transition_llm_review@1.2.4` / `gemini_signal_transition_review_prompt@1.2.4`（prompt 文本不变）；`_transition_policy_validation` 改为只对结构问题 `structural_block` 置 `passed=False / DEGRADED`，内容 issue 全部保留为 metadata；`_validate_transition_payload` 去掉 `language_guard` 自陈（`no_external_data` 等）的 `raise`，只留必填字段 / 类型 / 枚举的格式校验 |
| `deploy/signal_audit/frontend/app.js` | 主渲染路径去掉内容类 `is-degraded` 琥珀门与降级 banner，统一为中性参考块（新增 `transition-llm-reference-note`：“LLM 旁路参考，不影响置信度/因子/放行/执行”）；**保留** `SUPPRESS_LLM_TEXT` 与未知 render_state 的 fail-closed 防御分支；legacy sidecar 仍显示“未按当前策略验证”并可读 |
| `deploy/signal_audit/frontend/index.html` | 新增中性参考块样式；缓存串 `20260626c-transition-v1.2.3 → 20260626d-transition-v1.2.4` |
| `tools/server_self_check_signal_stack.sh` | 版本校验 `→1.2.4`；**去掉** `policy_passed 必须为 True` 与 `language_guard` 自陈的部署门；只保留 schema/version、`evidence_catalog_hash`、已知 `render_state` 的格式校验（`policy_passed / issue_codes` 仍打印可见） |
| `tools/materialize_signal_cards.py` | `TRANSITION_REVIEW_SCHEMA_VERSION → @1.2.4`（pass-through，不重算 policy） |

## 4. 测试

`tests/` 全量绿（含 codex 预置的 `test_signal_llm_review_pipeline.py` / `test_signal_audit_frontend_render_contract.py` / `test_server_bootstrap_assets.py` 新口径）：

- 内容类（direction_conflict / trading「开仓」/ raw_field_path_leak / external_data_claim / causal_overclaim）→ `passed=True` + `DISPLAY_LLM_TEXT` + issue 入 `issue_codes`；
- 结构类（invalid_ref / missing_evidence_ref / missing_observed_changes / understated / domain-target 等）→ `passed=False` 保持；
- `language_guard.no_external_data=False` 不再抛 `ValueError`；
- 前端 raw_leak 不再渲染 `transition-llm is-degraded`，suppress / unknown 仍 fail-closed。

## 5. 发布

随 `r3.3.5` 发布（沿用 r3.3.x 节奏）。服务器更新沿用既有 bootstrap + materialize + 单轮 `gemini_signal_llm_review.py --mode transition` 重建命令；本轮删除内容门后，自检不再因内容 issue 失败。
