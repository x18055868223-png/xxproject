# D｜统一服务器发布、切换与安全回滚操作手册（Runbook）

**适用发布包：** `.artifacts/server-native-release/xxproject-server-<shortsha>.tar.gz`（含 `RELEASE_MANIFEST.json`、`signal_core/`、`tools/`、`deploy/xxproject_server/systemd_units_unified/` 等 253 项标准化文件；零 FMZ 机器人与零密钥/状态数据）。

---

## 1. 上线前置安全锁与宿主机前提

1. **禁切时段：** 避开 **07:50–08:35 UTC**（每日 settle 窗口）与 **15:50–16:35 UTC**（每日 AI Player `daily_r16@2` 正式发牌锁定窗口）。确认 `systemctl is-active astra-player-deal.service` 返回 `inactive`。
2. **只读与影子边界：** `astra_signal_producer_v1.py` 代码内强制锁定 `read_only_demo=True`（不可通过环境变量开启实盘）；`/opt/xxproject/current/deploy/player_dossier/plan.daily_r16.v2.json` 固定 `mode="SHADOW_ONLY"`, `execution_allowed=false`。
3. **停 FMZ 再跑原生：** 正式接管时须**先停 `xxproject-fmz-node-69494805.service` 再启 `astra-signal-producer.service`**，避免双进程同 IP 竞争 Deribit `public/ticker` 配额触发 429。
4. **GEX 运行时环境（宿主机前提）：** `gexmonitorapi.service` 使用宿主机虚拟环境 `/opt/xxproject-runtime/venv/bin/python`（已于 `xx-eu` 验证存在；若在新主机部署需提前建置，实机 `--apply-units` 安装会自动校验该文件存在）。

---

## 2. 目录规划与环境接驳（首次切换一次性准备）

- **代码 root（只读符号链接切换）：** `/opt/xxproject/{releases/<id>,current,previous,deployments.jsonl}`
- **数据/状态 root（持久不随回滚丢失）：** `/var/lib/xxproject/{signal_native/logs,signal_native/state,workbench,research_archive,research_inbox,llm}`；MAP 状态目录维持 `/opt/astra-map-data/state`；KPF 目录维持 `/var/lib/astra-kpf-light`。
- **配置与密钥（不被发布包覆盖）：** `/etc/signal-audit/{signal-producer.env,workbench-v20.env,llm.env}`、`/etc/gexmonitorapi.env`、`/etc/astra-kpf-light.env`。
  - **关键环境变量核查清单：**
    1. **`/etc/signal-audit/workbench-v20.env`：** 将 `ASTRA_WORKBENCH_SNAPSHOTS_JSONL` 指向 `/var/lib/xxproject/signal_native/logs/snapshots.jsonl`；同步清理或更新 `ASTRA_WORKBENCH_FRONTEND_DIR` 指向 `/opt/xxproject/current/deploy/astra_workbench_v3`（且统一 unit `ExecStart` 已通过 `/usr/bin/env` 强制覆写以防旧值劫持）；确认 `ASTRA_WORKBENCH_STATE_DIR` 与 `ASTRA_WORKBENCH_USAGE_LEDGER` 均落在 `/var/lib/xxproject/workbench` 或 `/var/lib/xxproject/llm`（受 systemd `ReadWritePaths` 约束）。保留 `/etc/signal-audit/llm.env` 原地有效。
    2. **`/opt/astra-map-data/config.json`：** 将 `snapshots_jsonl` 同指 `/var/lib/xxproject/signal_native/logs/snapshots.jsonl`。
    3. **`/etc/signal-audit/signal-producer.env`：** 设置 `ASTRA_SIGNAL_LOGS_DIR=/var/lib/xxproject/signal_native/logs`、`ASTRA_SIGNAL_STATE_DIR=/var/lib/xxproject/signal_native/state`、`ASTRA_DERIBIT_COOLDOWN_FILE=/var/lib/xxproject/workbench/deribit_cooldown.json` 与 `ASTRA_SIGNAL_GEX_INFO_URL=http://127.0.0.1:8000`。

---

## 3. 正式部署与切换步骤（预计 5–10 分钟）

