import copy
import datetime
import importlib.util
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
SIGNAL_FILE = (
    ROOT / "demo" / "\u6700\u65b0\u4ea4\u4ed8\u7269" /
    "neutral_regulation_demo_fmz.py"
)


def load_signal_module():
    spec = importlib.util.spec_from_file_location(
        "nrd_signal_fmz_161_near_term", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(
            "{}: expected {!r}, got {!r}".format(message, expected, actual))


def assert_close(actual, expected, message, eps=1e-9):
    if actual is None or abs(actual - expected) > eps:
        raise AssertionError(
            "{}: expected {}, got {}".format(message, expected, actual))


def utc8_ms(year=2026, month=9, day=11, hour=23, minute=0, second=0):
    tz = datetime.timezone(datetime.timedelta(hours=8))
    dt = datetime.datetime(
        year, month, day, hour, minute, second, tzinfo=tz)
    return int(dt.timestamp() * 1000)


def make_bars(as_of_ms, count=30, start_price=100.0, step=1.0,
              volume=10.0, taker=6.0, first_offset_min=None):
    if first_offset_min is None:
        first_offset_min = count
    start = as_of_ms - first_offset_min * 60_000
    bars = []
    price = start_price
    for index in range(count):
        open_time = start + index * 60_000
        open_price = price
        close_price = open_price + step
        high = max(open_price, close_price) + abs(step) * 0.5
        low = min(open_price, close_price) - abs(step) * 0.5
        bars.append({
            "open_time": open_time,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close_price,
            "volume": volume,
            "close_time": open_time + 60_000 - 1,
            "taker_buy_base_asset_volume": taker,
        })
        price = close_price
    return bars


def flat_bars(as_of_ms, count=15, volume=0.0, taker=0.0):
    start = as_of_ms - count * 60_000
    bars = []
    for index in range(count):
        open_time = start + index * 60_000
        bars.append({
            "open_time": open_time,
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": volume,
            "close_time": open_time + 60_000 - 1,
            "taker_buy_base_asset_volume": taker,
        })
    return bars


def assert_old_signal_fields_unchanged(before, after):
    for key in (
            "decision", "decision_matrix", "blocking", "reasoning",
            "conflict", "factor_cross_section", "signal_window",
            "signal_rating"):
        assert_equal(after.get(key), before.get(key),
                     "near-term audit must not alter " + key)


def test_version_and_normalized_kline_taker_buy(mod, config):
    assert_equal(config["demo_version"], "1.6.2",
                 "FMZ producer version")
    row = [
        1000, "100.0", "102.0", "99.0", "101.0", "12.5", 59999,
        "1260.0", 33, "7.25", "731.5", "0",
    ]
    parsed = mod.BinanceAdapter.normalize_kline(row)
    assert_close(parsed["taker_buy_base_asset_volume"], 7.25,
                 "Binance USD-M taker-buy base volume is preserved")
    old_shape = row[:9]
    parsed_old = mod.BinanceAdapter.normalize_kline(old_shape)
    assert_true("taker_buy_base_asset_volume" in parsed_old,
                "missing optional taker-buy key remains explicit")
    assert_equal(parsed_old["taker_buy_base_asset_volume"], None,
                 "old kline shape keeps flow unavailable")


def test_m_die_ignores_optional_taker_buy_field(mod, config):
    as_of = mod.now_ms() - 60_000
    bars_with_flow = make_bars(as_of, count=20, start_price=100.0, step=0.2)
    bars_without_flow = copy.deepcopy(bars_with_flow)
    for item in bars_without_flow:
        item.pop("taker_buy_base_asset_volume", None)
    with_flow = mod.compute_m_die(copy.deepcopy(bars_with_flow), config)
    without_flow = mod.compute_m_die(copy.deepcopy(bars_without_flow), config)
    assert_equal(with_flow, without_flow,
                 "optional taker-buy row9 must not alter legacy M-DIE")


def test_near_term_context_ok_15m_30m_and_hash(mod, config):
    as_of = utc8_ms()
    bars = make_bars(as_of, count=30)
    context = mod.build_near_term_market_context(bars, as_of, config)
    assert_equal(context["schema_version"], "1.0.0",
                 "near-term context schema")
    assert_equal(context["source"], "binance_futures_klines_1m_cache",
                 "near-term context source")
    assert_equal(context["symbol"], config["futures_symbol"],
                 "near-term context symbol")
    assert_equal(context["observed_at_ms"], as_of - 1,
                 "latest closed minute is the observation time")
    assert_equal(len(context["bars"]), 30,
                 "closed minute bar summary archive is bounded at 30 bars")

    win15 = context["windows"]["15m"]
    assert_equal(win15["state"], "OK", "15m window state")
    assert_equal(win15["bar_count"], 15, "15m bar count")
    assert_equal(win15["expected_bar_count"], 15, "15m expected count")
    assert_equal(win15["missing_minutes"], 0, "15m missing minutes")
    assert_close(win15["total_volume"], 150.0, "15m total volume")
    assert_close(win15["taker_buy_volume"], 90.0, "15m taker volume")
    assert_close(win15["net_active_volume"], 30.0, "15m net active volume")
    assert_equal(win15["active_volume_state"], "OK",
                 "15m active volume state")
    assert_close(win15["return_pct"], (130.0 - 115.0) / 115.0 * 100.0,
                 "return_pct is exact percentage points, not ratio")
    assert_true(win15["close_efficiency"] is not None,
                "continuous non-flat window has close efficiency")

    win30 = context["windows"]["30m"]
    assert_equal(win30["state"], "OK", "30m window state")
    assert_equal(win30["bar_count"], 30, "30m bar count")

    card = mod.build_sample_review_card(config)
    card["near_term_market_context"] = context
    record = mod.build_audit_record(card, config)
    assert_equal(record["near_term_market_context"], context,
                 "audit record retains producer near-term context")
    stripped = dict(record)
    stripped.pop("near_term_market_context")
    stripped.pop("integrity")
    stripped_hash = mod._audit_integrity(stripped)["record_hash"]
    assert_true(record["integrity"]["record_hash"] != stripped_hash,
                "near-term context participates in audit integrity hash")


def test_missing_invalid_zero_flat_gap_stale_and_future(mod, config):
    as_of = utc8_ms()

    missing_buy = make_bars(as_of, count=15)
    for item in missing_buy:
        item.pop("taker_buy_base_asset_volume", None)
    win = mod.build_near_term_market_context(
        missing_buy, as_of, config)["windows"]["15m"]
    assert_equal(win["state"], "OK", "missing taker buy does not kill klines")
    assert_equal(win["active_volume_state"], "MISSING",
                 "missing taker buy only closes active-flow judgment")
    assert_equal(win["net_active_volume"], None,
                 "missing taker buy leaves net active flow unknown")

    invalid_buy = make_bars(as_of, count=15)
    invalid_buy[3]["taker_buy_base_asset_volume"] = (
        invalid_buy[3]["volume"] + 1.0)
    win = mod.build_near_term_market_context(
        invalid_buy, as_of, config)["windows"]["15m"]
    assert_equal(win["active_volume_state"], "INVALID",
                 "impossible taker buy volume is isolated to active flow")
    assert_equal(win["net_active_volume"], None,
                 "invalid taker buy does not synthesize flow")

    flat = mod.build_near_term_market_context(
        flat_bars(as_of), as_of, config)["windows"]["15m"]
    assert_equal(flat["state"], "OK", "zero-volume flat bars can be observed")
    assert_equal(flat["active_volume_state"], "OK",
                 "zero volume with zero taker buy is valid")
    assert_close(flat["total_volume"], 0.0, "flat total volume")
    assert_close(flat["net_active_volume"], 0.0, "flat net active volume")
    assert_close(flat["return_pct"], 0.0, "flat return")
    assert_equal(flat["close_efficiency"], None,
                 "flat window has no close efficiency")

    gap = make_bars(as_of, count=15)
    gap.pop(5)
    win = mod.build_near_term_market_context(
        gap, as_of, config)["windows"]["15m"]
    assert_equal(win["state"], "PARTIAL", "gap creates partial state")
    assert_equal(win["missing_minutes"], 1, "gap missing minutes")
    assert_equal(win["close_efficiency"], None,
                 "gap disables continuous close efficiency")

    stale = make_bars(as_of - 10 * 60_000, count=15)
    win = mod.build_near_term_market_context(
        stale, as_of, config)["windows"]["15m"]
    assert_equal(win["state"], "STALE", "stale latest close is explicit")

    future = make_bars(as_of + 20 * 60_000, count=15)
    context = mod.build_near_term_market_context(future, as_of, config)
    assert_equal(context["observed_at_ms"], None,
                 "future bars are not used as observations")
    assert_equal(context["windows"]["15m"]["state"], "MISSING",
                 "future-only bars produce missing current window")


def test_non_1m_cache_disables_near_term_context(mod, config):
    as_of = utc8_ms()
    non_minute_config = dict(config)
    non_minute_config["m_die_interval"] = "5m"
    context = mod.build_near_term_market_context(
        make_bars(as_of, count=30), as_of, non_minute_config)
    assert_equal(
        context["source"],
        "near_term_market_context_disabled_non_1m_m_die_cache",
        "non-1m cache cannot masquerade as minute context")
    assert_equal(context["source_interval"], "5m",
                 "disabled context records the actual cache interval")
    assert_equal(context["disabled"], True,
                 "non-1m near-term context is explicitly disabled")
    assert_true("闭合分钟柱摘要" in context["disabled_reason_cn"],
                "disabled reason explains the minute-bar summary guard")
    assert_equal(context["observed_at_ms"], None,
                 "disabled context has no observation timestamp")
    assert_equal(context["bars"], [],
                 "disabled context does not expose non-minute bars")
    assert_equal(context["windows"]["15m"]["state"], "MISSING",
                 "disabled 15m window is missing")
    assert_equal(context["windows"]["30m"]["state"], "MISSING",
                 "disabled 30m window is missing")


def test_runtime_attachment_and_old_fields_are_unchanged(mod, config):
    as_of = utc8_ms()
    bars = make_bars(as_of, count=30, start_price=200.0)
    card = mod.build_sample_review_card(config)
    card["confirmed_time"] = as_of

    before = mod.build_audit_record(copy.deepcopy(card), config)
    runtime = object.__new__(mod.DemoRuntime)
    runtime.config = config
    runtime.mdie_klines = bars
    enriched = mod.DemoRuntime._attach_near_term_market_context(runtime, card)
    assert_true(enriched is not card,
                "runtime attachment does not mutate the queued card in place")
    assert_true(isinstance(enriched.get("near_term_market_context"), dict),
                "runtime attachment creates the producer audit object")
    after = mod.build_audit_record(copy.deepcopy(enriched), config)
    assert_old_signal_fields_unchanged(before, after)


def main():
    mod = load_signal_module()
    config = dict(mod.CONFIG)
    test_version_and_normalized_kline_taker_buy(mod, config)
    test_m_die_ignores_optional_taker_buy_field(mod, config)
    test_near_term_context_ok_15m_30m_and_hash(mod, config)
    test_missing_invalid_zero_flat_gap_stale_and_future(mod, config)
    test_non_1m_cache_disables_near_term_context(mod, config)
    test_runtime_attachment_and_old_fields_are_unchanged(mod, config)
    print("fmz_161_near_term_context: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("fmz_161_near_term_context: FAIL - " + str(exc))
        sys.exit(1)
