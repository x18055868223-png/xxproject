import sys

from test_signal_comfort_frontend import (
    assert_no_machine_leak,
    assert_true,
    base_card,
    render_cards,
)


def native_signal_rating():
    return {
        "schema": "signal_rating@1.0.0",
        "rating_scope": "side_environment_v1",
        "candidate_quote_economics": "not_evaluated",
        "as_of_ms": 1781751600000,
        "claims": {
            "structure": {
                "status": "SUPPORTED",
                "summary_cn": "结构有支持，但这只是 producer 原生四态证据。",
                "source_refs": ["factor_cross_section.gamma_regime"],
            },
            "put_pressure": {
                "status": "CONFLICTED",
                "summary_cn": "Put 侧存在冲突。",
                "source_refs": ["factor_cross_section.tmvf"],
            },
            "call_pressure": {
                "status": "SUPPORTED",
                "summary_cn": "Call 侧有支持。",
                "source_refs": ["factor_cross_section.funding"],
            },
        },
    }


def main():
    card = base_card("NATIVE-RATING-ONLY", comfort=None)
    card["signal_rating"] = native_signal_rating()
    rendered = render_cards([card])
    text = rendered["documentText"]
    assert_true("历史版本未综合评级" in text,
                "native producer rating alone must not create D-S comfort grade")
    assert_true("A级" not in text and "S级" not in text,
                "native four-state evidence should not imply admission in the main page")
    assert_true("中文市场证据" in text,
                "native-only cards should still render Chinese market facts")
    assert_no_machine_leak(rendered, context="native signal_rating compatibility")
    print("signal_rating_frontend: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_rating_frontend: FAIL - " + str(exc))
        sys.exit(1)
