import gzip
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest

import astra_research_archive_v1 as ar
import astra_research_validation_v1 as rv

H = 3_600_000
DAY = 24 * H
START = 1_792_483_200_000          # 2026-10-20 08:00 UTC, an expiry; START - 20 h is a 4-hour checkpoint
COND = {"field": "ledger.states.gamma_regime", "op": "==", "value": "NEGATIVE"}
REG = START - 10 * DAY


def _hyp(id_="h-neg-gamma", received=REG, metric="touched_short_after_entry", direction="riskier", **extra):
    return {"submission_id": "hypothesis:" + id_, "received_at_ms": received,
            "document": {"id": id_, "condition": COND, "metric": metric, "expected_direction": direction, **extra}}


def _row(key, at, expiry, regime, displayed=True, side="put"):
    return {"contract_key": key, "at_ms": at, "expiry_ms": expiry, "displayed": displayed, "side": side,
            "states": {"gamma_regime": regime} if regime else {}, "credit_bid_ask_btc": 0.0015,
            "p_touch_short": 0.5, "p_loss": 0.3}


def test_assignment_is_decided_at_each_checkpoint_without_look_ahead():
    cp = START - 20 * H
    rows = [_row("a", cp - 10 * 60_000, START, "POSITIVE"),        # control at cp
            _row("a", cp + 4 * H - 5 * 60_000, START, "NEGATIVE"),  # condition at cp + 4h (later, not retroactive)
            _row("b", cp - 45 * 60_000, START, "NEGATIVE"),        # 45 min old at cp: not sampled
            _row("c", cp - 10 * 60_000, START, "NEGATIVE", displayed=False),
            _row("d", cp - 10 * 60_000, START, None),               # undecidable
            _row("old", REG - 60_000, START, "NEGATIVE"), _row("old", cp - 60_000, START, "NEGATIVE")]
    samples = rv.assign({"condition": COND}, rows, since_ms=REG, until_ms=START)
    got = {(s["contract_key"], s["checkpoint_ms"]): s["group"] for s in samples}
    assert got[("a", cp)] == "control" and got[("a", cp + 4 * H)] == "condition"
    assert not any(k[0] in {"b", "c", "d", "old"} for k in got if k[1] == cp)
    assert not any(k[0] == "old" for k in got)          # first seen before registration


def test_clustered_se_matches_the_binary_regressor_formula():
    values = [(1.0, 1, "d1"), (0.0, 1, "d2"), (0.0, 0, "d1"), (0.0, 0, "d2"), (1.0, 0, "d3")]
    res = rv.clustered_difference(values)
    m1, m0, n1, n0 = 0.5, 1 / 3, 2, 3
    s = {}
    for y, g, c in values:
        s.setdefault(c, [0.0, 0.0])[g] += y - (m1 if g else m0)
    var = (3 / 2) * (4 / 3) * sum((v[1] / n1 - v[0] / n0) ** 2 for v in s.values())
    assert res["effect"] == pytest.approx(m1 - m0) and res["se"] == pytest.approx(var ** 0.5)


def _archive(tmp_path, days, cond_prob, seed=7, same_outcomes=False):
    """Per expiry day: one condition put and two control calls sampled at the 12:00 checkpoint."""
    rnd = random.Random(seed)
    root = tmp_path / "archive"
    for sub in ("ledger", "outcomes", "paths"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    for d in range(days):
        expiry = START + d * DAY
        entry = expiry - 20 * H - 10 * 60_000
        day = rv._utc(expiry).strftime("%Y-%m-%d")
        put_touch = rnd.random() < cond_prob
        call_touch = put_touch if same_outcomes else rnd.random() < 0.5
        prices = {t: 82000.0 for t in range(entry - H, expiry + 1, H)}
        if put_touch:
            prices[entry + 2 * H] = 79900.0
        if call_touch:
            prices[entry + 3 * H] = 84100.0
        rows, outcomes = [], []
        for j, (regime, side, ks, kl) in enumerate([("NEGATIVE", "put", 80000.0, 78000.0),
                                                     ("POSITIVE", "call", 84000.0, 86000.0),
                                                     ("POSITIVE", "call", 84000.0, 86000.0)]):
            key = f"BTC-{d}-{j}"
            rows.append(_row(key, entry, expiry, regime, side=side))
            outcomes.append({"contract_key": key, "status": "settled", "side": side, "short_strike_usd": ks,
                             "long_strike_usd": kl, "expiry_ms": expiry, "delivery_price_usd": 82000.0})
        with (root / "ledger" / f"{rv._utc(entry).strftime('%Y-%m-%d')}.jsonl").open("a", encoding="utf-8") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows))
        (root / "outcomes" / f"{day}.jsonl").write_text("".join(json.dumps(o) + "\n" for o in outcomes), encoding="utf-8")
        (root / "paths" / f"{day}.json.gz").write_bytes(
            gzip.compress(json.dumps({"points": sorted([t, p] for t, p in prices.items())}).encode()))
    return root


