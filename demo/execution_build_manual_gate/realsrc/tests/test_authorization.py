# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import authorization as A


def test_auth_code_deterministic():
    assert A.auth_code("pos-1", A.POLICY_TAKE_PROFIT) == A.auth_code("pos-1", A.POLICY_TAKE_PROFIT)
    assert A.auth_code("pos-1", A.POLICY_TAKE_PROFIT) != A.auth_code("pos-2", A.POLICY_TAKE_PROFIT)
    assert len(A.auth_code("pos-1", A.POLICY_TAKE_PROFIT)) == 4


def test_build_and_is_authorized():
    auth = A.build_authorization("pos-1", A.POLICY_TAKE_PROFIT, 1000)
    assert auth["authorization_state"] == A.ST_AUTHORIZED and auth["auth_code"]
    assert A.is_authorized(auth, "pos-1", 1001)
    assert not A.is_authorized(auth, "pos-2", 1001)        # 持仓身份变化 → 失效


def test_revoke_and_consume():
    auth = A.build_authorization("pos-1", A.POLICY_TAKE_PROFIT, 1000)
    r = A.revoke(auth, 1100)
    assert r["authorization_state"] == A.ST_REVOKED and not A.is_authorized(r, "pos-1", 1101)
    c = A.consume(auth, 1200)
    assert c["authorization_state"] == A.ST_CONSUMED and not A.is_authorized(c, "pos-1", 1201)


def test_valid_until_expiry():
    auth = A.build_authorization("pos-1", A.POLICY_RISK_EXIT, 1000, valid_until=2000)
    assert A.is_authorized(auth, "pos-1", 1500)
    assert not A.is_authorized(auth, "pos-1", 2000)        # 到期失效


def test_authorize_from_code():
    code = A.auth_code("pos-1", A.POLICY_TAKE_PROFIT)
    ok = A.authorize_from_code(code, "pos-1", A.POLICY_TAKE_PROFIT, 1000)
    assert ok and ok["authorization_state"] == A.ST_AUTHORIZED
    assert A.authorize_from_code("WRONG", "pos-1", A.POLICY_TAKE_PROFIT, 1000) is None
    assert A.authorize_from_code(code, None, A.POLICY_TAKE_PROFIT, 1000) is None


def test_risk_exit_full_semantics():
    auth = A.authorize_from_code(
        A.auth_code("pos-1", A.POLICY_RISK_EXIT), "pos-1", A.POLICY_RISK_EXIT, 1000,
        max_exit_spend=0.01, allowed_order_types=["post_only"], valid_until=5000)
    assert auth["max_exit_spend"] == 0.01 and auth["valid_until"] == 5000
    assert auth["policy_code"] == A.POLICY_RISK_EXIT
