# 状态转移审计 LLM Prompt v1.1 本轮复评意见

> 评估对象：`状态转移审计LLM复核Prompt_v1.1复评交付说明.md`
> 评估范围：`gemini_signal_transition_review_prompt@1.1.0`、`signal_transition_llm_review@1.1.0`、runner 策略校验、前端消费方式及实验性两次调用架构。
> 评估边界：本复评基于交付说明中的调用链、Prompt、Schema 与校验摘要，不等同于对实际 Python/JavaScript 源码的逐行审计。涉及具体实现行为的判断，应再用真实代码和样本验证。

---

## 一句话结论

v1.1 已从“合规但容易复述 delta”提升为“可追溯、可分类、可校验”的合格中间版本，但仍存在三个结构性缺口：**单次调用内的推理顺序并不构成真盲审；模型直接生成 JSON Pointer 与数值事实仍然脆弱；多组语义重复字段可能制造自相矛盾和前端认知负荷。**

本轮最值得优先实施的不是继续加长 Prompt，而是：

1. 用稳定 `evidence_id` 替代模型直接生成 JSON Pointer；
2. 将 `fact_cn`、中文枚举映射和安全声明改为 runner 确定性派生；
3. 把两次调用作为目标架构，但先以 shadow A/B 或自适应路由验证，再切换生产默认；
4. 将 Call 2 限制为“对照与呈现”，由代码保证它不能重写 Call 1 的独立观察。

---

# 1. 总体判断

## 1.1 v1.1 已经解决的主要问题

| 维度 | v1.0 风险 | v1.1 改善 | 本轮判断 |
|---|---|---|---|
| 证据绑定 | 解释难以追溯 | 增加 `evidence_refs` 与证据状态 | 方向正确，但 Pointer 生成方式不够稳 |
| 缺失与不可比 | 容易勉强解释 | 增加 `PARTIAL / NOT_COMPARABLE / MISSING` | 有效，但状态组合规则仍需精化 |
| 倾向表达 | 依赖自由文本 | 增加 `directional_role` | 有效，但缺少“作用对象” |
| 幅度与审计关注 | 埋在 `impact_cn` 中 | 增加 `magnitude_verdict`、`audit_attention_effect` | 有信息增益，但二者定义重叠 |
| 认知性质 | 观察和推断混写 | 增加 `epistemic_status` | 应保留 |
| 跨因子综合 | 字符串叙事为主 | 增加 `cross_factor_assessments` | 应保留并改用 finding/evidence ID |
| 人工核验 | `operator_focus` 过于自由 | 增加结构化 `operator_checks` | 明显改善，但还需限制“触发器化” |
| 安全校验 | 依赖模型自报 | runner 增加 `policy_validation` | 方向正确，需要严重度和消费策略 |
| 锚定控制 | 无显式控制 | 增加单次调用推理顺序 | 只能减弱，不能形成真正独立性 |
| 前端兼容 | 字段少、信息不足 | 新旧字段 fallback | 合理，但主视觉字段过多 |

## 1.2 v1.1 仍未解决的核心问题

### A. “单次调用推理顺序盲”不是真盲审

只要 `decision_transition`、`blocking`、`materiality_score`、`cross_domain_flags`、`core_transition_display.meaning_cn` 等字段仍在同一个上下文中，模型就已经看见了系统答案。要求模型“先不要参考”只能降低显式复述，无法证明独立读数没有被锚定。

此外，当前 Prompt 无法验证模型内部确实先完成了独立判断。输出只有最终 JSON，没有一个不可变的中间产物。因此，该机制更准确的命名应是：

```text
single_call_evidence_first
```

而不是任何包含“blind”含义的名称。

LLM judge 的位置偏差和上下文顺序敏感性已有系统性实验支持；长上下文中关键信息的位置也会显著影响利用率。[R2][R3]

### B. `evidence_refs` 解决了追溯要求，但没有解决引用生成脆弱性

让模型直接输出 JSON Pointer 会引入以下工程风险：

- 数组索引随 packet 结构变化而失效；
- `/`、`~` 转义容易出错；
- 模型可能引用存在但语义不对应的路径；
- 同一事实可能存在多个等价路径，导致跨版本不稳定；
- validator 只能确认“路径存在”，不能确认“结论由该路径支持”。

因此，当前机制只能实现 **pointer validity**，不能自动实现 **claim-evidence support**。

### C. 多个字段在表达同一件事

以下字段存在明显语义重叠：

- `magnitude_verdict=changes_judgment` 与 `audit_attention_effect=SHIFT_FOCUS`；
- `tendency_cn` 与 `directional_role`；
- `operator_focus` 与 `operator_checks[].focus_cn`；
- `invalid_if` 与 `operator_checks[].weakens_if_cn`；
- `cross_factor_interactions` 与 `cross_factor_assessments[].assessment_cn`；
- `language_guard / not_trading_advice` 与 runner 的 `policy_validation`。

若这些字段都由模型独立生成，格式合规并不意味着内容一致。结构化输出可以约束类型和枚举，但不会自动保证语义真实性或字段间一致性。[R1][R4]

---

# 2. 当前 v1.1 Prompt 逐段评估

## 2.1 角色与边界段

### 当前设计的有效之处

- 明确 LLM 是审计旁路，不改变系统字段；
- 禁止外部行情和执行建议；
- 禁止重算权重、置信度与材料性；
- 明确目标是综合解释，而不是逐字段翻译。

这些边界应保留。

### 剩余问题

“只解释程序已经计算出的 transition delta”可能被保守模型理解为：只能改写 delta，不能形成关系性推断。这与后文“跨域综合”存在轻微张力。

### 建议替换文本

将首句替换为：

```text
你只能使用 packet 中已存在的事实、状态、阈值结果和字段语义进行审计综合。允许比较、归纳共同变化、识别支撑/抵消/约束关系，但不得生成 packet 中不存在的数值、状态、阈值结果、外部事实或系统结论，也不得重算权重、置信度、材料性、decision、blocking 或 trade_allowed。
```

理论依据：这一区分把“禁止重算”与“允许综合”分开，可降低模型为避免越界而退化成 delta 复述。

---

## 2.2 “推理顺序防锚定”段

### 当前设计的有效之处

- 明确材料性不是结论；
- 要求独立读数与系统结论不一致时记录张力；
- 把证据字段放在系统标签之前，方向正确。

### 剩余问题

1. 同一次调用中模型已经看到系统标签，无法形成可验证的独立性；
2. `core_transition_display.meaning_cn` 本身就是 materializer 的解释性文本，会提前锚定；
3. `domain_change_summaries` 若包含方向或重要性描述，同样不是纯证据；
4. `top_material_changes` 即使声明“只用于追溯”，其排序仍会形成显著性提示；
5. Prompt 说“写完独立读数后再参考”，但 schema 没有保存不可变的独立读数。

### 本轮结论

这一段应保留为单调用 fallback 的弱控制，但不能将其描述为盲审。真正的独立性必须由 packet 隔离和两次调用实现。

### 单调用兼容补丁

```text
输入字段分为两类：
A. EVIDENCE：原始前后值、确定性 delta、单位、比较质量、字段语义与确定性阈值结果；
B. SYSTEM_ASSERTIONS：decision、confidence、blocking、materiality、cross_domain_flags、展示层 meaning 和系统摘要。

形成 observed_changes 时，SYSTEM_ASSERTIONS 不能作为 evidence_ref，也不能作为事实或倾向的唯一依据；它们只能用于最终一致性对照。若某项结论只能由 SYSTEM_ASSERTIONS 支持，该项必须写为 UNDETERMINED，并在对照区说明“缺少独立证据”。
```

