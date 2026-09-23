import copy
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from astra_joint_contract import ASSESSMENT_SCHEMA
from astra_joint_projection import (
    DISPLAY_SCHEMA,
    SUMMARY_SCHEMA,
    build_joint_projection,
    compute_assessment_hash,
    validate_assessment,
)

AS_OF_MS = 1781751600000
EXPIRY_MS = AS_OF_MS + 14 * 60 * 60 * 1000
SOURCE_HASH = "sha256:" + "a" * 64


def make_assessment(status="available"):
    assessment = {
        "schema": ASSESSMENT_SCHEMA,
        "status": status,
        "event_id": "CARD-STAT-1",
        "symbol": "BTC",
        "scope_cn": "统计研究只用于影子验证；不改变 D-S 评级、原信号窗口或交易权限。",
        "provenance": {
            "as_of_ms": AS_OF_MS,
            "source_record_hash": SOURCE_HASH,
            "input_hash": "sha256:" + "b" * 64,
            "model_id": "gam-l2-two-part@research",
            "model_hash": "sha256:" + "c" * 64,
            "training_cutoff_ms": AS_OF_MS - 86400000,
        },
        "sides": {
            "put": {
                "status": status,
                "probability_positive": 0.22,
                "conditional_positive_loss": 0.35,
                "expected_loss_normalized": 0.077,
                "expected_payout_btc": 0.077 * 2000 / 79000,
                "reference": {
                    "short_strike": 77000,
                    "long_strike": 75000,
                    "width": 2000,
                    "entry_price": 79000,
                    "expiry_ms": EXPIRY_MS,
                },
                "quote": {"status": "not_collected"},
                "uncertainty_cn": "样本支持一般。",
                "scope_cn": "仅适用于封存研究口径。",
            },
            "call": {
                "status": status,
                "probability_positive": 0.31,
                "conditional_positive_loss": 0.43,
                "expected_loss_normalized": 0.1333,
                "expected_payout_btc": 0.1333 * 2000 / 79000,
                "reference": {
                    "short_strike": 81000,
                    "long_strike": 83000,
                    "width": 2000,
                    "entry_price": 79000,
                    "expiry_ms": EXPIRY_MS,
                },
                "quote": {"status": "not_collected"},
                "uncertainty_cn": "尾部样本偏少。",
                "scope_cn": "仅适用于封存研究口径。",
            },
        },
    }
    assessment["assessment_hash"] = compute_assessment_hash(assessment)
    return assessment


def make_card():
    return {
        "identity": {
            "card_id": "CARD-STAT-1",
            "symbol": "BTC",
            "confirmed_time_ms": AS_OF_MS,
            "source_record_hash": SOURCE_HASH,
        }
    }


def assert_raises(fn, text):
    try:
        fn()
    except ValueError as exc:
        if text not in str(exc):
            raise AssertionError(f"expected {text!r} in {exc!r}")
        return
    raise AssertionError(f"expected ValueError containing {text!r}")


def test_validate_returns_clone_and_builds_bound_projection():
    assessment = make_assessment()
    validated = validate_assessment(assessment, make_card())
    assert validated == assessment and validated is not assessment
    validated["status"] = "changed"
    assert assessment["status"] == "available"

    projection = build_joint_projection(assessment, make_card())
    assert projection["summary"]["schema_version"] == SUMMARY_SCHEMA
    assert projection["detail"]["schema_version"] == DISPLAY_SCHEMA
    assert projection["summary"]["detail_projection_hash"] == projection["detail"]["display_projection_hash"]
    assert projection["summary"]["risk_comparison"]["relative_side"] == "put"
    assert "模型估计 Put 赔付较低" in projection["detail"]["summary_cn"]
    assert "更值得卖" not in projection["detail"]["quote_comparison_cn"]
    assert projection["detail"]["sides"]["put"]["probability_cn"] == "模型估计赔付发生概率 22.0%"


def test_card_hash_time_and_assessment_hash_are_hard_checked():
    mismatch = make_card()
    mismatch["identity"]["source_record_hash"] = "sha256:" + "d" * 64
    assert_raises(lambda: validate_assessment(make_assessment(), mismatch), "source_record_hash")

    later = make_assessment()
    later["provenance"]["as_of_ms"] = AS_OF_MS + 1
    later["assessment_hash"] = compute_assessment_hash(later)
    assert_raises(lambda: validate_assessment(later, make_card()), "later than card time")

    tampered = make_assessment()
    tampered["symbol"] = "ETH"
    assert_raises(lambda: validate_assessment(tampered, make_card()), "assessment_hash mismatch")

    bad_cutoff = make_assessment()
    bad_cutoff["provenance"]["training_cutoff_ms"] = AS_OF_MS + 1
    bad_cutoff["assessment_hash"] = compute_assessment_hash(bad_cutoff)
    assert_raises(lambda: validate_assessment(bad_cutoff, make_card()), "training_cutoff_ms")


def test_unavailable_status_allows_missing_predictions():
    assessment = make_assessment(status="unavailable")
    assessment["reason_cn"] = "模型制品暂不可用。"
    assessment["sides"] = {"put": {"status": "unavailable"}, "call": {"status": "unavailable"}}
    assessment["assessment_hash"] = compute_assessment_hash(assessment)
    projection = build_joint_projection(assessment, make_card())
    assert projection["summary"]["status"] == "unavailable"
    assert projection["detail"]["summary_cn"] == "模型制品暂不可用。"
    assert projection["detail"]["sides"]["put"]["probability_positive"] is None
    assert "参考两腿尚未确认" in projection["detail"]["sides"]["put"]["reference_cn"]


