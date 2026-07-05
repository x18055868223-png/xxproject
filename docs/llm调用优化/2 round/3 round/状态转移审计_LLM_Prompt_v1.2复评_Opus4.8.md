# 状态转移审计 LLM Prompt v1.2 复评结果

> Reviewer：Claude Opus 4.8
> 评估对象：第 11 节 v1.2 增量（`@1.2.0` prompt / schema、`evidence_catalog`/`EV_*`、runner 证据绑定、`policy_validation` 状态矩阵、experimental `two_call_strict`）
> 说明：本轮按第 11 节描述评估；@1.2.0 的 prompt 原文未附（第 4 节仍是 @1.1.0），逐行级评审待原文。范围仍限 prompt/schema/validator/架构，不触执行层。

---

## 一句话结论

v1.2 把上一轮的建议几乎全部落地，且有几处**做得比建议更好**——`evidence_catalog`/`EV_*` 稳定 ID、`fact_cn` 由 runner 确定性派生、EVIDENCE 与 SYSTEM_ASSERTIONS 显式分离、把 `single_call_reasoning_order` 诚实改名为 `single_call_evidence_first`、两次调用降为实验 control、以及 §11.5 那套 label-flip / order-shuffle 指标。剩余火力集中在三点：**(1) 一个已变成"会主动伤害"的旧 bug**——enum 误报现在可经 `render_state` 抑制合法正文；**(2) 一个 v1.2 自己新引入的矛盾面**——runner 派生 `fact_cn` 但不派生 `impact_cn`，使二者可方向背离；**(3) `effect_target` 是对的新增，但它正好让你可以彻底删掉 `tendency_cn` 这个一直冗余的字段**——若不顺手做这步合并，倾向会被 `directional_role + effect_target + tendency_cn` 三重编码。

---

## 一、确认已落地且属于承重结构（无需再动）

简短确认这几项是对的、可作为后续基线，把注意力让给残余项：

- **`evidence_catalog` + `EV_*` ID**：正是"符号引用优于裸 JSON Pointer"的正解——把脆弱的指针构造从模型（不可靠）移到 materializer（确定）。
- **`fact_cn` runner 派生**：直接掐死数值幻觉与单位误读，比"让模型自己标注 norm vs 真实"更彻底（前提是 catalog 携带正确单位标签）。但它引入一个新矛盾面，见 §二·1。
- **EVIDENCE / SYSTEM_ASSERTIONS 分离 + "observed change 必须引用实质证据、系统断言路径降级失败"**：把抗锚定下沉到证据接地层并由代码强制，强于纯 prompt 劝告。这条很可能已经替你消化掉大部分单调用锚定，对两次调用的判断有直接影响（见 §四）。
- **`single_call_evidence_first` 改名**：诚实，回应了上轮"单调用里是声明式独立、非结构盲读、且不可验证"的批评。
- **两次调用降为实验 control + Call 1 observed_changes 不可变 + Call 2 丢弃其新 observed_changes**：与"Call 1 对 observed_changes 权威"的澄清一致。
- **mark-not-block + `severity`/`render_state` + `causal_overclaim_terms` + 状态矩阵校验**：方向对，但 render 粒度过钝、状态矩阵只做了半边，见 §二·2/3 与 §三·2。
- **§11.5 指标套件**（evidence grounding、label-flip invariance、order-shuffle stability、单位错误率、伪因果率、operator 可执行性、延迟成本）：这是本项目最成熟的一处设计，label-flip invariance 正是锚定的正确操作化。补充见 §四。

---

## 二、v1.2 自身引入的二阶问题（本轮重点）

### 1. runner 派生 `fact_cn` 但不派生 `impact_cn` → 二者可方向背离（新，重要）

派生 `fact_cn` 是净收益，但它打破了一个旧的隐性保障：**v1.1 里 fact_cn 与 impact_cn 都出自模型，所以一起对或一起错（内部自洽）。v1.2 把 fact_cn 用 catalog 纠正了、impact_cn 仍是模型读数**——一旦模型把变化方向读反（把 US10Y 升读成降），就会出现**同一条 observed_change 内 fact_cn（正确）与 impact_cn（反向）相互矛盾**的输出。对强模型这不频繁，但后果比从前更糟：从前是"一致地错"，现在是"自相矛盾"，人工审计读起来更困惑，且**当前没有任何检查能抓**（状态矩阵查的是 evidence_status×方向/幅度，不查 fact 方向×impact 方向）。

