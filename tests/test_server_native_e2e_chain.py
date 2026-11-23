"""Step 6b E2E tests: NativeSignalRuntime -> fact_context -> DataEngine -> MAP -> Workbench -> Archive -> Player -> Materializer -> Cooldown."""

from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import gzip
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from typing import Any
import urllib.error
import urllib.request

import pytest

from tools import astra_anchor_relative_v1 as ard
from tools import astra_candidate_asof_v1 as asof_packets
from tools import astra_data_engine_v23 as data_engine
from tools import astra_data_projection_v23 as dp
from tools import astra_deribit_cooldown_v1 as deribit_cooldown
from tools import astra_map_data_server_v24 as map_server
from tools import astra_map_projection_v24 as map_projection
from tools import astra_map_sources_v24 as map_sources
from tools import astra_player_dossier_v1 as dossier
from tools import astra_player_tracking_v13 as tracking
from tools import astra_research_archive_v1 as archive
from tools import astra_signal_producer_v1 as sp
from tools import astra_underwriting_discovery_v20 as discovery
from tools import astra_underwriting_fact_context_v20 as facts
from tools import astra_underwriting_public_quote_v20 as public_quote
from tools import astra_workbench_v20 as workbench
from tools import astra_wpem_native_v1 as wpem_native
from tools import materialize_signal_cards as materializer


ROOT = Path(__file__).resolve().parents[1]
PLAN_TEMPLATE_PATH = ROOT / "deploy" / "player_dossier" / "plan.daily_r16.v2.json"
HOUR = 3_600_000
MIN = 60_000
DAY = 24 * HOUR


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _make_klines(end_ms: int, count: int, *, price: float, interval_ms: int = 60_000) -> list[list[Any]]:
    rows = []
    for i in range(count):
        open_ts = end_ms - (count - i) * interval_ms
        close_ts = open_ts + interval_ms - 1
        drift = ((i % 5) - 2) * 3.0
        o = price + drift
        c = price + drift + 2.0
        rows.append([
            open_ts,
            f"{o:.2f}",
            f"{max(o, c) + 8.0:.2f}",
            f"{min(o, c) - 8.0:.2f}",
            f"{c:.2f}",
            "12.0000",
            close_ts,
            f"{12.0 * c:.2f}",
            100,
            "6.5000",
            f"{6.5 * c:.2f}",
            "0",
        ])
    return rows


def _make_agg_trades(start_id: int, ts_ms: int, count: int, *, price: float) -> list[dict[str, Any]]:
    return [
        {
            "a": start_id + i,
            "p": f"{price + (i % 3) * 0.5:.2f}",
            "q": "1.2500",
            "T": ts_ms - (count - i) * 500,
            "m": False,
        }
        for i in range(count)
    ]


