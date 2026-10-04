import copy
import json
import pathlib
import sys
from datetime import datetime, timezone

from astra_workbench_frontend_harness import render_workbench
from test_signal_comfort_frontend import fixture_old_card
from test_signal_evidence_v221_frontend import card_with_times

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import astra_underwriting_v20 as core
import astra_underwriting_guidance_v20 as guidance
import astra_workbench_preview_v20 as preview


VALUATION_MS = int(datetime(2026, 9, 25, 8, tzinfo=timezone.utc).timestamp() * 1000)
EXPIRY_MS = int(datetime(2026, 9, 26, 8, tzinfo=timezone.utc).timestamp() * 1000)
FACT_HASH = "b" * 64
SOURCE_HASH = "c" * 64


def leg_meta(name, strike, option_type="put"):
    return {
        "kind": "option",
        "base_currency": "BTC",
        "settlement_currency": "BTC",
        "quote_currency": "BTC",
        "counter_currency": "USD",
        "price_index": "btc_usd",
        "option_type": option_type,
        "instrument_type": "reversed",
        "state": "open",
        "expiration_timestamp": EXPIRY_MS,
        "instrument_name": name,
        "strike": strike,
        "contract_size": 1.0,
        "min_trade_amount": 0.01,
    }


def book(name, bid, ask, *, timestamp=VALUATION_MS - 800):
    return {
        "instrument_name": name,
        "state": "open",
        "timestamp": timestamp,
        "index_price": 100000,
        "bids": [[bid, 0.4]],
        "asks": [[ask, 0.4]],
    }


def base_payload(*, include_fee=True, include_risk=True, include_fact=True, data_kind="historical_replay"):
    candidate = {
        "short_instrument": "BTC-26SEP26-97000-P",
        "long_instrument": "BTC-26SEP26-95000-P",
        "quantity_btc": 0.1,
        "exit_policy": "hold_to_expiry",
    }
    metadata = {
        "short": leg_meta("BTC-26SEP26-97000-P", 97000),
        "long": leg_meta("BTC-26SEP26-95000-P", 95000),
    }
    fact_context = {
        "schema": "astra_fmz_fact_context@2.0.0",
        "status": "current",
        "underlying": "BTC",
        "source_hash": FACT_HASH,
        "snapshot_ts_ms": VALUATION_MS - 1000,
        "valuation_ts_ms": VALUATION_MS,
        "runtime_mode": "live_public_read_only",
        "current_price_usd": 100000,
        "current_price_source": "deribit_index",
        "price_observed_ms": VALUATION_MS - 1200,
        "market_facts": {
            "anchor": {
                "effective_flip_point": 100450,
                "anchor_gravity_ref_score": 0.62,
            },
            "flow": {
                "tmv_blend": -0.21,
                "direction": "DOWN",
            },
            "gamma_regime": {
                "market_state": "NEUTRAL",
                "net_gamma_notional_usd": 12500000,
                "pin_strike": 100000,
            },
            "edb": {
                "edb_score": -0.14,
            },
            "neutral_repair": {
                "state": "IDLE",
                "is_active": False,
            },
            "volatility": {
                "level": "background_only",
            },
            "liquidity": {
                "spread_bps": 9.5,
                "depth_btc": 0.4,
            },
        },
        "gap_reasons": [],
    }
    payload = {
        "origins": ["manual", "natural_nr"],
        "data_identity": {"kind": data_kind, "source_sha256": SOURCE_HASH},
        "candidate": candidate,
        "metadata": metadata,
        "market": {
            "valuation_ts_ms": VALUATION_MS,
            "reference_price_usd": 100000,
            "reference_source": "deribit_index",
            "received_ts_ms": VALUATION_MS - 500,
            "leg_received_ts_ms": {"short": VALUATION_MS - 500, "long": VALUATION_MS - 500},
            "short_book": book("BTC-26SEP26-97000-P", 0.0075, 0.0077, timestamp=VALUATION_MS - 800),
            "long_book": book("BTC-26SEP26-95000-P", 0.0033, 0.0035, timestamp=VALUATION_MS - 900),
        },
    }
    if include_fee:
        payload["fee"] = {
            "amount_btc": 0.0000375,
            "basis": "configured_assumption",
            "covers": ["entry", "delivery"],
        }
    if include_fact:
        payload["fmz_fact_context"] = fact_context
    if include_risk:
        contract = core.candidate_identity(candidate, metadata, VALUATION_MS)
        payload["risk"] = {
            "status": "supported",
            "support_verified": True,
            "source_kind": "frozen_model",
            "source_fact_hash": FACT_HASH,
            "candidate_id": contract["candidate_id"],
            "unit": "BTC",
            "model_id": "qualified-risk-v20",
            "model_hash": "7" * 64,
            "input_ts_ms": VALUATION_MS,
            "feature_end_ts_ms": VALUATION_MS - 60000,
            "reference_price_usd": 100000,
            "quantity_btc": 0.1,
            "horizon_hours": 24,
            "mu_btc": 0.00025,
            "calibration_id": "cal-202609",
            "support_scope": "same-candidate",
            "input_row_hash": "8" * 64,
            "normalization_basis": "same_snapshot_inverse_btc",
            "training_cutoff": "2026-08-31",
            "label_known_cutoff": "2026-09-13",
        }
    return payload


