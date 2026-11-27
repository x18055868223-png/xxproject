# xxproject｜LIVE-P0 → LIVE-P3 实盘前准备最终交付报告

> [!WARNING]
> **SUPERSEDED / HISTORICAL SNAPSHOT（Pre-v1.2.0 历史存档）**：本文件记录的是早先 `live-exec-p1-p3@2cbbb3f2` / `core_version=2.0.6` 阶段的阶段性快照，**已被 `live-exec-behavior@1.2.0` 与现行代码完全取代**。其中关于 `User=bitnami` + `SupplementaryGroups=astra-exec`、Workbench 持有 `LoadCredential`、控制面 UID 白名单包含 `bitnami`/`astra-workbench`、`approvals` 逐局审批以及 `deploy/astra_workbench_v3/fixtures/exec_states.json` 的描述均已废止且与现行隔离门禁直接冲突（现行代码禁止 `bitnami`/`astra-workbench` 加入 `astra-exec` 组或接触控制凭据）。**实机部署、运维和验收请一律以 [PRELIVE_VERIFICATION_RUNBOOK.md](PRELIVE_VERIFICATION_RUNBOOK.md)、[60-exec-control.conf](../../deploy/xxproject_server/systemd_units_execution/60-exec-control.conf) 与当前代码为准。**

- **终态裁决**：**`PRELIVE_BLOCKED`**（代码与离线/仿真/安全/发布门禁全部就绪并获独立审查 `PASS`，**禁止进入 P4 真实交易**）
- **权威基线**：`pre-trade-baseline-20261124-3a3f850e`（[server-native-release](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/server-native-release) 保持 `3a3f850e86423ce604ca25ee62e3ad9221fcef97` 干净未动）
- **本地代码冻结提交**：`codex/live-exec-p1-p3` 代码基线 `2cbbb3f299c020186d26e376a4fbf21a31b728b0`（`24 files changed, +12484 / -66`；未 `git push`，未修改 `xx-eu` 生产环境）
- **全局可用性说明**：本交付可直接用于 Testnet 联机闭环与 Linux 主机演练（`deploy_release.py install --with-execution` 默认 `--mode SHADOW_ONLY`），唯因缺少真实 Deribit API 凭证与在网 ITM 两阶段交割样本而阻断在线验收；P4 实盘仍需人工逐项核销下方阻断项与授权清单。

---

## 1. 阻断项与未验证项（先看能否往下用）

| 类别 | 状态 | 具体缺口与当前证据 | 解除条件 |
|---|---|---|---|
| **Testnet 私有下单闭环 (`I11`)** | `BLOCKED` | 本地与 `xx-eu` 均无 `DERIBIT_TESTNET_CLIENT_ID` / `SECRET`。运行 [astra_exec_testnet_verify_v1.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/tools/astra_exec_testnet_verify_v1.py) 连接 `https://test.deribit.com`：公共盘口与阶梯 tick（`958` 个期权、`126 ms` 时钟偏差）通过，但以退出码 `2`（`BLOCKED: missing_testnet_credentials`）退出；指向 `www.deribit.com` 时以退出码 `3` 拒绝。 | 注入隔离 Testnet 子账户 API 密钥后重试 `astra_exec_testnet_verify_v1.py` |
| **生产只读子账户快照 (`I11`)** | `BLOCKED` | 无隔离只读子账户 API 密钥，无法抓拍真实子账户 `positions` / `open_orders` / `tx_log`。 | 提供只读权限子账户 Key 后抓取只读快照 |
| **2026-08-01 在网 ITM 两阶段交割样本 (`R-01`)** | `BLOCKED` | 未获取真实在网到期 ITM `exercise`/`assignment -> delivery` `tx_log` 报文；当前由 [exec_sim_oracle.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/tests/exec_sim_oracle.py) 与 `test_i07_i08` / `test_e2e_5` / `test_reviewer_blocker3_major2...` 完成确定性覆盖。 | 在 Testnet 到期一次 ITM 价差并抓取原始 `tx_log` 对账 |
| **Linux 内核 `SO_PEERCRED` + `systemd` 实机拉起** | `NOT_VERIFIED` | 测试宿主机为 Windows，无法拉起真实 `systemd`；已在源代码中固化 `--with-execution` 预检、UID `0`/`astra-exec`/`bitnami`/`astra-workbench` 放行列表及 `60-exec-control.conf`，需在 Linux 宿主演练机实测。 | 在 Linux 宿主演练机验证 `astra-exec` 用户、`/etc/signal-audit/exec-control.token` 与 `LoadCredential` |

