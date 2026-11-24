# P5 影子豁免风险记录、线上观测与 Formal A 回滚预案（`P5_SHADOW_AND_ROLLBACK`）

**历史记录留存声明**：保留早期 P5 闭环记录中的 **`SHADOW_BLOCKED_OR_INCONCLUSIVE`**（`958f0c58` / `6c945ca3`）以及 **`hand_d9598e9be936b9b777c5295f` Arm B 首次 `CONTRACT_FAILED`**，不改写、不掩盖。

---

## 1. 用户明确接受的两项发布前验证豁免与残余风险

依据本轮用户最终授权，以下两项前瞻样本量门禁不再阻断首次正式部署，转由上线后真实生产观测继续验证：

1. **豁免项 1｜`6311071c` 部署后至少两个独立自然发牌周期的先期 soak**：
   - 现状：`xx-eu` 上游 `6311071c` 于 `2026-11-23T16:49Z` 上线，截至发布前 `2026-11-24T03:24Z` 尚处于当日 `daily_r16@2` 持仓期（`2026-11-24T08:00Z` 到期），`post-6311071c` 完整自然发牌周期数仍为 `0 / 2`。
   - 残余风险与缓释：上游发现/`pending_lock` 与 `@1.2 + p0_strict@1 + p2_strict@1` 的实盘首单联合表现将在 `2026-11-24T16:00Z` 正式自然发牌中首次承受 E2E 实测；保留 `execution_allowed=false` 与安静窗一键回滚。
2. **豁免项 2｜发布前补齐至 5–7 对完整双臂影子样本**：
   - 现状：仅使用此前已执行 **`3` 对**（`6 / 20` 次调用、峰值保守 **`¥3.62856`**、缓存 `¥3.588174`，剩余未使用影子预算 `14` 次 / `¥16.37144`），未新增付费影子调用。
   - 严格区分的三项口径：
     - **已执行合格影子对数**：`3` 对（均为 `pre-6311071c` 牌：`hand_8b126baf`、`hand_89df3d02`、`hand_d9598e9b`）；
     - **Arm B 首次契约合格对数**：**`2 / 3`**（`hand_d9598e9b` 为真实 **`CONTRACT_FAILED`**，说明模型在 `confirmation_excerpt` 字段上偶发跨节复制仍可能触发单次 `PARSE_FAILED(CONTRACT_VALIDATION_FAILED)` 并拒绝该局）；
     - **盲审质量**：`3/3` 原答文本偏好仅作定性参考，**不能据此宣称长期稳定收益或高首次可执行率已获统计证实**；独立 C 为 **`C_EARLY_WARNING_UNPROVEN`**。

---

## 2. 发布后自然业务周期观测与紧急回滚判定

- **禁止重试与篡改**：正式线 `auto_deal` 保持 `retries=0`。若真实首单出现模型自身 `CONTRACT_FAILED`，必须如实落盘为 `PARSE_FAILED(CONTRACT_VALIDATION_FAILED)`（单次请求上限，不自动补发、不改写回复、不跳过校验）。
- **立即触发回滚的条件**：
  1. 发生安全假放行（如错段摘录/非法规则却 `PASSED`）、Prompt/Input 身份静默降级、重复付费调用、`MISSED`/历史过期牌补发、历史 `@1.0` 卷宗篡改或服务严重异常；
  2. 连续自然周期确认存在系统性契约拒单或阻塞性生产回归。

---

## 3. Formal A 一键回滚通道与可执行性校验（禁用 Route D）

- **唯一正式回滚目标**：现网已验证 **Formal A（`player_initial_system@1.0 + legacy_v1`）**，严禁切换至未验证的 Route D。
- **两层无损回滚措施（均只在 UTC `08:05–15:50` 安静窗执行，严禁 `16:18–08:00 UTC`）**：
  1. **1 步前向计划回滚（保留 `6311071c` 与 32MB 修复）**：将 `/opt/signal-audit-tools/player-plan.json` 指向已随同上传的 [`plan.daily_r16.v4_rollback_formal_a.json`](../../deploy/player_dossier/plan.daily_r16.v4_rollback_formal_a.json)（`daily_r16@4`, `effective_at_ms=1795622400000` / `2026-11-25T16:00:00Z`，如在更晚日期执行则顺延至当日 `16:00Z`），自动跳过已结算 `daily_r16@3` 历史槽位，已在 `test_step1_daily_r16_v4_manual_import_late_and_parse_failed_cli` 与 `test_expedited_shadow_export_32mb_cap_and_cutover_schedule_zero_missed` 中实证 `MANUAL_IMPORT`、`accepted()` 与 `episode()` 全通。
  2. **完整快照备份还原**：切换前在 `xx-eu` `/var/backups/xxproject/pre_p5_<UTC_TS>/` 完整备份 `6311071c` 原 6 个 `.py` 与 `player-plan.json`（`daily_r16@2`）及 `sha256sum` 清单；还原文件后重启 `astra-workbench-v20.service` 即可恢复原状，历史 P5 卷宗只读保留。
