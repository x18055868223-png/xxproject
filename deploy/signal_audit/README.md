# 审计静态页面部署模块

> 当前模块口径：本目录是审计页面部署资产，包含静态前端、materializer、DeepSeek LLM sidecar runner、transition 审计旁路、systemd timer 和 Web server 示例。中文组件语义先读 [`因子文档/00_审计部署总览.md`](因子文档/00_审计部署总览.md)；审计卡展示语义见 [`docs/审计卡片语义.md`](docs/审计卡片语义.md)。

## 工程收纳

| 路径 | 用途 |
| --- | --- |
| `因子文档/` | 按 00-04 模块惯例整理的组件语义入口 |
| `docs/` | 审计卡片和前端展示语义 |
| `frontend/` | 静态页面、样例 `signal_cards/`、`VERSION.json` |
| `*.service` / `*.timer` | systemd 单元 |
| `install_or_update.sh` | 服务器安装/更新脚本 |

当前Astra版本复用既有服务与timer：新卡只进行一次总体证据综合评审，变化由本地台账提供，不再自动调用独立blind、transition LLM或24小时长报告。升级与回退先读[Astra兼容与数据复用约定](../../docs/astra/10_版本兼容与数据复用约定.md)。本次分支推送不等于已获服务器部署或FMZ更新验收。

This deployment target serves the finalized static audit frontend and refreshes
its `signal_cards/` data from the FMZ `signal_review.jsonl` file.

For new-server rebuilds or server migration, use the Chinese quick runbook
[`SERVER_MIGRATION_ZH.md`](SERVER_MIGRATION_ZH.md), the detailed English
runbook [`SERVER_MIGRATION.md`](SERVER_MIGRATION.md), and
[`../../tools/server_bootstrap_signal_stack.sh`](../../tools/server_bootstrap_signal_stack.sh).
The current Astra code target is `codex/astra-signal-rating-v1` in the primary
`xxproject` repository. The migration runbooks describe earlier infrastructure;
use the current installer and Astra compatibility contract for review protocols,
data preservation and timer defaults. Verify the intended commit before installing.

## Server Paths

Default input confirmed from the FMZ simulation run:

```text
/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl
```

Recommended static frontend root:

```text
/opt/signal-audit
```

The frontend root must contain:

```text
index.html
app.js
VERSION.json
README.md
signal_cards/index.json
signal_cards/*.json
signal_cards/fallback.js
```

## GitHub Deployment

Preferred deployment route: push this repository to a private GitHub repo, then
clone/pull it on the Debian server. This avoids manual zip/scp uploads and keeps
the server deployment reproducible.

Commit only source/config/scripts and the static runtime assets under
`deploy/signal_audit/frontend/`. Never commit:

- live `signal_review.jsonl`
- secrets, tokens, `.env`
- FMZ storage dumps
- server private keys

Use the primary xxproject repository and the selected Astra branch. Do not create
a separate deployment-only main branch or copy a hand-picked subset of tools.

Server first-time clone for the integrated backup repo:

```bash
sudo apt update
sudo apt install -y git nginx rsync python3
sudo mkdir -p /opt/repos
sudo chown "$USER":"$USER" /opt/repos
git clone --branch codex/astra-signal-rating-v1 https://github.com/x18055868223-png/xxproject.git /opt/repos/xxproject
```

Server deploy/update from Git:

```bash
cd /opt/repos/xxproject
git remote -v
git fetch origin codex/astra-signal-rating-v1
git switch codex/astra-signal-rating-v1
git pull --ff-only origin codex/astra-signal-rating-v1
git rev-parse HEAD
sudo bash deploy/signal_audit/install_or_update.sh
```

`install_or_update.sh` is an active audit-service update, not a read-only
check. It copies frontend files into `/opt/signal-audit`, installs tools under
`/opt/signal-audit-tools`, and enables/starts the materializer and LLM review
timers only when their explicit enable/start options are set. By default it
installs units without enabling timers, starting timers or running an initial
LLM request. Existing running timers are not stopped by installation: pause them
during a coordinated maintenance window before replacing the tool set. It does not change FMZ strategy
code, execution-layer trading gates, or exchange credentials.

If an older server is still running from `/opt/repos/signal-audit-deploy`, use
one repository directory consistently during a maintenance window. Do not pull
`xxproject` in one directory while running install scripts from the old
`signal-audit-deploy` checkout unless you intentionally keep both routes and
know which one owns the deployed files under `/opt/signal-audit` and
`/opt/signal-audit-tools`.

The install script installs these two systemd timer definitions; enabling and
starting them remains a separate, explicit step after canary validation:

- `signal-audit-materialize.timer`: refreshes static card JSON from FMZ JSONL.
- `signal-audit-llm-review.timer`: generates DeepSeek LLM review sidecar JSONL,
  then triggers materialization so the frontend shows the review.

The LLM timer is safe before the key is configured: it exits successfully with a
clear message and does not call the model.

Optional direct zip package still exists for emergency/manual transfer:

```powershell
.\deploy\signal_audit\package_signal_audit.ps1
```

## One-Time Deploy Without Git

When GitHub is unavailable, transfer the ZIP produced by the current
`package_signal_audit.ps1`. Unpack it into a new directory and use the same
installer as Git deployments:

```bash
package_dir=$(mktemp -d /tmp/signal-audit-deploy.XXXXXX)
unzip -q /tmp/signal-audit-deploy.zip -d "$package_dir"
sudo bash "$package_dir/deploy/install_or_update.sh"
```

