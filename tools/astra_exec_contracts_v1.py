"""Exact execution contracts, Decimal math, stepped ticks, and fee/risk primitives (live-exec-behavior@1.2.0)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN, ROUND_UP
from enum import Enum
import hashlib
import json
from typing import Any, Collection, Mapping, Sequence

CONTRACT_SCHEMA = "astra_exec_contract@1.2.0"
BEHAVIOR_SPEC_VERSION = "live-exec-behavior@1.2.0"

SAT = Decimal("0.00000001")
ZERO = Decimal("0")
ONE = Decimal("1")
ALLOWED_MODES = ("SHADOW_ONLY", "DRY_RUN", "TESTNET", "LIVE")  # Default SHADOW_ONLY; LIVE requires explicit Mainnet & account binding
ORDER_PURPOSES = ("EP", "ES", "TP", "RX", "LX")


def D(value: Any) -> Decimal:
    """Convert int/str/float/Decimal strictly via str/repr without precision surprises."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is None:
        raise ValueError(f"cannot convert {value!r} to Decimal")
    if isinstance(value, (int, str)):
        return Decimal(str(value).strip())
    if isinstance(value, float):
        return Decimal(repr(value))
    raise TypeError(f"unsupported Decimal input type: {type(value).__name__}")


def opt_dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return D(value)


