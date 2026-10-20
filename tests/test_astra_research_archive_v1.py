import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest

import astra_research_archive_v1 as ar

H = 3_600_000
EXPIRY = 1_792_483_200_000          # 2026-10-20 08:00 UTC
NOW = EXPIRY - 18 * H               # 2026-10-19 14:00 UTC, an inquiry hour


def _view(short="BTC-20OCT26-81500-P", long="BTC-20OCT26-79500-P", sid="a" * 64):
    contract = {"side": "put", "short_instrument": short, "long_instrument": long,
                "short_strike_usd": float(short.split("-")[2]), "long_strike_usd": float(long.split("-")[2]),
                "expiry_ms": EXPIRY}
    return {"candidates": [{"side": "put", "snapshot": {"snapshot_id": sid, "contract": contract}}],
            "space_gate": {"producer_state": "STRUCTURE_BLOCKED"},
            "market_context": {"market_facts": {"m_die": {"level": "NO_DIRECTIONAL_MOVE"},
                                                "gamma_regime": {"regime": "TRANSITION"},
                                                "space_context": {"gex_info": {"flip_point": 82400, "market_state": "positive_gamma"}}}}}


def _case(sid):
    return {"case_id": "cf_" + sid[:24], "asof_ms": NOW, "snapshot_id": sid,
            "layers": {"L1": [{"ref": "L1.economics", "values": {"net_credit_after_fee_btc": 0.0015}}],
                       "L9": [{"ref": "L9.history", "values": []}]},
            "refs": {"L1.economics": {"layer": "L1", "index": 0}, "L9.history": {"layer": "L9", "index": 0}}}


class FakeDeribit:
    def __init__(self, index=81900.0, delivery=None, path=None):
        self.index, self.delivery_price, self.path, self.calls = index, delivery, path or [], []

    def __call__(self, path, params):
        self.calls.append(path)
        if path == "public/get_order_book":
            name = params["instrument_name"]
            short = name.endswith("81500-P")
            return {"instrument_name": name, "index_price": self.index, "underlying_price": self.index - 20,
                    "best_bid_price": 0.0019 if short else 0.0002, "best_ask_price": 0.0023 if short else 0.0003,
                    "mark_price": 0.0021 if short else 0.0002, "mark_iv": 25.7 if short else 40.2,
                    "greeks": {"delta": -0.31 if short else -0.04}, "timestamp": 1}
        if path == "public/get_delivery_prices":
            return {"data": [{"date": "2026-10-20", "delivery_price": self.delivery_price}] if self.delivery_price else []}
        if path == "public/get_index_chart_data":
            return self.path
        raise AssertionError(path)


def _tick(archive, now, view, deribit, inquiry=None):
    return ar.tick(archive, now_ms=now, view=view, fetch_case=_case, deribit=ar.Deribit(deribit), run_inquiry=inquiry)


def test_case_once_per_hour_per_side_and_inquiry_without_l9(tmp_path):
    archive, deribit, seen = ar.Archive(tmp_path), FakeDeribit(), []
    def inquiry(case):
        seen.append(case)
        return {"status": "OK", "sheet": {}}
    r1 = _tick(archive, NOW + 60_000, _view(), deribit, inquiry)
    r2 = _tick(archive, NOW + 11 * 60_000, _view(), deribit, inquiry)
    assert (r1["cases"], r1["inquiry"], r2["cases"], r2["inquiry"]) == (1, 1, 0, 0)
    assert "L9" not in seen[0]["layers"]
    files = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert any(f.startswith("cases/2026-10-19/14_put_cf_") and f.endswith(".json.gz") for f in files)
    assert any(f.startswith("inquiry/2026-10-19/14_put_") for f in files)
    case_file = next(tmp_path.glob("cases/*/*.json.gz"))
    assert json.loads(gzip.decompress(case_file.read_bytes()))["case_id"] == "cf_" + "a" * 24


