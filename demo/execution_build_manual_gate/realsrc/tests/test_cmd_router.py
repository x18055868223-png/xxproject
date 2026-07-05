# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import cmd_router as CR
import fmz_shim


def _clear():
    fmz_shim._G(CR._CMD_LEDGER_KEY, None)


CTX = {"robot_id": "r1", "session_id": "s1", "refresh_seq": 7}


def test_parse_basic_and_aliases():
    c = CR.parse_command("执行:A4F2")
    assert c["type"] == "EXECUTE" and c["arg"] == "A4F2" and c["name"] == "执行"
    assert CR.parse_command("拒绝")["type"] == "REJECT"
    assert CR.parse_command("EXIT_AUTHORIZE:9C1E")["type"] == "EXIT_AUTHORIZE"
    assert CR.parse_command("授权止盈:Z9")["arg"] == "Z9"
    assert CR.parse_command("") is None and CR.parse_command(None) is None
    assert CR.parse_command("   ") is None
    assert CR.parse_command("乱七八糟:x")["type"] == "UNKNOWN"


def test_is_consume():
    assert CR.is_consume(CR.parse_command("执行:A4F2"))
    assert CR.is_consume(CR.parse_command("风险退出授权:Q1"))
    assert not CR.is_consume(CR.parse_command("急停"))
    assert not CR.is_consume(CR.parse_command("拒绝"))


def test_idempotency_key_structured():
    assert CR.idempotency_key("r1", "s1", 7, "EXECUTE", "A4F2") == "r1|s1|7|EXECUTE|A4F2"


def test_consume_command_dedup_within_session_refresh():
    _clear()
    r1 = CR.route_command("执行:A4F2", CTX, 1000)
    assert r1["status"] == "ACCEPTED" and r1["key"] == "r1|s1|7|EXECUTE|A4F2"
    CR.cmd_ledger_record(r1["command"], r1["key"], "ACCEPTED", "locked", 1000)
    r2 = CR.route_command("执行:A4F2", CTX, 1001)      # 同码重复 → 忽略（一次性消费）
    assert r2["status"] == "DUPLICATE"
    r3 = CR.route_command("执行:9C1E", CTX, 1002)      # 另一码 → 放行
    assert r3["status"] == "ACCEPTED"


def test_new_refresh_seq_revives_same_code():
    _clear()
    r1 = CR.route_command("执行:A4F2", CTX, 1000)
    CR.cmd_ledger_record(r1["command"], r1["key"], "ACCEPTED", "locked", 1000)
    ctx2 = dict(CTX); ctx2["refresh_seq"] = 8           # 库刷新 → 新快照 → 新键
    assert CR.route_command("执行:A4F2", ctx2, 1003)["status"] == "ACCEPTED"


def test_toggle_commands_not_deduped():
    _clear()
    a = CR.route_command("急停", CTX, 1000)
    CR.cmd_ledger_record(a["command"], a["key"], "ACCEPTED", "killed", 1000)
    b = CR.route_command("急停", CTX, 1001)
    assert a["status"] == "ACCEPTED" and b["status"] == "ACCEPTED"   # 切换型可重复
    assert a["key"] is None


def test_empty_and_unknown():
    _clear()
    assert CR.route_command("", CTX, 1)["status"] == "EMPTY"
    assert CR.route_command("foo:bar", CTX, 1)["status"] == "UNKNOWN"


def test_cmd_ledger_persists_and_caps():
    _clear()
    for i in range(CR._CMD_LEDGER_MAX + 10):
        CR.cmd_ledger_record(CR.parse_command("急停"), None, "ACCEPTED", "x", i)
    assert len(CR.cmd_ledger_load()) == CR._CMD_LEDGER_MAX        # 截断到上限
