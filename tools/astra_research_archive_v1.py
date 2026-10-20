"""Research archive E0: frozen samples for the long-horizon researcher (docs/opus/08, 2026-10-20).

One tick (every 10 minutes from a systemd timer) does what is due:

- cases:    once per UTC hour per side, the displayed candidate's frozen case file
- inquiry:  at the scheduled hours (UTC 02, 14), the inquirer's output for that case
- ledger:   for every contract seen since it first appeared until expiry, one compact row
            every 30 minutes, plus a row on a material change, plus every tick in the
            last 3 hours before expiry
- outcomes: after expiry, the delivery price, the payout, each frozen case's result, and
            the 1-minute index path statistics from first sighting to expiry

Everything is append-only. Each written file is recorded in chain.jsonl with its sha256
and the previous entry's hash, so history can be verified and never silently rewritten.
A sample is one contract (two legs + expiry) from first sighting to expiry; contracts of
one expiry share the delivery price, so statistics should cluster by expiry day.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping

if __package__:
    from . import astra_case_file_v1 as case_file_core
    from . import astra_commercial_math_v17 as commercial_math
    from . import astra_inquiry_v1 as inquiry
    from . import astra_market_baseline_v1 as market_baseline
else:
    import astra_case_file_v1 as case_file_core
    import astra_commercial_math_v17 as commercial_math
    import astra_inquiry_v1 as inquiry
    import astra_market_baseline_v1 as market_baseline

SCHEMA_ROW = "ledger_row@0.1"
SCHEMA_OUTCOME = "episode_outcome@0.1"
SCHEMA_CHAIN = "archive_chain@0.1"
HOUR_MS = 3_600_000
BASE_INTERVAL_MS = 30 * 60_000
NEAR_EXPIRY_MS = 3 * HOUR_MS
DISTANCE_TRIGGER_PP = 0.25
INQUIRY_HOURS_UTC = (2, 14)
INQUIRY_WINDOW_MS = 45 * 60_000
OUTCOME_DELAY_MS = 10 * 60_000
OUTCOME_GIVE_UP_MS = 12 * HOUR_MS
PUBLIC = "https://www.deribit.com/api/v2/"
PUBLIC_PATHS = frozenset({"public/get_order_book", "public/get_delivery_prices", "public/get_index_chart_data"})


def _utc(ms: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


class Archive:
    """Append-only files under root, each recorded in a sha256 hash chain."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.state_path = self.root / "_state" / "state.json"
        self.chain_path = self.root / "chain.jsonl"

    def load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {"contracts": {}, "cases": {}, "inquiry": {}, "outcomes_done": []}

    def save_state(self, state: Mapping[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name("state.json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _last_chain_hash(self) -> str:
        if not self.chain_path.exists():
            return "0" * 64
        last = None
        with self.chain_path.open("rb") as handle:
            for line in handle:
                if line.strip():
                    last = line
        return json.loads(last)["entry_sha256"] if last else "0" * 64

    def _chain(self, rel: str, content_sha: str, at_ms: int, kind: str) -> None:
        prev = self._last_chain_hash()
        entry = {"schema": SCHEMA_CHAIN, "at_ms": at_ms, "kind": kind, "path": rel,
                 "content_sha256": content_sha, "prev_sha256": prev}
        entry["entry_sha256"] = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()
        self.chain_path.parent.mkdir(parents=True, exist_ok=True)
        with self.chain_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")

    def write_new(self, rel: str, data: bytes, *, at_ms: int, kind: str) -> str:
        """Write a new file; an existing path is never overwritten."""
        path = self.root / rel
        if path.exists():
            raise FileExistsError(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        os.chmod(path, 0o640)
        sha = hashlib.sha256(data).hexdigest()
        self._chain(rel, sha, at_ms, kind)
        return sha

    def append_line(self, rel: str, row: Mapping[str, Any], *, at_ms: int, kind: str) -> str:
        line = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with path.open("ab") as handle:
            handle.write(line)
        if new:
            os.chmod(path, 0o640)
        sha = hashlib.sha256(line).hexdigest()
        self._chain(rel, sha, at_ms, kind)
        return sha

    def verify(self) -> dict[str, Any]:
        """Re-walk the chain: entry hashes link, files written once still match."""
        prev, count, bad = "0" * 64, 0, []
        if not self.chain_path.exists():
            return {"ok": True, "entries": 0, "problems": []}
        for line in self.chain_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            body = {k: v for k, v in entry.items() if k != "entry_sha256"}
            if entry.get("prev_sha256") != prev:
                bad.append({"path": entry.get("path"), "problem": "broken_link"})
            if hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() != entry.get("entry_sha256"):
                bad.append({"path": entry.get("path"), "problem": "entry_hash"})
            if entry.get("kind") in {"case", "inquiry"}:
                path = self.root / entry["path"]
                if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["content_sha256"]:
                    bad.append({"path": entry["path"], "problem": "content_changed_or_missing"})
            prev = entry.get("entry_sha256")
            count += 1
        return {"ok": not bad, "entries": count, "problems": bad}


def contract_key(contract: Mapping[str, Any]) -> str:
    return f"{contract.get('short_instrument')}|{contract.get('long_instrument')}"


def _program_states(view: Mapping[str, Any]) -> dict[str, Any]:
    facts = ((view.get("market_context") or {}).get("market_facts")) or {}
    gex = ((facts.get("space_context") or {}).get("gex_info")) or {}
    vp = facts.get("volume_price") or {}
    gate = view.get("space_gate") or {}
    return {"space_gate": gate.get("producer_state") or gate.get("state"),
            "m_die_level": (facts.get("m_die") or {}).get("level"),
            "gamma_regime": (facts.get("gamma_regime") or {}).get("regime"),
            "gex_market_state": gex.get("market_state"), "flip_usd": _num(gex.get("flip_point")),
            "tmv_24h": (vp.get("tmvf_24h") or {}).get("final_state"),
            "tmv_48h": (vp.get("tmvf_48h") or {}).get("final_state"),
            "micro_flow": vp.get("micro_flow_effect") or (facts.get("flow") or {}).get("micro_flow_effect")}


def ledger_row(record: Mapping[str, Any], short_book: Mapping[str, Any], long_book: Mapping[str, Any],
               states: Mapping[str, Any], *, now_ms: int, displayed: bool) -> dict[str, Any]:
    side, ks, kl = record["side"], float(record["short_strike_usd"]), float(record["long_strike_usd"])
    index = _num(short_book.get("index_price")) or _num(long_book.get("index_price"))
    forward = _num(short_book.get("underlying_price")) or _num(long_book.get("underlying_price"))
    bid, ask = _num(short_book.get("best_bid_price")), _num(long_book.get("best_ask_price"))
    credit = bid - ask if bid is not None and ask is not None else None
    sm, lm = _num(short_book.get("mark_price")), _num(long_book.get("mark_price"))
    base = market_baseline.baseline(
        side=side, short_strike=ks, long_strike=kl, forward_usd=forward, valuation_ms=now_ms,
        expiry_ms=int(record["expiry_ms"]), short_iv_pct=short_book.get("mark_iv"), long_iv_pct=long_book.get("mark_iv"),
        short_delta=(short_book.get("greeks") or {}).get("delta"), long_delta=(long_book.get("greeks") or {}).get("delta"),
        short_mark_btc=sm, long_mark_btc=lm, net_credit_btc=credit if credit and credit > 0 else None,
        width_usd=abs(ks - kl))
    probs = base.get("probabilities") or {}
    boundaries = probs.get("loss_boundaries_usd") or []
    pct = (lambda k: (k / index - 1.0) * 100.0 if index and k else None)
    leg = lambda b: {k: b.get(k) for k in ("best_bid_price", "best_ask_price", "mark_price", "mark_iv")} | {
        "delta": (b.get("greeks") or {}).get("delta"), "timestamp": b.get("timestamp")}
    return {
        "schema": SCHEMA_ROW, "at_ms": now_ms, "contract_key": record["key"], "side": side,
        "expiry_ms": record["expiry_ms"], "displayed": displayed,
        "short": leg(short_book), "long": leg(long_book),
        "credit_bid_ask_btc": credit, "mark_spread_btc": (sm - lm) if sm is not None and lm is not None else None,
        "index_usd": index, "forward_usd": forward,
        "short_from_index_pct": pct(ks), "long_from_index_pct": pct(kl),
        "breakeven_from_index_pct": pct(boundaries[0]) if boundaries else None,
        "baseline_status": base.get("status"),
        "p_itm_short": probs.get("p_itm_short"), "p_touch_short": probs.get("p_touch_short"),
        "p_loss": probs.get("p_loss"), "p_max_loss": probs.get("p_max_loss"),
        "states": dict(states),
    }


def _crossed(prev: float | None, cur: float | None) -> bool:
    return prev is not None and cur is not None and (prev > 0) != (cur > 0)


def row_trigger(last: Mapping[str, Any] | None, row: Mapping[str, Any], *, now_ms: int) -> str | None:
    if last is None:
        return "first"
    if int(row["expiry_ms"]) - now_ms <= NEAR_EXPIRY_MS:
        return "near_expiry"
    if now_ms - int(last["at_ms"]) >= BASE_INTERVAL_MS:
        return "base"
    for key in ("short_from_index_pct", "breakeven_from_index_pct", "long_from_index_pct"):
        if _crossed(last.get(key), row.get(key)):
            return "change"
    a, b = last.get("short_from_index_pct"), row.get("short_from_index_pct")
    if a is not None and b is not None and abs(a - b) > DISTANCE_TRIGGER_PP:
        return "change"
    if last.get("states") != row.get("states") or last.get("displayed") != row.get("displayed"):
        return "change"
    return None


def path_stats(record: Mapping[str, Any], points: list[list[float]], delivery: float) -> dict[str, Any]:
    """1-minute index from first sighting to expiry: touch, closest approach, adverse excursion, RV."""
    start, end = int(record["first_seen_ms"]), int(record["expiry_ms"])
    series = [(int(t), float(p)) for t, p in points if start <= int(t) <= end and p]
    if len(series) < 2:
        return {"status": "insufficient_path", "points": len(series)}
    ks, kl, side = float(record["short_strike_usd"]), float(record["long_strike_usd"]), record["side"]
    prices = [p for _, p in series]
    adverse = (lambda p: (ks - p) / ks * 100.0) if side == "put" else (lambda p: (p - ks) / ks * 100.0)
    touched = [t for t, p in series if (p <= ks if side == "put" else p >= ks)]
    rets = [math.log(b / a) for a, b in zip(prices, prices[1:]) if a > 0 and b > 0]
    minutes = max(1, len(rets))
    rv = math.sqrt(sum(r * r for r in rets) / minutes * 525_600) if rets else None
    return {"status": "ok", "points": len(series), "start_ms": series[0][0], "end_ms": series[-1][0],
            "min_usd": min(prices), "max_usd": max(prices),
            "touched_short": bool(touched), "first_touch_ms": touched[0] if touched else None,
            "minutes_beyond_short": len(touched),
            "touched_long": any((p <= kl if side == "put" else p >= kl) for p in prices),
            "max_adverse_vs_short_pct": max(adverse(p) for p in prices),
            "settle_vs_short_pct": adverse(delivery), "realized_vol_annual": rv}


class Deribit:
    def __init__(self, transport: Callable[[str, dict[str, Any]], Any] | None = None) -> None:
        self.transport = transport or self._get

    @staticmethod
    def _get(path: str, params: dict[str, Any]) -> Any:
        if path not in PUBLIC_PATHS:
            raise ValueError("only audited public read endpoints")
        url = PUBLIC + path + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as response:
            body = json.load(response)
        if not isinstance(body, dict) or body.get("error") or "result" not in body:
            raise ValueError("deribit public " + path + " invalid result")
        return body["result"]

    def book(self, name: str) -> dict[str, Any]:
        book = self.transport("public/get_order_book", {"instrument_name": name, "depth": 1})
        if not isinstance(book, dict) or book.get("instrument_name") != name:
            raise ValueError("book mismatch " + name)
        return book

    def delivery(self, expiry_ms: int) -> float | None:
        day = _utc(expiry_ms).strftime("%Y-%m-%d")
        for row in (self.transport("public/get_delivery_prices", {"index_name": "btc_usd", "count": 10}) or {}).get("data") or []:
            if row.get("date") == day and _num(row.get("delivery_price")):
                return float(row["delivery_price"])
        return None

    def index_path(self) -> list[list[float]]:
        return list(self.transport("public/get_index_chart_data", {"index_name": "btc_usd", "range": "2d"}) or [])


def tick(archive: Archive, *, now_ms: int, view: Mapping[str, Any] | None,
         fetch_case: Callable[[str], Mapping[str, Any]], deribit: Deribit,
         run_inquiry: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None) -> dict[str, Any]:
    state = archive.load_state()
    report: dict[str, Any] = {"cases": 0, "inquiry": 0, "rows": 0, "outcomes": 0, "errors": []}
    displayed: dict[str, dict[str, Any]] = {}
    for item in (view or {}).get("candidates") or []:
        snap = item.get("snapshot") if isinstance(item, Mapping) else None
        contract = (snap or {}).get("contract") or {}
        if not contract.get("short_instrument"):
            continue
        key = contract_key(contract)
        displayed[key] = item
        state["contracts"].setdefault(key, {
            "key": key, "side": contract.get("side"), "short_instrument": contract.get("short_instrument"),
            "long_instrument": contract.get("long_instrument"), "short_strike_usd": contract.get("short_strike_usd"),
            "long_strike_usd": contract.get("long_strike_usd"), "expiry_ms": contract.get("expiry_ms"),
            "first_seen_ms": now_ms, "cases": [], "last_row": None})

    hour = _utc(now_ms).strftime("%Y-%m-%dT%H")
    day = hour[:10]
    for key, item in displayed.items():
        side, snap = item.get("side"), item["snapshot"]
        slot_key = f"{hour}|{side}"
        if slot_key in state["cases"]:
            continue
        try:
            case = fetch_case(snap["snapshot_id"])
            data = gzip.compress(json.dumps(case, ensure_ascii=False, sort_keys=True).encode("utf-8"), mtime=0)
            rel = f"cases/{day}/{hour[11:]}_{side}_{case['case_id']}.json.gz"
            archive.write_new(rel, data, at_ms=now_ms, kind="case")
            credit = ((case_file_core_entry(case, "L1.economics") or {}).get("net_credit_after_fee_btc"))
            state["cases"][slot_key] = {"case_id": case["case_id"], "path": rel, "contract_key": key}
            state["contracts"][key]["cases"].append({"case_id": case["case_id"], "asof_ms": case.get("asof_ms"),
                                                     "net_credit_after_fee_btc": credit, "path": rel})
            report["cases"] += 1
        except Exception as exc:
            report["errors"].append({"step": "case", "side": side, "error": type(exc).__name__, "detail": str(exc)[:160]})
            continue
        ikey = f"{hour}|{side}"
        if (run_inquiry and _utc(now_ms).hour in INQUIRY_HOURS_UTC and now_ms % HOUR_MS < INQUIRY_WINDOW_MS
                and ikey not in state["inquiry"]):
            # recorded before the call: a slow or failed run is never repeated within the slot
            state["inquiry"][ikey] = "running"
            archive.save_state(state)
            try:
                result = dict(run_inquiry(case_file_core.for_inquirer(case)))
            except Exception as exc:
                result = {"status": "ERROR", "error": type(exc).__name__}
            irel = f"inquiry/{day}/{hour[11:]}_{side}_{case['case_id']}.json"
            archive.write_new(irel, json.dumps({"case_id": case["case_id"], "asof_ms": case.get("asof_ms"), **result},
                                               ensure_ascii=False, sort_keys=True).encode("utf-8"),
                              at_ms=now_ms, kind="inquiry")
            state["inquiry"][ikey] = result.get("status")
            report["inquiry"] += 1

    states = _program_states(view or {})
    for key, record in list(state["contracts"].items()):
        if int(record["expiry_ms"]) <= now_ms:
            continue
        try:
            row = ledger_row(record, deribit.book(record["short_instrument"]), deribit.book(record["long_instrument"]),
                             states, now_ms=now_ms, displayed=key in displayed)
        except Exception as exc:
            report["errors"].append({"step": "ledger", "contract": key, "error": type(exc).__name__})
            continue
        trigger = row_trigger(record.get("last_row"), row, now_ms=now_ms)
        if trigger:
            row["trigger"] = trigger
            archive.append_line(f"ledger/{_utc(now_ms).strftime('%Y-%m-%d')}.jsonl", row, at_ms=now_ms, kind="ledger")
            record["last_row"] = {k: row.get(k) for k in ("at_ms", "expiry_ms", "short_from_index_pct",
                                                          "breakeven_from_index_pct", "long_from_index_pct",
                                                          "states", "displayed")}
            report["rows"] += 1

    path_cache: list[list[float]] | None = None
    delivery_cache: dict[int, float | None] = {}
    for key, record in list(state["contracts"].items()):
        expiry = int(record["expiry_ms"])
        if now_ms < expiry + OUTCOME_DELAY_MS:
            continue
        try:
            if expiry not in delivery_cache:
                delivery_cache[expiry] = deribit.delivery(expiry)
            delivery = delivery_cache[expiry]
            if delivery is None:
                if now_ms > expiry + OUTCOME_GIVE_UP_MS:
                    archive.append_line(f"outcomes/{_utc(expiry).strftime('%Y-%m-%d')}.jsonl",
                                        {"schema": SCHEMA_OUTCOME, "contract_key": key, "status": "delivery_unavailable",
                                         "expiry_ms": expiry}, at_ms=now_ms, kind="outcome")
                    state["outcomes_done"].append(key)
                    del state["contracts"][key]
                continue
            if path_cache is None:
                path_cache = deribit.index_path()
            payout = commercial_math.inverse_spread_payout(
                record["side"], float(record["short_strike_usd"]), float(record["long_strike_usd"]), delivery, 1.0)
            cases = [{**c, "result_btc": (c["net_credit_after_fee_btc"] - payout)
                      if _num(c.get("net_credit_after_fee_btc")) is not None else None} for c in record["cases"]]
            outcome = {"schema": SCHEMA_OUTCOME, "contract_key": key, "status": "settled", "side": record["side"],
                       "short_strike_usd": record["short_strike_usd"], "long_strike_usd": record["long_strike_usd"],
                       "expiry_ms": expiry, "first_seen_ms": record["first_seen_ms"],
                       "delivery_price_usd": delivery, "payout_btc": payout, "cases": cases,
                       "path": path_stats(record, path_cache, delivery),
                       "meaning_cn": "到期静态结果：各冻结点扣费后净信用 − 到期赔付（币本位）；不是实际成交"}
            archive.append_line(f"outcomes/{_utc(expiry).strftime('%Y-%m-%d')}.jsonl", outcome,
                                at_ms=now_ms, kind="outcome")
            state["outcomes_done"].append(key)
            del state["contracts"][key]
            report["outcomes"] += 1
        except Exception as exc:
            report["errors"].append({"step": "outcome", "contract": key, "error": type(exc).__name__})
    state["outcomes_done"] = state["outcomes_done"][-2000:]
    for name in ("cases", "inquiry"):   # slot keys sort by hour; keep the recent ones
        state[name] = dict(sorted(state[name].items())[-500:])
    state["last_tick_ms"] = now_ms
    archive.save_state(state)
    return report


def case_file_core_entry(case: Mapping[str, Any], ref: str) -> Any:
    at = (case.get("refs") or {}).get(ref)
    if not at:
        return None
    return case["layers"][at["layer"]][at["index"]].get("values")


def _http_json(url: str, token: str | None = None, timeout: int = 180) -> Any:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description="research archive tick")
    parser.add_argument("command", choices=["tick", "verify"])
    parser.add_argument("--root", default=os.environ.get("ASTRA_RESEARCH_ARCHIVE", "/var/lib/xxproject/research_archive"))
    parser.add_argument("--workbench", default="http://127.0.0.1:8895")
    args = parser.parse_args()
    archive = Archive(args.root)
    if args.command == "verify":
        print(json.dumps(archive.verify(), ensure_ascii=False))
        return
    token = os.environ.get("ASTRA_WORKBENCH_TOKEN")
    key = os.environ.get("LLM_API_KEY") or os.environ.get("ASTRA_WORKBENCH_LLM_API_KEY")
    model = os.environ.get("ASTRA_WORKBENCH_LLM_MODEL") or "deepseek-flash"
    lock = archive.root / "_state" / "tick.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if time.time() - lock.stat().st_mtime < 3600:
            print(json.dumps({"skipped": "previous_tick_running"}))
            return
        lock.unlink()
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.close(fd)
        try:
            view = _http_json(args.workbench + "/api/workbench")
        except Exception as exc:
            view = None
            print(json.dumps({"view_error": type(exc).__name__}))
        report = tick(archive, now_ms=int(time.time() * 1000), view=view,
                      fetch_case=lambda sid: _http_json(args.workbench + "/api/workbench/case-file/" + sid, token),
                      deribit=Deribit(),
                      run_inquiry=(lambda case: inquiry.run(case, api_key=key, model=model)) if key else None)
        print(json.dumps(report, ensure_ascii=False))
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
