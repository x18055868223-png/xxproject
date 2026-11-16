"""Frozen, boundary-anchored observations from case_file@0.1 and level_card@0.1.

No network, model calls, outcome reads or action selection. Upstream calculations
are reused. CLI accepts a frozen case and optionally its same-cutoff level card.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

try:
    from .astra_level_behaviour_v1 import levels_for
except ImportError:
    from astra_level_behaviour_v1 import levels_for

SCHEMA = "structural_board@0.1"
MINUTE = 60_000


def number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode()).hexdigest()


def entry(case: Mapping[str, Any], ref: str) -> dict[str, Any]:
    address = (case.get("refs") or {}).get(ref)
    if not isinstance(address, dict):
        return {}
    try:
        row = case["layers"][address["layer"]][address["index"]]
    except (KeyError, IndexError, TypeError):
        return {}
    return row if isinstance(row, dict) and row.get("ref") == ref else {}


def usable(row: Mapping[str, Any], cutoff: int, max_age_ms: int) -> bool:
    at = number(row.get("asof_ms"))
    return (row.get("quality") == "ok" and at is not None
            and 0 <= cutoff - at <= max_age_ms)


def build(case: Mapping[str, Any], level_card: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Produce observations at the frozen build cutoff, never relabel them current."""
    if case.get("schema") != "case_file@0.1":
        raise ValueError("expected case_file@0.1")
    if not isinstance(case.get("case_id"), str) or not case["case_id"]:
        raise ValueError("case_id is required")
    cutoff = (case.get("build") or {}).get("built_at_ms")
    if not isinstance(cutoff, int) or isinstance(cutoff, bool) or cutoff <= 0:
        raise ValueError("a frozen built_at_ms is required")
    contract_row = entry(case, "L1.contract")
    contract = contract_row.get("values") or {}
    side = contract.get("side")
    short = number(contract.get("short_strike_usd"))
    long = number(contract.get("long_strike_usd"))
    expiry = number(contract.get("expiry_ms"))
    quantity = number(contract.get("quantity_btc"))
    if (side not in ("put", "call") or short is None or long is None
            or min(short, long) <= 0 or expiry is None or expiry <= cutoff or not expiry.is_integer()
            or quantity is None or quantity <= 0
            or (side == "put" and long >= short) or (side == "call" and long <= short)
            or not usable(contract_row, cutoff, 5 * MINUTE)
            or not all(isinstance(contract.get(k), str) and contract[k]
                       for k in ("short_instrument", "long_instrument"))):
        raise ValueError("valid frozen contract boundaries and identity are required")
    if str(contract.get("venue", "")).lower() != "deribit":
        raise ValueError("this adapter requires a Deribit contract")
    boundary = {"venue": "Deribit", "underlying": "BTC",
                "side": side, "short_instrument": contract["short_instrument"],
                "long_instrument": contract["long_instrument"], "short_usd": short,
                "protection_usd": long, "expiry_ms": int(expiry), "quantity_btc": quantity,
                "responsibility_interval_usd": sorted((short, long)),
                "settlement_currency": "BTC"}
    if contract.get("settlement_currency") != "BTC" or contract.get("underlying") != "BTC":
        raise ValueError("explicit BTC underlying and settlement are required")
    boundary_id = digest(boundary)
    gaps: list[str] = []
    observations: list[dict[str, Any]] = []

    def add(kind: str, position: float | None, fields: dict[str, Any], ref: str,
            at: int | None, source_hash: str, text: str) -> None:
        # Only constructed literals and allowlisted numbers enter observation text.
        observations.append({"id": f"o{len(observations) + 1}", "kind": kind,
                             "boundary_id": boundary_id, "level_usd": position,
                             "short_offset_usd": position - short if position is not None else None,
                             "protection_offset_usd": position - long if position is not None else None,
                             "measurements": fields, "source_ref": ref, "observed_at_ms": at,
                             "window_start_ms": cutoff - 24 * 60 * MINUTE
                             if kind in ("path", "flow_at_structure", "inventory_change") else None,
                             "window_end_ms": cutoff
                             if kind in ("path", "flow_at_structure", "inventory_change") else None,
                             "source_sha256": source_hash,
                             "description": f"短腿 {short:g}、保护腿 {long:g} USD；{text}"})

    liability_row = entry(case, "L1.liability")
    liability = liability_row.get("values") or {}
    if (usable(liability_row, cutoff, 5 * MINUTE)
            and liability.get("starts_at_usd") == short and liability.get("protection_at_usd") == long):
        terms = {k: number(liability.get(k)) for k in ("usd_intrinsic_cap", "btc_contract_cap")}
        if terms["usd_intrinsic_cap"] is not None and terms["usd_intrinsic_cap"] > 0:
            add("contract_terms", short, terms, "L1.liability", int(liability_row["asof_ms"]),
                digest(liability_row), "复用合同责任核算；BTC赔付上界与USD内在值上界分列，不视为实际成交或净损失。")
        else:
            gaps.append("contract_terms_unavailable_or_mismatched")
    else:
        gaps.append("contract_terms_unavailable_or_mismatched")

    walls_row = entry(case, "L3.expiry_walls")
    walls = walls_row.get("values") or {}
    walls_ok = (usable(walls_row, cutoff, 5 * MINUTE)
                and walls.get("expiry_ms") == expiry and walls.get("stale") is not True)
    roles = levels_for(walls, contract, None) if walls_ok else levels_for({}, contract, None)
    if not walls_ok:
        gaps.append("contract_expiry_structure_missing_stale_or_mismatched")
    roles = {k: names for k, names in roles.items() if number(k) is not None and k > 0}
    for k, names in sorted(roles.items()):
        # Names are generated by the existing levels_for function, never copied prose.
        if all(n in ("short", "long") for n in names):
            continue
        add("space", k, {"roles": names}, "L3.expiry_walls", int(walls_row["asof_ms"]),
            digest(walls_row), f"本期结构位 {k:g} USD，记录类型 {' / '.join(names)}。")
    if not any(o["kind"] == "space" for o in observations):
        gaps.append("contract_expiry_structure_missing_stale_or_mismatched")

    gex_row = entry(case, "L3.gex.structure")
    gex = gex_row.get("values") or {}
    if usable(gex_row, cutoff, 60 * MINUTE):
        for name in ("flip_point", "total_net_gex"):
            value = number(gex.get(name))
            if value is not None and (name != "flip_point" or value > 0):
                add("market_structure", value if name == "flip_point" else None,
                    {name: value}, "L3.gex.structure", int(gex_row["asof_ms"]), digest(gex_row),
                    f"市场口径 {name}={value:g}；与本期行权价义务的对应关系未知。")
    else:
        gaps.append("market_gamma_context_unavailable")
    if not any(o["kind"] == "market_structure" for o in observations):
        gaps.append("market_gamma_context_unavailable")

    if level_card is not None:
        key = f"{contract['short_instrument']}|{contract['long_instrument']}"
        if (level_card.get("schema") != "level_card@0.1" or level_card.get("rules") != "level_rules@0.1"
                or not case.get("case_id") or level_card.get("case_id") != case["case_id"]
                or level_card.get("contract_key") != key or level_card.get("expiry_ms") != expiry
                or level_card.get("side") != side or level_card.get("read_at_ms") != cutoff):
            raise ValueError("level card must bind the same case, legs, side, expiry and cutoff")
        card_hash = digest(level_card)
        # Native level_card exposes the reading cutoff, not its individual source clocks.
        # Keep those clocks unknown rather than stamping every source with the cutoff.
        gaps.append("level_source_clocks_not_exported")
        index = number(level_card.get("index_usd"))
        if index is not None and index > 0:
            add("position", index, {"index_usd": index}, "level_card.index_usd", None,
                card_hash, f"冻结指数 {index:g} USD。")
        else:
            gaps.append("index_at_read_unavailable")
        for lv in level_card.get("levels") or []:
            k = number(lv.get("k"))
            if k not in roles:
                continue
            oi = lv.get("oi") or {}
            values = {key: number(oi.get(key)) for key in ("c", "p")}
            values = {key: value if value is not None and value >= 0 else None
                      for key, value in values.items()}
            if any(v is not None for v in values.values()):
                add("obligation_inventory", k, values, "level_card.levels.oi", None, card_hash,
                    f"行权价 {k:g} 的Call/Put未平仓量分别为 {values['c']} / {values['p']}；持仓方身份与净对冲方向未知。")
            change = (lv.get("oi_change") or {}).get("24h") or {}
            if change.get("gap") is False:
                changes = {"oi_change_c": number(change.get("c")), "oi_change_p": number(change.get("p"))}
                if all(v is not None for v in changes.values()):
                    add("inventory_change", k, changes, "level_card.levels.oi_change.24h", None,
                        card_hash, f"行权价 {k:g} 前24小时Call/Put持仓变化 {changes['oi_change_c']} / {changes['oi_change_p']}。")
            path = lv.get("before_24h") or {}
            path_values = {key: number(path.get(key)) for key in ("crossings", "closest_usd")}
            if (all(v is not None and v >= 0 for v in path_values.values())
                    and path_values["crossings"].is_integer()):
                add("path", k, path_values, "level_card.levels.before_24h", None, card_hash,
                    f"前24小时路径穿越 {k:g} 共 {path_values['crossings']:g} 次，最近距离 {path_values['closest_usd']:g} USD。")
                flow = (lv.get("flow") or {}).get("24h") or {}
                values = {name: number(flow.get(name)) for name in ("c_buy", "c_sell", "p_buy", "p_sell")}
                if (flow.get("gap") is False and flow.get("truncated") is False
                        and all(v is not None and v >= 0 for v in values.values())):
                    add("flow_at_structure", k, {**values, **path_values},
                        "level_card.levels.flow.24h+before_24h", None, card_hash,
                        f"同一行权价 {k:g}、同一24小时窗，Call主动买/卖 {values['c_buy']:g}/{values['c_sell']:g} BTC，"
                        f"Put主动买/卖 {values['p_buy']:g}/{values['p_sell']:g} BTC，路径穿越 {path_values['crossings']:g} 次。"
                        "挑战或确认义务的因果关系未知，成交方身份未知。")
                else:
                    gaps.append("flow_unavailable_or_incomplete")
            else:
                gaps.append("path_window_unavailable")
    else:
        gaps.append("same_case_level_card_unavailable")
    inventory_levels = {o["level_usd"] for o in observations if o["kind"] == "obligation_inventory"
                        and all(v is not None for v in o["measurements"].values())}
    inventory_state = "observed" if inventory_levels == set(roles) else "partial" if inventory_levels else "unknown"
    if inventory_state != "observed":
        gaps.append("obligation_inventory_incomplete")
    if not any(o["kind"] == "position" for o in observations):
        gaps.append("index_at_read_unavailable")
    # A fixed presentation order: the skeleton precedes auxiliary flow, never a score.
    order = {kind: i for i, kind in enumerate(("position", "contract_terms", "space",
             "obligation_inventory", "inventory_change", "market_structure", "path", "flow_at_structure"))}
    observations.sort(key=lambda o: order[o["kind"]])
    for i, observation in enumerate(observations, 1):
        observation["id"] = f"o{i}"
    return {"schema": SCHEMA, "case_id": str(case.get("case_id") or ""),
            "cutoff_ms": cutoff, "mode": "frozen_observation", "boundary_id": boundary_id,
            "boundary": boundary, "observations": observations, "background": [],
            "obligation_state": {"inventory_coverage": inventory_state,
                                 "dealer_gamma": "unknown", "stability": "unknown",
                                 "movement": "unknown", "path_feedback": "unknown"},
            "unknowns": sorted(set(gaps + ["dealer_position_direction", "local_hedging_feedback",
                                           "causal_flow_confirmation", "structure_movement"])),
            "provenance": {"case_sha256": digest(case),
                           "contract_source_ref": "L1.contract",
                           "contract_asof_ms": int(contract_row["asof_ms"]),
                           "contract_sha256": digest(contract_row),
                           "level_card_sha256": digest(level_card) if level_card is not None else None,
                           "backfilled": bool((level_card or {}).get("backfilled", False))}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--level-card", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    case = json.loads(args.case.read_text(encoding="utf-8-sig"))
    level = json.loads(args.level_card.read_text(encoding="utf-8-sig")) if args.level_card else None
    board = build(case, level)
    # Exclusive creation prevents accidental replacement of a frozen observation.
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(board, stream, ensure_ascii=False, indent=2, allow_nan=False)


if __name__ == "__main__":
    main()
