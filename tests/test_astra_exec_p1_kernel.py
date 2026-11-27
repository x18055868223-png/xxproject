"""LIVE-P1 deterministic kernel, 4-table WAL store, TP/RX/LX v1.2.0 rules, and UNKNOWN counterexample tests."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import random
import sqlite3
import sys
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from tools import astra_exec_contracts_v1 as contracts  # noqa: E402
from tools import astra_exec_policy_v1 as policy  # noqa: E402
from tools import astra_exec_store_v1 as store  # noqa: E402
import exec_sim_oracle as sim  # noqa: E402

ZERO = contracts.ZERO
ONE = contracts.ONE
D = contracts.D


def _adapt_order(oo: sim.OracleExchangeOrder) -> contracts.ExchangeOrder:
    return contracts.ExchangeOrder(
        order_id=oo.order_id,
        label=oo.label,
        instrument_name=oo.instrument_name,
        direction=oo.direction,
        amount=oo.amount,
        filled_amount=oo.filled_amount,
        price=oo.price,
        average_price=oo.average_price,
        order_state=oo.order_state,
        time_in_force=oo.time_in_force,
        reduce_only=oo.reduce_only,
        post_only=oo.post_only,
        creation_timestamp_ms=oo.creation_timestamp_ms,
        last_update_timestamp_ms=oo.last_update_timestamp_ms,
    )


def _adapt_facts(
    ofacts: sim.OracleExchangeFacts,
    *,
    venue: sim.SimulatedDeribitVenue | None = None,
    cmds: list[contracts.ExecCommand] | None = None,
    now_ms_override: int | None = None,
    probe_override: dict[str, contracts.OrderReconcileProbe] | None = None,
) -> contracts.ExchangeFacts:
    def _spec(ospec: sim.OracleInstrumentSpec) -> contracts.InstrumentSpec:
        return contracts.InstrumentSpec(
            instrument_name=ospec.instrument_name,
            kind=ospec.kind,
            settlement_period=ospec.settlement_period,
            strike_usd=ospec.strike_usd,
            option_type=ospec.option_type,
            expiry_ms=ospec.expiry_ms,
            tick_size=ospec.tick_size,
            min_trade_amount=ospec.min_trade_amount,
            tick_size_steps=tuple(
                contracts.TickStep(above_price=s.above_price, tick_size=s.tick_size)
                for s in ospec.tick_size_steps
            ),
            maker_commission=ospec.maker_commission,
            taker_commission=ospec.taker_commission,
            fee_cap_ratio=ospec.fee_cap_ratio,
            delivery_fee_rate=ospec.delivery_fee_rate,
            contract_size=ospec.contract_size,
        )

    def _book(ob: sim.OracleBookQuote | None) -> contracts.BookQuote | None:
        if ob is None:
            return None
        return contracts.BookQuote(
            instrument_name=ob.instrument_name,
            bid_price=ob.bid_price,
            bid_amount=ob.bid_amount,
            ask_price=ob.ask_price,
            ask_amount=ob.ask_amount,
            mark_price=ob.mark_price,
            min_price=ob.min_price,
            max_price=ob.max_price,
            timestamp_ms=ob.timestamp_ms,
            status=contracts.BookStatus(ob.status.value),
            gap_reason=ob.gap_reason,
            fetched_at_ms=ob.fetched_at_ms or ob.timestamp_ms,
            bids=ob.bids,
            asks=ob.asks,
        )

    probes: dict[str, contracts.OrderReconcileProbe] = dict(probe_override or {})
    if venue is not None and cmds and probe_override is None and ofacts.open_orders_complete:
        open_oids = {o.order_id for o in ofacts.open_orders}
        open_lbls = {o.label for o in ofacts.open_orders if o.label}
        now_use = now_ms_override if now_ms_override is not None else ofacts.asof_ms
        for cmd in cmds:
            if not cmd.is_open_or_unknown:
                continue
            if (cmd.venue_order_id and cmd.venue_order_id in open_oids) or (cmd.label and cmd.label in open_lbls):
                continue
            cmd_age = max(0, now_use - cmd.created_at_ms)
            matched: dict[str, contracts.ExchangeOrder] = {}
            os_status = "NOT_QUERIED"
            lbl_status = "NOT_QUERIED"
            if cmd.venue_order_id:
                try:
                    vo = _adapt_order(venue.get_order_state(cmd.venue_order_id))
                    os_status = "FOUND"
                    matched[vo.order_id] = vo
                except sim.DeribitApiError as exc:
                    os_status = "NOT_FOUND" if exc.code in {10004, 11000} else "ERROR"
                except sim.RateLimitError:
                    os_status = "ERROR"
            if os_status != "FOUND":
                if cmd_age < 1_500_000:
                    try:
                        found = [_adapt_order(o) for o in venue.get_order_state_by_label(cmd.label)]
                        if found:
                            lbl_status = "FOUND"
                            for o in found:
                                matched[o.order_id] = o
                        else:
                            lbl_status = "EMPTY_CONFIRMED"
                    except sim.DeribitApiError as exc:
                        lbl_status = "EMPTY_CONFIRMED" if exc.code in {10004, 11000} else "ERROR"
                    except sim.RateLimitError:
                        lbl_status = "ERROR"
                else:
                    lbl_status = "EXPIRED_HOT_CACHE"
            inst_recent_ok = False
            inst_arch_ok = False
            if os_status != "FOUND" or cmd.venue_order_id is None:
                try:
                    off = 0
                    while True:
                        pg = [
                            _adapt_order(o)
                            for o in venue.get_order_history_by_instrument(
                                cmd.instrument_name, count=200, offset=off, historical=False
                            )
                        ]
                        for o in pg:
                            if (cmd.venue_order_id and o.order_id == cmd.venue_order_id) or o.label == cmd.label:
                                matched[o.order_id] = o
                        if len(pg) < 200:
                            inst_recent_ok = True
                            break
                        off += len(pg)
                except (sim.DeribitApiError, sim.RateLimitError):
                    inst_recent_ok = False
                if cmd_age >= 1_200_000 or os_status == "NOT_FOUND":
                    try:
                        off = 0
                        while True:
                            pg = [
                                _adapt_order(o)
                                for o in venue.get_order_history_by_instrument(
                                    cmd.instrument_name, count=200, offset=off, historical=True
                                )
                            ]
                            for o in pg:
                                if (cmd.venue_order_id and o.order_id == cmd.venue_order_id) or o.label == cmd.label:
                                    matched[o.order_id] = o
                            if len(pg) < 200:
                                inst_arch_ok = True
                                break
                            off += len(pg)
                    except (sim.DeribitApiError, sim.RateLimitError):
                        inst_arch_ok = False
            probes[cmd.cmd_id] = contracts.OrderReconcileProbe(
                cmd_id=cmd.cmd_id,
                order_state_status=os_status,
                label_query_status=lbl_status,
                inst_history_recent_complete=inst_recent_ok,
                inst_history_archive_complete=inst_arch_ok,
                matched_orders=tuple(matched.values()),
                fresh_est_server_ms=venue.get_server_time(),
            )

    return contracts.ExchangeFacts(
        asof_ms=ofacts.asof_ms,
        server_time_ms=ofacts.server_time_ms,
        spot_usd=ofacts.spot_usd,
        equity_btc=ofacts.equity_btc,
        available_funds_btc=ofacts.available_funds_btc,
        initial_margin_btc=ofacts.initial_margin_btc,
        maintenance_margin_btc=ofacts.maintenance_margin_btc,
        positions=tuple(
            contracts.ExchangePosition(
                instrument_name=p.instrument_name,
                kind=p.kind,
                size=p.size,
                average_price=p.average_price,
                mark_price=p.mark_price,
                index_price=p.index_price,
            )
            for p in ofacts.positions
        ),
        open_orders=tuple(_adapt_order(o) for o in ofacts.open_orders),
        open_orders_complete=ofacts.open_orders_complete,
        history_complete=ofacts.history_complete,
        recent_orders_by_label={k: tuple(_adapt_order(o) for o in v) for k, v in ofacts.recent_orders_by_label.items()},
        recent_trades=tuple(
            contracts.VenueTrade(
                trade_id=t.trade_id,
                order_id=t.order_id,
                label=t.label,
                instrument_name=t.instrument_name,
                direction=t.direction,
                amount=t.amount,
                price=t.price,
                fee=t.fee,
                fee_currency=t.fee_currency,
                timestamp_ms=t.timestamp_ms,
                liquidity=t.liquidity,
            )
            for t in ofacts.recent_trades
        ),
        tx_logs=tuple(
            contracts.TxLogEntry(
                id=tx.id,
                type=tx.type,
                instrument_name=tx.instrument_name,
                timestamp_ms=tx.timestamp_ms,
                amount=tx.amount,
                price=tx.price,
                cashflow=tx.cashflow,
                change=tx.change,
                commission=tx.commission,
                currency=tx.currency,
                trade_id=tx.trade_id,
                order_id=tx.order_id,
                settlement_price=tx.settlement_price,
                info=dict(tx.info),
            )
            for tx in ofacts.tx_logs
        ),
        tx_logs_complete=ofacts.tx_logs_complete,
        short_book=_book(ofacts.short_book),
        long_book=_book(ofacts.long_book),
        short_spec=_spec(ofacts.short_spec),
        long_spec=_spec(ofacts.long_spec),
        subaccount_id=ofacts.subaccount_id,
        api_error=ofacts.api_error,
        rate_limited_until_ms=ofacts.rate_limited_until_ms,
        balance_btc=ofacts.balance_btc,
        portfolio_margining_enabled=ofacts.portfolio_margining_enabled,
        margin_model=ofacts.margin_model,
        trades_complete=ofacts.history_complete,
        positions_complete=True,
        clock_rtt_ok=True,
        order_probes=probes,
    )


def _refresh_books(venue: sim.SimulatedDeribitVenue, ep: contracts.EpisodeState) -> None:
    for inst in (ep.short_instrument, ep.long_instrument):
        b = venue._books.get(inst)
        if b is not None:
            venue.set_book(
                inst,
                bids=b.bids,
                asks=b.asks,
                bid_price=b.bid_price,
                bid_amount=b.bid_amount,
                ask_price=b.ask_price,
                ask_amount=b.ask_amount,
                mark_price=b.mark_price,
                min_price=b.min_price,
                max_price=b.max_price,
                status=b.status.value,
                gap_reason=b.gap_reason,
                fetched_at_ms=venue.client_clock_ms(),
                match_resting=False,
            )


def _setup_put_spread(
    *,
    t0: int = 1_795_450_340_000,
    expiry_ms: int = 1_795_507_200_000,
    settlement_period: str = "day",
    target_qty: Decimal = D("0.1"),
    max_entry_slice: Decimal = D("0.1"),
    min_net_credit: Decimal = D("0.0008"),
    budget_cap_btc: Decimal = D("0.10"),
    initial_balance: Decimal = D("1.0"),
    portfolio_margining_enabled: bool = True,
    c_decision_ref_btc: Decimal | None = D("0.0028"),
) -> tuple[sim.SimulatedDeribitVenue, contracts.EpisodeState, contracts.CapitalAuthorization, contracts.ExecConfig]:
    short_inst = "BTC-25NOV26-62000-P"
    long_inst = "BTC-25NOV26-60000-P"
    venue = sim.SimulatedDeribitVenue(
        initial_time_ms=t0,
        initial_balance_btc=initial_balance,
        subaccount_id="sub-tn-1",
        portfolio_margining_enabled=portfolio_margining_enabled,
        margin_model="PM" if portfolio_margining_enabled else "SM",
    )
    venue.set_spot_usd("62785")
    venue.add_option_instrument(
        short_inst,
        strike_usd=62000,
        option_type="put",
        expiry_ms=expiry_ms,
        settlement_period=settlement_period,
        tick_size="0.0001",
        min_trade_amount="0.1",
        tick_size_steps=(("0.005", "0.0005"),),
    )
    venue.add_option_instrument(
        long_inst,
        strike_usd=60000,
        option_type="put",
        expiry_ms=expiry_ms,
        settlement_period=settlement_period,
        tick_size="0.0001",
        min_trade_amount="0.1",
        tick_size_steps=(("0.005", "0.0005"),),
    )
    venue.set_book(
        short_inst,
        bid="0.0044",
        bid_size="5.0",
        ask="0.0046",
        ask_size="5.0",
        mark_price="0.0045",
        min_price="0.0001",
        max_price="0.0500",
    )
    venue.set_book(
        long_inst,
        bid="0.0011",
        bid_size="5.0",
        ask="0.0013",
        ask_size="5.0",
        mark_price="0.0012",
        min_price="0.0001",
        max_price="0.0500",
    )

    chash = contracts.compute_contract_hash(
        dossier_id="Deribit_BTC_20261125T080000Z",
        hand_id="hand_a050f60ea6bb7d73acf68a3f",
        p5_episode_id="episode_95ef7494c06605a6b7e59e9c",
        decision_sha256="d" * 64,
        condition_sha256="c" * 64,
        short_instrument=short_inst,
        long_instrument=long_inst,
        expiry_ms=expiry_ms,
    )
    ep = contracts.EpisodeState(
        episode_id="ep_20261124_001",
        env="tn",
        subaccount_id="sub-tn-1",
        dossier_id="Deribit_BTC_20261125T080000Z",
        hand_id="hand_a050f60ea6bb7d73acf68a3f",
        p5_episode_id="episode_95ef7494c06605a6b7e59e9c",
        side="put",
        short_instrument=short_inst,
        long_instrument=long_inst,
        short_strike_usd=D("62000"),
        long_strike_usd=D("60000"),
        expiry_ms=expiry_ms,
        contract_hash=chash,
        phase=contracts.LifecyclePhase.ENTRY,
        created_at_ms=t0,
        auth_epoch=1,
        target_qty_btc=target_qty,
        min_net_credit_per_btc=min_net_credit,
        max_zero_rec_ep_outlay_btc=contracts.ceil_1e8(target_qty * D("0.0060")),
        effective_budget_at_entry_btc=budget_cap_btc,
        margin_model_at_entry="PM" if portfolio_margining_enabled else "SM",
        entry_spot_usd=D("62785"),
        decided_at_ms=t0 - 15_000,
        link_recorded_at_ms=t0 - 10_000,
        c_decision_ref_btc=c_decision_ref_btc,
    )
    auth = contracts.CapitalAuthorization(
        entry_armed=True,
        budget_cap_btc=budget_cap_btc,
        max_qty_btc=target_qty,
        grant_baseline_pnl_btc=ZERO,
        auth_epoch=1,
        updated_at_ms=t0,
        operator="test",
        note="armed",
    )
    cfg = contracts.ExecConfig(
        initial_qty_btc=target_qty,
        max_entry_slice_btc=max_entry_slice,
        min_net_credit_per_btc=min_net_credit,
    )
    return venue, ep, auth, cfg


def _step_once(
    st: store.ExecutionStore,
    venue: sim.SimulatedDeribitVenue,
    ep_id: str,
    cfg: contracts.ExecConfig,
    *,
    p5_contract: contracts.P5DecisionContract | None = None,
    p5_condition_triggered: bool = False,
    page_limit: int = 100,
    follow_pagination: bool = True,
    include_lag_cursor: bool = False,
    now_ms_override: int | None = None,
    probe_override: dict[str, contracts.OrderReconcileProbe] | None = None,
    recurse_entry: bool = True,
) -> policy.StepResult:
    ep = st.get_episode(ep_id)
    assert ep is not None
    ofacts = venue.build_facts(
        ep.short_instrument,
        ep.long_instrument,
        page_limit=page_limit,
        follow_pagination=follow_pagination,
        include_lag_cursor=include_lag_cursor,
    )
    now_ms = ofacts.asof_ms if now_ms_override is None else now_ms_override
    cmds = st.get_commands(ep_id)
    facts = _adapt_facts(
        ofacts,
        venue=venue if probe_override is None else None,
        cmds=cmds,
        now_ms_override=now_ms,
        probe_override=probe_override,
    )

    st.ingest_facts_events(ep, facts, now_ms=now_ms)
    events = st.get_venue_events(ep_id)
    writes_off, _ = st.writes_disabled()
    auth, _pnl, b_eff = st.get_effective_budget_state()
    if auth.budget_cap_btc <= ZERO:
        auth = contracts.CapitalAuthorization(
            entry_armed=True,
            budget_cap_btc=ep.effective_budget_at_entry_btc,
            max_qty_btc=ep.target_qty_btc,
            auth_epoch=ep.auth_epoch,
        )
        b_eff = ep.effective_budget_at_entry_btc

    res = policy.evaluate_step(
        ep,
        auth,
        cmds,
        events,
        facts,
        now_ms,
        cfg,
        effective_budget_btc=b_eff,
        p5_contract=p5_contract,
        p5_condition_triggered=p5_condition_triggered,
        writes_disabled=writes_off,
    )
    for cmd_id, (new_status, new_filled, new_oid) in res.command_resolutions.items():
        st.update_command_state(
            cmd_id,
            status=new_status,
            venue_order_id=new_oid,
            filled_amount=new_filled,
            now_ms=now_ms,
        )
    st.apply_episode_patch(ep_id, res.episode_patch, now_ms=now_ms)

    ep_after = st.get_episode(ep_id)
    assert ep_after is not None
    for act in res.actions:
        if act.action_type == contracts.ActionType.SUBMIT:
            ep_cur = st.get_episode(ep_id)
            assert ep_cur is not None
            lbl = contracts.make_order_label(ep_cur.env, ep_cur.episode_id, act.purpose or "EP", ep_cur.next_cmd_seq)
            valid_until = min(facts.server_time_ms + cfg.request_valid_window_ms, ep_cur.expiry_ms - 1_000)
            cmd = contracts.ExecCommand(
                cmd_id=f"cmd_{ep_cur.next_cmd_seq:04d}_{act.purpose}",
                episode_id=ep_id,
                purpose=act.purpose or "EP",
                label=lbl,
                instrument_name=act.instrument_name or "",
                direction=act.direction or "buy",
                amount=act.amount or ZERO,
                limit_price=act.limit_price or ZERO,
                time_in_force=act.time_in_force or "IOC",
                reduce_only=act.reduce_only,
                post_only=act.post_only,
                reject_post_only=act.reject_post_only,
                valid_until_ms=valid_until,
                created_at_ms=now_ms,
                status=contracts.CommandStatus.PREPARED_UNKNOWN,
                updated_at_ms=now_ms,
            )
            st.create_command_intent(cmd, now_ms=now_ms, estimated_abort_loss_btc=act.estimated_abort_loss_btc)
            try:
                vo = venue.submit_order(
                    cmd.instrument_name,
                    cmd.direction,
                    cmd.amount,
                    cmd.limit_price,
                    label=cmd.label,
                    time_in_force=cmd.time_in_force,
                    reduce_only=cmd.reduce_only,
                    post_only=cmd.post_only,
                    reject_post_only=cmd.reject_post_only,
                    valid_until=cmd.valid_until_ms,
                )
                assert vo is not None
                mapped = {
                    "open": contracts.CommandStatus.OPEN,
                    "filled": contracts.CommandStatus.FILLED,
                    "cancelled": contracts.CommandStatus.CANCELLED,
                    "rejected": contracts.CommandStatus.REJECTED,
                }[vo.order_state]
                st.update_command_state(
                    cmd.cmd_id,
                    status=mapped,
                    venue_order_id=vo.order_id,
                    filled_amount=vo.filled_amount,
                    now_ms=now_ms,
                )
            except (sim.SimulatedNetworkError, sim.RateLimitError) as exc:
                st.update_command_state(
                    cmd.cmd_id,
                    status=contracts.CommandStatus.SUBMIT_UNKNOWN,
                    last_error=str(exc),
                    now_ms=now_ms,
                )
            except sim.DeribitApiError as exc:
                rej_status = (
                    contracts.CommandStatus.EXPIRED_UNFILLED if exc.code == 13888 else contracts.CommandStatus.REJECTED
                )
                st.update_command_state(
                    cmd.cmd_id,
                    status=rej_status,
                    last_error=f"{exc.code}:{exc.message}",
                    now_ms=now_ms,
                )
        elif act.action_type == contracts.ActionType.CANCEL_BY_LABEL and act.target_cmd_id and act.target_label:
            st.update_command_state(
                act.target_cmd_id,
                status=contracts.CommandStatus.CANCEL_UNKNOWN,
                cancel_requested_at_ms=now_ms,
                now_ms=now_ms,
            )
            try:
                venue.cancel_by_label(act.target_label)
            except (sim.SimulatedNetworkError, sim.RateLimitError, sim.DeribitApiError) as exc:
                st.update_command_state(
                    act.target_cmd_id,
                    status=contracts.CommandStatus.CANCEL_UNKNOWN,
                    last_error=str(exc),
                    now_ms=now_ms,
                )

    if recurse_entry and any(
        a.action_type == contracts.ActionType.SUBMIT and a.purpose in {"EP", "ES", "RX", "LX"} for a in res.actions
    ):
        venue.advance_time(1)
        _refresh_books(venue, ep_after)
        return _step_once(
            st,
            venue,
            ep_id,
            cfg,
            p5_contract=p5_contract,
            p5_condition_triggered=p5_condition_triggered,
            page_limit=page_limit,
            follow_pagination=follow_pagination,
            include_lag_cursor=include_lag_cursor,
            now_ms_override=None if now_ms_override is None else now_ms_override + 1,
            probe_override=probe_override,
            recurse_entry=recurse_entry,
        )
    return res


def test_math_stepped_ticks_split_fee_oracle_and_stress_property():
    rng = random.Random(20261125)
    for idx in range(160):
        min_lot = rng.choice([D("0.1"), D("0.05"), D("0.2")])
        spec = contracts.InstrumentSpec.btc_option(
            f"BTC-25NOV26-{60000 + idx * 10}-P",
            strike_usd=62000,
            option_type="put",
            expiry_ms=1795507200000,
            min_trade_amount=str(min_lot),
        )
        matched_lots = rng.randint(1, 20)
        mq = min_lot * matched_lots
        rem_lots = rng.randint(1, matched_lots)
        rq = min_lot * rem_lots
        prot_cost = contracts.ceil_1e8(mq * D(str(rng.uniform(0.0005, 0.006))))
        fees = contracts.ceil_1e8(mq * D("0.0005"))
        short_proc = prot_cost + fees + D(str(round(rng.uniform(0.0002, 0.025), 5))) * mq
        c_matched = short_proc - prot_cost - fees
        b_tot = contracts.floor_1e8_nonneg(c_matched * D("0.15"))
        spent = ZERO if rq == mq else contracts.floor_1e8_nonneg(b_tot * D(str(rng.uniform(0.0, 0.8))))

        oracle_px = sim.IndependentSettlementOracle.expected_tp_max_price(
            short_proc,
            prot_cost,
            fees,
            mq,
            rq,
            spent,
            min_lot,
            tick_size="0.0001",
            tick_size_steps=(("0.005", "0.0005"),),
        )
        p_init = contracts.solve_max_buy_price(b_tot, mq, spec)
        prod_px = (
            contracts.solve_max_buy_price(max(ZERO, b_tot - spent), rq, spec, max_cap_btc=p_init)
            if p_init > ZERO
            else ZERO
        )
        assert prod_px == oracle_px, f"mismatch at iter {idx}: {prod_px} vs {oracle_px}"
        if prod_px > ZERO:
            assert contracts.is_valid_tick(prod_px, spec)
            assert contracts.conservative_split_buy_outlay_btc(prod_px, rq, spec) <= b_tot - spent

    # Nonlinear Put stress vs naive W/S0 check + SM vs PM margin comparison
    put_stress = contracts.spread_stress_loss_btc(
        side="put",
        short_strike_usd=D("62000"),
        long_strike_usd=D("60000"),
        spot_usd=D("62785"),
        qty_btc=ONE,
        put_stress_spot_factor=D("0.70"),
    )
    naive_flat = (D("2000") / D("62785")).quantize(contracts.SAT)
    assert put_stress > naive_flat
    assert put_stress == contracts.ceil_1e8(D("2000") / (D("62785") * D("0.70")))

    _, m_pm, cash_pm = contracts.compute_margin_and_cash_requirements(
        side="put",
        short_strike_usd=D("62000"),
        long_strike_usd=D("60000"),
        spot_usd=D("62785"),
        qty_btc=D("0.1"),
        unpaid_prot_cost_btc=D("0.0002"),
        short_mark_btc=D("0.0045"),
        portfolio_margining_enabled=True,
    )
    _, m_sm, cash_sm = contracts.compute_margin_and_cash_requirements(
        side="put",
        short_strike_usd=D("62000"),
        long_strike_usd=D("60000"),
        spot_usd=D("62785"),
        qty_btc=D("0.1"),
        unpaid_prot_cost_btc=D("0.0002"),
        short_mark_btc=D("0.0045"),
        portfolio_margining_enabled=False,
    )
    assert m_sm > m_pm
    assert cash_pm >= D("0.015") and cash_sm >= D("0.025")


def test_store_four_tables_legacy_migration_and_auth_audit_chain(tmp_path: Path):
    # 1. Fresh DB has strictly 4 tables (no approvals table)
    db_path = tmp_path / "store4.sqlite3"
    st = store.ExecutionStore(db_path)
    try:
        tables = {
            str(r["name"])
            for r in st.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert tables == {"meta", "episodes", "commands", "venue_events"}
        auth0 = st.get_capital_auth()
        assert auth0.entry_armed is False and auth0.budget_cap_btc == ZERO and auth0.max_qty_btc == D("0.1")

        # Continuous authorization + immutable audit chain + B_eff drawdown & cap
        st.set_capital_policy(
            entry_armed=True, budget_cap_btc=D("0.05"), max_qty_btc=D("0.1"), operator="cfg", now_ms=1000
        )
        st.set_capital_policy(
            entry_armed=False, budget_cap_btc=D("0.05"), max_qty_btc=D("0.1"), operator="cfg", now_ms=2000
        )
        audits = st.get_capital_auth_audits()
        assert [a["audit_key"] for a in audits] == ["capital_auth_audit:000001", "capital_auth_audit:000002"]
        assert audits[0]["after"]["entry_armed"] is True
        assert audits[1]["after"]["entry_armed"] is False

        assert contracts.compute_effective_budget_btc(
            budget_cap_btc=D("0.05"), pnl_settled_btc=D("-0.02"), grant_baseline_pnl_btc=ZERO
        ) == D("0.03")
        assert contracts.compute_effective_budget_btc(
            budget_cap_btc=D("0.05"), pnl_settled_btc=D("0.04"), grant_baseline_pnl_btc=ZERO
        ) == D("0.05")
    finally:
        st.close()

    # Restart preserves audit chain and raises on duplicate hand or second active episode
    st2 = store.ExecutionStore(db_path)
    try:
        assert len(st2.get_capital_auth_audits()) == 2
        _, ep, _, _ = _setup_put_spread()
        st2.create_episode(ep)
        with pytest.raises(store.EpisodeCreationError, match="MAX_EPISODES_ACTIVE_OR_UNSETTLED"):
            st2.create_episode(replace(ep, episode_id="ep_002", hand_id="hand_other"))
    finally:
        st2.close()

    # 2. Idempotent migration on legacy 5-table DB preserves historical rows & venue_events
    leg_path = tmp_path / "legacy.sqlite3"
    raw_conn = sqlite3.connect(str(leg_path))
    raw_conn.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE episodes (
            episode_id TEXT PRIMARY KEY, env TEXT NOT NULL, subaccount_id TEXT NOT NULL,
            dossier_id TEXT NOT NULL, hand_id TEXT NOT NULL UNIQUE, p5_episode_id TEXT NOT NULL,
            side TEXT NOT NULL, short_instrument TEXT NOT NULL, long_instrument TEXT NOT NULL,
            short_strike_usd TEXT NOT NULL, long_strike_usd TEXT NOT NULL, expiry_ms INTEGER NOT NULL,
            contract_hash TEXT NOT NULL, phase TEXT NOT NULL, created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL, entry_started_at_ms INTEGER, entry_halted INTEGER NOT NULL DEFAULT 0,
            entry_halt_reason TEXT, entry_closed INTEGER NOT NULL DEFAULT 0, entry_closed_at_ms INTEGER,
            entry_closure_reason TEXT, matched_qty_btc TEXT NOT NULL DEFAULT '0',
            matched_short_proceeds_btc TEXT NOT NULL DEFAULT '0', matched_prot_cost_ex_fee_btc TEXT NOT NULL DEFAULT '0',
            matched_entry_fees_btc TEXT NOT NULL DEFAULT '0', matched_net_credit_btc TEXT NOT NULL DEFAULT '0',
            unmatched_prot_qty_btc TEXT NOT NULL DEFAULT '0', unmatched_prot_cost_btc TEXT NOT NULL DEFAULT '0',
            tp_budget_total_btc TEXT NOT NULL DEFAULT '0', tp_cap_init_btc TEXT NOT NULL DEFAULT '0',
            tp_substate TEXT, exit_latched INTEGER NOT NULL DEFAULT 0, exit_latch_reason TEXT,
            exit_started_at_ms INTEGER, short_liability_cleared INTEGER NOT NULL DEFAULT 0,
            inventory_cleared INTEGER NOT NULL DEFAULT 0, cashflow_settled INTEGER NOT NULL DEFAULT 0,
            terminal_outcome TEXT, next_cmd_seq INTEGER NOT NULL DEFAULT 1,
            last_entry_attempt_ms INTEGER NOT NULL DEFAULT 0, entry_attempts INTEGER NOT NULL DEFAULT 0,
            last_rx_attempt_ms INTEGER NOT NULL DEFAULT 0, rx_attempts INTEGER NOT NULL DEFAULT 0,
            last_lx_attempt_ms INTEGER NOT NULL DEFAULT 0, alerts_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE approvals (approval_id TEXT PRIMARY KEY);
        CREATE TABLE commands (cmd_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL, purpose TEXT NOT NULL,
            label TEXT NOT NULL UNIQUE, instrument_name TEXT NOT NULL, direction TEXT NOT NULL,
            amount TEXT NOT NULL, limit_price TEXT NOT NULL, time_in_force TEXT NOT NULL,
            reduce_only INTEGER NOT NULL, post_only INTEGER NOT NULL, reject_post_only INTEGER NOT NULL,
            valid_until_ms INTEGER NOT NULL, created_at_ms INTEGER NOT NULL, status TEXT NOT NULL,
            venue_order_id TEXT, filled_amount TEXT NOT NULL DEFAULT '0', last_error TEXT,
            updated_at_ms INTEGER NOT NULL, cancel_requested_at_ms INTEGER);
        CREATE TABLE venue_events (event_uid TEXT PRIMARY KEY, episode_id TEXT NOT NULL, event_type TEXT NOT NULL,
            instrument_name TEXT NOT NULL, timestamp_ms INTEGER NOT NULL, trade_id TEXT, order_id TEXT,
            tx_log_id INTEGER, label TEXT, raw_json TEXT NOT NULL, ingested_at_ms INTEGER NOT NULL);
        INSERT INTO episodes (episode_id, env, subaccount_id, dossier_id, hand_id, p5_episode_id, side,
            short_instrument, long_instrument, short_strike_usd, long_strike_usd, expiry_ms, contract_hash,
            phase, created_at_ms, updated_at_ms, cashflow_settled, terminal_outcome)
        VALUES ('ep_old', 'tn', 'sub', 'dos', 'hand_old', 'p5_old', 'put', 'S', 'L', '62000', '60000',
            1800000000000, 'h', 'ENTRY_CLOSED', 100, 200, 1, 'EARLY_FLAT_SETTLED');
        """
    )
    raw_conn.commit()
    raw_conn.close()

    migrated = store.ExecutionStore(leg_path)
    try:
        old_ep = migrated.get_episode("ep_old")
        assert old_ep is not None and old_ep.phase == contracts.LifecyclePhase.TP_MANAGED
        assert old_ep.cashflow_settled is True
    finally:
        migrated.close()


