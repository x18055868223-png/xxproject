"""Selection scorecard (docs/opus/34 §2) and decision_event@0.2."""
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

import astra_research_archive_v1 as ar
import astra_research_scorecards_v1 as sc
import astra_research_selection_v1 as sel
import astra_research_validation_v1 as rv
from test_astra_research_archive_v1 import NOW as A_NOW, FakeDeribit, _case, _lock, _view

H = 3_600_000
M = 60_000
E = 1_794_470_400_000                 # 2026-11-12 08:00 UTC
R1, R2 = E - 30 * H, E - 18 * H       # 2026-11-11 02:00 and 14:00 UTC
SCHEMAS = Path(__file__).resolve().parents[1] / "deploy" / "research_archive" / "schemas"


def _contract(side="put", ks=80000.0, kl=78000.0, q=1.0, expiry=E):
    tag = "P" if side == "put" else "C"
    return {"side": side, "short_instrument": f"BTC-12NOV26-{int(ks)}-{tag}",
            "long_instrument": f"BTC-12NOV26-{int(kl)}-{tag}", "short_strike_usd": ks, "long_strike_usd": kl,
            "width_usd": abs(ks - kl), "quantity_btc": q, "expiry_ms": expiry}


def _key(c):
    return f"{c['short_instrument']}|{c['long_instrument']}"


def _write_case(root, slot, side, cid, *, contract=None, built=None, asof=None, eligible=True, current="auto",
                credit=0.003, max_loss=0.022, mark=0.0032, selection=True, vetoes=()):
    contract = contract or _contract(side)
    built = slot + 10_000 if built is None else built
    case = {"case_id": cid, "asof_ms": slot - 15_000 if asof is None else asof,
            "build": {"built_at_ms": built, "builder": "astra_case_file_v1@0.1.9"},
            "layers": {"L1": [{"ref": "L1.contract", "values": contract},
                              {"ref": "L1.economics", "values": {"net_credit_after_fee_btc": credit}}]},
            "refs": {"L1.contract": {"layer": "L1", "index": 0}, "L1.economics": {"layer": "L1", "index": 1}}}
    if selection:
        rnd = (1 if (contract["expiry_ms"] - slot) / H >= 24 else 2) if current == "auto" else current
        case["selection"] = {"schema": "selection@0.1", "eligible": eligible,
                             "vetoes": [{"code": v} for v in vetoes], "cautions": [],
                             "pricing": {"max_loss_btc": max_loss, "mark_spread_btc": mark,
                                         "required_edge_bid": 0.1, "required_edge_mid": 0.0},
                             "round": {"current": rnd}}
    import datetime as dt
    hour = dt.datetime.fromtimestamp(slot / 1000, dt.timezone.utc)
    rel = f"cases/{hour.strftime('%Y-%m-%d')}/{hour.strftime('%H')}_{side}_{cid}.json.gz"
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(gzip.compress(json.dumps(case).encode("utf-8")))
    return rel, contract


def _write_outcome(root, contract, cases, payout, *, delivery=81000.0, status="settled"):
    row = {"schema": "episode_outcome@0.1", "contract_key": _key(contract), "status": status, "side": contract["side"],
           "short_strike_usd": contract["short_strike_usd"], "long_strike_usd": contract["long_strike_usd"],
           "expiry_ms": contract["expiry_ms"], "first_seen_ms": E - 40 * H, "delivery_price_usd": delivery,
           "payout_btc": payout, "cases": cases}
    path = root / "outcomes" / "2026-11-12.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _write_path(root, points):
    p = root / "paths" / "2026-11-12.json.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(gzip.compress(json.dumps({"points": points}).encode("utf-8")))


def _flat_path(price=82000.0, start=E - 47 * H):
    return [[t, price] for t in range(start, E + 1, 5 * M)]


def _one(root, **kw):
    payout = kw.pop("payout", 0.0)
    rel, _ = _write_case(root, R1, "put", "cf_a", **kw)
    # the settled expiry is E; a case of another expiry in E's round hour is a mismatch
    _write_outcome(root, _contract(), [{"case_id": "cf_a", "asof_ms": R1 - 15_000, "path": rel}], payout)
    _write_path(root, _flat_path())
    return sel.build(root, now_ms=E + H)


