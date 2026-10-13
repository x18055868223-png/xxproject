"""Spatial position: the first structure met on the way to the short leg, per side; KPF is
described only; nothing is scored or predicted."""
from copy import deepcopy

from tools import astra_spatial_position_v1 as spatial
from tools import astra_workbench_reading_v1 as reading
from test_astra_locked_followup import setup


def snap(side="put", spot=83_500.0, short=83_000.0, long=81_000.0, flip=83_480.0, walls=(), fresh=True, zones=()):
    if side == "call":
        short, long = (84_000.0, 86_000.0) if short == 83_000.0 else (short, long)
    near = {"weekly": {"walls": {f"w{i}": {"strike": k, "gex_usd": g} for i, (k, g) in enumerate(walls)}}}
    return {
        "contract": {"side": side, "short_strike_usd": short, "long_strike_usd": long,
                     "expiry_ms": 86_400_000, "quantity_btc": 1.0},
        "market": {"reference_price_usd": spot, "valuation_ts_ms": 0},
        "fact_context": {"market_facts": {"space_context": {
            "anchor": {"effective_flip_point": flip, "gex_freshness": "FRESH" if fresh else "STALE"},
            "gex_info": {"atm_iv": 30.0, "near_term_walls": near, "stale": not fresh}}}},
        "data_identity": {"btc_map": {"reference_pool": [
            {"role": "KPF_BASIN", "raw_low": lo, "raw_high": hi, "observation_end_ms": 1} for lo, hi in zones]}},
    }


def test_crossing_the_flip_toward_a_put_short_accelerates():
    out = spatial.assess(snap(flip=83_400.0))
    assert out["state"] == "unfavorable" and out["gamma"]["events"][0]["source"] == "flip"
    assert out["validated"] is False


def test_the_same_terrain_and_distance_reads_differently_for_put_and_call():
    put = spatial.assess(snap(side="put", spot=83_500.0, short=83_000.0, flip=83_480.0))
    call = spatial.assess(snap(side="call", spot=83_500.0, short=84_000.0, long=86_000.0, flip=83_480.0))
    assert abs(put["distance_pct"] - call["distance_pct"]) < 0.01
    assert (put["state"], call["state"]) == ("unfavorable", "favorable")


def test_the_first_structure_met_decides():
    buffered = spatial.assess(snap(flip=82_000.0, walls=[(83_300.0, 2e7), (83_100.0, -3e7)]))
    assert buffered["state"] == "favorable" and buffered["gamma"]["events"][0]["price"] == 83_300.0
    accelerated = spatial.assess(snap(flip=82_000.0, walls=[(83_300.0, -2e7), (83_100.0, 3e7)]))
    assert accelerated["state"] == "unfavorable"


def test_with_nothing_on_the_way_the_short_legs_side_of_the_flip_decides():
    assert spatial.assess(snap(flip=82_000.0))["state"] == "favorable"      # short above the flip
    assert spatial.assess(snap(spot=83_500.0, short=83_000.0, flip=83_900.0))["state"] == "unfavorable"


def test_structures_beyond_the_short_leg_do_not_count():
    out = spatial.assess(snap(flip=82_000.0, walls=[(82_500.0, -5e7)]))  # inside the liability zone
    assert out["gamma"]["events"] == [] and out["state"] == "favorable"


def test_stale_or_missing_data_is_unknown_and_inside_the_liability_zone_is_named():
    assert spatial.assess(snap(fresh=False))["state"] == "unknown"
    assert spatial.assess(snap(flip=None))["state"] == "unknown"
    inside = spatial.assess(snap(spot=82_900.0))
    assert inside["state"] == "unfavorable" and "责任区内" in inside["reason_cn"]


def test_kpf_is_described_but_never_changes_the_state():
    plain = snap(flip=82_000.0)
    zoned = deepcopy(plain)
    zoned["data_identity"]["btc_map"]["reference_pool"] = [
        {"role": "KPF_BASIN", "raw_low": 82_800.0, "raw_high": 83_200.0, "observation_end_ms": 1}]
    a, b = spatial.assess(plain), spatial.assess(zoned)
    assert a["state"] == b["state"] and b["kpf"]["affects_state"] is False
    assert "短腿就在其中" in b["kpf"]["description_cn"] and b["kpf"]["tag"] == "dense_on_way"
    assert a["kpf"]["tag"] is None


def test_the_page_view_carries_the_spatial_position(tmp_path):
    wb, _, _, _ = setup(tmp_path)
    page = reading.workbench_page(wb.cached_view(reading_only=True))
    for item in page["candidates"]:
        assert item["spatial_position"]["schema"] == spatial.SCHEMA
        assert item["spatial_position"]["state"] in {"favorable", "unfavorable", "unknown"}