def test_tp_dynamic_fee_boundary_resync_and_no_expiry_freeze(tmp_path: Path):
    # 1. Task Doc 3.1 exact TP boundary: 0.00012 (between 0.0001125 and 0.00015!) clamps to 0.0001;
    #    0.00011 (<= 0.0001125) triggers TP_UNATTAINABLE_LOW_CREDIT + TP_UNATTAINABLE_ALERT
    spec = contracts.InstrumentSpec.btc_option(
        "BTC-25NOV26-62000-P", strike_usd=62000, option_type="put", expiry_ms=1795507200000, min_trade_amount="0.1"
    )
    plan_clamp = contracts.compute_tp_plan(matched_net_credit_btc=D("0.00001200"), short_qty_btc=D("0.1"), short_spec=spec)
    assert plan_clamp.substate == "TP_CLAMPED_TO_MIN_TICK"
    assert plan_clamp.limit_price_btc == D("0.0001")
    assert plan_clamp.target_capture_ratio is not None and plan_clamp.target_capture_ratio < D("0.85")

    plan_dead = contracts.compute_tp_plan(matched_net_credit_btc=D("0.00001100"), short_qty_btc=D("0.1"), short_spec=spec)
    assert plan_dead.substate == "TP_UNATTAINABLE_LOW_CREDIT"
    assert plan_dead.limit_price_btc == ZERO
    assert plan_dead.alert == "TP_UNATTAINABLE_ALERT"

    # 2. Full flow with clamped TP + bidirectional remaining_amount != short_qty resync + kept into <60s pre-expiry
    venue, ep, auth, cfg = _setup_put_spread(
        target_qty=D("1.0"), max_entry_slice=D("1.0"), min_net_credit=D("0.0003"), budget_cap_btc=D("0.50")
    )
    # Set spread net credit = 0.0021 - 0.0013 - 0.000425 = 0.000375 BTC/BTC (>= 0.0003 and < 0.00075 -> clamped to 0.0001!)
    venue.set_book(ep.short_instrument, bid="0.0021", bid_size="5.0", ask="0.0024", ask_size="5.0", mark_price="0.0022", min_price="0.0001", max_price="0.05")
    st = store.ExecutionStore(tmp_path / "tp_clamp.sqlite3")
    try:
        st.set_capital_policy(entry_armed=True, budget_cap_btc=D("0.50"), max_qty_btc=D("1.0"), now_ms=ep.created_at_ms)
        st.create_episode(ep)
        _step_once(st, venue, ep.episode_id, cfg)  # EP -> ES -> TP clamped!
        ep_tp = st.get_episode(ep.episode_id)
        assert ep_tp.entry_closed is True
        assert ep_tp.tp_substate == "TP_CLAMPED_TO_MIN_TICK"
        assert ep_tp.tp_target_capture_ratio is not None and ep_tp.tp_target_capture_ratio < D("0.85")
        tp_cmds = [c for c in st.get_commands(ep.episode_id) if c.purpose == "TP"]
        assert len(tp_cmds) == 1 and tp_cmds[0].limit_price == D("0.0001") and tp_cmds[0].amount == D("1.0")

        # Simulate TP resting inside 60s before expiry (previously canceled by TP_EXPIRY_FREEZE) -> MUST STAY RESTING!
        venue.set_server_time(ep.expiry_ms - 30_000)
        _refresh_books(venue, ep)
        res_near = _step_once(st, venue, ep.episode_id, cfg)
        assert not res_near.actions
        assert venue.get_open_orders()

        # Simulate under-sized TP (remaining_amount < short_qty) -> MUST cancel & re-place full 1.0 qty!
        venue.cancel_by_label(tp_cmds[0].label)
        small_label = tp_cmds[0].label + "_sub"
        small_vo = venue.submit_order(
            ep.short_instrument, "buy", D("0.4"), D("0.0001"), label=small_label,
            time_in_force="GTC", reduce_only=True, post_only=False, reject_post_only=False,
        )
        assert small_vo is not None
        # Update stored command so venue open order has amount=0.4 while short_qty=1.0
        st.conn.execute(
            "UPDATE commands SET amount='0.4', label=?, venue_order_id=? WHERE cmd_id=?",
            (small_label, small_vo.order_id, tp_cmds[0].cmd_id),
        )
        _step_once(st, venue, ep.episode_id, cfg)  # cancels undersized 0.4
        venue.advance_time(500)
        _refresh_books(venue, ep)
        _step_once(st, venue, ep.episode_id, cfg)  # re-places full 1.0 TP
        tp_all = [c for c in st.get_commands(ep.episode_id) if c.purpose == "TP"]
        assert tp_all[-1].status == contracts.CommandStatus.OPEN and tp_all[-1].amount == D("1.0")
    finally:
        st.close()


