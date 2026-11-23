# D｜统一服务器发布、切换与安全回滚操作手册（Runbook）

**适用发布包：** `.artifacts/server-native-release/xxproject-server-<shortsha>.tar.gz`（含 `RELEASE_MANIFEST.json`、`signal_core/`、`tools/`、`deploy/xxproject_server/systemd_units_unified/` 等 253 项标准化文件；零 FMZ 机器人与零密钥/状态数据）。

---

## 1. 上线前置安全锁（硬门禁）

1. **禁切时段：** 避开 **07:50–08:35 UTC**（每日 settle 窗口）与 **15:50–16:35 UTC**（每日 AI Player `daily_r16@2` 正式发牌锁定窗口）。确认 `systemctl is-active astra-player-deal.service` 返回 `inactive`。
2. **只读与影子边界：** 切换后保持 `ASTRA_SIGNAL_READ_ONLY=1` 及 `/opt/xxproject/current/deploy/player_dossier/plan.daily_r16.v2.json`（`mode="SHADOW_ONLY"`, `execution_allowed=false`）。
3. **停 FMZ 再跑原生：** 正式接管时须**先停 `xxproject-fmz-node-69494805.service` 再启 `astra-signal-producer.service`**，避免双进程同 IP 竞争 Deribit `public/ticker` 配额触发 429。

---

## 2. 目录规划与环境接驳（首次切换一次性准备）

- **代码 root（只读切换）：** `/opt/xxproject/{releases/<id>,current,previous,deployments.jsonl}`
- **数据/状态 root（持久不随回滚丢失）：** `/var/lib/xxproject/{signal_native/logs,signal_native/state,workbench,research_archive,research_inbox,llm}`（首次上线可按需保留 `/var/lib/astra-map-data/state`、`/var/lib/astra-kpf-light` 或迁移后做目录衔接）
- **配置与密钥（不被发布包覆盖）：** `/etc/signal-audit/{signal-producer.env,workbench-v20.env,llm.env}`、`/etc/gexmonitorapi.env`、`/etc/astra-kpf-light.env`。
  - **关键环境变量调整：**
    1. 将 `/etc/signal-audit/workbench-v20.env` 中的 `ASTRA_WORKBENCH_SNAPSHOTS_JSONL` 指向 `/var/lib/xxproject/signal_native/logs/snapshots.jsonl`。
    2. 将 `/opt/astra-map-data/config.json` 中 `snapshots_jsonl` 同指 `/var/lib/xxproject/signal_native/logs/snapshots.jsonl`。
    3. 确保 `signal-producer.env` 设置 `ASTRA_SIGNAL_LOGS_DIR=/var/lib/xxproject/signal_native/logs`、`ASTRA_SIGNAL_STATE_DIR=/var/lib/xxproject/signal_native/state`、`ASTRA_DERIBIT_COOLDOWN_FILE=/var/lib/xxproject/workbench/deribit_cooldown.json` 与 `ASTRA_SIGNAL_GEX_INFO_URL=http://127.0.0.1:8000`。
    4. 确保目录属主为 `bitnami:bitnami`（或 `research_archive` 保持 `bitnami:research`）。

---

## 3. 正式部署与切换步骤（预计 5–10 分钟）

```bash
# 1) 上传并解包部署器到临时目录后执行 preflight
PKG=/tmp/xxproject-server-<shortsha>.tar.gz
mkdir -p /tmp/xx-deploy && tar -xzf "$PKG" -C /tmp/xx-deploy deploy/xxproject_server/deploy_release.py
python3 /tmp/xx-deploy/deploy/xxproject_server/deploy_release.py preflight --package "$PKG"

# 2) 安装至 /opt/xxproject/releases/<id> 并安装 19 个统一 systemd unit（--no-systemd 先核验配置）
sudo python3 /tmp/xx-deploy/deploy/xxproject_server/deploy_release.py install --package "$PKG" --apply-units --no-systemd
sudo python3 /opt/xx-deploy/deploy/xxproject_server/deploy_release.py verify

# 3) 首次历史连续性预载（从现役 bot 668422 只读拷贝历史 jsonl 到 native logs_dir，避免首日卡片/快照断档）
sudo install -d -o bitnami -g bitnami /var/lib/xxproject/signal_native/logs /var/lib/xxproject/signal_native/state
sudo cp -n /var/lib/xxproject-fmz-node-69494805/logs/storage/668422/demo/logs/{snapshots.jsonl,signal_review.jsonl} /var/lib/xxproject/signal_native/logs/
sudo chown bitnami:bitnami /var/lib/xxproject/signal_native/logs/*.jsonl

# 4) 停 FMZ，停旧 data-engine-maintenance，重载并切换为 server-native 单元
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

# 2) 全栈只读健康检查（零 LLM / 零交易，退出码 0 即全绿）
sudo -u bitnami python3 /opt/xxproject/current/tools/xxproject_server_health_v1.py \
  --signal-logs-dir /var/lib/xxproject/signal_native/logs \
  --signal-state-dir /var/lib/xxproject/signal_native/state \
  --workbench-env /etc/signal-audit/workbench-v20.env \
  --release-dir /opt/xxproject/current \
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
- **场景 B｜极端情况退回 FMZ Bot `668422`（需在观察过渡期保留 Node `69494805` 镜像但禁用自启）：**
  1. 停止 `astra-signal-producer.service`。
  2. 将 `/etc/signal-audit/workbench-v20.env` 与 `/opt/astra-map-data/config.json` 恢复指向 `/var/lib/xxproject-fmz-node-69494805/logs/storage/668422/demo/logs/snapshots.jsonl`。
  3. 执行 `sudo systemctl restart astra-workbench-v20.service && sudo systemctl enable --now xxproject-fmz-node-69494805.service`。
  4. 确认 `/opt/xxproject/releases/` 与 `/var/lib/xxproject/` 状态完好即可。
