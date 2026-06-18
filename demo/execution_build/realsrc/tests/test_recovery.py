# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import ledger as LG


def test_ok_clean_state():
    r = LG.evaluate_startup_recovery([], 0.0, 0.0, [])
    assert r["state"] == "OK" and r["allow_new_open"]


def test_unknown_active_orders_block():
    r = LG.evaluate_startup_recovery([], 0.0, 0.0, [{"order_id": "x"}])  # 无 label → 身份不明
    assert r["state"] == "RECOVERY_BLOCKED" and not r["allow_new_open"]
    assert any("UNKNOWN_ACTIVE_ORDERS" in c for c in r["reasons"])


def test_known_label_orders_ok():
    r = LG.evaluate_startup_recovery([{"instrument": "S", "size": -0.1}], 0.0, 0.1,
                                     [{"order_id": "x", "label": "short"}])
    assert r["state"] == "OK"


def test_record_short_but_no_exchange_option_blocks():
    r = LG.evaluate_startup_recovery([], 0.0, 0.1, [])   # 记录有短腿但交易所无期权
    assert r["state"] == "RECOVERY_BLOCKED"
    assert any("RECORD_SHORT_BUT_NO_EXCHANGE_OPTION" in c for c in r["reasons"])


def test_exchange_option_but_no_record_blocks():
    # P0①：交易所有期权但持仓记录(快照)无 → 阻塞（防 v3 持仓未被恢复看见→重复开仓）
    r = LG.evaluate_startup_recovery([{"instrument": "S", "size": -0.1}], 0.0, 0.0, [])
    assert r["state"] == "RECOVERY_BLOCKED"
    assert any("EXCHANGE_OPTION_BUT_NO_RECORDED_POSITION" in c for c in r["reasons"])


def test_protection_only_after_short_flat_ok():
    # 短腿归零、保护腿仍持有(expected_long>0) → 可解释，不阻塞
    r = LG.evaluate_startup_recovery([{"instrument": "L", "size": 0.1}], 0.0, 0.0, [],
                                     expected_long_qty=0.1)
    assert r["state"] == "OK"


def test_orphan_hedge_emergency():
    # 有 BTC-PERPETUAL 对冲持仓但无期权卖方风险 → 孤儿对冲紧急态
    r = LG.evaluate_startup_recovery([], 0.5, 0.0, [])
    assert r["state"] == "ORPHAN_HEDGE_EMERGENCY" and not r["allow_new_open"]


def test_matched_option_and_ledger_ok():
    r = LG.evaluate_startup_recovery([{"instrument": "S", "size": -0.1}], 0.0, 0.1, [])
    assert r["state"] == "OK" and r["allow_new_open"]
