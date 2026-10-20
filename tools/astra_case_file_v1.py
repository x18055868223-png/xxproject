"""Case file builder: the frozen fact world of one candidate (docs/opus/03, 2026-10-20).

Reads what existing producers already froze or serve; computes nothing new except the
market baseline (astra_market_baseline_v1). Every entry carries a stable ref, its
quality, and its upstream lineage, so the inquirer and the external researcher can cite
it and see when two readings share a source.

Inputs:
- snapshot:   the frozen candidate snapshot (as-of packet ``snapshot``)
- raw_market: the as-of packet ``raw_payload.market`` (the books with greeks / mark IV;
              ``snapshot.market`` has them stripped)
- item:       the displayed candidate from the read view (program read-time layers);
              used only when its snapshot_id matches
- view:       read-view parts (data_health, gaps, release_identity, space_gate,
              transitions, daily_snapshots, expiry_gamma, cross_asset)
- dp_reading: astra_decision_path_v1.build() output for the same frame, if available
- history:    L9 rows (researcher only; the inquirer never receives L9)

Pure; no I/O. Hashes, debug traces and raw source records are dropped; nothing is
truncated by count. WPEM is deliberately absent (2026-10-20 decision): its level
duplicates L2-L5, its delta/path never formed in production, and its only non-redundant
part, the time memory of a locked contract, belongs to holding, not to underwriting.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Iterable, Mapping

if __package__:
    from . import astra_market_baseline_v1 as market_baseline
else:
    import astra_market_baseline_v1 as market_baseline

SCHEMA = "case_file@0.1"
BUILDER_VERSION = "astra_case_file_v1@0.1.2"
LAYERS = ("L0", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9")
DROP_KEYS = {"source_records", "source_manifest", "source_bindings", "debug", "_debug", "raw_payload"}
DROP_SUFFIXES = ("_hash", "sha256")
# never let credential-shaped keys reach an external reader
SECRET_MARKERS = ("token", "api_key", "apikey", "password", "secret", "authorization", "cookie")
TRANSITION_LOOKBACK_MS = 72 * 3_600_000
DAILY_DAYS = 7
GEX_VOL_KEYS = ("atm_iv", "dvol", "iv_percentile", "rr_skew", "term_structure", "volatility_trigger")
GEX_FLOW_KEYS = ("pcr", "pcr_oi", "put_oi", "call_oi", "put_volume", "call_volume", "whale_1h", "whale_24h",
                 "hedging_flows")
GEX_STRUCTURE_KEYS = ("flip_point", "total_net_gex", "support_walls", "resistance_walls", "near_term_walls",
                      "max_pain_price", "max_pain_target_expiry", "magnet_price", "market_state", "p1", "p2", "n1",
                      "n2", "next_expiry", "spot_price")
GEX_META_KEYS = ("age_ms", "fetched_at_ms", "data_state", "quality", "stale", "availability")


def clean(value: Any) -> Any:
    """Drop hashes, debug traces and raw source records; keep everything else."""
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            name = str(key)
            lowered = name.lower()
            if name in DROP_KEYS or name.endswith(DROP_SUFFIXES) or any(m in lowered for m in SECRET_MARKERS):
                continue
            out[name] = clean(item)
        return out
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    return value


def _empty(value: Any) -> bool:
    """True when every leaf is None or an empty container (a dict of Nones says nothing)."""
    if value is None:
        return True
    if isinstance(value, Mapping):
        return all(_empty(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    return False


def _pick(source: Mapping[str, Any] | None, keys: Iterable[str]) -> dict[str, Any]:
    source = source or {}
    return {key: clean(source[key]) for key in keys if key in source}


class _Case:
    def __init__(self) -> None:
        self.layers: dict[str, list[dict[str, Any]]] = {layer: [] for layer in LAYERS}

    def add(self, ref: str, label_cn: str, values: Any, *, lineage: Iterable[str], source: str,
            asof_ms: int | None = None, relations: Mapping[str, Any] | None = None,
            quality: str | None = None, quality_reason: str | None = None) -> None:
        layer = ref.split(".", 1)[0]
        if layer not in self.layers:
            raise ValueError("unknown layer in ref " + ref)
        if quality is None:
            quality = "missing" if _empty(values) else "ok"
        self.layers[layer].append({
            "ref": ref, "label_cn": label_cn, "values": clean(values),
            "relations": clean(dict(relations)) if relations else {},
            "asof_ms": asof_ms, "quality": quality, "quality_reason": quality_reason,
            "lineage": sorted(set(lineage)), "source": source,
        })

    def refs(self) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        for layer, entries in self.layers.items():
            for position, entry in enumerate(entries):
                if entry["ref"] in index:
                    raise ValueError("duplicate ref " + entry["ref"])
                index[entry["ref"]] = {"layer": layer, "index": position}
        return index


HEALTH_NOTE_CN = ("Deribit 报价类产品（order_book、two_leg_book、instrument、index 等）按需拉取，平时显示 STALE 或 MISSING "
                  "属正常，本候选的报价新鲜度看 L2.quote 与两腿盘口时间；risk.* 目前没有生产者，属设计现状；"
                  "NOT_CONNECTED 为尚未接入。判断某个缺口是否影响本保单，看 affected_consumers 与对应案卷条目的 quality。")


def _by_status(health: Mapping[str, Mapping[str, Any]]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for pid, row in health.items():
        groups.setdefault(str(row.get("status")), []).append(pid)
    return {status: sorted(ids) for status, ids in sorted(groups.items())}


def _stale_quality(meta: Mapping[str, Any] | None) -> tuple[str | None, str | None]:
    meta = meta or {}
    if meta.get("stale") is True:
        return "stale", "source_marked_stale"
    state = str(meta.get("data_state") or meta.get("quality") or "").lower()
    if state in {"stale", "degraded"}:
        return "stale", "source_" + state
    return None, None


def _contract_cn(contract: Mapping[str, Any]) -> str:
    side = {"put": "Put", "call": "Call"}.get(contract.get("side"), "?")
    short = contract.get("short_instrument") or "?"
    long = contract.get("long_instrument") or "?"
    return (f"Deribit BTC {side} 垂直信用价差：卖 {short}，买 {long}；数量 {contract.get('quantity_btc')} BTC；"
            f"宽 {contract.get('width_usd')} USD；持有到期；币本位（BTC）结算")


def build(*, snapshot: Mapping[str, Any], raw_market: Mapping[str, Any], asof_ms: int,
          item: Mapping[str, Any] | None = None, view: Mapping[str, Any] | None = None,
          dp_reading: Mapping[str, Any] | None = None,
          history: Iterable[Mapping[str, Any]] | None = None,
          built_at_ms: int | None = None,
          research_readings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    view = view or {}
    snapshot_id = snapshot.get("snapshot_id")
    if item is not None and (item.get("snapshot") or {}).get("snapshot_id") != snapshot_id:
        item = None  # read-time layers of a different capture never enter this case
    item = item or {}
    contract = snapshot.get("contract") or {}
    fact = snapshot.get("fact_context") or {}
    facts = fact.get("market_facts") or {}
    space = facts.get("space_context") or {}
    gex = space.get("gex_info") or {}
    gex_quality, gex_reason = _stale_quality(gex)
    gex_asof = gex.get("fetched_at_ms")
    fmz_asof = fact.get("snapshot_ts_ms")
    market = snapshot.get("market") or {}
    quote = market.get("quote") or {}
    case = _Case()

    # L0 evidence quality
    products = ((view.get("data_health") or {}).get("products") or {})
    health = {pid: {"status": p.get("status"),
                    "reason_codes": (p.get("usage_decision") or {}).get("reason_codes"),
                    "age_ms": (p.get("usage_decision") or {}).get("age_ms"),
                    "ttl_ms": (p.get("usage_decision") or {}).get("ttl_ms"),
                    "affected_consumers": p.get("affected_consumers")}
              for pid, p in sorted(products.items()) if isinstance(p, Mapping)}
    case.add("L0.health.products", "数据产品健康（状态、原因码、年龄、影响的消费者）", health,
             lineage=["workbench.data_health"], source="astra_workbench_v20._data_health",
             asof_ms=(view.get("data_health") or {}).get("generated_at_ms"),
             relations={"by_status": _by_status(health), "note_cn": HEALTH_NOTE_CN})
    case.add("L0.gaps", "本次捕获与视图的缺口码",
             {"view": view.get("gaps") or [], "capture": item.get("capture_gaps") or [],
              "snapshot": snapshot.get("gap_reasons") or [], "quote": quote.get("gap_reasons") or [],
              "fmz_fact": fact.get("gap_reasons") or []},
             lineage=["workbench.view", "astra_underwriting_v20"], source="view.gaps + snapshot.gap_reasons",
             quality="ok")
    case.add("L0.fmz_fact", "信号层（FMZ）事实的新鲜度与来源质量",
             _pick(fact, ("status", "age_ms", "strategy_version", "producer_schema_version", "runtime_mode",
                          "source_quality", "export_health", "snapshot_ts_ms")),
             lineage=["fmz.snapshot"], source="snapshot.fact_context", asof_ms=fmz_asof)
    components = [{k: c.get(k) for k in ("component", "candidate_version", "runtime_status", "production_status")}
                  for c in ((view.get("release_identity") or {}).get("components") or []) if isinstance(c, Mapping)]
    case.add("L0.release", "各组件版本与运行状态", components, lineage=["workbench.release_identity"],
             source="astra_release_identity_v1")

    # L1 contract and liability
    case.add("L1.contract", "合约", _pick(contract, ("side", "venue", "underlying", "short_instrument",
                                                     "long_instrument", "short_strike_usd", "long_strike_usd",
                                                     "width_usd", "quantity_btc", "expiry_ms", "exit_policy",
                                                     "settlement_currency")),
             lineage=["deribit.instrument"], source="snapshot.contract", asof_ms=asof_ms)
    liability = snapshot.get("liability") or {}
    case.add("L1.payout_table", "到期赔付表（BTC；static_net_btc = 净信用 − 赔付）", liability.get("payout_table"),
             lineage=["astra_underwriting_v20"], source="snapshot.liability.payout_table", asof_ms=asof_ms)
    case.add("L1.liability", "责任边界、盈亏平衡与距离",
             _pick(liability, ("starts_at_usd", "protection_at_usd", "breakeven_usd", "usd_intrinsic_cap",
                               "btc_contract_cap", "signed_distance_to_short_pct", "signed_distance_to_long_pct",
                               "btc_reference_scale_at_spot")),
             lineage=["astra_underwriting_v20"], source="snapshot.liability", asof_ms=asof_ms)
    case.add("L1.economics", "经济量（权利金、费用、净信用、亏损预算）", snapshot.get("economics"),
             lineage=["astra_underwriting_v20", "deribit.order_book"], source="snapshot.economics", asof_ms=asof_ms)

    # L2 quote, greeks, baseline
    case.add("L2.quote", "两腿盘口报价估计（非成交）", quote, lineage=["deribit.order_book"],
             source="snapshot.market.quote", asof_ms=quote.get("valuation_ts_ms"),
             quality="stale" if quote.get("quote_gap_reasons") else None,
             quality_reason=",".join(quote.get("quote_gap_reasons") or []) or None)
    case.add("L2.fee", "费用口径", snapshot.get("fee"), lineage=["deribit.fee_schedule"], source="snapshot.fee")
    for leg in ("short", "long"):
        book = raw_market.get(leg + "_book") or {}
        values = _pick(book, ("instrument_name", "mark_price", "mark_iv", "bid_iv", "ask_iv", "greeks",
                              "underlying_price", "underlying_index", "index_price", "best_bid_price",
                              "best_bid_amount", "best_ask_price", "best_ask_amount", "bids", "asks",
                              "open_interest", "timestamp", "state"))
        case.add(f"L2.book.{leg}", ("短腿" if leg == "short" else "保护腿") + "盘口、mark IV 与希腊值",
                 values, lineage=[f"deribit.order_book.{leg}"], source=f"raw_payload.market.{leg}_book",
                 asof_ms=book.get("timestamp"))
    baseline = market_baseline.from_capture(snapshot, raw_market)
    probs = baseline.get("probabilities") or {}
    base_quality = "ok" if baseline.get("status") == "ok" else "unknown"
    base_reason = ",".join(baseline.get("reasons") or []) or None
    for key, label in (("p_itm_short", "短腿到期实值概率（市场隐含）"),
                       ("p_touch_short", "到期前触及短腿概率（市场隐含，≈2×实值）"),
                       ("p_loss", "到期净亏损概率（市场隐含）"),
                       ("p_max_loss", "越过保护腿概率（市场隐含）")):
        value = probs.get(key)
        case.add(f"L2.baseline.{key}", label, {"value": value},
                 relations={"abs_delta_short": (baseline.get("delta_reference") or {}).get("short_abs_delta")}
                 if key == "p_itm_short" else
                 {"loss_boundaries_usd": probs.get("loss_boundaries_usd")} if key == "p_loss" else None,
                 lineage=["deribit.order_book.short", "deribit.order_book.long"],
                 source="astra_market_baseline_v1.from_capture", asof_ms=asof_ms,
                 quality=base_quality if value is not None or base_quality != "ok" else "unknown",
                 quality_reason=base_reason if value is not None or base_quality != "ok" else "net_credit_unknown")
    case.add("L2.baseline.basis", "基线口径与输入", {"basis_cn": baseline.get("basis_cn"),
                                                    "inputs": baseline.get("inputs"),
                                                    "delta_reference": baseline.get("delta_reference")},
             lineage=["deribit.order_book.short", "deribit.order_book.long"],
             source="astra_market_baseline_v1", asof_ms=asof_ms, quality=base_quality, quality_reason=base_reason)
    comp = baseline.get("compensation") or {}
    case.add("L2.compensation", "补偿：扣费后净信用 ÷ 两腿 mark 价差（档位为草案）", comp,
             lineage=["deribit.order_book.short", "deribit.order_book.long", "astra_underwriting_v20"],
             source="astra_market_baseline_v1._compensation", asof_ms=asof_ms,
             quality="ok" if comp.get("status") == "ok" else "unknown")
    case.add("L2.selection", "候选选取依据（发现阶段的短腿 Δ）", item.get("discovery"),
             lineage=["deribit.discovery"], source="item.discovery",
             quality=None if item else "unknown", quality_reason=None if item else "read_view_item_unavailable")
    case.add("L2.decision_reduction", "程序的补偿与最坏情形读数", item.get("decision_reduction"),
             lineage=["astra_workbench_v20"], source="item.decision_reduction",
             quality=None if item else "unknown", quality_reason=None if item else "read_view_item_unavailable")

    # L3 structure
    gate = view.get("space_gate") or {}
    case.add("L3.space.gate", "Space 门（自 2026-10-13 起只作评估参考，不阻断）", gate, lineage=["fmz.space_gate"],
             source="view.space_gate", asof_ms=gate.get("observed_at_ms"))
    since = asof_ms - TRANSITION_LOOKBACK_MS
    transitions = [{"at_ms": t.get("transition_ts_ms") or t.get("at_ms") or t.get("recorded_at_ms"),
                    "previous_state": t.get("previous_state"), "current_state": t.get("current_state"),
                    "anchor": ((t.get("space_gate") or {}).get("anchor") or {}).get("score"),
                    "net_gamma": (t.get("space_gate") or {}).get("net_gamma")}
                   for t in (view.get("transitions") or []) if isinstance(t, Mapping)]
    transitions = [t for t in transitions if isinstance(t["at_ms"], int) and since <= t["at_ms"] <= asof_ms]
    case.add("L3.space.transitions", "近 72 小时 Space 门 / 锚定迁移", transitions, lineage=["fmz.space_gate"],
             source="view.transitions", quality="ok")
    case.add("L3.space.anchor", "锚定与 Gamma 体制（信号层）",
             {"anchor": space.get("anchor"), "gamma_regime": space.get("gamma_regime"),
              "producer_gate": _pick(space, ("space_gate_state", "space_gate_anchor", "space_gate_net_gamma",
                                             "space_gate_reasons", "space_gate_since_ts_ms"))},
             lineage=["fmz.snapshot"], source="market_facts.space_context", asof_ms=fmz_asof)
    case.add("L3.gex.structure", "GEX 结构：Flip、墙、Max Pain、净 GEX", _pick(gex, GEX_STRUCTURE_KEYS),
             relations=_pick(gex, GEX_META_KEYS), lineage=["gexmonitor.gex_info"],
             source="market_facts.space_context.gex_info", asof_ms=gex_asof,
             quality=gex_quality, quality_reason=gex_reason)
    expiry_key = None
    by_expiry = (view.get("expiry_gamma") or {}).get("by_expiry") or {}
    for key, row in by_expiry.items():
        if isinstance(row, Mapping) and row.get("expiry_ms") == contract.get("expiry_ms"):
            expiry_key = key
    expiry_row = by_expiry.get(expiry_key) if expiry_key else None
    case.add("L3.expiry_walls", "本期到期合约自身的 Gamma/OI 集中与 Pin", expiry_row,
             lineage=["deribit.book_summary"], source="view.expiry_gamma.by_expiry",
             asof_ms=(expiry_row or {}).get("computed_at_ms"),
             quality="stale" if (expiry_row or {}).get("stale") else None)
    case.add("L3.spatial_position", "KPF 关键流动性密集区与空间接近度", item.get("spatial_position"),
             lineage=["btc_map.kpf"], source="item.spatial_position",
             quality=None if item else "unknown", quality_reason=None if item else "read_view_item_unavailable")
    btc_map = ((snapshot.get("data_identity") or {}).get("btc_map")) or {}
    presentation = btc_map.get("presentation") or {}
    case.add("L3.map.references", "价格地图参考位（成本区、KPF、墙等）",
             {"price_axis": presentation.get("price_axis"), "reference_pool": btc_map.get("reference_pool"),
              "co_location": btc_map.get("co_location")},
             lineage=["btc_map"], source="snapshot.data_identity.btc_map", asof_ms=btc_map.get("cutoff_at_ms"))
    case.add("L3.map.liability_regions", "责任区与地图区间的对照",
             {"policy_link": btc_map.get("policy_link"), "liability_by_region": presentation.get("liability_by_region")},
             lineage=["btc_map"], source="snapshot.data_identity.btc_map", asof_ms=btc_map.get("cutoff_at_ms"))

    # L4 volatility
    case.add("L4.iv", "隐含波动：ATM IV、DVOL、IV 分位、期限结构、RR skew", _pick(gex, GEX_VOL_KEYS),
             relations=_pick(gex, GEX_META_KEYS), lineage=["gexmonitor.gex_info"],
             source="market_facts.space_context.gex_info", asof_ms=gex_asof,
             quality=gex_quality, quality_reason=gex_reason)
    eg = view.get("expiry_gamma") or {}
    case.add("L4.expiry_vol", "本期 ATM IV、预期波动与已实现波动",
             {"atm_iv_pct": (expiry_row or {}).get("atm_iv_pct"),
              "expected_move_usd": (expiry_row or {}).get("expected_move_usd"),
              "forward_usd": (expiry_row or {}).get("forward_usd"), "realized_vol": eg.get("realized_vol")},
             lineage=["deribit.book_summary", "deribit.index"], source="view.expiry_gamma")
    case.add("L4.skew", "信号层 skew 读数", facts.get("skew"), lineage=["fmz.snapshot"],
             source="market_facts.skew", asof_ms=fmz_asof)

    # L5 movement and flow
    for ref, key, label in (("L5.volume_price", "volume_price", "量价形态（TMV 等）"),
                            ("L5.flow", "flow", "主动成交与微观流（CVD 等）"),
                            ("L5.m_die", "m_die", "M-DIE 运动方向判读"),
                            ("L5.conflicts", "conflicts", "信号层冲突标记"),
                            ("L5.edb", "edb", "EDB 读数"),
                            ("L5.edb_reasoning", "edb_reasoning", "EDB 推理细节")):
        case.add(ref, label, facts.get(key), lineage=["fmz.snapshot"], source="market_facts." + key,
                 asof_ms=fmz_asof)
    case.add("L5.options_flow", "期权流：PCR、OI、成交量、大单、对冲流", _pick(gex, GEX_FLOW_KEYS),
             relations=_pick(gex, GEX_META_KEYS), lineage=["gexmonitor.gex_info"],
             source="market_facts.space_context.gex_info", asof_ms=gex_asof,
             quality=gex_quality, quality_reason=gex_reason)

    # L6 time evolution
    days = [d for d in (view.get("daily_snapshots") or []) if isinstance(d, Mapping)][-DAILY_DAYS:]
    daily = [{"bjt_day": d.get("bjt_day"), "space_gate": (d.get("space_gate") or {}).get("state"),
              "candidates": [{k: c.get(k) for k in ("side", "candidate_id")} for c in d.get("candidates") or []
                             if isinstance(c, Mapping)]} for d in days]
    case.add("L6.daily", f"近 {DAILY_DAYS} 日每日快照摘要", daily, lineage=["workbench.daily_snapshots"],
             source="view.daily_snapshots")

    # L7 external
    case.add("L7.cross_asset", "动态跨资产参考（相关、β、归因）", view.get("cross_asset"),
             lineage=["cross_asset"], source="view.cross_asset")
    case.add("L7.macro", "宏观压力读数", facts.get("macro_pressure"), lineage=["fmz.snapshot", "fred"],
             source="market_facts.macro_pressure", asof_ms=fmz_asof)
    case.add("L7.events", "到期前的外生事件（事件日历）", item.get("exogenous_events"),
             lineage=["event_calendar"], source="item.exogenous_events",
             quality=None if item else "unknown", quality_reason=None if item else "read_view_item_unavailable")
    background = btc_map.get("background") or {}
    for key in sorted(background):
        if isinstance(background[key], Mapping):
            case.add(f"L7.background.{key}", "资金与慢背景：" + key, background[key],
                     lineage=["btc_map.background." + key], source="snapshot.data_identity.btc_map.background",
                     asof_ms=btc_map.get("cutoff_at_ms"))

    # L8 program readings
    case.add("L8.decision_path", "程序固定判读（原 Decision Path D0–Q2，可被质疑）", dp_reading,
             lineage=["astra_decision_path_v1"], source="astra_decision_path_v1.build",
             quality=None if dp_reading else "unknown",
             quality_reason=None if dp_reading else "decision_path_reading_unavailable")
    case.add("L8.guidance", "程序解读（reading_guidance）", item.get("reading_guidance"),
             lineage=["astra_underwriting_guidance_v20"], source="item.reading_guidance",
             quality=None if item else "unknown", quality_reason=None if item else "read_view_item_unavailable")
    case.add("L8.signal_recommendation", "信号层自身的策略建议", facts.get("strategy_recommendation"),
             lineage=["fmz.snapshot"], source="market_facts.strategy_recommendation", asof_ms=fmz_asof)
    case.add("L8.research_readings", "研究读数（经前向验证支持并经用户批准；可被质疑）",
             research_readings if research_readings is not None else {"readings": [], "note_cn": "研究读数未提供"},
             lineage=["research_archive.promotions"], source="astra_research_promotion_v1.evaluate_readings",
             asof_ms=asof_ms, quality="ok" if research_readings is not None else "unknown",
             quality_reason=None if research_readings is not None else "research_readings_unavailable")

    # L9 history (researcher only)
    if history is not None:
        case.add("L9.history", "同结构历史样本与结算", list(history), lineage=["workbench.history"],
                 source="history rows")

    body = {
        "schema": SCHEMA,
        "case_id": "cf_" + str(snapshot_id)[:24],
        "snapshot_id": snapshot_id,
        "candidate_id": snapshot.get("candidate_id"),
        "asof_ms": asof_ms,
        "contract_cn": _contract_cn(contract),
        "basis_cn": ["概率基线为风险中性、市场隐含，不是真实世界概率",
                     "所有金额、距离、盈亏平衡由程序计算；引用时请带 ref",
                     "quality 为 missing/unknown/stale 的条目不得当作已知事实"],
        "layers": case.layers,
        "refs": case.refs(),
    }
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    # built_at_ms stays outside the content hash: the same inputs give the same content
    body["build"] = {"builder": BUILDER_VERSION, "bytes": len(encoded), "built_at_ms": built_at_ms,
                     "content_sha256": hashlib.sha256(encoded).hexdigest(),
                     "time_note_cn": "asof_ms 是报价估值时刻；L0 健康、Space 门、跨资产、本期墙、事件取自 built_at_ms 时的读视图"}
    return body


def for_inquirer(case_file: Mapping[str, Any]) -> dict[str, Any]:
    """The same case without L9: the inquirer reads the current cross-section only."""
    out = deepcopy(dict(case_file))
    out["layers"] = {k: v for k, v in out["layers"].items() if k != "L9"}
    out["refs"] = {k: v for k, v in out["refs"].items() if v["layer"] != "L9"}
    out["build"] = {**out.get("build", {}), "variant": "inquirer_without_l9", "bytes": None, "content_sha256": None}
    return out
