# 状态转移审计 LLM Prompt 评估结果

## 一句话结论

当前 Prompt 的审计边界、金融语义禁区和中文表达约束是有效的，但**不足以稳定实现“事实变化 → 审计含义 → 倾向 → 是否改变人工关注重点”**。最值得优先修改的不是增加更多禁止句，而是要求每项结论绑定 `evidence_refs`、`evidence_status` 和 `audit_attention_effect`。

---

## 当前 Prompt 有效部分

| Prompt 段落 | 有效之处 | 评价 |
|---|---|---|
| “只解释程序已经计算出的 delta，不得重算……” | 明确了只读边界，阻止 LLM 重算权重、置信度和材料性 | 应保留 |
| “不得使用外部行情……不得输出交易建议……” | 清楚隔离审计层与执行层 | 应保留，并增加代码侧校验 |
| 明确 TMV、Funding、Skew、Gamma、P/C、Conflict、MACRO 等 domain | 防止输出退化成单一宏观摘要 | 方向正确，但缺少覆盖配额 |
| `fact_cn / impact_cn / tendency_cn` 三层 | 比单一自然语言摘要更容易测试和前端展示 | 应保留，但需要增加证据和判断影响字段 |
| P/C、Funding、Gamma 的专门限制 | 能防止最常见的金融语义和单位错误 | 很有价值，应升级成结构化 domain 规则 |
| 禁止“关键变化”“高材料性变化”等套话 | 能压制部分低信息量输出 | 只能解决词汇表面问题，不能保证信息增益 |
| raw enum 中文映射 | 有助于前端语言一致性 | 应改为完整映射和代码侧泄露检查 |

---

## 当前 Prompt 不足部分

### 1. “只解释 delta”可能反而鼓励复述 delta

当前表述没有明确区分：

- 允许的包内综合解释；
- 不允许的数值重算；
- 直接观察；
- 有证据支持的解释；
- 尚未验证的候选解释。

保守模型很容易选择最安全的行为：把 `previous → current` 改写成中文，然后附上“风险上升”“构成支撑”等泛化短语。

建议改成：

> 可以综合多个 packet 字段形成审计解释，但不得生成 packet 中不存在的数值、状态或外部事实；所有非直接观察必须标明其证据状态。

### 2. `impact_cn` 承担了过多职责

当前要求一个字符串同时回答：

1. 这项变化意味着什么；
2. 是利空还是利多；
3. 幅度够不够；
4. 是否会改变人工关注重点；
5. 是否存在不确定性。

模型通常只会覆盖其中一两项。因此，当前三层结构在概念上正确，但 schema 约束不足。

### 3. “实际影响”容易诱发伪因果

“实际影响”可能被模型理解为已经发生并得到确认的因果结果，例如：

> 美债收益率上升导致风险资产下跌。

而 packet 很可能只支持：

> 利率压力与风险约束同步增强，构成一种与风险资产不利的状态组合。

因此不建议把字段改名为 `actual_impact_cn`。“actual”会进一步强化因果确定性。

### 4. 禁止材料性套话不能替代正向质量标准

禁止“关键变化”后，模型仍可能改写成：

- “值得注意的变化”；
- “显著变化”；
- “核心变化”；
- “需要关注”。

这些仍然没有解释信息。Prompt 必须正向要求：

- 改变了哪个市场状态；
- 通过什么包内机制产生约束或支撑；
- 对人工审计关注是否有影响；
- 依据来自哪些字段。

### 5. `core_transition_display` 不应成为事实权威

建议继续把它作为**覆盖索引和展示规范化锚点**，但不要把其中的 `meaning` 文本当作独立证据：

- 数值事实应来自 `core_skeleton`、轨迹字段和底层结构化值；
- `core_transition_display` 用于确保重要 domain 没有遗漏；
- `meaning` 只用于校验展示口径，不应被 LLM 直接改写为结论。

