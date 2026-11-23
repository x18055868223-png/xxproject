# C｜架构整理、去冗余与权衡说明

**整理原则：** 以减法为主、围绕单一权威生产者收敛，同时守住既有模块的时间语义、安全边界与不可破坏算法契约。

---

## 1. 已实施的结构优化与权衡

| 优化项 | 原状与痛点 | 新设计与位置 | 收益 | 风险与回滚保障 |
| :--- | :--- | :--- | :--- | :--- |
| 跨进程 Deribit 429 协调器 | 信号生产者、Workbench（discovery/quote/expiry_gamma）与 Research Archive 各自限流，任一方撞 429 时其余进程仍在高频打请求，双轨及整点窗口易连锁失败 | 新增 `tools/astra_deribit_cooldown_v1.py`，以原子写 `ASTRA_DERIBIT_COOLDOWN_FILE`（上限 300s）联动 `signal_producer`、`workbench` 与 `research_archive` | 跨进程共享 429 冷却，Discovery 与 `refresh_expiry_gamma` 自动延迟而不强撞上游 | 未设置环境变量时退化为 fail-open no-op；不改变单进程既有缓存与 P0-REST 语义 |
| 统一不可变发布包与代码/状态分离 | 6 套代码散落 `/opt/{astra-*,signal-audit*,gexmonitorapi}`，靠手工 `scp`/零散脚本升级，难以原子回滚 | 新增 `deploy/xxproject_server/{build_release.py,deploy_release.py,install_release.sh}` + `systemd_units_unified/`（19 个 unit）；代码固定在 `/opt/xxproject/releases/<id>` 并由 `/opt/xxproject/current` 符号链接切换 | **253** 个标准化的字节可复现文件包；与 `/var/lib/xxproject/` 及 `/etc/signal-audit/` 完全物理隔离；一键回滚保留全量运行状态 | 当前保留原有 `/opt/*` 目录不动，待 Stage Cutover 验证稳定后再做清理归档 |
| 移除第 3 份冗余 `DataEngine` 存储并保留清理职责 | `astra-data-engine-maintenance.timer` 每 5 分钟在 `/opt/astra-data-engine-v23` 维护第 3 份快照库，但 Workbench 与 MAP 已直接读 `snapshots.jsonl` | 统一单元转为 `astra-log-retention.{service,timer}`，仅调用 `astra_data_retention_v23.py maintain --apply` 冻结/清理 >48h `.gz` | 消除无消费者周期计算与额外磁盘写 | `astra_data_retention_v23.py` 本身算法未动；`snapshots.jsonl` 与 MAP 消费路径更短 |
| 修复发布门禁脚本与版本漂移 | `tools/astra_release_check_v1.py` 硬按原始 CRLF 算哈希且 `release_candidate_manifest.json` 仍指旧版 `2.0.2`，导致跨平台校验失真 | 同时兼容 raw 与 LF 归一化 SHA-256，更新清单对齐 `2.0.6` (`6d9a34e1...`) | Windows/Linux 跨平台校验一致，21 项 RC/发布门禁测试恢复全绿 | 只读校验严格性不降反升 |
| 统一全栈只读健康检查 | 运维需人工拼凑多家日志文件、HTTP 端点与进程检查 | 新增 `tools/xxproject_server_health_v1.py`，一站校验 Producer 健康、`fact_context`、`signal_review` 尾行、GEX API、Workbench、`current_map`、`kpf`、`archive`、`SHADOW_ONLY` 计划与 `RELEASE_MANIFEST` | 单命令产出机器可读巡检 JSON；上线及回滚后均可在 5s 内完成验收 | 纯只读检测，无 LLM 调用、无下单权限 |

---

## 2. 刻意保留的架构边界（不做激进合并的原因）

| 保留项 | 决策 | 理由 |
| :--- | :--- | :--- |
| `demo/最新交付物/neutral_regulation_demo_fmz.py` (`2.0.6`) | 一字未改，原样打入 `signal_core/` | 杜绝迁移窗口引入算法/阈值漂移，保证与生产 BOT `668422` 字节级算法一致 |
| `spm_manual_gate_execution_fmz.py` (`3.0.0-manual-gate`) | **严禁打包进服务器发布包**，留作仓库归档 | 该层深耦合 FMZ 实盘 API（`exchange.IO`/`_G`/`GetCommand`）且现网未启用；隔离剔除是守住 `SHADOW_ONLY` 的硬防线 |
| `deploy/astra_workbench_v3/*` 与 `deploy/kpf_light/vendor/` | 保持原样与严格字节校验 | `workbench-v3` 受前端人工验收门禁保护；`KPF` 依赖 `source_manifest.json` 精确哈希，打包器强制校验不得损坏 |
| `AstraWorkbench` 与 `astra_map_data_server` 独立进程 | 不强行合入单一巨石服务 | 两者拥有不同的数据源失败隔离域与分钟/5分钟周期，合并会扩大单个 HTTP 卡顿的故障半径 |
| 保留 `materialize_signal_cards.py` 文件于发布包（移出必需切换门禁） | 代码保留但 `signal-audit-materialize` 不作为主服务阻断项 | Nginx 已 302 指向 `workbench-v3`，保留 CLI 即可兼容历史卡片查看与随时复算，且经测试持续满足 `SESSION_CONTEXT_REQUIRED=1` |
