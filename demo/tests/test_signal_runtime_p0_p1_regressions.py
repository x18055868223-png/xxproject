import importlib.util
import pathlib
import sys
import tempfile
import types


ROOT = pathlib.Path(__file__).resolve().parents[2]
SIGNAL_FILE = (
    ROOT
    / "demo"
    / "\u6700\u65b0\u4ea4\u4ed8\u7269"
    / "neutral_regulation_demo_fmz.py"
)


TESTS = [
    "bar_drain_backlog_and_trade_timestamp",
    "anchor_backlog_preserves_pending_bar_replay",
    "mdie_failed_refresh_does_not_reuse_old_ok_klines",
    "tmvf_failed_refresh_and_stale_funding_do_not_reuse_old_direction",
    "tick_current_price_uses_current_tick_sources_only",
    "stale_gex_info_does_not_change_ggr_live_ok_still_can",
    "deribit_ticker_failures_are_budget_limited",
    "edb_history_dedupes_same_bar_and_greeks_epoch",
    "signal_event_tracker_build_error_does_not_consume_episode",
    "recorder_retries_json_and_push_without_reordering",
    "read_only_audit_record_blocks_execution_but_keeps_model_support",
]


def load_signal_module():
    spec = importlib.util.spec_from_file_location(
        "nrd_signal_runtime_p0_p1_regressions", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(
            message + " (actual={!r}, expected={!r})".format(actual, expected))


class Clock:
    def __init__(self, start_ms=1_782_600_000_000):
        self.value = start_ms

    def set(self, value):
        self.value = int(value)

    def advance_ms(self, value):
        self.value += int(value)
        return self.value

    def now_ms(self):
        return self.value


class PatchedNow:
    def __init__(self, mod, clock):
        self.mod = mod
        self.clock = clock
        self.original = None

    def __enter__(self):
        self.original = self.mod.now_ms
        self.mod.now_ms = self.clock.now_ms
        return self.clock

    def __exit__(self, exc_type, exc, tb):
        self.mod.now_ms = self.original


def install_noop_fmz_io(mod):
    mod.fmz_log = lambda *args, **kwargs: None
    mod.fmz_status = lambda *args, **kwargs: None
    mod.fmz_push = lambda *args, **kwargs: None


def base_config(mod, logs_dir, **overrides):
    cfg = dict(mod.CONFIG)
    cfg.update({
        "startup_log_enabled": False,
        "tick_summary_log_enabled": False,
        "state_change_log_enabled": False,
        "log_status_enabled": False,
        "chart_enabled": False,
        "signal_review_push_test": False,
        "signal_review_push_enabled": False,
        "logs_dir": str(logs_dir),
    })
    cfg.update(overrides)
    return cfg


def make_runtime(mod, logs_dir, **overrides):
    return mod.DemoRuntime(base_config(mod, logs_dir, **overrides))


def trade(trade_id, price, qty=1.0, ts_ms=1_700_000_000_000):
    return {
        "id": trade_id,
        "price": float(price),
        "qty": float(qty),
        "signed_qty": float(qty),
        "ts_ms": int(ts_ms),
    }


def make_klines(count, start_ms, interval_ms, start_price=100000.0,
                step=50.0):
    rows = []
    price = float(start_price)
    for index in range(count):
        open_time = int(start_ms + index * interval_ms)
        close = price + float(step)
        rows.append({
            "open_time": open_time,
            "open": price,
            "high": max(price, close) + 10.0,
            "low": min(price, close) - 10.0,
            "close": close,
            "volume": 1000.0 + index,
            "close_time": open_time + interval_ms - 1,
        })
        price = close
    return rows


def funding_points(count, end_ms, rate=0.0003):
    step = 8 * 60 * 60 * 1000
    start = int(end_ms - (count - 1) * step)
    return [
        {
            "funding_time": start + index * step,
            "funding_rate": float(rate),
            "mark_price": 100000.0,
        }
        for index in range(count)
    ]


def anchor_ok(mod):
    return mod.module_result(
        mod.MODULE_ANCHOR,
        mod.STATE_VALID,
        facts={"normalized_deviation": 0.05},
        quality=mod.QUALITY_OK,
    )


def unavailable_macro():
    return {
        "macro_score": None,
        "macro_regime": "UNAVAILABLE",
        "verdict": "MACRO_UNAVAILABLE",
        "data_status": "unavailable",
    }


def sample_card(mod, cfg, episode_id):
    card = mod.build_sample_review_card(cfg)
    card["episode_id"] = episode_id
    card["card_id"] = episode_id
    return card


def test_bar_drain_backlog_and_trade_timestamp(mod):
    assert_true(
        mod.BinanceAdapter.normalize_agg_trade({
            "a": 1, "p": "100", "q": "1", "m": False,
        }) is None,
        "timestamp-less aggregate trades must be rejected instead of stamped as current")
    cfg = dict(mod.CONFIG)
    cfg.update({
        "bar_history_size": 20,
        "volume_bar_n": 1.0,
        "agg_trades_limit": 2,
        "max_drain_rounds": 2,
        "max_drain_wall_time_ms": 60000,
        "drain_enabled": True,
    })
    pages = [
        [trade(1, 100.0, ts_ms=1010), trade(2, 101.0, ts_ms=1020)],
        [trade(3, 102.0, ts_ms=1030), trade(4, 103.0, ts_ms=1040)],
    ]

    def fetch(from_id=None):
        del from_id
        data = pages.pop(0) if pages else []
        return {"quality": mod.QUALITY_OK, "data": data}

    clock = Clock(9_999_999_999)
    with PatchedNow(mod, clock):
        assembler = mod.BarAssembler(fetch, cfg, normalizer=None)
        bars = assembler.poll_with_drain()

    assert_true(
        assembler.last_cycle_metrics.get("backlogged") is True,
        "full drain pages cut off by max_drain_rounds should report backlogged")
    assert_equal(
        [bar.get("complete_ts_ms") for bar in bars],
        [1010, 1020, 1030, 1040],
        "volume bar complete_ts_ms should come from the completing trade ts_ms")

    responses = [
        {"quality": mod.QUALITY_OK,
         "data": [trade(1, 100.0, ts_ms=1010),
                  trade(2, 101.0, ts_ms=1020)]},
        {"quality": mod.QUALITY_ERROR, "error": "page_2_failed", "data": None},
    ]

    def interrupted_fetch(from_id=None):
        del from_id
        return responses.pop(0)

    interrupted = mod.BarAssembler(interrupted_fetch, cfg, normalizer=None)
    interrupted.poll_with_drain()
    assert_true(
        interrupted.last_cycle_metrics.get("backlogged") is True
        and interrupted.last_cycle_metrics.get("drain_interrupted") is True,
        "a full page followed by fetch failure is still backlogged until a short page proves catch-up")


def test_mdie_failed_refresh_does_not_reuse_old_ok_klines(mod):
    clock = Clock(1_782_600_000_000)
    with tempfile.TemporaryDirectory() as tmp:
        with PatchedNow(mod, clock):
            runtime = make_runtime(
                mod, tmp, live_fetch_enabled=True, m_die_refresh_sec=1)
            runtime.mdie_klines = make_klines(
                40, clock.now_ms() - 50 * 60_000, 60_000, step=220.0)
            runtime.last_mdie_refresh_ms = clock.now_ms() - 180_000
            runtime.mdie_data_quality = mod.QUALITY_OK
            runtime.binance.fetch_futures_klines = (
                lambda **kwargs: {"quality": mod.QUALITY_ERROR,
                                  "error": "network_down", "data": None})
            clock.advance_ms(180_000)
            runtime._refresh_mdie_market_data()
            age_ms = runtime._mdie_data_age_ms()
            mdie = mod.compute_m_die(runtime.mdie_klines, runtime.config)

    assert_true(
        age_ms is not None and age_ms >= 180_000,
        "M-DIE success age must not be reset by a failed refresh attempt")
    assert_true(
        (mdie.get("data_status") or {}).get("data_state") != mod.QUALITY_OK,
        "failed/non-OK M-DIE refresh must not leave old strong klines as OK input")


def test_anchor_backlog_preserves_pending_bar_replay(mod):
    clock = Clock(1_782_600_000_000)
    with tempfile.TemporaryDirectory() as tmp:
        with PatchedNow(mod, clock):
            runtime = make_runtime(mod, tmp, live_fetch_enabled=True)
            runtime._set_current_price(100000.0, "test", clock.now_ms())
            for index, close in ((1, 99000.0), (2, 100100.0)):
                runtime.bars.completed_bars.append(mod.add_schema({
                    "bar_index": index,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "total_volume": 10.0,
                    "cvd_delta": 0.0,
                    "complete_ts_ms": clock.now_ms() + index,
                }, mod.SCHEMA_VOLUME_BAR, runtime.config))
            gex = {
                "flip_point": 100000.0,
                "raw_flip_point": 100000.0,
                "source_ts_ms": clock.now_ms(),
                "spring": 0.0,
                "quality": mod.QUALITY_OK,
            }
            runtime.bars.last_cycle_metrics = {"backlogged": True}
            preview = runtime._evaluate_anchor_for_tick(gex, live=True)
            assert_true(
                preview.get("state") == mod.STATE_INVALID
                and "ANCHOR_TRADE_BACKLOG" in preview.get("reasons", []),
                "backlog tick should expose an invalid non-confirming Anchor")
            assert_true(
                runtime.last_anchor_bar_index is None
                and len(runtime.anchor_nd_window) == 0,
                "backlog preview must not consume pending Anchor bars")

            runtime.bars.last_cycle_metrics = {"backlogged": False}
            runtime._evaluate_anchor_for_tick(gex, live=True)
            assert_equal(
                runtime.last_anchor_bar_index, 2,
                "caught-up tick should replay all pending bars through Anchor")
            assert_equal(
                len(runtime.anchor_nd_window), 2,
                "caught-up replay should retain both damage and repair bars")


def test_tmvf_failed_refresh_and_stale_funding_do_not_reuse_old_direction(mod):
    clock = Clock(1_782_600_000_000)
    old_klines = make_klines(
        190, clock.now_ms() - 220 * 60 * 60_000,
        60 * 60_000, step=95.0)
    old_funding = funding_points(
        30, clock.now_ms() - 13 * 60 * 60_000, rate=0.0004)

    with tempfile.TemporaryDirectory() as tmp:
        with PatchedNow(mod, clock):
            runtime = make_runtime(
                mod, tmp, live_fetch_enabled=True, tmvf_refresh_sec=1)
            runtime.tmvf_klines = list(old_klines)
            runtime.tmvf_funding_points = list(old_funding)
            runtime.last_tmvf_refresh_ms = clock.now_ms() - 180_000
            runtime.tmvf_data_quality = mod.QUALITY_OK
            runtime.binance.fetch_futures_klines = (
                lambda **kwargs: {"quality": mod.QUALITY_ERROR,
                                  "error": "kline_down", "data": None})
            runtime.binance.fetch_funding_rate = (
                lambda **kwargs: {"quality": mod.QUALITY_ERROR,
                                  "error": "funding_down", "data": None})
            clock.advance_ms(180_000)
            runtime._refresh_tmvf_market_data()
            age_ms = runtime._tmvf_data_age_ms()
            tmvf = mod.evaluate_tmvf(
                [], anchor_ok(mod), {}, runtime.config,
                kline_bars=runtime.tmvf_klines,
                funding_points=runtime.tmvf_funding_points)
            stale_funding = mod.compute_funding_layer(
                old_funding, 24, 8, runtime.config)

    assert_true(
        age_ms is not None and age_ms >= 180_000,
        "TMVF success age must not be reset by a failed refresh attempt")
    assert_true(
        tmvf.get("quality") != mod.QUALITY_OK
        and (tmvf.get("facts") or {}).get("direction") == mod.DIRECTION_UNCLEAR,
        "failed TMVF kline refresh must not keep outputting old normal direction")
    assert_true(
        stale_funding.get("data_ready") is False,
        "funding layer must be data_ready=False when latest point is >12h stale")
    assert_true(
        stale_funding.get("funding_norm") in (None, 0.0),
        "stale funding adjustment must not participate in TMVF reflexivity")


def test_tick_current_price_uses_current_tick_sources_only(mod):
    clock = Clock(1_782_600_000_000)
    with tempfile.TemporaryDirectory() as tmp:
        with PatchedNow(mod, clock):
            runtime = make_runtime(
                mod, tmp, live_fetch_enabled=True,
                volume_bar_n=10.0, agg_trades_limit=100)
            runtime.current_price = 99000.0
            runtime.current_price_source = "previous_tick"
            runtime._refresh_macro_factor = (
                lambda live, offline_fixture=False: unavailable_macro())
            runtime._effective_macro_snapshot = unavailable_macro
            runtime._refresh_gex_info = lambda live: None
            runtime.gex.fetch_latest = lambda: {
                "quality": mod.QUALITY_OK,
                "cached": False,
                "data": {
                    "flip_point": 100000.0,
                    "spring": 0.0,
                    "source_ts_ms": clock.now_ms(),
                    "asset_price": 100500.0,
                    "quality": mod.QUALITY_OK,
                },
            }
            runtime.bars.normalize_trade = None
            runtime.bars.fetch_agg_trades = lambda from_id=None: {
                "quality": mod.QUALITY_OK,
                "data": [trade(1, 101234.0, qty=1.0,
                               ts_ms=clock.now_ms() - 1000)],
            }
            runtime.binance.fetch_premium_index = lambda: {
                "quality": mod.QUALITY_OK,
                "data": {"markPrice": "100800", "indexPrice": "100750",
                         "lastFundingRate": "0.0001", "time": clock.now_ms()},
            }
            runtime.binance.fetch_futures_klines = (
                lambda **kwargs: {"quality": mod.QUALITY_MISSING, "data": []})
            runtime.binance.fetch_funding_rate = (
                lambda **kwargs: {"quality": mod.QUALITY_MISSING, "data": []})
            runtime.binance.fetch_spot_depth = (
                lambda limit=20: {"quality": mod.QUALITY_MISSING, "data": None})
            runtime.deribit.get_index_price = (
                lambda index_name: {"quality": mod.QUALITY_MISSING,
                                    "data": None})
            runtime._refresh_option_expiries = lambda: None
            runtime.tick(live_fetch=True)
            facts = runtime._runtime_facts()

    assert_equal(
        facts.get("current_price"), 101234.0,
        "tick current_price should use latest spot trade even before volume bar completion")
    assert_equal(
        facts.get("current_price_source"), "binance_spot_latest_trade",
        "incomplete spot volume bar price source should be explicit")


def test_stale_gex_info_does_not_change_ggr_live_ok_still_can(mod):
    cfg = dict(mod.CONFIG)
    gex_snapshot = {
        "flip_point": 100000.0,
        "asset_price": 102000.0,
        "source_ts_ms": 1_782_600_000_000,
        "raw_payload": {},
    }
    base = mod.evaluate_gamma_regime(
        gex_snapshot, 102000.0, [], cfg, gex_info=None)
    stale_info = {
        "data_status": "STALE",
        "availability": "lkgv",
        "total_net_gex": -50_000_000.0,
        "market_state": "negative_gamma",
        "magnet_price": 98000.0,
    }
    stale = mod.evaluate_gamma_regime(
        gex_snapshot, 102000.0, [], cfg, gex_info=stale_info)
    partial_info = dict(stale_info)
    partial_info.update({"data_status": mod.QUALITY_OK,
                         "availability": "partial"})
    partial = mod.evaluate_gamma_regime(
        gex_snapshot, 102000.0, [], cfg, gex_info=partial_info)
    live_info = dict(stale_info)
    live_info.update({"data_status": mod.QUALITY_OK, "availability": "ready"})
    live = mod.evaluate_gamma_regime(
        gex_snapshot, 102000.0, [], cfg, gex_info=live_info)

    assert_equal(
        stale.get("regime"), base.get("regime"),
        "stale/LKGV gex_info must not change GGR regime")
    assert_equal(
        stale.get("veto"), base.get("veto"),
        "stale/LKGV gex_info must not change GGR veto")
    assert_equal(
        stale.get("pin"), base.get("pin"),
        "stale/LKGV gex_info must not change GGR pin")
    assert_equal(
        (partial.get("regime"), partial.get("veto"), partial.get("pin")),
        (base.get("regime"), base.get("veto"), base.get("pin")),
        "partial gex_info must remain display-only and not alter GGR")
    assert_true(
        live.get("regime") == "TRANSITION"
        and any(code in live.get("reason_codes", []) for code in (
            "GGR_GEX_INFO_STATE_DISAGREES",
            "GGR_AGG_NET_GEX_DISAGREES",
        )),
        "live OK gex_info should still be able to downgrade conflicting GGR")


def make_option_instruments(now_ms, price):
    instruments = []
    expiries = [
        now_ms + 24 * 60 * 60_000,
        now_ms + 48 * 60 * 60_000,
    ]
    for expiry in expiries:
        for opt_type, suffix in (("call", "C"), ("put", "P")):
            for offset in range(-10, 10):
                strike = price + offset * 100.0
                instruments.append({
                    "instrument_name": "BTC-TEST-{}-{}".format(
                        int(strike), suffix),
                    "option_type": opt_type,
                    "strike": strike,
                    "expiration_ts_ms": expiry,
                    "state": "open",
                    "is_active": True,
                })
    return expiries, instruments


def test_deribit_ticker_failures_are_budget_limited(mod):
    clock = Clock(1_782_600_000_000)
    with tempfile.TemporaryDirectory() as tmp:
        with PatchedNow(mod, clock):
            runtime = make_runtime(
                mod, tmp, live_fetch_enabled=True,
                deribit_option_strikes_each_side=8,
                deribit_option_refresh_sec=1)
            runtime.current_price = 100000.0
            expiries, instruments = make_option_instruments(
                clock.now_ms(), runtime.current_price)
            runtime.option_expiries = expiries
            runtime.option_instruments = instruments
            calls = []

            def fail_ticker(instrument_name):
                calls.append(instrument_name)
                return {"quality": mod.QUALITY_ERROR,
                        "error": "deribit_down", "data": None}

            runtime.deribit.get_ticker = fail_ticker
            runtime._refresh_option_greeks()

    assert_true(
        len(calls) < 32,
        "all-failed Deribit ticker refresh should fast-circuit or stay under budget, not request 32 tickers")


def test_edb_history_dedupes_same_bar_and_greeks_epoch(mod):
    with tempfile.TemporaryDirectory() as tmp:
        runtime = make_runtime(mod, tmp, live_fetch_enabled=False)
        flow = {
            "micro_flow": {
                "fast_4h": {
                    "data_ready": True,
                    "cvd_norm": 0.25,
                    "latest_bar_index": 7,
                    "latest_complete_ts_ms": 7000,
                },
                "slow_12h": {
                    "data_ready": True,
                    "cvd_norm": -0.15,
                    "latest_bar_index": 7,
                    "latest_complete_ts_ms": 7000,
                },
            },
        }
        skew = {
            "data_state": "OK",
            "rr_blend": -0.045,
            "greeks_epoch_ms": 1000,
        }
        runtime._update_edb_history(flow, skew)
        runtime._update_edb_history(flow, skew)

    assert_equal(
        len(runtime.cvd_hist["4h"]), 1,
        "same volume bar must not append duplicate 4h CVD history")
    assert_equal(
        len(runtime.cvd_hist["12h"]), 1,
        "same volume bar must not append duplicate 12h CVD history")
    assert_equal(
        len(runtime.rr_hist), 1,
        "same option greeks success epoch must not append duplicate RR history")

    next_flow = dict(flow)
    next_flow["micro_flow"] = {
        "fast_4h": dict(flow["micro_flow"]["fast_4h"], latest_bar_index=8,
                        latest_complete_ts_ms=8000),
        "slow_12h": dict(flow["micro_flow"]["slow_12h"], latest_bar_index=8,
                         latest_complete_ts_ms=8000),
    }
    next_skew = dict(skew, rr_blend=-0.030, greeks_epoch_ms=2000)
    runtime._update_edb_history(next_flow, next_skew)
    assert_equal(
        len(runtime.cvd_hist["4h"]), 2,
        "new volume bar should append one fresh CVD history point")
    assert_equal(
        len(runtime.rr_hist), 2,
        "new greeks success epoch should append one fresh RR history point")


def test_signal_event_tracker_build_error_does_not_consume_episode(mod):
    tracker = mod.SignalEventTracker(dict(mod.CONFIG))
    signal = {
        "state": "NR_REPAIR_CONFIRMED",
        "is_active": True,
        "event_context": {"episode_id": "EP-BUILD-FAIL",
                          "episode_direction": "UP"},
        "anchor_context": {"anchor_score": 64.0,
                           "normalized_deviation": 0.2},
    }
    original_builder = mod.build_signal_review_card

    def boom(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("card build failed")

    mod.build_signal_review_card = boom
    try:
        try:
            tracker.maybe_record(signal, {"neutral_repair_signal": signal}, {})
        except RuntimeError:
            pass
    finally:
        mod.build_signal_review_card = original_builder

    assert_true(
        "EP-BUILD-FAIL" not in tracker.seen_episode_ids,
        "SignalEventTracker must not consume episode id before card build succeeds")
    assert_true(
        tracker.maybe_record(signal, {"neutral_repair_signal": signal}, {}),
        "same episode should be recordable after a transient card build failure")


class FakeRecorder:
    def __init__(self, results):
        self.results = list(results)
        self.writes = []

    def write(self, name, payload):
        self.writes.append((name, payload))
        if self.results:
            return self.results.pop(0)
        return True


def test_recorder_retries_json_and_push_without_reordering(mod):
    cfg = dict(mod.CONFIG)
    cfg.update({
        "signal_review_enabled": True,
        "signal_review_push_enabled": False,
    })
    runtime = object.__new__(mod.DemoRuntime)
    runtime.config = cfg
    runtime.signal_events = types.SimpleNamespace(
        events=[sample_card(mod, cfg, "EP-JSON-RETRY")])
    runtime.last_signal_recorded = True
    runtime.recorder = FakeRecorder([False, True])

    mod.DemoRuntime._emit_signal_review_card(runtime)
    runtime.last_signal_recorded = False
    mod.DemoRuntime._emit_signal_review_card(runtime)

    assert_equal(
        len(runtime.recorder.writes), 2,
        "failed JSON write must be retried on the next tick even without a new signal")

    cfg = dict(cfg, signal_review_push_enabled=True)
    runtime = object.__new__(mod.DemoRuntime)
    runtime.config = cfg
    first_card = sample_card(mod, cfg, "EP-PUSH-1")
    second_card = sample_card(mod, cfg, "EP-PUSH-2")
    runtime.signal_events = types.SimpleNamespace(events=[first_card])
    runtime.last_signal_recorded = True
    runtime.recorder = FakeRecorder([True, True])
    pushes = []
    original_push = mod.fmz_push

    def fail_first_push(body):
        pushes.append(body)
        if len(pushes) == 1:
            raise RuntimeError("push down")
        return True

    mod.fmz_push = fail_first_push
    try:
        mod.DemoRuntime._emit_signal_review_card(runtime)
        first_body = pushes[0]
        runtime.last_signal_recorded = True
        runtime.signal_events = types.SimpleNamespace(
            events=[second_card, first_card])
        mod.DemoRuntime._emit_signal_review_card(runtime)
    finally:
        mod.fmz_push = original_push

    assert_equal(
        len(runtime.recorder.writes), 1,
        "push retry after successful JSON must not duplicate the JSON record")
    assert_true(
        len(pushes) >= 2 and pushes[1] == first_body,
        "failed push should retry the original queued card before newer cards")


def test_read_only_audit_record_blocks_execution_but_keeps_model_support(mod):
    cfg = dict(mod.CONFIG)
    cfg["read_only_demo"] = True
    card = mod.build_sample_review_card(cfg)
    card["conclusion"].update({
        "support_label": "TRADE_SUPPORT_STRONG",
        "support_cn": "强方向支持",
        "side_hint": mod.SIDE_PUT_CREDIT_SPREAD,
        "next_action": "ALLOW_DOWNSTREAM",
    })
    card["blocking"] = {
        "has_block": False,
        "block_kind": "NONE",
        "execution_allowed": False,
        "hard_veto": None,
        "soft_gates": [],
    }
    record = mod.build_audit_record(card, cfg)

    assert_true(
        record["decision"]["trade_allowed"] is False,
        "read_only_demo producer audit record must keep decision.trade_allowed=False")
    assert_true(
        record["decision_matrix"]["execution_allowed"] is False,
        "read_only_demo producer audit record must keep execution_allowed=False")
    assert_true(
        record["decision_matrix"]["model_trade_support"] is True,
        "read_only_demo audit should still express model trade support separately")


def main():
    mod = load_signal_module()
    install_noop_fmz_io(mod)
    test_bar_drain_backlog_and_trade_timestamp(mod)
    test_anchor_backlog_preserves_pending_bar_replay(mod)
    test_mdie_failed_refresh_does_not_reuse_old_ok_klines(mod)
    test_tmvf_failed_refresh_and_stale_funding_do_not_reuse_old_direction(mod)
    test_tick_current_price_uses_current_tick_sources_only(mod)
    test_stale_gex_info_does_not_change_ggr_live_ok_still_can(mod)
    test_deribit_ticker_failures_are_budget_limited(mod)
    test_edb_history_dedupes_same_bar_and_greeks_epoch(mod)
    test_signal_event_tracker_build_error_does_not_consume_episode(mod)
    test_recorder_retries_json_and_push_without_reordering(mod)
    test_read_only_audit_record_blocks_execution_but_keeps_model_support(mod)
    print("signal_runtime_p0_p1_regressions: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_runtime_p0_p1_regressions: FAIL - " + str(exc))
        sys.exit(1)
