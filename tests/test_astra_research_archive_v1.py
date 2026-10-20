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


def test_ledger_line_tamper_is_detected(tmp_path):
    archive = ar.Archive(tmp_path)
    _tick(archive, NOW, _view(), FakeDeribit())
    ledger = next(tmp_path.glob("ledger/*.jsonl"))
    ledger.write_text(ledger.read_text(encoding="utf-8").replace("STRUCTURE_BLOCKED", "STRUCTURE_ELIGIBLE"), encoding="utf-8")
    assert archive.verify()["problems"][0]["problem"] == "line_changed_or_missing"


def test_outcome_written_once_and_expired_candidate_not_reopened(tmp_path):
    archive = ar.Archive(tmp_path)
    _tick(archive, NOW, _view(), FakeDeribit())
    deribit = FakeDeribit(delivery=82000.0, path=[[NOW, 81900.0], [EXPIRY, 82000.0]])
    _tick(archive, EXPIRY + 15 * 60_000, _view(), deribit)   # the view still shows the expired contract
    _tick(archive, EXPIRY + 25 * 60_000, _view(), deribit)
    lines = next(tmp_path.glob("outcomes/*.jsonl")).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["payout_btc"] == 0.0
    assert archive.load_state()["contracts"] == {}


def test_outcome_gives_up_after_twelve_hours_without_delivery(tmp_path):
    archive = ar.Archive(tmp_path)
    _tick(archive, NOW, _view(), FakeDeribit())
    assert _tick(archive, EXPIRY + H, None, FakeDeribit())["outcomes"] == 0
    _tick(archive, EXPIRY + 13 * H, None, FakeDeribit())
    out = json.loads(next(tmp_path.glob("outcomes/*.jsonl")).read_text(encoding="utf-8"))
    assert out["status"] == "outcome_unavailable" and out["reason"] == "LookupError"


def test_view_failure_and_flip_moves_do_not_trigger_rows(tmp_path):
    archive = ar.Archive(tmp_path)
    _tick(archive, NOW, _view(), FakeDeribit())
    assert _tick(archive, NOW + 10 * 60_000, None, FakeDeribit())["rows"] == 0        # view unavailable
    moved = _view()
    moved["market_context"]["market_facts"]["space_context"]["gex_info"]["flip_point"] = 83000
    assert _tick(archive, NOW + 20 * 60_000, moved, FakeDeribit())["rows"] == 0       # flip alone
    moved["market_context"]["market_facts"]["gamma_regime"]["regime"] = "NEGATIVE"
    assert _tick(archive, NOW + 30 * 60_000 - 1, moved, FakeDeribit())["rows"] == 1   # regime change


def test_call_path_and_time_weighted_volatility():
    record = {"first_seen_ms": 0, "expiry_ms": 120_000, "short_strike_usd": 84000.0, "long_strike_usd": 86000.0,
              "side": "call"}
    stats = ar.path_stats(record, [[0, 83000.0], [60_000, 84500.0], [120_000, 83800.0]], 83800.0)
    assert stats["touched_short"] and not stats["touched_long"] and stats["ms_beyond_short"] == 60_000
    assert stats["settle_vs_short_pct"] == pytest.approx((83800 - 84000) / 84000 * 100)
    assert stats["path_complete"] is True and stats["realized_vol_annual"] > 0


def _lock(status="active", stopped=None):
    return {"episode_id": "lce_" + "1" * 24, "status": status, "created_at_ms": NOW - H, "stopped_at_ms": stopped,
            "candidate_id": "c", "contract": {"side": "put", "short_instrument": "BTC-20OCT26-81500-P",
                                              "long_instrument": "BTC-20OCT26-79500-P"}}


def test_decisions_locked_flag_and_index(tmp_path):
    archive = ar.Archive(tmp_path)
    dp = [{"verification_id": "dpv_1", "event": "concluded", "at_ms": NOW - 10, "conclusion": "lock",
           "contract": {"side": "put", "short_instrument": "BTC-20OCT26-81500-P", "long_instrument": "BTC-20OCT26-79500-P"}}]
    source = [([_lock()], dp)]
    r1 = ar.tick(archive, now_ms=NOW, view=_view(), fetch_case=_case, deribit=ar.Deribit(FakeDeribit()),
                 decision_source=lambda: source[0])
    assert r1["decisions"] == 2
    row = json.loads(next(tmp_path.glob("ledger/*.jsonl")).read_text(encoding="utf-8").splitlines()[0])
    assert row["locked"] is True
    # the same events are never appended twice; a stop is a new event and a change row
    source[0] = ([_lock("stopped", NOW + 5 * 60_000)], dp)
    r2 = ar.tick(archive, now_ms=NOW + 10 * 60_000, view=_view(), fetch_case=_case,
                 deribit=ar.Deribit(FakeDeribit()), decision_source=lambda: source[0])
    assert r2["decisions"] == 1 and r2["rows"] == 1
    events = [json.loads(l) for f in tmp_path.glob("decisions/*.jsonl") for l in f.read_text(encoding="utf-8").splitlines()]
    assert sorted(e["event"] for e in events) == ["concluded", "lock_created", "lock_stopped"]
    index = json.loads((tmp_path / "index/episodes/2026-10-20.json").read_text(encoding="utf-8"))
    ep = index["episodes"][0]
    assert ep["status"] == "open" and ep["ledger_rows"] == 2 and ep["locked"] is False and len(ep["decisions"]) == 3
    assert ep["cases"][0]["case_id"].startswith("cf_") and index["derived"] is True
    assert archive.verify()["ok"] is True


