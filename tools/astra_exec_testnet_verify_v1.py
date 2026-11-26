"""Deribit Testnet P3 verifier driving real ExecutionCoordinator + ExecutionStore + rebuild_episode_ledger (v1.2.0)."""

from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlencode
from urllib.request import Request, urlopen

if __package__:
    from . import astra_exec_contracts_v1 as contracts
    from . import astra_exec_store_v1 as exec_store
    from . import astra_execution_coordinator_v1 as coord_mod
else:
    ROOT_DIR = Path(__file__).resolve().parent
    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))
    import astra_exec_contracts_v1 as contracts  # type: ignore[no-redef]
    import astra_exec_store_v1 as exec_store  # type: ignore[no-redef]
    import astra_execution_coordinator_v1 as coord_mod  # type: ignore[no-redef]

D = contracts.D
ZERO = contracts.ZERO
SAT = contracts.SAT
SCHEMA = "astra_exec_testnet_verify@2.1.0"
SCENARIOS = ("public", "t1", "t2", "t3", "t4-single-itm", "t4-both-itm", "t5", "all")


def _http_get_json(url: str, params: Mapping[str, Any] | None = None, *, timeout_s: float = 10.0) -> Any:
    qs = urlencode(
        {k: json.dumps(v).lower() if isinstance(v, bool) else v for k, v in (params or {}).items() if v is not None}
    )
    full = f"{url}?{qs}" if qs else url
    req = Request(full, headers={"Accept": "application/json"}, method="GET")
    with urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, Mapping) and data.get("error"):
        raise RuntimeError(f"deribit_error:{data['error']}")
    return data.get("result") if isinstance(data, Mapping) else data