def dec_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.quantize(SAT) if value == value.quantize(SAT) else value.normalize()
    s = format(normalized, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def floor_sat(value: Decimal) -> Decimal:
    """Floor toward -infinity at 1e-8 BTC precision."""
    return (value / SAT).to_integral_value(rounding=ROUND_DOWN if value >= ZERO else ROUND_UP) * SAT


def ceil_1e8(value: Decimal) -> Decimal:
    """Ceiling at 1e-8 BTC precision (non-negative magnitudes)."""
    if value < ZERO:
        raise ValueError(f"ceil_1e8 expects non-negative value, got {value}")
    return value.quantize(SAT, rounding=ROUND_CEILING)


def floor_1e8_nonneg(value: Decimal) -> Decimal:
    if value < ZERO:
        return ZERO
    return value.quantize(SAT, rounding=ROUND_DOWN)


def quant_qty(value: Decimal, min_trade_amount: Decimal = Decimal("0.1")) -> Decimal:
    """Round quantity DOWN to a legal trade lot multiple."""
    if value <= ZERO:
        return ZERO
    steps = (value / min_trade_amount).to_integral_value(rounding=ROUND_DOWN)
    return (steps * min_trade_amount).normalize()


def is_qty_multiple(qty: Decimal, min_trade_amount: Decimal) -> bool:
    if qty <= ZERO or min_trade_amount <= ZERO:
        return False
    rem = qty % min_trade_amount
    return rem == ZERO


@dataclass(frozen=True)
class TickStep:
    above_price: Decimal
    tick_size: Decimal


@dataclass(frozen=True)
class InstrumentSpec:
    instrument_name: str
    kind: str  # "option" | "future"
    settlement_period: str  # "day" | "week" | "month" | ...
    strike_usd: Decimal | None
    option_type: str | None  # "put" | "call" | None
    expiry_ms: int
    tick_size: Decimal
    min_trade_amount: Decimal
    tick_size_steps: tuple[TickStep, ...] = ()
    maker_commission: Decimal = Decimal("0.0003")
    taker_commission: Decimal = Decimal("0.0003")
    fee_cap_ratio: Decimal = Decimal("0.125")
    delivery_fee_rate: Decimal = Decimal("0.00015")
    contract_size: Decimal = Decimal("1")

    @classmethod
    def from_deribit(cls, raw: Mapping[str, Any]) -> "InstrumentSpec":
        steps = tuple(
            sorted(
                (
                    TickStep(above_price=D(s["above_price"]), tick_size=D(s["tick_size"]))
                    for s in (raw.get("tick_size_steps") or ())
                ),
                key=lambda x: x.above_price,
            )
        )
        sp = str(raw.get("settlement_period") or "day").lower()
        kind = str(raw.get("kind") or "option").lower()
        default_deliv = ZERO if (kind == "option" and sp == "day") else Decimal("0.00015")
        return cls(
            instrument_name=str(raw["instrument_name"]),
            kind=kind,
            settlement_period=sp,
            strike_usd=opt_dec(raw.get("strike")),
            option_type=(str(raw["option_type"]).lower() if raw.get("option_type") else None),
            expiry_ms=int(raw["expiration_timestamp"]),
            tick_size=D(raw["tick_size"]),
            min_trade_amount=D(raw["min_trade_amount"]),
            tick_size_steps=steps,
            maker_commission=D(raw.get("maker_commission", "0.0003")),
            taker_commission=D(raw.get("taker_commission", "0.0003")),
            fee_cap_ratio=D(raw.get("fee_cap_ratio", "0.125")),
            delivery_fee_rate=D(raw.get("delivery_fee_rate", default_deliv)),
            contract_size=D(raw.get("contract_size", "1")),
        )

    @classmethod
    def btc_option(
        cls,
        instrument_name: str,
        *,
        strike_usd: Decimal | str | int | float,
        option_type: str,
        expiry_ms: int,
        settlement_period: str = "day",
        tick_size: Decimal | str = "0.0001",
        min_trade_amount: Decimal | str = "0.1",
    ) -> "InstrumentSpec":
        return cls(
            instrument_name=instrument_name,
            kind="option",
            settlement_period=settlement_period,
            strike_usd=D(strike_usd),
            option_type=option_type.lower(),
            expiry_ms=int(expiry_ms),
            tick_size=D(tick_size),
            min_trade_amount=D(min_trade_amount),
            tick_size_steps=(TickStep(above_price=Decimal("0.005"), tick_size=Decimal("0.0005")),),
            delivery_fee_rate=ZERO if settlement_period == "day" else Decimal("0.00015"),
        )


def _bands_for_spec(spec: InstrumentSpec) -> list[tuple[Decimal, Decimal]]:
    bands: list[tuple[Decimal, Decimal]] = [(ZERO, spec.tick_size)]
    for st in sorted(spec.tick_size_steps, key=lambda x: x.above_price):
        bands.append((st.above_price, st.tick_size))
    return bands


def _band_for_strictly_above(price: Decimal, spec: InstrumentSpec) -> tuple[Decimal, Decimal]:
    """Return (thresh, tick) for prices strictly above `thresh` (used at/below `price` for down rounding)."""
    bands = _bands_for_spec(spec)
    chosen = bands[0]
    for thresh, tick in bands[1:]:
        if price > thresh:
            chosen = (thresh, tick)
    return chosen


def _band_for_step_up(aligned_price: Decimal, spec: InstrumentSpec) -> tuple[Decimal, Decimal]:
    """Return (thresh, tick) governing the interval immediately ABOVE `aligned_price`."""
    bands = _bands_for_spec(spec)
    chosen = bands[0]
    for thresh, tick in bands[1:]:
        if aligned_price >= thresh:
            chosen = (thresh, tick)
    return chosen


def round_tick_down(price: Decimal, spec: InstrumentSpec) -> Decimal:
    if price <= ZERO:
        return ZERO
    thresh, tick = _band_for_strictly_above(price, spec)
    n = ((price - thresh) / tick).to_integral_value(rounding=ROUND_DOWN)
    return (thresh + n * tick).quantize(SAT)


def round_tick_up(price: Decimal, spec: InstrumentSpec) -> Decimal:
    if price <= ZERO:
        return ZERO
    bands = _bands_for_spec(spec)
    thresh, tick = _band_for_strictly_above(price, spec)
    n = ((price - thresh) / tick).to_integral_value(rounding=ROUND_CEILING)
    cand = (thresh + n * tick).quantize(SAT)
    for idx, (b_thresh, _b_tick) in enumerate(bands[:-1]):
        next_thresh, next_tick = bands[idx + 1]
        if thresh == b_thresh and cand > next_thresh:
            n2 = ((cand - next_thresh) / next_tick).to_integral_value(rounding=ROUND_CEILING)
            cand = (next_thresh + n2 * next_tick).quantize(SAT)
    return cand


def step_tick_up(price: Decimal, spec: InstrumentSpec) -> Decimal:
    g = round_tick_down(price, spec)
    thresh, tick = _band_for_step_up(g, spec)
    n = ((g - thresh) / tick).to_integral_value(rounding=ROUND_DOWN)
    return (thresh + (n + 1) * tick).quantize(SAT)


def step_tick_down(price: Decimal, spec: InstrumentSpec) -> Decimal:
    g = round_tick_up(price, spec)
    if g <= ZERO:
        return ZERO
    thresh, tick = _band_for_strictly_above(g, spec)
    n = ((g - thresh) / tick).to_integral_value(rounding=ROUND_DOWN)
    cand = (thresh + (n - 1) * tick).quantize(SAT)
    return cand if cand > ZERO else ZERO


def is_valid_tick(price: Decimal, spec: InstrumentSpec) -> bool:
    return price > ZERO and round_tick_down(price, spec) == price.quantize(SAT)


def option_trade_fee_btc(
    price_btc: Decimal,
    qty_btc: Decimal,
    spec: InstrumentSpec,
    *,
    is_maker: bool = False,
) -> Decimal:
    """Deribit inverse option trading fee: ceil_1e8(qty * min(rate, fee_cap_ratio * price))."""
    if price_btc <= ZERO or qty_btc <= ZERO:
        return ZERO
    rate = spec.maker_commission if is_maker else spec.taker_commission
    per_btc = min(rate, spec.fee_cap_ratio * price_btc)
    return ceil_1e8(qty_btc * per_btc)


def conservative_split_buy_outlay_btc(
    unit_price_btc: Decimal,
    qty_btc: Decimal,
    spec: InstrumentSpec,
    *,
    is_maker: bool = False,
) -> Decimal:
    """Worst-case total BTC spent to buy `qty_btc` across `min_trade_amount` split fills."""
    if qty_btc <= ZERO:
        return ZERO
    lot = spec.min_trade_amount
    full_lots = int((qty_btc / lot).to_integral_value(rounding=ROUND_DOWN))
    rem = qty_btc - D(full_lots) * lot
    principal = unit_price_btc * qty_btc
    per_lot_fee = option_trade_fee_btc(unit_price_btc, lot, spec, is_maker=is_maker)
    rem_fee = option_trade_fee_btc(unit_price_btc, rem, spec, is_maker=is_maker) if rem > ZERO else ZERO
    return ceil_1e8(principal) + D(full_lots) * per_lot_fee + rem_fee


def conservative_split_sell_proceeds_btc(
    unit_price_btc: Decimal,
    qty_btc: Decimal,
    spec: InstrumentSpec,
    *,
    is_maker: bool = False,
) -> Decimal:
    """Worst-case net BTC received when selling `qty_btc` across `min_trade_amount` split fills."""
    if qty_btc <= ZERO or unit_price_btc <= ZERO:
        return ZERO
    lot = spec.min_trade_amount
    full_lots = int((qty_btc / lot).to_integral_value(rounding=ROUND_DOWN))
    rem = qty_btc - D(full_lots) * lot
    principal = floor_1e8_nonneg(unit_price_btc * qty_btc)
    per_lot_fee = option_trade_fee_btc(unit_price_btc, lot, spec, is_maker=is_maker)
    rem_fee = option_trade_fee_btc(unit_price_btc, rem, spec, is_maker=is_maker) if rem > ZERO else ZERO
    return principal - D(full_lots) * per_lot_fee - rem_fee


def solve_max_buy_price(
    budget_btc: Decimal,
    qty_btc: Decimal,
    spec: InstrumentSpec,
    *,
    max_cap_btc: Decimal | None = None,
) -> Decimal:
    """Exact supremum legal tick p > 0 such that conservative_split_buy_outlay_btc(p, qty_btc, spec) <= budget_btc."""
    if budget_btc <= ZERO or qty_btc <= ZERO:
        return ZERO
    cap_bound = round_tick_down(max_cap_btc, spec) if max_cap_btc is not None and max_cap_btc > ZERO else None
    p_capped = budget_btc / (qty_btc * (ONE + spec.fee_cap_ratio))
    p_linear = (budget_btc - qty_btc * spec.taker_commission) / qty_btc
    p_guess = p_capped if (p_capped * spec.fee_cap_ratio <= spec.taker_commission) else p_linear
    if p_guess <= ZERO:
        return ZERO
    cand = round_tick_down(p_guess, spec)
    if cap_bound is not None and cand > cap_bound:
        cand = cap_bound

    while True:
        nxt = step_tick_up(cand, spec)
        if cap_bound is not None and nxt > cap_bound:
            break
        if conservative_split_buy_outlay_btc(nxt, qty_btc, spec) <= budget_btc:
            cand = nxt
        else:
            break

    while cand > ZERO and conservative_split_buy_outlay_btc(cand, qty_btc, spec) > budget_btc:
        cand = step_tick_down(cand, spec)
    return cand


def solve_min_sell_price(
    required_net_btc: Decimal,
    qty_btc: Decimal,
    spec: InstrumentSpec,
) -> Decimal:
    """Exact infimum legal tick p >= tick_size such that conservative_split_sell_proceeds_btc(p, qty_btc, spec) >= required_net_btc."""
    if qty_btc <= ZERO:
        raise ValueError(f"solve_min_sell_price expects positive qty_btc, got {qty_btc}")
    min_tick = round_tick_up(spec.tick_size, spec)
    if required_net_btc <= ZERO:
        return min_tick
    r = required_net_btc / qty_btc
    p0 = min(r / (ONE - spec.fee_cap_ratio), r + spec.taker_commission)
    cand = max(min_tick, round_tick_up(p0, spec))
    while cand > min_tick:
        prev = step_tick_down(cand, spec)
        if prev >= min_tick and conservative_split_sell_proceeds_btc(prev, qty_btc, spec) >= required_net_btc:
            cand = prev
        else:
            break
    while conservative_split_sell_proceeds_btc(cand, qty_btc, spec) < required_net_btc:
        cand = step_tick_up(cand, spec)
    return cand


def compute_ep_max_price(
    *,
    slice_qty: Decimal,
    short_bid_sweep: Decimal,
    ep_qty: Decimal,
    ep_gross: Decimal,
    ep_fee: Decimal,
    es_qty: Decimal,
    es_gross: Decimal,
    es_fee: Decimal,
    min_net_credit_per_btc: Decimal,
    max_zero_rec_ep_unit_btc: Decimal,
    effective_budget_btc: Decimal,
    long_spec: InstrumentSpec,
    short_spec: InstrumentSpec,
) -> Decimal:
    """Dual-budget EP max buy price (Spec 3.1.2: basic net credit floor + zero-recovery cap & B_eff)."""
    if slice_qty <= ZERO or short_bid_sweep <= ZERO:
        return ZERO
    q_after = ep_qty + slice_qty
    unmatched_before = max(ZERO, ep_qty - es_qty)
    q_unm_after = unmatched_before + slice_qty
    if ep_qty > ZERO and unmatched_before > ZERO:
        unmatched_ep_outlay = ceil_1e8((ep_gross + ep_fee) * unmatched_before / ep_qty)
    else:
        unmatched_ep_outlay = ZERO
    est_es_net = conservative_split_sell_proceeds_btc(short_bid_sweep, slice_qty, short_spec)
    b_credit = (es_gross - es_fee) + est_es_net - (ep_gross + ep_fee) - ceil_1e8(q_after * min_net_credit_per_btc)
    b_zero_rec = min(effective_budget_btc, ceil_1e8(q_unm_after * max_zero_rec_ep_unit_btc)) - unmatched_ep_outlay
    return solve_max_buy_price(min(b_credit, b_zero_rec), slice_qty, long_spec)


def compute_es_floor_price(
    *,
    slice_qty: Decimal,
    ep_qty: Decimal,
    ep_gross: Decimal,
    ep_fee: Decimal,
    es_qty: Decimal,
    es_gross: Decimal,
    es_fee: Decimal,
    min_net_credit_per_btc: Decimal,
    short_spec: InstrumentSpec,
) -> Decimal:
    """Cumulative deficit ES minimum sell price (Spec 3.1.3)."""
    q_after = es_qty + slice_qty
    o_prot = ceil_1e8((ep_gross + ep_fee) * q_after / ep_qty) if ep_qty > ZERO else ZERO
    r_total = max(ZERO, o_prot + ceil_1e8(q_after * min_net_credit_per_btc) - (es_gross - es_fee))
    return solve_min_sell_price(r_total, slice_qty, short_spec)


@dataclass(frozen=True)
class TpPlan:
    limit_price_btc: Decimal
    substate: str  # "TP_READY" | "TP_CLAMPED_TO_MIN_TICK" | "TP_UNATTAINABLE_LOW_CREDIT"
    budget_total_btc: Decimal
    rem_budget_btc: Decimal
    target_capture_ratio: Decimal | None
    alert: str | None = None


def compute_tp_plan(
    *,
    matched_net_credit_btc: Decimal,
    short_qty_btc: Decimal,
    short_spec: InstrumentSpec,
    tp_spent_btc: Decimal = ZERO,
    tp_budget_total_btc: Decimal | None = None,
    tp_cap_init_btc: Decimal | None = None,
    profit_capture_ratio: Decimal = Decimal("0.85"),
) -> TpPlan:
    """Exact dynamic-fee TP solver (Spec 3.2 & Task Doc 3.1: never hardcodes 0.00075/0.00015)."""
    budget_total = (
        tp_budget_total_btc
        if tp_budget_total_btc is not None
        else (floor_1e8_nonneg((ONE - profit_capture_ratio) * matched_net_credit_btc) if matched_net_credit_btc > ZERO else ZERO)
    )
    rem_budget = max(ZERO, budget_total - tp_spent_btc)
    if short_qty_btc <= ZERO or matched_net_credit_btc <= ZERO:
        return TpPlan(
            limit_price_btc=ZERO,
            substate="TP_UNATTAINABLE_LOW_CREDIT",
            budget_total_btc=budget_total,
            rem_budget_btc=rem_budget,
            target_capture_ratio=None,
            alert="TP_UNATTAINABLE_ALERT",
        )

    min_tick = round_tick_up(short_spec.tick_size, short_spec)
    cap_arg = tp_cap_init_btc if (tp_cap_init_btc is not None and tp_cap_init_btc >= min_tick) else None
    p_std = solve_max_buy_price(rem_budget, short_qty_btc, short_spec, max_cap_btc=cap_arg)
    if p_std >= min_tick:
        outlay = conservative_split_buy_outlay_btc(p_std, short_qty_btc, short_spec)
        rem_profit = matched_net_credit_btc - tp_spent_btc - outlay
        capture = (rem_profit / matched_net_credit_btc).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
        # If initial baseline itself was clamped to min_tick, preserve TP_CLAMPED_TO_MIN_TICK truth when capture < 0.85
        substate = "TP_CLAMPED_TO_MIN_TICK" if capture < profit_capture_ratio and p_std == min_tick else "TP_READY"
        return TpPlan(
            limit_price_btc=p_std,
            substate=substate,
            budget_total_btc=budget_total,
            rem_budget_btc=rem_budget,
            target_capture_ratio=capture,
            alert=None,
        )

    min_tick_outlay = conservative_split_buy_outlay_btc(min_tick, short_qty_btc, short_spec)
    rem_credit_ceiling = matched_net_credit_btc - tp_spent_btc
    if rem_credit_ceiling > min_tick_outlay:
        capture = ((rem_credit_ceiling - min_tick_outlay) / matched_net_credit_btc).quantize(
            Decimal("0.0001"), rounding=ROUND_DOWN
        )
        return TpPlan(
            limit_price_btc=min_tick,
            substate="TP_CLAMPED_TO_MIN_TICK",
            budget_total_btc=budget_total,
            rem_budget_btc=rem_budget,
            target_capture_ratio=capture,
            alert=None,
        )

    return TpPlan(
        limit_price_btc=ZERO,
        substate="TP_UNATTAINABLE_LOW_CREDIT",
        budget_total_btc=budget_total,
        rem_budget_btc=rem_budget,
        target_capture_ratio=None,
        alert="TP_UNATTAINABLE_ALERT",
    )


def delivery_fee_btc(unit_payoff_btc: Decimal, qty_btc: Decimal, spec: InstrumentSpec) -> Decimal:
    if unit_payoff_btc <= ZERO or qty_btc <= ZERO or spec.delivery_fee_rate <= ZERO:
        return ZERO
    per_btc = min(spec.delivery_fee_rate, spec.fee_cap_ratio * unit_payoff_btc)
    return ceil_1e8(qty_btc * per_btc)


def inverse_option_expiry_payoff_btc(
    option_type: str,
    strike_usd: Decimal,
    settlement_price_usd: Decimal,
    signed_qty_btc: Decimal,
) -> Decimal:
    """Unit intrinsic payoff * signed_qty (positive=long receives, negative=short pays)."""
    if signed_qty_btc == ZERO or settlement_price_usd <= ZERO:
        return ZERO
    opt = option_type.lower()
    if opt == "put":
        intrinsic_usd = max(ZERO, strike_usd - settlement_price_usd)
    elif opt == "call":
        intrinsic_usd = max(ZERO, settlement_price_usd - strike_usd)
    else:
        raise ValueError(f"unknown option_type: {option_type}")
    unit_btc = intrinsic_usd / settlement_price_usd
    return signed_qty_btc * unit_btc


def spread_stress_loss_btc(
    *,
    side: str,
    short_strike_usd: Decimal,
    long_strike_usd: Decimal,
    spot_usd: Decimal,
    qty_btc: Decimal,
    put_stress_spot_factor: Decimal = Decimal("0.70"),
) -> Decimal:
    """Nonlinear BTC-margined vertical spread stress loss (`R-09` & Spec 4.2.1)."""
    width = abs(short_strike_usd - long_strike_usd)
    if qty_btc <= ZERO or width <= ZERO:
        return ZERO
    s_lower = side.lower()
    if s_lower == "put":
        s_stress = max(Decimal("1"), spot_usd * put_stress_spot_factor)
        return ceil_1e8(qty_btc * width / s_stress)
    if s_lower == "call":
        denom = max(Decimal("1"), long_strike_usd)
        return ceil_1e8(qty_btc * width / denom)
    raise ValueError(f"unknown side: {side}")


def short_mark_from_book(short_book: "BookQuote | None") -> Decimal:
    """Extract best available short mark price reference from a book snapshot."""
    if short_book is None:
        return ZERO
    if short_book.mark_price is not None and short_book.mark_price > ZERO:
        return short_book.mark_price
    if short_book.ask_price is not None and short_book.ask_price > ZERO:
        return short_book.ask_price
    if short_book.bid_price is not None and short_book.bid_price > ZERO:
        return short_book.bid_price
    return ZERO


def compute_margin_and_cash_requirements(
    *,
    side: str,
    short_strike_usd: Decimal,
    long_strike_usd: Decimal,
    spot_usd: Decimal,
    qty_btc: Decimal,
    unpaid_prot_cost_btc: Decimal,
    short_mark_btc: Decimal,
    portfolio_margining_enabled: bool,
    put_stress_spot_factor: Decimal = Decimal("0.70"),
    candidate_min_cash_pm_per_btc: Decimal = Decimal("0.15"),
    candidate_min_cash_sm_per_btc: Decimal = Decimal("0.25"),
    apply_fixed_candidate_min_cash: bool = True,
) -> tuple[Decimal, Decimal, Decimal]:
    """Return (stress_loss_btc, margin_req_btc, min_cash_req_btc) per Spec 4.2."""
    stress_loss = spread_stress_loss_btc(
        side=side,
        short_strike_usd=short_strike_usd,
        long_strike_usd=long_strike_usd,
        spot_usd=spot_usd,
        qty_btc=qty_btc,
        put_stress_spot_factor=put_stress_spot_factor,
    )
    c_prot = max(ZERO, unpaid_prot_cost_btc)
    if portfolio_margining_enabled:
        m_req = ceil_1e8(Decimal("1.25") * (stress_loss + c_prot))
        cand_cash = ceil_1e8(qty_btc * candidate_min_cash_pm_per_btc) if apply_fixed_candidate_min_cash else ZERO
    else:
        s_clean = max(Decimal("1"), spot_usd)
        if side.lower() == "put":
            otm_usd = max(ZERO, s_clean - short_strike_usd)
        else:
            otm_usd = max(ZERO, short_strike_usd - s_clean)
        otm_ratio = otm_usd / s_clean
        base_factor = max(Decimal("0.15") - otm_ratio, Decimal("0.10"))
        mark_clean = max(ZERO, short_mark_btc)
        m_req = c_prot + ceil_1e8(qty_btc * (base_factor + mark_clean + Decimal("0.05")))
        cand_cash = ceil_1e8(qty_btc * candidate_min_cash_sm_per_btc) if apply_fixed_candidate_min_cash else ZERO
    min_cash = max(m_req, cand_cash)
    return stress_loss, m_req, min_cash


def compute_effective_budget_btc(
    *,
    budget_cap_btc: Decimal,
    pnl_settled_btc: Decimal,
    grant_baseline_pnl_btc: Decimal,
) -> Decimal:
    """Spec 4.1.1: B_eff = max(0, min(B_cap, B_cap + (PnL_settled - PnL_grant_baseline)))."""
    if budget_cap_btc <= ZERO:
        return ZERO
    raw = budget_cap_btc + (pnl_settled_btc - grant_baseline_pnl_btc)
    return max(ZERO, min(budget_cap_btc, raw)).quantize(SAT)


def make_order_label(env_tag: str, ep_id: str, purpose: str, seq: int) -> str:
    if purpose not in ORDER_PURPOSES:
        raise ValueError(f"invalid purpose: {purpose}")
    ep_short = ep_id.replace("episode_", "")[:10]
    label = f"ax1:{env_tag}:{ep_short}:{purpose}:{seq:04d}"
    if len(label) > 64:
        raise ValueError(f"label too long ({len(label)} > 64): {label}")
    return label


def parse_order_label(label: str) -> dict[str, Any] | None:
    if not isinstance(label, str) or not label.startswith("ax1:"):
        return None
    parts = label.split(":")
    if len(parts) != 5 or parts[3] not in ORDER_PURPOSES:
        return None
    try:
        seq = int(parts[4])
    except ValueError:
        return None
    return {"env": parts[1], "ep_short": parts[2], "purpose": parts[3], "seq": seq}


@dataclass(frozen=True)
class ExecConfig:
    """Frozen live-exec-behavior@1.2.0 execution parameters (offline/Testnet candidates, not P4 sign-off)."""

    version: str = "exec-config@3.0.0"
    request_valid_window_ms: int = 5_000
    min_valid_until_lead_ms: int = 500
    clock_safety_margin_ms: int = 1_500
    max_clock_skew_ms: int = 2_000
    max_rtt_for_clock_ms: int = 1_500
    max_book_fetch_age_ms: int = 3_000
    max_leg_fetch_skew_ms: int = 1_500
    max_decision_age_ms: int = 180_000
    min_entry_dte_ms: int = 1_800_000
    es_submit_cutoff_ms: int = 60_000
    lx_submit_cutoff_ms: int = 15_000
    rx_submit_cutoff_ms: int = 5_000
    tp_submit_cutoff_ms: int = 5_000
    entry_window_ms: int = 60_000
    es_rescue_window_ms: int = 20_000
    initial_qty_btc: Decimal = Decimal("0.1")
    max_entry_slice_btc: Decimal = Decimal("0.1")
    min_net_credit_per_btc: Decimal = Decimal("0.0008")
    max_zero_rec_ep_unit_btc: Decimal = Decimal("0.0060")
    tp_profit_capture_ratio: Decimal = Decimal("0.85")
    min_residual_bid_btc: Decimal = Decimal("0.0002")
    lx_retry_interval_ms: int = 5_000
    rx_stage1_window_ms: int = 3_000
    rx_stage1_min_samples: int = 2
    rx_stage1_mark_mult: Decimal = Decimal("1.15")
    rx_stage1_mark_add_btc: Decimal = Decimal("0.0010")
    rx_retry_schedule_ms: tuple[int, ...] = (1_000, 2_000, 5_000, 10_000)
    rx_cash_wall_backoff_ms: int = 10_000
    cancel_slow_warn_ms: int = 8_000
    candidate_min_cash_pm_per_btc: Decimal = Decimal("0.15")
    candidate_min_cash_sm_per_btc: Decimal = Decimal("0.25")
    put_stress_spot_factor: Decimal = Decimal("0.70")
    min_margin_buffer_ratio: Decimal = Decimal("2.5")
    max_active_episodes: int = 1
    rpc_connect_timeout_s: float = 2.0
    rpc_read_timeout_s: float = 2.5
    rpc_write_timeout_s: float = 3.0
    label_hot_cache_limit_ms: int = 1_500_000  # 25 minutes
    archive_history_threshold_ms: int = 1_200_000  # 20 minutes
    max_history_pages: int = 25
    status_warn_age_ms: int = 15_000
    status_stale_age_ms: int = 45_000


@dataclass(frozen=True)
class CapitalAuthorization:
    """Continuous backend capital authorization state persisted in SQLite meta."""

    entry_armed: bool = False
    budget_cap_btc: Decimal = ZERO
    max_qty_btc: Decimal = Decimal("0.1")
    grant_baseline_pnl_btc: Decimal = ZERO
    auth_epoch: int = 0
    updated_at_ms: int = 0
    operator: str = "init"
    note: str = "unarmed_default"


class BookStatus(str, Enum):
    OK = "OK"
    EMPTY_SIDE = "EMPTY_SIDE"
    DATA_GAP = "DATA_GAP"


@dataclass(frozen=True)
class BookQuote:
    instrument_name: str
    bid_price: Decimal | None
    bid_amount: Decimal
    ask_price: Decimal | None
    ask_amount: Decimal
    mark_price: Decimal | None
    min_price: Decimal | None
    max_price: Decimal | None
    timestamp_ms: int
    status: BookStatus
    gap_reason: str | None = None
    fetched_at_ms: int = 0
    bids: tuple[tuple[Decimal, Decimal], ...] = ()
    asks: tuple[tuple[Decimal, Decimal], ...] = ()

    def __post_init__(self) -> None:
        if self.fetched_at_ms <= 0 and self.timestamp_ms > 0:
            object.__setattr__(self, "fetched_at_ms", int(self.timestamp_ms))


def sweep_ask_price(book: BookQuote | None, qty_btc: Decimal) -> Decimal | None:
    """Worst level price needed to sweep `qty_btc` across `book.asks` (takes `asks[-1][0]` if thinner)."""
    if book is None or book.status == BookStatus.DATA_GAP:
        return None
    if book.asks:
        cum = ZERO
        for px, sz in book.asks:
            if px is None or px <= ZERO or sz <= ZERO:
                continue
            cum += sz
            if cum >= qty_btc:
                return px
        valid_asks = [px for px, sz in book.asks if px is not None and px > ZERO and sz > ZERO]
        if valid_asks:
            return valid_asks[-1]
    if book.ask_price is not None and book.ask_price > ZERO:
        return book.ask_price
    return None


def sweep_bid_price(book: BookQuote | None, qty_btc: Decimal) -> Decimal | None:
    """Worst level price needed to sweep `qty_btc` across `book.bids` (takes `bids[-1][0]` if thinner)."""
    if book is None or book.status == BookStatus.DATA_GAP:
        return None
    if book.bids:
        cum = ZERO
        for px, sz in book.bids:
            if px is None or px <= ZERO or sz <= ZERO:
                continue
            cum += sz
            if cum >= qty_btc:
                return px
        valid_bids = [px for px, sz in book.bids if px is not None and px > ZERO and sz > ZERO]
        if valid_bids:
            return valid_bids[-1]
    if book.bid_price is not None and book.bid_price > ZERO:
        return book.bid_price
    return None


@dataclass(frozen=True)
class ExchangePosition:
    instrument_name: str
    kind: str  # "option" | "future"
    size: Decimal  # + long, - short
    average_price: Decimal | None = None
    mark_price: Decimal | None = None
    index_price: Decimal | None = None


@dataclass(frozen=True)
class ExchangeOrder:
    order_id: str
    label: str
    instrument_name: str
    direction: str  # "buy" | "sell"
    amount: Decimal
    filled_amount: Decimal
    price: Decimal
    average_price: Decimal | None
    order_state: str  # "open" | "filled" | "cancelled" | "rejected" | "untriggered"
    time_in_force: str
    reduce_only: bool
    post_only: bool
    creation_timestamp_ms: int
    last_update_timestamp_ms: int
    trades: tuple[VenueTrade, ...] = ()

    @property
    def remaining_amount(self) -> Decimal:
        return max(ZERO, self.amount - self.filled_amount)


@dataclass(frozen=True)
class VenueTrade:
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
    liquidity: str  # "M" | "T" | ""


@dataclass(frozen=True)
class TxLogEntry:
    id: int
    type: str  # "trade" | "expiry" | "exercise" | "assignment" | "delivery" | ...
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
class OrderReconcileProbe:
    """Explicit per-command complete-evidence probe facts (Spec 5.3 & Task Doc 3.4)."""

    cmd_id: str
    order_state_status: str = "NOT_QUERIED"  # NOT_QUERIED | FOUND | NOT_FOUND | ERROR
    label_query_status: str = "NOT_QUERIED"  # NOT_QUERIED | FOUND | EMPTY_CONFIRMED | EXPIRED_HOT_CACHE | ERROR
    inst_history_recent_complete: bool = False
    inst_history_archive_complete: bool = False
    matched_orders: tuple[ExchangeOrder, ...] = ()
    duplicate_label_conflict: bool = False
    endpoint_contradiction: bool = False
    fresh_est_server_ms: int | None = None


@dataclass(frozen=True)
class ExchangeFacts:
    asof_ms: int
    server_time_ms: int
    spot_usd: Decimal
    equity_btc: Decimal
    available_funds_btc: Decimal
    initial_margin_btc: Decimal
    maintenance_margin_btc: Decimal
    positions: tuple[ExchangePosition, ...]
    open_orders: tuple[ExchangeOrder, ...]
    open_orders_complete: bool
    history_complete: bool
    recent_orders_by_label: dict[str, tuple[ExchangeOrder, ...]]
    recent_trades: tuple[VenueTrade, ...]
    tx_logs: tuple[TxLogEntry, ...]
    tx_logs_complete: bool
    short_book: BookQuote | None
    long_book: BookQuote | None
    short_spec: InstrumentSpec | None
    long_spec: InstrumentSpec | None
    subaccount_id: str = ""
    api_error: str | None = None
    rate_limited_until_ms: int | None = None
    balance_btc: Decimal | None = None
    portfolio_margining_enabled: bool = False
    margin_model: str = "SM"
    account_type: str = ""
    trades_complete: bool | None = None
    positions_complete: bool = True
    clock_rtt_ok: bool = True
    order_probes: dict[str, OrderReconcileProbe] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.balance_btc is None:
            object.__setattr__(self, "balance_btc", self.equity_btc)
        if self.trades_complete is None:
            object.__setattr__(self, "trades_complete", bool(self.history_complete))
        if not self.margin_model:
            object.__setattr__(self, "margin_model", "PM" if self.portfolio_margining_enabled else "SM")


def is_designated_mainnet_spm_single_episode_scope(
    *,
    mode: str,
    subaccount_id: str,
    allow_main_account: bool,
    facts: ExchangeFacts,
    target_qty_btc: Decimal,
    max_qty_btc: Decimal,
    config: ExecConfig,
) -> bool:
    """Return True only for designated Mainnet main-account S:PM 0.1 BTC single-episode controlled trials."""
    sub_norm = str(subaccount_id or "").strip()
    return (
        mode == "LIVE"
        and bool(sub_norm)
        and sub_norm != "0"
        and bool(allow_main_account)
        and str(facts.account_type or "").strip().lower() == "main"
        and bool(facts.portfolio_margining_enabled)
        and str(facts.margin_model or "").strip().lower() == "segregated_pm"
        and int(config.max_active_episodes) == 1
        and config.initial_qty_btc <= Decimal("0.1")
        and max_qty_btc <= Decimal("0.1")
        and target_qty_btc <= Decimal("0.1")
    )


def facts_clock_skew_ms(facts: ExchangeFacts, fallback_now_ms: int) -> int:
    """Measure exchange vs host skew using co-sampled facts.asof_ms so multi-RPC probe latency never mimics clock skew."""
    if facts.server_time_ms <= 0:
        return 0
    ref_ms = facts.asof_ms if facts.asof_ms > 0 else fallback_now_ms
    return abs(facts.server_time_ms - ref_ms)


@dataclass(frozen=True)
class P5DecisionContract:
    dossier_id: str
    hand_id: str
    episode_id: str
    decision_id: str
    decision: str  # "Accept" | ...
    side: str  # "put" | "call"
    short_instrument: str
    long_instrument: str
    short_strike_usd: Decimal
    long_strike_usd: Decimal
    expiry_ms: int
    decided_at_ms: int
    link_recorded_at_ms: int
    link_state: str
    decision_sha256: str
    condition_sha256: str
    input_sha256: str
    raw_sha256: str
    c_decision_ref_btc: Decimal | None = None
    revoked: bool = False
    revoke_review_id: str | None = None
    revoke_shadow_action: str | None = None
    revoke_at_ms: int | None = None
    condition_triggered: bool = False
    condition_triggered_at_ms: int | None = None
    hold_review_llm_unavailable: bool = False
    facts_readable: bool = True
    read_error: str | None = None


@dataclass(frozen=True)
class ExposurePermission:
    """Single authoritative verdict on whether a hand may open or add exposure (ADMIT / EP / ES)."""

    allowed: bool
    reason: str | None = None
    halt_entry: bool = False


class CommandStatus(str, Enum):
    PREPARED_UNKNOWN = "PREPARED_UNKNOWN"
    SUBMIT_UNKNOWN = "SUBMIT_UNKNOWN"
    OPEN = "OPEN"
    CANCEL_UNKNOWN = "CANCEL_UNKNOWN"
    CANCELLED = "CANCELLED"
    FILLED = "FILLED"
    EXPIRED_UNFILLED = "EXPIRED_UNFILLED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class ExecCommand:
    cmd_id: str
    episode_id: str
    purpose: str  # EP | ES | TP | RX | LX
    label: str
    instrument_name: str
    direction: str  # "buy" | "sell"
    amount: Decimal
    limit_price: Decimal
    time_in_force: str  # "GTC" | "IOC"
    reduce_only: bool
    post_only: bool
    reject_post_only: bool
    valid_until_ms: int
    created_at_ms: int
    status: CommandStatus
    venue_order_id: str | None = None
    filled_amount: Decimal = ZERO
    last_error: str | None = None
    updated_at_ms: int = 0
    cancel_requested_at_ms: int | None = None

    @property
    def is_blocking_unknown(self) -> bool:
        return self.status in {
            CommandStatus.PREPARED_UNKNOWN,
            CommandStatus.SUBMIT_UNKNOWN,
            CommandStatus.CANCEL_UNKNOWN,
        }

    @property
    def is_open_or_unknown(self) -> bool:
        return self.status in {
            CommandStatus.OPEN,
            CommandStatus.PREPARED_UNKNOWN,
            CommandStatus.SUBMIT_UNKNOWN,
            CommandStatus.CANCEL_UNKNOWN,
        }

    @property
    def reserved_remaining(self) -> Decimal:
        if not self.is_open_or_unknown:
            return ZERO
        return max(ZERO, self.amount - self.filled_amount)


class LifecyclePhase(str, Enum):
    ENTRY = "ENTRY"
    TP_MANAGED = "TP_MANAGED"
    EXIT_LATCHED = "EXIT_LATCHED"
    SHORT_LIABILITY_CLEARED = "SHORT_LIABILITY_CLEARED"
    INVENTORY_CLEARED = "INVENTORY_CLEARED"
    CASHFLOW_SETTLED = "CASHFLOW_SETTLED"


@dataclass(frozen=True)
class EpisodeState:
    episode_id: str
    env: str
    subaccount_id: str
    dossier_id: str
    hand_id: str
    p5_episode_id: str
    side: str
    short_instrument: str
    long_instrument: str
    short_strike_usd: Decimal
    long_strike_usd: Decimal
    expiry_ms: int
    contract_hash: str
    phase: LifecyclePhase
    created_at_ms: int
    auth_epoch: int = 0
    target_qty_btc: Decimal = Decimal("0.1")
    min_net_credit_per_btc: Decimal = Decimal("0.0008")
    max_zero_rec_ep_outlay_btc: Decimal = Decimal("0.0006")
    effective_budget_at_entry_btc: Decimal = Decimal("0.05")
    margin_model_at_entry: str = "SM"
    entry_spot_usd: Decimal | None = None
    decided_at_ms: int = 0
    link_recorded_at_ms: int = 0
    c_decision_ref_btc: Decimal | None = None
    estimated_abort_loss_at_ep_btc: Decimal | None = None
    entry_started_at_ms: int | None = None
    last_ep_fill_at_ms: int | None = None
    entry_halted: bool = False
    entry_halt_reason: str | None = None
    entry_closed: bool = False
    entry_closed_at_ms: int | None = None
    entry_closure_reason: str | None = None
    matched_qty_btc: Decimal = ZERO
    matched_short_proceeds_btc: Decimal = ZERO
    matched_prot_cost_ex_fee_btc: Decimal = ZERO
    matched_entry_fees_btc: Decimal = ZERO
    matched_net_credit_btc: Decimal = ZERO
    unmatched_prot_qty_btc: Decimal = ZERO
    unmatched_prot_cost_btc: Decimal = ZERO
    tp_budget_total_btc: Decimal = ZERO
    tp_cap_init_btc: Decimal = ZERO
    tp_target_capture_ratio: Decimal | None = None
    tp_substate: str | None = None
    exit_latched: bool = False
    exit_latch_reason: str | None = None
    exit_started_at_ms: int | None = None
    operator_exit_all: bool = False
    rx_quote_samples: int = 0
    rx_cash_wall_10009: bool = False
    short_liability_cleared: bool = False
    inventory_cleared: bool = False
    cashflow_settled: bool = False
    terminal_outcome: str | None = None  # ABORTED_ZERO_FILL | EARLY_FLAT_SETTLED | EXPIRY_SETTLED
    next_cmd_seq: int = 1
    last_entry_attempt_ms: int = 0
    entry_attempts: int = 0
    last_rx_attempt_ms: int = 0
    rx_attempts: int = 0
    last_lx_attempt_ms: int = 0
    alerts: tuple[str, ...] = ()


class ActionType(str, Enum):
    NONE = "NONE"
    SUBMIT = "SUBMIT"
    CANCEL_BY_LABEL = "CANCEL_BY_LABEL"
    RECONCILE_ORDER = "RECONCILE_ORDER"


@dataclass(frozen=True)
class PolicyAction:
    action_type: ActionType
    purpose: str | None = None
    instrument_name: str | None = None
    direction: str | None = None
    amount: Decimal | None = None
    limit_price: Decimal | None = None
    time_in_force: str | None = None
    reduce_only: bool = False
    post_only: bool = False
    reject_post_only: bool = False
    target_cmd_id: str | None = None
    target_label: str | None = None
    reason: str = ""
    estimated_abort_loss_btc: Decimal | None = None


@dataclass(frozen=True)
class EpisodePatch:
    phase: LifecyclePhase
    entry_started_at_ms: int | None
    last_ep_fill_at_ms: int | None
    entry_spot_usd: Decimal | None
    estimated_abort_loss_at_ep_btc: Decimal | None
    entry_halted: bool
    entry_halt_reason: str | None
    entry_closed: bool
    entry_closed_at_ms: int | None
    entry_closure_reason: str | None
    matched_qty_btc: Decimal
    matched_short_proceeds_btc: Decimal
    matched_prot_cost_ex_fee_btc: Decimal
    matched_entry_fees_btc: Decimal
    matched_net_credit_btc: Decimal
    unmatched_prot_qty_btc: Decimal
    unmatched_prot_cost_btc: Decimal
    tp_budget_total_btc: Decimal
    tp_cap_init_btc: Decimal
    tp_target_capture_ratio: Decimal | None
    tp_substate: str | None
    exit_latched: bool
    exit_latch_reason: str | None
    exit_started_at_ms: int | None
    operator_exit_all: bool
    rx_quote_samples: int
    rx_cash_wall_10009: bool
    short_liability_cleared: bool
    inventory_cleared: bool
    cashflow_settled: bool
    terminal_outcome: str | None
    alerts: tuple[str, ...]


@dataclass(frozen=True)
class StepResult:
    episode_patch: EpisodePatch
    actions: tuple[PolicyAction, ...]
    command_resolutions: dict[str, tuple[CommandStatus, Decimal, str | None]]
    alerts: tuple[str, ...]
    notes: tuple[str, ...] = ()


def compute_contract_hash(
    *,
    dossier_id: str,
    hand_id: str,
    p5_episode_id: str,
    decision_sha256: str,
    condition_sha256: str,
    short_instrument: str,
    long_instrument: str,
    expiry_ms: int,
) -> str:
    payload = json.dumps(
        {
            "dossier_id": dossier_id,
            "hand_id": hand_id,
            "p5_episode_id": p5_episode_id,
            "decision_sha256": decision_sha256,
            "condition_sha256": condition_sha256,
            "short_instrument": short_instrument,
            "long_instrument": long_instrument,
            "expiry_ms": int(expiry_ms),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def dataclass_to_jsonable(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return dec_str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, "__dataclass_fields__"):
        return {k: dataclass_to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [dataclass_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): dataclass_to_jsonable(v) for k, v in obj.items()}
    return obj


def matches_episode_future(instrument_name: str, short_instrument: str) -> bool:
    """Match Deribit same-expiry BTC future (BTC-25NOV26 or BTC-25NOV26-F) against BTC-25NOV26-62000-P."""
    if not instrument_name or instrument_name == short_instrument:
        return False
    s_parts = short_instrument.split("-")
    f_parts = instrument_name.split("-")
    if len(s_parts) < 2 or len(f_parts) < 2 or f_parts[0] != s_parts[0] or f_parts[1] != s_parts[1]:
        return False
    return len(f_parts) == 2 or (len(f_parts) == 3 and f_parts[2] == "F")


def is_episode_order_label(label: str, episode: "EpisodeState") -> bool:
    parsed = parse_order_label(label)
    if not parsed:
        return False
    ep_key = episode.episode_id.replace("episode_", "")
    return str(parsed["ep_short"]) in {ep_key, ep_key[:10]}


def trade_purpose_for_episode(
    trade_or_raw: Mapping[str, Any] | "VenueTrade",
    *,
    episode: "EpisodeState",
    cmd_by_label: Mapping[str, "ExecCommand"],
    cmd_by_order_id: Mapping[str, "ExecCommand"],
    tx_or_raw: Mapping[str, Any] | None = None,
) -> str:
    """Resolve fill purpose consistently across policy aggregation, store ingestion, and ledger rebuild."""
    if isinstance(trade_or_raw, Mapping):
        lbl = str(trade_or_raw.get("label") or "")
        oid = str(trade_or_raw.get("order_id") or (tx_or_raw or {}).get("order_id") or "")
        inst = str(trade_or_raw.get("instrument_name") or (tx_or_raw or {}).get("instrument_name") or "")
        direction = str(trade_or_raw.get("direction") or "").lower()
    else:
        lbl = str(trade_or_raw.label or "")
        oid = str(trade_or_raw.order_id or (tx_or_raw or {}).get("order_id") or "")
        inst = str(trade_or_raw.instrument_name or (tx_or_raw or {}).get("instrument_name") or "")
        direction = str(trade_or_raw.direction or "").lower()

    parsed = parse_order_label(lbl)
    if parsed and str(parsed["env"]) == episode.env and is_episode_order_label(lbl, episode):
        return str(parsed["purpose"])
    if lbl in cmd_by_label:
        return cmd_by_label[lbl].purpose
    if oid in cmd_by_order_id:
        return cmd_by_order_id[oid].purpose
    if not direction and tx_or_raw is not None:
        cf = opt_dec(tx_or_raw.get("cashflow")) or ZERO
        if inst == episode.long_instrument:
            direction = "buy" if cf < ZERO else "sell"
        else:
            direction = "sell" if cf > ZERO else "buy"
    if inst == episode.long_instrument:
        return "EP" if direction == "buy" else "LX"
    if inst == episode.short_instrument:
        return "ES" if direction == "sell" else "RX"
    return "UNKNOWN"


def classify_trade_attribution(
    trade_or_raw: Mapping[str, Any] | "VenueTrade",
    *,
    episode: "EpisodeState",
    cmd_by_label: Mapping[str, "ExecCommand"],
    cmd_by_order_id: Mapping[str, "ExecCommand"],
    facts_subaccount_id: str = "",
    foreign_event_uids: Collection[str] = (),
    foreign_order_ids: Collection[str] = (),
) -> tuple[bool, str | None]:
    """Unified trade attribution fence: checks subaccount, foreign IDs/labels, contract, and order identity."""
    if episode.subaccount_id and facts_subaccount_id and str(episode.subaccount_id) != str(facts_subaccount_id):
        return False, None
    if isinstance(trade_or_raw, Mapping):
        tid = str(trade_or_raw.get("trade_id") or "")
        oid = str(trade_or_raw.get("order_id") or "")
        lbl = str(trade_or_raw.get("label") or "")
        inst = str(trade_or_raw.get("instrument_name") or "")
    else:
        tid = str(trade_or_raw.trade_id or "")
        oid = str(trade_or_raw.order_id or "")
        lbl = str(trade_or_raw.label or "")
        inst = str(trade_or_raw.instrument_name or "")

    if inst not in {episode.short_instrument, episode.long_instrument}:
        return False, None
    if (tid and f"trade:{tid}" in foreign_event_uids) or (oid and oid in foreign_order_ids):
        return False, None
    if lbl and lbl in cmd_by_label:
        return True, cmd_by_label[lbl].purpose
    if oid and oid in cmd_by_order_id:
        return True, cmd_by_order_id[oid].purpose
    parsed = parse_order_label(lbl)
    if parsed is not None:
        if str(parsed["env"]) != episode.env or not is_episode_order_label(lbl, episode):
            return False, None
        return True, str(parsed["purpose"])
    return False, None


def classify_tx_log_attribution(
    entry_or_raw: Mapping[str, Any] | "TxLogEntry",
    *,
    episode: "EpisodeState",
    ep_trade_ids: Collection[str],
    ep_order_ids: Collection[str],
    facts_subaccount_id: str = "",
    has_open_option_at_expiry: bool = True,
    foreign_event_uids: Collection[str] = (),
    foreign_order_ids: Collection[str] = (),
) -> tuple[bool, str | None]:
    """Unified tx_log attribution fence across trades, Stage-1 option events, and Stage-2 deliveries."""
    if episode.subaccount_id and facts_subaccount_id and str(episode.subaccount_id) != str(facts_subaccount_id):
        return False, None
    if isinstance(entry_or_raw, Mapping):
        tx_id = entry_or_raw.get("id")
        tx_type = str(entry_or_raw.get("type") or "").lower()
        inst = str(entry_or_raw.get("instrument_name") or "")
        ts_ms = int(entry_or_raw.get("timestamp_ms") or 0)
        tid = str(entry_or_raw.get("trade_id") or "")
        oid = str(entry_or_raw.get("order_id") or "")
        info = entry_or_raw.get("info") if isinstance(entry_or_raw.get("info"), Mapping) else {}
    else:
        tx_id = entry_or_raw.id
        tx_type = str(entry_or_raw.type or "").lower()
        inst = str(entry_or_raw.instrument_name or "")
        ts_ms = int(entry_or_raw.timestamp_ms or 0)
        tid = str(entry_or_raw.trade_id or "")
        oid = str(entry_or_raw.order_id or "")
        info = entry_or_raw.info if isinstance(entry_or_raw.info, Mapping) else {}

    if (tx_id is not None and f"txlog:{tx_id}" in foreign_event_uids) or (oid and oid in foreign_order_ids):
        return False, None
    ep_insts = {episode.short_instrument, episode.long_instrument}
    if tx_type == "trade":
        if inst not in ep_insts:
            return False, None
        if tid and f"trade:{tid}" in foreign_event_uids:
            return False, None
        if (tid and tid in ep_trade_ids) or (oid and oid in ep_order_ids):
            return True, "trade"
        return False, None
    if tx_type in {"expiry", "exercise", "assignment"}:
        if (
            has_open_option_at_expiry
            and inst in ep_insts
            and ts_ms >= episode.created_at_ms
            and ts_ms >= episode.expiry_ms - 60_000
        ):
            return True, "stage1"
        return False, None
    if tx_type == "delivery":
        if ts_ms < episode.created_at_ms or not has_open_option_at_expiry:
            return False, None
        info_ep = str(info.get("episode_id") or "")
        if info_ep and info_ep != episode.episode_id:
            return False, None
        explicit_link = info.get("linked_expiry_ms") == episode.expiry_ms
        if explicit_link:
            return True, "stage2"
        if (
            (inst in ep_insts or matches_episode_future(inst, episode.short_instrument))
            and ts_ms >= episode.expiry_ms
        ):
            return True, "stage2"
        return False, None
    if inst in ep_insts and ((oid and oid in ep_order_ids) or (tid and tid in ep_trade_ids)):
        return True, "other"
    return False, None


def compute_episode_expiry_settlement(
    *,
    short_instrument: str,
    long_instrument: str,
    es_qty: Decimal,
    close_short_qty: Decimal,
    ep_qty: Decimal,
    lx_qty: Decimal,
    stage1_rows: Sequence[Mapping[str, Any]],
    stage2_rows: Sequence[Mapping[str, Any]],
    positions: Sequence["ExchangePosition"] | None = None,
    expiry_ms: int | None = None,
    server_time_ms: int | None = None,
    zero_net_future_grace_ms: int = 30_000,
) -> tuple[bool, bool, bool]:
    """Return (has_itm_stage1, all_stage1_legs_present, expiry_settlement_complete) safely."""
    required_legs: set[str] = set()
    rem_short = max(ZERO, es_qty - close_short_qty)
    rem_long = max(ZERO, ep_qty - lx_qty)
    if rem_short > ZERO:
        required_legs.add(short_instrument)
    if rem_long > ZERO:
        required_legs.add(long_instrument)
    stage1_seen = {str(r.get("instrument_name") or "") for r in stage1_rows}
    stage2_seen = {str(r.get("instrument_name") or "") for r in stage2_rows}
    covered_legs = stage1_seen | (stage2_seen & required_legs)
    all_legs_present = bool(covered_legs) and (not required_legs or required_legs.issubset(covered_legs))
    itm_stage1 = [r for r in stage1_rows if str(r.get("type") or "").lower() in {"exercise", "assignment"}]
    has_itm = len(itm_stage1) > 0
    assignment_qty = sum(
        (abs(D(r.get("amount") or "0")) for r in itm_stage1 if str(r.get("type") or "").lower() == "assignment"),
        ZERO,
    )
    exercise_qty = sum(
        (abs(D(r.get("amount") or "0")) for r in itm_stage1 if str(r.get("type") or "").lower() == "exercise"),
        ZERO,
    )
    both_itm_zero_net_future = (
        has_itm
        and len(itm_stage1) >= 2
        and (
            (assignment_qty > ZERO and exercise_qty > ZERO and assignment_qty == exercise_qty)
            or (assignment_qty == ZERO and exercise_qty == ZERO and rem_short > ZERO and rem_short == rem_long)
        )
    )
    direct_option_delivery = (
        not stage1_rows
        and bool(required_legs)
        and required_legs.issubset({str(r.get("instrument_name") or "") for r in stage2_rows})
    )
    grace_ok = server_time_ms is None or expiry_ms is None or server_time_ms >= (expiry_ms + zero_net_future_grace_ms)
    stage2_satisfied = (not has_itm) or (len(stage2_rows) > 0) or (both_itm_zero_net_future and grace_ok)
    venue_stage2_flat = True
    if positions is not None:
        venue_stage2_flat = all(
            p.size == ZERO
            for p in positions
            if p.instrument_name in {short_instrument, long_instrument}
            or matches_episode_future(p.instrument_name, short_instrument)
        )
    complete = bool(((all_legs_present and stage2_satisfied) or direct_option_delivery) and venue_stage2_flat)
    return has_itm, all_legs_present, complete
