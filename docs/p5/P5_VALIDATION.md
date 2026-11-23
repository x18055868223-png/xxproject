# P5 候选验收与复杂度审计报告（P5_VALIDATION）

**代码提交 SHA**：`8cc65692fcc41d4439c257a2ede57b798858f3db`（`codex/player-p5-candidate`，基于 `b3d79695`）  
**总验收状态**：**确定性离线测试全过（142/142）；真实大模型行为与前瞻 C 效果诚实标为待影子验证。**

---

## 1. 确定性测试实测记录（零 LLM / 零外网）

- **执行环境**：Windows PowerShell，解释器 `C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe`，工作目录 `.worktrees/player-p5-candidate`。
- **完整执行命令与结果**：
  ```powershell
  pytest tests/test_astra_p5_candidate.py tests/test_astra_player_condition_v1.py tests/test_astra_player_dossier_v1.py tests/test_astra_player_review_v1.py tests/test_astra_player_tracking_v13.py tests/test_player_initial_recovery.py tests/test_astra_audit_minimal_fixes.py tests/test_astra_workbench_v3_player_audit.py -q
  ```
  - 结果：**142 passed in 62.01s, 0 failed, 0 skipped**（其中新增 [test_astra_p5_candidate.py](../../tests/test_astra_p5_candidate.py) **22 passed in 4.07s**）。
- **未收集/未重跑的既有环境边界（如实披露）**：
  - 本地 `Python312` 环境未安装可选包 `jsonschema`，导致 `tests/test_astra_player_input_v1.py` 及 `tests/test_astra_anchor_relative_v1.py` 中依赖 `test_astra_structural_board_v1.py` 的 3 条测试无法由 pytest 收集；
  - `tests/test_astra_anchor_relative_v1.py::test_real_ard_replays_identical_A_without_hindsight` 在基线 `b3d79695` 时即因 D02 默认启用 `unverified_minute_label` 而与早期文档样例存在预期差异；
  - 为弥补无法收集 `test_astra_player_input_v1.py` 的不足，[test_astra_p5_candidate.py](../../tests/test_astra_p5_candidate.py) 中的 `test_real_frozen_h2_h3_h4_sources_and_legacy_b3d79695_parity` 直接编译执行 `git show b3d79695:tools/astra_player_input_v1.py`，并断言在 H2/H3/H4 真实冻结 `evidence/source.json` 上 `input_profile='legacy_v1'` 输出与 `b3d79695` **100% 逐字节一致**。

---

## 2. 独立只读 Reviewer 缺陷台账与修复闭环

在首轮完成实施后，独立只读 `reviewer`（`a1e642d7`）对全部 6 个修改文件对照 `b3d79695` 给出 **2 CRITICAL / 5 HIGH / 3 MEDIUM / 2 LOW** 审计结论；以下**全部 12 项**均已在提交 `8cc65692` 中完成修复并纳入回归测试复测：

