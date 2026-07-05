# 状态转移审计 LLM 复核 Prompt 评估交付说明 v1.0

> 交付对象：GPT-5.5 Pro / Opus 4.8
> 文档目的：请外部强模型评估当前状态转移审计 LLM prompt 是否存在优化空间，并在提出优化时给出可落地 prompt、理论依据、风险边界与验证方法。
> 当前基线：`tools/gemini_signal_llm_review.py` 中的 transition review 路径，prompt version `gemini_signal_transition_review_prompt@1.0.0`，输出 schema `signal_transition_llm_review@1.0.0`。
> 重要边界：本文档只用于 prompt 评估和方案征询，不包含任何密钥值、账户信息、服务器私有路径或执行层交易指令。

---

## 1. 背景与定位

状态转移审计是信号审计层在 r3.3 系列中新增的只读观察能力。它比较相邻两张信号审计卡，把信号事件之间的市场状态变化整理为可追溯的变化链，用于帮助人工审计人员理解：

- 当前信号事件相比上一张卡，核心市场状态发生了什么变化；
- 这些变化对信号解释、风险背景和审计判断有什么实际影响；
- 变化是否足以改变人工关注重点，还是只构成背景扰动；
- 是否存在跨因子共振、冲突扩大、冲击门触发、数据质量退化或历史卡兼容误读。

LLM 的参与不是为了装饰性总结，而是为了辅助用户做人工审计和认知决策：它应基于已有结构化数据深挖更深层的有效信息，把分散字段综合成可判断的市场状态路径，并给出清晰的倾向性表达与审计方案意见。这里的“方案意见”指人工下一步应关注什么、如何验证当前解释、哪些条件会使解释失效，不是下单、仓位或执行建议。

LLM 在这里不是方向判官，也不是执行建议生成器。它是一个审计旁路认知增强层：

- 不改变 `decision`；
- 不改变 `confidence`；
- 不改变 `blocking`；
- 不改变 `trade_allowed`；
- 不写入执行层动作；
- 不生成仓位、下单、对冲或交易许可建议。

当前最重要的产品目标是：**请外部 reviewer 基于现有系统、数据结构和边界约束，判断如何把变化解释从“材料性/关键性”这类低信息量标签，提升为更能辅助人工审计决策的高信息密度设计。**

因此，外部 reviewer 评估 prompt 时，不应只检查它是否安全或格式正确，还要判断它是否真的能帮助用户从已有数据中获得更高信息密度的决策辅助：

- 是否挖掘字段之间的组合含义，而不是逐字段复述；
- 是否能表达利空、利多、中性、风险约束、支撑、缓和等倾向，或提出更合适的倾向表达框架；
- 是否提出可执行的人工审计关注方案，例如继续观察哪个因子、等待哪个失效条件、检查哪个跨域共振；
- 是否避免把“趋势倾向”偷换成交易建议；
- 是否存在比当前 `fact_cn / impact_cn / tendency_cn` 更好的结构化表达方式。

---

## 2. 当前 LLM API 调用目的

### 2.1 调用目的

当前 transition LLM 调用用于生成 `transition_llm_review` sidecar。它的目的包括：

1. 把 materializer 已经计算好的状态转移 delta 转换为中文审计解释。
2. 从多维市场状态骨架中提取高信息量叙事，而不是逐字段展开 JSON。
3. 对 `TMV / MACRO / Funding / Skew / Gamma / P/C / Conflict / Decision / Quality` 等维度做综合解释。
4. 说明每项变化的实际影响和倾向性。
5. 区分观察事实、可能解释和失效条件。
6. 提供人工审计方案意见：下一步应关注哪些因子、哪些条件会强化或削弱当前倾向、哪些异常需要人工复核。
7. 保持审计边界，不输出任何执行层建议。

### 2.2 调用入口与数据流

当前 transition review 调用入口：

```bash
python tools/gemini_signal_llm_review.py --mode transition
```

核心数据流：

```text
FMZ producer 原始审计卡
  -> signal_review.jsonl
  -> materializer 生成 signal_transition_ledger.jsonl
  -> gemini_signal_llm_review.py --mode transition
  -> signal_transition_llm_reviews.jsonl
  -> materializer 合并 sidecar 到单卡 JSON
  -> 前端状态转移审计区展示
```

输入来源：

