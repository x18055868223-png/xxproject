#!/usr/bin/env bash
set -euo pipefail

# Non-destructive Linux reverse security verification for isolated execution + read-only Workbench cockpit.
# Verifies bitnami and astra-workbench cannot reach control.sock / SQLite / control secrets,
# Workbench rejects all execution POSTs (403), while P5 read-only status and root /health-fresh work.

WB_URL="${WB_URL:-http://127.0.0.1:8895}"
SOCK="${SOCK:-/run/astra-exec/control.sock}"
STATUS_FILE="${STATUS_FILE:-/var/lib/xxproject/execution/public_status.json}"
DB_FILE="${DB_FILE:-/var/lib/xxproject/execution/exec_store.sqlite}"
CTRL_TOK_FILE="${CTRL_TOK_FILE:-/etc/signal-audit/exec-control.token}"
WB_ENV_FILE="${WB_ENV_FILE:-/etc/signal-audit/workbench-v20.env}"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

[[ "$(id -u)" -eq 0 ]] || fail "must run as root to verify cross-user isolation"

# 1. OS accounts, groups & kernel yama ptrace_scope
id -u astra-exec >/dev/null 2>&1 || fail "missing user astra-exec"
id -u astra-workbench >/dev/null 2>&1 || fail "missing user astra-workbench"
[[ "$(id -u bitnami)" != "$(id -u astra-workbench)" ]] || fail "bitnami UID equals astra-workbench UID"
[[ "$(id -u bitnami)" != "$(id -u astra-exec)" ]] || fail "bitnami UID equals astra-exec UID"
[[ "$(id -u astra-workbench)" != "$(id -u astra-exec)" ]] || fail "astra-workbench UID equals astra-exec UID"
if id -nG bitnami | tr ' ' '\n' | grep -qx 'astra-exec'; then
  fail "bitnami must NOT belong to group astra-exec"
fi
if id -nG astra-workbench | tr ' ' '\n' | grep -qx 'astra-exec'; then
  fail "read-only astra-workbench must NOT belong to group astra-exec"
fi
ptrace_scope="$(sysctl -n kernel.yama.ptrace_scope 2>/dev/null || echo 0)"
[[ "${ptrace_scope}" -ge 1 ]] || fail "kernel.yama.ptrace_scope=${ptrace_scope} (< 1)"
pass "1. OS users separated & kernel.yama.ptrace_scope=${ptrace_scope}"

# 2. bitnami & astra-workbench denied on control.sock & SQLite, yet P5/bitnami CAN read public_status.json
[[ -S "${SOCK}" ]] || fail "control socket missing: ${SOCK}"
for u in bitnami astra-workbench; do
  if su -s /bin/bash "${u}" -c "python3 -c \"import socket; s=socket.socket(socket.AF_UNIX); s.connect('${SOCK}')\"" 2>/dev/null; then
    fail "${u} connected to ${SOCK}"
  fi
  if [[ -f "${DB_FILE}" ]] && su -s /bin/bash "${u}" -c "head -c 16 '${DB_FILE}'" >/dev/null 2>&1; then
    fail "${u} read ${DB_FILE}"
  fi
done
if [[ -f "${STATUS_FILE}" ]]; then
  su -s /bin/bash bitnami -c "test -r '${STATUS_FILE}'" || fail "bitnami/P5 cannot read ${STATUS_FILE}"
  su -s /bin/bash astra-workbench -c "test -r '${STATUS_FILE}'" || fail "astra-workbench cannot read ${STATUS_FILE}"
fi
pass "2. bitnami & astra-workbench blocked from socket & SQLite; public_status.json remains readable"

# 3. bitnami denied on secrets, session stores & workbench /proc environ
for sec in "${CTRL_TOK_FILE}" "/var/lib/xxproject/workbench/sessions.json"; do
  if [[ -e "${sec}" ]] && su -s /bin/bash bitnami -c "cat '${sec}'" >/dev/null 2>&1; then
    fail "bitnami read protected file ${sec}"
  fi
done
wb_pid="$(systemctl show astra-workbench-v20.service -p MainPID --value 2>/dev/null || echo 0)"
[[ "${wb_pid}" =~ ^[0-9]+$ && "${wb_pid}" -gt 0 ]] || fail "astra-workbench-v20.service not running"
wb_expected_uid="$(id -u astra-workbench)"
wb_proc_uid="$(ps -o uid= -p "${wb_pid}" | tr -d ' ')"
[[ "${wb_proc_uid}" == "${wb_expected_uid}" ]] || fail "workbench running as UID ${wb_proc_uid} instead of astra-workbench (${wb_expected_uid})"
exec_pid="$(systemctl show astra-execution-coordinator.service -p MainPID --value 2>/dev/null || echo 0)"
[[ "${exec_pid}" =~ ^[0-9]+$ && "${exec_pid}" -gt 0 ]] || fail "astra-execution-coordinator.service not running"
exec_expected_uid="$(id -u astra-exec)"
exec_proc_uid="$(ps -o uid= -p "${exec_pid}" | tr -d ' ')"
[[ "${exec_proc_uid}" == "${exec_expected_uid}" ]] || fail "coordinator running as UID ${exec_proc_uid} instead of astra-exec (${exec_expected_uid})"
if su -s /bin/bash bitnami -c "cat '/proc/${wb_pid}/environ'" >/dev/null 2>&1; then
  fail "bitnami read /proc/${wb_pid}/environ"
fi
if su -s /bin/bash bitnami -c "cat '/proc/${exec_pid}/environ'" >/dev/null 2>&1; then
  fail "bitnami read /proc/${exec_pid}/environ"
