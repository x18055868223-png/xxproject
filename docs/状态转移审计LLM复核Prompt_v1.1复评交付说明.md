# 状态转移审计 LLM 复核 Prompt v1.1 复评交付说明

> 注：第 1-10 节保留 v1.1 复评交付基线；第 11 节为 2026-06-25 v1.2 本轮优化后的增量声明，当前代码检查以第 11 节版本为准。  
> 交付对象：GPT-5.5 Pro / Opus 4.8  
> 当前实现目标：请基于本说明对 `gemini_signal_transition_review_prompt@1.1.0`、`signal_transition_llm_review@1.1.0`、runner 侧策略校验、前端消费方式和可选两次调用架构继续查缺补漏，提出更优设计。  
> 强边界：LLM 是信号审计旁路与人工决策辅助层，不改变系统 `decision / confidence / blocking / trade_allowed`，不生成交易执行建议，不访问外部行情。

## 1. 当前背景

状态转移审计用于解释两张相邻信号审计卡之间的市场状态路径变化。它不是单卡结论复述，也不是交易执行系统，而是把程序化 materializer 已经计算出的 transition delta、核心骨架、领域变化摘要和比较质量交给 LLM，让 LLM 帮助人工审计者回答：

- 哪些事实变化真正改变了市场状态理解；
- 这些变化对方向骨架、门控、幅度充分性和跨因子关系意味着什么；
- 哪些只是背景波动、兼容字段或不可比口径；
- 人工后续应核验哪些条件，什么情况下当前解释失效。

两份外部评估的共识是：v1.0 的安全边界与 JSON 结构可用，但缺少高信息量推理脚手架，容易出现合规但低价值的 delta 复述、材料性套话、单位误读、被系统标签锚定等问题。v1.1 的目标不是限制外部模型只能给某一种答案，而是把现状、边界、缺陷和已实施改动充分交代，请更强模型继续提出更优 prompt / schema / validator / 调用架构。

## 2. 当前完整调用链

### 2.1 数据生产与 transition ledger

1. FMZ producer 输出原始信号审计卡 `signal_review.jsonl`。producer 只补 transition anchor 元数据，不输出差分结论，不改变交易字段。
2. `tools/materialize_signal_cards.py` 读取原始审计卡，生成：
   - `signal_transition_ledger.jsonl`
   - `signal_transition_state.json`
   - 卡片内 `transition_context`
   - 前端消费的 `signal_cards/*.json`
3. materializer 负责计算 T0 差分账本，包括 `core_skeleton`、`core_transition_display`、`domain_change_summaries`、`top_material_changes`、`comparison_quality`、hash chain 等。
4. materializer 不伪造 LLM 结论；如果存在 sidecar，只按 `transition_id` 合并。

### 2.2 transition LLM 调用

入口：

```bash
python tools/gemini_signal_llm_review.py --mode transition
```

当前路径：

1. `generate_transition_reviews()` 读取 `signal_transition_ledger.jsonl`。
2. 只处理 `llm_review_required=true` 且尚未在 sidecar 中成功写入的 `transition_id`。
3. `build_transition_review_packet()` 构造 `SignalTransitionReviewPacket@1.0.0`。
4. `build_transition_review_prompt()` 生成 `gemini_signal_transition_review_prompt@1.1.0`。
5. Gemini 返回结构化 JSON。
6. `build_transition_llm_review()` 规范化输出，写入 `signal_transition_llm_reviews.jsonl`。
7. materializer 后续按 `transition_id` 把 sidecar 合并到卡片。
8. 前端只消费 materialized JSON，不计算 delta。

当前默认仍是单次调用。v1.1 在单次调用内引入“推理顺序盲”：先要求模型基于 evidence / delta 独立读数，再对照系统结论和材料性字段，降低被 `decision_transition / blocking / materiality_score` 锚定的概率。

### 2.3 单卡 review 的两次调用真盲审

单卡 LLM review 已经有 `two_call_strict`：

1. 第一次只看 `BLIND_THEORETICAL_PACKET`，隐藏系统 `decision / reasoning / conflict / blocking / trade_allowed`，输出 `theoretical_active_view` 与 `gamma_regime_lens`。
2. 第二次读取 `BLIND_REVIEW_RESULT + FULL_AUDIT_PACKET`，检查系统结论、证据账本和门控是否与盲读视角一致。
3. 第一次盲读结果不得被第二次调用重写。
4. sidecar 记录 `blind_review_mode=two_call_strict`、`llm_call_count=2`、`llm_call_routes`、`api_key_route`。

transition review 当前没有默认启用两次调用真盲审。v1.1 只预留实验性设计，不切换服务器默认路径。

## 3. v1.1 已实施改动

### 3.1 Prompt

版本：

- `gemini_signal_transition_review_prompt@1.1.0`

主要新增：

- 明确只能解释程序已计算出的 `transition delta`，不重算字段、权重、置信度、材料性或系统结论。
- 强化推理顺序：先读 `core_skeleton / core_transition_display / domain_change_summaries / field_glossary / comparison_quality / raw delta`，再对照 `decision_transition / blocking / cross_domain_flags / materiality_score`。
- `impact_cn` 必须覆盖四类影响轴：方向骨架关系、门控关系、幅度充分性、跨域关系。
- `observed_changes[]` 增加 evidence 与审计语义字段。
- 强制 domain 规则：MACRO 聚合，Funding 区分真实费率与归一化指标，P/C 禁止符号翻转，Gamma/GEX 只解释空间和波动约束。
- 缺字段、单位不明或不可比时必须走 `PARTIAL / NOT_COMPARABLE / MISSING` 与 `indeterminate` 出口。
- `operator_checks` 结构化输出人工核验方案。
- 禁止 raw enum 泄露到中文主文案。