否则 LLM 很容易只是扩写 materializer 已经给出的含义。

### 6. 系统标签和排序会产生锚定

即使 Prompt 说“材料性不能主导”，模型仍然同时看到了：

- `decision_transition`；
- blocking；
- confidence；
- `materiality_score`；
- `top_material_changes` 排序；
- `cross_domain_flags`。

语言模型评审对候选顺序、标签和上下文线索存在明显敏感性；将这些字段全部放在同一次调用里，无法通过一句“不要被锚定”真正消除影响。

### 7. 宏观维度仍可能过度占位

当前 Prompt 虽然明确要求不要忽略非宏观维度，但没有规定：

- DXY、US10Y、VOLQ 是否必须聚合；
- MACRO 最多占几个 `observed_changes`；
- 有可用非宏观数据时至少覆盖几个非宏观 domain。

只要 `top_material_changes` 中宏观子项排名靠前，模型仍可能输出三条宏观、零条 Funding/Gamma/P/C。

### 8. 缺字段和不可比口径没有进入每项观察的 schema

`comparison_quality` 和 `comparison_limitations` 只是全局参考，不能阻止模型对某个具体字段作过强解释。

每项 `observed_changes` 都应有：

```text
evidence_status:
  SUFFICIENT
  PARTIAL
  NOT_COMPARABLE
  MISSING
```

当历史卡字段缺失或口径不兼容时，应明确：

> 该域不参与本次倾向合成。

而不是勉强输出“中性”。

### 9. `candidate_causal_hypotheses` 与因果审慎目标有张力

字段名本身会提示模型生成因果故事。建议改成：

```text
candidate_explanations
```

并要求同时输出：

- 支持证据；
- 反证或缺口；
- `causal_status = UNVERIFIED`。

### 10. `operator_focus: string[]` 太自由

它可能产生：

- 泛化建议：“继续关注宏观环境”；
- 越权建议：“应降低风险敞口”；
- 无法测试的建议：“谨慎操作”。

更合适的是结构化 `operator_checks`，明确这是人工核验任务，而不是市场行动。

### 11. `language_guard` 和 `not_trading_advice` 是自我声明，不是安全控制

模型输出：

```json
"not_trading_advice": true
```

并不证明正文没有交易建议。它们可保留作为兼容字段，但最终状态应由 runner 的确定性校验器产生。

---

# 建议优化版 Prompt

以下版本可作为单调用兼容改造，也可作为推荐双调用架构中的第二次复核 Prompt。

