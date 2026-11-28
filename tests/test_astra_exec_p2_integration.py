"""P2 Integration, Smoke & Adversarial Counterexample Suite (live-exec-behavior@1.2.0)."""
from __future__ import annotations

import datetime as dt
import hashlib
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
DEPLOY = ROOT / "deploy" / "xxproject_server"
for p in (str(ROOT), str(ROOT / "tests"), str(TOOLS), str(DEPLOY)):
    if p not in sys.path:
        sys.path.insert(0, p)

import build_release  # noqa: E402
import deploy_release  # noqa: E402
import astra_exec_contracts_v1 as contracts  # noqa: E402
import astra_exec_policy_v1 as policy  # noqa: E402
import astra_exec_store_v1 as store  # noqa: E402
import astra_exec_testnet_verify_v1 as tv_mod  # noqa: E402
import astra_execution_coordinator_v1 as coord_mod  # noqa: E402
import astra_player_tracking_v13 as p5_mod  # noqa: E402
import astra_workbench_v20 as wb_mod  # noqa: E402
import xxproject_server_health_v1 as health_mod  # noqa: E402
from tests import exec_sim_oracle as sim  # noqa: E402

D = contracts.D
ZERO = contracts.ZERO


# ---------------------------------------------------------------------------
# Helpers: SimExchangeAdapter, P5 dossier builder, local HTTP stubs
# ---------------------------------------------------------------------------


def _adapt_spec(ospec: sim.OracleInstrumentSpec | None) -> contracts.InstrumentSpec | None:
    if ospec is None:
        return None
    steps = tuple(
        contracts.TickStep(above_price=D(st.above_price), tick_size=D(st.tick_size))
        for st in ospec.tick_size_steps
    )
    return contracts.InstrumentSpec(
        instrument_name=ospec.instrument_name,
        kind=ospec.kind,
        settlement_period=ospec.settlement_period,
        strike_usd=D(ospec.strike_usd),
        option_type=ospec.option_type,
        expiry_ms=ospec.expiry_ms,
        tick_size=D(ospec.tick_size),
        tick_size_steps=steps,
        min_trade_amount=D(ospec.min_trade_amount),
        maker_commission=D(ospec.maker_commission),
        taker_commission=D(ospec.taker_commission),
        fee_cap_ratio=D(ospec.fee_cap_ratio),
        delivery_fee_rate=D(ospec.delivery_fee_rate),
    )


def _adapt_book(ob: sim.OracleBookQuote | None) -> contracts.BookQuote | None:
    if ob is None:
        return None
    st_map = {
        sim.OracleBookStatus.OK: contracts.BookStatus.OK,
        sim.OracleBookStatus.EMPTY_SIDE: contracts.BookStatus.EMPTY_SIDE,
        sim.OracleBookStatus.DATA_GAP: contracts.BookStatus.DATA_GAP,
    }
    return contracts.BookQuote(
        instrument_name=ob.instrument_name,
        bid_price=D(ob.bid_price) if ob.bid_price is not None else None,
        bid_amount=D(ob.bid_amount),
        ask_price=D(ob.ask_price) if ob.ask_price is not None else None,
        ask_amount=D(ob.ask_amount),
        mark_price=D(ob.mark_price) if ob.mark_price is not None else None,
        min_price=D(ob.min_price) if ob.min_price is not None else None,
        max_price=D(ob.max_price) if ob.max_price is not None else None,
        timestamp_ms=ob.timestamp_ms,
        status=st_map[ob.status],
        gap_reason=ob.gap_reason,
        fetched_at_ms=ob.fetched_at_ms if ob.fetched_at_ms else ob.timestamp_ms,
        bids=tuple((D(p), D(q)) for p, q in ob.bids),
        asks=tuple((D(p), D(q)) for p, q in ob.asks),
    )


def _adapt_order(oo: sim.OracleExchangeOrder | sim.OracleOrder) -> contracts.ExchangeOrder:
    created_ms = getattr(oo, "creation_timestamp_ms", None)
    if created_ms is None:
        created_ms = getattr(oo, "created_at_ms", 0)
    updated_ms = getattr(oo, "last_update_timestamp_ms", None)
    if updated_ms is None:
        updated_ms = getattr(oo, "last_update_ms", 0)
    return contracts.ExchangeOrder(
        order_id=oo.order_id,
        label=oo.label,
        instrument_name=oo.instrument_name,
        direction=oo.direction,
        amount=D(oo.amount),
        filled_amount=D(oo.filled_amount),
        price=D(oo.price) if oo.price is not None else ZERO,
        average_price=D(oo.average_price) if oo.average_price is not None else None,
        order_state=oo.order_state,
        time_in_force=oo.time_in_force,
        reduce_only=oo.reduce_only,
        post_only=oo.post_only,
        creation_timestamp_ms=int(created_ms),
        last_update_timestamp_ms=int(updated_ms),
    )


class SimExchangeAdapter:
    """In-memory adapter wrapping SimulatedDeribitVenue for ExecutionCoordinator."""

    def __init__(self, venue: sim.SimulatedDeribitVenue) -> None:
        self.venue = venue
        self.disable_cod_calls = 0
        self.book_fetch_calls = 0

    def get_server_time_ms(self) -> int:
        return self.venue.get_server_time()

    def disable_cancel_on_disconnect(self) -> None:
        self.disable_cod_calls += 1

    def fetch_facts(
        self,
        short_inst: str,
        long_inst: str,
        *,
        need_tx_logs: bool = True,
    ) -> contracts.ExchangeFacts:
        if short_inst and long_inst:
            self.book_fetch_calls += 1
        try:
            if not short_inst and not long_inst:
                acct = self.venue.get_account_summary()
                t_ms = self.venue.get_server_time()
                pos = tuple(
                    contracts.ExchangePosition(
                        instrument_name=p.instrument_name,
                        kind=p.kind,
                        size=D(p.size),
                        average_price=D(p.average_price) if p.average_price is not None else None,
                        mark_price=D(p.mark_price) if p.mark_price is not None else None,
                        index_price=D(p.index_price) if getattr(p, "index_price", None) is not None else None,
                    )
                    for p in self.venue.get_positions()
                )
                return contracts.ExchangeFacts(
                    asof_ms=t_ms,
                    server_time_ms=t_ms,
                    spot_usd=D(self.venue.spot_usd),
                    equity_btc=D(acct["equity"]),
                    available_funds_btc=D(acct["available_funds"]),
                    initial_margin_btc=D(acct["initial_margin"]),
                    maintenance_margin_btc=D(acct["maintenance_margin"]),
                    positions=pos,
                    open_orders=tuple(_adapt_order(o) for o in self.venue.get_open_orders()),
                    open_orders_complete=True,
                    history_complete=True,
                    recent_orders_by_label={},
                    recent_trades=(),
                    tx_logs=(),
                    tx_logs_complete=True,
                    short_book=None,
                    long_book=None,
                    short_spec=None,
                    long_spec=None,
                    subaccount_id=self.venue.subaccount_id,
                    balance_btc=D(acct["balance"]),
                    portfolio_margining_enabled=bool(acct.get("portfolio_margining_enabled")),
                    margin_model=str(acct.get("margin_model") or ("PM" if acct.get("portfolio_margining_enabled") else "SM")),
                    trades_complete=True,
                    positions_complete=True,
                    clock_rtt_ok=True,
                )
            of = self.venue.build_facts(short_inst, long_inst, page_limit=100, follow_pagination=True)
        except sim.RateLimitError as exc:
            raise coord_mod.VenueTransportError(exc.code, exc.message, retry_after_ms=exc.retry_after_ms) from exc
        except sim.SimulatedNetworkError as exc:
            raise coord_mod.VenueTransportError("network_error", str(exc)) from exc
        except sim.DeribitApiError as exc:
            raise coord_mod.VenueTransportError(exc.code, exc.message) from exc

        return contracts.ExchangeFacts(
            asof_ms=of.asof_ms,
            server_time_ms=of.server_time_ms,
            spot_usd=D(of.spot_usd),
            equity_btc=D(of.equity_btc),
            available_funds_btc=D(of.available_funds_btc),
            initial_margin_btc=D(of.initial_margin_btc),
            maintenance_margin_btc=D(of.maintenance_margin_btc),
            positions=tuple(
                contracts.ExchangePosition(
                    instrument_name=p.instrument_name,
                    kind=p.kind,
                    size=D(p.size),
                    average_price=D(p.average_price) if p.average_price is not None else None,
                    mark_price=D(p.mark_price) if p.mark_price is not None else None,
                    index_price=D(p.index_price) if getattr(p, "index_price", None) is not None else None,
                )
                for p in of.positions
            ),
            open_orders=tuple(_adapt_order(o) for o in of.open_orders),
            open_orders_complete=of.open_orders_complete,
            history_complete=of.history_complete,
            recent_orders_by_label={k: tuple(_adapt_order(o) for o in vs) for k, vs in of.recent_orders_by_label.items()},
            recent_trades=tuple(
                contracts.VenueTrade(
                    trade_id=t.trade_id,
                    order_id=t.order_id,
                    label=t.label,
                    instrument_name=t.instrument_name,
                    direction=t.direction,
                    amount=D(t.amount),
                    price=D(t.price),
                    fee=D(t.fee),
                    fee_currency=t.fee_currency,
                    timestamp_ms=t.timestamp_ms,
                    liquidity=t.liquidity,
                )
                for t in of.recent_trades
            ),
            tx_logs=tuple(
                contracts.TxLogEntry(
                    id=x.id,
                    type=x.type,
                    instrument_name=x.instrument_name,
                    timestamp_ms=x.timestamp_ms,
                    amount=D(x.amount),
                    price=D(x.price) if x.price is not None else None,
                    cashflow=D(x.cashflow),
                    change=D(x.change),
                    commission=D(x.commission),
                    currency=x.currency,
                    trade_id=x.trade_id,
                    order_id=x.order_id,
                    settlement_price=D(x.settlement_price) if x.settlement_price is not None else None,
                    info=dict(x.info),
                )
                for x in of.tx_logs
            )
            if need_tx_logs
            else (),
            tx_logs_complete=of.tx_logs_complete if need_tx_logs else True,
            short_book=_adapt_book(of.short_book) if short_inst else None,
            long_book=_adapt_book(of.long_book) if long_inst else None,
            short_spec=_adapt_spec(of.short_spec) if short_inst else None,
            long_spec=_adapt_spec(of.long_spec) if long_inst else None,
            subaccount_id=of.subaccount_id,
            api_error=of.api_error,
            rate_limited_until_ms=of.rate_limited_until_ms,
            balance_btc=D(of.balance_btc) if of.balance_btc is not None else D(of.equity_btc),
            portfolio_margining_enabled=bool(of.portfolio_margining_enabled),
            margin_model=of.margin_model,
            trades_complete=of.history_complete,
            positions_complete=True,
            clock_rtt_ok=True,
        )

    def submit_order(
        self,
        *,
        instrument_name: str,
        direction: str,
        amount: Decimal,
        limit_price: Decimal,
        label: str,
        time_in_force: str,
        reduce_only: bool,
        post_only: bool,
        reject_post_only: bool,
        valid_until_ms: int | None = None,
    ) -> contracts.ExchangeOrder:
        try:
            oo = self.venue.submit_order(
                instrument_name,
                direction,
                amount,
                limit_price,
                label=label,
                time_in_force=time_in_force,
                reduce_only=reduce_only,
                post_only=post_only,
                reject_post_only=reject_post_only,
                valid_until=valid_until_ms,
            )
            assert oo is not None
            return _adapt_order(oo)
        except sim.RateLimitError as exc:
            raise coord_mod.VenueTransportError(exc.code, exc.message, retry_after_ms=exc.retry_after_ms) from exc
        except sim.SimulatedNetworkError as exc:
            raise coord_mod.VenueTransportError("network_unknown", str(exc)) from exc
        except sim.DeribitApiError as exc:
            raise coord_mod.VenueTransportError(exc.code, exc.message) from exc

    def cancel_by_label(self, label: str) -> int:
        try:
            return self.venue.cancel_by_label(label)
        except sim.RateLimitError as exc:
            raise coord_mod.VenueTransportError(exc.code, exc.message, retry_after_ms=exc.retry_after_ms) from exc
        except sim.SimulatedNetworkError as exc:
            raise coord_mod.VenueTransportError("network_error", str(exc)) from exc
        except sim.DeribitApiError as exc:
            raise coord_mod.VenueTransportError(exc.code, exc.message) from exc

    def get_order_state_by_label(self, label: str) -> tuple[contracts.ExchangeOrder, ...]:
        try:
            return tuple(_adapt_order(o) for o in self.venue.get_order_state_by_label(label))
        except sim.RateLimitError as exc:
            raise coord_mod.VenueTransportError(exc.code, exc.message, retry_after_ms=exc.retry_after_ms) from exc
        except sim.SimulatedNetworkError as exc:
            raise coord_mod.VenueTransportError("network_error", str(exc)) from exc
        except sim.DeribitApiError as exc:
            raise coord_mod.VenueTransportError(exc.code, exc.message) from exc

    def get_order_state(self, order_id: str) -> contracts.ExchangeOrder:
        try:
            return _adapt_order(self.venue.get_order_state(order_id))
        except sim.RateLimitError as exc:
            raise coord_mod.VenueTransportError(exc.code, exc.message, retry_after_ms=exc.retry_after_ms) from exc
        except sim.SimulatedNetworkError as exc:
            raise coord_mod.VenueTransportError("network_error", str(exc)) from exc
        except sim.DeribitApiError as exc:
            raise coord_mod.VenueTransportError(exc.code, exc.message) from exc

    def get_order_history_by_instrument(
        self,
        instrument_name: str,
        *,
        count: int = 200,
        offset: int = 0,
        include_old: bool = True,
        include_unfilled: bool = True,
        historical: bool = False,
    ) -> list[contracts.ExchangeOrder]:
        try:
            return [
                _adapt_order(o)
                for o in self.venue.get_order_history_by_instrument(
                    instrument_name,
                    count=count,
                    offset=offset,
                    include_old=include_old,
                    include_unfilled=include_unfilled,
                    historical=historical,
                )
            ]
        except sim.RateLimitError as exc:
            raise coord_mod.VenueTransportError(exc.code, exc.message, retry_after_ms=exc.retry_after_ms) from exc
        except sim.SimulatedNetworkError as exc:
            raise coord_mod.VenueTransportError("network_error", str(exc)) from exc
        except sim.DeribitApiError as exc:
            raise coord_mod.VenueTransportError(exc.code, exc.message) from exc


def _setup_venue(
    *,
    t0: int = 1_795_450_340_000,
    expiry_ms: int = 1_795_507_200_000,
    settlement_period: str = "day",
    initial_balance: Decimal = D("1.0"),
    short_inst: str = "BTC-25NOV26-62000-P",
    long_inst: str = "BTC-25NOV26-60000-P",
    short_strike: int = 62000,
    long_strike: int = 60000,
    option_type: str = "put",
    portfolio_margining_enabled: bool = True,
) -> sim.SimulatedDeribitVenue:
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
        strike_usd=short_strike,
        option_type=option_type,
        expiry_ms=expiry_ms,
        settlement_period=settlement_period,
        tick_size="0.0001",
        min_trade_amount="0.1",
        tick_size_steps=(("0.005", "0.0005"),),
    )
    venue.add_option_instrument(
        long_inst,
        strike_usd=long_strike,
        option_type=option_type,
        expiry_ms=expiry_ms,
        settlement_period=settlement_period,
        tick_size="0.0001",
        min_trade_amount="0.1",
        tick_size_steps=(("0.005", "0.0005"),),
    )
    venue.set_book(short_inst, bid="0.0044", bid_size="5.0", ask="0.0046", ask_size="5.0", mark_price="0.0045", min_price="0.0001", max_price="0.05")
    venue.set_book(long_inst, bid="0.0011", bid_size="5.0", ask="0.0013", ask_size="5.0", mark_price="0.0012", min_price="0.0001", max_price="0.05")
    return venue


def _refresh_books(venue: sim.SimulatedDeribitVenue, short_inst: str, long_inst: str) -> None:
    for inst in (short_inst, long_inst):
        q = venue.get_order_book(inst, depth=5)
        venue.set_book(
            inst,
            bid=q["best_bid_price"],
            bid_size=q["best_bid_amount"],
            ask=q["best_ask_price"],
            ask_size=q["best_ask_amount"],
            mark_price=q["mark_price"],
            min_price=q["min_price"],
            max_price=q["max_price"],
            match_resting=False,
        )


def _build_p5_dossier(
    root: Path,
    *,
    dossier_id: str = "Deribit_BTC_20261125T080000Z",
    hand_id: str = "hand_a050f60ea6bb7d73acf68a3f",
    ep_id: str = "episode_95ef7494c06605a6b7e59e9c",
    side: str = "put",
    short_inst: str = "BTC-25NOV26-62000-P",
    long_inst: str = "BTC-25NOV26-60000-P",
    short_strike: int = 62000,
    long_strike: int = 60000,
    expiry_ms: int = 1_795_507_200_000,
    decided_at_ms: int = 1_795_450_325_000,
    credit_btc: Decimal | None = D("0.0028"),
    link_state: str = "SHADOW_LOCK_CREATED",
    decision: str = "Accept",
) -> Path:
    d_dir = root / "dossiers" / dossier_id
    hand_dir = d_dir / "hands" / hand_id
    trk_dir = hand_dir / "tracking"
    ev_src_dir = hand_dir / "evidence"
    ep_dir = d_dir / "episodes" / ep_id
    ev_dir = ep_dir / "events"
    for p in (trk_dir, ev_src_dir, ev_dir):
        p.mkdir(parents=True, exist_ok=True)

    raw_txt = '{"decision": "' + decision + '"}'
    raw_sha = hashlib.sha256(raw_txt.encode("utf-8")).hexdigest()
    (hand_dir / "raw.txt").write_text(raw_txt, encoding="utf-8")

    dec_obj = {
        "attempt_id": "dec_attempt_001",
        "decision_id": "dec_attempt_001",
        "decided_at_ms": decided_at_ms,
        "decision": decision,
        "chosen_side": side,
        "judgment": {"action": decision, "side": side},
        "raw_sha256": raw_sha,
    }
    dec_bytes = json.dumps(dec_obj, sort_keys=True).encode("utf-8")
    (hand_dir / "decision.json").write_bytes(dec_bytes)
    dec_sha = hashlib.sha256(dec_bytes).hexdigest()

    cond_obj = {
        "hand_id": hand_id,
        "episode_id": ep_id,
        "contract": {
            "side": side,
            "short_instrument": short_inst,
            "long_instrument": long_inst,
            "short_strike_usd": short_strike,
            "long_strike_usd": long_strike,
            "expiry_ms": expiry_ms,
            "quantity_btc": 1.0,
        },
        "conditions": [],
    }
    cond_bytes = json.dumps(cond_obj, sort_keys=True).encode("utf-8")
    (ep_dir / "condition.json").write_bytes(cond_bytes)
    cond_sha = hashlib.sha256(cond_bytes).hexdigest()

    in_bytes = json.dumps({"dossier_id": dossier_id, "spot": 62785}, sort_keys=True).encode("utf-8")
    (hand_dir / "input.json").write_bytes(in_bytes)
    in_sha = hashlib.sha256(in_bytes).hexdigest()

    (hand_dir / "expected.json").write_text(json.dumps({"expiry_ms": expiry_ms}), encoding="utf-8")
    (ep_dir / "episode.json").write_text(
        json.dumps(
            {
                "hand_id": hand_id,
                "episode_id": ep_id,
                "dossier_id": dossier_id,
                "decision": decision,
                "chosen_side": side,
                "expiry_ms": expiry_ms,
                "t0_recorded_at_ms": decided_at_ms,
            }
        ),
        encoding="utf-8",
    )

    link_obj: dict[str, Any] = {
        "state": link_state,
        "recorded_at_ms": decided_at_ms + 5_000,
        "execution_allowed": False,
        "origin": {
            "dossier_id": dossier_id,
            "hand_id": hand_id,
            "decision_id": "dec_attempt_001",
            "dossier_episode_id": ep_id,
            "decision_sha256": dec_sha,
            "condition_sha256": cond_sha,
            "input_sha256": in_sha,
        },
    }
    if credit_btc is not None:
        link_obj["t0"] = {"economy": {"net_credit_after_entry_fee_btc": float(credit_btc)}}
    (trk_dir / "link.json").write_text(json.dumps(link_obj), encoding="utf-8")

    # Source summary (used if credit_btc provided, omitted if testing missing c_decision_ref)
    if credit_btc is not None:
        (ev_src_dir / "source.json").write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "contract": {"side": side, "quantity_btc": 1.0},
                            "economics": {"net_credit_after_entry_fee_btc": float(credit_btc)},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
    return hand_dir


def _write_p5_event(
    dossier_root: Path,
    *,
    kind: str,
    dossier_id: str = "Deribit_BTC_20261125T080000Z",
    ep_id: str = "episode_95ef7494c06605a6b7e59e9c",
    ts_ms: int,
    review_id: str = "rev_001",
    shadow_action: str = "REVOKE_ALIGNED",
    protocol_status: str = "REVOKE_VERIFIED",
) -> Path:
    ev_dir = dossier_root / "dossiers" / dossier_id / "episodes" / ep_id / "events"
    ev_dir.mkdir(parents=True, exist_ok=True)
    fname = f"ev_{ts_ms}_{kind.lower()}.json"
    body: dict[str, Any] = {}
    if kind == "AI_REVOKE":
        body = {
            "review_id": review_id,
            "shadow_action": shadow_action,
            "protocol_status": protocol_status,
            "trigger_reason": "structure_invalidated",
        }
    p = ev_dir / fname
    p.write_text(
        json.dumps(
            {
                "kind": kind,
                "recorded_at_ms": ts_ms,
                "decided_at_ms": ts_ms,
                "observed_at_ms": ts_ms,
                "body": body,
            }
        ),
        encoding="utf-8",
    )
    return p


def _new_coord(
    tmp_path: Path,
    venue: sim.SimulatedDeribitVenue,
    *,
    mode: str = "TESTNET",
    entry_armed: bool = True,
    budget_cap_btc: str = "0.20",
    max_qty_btc: str = "0.1",
    max_entry_slice_btc: str = "0.1",
    entry_window_ms: int = 60_000,
    min_net_credit_per_btc: str = "0.0008",
) -> tuple[store.ExecutionStore, SimExchangeAdapter, coord_mod.ExecutionCoordinator, Path]:
    db_path = tmp_path / "exec.sqlite3"
    st_path = tmp_path / "public_status.json"
    dos_root = tmp_path / "p5_root"
    dos_root.mkdir(parents=True, exist_ok=True)
    st = store.ExecutionStore(db_path)
    adp = SimExchangeAdapter(venue)
    cfg = contracts.ExecConfig(
        initial_qty_btc=D(max_qty_btc),
        max_entry_slice_btc=D(max_entry_slice_btc),
        entry_window_ms=entry_window_ms,
        min_net_credit_per_btc=D(min_net_credit_per_btc),
    )
    coord = coord_mod.ExecutionCoordinator(
        mode=mode,
        env_tag="tn",
        subaccount_id="sub-tn-1",
        store=st,
        adapter=adp,
        status_path=st_path,
        dossier_root=dos_root,
        config=cfg,
    )
    coord.startup_init(now_ms=venue.server_time_ms)
    if entry_armed:
        coord.handle_capital_policy(
            {"entry_armed": True, "budget_cap_btc": budget_cap_btc, "max_qty_btc": max_qty_btc, "operator": "cfg"},
            now_ms=venue.server_time_ms,
            run_tick_after=False,
        )
    return st, adp, coord, dos_root


def _all_episodes(st: store.ExecutionStore) -> list[contracts.EpisodeState]:
    rows = st.conn.execute("SELECT episode_id FROM episodes ORDER BY created_at_ms ASC").fetchall()
    return [ep for r in rows if (ep := st.get_episode(str(r[0]))) is not None]


def _drive(coord: coord_mod.ExecutionCoordinator, venue: sim.SimulatedDeribitVenue, short_inst: str, long_inst: str, n: int = 1, dt_ms: int = 100) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for _ in range(n):
        _refresh_books(venue, short_inst, long_inst)
        res = coord.run_tick(now_ms=venue.server_time_ms)
        venue.advance_time(dt_ms)
    return res


