import copy
import datetime
import importlib.util
import json
import pathlib
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
SIGNAL_FILE = (
    ROOT / "demo" / "\u6700\u65b0\u4ea4\u4ed8\u7269" /
    "neutral_regulation_demo_fmz.py"
)


def load_signal_module():
    spec = importlib.util.spec_from_file_location(
        "nrd_signal_fmz_162_gex_time_semantics", SIGNAL_FILE)
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


def utc_ms(year=2026, month=9, day=11, hour=12, minute=0, second=0):
    dt = datetime.datetime(
        year, month, day, hour, minute, second,
        tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


def iso(ms):
    return datetime.datetime.fromtimestamp(
        ms / 1000.0, datetime.timezone.utc).isoformat()


def make_payload(fetched_ms=None, semantics=None, observed_equals_fetch=False):
    fetched_ms = fetched_ms or utc_ms()
    payload = {
        "asset": "BTC",
        "availability": "ready",
        "stale": False,
        "fetched_at": iso(fetched_ms),
        "gex_board": {
            "total_net_gex": 210.61,
            "dvol": 41.5,
            "market_state": "positive_gamma",
        },
        "gamma_exposure": {
            "spot_price": 113000.0,
            "flip_point": 112500.0,
            "magnet_price": 113200.0,
            "volatility_trigger": 111900.0,
            "n1": 111000.0,
            "n2": 110500.0,
            "p1": 114000.0,
            "p2": 115000.0,
        },
        "volatility": {
            "iv_rv_ratio": 1.1,
            "pcr": 0.82,
            "term_structure": [
                {"expiry": "2026-09-12", "atm_iv": 0.62, "skew_25d": -0.03},
            ],
        },
        "flow": {
            "call_premium": 1200.0,
            "put_premium": 900.0,
            "put_call_ratio": 0.75,
            "call_put_bias": "57.1% Call",
            "abnormal_signal": None,
        },
        "missing_fields": [],
        "field_status": {
            "gex_board.total_net_gex": {
                "source_ref": "gex-latest.total_gex",
                "status": "ok",
            },
            "gamma_exposure.p1": {
                "source_ref": "gex-latest.profiles.total.walls.p1",
                "status": "ok",
            },
        },
        "sections": {
            "gex_board": {
                "fetched_at": iso(fetched_ms),
                "last_success_at": iso(fetched_ms),
                "content_hash": "hash-gex-board",
            },
            "gamma_exposure": {
                "fetched_at": iso(fetched_ms + 1000),
                "last_success_at": iso(fetched_ms + 1000),
                "content_hash": "hash-gamma-exposure",
            },
            "volatility": {
                "fetched_at": iso(fetched_ms + 2000),
                "last_success_at": iso(fetched_ms + 2000),
                "content_hash": "hash-volatility",
            },
            "flow": {
                "fetched_at": iso(fetched_ms + 3000),
                "last_success_at": iso(fetched_ms + 3000),
                "content_hash": "hash-flow",
            },
        },
    }
    if observed_equals_fetch:
        payload["observed_at"] = payload["fetched_at"]
    if semantics is not None:
        payload["gex_time_semantics"] = semantics
    return payload


def assert_old_signal_fields_unchanged(before, after):
    for key in (
            "decision", "decision_matrix", "blocking", "reasoning",
            "conflict", "signal_window", "signal_rating",
            "near_term_market_context"):
        assert_equal(after.get(key), before.get(key),
                     "GEX time semantics must not alter " + key)


def test_version_and_legacy_payload_has_unknown_observation(mod, config):
    assert_equal(config["demo_version"], "1.6.2", "FMZ producer version")
    fetched_ms = utc_ms()
    snapshot = mod.parse_info_payload(
        make_payload(fetched_ms, observed_equals_fetch=True), config)
    semantics = snapshot["gex_time_semantics"]
    assert_equal(semantics["schema_version"], "gex_time_semantics@1.0.0",
                 "native GEX time semantics schema")
    fields = semantics["fields"]
    assert_true("gex_board.total_net_gex" in fields,
                "board net GEX field time exists")
    assert_true("gamma_exposure.p1" in fields,
                "Gamma wall field time exists")
    assert_true("volatility.pcr" in fields,
                "volatility field time exists")

    net_gex = fields["gex_board.total_net_gex"]
    assert_equal(net_gex["source_ref"], "gex-latest.total_gex",
                 "legacy field status source is retained")
    assert_equal(net_gex["observed_at_ms"], None,
                 "legacy fetch time is not promoted to observation time")
    assert_equal(net_gex["generated_at_ms"], None,
                 "legacy payload without semantic source has unknown generated time")
    assert_equal(net_gex["fetched_at_ms"], fetched_ms,
                 "legacy fetch time is kept as fetch time only")
    assert_equal(net_gex["time_basis"],
                 "fetched_time_only_observation_unknown",
                 "legacy fallback basis is explicit")

    p1 = fields["gamma_exposure.p1"]
    assert_equal(p1["fetched_at_ms"], fetched_ms + 1000,
                 "field timing may use its own section fetch time")
    dvol = fields["gex_board.dvol"]
    assert_equal(
        dvol["source_ref"],
        "selection_unknown:gex-latest.dvol|volatility-metrics.metrics.dvol",
        "legacy alternate source is marked as selection-unknown")
    assert_equal(dvol["time_errors"], [],
                 "source selection ambiguity is not a time-format error")
    assert_equal(snapshot["source_content_hashes"]["gamma_exposure"],
                 "hash-gamma-exposure",
                 "section content fingerprint is retained")
    assert_close(snapshot["total_net_gex"], 210.61,
                 "legacy flattened total_net_gex remains unchanged")
    assert_close(snapshot["p1"], 114000.0,
                 "legacy flattened wall remains unchanged")
    assert_true("observed_at" not in snapshot,
                "adapter output has no top-level fake observation time")


def test_api_semantics_are_preserved_and_future_times_not_changed(mod, config):
    fetched_ms = utc_ms()
    generated_ms = fetched_ms - 2500
    future_ms = fetched_ms + 86_400_000
    semantics = {
        "schema_version": "gex_time_semantics@1.0.0",
        "fields": {
            "gex_board.total_net_gex": {
                "source_ref": "gex-latest.total_gex",
                "observed_at_ms": None,
                "generated_at_ms": generated_ms,
                "fetched_at_ms": None,
                "time_basis": "upstream_result_generated_time",
                "time_errors": [],
            },
            "gamma_exposure.p1": {
                "source_ref": "gex-latest.profiles.total.walls.p1",
                "observed_at_ms": future_ms,
                "generated_at_ms": generated_ms + 1000,
                "fetched_at_ms": fetched_ms + 1000,
                "time_basis": "source_observation_time",
                "time_errors": [],
            },
            "gex_board.dvol": {
                "source_ref": "dvol.own_source",
                "observed_at": iso(generated_ms - 2000),
                "generated_at_ms": None,
                "fetched_at_ms": fetched_ms,
                "time_errors": [],
            },
            "custom.missing_source": {
                "fetched_at_ms": None,
                "time_errors": [],
            },
        },
    }
    snapshot = mod.parse_info_payload(make_payload(fetched_ms, semantics), config)
    fields = snapshot["gex_time_semantics"]["fields"]
    assert_equal(fields["gex_board.total_net_gex"]["generated_at_ms"],
                 generated_ms, "upstream generated time is preserved")
    assert_equal(fields["gex_board.total_net_gex"]["fetched_at_ms"], None,
                 "explicit null fetch time in new semantics is preserved")
    assert_equal(fields["gamma_exposure.p1"]["observed_at_ms"],
                 future_ms, "future source time is carried, not rewritten")
    assert_equal(fields["gex_board.dvol"]["source_ref"], "dvol.own_source",
                 "API semantic source_ref wins over legacy field_status")
    assert_equal(fields["gex_board.dvol"]["observed_at_ms"],
                 generated_ms - 2000,
                 "string observation time is normalized to milliseconds")
    assert_equal(fields["custom.missing_source"]["source_ref"], "unknown",
                 "missing source stays explicit")
    assert_equal(fields["custom.missing_source"]["time_errors"], [],
                 "missing source is not a time-format error")

    card = mod.build_sample_review_card(config)
    card["factor_cross_section"]["gex_info"] = snapshot
    record = mod.build_audit_record(card, config)
    assert_equal(record["quality"]["sources"]["gex_info"]["observed_at"], None,
                 "per-field observation is not promoted to global GEX observation")


def test_audit_record_keeps_gex_semantics_and_no_fake_observed(mod, config):
    fetched_ms = utc_ms()
    snapshot = mod.parse_info_payload(
        make_payload(fetched_ms, observed_equals_fetch=True), config)
    legacy_snapshot = copy.deepcopy(snapshot)
    legacy_snapshot.pop("gex_time_semantics", None)
    legacy_snapshot.pop("source_content_hashes", None)
    legacy_snapshot["observed_at"] = legacy_snapshot.get("fetched_at")
    seed_card = mod.build_sample_review_card(config)
    base_card = copy.deepcopy(seed_card)
    base_card["factor_cross_section"]["gex_info"] = legacy_snapshot
    time_card = copy.deepcopy(seed_card)
    time_card["factor_cross_section"]["gex_info"] = snapshot
    before = mod.build_audit_record(copy.deepcopy(base_card), config)
    after = mod.build_audit_record(copy.deepcopy(time_card), config)

    gex = after["factor_cross_section"]["gex_info"]
    assert_equal(gex["gex_time_semantics"]["schema_version"],
                 "gex_time_semantics@1.0.0",
                 "audit record has producer-native GEX time semantics")
    assert_true("observed_at" not in gex,
                "audit GEX info does not backfill fetched_at as observed_at")
    assert_equal(after["quality"]["sources"]["gex_info"]["observed_at"], None,
                 "quality source summary does not claim fake GEX observation")
    assert_old_signal_fields_unchanged(before, after)
    before_gex = before["factor_cross_section"]["gex_info"]
    for key in ("total_net_gex", "net_gamma_notional_usd", "put_wall",
                "call_wall", "magnet_level", "data_status", "quality",
                "age_ms", "fetched_at_ms"):
        assert_equal(gex.get(key), before_gex.get(key),
                     "legacy GEX field unchanged: " + key)


def test_explicit_real_observed_time_and_invalid_entries(mod, config):
    observed_ms = utc_ms(hour=11, minute=55)
    fetched_ms = utc_ms(hour=12)
    snapshot = {
        "quality": "OK",
        "data_state": "live",
        "fetched_at": iso(fetched_ms),
        "fetched_at_ms": fetched_ms,
        "observed_at": iso(observed_ms),
        "observed_at_ms": observed_ms,
        "total_net_gex": 1.0,
        "gex_time_semantics": {
            "schema_version": "gex_time_semantics@1.0.0",
            "fields": {
                "gex_board.total_net_gex": {
                    "source_ref": "legacy.real_observed",
                    "observed_at": iso(observed_ms),
                    "generated_at_ms": "bad-time",
                    "fetched_at_ms": fetched_ms,
                    "time_errors": [],
                },
            },
        },
    }
    card = mod.build_sample_review_card(config)
    card["factor_cross_section"]["gex_info"] = snapshot
    record = mod.build_audit_record(card, config)
    gex = record["factor_cross_section"]["gex_info"]
    entry = gex["gex_time_semantics"]["fields"]["gex_board.total_net_gex"]
    assert_equal(entry["observed_at_ms"], observed_ms,
                 "explicit real observed time survives audit")
    assert_true("generated_at_ms_invalid" in entry["time_errors"],
                "invalid source time is recorded as a local semantic error")
    assert_equal(record["quality"]["sources"]["gex_info"]["observed_at"],
                 mod._iso8601_utc8(observed_ms),
                 "quality summary may show a real explicit GEX observation")


class FailingHttp:
    def get_json(self, *args, **kwargs):
        return {"quality": "ERROR", "error": "network_down"}


def test_cache_fallback_retains_semantics_and_content_fingerprint(mod, config):
    fetched_ms = utc_ms()
    cached = mod.parse_info_payload(make_payload(fetched_ms), config)
    with tempfile.TemporaryDirectory() as tmp:
        cache_path = pathlib.Path(tmp) / "gex_info_cache.json"
        cache_path.write_text(json.dumps({
            "updated_at_ms": fetched_ms,
            "snapshot": cached,
        }), encoding="utf-8")
        cfg = dict(config)
        cfg["gex_info_base_url"] = "http://example.invalid"
        cfg["gex_info_token"] = "token"
        cfg["gex_info_cache_file"] = str(cache_path)
        cfg["gex_info_cache_max_age_ms"] = 86_400_000
        adapter = mod.GexInfoAdapter(FailingHttp(), cfg)
        fallback = adapter.refresh()

    assert_equal(fallback["quality"], "STALE",
                 "failed fetch returns LKGV stale cache")
    assert_true("GEX_INFO_LKGV_FALLBACK" in fallback["reasons"],
                "LKGV reason is explicit")
    assert_equal(fallback["fetch_error"], "network_down",
                 "latest fetch error is recorded separately")
    assert_equal(
        fallback["gex_time_semantics"]["fields"][
            "gex_board.total_net_gex"]["fetched_at_ms"],
        fetched_ms,
        "cache fallback preserves prior field timing")
    assert_equal(fallback["source_content_hashes"]["gex_board"],
                 "hash-gex-board",
                 "cache fallback preserves prior content fingerprint")
    assert_true("observed_at" not in fallback,
                "cache fallback does not invent observed_at")


def test_near_term_context_still_available(mod, config):
    as_of = utc_ms(hour=12, minute=30)
    bars = []
    start = as_of - 30 * 60_000
    for index in range(30):
        open_time = start + index * 60_000
        bars.append({
            "open_time": open_time,
            "open": 100.0 + index,
            "high": 101.0 + index,
            "low": 99.5 + index,
            "close": 100.5 + index,
            "volume": 10.0,
            "close_time": open_time + 60_000 - 1,
            "taker_buy_base_asset_volume": 6.0,
        })
    context = mod.build_near_term_market_context(bars, as_of, config)
    assert_equal(context["schema_version"], "1.0.0",
                 "near-term context schema remains unchanged")
    assert_equal(context["windows"]["15m"]["state"], "OK",
                 "15m near-term window remains available")
    assert_equal(context["windows"]["30m"]["state"], "OK",
                 "30m near-term window remains available")


def main():
    mod = load_signal_module()
    config = dict(mod.CONFIG)
    for invalid in (True, False, float("nan"), float("inf"), -1, 0, "0", "2026-09-11T12:00:00"):
        parsed, errors = mod._gex_semantic_time({"observed_at_ms": invalid}, "observed_at_ms")
        assert_equal(parsed, None, "invalid clock cannot become an old valid observation")
        assert_true(bool(errors), "invalid clock must keep an explicit error")
    for key in ("observed_at_ms", "generated_at_ms", "fetched_at_ms"):
        parsed, errors = mod._gex_semantic_time({key: "2026-09-11T12:00:00"}, key)
        assert_true(parsed is None and errors, "GEX clocks require explicit timezone")
    unknown = {"schema_version": "gex_time_semantics@9.0.0", "fields": {}}
    assert_equal(mod._build_gex_time_semantics({"gex_time_semantics": unknown}), unknown,
                 "unknown upstream protocol cannot masquerade as native v1")
    test_version_and_legacy_payload_has_unknown_observation(mod, config)
    test_api_semantics_are_preserved_and_future_times_not_changed(mod, config)
    test_audit_record_keeps_gex_semantics_and_no_fake_observed(mod, config)
    test_explicit_real_observed_time_and_invalid_entries(mod, config)
    test_cache_fallback_retains_semantics_and_content_fingerprint(mod, config)
    test_near_term_context_still_available(mod, config)
    print("fmz_162_gex_time_semantics: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("fmz_162_gex_time_semantics: FAIL - " + str(exc))
        sys.exit(1)