### 3.2 Schema

版本：

- `signal_transition_llm_review@1.1.0`

向后兼容原则：只增不删。v1.0 字段仍保留，v1.1 新增字段供前端优先使用。

新增字段：

- `observed_changes[].evidence_refs`
- `observed_changes[].evidence_status`
- `observed_changes[].directional_role`
- `observed_changes[].magnitude_verdict`
- `observed_changes[].audit_attention_effect`
- `observed_changes[].epistemic_status`
- `cross_factor_assessments[]`
- `operator_checks[]`
- runner 侧 `policy_validation`
- runner 侧 `blind_review_mode=single_call_reasoning_order`
- runner 侧 `llm_call_count=1`

保留字段：

- `impact_cn`
- `cross_factor_interactions`
- `operator_focus`
- `invalid_if`
- `language_guard`
- `not_trading_advice`

### 3.3 Runner 侧策略校验

新增 `policy_validation`，由代码判断，不信任模型自报。

当前检查：

- `language_guard.no_external_data`
- `language_guard.no_trading_instruction`
- `language_guard.distinguishes_observation_from_causality`
- `not_trading_advice`
- `observed_changes` 是否具备 `fact_cn / impact_cn / tendency_cn`
- 主文案是否泄露 `NEUTRAL / MACRO_BLOCKING / Headwind` 等 raw enum
- 是否出现交易执行词，例如开仓、平仓、仓位、止损、下单等
- 是否出现材料性套话
- 是否出现单位或语义错误，例如：
  - 评分与 bps 混写；
  - 归一化指标写成 USD 名义额；
  - P/C 符号翻转；
  - `-0M` / `0M` 这类错误显示；
- `evidence_refs` 是否能在 `SignalTransitionReviewPacket` 中解析。

策略校验失败不会改变系统信号结论，只作为审计质量标记。

### 3.4 Frontend 消费

前端仍只消费 materialized JSON。

v1.1 新字段存在时：

- `observed_changes` 显示证据状态、方向作用、幅度判断、关注影响、认知性质；
- `cross_factor_assessments` 优先替代旧 `cross_factor_interactions` 展示；
- `operator_checks` 优先替代旧 `operator_focus` 作为人工核验方案；
- `policy_validation` 显示为紧凑策略校验状态；
- 旧 v1.0 sidecar 缺字段时继续 fallback 到原展示。

前端不会展示 raw `evidence_refs / source_ref / field path` 到主视觉区，这些只留在审计元数据或原始 trace。

## 4. 当前 v1.1 Prompt 原文

以下为当前 `build_transition_review_prompt()` 的静态 prompt。运行时末尾会拼接 `SignalTransitionReviewPacket` 的 JSON。

```text
你是信号审计变化链复核员。你只解释程序已经计算出的 transition delta，不得重算字段、权重、置信度、材料性、decision、blocking 或 trade_allowed。
严格边界：不得使用外部行情，不得把相关性等于因果，不得输出交易建议、仓位建议、下单建议、止损止盈、对冲或执行层动作。你的角色是审计旁路认知增强：把分散字段综合为可判断的市场状态路径，给出可追溯的倾向性解释与人工审计关注方案。

推理顺序（防系统结论锚定）：形成每个 domain 的解释时，先仅基于 core_skeleton、core_transition_display 的前后值、domain_change_summaries、field_glossary、comparison_quality 和原始 delta 形成独立读数；写完独立读数后，才参考 decision_transition、blocking 变化、cross_domain_flags 和 materiality_score 做一致性对照。materiality 只用于排序，绝不作为结论。若独立读数与系统 decision_transition 指向不一致，必须在 cross_factor_interactions 或 cross_factor_assessments 中如实记录张力，不得向系统结论靠拢。

impact_cn 必须覆盖的影响轴（缺一即容易成为低信息量复述）：每条 observed_change 必须从以下轴中选择适用项作答，禁止只重述数值：1) 方向骨架关系：支撑还是削弱当前 TMV/TMVF 方向骨架；2) 门控关系：是否跨过/退出冲击门、宏观硬阻断或其他阈值，使背景扰动升级为主动约束或反之；3) 幅度充分性：幅度是否足以改变人工审计关注重点，并写入 magnitude_verdict；4) 跨域关系：是否与其他 domain 共振、冲突、抵消或约束，并指出联合含义。

observed_changes 每项必须包含：domain、fact_cn、impact_cn、tendency_cn、evidence_refs、evidence_status、directional_role、magnitude_verdict、audit_attention_effect、epistemic_status。fact_cn 只写 packet 明示的客观数值、状态或缺失情况，不加入原因、评价和材料性语言。impact_cn 写包内证据支持的审计含义，不是已证实因果。tendency_cn 写市场状态压力方向，例如“风险约束/压制”、“支撑”、“中性/缓和”，不是价格预测或操作方向。

domain 语义规则：MACRO 必须将 DXY、US10Y、VOLQ 等子项聚合为一条，除非是数据质量异常；Funding 必须区分真实 last_rate/last_funding_rate 与 funding_norm 归一化指标，归一化指标不得写成真实资金费率；P/C 是非负比率，禁止写“正负符号翻转”，只能解释保护需求或相对期权需求变化；Gamma/GEX 只解释波动放大、钉住或空间约束，不得直接写成方向信号，历史兼容指标不得伪装成 USD 名义额；字段缺失、单位不明或口径不可比时，evidence_status 写 PARTIAL/NOT_COMPARABLE/MISSING，magnitude_verdict 写 indeterminate，epistemic_status 写 NOT_ASSESSABLE，不得编造影响。

人工审计方案：operator_focus 保留简短中文观察重点；operator_checks 输出 2 至 4 项结构化核验任务，每项包含 focus_cn、why_cn、strengthens_if_cn、weakens_if_cn、evidence_refs。只允许使用核对、观察、确认、比较、验证等审计动词；invalid_if 只能写状态/数据条件，不得写价位、仓位或执行触发器。

中文表达约束：结论句不得直接复用 raw enum；NEUTRAL 写成“中性”，MACRO_BLOCKING 写成“宏观硬阻断”，MACRO_SHOCK_BLOCKING 写成“宏观冲击门阻断”，Headwind 写成“逆风”。禁止使用“评估为关键变化”“被评估为高材料性变化”“材料性变化”或只说“关键/高”这类无实际审计含义的套话。transition_summary_cn 最多两句：第一句概括状态路径和主要约束/支撑，第二句说明是否改变人工关注重点及原因。只输出符合 response schema 的 JSON。

SignalTransitionReviewPacket:
{运行时拼接 packet JSON}
```

