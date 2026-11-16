"""Compact paired observations and a separate active-participation assessment contract.

No model call, trade selection, prediction, order routing or outcome ingestion.
The original structural boards remain the detailed, immutable evidence surface.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping

try:
    from .astra_structural_board_v1 import digest, number
except ImportError:
    from astra_structural_board_v1 import digest, number

SCHEMA = "structural_player_view@0.1"
ASSESSMENT = "structural_player_assessment@0.1"
SIDES = ("put", "call")
REGIONS = ("near_boundary", "outside_liability", "inside_liability")
LABELS = {"near_boundary": "两腿边界附近", "outside_liability": "短腿责任外侧",
          "inside_liability": "短腿责任内侧"}
UNAVAILABLE_REASONS = ("no_matching_instruments", "no_common_expiry", "no_valid_vertical", "no_positive_credit")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _board(value: Mapping[str, Any], side: str) -> None:
    require(value.get("schema") == "structural_board@0.1", "expected structural_board@0.1")
    b = value.get("boundary") or {}
    require(b.get("side") == side and value.get("boundary_id") == digest(b), "boundary binding mismatch")
    short, long = number(b.get("short_usd")), number(b.get("protection_usd"))
    cutoff = value.get("cutoff_ms")
    require(isinstance(cutoff, int) and not isinstance(cutoff, bool) and cutoff > 0, "invalid cutoff")
    require(short is not None and long is not None and min(short, long) > 0
            and (long < short if side == "put" else long > short), "invalid vertical boundary")
    require(b.get("venue") == "Deribit" and b.get("underlying") == "BTC"
            and b.get("settlement_currency") == "BTC", "unsupported contract basis")
    require(number(b.get("quantity_btc")) is not None and b["quantity_btc"] > 0
            and number(b.get("expiry_ms")) is not None and b["expiry_ms"] > cutoff, "invalid contract horizon")
    require(b.get("responsibility_interval_usd") == sorted((short, long)), "invalid responsibility interval")
    require(value.get("mode") == "frozen_observation", "expected frozen observation")
    ids, cells = set(), set()
    for o in value.get("observations", []):
        require(o.get("boundary_id") == value["boundary_id"], "observation belongs to another boundary")
        require(isinstance(o.get("id"), str) and o["id"] not in ids, "duplicate observation id")
        ids.add(o["id"])
        k = o.get("level_usd")
        if k is not None:
            require(number(k) is not None and k > 0, "invalid observation price")
            require(o.get("short_offset_usd") == k - short and o.get("protection_offset_usd") == k - long,
                    "observation offsets mismatch")
        if o.get("kind") in ("space", "position", "obligation_inventory", "path", "flow_at_structure"):
            cell = (o["kind"], k)
            require(cell not in cells, "duplicate observation would double count")
            cells.add(cell)
        at = o.get("observed_at_ms")
        require(at is None or (number(at) is not None and 0 < at <= cutoff), "future observation")
        start, end = o.get("window_start_ms"), o.get("window_end_ms")
        require((start is None and end is None) or
                (number(start) is not None and number(end) is not None and 0 < start < end <= cutoff),
                "invalid or future observation window")


def region(k: float, b: Mapping[str, Any]) -> str:
    """10% of leg width is only a display bin, never an acceptance threshold."""
    short, long = b["short_usd"], b["protection_usd"]
    if min(abs(k - short), abs(k - long)) <= abs(short - long) * 0.1:
        return "near_boundary"
    return "outside_liability" if (k > short if b["side"] == "put" else k < short) else "inside_liability"


def _sum_known(rows: list[Mapping[str, Any]], key: str) -> float | None:
    vals = [number(row["measurements"].get(key)) for row in rows]
    vals = [v for v in vals if v is not None and v >= 0]
    return sum(vals) if vals else None


def _groups(board: Mapping[str, Any]) -> tuple[list[dict], list[dict], list[str], list[str], str]:
    b, obs = board["boundary"], board["observations"]
    spaces = {o["level_usd"]: o for o in obs if o["kind"] == "space"}
    inventories = {o["level_usd"]: o for o in obs if o["kind"] == "obligation_inventory"}
    paths = {o["level_usd"]: o for o in obs if o["kind"] == "path"
             and o.get("window_start_ms") == board["cutoff_ms"] - 86_400_000
             and o.get("window_end_ms") == board["cutoff_ms"]}
    flows = {o["level_usd"]: o for o in obs if o["kind"] == "flow_at_structure"}
    positions = [o for o in obs if o["kind"] == "position"]
    index = number(positions[0]["measurements"].get("index_usd")) if positions else None
    groups, flow_groups, refs, flow_refs = [], [], [], []
    for r in REGIONS:
        ks = sorted(k for k in spaces if region(k, b) == r)
        if not ks:
            continue
        oi = [inventories[k] for k in ks if k in inventories]
        path = [paths[k] for k in ks if k in paths]
        refs += [spaces[k]["id"] for k in ks] + [o["id"] for o in oi + path]
        groups.append({"region": r, "level_count": len(ks), "range_usd": [min(ks), max(ks)],
                       "between_index_and_short_count": sum(min(index, b["short_usd"]) < k < max(index, b["short_usd"])
                                                             for k in ks) if index is not None else None,
                       "oi_structure_levels": sum(any(role.startswith("oi_") for role in spaces[k]["measurements"]["roles"])
                                                  for k in ks),
                       "derived_structure_levels": sum(any(role.startswith("gamma_") or role == "pin"
                                                            for role in spaces[k]["measurements"]["roles"]) for k in ks),
                       "nearest_short_distance_usd": min(abs(k - b["short_usd"]) for k in ks),
                       "beyond_protection_count": sum(k < b["protection_usd"] if b["side"] == "put"
                                                      else k > b["protection_usd"] for k in ks),
                       "oi_call_btc_known": _sum_known(oi, "c"), "oi_put_btc_known": _sum_known(oi, "p"),
                       "oi_call_levels": sum(number(o["measurements"].get("c")) is not None for o in oi),
                       "oi_put_levels": sum(number(o["measurements"].get("p")) is not None for o in oi),
                       "path_levels": len(path),
                       "crossed_levels": sum(o["measurements"]["crossings"] > 0 for o in path)})
        # The frozen board already pairs flow with path. Recheck window identity here.
        eligible = []
        for k in ks:
            f, p = flows.get(k), paths.get(k)
            if not f or not p:
                continue
            if not (f.get("window_start_ms") == p.get("window_start_ms") == board["cutoff_ms"] - 86_400_000
                    and f.get("window_end_ms") == p.get("window_end_ms") == board["cutoff_ms"]):
                continue
            if all(number(f["measurements"].get(key)) is not None and f["measurements"][key] >= 0
                   for key in ("c_buy", "c_sell", "p_buy", "p_sell")):
                eligible.append(f)
        if eligible:
            values = {key: sum(f["measurements"][key] for f in eligible)
                      for key in ("c_buy", "c_sell", "p_buy", "p_sell")}
            flow_groups.append({"region": r, "observed_levels": len(eligible), "structure_levels": len(ks),
                                **{f"{k}_btc": v for k, v in values.items()},
                                "c_net_buy_btc": values["c_buy"] - values["c_sell"],
                                "p_net_buy_btc": values["p_buy"] - values["p_sell"]})
            flow_refs += [f["id"] for f in eligible]
    # Express the path relationship in existing prose; keep the wire fields unchanged.
    feedback = []
    if index is not None:
        between = {k for k in spaces if min(index, b["short_usd"]) < k < max(index, b["short_usd"])}
        for label, ks in (("指数至短腿间", between), ("其余位置", set(spaces) - between)):
            observed = [paths[k] for k in ks if k in paths]
            if not observed:
                continue
            crossed = sum(o["measurements"]["crossings"] > 0 for o in observed)
            scope = "结构" if len(observed) == len(ks) else "已读部分结构"
            state = "未见穿越" if not crossed else "均有穿越记录" if crossed == len(observed) else "部分有穿越记录"
            feedback.append(label + scope + state)
    return groups, flow_groups, sorted(set(refs)), sorted(set(flow_refs)), "；".join(feedback)


def build(put: Mapping[str, Any] | None, call: Mapping[str, Any] | None, *,
          cutoff_ms: int | None = None, unavailable: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Normally 5-7 statements for two sides; never pad missing evidence to five."""
    boards, unavailable = {"put": put, "call": call}, dict(unavailable or {})
    require(set(unavailable) <= set(SIDES), "unknown unavailable side")
    for side, value in boards.items():
        if value is not None:
            _board(value, side)
            require(side not in unavailable, "a side cannot be both present and unavailable")
    cuts = {b["cutoff_ms"] for b in boards.values() if b is not None}
    if cutoff_ms is not None:
        cuts.add(cutoff_ms)
    require(len(cuts) == 1, "paired boards require the same frozen cutoff")
    cutoff = cuts.pop()
    require(isinstance(cutoff, int) and not isinstance(cutoff, bool) and cutoff > 0, "invalid cutoff")
    if put is not None and call is not None:
        for key in ("expiry_ms", "underlying", "venue", "settlement_currency"):
            require(put["boundary"][key] == call["boundary"][key], "paired market/expiry mismatch")
    phenomena, sides, gaps = [], {}, []

    def add(kind: str, side_list: list[str], measurements: dict, refs: dict, description: str) -> None:
        phenomena.append({"id": f"p{len(phenomena) + 1}", "kind": kind, "sides": side_list,
                          "boundary_ids": {s: boards[s]["boundary_id"] for s in side_list},
                          "measurements": measurements, "source_observation_refs": refs,
                          "description": description})

    present = [s for s in SIDES if boards[s] is not None]
    expiries = {boards[s]["boundary"]["expiry_ms"] for s in present}
    if present:
        add("responsibility", present,
            {"hours_to_expiry": (boards[present[0]]["boundary"]["expiry_ms"] - cutoff) / 3_600_000}, {},
            "；".join(f"{s.upper()} 短腿{boards[s]['boundary']['short_usd']:g}/保护腿{boards[s]['boundary']['protection_usd']:g} USD"
                      for s in present)
            + f"，距到期{(boards[present[0]]['boundary']['expiry_ms'] - cutoff) / 3_600_000:g}h。"
            + "净对冲义务的方向与强度未识别；采用已观测空间、OI与路径作条件判断。")
    indices = []
    for side in SIDES:
        value = boards[side]
        if value is None:
            proof = unavailable.get(side)
            if proof is not None:
                require(set(proof) == {"reason", "asof_ms", "evidence_ref", "source_sha256",
                                       "side", "venue", "underlying", "expiry_ms"}, "invalid qualification proof")
                require(proof["side"] == side and proof["venue"] == "Deribit" and proof["underlying"] == "BTC"
                        and number(proof["expiry_ms"]) is not None and proof["expiry_ms"] > cutoff,
                        "qualification failure belongs to another contract scope")
                expiries.add(proof["expiry_ms"])
                require(proof["reason"] in UNAVAILABLE_REASONS and isinstance(proof["evidence_ref"], str)
                        and bool(proof["evidence_ref"].strip())
                        and re.fullmatch(r"[a-f0-9]{64}", str(proof["source_sha256"])) is not None,
                        "qualification failure needs explicit source evidence")
                require(number(proof["asof_ms"]) is not None and 0 <= cutoff - proof["asof_ms"] <= 300_000,
                        "qualification proof is stale or future")
            sides[side] = {"status": "unavailable" if proof else "blocked", "boundary": None,
                           "boundary_id": None, "board_sha256": None, "qualification_failure": proof}
            if not proof:
                gaps.append({"side": side, "code": "candidate_missing", "blocks_this_side": True})
            continue
        b, obs = value["boundary"], value["observations"]
        positions = [o for o in obs if o["kind"] == "position"]
        require(len(positions) <= 1, "multiple index observations")
        groups, flow_groups, refs, flow_refs, feedback = _groups(value)
        sides[side] = {"status": "assessable" if positions and groups else "blocked",
                       "boundary": dict(b), "boundary_id": value["boundary_id"],
                       "board_sha256": digest(value), "qualification_failure": None}
        if positions:
            position = positions[0]
            index = number(position["measurements"].get("index_usd"))
            require(index is not None and index > 0 and index == position["level_usd"], "invalid index observation")
            indices.append(index)
            adverse = -1 if side == "put" else 1
            ds, dl = (index - b["short_usd"]) * adverse, (index - b["protection_usd"]) * adverse
            zone = "outside_liability" if ds < 0 else "beyond_protection" if dl > 0 else "within_responsibility"
            add("position", [side], {"index_usd": index, "index_zone": zone,
                                    "short_distance_usd": abs(index - b["short_usd"]),
                                    "protection_distance_usd": abs(index - b["protection_usd"])},
                {side: [position["id"]]},
                f"{side.upper()} {b['short_usd']:g}/{b['protection_usd']:g}：指数{index:g} USD，"
                f"距短腿{abs(index-b['short_usd']):g} USD，位置为"
                + {"outside_liability": "责任外侧", "within_responsibility": "两腿责任区间内",
                   "beyond_protection": "越过保护腿"}[zone] + "。")
        else:
            gaps.append({"side": side, "code": "index_missing", "blocks_this_side": True})
        if groups:
            snippets = []
            for g in groups:
                lo, hi = g["range_usd"]
                span = f"{lo:g}" if lo == hi else f"{lo:g}–{hi:g}"
                sentence = f"{LABELS[g['region']]}{span} USD"
                oi = g["oi_put_btc_known"] if side == "put" else g["oi_call_btc_known"]
                count = g["oi_put_levels"] if side == "put" else g["oi_call_levels"]
                if oi is not None:
                    coverage = "已读部分" if count < g["level_count"] else ""
                    sentence += f"，{coverage}{side.upper()} OI {oi:g} BTC"
                snippets.append(sentence)
            add("structures", [side], {"near_radius_usd": abs(b["short_usd"] - b["protection_usd"]) * 0.1,
                                       "groups": groups}, {side: refs},
                f"{side.upper()} {b['short_usd']:g}/{b['protection_usd']:g}：" + "；".join(snippets) + "。"
                + ("前24h，" + feedback + "。" if feedback else ""))
        else:
            gaps.append({"side": side, "code": "spatial_geometry_missing", "blocks_this_side": True})
        if flow_groups:
            chunks = []
            for g in flow_groups:
                # Call and Put ordinary aggressors stay separate; no underlying direction.
                amounts = []
                for prefix, label in (("c", "Call"), ("p", "Put")):
                    net = g[f"{prefix}_net_buy_btc"]
                    gross = g[f"{prefix}_buy_btc"] + g[f"{prefix}_sell_btc"]
                    if gross:
                        action = f"净主动{'买' if net > 0 else '卖'}{abs(net):g} BTC" if net else "主动买卖相抵"
                        amounts.append(f"{label}普通{action}（总量{gross:g} BTC）")
                if amounts:
                    active = [o["level_usd"] for o in obs if o["id"] in flow_refs
                              and region(o["level_usd"], b) == g["region"]
                              and any(o["measurements"][k] > 0 for k in ("c_buy", "c_sell", "p_buy", "p_sell"))]
                    lo, hi = min(active), max(active)
                    span = f"{lo:g}" if lo == hi else f"{lo:g}–{hi:g}"
                    chunks.append(LABELS[g["region"]] + f"的{span} USD结构位：" + "、".join(amounts))
            add("flow", [side], {"window_start_ms": cutoff - 86_400_000, "window_end_ms": cutoff,
                                 "groups": flow_groups}, {side: flow_refs},
                f"{side.upper()} {b['short_usd']:g}/{b['protection_usd']:g}前24h，"
                + ("；".join(chunks) if chunks else "已覆盖结构位普通成交量为0")
                + "。与同组路径并读，不推导标的方向。")
    require(len(set(indices)) <= 1, "paired index observations conflict")
    require(len(expiries) <= 1, "qualification evidence and boards have different expiry scopes")
    assessable = [s for s in SIDES if sides[s]["status"] == "assessable"]
    for gap in gaps:
        gap["blocks_all_sides"] = not assessable
    return {"schema": SCHEMA, "mode": "frozen_observation", "cutoff_ms": cutoff, "sides": sides,
            "phenomena": phenomena, "critical_gaps": gaps,
            "coverage": {"assessable_sides": assessable, "at_least_one_side_assessable": bool(assessable)}}