修法（二选一）：
- 让模型额外输出一个结构化 `observed_delta_sign`（up/down/flat），runner 拿它与 catalog 派生的符号比对，不一致即标记并 REDACT 该条；或
- runner 派生 fact_cn 时把"方向词"也注入，validator 用关键词比对 impact_cn 的方向断言与 catalog 符号是否冲突。
理论依据：结构化输出稳定性 + 金融语义精确性（方向是 0 容错项）。

### 2. `render_state` × enum 误报 = 自伤式内容抑制（旧 bug，严重度已升级，建议最先修）

上轮已指出：若 `raw_enum_leaks` 在整包或全字符串上 grep "NEUTRAL"，会对合法的 `directional_role=NEUTRAL_OR_EASING` 与 `signal_continuity=NEUTRALIZED` **必然误报**。§11 未确认把检测**限定到 `*_cn` 散文字段并排除枚举值字段**。

关键变化：v1.1 时这个误报只是外观噪声；**v1.2 里 `policy_validation` 会驱动 `render_state`，而 `render_state=SUPPRESS_LLM_TEXT` 会隐藏整段正文/observed_changes/cross-factor/operator_checks**。也就是说，**一条本来完全合规、只是 directional_role 取了 NEUTRAL_OR_EASING 的复核，可能因为这个误报被整段抑制**——validator 的假阳性现在会主动删除合法审计内容，直接违反系统"真实可见的审计"这一核心目的。

请先确认并修复：enum/交易词/单位检测只扫 `*_cn` 值，显式排除 `directional_role / signal_continuity / evidence_status / epistemic_status / relation / *_status` 等枚举字段与 `evidence_refs`。零风险、必修、且现在是会致害的。

### 3. `SUPPRESS_LLM_TEXT` 粒度过钝（单点问题灭全文）

`render_state=SUPPRESS_LLM_TEXT` 把 observed_changes / cross-factor / operator_checks / operator_focus / invalid_if 一并隐藏。于是**一条 operator_check 里夹了一个价位触发器，就会让整张复核的全部好内容一起消失**——人工损失了所有正确的 observed_change，只因为一个字段越界。

修法：把 render 分级以匹配 severity 粒度——新增 `REDACT_FIELD`（只隐藏越界的那个数组元素/字段），`SUPPRESS_LLM_TEXT` 仅保留给"违规弥漫"的情形（如 summary 本身含交易指令，或多条 HIGH）。理论依据：认知负荷/人工可操作性（保住可用信息）。

### 4. `effect_target` 是对的——正好用它删掉 `tendency_cn`（否则三重编码）

`effect_target`（作用对象，替代裸写"利空/利多"）方向正确：它把"压制"细化成"压制**什么**"。但要落两件事，否则它变成第三个倾向编码：
- **定义受控词表**（枚举，如 `DIRECTIONAL_SKELETON / GATING_STATE / VOLATILITY_SPACE / CROWDING_RISK / SIGNAL_CONFIDENCE`），否则自由文本又是一个漂移面、不可测。
- **明确它是"对象"而非"关系/倾向"**，并据此**把 `tendency_cn` 降为代码派生**：`directional_role`（倾向）× `effect_target`（对象）已经唯一确定展示串（如 RISK_CONSTRAINT×DIRECTIONAL_SKELETON →"对方向骨架构成风险约束"）。模型不再输出 `tendency_cn`，三重编码（tendency_cn / directional_role / effect_target）收敛为一对干净的结构化字段 + 一个派生展示串。

这其实是把上一轮的"消冗余"建议落地的最优时机：**effect_target 正是让 tendency_cn 可以安全删除的那块拼图。**

### 5. `candidate_explanations` 是否保留**强制 alternatives**？（去偏器不能丢）