```text
你是“状态转移审计解释器”。你的输出只服务于人工审计，不是交易系统的一部分。

【任务】
根据 SignalTransitionReviewPacket，解释相邻两张审计卡之间的市场状态路径：
1. 哪些变化是可直接观察的事实；
2. 这些变化在 packet 内支持怎样的市场状态或审计含义；
3. 它们分别构成风险约束、支撑、中性缓和、混合状态，还是证据不足；
4. 变化是否足以改变人工审计关注重点；
5. 人工下一步应核对什么，以及哪些条件会强化或削弱当前解释。

【不可越界】
- 只能使用 packet 内的信息。
- 不得重算字段、权重、置信度、材料性、decision、blocking 或 trade_allowed。
- 不得补充外部行情、历史知识、账户、仓位或执行信息。
- 不得把共同变化、时间先后或相关性写成已证实因果。
- 不得输出买入、卖出、做多、做空、开仓、平仓、加仓、减仓、止损、止盈、对冲、下单或交易许可建议。
- 不得改变、纠正或替代系统 decision；只能解释其证据背景或指出与独立观察之间的张力。

【证据层级】
A. 数值和状态事实：优先使用 core_skeleton、trajectory、recent_5_trajectory、
   baseline_24h、domain_states 中的结构化原始字段及其单位元数据。
B. core_transition_display：只作为 domain 覆盖和展示口径索引；
   其中的 meaning 文本不是独立事实，不得仅对其改写扩写。
C. domain_change_summaries：只作为交叉核对。
D. decision_transition、cross_domain_flags、materiality_score、
   top_material_changes 排序和材料性标签：属于系统结论或排序信号，
   不是独立证据。不得直接复制为 LLM 结论。

【工作规则】
先检查 comparison_quality、comparison_limitations、字段缺失、单位和历史兼容性。
不要输出分析过程，只输出最终 JSON。

对每个可解释 domain：
1. 给出 1 至 4 个 evidence_refs，必须是 packet 中真实存在的字段路径。
2. 标记 evidence_status：
   SUFFICIENT / PARTIAL / NOT_COMPARABLE / MISSING。
3. fact_cn 只描述 packet 明示的前值、当前值、状态或缺失情况，
   不加入原因、评价和材料性语言。
4. impact_cn 描述该变化改变了什么市场状态、风险约束、支撑或审计解释。
   impact_cn 是“基于包内证据的审计含义”，不是已证实因果结果。
5. directional_role 只能是：
   RISK_CONSTRAINT / SUPPORT / NEUTRAL_OR_EASING / MIXED / UNDETERMINED。
6. tendency_cn 用中文表达 directional_role，并明确作用对象，
   例如“对风险资产环境构成逆风”或“对原有方向骨架形成支撑”。
   不得把倾向写成应当采取的交易行动。
7. audit_attention_effect 只能是：
   SHIFT_FOCUS / REINFORCE_VIEW / WEAKEN_VIEW /
   BACKGROUND_ONLY / UNDETERMINED。
8. epistemic_status 只能是：
   OBSERVED / SUPPORTED_INFERENCE / HYPOTHESIS / NOT_ASSESSABLE。

【domain 语义规则】
- MACRO：
  将 DXY、US10Y、VOLQ 等子项优先聚合成一个 MACRO 观察。
  除非存在不同的不可比性问题，MACRO 最多占一项 observed_changes。
  区分普通逆风、冲击门 WATCH 和硬阻断；不得仅凭单个宏观字段宣布阻断。
- TMV/TMVF：
  只能解释量价路径对方向骨架的支撑、削弱或中性作用，不能生成交易方向。
- Funding：
  必须根据 field_glossary 判断输入是实际费率还是归一化指标。
  归一化指标不得写成真实资金费率。
  正向上升只能谨慎解释为多头拥挤压力可能升温，不得直接视为下跌因果。
- Skew/P/C：
  P/C 是非负比率，不得描述为正负翻转。
  只能解释保护需求、相对期权需求或尾部风险定价的变化。
- Gamma/GEX：
  只能解释波动放大、钉住或空间约束。
  不得把 Gamma 直接作为方向信号。
  历史兼容指标不得伪装成 USD 名义敞口。
- Conflict/Decision：
  冲突比例上升表示证据分歧扩大；置信下降表示系统证据支持收缩。
  不得自行改写 decision、blocking 或 confidence。
- Quality：
  当字段缺失、单位不明或口径不可比时，优先输出 UNDETERMINED，
  并说明该域不参与本次倾向合成。

【跨因子规则】
cross_factor_assessments 只有在至少两个不同 domain 各有独立 evidence_ref 时才能输出。
必须标记关系类型：
REINFORCING / OFFSETTING / CO_MOVEMENT / CONSTRAINT_INTERACTION。
除非 packet 明确提供因果机制，否则只能写“共同出现”“形成共振/抵消”
或“与……一致”，不得写成确定因果。

【人工审计核验】
operator_checks 输出 2 至 4 项结构化核验任务。
只允许使用“核对、观察、确认、比较、验证”等审计动词。
每项必须包含：
- focus_cn：核对什么；
- why_cn：为什么与当前解释有关；
- strengthens_if_cn：什么包内后续条件会强化解释；
- weakens_if_cn：什么条件会削弱或使解释失效；
- evidence_refs：当前依据。

【摘要与长度】
- transition_summary_cn 最多两句。
- 第一句概括状态路径和主要约束/支撑。
- 第二句说明是否改变人工关注重点及原因。
- observed_changes 输出 3 至 6 项；可用 domain 少于 3 个时按实际输出。
- 有可比非宏观数据时，至少包含两个非宏观 domain。
- 禁止使用“关键变化”“高材料性变化”“值得重点关注的变化”
  或其同义套话代替实际解释。

【中文与安全】
中文文本中不得泄露系统 raw enum；机器枚举字段除外。
P/C、Funding、Gamma 的单位和语义必须与 field_glossary 一致。
输出必须是符合 response schema 的单个 JSON object，不得附加 Markdown 或解释文字。
```