该补丁只能减少显式标签复制，不能替代真盲审。

---

## 2.3 `impact_cn` 四轴段

### 当前设计的有效之处

四轴覆盖了状态转移解释中最有价值的内容：

- 对方向骨架的关系；
- 对门控/阈值的关系；
- 幅度充分性；
- 跨域关系。

### 剩余问题

“每条 observed_change 必须从四轴中选择适用项作答”仍可能导致：

- 每条解释都重复提 TMV、门控、幅度、跨域；
- 为了满足格式，模型制造并不存在的跨域关系；
- `impact_cn` 过长，前端难以快速阅读；
- `magnitude_verdict` 与正文重复；
- 低信息量模型用四段套话机械填充。

长度偏好或丰富表达可能影响 LLM judge 的判断，因此“更长”不应被当作“更好”。[R7]

### 建议

新增机器字段：

```json
"impact_axes": [
  "DIRECTIONAL_SKELETON",
  "GATE_OR_THRESHOLD",
  "STATE_SIGNIFICANCE",
  "CROSS_DOMAIN"
]
```

规则：

- 每项选择 1 至 2 个真正适用的轴；
- `impact_cn` 只写一句核心含义，建议不超过 80 个汉字；
- 跨域含义优先放入 `cross_factor_assessments`，不要在每条单域项中重复；
- 不适用的轴不得硬填。

---

## 2.4 `observed_changes` 字段要求段

### 当前设计的有效之处

`fact_cn / impact_cn / tendency_cn + evidence_status + epistemic_status` 已经构成较完整的审计解释框架。

### 剩余问题一：缺少作用对象

`directional_role=SUPPORT` 或 `RISK_CONSTRAINT` 必须回答“对什么构成支撑或约束”。例如：

- Funding 上升可能对“多头延续”构成约束；
- 同一变化可能对“空头方向骨架”构成支撑；
- Gamma 只影响“波动空间”，不应直接作用于“价格方向”。

没有作用对象时，`directional_role` 容易被误读为价格预测。

### 建议新增

```json
"effect_target": "RISK_ASSET_ENVIRONMENT | CURRENT_DIRECTIONAL_SKELETON | VOLATILITY_SPACE | SIGNAL_COHERENCE | DATA_RELIABILITY"
```

并规定：

- `GAMMA_GEX` 默认只能使用 `VOLATILITY_SPACE`；
- `QUALITY` 只能使用 `DATA_RELIABILITY`；
- `CONFLICT` 优先使用 `SIGNAL_COHERENCE`；
- `MACRO / FUNDING / TMV` 可按证据选择目标；
- `tendency_cn` 必须包含目标，例如“对风险资产环境构成约束”，不能只写“利空”。

### 剩余问题二：事实文本仍由模型生成

即使有 `evidence_refs`，模型仍可能：

- 抄错数值；
- 添加不存在的 `% / bps / USD / M`；
- 把归一化分数当成真实费率；
- 把显示层四舍五入值当作原始值；
- 生成 `-0M`。

### 建议

`fact_cn` 应由 runner 根据证据目录和显示规则确定性生成。模型只负责选择 evidence ID 和输出解释。

兼容方式：

- v1.2 模型响应中可不要求 `fact_cn`；
- runner 生成 `fact_cn` 后写入现有 sidecar 字段；
- 旧前端无需修改；
- 旧 sidecar 继续读取原字段。

### 剩余问题三：证据状态与认知状态组合未定义

当前 Prompt 把缺字段、单位不明或不可比统一要求为：

```text
evidence_status = PARTIAL / NOT_COMPARABLE / MISSING
magnitude_verdict = indeterminate
epistemic_status = NOT_ASSESSABLE
```

这对 `NOT_COMPARABLE / MISSING` 合理，但对 `PARTIAL` 过于严格。部分证据有时仍能支持受限推断。

建议允许的组合：

| evidence_status | 允许的 epistemic_status | 方向结论 |
|---|---|---|
| `SUFFICIENT` | `OBSERVED / SUPPORTED_INFERENCE` | 可输出受控方向作用 |
| `PARTIAL` | `OBSERVED / SUPPORTED_INFERENCE / NOT_ASSESSABLE` | 必须写明限制，不得输出阈值跨越结论 |
| `NOT_COMPARABLE` | `NOT_ASSESSABLE` | `UNDETERMINED` |
| `MISSING` | `NOT_ASSESSABLE` | `UNDETERMINED` |

---

## 2.5 Domain 语义规则段

### MACRO

**有效：** 聚合 DXY、US10Y、VOLQ，能防止宏观子项挤占全部观察位。

**建议补充：**

- 数据质量异常应进入 `QUALITY`，不要以第二条 MACRO 观察展示；
- 宏观门控只有在 packet 明确提供阈值结果或状态时才能写“跨门”；
- 单个宏观字段变化不得自动升级为“硬阻断”；
- `score`、`bps`、原始收益率水平必须由单位契约区分。

### Funding

当前规则只区分真实费率与归一化指标，还不够。至少还需要 packet 提供：

- `unit_kind`：rate / normalized_score / percentile / compatibility_metric；
- `sign_convention`：正值代表谁向谁支付；
- `time_basis`：单周期、日化、年化或未知；
- `allowed_interpretations`：是否允许解释为拥挤代理；
- `comparison_scope`：不同 venue/contract 是否可比。

当 glossary 没有明确拥挤映射时，模型只能描述“指标上升/下降”，不能自动推导多头拥挤。

### P/C

除了“非负比率”外，还应明确：

- numerator/denominator；
- volume 或 open interest；
- tenor/expiry 范围；
- 聚合市场范围。

否则“保护需求上升”仍可能过度解释。P/C 上升只有在字段契约明确时，才能描述为保护性需求增强的代理。

### Gamma/GEX

除单位外，还必须明确：

- dealer sign convention；
- net gamma、spot gamma、GEX 或兼容指标；
- 名义币种和缩放因子；
- 当前值是否可与历史值同口径比较。

建议禁止裸句“Gamma 转负，因此方向转空”。即使 sign 可比，也只能表述为潜在波动放大或钉住约束改变。

### TMV/TMVF

建议补充：

- TMV/TMVF 是方向骨架还是确认因子，应由 glossary 明确；
- 不允许模型从 TMV 的内部评分反推价格目标；
- 若 TMV 与 TMVF 方向不一致，应优先输出 `SIGNAL_COHERENCE=MIXED`，而不是自行合成一个方向。

### Conflict / Decision

`Decision` 不应作为 Call 1 的市场证据 domain。它属于系统断言，只能在 reconciliation 阶段出现。

`Conflict` 可以作为证据，但其作用对象应是 `SIGNAL_COHERENCE`，而不是直接成为价格方向。

---

## 2.6 人工审计方案段

### 当前设计的有效之处

- 使用核对、观察、确认等审计动词；
- 要求强化和削弱条件；
- 明确禁止执行建议。

### 剩余问题

`strengthens_if_cn / weakens_if_cn` 容易被写成伪装的执行触发器，例如：

> 若价格突破某位则强化，可考虑……

此外，`invalid_if` 与 `weakens_if_cn` 重复。

### 建议改动

新增：

```json
"check_type": "DATA_QUALITY | STATE_PERSISTENCE | CROSS_DOMAIN_CONFIRMATION | THRESHOLD_RECHECK | HISTORICAL_COMPATIBILITY"
```

并把字段名改为：

```text
evidence_strengthens_if_cn
evidence_weakens_if_cn
```

Prompt 中增加：