---

## 2. 阶段与业务闭环总门禁表

| 检查对象 | 结论 | 关键证据与覆盖范围 |
|---|---|---|
| **P0 总体设计与合同收敛** | `PASS` | [LIVE_EXECUTION_CONTRACT.md](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/docs/live_execution/LIVE_EXECUTION_CONTRACT.md) v2.1 完整收敛 `R-01..R-09`、`A-01..A-05`、`D-01..D-06`；P0 Gate 审查员 (`cd440ff4`) 返回 `PASS`。（注：`R-07` 实现由草稿 `User=astra-workbench` 收束为在保留 `User=bitnami` + `UMask=0027` 基础上通过 [60-exec-control.conf](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/deploy/xxproject_server/systemd_units_execution/60-exec-control.conf) 增加 `SupplementaryGroups=astra-exec` 与隔离 `LoadCredential`。） |
| **P1 确定性执行内核 (`I01–I08`)** | `PASS` | [test_astra_exec_p1_kernel.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/tests/test_astra_exec_p1_kernel.py) **`11/11` 通过**：涵盖动态阶梯 tick (`>0.005 -> 0.0005`)、分段手续费精确反演 `solve_max_buy_price`、先买后卖对称覆盖、`NAKED_SHORT_BREACH` 告警与自保平仓、`A-01` 保护腿成本与最坏放弃亏损闸门、85% 预挂 GTC TP、有界 `PROTECTED_IOC` 撤销退出、`A-04` 残值处置、两阶段交割、`R-09` 非线性 Put/Call 压力测试及组合故障轨迹。 |
| **P2 协调器、安全隔离与集成 (`I09–I10, I12`)** | `PASS` | [test_astra_exec_p2_integration.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/tests/test_astra_exec_p2_integration.py) **`17/17` 通过**：6 条端到端生命期（全平、AI 撤销、缩容 TP+残仓、建仓中撤销、ITM/OTM 到期、崩溃恢复+`10028` 退避+过期检测）、CSRF/`SameSite`/token 与 peer UID 隔离、`SHADOW_ONLY`/`DRY_RUN` 禁写护栏、未授权 `handle_approve` 不烧号、`BLOCKER #1-#5` 与最终闭环回归均通过。 |
| **P5 不变性与影子隔离 (`R-06`, `A-05`)** | `PASS` | [test_astra_p5_shadow_exit.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/tests/test_astra_p5_shadow_exit.py) **`6/6` 通过**；`git diff --name-only 3a3f850e` 证实 [astra_player_review_v1.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/tools/astra_player_review_v1.py)、`deploy/player_dossier/`、[astra_signal_producer_v1.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/tools/astra_signal_producer_v1.py)、`systemd_units_unified/` 与 `demo/` 均 `0` 修改。[astra_player_tracking_v13.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/tools/astra_player_tracking_v13.py) 将实盘持仓复核预算隔离写入 `live_hold_review_usage.json`，经真实触发 `DailyHttpBudget.reserve(role="astra_player_live_hold_review")` 验证影子配额文件字节级不变。 |
| **Workbench v3 前端与控制 API** | `PASS` | `node --check` 与 `NODE_ESM_EXECUTION_JS_OK`（`facts_age_ms=null` `ago()`、零成交中止中文映射、`execution_control_unavailable`）通过；浏览器子 Agent (`5f1f709e`, `flash`) 在本地临时守护进程 (`http://127.0.0.1:18895`) 验证桌面 `1440x900` 与移动 `390x844` 页面（`revoke_open`/`tp`/`stale`/`residual`），`0` 写请求、`0` 控制台/页面报错（已由 `BROWSER_HOST_FACTS` 核验）。 |
| **发布打包、只读在线核验与健康门** | `PASS` | `py_compile` 全过；`build_release.py` + `deploy_release.py install/verify` 实测基础安装保持 `21` 单元（`verify ok: true`, `core_version: 2.0.6`），`--with-execution` 安装 `22` 单元（锁定 `--mode SHADOW_ONLY`）+ `60-exec-control.conf` + `exec-coordinator.env` 且升级零误备份；[xxproject_server_health_v1.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/tools/xxproject_server_health_v1.py) 覆盖 `DISCONNECTED`/`WRITE_DISABLED` 与关键告警；先期 `xx-eu` SSH 只读核验确认线上仍为 `xxproject-server-3a3f850e8642-5797ba0e28fc`（`2.0.6`, `SHADOW_ONLY`）。 |
| **最终编辑后回归测试全集** | `PASS` | `test_astra_exec_p1_kernel.py`、`test_astra_exec_p2_integration.py`、`test_astra_p5_shadow_exit.py`、`test_xxproject_server_release.py`、`test_astra_workbench_v3_*.py`、`test_astra_unsuitable_wording.py` 合计 **`121 passed in 175.16s`**（`0 failed, 0 skipped`）。 |
| **基线既有无关 UI 测试** | `FAIL (基线同款)` | [test_astra_ui_information_architecture.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/tests/test_astra_ui_information_architecture.py) 测试未修改的旧 `deploy/signal_audit/frontend/`：`2 failed, 5 passed in 0.85s`（与未改动基线 `3a3f850e` 一致，非本次引入）。 |

