# Signal Audit Static Deployment

This deployment target serves the finalized static audit frontend and refreshes
its `signal_cards/` data from the FMZ `signal_review.jsonl` file.

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

Local first-time Git setup:

```bash
git init
git add .gitignore .gitattributes tools/materialize_signal_cards.py tools/gemini_signal_llm_review.py tools/server_self_check_signal_stack.sh deploy/signal_audit
git status --short
git commit -m "Prepare signal audit git deployment"
git branch -M main
git remote add origin git@github.com:<your-org-or-user>/<repo>.git
git push -u origin main
```

Server first-time clone:

```bash
sudo apt update
sudo apt install -y git nginx rsync python3
sudo mkdir -p /opt/repos
sudo chown "$USER":"$USER" /opt/repos
git clone git@github.com:<your-org-or-user>/<repo>.git /opt/repos/neutral-loop
```

Server deploy/update from Git:

```bash
cd /opt/repos/neutral-loop
git pull --ff-only
sudo bash deploy/signal_audit/install_or_update.sh
```

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

## LLM API Keys

Configure Gemini keys only on the server:

```bash
sudo mkdir -p /etc/signal-audit
sudo chmod 700 /etc/signal-audit
sudo install -m 600 deploy/signal_audit/signal-audit-llm.env.example /etc/signal-audit/llm.env
sudoedit /etc/signal-audit/llm.env
```

Set:

```text
GEMINI_3_5_FLASH_API_KEY=<low-cost Gemini 3.5 Flash key>
GEMINI_PAID_API_KEY=<normal paid-tier fallback key>
GEMINI_MODEL=gemini-3.5-flash
LLM_REVIEW_LIMIT=2
LLM_REVIEW_TIMEOUT=60
JSONL_SOURCE=/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl
LLM_REVIEWS_SOURCE=/opt/signal-audit-tools/signal_llm_reviews.jsonl
```

The review tool performs two Gemini calls per card:

1. A true-blind first pass that sees only the blind theoretical packet.
2. A reconciliation pass that sees the full audit packet plus the first-pass
   blind result.

Each call tries the low-cost key first, then falls back to the paid-tier key if
the low-cost call fails. `GEMINI_API_KEY` remains supported as a backward-
compatible single-key fallback, but the two-key setup above is preferred.

Never commit `/etc/signal-audit/llm.env`. The repository only contains
`signal-audit-llm.env.example` with an empty key.

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