`candidate_causal_hypotheses→candidate_explanations` + `causal_status=UNVERIFIED` 是对的改名与降权。但要确认**`alternative_explanations_cn`（≥1 必填）被保留**。原结构最有价值的反伪因果机制是"强制写出替代解释"——那是真正的认知去偏器；`UNVERIFIED` 只是一个**标签/免责声明**，不构成去偏。**标签 + 强制替代解释 两者并存才有效**；若改名时把强制 alternatives 丢了，等于保留了免责声明却卸掉了刹车。理论依据：因果审慎。

### 6. `EV_*` ID 的稳定性与覆盖率（catalog 现在是正确性依赖）

把证据接到 catalog 后，catalog 的两个属性变成硬依赖：
- **覆盖率**：每个 `core_skeleton` 的 delta 字段都必须有对应 `EV_*`。否则模型遇到无目录项的真实变化时，只能回退裸指针（你正想淘汰的脆弱路径）、或漏报（信息损失）、或张冠李戴 EV（误绑）。建议 materializer 保证"每个 delta 一个 EV"，并加测试断言覆盖率。
- **跨版本稳定性**：EV_id 必须**内容派生**（如 `EV_<domain>_<field>`），不能是随字段增减而漂移的序号（`EV_001`…）。否则历史 sidecar 的 `evidence_refs` 在字段集变化后失效——这正是你淘汰位置型 Pointer 的同一个理由，现在原样适用于 catalog 内部。

### 7. evidence 可解析 ≠ 相关（残余、需人工抽检）

runner 校验 `evidence_refs` 能**解析**，但不校验它对该 `impact_cn` **相关**。模型可以引一个能解析却无关的 EV（impact 谈 funding 拥挤却引 `EV_macro_us10y`）。可解析性 ≠ 相关性，这层语义相关只能由 LLM-judge 或人工抽检覆盖。建议 A/B/质量评估里加一个"证据-影响相关性"人工抽样评分项，作为已知限制记录。

---

## 三、仍未处理的 v1.1 残余

### 1. `tendency_cn ↔ directional_role` 冗余仍是最高剩余结构杠杆

§5 schema 未变，`tendency_cn`、`directional_role`、`audit_attention_effect`（仍含 REINFORCE_VIEW/WEAKEN_VIEW）并存，"削弱/支撑骨架"仍可能在 impact_cn轴1 + directional_role + audit_attention_effect 三处重复。借 §二·4 的 effect_target 一并解决：`directional_role`×`effect_target` 为 canonical，`tendency_cn` 派生删除，`audit_attention_effect` 收窄为 `SHIFT_FOCUS / BACKGROUND_ONLY / UNDETERMINED`（方向作用全归 directional_role）。这是 v1.2 之后信息密度与稳定性的最高杠杆点，且零破坏兼容。

### 2. 状态矩阵只做了"过度断言"半边，缺"避险欠断言"半边

§11.2 的状态矩阵查的是"不可比 evidence + 强方向/强幅度"（overclaim）。上轮我标的是**双向**：还应查 **`evidence_status=SUFFICIENT` 却 `directional_role=UNDETERMINED` / `magnitude_verdict=indeterminate`**（avoidance hedge / 欠断言）。v1.2 的丰富枚举 + 多个出口，让"全填 UNDETERMINED 也合法过检"成为低努力路径；只查 overclaim 抓不到这种欠断言。补上对称检查：充分证据下退化为未定 → 标记复核。

### 3. `trajectory_state` / `signal_continuity`：模型输出还是代码派生（minor，仍开放）

二者接近系统级判断。若模型独立铸造且与系统 `decision_transition` 背离，需明确这是"独立读数 + 背离写入 cross_factor_assessments"，而非静默两个真相源。低优先，但建议在某轮明确归属。

---

## 四、两次调用与 A/B 指标的细化（§8.5）

**默认仍不切两次调用——这次理由更强了。** v1.2 的证据接地强制（observed change 必须引实质证据、系统断言路径降级）很可能**已经把单调用的锚定率压得更低**：模型的 observed_changes 现在锚在 EV 证据上、而非系统标签上。所以两次调用相对单调用 evidence-first 的**边际收益进一步缩小**。我的预测：A/B 大概率显示"单调用 evidence-first 已够"，除非**解释层**（impact_cn/directional_role 的诠释，而非事实选择）仍被 `decision_transition=BLOCKING` 带偏。

