# -*- coding: utf-8 -*-
"""币安 USDC 永续对冲适配（bnc_*）：经 FMZ exchanges[idx] 下对冲腿。

线性合约(单位 BTC)、USDC maker 0 费 → maker(post-only)；reduce_only 用平仓方向。
仅对冲腿用，不参与期权 / 信号。FMZ 多所：exchanges[0]=Deribit(期权)，exchanges[idx]=Binance。

注：真实下单调用形态依 FMZ 币安期货接口（SetContractType/SetDirection/Buy/Sell），**须真实机器人确认**；
默认 `ALLOW_HEDGE_TRADING=False` + `HEDGE_VENUE=DERIBIT`，本路径不触发。跨所对账/恢复取舍见 v3.1 文档。
"""
from config import HEDGE_BINANCE_EXCHANGE_INDEX
from fmz_shim import exchanges, Log


def _ex(idx):
    try:
        return exchanges[HEDGE_BINANCE_EXCHANGE_INDEX if idx is None else idx]
    except Exception:
        return None


def bnc_get_position_btc(symbol, idx=None):
    """读 BTCUSDC 永续净持仓(BTC；正=多 / 负=空)。读不到 → 0.0。"""
    ex = _ex(idx)
    if ex is None:
        return 0.0
    try:
        ex.SetContractType(symbol)
        net = 0.0
        for p in (ex.GetPosition() or []):
            amt = p.get("Amount") or 0.0
            long_side = p.get("Type") in (0, "buy", "long", "Long")
            net += amt if long_side else -amt
        return net
    except Exception as e:
        Log("[binance] GetPosition 异常:", str(e))
        return 0.0


def bnc_place_hedge(symbol, side, amount, reduce_only, maker_only, allow_live=True, idx=None):
    """下币安对冲腿。maker_only→post-only 限价(取同侧盘口被动价)；reduce_only→平仓方向。
    allow_live=False → 仅意图(dry)，不下单。"""
    if not side or not amount or amount <= 0:
        return {"filled": 0.0, "dry": (not allow_live), "venue": "BINANCE", "reason": "NO_OP"}
    if not allow_live:
        return {"filled": 0.0, "dry": True, "venue": "BINANCE", "symbol": symbol, "side": side,
                "amount": amount, "reduce_only": reduce_only, "maker_only": maker_only,
                "reason": "BINANCE_HEDGE_DRYRUN"}
    ex = _ex(idx)
    if ex is None:
        return {"filled": 0.0, "dry": False, "venue": "BINANCE", "reason": "BINANCE_EXCHANGE_UNAVAILABLE"}
    try:
        ex.SetContractType(symbol)
        t = ex.GetTicker() or {}
        price = t.get("Buy") if side == "buy" else t.get("Sell")   # 被动 maker 价：join 同侧盘口
        direction = ("closesell" if side == "buy" else "closebuy") if reduce_only else side
        ex.SetDirection(direction)
        resp = ex.Buy(price, amount) if side == "buy" else ex.Sell(price, amount)
        return {"filled": 0.0, "dry": False, "venue": "BINANCE", "order": resp,
                "reduce_only": reduce_only, "maker_only": maker_only,
                "reason": "BINANCE_HEDGE_SUBMITTED"}
    except Exception as e:
        Log("[binance] 下单异常:", str(e))
        return {"filled": 0.0, "dry": False, "venue": "BINANCE", "reason": "BINANCE_ORDER_ERROR"}
