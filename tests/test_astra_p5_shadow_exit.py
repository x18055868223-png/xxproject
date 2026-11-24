"""Adversarial regression suite for P5 terminal shadow exit & frozen exit valuation.

Covers all 6 Section 5 scenarios against Worker 1's shared backend contract and
Node (`--input-type=module`) UI checks for `locks.js`, `player-audit.js`, and `current-hand.js`:
1. Accept -> M_TRIGGERED -> valid AI_REVOKE -> SHADOW_EXITED stops 5m/1m monitoring while
   a second concurrent episode continues normally.
2. Non-terminal paths (pending/inflight trigger, HOLD, timeout/PARSE_FAILED,
   DISCRETIONARY_REVOKE_UNVERIFIED) do NOT enter SHADOW_EXITED and keep monitoring.
3. Three-tier valuation correctness & immutability (Put & Call EXECUTABLE_TWO_LEG_QUOTE
   with non-unit quantity & capped fees + market crash immutability; INDICATIVE_MARK_ONLY;
   UNPRICED with strictly non-hindsight pre-revoke last_indicative_reference).
4. Historical screenshot-like replay (177 CONDITION_TRIGGERED + AI_REVOKE + stale short-only
   revoke_quote.json + post-revoke -0.0222 BTC series rows) reconciled idempotently without
   mutating original evidence/event/review/series files.
5. Idempotency, crash recovery after decision.json before SHADOW_EXITED/service('stop'),
   and post-expiry counterfactual settlement isolation.
6. Real-vs-shadow boundary (execution_allowed=True never exits/stops), P5 (@1.2+p0+p2) /
   Formal A (@1.0) / daily_r16@2/@3/@4 compatibility, and Node UI end-to-end assertions.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from test_astra_locked_followup import setup
from test_astra_p5_candidate import _build_valid_p5_reply
from test_astra_player_e2e_v13 import _mock_initial_llm, _plan_r16, _seed_r16_archive
from test_astra_player_tracking_v13 import EXCERPTS, RULE, post, serving
from test_astra_workbench_v20 import native_gate, option_meta, write_fmz
from tools import astra_player_dossier_v1 as dossier
from tools import astra_player_review_v1 as review
from tools import astra_player_tracking_v13 as tracking
from tools import astra_underwriting_discovery_v20 as discovery
from tools import astra_underwriting_public_quote_v20 as public_quote
from tools import astra_workbench_v20 as workbench


ROOT = Path(__file__).resolve().parents[1]
M = dossier.MINUTE
DAY = dossier.DAY
HOUR = 3_600_000

LOCKS_JS = (ROOT / "deploy" / "astra_workbench_v3" / "locks.js").as_uri()
PLAYER_AUDIT_JS = (ROOT / "deploy" / "astra_workbench_v3" / "player-audit.js").as_uri()
CURRENT_HAND_JS = (ROOT / "deploy" / "astra_workbench_v3" / "current-hand.js").as_uri()
API_JS = (ROOT / "deploy" / "astra_workbench_v3" / "api.js").as_uri()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot_tree_hashes(root: Path, *, exclude_names: set[str] | None = None) -> dict[Path, str]:
    excluded = exclude_names or set()
    out: dict[Path, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name not in excluded and not p.name.startswith("."):
            out[p] = _sha256_file(p)
    return out


def _run_node_ui(script_body: str, payload: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    shim = r"""
class T { constructor(t) { this.text = String(t); } get textContent() { return this.text; } }
class CList {
  constructor(n) { this.n = n; }
  _parts() { return String(this.n.className || "").split(/\s+/).filter(Boolean); }
  add(...cs) { const s = new Set(this._parts()); cs.forEach((c) => s.add(c)); this.n.className = [...s].join(" "); }
  remove(...cs) { const r = new Set(cs); this.n.className = this._parts().filter((c) => !r.has(c)).join(" "); }
  contains(c) { return this._parts().includes(c); }
}
class N {
  constructor(tag) {
    this.tagName = tag; this.childNodes = []; this.attrs = {}; this.className = "";
    this.dataset = {}; this.style = {}; this.listeners = {}; this.classList = new CList(this);
    this.isConnected = true; this.value = "";
  }
  append(...cs) { for (const c of cs) if (c != null) this.childNodes.push(c); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(ev, fn) { (this.listeners[ev] ||= []).push(fn); }
  remove() {}
  focus() {}
  select() {}
  setSelectionRange() {}
  querySelectorAll() { return []; }
  set textContent(v) { this.childNodes = [new T(v)]; }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
}
globalThis.Node = N;
globalThis.document = {
  body: new N("body"),
  createElement: (t) => new N(t),
  createElementNS: (_, t) => new N(t),
  createTextNode: (t) => new T(t),
  execCommand: () => false,
};
const walk = (n, out = []) => { if (n && n.attrs) { out.push(n); for (const c of n.childNodes) walk(c, out); } return out; };
const sections = (n) => walk(n).filter((x) => x.tagName === "h5").map((x) => x.textContent);
"""
    full = (
        shim
        + 'import fs from "node:fs";\n'
        + 'const p = JSON.parse(fs.readFileSync(0, "utf8"));\n'
        + f'const L = await import("{LOCKS_JS}");\n'
        + f'const {{ renderPlayerAudit }} = await import("{PLAYER_AUDIT_JS}");\n'
        + f'const {{ api }} = await import("{API_JS}");\n'
        + f'const CH = await import("{CURRENT_HAND_JS}");\n'
        + f"const out = await (async () => {{ {script_body} }})();\n"
        + "console.log(JSON.stringify(out));\n"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", full],
        input=json.dumps(payload),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _load_events(ep_dir: Path) -> list[dict]:
    return sorted(
        (json.loads(p.read_text(encoding="utf-8")) for p in (ep_dir / "events").glob("*.json")),
        key=lambda e: (e.get("recorded_at_ms") or 0, e.get("event_id") or ""),
    )


def _setup_r16_market(
    tmp_path: Path,
    monkeypatch,
    *,
    quantity_btc: float = 1.0,
    taker_commission: float = 0.0003,
    put_strikes: tuple[int, int] = (97_000, 95_000),
    call_strikes: tuple[int, int] = (103_000, 105_000),
    initial_spot: float = 100_000.0,
    book_override_fn=None,
):
    wb, snapshots, now, first = setup(tmp_path / "tracker")
    expiry = first["contract"]["expiry_ms"]
    slot_r16 = expiry - 16 * HOUR
    spot_box = [initial_spot]

    def _make_instruments():
        rows = []
        for side, suffix, strikes in (
            ("put", "P", put_strikes),
            ("call", "C", call_strikes),
        ):
            for strike in strikes:
                row = option_meta(f"BTC-26SEP26-{strike}-{suffix}", side, strike)
                row["expiration_timestamp"] = expiry
                row["taker_commission"] = taker_commission
                row["greeks"] = {"delta": -0.35 if side == "put" else 0.35}
                row["delta_observed_at_ms"] = now[0] - 1_000
                rows.append(row)
        return rows

    def dynamic_quote(short_instrument, long_instrument, qty=1.0, **_kw):
        now_ms = now[0]
        idx = spot_box[0]
        metas = {r["instrument_name"]: r for r in _make_instruments()}

        def fetch(path, params):
            name = params["instrument_name"]
            if path == "public/get_instrument":
                return dict(metas[name])
            is_short = name == short_instrument
            default_book = {
                "instrument_name": name,
                "state": "open",
                "timestamp": now_ms,
                "index_price": idx,
                "mark_price": 0.0075 if is_short else 0.0025,
                "bids": [[0.007 if is_short else 0.002, max(2.0, qty * 2)]],
                "asks": [[0.008 if is_short else 0.003, max(2.0, qty * 2)]],
            }
            if book_override_fn is not None:
                override = book_override_fn(name, short_instrument, long_instrument, now_ms, idx, default_book)
                if override is not None:
                    return override
            return default_book

        return public_quote.capture_candidate(
            short_instrument, long_instrument, qty, transport=fetch, clock_ms=lambda: now_ms
        )

    def dynamic_discovery(*, now_ms):
        rows = [{**r, "delta_observed_at_ms": now_ms - 1_000} for r in _make_instruments()]
        return discovery.discover(rows, spot_box[0], now_ms, quantity_btc=quantity_btc, width_usd=2000.0)

    def write_fmz_with_flow(ts_ms: int):
        row = write_fmz(snapshots, native_gate(ts=ts_ms - 1_000), now=ts_ms)
        row["decision"]["runtime_facts"]["current_price"] = spot_box[0]
        row["factor_snapshot"]["flow"]["micro_flow"] = {
            "fast_4h": {"data_ready": True, "direction": "neutral", "cvd_sum": 15.0, "momentum_return_pct": 0.05},
            "slow_12h": {"data_ready": True, "direction": "neutral", "cvd_sum": 18.0, "momentum_return_pct": 0.10},
            "combined": {"data_ready": True, "direction": "neutral", "ready_horizons": ["4h", "12h"]},
        }
        snapshots.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    points = [[t, initial_spot] for t in range(slot_r16 - DAY, slot_r16 + M + 1, M)]

    def advance_market(ts_ms: int, idx: float | None = None, *, append_points: bool = True):
        now[0] = ts_ms
        if idx is not None:
            spot_box[0] = idx
        if append_points and points:
            start_m = points[-1][0] + M
            for minute in range(start_m, ts_ms + 1, M):
                points.append([minute, spot_box[0]])
        write_fmz_with_flow(ts_ms)
        wb.refresh(now_ms=ts_ms, record_daily=False)

    wb.discovery_capture = dynamic_discovery
    wb.quote_capture = dynamic_quote
    wb.config.shadow_experiment = True
    wb.config.shadow_dossier_root = tmp_path / "dossier"
    wb.config.shadow_service_token = "tok"
    wb.config.frontend_dir = ROOT / "deploy" / "astra_workbench_v3"
    advance_market(slot_r16, initial_spot, append_points=False)

    arch_root = tmp_path / "archive"
    _seed_r16_archive(wb, arch_root, slot_r16, expiry, points)
    contracts_proof = []
    for item in wb._current_view["candidates"]:
        c = item["snapshot"]["contract"]
        contracts_proof.extend([
            {"instrument_name": c["short_instrument"], "expiration_timestamp": expiry, "settlement_currency": "BTC", "is_active": True},
            {"instrument_name": c["long_instrument"], "expiration_timestamp": expiry, "settlement_currency": "BTC", "is_active": True},
        ])
    monkeypatch.setattr(
        dossier, "public",
        lambda method, **kw: contracts_proof if method == "get_instruments" else {"timestamps": [], "prices": []},
    )
    monkeypatch.setattr(
        dossier.archive, "Deribit",
        lambda *a, **kw: SimpleNamespace(index_path=lambda *x, **y: points),
    )

    return SimpleNamespace(
        wb=wb,
        d=dossier.Dossiers(wb.config.shadow_dossier_root, clock=lambda: now[0]),
        now=now,
        expiry=expiry,
        slot_r16=slot_r16,
        arch_root=arch_root,
        points=points,
        spot_box=spot_box,
        advance_market=advance_market,
        plan_r16=_plan_r16(slot_r16),
    )


def _deal_adhoc_hand(env, svc, *, side: str = "Put", reference: str = "ADHOC-CONCURRENT"):
    cases, cards, native = [], [], []
    for item in env.wb._current_view["candidates"]:
        snap = item["snapshot"]
        case = env.wb.case_file(snap["snapshot_id"])
        read_ms = case["build"]["built_at_ms"]
        walls = dict(
            expiry_ms=env.expiry,
            walls={"pin": 99_000.0},
            top={"oi_put": [{"strike": 98_000}], "oi_call": [{"strike": 102_000}]},
        )
        dossier.player.entry(case, "L3.expiry_walls").update(values=walls, asof_ms=read_ms, quality="ok")
        contract = dossier.player.values(case, "L1.contract")
        cards.append(
            dossier.levels.build_card(
                expiry_ms=env.expiry,
                rnd="r16",
                side=contract["side"],
                read_at_ms=read_ms,
                walls=walls,
                points=env.points,
                case=case,
                contract=contract,
                snapshots=[],
            )
        )
        cases.append(case)
        native.extend(
            dict(
                instrument_name=contract[f"{leg}_instrument"],
                expiration_timestamp=env.expiry,
                settlement_currency="BTC",
                is_active=True,
            )
            for leg in ("short", "long")
        )
    path = dict(schema="index_path@0.1", index_name="btc_usd", expiry_ms=env.expiry, points=env.points, fetched_at_ms=env.now[0])
    hand = env.d.expect(env.expiry, "AD_HOC", reference=reference)
    env.d.deal(hand, dossier.seal_source(cases, cards, path, [], native, received_at_ms=env.now[0], source="TEST_FROZEN"))
    req_t = env.now[0]
    env.now[0] += 100
    raw = (
        f"**Accept / {side}**\n"
        + "\n".join(EXCERPTS.values())
        + "\n"
        + "\n".join(RULE[k] for k in ("historical_facts", "accepted_conditions", "confirmation_excerpt"))
    ).encode("utf-8")
    env.d.respond(hand, f"adhoc-{side.lower()}", "RECEIVED", raw, EXCERPTS, model="deepseek-v4-pro", requested=req_t, decided=env.now[0])
    rule_for_side = {**RULE, "check_interval_ms": 5 * M, "require_outside_at_t0": True}
    receipt = tracking.link(env.d, hand, svc, rule_for_side)
    assert receipt["state"] == "SHADOW_LOCK_CREATED"
    return hand, receipt["t0"]["tracker_episode_id"]


def _revoke_transport_factory(counter: list[int], *, verdict: str = "REVOKE", affected: list[str] | None = None, reply_text: str | None = None):
    def _transport(api_key, model, request_body, timeout_s, endpoint=None):
        del api_key, model, request_body, timeout_s, endpoint
        counter[0] += 1
        aff = affected or ["M1"]
        msg = reply_text or ("边界规则已破坏，建议撤销影子承保。" if verdict == "REVOKE" else "维持观察。")
        content = (
            f"### {verdict}\n\n{msg}\n<HOLD_REVIEW_JSON>"
            + json.dumps({
                "verdict": verdict,
                "affected_conditions": aff,
                "public_reply": msg,
                "key_facts": [verdict],
            }, ensure_ascii=False)
            + "</HOLD_REVIEW_JSON>"
        )
        return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": {"total_tokens": 95}, "_http_status": 200}

    return _transport


def _invoke_reconcile(d, hand, wb, svc):
    fn = getattr(tracking, "reconcile_historical_shadow_exit", None)
    assert callable(fn), "Worker 1 must expose tracking.reconcile_historical_shadow_exit"
    sig = inspect.signature(fn)
    params = sig.parameters
    kwargs = {}
    if "service" in params:
        kwargs["service"] = svc
    if "wb" in params:
        kwargs["wb"] = wb
    if "workbench" in params:
        kwargs["workbench"] = wb
    if "locked_candidates" in params:
        kwargs["locked_candidates"] = wb.locked_candidates
    if "now_ms" in params:
        kwargs["now_ms"] = d.clock()
    if "hand" in params:
        return fn(d, hand, **kwargs)
    if "root" in params and "d" not in params:
        return fn(d.root, **kwargs)
    return fn(d, **kwargs)


def _assert_shadow_exit_contract_common(sx: dict, *, tier: str):
    assert sx["schema"] == "shadow_exit_snapshot@1"
    assert sx["exit_state"] == "SHADOW_EXITED"
    assert sx["terminal"] is True
    assert sx["active_monitoring_stopped"] is True
    assert sx["exit_reason"] == "AI_REVOKE"
    assert sx["valuation_tier"] == tier
    assert sx["simulated"] is True
    assert sx["execution_allowed"] is False
    assert sx["actual_trade_pnl_recorded"] is False
    assert isinstance(sx["snapshot_sha256"], str) and len(sx["snapshot_sha256"]) == 64


# ---------------------------------------------------------------------------
# Scenario 1: Accept -> M_TRIGGERED -> valid AI_REVOKE -> SHADOW_EXITED
# ---------------------------------------------------------------------------

def test_scenario1_accept_trigger_valid_revoke_shadow_exited_and_concurrent_episode(tmp_path, monkeypatch):
    env = _setup_r16_market(tmp_path, monkeypatch)
    _, init_transport = _mock_initial_llm("Call")

    with serving(env.wb) as base:
        svc = post(base, env.wb)
        env.advance_market(env.slot_r16 + 2 * M, 100_000.0)
        dealt = tracking.auto_deal(
            env.d, env.plan_r16, env.arch_root, svc,
            llm_config={"api_key": "k", "model": "deepseek-v4-pro", "transport": init_transport},
        )
        assert dealt[0]["decision"] == "Accept"
        hand_a = next((env.wb.config.shadow_dossier_root / "dossiers").glob("Deribit_BTC_*/hands/hand_*"))
        link_a = json.loads((hand_a / "tracking" / "link.json").read_text(encoding="utf-8"))
        lce_a = link_a["t0"]["tracker_episode_id"]
        ep_a = hand_a.parent.parent / "episodes" / link_a["origin"]["dossier_episode_id"]

        # Second concurrent active Put episode (97,000 / 95,000) stays outside throughout
        hand_b, lce_b = _deal_adhoc_hand(env, svc, side="Put", reference="CONCURRENT-ACTIVE-PUT")
        link_b = json.loads((hand_b / "tracking" / "link.json").read_text(encoding="utf-8"))
        ep_b = hand_b.parent.parent / "episodes" / link_b["origin"]["dossier_episode_id"]

        hold_calls = [0]
        revoke_llm = _revoke_transport_factory(hold_calls, verdict="REVOKE", affected=["M1"])

        # Trigger Call short breach (> 103,000) at 5m check tick -> valid AI_REVOKE -> SHADOW_EXITED
        env.advance_market(env.slot_r16 + 15 * M, 103_400.0)
        res_exit = tracking.sync(
            env.d, hand_a, svc, check_due=True, archive_root=env.arch_root, run_review=True,
            llm_config={"api_key": "k", "model": "deepseek-v4-pro", "transport": revoke_llm},
        )
        assert hold_calls[0] == 1
        assert any(c["kind"] == "CONDITION_TRIGGERED" for c in res_exit.get("checks") or [])

        ev_a = _load_events(ep_a)
        sx_events = [e for e in ev_a if e["kind"] == "SHADOW_EXITED"]
        assert len(sx_events) == 1
        sx_body = sx_events[0]["body"]
        _assert_shadow_exit_contract_common(sx_body, tier="EXECUTABLE_TWO_LEG_QUOTE")

        lce_a_row = next(e for e in env.wb.locked_candidates.list() if e["episode_id"] == lce_a)
        assert lce_a_row["status"] == "stopped"
        assert lce_a_row["stop_reason"] == "shadow_revoked_terminal"

        # Snapshot counts right at terminal exit
        grid_a_before = [e for e in ev_a if e["kind"].startswith("CONDITION_")]
        rev_a_before = list((ep_a / "reviews").glob("rev_*"))
        obs_a_before = [e for e in ev_a if e["kind"] == "OBSERVATION"]
        pts_a_before = env.wb.locked_candidates.detail(lce_a)["episode"]["point_count"]
        pts_b_before = env.wb.locked_candidates.detail(lce_b)["episode"]["point_count"]

        # Advance across multiple 1m MTM ticks + 5m sync + CLI sync-open -> Episode A completely frozen, Episode B continues!
        monkeypatch.setenv("ASTRA_AI_SHADOW_LOCK_TOKEN", env.wb.config.shadow_service_token)
        for step in (20, 25, 30):
            env.advance_market(env.slot_r16 + step * M, 104_500.0)
            tracking.sync(
                env.d, hand_a, svc, check_due=True, archive_root=env.arch_root, run_review=True,
                llm_config={"api_key": "k", "model": "deepseek-v4-pro", "transport": revoke_llm},
            )
            monkeypatch.setattr(
                sys,
                "argv",
                [
                    "astra_player_tracking_v13.py",
                    "--url", base,
                    "sync-open",
                    "--root", str(env.d.root),
                    "--check-due",
                    "--review",
                    "--archive", str(env.arch_root),
                ],
            )
            tracking.main()

        assert hold_calls[0] == 1  # 0 extra LLM calls!

        ev_a_after = _load_events(ep_a)
        assert [e for e in ev_a_after if e["kind"].startswith("CONDITION_")] == grid_a_before
        assert list((ep_a / "reviews").glob("rev_*")) == rev_a_before
        assert [e for e in ev_a_after if e["kind"] == "OBSERVATION"] == obs_a_before
        assert [e for e in ev_a_after if e["kind"] == "SHADOW_EXITED"] == sx_events
        assert env.wb.locked_candidates.detail(lce_a)["episode"]["point_count"] == pts_a_before
        lce_a_after = next(e for e in env.wb.locked_candidates.list() if e["episode_id"] == lce_a)
        assert lce_a_after["status"] == "stopped"
        assert lce_a_after["stop_reason"] == "shadow_revoked_terminal"

        # Second concurrent episode kept sampling & checking normally
        ev_b_after = _load_events(ep_b)
        assert len([e for e in ev_b_after if e["kind"] == "CONDITION_NOT_TRIGGERED"]) >= 3
        assert env.wb.locked_candidates.detail(lce_b)["episode"]["point_count"] > pts_b_before
        lce_b_after = next(e for e in env.wb.locked_candidates.list() if e["episode_id"] == lce_b)
        assert lce_b_after["status"] == "active"

        # Check audit_record + locked_candidate_list + locked_candidate_detail projections
        audit_a = tracking.audit_record(hand_a, env.now[0], {lce_a: "stopped", lce_b: "active"}, detail=True)
        assert audit_a["shadow_management"]["exit_terminal"] is True
        assert audit_a["shadow_management"]["exit_state"] == "SHADOW_EXITED"
        assert audit_a["shadow_management"]["active_monitoring_stopped"] is True
        assert audit_a["shadow_exit"]["valuation_tier"] == "EXECUTABLE_TWO_LEG_QUOTE"
        assert audit_a["post_revoke_grid_check_count"] == 0
        assert len(audit_a["grid_check_history"]) == len(grid_a_before)

        lc_list = env.wb.locked_candidate_list()
        ep_a_list = next(e for e in lc_list["episodes"] if e["episode_id"] == lce_a)
        assert ep_a_list["outcome"]["basis"] == "shadow_exit_frozen"
        assert ep_a_list["outcome"]["verdict"] in {"profit", "loss", "flat"}
        assert ep_a_list["ai_dossier_management"]["shadow_management"]["exit_terminal"] is True


# ---------------------------------------------------------------------------
# Scenario 2: Non-terminal paths must NOT enter SHADOW_EXITED
# ---------------------------------------------------------------------------

def test_scenario2_non_terminal_paths_keep_monitoring_and_recover(tmp_path, monkeypatch):
    env = _setup_r16_market(tmp_path, monkeypatch)
    _, init_transport = _mock_initial_llm("Call")

    with serving(env.wb) as base:
        svc = post(base, env.wb)
        env.advance_market(env.slot_r16 + 2 * M, 100_000.0)
        dealt = tracking.auto_deal(
            env.d, env.plan_r16, env.arch_root, svc,
            llm_config={"api_key": "k", "model": "deepseek-v4-pro", "transport": init_transport},
        )
        assert dealt[0]["decision"] == "Accept"
        hand = next((env.wb.config.shadow_dossier_root / "dossiers").glob("Deribit_BTC_*/hands/hand_*"))
        link_rec = json.loads((hand / "tracking" / "link.json").read_text(encoding="utf-8"))
        lce_id = link_rec["t0"]["tracker_episode_id"]
        ep_dir = hand.parent.parent / "episodes" / link_rec["origin"]["dossier_episode_id"]

        # Pre-deal second Put episode while spot == 100,000 so 2d tests DISCRETIONARY_REVOKE_UNVERIFIED with program_triggered=False
        hand_u, lce_u = _deal_adhoc_hand(env, svc, side="Put", reference="DISCRETIONARY-UNVERIFIED-PUT")
        link_u = json.loads((hand_u / "tracking" / "link.json").read_text(encoding="utf-8"))
        ep_u = hand_u.parent.parent / "episodes" / link_u["origin"]["dossier_episode_id"]

        def assert_still_active():
            events = _load_events(ep_dir)
            assert not any(e["kind"] == "SHADOW_EXITED" for e in events)
            ep_row = next(e for e in env.wb.locked_candidates.list() if e["episode_id"] == lce_id)
            assert ep_row["status"] == "active"
            audit = tracking.audit_record(hand, env.now[0], {lce_id: "active"}, detail=True)
            assert audit["shadow_management"]["exit_terminal"] is False
            assert audit["shadow_management"]["exit_state"] == "ACTIVE"
            assert audit["shadow_management"]["active_monitoring_stopped"] is False

        # 2a. Pending / inflight trigger inside debounce (no AI decision yet) -> remains active
        env.advance_market(env.slot_r16 + 7 * M, 102_200.0)
        exported_q = svc("export", {"dossier_id": hand.parent.parent.name, "hand_id": hand.name})
        res_q = tracking.evaluate_hold_management(
            env.d, hand, exported_q, archive_root=env.arch_root, service=svc,
            llm_config={"api_key": "k", "model": "deepseek-v4-pro", "transport": _revoke_transport_factory([0], verdict="HOLD")},
        )
        assert res_q["action"] == "QUEUED"
        assert_still_active()

        # Simulate inflight state -> remains active
        st_path = ep_dir / "trigger_state.json"
        st_saved = json.loads(st_path.read_text(encoding="utf-8"))
        st_saved["inflight"] = True
        st_saved["inflight_started_at_ms"] = env.now[0]
        st_path.write_text(json.dumps(st_saved), encoding="utf-8")
        assert_still_active()
        # Clear inflight for next subcase
        st_saved["inflight"] = False
        st_saved.pop("inflight_started_at_ms", None)
        st_path.write_text(json.dumps(st_saved), encoding="utf-8")

        # 2b. LLM timeout / HOLD_REVIEW_FAILED -> remains active & keeps checking on 5m grid
        def timeout_transport(*_a, **_kw):
            raise TimeoutError("upstream_timeout")

        env.advance_market(env.slot_r16 + 15 * M, 103_400.0)
        res_fail = tracking.sync(
            env.d, hand, svc, check_due=True, archive_root=env.arch_root, run_review=True,
            llm_config={"api_key": "k", "model": "deepseek-v4-pro", "transport": timeout_transport},
        )
        assert any(c["kind"] == "CONDITION_TRIGGERED" for c in res_fail["checks"])
        assert res_fail["hold_review"]["action"] == "FAILED"
        assert_still_active()

        # 2c. Recovers after backoff with AI HOLD -> still non-terminal and keeps checking
        hold_calls = [0]
        env.advance_market(env.slot_r16 + 22 * M, 103_450.0)
        res_hold = tracking.sync(
            env.d, hand, svc, check_due=True, archive_root=env.arch_root, run_review=True,
            llm_config={"api_key": "k", "model": "deepseek-v4-pro", "transport": _revoke_transport_factory(hold_calls, verdict="HOLD")},
        )
        assert hold_calls[0] == 1
        assert res_hold["hold_review"]["verdict"] == "HOLD"
        assert_still_active()

        # 2d. DISCRETIONARY_REVOKE_UNVERIFIED on an un-triggered active Put episode -> MUST NOT enter SHADOW_EXITED
        # Put spread (97,000 / 95,000) stays safely outside at spot 103,450 so program_triggered=False.
        env.advance_market(env.slot_r16 + 38 * M, 103_450.0)
        unverified_llm = _revoke_transport_factory([0], verdict="REVOKE", affected=["C999_UNMAPPED"])
        with patch.object(review, "detect_hold_triggers", return_value=[{"category": "D", "code": "SPOT_FLOW_FLIP", "summary_cn": "D", "observed_at_ms": env.now[0]}]):
            tracking.sync(
                env.d, hand_u, svc, check_due=True, archive_root=env.arch_root, run_review=False,
            )
            exported_u = svc("export", {"dossier_id": hand_u.parent.parent.name, "hand_id": hand_u.name})
            # First call enqueues Category D trigger; advancing past MIN_DEBOUNCE_MS fires evaluation on second call
            tracking.evaluate_hold_management(
                env.d, hand_u, exported_u, archive_root=env.arch_root, service=svc,
                llm_config={"api_key": "k", "model": "deepseek-v4-pro", "transport": unverified_llm},
                debounce_ms=0, cooldown_ms=0,
            )
            t_eval_u = env.now[0] + max(6 * M, review.MIN_DEBOUNCE_MS + M)
            env.advance_market(t_eval_u, 103_460.0)
            res_u_sync = tracking.evaluate_hold_management(
                env.d, hand_u, exported_u, archive_root=env.arch_root, service=svc,
                llm_config={"api_key": "k", "model": "deepseek-v4-pro", "transport": unverified_llm},
                debounce_ms=0, cooldown_ms=0,
            )
            env.advance_market(t_eval_u + 6 * M, 103_500.0)
            tracking.sync(
                env.d, hand_u, svc, check_due=True, archive_root=env.arch_root, run_review=False,
            )
            tracking.sync(
                env.d, hand, svc, check_due=True, archive_root=env.arch_root, run_review=False,
            )

        assert_still_active()
        assert not any(e["kind"] == "SHADOW_EXITED" for e in _load_events(ep_u))
        assert next(e for e in env.wb.locked_candidates.list() if e["episode_id"] == lce_u)["status"] == "active"
        audit_u = tracking.audit_record(hand_u, env.now[0], {lce_u: "active"}, detail=True)
        assert audit_u["shadow_management"]["exit_terminal"] is False
        assert audit_u["shadow_management"]["exit_state"] == "ACTIVE"
        assert audit_u["shadow_management"]["active_monitoring_stopped"] is False
        assert res_u_sync.get("protocol_status") == "DISCRETIONARY_REVOKE_UNVERIFIED" or any(
            r.get("protocol_status") == "DISCRETIONARY_REVOKE_UNVERIFIED" for r in audit_u["hold_reviews"]
        )
        # Verify grid checks continued past failed / HOLD / unverified-revoke ticks on both episodes
        assert len([e for e in _load_events(ep_dir) if e["kind"] == "CONDITION_TRIGGERED"]) >= 3
        assert len([e for e in _load_events(ep_u) if e["kind"] == "CONDITION_NOT_TRIGGERED"]) >= 2


# ---------------------------------------------------------------------------
# Scenario 3: Three-tier valuation correctness & immutability
# ---------------------------------------------------------------------------

def test_scenario3_valuation_three_tiers_and_immutability(tmp_path, monkeypatch):
    # 3a. Put spread (0.5 BTC) + Call spread (1.5 BTC) EXECUTABLE_TWO_LEG_QUOTE with capped fee math & crash immutability
    mode = {"tier": "executable", "crash": False}

    def book_override(name, short_inst, long_inst, now_ms, idx, default_book):
        is_short = name == short_inst
        if mode["crash"]:
            return {
                **default_book,
                "mark_price": 0.25 if is_short else 0.05,
                "bids": [[0.24, 5.0]],
                "asks": [[0.26, 5.0]],
            }
        if mode["tier"] == "executable" and idx != 100_000.0:
            # At revoke: multi-level depth + deep OTM long bid triggering 0.125*price fee cap!
            if name.endswith("-P"):
                # Put 0.5 BTC:
                # short_buy_btc = 0.2*0.012 + 0.3*0.014 = 0.0066; fee = 0.2*0.0003 + 0.3*0.0003 = 0.00015
                # long_sale_btc = 0.3*0.004 + 0.2*0.001 = 0.0014; fee = 0.3*0.0003 + 0.2*(0.125*0.001) = 0.000115
                return {
                    **default_book,
                    "mark_price": 0.013 if is_short else 0.0025,
                    "asks": [[0.012, 0.2], [0.014, 0.5]] if is_short else [[0.005, 1.0]],
                    "bids": [[0.011, 1.0]] if is_short else [[0.004, 0.3], [0.001, 0.5]],
                }
            # Call 1.5 BTC:
            # short_buy_btc = 0.5*0.010 + 1.0*0.012 = 0.0170; fee = 1.5*0.0003 = 0.00045
            # long_sale_btc = 1.0*0.003 + 0.5*0.001 = 0.0035; fee = 1.0*0.0003 + 0.5*(0.125*0.001) = 0.0003625
            return {
                **default_book,
                "mark_price": 0.011 if is_short else 0.002,
                "asks": [[0.010, 0.5], [0.012, 2.0]] if is_short else [[0.004, 2.0]],
                "bids": [[0.009, 2.0]] if is_short else [[0.003, 1.0], [0.001, 1.0]],
            }
        if mode["tier"] == "indicative" and idx != 100_000.0:
            # Fresh marks on both legs, missing long bid
            return {
                **default_book,
                "mark_price": 0.012 if is_short else 0.002,
                "asks": [[0.013, 2.0]] if is_short else [[0.003, 2.0]],
                "bids": [[0.011, 2.0]] if is_short else [],
            }
        if mode["tier"] == "unpriced" and idx != 100_000.0:
            # Stale & empty books
            return {
                **default_book,
                "timestamp": now_ms - 60_000,
                "mark_price": None,
                "bids": [],
                "asks": [],
            }
        return None

    # 1) Put spread 0.5 BTC EXECUTABLE_TWO_LEG_QUOTE
    env_put = _setup_r16_market(tmp_path / "put_exec", monkeypatch, quantity_btc=0.5, taker_commission=0.0003, book_override_fn=book_override)
    _, init_put = _mock_initial_llm("Put")
    with serving(env_put.wb) as base_p:
        svc_p = post(base_p, env_put.wb)
        env_put.advance_market(env_put.slot_r16 + 2 * M, 100_000.0)
        dealt_p = tracking.auto_deal(env_put.d, env_put.plan_r16, env_put.arch_root, svc_p, llm_config={"api_key": "k", "model": "m", "transport": init_put})
        assert dealt_p[0]["decision"] == "Accept"
        hand_p = next((env_put.wb.config.shadow_dossier_root / "dossiers").glob("Deribit_BTC_*/hands/hand_*"))
        link_p = json.loads((hand_p / "tracking" / "link.json").read_text(encoding="utf-8"))
        lce_p = link_p["t0"]["tracker_episode_id"]
        ep_p = hand_p.parent.parent / "episodes" / link_p["origin"]["dossier_episode_id"]

        env_put.advance_market(env_put.slot_r16 + 15 * M, 96_500.0)
        tracking.sync(
            env_put.d, hand_p, svc_p, check_due=True, archive_root=env_put.arch_root, run_review=True,
            llm_config={"api_key": "k", "model": "m", "transport": _revoke_transport_factory([0], verdict="REVOKE")},
        )
        audit_p = tracking.audit_record(hand_p, env_put.now[0], {lce_p: "stopped"}, detail=True)
        sx_p = audit_p["shadow_exit"]
        _assert_shadow_exit_contract_common(sx_p, tier="EXECUTABLE_TWO_LEG_QUOTE")
        assert sx_p["contract"]["side"] == "put" and sx_p["contract"]["quantity_btc"] == pytest.approx(0.5)
        entry_after_p = link_p["t0"]["economy"]["net_credit_after_entry_fee_btc"]
        expected_close_debit_p = 0.0066 - 0.0014
        expected_close_fee_p = 0.00015 + 0.000115
        expected_exit_pnl_p = entry_after_p - expected_close_debit_p - expected_close_fee_p
        assert sx_p["exit_quote"]["close_debit_btc"] == pytest.approx(expected_close_debit_p, abs=1e-9)
        assert sx_p["exit_quote"]["close_fee_btc"] == pytest.approx(expected_close_fee_p, abs=1e-9)
        assert sx_p["exit_pnl_btc"] == pytest.approx(expected_exit_pnl_p, abs=1e-8)
        assert sx_p["exit_pnl_usd"] == pytest.approx(expected_exit_pnl_p * 96_500.0, abs=1e-2)

        # Immutability under massive crash
        sx_file_p = hand_p / "tracking" / "shadow_exit.json"
        assert sx_file_p.is_file()
        sx_bytes_p = sx_file_p.read_bytes()
        sx_sha_p = sx_p["snapshot_sha256"]
        mode["crash"] = True
        env_put.advance_market(env_put.slot_r16 + 30 * M, 50_000.0)
        tracking.sync(env_put.d, hand_p, svc_p, check_due=True, archive_root=env_put.arch_root, run_review=True)
        audit_p2 = tracking.audit_record(hand_p, env_put.now[0], {lce_p: "stopped"}, detail=True)
        assert audit_p2["shadow_exit"]["snapshot_sha256"] == sx_sha_p
        assert audit_p2["shadow_exit"]["exit_pnl_btc"] == pytest.approx(sx_p["exit_pnl_btc"])
        assert sx_file_p.read_bytes() == sx_bytes_p

    # 2) Call spread 1.5 BTC EXECUTABLE_TWO_LEG_QUOTE
    mode["crash"] = False
    mode["tier"] = "executable"
    env_call = _setup_r16_market(tmp_path / "call_exec", monkeypatch, quantity_btc=1.5, taker_commission=0.0003, book_override_fn=book_override)
    _, init_call = _mock_initial_llm("Call")
    with serving(env_call.wb) as base_c:
        svc_c = post(base_c, env_call.wb)
        env_call.advance_market(env_call.slot_r16 + 2 * M, 100_000.0)
        tracking.auto_deal(env_call.d, env_call.plan_r16, env_call.arch_root, svc_c, llm_config={"api_key": "k", "model": "m", "transport": init_call})
        hand_c = next((env_call.wb.config.shadow_dossier_root / "dossiers").glob("Deribit_BTC_*/hands/hand_*"))
        link_c = json.loads((hand_c / "tracking" / "link.json").read_text(encoding="utf-8"))
        lce_c = link_c["t0"]["tracker_episode_id"]
        env_call.advance_market(env_call.slot_r16 + 15 * M, 103_500.0)
        tracking.sync(
            env_call.d, hand_c, svc_c, check_due=True, archive_root=env_call.arch_root, run_review=True,
            llm_config={"api_key": "k", "model": "m", "transport": _revoke_transport_factory([0], verdict="REVOKE")},
        )
        sx_c = tracking.audit_record(hand_c, env_call.now[0], {lce_c: "stopped"}, detail=True)["shadow_exit"]
        _assert_shadow_exit_contract_common(sx_c, tier="EXECUTABLE_TWO_LEG_QUOTE")
        assert sx_c["contract"]["side"] == "call" and sx_c["contract"]["quantity_btc"] == pytest.approx(1.5)
        entry_after_c = link_c["t0"]["economy"]["net_credit_after_entry_fee_btc"]
        expected_debit_c = 0.0170 - 0.0035
        expected_fee_c = 0.00045 + 0.0003625
        assert sx_c["exit_quote"]["close_debit_btc"] == pytest.approx(expected_debit_c, abs=1e-9)
        assert sx_c["exit_quote"]["close_fee_btc"] == pytest.approx(expected_fee_c, abs=1e-9)
        assert sx_c["exit_pnl_btc"] == pytest.approx(entry_after_c - expected_debit_c - expected_fee_c, abs=1e-8)

    # 3b. INDICATIVE_MARK_ONLY (fresh marks, missing long bid)
    mode["tier"] = "indicative"
    env_ind = _setup_r16_market(tmp_path / "ind_only", monkeypatch, quantity_btc=1.0, taker_commission=0.0003, book_override_fn=book_override)
    with serving(env_ind.wb) as base_i:
        svc_i = post(base_i, env_ind.wb)
        env_ind.advance_market(env_ind.slot_r16 + 2 * M, 100_000.0)
        tracking.auto_deal(env_ind.d, env_ind.plan_r16, env_ind.arch_root, svc_i, llm_config={"api_key": "k", "model": "m", "transport": init_call})
        hand_i = next((env_ind.wb.config.shadow_dossier_root / "dossiers").glob("Deribit_BTC_*/hands/hand_*"))
        lce_i = json.loads((hand_i / "tracking" / "link.json").read_text(encoding="utf-8"))["t0"]["tracker_episode_id"]
        env_ind.advance_market(env_ind.slot_r16 + 15 * M, 103_400.0)
        tracking.sync(
            env_ind.d, hand_i, svc_i, check_due=True, archive_root=env_ind.arch_root, run_review=True,
            llm_config={"api_key": "k", "model": "m", "transport": _revoke_transport_factory([0], verdict="REVOKE")},
        )
        sx_i = tracking.audit_record(hand_i, env_ind.now[0], {lce_i: "stopped"}, detail=True)["shadow_exit"]
        _assert_shadow_exit_contract_common(sx_i, tier="INDICATIVE_MARK_ONLY")
        assert sx_i["exit_pnl_btc"] is None and sx_i["exit_pnl_usd"] is None
        assert isinstance(sx_i["indicative_exit_pnl_btc"], float)
        assert "long_depth_insufficient" in sx_i["quote_gap_reasons"]
        out_i = next(e for e in env_ind.wb.locked_candidate_list()["episodes"] if e["episode_id"] == lce_i)["outcome"]
        assert out_i["basis"] == "shadow_exit_indicative_only" and out_i["verdict"] is None

    # 3c. UNPRICED + pre-revoke vs post-revoke hindsight guard on last_indicative_reference
    mode["tier"] = "unpriced"
    env_unp = _setup_r16_market(tmp_path / "unpriced", monkeypatch, quantity_btc=1.0, taker_commission=0.0003, book_override_fn=book_override)
    with serving(env_unp.wb) as base_u:
        svc_u = post(base_u, env_unp.wb)
        env_unp.advance_market(env_unp.slot_r16 + 2 * M, 100_000.0)
        tracking.auto_deal(env_unp.d, env_unp.plan_r16, env_unp.arch_root, svc_u, llm_config={"api_key": "k", "model": "m", "transport": init_call})
        hand_u = next((env_unp.wb.config.shadow_dossier_root / "dossiers").glob("Deribit_BTC_*/hands/hand_*"))
        lce_u = json.loads((hand_u / "tracking" / "link.json").read_text(encoding="utf-8"))["t0"]["tracker_episode_id"]

        # Append a valid pre-revoke sample with known mark float_pnl_btc, plus a fake future/post-revoke sample in series
        revoke_tick = env_unp.slot_r16 + 15 * M
        pre_row = {
            "point_id": hashlib.sha256(b"p_pre_valid").hexdigest(),
            "step": "sample",
            "recorded_at_ms": revoke_tick - 60_000,
            "metrics": {
                "asof_ms": revoke_tick - 60_000,
                "spot_usd": 102_500.0,
                "short_mark_btc": 0.009,
                "long_mark_btc": 0.002,
                "float_pnl_btc": -0.0013,
                "close_debit_btc": None,
                "close_fee_btc": None,
                "reference_result_btc": None,
            },
        }
        post_hindsight_row = {
            "point_id": hashlib.sha256(b"p_post_hindsight").hexdigest(),
            "step": "sample",
            "recorded_at_ms": revoke_tick + 300_000,
            "metrics": {
                "asof_ms": revoke_tick + 300_000,
                "spot_usd": 115_000.0,
                "short_mark_btc": 0.090,
                "long_mark_btc": 0.020,
                "float_pnl_btc": -0.0555,
                "close_debit_btc": None,
                "close_fee_btc": None,
                "reference_result_btc": None,
            },
        }
        env_unp.wb.locked_candidates.append_series(lce_u, [pre_row, post_hindsight_row])

        env_unp.advance_market(revoke_tick, 103_400.0)
        tracking.sync(
            env_unp.d, hand_u, svc_u, check_due=True, archive_root=env_unp.arch_root, run_review=True,
            llm_config={"api_key": "k", "model": "m", "transport": _revoke_transport_factory([0], verdict="REVOKE")},
        )
        sx_u = tracking.audit_record(hand_u, env_unp.now[0], {lce_u: "stopped"}, detail=True)["shadow_exit"]
        _assert_shadow_exit_contract_common(sx_u, tier="UNPRICED")
        assert sx_u["exit_pnl_btc"] is None and sx_u["exit_pnl_usd"] is None
        assert sx_u["indicative_exit_pnl_btc"] is None and sx_u["indicative_exit_pnl_usd"] is None
        ref = sx_u["last_indicative_reference"]
        assert ref is not None
        assert ref["asof_ms"] <= sx_u["shadow_exit_at_ms"]
        assert ref["basis"] == "pre_revoke_asof_series_reference_not_exit_fill"
        assert ref["float_pnl_btc"] != pytest.approx(-0.0555)
        out_u = next(e for e in env_unp.wb.locked_candidate_list()["episodes"] if e["episode_id"] == lce_u)["outcome"]
        assert out_u["basis"] == "shadow_exit_unpriced" and out_u["verdict"] is None


# ---------------------------------------------------------------------------
# Scenario 4: Historical screenshot-like replay + reconcile_historical_shadow_exit
# ---------------------------------------------------------------------------

def test_scenario4_historical_screenshot_replay_reconcile_and_byte_identity(tmp_path, monkeypatch):
    env = _setup_r16_market(tmp_path, monkeypatch)
    _, init_transport = _mock_initial_llm("Call")

    with serving(env.wb) as base:
        svc = post(base, env.wb)
        env.advance_market(env.slot_r16 + 2 * M, 100_000.0)
        tracking.auto_deal(
            env.d, env.plan_r16, env.arch_root, svc,
            llm_config={"api_key": "k", "model": "deepseek-v4-pro", "transport": init_transport},
        )
        hand = next((env.wb.config.shadow_dossier_root / "dossiers").glob("Deribit_BTC_*/hands/hand_*"))
        link_rec = json.loads((hand / "tracking" / "link.json").read_text(encoding="utf-8"))
        lce_id = link_rec["t0"]["tracker_episode_id"]
        ep_dir = hand.parent.parent / "episodes" / link_rec["origin"]["dossier_episode_id"]
        frozen = json.loads((ep_dir / "condition.json").read_text(encoding="utf-8"))

        # Plant 177 pre-existing CONDITION_TRIGGERED events (172 pre-revoke + 5 legacy post-revoke)
        t_start = frozen["check_clock"]["first_at_ms"]
        revoke_ts = t_start + 172 * 5 * M - 60_000
        env.now[0] = t_start + 177 * 5 * M + 60_000
        for i in range(177):
            ts = t_start + i * 5 * M
            env.d.event(
                ep_dir,
                dossier.identity("chk_hist", i, ts),
                "CONDITION_TRIGGERED",
                {"result": {"rule": "entry_not_returned", "spot_usd": 103_400.0}},
                observed=ts,
                available=ts + 1_000,
            )

        # Plant pre-fix AI_REVOKE decision + stale short-only revoke_quote.json + REVOKE_RECORDED_NO_QUOTE
        rev_id = "rev_hist000000000000000001"
        rev_decision = {
            "review_id": rev_id,
            "status": "VALID",
            "verdict": "REVOKE",
            "ai_management_verdict": "REVOKE",
            "mechanical_condition_state": "TRIGGERED",
            "protocol_status": "ALIGNED",
            "shadow_action": "REVOKE_ALIGNED",
            "trigger_categories": ["A"],
            "triggers": [{"category": "A", "code": "CONDITION_TRIGGERED", "summary_cn": "已破坏", "observed_at_ms": revoke_ts - 60_000}],
            "affected_conditions": ["M1"],
            "public_reply": "历史撤销建议",
            "model": "deepseek-v4-pro",
            "requested_at_ms": revoke_ts - 5_000,
            "decided_at_ms": revoke_ts,
        }
        dossier.publish(ep_dir / "reviews" / rev_id, {
            "decision.json": dossier.encode(rev_decision),
            "revoke_quote.json": dossier.encode({
                "captured_at_ms": revoke_ts,
                "quote": {
                    "short_buyback_ask_btc": None,
                    "short_buyback_fee_btc": None,
                    "short_buyback_total_btc": None,
                    "short_quote_asof_ms": revoke_ts - 180_000,
                    "short_buyback_gaps": ["short_quote_stale_at_revoke"],
                },
            }),
        })
        tracking._record_review_event(env.d, ep_dir, rev_decision)
        tracking._record_revoke_shadow_action(env.d, ep_dir, frozen, {
            "short_buyback_ask_btc": None,
            "short_buyback_fee_btc": None,
            "short_buyback_total_btc": None,
            "short_quote_asof_ms": revoke_ts - 180_000,
            "short_buyback_gaps": ["short_quote_stale_at_revoke"],
        }, revoke_ts, review_id=rev_id)

        # Plant pre-revoke row + post-revoke -0.0222 BTC rows in series
        env.wb.locked_candidates.append_series(lce_id, [
            {
                "point_id": hashlib.sha256(b"p_hist_pre").hexdigest(),
                "step": "sample",
                "recorded_at_ms": revoke_ts - 120_000,
                "metrics": {
                    "asof_ms": revoke_ts - 120_000,
                    "spot_usd": 102_100.0,
                    "short_mark_btc": 0.0085,
                    "long_mark_btc": 0.0020,
                    "float_pnl_btc": -0.0014,
                    "close_debit_btc": None,
                    "close_fee_btc": None,
                    "reference_result_btc": None,
                },
            },
            {
                "point_id": hashlib.sha256(b"p_hist_post").hexdigest(),
                "step": "sample",
                "recorded_at_ms": revoke_ts + 20 * M,
                "metrics": {
                    "asof_ms": revoke_ts + 20 * M,
                    "spot_usd": 109_000.0,
                    "short_mark_btc": 0.0320,
                    "long_mark_btc": 0.0050,
                    "float_pnl_btc": -0.0222,
                    "close_debit_btc": 0.0270,
                    "close_fee_btc": 0.0005,
                    "reference_result_btc": -0.0229,
                },
            },
        ])
        series_path = env.wb.locked_candidates._series_path(lce_id)

        pre_hashes = _snapshot_tree_hashes(env.d.root, exclude_names={"summary.md", "manifest.json", "trigger_state.json"})
        series_sha_before = _sha256_file(series_path)

        # Run reconcile_historical_shadow_exit twice -> idempotent, zero original file mutations!
        _invoke_reconcile(env.d, hand, env.wb, svc)
        _invoke_reconcile(env.d, hand, env.wb, svc)

        for orig_path, orig_sha in pre_hashes.items():
            assert _sha256_file(orig_path) == orig_sha, f"historical file mutated: {orig_path}"
        assert _sha256_file(series_path) == series_sha_before

        events_after = _load_events(ep_dir)
        sx_events = [e for e in events_after if e["kind"] == "SHADOW_EXITED"]
        assert len(sx_events) == 1
        assert any(e["kind"] == "AI_REVOKE" for e in events_after)

        sx = sx_events[0]["body"]
        _assert_shadow_exit_contract_common(sx, tier="UNPRICED")
        assert sx["exit_pnl_btc"] is None
        assert sx["indicative_exit_pnl_btc"] is None
        assert sx["historical_compat_closure"] is True
        ref = sx["last_indicative_reference"]
        assert ref is not None
        assert ref["asof_ms"] <= revoke_ts
        assert ref["float_pnl_btc"] == pytest.approx(-0.0014)
        assert ref["float_pnl_btc"] != pytest.approx(-0.0222)

        lce_row = next(e for e in env.wb.locked_candidates.list() if e["episode_id"] == lce_id)
        assert lce_row["status"] == "stopped"
        assert lce_row["stop_reason"] == "shadow_revoked_terminal"

        audit = tracking.audit_record(hand, env.now[0], {lce_id: "stopped"}, detail=True)
        assert any(ev["kind"] == "AI_REVOKE" for ev in audit["events"])
        assert any(ev["kind"] == "SHADOW_EXITED" for ev in audit["events"])
        assert len(audit["grid_check_history"]) == 177
        assert audit["post_revoke_grid_check_count"] == 5
        assert sum(1 for row in audit["grid_check_history"] if row.get("post_revoke") is True) == 5

        # Node UI verification on actual audit_record output
        ui_out = _run_node_ui(
            """
            const box = document.createElement("div");
            renderPlayerAudit(box, {
              cards: [p.audit],
              ref: `${p.audit.dossier_id}/${p.audit.hand_id}`,
              detail: p.audit,
              expanded: new Set(),
            }, {
              authenticated: true, available: true,
              onSelect: () => {}, onTracker: () => {}, onOriginals: () => {},
              onLogin: () => {}, onRefresh: () => {}, modelInputUrl: api.playerModelInputUrl,
            });
            const nodes = walk(box);
            const evTbl = nodes.find((n) => (n.className || "").includes("pa-events-table"));
            const trigRows = walk(evTbl).filter((n) => n.tagName === "tr" && n.textContent.includes("网格检查：已触发破坏条件"));
            const auditDet = nodes.find((n) => (n.className || "").includes("pa-grid-audit"));
            return {
              text: box.textContent,
              trigRowCount: trigRows.length,
              auditSummary: auditDet ? auditDet.childNodes[0].textContent : null,
            };
            """,
            {"audit": audit},
        )
        assert "AI 撤销 → 影子已退出 → 已停止主动追踪" in ui_out["text"]
        assert "含修复前遗留撤销后检查 5 次，已默认折叠" in ui_out["text"]
        assert ui_out["trigRowCount"] <= 2
        assert ui_out["auditSummary"] == "展开完整网格检查审计轨迹（共 177 次）"


# ---------------------------------------------------------------------------
# Scenario 5: Idempotency, crash recovery & post-expiry counterfactual settlement
# ---------------------------------------------------------------------------

def test_scenario5_idempotency_crash_recovery_and_post_expiry_counterfactual(tmp_path, monkeypatch):
    env = _setup_r16_market(tmp_path, monkeypatch)
    _, init_transport = _mock_initial_llm("Call")

    with serving(env.wb) as base:
        svc = post(base, env.wb)
        env.advance_market(env.slot_r16 + 2 * M, 100_000.0)
        tracking.auto_deal(
            env.d, env.plan_r16, env.arch_root, svc,
            llm_config={"api_key": "k", "model": "deepseek-v4-pro", "transport": init_transport},
        )
        hand = next((env.wb.config.shadow_dossier_root / "dossiers").glob("Deribit_BTC_*/hands/hand_*"))
        link_rec = json.loads((hand / "tracking" / "link.json").read_text(encoding="utf-8"))
        lce_id = link_rec["t0"]["tracker_episode_id"]
        ep_dir = hand.parent.parent / "episodes" / link_rec["origin"]["dossier_episode_id"]

        # Crash right after decision.json is written, BEFORE SHADOW_EXITED / service('stop')
        class CrashBeforeExit(RuntimeError):
            pass

        def crashing_svc(action, body):
            if action == "stop":
                raise CrashBeforeExit("simulated crash before stop")
            return svc(action, body)

        hold_calls = [0]
        revoke_llm = _revoke_transport_factory(hold_calls, verdict="REVOKE")
        env.advance_market(env.slot_r16 + 15 * M, 103_400.0)
        with patch.object(tracking, "sync", wraps=tracking.sync):
            try:
                tracking.sync(
                    env.d, hand, crashing_svc, check_due=True, archive_root=env.arch_root, run_review=True,
                    llm_config={"api_key": "k", "model": "deepseek-v4-pro", "transport": revoke_llm},
                )
            except CrashBeforeExit:
                pass

        # decision.json is persisted; now recover cleanly via sync() with ZERO extra LLM calls!
        assert any((ep_dir / "reviews").glob("rev_*/decision.json"))
        tracking.sync(
            env.d, hand, svc, check_due=True, archive_root=env.arch_root, run_review=True,
            llm_config={"api_key": "k", "model": "deepseek-v4-pro", "transport": revoke_llm},
        )
        assert hold_calls[0] == 1

        ev_1 = _load_events(ep_dir)
        assert len([e for e in ev_1 if e["kind"] == "SHADOW_EXITED"]) == 1
        lce_1 = next(e for e in env.wb.locked_candidates.list() if e["episode_id"] == lce_id)
        assert lce_1["status"] == "stopped" and lce_1["stop_reason"] == "shadow_revoked_terminal"

        audit_before = tracking.audit_record(hand, env.now[0], {lce_id: "stopped"}, detail=True)
        sx_before = audit_before["shadow_exit"]
        sx_file = hand / "tracking" / "shadow_exit.json"
        assert sx_file.is_file()
        sx_sha_disk = _sha256_file(sx_file)
        pts_before = env.wb.locked_candidates.detail(lce_id)["episode"]["point_count"]
        ev_count_before = len(ev_1)

        # Duplicate ticks + workbench restart + expiry settlement -> zero mutation / zero extra exits / zero LLM calls
        for step in (20, 25):
            env.advance_market(env.slot_r16 + step * M, 104_800.0)
            tracking.sync(
                env.d, hand, svc, check_due=True, archive_root=env.arch_root, run_review=True,
                llm_config={"api_key": "k", "model": "deepseek-v4-pro", "transport": revoke_llm},
            )

        env.now[0] = env.expiry + 3 * M
        wb_restarted = workbench.AstraWorkbench(env.wb.config, clock_ms=lambda: env.now[0])
        wb_restarted.delivery_price_fetch = lambda _exp: {"delivery_price_usd": 106_000.0, "date": "2026-09-26"}
        for minute in range(env.points[-1][0] + M, env.expiry + 1, M):
            env.points.append([minute, 106_000.0])
        wb_restarted._settle_expired_locks(env.now[0])
        out_body = env.d.settle(env.expiry, 106_000.0, {"index_name": "btc_usd", "expiry_ms": env.expiry, "points": env.points})
        env.d.save_outcome(out_body, {"source": "test_delivery", "price": 106_000.0})

        with serving(wb_restarted) as base2:
            svc2 = post(base2, wb_restarted)
            tracking.sync(
                env.d, hand, svc2, check_due=True, archive_root=env.arch_root, run_review=True,
                llm_config={"api_key": "k", "model": "deepseek-v4-pro", "transport": revoke_llm},
            )

        assert hold_calls[0] == 1
        ev_2 = _load_events(ep_dir)
        assert len([e for e in ev_2 if e["kind"] == "SHADOW_EXITED"]) == 1
        assert len([e for e in ev_2 if e["kind"].startswith("CONDITION_")]) == len([e for e in ev_1 if e["kind"].startswith("CONDITION_")])
        assert len(ev_2) >= ev_count_before
        assert wb_restarted.locked_candidates.detail(lce_id)["episode"]["point_count"] == pts_before
        assert _sha256_file(sx_file) == sx_sha_disk
        list_after = next(e for e in wb_restarted.locked_candidate_list()["episodes"] if e["episode_id"] == lce_id)
        detail_after = wb_restarted.locked_candidate_detail(lce_id)
        series_after = wb_restarted.locked_candidate_series(lce_id)
        assert list_after["outcome"]["basis"] == "shadow_exit_frozen"
        assert series_after["settlement"]["status"] == "settled"
        assert detail_after["ai_dossier_management"]["shadow_exit"]["snapshot_sha256"] == sx_before["snapshot_sha256"]
        audit_after = tracking.audit_record(hand, env.now[0], {lce_id: "stopped"}, detail=True)
        assert audit_after["shadow_exit"]["snapshot_sha256"] == sx_before["snapshot_sha256"]
        assert audit_after["shadow_exit"]["exit_pnl_btc"] == sx_before["exit_pnl_btc"]
        assert audit_after["outcome"]["classification"] == "OFFICIAL"


# ---------------------------------------------------------------------------
# Scenario 6: Real-vs-shadow boundary, P5/Formal-A plan compat & Node UI
# ---------------------------------------------------------------------------

def test_scenario6_real_vs_shadow_boundary_plan_compat_and_node_ui(tmp_path, monkeypatch):
    # 6a. Real-vs-shadow boundary: execution_allowed=True MUST NEVER trigger SHADOW_EXITED or stop monitoring
    env = _setup_r16_market(tmp_path / "exec_guard", monkeypatch)
    _, init_transport = _mock_initial_llm("Call")

    with serving(env.wb) as base:
        svc = post(base, env.wb)
        env.advance_market(env.slot_r16 + 2 * M, 100_000.0)
        tracking.auto_deal(
            env.d, env.plan_r16, env.arch_root, svc,
            llm_config={"api_key": "k", "model": "deepseek-v4-pro", "transport": init_transport},
        )
        hand = next((env.wb.config.shadow_dossier_root / "dossiers").glob("Deribit_BTC_*/hands/hand_*"))
        link_path = hand / "tracking" / "link.json"
        link_rec = json.loads(link_path.read_text(encoding="utf-8"))
        lce_id = link_rec["t0"]["tracker_episode_id"]
        ep_dir = hand.parent.parent / "episodes" / link_rec["origin"]["dossier_episode_id"]

        # Tamper execution_allowed=True on link.json & expected.json
        link_rec["execution_allowed"] = True
        link_path.write_text(json.dumps(link_rec), encoding="utf-8")
        exp_path = hand / "expected.json"
        exp_rec = json.loads(exp_path.read_text(encoding="utf-8"))
        exp_rec.setdefault("plan", {})["execution_allowed"] = True
        exp_path.write_text(json.dumps(exp_rec), encoding="utf-8")

        stop_calls = []

        def spy_svc(action, body):
            if action == "stop":
                stop_calls.append(body)
            res = svc(action, body)
            if action == "export" and isinstance(res, dict) and isinstance(res.get("episode"), dict):
                res["episode"]["execution_allowed"] = True
            return res

        env.advance_market(env.slot_r16 + 15 * M, 103_400.0)
        sync_exec_res = tracking.sync(
            env.d, hand, spy_svc, check_due=True, archive_root=env.arch_root, run_review=True,
            llm_config={"api_key": "k", "model": "deepseek-v4-pro", "transport": _revoke_transport_factory([0], verdict="REVOKE")},
        )
        assert sync_exec_res.get("state") != "SHADOW_EXITED"
        assert sync_exec_res["hold_review"]["verdict"] == "REVOKE"
        assert sync_exec_res["hold_review"]["exit_intent_only"] is True
        assert any(e["kind"] == "AI_REVOKE" for e in _load_events(ep_dir))
        assert stop_calls == []
        assert not any(e["kind"] == "SHADOW_EXITED" for e in _load_events(ep_dir))
        assert not (hand / "tracking" / "shadow_exit.json").exists()
        assert next(e for e in env.wb.locked_candidates.list() if e["episode_id"] == lce_id)["status"] == "active"
        # Next check-due / sync tick also keeps monitoring alive rather than short-circuiting to SHADOW_EXITED
        env.advance_market(env.slot_r16 + 20 * M, 103_500.0)
        sync_after_rev = tracking.sync(
            env.d, hand, spy_svc, check_due=True, archive_root=env.arch_root, run_review=True,
            llm_config={"api_key": "k", "model": "deepseek-v4-pro", "transport": _revoke_transport_factory([0], verdict="REVOKE")},
        )
        assert sync_after_rev.get("state") != "SHADOW_EXITED"
        assert sync_after_rev.get("hold_review", {}).get("exit_intent_only") is True
        assert stop_calls == []
        assert not any(e["kind"] == "SHADOW_EXITED" for e in _load_events(ep_dir))
        assert next(e for e in env.wb.locked_candidates.list() if e["episode_id"] == lce_id)["status"] == "active"

    # 6b. P5 (@1.2 + p0_strict@1 + p2_strict@1) and Formal A (@1.0) plan compatibility + daily_r16@2/@3/@4
    deploy_dir = ROOT / "deploy" / "player_dossier"
    v2_plan = json.loads((deploy_dir / "plan.daily_r16.v2.json").read_text(encoding="utf-8"))
    v3_plan = json.loads((deploy_dir / "plan.daily_r16.v3_p5.json").read_text(encoding="utf-8"))
    v4_plan = json.loads((deploy_dir / "plan.daily_r16.v4_rollback_formal_a.json").read_text(encoding="utf-8"))

    for label_plan, plan_cfg, use_p5 in (
        ("daily_r16@2", {**v2_plan, "effective_at_ms": env.slot_r16}, False),
        ("daily_r16@3", {**v3_plan, "effective_at_ms": env.slot_r16}, True),
        ("daily_r16@4", {**v4_plan, "effective_at_ms": env.slot_r16}, False),
    ):
        env_p = _setup_r16_market(
            tmp_path / f"compat_{label_plan.replace('@', '_')}",
            monkeypatch,
            put_strikes=(97_000, 95_000),
            call_strikes=(103_000, 105_000),
        )
        # Ensure L3/L5/band_snapshot exist for p0_strict@1 deal when use_p5=True
        plan_cfg["effective_at_ms"] = env_p.slot_r16
        if use_p5:
            def p5_transport(api_key, model, body, timeout, endpoint=None):
                del api_key, model, body, timeout, endpoint
                txt = _build_valid_p5_reply(
                    action_header="## Accept / Call",
                    short_strike=97_000,
                    long_strike=95_000,
                    call_short_strike=103_000,
                    call_long_strike=105_000,
                    side="call",
                )
                return {"choices": [{"message": {"content": txt}, "finish_reason": "stop"}], "usage": {"total_tokens": 110}}
            deal_transport = p5_transport
        else:
            _, deal_transport = _mock_initial_llm("Call")

        with serving(env_p.wb) as base_p:
            svc_p = post(base_p, env_p.wb)
            env_p.advance_market(env_p.slot_r16 + 2 * M, 100_000.0)
            dealt_p = tracking.auto_deal(
                env_p.d, plan_cfg, env_p.arch_root, svc_p,
                llm_config={"api_key": "k", "model": "deepseek-v4-pro", "transport": deal_transport},
            )
            assert dealt_p[0]["decision"] == "Accept", label_plan
            hand_p = next((env_p.wb.config.shadow_dossier_root / "dossiers").glob("Deribit_BTC_*/hands/hand_*"))
            lce_p = json.loads((hand_p / "tracking" / "link.json").read_text(encoding="utf-8"))["t0"]["tracker_episode_id"]

            env_p.advance_market(env_p.slot_r16 + 15 * M, 103_450.0)
            tracking.sync(
                env_p.d, hand_p, svc_p, check_due=True, archive_root=env_p.arch_root, run_review=True,
                llm_config={"api_key": "k", "model": "deepseek-v4-pro", "transport": _revoke_transport_factory([0], verdict="REVOKE")},
            )
            list_payload = env_p.wb.locked_candidate_list()
            detail_payload = env_p.wb.locked_candidate_detail(lce_p)
            series_payload = env_p.wb.locked_candidate_series(lce_p)
            audit_payload = tracking.audit_record(hand_p, env_p.now[0], {lce_p: "stopped"}, detail=True)

            assert detail_payload["ai_dossier_management"]["shadow_management"]["exit_terminal"] is True
            assert audit_payload["shadow_management"]["exit_terminal"] is True

            if label_plan == "daily_r16@3":
                # 6c. End-to-end Node UI rendering on real Python payloads for locks.js & player-audit.js
                ui_res = _run_node_ui(
                    """
                    const tbl = L.lockList(p.list.episodes, { selected: null, onOpen: () => {}, renderDetail: () => document.createElement('div') });
                    const det = L.lockDetail(p.detail, { session: true, series: p.series, onRelock: () => {} });
                    const paBox = document.createElement('div');
                    renderPlayerAudit(paBox, {
                      cards: [p.audit], ref: `${p.audit.dossier_id}/${p.audit.hand_id}`, detail: p.audit, expanded: new Set(),
                    }, {
                      authenticated: true, available: true,
                      onSelect: () => {}, onTracker: () => {}, onOriginals: () => {},
                      onLogin: () => {}, onRefresh: () => {}, modelInputUrl: api.playerModelInputUrl,
                    });
                    const cf = walk(det).find((x) => x.tagName === 'details' && (x.className || '').includes('ld-counterfactual'));
                    const btns = walk(det).filter((x) => x.tagName === 'button').map((x) => x.textContent);
                    return {
                      listText: tbl.textContent,
                      detailSecs: sections(det),
                      detailText: det.textContent,
                      cfSummary: cf ? cf.childNodes[0].textContent : null,
                      btns,
                      paText: paBox.textContent,
                      sxLabel: CH.LABELS.SHADOW_EXITED,
                    };
                    """,
                    {"list": list_payload, "detail": detail_payload, "series": series_payload, "audit": audit_payload},
                )
                assert ui_res["sxLabel"] == "影子已退出（终态 · 已停止主动追踪）"
                assert "影子已退出" in ui_res["listText"]
                assert "AI 撤销 · 影子已退出（已停止主动追踪）" in ui_res["listText"]
                assert "影子退出（终态）" in ui_res["detailSecs"]
                assert "现在" not in ui_res["detailSecs"]
                assert "影子模拟双腿退出估值（非实盘成交）" in ui_res["detailText"]
                assert ui_res["cfSummary"] == "反事实对照：若未撤销持有至到期（仅供研究，不计入本局影子退出盈亏）"
                assert "以最近一点重新追踪" not in ui_res["btns"]
                assert "影子已退出 · 已停止主动追踪" in ui_res["paText"]
                assert "冻结影子退出净结果（EXECUTABLE）" in ui_res["paText"]
