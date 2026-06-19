# -*- coding: utf-8 -*-
"""
执行层（exec_*，§10）：保护腿优先、maker-only、只追一步、禁 taker。

价格计算为纯函数（可单测）；下单/轮询/撤单走 dbt_*。
进场门控经 gates.gate_decision(ENTRY)：ALLOW_ENTRY_TRADING=False（或 KILL_NEW_RISK /
EMERGENCY_REDUCE_ONLY）时，进场真实下单短路为「记录意图」（空跑核对）。
"""

import math

from config import (ALLOW_ENTRY_TRADING, KILL_NEW_RISK, EMERGENCY_REDUCE_ONLY,
                    MAX_CHASE_STEPS, CHASE_WAIT_SECONDS, MAX_SPREAD_RATIO,
                    UNWIND_PROTECTION_ON_NO_SHORT)
from gates import gate_decision, ACTION_ENTRY
from deribit_io import (dbt_ticker, dbt_get_instrument, dbt_place_order,
                       dbt_get_order_state, dbt_cancel)
from binance_io import bnc_place_hedge
from accounting import acct_option_fee_ccy
from position import entry_credit_capped_index, entry_net_credit
from fmz_shim import Log, Sleep


# ---------- 纯价格计算（§10.3）----------

def _round_to_tick(price, tick, mode):
    if not tick:
        return price
    n = price / tick
    # 加微小 epsilon 抵消浮点误差（如 0.0013-0.0001 落在 0.00119999…，floor 会误降一格）
    n = math.floor(n + 1e-9) if mode == "down" else math.ceil(n - 1e-9)
    return round(n * tick, 10)


def exec_buy_price(mark, best_ask, tick, step):
    """买 protection：step0=min(mark,ask-tick)；每追一步 +tick，封顶 ask-tick。"""
    cap = best_ask - tick
    base = min(mark, cap)
    p = base + step * tick
    return _round_to_tick(min(p, cap), tick, "down")


def exec_sell_price(mark, best_bid, tick, step):
    """卖 short：step0=max(mark,bid+tick)；每追一步 -tick，封底 bid+tick。"""
    floor_p = best_bid + tick
    base = max(mark, floor_p)
    p = base - step * tick
    return _round_to_tick(max(p, floor_p), tick, "up")


def exec_price_for(side, mark, best_bid, best_ask, tick, step):
    return (exec_buy_price(mark, best_ask, tick, step) if side == "buy"
            else exec_sell_price(mark, best_bid, tick, step))


# ---------- 行情快照 ----------

def exec_quote(instrument):
    """返回 {mark, best_bid, best_ask, tick} 或 None。"""
    t = dbt_ticker(instrument)
    meta = dbt_get_instrument(instrument)
    if not t or not meta:
        return None
    return {
        "mark": t.get("mark_price"),
        "mark_iv": t.get("mark_iv"),
        "best_bid": t.get("best_bid_price"),
        "best_ask": t.get("best_ask_price"),
        "tick": meta.get("tick_size"),
        "underlying": t.get("underlying_price"),
        "delta": (t.get("greeks") or {}).get("delta"),
        "gamma": (t.get("greeks") or {}).get("gamma"),
    }


def exec_spread_ratio(q):
    """相对价差 (ask-bid)/mid；缺数据返回 None。"""
    if not q:
        return None
    bid, ask = q.get("best_bid"), q.get("best_ask")
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    return (ask - bid) / mid if mid > 0 else None


def exec_plan_prices(side, instrument, amount):
    """返回该腿的下单意图：计划价(含追价档)+盘口，供「将下达订单」意图表展示。"""
    q = exec_quote(instrument)
    if not q or q.get("best_bid") is None or q.get("best_ask") is None:
        return {"instrument": instrument, "side": side, "amount": amount, "prices": [], "quote": q}
    prices = [exec_price_for(side, q["mark"], q["best_bid"], q["best_ask"], q["tick"], s)
              for s in range(MAX_CHASE_STEPS + 1)]
    return {"instrument": instrument, "side": side, "amount": amount, "prices": prices,
            "mark": q.get("mark"), "best_bid": q.get("best_bid"), "best_ask": q.get("best_ask"),
            "spread_ratio": exec_spread_ratio(q)}


def _extract_order(resp):
    if not resp:
        return None
    return resp.get("order") if isinstance(resp, dict) and "order" in resp else resp


