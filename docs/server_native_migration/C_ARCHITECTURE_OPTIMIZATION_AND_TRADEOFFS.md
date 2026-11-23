# C｜架构优化、问题修复与取舍说明

**整理原则：** 仅修复真实 `systemd` 与生产链路上暴露的实质缺陷，围绕单一权威发布与五域清晰边界做减法；拒绝无证据收益的重构与过度工程化。

---

## 1. 已实施的结构优化与真实缺陷修复

| 优化 / 修复项 | 触发痛点与根因 | 新设计与改动位置 | 收益与实证结果 |
| :--- | :--- | :--- | :--- |
| **修复 `shadow_evidence_size_limit` 阻塞导出** | 生产卷宗实测（`2026-11-23 16:20Z`）`/shadow-tracking/export` 打包 `point` + `latest_point` + 3,000 样本 `points`（~15 MB 级别）超出旧上限 `8_000_000`，导致 `TRACKER_EXPORT_UNAVAILABLE` | `tools/astra_player_tracking_v13.py` 将 `MAX_BYTES` 提升至 `32_000_000`；新增超 `8 MB` 载荷回归单测 | `xx-eu` 实测 `6.73 MB` 导出一次通过；回归单测全绿 |
| **补齐 `21` 统一单元与移除第 3 份冗余 `DataEngine`** | 旧版仅打包 19 unit，漏掉生产活跃的 `signal-audit-materialize.{service,timer}`，且 `astra-research-archive` 未显式锁定回环地址 | 新增 `systemd_units_unified/signal-audit-materialize.{service,timer}` 与 `signal-audit-materialize.env.example`（总数 **21 unit**）；`astra-research-archive.service` 显式 `--workbench http://127.0.0.1:8895`；停用零读者 `astra-data-engine-maintenance` 与旧 `observation-canary` | 统一包覆盖全部现役工作负载，消除重复快照写与跨源路径漂移 |
| **强化发布器安全锁 (`deploy_release.py`)** | 旧版 `--apply-units` 先改 symlink 再检查宿主 Python、无 UTC 交易窗保护，且宿主机 `.service.d/*.conf` drop-in 可静默覆盖统一 unit | 新增 `_validate_systemd_prereqs`（改 symlink 前预检 `/opt/xxproject-runtime/venv/bin/python`）、`_validate_deploy_window`（拦截 `07:50–08:35Z` / `15:50–16:35Z`，可选 `--allow-window-override`）、安装单元前将旧 `*.d/*.conf` 备份迁移至 `/opt/xxproject/unit_backups/<release_id>/` | 杜绝半截安装与历史 drop-in 幽灵覆写；升级/回滚单命令完成 |
| **修复 KPF 健康检查误报 `kpf_stale`** | `astra-kpf-light` 按闭日 UTC 每日发版一次（常有 10–30h 正常龄期），而 `xxproject_server_health_v1.py` 默认 `max_kpf_age_ms=1_800_000` (30m) | 默认 `max_kpf_age_ms` 调整为 `172_800_000`（48h，低于 `data_product_registry_v23.json` 的 7 天 TTL），附带 12h 通过 / 72h 拦截单测 | `xx-eu` `v2` 全栈体检无须人工加参数即一次通过 **8/8 `ok: true`** |
| **修复 `refresh_quote` 错误掩码并接入跨进程 429 冷却** | `astra_workbench_v20.py` 在 `_capture_candidate` 返回 `snapshot=None`（如 `DERIBIT_HTTP_429`）时将其笼统掩盖为 `"contract_mismatch"`，且 `_default_quote_capture` 未前置读取共享冷却 | `refresh_quote` 在 `not isinstance(snap, Mapping)` 时透传真实 `error` / `capture_gaps[0]`；`_default_quote_capture` 入口先查 `deribit_cooldown.remaining_ms` | 429 故障清晰归因且跨进程 (`signal_producer` / `workbench` / `research_archive`) 协同退避 |
| **对齐 `anchor_relative` 基线测试口径** | `5a43b21d`/`b3d79695` 启用 `chart_points_audit` 后将 `14:00:00Z` 未核边界排除（`239/240` 分钟），导致旧测试字符串断言滞后；此前 `core` 无改动 | 保留 `docs/rulebook/.../163ff9b3` 冻结文档原样；更新 `test_astra_anchor_relative_v1.py` 与 `test_astra_ard_condition_v1.py` 明确校验 `163ff9b3 -> 5a43b21d` 演进与零前视字节一致 | 恢复全绿且锁死“零未来泄漏”底线 |

---

## 2. 刻意拒绝的过度优化与保留边界

| 拒绝合并项 | 决策 | 理由 |
| :--- | :--- | :--- |
| **`neutral_regulation_demo_fmz.py` (`2.0.6`) 与 `spm_manual_gate_execution_fmz.py`** | 前者一字未改打入 `signal_core/`；后者**严禁打包** | 防止策略阈值漂移；手工执行脚本深耦合 `exchange.IO`/`_G` 且现网禁实盘，物理剔除即硬件级 `SHADOW_ONLY` 保险丝 |
| **将 `Workbench` 与 `map-data`、`kpf-light` 合并为单进程** | 保持独立 `systemd` 服务/定时器 | 三者故障域与生命周期（常驻 / 5m / 日级历史回补）完全不同，合并只会放大单点卡顿半径 |
| **直接重写或删改 `deploy/astra_workbench_v3/*` 与 `kpf_light/vendor/`** | 原样保持字节校验 | `workbench_v3` 受前端人工确认门禁约束；`kpf/vendor` 依赖 `source_manifest.json` 精确校验和 |
| **修改 `signal_review` V21 旧测常量 (`tests/test_signal_evidence_v21_materializer.py`)** | 不在此次迁移范围盲改 | V22/V23 物化器与 `SESSION_CONTEXT_REQUIRED=1` 测试已独立全绿，无需扩大 diff |
