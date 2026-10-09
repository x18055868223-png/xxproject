import json
from pathlib import Path
import sys
import hashlib

import pytest

from tools import astra_candidate_features_v1 as features
from tools import astra_underwriting_v20 as underwriting
from tests.test_astra_underwriting_v20 import sample


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "wpem_rc"
REAL_MODEL = FIXTURE_ROOT / "astra-joint-v11-20260915" / "portable_qualified" / "model.json"
REAL_PROTOCOL = FIXTURE_ROOT / "astra-joint-v11-20260915" / "protocol_v11.json"




def _compact_source_definitions_for_feature(result, feature_name):
    by_name = {item["name"]: item for item in result["features"]}
    item = by_name[feature_name]
    refs = list(item.get("source_refs") or [])
    definition = item.get("definition") or {}
    refs.extend(definition.get("source_definitions") or [])
    resolved = []
    for ref in refs:
        assert "source_definition" not in ref
        ref_id = ref.get("definition_source_ref")
        assert ref_id in result["definition_sources"]
        resolved.append(result["definition_sources"][ref_id]["source_definition"])
    return resolved


def _assert_all_compact_refs_resolve(result):
    for item in result["features"]:
        refs = list(item.get("source_refs") or [])
        definition = item.get("definition") or {}
        refs.extend(definition.get("source_definitions") or [])
        for ref in refs:
            assert "source_definition" not in ref
            ref_id = ref.get("definition_source_ref")
            assert ref_id in result["definition_sources"]
            assert result["definition_sources"][ref_id]["source_definition"]

def require_real_assets() -> tuple[Path, Path]:
    if not REAL_MODEL.exists() or not REAL_PROTOCOL.exists():
        pytest.skip("sealed local Astra v1.1 assets are not present")
    return REAL_MODEL, REAL_PROTOCOL


def test_export_feature_catalog_uses_real_admitted_asset_and_source_definitions():
    model, protocol = require_real_assets()
    catalog = features.export_feature_catalog(model, protocol)
    artifact = json.loads(model.read_text(encoding="utf-8"))
    selected = artifact["models"][artifact["selected_feature_group"]]

    assert catalog["asset"]["sha256"] == "71fb23ff9cbb55a789cc1c95862e7e242d8647ce8ac883047d10bded01fd5860"
    assert catalog["protocol"]["sha256"] == "5f007cb512763a17bf475da38bee642c171ab7502e816c74a40091cde7ec828e"
    assert catalog["model"]["model_id"] == "catboost__statistical__D4"
    assert [item["name"] for item in catalog["features"]] == selected["feature_names"]
    assert [item["name"] for item in catalog["features"] if item["required"]] == selected["required_feature_names"]

    ret_15 = next(item for item in catalog["features"] if item["name"] == "ret_15")
    assert ret_15["window"]["minutes"] == 15
    assert ret_15["window"]["market"] == "Binance UM BTCUSDT 1m closed bars"
    assert ret_15["training_support"]["row_count"] > 0
    assert any(ref.get("symbol") == "HistoricalBars.feature_snapshot" for ref in ret_15["source_refs"])
    assert any("def feature_snapshot" in (ref.get("source_definition") or "") for ref in ret_15["source_refs"])
    assert "tools/astra_joint_data.py" in catalog["source_hashes"]
    assert catalog["source_hashes"]["tools/astra_joint_data.py"] == hashlib.sha256(
        (Path(__file__).resolve().parents[1] / "tools" / "astra_joint_data.py").read_bytes()
    ).hexdigest()
    assert catalog["catboost_missing_policy"] == {
        "split_count": 2286,
        "missing_goes_right": {"false": 2286},
    }
    assert catalog["catalog_hash"] == features._stable_hash(
        {key: value for key, value in catalog.items() if key != "catalog_hash"}
    )