```text
强化/削弱条件必须指向下一张或后续 packet 中可复核的字段状态、可比性或跨域一致性，不得写价格点位、仓位、订单、杠杆、入场/离场或任何执行条件。
```

`invalid_if` 建议由 runner 从 `operator_checks[].evidence_weakens_if_cn` 派生，模型不再重复生成。

---

## 2.7 中文表达段

### 当前设计的有效之处

- 禁止 raw enum 泄露；
- 禁止材料性套话；
- 限制摘要为两句；
- 将“实际影响”与交易建议分离。

### 剩余问题

1. 逐个在 Prompt 中列举 `NEUTRAL / MACRO_BLOCKING / Headwind` 无法覆盖未来 enum；
2. 让模型同时输出 machine enum 和对应中文，容易不一致；
3. “利空/利多”在未指定作用对象时会损伤金融语义精确性；
4. validator 若只按子串拦截“对冲”，会把“保护性对冲需求上升”误报为交易建议。

### 建议

- 中文枚举映射由 runner/frontend 统一完成；
- 模型输出 `directional_role + effect_target`，`tendency_cn` 可由 runner 模板化生成；
- Prompt 禁止裸用“利空/利多”，允许“对风险资产环境构成约束”“对当前方向骨架形成支撑”；
- 安全检测应识别“动作意图 + 执行对象”，而不是仅凭单个金融词。

---

# 3. 建议的 v1.1 兼容 Prompt Patch

以下 patch 可在不立即改成两次调用的前提下，降低 v1.1 的主要风险。

## 3.1 替换首段

```text
你是信号审计变化链复核员。你只能使用 packet 中已存在的事实、状态、阈值结果、比较质量和字段语义进行审计综合。允许比较多个字段、归纳共同变化、识别支撑、抵消、约束与信息不足，但不得生成 packet 中不存在的数值、状态、阈值、外部事实或系统结论；不得重算权重、置信度、材料性、decision、blocking 或 trade_allowed。

你输出的是人工审计解释，不是交易信号。不得输出价格预测、开仓、平仓、仓位、杠杆、止损止盈、对冲实施、下单或交易许可建议。
```

## 3.2 替换推理顺序段

```text
字段分层：
A. 证据字段：结构化前后值、确定性 delta、单位、比较质量、field_glossary 和明确的确定性阈值结果；
B. 系统断言：decision_transition、confidence、blocking、materiality_score、top_material_changes 排序、cross_domain_flags、core_transition_display.meaning_cn 和系统生成摘要。

observed_changes 的事实、方向作用和幅度判断必须由 A 类字段支持。B 类字段不能作为 evidence_ref，也不能成为 observed_change 的唯一依据，只能用于最终一致性对照。若独立证据与系统断言存在张力，应如实记录；若只有系统断言而无独立证据，写为 UNDETERMINED。
```

## 3.3 替换 `impact_cn` 四轴段

```text
每项 observed_change 只选择 1 至 2 个真正适用的影响轴：方向骨架、门控/阈值、状态幅度、跨域关系。禁止为满足格式而制造不受证据支持的跨域关系。impact_cn 只写一句核心审计含义，避免重复 fact_cn，建议不超过 80 个汉字。跨域综合优先写入 cross_factor_assessments。
```

## 3.4 增加字段一致性规则

```text
字段一致性：
- evidence_status 为 MISSING 或 NOT_COMPARABLE 时，directional_role 必须为 UNDETERMINED，magnitude_verdict 必须为 indeterminate，epistemic_status 必须为 NOT_ASSESSABLE。
- Gamma/GEX 的作用对象只能是波动空间或空间约束，不能直接成为价格方向。
- Conflict 的作用对象是信号一致性，不能直接成为价格方向。
- tendency_cn 必须说明作用对象，不得只写“利多/利空”。
- candidate_causal_hypotheses 默认输出空数组；只有 packet 明确提供机制证据时才允许输出，并必须标注未验证及证据缺口，不得输出 HIGH confidence。
```

## 3.5 增加输出长度和覆盖规则

```text
observed_changes 输出 2 至 5 项；若可比较 domain 少于 2 个则按实际输出。MACRO 最多一项，数据质量问题单列 QUALITY。不要为了凑数输出背景噪声。transition_summary_cn 最多两句，每句只概括一层信息。
```

---

# 4. 推荐的 v1.2 输入证据设计

## 4.1 用稳定 Evidence ID 替代模型生成 Pointer

推荐在 packet builder 中增加：

```json
{
  "evidence_catalog": [
    {
      "evidence_id": "EV_FUNDING_NORM_PREV",
      "domain": "FUNDING",
      "source_path": "/core_skeleton/funding/previous",
      "role": "PREVIOUS",
      "raw_value": 0.10,
      "display_cn": "0.10",
      "unit_kind": "NORMALIZED_SCORE",
      "sign_semantics": "higher_means_more_long_crowding_proxy",
      "comparison_status": "COMPARABLE"
    },
    {
      "evidence_id": "EV_FUNDING_NORM_CURR",
      "domain": "FUNDING",
      "source_path": "/core_skeleton/funding/current",
      "role": "CURRENT",
      "raw_value": 0.18,
      "display_cn": "0.18",
      "unit_kind": "NORMALIZED_SCORE",
      "sign_semantics": "higher_means_more_long_crowding_proxy",
      "comparison_status": "COMPARABLE"
    }
  ]
}
```

模型只输出：

```json
"evidence_ids": ["EV_FUNDING_NORM_PREV", "EV_FUNDING_NORM_CURR"]
```

runner 再确定性写入：

```json
"evidence_refs": [
  "/core_skeleton/funding/previous",
  "/core_skeleton/funding/current"
]
```

### 优点

- 模型不需要构造 JSON Pointer；
- Pointer 结构变化不会破坏模型输出协议；
- 可在 evidence catalog 中集中提供单位、符号与可比性；
- validator 可校验 finding 是否引用同一 domain、是否包含前后值；
- 前端和 sidecar 仍能保留原始路径；
- 可为事实文本提供稳定渲染。

证据引用应同时验证“存在性”和“支持性”。来源归属研究通常把 citation validity 与 claim attribution 分开评估；仅有可解析引用并不足以证明结论受到证据支持。[R5]

## 4.2 结构化字段语义契约

建议把自由文本 `field_glossary` 升级为机器可读 `semantic_contracts`：

```json
{
  "semantic_contracts": {
    "funding_norm": {
      "unit_kind": "NORMALIZED_SCORE",
      "sign_convention": "HIGHER_IS_MORE_LONG_CROWDING_PROXY",
      "time_basis": "NOT_APPLICABLE",
      "allowed_claims": ["crowding_pressure_proxy"],
      "forbidden_claims": ["actual_funding_rate", "annualized_rate"]
    },
    "put_call_ratio": {
      "unit_kind": "NON_NEGATIVE_RATIO",
      "numerator": "PUT",
      "denominator": "CALL",
      "basis": "VOLUME",
      "allowed_claims": ["relative_protection_demand_proxy"],
      "forbidden_claims": ["positive_negative_sign_flip"]
    }
  }
}
```

Prompt 不需要长期堆积各字段特例，validator 也能基于契约做确定性检查。

---

# 5. Schema 复评与 v1.2 建议

## 5.1 字段保留、调整与派生建议

