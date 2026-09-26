from __future__ import annotations

import hashlib
import json
import os
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import astra_kpf_light_v1 as light
from tools import astra_map_projection_v24 as map_projection
from tools.astra_data_engine_v23 import DataEngine


def _ms(day: date, offset: int = 0) -> int:
    dt = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(seconds=offset)
    return int(dt.timestamp() * 1000)


def _write_day(root, day: date, rows: list[tuple[int, str, str, int]]) -> None:
    source_root = root / "source" / "binance_usdm" / "aggTrades" / "BTCUSDT"
    source_root.mkdir(parents=True, exist_ok=True)
    filename = f"BTCUSDT-aggTrades-{day.isoformat()}.zip"
    zip_path = source_root / filename
    csv_name = filename.replace(".zip", ".csv")
    lines = []
    for agg_id, price, qty, ts_ms in rows:
        lines.append(f"{agg_id},{price},{qty},{agg_id},{agg_id},{ts_ms},false\n")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(csv_name, "".join(lines))
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (source_root / f"{filename}.CHECKSUM").write_text(f"{digest}  {filename}\n", encoding="utf-8")


def _write_full_window(root, end_day: date) -> None:
    agg_id = 1
    for idx in range(90):
        day = end_day - timedelta(days=89 - idx)
        rows = [
            (agg_id, "90000", "25", _ms(day, 1)),
            (agg_id + 1, "90500", "25", _ms(day, 2)),
            (agg_id + 2, "90500", "25", _ms(day, 3)),
            (agg_id + 3, "91000", "25", _ms(day, 4)),
        ]
        _write_day(root, day, rows)
        agg_id += 4


def _artifact(root, ref):
    return json.loads((root / ref["path"]).read_text(encoding="utf-8"))


def _registry(path: Path) -> Path:
    body = {
        "schema": "astra_data_product_registry@2.3.0",
        "products": [
            {
                "product_id": map_projection.KPF_PRODUCT,
                "provider_id": "kpf_light",
                "collector_id": "astra_kpf_light_v1",
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
                    "default": {"ttl_ms": 604800000, "allowed_data_states": ["OK", "PARTIAL", "INVALID"]},
                    "background": {"ttl_ms": 604800000, "allowed_data_states": ["OK", "PARTIAL", "INVALID"]},
                    "health": {"ttl_ms": 604800000, "allowed_data_states": ["OK", "PARTIAL", "INVALID"]},
                },
            }
        ],
    }
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return path


def _points_payload(points):
    return [
        {
            "price_bin": point.price_bin,
            "density": point.density,
            "smooth_density": point.smooth_density,
            "participating_volume_bars": point.participating_volume_bars,
        }
        for point in points
    ]


def _candidate_payload(candidates):
    return [
        {
            "raw_center": item.raw_center,
            "raw_basin_low": item.raw_basin_low,
            "raw_basin_high": item.raw_basin_high,
            "evidence_grade": item.evidence_grade,
            "evidence_type": item.evidence_type,
            "grade_path": item.grade_path,
            "display_center": item.display_center,
        }
        for item in candidates
    ]


class _JsonResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_streaming_volume_bar_tail_does_not_reset_across_days(tmp_path):
    first = date(2026, 1, 1)
    second = date(2026, 1, 2)
    _write_day(tmp_path, first, [(1, "90000", "60", _ms(first, 1))])
    _write_day(tmp_path, second, [(2, "90100", "40", _ms(second, 1))])

    statuses = light.inspect_sources(tmp_path, [first, second])
    parsed = light.parse_window(light.build_config(tmp_path), statuses)

    assert len(parsed.bars) == 1
    assert str(parsed.bars[0].total_qty) == "100"
    assert parsed.bars[0].end_ts_ms == _ms(second, 1)
    assert set(parsed.bars[0].bin_qty) == {90000, 90100}