## 5. 当前 Response Schema 摘要

核心字段：

```json
{
  "transition_summary_cn": "string",
  "trajectory_state": "DETERIORATING | IMPROVING | MIXED | STABLE | INSUFFICIENT_HISTORY | UNKNOWN",
  "signal_continuity": "CONTINUING | NEUTRALIZED | REVERSING | BLOCKED | UNKNOWN",
  "observed_changes": [
    {
      "domain": "string",
      "fact_cn": "string",
      "impact_cn": "string",
      "tendency_cn": "string",
      "evidence_refs": ["json pointer"],
      "evidence_status": "SUFFICIENT | PARTIAL | NOT_COMPARABLE | MISSING",
      "directional_role": "RISK_CONSTRAINT | SUPPORT | NEUTRAL_OR_EASING | MIXED | UNDETERMINED",
      "magnitude_verdict": "changes_judgment | background_only | indeterminate",
      "audit_attention_effect": "SHIFT_FOCUS | REINFORCE_VIEW | WEAKEN_VIEW | BACKGROUND_ONLY | UNDETERMINED",
      "epistemic_status": "OBSERVED | SUPPORTED_INFERENCE | HYPOTHESIS | NOT_ASSESSABLE"
    }
  ],
  "cross_factor_interactions": ["string"],
  "cross_factor_assessments": [
    {
      "domains": ["string"],
      "relation": "REINFORCING | OFFSETTING | CO_MOVEMENT | CONSTRAINT_INTERACTION",
      "assessment_cn": "string",
      "evidence_refs": ["json pointer"]
    }
  ],
  "candidate_causal_hypotheses": [
    {
      "hypothesis_cn": "string",
      "supporting_fact_ids": ["string"],
      "alternative_explanations_cn": ["string"],
      "confidence": "LOW | MEDIUM | HIGH"
    }
  ],
  "anomaly_assessment": {
    "state": "NORMAL_DELTA | REGIME_SHIFT | DATA_QUALITY_WARNING | INSUFFICIENT_COMPARABILITY",
    "basis_cn": "string"
  },
  "operator_focus": ["string"],
  "operator_checks": [
    {
      "focus_cn": "string",
      "why_cn": "string",
      "strengthens_if_cn": "string",
      "weakens_if_cn": "string",
      "evidence_refs": ["json pointer"]
    }
  ],
  "invalid_if": ["string"],
  "language_guard": {
    "distinguishes_observation_from_causality": true,
    "no_external_data": true,
    "no_trading_instruction": true
  }
}
```

Runner 会额外写入：

```json
{
  "schema_version": "signal_transition_llm_review@1.1.0",
  "prompt_version": "gemini_signal_transition_review_prompt@1.1.0",
  "input_packet_hash": "sha256:...",
  "blind_review_mode": "single_call_reasoning_order",
  "llm_call_count": 1,
  "policy_validation": {
    "passed": true,
    "raw_enum_leaks": [],
    "trading_instruction_terms": [],
    "unit_mislabel_terms": [],
    "invalid_evidence_refs": []
  }
}
```

## 6. 实验性两次调用设计

当前代码预留了实验能力，但生产默认未启用：

- `build_transition_blind_delta_packet(packet)`
- `build_transition_blind_prompt(packet)`
- `transition_blind_response_schema()`
- `build_transition_blind_gemini_request(prompt)`

实验设想：

1. Call 1 使用 `TRANSITION_BLIND_DELTA_PACKET@0.1.0`，隐藏：
   - `decision_transition`
   - `blocking`
   - `materiality_score`
   - `cross_domain_flags`
   - `core_transition_display.meaning_cn`
2. Call 1 只生成独立 delta 读数、倾向、幅度充分性和 evidence refs。
3. Call 2 使用 blind result + full packet 做一致性复核。
4. Call 2 不得重写 blind result，只能标注系统结论与盲读之间的一致、抵触或信息不足。
5. 若启用，应记录：
   - `blind_review_mode=transition_two_call_strict`
   - `llm_call_count=2`
   - `blind_packet_hash`
   - `blind_result_hash`
   - `blind_consistency`
   - `llm_call_routes`

请评估：是否应将两次调用提前为默认架构，还是应先保留 v1.1 单次调用并做 A/B 指标验证。

## 7. 当前已知风险和待评估点

