# P5 候选验收与复杂度审计报告（`P5_VALIDATION`）

**基于基线**：现网 `xx-eu` 上游 `6311071c887433efb5a9b754290a1d8050caa0d8`（合并提交 `5e237a87`）  
**加速发布候选审计状态**：**全部不可豁免安全、契约、故障注入与兼容门禁通过；2 项前瞻样本量门禁依据用户最新授权豁免并登记为残余风险，准备进入独立发布裁决与 UTC 安静窗（`08:05–15:50`）直接切换。**

---

## 1. 确定性全量回归实测（`Python 3.12` + `jsonschema 4.26.0`，零付费 LLM）

- **P5 候选对抗专项套件**（`tests/test_astra_p5_candidate.py`）：
  - `pytest tests/test_astra_p5_candidate.py -q` → **`39 passed in 15.45s, 0 failed, 0 skipped`**。
  - 新增 `test_expedited_shadow_export_32mb_cap_and_cutover_schedule_zero_missed` 验证 `tracking.MAX_BYTES == 32_000_000`（解决现网 `astra-player-sync.service` 对 `hand_a050f60e` 报 `TRACKER_EXPORT_UNAVAILABLE / shadow_evidence_size_limit` 问题并保持超出上限 fail-closed）以及 `2026-11-24T08:15Z` 切换 **0 `MISSED` / 0 历史补发** 与 `2026-11-25T16:00Z` `daily_r16@4` 回滚。
- **24 文件完整影响域回归**（[pytest_p5_expedited_full.log](file:///C:/Users/Xu/.gemini/antigravity/brain/28884534-050d-447b-951d-6ad0f1124025/scratch/p5_closeout/pytest_p5_expedited_full.log)）：
  - **`514 collected -> 511 passed, 2 failed, 1 skipped, 0 errors` in `195.70s`**。
- **同环境干净 `6311071c` 对照复现与 1 项跳过说明**：
  1. 在未改动的 `.worktrees/deribit-rest-pacing`（`6311071c`）同解释器下运行 `pytest tests/test_astra_anchor_relative_v1.py tests/test_astra_ard_condition_v1.py -q`：**`2 failed, 44 passed in 2.81s`**，失败项与断言位置（`test_astra_anchor_relative_v1.py:275` 与 `test_astra_ard_condition_v1.py:123`）完全相同，源于 D02 默认启用 `unverified_minute_label` 后 `build_input()` 输出 `合格图表末点66,739.4 + 双as-of核验` 与早期 Nov 15 fixture `指数66,767.6` 文本差异；`test_real_frozen_h2_h3_h4_sources_and_legacy_b3d79695_parity` 已验证 `legacy_v1` 与 `b3d79695` 逐字节一致。
  2. `1 skipped`：`tests/test_astra_locked_candidate_compare_v1.py:387`（缺少可选本地外部夹具 `.artifacts/locked_candidate_followup_20261008_01/initial_public_view.json`）。

---

## 2. 既有缺陷台账与本轮最小可靠性收口

1. **历史首轮与双臂加固均已完成**（详见 `c39b3d27` 记录）：
   - `a1e642d7` 的 12 项与 `b420a936` 发现的 **[HIGH] `WRONG_SECTION` 跨节假放行**（`hand_d9598e9b` Arm B 准确转拒）、**[MEDIUM] `daily_r16@4` `MANUAL_IMPORT` 元数据与 `c_degenerates_to_m`**、**[LOW] CLI `LATE`/`PARSE_FAILED` 落盘与 `6311071c` 300s 发现 TTL / `data_health` 校验**全部在代码中闭环，并经 `59cca70d` 独立确认 `0 CRITICAL / 0 HIGH`。
2. **本轮发布前最小可靠性增补**：
   - 将 [astra_player_tracking_v13.py](../../tools/astra_player_tracking_v13.py) 第 38 行 `MAX_BYTES` 从 `8_000_000` 提升至 `32_000_000`（对齐 `codex/server-native-release` 并直接消除 `xx-eu` 上 `hand_a050f60e` 实测 `shadow_evidence_size_limit` 故障；不改动上游工作线文件）。
   - 将 [`plan.daily_r16.v3_p5.json`](../../deploy/player_dossier/plan.daily_r16.v3_p5.json) `effective_at_ms` 拨至本次安静窗切换后的首个真实槽位 `1795536000000`（`2026-11-24T16:00:00Z`），并将 [`plan.daily_r16.v4_rollback_formal_a.json`](../../deploy/player_dossier/plan.daily_r16.v4_rollback_formal_a.json) 拨至 `1795622400000`（`2026-11-25T16:00:00Z`），避免产生历史 `MISSED` 或补发过期牌。

---

## 3. 冻结 Prompt、故障注入与离线重评真相（不掩盖真实失败）

- **Prompt 与规则零漂移**：`player_initial_system@1.2-p5-candidate` 保持 `3,073 B`、SHA-256 **`b746c2d2f7f996d290992e01b11d9e4c4792a0e1afcacb900bbf0dfc678b6f34`**；`input_profile='p0_strict@1'`、`mechanical_contract_version='p2_strict@1'`、`execution_allowed=false`。
- **故障注入全通**（`tests/test_astra_p5_candidate.py`）：HTTP `401/403`（即使 `retries=3`）仅调用 1 次；`TimeoutError` (`retries=0`) 落盘 `TIMEOUT`/`DELIVERY_UNKNOWN` 且 `recover_initial` 零重复付费；`on_response` 崩溃计费 1 次无静默重发；`DailyHttpBudget` 8 线程并发严格封顶；`daily_r16@2` 省略兼容与 `daily_r16@4` `@1.0` `MANUAL_IMPORT` / `episode()` 通过；`PENDING_LOCK` → `DELAYED_LOCK` 跨候选正常衔接。
- **3 对已有影子原答在最终解析器下重评**（[step2_regrade_report.json](file:///C:/Users/Xu/.gemini/antigravity/brain/28884534-050d-447b-951d-6ad0f1124025/scratch/p5_closeout/step2_regrade_report.json)，零读取 `outcomes/`）：
  - **已执行合格影子对数：`3` 对**（均为 `pre-6311071c` 牌，`6/20` 调用 / 峰值保守 `¥3.62856`）。
  - **首次契约合格对数（Arm B）：`2 / 3`**（`hand_8b126baf` `Accept/put` 与 `hand_89df3d02` `Accept/put` 通过；**`hand_d9598e9b` 维持真实 `CONTRACT_FAILED`**，因 `confirmation_excerpt` 复制了 `comparison` 触发 `WRONG_SECTION` + `j1_excerpt_duplicate_section` + `missing_m_mechanical_anchor`，严禁将其改称已通过）。
  - **判断质量区分**：Arm A `@1.0` 交叉评估 `0/3` 仅反映旧 Prompt 契约差异，不记作 `@1.0` 择侧失败；小样本盲评 `3/3` 偏好亦不解读为已证实的长期收益；独立 C 维持 **`C_EARLY_WARNING_UNPROVEN`**。
