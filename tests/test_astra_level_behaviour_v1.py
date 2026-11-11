"""Level cards, expiry reviews and their backfill (docs/opus/34 §3)."""
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest

import astra_level_behaviour_v1 as lb
import astra_research_archive_v1 as ar

H = 3_600_000
M = 60_000
E = 1_794_470_400_000                 # 2026-11-12 08:00 UTC
R2 = E - 18 * H
SCHEMAS = Path(__file__).resolve().parents[1] / "deploy" / "research_archive" / "schemas"
WALLS = {"expiry_ms": E, "expected_move_usd": 2000.0,
         "top": {"oi_put": [{"strike": 78000.0}, {"strike": 79000.0}, {"strike": 76000.0}],
                 "oi_call": [{"strike": 82000.0}, {"strike": 84000.0}, {"strike": 85000.0}],
                 "gamma_put": [{"strike": 80000.0}], "gamma_call": [{"strike": 81000.0}]},
         "walls": {"pin": 80500.0}}
CONTRACT = {"side": "put", "short_instrument": "BTC-12NOV26-79000-P", "long_instrument": "BTC-12NOV26-77000-P",
            "short_strike_usd": 79000.0, "long_strike_usd": 77000.0, "quantity_btc": 1.0, "expiry_ms": E}


def _path(start, end, price=80600.0, overrides=()):
    pts = [[t, price] for t in range(start, end + 1, M)]
    for lo, hi, p in overrides:
        pts = [[t, p if lo <= t <= hi else v] for t, v in pts]
    return pts


# ---------------------------------------------------------------- behaviour (ported)


def test_behaviour_crossing_and_the_hour_after():
    pts = [[i * M, 100.0] for i in range(1, 11)] + [[i * M, 110.0] for i in range(11, 31)] + [[31 * M, 95.0]]
    b = lb.behaviour(pts, 105.0, 0, 31 * M)
    assert b["crossings"] == 2 and b["first_cross_ms"] == 11 * M and b["first_cross_dir"] == "up"
    assert b["beyond_60m_usd"] == 5.0 and b["back_within_60m"] is True and b["end_beyond"] is False
    assert b["closest_usd"] == 5.0 and b["minutes_within_100usd"] == 31
    assert lb.behaviour(pts[:1], 105.0, 0, 31 * M) is None


# ---------------------------------------------------------------- levels and card fields


def test_roles_classes_and_controls():
    grid = [75000.0 + 500 * i for i in range(25)]
    roles = lb.levels_for(WALLS, CONTRACT, grid)
    assert roles[79000.0] == ["oi_put#2", "short"] and lb.role_class(roles[79000.0]) == "leg"
    assert lb.role_class(roles[80500.0]) == "derived" and lb.role_class(roles[78000.0]) == "oi"
    # controls: nearest listed strike either side of each leg / oi level that is neither
    assert 78500.0 in roles and "control" in roles[78500.0] and "control" in roles[79500.0]
    assert roles[80000.0] == ["gamma_put#1"]            # derived, not next to a leg or oi level: no control role
    assert roles[77500.0] == ["control"]                # between the long leg 77000 and put OI 78000
    key = {k for k, r in roles.items() if lb.role_class(r) in ("leg", "oi")}
    assert not any("control" in roles[k] for k in key)
    assert lb.levels_for(WALLS, CONTRACT, None).get(78500.0) is None              # no grid, no controls


