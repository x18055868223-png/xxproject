import json
import hashlib

from tools import astra_map_projection_v24 as projection
from tools.astra_data_engine_v23 import DataEngine


T = 1_790_323_200_000


def registry(path):
    body = {
        "schema": "astra_data_product_registry@2.3.0",
        "products": [
            {
                "product_id": projection.KPF_PRODUCT,
                "provider_id": "kpf_offline",
                "collector_id": "server_kpf_adapter_pending",
                "primary_collector": "server",
                "connection_status": "REGISTERED_ADAPTER",
                "definition_version": "btc.kpf_map_readonly@1.0.0",
                "market": "BTC",
                "asset": "BTC",
                "currency": "USD",
                "aggregation": "accepted_zone_with_input_cutoff",
                "unit": "USD",
                "source_ref": "local_same_version_kpf_artifacts",
                "usage_policies": {
                    "default": {"ttl_ms": 604800000, "allowed_data_states": ["OK"]},
                    "background": {"ttl_ms": 604800000, "allowed_data_states": ["OK"]},
                    "health": {"ttl_ms": 604800000, "allowed_data_states": ["OK", "PARTIAL", "INVALID"]},
                },
            }
        ],
    }
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return path


def engine(tmp_path):
    return DataEngine(tmp_path / "state", registry_path=registry(tmp_path / "registry.json"))


def zone(idx, *, low=None, high=None, market="Deribit BTC index", price_basis="DERIBIT_BTC_USD_INDEX", can_use=True):
    low = 99_000 + idx * 100 if low is None else low
    high = low + 40 if high is None else high
    return {
        "reference_id": f"kpf-{idx}",
        "raw_basin_low": low,
        "raw_basin_high": high,
        "market": market,
        "quote_currency": "USD",
        "price_basis": price_basis,
        "observation_end_ms": T - 10_000,
        "known_at_ms": T - 5_000,
        "active_from_ms": T - 10_000,
        "usage_decision": {"can_use": can_use, "status": "USABLE" if can_use else "DISABLED"},
        "source_record_ids": [f"kpf-source-{idx}"],
    }


def write_bundle(tmp_path, *, version="kpf-v1", zones=None, audit_version=None, debug_version=None):
    zones = list(zones or [zone(0)])
    paths = {
        "kpf_snapshot_path": tmp_path / "kpf_snapshot.json",
        "kpf_audit_path": tmp_path / "kpf_audit.json",
        "kpf_debug_path": tmp_path / "kpf_debug.json",
    }
    paths["kpf_snapshot_path"].write_text(json.dumps({"version": version}, ensure_ascii=False), encoding="utf-8")
    paths["kpf_audit_path"].write_text(json.dumps({"version": audit_version or version}, ensure_ascii=False), encoding="utf-8")
    paths["kpf_debug_path"].write_text(
        json.dumps({"version": debug_version or version, "zones": zones}, ensure_ascii=False),
        encoding="utf-8",
    )
    return {key: str(value) for key, value in paths.items()}


def kpf_refs(e, now=T, current=99_120):
    refs, gaps = projection.build_references(
        e,
        now,
        fmz_fact={"current_price_usd": current, "current_price_source": "deribit_index", "price_observed_ms": now},
        source_records=[],
        config={},
    )
    return [ref for ref in refs if ref.get("role") == "KPF_BASIN"], gaps