- 单次调用即便有推理顺序盲，模型仍可能被同一个 packet 内的系统标签锚定。
- `impact_cn` 四轴约束可能导致输出变长，前端可读性可能下降。
- `operator_checks` 可能被模型写成“执行触发器”，当前 validator 会拦截交易词，但仍需评估语义漏洞。
- `evidence_refs` 使用 JSON pointer，模型可能输出不可解析路径；当前 validator 会标记失败。
- `policy_validation` 是硬规则启发式，可能误报或漏报。
- Gamma/GEX、Funding、P/C、历史兼容字段的单位语义仍是高风险区域。
- `candidate_causal_hypotheses` 可能诱导伪因果，需要评估是否应改名或弱化。
- 前端现在展示更多结构化信息，需评估是否增加认知负荷。

## 8. 请 GPT-5.5 Pro / Opus 4.8 交付以下内容

请不要只给“更清晰”“更严格”这类主观意见。需要可落地设计、理论依据、风险和验证方法。

### 8.1 Prompt 逐段评估

请逐段评估当前 v1.1 prompt：

- 哪些约束有效；
- 哪些约束会让模型机械合规但低信息量；
- 哪些地方仍会诱发系统标签锚定；
- 哪些 domain 规则过宽或过窄；
- 哪些中文表达要求会损伤真实金融语义。

### 8.2 如果建议优化，请给出完整替代 prompt 或 patch

要求：

- 可以整体重写；
- 可以局部 patch；
- 必须说明替换位置；
- 必须能继续输出当前 schema，或明确提出 schema 改动理由；
- 不得要求 LLM 改系统结论、访问外部行情、生成交易执行建议。

### 8.3 Schema 评估

请评估 v1.1 schema 是否足够支持：

- 信息增益；
- evidence binding；
- 倾向表达；
- 跨因子综合；
- 历史卡兼容；
- 前端可读性；
- validator 可测试性。

如果建议改 schema，请说明：

- 是否向后兼容；
- 前端如何 fallback；
- materializer 是否需要改；
- sidecar 历史数据如何兼容；
- 哪些字段应由模型输出，哪些应由代码派生。

### 8.4 Validator 评估

请评估当前 `policy_validation` 是否应该：

- 仅标记不拦截；
- 失败时写 error sidecar；
- 失败时保留原文但前端降级；
- 对不同错误类型给不同严重度；
- 增加更多单位/语义/交易建议检测；
- 避免误报机器字段、证据路径或 enum 状态。

### 8.5 两次调用真盲审评估

请明确回答：

1. transition 是否应默认引入两次调用真盲审；
2. 如果应引入，Call 1 应隐藏哪些字段；
3. Call 1 输出 schema 应如何设计；
4. Call 2 如何合并 blind result 与 full packet；
5. blind result 是否允许被 Call 2 修改；
6. 成本、延迟、sidecar 复杂度是否值得；
7. A/B 指标怎样定义才足以证明两次调用优于单次 v1.1。

### 8.6 Before / After 示例

请至少给出 2 个示例：

- 一个包含 MACRO 压力变化；
- 一个必须包含 Funding、Gamma/GEX、P/C、TMV 或 conflict 中至少一个非宏观维度；
- 示例要展示当前 prompt 可能输出的问题，以及优化后如何提高信息增益。

### 8.7 理论依据

每条优化建议必须至少关联一种理论依据：

- 信息增益；
- 证据绑定；
- 因果审慎；
- 锚定偏差控制；
- 认知负荷控制；
- 结构化输出稳定性；
- 金融语义精确性；
- 历史数据兼容；
- 人工审计可操作性。

## 9. 不可突破的边界

以下边界不是为了限制方案创新，而是系统安全边界：

- 不改变 `decision / confidence / blocking / trade_allowed`；
- 不输出开仓、平仓、仓位、杠杆、止损止盈、下单、对冲或执行层动作；
- 不使用外部行情、新闻、盘口或私有服务器路径；
- 不要求暴露 API key、token 或账号信息；
- 不把相关性写成已证实因果；
- 不把 LLM 结论写成系统结论；
- 不让 `materiality` 变成主语言或结论来源；
- 不伪造历史卡缺失字段。

## 10. 当前本地验收命令

本轮 v1.1 相关本地检查包括：

```bash
python tests/test_signal_llm_review_pipeline.py
python tests/test_materializer_tail_window.py
python tests/test_signal_audit_frontend_render_contract.py
node --check deploy/signal_audit/frontend/app.js
python -m py_compile tools/gemini_signal_llm_review.py tools/materialize_signal_cards.py
```

请外部 reviewer 如提出优化，也给出相应测试建议，尤其覆盖：

- raw enum 泄露；
- P/C 符号翻转误读；
- Funding 真实费率与归一化指标混淆；
- Gamma 小量级误显示为 `-0M`；
- 无效 evidence refs；
- operator checks 越界成交易建议；
- 原始 delta 中性但系统标签显示 blocking 的锚定回归样本。

---

## 11. 本轮 v1.2 复评优化交付声明（2026-06-25）

本轮基于当前代码、v1.0 / Opus 4.8 外部评估，以及 `docs/llm调用优化/2 round/` 复评意见继续推进。结论是：v1.1 已经是可用中间版本，但仍不能宣称真盲审；剩余主要风险集中在系统标签锚定、模型自写事实与 JSON Pointer 脆弱性、单位/语义误读、伪因果命名以及校验失败后的前端消费策略。

### 11.1 本轮决策

- 生产默认不直接切换为两次调用。默认路径从 `single_call_reasoning_order` 改为更诚实的 `single_call_evidence_first`，表示单次调用内的 evidence-first 顺序约束，而不冒充信息隐藏盲审。
- 两次调用推进为实验路径：新增 `--transition-blind-mode two_call_strict`，用于 shadow / A-B 验证；默认仍为单次调用 control。
- Call 1 的 observed changes 在 runner 中保持不可变；Call 2 只做系统标签对照、张力说明和人工核验方案补充。即使 Call 2 返回新的 `observed_changes`，runner 也会丢弃并保留 Call 1 结果。
- 不触碰 FMZ producer、执行层文件、交易开关、系统 decision/confidence/blocking/trade_allowed。