def _sample(card, side="put", rnd="r1"):
    return next(s for s in card["samples"] if s["side"] == side and s["round"] == rnd)


# ---------------------------------------------------------------- results


def test_result_expected_and_luck_in_max_loss_multiples(tmp_path):
    rel, c = _write_case(tmp_path, R1, "put", "cf_a", credit=0.003, max_loss=0.022, mark=0.0032)
    _write_outcome(tmp_path, c, [{"case_id": "cf_a", "asof_ms": R1, "path": rel}], 0.01)
    _write_path(tmp_path, _flat_path())
    s = _sample(sel.build(tmp_path, now_ms=E + H))
    assert s["status"] == "ok" and s["verdict"] == "eligible"
    assert s["result_btc"] == pytest.approx(0.003 - 0.01)
    assert s["result_x"] == pytest.approx(-0.007 / 0.022)
    assert s["expected_x"] == pytest.approx((0.003 - 0.0032) / 0.022)
    assert s["luck_x"] == pytest.approx((0.0032 - 0.01) / 0.022)
    assert s["result_x"] == pytest.approx(s["expected_x"] + s["luck_x"])


def test_quantity_multiplies_the_payout_only(tmp_path):
    # credit, mark and max loss already carry q (astra_underwriting_v20 / decision path); the payout is per 1 BTC
    rel, c = _write_case(tmp_path, R1, "put", "cf_a", contract=_contract(q=2.0), credit=0.006, max_loss=0.044, mark=0.0064)
    _write_outcome(tmp_path, c, [{"case_id": "cf_a", "asof_ms": R1, "path": rel}], 0.01)
    _write_path(tmp_path, _flat_path())
    s = _sample(sel.build(tmp_path, now_ms=E + H))
    assert s["result_btc"] == pytest.approx(0.006 - 0.02)
    assert s["result_x"] == pytest.approx((0.006 - 0.02) / 0.044)
    assert s["result_x"] == pytest.approx(s["expected_x"] + s["luck_x"])


# ---------------------------------------------------------------- sample checks


def test_freeze_instant_decides_the_window_not_the_quote_time(tmp_path):
    # the case's asof is the quote valuation, seconds before the hour; the freeze is after it
    card = _one(tmp_path, asof=R1 - 30_000, built=R1 + 20_000)
    assert _sample(card)["status"] == "ok"


@pytest.mark.parametrize("kw, status", [
    ({"built": R1 + 41 * M}, "outside_window"),
    ({"built": R1 + 10 * M, "asof": R1 + 10 * M - 6 * M}, "outside_window"),   # quote older than 5 minutes
    ({"contract": _contract(expiry=E + 24 * H)}, "expiry_mismatch"),
    ({"current": 2}, "round_mismatch"),
    ({"credit": None}, "no_credit"),
    ({"max_loss": None}, "no_max_loss"),
    ({"max_loss": 0.0}, "no_max_loss"),
])
def test_each_failed_check_has_its_status_and_joins_no_group(tmp_path, kw, status):
    card = _one(tmp_path, **kw)
    assert _sample(card)["status"] == status
    assert card["coverage"]["by_status"][status] == 1
    assert all(g["n"] == 0 for g in card["groups"])


def test_missing_case_and_unsettled_outcome(tmp_path):
    rel, c = _write_case(tmp_path, R2, "put", "cf_b")
    _write_outcome(tmp_path, c, [{"case_id": "cf_b", "asof_ms": R2, "path": rel}], None, status="unavailable")
    card = sel.build(tmp_path, now_ms=E + H)
    assert _sample(card, rnd="r1")["status"] == "missing_case"
    assert _sample(card, rnd="r2")["status"] == "no_outcome"
    assert _sample(card, side="call")["status"] == "missing_case"
    assert card["coverage"]["samples"] == 4