def evaluated_snapshot(**kwargs):
    return core.evaluate(base_payload(**kwargs))


def card_with_snapshot(snapshot):
    card = card_with_times()
    card["underwriting_snapshot"] = copy.deepcopy(snapshot)
    return card


def attach_same_version_review(card, snapshot):
    review = {
        "schema": "astra_underwriting_causal_review@2.0.0",
        "status": "OK",
        "reviewed_snapshot_id": snapshot["snapshot_id"],
        "candidate_id": snapshot["candidate_id"],
        "packet_hash": core._hash(core.llm_applicability_packet(snapshot)),
        "http_calls": 1,
        "applicability": "CONTESTED",
        "supports": [{"ref": "quote", "claim_cn": "盘口、费用与冻结风险参考绑定同一候选。"}],
        "counters": [{"ref": "fmz.flow", "claim_cn": "上方主动买入恢复会挑战当前保单。"}],
        "gaps_cn": ["账户保证金仍未提供。"],
        "reassess_if_cn": ["报价或 FMZ 事实刷新后重建快照。"],
        "summary_cn": "责任与补偿可核查，但适用性仍有反证。",
    }
    card["review_annex"] = {
        "schema": "astra_underwriting_card_annex@2.0.0",
        "reviewed_snapshot_id": snapshot["snapshot_id"],
        "status": "attached",
        "review": copy.deepcopy(review),
    }
    card["underwriting_review"] = copy.deepcopy(review)



def _candidate(snapshot, *, review=None):
    return {
        "side": snapshot.get("side") or snapshot.get("contract", {}).get("side") or "put",
        "status": "snapshot",
        "candidate_id": snapshot["candidate_id"],
        "snapshot": copy.deepcopy(snapshot),
        "remaining_to_expiry_ms": snapshot["contract"]["expiry_ms"] - VALUATION_MS,
        "review": copy.deepcopy(review or {"status": "not_attached"}),
        "review_gate": {"enabled": False, "reasons": ["test harness read only"]},
        "reading_guidance": guidance.build_reading_guidance(snapshot, snapshot.get("fact_context") or {}),
    }


def _review_result(snapshot, review=None, *, status="OK", snapshot_id=None, http_calls=1):
    payload = {
        "schema": "astra_underwriting_card_annex@2.0.0",
        "status": status,
        "candidate_id": snapshot["candidate_id"],
        "side": snapshot["contract"]["side"],
        "snapshot_id": snapshot_id or snapshot["snapshot_id"],
        "reviewed_snapshot_id": snapshot_id or snapshot["snapshot_id"],
        "reviewed_at_ms": VALUATION_MS + 1,
        "http_calls": http_calls,
    }
    if review is not None:
        payload["review"] = copy.deepcopy(review)
    return payload


def _workbench(snapshot=None, *, review=None, last_result=None, include_candidate=True, history_state=None):
    view = preview.build_preview(VALUATION_MS, scenario_id="W1")
    view["preview"] = {"kind": "local_current_workbench_test", "mode": "CURRENT", "label": "当前工作台测试"}
    view["manual_review_state"] = {
        "inflight": None,
        "last_result": copy.deepcopy(last_result),
        "cooldown_remaining_ms": 0,
        "daily_http_budget": None,
    }
    if snapshot is not None and snapshot.get("fact_context"):
        view["market_context"] = copy.deepcopy(snapshot["fact_context"])
    if include_candidate and snapshot is not None and snapshot.get("schema") == core.SCHEMA:
        view["candidates"] = [_candidate(snapshot, review=review)]
    else:
        view["candidates"] = []
    if history_state:
        view["__test_state"] = history_state
    return render_workbench(view)["areas"]


def _attached_review(snapshot):
    card = card_with_snapshot(snapshot)
    attach_same_version_review(card, snapshot)
    return card["underwriting_review"]


