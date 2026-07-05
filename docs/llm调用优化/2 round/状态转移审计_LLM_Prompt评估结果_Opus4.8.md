# 状态转移审计 LLM Prompt 评估结果

> Reviewer：Claude Opus 4.8
> 评估对象：`gemini_signal_transition_review_prompt@1.0.0` / `signal_transition_llm_review@1.0.0`
> 评估范围：仅 prompt、schema 与审计解释方法，不触及 FMZ producer、执行层、交易开关、外部行情。

---

## 一句话结论

当前 prompt 已经把"边界安全"和"格式合规"做对了，但还没有把"信息密度"做出来——它给了模型一张**检查清单**（产出 fact/impact/tendency 三层），却没有给模型一个**推理脚手架**（impact 到底要回答哪几个问题）。结果是模型会"合规地复述"：三层都填了，但 `impact_cn` 经常退化为"事实 + 一个形容词"。最该改的一处不是架构、也不是字段改名，而是**给 `impact_cn` 定义四条强制影响轴**，这能直接打掉你列出的 #1（复述 delta）和 #2（材料性套话）两个头号问题。

---

## 当前 Prompt 有效部分（逐段确认）

第一段（角色 + 硬边界）：**有效且应保留**。"只解释已算出的 delta、不重算、不用外部行情、不输出执行建议"这组约束清晰、可测试、互不冲突。锚定优先级（core_transition_display > core_skeleton/domain_change_summaries，top_material_changes 仅作 trace）方向正确。

第二段中以下几项**有效**：
- enum→中文映射表是**具体且可断言**的（NEUTRAL→中性等），可以直接写成单测。
- 三层拆分（fact_cn / impact_cn / tendency_cn）是强结构装置，值得保留。
- P/C 非负比率、禁止"正负符号翻转"——**精确**，是真实金融语义约束。
- 显式列举禁用套话（"评估为关键变化"等）——**有效**，给了测试一个可检索的黑名单。

---

## 当前 Prompt 不足部分（逐段，按你 §8.1 的六类切分）

**1）哪些约束不够明确**
- `impact_cn` 的定义"写这个变化对市场状态或审计判断的实际含义"是**无操作性的**。模型没有判据，不知道"高信息量 impact"和"低信息量 impact"的边界在哪里，于是用平庸语填空。这是当前最大的单点缺陷。
- "幅度是否足以改变判断"被埋在 observed_changes 的散文要求里，而不是一个**离散的、必填的判定**。埋着 = 经常被跳过。这正是你 Q5 想解决的。
- `operator_focus`（人工审计方案）在 schema 里存在，但**prompt 正文从头到尾没有要求模型产出它**。模型只能从字段名猜。这是结构性遗漏：它是你的核心产品目标 #6 和 Q10，却不在 prompt 文本里。

**2）哪些约束可能互相冲突 / 笔误**
- 你在 §6 给出的**示范样例本身违反了你自己 §12.4 的单位规则**：
  ```json
  "fact_cn": "美债10年期收益率评分从6 bps升至22 bps。"
  ```
  "评分"（归一化分数，无量纲）和"bps"（真实基点）被写在同一句里。这恰好是你列为头号风险的"单位误读"。**模型会照抄你的示范**——示范即隐性指令。建议把这条 canonical 样例修正为二选一：要么是"评分由 6 升至 22（归一化）"，要么是"收益率由 X bps 升至 Y bps（真实基点）"，不能混写。

**3）哪些约束可能导致模型仍复述 delta**
- "observed_changes 必须说明倾向性、潜在意义和幅度……不要只复述 previous -> current"——这是**否定式约束**（告诉模型不要做什么），但没有给出**肯定式的填充结构**（告诉模型 impact 该长什么样）。否定式约束对生成模型的约束力弱，模型满足字面（不写"previous->current"字样），但语义仍是复述。

**4）哪些约束可能导致忽略非宏观维度**
- "不得让拆分后的宏观子字段主导解释"同样是**纯否定式**，没有机制。模型仍可能输出三条 MACRO（DXY/US10Y/VOLQ 各一条）挤占 Funding/Skew/Gamma。需要的是**结构性上限**（每 domain 至多一条，宏观子字段强制聚合为单一 MACRO 读数）。