- `signal_transition_ledger.jsonl`
- 每条 ledger record 是 materializer 从相邻两张审计卡生成的 `SignalTransitionRecord@1.0.0`
- LLM 不读取中文展示文本作为事实来源，主要读取结构化 packet

输出位置：

- `signal_transition_llm_reviews.jsonl`
- 以 `transition_id` 去重
- sidecar 合并后进入当前卡的 `transition_llm_review`

当前默认模型：

- `gemini-3.5-flash`

当前版本：

- prompt version：`gemini_signal_transition_review_prompt@1.0.0`
- output schema：`signal_transition_llm_review@1.0.0`

### 2.3 当前两类 LLM 调用路径

当前工具同时支持单卡复核和状态转移复核，两者不是同一个调用结构。

单卡 LLM review 当前采用严格两次调用真盲审：

1. 第一次调用读取 `BLIND_THEORETICAL_PACKET`，该包只包含身份、市场上下文、数据质量和核心因子截面，不包含系统 `decision / reasoning / conflict / blocking / trade_allowed`。
2. 第一次调用只生成 `theoretical_active_view` 与 `gamma_regime_lens`，目的是让模型先在不知道系统结论和门控的情况下形成独立理论观察。
3. 第二次调用读取 `BLIND_REVIEW_RESULT + FULL_AUDIT_PACKET`，用于检查系统结论、证据账本、冲突账本和门控是否与第一次盲读视角一致。
4. 第二次调用不得重写第一次盲读结果，只能把盲读作为独立参考视角纳入审计复核。
5. sidecar 记录 `blind_review_mode=two_call_strict`、`llm_call_count=2`、`llm_call_routes`、`api_key_route`，用于追溯调用链、密钥通道和失败路径。

真盲审的设计初衷：

- 降低模型被系统结论、门控标签、置信度或材料性排序锚定的风险；
- 让模型先基于市场截面形成独立理论读数，再与系统结论进行对照；
- 把“独立观察”和“系统一致性复核”分层，便于人工审计判断 LLM 是在补充信息，还是只是复述系统标签；
- 保持只读边界：盲读视角不是系统信号，不改变 `decision / confidence / blocking / trade_allowed`。

状态转移 LLM review 当前仍是单次调用：

1. 输入为 `SignalTransitionReviewPacket`，来源于 materializer 生成的 `signal_transition_ledger.jsonl`。
2. 输出为 `signal_transition_llm_reviews.jsonl`，再由 materializer 合并到前端卡片。
3. 当前 transition sidecar 记录 `api_key_route / llm_call_routes / input_packet_hash / prompt_version`，但没有实现与单卡 review 等价的 `two_call_strict` 真盲审。
4. 因此，请外部 reviewer 不要把 transition review 误认为已经完成双调用盲审；是否应引入类似设计，正是本次征询重点之一。

---

## 3. 当前调用要求与工程约束

### 3.1 审计边界

LLM 只能解释程序已经计算出的 transition delta，不得：

- 重算字段；
- 重算权重；
- 重算置信度；
- 重算材料性；
- 使用外部行情；
- 把相关性写成确定因果；
- 输出交易建议、仓位建议、下单建议或执行层动作。

### 3.2 密钥路由与软失败

当前 LLM runner 支持双通道密钥路由：

- channel 1：默认低成本路径；
- channel 2：失败或限流时的备用路径；
- sidecar 记录 `api_key_route` / `llm_call_routes`，用于审计实际调用走向；
- 不记录任何密钥值。

失败策略：

- LLM 调用失败不会阻断 producer 和 materializer；
- 失败会生成错误 sidecar 或保留 pending 状态；
- 前端展示 pending / error，但不把失败伪装为通过；
- 失败不改变系统信号结论。

### 3.3 去重、可复现与审计追溯

当前 transition review 使用以下机制保持可追溯：

- `transition_id` 去重；
- `input_packet_hash` 绑定本次输入包；
- `prompt_version` 记录 prompt 版本；
- `model` 记录模型名；
- `reviewed_at` 记录复核时间；
- `language_guard` 记录安全约束结果；
- sidecar 独立存储，materializer 只做合并，不伪装为 producer 原生字段。

### 3.4 Redaction 与数据边界

当前调用包只应包含审计所需字段，不应包含：

- 密钥值；
- 账户信息；
- 本地个人路径；
- 服务器私有路径；
- 执行层订单、仓位、下单许可字段；
- 与状态转移解释无关的大对象。

