# P5 候选验收与复杂度审计报告（`P5_VALIDATION`）

**最新 RC 代码提交 SHA**：`c39b3d278585b94fbdccd9cc320befe4b47871ad`（分支 `codex/player-p5-candidate`，已Clean合并现网 `6311071c887433efb5a9b754290a1d8050caa0d8` 于 `5e237a87`）  
**代码/确定性门禁状态**：**RC 加固清零（0 CRITICAL / 0 HIGH）；24 文件回归 510 passed / 2 基线已知 D02 差异（干净 `6311071c` 同现）/ 1 skipped。外部 soak（`>= 2` 次 `6311071c` 后自然发牌周期）未到前维持 `READY_BUT_NOT_DEPLOYED`。**

---

## 1. 确定性测试实测记录（`jsonschema 4.26.0`，零付费 LLM）

- **执行环境**：Windows PowerShell，解释器 `C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe`（`jsonschema 4.26.0`），工作目录 `.worktrees/player-p5-candidate`。
- **P5 候选专项套件**：
  - `pytest tests/test_astra_p5_candidate.py -q` → **`38 passed in 10.59s, 0 failed, 0 skipped`**。
- **24 文件全量影响域回归（含上游 `test_deribit_discovery_reliability.py`）**：
  - 范围：`test_astra_p5_candidate.py`、`test_astra_player_review_v1.py`、`test_astra_player_tracking_v13.py`、`test_astra_player_dossier_v1.py`、`test_astra_player_input_v1.py`、`test_astra_player_condition_v1.py`、`test_astra_player_v1.py`、`test_astra_player_e2e_v13.py`、`test_astra_hold_review.py`、`test_astra_anchor_relative_v1.py`、`test_astra_ard_condition_v1.py`、`test_astra_locked_candidate_compare_v1.py`、`test_astra_locked_candidate_store_v1.py`、`test_astra_locked_followup.py`、`test_astra_schedule_r16_archive.py`、`test_player_initial_recovery.py`、`test_astra_workbench_v20.py`、`test_astra_workbench_v3_player_audit.py`、`test_astra_audit_minimal_fixes.py`、`test_astra_strict_json_response.py`、`test_astra_offline_regrade.py`、`test_astra_underwriting_discovery_v20.py`、`test_astra_research_archive_v1.py`、`test_deribit_discovery_reliability.py`。
  - 结果：**`513 collected -> 510 passed, 2 failed, 1 skipped, 0 errors` in `173.23s`**（`pytest_p5_step2_full.log`）。