def validate_assessment(assessment: Mapping[str, Any], view: Mapping[str, Any]) -> None:
    """Validate a separate AI judgement; never derive its acceptability from geometry."""
    require(set(assessment) == {"schema", "view_sha256", "decision", "at_least_one_acceptable", "selected_side",
                                "side_assessments", "scope", "execution_authorized"}, "invalid assessment fields")
    require(assessment["schema"] == ASSESSMENT and view.get("schema") == SCHEMA, "assessment/view schema mismatch")
    require(assessment["view_sha256"] == digest(view), "assessment belongs to another view")
    require(assessment["scope"] == "structure_only" and assessment["execution_authorized"] is False,
            "invalid assessment scope")
    require(isinstance(assessment["side_assessments"], dict) and set(assessment["side_assessments"]) == set(SIDES),
            "both sides must be assessed")
    facts = {p["id"]: p for p in view["phenomena"]}
    states = {}
    for side in SIDES:
        item = assessment["side_assessments"][side]
        require(set(item) == {"state", "phenomenon_refs", "reason"}, "invalid side assessment fields")
        state, refs = item["state"], item["phenomenon_refs"]
        require(isinstance(item["reason"], str) and bool(item["reason"].strip()), "side reason is required")
        require(isinstance(refs, list) and all(isinstance(r, str) for r in refs)
                and len(refs) == len(set(refs)), "invalid evidence references")
        require(all(r in facts and side in facts[r]["sides"] for r in refs), "evidence belongs to another side")
        status = view["sides"][side]["status"]
        if status == "assessable":
            require(state in ("acceptable", "unfavorable"), "assessable space cannot abstain for generic unknowns")
            require(any(facts[r]["kind"] in ("position", "structures") for r in refs),
                    "side judgement requires spatial evidence, not flow alone")
        elif status == "unavailable":
            require(state == "no_qualified_vertical", "qualification failure is not a missing observation")
        else:
            require(state == "unassessable", "missing evidence is not unfavorable evidence")
        states[side] = state
    acceptable = [s for s in SIDES if states[s] == "acceptable"]
    unresolved = any(s == "unassessable" for s in states.values())
    expected = "Accept" if acceptable else "Unknown" if unresolved else "Reject"
    flag = True if acceptable else None if unresolved else False
    require(assessment["decision"] == expected, "at least one acceptable side requires Accept; Reject requires both ruled out")
    require(assessment["at_least_one_acceptable"] is flag, "acceptable-side flag conflicts with both-side assessment")
    require(assessment["selected_side"] in acceptable if acceptable else assessment["selected_side"] is None,
            "selected side must be acceptable")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--put", type=Path)
    parser.add_argument("--call", type=Path)
    parser.add_argument("--cutoff-ms", type=int)
    parser.add_argument("--unavailable", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    read = lambda p: json.loads(p.read_text(encoding="utf-8-sig")) if p else None
    view = build(read(args.put), read(args.call), cutoff_ms=args.cutoff_ms, unavailable=read(args.unavailable))
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(view, stream, ensure_ascii=False, indent=2, allow_nan=False)


if __name__ == "__main__":
    main()