**5）跨因子推理缺失**
- packet 有 `cross_domain_flags`、schema 有 `cross_factor_interactions`，但 prompt 只说"所有判断还要参考 cross_domain_flags"。它把 domain 当**清单**而非**图**。信息增益恰恰住在共振里：资金拥挤 + 斜率保护升温 + 宏观冲击门 = 一个连贯的 risk-off 状态，而不是三条各自"中性"的记录。当前 prompt 没有任何 forcing function 去要求"哪两个 domain 同向、联合含义是什么"。

**6）锚定风险（单次调用结构性问题）**
- packet 里同时含 `decision_transition`（决策/置信/阻断的前后变化）和 `materiality_score`。即便 prompt 说 materiality 只排序，模型在形成自己的读数**之前**就看到了系统结论和材料性排序，于是 `tendency_cn` 被系统的 `decision_transition` 污染。这正是单卡 review 用 two_call_strict 解决、而 transition 路径目前没有解决的问题（你的 Q11–Q13）。

**7）历史兼容缺字段无出口**
- prompt 没有给"数据不足以判断"的表达路径（你的 Q8）。历史兼容卡缺字段时，模型倾向编造 impact，而不是诚实地写"不足以判断"。

---

## 建议优化版 Prompt

下面是完整替代版。设计原则：**把否定式约束改写为肯定式脚手架**，并在单次调用内用"推理顺序"模拟两阶段独立性（即先独立读数、再对照系统结论），以最低成本拿到大部分抗锚定收益。

```text
角色与硬边界（保留，不可放松）：
你是信号审计变化链复核员。你只解释程序已计算出的 transition delta，不重算任何字段、权重、置信度或材料性；不使用外部行情；不把相关性写成因果；不输出任何交易、仓位、下单、止损、止盈、对冲或执行层建议。你的角色是审计旁路认知增强：把分散字段综合为可判断的市场状态路径，给出可追溯的倾向性解释与人工审计关注方案。

推理顺序（防系统结论锚定）：
形成每个 domain 的解释时，先仅基于 core_skeleton 前后值、core_transition_display 与该 domain 的原始 delta 形成独立读数；写完独立读数后，才参考 decision_transition、blocking 变化与 materiality_score 做一致性对照。materiality 只用于排序，绝不作为结论。若独立读数与系统 decision_transition 指向不一致，必须在 cross_factor_interactions 如实记录分歧，不得向系统结论靠拢。

impact_cn 必须覆盖的影响轴（缺一即判为低信息量复述）：
每条 observed_change 的 impact_cn 必须从以下轴中选取适用项作答，禁止只重述数值：
  (1) 方向骨架关系：该变化支撑还是削弱当前 TMV/TMVF 方向骨架；
  (2) 门控关系：是否跨过/退出冲击门、宏观硬阻断或其他阈值，使其由背景扰动升级为主动约束（或反之）；
  (3) 幅度充分性：幅度是否足以改变人工判断（同时写入 magnitude_verdict 字段）；
  (4) 跨域关系：是否与其他 domain 共振/冲突/对冲；若是，指出与哪个 domain、形成何种联合含义。

每条 observed_change 拆为：
  fact_cn：仅客观数值变化。若该字段为归一化评分、历史兼容指标或小量级 Gamma 指标，必须注明"（评分/兼容指标，非真实费率/bps/USD 名义额）"，不得写成可比较的市场金额。
  impact_cn：按上述四轴作答，写该变化对市场状态或审计判断的实际含义。
  tendency_cn：倾向性是对当前市场状态压力方向的解释——压制/风险约束、支撑、中性/缓和；它不是价格预测，不是操作方向。可附"利空/利多"作同义注解，但语义锚定在状态压力，不得读作交易方向。
  magnitude_verdict：枚举 changes_judgment / background_only / indeterminate。

数据充分性：
某 domain 字段缺失或仅为历史兼容值、不足以判断时，fact_cn 须说明该限制，tendency_cn 写"中性/无法判断"，magnitude_verdict 写 indeterminate，禁止编造影响。

domain 聚合（防宏观过度主导）：
observed_changes 每个 domain 至多一条。DXY/US10Y/VOLQ 等宏观子字段必须聚合为单一 MACRO 读数，不得拆成多条挤占 Funding/Skew/Gamma/P_C/Conflict。

中文语义映射（结论句不得出现 raw enum）：
NEUTRAL→中性；MACRO_BLOCKING→宏观硬阻断；MACRO_SHOCK_BLOCKING→宏观冲击门阻断；Headwind→逆风。
P/C 为非负比率，只解释为期权保护需求或相对需求变化，禁止写"正负符号翻转"。
Gamma/GEX 只作为波动放大/钉住的空间与风险约束，不得越权写成方向结论。

人工审计方案（operator_focus 必填，至少各一条）：
  - 下一步应重点观察哪个 domain 或哪个共振；
  - 当前解释的验证路径（看哪个字段可确认）；
  - 失效条件（满足什么条件时当前倾向解释作废，写入 invalid_if）。

综述（transition_summary_cn）：
写一句状态路径综合判断（整合各 domain 的联合含义），不是 observed_changes 的逐条复述。

禁止套话：
禁止"评估为关键变化""高材料性变化""材料性变化"或只说"关键/高"。

SignalTransitionReviewPacket:
{packet_json}
```