| 当前字段 | 建议 | 输出主体 | 原因 |
|---|---|---|---|
| `transition_summary_cn` | 保留 | Call 2 / 单调用模型 | 前端需要，但限制两句 |
| `trajectory_state` | 保留并增加依据 | 模型 | 增加 `basis_finding_ids` |
| `signal_continuity` | 兼容保留，新增 `interpretive_continuity` | 模型/runner | 避免被误认为系统 signal |
| `observed_changes[].domain` | 改为受控 enum | 模型 | 减少拼写和域漂移 |
| `fact_cn` | 保留但由 runner 生成 | runner | 避免数值、单位幻觉 |
| `impact_cn` | 保留 | 模型 | 主解释字段 |
| `tendency_cn` | 保留但优先 runner 派生 | runner | 避免与 enum 冲突 |
| `evidence_refs` | 兼容保留，由 evidence ID 映射 | runner | 稳定引用 |
| `evidence_ids` | 新增 | 模型 | 稳定证据绑定 |
| `evidence_status` | 保留 | 模型 + validator | 可比性表达 |
| `effect_target` | 新增 | 模型 | 明确支撑/约束对象 |
| `directional_role` | 保留 | 模型 | 结构化倾向 |
| `magnitude_verdict` | 重命名或重新定义 | 模型 | 当前与关注影响重叠 |
| `audit_attention_effect` | 保留 | 模型 | 人工审计价值高 |
| `epistemic_status` | 保留 | 模型 | 区分观察与推断 |
| `impact_axes` | 新增 | 模型 | 控制解释范围和长度 |
| `cross_factor_assessments` | 保留，改引用 finding IDs | 模型 | 避免重复证据 |
| `cross_factor_interactions` | deprecated，runner 派生 | runner | 兼容旧前端 |
| `candidate_causal_hypotheses` | deprecated | — | 字段名诱导伪因果 |
| `candidate_explanations` | 新增 | 模型 | 使用非因果框架 |
| `operator_checks` | 保留并增加 `check_type` | 模型 | 提升可测试性 |
| `operator_focus` | runner 从 checks 派生 | runner | 避免重复生成 |
| `invalid_if` | runner 从 checks 派生 | runner | 避免重复生成 |
| `language_guard` | runner 覆盖模型值 | runner | 自我声明不可靠 |
| `not_trading_advice` | runner 派生 | runner | 自我声明不可靠 |
| `policy_validation` | 扩展 | runner | 增加严重度与消费状态 |

## 5.2 重新定义幅度字段

当前：

```text
changes_judgment | background_only | indeterminate
```

“changes_judgment”容易被误解为改变系统 decision，也与 `SHIFT_FOCUS` 重叠。

建议改为：

```text
THRESHOLD_OR_REGIME_CHANGE
MEANINGFUL_WITHIN_REGIME
BACKGROUND_ONLY
INDETERMINATE
```

字段名建议：

```text
state_significance
```

定义：

- `THRESHOLD_OR_REGIME_CHANGE`：证据显示跨过明确阈值或状态类别；
- `MEANINGFUL_WITHIN_REGIME`：未跨状态，但足以强化/削弱当前解释；
- `BACKGROUND_ONLY`：不改变状态类别和主要审计重点；
- `INDETERMINATE`：不可比或证据不足。

它与 `audit_attention_effect` 的区别：

- `state_significance` 描述市场状态变化幅度；
- `audit_attention_effect` 描述人工审计优先级变化。

## 5.3 推荐的 observed finding v1.2

```json
{
  "finding_id": "F2",
  "domain": "FUNDING",
  "evidence_ids": [
    "EV_FUNDING_NORM_PREV",
    "EV_FUNDING_NORM_CURR",
    "EV_FUNDING_CONTRACT"
  ],
  "evidence_status": "SUFFICIENT",
  "effect_target": "CURRENT_DIRECTIONAL_SKELETON",
  "directional_role": "RISK_CONSTRAINT",
  "state_significance": "MEANINGFUL_WITHIN_REGIME",
  "audit_attention_effect": "REINFORCE_VIEW",
  "epistemic_status": "SUPPORTED_INFERENCE",
  "impact_axes": ["DIRECTIONAL_SKELETON", "STATE_SIGNIFICANCE"],
  "impact_cn": "拥挤代理有所增强，但尚无冲突扩大或量价骨架恶化的同步证据，因此目前只构成对延续性的附加约束。"
}
```

runner 生成：

```json
{
  "fact_cn": "归一化 Funding 指标由 0.10 升至 0.18。",
  "tendency_cn": "对当前方向骨架构成附加风险约束。",
  "evidence_refs": ["...", "..."]
}
```

## 5.4 跨因子字段建议

```json
{
  "assessment_id": "X1",
  "finding_ids": ["F1", "F2"],
  "domains": ["MACRO", "FUNDING"],
  "relation": "REINFORCING",
  "effect_target": "RISK_ASSET_ENVIRONMENT",
  "net_role": "RISK_CONSTRAINT",
  "assessment_cn": "宏观门控升级与拥挤约束同步出现，使风险压力不再只是单域背景。"
}
```

runner 根据 `finding_ids` 自动汇总 evidence IDs，避免模型重复列举路径。

## 5.5 候选因果字段建议

不建议继续让模型输出：

```text
candidate_causal_hypotheses + HIGH confidence
```

两张相邻卡的共同变化不足以证明因果，且模型自报置信度可能显著高估实际正确性。[R6]

推荐替换为：

```json
{
  "candidate_explanations": [
    {
      "explanation_cn": "宏观压力与资金拥挤代理共同增强，可能解释为何风险约束扩大。",
      "relation_type": "CONSISTENT_WITH",
      "supporting_finding_ids": ["F1", "F2"],
      "missing_or_counter_evidence_cn": [
        "缺少更长时间序列以判断先后关系",
        "当前 packet 不提供外部机制验证"
      ],
      "causal_status": "UNVERIFIED"
    }
  ]
}
```

默认允许空数组。不要要求模型给出 `HIGH / MEDIUM / LOW` 自信等级。

## 5.6 历史 sidecar 与前端兼容

推荐版本策略：

- 新版本：`signal_transition_llm_review@1.2.0`；
- 不迁移历史 sidecar；
- 前端按 `schema_version` 分支；
- v1.2 优先显示结构化 finding；
- v1.1 继续读取原字段；
- `operator_focus / invalid_if / cross_factor_interactions` 在 v1.2 由 runner 派生后继续提供；
- `evidence_refs` 继续保留，因此原始 trace 页面无需改协议；
- producer 不需要修改；
- evidence catalog 可先在 runner 构造，若要跨工具长期稳定，再下沉到 materializer。

---

# 6. Validator 复评

## 6.1 不建议所有失败都“只标记不拦截”

审计旁路不改变系统信号，不代表所有 LLM 文本都适合继续展示。应区分：

- 系统信号不受影响；
- LLM sidecar 的用户可见性可以降级、局部剔除或拒绝。

推荐引入：

```text
render_state = VALID | DEGRADED | REJECTED
```

## 6.2 严重度与处理矩阵

| 严重度 | 示例 | 建议处理 |
|---|---|---|
| `FATAL` | 非法 JSON、显式交易执行建议、外部行情编造、试图改写系统 decision | 保留原始响应到审计 trace；写 error sidecar；前端不展示 LLM 正文，回退程序化 transition |
| `ERROR` | 无效 evidence ID、编造数值、单位错配、P/C 符号翻转、Gamma 方向化、确定性因果过度声称 | 若错误局部且不污染摘要，剔除该 finding 并标记 DEGRADED；若污染摘要或多项，REJECTED |
| `WARN` | raw enum 泄露、材料性套话、超长、重复 finding、轻微字段不一致 | 可做确定性本地化或隐藏重复项；保留正文并显示质量提示 |
| `INFO` | 可比较 domain 太少、主动省略背景域 | 记录，不影响展示 |