def test_short_fields_strictly_between_both_sides():
    card = lb.build_card(expiry_ms=E, rnd="r2", side="put", read_at_ms=R2, walls=WALLS,
                         points=_path(R2 - 25 * H, R2, 80600.0), contract=CONTRACT)
    s = card["short"]
    assert s["is_oi_level"] is True                     # 79000 is put OI #2
    assert s["distance_usd"] == 1600.0 and s["distance_in_moves"] == 0.8
    assert s["oi_levels_between"] == 0 and s["nearest_oi_between"] is None
    call = {**CONTRACT, "side": "call", "short_strike_usd": 86000.0, "long_strike_usd": 88000.0,
            "short_instrument": "BTC-12NOV26-86000-C", "long_instrument": "BTC-12NOV26-88000-C"}
    c = lb.build_card(expiry_ms=E, rnd="r2", side="call", read_at_ms=R2, walls=WALLS,
                      points=_path(R2 - 25 * H, R2), contract=call)["short"]
    assert c["is_oi_level"] is False and c["oi_levels_between"] == 3 and c["nearest_oi_between"] == 82000.0


def test_card_refuses_data_after_its_reading():
    pts = _path(R2 - 25 * H, R2 + M)
    with pytest.raises(ValueError):
        lb.build_card(expiry_ms=E, rnd="r2", side="put", read_at_ms=R2, walls=WALLS, points=pts, contract=CONTRACT)
    with pytest.raises(ValueError):
        lb.build_card(expiry_ms=E, rnd="r2", side="put", read_at_ms=R2, walls=WALLS, points=_path(R2 - H, R2),
                      contract=CONTRACT, snapshots=[{"at_ms": R2 + 1, "strikes": []}])
    with pytest.raises(ValueError):
        lb.build_card(expiry_ms=E, rnd="r2", side="put", read_at_ms=R2, walls=WALLS, points=_path(R2 - H, R2),
                      contract=CONTRACT, flow_rows=[{"from_ms": R2 - 10 * M, "to_ms": R2 + 1, "rows": []}])
    with pytest.raises(ValueError):
        lb.build_card(expiry_ms=E, rnd="14utc", side="put", read_at_ms=R2, walls=WALLS, points=[], contract=CONTRACT)


def test_backfilled_card_lists_what_it_lacks():
    card = lb.build_card(expiry_ms=E, rnd="r2", side="put", read_at_ms=R2, walls=WALLS, points=_path(R2 - 10 * H, R2),
                         contract=CONTRACT, backfilled=True, extra_gaps=["before_collection"])
    assert {"strike_grid", "oi_change", "flow", "before_collection", "path_before_24h"} <= set(card["data_gaps"])
    assert all(l["before_24h"] is None and l["oi_change"] is None and l["flow"] is None for l in card["levels"])
    assert card["unknown"] == ["dealer_side", "liquidation_levels"] and card["backfilled"] is True
    assert not any(l["role_class"] == "control" for l in card["levels"])


def test_live_card_reads_oi_change_and_flow():
    snaps = [{"at_ms": R2 - 24 * H - 5 * M, "strikes": [{"k": 79000.0, "c_oi": 1.0, "p_oi": 10.0}]},
             {"at_ms": R2 - 6 * H - 5 * M, "strikes": [{"k": 79000.0, "c_oi": 1.0, "p_oi": 30.0}]},
             {"at_ms": R2 - 5 * M, "strikes": [{"k": 79000.0, "c_oi": 2.0, "p_oi": 45.0}]}]
    flow = [{"from_ms": R2 - 6 * H + i * 10 * M, "to_ms": R2 - 6 * H + (i + 1) * 10 * M, "truncated": False,
             "gap_from_ms": None, "rows": [{"k": 79000.0, "kind": "put", "buy_btc": 1.0, "sell_btc": 0.5, "n": 2}]}
            for i in range(36)]
    card = lb.build_card(expiry_ms=E, rnd="r2", side="put", read_at_ms=R2, walls=WALLS, points=_path(R2 - 25 * H, R2),
                         contract=CONTRACT, strikes=[78500.0, 79000.0, 79500.0], snapshots=snaps, flow_rows=flow)
    lv = next(l for l in card["levels"] if l["k"] == 79000.0)
    assert lv["oi"] == {"c": 2.0, "p": 45.0}
    assert lv["oi_change"]["6h"] == {"c": 1.0, "p": 15.0, "gap": False}
    assert lv["oi_change"]["24h"] == {"c": 1.0, "p": 35.0, "gap": False}
    assert lv["flow"]["6h"]["p_buy"] == 36.0 and lv["flow"]["6h"]["gap"] is False
    assert lv["flow"]["24h"]["gap"] is True              # no rows before 6 h: never padded
    assert "strike_grid" not in card["data_gaps"] and lv["before_24h"]["crossings"] == 0


