import copy
import hashlib
import json

import pytest

from tools import astra_risk_qualification_v244 as q
from tools import astra_underwriting_v20 as u
from tests.test_astra_underwriting_v20 import sample, T


def profile():
    return {
        "schema": q.SCHEMA, "artifact_status": "verified_research_asset",
        "artifact_sha256": q.MODEL_SHA256, "protocol_sha256": q.PROTOCOL_SHA256,
        "model_id": "catboost__statistical__D4", "model_hash": "a" * 64,
        "feature_group": "statistical", "training_cutoff": "2026-09-01",
        "scope": "research", "tail_probability": "research_only",
        "horizon_min_exclusive_hours": 8, "horizon_max_inclusive_hours": 24,
        "reference_price_basis": "last fully closed Binance spot BTCUSDT 1m close",
        "feature_names": ["dte_hours", "ret_15"],
        "required_feature_names": ["dte_hours", "ret_15"],
    }


@pytest.mark.parametrize("hours,in_range", [(8, False), (8.1, True), (24, True), (24.1, False)])
def test_research_horizon_never_grants_consumption_qualification(hours, in_range):
    payload = sample(risk=False)
    # Keep the named expiry and alter the frozen valuation/quote clocks.
    now = T + int((24 - hours) * 3_600_000)
    payload["market"]["valuation_ts_ms"] = now
    payload["market"]["short_book"]["timestamp"] = now
    payload["market"]["long_book"]["timestamp"] = now
    snapshot = u.evaluate(payload)
    original = copy.deepcopy(snapshot)
    diagnosis = q.diagnose(snapshot, profile())
    assert diagnosis["contract_domain"]["horizon_in_research_range"] is in_range
    assert diagnosis["status"] == "not_qualified"
    assert diagnosis["mu_ref_btc"] is None
    assert diagnosis["production_risk_admitted"] is False
    assert "model_input_row_missing" in diagnosis["gap_reasons"]
    assert "model_price_basis_binding_missing" in diagnosis["gap_reasons"]
    assert snapshot == original
    assert snapshot["economics"]["mu_ref_btc"] is None


def test_hash_identity_and_snapshot_integrity_fail_closed(tmp_path):
    bad = tmp_path / "model.json"
    bad.write_text('{"schema":"astra_joint_v11_model_artifact@1.0.0","qualified":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="hash_not_admitted"):
        q.load_profile(bad, bad)
    snapshot = u.evaluate(sample(risk=False))
    snapshot["economics"]["mu_ref_btc"] = 0.00001
    with pytest.raises(ValueError):
        q.diagnose(snapshot, profile())


def test_no_model_is_explicit_and_does_not_call_inference():
    result = q.diagnose(u.evaluate(sample(risk=False)), None)
    assert result["gap_reasons"] == ["risk_model_not_configured"]
    assert result["inference_calls"] == result["llm_http_calls"] == 0


@pytest.fixture
def recognized_assets(tmp_path, monkeypatch):
    artifact = {"schema": "astra_joint_v11_model_artifact@1.0.0", "selected_feature_group": "statistical",
                "models": {"statistical": {"model_id": "fixture", "model_hash": "a" * 64,
                                            "feature_names": ["ret_15"], "required_feature_names": ["ret_15"]}},
                "training_cutoff": "2026-09-01", "scope": "research",
                "publication_policy": {"tail_probability": "research_only"}}
    protocol = {"ordinary_dte_min_exclusive_hours": 8, "ordinary_dte_max_inclusive_hours": 24,
                "primary_width": 2000, "reference_price": "Binance closed 1m", "natural_training_observation": "30m"}
    paths = []
    for name, value, constant in [("model.json", artifact, "MODEL_SHA256"), ("protocol.json", protocol, "PROTOCOL_SHA256")]:
        path = tmp_path / name
        raw = json.dumps(value).encode()
        path.write_bytes(raw)
        monkeypatch.setattr(q, constant, hashlib.sha256(raw).hexdigest())
        paths.append(path)
    return paths


def test_loading_recognized_bytes_preserves_research_boundary(recognized_assets):
    paths = recognized_assets
    loaded = q.load_profile(*paths)
    assert loaded["production_risk_admitted"] is False
    assert loaded["tail_probability"] == "research_only"
    paths[0].write_bytes(paths[0].read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash_not_admitted"):
        q.load_profile(*paths)


@pytest.mark.parametrize("asset_mode", ["recognized", "changed", "missing_pair", "absent"])
def test_standard_workbench_config_diagnoses_without_risk_provider(
    tmp_path, monkeypatch, recognized_assets, asset_mode
):
    from tools import astra_workbench_v20 as wb
    from tests import test_astra_workbench_v20 as fx
    snapshot_path = tmp_path / "snapshots.jsonl"
    fx.write_fmz(snapshot_path, fx.native_gate())
    monkeypatch.setenv("ASTRA_WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ASTRA_WORKBENCH_SNAPSHOTS_JSONL", str(snapshot_path))
    monkeypatch.delenv("ASTRA_RISK_MODEL_PATH", raising=False)
    monkeypatch.delenv("ASTRA_RISK_PROTOCOL_PATH", raising=False)
    if asset_mode != "absent":
        monkeypatch.setenv("ASTRA_RISK_MODEL_PATH", str(recognized_assets[0]))
    if asset_mode not in {"absent", "missing_pair"}:
        monkeypatch.setenv("ASTRA_RISK_PROTOCOL_PATH", str(recognized_assets[1]))
    if asset_mode == "changed":
        recognized_assets[0].write_bytes(recognized_assets[0].read_bytes() + b" ")
    instance = wb.AstraWorkbench(
        wb.config_from_env(), discovery_capture=fx.fake_discovery,
        quote_capture=fx.fake_quote_capture, clock_ms=lambda: fx.T,
        llm_transport=lambda *args, **kwargs: pytest.fail("unexpected LLM call"),
    )
    assert instance.risk_provider is None
    view = instance.refresh(now_ms=fx.T)
    for item in view["candidates"]:
        assert item["snapshot"]["economics"]["mu_ref_btc"] is None
        assert item["review_gate"]["enabled"] is False
        diagnosis = item["risk_consumption_diagnosis"]
        assert diagnosis["snapshot_id"] == item["snapshot"]["snapshot_id"]
        assert diagnosis["inference_calls"] == 0
        if asset_mode == "recognized":
            assert "model_input_row_missing" in diagnosis["gap_reasons"]
        elif asset_mode == "absent":
            assert diagnosis["gap_reasons"] == ["risk_model_not_configured"]
        else:
            assert diagnosis["gap_reasons"] == ["risk_model_asset_unusable"]
