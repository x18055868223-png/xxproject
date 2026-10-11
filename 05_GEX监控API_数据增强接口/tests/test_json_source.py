from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
from conftest import bundle_fixture, fixture_payloads, gex_fixture, install_payloads

from gexmonitorapi.config import Settings
from gexmonitorapi.json_source import PublicJsonSource
from gexmonitorapi.models import SECTION_FIELDS, SECTIONS

REMOVED = ("iv_rv_ratio", "call_premium", "put_premium", "call_put_bias", "abnormal_signal", "call_share_pct")


def _fetch(monkeypatch, payloads: dict) -> dict:
    install_payloads(monkeypatch, payloads)
    source = PublicJsonSource(Settings(asset="BTC", request_timeout_seconds=1))
    return asyncio.run(source.fetch_snapshot())


def _ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).timestamp() * 1000)


def test_fixture_snapshot_maps_every_section(monkeypatch):
    snapshot = _fetch(monkeypatch, fixture_payloads())
    sections = snapshot["sections"]
    assert tuple(sections) == SECTIONS
    for section in SECTIONS:
        assert tuple(sections[section]["data"]) == SECTION_FIELDS[section]
        assert sections[section]["missing_fields"] == [], section

    assert sections["gex_board"]["data"] == {
        "total_net_gex": 158146534.89,
        "dvol": 36.89,
        "market_state": "positive_gamma",
    }
    gamma = sections["gamma_exposure"]["data"]
    assert (gamma["n2"], gamma["n1"], gamma["p1"], gamma["p2"]) == (80000.0, 78000.0, 90000.0, 95000.0)
    assert gamma["flip_point"] == pytest.approx(82725.44344773793)
    assert gamma["volatility_trigger"] == 90000.0
    assert gamma["spot_price"] == 82997.28
    assert gamma["magnet_price"] == 80000.0
    assert gamma["hedging_flows"]["total_gamma_usd"] == pytest.approx(158146534.8928078)
    assert gamma["hedging_flows"]["scenarios"][0] == {"price_change_pct": -5.0, "hedging_usd": -189483543.99}
    assert len(gamma["hedging_flows"]["scenarios"]) == 8

    zero_dte = sections["gamma_profiles"]["data"]["zero_dte"]
    assert (zero_dte["p1"], zero_dte["p2"], zero_dte["n1"], zero_dte["n2"]) == (84000.0, 84500.0, 82500.0, 81500.0)
    assert zero_dte["walls"]["p1"] == {
        "strike": 84000.0,
        "gex_usd": pytest.approx(12413166.529194506),
        "distance_pct": pytest.approx(1.2081359774681788),
    }
    assert zero_dte["volatility_trigger"] == 84000.0
    assert zero_dte["magnet_price"] == 84000.0
    assert "flip" not in json.dumps(sections["gamma_profiles"]["data"])
    weekly = sections["gamma_profiles"]["data"]["weekly"]
    assert (weekly["p1"], weekly["n1"], weekly["volatility_trigger"], weekly["magnet_price"]) == (
        84000.0, 80000.0, 80000.0, 83000.0,
    )

    vol = sections["volatility"]["data"]
    assert vol["pcr"] == 0.69 and vol["pcr_oi"] == 0.696
    assert vol["atm_iv"] == 26.88 and vol["iv_percentile"] == 16.0
    assert vol["term_structure"][0] == {"expiry": "13OCT26", "days_to_expiry": 1.0, "atm_iv": 26.88, "skew_25d": None}
    assert vol["rr_skew"][0] == {
        "tenor": "1d", "days": 1.0, "expiry": "12OCT26", "call_25d_iv": 23.01, "put_25d_iv": 22.63,
        "rr": 0.38, "chg_1d": -0.31, "chg_1w": -0.56, "chg_1m": 7.12, "methodology": "exact_expiry",
    }
    assert vol["rr_skew"][1]["expiry"] is None
    assert vol["rr_skew"][1]["methodology"] == "constant_maturity_variance_interpolation"

    flow = sections["flow"]["data"]
    assert flow["put_call_ratio"] == 0.69
    assert (flow["call_volume"], flow["put_volume"], flow["call_oi"], flow["put_oi"]) == (
        69903.4, 48221.9, 457354.8, 318101.6,
    )
    assert flow["whale_24h"]["net_flow_usd"] == pytest.approx(355603.67684881645)
    assert flow["whale_24h"]["trade_count"] == 16
    assert flow["whale_24h"]["lead_expiry"] == "30OCT26"
    assert flow["whale_24h"]["active_strike"] == 100000.0

    expiry = sections["expiry"]["data"]
    assert expiry["max_pain_price"] == 78000.0
    assert expiry["max_pain_target_expiry"] == "30OCT26"
    assert expiry["next_expiry"]["expiry"] == "12OCT26"
    assert expiry["next_expiry"]["hours_to_expiry"] == 23.1
    assert expiry["next_expiry"]["put_call_ratio_oi"] == 0.432
    assert set(expiry["next_expiry"]) == {
        "expiry", "expires_at", "hours_to_expiry", "put_call_ratio_oi", "max_pain_price",
        "spot_to_max_pain_pct", "call_oi", "put_oi", "notional_usd", "expiry_oi_share_pct",
    }

    metadata = snapshot["metadata"]
    assert metadata["source_mode"] == "public_json"
    assert set(metadata["source_urls"]) == {"gex", "bundle", "options_chain"}
    assert metadata["errors"] == {}
    dumped = json.dumps(snapshot)
    for name in REMOVED:
        assert name not in dumped