def test_verdicts_route_to_groups(tmp_path):
    rows = []
    for cid, slot, side, kw in (("cf_e", R1, "put", {}), ("cf_v", R2, "put", {"eligible": False, "vetoes": ["v1"]}),
                                ("cf_u", R1, "call", {"selection": False}), ("cf_n", R2, "call", {"eligible": None})):
        c = _contract(side, *((80000.0, 78000.0) if side == "put" else (84000.0, 86000.0)))
        rel, c = _write_case(tmp_path, slot, side, cid, contract=c, **kw)
        rows.append((c, cid, rel, slot))
    for c, cid, rel, slot in rows:
        _write_outcome(tmp_path, c, [{"case_id": cid, "asof_ms": slot, "path": rel}], 0.0)
    _write_path(tmp_path, _flat_path())
    card = sel.build(tmp_path, now_ms=E + H)
    groups = {g["id"]: g for g in card["groups"]}
    assert groups["eligible_all"]["n"] == 1 and groups["vetoed"]["n"] == 1
    # eligible, vetoed and the empty verdict (it has a max loss); the case without selection has none: not scored
    assert groups["all_incl_vetoed"]["n"] == 3
    assert _sample(card, "call", "r1")["status"] == "no_max_loss"
    assert card["coverage"]["by_verdict"] == {"eligible": 1, "vetoed": 1, "undetermined": 2}
    assert _sample(card, "put", "r2")["veto_codes"] == ["v1"]
    assert _sample(card, "call", "r1")["verdict"] == "undetermined"


def test_undetermined_with_a_max_loss_counts_only_in_all(tmp_path):
    card = _one(tmp_path, eligible=None)
    groups = {g["id"]: g for g in card["groups"]}
    assert _sample(card)["verdict"] == "undetermined" and _sample(card)["status"] == "ok"
    assert groups["all_incl_vetoed"]["n"] == 1 and groups["eligible_all"]["n"] == 0 and groups["vetoed"]["n"] == 0


# ---------------------------------------------------------------- path and secondary


def test_path_counts_only_after_the_freeze(tmp_path):
    path = _flat_path()
    path = [[t, 79000.0 if R1 - 3 * H <= t <= R1 - 2 * H else p] for t, p in path]   # touched before the case only
    rel, c = _write_case(tmp_path, R1, "put", "cf_a")
    _write_outcome(tmp_path, c, [{"case_id": "cf_a", "asof_ms": R1, "path": rel}], 0.0)
    _write_path(tmp_path, path)
    s = _sample(sel.build(tmp_path, now_ms=E + H))
    assert s["touched_short"] is False and s["crossed_long"] is False
    path2 = [[t, 77000.0 if E - 5 * H <= t <= E - 4 * H else p] for t, p in _flat_path()]
    _write_path(tmp_path, path2)
    s = _sample(sel.build(tmp_path, now_ms=E + H))
    assert s["touched_short"] is True and s["crossed_long"] is True
    assert s["max_adverse_pct"] == pytest.approx((80000.0 - 77000.0) / 80000.0 * 100)


def test_iv_change_uses_rows_at_or_before_each_end_and_sells_positive(tmp_path):
    rel, c = _write_case(tmp_path, R1, "put", "cf_a")
    _write_outcome(tmp_path, c, [{"case_id": "cf_a", "asof_ms": R1, "path": rel}], 0.0)
    _write_path(tmp_path, _flat_path())
    rows = [{"contract_key": _key(c), "at_ms": R1 - 5 * M, "short": {"mark_iv": 50.0}, "distance_in_moves": 0.9},
            {"contract_key": _key(c), "at_ms": R1 + 5 * M, "short": {"mark_iv": 99.0}},     # after the freeze: unused
            {"contract_key": _key(c), "at_ms": R1 + 6 * H - 10 * M, "short": {"mark_iv": 47.5}}]
    led = tmp_path / "ledger" / "2026-11-11.jsonl"
    led.parent.mkdir(parents=True)
    led.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    s = _sample(sel.build(tmp_path, now_ms=E + H))
    assert s["iv_change_6h_pp"] == pytest.approx(2.5) and s["distance_in_moves"] == 0.9


# ---------------------------------------------------------------- statistics


