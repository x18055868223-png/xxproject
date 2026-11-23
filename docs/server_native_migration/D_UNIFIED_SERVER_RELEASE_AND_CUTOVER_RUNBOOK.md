# D｜统一服务器发布、切换、回滚与 Pre-Trade 封版手册（Runbook）

**唯一权威分支：** `xxproject/codex/server-native-release`  
**可复现发布包：** `.artifacts/server-native-release/xxproject-server-<shortsha>.tar.gz`（内置 `RELEASE_MANIFEST.json`、**21 个统一 `systemd` unit**、`signal_core/`、`tools/`、`deploy/`；零 FMZ 机器人、零实盘脚本、零密钥与运行数据）。

---

## 1. 上线前置安全锁与宿主机前提

1. **禁切交易与结算窗：** 避开 **07:50–08:35 UTC**（官方到期 settle）与 **15:50–16:35 UTC**（每日 AI Player `daily_r16@2` 发牌与锁定窗）；`deploy_release.py` 已内置 UTC 窗保护拒绝实机热切（仅紧急回滚可显式 `--allow-window-override`）。确认 `systemctl is-active astra-player-deal.service` 返回 `inactive`。
2. **只读与影子边界：** `astra_signal_producer_v1.py` 强制锁定 `read_only_demo=True`；`deploy/player_dossier/plan.daily_r16.v2.json` 锁定 `mode="SHADOW_ONLY"`, `execution_allowed=false`。
3. **先停 FMZ 再启原生：** 正式接管须先停 `xxproject-fmz-node-69494805.service` 再启 `astra-signal-producer.service`，避免双进程同 IP 竞争 Deribit `public/ticker` 配额触发 429。
4. **宿主前置（GEX Python 环境）：** `gexmonitorapi.service` 依赖 `/opt/xxproject-runtime/venv/bin/python`；`deploy_release.py install --apply-units` 会在修改 symlink/unit 前自动预检该文件。

---

## 2. 目录规划与环境接驳（首次切换准备）

- **不可变代码 root：** `/opt/xxproject/{releases/<id>,current,previous,unit_backups,deployments.jsonl}`（`chmod -R a+rX /opt/xxproject`，供 `bitnami`/`research` 服务只读执行）。
- **持久化运行状态：** `/var/lib/xxproject/{signal_native/logs,signal_native/state,workbench,research_archive,research_inbox,llm}`；MAP 保持 `/opt/astra-map-data/state`；KPF 保持 `/var/lib/astra-kpf-light`。
- **宿主机配置与密钥（不被发布包或回滚覆盖）：**
  1. **`/etc/signal-audit/signal-producer.env`：** 设置 `ASTRA_SIGNAL_LOGS_DIR=/var/lib/xxproject/signal_native/logs`、`ASTRA_SIGNAL_STATE_DIR=/var/lib/xxproject/signal_native/state`、`ASTRA_DERIBIT_COOLDOWN_FILE=/var/lib/xxproject/workbench/deribit_cooldown.json` 与 `ASTRA_SIGNAL_GEX_INFO_URL=http://127.0.0.1:8000`。
  2. **`/etc/signal-audit/workbench-v20.env`：** `ASTRA_WORKBENCH_SNAPSHOTS_JSONL=/var/lib/xxproject/signal_native/logs/snapshots.jsonl`；`ASTRA_WORKBENCH_FRONTEND_DIR=/opt/xxproject/current/deploy/astra_workbench_v3`；确保状态与账本落在 `/var/lib/xxproject/{workbench,llm}`；保留 `/etc/signal-audit/llm.env` 原地有效。
  3. **`/opt/astra-map-data/config.json`：** 将 `snapshots_jsonl` 改指 `/var/lib/xxproject/signal_native/logs/snapshots.jsonl`。
  4. **`/etc/signal-audit/signal-audit-materialize.env`：** 设置 `JSONL_SOURCE=/var/lib/xxproject/signal_native/logs/signal_review.jsonl`、`OUTPUT_DIR=/opt/signal-audit`、`SESSION_CONTEXT_REQUIRED=1`（与 `systemd_units_unified/signal-audit-materialize.service` 及 `deploy/signal_audit/signal-audit-materialize.env.example` 对齐）。

---

## 3. 正式部署与切换命令（预计 5–10 分钟）