def test_fixture_time_semantics_are_per_section(monkeypatch):
    fields = _fetch(monkeypatch, fixture_payloads())["metadata"]["gex_time_semantics"]["fields"]
    assert fields["gex_board.total_net_gex"]["generated_at_ms"] == _ms("2026-10-11T08:52:13.131703Z")
    assert fields["gamma_exposure.p1"]["generated_at_ms"] == _ms("2026-10-11T08:52:13.131703Z")
    assert fields["gamma_exposure.hedging_flows"]["generated_at_ms"] == _ms("2026-10-11T08:52:13.131703Z")
    # gex-latest.dvol has no native clock any more.
    assert fields["gex_board.dvol"]["time_basis"] == "dvol_time_unknown"
    assert fields["gex_board.dvol"]["generated_at_ms"] is None
    # Bundle sections use meta.sections.<name>.data_timestamp, never the
    # bundle-level data_timestamp (which is the oldest section, 08:00:07).
    assert fields["volatility.pcr"]["generated_at_ms"] == _ms("2026-10-11T08:52:53.799Z")
    assert fields["flow.put_call_ratio"]["generated_at_ms"] == _ms("2026-10-11T08:52:53.799Z")
    assert fields["expiry.next_expiry"]["generated_at_ms"] == _ms("2026-10-11T08:52:53.799Z")
    # Sections whose meta data_timestamp is null fall back to their own timestamp.
    assert fields["volatility.atm_iv"]["generated_at_ms"] == _ms("2026-10-11T08:05:00.787Z")
    assert fields["volatility.term_structure"]["generated_at_ms"] == _ms("2026-10-11T08:05:00.787Z")
    whale = fields["flow.whale_24h"]
    assert whale["observed_at_ms"] == _ms("2026-10-11T06:46:34.488Z")
    assert whale["generated_at_ms"] == _ms("2026-10-11T08:53:02.458Z")
    bundle_level = _ms("2026-10-11T08:00:07.583Z")
    assert all(item["generated_at_ms"] != bundle_level for item in fields.values())
    assert all(item["time_errors"] == [] for item in fields.values())


def test_max_pain_epoch_seconds_are_parsed(monkeypatch):
    bundle = bundle_fixture()
    del bundle["meta"]["sections"]["maxPain"]
    fields = _fetch(monkeypatch, fixture_payloads(bundle=bundle))["metadata"]["gex_time_semantics"]["fields"]
    assert fields["expiry.max_pain_price"]["generated_at_ms"] == 1791706501 * 1000
    assert fields["expiry.max_pain_target_expiry"]["time_errors"] == []


def test_whale_zero_trade_window_is_present(monkeypatch):
    snapshot = _fetch(monkeypatch, fixture_payloads())
    flow = snapshot["sections"]["flow"]
    whale_1h = flow["data"]["whale_1h"]
    assert whale_1h["trade_count"] == 0
    assert whale_1h["net_flow_usd"] == 0.0
    assert whale_1h["lead_expiry"] is None and whale_1h["active_strike"] is None
    assert "flow.whale_1h" not in flow["missing_fields"]
    assert flow["field_status"]["flow.whale_1h"]["status"] == "ok"


def test_skew_25d_only_on_exact_expiry_match(monkeypatch):
    bundle = bundle_fixture()
    bundle["ivTerm"]["data"][0]["expiry"] = "12OCT26"
    # An interpolated row naming an ivTerm expiry must not be used.
    bundle["rrSkewSummary"]["rows"][1]["expiry"] = "14OCT26"
    term = _fetch(monkeypatch, fixture_payloads(bundle=bundle))["sections"]["volatility"]["data"]["term_structure"]
    assert term[0]["expiry"] == "12OCT26" and term[0]["skew_25d"] == 0.38
    assert term[1]["expiry"] == "14OCT26" and term[1]["skew_25d"] is None