---

## 建议的 schema 调整

### 结论

当前 schema 在“能否稳定渲染”方面基本合格，但在“能否稳定产生高信息量解释”方面不足。建议发布**向后兼容的 `signal_transition_llm_review@1.1.0`**，先只增加字段，不删除旧字段。

### 新旧字段映射

| 当前字段 | v1.1 建议 | 兼容策略 |
|---|---|---|
| `impact_cn` | 保留，重新定义为“包内证据支持的审计含义” | 不改字段名 |
| `actual_impact_cn` | 不建议采用 | “actual”容易暗示已证实因果 |
| `materiality` | 标记 deprecated，不由 LLM 生成 | 旧 sidecar 继续读取，前端不作为主文案 |
| `observed_changes[].domain` | 保留 | 建议使用受控 enum |
| — | `evidence_refs: string[]` | v1.1 新结果必填，旧结果允许缺失 |
| — | `evidence_status` | 新增 |
| — | `directional_role` | 新增，避免只依赖自由文本倾向 |
| — | `audit_attention_effect` | 新增 |
| — | `epistemic_status` | 新增 |
| `cross_factor_interactions: string[]` | 保留，同时新增 `cross_factor_assessments: object[]` | 前端优先读取新字段 |
| `candidate_causal_hypotheses` | deprecated；新增 `candidate_explanations` | v1.1 可双写，v2 删除旧名 |
| `operator_focus: string[]` | 保留；新增 `operator_checks: object[]` | 旧前端继续使用字符串，新前端使用对象 |
| `not_trading_advice` | 保留 | 仅作兼容；真实判定由代码 validator 输出 |
| `language_guard` | 保留 | 增加 runner 计算的 `policy_validation` |
| — | `comparison_assessment` | 新增，记录全局及逐域可比性 |

推荐的 `observed_changes`：

```json
{
  "domain": "FUNDING",
  "evidence_refs": [
    "/core_skeleton/funding/previous",
    "/core_skeleton/funding/current",
    "/field_glossary/funding"
  ],
  "evidence_status": "SUFFICIENT",
  "fact_cn": "归一化 Funding 指标由 0.10 升至 0.18。",
  "impact_cn": "多头拥挤压力有所升温，但在 TMV 与冲突状态未同步恶化时，该变化单独不足以重构总体解释。",
  "directional_role": "RISK_CONSTRAINT",
  "tendency_cn": "对风险状态构成轻度约束，不代表实际资金费率达到 18%。",
  "audit_attention_effect": "REINFORCE_VIEW",
  "epistemic_status": "SUPPORTED_INFERENCE"
}
```

JSON Schema 或 constrained decoding 能提高类型和格式稳定性，但不会自动保证字段内容真实、有证据或具有信息增益。因此必须同时做语义校验。

---

## 关键修改点与理论依据