### 11.2 已落地的代码契约

- `tools/gemini_signal_llm_review.py`
  - `TRANSITION_PACKET_VERSION` 升至 `SignalTransitionReviewPacket@1.1.1`。
  - `TRANSITION_PROMPT_VERSION` 升至 `gemini_signal_transition_review_prompt@1.2.1`。
  - `TRANSITION_OUTPUT_SCHEMA_VERSION` 升至 `signal_transition_llm_review@1.2.1`。
  - 新增 `evidence_catalog` 与稳定 `EV_*` evidence IDs；模型应优先在 `evidence_refs` 中选择 evidence ID，旧 JSON Pointer 仍兼容。
  - prompt 明确区分 `EVIDENCE` 与 `SYSTEM_ASSERTIONS`；`decision_transition/materiality/cross_domain_flags/display meaning` 不得作为 observed change 的唯一证据。
  - runner 侧进一步收紧证据绑定：`observed_changes[]` 必须至少引用一个实质证据；空 `evidence_refs`、`/decision_transition` 等系统断言路径、`/evidence_ref_policy` 等策略说明路径会降级失败；`field_glossary` 只能辅助单位语义，不能单独支撑 observed change。
  - `fact_cn` 在可解析 evidence refs 时由 runner 确定性派生，降低模型数值幻觉和单位误读风险。
  - `observed_changes[]` 新增 `effect_target`，要求说明作用对象，而不是裸写“利空/利多”。
  - `candidate_causal_hypotheses` 不再作为活跃模型输出承载；新增 `candidate_explanations[]`，并固定 `causal_status=UNVERIFIED`。
  - `policy_validation` 新增 `issue_codes`、`severity`、`render_state`、`causal_overclaim_terms`，并增加不可比 evidence 与强方向/强幅度组合的状态矩阵校验。
  - 新增 experimental transition two-call runner path：`transition_two_call_strict`、`llm_call_count=2`、`blind_packet_hash`、`blind_result_hash`、`blind_consistency`、`blind_differences_cn`。

### 11.3 Materializer / Frontend 边界

- `tools/materialize_signal_cards.py` 本轮不新增行为。当前边界仍是只按 `transition_id` 透传 sidecar，不伪造、不补写 LLM 结论。
- `deploy/signal_audit/frontend/app.js` 本轮新增 `policy_validation.render_state=SUPPRESS_LLM_TEXT` 消费路径：保留 LLM 区块、状态和策略校验提示，但隐藏模型正文、observed changes、cross-factor、operator checks、operator focus 和 invalid_if，避免越界文本继续影响人工阅读。
- 前端密度问题已记录为下一阶段 P2：主视觉应继续避免 raw evidence IDs、hash、route、field path；若后续展示 `blind_consistency`，只作为审计降级/张力提示，不作为交易警报。

### 11.4 本轮已验证

```powershell
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe tests\test_signal_llm_review_pipeline.py
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe tests\test_materializer_tail_window.py
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe tests\test_signal_audit_frontend_render_contract.py
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe -m py_compile tools\gemini_signal_llm_review.py tools\materialize_signal_cards.py
node --check deploy\signal_audit\frontend\app.js
```

结果：以上命令均通过。

### 11.5 下一阶段检查建议

- 用真实或代表性 transition 样本做 `single_call_evidence_first` vs `two_call_strict` shadow A-B。
- 指标不要看“更同意系统 decision”，而要看 evidence grounding、label-flip invariance、order-shuffle stability、单位语义错误率、伪因果率、operator check 可执行性、延迟和成本。
- 只有当 two-call 在高价值 transition 上稳定优于单次 evidence-first，并且 p95 延迟/成本可接受时，再考虑把 `two_call_strict` 升为默认路径。

## 12. 第三轮 v1.2.1 自审收口与项目兼容性评估（2026-06-25）

### 12.1 第三轮意见采纳范围

本轮第三轮意见的核心判断是：不要继续堆长 Prompt，而要把证据协议、validator、Call 2 schema、前端消费和项目记忆做工程收口。当前已落地的 v1.2.1 是兼容性收口版本，不是切换生产默认真盲审。

已采纳并落地：

- `evidence_catalog` 增加 `transition_evidence_catalog@1.0.0` 与 `evidence_catalog_hash`，sidecar 同步保存该 hash，便于复现模型当时引用的证据目录。
- `policy_validation` 增加 fact/impact 方向一致性启发式：当确定性 evidence 显示 MACRO 压力上升但模型写成“回落/支撑/缓和”等反向解释时，标记 `fact_impact_direction_conflict` 并降级。
- `policy_validation` 要求 reviewable transition 至少包含一条 `observed_changes[]`；空 finding 输出标记 `missing_observed_changes`，不得作为 OK 正文展示。
- 状态矩阵补上“欠断言”半边：`evidence_status=SUFFICIENT` 却完全写成 `UNDETERMINED / indeterminate / UNDETERMINED` 时，标记 `sufficient_evidence_understated`。
- 增加 domain × effect target 矩阵：`DECISION` 不得作为独立 `observed_changes[]`；Gamma/GEX 不得直接指向 `DIRECTIONAL_SKELETON`，只能解释波动空间、空间约束或数据可靠性。
- `two_call_strict` 的 Call 2 改为 reconciliation-only response schema，并由本地 validator / merge 白名单拒绝 `observed_changes`、`candidate_explanations`、`cross_factor_assessments`、`anomaly_assessment` 等新 finding 字段；Call 1 仍是唯一 observed finding 来源。
- 前端对未知 `policy_validation.render_state` fail-closed：保留状态/策略提示，隐藏 LLM 正文，并提示“复核结果未通过当前客户端校验”。
- legacy transition sidecar 缺少当前 `policy_validation` 时显示“未按当前策略验证”，仍可读旧正文，但不得暗示按 v1.2.1 策略通过。
- `tools/materialize_signal_cards.py` 的 transition review schema baseline 更新到 `signal_transition_llm_review@1.2.1`；materializer 仍只按 `transition_id` 透传 sidecar，不校验、不补写、不伪造 LLM 结论。
- `generate_reviews()` 与 `generate_transition_reviews()` 的 `limit` 现在限制本次尝试处理的目标数量；即使 LLM 调用失败，也不会因为 `written=0` 而继续扫完整个 backlog，避免成本和 fallback 调用失控。

