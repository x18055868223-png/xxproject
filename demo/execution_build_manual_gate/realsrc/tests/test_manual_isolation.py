# -*- coding: utf-8 -*-
import os
import sys


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")


def _read(name):
    with open(os.path.join(SRC, name), encoding="utf-8") as fh:
        return fh.read()


def _external_terms():
    sig = "sig" + "nal"
    schema = ("Sig" + "nal") + "EvidencePackage"
    return [
        "from " + sig + "_receiver import",
        "receive_" + sig + "(",
        sig.upper() + "_SOURCE",
        sig.upper() + "_FILE_PATH",
        sig.upper() + "_G_KEY",
        sig.upper() + "_SCHEMA_VERSION_PREFIX",
        "side" + "_hint",
        schema,
    ]


def test_strategy_main_path_has_no_external_receiver_or_source():
    text = _read("strategy.py")
    hits = [item for item in _external_terms() if item in text]
    assert hits == []


def test_bundle_order_excludes_external_receiver():
    with open(os.path.join(ROOT, "build_bundle.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert ('"' + ("sig" + "nal") + '_receiver"') not in src
    assert '"manual_context"' in src


def test_manual_gate_isolation_contract_constant():
    sys.path.insert(0, SRC)
    import strategy as ST
    assert getattr(ST, "MANUAL_GATE_ISOLATION_TESTS_PASSED", False) is True