## 6.3 `policy_validation` 建议结构

```json
{
  "render_state": "DEGRADED",
  "passed": false,
  "findings": [
    {
      "code": "UNIT_SEMANTIC_MISMATCH",
      "severity": "ERROR",
      "field_path": "/observed_changes/1/fact_cn",
      "evidence_ids": ["EV_FUNDING_NORM_CURR"],
      "excerpt_cn": "资金费率升至 18%",
      "action": "DROP_FINDING"
    }
  ],
  "dropped_finding_ids": ["F2"],
  "deterministic_repairs": [],
  "validator_version": "transition_policy_validator@1.2.0"
}
```

`passed` 只作为摘要字段，真实消费应依赖 `render_state` 与 findings。

## 6.4 扫描范围必须按字段 allowlist

自然语言检查只扫描用户可见文本，例如：

- `transition_summary_cn`；
- `impact_cn`；
- `assessment_cn`；
- `operator_checks` 文本；
- `candidate_explanations` 文本。

不扫描：

- machine enum；
- evidence ID；
- JSON Pointer；
- hash；
- raw trace；
- `source_path`；
- schema 名称。

否则 raw enum、交易词或路径名容易误报。

## 6.5 交易建议检测不能只做词表匹配

以下文本应允许：

> P/C 上升可作为保护性对冲需求增强的代理。

以下文本应拦截：

> 建议建立对冲仓位。

因此，检测至少要同时考虑：

```text
执行意图词 + 行动对象 + 建议/命令语气
```

建议分层词典：

- 意图：建议、应当、可以考虑、适合、等待后执行；
- 执行动词：买入、卖出、开仓、平仓、加仓、减仓、下单、止损、止盈、建立对冲；
- 执行对象：仓位、杠杆、订单、头寸、价格点位；
- 允许语境：对冲需求、仓位拥挤、订单流指标等描述性名词。

## 6.6 数值和单位验证

建议新增：

1. 从用户可见文本抽取数字和单位；
2. 数字必须能映射到该 finding 选择的 evidence IDs；
3. `unit_kind=NORMALIZED_SCORE` 时禁止 `% / bps / USD / M`；
4. `unit_kind=NON_NEGATIVE_RATIO` 时禁止正负翻转；
5. `unit_kind=COMPATIBILITY_METRIC` 时禁止名义金额措辞；
6. 只在明确为 USD exposure 且缩放后绝对值达到显示阈值时允许 `M`；
7. 零附近值统一由 runner 格式化，禁止模型生成 `-0M`。

## 6.7 字段一致性校验

建议建立组合矩阵，例如：

- `MISSING/NOT_COMPARABLE` 不允许 `SUPPORT/RISK_CONSTRAINT`；
- `GAMMA_GEX + effect_target != VOLATILITY_SPACE` 为 ERROR；
- `QUALITY + effect_target != DATA_RELIABILITY` 为 ERROR；
- `state_significance=BACKGROUND_ONLY` 但 `audit_attention_effect=SHIFT_FOCUS` 为 WARN/ERROR，除非有单独说明；
- `cross_factor_assessments.domains` 至少两个不同 domain；
- 跨因子 assessment 必须引用至少两个 finding；
- `operator_checks` 的 evidence IDs 必须是本次 findings 已使用的证据或明确的待核对字段。

## 6.8 校验失败后的重试

建议最多一次“定向修复重试”，只用于：

- schema 缺字段；
- evidence ID 无效；
- 明确单位错配；
- 字段组合不合法。

修复请求只发送机器可读错误列表，不发送模糊评价。若仍失败，按 DEGRADED/REJECTED 处理。

对显式交易建议、外部数据编造或系统字段改写，不建议自动重试后继续展示；应直接拒绝本次 LLM 正文，以免安全边界被重写掩盖。

---

# 7. 两次调用真盲审评估

## 7.1 是否应默认引入

### 明确结论

- **目标架构：应。** 对进入人工审计的高价值 transition，真正的独立变化读数应通过两次调用与 packet 隔离实现。
- **当前版本立即全量切换：不建议。** 应保留 v1.1 单调用作为 control，先运行 shadow A/B 或自适应路由，达到质量门槛后再把 strict two-call 提升为默认。
- **生产过渡方案：推荐 `adaptive_two_call`。** 高风险/高价值 transition 走两次调用，低风险背景变化走单次或确定性展示。

建议两次调用的触发条件包括：

- decision、confidence 或 blocking 发生变化；
- 系统标签与原始 delta 可能存在张力；
- 多个 domain 同时变化；
- comparison quality 不是完全可比；
- 使用 Funding norm、历史兼容 Gamma/GEX、P/C 等高语义风险字段；
- Call 1 或单调用 validator 出现 evidence/semantic warning；
- 人工主动展开“独立复核”。

## 7.2 Call 1 应隐藏的字段

必须隐藏：

- `decision_transition` 全部字段；
- previous/current decision；
- confidence；
- blocking；
- trade_allowed；
- materiality score；
- `top_material_changes` 的排序、标签与材料性文字；
- `cross_domain_flags`；
- `core_transition_display.meaning_cn`；
- `domain_change_summaries` 中的方向性、重要性和系统解释；
- 任何已有 LLM sidecar；
- `llm_review_required`；
- 系统 reasoning、anomaly label 和展示层结论；
- 由材料性排序决定的字段顺序。

可以保留：

- identity、时间间隔、episode 连续性；
- comparison quality 和 limitations；
- 原始前后值与确定性 delta；
- 单位、符号、可比性、语义契约；
- recent trajectory 的原始值；
- baseline 与 anchor 的原始值；
- 确定性阈值配置与阈值是否跨越，但前提是它不是系统 decision/blocking 的同义标签；
- evidence catalog。

Call 1 的 evidence 顺序应按固定 domain 顺序，而不是按 materiality 排序。A/B 测试中还应加入顺序打乱样本，检查位置敏感性。[R2][R3]

## 7.3 Call 1 推荐输出 schema

Call 1 应紧凑，不生成最终长文，也不生成 operator checks：

```json
{
  "schema_version": "transition_blind_evidence_review@1.0.0",
  "comparison_assessment": {
    "state": "SUFFICIENT | PARTIAL | NOT_COMPARABLE",
    "limitations_cn": ["string"]
  },
  "blind_trajectory_state": "DETERIORATING | IMPROVING | MIXED | STABLE | INSUFFICIENT_HISTORY | UNKNOWN",
  "findings": [
    {
      "finding_id": "F1",
      "domain": "MACRO",
      "evidence_ids": ["EV_..."],
      "evidence_status": "SUFFICIENT",
      "effect_target": "RISK_ASSET_ENVIRONMENT",
      "directional_role": "RISK_CONSTRAINT",
      "state_significance": "THRESHOLD_OR_REGIME_CHANGE",
      "audit_attention_effect": "SHIFT_FOCUS",
      "epistemic_status": "SUPPORTED_INFERENCE",
      "impact_axes": ["GATE_OR_THRESHOLD", "STATE_SIGNIFICANCE"],
      "impact_cn": "string"
    }
  ],
  "cross_factor_assessments": [
    {
      "assessment_id": "X1",
      "finding_ids": ["F1", "F2"],
      "relation": "REINFORCING | OFFSETTING | CO_MOVEMENT | CONSTRAINT_INTERACTION",
      "effect_target": "string",
      "assessment_cn": "string"
    }
  ],
  "coverage": {
    "available_domains": ["string"],
    "assessed_domains": ["string"],
    "omitted_domains": [
      {"domain": "string", "reason": "NO_CHANGE | BACKGROUND_ONLY | MISSING | NOT_COMPARABLE"}
    ]
  }
}
```

