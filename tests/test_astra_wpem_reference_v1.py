import copy
import json
from pathlib import Path
import sys

import pytest

from tools import astra_candidate_features_v1 as feature_catalog
from tools import astra_wpem_reference_v1 as reference
from tools import astra_underwriting_v20 as core


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "wpem_rc"
MODEL = FIXTURE_ROOT / "astra-joint-v11-20260915" / "portable_qualified" / "model.json"
PROTOCOL = FIXTURE_ROOT / "astra-joint-v11-20260915" / "protocol_v11.json"
PACKET_DIR = FIXTURE_ROOT / "astra-wpem-first-round-20261004"
PUT_PACKET = PACKET_DIR / "historical_real_put_packet.json"
CALL_PACKET = PACKET_DIR / "historical_real_call_packet.json"


def require_assets():
    if not MODEL.exists() or not PROTOCOL.exists() or not PUT_PACKET.exists():
        pytest.skip("sealed Astra v1.1 assets or first-round WPEM packets are not present")
    catalog = feature_catalog.export_feature_catalog(MODEL, PROTOCOL)
    packet = packet_for_catalog(PUT_PACKET, catalog)
    return packet, catalog


def packet_for_catalog(path, catalog):
    # The frozen first-round packet is historical.  Only its path-derived
    # model-input identity is rebound in memory to this tests/fixtures catalog
    # location so clean exports can replay without the original historical artifact tree.
    packet = json.loads(path.read_text(encoding="utf-8-sig"))
    packet["model_input"] = feature_catalog.build_feature_input(packet["snapshot"], catalog)
    packet.setdefault("versions", {})["feature_catalog_hash"] = catalog["catalog_hash"]
    packet["packet_hash"] = core._hash({k: v for k, v in packet.items() if k != "packet_hash"})
    return packet


def rehash_model_input(packet):
    mi = packet["model_input"]
    identity = {
        "row": mi["row"],
        "snapshot_id": packet["snapshot"]["snapshot_id"],
        "candidate_id": packet["snapshot"]["candidate_id"],
        "decision_asof_ms": packet["cutoff"]["decision_asof_ms"],
        "catalog_hash": mi["catalog_hash"],
        "asset_sha256": mi["asset_sha256"],
        "protocol_sha256": mi["protocol_sha256"],
        "source_hashes": mi["source_hashes"],
    }
    mi["input_row_hash"] = core._hash(identity)
    packet["packet_hash"] = core._hash({k: v for k, v in packet.items() if k != "packet_hash"})


def rehash_catalog(catalog):
    catalog["catalog_hash"] = core._hash({k: v for k, v in catalog.items() if k != "catalog_hash"})


def set_feature(packet, name, value, *, category=None, gaps=None, clock_delta=None):
    mi = packet["model_input"]
    mi["row"][name] = value
    for item in mi["features"]:
        if item["name"] == name:
            item["value"] = value
            if category is not None:
                item["category"] = category
            if gaps is not None:
                item["gaps"] = gaps
            if clock_delta is not None:
                item["source_observation_ms"] = packet["cutoff"]["decision_asof_ms"] + clock_delta
            break
    else:
        raise AssertionError(name + " missing")
    rehash_model_input(packet)


def test_real_first_round_packet_is_terminal_not_qualified_and_exports_actual_sources():
    packet, catalog = require_assets()

    result = reference.analyze_reference(packet, catalog)
    q = result["qualification_report"]
    manifest = result["feature_binding_manifest"]
    target = result["target_price_basis_binding"]

    assert result["terminal_status"] == "NOT_QUALIFIED_FINAL_FOR_THIS_ROUND"
    assert q["status"] == "NOT_QUALIFIED_FINAL_FOR_THIS_ROUND"
    assert q["earliest_breakpoint"] == "REQUIRED_FEATURES_ASOF_NOT_QUALIFIED"
    assert q["required_missing_count"] == 8
    assert q["required_coverage"] == {"available": 2, "total": 10, "missing": 8}
    assert set(q["required_missing_features"]) == {
        "short_distance_fraction", "width_fraction", "ret_15", "ret_30",
        "vol_15", "vol_30", "net_flow_15", "net_flow_30",
    }
    assert q["mu_ref_btc"] is None and q["M_ref_btc"] is None and q["m_ref_btc"] is None
    assert q["inference_calls"] == q["llm_http_calls"] == 0
    assert manifest["required_feature_accepted_count"] == 2
    by_name = {item["name"]: item for item in manifest["required_features"]}
    assert by_name["short_distance_fraction"]["classification"] == ["PRICE_BASIS_MISMATCH", "SOURCE_MISSING"]
    assert by_name["ret_15"]["classification"] == ["HISTORY_INSUFFICIENT", "SOURCE_MISSING"]
    assert by_name["dte_hours"]["classification"] == ["DERIVABLE_EXACT"]
    assert target["status"] == "TARGET_BINDING_UNPROVEN"
    assert target["actual_target_symbols"] == ["_candidate_row", "target_loss", "fast_predictions", "width_value_btc", "payout"]
    assert target["quantity_scale"]["training_payout_quantity"].startswith("q=1")
    assert target["loss_over_B"]["clipped_to_one"] is False
    assert "diagnostic_loss_above_one_rows" not in target["loss_over_B"]
    assert "diagnostic_maximum_realized_loss" not in target["loss_over_B"]
    assert target["tail_publication_limit"]["evidence"]["inconsistent_rows"] == 2264
    assert target["tail_publication_limit"]["evidence"]["rows"] == 93242
    source = "\n".join(ref["source_definition"] for ref in target["source_refs"])
    assert "loss_normalized = payout_btc / (actual_width / float(price))" in source
    assert "return actual_width / entry_price" in source
    assert "return short_payout - long_payout" in source
    assert all(ref["bound_asset_sha256"] == catalog["asset"]["sha256"] for ref in target["source_refs"])
    assert "acceptance_matrix" not in q


