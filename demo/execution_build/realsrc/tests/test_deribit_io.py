# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import deribit_io as IO


def test_build_query_types():
    q = IO._build_query({"a": True, "b": False, "c": {"X": -0.1}, "d": None, "e": 5})
    assert "a=true" in q and "b=false" in q     # bool → true/false
    assert "d=" not in q                        # None 丢弃
    assert "e=5" in q
    assert "c=" in q                            # dict → JSON 编码(URL escape)


def test_result_unwrap_and_error():
    assert IO._result({"result": {"x": 1}}) == {"x": 1}      # 解包 result
    assert IO._result({"error": {"code": 1, "message": "bad"}}, "ctx") is None
    assert IO._result(None) is None
    assert IO._result({"x": 1}) == {"x": 1}                  # 已解包则原样


def test_call_retries_on_none(monkeypatch=None):
    calls = {"n": 0}

    def io(*a):
        calls["n"] += 1
        return None if calls["n"] < 3 else {"result": {"ok": True}}

    IO.exchange.io_handler = io
    out = IO._call("GET", "/public/x", {}, "x", retries=3)
    assert out == {"ok": True} and calls["n"] == 3            # 前两次 None 重试，第三次成功


def test_simulate_portfolio_passes_positions():
    seen = {}

    def io(_tag, method, path, query):
        seen["path"] = path
        seen["query"] = query
        return {"result": {"initial_margin": 0.01}}

    IO.exchange.io_handler = io
    r = IO.dbt_simulate_portfolio("BTC", {"BTC-X": -0.1})
    assert r["initial_margin"] == 0.01
    assert "simulate_portfolio" in seen["path"] and "simulated_positions" in seen["query"]
