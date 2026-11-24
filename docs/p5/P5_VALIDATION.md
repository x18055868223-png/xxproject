# P5 候选验收与闭环修复验证报告（`P5_VALIDATION`）

**基于基线**：P5 冻结 RC `52264db3cbdf6339bc1245fab8a9fdbd6e730bd7` 合并现网 `xx-eu` 上游 `server-native-release`（`c49fed0dc24a84c46044fa232f361f888854599e`）  
**当前验证结论**：全部不可豁免安全、契约、AI `REVOKE -> SHADOW_EXITED` 终态退出、三档冻结估值、Workbench v3 展示及统一部署/回滚门禁均已通过（独立 Reviewer `ddaed22e` 签署 **`RELEASE_APPROVED`**，`0 CRITICAL / 0 HIGH / 0 MEDIUM`），待在 `08:35–15:50 UTC` 安全窗口原子切换上线。

---

## 1. 本轮最终最小闭环修复根因与改动摘要

- **问题 1 根因（撤销后网格与追踪继续运行）**：现网 `ai_rev_1e923027ba51b1acef76064a` (`rev_be66a6b65fd0c1caa9d4c9dd`) 仅写入 `AI_REVOKE`、`SHADOW_ACTION(REVOKE_RECORDED_NO_QUOTE)` 与 `trigger_state.json["revoked"] = true`，未将原生追踪槽位（`locked_candidates` `status` 仍为 `"active"`）置为终态且 `terminal.json` 仅在到期交割后生成；因此 `sync-open` / `_run_due_checks()`（未检查撤销）与 Workbench 1 分钟采样器持续写入约 177 次 `CONDITION_TRIGGERED` 与实时 `float_pnl_btc`。
- **问题 2 根因（退出 P&L 缺冻结快照且 UI 混入撤销后浮盈亏）**：`evaluate_hold_management()` 仅调用单腿 `short_buyback_quote()` 且 `service('quote')` 在 `astra_workbench_v20.py` 中夹带慢路径 WPEM/`refresh_expiry_gamma(force=True)` 导致 `6,922 ms > MAX_BOOK_AGE_MS (5,000 ms)` 触发 `short_quote_stale_at_revoke`；Workbench v3 `locks.js` `floatView()` 未区分已撤销终态与反事实继续持有，继续把撤销后的 `-0.0222 BTC` 展示在“现在”区；`player-audit.js` 未折叠重复 `CONDITION_TRIGGERED` 行，且后端 `sorted_events[-64:]` 会挤出早期生命周期事件。
- **最小修复实现**：
  1. `tools/astra_player_dossier_v1.py`：在 `EVENTS` 新增不可变 `SHADOW_EXITED`，`_shadow_managed_cashflow()` 保持 `SHADOW_MANAGED`/`UNPRICED_REVOKE` 向后兼容并附带 `SHADOW_EXITED` 元数据与 `counterfactual_hold_to_expiry_net_btc`。
  2. `tools/astra_locked_candidate_store_v1.py` & `tools/astra_workbench_v20.py`：`stop(..., stop_reason="shadow_revoked_terminal")` 幂等停表（不覆盖已 `stopped`/`expired`）；新增限权 `shadow_tracking(action="stop")`（仅允许 `execution_allowed is False` 且 `has_verified_shadow_revoke` 通过的 `ai_shadow` 单局）；`service('quote')` 启用 `fast_quote=True` 跳过慢 WPEM/gamma 刷新；`_lock_outcome` 优先按 `shadow_exit` 冻结层级定性；`_locked_public_proof` 对未鉴权请求剥离内部哈希。
  3. `tools/astra_player_tracking_v13.py`：新增 `build_shadow_exit_snapshot()` 严格三档冻结估值（`EXECUTABLE_TWO_LEG_QUOTE` / `INDICATIVE_MARK_ONLY` / `UNPRICED`，`last_indicative_reference` 严格限定 `0 <= revoke_at_ms - row_asof <= 10m` 零前视）；在 `execution_allowed is False` + `mode=='SHADOW_DECISION'` 下即刻写入 `SHADOW_EXITED` + `tracking/shadow_exit.json` 并停表（实盘 `execution_allowed=True` 仅标 `exit_intent_only=True` 不停监控）；`_run_due_checks()`、`sync()`（到期前静默，到期 `+2m` 后仅一次性归档反事实结算并保持冻结快照不变）与 `sync-open` 彻底停检；`audit_record()` 智能保留非网格关键事件、输出 `grid_check_history` 与 `post_revoke_grid_check_count`；新增只追加幂等 CLI `reconcile-shadow-exit`。
  4. `deploy/astra_workbench_v3/{current-hand,locks,player-audit}.js`：严格对齐后端 `exit_terminal || exit_state === "SHADOW_EXITED"`；主区展示「影子退出（终态）」与三档口径，实时 `float_pnl_btc` 与到期赔付移入独立可折叠「反事实对照：若未撤销持有至到期」；折叠重复 `CONDITION_TRIGGERED` 并保留完整 `<details>` 网格审计表。
  5. `deploy/xxproject_server/` & `tools/xxproject_server_health_v1.py`：统一切换至 `plan.daily_r16.v3_p5.json`（`daily_r16@3`），保持 `plan.daily_r16.v2.json` 与 `plan.daily_r16.v4_rollback_formal_a.json` 回滚兼容。