def _run_days(data, hyp, upto_days, k=1):
    """Evaluate daily so milestones are recorded as the sample grows."""
    prior = None
    for d in range(1, upto_days + 1):
        prior = rv.evaluate(hyp, {**data, "outcomes": {k2: o for k2, o in data["outcomes"].items()
                                                     if o["expiry_ms"] <= START + (d - 1) * DAY}},
                            now_ms=START + d * DAY, superseded=set(), k=k, prior=prior)
    return prior


def test_supported_needs_two_milestones_and_is_final_at_120(tmp_path):
    data = rv.load_archive(_archive(tmp_path, 125, 0.95), REG)
    at_70 = _run_days(data, _hyp(), 70)
    assert at_70["status"] == "observing" and at_70["milestones"]["60"]["pass"] is True
    at_125 = _run_days(data, _hyp(), 125)
    assert at_125["status"] == "supported" and set(at_125["milestones"]) == {"60", "90", "120"}
    against = _run_days(data, _hyp(direction="safer"), 65)
    assert against["status"] == "rejected"          # significant the other way at 60


def test_no_effect_is_weakened_then_rejected_at_120(tmp_path):
    data = rv.load_archive(_archive(tmp_path, 125, 0.5, same_outcomes=True), REG)
    assert _run_days(data, _hyp(), 95)["status"] == "weakened"
    assert _run_days(data, _hyp(), 125)["status"] == "rejected"


def test_observing_before_60_days_and_superseded_keeps_statistics(tmp_path):
    data = rv.load_archive(_archive(tmp_path, 20, 0.95), REG)
    res = rv.evaluate(_hyp(), data, now_ms=START + 30 * DAY, superseded=set(), k=1)
    assert res["status"] == "observing" and res["days_condition"] == 20
    sup = rv.evaluate(_hyp(), data, now_ms=START + 30 * DAY, superseded={"h-neg-gamma"}, k=1)
    assert sup["status"] == "superseded" and sup["status_basis"] == "observing" and sup["effect"] is not None


def test_truncated_path_excludes_path_metrics():
    outcome = {"side": "put", "short_strike_usd": 80000.0, "long_strike_usd": 78000.0, "expiry_ms": 10 * H,
               "delivery_price_usd": 82000.0}
    entry = {"at_ms": 0, "credit_bid_ask_btc": 0.0015}
    gap_start = [[5 * H + i * 60_000, 82000.0] for i in range(301)]          # starts 5 h after entry
    metrics = rv.sample_metrics(entry, outcome, gap_start)
    assert metrics["touched_short_after_entry"] is None and metrics["max_adverse_after_entry_pct"] is None
    full = [[i * 60_000, 82000.0] for i in range(601)]
    assert rv.sample_metrics(entry, outcome, full)["touched_short_after_entry"] == 0.0


def test_threshold_grows_with_registered_hypotheses():
    assert rv.threshold(1) == pytest.approx(2.0)
    assert rv.threshold(10) == pytest.approx(2.807, abs=0.01)


def test_run_records_milestones_across_runs_and_chains(tmp_path):
    root = _archive(tmp_path, 3, 0.9)
    archive = ar.Archive(root)
    (root / "registry").mkdir()
    (root / "registry" / "hypotheses.jsonl").write_text(json.dumps(_hyp()) + "\n", encoding="utf-8")
    assert rv.run(archive, now_ms=START + 5 * DAY) == {"evaluated": 1}
    status = json.loads((root / "validation" / "status.json").read_text(encoding="utf-8"))
    assert status["hypotheses"][0]["status"] == "observing" and status["scoring_rules"] == "scoring_rules@0.2"
    assert archive.verify()["ok"] is True