如果你不想做整段替换，最小局部 patch（按收益排序，前两条就能拿到 80% 收益）：

1. **插入"impact 四轴"段**（方向骨架/门控/幅度/跨域）——单点收益最高。
2. **把"幅度充分性"提升为独立枚举字段** `magnitude_verdict`。
3. **在 prompt 正文显式要求 `operator_focus`**（观察重点 / 验证路径 / 失效条件）。
4. **加 domain 聚合上限**（每 domain 至多一条，宏观强制聚合）。
5. **加数据充分性出口**（缺字段写 indeterminate，禁止编造）。
6. **修正 §6 的 canonical 样例**（不要"评分…bps"混写）。

---

## 关键修改点与理论依据

| 修改点 | 解决的问题 | 理论依据 | 潜在风险 | 验证方法 |
|---|---|---|---|---|
| impact_cn 引入四条强制影响轴（骨架/门控/幅度/跨域） | #1 复述 delta；#2 材料性套话 | 信息增益：把"无操作性的开放要求"换成"有限轴的结构化要求"，模型无法再用形容词填空，必须回答具体关系 | 模型可能机械套四轴、产出冗长 | 单测断言 impact_cn 至少命中一轴关键词；smoke case 人工判读是否"事实+形容词"已消失 |
| `magnitude_verdict` 升为独立枚举字段 | 幅度判断被埋、经常跳过（Q5） | 认知负荷/可测试：离散三值比散文更易被前端用作过滤器，也更易断言 | 模型在边界样本上误判幅度 | 构造"小幅变化"样本，断言不应输出 changes_judgment |
| prompt 正文显式要求 operator_focus（观察/验证/失效） | #8 方案意见不足（Q10） | 审计可用性：审计层价值在"下一步看什么"，而非仅"发生了什么" | 失效条件写成交易触发器 | 断言 invalid_if 为"状态/数据条件"措辞，不含价位/仓位 |
| domain 至多一条 + 宏观子字段强制聚合 | #7 宏观过度主导（Q7） | 结构约束 > 文案约束：用上限机制替代否定式劝告 | 真有多条宏观信号时聚合损失细节 | 构造 DXY/US10Y/VOLQ 三动样本，断言 MACRO 仅一条且提及三者 |
| 推理顺序（先独立读数、后对照系统结论） | 单次调用锚定（Q13） | 因果审慎/独立性：顺序隔离能在单调用内降低系统结论对 tendency 的污染 | 单调用内"独立"是软独立，模型仍可见全部上下文 | 锚定回归测试（见下方架构节） |
| tendency_cn 语义锚定为"状态压力方向"，"利空/利多"降为注解 | Q6 倾向被读作交易方向 | 安全边界/金融语义：把方向味词的语义钉死在 state-pressure，切断与操作方向的隐含等价 | 用户可能仍主观读成方向 | 前端文案与 tooltip 配合；断言 tendency 不与 not_trading_advice 冲突 |
| 数据充分性出口（缺字段→indeterminate） | Q8 历史兼容缺字段编造 | 因果审慎：给"不足以判断"一个合法出口，降低 confabulation | 模型过度使用 indeterminate 偷懒 | 构造缺字段样本断言 indeterminate；构造全字段样本断言不得 indeterminate |
| 单位标注（评分/兼容指标 vs 真实费率/bps/USD） | #4 单位误读 + §6 样例自相矛盾 | 金融语义精确性：在 fact_cn 强制标注量纲来源 | 标注啰嗦 | 黑名单断言："评分…bps""归一化…USD 名义额"等混写不得出现 |

