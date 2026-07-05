# 状态转移审计 LLM v1.2.1 第三轮自审收口与兼容性评估

> 日期：2026-06-25
> 范围：`tools/gemini_signal_llm_review.py`、`tools/materialize_signal_cards.py`、`deploy/signal_audit/frontend/app.js`、相关测试与项目记忆。
> 边界：不触碰 FMZ producer、执行层交易开关、系统 `decision / confidence / blocking / trade_allowed`。

## 1. 本轮结论

第三轮意见成立：v1.2 后续价值不在继续增加 Prompt 长度，而在工程闭环。当前已收口为 v1.2.1：

- 默认仍是 `single_call_evidence_first`，不宣称真盲审；
- `two_call_strict` 保持实验路径；
- Call 2 已改为 reconciliation-only schema，并由本地 validator / merge 白名单阻断新的 `observed_changes` 和其他 finding 字段注入；
- runner 增加证据目录 provenance、方向一致性、欠断言、domain × effect target 矩阵；
- reviewable transition 空 `observed_changes` 会被标记为 `missing_observed_changes`，不得作为 OK 正文展示；
- review generation 的 `limit` 按尝试目标计数，失败时也会停止在上限内，避免 fallback 成本失控；
- frontend 支持 explicit suppress、未知 render state fail-closed、legacy sidecar 未按当前策略验证；
- materializer 继续 pass-through sidecar，不伪造 LLM 结论。

项目当前兼容调整后的 transition LLM 链路。

## 2. 已采纳第三轮意见

| 第三轮意见 | 本轮处理 | 兼容性影响 |
|---|---|---|
| evidence catalog 需要 version/hash | 增加 `transition_evidence_catalog@1.0.0` 与 `evidence_catalog_hash`，sidecar 持久化 | 非破坏；旧 sidecar 仍可读 |
| enum false-positive 会误伤 render | 已确认 scanner 排除结构枚举，并加合法 enum 负样本测试 | 防止合法正文被误抑制 |
| fact/impact 可方向背离 | 增加 `fact_impact_direction_conflict` | 启发式，仅在 evidence 可解析数值方向时触发 |
| SUFFICIENT 欠断言 | 增加 `sufficient_evidence_understated` | 防止模型用全未定逃避解释 |
| effect_target 需要矩阵 | 增加 domain × target 检查；`DECISION` 禁止成为独立 finding | 阻断循环论证和 Gamma 方向化 |
| Call 2 应结构上不能改写 finding | 新增 reconciliation-only schema/request/validator，并在 merge 阶段只采纳 reconciliation 白名单字段 | Call 1 observed_changes / candidate_explanations 成为唯一事实解释来源 |
| 未知 render_state 应 fail closed | 前端隐藏正文并提示当前客户端未通过校验 | 面向未来 schema 更安全 |
| legacy sidecar 不应暗示当前策略通过 | 缺少 `policy_validation` 时显示“未按当前策略验证” | 旧卡仍可读但不混淆校验等级 |
| 空 finding 与失败重试成本 | `missing_observed_changes` 降级；`limit` 按尝试目标计数 | 防止空正文误过和失败 backlog 消耗失控 |

## 3. 暂不作为本轮代码改动

以下不是兼容性阻断项，进入下一阶段 shadow/A-B 前治理：

- 删除模型输出 `tendency_cn`，改为 runner 从 `directional_role × effect_target` 派生；
- 对所有 human-facing 字段做完整 numeric provenance validator；
- finding dependency graph、summary claim binding、局部 `DEGRADED` 依赖传播；
- coverage、state_significance、raw/normalized blind hash 区分；
- label-flip、order-shuffle、semantic mutation、missingness、prompt injection golden corpus；
- adaptive two-call 默认路由。

## 4. 调整后的链路

默认链路：

```text
transition ledger
-> SignalTransitionReviewPacket@1.1.1
-> single_call_evidence_first
-> signal_transition_llm_review@1.2.1
-> materializer pass-through by transition_id
-> frontend render by policy_validation.render_state
```

实验链路：

```text
Call 1 transition_delta_blind_first
-> Call 1 observed_changes / candidate_explanations
-> Call 2 reconciliation-only schema + local whitelist validator
-> runner deterministic merge, preserving Call 1 observed_changes / candidate_explanations
```

## 5. 兼容性评估

- **Runner**：兼容。schema/prompt 版本升到 v1.2.1，保留旧字段形态，新增 provenance 和 policy issue 不破坏 materializer/frontend。
- **Materializer**：兼容。只透传 sidecar dict，v1.2.1 字段不会影响 ledger hash chain。
- **Frontend**：兼容。当前状态支持 `DISPLAY_LLM_TEXT / DEGRADED_LLM_TEXT / SUPPRESS_LLM_TEXT`；未知未来状态 fail-closed；legacy 缺校验元数据时明确标识。
- **Docs/memory**：需要记录 transition two-call 仍是实验路径；不要把 card LLM two-call 默认与 transition LLM two-call 实验混在一起。
- **FMZ/执行层**：不涉及。本轮没有打开交易开关，也没有修改 signal producer。

## 6. 下一轮检查建议

下一轮不要用“更同意系统标签”作为好坏指标，而应拆成：

- fact-selection invariance；
- interpretation invariance；
- inter-field consistency；
- unsupported numeric claim rate；
- evidence-impact relevance 人工抽检；
- render suppression precision / false-positive rate；
- p50/p95 latency 与调用成本。
