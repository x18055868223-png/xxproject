"""Quote robustness (2026-10-28): the two books are read as a pair, the fee does not wait for a
tradable quote, a briefly out-of-step quote holds the last good reading of the same contract
for display only, and the card says "temporarily out of step" instead of "data incomplete"."""
import copy
import importlib.util
from pathlib import Path

import pytest

from tools import astra_underwriting_discovery_v20 as discovery
from tools import astra_underwriting_public_quote_v20 as q
from tools import astra_underwriting_v20 as u
from tools import astra_unsuitable_v1 as un
from test_astra_underwriting_v20 import T, sample

spec = importlib.util.spec_from_file_location("v20_fixtures_qr", Path(__file__).with_name("test_astra_workbench_v20.py"))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


# ---------------------------------------------------------------- pair capture

def _pair_fetch(fixture, names, stamps, calls=None, *, daily=False):
    """Book timestamps come from `stamps` (one entry per book read, in order)."""
    if daily:
        for leg in ("short", "long"):
            fixture["metadata"][leg]["taker_commission"] = 0.0003
            fixture["metadata"][leg]["settlement_period"] = "day"
    reads = iter(stamps)

    def fetch(path, params):
        leg = "short" if params["instrument_name"] == names["short"] else "long"
        if calls is not None:
            calls.append((path, leg))
        if path == "public/get_instrument":
            return copy.deepcopy(fixture["metadata"][leg])
        book = copy.deepcopy(fixture["market"][leg + "_book"])
        book["index_price"] = 100000
        book["timestamp"] = next(reads)
        return book
    return fetch


def _names(fixture):
    return {leg: fixture["candidate"][f"{leg}_instrument"] for leg in ("short", "long")}


def test_a_pair_out_of_step_on_arrival_is_read_again_as_a_pair():
    fixture = sample(risk=False)
    names = _names(fixture)
    calls = []
    # first pair: long book 3 s older than short (skew > 2 s); second pair in step
    fetch = _pair_fetch(fixture, names, [T, T - 3000, T - 100, T - 200], calls)
    payload = q.capture_candidate(names["short"], names["long"], 0.1, transport=fetch, clock_ms=lambda: T)
    assert payload["capture_reads"] == {"book_rounds": 2, "book_requests": 4,
                                        "first_round_sync_reasons": ["leg_book_time_skew"]}
    assert calls.count(("public/get_order_book", "short")) == 2
    # the pair actually used is the second one, and the hashes describe exactly it
    assert payload["market"]["short_book"]["timestamp"] == T - 100
    assert payload["market"]["long_book"]["timestamp"] == T - 200
    snap = u.evaluate(payload)
    assert snap["market"]["quote"]["credit_btc"] is not None
    assert snap["market"]["quote"]["source_book_hash"] == u._hash(
        {"short": payload["market"]["short_book"], "long": payload["market"]["long_book"]})


def test_a_pair_in_step_is_read_once_and_a_second_bad_pair_stays_a_gap():
    fixture = sample(risk=False)
    names = _names(fixture)
    once = q.capture_candidate(names["short"], names["long"], 0.1,
                               transport=_pair_fetch(fixture, names, [T, T - 500]), clock_ms=lambda: T)
    assert once["capture_reads"]["book_rounds"] == 1
    # both pairs out of step: exactly one re-read, and the freshness rules are unchanged
    stale = q.capture_candidate(names["short"], names["long"], 0.1,
                                transport=_pair_fetch(fixture, names, [T, T - 6000, T, T - 6000]),
                                clock_ms=lambda: T)
    assert stale["capture_reads"]["book_rounds"] == 2
    snap = u.evaluate(stale)
    assert snap["market"]["quote"]["credit_btc"] is None
    assert {"leg_book_time_skew", "long_book_stale_or_future"} <= set(snap["market"]["quote"]["gap_reasons"])


# ---------------------------------------------------------------- fee without a tradable quote