---

## 3. 核心设计裁决摘要

| 议题 | 最终实现裁决（[astra_exec_contracts_v1.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/tools/astra_exec_contracts_v1.py) `ExecConfig` 等） |
|---|---|
| **`A-01` 建仓双重风控** | 开仓同时校验 `min_net_credit_per_btc`、`max_prot_unit_price_btc`、`max_prot_cost_btc` 与最坏放弃亏损 `max_entry_abort_loss_btc`（可回收多头残值按 `min(prot_unmatched, bid_amount) * bid_price - fee` 保守计价，`EMPTY_SIDE` 记 `0`）。 |
| **`D-01..D-06` 候选参数** | `default_min_credit_floor_btc=0.0008`、`default_min_credit_ratio=0.80`、`approval_ttl_ms=900_000`、`entry_window_ms=90_000`、`max_entry_slice_btc=0.1`、`min_residual_net_btc=0.00005`（且 `min_residual_net_ratio=0.50`）、`min_margin_buffer_ratio=2.5`、`put_stress_spot_factor=0.70`、`revoke_retry_schedule_ms=(2000,5000,10000,15000)` 均为离线/Testnet 候选默认值，待实盘数据标定。 |
| **`R-02` / `R-03` 执行收敛** | `EP/ES/TP/RX/LX` 共享同一命令状态机与 `valid_until = server_ms + 5000ms` 围栏；`Priority 1 ES` 不再被 `long_book.ask_price` 缺失阻断；撤退严格 `RX` 优先、`PROTECTED_IOC` 降维执行、停写锁为单向安全闩锁。 |
| **独立对抗审计与模型披露** | 按仓库规则优先委派子 Agent，因当前运行环境不提供 `.codex/agents/` 指定的 `gpt-5.5/xhigh`，`explorer`/`worker`/`reviewer` 均使用 `Model: "inherit"`、`browser` 使用 `Model: "flash"`（未作静默降级隐瞒）。独立审查员 (`93d387f9`) 历经首轮 `FAIL`（5 BLOCKER / 5 MAJOR / 4 MINOR）-> 二轮 `FAIL`（1 BLOCKER / 1 MAJOR / 2 MINOR）-> 终轮静态重审返回 **`Verdict: PASS`（0 阻塞/主要/次要代码问题残留）**（运行时测试结果由主协调器实跑验证）。 |

