"""Pure deterministic execution policy engine (zero I/O, zero globals, live-exec-behavior@1.2.0)."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any, Collection, Mapping, Sequence

if __package__:
    from .astra_exec_contracts_v1 import (
        D,
        ONE,
        SAT,
        ZERO,
        ActionType,
        BookQuote,
        BookStatus,
        CapitalAuthorization,
        CommandStatus,
        EpisodePatch,
        EpisodeState,
        ExecCommand,
        ExecConfig,
        ExchangeFacts,
        ExchangeOrder,
        InstrumentSpec,
        LifecyclePhase,
        P5DecisionContract,
        PolicyAction,
        StepResult,
        ceil_1e8,
        classify_trade_attribution,
        classify_tx_log_attribution,
        compute_ep_max_price,
        compute_episode_expiry_settlement,
        compute_es_floor_price,
        compute_margin_and_cash_requirements,
        compute_tp_plan,
        conservative_split_buy_outlay_btc,
        facts_clock_skew_ms,
        matches_episode_future,
        option_trade_fee_btc,
        quant_qty,
        round_tick_down,
        round_tick_up,
        sweep_ask_price,
        sweep_bid_price,
        trade_purpose_for_episode,
    )
else:
    from astra_exec_contracts_v1 import (
        D,
        ONE,
        SAT,
        ZERO,
        ActionType,
        BookQuote,
        BookStatus,
        CapitalAuthorization,
        CommandStatus,
        EpisodePatch,
        EpisodeState,
        ExecCommand,
        ExecConfig,
        ExchangeFacts,
        ExchangeOrder,
        InstrumentSpec,
        LifecyclePhase,
        P5DecisionContract,
        PolicyAction,
        StepResult,
        ceil_1e8,
        classify_trade_attribution,
        classify_tx_log_attribution,
        compute_ep_max_price,
        compute_episode_expiry_settlement,
        compute_es_floor_price,
        compute_margin_and_cash_requirements,
        compute_tp_plan,
        conservative_split_buy_outlay_btc,
        facts_clock_skew_ms,
        matches_episode_future,
        option_trade_fee_btc,
        quant_qty,
        round_tick_down,
        round_tick_up,
        sweep_ask_price,
        sweep_bid_price,
        trade_purpose_for_episode,
    )

_VERIFIED_REVOKE_ACTIONS = {"REVOKE_ALIGNED", "REVOKE_DISCRETIONARY"}
_TERMINAL_VENUE_ORDER_STATES = {"filled", "cancelled", "rejected"}


def _book_fresh_ok(book: BookQuote | None, now_ms: int, max_age_ms: int) -> bool:
    if book is None or book.status == BookStatus.DATA_GAP or book.fetched_at_ms <= 0:
        return False
    age_ms = now_ms - book.fetched_at_ms
    return -500 <= age_ms <= max_age_ms


def _entry_capital_and_margin_ok(
    *,
    episode: EpisodeState,
    facts: ExchangeFacts,
    target_qty_btc: Decimal,
    paid_ep_outlay_btc: Decimal,
    paid_unmatched_ep_outlay_btc: Decimal,
    unpaid_prot_cost_btc: Decimal,
    effective_budget_btc: Decimal,
    config: ExecConfig,
) -> tuple[bool, str | None]:
    """Hard safety pre-check for B_eff, SM/PM initial margin, candidate operational cash, and MM buffer."""
    if effective_budget_btc <= ZERO:
        return False, "EFFECTIVE_BUDGET_EXHAUSTED"
    if facts.spot_usd <= ZERO:
        return False, "SPOT_INDEX_UNAVAILABLE"

    sb = facts.short_book
    short_mark = ZERO
    if sb is not None:
        if sb.mark_price is not None and sb.mark_price > ZERO:
            short_mark = sb.mark_price
        elif sb.ask_price is not None and sb.ask_price > ZERO:
            short_mark = sb.ask_price
        elif sb.bid_price is not None and sb.bid_price > ZERO:
            short_mark = sb.bid_price
    if not facts.portfolio_margining_enabled and short_mark <= ZERO:
        return False, "SHORT_MARK_UNAVAILABLE_FOR_SM"

    stress_loss, m_req, min_cash = compute_margin_and_cash_requirements(
        side=episode.side,
        short_strike_usd=episode.short_strike_usd,
        long_strike_usd=episode.long_strike_usd,
        spot_usd=facts.spot_usd,
        qty_btc=target_qty_btc,
        unpaid_prot_cost_btc=unpaid_prot_cost_btc,
        short_mark_btc=short_mark,
        portfolio_margining_enabled=facts.portfolio_margining_enabled,
        put_stress_spot_factor=config.put_stress_spot_factor,
        candidate_min_cash_pm_per_btc=config.candidate_min_cash_pm_per_btc,
        candidate_min_cash_sm_per_btc=config.candidate_min_cash_sm_per_btc,
    )
    # Non-self-locking episode risk demand <= B_eff (Spec 4.1.3)
    episode_risk_demand = stress_loss + paid_ep_outlay_btc + unpaid_prot_cost_btc
    if episode_risk_demand > effective_budget_btc:
        return False, "RISK_DEMAND_EXCEEDS_EFFECTIVE_BUDGET"
    if facts.available_funds_btc < m_req:
        return False, "AVAILABLE_FUNDS_BELOW_INITIAL_MARGIN_REQ"
    balance_val = facts.balance_btc if facts.balance_btc is not None else facts.equity_btc
    if (balance_val + paid_unmatched_ep_outlay_btc) < min_cash:
        return False, "BALANCE_BELOW_CANDIDATE_MIN_CASH"
    if (
        facts.maintenance_margin_btc > ZERO
        and facts.equity_btc < (facts.maintenance_margin_btc * config.min_margin_buffer_ratio)
    ):
        return False, "MARGIN_BUFFER_BELOW_MINIMUM"
    return True, None


def _collect_episode_trade_and_stage1_ledgers(
    episode: EpisodeState,
    commands: Sequence[ExecCommand],
    venue_events: Sequence[Mapping[str, Any]],
    facts: ExchangeFacts,
    *,
    foreign_event_uids: Collection[str],
    foreign_order_ids: Collection[str],
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
    dict[str, Decimal],
    dict[str, Decimal],
]:
    """Build deduplicated episode-attributed trade maps & Stage-1 expiry totals for UNKNOWN verification."""
    cmd_by_lbl = {c.label: c for c in commands}
    cmd_by_oid = {c.venue_order_id: c for c in commands if c.venue_order_id}
    trades_by_tid: dict[str, dict[str, Any]] = {}
    ep_order_ids = set(cmd_by_oid.keys())
    stage1_by_id: dict[int, dict[str, Any]] = {}

    for ev in venue_events:
        etype = ev.get("event_type")
        raw = ev.get("raw") or {}
        if etype == "USER_TRADE":
            tid = str(raw["trade_id"])
            trades_by_tid[tid] = dict(raw)
            if raw.get("order_id"):
                ep_order_ids.add(str(raw["order_id"]))
        elif etype == "TX_LOG":
            tx_t = str(raw.get("type") or "").lower()
            if tx_t in {"expiry", "exercise", "assignment"} and raw.get("id") is not None:
                stage1_by_id[int(raw["id"])] = dict(raw)

    for tr in facts.recent_trades:
        accepted, _ = classify_trade_attribution(
            tr,
            episode=episode,
            cmd_by_label=cmd_by_lbl,
            cmd_by_order_id=cmd_by_oid,
            facts_subaccount_id=facts.subaccount_id,
            foreign_event_uids=foreign_event_uids,
            foreign_order_ids=foreign_order_ids,
        )
        if not accepted:
            continue
        if tr.order_id:
            ep_order_ids.add(tr.order_id)
        trades_by_tid.setdefault(
            tr.trade_id,
            {
                "trade_id": tr.trade_id,
                "order_id": tr.order_id,
                "label": tr.label,
                "instrument_name": tr.instrument_name,
                "direction": tr.direction,
                "amount": str(tr.amount),
                "price": str(tr.price),
                "fee": str(tr.fee),
                "timestamp_ms": tr.timestamp_ms,
            },
        )

    ep_trade_ids = set(trades_by_tid.keys())
    for tx in facts.tx_logs:
        accepted, role = classify_tx_log_attribution(
            tx,
            episode=episode,
            ep_trade_ids=ep_trade_ids,
            ep_order_ids=ep_order_ids,
            facts_subaccount_id=facts.subaccount_id,
            has_open_option_at_expiry=True,
            foreign_event_uids=foreign_event_uids,
            foreign_order_ids=foreign_order_ids,
        )
        if accepted and role == "stage1":
            stage1_by_id.setdefault(
                tx.id,
                {
                    "id": tx.id,
                    "type": tx.type.lower(),
                    "instrument_name": tx.instrument_name,
                    "amount": str(tx.amount),
                },
            )

    trades_by_lbl: dict[str, list[dict[str, Any]]] = {}
    trades_by_oid: dict[str, list[dict[str, Any]]] = {}
    ep_net_signed: dict[str, Decimal] = {}
    for tr_raw in trades_by_tid.values():
        lbl = str(tr_raw.get("label") or "")
        oid = str(tr_raw.get("order_id") or "")
        inst = str(tr_raw.get("instrument_name") or "")
        amt = D(tr_raw["amount"])
        direction = str(tr_raw.get("direction") or "").lower()
        if lbl:
            trades_by_lbl.setdefault(lbl, []).append(tr_raw)
        if oid:
            trades_by_oid.setdefault(oid, []).append(tr_raw)
        signed = amt if direction == "buy" else -amt
        ep_net_signed[inst] = ep_net_signed.get(inst, ZERO) + signed

    stage1_abs_by_inst: dict[str, Decimal] = {}
    for st1 in stage1_by_id.values():
        inst = str(st1.get("instrument_name") or "")
        amt = abs(D(st1.get("amount") or 0))
        stage1_abs_by_inst[inst] = stage1_abs_by_inst.get(inst, ZERO) + amt

    return trades_by_lbl, trades_by_oid, ep_net_signed, stage1_abs_by_inst


def _reconcile_commands(
    episode: EpisodeState,
    commands: Sequence[ExecCommand],
    venue_events: Sequence[Mapping[str, Any]],
    facts: ExchangeFacts,
    now_ms: int,
    config: ExecConfig,
    alerts: list[str],
    *,
    foreign_event_uids: Collection[str],
    foreign_order_ids: Collection[str],
) -> tuple[list[ExecCommand], dict[str, tuple[CommandStatus, Decimal, str | None]], list[PolicyAction]]:
    """Complete-evidence UNKNOWN & OPEN order reconciliation (Spec 5.3 & Task Doc 3.4)."""
    open_by_oid: dict[str, ExchangeOrder] = {}
    open_by_lbl: dict[str, list[ExchangeOrder]] = {}
    for o in facts.open_orders:
        open_by_oid[o.order_id] = o
        if o.label:
            open_by_lbl.setdefault(o.label, []).append(o)

    trades_by_lbl, trades_by_oid, ep_net_signed, stage1_abs_by_inst = _collect_episode_trade_and_stage1_ledgers(
        episode,
        commands,
        venue_events,
        facts,
        foreign_event_uids=foreign_event_uids,
        foreign_order_ids=foreign_order_ids,
    )

    resolved: list[ExecCommand] = []
    resolutions: dict[str, tuple[CommandStatus, Decimal, str | None]] = {}
    reconcile_actions: list[PolicyAction] = []

    for cmd in commands:
        if not cmd.is_open_or_unknown:
            resolved.append(cmd)
            continue

        probe = facts.order_probes.get(cmd.cmd_id)
        lbl_open_list = open_by_lbl.get(cmd.label, [])
        hist_lbl_list = list(facts.recent_orders_by_label.get(cmd.label, ()))
        probe_matches = list(probe.matched_orders) if probe is not None else []

        all_candidates: list[ExchangeOrder] = []
        if cmd.venue_order_id and cmd.venue_order_id in open_by_oid:
            all_candidates.append(open_by_oid[cmd.venue_order_id])
        all_candidates.extend(lbl_open_list)
        all_candidates.extend(hist_lbl_list)
        all_candidates.extend(probe_matches)

        distinct_oids = {o.order_id for o in all_candidates if o.order_id}
        dup_label_conflict = bool(
            (probe is not None and probe.duplicate_label_conflict)
            or len(distinct_oids) > 1
            or (cmd.venue_order_id is not None and any(oid != cmd.venue_order_id for oid in distinct_oids))
        )
        identity_conflict = any(
            o.instrument_name != cmd.instrument_name or o.direction.lower() != cmd.direction.lower()
            for o in all_candidates
        )
        open_match = (
            open_by_oid.get(cmd.venue_order_id)
            if cmd.venue_order_id
            else (lbl_open_list[0] if len(lbl_open_list) == 1 else None)
        )
        terminal_candidates = [o for o in all_candidates if o.order_state in _TERMINAL_VENUE_ORDER_STATES]
        state_contradiction = bool(
            (probe is not None and probe.endpoint_contradiction)
            or (open_match is not None and len(terminal_candidates) > 0)
            or len({o.order_state for o in terminal_candidates}) > 1
        )

        if dup_label_conflict or identity_conflict or state_contradiction:
            if dup_label_conflict:
                alerts.append(f"ORDER_RECONCILE_CONFLICT_DUPLICATE_LABEL:{cmd.label}")
            if identity_conflict:
                alerts.append(f"ORDER_RECONCILE_CONFLICT_IDENTITY:{cmd.label}")
            if state_contradiction:
                alerts.append(f"ORDER_RECONCILE_CONFLICT_STATE:{cmd.label}")
            reconcile_actions.append(
                PolicyAction(
                    action_type=ActionType.RECONCILE_ORDER,
                    target_cmd_id=cmd.cmd_id,
                    target_label=cmd.label,
                    reason="CONFLICT_DETECTED_HOLD_RESERVATION",
                )
            )
            resolved.append(cmd)
            continue

        # Aggregate confirmed trade fills for this command
        tmap: dict[str, dict[str, Any]] = {}
        for t in trades_by_lbl.get(cmd.label, ()):
            tmap[str(t["trade_id"])] = t
        if cmd.venue_order_id:
            for t in trades_by_oid.get(cmd.venue_order_id, ()):
                tmap[str(t["trade_id"])] = t
        for cand_o in all_candidates:
            for t in trades_by_oid.get(cand_o.order_id, ()):
                tmap[str(t["trade_id"])] = t
        trades_filled = sum((D(t["amount"]) for t in tmap.values()), ZERO)

        obs_oid = cmd.venue_order_id
        if obs_oid is None:
            if open_match is not None:
                obs_oid = open_match.order_id
            elif terminal_candidates:
                obs_oid = terminal_candidates[-1].order_id
            elif tmap:
                first_oid = str(next(iter(tmap.values())).get("order_id") or "")
                obs_oid = first_oid or None

        obs_filled = max(
            [cmd.filled_amount, trades_filled]
            + [o.filled_amount for o in all_candidates]
        )
        new_status = cmd.status

        if open_match is not None:
            if cmd.status in {CommandStatus.PREPARED_UNKNOWN, CommandStatus.SUBMIT_UNKNOWN}:
                new_status = CommandStatus.OPEN
            elif cmd.status == CommandStatus.CANCEL_UNKNOWN:
                if cmd.cancel_requested_at_ms and (now_ms - cmd.cancel_requested_at_ms) >= config.cancel_slow_warn_ms:
                    alerts.append(f"CANCEL_SLOW_ALERT:{cmd.purpose}:{cmd.label}")
        elif not facts.open_orders_complete:
            reconcile_actions.append(
                PolicyAction(
                    action_type=ActionType.RECONCILE_ORDER,
                    target_cmd_id=cmd.cmd_id,
                    target_label=cmd.label,
                    reason="OPEN_ORDERS_INCOMPLETE",
                )
            )
        elif terminal_candidates:
            vo = terminal_candidates[-1]
            trades_for_vo = sum((D(t["amount"]) for t in trades_by_oid.get(vo.order_id, ())), ZERO)
            if trades_for_vo > vo.filled_amount:
                alerts.append(f"ORDER_TRADES_EXCEED_ORDER_FILLED:{cmd.label}")
                reconcile_actions.append(
                    PolicyAction(
                        action_type=ActionType.RECONCILE_ORDER,
                        target_cmd_id=cmd.cmd_id,
                        target_label=cmd.label,
                        reason="TRADES_EXCEED_VO_FILLED",
                    )
                )
            elif bool(facts.trades_complete) and trades_for_vo == vo.filled_amount:
                obs_oid = vo.order_id
                obs_filled = vo.filled_amount
                if vo.order_state == "filled" or (cmd.amount > ZERO and obs_filled >= cmd.amount):
                    new_status = CommandStatus.FILLED
                elif vo.order_state == "rejected" and obs_filled == ZERO:
                    new_status = CommandStatus.REJECTED
                else:
                    new_status = CommandStatus.CANCELLED
            else:
                reconcile_actions.append(
                    PolicyAction(
                        action_type=ActionType.RECONCILE_ORDER,
                        target_cmd_id=cmd.cmd_id,
                        target_label=cmd.label,
                        reason="WAIT_TRADES_MATCH_TERMINAL_VO",
                    )
                )
        else:
            # No open_match and no positive terminal order record found yet.
            if obs_oid is not None or cmd.status == CommandStatus.OPEN or trades_filled > ZERO:
                # Known order ID, previously OPEN order, or trade already observed: MUST get positive terminal vo!
                if cmd.status == CommandStatus.CANCEL_UNKNOWN and cmd.cancel_requested_at_ms and (
                    now_ms - cmd.cancel_requested_at_ms
                ) >= config.cancel_slow_warn_ms:
                    alerts.append(f"CANCEL_SLOW_ALERT:{cmd.purpose}:{cmd.label}")
                reconcile_actions.append(
                    PolicyAction(
                        action_type=ActionType.RECONCILE_ORDER,
                        target_cmd_id=cmd.cmd_id,
                        target_label=cmd.label,
                        reason="KNOWN_ORDER_AWAITING_TERMINAL_VO",
                    )
                )
            else:
                # Unknown venue_order_id (PREPARED_UNKNOWN / SUBMIT_UNKNOWN / precautionary CANCEL_UNKNOWN):
                # Enforce all 5 conjunctive negative-evidence proofs (Spec 5.3.2 & Task Doc 3.4).
                cmd_age_ms = max(0, now_ms - cmd.created_at_ms)
                if probe is None:
                    reconcile_actions.append(
                        PolicyAction(
                            action_type=ActionType.RECONCILE_ORDER,
                            target_cmd_id=cmd.cmd_id,
                            target_label=cmd.label,
                            reason="NEED_TARGETED_UNKNOWN_PROBE",
                        )
                    )
                elif cmd_age_ms >= config.label_hot_cache_limit_ms or probe.label_query_status == "EXPIRED_HOT_CACHE":
                    # Task Doc 3.4: Past 25m label hot-cache window without positive vo -> NEVER fake EMPTY_CONFIRMED!
                    alerts.append(f"UNKNOWN_UNVERIFIABLE_PAST_HOT_CACHE:{cmd.purpose}:{cmd.label}")
                else:
                    fresh_srv = probe.fresh_est_server_ms
                    cond_a = (
                        fresh_srv is not None
                        and facts.clock_rtt_ok
                        and fresh_srv > (cmd.valid_until_ms + config.clock_safety_margin_ms)
                    )
                    cond_b = facts.open_orders_complete and open_match is None
                    cond_c = probe.label_query_status == "EMPTY_CONFIRMED"
                    cond_d = (
                        probe.inst_history_recent_complete
                        and (cmd_age_ms < config.archive_history_threshold_ms or probe.inst_history_archive_complete)
                        and len(probe.matched_orders) == 0
                    )
                    cond_e = False
                    if bool(facts.trades_complete) and facts.positions_complete and fresh_srv is not None:
                        inst = cmd.instrument_name
                        ep_net = ep_net_signed.get(inst, ZERO)
                        venue_pos = sum((p.size for p in facts.positions if p.instrument_name == inst), ZERO)
                        if fresh_srv < episode.expiry_ms:
                            cond_e = ep_net == venue_pos
                        else:
                            pre_exp_abs = abs(ep_net)
                            st1_abs = stage1_abs_by_inst.get(inst, ZERO)
                            cond_e = (
                                bool(facts.tx_logs_complete)
                                and venue_pos == ZERO
                                and (
                                    (pre_exp_abs > ZERO and st1_abs == pre_exp_abs)
                                    or (pre_exp_abs == ZERO and st1_abs == ZERO and fresh_srv >= episode.expiry_ms + 5_000)
                                )
                            )
                    if cond_a and cond_b and cond_c and cond_d and cond_e:
                        new_status = CommandStatus.EXPIRED_UNFILLED
                    else:
                        reconcile_actions.append(
                            PolicyAction(
                                action_type=ActionType.RECONCILE_ORDER,
                                target_cmd_id=cmd.cmd_id,
                                target_label=cmd.label,
                                reason="AWAITING_5_NEGATIVE_PROOFS",
                            )
                        )

        if new_status != cmd.status or obs_filled != cmd.filled_amount or obs_oid != cmd.venue_order_id:
            resolutions[cmd.cmd_id] = (new_status, obs_filled, obs_oid)
            resolved.append(replace(cmd, status=new_status, filled_amount=obs_filled, venue_order_id=obs_oid))
        else:
            resolved.append(cmd)

    return resolved, resolutions, reconcile_actions


def _aggregate_episode_fills(
    episode: EpisodeState,
    commands: Sequence[ExecCommand],
    venue_events: Sequence[Mapping[str, Any]],
    facts: ExchangeFacts,
    *,
    foreign_event_uids: Collection[str] = (),
    foreign_order_ids: Collection[str] = (),
) -> dict[str, Any]:
    cmd_by_lbl = {c.label: c for c in commands}
    cmd_by_oid = {c.venue_order_id: c for c in commands if c.venue_order_id}
    user_trades: dict[str, dict[str, Any]] = {}
    tx_trade: dict[str, dict[str, Any]] = {}
    stage1_rows: list[dict[str, Any]] = []
    stage2_rows: list[dict[str, Any]] = []
    ep_order_ids = set(cmd_by_oid.keys())

    for ev in venue_events:
        etype = ev.get("event_type")
        raw = ev.get("raw") or {}
        if etype == "USER_TRADE":
            user_trades[str(raw["trade_id"])] = raw
            if raw.get("order_id"):
                ep_order_ids.add(str(raw["order_id"]))
        elif etype == "TX_LOG":
            tx_type = str(raw.get("type") or "").lower()
            if tx_type == "trade" and raw.get("trade_id"):
                tx_trade[str(raw["trade_id"])] = raw
                if raw.get("order_id"):
                    ep_order_ids.add(str(raw["order_id"]))
            elif tx_type in {"expiry", "exercise", "assignment"}:
                stage1_rows.append(raw)
            elif tx_type == "delivery" and (
                matches_episode_future(str(raw.get("instrument_name") or ""), episode.short_instrument)
                or str(raw.get("instrument_name") or "") in {episode.short_instrument, episode.long_instrument}
            ):
                stage2_rows.append(raw)

    for tr in facts.recent_trades:
        accepted, _ = classify_trade_attribution(
            tr,
            episode=episode,
            cmd_by_label=cmd_by_lbl,
            cmd_by_order_id=cmd_by_oid,
            facts_subaccount_id=facts.subaccount_id,
            foreign_event_uids=foreign_event_uids,
            foreign_order_ids=foreign_order_ids,
        )
        if not accepted:
            continue
        if tr.order_id:
            ep_order_ids.add(tr.order_id)
        user_trades.setdefault(
            tr.trade_id,
            {
                "trade_id": tr.trade_id,
                "order_id": tr.order_id,
                "label": tr.label,
                "instrument_name": tr.instrument_name,
                "direction": tr.direction,
                "amount": str(tr.amount),
                "price": str(tr.price),
                "fee": str(tr.fee),
                "timestamp_ms": tr.timestamp_ms,
            },
        )

    pre_ep_qty = ZERO
    pre_es_qty = ZERO
    pre_close_short = ZERO
    pre_lx_qty = ZERO
    latest_ep_trade_ms: int | None = None
    for tid in sorted(user_trades):
        tr = user_trades[tid]
        purpose = trade_purpose_for_episode(tr, episode=episode, cmd_by_label=cmd_by_lbl, cmd_by_order_id=cmd_by_oid)
        q = D(tr["amount"])
        if purpose == "EP":
            pre_ep_qty += q
            ts_tr = int(tr.get("timestamp_ms") or 0)
            if ts_tr > 0:
                latest_ep_trade_ms = max(latest_ep_trade_ms or 0, ts_tr)
        elif purpose == "ES":
            pre_es_qty += q
        elif purpose in {"TP", "RX"}:
            pre_close_short += q
        elif purpose == "LX":
            pre_lx_qty += q
    has_open_at_expiry = (pre_ep_qty > pre_lx_qty) or (pre_es_qty > pre_close_short) or bool(stage1_rows)
    ep_trade_ids = set(user_trades.keys())

    for tx in facts.tx_logs:
        accepted, tx_role = classify_tx_log_attribution(
            tx,
            episode=episode,
            ep_trade_ids=ep_trade_ids,
            ep_order_ids=ep_order_ids,
            facts_subaccount_id=facts.subaccount_id,
            has_open_option_at_expiry=has_open_at_expiry,
            foreign_event_uids=foreign_event_uids,
            foreign_order_ids=foreign_order_ids,
        )
        if not accepted:
            continue
        tx_type = tx.type.lower()
        inst = tx.instrument_name
        if tx_role == "trade" and tx.trade_id:
            tx_trade.setdefault(
                tx.trade_id,
                {
                    "id": tx.id,
                    "trade_id": tx.trade_id,
                    "order_id": tx.order_id,
                    "instrument_name": inst,
                    "amount": str(tx.amount),
                    "price": str(tx.price) if tx.price is not None else "0",
                    "commission": str(tx.commission),
                    "cashflow": str(tx.cashflow),
                    "change": str(tx.change),
                },
            )
        elif tx_role == "stage1":
            has_open_at_expiry = True
            if not any(int(r.get("id", -1)) == tx.id for r in stage1_rows):
                stage1_rows.append(
                    {
                        "id": tx.id,
                        "type": tx_type,
                        "instrument_name": inst,
                        "amount": str(tx.amount),
                        "timestamp_ms": tx.timestamp_ms,
                    }
                )
        elif tx_role == "stage2":
            if not any(int(r.get("id", -1)) == tx.id for r in stage2_rows):
                stage2_rows.append(
                    {
                        "id": tx.id,
                        "type": tx_type,
                        "instrument_name": inst,
                        "amount": str(tx.amount),
                        "timestamp_ms": tx.timestamp_ms,
                    }
                )

    totals: dict[str, Any] = {
        "ep_qty": ZERO,
        "ep_gross": ZERO,
        "ep_fee": ZERO,
        "es_qty": ZERO,
        "es_gross": ZERO,
        "es_fee": ZERO,
        "tp_qty": ZERO,
        "tp_gross": ZERO,
        "tp_fee": ZERO,
        "rx_qty": ZERO,
        "rx_gross": ZERO,
        "rx_fee": ZERO,
        "lx_qty": ZERO,
        "lx_gross": ZERO,
        "lx_fee": ZERO,
        "missing_tx_count": 0,
        "latest_ep_trade_ms": latest_ep_trade_ms,
    }
    for tid in sorted(set(user_trades) | set(tx_trade)):
        tr = user_trades.get(tid) or {}
        tx = tx_trade.get(tid)
        if tx is None:
            totals["missing_tx_count"] += 1
        qty = D(tr["amount"]) if "amount" in tr else D(tx["amount"])
        price = D(tr["price"]) if tr.get("price") is not None else D(tx["price"])
        fee = D(tx["commission"]) if tx is not None else D(tr["fee"])
        purpose = trade_purpose_for_episode(
            tr, episode=episode, cmd_by_label=cmd_by_lbl, cmd_by_order_id=cmd_by_oid, tx_or_raw=tx
        )
        gross = qty * price
        k = purpose.lower()
        if f"{k}_qty" in totals:
            totals[f"{k}_qty"] += qty
            totals[f"{k}_gross"] += gross
            totals[f"{k}_fee"] += fee

    close_short_qty = totals["tp_qty"] + totals["rx_qty"]
    _, _, exp_complete = compute_episode_expiry_settlement(
        short_instrument=episode.short_instrument,
        long_instrument=episode.long_instrument,
        es_qty=totals["es_qty"],
        close_short_qty=close_short_qty,
        ep_qty=totals["ep_qty"],
        lx_qty=totals["lx_qty"],
        stage1_rows=stage1_rows,
        stage2_rows=stage2_rows,
        positions=facts.positions,
        expiry_ms=episode.expiry_ms,
        server_time_ms=facts.server_time_ms,
    )
    totals["stage1_count"] = len(stage1_rows)
    totals["stage2_count"] = len(stage2_rows)
    totals["expiry_settlement_complete"] = exp_complete
    return totals


def _freeze_entry_closure(
    *,
    close_reason: str,
    fills: Mapping[str, Any],
    exit_latched: bool,
    now_ms: int,
    config: ExecConfig,
    short_spec: InstrumentSpec | None,
    alerts: list[str],
) -> dict[str, Any]:
    ep_filled = fills["ep_qty"]
    es_filled = fills["es_qty"]
    matched_qty = min(ep_filled, es_filled)
    if ep_filled > ZERO and matched_qty > ZERO:
        matched_prot_ex_fee = ceil_1e8(fills["ep_gross"] * matched_qty / ep_filled)
        matched_ep_fee = ceil_1e8(fills["ep_fee"] * matched_qty / ep_filled)
    else:
        matched_prot_ex_fee = ZERO
        matched_ep_fee = ZERO
    if es_filled > ZERO and matched_qty > ZERO:
        matched_short_proceeds = (fills["es_gross"] * matched_qty / es_filled).quantize(SAT)
        matched_es_fee = ceil_1e8(fills["es_fee"] * matched_qty / es_filled)
    else:
        matched_short_proceeds = ZERO
        matched_es_fee = ZERO
    matched_entry_fees = matched_ep_fee + matched_es_fee
    matched_net_credit = matched_short_proceeds - matched_prot_ex_fee - matched_entry_fees
    unmatched_prot_qty = max(ZERO, ep_filled - matched_qty)
    unmatched_prot_cost = (fills["ep_gross"] + fills["ep_fee"]) - (matched_prot_ex_fee + matched_ep_fee)

    tp_budget_total = ZERO
    tp_cap_init = ZERO
    tp_target_capture_ratio: Decimal | None = None
    tp_substate: str | None = None
    if matched_qty > ZERO and not exit_latched and short_spec is not None:
        plan = compute_tp_plan(
            matched_net_credit_btc=matched_net_credit,
            short_qty_btc=matched_qty,
            short_spec=short_spec,
            tp_spent_btc=ZERO,
            profit_capture_ratio=config.tp_profit_capture_ratio,
        )
        tp_budget_total = plan.budget_total_btc
        tp_cap_init = plan.limit_price_btc
        tp_target_capture_ratio = plan.target_capture_ratio
        tp_substate = plan.substate
        if plan.alert:
            alerts.append(plan.alert)

    return {
        "entry_closed": True,
        "entry_closed_at_ms": now_ms,
        "entry_closure_reason": close_reason,
        "matched_qty": matched_qty,
        "matched_short_proceeds": matched_short_proceeds,
        "matched_prot_ex_fee": matched_prot_ex_fee,
        "matched_entry_fees": matched_entry_fees,
        "matched_net_credit": matched_net_credit,
        "unmatched_prot_qty": unmatched_prot_qty,
        "unmatched_prot_cost": unmatched_prot_cost,
        "tp_budget_total": tp_budget_total,
        "tp_cap_init": tp_cap_init,
        "tp_target_capture_ratio": tp_target_capture_ratio,
        "tp_substate": tp_substate,
    }


def evaluate_step(
    episode: EpisodeState,
    capital_auth: CapitalAuthorization,
    commands: Sequence[ExecCommand],
    venue_events: Sequence[Mapping[str, Any]],
    facts: ExchangeFacts,
    now_ms: int,
    config: ExecConfig,
    *,
    effective_budget_btc: Decimal | None = None,
    p5_contract: P5DecisionContract | None = None,
    p5_condition_triggered: bool = False,
    writes_disabled: bool = False,
    foreign_event_uids: Collection[str] = (),
    foreign_order_ids: Collection[str] = (),
) -> StepResult:
    """Single deterministic control-step transition (live-exec-behavior@1.2.0)."""
    alerts: list[str] = list(episode.alerts)
    notes: list[str] = []

    def _dedup_alerts(xs: list[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        out: list[str] = []
        for x in xs:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return tuple(out[-24:])

    b_eff = effective_budget_btc if effective_budget_btc is not None else episode.effective_budget_at_entry_btc

    # 1. Reconcile active/unknown commands first via complete-evidence rules
    eff_cmds, resolutions, reconcile_actions = _reconcile_commands(
        episode,
        commands,
        venue_events,
        facts,
        now_ms,
        config,
        alerts,
        foreign_event_uids=foreign_event_uids,
        foreign_order_ids=foreign_order_ids,
    )
    fills = _aggregate_episode_fills(
        episode,
        eff_cmds,
        venue_events,
        facts,
        foreign_event_uids=foreign_event_uids,
        foreign_order_ids=foreign_order_ids,
    )

    ep_filled = fills["ep_qty"]
    es_filled = fills["es_qty"]
    close_short_filled = fills["tp_qty"] + fills["rx_qty"]
    lx_filled = fills["lx_qty"]

    # 2. Venue positions & anomalies
    short_pos = ZERO
    prot_pos = ZERO
    ext_opt_pos = ZERO
    ext_fut_pos = ZERO
    stage2_fut_pos = ZERO
    for p in facts.positions:
        if p.instrument_name == episode.short_instrument:
            short_pos += p.size
        elif p.instrument_name == episode.long_instrument:
            prot_pos += p.size
        elif matches_episode_future(p.instrument_name, episode.short_instrument):
            if facts.server_time_ms >= episode.expiry_ms:
                stage2_fut_pos += p.size
            else:
                ext_fut_pos += p.size
        else:
            if p.kind == "future":
                ext_fut_pos += p.size
            else:
                ext_opt_pos += p.size

    short_qty = max(ZERO, -short_pos)
    if short_pos > ZERO:
        alerts.append("UNEXPECTED_LONG_IN_SHORT_LEG")
    if prot_pos < ZERO:
        alerts.append("UNEXPECTED_SHORT_IN_LONG_LEG")
    if ext_opt_pos != ZERO or ext_fut_pos != ZERO:
        alerts.append("EXTERNAL_UNATTRIBUTED_POSITION")
    if stage2_fut_pos != ZERO:
        alerts.append("STAGE2_DELIVERY_FUTURE_PENDING")

    naked_breach = facts.api_error is None and facts.positions_complete and (prot_pos < short_qty)
    if naked_breach:
        alerts.append("NAKED_SHORT_BREACH")

    clock_skew_high = facts_clock_skew_ms(facts, now_ms) > config.max_clock_skew_ms
    if clock_skew_high:
        alerts.append("CLOCK_SKEW_HIGH")
    venue_now_ms = facts.server_time_ms if facts.server_time_ms > 0 else now_ms

    # 3. Unknowns and active reservations
    ep_cmds = [c for c in eff_cmds if c.purpose == "EP"]
    es_cmds = [c for c in eff_cmds if c.purpose == "ES"]
    tp_cmds = [c for c in eff_cmds if c.purpose == "TP"]
    rx_cmds = [c for c in eff_cmds if c.purpose == "RX"]
    lx_cmds = [c for c in eff_cmds if c.purpose == "LX"]

    entry_cmds_open_or_unknown = [c for c in (*ep_cmds, *es_cmds) if c.is_open_or_unknown]
    any_unknown = any(c.is_blocking_unknown for c in eff_cmds)
    has_unknown_ep = any(c.is_blocking_unknown for c in ep_cmds)
    has_unknown_tp = any(c.is_blocking_unknown for c in tp_cmds)
    has_unknown_rx = any(c.is_blocking_unknown for c in rx_cmds)
    has_unknown_lx = any(c.is_blocking_unknown for c in lx_cmds)

    reserved_es_rem = sum((c.reserved_remaining for c in es_cmds if c.is_open_or_unknown), ZERO)
    pending_ep_rem = sum((c.reserved_remaining for c in ep_cmds if c.is_open_or_unknown), ZERO)
    pending_lx_rem = sum((c.reserved_remaining for c in lx_cmds if c.is_open_or_unknown), ZERO)
    pending_tp_rem = sum((c.reserved_remaining for c in tp_cmds if c.is_open_or_unknown), ZERO)
    pending_rx_rem = sum((c.reserved_remaining for c in rx_cmds if c.is_open_or_unknown), ZERO)

    ep_cmd_filled = sum((c.filled_amount for c in ep_cmds), ZERO)
    es_cmd_filled = sum((c.filled_amount for c in es_cmds), ZERO)
    close_cmd_filled = sum((c.filled_amount for c in (*tp_cmds, *rx_cmds)), ZERO)
    lx_cmd_filled = sum((c.filled_amount for c in lx_cmds), ZERO)
    ep_trades_lagging = ep_cmd_filled > ep_filled
    es_trades_lagging = es_cmd_filled > es_filled
    close_trades_lagging = close_cmd_filled > close_short_filled
    lx_trades_lagging = lx_cmd_filled > lx_filled
    pre_expiry = venue_now_ms < episode.expiry_ms
    exp_prot_from_fills = max(ZERO, ep_filled - lx_filled)
    exp_short_from_fills = max(ZERO, es_filled - close_short_filled)
    prot_pos_lagging = pre_expiry and (prot_pos < exp_prot_from_fills)
    short_pos_lagging = pre_expiry and (short_qty < exp_short_from_fills)
    entry_sync_lagging = ep_trades_lagging or es_trades_lagging or prot_pos_lagging or short_pos_lagging
    close_sync_lagging = close_trades_lagging or lx_trades_lagging
    any_sync_lagging = entry_sync_lagging or close_sync_lagging
    eff_short_for_res = max(short_qty, exp_short_from_fills, max(ZERO, es_cmd_filled - close_short_filled))

    reserved_short = eff_short_for_res + reserved_es_rem
    available_prot = max(ZERO, prot_pos - pending_lx_rem)

    entry_started_at_ms = episode.entry_started_at_ms
    if entry_started_at_ms is None and (ep_cmds or ep_filled > ZERO or es_filled > ZERO):
        entry_started_at_ms = min([c.created_at_ms for c in (*ep_cmds, *es_cmds)] + [now_ms])

    entry_spot_usd = episode.entry_spot_usd
    if entry_spot_usd is None and facts.spot_usd > ZERO and (ep_filled > ZERO or es_filled > ZERO):
        entry_spot_usd = facts.spot_usd

    last_ep_fill_at_ms = episode.last_ep_fill_at_ms
    if ep_filled > ZERO:
        filled_ep_cmd_ms = max(
            [c.updated_at_ms or c.created_at_ms for c in ep_cmds if c.filled_amount > ZERO] + [0]
        )
        cand_ep_ms = max(fills["latest_ep_trade_ms"] or 0, filled_ep_cmd_ms)
        if last_ep_fill_at_ms is None:
            last_ep_fill_at_ms = cand_ep_ms if cand_ep_ms > 0 else now_ms
        elif cand_ep_ms > last_ep_fill_at_ms:
            last_ep_fill_at_ms = cand_ep_ms

    # 4. P5 Alerts, Irreversible Exit Latch & Entry Halt triggers (Spec 2.2.2)
    if p5_contract is not None and p5_contract.hold_review_llm_unavailable:
        alerts.append("HOLD_REVIEW_LLM_UNAVAILABLE")

    p5_revoked = bool(
        p5_contract is not None
        and p5_contract.revoked
        and p5_contract.revoke_shadow_action in _VERIFIED_REVOKE_ACTIONS
    )
    if p5_contract and p5_contract.revoked and p5_contract.revoke_shadow_action not in _VERIFIED_REVOKE_ACTIONS:
        alerts.append(f"P5_UNVERIFIED_REVOKE_IGNORED:{p5_contract.revoke_shadow_action}")

    operator_exit_all = episode.operator_exit_all
    exit_latched = episode.exit_latched
    exit_latch_reason = episode.exit_latch_reason
    exit_started_at_ms = episode.exit_started_at_ms

    if not exit_latched:
        if operator_exit_all:
            exit_latched = True
            exit_latch_reason = "OPERATOR_EXIT_ALL"
            exit_started_at_ms = exit_started_at_ms if exit_started_at_ms is not None else now_ms
        elif p5_revoked:
            exit_latched = True
            exit_latch_reason = "AI_REVOKE"
            # Spec 1.2.3 & 3.2: anchor exit_started_at_ms strictly to local latching now_ms!
            exit_started_at_ms = exit_started_at_ms if exit_started_at_ms is not None else now_ms
        elif naked_breach:
            exit_latched = True
            exit_latch_reason = "NAKED_SHORT_BREACH"
            exit_started_at_ms = exit_started_at_ms if exit_started_at_ms is not None else now_ms
    elif operator_exit_all and exit_latch_reason != "OPERATOR_EXIT_ALL":
        exit_latch_reason = "OPERATOR_EXIT_ALL"

    cond_triggered = bool(p5_condition_triggered or (p5_contract is not None and p5_contract.condition_triggered))

    entry_halted = episode.entry_halted or exit_latched or writes_disabled or (not capital_auth.entry_armed)
    entry_halt_reason = episode.entry_halt_reason
    if exit_latched and not entry_halt_reason:
        entry_halt_reason = exit_latch_reason
    elif writes_disabled and not entry_halt_reason:
        entry_halt_reason = "WRITES_DISABLED"
    elif not capital_auth.entry_armed and not episode.entry_closed and not entry_halt_reason:
        entry_halt_reason = "OPERATOR_PAUSE_ENTRY"

    short_spec = facts.short_spec
    long_spec = facts.long_spec
    min_s = short_spec.min_trade_amount if short_spec is not None else Decimal("0.1")
    min_l = long_spec.min_trade_amount if long_spec is not None else Decimal("0.1")
    target_qty = episode.target_qty_btc
    uncovered_prot = quant_qty(available_prot - reserved_short, min_s)
    need_short = quant_qty(target_qty - es_filled - reserved_es_rem, min_s)
    need_prot = quant_qty(target_qty - ep_filled - pending_ep_rem, min_l)

    if not episode.entry_closed and not entry_halted:
        if cond_triggered:
            # Spec 2.2.2: Pre-entry_closed CONDITION_TRIGGERED halts new entry ONLY, NEVER exit_latched!
            entry_halted = True
            entry_halt_reason = "P5_CONDITION_TRIGGERED"
        elif (now_ms - episode.created_at_ms) >= config.entry_window_ms:
            entry_halted = True
            entry_halt_reason = "ENTRY_WINDOW_EXPIRED"
        elif uncovered_prot >= min_s and need_short >= min_s:
            if venue_now_ms >= (episode.expiry_ms - config.es_submit_cutoff_ms):
                entry_halted = True
                entry_halt_reason = "ES_PRE_EXPIRY_CUTOFF"
            elif last_ep_fill_at_ms is not None and (now_ms - last_ep_fill_at_ms) >= config.es_rescue_window_ms:
                entry_halted = True
                entry_halt_reason = "ES_RESCUE_WINDOW_EXPIRED"
        elif need_prot >= min_l and venue_now_ms >= (episode.expiry_ms - config.min_entry_dte_ms):
            entry_halted = True
            entry_halt_reason = "EP_PRE_EXPIRY_CUTOFF"
        if not entry_halted and (clock_skew_high or ext_opt_pos != ZERO or ext_fut_pos != ZERO):
            entry_halted = True
            entry_halt_reason = "RUNTIME_ANOMALY_HALT"

    # 5. Formal Irreversible entry_closed
    entry_closed = episode.entry_closed
    entry_closed_at_ms = episode.entry_closed_at_ms
    entry_closure_reason = episode.entry_closure_reason
    matched_qty = episode.matched_qty_btc
    matched_short_proceeds = episode.matched_short_proceeds_btc
    matched_prot_ex_fee = episode.matched_prot_cost_ex_fee_btc
    matched_entry_fees = episode.matched_entry_fees_btc
    matched_net_credit = episode.matched_net_credit_btc
    unmatched_prot_qty = episode.unmatched_prot_qty_btc
    unmatched_prot_cost = episode.unmatched_prot_cost_btc
    tp_budget_total = episode.tp_budget_total_btc
    tp_cap_init = episode.tp_cap_init_btc
    tp_target_capture_ratio = episode.tp_target_capture_ratio
    tp_substate = episode.tp_substate
    estimated_abort_loss_at_ep = episode.estimated_abort_loss_at_ep_btc

    if (
        not entry_closed
        and facts.api_error is None
        and not entry_cmds_open_or_unknown
        and not entry_sync_lagging
        and (es_filled == ZERO or exit_latched or short_spec is not None)
    ):
        close_reason: str | None = None
        if es_filled >= target_qty:
            close_reason = "FULL_TARGET_FILLED"
        elif exit_latched:
            close_reason = f"PREEMPTED_BY_{exit_latch_reason or 'EXIT'}"
        elif entry_halted:
            if es_filled > ZERO:
                close_reason = f"PARTIAL_SHRUNK_ENTRY:{entry_halt_reason or 'HALTED'}"
            elif ep_filled > ZERO:
                close_reason = f"LONG_ONLY_ABORT:{entry_halt_reason or 'HALTED'}"
            else:
                close_reason = f"ABORTED_ZERO_FILL:{entry_halt_reason or 'HALTED'}"

        if close_reason is not None:
            frozen_ep = _freeze_entry_closure(
                close_reason=close_reason,
                fills=fills,
                exit_latched=exit_latched,
                now_ms=now_ms,
                config=config,
                short_spec=short_spec,
                alerts=alerts,
            )
            entry_closed = True
            entry_closed_at_ms = frozen_ep["entry_closed_at_ms"]
            entry_closure_reason = frozen_ep["entry_closure_reason"]
            entry_halted = True
            matched_qty = frozen_ep["matched_qty"]
            matched_short_proceeds = frozen_ep["matched_short_proceeds"]
            matched_prot_ex_fee = frozen_ep["matched_prot_ex_fee"]
            matched_entry_fees = frozen_ep["matched_entry_fees"]
            matched_net_credit = frozen_ep["matched_net_credit"]
            unmatched_prot_qty = frozen_ep["unmatched_prot_qty"]
            unmatched_prot_cost = frozen_ep["unmatched_prot_cost"]
            tp_budget_total = frozen_ep["tp_budget_total"]
            tp_cap_init = frozen_ep["tp_cap_init"]
            tp_target_capture_ratio = frozen_ep["tp_target_capture_ratio"]
            tp_substate = frozen_ep["tp_substate"]

    # Track 10009 cash wall state from commands & episode
    rx_cash_wall_10009 = episode.rx_cash_wall_10009
    if rx_cmds:
        last_rx = rx_cmds[-1]
        if last_rx.status == CommandStatus.REJECTED and last_rx.last_error and (
            "10009" in last_rx.last_error or "not_enough_funds" in last_rx.last_error.lower()
        ):
            rx_cash_wall_10009 = True
        elif last_rx.status == CommandStatus.FILLED:
            rx_cash_wall_10009 = False
    if short_qty == ZERO:
        rx_cash_wall_10009 = False
    if exit_latched and rx_cash_wall_10009 and short_qty > ZERO:
        alerts.append("RX_MARGIN_OR_CASH_EXHAUSTED_SPREAD_HEDGED_MANUAL_DECISION")

    rx_quote_samples = episode.rx_quote_samples

    # Shared milestone predicate (Spec 2.1): reused by Branch 3 (LX) and Section 7
    short_liability_cleared = (
        facts.api_error is None
        and facts.positions_complete
        and facts.open_orders_complete
        and short_qty == ZERO
        and not es_trades_lagging
        and not close_trades_lagging
        and not short_pos_lagging
        and not any(c.purpose in {"ES", "TP", "RX"} and c.is_open_or_unknown for c in eff_cmds)
    )

    # 6. Action Selection
    actions: list[PolicyAction] = []
    if writes_disabled:
        for ra in reconcile_actions:
            actions.append(ra)
        if any(c.is_open_or_unknown for c in eff_cmds) or short_qty > ZERO or prot_pos > ZERO:
            alerts.append("WRITES_DISABLED_OPEN_ORDERS_OR_RISK_MANUAL_TAKEOVER")
    elif facts.api_error is not None or (
        facts.rate_limited_until_ms is not None and now_ms < facts.rate_limited_until_ms
    ):
        alerts.append(f"VENUE_DEGRADED:{facts.api_error or 'RATE_LIMITED'}")
    else:
        for ra in reconcile_actions:
            actions.append(ra)

        # Cancel any entry orders if entry halted or exit latched
        if entry_halted or exit_latched:
            for c in entry_cmds_open_or_unknown:
                if c.status in {CommandStatus.OPEN, CommandStatus.CANCEL_UNKNOWN} or (
                    c.status in {CommandStatus.PREPARED_UNKNOWN, CommandStatus.SUBMIT_UNKNOWN}
                    and venue_now_ms <= (c.valid_until_ms + config.clock_safety_margin_ms)
                ):
                    actions.append(
                        PolicyAction(
                            action_type=ActionType.CANCEL_BY_LABEL,
                            target_cmd_id=c.cmd_id,
                            target_label=c.label,
                            reason=exit_latch_reason or entry_halt_reason or "HALT_ENTRY",
                        )
                    )

        # Branch 1: Irreversible Exit Path (Spec 3.2 RX: exact boolean complement + 10009 hedged backoff)
        if exit_latched:
            for c in tp_cmds:
                if c.is_open_or_unknown:
                    actions.append(
                        PolicyAction(
                            action_type=ActionType.CANCEL_BY_LABEL,
                            target_cmd_id=c.cmd_id,
                            target_label=c.label,
                            reason=exit_latch_reason or "REVOKE_CANCEL_TP",
                        )
                    )

            sb = facts.short_book
            sb_fresh = _book_fresh_ok(sb, now_ms, config.max_book_fetch_age_ms)
            if (
                short_qty > ZERO
                and sb_fresh
                and sb is not None
                and sb.ask_price is not None
                and sb.ask_price > ZERO
                and sb.max_price is not None
                and sb.max_price > ZERO
            ):
                rx_quote_samples += 1

            can_submit_rx = (
                short_spec is not None
                and short_qty > ZERO
                and not entry_cmds_open_or_unknown
                and pending_tp_rem == ZERO
                and pending_rx_rem == ZERO
                and not has_unknown_tp
                and not has_unknown_rx
                and venue_now_ms < (episode.expiry_ms - config.rx_submit_cutoff_ms)
            )
            if can_submit_rx and short_spec is not None:
                wait_ms = 0
                if rx_cash_wall_10009 and episode.last_rx_attempt_ms > 0:
                    wait_ms = config.rx_cash_wall_backoff_ms
                elif episode.rx_attempts > 0 and episode.last_rx_attempt_ms > 0:
                    sched = config.rx_retry_schedule_ms
                    idx = min(episode.rx_attempts - 1, len(sched) - 1)
                    wait_ms = sched[idx]
                if wait_ms == 0 or (now_ms - episode.last_rx_attempt_ms) >= wait_ms:
                    buy_qty = quant_qty(short_qty - pending_rx_rem, short_spec.min_trade_amount)
                    if buy_qty >= short_spec.min_trade_amount:
                        if (
                            not sb_fresh
                            or sb is None
                            or sb.ask_price is None
                            or sb.ask_price <= ZERO
                            or sb.max_price is None
                            or sb.max_price <= ZERO
                        ):
                            alerts.append("QUOTE_UNAVAILABLE_AT_REVOKE")
                        else:
                            dt_ms = max(0, now_ms - (exit_started_at_ms if exit_started_at_ms is not None else now_ms))
                            is_stage2 = bool(
                                operator_exit_all
                                or (dt_ms >= config.rx_stage1_window_ms and rx_quote_samples >= config.rx_stage1_min_samples)
                            )
                            p_cap: Decimal | None = None
                            if is_stage2:
                                p_cap = sb.max_price
                                if sb.mark_price is None or sb.mark_price <= ZERO:
                                    alerts.append("MARK_MISSING_AT_REVOKE")
                            else:
                                # Exact boolean complement: Stage 1 requires valid mark_price > 0
                                if sb.mark_price is None or sb.mark_price <= ZERO:
                                    alerts.append("QUOTE_UNAVAILABLE_AT_REVOKE")
                                else:
                                    mark_guard = max(
                                        config.rx_stage1_mark_mult * sb.mark_price,
                                        sb.mark_price + config.rx_stage1_mark_add_btc,
                                    )
                                    p_cap = min(sb.max_price, mark_guard)

                            if p_cap is not None:
                                cap_tick = round_tick_down(p_cap, short_spec)
                                ask_tick = round_tick_down(sb.ask_price, short_spec)
                                if ask_tick <= cap_tick and cap_tick >= short_spec.tick_size:
                                    ask_sweep = sweep_ask_price(sb, buy_qty) or sb.ask_price
                                    limit_cand = round_tick_down(min(max(ask_sweep, sb.ask_price), p_cap), short_spec)
                                    if sb.min_price is not None and limit_cand < sb.min_price:
                                        alerts.append("EXIT_PRICE_BELOW_EXCHANGE_MIN_BAND")
                                    elif limit_cand >= short_spec.tick_size:
                                        actions.append(
                                            PolicyAction(
                                                action_type=ActionType.SUBMIT,
                                                purpose="RX",
                                                instrument_name=episode.short_instrument,
                                                direction="buy",
                                                amount=buy_qty,
                                                limit_price=limit_cand,
                                                time_in_force="IOC",
                                                reduce_only=True,
                                                post_only=False,
                                                reject_post_only=False,
                                                reason=exit_latch_reason or "AI_REVOKE",
                                            )
                                        )
                                else:
                                    notes.append("RX_STAGE1_GUARD_WAIT")

        # Branch 2: Sole Resting 85% GTC TP (kept into 08:00Z expiry; dynamic fee solver + != short_qty resync)
        elif entry_closed and short_qty > ZERO and short_spec is not None:
            tp_spent = fills["tp_gross"] + fills["tp_fee"]
            plan = compute_tp_plan(
                matched_net_credit_btc=matched_net_credit,
                short_qty_btc=short_qty,
                short_spec=short_spec,
                tp_spent_btc=tp_spent,
                tp_budget_total_btc=tp_budget_total,
                tp_cap_init_btc=tp_cap_init if tp_cap_init > ZERO else None,
                profit_capture_ratio=config.tp_profit_capture_ratio,
            )
            tp_target_capture_ratio = plan.target_capture_ratio
            open_by_label = {o.label for o in facts.open_orders if o.label}
            active_tp = [c for c in tp_cmds if c.is_open_or_unknown]
            if active_tp:
                for idx, c in enumerate(active_tp):
                    stale_cancel_retry = (
                        c.status == CommandStatus.CANCEL_UNKNOWN
                        and c.label in open_by_label
                        and (c.cancel_requested_at_ms is None or (now_ms - c.cancel_requested_at_ms) >= 2_000)
                    )
                    if idx > 0 or (c.status == CommandStatus.OPEN and c.reserved_remaining != short_qty) or stale_cancel_retry:
                        actions.append(
                            PolicyAction(
                                action_type=ActionType.CANCEL_BY_LABEL,
                                target_cmd_id=c.cmd_id,
                                target_label=c.label,
                                reason="TP_QTY_MISMATCH_RESYNC",
                            )
                        )
            elif not has_unknown_tp:
                if plan.limit_price_btc < short_spec.tick_size:
                    tp_substate = plan.substate
                    if plan.alert:
                        alerts.append(plan.alert)
                else:
                    p_cap = plan.limit_price_btc
                    sb = facts.short_book
                    if sb is not None and sb.min_price is not None and p_cap < sb.min_price:
                        tp_substate = "TP_DEFERRED_EXCHANGE_BAND"
                    elif venue_now_ms < (episode.expiry_ms - config.tp_submit_cutoff_ms):
                        if sb is not None and sb.max_price is not None and sb.max_price > ZERO:
                            p_cap = min(p_cap, round_tick_down(sb.max_price, short_spec))
                        p_cap = round_tick_down(p_cap, short_spec)
                        if p_cap >= short_spec.tick_size:
                            tp_substate = (
                                "TP_CLAMPED_TO_MIN_TICK"
                                if plan.substate == "TP_CLAMPED_TO_MIN_TICK"
                                else "TP_ACTIVE"
                            )
                            actions.append(
                                PolicyAction(
                                    action_type=ActionType.SUBMIT,
                                    purpose="TP",
                                    instrument_name=episode.short_instrument,
                                    direction="buy",
                                    amount=short_qty,
                                    limit_price=p_cap,
                                    time_in_force="GTC",
                                    reduce_only=True,
                                    post_only=False,
                                    reject_post_only=False,
                                    reason=tp_substate,
                                )
                            )

        # Branch 3: Unified Protection Residual Disposal (Spec 1.2.4 & 3.2: floor_px >= 0.0002, DTE > 15s)
        can_consider_lx = (
            long_spec is not None
            and (entry_closed or exit_latched)
            and not naked_breach
            and not ep_trades_lagging
            and not es_trades_lagging
            and not close_trades_lagging
            and not short_pos_lagging
            and not lx_trades_lagging
            and (not exit_latched or short_liability_cleared)
            and (short_qty > ZERO or short_liability_cleared)
            and not has_unknown_ep
            and not any(c.is_open_or_unknown for c in ep_cmds)
            and not has_unknown_lx
            and not any(c.is_open_or_unknown for c in lx_cmds)
            and venue_now_ms < (episode.expiry_ms - config.lx_submit_cutoff_ms)
        )
        if can_consider_lx and long_spec is not None:
            protected_floor = reserved_short if short_qty == ZERO else max(reserved_short, matched_qty)
            excess_prot = quant_qty(available_prot - protected_floor, long_spec.min_trade_amount)
            lx_cooldown_ok = (
                episode.last_lx_attempt_ms == 0
                or (now_ms - episode.last_lx_attempt_ms) >= config.lx_retry_interval_ms
            )
            if excess_prot >= long_spec.min_trade_amount and lx_cooldown_ok:
                lb = facts.long_book
                if _book_fresh_ok(lb, now_ms, config.max_book_fetch_age_ms) and lb is not None:
                    floor_px = max(
                        round_tick_up(config.min_residual_bid_btc, long_spec),
                        round_tick_up(lb.min_price or ZERO, long_spec),
                    )
                    band_ok = lb.max_price is None or floor_px <= lb.max_price
                    if band_ok and lb.bid_price is not None and lb.bid_price >= floor_px:
                        actions.append(
                            PolicyAction(
                                action_type=ActionType.SUBMIT,
                                purpose="LX",
                                instrument_name=episode.long_instrument,
                                direction="sell",
                                amount=excess_prot,
                                limit_price=floor_px,
                                time_in_force="IOC",
                                reduce_only=True,
                                post_only=False,
                                reject_post_only=False,
                                reason="UNIFIED_RESIDUAL_RECOVERY",
                            )
                        )
                    else:
                        notes.append("RESIDUAL_HOLD")
                else:
                    notes.append("RESIDUAL_HOLD")

        # Branch 4: Pure Limit IOC Legged Entry (Spec 1.2.1 & 3.1-3.2: silent re-observation inside 60s/20s)
        if entry_sync_lagging and not entry_closed:
            notes.append("WAIT_ENTRY_FILL_SYNC")
        if (
            not entry_closed
            and not entry_halted
            and not exit_latched
            and not any_unknown
            and not entry_cmds_open_or_unknown
            and not entry_sync_lagging
            and short_spec is not None
            and long_spec is not None
        ):
            sb = facts.short_book
            lb = facts.long_book
            sb_fresh = _book_fresh_ok(sb, now_ms, config.max_book_fetch_age_ms)
            lb_fresh = _book_fresh_ok(lb, now_ms, config.max_book_fetch_age_ms)
            paid_ep_outlay = fills["ep_gross"] + fills["ep_fee"]
            prior_unmatched_qty = max(ZERO, ep_filled - es_filled)
            paid_unmatched_outlay = (
                ceil_1e8(paid_ep_outlay * prior_unmatched_qty / ep_filled)
                if (ep_filled > ZERO and prior_unmatched_qty > ZERO)
                else ZERO
            )

            if uncovered_prot >= min_s and need_short >= min_s:
                # Priority 1: ES against confirmed protection coverage (same-tick ready after EP fill!)
                if venue_now_ms < (episode.expiry_ms - config.es_submit_cutoff_ms):
                    if not sb_fresh or sb is None:
                        notes.append("ES_WAIT_BOOK_FRESHNESS")
                    elif sb.bid_price is None or sb.bid_price <= ZERO:
                        notes.append("ES_WAIT_SHORT_BID")
                    else:
                        slice_qty = min(uncovered_prot, need_short, config.max_entry_slice_btc)
                        margin_ok, margin_reason = _entry_capital_and_margin_ok(
                            episode=episode,
                            facts=facts,
                            target_qty_btc=reserved_short + slice_qty,
                            paid_ep_outlay_btc=paid_ep_outlay,
                            paid_unmatched_ep_outlay_btc=paid_unmatched_outlay,
                            unpaid_prot_cost_btc=ZERO,
                            effective_budget_btc=b_eff,
                            config=config,
                        )
                        if not margin_ok:
                            alerts.append(f"ENTRY_GATE_WAIT:{margin_reason}")
                            notes.append(f"ES_WAIT_{margin_reason}")
                        else:
                            p_es_floor = compute_es_floor_price(
                                slice_qty=slice_qty,
                                ep_qty=ep_filled,
                                ep_gross=fills["ep_gross"],
                                ep_fee=fills["ep_fee"],
                                es_qty=es_filled,
                                es_gross=fills["es_gross"],
                                es_fee=fills["es_fee"],
                                min_net_credit_per_btc=episode.min_net_credit_per_btc,
                                short_spec=short_spec,
                            )
                            limit_es = max(p_es_floor, round_tick_up(sb.min_price or ZERO, short_spec))
                            bid_tick = round_tick_down(sb.bid_price, short_spec)
                            max_band_ok = sb.max_price is None or limit_es <= sb.max_price
                            if (
                                bid_tick >= limit_es
                                and max_band_ok
                                and (reserved_short + slice_qty) <= available_prot
                            ):
                                actions.append(
                                    PolicyAction(
                                        action_type=ActionType.SUBMIT,
                                        purpose="ES",
                                        instrument_name=episode.short_instrument,
                                        direction="sell",
                                        amount=slice_qty,
                                        limit_price=limit_es,
                                        time_in_force="IOC",
                                        reduce_only=False,
                                        post_only=False,
                                        reject_post_only=False,
                                        reason="SELL_SHORT_AGAINST_CONFIRMED_PROTECTION",
                                    )
                                )
                            else:
                                notes.append("ES_WAIT_BID_ABOVE_FLOOR")
            elif need_prot >= min_l and venue_now_ms < (episode.expiry_ms - config.min_entry_dte_ms):
                # Priority 2: EP buy protection leg first (dual budget: C_min=0.0008 + U_ep_max=0.0060)
                skew_ok = (
                    sb is not None
                    and lb is not None
                    and abs(sb.fetched_at_ms - lb.fetched_at_ms) <= config.max_leg_fetch_skew_ms
                )
                if not (sb_fresh and lb_fresh and skew_ok) or sb is None or lb is None:
                    notes.append("EP_WAIT_BOOK_FRESHNESS")
                else:
                    slice_qty = min(need_prot, config.max_entry_slice_btc)
                    p_ask_sweep = sweep_ask_price(lb, slice_qty)
                    p_s_bid_sweep = sweep_bid_price(sb, slice_qty)
                    if p_ask_sweep is None or p_ask_sweep <= ZERO or p_s_bid_sweep is None or p_s_bid_sweep <= ZERO:
                        notes.append("EP_WAIT_TWO_SIDED_BOOK")
                    else:
                        rem_prot_qty = max(ZERO, target_qty - ep_filled)
                        unpaid_prot_est = conservative_split_buy_outlay_btc(p_ask_sweep, rem_prot_qty, long_spec)
                        margin_ok, margin_reason = _entry_capital_and_margin_ok(
                            episode=episode,
                            facts=facts,
                            target_qty_btc=target_qty,
                            paid_ep_outlay_btc=paid_ep_outlay,
                            paid_unmatched_ep_outlay_btc=paid_unmatched_outlay,
                            unpaid_prot_cost_btc=unpaid_prot_est,
                            effective_budget_btc=b_eff,
                            config=config,
                        )
                        if not margin_ok:
                            alerts.append(f"ENTRY_GATE_WAIT:{margin_reason}")
                            notes.append(f"EP_WAIT_{margin_reason}")
                        else:
                            p_ep_cap = compute_ep_max_price(
                                slice_qty=slice_qty,
                                short_bid_sweep=p_s_bid_sweep,
                                ep_qty=ep_filled,
                                ep_gross=fills["ep_gross"],
                                ep_fee=fills["ep_fee"],
                                es_qty=es_filled,
                                es_gross=fills["es_gross"],
                                es_fee=fills["es_fee"],
                                min_net_credit_per_btc=episode.min_net_credit_per_btc,
                                max_zero_rec_ep_unit_btc=config.max_zero_rec_ep_unit_btc,
                                effective_budget_btc=b_eff,
                                long_spec=long_spec,
                                short_spec=short_spec,
                            )
                            ask_tick = round_tick_down(lb.ask_price or p_ask_sweep, long_spec)
                            if p_ep_cap >= long_spec.tick_size and ask_tick <= p_ep_cap:
                                raw_lim = min(p_ask_sweep, p_ep_cap)
                                if lb.max_price is not None and lb.max_price > ZERO:
                                    raw_lim = min(raw_lim, lb.max_price)
                                limit_ep = round_tick_down(raw_lim, long_spec)
                                min_ep = max(long_spec.tick_size, lb.min_price or ZERO)
                                if limit_ep >= min_ep:
                                    # Compute non-blocking abort-loss observation (Spec 1.1 & 4.3)
                                    ep_slice_outlay = conservative_split_buy_outlay_btc(limit_ep, slice_qty, long_spec)
                                    unm_after = prior_unmatched_qty + slice_qty
                                    unm_cost_after = paid_unmatched_outlay + ep_slice_outlay
                                    liq_qty = min(unm_after, max(ZERO, lb.bid_amount))
                                    bid_liq_val = (
                                        (lb.bid_price * liq_qty) - option_trade_fee_btc(lb.bid_price, liq_qty, long_spec)
                                        if (lb.bid_price is not None and lb.bid_price > ZERO and liq_qty > ZERO)
                                        else ZERO
                                    )
                                    est_abort_obs = max(ZERO, unm_cost_after - max(ZERO, bid_liq_val)).quantize(SAT)
                                    if estimated_abort_loss_at_ep is None:
                                        estimated_abort_loss_at_ep = est_abort_obs
                                    actions.append(
                                        PolicyAction(
                                            action_type=ActionType.SUBMIT,
                                            purpose="EP",
                                            instrument_name=episode.long_instrument,
                                            direction="buy",
                                            amount=slice_qty,
                                            limit_price=limit_ep,
                                            time_in_force="IOC",
                                            reduce_only=False,
                                            post_only=False,
                                            reject_post_only=False,
                                            reason="BUY_PROTECTION_LEG_FIRST",
                                            estimated_abort_loss_btc=est_abort_obs,
                                        )
                                    )
                                else:
                                    notes.append("EP_WAIT_EXCHANGE_MIN_BAND")
                            else:
                                notes.append("EP_WAIT_PRICE_WITHIN_CAP")

    # 7. Independent Milestones & 6 Lifecycle Phases (Spec 2.1; short_liability_cleared shared above)
    inventory_cleared = (
        short_liability_cleared
        and prot_pos == ZERO
        and not any_sync_lagging
        and stage2_fut_pos == ZERO
        and ext_opt_pos == ZERO
        and ext_fut_pos == ZERO
        and not any(c.purpose in {"EP", "LX"} and c.is_open_or_unknown for c in eff_cmds)
    )

    cashflow_settled = episode.cashflow_settled
    terminal_outcome = episode.terminal_outcome
    if (
        not cashflow_settled
        and inventory_cleared
        and not any_unknown
        and fills["missing_tx_count"] == 0
        and facts.tx_logs_complete
    ):
        rem_short_at_exp = max(ZERO, es_filled - close_short_filled)
        rem_long_at_exp = max(ZERO, ep_filled - lx_filled)
        if entry_closed and ep_filled == ZERO and es_filled == ZERO:
            cashflow_settled = True
            terminal_outcome = "ABORTED_ZERO_FILL"
        elif (ep_filled > ZERO or es_filled > ZERO) and rem_short_at_exp == ZERO and rem_long_at_exp == ZERO:
            cashflow_settled = True
            terminal_outcome = "EARLY_FLAT_SETTLED" if facts.server_time_ms < episode.expiry_ms else "EXPIRY_SETTLED"
        elif facts.server_time_ms >= episode.expiry_ms and fills["expiry_settlement_complete"]:
            cashflow_settled = True
            terminal_outcome = "EXPIRY_SETTLED"

    if cashflow_settled:
        phase = LifecyclePhase.CASHFLOW_SETTLED
    elif inventory_cleared and (entry_closed or exit_latched):
        phase = LifecyclePhase.INVENTORY_CLEARED
    elif short_liability_cleared and (entry_closed or exit_latched) and (es_filled > ZERO or ep_filled > ZERO):
        phase = LifecyclePhase.SHORT_LIABILITY_CLEARED
    elif exit_latched:
        phase = LifecyclePhase.EXIT_LATCHED
    elif entry_closed:
        phase = LifecyclePhase.TP_MANAGED
    else:
        phase = LifecyclePhase.ENTRY

    patch = EpisodePatch(
        phase=phase,
        entry_started_at_ms=entry_started_at_ms,
        last_ep_fill_at_ms=last_ep_fill_at_ms,
        entry_spot_usd=entry_spot_usd,
        estimated_abort_loss_at_ep_btc=estimated_abort_loss_at_ep,
        entry_halted=entry_halted,
        entry_halt_reason=entry_halt_reason,
        entry_closed=entry_closed,
        entry_closed_at_ms=entry_closed_at_ms,
        entry_closure_reason=entry_closure_reason,
        matched_qty_btc=matched_qty,
        matched_short_proceeds_btc=matched_short_proceeds,
        matched_prot_cost_ex_fee_btc=matched_prot_ex_fee,
        matched_entry_fees_btc=matched_entry_fees,
        matched_net_credit_btc=matched_net_credit,
        unmatched_prot_qty_btc=unmatched_prot_qty,
        unmatched_prot_cost_btc=unmatched_prot_cost,
        tp_budget_total_btc=tp_budget_total,
        tp_cap_init_btc=tp_cap_init,
        tp_target_capture_ratio=tp_target_capture_ratio,
        tp_substate=tp_substate,
        exit_latched=exit_latched,
        exit_latch_reason=exit_latch_reason,
        exit_started_at_ms=exit_started_at_ms,
        operator_exit_all=operator_exit_all,
        rx_quote_samples=rx_quote_samples,
        rx_cash_wall_10009=rx_cash_wall_10009,
        short_liability_cleared=short_liability_cleared,
        inventory_cleared=inventory_cleared,
        cashflow_settled=cashflow_settled,
        terminal_outcome=terminal_outcome,
        alerts=_dedup_alerts(alerts),
    )

    return StepResult(
        episode_patch=patch,
        actions=tuple(actions),
        command_resolutions=resolutions,
        alerts=patch.alerts,
        notes=tuple(notes),
    )