暂不采纳为本轮代码改动、仅作为下一阶段 shadow/A-B 前治理项：

- 删除 `tendency_cn` 输出并完全改为 runner 从 `directional_role × effect_target` 派生。当前为了兼容前端和历史 sidecar，仍持久化 `tendency_cn`，但下一轮可把“模型不输出、runner 派生并持久化”作为破坏性更低的过渡方案。
- 全量 human-facing numeric provenance validator。当前已覆盖 `fact_cn` 确定性派生、单位正则和方向冲突启发式；summary、impact、operator check 中的无证据数字仍应进入 golden corpus。
- finding dependency graph、summary claim 绑定和局部 `DEGRADED` 依赖传播。当前仍是 review 级 `DISPLAY / DEGRADED / SUPPRESS`，只新增未知状态 fail-closed 和严重违规全文抑制。
- coverage、state_significance、raw/normalized blind hash 区分、adaptive two-call 路由和人工 blind A/B 指标面板。

### 12.2 调整后的 LLM 链路

默认 transition 路径仍为：

```text
materialized transition ledger
-> build SignalTransitionReviewPacket@1.1.1
-> single_call_evidence_first
-> runner normalize/derive fact_cn/tendency_cn
-> policy_validation
-> signal_transition_llm_review@1.2.1 sidecar
-> materializer pass-through merge by transition_id
-> frontend render by render_state
```

实验路径为：

```text
Call 1: transition_delta_blind_first, 生成 observed_changes
Call 2: reconciliation-only schema, 本地白名单 validator 拒绝新 finding 字段
runner: 合并时只采纳 Call 2 reconciliation 白名单字段，保留 Call 1 observed_changes / candidate_explanations
```

因此本轮结论仍是：`two_call_strict` 可进入代表性样本 shadow / A-B，不应提升为全量默认。

### 12.3 项目兼容性判断

当前项目对 v1.2.1 transition LLM 链路兼容：

- Runner：可生成 `signal_transition_llm_review@1.2.1`，默认单调用，实验双调用，Call 2 schema / validator / merge 已收窄。
- Materializer：继续透传 `transition_llm_review` dict，保留 `evidence_catalog_hash`、`render_state`、`blind_*` 等新增字段，不改 ledger/hash chain。
- Frontend：兼容 `DISPLAY_LLM_TEXT`、`DEGRADED_LLM_TEXT`、`SUPPRESS_LLM_TEXT`、未知未来 render state 和 legacy unvalidated sidecar。
- 测试：runner、materializer、frontend 契约、Python compile 和 `node --check` 均覆盖本轮兼容面。
- 边界：未触碰 FMZ producer、执行层交易开关、`decision/confidence/blocking/trade_allowed` 生产逻辑；本轮只改 out-of-process LLM sidecar、materializer sidecar baseline、前端消费和文档记忆。

### 12.4 下一阶段检查口径

下一阶段检查不应问“LLM 是否更同意系统标签”，而应拆成：

- fact-selection invariance：翻转 system assertions 后，Call 1 选择的 evidence ID 是否稳定；
- interpretation invariance：翻转 system assertions 后，`impact_cn / directional_role / effect_target` 是否仍稳定；
- inter-field consistency：`fact_cn`、`impact_cn`、`directional_role`、`effect_target`、`tendency_cn` 是否互相矛盾；
- unsupported numeric claim rate：summary、impact、cross-factor、operator checks 是否写出 evidence 未支持的数字或单位；
- render precision：`DEGRADED / SUPPRESS` 是否精准命中问题文本，是否误杀高价值审计内容；
- latency/cost：`two_call_strict` 的 p50/p95 延迟和 Gemini 调用成本是否可接受。

## 13. 第四轮 v1.2.2 真实调用与页面审计收口（2026-06-26）

本轮针对真实 Gemini 调用和本地页面审计暴露的三个问题做最小边界修复：

- LLM 主解释不得混入 `macro_pressure.components.*`、`factor_cross_section.*`、`source_ref`、`primary_fields`、`主要字段`、`来源`、`核心前后值已入包` 等机器溯源文本；证据定位只保留在 `evidence_refs` / metadata。
- transition 综合论证不能只覆盖 MACRO/P/C；当输入存在 TMV、宏观、Funding、Skew、Gamma/GEX、P/C 等核心域时，`observed_changes`、`cross_factor_assessments` 与 `transition_summary_cn` 必须共同覆盖，稳定项可进入综合论证，不伪造成“变化”。
- 核心变化骨架不再显示 `未定`、`关键`、`高` 等分级 badge；LLM 观察项隐藏 `UNKNOWN / UNDETERMINED / indeterminate` 类无信息 chip。