---

## Before / After 示例

### 示例 1：MACRO 压力上升

**Before**
```
美债收益率评分从 6 bps 升至 22 bps，被评估为关键变化。
```
问题：①"评分…bps"单位混写；②"被评估为关键变化"是套话，零新增信息；③没说对方向骨架/门控/其他维度有什么影响。

**After**
```json
{
  "domain": "MACRO",
  "fact_cn": "US10Y 收益率分项评分由 6 升至 22（归一化评分，非真实 bps）。",
  "impact_cn": "宏观利率压力分项跨过冲击门阈值，由背景扰动升级为对风险资产的主动压制；与原 TMV 多头方向骨架形成对冲，削弱多头解释的环境支撑。",
  "tendency_cn": "压制/风险约束（市场状态压力方向，非价格预测）",
  "magnitude_verdict": "changes_judgment",
  "cross_factor_note": "需核对是否与 DXY、VOLQ 同向；若三项同向，应整合为单一 MACRO 读数而非三条独立项。"
}
```
**理论依据**：信息增益——impact 命中"门控跨越 + 骨架对冲"两轴，而非"关键"；因果审慎——用"跨过阈值/形成对冲"描述状态关系，不写"导致价格下跌"；金融语义——fact_cn 标注"归一化评分，非真实 bps"，直接修复 Before 的单位混写；宏观主导控制——cross_factor_note 逼迫聚合，避免三条宏观行。

### 示例 2：Funding（非宏观维度）

**Before**
```
资金费率评分从 0.2 升至 0.6，关键变化。
```
问题：①未区分"拥挤=趋势确认"还是"拥挤=回调脆弱"；②单位含糊；③套话结论。

**After**
```json
{
  "domain": "FUNDING",
  "fact_cn": "永续资金费率归一化评分由 0.2 升至 0.6（评分，非真实费率百分比）。",
  "impact_cn": "多头持仓拥挤度上升、杠杆多头维持仓位的付费意愿增强；在方向骨架未同步走强时，拥挤升温更多构成回调脆弱性而非趋势确认。",
  "tendency_cn": "中性偏风险约束（拥挤脆弱性，非看空价格）",
  "magnitude_verdict": "background_only",
  "cross_factor_note": "若同期 Skew 保护需求上升，则拥挤+对冲并存、风险约束权重提高；若 Skew 平稳，则仅为多头情绪升温。"
}
```
**理论依据**：信息增益——区分"拥挤作为脆弱性 vs 作为确认"是系统算不出、但模型能articulate的语义；金融语义——标注"评分，非真实费率"（对应 §7.3/§9.4）；因果审慎——跨因子用条件式（若…则…），不写成确定因果；防方向洗白——显式"非看空价格"。

---

## 是否建议调整 schema

**建议（全部向后兼容，旧 sidecar 缺字段时前端回落显示，不改历史 raw JSON）：**

- **新增 `magnitude_verdict`（per observed_change，可选枚举）**：changes_judgment / background_only / indeterminate。直接服务 Q5，且离散值便于前端做"仅显示足以改变判断的变化"过滤。
- **不建议把 `impact_cn` 改名为 `actual_impact_cn`（你的 Q2）**：字段名不是产出弱 impact 的原因，缺判据才是。改名是一次零信息增益的迁移，还要写兼容层。若一定要强化"实际"语义，在 prompt 内部标签里强调即可，持久化字段保持 `impact_cn`。
- **`operator_focus` / `invalid_if` 保持现状**，但在 prompt 正文显式要求其内容结构（观察/验证/失效）——这是 prompt 缺陷，不是 schema 缺陷。
- **关于 `not_trading_advice` vs `no_trading_instruction`（你的 §6 备注）**：建议**持久化字段名保持 `not_trading_advice`**，把 `no_trading_instruction` 仅作为文档/测试层的评估标签。若确需统一命名，用 additive 别名（同时写两个 key，下个大版本再废弃旧 key），不要硬改单一字段。
- **`transition_summary_cn` 不删但收紧（你的 Q4）**：定义为"状态路径综合判断"，与 observed_changes 的逐域拆解显式区分，避免二者重复。不建议完全压缩掉——一句综合 narrative 对前端"先看结论再看明细"的认知路径有价值。
- **per-domain 固定模板（你的 Q3）**：建议**给"语义判据"而非"输出模板"**。刚性模板会产出机械文本；语义判据只引导不规定措辞。更优做法是把各 domain 的语义判据**下沉到 packet 的 `field_glossary`**（数据驱动、随数据版本化），让 prompt 保持精简——这与你"结构字段驱动、非展示文本驱动"的一贯哲学一致。

