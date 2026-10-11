from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import bundle_fixture, fixture_payloads, gex_fixture, install_payloads

from gexmonitorapi.cache import RANK_METRICS, MetricsCache
from gexmonitorapi.config import Settings
from gexmonitorapi.json_source import PublicJsonSource
from gexmonitorapi.models import SECTION_FIELDS, SECTIONS, empty_section_data


class StaticScraper:
    async def fetch_section_text(self, section: str) -> str:
        texts = {
            "gex_board": "DVOL 42.9% TOTAL NET GEX $-67M MM Short Gamma MARKET STATE Critical",
            # Deliberately partial: flip/spot present, n1/n2/etc. missing.
            "gamma_exposure": "GAMMA COMPONENTS FLIP 67,372.727 +1.0% SPOT PRICE 66,684.5",
            "volatility": "IV/RV RATIO 1.26x PCR (VOLUME) 0.93",
            "flow": "CALL PREMIUM $27.7M PUT PREMIUM $51.8M P/C RATIO 1.88",
        }
        return texts.get(section, "")  # gamma_profiles/expiry: no page source


class FailingScraper:
    async def fetch_section_text(self, section: str) -> str:
        raise RuntimeError(f"fetch failed for {section}")


class BlockingScraper:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def fetch_section_text(self, section: str) -> str:
        self.started.set()
        await self.release.wait()
        raise RuntimeError(f"released {section}")


class SnapshotScraper:
    def __init__(self, snapshots: list[dict]) -> None:
        self.snapshots = snapshots
        self.index = -1

    async def fetch_snapshot(self) -> dict:
        self.index += 1
        return self.snapshots[self.index]


class SequenceScraper:
    def __init__(self, rows: list[dict[str, float]]) -> None:
        self.rows = rows
        self.index = -1

    async def fetch_section_text(self, section: str) -> str:
        if section == "gex_board":
            self.index += 1
        row = self.rows[self.index]
        texts = {
            "gex_board": (
                f"DVOL {row['dvol']}% TOTAL NET GEX ${row['net_gex_m']}M "
                "MM Short Gamma MARKET STATE Critical"
            ),
            "gamma_exposure": "GAMMA COMPONENTS FLIP 67,372.727 +1.0% SPOT PRICE 66,684.5",
            "volatility": f"IV/RV RATIO {row['iv_rv']}x PCR (VOLUME) {row['pcr']}",
            "flow": (
                f"CALL PREMIUM $27.7M PUT PREMIUM $51.8M "
                f"CALL / PUT TILT {row['call_share']:g}% Call P/C RATIO {row['put_call_ratio']}"
            ),
        }
        return texts.get(section, "")  # gamma_profiles/expiry: no page source


@pytest.mark.asyncio
async def test_refresh_builds_info_payload_and_missing_fields() -> None:
    cache = MetricsCache(StaticScraper(), now=lambda: datetime(2026, 6, 3, 9, tzinfo=UTC))

    payload = await cache.refresh("all")

    assert payload["asset"] == "BTC"
    assert payload["availability"] == "partial"
    assert payload["stale"] is False
    assert payload["gex_board"]["total_net_gex"] == -67000000.0
    assert "gamma_exposure.n2" in payload["missing_fields"]
    assert payload["field_status"]["gamma_exposure.n2"]["status"] == "missing"
    # field_status only surfaces problem fields; "ok" entries are dropped as noise.
    assert all(s["status"] != "ok" for s in payload["field_status"].values())
    assert "gex_board.total_net_gex" not in payload["field_status"]
    assert payload["gex_time_semantics"]["schema_version"] == "gex_time_semantics@1.0.0"
    assert "gex_board.total_net_gex" in payload["gex_time_semantics"]["fields"]
    # raw_excerpt debug blob is no longer exposed in the response.
    assert "raw_excerpt" not in payload["sections"]["gex_board"]


@pytest.mark.asyncio
async def test_refresh_failure_keeps_previous_cache_and_marks_stale() -> None:
    cache = MetricsCache(StaticScraper(), now=lambda: datetime(2026, 6, 3, 9, tzinfo=UTC))
    await cache.refresh("all")
    cache.scraper = FailingScraper()

    payload = await cache.refresh("all")

    assert payload["stale"] is True
    assert payload["availability"] == "partial"
    assert payload["gex_board"]["total_net_gex"] == -67000000.0
    assert payload["sections"]["gex_board"]["last_error"] == "fetch failed for gex_board"


