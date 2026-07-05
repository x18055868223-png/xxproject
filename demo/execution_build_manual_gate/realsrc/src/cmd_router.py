# -*- coding: utf-8 -*-
"""交互命令路由 + 命令账本 + 幂等（cmd_*）。

把 FMZ `GetCommand()` 返回的 "名:参数" 解析、归一、去重并落审计账本。
解决补充意见 P0-2：
  - 幂等键 = robot_id + session_id + refresh_seq + command_type + nonce（**非**原始命令串哈希）；
  - 「消费型」命令（执行/授权）一次性消费：同键已在历史账本则忽略
    （防跨轮/重启重复下单、防延迟旧命令命中新方案）；
  - 「切换型」命令（拒绝/撤销/急停/恢复）不去重（幂等动作可重复应用），但仍全部入账本审计。

注：FMZ `GetCommand()` 在回测系统不生效，须真实机器人空跑验收。
按钮名↔命令类型见 COMMAND_ALIASES（中文按钮名与英文类型双向）。
"""
from fmz_shim import _G

# 中文按钮名 / 英文类型 → 规范类型
COMMAND_ALIASES = {
    "执行": "EXECUTE", "EXECUTE": "EXECUTE",
    "拒绝": "REJECT", "REJECT": "REJECT",
    "授权止盈": "EXIT_AUTHORIZE", "EXIT_AUTHORIZE": "EXIT_AUTHORIZE",
    "撤销授权": "EXIT_REVOKE", "EXIT_REVOKE": "EXIT_REVOKE",
    "风险退出授权": "RISK_EXIT_AUTHORIZE", "RISK_EXIT_AUTHORIZE": "RISK_EXIT_AUTHORIZE",
    "急停": "KILL", "KILL": "KILL",
    "恢复": "RESUME", "RESUME": "RESUME",
}

# 一次性消费型（触发下单 / 授权等不可重复后果）→ 严格幂等
CONSUME_TYPES = frozenset({"EXECUTE", "EXIT_AUTHORIZE", "RISK_EXIT_AUTHORIZE"})

_CMD_LEDGER_KEY = "spm_cmd_ledger_v1"
_CMD_LEDGER_MAX = 200


def parse_command(raw):
    """'名:参数' 或 '名' → {"raw","name","type","arg"}；空串 / None 返回 None。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if ":" in s:
        name, arg = s.split(":", 1)
    else:
        name, arg = s, ""
    name = name.strip()
    return {"raw": s, "name": name,
            "type": COMMAND_ALIASES.get(name, "UNKNOWN"), "arg": arg.strip()}


def is_consume(command):
    return bool(command) and command.get("type") in CONSUME_TYPES


def idempotency_key(robot_id, session_id, refresh_seq, command_type, nonce):
    """结构化幂等键：robot_id|session_id|refresh_seq|command_type|nonce。"""
    return "|".join(str(x) for x in
                    (robot_id, session_id, refresh_seq, command_type, nonce))


def _nonce_for(command):
    # 消费型用 arg（确认码/授权码）作 nonce → 一次性消费、码变即新命令
    return command.get("arg") or command.get("type")


# ---------- 命令账本（_G 持久化，跨重启可查）----------

def cmd_ledger_load():
    return list(_G(_CMD_LEDGER_KEY) or [])


def cmd_ledger_save(records):
    trimmed = records[-_CMD_LEDGER_MAX:]
    _G(_CMD_LEDGER_KEY, trimmed)
    return trimmed


def cmd_ledger_has_key(key):
    if not key:
        return False
    return any(r.get("key") == key for r in cmd_ledger_load())


def cmd_ledger_record(command, key, status, outcome, now_ts):
    recs = cmd_ledger_load()
    recs.append({"key": key, "type": (command or {}).get("type"),
                 "name": (command or {}).get("name"), "arg": (command or {}).get("arg"),
                 "status": status, "outcome": outcome, "ts": now_ts})
    return cmd_ledger_save(recs)


# ---------- 路由 ----------

def route_command(raw, ctx, now_ts):
    """解析 + 幂等判定。ctx={robot_id, session_id, refresh_seq}。
    返回 {"status", "command", "key"}；status ∈ EMPTY / UNKNOWN / DUPLICATE / ACCEPTED。
    ACCEPTED：调用方应处理该命令并在处理后调用 cmd_ledger_record 落账（消费型据此一次性消费）。"""
    cmd = parse_command(raw)
    if cmd is None:
        return {"status": "EMPTY", "command": None, "key": None}
    if cmd["type"] == "UNKNOWN":
        return {"status": "UNKNOWN", "command": cmd, "key": None}
    key = None
    if is_consume(cmd):
        key = idempotency_key(ctx.get("robot_id"), ctx.get("session_id"),
                              ctx.get("refresh_seq"), cmd["type"], _nonce_for(cmd))
        if cmd_ledger_has_key(key):
            return {"status": "DUPLICATE", "command": cmd, "key": key}
    return {"status": "ACCEPTED", "command": cmd, "key": key}