---

## 是否建议调整调用架构

### 是否建议 transition 引入两次调用真盲审

**结论：先不要直接上 two_call_strict；分两步走。**

**第一步（现在做）——单次调用内的"推理顺序盲"（上面替代 prompt 已含）+ packet 内部分层。** 即把 packet 结构上区分"原始 delta"与"系统解释"，并用输出契约强制模型先写独立读数、再对照 decision_transition/materiality。这在单调用成本下拿到大部分抗锚定收益。它的局限要诚实承认：单调用内模型仍可见全部上下文，这是**推理顺序独立**，不是**信息隐藏独立**——是软独立。

**第二步（按测试结果决定是否升级）——真两次调用。** 上一个**锚定回归测试**作为升级门槛：构造一批"原始 delta 中性、但 decision_transition 显示进入 BLOCKING"的样本，观测模型 tendency 跟随的是**原始 delta** 还是**系统结论**。
- 若 tendency 跟 delta → 软独立已够，停在第一步（符合"最小手术、证据约束、诚实停手"）。
- 若 tendency 跟系统结论 → 锚定确实严重，再上 Option B。

**Option B（真两次调用）规格，供升级时直接落地：**
- **Call 1** 收 `TRANSITION_BLIND_DELTA_PACKET`：仅含 identity、comparison_quality/limitations、core_skeleton 前后值、core_transition_display、各 domain 原始 delta。**隐藏**：`decision_transition`、blocking 前后、`materiality_score`/材料性排序、以及任何编码了系统门控结论的 cross_domain_flags。Call 1 只产出 per-domain 独立读数 + tendency + magnitude_verdict。
- **Call 2** 收 `TRANSITION_BLIND_READ_RESULT` + 完整 packet（含 decision_transition、materiality、blocking）：做一致性对照，产出 operator_focus、invalid_if，并在 cross_factor_interactions 标注独立读数与系统结论的分歧点。Call 2 **不得重写** Call 1 的盲读结果，只能纳入为独立参考视角。

**命名建议：不要复用** `BLIND_THEORETICAL_PACKET / BLIND_REVIEW_RESULT`。两者语义不同——单卡盲的是**方向形成**，transition 盲的是**delta 解释**。复用同名会让审计日志无法区分两种盲审 regime。用上面新名 `TRANSITION_BLIND_DELTA_PACKET / TRANSITION_BLIND_READ_RESULT`，sidecar 里 `blind_review_mode` 取一个新值（如 `transition_two_call_strict`）。

**单次 vs 两次取舍速览：**

| 维度 | 单次（推理顺序盲） | 两次（真盲） |
|---|---|---|
| 成本/延迟 | 1× | ~2× |
| 独立性 | 软（顺序隔离） | 硬（信息隐藏） |
| sidecar schema | 不变 | 需加 blind 包 hash、双 call 路由 |
| 前端合并复杂度 | 不变 | 升高（需呈现"盲读 vs 复核"两视角或择一） |
| 输出稳定性 | 较高 | Call 2 受 Call 1 影响，需约束不重写 |
| 认知增益 | 中（够多数场景） | 高（仅当锚定确为瓶颈时才兑现） |

对 transition 这种**delta 解释**任务（而非单卡的**方向形成**任务），独立性的边际价值本就低于单卡——因为 delta 本身就是系统算的，你无法把 delta 解释从 delta 隐藏，能隐藏的只是系统对 delta 的**解读**。所以默认停在第一步是合理的，证据触发才升级。

---

## 是否存在越权或安全风险

当前 prompt 与建议改法**均未引入越权**。需要持续守住的两个软点：
1. `tendency_cn` 的"利空/利多"措辞最接近交易方向语言——已在替代 prompt 中把语义钉死为"状态压力方向"并把方向味词降为注解，配合前端 tooltip 即可。
2. `operator_focus` 的"失效条件"必须写成**状态/数据条件**（如"VOLQ 回落至阈下""资金费率评分回到 0.3 以下"），不得写成价位或仓位触发器——已在替代 prompt 与验证方法中加了断言。