| 修改点 | 解决的问题 | 理论依据 | 潜在风险 | 验证方法 |
|---|---|---|---|---|
| 每项增加 `evidence_refs` | 复述、编造、不可追溯 | 强制结论回落到结构化证据 | 模型可能给出无效路径 | JSON Pointer 存在性校验 |
| 增加 `evidence_status` | 缺字段时勉强解释 | 先判断可比性，再判断含义 | 输出更保守 | 缺字段和历史兼容 smoke case |
| 增加 `audit_attention_effect` | “幅度是否够大”埋在自由文本中 | 把人工关注变化变成可测试分类 | 模型可能过多选择 SHIFT_FOCUS | 与人工标注混淆矩阵对比 |
| 增加 `epistemic_status` | 观察、解释和假设混写 | 类型化不确定性，减少伪因果 | 前端字段增加 | 检查 HYPOTHESIS 是否同时给出证据缺口 |
| MACRO 聚合且最多一项 | 宏观子字段挤占全部空间 | 降低重复信息和阅读负担 | 可能压缩真正独立的宏观机制 | 特殊场景允许一项宏观状态、一项数据质量异常 |
| 固定 domain 语义规则 | Funding、P/C、Gamma 单位误读 | 将金融语义限制从自由语言变成领域契约 | Prompt 变长 | 可把规则版本化放入 packet |
| `operator_checks` 对象化 | 人工方案过泛或越权 | 核验对象、强化条件、失效条件可单独测试 | 前端稍复杂 | 审计动词白名单和交易动词黑名单 |
| 压缩 summary | 摘要与明细重复 | 把信息密度集中在可追溯条目 | 摘要可能过简 | 字符上限和覆盖断言 |
| 双调用盲审 | 被 decision、blocking、materiality 锚定 | 先形成独立证据解释，再与系统标签对照 | 成本、延迟增加 | 同 packet 单调用/双调用盲测 |

长 packet 中关键证据的位置会影响模型利用率，因此证据规则、比较限制和核心原始字段应放在 Prompt/packet 的显著位置，不应夹在大量系统标签之间。

不建议让模型输出数值型“解释置信度”。LLM 评审的自报置信度可能高于其实际正确率；这里用 `epistemic_status` 和 `evidence_status` 描述证据条件，比要求模型打 0–100 分更稳妥。

---

# Before / After 示例

## 示例 1：MACRO 压力上升

假定 packet 同时显示 US10Y 压力扩大，并且宏观冲击门由 WATCH 进入 BLOCK。

**Before**

> 美债收益率评分从 6 bps 升至 22 bps，被评估为关键变化。

**After**

```json
{
  "domain": "MACRO",
  "evidence_refs": [
    "/core_skeleton/macro/us10y/previous",
    "/core_skeleton/macro/us10y/current",
    "/core_skeleton/macro/shock_gate/previous",
    "/core_skeleton/macro/shock_gate/current"
  ],
  "evidence_status": "SUFFICIENT",
  "fact_cn": "US10Y 变动值由 6 bps 扩大至 22 bps，宏观冲击门由观察状态进入阻断状态。",
  "impact_cn": "利率压力已不只是方向背景，而是与冲击门升级共同形成对风险资产环境的硬约束；即使原有 TMV 骨架未反转，其解释权重也受到压制。",
  "directional_role": "RISK_CONSTRAINT",
  "tendency_cn": "对风险资产状态构成逆风和硬约束，不代表应采取任何交易动作。",
  "audit_attention_effect": "SHIFT_FOCUS",
  "epistemic_status": "SUPPORTED_INFERENCE"
}
```

**为什么更有信息量**

它同时回答了数值变化、门状态、对原有 TMV 解释的影响，以及为什么人工关注重点需要转向宏观约束。它没有把收益率变化单独写成确定因果。

---

## 示例 2：Funding 非宏观维度

**Before**

> Funding 从 0.10 升至 0.18，是高材料性变化。

**After**

