"""Independent Deribit inverse-option execution simulator and settlement oracle.

Standalone test harness implementing venue matching, valid_until expiration,
stepped ticks, rate-limit/network fault injection, 2026-08-01 two-stage inverse
option settlement, and independent Decimal accounting/TP verification without
importing production fee, payoff, or policy logic.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN, ROUND_UP
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

SAT = Decimal("0.00000001")
ZERO = Decimal("0")
ONE = Decimal("1")
DEFAULT_ORDER_STATE_LABEL_TTL_MS = 1_800_000  # Deribit 30m closed-order label retention
DEFAULT_ARCHIVE_AGE_MS = 1_500_000  # Deribit 25m hot-history vs archive boundary

__all__ = [
    "DEFAULT_ORDER_STATE_LABEL_TTL_MS",
    "DEFAULT_ARCHIVE_AGE_MS",
    "DeribitApiError",
    "RateLimitError",
    "SimulatedNetworkError",
    "OracleBookStatus",
    "OracleTickStep",
    "OracleInstrumentSpec",
    "OracleBookQuote",
    "OracleExchangePosition",
    "OracleExchangeOrder",
    "OracleOrder",
    "OracleVenueTrade",
    "OracleTxLogEntry",
    "OracleExchangeFacts",
    "OracleTradeRecord",
    "OracleTxAdjustment",
    "SimulatedDeribitVenue",
    "IndependentSettlementOracle",
    "to_dec",
    "ceil_1e8",
    "floor_1e8",
    "is_valid_tick_for_spec",
    "round_tick_down_for_spec",
    "round_tick_up_for_spec",
    "step_tick_down_for_spec",
    "step_tick_up_for_spec",
    "compute_option_trade_fee_btc",
    "compute_delivery_fee_btc",
]


def to_dec(value: Any) -> Decimal:
    """Convert int/str/float/Decimal strictly into Decimal."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is None:
        raise ValueError(f"cannot convert {value!r} to Decimal")
    if isinstance(value, (int, str)):
        return Decimal(str(value).strip())
    if isinstance(value, float):
        return Decimal(repr(value))
    raise TypeError(f"unsupported Decimal input type: {type(value).__name__}")


def _opt_dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    return to_dec(value)