def test_protocol_versions_recorded_once_and_on_change(tmp_path, monkeypatch):
    archive = ar.Archive(tmp_path)
    _tick(archive, NOW, None, FakeDeribit())
    _tick(archive, NOW + 600_000, None, FakeDeribit())
    lines = (tmp_path / "protocols/versions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(ar.observed_versions())
    monkeypatch.setattr(ar.inquiry, "PROMPT_VERSION", "inquiry_prompt@0.2")
    _tick(archive, NOW + 1_200_000, None, FakeDeribit())
    last = json.loads((tmp_path / "protocols/versions.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert last["component"] == "inquirer_prompt" and last["previous"] == "inquiry_prompt@0.1"


def test_episode_view_only_before_expiry(tmp_path):
    archive = ar.Archive(tmp_path / "a")
    box, receipts = tmp_path / "inbox", tmp_path / "r"
    box.mkdir()
    view = {"schema": "episode_view@0.1", "id": "ev-1", "agent": {"name": "GrokBot", "model": "GrokBot"},
            "contract_key": "BTC-20OCT26-81500-P|BTC-20OCT26-79500-P",
            "view": {"risk_vs_baseline": "riskier", "grade": "D", "lean": "watch", "confidence": "medium",
                     "drivers_cn": ["x"], "summary_cn": "y"}}
    (box / "ev-1.json").write_text(json.dumps(view), encoding="utf-8")
    report = ar.tick(archive, now_ms=NOW, view=_view(), fetch_case=_case, deribit=ar.Deribit(FakeDeribit()),
                     inbox=(box, receipts))
    assert report["inbox"] == {"accepted": 1, "rejected": 0}
    (box / "ev-2.json").write_text(json.dumps({**view, "id": "ev-2"}), encoding="utf-8")
    deribit = FakeDeribit(delivery=82000.0, path=[[NOW, 81900.0], [EXPIRY, 82000.0]])
    report = ar.tick(archive, now_ms=EXPIRY + 15 * 60_000, view=None, fetch_case=_case, deribit=ar.Deribit(deribit),
                     inbox=(box, receipts))
    assert report["inbox"] == {"accepted": 0, "rejected": 1}


def test_backfill_legacy_once(tmp_path):
    wb = tmp_path / "wb"
    (wb / "locked_candidates" / "points").mkdir(parents=True)
    (wb / "locked_candidates" / "index.json").write_text("{}", encoding="utf-8")
    (wb / "locked_candidates" / "points" / "p1.json.gz").write_bytes(gzip.compress(b"{}"))
    (wb / "daily_snapshots.jsonl").write_text("{}\n", encoding="utf-8")
    (wb / "daily_snapshots.jsonl.lock").write_text("", encoding="utf-8")
    archive = ar.Archive(tmp_path / "a")
    assert ar.backfill_legacy(archive, wb, now_ms=1)["legacy_files"] == 4
    assert ar.backfill_legacy(archive, wb, now_ms=2) == {"skipped": "legacy_exists"}
    assert (tmp_path / "a/legacy/daily_snapshots/daily_snapshots.jsonl.gz").exists()
    assert archive.verify()["ok"] is True


def test_validation_runs_once_per_day_after_0930_utc(tmp_path, monkeypatch):
    archive, calls = ar.Archive(tmp_path), []
    monkeypatch.setattr(ar.research_validation, "run", lambda a, now_ms: calls.append(now_ms) or {"evaluated": 0})
    day0 = EXPIRY + H                                       # 2026-10-20 09:00 UTC
    _tick(archive, day0, None, FakeDeribit())               # before 09:30
    _tick(archive, day0 + 40 * 60_000, None, FakeDeribit())  # 09:40: runs
    _tick(archive, day0 + 50 * 60_000, None, FakeDeribit())  # same day: not again
    assert len(calls) == 1


def test_empty_index_path_is_retried_not_written(tmp_path):
    archive = ar.Archive(tmp_path)
    _tick(archive, NOW, _view(), FakeDeribit())
    assert _tick(archive, EXPIRY + 15 * 60_000, None, FakeDeribit(delivery=82000.0, path=[]))["outcomes"] == 0
    assert not list(tmp_path.glob("paths/*")) and archive.load_state()["contracts"]
