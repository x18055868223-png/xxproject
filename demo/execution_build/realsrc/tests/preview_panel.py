# -*- coding: utf-8 -*-
"""开发辅助：用假 Deribit 驱动计划轮，预览方案库面板（ASCII 渲染 FMZ 表格）。非测试。"""
import os, sys, json, time
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import fmz_shim

H = 3600000
SPOT = 73416.0
S48 = {74000: (0.45, 0.016), 75000: (0.38, 0.012), 76000: (0.30, 0.008),
       77000: (0.22, 0.005), 78000: (0.15, 0.0035), 79000: (0.10, 0.0025)}
P168 = {76000: (0.34, 0.018), 77000: (0.28, 0.014), 78000: (0.22, 0.011),
        79000: (0.18, 0.009), 80000: (0.14, 0.007), 81000: (0.11, 0.0055)}
_T = int(time.time() * 1000)

try:
    from urllib.parse import parse_qs
except ImportError:
    from urlparse import parse_qs


def _instruments():
    out = []
    for k in S48:
        out.append({"instrument_name": "BTC-1JUN26-%d-C" % k, "strike": k, "option_type": "call",
                    "expiration_timestamp": _T + 48 * H, "kind": "option", "tick_size": 0.0001})
    for k in P168:
        out.append({"instrument_name": "BTC-5JUN26-%d-C" % k, "strike": k, "option_type": "call",
                    "expiration_timestamp": _T + 168 * H, "kind": "option", "tick_size": 0.0001})
    return out


def _handler(*a):
    _, _m, path, query = a
    qs = parse_qs(query or "")
    if path.endswith("/public/get_instruments"):
        return {"result": _instruments()}
    if path.endswith("/public/get_index_price"):
        return {"result": {"index_price": SPOT}}
    if path.endswith("/public/ticker"):
        p = qs.get("instrument_name", ["BTC-1JUN26-76000-C"])[0].split("-")
        d, m = (S48 if p[1] == "1JUN26" else P168)[int(p[2])]
        return {"result": {"mark_price": m, "best_bid_price": round(m * 0.97, 6),
                           "best_ask_price": round(m * 1.03, 6), "underlying_price": SPOT,
                           "greeks": {"delta": d}}}
    if path.endswith("/public/get_instrument"):
        return {"result": {"tick_size": 0.0001}}
    if path.endswith("/private/get_account_summary"):
        return {"result": {"margin_model": "segregated_pm", "portfolio_margining_enabled": True}}
    if path.endswith("/private/get_positions"):
        return {"result": []}
    if path.endswith("/private/simulate_portfolio"):
        sp = json.loads(qs.get("simulated_positions", ["{}"])[0])
        im = 0.025 if len(sp) == 1 else 0.013
        return {"result": {"initial_margin": im, "maintenance_margin": im * 0.8, "available_funds": 1.0}}
    return {"result": None}


fmz_shim.exchange.io_handler = _handler
import display as D
import strategy as ST
ST.SETTLEMENT_CURRENCY = "BTC"; ST.DIRECTION_BIAS = "SHORT_CALL"; ST.ROUND_MODE = "PLAN"
ST.SELECTED_PLAN = 1; ST.MENU_SIZE = 6; ST.SHORT_DELTA_RANGE = (0.15, 0.45)
ST.PROTECTION_WIDTH_RANGE = (2000, 2500); ST.PROTECTION_RESIDUAL_RECOVERY = 0.40
ST.SIGNAL_CONFIDENCE = 62; ST.SIGNAL_STATE = "TRADE_SUPPORT_WEAK"
ST.SHORT_DTE_HOURS = (24, 72); ST.PROT_DTE_DAYS = (5, 10); ST.ORDER_AMOUNT = 0.1
ST.MIN_MARGIN_RELIEF_RATIO = 0.10; ST.UNDERLYING_REF_PRICE = None; ST.ALLOW_TRADING = False
ST.PLAN_WEIGHTS = {"win_rate": 0.375, "rr": 0.375, "signal": 0.25}

# 先 peek 一个编号设为选用，演示 ★ 标记
ST.ENABLE_CALENDAR = True                  # 预览演示垂直+日历双模
_m, _, _, _, _ = ST._build_menu(ST._now_ms(), SPOT)
ST.SELECTED_PLAN = _m[0]["id"] if _m else 0
ctx = ST._plan_round(ST._spot_price())


def render(panel):
    header, _, block = panel.partition("\n")
    print("标题行:", header)
    for t in json.loads(block.strip().strip("`")):
        cols = t["cols"]
        w = [len(str(c)) for c in cols]
        for r in t["rows"]:
            for i, c in enumerate(r):
                w[i] = max(w[i], len(str(c)))
        print("\n== %s ==" % t["title"])
        print(" | ".join(str(c).ljust(w[i]) for i, c in enumerate(cols)))
        print("-+-".join("-" * x for x in w))
        for r in t["rows"]:
            print(" | ".join(str(c).ljust(w[i]) for i, c in enumerate(r)))


render(D.disp_status_panel(ctx, "计划轮·方案库"))