# ---------- maker-only 成交（只追一步）----------

def exec_maker_only_fill(side, instrument, target_amount, label=None):
    """返回 dict：
       {filled, avg_price, price0, final_price, dry, steps_used, quote}
    空跑(dry)时只计算并记录意图，不下单（filled=0, dry=True）。"""
    q = exec_quote(instrument)
    if not q or q["best_bid"] is None or q["best_ask"] is None:
        Log("[exec] 盘口缺失，跳过:", instrument)
        return {"filled": 0.0, "dry": False, "quote": q, "reason": "NO_QUOTE"}

    price0 = exec_price_for(side, q["mark"], q["best_bid"], q["best_ask"], q["tick"], 0)
    # 进场门控（ENTRY）：exec_open_structure 为唯一调用方；退出/对冲执行器后续各自传专属门控
    live = gate_decision(ACTION_ENTRY, ALLOW_ENTRY_TRADING, False, False,
                         KILL_NEW_RISK, EMERGENCY_REDUCE_ONLY)["allowed"]

    if not live:
        intents = [exec_price_for(side, q["mark"], q["best_bid"], q["best_ask"], q["tick"], s)
                   for s in range(MAX_CHASE_STEPS + 1)]
        Log("[exec][DRY] 意图 %s %s amt=%s 计划价(含追价)=%s 盘口=%s/%s mark=%s" %
            (side, instrument, target_amount, intents, q["best_bid"], q["best_ask"], q["mark"]))
        return {"filled": 0.0, "dry": True, "price0": price0,
                "intended_prices": intents, "quote": q}

    # 实盘成交价守门：价差过宽不下单（防高磨损/难成交）
    sr = exec_spread_ratio(q)
    if sr is not None and sr > MAX_SPREAD_RATIO:
        Log("[exec] 价差过宽 %.0f%% > 上限 %.0f%%，放弃下单: %s" %
            (sr * 100, MAX_SPREAD_RATIO * 100, instrument))
        return {"filled": 0.0, "dry": False, "quote": q, "reason": "WIDE_SPREAD"}

    filled = 0.0
    avg_acc = 0.0
    final_price = price0
    steps_used = 0
    for step in range(MAX_CHASE_STEPS + 1):
        remaining = target_amount - filled
        if remaining <= 0:
            break
        price = exec_price_for(side, q["mark"], q["best_bid"], q["best_ask"], q["tick"], step)
        final_price = price
        steps_used = step
        resp = dbt_place_order(side, instrument, remaining, price,
                               post_only=True, reject_post_only=True, label=label)
        order = _extract_order(resp)
        if order is None:
            # reject_post_only 拒单（会越价）→ 视为需要追一步
            Log("[exec] 挂单被拒/失败 step=%s price=%s，尝试追价" % (step, price))
            continue
        oid = order.get("order_id")
        # 等待后查状态
        Sleep(int(CHASE_WAIT_SECONDS * 1000))
        st = _extract_order(dbt_get_order_state(oid)) or order
        fa = st.get("filled_amount") or 0.0
        if fa > 0:
            ap = st.get("average_price") or price
            avg_acc += ap * fa
            filled += fa
        state = st.get("order_state")
        if state not in ("filled",) and (target_amount - filled) > 0:
            # 未完全成交 → 撤掉残单，进入下一步追价
            dbt_cancel(oid)
        if filled >= target_amount:
            break

    avg_price = (avg_acc / filled) if filled > 0 else final_price
    return {"filled": filled, "avg_price": avg_price, "price0": price0,
            "final_price": final_price, "dry": False, "steps_used": steps_used,
            "quote": q}


# ---------- 保护腿优先开仓（§10.1）----------