@pytest.mark.asyncio
async def test_public_snapshot_failure_keeps_legacy_fetch_age_behavior() -> None:
    first = _snapshot(
        "2026-06-03T09:00:00+00:00",
        "2026-06-03T09:00:00+00:00",
        total_net_gex=-67000000.0,
        total_gex_fetched_ms=_ms("2026-06-03T09:00:00+00:00"),
        content_hash="hash-good",
    )
    second = _snapshot(
        "2026-06-03T09:30:00+00:00",
        "2026-06-03T09:30:00+00:00",
        total_net_gex=-1.0,
        total_gex_fetched_ms=_ms("2026-06-03T09:30:00+00:00"),
        content_hash="hash-failed",
        errors={"gex": "blocked", "bundle": "blocked"},
    )
    cache = MetricsCache(
        SnapshotScraper([first, second]),
        now=lambda: datetime(2026, 6, 3, 10, tzinfo=UTC),
    )

    await cache.refresh("all")
    payload = await cache.refresh("all")
    field = payload["gex_time_semantics"]["fields"]["gex_board.total_net_gex"]

    assert payload["stale"] is True
    assert payload["availability"] == "partial"
    assert payload["fetched_at"] == "2026-06-03T09:30:00+00:00"
    assert payload["data_age_ms"] is not None
    assert payload["gex_board"]["total_net_gex"] == -67000000.0
    assert payload["sections"]["gex_board"]["fetched_at"] == "2026-06-03T09:30:00+00:00"
    assert payload["sections"]["gex_board"]["latest_attempt_at"] == "2026-06-03T09:30:00+00:00"
    assert payload["sections"]["gex_board"]["content_hash"] == "hash-good"
    assert payload["sections"]["gex_board"]["last_error"] == "blocked"
    assert field["fetched_at_ms"] == _ms("2026-06-03T09:00:00+00:00")
    assert field["time_basis"] == "test_initial_fetch"


@pytest.mark.asyncio
async def test_get_info_does_not_wait_for_blocked_refresh() -> None:
    scraper = BlockingScraper()
    cache = MetricsCache(scraper)
    refresh_task = asyncio.create_task(cache.refresh("gex_board"))
    await scraper.started.wait()

    payload = await asyncio.wait_for(cache.get_info(), timeout=0.1)

    assert payload["availability"] == "missing"
    scraper.release.set()
    await refresh_task


@pytest.mark.asyncio
async def test_refresh_all_accumulates_history_and_adds_rolling_rank(tmp_path) -> None:
    rows = [
        {"net_gex_m": -10.0, "dvol": 40.0, "iv_rv": 1.50, "pcr": 0.80, "call_share": 60.0, "put_call_ratio": 0.70},
        {"net_gex_m": -100.0, "dvol": 55.0, "iv_rv": 0.70, "pcr": 1.20, "call_share": 20.0, "put_call_ratio": 1.90},
        {"net_gex_m": -25.0, "dvol": 35.0, "iv_rv": 1.20, "pcr": 0.95, "call_share": 50.0, "put_call_ratio": 1.10},
        {"net_gex_m": -50.0, "dvol": 45.0, "iv_rv": 0.83, "pcr": 1.63, "call_share": 38.0, "put_call_ratio": 1.63},
    ]
    now_values = iter(
        [
            datetime(2026, 6, 1, 9, tzinfo=UTC) + timedelta(seconds=i)
            for i in range(len(SECTIONS))
        ]
        + [
            datetime(2026, 6, 20, 9, tzinfo=UTC) + timedelta(seconds=i)
            for i in range(len(SECTIONS))
        ]
        + [
            datetime(2026, 6, 25, 9, tzinfo=UTC) + timedelta(seconds=i)
            for i in range(len(SECTIONS))
        ]
        + [
            datetime(2026, 7, 5, 9, tzinfo=UTC) + timedelta(seconds=i)
            for i in range(len(SECTIONS))
        ]
    )
    history_file = tmp_path / "metrics_history.jsonl"
    cache = MetricsCache(
        SequenceScraper(rows),
        history_file=history_file,
        rank_lookback_days=30,
        now=lambda: next(now_values),
    )

    payload = None
    for _ in rows:
        payload = await cache.refresh("all")

    assert payload is not None
    rank = payload["rank"]
    assert rank["window"]["sample_count"] == 3
    assert rank["window"]["history_retained_count"] == 4
    assert rank["metrics"]["gex_board.total_net_gex"]["value"] == -50000000.0
    assert rank["metrics"]["gex_board.total_net_gex"]["percentile"] == pytest.approx(2 / 3)
    assert rank["metrics"]["gex_board.total_net_gex"]["abs_percentile"] == pytest.approx(2 / 3)
    assert rank["metrics"]["volatility.pcr"]["value"] == 1.63
    assert rank["metrics"]["volatility.pcr"]["percentile"] == pytest.approx(1.0)
    assert rank["metrics"]["flow.put_call_ratio"]["value"] == 1.63
    assert rank["metrics"]["flow.put_call_ratio"]["percentile"] == pytest.approx(2 / 3)
    assert set(rank["metrics"]) == set(RANK_METRICS)
    assert len(history_file.read_text(encoding="utf-8").splitlines()) == 4