Do not manually run an unprotected `rsync --delete` against the live frontend
or copy only the old materializer/LLM files. The installer requires
`VERSION.json`, installs the complete v2 tools, and protects the whole existing
`signal_cards/` directory even if its index is missing or damaged. A first
installation can contain packaged fixtures; these are not current FMZ evidence.

Keep the source JSONL, sidecar, its `.v2_attempts/` directory and responses,
daily usage ledger, transition ledger/state and existing environment file.
The installer preserves the existing key and writes an updated example
separately. Verify DeepSeek provider/base URL/model settings before activating
the new workflow; old provider settings are not automatically migrated.

After installation and configuration review, refresh through the installed
materializer service so that all configured review and transition sources are
used consistently:

```bash
sudo systemctl start signal-audit-materialize.service
sudo systemctl status signal-audit-materialize.service --no-pager
```

This operation builds the reading projection; it is not evidence that a new
LLM request, FMZ update or trading validation succeeded. The initial LLM
canary and production acceptance remain separate steps.

## Apache / Nginx Example

On the current Bitnami strategy server, port 80 is already owned by Bitnami
Apache (`/opt/bitnami/apache/bin/httpd`). Prefer adding an Apache alias instead
of starting nginx on port 80.

Apache alias example:

```bash
sudo cp deploy/signal_audit/apache-bitnami-signal-audit.conf.example \
  /opt/bitnami/apache/conf/extra/signal-audit.conf

grep -q 'conf/extra/signal-audit.conf' /opt/bitnami/apache/conf/httpd.conf || \
  echo 'Include "/opt/bitnami/apache/conf/extra/signal-audit.conf"' | \
  sudo tee -a /opt/bitnami/apache/conf/httpd.conf

sudo /opt/bitnami/apache/bin/apachectl -t
sudo /opt/bitnami/ctlscript.sh restart apache
```

Verification URLs:

```text
http://<server>/signal-audit/
http://<server>/signal-audit/signal_cards/index.json
```

Disable nginx if it was installed but failed because Apache owns port 80:

```bash
sudo systemctl disable --now nginx || true
```

### Nginx Alternative

Use one of the nginx examples as a template:

- `nginx.signal-audit.conf.example`: dedicated root/site.
- `nginx.signal-audit-location.conf.example`: mount under `/signal-audit/`.

The important behavior is:

- serve `/` from `/opt/signal-audit`
- disable cache for `/signal_cards/`
- keep `try_files $uri $uri/ /index.html`

Verification URLs:

```text
http://<server>/signal_cards/index.json
http://<server>/
```

If mounted under `/signal-audit/`, verify with the trailing slash:

```text
http://<server>/signal-audit/
http://<server>/signal-audit/signal_cards/index.json
```

First production smoke test should keep the FMZ config `audit_static_base_url`
empty. The current finalized frontend loads and lists cards correctly, but it
does not yet consume the signal layer's `/c/<short_id>` deep-link format. Enable
`audit_static_base_url` only after either the frontend supports card deep links
or the signal push format is changed to point at the page root.

## Debian 1GB Memory Rules

The page itself is static. Keep it static: do not run a Node/Python web service
for the frontend on a 1GB server. Let nginx serve files, and run the JSONL
materializer as a short-lived systemd oneshot.

Recommended checks:

```bash
free -h
systemctl status nginx --no-pager
```

If swap is absent and disk space allows, add a small 1G swapfile:

```bash
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

The provided materializer service is capped:

```ini
MemoryHigh=64M
MemoryMax=128M
CPUQuota=25%
TimeoutStartSec=300
```

Keep refresh interval at 120 seconds unless real signal volume requires faster
updates. The script atomically writes files and exits, so memory is released
after each run.

For nginx on a small server, keep workers low in `/etc/nginx/nginx.conf`:

```nginx
worker_processes 1;
events {
    worker_connections 256;
}
```

Optional log rotation check:

```bash
sudo logrotate -d /etc/logrotate.d/nginx
```

## LLM API Key

The installer creates a template only when no environment file exists. Edit
the existing file; do not replace it with an empty example during upgrades.
Configure the DeepSeek key only on the server:

```bash
sudo mkdir -p /etc/signal-audit
sudo chmod 700 /etc/signal-audit
sudoedit /etc/signal-audit/llm.env
```

Set:

```text
LLM_PROVIDER=deepseek
LLM_API_KEY=<server-only DeepSeek API key>
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LLM_REVIEW_LIMIT=4
LLM_MAX_CONCURRENCY=4
LLM_DAILY_HTTP_CAP=60
JSONL_SOURCE=/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl
LLM_REVIEWS_SOURCE=/opt/signal-audit-tools/signal_llm_reviews.jsonl
```

Never commit `/etc/signal-audit/llm.env`. The repository only contains
`signal-audit-llm.env.example` with an empty key. Gemini variables and fallback
channels are not read. Each assessment has a shared, persistent limit of two
HTTP attempts for transport and format recovery, and the Beijing-day HTTP
ledger fails closed at 60 requests. Restarting or updating code does not clear
these ledgers or automatically reassess completed historical cards.

## Auto-Refresh And LLM Review

`install_or_update.sh` installs the units, but does not enable/start timers by
default. The following service start can make a real paid LLM request and is
only for an authorized canary or approved production operation:

```bash
sudo systemctl start signal-audit-llm-review.service
sudo systemctl status signal-audit-llm-review.service --no-pager
sudo systemctl start signal-audit-materialize.service
sudo systemctl status signal-audit-materialize.service --no-pager
systemctl list-timers | grep signal-audit
```

The LLM review service writes:

```text
/opt/signal-audit-tools/signal_llm_reviews.jsonl
```

The materializer merges that file into `signal_cards/*.json` and
`signal_cards/fallback.js`.
