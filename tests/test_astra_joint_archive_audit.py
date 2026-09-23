#!/usr/bin/env python3
from __future__ import annotations

import gzip
import json
import tempfile
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from astra_joint_archive_audit import (
    audit_archive,
    canonical_hash,
    choose_market_files,
    default_output_dir,
    next_bjt_16_expiry,
    rebuild_um_features,
)


BASE_MS = 1_788_998_400_000  # 2026-09-10 00:00:00 UTC


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def write_jsonl_gz(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            stream.write("\n")


def kline(open_ms: int, price: float, *, available_delay_ms: int = 1) -> dict:
    close_ms = open_ms + 59_999
    return {
        "open_time_ms": open_ms,
        "open_time": open_ms,
        "close_time_ms": close_ms,
        "close_time": close_ms,
        "open": price,
        "high": price + 10,
        "low": price - 10,
        "close": price + 1,
        "volume": 10.0,
        "quote_volume": price * 10,
        "trade_count": 10.0,
        "taker_buy_base_volume": 6.0,
        "taker_buy_quote_volume": price * 6,
        "available_at_ms": close_ms + available_delay_ms,
    }


def card(card_id: str, ms: int, *, event_type="NR_REPAIR_CONFIRMED", fixed=False, synthetic=False) -> dict:
    episode = f"nr_{ms}_UP"
    analysis_round = None
    if fixed:
        event_type = "FIXED_ANALYSIS_ROUND"
        episode = f"fixed_us_round_{card_id}"
        analysis_round = {"audit_only": True, "scheduled_time_ms": ms}
    result = {
        "identity": {
            "card_id": card_id,
            "confirmed_time_ms": ms,
            "confirmed_at": "2026-09-10T09:30:00+08:00",
            "episode_id": episode,
            "event_type": event_type,
            "is_synthetic": synthetic,
            "strategy_version": "1.6.2",
            "symbol": "BTC",
        },
        "decision": {"lean": "BULLISH", "side_hint": "put_credit_spread", "support_label": "TRADE_SUPPORT_WEAK"},
        "signal_window": {"episode_direction": "UP", "peak_m_die": 0.8, "nr_state": "confirmed"},
        "factor_cross_section": {
            "anchor": {"ready": True, "score": 70.0, "normalized_deviation": 0.2},
            "gex_info": {"availability": "ready", "data_status": "OK", "call_wall": 82000.0, "put_wall": 78000.0},
            "gamma_regime": {"data_state": "OK", "regime": "TRANSITION", "pin_strike": 80000.0},
        },
    }
    if analysis_round:
        result["analysis_round"] = analysis_round
    return result


def test_archive_audit_extracts_nr_interface_and_respects_closed_bar_time():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source = root / "raw" / "server-frozen"
        facts = root / "facts"
        source.mkdir(parents=True)

        native_card = card("native", BASE_MS + 31 * 60_000)
        fixed_card = card("fixed", BASE_MS + 32 * 60_000, fixed=True)
        rows = [
            {"source_sha256": "source-review", "line": 1, "version": "1.6.2", "original_card": native_card, "projection_schema": "x"},
            {"source_sha256": "source-review", "line": 2, "version": "1.6.2", "original_card": fixed_card, "projection_schema": "x"},
        ]
        write_jsonl_gz(source / "signal_review.jsonl.facts.jsonl.gz", rows)
        for idx in range(18):
            write_jsonl_gz(
                source / f"decisions-{idx}.facts.jsonl.gz",
                [{"ts_ms": BASE_MS + idx * 60_000, "version": "1.6.2", "decision": {"demo_version": "1.6.2"}}],
            )
        manifest = []
        for path in sorted(source.glob("*.facts.jsonl.gz")):
            manifest.append({"file": path.name, "sha256": _sha(path), "bytes": path.stat().st_size})
        write_json(source / "manifest.json", manifest)
        write_json(source / "scan_summary.json", [{"name": "signal_review.jsonl", "lines": 2, "bad_lines": 0}])
        (source / "slim_bundle.tar").write_bytes(b"bundle")

        market_rows = [kline(BASE_MS + i * 60_000, 80_000.0 + i) for i in range(31)]
        # This bar is after the card time and must not become the last closed price.
        market_rows.append(kline(BASE_MS + 31 * 60_000, 90_000.0))
        write_jsonl_gz(facts / "um" / "2026-09-10.jsonl.gz", market_rows)
        write_json(facts / "manifest.json", [{"path": "facts/um/2026-09-10.jsonl.gz", "rows": len(market_rows)}])

        out = root / "R" / "archives-analysis"
        audit = audit_archive(source, out, facts)

        assert audit["server_frozen"]["fact_file_count"] == 19
        assert audit["signal_review_observations"]["signal_review_card_count"] == 2
        assert audit["signal_review_observations"]["native_nr_events"] == 1
        assert audit["signal_review_observations"]["record_kind_counts"]["fixed_round"] == 1
        assert audit["signal_review_observations"]["option_common_min_100_delivery_day_gate"].startswith("insufficient")
        lines = (out / "nr_observation_interface.jsonl").read_text(encoding="utf-8").splitlines()
        native = json.loads(lines[0])
        assert native["source"]["source_record_hash"] == canonical_hash(native_card)
        assert native["um_rebuilt_features"]["last_closed_price"] < 90_000
        assert native["um_rebuilt_features"]["window_status"]["15"] == "available"
        assert (out / "archive_audit_report.md").exists()
        assert (out / "server_fact_inventory.csv").exists()


def test_next_bjt_16_expiry_uses_next_day_after_16_bjt():
    before_16_bjt = 1_789_023_000_000  # 2026-09-10 14:30 +08
    after_16_bjt = 1_789_034_400_000  # 2026-09-10 17:40 +08
    assert next_bjt_16_expiry(before_16_bjt)["expiry_date_bjt"] == "2026-09-10"
    assert next_bjt_16_expiry(after_16_bjt)["expiry_date_bjt"] == "2026-09-11"


def test_default_output_dir_uses_research_root_not_literal_r():
    source = Path("C:/tmp/astra-joint-v1/raw/server-frozen")
    assert default_output_dir(source) == Path("C:/tmp/astra-joint-v1/archives-analysis")


def test_daily_partitions_prevent_monthly_fallback_for_missing_day():
    with tempfile.TemporaryDirectory() as td:
        market = Path(td)
        (market / "2026-09.parquet").write_bytes(b"monthly")
        (market / "2026-09-10.parquet").write_bytes(b"daily")
        files = choose_market_files(market, BASE_MS, BASE_MS + 36 * 60 * 60 * 1000)
        assert files == [market / "2026-09-10.parquet"]


def test_nr_interface_projects_rebuildable_long_window_features_from_closed_bars():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        facts = root / "facts"
        card_ms = BASE_MS + 1440 * 60_000
        market_rows = [kline(BASE_MS + i * 60_000, 80_000.0 + i) for i in range(1440)]
        # This bar starts at the card minute and closes after the card. It must
        # not leak into a card-time feature snapshot.
        market_rows.append(kline(card_ms, 90_000.0))
        write_jsonl_gz(facts / "um" / "2026-09-10.jsonl.gz", market_rows)
        write_json(facts / "manifest.json", [{"path": "facts/um/2026-09-10.jsonl.gz", "rows": len(market_rows)}])
        observations = [
            {
                "identity": {
                    "record_kind": "native_nr_event",
                    "confirmed_time_ms": card_ms,
                }
            }
        ]

        rebuilt, summary = rebuild_um_features(observations, facts)

        features = rebuilt[0]["um_rebuilt_features"]
        assert summary["status"] == "available"
        assert features["window_status"]["240"] == "available"
        assert features["window_status"]["720"] == "available"
        assert features["window_status"]["1440"] == "available"
        for key in ("vol_240", "vol_720", "vol_1440", "net_flow_240"):
            assert features[key] is not None
        assert features["feature_as_of_ms"] == card_ms
        assert features["last_closed_time_ms"] == card_ms - 1
        assert features["last_closed_price"] < 90_000


def _sha(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


if __name__ == "__main__":
    test_archive_audit_extracts_nr_interface_and_respects_closed_bar_time()
    test_next_bjt_16_expiry_uses_next_day_after_16_bjt()
    test_default_output_dir_uses_research_root_not_literal_r()
    test_daily_partitions_prevent_monthly_fallback_for_missing_day()
    test_nr_interface_projects_rebuildable_long_window_features_from_closed_bars()
    print("astra_joint_archive_audit: PASS")
