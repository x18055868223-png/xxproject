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
    current = ar.inquiry.PROMPT_VERSION
    monkeypatch.setattr(ar.inquiry, "PROMPT_VERSION", "inquiry_prompt@9.9")
    _tick(archive, NOW + 1_200_000, None, FakeDeribit())
    last = json.loads((tmp_path / "protocols/versions.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert last["component"] == "inquirer_prompt" and last["previous"] == current and last["version"] == "inquiry_prompt@9.9"


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


def _player_eligible_case(cid, side, built, expiry):
    entries = [
        ("L1", "L1.contract", {"side": side, "short_instrument": f"BTC-20OCT26-81500-{side[0].upper()}",
                               "long_instrument": f"BTC-20OCT26-79500-{side[0].upper()}",
                               "expiry_ms": expiry, "quantity_btc": 1.0}),
        ("L2", "L2.quote", {"short_bid": 0.0061, "long_ask": 0.0028}),
        ("L3", "L3.expiry_walls", {"pin": 80500.0}),
        ("L6", "L6.session_clock", {"remaining_variance_share": 0.62}),
    ]
    layers, refs = {}, {}
    for layer, ref, values in entries:
        layers.setdefault(layer, []).append({"ref": ref, "label_cn": ref, "values": values})
        refs[ref] = {"layer": layer, "index": len(layers[layer]) - 1}
    return {
        "case_id": cid, "asof_ms": built - 10_000, "build": {"built_at_ms": built},
        "layers": layers, "refs": refs,
        "selection": {"schema": "selection@0.1", "path_version": "dp-test", "eligible": True, "vetoes": [],
                      "round": {"current": 2}},
    }


def _freeze_player_case(root, state, case, hour_key, side):
    rel = f"cases/{hour_key[:10]}/{hour_key[11:]}_{side}_{case['case_id']}.json.gz"
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(gzip.compress(json.dumps(case).encode("utf-8")))
    state.setdefault("cases", {})[f"{hour_key}|{side}"] = {"case_id": case["case_id"], "path": rel}


def test_run_players_stale_running_ttl_and_corrupt_gz_isolation(tmp_path):
    M = 60_000
    archive = ar.Archive(tmp_path)
    state = archive.load_state()
    hour_key = ar._utc(NOW).strftime("%Y-%m-%dT%H")
    # Corrupt put case alongside valid call case
    corrupt_rel = f"cases/{hour_key[:10]}/{hour_key[11:]}_put_corrupt.json.gz"
    (tmp_path / corrupt_rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / corrupt_rel).write_bytes(b"not-a-valid-gzip-archive")
    state.setdefault("cases", {})[f"{hour_key}|put"] = {"case_id": "cf_bad", "path": corrupt_rel}
    _freeze_player_case(tmp_path, state, _player_eligible_case("cf_call_ok", "call", NOW + 10_000, EXPIRY), hour_key, "call")

    # Seed active running marker (within TTL) -> blocked; then advance past STALE_PLAYER_RUNNING_MS -> recovered
    state["player"] = {f"{hour_key}|call": {"status": "running", "started_at_ms": NOW}}
    seen = []

    def ok_player(case, rnd, prev):
        seen.append((case["case_id"], rnd, prev))
        return {"status": "OK", "decision": {"action": "reject", "reject_reason": "pricing"},
                "rulebook": "rb", "prompt_version": "pv", "requested_model": "m"}

    report = {"errors": []}
    assert ar.run_players(archive, state, now_ms=NOW + 10 * M, run_player=ok_player, report=report) == 0
    assert seen == []
    assert state["player"][f"{hour_key}|put"] == "corrupt_case"
    assert any(e["step"] == "player_case_read" for e in report["errors"])

    assert ar.run_players(archive, state, now_ms=NOW + 31 * M, run_player=ok_player, report=report) == 1
    assert seen == [("cf_call_ok", "r2", None)]
    assert state["player"][f"{hour_key}|call"] == "OK"

    # Legacy string "running" falls back to state["last_tick_ms"]
    state["player"][f"{hour_key}|call"] = "running"
    state["last_tick_ms"] = NOW + 20 * M
    assert ar.run_players(archive, state, now_ms=NOW + 32 * M, run_player=ok_player, report=report) == 0
    state["last_tick_ms"] = NOW
    assert ar.run_players(archive, state, now_ms=NOW + 33 * M, run_player=ok_player, report=report) == 0
    # Existing decision row with matching case_id is adopted without re-calling run_player!
    assert len(seen) == 1 and state["player"][f"{hour_key}|call"] == "OK"

    # Legacy string "running" WITHOUT prior decision row recovers and executes run_player after TTL
    arch2 = ar.Archive(tmp_path / "legacy_stale")
    st2 = arch2.load_state()
    _freeze_player_case(tmp_path / "legacy_stale", st2, _player_eligible_case("cf_leg", "put", NOW + 10_000, EXPIRY), hour_key, "put")
    st2["player"] = {f"{hour_key}|put": "running"}
    st2["last_tick_ms"] = NOW
    leg_seen = []
    assert ar.run_players(arch2, st2, now_ms=NOW + 31 * M, run_player=lambda c, r, p: leg_seen.append(c["case_id"]) or {
        "status": "OK", "decision": {"action": "reject", "reject_reason": "pricing"},
        "rulebook": "rb", "prompt_version": "pv", "requested_model": "m",
    }, report={"errors": []}) == 1
    assert leg_seen == ["cf_leg"] and st2["player"][f"{hour_key}|put"] == "OK"


def test_tick_prunes_player_state_to_1500_sorted_keys(tmp_path):
    archive = ar.Archive(tmp_path)
    state = archive.load_state()
    state["player"] = {f"2026-01-{i // 24 + 1:02d}T{i % 24:02d}|put": "OK" for i in range(1600)}
    archive.save_state(state)
    _tick(archive, NOW, None, FakeDeribit())
    pruned = archive.load_state()["player"]
    assert len(pruned) == 1500
    assert min(pruned) > "2026-01-01T00|put"


def _native_built_case(
    side: str,
    *,
    built_at_ms: int,
    expiry_ms: int = EXPIRY,
    sid_char: str | None = None,
    credit_btc: float | None = 0.008,
    quote_gaps: list[str] | None = None,
    valuation_ts_ms: int | None = None,
    l3_computed_at_ms: int | None = None,
):
    import astra_case_file_v1 as cf
    import astra_expiry_gamma_v1 as eg

    ch = sid_char or ("p" if side == "put" else "c")
    sid = (ch * 64)[:64]
    short_inst = "BTC-20OCT26-81500-P" if side == "put" else "BTC-20OCT26-84500-C"
    long_inst = "BTC-20OCT26-79500-P" if side == "put" else "BTC-20OCT26-86500-C"
    ks = 81500.0 if side == "put" else 84500.0
    kl = 79500.0 if side == "put" else 86500.0
    val_ts = (built_at_ms - 5_000) if valuation_ts_ms is None else valuation_ts_ms
    l3_ts = (built_at_ms - 5_000) if l3_computed_at_ms is None else l3_computed_at_ms
    exp_code = eg.expiry_code(expiry_ms)
    return cf.build(
        snapshot={
            "snapshot_id": sid,
            "candidate_id": f"cand_{side}",
            "contract": {
                "side": side,
                "venue": "Deribit",
                "underlying": "BTC",
                "settlement_currency": "BTC",
                "short_instrument": short_inst,
                "long_instrument": long_inst,
                "short_strike_usd": ks,
                "long_strike_usd": kl,
                "width_usd": 2000.0,
                "quantity_btc": 1.0,
                "expiry_ms": expiry_ms,
            },
            "market": {
                "valuation_ts_ms": val_ts,
                "quote": {
                    "credit_btc": credit_btc,
                    "valuation_ts_ms": val_ts,
                    "gap_reasons": list(quote_gaps or []),
                },
            },
        },
        raw_market={},
        asof_ms=val_ts,
        item=None,
        view={
            "expiry_gamma": {
                "status": "ok",
                "computed_at_ms": l3_ts,
                "by_expiry": {
                    exp_code: {
                        "expiry": exp_code,
                        "expiry_ms": expiry_ms,
                        "computed_at_ms": l3_ts,
                        "stale": False,
                        "atm_iv_pct": 52.0,
                        "expected_move_usd": 1400.0,
                        "walls": {"pin": 83000.0},
                    }
                },
            }
        },
        built_at_ms=built_at_ms,
    )


def test_partial_single_side_defers_at_05_and_10_then_archives_at_20(tmp_path):
    M = 60_000
    slot_16 = EXPIRY - 16 * H  # 16:00 UTC
    archive = ar.Archive(tmp_path)
    partial_view = {**_view(), "discovery_status": {"capture_state": "PARTIAL"}}

    # :05 and :10 (< 15m) defer freezing the lone Put candidate
    r05 = _tick(archive, slot_16 + 5 * M, partial_view, FakeDeribit())
    r10 = _tick(archive, slot_16 + 10 * M, partial_view, FakeDeribit())
    assert r05["cases"] == 0 and r10["cases"] == 0
    assert not list((tmp_path / "cases").glob("*/*.json.gz"))

    # :20 (>= 15m) archives the surviving single side before the 30m window closes
    r20 = _tick(archive, slot_16 + 20 * M, partial_view, FakeDeribit())
    assert r20["cases"] == 1
    written = list((tmp_path / "cases").glob("2026-10-19/16_put_*.json.gz"))
    assert len(written) == 1
    assert archive.verify()["ok"] is True


def test_backfill_slot_cases_hash_chain_idempotency_adoption_and_lock_busy(tmp_path):
    M = 60_000
    slot_16 = EXPIRY - 16 * H
    now_ms = slot_16 + 2 * M
    put_case = _native_built_case("put", built_at_ms=slot_16 + 15_000, sid_char="a")
    call_case = _native_built_case("call", built_at_ms=slot_16 + 25_000, sid_char="b")

    # 1. Fresh archive backfill writes both cases into chain.jsonl and passes verify()
    res1 = ar.backfill_slot_cases(
        tmp_path / "arch1",
        {"put": put_case, "call": call_case},
        slot_ms=slot_16,
        target_expiry_ms=EXPIRY,
        now_ms=now_ms,
    )
    assert res1["ok"] is True and res1["persisted"] is True and res1["lock_busy"] is False
    assert res1["wrote_sides"] == ["put", "call"]
    arch1 = ar.Archive(tmp_path / "arch1")
    v1 = arch1.verify()
    assert v1 == {"ok": True, "entries": 2, "problems": []}

    # 2. Second call is 100% idempotent (wrote_sides == [], chain entries unchanged, 1 file per side)
    alt_put = _native_built_case("put", built_at_ms=slot_16 + 30_000, sid_char="c")
    alt_call = _native_built_case("call", built_at_ms=slot_16 + 35_000, sid_char="d")
    res2 = ar.backfill_slot_cases(
        tmp_path / "arch1",
        {"put": alt_put, "call": alt_call},
        slot_ms=slot_16,
        target_expiry_ms=EXPIRY,
        now_ms=now_ms + 30_000,
    )
    assert res2["ok"] is True and res2["persisted"] is True and res2["wrote_sides"] == []
    assert [c["case_id"] for c in res2["cases"]] == [put_case["case_id"], call_case["case_id"]]
    assert arch1.verify() == {"ok": True, "entries": 2, "problems": []}
    assert len(list((tmp_path / "arch1" / "cases" / "2026-10-19").glob("16_put_*.json.gz"))) == 1
    assert len(list((tmp_path / "arch1" / "cases" / "2026-10-19").glob("16_call_*.json.gz"))) == 1

    # 3. Single-side adoption when 1 native side is already on disk
    arch2_dir = tmp_path / "arch2"
    arch2 = ar.Archive(arch2_dir)
    disk_put = _native_built_case("put", built_at_ms=slot_16 + 10_000, sid_char="e")
    arch2.write_new(
        f"cases/2026-10-19/16_put_{disk_put['case_id']}.json.gz",
        ar._gzip_json(disk_put),
        at_ms=slot_16 + 12_000,
        kind="case",
    )
    res_adopt = ar.backfill_slot_cases(
        arch2_dir,
        {"put": put_case, "call": call_case},
        slot_ms=slot_16,
        target_expiry_ms=EXPIRY,
        now_ms=now_ms,
    )
    assert res_adopt["wrote_sides"] == ["call"]
    assert [c["case_id"] for c in res_adopt["cases"]] == [disk_put["case_id"], call_case["case_id"]]
    assert len(list((arch2_dir / "cases" / "2026-10-19").glob("16_put_*.json.gz"))) == 1
    assert arch2.verify() == {"ok": True, "entries": 2, "problems": []}

    # 4. Graceful lock_busy=True when tick.lock is held, without touching _state/state.json or chain.jsonl
    busy_dir = tmp_path / "arch_busy"
    with ar._file_lock(busy_dir / "_state" / "tick.lock"):
        busy_res = ar.backfill_slot_cases(
            busy_dir,
            {"put": put_case, "call": call_case},
            slot_ms=slot_16,
            target_expiry_ms=EXPIRY,
            now_ms=now_ms,
            lock_timeout_s=0.15,
            lock_poll_s=0.05,
        )
        assert busy_res == {
            "ok": True,
            "persisted": False,
            "lock_busy": True,
            "cases": [put_case, call_case],
        }
        assert not (busy_dir / "chain.jsonl").exists()
        assert not (busy_dir / "_state" / "state.json").exists()
        assert (busy_dir / "_state" / "tick.lock").exists()
    # After lock release, tick.lock is cleaned up and backfill persists cleanly
    assert not (busy_dir / "_state" / "tick.lock").exists()
    after_busy = ar.backfill_slot_cases(
        busy_dir,
        {"put": put_case, "call": call_case},
        slot_ms=slot_16,
        target_expiry_ms=EXPIRY,
        now_ms=now_ms,
    )
    assert after_busy["persisted"] is True and after_busy["wrote_sides"] == ["put", "call"]
    assert ar.Archive(busy_dir).verify()["ok"] is True


def test_backfill_slot_cases_rejects_invalid_expiry_hash_skew_and_window(tmp_path):
    M = 60_000
    slot_16 = EXPIRY - 16 * H
    now_ms = slot_16 + 130_000
    put_ok = _native_built_case("put", built_at_ms=slot_16 + 20_000, sid_char="1")
    call_ok = _native_built_case("call", built_at_ms=slot_16 + 30_000, sid_char="2")

    # Wrong expiry_ms
    bad_exp = _native_built_case("call", built_at_ms=slot_16 + 30_000, expiry_ms=EXPIRY + 24 * H, sid_char="3")
    with pytest.raises(ValueError, match="expiry_ms"):
        ar.backfill_slot_cases(tmp_path / "e1", {"put": put_ok, "call": bad_exp}, slot_ms=slot_16, target_expiry_ms=EXPIRY, now_ms=now_ms)

    # Tampered content_sha256 / payload mutation
    tampered = json.loads(json.dumps(call_ok))
    tampered["contract_cn"] = "forged"
    with pytest.raises(ValueError, match="content_sha256"):
        ar.backfill_slot_cases(tmp_path / "e2", {"put": put_ok, "call": tampered}, slot_ms=slot_16, target_expiry_ms=EXPIRY, now_ms=now_ms)

    # Pair skew > 60s
    skew_call = _native_built_case("call", built_at_ms=slot_16 + 85_000, sid_char="4")
    with pytest.raises(ValueError, match="skew"):
        ar.backfill_slot_cases(tmp_path / "e3", {"put": put_ok, "call": skew_call}, slot_ms=slot_16, target_expiry_ms=EXPIRY, now_ms=now_ms)

    # Pre-slot_ms built_at_ms
    pre_slot_put = _native_built_case("put", built_at_ms=slot_16 - 1_000, sid_char="5")
    with pytest.raises(ValueError, match="built_at_ms"):
        ar.backfill_slot_cases(tmp_path / "e4", {"put": pre_slot_put, "call": call_ok}, slot_ms=slot_16, target_expiry_ms=EXPIRY, now_ms=now_ms)

    # Note: 5m = 300_000ms now_ms vs slot_16+20s (280s > 180s QUOTE_HOLD_MS) must reject stale valuation_ts_ms!
    with pytest.raises(ValueError, match="stale"):
        ar.backfill_slot_cases(tmp_path / "e4_stale", {"put": put_ok, "call": call_ok}, slot_ms=slot_16, target_expiry_ms=EXPIRY, now_ms=slot_16 + 205_000)

    # Fresh now_ms within 180s for remaining structural tests
    fresh_now = slot_16 + 130_000

    # Unpriced / gapped L2.quote (credit_btc=None or gap_reasons) rejected
    no_credit_call = _native_built_case("call", built_at_ms=slot_16 + 30_000, sid_char="9", credit_btc=None)
    with pytest.raises(ValueError, match="credit_btc"):
        ar.backfill_slot_cases(tmp_path / "e_nocred", {"put": put_ok, "call": no_credit_call}, slot_ms=slot_16, target_expiry_ms=EXPIRY, now_ms=fresh_now)

    gapped_call = _native_built_case("call", built_at_ms=slot_16 + 30_000, sid_char="g", quote_gaps=["short_depth_insufficient"])
    with pytest.raises(ValueError, match="gap_reasons"):
        ar.backfill_slot_cases(tmp_path / "e_gap", {"put": put_ok, "call": gapped_call}, slot_ms=slot_16, target_expiry_ms=EXPIRY, now_ms=fresh_now)

    # L3.expiry_walls > 5m old (e.g., 6m old while < 20m STALE_MS) rejected before poisoning archive slot
    stale_l3_call = _native_built_case("call", built_at_ms=slot_16 + 30_000, sid_char="w", l3_computed_at_ms=slot_16 - 340_000)
    with pytest.raises(ValueError, match="L3.expiry_walls"):
        ar.backfill_slot_cases(tmp_path / "e_l3", {"put": put_ok, "call": stale_l3_call}, slot_ms=slot_16, target_expiry_ms=EXPIRY, now_ms=fresh_now)

    # Existing disk case + new partner skew > 60s fails BEFORE writing partner
    arch_skew = ar.Archive(tmp_path / "e5")
    early_put = _native_built_case("put", built_at_ms=slot_16 + 5_000, valuation_ts_ms=slot_16 + 2_000, sid_char="6")
    arch_skew.write_new(f"cases/2026-10-19/16_put_{early_put['case_id']}.json.gz", ar._gzip_json(early_put), at_ms=slot_16 + 6_000, kind="case")
    late_put = _native_built_case("put", built_at_ms=slot_16 + 120_000, valuation_ts_ms=slot_16 + 118_000, sid_char="7")
    late_call = _native_built_case("call", built_at_ms=slot_16 + 125_000, valuation_ts_ms=slot_16 + 122_000, sid_char="8")
    with pytest.raises(ValueError, match="skew"):
        ar.backfill_slot_cases(tmp_path / "e5", {"put": late_put, "call": late_call}, slot_ms=slot_16, target_expiry_ms=EXPIRY, now_ms=fresh_now)
    assert not list((tmp_path / "e5" / "cases" / "2026-10-19").glob("16_call_*.json.gz"))
    assert arch_skew.verify() == {"ok": True, "entries": 1, "problems": []}


def _dual_view(*, put_sid: str = "a" * 64, call_sid: str | None = "b" * 64, capture_state: str = "COMPLETE"):
    base = _view(sid=put_sid)
    cands = list(base["candidates"])
    if call_sid is not None:
        cands.append({
            "side": "call",
            "snapshot": {
                "snapshot_id": call_sid,
                "contract": {
                    "side": "call",
                    "short_instrument": "BTC-20OCT26-84500-C",
                    "long_instrument": "BTC-20OCT26-86500-C",
                    "short_strike_usd": 84500.0,
                    "long_strike_usd": 86500.0,
                    "expiry_ms": EXPIRY,
                },
            },
        })
    return {**base, "candidates": cands, "discovery_status": {"capture_state": capture_state}}


def test_complete_single_side_defers_early_and_writes_pair_or_lone_at_20(tmp_path):
    M = 60_000
    slot_16 = EXPIRY - 16 * H

    # Branch 1: COMPLETE with 1 displayed side defers at :00/:05, then writes both cleanly when Call joins at :10
    arch_pair = ar.Archive(tmp_path / "pair_recovery")
    single_complete = _dual_view(call_sid=None, capture_state="COMPLETE")
    r00 = _tick(arch_pair, slot_16, single_complete, FakeDeribit())
    r05 = _tick(arch_pair, slot_16 + 5 * M, single_complete, FakeDeribit())
    assert r00["cases"] == 0 and r05["cases"] == 0
    assert not list((tmp_path / "pair_recovery" / "cases").glob("*/*.json.gz"))

    r10 = _tick(arch_pair, slot_16 + 10 * M, _dual_view(capture_state="COMPLETE"), FakeDeribit())
    assert r10["cases"] == 2
    assert len(list((tmp_path / "pair_recovery" / "cases" / "2026-10-19").glob("16_put_*.json.gz"))) == 1
    assert len(list((tmp_path / "pair_recovery" / "cases" / "2026-10-19").glob("16_call_*.json.gz"))) == 1
    assert arch_pair.verify()["ok"] is True

    # Branch 2: COMPLETE with 1 displayed side at :00 and :10 defers, then writes the lone side at :20
    arch_lone = ar.Archive(tmp_path / "still_single")
    assert _tick(arch_lone, slot_16, single_complete, FakeDeribit())["cases"] == 0
    assert _tick(arch_lone, slot_16 + 10 * M, single_complete, FakeDeribit())["cases"] == 0
    assert not list((tmp_path / "still_single" / "cases").glob("*/*.json.gz"))
    r20 = _tick(arch_lone, slot_16 + 20 * M, single_complete, FakeDeribit())
    assert r20["cases"] == 1
    assert len(list((tmp_path / "still_single" / "cases" / "2026-10-19").glob("16_put_*.json.gz"))) == 1
    assert arch_lone.verify()["ok"] is True


def test_two_displayed_sides_with_fetch_case_error_defers_at_05_without_poisoning_slot(tmp_path):
    M = 60_000
    slot_16 = EXPIRY - 16 * H
    arch = ar.Archive(tmp_path / "held_recovery")
    held_call_sid = "h" * 64
    live_call_sid = "c" * 64

    def fetch_with_held_call(sid: str):
        if sid == held_call_sid:
            raise RuntimeError("409 quote_held_not_current")
        return _case(sid)

    # At :05 (< 15m), Call raises quote_held_not_current -> lone surviving Put is deferred and no case error recorded
    r05 = ar.tick(
        arch,
        now_ms=slot_16 + 5 * M,
        view=_dual_view(put_sid="p" * 64, call_sid=held_call_sid, capture_state="COMPLETE"),
        fetch_case=fetch_with_held_call,
        deribit=ar.Deribit(FakeDeribit()),
    )
    assert r05["cases"] == 0
    assert not [e for e in r05["errors"] if e.get("step") == "case"]
    assert not list((tmp_path / "held_recovery" / "cases").glob("*/*.json.gz"))

    # At :10 (< 15m), Call snapshot refreshes -> both Put and Call write cleanly together
    r10 = ar.tick(
        arch,
        now_ms=slot_16 + 10 * M,
        view=_dual_view(put_sid="p" * 64, call_sid=live_call_sid, capture_state="COMPLETE"),
        fetch_case=fetch_with_held_call,
        deribit=ar.Deribit(FakeDeribit()),
    )
    assert r10["cases"] == 2
    assert not [e for e in r10["errors"] if e.get("step") == "case"]
    assert len(list((tmp_path / "held_recovery" / "cases" / "2026-10-19").glob("16_put_*.json.gz"))) == 1
    assert len(list((tmp_path / "held_recovery" / "cases" / "2026-10-19").glob("16_call_*.json.gz"))) == 1
    assert arch.verify()["ok"] is True

    # If still failing at :20 (>= 15m), surviving Put writes and Call error is recorded
    arch_late = ar.Archive(tmp_path / "held_late")
    r20 = ar.tick(
        arch_late,
        now_ms=slot_16 + 20 * M,
        view=_dual_view(put_sid="p" * 64, call_sid=held_call_sid, capture_state="COMPLETE"),
        fetch_case=fetch_with_held_call,
        deribit=ar.Deribit(FakeDeribit()),
    )
    assert r20["cases"] == 1
    assert [e["side"] for e in r20["errors"] if e.get("step") == "case"] == ["call"]
    assert arch_late.verify()["ok"] is True
