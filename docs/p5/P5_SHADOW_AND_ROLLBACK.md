# P5 影子豁免风险记录、线上观测与 Formal A 回滚预案（`P5_SHADOW_AND_ROLLBACK`）

**历史记录留存声明**：保留早期 P5 闭环记录中的 **`SHADOW_BLOCKED_OR_INCONCLUSIVE`**（`958f0c58` / `6c945ca3`）、**`READY_BUT_NOT_DEPLOYED`**（`2b7b42be`）以及 **`hand_d9598e9be936b9b777c5295f` Arm B 首次 `CONTRACT_FAILED`**，不改写、不掩盖。

---

## 1. 用户明确接受的两项发布前验证豁免与残余风险

依据用户本轮持续加速发布授权，以下两项前瞻样本量门禁不再阻断首次正式部署，转由上线后真实自然发牌继续观测：

1. **豁免项 1｜`6311071c` 部署后至少两个独立自然发牌周期的先期 soak**：
   - 现状：`Deribit_BTC_20261124T080000Z`（`hand_a050f60ea6bb7d73acf68a3f`）已在 `2026-11-24T08:06:11Z` 完成交割结算并生成 `outcome_38a533146d3ceec6dc23eb7e`（期间共 `0` 个 `@1.2` 自然发牌周期）。
   - 残余风险与缓释：上游发现/`pending_lock` + `@1.2 + p0_strict@1 + p2_strict@1` + 撤销终态退出闭环将在 `2026-11-24T16:00:00Z` 首笔正式自然牌中首次实测；保留 `execution_allowed=false`、独立 C 只读与 `deploy_release.py` 一键无损回滚。
2. **豁免项 2｜发布前补齐至 5–7 对完整双臂影子样本**：
   - 现状：维持此前已执行 **`3` 对**（`6 / 20` 次调用、峰值保守 `¥3.62856`、缓存 `¥3.588174`，剩余影子预算 `14` 次 / `¥16.37144`），未新增付费调用。
   - 严格区分口径：
     - **已执行合格影子对数**：`3` 对；
     - **Arm B 首次契约合格对数**：**`2 / 3`**（`hand_d9598e9b` 维持真实 **`CONTRACT_FAILED`**）；
     - **未实证边界**：未在真实发牌周期实际触发 `AI_REVOKE` 前，**`SHADOW_EXIT_LIVE_VERIFIED` 保持待验证**，历史截图局只读/只追加收敛不得冒称新版本实盘 `REVOKE` 已验证。

---

## 2. 发布后自然业务周期观测与紧急回滚判定

- **禁止重试与篡改**：正式线 `auto_deal` 保持 `retries=0`。若真实首单出现模型 `CONTRACT_FAILED`，必须如实落盘为 `PARSE_FAILED(CONTRACT_VALIDATION_FAILED)`（单次请求上限，不自动补发、不跳过校验）。
- **立即触发回滚的条件**：
  1. 发生安全假放行、Prompt/Input 身份静默降级、重复付费调用、`MISSED`/过期牌补发、撤销后仍继续写入新增网格检查/活跃 MTM、历史卷宗篡改或健康检查持续失败；
  2. 连续自然周期确认存在阻塞性生产回归。

---

## 3. Formal A 一键回滚通道与可执行性校验（禁用 Route D）

- **唯一正式回滚目标**：现网已验证 `xxproject-server-c49fed0dc24a-bf7788fcff5e`（Formal A `daily_r16@2 + @1.0 + legacy_v1`），严禁切换至未验证的 Route D。
- **两层无损回滚路径（严格避开 `07:50–08:35 UTC` 结算窗与 `15:50–16:35 UTC` 发牌窗，即在 `08:35–15:50 UTC` 安全窗口执行；紧急故障时按授权传 `--allow-window-override`）**：
  1. **原子包级一键回滚**：在 `xx-eu` 执行 `python3 /opt/xxproject/current/deploy/xxproject_server/deploy_release.py rollback --apply-units`，直接把 `/opt/xxproject/current` 切回 `/opt/xxproject/releases/xxproject-server-c49fed0dc24a-bf7788fcff5e` 并重装载统一 systemd 单元（不触碰 `/var/lib/xxproject/` 与 `/etc/signal-audit/`，已通过本地升级-校验-回滚全链路预演）；再执行 `xxproject_server_health_v1.py` 核验恢复。
  2. **1 步前向计划回滚**：如只需保留代码而退回 `@1.0` 提示词策略，使用 `deploy/player_dossier/plan.daily_r16.v4_rollback_formal_a.json`（`daily_r16@4`）。回滚后已存在的 `SHADOW_EXITED` 终态与 `stopped` 追踪记录保持静止，不被误重新激活。