# ---------------------------------------------------------------- review


def test_review_after_the_reading_only():
    before = _path(R2 - 25 * H, R2, overrides=[(R2 - 3 * H, R2 - 2 * H, 78000.0)])     # touched before: not counted
    card = lb.build_card(expiry_ms=E, rnd="r2", side="put", read_at_ms=R2, walls=WALLS, points=before, contract=CONTRACT)
    after = before + _path(R2 + M, E, overrides=[(E - 5 * H, E - 4 * H, 79150.0)])
    review = lb.build_review(card, after, delivery_price_usd=80600.0, payout_btc=0.0)
    assert review["short"]["touched"] is False and review["path_complete"] is True
    lv = next(l for l in review["levels"] if l["k"] == 79000.0)
    assert lv["after"]["crossings"] == 0 and lv["approach_no_cross"] is True          # 150 <= 10% of 2000
    assert review["settle_in_moves"] == 0.0 and review["range_in_moves"]["down"] == pytest.approx(0.725)
    gap = lb.build_review(card, before + _path(R2 + M, E - 2 * H), delivery_price_usd=80600.0)
    assert gap["path_complete"] is False and gap["short"]["touched"] is None and gap["levels"][0]["after"] is None


def test_card_and_review_match_their_schemas():
    jsonschema = pytest.importorskip("jsonschema")
    card = lb.build_card(expiry_ms=E, rnd="r2", side="put", read_at_ms=R2, walls=WALLS, points=_path(R2 - 25 * H, R2),
                         contract=CONTRACT, strikes=[78500.0, 79500.0])
    review = lb.build_review(card, _path(R2 - 25 * H, E), delivery_price_usd=80000.0, payout_btc=0.0)
    late = lb.build_card(expiry_ms=E, rnd="late4h", side="structure", read_at_ms=E - 4 * H, walls=WALLS,
                         points=_path(E - 30 * H, E - 4 * H))
    for doc, name in ((card, "level_card"), (late, "level_card"), (review, "level_review")):
        jsonschema.validate(doc, json.loads((SCHEMAS / f"{name}_0.1.schema.json").read_text(encoding="utf-8")))


# ---------------------------------------------------------------- backfill through the archive


def _seed(root, *, walls_expiry=E):
    case = {"case_id": "cf_r2", "asof_ms": R2 - 20_000, "build": {"built_at_ms": R2 + 10_000},
            "layers": {"L1": [{"ref": "L1.contract", "values": CONTRACT}],
                       "L3": [{"ref": "L3.expiry_walls", "values": {**WALLS, "expiry_ms": walls_expiry}}]},
            "refs": {"L1.contract": {"layer": "L1", "index": 0}, "L3.expiry_walls": {"layer": "L3", "index": 0}}}
    d = root / "cases" / "2026-11-11"
    d.mkdir(parents=True)
    (d / "14_put_cf_r2.json.gz").write_bytes(gzip.compress(json.dumps(case).encode("utf-8")))
    (root / "paths").mkdir()
    (root / "paths" / "2026-11-12.json.gz").write_bytes(gzip.compress(json.dumps({"points": _path(E - 47 * H, E)}).encode()))
    (root / "paths" / "2026-11-11.json.gz").write_bytes(gzip.compress(json.dumps({"points": _path(E - 71 * H, E - 23 * H)}).encode()))
    (root / "outcomes").mkdir()
    o = {"contract_key": f"{CONTRACT['short_instrument']}|{CONTRACT['long_instrument']}", "status": "settled",
         "expiry_ms": E, "delivery_price_usd": 80600.0, "payout_btc": 0.0, "side": "put", "cases": []}
    (root / "outcomes" / "2026-11-12.jsonl").write_text(json.dumps(o) + "\n", encoding="utf-8")