def exec_open_structure(short_instrument, protection_instrument, amount):
    """先买 protection，再以 min(amount, 已成交保护量) 卖 short。
    返回 {protection_fill, short_fill, short_amount}。
    空跑下两腿都只记录意图。"""
    prot = exec_maker_only_fill("buy", protection_instrument, amount,
                                label="prot")
    if prot.get("dry"):
        short = exec_maker_only_fill("sell", short_instrument, amount, label="short")
        return {"protection_fill": prot, "short_fill": short, "short_amount": amount,
                "dry": True}

    filled_prot = prot.get("filled", 0.0)
    if filled_prot <= 0:
        Log("[exec] 保护腿未成交，按保护腿优先原则不卖 short")
        return {"protection_fill": prot, "short_fill": None, "short_amount": 0.0,
                "dry": False}

    short_amount = min(amount, filled_prot)   # 硬保证 short <= protection 可用量
    short = exec_maker_only_fill("sell", short_instrument, short_amount, label="short")
    result = {"protection_fill": prot, "short_fill": short,
              "short_amount": short_amount, "dry": False}
    # 短腿未成交 → 自动 maker 卖回保护腿，避免裸保护（一次尝试）
    if (short or {}).get("filled", 0.0) <= 0 and UNWIND_PROTECTION_ON_NO_SHORT:
        Log("[exec] 短腿未成交，自动卖回保护腿避免裸保护:", protection_instrument)
        result["unwind"] = exec_maker_only_fill("sell", protection_instrument,
                                                filled_prot, label="unwind")
    return result


# ---------- 开仓活动（entry campaign）：跨轮持久 maker、信用底线约束、保护腿先成交 ----------

def _post_maker_once(side, instrument, amount, price, label):
    """单次 post-only 挂单(给定价)，等一周期，查成交，撤未成交后再查捕捉晚到成交。返回 filled。"""
    if not amount or amount <= 0 or price is None or price <= 0:
        return 0.0
    resp = dbt_place_order(side, instrument, amount, price,
                           post_only=True, reject_post_only=True, label=label)
    order = _extract_order(resp)
    if order is None:
        return 0.0
    oid = order.get("order_id")
    Sleep(int(CHASE_WAIT_SECONDS * 1000))
    st = _extract_order(dbt_get_order_state(oid)) or order
    filled = st.get("filled_amount") or 0.0
    if st.get("order_state") not in ("filled",) and (amount - filled) > 0:
        dbt_cancel(oid)
        st2 = _extract_order(dbt_get_order_state(oid)) or st
        if (st2.get("filled_amount") or 0.0) > filled:
            filled = st2.get("filled_amount")
    return filled


def exec_entry_campaign_step(prot_inst, short_inst, amount, credit_floor, max_tick_steps,
                             attempt, prot_done_qty, short_done_qty, allow_live, label="entry"):
    """开仓活动一轮：保护腿先成交，价格在「净 credit ≥ credit_floor」内逐 tick 改善(本轮档=min(attempt,信用上限档))。
    跨轮持久（每轮一次 post-only）。allow_live=False → 仅意图(dry)。
    返回 {quotes_ok, credit_ok, dry, prot_price, short_price, net_credit, n_used, prot_fill, short_fill, reason}。"""
    pq, sq = exec_quote(prot_inst), exec_quote(short_inst)
    quotes_ok = bool(pq and sq and pq.get("mark") is not None and sq.get("mark") is not None
                     and pq.get("best_ask") is not None and pq.get("best_bid") not in (None, 0)
                     and sq.get("best_bid") not in (None, 0) and sq.get("best_ask") is not None)
    if not quotes_ok:
        return {"quotes_ok": False, "credit_ok": False, "dry": (not allow_live),
                "prot_fill": 0.0, "short_fill": 0.0, "reason": "NO_QUOTE"}
    steps = max(0, int(max_tick_steps))
    prot_buy_prices = [exec_buy_price(pq["mark"], pq["best_ask"], pq["tick"], n) for n in range(steps + 1)]
    short_sell_prices = [exec_sell_price(sq["mark"], sq["best_bid"], sq["tick"], n) for n in range(steps + 1)]
    fees = acct_option_fee_ccy(pq["mark"], amount) + acct_option_fee_ccy(sq["mark"], amount)
    i_cap = entry_credit_capped_index(prot_buy_prices, short_sell_prices, amount, fees, credit_floor)
    if i_cap < 0:
        nc0 = entry_net_credit(short_sell_prices[0], prot_buy_prices[0], amount, fees)
        return {"quotes_ok": True, "credit_ok": False, "dry": (not allow_live), "net_credit": nc0,
                "prot_fill": 0.0, "short_fill": 0.0, "reason": "BELOW_CREDIT_FLOOR"}
    n = min(max(0, int(attempt)), i_cap)
    prot_price, short_price = prot_buy_prices[n], short_sell_prices[n]
    net_credit = entry_net_credit(short_price, prot_price, amount, fees)
    if not allow_live:
        return {"quotes_ok": True, "credit_ok": True, "dry": True, "prot_price": prot_price,
                "short_price": short_price, "net_credit": net_credit, "n_used": n,
                "prot_fill": 0.0, "short_fill": 0.0, "reason": "ENTRY_DRYRUN"}
    prot_fill = 0.0
    if (prot_done_qty or 0.0) < amount - 1e-12:                 # 保护腿先成交（持久重挂）
        prot_fill = _post_maker_once("buy", prot_inst, amount - (prot_done_qty or 0.0),
                                     prot_price, label + "_prot")
    short_cap = min(amount, (prot_done_qty or 0.0) + prot_fill) - (short_done_qty or 0.0)
    short_fill = 0.0
    if short_cap > 1e-12:                                       # 短腿数量 ≤ 已成交保护腿量
        short_fill = _post_maker_once("sell", short_inst, short_cap, short_price, label + "_short")
    return {"quotes_ok": True, "credit_ok": True, "dry": False, "prot_price": prot_price,
            "short_price": short_price, "net_credit": net_credit, "n_used": n,
            "prot_fill": prot_fill, "short_fill": short_fill, "reason": "ENTRY_STEP"}


