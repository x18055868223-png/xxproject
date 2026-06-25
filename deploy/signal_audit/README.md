# 审计静态页面部署模块

> 当前模块口径（r3.3.1 / 2026-06-25）：本目录是当前审计页面部署资产，包含静态前端、materializer、Gemini LLM sidecar runner、transition 审计旁路、MACRO 双轴审计展示、systemd timer 和 Web server 示例。中文组件语义先读 [`因子文档/00_审计部署总览.md`](因子文档/00_审计部署总览.md)；审计卡展示语义见 [`docs/审计卡片语义.md`](docs/审计卡片语义.md)。

## 工程收纳

| 路径 | 用途 |
| --- | --- |
| `因子文档/` | 按 00-04 模块惯例整理的组件语义入口 |
| `docs/` | 审计卡片和前端展示语义 |
| `frontend/` | 静态页面、样例 `signal_cards/`、`VERSION.json` |
| `*.service` / `*.timer` | systemd 单元 |
| `install_or_update.sh` | 服务器安装/更新脚本 |

本轮 r3.3.1 保持既有服务/timer 复用，新增 transition ledger/LLM sidecar 参数与 MACRO direction-background/shock-gate 自检，不新增独立 transition 或 macro 服务。

This deployment target serves the finalized static audit frontend and refreshes
its `signal_cards/` data from the FMZ `signal_review.jsonl` file.

For new-server rebuilds or server migration, use the Chinese quick runbook
[`SERVER_MIGRATION_ZH.md`](SERVER_MIGRATION_ZH.md), the detailed English
runbook [`SERVER_MIGRATION.md`](SERVER_MIGRATION.md), and
[`../../tools/server_bootstrap_signal_stack.sh`](../../tools/server_bootstrap_signal_stack.sh).
The current migration/bootstrap release target is `r3.3.1` in the primary
`xxproject` repository.

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

Local first-time Git setup for a brand new standalone deployment repo:

> In the integrated backup workflow, prefer using the existing
> `https://github.com/x18055868223-png/xxproject.git` repository instead of
> creating another remote. The snippet below is only for the older standalone
> `signal-audit-deploy` route.

```bash
git init
git add .gitignore .gitattributes tools/materialize_signal_cards.py tools/gemini_signal_llm_review.py tools/server_self_check_signal_stack.sh deploy/signal_audit
git status --short
git commit -m "Prepare signal audit git deployment"
git branch -M main
git remote add origin git@github.com:<your-org-or-user>/<repo>.git
git push -u origin main
```

Server first-time clone for the integrated backup repo:

```bash
sudo apt update
sudo apt install -y git nginx rsync python3
sudo mkdir -p /opt/repos
sudo chown "$USER":"$USER" /opt/repos
git clone https://github.com/x18055868223-png/xxproject.git /opt/repos/xxproject
```

Server deploy/update from Git:

```bash
cd /opt/repos/xxproject
git pull --ff-only
sudo bash deploy/signal_audit/install_or_update.sh
```

`install_or_update.sh` is an active audit-service update, not a read-only
check. It copies frontend files into `/opt/signal-audit`, installs tools under
`/opt/signal-audit-tools`, and enables/starts the materializer and LLM review
timers. Run it during a maintenance window. It does not change FMZ strategy
code, execution-layer trading gates, or exchange credentials.

If an older server is still running from `/opt/repos/signal-audit-deploy`, use
one repository directory consistently during a maintenance window. Do not pull
`xxproject` in one directory while running install scripts from the old
`signal-audit-deploy` checkout unless you intentionally keep both routes and
know which one owns the deployed files under `/opt/signal-audit` and
`/opt/signal-audit-tools`.

The install script now also installs and enables the two systemd timers:

- `signal-audit-materialize.timer`: refreshes static card JSON from FMZ JSONL.
- `signal-audit-llm-review.timer`: generates Gemini LLM review sidecar JSONL,
  then triggers materialization so the frontend shows the review.

The LLM timer is safe before the key is configured: it exits successfully with a
clear message and does not call the model.

Optional direct zip package still exists for emergency/manual transfer:

```powershell
.\deploy\signal_audit\package_signal_audit.ps1
```

## One-Time Deploy Without Git

If GitHub is unavailable, unpack a zip on the server:

```bash
cd /tmp
rm -rf signal-audit-deploy
unzip -q signal-audit-deploy.zip -d signal-audit-deploy
```

Copy only the runtime frontend assets to the server root:

```bash
sudo mkdir -p /opt/signal-audit
sudo rsync -a --delete \
  /tmp/signal-audit-deploy/frontend/ \
  /opt/signal-audit/
```

Do not publish temporary notes such as `新建文本文档.txt`. `README.md` and
`VERSION.json` are useful release metadata but are not runtime dependencies.

Copy the materializer script from this repo:

```bash
sudo mkdir -p /opt/signal-audit-tools
sudo cp /tmp/signal-audit-deploy/tools/materialize_signal_cards.py /opt/signal-audit-tools/
sudo cp /tmp/signal-audit-deploy/tools/gemini_signal_llm_review.py /opt/signal-audit-tools/
sudo cp /tmp/signal-audit-deploy/deploy/run_signal_llm_review.sh /opt/signal-audit-tools/
sudo chmod +x /opt/signal-audit-tools/materialize_signal_cards.py
sudo chmod +x /opt/signal-audit-tools/gemini_signal_llm_review.py
sudo chmod +x /opt/signal-audit-tools/run_signal_llm_review.sh
```

Build live cards from the FMZ JSONL:

```bash
sudo /usr/bin/python3 /opt/signal-audit-tools/materialize_signal_cards.py \
  --source /home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl \
  --output /opt/signal-audit \
  --max-cards 200 \
  --llm-reviews /opt/signal-audit-tools/signal_llm_reviews.jsonl
```

Expected output is JSON with `written_cards >= 1` after the FMZ self-test.

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
TimeoutStartSec=30
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

Configure the Gemini key only on the server:

```bash
sudo mkdir -p /etc/signal-audit
sudo chmod 700 /etc/signal-audit
sudo install -m 600 deploy/signal_audit/signal-audit-llm.env.example /etc/signal-audit/llm.env
sudoedit /etc/signal-audit/llm.env
```

Set:

```text
GEMINI_CHANNEL1_API_KEY=<low-cost or free-tier Gemini API key>
GEMINI_CHANNEL2_API_KEY=<paid fallback Gemini API key>
GEMINI_MODEL=gemini-3.5-flash
LLM_REVIEW_LIMIT=2
LLM_REVIEW_TIMEOUT=60
JSONL_SOURCE=/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl
LLM_REVIEWS_SOURCE=/opt/signal-audit-tools/signal_llm_reviews.jsonl
```

Never commit `/etc/signal-audit/llm.env`. The repository only contains
`signal-audit-llm.env.example` with empty channel keys. Channel 1 is tried
first for cost control; channel 2 is used only when channel 1 returns a
retryable capacity/network error such as 429, 5xx, or timeout.

## Auto-Refresh And LLM Review

`install_or_update.sh` installs and enables these by default. Manual commands
are still useful for troubleshooting:

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