def attach_quote(assessment, side, net_credit, checked_delta_ms=3000, max_age_ms=2000, skew_ms=1000):
    payout = assessment["sides"][side]["expected_payout_btc"]
    assessment["sides"][side]["quote"] = {
        "status": "available",
        "net_credit_btc": net_credit,
        "expected_net_btc": net_credit - payout,
        "observed_at_ms": AS_OF_MS + checked_delta_ms - max_age_ms,
        "checked_at_ms": AS_OF_MS + checked_delta_ms,
        "max_age_ms": max_age_ms,
        "leg_timestamp_skew_ms": skew_ms,
    }


def test_negative_credit_cannot_be_flagged_eligible():
    assessment = make_assessment()
    attach_quote(assessment, 'put', -0.001)
    assessment['sides']['put']['quote']['credit_eligible'] = True
    assessment['assessment_hash'] = compute_assessment_hash(assessment)
    assert_raises(lambda: validate_assessment(assessment), 'credit_eligible')
    assessment['sides']['put']['quote']['credit_eligible'] = False
    assessment['sides']['put']['quote']['strict_net_result_ready'] = True
    assessment['assessment_hash'] = compute_assessment_hash(assessment)
    assert_raises(lambda: validate_assessment(assessment), 'requires positive net credit')


def test_quote_after_market_asof_is_valid_when_checked_quote_is_strictly_fresh():
    assessment = make_assessment()
    attach_quote(assessment, "put", 0.0035)
    attach_quote(assessment, "call", 0.0037)
    assessment["assessment_hash"] = compute_assessment_hash(assessment)
    projection = build_joint_projection(assessment, make_card())
    assert projection["detail"]["risk_comparison"]["relative_side"] == "put"
    assert projection["detail"]["sides"]["put"]["quote_status"] == "available"
    assert "同期补偿" in projection["detail"]["sides"]["put"]["quote_cn"]
    assert "纸面净值" in projection["detail"]["quote_comparison_cn"]


def test_stale_or_unitless_quote_is_rejected_instead_of_retimed():
    stale = make_assessment()
    attach_quote(stale, "put", 0.0035, max_age_ms=10001)
    stale["assessment_hash"] = compute_assessment_hash(stale)
    assert_raises(lambda: validate_assessment(stale, make_card()), "quote age limit")

    unitless = make_assessment()
    unitless["sides"]["put"]["quote"] = {
        "status": "available",
        "net_credit_btc": 0.0035,
        "expected_net_btc": 0.0035 - unitless["sides"]["put"]["expected_payout_btc"],
        "checked_at_ms": AS_OF_MS + 3000,
        "age": 2,
        "leg_timestamp_skew_ms": 1000,
    }
    unitless["assessment_hash"] = compute_assessment_hash(unitless)
    assert_raises(lambda: validate_assessment(unitless, make_card()), "millisecond units")


def test_expected_loss_and_reference_scaling_are_hard_checked():
    bad_relation = make_assessment()
    bad_relation["sides"]["put"]["expected_loss_normalized"] = 0.09
    bad_relation["assessment_hash"] = compute_assessment_hash(bad_relation)
    assert_raises(lambda: validate_assessment(bad_relation, make_card()), "probability_positive times")

    bad_scale = make_assessment()
    bad_scale["sides"]["call"]["expected_payout_btc"] += 0.001
    bad_scale["assessment_hash"] = compute_assessment_hash(bad_scale)
    assert_raises(lambda: validate_assessment(bad_scale, make_card()), "width over entry_price")


def test_geometry_requires_shared_expiry_and_side_shape():
    wrong_put = make_assessment()
    wrong_put["sides"]["put"]["reference"]["long_strike"] = 79000
    wrong_put["assessment_hash"] = compute_assessment_hash(wrong_put)
    assert_raises(lambda: validate_assessment(wrong_put, make_card()), "put reference")

    wrong_width = make_assessment()
    wrong_width["sides"]["call"]["reference"]["width"] = 1500
    wrong_width["assessment_hash"] = compute_assessment_hash(wrong_width)
    assert_raises(lambda: validate_assessment(wrong_width, make_card()), "width must match")

    wrong_expiry = make_assessment()
    wrong_expiry["sides"]["call"]["reference"]["expiry_ms"] = EXPIRY_MS + 60000
    wrong_expiry["assessment_hash"] = compute_assessment_hash(wrong_expiry)
    assert_raises(lambda: validate_assessment(wrong_expiry, make_card()), "share the same target expiry")


def main():
    test_validate_returns_clone_and_builds_bound_projection()
    test_card_hash_time_and_assessment_hash_are_hard_checked()
    test_unavailable_status_allows_missing_predictions()
    test_quote_after_market_asof_is_valid_when_checked_quote_is_strictly_fresh()
    test_stale_or_unitless_quote_is_rejected_instead_of_retimed()
    test_expected_loss_and_reference_scaling_are_hard_checked()
    test_geometry_requires_shared_expiry_and_side_shape()
    print("astra_joint_projection: PASS")


if __name__ == "__main__":
    main()