fi
if su -s /bin/bash astra-workbench -c "cat '/proc/${exec_pid}/environ'" >/dev/null 2>&1; then
  fail "astra-workbench read /proc/${exec_pid}/environ"
fi
pass "3. bitnami & astra-workbench blocked from secrets & coordinator environ (wb=${wb_proc_uid}, exec=${exec_proc_uid})"

# 4. Cross-UID ptrace attempt denied
su -s /bin/bash bitnami -c "python3 -c \"import ctypes, sys; libc=ctypes.CDLL('libc.so.6', use_errno=True); r=libc.ptrace(16, ${wb_pid}, 0, 0); sys.exit(0 if r == -1 else 1)\"" \
  || fail "bitnami ptrace on PID ${wb_pid} was not denied"
pass "4. bitnami ptrace on astra-workbench denied"

# 5. Read-only cockpit verification: GET /execution/live returns 200; all execution POSTs rejected with 403
wb_tok="$(grep -E '^ASTRA_WORKBENCH_TOKEN=' "${WB_ENV_FILE}" | head -n1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^[[:space:]]*//;s/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/" || true)"
[[ -n "${wb_tok}" ]] || fail "missing ASTRA_WORKBENCH_TOKEN in ${WB_ENV_FILE}"
ex_body="$(curl -sS -w '\n%{http_code}' -H "Authorization: Bearer ${wb_tok}" "${WB_URL}/api/workbench/execution/live")"
code_ro="${ex_body##*$'\n'}"
ex_json="${ex_body%$'\n'*}"
[[ "${code_ro}" == "200" ]] || fail "P5 read-only GET /execution/live returned HTTP ${code_ro} (expected 200)"
python3 -c 'import json,sys; d=json.loads(sys.argv[1]); assert d.get("mode")=="SHADOW_ONLY" and d.get("cockpit_mode")=="READ_ONLY" and d.get("writes_enabled") is False, d' "${ex_json}" \
  || fail "GET /execution/live did not confirm SHADOW_ONLY + READ_ONLY + writes_enabled=false"
for ep in capital-policy exit-all disable-writes approve halt-entry session; do
  code_post="$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H "Origin: ${WB_URL}" -H "Authorization: Bearer ${wb_tok}" -H "Content-Type: application/json" -d '{}' "${WB_URL}/api/workbench/execution/${ep}")"
  [[ "${code_post}" == "403" ]] || fail "POST /execution/${ep} returned HTTP ${code_post} (expected 403)"
done
pass "5. P5 GET /execution/live allowed (200); all Workbench execution POSTs rejected (403)"

# 6. Root deploy_release control socket check passes auth (200 or 503 if unconfigured testnet, never 401/403)
ctrl_tok="$(cat "${CTRL_TOK_FILE}")"
code_root="$(curl -sS -o /dev/null -w '%{http_code}' --unix-socket "${SOCK}" -H "Authorization: Bearer ${ctrl_tok}" http://localhost/health-fresh)"
[[ "${code_root}" != "401" && "${code_root}" != "403" ]] || fail "root SO_PEERCRED /health-fresh rejected with HTTP ${code_root}"
pass "6. Root control socket auth accepted (HTTP ${code_root})"

# 7. Bad token fails closed with 401 on control.sock
code_bad="$(curl -sS -o /dev/null -w '%{http_code}' --unix-socket "${SOCK}" -H "Authorization: Bearer invalid-probe" http://localhost/health-fresh)"
[[ "${code_bad}" == "401" ]] || fail "invalid token expected 401, got ${code_bad}"
pass "7. Invalid exec control token fails closed (HTTP 401)"

# 8. Cross-UID shared LLM & workbench paths preserve group research setgid and astra-workbench rw without loosening execution store
LLM_DIR="/var/lib/xxproject/llm"
WB_STATE_DIR="/var/lib/xxproject/workbench"
if [[ -d "${LLM_DIR}" ]]; then
  llm_grp="$(stat -c '%G' "${LLM_DIR}")"
  llm_mode="$(stat -c '%a' "${LLM_DIR}")"
  [[ "${llm_grp}" == "research" && "${llm_mode}" == 2* ]] || fail "${LLM_DIR} group=${llm_grp} mode=${llm_mode} (expected group=research setgid)"
  su -s /bin/bash astra-workbench -c "test -r '${LLM_DIR}' -a -w '${LLM_DIR}' -a -x '${LLM_DIR}'" || fail "astra-workbench lacks rwx on ${LLM_DIR}"
  for lf in "${LLM_DIR}/usage.json" "${LLM_DIR}/usage.json.lock"; do
    if [[ -e "${lf}" ]]; then
      su -s /bin/bash astra-workbench -c "test -r '${lf}' -a -w '${lf}'" || fail "astra-workbench lacks rw on ${lf}"
      su -s /bin/bash bitnami -c "test -r '${lf}' -a -w '${lf}'" || fail "bitnami lacks rw on ${lf}"
    fi
  done
fi
if [[ -d "${WB_STATE_DIR}" ]]; then
  su -s /bin/bash astra-workbench -c "test -r '${WB_STATE_DIR}' -a -w '${WB_STATE_DIR}' -a -x '${WB_STATE_DIR}'" || fail "astra-workbench lacks rwx on ${WB_STATE_DIR}"
fi
pass "8. Shared llm/workbench cross-UID permissions verified"
echo "ALL_LINUX_SECURITY_CHECKS_PASSED"
