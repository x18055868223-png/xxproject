# 信号层配套服务新服务器迁移速查

本文件用于回答一个实操问题：如果后续迁移服务器，是否能用当前仓库资产快速复建一份完整的信号层配套服务。

结论：可以。当前 `xxproject` 仓库已经具备可重复部署资产，可以在新服务器上重建信号审计静态页、JSONL materializer、LLM 复核 sidecar、systemd timer，以及可选的 GEX Monitor API。迁移时不要从 `signal-audit-deploy` 镜像仓库作为项目基线启动，默认使用 `xxproject` release tag。

## 当前权威来源

- 主仓库：`https://github.com/x18055868223-png/xxproject.git`
- 当前迁移默认 release：`r3.2.1`
- 快速 bootstrap 脚本：`tools/server_bootstrap_signal_stack.sh`
- 完整英文 runbook：`deploy/signal_audit/SERVER_MIGRATION.md`
- 审计页面安装脚本：`deploy/signal_audit/install_or_update.sh`
- 服务器自检脚本：`tools/server_self_check_signal_stack.sh`

## 可以从仓库自动复建的部分

运行 bootstrap 后会安装或刷新：

- `/opt/repos/neutral-loop`：`xxproject` release checkout。
- `/opt/signal-audit`：静态审计页面。
- `/opt/signal-audit-tools/materialize_signal_cards.py`：把 FMZ `signal_review.jsonl` 转成 `signal_cards/index.json` 与单卡 JSON。
- `/opt/signal-audit-tools/gemini_signal_llm_review.py`：LLM 复核 sidecar。
- `signal-audit-materialize.*` 与 `signal-audit-llm-review.*`：systemd service/timer。
- 可选 `/opt/gexmonitorapi` 与 `gexmonitorapi.service`：GEX Monitor API。

## 不能写进仓库、必须在新服务器补齐的部分

这些内容属于服务器本地状态或秘密，不应提交到 git：

- `/etc/signal-audit/llm.env`
  - `GEMINI_CHANNEL1_API_KEY`
  - `GEMINI_CHANNEL2_API_KEY`
  - `GEMINI_MODEL`
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
curl -fsSL https://raw.githubusercontent.com/x18055868223-png/xxproject/r3.2.1/tools/server_bootstrap_signal_stack.sh \
  -o /tmp/server_bootstrap_signal_stack.sh
chmod +x /tmp/server_bootstrap_signal_stack.sh

RELEASE_REF=r3.2.1 \
REPO_DIR=/opt/repos/neutral-loop \
INSTALL_GEX=0 \
GEX_REQUIRED=0 \
RUN_SELF_CHECK=1 \
/tmp/server_bootstrap_signal_stack.sh
```

如果是干净 Debian/Ubuntu 主机，先允许脚本安装基础包：

```bash
INSTALL_SYSTEM_PACKAGES=1 \
RELEASE_REF=r3.2.1 \
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
GEMINI_CHANNEL1_API_KEY=<低成本或免费层级 key>
GEMINI_CHANNEL2_API_KEY=<付费 fallback key>
GEMINI_MODEL=gemini-3.5-flash
JSONL_SOURCE=/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl
LLM_REVIEWS_SOURCE=/opt/signal-audit-tools/signal_llm_reviews.jsonl
```

如果两个通道都不填，审计页面和 materializer 仍可运行，但 LLM sidecar 会跳过新复核调用，只能展示已有 sidecar 历史。

从 `r3.2` 起，LLM sidecar 对每张新卡采用真正两次调用：第一次只看盲包形成理论主动视角与 Gamma 体制风险叠加，第二次再读取完整审计包做复核。API 用量页面应能看到新卡触发的调用；如果用量为 0，优先检查 `/etc/signal-audit/llm.env` 是否真的加载了 `GEMINI_CHANNEL1_API_KEY` 或 `GEMINI_CHANNEL2_API_KEY`，以及最新 sidecar 是否匹配最新 `card_id`。

双通道标准：`GEMINI_CHANNEL1_API_KEY` 是通道 1，默认放低成本或免费层级 key；`GEMINI_CHANNEL2_API_KEY` 是通道 2，默认放付费层级 key。sidecar 先调通道 1；只有通道 1 返回 429、5xx、timeout 等可重试容量/网络错误时，才切到通道 2 补全。400/schema/解析错误不切通道，以免掩盖程序或提示词问题。sidecar 会记录 `api_key_route` 和 `llm_call_routes`，用于确认本次复核实际走了通道 1、通道 2 还是 mixed。

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
git describe --tags --exact-match

GEX_REQUIRED=0 LLM_REQUIRED=1 SESSION_CONTEXT_REQUIRED=1 sudo -E bash tools/server_self_check_signal_stack.sh --run-oneshots
```

期望：

- `git describe --tags --exact-match` 输出 `r3.2.1`。
- self-check 汇总 `FAIL=0`。
- `signal-audit-materialize.service` 的 `Result=success`。
- `signal-audit-llm-review.service` 的 `Result=success`。
- `LLM_REQUIRED=1` 模式下两个 Gemini 通道 key 都必须加载，且最新 signal card 必须有 `status=OK`、`blind_review_mode=two_call_strict`、`llm_call_count>=2` 的 sidecar 复核。
- `SESSION_CONTEXT_REQUIRED=1` 模式下最新真实卡必须来自 `identity.strategy_version=1.4.1` 的 FMZ 生产者，且不能带 `compat_backfill_applied=true`；同时必须有 `SignalSessionPremiseDurabilityContext`、`clock_window`、`backtest_delta_pp`、结构化 `validation_basis`、`confidence_policy` 和 `decision_matrix.temporal_durability`。否则说明 FMZ 生产者、materializer 或部署链路仍未闭环，不得封版。
- `http://127.0.0.1/signal-audit/` 返回 HTTP 200。
- `http://127.0.0.1/signal-audit/signal_cards/index.json` 返回 HTTP 200 且有真实卡片。

## 迁移边界

- 这套资产复建的是信号层配套服务，不会自动迁移 FMZ 机器人本体、交易所密钥或执行层交易许可。
- FMZ 信号端仍需要在新服务器或新 FMZ 环境中单独确认 `signal_review.jsonl` 输出路径。
- 不要把 `/etc/*.env`、历史 JSONL、API token、Gemini key、服务器私钥打包进仓库。
- `signal-audit-deploy` 只能作为静态审计面辅助镜像；项目 release、tag、服务器 baseline 默认以 `xxproject` 为准。
