# P5 独立架构裁决与最小候选交付报告（P5_DESIGN_DECISION）

**当前状态**：`ISOLATED_CANDIDATE_READY / READY_FOR_SHADOW`（**明确非 `READY_FOR_PRODUCTION`**）  
**隔离分支 / 提交**：`codex/player-p5-candidate`（基于已核实线上基线 `b3d79695`；代码与测试原子提交 `8cc65692fcc41d4439c257a2ede57b798858f3db`）  
**候选标识**：System `player_initial_system@1.2-p5-candidate`（3,073 B, `sha256: b746c2d2...`）+ User `p0_strict@1` + 机械契约 `p2_strict@1`

---

## 1. 执行摘要

基于对 P4 全部 8 份原始模型记录及 `xx-eu` 当前真实部署（**`b3d79695`，非历史 `ba93eef2`**）的独立核查，本轮最终架构裁决为：**采用改良版方案 B（`@1.2-p5-candidate + p0_strict@1 + p2_strict@1`）并在隔离工作树完成零付费最小实施**，同时以 **方案 D（纯 P0+P2 无初始 C）** 作为影子对照不合格时的直接降级方案。

### 与 P4 原裁决的异同
- **相同**：保留 A 的双侧自主择优内核、落实现有未上线的 P0 严格源资格与 P2 程序硬保护、C 仅作为独立复核证据而不授权自动交易、不晋级完整 `@1.1-candidate`。
- **关键修正（独立挑战 P4 原表述）**：
  1. **不能原封不动保留全部 `A_System@1.0`**：经逐字核查，`@1.0` 自身包含“两腿之间亏损承受区间”和“按 UTC 网格执行”的错误表述，这直接导致了 H4-A+P0 的边界漏判。本版保留 `@1.0` 择侧逻辑，仅对其做外科手术式边界与真时钟纠偏，**并非直接拼接未经改写的 `@1.0`**。
  2. **P0 在线上属于 `PARTIAL` 而非全空白**：线上 `b3d79695` 已部分落地 `chart_points_audit` 和 `L2.book.short` 单腿，但仍缺 `p0_strict@1` 的现货三态降级、L2 长短腿偏移、ARD 严格拒绝 `compat_backfill_applied`、全窗极值披露，以及完整 `request.json` 哈希收据。
  3. **最小 C 必须“可由现有 Hold 数据源证伪”且防误拒**：针对 H2-B 两条晚于 M 的 C 触发、H4-B 不可操作的盘中补历史 Pin 触发，将 C 绑定到真实现场源；同时基于独立只读 Reviewer 的反例警告，移除对 C 段落使用 M 区间正则作 Layer-1 阻断的做法，防止 `retries=0` 下烧毁正式发牌。

---

## 2. P4 证据边界与生产差距溯源

- **证据真实边界**（详见 [P4_EVIDENCE_BOUNDARIES.md](./P4_EVIDENCE_BOUNDARIES.md) 与 [P4_TO_P5_TRACEABILITY.md](./P4_TO_P5_TRACEABILITY.md)）：P4 实为 **3 张牌、8 份首次输出**（A+P0 vs B 择侧 3/3 一致，零择侧胜出证据）。P0 资格价值已由 H2/H3 证实；C 的结构清晰度有提升，但其真实 Hold 提前预警价值及混合 Prompt 的线上行为均 **仍待影子验证**。
- **生产调用链差距**（详见 [CURRENT_STATE_AUDIT.md](./CURRENT_STATE_AUDIT.md)）：
  - `b3d79695` 保留完好并维持不变的底座：**1 次 `API_AUTO` 付费硬上限（`retries=0`）**、`dispatch.json`/`recover_initial()` 防重发、旧牌异常隔离、120s/15m 交接兼容、`DISCRETIONARY_REVOKE_UNVERIFIED` 归因防伪、`current_scheduled()` 与工作台 403/首屏优化、`execution_allowed=false`。
  - P5 新增与修改边界：**仅改动 `tools/` 下 6 个玩家核心模块 + 新增 `tests/test_astra_p5_candidate.py`**；明确**不触碰** `astra_workbench_v20.py`、前端 `deploy/astra_workbench_v3/*`、真实调度定时器及 `outcomes/`。