文档本身不含密钥/账户/私有路径/执行指令，redaction 边界正确。

---

## 最终建议（含 §11 十四问逐条回答）

**如果只能改一处（Q14）**：改 prompt——**给 `impact_cn` 加四条强制影响轴**。它最直接地把产物从"材料性标签"抬到"决策可用信息"，且零迁移成本、零架构改动。架构（盲审）是第二顺位（提升独立性但不直接提升信息密度），schema 改名是装饰性。

**§11 逐条回答：**

1. **当前 prompt 是否足以稳定实现"事实→实际影响→倾向"？** 否。三层会被填满，但 impact 缺判据导致退化为复述。加四轴后可稳定。
2. **impact_cn 是否改名 actual_impact_cn？** 不改持久化字段名（零信息增益的迁移）；要强化"实际"在 prompt 标签里强调即可。
3. **是否给每 domain 加固定解释模板？** 加"语义判据"而非"输出模板"，并优先下沉到 `field_glossary`（数据驱动），保持 prompt 精简。
4. **是否压缩 transition_summary_cn、把主信息放进 observed_changes？** 不压缩掉，但收紧为"状态路径综合判断"，与逐域拆解显式区分。
5. **是否要求"幅度是否足以改变判断"作为独立字段？** 是。新增 `magnitude_verdict` 枚举（可选、向后兼容）。
6. **如何避免把"利空/利多"写成交易建议？** 把 tendency 语义钉死为"市场状态压力方向"，方向味词降为注解，前端 tooltip 配合，断言不与 not_trading_advice 冲突。
7. **如何避免宏观子字段过度主导？** 用结构上限：每 domain 至多一条 + DXY/US10Y/VOLQ 强制聚合为单一 MACRO，替代纯否定式劝告。
8. **缺字段时如何表达"不足以判断"？** 给合法出口：fact_cn 说明限制、tendency 写"中性/无法判断"、magnitude_verdict 写 indeterminate，禁止编造。
9. **当前 schema 是否已足够前端稳定展示？** 基本够（三层 + summary + operator_focus + invalid_if 映射前端固定区是对的）；建议补 `magnitude_verdict` 一个离散字段以支持前端过滤。
10. **当前 prompt 是否给出有用的人工审计方案意见？** 不够——operator_focus 在 schema 里但 prompt 正文没要求。需显式要求"观察重点/验证路径/失效条件"三件套。
11. **transition 是否应引入两次调用真盲审？** 不立即上；先做单调用推理顺序盲 + 锚定回归测试，证据触发才升级到真两次。
12. **若引入 transition 真盲审，盲读隐藏什么、复核如何合并？** Call 1 隐藏 decision_transition/blocking/materiality/门控类 flags，只出独立读数+tendency+magnitude；Call 2 收盲读结果+全包做一致性对照、出 operator_focus/invalid_if、标注分歧，不重写盲读。
13. **若不引入真盲审，如何避免被系统标签和材料性排序锚定？** 输出契约强制"先独立读数、后对照系统结论"，独立读数与 decision_transition 不一致时在 cross_factor_interactions 如实记录分歧而非靠拢。
14. **只改一处最该改哪？** impact_cn 四轴（见上）。

---

### 落地顺序建议（最小手术优先）

1. 加 impact 四轴 + `magnitude_verdict` + operator_focus 三件套 + domain 聚合上限 + 缺字段出口（一次 prompt/schema 改动，向后兼容）。
2. 修正 §6 canonical 样例的单位混写。
3. 补单测：impact 命中轴关键词、套话黑名单、单位混写黑名单、缺字段→indeterminate、invalid_if 措辞不含价位/仓位。
4. 补一个真实 LLM smoke case 矩阵：MACRO 冲击门、Funding 拥挤、Gamma 历史兼容、缺字段卡各一。
5. 上锚定回归测试，据结果决定是否进入两次调用真盲审。

判定"影响+倾向"是否已胜过"材料性"的标准：把同一 packet 的旧/新输出并排，问一个未读过该卡原始数据的审计员——**仅凭 LLM 文本能否说出"下一步该看什么、什么条件会推翻当前解释"**。能 = 信息增益达标；只能复述变化 = 未达标。
