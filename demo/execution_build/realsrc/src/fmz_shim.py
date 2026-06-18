# -*- coding: utf-8 -*-
"""
本地 mock，模拟 FMZ 运行时注入的全局：exchange / Log / LogStatus / LogProfit / _G / GetCommand / Sleep。

仅用于本地单测与 bundle 前的逻辑验证；**不会被打包进 FMZ 交付文件**
（build_bundle.py 会剥离 `from fmz_shim import *`，由 FMZ 运行时提供真实实现）。
"""

__all__ = ["_G", "Log", "LogStatus", "LogProfit", "Sleep", "GetCommand", "exchange", "exchanges"]

_MISSING = object()
_STORE = {}


def _G(key=_MISSING, value=_MISSING):
    """模拟 FMZ 持久化全局：
    _G(k)        -> 取值（不存在返回 None）
    _G(k, v)     -> 存值并返回 v
    _G(k, None)  -> 删除该键
    """
    if key is _MISSING:
        return None
    if value is _MISSING:
        return _STORE.get(key)
    if value is None:
        _STORE.pop(key, None)
        return None
    _STORE[key] = value
    return value


def Log(*args):
    print("[Log]", *args)


_last_status = {"text": ""}


def LogStatus(*args):
    _last_status["text"] = " ".join(str(a) for a in args)


def LogProfit(profit, *args):
    print("[LogProfit]", profit)


def Sleep(ms):
    # 本地测试不真正 sleep
    return None


_commands = []


def GetCommand():
    return _commands.pop(0) if _commands else ""


class _MockExchange(object):
    """可注入响应的交易所 mock。测试可设置 self.io_handler = fn(*args)。"""

    def __init__(self):
        self.io_handler = None
        self.contract = None
        self.account = {}
        self.ticker = {}

    def IO(self, *args):
        if self.io_handler is not None:
            return self.io_handler(*args)
        return None

    def SetContractType(self, symbol):
        self.contract = symbol
        return {"symbol": symbol}

    def GetTicker(self):
        return dict(self.ticker)

    def GetAccount(self):
        return dict(self.account)

    # --- 期货/对冲所(币安)所需的最小桩 ---
    def GetPosition(self):
        return list(getattr(self, "positions", []) or [])

    def SetDirection(self, direction):
        self.direction = direction
        return direction

    def Buy(self, price, amount):
        return {"id": "mock-buy", "price": price, "amount": amount}

    def Sell(self, price, amount):
        return {"id": "mock-sell", "price": price, "amount": amount}


exchange = _MockExchange()
# FMZ 多所：exchanges[0]=主所(Deribit 期权)，exchanges[1]=备用(测试占位，可设对冲所)
exchanges = [exchange, _MockExchange()]