def write_manifest(tmp_path, cfg, version="kpf-v1"):
    entries = {}
    for name, key in (("snapshot", "kpf_snapshot_path"), ("audit", "kpf_audit_path"), ("zones", "kpf_debug_path")):
        path = __import__("pathlib").Path(cfg[key])
        entries[name] = {"path": path.relative_to(tmp_path).as_posix(),
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest = tmp_path / "current.json"
    manifest.write_text(json.dumps({"schema": "astra_kpf_bundle_manifest@1", "version_id": version,
                                    "artifacts": entries}), encoding="utf-8")
    return {"kpf_manifest_path": str(manifest)}


def test_atomic_native_manifest_retains_hash_identity_and_nominal_basis(tmp_path):
    e = engine(tmp_path)
    cfg = write_bundle(tmp_path, zones=[zone(0, market="BINANCE_USD_M_FUTURES", price_basis="BINANCE_USDM_BTCUSDT_AGGTRADES")])
    captured = projection.collect_kpf_artifacts(e, T, write_manifest(tmp_path, cfg))
    refs, _ = kpf_refs(e)
    assert not captured["gaps"]
    assert "manifest" in captured["artifact_hashes"]
    assert len(refs) == 1 and refs[0]["coordinate_qualification"] == "NOMINAL_ONLY"


def test_native_manifest_changed_artifact_blocks_previous_good_record(tmp_path):
    e = engine(tmp_path)
    cfg = write_bundle(tmp_path)
    manifest = write_manifest(tmp_path, cfg)
    assert not projection.collect_kpf_artifacts(e, T, manifest)["gaps"]
    with open(cfg["kpf_debug_path"], "a", encoding="utf-8") as handle:
        handle.write(" ")
    rejected = projection.collect_kpf_artifacts(e, T + 100, manifest)
    assert rejected["gaps"] == ["kpf_manifest_hash_mismatch"]
    assert rejected["record"]["content"]["values"]["valid"] is False
    assert kpf_refs(e, T + 100)[0] == []


def test_native_manifest_rejects_traversal_and_mixed_version(tmp_path):
    e = engine(tmp_path)
    cfg = write_bundle(tmp_path)
    manifest = write_manifest(tmp_path, cfg, version="other-version")
    assert "kpf_same_version_artifacts_mismatch" in projection.collect_kpf_artifacts(e, T, manifest)["gaps"]
    path = __import__("pathlib").Path(manifest["kpf_manifest_path"])
    body = json.loads(path.read_text(encoding="utf-8"))
    body["artifacts"]["snapshot"]["path"] = "../outside.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    assert projection.collect_kpf_artifacts(e, T + 1, manifest)["gaps"] == ["kpf_manifest_artifact_invalid"]


def test_collect_kpf_preserves_full_pool_but_default_references_are_limited(tmp_path):
    e = engine(tmp_path)
    cfg = write_bundle(tmp_path, zones=[zone(i) for i in range(7)])

    result = projection.collect_kpf_artifacts(e, T, cfg)
    refs, gaps = kpf_refs(e)

    assert result["gaps"] == []
    assert len(refs) == 7
    mapping = projection.build_projection(
        e, now_ms=T, fmz_fact={"current_price_usd": 99120}, source_records=[], config=cfg,
    )["btc_map"]
    assert len([r for r in mapping["reference_pool"] if r["role"] == "KPF_BASIN"]) == 7
    assert len(mapping["display_reference_ids"]) == 2
    frozen = refs[0]["source_record"]
    assert frozen["identity"]["product_id"] == projection.KPF_PRODUCT
    assert frozen["content"]["values"]["zone_count"] == 7
    assert len(frozen["content"]["values"]["zones"]) == 7
    assert all(ref["coordinate_qualification"] == "COMPARABLE" for ref in refs)
    assert "_synthetic" not in json.dumps(refs, ensure_ascii=False)


def test_kpf_same_version_mismatch_is_rejected_without_partial_refs(tmp_path):
    e = engine(tmp_path)
    cfg = write_bundle(tmp_path, version="kpf-v1", audit_version="kpf-v2")

    result = projection.collect_kpf_artifacts(e, T, cfg)
    refs, gaps = kpf_refs(e)

    assert "kpf_same_version_artifacts_mismatch" in result["gaps"]
    assert refs == []
    assert "kpf_cached_record_unavailable" in gaps


def test_rejected_kpf_artifact_identity_is_still_frozen(tmp_path):
    e = engine(tmp_path)
    cfg = write_bundle(tmp_path, version="kpf-v1", audit_version="kpf-v2")
    captured = projection.collect_kpf_artifacts(e, T, cfg)
    mapping = projection.build_projection(e, now_ms=T, source_records=[], config=cfg)["btc_map"]
    assert not [ref for ref in mapping["reference_pool"] if ref["family"] == "TRADED_ACCEPTANCE"]
    frozen = next(r for r in mapping["source_manifest"]["source_records"]
                  if r["identity"]["product_id"] == projection.KPF_PRODUCT)
    assert frozen["content"]["values"]["artifact_hashes"] == captured["artifact_hashes"]
    assert frozen["content"]["values"]["valid"] is False
    assert frozen["usage_qualification"]["can_use"] is False


def test_kpf_unknown_price_basis_stays_nominal_not_precise(tmp_path):
    e = engine(tmp_path)
    cfg = write_bundle(tmp_path, zones=[zone(0, market="KPF offline", price_basis="USD")])

    projection.collect_kpf_artifacts(e, T, cfg)
    refs, _gaps = kpf_refs(e)

    assert len(refs) == 1
    assert refs[0]["coordinate_qualification"] == "NOMINAL_ONLY"
    assert refs[0]["usage_decision"]["can_use"] is True


def test_kpf_explicit_usage_false_is_unavailable(tmp_path):
    e = engine(tmp_path)
    cfg = write_bundle(tmp_path, zones=[zone(0, can_use=False)])

    projection.collect_kpf_artifacts(e, T, cfg)
    refs, gaps = kpf_refs(e)

    assert refs == []
    assert "kpf_raw_basin_geometry_missing" in gaps


def test_kpf_late_first_seen_cannot_be_used_for_earlier_candidate(tmp_path):
    e = engine(tmp_path)
    cfg = write_bundle(tmp_path)

    projection.collect_kpf_artifacts(e, T + 60_000, cfg)
    refs, gaps = kpf_refs(e, now=T)

    assert refs == []
    assert "kpf_cached_record_unavailable" in gaps


def test_kpf_repeated_same_payload_does_not_extend_first_seen(tmp_path):
    e = engine(tmp_path)
    cfg = write_bundle(tmp_path)

    first = projection.collect_kpf_artifacts(e, T, cfg)["record"]
    second = projection.collect_kpf_artifacts(e, T + 60_000, cfg)["record"]

    assert first["time"]["first_seen_at_ms"] == T
    assert second["time"]["first_seen_at_ms"] == T
    assert second["collector"]["last_checked_at_ms"] == T + 60_000


def test_kpf_pool_over_limit_rejects_all_refs_but_keeps_hash_count(tmp_path):
    e = engine(tmp_path)
    cfg = write_bundle(tmp_path, zones=[zone(i) for i in range(projection.KPF_MAX_POOL + 1)])

    result = projection.collect_kpf_artifacts(e, T, cfg)
    current = e.read(projection.KPF_PRODUCT, now_ms=T, usage="health")
    refs, gaps = kpf_refs(e)

    assert "kpf_finite_pool_too_large" in result["gaps"]
    assert current["content"]["values"]["raw_zone_count"] == projection.KPF_MAX_POOL + 1
    assert set(current["content"]["values"]["artifact_hashes"]) == {"kpf_snapshot_path", "kpf_audit_path", "kpf_debug_path"}
    assert refs == []
    assert "kpf_cached_record_unavailable" in gaps


def test_kpf_build_references_reads_cache_without_file_io_or_state_mutation(tmp_path):
    e = engine(tmp_path)
    cfg = write_bundle(tmp_path)
    projection.collect_kpf_artifacts(e, T, cfg)
    state_path = tmp_path / "state" / "data_engine_state_v23.json"
    before = state_path.read_text(encoding="utf-8")
    for path in cfg.values():
        # If build_references tries to read files from config, this would now fail.
        __import__("pathlib").Path(path).unlink()

    refs, _gaps = projection.build_references(
        e,
        T,
        fmz_fact={"current_price_usd": 99_020, "current_price_source": "deribit_index", "price_observed_ms": T},
        source_records=[],
        config={"kpf_snapshot_path": "missing", "kpf_audit_path": "missing", "kpf_debug_path": "missing"},
    )
    after = state_path.read_text(encoding="utf-8")

    assert [ref for ref in refs if ref.get("role") == "KPF_BASIN"]
    assert before == after


def test_snapshot_observation_requires_exchange_source_clock():
    refs = [{
        "reference_id": "contract-near-be",
        "revision_id": "r1",
        "coordinate_qualification": "COMPARABLE",
        "market": "Deribit BTC index",
        "price_basis": "DERIBIT_BTC_USD_INDEX",
    }]
    snapshot = {"market": {"valuation_ts_ms": T, "reference_price_usd": 100000, "reference_source": "deribit_index"}}

    assert projection._snapshot_observations(snapshot, refs) == []

    snapshot["market"]["quote"] = {"exchange_times_ms": {"short": T - 123}}
    observed = projection._snapshot_observations(snapshot, refs)
    assert observed[0]["observed_at_ms"] == T - 123