```bash
# 1) 备份切换前宿主机 unit 与配置
sudo install -d -m 755 /var/lib/xxproject/backups/pre_cutover_units
sudo cp -a /etc/systemd/system/{gexmonitorapi,astra-*,signal-audit-*,xxproject-*}.* /var/lib/xxproject/backups/pre_cutover_units/ || true

# 2) 解包部署器并执行 preflight
PKG=/tmp/xxproject-server-<shortsha>.tar.gz
mkdir -p /tmp/xx-deploy && tar -xzf "$PKG" -C /tmp/xx-deploy deploy/xxproject_server
python3 /tmp/xx-deploy/deploy/xxproject_server/deploy_release.py preflight --package "$PKG"

# 3) 安装不可变发布树、备份并清理旧 drop-in、安装 21 个统一 unit、开放只读执行权限并验证
sudo python3 /tmp/xx-deploy/deploy/xxproject_server/deploy_release.py install --package "$PKG" --apply-units --no-systemd
sudo chmod -R a+rX /opt/xxproject
sudo python3 /opt/xxproject/current/deploy/xxproject_server/deploy_release.py verify

# 4) 初始化目录并将 bot 668422 历史连续性日志只读迁入原生目录
sudo install -d -o bitnami -g bitnami /var/lib/xxproject/signal_native/logs /var/lib/xxproject/signal_native/state /var/lib/xxproject/workbench
sudo install -d -m 755 /opt/signal-audit
sudo cp -n /var/lib/xxproject-fmz-node-69494805/logs/storage/668422/demo/logs/{snapshots.jsonl,signal_review.jsonl,space_gate_state.json,fixed_analysis_round_state.json} /var/lib/xxproject/signal_native/logs/ || true
sudo chown -R bitnami:bitnami /var/lib/xxproject/signal_native

# 5) 停 FMZ 与退役冗余 unit（data-engine-maintenance / observation-canary），切流启全栈
sudo systemctl stop xxproject-fmz-node-69494805.service && sudo systemctl disable xxproject-fmz-node-69494805.service
sudo systemctl disable --now astra-data-engine-maintenance.timer astra-data-engine-maintenance.service || true
sudo systemctl disable --now xxproject-observation-canary.timer xxproject-observation-canary.service || true
sudo systemctl daemon-reload
sudo systemctl restart gexmonitorapi.service
sudo systemctl enable --now astra-signal-producer.service
sleep 15
sudo systemctl restart astra-workbench-v20.service
sudo systemctl enable --now astra-map-data.timer astra-kpf-light.timer astra-research-archive.timer \
  astra-log-retention.timer signal-audit-materialize.timer astra-player-{prewarm,deal,sync,settle}.timer
```

---

## 4. 切换后验收命令

```bash
python3 /opt/xxproject/current/tools/astra_signal_producer_v1.py --state-dir /var/lib/xxproject/signal_native/state --health-check
python3 /opt/xxproject/current/deploy/xxproject_server/deploy_release.py verify --expected-core-version 2.0.6
sudo python3 /opt/xxproject/current/tools/xxproject_server_health_v1.py \
  --signal-logs-dir /var/lib/xxproject/signal_native/logs \
  --signal-state-dir /var/lib/xxproject/signal_native/state \
  --workbench-env /etc/signal-audit/workbench-v20.env \
  --release-dir /opt/xxproject/current \
  --require-workbench-auth \
  --require-release-manifest
```

---

## 5. 快速回滚方案（< 2 分钟，零状态丢失）

- **场景 A｜服务器代码版本回滚（`v2 -> v1`，保持原生运行）：**
  ```bash
  sudo python3 /opt/xxproject/current/deploy/xxproject_server/deploy_release.py rollback --apply-units
  sudo systemctl restart astra-signal-producer.service astra-workbench-v20.service
  python3 /opt/xxproject/current/deploy/xxproject_server/deploy_release.py verify
  ```
- **场景 B｜紧急退回 FMZ Bot `668422`：**
  1. 停止并禁用 `astra-signal-producer.service`。
  2. 如需完全还原切换前 unit/drop-in，将 `/var/lib/xxproject/backups/pre_cutover_units/` 恢复至 `/etc/systemd/system/` 并 `sudo systemctl daemon-reload`。
  3. 如切流期间产生新增审计行且需衔接 FMZ 序列，将增量追加至 `/var/lib/xxproject-fmz-node-69494805/logs/storage/668422/demo/logs/`（属主 `xxproject-fmz-worker:xxproject-fmz-worker`）。
  4. 将 `/etc/signal-audit/workbench-v20.env`、`signal-audit-materialize.env` 与 `/opt/astra-map-data/config.json` 改回指向 bot `668422` 日志目录。
  5. 执行 `sudo systemctl restart astra-workbench-v20.service gexmonitorapi.service && sudo systemctl enable --now xxproject-fmz-node-69494805.service`。

---

## 6. Pre-Trade 封版核对与未来实盘执行边界契约（`E1–E3`）

- **当前冻结定位：** 除正式停用 FMZ 切流窗口与未来实盘下单接入外，**软件基座功能与架构已经收敛闭环**；生产环境完成上述 Section 3 切换与 Section 4 验收并通过一个完整 `08:00Z -> 16:00Z -> 08:00Z` 周期后，即可升级标记 **`PRE_TRADE_BASELINE_FROZEN`**。
- **未来实盘执行边界硬约定（严禁混层）：**
  1. **只作为独立消费端接入：** 实盘执行层仅能消费已冻结的 AI Player 输出契约（`decision.json` / 合约绑定四腿 / 净信用与破坏阈值），**严禁写入** `Signal Producer`、`Workbench UI` 路由或 AI Player Prompt/选择标准。
  2. **独立风控闸门与硬件隔离：** 必须经过独立风控闸门（最大仓位、保证金上限、滑点/最小净信用门槛、重复下单幂等键 `request_key`、全局 Kill Switch）后，方可通过独立实盘密钥配置文件发单；`SHADOW_ONLY` 配置继续作为发布默认边界。
  3. **准入前提：** 实盘执行模块接入前必须单独完成离线确定性回放、交易所测试网/小额 dry-run 及断网超时幂等验收，不得借本次服务器原生化暗开执行权限。