def test_entry_pure_ioc_silent_reobs_condition_halt_and_zero_rec_cap(tmp_path: Path):
    # 1. Transient book spike above 0.0060 or net credit < 0.0008 waits silently inside 60s, then fills when normalized
    venue, ep, _, cfg = _setup_put_spread(target_qty=D("0.1"), max_entry_slice=D("0.1"), budget_cap_btc=D("0.05"))
    st = store.ExecutionStore(tmp_path / "entry.sqlite3")
    try:
        st.set_capital_policy(entry_armed=True, budget_cap_btc=D("0.05"), max_qty_btc=D("0.1"), now_ms=ep.created_at_ms)
        st.create_episode(ep)
        # Protection ask temporarily 0.0065 (> 0.0060 hard cap)
        venue.set_book(ep.long_instrument, bid="0.0060", bid_size="5.0", ask="0.0065", ask_size="5.0", mark_price="0.0062")
        venue.set_book(ep.short_instrument, bid="0.0095", bid_size="5.0", ask="0.0098", ask_size="5.0", mark_price="0.0096")
        res_wait = _step_once(st, venue, ep.episode_id, cfg)
        assert not res_wait.actions
        assert st.get_episode(ep.episode_id).entry_halted is False  # NEVER permanently halted on 1 tick!

        # 0.0045 protection with short bid 0.0060 -> passes 0.0060 cap cleanly!
        venue.advance_time(1000)
        venue.set_book(ep.long_instrument, bid="0.0042", bid_size="5.0", ask="0.0045", ask_size="5.0", mark_price="0.0043")
        venue.set_book(ep.short_instrument, bid="0.0060", bid_size="5.0", ask="0.0063", ask_size="5.0", mark_price="0.0061")
        _step_once(st, venue, ep.episode_id, cfg)
        ep_ok = st.get_episode(ep.episode_id)
        assert ep_ok.entry_closed is True and ep_ok.matched_qty_btc == D("0.1")
    finally:
        st.close()

    # 2. Pre-entry_closed CONDITION_TRIGGERED sets entry_halted ONLY (never exit_latched)
    venue2, ep2, _, cfg2 = _setup_put_spread(target_qty=D("0.2"), max_entry_slice=D("0.1"), budget_cap_btc=D("0.10"))
    st2 = store.ExecutionStore(tmp_path / "cond_halt.sqlite3")
    try:
        st2.set_capital_policy(entry_armed=True, budget_cap_btc=D("0.10"), max_qty_btc=D("0.2"), now_ms=ep2.created_at_ms)
        st2.create_episode(ep2)
        _step_once(st2, venue2, ep2.episode_id, cfg2, recurse_entry=False)  # EP1 0.1
        venue2.advance_time(10)
        _refresh_books(venue2, ep2)
        _step_once(st2, venue2, ep2.episode_id, cfg2, recurse_entry=False)  # ES1 0.1
        assert st2.get_episode(ep2.episode_id).entry_closed is False
        venue2.advance_time(1000)
        _refresh_books(venue2, ep2)
        _step_once(st2, venue2, ep2.episode_id, cfg2, p5_condition_triggered=True)
        ep2_after = st2.get_episode(ep2.episode_id)
        assert ep2_after.entry_halted is True and ep2_after.entry_halt_reason == "P5_CONDITION_TRIGGERED"
        assert ep2_after.exit_latched is False  # NEVER triggers REVOKE!
        assert ep2_after.entry_closed is True and ep2_after.phase == contracts.LifecyclePhase.TP_MANAGED
        assert any(c.purpose == "TP" and c.status == contracts.CommandStatus.OPEN for c in st2.get_commands(ep2.episode_id))
    finally:
        st2.close()

    # 3. ES 20s rescue window expires -> LONG_ONLY_ABORT -> LX recovers protection residual
    venue3, ep3, _, cfg3 = _setup_put_spread(target_qty=D("0.1"), max_entry_slice=D("0.1"), budget_cap_btc=D("0.05"))
    st3 = store.ExecutionStore(tmp_path / "es_rescue.sqlite3")
    try:
        st3.set_capital_policy(entry_armed=True, budget_cap_btc=D("0.05"), max_qty_btc=D("0.1"), now_ms=ep3.created_at_ms)
        st3.create_episode(ep3)
        # Cause short bid to vanish right after EP by setting ask only after first EP attempt via partial sequence:
        venue3.inject_partial_fill_cap(ep3.short_instrument, ZERO)  # temporarily simulate 0 ES fill
        _step_once(st3, venue3, ep3.episode_id, cfg3, recurse_entry=False)  # EP fills 0.1
        venue3.advance_time(10)
        _refresh_books(venue3, ep3)
        _step_once(st3, venue3, ep3.episode_id, cfg3, recurse_entry=False)  # ES fills 0
        venue3.inject_partial_fill_cap(ep3.short_instrument, None)
        venue3.set_book(ep3.short_instrument, bid="0.0005", bid_size="5.0", ask="0.0020", ask_size="5.0", mark_price="0.0010")
        venue3.advance_time(21_000)  # past 20s es_rescue_window_ms!
        _refresh_books(venue3, ep3)
        _step_once(st3, venue3, ep3.episode_id, cfg3)
        ep3_end = st3.get_episode(ep3.episode_id)
        assert ep3_end.entry_closed is True
        assert "LONG_ONLY_ABORT:ES_RESCUE_WINDOW_EXPIRED" in (ep3_end.entry_closure_reason or "")
        assert any(c.purpose == "LX" and c.filled_amount == D("0.1") for c in st3.get_commands(ep3.episode_id))
    finally:
        st3.close()


