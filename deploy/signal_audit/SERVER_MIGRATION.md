# Signal Stack Server Migration

This document describes how to bring up a new server for the signal-layer
supporting services from the current `xxproject` release. The target is a fast,
repeatable rebuild of the audit frontend, materializer, LLM review sidecar, and
optionally the GEX Monitor API.

## Repository Authority

- Primary repo: `https://github.com/x18055868223-png/xxproject.git`
- Current release ref: `r3.2.1`
- Do not use `signal-audit-deploy` as the project baseline. It is only a static
  audit mirror/helper surface.

## What Is Recreated

The bootstrap installs or refreshes:

- `/opt/repos/neutral-loop`: checkout of `xxproject` at `r3.2.1`.
- `/opt/signal-audit`: static audit frontend.
- `/opt/signal-audit-tools/materialize_signal_cards.py`: JSONL-to-card
  materializer.
- `/opt/signal-audit-tools/gemini_signal_llm_review.py`: LLM review sidecar.
- `signal-audit-materialize.*` and `signal-audit-llm-review.*`: systemd
  service/timer units.
- Systemd drop-ins under `/etc/systemd/system/*.service.d/10-bootstrap-overrides.conf`
  so custom paths from bootstrap are preserved for future timer runs.
- Optional `gexmonitorapi.service` and `/opt/gexmonitorapi` when
  `INSTALL_GEX=1`.

## What Must Be Supplied Per Server

Do not commit these values to git and do not bake them into release tags.

- `/etc/signal-audit/llm.env`
  - Set `GEMINI_CHANNEL1_API_KEY` for the low-cost/free tier.
  - Set `GEMINI_CHANNEL2_API_KEY` for the paid fallback tier.
  - Keep mode `0600`.
- `/etc/gexmonitorapi.env`
  - Set `API_TOKEN` if GEX is installed.
  - Keep mode `0600`.
- FMZ runtime JSONL path
  - Default: `/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl`
  - Override with `JSONL_SOURCE=...` if the new server uses a different FMZ
    storage path.
- Optional history archives
  - `signal_review.jsonl`
  - `signal_llm_reviews.jsonl`
  - Copy them with `IMPORT_HISTORY_DIR=/path/to/archive`.

## Minimal Signal-Audit Bootstrap

Run as a sudo-capable user on the new server:

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

If the host does not yet have basic tools, add:

```bash
INSTALL_SYSTEM_PACKAGES=1 /tmp/server_bootstrap_signal_stack.sh
```

## Optional GEX Monitor API Bootstrap

GEX requires a headless browser stack, token configuration, and enough memory.
Use at least a 1 GB host with swap, and prefer 2 GB for smoother browser
refreshes.

The bootstrap syncs GEX source into `GEX_APP_DIR` with `rsync --delete`. Do not
store private cache/history or manual files inside that directory unless they
are backed up or intentionally disposable. The generated env template rewrites
`CACHE_FILE` and `HISTORY_FILE` to live under `GEX_STATE_DIR`, which defaults to
`/var/lib/gexmonitorapi`.

```bash
INSTALL_GEX=1 \
INSTALL_GEX_BROWSER=1 \
GEX_SERVICE_USER=bitnami \
GEX_SERVICE_GROUP=bitnami \
GEX_APP_DIR=/opt/gexmonitorapi \
GEX_STATE_DIR=/var/lib/gexmonitorapi \
/tmp/server_bootstrap_signal_stack.sh
```

After the script creates `/etc/gexmonitorapi.env`, edit `API_TOKEN`, then run:

```bash
sudo systemctl enable --now gexmonitorapi.service
```

## Import Historical Cards

Prepare a directory on the server:

```text
/tmp/signal-history/
  signal_review.jsonl
  signal_llm_reviews.jsonl
```

Then run:

```bash
IMPORT_HISTORY_DIR=/tmp/signal-history \
JSONL_SOURCE=/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl \
LLM_REVIEWS_SOURCE=/opt/signal-audit-tools/signal_llm_reviews.jsonl \
/tmp/server_bootstrap_signal_stack.sh
```

The import is explicit. The bootstrap does not copy private runtime history
unless `IMPORT_HISTORY_DIR` is set.

## Web Exposure

The static audit frontend is installed under `/opt/signal-audit`. Use one of
the examples in this directory:

- `apache-bitnami-signal-audit.conf.example`
- `nginx.signal-audit-location.conf.example`
- `nginx.signal-audit.conf.example`

The expected local URL for self-check is:

```text
http://127.0.0.1/signal-audit/
```