def test_build_feature_input_keeps_unbound_price_and_um_required_features_null():
    model, protocol = require_real_assets()
    catalog = features.export_feature_catalog(model, protocol)
    snapshot = underwriting.evaluate(sample(risk=False))

    result = features.build_feature_input(snapshot, catalog)

    assert result["status"] == "not_qualified"
    assert result["row_qualified"] is False
    assert result["inference_calls"] == 0
    assert result["llm_http_calls"] == 0
    assert result["mu_ref_btc"] is None
    assert result["row"]["dte_hours"] == pytest.approx(24.0)
    assert result["row"]["side_sign"] == -1.0
    assert result["row"]["hour_sin"] == pytest.approx(0.8660254037844387)
    assert result["row"]["hour_cos"] == pytest.approx(-0.5)
    assert result["row"]["short_distance_fraction"] is None
    assert result["row"]["width_fraction"] is None
    assert result["row"]["ret_15"] is None
    assert result["row"]["net_flow_30"] is None
    assert result["asset_sha256"] == catalog["asset"]["sha256"]
    assert result["protocol_sha256"] == catalog["protocol"]["sha256"]
    assert result["source_hashes"] == catalog["source_hashes"]
    assert result["price_identity"] == catalog["price_identity"]
    assert result["feature_group_protocol"] == catalog["feature_group_protocol"]
    assert set(result["required_missing_features"]) == {
        "short_distance_fraction",
        "width_fraction",
        "ret_15",
        "ret_30",
        "vol_15",
        "vol_30",
        "net_flow_15",
        "net_flow_30",
    }
    assert "model_price_basis_binding_missing" in result["gap_reasons"]
    assert "binance_um_closed_1m_window_missing" in result["gap_reasons"]
    assert "required_features_missing" in result["gap_reasons"]

    by_name = {item["name"]: item for item in result["features"]}
    assert by_name["short_distance_fraction"]["category"] == "semantic_mismatch"
    assert by_name["ret_15"]["category"] == "source_not_connected"
    assert by_name["dte_hours"]["category"] == "available_from_snapshot"
    assert by_name["hour_sin"]["category"] == "available_from_snapshot"
    assert by_name["dte_hours"]["decision_asof_ms"] == snapshot["market"]["valuation_ts_ms"]
    assert by_name["dte_hours"]["source_observation_ms"] == snapshot["market"]["valuation_ts_ms"]
    assert by_name["dte_hours"]["first_available_ms"] is None
    assert by_name["dte_hours"]["source_snapshot_id"] == snapshot["snapshot_id"]
    assert by_name["dte_hours"]["source_contract"]["candidate_id"] == snapshot["candidate_id"]
    assert by_name["ret_15"]["source_observation_ms"] is None
    assert by_name["ret_15"]["retrieved_ms"] is None
    _assert_all_compact_refs_resolve(result)
    hour_sin_sources = _compact_source_definitions_for_feature(result, "hour_sin")
    assert any("def _hour_sin" in source for source in hour_sin_sources)
    raw_result = json.dumps(result, ensure_ascii=False, sort_keys=True)
    assert raw_result.count("def feature_snapshot") == 1
    assert result["input_row_hash_scope"] == "research_input_identity_only; does_not_grant_row_or_risk_qualification"
    expected_identity = {
        "row": result["row"],
        "snapshot_id": snapshot["snapshot_id"],
        "candidate_id": snapshot["candidate_id"],
        "decision_asof_ms": snapshot["market"]["valuation_ts_ms"],
        "catalog_hash": catalog["catalog_hash"],
        "asset_sha256": catalog["asset"]["sha256"],
        "protocol_sha256": catalog["protocol"]["sha256"],
        "source_hashes": catalog["source_hashes"],
    }
    assert result["input_row_hash"] == features._stable_hash(expected_identity)


def test_tampered_real_asset_bytes_are_rejected(tmp_path):
    model, protocol = require_real_assets()
    bad_model = tmp_path / "model.json"
    good_protocol = tmp_path / "protocol_v11.json"
    bad_model.write_bytes(model.read_bytes() + b" ")
    good_protocol.write_bytes(protocol.read_bytes())

    with pytest.raises(ValueError, match="risk_asset_or_protocol_hash_not_admitted"):
        features.export_feature_catalog(bad_model, good_protocol)


def test_asset_change_after_exact_hash_admission_is_rejected(tmp_path, monkeypatch):
    model, protocol = require_real_assets()
    model_copy = tmp_path / "model.json"
    protocol_copy = tmp_path / "protocol_v11.json"
    model_copy.write_bytes(model.read_bytes())
    protocol_copy.write_bytes(protocol.read_bytes())
    model_sha = hashlib.sha256(model_copy.read_bytes()).hexdigest()
    protocol_sha = hashlib.sha256(protocol_copy.read_bytes()).hexdigest()
    real_load_profile = features.risk_qualification.load_profile

    def changing_load_profile(model_path, protocol_path):
        profile = real_load_profile(model_path, protocol_path)
        assert profile["artifact_sha256"] == model_sha
        assert profile["protocol_sha256"] == protocol_sha
        model_copy.write_bytes(model_copy.read_bytes() + b" ")
        return profile

    monkeypatch.setattr(features.risk_qualification, "load_profile", changing_load_profile)

    with pytest.raises(ValueError, match="risk_asset_changed_after_admission"):
        features.export_feature_catalog(model_copy, protocol_copy)


def test_catalog_build_does_not_load_v11_inference_module():
    model, protocol = require_real_assets()
    before = {name for name in sys.modules if name.endswith("astra_joint_v11_inference")}
    catalog = features.export_feature_catalog(model, protocol)
    snapshot = underwriting.evaluate(sample(risk=False))
    result = features.build_feature_input(snapshot, catalog)
    after = {name for name in sys.modules if name.endswith("astra_joint_v11_inference")}

    assert after == before
    assert result["inference_calls"] == 0
    assert result["production_risk_admitted"] is False
