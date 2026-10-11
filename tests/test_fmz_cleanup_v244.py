import importlib.util
import pathlib
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
SIGNAL_FILE = ROOT / "demo" / "最新交付物" / "neutral_regulation_demo_fmz.py"


def load_signal_module():
    spec = importlib.util.spec_from_file_location(
        "neutral_regulation_demo_fmz_cleanup", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.fmz_log = lambda *parts: None
    module.fmz_status = lambda text: None
    module.fmz_push = lambda text: None
    return module


def test_cleanup_preserves_identity_safety_defaults_and_drops_dead_knobs():
    module = load_signal_module()
    assert module.CONFIG["demo_version"] == "2.0.3"
    assert module.CONFIG["read_only_demo"] is True
    assert module.CONFIG["signal_review_push_enabled"] is False
    for key in ("llm_review_enabled", "status_max_lines", "signal_session_boundary_buffer_min",
                "signal_session_high_confirmation_min", "gex_info_token"):
        assert key not in module.CONFIG
        assert key not in module.USER_CONFIG_KEYS
    assert not hasattr(module, "_session_buffer_transition")


def test_runtime_offline_tick_no_longer_allocates_dead_lkgv_attrs():
    module = load_signal_module()
    with tempfile.TemporaryDirectory() as raw:
        tmpdir = pathlib.Path(raw)
        config = dict(module.CONFIG)
        config.update({
            "startup_log_enabled": False,
            "tick_summary_log_enabled": False,
            "log_status_enabled": False,
            "chart_enabled": False,
            "signal_review_push_test": False,
            "signal_review_push_enabled": False,
            "fixed_analysis_round_enabled": False,
            "live_fetch_enabled": False,
            "offline_fixture_enabled": True,
            "logs_dir": str(tmpdir),
            "space_gate_state_file": str(tmpdir / "space_gate_state.json"),
            "data_export_snapshot_file": str(tmpdir / "snapshots.jsonl"),
        })
        runtime = module.DemoRuntime(config)
        runtime.tick(live_fetch=False)
        for name in (
            "last_good_tmvf_klines",
            "last_good_tmvf_funding_points",
            "last_good_mdie_klines",
        ):
            assert not hasattr(runtime, name)