```json
{
  "domain": "FUNDING",
  "evidence_refs": [
    "/core_skeleton/funding/previous",
    "/core_skeleton/funding/current",
    "/field_glossary/funding/unit_type",
    "/core_skeleton/conflict/current"
  ],
  "evidence_status": "SUFFICIENT",
  "fact_cn": "归一化 Funding 指标由 0.10 升至 0.18，当前冲突比例未同步扩大。",
  "impact_cn": "多头拥挤约束有所增强，但缺少冲突扩大或量价路径恶化的同步证据，因此目前更接近对既有风险解释的补强，而非独立改变整体判断。",
  "directional_role": "RISK_CONSTRAINT",
  "tendency_cn": "轻度风险约束；该数值是归一化指标，不是 18% 的实际资金费率。",
  "audit_attention_effect": "REINFORCE_VIEW",
  "epistemic_status": "SUPPORTED_INFERENCE"
}
```

**为什么更有信息量**

它避免了单位误读，也没有把 Funding 上升机械等同于价格下跌；同时说明了为什么该变化尚不足以改变人工关注重点。

---

## 示例 3：历史 Gamma 兼容字段

**Before**

> Gamma 转负，市场方向转空。

**After**

```json
{
  "domain": "GAMMA",
  "evidence_refs": [
    "/core_skeleton/gamma/current",
    "/comparison/limitations",
    "/field_glossary/gamma/compatibility"
  ],
  "evidence_status": "NOT_COMPARABLE",
  "fact_cn": "当前卡仅提供历史兼容 Gamma 指标，上一张卡缺少同口径可比值。",
  "impact_cn": "无法判断净 Gamma 名义敞口是增加还是减少，该字段不能参与本次方向倾向合成。",
  "directional_role": "UNDETERMINED",
  "tendency_cn": "不足以判断；Gamma 在此只能作为潜在波动空间约束，不能写成方向信号。",
  "audit_attention_effect": "UNDETERMINED",
  "epistemic_status": "NOT_ASSESSABLE"
}
```

---

# 是否建议 transition 引入两次调用真盲审

## 建议

**建议引入可配置的 `two_call_strict` 模式，并在离线 A/B 验证通过后作为主审计路径。**

它不应直接复用单卡的 `BLIND_THEORETICAL_PACKET` 名称。transition 的第一次调用不是形成“理论主动观点”，而是形成不受系统标签影响的变化链解释。

推荐命名：

```text
TRANSITION_BLIND_EVIDENCE_PACKET
TRANSITION_BLIND_INTERPRETATION
TRANSITION_RECONCILIATION_PACKET
```

## 第一次调用应保留

- identity 中的 symbol、时间、elapsed、episode 连续性；
- comparison quality 和 limitations；
- previous/current 原始数值、单位及字段口径；
- `core_skeleton`；
- `recent_5_trajectory`；
- `baseline_24h`；
- `episode_anchor`；
- field glossary；
- 数据质量和历史兼容信息；
- 去除 `meaning` 后的 `core_transition_display` 标题、前值和当前值。

## 第一次调用应隐藏

- `decision_transition`；
- previous/current decision；
- confidence；
- blocking；
- trade_allowed；
- materiality score；
- `top_material_changes` 的材料性和排序；
- `cross_domain_flags`；
- materializer 已生成的方向性含义；
- `core_transition_display.meaning`；
- 任何系统 reasoning；
- 由系统决策直接派生的 domain 标签。

隐藏这些字段不意味着否认它们，而是避免模型在形成独立变化解释之前先看到答案标签。

## 第二次调用

输入：

```text
TRANSITION_BLIND_INTERPRETATION
+
FULL_SIGNAL_TRANSITION_REVIEW_PACKET
```

第二次调用必须：

1. 把 blind result 作为不可改写的独立记录；
2. 对照 decision、blocking、confidence、flags 和材料性排序；
3. 输出一致、部分一致、存在张力或无法判断；
4. 说明差异来自哪些字段；
5. 生成最终 sidecar；
6. 不得把“不一致”解释成允许修改系统结论。