请评估当前 prompt 是否足以约束模型不扩展上下文、不编造外部数据、不输出越权建议。

---

## 4. 当前输入包结构

当前 `SignalTransitionReviewPacket` 由 `build_transition_review_packet()` 构造，核心字段如下：

```text
schema
identity
comparison
decision_transition
core_skeleton
core_transition_display
domain_change_summaries
top_material_changes
recent_5_trajectory
baseline_24h
episode_anchor
trajectory
domain_states
cross_domain_flags
materiality_score
field_glossary
guardrails
```

各字段用途：

| 字段 | 用途 |
|---|---|
| `identity` | transition id、symbol、前后 card id、时间戳、elapsed |
| `comparison` | 比较质量、比较限制、是否同 episode |
| `decision_transition` | 决策状态、置信、阻断状态的前后变化 |
| `core_skeleton` | 多维市场状态骨架 |
| `core_transition_display` | materializer 给出的显示层行，包含标题、前值、当前值、含义 |
| `domain_change_summaries` | 按 domain 聚合后的变化摘要 |
| `top_material_changes` | 底层排序后的原始变化 trace，仅用于追溯 |
| `trajectory` / `domain_states` | 最近路径与状态分类 |
| `cross_domain_flags` | 跨域旗标，例如宏观冲击、资金拥挤、Gamma 体制变化 |
| `field_glossary` | 字段含义说明 |
| `guardrails` | 审计边界约束 |

关键设计判断：

- `core_transition_display` 应是 LLM 主叙事锚点；
- `top_material_changes` 只能作为底层 trace，不应主导语言；
- 拆分后的宏观子字段不能挤占 TMV、Funding、Skew、Gamma、P/C、Conflict 等维度；
- materiality 只能帮助排序，不应成为主语言。

---

## 5. 当前 Prompt 原文

以下为当前 `build_transition_review_prompt()` 的静态 prompt 模板。运行时会在末尾拼接 `SignalTransitionReviewPacket` 的 JSON。

```text
你是信号审计变化链复核员，只解释程序已经计算出的 delta，不得重算字段、权重、置信度或材料性。
严格边界：不得使用外部行情，不得把相关性等于因果，不得输出交易建议、仓位建议、下单建议或执行层动作。
请基于 SignalTransitionReviewPacket 输出结构化中文解释，优先锚定 packet 中的 core_transition_display，其次参考 core_skeleton 和 domain_change_summaries，围绕 TMV/TMVF、期货资金费率、期权斜率、net gamma/GEX、P/C 比例、冲突比例和宏观状态解释综合变化链；top_material_changes 只作为底层 trace，不得让拆分后的宏观子字段主导解释。所有判断还要参考 cross_domain_flags、comparison_quality 和 comparison_limitations。

中文表达约束：结论句不得直接复用 raw enum；NEUTRAL 写成“中性”，MACRO_BLOCKING 写成“宏观硬阻断”，MACRO_SHOCK_BLOCKING 写成“宏观冲击门阻断”，Headwind 写成“逆风”。observed_changes 必须说明倾向性、潜在意义和幅度是否足以改变判断，不要只复述 previous -> current；不要把 P/C 比例描述为正负符号翻转，P/C 只能解释为期权保护需求或相对需求变化。observed_changes 每项必须拆成三层：fact_cn 只写客观数值变化，impact_cn（actual_impact_cn）写这个变化对市场状态或审计判断的实际含义，tendency_cn 写清倾向，例如“利空/风险约束”“利多/支撑”“中性/缓和”。禁止使用“评估为关键变化”“被评估为高材料性变化”“材料性变化”或只说“关键/高”这类无实际审计含义的套话。

SignalTransitionReviewPacket:
{packet_json}
```

请重点评估：

- 这个 prompt 是否足以强制模型输出实际影响，而不是复述 delta；
- 这个 prompt 是否足以压制“材料性/关键性”套话；
- 这个 prompt 是否足以深挖已有数据中的跨因子含义，而不是停留在表层变化；
- 这个 prompt 是否能输出对人工审计决策有帮助的方案意见，例如观察重点、验证路径和失效条件；
- 这个 prompt 是否会让模型过度方向化；
- 这个 prompt 是否会让模型忽略非宏观维度；
- 这个 prompt 是否需要更明确的 domain-by-domain 推理框架；
- 这个 prompt 是否需要更强的因果审慎表达模板。