### 13.1 已落地改动

- `tools/gemini_signal_llm_review.py`
  - `TRANSITION_PROMPT_VERSION` 升至 `gemini_signal_transition_review_prompt@1.2.2`。
  - `TRANSITION_OUTPUT_SCHEMA_VERSION` 升至 `signal_transition_llm_review@1.2.2`。
  - evidence catalog 摘要不再把 raw field path 写入可读事实文本。
  - `fact_cn` 优先保留模型给出的干净中文事实；为空或检测到 raw path 泄漏时，才从核心骨架展示安全摘要派生。
  - `policy_validation` 新增 `raw_field_path_leak`、`external_data_claim`、`missing_core_domain_coverage`，并继续保留交易建议、伪因果、单位语义和 effect target 校验。
  - P/C 缺失数据可作为 `DATA_RELIABILITY` 审计对象，不再误判为 domain/effect target 非法。
  - “杠杆拥挤度”等背景描述不再误报为交易建议；只有明确“调整/使用杠杆、开仓、下单、仓位、执行”等动作组合才触发交易越界。
- `tools/materialize_signal_cards.py`
  - transition review baseline 升至 `signal_transition_llm_review@1.2.2`；仍只按 `transition_id` 透传 sidecar，不补造 LLM 判断。
- `deploy/signal_audit/frontend/app.js`
  - 核心骨架移除 materiality/grade badge。
  - LLM meta chip 隐藏无信息状态。
  - 主阅读流对 raw path 污染文本 fail-closed 到安全中文摘要，不改写 sidecar 原文。
  - 跨因子标题只显示中文/常用缩写标签，不追加 `P_C_RATIO` 等 raw enum。
- `deploy/signal_audit/frontend/index.html`
  - 更新静态脚本 query 到 `20260626-transition-v1.2.2`，避免浏览器继续执行缓存旧脚本。

### 13.2 真实调用与本地页面审计结果

本轮用真实 Gemini transition 调用重新生成 sidecar，最终产物位于：

```text
.preview_signal_audit/real_llm_transition_v122_20260626_104207/
```

最终真实 sidecar 结果：

- `schema_version = signal_transition_llm_review@1.2.2`
- `prompt_version = gemini_signal_transition_review_prompt@1.2.2`
- `blind_review_mode = single_call_evidence_first`
- `llm_call_count = 1`
- `policy_validation.passed = true`
- `policy_validation.severity = OK`
- `policy_validation.render_state = DISPLAY_LLM_TEXT`
- `issue_codes = []`
- `raw_field_path_terms = []`
- `external_data_terms = []`
- `causal_overclaim_terms = []`
- `missing_core_domain_coverage = []`
- `materiality_boilerplate_terms = []`

覆盖情况：

- `observed_changes` 覆盖 MACRO、TMV、Funding、Skew、Gamma、P/C。
- `cross_factor_assessments` 覆盖 MACRO 与 TMV 的约束互动。
- 主阅读区未出现 raw path、`source_ref`、字段清单式表达、交易建议、外部新闻/宏观数据猜测或“导致/触发阻断”类确定性因果句。

本地页面审计：

- 服务地址：`http://127.0.0.1:8789/index.html`
- 桌面和移动宽度均无横向溢出。
- `关键变化骨架 / Core transition` 的 `.badge` 数量为 0。
- transition LLM 主阅读区无 `macro_pressure.components`、`factor_cross_section`、`source_ref`、`primary_fields`、`主要字段`、`核心前后值已入包`、`scoring_bps`、`P_C_RATIO`。
- 页面全局仍保留 source-ref/raw trace 区的可追溯字段路径，这是低层溯源区域，不进入 LLM 主解释。
- 截图证据：
  - `.preview_signal_audit/real_llm_transition_v122_20260626_104207/audit_desktop_v122.png`
  - `.preview_signal_audit/real_llm_transition_v122_20260626_104207/audit_mobile_v122.png`

### 13.3 本轮验证命令

```powershell
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe tests\test_signal_llm_review_pipeline.py
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe tests\test_signal_audit_frontend_render_contract.py
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe tests\test_materializer_tail_window.py
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe -m py_compile tools\gemini_signal_llm_review.py tools\materialize_signal_cards.py
node --check deploy\signal_audit\frontend\app.js
```

### 13.4 边界结论

- 本轮不启用 transition 两次调用真盲审为默认路径；`two_call_strict` 继续保持实验 shadow/A-B 路径。
- 未触碰 FMZ producer、执行层交易开关、`decision/confidence/blocking/trade_allowed` 生产逻辑。
- 未 commit、未 push；本地页面仍需用户确认“当前本地页面可推送”后，才进入推送流程。

## 14. 第五轮 r3.3.4 / v1.2.3 数值口径与降级展示收口（2026-06-26）

本轮针对服务器最新真实卡和页面截图暴露的四类问题做小版本修复：

- LLM 事实文本不得把 Funding 原始小数写成科学计数法；资金费率统一按百分比展示，并与模型面板一致。
- LLM 事实文本不得把 Gamma/GEX 的大额 `net_gamma_notional_usd` 写成 `-0.151 USD` 这类小数 USD；大额净 Gamma 必须按 USD 名义额展示，小量级历史兼容指标只能标为兼容指标。
- `DEGRADED_LLM_TEXT` 中非致命文本质量问题与 legacy sidecar 不再整块红色化，改为琥珀提示；`SUPPRESS_LLM_TEXT`、未知 render state、交易建议等 fatal 情况仍红色/隐藏正文。
- “观察到的变化”不再把 `事实；影响；倾向` 拼成一行；前端改为事实、影响、倾向三段结构化展示，“倾向”作为轻微加粗小标签。

