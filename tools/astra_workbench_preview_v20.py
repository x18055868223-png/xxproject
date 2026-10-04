"""Loopback-only finite workbench preview; no FMZ, exchange, or LLM access.

The service exercises the same deterministic underwriting, MAP binding, and
bounded review validator used by the real workbench, but every input remains a
local synthetic scenario. It deliberately does not import the production
workbench service or accept credentials.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import threading
import time
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlparse

if __package__:
    from . import astra_map_v24 as maps
    from . import astra_underwriting_fact_context_v20 as fact_context
    from . import astra_underwriting_guidance_v20 as guidance
    from . import astra_underwriting_review_v20 as review_core
    from . import astra_underwriting_v20 as underwriting
else:
    import astra_map_v24 as maps
    import astra_underwriting_fact_context_v20 as fact_context
    import astra_underwriting_guidance_v20 as guidance
    import astra_underwriting_review_v20 as review_core
    import astra_underwriting_v20 as underwriting

FRONTEND = Path(__file__).resolve().parents[1] / "deploy" / "signal_audit" / "frontend"
SCHEMA = "astra_workbench@2.0.0"
DEFAULT_SCENARIO = "W1"
SCENARIO_ALIASES = {"", "NORMAL", "FIXTURE"}
QUANTITY_BTC = 0.1
WIDTH_USD = 2_000
REFERENCE_PRICE_USD = 100_000
LONG_ASK_BTC = 0.0030

SCENARIOS: dict[str, dict[str, Any]] = {
    "W1": {
        "label_cn": "W1 正余量：轻微信用补偿",
        "status_cn": "M_ref 为正，模拟研究可解释",
        "credit_btc": 0.00023,
        "mu_btc": 0.00018,
        "fee_btc": 0.00002,
        "applicability": "EXPLAINED",
        "flow": "UP",
        "space_eligible": True,
    },
    "W2": {
        "label_cn": "W2 正余量：补偿较厚但仍需反证",
        "status_cn": "M_ref 为正，竞争解释保留",
        "credit_btc": 0.00050,
        "mu_btc": 0.00034,
        "fee_btc": 0.00002,
        "applicability": "CONTESTED",
        "flow": "UP",
        "conflict_level": "MILD",
        "space_eligible": True,
    },
    "W3": {
        "label_cn": "W3 负余量：风险参考吃掉补偿",
        "status_cn": "M_ref 为负，不能解释成可承保",
        "credit_btc": 0.00050,
        "mu_btc": 0.00062,
        "fee_btc": 0.00002,
        "applicability": "UNABLE_TO_JUDGE",
        "flow": "NEUTRAL",
        "space_eligible": True,
    },
    "W4": {
        "label_cn": "W4 薄信用：补偿不足",
        "status_cn": "M_ref 为负，薄信用需重定价",
        "credit_btc": 0.00015,
        "mu_btc": 0.00018,
        "fee_btc": 0.00002,
        "applicability": "CONTESTED",
        "flow": "DOWN",
        "conflict_level": "MILD",
        "space_eligible": True,
    },
    "W5": {
        "label_cn": "W5 主门关闭：负 NetGamma 研究诊断",
        "status_cn": "主 SpaceGate 关闭；只允许隔离研究诊断",
        "credit_btc": 0.00050,
        "mu_btc": 0.00034,
        "fee_btc": 0.00002,
        "applicability": "CONTESTED",
        "flow": "UP",
        "conflict_level": "MILD",
        "space_eligible": False,
        "space_reasons": ["negative_net_gamma_main_gate_closed"],
    },
    "MISSING_MU": {
        "label_cn": "缺 μ：风险参考缺失",
        "status_cn": "冻结风险均值缺失；不能排序补偿",
        "credit_btc": 0.00050,
        "mu_btc": None,
        "fee_btc": 0.00002,
        "applicability": "UNABLE_TO_JUDGE",
        "flow": "NEUTRAL",
        "space_eligible": True,
    },
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _scenario_id(value: Any) -> str:
    raw = str(value or DEFAULT_SCENARIO).strip().upper()
    if raw in SCENARIO_ALIASES:
        raw = DEFAULT_SCENARIO
    if raw not in SCENARIOS:
        raise ValueError("unknown preview scenario")
    return raw


def _scenario_options() -> list[dict[str, str]]:
    return [
        {"id": scenario_id, "label_cn": item["label_cn"], "status_cn": item["status_cn"]}
        for scenario_id, item in SCENARIOS.items()
    ]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _expiry_ms(now: int) -> int:
    valuation = datetime.fromtimestamp(now / 1000, timezone.utc)
    minimum = valuation + timedelta(hours=24)
    expiry = minimum.replace(hour=8, minute=0, second=0, microsecond=0)
    if expiry < minimum:
        expiry += timedelta(days=1)
    return int(expiry.timestamp() * 1000)


def _instrument(side: str, strike: int, expiry_ms: int) -> str:
    expiry = datetime.fromtimestamp(expiry_ms / 1000, timezone.utc)
    code = expiry.strftime("%d%b%y").upper().lstrip("0")
    return f"BTC-{code}-{strike}-{'P' if side == 'put' else 'C'}"


def _leg_meta(side: str, strike: int, expiry_ms: int) -> dict[str, Any]:
    name = _instrument(side, strike, expiry_ms)
    return {
        "kind": "option",
        "base_currency": "BTC",
        "settlement_currency": "BTC",
        "quote_currency": "BTC",
        "counter_currency": "USD",
        "price_index": "btc_usd",
        "option_type": side,
        "instrument_type": "reversed",
        "state": "open",
        "expiration_timestamp": expiry_ms,
        "instrument_name": name,
        "strike": strike,
        "contract_size": 1.0,
        "min_trade_amount": 0.01,
    }


def _book(name: str, bid: float, ask: float, now: int) -> dict[str, Any]:
    return {
        "instrument_name": name,
        "state": "open",
        "timestamp": now - 800,
        "index_price": REFERENCE_PRICE_USD,
        "bids": [[bid, 0.4]],
        "asks": [[ask, 0.4]],
    }


def _space_gate_from_context(context: Mapping[str, Any], scenario: Mapping[str, Any]) -> dict[str, Any]:
    gate = deepcopy(((context.get("market_facts") or {}).get("space_gate")) or {})
    eligible = scenario.get("space_eligible") is True
    reasons = list(scenario.get("space_reasons") or [])
    gate.update({
        "state": "STRUCTURE_ELIGIBLE" if eligible else "STRUCTURE_BLOCKED",
        "source": "simulated_space_gate",
        "fmz_fact_status": "synthetic_fixture",
        "eligible": eligible,
        "gap_reasons": reasons,
        "reason_codes": reasons,
    })
    if not eligible:
        gate["net_gamma"] = {
            **deepcopy(gate.get("net_gamma") or {}),
            "value": -4_200_000.0,
            "unit": "USD",
            "sign": "NEGATIVE",
            "ok": False,
        }
    return gate


def _apply_flow_profile(facts: dict[str, Any], scenario: Mapping[str, Any]) -> None:
    direction = str(scenario.get("flow") or "NEUTRAL").upper()
    profiles = {
        "UP": {
            "lean": "MILD_BULLISH",
            "flow": {"direction": "UP", "tmv_blend": 0.32, "micro_flow_effect": "BUY_CONFIRMS_UP"},
            "vp": {
                "direction": "UP", "tmv_blend": 0.32, "micro_flow_effect": "BUY_CONFIRMS_UP",
                "trend_direction": 1, "fast": 0.34, "slow": 0.33, "momentum": 0.0021,
                "momentum_pct": 0.21, "cvd_sum": 180.0, "cvd_per_bar": 11.25,
                "cvd_norm": 0.42, "micro_direction": "bullish",
            },
            "score": 0.26,
            "vote": 0.32,
            "summary_cn": "模拟量价略偏上行，但宏观与空间只是背景约束，不据此独立选边。",
            "side_hint": "CALL_SIDE_CONTEXT",
        },
        "DOWN": {
            "lean": "MILD_BEARISH",
            "flow": {"direction": "DOWN", "tmv_blend": -0.24, "micro_flow_effect": "SELL_PRESSURE_CONFLICTS_WITH_MACRO"},
            "vp": {
                "direction": "DOWN", "tmv_blend": -0.24, "micro_flow_effect": "SELL_PRESSURE_CONFLICTS_WITH_MACRO",
                "trend_direction": -1, "fast": -0.27, "slow": -0.22, "momentum": -0.0019,
                "momentum_pct": -0.19, "cvd_sum": -150.0, "cvd_per_bar": -9.38,
                "cvd_norm": -0.36, "micro_direction": "bearish",
            },
            "score": -0.18,
            "vote": -0.24,
            "summary_cn": "模拟量价略偏下行；宏观分量仍偏宽松、空间仍为正Gamma背景，故意保留冲突解释，不据此独立选边。",
            "side_hint": "PUT_SIDE_CONTEXT",
        },
        "NEUTRAL": {
            "lean": "NEUTRAL",
            "flow": {"direction": "NEUTRAL", "tmv_blend": 0.0, "micro_flow_effect": "MIXED"},
            "vp": {
                "direction": "NEUTRAL", "tmv_blend": 0.0, "micro_flow_effect": "MIXED",
                "trend_direction": 0, "fast": 0.0, "slow": 0.0, "momentum": 0.0,
                "momentum_pct": 0.0, "cvd_sum": 0.0, "cvd_per_bar": 0.0,
                "cvd_norm": 0.0, "micro_direction": "neutral",
            },
            "score": 0.0,
            "vote": 0.0,
            "summary_cn": "模拟量价中性；仅作为页面表达输入，不据此独立选边。",
            "side_hint": "NEUTRAL_CONTEXT",
        },
    }
    profile = profiles.get(direction, profiles["NEUTRAL"])
    flow = facts.setdefault("flow", {})
    flow.update(profile["flow"])
    volume_price = facts.setdefault("volume_price", {})
    volume_price.update({
        "direction": profile["vp"]["direction"],
        "tmv_blend": profile["vp"]["tmv_blend"],
        "micro_flow_effect": profile["vp"]["micro_flow_effect"],
    })
    for key in ("tmvf_24h", "tmvf_48h"):
        window = volume_price.setdefault(key, {})
        window["tmv_core"] = profile["vp"]["fast"] if key == "tmvf_24h" else profile["vp"]["slow"]
        window["tmv_final"] = window["tmv_core"]
        core = window.setdefault("core", {})
        core["trend_direction"] = profile["vp"]["trend_direction"]
        core["tmv_core"] = window["tmv_core"]
        core["tmv_core_raw"] = window["tmv_core"]
        core["trend_strength_pct"] = abs(profile["vp"]["momentum_pct"])
    micro = volume_price.setdefault("micro_flow", {})
    for key in ("fast_4h", "slow_12h"):
        item = micro.setdefault(key, {})
        item.update({
            "direction": profile["vp"]["micro_direction"],
            "momentum": profile["vp"]["momentum"],
            "momentum_return_pct": profile["vp"]["momentum_pct"],
            "momentum_norm": profile["vp"]["momentum_pct"],
            "cvd_sum": profile["vp"]["cvd_sum"],
            "cvd_per_bar": profile["vp"]["cvd_per_bar"],
            "cvd_norm": profile["vp"]["cvd_norm"],
            "score": profile["vp"]["fast"] if key == "fast_4h" else profile["vp"]["slow"],
        })
    combined = micro.setdefault("combined", {})
    combined.update({
        "direction": profile["vp"]["micro_direction"],
        "score": profile["vp"]["tmv_blend"],
        "state": "directional" if direction != "NEUTRAL" else "mixed",
    })
    edb = facts.setdefault("edb", {})
    edb["edb_score"] = profile["score"]
    reasoning = facts.setdefault("edb_reasoning", {})
    reasoning.update({
        "lean": profile["lean"],
        "edb_score": profile["score"],
        "edb_score_raw": profile["score"],
        "summary_cn": profile["summary_cn"],
        "side_hint": profile["side_hint"],
        "conflict_level": scenario.get("conflict_level", "LOW"),
    })
    evidence = reasoning.setdefault("evidence", [])
    if evidence:
        evidence[0].update({"vote": profile["vote"]})
        evidence[0]["detail"] = {"direction": direction, "tmv_blend": profile["vp"]["tmv_blend"]}


def _context_for_scenario(now: int, scenario_id: str) -> dict[str, Any]:
    scenario = SCENARIOS[scenario_id]
    context = fact_context.synthetic_preview_context(now)
    context["status"] = "synthetic_fixture"
    context["scenario_id"] = scenario_id
    facts = context.setdefault("market_facts", {})
    _apply_flow_profile(facts, scenario)
    if scenario_id == "W5":
        edb = facts.setdefault("edb_reasoning", {})
        edb["ggr_gate"] = {
            "multiplier": 0.0,
            "regime": "NEGATIVE_GAMMA_TRANSITION",
            "veto": True,
            "reason_codes": ["negative_net_gamma_main_gate_closed"],
        }
        edb["reason_codes"] = ["negative_net_gamma_main_gate_closed"]
        gamma = facts.setdefault("gamma_regime", {})
        gamma.update({
            "regime": "NEGATIVE_GAMMA_TRANSITION",
            "net_gamma_notional": -4_200_000.0,
            "net_gex_sign": "NEGATIVE",
            "gex_info_market_state": "negative_gamma",
            "gex_info_agrees": True,
            "veto": True,
            "data_state": "synthetic_negative_gamma",
            "reason_codes": ["negative_net_gamma_main_gate_closed"],
        })
        gex_info = facts.setdefault("gex_info", {})
        gex_info.update({
            "market_state": "negative_gamma",
            "total_net_gex": -4_200_000.0,
        })
        space_context = facts.setdefault("space_context", {})
        (space_context.setdefault("gamma_regime", {})).update({
            "regime": "NEGATIVE_GAMMA_TRANSITION",
            "net_gamma_notional": -4_200_000.0,
            "net_gex_sign": "NEGATIVE",
            "gex_info_market_state": "negative_gamma",
            "gex_info_agrees": True,
            "veto": True,
            "data_state": "synthetic_negative_gamma",
            "reason_codes": ["negative_net_gamma_main_gate_closed"],
        })
        (space_context.setdefault("gex_info", {})).update({
            "market_state": "negative_gamma",
            "total_net_gex": -4_200_000.0,
        })
        facts["space_gate"] = _space_gate_from_context(context, scenario)
        space_context.update({
            "space_gate_state": facts["space_gate"]["state"],
            "space_gate_reasons": facts["space_gate"]["reason_codes"],
            "space_gate_net_gamma": deepcopy(facts["space_gate"]["net_gamma"]),
        })
    return context


def _payload(side: str, now: int, scenario_id: str) -> dict[str, Any]:
    scenario = SCENARIOS[scenario_id]
    put = side == "put"
    short_strike, long_strike = (97_000, 95_000) if put else (103_000, 105_000)
    expiry_ms = _expiry_ms(now)
    short_name = _instrument(side, short_strike, expiry_ms)
    long_name = _instrument(side, long_strike, expiry_ms)
    short_bid = LONG_ASK_BTC + scenario["credit_btc"] / QUANTITY_BTC
    fee_btc = scenario["fee_btc"]
    entry_fee = round(fee_btc * 0.75, 12)
    snapshot_context = _context_for_scenario(now, scenario_id)
    snapshot_context["status"] = "background_only"
    payload = {
        "origins": ["manual"],
        "data_identity": {"kind": "synthetic_fixture"},
        "candidate": {
            "short_instrument": short_name,
            "long_instrument": long_name,
            "quantity_btc": QUANTITY_BTC,
            "exit_policy": "hold_to_expiry",
        },
        "metadata": {
            "short": _leg_meta(side, short_strike, expiry_ms),
            "long": _leg_meta(side, long_strike, expiry_ms),
        },
        "market": {
            "valuation_ts_ms": now,
            "received_ts_ms": now - 500,
            "reference_price_usd": REFERENCE_PRICE_USD,
            "reference_source": "synthetic_fixture",
            "short_book": _book(short_name, short_bid, short_bid + 0.0001, now),
            "long_book": _book(long_name, LONG_ASK_BTC - 0.0001, LONG_ASK_BTC, now),
        },
        "fee": {
            "amount_btc": fee_btc,
            "entry_fee_btc": entry_fee,
            "delivery_fee_btc": round(fee_btc - entry_fee, 12),
            "basis": "configured_assumption",
            "covers": ["entry", "delivery"],
            "assumption_basis": "finite_local_preview_scenario",
        },
        "fmz_fact_context": snapshot_context,
    }
    if scenario["mu_btc"] is not None:
        identity = underwriting.candidate_identity(payload["candidate"], payload["metadata"], now)
        payload["risk"] = {
            "status": "supported",
            "support_verified": True,
            "source_kind": "synthetic_fixture",
            "candidate_id": identity["candidate_id"],
            "unit": "BTC",
            "model_id": "local-preview-risk-fixture",
            "model_hash": _sha("local-preview-risk-fixture"),
            "calibration_id": f"preview-{scenario_id.lower()}",
            "support_scope": "synthetic-preview-same-candidate",
            "input_ts_ms": now,
            "feature_end_ts_ms": now,
            "reference_price_usd": REFERENCE_PRICE_USD,
            "quantity_btc": QUANTITY_BTC,
            "horizon_hours": (expiry_ms - now) / 3_600_000,
            "mu_btc": scenario["mu_btc"],
            "input_row_hash": _sha(f"local-preview-{scenario_id}-{side}"),
            "normalization_basis": "same_snapshot_inverse_btc",
            "training_cutoff": "synthetic-not-trained",
            "label_known_cutoff": "synthetic-not-trained",
        }
    return payload


def _map_fixture(now: int, scenario_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    scenario = SCENARIOS[scenario_id]
    gamma_label = "负 NetGamma 主门关闭。" if scenario_id == "W5" else "空间门为模拟可读。"
    summaries = {
        "allocation": (f"模拟 {scenario_id}：配置资金3日合计210、7日合计560；对应日均70/80，短窗口速度放缓。", {
            "f3_usd_m": 210,
            "f7_usd_m": 560,
            "v3_usd_m_per_day": 70,
            "v7_usd_m_per_day": 80,
        }),
        "inventory": ("模拟：成本位置与兑现活动分别读取；成本带不是到期保护。", {
            "cost_basis": {"cp_sth_usd": 98_000, "cp_lth_usd": 64_000, "source_price_usd": 100_000},
            "gap_sth_source_price_pct": 2.0408163265,
            "cost_comparison_basis": "BITVIEW_SAME_SOURCE_SAME_DAY_ONLY",
            "loss_share_current_pct": 25,
            "loss_share_previous_pct": 20,
            "realized_loss_current_usd_mn": 150,
            "realized_loss_previous_usd_mn": 200,
        }),
        "financing": (f"模拟：原生持仓增加，实际结算资金费率为正；{gamma_label}", {
            "oi": {"open_interest_native_btc": 91_000, "change_24h": {"native_change_pct": 1.4}},
            "funding": {"settled_funding_rate_sum": 0.0001},
            "net_gamma_notional_usd": -4_200_000 if scenario_id == "W5" else 4_200_000,
            "borrow_history_missing": True,
        }),
        "external_conditions": ("模拟：美元广义指数和实际收益率上升。", {
            "dollar": {"series_id": "DTWEXBGS", "change_5obs_pct": 0.3, "change_20obs_pct": 0.7},
            "nominal_10y": {"change_5obs_bp": 4, "change_20obs_bp": 9},
            "real_10y": {"change_5obs_bp": 7, "change_20obs_bp": 12},
        }),
    }
    background = {
        name: {
            "summary_cn": summary,
            "metrics": metrics,
            "fact_state": "usable",
            "source_record_ids": [f"synthetic-{scenario_id}-{name}"],
            "cutoff_at_ms": now,
            "limitations": ["synthetic_fixture_not_current_source"],
        }
        for name, (summary, metrics) in summaries.items()
    }
    specs = [
        ("put-demo", "OPTION_STRUCTURE", "PUT_REFERENCE", 97_000, None, None),
        ("call-demo", "OPTION_STRUCTURE", "CALL_REFERENCE", 103_000, None, None),
        ("flip-demo", "OPTION_STRUCTURE", "EFFECTIVE_FLIP", 98_500, None, None),
        ("basin-demo-current", "TRADED_ACCEPTANCE", "KPF", None, 99_650, 100_350),
        ("basin-demo-lower", "TRADED_ACCEPTANCE", "KPF", None, 94_450, 95_550),
        ("basin-demo-deep", "TRADED_ACCEPTANCE", "KPF", None, 69_950, 70_450),
        ("cp-sth-demo", "ONCHAIN_COST", "CP_STH", 98_000, None, None),
    ]
    references = []
    for ref_id, family, role, price, low, high in specs:
        references.append({
            "reference_id": ref_id,
            "revision_id": f"synthetic-{scenario_id}",
            "family": family,
            "role": role,
            "source_family": family,
            "geometry": "PRICE_LEVEL" if price else "OBSERVED_ZONE",
            "raw_price": price,
            "raw_low": low,
            "raw_high": high,
            "market": "BITVIEW" if family == "ONCHAIN_COST" else "DERIBIT",
            "quote_currency": "USD",
            "price_basis": "ONCHAIN_COST_CP_STH" if family == "ONCHAIN_COST" else "DERIBIT_BTC_USD_INDEX",
            "coordinate_qualification": "COMPARABLE" if family != "ONCHAIN_COST" else "NOMINAL_ONLY",
            "expiry_scope": _expiry_ms(now) if family == "OPTION_STRUCTURE" else "GLOBAL_ONLY",
            "known_at_ms": now - 60_000,
            "observation_end_ms": now - 60_000,
            "active_from_ms": now - 60_000,
            "quality": "OK",
            "usage_decision": {"can_use": True},
            "source_record_ids": [f"synthetic-{scenario_id}-{ref_id}"],
            "limitations": ["synthetic_fixture_not_current_source"],
        })
    observations = [{
        "reference_id": "basin-demo-current",
        "revision_id": f"synthetic-{scenario_id}",
        "observed_at_ms": now - 1000,
        "known_at_ms": now - 1000,
        "method": "PRICE_SAMPLE",
        "coverage": "COMPLETE",
        "price": REFERENCE_PRICE_USD,
        "market": "DERIBIT",
        "price_basis": "DERIBIT_BTC_USD_INDEX",
        "source_record_ids": [f"synthetic-{scenario_id}-observation"],
    }]
    return background, references, observations


def _fixture_manifest(
    now: int,
    scenario_id: str,
    background: Mapping[str, Any],
    references: list[Mapping[str, Any]],
    observations: list[Mapping[str, Any]],
    candidate_id: str | None,
) -> dict[str, Any]:
    """Explicit synthetic provenance, never public-source collection evidence."""
    products = {
        "allocation": "btc.etf.flow.daily.v1",
        "inventory": "btc.brk.cost_basis.v1",
        "financing": "binance.um.btcusdt.oi.native.v1",
        "external_conditions": "macro.us10y.real.v1",
    }
    records = []
    for key, product_id in products.items():
        records.append({
            "record_id": f"synthetic-{scenario_id}-{key}",
            "identity": {
                "product_id": product_id,
                "asset": "BTC",
                "currency": "USD",
                "unit": "US$m" if key == "allocation" else "fixture",
                "provider_id": "synthetic_fixture",
                "source_ref": "本地合成示例；不是已取得的公开数据",
            },
            "collector": {"synthetic_fixture": True},
            "time": {"observation_end_ms": now - 1000, "first_seen_at_ms": now - 500},
            "content": {"values": deepcopy((background[key] or {}).get("metrics") or {})},
            "usage_qualification": {
                "can_use": True,
                "status": "USABLE",
                "usage": "background",
                "qualified_at_ms": now,
                "simulation_only": True,
            },
        })
    for item in references + observations:
        for record_id in item.get("source_record_ids") or []:
            records.append({
                "record_id": record_id,
                "identity": {
                    "product_id": "deribit.btc.index.map_context.v1",
                    "asset": "BTC",
                    "currency": "USD",
                    "unit": "USD",
                    "provider_id": "synthetic_fixture",
                    "source_ref": "本地合成指数价格；不是Deribit真实报价",
                },
                "collector": {"synthetic_fixture": True},
                "time": {"observation_end_ms": now - 1000, "first_seen_at_ms": now - 500},
                "content": {"values": {"scenario_id": scenario_id, "price_usd": REFERENCE_PRICE_USD}},
                "usage_qualification": {
                    "can_use": True,
                    "status": "USABLE",
                    "usage": "background",
                    "qualified_at_ms": now,
                    "simulation_only": True,
                },
            })
    body = {
        "schema": "btc_map_source_manifest@1",
        "cutoff_at_ms": now,
        "candidate_id": candidate_id,
        "source_records": records,
        "immutable": True,
        "simulation_only": True,
        "scenario_id": scenario_id,
    }
    body["manifest_hash"] = maps._hash(body)
    return body


def _candidate(side: str, now: int, scenario_id: str, context: Mapping[str, Any]) -> dict[str, Any]:
    base_snapshot = underwriting.evaluate(_payload(side, now, scenario_id))
    background, references, observations = _map_fixture(now, scenario_id)
    background["source_manifest"] = _fixture_manifest(
        now, scenario_id, background, references, observations, base_snapshot["candidate_id"])
    btc_map = maps.build_map(
        None,
        now_ms=now,
        snapshot=base_snapshot,
        background=background,
        references=references,
        observations=observations,
        current_price_usd=REFERENCE_PRICE_USD,
        current_price_basis="DERIBIT_BTC_USD_INDEX",
        presentation_mode="FIXTURE",
    )
    snapshot = underwriting.attach_btc_map(base_snapshot, btc_map)
    return {
        "side": side,
        "status": "snapshot",
        "scenario_id": scenario_id,
        "scenario_status_cn": SCENARIOS[scenario_id]["status_cn"],
        "candidate_id": snapshot["candidate_id"],
        "snapshot": snapshot,
        "remaining_to_expiry_ms": snapshot["contract"]["expiry_ms"] - now,
        "btc_map": snapshot["data_identity"]["btc_map"],
        "review": {"status": "not_attached"},
        "review_gate": {
            "enabled": True,
            "reasons": [],
            "kind": "synthetic_research_only",
            "production_attach_allowed": False,
        },
        "reading_guidance": guidance.build_reading_guidance(snapshot, context),
    }


def _select_exact_ids(
    catalog: Mapping[str, Any],
    role: str,
    exact_ids: list[str],
    limit: int,
) -> list[str]:
    role_ids = {
        str(item.get("id") or "")
        for item in catalog.get(role) or []
        if isinstance(item, Mapping)
    }
    return [item_id for item_id in exact_ids if item_id in role_ids][:limit]


def _wire_response(snapshot: Mapping[str, Any], scenario_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    scenario = SCENARIOS[scenario_id]
    review_input = review_core.build_review_input(snapshot, research_only=True)
    catalog = review_input["claim_catalog"]
    margin = (snapshot.get("economics") or {}).get("reference_margin_btc")
    negative_margin = isinstance(margin, (int, float)) and margin < 0
    background_claim = {
        "W1": "claim.q1.background.map_background_allocation.current_context",
        "W2": "claim.q1.background.map_background_financing.current_context",
        "W3": "claim.q1.background.map_background_inventory.current_context",
        "W4": "claim.q1.background.map_background_external_conditions.current_context",
        "W5": "claim.q1.background.map_background_financing.current_context",
        "MISSING_MU": "claim.q1.background.map_background_allocation.current_context",
    }[scenario_id]
    claim_exact = [
        background_claim,
        "claim.quote.visible_credit",
        "claim.risk.synthetic_reference_for_research",
    ]
    counter_exact = ["counter.risk.synthetic_not_production_support", "counter.execution.permission_closed"]
    gap_exact = ["gap.account_margin_missing", "gap.fmz_current_fact_missing"]
    reassess_exact = ["reassess.snapshot_rebuilt", "reassess.fmz_current_fact_available"]
    if negative_margin:
        counter_exact.insert(0, "counter.compensation.nonpositive_margin")
        reassess_exact.insert(1, "reassess.compensation_reprice")
    if scenario_id == "W5":
        counter_exact.insert(0, "counter.structure.opening_blocked")
        reassess_exact.insert(1, "reassess.structure_new_snapshot")
    if scenario_id == "MISSING_MU":
        gap_exact = ["gap.compensation.mu_reference_missing", "gap.risk_reference_missing", *gap_exact]
        reassess_exact.insert(1, "reassess.risk_reference_available")
    # The stub obeys requirements from the same frozen catalog as a real call.
    requirements = review_input["decision_relations"]["selection_requirements"]
    claim_exact = list(dict.fromkeys([*requirements["claim_ids"], *claim_exact]))
    counter_exact = list(dict.fromkeys([*requirements["counter_ids"], *counter_exact]))
    reassess_exact = list(dict.fromkeys([*requirements["reassess_condition_ids"], *reassess_exact]))
    wire = {
        "response_protocol": review_core.RESPONSE_PROTOCOL,
        "reviewed_snapshot_id": review_input["snapshot_id"],
        "projection_hash": review_input["projection_hash"],
        "claim_catalog_hash": review_input["claim_catalog_hash"],
        "applicability": scenario["applicability"],
        "selected_claim_ids": _select_exact_ids(catalog, "claims", claim_exact, review_core.MAX_SELECTED_IDS),
        "selected_counter_ids": _select_exact_ids(catalog, "counterclaims", counter_exact, review_core.MAX_SELECTED_IDS),
        "selected_gap_ids": _select_exact_ids(catalog, "gaps", gap_exact, 4),
        "selected_reassess_condition_ids": _select_exact_ids(
            catalog, "reassess_conditions", reassess_exact, review_core.MAX_SELECTED_IDS),
    }
    normalized = review_core.validate_wire_response(snapshot, wire, review_input=review_input)
    normalized["http_calls"] = 0
    return wire, normalized


def _review_delta(snapshot: Mapping[str, Any], receipt: Mapping[str, Any]) -> dict[str, Any]:
    if receipt.get("candidate_id") != snapshot.get("candidate_id"):
        return {}
    old_snapshot_id = receipt.get("snapshot_id") or receipt.get("reviewed_snapshot_id")
    old_map_id = receipt.get("map_id")
    new_map_id = ((snapshot.get("data_identity") or {}).get("btc_map") or {}).get("map_id")
    differences = []
    if old_snapshot_id != snapshot.get("snapshot_id"):
        differences.append("冻结快照版本已有变化，旧模拟回执不能覆盖当前候选。")
    if old_map_id != new_map_id:
        differences.append("MAP来源或区域版本已有变化，旧模拟回执不能覆盖当前候选。")
    return {
        "summary_cn": "最近模拟评审仍绑定原冻结版，当前资料已有变化。" if differences
        else "最近模拟评审与本张候选冻结版一致；仍需核对当前来源资格。",
        "differences_cn": differences,
        "reviewed_at_ms": receipt.get("reviewed_at_ms"),
        "same_snapshot": not differences,
    }


def _review_receipts(
    manual_review_state: Mapping[str, Any],
    review_receipts: list[Mapping[str, Any]] | None = None,
) -> list[Mapping[str, Any]]:
    if not isinstance(manual_review_state, Mapping):
        manual_review_state = {}
    receipts = []
    seen = set()
    entries = [manual_review_state.get("last_result")]
    entries.extend(review_receipts or [])
    for receipt in entries:
        if not isinstance(receipt, Mapping) or receipt.get("status") != "SIMULATED":
            continue
        key = (
            receipt.get("idempotency_key"),
            receipt.get("candidate_id"),
            receipt.get("snapshot_id") or receipt.get("reviewed_snapshot_id"),
            receipt.get("map_id"),
        )
        if key in seen:
            continue
        seen.add(key)
        receipts.append(receipt)
    return receipts


def _receipt_for_candidate(snapshot: Mapping[str, Any], receipts: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    current_map_id = ((snapshot.get("data_identity") or {}).get("btc_map") or {}).get("map_id")
    matching = [item for item in receipts if item.get("candidate_id") == snapshot.get("candidate_id")]
    if not matching:
        return None
    exact = [
        item for item in matching
        if (item.get("snapshot_id") or item.get("reviewed_snapshot_id")) == snapshot.get("snapshot_id")
        and item.get("map_id") == current_map_id
    ]
    if exact:
        return max(exact, key=lambda item: int(item.get("reviewed_at_ms") or 0))
    return max(matching, key=lambda item: int(item.get("reviewed_at_ms") or 0))


def _attach_receipt(
    candidates: list[dict[str, Any]],
    manual_review_state: Mapping[str, Any],
    review_receipts: list[Mapping[str, Any]] | None = None,
) -> None:
    receipts = _review_receipts(manual_review_state, review_receipts)
    for item in candidates:
        snapshot = item["snapshot"]
        receipt = _receipt_for_candidate(snapshot, receipts)
        if receipt is None:
            continue
        item["btc_map_review_delta"] = _review_delta(snapshot, receipt)
        current_map_id = ((snapshot.get("data_identity") or {}).get("btc_map") or {}).get("map_id")
        if (
            receipt.get("candidate_id") != snapshot.get("candidate_id")
            or receipt.get("snapshot_id") != snapshot.get("snapshot_id")
            or receipt.get("map_id") != current_map_id
            or not isinstance(receipt.get("wire_response"), Mapping)
        ):
            continue
        try:
            review_input = review_core.build_review_input(snapshot, research_only=True)
            normalized = review_core.validate_wire_response(
                snapshot, receipt["wire_response"], review_input=review_input)
        except Exception:
            continue
        attached = deepcopy(normalized)
        attached["status"] = "SIMULATED"
        attached["normalized_status"] = normalized.get("status")
        attached["http_calls"] = 0
        attached["reviewed_at_ms"] = receipt.get("reviewed_at_ms")
        item["review"] = attached


def _global_map(now: int, scenario_id: str) -> dict[str, Any]:
    background, references, observations = _map_fixture(now, scenario_id)
    background["source_manifest"] = _fixture_manifest(now, scenario_id, background, references, observations, None)
    return maps.build_map(
        None,
        now_ms=now,
        background=background,
        references=references,
        observations=observations,
        current_price_usd=REFERENCE_PRICE_USD,
        current_price_basis="DERIBIT_BTC_USD_INDEX",
        presentation_mode="FIXTURE",
    )


def build_preview(
    now: int | None = None,
    manual_review_state: dict | None = None,
    *,
    scenario_id: str = DEFAULT_SCENARIO,
    review_receipts: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    now = _now_ms() if now is None else int(now)
    scenario_id = _scenario_id(scenario_id)
    scenario = SCENARIOS[scenario_id]
    context = _context_for_scenario(now, scenario_id)
    gate = _space_gate_from_context(context, scenario)
    registry_path = FRONTEND.parents[2] / "demo" / "shared" / "data_product_registry_v23.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    simulated_ids = {"fmz.bridge.snapshots.v1", "fmz.spacegate.current.v1",
                     "deribit.btc.vertical.two_leg_book.v1", "risk.astra.frozen_mu.reference.v1"}
    health = {"generated_at_ms": now, "simulation_only": True, "products": {
        row["product_id"]: {
            "collector_id": row.get("collector_id"),
            "status": "SIMULATION_GAP" if row["product_id"] == "risk.astra.frozen_mu.reference.v1"
            and scenario["mu_btc"] is None else "SIMULATION" if row["product_id"] in simulated_ids else "NOT_CONNECTED",
            "current": [{"observation_end_ms": now}] if row["product_id"] in simulated_ids
            and not (row["product_id"] == "risk.astra.frozen_mu.reference.v1" and scenario["mu_btc"] is None) else [],
        }
        for row in registry["products"]}}
    candidates = [_candidate("put", now, scenario_id, context), _candidate("call", now, scenario_id, context)]
    review_state = deepcopy(manual_review_state or {
        "inflight": None,
        "last_result": None,
        "cooldown_remaining_ms": 0,
        "daily_http_budget": None,
    })
    _attach_receipt(candidates, review_state, review_receipts)
    return {
        "schema": SCHEMA,
        "updated_at_ms": now,
        "preview": {
            "kind": "synthetic_fixture",
            "mode": "FIXTURE",
            "label": scenario["label_cn"],
            "scenario_id": scenario_id,
            "scenario_status_cn": scenario["status_cn"],
            "scenarios": _scenario_options(),
            "llm_http_calls": 0,
            "trading_permission": False,
            "production_attach_allowed": False,
        },
        "market_context": context,
        "data_health": health,
        "space_gate": gate,
        "candidates": candidates,
        "btc_map": _global_map(now, scenario_id),
        "manual_review_state": review_state,
        "daily_snapshots": [],
        "transitions": [],
        "gaps": [],
        "llm_call_count": 0,
    }


class PreviewServer(ThreadingHTTPServer):
    def __init__(self, port: int):
        super().__init__(("127.0.0.1", port), PreviewHandler)
        self.fixture_ts_ms = _now_ms()
        self.lock = threading.Lock()
        self.review_state: dict = {"inflight": None, "last_result": None,
                                   "cooldown_remaining_ms": 0, "daily_http_budget": None}
        self.idempotency: dict[str, dict] = {}


class PreviewHandler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict) -> None:
        body = (json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _view(self, scenario_id: str) -> dict[str, Any]:
        with self.server.lock:
            state = deepcopy(self.server.review_state)
            receipts = list(deepcopy(self.server.idempotency).values())
        view = build_preview(
            self.server.fixture_ts_ms,
            manual_review_state=state,
            scenario_id=scenario_id,
            review_receipts=receipts,
        )
        observed = _now_ms()
        view["updated_at_ms"] = observed
        view["market_context"]["age_ms"] = observed - self.server.fixture_ts_ms
        return view

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/workbench":
            try:
                scenario = _scenario_id((parse_qs(parsed.query).get("scenario") or [DEFAULT_SCENARIO])[0])
            except ValueError:
                self._json(400, {"status": "ERROR", "code": "unknown_scenario"})
                return
            self._json(200, self._view(scenario))
            return
        if path == "/api/workbench/review-state":
            with self.server.lock:
                state = deepcopy(self.server.review_state)
            self._json(200, {"schema": SCHEMA, "manual_review_state": state})
            return
        root = FRONTEND.resolve()
        relative = unquote(path.lstrip("/")) or "index.html"
        target = (root / relative).resolve()
        if root not in target.parents and target != root:
            self._json(403, {"status": "ERROR", "code": "static_forbidden"})
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self._json(404, {"status": "ERROR", "code": "not_found"})
            return
        body = target.read_bytes()
        mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") else ""))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/workbench/review":
            self._json(404, {"status": "ERROR", "code": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= 8192:
                raise ValueError("request size invalid")
            request = json.loads(self.rfile.read(length))
            scenario_id = _scenario_id(request.get("scenario"))
            candidate_id = str(request.get("candidate_id") or "")
            snapshot_id = str(request.get("snapshot_id") or "")
            key = str(request.get("idempotency_key") or "")
            view = self._view(scenario_id)
            candidates = [
                item for item in view["candidates"]
                if item["snapshot"]["candidate_id"] == candidate_id
            ]
            if len(candidates) != 1 or not key:
                raise ValueError("candidate or idempotency key invalid")
            candidate = candidates[0]
            snapshot = candidate["snapshot"]
            if snapshot_id and snapshot_id != snapshot["snapshot_id"]:
                raise ValueError("stale snapshot id")
        except (ValueError, json.JSONDecodeError, UnicodeError):
            self._json(400, {"status": "ERROR", "code": "bad_request"})
            return
        with self.server.lock:
            previous = self.server.idempotency.get(key)
            if previous is not None:
                if (previous.get("candidate_id") == candidate_id
                        and previous.get("scenario_id") == scenario_id
                        and (not snapshot_id or previous.get("snapshot_id") == snapshot_id)):
                    self._json(200, previous)
                else:
                    self._json(409, {"status": "ERROR", "code": "idempotency_conflict"})
                return
            if self.server.review_state["inflight"] is not None:
                self._json(409, {"status": "ERROR", "code": "review_inflight"})
                return
            self.server.review_state["inflight"] = {
                "candidate_id": candidate_id,
                "snapshot_id": snapshot["snapshot_id"],
                "scenario_id": scenario_id,
                "idempotency_key": key,
                "started_ts_ms": _now_ms(),
            }
        time.sleep(2)
        try:
            wire, normalized = _wire_response(snapshot, scenario_id)
        except Exception as exc:
            with self.server.lock:
                self.server.review_state["inflight"] = None
            self._json(500, {"status": "ERROR", "code": "simulated_review_invalid", "reason": str(exc)})
            return
        reviewed_at = _now_ms()
        result = {
            "schema": SCHEMA,
            "status": "SIMULATED",
            "scenario_id": scenario_id,
            "scenario_label_cn": SCENARIOS[scenario_id]["label_cn"],
            "simulated_stub_id": f"bounded-wire-{scenario_id.lower()}",
            "candidate_id": candidate_id,
            "side": candidate["side"],
            "snapshot_id": snapshot["snapshot_id"],
            "reviewed_snapshot_id": snapshot["snapshot_id"],
            "map_id": ((snapshot.get("data_identity") or {}).get("btc_map") or {}).get("map_id"),
            "idempotency_key": key,
            "reviewed_at_ms": reviewed_at,
            "llm_call_count": 0,
            "http_calls": 0,
            "wire_response": wire,
            "review": normalized,
        }
        with self.server.lock:
            self.server.review_state["inflight"] = None
            self.server.review_state["last_result"] = deepcopy(result)
            self.server.review_state["last_completed_ts_ms"] = reviewed_at
            self.server.idempotency[key] = deepcopy(result)
        try:
            self._json(200, result)
        except (BrokenPipeError, ConnectionAbortedError):
            # A browser refresh may close the long POST after the receipt is settled.
            pass

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8896)
    args = parser.parse_args()
    server = PreviewServer(args.port)
    print(f"Synthetic workbench preview: http://127.0.0.1:{server.server_port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