---

## 6. 当前回复格式与原因

当前 response schema 要求 JSON object，关键字段如下：

```text
transition_summary_cn: string
trajectory_state: enum
signal_continuity: enum
observed_changes: array
  - domain: string
  - fact_cn: string
  - impact_cn: string
  - tendency_cn: string
  - materiality: optional string
cross_factor_interactions: string[]
candidate_causal_hypotheses: object[]
anomaly_assessment: object
operator_focus: string[]  # 人工观察重点和审计方案意见
invalid_if: string[]
language_guard: object
not_trading_advice: boolean
```

说明：当前代码中的结构化字段是 `not_trading_advice`；本文用 `no_trading_instruction` 作为同一安全目标的评估标签，表示“不得生成交易、仓位、下单、对冲或执行层建议”。若外部 reviewer 建议改名，必须给出向后兼容策略。

其中 `observed_changes` 的当前目标格式：

```json
{
  "domain": "MACRO",
  "fact_cn": "美债10年期收益率评分从6 bps升至22 bps。",
  "impact_cn": "进一步推升风险资产压力，并触发宏观冲击门阻断。",
  "tendency_cn": "利空/风险约束"
}
```

结构化 JSON 的原因：

1. **可追溯**：每个字段能映射到前端固定区域，避免自然语言堆叠。
2. **可测试**：测试可以断言 `impact_cn`、`tendency_cn` 是否存在，并检查是否出现材料性套话。
3. **可降噪**：前端可把事实、影响、倾向分层展示。
4. **可兼容**：旧 sidecar 缺字段时，前端可用 `core_transition_display` 回填显示含义，但不改 raw JSON。
5. **可控边界**：`language_guard` 与 `not_trading_advice` 让审计层和执行层隔离。
6. **可比较**：同一 packet 可由不同模型复核，基于 `input_packet_hash` 对比结果。

请评估当前 schema 是否足以约束输出，是否需要新增、删除或重命名字段。若建议 schema 调整，请说明向后兼容策略。

---

## 7. 应用场景与评估重点

请围绕以下典型场景评估 prompt 表现：

### 7.1 MACRO 冲击门

目标不是只说“宏观变化关键”，而是说明：

- 美债收益率、美元指数、波动率压力变化意味着什么；
- 是否推升风险资产压力；
- 是否进入 `WATCH/BLOCK` 等冲击门状态；
- 是否只是方向背景，还是已经构成硬阻断；
- 对原先 TMV 或决策状态有什么压制。

### 7.2 TMV / TMVF 量价路径

评估是否能表达：

- 量价路径由强转弱、由负转正、维持稳定的审计含义；
- 是支撑方向骨架，还是削弱方向骨架；
- 变化幅度是否足以改变判断。

### 7.3 Funding 期货资金费率

评估是否能表达：

- 正费率上升是否代表多头拥挤升温；
- 费率转负是否代表多头付费压力消失或空头需求上升；
- 小幅变化是否只构成中性/拥挤缓和；
- 不得把归一化分数误写成真实费率。

### 7.4 Skew / P/C 期权需求

评估是否能表达：

- 期权保护需求升温或回落；
- P/C 是非负比率，不得写“正负符号翻转”；
- 偏斜变化是尾部保护、方向压力还是中性缓和。

### 7.5 Gamma / GEX 空间约束

评估是否能表达：

- 净 Gamma 名义敞口增加或减少对波动放大/钉住的意义；
- 小量级历史兼容指标不得伪装成 USD 名义额；
- Gamma 只能作为空间/风险约束，不应越权成为方向结论。

### 7.6 Conflict / Decision

评估是否能表达：

- 冲突比例升高是否意味着信号分歧扩大；
- 决策置信下降是否代表证据支持塌缩；
- 阻断状态持续是否应该写成“持续受宏观硬阻断”或“宏观冲击门阻断”，而不是 raw enum。

---

## 8. 请 GPT-5.5 Pro / Opus 4.8 交付的内容

请外部 reviewer 不要只给“更好/更清晰”这类主观评价。请按以下格式交付。

### 8.1 当前 Prompt 逐段评估

请逐段评价当前 prompt：