Expose GEX either directly on `:8000` or behind Nginx. The bootstrap defaults
`GEX_BIND_HOST=127.0.0.1`. Only set `GEX_BIND_HOST=0.0.0.0` when the service is
intentionally exposed directly and protected by firewall rules.

## Verification

Run:

```bash
cd /opt/repos/neutral-loop
git rev-parse --short HEAD
git describe --tags --exact-match

GEX_REQUIRED=0 LLM_REQUIRED=1 SESSION_CONTEXT_REQUIRED=1 sudo -E bash tools/server_self_check_signal_stack.sh --run-oneshots
```

Expected release output:

```text
<commit hash>
r3.2.1
```

Expected self-check summary:

```text
FAIL=0
```

`server_self_check_signal_stack.sh --run-oneshots` is active verification: it
starts the materializer and LLM sidecar units once. If either Gemini channel key
is configured, each new unreviewed card can trigger two Gemini calls: a blind
theoretical read and then full audit reconciliation. For production LLM
verification, set `LLM_REQUIRED=1`; the self-check then fails if either channel
key is not loaded or if the latest signal card does not have an OK two-call
sidecar review.
With the r3.2 two-channel standard, `GEMINI_CHANNEL1_API_KEY` is tried first
for cost control and `GEMINI_CHANNEL2_API_KEY` is the paid fallback. Channel 2
is used only when channel 1 returns a retryable capacity/network failure such
as 429, 5xx, or timeout; 400/schema/parse errors do not fall back because they
usually indicate a prompt or code defect. The sidecar records `api_key_route`
and `llm_call_routes` so operators can verify whether a review used channel 1,
channel 2, or mixed routing.
Warnings are acceptable only when they describe intentionally missing optional
state and `LLM_REQUIRED=0`. The signal-audit runtime is considered ready only
after the audit page and manifest return HTTP 200, the materializer service has
`Result=success`, and, when LLM is required, the latest card has
`blind_review_mode=two_call_strict` and `llm_call_count>=2`.
For r3.2.1 session-context acceptance, also set `SESSION_CONTEXT_REQUIRED=1`.
The self-check must fail if the latest real card is not from FMZ producer
`identity.strategy_version=1.4.1`, uses materializer compatibility backfill, or lacks
`SignalSessionPremiseDurabilityContext`, `clock_window`, `backtest_delta_pp`,
structured `validation_basis`, `confidence_policy`, or
`decision_matrix.temporal_durability`. This prevents a server/front-end update
from being mistaken for an updated FMZ signal-layer producer.

For a signal-audit-only migration, run the self-check with `GEX_REQUIRED=0`.
That skips the `gexmonitorapi.service`, `/health`, and authenticated `/v1/info`
checks while still verifying the audit page, materializer, timers, JSONL source,
and LLM sidecar state.

## Post-Migration Checks

Use this extra text check after the first materialization:

```bash
python3 -c 'import json,pathlib,re; root=pathlib.Path("/opt/signal-audit"); index=(root/"index.html").read_text(encoding="utf-8"); app=(root/"app.js").read_text(encoding="utf-8"); fallback=(root/"signal_cards/fallback.js").read_text(encoding="utf-8"); manifest=json.loads((root/"signal_cards/index.json").read_text(encoding="utf-8")); cards=manifest.get("cards") or []; blob=index+app+fallback+"".join((root/item["path"]).read_text(encoding="utf-8") for item in cards); checks=[("HAS_APP_R3_2_1","app.js?v=20260624-r3.2.1" in index),("HAS_LLM_SECTION","LLM 复核意见" in app),("HAS_PENDING_LLM","LLM 复核尚未生成" in app),("NO_GEMINI_LOCAL_PREVIEW","GEMINI-LOCAL-PREVIEW" not in blob),("NO_SYNTHETIC_TRUE","\"is_synthetic\": true" not in blob),("NO_OLD_CALIBRATION_TEXT",not re.search(r"\u7f6e\u4fe1\d+\u672a\u6821\u51c6|\u7f6e\u4fe1\u5ea6\d+\u672a\u6821\u51c6", blob))]; [print(k,v) for k,v in checks]; print("CARDS",len(cards),cards[0].get("card_id") if cards else None); raise SystemExit(0 if all(v for _,v in checks) else 1)'
```

## Rollback

The checkout is a normal git worktree:

```bash
cd /opt/repos/neutral-loop
git fetch xxproject --tags
git checkout -B deploy-r3.2.1 refs/tags/r3.2.1
sudo bash deploy/signal_audit/install_or_update.sh
```

Historical JSONL and env files live outside git; back them up before replacing
a server.