def test_rx_boolean_complement_mark_guard_and_10009_locked_protection(tmp_path: Path):
    venue, ep, _, cfg = _setup_put_spread(target_qty=D("0.1"), max_entry_slice=D("0.1"), budget_cap_btc=D("0.10"))
    st = store.ExecutionStore(tmp_path / "rx_guard.sqlite3")
    try:
        st.set_capital_policy(entry_armed=True, budget_cap_btc=D("0.10"), max_qty_btc=D("0.1"), now_ms=ep.created_at_ms)
        st.create_episode(ep)
        _step_once(st, venue, ep.episode_id, cfg)  # EP + ES + TP

        p5_rev = contracts.P5DecisionContract(
            dossier_id=ep.dossier_id,
            hand_id=ep.hand_id,
            episode_id=ep.p5_episode_id,
            decision_id="d1",
            decision="Accept",
            side="put",
            short_instrument=ep.short_instrument,
            long_instrument=ep.long_instrument,
            short_strike_usd=ep.short_strike_usd,
            long_strike_usd=ep.long_strike_usd,
            expiry_ms=ep.expiry_ms,
            decided_at_ms=ep.decided_at_ms,
            link_recorded_at_ms=ep.link_recorded_at_ms,
            link_state="SHADOW_LOCK_CREATED",
            decision_sha256="d" * 64,
            condition_sha256="c" * 64,
            input_sha256="i" * 64,
            raw_sha256="r" * 64,
            revoked=True,
            revoke_review_id="rev_1",
            revoke_shadow_action="REVOKE_ALIGNED",
            revoke_at_ms=venue.server_time_ms,
        )

        # Stage 1 cancels TP first, then with missing mark -> MUST NOT fire blind IOC! Emits QUOTE_UNAVAILABLE_AT_REVOKE
        venue.set_book(ep.short_instrument, bid="0.0080", bid_size="5.0", ask="0.0450", ask_size="5.0", mark_price=None, max_price="0.0500")
        venue.advance_time(500)
        _refresh_books(venue, ep)
        _step_once(st, venue, ep.episode_id, cfg, p5_contract=p5_rev)  # cancels TP
        venue.advance_time(100)
        _refresh_books(venue, ep)
        _step_once(st, venue, ep.episode_id, cfg, p5_contract=p5_rev)  # observes missing mark after TP cancelled
        assert not [c for c in st.get_commands(ep.episode_id) if c.purpose == "RX"]
        assert "QUOTE_UNAVAILABLE_AT_REVOKE" in st.get_episode(ep.episode_id).alerts

        # Even if dt > 3s, with only 1 sample before, after sample #2 with dt=2000ms (< 3s) and spike ask=0.0450 vs mark=0.0100 -> blocked by Stage 1 guard!
        venue.set_book(ep.short_instrument, bid="0.0090", bid_size="5.0", ask="0.0450", ask_size="5.0", mark_price="0.0100", max_price="0.0500")
        venue.advance_time(1500)
        _refresh_books(venue, ep)
        res_s1 = _step_once(st, venue, ep.episode_id, cfg, p5_contract=p5_rev)
        assert not [c for c in st.get_commands(ep.episode_id) if c.purpose == "RX"]
        assert "RX_STAGE1_GUARD_WAIT" in res_s1.notes

        # Past 3s AND >= 2 valid RX samples, and inject 10009 not_enough_funds -> keeps protection strictly LOCKED (0 LX)!
        venue.advance_time(2000)
        venue.inject_10009(1)
        _refresh_books(venue, ep)
        _step_once(st, venue, ep.episode_id, cfg, p5_contract=p5_rev)
        rx_cmds = [c for c in st.get_commands(ep.episode_id) if c.purpose == "RX"]
        assert len(rx_cmds) == 1 and rx_cmds[0].status == contracts.CommandStatus.REJECTED
        assert st.get_episode(ep.episode_id).rx_cash_wall_10009 is True
        assert "RX_MARGIN_OR_CASH_EXHAUSTED_SPREAD_HEDGED_MANUAL_DECISION" in st.get_episode(ep.episode_id).alerts
        assert not [c for c in st.get_commands(ep.episode_id) if c.purpose == "LX"]  # NEVER unlocks protection!

        # 5s < 10s cash wall backoff -> must stay backed off; 10s -> succeeds and clears short + LX
        venue.advance_time(5000)
        _refresh_books(venue, ep)
        _step_once(st, venue, ep.episode_id, cfg, p5_contract=p5_rev)
        assert len([c for c in st.get_commands(ep.episode_id) if c.purpose == "RX"]) == 1

        venue.advance_time(5500)
        _refresh_books(venue, ep)
        _step_once(st, venue, ep.episode_id, cfg, p5_contract=p5_rev)
        rx_cmds2 = [c for c in st.get_commands(ep.episode_id) if c.purpose == "RX"]
        assert len(rx_cmds2) == 2 and rx_cmds2[-1].status == contracts.CommandStatus.FILLED
        assert st.get_episode(ep.episode_id).short_liability_cleared is True
    finally:
        st.close()


