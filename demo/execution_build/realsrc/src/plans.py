# -*- coding: utf-8 -*-
"""
方案库构建、评估与排序（plan_*）。

计划轮枚举所有符合范围的同期垂直信用价差备选，每个 = 一组(短腿 + 同到期更价外保护腿)，
按 胜率 / 盈亏比 / 信号契合 计算综合分排序，输出方案库（含方案号 + 推荐标签）。

口径（启发式，用于排序比较；非精确定价）：
- 胜率 ≈ 1 - |短腿 delta|（短腿到期 OTM 近似概率）。
- 同期垂直：保护腿与短腿同到期、到期一起了结。
    净 credit = (短腿 mark - 保护腿 mark) × 数量；最大亏损 = 腿宽折BTC - 净credit（**硬封顶**）。
- 盈亏比 = 净credit / 最大亏损（仅二者均为正时有意义）。
纯函数，便于单测。
"""
from accounting import (acct_option_fee_ccy, acct_full_burn, acct_spread_cost)

MODE_VERTICAL = 2  # 唯一结构标识（保留数值 2，兼容菜单/展示读取 p["mode"]）


def plan_mode_cn(mode=MODE_VERTICAL):
    return "同期垂直"


def plan_expiry_label(instrument_name):
    """从合约名取期号(到期标签)，如 BTC-1JUN26-74000-C → 1JUN26。"""
    if not instrument_name:
        return "—"
    parts = instrument_name.split("-")
    return parts[1] if len(parts) >= 2 else "—"


def plan_id(mode, short_instrument, protection_instrument):
    """按结构内容生成**稳定唯一编号**（确定性，不随排序/进程变化）。
    下单轮按此编号匹配，避免「方案重排后选错执行」。返回 4 位数 1000-9999。"""
    key = "%s|%s|%s" % (mode, short_instrument or "", protection_instrument or "")
    h = 0
    for ch in key:
        h = (h * 131 + ord(ch)) % 1000000007
    return 1000 + (h % 9000)


def plan_win_rate(short_delta):
    return None if short_delta is None else 1.0 - abs(short_delta)


def plan_width_btc(width_usd, index_price, amount):
    if not width_usd or not index_price:
        return None
    return (width_usd / index_price) * amount


def plan_effective_credit(short_prem, prot_prem):
    """垂直：同到期了结，净credit = 短腿权利金 - 保护腿权利金，无复用/残值。
    返回 (net_credit, net_credit, protection_premium, 0.0)（保留四元组形态兼容既有读取）。
    short_prem/prot_prem 为持仓口径权利金(已×数量)。"""
    if short_prem is None or prot_prem is None:
        return None, None, None, None
    single = short_prem - prot_prem
    return single, single, prot_prem, 0.0


def plan_max_loss(width_usd, index_price, effective_net_credit, amount):
    wb = plan_width_btc(width_usd, index_price, amount)
    if wb is None or effective_net_credit is None:
        return None
    return max(wb - effective_net_credit, 0.0)


def plan_rr(net_credit, max_loss):
    if net_credit is None or max_loss is None or max_loss <= 0 or net_credit <= 0:
        return None
    return net_credit / max_loss


def plan_ev(win_rate, net_credit, max_loss):
    """期望值/周期(BTC) = 胜率×有效净credit − (1−胜率)×最大亏损（最坏亏损口径，仅作参考）。"""
    if win_rate is None or net_credit is None or max_loss is None:
        return None
    return win_rate * net_credit - (1.0 - win_rate) * max_loss


def plan_breakeven(want_call, short_strike, short_mark, prot_mark, spot):
    """到期盈亏平衡价(近似)：短腿行权 ± 每张净credit折USD。
    call: 价格高于此开始亏；put: 价格低于此开始亏。"""
    if short_strike is None or short_mark is None or prot_mark is None or not spot:
        return None
    net_pc_usd = (short_mark - prot_mark) * spot      # 每张净credit折 USD(价格点)
    return short_strike + net_pc_usd if want_call else short_strike - net_pc_usd