## 7.4 Call 1 推荐 Prompt

```text
你是状态转移独立证据复核员。输入只包含证据视图，不包含系统 decision、confidence、blocking、materiality 或展示层结论。

任务：基于 evidence_catalog、比较质量、原始前后值、确定性 delta、单位和语义契约，形成独立的状态变化读数。

只能使用 evidence_id 引用证据。不得构造 JSON Pointer，不得生成 packet 中不存在的数值、单位、状态或外部事实。不得输出交易、仓位、价格点位或执行建议。

每个 finding 必须说明：证据是否可比、作用对象、支撑/约束/中性/混合作用、状态幅度、是否改变人工审计关注、认知性质及一句核心影响。不得参考或猜测系统 decision。

Gamma/GEX 只能作用于波动空间；Conflict 只能作用于信号一致性；Quality 只能作用于数据可靠性。Funding、P/C、Skew 的解释必须服从 semantic_contracts。缺少语义契约时使用 UNDETERMINED。

只输出符合 schema 的 JSON。不要输出最终摘要、系统一致性判断、operator checks 或候选因果故事。
```

## 7.5 Call 2 应如何合并

Call 2 输入：

```text
IMMUTABLE_BLIND_RESULT
+
FULL_SYSTEM_ASSERTION_VIEW
+
EVIDENCE_CATALOG
```

Call 2 只负责：

- 对照 blind findings 与系统 decision、blocking、confidence、flags；
- 标记一致、部分一致、张力或信息不足；
- 生成最终摘要和 operator checks；
- 指出系统标签是否有独立证据支持；
- 不改写 blind findings。

### 最重要的实现要求

**不可变性不能只靠 Prompt。** runner 应：

1. 保存 Call 1 原始结构及 hash；
2. Call 2 响应 schema 不包含可重写 `findings` 的字段；
3. 最终 sidecar 中的 `observed_changes` 由 runner 直接从 Call 1 复制；
4. Call 2 只能通过 `reconciliation` 引用 `finding_id`；
5. 若 Call 2 认为 Call 1 有语义错误，只能输出 `blind_quality_issue`，不能静默修改；
6. validator 决定该 finding 是否进入前端。

## 7.6 Call 2 推荐 schema

```json
{
  "schema_version": "transition_reconciliation@1.0.0",
  "blind_consistency": "ALIGNED | PARTIALLY_ALIGNED | TENSION | NOT_ASSESSABLE",
  "reconciliation": {
    "agreement_points": [
      {
        "finding_ids": ["F1"],
        "system_field_refs": ["/decision_transition/blocking"],
        "assessment_cn": "string"
      }
    ],
    "tension_points": [
      {
        "finding_ids": ["F2"],
        "system_field_refs": ["/cross_domain_flags/0"],
        "assessment_cn": "string"
      }
    ],
    "unsupported_system_assertions": [
      {
        "system_field_ref": "string",
        "reason_cn": "string"
      }
    ]
  },
  "transition_summary_cn": "string",
  "operator_checks": [
    {
      "check_type": "STATE_PERSISTENCE",
      "focus_cn": "string",
      "why_cn": "string",
      "evidence_strengthens_if_cn": "string",
      "evidence_weakens_if_cn": "string",
      "finding_ids": ["F1"]
    }
  ],
  "blind_quality_issues": [
    {
      "finding_id": "F1",
      "issue_code": "string",
      "basis_cn": "string"
    }
  ]
}
```

## 7.7 Call 2 推荐 Prompt

```text
你是状态转移系统一致性复核员。IMMUTABLE_BLIND_RESULT 是第一次调用生成的独立证据读数，必须视为只读记录。你不得改写、删除、合并或重新表述其中的 findings；最终 observed_changes 将由 runner 直接复制第一次结果。

你的任务仅是：
1. 将 blind findings 与 full packet 中的 decision、confidence、blocking、cross_domain_flags 和其他系统断言对照；
2. 标记一致、部分一致、存在张力或信息不足；
3. 指出哪些系统断言缺少独立 finding 支持；
4. 生成最多两句摘要；
5. 生成 2 至 4 项人工审计核验任务。

系统断言不是独立证据。不得因为系统 decision 与 blind finding 不一致而向系统结论改写 blind result。若 blind finding 可能违反单位、语义契约或可比性，只能在 blind_quality_issues 中指出，由 validator 决定是否展示。

不得改变 decision、confidence、blocking 或 trade_allowed；不得输出交易执行建议；不得使用外部数据；不得把共同变化写成已证实因果。

只输出符合 schema 的 JSON。
```

## 7.8 成本、延迟和复杂度是否值得

两次调用的价值不在于“多一遍总结”，而在于产生可验证的独立中间产物。若 Call 1 只输出紧凑 finding，不生成长摘要与 operator checks，token 成本通常可以明显低于复制两次完整 review。

值得与否应由以下条件决定：

- 用户是否真的需要“独立于系统标签”的审计价值；
- transition 调用量是否足以承受额外请求；
- Call 1 是否在标签翻转测试中保持稳定；
- Call 2 是否产生有用张力，而不是只做一致性背书；
- 失败率和 p95 延迟是否仍满足产品预算。

---

# 8. 前端消费建议

v1.1 新字段全部平铺到主视觉会增加认知负荷。建议主卡只展示：

1. 两句摘要；
2. 最多三条主要 finding；
3. 每条只显示两个徽标：
   - 方向作用（已中文化）；
   - 审计关注影响；
4. 一条联合含义；
5. 两项 operator checks。

以下内容放入“证据与限制”折叠区：

- evidence status；
- epistemic status；
- state significance；
- comparison limitations；
- cross-factor 明细；
- policy findings；
- evidence IDs / refs。

### 降级展示

- `VALID`：正常展示；
- `DEGRADED`：只展示通过校验的 findings，并显示“部分解释已因证据或语义校验隐藏”；
- `REJECTED`：只展示程序化 transition、比较限制和错误状态，不展示模型正文；
- 旧 v1.0/v1.1：按现有 fallback。

不要把 blind 与系统不一致渲染成交易警报。建议文案：

> 独立证据读数与系统标签存在张力，需人工核对。

---

# 9. Before / After 示例

以下数值仅用于展示结构，不代表实际行情或系统结论。

## 示例 1：MACRO 压力与门控变化

### 当前 Prompt 可能产生的低价值输出

```text
美债收益率从 6 bps 升至 22 bps，宏观状态被评估为关键变化，并触发宏观硬阻断，整体利空。
```

问题：

- “关键变化”没有信息增益；
- “整体利空”没有作用对象；
- 没说明是普通背景压力还是跨阈值；
- 没说明与 TMV 骨架的关系；
- 若 `6→22` 实际是 score 而不是 bps，会发生单位错误。

### 优化后 Call 1 finding

```json
{
  "finding_id": "F1",
  "domain": "MACRO",
  "evidence_ids": [
    "EV_US10Y_DELTA_PREV",
    "EV_US10Y_DELTA_CURR",
    "EV_MACRO_GATE_PREV",
    "EV_MACRO_GATE_CURR"
  ],
  "evidence_status": "SUFFICIENT",
  "effect_target": "RISK_ASSET_ENVIRONMENT",
  "directional_role": "RISK_CONSTRAINT",
  "state_significance": "THRESHOLD_OR_REGIME_CHANGE",
  "audit_attention_effect": "SHIFT_FOCUS",
  "epistemic_status": "SUPPORTED_INFERENCE",
  "impact_axes": ["GATE_OR_THRESHOLD", "STATE_SIGNIFICANCE"],
  "impact_cn": "利率压力扩大并伴随宏观门控跨入更严格状态，使宏观因素由背景逆风升级为主动约束。"
}
```

