"""End-to-end tests for spot active micro-flow clock, provenance, and bar integrity.

Covers the full pipeline:
  BarAssembler -> compute_micro_flow_context / evaluate_tmvf / DemoRuntime
  -> astra_underwriting_fact_context_v20 -> astra_case_file_v1
  -> astra_evidence_qual_v1.qualify_spot_window & astra_player_input_v1
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from tools import astra_case_file_v1 as cf
from tools import astra_evidence_qual_v1 as eq
from tools import astra_player_input_v1 as player
from tools import astra_underwriting_fact_context_v20 as facts

ROOT = Path(__file__).resolve().parents[1]
SIGNAL_FILE = ROOT / "demo" / "最新交付物" / "neutral_regulation_demo_fmz.py"
HOUR = 3_600_000
MIN = 60_000


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("nrd_signal_fmz_p5_flow", SIGNAL_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.fmz_log = lambda *parts: None
    module.fmz_status = lambda text: None
    module.fmz_push = lambda text: None
    return module


def _live_assembler(mod, *, bar_history_size: int | None = None, spot_symbol: str = "BTCUSDT"):
    cfg = dict(mod.CONFIG)
    cfg["spot_symbol"] = spot_symbol
    if bar_history_size is not None:
        cfg["bar_history_size"] = bar_history_size
    return mod.BarAssembler(
        lambda **_: {"quality": mod.QUALITY_OK, "data": []},
        cfg,
        mod.BinanceAdapter.normalize_agg_trade,
    ), cfg


def _feed_live_bars(
    assembler,
    *,
    start_ts_ms: int,
    bar_count: int,
    step_ms: int,
    start_trade_id: int = 1,
    base_price: float = 95_000.0,
    price_step: float = 5.0,
    buyer_is_maker: bool = False,
):
    threshold = float(assembler.config["volume_bar_n"])
    trade_id = start_trade_id
    for idx in range(bar_count):
        ts = start_ts_ms + idx * step_ms
        price = base_price + idx * price_step
        rows = [
            {
                "a": trade_id,
                "p": f"{price:.2f}",
                "q": f"{threshold / 2.0:.4f}",
                "T": ts - 2_000,
                "m": buyer_is_maker,
            },
            {
                "a": trade_id + 1,
                "p": f"{price + 1.0:.2f}",
                "q": f"{threshold / 2.0:.4f}",
                "T": ts,
                "m": buyer_is_maker,
            },
        ]
        trade_id += 2
        assembler.fetch_agg_trades = lambda from_id=None, _rows=rows: {
            "quality": "OK",
            "data": _rows,
        }
        assembler.poll()
    return trade_id


def _build_eval_snapshot(mod, cfg, *, bars, as_of_ms: int, history_evicted_before_ms: int | None = None):
    last_close = float(bars[-1]["close"]) if bars else 95_000.0
    anchor = {
        "module": mod.MODULE_ANCHOR,
        "state": mod.STATE_VALID,
        "quality": mod.QUALITY_OK,
        "facts": {
            "ready": True,
            "effective_flip_point": last_close - 500.0,
            "raw_flip_point": last_close - 520.0,
            "gex_source_ts_ms": as_of_ms - 10_000,
            "gex_freshness": "FRESH",
            "freshness": "FRESH",
            "normalized_deviation": 0.15,
            "anchor_gravity_ref_score": 72.0,
            "anchor_gravity_ref_label": "Valid",
            "anchor_gravity_warming": False,
            "anchor_gravity_window_count": 40,
        },
    }
    kline_bars = []
    for i in range(60):
        open_t = as_of_ms - (60 - i) * HOUR
        kline_bars.append({
            "open_time": open_t,
            "open": last_close - 100.0 + i,
            "high": last_close - 80.0 + i,
            "low": last_close - 120.0 + i,
            "close": last_close - 95.0 + i,
            "volume": 500.0,
            "close_time": open_t + HOUR - 1,
        })
    funding_points = [
        {"funding_rate": 0.00003, "funding_time": as_of_ms - (i + 1) * 8 * HOUR}
        for i in range(6)
    ]
    tmvf = mod.evaluate_tmvf(
        bars,
        anchor,
        {"last_funding_rate": 0.00003, "mark_price": last_close, "index_price": last_close},
        cfg,
        kline_bars=kline_bars,
        funding_points=funding_points,
        as_of_ms=as_of_ms,
        history_evicted_before_ms=history_evicted_before_ms,
    )
    external = {"module": mod.MODULE_EXTERNAL_GATE, "state": mod.STATE_VALID, "quality": mod.QUALITY_OK, "facts": {}}
    fs = mod.build_factor_snapshot(
        [external, anchor, tmvf],
        None,
        {"macro_score": 0.0, "macro_regime": "Neutral", "data_status": "OK"},
        {"state": "NO_MOVE", "score": 0.1},
        {"state": "NR_IDLE", "is_active": False},
        cfg,
    )
    fs["gex_info"] = {
        "asset": "BTC",
        "quality": "OK",
        "data_state": "live",
        "availability": "ready",
        "total_net_gex": 100_000_000.0,
        "fetched_at_ms": as_of_ms - 5_000,
    }
    fs["space_gate"] = {
        "state": "STRUCTURE_ELIGIBLE",
        "observed_at_ms": as_of_ms,
        "since_ts_ms": as_of_ms - 60_000,
        "reason_codes": [],
        "anchor": {"state": "Valid", "score": 72.0, "ok": True},
        "net_gamma": {
            "ok": True,
            "valid": True,
            "transition": False,
            "conflict": False,
            "sign": "POSITIVE",
            "unit": "USD",
            "value": 100_000_000.0,
            "source": "gexmonitorapi.gex_board.total_net_gex",
            "source_ts_ms": as_of_ms - 5_000,
        },
    }
    return {
        "schema_name": "EvaluationSnapshot",
        "schema_version": cfg["schema_version"],
        "decision": {
            "ts_ms": as_of_ms,
            "demo_version": cfg["demo_version"],
            "symbol": "BTC",
            "runtime_facts": {
                "runtime_mode": "live_public_read_only",
                "current_price": last_close,
                "current_price_source": "binance_spot_latest_trade",
                "current_price_observed_ms": as_of_ms - 1_000,
                "source_quality": {"binance": "OK", "deribit": "OK", "gex": "OK"},
            },
        },
        "factor_snapshot": fs,
    }


def _to_case_l5_vp(fact_ctx: dict[str, Any], *, spot: float, asof_ms: int) -> tuple[dict[str, Any], dict[str, Any]]:
    expiry_ms = asof_ms + 16 * HOUR
    cand_snap = {
        "snapshot_id": "s" * 64,
        "candidate_id": "c" * 24,
        "contract": {
            "side": "put",
            "short_instrument": "BTC-25NOV26-93000-P",
            "long_instrument": "BTC-25NOV26-91000-P",
            "short_strike_usd": 93_000.0,
            "long_strike_usd": 91_000.0,
            "width_usd": 2_000.0,
            "quantity_btc": 1.0,
            "expiry_ms": expiry_ms,
        },
        "liability": {"payout_table": [], "breakeven_usd": [92_800.0]},
        "economics": {"net_credit_after_fee_btc": 0.002},
        "market": {"valuation_ts_ms": asof_ms, "quote": {"credit_btc": 0.0025, "gap_reasons": []}},
        "fact_context": fact_ctx,
        "data_identity": {},
    }
    book = {"underlying_price": spot, "mark_price": 0.003, "mark_iv": 45.0, "greeks": {"delta": -0.3}}
    case = cf.build(
        snapshot=cand_snap,
        raw_market={"short_book": book, "long_book": {**book, "mark_price": 0.001}},
        asof_ms=asof_ms,
    )
    l5_vp = case["layers"]["L5"][case["refs"]["L5.volume_price"]["index"]]
    return case, l5_vp


def _klines(n: int, *, end_ms: int, volume: float = 12.0, taker_buy: float = 7.0, price: float = 95_000.0):
    rows = []
    for i in range(n):
        open_ts = end_ms - (n - i) * MIN
        rows.append([
            open_ts,
            f"{price:.2f}",
            f"{price + 15:.2f}",
            f"{price - 15:.2f}",
            f"{price + i * 0.5:.2f}",
            f"{volume:.4f}",
            open_ts + MIN - 1,
            "0",
            100,
            f"{taker_buy:.4f}",
            "0",
            "0",
        ])
    return rows


def test_e2e_positive_live_agg_trades_observed_aligned_and_derived_windows(mod, monkeypatch):
    t_now = 1_795_500_000_000
    monkeypatch.setattr(mod, "now_ms", lambda: t_now)

    assembler, cfg = _live_assembler(mod)
    # 13 hours of continuous live 10m bars (78 bars)
    step_ms = 10 * MIN
    bar_count = 78
    start_ts = t_now - (bar_count - 1) * step_ms - 30_000
    _feed_live_bars(assembler, start_ts_ms=start_ts, bar_count=bar_count, step_ms=step_ms)

    eval_snap = _build_eval_snapshot(
        mod, cfg, bars=list(assembler.completed_bars), as_of_ms=t_now,
        history_evicted_before_ms=assembler.history_evicted_before_ms,
    )
    cross = mod._build_cross_section(eval_snap["factor_snapshot"], {"last_cycle_trade_count": 2})
    assert cross["micro_flow"]["age_ms"] == 30_000

    fact_ctx = facts.from_evaluation_snapshot(eval_snap, t_now + 1_000, source_hash="f" * 64)
    assert fact_ctx["strategy_version"] == "2.0.7"
    mf = fact_ctx["market_facts"]["volume_price"]["micro_flow"]
    fast, slow, comb = mf["fast_4h"], mf["slow_12h"], mf["combined"]

    assert comb["fast_4h_ready"] is True
    assert comb["slow_12h_ready"] is True
    assert comb["end_ts_ms"] == fast["end_ts_ms"] == fast["complete_ts_ms"] == fast["window_end_ms"]
    assert comb["available_at_ms"] == fast["available_at_ms"] == t_now
    assert fast["source"] == fast["bar_source"] == fast["price_source"] == "binance_spot_agg_trades"
    assert fast["symbol"] == slow["symbol"] == "BTCUSDT"
    assert fast["bar_complete"] is True and slow["bar_complete"] is True
    assert fast["last_bar_index"] == bar_count
    assert fast["cum_session_id"] == f"sess_{t_now}"
    assert fast["cum_gap_count"] == 0
    assert fast["cum_cvd_btc"] == pytest.approx(assembler.cum_cvd_btc)

    _, l5_vp = _to_case_l5_vp(fact_ctx, spot=95_400.0, asof_ms=t_now + 2_000)
    l5_mf = l5_vp["values"]["micro_flow"]

    q_fast = eq.qualify_spot_window(l5_mf["fast_4h"], cutoff_ms=t_now + 2_000, window_name="fast_4h")
    q_slow = eq.qualify_spot_window(l5_mf["slow_12h"], cutoff_ms=t_now + 2_000, window_name="slow_12h")
    assert q_fast["status"] == eq.QUAL_ALIGNED and q_fast["capability"] == eq.CAP_ACTIVE
    assert q_slow["status"] == eq.QUAL_ALIGNED and q_slow["capability"] == eq.CAP_ACTIVE

    insp = player.inspect_spot_qualification(l5_vp, cutoff=t_now + 2_000)
    assert insp["status"] == "OBSERVED_ALIGNED"
    assert insp["derived"]["comparable"] is True


def test_adversarial_missing_trade_timestamp_never_uses_now_ms(mod, monkeypatch):
    sentinel_now = 9_999_999_000_000
    monkeypatch.setattr(mod, "now_ms", lambda: sentinel_now)

    assembler, cfg = _live_assembler(mod)
    threshold = float(cfg["volume_bar_n"])
    # Ingest trade missing ts_ms directly (e.g. malformed or zero timestamp)
    assembler._ingest_trade({"id": 1, "price": 95_000.0, "qty": threshold, "signed_qty": threshold, "ts_ms": None})
    assert len(assembler.completed_bars) == 1
    bad_bar = assembler.completed_bars[-1]
    assert bad_bar["complete_ts_ms"] is None
    assert bad_bar["complete_ts_synthetic"] is True
    assert bad_bar["bar_complete"] is False

    # Also test <= 0 timestamp
    assembler._ingest_trade({"id": 2, "price": 95_010.0, "qty": threshold, "signed_qty": threshold, "ts_ms": 0})
    assert assembler.completed_bars[-1]["complete_ts_ms"] is None
    assert assembler.completed_bars[-1]["complete_ts_synthetic"] is True
    assert assembler.completed_bars[-1]["bar_complete"] is False

    ctx = mod.compute_micro_flow_context(list(assembler.completed_bars), cfg, as_of_ms=sentinel_now)
    assert ctx["fast_4h"]["data_ready"] is False
    assert ctx["fast_4h"].get("end_ts_ms") is None
    assert ctx["fast_4h"]["complete_ts_synthetic"] is True
    assert ctx["fast_4h"]["bar_complete"] is False
    assert ctx["combined"].get("end_ts_ms") is None

    # Cross-section must NOT fabricate age_ms=0 when end_ts_ms is absent
    cross = mod._build_cross_section({"flow": {"micro_flow": ctx}}, {"last_cycle_trade_count": 2})
    assert "age_ms" not in cross["micro_flow"]

    q = eq.qualify_spot_window(ctx["fast_4h"], cutoff_ms=sentinel_now, window_name="fast_4h")
    assert q["status"] == eq.QUAL_UNKNOWN


def test_adversarial_mid_batch_trade_id_gap_sets_bar_gap_and_downgrades_e2e(mod, monkeypatch):
    t_now = 1_795_500_000_000
    monkeypatch.setattr(mod, "now_ms", lambda: t_now)
    assembler, cfg = _live_assembler(mod)
    threshold = float(cfg["volume_bar_n"])

    # Warm 4.5h of clean bars (27 bars, 10m apart)
    next_id = _feed_live_bars(assembler, start_ts_ms=t_now - 29 * 10 * MIN, bar_count=27, step_ms=10 * MIN)

    # Single poll() batch: first closes bar 28 cleanly (new_bars becomes non-empty!),
    # THEN jumps from next_id+1 to next_id+10 mid-batch to close bar 29!
    batch_ts = t_now - 2 * 10 * MIN
    batch = [
        {"a": next_id, "p": "95500", "q": f"{threshold:.4f}", "T": batch_ts, "m": False},
        {"a": next_id + 10, "p": "95520", "q": f"{threshold:.4f}", "T": batch_ts + 5 * MIN, "m": True},
    ]
    assembler.fetch_agg_trades = lambda from_id=None: {"quality": "OK", "data": batch}
    new_bars = assembler.poll()
    assert len(new_bars) == 2
    assert new_bars[0]["gap"] is False and new_bars[0]["cvd_degraded"] is False
    assert new_bars[1]["gap"] is True and new_bars[1]["cvd_degraded"] is True
    assert new_bars[1]["cum_gap_count"] == 1

    # Next contiguous batch closes bar 30 -> per-bar gap flags reset to False
    _feed_live_bars(
        assembler,
        start_ts_ms=batch_ts + 10 * MIN,
        bar_count=1,
        step_ms=10 * MIN,
        start_trade_id=next_id + 11,
    )
    assert assembler.completed_bars[-1]["gap"] is False
    assert assembler.completed_bars[-1]["cvd_degraded"] is False
    assert assembler.completed_bars[-1]["cum_gap_count"] == 1

    eval_snap = _build_eval_snapshot(mod, cfg, bars=list(assembler.completed_bars), as_of_ms=t_now)
    fact_ctx = facts.from_evaluation_snapshot(eval_snap, t_now + 500, source_hash="g" * 64)
    _, l5_vp = _to_case_l5_vp(fact_ctx, spot=95_520.0, asof_ms=t_now + 1_000)
    fast = l5_vp["values"]["micro_flow"]["fast_4h"]
    comb = l5_vp["values"]["micro_flow"]["combined"]

    assert fast["gap"] is True and fast["cvd_degraded"] is True
    assert "end_ts_ms" not in comb and "available_at_ms" not in comb

    q = eq.qualify_spot_window(fast, cutoff_ms=t_now + 1_000, window_name="fast_4h")
    assert q["status"] == eq.QUAL_UNALIGNED
    assert "degraded:gap" in q["reason_codes"]
    assert "degraded:cvd_degraded" in q["reason_codes"]


def test_adversarial_kline_backfill_and_mixed_backfill_e2e(mod, monkeypatch):
    t_now = 1_795_500_000_000
    monkeypatch.setattr(mod, "now_ms", lambda: t_now)
    assembler, cfg = _live_assembler(mod)

    # Pure 12h kline backfill
    rows = _klines(12 * 60, end_ms=t_now - 30 * MIN, volume=12.0, taker_buy=7.0)
    bf = assembler.backfill_from_klines(rows, t_now)
    assert bf["applied"] is True
    pure_ctx = mod.compute_micro_flow_context(list(assembler.completed_bars), cfg, as_of_ms=t_now)
    assert pure_ctx["fast_4h"]["window_backfill"] is True
    assert pure_ctx["fast_4h"]["backfill_mixed"] is False
    assert pure_ctx["fast_4h"]["source"] == "binance_spot_kline_1m_backfill"
    assert pure_ctx["combined"].get("end_ts_ms") is None

    # Add 2 live bars after backfill -> mixed backfill
    _feed_live_bars(assembler, start_ts_ms=t_now - 20 * MIN, bar_count=2, step_ms=10 * MIN)
    eval_snap = _build_eval_snapshot(mod, cfg, bars=list(assembler.completed_bars), as_of_ms=t_now)
    fact_ctx = facts.from_evaluation_snapshot(eval_snap, t_now + 500, source_hash="b" * 64)
    _, l5_vp = _to_case_l5_vp(fact_ctx, spot=95_200.0, asof_ms=t_now + 1_000)
    fast = l5_vp["values"]["micro_flow"]["fast_4h"]
    assert fast["window_backfill"] is True
    assert fast["backfill_mixed"] is True
    assert fast["source_conflict"] is False

    q = eq.qualify_spot_window(fast, cutoff_ms=t_now + 1_000, window_name="fast_4h")
    assert q["status"] == eq.QUAL_UNALIGNED
    assert "degraded:window_backfill" in q["reason_codes"]
    assert "degraded:backfill_mixed" in q["reason_codes"]


def test_adversarial_deque_eviction_sets_truncated_e2e(mod, monkeypatch):
    t_now = 1_795_500_000_000
    monkeypatch.setattr(mod, "now_ms", lambda: t_now)
    # 50 bars max; feed 80 bars spaced 10m apart -> last 50 bars cover ~8.17h (>0.65 of 12h),
    # so slow_12h has data_ready=True YET oldest 30 bars were evicted inside the 12h window!
    assembler, cfg = _live_assembler(mod, bar_history_size=50)
    step_ms = 10 * MIN
    _feed_live_bars(assembler, start_ts_ms=t_now - 79 * step_ms - 10_000, bar_count=80, step_ms=step_ms)

    assert len(assembler.completed_bars) == 50
    assert assembler.history_evicted_before_ms == t_now - 50 * step_ms - 10_000

    eval_snap = _build_eval_snapshot(
        mod, cfg, bars=list(assembler.completed_bars), as_of_ms=t_now,
        history_evicted_before_ms=assembler.history_evicted_before_ms,
    )
    fact_ctx = facts.from_evaluation_snapshot(eval_snap, t_now + 500, source_hash="t" * 64)
    _, l5_vp = _to_case_l5_vp(fact_ctx, spot=95_200.0, asof_ms=t_now + 1_000)
    l5_mf = l5_vp["values"]["micro_flow"]

    # 4h lower bound is t_end - 4h > evicted watermark (8.33h ago) -> fast_4h NOT truncated
    assert l5_mf["fast_4h"]["truncated"] is False
    assert eq.qualify_spot_window(l5_mf["fast_4h"], cutoff_ms=t_now + 1_000, window_name="fast_4h")["status"] == eq.QUAL_ALIGNED
    # 12h lower bound is t_end - 12h <= evicted watermark -> slow_12h IS ready AND truncated!
    assert l5_mf["slow_12h"]["data_ready"] is True
    assert l5_mf["slow_12h"]["truncated"] is True
    q_slow = eq.qualify_spot_window(l5_mf["slow_12h"], cutoff_ms=t_now + 1_000, window_name="slow_12h")
    assert q_slow["status"] == eq.QUAL_UNALIGNED
    assert "degraded:truncated" in q_slow["reason_codes"]

    # When eviction reaches inside 4h, fast_4h is also truncated and combined omits end_ts_ms
    ctx_4h_trunc = mod.compute_micro_flow_context(
        list(assembler.completed_bars),
        cfg,
        as_of_ms=t_now,
        history_evicted_before_ms=t_now - 3 * HOUR,
    )
    assert ctx_4h_trunc["fast_4h"]["data_ready"] is True
    assert ctx_4h_trunc["fast_4h"]["truncated"] is True
    assert "end_ts_ms" not in ctx_4h_trunc["combined"]
    q_fast_trunc = eq.qualify_spot_window(ctx_4h_trunc["fast_4h"], cutoff_ms=t_now, window_name="fast_4h")
    assert q_fast_trunc["status"] == eq.QUAL_UNALIGNED
    assert "degraded:truncated" in q_fast_trunc["reason_codes"]


def test_adversarial_out_of_order_bars_and_trades_e2e(mod, monkeypatch):
    t_now = 1_795_500_000_000
    monkeypatch.setattr(mod, "now_ms", lambda: t_now)
    assembler, cfg = _live_assembler(mod)
    threshold = float(cfg["volume_bar_n"])

    next_id = _feed_live_bars(assembler, start_ts_ms=t_now - 26 * 10 * MIN, bar_count=26, step_ms=10 * MIN)
    # Trade with timestamp earlier than previous trade
    assembler.fetch_agg_trades = lambda from_id=None: {
        "quality": "OK",
        "data": [{"a": next_id, "p": "95100", "q": f"{threshold:.4f}", "T": t_now - 3 * HOUR, "m": False}],
    }
    ooo_bars = assembler.poll()
    assert ooo_bars[0]["out_of_order"] is True

    eval_snap = _build_eval_snapshot(mod, cfg, bars=list(assembler.completed_bars), as_of_ms=t_now)
    fact_ctx = facts.from_evaluation_snapshot(eval_snap, t_now + 500, source_hash="o" * 64)
    _, l5_vp = _to_case_l5_vp(fact_ctx, spot=95_100.0, asof_ms=t_now + 1_000)
    fast = l5_vp["values"]["micro_flow"]["fast_4h"]
    assert fast["out_of_order"] is True
    assert "end_ts_ms" not in l5_vp["values"]["micro_flow"]["combined"]
    q = eq.qualify_spot_window(fast, cutoff_ms=t_now + 1_000, window_name="fast_4h")
    assert q["status"] == eq.QUAL_UNALIGNED
    assert "degraded:out_of_order" in q["reason_codes"]

    # Raw bar sequence out-of-order directly into compute_micro_flow_context
    clean_bars = [dict(b, out_of_order=False) for b in list(assembler.completed_bars)[:25]]
    clean_bars[-3], clean_bars[-2] = clean_bars[-2], clean_bars[-3]
    ctx_raw_ooo = mod.compute_micro_flow_context(clean_bars, cfg, as_of_ms=t_now)
    assert ctx_raw_ooo["fast_4h"]["out_of_order"] is True


def test_adversarial_duplicate_klines_and_bar_index_conflicts(mod, monkeypatch):
    t_now = 1_795_500_000_000
    monkeypatch.setattr(mod, "now_ms", lambda: t_now)
    assembler, cfg = _live_assembler(mod)

    # Duplicate klines + 1m gap in klines
    base_klines = _klines(260, end_ms=t_now - 10 * MIN, volume=10.0, taker_buy=6.0)
    with_gap = base_klines[:100] + base_klines[102:]  # 2-minute jump -> gap!
    with_dups = with_gap + [base_klines[10], base_klines[20]]
    bf = assembler.backfill_from_klines(with_dups, t_now)
    assert bf["applied"] is True
    assert bf["duplicate_kline_count"] == 2
    assert bf["kline_gap"] is True
    assert any(b["gap"] is True for b in assembler.completed_bars)

    # Exact duplicate bar_index should be deduplicated without conflict or double-counting
    assembler_live, _ = _live_assembler(mod)
    _feed_live_bars(assembler_live, start_ts_ms=t_now - 26 * 10 * MIN, bar_count=26, step_ms=10 * MIN)
    live_bars = list(assembler_live.completed_bars)
    ctx_base = mod.compute_micro_flow_context(live_bars, cfg, as_of_ms=t_now)
    dup_exact = live_bars + [dict(live_bars[-1])]
    ctx_dup = mod.compute_micro_flow_context(dup_exact, cfg, as_of_ms=t_now)
    assert ctx_dup["fast_4h"]["duplicate_bars"] == 1
    assert ctx_dup["fast_4h"]["bar_count"] == ctx_base["fast_4h"]["bar_count"]
    assert ctx_dup["fast_4h"]["cvd_sum"] == pytest.approx(ctx_base["fast_4h"]["cvd_sum"])
    assert ctx_dup["fast_4h"]["source_conflict"] is False

    # Conflicting duplicate bar_index -> source_conflict=True -> OBSERVED_UNALIGNED
    conflicting = dict(live_bars[-1], cvd_delta=-999.0)
    ctx_conflict = mod.compute_micro_flow_context(live_bars + [conflicting], cfg, as_of_ms=t_now)
    assert ctx_conflict["fast_4h"]["source_conflict"] is True
    assert "end_ts_ms" not in ctx_conflict["combined"]
    q = eq.qualify_spot_window(ctx_conflict["fast_4h"], cutoff_ms=t_now, window_name="fast_4h")
    assert q["status"] == eq.QUAL_UNALIGNED
    assert "degraded:source_conflict" in q["reason_codes"]


def test_adversarial_wrong_symbol_and_source_conflict_e2e(mod, monkeypatch):
    t_now = 1_795_500_000_000
    monkeypatch.setattr(mod, "now_ms", lambda: t_now)

    # 1. Wrong symbol (ETHUSDT)
    assembler_eth, cfg_eth = _live_assembler(mod, spot_symbol="ETHUSDT")
    _feed_live_bars(assembler_eth, start_ts_ms=t_now - 26 * 10 * MIN, bar_count=26, step_ms=10 * MIN)
    ctx_eth = mod.compute_micro_flow_context(list(assembler_eth.completed_bars), cfg_eth, as_of_ms=t_now)
    q_eth = eq.qualify_spot_window(ctx_eth["fast_4h"], cutoff_ms=t_now, expected_symbol="BTCUSDT", window_name="fast_4h")
    assert q_eth["status"] == eq.QUAL_UNALIGNED
    assert "symbol_mismatch" in q_eth["reason_codes"]

    # 2. Mixed non-backfill sources & mixed symbols
    assembler_btc, cfg_btc = _live_assembler(mod)
    _feed_live_bars(assembler_btc, start_ts_ms=t_now - 26 * 10 * MIN, bar_count=26, step_ms=10 * MIN)
    mixed_bars = list(assembler_btc.completed_bars)
    mixed_bars[-2] = dict(mixed_bars[-2], source="other_venue_trades", symbol="ETHUSDT")
    ctx_mixed = mod.compute_micro_flow_context(mixed_bars, cfg_btc, as_of_ms=t_now)
    assert ctx_mixed["fast_4h"]["source"] is None
    assert ctx_mixed["fast_4h"]["symbol"] is None
    assert ctx_mixed["fast_4h"]["source_conflict"] is True
    q_mixed = eq.qualify_spot_window(ctx_mixed["fast_4h"], cutoff_ms=t_now, window_name="fast_4h")
    assert q_mixed["status"] == eq.QUAL_UNALIGNED
    assert "degraded:source_conflict" in q_mixed["reason_codes"]
    assert "missing_source" in q_mixed["reason_codes"]
    assert "missing_symbol" in q_mixed["reason_codes"]


def test_adversarial_4h_valid_while_12h_invalid_and_anti_lookahead_e2e(mod, monkeypatch):
    t_now = 1_795_500_000_000
    monkeypatch.setattr(mod, "now_ms", lambda: t_now)
    assembler, cfg = _live_assembler(mod)

    # Backfill -12.5h .. -5.5h (7h of klines), then 5h of clean live aggTrades (-5h .. 0)
    bf_end = t_now - 5 * HOUR - 30 * MIN
    bf_rows = _klines(7 * 60, end_ms=bf_end, volume=12.0, taker_buy=7.0)
    assembler.backfill_from_klines(bf_rows, bf_end + MIN)
    _feed_live_bars(assembler, start_ts_ms=t_now - 5 * HOUR, bar_count=31, step_ms=10 * MIN)

    # Append a future bar beyond t_now to verify anti-lookahead exclusion
    bars_with_future = list(assembler.completed_bars) + [
        dict(assembler.completed_bars[-1], bar_index=9999, complete_ts_ms=t_now + 10 * MIN)
    ]

    eval_snap = _build_eval_snapshot(mod, cfg, bars=bars_with_future, as_of_ms=t_now)
    fact_ctx = facts.from_evaluation_snapshot(eval_snap, t_now + 500, source_hash="m" * 64)
    _, l5_vp = _to_case_l5_vp(fact_ctx, spot=95_150.0, asof_ms=t_now + 1_000)
    l5_mf = l5_vp["values"]["micro_flow"]

    fast, slow, comb = l5_mf["fast_4h"], l5_mf["slow_12h"], l5_mf["combined"]
    assert fast["end_ts_ms"] == t_now
    assert fast["window_backfill"] is False and fast["backfill_mixed"] is False
    assert slow["window_backfill"] is True and slow["backfill_mixed"] is True
    assert comb["fast_4h_ready"] is True and comb["slow_12h_ready"] is True
    assert comb["end_ts_ms"] == t_now

    q_fast = eq.qualify_spot_window(fast, cutoff_ms=t_now + 1_000, window_name="fast_4h")
    q_slow = eq.qualify_spot_window(slow, cutoff_ms=t_now + 1_000, window_name="slow_12h")
    assert q_fast["status"] == eq.QUAL_ALIGNED
    assert q_slow["status"] == eq.QUAL_UNALIGNED
    assert "degraded:window_backfill" in q_slow["reason_codes"]

    insp = player.inspect_spot_qualification(l5_vp, cutoff=t_now + 1_000)
    assert insp["fast_4h"]["status"] == "OBSERVED_ALIGNED"
    assert insp["slow_12h"]["status"] == "OBSERVED_UNALIGNED"
    assert insp["derived"]["comparable"] is False


def test_runtime_tick_wires_asof_and_eviction_watermark(mod, tmp_path, monkeypatch):
    t_now = 1_795_500_000_000
    monkeypatch.setattr(mod, "now_ms", lambda: t_now)
    cfg = dict(mod.CONFIG)
    cfg.update({
        "startup_log_enabled": False,
        "tick_summary_log_enabled": False,
        "log_status_enabled": False,
        "chart_enabled": False,
        "signal_review_push_enabled": False,
        "signal_review_push_test": False,
        "fixed_analysis_round_enabled": False,
        "volume_bar_backfill_enabled": False,
        "bar_history_size": 30,
        "logs_dir": str(tmp_path / "logs"),
        "space_gate_state_file": str(tmp_path / "space_gate.json"),
        "data_export_snapshot_file": str(tmp_path / "snapshots.jsonl"),
        "macro_cache_file": str(tmp_path / "macro.json"),
        "gex_info_cache_file": str(tmp_path / "gex.json"),
    })
    rt = mod.DemoRuntime(cfg)
    _feed_live_bars(rt.bars, start_ts_ms=t_now - 49 * 10 * MIN - 5_000, bar_count=50, step_ms=10 * MIN)
    rt.bars.fetch_agg_trades = lambda from_id=None: {"quality": mod.QUALITY_OK, "data": []}
    rt.http.get_json = lambda url, params=None, **_: (
        {"quality": mod.QUALITY_OK, "data": {"flip_point": 95_000.0, "spot": 95_200.0, "spring": 200.0, "timestamp": t_now - 10_000}}
        if "gex" in url and "/v1/info" not in url else
        {"quality": mod.QUALITY_OK, "data": {"asset": "BTC", "quality": "OK", "stale": False, "availability": "ready", "gex_board": {"total_net_gex": 1.0e8}}}
        if "/v1/info" in url else
        {"quality": mod.QUALITY_OK, "data": _klines(60, end_ms=t_now - MIN)}
        if "/fapi/v1/klines" in url else
        {"quality": mod.QUALITY_OK, "data": [{"symbol": "BTCUSDT", "fundingRate": "0.00003", "fundingTime": t_now - HOUR}]}
        if "/fapi/v1/fundingRate" in url else
        {"quality": mod.QUALITY_OK, "data": {"symbol": "BTCUSDT", "markPrice": "95200", "indexPrice": "95200", "lastFundingRate": "0.00003", "time": t_now - 1_000}}
        if "/fapi/v1/premiumIndex" in url else
        {"quality": mod.QUALITY_OK, "data": {"lastUpdateId": 1, "bids": [["95195", "1"]], "asks": [["95205", "1"]]}}
        if "/api/v3/depth" in url else
        {"quality": mod.QUALITY_OK, "data": {"jsonrpc": "2.0", "result": []}}
        if "get_instruments" in url else
        {"quality": mod.QUALITY_OK, "data": {"jsonrpc": "2.0", "result": {"index_price": 95200.0}}}
        if "get_index_price" in url else
        {"quality": mod.QUALITY_OK, "data": {"chart": {"result": [{"timestamp": [int((t_now - i * 86_400_000) // 1000) for i in range(10, 0, -1)], "indicators": {"quote": [{"close": [100.0] * 10}]}}]}}}
    )
    snap, _ = rt.tick(live_fetch=True)
    mf = snap["factor_snapshot"]["flow"]["micro_flow"]
    assert mf["fast_4h"]["truncated"] is False
    assert mf["slow_12h"]["truncated"] is True
    assert mf["combined"]["available_at_ms"] == t_now
    assert mf["combined"]["end_ts_ms"] == t_now - 5_000