def test_daily_duplicate_filter_resets_but_volume_phase_crosses_days(tmp_path):
    first = date(2026, 1, 1)
    second = date(2026, 1, 2)
    _write_day(
        tmp_path,
        first,
        [
            (1, "90000", "60", _ms(first, 1)),
            (1, "90000", "60", _ms(first, 1)),
        ],
    )
    _write_day(tmp_path, second, [(2, "90100", "40", _ms(second, 1))])

    statuses = light.inspect_sources(tmp_path, [first, second])
    streamed = light.stream_window(light.build_config(tmp_path), statuses, datetime(2026, 1, 3, tzinfo=UTC))
    parsed = light.parse_window(light.build_config(tmp_path), statuses)

    assert streamed.volume_bar_count == 1
    assert parsed.duplicate_count == 1
    assert parsed.duplicate_conflict_count == 0
    assert parsed.bars[0].end_ts_ms == _ms(second, 1)
    assert str(parsed.bars[0].total_qty) == "100"


def test_cross_day_agg_id_or_clock_conflict_rejects_instead_of_deleting(tmp_path):
    first = date(2026, 1, 1)
    second = date(2026, 1, 2)
    _write_day(tmp_path, first, [(10, "90000", "60", _ms(first, 10))])
    _write_day(tmp_path, second, [(9, "90100", "40", _ms(second, 1))])

    statuses = light.inspect_sources(tmp_path, [first, second])
    streamed = light.stream_window(light.build_config(tmp_path), statuses, datetime(2026, 1, 3, tzinfo=UTC))

    assert second.isoformat() in streamed.schema_failed_dates
    assert streamed.duplicate_conflict_count == 1
    assert streamed.volume_bar_count == 0


def test_source_day_timestamp_out_of_range_rejects_day(tmp_path):
    first = date(2026, 1, 1)
    _write_day(tmp_path, first, [(1, "90000", "100", _ms(first + timedelta(days=1), 1))])

    statuses = light.inspect_sources(tmp_path, [first])
    streamed = light.stream_window(light.build_config(tmp_path), statuses, datetime(2026, 1, 3, tzinfo=UTC))

    assert first.isoformat() in streamed.schema_failed_dates
    assert streamed.duplicate_conflict_count == 1
    assert streamed.volume_bar_count == 0