def _make_options_universe(now_ms: int, expiry_ms: int, spot: float) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    exp48 = expiry_ms + DAY
    instruments: list[dict[str, Any]] = []
    tickers: dict[str, dict[str, Any]] = {}
    for exp_ts, tag in ((expiry_ms, "16OCT26"), (exp48, "17OCT26")):
        for side, suffix, strikes in (
            ("put", "P", (95_000.0, 97_000.0)),
            ("call", "C", (103_000.0, 105_000.0)),
        ):
            for idx, strike in enumerate(strikes):
                name = f"BTC-{tag}-{int(strike)}-{suffix}"
                delta = (-0.25 if idx == 0 else -0.35) if side == "put" else (0.35 if idx == 0 else 0.25)
                instruments.append({
                    "instrument_name": name,
                    "kind": "option",
                    "quote_currency": "BTC",
                    "base_currency": "BTC",
                    "settlement_currency": "BTC",
                    "counter_currency": "USD",
                    "price_index": "btc_usd",
                    "instrument_type": "reversed",
                    "settlement_period": "day",
                    "option_type": side,
                    "strike": float(strike),
                    "expiration_timestamp": exp_ts,
                    "creation_timestamp": now_ms - DAY,
                    "contract_size": 1,
                    "min_trade_amount": 0.1,
                    "taker_commission": 0.0003,
                    "is_active": True,
                    "state": "open",
                    "greeks": {"delta": delta, "gamma": 0.00012},
                    "delta_observed_at_ms": now_ms - 1_000,
                })
                tickers[name] = {
                    "instrument_name": name,
                    "mark_iv": 52.0 if side == "put" else 48.0,
                    "underlying_price": spot,
                    "index_price": spot,
                    "open_interest": 700.0,
                    "state": "open",
                    "greeks": {"delta": delta, "gamma": 0.00012},
                    "timestamp": now_ms - 1_000,
                }
        # Also include ATM strikes so SRD/GGR compute cleanly
        for side, suffix, delta in (("call", "C", 0.50), ("put", "P", -0.50)):
            atm_name = f"BTC-{tag}-100000-{suffix}"
            instruments.append({
                "instrument_name": atm_name,
                "kind": "option",
                "quote_currency": "BTC",
                "base_currency": "BTC",
                "settlement_currency": "BTC",
                "counter_currency": "USD",
                "price_index": "btc_usd",
                "instrument_type": "reversed",
                "settlement_period": "day",
                "option_type": side,
                "strike": 100_000.0,
                "expiration_timestamp": exp_ts,
                "creation_timestamp": now_ms - DAY,
                "contract_size": 1,
                "min_trade_amount": 0.1,
                "taker_commission": 0.0003,
                "is_active": True,
                "state": "open",
                "greeks": {"delta": delta, "gamma": 0.00015},
                "delta_observed_at_ms": now_ms - 1_000,
            })
            tickers[atm_name] = {
                "instrument_name": atm_name,
                "mark_iv": 50.0,
                "underlying_price": spot,
                "index_price": spot,
                "open_interest": 900.0,
                "state": "open",
                "greeks": {"delta": delta, "gamma": 0.00015},
                "timestamp": now_ms - 1_000,
            }
    return instruments, tickers