@pytest.mark.asyncio
async def test_section_refresh_does_not_append_rank_history(tmp_path) -> None:
    history_file = tmp_path / "metrics_history.jsonl"
    cache = MetricsCache(
        StaticScraper(),
        history_file=history_file,
        now=lambda: datetime(2026, 6, 3, 9, tzinfo=UTC),
    )

    await cache.refresh("all")
    await cache.refresh("volatility")

    lines = history_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["metrics"]["gex_board.total_net_gex"] == -67000000.0


@pytest.mark.asyncio
async def test_load_restores_cached_payload_data_and_rank_history(tmp_path) -> None:
    cache_file = tmp_path / "cache.json"
    history_file = tmp_path / "metrics_history.jsonl"
    cache = MetricsCache(
        StaticScraper(),
        cache_file=cache_file,
        history_file=history_file,
        now=lambda: datetime(2026, 6, 3, 9, tzinfo=UTC),
    )
    await cache.refresh("all")

    restored = MetricsCache(
        StaticScraper(),
        cache_file=cache_file,
        history_file=history_file,
        now=lambda: datetime(2026, 6, 3, 10, tzinfo=UTC),
    )
    await restored.load()
    payload = await restored.get_info()

    assert payload["gex_board"]["total_net_gex"] == -67000000.0
    assert payload["rank"]["window"]["history_retained_count"] == 1
    assert payload["rank"]["metrics"]["gex_board.total_net_gex"]["value"] == -67000000.0
    assert payload["rank"]["metrics"]["gex_board.total_net_gex"]["percentile"] == 1.0
    assert payload["gex_time_semantics"]["fields"]["gex_board.total_net_gex"]["time_basis"] == "legacy_page_fetch_time_only"


def _snapshot(
    fetched_at: str,
    latest_attempt_at: str,
    *,
    total_net_gex: float,
    total_gex_fetched_ms: int,
    content_hash: str,
    errors: dict[str, str] | None = None,
) -> dict:
    sections = {}
    for section in SECTIONS:
        data = empty_section_data(section)
        if section == "gex_board":
            data["total_net_gex"] = total_net_gex
        missing = [
            f"{section}.{field}"
            for field in SECTION_FIELDS[section]
            if data.get(field) is None or (isinstance(data.get(field), list) and not data.get(field))
        ]
        semantics = {}
        for field in SECTION_FIELDS[section]:
            path = f"{section}.{field}"
            semantics[path] = {
                "source_ref": f"test.{path}",
                "observed_at_ms": None,
                "generated_at_ms": None,
                "fetched_at_ms": total_gex_fetched_ms if path == "gex_board.total_net_gex" else None,
                "time_basis": "test_initial_fetch" if path == "gex_board.total_net_gex" else "source_field_missing",
                "time_errors": [],
            }
        sections[section] = {
            "data": data,
            "missing_fields": missing,
            "field_status": {
                f"{section}.{field}": {
                    "status": "missing" if f"{section}.{field}" in missing else "ok",
                    "reason": "test_missing" if f"{section}.{field}" in missing else "test_ok",
                    "source_ref": f"test.{section}.{field}",
                }
                for field in SECTION_FIELDS[section]
            },
            "gex_time_semantics": semantics,
            "fetched_at": fetched_at,
            "latest_attempt_at": latest_attempt_at,
            "last_success_at": fetched_at,
            "source_url": "https://example.test",
            "content_hash": content_hash,
            "last_error": None,
        }
    return {
        "sections": sections,
        "metadata": {
            "source_mode": "public_json",
            "errors": errors or {},
            "fetched_at": fetched_at,
            "latest_attempt_at": latest_attempt_at,
            "observed_at": None,
        },
    }


