# xxproject 执行层 Linux 隔离验收与受控主网实盘验收清单

> 权威实盘路线与缺口清单见 [LIVE_EXECUTION_ACCESS_PLAN.md](LIVE_EXECUTION_ACCESS_PLAN.md)；行为合同以 `live-exec-behavior@1.2.0` 与当前源码为准（Testnet 已退出路线）。  
> **当前状态**：`SHADOW_ONLY`（已上线 `4f97732b`；**真实交易写权限关闭**，未完成主网 `LIVE` 适配与正式主网 API Key 准入核验前，严禁在生产启用非 `SHADOW_ONLY` 模式）。

**唯一执行路线**：**`阶段 1：自然影子轮取证` → `阶段 2：准备 Deribit 主网接入` → `阶段 3：受控金额实盘验证`**。

---

## 1. Linux 主机 UID / Group 与双令牌准备

在目标 Linux 主机执行（全部执行级密钥均由 `root` 持有并通过 systemd `LoadCredential` 或 `0600 root:root` 环境文件注入，不得写入 `workbench-v20.env`、`llm.env` 或 `research.env`）：

```bash
# 1) 创建独立系统用户与共享组（禁止 bitnami 或 astra-workbench 加入 astra-exec 或共享 UID）
sudo groupadd --system research || true
sudo useradd --system --no-create-home --shell /usr/sbin/nologin astra-exec || true
sudo useradd --system --no-create-home --shell /usr/sbin/nologin astra-workbench || true
sudo usermod -aG research bitnami || true
sudo usermod -aG research astra-exec
sudo usermod -aG bitnami,research astra-workbench

# 2) 生成与 ASTRA_WORKBENCH_TOKEN 互不相同的 32B 控制令牌
sudo install -d -m 0750 /etc/signal-audit
openssl rand -hex 32 | sudo tee /etc/signal-audit/exec-control.token >/dev/null
sudo chmod 0600 /etc/signal-audit/exec-control.token
sudo chown root:root /etc/signal-audit/exec-control.token

# 3) 配置协调器环境文件（ASTRA_EXEC_ALLOWED_PEER_UIDS 留空以严格使用 {0, astra-exec}）
#    进入阶段 2/3 主网接入前，经安全通道在此填入主网 ASTRA_EXEC_ENV_TAG、ASTRA_EXEC_SUBACCOUNT_ID 与专用子账户 Key
sudo cp deploy/xxproject_server/systemd_units_execution/exec-coordinator.env.example /etc/signal-audit/exec-coordinator.env
sudo chmod 0600 /etc/signal-audit/exec-coordinator.env
sudo chown root:root /etc/signal-audit/exec-coordinator.env

# 4) 确认内核 ptrace 隔离开启（>= 1）
sudo sysctl -w kernel.yama.ptrace_scope=1
```

---

## 2. 发布包构建、原子部署与 Linux 反向安全验收

```bash
# 构建单一不可变发布包（默认拒绝脏工作树，必须干净提交后再打正式包）
python3 deploy/xxproject_server/build_release.py --output /tmp/xxproject_release.tar.gz

# 提取新版 deploy_release.py 并安装 21 基线 + 执行隔离单元（默认 --mode SHADOW_ONLY）
mkdir -p /tmp/xxp_deploy_runner
tar -xzf /tmp/xxproject_release.tar.gz -C /tmp/xxp_deploy_runner deploy/xxproject_server/deploy_release.py
sudo python3 /tmp/xxp_deploy_runner/deploy/xxproject_server/deploy_release.py install \
  --package /tmp/xxproject_release.tar.gz \
  --apply-units \
  --with-execution

# 幂等校准存量案卷权限（仅限 shadow_dossiers，2750/0640 bitnami:research）
sudo python3 /opt/xxproject/current/deploy/xxproject_server/deploy_release.py repair-dossier-perms

# 校验发布树清单、文件摘要与 SHADOW_ONLY 不变量
sudo python3 /opt/xxproject/current/deploy/xxproject_server/deploy_release.py verify

# 若启用 --with-execution 后需回退至无执行层的基线，必须使用新版 deploy_release.py 执行回滚：
sudo python3 /opt/xxproject/current/deploy/xxproject_server/deploy_release.py rollback --apply-units

# 验证 9 项 OS / Confused-Deputy / 跨 UID 共享读写（含新写案卷跨账号可读性）门禁
sudo bash /opt/xxproject/current/deploy/xxproject_server/systemd_units_execution/linux_security_verify.sh

# 运行 SHADOW_ONLY 统一健康检查（含执行层 public_status.json + systemd + /api/workbench/execution/live）
sudo python3 /opt/xxproject/current/tools/xxproject_server_health_v1.py --require-systemd --require-workbench-auth --require-release-manifest --require-execution --expected-exec-mode SHADOW_ONLY
```

验收通过标志：输出 `ALL_LINUX_SECURITY_CHECKS_PASSED`（9/9）且健康自检返回 `"ok": true`，确认：
- `astra-workbench`（非 `bitnami`）运行 `astra-workbench-v20.service`，`astra-exec` 运行 `astra-execution-coordinator.service`（`mode=SHADOW_ONLY`, `writes_enabled=false`, `cockpit_mode=READ_ONLY`）；
- `ASTRA_WORKBENCH_TOKEN` 可读 `GET /api/workbench/execution/live`（HTTP 200），调用 `POST /api/workbench/execution/*` 均返回 `403`；
- `bitnami` 与 `astra-workbench` 对 `/run/astra-exec/control.sock`、`exec_store.sqlite`、`exec-control.token`、`/proc/<exec_pid>/environ` 均被拒绝；同时 `bitnami` 新写的案卷对 `astra-workbench` 与 `astra-exec`（`research` 组）可读不可写，`/var/lib/xxproject/execution/public_status.json`（`0644`）保持只读可见。