def _raw_http(url: str, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    hdrs = dict(headers or {})
    if payload is not None:
        hdrs.setdefault("Content-Type", "application/json")
    req = Request(f"{url}{path}", data=body, method=method, headers=hdrs)
    try:
        with urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# E2E 1 - 6: Full Coordinator + Simulator + IndependentSettlementOracle
# ---------------------------------------------------------------------------


def test_e2e_1_full_entry_tp_and_lx_early_flat(tmp_path: Path):
    venue = _setup_venue()
    st, _adp, coord, dos_root = _new_coord(tmp_path, venue, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        _build_p5_dossier(dos_root, decided_at_ms=venue.server_time_ms - 5_000)
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=50)
        ep = st.get_latest_episode()
        assert ep is not None
        assert ep.phase == contracts.LifecyclePhase.TP_MANAGED
        assert ep.matched_qty_btc == D("0.1")

        tp_cmd = next(c for c in st.get_commands(ep.episode_id) if c.purpose == "TP")
        venue.fill_resting_order(tp_cmd.label, D("0.1"), price=D("0.0002"), liquidity="M")
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=500)

        ep_done = st.get_episode(ep.episode_id)
        assert ep_done is not None and ep_done.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
        assert not venue.get_positions()
        oracle = sim.IndependentSettlementOracle(initial_balance_btc=D("1.0"), trades=venue.export_raw_trades())
        assert oracle.is_flat is True
        assert D(st.rebuild_episode_ledger(ep.episode_id)["accounting"]["episode_total_realized_pnl_btc"]) == oracle.expected_episode_realized_pnl_btc
        assert D(venue.get_account_summary()["equity"]) == oracle.expected_cash_balance_btc
    finally:
        st.close()


def test_e2e_2_full_entry_ai_revoke_rx_and_lx(tmp_path: Path):
    venue = _setup_venue()
    st, _adp, coord, dos_root = _new_coord(tmp_path, venue, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        _build_p5_dossier(dos_root, decided_at_ms=venue.server_time_ms - 5_000)
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=50)
        _write_p5_event(dos_root, kind="AI_REVOKE", ts_ms=venue.server_time_ms)
        venue.set_book("BTC-25NOV26-62000-P", bid="0.0080", bid_size="5.0", ask="0.0085", ask_size="5.0", mark_price="0.0083", max_price="0.05")
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=6, dt_ms=500)

        ep_done = st.get_latest_episode()
        assert ep_done is not None and ep_done.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
        assert ep_done.exit_latch_reason == "AI_REVOKE"
        assert not venue.get_positions()
        oracle = sim.IndependentSettlementOracle(initial_balance_btc=D("1.0"), trades=venue.export_raw_trades())
        assert D(venue.get_account_summary()["equity"]) == oracle.expected_cash_balance_btc
    finally:
        st.close()


def test_e2e_3_partial_shrunk_entry_tp_and_excess_lx(tmp_path: Path):
    venue = _setup_venue()
    st, adp, coord, dos_root = _new_coord(
        tmp_path, venue, budget_cap_btc="0.50", max_qty_btc="0.5", max_entry_slice_btc="0.5", entry_window_ms=25_000
    )
    try:
        _build_p5_dossier(dos_root, decided_at_ms=venue.server_time_ms - 5_000)
        venue.inject_partial_fill_cap("BTC-25NOV26-62000-P", D("0.3"))
        orig_sub = adp.submit_order
        dropped = {"done": False}

        def _sub_drop_bid(**kw: Any) -> contracts.ExchangeOrder:
            out = orig_sub(**kw)
            if ":ES:" in kw.get("label", "") and not dropped["done"]:
                dropped["done"] = True
                venue.inject_partial_fill_cap("BTC-25NOV26-62000-P", None)
                venue.set_book("BTC-25NOV26-62000-P", bid=None, bid_size="0", ask="0.0020", ask_size="5.0", mark_price="0.0010")
            return out

        adp.submit_order = _sub_drop_bid  # type: ignore[method-assign]
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=1, dt_ms=50)  # EP 0.5 -> ES 0.3 -> bid drops!
        venue.advance_time(21_000)
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=5, dt_ms=100)
        ep = st.get_latest_episode()
        assert ep is not None and ep.entry_closed is True
        assert ep.matched_qty_btc == D("0.3")
        assert ep.unmatched_prot_qty_btc == D("0.2")
        assert any(c.purpose == "LX" and c.filled_amount == D("0.2") for c in st.get_commands(ep.episode_id))

        tp_cmd = next(c for c in st.get_commands(ep.episode_id) if c.purpose == "TP" and c.status == contracts.CommandStatus.OPEN)
        venue.fill_resting_order(tp_cmd.label, D("0.3"), price=D("0.0002"), liquidity="M")
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=6, dt_ms=1_000)
        ep_end = st.get_episode(ep.episode_id)
        assert ep_end is not None and ep_end.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
        oracle = sim.IndependentSettlementOracle(initial_balance_btc=D("1.0"), trades=venue.export_raw_trades())
        assert D(st.rebuild_episode_ledger(ep.episode_id)["accounting"]["episode_total_realized_pnl_btc"]) == oracle.expected_episode_realized_pnl_btc
    finally:
        st.close()


def test_e2e_4_mid_entry_ai_revoke_rx_before_lx(tmp_path: Path):
    venue = _setup_venue()
    st, _adp, coord, dos_root = _new_coord(tmp_path, venue, budget_cap_btc="0.50", max_qty_btc="0.4", max_entry_slice_btc="0.1")
    try:
        _build_p5_dossier(dos_root, decided_at_ms=venue.server_time_ms - 5_000)
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=2, dt_ms=20)
        _write_p5_event(dos_root, kind="AI_REVOKE", ts_ms=venue.server_time_ms)
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=8, dt_ms=100)

        ep = st.get_latest_episode()
        assert ep is not None and ep.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
        cmds = st.get_commands(ep.episode_id)
        first_rx = min(c.created_at_ms for c in cmds if c.purpose == "RX")
        first_lx = min(c.created_at_ms for c in cmds if c.purpose == "LX")
        assert first_rx <= first_lx
    finally:
        st.close()


@pytest.mark.parametrize(
    "settlement_period,settle_usd,expected_outcome",
    [
        ("day", "61000", "SETTLED_EXPIRY_ITM"),
        ("week", "61000", "SETTLED_EXPIRY_ITM"),
        ("day", "65000", "SETTLED_EXPIRY_OTM"),
    ],
)
def test_e2e_5_expiry_two_stage_itm_and_otm(tmp_path: Path, settlement_period: str, settle_usd: str, expected_outcome: str):
    venue = _setup_venue(settlement_period=settlement_period)
    st, _adp, coord, dos_root = _new_coord(tmp_path, venue, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        _build_p5_dossier(dos_root, decided_at_ms=venue.server_time_ms - 5_000)
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=50)
        ep = st.get_latest_episode()
        assert ep is not None

        venue.set_server_time(ep.expiry_ms + 1_000)
        venue.inject_delayed_tx_log(True)
        venue.expire_at_settlement(D(settle_usd))
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=2, dt_ms=100)
        ep_wait = st.get_episode(ep.episode_id)
        assert ep_wait is not None and ep_wait.phase == contracts.LifecyclePhase.INVENTORY_CLEARED

        venue.settle_delayed_tx_log(flush_pending=True)
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=2, dt_ms=100)
        ep_done = st.get_episode(ep.episode_id)
        assert ep_done is not None and ep_done.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
        assert ep_done.terminal_outcome == "EXPIRY_SETTLED"
        oracle = sim.IndependentSettlementOracle(
            initial_balance_btc=D("1.0"),
            trades=venue.export_raw_trades(),
            settlement_price_usd=D(settle_usd),
            settlement_period=settlement_period,
        )
        assert D(st.rebuild_episode_ledger(ep.episode_id)["accounting"]["episode_total_realized_pnl_btc"]) == oracle.expected_episode_realized_pnl_btc
        assert D(venue.get_account_summary()["equity"]) == oracle.expected_cash_balance_btc
    finally:
        st.close()


def test_e2e_6_crash_restart_backoff_m1_and_stale_status(tmp_path: Path):
    venue = _setup_venue()
    st1, _adp1, coord1, dos_root = _new_coord(tmp_path, venue, budget_cap_btc="0.20", max_qty_btc="0.1")
    db_path = tmp_path / "exec.sqlite3"
    st_path = tmp_path / "public_status.json"
    _build_p5_dossier(dos_root, decided_at_ms=venue.server_time_ms - 5_000)
    _drive(coord1, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=1, dt_ms=10)
    # Drop submit ack on ES, leaving SUBMIT_UNKNOWN, then close coordinator 1 (simulate crash)
    venue.inject_drop_next_submit_ack(venue_received=True)
    _drive(coord1, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=1, dt_ms=10)
    st1.close()

    # Restart coordinator 2 against same SQLite DB & public_status.json
    st2 = store.ExecutionStore(db_path)
    try:
        adp2 = SimExchangeAdapter(venue)
        coord2 = coord_mod.ExecutionCoordinator(
            mode="TESTNET",
            env_tag="tn",
            subaccount_id="sub-tn-1",
            store=st2,
            adapter=adp2,
            status_path=st_path,
            dossier_root=dos_root,
            config=contracts.ExecConfig(initial_qty_btc=D("0.1"), max_entry_slice_btc=D("0.1")),
        )
        coord2.startup_init(now_ms=venue.server_time_ms)

        # Rate limit backoff step test (Spec 5.1 exponential schedule 2/4/8/15s)
        venue.inject_10028(1, retry_after_ms=1500)
        res_rl = coord2.run_tick(now_ms=venue.server_time_ms)
        assert res_rl.get("venue_connected") is False
        assert "10028" in str(res_rl.get("last_error") or "")
        ep_cur = st2.get_latest_episode()
        assert ep_cur is not None and coord2.recommended_poll_ms(ep_cur, st2.get_commands(ep_cur.episode_id)) >= 2_000
        venue.advance_time(5_000)

        _drive(coord2, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=3, dt_ms=50)
        ep = st2.get_latest_episode()
        assert ep is not None and ep.phase == contracts.LifecyclePhase.TP_MANAGED
        assert adp2.disable_cod_calls >= 1

        # Public status schema contains READ_ONLY contract fields
        pub = json.loads(st_path.read_text(encoding="utf-8"))
        assert pub["cockpit_mode"] == "READ_ONLY"
        assert pub["capital_policy"]["entry_armed"] is True
        assert pub["active_episode"]["tp"]["active_limit_price_btc"] is not None
    finally:
        st2.close()


# ---------------------------------------------------------------------------
# Security, Pure Read-Only Workbench, OS Isolation & SQLite Durability
# ---------------------------------------------------------------------------


def test_security_token_isolation_readonly_workbench_peercred_and_durability(tmp_path: Path):
    venue = _setup_venue()
    st, _adp, coord, _dos_root = _new_coord(tmp_path, venue, entry_armed=False)
    st_path = tmp_path / "public_status.json"
    srv, _bind = coord_mod.start_control_server(coord, expected_token="ctrl_secret_tok", port=0)
    ctrl_url = f"http://127.0.0.1:{srv.server_port}"

    clock_box = [venue.server_time_ms]
    wb_dir = tmp_path / "wb_state"
    wb_dir.mkdir(parents=True, exist_ok=True)
    snap_path = tmp_path / "snapshots.jsonl"
    snap_path.write_text("", encoding="utf-8")
    wb = wb_mod.AstraWorkbench(
        wb_mod.WorkbenchConfig(
            snapshots_jsonl=snap_path,
            state_dir=wb_dir,
            server_token="wb_srv_tok",
            exec_status_path=st_path,
            exec_stale_after_ms=10_000,
        ),
        clock_ms=lambda: clock_box[0],
    )
    wb_srv = wb_mod.ThreadingHTTPServer(("127.0.0.1", 0), wb_mod.make_handler(wb))
    threading.Thread(target=wb_srv.serve_forever, daemon=True).start()
    wb_url = f"http://127.0.0.1:{wb_srv.server_port}"

    try:
        # 1. Coordinator auth & routes: valid token works; invalid gets 401; deleted legacy routes return 404
        c401, p401 = _raw_http(ctrl_url, "/capital-policy", method="POST", payload={"entry_armed": True}, headers={"Authorization": "Bearer wrong"})
        assert c401 == 401 and p401["code"] == "invalid_exec_control_token"

        c404_a, _ = _raw_http(ctrl_url, "/approve", method="POST", payload={}, headers={"Authorization": "Bearer ctrl_secret_tok"})
        c404_h, _ = _raw_http(ctrl_url, "/halt-entry", method="POST", payload={}, headers={"Authorization": "Bearer ctrl_secret_tok"})
        assert c404_a == 404 and c404_h == 404

        c200_cp, p200_cp = _raw_http(
            ctrl_url,
            "/capital-policy",
            method="POST",
            payload={"entry_armed": True, "budget_cap_btc": "0.15", "max_qty_btc": "0.1", "operator": "cli_admin", "note": "enable"},
            headers={"Authorization": "Bearer ctrl_secret_tok"},
        )
        assert c200_cp == 200 and p200_cp["entry_armed"] is True and p200_cp["status"]["capital_policy"]["entry_armed"] is True

        # SQLite durability + immutable capital_auth_audit across DB reopen
        sync_val = st.conn.execute("PRAGMA synchronous").fetchone()[0]
        assert int(sync_val) == 2  # FULL
        audit_rows = st.get_capital_auth_audits()
        assert len(audit_rows) >= 1 and audit_rows[-1]["after"]["budget_cap_btc"] == "0.15"

        # 2. Workbench GET live succeeds & is strictly READ_ONLY; ALL execution POSTs return 403
        g_code, g_body = _raw_http(wb_url, "/api/workbench/execution/live", headers={"Authorization": "Bearer wb_srv_tok"})
        assert g_code == 200
        assert g_body["cockpit_mode"] == "READ_ONLY"
        assert "capital_policy" in g_body
        assert "lifecycle_view" in g_body
        assert "pending_approval_candidates" not in g_body

        # Staleness enforcement on server side (> status_stale_age_ms=45_000ms)
        clock_box[0] = int(g_body["generated_at_ms"]) + 60_000
        s_code, s_body = _raw_http(wb_url, "/api/workbench/execution/live", headers={"Authorization": "Bearer wb_srv_tok"})
        assert s_code == 200 and s_body["stale"] is True and s_body["health"] == "STALE"

        for post_ep in (
            "/api/workbench/execution/capital-policy",
            "/api/workbench/execution/exit-all",
            "/api/workbench/execution/disable-writes",
            "/api/workbench/execution/approve",
            "/api/workbench/execution/halt-entry",
            "/api/workbench/execution/session",
        ):
            r_unauth, b_unauth = _raw_http(wb_url, post_ep, method="POST", payload={})
            r_auth, b_auth = _raw_http(
                wb_url,
                post_ep,
                method="POST",
                payload={"entry_armed": False},
                headers={"Authorization": "Bearer wb_srv_tok", "Origin": wb_url},
            )
            assert r_unauth == 403 and b_unauth["code"] == "execution_control_disabled_readonly_cockpit"
            assert r_auth == 403 and b_auth["code"] == "execution_control_disabled_readonly_cockpit"

        # 3. Coordinator /disable-writes latches write cutoff cleanly
        c_dw, p_dw = _raw_http(
            ctrl_url,
            "/disable-writes",
            method="POST",
            payload={"reason": "emergency_test", "operator": "root"},
            headers={"Authorization": "Bearer ctrl_secret_tok"},
        )
        assert c_dw == 200 and p_dw["ok"] is True and p_dw["status"]["writes_enabled"] is False
        assert st.writes_disabled()[0] is True
    finally:
        wb_srv.shutdown()
        wb_srv.server_close()
        srv.shutdown()
        srv.server_close()
        st.close()


def test_os_isolation_no_workbench_exec_access(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pref = tmp_path / "srv"
    sig_dir = pref / "etc" / "signal-audit"
    sig_dir.mkdir(parents=True, exist_ok=True)
    (sig_dir / "workbench.token").write_text("wb_tok\n", encoding="utf-8")
    (sig_dir / "exec-control.token").write_text("ctrl_tok\n", encoding="utf-8")

    # Fake pwd/grp mapping enforcing {root, astra-exec} only; astra-workbench and bitnami forbidden from astra-exec group
    fake_users = {
        "root": type("P", (), {"pw_uid": 0, "pw_gid": 0})(),
        "astra-exec": type("P", (), {"pw_uid": 2001, "pw_gid": 2001})(),
        "astra-workbench": type("P", (), {"pw_uid": 2002, "pw_gid": 2002})(),
        "bitnami": type("P", (), {"pw_uid": 1000, "pw_gid": 1000})(),
    }

    class _FakeGrp:
        def __init__(self, mem: list[str] | None = None) -> None:
            self.gr_mem = mem or []
            self.gr_gid = 2001

    class _FakePwdMod:
        @staticmethod
        def getpwnam(name: str) -> Any:
            if name not in fake_users:
                raise KeyError(name)
            return fake_users[name]

    class _FakeGrpMod:
        members: list[str] = []

        @classmethod
        def getgrnam(cls, name: str) -> Any:
            return _FakeGrp(cls.members)

    monkeypatch.setitem(sys.modules, "pwd", _FakePwdMod)
    monkeypatch.setitem(sys.modules, "grp", _FakeGrpMod)
    deploy_release._validate_execution_os_isolation(pref)  # Succeeds cleanly without exec-operator.token!

    # Adding astra-workbench into astra-exec group must fail closed!
    _FakeGrpMod.members = ["astra-workbench"]
    with pytest.raises(RuntimeError, match="UNSAFE_GROUP_MEMBERSHIP_IN_ASTRA_EXEC"):
        deploy_release._validate_execution_os_isolation(pref)

    # _default_allowed_peer_uids accepts only {0, uid(astra-exec)} and excludes bitnami / astra-workbench
    monkeypatch.setattr(coord_mod.os, "getuid", lambda: 2001, raising=False)
    allowed = coord_mod._default_allowed_peer_uids()
    assert allowed == {0, 2001}
    assert 2002 not in allowed and 1000 not in allowed


# ---------------------------------------------------------------------------
# New Task Doc Sec 4 / 5 Scenarios + P5 CLI + DeribitRestAdapter Pagination/KeepAlive
# ---------------------------------------------------------------------------


def test_p5_missing_c_decision_ref_and_low_retention_still_executes(tmp_path: Path):
    venue = _setup_venue()
    st, _adp, coord, dos_root = _new_coord(tmp_path, venue, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        # Omit credit_btc completely AND drop short bid slightly so credit < 80% of any typical reference
        _build_p5_dossier(dos_root, decided_at_ms=venue.server_time_ms - 5_000, credit_btc=None)
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=50)
        ep = st.get_latest_episode()
        assert ep is not None and ep.phase == contracts.LifecyclePhase.TP_MANAGED
        assert ep.c_decision_ref_btc is None

        ledger = st.rebuild_episode_ledger(ep.episode_id)
        obs = ledger["observations"]
        assert obs["c_decision_ref_status"] == "unavailable"
        assert obs["credit_retention_ratio"] is None
        assert "ENTRY_SLIPPAGE_HIGH" not in ep.alerts
    finally:
        st.close()


def test_failed_zero_fill_and_es_short_drought_long_only_abort(tmp_path: Path):
    venue = _setup_venue()
    st, _adp, coord, dos_root = _new_coord(tmp_path, venue, budget_cap_btc="0.20", max_qty_btc="0.1", entry_window_ms=60_000)
    try:
        # Part A: EP ask > 0.0060 across entire 60s window -> ABORTED_ZERO_FILL cleanly
        venue.set_book("BTC-25NOV26-62000-P", bid="0.0080", bid_size="5.0", ask="0.0085", ask_size="5.0", mark_price="0.0082")
        venue.set_book("BTC-25NOV26-60000-P", bid="0.0062", bid_size="5.0", ask="0.0066", ask_size="5.0", mark_price="0.0064")
        _build_p5_dossier(dos_root, hand_id="hand_zero_001", ep_id="episode_zero_001", decided_at_ms=venue.server_time_ms - 5_000)
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=2, dt_ms=100)
        venue.advance_time(65_000)
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=2, dt_ms=100)
        ep_z = next(e for e in _all_episodes(st) if e.hand_id == "hand_zero_001")
        assert ep_z.terminal_outcome == "ABORTED_ZERO_FILL"
        assert not [c for c in st.get_commands(ep_z.episode_id) if c.purpose == "EP"]

        # Part B: ES 20s short drought after EP fills -> LONG_ONLY_ABORT -> LX recovers protection
        venue.set_book("BTC-25NOV26-60000-P", bid="0.0011", bid_size="5.0", ask="0.0013", ask_size="5.0", mark_price="0.0012")
        venue.set_book("BTC-25NOV26-62000-P", bid="0.0044", bid_size="5.0", ask="0.0046", ask_size="5.0", mark_price="0.0045")
        venue.inject_partial_fill_cap("BTC-25NOV26-62000-P", ZERO)  # EP fills, ES gets 0
        _build_p5_dossier(dos_root, hand_id="hand_drought_002", ep_id="episode_drought_002", decided_at_ms=venue.server_time_ms - 5_000)
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=2, dt_ms=20)
        venue.inject_partial_fill_cap("BTC-25NOV26-62000-P", None)
        venue.set_book("BTC-25NOV26-62000-P", bid="0.0004", bid_size="5.0", ask="0.0020", ask_size="5.0", mark_price="0.0010")
        venue.advance_time(21_000)
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=100)
        ep_d = next(e for e in _all_episodes(st) if e.hand_id == "hand_drought_002")
        assert ep_d.entry_closed is True
        assert "LONG_ONLY_ABORT:ES_RESCUE_WINDOW_EXPIRED" in (ep_d.entry_closure_reason or "")
        assert any(c.purpose == "LX" and c.filled_amount == D("0.1") for c in st.get_commands(ep_d.episode_id))
        assert ep_d.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
    finally:
        st.close()


def test_pause_entry_armed_false_halts_new_opens_while_active_completes(tmp_path: Path):
    venue = _setup_venue()
    st, adp, coord, dos_root = _new_coord(tmp_path, venue, budget_cap_btc="0.50", max_qty_btc="0.2", max_entry_slice_btc="0.1")
    try:
        # Simulate concurrent operator /capital-policy pause right after slice 1 ES completes (0.1 matched, 0.1 remaining)!
        orig_submit = adp.submit_order
        paused_once = {"done": False}

        def _submit_with_mid_pause(**kw: Any) -> contracts.ExchangeOrder:
            out = orig_submit(**kw)
            if ":ES:" in kw.get("label", "") and not paused_once["done"]:
                paused_once["done"] = True
                coord.handle_capital_policy(
                    {"entry_armed": False, "operator": "op"}, now_ms=venue.server_time_ms, run_tick_after=False
                )
            return out

        adp.submit_order = _submit_with_mid_pause  # type: ignore[method-assign]
        _build_p5_dossier(dos_root, hand_id="hand_pause_001", ep_id="episode_pause_001", decided_at_ms=venue.server_time_ms - 5_000)
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=2, dt_ms=10)

        ep1 = next(e for e in _all_episodes(st) if e.hand_id == "hand_pause_001")
        assert coord.store.get_capital_auth().entry_armed is False
        assert ep1.entry_halted is True and ep1.entry_halt_reason == "OPERATOR_PAUSE_ENTRY"
        assert ep1.exit_latched is False  # NEVER forces exit on active position!
        assert ep1.matched_qty_btc == D("0.1")
        assert ep1.phase == contracts.LifecyclePhase.TP_MANAGED

        # Fresh Accept hand while entry_armed=False must NOT admit!
        _build_p5_dossier(dos_root, hand_id="hand_pause_002", ep_id="episode_pause_002", decided_at_ms=venue.server_time_ms - 2_000)
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=2, dt_ms=50)
        assert len(_all_episodes(st)) == 1

        # Existing active position still closes via resting TP cleanly!
        tp_cmd = next(c for c in st.get_commands(ep1.episode_id) if c.purpose == "TP" and c.status == contracts.CommandStatus.OPEN)
        venue.fill_resting_order(tp_cmd.label, D("0.1"), price=D("0.0002"), liquidity="M")
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=3, dt_ms=100)
        ep1_done = st.get_episode(ep1.episode_id)
        assert ep1_done is not None and ep1_done.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
    finally:
        st.close()