def _ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def test_cached_invalid_clocks_do_not_crash_or_become_unknown_without_error(tmp_path):
    cache = MetricsCache(StaticScraper(), cache_file=tmp_path / "times.json")
    for bad in (True, -1, 0, float("nan"), float("inf"), "invalid"):
        result = cache._coerce_section_time_semantics("gex_board", {
            "gex_board.total_net_gex": {"generated_at_ms": bad}
        })["gex_board.total_net_gex"]
        assert result["generated_at_ms"] is None
        assert result["time_errors"]


# --- public JSON (dashboard-bundle + gex-latest) cache behaviour -----------

REMOVED_NAMES = ("iv_rv_ratio", "call_premium", "put_premium", "call_put_bias", "abnormal_signal", "call_share_pct")


def _json_cache(monkeypatch, payloads: dict, **kwargs) -> MetricsCache:
    install_payloads(monkeypatch, payloads)
    source = PublicJsonSource(Settings(asset="BTC", request_timeout_seconds=1))
    kwargs.setdefault("now", lambda: datetime(2026, 10, 11, 9, tzinfo=UTC))
    return MetricsCache(source, **kwargs)


def _assert_no_removed_fields(payload: dict) -> None:
    dumped = json.dumps(payload)
    for name in REMOVED_NAMES:
        assert name not in dumped, name


@pytest.mark.asyncio
async def test_public_json_fixture_payload_is_ready(monkeypatch) -> None:
    cache = _json_cache(monkeypatch, fixture_payloads())
    payload = await cache.refresh("all")

    assert payload["availability"] == "ready"
    assert payload["stale"] is False
    assert payload["enrichment_availability"] == "ready"
    assert payload["enrichment_stale"] is False
    assert payload["missing_fields"] == []
    assert payload["field_status"] == {}
    for section in SECTIONS:
        assert tuple(payload[section]) == SECTION_FIELDS[section]
        assert section in payload["sections"]
    assert set(payload["gex_time_semantics"]["fields"]) == {
        f"{s}.{f}" for s in SECTIONS for f in SECTION_FIELDS[s]
    }
    assert "gex_time_semantics" not in payload["source_metadata"]
    assert payload["rank"]["metrics"]["flow.whale_24h.net_flow_usd"]["value"] == pytest.approx(355603.67684881645)
    _assert_no_removed_fields(payload)


@pytest.mark.asyncio
async def test_empty_pcr_rows_make_enrichment_partial_not_core(monkeypatch) -> None:
    bundle = bundle_fixture()
    bundle["pcr"]["data"] = []
    payload = await _json_cache(monkeypatch, fixture_payloads(bundle=bundle)).refresh("all")
    # top-level availability describes the signal core (gex-latest sections)
    assert payload["availability"] == "ready"
    assert payload["enrichment_availability"] == "partial"
    assert "volatility.pcr" in payload["missing_fields"]
    assert payload["field_status"]["volatility.pcr"]["status"] == "missing"


@pytest.mark.asyncio
async def test_missing_zero_dte_profile_is_optional_and_ready(monkeypatch) -> None:
    gex = gex_fixture()
    del gex["profiles"]["0dte"]
    payload = await _json_cache(monkeypatch, fixture_payloads(gex=gex)).refresh("all")
    assert payload["availability"] == "ready"
    assert payload["gamma_profiles"]["zero_dte"] is None
    assert "gamma_profiles.zero_dte" not in payload["missing_fields"]