<details>
<summary>子 Agent 清单、核心交付物列表与验证命令明细</summary>

- **子 Agent 清单**：
  - `e606fc6d` (`explorer`, `inherit`)：P5 Dossier 与 Workbench 架构摸底
  - `e8c6626e` (`explorer`, `inherit`)：历史执行层与 Deribit 资产审计
  - `7ebe1d49` (`reviewer`, `inherit`)：LIVE-P0 初稿对抗审计
  - `cd440ff4` (`reviewer`, `inherit`)：LIVE-P0 收敛合同 (`v2.1`) 门禁 `PASS`
  - `5bb6d06f` (`worker`, `inherit`)：独立仿真交易所与结算 Oracle ([exec_sim_oracle.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/tests/exec_sim_oracle.py))
  - `96407491` (`worker`, `inherit`)：Workbench v3 实盘驾驶舱前端 ([execution.js](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/deploy/astra_workbench_v3/execution.js), [exec_states.json](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/deploy/astra_workbench_v3/fixtures/exec_states.json))
  - `5f1f709e` (`browser`, `flash`)：Workbench v3 本地浏览器桌面/移动端实机巡检（0 异常 / 0 写请求）
  - `93d387f9` (`reviewer`, `inherit`)：LIVE P1–P3 独立对抗审查（终审 `PASS`）
- **新增/核心修改文件**：
  - 核心执行：[astra_exec_contracts_v1.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/tools/astra_exec_contracts_v1.py)、[astra_exec_store_v1.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/tools/astra_exec_store_v1.py)、[astra_exec_policy_v1.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/tools/astra_exec_policy_v1.py)、[astra_execution_coordinator_v1.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/tools/astra_execution_coordinator_v1.py)、[astra_exec_testnet_verify_v1.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/tools/astra_exec_testnet_verify_v1.py)
  - 控制与跟踪：[astra_workbench_v20.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/tools/astra_workbench_v20.py)、[astra_player_tracking_v13.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/tools/astra_player_tracking_v13.py)、[xxproject_server_health_v1.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/tools/xxproject_server_health_v1.py)
  - 发布与单元：[build_release.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/deploy/xxproject_server/build_release.py)、[deploy_release.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/deploy/xxproject_server/deploy_release.py)、[astra-execution-coordinator.service](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/deploy/xxproject_server/systemd_units_execution/astra-execution-coordinator.service)、[60-exec-control.conf](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/deploy/xxproject_server/systemd_units_execution/60-exec-control.conf)、[exec-coordinator.env.example](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/live-exec-p1-p3/deploy/xxproject_server/systemd_units_execution/exec-coordinator.env.example)

</details>

---

## 4. 进入 P3 在线补验与 P4 人工签字清单

1. **Testnet 联机放行**：配置 `DERIBIT_TESTNET_CLIENT_ID` / `SECRET`，实跑 `astra_exec_testnet_verify_v1.py --base-url https://test.deribit.com`，并在 Testnet 完成至少 1 次分腿建仓 -> 85% TP -> REVOKE/到期两阶段交割对账。
2. **只读主网子账户核对**：提供独立只读 Key，核对真实子账户 `id/username`、`get_positions(currency="BTC")` 与在网 ITM 交割 `tx_log` 样本。
3. **Linux 宿主隔离实测**：确认宿主机创建 `astra-exec` 系统用户与 `/etc/signal-audit/exec-control.token` 后，实测 `deploy_release.py install --apply-units --with-execution` 与 `SO_PEERCRED` 控制面联通。
4. **前端发布门禁**：在本地浏览器打开 Workbench v3 实盘面板并人工确认 `当前本地页面可推送` 后，方可执行前端提交与服务器部署。
5. **独立资本授权**：P4 真实交易需另行获得明确人工资本额度、风控参数标定与生产密钥启用批准；本阶段产出的任何测试默认参数 (`ExecConfig`) 不构成实盘授权依据。