def test_fee_is_computed_from_the_books_even_when_the_quote_is_out_of_step():
    fixture = sample(risk=False)
    names = _names(fixture)
    payload = q.capture_candidate(names["short"], names["long"], 0.1,
                                  transport=_pair_fetch(fixture, names, [T, T - 3000, T, T - 3000], daily=True),
                                  clock_ms=lambda: T)
    snap = u.evaluate(payload)
    assert snap["market"]["quote"]["credit_btc"] is None          # never a tradable credit
    assert snap["economics"]["net_credit_after_fee_btc"] is None
    assert snap["fee"]["basis"] == "configured_assumption"
    assert snap["fee"]["assumption_basis"] == q.FEE_FROM_BOOKS
    assert snap["fee"]["amount_btc"] == pytest.approx(0.00006)
    assert "fee_unknown" not in snap["gap_reasons"]


def test_fee_falls_back_to_marks_when_the_books_cannot_fill_and_says_so():
    fixture = sample(risk=False)
    names = _names(fixture)
    for leg in ("short", "long"):
        fixture["market"][leg + "_book"]["bids"] = [[0.001, 0.01]]
        fixture["market"][leg + "_book"]["asks"] = [[0.002, 0.01]]
        fixture["market"][leg + "_book"]["mark_price"] = 0.0016
    payload = q.capture_candidate(names["short"], names["long"], 0.1,
                                  transport=_pair_fetch(fixture, names, [T, T], daily=True), clock_ms=lambda: T)
    fee = payload["fee"]
    assert fee["assumption_basis"] == q.FEE_FROM_MARKS
    assert fee["detail"]["legs"]["short"]["price_source"] == "mark_price"
    # min(0.0003, 12.5% x 0.0016 = 0.0002) x 0.1 per leg
    assert fee["amount_btc"] == pytest.approx(2 * 0.0002 * 0.1)
    snap = u.evaluate(payload)
    assert snap["market"]["quote"]["credit_btc"] is None
    assert "short_depth_insufficient" in snap["market"]["quote"]["gap_reasons"]


def test_no_fee_without_both_books_or_for_a_non_daily_expiry():
    fixture = sample(risk=False)
    names = _names(fixture)
    with pytest.raises(ValueError):
        q.configured_fee(fixture["metadata"], {"short": fixture["market"]["short_book"], "long": {}},
                         names["short"], names["long"], 0.1)
    fixture["metadata"]["long"]["settlement_period"] = "week"
    fixture["metadata"]["short"]["settlement_period"] = "day"
    for leg in ("short", "long"):
        fixture["metadata"][leg]["taker_commission"] = 0.0003
    payload = q.capture_candidate(names["short"], names["long"], 0.1,
                                  transport=_pair_fetch(fixture, names, [T, T]), clock_ms=lambda: T)
    assert payload["fee"]["basis"] == "unknown"


# ---------------------------------------------------------------- card classification

def _card(gaps, *, held=None, persistence=None, capture_gaps=None, depth=None):
    cand = {"side": "put", "snapshot": {"contract": {"quantity_btc": 1.0, "side": "put"},
                                        "market": {"quote": {"gap_reasons": gaps,
                                                             "available_depth_btc": depth or {"short": 5, "long": 5}}}}}
    if held:
        cand["held_capture"] = held
    if persistence:
        cand["quote_gap_persistence"] = persistence
    if capture_gaps:
        cand["capture_gaps"] = capture_gaps
    return un.card(cand)


