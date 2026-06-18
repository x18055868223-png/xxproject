from __future__ import annotations

from gexmonitorapi.parsers import parse_section

# Snippets below mirror the real rendered text of gexmonitor.com BTC analytics tabs
# (labels, ordering, and number formats like "$-67M", "1.26x", "PCR (VOLUME) 0.93").


def test_parse_gex_board_key_metrics() -> None:
    parsed = parse_section(
        "gex_board",
        """
        STRUCTURE SNAPSHOT TIME 6/3 17:35
        DVOL 42.9%
        TOTAL NET GEX $-67M MM Short Gamma
        GAMMA FLIP POINT $67,372.727
        MARKET STATE Critical High volatility expected
        """,
    )

    assert parsed.data == {
        "total_net_gex": -67000000.0,
        "dvol": 42.9,
        "market_state": "negative_gamma",
    }
    assert parsed.missing_fields == []


def test_parse_gamma_exposure_key_levels() -> None:
    parsed = parse_section(
        "gamma_exposure",
        """
        SPOT $66,684.5 FLIP 67,372.727 P1 P2 N1 N2 VOL TRIGGER A1 A2 V
        GAMMA COMPONENTS
        35K 106K SPOT
        N2 60,000 -10.0% 17.5M
        N1 65,000 -2.5% 22.0M
        FLIP 67,372.727 +1.0% Negative
        VOL TRIGGER 65,000 -2.5%
        SPOT PRICE 66,684.5
        Filtered Net GEX -66.7M
        MAGNET 70,000 +5.0%
        P1 80,000 +20.0% 14.3M
        P2 82,000 +23.0% 5.3M
        """,
    )

    assert parsed.data == {
        "n2": 60000.0,
        "n1": 65000.0,
        "flip_point": 67372.727,
        "volatility_trigger": 65000.0,
        "spot_price": 66684.5,
        "magnet_price": 70000.0,
        "p1": 80000.0,
        "p2": 82000.0,
    }
    assert parsed.missing_fields == []


def test_gamma_exposure_ignores_chart_legend_noise() -> None:
    # The legend "P1 P2 N1 N2" before GAMMA COMPONENTS must not be mistaken for values.
    parsed = parse_section(
        "gamma_exposure",
        "P1 P2 N1 N2 VOL TRIGGER A1 A2 V GAMMA COMPONENTS N2 60,000 P1 80,000",
    )
    assert parsed.data["n2"] == 60000.0
    assert parsed.data["p1"] == 80000.0


def test_parse_volatility_metrics_and_term_structure() -> None:
    parsed = parse_section(
        "volatility",
        """
        DVOL INDEX -0.3% 43
        IV/RV RATIO 1.26x
        PCR (VOLUME) 0.93
        TERM STRUCTURE 4 JUN 5 JUN 6 JUN
        Term Details
        4 JUN 26 1d to expiry 47.2% -12.8% ± $1,315 (2%) Trade
        5 JUN 26 2d to expiry 50.1% -10.6% ± $1,973 (3%) Trade
        """,
    )

    assert parsed.data["iv_rv_ratio"] == 1.26
    assert parsed.data["pcr"] == 0.93
    assert parsed.data["term_structure"] == [
        {"expiry": "4 JUN 26", "atm_iv": 47.2, "skew_25d": -12.8},
        {"expiry": "5 JUN 26", "atm_iv": 50.1, "skew_25d": -10.6},
    ]
    assert parsed.missing_fields == []


def test_parse_flow_metrics() -> None:
    parsed = parse_section(
        "flow",
        """
        CALL PREMIUM $27.7M
        PUT PREMIUM $51.8M
        NET PREMIUM -$24.2M
        P/C RATIO 1.88
        FLOW READ Put-led selling is setting the tone. 50 recent anomaly signals still need confirmation.
        CALL / PUT TILT 35% Call
        Time: 1H 4H 24H 7D
        """,
    )

    assert parsed.data == {
        "call_premium": 27700000.0,
        "put_premium": 51800000.0,
        "call_put_bias": "35% Call",
        "put_call_ratio": 1.88,
        "abnormal_signal": "Put-led selling is setting the tone.",
    }
    assert parsed.missing_fields == []


def test_missing_fields_are_reported_with_reasons() -> None:
    parsed = parse_section("gamma_exposure", "Gamma Exposure 加载中...")

    assert parsed.data["n2"] is None
    assert "gamma_exposure.n2" in parsed.missing_fields
    assert parsed.field_status["gamma_exposure.n2"] == {
        "status": "missing",
        "reason": "not_found_in_rendered_page",
    }