# ---------- 低成本退出：买回卖方短腿（§7.3；每轮一次、价格 ≤ 预算上限、post-only）----------

def exec_exit_buyback_step(short_instrument, target_amount, price_cap, allow_live,
                           allow_taker=False, label="exit_short"):
    """退出活动一轮：买回（平）卖方短腿。
    - **止盈退出**(allow_taker=False)：被动 post-only，买价 ≤ min(ask−tick, price_cap)，patient 不越价。
    - **风险退出**(allow_taker=True)：可**越价吃单**至 price_cap（限价=price_cap、非 post-only，
      扫所有 ask ≤ cap 的卖盘、残量挂 cap）；成本仍硬封在 price_cap·qty 内（由风险退出预算反推）。
    allow_live=False → 仅返回意图(dry)。撤未成交单后再查一次以捕捉晚到成交。
    返回 {filled, avg_price, dry, price, taker, reason}。"""
    q = exec_quote(short_instrument)
    if not q or q.get("best_bid") is None or q.get("best_ask") is None or q.get("mark") is None:
        return {"filled": 0.0, "dry": (not allow_live), "reason": "NO_QUOTE"}
    tick = q.get("tick") or 0.0
    if allow_taker:
        price = price_cap                       # 限价=预算上限：≤cap 的卖盘成交、残量挂 cap（成本硬封）
        post_only = False
    else:
        maker_safe = (q["best_ask"] - tick) if tick else q["best_bid"]   # 最高仍为 maker 的买价
        price = min(maker_safe, price_cap)
        post_only = True
    if price <= 0 or price > price_cap + 1e-12:
        return {"filled": 0.0, "dry": (not allow_live), "price": price, "reason": "ABOVE_BUDGET_CAP"}
    if not allow_live:
        return {"filled": 0.0, "dry": True, "price": price, "taker": allow_taker, "reason": "EXIT_DRYRUN"}
    resp = dbt_place_order("buy", short_instrument, target_amount, price,
                           post_only=post_only, reject_post_only=post_only, label=label)
    order = _extract_order(resp)
    if order is None:
        return {"filled": 0.0, "dry": False, "price": price, "taker": allow_taker,
                "reason": ("ORDER_REJECTED" if allow_taker else "POST_ONLY_REJECTED")}
    oid = order.get("order_id")
    Sleep(int(CHASE_WAIT_SECONDS * 1000))
    st = _extract_order(dbt_get_order_state(oid)) or order
    filled = st.get("filled_amount") or 0.0
    avg = st.get("average_price") or price
    if st.get("order_state") not in ("filled",) and (target_amount - filled) > 0:
        dbt_cancel(oid)
        st2 = _extract_order(dbt_get_order_state(oid)) or st        # 撤单后再查，捕捉晚到成交
        if (st2.get("filled_amount") or 0.0) > filled:
            filled = st2.get("filled_amount")
            avg = st2.get("average_price") or avg
    return {"filled": filled, "avg_price": avg, "dry": False, "price": price,
            "taker": allow_taker, "reason": "EXIT_STEP"}


# ---------- 保护腿回收（§7.5；短腿归零后 maker 卖出；无 bid → LONG_RESIDUAL_ONLY）----------

