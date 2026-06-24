#!/usr/bin/env bash
# Bootstrap a new neutral-loop signal audit server from the xxproject release.
#
# Defaults are safe for a fresh host: install the signal-audit static stack,
# create env templates, and run the self-check. Secrets and historical JSONL
# archives must be provided outside git.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/x18055868223-png/xxproject.git}"
RELEASE_REF="${RELEASE_REF:-r3.2.1}"
REPO_DIR="${REPO_DIR:-/opt/repos/neutral-loop}"

STATIC_ROOT="${STATIC_ROOT:-/opt/signal-audit}"
TOOLS_ROOT="${TOOLS_ROOT:-/opt/signal-audit-tools}"
CONFIG_ROOT="${CONFIG_ROOT:-/etc/signal-audit}"
LLM_ENV_FILE="${LLM_ENV_FILE:-${CONFIG_ROOT}/llm.env}"
JSONL_SOURCE="${JSONL_SOURCE:-/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl}"
LLM_REVIEWS_SOURCE="${LLM_REVIEWS_SOURCE:-${TOOLS_ROOT}/signal_llm_reviews.jsonl}"
MAX_CARDS="${MAX_CARDS:-200}"

GEX_ENV_FILE="${GEX_ENV_FILE:-/etc/gexmonitorapi.env}"
GEX_APP_DIR="${GEX_APP_DIR:-/opt/gexmonitorapi}"
GEX_STATE_DIR="${GEX_STATE_DIR:-/var/lib/gexmonitorapi}"
GEX_BIND_HOST="${GEX_BIND_HOST:-127.0.0.1}"
GEX_PORT="${GEX_PORT:-8000}"
GEX_SERVICE_USER="${GEX_SERVICE_USER:-${SUDO_USER:-${USER:-ubuntu}}}"
GEX_SERVICE_GROUP="${GEX_SERVICE_GROUP:-$GEX_SERVICE_USER}"

INSTALL_SYSTEM_PACKAGES="${INSTALL_SYSTEM_PACKAGES:-0}"
INSTALL_SIGNAL_AUDIT="${INSTALL_SIGNAL_AUDIT:-1}"
INSTALL_GEX="${INSTALL_GEX:-0}"
INSTALL_GEX_BROWSER="${INSTALL_GEX_BROWSER:-0}"
GEX_REQUIRED="${GEX_REQUIRED:-$INSTALL_GEX}"
RUN_SELF_CHECK="${RUN_SELF_CHECK:-1}"
IMPORT_HISTORY_DIR="${IMPORT_HISTORY_DIR:-}"

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  SUDO=()
else
  SUDO=(sudo)
fi

log() {
  printf '\n== %s ==\n' "$1"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

need() {
  if ! have "$1"; then
    echo "missing required command: $1" >&2
    echo "Set INSTALL_SYSTEM_PACKAGES=1 on Debian/Ubuntu, or install it manually." >&2
    exit 2
  fi
}

run_as_gex_user() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    runuser -u "$GEX_SERVICE_USER" -- "$@"
  else
    sudo -u "$GEX_SERVICE_USER" "$@"
  fi
}