---

## 3. 现行三阶段实盘接入验收清单

### 阶段 1：自然影子轮取证（`SHADOW_ONLY`）
- [x] 本地 136 项核心与跨模块回归、`xx-eu` Linux 9/9 安全自检、`repair-dossier-perms` 与真实 `astra-workbench`/`astra-exec` D28 读取通过（`4f97732b`）。
- [ ] 在 `16:00 UTC` 自然牌局证实端到端影子闭环：`Accept` 落盘 → `tracking/link.json` 成功回执 → 5 分钟巡检与持有复评 → `08:00 UTC` 到期结算。
- [ ] 解除 `/etc/signal-audit/workbench-v20.env` 的 `LLM_TIMEOUT_SEC=360` 及 `hold_timeout_s = min(timeout_s, 360.0)` 封顶。

### 阶段 2：准备 Deribit 主网接入
- [ ] **准入信号**：面向受控实盘的正式主网 API Key 与独立受控子账户通过 `root:root 0600` 安全配置通道写入目标环境，并完成只读身份、余额、SM/PM 模式、`disable_cancel_on_disconnect` 与 `0` 外部持仓/挂单核验（回执仅记配置与可用状态，不输出密钥）。
- [ ] 补齐主网代码改造并经独立审查通过：`ALLOWED_MODES` 新增受控 `LIVE` 模式、`validate_endpoint_for_mode` 与 `_allow_private_writes` 严格绑定 `https://www.deribit.com` + 非空子账户/DCOD、健康自检 `--expected-exec-mode` 支持 `LIVE`、`execution.js` 停写推断覆盖 `LIVE`（遵守 `当前本地页面可推送` 门禁）及宿主机受控 systemd drop-in 方案。

### 阶段 3：受控金额实盘最小验证（固定 `0.1 BTC`、`MAX_EPISODES = 1`）
- [ ] **下单 (`EP -> ES`)**：实测主网保护腿先买 `IOC` -> 实证覆盖 -> 短腿再卖 `IOC`、动态 `tick_size_steps`、`C_min >= 0.0008 BTC/BTC`、`0.0060 BTC/BTC` 单腿硬封顶与不可逆 `entry_closed`。
- [ ] **成交与止盈 (`TP`)**：实测 `USER_TRADE`/`TX_LOG` 按 `trade_id` 去重入账、封口即挂唯一 `85% GTC BUY (reduce_only=true)`（波动期静默不动），及 TP 成交后 `diff_btc == "0"`。
- [ ] **撤单与主动退出 (`CANCEL / AI_REVOKE / EXIT_ALL`)**：实测 `cancel_by_label` 清退在途单/TP -> `RX IOC (reduce_only=true)`（遇 `10009` 现金墙保持保护腿锁定并亮红灯）-> 短腿归零后 `LX` 或 `RESIDUAL_HOLD`。
- [ ] **仓位与重启恢复**：实测每拍 `positions/open_orders/order_history/user_trades` 一致性，及协调器重启后写锁、控制标志和 `UNKNOWN` 零重复下单恢复。
- [ ] **到期交割对账 (`OTM` / 2026-08-01 `ITM` 两阶段交割)**：实测 `get_transaction_log` 全量过滤对账完成 OTM `expiry` 与（真实触及时）ITM `exercise/assignment -> delivery` 过渡期货净额结清，`diff_btc == "0"`。
- [ ] **限额与三级人工停单核验**：证实启动默认 `entry_armed=0, budget_cap_btc=0, max_qty_btc=0.1`、`B_eff` 逐局扣损与子账户余额校验生效，且经 `/run/astra-exec/control.sock` 实测 `entry_armed=false`、`EXIT_ALL`、`DISABLE_WRITES` 三级停单均可立即中断并持久化。

---

## 4. 主网只读检查（`SHADOW_ONLY` 限定）

在阶段 2/3 未获正式授权前，仅允许使用公共行情运行只读巡检，严禁传递写模式或注入实盘写私钥：

```bash
# 只读模式单次巡检主网公共状态（无私钥不写订单）
python3 tools/astra_execution_coordinator_v1.py \
  --mode SHADOW_ONLY \
  --base-url https://www.deribit.com \
  --db /tmp/shadow_check.sqlite3 \
  --status-path /tmp/shadow_status.json \
  --once
```

---

## 5. 现行权威基线最小回归命令清单

```bash
uv run --isolated --no-project --python 3.12 python -m py_compile \
  tools/astra_workbench_session_v1.py tools/astra_workbench_v20.py \
  tools/astra_exec_contracts_v1.py tools/astra_exec_store_v1.py \
  tools/astra_exec_policy_v1.py tools/astra_execution_coordinator_v1.py \
  tools/astra_player_dossier_v1.py tools/astra_player_tracking_v13.py \
  deploy/xxproject_server/build_release.py deploy/xxproject_server/deploy_release.py

node --check deploy/astra_workbench_v3/api.js
node --check deploy/astra_workbench_v3/app.js
node --check deploy/astra_workbench_v3/current-hand.js
node --check deploy/astra_workbench_v3/player-audit.js
node --check deploy/astra_workbench_v3/execution.js

uv run --isolated --no-project --python 3.12 --with pytest pytest -q \
  tests/test_astra_exec_p1_kernel.py \
  tests/test_astra_exec_p2_integration.py \
  tests/test_p5_exec_unified_joint.py \
  tests/test_astra_player_dossier_v1.py \
  tests/test_astra_player_tracking_v13.py \
  tests/test_astra_p5_shadow_exit.py \
  tests/test_xxproject_server_release.py \
  tests/test_astra_workbench_v3_player_audit.py \
  tests/test_astra_unsuitable_wording.py
```
