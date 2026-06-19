# -*- coding: utf-8 -*-
"""
Deribit IO 适配层（dbt_*）。

统一经 FMZ 的 exchange.IO("api", method, path, query) 调 Deribit 原生 REST。
FMZ 已配置好 Deribit 交易所对象与 API key，私有请求由 FMZ 自动签名，本层不处理鉴权。
若个别端点连 IO 也不通，可在此层改为手动签名 REST 兜底（v1 默认走 IO）。

返回值统一抽取 Deribit JSON-RPC 的 result 字段；error 时 Log 并返回 None。
"""

import json

try:
    from urllib.parse import urlencode  # py3
except ImportError:  # pragma: no cover
    from urllib import urlencode        # py2 (FMZ 部分环境)

from fmz_shim import exchange, Log  # bundle 时剥离；FMZ 运行时由平台注入

DERIBIT_API_PREFIX = "/api/v2"


def _build_query(params):
    """dict -> querystring，对 JSON 值（如 simulated_positions）安全编码。"""
    flat = {}
    for k, v in params.items():
        if v is None:
            continue
        if isinstance(v, bool):
            flat[k] = "true" if v else "false"
        elif isinstance(v, (dict, list)):
            flat[k] = json.dumps(v, separators=(",", ":"))
        else:
            flat[k] = v
    return urlencode(flat)


def _result(resp, ctx=""):
    """从 IO 返回值中抽取 result；兼容 FMZ 已解包/未解包两种形态。"""
    if resp is None:
        Log("[dbt] IO 返回 None:", ctx)
        return None
    if isinstance(resp, dict):
        if "error" in resp and resp["error"]:
            Log("[dbt] Deribit error:", ctx, json.dumps(resp["error"]))
            return None
        if "result" in resp:
            return resp["result"]
    return resp


def _call(method, path, params=None, ctx="", retries=0):
    """retries>0 时对返回 None（网络/瞬时错误）做有限重试；写操作请用 retries=0。"""
    query = _build_query(params or {})
    attempt = 0
    while True:
        resp = exchange.IO("api", method, DERIBIT_API_PREFIX + path, query)
        out = _result(resp, ctx or path)
        if out is not None or attempt >= retries:
            return out
        attempt += 1


_READ_RETRIES = 2


# ---------- 公开行情 / 合约 ----------

def dbt_get_instruments(currency, kind="option", expired=False):
    return _call("GET", "/public/get_instruments",
                 {"currency": currency, "kind": kind, "expired": expired},
                 "get_instruments", _READ_RETRIES) or []


def dbt_get_instrument(instrument_name):
    """单合约元数据（含 tick_size / tick_size_steps / contract_size / min_trade_amount）。"""
    return _call("GET", "/public/get_instrument",
                 {"instrument_name": instrument_name}, "get_instrument", _READ_RETRIES)


def dbt_ticker(instrument_name):
    """含 best_bid_price / best_ask_price / mark_price / underlying_price / greeks。"""
    return _call("GET", "/public/ticker",
                 {"instrument_name": instrument_name}, "ticker", _READ_RETRIES)


def dbt_order_book(instrument_name, depth=1):
    return _call("GET", "/public/get_order_book",
                 {"instrument_name": instrument_name, "depth": depth}, "order_book", _READ_RETRIES)


def dbt_index_price(currency):
    """结算币对 USD 指数价（费用 USD 展示用）。"""
    index_name = (currency + "_usd").lower()
    r = _call("GET", "/public/get_index_price", {"index_name": index_name},
              "index_price", _READ_RETRIES)
    return (r or {}).get("index_price")


# ---------- 私有账户 / 持仓 ----------

def dbt_account_summary(currency, extended=True):
    """含 margin_model / portfolio_margining_enabled / initial_margin / maintenance_margin。"""
    return _call("GET", "/private/get_account_summary",
                 {"currency": currency, "extended": extended}, "account_summary", _READ_RETRIES)


def dbt_get_positions(currency, kind="option"):
    return _call("GET", "/private/get_positions",
                 {"currency": currency, "kind": kind}, "get_positions", _READ_RETRIES) or []


def dbt_simulate_portfolio(currency, simulated_positions, add_positions=True):
    """S:PM 模拟。simulated_positions: {instrument_name: size}（负数=short）。
    返回含 initial_margin / maintenance_margin / available_funds / margin_model。"""
    return _call("GET", "/private/simulate_portfolio",
                 {"currency": currency,
                  "add_positions": add_positions,
                  "simulated_positions": simulated_positions},
                 "simulate_portfolio", _READ_RETRIES)


# ---------- 私有交易 ----------

def dbt_place_order(side, instrument_name, amount, price,
                    post_only=True, reject_post_only=True, label=None, reduce_only=False):
    """限价单。side: 'buy'/'sell'。reduce_only=True 用于对冲减仓/平仓（不可建仓）。
    返回 result（含 order 字段）或 None。"""
    path = "/private/buy" if side == "buy" else "/private/sell"
    params = {
        "instrument_name": instrument_name,
        "amount": amount,
        "type": "limit",
        "price": price,
        "post_only": post_only,
        "reject_post_only": reject_post_only,
    }
    if reduce_only:
        params["reduce_only"] = True
    if label:
        params["label"] = label
    return _call("GET", path, params, "place_order:" + side)


def dbt_get_open_orders(currency, kind=None):
    """当前未成交挂单（按币种；kind 可选 option/future）。
    供预提交 no_unknown_orders 与启动恢复的「未知活动订单」检测。
    **读失败返回 None**（区别于"确实无挂单"的 []），便于调用方对查询失败 fail-closed。"""
    params = {"currency": currency}
    if kind:
        params["kind"] = kind
    return _call("GET", "/private/get_open_orders_by_currency", params,
                 "open_orders", _READ_RETRIES)


def dbt_get_order_state(order_id):
    return _call("GET", "/private/get_order_state", {"order_id": order_id}, "order_state")


def dbt_cancel(order_id):
    return _call("GET", "/private/cancel", {"order_id": order_id}, "cancel")