def test_unknown_complete_evidence_counterexamples_and_0800_expiry_safe(tmp_path: Path):
    venue, ep, _, cfg = _setup_put_spread(target_qty=D("0.1"), max_entry_slice=D("0.1"), budget_cap_btc=D("0.05"))
    st = store.ExecutionStore(tmp_path / "unk_strict.sqlite3")
    try:
        st.set_capital_policy(entry_armed=True, budget_cap_btc=D("0.05"), max_qty_btc=D("0.1"), now_ms=ep.created_at_ms)
        st.create_episode(ep)

        # 1. Unknown venue_order_id SUBMIT_UNKNOWN -> incomplete history / ERROR / contradiction / >25m blocks release!
        t_start = venue.server_time_ms
        venue.inject_delay_submit_until_ms(t_start + cfg.request_valid_window_ms + 200, raise_timeout=True)
        _step_once(st, venue, ep.episode_id, cfg)
        unk_cmd = st.get_commands(ep.episode_id)[0]
        assert unk_cmd.status == contracts.CommandStatus.SUBMIT_UNKNOWN

        # Past valid_until + 1500, BUT label_query_status == "ERROR" -> MUST STAY UNKNOWN!
        venue.advance_time(8000)
        _refresh_books(venue, ep)
        bad_probe = {
            unk_cmd.cmd_id: contracts.OrderReconcileProbe(
                cmd_id=unk_cmd.cmd_id,
                label_query_status="ERROR",
                inst_history_recent_complete=True,
                fresh_est_server_ms=venue.get_server_time(),
            )
        }
        _step_once(st, venue, ep.episode_id, cfg, probe_override=bad_probe)
        assert st.get_commands(ep.episode_id)[0].status == contracts.CommandStatus.SUBMIT_UNKNOWN

        # Incomplete inst_history_recent_complete=False -> MUST STAY UNKNOWN!
        inc_probe = {
            unk_cmd.cmd_id: contracts.OrderReconcileProbe(
                cmd_id=unk_cmd.cmd_id,
                label_query_status="EMPTY_CONFIRMED",
                inst_history_recent_complete=False,
                fresh_est_server_ms=venue.get_server_time(),
            )
        }
        _step_once(st, venue, ep.episode_id, cfg, probe_override=inc_probe)
        assert st.get_commands(ep.episode_id)[0].status == contracts.CommandStatus.SUBMIT_UNKNOWN

        # Real 5-condition proof resolves SUBMIT_UNKNOWN cleanly -> proceeds to fill EP + ES + TP
        _step_once(st, venue, ep.episode_id, cfg)
        cmds_now = st.get_commands(ep.episode_id)
        assert cmds_now[0].status == contracts.CommandStatus.EXPIRED_UNFILLED
        assert st.get_episode(ep.episode_id).entry_closed is True

        # 2. Past 25m hot-cache with EXPIRED_HOT_CACHE and no positive vo MUST stay UNKNOWN + alert!
        tp_cmd = next(c for c in cmds_now if c.purpose == "TP")
        unk_lx = contracts.ExecCommand(
            cmd_id="cmd_stale_lx",
            episode_id=ep.episode_id,
            purpose="LX",
            label="ax1:tn:old:LX:0900",
            instrument_name=ep.long_instrument,
            direction="sell",
            amount=D("0.1"),
            limit_price=D("0.0005"),
            time_in_force="IOC",
            reduce_only=True,
            post_only=False,
            reject_post_only=False,
            valid_until_ms=venue.server_time_ms + 5000,
            created_at_ms=venue.server_time_ms - 1_600_000,  # >25m ago
            status=contracts.CommandStatus.SUBMIT_UNKNOWN,
        )
        st.create_command_intent(unk_lx, now_ms=venue.server_time_ms)
        _step_once(st, venue, ep.episode_id, cfg)
        lx_row = next(c for c in st.get_commands(ep.episode_id) if c.cmd_id == "cmd_stale_lx")
        assert lx_row.status == contracts.CommandStatus.SUBMIT_UNKNOWN
        assert any("UNKNOWN_UNVERIFIABLE_PAST_HOT_CACHE" in a for a in st.get_episode(ep.episode_id).alerts)

        # Clean up synthetic unknown so expiry TP resolution can settle
        st.update_command_state("cmd_stale_lx", status=contracts.CommandStatus.EXPIRED_UNFILLED, now_ms=venue.server_time_ms)

        # 3. Across 08:00 UTC expiry, unfilled resting TP auto-cancelled by venue MUST resolve cleanly (0 == 0 trades_for_vo)!
        venue.set_book(ep.long_instrument, bid=None, bid_size="0", ask="0.0002", ask_size="1.0", mark_price="0.0001")
        venue.expire_at_settlement("63500")
        _step_once(st, venue, ep.episode_id, cfg)
        ep_done = st.get_episode(ep.episode_id)
        assert ep_done.cashflow_settled is True and ep_done.terminal_outcome == "EXPIRY_SETTLED"
        tp_done = next(c for c in st.get_commands(ep.episode_id) if c.cmd_id == tp_cmd.cmd_id)
        assert tp_done.status == contracts.CommandStatus.CANCELLED
    finally:
        st.close()


