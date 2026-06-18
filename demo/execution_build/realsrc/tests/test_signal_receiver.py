# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import signal_receiver as SR
import fmz_shim


def _pkg(now=1000, ttl=90000, side="put_credit_spread", **over):
    p = {
        "schema_name": "SignalEvidencePackage",
        "schema_version": "nrd.integration.signal.v0.1",
        "package_id": "pkg-1",
        "created_ts": now,
        "expires_ts": now + ttl,
        "strategy_recommendation": {"expiry_hours": 24, "side_hint": side},
        "data_quality": {"state": "OK"},
        "reject_state": {},
    }
    p.update(over)
    return p


def test_validate_ok():
    v = SR.validate_signal_package(_pkg(), 1500)
    assert v["availability"] == "OK" and v["tradeable"] and not v["block_new_opens"]
    assert v["side_hint"] == "put_credit_spread" and v["expiry_hours"] == 24


def test_missing_and_not_dict():
    assert SR.validate_signal_package(None, 1)["availability"] == "MISSING"
    assert SR.validate_signal_package({}, 1)["block_new_opens"]


def test_schema_name_and_version_mismatch():
    assert SR.validate_signal_package(_pkg(schema_name="Other"), 1500)["availability"] == "SCHEMA_MISMATCH"
    assert SR.validate_signal_package(_pkg(schema_version="other.v1"), 1500)["availability"] == "SCHEMA_MISMATCH"


def test_expired_ttl_blocks():
    v = SR.validate_signal_package(_pkg(now=1000, ttl=100), 2000)   # expires 1100 < 2000
    assert v["availability"] == "STALE" and v["block_new_opens"]


def test_rejected_and_bad_quality():
    assert SR.validate_signal_package(_pkg(reject_state={"state": "BLOCKED"}), 1500)["availability"] == "REJECTED"
    assert SR.validate_signal_package(_pkg(data_quality={"state": "STALE"}), 1500)["availability"] == "BAD_QUALITY"
    assert SR.validate_signal_package(_pkg(data_quality={"ok": False}), 1500)["availability"] == "BAD_QUALITY"


def test_no_executable_side_blocks():
    v = SR.validate_signal_package(
        _pkg(strategy_recommendation={"expiry_hours": 24, "side_hint": "none"}), 1500)
    assert v["availability"] == "NO_SIDE" and v["block_new_opens"]


def test_receive_offline_manual_does_not_block():
    v = SR.receive_signal(1500, "OFFLINE_MANUAL")
    assert v["availability"] == "OFFLINE_MANUAL"
    assert v["tradeable"] is None and v["block_new_opens"] is False


def test_receive_file_missing_blocks_new_opens():
    v = SR.receive_signal(1500, "FILE", file_path="/no/such/signal_evidence.json")
    assert v["availability"] == "MISSING" and v["block_new_opens"]


def test_receive_from_g_source():
    fmz_shim._G("test_pkg_key", _pkg(now=1000, ttl=90000))
    v = SR.receive_signal(1500, "G", g_key="test_pkg_key")
    assert v["availability"] == "OK" and v["tradeable"]
    fmz_shim._G("test_pkg_key", None)


def test_signal_lineage_record_and_last():
    fmz_shim._G(SR._SIG_LINEAGE_KEY, None)
    SR.signal_lineage_record("pkg-1", 1000)
    SR.signal_lineage_record("pkg-2", 2000, note="entered")
    assert SR.signal_lineage_last()["package_id"] == "pkg-2"
    assert len(SR.signal_lineage_load()) == 2