| ID | 发现摘要与风险 | 修复位置与机制 | 对应回归用例 |
|---|---|---|---|
| **C1** | `validate_confirmation_prose` 存在 5 类自然中文误拒（复述“不要写成:00/:05”、1 分钟采样、“不采用 new_entry”、“不构成进入/返回”、单句否认后带单侧规则 & C 内写亏损带触发阻断） | [astra_player_condition_v1.py](../../tools/astra_player_condition_v1.py) 扩展否定前缀与 36 字符前向窗口、按子句切分、豁免采样上下文；[astra_player_review_v1.py](../../tools/astra_player_review_v1.py) 将 C 区间提法降为 `c1_mechanical_advisory` 非阻断 | `test_c1_natural_prose_anti_false_rejection`, `test_h1_j1_natural_prose_sections_and_c1_band_advisory_not_blocking` |
| **C2** | `collect()` 回填 `compat_backfill_applied=True` 绕过 `strict_asof=True`；且 `condition_at_t0/episode` 与 `build_input` ARD 口径不一致 | [astra_anchor_relative_v1.py](../../tools/astra_anchor_relative_v1.py) 拒绝 `compat_backfill_applied`；新增共享 `ard_facts_for_evidence` 在三端统一调用 | `test_p0_strict_ard_rejects_compat_backfill_and_matches_rendered_markdown` |
| **H1** | J1 对 `有效破坏标准：`、`ARD责任相关性：` 等行吞并、带空行多段 C、`Put路径（14:00 UTC截面）：` 存在误拒 | [astra_player_dossier_v1.py](../../tools/astra_player_dossier_v1.py) 扩展标签识别与短冒号边界切分，支持边界完结的多自然段 C 摘录 | `test_h1_j1_natural_prose_sections_and_c1_band_advisory_not_blocking` |
| **H2** | `respond()` 信任外部 `PASSED`、接纳未知版本、`@1.0` 误降级、`episode()` 手工冻结漏检文本、`_resolve_reply_prompt_version` 可双向前后端伪造 | [astra_player_dossier_v1.py](../../tools/astra_player_dossier_v1.py) `respond`/`episode` 内部重算校验；[astra_player_review_v1.py](../../tools/astra_player_review_v1.py) 双向全文本匹配（`.strip()`） | `test_all_six_anti_bypass_gates_and_legacy_compat` |
| **H3** | `tracking.link()` 在调用 `service('lock')` **之后**才做契约全检，失败时产生孤立锁单且循环 `LINK_UNVERIFIED` | [astra_player_tracking_v13.py](../../tools/astra_player_tracking_v13.py) 将 `validate_initial_reply_contract` 前移至 `condition_plan()`（发 `lock` 之前阻断） | `test_all_six_anti_bypass_gates_and_legacy_compat` (case 6) |
| **H4** | `inspect_spot_qualification` 忽略 `gap`/`truncated`/`cvd_degraded` 标记，混合窗口（如 4h `UNKNOWN` + 12h `ALIGNED`）标注与文案失真 | [astra_player_input_v1.py](../../tools/astra_player_input_v1.py) 检查全部降级标记，拆出 `OBSERVED_ALIGNED_SLOW_ONLY` 并阻止混入“已成立反证”导语 | `test_p0_spot_qualification_and_degraded_mixed_windows`, `test_p0_strict_l2_and_mixed_spot_and_extremum_rendering` |
| **H5** | 把 `@1.1-candidate` 强制等同于 P5 必填 `require_outside_at_t0` 破坏了 Route C 原 Prompt“可选”语义 | 拆分 `P5_STRICT_PROMPT_VERSIONS`，保留 `@1.1-candidate` 省略默认 fail-closed `True` 而 `@1.2-p5-candidate` 严格必填 | `test_all_six_anti_bypass_gates_and_legacy_compat` (case 1) |
| **M1** | `new_qualified_data_present` 误把 `t0` 样本、`check_at_ms` 时钟与未证实边界分钟当成入场后新增合格数据 | [astra_player_tracking_v13.py](../../tools/astra_player_tracking_v13.py) 严格限制 `data_floor < ts <= at`、有效指标、`anchor.ready` 与图表延迟；修复 `review_v1` fallback | `test_hold_review_cm_separation_and_new_qualified_data_present` |
| **M2** | `ard.analyze(strict_asof=False)` 输出字典一致性风险 | 保持 `strict_asof=False` 返回结构与 `b3d79695` 完全一致，仅在 `strict_asof=True` 时剔除 `compat_backfill_applied` | `test_p0_strict_ard_rejects_compat_backfill_and_matches_rendered_markdown` |
| **M3** | `Dossiers.respond()` 遇到非法 `invalidation` 时 `canonical_confirmation_text` 抛未捕获异常 | 在 `validate_initial_reply_contract` 守卫合法规则并于 `respond()` 捕获异常落盘 `PARSE_FAILED` | `test_all_six_anti_bypass_gates_and_legacy_compat` (case 5) |
| **LOW** | `extract_l2_index_observations` 短腿缺失时丢弃合法 `long` 观测；`accepted()` 静默覆盖版本不一致 | 支持 `long_only` L2 记录并在 `accepted()` 对 `req_pv != att_pv` 抛 `Blocked('prompt_version_mismatch')` | `test_p0_strict_l2_and_mixed_spot_and_extremum_rendering`, `test_all_six_anti_bypass_gates_and_legacy_compat` |