def test_inquiry_failure_is_recorded_not_retried(tmp_path):
    archive, deribit, calls = ar.Archive(tmp_path), FakeDeribit(), []
    def inquiry(case):
        calls.append(1)
        raise TimeoutError("slow")
    _tick(archive, NOW + 60_000, _view(), deribit, inquiry)
    _tick(archive, NOW + 70 * 60_000 - H, _view(), deribit, inquiry)
    assert calls == [1]
    rec = json.loads(next(tmp_path.glob("inquiry/*/*.json")).read_text(encoding="utf-8"))
    assert rec["status"] == "ERROR" and rec["error"] == "TimeoutError"


def test_ledger_base_change_and_near_expiry_triggers(tmp_path):
    archive = ar.Archive(tmp_path)
    _tick(archive, NOW, _view(), FakeDeribit())                       # first
    assert _tick(archive, NOW + 10 * 60_000, _view(), FakeDeribit())["rows"] == 0   # nothing changed
    assert _tick(archive, NOW + 20 * 60_000, _view(), FakeDeribit(index=81400.0))["rows"] == 1  # crossed short
    assert _tick(archive, NOW + 50 * 60_000, _view(), FakeDeribit(index=81400.0))["rows"] == 1  # 30 min base
    # the contract stays tracked after it stops being displayed, and near expiry every tick writes
    other = _view("BTC-20OCT26-82000-P", "BTC-20OCT26-80000-P", "b" * 64)
    near = EXPIRY - 2 * H
    assert _tick(archive, near, other, FakeDeribit(index=81400.0))["rows"] == 2
    assert _tick(archive, near + 10 * 60_000, other, FakeDeribit(index=81400.0))["rows"] == 2
    rows = [json.loads(l) for f in sorted(tmp_path.glob("ledger/*.jsonl")) for l in f.read_text(encoding="utf-8").splitlines()]
    triggers = [(r["contract_key"].split("-")[2], r["trigger"]) for r in rows]
    assert triggers[:4] == [("81500", "first"), ("81500", "change"), ("81500", "base"), ("81500", "near_expiry")]
    first = rows[0]
    assert first["schema"] == "ledger_row@0.1" and first["displayed"] is True
    assert first["credit_bid_ask_btc"] == pytest.approx(0.0019 - 0.0003)
    assert first["states"]["m_die_level"] == "NO_DIRECTIONAL_MOVE" and first["p_itm_short"] is not None
    assert rows[-1]["displayed"] in {True, False}


def test_outcome_after_expiry_with_path_and_case_results(tmp_path):
    archive = ar.Archive(tmp_path)
    _tick(archive, NOW, _view(), FakeDeribit())
    minutes = [[NOW + i * 60_000, 81900.0 - i * 2.0] for i in range(0, 18 * 60 + 1)]   # drifts to ~79740
    deribit = FakeDeribit(delivery=81000.0, path=minutes)
    assert _tick(archive, EXPIRY + 5 * 60_000, None, deribit)["outcomes"] == 0          # waits 10 minutes
    report = _tick(archive, EXPIRY + 15 * 60_000, None, deribit)
    assert report["outcomes"] == 1
    out = json.loads(next(tmp_path.glob("outcomes/2026-10-20.jsonl")).read_text(encoding="utf-8").splitlines()[0])
    assert out["payout_btc"] == pytest.approx((81500 - 81000) / 81000)
    assert out["cases"][0]["result_btc"] == pytest.approx(0.0015 - (81500 - 81000) / 81000)
    assert out["path"]["touched_short"] is True and out["path"]["touched_long"] is False
    assert out["path"]["settle_vs_short_pct"] == pytest.approx((81500 - 81000) / 81500 * 100)
    assert archive.load_state()["contracts"] == {}


def test_chain_links_and_detects_a_changed_case(tmp_path):
    archive = ar.Archive(tmp_path)
    _tick(archive, NOW + 60_000, _view(), FakeDeribit())
    assert archive.verify()["ok"] is True
    case_file = next(tmp_path.glob("cases/*/*.json.gz"))
    case_file.write_bytes(b"tampered")
    report = archive.verify()
    assert report["ok"] is False and report["problems"][0]["problem"] == "content_changed_or_missing"
    with pytest.raises(FileExistsError):
        archive.write_new(case_file.relative_to(tmp_path).as_posix(), b"x", at_ms=0, kind="case")


def test_only_audited_public_endpoints():
    with pytest.raises(ValueError):
        ar.Deribit._get("private/get_positions", {})
