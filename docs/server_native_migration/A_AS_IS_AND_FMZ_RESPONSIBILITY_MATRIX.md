# A｜现役架构基线与 FMZ 职责清单（As-Is Matrix）

**裁决结论：** `xx-eu` 当前生产基线为 commit `607d66fe`（`codex/deribit-discovery-reliability`）。全系统仅有 1 个现役 FMZ 进程（`xxproject-fmz-node-69494805.service`，托管 bot `668422` 运行只读信号核心 `neutral_regulation_demo_fmz.py` v`2.0.6`）；仓库中的人工执行层（`spm_manual_gate_execution_fmz.py` v`3.0.0-manual-gate`）及相关 FMZ bridge/worker **未启用且零生产消费者**。

---

## 1. 当前生产基线与代码一致性

| 层级 | 生产实际（`xx-eu` `169.58.177.126`） | 验证结果 |
| :--- | :--- | :--- |
| Git 基线 | `607d66fe111fcae67f05a82cced9347e67058847`（含 Deribit REST 调频、负缓存与配对修复） | 本地 worktree 与生产 backup `/var/lib/xxproject/backups/p0_rest_20261123_607d66fe` 一致 |
| 运行时工具 | `/opt/signal-audit-tools/` 下 **117/121** 文件与 `607d66fe` SHA-256 精确一致 | 其余 4 个离线脚本路径与 `deploy/player_dossier/production_manifest.json` 一致 |
| GEX 接口 | `/opt/gexmonitorapi/current/src/gexmonitorapi/` 与 `05_GEX监控API_数据增强接口/src/gexmonitorapi/` 一致 | `gexmonitorapi.service` 由宿主机 `/opt/xxproject-runtime/venv/bin/python` 托管；`127.0.0.1:8000/health` 返回 `{"status":"ok","version":"0.2.1"}` |
| 现役 FMZ 信号 | `xxproject-fmz-node-69494805.service` -> `/var/lib/xxproject-fmz-node-69494805/logs/storage/668422/demo/logs/` | `neutral_regulation_demo_fmz.py` `2.0.6`，`read_only_demo=true` |
| 未启用 FMZ 资产 | `spm_manual_gate_execution_fmz.py`、`xxproject-fmz-node-115167519`、`xxproject-fmz-bridge` | 服务未运行，`tools/` 与 `deploy/` 零消费者；保持静态归档边界 |

---

## 2. 现役服务与定时器总览（25 loaded units / 11 active timers）

- **常驻服务（3 个）：** `gexmonitorapi.service`（8000 环回）、`xxproject-fmz-node-69494805.service`（待退役）、`astra-workbench-v20.service`（8895）。
- **周期定时器（11 个）：** `astra-map-data.timer`（5m）、`astra-kpf-light.timer`（5m）、`astra-data-engine-maintenance.timer`（5m）、`astra-research-archive.timer`（10m）、`astra-player-{prewarm,deal,sync,settle}.timer`（每日 15:52 / 16:02–17 / 每 5m / 08:06–31 UTC）、`signal-audit-materialize.timer`、`xxproject-observation-canary.timer`、`xxproject-certbot-renew.timer`。
- **分散安装目录（6 处）：** `/opt/gexmonitorapi`、`/opt/signal-audit-tools`、`/opt/signal-audit`、`/opt/astra-map-data`、`/opt/astra-kpf-light`、`/opt/astra-data-engine-v23`；配置与密钥位于 `/etc/signal-audit/`、`/etc/gexmonitorapi.env`、`/etc/astra-kpf-light.env`。

---

## 3. FMZ 与相关生产职责清单

| 功能模块 | 当前生产者 | 实际消费者 | 周期与时钟/新鲜度要求 | 输入数据源 | 输出契约 | 状态恢复与处置裁决 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 现货成交/量柱/CVD/Anchor/TMV-F/M-DIE/NR/SpaceGate/EDB/Skew/Macro | Bot `668422` (`neutral_regulation_demo_fmz.py` 2.0.6) | `workbench`, `map-data`, `wpem` | ~60s tick；`snapshots` <=600s 新鲜；精确 7 键 `clock_provenance` | Binance 现货/合约、Deribit 公开期权、`127.0.0.1:8000`、`macro` | `EvaluationSnapshot`（写入 `snapshots.jsonl`） | 保留算法本体；由 `astra_signal_producer_v1.py` 托管（含 1m K 线回补与检查点） -> **原生迁移** |
| 原生审计卡与 23:00 BJT 固定轮 | Bot `668422` (`build_audit_record`) | `signal_llm_review` / `materialize_signal_cards` | NR 确认事件 & 23:00–23:05 BJT | 因子截面 + `SignalSessionPremiseDurabilityContext@1.0.0` | `signal_review.jsonl` + `integrity.record_hash` | 持久化 `seen_episode_ids`/`seen_fixed_round_slots` 防重复发卡 -> **原生迁移** |
| FMZ 宿主接口 (`Log`/`Status`/`Chart`/Push) | FMZ Node `69494805` 进程 | 无业务消费者（仅人工看面板） | 每轮 tick | 内存日志与图表 | stdout / FMZ 面板 | systemd stdout -> `journald` + `producer_health.json` 供健康检查 -> **退役 FMZ 依赖** |
| 状态面板、图表与进程守护 | FMZ Node `69494805` | 运维观测 | 连续守护 | 进程信号与看门狗 | 进程存活与退出状态 | `systemd Type=notify` (`WatchdogSec=300`) + `sd_notify` + `SIGTERM` 保存检查点 -> **原生替代** |
| Deribit 公开行情限流保护 | 各进程独立限流，无跨进程协调 | `signal`, `workbench`, `research_archive` | 实时（429 易在并发窗口放大） | Deribit REST 响应 | 各进程私有内存状态 | 抽象共享 `astra_deribit_cooldown_v1.py` 跨进程协同退避 -> **架构整合** |
| 多生产者市场发现/报价/Gamma 墙 | `astra-workbench-v20` + `astra-map-data` + `astra-kpf-light` | `research_archive`, `ai_player`, `workbench-v3` | 常驻 / 5m 周期 | `snapshots.jsonl` + Deribit + 链上/外部数据 | `/api/workbench`, `current_map.json`, `kpf/current.json` | 保留既有服务器原生角色，纳入统一 release 发布树 -> **保留复用** |
| 快照保留与过期 `.gz` 归档清理 | `astra-data-engine-maintenance.service` | 仅磁盘维护（第 3 份 `DataEngine` 存储无读者） | 5m | `logs/*.jsonl.gz` | 冻结归档，清理 >48h 旧包 | 拆出重复 `DataEngine` 存储；保留 `astra_data_retention_v23.py` 为 `astra-log-retention.{service,timer}` -> **架构减法** |
| 旧版静态卡片物化页面 | `signal-audit-materialize.service` | Nginx `/signal-audit/` 现已 `302` 重定向到 `workbench-v3` | 周期运行 | `signal_review.jsonl` | `/opt/signal-audit/signal_cards/*.json` | 算法保留在 release 包内（兼容历史/按需手动执行），但不再作为核心主链路依赖 -> **降级旁路** |
| 人工执行与交易门控 (`3.0.0-manual-gate`) | 仓库源码（未部署运行） | 零消费者（`SHADOW_ONLY`, `execution_allowed=false`） | N/A | N/A | N/A | 强耦合 `exchange.IO`/`_G`；严禁打包进生产包，留作历史源码隔离 -> **隔离退役** |