def test_zero_dte_profile_is_optional(monkeypatch):
    gex = gex_fixture()
    del gex["profiles"]["0dte"]
    profiles = _fetch(monkeypatch, fixture_payloads(gex=gex))["sections"]["gamma_profiles"]
    assert profiles["data"]["zero_dte"] is None
    assert profiles["data"]["weekly"]["p1"] == 84000.0
    assert profiles["missing_fields"] == []
    assert profiles["field_status"]["gamma_profiles.zero_dte"]["status"] == "optional_missing"


def test_empty_pcr_rows_are_missing(monkeypatch):
    bundle = bundle_fixture()
    bundle["pcr"]["data"] = []
    sections = _fetch(monkeypatch, fixture_payloads(bundle=bundle))["sections"]
    assert sections["volatility"]["data"]["pcr"] is None
    assert "volatility.pcr" in sections["volatility"]["missing_fields"]
    assert "flow.put_call_ratio" in sections["flow"]["missing_fields"]


def test_spot_and_dvol_fall_back_to_bundle(monkeypatch):
    gex = {
        "total_gex": 12500000,
        "timestamp": "not-a-time",
        "profiles": {
            "total": {
                "walls": {"p1": {"strike": 80000}},
                "meta": {"flip": 79500, "updateTime": "2026-06-03T08:55:00Z"},
            }
        },
    }
    fields = _fetch(monkeypatch, fixture_payloads(gex=gex))["metadata"]["gex_time_semantics"]["fields"]
    assert fields["gamma_exposure.spot_price"]["source_ref"] == "dashboard-bundle.price.price"
    assert fields["gamma_exposure.spot_price"]["generated_at_ms"] == _ms("2026-10-11T08:52:53.799Z")
    assert fields["gamma_exposure.flip_point"]["source_ref"] == "gex-latest.profiles.total.meta.flip"
    assert fields["gamma_exposure.flip_point"]["generated_at_ms"] == _ms("2026-06-03T08:55:00Z")
    assert fields["gex_board.dvol"]["source_ref"] == "dashboard-bundle.dvol.value"
    assert fields["gex_board.total_net_gex"]["generated_at_ms"] is None
    assert fields["gex_board.total_net_gex"]["time_errors"] == ["generated_at_parse_failed:gex-latest.total_gex"]


def test_dvol_native_time_when_upstream_provides_it(monkeypatch):
    gex = gex_fixture()
    gex["dvol_source_timestamp"] = "2026-10-11T08:50:00Z"
    gex["dvol_timestamp"] = "2026-10-11T08:51:00Z"
    dvol = _fetch(monkeypatch, fixture_payloads(gex=gex))["metadata"]["gex_time_semantics"]["fields"]["gex_board.dvol"]
    assert dvol["time_basis"] == "dvol_native_time"
    assert dvol["observed_at_ms"] == _ms("2026-10-11T08:50:00Z")
    assert dvol["generated_at_ms"] == _ms("2026-10-11T08:51:00Z")


def test_fetched_only_dvol_time_is_unknown_not_error(monkeypatch):
    gex = gex_fixture()  # a valid gex payload whose dvol has no native clock
    gex["dvol"], gex["dvol_timestamp"], gex["dvol_source_timestamp"] = 39.5, None, None
    payloads = fixture_payloads(gex=gex, bundle={})
    dvol = _fetch(monkeypatch, payloads)["metadata"]["gex_time_semantics"]["fields"]["gex_board.dvol"]
    assert dvol["source_ref"] == "gex-latest.dvol"
    assert dvol["observed_at_ms"] is None and dvol["generated_at_ms"] is None
    assert dvol["fetched_at_ms"] is not None
    assert dvol["time_basis"] == "dvol_time_unknown"
    assert dvol["time_errors"] == []


def test_primary_failures_use_gex_and_bundle_error_keys(monkeypatch):
    def fail_urlopen(request, timeout):
        raise OSError("blocked")

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)
    source = PublicJsonSource(Settings(asset="BTC", request_timeout_seconds=1))
    snapshot = asyncio.run(source.fetch_snapshot())
    assert set(snapshot["metadata"]["errors"]) == {"gex", "bundle", "options_chain"}
    assert "gex_board.total_net_gex" in snapshot["sections"]["gex_board"]["missing_fields"]
    assert "volatility.pcr" in snapshot["sections"]["volatility"]["missing_fields"]
    fields = snapshot["metadata"]["gex_time_semantics"]["fields"]
    assert fields["gex_board.total_net_gex"]["fetched_at_ms"] is None
    assert set(fields) == {f"{s}.{f}" for s in SECTIONS for f in SECTION_FIELDS[s]}


def test_source_time_requires_timezone_or_valid_epoch():
    from gexmonitorapi.json_source import _time_to_ms
    for bad in (True, -1, 0, float("nan"), "2026-09-11T12:00:00"):
        value, errors = _time_to_ms(bad, "test.source", "generated_at")
        assert value is None
        assert errors