建议新增：

```json
{
  "blind_review_mode": "two_call_strict",
  "llm_call_count": 2,
  "blind_packet_hash": "...",
  "blind_result_hash": "...",
  "blind_consistency": "ALIGNED",
  "blind_differences_cn": []
}
```

## 单调用与双调用取舍

| 维度 | 单调用 | 两次调用真盲审 |
|---|---|---|
| API 请求 | 1 次 | 2 次 |
| 成本与延迟 | 较低 | 较高；第一次可采用较短输出降低 token 成本 |
| 独立性 | 较弱 | 明显更强 |
| 材料性/decision 锚定 | 难以排除 | 可直接控制 |
| sidecar 复杂度 | 低 | 增加 blind hash、结果和一致性字段 |
| 前端复杂度 | 低 | 可只显示一致性徽标，blind 明细折叠 |
| 输出稳定性 | 单次更简单，但容易复述系统标签 | 两阶段可能出现张力，但张力本身具有审计价值 |
| 失败处理 | 简单 | 需要 fallback 状态 |
| 认知增益 | 依赖 Prompt 服从度 | 可以区分独立读数与系统复核 |

建议失败状态：

```text
two_call_strict
single_call_fallback
blind_only_pending_reconciliation
error
```

第一次失败时可退化为单调用，但必须记录 `single_call_fallback`，不能伪装成真盲审。

---

# 风险与验证方法

## Prompt 与 schema 单测

至少增加以下断言：

1. 所有 `evidence_refs` 必须存在于输入 packet。
2. `fact_cn` 中出现的数值必须能在其 `evidence_refs` 指向的数据中找到。
3. `evidence_status != SUFFICIENT` 时，不允许强方向性表述。
4. P/C 文案不得出现“由正转负”“由负转正”。
5. 归一化 Funding 不得被写成百分比实际费率。
6. 历史兼容 Gamma 不得出现 USD、美元名义敞口等表述。
7. Gamma 不得单独生成方向结论。
8. 有非宏观可比字段时，`observed_changes` 至少覆盖两个非宏观 domain。
9. MACRO 默认最多占一个 `observed_changes`。
10. 每项跨因子结论必须包含至少两个不同 domain 的证据。
11. 中文字段不得出现未映射 raw enum。
12. `operator_checks` 不得出现交易、仓位和执行动词。
13. `transition_summary_cn` 不得超过约定长度。
14. 禁止材料性词后，仍需检查 `impact_cn` 是否包含状态作用对象和审计影响，而不是只做同义替换。

## 前端渲染断言

- 老 sidecar 缺少新增字段时继续正常渲染；
- `NOT_COMPARABLE/MISSING` 不使用利多或利空颜色；
- `impact_cn` 仍作为兼容主字段；
- 未识别 enum 显示“无法判断”，不显示原始英文；
- `operator_checks` 可折叠，避免卡片过长；
- blind 不一致只显示“独立观察存在张力”，不得显示为系统错误或交易警报。

## 真实 LLM smoke case

建议至少覆盖：

- 宏观冲击门升级，但 TMV 未变；
- 宏观压力上升，同时 Funding 拥挤与 Skew 保护需求共振；
- 宏观逆风缓和，但 Conflict 继续扩大；
- Funding 归一化指标变化；
- P/C 从低值升至高值；
- Gamma 历史兼容字段；
- 前卡缺字段；
- 单卡与当前卡跨 episode；
- decision 未变但状态路径明显变化；
- decision 变化但结构化因子变化很弱；
- `cross_domain_flags` 与原始字段存在张力；
- 同一 packet 的单调用和双调用对照。

## 如何判断信息增益确实提高

建议跟踪以下指标：