def plan_credit_on_margin(net_credit_effective, im_with_protection):
    """净credit / 占用保证金（每周期保证金回报率）——本策略价值核心指标。"""
    if net_credit_effective is None or not im_with_protection or im_with_protection <= 0:
        return None
    return net_credit_effective / im_with_protection


def plan_preferred_delta(signal_state, confidence, delta_range):
    """信号强度 → 偏好短腿 |delta|：弱/低置信偏低(高胜率)，强/高置信偏高(高盈亏比)。"""
    lo, hi = delta_range
    c = (confidence if confidence is not None else 50) / 100.0
    base = lo + (hi - lo) * c
    if signal_state == "TRADE_SUPPORT_STRONG":
        base = min(hi, base + 0.05)
    return base


def plan_signal_fit(short_delta, preferred_delta, scale=0.25):
    if short_delta is None:
        return 0.0
    return max(0.0, 1.0 - abs(abs(short_delta) - preferred_delta) / scale)


def plan_assemble(amount, spot, min_ratio,
                  preferred_delta, want_call,
                  short, sq, prot, pq, spm, pm_ok, account_model,
                  short_dte_hours=None, prot_dte_hours=None):
    """组装一个同期垂直候选方案 dict（不含综合分/方案号，由 plan_rank 补充）。"""
    sq, pq = sq or {}, pq or {}
    short_mark, prot_mark = sq.get("mark"), pq.get("mark")
    short_delta = (short or {}).get("_delta", sq.get("delta"))
    width = abs(prot.get("strike", 0) - short.get("strike", 0)) if (short and prot) else None

    premium_income = (short_mark * amount) if short_mark is not None else None
    protection_premium = (prot_mark * amount) if prot_mark is not None else None
    covered = 1
    eff_credit, single_credit, amort, residual = plan_effective_credit(
        premium_income, protection_premium)
    max_loss = plan_max_loss(width, spot, eff_credit, amount)
    rr = plan_rr(eff_credit, max_loss)

    fee = 0.0
    if short_mark is not None:
        fee += acct_option_fee_ccy(short_mark, amount)
    if prot_mark is not None:
        fee += acct_option_fee_ccy(prot_mark, amount)
    full_burn = (acct_full_burn(protection_premium, acct_option_fee_ccy(prot_mark, amount))
                 if prot_mark is not None else None)

    relief_ratio = (spm or {}).get("relief_ratio")
    relief_ok = isinstance(relief_ratio, (int, float)) and relief_ratio >= min_ratio
    no_bid = sq.get("best_bid") in (None, 0)

    reject = None
    if not short:
        reject = "无合适短腿"
    elif not prot:
        reject = "无合格保护腿"
    elif no_bid:
        reject = "短腿无买盘"
    elif not relief_ok:
        reject = "S:PM 释放不足"
    elif not pm_ok:
        reject = "账户非组合保证金"
    qualified = reject is None

    short_inst = (short or {}).get("instrument_name")
    prot_inst = (prot or {}).get("instrument_name")
    return {
        "id": plan_id(MODE_VERTICAL, short_inst, prot_inst),
        "short_expiry_label": plan_expiry_label(short_inst),
        "protection_expiry_label": plan_expiry_label(prot_inst),
        "mode": MODE_VERTICAL, "mode_cn": plan_mode_cn(),
        "short_instrument": (short or {}).get("instrument_name"),
        "short_strike": (short or {}).get("strike"), "short_delta": short_delta,
        "short_mark": short_mark, "short_bid": sq.get("best_bid"),
        "short_ask": sq.get("best_ask"), "short_tick": sq.get("tick"),
        "short_dte_hours": short_dte_hours, "short_expiry": (short or {}).get("expiration_timestamp"),
        "protection_instrument": (prot or {}).get("instrument_name"),
        "protection_strike": (prot or {}).get("strike"), "protection_delta": pq.get("delta"),
        "protection_mark": prot_mark, "protection_bid": pq.get("best_bid"),
        "protection_ask": pq.get("best_ask"), "protection_tick": pq.get("tick"),
        "protection_dte_days": (round(prot_dte_hours / 24.0, 2) if prot_dte_hours else None),
        "protection_dte_hours": prot_dte_hours,
        "protection_expiry": (prot or {}).get("expiration_timestamp"),
        "width": width, "amount": amount, "spot": spot,
        "win_rate": plan_win_rate(short_delta),
        "premium_income": premium_income, "protection_premium": protection_premium,
        "covered_cycles": covered, "residual_value": residual,
        "amortized_cost_per_cycle": amort,
        "net_credit_single": single_credit, "net_credit_effective": eff_credit,
        "max_loss": max_loss, "rr": rr,
        "ev": plan_ev(plan_win_rate(short_delta), eff_credit, max_loss),
        "breakeven": plan_breakeven(want_call, (short or {}).get("strike"),
                                    short_mark, prot_mark, spot),
        "credit_on_margin": plan_credit_on_margin(eff_credit, (spm or {}).get("im_with_protection")),
        "entry_fee": fee, "full_burn": full_burn,
        "spread_cost": acct_spread_cost(sq.get("best_bid"), sq.get("best_ask"), amount),
        "signal_fit": plan_signal_fit(short_delta, preferred_delta),
        "im_short_only": (spm or {}).get("im_short_only"),
        "im_with_protection": (spm or {}).get("im_with_protection"),
        "margin_relief_abs": (spm or {}).get("relief_abs"),
        "margin_relief_ratio": relief_ratio,
        "pm_ok": pm_ok, "account_model": account_model,
        "qualified": qualified, "reject_reason": reject,
        "composite": None, "plan_no": None, "tags": [],
    }