def test_clustered_ols_matches_clustered_difference_without_the_side_dummy():
    values = [(0.1, 1, "d1"), (-0.2, 0, "d1"), (0.3, 1, "d2"), (0.0, 0, "d2"), (-0.1, 1, "d3"), (0.05, 0, "d3"),
              (0.2, 0, "d4"), (-0.3, 1, "d4")]
    ref = rv.clustered_difference(values)
    fit = sel.clustered_ols([v[0] for v in values], [[1.0, float(v[1])] for v in values], [v[2] for v in values])
    assert fit["beta"][1] == pytest.approx(ref["effect"]) and fit["se"][1] == pytest.approx(ref["se"])


def test_clustered_ols_with_side_dummy_and_degenerate_cases():
    y = [1.0, 2.0, 3.0, 5.0, 2.0, 4.0]
    X = [[1.0, g, s] for g, s in ((0, 0), (1, 0), (0, 1), (1, 1), (1, 0), (0, 1))]
    fit = sel.clustered_ols(y, X, ["a", "a", "b", "b", "c", "c"])
    # exact OLS on this design (checked with numpy.linalg.lstsq)
    assert fit["beta"] == pytest.approx([5 / 6, 1.25, 2.75])
    assert all(se is not None and se > 0 for se in fit["se"])
    assert sel.clustered_ols([1.0, 2.0], [[1.0], [1.0]], ["a", "a"])["se"] is None          # one cluster
    assert sel.clustered_ols([1.0, 2.0], [[1.0, 0.0], [1.0, 0.0]], ["a", "b"])["beta"] is None   # singular
    assert sel.mean_stats([])["mean"] is None


# ---------------------------------------------------------------- human locks


def test_human_lock_uses_the_latest_case_at_or_before_the_lock(tmp_path):
    rel_a, c = _write_case(tmp_path, R1, "put", "cf_a", credit=0.003)
    rel_b, _ = _write_case(tmp_path, R1 + H, "put", "cf_b", credit=0.009)
    _write_outcome(tmp_path, c, [{"case_id": "cf_a", "asof_ms": R1, "path": rel_a},
                                 {"case_id": "cf_b", "asof_ms": R1 + H, "path": rel_b}], 0.0)
    _write_path(tmp_path, _flat_path())
    lock = {"schema": "decision_event@0.1", "source": "lock", "event": "lock_created", "at_ms": R1 + 30 * M,
            "contract_key": _key(c), "side": "put", "ref_id": "lce_1", "detail": {}}
    late = {**lock, "ref_id": "lce_2", "at_ms": R1 + 3 * H}       # no case within 65 minutes before it
    hourly = {**lock, "ref_id": "lce_3", "at_ms": R1 + H + 55 * M}  # 55 minutes after the hourly freeze: scored
    d = tmp_path / "decisions" / "2026-11-11.jsonl"
    d.parent.mkdir(parents=True)
    d.write_text("".join(json.dumps(x) + "\n" for x in (lock, late, hourly)), encoding="utf-8")
    locks = sel.build(tmp_path, now_ms=E + H)["human_locks"]
    first, second, third = locks["items"]
    assert first["case_id"] == "cf_a" and first["result_btc"] == pytest.approx(0.003) and first["case_age_min"] == 30.0
    assert second["status"] == "no_case_at_lock"
    assert third["case_id"] == "cf_b" and third["case_age_min"] == 55.0 and locks["scored"] == 2


