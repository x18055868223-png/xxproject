"""lock_levels@0.1: this expiry's structure kept small inside a lock point."""
import json

from tools import astra_expiry_gamma_v1 as expiry_gamma
from tools import astra_lock_levels_v1 as levels

T = 1_793_070_000_000
EXPIRY = 1_793_174_400_000          # 28OCT26 08:00 UTC

PROFILE = {
    "expiry": "28OCT26", "computed_at_ms": T - 120_000,
    "walls": {"pin": 80000.0, "gamma_put": 77000.0, "gamma_call": 80000.0, "oi_put": 77000.0, "oi_call": 84000.0},
    "top": {"gamma_put": [{"strike": 77000.0, "value": 3085440.2}, {"strike": 79000.0, "value": 2469677.2},
                          {"strike": 80500.0, "value": 1264036.1}, {"strike": 76000.0, "value": 1.0}],
            "gamma_call": [{"strike": 80000.0, "value": 2460505.46}, {"strike": "bad", "value": 1.0}]},
}
GEX = {"fetched_at_ms": T - 600_000, "magnet_price": 80000.0, "max_pain_price": 78000.0,
       "max_pain_target_expiry": "30OCT26",
       "next_expiry": {"expiry": "27OCT26", "max_pain_price": 80000.0}}
MAP = {"cutoff_at_ms": T - 30_000, "reference_pool": [
    {"role": "EFFECTIVE_FLIP", "raw_price": 79819.26},
    {"role": "CP_STH", "raw_price": 76709.52},
    {"role": "KPF_BASIN", "raw_low": 83250.0, "raw_high": 83600.0, "observation_end_ms": T - 86_400_000},
    {"role": "KPF_BASIN", "raw_low": 62700.0, "raw_high": 63200.0, "observation_end_ms": T - 90_000_000},
], "background": {"inventory": {"metrics": {"cost_basis": {
    "cp_lth_usd": 84345.9, "cp_all_usd": 82378.1, "latest_observation_date": "2026-10-26"}}}}}


def test_compact_reads_profile_gexmonitor_and_map_with_the_page_semantics():
    out = levels.compact(PROFILE, GEX, MAP, now_ms=T, expiry_ms=EXPIRY)
    assert out["schema"] == "lock_levels@0.1" and out["expiry"] == "28OCT26"
    assert (out["pin"], out["gamma_put"], out["gamma_call"], out["oi_put"], out["oi_call"]) == (
        80000.0, 77000.0, 80000.0, 77000.0, 84000.0)
    assert out["top_gamma_put"] == [[77000.0, 3085440], [79000.0, 2469677], [80500.0, 1264036]]
    assert out["top_gamma_call"] == [[80000.0, 2460505]]
    # neither the next expiry (27OCT26) nor the main one (30OCT26) is this contract's
    assert out["max_pain_this"] is None
    assert (out["max_pain_main"], out["max_pain_main_expiry"], out["magnet"]) == (78000.0, "30OCT26", 80000.0)
    assert out["flip"] == 79819.26
    # the background's cost basis first, the display pool fills what it lacks
    assert (out["cost_sth"], out["cost_lth"], out["cost_all"]) == (76709.52, 84345.9, 82378.1)
    assert out["kpf"] == [[62700.0, 63200.0], [83250.0, 83600.0]] and out["kpf_asof_ms"] == T - 86_400_000
    assert (out["computed_at_ms"], out["age_ms"], out["stale"]) == (T - 120_000, 120_000, False)
    assert (out["gex_asof_ms"], out["map_asof_ms"]) == (T - 600_000, T - 30_000)
    assert len(json.dumps(out, separators=(",", ":"))) < 1024


def test_this_expiry_max_pain_comes_from_whichever_gexmonitor_expiry_matches():
    nxt = {**GEX, "next_expiry": {"expiry": "28OCT26", "max_pain_price": 79500.0}}
    assert levels.compact(PROFILE, nxt, MAP, now_ms=T)["max_pain_this"] == 79500.0
    main = {**GEX, "max_pain_target_expiry": "28OCT26"}
    assert levels.compact(None, main, None, now_ms=T, expiry_ms=EXPIRY)["max_pain_this"] == 78000.0


def test_missing_sources_stay_none_and_an_old_profile_is_stale():
    empty = levels.compact(None, None, None, now_ms=T, expiry_ms=EXPIRY)
    assert empty["expiry"] == expiry_gamma.expiry_code(EXPIRY) == "28OCT26"
    assert empty["profile_present"] is False and empty["stale"] is False
    assert all(empty[k] is None for k in ("pin", "gamma_put", "max_pain_this", "flip", "cost_sth", "age_ms"))
    assert empty["kpf"] == [] and empty["top_gamma_put"] == []
    old = levels.compact({**PROFILE, "computed_at_ms": T - levels.STALE_MS - 1}, GEX, MAP, now_ms=T)
    assert old["stale"] is True


def test_series_subset_is_the_few_levels_of_the_process_series():
    out = levels.compact(PROFILE, GEX, MAP, now_ms=T)
    assert levels.series_subset(out) == {"pin": 80000.0, "gamma_put": 77000.0, "gamma_call": 80000.0,
                                         "oi_put": 77000.0, "oi_call": 84000.0, "max_pain_this": None,
                                         "flip": 79819.26}
    assert levels.series_subset(None) is None and levels.series_subset({"schema": "other"}) is None