def test_underwriting_snapshot_renders_real_evaluate_contract_liability_and_bound_review():
    snapshot = evaluated_snapshot(data_kind="live_public_capture")
    review = _attached_review(snapshot)

    areas = _workbench(snapshot, review=review)
    panel = areas["candidateArea"]

    assert "Put 候选" in panel
    assert "BTC-26SEP26-97000-P" in panel
    assert "BTC-26SEP26-95000-P" in panel
    assert "0.1 BTC" in panel
    assert "腿宽 2,000 USD" in panel
    assert "2026/9/26" in panel
    assert "24 小时" in panel
    assert "权利金差 c" in panel
    assert "0.0004 BTC" in panel
    assert "总费用 f" in panel
    assert "0.0000375 BTC" in panel
    assert "冻结赔付 μ_ref" in panel
    assert "0.00025 BTC" in panel
    assert "参考余量 M_ref" in panel
    assert "0.0001125 BTC" in panel
    assert "到期盈亏平衡价" in panel
    assert "96,649.65 USD" in panel
    assert "支付表" in panel
    assert "同版评审：完成" in panel
    assert "责任与补偿可核查" in panel
    assert "同截面只读提示" in panel
    assert "执行许可" in panel


def test_underwriting_snapshot_missing_inputs_use_real_evaluate_gaps_without_review_promotion():
    snapshot = evaluated_snapshot(include_fee=False, include_risk=False, include_fact=False)

    panel = _workbench(snapshot)["candidateArea"]

    assert "资料缺口" in panel or "缺口/反证" in panel
    assert "理论费用缺失" in panel
    assert "冻结赔付参考缺失" in panel
    assert "冻结赔付参考缺失" in panel
    assert "同版评审：未绑定同版评审" in panel
    assert "0.0001125 BTC" not in panel
    assert "同版评审：完成" not in panel


def test_rejected_same_snapshot_review_attempt_is_visible_without_fabricated_body():
    snapshot = evaluated_snapshot(data_kind="live_public_capture", include_risk=False)
    rejected = _review_result(snapshot, None, status="rejected", http_calls=2)

    manual = _workbench(snapshot, last_result=rejected)["manualReviewState"]

    assert "本次无合格评审正文：拒绝" in manual
    assert "页面不会自动补发 LLM" in manual
    assert "Q1 ·" not in manual
    assert "Q2 ·" not in manual
    assert "因果解释可用" not in manual


def test_underwriting_review_body_is_hidden_from_candidate_when_snapshot_binding_mismatches():
    snapshot = evaluated_snapshot(data_kind="live_public_capture")
    review = _attached_review(snapshot)
    previous = _review_result(snapshot, review, snapshot_id="d" * 64)

    areas = _workbench(snapshot, last_result=previous)

    assert "同版评审：未绑定同版评审" in areas["candidateArea"]
    assert "盘口、费用与冻结风险参考绑定同一候选" not in areas["candidateArea"]
    assert "上一次冻结保单" in areas["manualReviewState"]
    assert "盘口、费用与冻结风险参考绑定同一候选" in areas["manualReviewState"]


def test_underwriting_review_body_is_hidden_for_wrong_schema_or_historical_card():
    snapshot = evaluated_snapshot(data_kind="live_public_capture")
    review = _attached_review(snapshot)
    wrong = copy.deepcopy(snapshot)
    wrong["schema"] = "astra_underwriting_snapshot@1.0.0"

    wrong_panel = _workbench(wrong, review=review)["candidateArea"]
    assert "工作台服务尚未返回 Put/Call 候选" in wrong_panel
    assert "盘口、费用与冻结风险参考绑定同一候选" not in wrong_panel

    historical = evaluated_snapshot()
    historical_panel = _workbench(historical)["candidateArea"]
    assert "冻结回放候选" in historical_panel
    assert "同版评审：未绑定同版评审" in historical_panel
    assert "盘口、费用与冻结风险参考绑定同一候选" not in historical_panel


def test_old_or_wrong_schema_cards_keep_history_raw_readable_without_candidate_promotion():
    old_card = fixture_old_card()
    raw_text = json.dumps(old_card, ensure_ascii=False, indent=2)
    areas = _workbench(None, include_candidate=False, history_state={
        "historyLoaded": True,
        "historyItems": [{"id": "old-card", "path": "signal_cards/old-card.json", "time": "2026-09-25", "symbol": "BTC", "quality": "OK"}],
        "rawCard": {"title": "历史原始 JSON · LLM 旧卡", "text": raw_text},
    })

    assert "工作台服务尚未返回 Put/Call 候选" in areas["candidateArea"]
    assert "历史卡 1" in areas["historyArea"]
    assert "历史原始 JSON" in areas["historyArea"]
    assert "Astra 舒适度测试卡" in areas["historyArea"]
    assert "当前保单与补偿" not in areas["candidateArea"]