def test_case_before_0_1_9_gets_the_same_max_loss_from_its_own_fields(tmp_path):
    # no selection: round_info.max_loss on L1 (q x width / S0 - net credit); mark from the two legs' marks
    rel, c = _write_case(tmp_path, R1, "put", "cf_a", selection=False, credit=0.003)
    path = tmp_path / rel
    case = json.loads(gzip.decompress(path.read_bytes()))
    case["layers"]["L1"][1]["values"]["valuation_spot_usd"] = 80000.0
    case["layers"]["L2"] = [{"ref": "L2.book.short", "values": {"mark_price": 0.005}},
                            {"ref": "L2.book.long", "values": {"mark_price": 0.0018}}]
    case["refs"].update({"L2.book.short": {"layer": "L2", "index": 0}, "L2.book.long": {"layer": "L2", "index": 1}})
    path.write_bytes(gzip.compress(json.dumps(case).encode("utf-8")))
    _write_outcome(tmp_path, c, [{"case_id": "cf_a", "asof_ms": R1, "path": rel}], 0.01)
    _write_path(tmp_path, _flat_path())
    s = _sample(sel.build(tmp_path, now_ms=E + H))
    ml = 2000.0 / 80000.0 - 0.003
    assert s["status"] == "ok" and s["verdict"] == "undetermined" and s["max_loss_basis"] == "case_l1"
    assert s["result_x"] == pytest.approx((0.003 - 0.01) / ml)
    assert s["expected_x"] == pytest.approx((0.003 - 0.0032) / ml)
    assert s["result_x"] == pytest.approx(s["expected_x"] + s["luck_x"])


# ---------------------------------------------------------------- scorecards run and isolation


def test_lookback_matches_the_scorecards():
    assert sel.ENTRY_LOOKBACK_MS == sc.ENTRY_LOOKBACK_MS


