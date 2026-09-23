import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from astra_joint_data import MINUTE_MS
from astra_joint_v11_cards import build_assessment, range_support


BASE_MS = int(datetime(2026, 9, 15, 1, 30, tzinfo=timezone.utc).timestamp() * 1000)


def kline(index: int, price: float = 80_000.0) -> dict[str, object]:
    open_ms = BASE_MS + index * MINUTE_MS
    close = price + index + 1.0
    return {
        "open_time_ms": open_ms,
        "open": price + index,
        "high": close + 10.0,
        "low": price + index - 10.0,
        "close": close,
        "volume": 10.0,
        "close_time_ms": open_ms + MINUTE_MS - 1,
        "quote_volume": 10.0 * close,
        "taker_buy_base_volume": 6.0,
    }


class LedgerStub:
    def __init__(self, spot_rows, um_rows):
        self._rows = {"spot": spot_rows, "um": um_rows}

    def bars(self, market):
        return list(self._rows[market])


def test_support_is_a_warning_not_a_probability_or_permission():
    model={'feature_support':{'features':[{'name':'ret_15','min':-.1,'max':.1,'missing_count':0}]}}
    assert range_support({'ret_15':.2},model)['out_of_range_features']==['ret_15']
    assert range_support({'ret_15':None},model)['unseen_missing_features']==['ret_15']
    inside=range_support({'ret_15':0},model)
    assert inside['range_status']=='within_marginal_observed_support'
    assert 'qualified' not in inside and 'probability' not in inside


def test_natural_card_adapter_uses_shared_complete_minute_gate():
    asof = BASE_MS + 30 * MINUTE_MS
    spot = [kline(i) for i in range(30)]
    um = [kline(i) for i in range(30)]
    um[-1]["close_time_ms"] = int(um[-1]["open_time_ms"]) + MINUTE_MS
    ledger = LedgerStub(spot, um)
    card = {
        "identity": {
            "card_id": "unit-natural-card",
            "confirmed_time_ms": asof,
            "symbol": "BTC",
            "source_record_hash": "sha256:unit",
        }
    }
    artifact = {"training_cutoff": "2026-08-31", "model_id": "unit-model"}

    assessment = build_assessment(card, ledger, artifact, contracts=[])

    assert assessment["status"] == "insufficient"
    assert "近端合约分钟不完整" in assessment["reason_cn"]
    assert assessment["market_reference"]["price"] > 0