def test_card_reads_plumbing_gaps_as_a_caution_and_market_problems_as_a_veto():
    held = _card([], held={"age_ms": 42_000, "reason_codes": ["leg_book_time_skew"]})
    assert held["veto_count"] == 0 and held["items"][0]["code"] == "quote_held"
    assert held["items"][0]["title_cn"] == "报价暂时不同步（42 秒前的读数）"
    assert "两腿盘口读取时间相差过大" in held["items"][0]["evidence_cn"]
    brief = _card(["leg_book_time_skew", "long_book_stale_or_future"], persistence={"unsynced_ms": 30_000})
    assert brief["veto_count"] == 0 and brief["items"][0]["code"] == "quote_unsynced"
    failed_read = _card(["long_book_instrument_mismatch", "long_book_not_open", "leg_index_missing"],
                        capture_gaps=["long_book_unavailable:TimeoutError"])
    assert failed_read["veto_count"] == 0
    lasting = _card(["leg_book_time_skew"], persistence={"unsynced_ms": 181_000})
    assert lasting["veto_count"] == 1 and lasting["items"][0]["title_cn"] == "资料不完整"
    assert "报价持续不同步（181 秒）" in lasting["items"][0]["evidence_cn"]
    for code, label in (("short_depth_insufficient", "盘口深度不足"), ("long_crossed_book", "盘口交叉"),
                        ("short_book_not_open", "合约未开放交易"), ("short_book_instrument_mismatch", "合约与盘口不符")):
        card = _card(["leg_book_time_skew", code], depth={"short": 5, "long": 5})
        assert card["veto_count"] == 1 and label in card["items"][0]["evidence_cn"], code
        assert not any(i["code"].startswith("quote_") for i in card["items"])


# ---------------------------------------------------------------- hold window (workbench)

def _skewed_capture(state):
    def capture(short, long, quantity, *, now_ms):
        payload = fixtures.fake_quote_capture(short, long, quantity, now_ms=now_ms)
        if state["mode"] == "skew":
            payload["market"]["long_book"]["timestamp"] = now_ms - 3000
        elif state["mode"] == "crossed":
            payload["market"]["long_book"]["bids"] = [[0.009, 1.0]]
        return payload
    return capture


def _workbench(tmp_path):
    clock = [fixtures.T]
    wb, path = fixtures.make_workbench(tmp_path, clock=lambda: clock[0])
    state = {"mode": "good"}
    wb.quote_capture = _skewed_capture(state)
    fixtures.write_fmz(path, fixtures.native_gate())
    return wb, path, clock, state


def _put(view):
    return next(c for c in view["candidates"] if c["side"] == "put")


def test_a_brief_skew_shows_the_last_good_reading_which_cannot_be_locked_or_frozen(tmp_path):
    wb, path, clock, state = _workbench(tmp_path)
    wb.refresh(record_daily=False)
    good = _put(wb.cached_view())
    good_id = good["snapshot"]["snapshot_id"]
    assert "held_capture" not in good and good["lock_availability"]["enabled"]

    clock[0] += 60_000
    fixtures.write_fmz(path, fixtures.native_gate(ts=clock[0] - 1000), now=clock[0])
    state["mode"] = "skew"
    fresh = _put(wb.refresh(record_daily=False))
    # everything the refresh records (locks, research, audit) saw the newest, gapped capture
    assert "leg_book_time_skew" in fresh["snapshot"]["market"]["quote"]["gap_reasons"]
    shown = _put(wb.cached_view())
    assert shown["snapshot"]["snapshot_id"] == good_id
    assert shown["held_capture"]["age_ms"] == 60_000
    assert shown["held_capture"]["reason_codes"] == ["leg_book_time_skew"]
    assert shown["lock_availability"] == {"enabled": False, "reason": "quote_held_not_current"}
    assert shown["unsuitable"]["veto_count"] == 0
    assert shown["unsuitable"]["items"][0]["title_cn"] == "报价暂时不同步（60 秒前的读数）"
    candidate_id = shown["snapshot"]["candidate_id"]
    for call in (lambda: wb.lock_candidate(candidate_id, good_id, "k1", authorization="Bearer secret"),
                 lambda: wb.case_file(good_id),
                 lambda: wb.decision_path_start(candidate_id, good_id, authorization="Bearer secret")):
        with pytest.raises(fixtures.workbench.WorkbenchError) as exc:
            call()
        assert exc.value.code == "quote_held_not_current"

    # past the window the gap is shown as it is, and it is a data veto again
    clock[0] += 130_000
    fixtures.write_fmz(path, fixtures.native_gate(ts=clock[0] - 1000), now=clock[0])
    wb.refresh(record_daily=False)
    late = _put(wb.cached_view())
    assert "held_capture" not in late
    assert late["quote_gap_persistence"]["unsynced_ms"] == 190_000
    assert late["unsuitable"]["veto_count"] >= 1 and late["unsuitable"]["items"][0]["title_cn"] == "资料不完整"
    assert wb._held_snapshot_ids == set()