---

## 3. 竞争架构评估与单一决策选择

我们在完全同一维度下比较四条路线（无 API 实跑成本项以字节/正则/静态路径计）：

| 维度 | 方案 A：`@1.0` 不变 + P0 + P2 + 事后抽 C | **方案 B（本轮选定）：`@1.2-p5-candidate`（A 内核+公理修正+最小源绑 C）+ P0 + P2** | 方案 C：完整 P3 `@1.1-candidate` + P0 + P2 | 方案 D（零假定对照/降级基线）：A 公理修正 + P0 + P2（零 C） |
|---|---|---|---|---|
| **事实与 P0 消费** | `@1.0` 旧公理含错；无法根除“资料缺失当否定”提示 | 完整消费 `p0_strict@1`，System 与 User 口径无冲突 | 完整消费 `p0_strict@1` | 完整消费 `p0_strict@1` |
| **双侧自主择优** | 保持 `@1.0` | **保持 `@1.0` 核心规则，无新增方向偏置与示例** | 结构最重（+475 B vs `@1.0`），格式段规则更僵化 | 保持 `@1.0` |
| **C 可观测性与可证伪** | 要么退化，要么需**额外一次 LLM 调用**抽 C（双倍调用/延迟）或丢掉首次理由 | **单次调用**内嵌 4 要素，绑定 3 个现存 Hold 真实源（外侧余量、对齐 4h/12h 现货流、同口径 Pin/ARD） | 要求 C 四要素，但缺少盘中数据源绑定约束 | 无前瞻 C（Hold 仅靠 4 类固定触发器） |
| **机械 M/t0 防错与误拒面** | 模型易按 `@1.0` 旧句复述“两腿之间”遭 P2 正则自相拦截 | 公理对齐程序，同时保留自然段容错、5 类反误拒校准与 C 区间改 Layer-2 警示 | 强要求固定 9 冒号段头；原分支还混同 `require_outside_at_t0` 可选与否 | 最低误拒面，只需校验 M 边界与 JSON |
| **调用/Token/可维护性** | 若事后抽 C 则调用数 ×2；若不抽则回退成 D | **1 次调用**，System 3,073 B（较 `@1.0` 仅约 **+61 tok / +9.1%**）；与 `@1.0` 共同运行同构回滚 | System 3,306 B（+106 tok），规则复杂度高 | System 仅约 2,900 B，审计归因最干净 |

### 为什么不选 A、C、D，以及针对本决策的最强反论点

- **否决方案 A**：`@1.0` 自带过时公理会与 P2 校验正面冲突；若为了保住 C 增加后验第二次 LLM 调用，直接增加 1 倍尾部延迟、预算与恢复状态机复杂度，违背最小工程原则。
- **否决方案 C**：P4 没有给出 `@1.1-candidate` 优于极简化的择侧或止损证据，强行上马更长、更僵硬的 P3 协议违反项目风格（`ASTRA_MODEL_STYLE.md`）。
- **最强反论点（来自本轮只读 Reviewer 对方案 D 的支持）**：
  1. 在 `retries=0` 约束下，哪怕经过校准的中文 M 正则（`validate_confirmation_prose`）与自然段 J1 检查（`inspect_excerpt_integrity`），理论上仍存在极小概率的边界表达误拒，从而把一个高价值正式局记成 `PARSE_FAILED`。
  2. P5 同时改动 P0、P2 和最小 C，影子对照时若 Reject 率微升或输出变长，难以 100% 离析纯 C 与 P0 的贡献，而方案 D 将变更收敛为纯确定性工程修复。
- **为什么仍选改良方案 B 而不是直接停在 D**？因为一旦选 D 推进影子，后续测试 C 的可证伪价值还得另启一轮实验。方案 B 通过将 System 增幅严控在 `+242 B`（约 `+61 tokens`）、允许自然段摘录、把 C 区间校验降为 Layer-2 非阻断警示，实现了**低代价同轮联合验证 C 与 P0+P2**；且因为 P2 和 C 均有独立代码开关与版本隔离，**可随时在影子阶段按下列客观触发条件无痛降级至 D**。
- **推荐改变 / 降级方案 D 的明确触发条件**：影子测试中只要满足任一条件即切回方案 D——① `@1.2-p5-candidate` 因正则/J1 导致 `CONTRACT_VALIDATION_FAILED` 合法拒单率 > 5%；② 相比同证据 `@1.0`，出现明显保守 Reject 漂移或输出明显模板化空洞；③ C 条件仍反复晚于 M 或不可观察且在 Hold 中无真实信息增量。

