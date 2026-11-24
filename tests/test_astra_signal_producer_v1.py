"""Step 6a tests for tools/astra_signal_producer_v1.py: parity & fault recovery."""

from __future__ import annotations

import builtins
import copy
import datetime as dt
import importlib.util
import json
import math
from pathlib import Path
from typing import Any

import pytest

from tools import astra_deribit_cooldown_v1 as deribit_cooldown
from tools import astra_signal_producer_v1 as sp


ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "demo" / "\u6700\u65b0\u4ea4\u4ed8\u7269" / "neutral_regulation_demo_fmz.py"


def _load_isolated_core(tag: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"test_nrd_core_{tag}", CORE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _make_klines(end_ms: int, count: int, *, price: float = 90_000.0, vol: float = 12.0, buy_vol: float = 7.0, interval_ms: int = 60_000) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for i in range(count):
        open_ts = end_ms - (count - i) * interval_ms
        close_ts = open_ts + interval_ms - 1
        drift = (i % 7 - 3) * 5.0
        o = price + drift
        c = price + drift + (4.0 if buy_vol >= vol / 2 else -12.0)
        rows.append([
            open_ts,
            f"{o:.2f}",
            f"{max(o, c) + 15.0:.2f}",
            f"{min(o, c) - 15.0:.2f}",
            f"{c:.2f}",
            f"{vol:.4f}",
            close_ts,
            f"{vol * c:.2f}",
            120,
            f"{buy_vol:.4f}",
            f"{buy_vol * c:.2f}",
            "0",
        ])
    return rows


def _make_agg_trades(start_id: int, ts_ms: int, count: int, *, price: float = 90_000.0, qty: float = 1.5, buyer_is_maker: bool = False) -> list[dict[str, Any]]:
    return [
        {
            "a": start_id + i,
            "p": f"{price + (i * 0.5):.2f}",
            "q": f"{qty:.4f}",
            "T": ts_ms - (count - i) * 500,
            "m": buyer_is_maker,
        }
        for i in range(count)
    ]


def _make_options_universe(now_ms: int, spot: float) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    exp24 = now_ms + 24 * 3600 * 1000
    exp48 = now_ms + 48 * 3600 * 1000
    exp_labels = [(exp24, "16OCT26"), (exp48, "17OCT26")]
    instruments: list[dict[str, Any]] = []
    tickers: dict[str, dict[str, Any]] = {}
    for exp_ts, tag in exp_labels:
        specs = [
            (round(spot * 0.94 / 1000) * 1000, "put", -0.25, 58.0, 620.0),
            (round(spot / 1000) * 1000, "call", 0.50, 50.0, 800.0),
            (round(spot / 1000) * 1000, "put", -0.50, 51.0, 750.0),
            (round(spot * 1.06 / 1000) * 1000, "call", 0.25, 52.0, 590.0),
        ]
        for strike, opt_type, delta, iv, oi in specs:
            suffix = "C" if opt_type == "call" else "P"
            name = f"BTC-{tag}-{int(strike)}-{suffix}"
            instruments.append({
                "instrument_name": name,
                "kind": "option",
                "quote_currency": "BTC",
                "base_currency": "BTC",
                "settlement_period": "day",
                "option_type": opt_type,
                "strike": float(strike),
                "expiration_timestamp": exp_ts,
                "creation_timestamp": now_ms - 86_400_000,
                "is_active": True,
                "state": "open",
            })
            tickers[name] = {
                "instrument_name": name,
                "mark_iv": iv,
                "underlying_price": spot,
                "index_price": spot,
                "open_interest": oi,
                "state": "open",
                "greeks": {"delta": delta, "gamma": 0.00012},
                "timestamp": now_ms - 5_000,
            }
    return instruments, tickers


class _MockTransport:
    def __init__(self, core_mod: Any, scenario: dict[str, Any]):
        self.core = core_mod
        self.scenario = scenario

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        timeout_sec: float | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        now = int(self.scenario["clock_ms"])
        price = float(self.scenario["price"])
        params = params or {}

        if "/api/v3/klines" in url:
            limit = int(params.get("limit", 720))
            data = _make_klines(
                now - 60_000,
                limit,
                price=price,
                vol=self.scenario["spot_kline_vol"],
                buy_vol=self.scenario["spot_kline_buy_vol"],
            )
            return self._ok(data)

        if "/api/v3/aggTrades" in url:
            tid_base = int(self.scenario["spot_trade_base_id"])
            data = _make_agg_trades(
                tid_base,
                now - 1_000,
                16,
                price=price,
                qty=self.scenario["spot_trade_qty"],
                buyer_is_maker=self.scenario["spot_buyer_is_maker"],
            )
            return self._ok(data)

        if "/api/v3/depth" in url:
            return self._ok({
                "lastUpdateId": 1001,
                "bids": [[f"{price - 5:.2f}", "8.0"], [f"{price - 25:.2f}", "12.0"]],
                "asks": [[f"{price + 5:.2f}", "8.0"], [f"{price + 25:.2f}", "12.0"]],
            })

        if "/fapi/v1/aggTrades" in url:
            tid_base = int(self.scenario["fut_trade_base_id"])
            data = _make_agg_trades(
                tid_base,
                now - 1_000,
                16,
                price=price + 10.0,
                qty=self.scenario["fut_trade_qty"],
                buyer_is_maker=self.scenario["fut_buyer_is_maker"],
            )
            return self._ok(data)

        if "/fapi/v1/premiumIndex" in url:
            return self._ok({
                "symbol": "BTCUSDT",
                "markPrice": f"{price + 12.0:.2f}",
                "indexPrice": f"{price:.2f}",
                "lastFundingRate": "0.00012",
                "time": now - 1_000,
            })

        if "/fapi/v1/klines" in url:
            interval = str(params.get("interval", "15m"))
            step = 15 * 60_000 if interval == "15m" else 5 * 60_000
            limit = int(params.get("limit", 48))
            data = _make_klines(
                now - step,
                limit,
                price=price + 10.0,
                vol=self.scenario["fut_kline_vol"],
                buy_vol=self.scenario["fut_kline_buy_vol"],
                interval_ms=step,
            )
            return self._ok(data)

        if "/fapi/v1/fundingRate" in url:
            limit = int(params.get("limit", 24))
            rows = [
                {
                    "symbol": "BTCUSDT",
                    "fundingRate": "0.00010",
                    "fundingTime": now - (limit - i) * 8 * 3600 * 1000,
                }
                for i in range(limit)
            ]
            return self._ok(rows)

        if "/v1/info" in url:
            if self.scenario.get("gex_info_down"):
                return self._err("gex_info_down")
            return self._ok({
                "asset": "BTC",
                "quality": "OK",
                "stale": False,
                "data_state": "live",
                "availability": "ready",
                "total_net_gex": self.scenario["total_net_gex"],
                "market_state": self.scenario["gex_market_state"],
                "gex_board": {
                    "total_net_gex": self.scenario["total_net_gex"],
                    "dvol": 45.0,
                    "spot": price,
                },
                "gex_time_semantics": {
                    "schema_version": "gex_time_semantics@1.0.0",
                    "fields": {
                        "gex_board.total_net_gex": {
                            "source_ref": "gex-latest.total_gex",
                            "time_basis": "source_observation_time",
                            "observed_at_ms": now - 30_000,
                            "time_errors": [],
                        }
                    },
                },
            })

        if "gexmonitor.com" in url or "gex" in url:
            if self.scenario.get("gex_down"):
                return self._err("gex_down")
            return self._ok({
                "flip_point": self.scenario["flip_point"],
                "spot": price,
                "spring": self.scenario["spring"],
                "timestamp": now - 30_000,
            })

        instruments, tickers = _make_options_universe(now, price)
        if "public/get_instruments" in url:
            return self._ok({"jsonrpc": "2.0", "result": instruments})
        if "public/get_index_price" in url:
            return self._ok({"jsonrpc": "2.0", "result": {"index_price": price}})
        if "public/ticker" in url:
            inst = str(params.get("instrument_name", ""))
            return self._ok({"jsonrpc": "2.0", "result": tickers[inst]})

        if "finance.yahoo.com" in url:
            ts_list = [int((now - (10 - i) * 86_400_000) // 1000) for i in range(10)]
            closes = [100.0 + i * 0.2 for i in range(10)]
            return self._ok({
                "chart": {
                    "result": [
                        {
                            "timestamp": ts_list,
                            "indicators": {"quote": [{"close": closes}]},
                        }
                    ]
                }
            })

        raise AssertionError(f"unexpected URL in mock transport: {url}")

    def _ok(self, data: Any) -> dict[str, Any]:
        return {
            "quality": self.core.QUALITY_OK,
            "data": data,
            "error": None,
            "fetched_at_ms": int(self.scenario["clock_ms"]),
        }

    def _err(self, msg: str) -> dict[str, Any]:
        return {
            "quality": self.core.QUALITY_ERROR,
            "data": None,
            "error": msg,
            "fetched_at_ms": int(self.scenario["clock_ms"]),
        }


def _assert_numeric_parity(a: Any, b: Any, *, path: str = "root", ignore_keys: set[str] | None = None) -> None:
    skip = ignore_keys or set()
    if isinstance(a, dict) and isinstance(b, dict):
        keys_a = set(a.keys()) - skip
        keys_b = set(b.keys()) - skip
        assert keys_a == keys_b, f"key mismatch at {path}: {keys_a ^ keys_b}"
        for key in sorted(keys_a):
            _assert_numeric_parity(a[key], b[key], path=f"{path}.{key}", ignore_keys=skip)
        return
    if isinstance(a, list) and isinstance(b, list):
        assert len(a) == len(b), f"len mismatch at {path}: {len(a)} != {len(b)}"
        for idx, (xa, xb) in enumerate(zip(a, b)):
            _assert_numeric_parity(xa, xb, path=f"{path}[{idx}]", ignore_keys=skip)
        return
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return
        assert abs(a - b) <= 1e-9, f"float mismatch at {path}: {a} != {b}"
        return
    assert a == b, f"value mismatch at {path}: {a!r} != {b!r}"


def test_deterministic_parity_backfill_spacegate_nr_and_fixed_round(tmp_path: Path):
    base_ts = int(dt.datetime(2026, 10, 15, 14, 55, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
    fixed_round_ts = int(dt.datetime(2026, 10, 15, 15, 1, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)  # 23:01 BJT

    scenario: dict[str, Any] = {
        "clock_ms": base_ts,
        "price": 90_000.0,
        "flip_point": 89_800.0,
        "spring": 200.0,
        "total_net_gex": 250_000_000.0,
        "gex_market_state": "positive_gamma",
        "spot_kline_vol": 14.0,
        "spot_kline_buy_vol": 9.0,
        "fut_kline_vol": 20.0,
        "fut_kline_buy_vol": 13.0,
        "spot_trade_base_id": 1_000,
        "fut_trade_base_id": 2_000,
        "spot_trade_qty": 1.2,
        "fut_trade_qty": 1.5,
        "spot_buyer_is_maker": False,
        "fut_buyer_is_maker": False,
    }

    demo_core = _load_isolated_core("demo_parity")
    native_core = _load_isolated_core("native_parity")
    demo_core.now_ms = lambda: int(scenario["clock_ms"])
    native_core.now_ms = lambda: int(scenario["clock_ms"])

    demo_logs = tmp_path / "demo_logs"
    demo_logs.mkdir()
    demo_cfg = dict(demo_core.CONFIG)
    demo_cfg.update({
        "read_only_demo": True,
        "log_status_enabled": False,
        "chart_enabled": False,
        "chart_reset_on_start": False,
        "signal_review_push_enabled": False,
        "signal_review_push_test": False,
        "decision_snapshot_diagnostic_log_enabled": False,
        "logs_dir": str(demo_logs),
        "data_export_snapshot_file": str(demo_logs / "snapshots.jsonl"),
        "space_gate_state_file": str(demo_logs / "space_gate_state.json"),
        "fixed_analysis_round_state_file": str(demo_logs / "fixed_analysis_round_state.json"),
        "gex_info_cache_file": str(demo_logs / "gex_info_cache.json"),
        "macro_cache_file": str(demo_logs / "macro_factor_cache.json"),
        "gex_info_base_url": "http://127.0.0.1:8000",
    })
    demo_rt = demo_core.DemoRuntime(demo_cfg)
    demo_rt.http.get_json = _MockTransport(demo_core, scenario).get_json

    native_logs = tmp_path / "native_logs"
    native_state = tmp_path / "native_state"
    native_rt = sp.NativeSignalRuntime(
        logs_dir=native_logs,
        state_dir=native_state,
        core_mod=native_core,
        core_sha256=sp.compute_lf_sha256(CORE_PATH),
        overrides={"gex_info_base_url": "http://127.0.0.1:8000"},
    )
    native_rt.runtime.http.get_json = _MockTransport(native_core, scenario).get_json

    # Stage 1: Initial startup + 1m kline backfill
    d1, _ = demo_rt.tick(live_fetch=True)
    n1, _ = native_rt.tick(live_fetch=True)
    assert len(demo_rt.bars.completed_bars) == len(native_rt.runtime.bars.completed_bars) > 0
    assert d1["factor_snapshot"]["space_gate"]["state"] == n1["factor_snapshot"]["space_gate"]["state"]

    # Stage 2: Steady state inside 5m snapshot cadence -> no extra snapshot emitted
    scenario["clock_ms"] = base_ts + 60_000
    scenario["spot_trade_base_id"] += 50
    scenario["fut_trade_base_id"] += 50
    d2, _ = demo_rt.tick(live_fetch=True)
    n2, _ = native_rt.tick(live_fetch=True)
    assert len(_read_jsonl(demo_logs / "snapshots.jsonl")) == 1
    assert len(_read_jsonl(native_logs / "snapshots.jsonl")) == 1

    # Stage 3: SpaceGate transition (block gate by going far from flip + stale/negative net_gex change)
    scenario["clock_ms"] = base_ts + 120_000
    scenario["total_net_gex"] = -350_000_000.0
    scenario["gex_market_state"] = "negative_gamma"
    scenario["price"] = 84_000.0
    scenario["flip_point"] = 95_000.0
    scenario["spot_trade_base_id"] += 50
    scenario["fut_trade_base_id"] += 50
    d3, _ = demo_rt.tick(live_fetch=True)
    n3, _ = native_rt.tick(live_fetch=True)
    assert d3["factor_snapshot"]["space_gate"]["state"] == n3["factor_snapshot"]["space_gate"]["state"]

    # Stage 4: Neutral Repair progression comparison with heavy spot sell shock -> recovery
    scenario["clock_ms"] = base_ts + 180_000
    scenario["spot_buyer_is_maker"] = True
    scenario["fut_buyer_is_maker"] = True
    scenario["spot_trade_qty"] = 9.0
    scenario["fut_trade_qty"] = 10.0
    scenario["price"] = 83_200.0
    scenario["spot_trade_base_id"] += 50
    scenario["fut_trade_base_id"] += 50
    d4, _ = demo_rt.tick(live_fetch=True)
    n4, _ = native_rt.tick(live_fetch=True)
    _assert_numeric_parity(
        d4["factor_snapshot"]["neutral_repair_signal"],
        n4["factor_snapshot"]["neutral_repair_signal"],
        path="neutral_repair_signal",
    )

    # Also verify exact Neutral Repair state machine progression on identical shock + stabilization frames
    nr_demo = demo_rt.neutral_repair_tracker.update(
        {
            "dir": -1,
            "strength": 0.9,
            "price_chg": -0.025,
            "spread": 0.002,
            "cvd_fast": -250.0,
            "data_ready": True,
        },
        {
            "state": demo_core.STATE_VALID,
            "facts": {
                "anchor_distance_pct": 0.045,
                "anchor_gravity_ref_score": 45.0,
                "anchor_price": 87_000.0,
            },
        },
        {"current_price": 83_200.0},
    )
    nr_native = native_rt.runtime.neutral_repair_tracker.update(
        {
            "dir": -1,
            "strength": 0.9,
            "price_chg": -0.025,
            "spread": 0.002,
            "cvd_fast": -250.0,
            "data_ready": True,
        },
        {
            "state": native_core.STATE_VALID,
            "facts": {
                "anchor_distance_pct": 0.045,
                "anchor_gravity_ref_score": 45.0,
                "anchor_price": 87_000.0,
            },
        },
        {"current_price": 83_200.0},
    )
    _assert_numeric_parity(nr_demo, nr_native, path="nr_tracker")

    # Stage 5: 23:00 UTC+8 Fixed Analysis Round (15:01 UTC)
    scenario["clock_ms"] = fixed_round_ts
    scenario["spot_trade_base_id"] += 50
    scenario["fut_trade_base_id"] += 50
    d5, _ = demo_rt.tick(live_fetch=True)
    n5, _ = native_rt.tick(live_fetch=True)

    # Compare full decision payloads ignoring server-native identity metadata, file paths, & wall clocks
    ignore = {
        "producer_platform",
        "producer_service",
        "fmz_platform_used",
        "core_source_sha256",
        "producer_identity",
        "snapshot_exported_ms",
        "ingested_at_ms",
        "source_available_at_ms",
        "snapshot_file",
    }
    _assert_numeric_parity(d5, n5, path="decision_fixed_round", ignore_keys=ignore)

    # Compare exported snapshots.jsonl
    demo_snaps = _read_jsonl(demo_logs / "snapshots.jsonl")
    native_snaps = _read_jsonl(native_logs / "snapshots.jsonl")
    assert len(demo_snaps) == len(native_snaps) >= 2
    for ds, ns in zip(demo_snaps, native_snaps):
        assert len(ns["clock_provenance"]) == 7
        assert ds["clock_provenance"] == ns["clock_provenance"]
        assert ns["producer_identity"]["fmz_platform_used"] is False
        assert ns["producer_identity"]["platform"] == sp.PRODUCER_PLATFORM
        assert ns["producer_identity"]["service"] == sp.PRODUCER_SERVICE
        assert ns["producer_identity"]["core_source_sha256"] == native_rt.core_source_sha256
        assert ns["producer_identity"]["contract_compat"] == "fmz.evaluation_snapshot.v2"
        assert ns["decision"]["runtime_facts"]["fmz_platform_used"] is False
        assert ns["decision"]["runtime_facts"]["read_only_demo"] is True
        assert ns["current_snapshot_context"]["session_context"]["schema_name"] == "SignalSessionPremiseDurabilityContext"
        assert "clock_window" in ns["current_snapshot_context"]["session_context"]
        assert "validation_basis" in ns["current_snapshot_context"]["session_context"]
        _assert_numeric_parity(ds, ns, path="snapshot", ignore_keys=ignore)

    # Compare signal_review.jsonl fixed analysis round cards & integrity hash
    assert d5["factor_snapshot"]["skew"]["data_state"] == "OK"
    assert d5["factor_snapshot"]["gamma_regime"]["data_state"] == "OK"
    demo_cards = _read_jsonl(demo_logs / "signal_review.jsonl")
    native_cards = _read_jsonl(native_logs / "signal_review.jsonl")
    assert len(demo_cards) == 1 and len(native_cards) == 1
    dc, nc = demo_cards[0], native_cards[0]
    assert nc["analysis_round"]["round_type"] == "US_FIXED_ANALYSIS_ROUND"
    assert "FIXED_ANALYSIS_ROUND" in nc["decision_matrix"]["reason_codes"]
    assert nc["identity"]["strategy_version"] == "2.0.7"
    sess_ctx = nc["signal_window"]["session_context"]
    assert sess_ctx["schema_name"] == "SignalSessionPremiseDurabilityContext"
    assert sess_ctx["schema"] == "SignalSessionPremiseDurabilityContext@1.0.0"
    assert "clock_window" in sess_ctx
    assert "backtest_delta_pp" in sess_ctx
    assert "validation_basis" in sess_ctx
    assert "temporal_durability" in nc["decision_matrix"]
    assert nc["decision_matrix"]["execution_allowed"] is False
    assert nc["provenance"]["fmz_platform_used"] is False
    assert nc["provenance"]["producer_platform"] == sp.PRODUCER_PLATFORM
    assert nc["delivery"]["delivery_channel"] == "server_native_jsonl"

    # Recomputed integrity hash must match record_hash
    card_without_integrity = {k: v for k, v in nc.items() if k != "integrity"}
    assert nc["integrity"] == native_core._audit_integrity(card_without_integrity)

    # Core algorithm content parity on the fixed round card (excluding provenance/delivery/integrity)
    _assert_numeric_parity(
        dc,
        nc,
        path="fixed_round_card",
        ignore_keys={"provenance", "delivery", "integrity"},
    )


def test_read_only_demo_override_rejected(tmp_path: Path):
    core = _load_isolated_core("readonly_guard")
    with pytest.raises(ValueError, match="READ_ONLY_DEMO_REQUIRED"):
        sp.build_native_config(
            core,
            logs_dir=tmp_path / "logs",
            state_dir=tmp_path / "state",
            overrides={"read_only_demo": False},
        )


def test_zero_fmz_globals_required(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    for name in ("Log", "LogStatus", "Sleep", "Chart", "exchange", "_G"):
        monkeypatch.delattr(builtins, name, raising=False)
    runner = sp.NativeSignalRuntime(
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        overrides={"live_fetch_enabled": False, "offline_fixture_enabled": True},
    )
    assert runner.run(live_fetch=False, max_loops=1) == 0


def test_checkpoint_restore_windows_and_no_duplicate_fixed_round(tmp_path: Path):
    fixed_ts = int(dt.datetime(2026, 10, 15, 15, 1, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
    scenario: dict[str, Any] = {
        "clock_ms": fixed_ts,
        "price": 90_000.0,
        "flip_point": 89_800.0,
        "spring": 200.0,
        "total_net_gex": 250_000_000.0,
        "gex_market_state": "positive_gamma",
        "spot_kline_vol": 14.0,
        "spot_kline_buy_vol": 9.0,
        "fut_kline_vol": 20.0,
        "fut_kline_buy_vol": 13.0,
        "spot_trade_base_id": 10_000,
        "fut_trade_base_id": 20_000,
        "spot_trade_qty": 1.2,
        "fut_trade_qty": 1.5,
        "spot_buyer_is_maker": False,
        "fut_buyer_is_maker": False,
    }
    logs_dir = tmp_path / "logs"
    state_dir = tmp_path / "state"

    core1 = _load_isolated_core("ckpt1")
    core1.now_ms = lambda: int(scenario["clock_ms"])
    r1 = sp.NativeSignalRuntime(logs_dir=logs_dir, state_dir=state_dir, core_mod=core1, core_sha256="sha1")
    r1.runtime.http.get_json = _MockTransport(core1, scenario).get_json
    r1.tick(live_fetch=True)
    # Seed tracking histories & NR state to verify checkpoint fidelity
    r1.runtime.rr_hist.extend([0.01, -0.02])
    r1.runtime.cvd_hist["4h"].append(12.5)
    r1.runtime.neutral_repair_tracker.context = {"event_start_ms": fixed_ts - 60_000, "state": "OBSERVING"}
    r1.save_checkpoint(now_ms=fixed_ts)
    assert len(_read_jsonl(logs_dir / "signal_review.jsonl")) == 1
    orig_gex_ingest_ms = r1.runtime.gex_state.last_raw_ingest_ms

    # Case 1: Short restart (60s later, <= 90s) -> restores bars_tail, histories, NR, GEX + no duplicate fixed round
    scenario["clock_ms"] = fixed_ts + 60_000
    core2 = _load_isolated_core("ckpt2")
    core2.now_ms = lambda: int(scenario["clock_ms"])
    r2 = sp.NativeSignalRuntime(logs_dir=logs_dir, state_dir=state_dir, core_mod=core2, core_sha256="sha1")
    assert r2.checkpoint_restored["restored"] is True
    assert r2.checkpoint_restored["bars_restored"] is True
    assert list(r2.runtime.rr_hist)[-2:] == [0.01, -0.02]
    assert list(r2.runtime.cvd_hist["4h"])[-1] == pytest.approx(12.5)
    assert r2.runtime.neutral_repair_tracker.context["state"] == "OBSERVING"
    assert r2.runtime.gex_state.last_raw_ingest_ms == orig_gex_ingest_ms
    r2.runtime.http.get_json = _MockTransport(core2, scenario).get_json
    r2.tick(live_fetch=True)
    assert len(_read_jsonl(logs_dir / "signal_review.jsonl")) == 1

    # Case 2: Medium outage (30m later, > 10m and <= 24h) -> skips bars_tail so 1m backfill runs
    scenario["clock_ms"] = fixed_ts + 30 * 60_000
    core3 = _load_isolated_core("ckpt3")
    core3.now_ms = lambda: int(scenario["clock_ms"])
    r3 = sp.NativeSignalRuntime(logs_dir=logs_dir, state_dir=state_dir, core_mod=core3, core_sha256="sha1")
    assert r3.checkpoint_restored["restored"] is True
    assert r3.checkpoint_restored["bars_restored"] is False
    assert len(r3.runtime.bars.completed_bars) == 0

    # Case 3: Expired checkpoint (> 24h) -> still restores dedup sets if same-day slot passed back
    ckpt = json.loads((state_dir / "runtime_checkpoint.json").read_text(encoding="utf-8"))
    ckpt["saved_at_ms"] = fixed_ts
    (state_dir / "runtime_checkpoint.json").write_text(json.dumps(ckpt), encoding="utf-8")
    core4 = _load_isolated_core("ckpt4")
    core4.now_ms = lambda: fixed_ts + 25 * 3600 * 1000
    r4 = sp.NativeSignalRuntime(logs_dir=logs_dir, state_dir=state_dir, core_mod=core4, core_sha256="sha1")
    assert r4.checkpoint_restored["restored"] is False
    assert r4.checkpoint_restored["reason"] == "checkpoint_expired"
    assert r4.checkpoint_restored["dedup_restored"] is True
    assert len(r4.runtime.signal_events.seen_fixed_round_slots) >= 1


def test_shutdown_health_string_spacegate_and_atomic_caches(tmp_path: Path):
    logs_dir = tmp_path / "logs"
    state_dir = tmp_path / "state"
    runner = sp.NativeSignalRuntime(
        logs_dir=logs_dir,
        state_dir=state_dir,
        overrides={
            "live_fetch_enabled": False,
            "offline_fixture_enabled": True,
            "loop_sleep_ms": 100,
        },
    )
    runner.tick(live_fetch=False)
    live_check = sp.check_health_file(state_dir, max_age_ms=60_000)
    assert live_check["ok"] is True
    assert live_check["state"] == "running"
    assert live_check["fmz_platform_used"] is False
    assert live_check["read_only_demo"] is True

    # String space_gate.state must survive health write when decision_snapshot=None
    runner.runtime.space_gate.state = "STRUCTURE_BLOCKED"
    runner.write_health(state="degraded", decision_snapshot=None)
    health_data = json.loads((state_dir / "producer_health.json").read_text(encoding="utf-8"))
    assert health_data["space_gate_state"] == "STRUCTURE_BLOCKED"
    assert sp.check_health_file(state_dir, max_age_ms=60_000)["ok"] is True

    # Stale health file rejected
    assert sp.check_health_file(state_dir, max_age_ms=60_000, now_ms=health_data["updated_at_ms"] + 120_000)["ok"] is False

    # Atomic cache hooks leave no .tmp leftovers
    assert runner.runtime.gex_info._write_cache({"total_net_gex": 123.0}) is True
    assert (logs_dir / "gex_info_cache.json").is_file()
    assert runner.runtime.macro_factor._write_cache({"DXY": {"value": 104.0}}) is True
    assert (logs_dir / "macro_factor_cache.json").is_file()
    assert not list(logs_dir.glob("*.tmp-*"))

    # Clean exit writes state="stopped"; check_health_file rejects stopped health
    runner.stop_event.set()
    assert runner.run(live_fetch=False) == 0
    stopped_health = sp.check_health_file(state_dir, max_age_ms=60_000)
    assert stopped_health["ok"] is False
    assert stopped_health["state"] == "stopped"


def test_log_hygiene_repairs_newline_and_quarantines_corrupt_snapshots(tmp_path: Path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)
    review_path = logs_dir / "signal_review.jsonl"
    review_path.write_bytes(b'{"identity":{"card_id":"c1"}}')
    snap_path = logs_dir / "snapshots.jsonl"
    snap_path.write_text("not-json-at-all\n{also-corrupt\n", encoding="utf-8")

    rep = sp.ensure_log_hygiene(logs_dir, now_ms=1_760_000_000_000)
    assert rep["signal_review_newline_repaired"] is True
    assert review_path.read_bytes().endswith(b"\n")
    assert len(_read_jsonl(review_path)) == 1
    assert rep["snapshots_quarantined"] is not None
    assert not snap_path.exists()
    assert Path(rep["snapshots_quarantined"]).is_file()


def test_upstream_outage_resilience_and_deribit_429_shared_cooldown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cooldown_file = tmp_path / "deribit_cooldown.json"
    monkeypatch.setenv(deribit_cooldown.ENV_KEY, str(cooldown_file))

    runner = sp.NativeSignalRuntime(
        logs_dir=tmp_path / "logs",
        state_dir=tmp_path / "state",
        force_deribit_pacing=True,
        overrides={
            "native_deribit_ticker_interval_ms": 20,
            "native_deribit_429_cooldown_sec": 45,
            "loop_sleep_ms": 100,
        },
    )

    calls: list[str] = []

    def _flaky_http(url: str, params=None, headers=None, timeout_sec=None, **_kwargs: Any):
        calls.append(url)
        if "public/ticker" in url:
            return {
                "quality": runner.core_mod.QUALITY_ERROR,
                "data": None,
                "error": "HTTP 429 too_many_requests",
            }
        return {
            "quality": runner.core_mod.QUALITY_ERROR,
            "data": None,
            "error": "upstream_timeout",
        }

    runner.runtime.http.get_json = _flaky_http
    # Should survive Binance/GEX failures without raising
    assert runner.run(live_fetch=True, max_loops=1) == 0

    # Now verify HTTP 429 from public/ticker trips both local & shared cooldown and blocks immediate next call
    res1 = runner.runtime.deribit.public_get("public/ticker", {"instrument_name": "BTC-16OCT26-90000-C"})
    assert "429" in str(res1.get("error", ""))
    assert deribit_cooldown.remaining_ms(cooldown_file) > 0

    prev_calls = len(calls)
    res2 = runner.runtime.deribit.public_get("public/ticker", {"instrument_name": "BTC-16OCT26-90000-P"})
    assert res2["error"] == "deribit_429_cooldown_active"
    assert len(calls) == prev_calls  # short-circuited without hitting HTTP
