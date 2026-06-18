# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import fmz_shim
import ledger as LG


def test_inventory_alloc_release():
    inv = LG.ledger_make_inventory("BTC-X", "CALL", 80000, 1, 1.0, 0.01, 0.0001, 0.5)
    led = {"protection": inv, "short": None, "history": []}
    assert LG.ledger_allocate_short(led, 0.6) is True
    assert abs(inv["amount_free"] - 0.4) < 1e-9 and abs(inv["amount_allocated"] - 0.6) < 1e-9
    assert LG.ledger_allocate_short(led, 0.5) is False    # 超过可用 → 拒绝
    LG.ledger_release_short(led, 0.6)
    assert abs(inv["amount_free"] - 1.0) < 1e-9


def test_persist_roundtrip_and_state():
    fmz_shim._STORE.clear()
    led = LG.ledger_load()
    led["short"] = {"instrument": "BTC-S-1"}
    LG.ledger_save(led)
    assert LG.ledger_load()["short"]["instrument"] == "BTC-S-1"   # _G 持久化恢复
    LG.ledger_set_state(LG.S_SHORT_ACTIVE_PROTECTED)
    assert LG.ledger_get_state() == LG.S_SHORT_ACTIVE_PROTECTED


def test_can_enter():
    enter = ("TRADE_SUPPORT_STRONG", "TRADE_SUPPORT_WEAK")
    assert LG.ledger_can_enter("TRADE_SUPPORT_WEAK", enter) is True
    assert LG.ledger_can_enter("NO_TRADE_BLOCKED", enter) is False


def test_reconcile_warns_on_mismatch():
    fmz_shim._STORE.clear()
    # 账本记保护腿，但交易所(默认 mock)无持仓 → 不抛异常，返回结构
    inv = LG.ledger_make_inventory("BTC-X", "CALL", 80000, 1, 1.0, 0.01, 0.0001, 0.5)
    LG.ledger_save({"protection": inv, "short": None, "history": []})
    fmz_shim.exchange.io_handler = lambda *a: {"result": []}
    out = LG.ledger_reconcile("BTC")
    assert "BTC-X" in out["expected"] and out["actual"] == {}