def exec_protection_recovery_step(long_inst, qty, allow_live, label="recover_long"):
    """短腿归零后回收保护腿：被动 maker 卖出(post-only，join bid)；无 bid → LONG_RESIDUAL_ONLY(保持等结算)。
    allow_live=False → 仅意图(dry)。返回 {sold, price, state, dry, reason}。"""
    if not qty or qty <= 0:
        return {"sold": 0.0, "dry": (not allow_live), "state": "COMPLETE", "reason": "NO_LONG"}
    if not long_inst:
        return {"sold": 0.0, "dry": (not allow_live), "state": "LONG_RESIDUAL_ONLY",
                "reason": "NO_LONG_INSTRUMENT"}
    q = exec_quote(long_inst)
    bid = (q or {}).get("best_bid")
    if not q or bid in (None, 0) or bid <= 0:
        return {"sold": 0.0, "dry": (not allow_live), "state": "LONG_RESIDUAL_ONLY", "reason": "NO_BID"}
    price = bid                                    # 被动 maker 卖：join bid（不接受负净回收 → bid>0 已保证）
    if not allow_live:
        return {"sold": 0.0, "dry": True, "price": price, "state": "WORKING_LONG", "reason": "RECOVER_DRYRUN"}
    sold = _post_maker_once("sell", long_inst, qty, price, label)
    return {"sold": sold, "price": price, "dry": False,
            "state": ("COMPLETE" if sold >= qty - 1e-12 else "WORKING_LONG"), "reason": "RECOVER_STEP"}


# ---------- BTC-PERPETUAL 对冲下单（§10.4；REDUCE/UNWIND 强制 reduce_only）----------

def exec_hedge_step(venue_cfg, side, amount, reduce_only, allow_live, label="hedge"):
    """对冲一步（场所感知）。OPEN/INCREASE 非 reduce_only；REDUCE/UNWIND 强制 reduce_only。
    venue_cfg: hedge.hedge_venue_config 结果(含 venue/instrument/linear/maker_only)。
    BINANCE → binance_io(maker post-only/USDC 永续)；DERIBIT → BTC-PERPETUAL。allow_live=False → 仅意图(dry)。"""
    venue_cfg = venue_cfg or {}
    venue = venue_cfg.get("venue")
    instrument = venue_cfg.get("instrument")
    maker_only = bool(venue_cfg.get("maker_only"))
    if not side or not amount or amount <= 0:
        return {"filled": 0.0, "dry": (not allow_live), "venue": venue, "reason": "NO_OP"}
    if venue == "BINANCE":
        return bnc_place_hedge(instrument, side, amount, reduce_only, maker_only,
                               allow_live=allow_live, idx=venue_cfg.get("exchange_index"))
    # DERIBIT 反向永续
    if not allow_live:
        return {"filled": 0.0, "dry": True, "venue": venue, "instrument": instrument,
                "side": side, "amount": amount, "reduce_only": reduce_only, "reason": "HEDGE_DRYRUN"}
    q = exec_quote(instrument) or {}
    price = q.get("best_ask") if side == "buy" else q.get("best_bid")
    if price is None or price <= 0:                       # C1：无可成交盘口 → 不下单（防 price=None 误单）
        return {"filled": 0.0, "dry": False, "venue": venue, "reduce_only": reduce_only,
                "reason": "NO_QUOTE"}
    resp = dbt_place_order(side, instrument, amount, price, post_only=maker_only,
                           reject_post_only=False, label=label, reduce_only=reduce_only)
    order = _extract_order(resp)
    if order is None:
        return {"filled": 0.0, "dry": False, "venue": venue, "reduce_only": reduce_only,
                "reason": "HEDGE_ORDER_FAILED"}
    oid = order.get("order_id")
    Sleep(int(CHASE_WAIT_SECONDS * 1000))                 # C1：等一周期再查成交（原即查多为 0）
    st = _extract_order(dbt_get_order_state(oid)) or order
    filled = st.get("filled_amount") or 0.0
    if st.get("order_state") not in ("filled",) and (amount - filled) > 0:
        dbt_cancel(oid)                                  # 残单撤掉(不留挂)，撤后再查捕捉晚到成交
        st2 = _extract_order(dbt_get_order_state(oid)) or st
        if (st2.get("filled_amount") or 0.0) > filled:
            filled = st2.get("filled_amount")
    return {"filled": filled, "avg_price": st.get("average_price"), "dry": False, "venue": venue,
            "reduce_only": reduce_only, "reason": "HEDGE_STEP"}