def test_p5_cli_sync_open_reads_public_status_and_bypasses_exhausted_shadow_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tests import test_astra_player_tracking_v13 as pt13_test

    # 1. Real Workbench + P5 Dossiers hand linked through real tracker service
    wb, d, hand, now, expiry = pt13_test.sample(tmp_path, action="Accept / Call")
    shadow_ledger = tmp_path / "shadow_usage.json"
    sh_budget = p5_mod.signal_llm_review.DailyHttpBudget(shadow_ledger, limit=15)
    for _ in range(15):
        sh_budget.reserve(1, role="shadow_exhaust_test")  # EXHAUSTED 15/day shadow budget!
    live_ledger = tmp_path / "astra_player_live_hold_review.json"

    class _LlmHandler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            _ = self.rfile.read(length)
            reply = (
                "## REVOKE\n\nTrigger breached.\n"
                '<HOLD_REVIEW_JSON>{"verdict":"REVOKE","affected_conditions":["M1"],"public_reply":"Live revoke","key_facts":["M1"]}</HOLD_REVIEW_JSON>'
            )
            body = json.dumps(
                {"id": "chatcmpl_test", "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": reply}}]}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    llm_srv = ThreadingHTTPServer(("127.0.0.1", 0), _LlmHandler)
    threading.Thread(target=llm_srv.serve_forever, daemon=True).start()

    st_path = tmp_path / "public_status.json"
    st = store.ExecutionStore(tmp_path / "exec.sqlite3")
    try:
        with pt13_test.serving(wb) as wb_url:
            svc = pt13_test.post(wb_url, wb)
            receipt = p5_mod.link(d, hand, svc, pt13_test.RULE)
            ep_dir = hand.parent.parent / "episodes" / receipt["origin"]["dossier_episode_id"]
            cond = json.loads((ep_dir / "condition.json").read_text(encoding="utf-8"))
            cinfo = cond["contract"]
            short_inst = str(cinfo["short_instrument"])
            long_inst = str(cinfo["long_instrument"])

            venue = _setup_venue(
                t0=now[0] + 500,
                expiry_ms=int(cinfo["expiry_ms"]),
                short_inst=short_inst,
                long_inst=long_inst,
                short_strike=int(cinfo["short_strike_usd"]),
                long_strike=int(cinfo["long_strike_usd"]),
                option_type=str(cinfo["side"]).lower(),
            )
            venue.set_spot_usd(str(wb._current_view.get("spot_usd") or 100_000))
            adp = SimExchangeAdapter(venue)
            coord = coord_mod.ExecutionCoordinator(
                mode="TESTNET",
                env_tag="tn",
                subaccount_id="sub-tn-1",
                store=st,
                adapter=adp,
                status_path=st_path,
                dossier_root=d.root,
                config=contracts.ExecConfig(initial_qty_btc=D("0.1"), max_entry_slice_btc=D("0.1")),
            )
            coord.startup_init(now_ms=venue.server_time_ms)
            coord.handle_capital_policy(
                {"entry_armed": True, "budget_cap_btc": "0.20", "max_qty_btc": "0.1", "operator": "cfg"},
                now_ms=venue.server_time_ms,
                run_tick_after=False,
            )
            _drive(coord, venue, short_inst, long_inst, n=3, dt_ms=50)
            ep_tp = st.get_latest_episode()
            assert ep_tp is not None and ep_tp.phase == contracts.LifecyclePhase.TP_MANAGED

            # Seed CONDITION_TRIGGERED after entry_closed (TP_MANAGED) so sync-open --review fires Priority A trigger
            now[0] += 5 * pt13_test.M
            venue.set_server_time(now[0])
            _refresh_books(venue, short_inst, long_inst)
            coord.run_tick(now_ms=now[0])
            with p5_mod._short_writer(d):
                d.event(
                    ep_dir,
                    p5_mod.dossier.identity("chk", now[0] - pt13_test.M),
                    "CONDITION_TRIGGERED",
                    {"result": {"rule": "entry_not_returned"}},
                    observed=now[0] - pt13_test.M,
                    available=now[0],
                )
            coord.run_tick(now_ms=now[0])

            monkeypatch.setenv("ASTRA_AI_SHADOW_LOCK_TOKEN", str(wb.config.shadow_service_token))
            monkeypatch.setenv("LLM_API_KEY", "test-key")
            monkeypatch.setenv("LLM_BASE_URL", f"http://127.0.0.1:{llm_srv.server_port}/v1")
            monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
            monkeypatch.setenv("ASTRA_PLAYER_USAGE_LEDGER", str(shadow_ledger))
            monkeypatch.setenv("ASTRA_PLAYER_DAILY_HTTP_LIMIT", "15")
            monkeypatch.setenv("ASTRA_PLAYER_LIVE_DAILY_HTTP_LIMIT", "60")

            # Align Dossiers wall clock with the test's simulated clock so coordinator public_status TTL matches
            orig_dossiers = p5_mod.dossier.Dossiers
            monkeypatch.setattr(p5_mod.dossier, "Dossiers", lambda rp, clock=None, **kw: orig_dossiers(rp, clock=(clock or (lambda: now[0])), **kw))

            # Drive ACTUAL P5 CLI entrypoint (main(['--url', ..., 'sync-open', ...]))!
            rc = p5_mod.main(
                [
                    "--url",
                    wb_url,
                    "sync-open",
                    "--root",
                    str(d.root),
                    "--review",
                    "--exec-status-path",
                    str(st_path),
                    "--live-budget-path",
                    str(live_ledger),
                ]
            )
            assert rc in (None, 0)
            # Isolated 60/day live ledger incremented while exhausted 15/day shadow ledger remained untouched!
            live_snap = p5_mod.signal_llm_review.DailyHttpBudget(live_ledger, limit=60).snapshot()
            assert live_snap["http_calls_used"] >= 1
            assert live_snap["events"][-1]["role"] == "astra_player_live_hold_review"
            assert sh_budget.snapshot()["http_calls_used"] == 15

            ev_files = list((ep_dir / "events").glob("*.json"))
            assert any(json.loads(p.read_text(encoding="utf-8")).get("kind") == "AI_REVOKE" for p in ev_files)
            _drive(coord, venue, short_inst, long_inst, n=6, dt_ms=600)
            assert st.get_latest_episode() is not None
            assert st.get_latest_episode().phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
    finally:
        llm_srv.shutdown()
        llm_srv.server_close()
        st.close()


