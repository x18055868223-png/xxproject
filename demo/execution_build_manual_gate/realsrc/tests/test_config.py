# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import config as C


def _with_config(**overrides):
    old = {k: getattr(C, k) for k in overrides}
    try:
        for k, v in overrides.items():
            setattr(C, k, v)
        return C.validate_config()
    finally:
        for k, v in old.items():
            setattr(C, k, v)


def test_default_config_is_dry_run_ready():
    assert C.validate_config() == []


def test_live_trading_gates_require_dry_run_passed():
    errs = _with_config(DRY_RUN_PASSED=False, ALLOW_EXIT_TRADING=True)

    assert any("DRY_RUN_PASSED" in e for e in errs)


def test_entry_live_requires_exit_path_and_risk_budget():
    errs = _with_config(DRY_RUN_PASSED=True, ALLOW_ENTRY_TRADING=True,
                        ALLOW_EXIT_TRADING=False, RISK_EXIT_MAX_SPEND=0.0)

    assert any("ALLOW_EXIT_TRADING" in e for e in errs)
    assert any("RISK_EXIT_MAX_SPEND" in e for e in errs)


def test_threshold_sanity_checks_fail_closed():
    errs = _with_config(
        RISK_EXIT_MAX_SPEND=-0.1,
        EXIT_RESERVE_RATIO=1.0,
        HEDGE_REDUCTION_RATIO=0.0,
        PORTFOLIO_LIMITS={"max_open_positions": 1, "max_margin": -0.1},
    )

    assert any("RISK_EXIT_MAX_SPEND" in e for e in errs)
    assert any("EXIT_RESERVE_RATIO" in e for e in errs)
    assert any("HEDGE_REDUCTION_RATIO" in e for e in errs)
    assert any("PORTFOLIO_LIMITS" in e for e in errs)
