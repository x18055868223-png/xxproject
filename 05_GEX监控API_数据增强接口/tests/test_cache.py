from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from gexmonitorapi.cache import MetricsCache


class StaticScraper:
    async def fetch_section_text(self, section: str) -> str:
        texts = {
            "gex_board": "DVOL 42.9% TOTAL NET GEX $-67M MM Short Gamma MARKET STATE Critical",
            # Deliberately partial: flip/spot present, n1/n2/etc. missing.
            "gamma_exposure": "GAMMA COMPONENTS FLIP 67,372.727 +1.0% SPOT PRICE 66,684.5",
            "volatility": "IV/RV RATIO 1.26x PCR (VOLUME) 0.93",
            "flow": "CALL PREMIUM $27.7M PUT PREMIUM $51.8M P/C RATIO 1.88",
        }
        return texts[section]


class FailingScraper:
    async def fetch_section_text(self, section: str) -> str:
        raise RuntimeError(f"fetch failed for {section}")


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
        return texts[section]


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
            for i in range(4)
        ]
        + [
            datetime(2026, 6, 20, 9, tzinfo=UTC) + timedelta(seconds=i)
            for i in range(4)
        ]
        + [
            datetime(2026, 6, 25, 9, tzinfo=UTC) + timedelta(seconds=i)
            for i in range(4)
        ]
        + [
            datetime(2026, 7, 5, 9, tzinfo=UTC) + timedelta(seconds=i)
            for i in range(4)
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
    assert rank["metrics"]["volatility.iv_rv_ratio"]["value"] == 0.83
    assert rank["metrics"]["volatility.iv_rv_ratio"]["percentile"] == pytest.approx(2 / 3)
    assert rank["metrics"]["flow.call_share_pct"]["value"] == 38.0
    assert rank["metrics"]["flow.call_share_pct"]["percentile"] == pytest.approx(2 / 3)
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