def test_a_market_problem_is_never_hidden_behind_a_held_reading(tmp_path):
    wb, path, clock, state = _workbench(tmp_path)
    wb.refresh(record_daily=False)
    clock[0] += 30_000
    fixtures.write_fmz(path, fixtures.native_gate(ts=clock[0] - 1000), now=clock[0])
    state["mode"] = "crossed"
    wb.refresh(record_daily=False)
    shown = _put(wb.cached_view())
    assert "held_capture" not in shown
    assert "long_crossed_book" in shown["snapshot"]["market"]["quote"]["gap_reasons"]
    assert shown["unsuitable"]["veto_count"] >= 1
    # and the good reading is dropped: a later plumbing gap does not bring it back
    clock[0] += 30_000
    fixtures.write_fmz(path, fixtures.native_gate(ts=clock[0] - 1000), now=clock[0])
    state["mode"] = "skew"
    wb.refresh(record_daily=False)
    assert "held_capture" not in _put(wb.cached_view())


def test_a_held_reading_is_never_reused_as_a_lock_sample(tmp_path):
    wb, path, clock, state = _workbench(tmp_path)
    wb.refresh(record_daily=False)
    good = _put(wb.cached_view())
    wb.lock_candidate(good["snapshot"]["candidate_id"], good["snapshot"]["snapshot_id"], "lock-1",
                      authorization="Bearer secret")
    clock[0] += 20_000
    fixtures.write_fmz(path, fixtures.native_gate(ts=clock[0] - 1000), now=clock[0])
    state["mode"] = "skew"
    wb.refresh(record_daily=False)
    assert _put(wb._current_view).get("held_capture")
    fresh = []

    def capture(episode, view, now):
        fresh.append(now)
        raise fixtures.workbench.WorkbenchError(409, "locked_quote_unavailable", "test")
    wb._capture_locked_point = capture
    # the sampler pass reads the cached (held) view: it must capture afresh, not reuse the held point
    # due for a sample (past interval - slack since the lock) while the held point is still
    # younger than one interval: exactly when the sampler would otherwise reuse it
    interval = wb.config.locked_sample_interval_ms
    wb._refresh_locked_candidates(wb._current_view, fixtures.T + interval - interval // 8, from_refresh=False)
    assert len(fresh) == 1


# ---------------------------------------------------------------- discovery

def test_discovery_retries_a_failed_short_candidate_book_once():
    rows = [dict(r) for r in fixtures.instruments()]
    name = rows[0]["instrument_name"]
    failures = {name: 1}

    def get(base, path, params, timeout):
        if failures.get(params["instrument_name"]):
            failures[params["instrument_name"]] -= 1
            raise TimeoutError("read timed out")
        return {"result": {"instrument_name": params["instrument_name"], "timestamp": T,
                           "greeks": {"delta": -0.35}}}

    enriched, identity = discovery._with_public_greeks(rows, {name}, base_url="x", timeout=1, get=get,
                                                        now=lambda: T / 1000)
    assert identity["order_book_errors"] == {}
    assert identity["order_book_retried"] == [name]
    assert enriched[0]["greeks"] == {"delta": -0.35}

    def always(base, path, params, timeout):
        raise TimeoutError("read timed out")

    _, identity = discovery._with_public_greeks(rows, {name}, base_url="x", timeout=1, get=always,
                                                now=lambda: T / 1000)
    assert identity["order_book_errors"] == {name: "TimeoutError"}
