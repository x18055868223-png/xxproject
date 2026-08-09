# 信号层配套服务新服务器迁移速查

本文件用于回答一个实操问题：如果后续迁移服务器，是否能用当前仓库资产快速复建一份完整的信号层配套服务。

结论：可以。当前 `xxproject` 仓库已经具备可重复部署资产，可以在新服务器上重建信号审计静态页、JSONL materializer、LLM 复核 sidecar、systemd timer，以及可选的 GEX Monitor API。迁移时不要从 `signal-audit-deploy` 镜像仓库作为项目基线启动；必须显式指定已经审查的 `xxproject` tag、分支或 commit SHA。

## 当前权威来源

- 主仓库：`https://github.com/x18055868223-png/xxproject.git`
- 部署 ref：必须通过 `DEPLOY_REF` / `RELEASE_REF` 显式指定，bootstrap 不提供隐式旧版本默认值
- 快速 bootstrap 脚本：`tools/server_bootstrap_signal_stack.sh`
- 完整英文 runbook：`deploy/signal_audit/SERVER_MIGRATION.md`
- 审计页面安装脚本：`deploy/signal_audit/install_or_update.sh`
- 服务器自检脚本：`tools/server_self_check_signal_stack.sh`

## 可以从仓库自动复建的部分

运行 bootstrap 后会安装或刷新：

- `/opt/repos/neutral-loop`：`xxproject` release checkout。
- `/opt/signal-audit`：静态审计页面。
- `/opt/signal-audit-tools/materialize_signal_cards.py`：把 FMZ `signal_review.jsonl` 转成 `signal_cards/index.json` 与单卡 JSON。
- `/opt/signal-audit-tools/signal_llm_review.py` 与 `signal_llm_review_entry.py`：LLM 复核 sidecar。
- `signal-audit-materialize.*` 与 `signal-audit-llm-review.*`：systemd service/timer。
- 可选 `/opt/gexmonitorapi` 与 `gexmonitorapi.service`：GEX Monitor API。

## 不能写进仓库、必须在新服务器补齐的部分

这些内容属于服务器本地状态或秘密，不应提交到 git：

- `/etc/signal-audit/llm.env`
  - `LLM_API_KEY`
  - `LLM_PROVIDER=deepseek`
  - `LLM_BASE_URL=https://api.deepseek.com`
  - `LLM_MODEL=deepseek-v4-flash`
  - LLM 限速、超时、JSONL 路径配置
- `/etc/gexmonitorapi.env`
  - `API_TOKEN`
  - GEX cache/history 路径
- FMZ 运行时 JSONL
  - 默认：`/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl`
  - 新服务器路径不同就用 `JSONL_SOURCE=...` 覆盖。
- 历史 sidecar 文件
  - 默认：`/opt/signal-audit-tools/signal_llm_reviews.jsonl`
  - 旧服务器历史需要单独备份和导入。

## 最小迁移命令

在新服务器上用 sudo 用户执行：

```bash
DEPLOY_REF=codex/integrated-trade-advisory-next-design
curl -fsSL "https://raw.githubusercontent.com/x18055868223-png/xxproject/${DEPLOY_REF}/tools/server_bootstrap_signal_stack.sh" \
  -o /tmp/server_bootstrap_signal_stack.sh
chmod +x /tmp/server_bootstrap_signal_stack.sh

RELEASE_REF="$DEPLOY_REF" \
REPO_DIR=/opt/repos/neutral-loop \
INSTALL_GEX=0 \
GEX_REQUIRED=0 \
RUN_SELF_CHECK=1 \
/tmp/server_bootstrap_signal_stack.sh
```

如果是干净 Debian/Ubuntu 主机，先允许脚本安装基础包：

```bash
INSTALL_SYSTEM_PACKAGES=1 \
RELEASE_REF="$DEPLOY_REF" \
REPO_DIR=/opt/repos/neutral-loop \
INSTALL_GEX=0 \
GEX_REQUIRED=0 \
RUN_SELF_CHECK=1 \
/tmp/server_bootstrap_signal_stack.sh
```

## 启用 LLM 复核

bootstrap 会创建 env 模板，但不会写入密钥。部署完成后在服务器编辑：

```bash
sudoedit /etc/signal-audit/llm.env
sudo chmod 600 /etc/signal-audit/llm.env
sudo systemctl restart signal-audit-llm-review.service
sudo systemctl restart signal-audit-materialize.service
```

至少确认：

```text
LLM_PROVIDER=deepseek
LLM_API_KEY=<仅保存在服务器的 DeepSeek key>
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LLM_REVIEW_LIMIT=4
TRANSITION_REVIEW_LIMIT=4
LLM_MAX_CONCURRENCY=4
LLM_DAILY_HTTP_CAP=60
JSONL_SOURCE=/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl
LLM_REVIEWS_SOURCE=/opt/signal-audit-tools/signal_llm_reviews.jsonl
```

如果 `LLM_API_KEY` 不填，审计页面和 materializer 仍可运行，但 LLM sidecar 会跳过新复核调用，只能展示已有 sidecar 历史。