runner 生成：

```text
事实：US10Y 变动由 6 bps 扩大至 22 bps，宏观门控由观察状态进入阻断状态。
倾向：对风险资产环境构成主动约束。
```

Call 2 对照：

```json
{
  "blind_consistency": "ALIGNED",
  "reconciliation": {
    "agreement_points": [
      {
        "finding_ids": ["F1"],
        "system_field_refs": ["/decision_transition/blocking/current"],
        "assessment_cn": "独立证据识别到门控升级，与系统阻断状态一致；一致性不改变系统结论。"
      }
    ]
  }
}
```

信息增益：明确了作用对象、阈值性质、审计关注变化以及与系统标签的对照关系，没有把共同变化写成价格因果。

---

## 示例 2：Funding 归一化指标上升

### 当前 Prompt 可能产生的问题输出

```text
Funding 从 0.10 上升到 0.18，资金费率升至 18%，说明多头过度拥挤，后续利空。
```

问题：

- 把归一化指标误写成 18% 实际费率；
- “过度”没有阈值依据；
- “后续利空”成为价格预测；
- 未说明是否有 TMV、Conflict 等同步证据。

### 优化后 finding

```json
{
  "finding_id": "F2",
  "domain": "FUNDING",
  "evidence_ids": [
    "EV_FUNDING_NORM_PREV",
    "EV_FUNDING_NORM_CURR",
    "EV_FUNDING_SEMANTIC_CONTRACT",
    "EV_CONFLICT_CURR"
  ],
  "evidence_status": "SUFFICIENT",
  "effect_target": "CURRENT_DIRECTIONAL_SKELETON",
  "directional_role": "RISK_CONSTRAINT",
  "state_significance": "MEANINGFUL_WITHIN_REGIME",
  "audit_attention_effect": "REINFORCE_VIEW",
  "epistemic_status": "SUPPORTED_INFERENCE",
  "impact_axes": ["DIRECTIONAL_SKELETON", "STATE_SIGNIFICANCE"],
  "impact_cn": "拥挤代理有所增强，但冲突比例未同步扩大，因此目前只构成对方向延续性的附加约束，不足以单独重构总体解释。"
}
```

runner 生成：

```text
事实：归一化 Funding 指标由 0.10 升至 0.18，当前冲突比例未同步扩大。
倾向：对当前方向骨架构成附加风险约束；该指标不是实际资金费率。
```

信息增益：既说明了拥挤代理的有限含义，也指出缺少的确认条件；避免单位误读和价格方向化。

---

## 示例 3：Gamma 历史兼容字段不可比

### 问题输出

```text
Gamma 转负，说明市场方向转空，波动将扩大。
```

### 优化后 finding

```json
{
  "finding_id": "F3",
  "domain": "GAMMA_GEX",
  "evidence_ids": [
    "EV_GAMMA_CURRENT_COMPAT",
    "EV_GAMMA_PREVIOUS_MISSING",
    "EV_GAMMA_SEMANTIC_CONTRACT"
  ],
  "evidence_status": "NOT_COMPARABLE",
  "effect_target": "VOLATILITY_SPACE",
  "directional_role": "UNDETERMINED",
  "state_significance": "INDETERMINATE",
  "audit_attention_effect": "UNDETERMINED",
  "epistemic_status": "NOT_ASSESSABLE",
  "impact_axes": ["STATE_SIGNIFICANCE"],
  "impact_cn": "上一张卡缺少同口径值，无法判断 Gamma 空间约束是增强还是减弱，该域不参与本次方向合成。"
}
```

信息增益：把“不可比较”作为有效结论，而不是勉强输出方向。

---

# 10. A/B 与验收设计

## 10.1 不应只比较“与系统 decision 的一致率”

真盲审的价值是独立性。若把“更同意系统”当成质量指标，会奖励锚定。应重点测量以下指标。

## 10.2 核心指标

### 1. Label-flip invariance

保持原始证据不变，只翻转或替换 `decision / blocking / materiality / cross_domain_flags`：

- Call 1 finding 应基本不变；
- Call 2 只应改变 reconciliation；
- 单调用 v1.1 若 observed_changes 明显随标签改变，说明仍受锚定。

建议作为两次调用上线的首要门槛。

### 2. Order invariance

对 domain 顺序、system assertion 顺序和材料性排序做置换，观察 finding 是否稳定。位置偏差研究表明，该测试不能省略。[R2][R3]

### 3. Evidence binding precision

人工或规则标注每个 claim 的必要证据，检查：

- evidence ID 是否存在；
- 是否属于正确 domain；
- 是否包含前后值或比较限制；
- 引用证据是否足以支持 impact；
- 是否引用了系统断言冒充独立证据。

### 4. Unsupported numeric claim rate

用户可见文本中出现的数值、百分号、bps、USD、M 等，必须能映射到 evidence catalog。

### 5. Domain semantic error rate

单独统计：

- Funding 实际费率/归一化混淆；
- P/C 符号翻转；
- Gamma 名义额与兼容指标混淆；
- Gamma 方向化；
- Conflict 价格方向化；
- score 与 bps 混写。

这些项目在 golden set 中应作为硬门，目标为零错误。

### 6. Delta paraphrase rate

判定 finding 是否只包含：

```text
previous -> current + 泛化词
```

而没有作用对象、状态含义或审计关注影响。

### 7. Human audit utility

由审计人员盲评：

- 是否更快发现真正需要核对的 domain；
- operator check 是否能直接用于下一次审计；
- 是否有多余、重复或误导信息；
- 单调用与双调用哪一个提供了更高增量价值。

### 8. Repetition stability

同一 packet 多次运行，比较：

- finding domain；
- evidence IDs；
- effect target；
- directional role；
- state significance；
- reconciliation 状态。

### 9. 工程指标

- schema valid rate；
- first-pass validator pass rate；
- retry rate；
- DEGRADED/REJECTED 比例；
- 平均与 p95 延迟；
- 输入/输出 token；
- 每条有效 finding 成本。

## 10.3 建议的上线门槛

以下是建议门槛，不是经验事实，应由项目样本校准：

- golden semantic tests：100% 通过；
- 无效 evidence ID：0；
- 显式交易建议：0；
- 外部数据编造：0；
- Call 1 label-flip invariance：至少 95%；
- 人工偏好双调用相对单调用有稳定正提升；
- 双调用增加的 p95 延迟和成本落在既定产品预算内；
- 双调用的 DEGRADED/REJECTED 率不显著高于单调用。

---

# 11. 建议新增的测试

## 11.1 Prompt / 模型 smoke cases

1. 原始 delta 中性，但系统 `blocking=true`；
2. 原始证据不变，system decision 人为翻转；
3. materiality 排序打乱；
4. US10Y score 与 bps 同时存在；
5. Funding norm 为 0.18，实际 rate 缺失；
6. Funding 实际 rate 存在但 time basis 不明；
7. P/C 由 0.7 升至 1.1；
8. P/C numerator/denominator 反向定义；
9. Gamma 为历史兼容指标，上一卡缺失；
10. Gamma 小负值，验证不显示 `-0M`；
11. Conflict 上升但 TMV/Funding 不变；
12. TMV 与 TMVF 相互冲突；
13. 同 episode 与跨 episode；
14. comparison quality 为 PARTIAL；
15. `core_transition_display.meaning_cn` 与 raw evidence 张力；
16. operator check 中出现“对冲需求”但不是执行建议；
17. operator check 中出现“建议建立对冲仓位”，应拦截；
18. summary 合规但某 finding 使用无效 evidence ID；
19. cross-factor 只引用一个 domain；
20. observed finding 引用 decision_transition 作为唯一证据。

