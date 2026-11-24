# B｜迁移等价性、真实 `systemd` 实验与可靠性验收报告

**阶段裁决：`READY_FOR_CUTOVER`**（已完成核心等价、合流 `6311071c` 回归、`xx-eu` 隔离 `systemd` 真实环境全链路与跨版本热升级/回滚验收，沙盒已完全清理且现网生产零扰动；正式切流停 FMZ 按文档 D 待独立切换指令执行）。

---

## 1. 算法与契约保真机制

`tools/astra_signal_producer_v1.py`（`astra-signal-producer@1.0.0`）直接加载未修改的 `neutral_regulation_demo_fmz.py`（`2.0.6`，LF SHA-256 `6d9a34e1d0cd4d11fa4d6c8f8edae9165d9190beb672fafc63e3dfd9c8072132`），保留全部中性回路算法、时钟与审计状态机：
- **安全只读底线：** 强制 `read_only_demo=True`（任何 `False` 覆写抛出 `READ_ONLY_DEMO_REQUIRED`）。`EvaluationSnapshot` 仅增设顶层 `producer_identity`（`platform="xxproject_server_native"`, `fmz_platform_used=False`），严格保持 `clock_provenance`、factor 结构与 `_audit_integrity` `record_hash` 口径。
- **持久化与原子写：** `gex_info_cache.json` 与 `macro_factor_cache.json` 采用临时文件+`fsync`+`os.replace` 原子写；启动自动清理 `signal_review.jsonl` 残缺尾行与全损 `snapshots.jsonl`。

---

## 2. 离线确定性等价与故障恢复矩阵

| 验证项目 | 覆盖条件 | 实测结果 |
| :--- | :--- | :--- |
| 核心等价 (`DemoRuntime` vs `NativeSignalRuntime`) | 720 根 1m K 线回补、5m 稳态节流、SpaceGate 切换、NR 状态机、23:00 BJT (`15:01Z`) 固定轮 | `test_astra_signal_producer_v1.py` 全通；因子误差 `<= 1e-9`，`clock_provenance`、`SignalSessionPremiseDurabilityContext@1.0.0` 与 `record_hash` 100% 一致 |
| 分窗检查点恢复 | 短重启（`<=10m` 恢复 `bars_tail` + `cvd_hist` + `nr_context` + `gex_state`）、中停机（`10m–24h` 触发回补）、超期（`>24h` 防重发） | 3 分支全绿；同日重启不重复输出 23:00 固定轮卡片 |
| 429 跨进程熔断 | `SIGTERM` 保存检查点、Binance/GEX 全断网存活、Deribit 429 触发 `ASTRA_DERIBIT_COOLDOWN_FILE` | 冷却期内 `Workbench` discovery/`refresh_expiry_gamma`/`_default_quote_capture` 与 `research_archive` 均短路拦截，`0` 次多余 HTTP |
| 下游全链路 E2E | `NativeSignalRuntime` -> `fact_context` -> `wpem` -> `map_data` -> `workbench` -> `research_archive` -> `AI Player` -> `materialize_signal_cards` (`SESSION_CONTEXT_REQUIRED=1`) | `test_server_native_e2e_chain.py` 通过；`actual_execution_net_btc=None`（`SHADOW_ONLY`），`compat_backfill_applied` 非 `True` |

---

## 3. 代码回归测试汇总（含本轮修复与基线对齐）

- **Post-Merge `6311071c` 核心与周边界测试：**
  - `pytest -q tests/test_xxproject_server_release.py tests/test_server_native_e2e_chain.py tests/test_astra_signal_producer_v1.py tests/test_astra_player_tracking_v13.py tests/test_astra_anchor_relative_v1.py tests/test_astra_ard_condition_v1.py`：**97 passed (83.30s)**（含 `32 MB` export、48h KPF 窗、`refresh_quote` 错误透传、deploy window 守卫、drop-in 备份与 `5a43b21d`/`b3d79695` `anchor_relative` `239/240` 无前视字节一致断言）。
  - `test_astra_player_dossier_v1`, `test_astra_player_input_v1`, `test_astra_player_e2e_v13`, `test_astra_audit_minimal_fixes`, `test_astra_research_archive_v1`, `test_astra_workbench_v20`, `test_astra_release_check_v1`：**127 passed**。
  - FMZ 算法与物化器套（`test_fmz_204..206`, `cleanup_v244`, `signal_session_context_deploy_assets`, `demo/tests/*`, `materialize_signal_cards`）：**67 passed** + 6 个独立脚本（`test_fmz_157_{fixed_analysis_round,producer_contract}`, `161`, `162`, `20`, `23`）**6/6 PASS**。（注：未动文件 `tests/test_signal_evidence_v21_materializer.py:69` 存有与现役 `2.3.0` 不符的历史常量 `'2.2.0'`，`v22` 测试已覆盖并全绿）。

---

## 4. `xx-eu` 真实 Linux `systemd` 隔离部署与全链路实验（`2026-11-23`）

在 `xx-eu` 宿主机 `/var/lib/xxproject-exp/root` 搭建 **20 个瞬态 `systemd` 单元**（`/run/systemd/system/xxproject-exp-*.{service,timer}`，`systemd-analyze verify` 退出码 `0`），绑定独立回环端口 `127.0.0.1:18895`，`LLM_API_KEY=` 置空（**0 次付费 LLM 调用**），在不触碰现网 FMZ Bot `668422` 及生产 unit 的前提下完成全量真机验收：