---

## 3. 故障注入、兼容矩阵与复杂度/字节预算审计

- **运行可靠性故障注入（全部通过）**：
  - 付费回复触犯 `ContractValidationError`：保存 `reply.json`、`response.full.raw`、`attempt.json` (`status='PARSE_FAILED'`, `error_code='CONTRACT_VALIDATION_FAILED'`, `delivery_state='RECEIVED'`)，`_auto_deal` 与二次运行均 **1 次调用零重试**。
  - 在 `parse_initial_reply` 前崩溃重启：`recover_initial()` 自动加载 `source.json` 合同完成严格重解析，**0 次额外 LLM 调用**。
  - 损坏历史旧档与现役新牌并行：`old_hand` 隔离不阻断现役 P5 槽位；`@1.0` 旧回复 + 120s legacy deadline 无伤通过。
  - Hold 复核触发时：`t0` 同刻与边界未完结分钟均得 `new_qualified_data_present=False`，真实新采样后才转 `True`，且保持 `execution_allowed=false`。

- **静态字节、SHA-256 与 Token 预算（真实冻结牌测算，0 LLM 调用）**：

| 载体 / 样本 | 方案对比 | 文本字节 | 粗估 Tokens | `system_sha256` / `body_sha256`（前 12 位） | 实测变化与备注 |
|---|---|---|---|---|---|
| **System `@1.0`** | 现行生产 | 2,831 B | ~671 tok | `653168703cd8...` | 含错误“两腿之间/UTC网格”旧公理 |
| **System `@1.1-candidate`** | P3 研究线 | 3,306 B | ~777 tok | `98ae4aaae928...` | +475 B / ~+106 tok（不采用） |
| **System `@1.2-p5-candidate`** | **本轮选定** | **3,073 B** | **~732 tok** | **`b746c2d2f7f9...`** | **较 `@1.0` 仅 +242 B (~+61 tok, +9.1%)；较 `@1.1` 缩减 233 B** |
| **H1** `hand_8b126baf...` | `@1.0+legacy` → `@1.2+p0` | User 14,664 → 14,835 B | ~3,343 → ~3,392 | `946f04058e6e...` → `b93cf1924ac8...` | +171 B（增加规范行与L2说明） |
| **H2** `hand_af89529f...` | `@1.0+legacy` → `@1.2+p0` | User 14,977 → 15,306 B | ~3,424 → ~3,511 | `6dac68a56cb5...` → `277a2a50fd50...` | +329 B（增 `599ms/-2.49 USD` L2 双腿与现货 `OBSERVED_UNALIGNED`） |
| **H3** `hand_471b8255...` | `@1.0+legacy` → `@1.2+p0` | User 14,824 → 15,250 B | ~3,386 → ~3,497 | `36d3536356ef...` → `b6c3f9666172...` | +426 B（增 L2 偏移与现货 `OBSERVED_UNALIGNED`） |
| **H4** `hand_0cdc0af5...` | `@1.0+legacy` → `@1.2+p0` | User 14,550 → 14,417 B | ~3,315 → ~3,299 | `7c7ebbb4f68d...` → `91b00ac3e280...` | **-133 B**（无收据旧 ARD 诚实降为 `UNKNOWN` 短句，且增全窗极值披露） |

- **运行时新增步骤与失败面**：解析链路多一次纯本地 CPU 确定性校验（毫秒级、零 I/O）；新增 `UNKNOWN` 与 `PARSE_FAILED(CONTRACT_VALIDATION_FAILED)` 状态。因所有 LLM 输出特性均尚未在真机新牌上运行，**输出长度/思考冗余/真实误拒率/C 早于 M 准确率一律诚实标为 `待影子验证`**。