def plan_prelim_score(c, weights):
    """无 S:PM 的初筛分（用于枚举后裁剪 top-K）。"""
    wr = c.get("win_rate") or 0.0
    rr = c.get("rr") or 0.0
    return (weights["win_rate"] * wr + weights["rr"] * min(rr, 1.0)
            + weights["signal"] * (c.get("signal_fit") or 0.0))


def plan_rank(cands, weights, menu_size):
    """对候选打综合分、排序、确保两种模式均入选、编号、打推荐标签，返回菜单 list。"""
    pool = [c for c in cands if c.get("qualified")] or list(cands)
    rrs = [c["rr"] for c in pool if isinstance(c.get("rr"), (int, float)) and c["rr"] > 0]
    max_rr = max(rrs) if rrs else 1.0
    for c in pool:
        wr = c.get("win_rate") or 0.0
        rr = c.get("rr") or 0.0
        rr_norm = min(rr / max_rr, 1.0) if max_rr else 0.0
        c["rr_norm"] = rr_norm
        c["composite"] = (weights["win_rate"] * wr + weights["rr"] * rr_norm
                          + weights["signal"] * (c.get("signal_fit") or 0.0))
    ranked = sorted(pool, key=lambda c: c["composite"], reverse=True)
    menu = ranked[:menu_size]
    for i, c in enumerate(menu, start=1):
        c["plan_no"] = i
    _assign_tags(menu)
    return menu


def _assign_tags(menu):
    for c in menu:
        c["tags"] = []
    if not menu:
        return
    max(menu, key=lambda c: c.get("win_rate") or 0.0)["tags"].append("高胜率")
    rr_c = [c for c in menu if isinstance(c.get("rr"), (int, float)) and c["rr"] > 0]
    if rr_c:
        max(rr_c, key=lambda c: c["rr"])["tags"].append("高盈亏比")
    ev_c = [c for c in menu if isinstance(c.get("ev"), (int, float))]
    if ev_c:
        max(ev_c, key=lambda c: c["ev"])["tags"].append("高期望")
    max(menu, key=lambda c: c.get("composite") or 0.0)["tags"].append("均衡")