def _dec_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    norm = value.quantize(SAT) if value == value.quantize(SAT) else value.normalize()
    text = format(norm, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def ceil_1e8(value: Decimal) -> Decimal:
    """Ceiling at 1e-8 BTC precision for non-negative fee/outlay amounts."""
    if value < ZERO:
        raise ValueError(f"ceil_1e8 expects non-negative value, got {value}")
    return value.quantize(SAT, rounding=ROUND_CEILING)


def floor_1e8(value: Decimal) -> Decimal:
    """Floor toward 0 at 1e-8 BTC precision for non-negative budgets."""
    if value <= ZERO:
        return ZERO
    return value.quantize(SAT, rounding=ROUND_DOWN)


def _floor_toward_neg_inf_1e8(value: Decimal) -> Decimal:
    rounding = ROUND_DOWN if value >= ZERO else ROUND_UP
    return value.quantize(SAT, rounding=rounding)


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return _dec_str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    return obj


class DeribitApiError(Exception):
    """Simulated Deribit JSON-RPC / HTTP error."""

    def __init__(self, code: int, message: str, *, data: Mapping[str, Any] | None = None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = int(code)
        self.message = str(message)
        self.data = dict(data or {})


class RateLimitError(DeribitApiError):
    """Simulated 429 or 10028 too_many_requests error."""

    def __init__(self, code: int, message: str, *, retry_after_ms: int = 1000) -> None:
        super().__init__(code, message, data={"retry_after_ms": int(retry_after_ms)})
        self.retry_after_ms = int(retry_after_ms)


class SimulatedNetworkError(Exception):
    """Simulated dropped ack or transport timeout."""

    def __init__(self, message: str, *, code: int = -1, phase: str = "unknown") -> None:
        super().__init__(message)
        self.code = int(code)
        self.phase = phase


class OracleBookStatus(str, Enum):
    OK = "OK"
    EMPTY_SIDE = "EMPTY_SIDE"
    DATA_GAP = "DATA_GAP"


@dataclass(frozen=True)
class OracleTickStep:
    above_price: Decimal
    tick_size: Decimal


@dataclass(frozen=True)
class OracleInstrumentSpec:
    instrument_name: str
    kind: str  # "option" | "future"
    settlement_period: str  # "day" | "week" | "month"
    strike_usd: Decimal | None
    option_type: str | None  # "put" | "call" | None
    expiry_ms: int
    tick_size: Decimal
    min_trade_amount: Decimal
    tick_size_steps: tuple[OracleTickStep, ...] = ()
    maker_commission: Decimal = Decimal("0.0003")
    taker_commission: Decimal = Decimal("0.0003")
    fee_cap_ratio: Decimal = Decimal("0.125")
    delivery_fee_rate: Decimal = Decimal("0.00015")
    contract_size: Decimal = Decimal("1")


def _normalize_book_levels(
    levels: Sequence[tuple[Any, Any]] | None,
    *,
    side: str,
) -> tuple[tuple[Decimal, Decimal], ...]:
    if not levels:
        return ()
    parsed: list[tuple[Decimal, Decimal]] = []
    for raw_px, raw_sz in levels:
        if raw_px is None or raw_sz is None or isinstance(raw_px, bool) or isinstance(raw_sz, bool):
            continue
        px = to_dec(raw_px)
        sz = to_dec(raw_sz)
        if px > ZERO and sz > ZERO:
            parsed.append((px, sz))
    parsed.sort(key=lambda lv: lv[0], reverse=(side == "bid"))
    return tuple(parsed)


@dataclass(frozen=True)
class OracleBookQuote:
    instrument_name: str
    bid_price: Decimal | None
    bid_amount: Decimal
    ask_price: Decimal | None
    ask_amount: Decimal
    mark_price: Decimal | None
    min_price: Decimal | None
    max_price: Decimal | None
    timestamp_ms: int
    status: OracleBookStatus
    gap_reason: str | None = None
    fetched_at_ms: int | None = None
    bids: Sequence[tuple[Any, Any]] | None = None
    asks: Sequence[tuple[Any, Any]] | None = None

    def __post_init__(self) -> None:
        eff_fetched = int(self.timestamp_ms) if self.fetched_at_ms is None else int(self.fetched_at_ms)
        if eff_fetched <= 0 and self.timestamp_ms > 0 and self.fetched_at_ms is None:
            eff_fetched = int(self.timestamp_ms)
        object.__setattr__(self, "fetched_at_ms", eff_fetched)

        b_px = _opt_dec(self.bid_price)
        b_sz = to_dec(self.bid_amount)
        if self.bids is None:
            if self.status != OracleBookStatus.DATA_GAP and b_px is not None and b_px > ZERO and b_sz > ZERO:
                norm_bids: tuple[tuple[Decimal, Decimal], ...] = ((b_px, b_sz),)
            else:
                norm_bids = ()
        else:
            norm_bids = _normalize_book_levels(self.bids, side="bid")
            if self.status != OracleBookStatus.DATA_GAP and b_px is None and norm_bids:
                b_px, b_sz = norm_bids[0]
        object.__setattr__(self, "bid_price", b_px if (b_px is not None and b_px > ZERO and b_sz > ZERO) else None)
        object.__setattr__(self, "bid_amount", b_sz if (b_px is not None and b_px > ZERO and b_sz > ZERO) else ZERO)
        object.__setattr__(self, "bids", norm_bids)

        a_px = _opt_dec(self.ask_price)
        a_sz = to_dec(self.ask_amount)
        if self.asks is None:
            if self.status != OracleBookStatus.DATA_GAP and a_px is not None and a_px > ZERO and a_sz > ZERO:
                norm_asks: tuple[tuple[Decimal, Decimal], ...] = ((a_px, a_sz),)
            else:
                norm_asks = ()
        else:
            norm_asks = _normalize_book_levels(self.asks, side="ask")
            if self.status != OracleBookStatus.DATA_GAP and a_px is None and norm_asks:
                a_px, a_sz = norm_asks[0]
        object.__setattr__(self, "ask_price", a_px if (a_px is not None and a_px > ZERO and a_sz > ZERO) else None)
        object.__setattr__(self, "ask_amount", a_sz if (a_px is not None and a_px > ZERO and a_sz > ZERO) else ZERO)
        object.__setattr__(self, "asks", norm_asks)


def _rebuild_book_after_match(
    book: OracleBookQuote,
    *,
    bids: tuple[tuple[Decimal, Decimal], ...],
    asks: tuple[tuple[Decimal, Decimal], ...],
    ts_ms: int,
) -> OracleBookQuote:
    b_px = bids[0][0] if bids else None
    b_sz = bids[0][1] if bids else ZERO
    a_px = asks[0][0] if asks else None
    a_sz = asks[0][1] if asks else ZERO
    status = (
        OracleBookStatus.DATA_GAP
        if book.status == OracleBookStatus.DATA_GAP
        else (OracleBookStatus.EMPTY_SIDE if (b_px is None or a_px is None) else OracleBookStatus.OK)
    )
    return OracleBookQuote(
        instrument_name=book.instrument_name,
        bid_price=b_px,
        bid_amount=b_sz,
        ask_price=a_px,
        ask_amount=a_sz,
        mark_price=book.mark_price,
        min_price=book.min_price,
        max_price=book.max_price,
        timestamp_ms=int(ts_ms),
        status=status,
        gap_reason=book.gap_reason if status == OracleBookStatus.DATA_GAP else None,
        fetched_at_ms=int(book.fetched_at_ms or ts_ms),
        bids=bids,
        asks=asks,
    )


@dataclass(frozen=True)
class OracleExchangePosition:
    instrument_name: str
    kind: str
    size: Decimal
    average_price: Decimal | None = None
    mark_price: Decimal | None = None
    index_price: Decimal | None = None


@dataclass(frozen=True)
class OracleExchangeOrder:
    order_id: str
    label: str
    instrument_name: str
    direction: str
    amount: Decimal
    filled_amount: Decimal
    price: Decimal
    average_price: Decimal | None
    order_state: str
    time_in_force: str
    reduce_only: bool
    post_only: bool
    creation_timestamp_ms: int
    last_update_timestamp_ms: int
    reject_reason: str | None = None

    @property
    def remaining_amount(self) -> Decimal:
        return max(ZERO, self.amount - self.filled_amount)


OracleOrder = OracleExchangeOrder


@dataclass(frozen=True)
class OracleVenueTrade:
    trade_id: str
    order_id: str
    label: str
    instrument_name: str
    direction: str
    amount: Decimal
    price: Decimal
    fee: Decimal
    fee_currency: str
    timestamp_ms: int
    liquidity: str


@dataclass(frozen=True)
class OracleTxLogEntry:
    id: int
    type: str
    instrument_name: str
    timestamp_ms: int
    amount: Decimal
    price: Decimal | None
    cashflow: Decimal
    change: Decimal
    commission: Decimal
    currency: str
    trade_id: str | None = None
    order_id: str | None = None
    settlement_price: Decimal | None = None
    info: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OracleExchangeFacts:
    asof_ms: int
    server_time_ms: int
    spot_usd: Decimal
    equity_btc: Decimal
    available_funds_btc: Decimal
    initial_margin_btc: Decimal
    maintenance_margin_btc: Decimal
    positions: tuple[OracleExchangePosition, ...]
    open_orders: tuple[OracleExchangeOrder, ...]
    open_orders_complete: bool
    history_complete: bool
    recent_orders_by_label: dict[str, tuple[OracleExchangeOrder, ...]]
    recent_trades: tuple[OracleVenueTrade, ...]
    tx_logs: tuple[OracleTxLogEntry, ...]
    tx_logs_complete: bool
    short_book: OracleBookQuote | None
    long_book: OracleBookQuote | None
    short_spec: OracleInstrumentSpec
    long_spec: OracleInstrumentSpec
    subaccount_id: str = ""
    api_error: str | None = None
    rate_limited_until_ms: int | None = None
    balance_btc: Decimal | None = None
    portfolio_margining_enabled: bool = False
    margin_model: str = "SM"

    def __post_init__(self) -> None:
        if self.balance_btc is None:
            object.__setattr__(self, "balance_btc", self.equity_btc)
        else:
            object.__setattr__(self, "balance_btc", to_dec(self.balance_btc))
        if not self.margin_model:
            object.__setattr__(self, "margin_model", "PM" if self.portfolio_margining_enabled else "SM")

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)


def _tick_bands(tick_size: Decimal, tick_size_steps: Sequence[OracleTickStep]) -> list[tuple[Decimal, Decimal]]:
    bands: list[tuple[Decimal, Decimal]] = [(ZERO, tick_size)]
    for st in sorted(tick_size_steps, key=lambda x: x.above_price):
        bands.append((st.above_price, st.tick_size))
    return bands


def _band_strictly_above(price: Decimal, bands: Sequence[tuple[Decimal, Decimal]]) -> tuple[Decimal, Decimal]:
    chosen = bands[0]
    for thresh, tick in bands[1:]:
        if price > thresh:
            chosen = (thresh, tick)
    return chosen


def is_valid_tick_for_spec(price: Decimal, spec: OracleInstrumentSpec) -> bool:
    if price <= ZERO:
        return False
    bands = _tick_bands(spec.tick_size, spec.tick_size_steps)
    thresh, tick = _band_strictly_above(price, bands)
    return (price - thresh) % tick == ZERO


def round_tick_down_for_spec(price: Decimal, spec: OracleInstrumentSpec) -> Decimal:
    if price <= ZERO:
        return ZERO
    bands = _tick_bands(spec.tick_size, spec.tick_size_steps)
    thresh, tick = _band_strictly_above(price, bands)
    steps = ((price - thresh) / tick).to_integral_value(rounding=ROUND_DOWN)
    return (thresh + steps * tick).quantize(SAT)


def round_tick_up_for_spec(price: Decimal, spec: OracleInstrumentSpec) -> Decimal:
    if price <= ZERO:
        return ZERO
    bands = _tick_bands(spec.tick_size, spec.tick_size_steps)
    thresh, tick = _band_strictly_above(price, bands)
    steps = ((price - thresh) / tick).to_integral_value(rounding=ROUND_CEILING)
    cand = (thresh + steps * tick).quantize(SAT)
    for idx in range(len(bands) - 1):
        b_thresh, _ = bands[idx]
        next_thresh, next_tick = bands[idx + 1]
        if thresh == b_thresh and cand > next_thresh:
            n2 = ((cand - next_thresh) / next_tick).to_integral_value(rounding=ROUND_CEILING)
            cand = (next_thresh + n2 * next_tick).quantize(SAT)
    return cand


def step_tick_up_for_spec(price: Decimal, spec: OracleInstrumentSpec) -> Decimal:
    aligned = round_tick_down_for_spec(price, spec)
    bands = _tick_bands(spec.tick_size, spec.tick_size_steps)
    chosen = bands[0]
    for thresh, tick in bands[1:]:
        if aligned >= thresh:
            chosen = (thresh, tick)
    thresh, tick = chosen
    n = ((aligned - thresh) / tick).to_integral_value(rounding=ROUND_DOWN)
    return (thresh + (n + 1) * tick).quantize(SAT)


def step_tick_down_for_spec(price: Decimal, spec: OracleInstrumentSpec) -> Decimal:
    aligned = round_tick_up_for_spec(price, spec)
    if aligned <= ZERO:
        return ZERO
    bands = _tick_bands(spec.tick_size, spec.tick_size_steps)
    thresh, tick = _band_strictly_above(aligned, bands)
    n = ((aligned - thresh) / tick).to_integral_value(rounding=ROUND_DOWN)
    cand = (thresh + (n - 1) * tick).quantize(SAT)
    return cand if cand > ZERO else ZERO


def compute_option_trade_fee_btc(
    price_btc: Decimal,
    qty_btc: Decimal,
    spec: OracleInstrumentSpec,
    *,
    is_maker: bool = False,
) -> Decimal:
    if price_btc <= ZERO or qty_btc <= ZERO:
        return ZERO
    if spec.kind == "future":
        rate = spec.maker_commission if is_maker else spec.taker_commission
        return ceil_1e8(qty_btc * rate)
    rate = spec.maker_commission if is_maker else spec.taker_commission
    per_btc = min(rate, spec.fee_cap_ratio * price_btc)
    return ceil_1e8(qty_btc * per_btc)


def compute_delivery_fee_btc(
    unit_payoff_btc: Decimal,
    qty_btc: Decimal,
    settlement_period: str,
    *,
    delivery_fee_rate: Decimal = Decimal("0.00015"),
    fee_cap_ratio: Decimal = Decimal("0.125"),
) -> Decimal:
    sp = settlement_period.lower()
    if sp == "day" or unit_payoff_btc <= ZERO or qty_btc <= ZERO or delivery_fee_rate <= ZERO:
        return ZERO
    per_btc = min(delivery_fee_rate, fee_cap_ratio * unit_payoff_btc)
    return ceil_1e8(qty_btc * per_btc)


def _normalize_steps(
    tick_size_steps: Sequence[Any] | None,
) -> tuple[OracleTickStep, ...]:
    if not tick_size_steps:
        return ()
    out: list[OracleTickStep] = []
    for item in tick_size_steps:
        if isinstance(item, OracleTickStep):
            out.append(item)
        elif isinstance(item, Mapping):
            out.append(OracleTickStep(above_price=to_dec(item["above_price"]), tick_size=to_dec(item["tick_size"])))
        else:
            above, tick = item
            out.append(OracleTickStep(above_price=to_dec(above), tick_size=to_dec(tick)))
    return tuple(sorted(out, key=lambda x: x.above_price))


def _derive_future_name(option_instrument: str, expiry_ms: int) -> str:
    parts = option_instrument.split("-")
    if len(parts) >= 2 and parts[0] and parts[1]:
        return f"{parts[0]}-{parts[1]}"
    return f"BTC-FUT-{expiry_ms}"


@dataclass
class _MutableOrder:
    order_id: str
    label: str
    instrument_name: str
    direction: str
    amount: Decimal
    filled_amount: Decimal
    notional_filled: Decimal
    price: Decimal
    order_state: str
    time_in_force: str
    reduce_only: bool
    post_only: bool
    creation_timestamp_ms: int
    last_update_timestamp_ms: int
    reject_reason: str | None = None
    history_lag_remaining: int = 0

    def to_immutable(self) -> OracleExchangeOrder:
        avg = (self.notional_filled / self.filled_amount) if self.filled_amount > ZERO else None
        return OracleExchangeOrder(
            order_id=self.order_id,
            label=self.label,
            instrument_name=self.instrument_name,
            direction=self.direction,
            amount=self.amount,
            filled_amount=self.filled_amount,
            price=self.price,
            average_price=avg,
            order_state=self.order_state,
            time_in_force=self.time_in_force,
            reduce_only=self.reduce_only,
            post_only=self.post_only,
            creation_timestamp_ms=self.creation_timestamp_ms,
            last_update_timestamp_ms=self.last_update_timestamp_ms,
            reject_reason=self.reject_reason,
        )

    @property
    def remaining_amount(self) -> Decimal:
        return max(ZERO, self.amount - self.filled_amount)


@dataclass
class _MutablePosition:
    instrument_name: str
    kind: str
    size: Decimal
    average_price: Decimal | None = None


@dataclass
class _DelayedSubmit:
    deliver_at_ms: int
    args: dict[str, Any]


class SimulatedDeribitVenue:
    """In-memory simulated Deribit BTC inverse-option/future venue with fault injection."""

    def __init__(
        self,
        *,
        initial_time_ms: int = 1_780_000_000_000,
        initial_balance_btc: Decimal | str | int | float = "1.0",
        spot_usd: Decimal | str | int | float = "90000",
        subaccount_id: str = "sim-sub-1",
        clock_drift_ms: int = 0,
        reduce_only_clamp: bool = True,
        order_state_by_label_ttl_ms: int = DEFAULT_ORDER_STATE_LABEL_TTL_MS,
        archive_age_ms: int = DEFAULT_ARCHIVE_AGE_MS,
        quantize_settlement_to_sat: bool = False,
        portfolio_margining_enabled: bool = False,
        margin_model: str = "SM",
    ) -> None:
        self._server_time_ms = int(initial_time_ms)
        self.clock_drift_ms = int(clock_drift_ms)
        self.subaccount_id = subaccount_id
        self.reduce_only_clamp = bool(reduce_only_clamp)
        self.order_state_by_label_ttl_ms = int(order_state_by_label_ttl_ms)
        self.archive_age_ms = int(archive_age_ms)
        self.quantize_settlement_to_sat = bool(quantize_settlement_to_sat)

        if portfolio_margining_enabled and margin_model == "SM":
            self.portfolio_margining_enabled = True
            self.margin_model = "PM"
        elif not portfolio_margining_enabled and str(margin_model).upper() in {"PM", "CROSS_PM", "SEGREGATED_PM"}:
            self.portfolio_margining_enabled = True
            self.margin_model = str(margin_model)
        else:
            self.portfolio_margining_enabled = bool(portfolio_margining_enabled)
            self.margin_model = str(margin_model)

        self.initial_balance_btc = to_dec(initial_balance_btc)
        self.cash_balance_btc = self.initial_balance_btc
        self.total_fees_btc = ZERO
        self.spot_usd = to_dec(spot_usd)

        self._specs: dict[str, OracleInstrumentSpec] = {}
        self._books: dict[str, OracleBookQuote] = {}
        self._positions: dict[str, _MutablePosition] = {}
        self._orders: dict[str, _MutableOrder] = {}
        self._order_seq = 0
        self._trade_seq = 0
        self._tx_seq = 0

        self._trades: list[OracleVenueTrade] = []
        self._tx_logs: list[OracleTxLogEntry] = []
        self._delayed_tx_logs: list[OracleTxLogEntry] = []
        self._delayed_submits: list[_DelayedSubmit] = []

        self.timed_out_submits: list[dict[str, Any]] = []
        self.rejected_submits: list[dict[str, Any]] = []
        self.delayed_submit_results: list[dict[str, Any]] = []

        # Fault-injection state
        self._drop_next_submit_ack: dict[str, Any] | None = None
        self._delay_submit_until_ms: int | None = None
        self._delay_submit_raise_timeout: bool = True
        self._drop_next_cancel_ack: dict[str, Any] | None = None
        self._rate_limit_queue: list[tuple[int, int]] = []  # (code, retry_after_ms)
        self._reject_10009_remaining: int = 0
        self._order_state_error_queue: list[tuple[int, str]] = []
        self._label_query_error_queue: list[tuple[int, str]] = []
        self._inst_history_error_queue: list[tuple[int, str]] = []
        self._archive_migration_hides: dict[str, int] = {}
        self._partial_fill_caps: dict[str, Decimal] = {}
        self._history_pagination_lag_cycles: int = 0
        self._hold_tx_logs: bool = False
        self._margin_override: dict[str, Decimal | None] = {}

    # ------------------------------------------------------------------
    # Clock management
    # ------------------------------------------------------------------
    @property
    def server_time_ms(self) -> int:
        return self._server_time_ms

    @server_time_ms.setter
    def server_time_ms(self, value: int) -> None:
        self.set_server_time(value)

    @property
    def history_pagination_lag_cycles(self) -> int:
        return self._history_pagination_lag_cycles

    @history_pagination_lag_cycles.setter
    def history_pagination_lag_cycles(self, cycles: int) -> None:
        self.inject_history_pagination_lag(cycles)

    @property
    def delay_submit_until_ms(self) -> int | None:
        return self._delay_submit_until_ms

    @delay_submit_until_ms.setter
    def delay_submit_until_ms(self, value: int | None) -> None:
        self._delay_submit_until_ms = int(value) if value is not None else None

    def get_server_time(self) -> int:
        self._maybe_raise_rate_limit()
        return self._server_time_ms

    def client_clock_ms(self) -> int:
        return self._server_time_ms + self.clock_drift_ms

    def set_clock_drift(self, drift_ms: int) -> None:
        self.clock_drift_ms = int(drift_ms)

    def advance_time(self, delta_ms: int) -> int:
        if delta_ms < 0:
            raise ValueError(f"advance_time requires delta_ms >= 0, got {delta_ms}")
        return self.set_server_time(self._server_time_ms + int(delta_ms))

    def set_server_time(self, timestamp_ms: int) -> int:
        self._server_time_ms = int(timestamp_ms)
        self._flush_delayed_submits_due()
        return self._server_time_ms

    # ------------------------------------------------------------------
    # Instruments, books, and margin configuration
    # ------------------------------------------------------------------
    def add_option_instrument(
        self,
        instrument_name: str,
        *,
        strike_usd: Decimal | str | int | float,
        option_type: str,
        expiry_ms: int,
        settlement_period: str = "day",
        min_trade_amount: Decimal | str | int | float = "0.1",
        tick_size: Decimal | str | int | float = "0.0001",
        tick_size_steps: Sequence[Any] = (("0.005", "0.0005"),),
        maker_commission: Decimal | str | int | float = "0.0003",
        taker_commission: Decimal | str | int | float = "0.0003",
        fee_cap_ratio: Decimal | str | int | float = "0.125",
        delivery_fee_rate: Decimal | str | int | float | None = None,
    ) -> OracleInstrumentSpec:
        sp = settlement_period.lower()
        opt = option_type.lower()
        if opt not in ("put", "call"):
            raise ValueError(f"invalid option_type: {option_type}")
        deliv = ZERO if sp == "day" else Decimal("0.00015")
        if delivery_fee_rate is not None:
            deliv = to_dec(delivery_fee_rate)
        spec = OracleInstrumentSpec(
            instrument_name=instrument_name,
            kind="option",
            settlement_period=sp,
            strike_usd=to_dec(strike_usd),
            option_type=opt,
            expiry_ms=int(expiry_ms),
            tick_size=to_dec(tick_size),
            min_trade_amount=to_dec(min_trade_amount),
            tick_size_steps=_normalize_steps(tick_size_steps),
            maker_commission=to_dec(maker_commission),
            taker_commission=to_dec(taker_commission),
            fee_cap_ratio=to_dec(fee_cap_ratio),
            delivery_fee_rate=deliv,
        )
        self._specs[instrument_name] = spec
        return spec

    def add_future_instrument(
        self,
        instrument_name: str,
        *,
        expiry_ms: int,
        settlement_period: str = "week",
        min_trade_amount: Decimal | str | int | float = "10",
        tick_size: Decimal | str | int | float = "0.5",
        maker_commission: Decimal | str | int | float = "0",
        taker_commission: Decimal | str | int | float = "0.0005",
        delivery_fee_rate: Decimal | str | int | float = "0.00015",
    ) -> OracleInstrumentSpec:
        spec = OracleInstrumentSpec(
            instrument_name=instrument_name,
            kind="future",
            settlement_period=settlement_period.lower(),
            strike_usd=None,
            option_type=None,
            expiry_ms=int(expiry_ms),
            tick_size=to_dec(tick_size),
            min_trade_amount=to_dec(min_trade_amount),
            tick_size_steps=(),
            maker_commission=to_dec(maker_commission),
            taker_commission=to_dec(taker_commission),
            fee_cap_ratio=Decimal("0.125"),
            delivery_fee_rate=to_dec(delivery_fee_rate),
        )
        self._specs[instrument_name] = spec
        return spec

    def set_spot_usd(self, spot_usd: Decimal | str | int | float) -> None:
        self.spot_usd = to_dec(spot_usd)

    def set_book(
        self,
        instrument_name: str,
        *,
        bid: Decimal | str | int | float | None = None,
        bid_size: Decimal | str | int | float = "0",
        ask: Decimal | str | int | float | None = None,
        ask_size: Decimal | str | int | float = "0",
        bid_price: Decimal | str | int | float | None = None,
        bid_amount: Decimal | str | int | float | None = None,
        ask_price: Decimal | str | int | float | None = None,
        ask_amount: Decimal | str | int | float | None = None,
        bids: Sequence[tuple[Any, Any]] | None = None,
        asks: Sequence[tuple[Any, Any]] | None = None,
        mark_price: Decimal | str | int | float | None = None,
        min_price: Decimal | str | int | float | None = None,
        max_price: Decimal | str | int | float | None = None,
        status: OracleBookStatus | str | None = None,
        data_gap: bool = False,
        gap_reason: str | None = None,
        timestamp_ms: int | None = None,
        fetched_at_ms: int | None = None,
        match_resting: bool = True,
    ) -> OracleBookQuote:
        explicit_bid = bid_price is not None or bid is not None
        explicit_ask = ask_price is not None or ask is not None
        b_px = _opt_dec(bid_price if bid_price is not None else bid)
        b_sz = to_dec(bid_amount if bid_amount is not None else bid_size)
        a_px = _opt_dec(ask_price if ask_price is not None else ask)
        a_sz = to_dec(ask_amount if ask_amount is not None else ask_size)

        if bids is not None:
            norm_bids = _normalize_book_levels(bids, side="bid")
            if not explicit_bid:
                b_px = norm_bids[0][0] if norm_bids else None
                b_sz = norm_bids[0][1] if norm_bids else ZERO
        else:
            norm_bids = ((b_px, b_sz),) if (b_px is not None and b_px > ZERO and b_sz > ZERO) else ()

        if asks is not None:
            norm_asks = _normalize_book_levels(asks, side="ask")
            if not explicit_ask:
                a_px = norm_asks[0][0] if norm_asks else None
                a_sz = norm_asks[0][1] if norm_asks else ZERO
        else:
            norm_asks = ((a_px, a_sz),) if (a_px is not None and a_px > ZERO and a_sz > ZERO) else ()

        if b_px is None or b_px <= ZERO or b_sz <= ZERO:
            b_px = None
            b_sz = ZERO
        if a_px is None or a_px <= ZERO or a_sz <= ZERO:
            a_px = None
            a_sz = ZERO

        if data_gap or (status is not None and str(status) == OracleBookStatus.DATA_GAP.value):
            resolved_status = OracleBookStatus.DATA_GAP
        elif status is not None:
            resolved_status = OracleBookStatus(str(status))
        elif b_px is None or a_px is None:
            resolved_status = OracleBookStatus.EMPTY_SIDE
        else:
            resolved_status = OracleBookStatus.OK

        if resolved_status == OracleBookStatus.DATA_GAP:
            if bids is None:
                norm_bids = ()
            if asks is None:
                norm_asks = ()

        quote = OracleBookQuote(
            instrument_name=instrument_name,
            bid_price=b_px,
            bid_amount=b_sz,
            ask_price=a_px,
            ask_amount=a_sz,
            mark_price=_opt_dec(mark_price),
            min_price=_opt_dec(min_price),
            max_price=_opt_dec(max_price),
            timestamp_ms=int(self._server_time_ms if timestamp_ms is None else timestamp_ms),
            status=resolved_status,
            gap_reason=gap_reason if resolved_status == OracleBookStatus.DATA_GAP else None,
            fetched_at_ms=int(self.client_clock_ms() if fetched_at_ms is None else fetched_at_ms),
            bids=norm_bids,
            asks=norm_asks,
        )
        self._books[instrument_name] = quote
        if match_resting and resolved_status != OracleBookStatus.DATA_GAP:
            self._match_resting_against_book(instrument_name)
        return self._books[instrument_name]

    def get_order_book(self, instrument_name: str, depth: int = 5) -> dict[str, Any]:
        self._maybe_raise_rate_limit()
        book = self._books.get(instrument_name)
        if book is None and instrument_name not in self._specs:
            raise DeribitApiError(10001, f"instrument_not_found: {instrument_name}")
        eff_depth = max(0, int(depth))
        if book is None:
            return {
                "instrument_name": instrument_name,
                "bids": [],
                "asks": [],
                "best_bid_price": None,
                "best_bid_amount": ZERO,
                "best_ask_price": None,
                "best_ask_amount": ZERO,
                "mark_price": None,
                "min_price": None,
                "max_price": None,
                "index_price": self.spot_usd,
                "timestamp": int(self._server_time_ms),
                "fetched_at_ms": int(self.client_clock_ms()),
                "state": "open",
                "status": OracleBookStatus.EMPTY_SIDE.value,
                "gap_reason": None,
            }
        bids_slice = [[px, sz] for px, sz in book.bids[:eff_depth]]
        asks_slice = [[px, sz] for px, sz in book.asks[:eff_depth]]
        return {
            "instrument_name": instrument_name,
            "bids": bids_slice,
            "asks": asks_slice,
            "best_bid_price": book.bid_price,
            "best_bid_amount": book.bid_amount,
            "best_ask_price": book.ask_price,
            "best_ask_amount": book.ask_amount,
            "mark_price": book.mark_price,
            "min_price": book.min_price,
            "max_price": book.max_price,
            "index_price": self.spot_usd,
            "timestamp": int(book.timestamp_ms),
            "fetched_at_ms": int(book.fetched_at_ms),
            "state": "open",
            "status": book.status.value,
            "gap_reason": book.gap_reason,
        }

    def set_margin_override(
        self,
        *,
        initial_margin_btc: Decimal | str | int | float | None = None,
        maintenance_margin_btc: Decimal | str | int | float | None = None,
        available_funds_btc: Decimal | str | int | float | None = None,
        equity_btc: Decimal | str | int | float | None = None,
        balance_btc: Decimal | str | int | float | None = None,
        portfolio_margining_enabled: bool | None = None,
        margin_model: str | None = None,
    ) -> None:
        self._margin_override = {
            "initial_margin_btc": _opt_dec(initial_margin_btc),
            "maintenance_margin_btc": _opt_dec(maintenance_margin_btc),
            "available_funds_btc": _opt_dec(available_funds_btc),
            "equity_btc": _opt_dec(equity_btc),
            "balance_btc": _opt_dec(balance_btc),
        }
        if portfolio_margining_enabled is not None and margin_model is not None:
            self.portfolio_margining_enabled = bool(portfolio_margining_enabled)
            self.margin_model = str(margin_model)
        elif portfolio_margining_enabled is not None:
            self.portfolio_margining_enabled = bool(portfolio_margining_enabled)
            self.margin_model = "PM" if self.portfolio_margining_enabled else "SM"
        elif margin_model is not None:
            self.margin_model = str(margin_model)
            self.portfolio_margining_enabled = str(margin_model).upper() in {"PM", "CROSS_PM", "SEGREGATED_PM"}

    # ------------------------------------------------------------------
    # Fault injection hooks
    # ------------------------------------------------------------------
    def inject_drop_next_submit_ack(
        self,
        *,
        venue_received: bool = True,
        error_message: str = "network_timeout_submit_ack",
    ) -> None:
        self._drop_next_submit_ack = {
            "venue_received": bool(venue_received),
            "error_message": str(error_message),
        }

    def inject_delay_submit_until_ms(
        self,
        deliver_at_ms: int,
        *,
        raise_timeout: bool = True,
    ) -> None:
        self._delay_submit_until_ms = int(deliver_at_ms)
        self._delay_submit_raise_timeout = bool(raise_timeout)

    def inject_drop_next_cancel_ack(
        self,
        *,
        concurrent_fill_qty: Decimal | str | int | float | None = None,
        concurrent_fill_price: Decimal | str | int | float | None = None,
        concurrent_liquidity: str = "T",
        cancel_succeeded_if_remaining: bool = True,
        error_message: str = "network_timeout_cancel_ack",
    ) -> None:
        self._drop_next_cancel_ack = {
            "concurrent_fill_qty": _opt_dec(concurrent_fill_qty),
            "concurrent_fill_price": _opt_dec(concurrent_fill_price),
            "concurrent_liquidity": str(concurrent_liquidity),
            "cancel_succeeded_if_remaining": bool(cancel_succeeded_if_remaining),
            "error_message": str(error_message),
        }

    def inject_429(self, count: int = 1, *, retry_after_ms: int = 1000) -> None:
        for _ in range(max(0, int(count))):
            self._rate_limit_queue.append((429, int(retry_after_ms)))

    def inject_10028(self, count: int = 1, *, retry_after_ms: int = 1000) -> None:
        for _ in range(max(0, int(count))):
            self._rate_limit_queue.append((10028, int(retry_after_ms)))

    def inject_10009(self, count: int = 1) -> None:
        n = int(count)
        if n <= 0:
            self._reject_10009_remaining = 0
        else:
            self._reject_10009_remaining += n

    def inject_order_state_error(
        self,
        count: int = 1,
        code: int = 10028,
        message: str = "too_many_requests",
    ) -> None:
        n = int(count)
        if n <= 0:
            self._order_state_error_queue.clear()
        else:
            for _ in range(n):
                self._order_state_error_queue.append((int(code), str(message)))

    def inject_label_query_error(
        self,
        count: int = 1,
        code: int = 10028,
        message: str = "too_many_requests",
    ) -> None:
        n = int(count)
        if n <= 0:
            self._label_query_error_queue.clear()
        else:
            for _ in range(n):
                self._label_query_error_queue.append((int(code), str(message)))

    def inject_inst_history_error(
        self,
        count: int = 1,
        code: int = 10028,
        message: str = "too_many_requests",
    ) -> None:
        n = int(count)
        if n <= 0:
            self._inst_history_error_queue.clear()
        else:
            for _ in range(n):
                self._inst_history_error_queue.append((int(code), str(message)))

    def inject_archive_migration_hide(self, label_or_oid: str, until_ms: int) -> None:
        key = str(label_or_oid)
        if int(until_ms) <= 0:
            self._archive_migration_hides.pop(key, None)
        else:
            self._archive_migration_hides[key] = int(until_ms)

    def seed_closed_orders(
        self,
        instrument_name: str,
        count: int,
        *,
        age_ms: int = 60_000,
    ) -> list[OracleOrder]:
        spec = self._specs.get(instrument_name)
        lot = spec.min_trade_amount if spec is not None else Decimal("0.1")
        px = spec.tick_size if spec is not None else Decimal("0.0001")
        ts_ms = int(self._server_time_ms) - int(age_ms)
        seeded: list[OracleOrder] = []
        for _ in range(max(0, int(count))):
            oid = self._next_order_id()
            order = _MutableOrder(
                order_id=oid,
                label=f"seed:noise:{oid.lower()}",
                instrument_name=instrument_name,
                direction="buy",
                amount=lot,
                filled_amount=ZERO,
                notional_filled=ZERO,
                price=px,
                order_state="cancelled",
                time_in_force="GTC",
                reduce_only=False,
                post_only=False,
                creation_timestamp_ms=ts_ms,
                last_update_timestamp_ms=ts_ms,
            )
            self._orders[oid] = order
            seeded.append(order.to_immutable())
        return seeded

    def inject_partial_fill_cap(
        self,
        instrument_name: str,
        max_fill_qty: Decimal | str | int | float | None,
    ) -> None:
        if max_fill_qty is None:
            self._partial_fill_caps.pop(instrument_name, None)
        else:
            self._partial_fill_caps[instrument_name] = to_dec(max_fill_qty)

    def inject_history_pagination_lag(self, cycles: int = 1) -> None:
        self._history_pagination_lag_cycles = max(0, int(cycles))

    def inject_delayed_tx_log(self, hold: bool = True) -> None:
        self._hold_tx_logs = bool(hold)

    def force_external_position(
        self,
        instrument_name: str,
        qty: Decimal | str | int | float,
        *,
        kind: str = "option",
        average_price: Decimal | str | int | float | None = None,
        expiry_ms: int | None = None,
    ) -> None:
        signed_qty = to_dec(qty)
        if instrument_name not in self._specs:
            exp = int(expiry_ms if expiry_ms is not None else self._server_time_ms + 86_400_000)
            if kind == "future":
                self.add_future_instrument(instrument_name, expiry_ms=exp)
            else:
                self.add_option_instrument(
                    instrument_name,
                    strike_usd=self.spot_usd,
                    option_type="put",
                    expiry_ms=exp,
                )
        if signed_qty == ZERO:
            self._positions.pop(instrument_name, None)
        else:
            spec = self._specs[instrument_name]
            self._positions[instrument_name] = _MutablePosition(
                instrument_name=instrument_name,
                kind=spec.kind,
                size=signed_qty,
                average_price=_opt_dec(average_price),
            )
        self._reconcile_reduce_only_resting(instrument_name)

    def force_external_future(
        self,
        instrument_name: str,
        qty: Decimal | str | int | float,
        *,
        average_price: Decimal | str | int | float | None = None,
        expiry_ms: int | None = None,
        settlement_period: str = "week",
    ) -> None:
        if instrument_name not in self._specs:
            exp = int(expiry_ms if expiry_ms is not None else self._server_time_ms + 86_400_000)
            self.add_future_instrument(
                instrument_name,
                expiry_ms=exp,
                settlement_period=settlement_period,
            )
        self.force_external_position(
            instrument_name,
            qty,
            kind="future",
            average_price=average_price,
            expiry_ms=expiry_ms,
        )

    # ------------------------------------------------------------------
    # Order submission, cancellation, and matching engine
    # ------------------------------------------------------------------
    def _maybe_raise_rate_limit(self) -> None:
        if self._rate_limit_queue:
            code, retry_after_ms = self._rate_limit_queue.pop(0)
            msg = "too_many_requests" if code == 10028 else "rate_limit_exceeded"
            raise RateLimitError(code, msg, retry_after_ms=retry_after_ms)

    @staticmethod
    def _raise_injected_api_error(code: int, message: str) -> None:
        if int(code) in (429, 10028):
            raise RateLimitError(int(code), str(message))
        raise DeribitApiError(int(code), str(message))

    def _is_archive_migration_hidden(self, order: _MutableOrder) -> bool:
        if not self._archive_migration_hides:
            return False
        oid_until = self._archive_migration_hides.get(order.order_id)
        if oid_until is not None and self._server_time_ms < oid_until:
            return True
        if order.label:
            lbl_until = self._archive_migration_hides.get(order.label)
            if lbl_until is not None and self._server_time_ms < lbl_until:
                return True
        return False

    def _next_order_id(self) -> str:
        self._order_seq += 1
        return f"ORD-{self._order_seq:06d}"

    def _next_trade_id(self) -> str:
        self._trade_seq += 1
        return f"TRD-{self._trade_seq:06d}"

    def _next_tx_id(self) -> int:
        self._tx_seq += 1
        return self._tx_seq

    def _emit_tx_log(self, entry: OracleTxLogEntry) -> None:
        if self._hold_tx_logs:
            self._delayed_tx_logs.append(entry)
        else:
            self._tx_logs.append(entry)

    def submit_order(
        self,
        instrument_name: str,
        direction: str,
        amount: Decimal | str | int | float,
        price: Decimal | str | int | float,
        *,
        label: str = "",
        time_in_force: str = "GTC",
        reduce_only: bool = False,
        post_only: bool = False,
        reject_post_only: bool = True,
        valid_until: int | None = None,
        valid_until_ms: int | None = None,
    ) -> OracleExchangeOrder | None:
        self._maybe_raise_rate_limit()
        eff_valid_until = valid_until_ms if valid_until_ms is not None else valid_until

        if self._delay_submit_until_ms is not None and self._delay_submit_until_ms > self._server_time_ms:
            deliver_at = self._delay_submit_until_ms
            raise_timeout = self._delay_submit_raise_timeout
            self._delay_submit_until_ms = None
            self._delayed_submits.append(
                _DelayedSubmit(
                    deliver_at_ms=deliver_at,
                    args={
                        "instrument_name": instrument_name,
                        "direction": direction,
                        "amount": amount,
                        "price": price,
                        "label": label,
                        "time_in_force": time_in_force,
                        "reduce_only": reduce_only,
                        "post_only": post_only,
                        "reject_post_only": reject_post_only,
                        "valid_until_ms": eff_valid_until,
                    },
                )
            )
            if raise_timeout:
                raise SimulatedNetworkError("submit_delayed_in_flight", phase="submit_ack")
            return None

        drop_ack = self._drop_next_submit_ack
        if drop_ack is not None:
            self._drop_next_submit_ack = None
            if not drop_ack["venue_received"]:
                raise SimulatedNetworkError(drop_ack["error_message"], phase="submit_ack")

        try:
            order = self._process_submit_at(
                processing_time_ms=self._server_time_ms,
                instrument_name=instrument_name,
                direction=direction,
                amount=amount,
                price=price,
                label=label,
                time_in_force=time_in_force,
                reduce_only=reduce_only,
                post_only=post_only,
                reject_post_only=reject_post_only,
                valid_until_ms=eff_valid_until,
            )
        except DeribitApiError:
            if drop_ack is not None:
                raise SimulatedNetworkError(drop_ack["error_message"], phase="submit_ack")
            raise

        if drop_ack is not None:
            raise SimulatedNetworkError(drop_ack["error_message"], phase="submit_ack")
        return order

    def buy(self, instrument_name: str, amount: Any, price: Any, **kwargs: Any) -> OracleExchangeOrder | None:
        return self.submit_order(instrument_name, "buy", amount, price, **kwargs)

    def sell(self, instrument_name: str, amount: Any, price: Any, **kwargs: Any) -> OracleExchangeOrder | None:
        return self.submit_order(instrument_name, "sell", amount, price, **kwargs)

    def _flush_delayed_submits_due(self) -> None:
        due = [d for d in self._delayed_submits if d.deliver_at_ms <= self._server_time_ms]
        if not due:
            return
        self._delayed_submits = [d for d in self._delayed_submits if d.deliver_at_ms > self._server_time_ms]
        for item in sorted(due, key=lambda d: d.deliver_at_ms):
            try:
                order = self._process_submit_at(processing_time_ms=item.deliver_at_ms, **item.args)
                self.delayed_submit_results.append(
                    {"deliver_at_ms": item.deliver_at_ms, "status": "ok", "order_id": order.order_id, "order_state": order.order_state}
                )
            except DeribitApiError as exc:
                self.delayed_submit_results.append(
                    {"deliver_at_ms": item.deliver_at_ms, "status": "error", "code": exc.code, "message": exc.message}
                )

    def _process_submit_at(
        self,
        *,
        processing_time_ms: int,
        instrument_name: str,
        direction: str,
        amount: Decimal | str | int | float,
        price: Decimal | str | int | float,
        label: str,
        time_in_force: str,
        reduce_only: bool,
        post_only: bool,
        reject_post_only: bool,
        valid_until_ms: int | None,
    ) -> OracleExchangeOrder:
        # 1. Deribit valid_until check at start of matching engine processing
        if valid_until_ms is not None and processing_time_ms >= int(valid_until_ms):
            self.timed_out_submits.append(
                {
                    "instrument_name": instrument_name,
                    "label": label,
                    "processing_time_ms": processing_time_ms,
                    "valid_until_ms": int(valid_until_ms),
                }
            )
            raise DeribitApiError(13888, "timed_out")

        spec = self._specs.get(instrument_name)
        if spec is None:
            raise DeribitApiError(10001, f"instrument_not_found: {instrument_name}")

        side = direction.lower()
        if side not in ("buy", "sell"):
            raise DeribitApiError(10001, f"invalid_direction: {direction}")

        tif = time_in_force.upper()
        if tif not in ("GTC", "IOC"):
            raise DeribitApiError(10001, f"unsupported_time_in_force: {time_in_force}")

        qty = to_dec(amount)
        if qty <= ZERO or (qty % spec.min_trade_amount) != ZERO:
            raise DeribitApiError(10001, f"invalid_amount: {qty} vs lot {spec.min_trade_amount}")

        px = to_dec(price)
        if not is_valid_tick_for_spec(px, spec):
            raise DeribitApiError(10002, f"invalid_tick_size: {px}")

        if self._reject_10009_remaining > 0:
            self._reject_10009_remaining -= 1
            self.rejected_submits.append(
                {
                    "instrument_name": instrument_name,
                    "label": label,
                    "reason": "not_enough_funds",
                    "code": 10009,
                }
            )
            raise DeribitApiError(10009, "not_enough_funds")

        book = self._books.get(instrument_name)
        if book is not None:
            if book.min_price is not None and px < book.min_price:
                raise DeribitApiError(10005, f"price_too_low: {px} < {book.min_price}")
            if book.max_price is not None and px > book.max_price:
                raise DeribitApiError(10007, f"price_too_high: {px} > {book.max_price}")

        # 2. reduce_only enforcement against position & open same-side closing orders
        if reduce_only:
            qty = self._apply_reduce_only_check(spec, side, qty)

        # 3. post_only check
        if post_only and tif == "IOC":
            raise DeribitApiError(10001, "post_only_incompatible_with_ioc")

        crosses = self._would_cross(book, side, px)
        if post_only and crosses:
            if reject_post_only:
                order = self._create_order(
                    label=label,
                    instrument_name=instrument_name,
                    direction=side,
                    amount=qty,
                    price=px,
                    order_state="rejected",
                    time_in_force=tif,
                    reduce_only=reduce_only,
                    post_only=True,
                    ts_ms=processing_time_ms,
                    reject_reason="reject_post_only",
                )
                self.rejected_submits.append({"order_id": order.order_id, "label": label, "reason": "reject_post_only"})
                return order.to_immutable()
            px = self._adjust_post_only_price(book, spec, side, px)
            if px <= ZERO or (book is not None and book.min_price is not None and px < book.min_price):
                order = self._create_order(
                    label=label,
                    instrument_name=instrument_name,
                    direction=side,
                    amount=qty,
                    price=to_dec(price),
                    order_state="rejected",
                    time_in_force=tif,
                    reduce_only=reduce_only,
                    post_only=True,
                    ts_ms=processing_time_ms,
                    reject_reason="reject_post_only",
                )
                return order.to_immutable()

        order = self._create_order(
            label=label,
            instrument_name=instrument_name,
            direction=side,
            amount=qty,
            price=px,
            order_state="open",
            time_in_force=tif,
            reduce_only=reduce_only,
            post_only=post_only,
            ts_ms=processing_time_ms,
        )

        # 4. Immediate crossing match (Taker)
        if not post_only and crosses and book is not None and book.status != OracleBookStatus.DATA_GAP:
            self._match_taker(order, spec, book, processing_time_ms)

        if order.remaining_amount == ZERO:
            self._close_order(order, "filled", processing_time_ms)
        elif tif == "IOC":
            self._close_order(order, "cancelled", processing_time_ms)

        return order.to_immutable()

    def _apply_reduce_only_check(self, spec: OracleInstrumentSpec, side: str, qty: Decimal) -> Decimal:
        pos = self._positions.get(spec.instrument_name)
        pos_size = pos.size if pos is not None else ZERO
        open_closing = sum(
            (
                o.remaining_amount
                for o in self._orders.values()
                if o.instrument_name == spec.instrument_name
                and o.order_state == "open"
                and o.direction == side
                and o.reduce_only
            ),
            ZERO,
        )
        if side == "buy" and pos_size < ZERO:
            reducible = max(ZERO, -pos_size - open_closing)
        elif side == "sell" and pos_size > ZERO:
            reducible = max(ZERO, pos_size - open_closing)
        else:
            reducible = ZERO

        if reducible <= ZERO:
            self.rejected_submits.append({"instrument_name": spec.instrument_name, "reason": "reduce_only_reject"})
            raise DeribitApiError(11008, "reduce_only_reject")
        if qty > reducible:
            if not self.reduce_only_clamp:
                self.rejected_submits.append({"instrument_name": spec.instrument_name, "reason": "reduce_only_reject"})
                raise DeribitApiError(11008, "reduce_only_reject")
            lots = (reducible / spec.min_trade_amount).to_integral_value(rounding=ROUND_DOWN)
            clamped = lots * spec.min_trade_amount
            if clamped <= ZERO:
                self.rejected_submits.append({"instrument_name": spec.instrument_name, "reason": "reduce_only_reject"})
                raise DeribitApiError(11008, "reduce_only_reject")
            return clamped
        return qty

    def _would_cross(self, book: OracleBookQuote | None, side: str, px: Decimal) -> bool:
        if book is None or book.status == OracleBookStatus.DATA_GAP:
            return False
        if side == "buy":
            return book.ask_price is not None and book.ask_amount > ZERO and px >= book.ask_price
        return book.bid_price is not None and book.bid_amount > ZERO and px <= book.bid_price

    def _adjust_post_only_price(
        self,
        book: OracleBookQuote | None,
        spec: OracleInstrumentSpec,
        side: str,
        px: Decimal,
    ) -> Decimal:
        if book is None:
            return px
        if side == "buy" and book.ask_price is not None and px >= book.ask_price:
            return step_tick_down_for_spec(book.ask_price, spec)
        if side == "sell" and book.bid_price is not None and px <= book.bid_price:
            return step_tick_up_for_spec(book.bid_price, spec)
        return px

    def _create_order(
        self,
        *,
        label: str,
        instrument_name: str,
        direction: str,
        amount: Decimal,
        price: Decimal,
        order_state: str,
        time_in_force: str,
        reduce_only: bool,
        post_only: bool,
        ts_ms: int,
        reject_reason: str | None = None,
    ) -> _MutableOrder:
        order = _MutableOrder(
            order_id=self._next_order_id(),
            label=label,
            instrument_name=instrument_name,
            direction=direction,
            amount=amount,
            filled_amount=ZERO,
            notional_filled=ZERO,
            price=price,
            order_state=order_state,
            time_in_force=time_in_force,
            reduce_only=bool(reduce_only),
            post_only=bool(post_only),
            creation_timestamp_ms=int(ts_ms),
            last_update_timestamp_ms=int(ts_ms),
            reject_reason=reject_reason,
            history_lag_remaining=(
                self._history_pagination_lag_cycles if order_state in ("filled", "cancelled", "rejected") else 0
            ),
        )
        self._orders[order.order_id] = order
        return order

    def _close_order(self, order: _MutableOrder, state: str, ts_ms: int) -> None:
        order.order_state = state
        order.last_update_timestamp_ms = int(ts_ms)
        if self._history_pagination_lag_cycles > 0:
            order.history_lag_remaining = self._history_pagination_lag_cycles

    def _sweep_book_against_order(
        self,
        order: _MutableOrder,
        spec: OracleInstrumentSpec,
        book: OracleBookQuote,
        *,
        liquidity: str,
        ts_ms: int,
    ) -> None:
        side = order.direction
        raw_levels = list(book.asks if side == "buy" else book.bids)
        if not raw_levels:
            top_px = book.ask_price if side == "buy" else book.bid_price
            top_sz = book.ask_amount if side == "buy" else book.bid_amount
            if top_px is not None and top_px > ZERO and top_sz > ZERO:
                raw_levels = [(top_px, top_sz)]
        if not raw_levels:
            return

        head_levels = raw_levels[:5]
        tail_levels = raw_levels[5:]
        cap = self._partial_fill_caps.get(spec.instrument_name)
        filled_in_sweep = ZERO
        new_head: list[tuple[Decimal, Decimal]] = []
        is_maker = liquidity.upper() == "M"

        for idx, (lvl_px, lvl_sz) in enumerate(head_levels):
            price_ok = (order.price >= lvl_px) if side == "buy" else (order.price <= lvl_px)
            if not price_ok or order.remaining_amount <= ZERO:
                new_head.extend(head_levels[idx:])
                break

            max_want = order.remaining_amount
            if cap is not None:
                rem_cap = max(ZERO, cap - filled_in_sweep)
                if rem_cap <= ZERO:
                    new_head.extend(head_levels[idx:])
                    break
                max_want = min(max_want, rem_cap)

            raw_fill = min(max_want, lvl_sz)
            lots = (raw_fill / spec.min_trade_amount).to_integral_value(rounding=ROUND_DOWN)
            fill_qty = lots * spec.min_trade_amount
            if fill_qty <= ZERO:
                new_head.append((lvl_px, lvl_sz))
                if max_want < spec.min_trade_amount:
                    new_head.extend(head_levels[idx + 1 :])
                    break
                continue

            exec_px = order.price if is_maker else lvl_px
            self._execute_fill(order, spec, fill_qty, exec_px, liquidity=liquidity, ts_ms=ts_ms)
            filled_in_sweep += fill_qty
            rem_lvl = max(ZERO, lvl_sz - fill_qty)
            if rem_lvl > ZERO:
                new_head.append((lvl_px, rem_lvl))

        if filled_in_sweep <= ZERO:
            return

        updated_levels = tuple(new_head + tail_levels)
        if side == "buy":
            self._books[spec.instrument_name] = _rebuild_book_after_match(
                book,
                bids=book.bids,
                asks=updated_levels,
                ts_ms=ts_ms,
            )
        else:
            self._books[spec.instrument_name] = _rebuild_book_after_match(
                book,
                bids=updated_levels,
                asks=book.asks,
                ts_ms=ts_ms,
            )

    def _match_taker(
        self,
        order: _MutableOrder,
        spec: OracleInstrumentSpec,
        book: OracleBookQuote,
        ts_ms: int,
    ) -> None:
        self._sweep_book_against_order(order, spec, book, liquidity="T", ts_ms=ts_ms)

    def _match_resting_against_book(self, instrument_name: str) -> None:
        spec = self._specs.get(instrument_name)
        book = self._books.get(instrument_name)
        if spec is None or book is None or book.status == OracleBookStatus.DATA_GAP:
            return
        open_orders = [
            o for o in self._orders.values() if o.instrument_name == instrument_name and o.order_state == "open"
        ]
        for order in open_orders:
            book = self._books.get(instrument_name)
            if book is None or order.remaining_amount <= ZERO or order.order_state != "open":
                continue
            self._sweep_book_against_order(order, spec, book, liquidity="M", ts_ms=self._server_time_ms)
            if order.remaining_amount == ZERO and order.order_state == "open":
                self._close_order(order, "filled", self._server_time_ms)

    def fill_resting_order(
        self,
        order_id_or_label: str,
        amount: Decimal | str | int | float,
        *,
        price: Decimal | str | int | float | None = None,
        liquidity: str = "M",
    ) -> tuple[OracleExchangeOrder, OracleVenueTrade]:
        order = self._find_open_order(order_id_or_label)
        if order is None:
            raise DeribitApiError(10004, f"open_order_not_found: {order_id_or_label}")
        spec = self._specs[order.instrument_name]
        fill_qty = min(to_dec(amount), order.remaining_amount)
        if fill_qty <= ZERO:
            raise DeribitApiError(10001, "invalid_fill_amount")
        fill_px = to_dec(price) if price is not None else order.price
        trade = self._execute_fill(order, spec, fill_qty, fill_px, liquidity=liquidity, ts_ms=self._server_time_ms)
        if order.remaining_amount == ZERO:
            self._close_order(order, "filled", self._server_time_ms)
        return order.to_immutable(), trade

    def _find_open_order(self, order_id_or_label: str) -> _MutableOrder | None:
        if order_id_or_label in self._orders and self._orders[order_id_or_label].order_state == "open":
            return self._orders[order_id_or_label]
        for order in self._orders.values():
            if order.label == order_id_or_label and order.order_state == "open":
                return order
        return None

    def _execute_fill(
        self,
        order: _MutableOrder,
        spec: OracleInstrumentSpec,
        fill_qty: Decimal,
        fill_px: Decimal,
        *,
        liquidity: str,
        ts_ms: int,
    ) -> OracleVenueTrade:
        is_maker = liquidity.upper() == "M"
        fee_btc = compute_option_trade_fee_btc(fill_px, fill_qty, spec, is_maker=is_maker)
        order.filled_amount += fill_qty
        order.notional_filled += fill_qty * fill_px
        order.last_update_timestamp_ms = int(ts_ms)

        trade_id = self._next_trade_id()
        trade = OracleVenueTrade(
            trade_id=trade_id,
            order_id=order.order_id,
            label=order.label,
            instrument_name=order.instrument_name,
            direction=order.direction,
            amount=fill_qty,
            price=fill_px,
            fee=fee_btc,
            fee_currency="BTC",
            timestamp_ms=int(ts_ms),
            liquidity="M" if is_maker else "T",
        )
        self._trades.append(trade)

        cashflow = (fill_qty * fill_px) if order.direction == "sell" else -(fill_qty * fill_px)
        change = cashflow - fee_btc
        self.cash_balance_btc += change
        self.total_fees_btc += fee_btc

        self._emit_tx_log(
            OracleTxLogEntry(
                id=self._next_tx_id(),
                type="trade",
                instrument_name=order.instrument_name,
                timestamp_ms=int(ts_ms),
                amount=fill_qty,
                price=fill_px,
                cashflow=cashflow,
                change=change,
                commission=fee_btc,
                currency="BTC",
                trade_id=trade_id,
                order_id=order.order_id,
            )
        )
        self._apply_position_fill(spec, order.direction, fill_qty, fill_px)
        return trade

    def _apply_position_fill(
        self,
        spec: OracleInstrumentSpec,
        direction: str,
        qty: Decimal,
        px: Decimal,
    ) -> None:
        delta = qty if direction == "buy" else -qty
        existing = self._positions.get(spec.instrument_name)
        old_size = existing.size if existing is not None else ZERO
        old_avg = existing.average_price if existing is not None else None
        new_size = old_size + delta

        if new_size == ZERO:
            self._positions.pop(spec.instrument_name, None)
        elif old_size == ZERO or (old_size > ZERO and delta > ZERO) or (old_size < ZERO and delta < ZERO):
            prev_cost = abs(old_size) * (old_avg if old_avg is not None else px)
            new_avg = (prev_cost + abs(delta) * px) / abs(new_size)
            self._positions[spec.instrument_name] = _MutablePosition(
                instrument_name=spec.instrument_name,
                kind=spec.kind,
                size=new_size,
                average_price=new_avg,
            )
        elif (old_size > ZERO and new_size > ZERO) or (old_size < ZERO and new_size < ZERO):
            self._positions[spec.instrument_name] = _MutablePosition(
                instrument_name=spec.instrument_name,
                kind=spec.kind,
                size=new_size,
                average_price=old_avg,
            )
        else:
            # Position flipped across zero
            self._positions[spec.instrument_name] = _MutablePosition(
                instrument_name=spec.instrument_name,
                kind=spec.kind,
                size=new_size,
                average_price=px,
            )
        self._reconcile_reduce_only_resting(spec.instrument_name)

    def _reconcile_reduce_only_resting(self, instrument_name: str) -> None:
        pos = self._positions.get(instrument_name)
        pos_size = pos.size if pos is not None else ZERO
        for order in self._orders.values():
            if order.instrument_name != instrument_name or order.order_state != "open" or not order.reduce_only:
                continue
            max_close = max(ZERO, -pos_size) if order.direction == "buy" else max(ZERO, pos_size)
            if max_close <= ZERO:
                self._close_order(order, "cancelled", self._server_time_ms)
            elif order.remaining_amount > max_close:
                order.amount = order.filled_amount + max_close
                order.last_update_timestamp_ms = self._server_time_ms

    def cancel(self, order_id: str, *, valid_until: int | None = None) -> OracleExchangeOrder:
        if valid_until is not None:
            raise DeribitApiError(10004, "invalid_params: cancel does not accept valid_until")
        self._maybe_raise_rate_limit()
        order = self._orders.get(order_id)
        if order is None:
            raise DeribitApiError(10004, f"order_not_found: {order_id}")

        drop = self._drop_next_cancel_ack
        if drop is not None:
            self._drop_next_cancel_ack = None
            self._apply_concurrent_cancel_fill(order, drop)
            if order.order_state == "open" and drop["cancel_succeeded_if_remaining"]:
                self._close_order(order, "cancelled", self._server_time_ms)
            raise SimulatedNetworkError(drop["error_message"], phase="cancel_ack")

        if order.order_state == "open":
            self._close_order(order, "cancelled", self._server_time_ms)
        return order.to_immutable()

    def cancel_by_label(self, label: str, *, valid_until: int | None = None) -> int:
        if valid_until is not None:
            raise DeribitApiError(10004, "invalid_params: cancel_by_label does not accept valid_until")
        self._maybe_raise_rate_limit()
        targets = [o for o in self._orders.values() if o.label == label and o.order_state == "open"]

        drop = self._drop_next_cancel_ack
        if drop is not None:
            self._drop_next_cancel_ack = None
            for order in targets:
                self._apply_concurrent_cancel_fill(order, drop)
                if order.order_state == "open" and drop["cancel_succeeded_if_remaining"]:
                    self._close_order(order, "cancelled", self._server_time_ms)
            raise SimulatedNetworkError(drop["error_message"], phase="cancel_ack")

        count = 0
        for order in targets:
            self._close_order(order, "cancelled", self._server_time_ms)
            count += 1
        return count

    def _apply_concurrent_cancel_fill(self, order: _MutableOrder, drop: Mapping[str, Any]) -> None:
        fill_qty_opt = drop.get("concurrent_fill_qty")
        if fill_qty_opt is None or order.order_state != "open":
            return
        spec = self._specs[order.instrument_name]
        fill_qty = min(fill_qty_opt, order.remaining_amount)
        if fill_qty <= ZERO:
            return
        fill_px = drop.get("concurrent_fill_price")
        if fill_px is None:
            fill_px = order.price
        self._execute_fill(
            order,
            spec,
            fill_qty,
            fill_px,
            liquidity=str(drop.get("concurrent_liquidity", "T")),
            ts_ms=self._server_time_ms,
        )
        if order.remaining_amount == ZERO:
            self._close_order(order, "filled", self._server_time_ms)

    # ------------------------------------------------------------------
    # 2026-08-01 two-stage inverse option settlement & delayed tx_log
    # ------------------------------------------------------------------
    def expire_at_settlement(
        self,
        settlement_price_usd: Decimal | str | int | float,
    ) -> tuple[OracleTxLogEntry, ...]:
        """Settle all option positions at expiry using Deribit 2026-08-01 two-stage delivery."""
        S = to_dec(settlement_price_usd)
        if S <= ZERO:
            raise ValueError(f"settlement_price_usd must be > 0, got {S}")

        option_positions = [
            pos
            for pos in list(self._positions.values())
            if pos.kind == "option" and pos.size != ZERO
        ]
        if not option_positions:
            return ()

        max_expiry = max(self._specs[pos.instrument_name].expiry_ms for pos in option_positions)
        if self._server_time_ms < max_expiry:
            self.set_server_time(max_expiry)

        # Cancel resting orders on expiring options
        expiring_names = {pos.instrument_name for pos in option_positions}
        for order in self._orders.values():
            if order.instrument_name in expiring_names and order.order_state == "open":
                self._close_order(order, "cancelled", self._server_time_ms)

        emitted: list[OracleTxLogEntry] = []
        by_expiry: dict[int, list[_MutablePosition]] = {}
        for pos in option_positions:
            spec = self._specs[pos.instrument_name]
            by_expiry.setdefault(spec.expiry_ms, []).append(pos)

        for exp_ms, legs in sorted(by_expiry.items()):
            ts_ms = max(self._server_time_ms, exp_ms)
            itm_details: list[dict[str, Any]] = []

            for pos in legs:
                spec = self._specs[pos.instrument_name]
                K = spec.strike_usd
                if K is None or spec.option_type is None:
                    raise ValueError(f"option missing strike/type: {spec.instrument_name}")
                intrinsic_usd = max(ZERO, S - K) if spec.option_type == "call" else max(ZERO, K - S)
                unit_payoff = intrinsic_usd / S

                if unit_payoff == ZERO:
                    entry = OracleTxLogEntry(
                        id=self._next_tx_id(),
                        type="expiry",
                        instrument_name=spec.instrument_name,
                        timestamp_ms=ts_ms,
                        amount=abs(pos.size),
                        price=ZERO,
                        cashflow=ZERO,
                        change=ZERO,
                        commission=ZERO,
                        currency="BTC",
                        settlement_price=S,
                        info={
                            "side": "long" if pos.size > ZERO else "short",
                            "option_type": spec.option_type,
                            "strike": _dec_str(K),
                            "expiry_ms": exp_ms,
                            "two_stage": False,
                        },
                    )
                    self._emit_tx_log(entry)
                    emitted.append(entry)
                    self._positions.pop(spec.instrument_name, None)
                    continue

                # ITM leg: Stage-1 option exercise/assignment -> same-expiry inverse future
                future_name = _derive_future_name(spec.instrument_name, exp_ms)
                if future_name not in self._specs:
                    self.add_future_instrument(
                        future_name,
                        expiry_ms=exp_ms,
                        settlement_period=spec.settlement_period,
                    )
                signed_payoff = pos.size * unit_payoff
                fut_signed_usd = (pos.size * K) if spec.option_type == "call" else (-pos.size * K)
                deliv_fee = compute_delivery_fee_btc(
                    unit_payoff,
                    abs(pos.size),
                    spec.settlement_period,
                    delivery_fee_rate=spec.delivery_fee_rate,
                    fee_cap_ratio=spec.fee_cap_ratio,
                )

                stage1_entry = OracleTxLogEntry(
                    id=self._next_tx_id(),
                    type="exercise" if pos.size > ZERO else "assignment",
                    instrument_name=spec.instrument_name,
                    timestamp_ms=ts_ms,
                    amount=abs(pos.size),
                    price=K,
                    cashflow=ZERO,
                    change=ZERO,
                    commission=ZERO,
                    currency="BTC",
                    settlement_price=S,
                    info={
                        "two_stage": True,
                        "stage": 1,
                        "future_instrument": future_name,
                        "future_usd_delta": _dec_str(fut_signed_usd),
                        "strike": _dec_str(K),
                        "option_type": spec.option_type,
                        "expiry_ms": exp_ms,
                    },
                )
                self._emit_tx_log(stage1_entry)
                emitted.append(stage1_entry)
                self._positions.pop(spec.instrument_name, None)

                itm_details.append(
                    {
                        "instrument_name": spec.instrument_name,
                        "future_name": future_name,
                        "signed_qty": pos.size,
                        "fut_signed_usd": fut_signed_usd,
                        "signed_payoff": signed_payoff,
                        "delivery_fee": deliv_fee,
                    }
                )

            if itm_details:
                # Group Stage-2 delivery by derived same-expiry future instrument
                by_future: dict[str, list[dict[str, Any]]] = {}
                for det in itm_details:
                    by_future.setdefault(det["future_name"], []).append(det)

                for future_name, fut_legs in by_future.items():
                    raw_cf = sum((d["signed_payoff"] for d in fut_legs), ZERO)
                    net_cf = _floor_toward_neg_inf_1e8(raw_cf) if self.quantize_settlement_to_sat else raw_cf
                    tot_fee = sum((d["delivery_fee"] for d in fut_legs), ZERO)
                    net_change = net_cf - tot_fee
                    net_fut_usd = sum((d["fut_signed_usd"] for d in fut_legs), ZERO)

                    self.cash_balance_btc += net_change
                    self.total_fees_btc += tot_fee
                    self._positions.pop(future_name, None)

                    stage2_entry = OracleTxLogEntry(
                        id=self._next_tx_id(),
                        type="delivery",
                        instrument_name=future_name,
                        timestamp_ms=ts_ms,
                        amount=abs(net_fut_usd),
                        price=S,
                        cashflow=net_cf,
                        change=net_change,
                        commission=tot_fee,
                        currency="BTC",
                        settlement_price=S,
                        info={
                            "two_stage": True,
                            "stage": 2,
                            "expiry_ms": exp_ms,
                            "net_future_usd": _dec_str(net_fut_usd),
                            "option_instruments": [d["instrument_name"] for d in fut_legs],
                            "leg_payoffs": {d["instrument_name"]: _dec_str(d["signed_payoff"]) for d in fut_legs},
                            "leg_delivery_fees": {d["instrument_name"]: _dec_str(d["delivery_fee"]) for d in fut_legs},
                        },
                    )
                    self._emit_tx_log(stage2_entry)
                    emitted.append(stage2_entry)

        return tuple(emitted)

    def settle_delayed_tx_log(
        self,
        *,
        entry_type: str | None = None,
        instrument_name: str = "",
        amount: Decimal | str | int | float = "0",
        price: Decimal | str | int | float | None = None,
        cashflow: Decimal | str | int | float = "0",
        commission: Decimal | str | int | float = "0",
        change: Decimal | str | int | float | None = None,
        trade_id: str | None = None,
        order_id: str | None = None,
        settlement_price: Decimal | str | int | float | None = None,
        timestamp_ms: int | None = None,
        info: Mapping[str, Any] | None = None,
        apply_to_balance: bool = True,
        flush_pending: bool = True,
    ) -> tuple[OracleTxLogEntry, ...]:
        """Release withheld tx_log rows and/or inject a late-arriving tx_log entry."""
        released: list[OracleTxLogEntry] = []
        if flush_pending and self._delayed_tx_logs:
            released.extend(self._delayed_tx_logs)
            self._tx_logs.extend(self._delayed_tx_logs)
            self._delayed_tx_logs.clear()
        self._hold_tx_logs = False

        if entry_type is not None or to_dec(cashflow) != ZERO or to_dec(commission) != ZERO or change is not None:
            cf = to_dec(cashflow)
            fee = to_dec(commission)
            net_change = to_dec(change) if change is not None else (cf - fee)
            if apply_to_balance:
                self.cash_balance_btc += net_change
                self.total_fees_btc += fee
            injected = OracleTxLogEntry(
                id=self._next_tx_id(),
                type=entry_type or "delivery",
                instrument_name=instrument_name,
                timestamp_ms=int(self._server_time_ms if timestamp_ms is None else timestamp_ms),
                amount=to_dec(amount),
                price=_opt_dec(price),
                cashflow=cf,
                change=net_change,
                commission=fee,
                currency="BTC",
                trade_id=trade_id,
                order_id=order_id,
                settlement_price=_opt_dec(settlement_price),
                info=dict(info or {}),
            )
            self._tx_logs.append(injected)
            released.append(injected)
        return tuple(released)

    # ------------------------------------------------------------------
    # Exchange RPC queries & ExchangeFacts snapshot builder
    # ------------------------------------------------------------------
    def _effective_balance_btc(self) -> Decimal:
        ov_bal = self._margin_override.get("balance_btc")
        return ov_bal if ov_bal is not None else self.cash_balance_btc

    def get_account_summary(self, *, currency: str = "BTC") -> dict[str, Any]:
        self._maybe_raise_rate_limit()
        equity, avail, im, mm = self._collect_equity_and_margins()
        bal = self._effective_balance_btc()
        return {
            "currency": str(currency),
            "id": self.subaccount_id,
            "username": self.subaccount_id,
            "balance": bal,
            "balance_btc": bal,
            "equity": equity,
            "equity_btc": equity,
            "available_funds": avail,
            "available_funds_btc": avail,
            "initial_margin": im,
            "initial_margin_btc": im,
            "maintenance_margin": mm,
            "maintenance_margin_btc": mm,
            "portfolio_margining_enabled": bool(self.portfolio_margining_enabled),
            "margin_model": str(self.margin_model),
        }

    def get_positions(self, *, currency: str = "BTC", kind: str | None = None) -> tuple[OracleExchangePosition, ...]:
        self._maybe_raise_rate_limit()
        del currency
        out: list[OracleExchangePosition] = []
        for pos in self._positions.values():
            if pos.size == ZERO:
                continue
            if kind is not None and pos.kind != kind:
                continue
            book = self._books.get(pos.instrument_name)
            out.append(
                OracleExchangePosition(
                    instrument_name=pos.instrument_name,
                    kind=pos.kind,
                    size=pos.size,
                    average_price=pos.average_price,
                    mark_price=book.mark_price if book is not None else None,
                    index_price=self.spot_usd,
                )
            )
        return tuple(out)

    def get_open_orders(self, instrument_name: str | None = None) -> tuple[OracleExchangeOrder, ...]:
        self._maybe_raise_rate_limit()
        return tuple(
            o.to_immutable()
            for o in self._orders.values()
            if o.order_state == "open" and (instrument_name is None or o.instrument_name == instrument_name)
        )

    def get_order_state(self, order_id: str) -> OracleOrder:
        self._maybe_raise_rate_limit()
        if self._order_state_error_queue:
            code, msg = self._order_state_error_queue.pop(0)
            self._raise_injected_api_error(code, msg)
        order = self._orders.get(order_id)
        if order is None:
            raise DeribitApiError(10004, "order_not_found")
        if order.order_state == "open":
            return order.to_immutable()
        if (self._server_time_ms - order.last_update_timestamp_ms) <= self.order_state_by_label_ttl_ms:
            return order.to_immutable()
        raise DeribitApiError(10004, "order_not_found")

    def get_order_state_by_label(self, label: str) -> tuple[OracleExchangeOrder, ...]:
        self._maybe_raise_rate_limit()
        if self._label_query_error_queue:
            code, msg = self._label_query_error_queue.pop(0)
            self._raise_injected_api_error(code, msg)
        out: list[OracleExchangeOrder] = []
        for o in self._orders.values():
            if o.label != label:
                continue
            if o.order_state != "open" and (self._server_time_ms - o.last_update_timestamp_ms) > self.order_state_by_label_ttl_ms:
                continue
            out.append(o.to_immutable())
        return tuple(out)

    def get_order_history_by_instrument(
        self,
        instrument_name: str,
        *,
        count: int = 200,
        offset: int = 0,
        include_old: bool = True,
        include_unfilled: bool = True,
        historical: bool = False,
    ) -> list[OracleOrder]:
        self._maybe_raise_rate_limit()
        if self._inst_history_error_queue:
            code, msg = self._inst_history_error_queue.pop(0)
            self._raise_injected_api_error(code, msg)

        matched: list[tuple[int, _MutableOrder]] = []
        for idx, o in enumerate(self._orders.values()):
            if o.instrument_name != instrument_name or o.order_state == "open":
                continue
            if self._is_archive_migration_hidden(o):
                continue
            if not include_unfilled and o.filled_amount <= ZERO:
                continue
            age_ms = self._server_time_ms - o.last_update_timestamp_ms
            if not historical and age_ms > self.archive_age_ms:
                continue
            if not include_old and not historical and age_ms > self.order_state_by_label_ttl_ms:
                continue
            matched.append((idx, o))

        matched.sort(key=lambda pair: (pair[1].last_update_timestamp_ms, pair[0]), reverse=True)
        eff_offset = max(0, int(offset))
        eff_count = max(0, int(count))
        return [o.to_immutable() for _, o in matched[eff_offset : eff_offset + eff_count]]

    def get_order_history(
        self,
        *,
        count: int = 100,
        offset: int = 0,
        cursor: str | None = None,
        include_old: bool = True,
        include_unfilled: bool = True,
    ) -> dict[str, Any]:
        self._maybe_raise_rate_limit()
        indexed_closed = [
            (idx, o)
            for idx, o in enumerate(self._orders.values())
            if o.order_state != "open"
            and not self._is_archive_migration_hidden(o)
            and (include_unfilled or o.filled_amount > ZERO)
            and (include_old or (self._server_time_ms - o.last_update_timestamp_ms) <= self.order_state_by_label_ttl_ms)
        ]
        indexed_closed.sort(key=lambda pair: (pair[1].last_update_timestamp_ms, pair[0]), reverse=True)
        closed = [o for _, o in indexed_closed]

        lagged = [o for o in closed if o.history_lag_remaining > 0]
        visible = [o for o in closed if o.history_lag_remaining <= 0]

        if cursor is not None and (cursor == "lag" or cursor.startswith("lag:")):
            lag_off = 0 if cursor == "lag" else int(cursor.split(":", 1)[1])
            page = lagged[lag_off : lag_off + count]
            for o in page:
                o.history_lag_remaining = 0
            rem_lag = (lag_off + count) < len(lagged)
            return {
                "orders": tuple(o.to_immutable() for o in page),
                "has_more": rem_lag,
                "next_offset": None,
                "next_cursor": f"lag:{lag_off + count}" if rem_lag else None,
                "complete": not rem_lag,
            }

        eff_offset = int(cursor) if cursor is not None else int(offset)
        page = visible[eff_offset : eff_offset + count]
        more_visible = (eff_offset + count) < len(visible)
        has_lag = bool(lagged)
        has_more = more_visible or has_lag
        if more_visible:
            next_cursor: str | None = str(eff_offset + len(page))
            next_offset: int | None = eff_offset + len(page)
        elif has_lag:
            next_cursor = "lag"
            next_offset = None
        else:
            next_cursor = None
            next_offset = None

        if eff_offset == 0:
            for o in lagged:
                o.history_lag_remaining = max(0, o.history_lag_remaining - 1)

        return {
            "orders": tuple(o.to_immutable() for o in page),
            "has_more": has_more,
            "next_offset": next_offset,
            "next_cursor": next_cursor,
            "complete": not has_more,
        }

    def get_user_trades(self, *, count: int = 100, include_old: bool = True) -> tuple[OracleVenueTrade, ...]:
        self._maybe_raise_rate_limit()
        items = list(reversed(self._trades))
        if not include_old:
            items = [t for t in items if (self._server_time_ms - t.timestamp_ms) <= self.order_state_by_label_ttl_ms]
        return tuple(items[:count])

    def get_transaction_log(self, *, count: int = 100, offset: int = 0) -> tuple[OracleTxLogEntry, ...]:
        self._maybe_raise_rate_limit()
        items = list(reversed(self._tx_logs))
        return tuple(items[offset : offset + count])

    def _collect_equity_and_margins(self) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        mark_pnl = ZERO
        im = ZERO
        for pos in self._positions.values():
            if pos.size == ZERO:
                continue
            book = self._books.get(pos.instrument_name)
            mark = book.mark_price if (book is not None and book.mark_price is not None) else pos.average_price
            if mark is not None and pos.kind == "option":
                mark_pnl += pos.size * mark
            if pos.size < ZERO:
                unit_im = (mark if mark is not None else ZERO) + Decimal("0.10")
                im += abs(pos.size) * unit_im

        equity = self.cash_balance_btc + mark_pnl
        mm = im * Decimal("0.75")
        avail = max(ZERO, equity - im)

        ov = self._margin_override
        if ov.get("equity_btc") is not None:
            equity = ov["equity_btc"]  # type: ignore[assignment]
        if ov.get("initial_margin_btc") is not None:
            im = ov["initial_margin_btc"]  # type: ignore[assignment]
        if ov.get("maintenance_margin_btc") is not None:
            mm = ov["maintenance_margin_btc"]  # type: ignore[assignment]
        if ov.get("available_funds_btc") is not None:
            avail = ov["available_funds_btc"]  # type: ignore[assignment]
        else:
            avail = max(ZERO, equity - im)
        return equity, avail, im, mm

    def build_facts(
        self,
        short_inst: str,
        long_inst: str,
        *,
        page_limit: int = 100,
        follow_pagination: bool = True,
        include_lag_cursor: bool = False,
        as_dict: bool = False,
    ) -> OracleExchangeFacts | dict[str, Any]:
        short_spec = self._specs[short_inst]
        long_spec = self._specs[long_inst]
        short_book = self._books.get(short_inst)
        long_book = self._books.get(long_inst)

        try:
            lagged_at_start = {
                o.order_id for o in self._orders.values() if o.history_lag_remaining > 0
            }
            positions = self.get_positions(currency="BTC")
            all_open_orders = self.get_open_orders()
            open_orders_complete = len(all_open_orders) <= page_limit
            open_orders = all_open_orders[:page_limit]

            # Collect closed order history respecting page_limit and lag cycles
            history_orders: list[OracleExchangeOrder] = []
            cursor: str | None = None
            history_complete = True
            while True:
                page = self.get_order_history(count=page_limit, cursor=cursor, include_old=True, include_unfilled=True)
                history_orders.extend(page["orders"])
                if not page["has_more"]:
                    break
                nxt = page["next_cursor"]
                is_lag_hop = nxt is not None and (nxt == "lag" or str(nxt).startswith("lag:"))
                if not follow_pagination or nxt is None or (is_lag_hop and not include_lag_cursor):
                    history_complete = False
                    break
                cursor = nxt

            by_label: dict[str, list[OracleExchangeOrder]] = {}
            seen_ids: set[str] = set()
            for ord_obj in list(open_orders) + history_orders:
                if ord_obj.label and ord_obj.order_id not in seen_ids:
                    seen_ids.add(ord_obj.order_id)
                    by_label.setdefault(ord_obj.label, []).append(ord_obj)

            # Also consult order_state_by_label for active/recent (<30m) non-lagged labels
            recent_labels = {o.label for o in self._orders.values() if o.label}
            for lbl in recent_labels:
                if lbl not in by_label:
                    found = [
                        o.to_immutable()
                        for o in self._orders.values()
                        if o.label == lbl
                        and (o.order_state == "open" or (self._server_time_ms - o.last_update_timestamp_ms) <= self.order_state_by_label_ttl_ms)
                        and (include_lag_cursor or o.order_id not in lagged_at_start)
                    ]
                    if found:
                        by_label[lbl] = found

            recent_trades = self.get_user_trades(count=page_limit, include_old=True)
            tx_logs = self.get_transaction_log(count=page_limit, offset=0)
            tx_logs_complete = len(self._tx_logs) <= page_limit and not self._delayed_tx_logs
            equity, avail, im, mm = self._collect_equity_and_margins()
            bal = self._effective_balance_btc()

            facts = OracleExchangeFacts(
                asof_ms=self._server_time_ms,
                server_time_ms=self._server_time_ms,
                spot_usd=self.spot_usd,
                equity_btc=equity,
                available_funds_btc=avail,
                initial_margin_btc=im,
                maintenance_margin_btc=mm,
                positions=positions,
                open_orders=open_orders,
                open_orders_complete=open_orders_complete,
                history_complete=history_complete,
                recent_orders_by_label={k: tuple(v) for k, v in by_label.items()},
                recent_trades=recent_trades,
                tx_logs=tx_logs,
                tx_logs_complete=tx_logs_complete,
                short_book=short_book,
                long_book=long_book,
                short_spec=short_spec,
                long_spec=long_spec,
                subaccount_id=self.subaccount_id,
                api_error=None,
                rate_limited_until_ms=None,
                balance_btc=bal,
                portfolio_margining_enabled=bool(self.portfolio_margining_enabled),
                margin_model=str(self.margin_model),
            )
        except RateLimitError as exc:
            equity, avail, im, mm = self._collect_equity_and_margins()
            bal = self._effective_balance_btc()
            facts = OracleExchangeFacts(
                asof_ms=self._server_time_ms,
                server_time_ms=self._server_time_ms,
                spot_usd=self.spot_usd,
                equity_btc=equity,
                available_funds_btc=avail,
                initial_margin_btc=im,
                maintenance_margin_btc=mm,
                positions=(),
                open_orders=(),
                open_orders_complete=False,
                history_complete=False,
                recent_orders_by_label={},
                recent_trades=(),
                tx_logs=(),
                tx_logs_complete=False,
                short_book=short_book,
                long_book=long_book,
                short_spec=short_spec,
                long_spec=long_spec,
                subaccount_id=self.subaccount_id,
                api_error=f"{exc.code} {exc.message}",
                rate_limited_until_ms=self._server_time_ms + exc.retry_after_ms,
                balance_btc=bal,
                portfolio_margining_enabled=bool(self.portfolio_margining_enabled),
                margin_model=str(self.margin_model),
            )
        return facts.to_dict() if as_dict else facts

    def export_raw_trades(
        self,
    ) -> list[tuple[str, str, Decimal, str, Decimal, Decimal, Decimal, Decimal]]:
        """Export trades as raw 8-tuples for IndependentSettlementOracle cross-checks."""
        tx_by_trade = {tx.trade_id: tx for tx in self._tx_logs + self._delayed_tx_logs if tx.trade_id}
        rows: list[tuple[str, str, Decimal, str, Decimal, Decimal, Decimal, Decimal]] = []
        for tr in self._trades:
            spec = self._specs[tr.instrument_name]
            tx = tx_by_trade.get(tr.trade_id)
            cash = tx.cashflow if tx is not None else ((tr.amount * tr.price) if tr.direction == "sell" else -(tr.amount * tr.price))
            rows.append(
                (
                    tr.instrument_name,
                    spec.option_type or "option",
                    spec.strike_usd if spec.strike_usd is not None else ZERO,
                    tr.direction,
                    tr.amount,
                    tr.price,
                    tr.fee,
                    cash,
                )
            )
        return rows


# ----------------------------------------------------------------------
# Independent Settlement & TP Math Oracle
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class OracleTradeRecord:
    instrument: str
    option_type: str
    strike_usd: Decimal
    direction: str
    qty: Decimal
    price: Decimal
    fee_btc: Decimal
    cash_paid_or_received_in_tx_log: Decimal | None = None
    settlement_period: str | None = None


@dataclass(frozen=True)
class OracleTxAdjustment:
    cashflow: Decimal = ZERO
    commission: Decimal = ZERO
    change: Decimal | None = None
    kind: str = "adjustment"


def _coerce_trade_record(item: Any, default_sp: str, sp_map: Mapping[str, str]) -> OracleTradeRecord:
    if isinstance(item, OracleTradeRecord):
        sp = item.settlement_period or sp_map.get(item.instrument, default_sp)
        return OracleTradeRecord(
            instrument=item.instrument,
            option_type=item.option_type.lower(),
            strike_usd=to_dec(item.strike_usd),
            direction=item.direction.lower(),
            qty=to_dec(item.qty),
            price=to_dec(item.price),
            fee_btc=to_dec(item.fee_btc),
            cash_paid_or_received_in_tx_log=_opt_dec(item.cash_paid_or_received_in_tx_log),
            settlement_period=sp.lower(),
        )
    if isinstance(item, Mapping):
        inst = str(item["instrument"])
        return OracleTradeRecord(
            instrument=inst,
            option_type=str(item["option_type"]).lower(),
            strike_usd=to_dec(item["strike_usd"]),
            direction=str(item["direction"]).lower(),
            qty=to_dec(item["qty"]),
            price=to_dec(item["price"]),
            fee_btc=to_dec(item["fee_btc"]),
            cash_paid_or_received_in_tx_log=_opt_dec(item.get("cash_paid_or_received_in_tx_log")),
            settlement_period=str(item.get("settlement_period") or sp_map.get(inst, default_sp)).lower(),
        )
    if isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
        if len(item) not in (8, 9):
            raise ValueError(f"expected 8- or 9-tuple trade record, got length {len(item)}")
        inst = str(item[0])
        sp = str(item[8]).lower() if len(item) == 9 and item[8] else sp_map.get(inst, default_sp).lower()
        return OracleTradeRecord(
            instrument=inst,
            option_type=str(item[1]).lower(),
            strike_usd=to_dec(item[2]),
            direction=str(item[3]).lower(),
            qty=to_dec(item[4]),
            price=to_dec(item[5]),
            fee_btc=to_dec(item[6]),
            cash_paid_or_received_in_tx_log=_opt_dec(item[7]),
            settlement_period=sp,
        )
    raise TypeError(f"unsupported trade input: {type(item).__name__}")


def _coerce_adjustment(item: Any) -> OracleTxAdjustment:
    if isinstance(item, OracleTxAdjustment):
        return item
    if hasattr(item, "cashflow") and hasattr(item, "commission"):
        return OracleTxAdjustment(
            cashflow=to_dec(item.cashflow),
            commission=to_dec(item.commission),
            change=_opt_dec(getattr(item, "change", None)),
            kind=str(getattr(item, "type", "adjustment")),
        )
    if isinstance(item, Mapping):
        return OracleTxAdjustment(
            cashflow=to_dec(item.get("cashflow", 0)),
            commission=to_dec(item.get("commission", 0)),
            change=_opt_dec(item.get("change")),
            kind=str(item.get("type", item.get("kind", "adjustment"))),
        )
    if isinstance(item, Sequence) and len(item) in (2, 3):
        cf = to_dec(item[0])
        fee = to_dec(item[1])
        chg = _opt_dec(item[2]) if len(item) == 3 else None
        return OracleTxAdjustment(cashflow=cf, commission=fee, change=chg)
    raise TypeError(f"unsupported adjustment input: {type(item).__name__}")


def _tick_at_index(
    idx: int,
    tick_size: Decimal,
    tick_size_steps: Sequence[OracleTickStep],
) -> Decimal:
    """Bijective 1-based index -> exact legal tick price across stepped tick bands."""
    if idx <= 0:
        return ZERO
    bands = _tick_bands(tick_size, tick_size_steps)
    remaining = idx
    for i, (thresh, tick) in enumerate(bands):
        if i + 1 < len(bands):
            next_thresh = bands[i + 1][0]
            span_count = int(((next_thresh - thresh) / tick).to_integral_value(rounding=ROUND_DOWN))
            if remaining <= span_count:
                return (thresh + Decimal(remaining) * tick).quantize(SAT)
            remaining -= span_count
        else:
            return (thresh + Decimal(remaining) * tick).quantize(SAT)
    return ZERO


def _split_buy_outlay_from_scratch(
    unit_price: Decimal,
    qty: Decimal,
    min_lot: Decimal,
    taker_rate: Decimal,
    fee_cap_ratio: Decimal,
) -> Decimal:
    if unit_price <= ZERO or qty <= ZERO:
        return ZERO
    lot = min_lot if min_lot > ZERO else qty
    full_lots = int((qty / lot).to_integral_value(rounding=ROUND_DOWN))
    rem = qty - Decimal(full_lots) * lot
    principal = ceil_1e8(unit_price * qty)
    per_btc_fee = min(taker_rate, fee_cap_ratio * unit_price)
    lot_fee = ceil_1e8(lot * per_btc_fee)
    rem_fee = ceil_1e8(rem * per_btc_fee) if rem > ZERO else ZERO
    return principal + Decimal(full_lots) * lot_fee + rem_fee


def _solve_max_legal_tick_by_search(
    budget: Decimal,
    qty: Decimal,
    min_lot: Decimal,
    tick_size: Decimal,
    steps: Sequence[OracleTickStep],
    taker_rate: Decimal,
    fee_cap_ratio: Decimal,
) -> Decimal:
    if budget <= ZERO or qty <= ZERO:
        return ZERO
    first_tick = _tick_at_index(1, tick_size, steps)
    if _split_buy_outlay_from_scratch(first_tick, qty, min_lot, taker_rate, fee_cap_ratio) > budget:
        return ZERO

    lo = 1
    hi = 1
    while _split_buy_outlay_from_scratch(_tick_at_index(hi, tick_size, steps), qty, min_lot, taker_rate, fee_cap_ratio) <= budget:
        lo = hi
        hi *= 2

    while lo + 1 < hi:
        mid = (lo + hi) // 2
        px = _tick_at_index(mid, tick_size, steps)
        if _split_buy_outlay_from_scratch(px, qty, min_lot, taker_rate, fee_cap_ratio) <= budget:
            lo = mid
        else:
            hi = mid
    return _tick_at_index(lo, tick_size, steps)


class IndependentSettlementOracle:
    """Independent raw-Decimal verifier for cash balances, fees, expiry delivery, and TP limits."""

    def __init__(
        self,
        *,
        initial_balance_btc: Decimal | str | int | float = "0",
        trades: Iterable[Any] = (),
        tx_adjustments: Iterable[Any] = (),
        settlement_price_usd: Decimal | str | int | float | None = None,
        settlement_period: str = "day",
        instrument_settlement_periods: Mapping[str, str] | None = None,
        cash_is_net_of_fee: bool = False,
        delivery_fee_rate: Decimal | str | int | float = "0.00015",
        fee_cap_ratio: Decimal | str | int | float = "0.125",
    ) -> None:
        self.initial_balance_btc = to_dec(initial_balance_btc)
        self.settlement_price_usd = _opt_dec(settlement_price_usd)
        self.default_settlement_period = settlement_period.lower()
        sp_map = {k: v.lower() for k, v in (instrument_settlement_periods or {}).items()}
        self.delivery_fee_rate = to_dec(delivery_fee_rate)
        self.fee_cap_ratio = to_dec(fee_cap_ratio)

        parsed_trades = [_coerce_trade_record(t, self.default_settlement_period, sp_map) for t in trades]
        parsed_adjs = [_coerce_adjustment(a) for a in tx_adjustments]

        trade_gross_cf = ZERO
        trade_fees = ZERO
        trade_net_delta = ZERO
        positions: dict[str, Decimal] = {}
        leg_meta: dict[str, tuple[str, Decimal, str]] = {}

        for tr in parsed_trades:
            if tr.direction not in ("buy", "sell"):
                raise ValueError(f"invalid direction: {tr.direction}")
            raw_signed = (tr.qty * tr.price) if tr.direction == "sell" else -(tr.qty * tr.price)
            cash_val = tr.cash_paid_or_received_in_tx_log if tr.cash_paid_or_received_in_tx_log is not None else raw_signed
            if cash_is_net_of_fee:
                net_delta = cash_val
                gross_cf = cash_val + tr.fee_btc
            else:
                gross_cf = cash_val
                net_delta = cash_val - tr.fee_btc

            trade_gross_cf += gross_cf
            trade_fees += tr.fee_btc
            trade_net_delta += net_delta

            signed_qty = tr.qty if tr.direction == "buy" else -tr.qty
            positions[tr.instrument] = positions.get(tr.instrument, ZERO) + signed_qty
            leg_meta[tr.instrument] = (tr.option_type, tr.strike_usd, tr.settlement_period or self.default_settlement_period)

        adj_gross_cf = ZERO
        adj_fees = ZERO
        adj_net_delta = ZERO
        for adj in parsed_adjs:
            adj_gross_cf += adj.cashflow
            adj_fees += adj.commission
            adj_net_delta += adj.change if adj.change is not None else (adj.cashflow - adj.commission)

        expiry_gross = ZERO
        expiry_deliv_fees = ZERO
        if self.settlement_price_usd is not None:
            if self.settlement_price_usd <= ZERO:
                raise ValueError(f"settlement_price_usd must be > 0, got {self.settlement_price_usd}")
            S = self.settlement_price_usd
            for inst, net_q in positions.items():
                if net_q == ZERO:
                    continue
                opt_type, K, sp = leg_meta[inst]
                if opt_type == "put":
                    intrinsic = max(ZERO, K - S)
                elif opt_type == "call":
                    intrinsic = max(ZERO, S - K)
                else:
                    continue
                unit_payoff = intrinsic / S
                if unit_payoff > ZERO:
                    expiry_gross += net_q * unit_payoff
                    expiry_deliv_fees += compute_delivery_fee_btc(
                        unit_payoff,
                        abs(net_q),
                        sp,
                        delivery_fee_rate=self.delivery_fee_rate,
                        fee_cap_ratio=self.fee_cap_ratio,
                    )

        self.net_positions: dict[str, Decimal] = {k: v for k, v in positions.items() if v != ZERO}
        self.is_flat: bool = len(self.net_positions) == 0

        self.expected_trade_fees_btc = trade_fees
        self.expected_adjustment_fees_btc = adj_fees
        self.expected_delivery_fees_btc = expiry_deliv_fees
        self.expected_fees_btc = trade_fees + adj_fees + expiry_deliv_fees

        self.expected_expiry_gross_payoff_btc = expiry_gross
        self.expected_expiry_payoff_btc = expiry_gross - expiry_deliv_fees

        self.gross_cashflow_btc = trade_gross_cf + adj_gross_cf + expiry_gross
        self.total_cashflow_btc = trade_net_delta + adj_net_delta + self.expected_expiry_payoff_btc
        self.expected_cash_balance_btc = self.initial_balance_btc + self.total_cashflow_btc

        if self.is_flat or self.settlement_price_usd is not None:
            self.expected_episode_realized_pnl_btc: Decimal | None = self.total_cashflow_btc
        else:
            self.expected_episode_realized_pnl_btc = None

    @staticmethod
    def expected_tp_max_price(
        short_proceeds: Decimal | str | int | float,
        prot_cost: Decimal | str | int | float,
        fees: Decimal | str | int | float,
        matched_qty: Decimal | str | int | float,
        remaining_short_qty: Decimal | str | int | float,
        earlier_close_spent: Decimal | str | int | float,
        min_lot: Decimal | str | int | float,
        *,
        tick_size: Decimal | str | int | float = "0.0001",
        tick_size_steps: Sequence[Any] = (("0.005", "0.0005"),),
        taker_commission: Decimal | str | int | float = "0.0003",
        fee_cap_ratio: Decimal | str | int | float = "0.125",
        profit_capture_ratio: Decimal | str | int | float = "0.85",
    ) -> Decimal:
        """Compute exact supremum legal TP limit price from scratch via discrete tick-grid search."""
        sp = to_dec(short_proceeds)
        pc = to_dec(prot_cost)
        fe = to_dec(fees)
        mq = to_dec(matched_qty)
        rq = to_dec(remaining_short_qty)
        spent = to_dec(earlier_close_spent)
        lot = to_dec(min_lot)
        t_size = to_dec(tick_size)
        steps = _normalize_steps(tick_size_steps)
        t_rate = to_dec(taker_commission)
        f_cap = to_dec(fee_cap_ratio)
        capture = to_dec(profit_capture_ratio)

        if mq <= ZERO or rq <= ZERO:
            return ZERO

        c_matched = sp - pc - fe
        if c_matched <= ZERO:
            return ZERO

        b_total = floor_1e8((ONE - capture) * c_matched)
        rem_budget = b_total - spent
        if rem_budget <= ZERO:
            return ZERO

        p_init = _solve_max_legal_tick_by_search(b_total, mq, lot, t_size, steps, t_rate, f_cap)
        if p_init <= ZERO:
            return ZERO
        p_rem = _solve_max_legal_tick_by_search(rem_budget, rq, lot, t_size, steps, t_rate, f_cap)
        return min(p_init, p_rem)

    def summary(self) -> dict[str, Any]:
        return {
            "initial_balance_btc": self.initial_balance_btc,
            "expected_cash_balance_btc": self.expected_cash_balance_btc,
            "total_cashflow_btc": self.total_cashflow_btc,
            "gross_cashflow_btc": self.gross_cashflow_btc,
            "expected_fees_btc": self.expected_fees_btc,
            "expected_trade_fees_btc": self.expected_trade_fees_btc,
            "expected_delivery_fees_btc": self.expected_delivery_fees_btc,
            "expected_expiry_gross_payoff_btc": self.expected_expiry_gross_payoff_btc,
            "expected_expiry_payoff_btc": self.expected_expiry_payoff_btc,
            "expected_episode_realized_pnl_btc": self.expected_episode_realized_pnl_btc,
            "is_flat": self.is_flat,
            "net_positions": dict(self.net_positions),
        }