主信号 sidecar 对每张新卡采用真正两次调用：第一次只看盲包，第二次读取完整审计包做 reconciliation；状态转移保持单调用。若用量为 0，优先检查 `/etc/signal-audit/llm.env` 是否加载 `LLM_API_KEY`，以及最新 sidecar 是否匹配最新 `card_id`。

当前只支持 DeepSeek Bearer 单通道，不读取 Gemini 环境变量，也没有第二厂商 fallback。每日北京时间 HTTP 上限为 60；连接重置允许一次无等待重试，限定的 408/429/5xx 最多允许一次短退避重试：服务端提供的 `Retry-After` 必须不超过 10 秒，未提供时固定等待 5 秒；完整超时、空响应、非法 JSON 和本地校验失败进入跨轮冷却。非流式响应使用单调时钟墙钟截止，DeepSeek 排队期间的空行保活不会延长 60/240/120 秒阶段上限。每次重试前仍先在私有 usage ledger 内预留额度。

由于 DeepSeek V4 会把思考模式的 `low/medium` 映射为 `high`，运行链把本地 low profile（main blind、默认 transition）落实为 `thinking=disabled`，把 reconciliation high profile 落实为 `thinking=enabled` 与 `reasoning_effort=high`。

## 可选启用 GEX Monitor API

GEX 需要浏览器依赖、API token 和足够内存。只迁移审计页时不必开启。

```bash
INSTALL_GEX=1 \
INSTALL_GEX_BROWSER=1 \
GEX_SERVICE_USER=bitnami \
GEX_SERVICE_GROUP=bitnami \
GEX_APP_DIR=/opt/gexmonitorapi \
GEX_STATE_DIR=/var/lib/gexmonitorapi \
/tmp/server_bootstrap_signal_stack.sh
```

然后编辑：

```bash
sudoedit /etc/gexmonitorapi.env
sudo chmod 600 /etc/gexmonitorapi.env
sudo systemctl enable --now gexmonitorapi.service
```

## 导入旧服务器历史

如果需要保留旧卡片和旧 LLM 复核结果，先在新服务器准备：

```text
/tmp/signal-history/
  signal_review.jsonl
  signal_llm_reviews.jsonl
```

再执行：

```bash
IMPORT_HISTORY_DIR=/tmp/signal-history \
JSONL_SOURCE=/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl \
LLM_REVIEWS_SOURCE=/opt/signal-audit-tools/signal_llm_reviews.jsonl \
/tmp/server_bootstrap_signal_stack.sh
```

## 验收标准

迁移后必须跑：

```bash
cd /opt/repos/neutral-loop
git rev-parse --short HEAD
git rev-parse HEAD

GEX_REQUIRED=0 LLM_REQUIRED=1 SESSION_CONTEXT_REQUIRED=1 sudo -E bash tools/server_self_check_signal_stack.sh --run-oneshots
```

期望：

- `git rev-parse HEAD` 与本次已审查、已推送的部署 commit 完全一致。
- self-check 汇总 `FAIL=0`。
- `signal-audit-materialize.service` 的 `Result=success`。
- `signal-audit-llm-review.service` 的 `Result=success`。
- `LLM_REQUIRED=1` 模式下 `LLM_API_KEY` 必须加载，且最新 signal card 必须有 `status=OK`、`blind_review_mode=two_call_strict`、`llm_call_count>=2` 的 sidecar 复核。
- `SESSION_CONTEXT_REQUIRED=1` 模式下最新真实卡必须来自 `identity.strategy_version=1.5.7` 的 FMZ 生产者，且不能带 `compat_backfill_applied=true`；同时必须有 `SignalSessionPremiseDurabilityContext`、`clock_window`、`backtest_delta_pp`、结构化 `validation_basis`、`confidence_policy` 和 `decision_matrix.temporal_durability`，并原生带有 `factor_cross_section.macro_pressure.macro_shock.state/block`。若本次把变化链纳入封版，还应设置 `TRANSITION_REQUIRED=1` 与 `TRANSITION_LLM_REQUIRED=1`，并确认最新卡 `transition_context.audit_scope=AUDIT_ONLY`、私有 `signal_transition_ledger.jsonl` 对齐最新卡、transition LLM review 保留 `no_trading_instruction` guard。否则说明 FMZ 生产者、materializer 或部署链路仍未闭环，不得封版。
- `http://127.0.0.1/signal-audit/` 返回 HTTP 200。
- `http://127.0.0.1/signal-audit/signal_cards/index.json` 返回 HTTP 200 且有真实卡片。

## 迁移边界

- 这套资产复建的是信号层配套服务，不会自动迁移 FMZ 机器人本体、交易所密钥或执行层交易许可。
- FMZ 信号端仍需要在新服务器或新 FMZ 环境中单独确认 `signal_review.jsonl` 输出路径。
- 不要把 `/etc/*.env`、历史 JSONL、API token、LLM key、服务器私钥打包进仓库。
- `signal-audit-deploy` 只能作为静态审计面辅助镜像；项目 release、tag、服务器 baseline 默认以 `xxproject` 为准。