### 14.1 已落地改动

- `tools/gemini_signal_llm_review.py`
  - `TRANSITION_PROMPT_VERSION` 升至 `gemini_signal_transition_review_prompt@1.2.3`。
  - `TRANSITION_OUTPUT_SCHEMA_VERSION` 升至 `signal_transition_llm_review@1.2.3`。
  - `fact_cn` 对 Funding/Gamma 增加数值污染识别：科学计数法、Funding raw rate 百分比误读、Gamma 小数 USD 误读会改用 core display / core skeleton 的安全中文事实。
  - `policy_validation.issue_codes` 记录 `scientific_notation_in_human_text`、`numeric_display_mismatch`、`gamma_usd_unit_misread`、`funding_rate_percent_misread` 等 normalization issue，避免静默纠错。
  - `external_data_claim` 不再把包内宏观背景词误判为外部数据；仍拦截外部宏观事件、流动性、政策、新闻、央行、CPI、非农等包外归因。
- `tools/materialize_signal_cards.py`
  - transition review baseline 升至 `signal_transition_llm_review@1.2.3`。
  - Funding display meaning 对低于 `0.01%` 阈值的正资金费率写为“温和多头倾向”，不写“拥挤升温”。
- `deploy/signal_audit/frontend/app.js`
  - `renderTransitionObservedChange` 改为结构化 DOM：事实、影响、倾向分段展示，不再出现 `；倾向：`。
  - legacy / nonfatal degraded 增加 `is-soft-degraded`；suppressed / unknown render state 保持 hard degraded。
  - legacy 提示文案更新为当前 `v1.2.3` 策略校验。
- `deploy/signal_audit/frontend/index.html`
  - 观察项新增分段样式与倾向小标签样式。
  - soft degraded 改为琥珀色，hard degraded 保持红色。
  - 静态脚本 query 更新至 `20260626c-transition-v1.2.3`。
- `tools/server_self_check_signal_stack.sh`
  - `TRANSITION_LLM_REQUIRED=1` 时校验最新 transition sidecar 的 schema、prompt、`policy_validation`、`render_state`、`evidence_catalog_hash` 和 guard，不再只看 `status=OK`。

### 14.2 本地验证结果

本轮真实 Gemini transition 调用与本地页面审计产物位于：

```text
.preview_signal_audit/r334_local_20260626_212316/
```

真实 transition sidecar 结果：

- `schema_version = signal_transition_llm_review@1.2.3`
- `prompt_version = gemini_signal_transition_review_prompt@1.2.3`
- `policy_validation.passed = true`
- `policy_validation.severity = OK`
- `policy_validation.render_state = DISPLAY_LLM_TEXT`
- `issue_codes = []`
- 人读字段未出现科学计数法、小数 USD Gamma、raw field path、`source_ref`、`primary_fields`。

页面审计结果：

- preview URL：`http://127.0.0.1:8794/index.html`
- 桌面宽度：无横向溢出；观察项结构化 DOM 存在；主阅读区无 raw path、科学计数法或小数 USD Gamma。
- 移动宽度 `390px`：无横向溢出；观察项结构化 DOM 存在；主阅读区无 raw path、科学计数法或小数 USD Gamma。
- 截图证据：
  - `.preview_signal_audit/r334_local_20260626_212316/audit_desktop_r334.png`
  - `.preview_signal_audit/r334_local_20260626_212316/audit_mobile_r334.png`

本轮已完成以下本地验证：

```powershell
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe tests\test_signal_llm_review_pipeline.py
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe tests\test_signal_audit_frontend_render_contract.py
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe tests\test_materializer_tail_window.py
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe tests\test_server_bootstrap_assets.py
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe -m py_compile tools\gemini_signal_llm_review.py tools\materialize_signal_cards.py
node --check deploy\signal_audit\frontend\app.js
```

覆盖断言包括：

- Funding `2.999e-05 -> 7.117e-05` 会被替换为百分比口径，不进入主阅读区。
- Funding `0.0071%` 低于 `0.01%` 阈值时表达为“温和多头倾向”，不写“拥挤升温”。
- Gamma `-0.151 USD -> -0.042 USD` 会被替换为 core display 中的 USD 名义额，例如 `-$152M -> -$42M`。
- 独立复核补丁：`GEX` / `GAMMA_GEX` 别名域同样归一到 Gamma/GEX 口径，不能绕过小数 USD 误读校验。
- 包内宏观背景可作为审计解释，外部新闻/政策/CPI 等包外归因仍会触发策略降级。
- 前端观察项 DOM 含事实、影响、倾向三段，不再出现多余分号。
- nonfatal degraded/legacy 走琥珀提示，suppressed/fatal 仍 hard degraded。

### 14.3 服务器更新待执行事项

本地修复尚未 commit/push。进入服务器更新前仍需用户在本地页面确认：

```text
当前本地页面可推送
```

确认后发布 `r3.3.4`，服务器侧需先安装新代码，再备份并过滤最近 transition sidecar 中的旧 `transition_id`，直接运行 `gemini_signal_llm_review.py --mode transition --transition-limit 8` 重算 transition review，最后 materialize 与 self-check。不要默认重算 card-level sidecar，除非另行确认。

### 14.4 边界结论

- 本轮不更新 FMZ `1.5.1` producer。
- 本轮不改变执行层交易开关。
- transition `two_call_strict` 仍不是默认路径。
- materializer 仍只透传 sidecar，不伪造 LLM 判断；展示安全摘要只在 runner normalization 和 frontend presentation 层完成。