---

## 2. 确定性回归与预演结果（`Python 3.12.10` + `Node v24.15.0`）

- **31 文件全量影响域回归**（清理前完整日志 [pytest_p5_shadow_exit_full.log](file:///C:/Users/Xu/.gemini/antigravity/brain/28884534-050d-447b-951d-6ad0f1124025/scratch/p5_closeout/pytest_p5_shadow_exit_full.log)）：**`572 passed, 1 skipped, 0 failed in 348.95s`**（`c49fed0d` 已解决原 2 项 D02 基线失败；唯一 `1 skipped` 为缺少外部可选夹具 `test_real_initial_public_view_context_refs_keep_missing_source_clock_none`）。
- **最终收束编辑后重跑（全部修改文件覆盖，零失败）**：
  - `node --input-type=module` ES 模块加载校验（`current-hand.js`, `locks.js`, `player-audit.js`）：`JS_OK`。
  - `pytest tests/test_astra_p5_shadow_exit.py tests/test_astra_workbench_v3_locks.py tests/test_astra_workbench_v3_player_audit.py tests/test_astra_player_tracking_v13.py -q`：**`62 passed in 176.64s`**（涵盖 6 大场景：终态零新增检查+邻局并发、非终态恢复、Put/Call 双腿与防前视 3 档估值、177 检历史截图回放字节零改写、崩溃恢复与到期反事实结算、`execution_allowed=True` 实盘边界与 Node UI）。
  - `pytest tests/test_astra_p5_candidate.py tests/test_astra_player_e2e_v13.py tests/test_astra_audit_minimal_fixes.py tests/test_player_initial_recovery.py tests/test_astra_workbench_v3_readings.py tests/test_astra_locked_followup_frontend.py tests/test_xxproject_server_release.py -q`：**`99 passed in 80.21s`**。
- **本地构建与升级/回滚预演**：`build_release.py`（258 文件、`player_plan=daily_r16@3`、`MANIFEST_MISSING: []`）+ `deploy_release.py` 从 `c49fed0d` 安装 → 升级 → `verify` → `rollback` 退回 `xxproject-server-c49fed0dc24a-bf7788fcff5e` 全程 `exit 0`（`runtime_state_preserved=true, etc_configs_preserved=true`）。

---

## 3. 冻结 Prompt、不可变边界与历史重评留存

- **核心研究契约零漂移**：`tools/astra_player_review_v1.py`、`tools/astra_player_input_v1.py`、`tools/astra_player_condition_v1.py`、`tools/astra_anchor_relative_v1.py`、`deploy/player_dossier/plan.daily_r16.v2.json` 改动 **0 字节**；`player_initial_system@1.2-p5-candidate` 保持 `3,073 B`（SHA-256 `b746c2d2f7f996d290992e01b11d9e4c4792a0e1afcacb900bbf0dfc678b6f34`），`input_profile='p0_strict@1'`、`mechanical_contract_version='p2_strict@1'`、`execution_allowed=false`。
- **历史影子基准保留不掩盖**：`3` 对已执行影子原答在最终解析器下重评维持 **`2 / 3`** 首次契约合格（`hand_8b126baf` 与 `hand_89df3d02` `PASSED`，**`hand_d9598e9b` 保留真实 `CONTRACT_FAILED`**）；`C_EARLY_WARNING_UNPROVEN`；未新增任何付费模型请求。
