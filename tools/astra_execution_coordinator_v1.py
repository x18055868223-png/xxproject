"""Single-writer live execution coordinator, Keep-Alive Deribit adapter, P5 bridge, and admin control socket."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from decimal import Decimal
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import socketserver
import ssl
import struct
import threading
import time
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse

if __package__:
    from .astra_exec_contracts_v1 import (
        ALLOWED_MODES,
        BEHAVIOR_SPEC_VERSION,
        D,
        ONE,
        SAT,
        ZERO,
        ActionType,
        BookQuote,
        BookStatus,
        CapitalAuthorization,
        CommandStatus,
        EpisodeState,
        ExecCommand,
        ExecConfig,
        ExchangeFacts,
        ExchangeOrder,
        ExchangePosition,
        InstrumentSpec,
        LifecyclePhase,
        OrderReconcileProbe,
        P5DecisionContract,
        TxLogEntry,
        VenueTrade,
        ceil_1e8,
        compute_contract_hash,
        conservative_split_buy_outlay_btc,
        dataclass_to_jsonable,
        dec_str,
        make_order_label,
        matches_episode_future,
        opt_dec,
        quant_qty,
        sweep_ask_price,
    )
    from .astra_exec_policy_v1 import _entry_capital_and_margin_ok, evaluate_step
    from .astra_exec_store_v1 import ExecutionStore
else:
    from astra_exec_contracts_v1 import (
        ALLOWED_MODES,
        BEHAVIOR_SPEC_VERSION,
        D,
        ONE,
        SAT,
        ZERO,
        ActionType,
        BookQuote,
        BookStatus,
        CapitalAuthorization,
        CommandStatus,
        EpisodeState,
        ExecCommand,
        ExecConfig,
        ExchangeFacts,
        ExchangeOrder,
        ExchangePosition,
        InstrumentSpec,
        LifecyclePhase,
        OrderReconcileProbe,
        P5DecisionContract,
        TxLogEntry,
        VenueTrade,
        ceil_1e8,
        compute_contract_hash,
        conservative_split_buy_outlay_btc,
        dataclass_to_jsonable,
        dec_str,
        make_order_label,
        matches_episode_future,
        opt_dec,
        quant_qty,
        sweep_ask_price,
    )
    from astra_exec_policy_v1 import _entry_capital_and_margin_ok, evaluate_step
    from astra_exec_store_v1 import ExecutionStore

SCHEMA = "astra_exec_coordinator@1.2.0"
STATUS_SCHEMA = "astra_exec_status@1.0.0"
TESTNET_BASE_URL = "https://test.deribit.com"
MAINNET_BASE_URL = "https://www.deribit.com"
DEFAULT_SUBACCOUNT_CURRENCY = "BTC"
_VERIFIED_REVOKE_ACTIONS = {"REVOKE_ALIGNED", "REVOKE_DISCRETIONARY"}
_VALID_LINK_STATES = {"SHADOW_LOCK_CREATED", "ALREADY_LINKED"}
_BACKOFF_SCHEDULE_S = (2, 4, 8, 15)


class VenueTransportError(RuntimeError):
    """Raised on exchange transport / API / rate-limit failure."""

    def __init__(self, code: str | int, message: str, *, retry_after_ms: int | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = str(code)
        self.message = message
        self.retry_after_ms = retry_after_ms


class WriteBlockedByModeError(PermissionError):
    """Raised when transport write is attempted in SHADOW_ONLY, DRY_RUN, or writes_disabled."""


class EnvironmentGuardError(ValueError):
    """Raised when endpoint URL conflicts with configured execution mode (e.g. mainnet blocked)."""


class ExchangeAdapter(Protocol):
    def fetch_facts(
        self,
        short_instrument: str,
        long_instrument: str,
        *,
        need_tx_logs: bool = True,
    ) -> ExchangeFacts: ...
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
        valid_until_ms: int,
    ) -> ExchangeOrder: ...
    def cancel_by_label(self, label: str) -> int: ...
    def get_order_state_by_label(self, label: str) -> list[ExchangeOrder]: ...
    def get_server_time_ms(self) -> int: ...
    def disable_cancel_on_disconnect(self) -> bool: ...


def validate_endpoint_for_mode(mode: str, base_url: str) -> None:
    if mode not in ALLOWED_MODES:
        raise EnvironmentGuardError(f"FORBIDDEN_MODE:{mode} (allowed={ALLOWED_MODES}; mainnet excluded in pre-live)")
    parsed = urlparse(base_url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https" or not host:
        raise EnvironmentGuardError(f"INVALID_HTTPS_ENDPOINT:{base_url}")
    if mode == "TESTNET" and host != "test.deribit.com":
        raise EnvironmentGuardError(f"TESTNET_REQUIRES_TEST_DERIBIT_URL: {base_url}")
    if host == "www.deribit.com" and mode not in {"SHADOW_ONLY", "DRY_RUN"}:
        raise EnvironmentGuardError("MAINNET_WRITES_FORBIDDEN_IN_PRELIVE")


def _atomic_write_json(path: Path, payload: Mapping[str, Any], *, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    raw = (json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    with tmp.open("wb") as fh:
        fh.write(raw)
        fh.flush()
        os.fsync(fh.fileno())
    if os.name != "nt":
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def _book_from_deribit(raw: Mapping[str, Any], instrument_name: str, fetched_at_ms: int) -> BookQuote:
    raw_bids = raw.get("bids") or []
    raw_asks = raw.get("asks") or []
    bids = tuple(
        (D(lv[0]), D(lv[1]))
        for lv in raw_bids[:5]
        if isinstance(lv, (list, tuple)) and len(lv) >= 2 and lv[0] is not None and lv[1] is not None
    )
    asks = tuple(
        (D(lv[0]), D(lv[1]))
        for lv in raw_asks[:5]
        if isinstance(lv, (list, tuple)) and len(lv) >= 2 and lv[0] is not None and lv[1] is not None
    )
    bid_px = bids[0][0] if bids else None
    bid_sz = bids[0][1] if bids else ZERO
    ask_px = asks[0][0] if asks else None
    ask_sz = asks[0][1] if asks else ZERO
    status = BookStatus.OK if (bid_px is not None and ask_px is not None) else BookStatus.EMPTY_SIDE
    return BookQuote(
        instrument_name=instrument_name,
        bid_price=bid_px,
        bid_amount=bid_sz,
        ask_price=ask_px,
        ask_amount=ask_sz,
        mark_price=opt_dec(raw.get("mark_price")),
        min_price=opt_dec(raw.get("min_price")),
        max_price=opt_dec(raw.get("max_price")),
        timestamp_ms=int(raw.get("timestamp") or fetched_at_ms),
        status=status,
        fetched_at_ms=int(fetched_at_ms),
        bids=bids,
        asks=asks,
    )


def _order_from_deribit(raw: Mapping[str, Any]) -> ExchangeOrder:
    return ExchangeOrder(
        order_id=str(raw["order_id"]),
        label=str(raw.get("label") or ""),
        instrument_name=str(raw["instrument_name"]),
        direction=str(raw["direction"]).lower(),
        amount=D(raw["amount"]),
        filled_amount=D(raw.get("filled_amount", 0)),
        price=D(raw["price"]),
        average_price=opt_dec(raw.get("average_price")),
        order_state=str(raw["order_state"]).lower(),
        time_in_force=str(raw.get("time_in_force", "good_til_cancelled")),
        reduce_only=bool(raw.get("reduce_only", False)),
        post_only=bool(raw.get("post_only", False)),
        creation_timestamp_ms=int(raw.get("creation_timestamp") or 0),
        last_update_timestamp_ms=int(raw.get("last_update_timestamp") or raw.get("creation_timestamp") or 0),
    )


def _trade_from_deribit(raw: Mapping[str, Any]) -> VenueTrade:
    return VenueTrade(
        trade_id=str(raw["trade_id"]),
        order_id=str(raw["order_id"]),
        label=str(raw.get("label") or ""),
        instrument_name=str(raw["instrument_name"]),
        direction=str(raw["direction"]).lower(),
        amount=D(raw["amount"]),
        price=D(raw["price"]),
        fee=D(raw["fee"]),
        fee_currency=str(raw.get("fee_currency") or "BTC"),
        timestamp_ms=int(raw["timestamp"]),
        liquidity=str(raw.get("liquidity") or ""),
    )


def _tx_from_deribit(raw: Mapping[str, Any]) -> TxLogEntry:
    return TxLogEntry(
        id=int(raw["id"]),
        type=str(raw["type"]).lower(),
        instrument_name=str(raw.get("instrument_name") or ""),
        timestamp_ms=int(raw["timestamp"]),
        amount=D(raw.get("amount") or 0),
        price=opt_dec(raw.get("price")),
        cashflow=D(raw.get("cashflow") or 0),
        change=D(raw.get("change") or 0),
        commission=D(raw.get("commission") or 0),
        currency=str(raw.get("currency") or "BTC"),
        trade_id=raw.get("trade_id"),
        order_id=raw.get("order_id"),
        settlement_price=opt_dec(raw.get("settlement_price") or (raw.get("info") or {}).get("settlement_price")),
        info=dict(raw.get("info") or {}) if isinstance(raw.get("info"), Mapping) else {},
    )


def _parse_retry_after_ms(header_val: str | None) -> int | None:
    if not header_val:
        return None
    try:
        secs = float(header_val.strip())
        if secs > 0:
            return int(secs * 1000)
    except ValueError:
        pass
    return None


class DeribitRestAdapter:
    """Stdlib Deribit REST v2 client with persistent HTTPS Keep-Alive, read/write separation, and hard write guard."""

    def __init__(
        self,
        *,
        mode: str = "SHADOW_ONLY",
        base_url: str = TESTNET_BASE_URL,
        client_id: str | None = None,
        client_secret: str | None = None,
        subaccount_id: str = "",
        connect_timeout_s: float = 2.0,
        read_timeout_s: float = 2.5,
        write_timeout_s: float = 3.0,
        max_rtt_for_clock_ms: int = 1_500,
        timeout_s: float | None = None,
        http_fn: Any = None,
        abort_check: Callable[[], bool] | None = None,
    ) -> None:
        validate_endpoint_for_mode(mode, base_url)
        self.mode = mode
        self.base_url = base_url.rstrip("/")
        parsed = urlparse(self.base_url)
        self._host = parsed.hostname or "test.deribit.com"
        self._port = parsed.port or 443
        self._base_path = parsed.path.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.subaccount_id = subaccount_id
        self.connect_timeout_s = float(connect_timeout_s)
        self.read_timeout_s = float(timeout_s if timeout_s is not None else read_timeout_s)
        self.write_timeout_s = float(timeout_s if timeout_s is not None else write_timeout_s)
        self.max_rtt_for_clock_ms = int(max_rtt_for_clock_ms)
        self._http_fn = http_fn
        self._abort_check = abort_check
        self._ssl_ctx = ssl.create_default_context()
        self._conn: http.client.HTTPSConnection | None = None
        self._conn_last_used_mono: float = 0.0
        self._access_token: str | None = None
        self._token_expires_ms: int = 0
        self._server_offset_ms: int = 0
        self._has_server_offset: bool = False
        self._last_clock_rtt_ok: bool = True
        self.last_rpc_rtt_ms: int | None = None
        self._spec_cache: dict[str, InstrumentSpec] = {}

    def set_abort_check(self, fn: Callable[[], bool] | None) -> None:
        self._abort_check = fn

    def close(self) -> None:
        self._close_conn()

    def _close_conn(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _ensure_conn(self, *, op_timeout_s: float, refresh_if_idle_s: float | None = None) -> tuple[http.client.HTTPSConnection, bool]:
        now_mono = time.monotonic()
        if (
            self._conn is not None
            and refresh_if_idle_s is not None
            and (now_mono - self._conn_last_used_mono) > refresh_if_idle_s
        ):
            self._close_conn()
        if self._conn is None:
            conn = http.client.HTTPSConnection(
                self._host,
                port=self._port,
                timeout=self.connect_timeout_s,
                context=self._ssl_ctx,
            )
            conn.connect()
            if conn.sock is not None:
                conn.sock.settimeout(op_timeout_s)
            self._conn = conn
            self._conn_last_used_mono = time.monotonic()
            return conn, False
        if self._conn.sock is not None:
            self._conn.sock.settimeout(op_timeout_s)
        return self._conn, True

    def _allow_private_writes(self) -> bool:
        return self.mode == "TESTNET" and self._host.lower() == "test.deribit.com"

    def estimated_server_time_ms(self) -> int:
        now_ms = int(time.time() * 1000)
        return now_ms + self._server_offset_ms if self._has_server_offset else now_ms

    def _update_clock_from_envelope(self, envelope: Mapping[str, Any], t0_ms: int, t1_ms: int) -> None:
        rtt_ms = max(0, t1_ms - t0_ms)
        self.last_rpc_rtt_ms = rtt_ms
        us_out = envelope.get("usOut")
        if isinstance(us_out, int) and not isinstance(us_out, bool):
            if rtt_ms <= self.max_rtt_for_clock_ms:
                srv_ms = us_out // 1000
                midpoint_ms = (t0_ms + t1_ms) // 2
                self._server_offset_ms = srv_ms - midpoint_ms
                self._has_server_offset = True
                self._last_clock_rtt_ok = True
            else:
                self._last_clock_rtt_ok = False

    def _raw_call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        private: bool = False,
        is_write: bool = False,
    ) -> Any:
        if not is_write and self._abort_check is not None and self._abort_check():
            raise VenueTransportError("control_interrupted", "aborted_by_control_signal")

        if self._http_fn is not None:
            t0_ms = int(time.time() * 1000)
            try:
                res = self._http_fn(method, dict(params or {}), private=private)
            except HTTPError as exc:
                body_txt = exc.read().decode("utf-8", errors="replace")[:300]
                retry_ms = _parse_retry_after_ms(exc.headers.get("Retry-After") if exc.headers else None)
                if exc.code == 429:
                    raise VenueTransportError(429, f"http_429:{body_txt}", retry_after_ms=retry_ms) from exc
                raise VenueTransportError("network_unknown" if is_write else exc.code, body_txt) from exc
            except (URLError, TimeoutError, socket.timeout, OSError) as exc:
                raise VenueTransportError("network_unknown" if is_write else "network_error", str(exc)) from exc
            t1_ms = int(time.time() * 1000)
            self.last_rpc_rtt_ms = max(0, t1_ms - t0_ms)
            if isinstance(res, Mapping) and ("result" in res or "error" in res):
                self._update_clock_from_envelope(res, t0_ms, t1_ms)
                if res.get("error"):
                    err = res["error"]
                    code = err.get("code", "rpc_error")
                    msg = str(err.get("message") or err)
                    raise VenueTransportError(code, msg, retry_after_ms=None)
                return res.get("result")
            return res

        headers = {
            "Accept": "application/json",
            "Connection": "keep-alive",
        }
        if private:
            token = self._ensure_auth()
            headers["Authorization"] = f"Bearer {token}"
        qs = urlencode(
            {
                k: json.dumps(v).lower() if isinstance(v, bool) else v
                for k, v in (params or {}).items()
                if v is not None
            }
        )
        path = f"{self._base_path}/api/v2/{method}"
        if qs:
            path = f"{path}?{qs}"

        op_timeout = self.write_timeout_s if is_write else self.read_timeout_s
        max_attempts = 1 if is_write else 2

        for attempt in range(max_attempts):
            t0_ms = int(time.time() * 1000)
            reused = False
            try:
                conn, reused = self._ensure_conn(
                    op_timeout_s=op_timeout,
                    refresh_if_idle_s=20.0 if is_write else None,
                )
                conn.request("GET", path, headers=headers)
                resp = conn.getresponse()
                raw_bytes = resp.read()
                self._conn_last_used_mono = time.monotonic()
                t1_ms = int(time.time() * 1000)
            except (
                http.client.RemoteDisconnected,
                http.client.BadStatusLine,
                http.client.CannotSendRequest,
                ConnectionResetError,
                BrokenPipeError,
                EOFError,
            ) as exc:
                self._close_conn()
                # Spec 5.1: ONLY idempotent reads on a reused socket may reconnect & retry ONCE; writes NEVER retry!
                if not is_write and reused and attempt == 0:
                    continue
                raise VenueTransportError("network_unknown" if is_write else "network_error", str(exc)) from exc
            except (TimeoutError, socket.timeout, OSError, http.client.HTTPException) as exc:
                self._close_conn()
                raise VenueTransportError("network_unknown" if is_write else "network_error", str(exc)) from exc

            body_txt = raw_bytes.decode("utf-8", errors="replace")
            if resp.status >= 400:
                retry_ms = _parse_retry_after_ms(resp.getheader("Retry-After"))
                if resp.status == 429:
                    raise VenueTransportError(429, f"http_429:{body_txt[:240]}", retry_after_ms=retry_ms)
                if resp.status >= 500 and is_write:
                    raise VenueTransportError("network_unknown", f"http_{resp.status}:{body_txt[:240]}")
                try:
                    err_obj = (json.loads(body_txt).get("error") or {})
                    code = err_obj.get("code", resp.status)
                    msg = str(err_obj.get("message") or body_txt[:240])
                    raise VenueTransportError(code, msg, retry_after_ms=retry_ms if code in {429, 10028} else None)
                except (json.JSONDecodeError, AttributeError):
                    raise VenueTransportError(resp.status, body_txt[:240])

            try:
                data = json.loads(body_txt)
            except json.JSONDecodeError as exc:
                self._close_conn()
                raise VenueTransportError("network_unknown" if is_write else "invalid_json", str(exc)) from exc

            if isinstance(data, Mapping):
                self._update_clock_from_envelope(data, t0_ms, t1_ms)
                if data.get("error"):
                    err = data["error"]
                    code = err.get("code", "rpc_error")
                    msg = str(err.get("message") or err)
                    raise VenueTransportError(code, msg, retry_after_ms=None)
                return data.get("result")
            self.last_rpc_rtt_ms = max(0, t1_ms - t0_ms)
            return data

        raise VenueTransportError("network_unknown" if is_write else "network_error", "exhausted_attempts")

    def _ensure_auth(self) -> str:
        now_ms = int(time.time() * 1000)
        if self._access_token and now_ms < (self._token_expires_ms - 30_000):
            return self._access_token
        if not self.client_id or not self.client_secret:
            raise VenueTransportError("credentials_missing", "Deribit API credentials not configured")
        res = self._raw_call(
            "public/auth",
            {"grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self.client_secret},
            private=False,
            is_write=False,
        )
        self._access_token = str(res["access_token"])
        self._token_expires_ms = now_ms + int(res.get("expires_in", 900)) * 1000
        return self._access_token

    def get_server_time_ms(self) -> int:
        t0_ms = int(time.time() * 1000)
        res = int(self._raw_call("public/get_time", {}, private=False, is_write=False))
        t1_ms = int(time.time() * 1000)
        rtt_ms = max(0, t1_ms - t0_ms)
        self.last_rpc_rtt_ms = rtt_ms
        if rtt_ms <= self.max_rtt_for_clock_ms:
            self._server_offset_ms = res - ((t0_ms + t1_ms) // 2)
            self._has_server_offset = True
            self._last_clock_rtt_ok = True
        else:
            self._last_clock_rtt_ok = False
        return res

    def disable_cancel_on_disconnect(self) -> bool:
        if not self._allow_private_writes():
            raise WriteBlockedByModeError(f"disable_cancel_on_disconnect blocked in mode={self.mode}")
        self._raw_call("private/disable_cancel_on_disconnect", {"scope": "account"}, private=True, is_write=True)
        return True

    def _get_instrument_spec(self, instrument_name: str) -> InstrumentSpec:
        cached = self._spec_cache.get(instrument_name)
        if cached is not None:
            return cached
        raw = self._raw_call(
            "public/get_instrument", {"instrument_name": instrument_name}, private=False, is_write=False
        )
        spec = InstrumentSpec.from_deribit(raw)
        self._spec_cache[instrument_name] = spec
        return spec

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
        valid_until_ms: int,
    ) -> ExchangeOrder:
        if not self._allow_private_writes():
            raise WriteBlockedByModeError(f"order submit blocked in mode={self.mode}")
        tif_map = {"GTC": "good_til_cancelled", "IOC": "immediate_or_cancel", "FOK": "fill_or_kill"}
        method = "private/buy" if direction.lower() == "buy" else "private/sell"
        params = {
            "instrument_name": instrument_name,
            "amount": dec_str(amount),
            "type": "limit",
            "price": dec_str(limit_price),
            "time_in_force": tif_map.get(time_in_force.upper(), time_in_force),
            "label": label,
            "reduce_only": bool(reduce_only),
            "post_only": bool(post_only),
            "reject_post_only": bool(reject_post_only) if post_only else False,
            "valid_until": int(valid_until_ms),
        }
        res = self._raw_call(method, params, private=True, is_write=True)
        order_raw = res.get("order") if isinstance(res, Mapping) else res
        raw_trades = res.get("trades") if isinstance(res, Mapping) else None
        parsed_trades: tuple[VenueTrade, ...] = ()
        if isinstance(raw_trades, list):
            parsed_trades = tuple(
                _trade_from_deribit(t) for t in raw_trades if isinstance(t, Mapping) and t.get("trade_id")
            )
        return replace(_order_from_deribit(order_raw), trades=parsed_trades)

    def cancel_by_label(self, label: str) -> int:
        if not self._allow_private_writes():
            raise WriteBlockedByModeError(f"cancel_by_label blocked in mode={self.mode}")
        res = self._raw_call("private/cancel_by_label", {"label": label}, private=True, is_write=True)
        return int(res or 0)

    def get_order_state(self, order_id: str) -> ExchangeOrder:
        if not self.client_id or not self.client_secret:
            raise VenueTransportError("credentials_missing", "Deribit API credentials not configured")
        res = self._raw_call("private/get_order_state", {"order_id": order_id}, private=True, is_write=False)
        if not isinstance(res, Mapping) or not res.get("order_id"):
            raise VenueTransportError(10004, "order_not_found")
        return _order_from_deribit(res)

    def get_order_state_by_label(self, label: str) -> list[ExchangeOrder]:
        if not self.client_id or not self.client_secret:
            return []
        res = self._raw_call(
            "private/get_order_state_by_label",
            {"currency": "BTC", "label": label},
            private=True,
            is_write=False,
        )
        if not res:
            return []
        rows = [res] if isinstance(res, Mapping) else list(res)
        return [_order_from_deribit(x) for x in rows if isinstance(x, Mapping) and x.get("order_id")]

    def get_order_history_by_instrument(
        self,
        instrument_name: str,
        *,
        count: int = 200,
        offset: int = 0,
        include_old: bool = True,
        include_unfilled: bool = True,
        historical: bool = False,
    ) -> list[ExchangeOrder]:
        if not self.client_id or not self.client_secret:
            return []
        res = self._raw_call(
            "private/get_order_history_by_instrument",
            {
                "instrument_name": instrument_name,
                "count": int(count),
                "offset": int(offset),
                "include_old": bool(include_old),
                "include_unfilled": bool(include_unfilled),
                "historical": bool(historical),
            },
            private=True,
            is_write=False,
        )
        if not res:
            return []
        return [_order_from_deribit(x) for x in res if isinstance(x, Mapping) and x.get("order_id")]

    def fetch_facts(
        self,
        short_instrument: str = "",
        long_instrument: str = "",
        *,
        need_tx_logs: bool = True,
        episode_start_ms: int | None = None,
    ) -> ExchangeFacts:
        server_ms = self.get_server_time_ms()
        asof_ms = int(time.time() * 1000)

        short_spec = self._get_instrument_spec(short_instrument) if short_instrument else None
        long_spec = self._get_instrument_spec(long_instrument) if long_instrument else None

        short_book: BookQuote | None = None
        long_book: BookQuote | None = None
        spot_usd = ZERO

        def _fetch_books() -> None:
            nonlocal short_book, long_book, spot_usd
            if short_instrument:
                sb_raw = self._raw_call(
                    "public/get_order_book",
                    {"instrument_name": short_instrument, "depth": 5},
                    private=False,
                    is_write=False,
                )
                sb_fetched = int(time.time() * 1000)
                short_book = _book_from_deribit(sb_raw, short_instrument, sb_fetched)
                spot_usd = opt_dec(sb_raw.get("index_price")) or spot_usd
            if long_instrument:
                lb_raw = self._raw_call(
                    "public/get_order_book",
                    {"instrument_name": long_instrument, "depth": 5},
                    private=False,
                    is_write=False,
                )
                lb_fetched = int(time.time() * 1000)
                long_book = _book_from_deribit(lb_raw, long_instrument, lb_fetched)
                if spot_usd <= ZERO:
                    spot_usd = opt_dec(lb_raw.get("index_price")) or spot_usd

        if not self.client_id or not self.client_secret:
            _fetch_books()
            unauth_err = None if self.mode == "SHADOW_ONLY" else "credentials_missing"
            return ExchangeFacts(
                asof_ms=int(time.time() * 1000),
                server_time_ms=server_ms,
                spot_usd=spot_usd,
                equity_btc=ZERO,
                available_funds_btc=ZERO,
                initial_margin_btc=ZERO,
                maintenance_margin_btc=ZERO,
                positions=(),
                open_orders=(),
                open_orders_complete=self.mode == "SHADOW_ONLY",
                history_complete=self.mode == "SHADOW_ONLY",
                recent_orders_by_label={},
                recent_trades=(),
                tx_logs=(),
                tx_logs_complete=self.mode == "SHADOW_ONLY",
                short_book=short_book,
                long_book=long_book,
                short_spec=short_spec,
                long_spec=long_spec,
                subaccount_id=self.subaccount_id,
                api_error=unauth_err,
                balance_btc=ZERO,
                portfolio_margining_enabled=False,
                margin_model="SM",
                trades_complete=self.mode == "SHADOW_ONLY",
                positions_complete=self.mode == "SHADOW_ONLY",
                clock_rtt_ok=self._last_clock_rtt_ok,
            )

        sub_norm = str(self.subaccount_id or "").strip()
        if self.mode != "SHADOW_ONLY" and (not sub_norm or sub_norm == "0"):
            return ExchangeFacts(
                asof_ms=int(time.time() * 1000),
                server_time_ms=server_ms,
                spot_usd=spot_usd,
                equity_btc=ZERO,
                available_funds_btc=ZERO,
                initial_margin_btc=ZERO,
                maintenance_margin_btc=ZERO,
                positions=(),
                open_orders=(),
                open_orders_complete=False,
                history_complete=False,
                recent_orders_by_label={},
                recent_trades=(),
                tx_logs=(),
                tx_logs_complete=False,
                short_book=None,
                long_book=None,
                short_spec=short_spec,
                long_spec=long_spec,
                subaccount_id=self.subaccount_id,
                api_error="subaccount_id_required_non_shadow",
                balance_btc=ZERO,
                portfolio_margining_enabled=False,
                margin_model="SM",
                trades_complete=False,
                positions_complete=False,
                clock_rtt_ok=self._last_clock_rtt_ok,
            )

        acct = self._raw_call("private/get_account_summary", {"currency": "BTC"}, private=True, is_write=False)
        pm_enabled = bool(acct.get("portfolio_margining_enabled", False))
        margin_model = str(acct.get("margin_model") or ("PM" if pm_enabled else "SM"))
        balance_btc = D(acct.get("balance") if acct.get("balance") is not None else (acct.get("equity") or 0))
        if spot_usd <= ZERO and acct.get("index_price") is not None:
            spot_usd = opt_dec(acct.get("index_price")) or ZERO

        acct_type = str(acct.get("type") or "").strip().lower()
        acct_id_str = str(acct.get("id") or "").strip()
        if self.mode != "SHADOW_ONLY" and (acct_type == "main" or acct_id_str == "0"):
            return ExchangeFacts(
                asof_ms=int(time.time() * 1000),
                server_time_ms=server_ms,
                spot_usd=spot_usd,
                equity_btc=ZERO,
                available_funds_btc=ZERO,
                initial_margin_btc=ZERO,
                maintenance_margin_btc=ZERO,
                positions=(),
                open_orders=(),
                open_orders_complete=False,
                history_complete=False,
                recent_orders_by_label={},
                recent_trades=(),
                tx_logs=(),
                tx_logs_complete=False,
                short_book=None,
                long_book=None,
                short_spec=short_spec,
                long_spec=long_spec,
                subaccount_id=self.subaccount_id,
                api_error=f"main_account_forbidden:type={acct.get('type')}:id={acct.get('id')}",
                balance_btc=ZERO,
                portfolio_margining_enabled=pm_enabled,
                margin_model=margin_model,
                trades_complete=False,
                positions_complete=False,
                clock_rtt_ok=self._last_clock_rtt_ok,
            )

        if self.subaccount_id:
            acct_ids = {str(acct.get("id") or ""), str(acct.get("username") or "")}
            if str(self.subaccount_id) not in acct_ids:
                return ExchangeFacts(
                    asof_ms=int(time.time() * 1000),
                    server_time_ms=server_ms,
                    spot_usd=spot_usd,
                    equity_btc=ZERO,
                    available_funds_btc=ZERO,
                    initial_margin_btc=ZERO,
                    maintenance_margin_btc=ZERO,
                    positions=(),
                    open_orders=(),
                    open_orders_complete=False,
                    history_complete=False,
                    recent_orders_by_label={},
                    recent_trades=(),
                    tx_logs=(),
                    tx_logs_complete=False,
                    short_book=None,
                    long_book=None,
                    short_spec=short_spec,
                    long_spec=long_spec,
                    subaccount_id=self.subaccount_id,
                    api_error=f"subaccount_mismatch:expected={self.subaccount_id}:got={acct.get('id')}/{acct.get('username')}",
                    balance_btc=ZERO,
                    portfolio_margining_enabled=pm_enabled,
                    margin_model=margin_model,
                    trades_complete=False,
                    positions_complete=False,
                    clock_rtt_ok=self._last_clock_rtt_ok,
                )

        # Fetch open_orders before positions (reduces spurious RX on concurrent TP fill), and books last for max freshness.
        raw_open = self._raw_call(
            "private/get_open_orders_by_currency", {"currency": "BTC"}, private=True, is_write=False
        ) or []
        open_orders = tuple(_order_from_deribit(o) for o in raw_open)

        by_lbl: dict[str, list[ExchangeOrder]] = {}
        for o in open_orders:
            if o.label:
                by_lbl.setdefault(o.label, []).append(o)

        # R-01: NEVER filter get_positions by kind="option"!
        raw_pos = self._raw_call("private/get_positions", {"currency": "BTC"}, private=True, is_write=False) or []
        positions = tuple(
            ExchangePosition(
                instrument_name=str(p["instrument_name"]),
                kind=str(p.get("kind") or "option").lower(),
                size=D(p["size"]),
                average_price=opt_dec(p.get("average_price")),
                mark_price=opt_dec(p.get("mark_price")),
                index_price=opt_dec(p.get("index_price")),
            )
            for p in raw_pos
            if D(p.get("size") or 0) != ZERO
        )
        if spot_usd <= ZERO:
            for p in positions:
                if p.index_price is not None and p.index_price > ZERO:
                    spot_usd = p.index_price
                    break

        recent_trades: tuple[VenueTrade, ...] = ()
        trades_complete = True
        if short_instrument or long_instrument:
            trades_by_id: dict[str, VenueTrade] = {}
            trades_complete = False
            tr_start = (
                max(0, int(episode_start_ms) - 120_000)
                if episode_start_ms is not None and episode_start_ms > 0
                else (server_ms - 72 * 3_600_000)
            )
            tr_end = server_ms + 60_000
            for _ in range(25):
                trades_res = self._raw_call(
                    "private/get_user_trades_by_currency_and_time",
                    {
                        "currency": "BTC",
                        "start_timestamp": tr_start,
                        "end_timestamp": tr_end,
                        "count": 200,
                        "sorting": "asc",
                    },
                    private=True,
                    is_write=False,
                )
                page_trades = trades_res.get("trades") if isinstance(trades_res, Mapping) else trades_res or []
                has_more = bool(isinstance(trades_res, Mapping) and trades_res.get("has_more"))
                prev_len = len(trades_by_id)
                max_ts = tr_start
                for t in page_trades:
                    vt = _trade_from_deribit(t)
                    trades_by_id[vt.trade_id] = vt
                    if vt.timestamp_ms > max_ts:
                        max_ts = vt.timestamp_ms
                if not has_more:
                    trades_complete = True
                    break
                if len(trades_by_id) == prev_len or max_ts <= tr_start:
                    tr_start = max_ts + 1
                else:
                    tr_start = max_ts
            recent_trades = tuple(sorted(trades_by_id.values(), key=lambda x: (x.timestamp_ms, x.trade_id)))

        tx_logs: list[TxLogEntry] = []
        tx_complete = True
        if need_tx_logs and (short_instrument or long_instrument):
            end_ms = server_ms + 60_000
            start_ms = (
                max(0, int(episode_start_ms) - 120_000)
                if episode_start_ms is not None and episode_start_ms > 0
                else (server_ms - 48 * 3_600_000)
            )
            cont = None
            for _ in range(15):
                tx_res = self._raw_call(
                    "private/get_transaction_log",
                    {
                        "currency": "BTC",
                        "start_timestamp": start_ms,
                        "end_timestamp": end_ms,
                        "count": 200,
                        "continuation": cont,
                    },
                    private=True,
                    is_write=False,
                )
                rows = tx_res.get("logs") if isinstance(tx_res, Mapping) else []
                tx_logs.extend(_tx_from_deribit(x) for x in rows)
                cont = tx_res.get("continuation") if isinstance(tx_res, Mapping) else None
                if not cont:
                    break
            else:
                tx_complete = False

        _fetch_books()
        asof_done = int(time.time() * 1000)
        return ExchangeFacts(
            asof_ms=asof_done,
            server_time_ms=self.estimated_server_time_ms() if self._has_server_offset else server_ms,
            spot_usd=spot_usd,
            equity_btc=D(acct.get("equity") or 0),
            available_funds_btc=D(acct.get("available_funds") or 0),
            initial_margin_btc=D(acct.get("initial_margin") or 0),
            maintenance_margin_btc=D(acct.get("maintenance_margin") or 0),
            positions=positions,
            open_orders=open_orders,
            open_orders_complete=True,
            history_complete=trades_complete,
            recent_orders_by_label={k: tuple(v) for k, v in by_lbl.items()},
            recent_trades=recent_trades,
            tx_logs=tuple(tx_logs),
            tx_logs_complete=tx_complete,
            short_book=short_book,
            long_book=long_book,
            short_spec=short_spec,
            long_spec=long_spec,
            subaccount_id=self.subaccount_id,
            balance_btc=balance_btc,
            portfolio_margining_enabled=pm_enabled,
            margin_model=margin_model,
            trades_complete=trades_complete,
            positions_complete=True,
            clock_rtt_ok=self._last_clock_rtt_ok,
        )


def _read_json_optional(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _extract_c_decision_ref_btc(
    *,
    link: Mapping[str, Any],
    cond: Mapping[str, Any],
    hand_dir: Path,
    side: str,
) -> Decimal | None:
    """Extract per-1.0-BTC net credit after entry fee strictly for non-blocking observation (Spec 2.2.3 & Task Doc 3.3)."""
    contract_map = cond.get("contract") if isinstance(cond.get("contract"), Mapping) else {}
    t0 = link.get("t0") if isinstance(link.get("t0"), Mapping) else {}
    econ = t0.get("economy") if isinstance(t0.get("economy"), Mapping) else {}
    qty_raw = (
        (contract_map or {}).get("quantity_btc")
        or ((t0.get("contract") if isinstance(t0.get("contract"), Mapping) else {}) or {}).get("quantity_btc")
        or 1
    )
    try:
        qty_dec = D(qty_raw)
    except Exception:
        qty_dec = ONE
    if qty_dec <= ZERO:
        qty_dec = ONE

    net_entry = opt_dec((econ or {}).get("net_credit_after_entry_fee_btc"))
    if net_entry is not None:
        return (net_entry / qty_dec).quantize(SAT)

    src = _read_json_optional(hand_dir / "evidence" / "source.json")
    if isinstance(src, Mapping):
        cases = src.get("cases")
        if isinstance(cases, list):
            for case in cases:
                if not isinstance(case, Mapping):
                    continue
                c_contract = case.get("contract") if isinstance(case.get("contract"), Mapping) else {}
                c_side = str((c_contract or {}).get("side") or "").lower()
                if side and c_side and c_side != side.lower():
                    continue
                c_econ = case.get("economics") if isinstance(case.get("economics"), Mapping) else {}
                c_net = opt_dec((c_econ or {}).get("net_credit_after_entry_fee_btc"))
                if c_net is not None:
                    c_qty = opt_dec((c_contract or {}).get("quantity_btc")) or ONE
                    if c_qty <= ZERO:
                        c_qty = ONE
                    return (c_net / c_qty).quantize(SAT)
    return None


def _is_verified_revoke_event(ev: Mapping[str, Any]) -> bool:
    rbody = ev.get("body") if isinstance(ev.get("body"), Mapping) else {}
    if not isinstance(rbody, Mapping):
        return False
    act_s = str(rbody.get("shadow_action") or "")
    if act_s not in _VERIFIED_REVOKE_ACTIONS:
        return False
    if str(rbody.get("protocol_status") or "") == "DISCRETIONARY_REVOKE_UNVERIFIED":
        return False
    if str(rbody.get("discretionary_revoke_attribution") or "") == "UNVERIFIED":
        return False
    return True


def scan_p5_dossier_root(dossier_root: Path | None) -> tuple[dict[str, P5DecisionContract], dict[str, dict[str, Any]]]:
    """Read-only scanner for immutable P5 hands/episodes/events matching Deribit_BTC_* layout."""
    if dossier_root is None:
        return {}, {}
    dossiers_dir = dossier_root / "dossiers"
    if not dossiers_dir.is_dir():
        return {}, {}

    out: dict[str, P5DecisionContract] = {}
    aux: dict[str, dict[str, Any]] = {}
    for dossier_dir in sorted(dossiers_dir.iterdir()):
        if not dossier_dir.is_dir():
            continue
        hands_dir = dossier_dir / "hands"
        if not hands_dir.is_dir():
            continue
        episodes_dir = dossier_dir / "episodes"
        ep_by_hand: dict[str, Path] = {}
        if episodes_dir.is_dir():
            for ep_dir in sorted(episodes_dir.glob("episode_*")):
                ep_meta = _read_json_optional(ep_dir / "episode.json")
                if isinstance(ep_meta, dict) and ep_meta.get("hand_id"):
                    ep_by_hand[str(ep_meta["hand_id"])] = ep_dir

        for hand_dir in sorted(hands_dir.glob("hand_*")):
            if not hand_dir.is_dir():
                continue
            dec = _read_json_optional(hand_dir / "decision.json")
            if not isinstance(dec, dict):
                continue
            link = _read_json_optional(hand_dir / "tracking" / "link.json")
            if not isinstance(link, dict):
                continue
            link_state = str(link.get("state") or "")
            if link_state not in _VALID_LINK_STATES:
                continue

            origin = link.get("origin") if isinstance(link.get("origin"), Mapping) else {}
            if origin.get("dossier_id") and str(origin["dossier_id"]) != dossier_dir.name:
                continue
            if origin.get("hand_id") and str(origin["hand_id"]) != hand_dir.name:
                continue
            dec_attempt_id = str(dec.get("attempt_id") or dec.get("decision_id") or "")
            origin_dec_id = str(origin.get("decision_id") or "")
            if dec_attempt_id and origin_dec_id and dec_attempt_id != origin_dec_id:
                continue

            ep_dir = ep_by_hand.get(hand_dir.name)
            if ep_dir is None and origin.get("dossier_episode_id") and episodes_dir.is_dir():
                cand_ep = episodes_dir / str(origin["dossier_episode_id"])
                if cand_ep.is_dir():
                    ep_dir = cand_ep
            cond = _read_json_optional(ep_dir / "condition.json") if ep_dir else None
            if not isinstance(cond, dict):
                continue
            contract_map = cond.get("contract") if isinstance(cond.get("contract"), Mapping) else {}
            if not isinstance(contract_map, Mapping) or not contract_map.get("short_instrument"):
                continue

            judgment = dec.get("judgment") if isinstance(dec.get("judgment"), Mapping) else {}
            action = str(judgment.get("action") or dec.get("action") or dec.get("decision") or "")
            side = str(
                judgment.get("side")
                or dec.get("chosen_side")
                or dec.get("side")
                or contract_map.get("side")
                or ""
            ).lower()

            raw_sha = str(dec.get("raw_sha256") or "")
            cond_bytes = (ep_dir / "condition.json").read_bytes() if (ep_dir and (ep_dir / "condition.json").is_file()) else b""
            cond_sha = str(origin.get("condition_sha256") or hashlib.sha256(cond_bytes).hexdigest())
            dec_sha = str(origin.get("decision_sha256") or hashlib.sha256((hand_dir / "decision.json").read_bytes()).hexdigest())
            in_sha = str(origin.get("input_sha256") or "")
            c_ref = _extract_c_decision_ref_btc(link=link, cond=cond, hand_dir=hand_dir, side=side)
            p5_ep_id = ep_dir.name if ep_dir else ""

            revoked = False
            rev_id = None
            rev_act = None
            rev_at = None
            unverified_rev_act: str | None = None
            cond_triggered = False
            cond_triggered_at: int | None = None
            latest_valid_review_ts = 0
            latest_fail_ts = 0

            ev_dir = ep_dir / "events" if ep_dir else None
            if ev_dir and ev_dir.is_dir():
                rev_candidates = []
                unverified_revs = []
                for p in sorted(ev_dir.glob("*.json")):
                    ev = _read_json_optional(p)
                    if not isinstance(ev, dict):
                        continue
                    kind = str(ev.get("kind") or "")
                    ev_ts = int(ev.get("recorded_at_ms") or ev.get("decided_at_ms") or ev.get("observed_at_ms") or 0)
                    if kind == "AI_REVOKE":
                        if _is_verified_revoke_event(ev):
                            rev_candidates.append(ev)
                            latest_valid_review_ts = max(latest_valid_review_ts, ev_ts)
                        else:
                            unverified_revs.append(ev)
                    elif kind in {"AI_HOLD", "HOLD_REVIEW_PASSED"}:
                        latest_valid_review_ts = max(latest_valid_review_ts, ev_ts)
                    elif kind == "CONDITION_TRIGGERED":
                        cond_triggered = True
                        obs_ts = int(ev.get("observed_at_ms") or ev_ts)
                        cond_triggered_at = max(cond_triggered_at or 0, obs_ts)
                    elif kind == "HOLD_REVIEW_FAILED":
                        latest_fail_ts = max(latest_fail_ts, ev_ts)

                if rev_candidates:
                    last_rev = max(rev_candidates, key=lambda e: int(e.get("recorded_at_ms") or 0))
                    rbody = last_rev.get("body") if isinstance(last_rev.get("body"), Mapping) else {}
                    revoked = True
                    rev_id = str(rbody.get("review_id") or "")
                    rev_act = str(rbody.get("shadow_action") or "")
                    rev_at = int(last_rev.get("decided_at_ms") or last_rev.get("recorded_at_ms") or 0)
                elif unverified_revs:
                    last_u = max(unverified_revs, key=lambda e: int(e.get("recorded_at_ms") or 0))
                    ubody = last_u.get("body") if isinstance(last_u.get("body"), Mapping) else {}
                    revoked = True
                    unverified_rev_act = str(
                        ubody.get("shadow_action") or ubody.get("protocol_status") or "UNVERIFIED"
                    )

            hold_llm_unavail = bool(not (revoked and rev_act in _VERIFIED_REVOKE_ACTIONS) and latest_fail_ts > latest_valid_review_ts)
            sx_meta = _read_json_optional(hand_dir / "tracking" / "shadow_exit.json")
            out[hand_dir.name] = P5DecisionContract(
                dossier_id=dossier_dir.name,
                hand_id=hand_dir.name,
                episode_id=p5_ep_id,
                decision_id=dec_attempt_id,
                decision=action,
                side=side,
                short_instrument=str(contract_map["short_instrument"]),
                long_instrument=str(contract_map["long_instrument"]),
                short_strike_usd=D(contract_map["short_strike_usd"]),
                long_strike_usd=D(contract_map["long_strike_usd"]),
                expiry_ms=int(contract_map["expiry_ms"]),
                decided_at_ms=int(dec.get("decided_at_ms") or 0),
                link_recorded_at_ms=int(link.get("recorded_at_ms") or 0),
                link_state=link_state,
                decision_sha256=dec_sha,
                condition_sha256=cond_sha,
                input_sha256=in_sha,
                raw_sha256=raw_sha,
                c_decision_ref_btc=c_ref,
                revoked=revoked,
                revoke_review_id=rev_id,
                revoke_shadow_action=rev_act if rev_act else unverified_rev_act,
                revoke_at_ms=rev_at,
                condition_triggered=cond_triggered,
                condition_triggered_at_ms=cond_triggered_at,
                hold_review_llm_unavailable=hold_llm_unavail,
            )
            aux[hand_dir.name] = {
                "hold_review_llm_unavailable": hold_llm_unavail,
                "shadow_exit_snapshot": (sx_meta.get("snapshot") if isinstance(sx_meta, Mapping) else None),
            }
    return out, aux


class ExecutionCoordinator:
    """Single-writer orchestrator coordinating exchange facts, policy decisions, SQLite & read-only cockpit status."""

    def __init__(
        self,
        *,
        mode: str = "SHADOW_ONLY",
        env_tag: str = "tn",
        subaccount_id: str = "",
        store: ExecutionStore,
        adapter: ExchangeAdapter,
        status_path: Path,
        dossier_root: Path | None = None,
        config: ExecConfig | None = None,
    ) -> None:
        if mode not in ALLOWED_MODES:
            raise EnvironmentGuardError(f"INVALID_MODE:{mode}")
        self.mode = mode
        self.env_tag = env_tag
        self.subaccount_id = subaccount_id
        self.store = store
        self.adapter = adapter
        self.status_path = Path(status_path)
        self.dossier_root = Path(dossier_root) if dossier_root else None
        self.config = config or ExecConfig()
        self._lock = threading.RLock()
        self._ctrl_state_lock = threading.Lock()
        self._pending_control_ops: int = 0
        self._pending_exit_all: int = 0
        self._pending_disarm_entry: int = 0
        self._mem_writes_disabled: bool = bool(self.store.writes_disabled()[0])
        self._dcod_confirmed: bool = self.mode != "TESTNET"
        self._last_dcod_attempt_ms: int = 0
        self._control_interrupt = threading.Event()
        self._wake_event = threading.Event()
        if hasattr(self.adapter, "set_abort_check"):
            self.adapter.set_abort_check(self._should_abort_reads)
        self._last_facts: ExchangeFacts | None = None
        self._last_error: str | None = None
        self._last_reject_reason: str | None = None
        self._rate_limited_until_ms: int | None = None
        self._backoff_step: int = 0
        self._last_venue_poll_ms: int = 0
        self._last_tick_duration_ms: int | None = None
        self._cached_contracts: dict[str, P5DecisionContract] = {}
        self._cached_aux: dict[str, dict[str, Any]] = {}

    def _should_abort_reads(self) -> bool:
        return self._control_interrupt.is_set() or self._pending_control_ops > 0

    def _enter_control_op(
        self,
        *,
        disable_writes: bool = False,
        exit_all: bool = False,
        disarm_entry: bool = False,
    ) -> None:
        with self._ctrl_state_lock:
            self._pending_control_ops += 1
            if disable_writes:
                self._mem_writes_disabled = True
            if exit_all:
                self._pending_exit_all += 1
            if disarm_entry:
                self._pending_disarm_entry += 1
            self._control_interrupt.set()
            self._wake_event.set()

    def _leave_control_op(self, *, exit_all: bool = False, disarm_entry: bool = False) -> None:
        with self._ctrl_state_lock:
            self._pending_control_ops = max(0, self._pending_control_ops - 1)
            if exit_all:
                self._pending_exit_all = max(0, self._pending_exit_all - 1)
            if disarm_entry:
                self._pending_disarm_entry = max(0, self._pending_disarm_entry - 1)
            if self._pending_control_ops == 0:
                self._control_interrupt.clear()

    def writes_permitted_by_mode(self) -> bool:
        if self._mem_writes_disabled:
            return False
        disabled, _ = self.store.writes_disabled()
        if disabled:
            self._mem_writes_disabled = True
            return False
        return self.mode == "TESTNET"

    def _ensure_dcod_disabled(self, now_ms: int) -> None:
        if not self.writes_permitted_by_mode() or self._dcod_confirmed:
            return
        if self._last_dcod_attempt_ms > 0 and (now_ms - self._last_dcod_attempt_ms) < 5_000:
            return
        self._last_dcod_attempt_ms = now_ms
        try:
            self.adapter.disable_cancel_on_disconnect()
            self._dcod_confirmed = True
        except Exception as exc:
            self._last_error = f"dcod_unconfirmed:{exc}"

    def _next_backoff_ms(self, retry_after_ms: int | None) -> int:
        if retry_after_ms is not None and retry_after_ms > 0:
            return int(retry_after_ms)
        secs = _BACKOFF_SCHEDULE_S[min(self._backoff_step, len(_BACKOFF_SCHEDULE_S) - 1)]
        self._backoff_step = min(self._backoff_step + 1, len(_BACKOFF_SCHEDULE_S) - 1)
        return secs * 1000

    def startup_init(
        self,
        *,
        initial_entry_armed: bool | None = None,
        initial_budget_cap_btc: Decimal | None = None,
        initial_max_qty_btc: Decimal | None = None,
        reset_grant_baseline: bool = False,
        now_ms: int | None = None,
    ) -> None:
        self._mem_writes_disabled = bool(self.store.writes_disabled()[0])
        if self.writes_permitted_by_mode():
            self._last_dcod_attempt_ms = int(now_ms if now_ms is not None else time.time() * 1000)
            try:
                self.adapter.disable_cancel_on_disconnect()
                self._dcod_confirmed = True
            except Exception as exc:
                self._dcod_confirmed = False
                self._last_error = f"startup_dcod:{exc}"

        if (
            initial_entry_armed is not None
            or initial_budget_cap_btc is not None
            or initial_max_qty_btc is not None
            or reset_grant_baseline
        ):
            t_now = int(now_ms if now_ms is not None else time.time() * 1000)
            cur_auth = self.store.get_capital_auth()
            want_armed = cur_auth.entry_armed if initial_entry_armed is None else bool(initial_entry_armed)
            want_cap = cur_auth.budget_cap_btc if initial_budget_cap_btc is None else initial_budget_cap_btc
            want_qty = cur_auth.max_qty_btc if initial_max_qty_btc is None else initial_max_qty_btc
            if (
                want_armed != cur_auth.entry_armed
                or want_cap != cur_auth.budget_cap_btc
                or want_qty != cur_auth.max_qty_btc
                or reset_grant_baseline
            ):
                try:
                    self.handle_capital_policy(
                        {
                            "entry_armed": want_armed,
                            "budget_cap_btc": dec_str(want_cap),
                            "max_qty_btc": dec_str(want_qty),
                            "reset_grant_baseline": reset_grant_baseline,
                            "operator": "startup_env",
                            "note": "startup_config_sync",
                        },
                        now_ms=now_ms,
                        run_tick_after=False,
                    )
                except Exception as exc:
                    # Fail-closed: if startup arming fails funds/mode check, force entry_armed=False!
                    self._last_error = f"startup_capital_policy_rejected:{exc}"
                    self._last_reject_reason = str(exc)
                    if cur_auth.entry_armed:
                        self.store.set_capital_policy(
                            entry_armed=False,
                            operator="startup_guard",
                            note=f"disarmed_on_startup_error:{exc}",
                            now_ms=t_now,
                        )

    def recommended_poll_ms(self, ep: EpisodeState | None, cmds: Sequence[ExecCommand]) -> int:
        if ep is None or ep.cashflow_settled:
            return 30_000
        if any(c.is_blocking_unknown for c in cmds) or ep.phase in {LifecyclePhase.ENTRY, LifecyclePhase.EXIT_LATCHED} or ep.exit_latched:
            return 1_000
        return 5_000

    def _scan_dossiers(self) -> tuple[dict[str, P5DecisionContract], dict[str, dict[str, Any]]]:
        try:
            c_map, aux = scan_p5_dossier_root(self.dossier_root)
            self._cached_contracts = c_map
            self._cached_aux = aux
        except Exception as exc:
            self._last_error = f"dossier_scan_nonfatal:{exc}"
        return self._cached_contracts, self._cached_aux

    def _estimated_server_ms(self, facts: ExchangeFacts, now_ms: int) -> int:
        if hasattr(self.adapter, "estimated_server_time_ms") and isinstance(self.adapter, DeribitRestAdapter):
            if self.adapter._has_server_offset:
                return self.adapter.estimated_server_time_ms()
        if facts.server_time_ms > 0 and facts.asof_ms > 0:
            return facts.server_time_ms + max(0, now_ms - facts.asof_ms)
        return facts.server_time_ms if facts.server_time_ms > 0 else now_ms

    def _probe_missing_commands(
        self,
        ep: EpisodeState,
        cmds: Sequence[ExecCommand],
        facts: ExchangeFacts,
        now_ms: int,
    ) -> ExchangeFacts:
        """Targeted complete-evidence order probe ONLY when an open/unknown command is absent from open_orders."""
        if not facts.open_orders_complete:
            return facts
        open_oids = {o.order_id for o in facts.open_orders}
        open_lbls = {o.label for o in facts.open_orders if o.label}
        missing = [
            c
            for c in cmds
            if c.is_open_or_unknown
            and not ((c.venue_order_id and c.venue_order_id in open_oids) or (c.label and c.label in open_lbls))
        ]
        if not missing:
            return facts

        probes = dict(facts.order_probes)
        fresh_srv_ms: int | None = None
        clock_rtt_ok = facts.clock_rtt_ok
        try:
            fresh_srv_ms = self.adapter.get_server_time_ms()
            if isinstance(self.adapter, DeribitRestAdapter):
                clock_rtt_ok = self.adapter._last_clock_rtt_ok
        except Exception:
            fresh_srv_ms = None
            clock_rtt_ok = False

        for cmd in missing:
            if self._control_interrupt.is_set():
                break
            cmd_age_ms = max(0, now_ms - cmd.created_at_ms)
            matched_by_oid: dict[str, ExchangeOrder] = {}
            order_state_status = "NOT_QUERIED"
            label_query_status = "NOT_QUERIED"
            endpoint_contradiction = False

            # 1. Known venue_order_id -> private/get_order_state
            if cmd.venue_order_id and hasattr(self.adapter, "get_order_state"):
                try:
                    vo = self.adapter.get_order_state(cmd.venue_order_id)  # type: ignore[attr-defined]
                    order_state_status = "FOUND"
                    matched_by_oid[vo.order_id] = vo
                    if vo.order_state == "open":
                        # Contradicts absence from open_orders snapshot!
                        endpoint_contradiction = True
                except VenueTransportError as exc:
                    if exc.code in {"10004", "11000", "not_found", "order_not_found"}:
                        order_state_status = "NOT_FOUND"
                    else:
                        order_state_status = "ERROR"
                        if exc.code in {"429", "10028"}:
                            self._rate_limited_until_ms = now_ms + self._next_backoff_ms(exc.retry_after_ms)
                except Exception as exc:
                    err_s = str(exc).lower()
                    if "10004" in err_s or "11000" in err_s or "not_found" in err_s:
                        order_state_status = "NOT_FOUND"
                    else:
                        order_state_status = "ERROR"

            # 2. Label hot-cache query (< 25m) vs explicit EXPIRED_HOT_CACHE (>= 25m)
            if order_state_status != "FOUND":
                if cmd_age_ms < self.config.label_hot_cache_limit_ms:
                    try:
                        found_lbl = self.adapter.get_order_state_by_label(cmd.label)
                        if found_lbl:
                            label_query_status = "FOUND"
                            for o in found_lbl:
                                matched_by_oid[o.order_id] = o
                                if o.order_state == "open":
                                    endpoint_contradiction = True
                        else:
                            label_query_status = "EMPTY_CONFIRMED"
                    except VenueTransportError as exc:
                        if exc.code in {"10004", "11000", "not_found", "order_not_found"}:
                            label_query_status = "EMPTY_CONFIRMED"
                        else:
                            label_query_status = "ERROR"
                            if exc.code in {"429", "10028"}:
                                self._rate_limited_until_ms = now_ms + self._next_backoff_ms(exc.retry_after_ms)
                    except Exception:
                        label_query_status = "ERROR"
                else:
                    label_query_status = "EXPIRED_HOT_CACHE"

            # 3. Paginated private/get_order_history_by_instrument (recent + archive when >= 20m or hot-cache miss)
            inst_recent_complete = False
            inst_archive_complete = False
            need_inst_hist = (order_state_status != "FOUND") or (cmd.venue_order_id is None)
            if need_inst_hist:
                if hasattr(self.adapter, "get_order_history_by_instrument"):
                    offset = 0
                    for _ in range(self.config.max_history_pages):
                        if self._control_interrupt.is_set():
                            break
                        try:
                            page = self.adapter.get_order_history_by_instrument(  # type: ignore[attr-defined]
                                cmd.instrument_name,
                                count=200,
                                offset=offset,
                                include_old=True,
                                include_unfilled=True,
                                historical=False,
                            )
                        except VenueTransportError as exc:
                            if exc.code in {"429", "10028"}:
                                self._rate_limited_until_ms = now_ms + self._next_backoff_ms(exc.retry_after_ms)
                            break
                        except Exception:
                            break
                        for o in page:
                            if (cmd.venue_order_id and o.order_id == cmd.venue_order_id) or (
                                o.label and o.label == cmd.label
                            ):
                                if o.order_id in matched_by_oid and matched_by_oid[o.order_id].order_state != o.order_state:
                                    endpoint_contradiction = True
                                matched_by_oid[o.order_id] = o
                        if cmd.venue_order_id is not None and cmd.venue_order_id in matched_by_oid:
                            inst_recent_complete = True
                            break
                        if len(page) < 200:
                            inst_recent_complete = True
                            break
                        offset += len(page)

                    need_archive = (
                        cmd_age_ms >= self.config.archive_history_threshold_ms
                        or order_state_status == "NOT_FOUND"
                    ) and not (cmd.venue_order_id is not None and cmd.venue_order_id in matched_by_oid)
                    if need_archive:
                        offset = 0
                        for _ in range(self.config.max_history_pages):
                            if self._control_interrupt.is_set():
                                break
                            try:
                                page = self.adapter.get_order_history_by_instrument(  # type: ignore[attr-defined]
                                    cmd.instrument_name,
                                    count=200,
                                    offset=offset,
                                    include_old=True,
                                    include_unfilled=True,
                                    historical=True,
                                )
                            except VenueTransportError as exc:
                                if exc.code in {"429", "10028"}:
                                    self._rate_limited_until_ms = now_ms + self._next_backoff_ms(exc.retry_after_ms)
                                break
                            except Exception:
                                break
                            for o in page:
                                if (cmd.venue_order_id and o.order_id == cmd.venue_order_id) or (
                                    o.label and o.label == cmd.label
                                ):
                                    if o.order_id in matched_by_oid and matched_by_oid[o.order_id].order_state != o.order_state:
                                        endpoint_contradiction = True
                                    matched_by_oid[o.order_id] = o
                            if cmd.venue_order_id is not None and cmd.venue_order_id in matched_by_oid:
                                inst_archive_complete = True
                                break
                            if len(page) < 200:
                                inst_archive_complete = True
                                break
                            offset += len(page)
                else:
                    # Fallback if a minimal test adapter only populates facts.recent_orders_by_label
                    for o in facts.recent_orders_by_label.get(cmd.label, ()):
                        matched_by_oid[o.order_id] = o
                    inst_recent_complete = bool(facts.history_complete)
                    inst_archive_complete = bool(facts.history_complete)

            dup_label = len(matched_by_oid) > 1 or (
                cmd.venue_order_id is not None and any(oid != cmd.venue_order_id for oid in matched_by_oid)
            )
            probes[cmd.cmd_id] = OrderReconcileProbe(
                cmd_id=cmd.cmd_id,
                order_state_status=order_state_status,
                label_query_status=label_query_status,
                inst_history_recent_complete=inst_recent_complete,
                inst_history_archive_complete=inst_archive_complete,
                matched_orders=tuple(matched_by_oid.values()),
                duplicate_label_conflict=dup_label,
                endpoint_contradiction=endpoint_contradiction,
                fresh_est_server_ms=fresh_srv_ms,
            )

        return replace(facts, clock_rtt_ok=clock_rtt_ok, order_probes=probes)

    def _execute_actions(
        self,
        ep_id: str,
        actions: Sequence[Any],
        facts: ExchangeFacts,
        now_ms: int,
        *,
        from_control: bool = False,
    ) -> bool:
        did_sync_venue_op = False
        if not self.writes_permitted_by_mode():
            return False

        for act in actions:
            if not self.writes_permitted_by_mode():
                break
            if act.action_type == ActionType.SUBMIT:
                # Task Doc 3.2: Re-verify authorization, latches, coverage, and expiry boundary at final send boundary!
                ep_cur = self.store.get_episode(ep_id)
                if ep_cur is None or not self.writes_permitted_by_mode():
                    break
                auth_cur = self.store.get_capital_auth()
                purpose = act.purpose or "EP"
                pending_halt = (not from_control) and (self._pending_control_ops > 0 or self._control_interrupt.is_set())
                if purpose in {"EP", "ES"}:
                    if (
                        pending_halt
                        or self._pending_disarm_entry > 0
                        or self._pending_exit_all > 0
                        or not auth_cur.entry_armed
                        or ep_cur.entry_halted
                        or ep_cur.exit_latched
                        or ep_cur.entry_closed
                    ):
                        continue
                elif purpose == "TP":
                    if self._pending_exit_all > 0 or ep_cur.exit_latched:
                        continue
                elif purpose == "LX":
                    if (self._pending_exit_all > 0 or ep_cur.exit_latched) and not ep_cur.short_liability_cleared:
                        continue

                cmds_cur = self.store.get_commands(ep_id)
                short_pos = sum(
                    (p.size for p in facts.positions if p.instrument_name == ep_cur.short_instrument), ZERO
                )
                prot_pos = sum(
                    (p.size for p in facts.positions if p.instrument_name == ep_cur.long_instrument), ZERO
                )
                short_qty = max(ZERO, -short_pos)
                pending_es = sum((c.reserved_remaining for c in cmds_cur if c.purpose == "ES" and c.is_open_or_unknown), ZERO)
                pending_lx = sum((c.reserved_remaining for c in cmds_cur if c.purpose == "LX" and c.is_open_or_unknown), ZERO)
                avail_prot = max(ZERO, prot_pos - pending_lx)
                res_short = short_qty + pending_es
                amt = act.amount or ZERO
                if purpose == "ES" and (res_short + amt) > avail_prot:
                    continue
                if purpose == "TP" and short_qty <= ZERO:
                    continue

                est_srv_ms = self._estimated_server_ms(facts, now_ms)
                valid_until_ms = min(est_srv_ms + self.config.request_valid_window_ms, ep_cur.expiry_ms - 1_000)
                if valid_until_ms <= (est_srv_ms + self.config.min_valid_until_lead_ms):
                    continue

                lbl = make_order_label(self.env_tag, ep_cur.episode_id, purpose, ep_cur.next_cmd_seq)
                cmd = ExecCommand(
                    cmd_id=f"{ep_cur.episode_id}:cmd_{ep_cur.next_cmd_seq:04d}_{purpose}",
                    episode_id=ep_id,
                    purpose=purpose,
                    label=lbl,
                    instrument_name=act.instrument_name or "",
                    direction=act.direction or "buy",
                    amount=amt,
                    limit_price=act.limit_price or ZERO,
                    time_in_force=act.time_in_force or "IOC",
                    reduce_only=act.reduce_only,
                    post_only=act.post_only,
                    reject_post_only=act.reject_post_only,
                    valid_until_ms=valid_until_ms,
                    created_at_ms=now_ms,
                    status=CommandStatus.PREPARED_UNKNOWN,
                    updated_at_ms=now_ms,
                )
                self.store.create_command_intent(
                    cmd,
                    now_ms=now_ms,
                    estimated_abort_loss_btc=getattr(act, "estimated_abort_loss_btc", None),
                )
                try:
                    vo = self.adapter.submit_order(
                        instrument_name=cmd.instrument_name,
                        direction=cmd.direction,
                        amount=cmd.amount,
                        limit_price=cmd.limit_price,
                        label=cmd.label,
                        time_in_force=cmd.time_in_force,
                        reduce_only=cmd.reduce_only,
                        post_only=cmd.post_only,
                        reject_post_only=cmd.reject_post_only,
                        valid_until_ms=cmd.valid_until_ms,
                    )
                    mapped = {
                        "open": CommandStatus.OPEN,
                        "filled": CommandStatus.FILLED,
                        "cancelled": CommandStatus.CANCELLED,
                        "rejected": CommandStatus.REJECTED,
                    }.get(vo.order_state, CommandStatus.OPEN)
                    vo_trades = getattr(vo, "trades", ()) or ()
                    accepted_trade_qty = ZERO
                    if vo_trades:
                        _ins_cnt, accepted_trade_qty = self.store.ingest_order_trades(
                            ep_cur,
                            vo_trades,
                            subaccount_id=self.subaccount_id,
                            now_ms=now_ms,
                            extra_order_id_to_cmd={vo.order_id: cmd} if vo.order_id else None,
                        )
                    # Do NOT release IOC reservation into FILLED/CANCELLED if trades are incomplete!
                    effective_status = mapped
                    if (
                        mapped in {CommandStatus.FILLED, CommandStatus.CANCELLED}
                        and vo.filled_amount > ZERO
                        and accepted_trade_qty < vo.filled_amount
                    ):
                        effective_status = CommandStatus.SUBMIT_UNKNOWN
                    self.store.update_command_state(
                        cmd.cmd_id,
                        status=effective_status,
                        venue_order_id=vo.order_id,
                        filled_amount=vo.filled_amount,
                        now_ms=now_ms,
                    )
                    if effective_status in {CommandStatus.FILLED, CommandStatus.CANCELLED} or purpose in {"EP", "ES", "RX", "LX"}:
                        did_sync_venue_op = True
                except VenueTransportError as exc:
                    if exc.code in {"429", "10028"}:
                        self._rate_limited_until_ms = now_ms + self._next_backoff_ms(exc.retry_after_ms)
                        self.store.update_command_state(
                            cmd.cmd_id, status=CommandStatus.SUBMIT_UNKNOWN, last_error=str(exc), now_ms=now_ms
                        )
                    elif (
                        exc.code in {"network_unknown", "network_error", "timeout", "submit_ack"}
                        or exc.code.startswith("network")
                    ):
                        self.store.update_command_state(
                            cmd.cmd_id, status=CommandStatus.SUBMIT_UNKNOWN, last_error=str(exc), now_ms=now_ms
                        )
                    elif exc.code == "13888":
                        self.store.update_command_state(
                            cmd.cmd_id, status=CommandStatus.EXPIRED_UNFILLED, last_error=str(exc), now_ms=now_ms
                        )
                    else:
                        self.store.update_command_state(
                            cmd.cmd_id, status=CommandStatus.REJECTED, last_error=str(exc), now_ms=now_ms
                        )
                        if exc.code == "10009":
                            did_sync_venue_op = True
                except Exception as exc:
                    self.store.update_command_state(
                        cmd.cmd_id, status=CommandStatus.SUBMIT_UNKNOWN, last_error=str(exc), now_ms=now_ms
                    )

            elif act.action_type == ActionType.CANCEL_BY_LABEL and act.target_cmd_id and act.target_label:
                if not self.writes_permitted_by_mode():
                    break
                self.store.update_command_state(
                    act.target_cmd_id,
                    status=CommandStatus.CANCEL_UNKNOWN,
                    cancel_requested_at_ms=now_ms,
                    now_ms=now_ms,
                )
                try:
                    self.adapter.cancel_by_label(act.target_label)
                    did_sync_venue_op = True
                except VenueTransportError as exc:
                    if exc.code in {"429", "10028"}:
                        self._rate_limited_until_ms = now_ms + self._next_backoff_ms(exc.retry_after_ms)
                    self.store.update_command_state(
                        act.target_cmd_id,
                        status=CommandStatus.CANCEL_UNKNOWN,
                        last_error=str(exc),
                        now_ms=now_ms,
                    )
                except Exception as exc:
                    self.store.update_command_state(
                        act.target_cmd_id,
                        status=CommandStatus.CANCEL_UNKNOWN,
                        last_error=str(exc),
                        now_ms=now_ms,
                    )
        return did_sync_venue_op

    def _try_admit_p5_candidate(
        self,
        contracts_map: Mapping[str, P5DecisionContract],
        t_now: int,
        *,
        from_control: bool = False,
    ) -> tuple[EpisodeState | None, ExchangeFacts | None]:
        """Automatically admit a fresh, valid P5 Accept under continuous capital authorization (Spec 2.1, 2.2, 4.1)."""
        if (
            not self.writes_permitted_by_mode()
            or self._pending_disarm_entry > 0
            or self._pending_exit_all > 0
            or (not from_control and (self._pending_control_ops > 0 or self._control_interrupt.is_set()))
        ):
            return None, None
        if not self._dcod_confirmed:
            self._last_reject_reason = "DCOD_UNCONFIRMED"
            return None, None
        auth, _pnl_settled, b_eff = self.store.get_effective_budget_state()
        if not auth.entry_armed:
            return None, None
        if b_eff <= ZERO:
            self._last_reject_reason = "EFFECTIVE_BUDGET_EXHAUSTED"
            return None, None

        # Evaluate newest hands first by decided_at_ms
        candidates = sorted(
            contracts_map.values(),
            key=lambda x: (x.decided_at_ms, x.hand_id),
            reverse=True,
        )
        for p5c in candidates:
            if p5c.decision != "Accept":
                continue
            if self.store.has_consumed_hand(p5c.hand_id):
                continue
            if p5c.link_state not in _VALID_LINK_STATES:
                self._last_reject_reason = f"INVALID_LINK_STATE:{p5c.link_state}"
                continue
            if p5c.revoked:
                self._last_reject_reason = "P5_ALREADY_REVOKED"
                continue
            if p5c.condition_triggered:
                self._last_reject_reason = "P5_PRE_ENTRY_CONDITION_TRIGGERED"
                continue
            dec_age = t_now - p5c.decided_at_ms
            link_age = t_now - p5c.link_recorded_at_ms
            if p5c.decided_at_ms <= 0 or dec_age < -5_000 or dec_age > self.config.max_decision_age_ms:
                # Spec 2.2.1: silently ignore stale (>180s) or future hands without dead-lettering
                self._last_reject_reason = "DECISION_TIMESTAMP_OUT_OF_WINDOW"
                continue
            if p5c.link_recorded_at_ms <= 0 or link_age < -5_000 or link_age > self.config.max_decision_age_ms:
                self._last_reject_reason = "LINK_TIMESTAMP_OUT_OF_WINDOW"
                continue
            if (p5c.expiry_ms - t_now) < self.config.min_entry_dte_ms:
                self._last_reject_reason = "DTE_BELOW_30M"
                continue

            facts = self._fetch_facts_safe(
                p5c.short_instrument,
                p5c.long_instrument,
                t_now=t_now,
                need_tx_logs=False,
                use_cache=False,
                episode_start_ms=t_now,
            )
            if facts is None or facts.api_error is not None:
                self._last_reject_reason = f"VENUE_FACTS_UNAVAILABLE:{(facts.api_error if facts else None) or self._last_error}"
                return None, facts
            if (p5c.expiry_ms - facts.server_time_ms) < self.config.min_entry_dte_ms:
                self._last_reject_reason = "DTE_BELOW_30M"
                continue
            if abs(facts.server_time_ms - t_now) > self.config.max_clock_skew_ms:
                self._last_reject_reason = "CLOCK_SKEW_HIGH"
                return None, facts
            if any(p.size != ZERO for p in facts.positions):
                self._last_reject_reason = "NON_ZERO_EXISTING_VENUE_POSITIONS"
                return None, facts
            if not facts.open_orders_complete or len(facts.open_orders) > 0:
                self._last_reject_reason = "NON_ZERO_EXISTING_OPEN_ORDERS"
                return None, facts
            if facts.short_spec is None or facts.long_spec is None:
                self._last_reject_reason = "INSTRUMENT_SPEC_MISSING"
                continue

            qty = quant_qty(min(auth.max_qty_btc, self.config.initial_qty_btc), facts.short_spec.min_trade_amount)
            if qty < facts.short_spec.min_trade_amount:
                self._last_reject_reason = "AUTHORIZED_QTY_BELOW_MIN_TRADE_AMOUNT"
                return None, facts

            ask_est = sweep_ask_price(facts.long_book, qty) or ZERO
            unpaid_prot_est = (
                conservative_split_buy_outlay_btc(ask_est, qty, facts.long_spec)
                if ask_est > ZERO
                else ceil_1e8(qty * self.config.max_zero_rec_ep_unit_btc)
            )
            temp_ep = EpisodeState(
                episode_id="cand",
                env=self.env_tag,
                subaccount_id=self.subaccount_id,
                dossier_id=p5c.dossier_id,
                hand_id=p5c.hand_id,
                p5_episode_id=p5c.episode_id,
                side=p5c.side,
                short_instrument=p5c.short_instrument,
                long_instrument=p5c.long_instrument,
                short_strike_usd=p5c.short_strike_usd,
                long_strike_usd=p5c.long_strike_usd,
                expiry_ms=p5c.expiry_ms,
                contract_hash="",
                phase=LifecyclePhase.ENTRY,
                created_at_ms=t_now,
                target_qty_btc=qty,
            )
            gate_ok, gate_reason = _entry_capital_and_margin_ok(
                episode=temp_ep,
                facts=facts,
                target_qty_btc=qty,
                paid_ep_outlay_btc=ZERO,
                paid_unmatched_ep_outlay_btc=ZERO,
                unpaid_prot_cost_btc=unpaid_prot_est,
                effective_budget_btc=b_eff,
                config=self.config,
            )
            if not gate_ok:
                self._last_reject_reason = gate_reason
                continue

            chash = compute_contract_hash(
                dossier_id=p5c.dossier_id,
                hand_id=p5c.hand_id,
                p5_episode_id=p5c.episode_id,
                decision_sha256=p5c.decision_sha256,
                condition_sha256=p5c.condition_sha256,
                short_instrument=p5c.short_instrument,
                long_instrument=p5c.long_instrument,
                expiry_ms=p5c.expiry_ms,
            )
            ep_id = f"ep_{hashlib.sha256(f'{p5c.hand_id}:{t_now}'.encode('utf-8')).hexdigest()[:12]}"
            ep = EpisodeState(
                episode_id=ep_id,
                env=self.env_tag,
                subaccount_id=self.subaccount_id,
                dossier_id=p5c.dossier_id,
                hand_id=p5c.hand_id,
                p5_episode_id=p5c.episode_id,
                side=p5c.side,
                short_instrument=p5c.short_instrument,
                long_instrument=p5c.long_instrument,
                short_strike_usd=p5c.short_strike_usd,
                long_strike_usd=p5c.long_strike_usd,
                expiry_ms=p5c.expiry_ms,
                contract_hash=chash,
                phase=LifecyclePhase.ENTRY,
                created_at_ms=t_now,
                auth_epoch=auth.auth_epoch,
                target_qty_btc=qty,
                min_net_credit_per_btc=self.config.min_net_credit_per_btc,
                max_zero_rec_ep_outlay_btc=min(b_eff, ceil_1e8(qty * self.config.max_zero_rec_ep_unit_btc)),
                effective_budget_at_entry_btc=b_eff,
                margin_model_at_entry=facts.margin_model,
                entry_spot_usd=facts.spot_usd if facts.spot_usd > ZERO else None,
                decided_at_ms=p5c.decided_at_ms,
                link_recorded_at_ms=p5c.link_recorded_at_ms,
                c_decision_ref_btc=p5c.c_decision_ref_btc,
            )
            self.store.create_episode(ep)
            self._last_reject_reason = None
            return ep, facts

        return None, None

    def run_tick(self, *, now_ms: int | None = None, max_followups: int = 3) -> dict[str, Any]:
        if self._pending_control_ops > 0 or self._control_interrupt.is_set():
            t_bail = int(now_ms if now_ms is not None else time.time() * 1000)
            with self._lock:
                writes_off, writes_off_reason = self.store.writes_disabled()
                return self._emit_status(
                    self.store.get_active_episode() or self.store.get_latest_episode(),
                    self._last_facts,
                    self._cached_contracts,
                    self._cached_aux,
                    writes_off=writes_off,
                    writes_off_reason=writes_off_reason,
                    now_ms=t_bail,
                )
        with self._lock:
            return self._run_tick_locked(now_ms=now_ms, max_followups=max_followups, from_control=False)

    def _run_tick_locked(
        self,
        *,
        now_ms: int | None = None,
        max_followups: int = 3,
        from_control: bool = False,
    ) -> dict[str, Any]:
        tick_start_mono = time.monotonic()
        synthetic_clock = now_ms is not None
        t_now = int(now_ms if synthetic_clock else time.time() * 1000)
        if not from_control and (self._pending_control_ops > 0 or self._control_interrupt.is_set()):
            writes_off, writes_off_reason = self.store.writes_disabled()
            self._last_tick_duration_ms = int((time.monotonic() - tick_start_mono) * 1000)
            return self._emit_status(
                self.store.get_active_episode() or self.store.get_latest_episode(),
                self._last_facts,
                self._cached_contracts,
                self._cached_aux,
                writes_off=writes_off,
                writes_off_reason=writes_off_reason,
                now_ms=t_now,
            )

        c_map, aux_map = self._scan_dossiers()
        active = self.store.get_active_episode()
        writes_off, writes_off_reason = self.store.writes_disabled()
        self._ensure_dcod_disabled(t_now)

        if self._rate_limited_until_ms is not None and t_now < self._rate_limited_until_ms:
            self._last_tick_duration_ms = int((time.monotonic() - tick_start_mono) * 1000)
            return self._emit_status(
                active or self.store.get_latest_episode(),
                self._last_facts,
                c_map,
                aux_map,
                writes_off=writes_off,
                writes_off_reason=writes_off_reason,
                now_ms=t_now,
            )

        prefetched_facts: ExchangeFacts | None = None
        if active is None:
            admitted_ep, admitted_facts = self._try_admit_p5_candidate(
                c_map, t_now, from_control=from_control
            )
            if admitted_ep is not None:
                active = admitted_ep
                prefetched_facts = admitted_facts
            else:
                # Spec 5.2 (Fixing D-9): IDLE polls account/positions ONLY — NEVER polls expired option books!
                facts = self._fetch_facts_safe(
                    "",
                    "",
                    t_now=t_now,
                    need_tx_logs=False,
                    allow_account_only=True,
                    use_cache=False,
                )
                self._last_venue_poll_ms = t_now
                self._last_tick_duration_ms = int((time.monotonic() - tick_start_mono) * 1000)
                return self._emit_status(
                    self.store.get_latest_episode(),
                    facts,
                    c_map,
                    aux_map,
                    writes_off=writes_off,
                    writes_off_reason=writes_off_reason,
                    now_ms=t_now,
                )

        for pass_idx in range(max_followups + 1):
            if (not from_control) and (self._pending_control_ops > 0 or self._control_interrupt.is_set()):
                break
            if from_control and self._pending_control_ops > 0:
                break
            if not synthetic_clock and pass_idx > 0:
                t_now = int(time.time() * 1000)
            ep = self.store.get_active_episode()
            if ep is None:
                break
            cmds_pre = self.store.get_commands(ep.episode_id)
            events_pre = self.store.get_venue_events(ep.episode_id)
            has_unmatched_tx = any(
                ev["event_type"] == "USER_TRADE" for ev in events_pre
            ) or any(c.filled_amount > ZERO or c.purpose in {"EP", "ES", "TP", "RX", "LX"} for c in cmds_pre)
            need_tx = bool(
                has_unmatched_tx
                or ep.phase in {LifecyclePhase.SHORT_LIABILITY_CLEARED, LifecyclePhase.INVENTORY_CLEARED}
                or t_now >= (ep.expiry_ms - 60_000)
            )

            if pass_idx == 0 and prefetched_facts is not None:
                facts = prefetched_facts
                prefetched_facts = None
            else:
                facts = self._fetch_facts_safe(
                    ep.short_instrument,
                    ep.long_instrument,
                    t_now=t_now,
                    need_tx_logs=need_tx,
                    use_cache=False,
                    episode_start_ms=ep.created_at_ms,
                )
            if facts is None:
                break
            if not synthetic_clock:
                t_now = int(time.time() * 1000)
            self._last_venue_poll_ms = t_now

            if facts.api_error is None:
                self.store.ingest_facts_events(ep, facts, now_ms=t_now)
                cmds_mid = self.store.get_commands(ep.episode_id)
                facts = self._probe_missing_commands(ep, cmds_mid, facts, t_now)
                self._last_facts = facts
                if not synthetic_clock:
                    t_now = int(time.time() * 1000)

            cmds = self.store.get_commands(ep.episode_id)
            events = self.store.get_venue_events(ep.episode_id)
            foreign_uids, foreign_oids = self.store.get_foreign_attribution_ids(ep.episode_id)
            auth_cur, _pnl_s, b_eff = self.store.get_effective_budget_state()
            p5c = c_map.get(ep.hand_id)

            res = evaluate_step(
                ep,
                auth_cur,
                cmds,
                events,
                facts,
                t_now,
                self.config,
                effective_budget_btc=b_eff,
                p5_contract=p5c,
                writes_disabled=not self.writes_permitted_by_mode(),
                foreign_event_uids=foreign_uids,
                foreign_order_ids=foreign_oids,
            )
            for cid, (nst, nf, noid) in res.command_resolutions.items():
                self.store.update_command_state(cid, status=nst, filled_amount=nf, venue_order_id=noid, now_ms=t_now)
            self.store.apply_episode_patch(ep.episode_id, res.episode_patch, now_ms=t_now)

            did_op = self._execute_actions(
                ep.episode_id, res.actions, facts, t_now, from_control=from_control
            )
            if not did_op:
                break
            if synthetic_clock:
                t_now += 1
        else:
            ep_last = self.store.get_active_episode()
            if (
                ep_last is not None
                and self._pending_control_ops == 0
                and (from_control or not self._control_interrupt.is_set())
            ):
                if not synthetic_clock:
                    t_now = int(time.time() * 1000)
                final_facts = self._fetch_facts_safe(
                    ep_last.short_instrument,
                    ep_last.long_instrument,
                    t_now=t_now,
                    need_tx_logs=True,
                    use_cache=False,
                    episode_start_ms=ep_last.created_at_ms,
                )
                if final_facts is not None and final_facts.api_error is None:
                    self.store.ingest_facts_events(ep_last, final_facts, now_ms=t_now)

        writes_off, writes_off_reason = self.store.writes_disabled()
        ep_show = self.store.get_active_episode() or self.store.get_latest_episode()
        self._last_tick_duration_ms = int((time.monotonic() - tick_start_mono) * 1000)
        return self._emit_status(
            ep_show,
            self._last_facts,
            c_map,
            aux_map,
            writes_off=writes_off,
            writes_off_reason=writes_off_reason,
            now_ms=t_now,
        )

    def _fetch_facts_safe(
        self,
        short_inst: str | None,
        long_inst: str | None,
        *,
        t_now: int,
        need_tx_logs: bool = True,
        allow_account_only: bool = False,
        use_cache: bool = True,
        episode_start_ms: int | None = None,
    ) -> ExchangeFacts | None:
        try:
            if (short_inst and long_inst) or allow_account_only:
                if isinstance(self.adapter, DeribitRestAdapter):
                    facts = self.adapter.fetch_facts(
                        short_inst or "",
                        long_inst or "",
                        need_tx_logs=need_tx_logs,
                        episode_start_ms=episode_start_ms,
                    )
                else:
                    facts = self.adapter.fetch_facts(
                        short_inst or "",
                        long_inst or "",
                        need_tx_logs=need_tx_logs,
                    )
                self._last_facts = facts
                self._last_error = facts.api_error
                if facts.api_error is None:
                    self._backoff_step = 0
                    self._rate_limited_until_ms = None
                elif facts.rate_limited_until_ms is not None:
                    self._rate_limited_until_ms = max(self._rate_limited_until_ms or 0, int(facts.rate_limited_until_ms))
                return facts
            return self._last_facts if use_cache else None
        except VenueTransportError as exc:
            if exc.code == "control_interrupted":
                return self._last_facts if use_cache else None
            self._last_error = f"{exc.code}:{exc.message}"
            if exc.code in {"429", "10028"}:
                self._rate_limited_until_ms = t_now + self._next_backoff_ms(exc.retry_after_ms)
            return self._last_facts if use_cache else None
        except Exception as exc:
            self._last_error = str(exc)
            return self._last_facts if use_cache else None

    def _emit_status(
        self,
        ep: EpisodeState | None,
        facts: ExchangeFacts | None,
        contracts_map: Mapping[str, P5DecisionContract],
        aux_map: Mapping[str, dict[str, Any]],
        *,
        writes_off: bool,
        writes_off_reason: str | None,
        now_ms: int,
    ) -> dict[str, Any]:
        stale_after = self.config.status_warn_age_ms
        facts_asof = facts.asof_ms if facts else 0
        facts_age = max(0, now_ms - facts_asof) if facts_asof > 0 else None
        venue_ok = (
            facts is not None
            and facts.api_error is None
            and (facts_age is None or facts_age <= self.config.status_stale_age_ms)
        )

        auth, pnl_settled, b_eff = self.store.get_effective_budget_state()
        writes_en = self.writes_permitted_by_mode()

        active_block = None
        manual_takeover = False
        cmds_for_poll: list[ExecCommand] = []
        if ep is not None and not ep.cashflow_settled:
            cmds = self.store.get_commands(ep.episode_id)
            cmds_for_poll = cmds
            all_cmds_json = [
                {
                    "cmd_id": c.cmd_id,
                    "purpose": c.purpose,
                    "label": c.label,
                    "status": c.status.value,
                    "instrument_name": c.instrument_name,
                    "direction": c.direction,
                    "amount": dec_str(c.amount),
                    "filled_amount": dec_str(c.filled_amount),
                    "limit_price": dec_str(c.limit_price),
                    "time_in_force": c.time_in_force,
                    "venue_order_id": c.venue_order_id,
                    "last_error": c.last_error,
                    "created_at_ms": c.created_at_ms,
                    "updated_at_ms": c.updated_at_ms,
                }
                for c in cmds
            ]
            open_or_unk = [x for x, c in zip(all_cmds_json, cmds) if c.is_open_or_unknown]

            short_pos = (
                sum((p.size for p in facts.positions if p.instrument_name == ep.short_instrument), ZERO)
                if facts
                else ZERO
            )
            prot_pos = (
                sum((p.size for p in facts.positions if p.instrument_name == ep.long_instrument), ZERO)
                if facts
                else ZERO
            )
            pending_lx = sum((c.reserved_remaining for c in cmds if c.purpose == "LX" and c.is_open_or_unknown), ZERO)
            pending_es = sum((c.reserved_remaining for c in cmds if c.purpose == "ES" and c.is_open_or_unknown), ZERO)
            avail_prot = max(ZERO, prot_pos - pending_lx)
            res_short = max(ZERO, -short_pos) + pending_es

            tp_cmd = next((c for c in reversed(cmds) if c.purpose == "TP"), None)
            lx_cmd = next((c for c in reversed(cmds) if c.purpose == "LX"), None)
            shadow_sx = (aux_map.get(ep.hand_id) or {}).get("shadow_exit_snapshot")
            ledger = self.store.rebuild_episode_ledger(ep.episode_id, facts=facts, shadow_counterfactual=shadow_sx)
            if writes_off and (open_or_unk or short_pos != ZERO or prot_pos != ZERO):
                manual_takeover = True

            res_status = "NONE"
            if ep.cashflow_settled:
                res_status = "SETTLED"
            elif lx_cmd and lx_cmd.is_open_or_unknown:
                res_status = "LX_ACTIVE"
            elif prot_pos > ZERO and ep.short_liability_cleared:
                res_status = "RESIDUAL_HOLD"

            tp_spent = D(ledger["accounting"]["tp_spend_ex_fee_btc"]) + D(ledger["accounting"]["tp_fees_btc"])
            rx_stage: int | None = None
            if ep.exit_latched and ep.exit_started_at_ms is not None:
                dt_rx = max(0, now_ms - ep.exit_started_at_ms)
                rx_stage = (
                    2
                    if (
                        ep.operator_exit_all
                        or (dt_rx >= self.config.rx_stage1_window_ms and ep.rx_quote_samples >= self.config.rx_stage1_min_samples)
                    )
                    else 1
                )

            active_block = {
                "episode_id": ep.episode_id,
                "hand_id": ep.hand_id,
                "dossier_id": ep.dossier_id,
                "p5_episode_id": ep.p5_episode_id,
                "side": ep.side,
                "short_instrument": ep.short_instrument,
                "long_instrument": ep.long_instrument,
                "short_strike_usd": dec_str(ep.short_strike_usd),
                "long_strike_usd": dec_str(ep.long_strike_usd),
                "expiry_ms": ep.expiry_ms,
                "created_at_ms": ep.created_at_ms,
                "decided_at_ms": ep.decided_at_ms,
                "link_recorded_at_ms": ep.link_recorded_at_ms,
                "auth_epoch": ep.auth_epoch,
                "target_qty_btc": dec_str(ep.target_qty_btc),
                "margin_model_at_entry": ep.margin_model_at_entry,
                "entry_spot_usd": dec_str(ep.entry_spot_usd),
                "phase": ep.phase.value,
                "terminal_outcome": ep.terminal_outcome,
                "short_liability_cleared": ep.short_liability_cleared,
                "inventory_cleared": ep.inventory_cleared,
                "cashflow_settled": ep.cashflow_settled,
                "entry_started_at_ms": ep.entry_started_at_ms,
                "entry_halted": ep.entry_halted,
                "entry_halt_reason": ep.entry_halt_reason,
                "entry_closed": ep.entry_closed,
                "entry_closed_at_ms": ep.entry_closed_at_ms,
                "entry_closure_reason": ep.entry_closure_reason,
                "exit_latched": ep.exit_latched,
                "exit_latch_reason": ep.exit_latch_reason,
                "exit_started_at_ms": ep.exit_started_at_ms,
                "short_pos_btc": dec_str(short_pos),
                "prot_pos_btc": dec_str(prot_pos),
                "available_prot_btc": dec_str(avail_prot),
                "reserved_short_btc": dec_str(res_short),
                "matched_qty_btc": dec_str(ep.matched_qty_btc),
                "matched_net_credit_btc": dec_str(ep.matched_net_credit_btc),
                "unmatched_prot_qty_btc": dec_str(ep.unmatched_prot_qty_btc),
                "unmatched_prot_cost_btc": dec_str(ep.unmatched_prot_cost_btc),
                "tp": {
                    "substate": ep.tp_substate,
                    "budget_total_btc": dec_str(ep.tp_budget_total_btc),
                    "spent_btc": dec_str(tp_spent),
                    "cap_init_btc": dec_str(ep.tp_cap_init_btc),
                    "target_capture_ratio": dec_str(ep.tp_target_capture_ratio),
                    "active_limit_price_btc": dec_str(tp_cmd.limit_price) if tp_cmd else None,
                    "active_remaining_btc": dec_str(tp_cmd.reserved_remaining) if tp_cmd else "0",
                    "order_status": tp_cmd.status.value if tp_cmd else None,
                },
                "revoke": {
                    "exit_latched": ep.exit_latched,
                    "exit_latch_reason": ep.exit_latch_reason,
                    "exit_started_at_ms": ep.exit_started_at_ms,
                    "operator_exit_all": ep.operator_exit_all,
                    "rx_attempts": ep.rx_attempts,
                    "rx_quote_samples": ep.rx_quote_samples,
                    "rx_stage": rx_stage,
                    "cash_wall_10009": ep.rx_cash_wall_10009,
                },
                "residual": {
                    "status": res_status,
                    "unmatched_prot_qty_btc": dec_str(ep.unmatched_prot_qty_btc),
                    "unmatched_prot_cost_btc": dec_str(ep.unmatched_prot_cost_btc),
                    "recovery_btc": ledger["accounting"]["unmatched_prot_recovery_btc"],
                },
                "commands": all_cmds_json,
                "open_or_unknown_commands": open_or_unk,
                "observations": ledger["observations"],
                "alerts": list(ep.alerts),
                "ledger": ledger,
                "shadow_counterfactual": shadow_sx,
            }

        if writes_off:
            health = "WRITE_DISABLED"
        elif self.mode == "SHADOW_ONLY" and active_block is None:
            health = "SHADOW_IDLE"
        elif self._last_error or (self._rate_limited_until_ms and now_ms < self._rate_limited_until_ms):
            health = "DEGRADED"
        elif facts is not None and facts_age is not None and facts_age > stale_after:
            health = "STALE"
        else:
            health = "OK" if (venue_ok or self.mode in {"SHADOW_ONLY", "DRY_RUN"}) else "DISCONNECTED"

        acct_block = None
        if facts is not None:
            mm_ratio = (
                dec_str((facts.equity_btc / facts.maintenance_margin_btc).quantize(Decimal("0.01")))
                if facts.maintenance_margin_btc > ZERO
                else None
            )
            cand_cash = ceil_1e8(
                self.config.initial_qty_btc
                * (
                    self.config.candidate_min_cash_pm_per_btc
                    if facts.portfolio_margining_enabled
                    else self.config.candidate_min_cash_sm_per_btc
                )
            )
            acct_block = {
                "spot_usd": dec_str(facts.spot_usd),
                "balance_btc": dec_str(facts.balance_btc if facts.balance_btc is not None else facts.equity_btc),
                "equity_btc": dec_str(facts.equity_btc),
                "available_funds_btc": dec_str(facts.available_funds_btc),
                "initial_margin_btc": dec_str(facts.initial_margin_btc),
                "maintenance_margin_btc": dec_str(facts.maintenance_margin_btc),
                "margin_buffer_ratio": mm_ratio,
                "portfolio_margining_enabled": facts.portfolio_margining_enabled,
                "margin_model": facts.margin_model,
                "candidate_min_cash_btc": dec_str(cand_cash),
            }

        settled_rows = self.store.conn.execute(
            """
            SELECT episode_id, hand_id, dossier_id, side, short_instrument, long_instrument,
                   phase, terminal_outcome, matched_qty_btc, matched_net_credit_btc,
                   short_liability_cleared, inventory_cleared, cashflow_settled, updated_at_ms
            FROM episodes
            WHERE cashflow_settled=1
            ORDER BY updated_at_ms DESC
            LIMIT 5
            """
        ).fetchall()
        history = []
        for r in settled_rows:
            ep_hist_id = str(r["episode_id"])
            hist_ledger = self.store.rebuild_episode_ledger(ep_hist_id)
            history.append(
                {
                    "episode_id": ep_hist_id,
                    "hand_id": r["hand_id"],
                    "dossier_id": r["dossier_id"],
                    "side": r["side"],
                    "short_instrument": r["short_instrument"],
                    "long_instrument": r["long_instrument"],
                    "phase": r["phase"],
                    "terminal_outcome": r["terminal_outcome"],
                    "matched_qty_btc": r["matched_qty_btc"],
                    "matched_net_credit_btc": r["matched_net_credit_btc"],
                    "realized_pnl_btc": hist_ledger["accounting"]["episode_total_realized_pnl_btc"],
                    "actual_fees_btc": hist_ledger["accounting"]["actual_fees_btc"],
                    "short_liability_cleared": bool(r["short_liability_cleared"]),
                    "inventory_cleared": bool(r["inventory_cleared"]),
                    "cashflow_settled": bool(r["cashflow_settled"]),
                    "updated_at_ms": int(r["updated_at_ms"]),
                }
            )

        rpc_rtt = self.adapter.last_rpc_rtt_ms if isinstance(self.adapter, DeribitRestAdapter) else None
        effective_last_err = self._last_error or (
            "dcod_unconfirmed" if (self.writes_permitted_by_mode() and not self._dcod_confirmed) else None
        )
        payload = {
            "schema": STATUS_SCHEMA,
            "contract_version": BEHAVIOR_SPEC_VERSION,
            "cockpit_mode": "READ_ONLY",
            "mode": self.mode,
            "writes_enabled": writes_en,
            "writes_disabled_reason": writes_off_reason,
            "manual_takeover_required": manual_takeover,
            "lifecycle_view": active_block["phase"] if active_block is not None else "IDLE",
            "generated_at_ms": now_ms,
            "facts_asof_ms": facts_asof or None,
            "stale_after_ms": stale_after,
            "stale": False,
            "status_age_ms": 0,
            "facts_age_ms": facts_age,
            "health": health,
            "venue_connected": bool(
                facts is not None
                and facts.api_error is None
                and not effective_last_err
                and not (self._rate_limited_until_ms and now_ms < self._rate_limited_until_ms)
            ),
            "last_error": effective_last_err,
            "subaccount_id": self.subaccount_id,
            "capital_policy": {
                "entry_armed": auth.entry_armed,
                "entry_paused": (not auth.entry_armed) or (not writes_en),
                "auth_epoch": auth.auth_epoch,
                "budget_cap_btc": dec_str(auth.budget_cap_btc),
                "effective_budget_btc": dec_str(b_eff),
                "pnl_settled_btc": dec_str(pnl_settled),
                "grant_baseline_pnl_btc": dec_str(auth.grant_baseline_pnl_btc),
                "max_qty_btc": dec_str(auth.max_qty_btc),
                "max_active_episodes": self.config.max_active_episodes,
                "min_net_credit_per_btc": dec_str(self.config.min_net_credit_per_btc),
                "max_zero_rec_ep_unit_btc": dec_str(self.config.max_zero_rec_ep_unit_btc),
                "updated_at_ms": auth.updated_at_ms,
                "last_reject_reason": self._last_reject_reason,
            },
            "telemetry": {
                "recommended_poll_ms": self.recommended_poll_ms(
                    ep if (ep and not ep.cashflow_settled) else None, cmds_for_poll
                ),
                "last_tick_duration_ms": self._last_tick_duration_ms,
                "last_rpc_rtt_ms": rpc_rtt,
            },
            "account": acct_block,
            "active_episode": active_block,
            "history_episodes": history,
        }
        _atomic_write_json(self.status_path, payload, mode=0o644)
        return payload

    # ------------------------------------------------------------------
    # Backend Admin / Local Socket Control Operations (No Frontend Control Surface)
    # ------------------------------------------------------------------
    def handle_health_fresh(self, *, now_ms: int | None = None) -> dict[str, Any]:
        """Query fresh venue snapshot + command state directly (never SQLite-only)."""
        with self._lock:
            t_now = int(now_ms if now_ms is not None else time.time() * 1000)
            active = self.store.get_active_episode()
            short_i = active.short_instrument if active else ""
            long_i = active.long_instrument if active else ""

            unauth_shadow_idle = (
                isinstance(self.adapter, DeribitRestAdapter)
                and self.mode == "SHADOW_ONLY"
                and not (self.adapter.client_id and self.adapter.client_secret)
            )
            if not (short_i and long_i) and unauth_shadow_idle:
                try:
                    srv = self.adapter.get_server_time_ms()
                    return {
                        "ok": True,
                        "mode": self.mode,
                        "server_time_ms": srv,
                        "facts_asof_ms": t_now,
                        "open_positions_count": 0,
                        "open_orders_count": 0,
                        "active_or_unknown_commands_count": 0,
                        "active_unsettled_episode": active.episode_id if active else None,
                        "safe_for_deploy": active is None,
                    }
                except Exception as exc:
                    return {
                        "ok": False,
                        "mode": self.mode,
                        "error": f"venue_unreachable:{exc}",
                        "safe_for_deploy": False,
                    }

            facts = self._fetch_facts_safe(
                short_i,
                long_i,
                t_now=t_now,
                need_tx_logs=False,
                allow_account_only=True,
                use_cache=False,
            )
            if facts is None or facts.api_error is not None or (t_now - facts.asof_ms) > self.config.status_stale_age_ms:
                return {
                    "ok": False,
                    "mode": self.mode,
                    "error": (facts.api_error if facts else None) or self._last_error or "facts_unavailable_or_stale",
                    "safe_for_deploy": False,
                }
            cmds = self.store.get_commands(active.episode_id) if active else []
            open_or_unk = [c for c in cmds if c.is_open_or_unknown]
            open_pos_cnt = sum(1 for p in facts.positions if p.size != ZERO)
            safe = (
                (open_pos_cnt == 0)
                and (len(open_or_unk) == 0)
                and (active is None or active.cashflow_settled)
                and (len(facts.open_orders) == 0)
            )
            return {
                "ok": True,
                "mode": self.mode,
                "server_time_ms": facts.server_time_ms,
                "facts_asof_ms": facts.asof_ms,
                "open_positions_count": open_pos_cnt,
                "open_orders_count": len(facts.open_orders),
                "active_or_unknown_commands_count": len(open_or_unk),
                "active_unsettled_episode": active.episode_id if active else None,
                "safe_for_deploy": safe,
            }

    def handle_capital_policy(
        self,
        body: Mapping[str, Any],
        *,
        now_ms: int | None = None,
        run_tick_after: bool = True,
    ) -> dict[str, Any]:
        """Configure continuous capital authorization (arm/pause, budget_cap_btc, max_qty_btc, reset_grant_baseline)."""
        want_armed_raw = body.get("entry_armed")
        if not isinstance(want_armed_raw, bool):
            raise ValueError("entry_armed_bool_required")
        disarm = not want_armed_raw
        self._enter_control_op(disarm_entry=disarm)
        left = False
        try:
            with self._lock:
                t_now = int(now_ms if now_ms is not None else time.time() * 1000)
                cur_auth = self.store.get_capital_auth()
                new_cap = D(body["budget_cap_btc"]) if body.get("budget_cap_btc") is not None else cur_auth.budget_cap_btc
                new_qty = D(body["max_qty_btc"]) if body.get("max_qty_btc") is not None else cur_auth.max_qty_btc
                reset_base = bool(body.get("reset_grant_baseline", False))
                operator = str(body.get("operator") or "admin_control")
                note = str(body.get("note") or "")

                if new_cap < ZERO:
                    raise ValueError("budget_cap_btc_must_be_non_negative")
                if new_qty <= ZERO:
                    raise ValueError("max_qty_btc_must_be_positive")

                risk_increasing = bool(
                    want_armed_raw
                    and (
                        not cur_auth.entry_armed
                        or new_cap > cur_auth.budget_cap_btc
                        or new_qty > cur_auth.max_qty_btc
                        or reset_base
                    )
                )
                if want_armed_raw:
                    if not self.writes_permitted_by_mode():
                        disabled, reason = self.store.writes_disabled()
                        raise ValueError(f"writes_not_permitted:mode={self.mode}:disabled={disabled}:{reason or ''}")
                    if new_cap <= ZERO:
                        raise ValueError("budget_cap_btc_must_be_positive_when_armed")
                    if new_qty != self.config.initial_qty_btc:
                        raise ValueError(f"initial_phase_fixed_qty_required:{dec_str(self.config.initial_qty_btc)}")

                if not disarm:
                    self._leave_control_op(disarm_entry=False)
                    left = True

                if risk_increasing:
                    facts = self._fetch_facts_safe(
                        "",
                        "",
                        t_now=t_now,
                        need_tx_logs=False,
                        allow_account_only=True,
                        use_cache=False,
                    )
                    if facts is None or facts.api_error is not None:
                        raise ValueError("venue_facts_unavailable_for_capital_authorization")
                    bal = facts.balance_btc if facts.balance_btc is not None else facts.equity_btc
                    if facts.available_funds_btc < new_cap or bal < new_cap:
                        raise ValueError(
                            f"subaccount_funds_below_budget_cap:avail={dec_str(facts.available_funds_btc)}:bal={dec_str(bal)}<{dec_str(new_cap)}"
                        )

                updated_auth = self.store.set_capital_policy(
                    entry_armed=want_armed_raw,
                    budget_cap_btc=new_cap,
                    max_qty_btc=new_qty,
                    reset_grant_baseline=reset_base,
                    operator=operator,
                    note=note,
                    now_ms=t_now,
                )
                if not left:
                    self._leave_control_op(disarm_entry=disarm)
                    left = True
                self._wake_event.set()
                tick_error: str | None = None
                if run_tick_after:
                    try:
                        status = self._run_tick_locked(now_ms=now_ms, from_control=True)
                    except Exception as exc:
                        tick_error = str(exc)
                        self._last_error = f"capital_policy_post_tick:{exc}"
                        writes_off, writes_off_reason = self.store.writes_disabled()
                        status = self._emit_status(
                            self.store.get_active_episode() or self.store.get_latest_episode(),
                            self._last_facts,
                            self._cached_contracts,
                            self._cached_aux,
                            writes_off=writes_off,
                            writes_off_reason=writes_off_reason,
                            now_ms=int(now_ms if now_ms is not None else time.time() * 1000),
                        )
                else:
                    writes_off, writes_off_reason = self.store.writes_disabled()
                    status = self._emit_status(
                        self.store.get_active_episode() or self.store.get_latest_episode(),
                        self._last_facts,
                        self._cached_contracts,
                        self._cached_aux,
                        writes_off=writes_off,
                        writes_off_reason=writes_off_reason,
                        now_ms=t_now,
                    )
                res = {
                    "ok": True,
                    "action": "SET_CAPITAL_POLICY",
                    "auth_epoch": updated_auth.auth_epoch,
                    "entry_armed": updated_auth.entry_armed,
                    "budget_cap_btc": dec_str(updated_auth.budget_cap_btc),
                    "status": status,
                }
                if tick_error is not None:
                    res["tick_error"] = tick_error
                return res
        finally:
            if not left:
                self._leave_control_op(disarm_entry=disarm)

    def handle_exit_all(
        self,
        *,
        operator: str = "admin_control",
        reason: str = "OPERATOR_EXIT_ALL",
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Interrupt active reads, durably fsync EXIT_ALL latch to SQLite, then execute unwind tick."""
        self._enter_control_op(exit_all=True)
        left = False
        try:
            with self._lock:
                t_now = int(now_ms if now_ms is not None else time.time() * 1000)
                active_ep_id = self.store.latch_exit_all(now_ms=t_now, operator=operator, reason=reason)
                self._leave_control_op(exit_all=True)
                left = True
                self._wake_event.set()
                tick_error: str | None = None
                try:
                    status = self._run_tick_locked(now_ms=now_ms, from_control=True)
                except Exception as exc:
                    tick_error = str(exc)
                    self._last_error = f"exit_all_post_tick:{exc}"
                    writes_off, writes_off_reason = self.store.writes_disabled()
                    status = self._emit_status(
                        self.store.get_active_episode() or self.store.get_latest_episode(),
                        self._last_facts,
                        self._cached_contracts,
                        self._cached_aux,
                        writes_off=writes_off,
                        writes_off_reason=writes_off_reason,
                        now_ms=int(now_ms if now_ms is not None else time.time() * 1000),
                    )
                res = {
                    "ok": True,
                    "action": "EXIT_ALL",
                    "episode_id": active_ep_id,
                    "status": status,
                }
                if tick_error is not None:
                    res["tick_error"] = tick_error
                return res
        finally:
            if not left:
                self._leave_control_op(exit_all=True)

    def handle_disable_writes(
        self,
        reason: str,
        *,
        operator: str = "admin_control",
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        """Interrupt active reads, durably fsync one-way writes_disabled latch to SQLite, and emit takeover alert."""
        self._enter_control_op(disable_writes=True)
        left = False
        try:
            with self._lock:
                t_now = int(now_ms if now_ms is not None else time.time() * 1000)
                self.store.set_writes_disabled(reason or "OPERATOR_DISABLE_WRITES", now_ms=t_now, operator=operator)
                self._leave_control_op()
                left = True
                self._wake_event.set()
                tick_error: str | None = None
                try:
                    status = self._run_tick_locked(now_ms=now_ms, from_control=True)
                except Exception as exc:
                    tick_error = str(exc)
                    self._last_error = f"disable_writes_post_tick:{exc}"
                    writes_off, writes_off_reason = self.store.writes_disabled()
                    status = self._emit_status(
                        self.store.get_active_episode() or self.store.get_latest_episode(),
                        self._last_facts,
                        self._cached_contracts,
                        self._cached_aux,
                        writes_off=writes_off,
                        writes_off_reason=writes_off_reason,
                        now_ms=int(now_ms if now_ms is not None else time.time() * 1000),
                    )
                active = status.get("active_episode") or {}
                open_cnt = len(active.get("open_or_unknown_commands") or [])
                res = {
                    "ok": True,
                    "action": "DISABLE_WRITES",
                    "writes_enabled": False,
                    "manual_takeover_required": bool(status.get("manual_takeover_required")),
                    "open_orders_warning": (
                        f"{open_cnt} open/unknown orders remain on exchange; manual exchange takeover required"
                        if (open_cnt or status.get("manual_takeover_required"))
                        else None
                    ),
                    "status": status,
                }
                if tick_error is not None:
                    res["tick_error"] = tick_error
                return res
        finally:
            if not left:
                self._leave_control_op()

    def has_fast_poll_trigger(
        self,
        active: EpisodeState | None,
        c_map: Mapping[str, P5DecisionContract],
        now_ms: int,
    ) -> bool:
        """Return True ONLY on actionable admission or active exit triggers (never stale accepts or unverified revokes)."""
        if active is None:
            if not self.writes_permitted_by_mode() or not self.store.get_capital_auth().entry_armed:
                return False
            for c in c_map.values():
                if (
                    c.decision == "Accept"
                    and not self.store.has_consumed_hand(c.hand_id)
                    and c.link_state in _VALID_LINK_STATES
                    and not c.revoked
                    and not c.condition_triggered
                    and c.decided_at_ms > 0
                    and -5_000 <= (now_ms - c.decided_at_ms) <= self.config.max_decision_age_ms
                    and c.link_recorded_at_ms > 0
                    and -5_000 <= (now_ms - c.link_recorded_at_ms) <= self.config.max_decision_age_ms
                    and (c.expiry_ms - now_ms) >= self.config.min_entry_dte_ms
                ):
                    return True
            return False
        if active.hand_id in c_map:
            p5c = c_map[active.hand_id]
            verified_revoke = bool(p5c.revoked and p5c.revoke_shadow_action in _VERIFIED_REVOKE_ACTIONS)
            if (verified_revoke and not active.exit_latched) or (
                p5c.condition_triggered and not active.entry_closed and not active.entry_halted
            ):
                return True
        return False


def load_control_token(*, token_file: Path | None = None) -> str | None:
    """Load dedicated execution control token strictly from LoadCredential or isolated token file."""
    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if cred_dir:
        cand = Path(cred_dir) / "exec_control_token"
        if cand.is_file():
            return cand.read_text(encoding="utf-8").strip()
    if token_file and Path(token_file).is_file():
        return Path(token_file).read_text(encoding="utf-8").strip()
    env_file = os.environ.get("ASTRA_EXEC_CONTROL_TOKEN_FILE")
    if env_file and Path(env_file).is_file():
        return Path(env_file).read_text(encoding="utf-8").strip()
    return None


def _build_control_handler(
    coordinator: ExecutionCoordinator,
    *,
    expected_token: str,
    allowed_peer_uids: set[int] | None = None,
) -> type[BaseHTTPRequestHandler]:
    class ControlHandler(BaseHTTPRequestHandler):
        def _authorize(self) -> tuple[bool, int, str]:
            af_unix = getattr(socket, "AF_UNIX", None)
            if af_unix is not None and self.connection.family == af_unix:
                if allowed_peer_uids is not None and hasattr(socket, "SO_PEERCRED"):
                    creds = self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                    _pid, uid, _gid = struct.unpack("3i", creds)
                    if uid not in allowed_peer_uids:
                        return False, 403, f"peer_uid_forbidden:{uid}"
            else:
                host = self.client_address[0] if self.client_address else ""
                if host not in {"127.0.0.1", "::1"}:
                    return False, 403, "loopback_only"
            auth = self.headers.get("Authorization", "")
            if not expected_token or not hmac.compare_digest(
                auth.encode("utf-8"), f"Bearer {expected_token}".encode("utf-8")
            ):
                return False, 401, "invalid_exec_control_token"
            return True, 200, "ok"

        def _send(self, code: int, payload: Mapping[str, Any]) -> None:
            raw = (json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            ok, st, reason = self._authorize()
            if not ok:
                self._send(st, {"ok": False, "code": reason})
                return
            if self.path.split("?", 1)[0] == "/health-fresh":
                res = coordinator.handle_health_fresh()
                self._send(200 if res.get("ok") else 503, res)
                return
            self._send(404, {"ok": False, "code": "not_found"})

        def do_POST(self) -> None:
            ok, st, reason = self._authorize()
            if not ok:
                self._send(st, {"ok": False, "code": reason})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0 or length > 32_768:
                self._send(413, {"ok": False, "code": "body_too_large"})
                return
            raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            try:
                body = json.loads(raw or "{}")
                if not isinstance(body, dict):
                    raise ValueError("body_must_be_object")
                path = self.path.split("?", 1)[0]
                if path == "/capital-policy":
                    self._send(200, coordinator.handle_capital_policy(body))
                elif path == "/exit-all":
                    self._send(
                        200,
                        coordinator.handle_exit_all(
                            operator=str(body.get("operator") or "admin_socket"),
                            reason=str(body.get("reason") or "OPERATOR_EXIT_ALL"),
                        ),
                    )
                elif path == "/disable-writes":
                    self._send(
                        200,
                        coordinator.handle_disable_writes(
                            str(body.get("reason") or "OPERATOR_DISABLE_WRITES"),
                            operator=str(body.get("operator") or "admin_socket"),
                        ),
                    )
                else:
                    self._send(404, {"ok": False, "code": "not_found"})
            except ValueError as exc:
                self._send(409, {"ok": False, "code": str(exc)})
            except Exception as exc:
                self._send(500, {"ok": False, "code": f"{type(exc).__name__}:{exc}"})

        def log_message(self, format: str, *args: Any) -> None:
            return

    return ControlHandler


if hasattr(socketserver, "UnixStreamServer"):
    class UnixControlHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True
else:
    UnixControlHTTPServer = None  # type: ignore[assignment,misc]


def start_control_server(
    coordinator: ExecutionCoordinator,
    *,
    expected_token: str,
    unix_socket: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 0,
    allowed_peer_uids: set[int] | None = None,
) -> tuple[Any, str]:
    handler = _build_control_handler(coordinator, expected_token=expected_token, allowed_peer_uids=allowed_peer_uids)
    if unix_socket is not None and UnixControlHTTPServer is not None and hasattr(socket, "AF_UNIX"):
        unix_socket.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(unix_socket.parent, 0o750)
        unix_socket.unlink(missing_ok=True)
        srv = UnixControlHTTPServer(str(unix_socket), handler)
        if os.name != "nt":
            os.chmod(unix_socket, 0o660)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        return srv, f"unix://{unix_socket}"
    srv = ThreadingHTTPServer((host, port), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://{host}:{srv.server_address[1]}"


def _default_allowed_peer_uids() -> set[int]:
    """Allow root (uid 0) and coordinator UID strictly (bitnami & read-only workbench excluded by default)."""
    uids: set[int] = {0}
    if hasattr(os, "getuid"):
        uids.add(os.getuid())
    return uids


def _is_posix_runtime() -> bool:
    return os.name != "nt"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="xxproject Single-Writer Execution Coordinator (v1.2.0)")
    parser.add_argument("--mode", choices=ALLOWED_MODES, default="SHADOW_ONLY")
    parser.add_argument("--env-tag", default="tn")
    parser.add_argument("--subaccount-id", default=os.environ.get("ASTRA_EXEC_SUBACCOUNT_ID", ""))
    parser.add_argument("--base-url", default=os.environ.get("ASTRA_EXEC_BASE_URL", TESTNET_BASE_URL))
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--status-path", type=Path, required=True)
    parser.add_argument("--dossier-root", type=Path, default=None)
    parser.add_argument("--control-socket", type=Path, default=None)
    parser.add_argument("--control-port", type=int, default=0)
    parser.add_argument("--control-token-file", type=Path, default=None)
    parser.add_argument("--allowed-peer-uids", default=os.environ.get("ASTRA_EXEC_ALLOWED_PEER_UIDS", ""))
    parser.add_argument("--entry-armed", choices=("0", "1"), default=os.environ.get("ASTRA_EXEC_ENTRY_ARMED"))
    parser.add_argument("--budget-cap-btc", default=os.environ.get("ASTRA_EXEC_BUDGET_CAP_BTC"))
    parser.add_argument("--max-qty-btc", default=os.environ.get("ASTRA_EXEC_MAX_QTY_BTC"))
    parser.add_argument("--reset-grant-baseline", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--admin-resolve-unknown-cmd", default=None)
    parser.add_argument("--target-status", choices=("EXPIRED_UNFILLED", "CANCELLED", "REJECTED"), default="EXPIRED_UNFILLED")
    parser.add_argument("--operator", default="admin_cli")
    parser.add_argument("--evidence-note", default="")
    args = parser.parse_args(argv)

    if args.admin_resolve_unknown_cmd:
        st_admin = ExecutionStore(args.db)
        try:
            resolved_cmd = st_admin.resolve_stale_unknown_command_admin_only(
                args.admin_resolve_unknown_cmd,
                target_status=CommandStatus(args.target_status),
                operator=args.operator,
                evidence_note=args.evidence_note,
                now_ms=int(time.time() * 1000),
            )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "action": "ADMIN_RESOLVE_UNKNOWN_CMD",
                        "cmd_id": resolved_cmd.cmd_id,
                        "status": resolved_cmd.status.value,
                        "last_error": resolved_cmd.last_error,
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        finally:
            st_admin.close()

    if _is_posix_runtime() and args.mode != "SHADOW_ONLY" and not args.once and args.control_socket is None:
        raise EnvironmentGuardError("CONTROL_SOCKET_REQUIRED_ON_POSIX_NON_SHADOW")

    cid = os.environ.get("DERIBIT_EXEC_CLIENT_ID")
    csec = os.environ.get("DERIBIT_EXEC_CLIENT_SECRET")
    adapter = DeribitRestAdapter(
        mode=args.mode,
        base_url=args.base_url,
        client_id=cid,
        client_secret=csec,
        subaccount_id=args.subaccount_id,
    )
    store = ExecutionStore(args.db)
    try:
        coord = ExecutionCoordinator(
            mode=args.mode,
            env_tag=args.env_tag,
            subaccount_id=args.subaccount_id,
            store=store,
            adapter=adapter,
            status_path=args.status_path,
            dossier_root=args.dossier_root,
        )
        init_armed = None if args.entry_armed is None else (args.entry_armed == "1")
        init_cap = opt_dec(args.budget_cap_btc)
        init_qty = opt_dec(args.max_qty_btc)
        coord.startup_init(
            initial_entry_armed=init_armed,
            initial_budget_cap_btc=init_cap,
            initial_max_qty_btc=init_qty,
            reset_grant_baseline=bool(args.reset_grant_baseline),
        )
        if args.once:
            res = coord.run_tick()
            print(json.dumps({"ok": True, "mode": args.mode, "health": res["health"]}, ensure_ascii=False))
            return 0

        tok = load_control_token(token_file=args.control_token_file) or ""
        raw_uids = str(getattr(args, "allowed_peer_uids", "") or "").strip()
        if raw_uids:
            allowed_uids: set[int] | None = {int(x.strip()) for x in raw_uids.split(",") if x.strip()}
        elif args.control_socket is not None and os.name != "nt":
            allowed_uids = _default_allowed_peer_uids()
        else:
            allowed_uids = None
        srv, _addr = start_control_server(
            coord,
            expected_token=tok,
            unix_socket=args.control_socket,
            port=args.control_port,
            allowed_peer_uids=allowed_uids,
        )
        try:
            # Target 0.5s local watcher cadence + adaptive 1s / 5s / 30s venue polling (Spec 5.2)
            while True:
                now_ms = int(time.time() * 1000)
                active = store.get_active_episode()
                cmds = store.get_commands(active.episode_id) if active else []
                poll_ms = coord.recommended_poll_ms(active, cmds)
                should_poll = (now_ms - coord._last_venue_poll_ms) >= poll_ms or coord._wake_event.is_set()
                if not should_poll:
                    c_map, _ = coord._scan_dossiers()
                    if coord.has_fast_poll_trigger(active, c_map, now_ms):
                        should_poll = True
                if should_poll:
                    coord._wake_event.clear()
                    coord.run_tick()
                coord._wake_event.wait(timeout=0.5)
        finally:
            srv.shutdown()
            srv.server_close()
    finally:
        adapter.close()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