def test_run_writes_the_card_and_survives_its_failure(tmp_path, monkeypatch):
    _one(tmp_path)
    report = sc.run(tmp_path, now_ms=E + H)
    card = json.loads((tmp_path / "scorecards" / "selection.json").read_text(encoding="utf-8"))
    assert card["schema"] == "selection_scorecard@0.1" and card["derived"] is True and report["selection_samples"] == 4
    assert "可接受的全体一律接受" in (tmp_path / "scorecards" / "selection_summary.txt").read_text(encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(sc.selection, "build", boom)
    report = sc.run(tmp_path, now_ms=E + H)
    assert "selection_error" in report and (tmp_path / "scorecards" / "views.json").exists()
    assert not (tmp_path / "scorecards" / "selection.json").exists()        # never a stale card
    assert not (tmp_path / "scorecards" / "selection_summary.txt").exists()


def test_card_matches_its_schema(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    card = _one(tmp_path)
    jsonschema.validate(card, json.loads((SCHEMAS / "selection_scorecard_0.1.schema.json").read_text(encoding="utf-8")))


# ---------------------------------------------------------------- decision_event@0.2


def test_decision_rows_are_0_2_human_rows_with_actor_in_the_index(tmp_path):
    archive = ar.Archive(tmp_path)
    lock = _lock()
    lock["contract"]["expiry_ms"] = 1_792_483_200_000
    dp = [{"verification_id": "dpv_1", "event": "concluded", "at_ms": A_NOW - 10,
           "contract": {"side": "put", "short_instrument": "BTC-20OCT26-81500-P", "long_instrument": "BTC-20OCT26-79500-P"}}]
    ar.tick(archive, now_ms=A_NOW, view=_view(), fetch_case=_case, deribit=ar.Deribit(FakeDeribit()),
            decision_source=lambda: ([lock], dp))
    events = [json.loads(l) for f in tmp_path.glob("decisions/*.jsonl") for l in f.read_text(encoding="utf-8").splitlines()]
    assert {e["schema"] for e in events} == {"decision_event@0.2"}
    assert all(e["actor"] == "human" and e["round"] is None and e["case_id"] is None and e["action"] is None
               for e in events)
    assert {e["source"]: e["expiry_ms"] for e in events} == {"lock": 1_792_483_200_000, "decision_path": None}
    index = json.loads((tmp_path / "index/episodes/2026-10-20.json").read_text(encoding="utf-8"))
    assert {d["actor"] for d in index["episodes"][0]["decisions"]} == {"human"}
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((SCHEMAS / "decision_event_0.2.schema.json").read_text(encoding="utf-8"))
    for e in events:
        jsonschema.validate(e, schema)


def test_seen_decisions_are_never_trimmed(tmp_path):
    # the workbench hands over every past decision on each tick; a trimmed set rewrote the oldest ones
    archive = ar.Archive(tmp_path)
    state = archive.load_state()
    state["decisions_seen"] = [f"lock|old{i}|lock_created|{i}" for i in range(6000)]
    archive.save_state(state)
    ar.tick(archive, now_ms=A_NOW, view=_view(), fetch_case=_case, deribit=ar.Deribit(FakeDeribit()),
            decision_source=lambda: ([_lock()], []))
    seen = archive.load_state()["decisions_seen"]
    assert len(seen) == 6001 and seen[0] == "lock|old0|lock_created|0"
    r = ar.tick(archive, now_ms=A_NOW + 10 * M, view=_view(), fetch_case=_case, deribit=ar.Deribit(FakeDeribit()),
                decision_source=lambda: ([_lock()], []))
    assert r["decisions"] == 0


# ---------------------------------------------------------------- bad records (review 2026-11-11)


def test_two_cases_in_one_hour_take_the_earliest_freeze_inside_the_window(tmp_path):
    rel, c = _write_case(tmp_path, R1, "put", "cf_z", built=R1 + 5_000)           # sorts last by name
    _write_case(tmp_path, R1, "put", "cf_a", built=R1 + 50 * M, asof=R1 + 50 * M)  # late: outside the window
    _write_outcome(tmp_path, c, [{"case_id": "cf_z", "asof_ms": R1, "path": rel}], 0.0)
    _write_path(tmp_path, _flat_path())
    s = _sample(sel.build(tmp_path, now_ms=E + H))
    assert s["case_id"] == "cf_z" and s["status"] == "ok"


def test_bad_records_never_take_the_card_down(tmp_path):
    rel, c = _write_case(tmp_path, R1, "put", "cf_a")
    _write_case(tmp_path, R2, "put", "cf_b")
    (tmp_path / rel.replace("cf_a", "cf_broken")).write_bytes(b"not gzip")     # unreadable file in the same hour
    _write_outcome(tmp_path, c, [{"case_id": "cf_a", "asof_ms": R1, "path": rel}, "garbage"], 0.0)
    with (tmp_path / "outcomes" / "2026-11-12.jsonl").open("a", encoding="utf-8") as f:
        f.write("[1, 2]\n{cut")                                                    # non-object and cut lines
    # no paths file: path readings are null, the result still counts
    lock = {"source": "lock", "event": "lock_created", "at_ms": R1 + 10 * M, "contract_key": _key(c), "side": "put"}
    d = tmp_path / "decisions" / "2026-11-11.jsonl"
    d.parent.mkdir(parents=True)
    d.write_text(json.dumps(lock) + "\n" + json.dumps([1]) + "\n", encoding="utf-8")
    case = json.loads(gzip.decompress((tmp_path / rel).read_bytes()))
    case["build"]["built_at_ms"] = None      # the lock path still scores it (falls back to asof)
    card_path = tmp_path / rel
    card = sel.build(tmp_path, now_ms=E + H)
    s = _sample(card)
    assert s["status"] == "ok" and s["touched_short"] is None and s["result_x"] is not None
    assert card["human_locks"]["items"][0]["status"] == "ok"
    assert set(card["human_locks"]["items"][0]) >= {"case_age_min", "result_btc", "result_x"}
    card_path.write_bytes(gzip.compress(json.dumps(case).encode("utf-8")))
    card = sel.build(tmp_path, now_ms=E + H)
    assert _sample(card)["status"] == "outside_window" and card["human_locks"]["items"][0]["status"] == "ok"


def test_a_sample_that_raises_is_listed_as_unreadable(tmp_path, monkeypatch):
    _one(tmp_path)
    monkeypatch.setattr(sel, "round_sample", lambda *a, **k: 1 / 0)
    card = sel.build(tmp_path, now_ms=E + H)
    assert card["coverage"]["by_status"]["unreadable"] == 4 and all(g["n"] == 0 for g in card["groups"])


def test_empty_archive_builds_an_empty_card(tmp_path):
    card = sel.build(tmp_path, now_ms=E)
    assert card["coverage"]["samples"] == 0 and card["human_locks"]["n"] == 0
    assert "到期日 0 个" in sel.summary_text(card)