| 验收项 | 实测过程与关键输出 | 结论 |
| :--- | :--- | :--- |
| **1. 原生信号生产 & 物化** | `xxproject-exp-signal-producer.service` (`Type=notify`, `active (running)`) 写入 `snapshots.jsonl` 与 `signal_review.jsonl`（`fmz_platform_used=false`, `binance=OK, deribit=OK, gex=OK`）；`xxproject-exp-signal-audit-materialize.service` 产出原生 `2.0.6` 卡片（`written_cards=1, skipped_lines=0`, `SignalSessionPremiseDurabilityContext=True`, `clock_window=True`, `backtest_delta_pp=True`, `validation_basis=True`, `temporal_durability=True`, `compat_backfill_applied=None`） | **PASS** |
| **2. Market & Data + Workbench 鉴权** | `xxproject-exp-map-data.service` 生成 `2,991,312`B `current_map.json`（`fmz.status="current"`）；`xxproject-exp-kpf-light.service`（基于生产只读闭日快照副本）输出 `status="READY"`, `version_id="kpf-light-v1-dcfa04af30fc3cbc5768"`；`:18895` 未带/错 Token 调 `/api/workbench/{case-file,locked-candidates,shadow-tracking/readiness}` 均返回 `HTTP 401`，有效 Token 返回 `200`（`execution_allowed=false`） | **PASS** |
| **3. AI Player 发牌 -> `PENDING_LOCK` -> 自动 `DELAYED_LOCK` -> 3.70/6.73 MB 导出 -> 结算** | 对实时双边候选 `BTC-24NOV26-60500-P/58500-P` 与 `62000-C/64000-C` 注入确定性 `Accept/Put` 回复，触发真实 `60m PENDING_LOCK`；`xxproject-exp-workbench-v20.service` 后台 `retry_pending_shadow_locks()` 自动恢复为 `state="SHADOW_LOCK_CREATED"`, `lock_mode="DELAYED_LOCK"`, `delayed_lock=true`；`xxproject-exp-player-sync.service` 正常生成 `OBSERVATION` (`Result=success`)；`POST /shadow-tracking/export` 成功返回 **3,699,987 B（`v1`, 9 点）** 与 **6,732,180 B（`v2`, 24 点）**（验证 `MAX_BYTES = 32 MB` 彻底消除 `shadow_evidence_size_limit`）；`xxproject-exp-player-settle.service` 与 `prewarm` 调度护栏均按预期工作 | **PASS** |
| **4. `SIGKILL` 崩溃自愈** | `OLD_PID=2188651` 被 `systemctl kill -s SIGKILL` 后 10s 内拉起 `NEW_PID=2189793` (`active (running)`)；`producer_health.json` 记录 `checkpoint_restored: {restored: true, bars_restored: true, cvd_hist_4h_len: 25, gex_effective_restored: true, nr_context_restored: true, age_ms: 68669}` | **PASS** |
| **5. 实机升级 `v1 -> v2` 与原子回滚 `v2 -> v1`** | `deploy_release.py install pkg_v2` 切换符号链接（`V2_WB_PID=2189895` 进程 `cwd` 切换至 `v2`），不加 `--max-kpf-age-ms` 覆写即通过 `xxproject_server_health_v1.py` **全 8 项 `"ok": true`**（含 `kpf_light` 与 255 文件 manifest 校验）；随后写入 `v2_evidence_marker.json` 并执行 `rollback`，PID 重启回 `v1`（`V1_RB_WB_PID=2190758`），状态、`tracking/link.json`、`v2_evidence_marker.json` 与 `/etc/signal-audit/*.env` 100% 完整保留，8 项健康检查再次全部 `"ok": true` | **PASS** |
| **6. `C1` Live FMZ Bot `668422` 并行对照** | 对比两机最新 `EvaluationSnapshot`（时间戳差 `22.72s`：`1795462140245` vs `1795462117526`）：`schema_version` (`nrd.schema.v1.0.0`)、`demo_version` (`2.0.6`)、`factor_snapshot` 顶层键、`space_gate.state` (`STRUCTURE_BLOCKED`) 与 `anchor.effective_flip_point` (`61401.92153934354`) 精确相等；`fact_context.latest_from_jsonl` 返回 `status="current"`, `gap_reasons=[]`, `space_gate_provenance="producer_native"` | **PASS** |
| **7. 沙盒彻底清理与现网保护核验** | 停止并删除全部 `/run/systemd/system/xxproject-exp-*` 与 `/var/lib/xxproject-exp`（`SANDBOX_DIR_REMOVED=true`, `SANDBOX_UNITS_REMOVED=true`）；确认现网 `astra-workbench-v20.service`、`xxproject-fmz-node-69494805.service`、`gexmonitorapi.service`、`astra-map-data.timer`、`astra-player-sync.timer`、`signal-audit-materialize.timer` 全部持续 `active` | **PASS** |

---

## 5. `C2` 正式切换放行条件回执

- **当前状态：** 所有技术验证、等价性对照与 `systemd` 真实升级回滚均已通过（**`READY_FOR_CUTOVER`**）。
- **未切原因：** 遵循阶段授权边界，**正式停用 FMZ Bot `668422` 并接管生产 `/opt/xxproject/current` 需单独明确的上线切换指令**，且须避开 `07:50–08:35 UTC`（结算）与 `15:50–16:35 UTC`（发牌）保护窗口。
