# xxproject 实盘前 Linux 与 Testnet 联机补验操作手册

> 当前阶段：`PRELIVE_BLOCKED`（尚未授权真实资金交易；不得在未获 P4 明确授权前解除 `--mode SHADOW_ONLY` 或接入主网写权限）。

## 1. Linux 主机 UID / Group 与双令牌准备

在目标 Linux 主机执行（全部执行级密钥均由 `root` 持有并通过 systemd `LoadCredential` 注入，不得写入 `workbench-v20.env`、`llm.env` 或 `research.env`）：

```bash
# 1) 创建独立系统用户与组（禁止 bitnami 或 astra-workbench 加入 astra-exec 或共享 UID）
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

# 3) 配置协调器环境文件（ASTRA_EXEC_ALLOWED_PEER_UIDS 留空以严格使用 {0, astra-exec}；启用 Testnet 时必须设置 ASTRA_EXEC_SUBACCOUNT_ID）
sudo cp deploy/xxproject_server/systemd_units_execution/exec-coordinator.env.example /etc/signal-audit/exec-coordinator.env
sudo chmod 0600 /etc/signal-audit/exec-coordinator.env
sudo chown root:root /etc/signal-audit/exec-coordinator.env

# 4) 确认内核 ptrace 隔离开启（>= 1）
sudo sysctl -w kernel.yama.ptrace_scope=1
```

## 2. 发布包构建、隔离部署与 Linux 反向安全验收

```bash
# 构建单一不可变发布包（默认拒绝脏工作树，必须干净提交后再打正式包）
python3 deploy/xxproject_server/build_release.py --output /tmp/xxproject_release.tar.gz

# 应用 21 基线 + 执行隔离单元（默认 SHADOW_ONLY；未满足双令牌与三独立 UID 前 deploy_release 直接 fail-closed）
sudo python3 deploy/xxproject_server/deploy_release.py install \
  --package /tmp/xxproject_release.tar.gz \
  --apply-units \
  --with-execution

# 若启用 --with-execution 后需回退至无执行层的 P5 基线，必须用新版 deploy_release.py 执行回滚（禁止使用旧版脚本）：
# 会校验 /health-fresh safe_for_deploy=true、卸载执行单元与 60-exec-control.conf、恢复 workbench 属主为 bitnami 并归档旧 public_status.json
sudo python3 /opt/xxproject/current/deploy/xxproject_server/deploy_release.py rollback --apply-units

# 验证 8 项 OS / Confused-Deputy / 跨 UID 共享读写 / P5 只读可见性隔离门禁
sudo bash /opt/xxproject/current/deploy/xxproject_server/systemd_units_execution/linux_security_verify.sh

# 运行 SHADOW_ONLY 统一健康检查（含执行层 public_status.json + systemd + /api/workbench/execution/live）
sudo python3 /opt/xxproject/current/tools/xxproject_server_health_v1.py --require-systemd --require-workbench-auth --require-release-manifest --require-execution --expected-exec-mode SHADOW_ONLY
```

验收通过标志：输出 `ALL_LINUX_SECURITY_CHECKS_PASSED` 且健康自检返回 `"ok": true`，确认：
- `astra-workbench`（非 `bitnami`）运行 `astra-workbench-v20.service`，`astra-exec` 运行 `astra-execution-coordinator.service`（`mode=SHADOW_ONLY`, `writes_enabled=false`, `cockpit_mode=READ_ONLY`）；
- `ASTRA_WORKBENCH_TOKEN` 可读 `GET /api/workbench/execution/live`（HTTP 200），但调用 `POST /api/workbench/execution/halt-entry`（及任意 `/api/workbench/execution/*` POST）均返回 `403`；
- `bitnami` 与 `astra-workbench` 对 `/run/astra-exec/control.sock`、`exec_store.sqlite`、`exec-control.token`、`/proc/<exec_pid>/environ` 均被拒绝，同时保留对 `/var/lib/xxproject/execution/public_status.json`（`0644`）的只读可见性。

## 3. Deribit Testnet `T1–T5` 独立补验（严禁削弱入场门禁）

使用独立 Testnet 子账户环境变量（仅供验证脚本临时会话使用，勿常驻写主环境）：

```bash
export DERIBIT_TESTNET_CLIENT_ID="<testnet_subaccount_client_id>"
export DERIBIT_TESTNET_CLIENT_SECRET="<testnet_subaccount_client_secret>"
export DERIBIT_TESTNET_SUBACCOUNT_ID="<testnet_subaccount_id>"
```

所有场景均通过真实 `ExecutionCoordinator(mode="TESTNET", env_tag="tn")`、`ExecutionStore` 与 `rebuild_episode_ledger()` 执行，完整保留 `ExecConfig` 默认准入门禁（净信用下限 `0.0008 BTC/BTC`、A-01 压力损失预算、R-09 保证金缓冲）：