def test_backfill_writes_once_chains_and_marks_gaps(tmp_path):
    _seed(tmp_path)
    archive = ar.Archive(tmp_path)
    r = ar.backfill_levels(archive, now_ms=E + H)
    assert r["cards"] == 4 and r["reviews"] == 4 and r["errors"] == [] and r["no_case"] == 3   # r1 x2, r2 call
    rows = [json.loads(l) for l in (tmp_path / "levels" / "2026-11-12.jsonl").read_text(encoding="utf-8").splitlines()]
    card = next(x for x in rows if x["schema"] == "level_card@0.1" and x["round"] == "r2" and x["side"] == "put")
    assert card["case_id"] == "cf_r2" and card["read_at_ms"] == R2 + 10_000 and card["backfilled"] is True
    assert "path_before_24h" not in card["data_gaps"]                     # the previous day's path is joined
    review = next(x for x in rows if x["schema"] == "level_review@0.1" and x["round"] == "r2" and x["side"] == "put")
    assert review["short"]["payout_btc"] == 0.0 and review["delivery_price_usd"] == 80600.0
    assert ar.backfill_levels(archive, now_ms=E + H)["cards"] == 0       # written once
    assert archive.verify()["ok"] is True
    assert ar.observed_versions()["level_rules"] == "level_rules@0.1"