---

## 4. 已完成隔离实施与核心变更明细

- **Git 隔离位置**：工作树 `.worktrees/player-p5-candidate` 分支 `codex/player-p5-candidate`（基线 `b3d79695` → 提交 1 代码+测试 `8cc65692fcc41d4439c257a2ede57b798858f3db`；本批文档随后原子提交）。
- **System Prompt 全量统一差分（`@1.0` → `@1.2-p5-candidate`）**：

```diff
--- player_initial_system@1.0
+++ player_initial_system@1.2-p5-candidate
@@ -1,13 +1,12 @@
 你是承保影子牌手。请完整阅读冻结牌面并用中文作答。
-【核心术语公理】
-- 责任区：卖方短腿与保护腿之间的亏损承受区间（触及边界不算入区，越过短腿进入区间才算严格入区）。
-- 本期尺度：指本期到期隐含波动给出的 `expected_move_usd` 预期波动尺度。
-- Pin / 墙：仅作当前空间结构位置参照，不带方向性，不构成价格吸附保证。
-- ARD：仅衡量过去 4 小时围绕同到期已知 Pin 的历史贴近、离返与迁移事实，不做未来方向预测。
+【核心术语与机械公理】
+- 单侧机械边界：短腿与保护腿之间仅描述价差几何亏损带，不构成机械上限。Put 分钟采样严格低于短腿、Call 严格高于短腿即记为严格进入（含保护腿边界与外侧）；触及短腿不算进入也不算返回，严格回到短腿外侧才算返回。
+- 检查时钟：首次检查为入场后下一 UTC 整分钟，之后每 5 分钟（`check_interval_ms = 300000`）步进；不要写成自然 `:00/:05` 整点网格或 60 分钟锚点。
+- 本期尺度：到期隐含波动给出的 `expected_move_usd`；Pin/墙仅作空间参照不带方向性；ARD 仅衡量过去 4 小时围绕同到期 Pin 的贴近、离返与迁移事实。
 【作答与规则锁定】
-1. 在正文开头或明确独立标题行写出唯一结论：`## Accept / Put`、`## Accept / Call`、`## Reject` 或 `## Unknown`。
-2. 正文必须分别给出 Put 路径判断、Call 路径判断、Put 最强反证、Call 最强反证与两侧对比择优，并冻结历史事实、承担条件与破坏确认口径。
-3. 系统的机械有效破坏规则仅支持两选一：`"new_entry"`（入场后出现严格进入责任区的分钟采样即触发）或 `"entry_not_returned"`（入场后严格进入责任区且至固定检查点仍未严格返回责任外才触发）；固定检查钟默认每 5 分钟（`check_interval_ms = 300000`）按 UTC 网格执行。正文书写破坏确认口径时必须直接对齐该两选一规则与 5 分钟网格，严禁自行编造“每 60 分钟整点锚点”等其他检查时钟。
-4. 为便于程序做逐字子串核验，建议正文中 Put 路径、Call 路径、Put 最强反证、Call 最强反证、两侧对比择优，以及（若 Accept）冻结历史事实、承担条件、破坏确认口径（与 ARD 判读）各自先写一段连贯完整的中文整句段落，尽量不在摘录段落内使用分点列表或反引号。
-5. 在正文最后附带 `<ANNOTATION_JSON>...</ANNOTATION_JSON>`（必须是合法 JSON 对象）。其中 `put`、`call`、`comparison`、`put_counter`、`call_counter` 必须是**从上方中文正文原封不动逐字复制（verbatim substring）的非空连续片段**——不要删改、浓缩、跨段拼接或替换任何字词、数字、标点或符号；`depends_on_ard` 必须是显式布尔值。
-6. 若结论为 Accept，`<ANNOTATION_JSON>` 中必须包含 `condition_plan` 对象（不能是字符串），含：`invalidation`（仅限 `"new_entry"` 或 `"entry_not_returned"`）、`check_interval_ms`（默认 `300000`，整分钟毫秒）、`historical_facts`、`accepted_conditions`、`confirmation_excerpt`（三者均须是**从上方中文正文原封不动逐字复制的连续片段**，任意改写或浓缩都会导致解析失败作废），以及可选 `require_outside_at_t0`（布尔）；若 `depends_on_ard` 为 true，还须提供逐字复制自正文的 `ard_responsibility_meaning` 与 `ard_review_on`。
+1. 正文开头或独立标题行写出唯一结论：`## Accept / Put`、`## Accept / Call`、`## Reject` 或 `## Unknown`。
+2. 正文以连贯完整的中文自然段分别写清 Put 路径判断、Call 路径判断、Put 最强反证、Call 最强反证与两侧对比择优；若 Accept，另写清冻结历史事实、承担条件（C）、破坏确认口径（M）及（若依赖 ARD）ARD 责任相关性与复核变化；每段请写完整句，摘录段尽量不用分点或反引号。
+3. 机械破坏口径（M）仅支持 `"new_entry"`（入场后出现严格越过短腿采样即触发）或 `"entry_not_returned"`（入场后严格越过短腿且至检查点仍未严格回到短腿外侧才触发），按上述 5 分钟时钟执行且须锁定 `require_outside_at_t0 = true`。
+4. 承担条件（C，对应 `accepted_conditions`）须独立于机械 M，简明写清四项：（1）冻结时正面承担的最强已知不利事实；（2）尚未排除承保依据的理由；（3）关键来源、时钟或覆盖不确定性（标明 `OBSERVED_UNALIGNED` 或 `UNKNOWN`，严禁将资料缺失或未证实表述为“从未发生”）；（4）独立于机械 M、可在越界前由系统盘中真实数据源复核的未来证伪事实（短腿外侧剩余余量与路径演变、时钟对齐后的同源 4h/12h 现货主动流、同口径本期 Pin/ARD 迁移）。仅在机械越界后才成立的条件不算独立前置 C；不可观测项标 `UNKNOWN`；C 触发仅启动独立复核与留痕，不授权自动退出或交易。
+5. 文末附 `<ANNOTATION_JSON>...</ANNOTATION_JSON>`（合法 JSON 对象）：`put`、`call`、`comparison`、`put_counter`、`call_counter` 须为上方正文逐字复制的完整连贯段落（禁止删改、浓缩或句中截断）；`depends_on_ard` 为显式布尔值。
+6. 若 Accept，须含 `condition_plan` 对象：`invalidation`（`"new_entry"` 或 `"entry_not_returned"`）、`check_interval_ms`（`300000`）、`require_outside_at_t0`（`true`）、`historical_facts`、`accepted_conditions`、`confirmation_excerpt`（均完整逐字复制自正文）；若 `depends_on_ard` 为 `true`，须在 `condition_plan` 内逐字提供 `ard_responsibility_meaning` 与 `ard_review_on`。
```

- **真实冻结输入 SHA-256 样本摘要**（全表与验证见 [P5_VALIDATION.md](./P5_VALIDATION.md)）：
  - H2 `p0_strict@1 + @1.2`：`user=15,306 B` (`cf290b83...`), `body_sha256=277a2a50...`（`legacy_v1+@1.0` `body_sha256=6dac68a5...`）
  - H3 `p0_strict@1 + @1.2`：`user=15,250 B` (`ba8a8526...`), `body_sha256=b6c3f966...`
  - H4 `p0_strict@1 + @1.2`：`user=14,417 B` (`d3b6b677...`), `body_sha256=91b00ac3...`
- **确定性验证结果**：[test_astra_p5_candidate.py](../../tests/test_astra_p5_candidate.py) **22/22 通过**；全部 8 份玩家核心测试集共 **142 passed, 0 failed**（含真实 H2/H3/H4 `legacy_v1 == b3d79695` 逐字节无漂移验证，以及独立 Reviewer 提出的所有 CRITICAL/HIGH/MEDIUM/LOW 缺陷修复后复测）。所有涉及 LLM 输出质量、择侧分布与 C 前瞻增量的结论均诚实标为 **待影子验证**。

---

## 5. 影子验收方案与下一轮唯一清晰授权建议

- **影子执行设计**：详见 [P5_SHADOW_AND_ROLLBACK.md](./P5_SHADOW_AND_ROLLBACK.md) 与 [P5_RELEASE_CANDIDATE_MANIFEST.json](./P5_RELEASE_CANDIDATE_MANIFEST.json)。
- **下一轮唯一清晰授权建议**：
  1. **先行零付费离线影子核验（推荐立即批复）**：授权只读拉取 `xx-eu` 上 **2026-11-20 至 2026-11-23 未见过的新自然牌 `source.json`**（严禁读 `outcomes/`）到本地离线回放 `p0_strict@1` 输入构造与历史 `@1.0` 回复跨版本校验审计（零 LLM 预算，零生产风险）。
  2. **有界限双臂付费影子试点（如批准调用预算）**：授权从提交 `8cc65692` 选出 5–10 张未见新自然牌或只读旁路，在 `execution_allowed=false`、单独目录、`retries=0` 下对同一 `p0_strict@1` 证据平行跑 `@1.0` 与 `@1.2-p5-candidate`（每张牌总双臂预算上限约 8,500 输入 tokens + 模型思考/回复输出，详见成本框算），按盲审门槛决定最终晋级、微调还是退回方案 D。
  - 当前 **`READY_FOR_SHADOW`** 已达成；**`READY_FOR_PRODUCTION`** 尚未达成（必须先通过自然新牌影子验收并另案审批生产部署）。

---

## 6. 最后必答三问

1. **P4 的成果里真正值得生产吸收的最小集合是什么？**  
   ① **完整 `p0_strict@1` 证据纪律**：现货三态 (`OBSERVED_ALIGNED / OBSERVED_UNALIGNED / UNKNOWN`) 与禁用非对齐反证导语、L2 短/长双腿顺序时差/价差、`strict_asof=True` 拒绝历史回填假 `available_at_ms`、全窗极值仅在完整覆盖时声明“未触及保护腿”；② **确定性 P2 防绕过门禁 (`p2_strict@1`)**：单侧严格短腿门槛、入场后下一 UTC 整分钟首检与 5m 步进、阻断 `strict_contract=False` / `require_outside_at_t0` 省略 / 手工与恢复绕过、先验契约再锁单；③ **`@1.2-p5-candidate` 外科公理对齐 + 绑定真实 Hold 数据源的最小 4 要素 C**（仅作人工/独立复核留痕，不授自动交易权）。

2. **你为什么认为当前推荐实现比其余方案更优？**  
   相比方案 A，它一次消除 `@1.0` 自带“两腿之间/UTC网格”的规则冲突，并避免事后抽 C 带来的双倍 LLM 调用成本；相比方案 C（`@1.1-candidate`），它在 System 更短（3,073 B vs 3,306 B）且保留自然段摘录容错的基础上去掉了无依据的硬性冒号前缀与冗余结构；相比方案 D（无 C），它在单次调用仅约 +61 输入 tokens 的边际开销下以统一版本化契约把“C 究竟有没有独立信息增量”变成可观测、可证伪的影子问题——且我们已在 Reviewer 审计后将 C 区间检查降为非阻断，一旦影子期 C 无效可零痛退回方案 D。

3. **离下一次有价值的受控验证还差什么、是否已准备好申请影子授权？**  
   工程与确定性离线防线（隔离工作树 `codex/player-p5-candidate@8cc65692`、6 个运行时核心模块、142/142 回归与对抗测试、请求全套 SHA-256 收据与 1 步回滚方案）已全部就绪，**唯一缺少的是真实大模型对 `@1.2-p5-candidate` 在未见自然新牌上的首次输出率、J1/M1 合规率及后续 Hold 增量复核记录**。**已准备好申请零付费离线回放及 5–10 局有预算上限的双臂只读影子授权。**