```text
Evidence Grounding Rate
  有效 evidence_refs 支持的实质性结论占比

Delta Paraphrase Rate
  只描述 previous/current、未给出审计含义的条目占比

Audit Attention Classification Accuracy
  audit_attention_effect 与人工标注的一致率

Unit/Semantic Error Rate
  Funding、P/C、Gamma 单位或语义错误率

Causal Overclaim Rate
  把共同变化写成确定因果的比例

Macro Dominance Ratio
  observed_changes 中 MACRO 条目占比

Operator Check Usefulness
  人工评审认为可直接执行核验的检查项比例

Decision Non-interference Rate
  是否始终不改写系统 decision、confidence、blocking、trade_allowed
```

其中以下应作为硬门：

- JSON schema 合规率 100%；
- 无效证据路径为 0；
- 编造数值为 0；
- 交易执行建议为 0；
- P/C、Funding、Gamma 已知语义错误为 0。

---

# 核心问题直接回答

1. **当前 Prompt 是否足以稳定实现三层输出？**
   不足。能提高出现概率，但没有把证据、可比性和关注影响设为必填。

2. **是否改成 `actual_impact_cn`？**
   不建议。保留 `impact_cn`；前端标题可显示“审计含义”。未来大版本可考虑 `audit_implication_cn`。

3. **是否给每个 domain 固定模板？**
   应给固定语义规则和禁区，不应给固定自然语言句式。

4. **是否压缩 `transition_summary_cn`？**
   应压缩到最多两句，主信息放入 `observed_changes`。

5. **幅度是否足以改变判断是否应独立字段？**
   应增加 `audit_attention_effect`，但名称要明确是改变人工关注，不是改变系统 decision。

6. **如何避免利空/利多变成交易建议？**
   明确作用对象、使用 `directional_role` enum、禁止行动动词，并由代码扫描正文。

7. **如何避免宏观过度主导？**
   聚合宏观子字段、MACRO 默认最多一项、有数据时至少两个非宏观 domain。

8. **历史卡缺字段如何表达？**
   使用 `PARTIAL/NOT_COMPARABLE/MISSING`，并明确该域不参与倾向合成。

9. **当前 schema 是否足够稳定展示？**
   对基础展示足够；对高质量、可测试解释不足。

10. **当前 Prompt 是否足以生成有用的人工审计方案？**
    不足。`operator_focus: string[]` 应升级为带强化和失效条件的 `operator_checks`。

11. **是否引入两次调用真盲审？**
    建议引入可配置 strict mode，并通过 A/B 后作为主路径。

12. **第一次隐藏什么，第二次如何合并？**
    隐藏 decision、confidence、blocking、materiality、排序、flags 和已有含义；第二次保留 blind 结果不变，只做一致性复核。

13. **不引入盲审时如何降低锚定？**
    把系统标签放在 packet 末尾，标记为待复核声明；原始证据在前；增加 evidence binding。但这仍不等同于真盲审。

14. **只能改一处，最值得改哪里？**
    **把 `evidence_refs + evidence_status + audit_attention_effect` 设为每项 `observed_changes` 的必填字段。** 这一处同时提升信息增益、可追溯性、可测试性和缺失数据处理能力。

---

## 最终建议

推荐按以下顺序落地：

1. 发布向后兼容的 Prompt/schema `@1.1.0`，增加证据绑定、可比性和人工关注影响分类。
2. 将材料性、raw enum、单位和执行建议检查移到确定性 post-validator，不能依赖模型自报。
3. 用同一历史语料对现有单调用与新版单调用做基线测试。
4. 再加入 `TRANSITION_BLIND_EVIDENCE_PACKET → TRANSITION_RECONCILIATION_PACKET` 双调用 A/B。
5. 只有当双调用在证据落地率、低复述率、人工方案有效性和非宏观覆盖上显著优于单调用时，再设为默认路径。

当前版本最需要修复的并非“模型不够会写”，而是**schema 没有迫使模型证明每一句解释来自哪里、证据是否可比，以及这项变化究竟只是背景噪声还是足以改变人工审计重点**。