def _run_native_ticks(logs_dir: Path, state_dir: Path, expiry_ms: int, clock_box: list[int], spot_box: list[float]) -> sp.NativeSignalRuntime:
    core = sp.load_signal_core(unique_module=True)[0]
    core.now_ms = lambda: int(clock_box[0])
    trade_seq = [100_000]

    runner = sp.NativeSignalRuntime(
        logs_dir=logs_dir,
        state_dir=state_dir,
        core_mod=core,
        core_sha256=sp.compute_lf_sha256(ROOT / "demo" / "\u6700\u65b0\u4ea4\u4ed8\u7269" / "neutral_regulation_demo_fmz.py"),
        overrides={"gex_info_base_url": "http://127.0.0.1:8000"},
    )

    def mock_http(url: str, params: dict[str, Any] | None = None, **_kw: Any) -> dict[str, Any]:
        now = int(clock_box[0])
        price = float(spot_box[0])
        params = params or {}
        trade_seq[0] += 50

        def ok(data: Any) -> dict[str, Any]:
            return {"quality": core.QUALITY_OK, "data": data, "error": None, "fetched_at_ms": now}

        if "/api/v3/klines" in url:
            return ok(_make_klines(now - MIN, int(params.get("limit", 720)), price=price))
        if "/api/v3/aggTrades" in url or "/fapi/v1/aggTrades" in url:
            return ok(_make_agg_trades(trade_seq[0], now - 1_000, 12, price=price))
        if "/api/v3/depth" in url:
            return ok({"lastUpdateId": 1, "bids": [[f"{price - 5:.2f}", "10.0"]], "asks": [[f"{price + 5:.2f}", "10.0"]]})
        if "/fapi/v1/premiumIndex" in url:
            return ok({"symbol": "BTCUSDT", "markPrice": f"{price + 5:.2f}", "indexPrice": f"{price:.2f}", "lastFundingRate": "0.0001", "time": now - 1_000})
        if "/fapi/v1/klines" in url:
            step = 15 * MIN if params.get("interval") == "15m" else 5 * MIN
            return ok(_make_klines(now - step, int(params.get("limit", 48)), price=price, interval_ms=step))
        if "/fapi/v1/fundingRate" in url:
            return ok([{"symbol": "BTCUSDT", "fundingRate": "0.0001", "fundingTime": now - (i + 1) * 8 * HOUR} for i in range(8)])
        if "/v1/info" in url:
            return ok({
                "asset": "BTC",
                "quality": "OK",
                "stale": False,
                "data_state": "live",
                "availability": "ready",
                "market_state": "positive_gamma",
                "gex_board": {"total_net_gex": 250_000_000.0, "dvol": 45.0, "spot": price},
                "gex_time_semantics": {
                    "schema_version": "gex_time_semantics@1.0.0",
                    "fields": {
                        "gex_board.total_net_gex": {
                            "source_ref": "gex-latest.total_gex",
                            "time_basis": "source_observation_time",
                            "observed_at_ms": now - 20_000,
                            "time_errors": [],
                        }
                    },
                },
            })
        if "gexmonitor.com" in url or "gex" in url:
            return ok({"flip_point": 100_020.0, "spot": price, "spring": 200.0, "timestamp": now - 20_000})
        instruments, tickers = _make_options_universe(now, expiry_ms, price)
        if "public/get_instruments" in url:
            return ok({"jsonrpc": "2.0", "result": instruments})
        if "public/get_index_price" in url:
            return ok({"jsonrpc": "2.0", "result": {"index_price": price}})
        if "public/ticker" in url:
            return ok({"jsonrpc": "2.0", "result": tickers[params["instrument_name"]]})
        if "finance.yahoo.com" in url:
            ts_list = [int((now - (10 - i) * DAY) // 1000) for i in range(10)]
            return ok({"chart": {"result": [{"timestamp": ts_list, "indicators": {"quote": [{"close": [100.0 + i * 0.2 for i in range(10)]}]}}]}})
        raise AssertionError(f"unexpected URL: {url}")

    runner.runtime.http.get_json = mock_http
    # Tick 1 at 15:01Z (23:01 BJT) produces both startup EvaluationSnapshot & FIXED_ANALYSIS_ROUND signal_review card
    runner.tick(live_fetch=True)
    return runner


@contextmanager
def _serve_workbench(wb: workbench.AstraWorkbench):
    server = ThreadingHTTPServer(("127.0.0.1", 0), workbench.make_handler(wb))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def _mock_llm_accept():
    calls = [0]

    def _call(api_key, model, request_body, timeout_s, endpoint=None):
        del api_key, model, request_body, timeout_s, endpoint
        calls[0] += 1
        rule = {
            "historical_facts": "历史事实：两个短腿在前窗采样均未被进入。",
            "accepted_conditions": "继续观察的前提是先在短腿责任外锁定，进入后在检查钟前严格返回。",
            "confirmation_excerpt": "UTC 下一个整分钟首检，随后每 5 分钟检查。严格进入后仍未严格回到责任外时触发。",
            "invalidation": "entry_not_returned",
            "check_interval_ms": 5 * MIN,
            "require_outside_at_t0": True,
        }
        excerpts = {
            "put": "Put：现价在外，先核查 97,000 及 95,000 的短腿路径。",
            "put_counter": "Put 最强反证：97,000 缓冲接近但尚未触发。",
            "call": "Call：现价在外，先核查 103,000 及 105,000 的短腿路径。",
            "call_counter": "Call 最强反证：103,000 可再测试，剩余期限内仍存在入区路径。",
            "comparison": "两侧分别核查，按到期前入区路径和相对余量择边。",
        }
        lines = [
            "Put 侧路径核查：" + excerpts["put"],
            excerpts["put_counter"],
            "Call 侧路径核查：" + excerpts["call"],
            excerpts["call_counter"],
            excerpts["comparison"],
            "### Accept / Call",
            "",
            rule["historical_facts"],
            rule["accepted_conditions"],
            rule["confirmation_excerpt"],
        ]
        ann = {**excerpts, "depends_on_ard": False, "condition_plan": rule}
        content = "\n".join(lines) + "\n\n<ANNOTATION_JSON>\n" + json.dumps(ann, ensure_ascii=False) + "\n</ANNOTATION_JSON>\n"
        return {
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 60},
            "_http_status": 200,
        }

    return calls, _call


def test_full_server_native_downstream_chain_e2e(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    slot_r16 = int(dt.datetime(2026, 10, 15, 16, 0, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
    tick_1501 = int(dt.datetime(2026, 10, 15, 15, 1, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
    expiry = slot_r16 + 16 * HOUR
    exp_day = dossier.utc(expiry).date().isoformat()
    clock_box = [tick_1501]
    spot_box = [100_000.0]

    signal_logs = tmp_path / "signal_native" / "logs"
    signal_state = tmp_path / "signal_native" / "state"
    runner = _run_native_ticks(signal_logs, signal_state, expiry, clock_box, spot_box)

    # Advance to 16:00 UTC (interval export > 5m) and export fresh r16 native snapshot
    clock_box[0] = slot_r16
    runner.tick(live_fetch=True)
    snapshots_jsonl = signal_logs / "snapshots.jsonl"
    review_jsonl = signal_logs / "signal_review.jsonl"
    raw_lines = [ln for ln in snapshots_jsonl.read_bytes().splitlines() if ln.strip()]
    assert len(raw_lines) >= 2
    latest_raw_bytes = raw_lines[-1]
    latest_snap = json.loads(latest_raw_bytes)
    assert latest_snap["producer_identity"]["fmz_platform_used"] is False

    # 1. fact_context + astra_wpem_native_v1 qualification
    fact_res = facts.latest_from_jsonl(snapshots_jsonl, slot_r16 + 2_000)
    assert fact_res["status"] == "current"
    assert fact_res["strategy_version"] == "2.0.6"
    assert fact_res["runtime_mode"] == "live_public_read_only"
    assert fact_res["underlying"] == "BTC"
    assert fact_res["current_price_usd"] > 0
    assert fact_res["market_facts"]["space_gate"]["net_gamma"]["source"] == "gexmonitorapi.gex_board.total_net_gex"
    assert fact_res["market_facts"]["anchor"]["effective_flip_point"] == pytest.approx(100_020.0)

    # Build a WPEM native packet directly from public_quote.capture_candidate + native EvaluationSnapshot bytes
    now_wpem = slot_r16 + 2_000
    import hashlib
    snap_sha = hashlib.sha256(latest_raw_bytes).hexdigest()
    instruments_uni, _tickers_uni = _make_options_universe(now_wpem, expiry, 100_000.0)
    meta_by_name = {row["instrument_name"]: row for row in instruments_uni}
    short_inst = "BTC-16OCT26-103000-C"
    long_inst = "BTC-16OCT26-105000-C"

    def wpem_fetch(path: str, params: dict[str, Any]):
        name = params["instrument_name"]
        if path == "public/get_instrument":
            return dict(meta_by_name[name])
        return {
            "instrument_name": name,
            "state": "open",
            "timestamp": now_wpem - 500,
            "index_price": 100_000.0,
            "bids": [[0.007 if name == short_inst else 0.002, 1.0]],
            "asks": [[0.008 if name == short_inst else 0.003, 1.0]],
        }

    candidate_raw = public_quote.capture_candidate(short_inst, long_inst, 0.1, transport=wpem_fetch, clock_ms=lambda: now_wpem)
    candidate_raw["fmz_fact_context"] = facts.from_evaluation_snapshot(latest_snap, now_wpem, source_hash=snap_sha)
    candidate_raw["data_identity"] = {"kind": "live_public_capture"}
    candidate_raw["metadata_received_ts_ms"] = {"short": now_wpem - 400, "long": now_wpem - 400}
    snap_eval = asof_packets.core.evaluate(candidate_raw)
    manifest = {
        "cutoff_at_ms": now_wpem,
        "candidate_id": snap_eval["candidate_id"],
        "source_records": [{
            "record_id": f"native-{now_wpem}",
            "identity": {"product_id": "fmz.bridge.snapshots.v1"},
            "time": {
                "observation_end_ms": latest_snap["decision"]["ts_ms"],
                "first_seen_at_ms": latest_snap["source_available_at_ms"],
                "retrieved_at_ms": latest_snap["ingested_at_ms"],
                "ingested_at_ms": latest_snap["ingested_at_ms"],
            },
            "version": {"payload_hash": snap_sha},
            "content": {"values": {"source_hash": snap_sha}},
        }],
    }
    manifest["manifest_hash"] = asof_packets.core._hash(manifest)
    candidate_raw["data_identity"]["source_manifest"] = manifest
    snap_eval = asof_packets.core.evaluate(candidate_raw)
    packet = asof_packets.build_packet(snap_eval, raw_payload=candidate_raw)
    wpem_rep = wpem_native.qualify_native(packet, latest_raw_bytes, evidence_kind="native_producer")
    for forbidden_gap in (
        "NATIVE_CLOCK_PROVENANCE_INVALID",
        "NATIVE_SESSION_SCHEMA_MISSING",
        "NATIVE_TEMPORAL_DURABILITY_MISSING",
        "NATIVE_CURRENT_CONTEXT_BINDING_INVALID",
        "NATIVE_COMPAT_OR_SYNTHETIC_REJECTED",
        "NATIVE_PRODUCER_VERSION_MISMATCH",
        "NATIVE_FACT_CONTENT_MISMATCH",
    ):
        assert forbidden_gap not in wpem_rep["gaps"]
    assert wpem_rep["producer_version"] == "2.0.6"

    # 2 & 3. DataEngine + astra_data_projection_v23 + astra_map_data_server_v24
    def fake_bg_sources(engine: data_engine.DataEngine, now_ms: int, *, transport=None, config=None):
        del transport
        rows = []
        for product in config["products"]:
            scope = map_sources._scope_for(product, config)
            engine.ingest(product, {"test": product}, observation_end_ms=now_ms - 5_000, retrieved_at_ms=None, ingested_at_ms=now_ms, scope=scope, data_state="OK")
            rows.append(engine.read(product, now_ms=now_ms, usage="background", scope=scope))
        return rows

    monkeypatch.setattr(map_sources, "collect_sources", fake_bg_sources)
    map_cfg = {
        "state_dir": str(tmp_path / "map_state"),
        "snapshots_jsonl": str(snapshots_jsonl),
        "kpf_manifest_path": str(tmp_path / "absent_kpf.json"),
        "min_free_bytes": 0,
        "sources_enabled": True,
        "index_enabled": False,
    }
    map_res = map_server.collect(map_cfg, now_ms=slot_r16 + 2_000)
    assert map_res["fmz"]["status"] == "current"
    assert map_res["llm_http_calls"] == map_res["trading_calls"] == 0
    qualifier = data_engine.DataEngine(tmp_path / "qualifier_engine")
    proj_engine = map_projection.load_producer_source_engine(
        Path(map_cfg["state_dir"]) / "current_map.json",
        now_ms=map_res["generated_at_ms"],
        qualifier_engine=qualifier,
    )
    assert proj_engine.source_summary(map_res["generated_at_ms"])["status"] == "OK"

    # 4 & 5. AstraWorkbench + Research Archive + ARD + AI Player full simulated lifecycle
    wb_clock = [slot_r16]

    def dyn_discovery(*, now_ms: int):
        insts, _ = _make_options_universe(now_ms, expiry, spot_box[0])
        return discovery.discover(insts, spot_box[0], now_ms, quantity_btc=1.0, width_usd=2000.0)

    def dyn_quote(short_instrument: str, long_instrument: str, quantity_btc: float, *, now_ms: int):
        insts, _ = _make_options_universe(now_ms, expiry, spot_box[0])
        metas = {r["instrument_name"]: r for r in insts}

        def fetch(path: str, params: dict[str, Any]):
            name = params["instrument_name"]
            if path == "public/get_instrument":
                return dict(metas[name])
            return {
                "instrument_name": name,
                "state": "open",
                "timestamp": now_ms,
                "index_price": spot_box[0],
                "bids": [[0.007 if name == short_instrument else 0.002, 1.0]],
                "asks": [[0.008 if name == short_instrument else 0.003, 1.0]],
            }

        return public_quote.capture_candidate(short_instrument, long_instrument, quantity_btc, transport=fetch, clock_ms=lambda: now_ms)

    wb_cfg = workbench.WorkbenchConfig(
        snapshots_jsonl=snapshots_jsonl,
        state_dir=tmp_path / "wb_state",
        frontend_dir=ROOT / "deploy" / "astra_workbench_v3",
        server_token="server-tok",
        api_key="test-key",
        shared_usage_ledger=tmp_path / "wb_shared_budget.json",
        shadow_experiment=True,
        shadow_dossier_root=tmp_path / "shadow_dossiers",
        shadow_service_token="shadow-service-tok",
    )
    wb = workbench.AstraWorkbench(
        wb_cfg,
        discovery_capture=dyn_discovery,
        quote_capture=dyn_quote,
        clock_ms=lambda: wb_clock[0],
    )
    wb._expiry_gamma = {
        "schema": "astra_expiry_gamma_v1@1.0.0",
        "status": "ok",
        "computed_at_ms": slot_r16 - 5_000,
        "expiries": {
            "16OCT26": {
                "expiry": "16OCT26",
                "expiry_ms": expiry,
                "forward_usd": 100_000.0,
                "expected_move_usd": 1400.0,
                "walls": {"pin": 100_000.0, "gamma_call": 103_000.0, "gamma_put": 97_000.0},
                "top": {"oi_put": [{"strike": 97_000.0}], "oi_call": [{"strike": 103_000.0}]},
            }
        },
    }
    view = wb.refresh(now_ms=slot_r16, record_daily=False)
    assert view["market_context"]["status"] == "current"
    assert view["market_context"]["strategy_version"] == "2.0.6"
    assert view["llm_call_count"] == 0
    assert len(view["candidates"]) == 2

    # Archive tick + level cards + ARD using native EvaluationSnapshot as band_snapshot
    arch_root = tmp_path / "research_archive"
    arch = archive.Archive(arch_root)
    points = [[t, 100_000.0] for t in range(slot_r16 - DAY, slot_r16 + MIN + 1, MIN)]

    def fake_arch_deribit(path: str, params: dict[str, Any]):
        if path == "public/get_order_book":
            name = params["instrument_name"]
            short = name.endswith("103000-C") or name.endswith("97000-P")
            return {
                "instrument_name": name,
                "index_price": 100_000.0,
                "underlying_price": 100_000.0,
                "best_bid_price": 0.007 if short else 0.002,
                "best_ask_price": 0.008 if short else 0.003,
                "mark_price": 0.0075 if short else 0.0025,
                "mark_iv": 50.0,
                "greeks": {"delta": 0.35 if "-C" in name else -0.35},
                "timestamp": slot_r16,
            }
        if path == "public/get_index_chart_data":
            return {"timestamps": [p[0] for p in points], "prices": [p[1] for p in points]}
        raise AssertionError(path)

    archive.tick(arch, now_ms=slot_r16, view=view, fetch_case=wb.case_file, deribit=archive.Deribit(fake_arch_deribit))
    (arch_root / "structure").mkdir(parents=True, exist_ok=True)
    (arch_root / "band_snapshots").mkdir(parents=True, exist_ok=True)
    (arch_root / "paths").mkdir(parents=True, exist_ok=True)
    (arch_root / "paths" / f"{exp_day}.json.gz").write_bytes(gzip.compress(json.dumps({"points": points}).encode("utf-8")))
    (arch_root / "band_snapshots" / "latest.jsonl").write_text(latest_raw_bytes.decode("utf-8") + "\n", encoding="utf-8")

    struct_row = {
        "schema": "structure_snapshot@0.1",
        "expiry_ms": expiry,
        "at_ms": slot_r16,
        "available_at_ms": slot_r16,
        "pin_source": "deribit_gex",
        "pin_method": "gamma_peak",
        "pin_status": "ok",
        "profile": {"walls": {"pin": 100_000.0, "gamma_call": 103_000.0, "gamma_put": 97_000.0}},
    }
    arch.append_line(f"structure/{exp_day}.jsonl", struct_row, at_ms=slot_r16, kind="structure")

    ard_res = ard.analyze(
        [struct_row],
        {"index_name": "btc_usd", "expiry_ms": expiry, "points": points[-10:]},
        expiry_ms=expiry,
        cutoff_ms=slot_r16 + MIN,
        window_ms=10 * MIN,
        band_snapshot=latest_snap,
    )
    assert "full" in ard_res

    for case_gz in sorted((arch_root / "cases" / dossier.utc(slot_r16).date().isoformat()).glob("16_*.json.gz")):
        c_obj = json.loads(gzip.decompress(case_gz.read_bytes()).decode("utf-8"))
        c_side = dossier.player.values(c_obj, "L1.contract")["side"]
        card = dossier.levels.build_card(
            expiry_ms=expiry,
            rnd="r16",
            side=c_side,
            read_at_ms=slot_r16,
            walls=dossier.player.values(c_obj, "L3.expiry_walls"),
            points=[pt for pt in points if pt[0] <= slot_r16],
            case=c_obj,
            contract=dossier.player.values(c_obj, "L1.contract"),
            snapshots=[dict(latest_snap, at_ms=latest_snap["snapshot_exported_ms"])],
        )
        arch.append_line(f"levels/{exp_day}.jsonl", card, at_ms=slot_r16, kind="level")

    real_plan = json.loads(PLAN_TEMPLATE_PATH.read_text(encoding="utf-8"))
    assert real_plan["mode"] == "SHADOW_ONLY"
    assert real_plan["execution_allowed"] is False
    plan = dict(real_plan, effective_at_ms=slot_r16)

    contracts_proof = [
        {"instrument_name": item["snapshot"]["contract"][leg], "expiration_timestamp": expiry, "settlement_currency": "BTC", "is_active": True}
        for item in view["candidates"]
        for leg in ("short_instrument", "long_instrument")
    ]
    llm_calls, llm_transport = _mock_llm_accept()
    d = dossier.Dossiers(wb_cfg.shadow_dossier_root, clock=lambda: wb_clock[0])

    with monkeypatch.context() as mp, _serve_workbench(wb) as base_url:
        mp.setattr(dossier, "public", lambda method, **kw: contracts_proof if method == "get_instruments" else {})
        mp.setattr(dossier.archive, "Deribit", lambda *a, **kw: SimpleNamespace(index_path=lambda *x, **y: points))
        svc = tracking.local_service(base_url, "shadow-service-tok")
        pre = tracking.prewarm(
            plan,
            arch_root,
            svc,
            clock=lambda: slot_r16 - 5 * MIN,
            public=lambda method, **kw: contracts_proof,
        )
        assert pre["state"] == "PREWARM"
        assert pre["archive_readable"] is True

        wb_clock[0] = slot_r16 + 2 * MIN
        wb.refresh(now_ms=wb_clock[0], record_daily=False)
        dealt = tracking.auto_deal(
            d,
            plan,
            arch_root,
            svc,
            llm_config={"api_key": "k", "model": "mock-model", "transport": llm_transport},
        )
        assert llm_calls[0] == 1
        assert len(dealt) == 1
        assert dealt[0]["decision"] == "Accept"
        assert dealt[0]["link"].get("state") == "SHADOW_LOCK_CREATED", f"unexpected link: {dealt[0]['link']}"
        assert dealt[0]["link"]["execution_allowed"] is False

        hand = next((wb_cfg.shadow_dossier_root / "dossiers").glob("Deribit_BTC_*/hands/hand_*"))
        wb_clock[0] = slot_r16 + 7 * MIN
        points.extend([[slot_r16 + i * MIN, 100_050.0] for i in range(2, 8)])
        sync_res = tracking.sync(
            d,
            hand,
            svc,
            check_due=True,
            archive_root=arch_root,
            run_review=True,
            llm_config={"api_key": "k", "model": "mock-model", "transport": llm_transport},
        )
        assert sync_res["checks"]
        assert llm_calls[0] == 1  # No unnecessary LLM call when conditions stay healthy

        # Settle at expiry -> SHADOW_ONLY guarantees actual_execution_net_btc is None
        wb_clock[0] = expiry + MIN
        for m in range(slot_r16 + 8 * MIN, expiry + 1, MIN):
            points.append([m, 101_000.0])
        d_settle = dossier.Dossiers(wb_cfg.shadow_dossier_root, clock=lambda: wb_clock[0])
        outcome = d_settle.settle(expiry, 101_000.0, {"index_name": "btc_usd", "expiry_ms": expiry, "points": points})
        assert outcome["actual_execution_net_btc"] is None

    # 6. Materializer with SESSION_CONTEXT_REQUIRED=1 -> zero compat_backfill_applied
    monkeypatch.setenv("SESSION_CONTEXT_REQUIRED", "1")
    mat_out = tmp_path / "static_cards"
    mat_res = materializer.materialize(review_jsonl, mat_out, max_cards=15)
    assert mat_res["written_cards"] == 1
    card_files = [p for p in (mat_out / "signal_cards").glob("*.json") if p.name != "index.json"]
    assert len(card_files) == 1
    card_data = json.loads(card_files[0].read_text(encoding="utf-8"))
    assert card_data["identity"]["strategy_version"] == "2.0.6"
    sess_ctx = card_data["signal_window"]["session_context"]
    assert sess_ctx["schema_name"] == "SignalSessionPremiseDurabilityContext"
    assert "clock_window" in sess_ctx
    assert "backtest_delta_pp" in sess_ctx
    assert "validation_basis" in sess_ctx
    assert card_data["decision_matrix"]["temporal_durability"] is not None
    assert card_data.get("compat_backfill_applied") is not True
    assert sess_ctx.get("compat_backfill_applied") is not True

    # 7. Cross-process Deribit 429 cooldown coordination across producer -> workbench -> research_archive
    import time
    wall_now_ms = int(time.time() * 1000)
    clock_box[0] = wall_now_ms
    cooldown_file = tmp_path / "shared_deribit_cooldown.json"
    monkeypatch.setenv(deribit_cooldown.ENV_KEY, str(cooldown_file))
    runner.cooldown_file = cooldown_file
    runner.runtime.http.get_json = lambda *_a, **_k: {
        "quality": runner.core_mod.QUALITY_ERROR,
        "data": None,
        "error": "HTTP 429 too_many_requests",
    }
    runner.runtime.deribit.public_get("public/ticker", {"instrument_name": short_inst})
    assert deribit_cooldown.remaining_ms(cooldown_file, now_ms=wall_now_ms) > 0

    # Workbench expiry gamma & discovery must defer without hitting HTTP
    wb.expiry_gamma_get_json = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should defer expiry gamma under shared cooldown"))
    wb._expiry_gamma = None
    assert wb.refresh_expiry_gamma(now_ms=wall_now_ms, force=False) is None

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should skip HTTP under cooldown")))
    with pytest.raises(workbench.UpstreamUnavailable, match="deribit_rate_limited"):
        wb._default_discovery_capture(now_ms=wall_now_ms)

    # Research archive Deribit.book must skip network while cooldown is active
    live_arch_deribit = archive.Deribit(min_book_interval_s=0.0)
    with pytest.raises(ValueError, match="cooldown active"):
        live_arch_deribit.book(short_inst)