```bash
# 公共行情 Schema 只读探测（同时验证主网护栏：传 https://www.deribit.com 必返回退出码 3）
python3 tools/astra_exec_testnet_verify_v1.py --base-url https://test.deribit.com --public-only

# T1：正式建仓 + 85% TP 预挂 -> 成交 -> LX -> 账本零差对账（未即时成交会保留 DB 返回 AWAITING_TP_FILL，可用 --resume-db 续跑）
python3 tools/astra_exec_testnet_verify_v1.py --scenario t1 --work-dir /tmp/tn_p3 --wait-seconds 120

# T2：建仓 -> AI_REVOKE -> 撤 TP + RX 买回短腿 -> LX -> 账本零差对账
python3 tools/astra_exec_testnet_verify_v1.py --scenario t2 --work-dir /tmp/tn_p3 --wait-seconds 120

# T3：OTM 到期两阶段结算（08:00 UTC 前开仓后若返回 AWAITING_EXPIRY，到期后使用返回的 --resume-db 续跑）
python3 tools/astra_exec_testnet_verify_v1.py --scenario t3 --work-dir /tmp/tn_p3 --wait-seconds 60
python3 tools/astra_exec_testnet_verify_v1.py --scenario t3 --resume-db /tmp/tn_p3/t3_exec.sqlite3 --wait-seconds 120

# T4：ITM 到期两阶段交割（单腿 ITM 与双腿 ITM 分开验证；也可显式指定 --short-instrument / --long-instrument）
python3 tools/astra_exec_testnet_verify_v1.py --scenario t4-single-itm --work-dir /tmp/tn_p3 --wait-seconds 60
python3 tools/astra_exec_testnet_verify_v1.py --scenario t4-single-itm --resume-db /tmp/tn_p3/t4-single-itm_exec.sqlite3 --wait-seconds 120

python3 tools/astra_exec_testnet_verify_v1.py --scenario t4-both-itm --work-dir /tmp/tn_p3 --wait-seconds 60
python3 tools/astra_exec_testnet_verify_v1.py --scenario t4-both-itm --resume-db /tmp/tn_p3/t4-both-itm_exec.sqlite3 --wait-seconds 120

# T5：下单 ACK 超时注入 SUBMIT_UNKNOWN -> 关闭并重启协调器通过 get_order_state_by_label 恢复（零重复下单）-> AI_REVOKE 清仓结算
python3 tools/astra_exec_testnet_verify_v1.py --scenario t5 --work-dir /tmp/tn_p3 --wait-seconds 120
```

- [x] **M1–M4 本地代码与模拟反例全绿** -> 下一步在维护窗 (`08:35–15:50 UTC`) 执行上方 Linux `SHADOW_ONLY` 部署与 `linux_security_verify.sh`，随后持隔离 Testnet 子账户凭证运行 `T1–T5` 补验并确认全部返回 `PASSED` 且 `diff_btc == "0"`。

> 若 Testnet 盘口不满足正式 `ExecConfig` 条件，工具将严格返回 `BLOCKED:no_qualified_testnet_spread`（退出码 `2`）；不得通过放宽 `ExecConfig` 阈值绕过。`t4-both-itm` 如遇近月跨度大导致保护腿超出 `0.0060` 上限，可通过 `--short-instrument / --long-instrument` 指定临近到期或合规实值价差。

## 4. 主网只读检查（`SHADOW_ONLY` 限定）

仅允许验证只读行情与协调器 `SHADOW_ONLY` 单步只读巡检，严禁在 P3 对主网启用 `LIVE` 或传递写模式：

```bash
# 验证主网端点护栏阻止 Testnet/写脚本误连（预期退出码 3）
python3 tools/astra_exec_testnet_verify_v1.py --base-url https://www.deribit.com

# 只读模式单次巡检公共状态（无私钥不写订单）
python3 tools/astra_execution_coordinator_v1.py \
  --mode SHADOW_ONLY \
  --base-url https://www.deribit.com \
  --db /tmp/shadow_check.sqlite3 \
  --status-path /tmp/shadow_status.json \
  --once
```

## 5. 与 P5 权威基线整合后的最小回归命令清单

后续将本分支工作成果合入最终 P5 权威基线后，必须完整运行以下最小回归集：

```bash
uv run --isolated --no-project --python 3.12 python -m py_compile \
  tools/astra_workbench_session_v1.py tools/astra_workbench_v20.py \
  tools/astra_exec_contracts_v1.py tools/astra_exec_store_v1.py \
  tools/astra_exec_policy_v1.py tools/astra_execution_coordinator_v1.py \
  tools/astra_exec_testnet_verify_v1.py \
  deploy/xxproject_server/build_release.py deploy/xxproject_server/deploy_release.py

node --check deploy/astra_workbench_v3/api.js
node --check deploy/astra_workbench_v3/app.js
node --check deploy/astra_workbench_v3/execution.js

uv run --isolated --no-project --python 3.12 --with pytest pytest -q \
  tests/test_astra_exec_p1_kernel.py \
  tests/test_astra_exec_p2_integration.py \
  tests/test_astra_p5_shadow_exit.py \
  tests/test_xxproject_server_release.py \
  tests/test_astra_workbench_v3_*.py \
  tests/test_astra_unsuitable_wording.py
```