- **2 项基线已知 D02 差异与 1 项跳过说明**：
  1. 在干净现网基线 `.worktrees/deribit-rest-pacing` (`6311071c`) 同环境实跑 `pytest tests/test_astra_anchor_relative_v1.py tests/test_astra_ard_condition_v1.py -q`：**`2 failed, 44 passed in 0.87s`**，失败用例与报错行完全一致：
     - [test_astra_anchor_relative_v1.py:275](../../tests/test_astra_anchor_relative_v1.py#L275) (`test_real_ard_replays_identical_A_without_hindsight`)
     - [test_astra_ard_condition_v1.py:123](../../tests/test_astra_ard_condition_v1.py#L123) (`test_real_hand_and_synthetic_continuation_stay_separate_and_reproducible`)
     - 原因：D02 默认启用 `unverified_minute_label` 后 `build_input()` 输出 `合格图表末点66,739.4 + 双as-of核验`，而早期 Nov 15 fixture 仍比对旧文案 `指数66,767.6`；非 P5 引入回归（且 `test_real_frozen_h2_h3_h4_sources_and_legacy_b3d79695_parity` 已逐字节验证 `legacy_v1` 与 `b3d79695` 一致并 `PASSED`）。
  2. `1 skipped`：[test_astra_locked_candidate_compare_v1.py:387](../../tests/test_astra_locked_candidate_compare_v1.py#L387) (`test_real_initial_public_view_context_refs_keep_missing_source_clock_none`)，因工作树未内置可选外部夹具 `.artifacts/locked_candidate_followup_20261008_01/initial_public_view.json` 而按设计跳过。

---

## 2. 本轮阻断缺陷清零与 Reviewer 终审闭环

在首轮 `a1e642d7`（12 项全部修复于 `8cc65692`）及影子盲审 `b420a936` 发现 **[HIGH] 跨节假放行** / **[MEDIUM] `daily_r16@4` 元数据** / **[LOW] CLI `LATE`** 之后，本轮已完成最小加固（提交 `c39b3d27`）并经独立只读 Reviewer `59cca70d` 复审（**0 CRITICAL / 0 HIGH 未决**）：

| 缺陷编号与等级 | 问题根因 | 最小修复位置与机制 | 对应回归与重评验证 |
|---|---|---|---|
| **[HIGH] `confirmation_excerpt` 跨节假放行** | `hand_d9598e9b` Arm B 将 `comparison` 段落拷入 `condition_plan.confirmation_excerpt`，旧 `inspect_excerpt_integrity` / `validate_initial_reply_contract` 未严格拒绝跨节借段与缺少 M 正向要素 | [astra_player_dossier_v1.py](../../tools/astra_player_dossier_v1.py) `WRONG_SECTION` 阻断；[astra_player_review_v1.py](../../tools/astra_player_review_v1.py) 强制 `chk['section'] == key` 与 `j1_excerpt_duplicate_section`；[astra_player_condition_v1.py](../../tools/astra_player_condition_v1.py) 新增 `require_positive_m=True`（短腿+规则+5m 步进）、拓展 `不用` 与 `UTC五分钟网格` | `test_step1_d959_cross_section_regression_and_positive_m_anchor`；离线重评使 `hand_d9598e9b` Arm B 由 `PASSED` 准确翻转为 `CONTRACT_FAILED`，同时 `89df3d02` / `8b126baf` 零误拒 |
| **[MEDIUM] `daily_r16@4` 回滚 `MANUAL_IMPORT` 身份与 `c_degenerates_to_m`** | `daily_r16@4` 显式 `@1.0` 手工导入未保存 `prompt_version`，且 `hand_8b12` Arm A `不接受...按破坏规则处理` 漏判退化 | [astra_player_dossier_v1.py](../../tools/astra_player_dossier_v1.py) 当 `plan_pv is not None` 时保留 `prompt_version`（`daily_r16@2` 保持省略兼容）；[astra_player_review_v1.py](../../tools/astra_player_review_v1.py) 收紧 `不接受...按破坏规则处理` 退化检测并保留 Layer-2 只读边界 | `test_step1_8b12_c_degeneration_and_advisory_boundary`、`test_step1_daily_r16_v4_manual_import_late_and_parse_failed_cli` |
| **[LOW] CLI `LATE`/预解析失败落盘 & 上游 `6311071c` 就绪校验** | 缺失 `<ANNOTATION_JSON>` 或预检异常曾未落盘失败状态、`Dossiers.respond` 错误码需无条件 fail-closed；`evaluate_source_readiness` 需校验 `6311071c` 300s `observed_at_ms` 与 `data_health` `can_use` | [astra_player_dossier_v1.py](../../tools/astra_player_dossier_v1.py) pre-contract 失败落盘 `PARSE_FAILED` + `LATE` 保留 `error`/`error_code` + `eff_error_code=='CONTRACT_VALIDATION_FAILED'` fail-closed；[astra_player_tracking_v13.py](../../tools/astra_player_tracking_v13.py) 增加 `max_discovery_age_ms=300_000` 并融合 `6311071c` `pending_lock` | `test_step1_daily_r16_v4_manual_import_late_and_parse_failed_cli`、`test_step2_6311071c_live_readiness_and_pending_lock_p5_interop` |

- **Reviewer `59cca70d` 保留项（非阻断 LOW，供发布/后续记录）**：
  1. `plan.daily_r16.v3_p5.json` 中 `effective_at_ms` 仍为 `2026-11-23T16:00:00Z`：实际完成 soak 后在发布切换前刷新为切换当日 `16:00Z` 槽位（参考 `v4` 做法），避免中间已过天被标 `MISSED`。
  2. CLI `response` 非 UTF-8 `--raw` 字节、直接编程式调用 `respond()` 传入 `LATE` 时跳过 `cv` 附加（`select()` 已拒选 `LATE`）、`validate_confirmation_prose` 同句重复子句边缘情形均为 `LOW`，不阻塞 RC。

---

## 3. Step 2.3 故障注入、离线重评与冻结字节身份

- **故障注入矩阵（`test_step2_fault_injection_401_403_timeout_lost_receipt_budget_and_isolation` 全过）**：
  - HTTP `401` / `403`（即使 `retries=3`）：立即失败，严格 **1 次调用 / 0 重试**，`attempt.json` 落盘 `REQUEST_FAILED` (`delivery_state='PROVIDER_REJECTED'`)。
  - `TimeoutError` (`retries=0`)：严格 **1 次调用 / 0 重试**，落盘 `TIMEOUT` (`DELIVERY_UNKNOWN`)，`recover_initial()` 与二次 `auto_deal()` **0 重复付费请求**。
  - 回执写入前失败（`on_response` 抛 `OSError`）：`DailyHttpBudget` 扣减 `1` 次并阻断静默重发；8 线程并发抢占 `limit=3` 账本，严格 `3` 成功 / `5` 抛 `DailyBudgetExceeded`，绝不超额。
  - 过期手 + `Reject` + 现役 `daily_r16@3` + 历史正式 A `daily_r16@2` 隔离：历史正式 A 文件字节零变化，0 重复调用。
- **冻结 System Prompt 与静态预算（零漂移）**：
  - `player_initial_system@1.0`: `2,831 B`, `653168703cd80d6ef6a0c0243d8a380bd2c2b8dbf8c76de45045124fb66a88e6`
  - `player_initial_system@1.1-candidate`: `3,306 B`, `98ae4aaae928f5d6dd9914aad2832df6642d0530d4ad0317231a987ea6c83137`
  - **`player_initial_system@1.2-p5-candidate`**: **`3,073 B`**, **`b746c2d2f7f996d290992e01b11d9e4c4792a0e1afcacb900bbf0dfc678b6f34`**（1 字节未改）
- **确定性离线重评与上游快照（`step2_regrade_report.json`，零读取 `outcomes/`）**：
  - H2 (`hand_af89529f`) / H3 (`hand_471b8255`)：`SOURCE_READY`，`legacy_v1` 与 `b3d79695` 逐字节一致，`p0_strict@1` 严格区分 L2 偏移与现货 `OBSERVED_UNALIGNED`。H4 (`hand_0cdc0af5`)：`UPSTREAM_BLOCKED`，88 条无时钟 ARD 行诚实降为 `UNKNOWN`。
  - Nov 23 `16:00Z` 原型牌 (`hand_a050f60e`，早于 `16:49:44Z` `6311071c` 上线)：`legacy_v1` 与现网 `input.md` / `b3d79695` 一致；现网 `@1.0` 原答在 P5 门禁下呈 `CONTRACT_FAILED`（5 处截断 + 有限区间违规 + C→M 退化），且仅计作基线契约差异。
  - `live_xxeu_6311071c_snapshot.json`（抓自现网 `17:00:51Z`）：`SOURCE_READY`，`accept_lock_ready=True`，`discovery_max_age_ms=300_000`；`+301s` 模拟即转 `UPSTREAM_BLOCKED(discovery_observed_stale)`。