1. 哪些约束是有效的；
2. 哪些约束不够明确；
3. 哪些约束可能互相冲突；
4. 哪些约束可能导致输出过长或过度保守；
5. 哪些约束可能导致模型仍然复述 delta；
6. 哪些约束可能导致模型忽略非宏观维度。

### 8.2 优化建议

如果建议优化，请给出：

- 完整替代 prompt；或
- 针对当前 prompt 的局部 patch；
- 如果建议调整调用架构，请给出单次调用、两次调用真盲审或其他方案的完整数据流；
- 如果涉及 schema 变化，请给出新旧字段映射；
- 如果不建议改 prompt，也请说明为什么当前版本已经足够。

### 8.3 理论依据

每条优化建议都必须给出理论依据。可参考但不限于：

- 信息增益：为什么该改法能减少低信息量复述；
- 因果审慎：为什么该改法能避免把相关性写成因果；
- 审计可追溯：为什么该改法让输出更容易追溯到结构化字段；
- 结构化输出稳定性：为什么该改法能提高 JSON 一致性；
- 认知负荷控制：为什么该改法能让前端读者更快抓住重点；
- 金融语义精确性：为什么该改法能避免单位、方向、比例或期权语义误读；
- 安全边界：为什么该改法不会引入交易建议或执行层越权。

### 8.4 必须给出的 before / after 示例

至少给出 2 组 before / after，用于展示优化方案如何解决当前问题。示例不要求沿用本文的句式，重点是证明新 prompt 或新 schema 能带来更高信息增益，其中必须包含：

1. MACRO 压力上升；
2. Funding / Gamma / P/C 中至少一个非宏观维度。

示例格式：

```text
Before:
美债收益率评分从 6 bps 升至 22 bps，被评估为关键变化。

After:
请给出你认为更优的结构化表达，并说明该表达为什么比 before 更有信息量。
理论依据：...
```

### 8.5 风险与验证方法

请列出每个优化方案的潜在风险：

- 是否会过度方向化；
- 是否会引入伪因果；
- 是否会让模型过度自信；
- 是否会导致输出过长；
- 是否会破坏 schema 稳定性；
- 是否会让前端展示变得拥挤；
- 是否会生成交易建议或执行建议；
- 是否会误读历史卡兼容字段。

请同时给出验证方法：

- 应增加哪些 prompt 单测；
- 应增加哪些前端渲染断言；
- 应增加哪些真实 LLM smoke case；
- 如何判断“影响 + 倾向性”已经比“材料性”更有信息量；
- 如何在不改变系统信号结论的前提下增强解释质量。

### 8.6 对真盲审架构的评估

请专门评估 transition 变化链是否应该引入类似单卡 review 的两次调用真盲审：

- 如果建议引入，请说明第一次盲读应隐藏哪些字段，例如 `decision_transition`、blocking、materiality、系统标签、置信度或材料性排序；
- 如果不建议引入，请说明单次调用如何达到足够的信息增益、独立性和倾向性表达；
- 请比较单次调用与两次调用在成本、延迟、sidecar schema、前端合并复杂度、输出稳定性、认知增益上的取舍；
- 请说明是否需要保留 `BLIND_THEORETICAL_PACKET / BLIND_REVIEW_RESULT` 的命名，还是为 transition 设计新的盲读包名称。

---

## 9. 评估红线

以下建议不应被采纳：

1. 让 LLM 改写系统方向、置信度、阻断状态或交易许可。
2. 让 LLM 使用外部行情或自行补充未提供的数据。
3. 让 LLM 输出仓位、下单、止损、止盈、对冲或执行建议。
4. 让 LLM 把 `materiality` 当成主结论。
5. 让 LLM 把 P/C 比例写成正负符号翻转。
6. 让 LLM 把 Gamma 空间约束写成方向信号。
7. 让 LLM 用英文 raw enum 替代中文语义。
8. 让 prompt 依赖前端展示文本而不是结构化字段。

---

## 10. 推荐评审输出模板

请 GPT-5.5 Pro / Opus 4.8 按以下模板回复：

```markdown
# 状态转移审计 LLM Prompt 评估结果

## 一句话结论

## 当前 Prompt 有效部分

## 当前 Prompt 不足部分

## 建议优化版 Prompt

## 关键修改点与理论依据

| 修改点 | 解决的问题 | 理论依据 | 潜在风险 | 验证方法 |
|---|---|---|---|---|

## Before / After 示例

### 示例 1：MACRO 压力上升

### 示例 2：非宏观维度

## 是否建议调整 schema

## 是否建议调整调用架构

### 是否建议 transition 引入两次调用真盲审

## 是否存在越权或安全风险

## 最终建议
```