find_gex_source_dir() {
  local candidate
  for candidate in "$REPO_DIR"/05_GEX*; do
    if [[ -d "$candidate/deploy" && -f "$candidate/pyproject.toml" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

systemd_escape_value() {
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

install_system_packages() {
  if [[ "$INSTALL_SYSTEM_PACKAGES" != "1" ]]; then
    return
  fi
  if ! have apt-get; then
    echo "INSTALL_SYSTEM_PACKAGES=1 is only implemented for apt-get hosts." >&2
    exit 2
  fi
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y git rsync curl python3 python3-venv python3-pip
}

checkout_release() {
  need git
  local parent
  parent="$(dirname "$REPO_DIR")"
  "${SUDO[@]}" install -d "$parent"
  if [[ ! -d "$REPO_DIR/.git" ]]; then
    "${SUDO[@]}" git clone "$REPO_URL" "$REPO_DIR"
  fi
  "${SUDO[@]}" git -C "$REPO_DIR" remote set-url xxproject "$REPO_URL" 2>/dev/null \
    || "${SUDO[@]}" git -C "$REPO_DIR" remote add xxproject "$REPO_URL"
  "${SUDO[@]}" git -C "$REPO_DIR" fetch xxproject \
    "+refs/heads/main:refs/remotes/xxproject/main" \
    "+refs/tags/${RELEASE_REF}:refs/tags/${RELEASE_REF}"
  local target
  target="$("${SUDO[@]}" git -C "$REPO_DIR" rev-parse "refs/tags/${RELEASE_REF}^{}")"
  "${SUDO[@]}" git -C "$REPO_DIR" checkout -B "deploy-${RELEASE_REF}" "$target"
  "${SUDO[@]}" git -C "$REPO_DIR" rev-parse --short HEAD
  "${SUDO[@]}" git -C "$REPO_DIR" describe --tags --exact-match
}

import_history() {
  if [[ -z "$IMPORT_HISTORY_DIR" ]]; then
    return
  fi
  if [[ ! -d "$IMPORT_HISTORY_DIR" ]]; then
    echo "IMPORT_HISTORY_DIR is not a directory: $IMPORT_HISTORY_DIR" >&2
    exit 2
  fi
  if [[ -f "$IMPORT_HISTORY_DIR/signal_review.jsonl" ]]; then
    "${SUDO[@]}" install -D -m 0644 \
      "$IMPORT_HISTORY_DIR/signal_review.jsonl" "$JSONL_SOURCE"
    echo "imported signal_review.jsonl -> $JSONL_SOURCE"
  fi
  if [[ -f "$IMPORT_HISTORY_DIR/signal_llm_reviews.jsonl" ]]; then
    "${SUDO[@]}" install -D -m 0644 \
      "$IMPORT_HISTORY_DIR/signal_llm_reviews.jsonl" "$LLM_REVIEWS_SOURCE"
    echo "imported signal_llm_reviews.jsonl -> $LLM_REVIEWS_SOURCE"
  fi
}

install_signal_audit() {
  if [[ "$INSTALL_SIGNAL_AUDIT" != "1" ]]; then
    return
  fi
  need rsync
  need python3
  "${SUDO[@]}" env \
    STATIC_ROOT="$STATIC_ROOT" \
    TOOLS_ROOT="$TOOLS_ROOT" \
    CONFIG_ROOT="$CONFIG_ROOT" \
    LLM_ENV_FILE="$LLM_ENV_FILE" \
    JSONL_SOURCE="$JSONL_SOURCE" \
    LLM_REVIEWS_SOURCE="$LLM_REVIEWS_SOURCE" \
    MAX_CARDS="$MAX_CARDS" \
    bash "$REPO_DIR/deploy/signal_audit/install_or_update.sh"
  install_signal_audit_dropins
}

install_signal_audit_dropins() {
  local mat_dir llm_dir temp_conf
  mat_dir="/etc/systemd/system/signal-audit-materialize.service.d"
  llm_dir="/etc/systemd/system/signal-audit-llm-review.service.d"
  "${SUDO[@]}" install -d "$mat_dir" "$llm_dir"

  temp_conf="$(mktemp)"
  cat > "$temp_conf" <<EOF
[Service]
Environment="TOOLS_ROOT=$(systemd_escape_value "$TOOLS_ROOT")"
Environment="STATIC_ROOT=$(systemd_escape_value "$STATIC_ROOT")"
Environment="JSONL_SOURCE=$(systemd_escape_value "$JSONL_SOURCE")"
Environment="LLM_REVIEWS_SOURCE=$(systemd_escape_value "$LLM_REVIEWS_SOURCE")"
Environment="MAX_CARDS=$(systemd_escape_value "$MAX_CARDS")"
ExecStart=
ExecStart=/usr/bin/python3 \${TOOLS_ROOT}/materialize_signal_cards.py --source \${JSONL_SOURCE} --output \${STATIC_ROOT} --max-cards \${MAX_CARDS} --llm-reviews \${LLM_REVIEWS_SOURCE}
EOF
  "${SUDO[@]}" install -m 0644 "$temp_conf" "$mat_dir/10-bootstrap-overrides.conf"
  rm -f "$temp_conf"

  temp_conf="$(mktemp)"
  cat > "$temp_conf" <<EOF
[Service]
Environment="TOOLS_ROOT=$(systemd_escape_value "$TOOLS_ROOT")"
Environment="JSONL_SOURCE=$(systemd_escape_value "$JSONL_SOURCE")"
Environment="LLM_REVIEWS_SOURCE=$(systemd_escape_value "$LLM_REVIEWS_SOURCE")"
EnvironmentFile=
EnvironmentFile=-$(systemd_escape_value "$LLM_ENV_FILE")
ExecStart=
ExecStart=\${TOOLS_ROOT}/run_signal_llm_review.sh
ExecStartPost=
ExecStartPost=/bin/systemctl start signal-audit-materialize.service
EOF
  "${SUDO[@]}" install -m 0644 "$temp_conf" "$llm_dir/10-bootstrap-overrides.conf"
  rm -f "$temp_conf"

  "${SUDO[@]}" systemctl daemon-reload
}

install_gex() {
  if [[ "$INSTALL_GEX" != "1" ]]; then
    return
  fi
  need python3
  need rsync
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    need runuser
  fi
  local source_dir
  source_dir="$(find_gex_source_dir || true)"
  if [[ ! -d "$source_dir" ]]; then
    echo "missing GEX source directory under $REPO_DIR/05_GEX*" >&2
    exit 2
  fi
  "${SUDO[@]}" install -d -o "$GEX_SERVICE_USER" -g "$GEX_SERVICE_GROUP" "$GEX_APP_DIR" "$GEX_STATE_DIR"
  "${SUDO[@]}" rsync -a --delete --exclude .venv "$source_dir"/ "$GEX_APP_DIR"/
  "${SUDO[@]}" chown -R "$GEX_SERVICE_USER:$GEX_SERVICE_GROUP" "$GEX_APP_DIR"
  "${SUDO[@]}" chown -R "$GEX_SERVICE_USER:$GEX_SERVICE_GROUP" "$GEX_STATE_DIR"
  run_as_gex_user python3 -m venv "$GEX_APP_DIR/.venv"
  run_as_gex_user "$GEX_APP_DIR/.venv/bin/pip" install --upgrade pip
  run_as_gex_user "$GEX_APP_DIR/.venv/bin/pip" install -e "$GEX_APP_DIR"

  if [[ "$INSTALL_GEX_BROWSER" = "1" ]]; then
    "${SUDO[@]}" "$GEX_APP_DIR/.venv/bin/playwright" install-deps chromium
    run_as_gex_user "$GEX_APP_DIR/.venv/bin/scrapling" install
    run_as_gex_user "$GEX_APP_DIR/.venv/bin/playwright" install chromium
  fi

  if [[ ! -f "$GEX_ENV_FILE" ]]; then
    local temp_env
    temp_env="$(mktemp)"
    sed \
      -e "s|^CACHE_FILE=.*|CACHE_FILE=${GEX_STATE_DIR}/cache.json|" \
      -e "s|^HISTORY_FILE=.*|HISTORY_FILE=${GEX_STATE_DIR}/metrics_history.jsonl|" \
      "$source_dir/deploy/gexmonitorapi.env.example" > "$temp_env"
    "${SUDO[@]}" install -m 0600 "$temp_env" "$GEX_ENV_FILE"
    rm -f "$temp_env"
    echo "created $GEX_ENV_FILE; edit API_TOKEN before starting gexmonitorapi"
  else
    "${SUDO[@]}" chmod 0600 "$GEX_ENV_FILE"
  fi

  local temp_unit
  temp_unit="$(mktemp)"
  sed \
    -e "s|^User=.*|User=${GEX_SERVICE_USER}|" \
    -e "s|^Group=.*|Group=${GEX_SERVICE_GROUP}|" \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=${GEX_APP_DIR}|" \
    -e "s|^EnvironmentFile=.*|EnvironmentFile=${GEX_ENV_FILE}|" \
    -e "s|^ExecStart=.*|ExecStart=${GEX_APP_DIR}/.venv/bin/python -m uvicorn gexmonitorapi.app:app --host ${GEX_BIND_HOST} --port ${GEX_PORT}|" \
    "$source_dir/deploy/gexmonitorapi.service" > "$temp_unit"
  "${SUDO[@]}" install -m 0644 "$temp_unit" /etc/systemd/system/gexmonitorapi.service
  rm -f "$temp_unit"
  "${SUDO[@]}" systemctl daemon-reload
  if "${SUDO[@]}" grep -q "REPLACE_WITH" "$GEX_ENV_FILE"; then
    echo "gexmonitorapi.service installed but not started; edit $GEX_ENV_FILE first"
  else
    "${SUDO[@]}" systemctl enable --now gexmonitorapi.service
  fi
}

self_check() {
  if [[ "$RUN_SELF_CHECK" != "1" ]]; then
    return
  fi
  "${SUDO[@]}" env \
    JSONL_SOURCE="$JSONL_SOURCE" \
    AUDIT_ROOT="$STATIC_ROOT" \
    TOOLS_ROOT="$TOOLS_ROOT" \
    LLM_REVIEWS_SOURCE="$LLM_REVIEWS_SOURCE" \
    GEX_REQUIRED="$GEX_REQUIRED" \
    GEX_ENV="$GEX_ENV_FILE" \
    LLM_ENV="$LLM_ENV_FILE" \
    SESSION_CONTEXT_REQUIRED=1 bash "$REPO_DIR/tools/server_self_check_signal_stack.sh" --run-oneshots
}

log "system packages"
install_system_packages
need git
need curl

log "checkout ${RELEASE_REF}"
checkout_release

log "optional history import"
import_history

log "signal audit"
install_signal_audit

log "optional GEX monitor"
install_gex

log "self check"
self_check

cat <<EOF

Bootstrap complete.

Important files to review:
- LLM env: ${LLM_ENV_FILE}
- GEX env: ${GEX_ENV_FILE}
- FMZ signal JSONL source: ${JSONL_SOURCE}
- LLM sidecar JSONL: ${LLM_REVIEWS_SOURCE}

Secrets are templates only. Fill GEMINI_CHANNEL1_API_KEY/GEMINI_CHANNEL2_API_KEY/API_TOKEN on the server,
then rerun the self-check:
  SESSION_CONTEXT_REQUIRED=1 sudo -E bash ${REPO_DIR}/tools/server_self_check_signal_stack.sh --run-oneshots
EOF