def test_right_packet_with_bad_rehashed_catalog_asset_is_rejected():
    packet, catalog = require_assets()
    bad = copy.deepcopy(catalog)
    bad["asset"]["sha256"] = "0" * 64
    bad["catalog_hash"] = core._hash({k: v for k, v in bad.items() if k != "catalog_hash"})

    with pytest.raises(ValueError, match="identity_not_admitted|asset_hash_mismatch"):
        reference.analyze_reference(packet, bad)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda catalog: catalog["features"][0].__setitem__("required", not catalog["features"][0]["required"]),
        lambda catalog: catalog["features"][0].__setitem__("definition", {"kind": "forged"}),
        lambda catalog: catalog["features"][0].__setitem__("training_support", {"row_count": 1}),
    ],
)
def test_rehashed_catalog_required_definition_or_support_tampering_is_rejected(mutator):
    packet, catalog = require_assets()
    bad = copy.deepcopy(catalog)
    mutator(bad)
    rehash_catalog(bad)

    with pytest.raises(ValueError, match="catalog_not_actual_export"):
        reference.analyze_reference(packet, bad)


@pytest.mark.parametrize("name,value", [("ret_15", 0.0), ("net_flow_30", 0.0)])
def test_null_required_market_feature_cannot_be_rehashed_to_zero(name, value):
    packet, catalog = require_assets()
    tampered = copy.deepcopy(packet)
    set_feature(tampered, name, value)

    with pytest.raises(ValueError, match="model_input_not_actual_rebuild|feature_substitution_rejected"):
        reference.analyze_reference(tampered, catalog)


@pytest.mark.parametrize("name,value", [("short_distance_fraction", 0.01), ("width_fraction", 0.02)])
def test_in_domain_looking_geometry_without_binance_spot_basis_is_rejected(name, value):
    packet, catalog = require_assets()
    tampered = copy.deepcopy(packet)
    set_feature(tampered, name, value)

    with pytest.raises(ValueError, match="model_input_not_actual_rebuild|feature_substitution_rejected"):
        reference.analyze_reference(tampered, catalog)


def test_rehashed_packet_cannot_change_dte_category():
    packet, catalog = require_assets()
    tampered = copy.deepcopy(packet)
    for item in tampered["model_input"]["features"]:
        if item["name"] == "dte_hours":
            item["category"] = "forged_available"
            break
    rehash_model_input(tampered)

    with pytest.raises(ValueError, match="model_input_not_actual_rebuild"):
        reference.analyze_reference(tampered, catalog)


def test_rehashed_packet_cannot_clear_required_missing_list():
    packet, catalog = require_assets()
    tampered = copy.deepcopy(packet)
    tampered["model_input"]["required_missing_features"] = []
    rehash_model_input(tampered)

    with pytest.raises(ValueError, match="model_input_not_actual_rebuild"):
        reference.analyze_reference(tampered, catalog)


def test_future_feature_clock_is_rejected_even_when_packet_rehashed():
    packet, catalog = require_assets()
    tampered = copy.deepcopy(packet)
    set_feature(tampered, "dte_hours", tampered["model_input"]["row"]["dte_hours"], clock_delta=1)

    with pytest.raises(ValueError, match="model_input_not_actual_rebuild|future_feature_clock"):
        reference.analyze_reference(tampered, catalog)


def test_target_binding_unproven_keeps_mu_and_m_null_for_both_sides():
    if not CALL_PACKET.exists():
        pytest.skip("call first-round packet missing")
    _, catalog = require_assets()
    call_packet = packet_for_catalog(CALL_PACKET, catalog)

    result = reference.analyze_reference(call_packet, catalog)

    assert result["qualification_report"]["mu_ref_btc"] is None
    assert result["qualification_report"]["M_ref_btc"] is None
    assert result["qualification_report"]["m_ref_btc"] is None
    assert result["target_price_basis_binding"]["status"] == "TARGET_BINDING_UNPROVEN"
    assert result["target_price_basis_binding"]["candidate_price_basis"] == "deribit_index"
    assert result["qualification_report"]["shadow_inference_allowed"] is False


def test_analysis_does_not_import_training_or_inference_modules():
    packet, catalog = require_assets()
    watched = {
        name for name in sys.modules
        if name.endswith("astra_joint_v11_inference")
        or name.endswith("astra_joint_v11_model")
        or name == "catboost"
    }

    result = reference.analyze_reference(packet, catalog)

    after = {
        name for name in sys.modules
        if name.endswith("astra_joint_v11_inference")
        or name.endswith("astra_joint_v11_model")
        or name == "catboost"
    }
    assert after == watched
    assert result["inference_calls"] == 0