def test_deribit_rest_adapter_pagination_keepalive_zero_retry_and_idle_zero_books(tmp_path: Path):
    venue = _setup_venue()
    venue.seed_closed_orders("BTC-25NOV26-62000-P", 235, age_ms=10 * 60_000)

    def _mock_rpc(method: str, params: dict[str, Any], *, private: bool = False) -> dict[str, Any]:
        t_ms = venue.get_server_time()
        us_out = t_ms * 1000
        if method == "public/get_time":
            res = t_ms
        elif method == "public/get_index_price":
            res = {"index_price": 62785.0}
        elif method == "private/get_account_summary":
            res = venue.get_account_summary()
        elif method == "private/get_positions":
            res = []
        elif method == "private/get_open_orders_by_currency":
            res = []
        elif method == "private/get_order_history_by_instrument":
            inst = str(params["instrument_name"])
            cnt = int(params.get("count") or 200)
            off = int(params.get("offset") or 0)
            hist = bool(params.get("historical") or False)
            rows = venue.get_order_history_by_instrument(inst, count=cnt, offset=off, historical=hist)
            res = [
                {
                    "order_id": o.order_id,
                    "label": o.label,
                    "instrument_name": o.instrument_name,
                    "direction": o.direction,
                    "amount": float(o.amount),
                    "filled_amount": float(o.filled_amount),
                    "average_price": float(o.average_price) if o.average_price is not None else 0.0,
                    "price": float(o.price) if o.price is not None else 0.0,
                    "time_in_force": o.time_in_force,
                    "reduce_only": o.reduce_only,
                    "post_only": o.post_only,
                    "order_state": o.order_state,
                    "creation_timestamp": int(getattr(o, "created_at_ms", None) or getattr(o, "creation_timestamp_ms", 0)),
                    "last_update_timestamp": int(getattr(o, "last_update_ms", None) or getattr(o, "last_update_timestamp_ms", 0)),
                }
                for o in rows
            ]
        elif method == "private/get_user_trades_by_currency_and_time":
            res = {"trades": [], "has_more": False}
        else:
            res = {}
        return {"result": res, "usIn": us_out, "usOut": us_out}

    adp = coord_mod.DeribitRestAdapter(
        mode="TESTNET",
        base_url="https://test.deribit.com",
        client_id="cid",
        client_secret="csec",
        subaccount_id="sub-tn-1",
        http_fn=_mock_rpc,
    )
    facts = adp.fetch_facts("", "", need_tx_logs=False)
    assert facts.balance_btc == D("1.0")
    assert facts.portfolio_margining_enabled is True
    assert facts.margin_model == "PM"
    assert facts.clock_rtt_ok is True

    # >200 single-instrument pagination
    p1 = adp.get_order_history_by_instrument("BTC-25NOV26-62000-P", count=200, offset=0, historical=False)
    p2 = adp.get_order_history_by_instrument("BTC-25NOV26-62000-P", count=200, offset=200, historical=False)
    assert len(p1) == 200 and len(p2) == 35

    # Live Keep-Alive socket test: reused socket RemoteDisconnected retries ONCE on idempotent read, NEVER on write!
    class _FakeResp:
        def __init__(self, body: bytes) -> None:
            self.status = 200
            self._body = body

        def read(self) -> bytes:
            return self._body

        def getheader(self, _k: str) -> None:
            return None

    calls_log: list[tuple[str, bool]] = []

    class _FakeConn:
        def __init__(self, idx: int) -> None:
            self.idx = idx

        def request(self, _m: str, path: str, headers: dict[str, str] | None = None) -> None:
            is_buy = "private/buy" in path
            calls_log.append((path, self.idx == 1))
            if self.idx == 1:
                raise http.client.RemoteDisconnected("stale_keepalive")
            if is_buy:
                raise TimeoutError("write_timeout")

        def getresponse(self) -> _FakeResp:
            t_ms = venue.get_server_time()
            return _FakeResp(json.dumps({"result": t_ms, "usIn": t_ms * 1000, "usOut": t_ms * 1000}).encode("utf-8"))

        def close(self) -> None:
            return None

    adp_ka = coord_mod.DeribitRestAdapter(
        mode="TESTNET",
        base_url="https://test.deribit.com",
        client_id="cid",
        client_secret="csec",
    )
    adp_ka._access_token = "tok"
    adp_ka._token_expires_ms = int(time.time() * 1000) + 3_600_000
    adp_ka._conn_last_used_mono = time.monotonic()

    class _FirstGoodThenStale:
        def __init__(self, idx: int) -> None:
            self.idx = idx
            self.calls = 0

        def request(self, _m: str, path: str, headers: dict[str, str] | None = None) -> None:
            self.calls += 1
            calls_log.append((path, self.idx == 1 and self.calls == 2))
            if self.idx == 1 and self.calls == 2:
                raise http.client.RemoteDisconnected("stale_reused")
            if "private/buy" in path:
                raise TimeoutError("write_timed_out")

        def getresponse(self) -> _FakeResp:
            t_ms = venue.get_server_time()
            return _FakeResp(json.dumps({"result": t_ms, "usIn": t_ms * 1000, "usOut": t_ms * 1000}).encode("utf-8"))

        def close(self) -> None:
            return None

    conn1 = _FirstGoodThenStale(1)
    adp_ka._conn = conn1
    adp_ka._conn_last_used_mono = time.monotonic()
    adp_ka._ensure_conn = lambda **kw: (  # type: ignore[method-assign]
        (adp_ka._conn, True)
        if adp_ka._conn is not None
        else (setattr(adp_ka, "_conn", _FirstGoodThenStale(2)) or adp_ka._conn, False)
    )
    # First read populates good socket, second read hits RemoteDisconnected on reused socket -> reconnects & succeeds!
    assert adp_ka.get_server_time_ms() == venue.get_server_time()
    assert adp_ka.get_server_time_ms() == venue.get_server_time()
    assert conn1.calls == 2  # failed once on stale socket, reconnected on conn2!

    # Write on private/buy MUST make EXACTLY 1 attempt and raise VenueTransportError("network_unknown") (0 retries!)
    buy_attempts_before = len([c for c in calls_log if "private/buy" in c[0]])
    with pytest.raises(coord_mod.VenueTransportError) as exc_info:
        adp_ka.submit_order(
            instrument_name="BTC-25NOV26-62000-P",
            direction="buy",
            amount=D("0.1"),
            limit_price=D("0.0010"),
            label="test_lbl",
            time_in_force="IOC",
            reduce_only=True,
            post_only=False,
            reject_post_only=False,
            valid_until_ms=venue.get_server_time() + 10_000,
        )
    assert exc_info.value.code == "network_unknown"
    buy_attempts_after = len([c for c in calls_log if "private/buy" in c[0]])
    assert buy_attempts_after - buy_attempts_before == 1  # ZERO retries!

    # Coordinator in IDLE does ZERO option-book fetches!
    sim_venue = _setup_venue()
    st, sim_adp, coord, _dos_root = _new_coord(tmp_path, sim_venue, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        before_books = sim_adp.book_fetch_calls
        coord.run_tick(now_ms=sim_venue.server_time_ms)
        coord.run_tick(now_ms=sim_venue.server_time_ms + 5_000)
        assert sim_adp.book_fetch_calls == before_books
    finally:
        st.close()


# ---------------------------------------------------------------------------
# Mode Guards, Credentials, Two-Stage Attribution Fences, Release/Health, Testnet T1-T5
# ---------------------------------------------------------------------------


def test_adapter_mode_boundaries_and_missing_creds(tmp_path: Path):
    adp_shadow = coord_mod.DeribitRestAdapter(mode="SHADOW_ONLY", base_url="https://test.deribit.com")
    assert adp_shadow.fetch_facts("", "").api_error is None
    with pytest.raises((coord_mod.EnvironmentGuardError, coord_mod.WriteBlockedByModeError)):
        adp_shadow.submit_order(
            instrument_name="BTC-25NOV26-62000-P",
            direction="buy",
            amount=D("0.1"),
            limit_price=D("0.001"),
            label="x",
            time_in_force="IOC",
            reduce_only=False,
            post_only=False,
            reject_post_only=False,
            valid_until_ms=1_000,
        )
    with pytest.raises(coord_mod.EnvironmentGuardError):
        coord_mod.DeribitRestAdapter(mode="TESTNET", base_url="https://www.deribit.com", client_id="k", client_secret="s")
    adp_nocred = coord_mod.DeribitRestAdapter(mode="TESTNET", base_url="https://test.deribit.com", client_id=None, client_secret="s")
    with pytest.raises(coord_mod.VenueTransportError):
        adp_nocred._ensure_auth()

    st = store.ExecutionStore(tmp_path / "mguard.sqlite3")
    try:
        venue = _setup_venue()
        adp = SimExchangeAdapter(venue)
        c_shad = coord_mod.ExecutionCoordinator(mode="SHADOW_ONLY", env_tag="tn", store=st, adapter=adp, status_path=tmp_path / "s.json")
        assert c_shad.writes_permitted_by_mode() is False
        c_tn = coord_mod.ExecutionCoordinator(mode="TESTNET", env_tag="tn", store=st, adapter=adp, status_path=tmp_path / "t.json")
        assert c_tn.writes_permitted_by_mode() is True
        with pytest.raises(coord_mod.EnvironmentGuardError):
            coord_mod.ExecutionCoordinator(mode="MAINNET", env_tag="mn", store=st, adapter=adp, status_path=tmp_path / "m.json")
    finally:
        st.close()


def test_attribution_consistency_no_stale_cache_and_two_stage_fences(tmp_path: Path):
    venue = _setup_venue()
    st, adp, coord, dos_root = _new_coord(tmp_path, venue, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        _build_p5_dossier(dos_root, decided_at_ms=venue.server_time_ms - 5_000)
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=50)
        ep = st.get_latest_episode()
        assert ep is not None

        # 1) When venue returns api_error (e.g. 429) facts carries api_error; 2) When transport raises with use_cache=False, _fetch_facts_safe returns None (zero stale cache!)
        venue.inject_429(1, retry_after_ms=2000)
        res_429 = coord._fetch_facts_safe(ep.short_instrument, ep.long_instrument, t_now=venue.server_time_ms, use_cache=False)
        assert res_429 is not None and "429" in str(res_429.api_error or "")
        orig_fetch = adp.fetch_facts
        adp.fetch_facts = lambda *_a, **_kw: (_ for _ in ()).throw(coord_mod.VenueTransportError("network_error", "boom"))  # type: ignore[method-assign]
        res_err = coord._fetch_facts_safe(ep.short_instrument, ep.long_instrument, t_now=venue.server_time_ms, use_cache=False)
        assert res_err is None
        adp.fetch_facts = orig_fetch  # type: ignore[method-assign]

        # External trade with unknown order_id & unrelated label MUST be ignored by rebuild_episode_ledger
        facts = adp.fetch_facts(ep.short_instrument, ep.long_instrument)
        fake_trade = contracts.VenueTrade(
            trade_id="ext_t99",
            order_id="ext_oid_99",
            label="external_unrelated",
            instrument_name=ep.short_instrument,
            direction="buy",
            amount=D("0.5"),
            price=D("0.0010"),
            fee=D("0.00001"),
            fee_currency="BTC",
            timestamp_ms=ep.created_at_ms + 50,
            liquidity="T",
        )
        facts_ext = contracts.ExchangeFacts(**{**facts.__dict__, "recent_trades": facts.recent_trades + (fake_trade,)})
        st.ingest_facts_events(ep, facts_ext, now_ms=venue.server_time_ms)
        ledger = st.rebuild_episode_ledger(ep.episode_id)
        assert "ext_t99" not in str(ledger)
    finally:
        st.close()


def test_release_build_deploy_health_and_testnet_t1_to_t5_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 1. Sealed release build into tmp_path verifies clean manifest & execution drop-in hygiene
    out_pkg = tmp_path / "pkg"
    manifest = build_release.build_release(ROOT, out_pkg, allow_dirty=True)
    assert manifest["schema"] == "xxproject_server_release@1.0.0"

    # 2. Deploy with_execution + health check
    prefix = tmp_path / "prefix"
    monkeypatch.setattr(deploy_release, "_validate_execution_os_isolation", lambda _p: None)
    monkeypatch.setattr(deploy_release, "_migrate_exec_shared_permissions", lambda _p: None)
    r_inst = deploy_release.cmd_install(out_pkg, prefix, apply_units=True, no_systemd=True, with_execution=True)
    assert r_inst["ok"] is True and any("exec" in u for u in r_inst["installed_units"])

    # 3. Testnet verifier refuses mainnet & missing creds
    monkeypatch.delenv("DERIBIT_TESTNET_CLIENT_ID", raising=False)
    monkeypatch.delenv("DERIBIT_TESTNET_CLIENT_SECRET", raising=False)
    rc_ref = tv_mod.main(["--base-url", "https://www.deribit.com", "--scenario", "t1"])
    assert rc_ref != 0

    # 4. Run t1 and t2 via run_testnet_scenario over real Coordinator & SimulatedDeribitVenue
    venue = _setup_venue()
    adp = SimExchangeAdapter(venue)

    def _hook(scen: str, c_obj: coord_mod.ExecutionCoordinator, step_idx: int) -> None:
        venue.advance_time(300)
        ep = c_obj.store.get_latest_episode()
        if not ep:
            return
        if scen == "t1" and ep.phase == contracts.LifecyclePhase.TP_MANAGED and step_idx == 2:
            tp_c = next((x for x in c_obj.store.get_commands(ep.episode_id) if x.purpose == "TP" and x.status == contracts.CommandStatus.OPEN), None)
            if tp_c:
                venue.fill_resting_order(tp_c.label, ep.matched_qty_btc, price=D("0.0002"), liquidity="M")
        _refresh_books(venue, ep.short_instrument, ep.long_instrument)

    rc1, res1 = tv_mod.run_testnet_scenario(
        "t1", adapter=adp, subaccount_id="sub-tn-1", work_dir=tmp_path / "t1",
        short_instrument="BTC-25NOV26-62000-P", long_instrument="BTC-25NOV26-60000-P",
        wait_seconds=1, poll_interval_s=0.0, step_hook=_hook,
    )
    assert rc1 == 0 and res1["status"] == "PASSED"

    venue2 = _setup_venue()
    adp2 = SimExchangeAdapter(venue2)
    def _hook2(scen: str, c_obj: coord_mod.ExecutionCoordinator, step_idx: int) -> None:
        venue2.advance_time(500)
        ep = c_obj.store.get_latest_episode()
        if not ep:
            return
        if ep.phase == contracts.LifecyclePhase.TP_MANAGED and step_idx == 3:
            _write_p5_event(c_obj.dossier_root, dossier_id=ep.dossier_id, ep_id=ep.p5_episode_id, kind="AI_REVOKE", ts_ms=venue2.server_time_ms)  # type: ignore[arg-type]
        _refresh_books(venue2, ep.short_instrument, ep.long_instrument)

    rc2, res2 = tv_mod.run_testnet_scenario(
        "t2", adapter=adp2, subaccount_id="sub-tn-1", work_dir=tmp_path / "t2",
        short_instrument="BTC-25NOV26-62000-P", long_instrument="BTC-25NOV26-60000-P",
        wait_seconds=1, poll_interval_s=0.0, step_hook=_hook2,
    )
    assert rc2 == 0 and res2["status"] == "PASSED"


# ---------------------------------------------------------------------------
# M1 / M2 / M4 Closure Tests (Sync Trade Ingest, Read-Lag Fences, Control Races,
# Real Clock, Subaccount/Socket Guards, Fast-Poll Storm, Both-ITM Grace, t3..t5)
# ---------------------------------------------------------------------------


def test_m1_sync_order_trades_ingestion_and_async_read_lag_fences(tmp_path: Path):
    # 1. submit_order response trades with missing label attribute cleanly via vo.order_id and dedupe late get_user_trades
    venue = _setup_venue()
    st, adp, coord, dos_root = _new_coord(tmp_path / "case1", venue, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        _build_p5_dossier(dos_root, decided_at_ms=venue.server_time_ms - 5_000)

        def _submit_with_unlabeled_trade(**kwargs: Any) -> contracts.ExchangeOrder:
            oo = venue.submit_order(
                kwargs["instrument_name"],
                kwargs["direction"],
                kwargs["amount"],
                kwargs["limit_price"],
                label=kwargs["label"],
                time_in_force=kwargs["time_in_force"],
                reduce_only=kwargs["reduce_only"],
                post_only=kwargs["post_only"],
                reject_post_only=kwargs["reject_post_only"],
                valid_until=kwargs.get("valid_until_ms"),
            )
            matching_trades = tuple(
                contracts.VenueTrade(
                    trade_id=t.trade_id,
                    order_id=t.order_id,
                    label="",  # simulate Deribit omitting label in res["trades"]
                    instrument_name=t.instrument_name,
                    direction=t.direction,
                    amount=D(t.amount),
                    price=D(t.price),
                    fee=D(t.fee),
                    fee_currency=t.fee_currency,
                    timestamp_ms=t.timestamp_ms,
                    liquidity=t.liquidity,
                )
                for t in venue._trades
                if t.order_id == oo.order_id
            )
            eo = contracts.ExchangeOrder(
                order_id=oo.order_id,
                label=oo.label,
                instrument_name=oo.instrument_name,
                direction=oo.direction,
                amount=D(oo.amount),
                filled_amount=D(oo.filled_amount),
                price=D(oo.price),
                average_price=D(oo.average_price) if oo.average_price is not None else None,
                order_state=oo.order_state,
                time_in_force=oo.time_in_force,
                reduce_only=oo.reduce_only,
                post_only=oo.post_only,
                creation_timestamp_ms=int(oo.creation_timestamp_ms),
                last_update_timestamp_ms=int(oo.last_update_timestamp_ms),
                trades=matching_trades,
            )
            return eo

        adp.submit_order = _submit_with_unlabeled_trade  # type: ignore[method-assign]
        _refresh_books(venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P")
        coord.run_tick(now_ms=venue.server_time_ms)
        ep = st.get_latest_episode()
        assert ep is not None
        assert ep.entry_closed is True
        cmds_t1 = st.get_commands(ep.episode_id)
        assert all(c.status == contracts.CommandStatus.FILLED for c in cmds_t1 if c.purpose in {"EP", "ES"})
        trade_rows = [ev for ev in st.get_venue_events(ep.episode_id) if ev["event_type"] == "USER_TRADE"]
        assert len(trade_rows) == 2

        venue.advance_time(50)
        _refresh_books(venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P")
        coord.run_tick(now_ms=venue.server_time_ms)
        ep = st.get_episode(ep.episode_id)
        assert ep is not None and ep.phase == contracts.LifecyclePhase.TP_MANAGED
        # Duplicate trade arriving in subsequent recent_trades poll must NOT double-count
        venue.advance_time(100)
        _refresh_books(venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P")
        coord.run_tick(now_ms=venue.server_time_ms)
        trade_rows_after = [ev for ev in st.get_venue_events(ep.episode_id) if ev["event_type"] == "USER_TRADE"]
        assert len(trade_rows_after) == 2
    finally:
        st.close()

    # 2. Async read-lag: submit_order omits res["trades"], AND fetch_facts hides recent_trades + positions for 2 ticks!
    venue2 = _setup_venue()
    st2, adp2, coord2, dos_root2 = _new_coord(tmp_path / "case2", venue2, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        _build_p5_dossier(dos_root2, decided_at_ms=venue2.server_time_ms - 5_000)
        orig_fetch = adp2.fetch_facts
        lag_mode = [True]

        def _laggy_fetch(short_inst: str, long_inst: str, *, need_tx_logs: bool = True) -> contracts.ExchangeFacts:
            facts = orig_fetch(short_inst, long_inst, need_tx_logs=need_tx_logs)
            if lag_mode[0]:
                return contracts.ExchangeFacts(**{**facts.__dict__, "positions": (), "recent_trades": ()})
            return facts

        adp2.fetch_facts = _laggy_fetch  # type: ignore[method-assign]
        _refresh_books(venue2, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P")
        coord2.run_tick(now_ms=venue2.server_time_ms)
        ep2 = st2.get_latest_episode()
        assert ep2 is not None
        ep_cmds = [c for c in st2.get_commands(ep2.episode_id) if c.purpose == "EP"]
        assert len(ep_cmds) == 1 and ep_cmds[0].status == contracts.CommandStatus.SUBMIT_UNKNOWN
        assert ep2.entry_closed is False
        assert ep2.terminal_outcome is None  # NO false ABORTED_ZERO_FILL!

        # 2 more ticks while lagged: ZERO duplicate EP, ZERO o_prot=0 ES, ZERO premature entry_closed
        for _ in range(2):
            venue2.advance_time(100)
            _refresh_books(venue2, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P")
            coord2.run_tick(now_ms=venue2.server_time_ms)
        ep2_mid = st2.get_episode(ep2.episode_id)
        assert ep2_mid is not None and ep2_mid.entry_closed is False
        assert len([c for c in st2.get_commands(ep2.episode_id) if c.purpose == "EP"]) == 1
        assert len([c for c in st2.get_commands(ep2.episode_id) if c.purpose == "ES"]) == 0

        # Once venue reads catch up, coordinator converges cleanly to TP_MANAGED with single EP & single ES!
        lag_mode[0] = False
        _drive(coord2, venue2, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=3, dt_ms=50)
        ep2_done = st2.get_episode(ep2.episode_id)
        assert ep2_done is not None and ep2_done.phase == contracts.LifecyclePhase.TP_MANAGED
        assert ep2_done.matched_qty_btc == D("0.1")
        assert len([c for c in st2.get_commands(ep2.episode_id) if c.purpose == "EP"]) == 1
        assert len([c for c in st2.get_commands(ep2.episode_id) if c.purpose == "ES"]) == 1
    finally:
        st2.close()


def test_m1_control_race_interrupt_preserves_latches_and_allows_subsequent_episode_after_exit_all(tmp_path: Path):
    # 1. Slow run_tick holding/about-to-release around mocked slow fetch_facts interrupted by concurrent disable_writes & exit_all
    venue = _setup_venue()
    st, adp, coord, dos_root = _new_coord(tmp_path / "race", venue, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        _build_p5_dossier(dos_root, decided_at_ms=venue.server_time_ms - 5_000)
        _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=50)
        ep = st.get_latest_episode()
        assert ep is not None and ep.phase == contracts.LifecyclePhase.TP_MANAGED

        entered_fetch = threading.Event()
        release_fetch = threading.Event()
        orig_fetch = adp.fetch_facts
        submits_after_ctrl: list[str] = []
        orig_submit = adp.submit_order
        ctrl_done = [False]

        def _slow_fetch(short_inst: str, long_inst: str, *, need_tx_logs: bool = True) -> contracts.ExchangeFacts:
            entered_fetch.set()
            release_fetch.wait(timeout=3.0)
            if ctrl_done[0]:
                raise coord_mod.VenueTransportError("network_error", "post_latch_tick_boom")
            return orig_fetch(short_inst, long_inst, need_tx_logs=need_tx_logs)

        def _spy_submit(**kwargs: Any) -> contracts.ExchangeOrder:
            if ctrl_done[0]:
                submits_after_ctrl.append(str(kwargs.get("label")))
            return orig_submit(**kwargs)

        adp.fetch_facts = _slow_fetch  # type: ignore[method-assign]
        adp.submit_order = _spy_submit  # type: ignore[method-assign]

        t_tick = threading.Thread(target=lambda: coord.run_tick(now_ms=venue.server_time_ms))
        t_tick.start()
        assert entered_fetch.wait(timeout=2.0)

        ctrl_results: dict[str, Any] = {}
        t_dw = threading.Thread(
            target=lambda: ctrl_results.setdefault("dw", coord.handle_disable_writes("RACE_TEST", now_ms=venue.server_time_ms + 5))
        )
        t_ea = threading.Thread(
            target=lambda: ctrl_results.setdefault("ea", coord.handle_exit_all(reason="RACE_EA", now_ms=venue.server_time_ms + 6))
        )
        t_dw.start()
        t_ea.start()
        # Wait briefly so both control threads enter _enter_control_op() before releasing fetch
        deadline_w = time.monotonic() + 2.0
        while coord._pending_control_ops < 2 and time.monotonic() < deadline_w:
            time.sleep(0.005)
        ctrl_done[0] = True
        release_fetch.set()
        t_tick.join(timeout=3.0)
        t_dw.join(timeout=3.0)
        t_ea.join(timeout=3.0)

        assert submits_after_ctrl == []
        assert st.writes_disabled() == (True, "RACE_TEST")
        assert coord.writes_permitted_by_mode() is False
        ep_after = st.get_episode(ep.episode_id)
        assert ep_after is not None and ep_after.operator_exit_all is True and ep_after.exit_latched is True
        assert ctrl_results["dw"]["ok"] is True and ctrl_results["ea"]["ok"] is True
        assert coord._pending_control_ops == 0 and coord._pending_exit_all == 0
    finally:
        st.close()

    # 2. After an /exit-all episode settles, a NEW fresh episode in the SAME coordinator instance MUST admit cleanly!
    venue2 = _setup_venue()
    st2, _adp2, coord2, dos_root2 = _new_coord(tmp_path / "next_ep", venue2, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        _build_p5_dossier(
            dos_root2,
            dossier_id="Dos1",
            hand_id="hand_ea_1",
            ep_id="ep_ea_1",
            decided_at_ms=venue2.server_time_ms - 5_000,
        )
        _drive(coord2, venue2, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=50)
        ea_res = coord2.handle_exit_all(reason="CLEAN_EXIT_ALL", now_ms=venue2.server_time_ms)
        assert ea_res["ok"] is True and coord2._pending_exit_all == 0
        _drive(coord2, venue2, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=6, dt_ms=500)
        ep1 = st2.get_episode(ea_res["episode_id"])
        assert ep1 is not None and ep1.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
        assert st2.get_capital_auth().entry_armed is False

        venue2.advance_time(5_000)
        coord2.handle_capital_policy(
            {"entry_armed": True, "operator": "rearm_after_exit_all"},
            now_ms=venue2.server_time_ms,
            run_tick_after=False,
        )
        _build_p5_dossier(
            dos_root2,
            dossier_id="Dos2",
            hand_id="hand_ea_2",
            ep_id="ep_ea_2",
            decided_at_ms=venue2.server_time_ms - 2_000,
        )
        _drive(coord2, venue2, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=50)
        eps = _all_episodes(st2)
        assert len(eps) == 2 and eps[1].hand_id == "hand_ea_2"
        assert eps[1].phase == contracts.LifecyclePhase.TP_MANAGED
    finally:
        st2.close()


def test_m1_real_wall_clock_subaccount_socket_startup_dcod_and_fast_poll(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 1. Real wall clock (now_ms=None) + 600ms transport latency -> _book_fresh_ok True and NO false CLOCK_SKEW_HIGH
    venue = _setup_venue()
    now_wall = int(time.time() * 1000)
    venue.set_server_time(now_wall)
    _refresh_books(venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P")

    def _http_with_latency(method: str, params: dict[str, Any], *, private: bool = False) -> dict[str, Any]:
        t_cur = int(time.time() * 1000)
        venue.set_server_time(t_cur)
        if method == "public/get_order_book":
            time.sleep(0.05)  # simulate non-zero latency; test 600ms via timestamp offset on book below
            inst = str(params["instrument_name"])
            q = venue.get_order_book(inst, depth=5)
            q["timestamp"] = t_cur - 600
            return q
        if method == "public/get_time":
            return t_cur
        if method == "public/get_index_price":
            return {"index_price": 62785.0}
        if method == "public/get_instrument":
            inst = str(params["instrument_name"])
            return {
                "instrument_name": inst,
                "kind": "option",
                "settlement_period": "day",
                "strike": 62000.0 if "62000" in inst else 60000.0,
                "option_type": "put",
                "expiration_timestamp": now_wall + 10_800_000,
                "tick_size": 0.0001,
                "tick_size_steps": [],
                "min_trade_amount": 0.1,
                "maker_commission": 0.0003,
                "taker_commission": 0.0003,
            }
        if method == "private/disable_cancel_on_disconnect":
            return {"ok": True}
        if method == "private/get_account_summary":
            return {**venue.get_account_summary(), "id": 1001, "username": "sub-tn-1", "type": "subaccount"}
        if method == "private/get_positions":
            return []
        if method == "private/get_open_orders_by_currency":
            return []
        if method in {"private/get_user_trades_by_instrument", "private/get_user_trades_by_currency_and_time"}:
            return {"trades": [], "has_more": False}
        if method == "private/get_transaction_log":
            return {"logs": [], "continuation": None}
        raise AssertionError(f"unexpected method {method}")

    adp_real = coord_mod.DeribitRestAdapter(
        mode="TESTNET",
        base_url="https://test.deribit.com",
        client_id="cid",
        client_secret="csec",
        subaccount_id="sub-tn-1",
        http_fn=lambda m, p, **kw: _http_with_latency(m, p, private=bool(kw.get("private"))),
    )
    facts_wall = adp_real.fetch_facts("BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", need_tx_logs=False)
    t_eval = int(time.time() * 1000)
    assert facts_wall.api_error is None
    assert policy._book_fresh_ok(facts_wall.short_book, t_eval, 2_000) is True
    assert abs(t_eval - facts_wall.server_time_ms) < 2_000

    # 2. Subaccount guards reject empty, "0", and type="main" in TESTNET
    for bad_sub in ("", "0"):
        adp_bad_sub = coord_mod.DeribitRestAdapter(
            mode="TESTNET",
            base_url="https://test.deribit.com",
            client_id="cid",
            client_secret="csec",
            subaccount_id=bad_sub,
            http_fn=lambda m, p, **kw: _http_with_latency(m, p, private=bool(kw.get("private"))),
        )
        res_sub = adp_bad_sub.fetch_facts("BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P")
        assert res_sub.api_error == "subaccount_id_required_non_shadow"

    adp_main = coord_mod.DeribitRestAdapter(
        mode="TESTNET",
        base_url="https://test.deribit.com",
        client_id="cid",
        client_secret="csec",
        subaccount_id="1",
        http_fn=lambda m, p, **kw: (
            int(time.time() * 1000)
            if m == "public/get_time"
            else {"id": 1, "username": "main", "type": "main", "equity": 1.0, "available_funds": 1.0, "initial_margin": 0.0, "maintenance_margin": 0.0, "balance": 1.0}
            if m == "private/get_account_summary"
            else {}
        ),
    )
    res_main = adp_main.fetch_facts("", "")
    assert str(res_main.api_error or "").startswith("main_account_forbidden:")

    # 3. POSIX non-SHADOW_ONLY daemon refuses missing --control-socket
    monkeypatch.setattr(coord_mod, "_is_posix_runtime", lambda: True)
    with pytest.raises(coord_mod.EnvironmentGuardError, match="CONTROL_SOCKET_REQUIRED_ON_POSIX_NON_SHADOW"):
        coord_mod.main(["--mode", "TESTNET", "--db", str(tmp_path / "p.sqlite3"), "--status-path", str(tmp_path / "st.json")])
    monkeypatch.undo()

    # 4. startup_init initial_max_qty_btc alone + DCOD failure retry blocking admission via DCOD_UNCONFIRMED
    v_dcod = _setup_venue()
    st_dcod = store.ExecutionStore(tmp_path / "dcod.sqlite3")
    adp_dcod = SimExchangeAdapter(v_dcod)
    dos_dcod = tmp_path / "dos_dcod"
    dos_dcod.mkdir()
    _build_p5_dossier(dos_dcod, decided_at_ms=v_dcod.server_time_ms - 2_000)
    fail_dcod = [True]
    orig_dcod_fn = adp_dcod.disable_cancel_on_disconnect

    def _flaky_dcod() -> None:
        if fail_dcod[0]:
            raise RuntimeError("dcod_rpc_down")
        orig_dcod_fn()

    adp_dcod.disable_cancel_on_disconnect = _flaky_dcod  # type: ignore[method-assign]
    c_dcod = coord_mod.ExecutionCoordinator(
        mode="TESTNET", env_tag="tn", subaccount_id="sub-tn-1",
        store=st_dcod, adapter=adp_dcod, status_path=tmp_path / "dcod_st.json", dossier_root=dos_dcod,
    )
    try:
        c_dcod.startup_init(initial_max_qty_btc=D("0.1"), now_ms=v_dcod.server_time_ms)
        assert st_dcod.get_capital_auth().max_qty_btc == D("0.1")
        st_dcod.set_capital_policy(entry_armed=True, budget_cap_btc=D("0.2"), max_qty_btc=D("0.1"), now_ms=v_dcod.server_time_ms)
        st_blk = c_dcod.run_tick(now_ms=v_dcod.server_time_ms)
        assert c_dcod._last_reject_reason == "DCOD_UNCONFIRMED"
        assert "dcod_unconfirmed" in str(st_blk.get("last_error") or "")
        assert st_dcod.get_active_episode() is None

        fail_dcod[0] = False
        v_dcod.advance_time(6_000)
        _refresh_books(v_dcod, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P")
        c_dcod.run_tick(now_ms=v_dcod.server_time_ms)
        assert c_dcod._dcod_confirmed is True
        assert st_dcod.get_active_episode() is not None
    finally:
        st_dcod.close()

    # 5. has_fast_poll_trigger rejects stale >180s Accept & unverified revoke, accepts fresh ones
    v_fp = _setup_venue()
    st_fp, _adp_fp, c_fp, _dos_fp = _new_coord(tmp_path / "fp", v_fp, budget_cap_btc="0.2", max_qty_btc="0.1")
    try:
        t_base = v_fp.server_time_ms
        stale_c = contracts.P5DecisionContract(
            dossier_id="d1", hand_id="h_stale", episode_id="e1", decision_id="dec1",
            short_instrument="BTC-25NOV26-62000-P", long_instrument="BTC-25NOV26-60000-P",
            side="put", short_strike_usd=D("62000"), long_strike_usd=D("60000"),
            expiry_ms=t_base + 3_600_000, decision="Accept", decided_at_ms=t_base - 200_000,
            link_recorded_at_ms=t_base - 195_000, link_state="SHADOW_LOCK_CREATED",
            decision_sha256="s1", condition_sha256="s2", input_sha256="s3", raw_sha256="s4",
        )
        fresh_c = contracts.P5DecisionContract(**{**stale_c.__dict__, "hand_id": "h_fresh", "decided_at_ms": t_base - 5_000, "link_recorded_at_ms": t_base - 4_000})
        assert c_fp.has_fast_poll_trigger(None, {"h_stale": stale_c}, t_base) is False
        assert c_fp.has_fast_poll_trigger(None, {"h_fresh": fresh_c}, t_base) is True

        dummy_ep = contracts.EpisodeState(
            episode_id="ep_x", hand_id="h_act", p5_episode_id="e1", dossier_id="d1",
            env="tn", subaccount_id="sub-tn-1", side="put",
            short_instrument="BTC-25NOV26-62000-P", long_instrument="BTC-25NOV26-60000-P",
            short_strike_usd=D("62000"), long_strike_usd=D("60000"),
            expiry_ms=t_base + 3_600_000, contract_hash="ch_fp",
            phase=contracts.LifecyclePhase.TP_MANAGED, created_at_ms=t_base - 2_000,
            target_qty_btc=D("0.1"), matched_qty_btc=D("0.1"),
            auth_epoch=1, entry_started_at_ms=t_base - 1_000, entry_closed=True,
        )
        unver_rev = contracts.P5DecisionContract(**{**fresh_c.__dict__, "hand_id": "h_act", "revoked": True, "revoke_shadow_action": "DISCRETIONARY_REVOKE_UNVERIFIED"})
        ver_rev = contracts.P5DecisionContract(**{**fresh_c.__dict__, "hand_id": "h_act", "revoked": True, "revoke_shadow_action": "REVOKE_ALIGNED"})
        assert c_fp.has_fast_poll_trigger(dummy_ep, {"h_act": unver_rev}, t_base) is False
        assert c_fp.has_fast_poll_trigger(dummy_ep, {"h_act": ver_rev}, t_base) is True
    finally:
        st_fp.close()


def test_m1_tp_cancel_unknown_retry_both_itm_grace_and_admin_unknown_cli(tmp_path: Path):
    # 1. TP_QTY_MISMATCH_RESYNC retries cancel_by_label after CANCEL_UNKNOWN ages >= 2000ms
    now_ms = 1_795_450_350_000
    exp_ms = 1_795_507_200_000
    ep = contracts.EpisodeState(
        episode_id="ep_tp", hand_id="h_tp", p5_episode_id="p5_tp", dossier_id="dos_tp",
        env="tn", subaccount_id="sub-tn-1", side="put",
        short_instrument="BTC-25NOV26-62000-P", long_instrument="BTC-25NOV26-60000-P",
        short_strike_usd=D("62000"), long_strike_usd=D("60000"), expiry_ms=exp_ms,
        contract_hash="ch_tp", phase=contracts.LifecyclePhase.TP_MANAGED,
        created_at_ms=now_ms - 10_000,
        target_qty_btc=D("0.1"), matched_qty_btc=D("0.1"), auth_epoch=1,
        entry_started_at_ms=now_ms - 10_000, entry_closed=True,
    )
    tp_cmd = contracts.ExecCommand(
        cmd_id="cmd_tp_1", episode_id="ep_tp", purpose="TP",
        label="astra_tn_ep_tp_TP_3", instrument_name=ep.short_instrument, direction="buy",
        amount=D("0.2"), limit_price=D("0.0002"), time_in_force="GTC",
        reduce_only=True, post_only=True, reject_post_only=True, valid_until_ms=exp_ms,
        created_at_ms=now_ms - 5_000, status=contracts.CommandStatus.CANCEL_UNKNOWN,
        cancel_requested_at_ms=now_ms - 2_500, updated_at_ms=now_ms - 2_500, venue_order_id="oid_tp_1",
    )
    spec = contracts.InstrumentSpec(
        instrument_name=ep.short_instrument, kind="option", settlement_period="day",
        strike_usd=D("62000"), option_type="put", expiry_ms=exp_ms,
        tick_size=D("0.0001"), min_trade_amount=D("0.1"),
    )
    facts_tp = contracts.ExchangeFacts(
        asof_ms=now_ms, server_time_ms=now_ms, spot_usd=D("62785"),
        equity_btc=D("1"), available_funds_btc=D("1"), initial_margin_btc=ZERO, maintenance_margin_btc=ZERO,
        positions=(
            contracts.ExchangePosition(instrument_name=ep.short_instrument, kind="option", size=D("-0.1")),
            contracts.ExchangePosition(instrument_name=ep.long_instrument, kind="option", size=D("0.1")),
        ),
        open_orders=(
            contracts.ExchangeOrder(
                order_id="oid_tp_1", label="astra_tn_ep_tp_TP_3", instrument_name=ep.short_instrument,
                direction="buy", amount=D("0.2"), filled_amount=ZERO, price=D("0.0002"),
                average_price=None, order_state="open", time_in_force="good_til_cancelled",
                reduce_only=True, post_only=True, creation_timestamp_ms=now_ms - 5_000, last_update_timestamp_ms=now_ms - 5_000,
            ),
        ),
        open_orders_complete=True, history_complete=True, recent_orders_by_label={},
        recent_trades=(), tx_logs=(), tx_logs_complete=True,
        short_book=contracts.BookQuote(instrument_name=ep.short_instrument, bid_price=D("0.0044"), bid_amount=D("5"), ask_price=D("0.0046"), ask_amount=D("5"), mark_price=D("0.0045"), min_price=D("0.0001"), max_price=D("0.05"), timestamp_ms=now_ms, status=contracts.BookStatus.OK, fetched_at_ms=now_ms),
        long_book=contracts.BookQuote(instrument_name=ep.long_instrument, bid_price=D("0.0011"), bid_amount=D("5"), ask_price=D("0.0013"), ask_amount=D("5"), mark_price=D("0.0012"), min_price=D("0.0001"), max_price=D("0.05"), timestamp_ms=now_ms, status=contracts.BookStatus.OK, fetched_at_ms=now_ms),
        short_spec=spec, long_spec=contracts.InstrumentSpec(**{**spec.__dict__, "instrument_name": ep.long_instrument, "strike_usd": D("60000")}),
        subaccount_id="sub-tn-1",
    )
    ev_tp = [
        {"event_type": "USER_TRADE", "raw": {"trade_id": "t_ep", "order_id": "o_ep", "label": "astra_tn_ep_tp_EP_1", "instrument_name": ep.long_instrument, "direction": "buy", "amount": "0.1", "price": "0.0012", "fee": "0.00003", "timestamp_ms": now_ms - 9_000}},
        {"event_type": "USER_TRADE", "raw": {"trade_id": "t_es", "order_id": "o_es", "label": "astra_tn_ep_tp_ES_2", "instrument_name": ep.short_instrument, "direction": "sell", "amount": "0.1", "price": "0.0045", "fee": "0.00003", "timestamp_ms": now_ms - 8_000}},
    ]
    res_tp = policy.evaluate_step(
        ep, contracts.CapitalAuthorization(entry_armed=True, budget_cap_btc=D("0.2"), max_qty_btc=D("0.1"), auth_epoch=1),
        (tp_cmd,), ev_tp, facts_tp, now_ms, contracts.ExecConfig(initial_qty_btc=D("0.1"), max_entry_slice_btc=D("0.1")),
    )
    assert any(a.action_type == contracts.ActionType.CANCEL_BY_LABEL and a.target_cmd_id == "cmd_tp_1" for a in res_tp.actions)

    # 1b. Post-expiry incomplete/error facts (positions_complete=False, api_error!=None) MUST NOT clear liability or inventory!
    err_facts_exp = contracts.ExchangeFacts(
        asof_ms=exp_ms + 5_000, server_time_ms=exp_ms + 5_000, spot_usd=D("62785"),
        equity_btc=D("1"), available_funds_btc=D("1"), initial_margin_btc=ZERO, maintenance_margin_btc=ZERO,
        positions=(), open_orders=(), open_orders_complete=False, history_complete=False,
        recent_orders_by_label={}, recent_trades=(), tx_logs=(), tx_logs_complete=False,
        short_book=None, long_book=None, short_spec=spec, long_spec=facts_tp.long_spec,
        subaccount_id="sub-tn-1", api_error="10028", positions_complete=False,
    )
    res_err_exp = policy.evaluate_step(
        ep, contracts.CapitalAuthorization(entry_armed=True, budget_cap_btc=D("0.2"), max_qty_btc=D("0.1"), auth_epoch=1),
        (), ev_tp, err_facts_exp, exp_ms + 5_000, contracts.ExecConfig(initial_qty_btc=D("0.1"), max_entry_slice_btc=D("0.1")),
    )
    assert res_err_exp.episode_patch.short_liability_cleared is False
    assert res_err_exp.episode_patch.inventory_cleared is False
    assert res_err_exp.episode_patch.phase == contracts.LifecyclePhase.TP_MANAGED

    # 2. Both-ITM zero-net-future expiry waits 30s grace then settles with diff_btc == 0
    s_rows = [
        {"id": 9001, "type": "assignment", "instrument_name": ep.short_instrument, "amount": "0.1", "timestamp_ms": exp_ms + 100},
        {"id": 9002, "type": "exercise", "instrument_name": ep.long_instrument, "amount": "0.1", "timestamp_ms": exp_ms + 101},
    ]
    has_itm, all_legs, complete_early = contracts.compute_episode_expiry_settlement(
        short_instrument=ep.short_instrument, long_instrument=ep.long_instrument,
        es_qty=D("0.1"), close_short_qty=ZERO, ep_qty=D("0.1"), lx_qty=ZERO,
        stage1_rows=s_rows, stage2_rows=(), positions=(), expiry_ms=exp_ms, server_time_ms=exp_ms + 5_000,
    )
    assert has_itm and all_legs and not complete_early
    _has_itm2, _all2, complete_after = contracts.compute_episode_expiry_settlement(
        short_instrument=ep.short_instrument, long_instrument=ep.long_instrument,
        es_qty=D("0.1"), close_short_qty=ZERO, ep_qty=D("0.1"), lx_qty=ZERO,
        stage1_rows=s_rows, stage2_rows=(), positions=(), expiry_ms=exp_ms, server_time_ms=exp_ms + 30_000,
    )
    assert complete_after is True

    # 3. Orphan >25m PREPARED_UNKNOWN resolves via admin CLI --admin-resolve-unknown-cmd with SQLite audit record
    db_adm = tmp_path / "admin.sqlite3"
    st_adm = store.ExecutionStore(db_adm)
    try:
        old_ms = int(time.time() * 1000) - 2_000_000
        ep_adm = contracts.EpisodeState(
            episode_id="ep_adm", hand_id="h_adm", p5_episode_id="p5_adm", dossier_id="dos_adm",
            env="tn", subaccount_id="sub-tn-1", side="put",
            short_instrument="BTC-25NOV26-62000-P", long_instrument="BTC-25NOV26-60000-P",
            short_strike_usd=D("62000"), long_strike_usd=D("60000"), expiry_ms=old_ms + 50_000_000,
            contract_hash="ch_adm", phase=contracts.LifecyclePhase.ENTRY,
            created_at_ms=old_ms,
            target_qty_btc=D("0.1"), matched_qty_btc=ZERO, auth_epoch=1,
            entry_started_at_ms=old_ms,
        )
        st_adm.create_episode(ep_adm)
        st_adm.create_command_intent(
            contracts.ExecCommand(
                cmd_id="cmd_stale_unk", episode_id="ep_adm", purpose="EP",
                label="astra_tn_ep_adm_EP_1", instrument_name=ep_adm.long_instrument,
                direction="buy", amount=D("0.1"), limit_price=D("0.0013"),
                time_in_force="IOC", reduce_only=False, post_only=False, reject_post_only=False,
                valid_until_ms=old_ms + 10_000, created_at_ms=old_ms,
                status=contracts.CommandStatus.PREPARED_UNKNOWN, updated_at_ms=old_ms,
            ),
            now_ms=old_ms,
        )
    finally:
        st_adm.close()

    rc_adm = coord_mod.main([
        "--db", str(db_adm),
        "--status-path", str(tmp_path / "adm_st.json"),
        "--admin-resolve-unknown-cmd", "cmd_stale_unk",
        "--target-status", "EXPIRED_UNFILLED",
        "--operator", "ops_test",
        "--evidence-note", "verified_absent_on_deribit_history",
    ])
    assert rc_adm == 0
    st_adm2 = store.ExecutionStore(db_adm)
    try:
        cmds_adm = st_adm2.get_commands("ep_adm")
        assert cmds_adm[0].status == contracts.CommandStatus.EXPIRED_UNFILLED
        assert "ops_test" in str(cmds_adm[0].last_error or "")
        meta_rows = st_adm2.conn.execute("SELECT key, value FROM meta WHERE key LIKE 'admin_cmd_resolution:%'").fetchall()
        assert len(meta_rows) == 1 and "verified_absent_on_deribit_history" in str(meta_rows[0][1])
    finally:
        st_adm2.close()


def test_m4_simulated_t3_t4_single_both_itm_t5_and_negative_no_false_pass(tmp_path: Path):
    # 1. t3 (OTM hold-to-expiry)
    v3 = _setup_venue()
    adp3 = SimExchangeAdapter(v3)
    settled_t3 = [False]
    def _hook3(_sc: str, c3: coord_mod.ExecutionCoordinator, step_idx: int) -> None:
        v3.advance_time(500)
        ep = c3.store.get_latest_episode()
        if ep is None:
            return
        if not settled_t3[0] and ep.phase == contracts.LifecyclePhase.TP_MANAGED and step_idx >= 3:
            v3.set_server_time(ep.expiry_ms + 2_000)
            v3.expire_at_settlement(D("63000"))
            settled_t3[0] = True
        _refresh_books(v3, ep.short_instrument, ep.long_instrument)
    rc3, res3 = tv_mod.run_testnet_scenario(
        "t3", adapter=adp3, subaccount_id="sub-tn-1", work_dir=tmp_path / "t3",
        short_instrument="BTC-25NOV26-62000-P", long_instrument="BTC-25NOV26-60000-P",
        wait_seconds=1, poll_interval_s=0.0, step_hook=_hook3,
    )
    assert rc3 == 0 and res3["status"] == "PASSED"

    # 2. t4-single-itm and t4-both-itm
    for scen, settle_px in (("t4-single-itm", "61000"), ("t4-both-itm", "58000")):
        vx = _setup_venue(settlement_period="week")
        adpx = SimExchangeAdapter(vx)
        settled_flag = [False]

        def _hook_exp(_sc: str, cx: coord_mod.ExecutionCoordinator, step_idx: int, _v: sim.SimulatedDeribitVenue = vx, _px: str = settle_px, _flag: list[bool] = settled_flag) -> None:
            _v.advance_time(500)
            ep = cx.store.get_latest_episode()
            if ep is None:
                return
            if not _flag[0] and ep.phase == contracts.LifecyclePhase.TP_MANAGED and step_idx >= 3:
                _v.set_server_time(ep.expiry_ms + 35_000)
                _v.expire_at_settlement(D(_px))
                _flag[0] = True
            _refresh_books(_v, ep.short_instrument, ep.long_instrument)

        rc_e, res_e = tv_mod.run_testnet_scenario(
            scen, adapter=adpx, subaccount_id="sub-tn-1", work_dir=tmp_path / scen,
            short_instrument="BTC-25NOV26-62000-P", long_instrument="BTC-25NOV26-60000-P",
            wait_seconds=2, poll_interval_s=0.0, step_hook=_hook_exp,
        )
        assert rc_e == 0 and res_e["status"] == "PASSED"

    # 3. t5 (post-submit UNKNOWN fault + coordinator restart on same DB -> PASS)
    v5 = _setup_venue()
    adp5 = SimExchangeAdapter(v5)
    def _hook5(_sc: str, c5: coord_mod.ExecutionCoordinator, step_idx: int) -> None:
        v5.advance_time(500)
        ep = c5.store.get_latest_episode()
        if ep is not None and ep.phase == contracts.LifecyclePhase.TP_MANAGED and step_idx >= 3:
            _write_p5_event(c5.dossier_root, dossier_id=ep.dossier_id, ep_id=ep.p5_episode_id, kind="AI_REVOKE", ts_ms=v5.server_time_ms)  # type: ignore[arg-type]
        _refresh_books(v5, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P")

    rc5, res5 = tv_mod.run_testnet_scenario(
        "t5", adapter=adp5, subaccount_id="sub-tn-1", work_dir=tmp_path / "t5",
        short_instrument="BTC-25NOV26-62000-P", long_instrument="BTC-25NOV26-60000-P",
        wait_seconds=2, poll_interval_s=0.0, step_hook=_hook5,
    )
    assert rc5 == 0 and res5["status"] == "PASSED"

    # 4. Negative: LONG_ONLY_ABORT (matched_qty == 0) cannot false-PASS t1, t2, or t3!
    v_neg = _setup_venue()
    v_neg.set_book("BTC-25NOV26-62000-P", bid="0.0001", bid_size="0.0", ask="0.05", ask_size="1.0", mark_price="0.0045")
    adp_neg = SimExchangeAdapter(v_neg)
    rc_neg, res_neg = tv_mod.run_testnet_scenario(
        "t2", adapter=adp_neg, subaccount_id="sub-tn-1", work_dir=tmp_path / "t2_neg",
        short_instrument="BTC-25NOV26-62000-P", long_instrument="BTC-25NOV26-60000-P",
        wait_seconds=1, poll_interval_s=0.0,
        step_hook=lambda _s, _c, _i: v_neg.advance_time(5_000),
    )
    assert rc_neg != 0 and res_neg["status"] != "PASSED"


def test_closure_k_b1_daemon_poll_concurrent_control_and_http_400(tmp_path: Path):
    import urllib.request

    venue = _setup_venue()
    st, _adp, coord, dos_root = _new_coord(tmp_path / "kb1", venue, budget_cap_btc="0.20", max_qty_btc="0.1")
    _build_p5_dossier(dos_root, decided_at_ms=venue.server_time_ms - 5_000)
    srv, _ = coord_mod.start_control_server(coord, expected_token="kb1_tok", port=0)
    ctrl_url = f"http://127.0.0.1:{srv.server_port}"

    loop_errors: list[str] = []
    stop_flag = threading.Event()

    def _poll_worker() -> None:
        while not stop_flag.is_set():
            try:
                if coord.should_poll_now():
                    coord.run_tick()
            except Exception as exc:
                loop_errors.append(repr(exc))
            time.sleep(0.002)

    t_bg = threading.Thread(target=_poll_worker, daemon=True)
    t_bg.start()
    try:
        # Malformed Content-Length must return HTTP 400 JSON rather than unhandled traceback (Info 1)
        req_bad = urllib.request.Request(
            f"{ctrl_url}/exit-all",
            data=b"{}",
            headers={"Authorization": "Bearer kb1_tok", "Content-Length": "abc"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_http:
            urllib.request.urlopen(req_bad, timeout=5.0)
        assert exc_http.value.code == 400
        assert json.loads(exc_http.value.read().decode("utf-8"))["code"].startswith("invalid_request:")

        ctrl_errors: list[tuple[str, int, Any]] = []
        for idx in range(15):
            code, body = _raw_http(
                ctrl_url,
                "/capital-policy",
                method="POST",
                payload={"entry_armed": True, "budget_cap_btc": "0.15", "max_qty_btc": "0.1", "operator": f"op_{idx}"},
                headers={"Authorization": "Bearer kb1_tok"},
            )
            if code != 200 or not body.get("ok"):
                ctrl_errors.append(("cp", code, body))

        code_ex, body_ex = _raw_http(
            ctrl_url, "/exit-all", method="POST", payload={"operator": "ops_ex"}, headers={"Authorization": "Bearer kb1_tok"}
        )
        if code_ex != 200 or not body_ex.get("ok"):
            ctrl_errors.append(("exit_all", code_ex, body_ex))

        code_dw, body_dw = _raw_http(
            ctrl_url, "/disable-writes", method="POST", payload={"reason": "kb1_lock"}, headers={"Authorization": "Bearer kb1_tok"}
        )
        if code_dw != 200 or not body_dw.get("ok"):
            ctrl_errors.append(("disable_writes", code_dw, body_dw))

        assert not loop_errors, f"background daemon loop failed: {loop_errors}"
        assert not ctrl_errors, f"concurrent control HTTP failed: {ctrl_errors}"
        assert st.writes_disabled()[0] is True
    finally:
        stop_flag.set()
        t_bg.join(timeout=3.0)
        srv.shutdown()
        srv.server_close()
        st.close()


def test_closure_k_b2_slow_probe_and_admit_no_false_clock_skew(tmp_path: Path):
    # 1. Slow cold-start DeribitRestAdapter.fetch_facts (>2.1s between get_time and private calls) with 0ms true skew
    t_start = int(time.time() * 1000)

    def _slow_fetch_http(method: str, params: dict[str, Any], *, private: bool = False) -> dict[str, Any]:
        if method == "public/get_time":
            return int(time.time() * 1000)
        if method == "private/get_account_summary":
            time.sleep(0.35)
            return {"id": 1001, "username": "sub-tn-1", "type": "subaccount", "equity": 1.0, "available_funds": 1.0, "initial_margin": 0.0, "maintenance_margin": 0.0, "balance": 1.0, "index_price": 62785.0}
        if method in {"private/get_positions", "private/get_open_orders_by_currency"}:
            time.sleep(0.35)
            return []
        if method in {"private/get_user_trades_by_currency_and_time", "private/get_user_trades_by_instrument"}:
            return {"trades": [], "has_more": False}
        if method == "public/get_instrument":
            inst = str(params["instrument_name"])
            return {
                "instrument_name": inst, "kind": "option", "settlement_period": "day",
                "strike": 62000.0 if "62000" in inst else 60000.0, "option_type": "put",
                "expiration_timestamp": t_start + 10_800_000, "tick_size": 0.0001,
                "tick_size_steps": [], "min_trade_amount": 0.1,
            }
        if method == "public/get_order_book":
            time.sleep(0.6)  # Push total fetch_facts > 2.2s while books stay fresh at end
            inst = str(params["instrument_name"])
            return {
                "instrument_name": inst, "timestamp": int(time.time() * 1000), "index_price": 62785.0,
                "best_bid_price": 0.0044 if "62000" in inst else 0.0011, "best_bid_amount": 5.0,
                "best_ask_price": 0.0046 if "62000" in inst else 0.0013, "best_ask_amount": 5.0,
                "mark_price": 0.0045 if "62000" in inst else 0.0012, "min_price": 0.0001, "max_price": 0.05,
                "bids": [[0.0044, 5.0]], "asks": [[0.0046, 5.0]],
            }
        return {"ok": True}

    adp_slow = coord_mod.DeribitRestAdapter(
        mode="TESTNET", base_url="https://test.deribit.com", client_id="cid", client_secret="csec",
        subaccount_id="sub-tn-1", http_fn=lambda m, p, **kw: _slow_fetch_http(m, p, private=bool(kw.get("private"))),
    )
    facts_slow = adp_slow.fetch_facts("BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", need_tx_logs=False)
    assert contracts.facts_clock_skew_ms(facts_slow, int(time.time() * 1000)) < 1_000
    assert abs(facts_slow.server_time_ms - facts_slow.asof_ms) < 500

    # 2. Slow 2.5s order_probe during SUBMIT_UNKNOWN recovery MUST NOT trigger CLOCK_SKEW_HIGH / RUNTIME_ANOMALY_HALT
    venue = _setup_venue()
    st, _base_adp, coord, dos_root = _new_coord(tmp_path / "kb2_probe", venue, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        ep = contracts.EpisodeState(
            episode_id="ep_kb2", hand_id="h_kb2", p5_episode_id="p5_kb2", dossier_id="d_kb2",
            env="tn", subaccount_id="sub-tn-1", side="put",
            short_instrument="BTC-25NOV26-62000-P", long_instrument="BTC-25NOV26-60000-P",
            short_strike_usd=D("62000"), long_strike_usd=D("60000"),
            expiry_ms=venue.server_time_ms + 10_800_000, contract_hash="ch_kb2",
            phase=contracts.LifecyclePhase.ENTRY, created_at_ms=venue.server_time_ms - 1_000,
            target_qty_btc=D("0.1"), matched_qty_btc=ZERO, auth_epoch=1,
        )
        st.create_episode(ep)
        cmd_ep = contracts.ExecCommand(
            cmd_id="ep_kb2:cmd_0001_EP", episode_id="ep_kb2", purpose="EP",
            label="ax1:tn:kb2:EP:0001", instrument_name=ep.long_instrument, direction="buy",
            amount=D("0.1"), limit_price=D("0.0013"), time_in_force="IOC",
            reduce_only=False, post_only=False, reject_post_only=False,
            valid_until_ms=venue.server_time_ms - 3_000, created_at_ms=venue.server_time_ms - 8_000,
            status=contracts.CommandStatus.SUBMIT_UNKNOWN, updated_at_ms=venue.server_time_ms - 8_000,
        )
        st.create_command_intent(cmd_ep, now_ms=venue.server_time_ms - 8_000)

        facts_ok = coord.adapter.fetch_facts(ep.short_instrument, ep.long_instrument)
        facts_probed = contracts.ExchangeFacts(
            **{
                **facts_ok.__dict__,
                "order_probes": {
                    cmd_ep.cmd_id: contracts.OrderReconcileProbe(
                        cmd_id=cmd_ep.cmd_id,
                        order_state_status="NOT_QUERIED",
                        label_query_status="EMPTY_CONFIRMED",
                        inst_history_recent_complete=True,
                        inst_history_archive_complete=True,
                        matched_orders=(),
                    )
                },
            }
        )
        # Simulate 2.5s wall time passing between initial fetch (asof_ms == server_time_ms) and evaluate_step(now_ms)
        now_post_probe = facts_probed.asof_ms + 2_500
        res_probe = policy.evaluate_step(
            ep, st.get_capital_auth(), st.get_commands("ep_kb2"), (), facts_probed, now_post_probe, coord.config
        )
        assert "CLOCK_SKEW_HIGH" not in res_probe.alerts
        assert res_probe.episode_patch.entry_halted is False

        # Genuine 5s exchange clock skew STILL triggers CLOCK_SKEW_HIGH
        facts_bad_skew = contracts.ExchangeFacts(**{**facts_probed.__dict__, "server_time_ms": facts_probed.asof_ms + 5_000})
        res_skew = policy.evaluate_step(
            ep, st.get_capital_auth(), st.get_commands("ep_kb2"), (), facts_bad_skew, now_post_probe, coord.config
        )
        assert "CLOCK_SKEW_HIGH" in res_skew.alerts and res_skew.episode_patch.entry_halt_reason == "RUNTIME_ANOMALY_HALT"
    finally:
        st.close()


def test_closure_warnings_and_ops_b5_idle_stale_boundary(tmp_path: Path):
    # 1. OPS-B5: IDLE recommends 30s poll -> stale_after_ms=45s (25s fresh, 50s stale); active uses 15s (20s stale)
    venue = _setup_venue()
    st, adp, coord, dos_root = _new_coord(tmp_path / "b5", venue, budget_cap_btc="0.20", max_qty_btc="0.1")
    st_path = tmp_path / "b5_public_status.json"
    coord.status_path = st_path
    try:
        st.set_capital_policy(entry_armed=False, budget_cap_btc=D("0"), max_qty_btc=D("0.1"), now_ms=venue.server_time_ms)
        idle_payload = coord.run_tick(now_ms=venue.server_time_ms)
        assert idle_payload["telemetry"]["recommended_poll_ms"] == 30_000
        assert idle_payload["stale_after_ms"] == 45_000

        clock_box = [venue.server_time_ms + 25_000]
        wb_dir = tmp_path / "wb_b5"
        wb_dir.mkdir(parents=True, exist_ok=True)
        snap = tmp_path / "snap.jsonl"
        snap.write_text("", encoding="utf-8")
        wb = wb_mod.AstraWorkbench(
            wb_mod.WorkbenchConfig(snapshots_jsonl=snap, state_dir=wb_dir, exec_status_path=st_path, exec_stale_after_ms=15_000),
            clock_ms=lambda: clock_box[0],
        )
        v_25s = wb.execution_live_status()
        assert v_25s["stale"] is False and v_25s["health"] in {"OK", "SHADOW_IDLE"}
        clock_box[0] = venue.server_time_ms + 50_000
        v_50s = wb.execution_live_status()
        assert v_50s["stale"] is True and v_50s["health"] == "STALE"

        # 2. K-W3: startup_init(initial_entry_armed=True) does NOT override persisted disarm when auth_epoch > 0 (unless reset_grant_baseline)
        st.set_capital_policy(entry_armed=True, budget_cap_btc=D("0.1"), max_qty_btc=D("0.1"), now_ms=venue.server_time_ms)
        st.set_capital_policy(entry_armed=False, budget_cap_btc=D("0.1"), max_qty_btc=D("0.1"), now_ms=venue.server_time_ms + 10)
        coord.startup_init(initial_entry_armed=True, now_ms=venue.server_time_ms + 20)
        assert st.get_capital_auth().entry_armed is False
        coord.startup_init(initial_entry_armed=True, reset_grant_baseline=True, now_ms=venue.server_time_ms + 30)
        assert st.get_capital_auth().entry_armed is True

        # 3. K-W2: concurrent disarm during _try_admit_p5_candidate does NOT create ghost episode or consume hand_id
        _build_p5_dossier(dos_root, hand_id="hand_kw2", ep_id="ep_kw2", decided_at_ms=venue.server_time_ms)
        orig_fetch = adp.fetch_facts

        def _disarm_mid_fetch(*a: Any, **kw: Any) -> contracts.ExchangeFacts:
            coord._pending_disarm_entry += 1
            return orig_fetch(*a, **kw)

        adp.fetch_facts = _disarm_mid_fetch  # type: ignore[method-assign]
        c_map, _ = coord._scan_dossiers()
        ep_race, _ = coord._try_admit_p5_candidate(c_map, venue.server_time_ms + 100)
        coord._pending_disarm_entry -= 1
        adp.fetch_facts = orig_fetch  # type: ignore[method-assign]
        assert ep_race is None and st.has_consumed_hand("hand_kw2") is False

        # 4. K-W6 (Prior Warning 2): terminal TP sets active_limit_price_btc=None; K-W4: HTTP 408 submit -> SUBMIT_UNKNOWN
        st_ep = _drive(coord, venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=50)
        assert st_ep["stale_after_ms"] == 15_000
        assert st_ep["active_episode"]["tp"]["active_limit_price_btc"] is not None
        ep_id_active = st_ep["active_episode"]["episode_id"]
        tp_cmd = next(c for c in st.get_commands(ep_id_active) if c.purpose == "TP")
        st.update_command_state(tp_cmd.cmd_id, status=contracts.CommandStatus.CANCELLED, now_ms=venue.server_time_ms)
        ep_obj = st.get_episode(ep_id_active)
        assert ep_obj is not None
        emitted = coord._emit_status(ep_obj, adp.fetch_facts(ep_obj.short_instrument, ep_obj.long_instrument), {}, {}, writes_off=False, writes_off_reason=None, now_ms=venue.server_time_ms)
        assert emitted["active_episode"]["tp"]["active_limit_price_btc"] is None
        assert emitted["active_episode"]["tp"]["order_status"] == "CANCELLED"

        # K-W4: HTTP 408 on write raises network_unknown, placing order in SUBMIT_UNKNOWN instead of REJECTED
        def _http_408(**_kw: Any) -> Any:
            raise coord_mod.VenueTransportError("network_unknown", "http_408:Request Timeout")

        orig_submit = adp.submit_order
        adp.submit_order = _http_408  # type: ignore[method-assign]
        coord._execute_actions(
            ep_id_active,
            (contracts.PolicyAction(action_type=contracts.ActionType.SUBMIT, purpose="RX", instrument_name=ep_obj.short_instrument, direction="buy", amount=D("0.1"), limit_price=D("0.01"), time_in_force="IOC", reduce_only=True, post_only=False, reject_post_only=False, reason="test_408"),),
            adp.fetch_facts(ep_obj.short_instrument, ep_obj.long_instrument),
            venue.server_time_ms,
        )
        adp.submit_order = orig_submit  # type: ignore[method-assign]
        rx_cmds = [c for c in st.get_commands(ep_id_active) if c.purpose == "RX"]
        assert rx_cmds[-1].status == contracts.CommandStatus.SUBMIT_UNKNOWN
    finally:
        st.close()


# ---------------------------------------------------------------------------
# Final Pre-Freeze Robustness Closure: Per-Hand Isolation, No Stale Cache,
# Slow-Query Send-Boundary Gates, Mid-Entry Revoke After EP Fill, & Real P5 Contract
# ---------------------------------------------------------------------------


def _seed_historical_bad_dossier_and_note(dossier_root: Path) -> Path:
    """Create an expired historical dossier containing a NOTE with string 'unavailable' timestamps + corrupt hand."""
    old_dos = dossier_root / "dossiers" / "Deribit_BTC_20261120T080000Z"
    old_ep = old_dos / "episodes" / "episode_000000000000000000000001"
    (old_ep / "events").mkdir(parents=True, exist_ok=True)
    old_hand = old_dos / "hands" / "hand_000000000000000000000001"
    (old_hand / "tracking").mkdir(parents=True, exist_ok=True)
    (old_ep / "episode.json").write_text(
        json.dumps({"episode_id": old_ep.name, "hand_id": old_hand.name}), encoding="utf-8"
    )
    (old_ep / "condition.json").write_text(
        json.dumps(
            {
                "hand_id": old_hand.name,
                "episode_id": old_ep.name,
                "contract": {
                    "side": "put",
                    "short_instrument": "BTC-20NOV26-60000-P",
                    "long_instrument": "BTC-20NOV26-58000-P",
                    "short_strike_usd": 60000,
                    "long_strike_usd": 58000,
                    "expiry_ms": 1_795_075_200_000,
                    "quantity_btc": 1.0,
                },
            }
        ),
        encoding="utf-8",
    )
    (old_hand / "decision.json").write_text(
        json.dumps(
            {
                "attempt_id": "dec_old_001",
                "decided_at_ms": 1_795_010_000_000,
                "judgment": {"action": "Accept", "side": "put"},
                "raw_sha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    (old_hand / "tracking" / "link.json").write_text(
        json.dumps(
            {
                "state": "SHADOW_LOCK_CREATED",
                "recorded_at_ms": 1_795_010_005_000,
                "origin": {
                    "dossier_id": old_dos.name,
                    "hand_id": old_hand.name,
                    "decision_id": "dec_old_001",
                    "dossier_episode_id": old_ep.name,
                },
            }
        ),
        encoding="utf-8",
    )
    bad_note = old_ep / "events" / "ev_001_note_unavailable.json"
    bad_note.write_text(
        json.dumps(
            {
                "kind": "NOTE",
                "recorded_at_ms": "unavailable",
                "decided_at_ms": "unavailable",
                "observed_at_ms": "unavailable",
                "body": {"note": "historical_unclocked_note"},
            }
        ),
        encoding="utf-8",
    )
    # Also add a completely corrupt sibling hand in the old dossier to verify per-hand isolation
    corrupt_hand = old_dos / "hands" / "hand_000000000000000000000002"
    (corrupt_hand / "tracking").mkdir(parents=True, exist_ok=True)
    (corrupt_hand / "decision.json").write_text("{corrupt_json", encoding="utf-8")
    (corrupt_hand / "tracking" / "link.json").write_text('{"state":"SHADOW_LOCK_CREATED"}', encoding="utf-8")
    return bad_note


def test_audit_1_historical_bad_note_and_corrupt_files_never_mask_revoke_or_allow_cached_open(tmp_path: Path):
    """Audit #1 exact reproduction + degraded current-hand fail-closed controls through real ExecutionCoordinator."""
    # Scenario A: Initially unarmed coordinator scans valid Accept first, then AI_REVOKE + old NOTE("unavailable")
    # are written before arming -> MUST NOT open from stale cache!
    venue_a = _setup_venue()
    st_a, _adp_a, coord_a, dos_a = _new_coord(tmp_path / "empty_unarmed", venue_a, entry_armed=False)
    try:
        assert not hasattr(coord_a, "_cached_contracts")
        _build_p5_dossier(dos_a, decided_at_ms=venue_a.server_time_ms - 5_000)
        coord_a.run_tick(now_ms=venue_a.server_time_ms)
        assert st_a.get_active_episode() is None

        # Write valid AI_REVOKE on current hand + historical NOTE("unavailable") on expired dossier
        _write_p5_event(dos_a, kind="AI_REVOKE", ts_ms=venue_a.server_time_ms + 100)
        _seed_historical_bad_dossier_and_note(dos_a)

        venue_a.advance_time(500)
        coord_a.handle_capital_policy(
            {"entry_armed": True, "budget_cap_btc": "0.20", "max_qty_btc": "0.1", "operator": "audit_test"},
            now_ms=venue_a.server_time_ms,
            run_tick_after=True,
        )
        _drive(coord_a, venue_a, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=100)
        assert _all_episodes(st_a) == []
        assert not venue_a.get_positions()
        assert coord_a._last_reject_reason == "P5_ALREADY_REVOKED"
    finally:
        st_a.close()

    # Scenario B: Already holding 0.1 BTC spread -> write AI_REVOKE + historical NOTE("unavailable") + same-episode NOTE("unavailable")
    # -> MUST latch AI_REVOKE, unwind short + long within <=6 ticks, and settle satoshi-exact!
    venue_b = _setup_venue()
    st_b, _adp_b, coord_b, dos_b = _new_coord(tmp_path / "open_pos", venue_b, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        _build_p5_dossier(dos_b, decided_at_ms=venue_b.server_time_ms - 5_000)
        _drive(coord_b, venue_b, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=50)
        ep_open = st_b.get_latest_episode()
        assert ep_open is not None and ep_open.phase == contracts.LifecyclePhase.TP_MANAGED

        _write_p5_event(dos_b, kind="AI_REVOKE", ts_ms=venue_b.server_time_ms)
        _seed_historical_bad_dossier_and_note(dos_b)
        same_ep_events = dos_b / "dossiers" / ep_open.dossier_id / "episodes" / ep_open.p5_episode_id / "events"
        (same_ep_events / "ev_000_note_unavail.json").write_text(
            json.dumps({"kind": "NOTE", "recorded_at_ms": "unavailable", "observed_at_ms": "unavailable", "body": {}}),
            encoding="utf-8",
        )
        venue_b.set_book(
            "BTC-25NOV26-62000-P", bid="0.0080", bid_size="5.0", ask="0.0085", ask_size="5.0", mark_price="0.0083", max_price="0.05"
        )
        _drive(coord_b, venue_b, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=6, dt_ms=500)

        ep_settled = st_b.get_episode(ep_open.episode_id)
        assert ep_settled is not None and ep_settled.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
        assert ep_settled.exit_latched is True and ep_settled.exit_latch_reason == "AI_REVOKE"
        assert not venue_b.get_positions()
        oracle_b = sim.IndependentSettlementOracle(initial_balance_btc=D("1.0"), trades=venue_b.export_raw_trades())
        assert D(st_b.rebuild_episode_ledger(ep_open.episode_id)["accounting"]["episode_total_realized_pnl_btc"]) == oracle_b.expected_episode_realized_pnl_btc
    finally:
        st_b.close()

    # Scenario C: Current hand's own material unreadable -> blocks new exposure (empty & mid-entry),
    # while an already-open position still reads AI_REVOKE from episode events and settles!
    venue_c = _setup_venue()
    st_c, _adp_c, coord_c, dos_c = _new_coord(tmp_path / "corrupt_cur", venue_c, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        hand_c = _build_p5_dossier(
            dos_c, hand_id="hand_corrupt_empty", ep_id="episode_corrupt_empty", decided_at_ms=venue_c.server_time_ms - 3_000
        )
        ev_dir_c = dos_c / "dossiers" / "Deribit_BTC_20261125T080000Z" / "episodes" / "episode_corrupt_empty" / "events"
        (ev_dir_c / "00_corrupt.json").write_text("{truncated_json", encoding="utf-8")
        _drive(coord_c, venue_c, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=2, dt_ms=50)
        assert _all_episodes(st_c) == []
        assert coord_c._last_reject_reason == "P5_FACTS_UNREADABLE"

        # Repair/create clean second hand, enter TP_MANAGED, then corrupt decision.json AND write AI_REVOKE -> still exits!
        (ev_dir_c / "00_corrupt.json").unlink()
        hand_c2 = _build_p5_dossier(
            dos_c, hand_id="hand_corrupt_hold", ep_id="episode_corrupt_hold", decided_at_ms=venue_c.server_time_ms - 1_000
        )
        _drive(coord_c, venue_c, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=50)
        ep_c2 = st_c.get_latest_episode()
        assert ep_c2 is not None and ep_c2.hand_id == "hand_corrupt_hold" and ep_c2.phase == contracts.LifecyclePhase.TP_MANAGED

        (hand_c2 / "decision.json").write_text("{corrupt_decision_during_hold", encoding="utf-8")
        _write_p5_event(dos_c, ep_id="episode_corrupt_hold", kind="AI_REVOKE", ts_ms=venue_c.server_time_ms)
        venue_c.set_book(
            "BTC-25NOV26-62000-P", bid="0.0080", bid_size="5.0", ask="0.0085", ask_size="5.0", mark_price="0.0083", max_price="0.05"
        )
        _drive(coord_c, venue_c, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=6, dt_ms=500)
        ep_c2_done = st_c.get_episode(ep_c2.episode_id)
        assert ep_c2_done is not None and ep_c2_done.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
        assert ep_c2_done.exit_latch_reason == "AI_REVOKE"
    finally:
        st_c.close()


def test_audit_2_slow_query_revoke_and_180s_decision_expiry_blocked_before_first_order(tmp_path: Path):
    """Audit #2 exact reproduction + ordering controls (during account/book query, post-evaluate_step send boundary, real & synthetic clock)."""
    # 1. AI_REVOKE written BEFORE scan, DURING account/book fetch_facts, or AT _execute_actions SEND BOUNDARY -> ZERO orders!
    for mode_label in ("before_scan", "during_query", "at_send_boundary"):
        venue = _setup_venue()
        st, adp, coord, dos_root = _new_coord(tmp_path / f"rev_{mode_label}", venue, budget_cap_btc="0.20", max_qty_btc="0.1")
        try:
            _build_p5_dossier(dos_root, decided_at_ms=venue.server_time_ms - 10_000)
            if mode_label == "before_scan":
                _write_p5_event(dos_root, kind="AI_REVOKE", ts_ms=venue.server_time_ms - 1_000)
            elif mode_label == "during_query":
                orig_fetch = adp.fetch_facts
                wrote = [False]

                def _fetch_write_revoke(short_i: str, long_i: str, *, need_tx_logs: bool = True) -> contracts.ExchangeFacts:
                    if short_i and long_i and not wrote[0]:
                        wrote[0] = True
                        venue.advance_time(500)
                        _refresh_books(venue, short_i, long_i)
                        _write_p5_event(dos_root, kind="AI_REVOKE", ts_ms=venue.server_time_ms)
                    return orig_fetch(short_i, long_i, need_tx_logs=need_tx_logs)

                adp.fetch_facts = _fetch_write_revoke  # type: ignore[method-assign]
            else:
                # Hook apply_episode_patch (runs AFTER evaluate_step emits SUBMIT(EP) and IMMEDIATELY BEFORE _execute_actions!)
                orig_patch = st.apply_episode_patch
                hooked_sb = [False]

                def _patch_then_revoke(ep_id_arg: str, patch_arg: contracts.EpisodePatch, *, now_ms: int) -> None:
                    orig_patch(ep_id_arg, patch_arg, now_ms=now_ms)
                    if not hooked_sb[0]:
                        hooked_sb[0] = True
                        _write_p5_event(dos_root, kind="AI_REVOKE", ts_ms=venue.server_time_ms)

                st.apply_episode_patch = _patch_then_revoke  # type: ignore[method-assign]

            _refresh_books(venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P")
            coord.run_tick(now_ms=venue.server_time_ms)
            assert not venue.export_raw_trades()
            assert not venue.get_positions()
            if mode_label in {"before_scan", "during_query"}:
                assert _all_episodes(st) == []
                assert coord._last_reject_reason == "P5_ALREADY_REVOKED"
            else:
                ep_sb = st.get_latest_episode()
                assert ep_sb is not None and st.get_commands(ep_sb.episode_id) == []
                assert ep_sb.exit_latched is True and ep_sb.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
        finally:
            st.close()

    # 1b. Decision age crosses >180s right between evaluate_step and _execute_actions send boundary -> ZERO orders!
    t_sb0 = int(time.time() * 1000)
    exp_sb = t_sb0 + 7_200_000
    venue_sb = _setup_venue(t0=t_sb0, expiry_ms=exp_sb)
    st_sb, _adp_sb, coord_sb, dos_sb = _new_coord(tmp_path / "age_send_boundary", venue_sb, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        decided_sb = int(time.time() * 1000) - 179_850
        _build_p5_dossier(dos_sb, expiry_ms=exp_sb, decided_at_ms=decided_sb)
        orig_patch_sb = st_sb.apply_episode_patch
        slept_sb = [False]

        def _patch_then_sleep(ep_id_arg: str, patch_arg: contracts.EpisodePatch, *, now_ms: int) -> None:
            orig_patch_sb(ep_id_arg, patch_arg, now_ms=now_ms)
            if not slept_sb[0]:
                slept_sb[0] = True
                time.sleep(0.30)  # crosses 180.0s right before _execute_actions SUBMIT(EP)!

        st_sb.apply_episode_patch = _patch_then_sleep  # type: ignore[method-assign]
        _refresh_books(venue_sb, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P")
        coord_sb.run_tick()
        ep_sb_age = st_sb.get_latest_episode()
        assert ep_sb_age is not None
        assert st_sb.get_commands(ep_sb_age.episode_id) == []
        assert not venue_sb.export_raw_trades()
        assert ep_sb_age.terminal_outcome == "ABORTED_ZERO_FILL"
        assert ep_sb_age.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
    finally:
        st_sb.close()

    # 2. Exact 179.5s decision age + 2.5s slow query -> 182.05s (>180s) on REAL wall clock (now_ms=None) -> ZERO orders!
    t_wall0 = int(time.time() * 1000)
    exp_wall = t_wall0 + 7_200_000
    venue_w = _setup_venue(t0=t_wall0, expiry_ms=exp_wall)
    st_w, adp_w, coord_w, dos_w = _new_coord(tmp_path / "age_wall", venue_w, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        decided_ms = int(time.time() * 1000) - 179_500
        _build_p5_dossier(dos_w, expiry_ms=exp_wall, decided_at_ms=decided_ms)
        orig_fetch_w = adp_w.fetch_facts
        slept = [False]

        def _slow_wall_fetch(short_i: str, long_i: str, *, need_tx_logs: bool = True) -> contracts.ExchangeFacts:
            if short_i and long_i and not slept[0]:
                slept[0] = True
                time.sleep(2.55)
            t_cur = int(time.time() * 1000)
            venue_w.set_server_time(t_cur)
            if short_i and long_i:
                _refresh_books(venue_w, short_i, long_i)
            return orig_fetch_w(short_i, long_i, need_tx_logs=need_tx_logs)

        adp_w.fetch_facts = _slow_wall_fetch  # type: ignore[method-assign]
        coord_w.run_tick()  # real wall clock (now_ms=None)
        assert slept[0] is True
        assert _all_episodes(st_w) == []
        assert not venue_w.export_raw_trades()
        assert coord_w._last_reject_reason == "DECISION_TIMESTAMP_OUT_OF_WINDOW"
    finally:
        st_w.close()

    # 3. Positive control: 175.0s decision age + 2.5s slow query = 177.5s (<=180s) -> admits & enters TP_MANAGED cleanly!
    venue_ok = _setup_venue()
    st_ok, adp_ok, coord_ok, dos_ok = _new_coord(tmp_path / "age_ok", venue_ok, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        _build_p5_dossier(dos_ok, decided_at_ms=venue_ok.server_time_ms - 175_000)
        orig_fetch_ok = adp_ok.fetch_facts
        advanced_ok = [False]

        def _slow_ok_fetch(short_i: str, long_i: str, *, need_tx_logs: bool = True) -> contracts.ExchangeFacts:
            if short_i and long_i and not advanced_ok[0]:
                advanced_ok[0] = True
                venue_ok.advance_time(2_500)
                _refresh_books(venue_ok, short_i, long_i)
            return orig_fetch_ok(short_i, long_i, need_tx_logs=need_tx_logs)

        adp_ok.fetch_facts = _slow_ok_fetch  # type: ignore[method-assign]
        _drive(coord_ok, venue_ok, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=3, dt_ms=50)
        ep_ok = st_ok.get_latest_episode()
        assert ep_ok is not None and ep_ok.phase == contracts.LifecyclePhase.TP_MANAGED
        assert ep_ok.matched_qty_btc == D("0.1")
    finally:
        st_ok.close()


def test_ep_partial_or_full_fill_revoke_before_es_converges_without_duplicate_ep(tmp_path: Path):
    """Cover full/partial EP fill + AI_REVOKE before ES send (executable LX vs dust RESIDUAL_HOLD + UNKNOWN EP fill)."""
    # Case A: Full 0.1 BTC EP fills, AI_REVOKE arrives right before ES -> 1 EP, 0 ES, 0 RX, 1 LX -> EARLY_FLAT_SETTLED
    venue_a = _setup_venue()
    st_a, adp_a, coord_a, dos_a = _new_coord(tmp_path / "ep_full_rev", venue_a, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        _build_p5_dossier(dos_a, decided_at_ms=venue_a.server_time_ms - 5_000)
        orig_sub_a = adp_a.submit_order

        def _sub_ep_then_revoke(**kw: Any) -> contracts.ExchangeOrder:
            vo = orig_sub_a(**kw)
            if ":EP:" in str(kw.get("label") or ""):
                _write_p5_event(dos_a, kind="AI_REVOKE", ts_ms=venue_a.server_time_ms)
            return vo

        adp_a.submit_order = _sub_ep_then_revoke  # type: ignore[method-assign]
        _drive(coord_a, venue_a, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=100)

        ep_a = st_a.get_latest_episode()
        assert ep_a is not None and ep_a.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
        assert ep_a.terminal_outcome == "EARLY_FLAT_SETTLED"
        assert ep_a.exit_latch_reason == "AI_REVOKE"
        assert ep_a.entry_closure_reason == "PREEMPTED_BY_AI_REVOKE"
        cmds_a = st_a.get_commands(ep_a.episode_id)
        assert [c.purpose for c in cmds_a] == ["EP", "LX"]
        assert not venue_a.get_positions()
        oracle_a = sim.IndependentSettlementOracle(initial_balance_btc=D("1.0"), trades=venue_a.export_raw_trades())
        assert D(st_a.rebuild_episode_ledger(ep_a.episode_id)["accounting"]["episode_total_realized_pnl_btc"]) == oracle_a.expected_episode_realized_pnl_btc
    finally:
        st_a.close()

    # Case B: Partial 0.1 BTC EP fill (out of 0.2 BTC IOC) + AI_REVOKE before ES + dust bid (<0.0002) -> RESIDUAL_HOLD -> EXPIRY_SETTLED
    venue_b = _setup_venue()
    st_b, adp_b, coord_b, dos_b = _new_coord(
        tmp_path / "ep_part_dust", venue_b, budget_cap_btc="0.30", max_qty_btc="0.2", max_entry_slice_btc="0.2"
    )
    try:
        _build_p5_dossier(dos_b, decided_at_ms=venue_b.server_time_ms - 5_000)
        venue_b.inject_partial_fill_cap("BTC-25NOV26-60000-P", D("0.1"))
        orig_sub_b = adp_b.submit_order

        def _sub_part_ep_then_revoke_dust(**kw: Any) -> contracts.ExchangeOrder:
            vo = orig_sub_b(**kw)
            if ":EP:" in str(kw.get("label") or ""):
                venue_b.inject_partial_fill_cap("BTC-25NOV26-60000-P", None)
                venue_b.set_book("BTC-25NOV26-60000-P", bid="0.0001", bid_size="5.0", ask="0.0003", ask_size="5.0", mark_price="0.0002")
                _write_p5_event(dos_b, kind="AI_REVOKE", ts_ms=venue_b.server_time_ms)
            return vo

        adp_b.submit_order = _sub_part_ep_then_revoke_dust  # type: ignore[method-assign]
        _drive(coord_b, venue_b, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=100)
        ep_b = st_b.get_latest_episode()
        assert ep_b is not None
        assert ep_b.short_liability_cleared is True and ep_b.inventory_cleared is False
        assert ep_b.phase == contracts.LifecyclePhase.SHORT_LIABILITY_CLEARED
        cmds_b = st_b.get_commands(ep_b.episode_id)
        assert [c.purpose for c in cmds_b] == ["EP"] and cmds_b[0].filled_amount == D("0.1")

        venue_b.set_server_time(ep_b.expiry_ms + 2_000)
        venue_b.expire_at_settlement(D("59000"))  # ITM long protection payoff at expiry!
        _drive(coord_b, venue_b, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=2, dt_ms=100)
        ep_b_done = st_b.get_episode(ep_b.episode_id)
        assert ep_b_done is not None and ep_b_done.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
        assert ep_b_done.terminal_outcome == "EXPIRY_SETTLED"
        oracle_b = sim.IndependentSettlementOracle(
            initial_balance_btc=D("1.0"), trades=venue_b.export_raw_trades(), settlement_price_usd=D("59000"), settlement_period="day"
        )
        assert D(st_b.rebuild_episode_ledger(ep_b.episode_id)["accounting"]["episode_total_realized_pnl_btc"]) == oracle_b.expected_episode_realized_pnl_btc
    finally:
        st_b.close()

    # Case C: EP filled on venue with dropped submit ACK (SUBMIT_UNKNOWN) + AI_REVOKE arrives during recovery -> zero duplicate EP!
    venue_c = _setup_venue()
    st_c, _adp_c, coord_c, dos_c = _new_coord(tmp_path / "ep_unk_rev", venue_c, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        _build_p5_dossier(dos_c, decided_at_ms=venue_c.server_time_ms - 5_000)
        venue_c.inject_drop_next_submit_ack(venue_received=True)
        _refresh_books(venue_c, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P")
        coord_c.run_tick(now_ms=venue_c.server_time_ms)
        ep_c = st_c.get_latest_episode()
        assert ep_c is not None
        assert [c.status for c in st_c.get_commands(ep_c.episode_id)] == [contracts.CommandStatus.SUBMIT_UNKNOWN]

        _write_p5_event(dos_c, kind="AI_REVOKE", ts_ms=venue_c.server_time_ms + 50)
        venue_c.advance_time(100)
        _drive(coord_c, venue_c, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=100)
        ep_c_done = st_c.get_episode(ep_c.episode_id)
        assert ep_c_done is not None and ep_c_done.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
        cmds_c = st_c.get_commands(ep_c.episode_id)
        assert [c.purpose for c in cmds_c] == ["EP", "LX"]
    finally:
        st_c.close()

    # Case D: EP validly submitted at 179.5s (SUBMIT_UNKNOWN), resolves as FILLED at 181.0s (>180s) -> pairs ES cleanly within 20s rescue window!
    venue_d = _setup_venue()
    st_d, _adp_d, coord_d, dos_d = _new_coord(tmp_path / "ep_unk_181s_pair", venue_d, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        _build_p5_dossier(dos_d, decided_at_ms=venue_d.server_time_ms - 179_500)
        venue_d.inject_drop_next_submit_ack(venue_received=True)
        _refresh_books(venue_d, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P")
        coord_d.run_tick(now_ms=venue_d.server_time_ms)  # EP submitted at 179.5s, ACK dropped -> SUBMIT_UNKNOWN
        ep_d = st_d.get_latest_episode()
        assert ep_d is not None and [c.status for c in st_d.get_commands(ep_d.episode_id)] == [contracts.CommandStatus.SUBMIT_UNKNOWN]

        venue_d.advance_time(1_500)  # now 181.0s (>180s), within 20s ES rescue window!
        _drive(coord_d, venue_d, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=3, dt_ms=100)
        ep_d_done = st_d.get_episode(ep_d.episode_id)
        assert ep_d_done is not None and ep_d_done.phase == contracts.LifecyclePhase.TP_MANAGED
        assert ep_d_done.matched_qty_btc == D("0.1")
        assert [c.purpose for c in st_d.get_commands(ep_d.episode_id)] == ["EP", "ES", "TP"]
    finally:
        st_d.close()

    # Case E: Mid-ENTRY decision.json corruption right after EP fills before ES sends -> LONG_ONLY_ABORT:P5_FACTS_UNREADABLE -> LX!
    venue_e = _setup_venue()
    st_e, adp_e, coord_e, dos_e = _new_coord(tmp_path / "ep_corrupt_before_es", venue_e, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        hand_e = _build_p5_dossier(dos_e, decided_at_ms=venue_e.server_time_ms - 5_000)
        orig_sub_e = adp_e.submit_order

        def _sub_ep_then_corrupt_decision(**kw: Any) -> contracts.ExchangeOrder:
            vo = orig_sub_e(**kw)
            if ":EP:" in str(kw.get("label") or ""):
                (hand_e / "decision.json").write_bytes(b'{"judgment": "\xe4\xb8')  # truncated UTF-8!
            return vo

        adp_e.submit_order = _sub_ep_then_corrupt_decision  # type: ignore[method-assign]
        _drive(coord_e, venue_e, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=100)
        ep_e = st_e.get_latest_episode()
        assert ep_e is not None and ep_e.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
        assert ep_e.entry_closure_reason == "LONG_ONLY_ABORT:P5_FACTS_UNREADABLE"
        assert [c.purpose for c in st_e.get_commands(ep_e.episode_id)] == ["EP", "LX"]
    finally:
        st_e.close()


def test_events_utf8_truncation_and_eacces_permission_error_fail_closed_without_crashing_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify truncated UTF-8 event bytes & PermissionError on events/ scandir block new exposure yet never mask revoke or crash ticks."""
    # 1. Truncated Chinese UTF-8 in events/ev_001_bad.json + valid events/ev_002_revoke.json:
    #    - Empty position: blocked (P5_ALREADY_REVOKED / P5_FACTS_UNREADABLE, 0 orders)
    #    - Open TP_MANAGED position: does NOT crash, reads AI_REVOKE, unwinds & settles!
    venue_u = _setup_venue()
    st_u, _adp_u, coord_u, dos_u = _new_coord(tmp_path / "utf8_fault", venue_u, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        _build_p5_dossier(dos_u, decided_at_ms=venue_u.server_time_ms - 5_000)
        _drive(coord_u, venue_u, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=50)
        ep_u = st_u.get_latest_episode()
        assert ep_u is not None and ep_u.phase == contracts.LifecyclePhase.TP_MANAGED

        ev_dir_u = dos_u / "dossiers" / ep_u.dossier_id / "episodes" / ep_u.p5_episode_id / "events"
        (ev_dir_u / "ev_001_bad_utf8.json").write_bytes(b'{"kind":"NOTE","msg":"\xe4\xb8')
        _write_p5_event(dos_u, kind="AI_REVOKE", ts_ms=venue_u.server_time_ms)
        venue_u.set_book(
            "BTC-25NOV26-62000-P", bid="0.0080", bid_size="5.0", ask="0.0085", ask_size="5.0", mark_price="0.0083", max_price="0.05"
        )
        _drive(coord_u, venue_u, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=6, dt_ms=500)
        ep_u_done = st_u.get_episode(ep_u.episode_id)
        assert ep_u_done is not None and ep_u_done.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
        assert ep_u_done.exit_latch_reason == "AI_REVOKE"
        assert "P5_FACTS_UNREADABLE" in ep_u_done.alerts
    finally:
        st_u.close()

    # 2. PermissionError (EACCES) on events/ scandir: fails CLOSED on ADMIT (never silently returns []),
    #    and on an active TP_MANAGED episode emits P5_FACTS_UNREADABLE alert while TP fill + LX settlement still complete!
    venue_p = _setup_venue()
    st_p, _adp_p, coord_p, dos_p = _new_coord(tmp_path / "eacces_fault", venue_p, budget_cap_btc="0.20", max_qty_btc="0.1")
    try:
        _build_p5_dossier(
            dos_p, hand_id="hand_eacces_1", ep_id="episode_eacces_1", decided_at_ms=venue_p.server_time_ms - 3_000
        )
        blocked_ev_dir = dos_p / "dossiers" / "Deribit_BTC_20261125T080000Z" / "episodes" / "episode_eacces_1" / "events"
        orig_scandir = coord_mod.os.scandir
        block_active = [True]

        def _guarded_scandir(target_path: Any) -> Any:
            if block_active[0] and Path(target_path) == blocked_ev_dir:
                raise PermissionError(13, "Permission denied", str(target_path))
            return orig_scandir(target_path)

        monkeypatch.setattr(coord_mod.os, "scandir", _guarded_scandir)
        coord_p.run_tick(now_ms=venue_p.server_time_ms)
        assert _all_episodes(st_p) == []
        assert coord_p._last_reject_reason == "P5_FACTS_UNREADABLE"

        # Restore permission -> admits & reaches TP_MANAGED; re-inject EACCES during hold -> alerts P5_FACTS_UNREADABLE, TP + LX still settle!
        block_active[0] = False
        _drive(coord_p, venue_p, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=50)
        ep_p = st_p.get_latest_episode()
        assert ep_p is not None and ep_p.phase == contracts.LifecyclePhase.TP_MANAGED

        block_active[0] = True
        _drive(coord_p, venue_p, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=1, dt_ms=100)
        ep_p_alert = st_p.get_episode(ep_p.episode_id)
        assert ep_p_alert is not None and "P5_FACTS_UNREADABLE" in ep_p_alert.alerts
        assert ep_p_alert.exit_latched is False

        tp_cmd_p = next(c for c in st_p.get_commands(ep_p.episode_id) if c.purpose == "TP")
        venue_p.fill_resting_order(tp_cmd_p.label, D("0.1"), price=D("0.0002"), liquidity="M")
        _drive(coord_p, venue_p, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=500)
        ep_p_done = st_p.get_episode(ep_p.episode_id)
        assert ep_p_done is not None and ep_p_done.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
    finally:
        st_p.close()


def test_real_p5_latest_dossier_contract_case_file_l1_and_ai_hold_unknown_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """End-to-end contract test wiring real P5 auto_deal() case_file@0.1 output + 2c329f27 note/event shapes into ExecutionCoordinator."""
    from tests import test_player_e2e_robustness_matrix as p5_e2e

    slot_ms, expiry_ms, spot, _rows, _cd = p5_e2e._nov26_27nov26_chain()
    clock = [slot_ms + 180_000]
    wb, arch_root, dossier_root, plan_r16, _s, _exp, _wc, _sync = p5_e2e._setup_nov26_workbench_and_archive(
        tmp_path / "p5_real", monkeypatch, clock=clock
    )
    accept_put = p5_e2e._strict_p5_reply_text("## Accept / Put", short_strike=60_000, long_strike=58_000)

    with (
        p5_e2e._sse_mock_server(lambda _i, _b: {"status": 200, "text": accept_put}) as (llm_url, _calls),
        p5_e2e.serving(wb) as wb_url,
    ):
        monkeypatch.setenv("ASTRA_PLAYER_USAGE_LEDGER", str(tmp_path / "p5_usage.json"))
        svc = p5_e2e.post(wb_url, wb)
        d_p5 = p5_e2e.dossier.Dossiers(dossier_root, clock=lambda: clock[0])
        res_deal, = p5_e2e.tracking.auto_deal(
            d_p5,
            plan_r16,
            arch_root,
            svc,
            llm_config={"api_key": "test-key", "model": "deepseek-reasoner", "endpoint": llm_url},
        )
        assert res_deal["status"] == "VALID" and res_deal["decision"] == "Accept"

    dos_dir = d_p5.path(expiry_ms)
    dos_id = dos_dir.name
    hand_id = str(res_deal["hand_id"])
    hand_dir = dos_dir / "hands" / hand_id
    link_path = hand_dir / "tracking" / "link.json"
    link_raw = json.loads(link_path.read_text(encoding="utf-8"))
    assert link_raw["state"] == "SHADOW_LOCK_CREATED"
    p5_ep_id = str(link_raw["origin"]["dossier_episode_id"])
    ep_dir = dos_dir / "episodes" / p5_ep_id

    # Seed 2c329f27 issuance notes (DEAL_BLOCKED / SERVICE_BUDGET_DEFERRED) & OBSERVATION(observed_at_ms=None)
    (hand_dir / "issuance").mkdir(parents=True, exist_ok=True)
    (ep_dir / "events").mkdir(parents=True, exist_ok=True)
    (hand_dir / "issuance" / "note_001_budget_deferred.json").write_text(
        json.dumps({"kind": "SERVICE_BUDGET_DEFERRED", "recorded_at_ms": clock[0] - 1_000}), encoding="utf-8"
    )
    (ep_dir / "events" / "obs_001.json").write_text(
        json.dumps({"kind": "OBSERVATION", "recorded_at_ms": clock[0], "observed_at_ms": None, "decided_at_ms": None, "body": {}}),
        encoding="utf-8",
    )

    # Verify fallback extraction of c_decision_ref_btc from genuine case_file@0.1 layers.L1 when t0.economy omits entry split
    orig_entry_net = link_raw["t0"]["economy"].pop("net_credit_after_entry_fee_btc", None)
    assert orig_entry_net is not None
    link_path.write_text(json.dumps(link_raw, ensure_ascii=False, indent=2), encoding="utf-8")

    c_map, _ = coord_mod.scan_p5_dossier_root(dossier_root)
    assert hand_id in c_map
    p5c = c_map[hand_id]
    assert p5c.facts_readable is True
    assert p5c.c_decision_ref_btc is not None and p5c.c_decision_ref_btc > ZERO

    # Drive real ExecutionCoordinator against the genuine P5 output dossier
    cond_contract = json.loads((ep_dir / "condition.json").read_text(encoding="utf-8"))["contract"]
    short_inst = str(cond_contract["short_instrument"])
    long_inst = str(cond_contract["long_instrument"])
    venue = _setup_venue(
        t0=clock[0] + 1_000,
        expiry_ms=int(cond_contract["expiry_ms"]),
        short_inst=short_inst,
        long_inst=long_inst,
        short_strike=int(cond_contract["short_strike_usd"]),
        long_strike=int(cond_contract["long_strike_usd"]),
        option_type=str(cond_contract["side"]).lower(),
    )
    venue.set_spot_usd(str(spot))
    st = store.ExecutionStore(tmp_path / "real_p5_exec.sqlite3")
    try:
        adp = SimExchangeAdapter(venue)
        coord = coord_mod.ExecutionCoordinator(
            mode="TESTNET",
            env_tag="tn",
            subaccount_id="sub-tn-1",
            store=st,
            adapter=adp,
            status_path=tmp_path / "real_p5_status.json",
            dossier_root=dossier_root,
            config=contracts.ExecConfig(initial_qty_btc=D("0.1"), max_entry_slice_btc=D("0.1")),
        )
        coord.startup_init(now_ms=venue.server_time_ms)
        coord.handle_capital_policy(
            {"entry_armed": True, "budget_cap_btc": "0.20", "max_qty_btc": "0.1", "operator": "p5_e2e"},
            now_ms=venue.server_time_ms,
            run_tick_after=False,
        )
        _drive(coord, venue, short_inst, long_inst, n=4, dt_ms=50)
        ep_exec = st.get_latest_episode()
        assert ep_exec is not None and ep_exec.phase == contracts.LifecyclePhase.TP_MANAGED
        assert ep_exec.c_decision_ref_btc == p5c.c_decision_ref_btc

        # HOLD_REVIEW_FAILED sets hold_review_llm_unavailable=True; subsequent AI_HOLD_UNKNOWN clears it!
        fail_ts = venue.server_time_ms + 1_000
        (ep_dir / "events" / f"ev_{fail_ts}_fail.json").write_text(
            json.dumps({"kind": "HOLD_REVIEW_FAILED", "recorded_at_ms": fail_ts, "observed_at_ms": fail_ts, "decided_at_ms": None, "body": {"reason": "http_502"}}),
            encoding="utf-8",
        )
        p5_fail = coord_mod.read_hand_p5_state(coord.dossier_root, dossier_id=dos_id, hand_id=hand_id, p5_episode_id=p5_ep_id)
        assert p5_fail is not None and p5_fail.hold_review_llm_unavailable is True

        unk_ts = fail_ts + 2_000
        (ep_dir / "events" / f"ev_{unk_ts}_hold_unk.json").write_text(
            json.dumps({"kind": "AI_HOLD_UNKNOWN", "recorded_at_ms": unk_ts, "decided_at_ms": unk_ts, "observed_at_ms": unk_ts - 100, "body": {"verdict": "UNKNOWN", "shadow_action": "HOLD_UNKNOWN_OBSERVE"}}),
            encoding="utf-8",
        )
        p5_unk = coord_mod.read_hand_p5_state(coord.dossier_root, dossier_id=dos_id, hand_id=hand_id, p5_episode_id=p5_ep_id)
        assert p5_unk is not None and p5_unk.hold_review_llm_unavailable is False

        # Verified AI_REVOKE unwinds cleanly to CASHFLOW_SETTLED
        venue.advance_time(5_000)
        _write_p5_event(coord.dossier_root, dossier_id=dos_id, ep_id=p5_ep_id, kind="AI_REVOKE", ts_ms=venue.server_time_ms)  # type: ignore[arg-type]
        _drive(coord, venue, short_inst, long_inst, n=6, dt_ms=500)
        ep_final = st.get_episode(ep_exec.episode_id)
        assert ep_final is not None and ep_final.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
        assert ep_final.exit_latch_reason == "AI_REVOKE"
    finally:
        st.close()


def test_live_mainnet_mode_guards_main_account_binding_and_readonly_dcod(tmp_path: Path):
    # 1. validate_endpoint_for_mode & read-only token guards
    coord_mod.validate_endpoint_for_mode("LIVE", "https://www.deribit.com")
    with pytest.raises(coord_mod.EnvironmentGuardError):
        coord_mod.validate_endpoint_for_mode("LIVE", "https://test.deribit.com")
    with pytest.raises(coord_mod.EnvironmentGuardError):
        coord_mod.DeribitRestAdapter(
            mode="LIVE",
            base_url="https://www.deribit.com",
            read_only_access_token="ro-tok",
            subaccount_id="900001",
        )
    with pytest.raises(coord_mod.EnvironmentGuardError):
        coord_mod.DeribitRestAdapter(
            mode="SHADOW_ONLY",
            base_url="https://www.deribit.com",
            client_id="cid",
            client_secret="csec",
            read_only_access_token="ro-tok",
        )

    tok_file = tmp_path / "ro.token"
    tok_file.write_text("tok_v1\n", encoding="utf-8")
    seen_auth_headers: list[str] = []

    def _ro_http(method: str, params: dict[str, Any], *, private: bool = False) -> Any:
        del private
        if method == "public/get_time":
            return 1_795_882_000_000
        if method == "private/get_account_summary":
            base: dict[str, Any] = {
                "margin_model": "segregated_pm",
                "portfolio_margining_enabled": True,
                "balance": 0.05,
                "equity": 0.05,
                "available_funds": 0.05,
                "initial_margin": 0.0,
                "maintenance_margin": 0.0,
            }
            if params.get("extended"):
                base.update({"id": 900001, "username": "acct-main-sim", "type": "main"})
            return base
        if method == "private/get_positions":
            return []
        if method == "private/get_open_orders_by_currency":
            return []
        if method == "private/get_cancel_on_disconnect":
            return {"enabled": False, "scope": "account"}
        raise AssertionError(f"unexpected ro method {method}")

    adp_ro = coord_mod.DeribitRestAdapter(
        mode="SHADOW_ONLY",
        base_url="https://www.deribit.com",
        subaccount_id="900001",
        allow_main_account=True,
        read_only_token_file=tok_file,
        http_fn=_ro_http,
    )
    assert adp_ro._has_private_auth() is True
    assert adp_ro._allow_private_writes() is False
    assert adp_ro._ensure_auth() == "tok_v1"
    tok_file.write_text("tok_v2\n", encoding="utf-8")
    assert adp_ro.reload_readonly_token() is True
    assert adp_ro._ensure_auth() == "tok_v2"
    for write_fn in (
        lambda: adp_ro.disable_cancel_on_disconnect(),
        lambda: adp_ro.cancel_by_label("ax1:live:1:TP:0001"),
        lambda: adp_ro.submit_order(
            instrument_name="BTC-29NOV26-58000-P",
            direction="buy",
            amount=D("0.1"),
            limit_price=D("0.0010"),
            label="ax1:live:1:EP:0001",
            time_in_force="IOC",
            reduce_only=False,
            post_only=False,
            reject_post_only=False,
            valid_until_ms=1_795_882_005_000,
        ),
    ):
        with pytest.raises(coord_mod.WriteBlockedByModeError):
            write_fn()

    facts_ro = adp_ro.fetch_facts("", "", need_tx_logs=False)
    assert facts_ro.api_error is None
    assert facts_ro.portfolio_margining_enabled is True
    assert facts_ro.margin_model == "segregated_pm"
    assert adp_ro.get_cancel_on_disconnect() == {"enabled": False, "scope": "account"}
    del seen_auth_headers

    # 2. Designated main-account binding & mismatch rejection in LIVE mode
    adp_live_ok = coord_mod.DeribitRestAdapter(
        mode="LIVE",
        base_url="https://www.deribit.com",
        client_id="cid",
        client_secret="csec",
        subaccount_id="900001",
        allow_main_account=True,
        http_fn=_ro_http,
    )
    assert adp_live_ok._allow_private_writes() is True
    assert adp_live_ok.fetch_facts("", "", need_tx_logs=False).api_error is None

    adp_live_mismatch = coord_mod.DeribitRestAdapter(
        mode="LIVE",
        base_url="https://www.deribit.com",
        client_id="cid",
        client_secret="csec",
        subaccount_id="999999",
        allow_main_account=True,
        http_fn=_ro_http,
    )
    assert str(adp_live_mismatch.fetch_facts("", "", need_tx_logs=False).api_error or "").startswith("subaccount_mismatch:")

    adp_live_main_disallowed = coord_mod.DeribitRestAdapter(
        mode="LIVE",
        base_url="https://www.deribit.com",
        client_id="cid",
        client_secret="csec",
        subaccount_id="900001",
        allow_main_account=False,
        http_fn=_ro_http,
    )
    assert str(adp_live_main_disallowed.fetch_facts("", "", need_tx_logs=False).api_error or "").startswith("main_account_forbidden:")

    # 3. LIVE coordinator guards (env_tag != tn, non-empty account ID, read-only DCOD)
    st = store.ExecutionStore(tmp_path / "live_dcod.sqlite3")
    try:
        venue = _setup_venue()
        adp_sim = SimExchangeAdapter(venue)
        with pytest.raises(coord_mod.EnvironmentGuardError, match="INVALID_LIVE_ENV_TAG"):
            coord_mod.ExecutionCoordinator(
                mode="LIVE", env_tag="tn", subaccount_id="900001", store=st, adapter=adp_sim, status_path=tmp_path / "l1.json"
            )
        with pytest.raises(coord_mod.EnvironmentGuardError, match="LIVE_ACCOUNT_ID_REQUIRED"):
            coord_mod.ExecutionCoordinator(
                mode="LIVE", env_tag="live", subaccount_id="", store=st, adapter=adp_sim, status_path=tmp_path / "l2.json"
            )

        dcod_state = {"enabled": True, "scope": "account"}
        adp_sim.get_cancel_on_disconnect = lambda: dcod_state  # type: ignore[attr-defined]
        dos = tmp_path / "dos_live_dcod"
        dos.mkdir()
        _build_p5_dossier(dos, decided_at_ms=venue.server_time_ms - 5_000)

        coord = coord_mod.ExecutionCoordinator(
            mode="LIVE",
            env_tag="live",
            subaccount_id="900001",
            store=st,
            adapter=adp_sim,
            status_path=tmp_path / "live_st.json",
            dossier_root=dos,
        )
        coord.startup_init(now_ms=venue.server_time_ms)
        # LIVE must NEVER call disable_cancel_on_disconnect! When DCOD=enabled, fails closed.
        assert adp_sim.disable_cod_calls == 0
        assert coord._dcod_confirmed is False
        st.set_capital_policy(entry_armed=True, budget_cap_btc=D("0.20"), max_qty_btc=D("0.1"), now_ms=venue.server_time_ms)
        st_blk = coord.run_tick(now_ms=venue.server_time_ms)
        assert coord._last_reject_reason == "DCOD_UNCONFIRMED"
        assert "dcod_unconfirmed" in str(coord._dcod_error or "")
        assert "dcod_enabled_on_account_requires_manual_disable" in str(coord._dcod_error or "")
        assert "dcod_unconfirmed" in str(st_blk.get("last_error") or "")
        assert "dcod_enabled_on_account_requires_manual_disable" in str(st_blk.get("last_error") or "")
        assert st.get_active_episode() is None

        for incomplete in (
            {},
            {"scope": "account"},
            {"enabled": False},
            {"scope": "connection", "enabled": False},
            {"scope": "account", "enabled": 0},
            {"scope": "account", "enabled": "false"},
        ):
            dcod_state = incomplete
            venue.advance_time(6_000)
            coord.run_tick(now_ms=venue.server_time_ms)
            assert coord._dcod_confirmed is False
            assert coord._dcod_error == "dcod_unconfirmed:invalid_account_setting"
            assert st.get_active_episode() is None

        adp_dcod_nonmap = coord_mod.DeribitRestAdapter(
            mode="LIVE",
            base_url="https://www.deribit.com",
            client_id="cid",
            client_secret="csec",
            subaccount_id="900001",
            allow_main_account=True,
            http_fn=lambda m, p, **kw: False if m == "private/get_cancel_on_disconnect" else {},
        )
        with pytest.raises(coord_mod.VenueTransportError, match="dcod_invalid_response"):
            adp_dcod_nonmap.get_cancel_on_disconnect()

        # Once account DCOD is manually disabled (enabled=False), LIVE confirms read-only and admits cleanly
        dcod_state = {"enabled": False, "scope": "account"}
        venue.advance_time(6_000)
        _refresh_books(venue, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P")
        coord.run_tick(now_ms=venue.server_time_ms)
        assert adp_sim.disable_cod_calls == 0
        assert coord._dcod_confirmed is True
        assert st.get_active_episode() is not None
    finally:
        st.close()


def test_live_mainnet_write_paths_recovery_and_takeover(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # A. Normal EP -> confirmed coverage -> ES -> 85% TP -> fill -> EARLY_FLAT_SETTLED
    v1 = _setup_venue()
    v1.subaccount_id = "900001"
    st1 = store.ExecutionStore(tmp_path / "lwp1.sqlite3")
    dos1 = tmp_path / "dos1"
    dos1.mkdir()
    try:
        adp1 = SimExchangeAdapter(v1)
        adp1.get_cancel_on_disconnect = lambda: {"enabled": False, "scope": "account"}  # type: ignore[attr-defined]
        c1 = coord_mod.ExecutionCoordinator(
            mode="LIVE", env_tag="live", subaccount_id="900001", store=st1, adapter=adp1,
            status_path=tmp_path / "st1.json", dossier_root=dos1,
        )
        c1.startup_init(now_ms=v1.server_time_ms)
        c1.handle_capital_policy({"entry_armed": True, "budget_cap_btc": "0.20", "max_qty_btc": "0.1", "operator": "op"}, now_ms=v1.server_time_ms, run_tick_after=False)
        _build_p5_dossier(dos1, decided_at_ms=v1.server_time_ms - 5_000)
        _drive(c1, v1, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=50)
        ep1 = st1.get_latest_episode()
        assert ep1 is not None and ep1.phase == contracts.LifecyclePhase.TP_MANAGED
        assert adp1.disable_cod_calls == 0
        tp1 = next(c for c in st1.get_commands(ep1.episode_id) if c.purpose == "TP")
        v1.fill_resting_order(tp1.label, D("0.1"), price=D("0.0002"), liquidity="M")
        _drive(c1, v1, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=500)
        assert st1.get_episode(ep1.episode_id).terminal_outcome == "EARLY_FLAT_SETTLED"  # type: ignore[union-attr]
        # Idempotency: re-running ticks never re-admits same hand_id
        _drive(c1, v1, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=2, dt_ms=100)
        assert len(_all_episodes(st1)) == 1
    finally:
        st1.close()

    # B. Timeout UNKNOWN -> restart query-before-recover (zero duplicate submit) -> AI_REVOKE -> DISABLE_WRITES persistence
    v2 = _setup_venue()
    v2.subaccount_id = "900001"
    db2 = tmp_path / "lwp2.sqlite3"
    dos2 = tmp_path / "dos2"
    dos2.mkdir()
    _build_p5_dossier(dos2, decided_at_ms=v2.server_time_ms - 5_000)
    st2 = store.ExecutionStore(db2)
    try:
        adp2 = SimExchangeAdapter(v2)
        adp2.get_cancel_on_disconnect = lambda: {"enabled": False, "scope": "account"}  # type: ignore[attr-defined]
        c2 = coord_mod.ExecutionCoordinator(
            mode="LIVE", env_tag="live", subaccount_id="900001", store=st2, adapter=adp2,
            status_path=tmp_path / "st2.json", dossier_root=dos2,
        )
        c2.startup_init(now_ms=v2.server_time_ms)
        c2.handle_capital_policy({"entry_armed": True, "budget_cap_btc": "0.20", "max_qty_btc": "0.1", "operator": "op"}, now_ms=v2.server_time_ms, run_tick_after=False)

        orig_sub = adp2.submit_order
        injected = {"done": False}
        submitted_labels: list[str] = []

        def _timeout_after_fill(**kw: Any) -> contracts.ExchangeOrder:
            submitted_labels.append(str(kw.get("label") or ""))
            vo = orig_sub(**kw)
            if not injected["done"] and ":EP:" in str(kw.get("label") or ""):
                injected["done"] = True
                raise coord_mod.VenueTransportError("network_unknown", "simulated_write_timeout")
            return vo

        monkeypatch.setattr(adp2, "submit_order", _timeout_after_fill)
        c2.run_tick(now_ms=v2.server_time_ms)
        ep2 = st2.get_latest_episode()
        assert ep2 is not None
        ep_cmds = st2.get_commands(ep2.episode_id)
        assert any(c.purpose == "EP" and c.status == contracts.CommandStatus.SUBMIT_UNKNOWN for c in ep_cmds)
        ep_label = next(c.label for c in ep_cmds if c.purpose == "EP")
        # Single-writer lock prevents concurrent writer
        with pytest.raises(store.WriterLockError):
            store.ExecutionStore(db2)
    finally:
        st2.close()

    # Restart coordinator on same SQLite DB: queries exchange first, resolves EP without re-submitting, completes ES -> TP
    v2.advance_time(500)
    _refresh_books(v2, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P")
    st2_re = store.ExecutionStore(db2)
    try:
        adp2_re = SimExchangeAdapter(v2)
        adp2_re.get_cancel_on_disconnect = lambda: {"enabled": False, "scope": "account"}  # type: ignore[attr-defined]
        c2_re = coord_mod.ExecutionCoordinator(
            mode="LIVE", env_tag="live", subaccount_id="900001", store=st2_re, adapter=adp2_re,
            status_path=tmp_path / "st2.json", dossier_root=dos2,
        )
        c2_re.startup_init(now_ms=v2.server_time_ms)
        _drive(c2_re, v2, "BTC-25NOV26-62000-P", "BTC-25NOV26-60000-P", n=4, dt_ms=100)
        ep2_cmds = st2_re.get_commands(ep2.episode_id)
        assert len([c for c in ep2_cmds if c.purpose == "EP"]) == 1
        assert next(c for c in ep2_cmds if c.label == ep_label).status == contracts.CommandStatus.FILLED
        assert st2_re.get_episode(ep2.episode_id).phase == contracts.LifecyclePhase.TP_MANAGED  # type: ignore[union-attr]

        # Trigger AI_REVOKE -> cancels TP, then DISABLE_WRITES pauses writes & flags manual_takeover across restart
        _write_p5_event(dos2, kind="AI_REVOKE", ts_ms=v2.server_time_ms)
        dw_res = c2_re.handle_disable_writes("live_drill_stop", operator="op", now_ms=v2.server_time_ms)
        assert dw_res["writes_enabled"] is False
        assert dw_res["manual_takeover_required"] is True
        st2_re.close()

        st2_ro = store.ExecutionStore(db2)
        adp2_ro = SimExchangeAdapter(v2)
        adp2_ro.get_cancel_on_disconnect = lambda: {"enabled": False, "scope": "account"}  # type: ignore[attr-defined]
        c2_ro = coord_mod.ExecutionCoordinator(
            mode="LIVE", env_tag="live", subaccount_id="900001", store=st2_ro, adapter=adp2_ro,
            status_path=tmp_path / "st2.json", dossier_root=dos2,
        )
        c2_ro.startup_init(initial_entry_armed=True, now_ms=v2.server_time_ms)
        assert c2_ro.writes_permitted_by_mode() is False
        tick_after = c2_ro.run_tick(now_ms=v2.server_time_ms)
        assert tick_after["writes_enabled"] is False
        assert tick_after["manual_takeover_required"] is True
        assert st2_ro.get_capital_auth().entry_armed is False
        st2_ro.close()
    finally:
        try:
            st2_re.close()
        except Exception:
            pass


def _make_live_spm_wire_adapter(
    *,
    balance_btc: Decimal,
    acct_type: str = "main",
    margin_model: str = "segregated_pm",
    portfolio_margining_enabled: bool = True,
    allow_main_account: bool = True,
    mode: str = "LIVE",
    base_url: str = "https://www.deribit.com",
    subaccount_id: str = "900001",
) -> tuple[sim.SimulatedDeribitVenue, coord_mod.DeribitRestAdapter, dict[str, Any]]:
    t0 = int(time.time() * 1000)
    exp_ms = t0 + 18_000_000
    venue = sim.SimulatedDeribitVenue(
        initial_time_ms=t0,
        initial_balance_btc=balance_btc,
        subaccount_id=subaccount_id,
        portfolio_margining_enabled=portfolio_margining_enabled,
        margin_model=margin_model,
    )
    venue.set_spot_usd("59389.71")
    for inst_name, strike in (("BTC-29NOV26-58500-P", 58500), ("BTC-29NOV26-56500-P", 56500)):
        venue.add_option_instrument(
            inst_name,
            strike_usd=strike,
            option_type="put",
            expiry_ms=exp_ms,
            settlement_period="day",
            tick_size="0.0001",
            min_trade_amount="0.1",
            tick_size_steps=(("0.005", "0.0005"),),
        )

    def _set_real_books() -> None:
        venue.set_book(
            "BTC-29NOV26-58500-P",
            bid="0.0016", bid_size="27.7", ask="0.0020", ask_size="4.9",
            mark_price="0.0018", min_price="0.0001", max_price="0.028",
        )
        venue.set_book(
            "BTC-29NOV26-56500-P",
            bid="0.0003", bid_size="14.1", ask="0.0004", ask_size="5.7",
            mark_price="0.0003", min_price="0.0001", max_price="0.0175",
        )

    _set_real_books()
    wire_state: dict[str, Any] = {
        "acct_type": acct_type,
        "margin_model": margin_model,
        "pm_enabled": portfolio_margining_enabled,
        "avail_override": None,
        "ep_fill_mode": "normal",  # normal | unfilled | partial_005
        "refresh_books": _set_real_books,
    }

    def _order_to_wire(o: sim.OracleExchangeOrder | sim.OracleOrder) -> dict[str, Any]:
        return {
            "order_id": o.order_id,
            "label": o.label,
            "instrument_name": o.instrument_name,
            "direction": o.direction,
            "amount": float(o.amount),
            "filled_amount": float(o.filled_amount),
            "price": float(o.price) if o.price is not None else 0.0,
            "average_price": float(o.average_price) if o.average_price is not None else None,
            "order_state": o.order_state,
            "time_in_force": o.time_in_force,
            "reduce_only": o.reduce_only,
            "post_only": o.post_only,
            "creation_timestamp": int(getattr(o, "creation_timestamp_ms", getattr(o, "created_at_ms", t0))),
            "last_update_timestamp": int(getattr(o, "last_update_timestamp_ms", getattr(o, "last_update_ms", t0))),
        }

    def _sync_pm_margins() -> tuple[Decimal, Decimal, Decimal]:
        pos_map = {p.instrument_name: D(p.size) for p in venue.get_positions()}
        short_sz = pos_map.get("BTC-29NOV26-58500-P", ZERO)
        long_sz = pos_map.get("BTC-29NOV26-56500-P", ZERO)
        if short_sz < ZERO and long_sz > ZERO:
            im, mm = D("0.00384912"), D("0.00307930")
        elif long_sz > ZERO and short_sz == ZERO:
            im, mm = D("0.00003441"), D("0.00002753")
        elif short_sz < ZERO and long_sz == ZERO:
            im, mm = D("0.01446991"), D("0.01157593")
        else:
            im, mm = ZERO, ZERO
        eq = D(venue.cash_balance_btc) + short_sz * D("0.0018") + long_sz * D("0.0003")
        avail = wire_state["avail_override"] if wire_state["avail_override"] is not None else max(ZERO, eq - im)
        return im, mm, avail

    def _http(method: str, params: dict[str, Any], *, private: bool = False) -> Any:
        del private
        if method == "public/get_time":
            return venue.get_server_time()
        if method == "public/get_index_price":
            return {"index_price": float(venue.spot_usd)}
        if method == "public/get_instrument":
            inst = str(params["instrument_name"])
            sp = venue._specs[inst]
            return {
                "instrument_name": sp.instrument_name,
                "kind": sp.kind,
                "settlement_period": sp.settlement_period,
                "strike": float(sp.strike_usd or 0),
                "option_type": sp.option_type,
                "expiration_timestamp": sp.expiry_ms,
                "tick_size": float(sp.tick_size),
                "tick_size_steps": [{"above_price": float(x.above_price), "tick_size": float(x.tick_size)} for x in sp.tick_size_steps],
                "min_trade_amount": float(sp.min_trade_amount),
                "maker_commission": float(sp.maker_commission),
                "taker_commission": float(sp.taker_commission),
            }
        if method == "public/get_order_book":
            return venue.get_order_book(str(params["instrument_name"]), depth=int(params.get("depth", 5)))
        if method == "private/get_cancel_on_disconnect":
            return {"enabled": False, "scope": "account"}
        if method == "private/disable_cancel_on_disconnect":
            return {"ok": True}
        if method == "private/get_account_summary":
            im, mm, avail = _sync_pm_margins()
            acct = venue.get_account_summary()
            out: dict[str, Any] = {
                "balance": float(acct["balance"]),
                "equity": float(D(venue.cash_balance_btc) + sum(D(p.size) * D(p.mark_price or 0) for p in venue.get_positions())),
                "available_funds": float(avail),
                "initial_margin": float(im),
                "maintenance_margin": float(mm),
                "margin_model": wire_state["margin_model"],
                "portfolio_margining_enabled": bool(wire_state["pm_enabled"]),
                "index_price": float(venue.spot_usd),
            }
            if params.get("extended"):
                out.update({"id": int(subaccount_id), "username": "acct-main-sim", "type": wire_state["acct_type"]})
            return out
        if method == "private/get_open_orders_by_currency":
            return [_order_to_wire(o) for o in venue.get_open_orders()]
        if method == "private/get_positions":
            return [
                {
                    "instrument_name": p.instrument_name,
                    "kind": p.kind,
                    "size": float(p.size),
                    "average_price": float(p.average_price) if p.average_price is not None else None,
                    "mark_price": float(p.mark_price) if p.mark_price is not None else None,
                    "index_price": float(venue.spot_usd),
                }
                for p in venue.get_positions()
            ]
        if method == "private/get_user_trades_by_currency_and_time":
            trades = venue.build_facts("BTC-29NOV26-58500-P", "BTC-29NOV26-56500-P", follow_pagination=True).recent_trades
            return {
                "trades": [
                    {
                        "trade_id": t.trade_id,
                        "order_id": t.order_id,
                        "label": t.label,
                        "instrument_name": t.instrument_name,
                        "direction": t.direction,
                        "amount": float(t.amount),
                        "price": float(t.price),
                        "fee": float(t.fee),
                        "fee_currency": t.fee_currency,
                        "timestamp": t.timestamp_ms,
                        "liquidity": t.liquidity,
                    }
                    for t in trades
                ],
                "has_more": False,
            }
        if method == "private/get_transaction_log":
            txs = venue.build_facts("BTC-29NOV26-58500-P", "BTC-29NOV26-56500-P", follow_pagination=True).tx_logs
            return {
                "logs": [
                    {
                        "id": x.id,
                        "type": x.type,
                        "instrument_name": x.instrument_name,
                        "timestamp": x.timestamp_ms,
                        "amount": float(x.amount),
                        "price": float(x.price) if x.price is not None else None,
                        "cashflow": float(x.cashflow),
                        "change": float(x.change),
                        "commission": float(x.commission),
                        "currency": x.currency,
                        "trade_id": x.trade_id,
                        "order_id": x.order_id,
                        "settlement_price": float(x.settlement_price) if x.settlement_price is not None else None,
                        "info": dict(x.info),
                    }
                    for x in txs
                ],
                "continuation": None,
            }
        if method in {"private/buy", "private/sell"}:
            direction = "buy" if method == "private/buy" else "sell"
            inst = str(params["instrument_name"])
            amt = D(str(params["amount"]))
            px = D(str(params["price"]))
            lbl = str(params["label"])
            if ":EP:" in lbl and wire_state["ep_fill_mode"] == "unfilled":
                venue.set_book(inst, bid="0.0001", bid_size="5.0", ask="0.0050", ask_size="5.0", mark_price="0.0003", min_price="0.0001", max_price="0.0175")
            elif ":EP:" in lbl and wire_state["ep_fill_mode"] == "partial_005":
                venue.set_book(inst, bid="0.0003", bid_size="5.0", ask="0.0004", ask_size="0.05", mark_price="0.0003", min_price="0.0001", max_price="0.0175")
            tif_rev = {"good_til_cancelled": "GTC", "immediate_or_cancel": "IOC", "fill_or_kill": "FOK"}
            oo = venue.submit_order(
                inst,
                direction,
                amt,
                px,
                label=lbl,
                time_in_force=tif_rev.get(str(params["time_in_force"]), "IOC"),
                reduce_only=bool(params.get("reduce_only", False)),
                post_only=bool(params.get("post_only", False)),
                reject_post_only=bool(params.get("reject_post_only", False)),
                valid_until=int(params["valid_until"]),
            )
            tr_list = [
                {
                    "trade_id": t.trade_id, "order_id": t.order_id, "label": t.label,
                    "instrument_name": t.instrument_name, "direction": t.direction,
                    "amount": float(t.amount), "price": float(t.price), "fee": float(t.fee),
                    "fee_currency": t.fee_currency, "timestamp": t.timestamp_ms, "liquidity": t.liquidity,
                }
                for t in venue.build_facts("BTC-29NOV26-58500-P", "BTC-29NOV26-56500-P", follow_pagination=True).recent_trades
                if t.order_id == oo.order_id
            ]
            return {"order": _order_to_wire(oo), "trades": tr_list}
        if method == "private/cancel_by_label":
            return venue.cancel_by_label(str(params["label"]))
        if method == "private/get_order_state_by_label":
            return [_order_to_wire(o) for o in venue.get_order_state_by_label(str(params["label"]))]
        if method == "private/get_order_state":
            return _order_to_wire(venue.get_order_state(str(params["order_id"])))
        if method == "private/get_order_history_by_instrument":
            return [_order_to_wire(o) for o in venue.get_order_history_by_instrument(str(params["instrument_name"]))]
        raise AssertionError(f"unexpected rpc {method}")

    adp = coord_mod.DeribitRestAdapter(
        mode=mode,
        base_url=base_url,
        client_id="cid",
        client_secret="csec",
        subaccount_id=subaccount_id,
        allow_main_account=allow_main_account,
        http_fn=_http,
    )
    return venue, adp, wire_state


def test_live_mainnet_spm_single_trial_dynamic_mreq_full_cycle_and_guards(tmp_path: Path):
    def _build_nov29_dossier(dos_dir: Path, venue: sim.SimulatedDeribitVenue) -> None:
        dos_dir.mkdir(parents=True, exist_ok=True)
        _build_p5_dossier(
            dos_dir,
            dossier_id="Deribit_BTC_20261129T080000Z",
            short_inst="BTC-29NOV26-58500-P",
            long_inst="BTC-29NOV26-56500-P",
            short_strike=58500,
            long_strike=56500,
            expiry_ms=venue._specs["BTC-29NOV26-58500-P"].expiry_ms,
            decided_at_ms=venue.server_time_ms - 5_000,
            credit_btc=D("0.00095"),
        )

    # 1. Positive full cycle at 0.0065 BTC on designated main-account S:PM (29NOV26 real quotes)
    v_ok, adp_ok, ws_ok = _make_live_spm_wire_adapter(balance_btc=D("0.0065"))
    dos_ok = tmp_path / "dos_ok"
    _build_nov29_dossier(dos_ok, v_ok)
    st_ok = store.ExecutionStore(tmp_path / "ok.sqlite3")
    try:
        c_ok = coord_mod.ExecutionCoordinator(
            mode="LIVE",
            env_tag="live",
            subaccount_id="900001",
            allow_main_account=True,
            store=st_ok,
            adapter=adp_ok,
            status_path=tmp_path / "ok_st.json",
            dossier_root=dos_ok,
        )
        c_ok.startup_init(now_ms=v_ok.server_time_ms)
        st_idle = c_ok.run_tick(now_ms=v_ok.server_time_ms)
        assert st_idle["account"]["candidate_min_cash_fixed_floor_applied"] is False
        assert st_idle["account"]["candidate_min_cash_btc"] is None

        c_ok.handle_capital_policy(
            {"entry_armed": True, "budget_cap_btc": "0.0065", "max_qty_btc": "0.1", "operator": "op"},
            now_ms=v_ok.server_time_ms,
            run_tick_after=False,
        )
        ws_ok["refresh_books"]()
        st_tp = c_ok.run_tick(now_ms=v_ok.server_time_ms)
        ep_ok = st_ok.get_latest_episode()
        assert ep_ok is not None and ep_ok.phase == contracts.LifecyclePhase.TP_MANAGED
        assert ep_ok.margin_model_at_entry == "segregated_pm"
        assert st_tp["account"]["candidate_min_cash_fixed_floor_applied"] is False
        assert st_tp["account"]["candidate_min_cash_btc"] == "0.00601355"
        cmds_entry = st_ok.get_commands(ep_ok.episode_id)
        assert [c.purpose for c in cmds_entry] == ["EP", "ES", "TP"]
        assert cmds_entry[0].filled_amount == D("0.1") and cmds_entry[1].filled_amount == D("0.1")

        # Trigger AI_REVOKE -> cancels TP -> buys back ES -> sells LX -> positions flat & settled
        _write_p5_event(
            dos_ok,
            kind="AI_REVOKE",
            dossier_id="Deribit_BTC_20261129T080000Z",
            ts_ms=v_ok.server_time_ms,
        )
        for _ in range(5):
            v_ok.set_server_time(int(time.time() * 1000))
            ws_ok["refresh_books"]()
            c_ok.run_tick(now_ms=v_ok.server_time_ms)
        ep_end = st_ok.get_episode(ep_ok.episode_id)
        assert ep_end is not None and ep_end.phase == contracts.LifecyclePhase.CASHFLOW_SETTLED
        assert ep_end.terminal_outcome == "EARLY_FLAT_SETTLED"
        assert ep_end.short_liability_cleared is True and ep_end.inventory_cleared is True
        assert v_ok.get_positions() == ()
        assert [c.purpose for c in st_ok.get_commands(ep_ok.episode_id)] == ["EP", "ES", "TP", "RX", "LX"]
    finally:
        st_ok.close()

    # 2a. Negative 1: Dynamic M_req insufficient (0.0055 < 0.00606980 -> rejected at ADMIT, 0 orders)
    v_low, adp_low, _ = _make_live_spm_wire_adapter(balance_btc=D("0.0055"))
    dos_low = tmp_path / "dos_low"
    _build_nov29_dossier(dos_low, v_low)
    st_low = store.ExecutionStore(tmp_path / "low.sqlite3")
    try:
        c_low = coord_mod.ExecutionCoordinator(
            mode="LIVE", env_tag="live", subaccount_id="900001", allow_main_account=True,
            store=st_low, adapter=adp_low, status_path=tmp_path / "low_st.json", dossier_root=dos_low,
        )
        c_low.startup_init(now_ms=v_low.server_time_ms)
        c_low.handle_capital_policy(
            {"entry_armed": True, "budget_cap_btc": "0.0055", "max_qty_btc": "0.1", "operator": "op"},
            now_ms=v_low.server_time_ms,
            run_tick_after=False,
        )
        st_low_tick = c_low.run_tick(now_ms=v_low.server_time_ms)
        assert c_low._last_reject_reason == "AVAILABLE_FUNDS_BELOW_INITIAL_MARGIN_REQ"
        assert st_low.get_active_episode() is None
        assert st_low_tick["account"]["candidate_min_cash_fixed_floor_applied"] is False
        assert st_low_tick["account"]["candidate_min_cash_btc"] == "0.0060698"
        assert len(v_low.export_raw_trades()) == 0 and len(v_low.get_open_orders()) == 0
    finally:
        st_low.close()

    # 2b. Scope boundary: outside controlled scope (subaccount / cross_pm / qty=0.2 / TESTNET) retains fixed floor!
    for case_name, kw, max_qty in (
        ("subaccount", {"acct_type": "subaccount", "allow_main_account": False}, "0.1"),
        ("cross_pm", {"margin_model": "cross_pm"}, "0.1"),
        ("qty_0_2", {}, "0.2"),
        ("testnet", {"mode": "TESTNET", "base_url": "https://test.deribit.com", "acct_type": "subaccount", "allow_main_account": False}, "0.1"),
    ):
        sub_dir = tmp_path / f"scope_{case_name}"
        sub_dir.mkdir()
        bal_s = D("0.015") if max_qty == "0.2" else D("0.0065")
        v_s, adp_s, _ = _make_live_spm_wire_adapter(balance_btc=bal_s, **kw)
        dos_s = sub_dir / "dos"
        _build_nov29_dossier(dos_s, v_s)
        st_s = store.ExecutionStore(sub_dir / "s.sqlite3")
        try:
            mode_s = str(kw.get("mode", "LIVE"))
            cfg_s = contracts.ExecConfig(initial_qty_btc=D(max_qty), max_entry_slice_btc=D(max_qty))
            c_s = coord_mod.ExecutionCoordinator(
                mode=mode_s,
                env_tag="tn" if mode_s == "TESTNET" else "live",
                subaccount_id="900001",
                allow_main_account=bool(kw.get("allow_main_account", True)),
                store=st_s,
                adapter=adp_s,
                status_path=sub_dir / "st.json",
                dossier_root=dos_s,
                config=cfg_s,
            )
            c_s.startup_init(now_ms=v_s.server_time_ms)
            c_s.handle_capital_policy(
                {"entry_armed": True, "budget_cap_btc": str(bal_s), "max_qty_btc": max_qty, "operator": "op"},
                now_ms=v_s.server_time_ms,
                run_tick_after=False,
            )
            tick_s = c_s.run_tick(now_ms=v_s.server_time_ms)
            assert c_s._last_reject_reason == "BALANCE_BELOW_CANDIDATE_MIN_CASH", case_name
            assert st_s.get_active_episode() is None, case_name
            assert tick_s["account"]["candidate_min_cash_fixed_floor_applied"] is True, case_name
            expected_floor = "0.03" if max_qty == "0.2" else "0.015"
            assert tick_s["account"]["candidate_min_cash_btc"] == expected_floor, case_name
        finally:
            st_s.close()

    # 2c. Open position still unwinds cleanly even if entry M_req / available_funds would reject new entry
    v_ex, adp_ex, ws_ex = _make_live_spm_wire_adapter(balance_btc=D("0.0065"))
    dos_ex = tmp_path / "dos_ex"
    _build_nov29_dossier(dos_ex, v_ex)
    st_ex = store.ExecutionStore(tmp_path / "ex.sqlite3")
    try:
        c_ex = coord_mod.ExecutionCoordinator(
            mode="LIVE", env_tag="live", subaccount_id="900001", allow_main_account=True,
            store=st_ex, adapter=adp_ex, status_path=tmp_path / "ex_st.json", dossier_root=dos_ex,
        )
        c_ex.startup_init(now_ms=v_ex.server_time_ms)
        c_ex.handle_capital_policy(
            {"entry_armed": True, "budget_cap_btc": "0.0065", "max_qty_btc": "0.1", "operator": "op"},
            now_ms=v_ex.server_time_ms,
            run_tick_after=False,
        )
        c_ex.run_tick(now_ms=v_ex.server_time_ms)
        ep_ex = st_ex.get_latest_episode()
        assert ep_ex is not None and ep_ex.phase == contracts.LifecyclePhase.TP_MANAGED
        ws_ex["avail_override"] = D("0.0010")
        _write_p5_event(
            dos_ex,
            kind="AI_REVOKE",
            dossier_id="Deribit_BTC_20261129T080000Z",
            ts_ms=v_ex.server_time_ms,
        )
        for _ in range(5):
            v_ex.set_server_time(int(time.time() * 1000))
            ws_ex["refresh_books"]()
            c_ex.run_tick(now_ms=v_ex.server_time_ms)
        assert st_ex.get_episode(ep_ex.episode_id).phase == contracts.LifecyclePhase.CASHFLOW_SETTLED  # type: ignore[union-attr]
        assert v_ex.get_positions() == ()
    finally:
        st_ex.close()

    # 3. Negative 2: Protection leg unfilled OR insufficient partial fill (< 0.1) -> NEVER submits ES
    for fill_mode in ("unfilled", "partial_005"):
        sub_p = tmp_path / f"prot_{fill_mode}"
        sub_p.mkdir()
        v_p, adp_p, ws_p = _make_live_spm_wire_adapter(balance_btc=D("0.0065"))
        ws_p["ep_fill_mode"] = fill_mode
        dos_p = sub_p / "dos"
        _build_nov29_dossier(dos_p, v_p)
        st_p = store.ExecutionStore(sub_p / "p.sqlite3")
        try:
            c_p = coord_mod.ExecutionCoordinator(
                mode="LIVE", env_tag="live", subaccount_id="900001", allow_main_account=True,
                store=st_p, adapter=adp_p, status_path=sub_p / "p_st.json", dossier_root=dos_p,
            )
            c_p.startup_init(now_ms=v_p.server_time_ms)
            c_p.handle_capital_policy(
                {"entry_armed": True, "budget_cap_btc": "0.0065", "max_qty_btc": "0.1", "operator": "op"},
                now_ms=v_p.server_time_ms,
                run_tick_after=False,
            )
            c_p.run_tick(now_ms=v_p.server_time_ms)
            ep_p = st_p.get_latest_episode()
            assert ep_p is not None
            cmds_p = st_p.get_commands(ep_p.episode_id)
            assert len(cmds_p) >= 1 and all(c.purpose == "EP" for c in cmds_p), fill_mode
            assert all(p.size >= ZERO for p in v_p.get_positions()), fill_mode
        finally:
            st_p.close()


def test_tx_from_deribit_handles_string_dict_and_none_info() -> None:
    str_row = {
        "id": 1117464377,
        "type": "trade",
        "instrument_name": "BTC-29NOV26-59000-P",
        "timestamp": 1795888780067,
        "amount": 0.1,
        "price": 0.0031,
        "cashflow": 0.00031,
        "change": 0.00028,
        "commission": 0.00003,
        "currency": "BTC",
        "trade_id": "477972077",
        "order_id": "184914253219",
        "info": "Source: 'api:test_main'",
    }
    tx_str = coord_mod._tx_from_deribit(str_row)
    assert tx_str.id == 1117464377
    assert tx_str.type == "trade"
    assert tx_str.instrument_name == "BTC-29NOV26-59000-P"
    assert tx_str.timestamp_ms == 1795888780067
    assert tx_str.amount == D("0.1")
    assert tx_str.price == D("0.0031")
    assert tx_str.cashflow == D("0.00031")
    assert tx_str.change == D("0.00028")
    assert tx_str.commission == D("0.00003")
    assert tx_str.trade_id == "477972077"
    assert tx_str.order_id == "184914253219"
    assert tx_str.settlement_price is None
    assert tx_str.info == {}

    dict_row = {
        "id": 1117464378,
        "type": "delivery",
        "instrument_name": "BTC-29NOV26-59000-P",
        "timestamp": 1795939200000,
        "amount": 0.1,
        "price": None,
        "cashflow": 0,
        "change": 0,
        "commission": 0,
        "currency": "BTC",
        "info": {"settlement_price": "60123.45", "note": "expiry"},
    }
    tx_dict = coord_mod._tx_from_deribit(dict_row)
    assert tx_dict.settlement_price == D("60123.45")
    assert tx_dict.info["note"] == "expiry"

    none_row = {**str_row, "id": 1117464379, "info": None}
    tx_none = coord_mod._tx_from_deribit(none_row)
    assert tx_none.settlement_price is None
    assert tx_none.info == {}