因此对 label-flip invariance 做**两层拆分**测量：
- **fact-selection invariance**：翻转系统标签后，引用的 EV 集是否不变——这层很可能已被证据绑定保护，预期高不变性。
- **interpretation invariance**：翻转系统标签后，impact_cn/directional_role 是否不变——**这才是两次调用真正可能帮的层**。若这层在单调用下仍随系统标签翻转，才构成切两次调用的证据。

**补一个 §11.5 没列的指标：inter-field consistency rate**（fact 方向 vs impact 方向、directional_role vs tendency_cn、evidence_status×方向/幅度矩阵）。它直接量化 §二·1 的新矛盾面与 §三 的冗余风险，且可自动测。

**auditability 仍是独立一轴**：两次调用产出可哈希的 `blind_result_hash` + `blind_differences_cn`（系统框定与盲读的背离记录，那是产品而非 bug）。即便锚定指标处在边界，对"身份即可追溯"的系统，可证明的独立性可单独构成切换理由——与指标分开权衡，§11.3"blind_consistency 只作审计降级提示、不作交易警报"已正确预置。

---

## 五、测试补充（针对 v1.2 新风险）

在你 §10/§11.5 基础上补：

- **fact↔impact 方向一致性**：构造模型读反方向的样本，断言 runner 标记 fact/impact 方向冲突并 REDACT 该条（守 §二·1）。
- **enum 误报负样本**：含 `directional_role=NEUTRAL_OR_EASING` 与 `signal_continuity=NEUTRALIZED` 的合法输出，断言**既不触发 raw_enum_leaks、也不触发任何 render_state 抑制**（守 §二·2，防自伤式抑制回归）。
- **render 粒度**：单条 operator_check 含价位触发器的样本，断言只 REDACT 该条、保留其余 observed_changes（守 §二·3，若采纳 REDACT_FIELD）。
- **effect_target 词表一致性**：断言 effect_target 取值落在枚举内；断言派生的 tendency_cn 与 directional_role×effect_target 严格对应（守 §二·4/§三·1）。
- **candidate_explanations 强制 alternatives**：断言每条至少一个 `alternative_explanations_cn`（守 §二·5）。
- **EV catalog 覆盖率与稳定性**：断言每个 core_skeleton delta 有 EV_id；同一逻辑字段在不同 run 的 EV_id 不变（守 §二·6）。
- **避险欠断言**：SUFFICIENT 证据 + UNDETERMINED 方向/indeterminate 幅度 → 应被状态矩阵标记（守 §三·2）。
- **interpretation invariance 脚本**：label-flip 下 impact_cn/directional_role 的变动率（§四的两层拆分）。
- **证据相关性人工抽检**：抽样人评 evidence_refs 对 impact_cn 的相关性（§二·7）。

---

## 六、最终建议（落地顺序，最小手术优先）

1. **修 enum 误报 × render_state 自伤式抑制**——零风险、必修、当前会致害（§二·2）。**若只能改一处，改这个**：validator 假阳性抑制合法审计内容，直接违背系统核心目的，且修复成本为零。
2. **加 fact↔impact 方向一致性检查**——这是本轮最重要的**新增**洞察，是 v1.2 自己引入的矛盾面、目前无人能抓（§二·1）。
3. **借 effect_target 删 tendency_cn + 收窄 audit_attention_effect**——一次性解决 v1.1 遗留的三重编码，是 v1.2 之后的最高结构杠杆（§二·4 + §三·1）。
4. **状态矩阵补对称的"避险欠断言"检查**；确认 candidate_explanations 保留强制 alternatives（§三·2、§二·5）。
5. **EV catalog 覆盖率 + 内容派生 ID 稳定性测试**（§二·6）。
6. **跑 evidence-first vs two_call_strict 的 A/B，但按两层拆分看 interpretation invariance**；据此 + auditability 权衡是否升默认（§四）。

整体判断：系统已从"安全但低信息"成熟到"证据接地、可追溯、可降级"的可用中间版本。剩下的不是大改，而是几处**一致性兜底**（fact↔impact、避险欠断言、enum 扫描域）和**一处结构收敛**（借 effect_target 消 tendency 冗余）。把这几处做完，单调用 evidence-first 很可能就是稳定的生产形态，两次调用留作可证明独立性的审计增强选项即可。
