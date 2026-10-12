"""FMZ 2.0.5: clean stop, no deprecated UTC helpers, GEX freshness for slow upstream."""
import importlib.util
import pathlib
import warnings

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SIGNAL_FILE = ROOT / "demo" / "最新交付物" / "neutral_regulation_demo_fmz.py"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location("nrd_signal_fmz_205", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StopError(BaseException):
    """Same class name FMZ raises from Sleep() when the robot is stopped."""


def test_version_is_205(mod):
    assert mod.CONFIG["demo_version"] == "2.0.5"


def test_main_exits_cleanly_when_fmz_stops_the_robot(mod, monkeypatch):
    def stopped(*args, **kwargs):
        raise StopError("stop")
    monkeypatch.setattr(mod, "run", stopped)
    assert mod.main() is None  # no exception -> process exit code 0


def test_main_still_raises_real_errors(mod, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr(mod, "run", broken)
    with pytest.raises(RuntimeError):
        mod.main()


def test_tick_loop_does_not_swallow_stop(mod, monkeypatch):
    class Runtime:
        def __init__(self, config):
            self.config = dict(config, max_main_loops=0)
            self.last_tick_error = None

        def tick(self, live_fetch=None):
            raise StopError("stop")

    monkeypatch.setattr(mod, "DemoRuntime", Runtime)
    monkeypatch.setattr(mod, "fmz_sleep", lambda ms: None)
    with pytest.raises(StopError):
        mod.run(max_loops=3)


def test_utc_helpers_emit_no_deprecation_and_keep_output(mod):
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert mod.utc8_text(1_791_704_849_000) == "2026-10-11 15:47:29"
        assert mod.format_option_expiry(1_791_792_000_000) == "BTC-12OCT26"


def test_net_gamma_freshness_tolerates_hourly_upstream(mod):
    assert mod.CONFIG["space_gate_net_gamma_fresh_ms"] >= 7_200_000