def verify_public_venue_schema(base_url: str, *, timeout_s: float = 10.0) -> dict[str, Any]:
    """Read-only schema probe against Deribit public REST v2 endpoints (depth=5)."""
    base = base_url.rstrip("/")
    server_ms = int(_http_get_json(f"{base}/api/v2/public/get_time", timeout_s=timeout_s))
    raw_insts = (
        _http_get_json(
            f"{base}/api/v2/public/get_instruments",
            {"currency": "BTC", "kind": "option", "expired": False},
            timeout_s=timeout_s,
        )
        or []
    )
    specs = [contracts.InstrumentSpec.from_deribit(item) for item in raw_insts[:5]]
    samples: list[dict[str, Any]] = []
    for sp in specs[:2]:
        ob = _http_get_json(
            f"{base}/api/v2/public/get_order_book",
            {"instrument_name": sp.instrument_name, "depth": 5},
            timeout_s=timeout_s,
        )
        bq = coord_mod._book_from_deribit(ob, sp.instrument_name, int(time.time() * 1000))
        samples.append(
            {
                "instrument_name": sp.instrument_name,
                "tick_size": contracts.dec_str(sp.tick_size),
                "min_trade_amount": contracts.dec_str(sp.min_trade_amount),
                "tick_steps_count": len(sp.tick_size_steps),
                "book_status": bq.status.value,
                "mark_price": contracts.dec_str(bq.mark_price),
                "depth_levels": {"bids": len(bq.bids), "asks": len(bq.asks)},
            }
        )
    return {
        "ok": True,
        "base_url": base,
        "server_time_ms": server_ms,
        "instrument_count": len(raw_insts),
        "samples": samples,
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def _build_scenario_dossier(
    dossier_root: Path,
    *,
    scenario: str,
    short_spec: contracts.InstrumentSpec,
    long_spec: contracts.InstrumentSpec,
    decided_at_ms: int,
    decision_credit_btc: Decimal,
) -> tuple[str, str, Path]:
    tag = hashlib.sha256(
        f"{scenario}:{short_spec.instrument_name}:{long_spec.instrument_name}:{decided_at_ms}".encode("utf-8")
    ).hexdigest()[:16]
    dossier_id = f"Testnet_BTC_{scenario}_{tag}"
    hand_id = f"hand_{tag}"
    ep_id = f"episode_{tag}"
    base = dossier_root / "dossiers" / dossier_id
    hand_dir = base / "hands" / hand_id
    ep_dir = base / "episodes" / ep_id
    (ep_dir / "events").mkdir(parents=True, exist_ok=True)

    dec_path = _write_json(
        hand_dir / "decision.json",
        {
            "attempt_id": f"dec_{tag}",
            "decided_at_ms": decided_at_ms,
            "judgment": {"action": "Accept", "side": short_spec.option_type},
            "raw_sha256": hashlib.sha256(f"raw:{tag}".encode("utf-8")).hexdigest(),
        },
    )
    cond_path = _write_json(
        ep_dir / "condition.json",
        {
            "contract": {
                "side": short_spec.option_type,
                "short_instrument": short_spec.instrument_name,
                "long_instrument": long_spec.instrument_name,
                "short_strike_usd": contracts.dec_str(short_spec.strike_usd),
                "long_strike_usd": contracts.dec_str(long_spec.strike_usd),
                "expiry_ms": int(short_spec.expiry_ms),
                "quantity_btc": 1.0,
            },
            "entry_at_ms": decided_at_ms + 1_000,
        },
    )
    _write_json(ep_dir / "episode.json", {"episode_id": ep_id, "hand_id": hand_id, "mode": "SHADOW_ONLY"})
    dec_sha = hashlib.sha256(dec_path.read_bytes()).hexdigest()
    cond_sha = hashlib.sha256(cond_path.read_bytes()).hexdigest()
    in_sha = hashlib.sha256(f"in:{tag}".encode("utf-8")).hexdigest()
    _write_json(
        hand_dir / "tracking" / "link.json",
        {
            "state": "SHADOW_LOCK_CREATED",
            "recorded_at_ms": decided_at_ms + 1_000,
            "execution_allowed": False,
            "origin": {
                "dossier_id": dossier_id,
                "hand_id": hand_id,
                "decision_id": f"dec_{tag}",
                "dossier_episode_id": ep_id,
                "decision_sha256": dec_sha,
                "condition_sha256": cond_sha,
                "input_sha256": in_sha,
            },
            "t0": {
                "economy": {
                    "net_credit_after_entry_fee_btc": float(decision_credit_btc),
                }
            },
        },
    )
    chash = contracts.compute_contract_hash(
        dossier_id=dossier_id,
        hand_id=hand_id,
        p5_episode_id=ep_id,
        decision_sha256=dec_sha,
        condition_sha256=cond_sha,
        short_instrument=short_spec.instrument_name,
        long_instrument=long_spec.instrument_name,
        expiry_ms=short_spec.expiry_ms,
    )
    return hand_id, chash, ep_dir


def _evaluate_candidate(
    adapter: Any,
    cfg: contracts.ExecConfig,
    short_inst: str,
    long_inst: str,
) -> tuple[dict[str, Any] | None, str | None]:
    facts = adapter.fetch_facts(short_inst, long_inst)
    if facts.api_error:
        return None, f"facts_error:{facts.api_error}"
    s_spec = facts.short_spec
    l_spec = facts.long_spec
    sb = facts.short_book
    lb = facts.long_book
    if s_spec is None or l_spec is None or sb is None or lb is None:
        return None, "missing_instrument_spec_or_book"
    if s_spec.option_type != l_spec.option_type or s_spec.expiry_ms != l_spec.expiry_ms:
        return None, "leg_type_or_expiry_mismatch"
    if (s_spec.expiry_ms - facts.server_time_ms) < cfg.min_entry_dte_ms:
        return None, f"dte_below_min_entry:{s_spec.expiry_ms - facts.server_time_ms}<{cfg.min_entry_dte_ms}"
    if s_spec.option_type == "put" and not (s_spec.strike_usd > l_spec.strike_usd):
        return None, "invalid_put_spread_strikes"
    if s_spec.option_type == "call" and not (s_spec.strike_usd < l_spec.strike_usd):
        return None, "invalid_call_spread_strikes"
    if sb.status != contracts.BookStatus.OK or lb.status != contracts.BookStatus.OK:
        return None, f"empty_book:short={sb.status.value}:long={lb.status.value}"
    if sb.bid_price is None or lb.ask_price is None:
        return None, "missing_executable_bbo"

    qty = max(cfg.initial_qty_btc, s_spec.min_trade_amount, l_spec.min_trade_amount)
    if qty != cfg.initial_qty_btc:
        return None, f"min_trade_amount_mismatch:{qty}!={cfg.initial_qty_btc}"
    if sb.bid_amount < qty or lb.ask_amount < qty:
        return None, f"insufficient_top_depth:short_bid={sb.bid_amount}:long_ask={lb.ask_amount}"

    p_ask_sweep = contracts.sweep_ask_price(lb, qty) or lb.ask_price
    p_bid_sweep = contracts.sweep_bid_price(sb, qty) or sb.bid_price
    prot_outlay = contracts.conservative_split_buy_outlay_btc(p_ask_sweep, qty, l_spec)
    if prot_outlay > contracts.ceil_1e8(qty * cfg.max_zero_rec_ep_unit_btc):
        return None, f"prot_unit_exceeds_zero_rec_cap:{prot_outlay}"

    short_net = contracts.conservative_split_sell_proceeds_btc(p_bid_sweep, qty, s_spec)
    est_net_per_btc = ((short_net - prot_outlay) / qty).quantize(SAT)
    if est_net_per_btc < cfg.min_net_credit_per_btc:
        return None, f"net_credit_below_floor:{est_net_per_btc}<{cfg.min_net_credit_per_btc}"

    spot = facts.spot_usd if facts.spot_usd > ZERO else s_spec.strike_usd
    short_mark = sb.mark_price or sb.ask_price or sb.bid_price or ZERO
    stress_loss, m_req, min_cash = contracts.compute_margin_and_cash_requirements(
        side=s_spec.option_type or "put",
        short_strike_usd=s_spec.strike_usd or ZERO,
        long_strike_usd=l_spec.strike_usd or ZERO,
        spot_usd=spot or ZERO,
        qty_btc=qty,
        unpaid_prot_cost_btc=prot_outlay,
        short_mark_btc=short_mark,
        portfolio_margining_enabled=facts.portfolio_margining_enabled,
        put_stress_spot_factor=cfg.put_stress_spot_factor,
        candidate_min_cash_pm_per_btc=cfg.candidate_min_cash_pm_per_btc,
        candidate_min_cash_sm_per_btc=cfg.candidate_min_cash_sm_per_btc,
    )
    bal = facts.balance_btc if facts.balance_btc is not None else facts.equity_btc
    if facts.available_funds_btc < m_req or bal < min_cash:
        return None, f"insufficient_available_funds_or_cash:avail={facts.available_funds_btc}<{m_req}:bal={bal}<{min_cash}"
    if facts.maintenance_margin_btc > ZERO and facts.equity_btc < facts.maintenance_margin_btc * cfg.min_margin_buffer_ratio:
        return None, "margin_buffer_below_minimum"

    req_budget = max(D("0.05"), stress_loss + prot_outlay + D("0.0020"))
    return {
        "facts": facts,
        "short_spec": s_spec,
        "long_spec": l_spec,
        "qty": qty,
        "decision_credit_per_btc": est_net_per_btc,
        "budget_cap_btc": min(bal, facts.available_funds_btc, req_budget)
        if min(bal, facts.available_funds_btc) >= (stress_loss + prot_outlay)
        else req_budget,
    }, None


def _scenario_pair_score(
    scenario: str,
    s_sp: contracts.InstrumentSpec,
    l_sp: contracts.InstrumentSpec,
    spot: Decimal,
    now_ms: int,
    min_dte_ms: int,
) -> tuple[int, int, Decimal]:
    if s_sp.option_type == "put":
        short_itm = s_sp.strike_usd > spot
        long_itm = l_sp.strike_usd > spot
    else:
        short_itm = s_sp.strike_usd < spot
        long_itm = l_sp.strike_usd < spot
    if scenario == "t3":
        fit = 0 if (not short_itm and not long_itm) else 2
    elif scenario == "t4-single-itm":
        fit = 0 if (short_itm and not long_itm) else 2
    elif scenario == "t4-both-itm":
        fit = 0 if (short_itm and long_itm) else 2
    else:
        fit = 0
    min_ttf = max(min_dte_ms + 60_000, 3_600_000 if scenario not in {"t3", "t4-single-itm", "t4-both-itm"} else min_dte_ms + 60_000)
    ttf_penalty = 0 if (s_sp.expiry_ms - now_ms) >= min_ttf else 1
    return (fit, ttf_penalty, abs(s_sp.strike_usd - spot))


def _discover_qualified_spread(
    adapter: Any,
    cfg: contracts.ExecConfig,
    *,
    scenario: str,
    short_instrument: str | None = None,
    long_instrument: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    if short_instrument and long_instrument:
        return _evaluate_candidate(adapter, cfg, short_instrument, long_instrument)

    base_facts = adapter.fetch_facts("", "")
    if base_facts.api_error:
        return None, f"account_facts_error:{base_facts.api_error}"
    if base_facts.positions or base_facts.open_orders:
        return None, f"dirty_subaccount_state:positions={len(base_facts.positions)}:open_orders={len(base_facts.open_orders)}"

    if not hasattr(adapter, "_raw_call"):
        return None, "explicit_instruments_required_for_adapter"
    raw_insts = (
        adapter._raw_call("public/get_instruments", {"currency": "BTC", "kind": "option", "expired": False}, private=False)
        or []
    )
    specs = [contracts.InstrumentSpec.from_deribit(r) for r in raw_insts]
    now_ms = int(base_facts.server_time_ms)
    spot = base_facts.spot_usd
    if spot <= ZERO:
        idx = adapter._raw_call("public/get_index_price", {"index_name": "btc_usd"}, private=False)
        spot = D((idx or {}).get("index_price") or "0")

    groups: dict[tuple[int, str], list[contracts.InstrumentSpec]] = {}
    for sp in specs:
        if sp.expiry_ms >= now_ms + cfg.min_entry_dte_ms + 60_000 and sp.option_type:
            groups.setdefault((sp.expiry_ms, sp.option_type), []).append(sp)

    pairs: list[tuple[tuple[int, int, Decimal, int], str, str]] = []
    for (exp_ms, opt_type), bucket in groups.items():
        bucket.sort(key=lambda x: x.strike_usd or ZERO)
        for i in range(len(bucket)):
            for j in range(i + 1, min(len(bucket), i + 4)):
                s_sp, l_sp = (bucket[j], bucket[i]) if opt_type == "put" else (bucket[i], bucket[j])
                sc = _scenario_pair_score(scenario, s_sp, l_sp, spot, now_ms, cfg.min_entry_dte_ms)
                if sc[0] != 0:
                    continue
                pairs.append(((*sc, exp_ms), s_sp.instrument_name, l_sp.instrument_name))
    pairs.sort(key=lambda item: item[0])

    reasons: list[str] = []
    for _, s_name, l_name in pairs[:15]:
        cand, err = _evaluate_candidate(adapter, cfg, s_name, l_name)
        if cand is not None:
            return cand, None
        if err and len(reasons) < 5:
            reasons.append(f"{s_name}/{l_name}:{err}")
    hint = (
        " [hint: t4-both-itm requires explicit --short-instrument/--long-instrument or near-expiry ITM pair within 0.0060 EP budget without relaxing ExecConfig]"
        if scenario == "t4-both-itm" and any("prot_unit_exceeds_zero_rec_cap" in r for r in reasons)
        else ""
    )
    return None, "no_qualified_testnet_spread" + (f" ({'; '.join(reasons)})" if reasons else "") + hint


def _is_real_deribit_adapter(adapter: Any) -> bool:
    inner = getattr(adapter, "_inner", None)
    return isinstance(adapter, coord_mod.DeribitRestAdapter) or isinstance(inner, coord_mod.DeribitRestAdapter)


class _PostSubmitFaultAdapter:
    """Forwards submit_order to the real adapter once, persists venue order, then injects one transport timeout."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.fault_fired = False
        self.injected_label: str | None = None

    def get_server_time_ms(self) -> int:
        return int(self._inner.get_server_time_ms())

    def disable_cancel_on_disconnect(self) -> bool:
        return bool(self._inner.disable_cancel_on_disconnect())

    def fetch_facts(
        self, short_instrument: str = "", long_instrument: str = "", *, need_tx_logs: bool = True, **kwargs: Any
    ) -> contracts.ExchangeFacts:
        try:
            return self._inner.fetch_facts(short_instrument, long_instrument, need_tx_logs=need_tx_logs, **kwargs)
        except TypeError:
            return self._inner.fetch_facts(short_instrument, long_instrument, need_tx_logs=need_tx_logs)

    def cancel_by_label(self, label: str) -> int:
        return int(self._inner.cancel_by_label(label))

    def get_order_state(self, order_id: str) -> contracts.ExchangeOrder:
        return self._inner.get_order_state(order_id)

    def get_order_state_by_label(self, label: str) -> list[contracts.ExchangeOrder]:
        return list(self._inner.get_order_state_by_label(label))

    def get_order_history_by_instrument(self, instrument_name: str, **kwargs: Any) -> list[contracts.ExchangeOrder]:
        if hasattr(self._inner, "get_order_history_by_instrument"):
            return list(self._inner.get_order_history_by_instrument(instrument_name, **kwargs))
        return []

    def submit_order(self, **kwargs: Any) -> contracts.ExchangeOrder:
        vo = self._inner.submit_order(**kwargs)
        if not self.fault_fired:
            self.fault_fired = True
            self.injected_label = str(kwargs.get("label") or vo.label)
            raise coord_mod.VenueTransportError("network_unknown", "t5_injected_post_submit_ack_timeout")
        return vo


def _evaluate_expiry_outcome(
    scenario: str, ep: contracts.EpisodeState, ledger: Mapping[str, Any]
) -> tuple[int, str, str]:
    if ep.matched_qty_btc <= ZERO:
        return 2, "MISMATCH", f"expected_matched_entry_qty_positive:got={ep.matched_qty_btc}"
    if ep.terminal_outcome != "EXPIRY_SETTLED":
        return 2, "MISMATCH", f"expected_EXPIRY_SETTLED:got={ep.terminal_outcome}"
    two_stage = ledger.get("two_stage_settlement") or {}
    stage1 = list(two_stage.get("stage1_rows") or [])
    stage2 = list(two_stage.get("stage2_delivery_rows") or [])
    xcheck = two_stage.get("crosscheck")
    if xcheck is None or D(xcheck.get("diff_btc") or "0") != ZERO:
        return 2, "MISMATCH", f"settlement_crosscheck_mismatch:{xcheck}"
    itm_stage1 = [r for r in stage1 if str(r.get("type") or "").lower() in {"exercise", "assignment"}]
    if scenario == "t3" and (itm_stage1 or stage2):
        return 2, "MISMATCH", f"expected_otm_zero_delivery:itm={len(itm_stage1)}:stage2={len(stage2)}"
    if scenario == "t4-single-itm" and (len(itm_stage1) != 1 or not stage2):
        return 2, "MISMATCH", f"expected_single_itm:itm={len(itm_stage1)}:stage2={len(stage2)}"
    if scenario == "t4-both-itm":
        assign_qty = sum((abs(D(r.get("amount") or "0")) for r in itm_stage1 if str(r.get("type") or "").lower() == "assignment"), ZERO)
        exerc_qty = sum((abs(D(r.get("amount") or "0")) for r in itm_stage1 if str(r.get("type") or "").lower() == "exercise"), ZERO)
        zero_net = assign_qty > ZERO and exerc_qty > ZERO and assign_qty == exerc_qty
        if len(itm_stage1) != 2 or (not stage2 and not zero_net):
            return 2, "MISMATCH", f"expected_both_itm:itm={len(itm_stage1)}:stage2={len(stage2)}:zero_net={zero_net}"
    return 0, "PASSED", "expiry_settlement_verified"


def _make_coord(
    *,
    db_path: Path,
    dossier_root: Path,
    status_path: Path,
    subaccount_id: str,
    adapter: Any,
    cfg: contracts.ExecConfig,
) -> coord_mod.ExecutionCoordinator:
    st = exec_store.ExecutionStore(db_path)
    coord = coord_mod.ExecutionCoordinator(
        mode="TESTNET",
        env_tag="tn",
        subaccount_id=subaccount_id,
        store=st,
        adapter=adapter,
        status_path=status_path,
        dossier_root=dossier_root,
        config=cfg,
    )
    use_real_clock = _is_real_deribit_adapter(adapter)
    init_now = None if use_real_clock else adapter.get_server_time_ms()
    coord.startup_init(now_ms=init_now)
    coord.handle_health_fresh(now_ms=init_now)
    return coord


def run_testnet_scenario(
    scenario: str,
    *,
    adapter: Any,
    subaccount_id: str,
    work_dir: Path,
    short_instrument: str | None = None,
    long_instrument: str | None = None,
    resume_db: Path | None = None,
    wait_seconds: int = 30,
    poll_interval_s: float = 2.0,
    step_hook: Callable[[str, coord_mod.ExecutionCoordinator, int], None] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run one independent P3 scenario (t1/t2/t3/t4-single-itm/t4-both-itm/t5) through real Coordinator + Store."""
    work_dir.mkdir(parents=True, exist_ok=True)
    cfg = contracts.ExecConfig()
    db_path = Path(resume_db) if resume_db else (work_dir / f"{scenario}_exec.sqlite3")
    dossier_root = work_dir / f"{scenario}_dossiers"
    status_path = work_dir / f"{scenario}_public_status.json"

    coord: coord_mod.ExecutionCoordinator | None = None
    resumed_existing = bool(resume_db)
    observed_submit_unknown = False
    ep_id = ""
    ep_dir: Path | None = None

    use_real_clock = _is_real_deribit_adapter(adapter)
    try:
        if not resumed_existing:
            cand, err = _discover_qualified_spread(
                adapter,
                cfg,
                scenario=scenario,
                short_instrument=short_instrument,
                long_instrument=long_instrument,
            )
            if cand is None:
                return 2, {
                    "schema": SCHEMA,
                    "scenario": scenario,
                    "status": "BLOCKED",
                    "reason": err or "no_qualified_testnet_spread",
                }
            s_spec = cand["short_spec"]
            l_spec = cand["long_spec"]
            now_ms = int(adapter.get_server_time_ms())
            _hand_id, _chash, ep_dir = _build_scenario_dossier(
                dossier_root,
                scenario=scenario,
                short_spec=s_spec,
                long_spec=l_spec,
                decided_at_ms=now_ms - 15_000,
                decision_credit_btc=cand["decision_credit_per_btc"],
            )
            run_adapter: Any = _PostSubmitFaultAdapter(adapter) if scenario == "t5" else adapter
            coord = _make_coord(
                db_path=db_path,
                dossier_root=dossier_root,
                status_path=status_path,
                subaccount_id=subaccount_id,
                adapter=run_adapter,
                cfg=cfg,
            )
            try:
                coord.handle_capital_policy(
                    {
                        "entry_armed": True,
                        "budget_cap_btc": contracts.dec_str(cand["budget_cap_btc"]),
                        "max_qty_btc": contracts.dec_str(cand["qty"]),
                        "operator": "testnet_verify_v2",
                        "note": f"testnet_{scenario}",
                    },
                    now_ms=None if use_real_clock else run_adapter.get_server_time_ms(),
                    run_tick_after=True,
                )
            except (ValueError, RuntimeError) as exc:
                return 2, {
                    "schema": SCHEMA,
                    "scenario": scenario,
                    "status": "BLOCKED",
                    "reason": f"no_qualified_testnet_spread:{exc}",
                }
            ep_now = coord.store.get_active_episode() or coord.store.get_latest_episode()
            if ep_now is None:
                return 2, {
                    "schema": SCHEMA,
                    "scenario": scenario,
                    "status": "BLOCKED",
                    "reason": f"episode_not_admitted:{coord._last_reject_reason}",
                }
            ep_id = ep_now.episode_id
            if scenario == "t5":
                cmds_now = coord.store.get_commands(ep_id)
                observed_submit_unknown = any(
                    c.status == contracts.CommandStatus.SUBMIT_UNKNOWN
                    or "t5_injected_post_submit_ack_timeout" in str(c.last_error or "")
                    for c in cmds_now
                )
                if observed_submit_unknown:
                    coord.store.close()
                    coord = _make_coord(
                        db_path=db_path,
                        dossier_root=dossier_root,
                        status_path=status_path,
                        subaccount_id=subaccount_id,
                        adapter=adapter,
                        cfg=cfg,
                    )
        else:
            if not db_path.exists():
                return 2, {
                    "schema": SCHEMA,
                    "scenario": scenario,
                    "status": "BLOCKED",
                    "reason": f"resume_db_missing:{db_path}",
                }
            if not dossier_root.exists() and (db_path.parent / f"{scenario}_dossiers").is_dir():
                dossier_root = db_path.parent / f"{scenario}_dossiers"
            coord = _make_coord(
                db_path=db_path,
                dossier_root=dossier_root,
                status_path=status_path,
                subaccount_id=subaccount_id,
                adapter=adapter,
                cfg=cfg,
            )
            target = coord.store.get_active_episode() or coord.store.get_latest_episode()
            if target is None:
                return 2, {
                    "schema": SCHEMA,
                    "scenario": scenario,
                    "status": "BLOCKED",
                    "reason": "no_episode_in_resume_db",
                }
            ep_id = target.episode_id
            cmds_res = coord.store.get_commands(ep_id)
            observed_submit_unknown = any(
                c.status == contracts.CommandStatus.SUBMIT_UNKNOWN
                or "t5_injected_post_submit_ack_timeout" in str(c.last_error or "")
                for c in cmds_res
            )
            cand_ep_dir = dossier_root / "dossiers" / target.dossier_id / "episodes" / target.p5_episode_id
            if cand_ep_dir.is_dir():
                ep_dir = cand_ep_dir

        deadline = time.monotonic() + max(1.0, float(wait_seconds))
        revoke_written = False
        restarted_after_t5_fault = not (scenario == "t5" and not resumed_existing and not observed_submit_unknown)
        max_steps = 40 if step_hook is not None else 10_000
        step_idx = 0
        while step_idx < max_steps:
            step_idx += 1
            ep = coord.store.get_episode(ep_id)
            assert ep is not None
            if (
                scenario in {"t2", "t5"}
                and not revoke_written
                and ep.phase == contracts.LifecyclePhase.TP_MANAGED
                and ep_dir is not None
            ):
                rev_ms = int(adapter.get_server_time_ms())
                _write_json(
                    ep_dir / "events" / f"ai_rev_{scenario}.json",
                    {
                        "kind": "AI_REVOKE",
                        "recorded_at_ms": rev_ms,
                        "decided_at_ms": rev_ms,
                        "body": {
                            "review_id": f"rev_{scenario}",
                            "verdict": "REVOKE",
                            "shadow_action": "REVOKE_ALIGNED",
                        },
                    },
                )
                revoke_written = True
            if step_hook is not None:
                step_hook(scenario, coord, step_idx)
            tick_now = None if use_real_clock else int(adapter.get_server_time_ms())
            coord.handle_health_fresh(now_ms=tick_now)
            coord.run_tick(now_ms=tick_now)
            if scenario == "t5" and not restarted_after_t5_fault:
                cmds_loop = coord.store.get_commands(ep_id)
                if any(
                    c.status == contracts.CommandStatus.SUBMIT_UNKNOWN
                    or "t5_injected_post_submit_ack_timeout" in str(c.last_error or "")
                    for c in cmds_loop
                ):
                    observed_submit_unknown = True
                    restarted_after_t5_fault = True
                    coord.store.close()
                    coord = _make_coord(
                        db_path=db_path,
                        dossier_root=dossier_root,
                        status_path=status_path,
                        subaccount_id=subaccount_id,
                        adapter=adapter,
                        cfg=cfg,
                    )
            ep = coord.store.get_episode(ep_id)
            assert ep is not None
            if ep.cashflow_settled:
                break
            if time.monotonic() >= deadline:
                break
            if poll_interval_s > 0:
                time.sleep(poll_interval_s)

        ep = coord.store.get_episode(ep_id)
        assert ep is not None
        facts = adapter.fetch_facts(ep.short_instrument, ep.long_instrument)
        ledger = coord.store.rebuild_episode_ledger(ep_id, facts=facts)
        cmds = coord.store.get_commands(ep_id)

        if not ep.cashflow_settled:
            if scenario == "t1" and ep.phase == contracts.LifecyclePhase.TP_MANAGED:
                tp_cmds = [c for c in cmds if c.purpose == "TP" and c.status == contracts.CommandStatus.OPEN]
                return 2, {
                    "schema": SCHEMA,
                    "scenario": scenario,
                    "episode_id": ep_id,
                    "status": "AWAITING_TP_FILL",
                    "resume_db": str(db_path),
                    "tp_labels": [c.label for c in tp_cmds],
                    "ledger": ledger,
                }
            if scenario in {"t3", "t4-single-itm", "t4-both-itm"} and int(adapter.get_server_time_ms()) < ep.expiry_ms:
                return 2, {
                    "schema": SCHEMA,
                    "scenario": scenario,
                    "episode_id": ep_id,
                    "status": "AWAITING_EXPIRY",
                    "expiry_ms": ep.expiry_ms,
                    "resume_db": str(db_path),
                    "ledger": ledger,
                }
            return 2, {
                "schema": SCHEMA,
                "scenario": scenario,
                "episode_id": ep_id,
                "status": "INCOMPLETE",
                "phase": ep.phase.value,
                "resume_db": str(db_path),
                "ledger": ledger,
            }

        if not ledger["cashflow_settled"] or ledger["accounting"]["trades_missing_tx_log"]:
            return 2, {
                "schema": SCHEMA,
                "scenario": scenario,
                "episode_id": ep_id,
                "status": "FAILED",
                "reason": "ledger_incomplete_or_missing_tx_log",
                "ledger": ledger,
            }

        if scenario == "t1":
            tp_filled = any(c.purpose == "TP" and c.filled_amount > ZERO for c in cmds)
            ok = ep.matched_qty_btc > ZERO and tp_filled and ep.terminal_outcome == "EARLY_FLAT_SETTLED"
            return (0 if ok else 2), {
                "schema": SCHEMA,
                "scenario": scenario,
                "episode_id": ep_id,
                "status": "PASSED" if ok else "FAILED",
                "reason": "tp_and_lx_settled" if ok else f"unexpected_outcome:{ep.terminal_outcome}:matched={ep.matched_qty_btc}:tp_filled={tp_filled}",
                "db_path": str(db_path),
                "ledger": ledger,
            }

        if scenario in {"t2", "t5"}:
            rx_filled = any(c.purpose == "RX" and c.filled_amount > ZERO for c in cmds)
            post_latch_tp = any(
                c.purpose == "TP"
                and c.filled_amount > ZERO
                and (c.updated_at_ms or 0) >= (ep.exit_started_at_ms or 0)
                for c in cmds
            )
            rx_ok = (
                ep.matched_qty_btc > ZERO
                and ep.exit_latched
                and ep.exit_latch_reason == "AI_REVOKE"
                and (rx_filled or post_latch_tp)
            )
            extra_ok = (
                True
                if scenario == "t2"
                else (observed_submit_unknown and len([c for c in cmds if c.purpose == "EP"]) == 1)
            )
            ok = rx_ok and ep.terminal_outcome == "EARLY_FLAT_SETTLED" and extra_ok
            return (0 if ok else 2), {
                "schema": SCHEMA,
                "scenario": scenario,
                "episode_id": ep_id,
                "status": "PASSED" if ok else "FAILED",
                "observed_submit_unknown": observed_submit_unknown,
                "command_count": len(cmds),
                "db_path": str(db_path),
                "ledger": ledger,
            }

        if scenario in {"t3", "t4-single-itm", "t4-both-itm"}:
            code, st_label, reason = _evaluate_expiry_outcome(scenario, ep, ledger)
            return code, {
                "schema": SCHEMA,
                "scenario": scenario,
                "episode_id": ep_id,
                "status": st_label,
                "reason": reason,
                "db_path": str(db_path),
                "ledger": ledger,
            }

        return 2, {"schema": SCHEMA, "scenario": scenario, "status": "FAILED", "reason": f"unknown_scenario:{scenario}"}
    finally:
        if coord is not None:
            coord.store.close()


def run_testnet_suite(
    *,
    adapter: Any,
    subaccount_id: str,
    work_dir: Path,
    short_instrument: str | None = None,
    long_instrument: str | None = None,
    resume_db: Path | None = None,
    wait_seconds: int = 30,
    poll_interval_s: float = 2.0,
    step_hook: Callable[[str, coord_mod.ExecutionCoordinator, int], None] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run all P3 execution scenarios (t1, t2, t3, t4-single-itm, t4-both-itm, t5) independently."""
    results: dict[str, Any] = {}
    overall_code = 0
    for sc in ("t1", "t2", "t3", "t4-single-itm", "t4-both-itm", "t5"):
        code, rep = run_testnet_scenario(
            sc,
            adapter=adapter,
            subaccount_id=subaccount_id,
            work_dir=work_dir / sc,
            short_instrument=short_instrument,
            long_instrument=long_instrument,
            resume_db=resume_db,
            wait_seconds=wait_seconds,
            poll_interval_s=poll_interval_s,
            step_hook=step_hook,
        )
        results[sc] = {"exit_code": code, **rep}
        if code != 0:
            overall_code = code
    return overall_code, {
        "schema": SCHEMA,
        "status": "PASSED" if overall_code == 0 else "BLOCKED",
        "scenarios": results,
    }


def run_testnet_verification(
    *,
    base_url: str = coord_mod.TESTNET_BASE_URL,
    subaccount_id: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    public_only: bool = False,
    scenario: str = "t1",
    work_dir: Path | None = None,
    short_instrument: str | None = None,
    long_instrument: str | None = None,
    resume_db: Path | None = None,
    wait_seconds: int = 30,
    poll_interval_s: float = 2.0,
    adapter: Any = None,
    step_hook: Callable[[str, coord_mod.ExecutionCoordinator, int], None] | None = None,
) -> tuple[int, dict[str, Any]]:
    coord_mod.validate_endpoint_for_mode("TESTNET", base_url)
    pub = verify_public_venue_schema(base_url) if adapter is None else {"ok": True, "injected_adapter": True}
    if public_only or scenario == "public":
        return 0, {"schema": SCHEMA, "status": "PASS_PUBLIC_ONLY", "public": pub}

    sub_id = subaccount_id if subaccount_id is not None else os.environ.get("DERIBIT_TESTNET_SUBACCOUNT_ID", "")
    if adapter is None:
        cid = client_id or os.environ.get("DERIBIT_TESTNET_CLIENT_ID")
        csec = client_secret or os.environ.get("DERIBIT_TESTNET_CLIENT_SECRET")
        if not cid or not csec:
            return 2, {"schema": SCHEMA, "status": "BLOCKED", "reason": "missing_testnet_credentials", "public": pub}
        adapter = coord_mod.DeribitRestAdapter(
            mode="TESTNET",
            base_url=base_url,
            client_id=cid,
            client_secret=csec,
            subaccount_id=sub_id,
        )

    wdir = Path(work_dir) if work_dir else (Path(tempfile.gettempdir()) / f"astra_testnet_verify_{os.getpid()}")
    if scenario == "all":
        code, suite_rep = run_testnet_suite(
            adapter=adapter,
            subaccount_id=sub_id,
            work_dir=wdir,
            short_instrument=short_instrument,
            long_instrument=long_instrument,
            resume_db=resume_db,
            wait_seconds=wait_seconds,
            poll_interval_s=poll_interval_s,
            step_hook=step_hook,
        )
        suite_rep["public"] = pub
        return code, suite_rep

    code, sc_rep = run_testnet_scenario(
        scenario,
        adapter=adapter,
        subaccount_id=sub_id,
        work_dir=wdir,
        short_instrument=short_instrument,
        long_instrument=long_instrument,
        resume_db=resume_db,
        wait_seconds=wait_seconds,
        poll_interval_s=poll_interval_s,
        step_hook=step_hook,
    )
    sc_rep["public"] = pub
    return code, sc_rep


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Deribit Testnet P3 verifier (real ExecutionCoordinator + ExecutionStore + Ledger)"
    )
    ap.add_argument("--base-url", default=coord_mod.TESTNET_BASE_URL)
    ap.add_argument("--subaccount-id", default=None)
    ap.add_argument("--public-only", action="store_true")
    ap.add_argument("--scenario", choices=list(SCENARIOS), default="t1")
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--short-instrument", default=None)
    ap.add_argument("--long-instrument", default=None)
    ap.add_argument("--resume-db", default=None)
    ap.add_argument("--wait-seconds", type=int, default=30)
    args = ap.parse_args(argv)
    try:
        code, rep = run_testnet_verification(
            base_url=args.base_url,
            subaccount_id=args.subaccount_id,
            public_only=args.public_only,
            scenario=args.scenario,
            work_dir=Path(args.work_dir) if args.work_dir else None,
            short_instrument=args.short_instrument,
            long_instrument=args.long_instrument,
            resume_db=Path(args.resume_db) if args.resume_db else None,
            wait_seconds=args.wait_seconds,
        )
    except coord_mod.EnvironmentGuardError as exc:
        print(json.dumps({"schema": SCHEMA, "status": "REFUSED", "reason": str(exc)}, ensure_ascii=False, indent=2))
        return 3
    print(json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True))
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