@pytest.mark.asyncio
async def test_bundle_error_keeps_cached_bundle_sections_only(monkeypatch) -> None:
    cache = _json_cache(monkeypatch, fixture_payloads())
    first = await cache.refresh("all")
    gex = gex_fixture()
    gex["total_gex"] = -5_000_000.0
    install_payloads(monkeypatch, fixture_payloads(bundle=OSError("bundle down"), gex=gex))

    payload = await cache.refresh("all")

    # a bundle outage degrades enrichment only; the signal core stays usable
    assert payload["stale"] is False
    assert payload["availability"] == "ready"
    assert payload["enrichment_stale"] is True
    assert payload["enrichment_availability"] == "partial"
    assert payload["gex_board"]["total_net_gex"] == -5_000_000.0
    assert payload["gex_board"]["market_state"] == "negative_gamma"
    for section in ("volatility", "flow", "expiry"):
        assert payload[section] == first[section]
        assert "bundle down" in payload["sections"][section]["last_error"]
    for section in ("gex_board", "gamma_exposure", "gamma_profiles"):
        assert payload["sections"][section]["last_error"] is None
    assert payload["gex_time_semantics"]["fields"]["volatility.pcr"]["generated_at_ms"] is not None


@pytest.mark.asyncio
async def test_removed_fields_never_survive_old_cache_restore(monkeypatch, tmp_path) -> None:
    cache_file = tmp_path / "cache.json"
    legacy_time = {
        "source_ref": "volatility-metrics.metrics.ivRvRatio",
        "observed_at_ms": None,
        "generated_at_ms": 1780000000000,
        "fetched_at_ms": 1780000000000,
        "time_basis": "volatility_metrics_timestamp",
        "time_errors": [],
    }
    old_fields = {
        "volatility.iv_rv_ratio": legacy_time,
        "flow.call_premium": legacy_time,
        "flow.put_premium": legacy_time,
        "flow.call_put_bias": legacy_time,
        "flow.abnormal_signal": legacy_time,
        "volatility.pcr": {**legacy_time, "source_ref": "volatility-metrics.metrics.pcrVolume"},
    }
    old_payload = {
        "asset": "BTC",
        "stale": False,
        "source_mode": "public_json",
        "source_metadata": {"source_mode": "public_json", "gex_time_semantics": {"fields": old_fields}},
        "gex_time_semantics": {"schema_version": "gex_time_semantics@1.0.0", "fields": old_fields},
        "gex_board": {"total_net_gex": 1.0, "dvol": 40.0, "market_state": "positive_gamma"},
        "volatility": {"iv_rv_ratio": 0.9, "pcr": 0.7, "term_structure": []},
        "flow": {
            "call_premium": None, "put_premium": None, "call_put_bias": "60.0% Call",
            "put_call_ratio": 0.7, "abnormal_signal": None,
        },
        "missing_fields": ["flow.call_premium", "flow.put_premium", "flow.abnormal_signal"],
        "field_status": {
            "flow.call_premium": {"status": "missing", "reason": "not_available_from_public_json"},
            "flow.abnormal_signal": {"status": "missing", "reason": "not_available_from_public_json"},
        },
        "sections": {
            "volatility": {
                "fetched_at": "2026-06-03T09:00:00+00:00",
                "last_success_at": "2026-06-03T09:00:00+00:00",
                "missing_fields": [],
                "gex_time_semantics": {"volatility.iv_rv_ratio": legacy_time},
            },
            "flow": {
                "fetched_at": "2026-06-03T09:00:00+00:00",
                "last_success_at": "2026-06-03T09:00:00+00:00",
                "missing_fields": ["flow.call_premium", "flow.put_premium", "flow.abnormal_signal"],
                "field_status": {"flow.call_premium": {"status": "missing"}},
            },
        },
    }
    cache_file.write_text(json.dumps(old_payload), encoding="utf-8")

    cache = _json_cache(monkeypatch, fixture_payloads(), cache_file=cache_file)
    await cache.load()
    restored = await cache.get_info()
    _assert_no_removed_fields(restored)
    assert restored["volatility"]["pcr"] == 0.7
    assert tuple(restored["volatility"]) == SECTION_FIELDS["volatility"]
    assert tuple(restored["flow"]) == SECTION_FIELDS["flow"]

    refreshed = await cache.refresh("all")
    _assert_no_removed_fields(refreshed)
    _assert_no_removed_fields(json.loads(cache_file.read_text(encoding="utf-8")))