---

## 11. 本次征询的核心判断问题

请重点回答：

1. 当前 prompt 是否足以稳定实现“事实变化 -> 实际影响 -> 倾向性”？
2. 是否需要把 `impact_cn` 改名为 `actual_impact_cn`，或者保留当前字段名？
3. 是否应该给每个 domain 增加固定解释模板？
4. 是否应该压缩 `transition_summary_cn`，把主要信息放入 `observed_changes`？
5. 是否应该要求模型输出“幅度是否足以改变判断”作为独立字段？
6. 如何避免 LLM 把“利空/利多”写成交易建议？
7. 如何避免宏观子字段过度主导解释？
8. 真实历史卡缺字段时，prompt 应如何要求模型表达“不足以判断”？
9. 当前 schema 是否已经足够前端稳定展示？
10. 当前 prompt 是否足以给出有用的人工审计方案意见，而不仅是解释？
11. transition 变化链是否应该引入类似单卡 review 的两次调用真盲审？
12. 如果引入 transition 真盲审，第一次盲读应隐藏哪些字段，第二次复核应如何合并盲读结果？
13. 如果不引入 transition 真盲审，当前单次 prompt 应如何避免被系统标签和材料性排序锚定？
14. 如果只能改一处 prompt、schema 或调用架构，最值得改哪里？

---

## 12. 开放式评估目标与设计征询

以下内容不是标准答案，也不是要求外部 reviewer 必须照抄的表达范式。它们只是当前版本或历史兼容路径中暴露出的典型问题样本，用来帮助 reviewer 理解为什么需要重新评估 prompt、schema 和调用架构。

已观察到或需要重点防止的问题包括：

1. **复述 delta**：只说某字段从 previous 变成 current，没有说明该变化对市场状态、信号路径或人工审计有什么意义。
2. **材料性套话**：把“关键变化”“高材料性”当成结论，但没有提供新的解释信息。
3. **raw enum 泄露**：在中文结论中直接输出 `NEUTRAL / MACRO_BLOCKING / Headwind` 等系统枚举，而不是表达为中性、宏观硬阻断、逆风等中文语义。
4. **单位误读**：把归一化分数、兼容指标或小量级历史字段误写成真实费率、USD 名义额或可比较的市场金额。
5. **P/C 符号误读**：把 Put/Call 这类非负比率的升降误写成“正负符号翻转”。
6. **Gamma 误读**：把旧卡兼容 Gamma 指标伪装成 USD 名义敞口，或把 Gamma 空间约束直接写成方向信号。
7. **宏观过度主导**：拆分后的 DXY / US10Y / VOLQ 子字段挤占 TMV、Funding、Skew、Gamma、P/C、Conflict 等维度。
8. **方案意见不足**：只解释发生了什么，没有指出人工审计下一步应重点观察什么、如何验证当前解释、哪些条件会使解释失效。

请 GPT-5.5 Pro / Opus 4.8 基于以上问题样本，自主提出更优方案：

- 可以改 prompt 文案；
- 可以改 response schema；
- 可以改字段命名或输出层级；
- 可以建议 transition 变化链引入两次调用真盲审；
- 也可以论证当前单次调用更合适，但需要给出如何避免锚定和低信息量复述的设计。

评价标准不是“是否符合本文给出的某个句式”，而是：

- 是否从已有结构化数据中提取出更深层的有效信息；
- 是否能形成清晰但不过度自信的倾向表达；
- 是否能给出有用的人工审计方案意见；
- 是否保持可追溯、可测试、可前端稳定渲染；
- 是否严格维持审计边界，不生成交易执行建议。

---

## 13. 交付边界

请外部 reviewer 只评估 prompt、schema 与审计解释方法，不需要也不应要求：

- 修改 FMZ producer；
- 修改执行层；
- 增加独立服务；
- 改变交易开关；
- 使用外部行情；
- 调用真实账户；
- 访问本地或服务器私有文件。

所有建议必须能回落到当前状态转移审计和 LLM 复核层的增强。LLM 可以更深地辅助用户理解数据、形成倾向性判断和人工审计方案，但不得把 LLM 变成交易系统的一部分。