def test_mandatory_multi_fault_combined_trajectory(tmp_path: Path):
    """Multi-fault trajectory: partial entry + excess LX -> partial TP -> REVOKE + lost cancel ACK + concurrent fill
    -> process crash/kill + stale writer fencing -> order-level reconcile -> partial RX + backoff -> ITM two-stage settlement."""
    venue, ep, _, _ = _setup_put_spread(
        settlement_period="week", target_qty=D("1.0"), max_entry_slice=D("0.5"), budget_cap_btc=D("0.50")
    )
    cfg = contracts.ExecConfig(initial_qty_btc=D("1.0"), max_entry_slice_btc=D("0.5"), entry_window_ms=15_000)
    db_path = tmp_path / "multifault.sqlite3"
    st1 = store.ExecutionStore(db_path)

    st1.set_capital_policy(entry_armed=True, budget_cap_btc=D("0.50"), max_qty_btc=D("1.0"), now_ms=ep.created_at_ms)
    st1.create_episode(ep)

    # 1. Partial/shrunk entry: EP fills 1.0 in 2 slices; ES fills only 0.7 (0.5 + 0.2), then window expires
    _step_once(st1, venue, ep.episode_id, cfg, recurse_entry=False)  # EP1 0.5
    venue.advance_time(10)
    _refresh_books(venue, ep)
    _step_once(st1, venue, ep.episode_id, cfg, recurse_entry=False)  # ES1 0.5
    venue.advance_time(1200)
    _refresh_books(venue, ep)
    venue.inject_partial_fill_cap(ep.short_instrument, D("0.2"))
    _step_once(st1, venue, ep.episode_id, cfg, recurse_entry=False)  # EP2 0.5
    venue.advance_time(10)
    _refresh_books(venue, ep)
    _step_once(st1, venue, ep.episode_id, cfg, recurse_entry=False)  # ES2 0.2
    venue.inject_partial_fill_cap(ep.short_instrument, None)

    venue.advance_time(16_000)  # Entry window expires -> formal entry_closed (0.7 matched, 0.3 unmatched)!
    _refresh_books(venue, ep)
    venue.inject_partial_fill_cap(ep.long_instrument, D("0.1"))
    _step_once(st1, venue, ep.episode_id, cfg)
    venue.inject_partial_fill_cap(ep.long_instrument, None)
    ep_c = st1.get_episode(ep.episode_id)
    assert ep_c.entry_closed is True
    assert ep_c.matched_qty_btc == D("0.7")
    assert ep_c.unmatched_prot_qty_btc == D("0.3")

    # 2. TP partially fills 0.2 -> remaining 0.5 TP resting
    tp_cmd = next(c for c in st1.get_commands(ep.episode_id) if c.purpose == "TP")
    venue.fill_resting_order(tp_cmd.label, D("0.2"), price=D("0.0002"), liquidity="M")
    venue.advance_time(1000)
    _refresh_books(venue, ep)
    _step_once(st1, venue, ep.episode_id, cfg)

    # 3. AI_REVOKE arrives -> cancel_by_label(TP) ACK LOST + CONCURRENT +0.2 FILL (leaving -0.3 short) -> KILL process!
    venue.set_book(
        ep.short_instrument,
        bid="0.0080",
        bid_size="5.0",
        ask="0.0085",
        ask_size="5.0",
        mark_price="0.0082",
        min_price="0.0001",
        max_price="0.05",
    )
    venue.inject_drop_next_cancel_ack(
        concurrent_fill_qty=D("0.2"), concurrent_fill_price=D("0.0003"), cancel_succeeded_if_remaining=True
    )
    rev_at = venue.server_time_ms
    p5_rev = contracts.P5DecisionContract(
        dossier_id=ep.dossier_id,
        hand_id=ep.hand_id,
        episode_id=ep.p5_episode_id,
        decision_id="d1",
        decision="Accept",
        side="put",
        short_instrument=ep.short_instrument,
        long_instrument=ep.long_instrument,
        short_strike_usd=ep.short_strike_usd,
        long_strike_usd=ep.long_strike_usd,
        expiry_ms=ep.expiry_ms,
        decided_at_ms=ep.created_at_ms,
        link_recorded_at_ms=ep.link_recorded_at_ms,
        link_state="SHADOW_LOCK_CREATED",
        decision_sha256="d" * 64,
        condition_sha256="c" * 64,
        input_sha256="i" * 64,
        raw_sha256="r" * 64,
        revoked=True,
        revoke_review_id="rev_mf",
        revoke_shadow_action="REVOKE_ALIGNED",
        revoke_at_ms=rev_at,
    )
    _step_once(st1, venue, ep.episode_id, cfg, p5_contract=p5_rev)
    assert st1.get_episode(ep.episode_id).exit_latched is True

    store._release_os_lock(st1._lock_fd)
    st1._lock_fd = None

    st2 = store.ExecutionStore(db_path)
    try:
        with pytest.raises(store.StaleWriterEpochError):
            st1.set_writes_disabled("stale", now_ms=venue.server_time_ms)

        # 4. Restarted coordinator reconciles CANCEL_UNKNOWN TP -> Stage 2 / sample #2 after 3s clears remaining -0.3
        venue.inject_partial_fill_cap(ep.short_instrument, D("0.2"))
        venue.advance_time(1500)
        _refresh_books(venue, ep)
        _step_once(st2, venue, ep.episode_id, cfg, p5_contract=p5_rev)  # RX sample #2, buys 0.2 -> -0.1 short left!
        venue.inject_partial_fill_cap(ep.short_instrument, None)
        venue.advance_time(2000)
        _refresh_books(venue, ep)
        _step_once(st2, venue, ep.episode_id, cfg, p5_contract=p5_rev)  # RX2 buys remaining 0.1 -> short_qty == 0!
        assert st2.get_episode(ep.episode_id).short_liability_cleared is True

        # 5. Residual 0.9 prot has NO BID -> holds -> delayed tx_log settles Stage-1+Stage-2 ITM delivery!
        venue.set_book(ep.long_instrument, bid=None, bid_size="0", ask="0.0020", ask_size="1.0", mark_price="0.0018")
        venue.advance_time(1000)
        _refresh_books(venue, ep)
        _step_once(st2, venue, ep.episode_id, cfg, p5_contract=p5_rev)
        assert st2.get_episode(ep.episode_id).inventory_cleared is False

        venue.inject_delayed_tx_log(True)
        venue.expire_at_settlement("58000")
        _step_once(st2, venue, ep.episode_id, cfg, p5_contract=p5_rev)
        assert st2.get_episode(ep.episode_id).inventory_cleared is True
        assert st2.get_episode(ep.episode_id).cashflow_settled is False

        venue.settle_delayed_tx_log()
        venue.advance_time(1000)
        _step_once(st2, venue, ep.episode_id, cfg, p5_contract=p5_rev)
        ep_final = st2.get_episode(ep.episode_id)
        assert ep_final.cashflow_settled is True
        assert ep_final.terminal_outcome == "EXPIRY_SETTLED"

        # 6. Full ledger rebuild cross-checks IndependentSettlementOracle to the EXACT satoshi!
        facts_end = _adapt_facts(venue.build_facts(ep.short_instrument, ep.long_instrument))
        ledger = st2.rebuild_episode_ledger(ep.episode_id, facts=facts_end)
        oracle = sim.IndependentSettlementOracle(
            initial_balance_btc="1.0",
            trades=venue.export_raw_trades(),
            settlement_price_usd="58000",
            settlement_period="week",
        )
        assert D(ledger["accounting"]["cashflow_btc"]) == oracle.total_cashflow_btc
        assert D(ledger["accounting"]["episode_total_realized_pnl_btc"]) == oracle.expected_episode_realized_pnl_btc
        assert D(ledger["accounting"]["actual_fees_btc"]) == oracle.expected_fees_btc
        assert D(ledger["two_stage_settlement"]["crosscheck"]["diff_btc"]) == ZERO
    finally:
        st2.close()
        st1.conn.close()