def test_oneshot_publishes_three_hash_checked_artifacts(tmp_path):
    end_day = date(2026, 1, 30)
    as_of_ms = _ms(end_day + timedelta(days=1), 60)
    _write_full_window(tmp_path, end_day)
    output = tmp_path / "published"

    result = light.run_oneshot(
        data_root=tmp_path,
        output_root=output,
        as_of_ms=as_of_ms,
        current_price=Decimal("89500"),
        min_free_bytes=0,
        storage_budget_bytes=10 * 1024**3,
    )

    assert result["ok"] is True
    manifest = json.loads((output / "current.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == light.SCHEMA_MANIFEST
    assert set(manifest["artifacts"]) == {"snapshot", "audit", "zones"}
    version_id = manifest["version_id"]
    for ref in manifest["artifacts"].values():
        path = output / ref["path"]
        assert path.exists()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == ref["sha256"]

    snapshot = _artifact(output, manifest["artifacts"]["snapshot"])
    audit = _artifact(output, manifest["artifacts"]["audit"])
    zones = _artifact(output, manifest["artifacts"]["zones"])
    assert {snapshot["version_id"], audit["version_id"], zones["version_id"]} == {version_id}
    assert snapshot["status"] == "READY"
    assert snapshot["supported_ready"] is True
    assert audit["parse"]["tail_phase_policy"] == "single_volume_bar_builder_across_all_verified_days"
    assert audit["parse"]["stored_cache_policy"] == "source_zip_and_checksum_only_streaming_density_no_full_parsed_csv_no_window_bar_array"
    assert audit["parse"]["production_density_policy"] == "stream_volume_bars_into_density_accumulator_no_90d_bar_list"
    assert audit["source_manifest"]["ok"] is True
    assert all(record["first_full_checksum_verified_at_ms"] for record in audit["source_records"])
    assert all(record["last_full_checksum_verified_at_ms"] for record in audit["source_records"])
    assert zones["schema"] == light.SCHEMA_ZONES
    assert zones["zone_count"] == len(zones["zones"])
    assert zones["zone_count"] > 0
    assert all(zone["market"]["mapping"] == "NOMINAL_ONLY" for zone in zones["zones"])
    assert all(zone["market"]["basis"] == light.PRICE_BASIS for zone in zones["zones"])
    assert any(zone["raw"]["center"] == 90500 for zone in zones["zones"])
    for zone in zones["zones"]:
        if zone["usage"]["can_use"]:
            assert zone["evidence"]["grade"] in {"A", "B"}


def test_streaming_density_matches_original_density_builder_and_targets(tmp_path):
    end_day = date(2026, 1, 30)
    as_of = datetime(2026, 1, 31, 0, 1, tzinfo=UTC)
    _write_full_window(tmp_path, end_day)
    config = light.build_config(tmp_path)
    statuses = light.inspect_sources(tmp_path, light.required_dates(end_day, config.history_days))

    parsed = light.parse_window(config, statuses)
    streamed = light.stream_window(config, statuses, as_of)

    original_90 = light.DensityBuilder().build(config, parsed.bars, as_of, config.history_days)
    original_60 = light.DensityBuilder().build(config, parsed.bars, as_of, config.fresh_confirm_days)
    assert _points_payload(streamed.density_90) == _points_payload(original_90)
    assert _points_payload(streamed.density_60) == _points_payload(original_60)

    selected_original, debug_original = light.compute_candidates(
        config, parsed, as_of, "OK", Decimal("89500")
    )
    selected_streamed, debug_streamed = light.compute_candidates_from_density(
        config, streamed, "OK", Decimal("89500")
    )
    assert _candidate_payload(selected_streamed) == _candidate_payload(selected_original)
    assert _candidate_payload(debug_streamed) == _candidate_payload(debug_original)


def test_no_quotes_bundle_is_not_usable(tmp_path):
    end_day = date(2026, 1, 30)
    as_of_ms = _ms(end_day + timedelta(days=1), 60)
    _write_full_window(tmp_path, end_day)
    output = tmp_path / "published"

    result = light.run_oneshot(
        data_root=tmp_path,
        output_root=output,
        as_of_ms=as_of_ms,
        current_price=None,
        fetch_quote=False,
        min_free_bytes=0,
        storage_budget_bytes=10 * 1024**3,
    )

    manifest = result["manifest"]
    snapshot = _artifact(output, manifest["artifacts"]["snapshot"])
    zones = _artifact(output, manifest["artifacts"]["zones"])
    assert snapshot["status"] == "NO_QUOTES"
    assert snapshot["usage"]["can_use"] is False
    assert snapshot["quote"]["http_budget_used"] == 0
    assert all(zone["usage"]["can_use"] is False for zone in zones["zones"])


def test_auto_quote_failure_does_not_cache_and_later_recovers(tmp_path, monkeypatch):
    end_day = date(2026, 1, 30)
    as_of_ms = _ms(end_day + timedelta(days=1), 60)
    _write_full_window(tmp_path, end_day)
    output = tmp_path / "published"
    monkeypatch.setattr(light, "fetch_current_quote", lambda *args, **kwargs: None)

    first = light.run_oneshot(
        data_root=tmp_path,
        output_root=output,
        as_of_ms=as_of_ms,
        current_price=None,
        fetch_quote=True,
        min_free_bytes=0,
        storage_budget_bytes=10 * 1024**3,
    )

    first_snapshot = _artifact(output, first["manifest"]["artifacts"]["snapshot"])
    first_audit = _artifact(output, first["manifest"]["artifacts"]["audit"])
    assert first_snapshot["status"] == "NO_QUOTES"
    assert first_snapshot["quote"]["http_budget_used"] == 1
    assert first_audit["parse"]["bar_count"] > 0

    quote = light.CurrentQuote(
        price=Decimal("89500"),
        observed_ms=as_of_ms + 1,
        acquired_at_ms=as_of_ms + 2,
        source_url=light.DEFAULT_USDM_QUOTE_URL,
        raw={"symbol": "BTCUSDT", "price": "89500", "time": as_of_ms + 1},
    )
    monkeypatch.setattr(light, "fetch_current_quote", lambda *args, **kwargs: quote)

    second = light.run_oneshot(
        data_root=tmp_path,
        output_root=output,
        as_of_ms=as_of_ms,
        current_price=None,
        fetch_quote=True,
        min_free_bytes=0,
        storage_budget_bytes=10 * 1024**3,
    )

    second_snapshot = _artifact(output, second["manifest"]["artifacts"]["snapshot"])
    second_audit = _artifact(output, second["manifest"]["artifacts"]["audit"])
    assert second_snapshot["status"] == "READY"
    assert second_snapshot["quote"]["source"] == "binance_usdm_public_ticker_price"
    assert second_audit["parse"]["bar_count"] > 0


def test_fetch_quote_requires_exchange_observed_time(monkeypatch):
    monkeypatch.setattr(light.urllib.request, "urlopen", lambda *args, **kwargs: _JsonResponse({"symbol": "BTCUSDT", "price": "90000"}))
    monkeypatch.setattr(light, "now_ms", lambda: 1_000_000)

    assert light.fetch_current_quote() is None


def test_fetch_quote_rejects_future_or_stale_observed_time(monkeypatch):
    observed_now = 1_000_000
    monkeypatch.setattr(light, "now_ms", lambda: observed_now)
    monkeypatch.setattr(light.urllib.request, "urlopen", lambda *args, **kwargs: _JsonResponse({"symbol": "BTCUSDT", "price": "90000", "time": observed_now + 1}))

    assert light.fetch_current_quote() is None

    monkeypatch.setattr(light.urllib.request, "urlopen", lambda *args, **kwargs: _JsonResponse({"symbol": "BTCUSDT", "price": "90000", "time": observed_now - light.DEFAULT_QUOTE_MAX_AGE_MS - 1}))

    assert light.fetch_current_quote() is None


def test_fetch_quote_rejects_non_finite_price_and_records_response_done_clock(monkeypatch):
    observed_now = 1_000_000
    monkeypatch.setattr(light, "now_ms", lambda: observed_now)
    monkeypatch.setattr(light.urllib.request, "urlopen", lambda *args, **kwargs: _JsonResponse({"symbol": "BTCUSDT", "price": "NaN", "time": observed_now}))

    assert light.fetch_current_quote() is None

    monkeypatch.setattr(light.urllib.request, "urlopen", lambda *args, **kwargs: _JsonResponse({"symbol": "BTCUSDT", "price": "90000", "time": observed_now - 1}))
    quote = light.fetch_current_quote()

    assert quote is not None
    assert quote.observed_ms == observed_now - 1
    assert quote.acquired_at_ms == observed_now


def test_warmup_writes_audit_without_replaying_downloaded_days(tmp_path, monkeypatch):
    end_day = date(2026, 1, 30)
    as_of_ms = _ms(end_day + timedelta(days=1), 60)
    _write_day(tmp_path, end_day, [(1, "90000", "100", _ms(end_day, 1))])
    monkeypatch.setattr(light, "stream_window", lambda *args, **kwargs: pytest.fail("warmup must not stream parse"))

    result = light.run_oneshot(
        data_root=tmp_path,
        output_root=tmp_path / "published",
        as_of_ms=as_of_ms,
        current_price=None,
        min_free_bytes=0,
        storage_budget_bytes=10 * 1024**3,
    )

    snapshot = _artifact(tmp_path / "published", result["manifest"]["artifacts"]["snapshot"])
    audit = _artifact(tmp_path / "published", result["manifest"]["artifacts"]["audit"])
    assert snapshot["status"] == "WARMUP"
    assert audit["parse"]["bar_count"] == 0


def test_ready_same_cutoff_skips_recompute(tmp_path, monkeypatch):
    end_day = date(2026, 1, 30)
    as_of_ms = _ms(end_day + timedelta(days=1), 60)
    _write_full_window(tmp_path, end_day)
    output = tmp_path / "published"

    first = light.run_oneshot(
        data_root=tmp_path,
        output_root=output,
        as_of_ms=as_of_ms,
        current_price=Decimal("89500"),
        min_free_bytes=0,
        storage_budget_bytes=10 * 1024**3,
    )
    assert first["ok"] is True
    monkeypatch.setattr(light, "inspect_sources", lambda *args, **kwargs: pytest.fail("same cutoff should skip source scan"))

    second = light.run_oneshot(
        data_root=tmp_path,
        output_root=output,
        as_of_ms=as_of_ms,
        current_price=Decimal("89500"),
        min_free_bytes=0,
        storage_budget_bytes=10 * 1024**3,
    )
    assert second["unchanged"] is True


def test_same_cutoff_recomputes_when_artifact_hash_or_source_fingerprint_changes(tmp_path, monkeypatch):
    end_day = date(2026, 1, 30)
    as_of_ms = _ms(end_day + timedelta(days=1), 60)
    _write_full_window(tmp_path, end_day)
    output = tmp_path / "published"

    first = light.run_oneshot(
        data_root=tmp_path,
        output_root=output,
        as_of_ms=as_of_ms,
        current_price=Decimal("89500"),
        min_free_bytes=0,
        storage_budget_bytes=10 * 1024**3,
    )
    manifest = first["manifest"]
    snapshot_path = output / manifest["artifacts"]["snapshot"]["path"]
    snapshot_path.write_text(snapshot_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    calls = {"count": 0}
    original_inspect = light.inspect_sources

    def counted_inspect(*args, **kwargs):
        calls["count"] += 1
        return original_inspect(*args, **kwargs)

    monkeypatch.setattr(light, "inspect_sources", counted_inspect)
    second = light.run_oneshot(
        data_root=tmp_path,
        output_root=output,
        as_of_ms=as_of_ms,
        current_price=Decimal("89500"),
        min_free_bytes=0,
        storage_budget_bytes=10 * 1024**3,
    )

    assert second.get("unchanged") is not True
    assert calls["count"] > 0

    calls["count"] = 0
    zip_path = light.source_zip_path(tmp_path, end_day)
    with zip_path.open("ab") as handle:
        handle.write(b"tamper")
    third = light.run_oneshot(
        data_root=tmp_path,
        output_root=output,
        as_of_ms=as_of_ms,
        current_price=Decimal("89500"),
        min_free_bytes=0,
        storage_budget_bytes=10 * 1024**3,
    )

    assert third.get("unchanged") is not True
    assert calls["count"] > 0
    assert _artifact(output, third["manifest"]["artifacts"]["snapshot"])["status"] != "READY"


def test_same_cutoff_recomputes_when_source_content_changes_with_same_size_and_mtime(tmp_path, monkeypatch):
    end_day = date(2026, 1, 30)
    as_of_ms = _ms(end_day + timedelta(days=1), 60)
    _write_full_window(tmp_path, end_day)
    output = tmp_path / "published"

    first = light.run_oneshot(
        data_root=tmp_path,
        output_root=output,
        as_of_ms=as_of_ms,
        current_price=Decimal("89500"),
        min_free_bytes=0,
        storage_budget_bytes=10 * 1024**3,
    )
    assert first["ok"] is True

    zip_path = light.source_zip_path(tmp_path, end_day)
    original_stat = zip_path.stat()
    body = bytearray(zip_path.read_bytes())
    body[0] = body[0] ^ 0x01
    zip_path.write_bytes(body)
    os.utime(zip_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    calls = {"count": 0}
    original_inspect = light.inspect_sources

    def counted_inspect(*args, **kwargs):
        calls["count"] += 1
        return original_inspect(*args, **kwargs)

    monkeypatch.setattr(light, "inspect_sources", counted_inspect)
    second = light.run_oneshot(
        data_root=tmp_path,
        output_root=output,
        as_of_ms=as_of_ms,
        current_price=Decimal("89500"),
        min_free_bytes=0,
        storage_budget_bytes=10 * 1024**3,
    )

    assert second.get("unchanged") is not True
    assert calls["count"] > 0
    assert _artifact(output, second["manifest"]["artifacts"]["snapshot"])["status"] != "READY"


def test_source_retention_keeps_previous_ready_window_then_contracts_to_current_90(tmp_path):
    end_day = date(2026, 1, 30)
    as_of_ms = _ms(end_day + timedelta(days=1), 60)
    _write_full_window(tmp_path, end_day)
    source_root = light.source_root(tmp_path)
    unknown = source_root / "manual-note.txt"
    unknown.write_text("keep", encoding="utf-8")
    expired = end_day - timedelta(days=90)
    _write_day(tmp_path, expired, [(9000, "88000", "100", _ms(expired, 1))])
    expired_zip = light.source_zip_path(tmp_path, expired)
    expired_checksum = light.checksum_path_for(expired_zip)
    expired_meta = expired_zip.with_name(expired_zip.name + ".source.json")
    expired_meta.write_text(json.dumps({"source_acquired_at_ms": 1}), encoding="utf-8")
    output = tmp_path / "published"

    first = light.run_oneshot(
        data_root=tmp_path,
        output_root=output,
        as_of_ms=as_of_ms,
        current_price=Decimal("89500"),
        min_free_bytes=0,
        storage_budget_bytes=10 * 1024**3,
    )

    assert first["ok"] is True
    assert expired_zip.exists() is False
    assert expired_checksum.exists() is False
    assert expired_meta.exists() is False
    assert unknown.exists() is True

    old_start = end_day - timedelta(days=89)
    next_end = end_day + timedelta(days=1)
    warmup = light.run_oneshot(
        data_root=tmp_path,
        output_root=output,
        as_of_ms=_ms(next_end + timedelta(days=1), 60),
        current_price=Decimal("89500"),
        min_free_bytes=0,
        storage_budget_bytes=10 * 1024**3,
    )

    assert _artifact(output, warmup["manifest"]["artifacts"]["snapshot"])["status"] == "WARMUP"
    assert light.source_zip_path(tmp_path, old_start).exists() is True
    for offset in (120, 180):
        light.run_oneshot(
            data_root=tmp_path, output_root=output,
            as_of_ms=_ms(next_end + timedelta(days=1), offset),
            current_price=Decimal("89500"), min_free_bytes=0,
            storage_budget_bytes=10 * 1024**3,
        )
        assert json.loads((output / "previous.json").read_text())["status"] == "READY"
        assert light.source_zip_path(tmp_path, old_start).exists()
        assert len(list((output / "versions").iterdir())) == 2
    retention = light.cleanup_source_retention(tmp_path, output, next_end, 90, keep_previous_ready=True)
    assert light.source_zip_path(tmp_path, old_start).name not in retention["deleted"]
    assert light.source_zip_path(tmp_path, old_start).exists() is True

    _write_day(
        tmp_path,
        next_end,
        [
            (100000, "90000", "25", _ms(next_end, 1)),
            (100001, "90500", "25", _ms(next_end, 2)),
            (100002, "90500", "25", _ms(next_end, 3)),
            (100003, "91000", "25", _ms(next_end, 4)),
        ],
    )
    ready = light.run_oneshot(
        data_root=tmp_path,
        output_root=output,
        as_of_ms=_ms(next_end + timedelta(days=1), 60),
        current_price=Decimal("89500"),
        min_free_bytes=0,
        storage_budget_bytes=10 * 1024**3,
    )

    assert _artifact(output, ready["manifest"]["artifacts"]["snapshot"])["status"] == "READY"
    assert light.source_zip_path(tmp_path, old_start).exists() is False
    assert light.checksum_path_for(light.source_zip_path(tmp_path, old_start)).exists() is False
    assert unknown.exists() is True


def test_zone_pool_over_limit_rejects_whole_batch(tmp_path, monkeypatch):
    end_day = date(2026, 1, 30)
    as_of_ms = _ms(end_day + timedelta(days=1), 60)
    _write_full_window(tmp_path, end_day)

    fake_candidates = [SimpleNamespace(raw_center=i, raw_basin_low=i - 50, raw_basin_high=i + 50) for i in range(3)]
    monkeypatch.setattr(light, "compute_candidates_from_density", lambda *args, **kwargs: ([], fake_candidates))

    result = light.run_oneshot(
        data_root=tmp_path,
        output_root=tmp_path / "published",
        as_of_ms=as_of_ms,
        current_price=Decimal("89500"),
        min_free_bytes=0,
        storage_budget_bytes=10 * 1024**3,
        max_zones=2,
    )

    assert result["ok"] is False
    assert "raw zone pool has 3 zones" in result["error"]
    assert (tmp_path / "published" / "current.json").exists() is False
    assert (tmp_path / "published" / "failures").exists()


def test_vendor_manifest_matches_copied_original_sources():
    ok, errors = light.verify_vendor_manifest()

    assert ok is True
    assert errors == []


def test_producer_manifest_ingests_through_map_kpf_collector(tmp_path):
    end_day = date(2026, 1, 30)
    as_of_ms = _ms(end_day + timedelta(days=1), 60)
    _write_full_window(tmp_path, end_day)
    output = tmp_path / "published"
    result = light.run_oneshot(
        data_root=tmp_path,
        output_root=output,
        as_of_ms=as_of_ms,
        current_price=Decimal("89500"),
        min_free_bytes=0,
        storage_budget_bytes=10 * 1024**3,
    )
    engine = DataEngine(tmp_path / "engine", registry_path=_registry(tmp_path / "registry.json"))
    manifest = json.loads((output / "current.json").read_text(encoding="utf-8"))
    zones_payload = _artifact(output, manifest["artifacts"]["zones"])

    collected = map_projection.collect_kpf_artifacts(
        engine,
        zones_payload["generated_at_ms"] + 1000,
        {"kpf_manifest_path": result["current_manifest_path"]},
    )

    assert collected["gaps"] == []
    assert collected["record"] is not None
    values = collected["record"]["content"]["values"]
    assert values["version_id"] == result["manifest"]["version_id"]
    assert values["raw_zone_count"] > 0
    assert values["finite_zone_count"] > 0
    first = values["zones"][0]
    assert first["market"] == "BINANCE_USD_M_FUTURES"
    assert first["price_basis"] == "BINANCE_USDM_BTCUSDT_AGGTRADES"
    assert first["coordinate_qualification"] in {"UNKNOWN", "NOMINAL_ONLY"}
    assert "native_producer_zone" in first["original_zone"]
    assert first["original_zone"]["native_producer_zone"]["market"]["mapping"] == "NOMINAL_ONLY"


def test_stream_download_hard_stop_removes_part_file(tmp_path, monkeypatch):
    class FakeResponse:
        def __init__(self, payload: bytes):
            self.payload = payload
            self.used = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size=-1):
            if self.used:
                return b""
            self.used = True
            return self.payload

    monkeypatch.setattr(light.urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse(b"abcdef"))
    part = tmp_path / "download.part"

    with pytest.raises(light.KpfLightError):
        light.stream_http_to_part(
            "https://data.binance.vision/example.zip",
            part,
            timeout_seconds=1,
            min_free_bytes=0,
            storage_budget_bytes=1,
            data_root=tmp_path,
        )

    assert part.exists() is False


def test_pid_lock_recovers_dead_process_file(tmp_path):
    lock_path = tmp_path / "run.lock"
    lock_path.write_text(json.dumps({"pid": 999999999, "created_at_ms": 1}), encoding="utf-8")

    with light.RunLock(lock_path, use_flock=False):
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["pid"] > 0

    assert lock_path.exists() is False


def test_download_acquisition_clock_follows_verified_response(tmp_path, monkeypatch):
    day = date(2026, 1, 30)
    seed = tmp_path / "seed"
    _write_day(seed, day, [(1, "90000", "100", _ms(day, 1))])
    payload = light.source_zip_path(seed, day).read_bytes()
    events = []

    def fake_stream(url, part_path, **kwargs):
        part_path.write_bytes(
            (hashlib.sha256(payload).hexdigest() + "  source.zip\n").encode()
            if url.endswith(".CHECKSUM") else payload
        )
        events.append("checksum_response" if url.endswith(".CHECKSUM") else "zip_response")

    def completed_clock():
        assert events[:2] == ["checksum_response", "zip_response"]
        events.append("clock")
        return 123456789

    monkeypatch.setattr(light, "stream_http_to_part", fake_stream)
    monkeypatch.setattr(light, "now_ms", completed_clock)
    result = light.download_one_day(tmp_path / "target", day, min_free_bytes=0)
    assert result.verified
    assert result.source_acquired_at_ms == 123456789


def test_installer_and_systemd_are_release_namespaced():
    root = Path(__file__).resolve().parents[1]
    installer = (root / "deploy" / "kpf_light" / "install_kpf_light.sh").read_text(encoding="utf-8")
    rollback = (root / "deploy" / "kpf_light" / "rollback_kpf_light.sh").read_text(encoding="utf-8")
    service = (root / "deploy" / "kpf_light" / "systemd" / "astra-kpf-light.service").read_text(encoding="utf-8")
    env_example = (root / "deploy" / "kpf_light" / "astra-kpf-light.env.example").read_text(encoding="utf-8")

    assert 'APP_ROOT="/opt/astra-kpf-light"' in installer
    assert 'DATA_DIR="/var/lib/astra-kpf-light"' in installer
    assert "ASTRA_KPF_LIGHT_APP_DIR" not in installer
    assert "ASTRA_KPF_LIGHT_DATA_ROOT:-" not in installer
    assert 'rm -rf "$APP_DIR' not in installer
    assert "chown -R" not in installer
    assert "Refusing to chown symlink" in installer
    assert "release_target_or_empty" in installer
    assert "Refusing release link outside" in installer
    assert "safe_rm_release_dir" in installer
    assert "package_manifest.json" in installer
    assert "astra-kpf-light.env.example" in installer
    assert "/releases" in installer
    assert "pkg-$package_hash" in installer
    assert "mv -Tf" in installer
    assert "previous" in installer
    assert "release_target_or_empty" in rollback
    assert "Refusing release link outside" in rollback
    assert "WorkingDirectory=/opt/astra-kpf-light/current" in service
    assert "ExecStart=/usr/bin/python3 /opt/astra-kpf-light/current/tools/astra_kpf_light_v1.py oneshot --data-root /var/lib/astra-kpf-light --output-root /var/lib/astra-kpf-light/published --fetch-quote" in service
    assert "User=bitnami" in service
    assert "StateDirectory=astra-kpf-light" in service
    assert "ASTRA_KPF_LIGHT_DATA_ROOT" not in env_example