```bash
# 1) 备份切换前宿主机 systemd unit 与 drop-in（用于 Scenario B 全量退回）
sudo install -d -m 755 /var/lib/xxproject/backups/pre_cutover_units
sudo cp -a /etc/systemd/system/{gexmonitorapi,astra-*,signal-audit-*,xxproject-*}.* /var/lib/xxproject/backups/pre_cutover_units/ || true

# 2) 上传并解包部署器，执行 preflight（含 /opt/xxproject-runtime/venv 检查）
PKG=/tmp/xxproject-server-<shortsha>.tar.gz
mkdir -p /tmp/xx-deploy && tar -xzf "$PKG" -C /tmp/xx-deploy deploy/xxproject_server
python3 /tmp/xx-deploy/deploy/xxproject_server/deploy_release.py preflight --package "$PKG"

# 3) 安装不可变发布树与 19 个统一 systemd unit，并验证
sudo python3 /tmp/xx-deploy/deploy/xxproject_server/deploy_release.py install --package "$PKG" --apply-units --no-systemd
sudo python3 /tmp/xx-deploy/deploy/xxproject_server/deploy_release.py verify

# 4) 初始化 native 目录（ReadWritePaths 要求）并从 bot 668422 只读拷贝历史连续性状态
sudo install -d -o bitnami -g bitnami /var/lib/xxproject/signal_native/logs /var/lib/xxproject/signal_native/state /var/lib/xxproject/workbench
sudo cp -n /var/lib/xxproject-fmz-node-69494805/logs/storage/668422/demo/logs/{snapshots.jsonl,signal_review.jsonl,space_gate_state.json,fixed_analysis_round_state.json} /var/lib/xxproject/signal_native/logs/ || true
sudo chown -R bitnami:bitnami /var/lib/xxproject/signal_native

# 5) 停 FMZ 与冗余 data-engine-maintenance，切流启动 server-native 全栈
sudo systemctl stop xxproject-fmz-node-69494805.service && sudo systemctl disable xxproject-fmz-node-69494805.service
sudo systemctl stop astra-data-engine-maintenance.timer astra-data-engine-maintenance.service || true
sudo systemctl disable astra-data-engine-maintenance.timer || true
sudo systemctl daemon-reload
sudo systemctl restart gexmonitorapi.service
sudo systemctl enable --now astra-signal-producer.service
sleep 15
sudo systemctl restart astra-workbench-v20.service
sudo systemctl enable --now astra-map-data.timer astra-kpf-light.timer astra-research-archive.timer \
  astra-log-retention.timer astra-player-{prewarm,deal,sync,settle}.timer
```

---

## 4. 切换后验收命令

```bash
# 1) 信号生产者与发布完整性自检
python3 /opt/xxproject/current/tools/astra_signal_producer_v1.py --state-dir /var/lib/xxproject/signal_native/state --health-check
python3 /opt/xxproject/current/deploy/xxproject_server/deploy_release.py verify

# 2) 全栈只读健康检查（须带 --require-workbench-auth 并用 sudo 读 0700 /etc/signal-audit，验证 strategy_version=2.0.6 与 execution_allowed=false）
sudo python3 /opt/xxproject/current/tools/xxproject_server_health_v1.py \
  --signal-logs-dir /var/lib/xxproject/signal_native/logs \
  --signal-state-dir /var/lib/xxproject/signal_native/state \
  --workbench-env /etc/signal-audit/workbench-v20.env \
  --release-dir /opt/xxproject/current \
  --require-workbench-auth \
  --require-release-manifest
```

---

## 5. 快速回滚方案（< 2 分钟，零数据丢失）

- **场景 A｜服务器代码版本回滚（v2 -> v1，不退回 FMZ）：**
  ```bash
  sudo python3 /opt/xxproject/current/deploy/xxproject_server/deploy_release.py rollback --apply-units
  sudo systemctl restart astra-signal-producer.service astra-workbench-v20.service
  python3 /opt/xxproject/current/deploy/xxproject_server/deploy_release.py verify
  ```
- **场景 B｜极端情况退回 FMZ Bot `668422`：**
  1. 停止并禁用 `astra-signal-producer.service`。
  2. 如需完全退回切换前旧目录（`/opt/signal-audit-tools`, `/opt/gexmonitorapi` 等），将 `/var/lib/xxproject/backups/pre_cutover_units/` 下旧 unit 与 drop-in 恢复至 `/etc/systemd/system/` 并执行 `sudo systemctl daemon-reload`。
  3. 若切换期间已有新增 `snapshots.jsonl`/`signal_review.jsonl` 行且需延续审计序列，按需将其追加/同步回 `/var/lib/xxproject-fmz-node-69494805/logs/storage/668422/demo/logs/` 并维持属主 `xxproject-fmz-worker:xxproject-fmz-worker`。
  4. 将 `/etc/signal-audit/workbench-v20.env` 与 `/opt/astra-map-data/config.json` 恢复指向 `/var/lib/xxproject-fmz-node-69494805/logs/storage/668422/demo/logs/snapshots.jsonl`。
  5. 执行 `sudo systemctl restart astra-workbench-v20.service gexmonitorapi.service && sudo systemctl enable --now xxproject-fmz-node-69494805.service`。