@pytest.mark.asyncio
async def test_rank_only_compares_samples_with_same_source(monkeypatch, tmp_path) -> None:
    history_file = tmp_path / "metrics_history.jsonl"
    old_lines = [
        # Pre-redesign sample: no metric_sources recorded.
        {
            "asset": "BTC",
            "fetched_at": "2026-10-01T09:00:00+00:00",
            "metrics": {
                "gex_board.total_net_gex": 1.0,
                "volatility.pcr": 9.0,
                "volatility.iv_rv_ratio": 1.2,
                "flow.call_share_pct": 40.0,
            },
        },
        {
            "asset": "BTC",
            "fetched_at": "2026-10-02T09:00:00+00:00",
            "metrics": {"volatility.pcr": 0.1, "gex_board.dvol": 30.0},
            "metric_sources": {"volatility.pcr": "some-other-source", "gex_board.dvol": "gex-latest.dvol"},
        },
    ]
    history_file.write_text("".join(json.dumps(line) + "\n" for line in old_lines), encoding="utf-8")

    cache = _json_cache(monkeypatch, fixture_payloads(), history_file=history_file)
    await cache.load()
    payload = await cache.refresh("all")
    metrics = payload["rank"]["metrics"]

    assert set(metrics) == set(RANK_METRICS)
    assert metrics["volatility.pcr"]["sample_count"] == 1
    assert metrics["volatility.pcr"]["quality"] == "single_sample"
    assert metrics["gex_board.dvol"]["sample_count"] == 2
    # Legacy gex-latest.total_gex samples keep continuity (same upstream field).
    assert metrics["gex_board.total_net_gex"]["sample_count"] == 2
    newest = json.loads(history_file.read_text(encoding="utf-8").splitlines()[-1])
    assert newest["metric_sources"]["volatility.pcr"] == "dashboard-bundle.pcr.data[latest].pcr_volume"
    assert newest["metric_sources"]["flow.whale_24h.net_flow_usd"].startswith(
        "dashboard-bundle.whaleFlowSummary.summary24h"
    )
    assert set(newest["metrics"]) == set(RANK_METRICS)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_gex", [
    {"error": "rate limited"},
    "missing_timestamp",
    "upstream_stale",
])
async def test_invalid_gex_payload_is_a_core_error_and_keeps_last_good(monkeypatch, bad_gex) -> None:
    cache = _json_cache(monkeypatch, fixture_payloads())
    first = await cache.refresh("all")
    first_time = first["gex_time_semantics"]["fields"]["gex_board.total_net_gex"]["generated_at_ms"]
    if bad_gex == "missing_timestamp":
        bad_gex = gex_fixture()
        bad_gex["total_gex"] = 1.0
        del bad_gex["timestamp"]
    elif bad_gex == "upstream_stale":
        bad_gex = gex_fixture()
        bad_gex["total_gex"] = 1.0
        bad_gex["stale"] = True
    install_payloads(monkeypatch, fixture_payloads(gex=bad_gex))

    payload = await cache.refresh("all")

    assert payload["stale"] is True
    assert payload["availability"] == "partial"
    assert payload["gex_board"]["total_net_gex"] == first["gex_board"]["total_net_gex"]
    assert payload["gex_time_semantics"]["fields"]["gex_board.total_net_gex"]["generated_at_ms"] == first_time
    assert payload["sections"]["gex_board"]["last_error"]


@pytest.mark.asyncio
async def test_pcr_uses_latest_row_regardless_of_order(monkeypatch) -> None:
    bundle = bundle_fixture()
    latest = dict(bundle["pcr"]["data"][0])
    older = dict(latest, timestamp="2026-10-10T08:00:00.000Z", pcr_volume=9.9, pcr_oi=9.9)
    bundle["pcr"]["data"] = [latest, older]
    payload = await _json_cache(monkeypatch, fixture_payloads(bundle=bundle)).refresh("all")
    assert payload["volatility"]["pcr"] == latest["pcr_volume"]
