# -*- coding: utf-8 -*-
"""SyntheticFillAdapter：本地空跑用的「合成成交」适配器。

**只替换订单成交回报，不接触真实私有写接口**——用真实的 exec_* / dbt_place_order 主链跑
entry campaign / 退出 / 对冲，但把 /private/buy|sell|get_order_state|cancel 的回报换成可配置的
合成成交模型，从而在纯空跑下覆盖 §11.1 场景矩阵（保护全成短腿部分成、晚到成交、放弃部分冻结等）。

用法（测试/runbook）：
    fmz_shim.exchange.io_handler = make_synthetic_handler(base_handler, fill_model)
其中 base_handler 处理公开行情/合约/账户（如 test_run_cycle._handler）；
fill_model(side, instrument, amount, price) -> filled_amount（≤amount）。
"""
try:
    from urllib.parse import parse_qs
except ImportError:                      # pragma: no cover  (py2 / FMZ)
    from urlparse import parse_qs


def constant_ratio_model(buy_ratio=1.0, sell_ratio=1.0):
    """最常用模型：保护腿(buy)按 buy_ratio 成交、短腿(sell)按 sell_ratio 成交。"""
    def model(side, instrument, amount, price):
        r = buy_ratio if side == "buy" else sell_ratio
        return amount * r
    return model


def make_synthetic_handler(base_handler, fill_model):
    """返回一个 io_handler：拦截私有交易端点给合成成交、并按成交累计净持仓供 get_positions(期权)，
    其余转 base_handler。fill_model 抛错或返回非数 → 视为 0 成交（保守）。"""
    state = {"orders": {}, "seq": 0, "pos": {}}      # pos: instrument -> 净持仓(买+卖-)

    def _q(qs, name, default=None):
        v = qs.get(name)
        return v[0] if v else default

    def handler(*args):
        _, _method, path, query = args
        qs = parse_qs(query or "")
        is_buy, is_sell = path.endswith("/private/buy"), path.endswith("/private/sell")
        if is_buy or is_sell:
            side = "buy" if is_buy else "sell"
            inst = _q(qs, "instrument_name")
            amount = float(_q(qs, "amount", "0") or 0.0)
            price = float(_q(qs, "price", "0") or 0.0)
            try:
                filled = float(fill_model(side, inst, amount, price))
            except Exception:
                filled = 0.0
            filled = max(0.0, min(amount, filled))
            state["pos"][inst] = state["pos"].get(inst, 0.0) + (filled if is_buy else -filled)
            state["seq"] += 1
            oid = "syn-%d" % state["seq"]
            order = {"order_id": oid, "instrument_name": inst, "label": _q(qs, "label"),
                     "order_state": "filled" if filled >= amount - 1e-12 else "open",
                     "filled_amount": filled, "average_price": price}
            state["orders"][oid] = order
            return {"result": {"order": order}}
        if path.endswith("/private/get_order_state"):
            return {"result": state["orders"].get(_q(qs, "order_id"))}
        if path.endswith("/private/cancel"):
            o = state["orders"].get(_q(qs, "order_id"))
            if o and o["order_state"] != "filled":
                o["order_state"] = "cancelled"
            return {"result": o}
        if path.endswith("/private/get_positions"):
            kind = _q(qs, "kind", "option")
            if kind == "option":                      # 合成净期权持仓（供 T0-A 放弃以交易所为准）
                return {"result": [{"instrument_name": k, "size": v}
                                   for k, v in state["pos"].items() if abs(v) > 1e-12]}
            return {"result": []}
        return base_handler(*args)

    handler._synthetic_state = state      # 便于测试断言落单/持仓
    return handler