def test_backfill_ignores_a_case_of_another_expiry(tmp_path):
    _seed(tmp_path, walls_expiry=E + 24 * H)
    r = ar.backfill_levels(ar.Archive(tmp_path), now_ms=E + H)
    assert r["no_case"] == 4
    rows = [json.loads(l) for l in (tmp_path / "levels" / "2026-11-12.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(x["case_id"] is None for x in rows if x["schema"] == "level_card@0.1")


# ---------------------------------------------------------------- review 2026-11-11 (step 3)


def test_missing_values_stay_empty_not_false():
    card = lb.build_card(expiry_ms=E, rnd="r2", side="put", read_at_ms=R2, walls={"expiry_ms": E},
                         points=_path(R2 - 25 * H, R2), contract=CONTRACT)
    assert card["short"]["is_oi_level"] is None and card["short"]["oi_levels_between"] is None
    assert "expected_move" in card["data_gaps"]
    review = lb.build_review(card, _path(R2 - 25 * H, E), delivery_price_usd=80000.0)
    assert all(l["approach_no_cross"] is None for l in review["levels"])
    gap = lb.build_review(card, _path(R2 - 25 * H, E - 3 * H, 78000.0), delivery_price_usd=80000.0)
    assert gap["short"]["touched"] is None and gap["short"]["first_touch_ms"] is None


def test_future_check_handles_malformed_times():
    with pytest.raises(ValueError):
        lb.build_card(expiry_ms=E, rnd="r2", side="put", read_at_ms=R2, walls=WALLS, points=_path(R2 - H, R2),
                      contract=CONTRACT, snapshots=[{"at_ms": None}, {"at_ms": R2 + 1, "strikes": []}])
    card = lb.build_card(expiry_ms=E, rnd="r2", side="put", read_at_ms=R2, walls=WALLS, points=_path(R2 - H, R2),
                         contract=CONTRACT, snapshots=[{"at_ms": R2 - 2 * H, "strikes": []}],
                         flow_rows=[{"from_ms": None, "to_ms": None}])
    assert "oi_at_read" in card["data_gaps"]           # snapshots exist, none within 40 minutes of the reading


def test_backfill_uses_collected_data_cut_to_the_reading(tmp_path):
    _seed(tmp_path)
    snaps = [{"schema": "structure_snapshot@0.1", "at_ms": t, "expiry_ms": E,
              "strikes": [{"k": k, "c_oi": 1.0, "p_oi": 10.0 + (t > R2)} for k in (78500.0, 79000.0, 79500.0)]}
             for t in (R2 - 24 * H - 10 * M, R2 - 6 * H - 10 * M, R2 - 10 * M, R2 + H)]
    (tmp_path / "structure").mkdir()
    (tmp_path / "structure" / "2026-11-12.jsonl").write_text("".join(json.dumps(x) + "\n" for x in snaps), encoding="utf-8")
    ar.backfill_levels(ar.Archive(tmp_path), now_ms=E + H)
    rows = [json.loads(l) for l in (tmp_path / "levels" / "2026-11-12.jsonl").read_text(encoding="utf-8").splitlines()]
    card = next(x for x in rows if x["schema"] == "level_card@0.1" and x["round"] == "r2" and x["side"] == "put")
    assert "before_collection" not in card["data_gaps"] and "strike_grid" not in card["data_gaps"]
    lv = next(l for l in card["levels"] if l["k"] == 79000.0)
    assert lv["oi"] == {"c": 1.0, "p": 10.0}            # the snapshot after the reading is never used
    assert any(l["role_class"] == "control" for l in card["levels"])


def test_a_card_without_its_review_gets_the_review_on_the_next_run(tmp_path, monkeypatch):
    _seed(tmp_path)
    archive = ar.Archive(tmp_path)
    real = ar.level_behaviour.build_review
    monkeypatch.setattr(ar.level_behaviour, "build_review", lambda *a, **k: 1 / 0)
    r = ar.backfill_levels(archive, now_ms=E + H)
    assert r["cards"] == 4 and r["reviews"] == 0 and len(r["errors"]) == 4
    monkeypatch.setattr(ar.level_behaviour, "build_review", real)
    r = ar.backfill_levels(archive, now_ms=E + H)
    assert r["cards"] == 0 and r["reviews"] == 4 and r["errors"] == []
    assert ar.backfill_levels(archive, now_ms=E + H)["skipped"] == 4 and archive.verify()["ok"] is True


# ---------------------------------------------------------------- step 4: cards at their rounds, phenomena


def _live_seed(root):
    """Cases at the r2 hour, structure snapshots for E; no outcome yet."""
    _seed(root)
    (root / "outcomes" / "2026-11-12.jsonl").unlink()
    snaps = [{"schema": "structure_snapshot@0.1", "at_ms": t, "expiry_ms": E, "expected_move_usd": 1900.0,
              "profile": {"walls": {"pin": 80500.0}, "top": WALLS["top"]},
              "strikes": [{"k": k, "c_oi": 1.0, "p_oi": 10.0} for k in (78500.0, 79000.0, 79500.0)]}
             for t in range(E - 40 * H, E, 30 * M)]
    (root / "structure").mkdir()
    (root / "structure" / "2026-11-12.jsonl").write_text("".join(json.dumps(x) + "\n" for x in snaps), encoding="utf-8")


def _levels(root):
    path = root / "levels" / "2026-11-12.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_round_card_waits_for_its_case_then_reads_at_the_freeze(tmp_path):
    _live_seed(tmp_path)
    archive = ar.Archive(tmp_path)
    pts = lambda: _path(E - 47 * H, R2 + 45 * M)
    # r2 put has a case frozen at R2+10s: written at the first tick; r2 call has none yet: waits
    assert ar.write_level_cards(archive, now_ms=R2 + 10 * M, points_fn=pts) == 1
    put = next(c for c in _levels(tmp_path) if c["side"] == "put")
    assert put["round"] == "r2" and put["read_at_ms"] == R2 + 10_000 and put["case_id"] == "cf_r2"
    assert "strike_grid" not in put["data_gaps"] and any(l["role_class"] == "control" for l in put["levels"])
    # the tolerance runs out with no call case: a card without a contract, read now, walls from the snapshot
    assert ar.write_level_cards(archive, now_ms=R2 + 41 * M, points_fn=pts) == 1
    call = next(c for c in _levels(tmp_path) if c["side"] == "call")
    assert call["case_id"] is None and call["read_at_ms"] == R2 + 41 * M and "no_case" in call["data_gaps"]
    assert call["expected_move_usd"] == 1900.0 and any("oi_put#1" in l["roles"] for l in call["levels"])
    # once written, never again; after the hour, nothing
    assert ar.write_level_cards(archive, now_ms=R2 + 50 * M, points_fn=pts) == 0
    assert ar.write_level_cards(archive, now_ms=R2 + 2 * H, points_fn=pts) == 0
    assert archive.verify()["ok"] is True


def test_late_looks_and_their_reviews(tmp_path):
    _live_seed(tmp_path)
    archive = ar.Archive(tmp_path)
    assert ar.write_level_cards(archive, now_ms=E - 4 * H + 5 * M,
                                points_fn=lambda: _path(E - 47 * H, E - 4 * H + 5 * M)) == 1
    assert ar.write_level_cards(archive, now_ms=E - H + 5 * M, points_fn=lambda: _path(E - 47 * H, E - H + 5 * M)) == 1
    late = [c for c in _levels(tmp_path) if c["schema"] == "level_card@0.1"]
    assert {(c["round"], c["side"]) for c in late} == {("late4h", "structure"), ("late1h", "structure")}
    assert all(c["short"] is None and c["contract_key"] is None for c in late)
    o = {"contract_key": f"{CONTRACT['short_instrument']}|{CONTRACT['long_instrument']}", "status": "settled",
         "expiry_ms": E, "delivery_price_usd": 80600.0, "payout_btc": 0.0, "side": "put", "cases": []}
    (tmp_path / "outcomes" / "2026-11-12.jsonl").write_text(json.dumps(o) + "\n", encoding="utf-8")
    r = ar.backfill_levels(archive, now_ms=E + H, since_ms=E - 3 * 24 * H)
    assert r["errors"] == [] and r["reviews"] == 4 + 2          # the two rounds backfilled, the late looks reviewed
    reviews = {(x["round"], x["side"]) for x in _levels(tmp_path) if x["schema"] == "level_review@0.1"}
    assert ("late4h", "structure") in reviews and ("late1h", "structure") in reviews
    assert ar.backfill_levels(archive, now_ms=E + H, since_ms=E + 1)["reviews"] == 0   # outside since_ms


def test_tick_writes_level_cards_and_isolates_their_failure(tmp_path, monkeypatch):
    from test_astra_research_archive_v1 import FakeDeribit
    _live_seed(tmp_path)
    archive = ar.Archive(tmp_path)
    monkeypatch.setattr(ar, "collect_structure", lambda *a, **k: (0, []))
    monkeypatch.setattr(ar, "collect_flow", lambda *a, **k: 0)
    deribit = FakeDeribit(path=_path(E - 47 * H, R2 + 10 * M))
    r = ar.tick(archive, now_ms=R2 + 10 * M, view=None, fetch_case=None, deribit=ar.Deribit(deribit))
    assert r["levels"] == 1 and not [e for e in r["errors"] if e["step"].startswith("level")]
    monkeypatch.setattr(ar, "write_level_cards", lambda *a, **k: 1 / 0)
    r = ar.tick(archive, now_ms=R2 + 20 * M, view=None, fetch_case=None, deribit=ar.Deribit(deribit))
    assert any(e["step"] == "levels" for e in r["errors"]) and archive.verify()["ok"] is True


def _pair(side="put", index=80600.0, oi1=80000.0, after_oi1=None, short_before=None, short_after=None,
          oi_change=None, flow=None, controls=(), day=0):
    def lv(k, roles, cls, before=None, change=None, fl=None):
        return {"k": k, "roles": roles, "role_class": cls, "before_24h": before, "oi_change": change, "flow": fl}
    levels = [lv(oi1, [f"oi_{side}#1"], "oi", change={"24h": oi_change} if oi_change else None,
                 fl={"24h": flow} if flow else None),
              lv(79000.0, ["short"], "leg", before=short_before)] + [lv(k, ["control"], "control") for k, _ in controls]
    card = {"expiry_day": f"2026-11-{12 + day}", "expiry_ms": E + day * 24 * H, "round": "r2", "side": side, "index_usd": index, "expected_move_usd": 2000.0,
            "short": {"k": 79000.0}, "levels": levels, "backfilled": False}

    def rv(k, a):
        return {"k": k, "after": a, "approach_no_cross": bool(a) and a["crossings"] == 0 and a["closest_usd"] <= 200}
    review = {"delivery_price_usd": 80000.0,
              "levels": [rv(oi1, after_oi1), rv(79000.0, short_after)] + [rv(k, a) for k, a in controls]}
    return card, review


def test_phenomena_count_for_against_and_controls():
    quiet = {"crossings": 0, "closest_usd": 50.0}
    through = {"crossings": 1, "closest_usd": 0.0, "end_beyond": True, "back_within_60m": False, "beyond_60m_usd": 1500.0}
    pairs = [_pair(after_oi1=quiet, controls=[(79500.0, through), (80500.0, quiet)]),
             _pair(after_oi1=through, short_before={"crossings": 0}, short_after=through,
                   oi_change={"p": -5.0, "gap": False}, flow={"p_buy": 9.0, "p_sell": 1.0, "gap": False, "truncated": False},
                   day=1),
             _pair(oi1=82000.0, after_oi1=quiet, day=2)]                                 # inside the spot: P1 n/a
    body = lb.phenomena(pairs, now_ms=1)
    it = {i["id"]: i for i in body["items"]}
    assert (it["P1"]["applies"], it["P1"]["for"], it["P1"]["against"], it["P1"]["not_applicable"]) == (2, 1, 1, 1)
    assert it["P1"]["control"] == {"applies": 2, "for": 1, "against": 1, "not_applicable": 0, "missing": 0}
    assert (it["P3"]["applies"], it["P3"]["for"]) == (1, 1)                          # 1500 > 0.5 x 2000
    assert (it["P4"]["applies"], it["P4"]["against"]) == (1, 1) and it["P4"]["control"]["applies"] == 1
    assert (it["P5"]["applies"], it["P5"]["for"]) == (1, 1)                          # decreasing and crossed
    assert it["P6"]["groups"]["buy_heavy"] == {"n": 1, "crossed": 1} and it["P6"]["applies"] == 1
    assert body["expiry_days"] == 3 and "不下结论" in body["note_cn"]
    assert "P6" in lb.phenomena_text(body)


def test_scorecards_write_the_phenomena_and_survive_their_failure(tmp_path, monkeypatch):
    import astra_research_scorecards_v1 as sc
    _seed(tmp_path)
    ar.backfill_levels(ar.Archive(tmp_path), now_ms=E + H)
    report = sc.run(tmp_path, now_ms=E + H)
    body = json.loads((tmp_path / "scorecards" / "phenomena.json").read_text(encoding="utf-8"))
    assert report["phenomena_pairs"] == 4 and body["schema"] == "phenomena@0.1" and body["derived"] is True
    assert (tmp_path / "scorecards" / "phenomena_summary.txt").exists()
    monkeypatch.setattr(sc.level_behaviour, "phenomena", lambda *a, **k: 1 / 0)
    report = sc.run(tmp_path, now_ms=E + H)
    assert "phenomena_error" in report and not (tmp_path / "scorecards" / "phenomena.json").exists()
    assert (tmp_path / "scorecards" / "selection.json").exists()


def test_phenomena_p4_once_per_round_strike_and_legs_count_as_oi():
    through = {"crossings": 1, "closest_usd": 0.0, "end_beyond": True, "back_within_60m": True, "beyond_60m_usd": 10.0}
    put, review = _pair(after_oi1=through)
    put["levels"][1]["roles"] = ["oi_put#2", "short"]                # a leg that is also an OI level
    put["levels"][1]["role_class"] = "leg"
    review["levels"][1]["after"] = through
    call = {**put, "side": "call"}                                    # the call card lists the same levels
    it = {i["id"]: i for i in lb.phenomena([(put, review), (call, review)], now_ms=1)["items"]}
    assert it["P4"]["applies"] == 2 and it["P4"]["for"] == 2          # 80000 and 79000, once each


def test_phenomena_missing_is_not_not_applicable_and_truncated_flow_is_missing():
    p, r = _pair(after_oi1=None, flow={"p_buy": 9.0, "p_sell": 1.0, "gap": False, "truncated": True},
                 oi_change={"p": 0.0, "gap": False})
    it = {i["id"]: i for i in lb.phenomena([(p, r)], now_ms=1)["items"]}
    assert it["P1"]["missing"] == 1 and it["P1"]["not_applicable"] == 0
    assert it["P6"]["missing"] == 1 and it["P6"]["groups"]["buy_heavy"]["n"] == 0
    quiet = {"crossings": 0, "closest_usd": 50.0}
    p, r = _pair(after_oi1=quiet, oi_change={"p": 0.0, "gap": False},
                 flow={"p_buy": 1.0, "p_sell": 9.0, "gap": False, "truncated": False})
    it = {i["id"]: i for i in lb.phenomena([(p, r)], now_ms=1)["items"]}
    assert it["P5"]["not_applicable"] == 1 and it["P6"]["groups"]["sell_heavy"] == {"n": 1, "crossed": 0}


def test_phenomena_p2_pin_and_spot_control():
    walls = {**WALLS, "walls": {"pin": 80500.0}}
    pts = _path(E - 30 * H, E - 4 * H, 80600.0)
    late = lb.build_card(expiry_ms=E, rnd="late4h", side="structure", read_at_ms=E - 4 * H, walls=walls, points=pts,
                         strikes=[80000.0, 80500.0, 81000.0])
    assert "spot_control" in next(l for l in late["levels"] if l["k"] == 81000.0)["roles"]   # nearest non-pin strike
    assert not any("spot_control" in l["roles"] for l in late["levels"] if l["k"] != 81000.0)
    review = lb.build_review(late, pts + _path(E - 4 * H + M, E, 80550.0), delivery_price_usd=80550.0)
    it = {i["id"]: i for i in lb.phenomena([(late, review)], now_ms=1)["items"]}
    assert (it["P2"]["applies"], it["P2"]["for"]) == (1, 1)            # 50 from the pin, nearer than 100
    assert (it["P2"]["control"]["applies"], it["P2"]["control"]["against"]) == (1, 1)   # 450 from 81000, was 400


def test_write_level_cards_waits_for_the_index_and_isolates_a_bad_card(tmp_path, monkeypatch):
    _live_seed(tmp_path)
    archive = ar.Archive(tmp_path)
    assert ar.write_level_cards(archive, now_ms=R2 + 10 * M, points_fn=lambda: []) == 0     # failed fetch: wait
    assert _levels(tmp_path) == []
    errors = []
    real = ar.level_behaviour.build_card
    monkeypatch.setattr(ar.level_behaviour, "build_card",
                        lambda **k: (_ for _ in ()).throw(KeyError("x")) if k["side"] == "put" else real(**k))
    n = ar.write_level_cards(archive, now_ms=R2 + 41 * M, points_fn=lambda: _path(E - 47 * H, R2 + 45 * M), errors=errors)
    assert n == 1 and errors and errors[0]["side"] == "put"           # the call card is still written


def test_phenomena_matches_its_schema():
    jsonschema = pytest.importorskip("jsonschema")
    quiet = {"crossings": 0, "closest_usd": 50.0}
    body = lb.phenomena([_pair(after_oi1=quiet)], now_ms=1)
    jsonschema.validate(body, json.loads((SCHEMAS / "phenomena_0.1.schema.json").read_text(encoding="utf-8")))