## 11.2 Validator 单测

- 只扫描 human-facing field allowlist；
- evidence ID 映射正确；
- Pointer 转义正确；
- unsupported number detection；
- normalized score 禁止 `% / bps / USD / M`；
- ratio 禁止 sign flip；
- gamma target matrix；
- status combination matrix；
- duplicate finding detection；
- summary 受污染时整份拒绝；
- 局部 finding 错误时降级；
- deterministic enum localization；
- `-0M`、`0M`、极小负值格式化。

## 11.3 前端断言

- v1.0/v1.1/v1.2 sidecar 均可渲染；
- 主卡不显示 raw evidence IDs/refs；
- 主卡最多两个徽标；
- `NOT_COMPARABLE/MISSING` 不显示方向色；
- `DEGRADED` 只展示有效 finding；
- `REJECTED` 回退程序化 transition；
- blind tension 不显示为交易警报；
- raw enum 不进入中文主文案；
- operator checks 不渲染价格点位或执行字段；
- 超长 impact 自动折叠，不截断审计含义。

---

# 12. 实施优先级

## P0：下一小版本优先完成

1. 增加 `evidence_catalog + evidence_id`；
2. `fact_cn` 改为 runner 确定性生成；
3. 中文 enum 与 `tendency_cn` 优先 runner 派生；
4. `language_guard / not_trading_advice` 由 runner 覆盖模型值；
5. validator 增加严重度、`render_state` 和字段扫描 allowlist；
6. 禁用 `candidate_causal_hypotheses` 的 HIGH confidence，默认空数组；
7. 为 domain 加 `effect_target`；
8. 明确状态组合矩阵。

## P1：随后实施

1. 实现 Call 1 compact blind schema；
2. 实现 Call 2 reconciliation-only schema；
3. runner 强制 blind result 不可变；
4. 增加 label-flip、order-shuffle A/B；
5. 上线 `adaptive_two_call` shadow 模式；
6. 改名 `single_call_reasoning_order` 为 `single_call_evidence_first`。

## P2：前端与长期治理

1. 主视觉只保留两类徽标；
2. 证据和认知状态折叠；
3. schema/version 兼容测试矩阵；
4. validator 版本化；
5. 建立人工标注 golden corpus；
6. 达到门槛后将 strict two-call 设为高价值 transition 默认路径。

---

# 13. 对交付说明核心问题的直接回答

1. **v1.1 是否已足以稳定产生高信息量解释？**
   比 v1.0 明显更强，但尚不能保证。字段齐全不等于语义不重复，单调用也不能保证独立性。

2. **当前推理顺序盲是否足够？**
   不足。它是 evidence-first 提示，不是真盲审。

3. **是否应该把两次调用立即设为全量默认？**
   不建议立即全量切换；建议先 shadow A/B 和自适应路由。目标默认应是两次调用，尤其是进入人工审计的高价值 transition。

4. **Call 1 应隐藏什么？**
   隐藏全部系统结论、门控结果、置信度、材料性、排序、flags、展示层 meaning、系统摘要与旧 LLM 结果。

5. **Call 1 结果能否被 Call 2 修改？**
   不能。应由 schema 和 runner 从结构上禁止，而不是只写 Prompt 约束。

6. **当前 `evidence_refs` 设计是否足够？**
   不足。应让模型输出稳定 evidence IDs，runner 映射为 refs。

7. **哪些文本应由代码生成？**
   `fact_cn`、enum 中文映射、优先版 `tendency_cn`、兼容字段、语言安全声明和 policy validation。

8. **`magnitude_verdict` 是否保留？**
   建议重命名为 `state_significance`，避免“改变判断”被误解为改变系统 decision。

9. **是否应保留 `audit_attention_effect`？**
   应保留。它是 v1.1 最有价值的新增字段之一，但需与状态幅度严格区分。

10. **是否应保留 `candidate_causal_hypotheses`？**
    不建议。改为 `candidate_explanations`，使用 `CONSISTENT_WITH / CO_MOVEMENT` 等非因果关系，并固定 `causal_status=UNVERIFIED`。

11. **validator 应只标记还是拦截？**
    应按严重度处理。FATAL/严重 ERROR 应阻止 LLM 正文进入前端；局部 ERROR 可降级剔除；WARN 可本地化修复或提示。

12. **前端是否会认知过载？**
    会。五类徽标不应同时进入主视觉；主卡只展示方向作用和审计关注影响，其余折叠。

13. **如果只能改一处，改哪里？**
    **把模型生成 JSON Pointer 和数值事实改为 `evidence_id` 选择 + runner 确定性事实渲染。** 这一处同时降低无效引用、单位误读、数值幻觉和跨版本不稳定。

14. **如果只能改一处架构，改哪里？**
    **让 Call 2 的 schema 不再包含 observed findings，由 runner 直接复制 Call 1 结果。** 这是确保真盲结果不可被系统标签重新锚定的关键。

---

# 14. 最终建议

v1.1 可以继续作为生产 control 和兼容基线，但不建议继续通过增加 Prompt 长度来解决剩余问题。下一阶段应把质量责任重新分配：

- **materializer/runner** 负责证据目录、单位契约、事实渲染、枚举本地化、字段一致性和安全校验；
- **Call 1 LLM** 负责基于纯证据形成紧凑、独立、可追溯的状态解释；
- **Call 2 LLM** 负责系统标签对照、张力说明和人工核验方案；
- **frontend** 负责分层呈现，不把所有机器状态暴露到主视觉；
- **validator** 决定 LLM 文本是否可展示，但永远不改变系统 `decision / confidence / blocking / trade_allowed`。

换言之，v1.2 的核心不应是“让模型更听话”，而应是：**让模型只承担它擅长的关系性解释，把事实、引用、单位、安全和不可变性收回到代码控制。**

---

# 参考依据

- **[R1]** Google AI for Developers, *Structured outputs*，Gemini API 官方文档。说明结构化输出可按支持的 JSON Schema 子集生成可预测、类型安全的结果；这解决结构约束，不等同于语义真实性校验。
- **[R2]** Liu, N. F. et al., *Lost in the Middle: How Language Models Use Long Contexts*, TACL 2024，arXiv:2307.03172。研究显示相关信息在长上下文中的位置会影响模型利用效果。
- **[R3]** Shi, L. et al., *A Systematic Study of Position Bias in LLM-as-a-Judge*, IJCNLP 2025，arXiv:2406.07791。系统研究了 LLM judge 的位置偏差与稳定性问题。
- **[R4]** Geng, S. et al., *Generating Structured Outputs from Language Models*, arXiv:2501.10868。区分约束合规、约束覆盖与生成质量，支持“schema 合规不等于内容正确”的判断。
- **[R5]** *Attribution, Citation, and Quotation: A Survey of Evidence Grounding in LLM-Generated Text*, arXiv:2508.15396。归纳了引用有效性、证据归属与事实支持之间的不同评估维度。
- **[R6]** *Overconfidence in LLM-as-a-Judge: Diagnosis and Confidence-Driven Solution*, arXiv:2508.06225。指出 LLM judge 的自报置信度可能显著高于实际正确率。
- **[R7]** Chen, G. H. et al., *Humans or LLMs as the Judge? A Study on Judgement Bias*, EMNLP 2024；以及相关 length/verbosity bias 研究。支持不要把更长、更丰富的表达直接视为更高质量。