def test_closure_policy_completeness_guards_and_mixed_expiry_settlement(tmp_path: Path):
    # 1. K-W1: incomplete facts / transient missing short_spec must NEVER trigger false NAKED_SHORT_BREACH or freeze tp_cap_init=0
    venue, ep, _, cfg = _setup_put_spread(target_qty=D("0.1"), max_entry_slice=D("0.1"), budget_cap_btc=D("0.10"))
    st = store.ExecutionStore(tmp_path / "kw1.sqlite3")
    try:
        auth = st.set_capital_policy(entry_armed=True, budget_cap_btc=D("0.10"), max_qty_btc=D("0.1"), now_ms=ep.created_at_ms)
        st.create_episode(ep)
        _step_once(st, venue, ep.episode_id, cfg, recurse_entry=False)  # EP filled 0.1
        venue.advance_time(10)
        _refresh_books(venue, ep)
        _step_once(st, venue, ep.episode_id, cfg, recurse_entry=False)  # ES filled 0.1 (entry still open on partial poll)

        ep_cur = st.get_episode(ep.episode_id)
        cmds_cur = st.get_commands(ep.episode_id)
        evs_cur = st.get_venue_events(ep.episode_id)
        facts_ok = _adapt_facts(venue.build_facts(ep.short_instrument, ep.long_instrument))

        # Incomplete positions_complete=False with missing long position MUST NOT trigger NAKED_SHORT_BREACH
        facts_incomplete = contracts.ExchangeFacts(
            **{
                **facts_ok.__dict__,
                "positions": tuple(p for p in facts_ok.positions if p.instrument_name == ep.short_instrument),
                "positions_complete": False,
            }
        )
        res_inc = policy.evaluate_step(ep_cur, auth, cmds_cur, evs_cur, facts_incomplete, venue.server_time_ms, cfg)
        assert res_inc.episode_patch.exit_latched is False
        assert "NAKED_SHORT_BREACH" not in res_inc.alerts

        # Transient short_spec=None on non-exited filled ES MUST NOT freeze entry_closed with tp_cap_init=0
        facts_no_spec = contracts.ExchangeFacts(**{**facts_ok.__dict__, "short_spec": None})
        res_nospec = policy.evaluate_step(ep_cur, auth, cmds_cur, evs_cur, facts_no_spec, venue.server_time_ms, cfg)
        assert res_nospec.episode_patch.entry_closed is False

        # Transient api_error with stale clock on empty episode MUST NOT close zero-fill prematurely
        ep_empty = contracts.EpisodeState(
            **{
                **ep.__dict__,
                "episode_id": "ep_err_empty",
                "hand_id": "h_err_empty",
                "contract_hash": "ch_err_empty",
            }
        )
        facts_err = contracts.ExchangeFacts(
            **{**facts_ok.__dict__, "api_error": "10028", "server_time_ms": facts_ok.asof_ms - 5_000}
        )
        res_err = policy.evaluate_step(ep_empty, auth, (), (), facts_err, facts_ok.asof_ms, cfg)
        assert res_err.episode_patch.entry_closed is False

        # Incomplete positions_complete=False during exit_latched when short_qty == 0 blocks LX and keeps short_liability_cleared=False
        ep_exit = contracts.EpisodeState(
            **{
                **ep_cur.__dict__,
                "episode_id": "ep_exit_inc",
                "hand_id": "h_exit_inc",
                "contract_hash": "ch_exit_inc",
                "entry_closed": True,
                "exit_latched": True,
                "exit_latch_reason": "AI_REVOKE",
                "matched_qty_btc": ZERO,
            }
        )
        facts_exit_inc = contracts.ExchangeFacts(
            **{
                **facts_ok.__dict__,
                "positions": (contracts.ExchangePosition(instrument_name=ep.long_instrument, kind="option", size=D("0.1")),),
                "positions_complete": False,
            }
        )
        res_ex_inc = policy.evaluate_step(ep_exit, auth, (), (), facts_exit_inc, venue.server_time_ms, cfg)
        assert res_ex_inc.episode_patch.short_liability_cleared is False
        assert not [a for a in res_ex_inc.actions if a.purpose == "LX"]
    finally:
        st.close()

    # 2. K-W5: Mixed settlement format (OTM stage-1 expiry + ITM direct option delivery) settles cleanly
    exp_ms = 1_795_507_200_000
    s1 = [{"id": 1, "type": "expiry", "instrument_name": ep.long_instrument, "amount": "0", "timestamp_ms": exp_ms + 50}]
    s2 = [{"id": 2, "type": "delivery", "instrument_name": ep.short_instrument, "amount": "0.1", "timestamp_ms": exp_ms + 60}]
    has_itm, all_legs, complete = contracts.compute_episode_expiry_settlement(
        short_instrument=ep.short_instrument,
        long_instrument=ep.long_instrument,
        es_qty=D("0.1"),
        close_short_qty=ZERO,
        ep_qty=D("0.1"),
        lx_qty=ZERO,
        stage1_rows=s1,
        stage2_rows=s2,
        positions=(),
        expiry_ms=exp_ms,
        server_time_ms=exp_ms + 100,
    )
    assert has_itm is False and all_legs is True and complete is True

    # 3. K-W7 & Info-4: update_command_state preserves last_error when omitted; ingest_order_trades dedupes batch trade_ids
    st_store = store.ExecutionStore(tmp_path / "store_kw7.sqlite3")
    try:
        st_store.create_episode(ep)
        cmd_u = contracts.ExecCommand(
            cmd_id=f"{ep.episode_id}:cmd_0001_EP",
            episode_id=ep.episode_id,
            purpose="EP",
            label="ax1:tn:kw7:EP:0001",
            instrument_name=ep.long_instrument,
            direction="buy",
            amount=D("0.1"),
            limit_price=D("0.0012"),
            time_in_force="IOC",
            reduce_only=False,
            post_only=False,
            reject_post_only=False,
            valid_until_ms=ep.expiry_ms,
            created_at_ms=ep.created_at_ms,
            status=contracts.CommandStatus.SUBMIT_UNKNOWN,
            updated_at_ms=ep.created_at_ms,
        )
        st_store.create_command_intent(cmd_u, now_ms=ep.created_at_ms)
        st_store.update_command_state(
            cmd_u.cmd_id,
            status=contracts.CommandStatus.SUBMIT_UNKNOWN,
            last_error="t5_injected_post_submit_ack_timeout",
            now_ms=ep.created_at_ms + 10,
        )
        st_store.update_command_state(
            cmd_u.cmd_id,
            status=contracts.CommandStatus.FILLED,
            venue_order_id="oid_kw7",
            filled_amount=D("0.1"),
            now_ms=ep.created_at_ms + 20,
        )
        cmds_loaded = st_store.get_commands(ep.episode_id)
        assert cmds_loaded[0].last_error == "t5_injected_post_submit_ack_timeout"

        dup_trade = contracts.VenueTrade(
            trade_id="dup_tid_1",
            order_id="oid_kw7",
            label=cmd_u.label,
            instrument_name=ep.long_instrument,
            direction="buy",
            amount=D("0.1"),
            price=D("0.0012"),
            fee=D("0.000015"),
            fee_currency="BTC",
            timestamp_ms=ep.created_at_ms + 15,
            liquidity="T",
        )
        ins_n, acc_qty = st_store.ingest_order_trades(
            ep, [dup_trade, dup_trade], subaccount_id=ep.subaccount_id, now_ms=ep.created_at_ms + 15
        )
        assert ins_n == 1 and acc_qty == D("0.1")
    finally:
        st_store.close()
