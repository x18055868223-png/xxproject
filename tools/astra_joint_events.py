#!/usr/bin/env python3
"""Price rebalance event replay for Astra joint research.

The event family is independent from option anchors and production NR triggers.
It uses the FMZ M-DIE calculation equivalently, then emits causal observations
from closed one-minute bars only.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterable

from astra_joint_contract import EVENT_SCHEMA, FEATURE_GROUPS, PROTOCOL
from astra_joint_data import (
    MINUTE_MS,
    RollingHistoricalBars,
    entry_open_ms,
    is_complete_minute_bar,
    iter_binance_klines,
    ms_to_iso,
    normalize_kline,
    write_json,
)


M_DIE_CONFIG = {
    "m_die_interval": "1m",
    "m_die_window_bars": 15,
    "m_die_return_floor": 0.0006,
    "m_die_micro_return_floor": 0.00005,
    "m_die_z_start": 0.6,
    "m_die_z_full": 1.8,
    "m_die_r_start": 0.0006,
    "m_die_r_full": 0.0025,
    "m_die_e_start": 0.35,
    "m_die_e_full": 0.85,
    "m_die_p_start": 0.45,
    "m_die_p_full": 0.70,
    "m_die_eps": 1e-8,
}

MDieFunc = Callable[[list[dict[str, Any]], dict[str, Any] | None, int | None], dict[str, Any]]
STREAMING_REPLAY_SNAPSHOT_SCHEMA = "astra_joint_streaming_replay_snapshot@1.0.0"
OBSERVATION_CONTEXT_SCHEMA = "price_rebalance_observation_context@1.0.0"


def replay_protocol_identity(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = dict(PROTOCOL)
    if config:
        cfg.update(config)
    keys = (
        "market_id",
        "trigger_abs",
        "cooldown_abs",
        "same_direction_merge_min",
        "max_wait_min",
        "opposite_confirm_bars",
        "checkpoints_after_cooldown_min",
        "clock_hours_utc",
    )
    return {
        "schema": "astra_joint_replay_protocol_identity@1.0.0",
        "event_schema": EVENT_SCHEMA,
        "protocol": {key: cfg.get(key) for key in keys},
        "m_die_config": dict(M_DIE_CONFIG),
    }

EVENT_COLUMNS = (
    "schema",
    "event_family",
    "episode_id",
    "episode_sequence",
    "event_type",
    "as_of_ms",
    "as_of_utc",
    "direction",
    "shock_as_of_ms",
    "last_shock_as_of_ms",
    "m_die",
    "status",
    "gap_after_open_ms",
    "gap_next_open_ms",
    "gap_discovered_as_of_ms",
    "previous_as_of_ms",
    "opposite_direction",
    "opposite_streak_count",
    "opposite_first_as_of_ms",
    "target_as_of_ms",
    "confirmed_from_opposite_first_as_of_ms",
)

OBSERVATION_COLUMNS = tuple(
    dict.fromkeys(
        (
            "schema",
            "event_family",
            "episode_id",
            "episode_sequence",
            "active_episode_id",
            "in_episode",
            "observation_id",
            "observation_kind",
            "as_of_ms",
            "as_of_utc",
            "entry_ms",
            "entry_price",
            "entry_price_basis",
            "m_die",
            "abs_m_die",
            "m_die_direction",
            "shock_direction",
            "shock_as_of_ms",
            "cooldown_as_of_ms",
            "event_context_schema",
            "initial_m_die",
            "initial_abs_m_die",
            "max_abs_m_die_since_shock",
            "post_cooldown_max_abs_m_die",
            "post_cooldown_reheated",
            "post_cooldown_reheat_as_of_ms",
            "opposite_streak_count",
            "opposite_first_as_of_ms",
            "current_return_from_shock",
            "range_fraction_since_shock",
            "range_expansion_since_shock",
            "vwap_migration_since_shock",
            "feature_schema",
            "feature_as_of_ms",
            "feature_as_of_utc",
            "feature_names",
            "window_status",
            "shock_price",
            "up_move_since_shock",
            "down_move_since_shock",
            "put_adverse_move_since_shock",
            "call_adverse_move_since_shock",
            "adverse_move_since_shock",
            "favorable_move_since_shock",
            *FEATURE_GROUPS["joint"],
        )
    )
)


def safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return None
        return result
    except Exception:
        return None


def clamp(value: float | None, lower: float, upper: float) -> float | None:
    if value is None:
        return None
    return max(lower, min(upper, value))


def sign(value: Any, eps: float = 1e-12) -> int:
    value = safe_float(value)
    if value is None or abs(value) <= eps:
        return 0
    return 1 if value > 0 else -1


def compute_m_die(
    klines: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Equivalent pure-Python copy of the FMZ M-DIE v1.1 calculation."""
    cfg = dict(M_DIE_CONFIG)
    if config:
        cfg.update(config)
    n_bars = int(cfg.get("m_die_window_bars", 15))
    clean = _clean_m_die_klines(klines, now_ms)
    required = n_bars + 1
    if len(clean) < required:
        return _m_die_no_value("insufficient_bars", len(clean), required, cfg)

    window = clean[-n_bars:]
    prev_close = safe_float(clean[-required].get("close"))
    closes = [prev_close] + [safe_float(item.get("close")) for item in window]
    if any(value is None or value <= 0 for value in closes):
        return _m_die_no_value("invalid_close", len(clean), required, cfg)

    returns = [math.log(closes[index] / closes[index - 1]) for index in range(1, len(closes))]
    total_return = sum(returns)
    return_floor = float(cfg.get("m_die_return_floor", 0.0006))
    if total_return > return_floor:
        direction = 1
    elif total_return < -return_floor:
        direction = -1
    else:
        return _m_die_zero("direction_not_clear", clean, total_return, cfg)

    eps = float(cfg.get("m_die_eps", 1e-8))
    realized_vol = _stddev(returns) * math.sqrt(float(n_bars))
    displacement_z = abs(total_return) / max(realized_vol, eps)
    d_z_score = _linear_score(displacement_z, cfg.get("m_die_z_start", 0.6), cfg.get("m_die_z_full", 1.8))
    abs_return_score = _linear_score(abs(total_return), cfg.get("m_die_r_start", 0.0006), cfg.get("m_die_r_full", 0.0025))
    d_final = math.sqrt(d_z_score * abs_return_score)

    path_length = sum(abs(item) for item in returns)
    path_efficiency = abs(total_return) / max(path_length, eps)
    e_score = _linear_score(path_efficiency, cfg.get("m_die_e_start", 0.35), cfg.get("m_die_e_full", 0.85))

    micro_floor = float(cfg.get("m_die_micro_return_floor", 0.00005))
    valid_returns = [item for item in returns if abs(item) > micro_floor]
    valid_count = len(valid_returns)
    coverage_ratio = valid_count / float(n_bars)
    if valid_count:
        same_count = sum(1 for item in valid_returns if sign(item) == direction)
        same_direction_ratio = same_count / float(valid_count)
    else:
        same_direction_ratio = 0.5
    p_raw = same_direction_ratio * math.sqrt(coverage_ratio)
    p_final = _linear_score(p_raw, cfg.get("m_die_p_start", 0.45), cfg.get("m_die_p_full", 0.70))

    score = clamp(0.40 * d_final + 0.40 * e_score + 0.20 * p_final, 0.0, 1.0)
    m_die = direction * score
    level = _m_die_level(abs(m_die))
    move_shape = _m_die_move_shape(score, coverage_ratio, e_score)
    last_time = window[-1].get("close_time") or window[-1].get("close_time_ms") or window[-1].get("open_time")
    return {
        "schema": "MicroDirectionalImbalanceExtent",
        "factor_name": "M-DIE",
        "factor_version": "v1.1_final",
        "interval": cfg.get("m_die_interval", "1m"),
        "window": "15m",
        "n_bars": n_bars,
        "rolling": True,
        "last_closed_bar_time": last_time,
        "direction": "UP" if direction > 0 else "DOWN",
        "m_die": m_die,
        "score": score,
        "level": level,
        "move_shape": move_shape,
        "components": {
            "displacement": {
                "score": d_final,
                "raw": {
                    "window_log_return": total_return,
                    "window_return_pct": math.exp(total_return) - 1.0,
                    "realized_vol": realized_vol,
                    "displacement_z": displacement_z,
                    "d_z_score": d_z_score,
                    "abs_return_score": abs_return_score,
                    "d_final": d_final,
                },
            },
            "path_efficiency": {"score": e_score, "raw": {"efficiency": path_efficiency}},
            "directional_persistence": {
                "score": p_final,
                "raw": {
                    "same_direction_ratio": same_direction_ratio,
                    "valid_return_count": valid_count,
                    "coverage_ratio": coverage_ratio,
                    "p_raw": p_raw,
                },
            },
        },
        "data_status": {
            "source": "api_backfill_or_live_polling",
            "bars_loaded": len(clean),
            "bars_required": required,
            "uses_closed_bars_only": True,
            "data_state": "OK",
        },
    }


def replay(
    rows: Iterable[Any],
    *,
    config: dict[str, Any] | None = None,
    as_of_upper_ms: int | None = None,
    mdie_func: MDieFunc = compute_m_die,
) -> dict[str, list[dict[str, Any]]]:
    normalized = sorted(
        (row for row in (normalize_kline(item) for item in rows) if row is not None),
        key=lambda item: item["open_time_ms"],
    )
    events: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    clock_rows: list[dict[str, Any]] = []
    runner = StreamingReplay(
        config=config,
        as_of_upper_ms=as_of_upper_ms,
        mdie_func=mdie_func,
        on_event=events.append,
        on_observation=observations.append,
        on_clock=clock_rows.append,
    )
    for row in normalized:
        runner.push(row)
    runner.finish()
    return {
        "events": events,
        "observations": observations,
        "clock_rows": clock_rows,
    }


def stream_replay(
    rows: Iterable[Any],
    *,
    config: dict[str, Any] | None = None,
    as_of_upper_ms: int | None = None,
    mdie_func: MDieFunc = compute_m_die,
) -> Iterable[tuple[str, dict[str, Any]]]:
    """Yield replay records without retaining outputs or all market history."""
    runner = StreamingReplay(config=config, as_of_upper_ms=as_of_upper_ms, mdie_func=mdie_func)
    for raw in rows:
        yield from runner.push(raw)
    yield from runner.finish()


class StreamingReplay:
    """Stateful bounded replay over ordered one-minute bars."""

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        as_of_upper_ms: int | None = None,
        mdie_func: MDieFunc = compute_m_die,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        on_observation: Callable[[dict[str, Any]], None] | None = None,
        on_clock: Callable[[dict[str, Any]], None] | None = None,
    ):
        cfg = dict(PROTOCOL)
        if config:
            cfg.update(config)
        self.config = cfg
        self.as_of_upper_ms = as_of_upper_ms
        self.mdie_func = mdie_func
        self.bars = RollingHistoricalBars(max_minutes=1441)
        self.rolling: deque[dict[str, Any]] = deque(maxlen=int(M_DIE_CONFIG["m_die_window_bars"]) + 1)
        self.previous_open: int | None = None
        self.previous_as_of: int | None = None
        self._emitted: list[tuple[str, dict[str, Any]]] = []
        self._external_event = on_event
        self._external_observation = on_observation
        self._external_clock = on_clock
        self.state = _ReplayState(
            cfg,
            self.bars,
            on_event=self._emit_event,
            on_observation=self._emit_observation,
            on_clock=self._emit_clock,
        )

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict[str, Any],
        *,
        config: dict[str, Any] | None = None,
        as_of_upper_ms: int | None = None,
        mdie_func: MDieFunc = compute_m_die,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        on_observation: Callable[[dict[str, Any]], None] | None = None,
        on_clock: Callable[[dict[str, Any]], None] | None = None,
    ) -> "StreamingReplay":
        if not isinstance(snapshot, dict) or snapshot.get("schema") != STREAMING_REPLAY_SNAPSHOT_SCHEMA:
            raise ValueError("unsupported streaming replay snapshot")
        runner = cls(
            config=config,
            as_of_upper_ms=as_of_upper_ms,
            mdie_func=mdie_func,
            on_event=on_event,
            on_observation=on_observation,
            on_clock=on_clock,
        )
        if snapshot.get("protocol_identity") != replay_protocol_identity(runner.config):
            raise ValueError("streaming replay snapshot protocol mismatch")
        runner.bars = RollingHistoricalBars.from_snapshot(snapshot.get("bars") or {})
        rolling = []
        for row in snapshot.get("rolling") or []:
            normalized = normalize_kline(row)
            if normalized is not None:
                rolling.append(normalized)
        runner.rolling = deque(rolling, maxlen=int(M_DIE_CONFIG["m_die_window_bars"]) + 1)
        runner.previous_open = snapshot.get("previous_open_ms")
        runner.previous_as_of = snapshot.get("previous_as_of_ms")
        if runner.previous_open is not None:
            runner.previous_open = int(runner.previous_open)
        if runner.previous_as_of is not None:
            runner.previous_as_of = int(runner.previous_as_of)
        runner.state = _ReplayState.from_snapshot(
            snapshot.get("state") or {},
            runner.config,
            runner.bars,
            on_event=runner._emit_event,
            on_observation=runner._emit_observation,
            on_clock=runner._emit_clock,
        )
        return runner

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "schema": STREAMING_REPLAY_SNAPSHOT_SCHEMA,
            "protocol_identity": replay_protocol_identity(self.config),
            "previous_open_ms": self.previous_open,
            "previous_as_of_ms": self.previous_as_of,
            "bars": self.bars.to_snapshot(),
            "rolling": list(self.rolling),
            "state": self.state.to_snapshot(),
        }

    def push(self, raw: Any) -> list[tuple[str, dict[str, Any]]]:
        self._emitted = []
        row = normalize_kline(raw)
        if row is None:
            return []
        open_ms = int(row["open_time_ms"])
        close_ms = int(row["close_time_ms"])
        if self.as_of_upper_ms is not None and not is_complete_minute_bar(row, as_of_ms=self.as_of_upper_ms):
            return []
        if self.previous_open is not None:
            if open_ms <= self.previous_open:
                raise ValueError(f"streaming input is not strictly increasing: {open_ms} <= {self.previous_open}")
            if open_ms != self.previous_open + MINUTE_MS:
                self.state.terminate_gap(self.previous_as_of, self.previous_open, open_ms)
                self.rolling.clear()
                self.bars.clear()
        self.bars.append(row)
        self.rolling.append(row)
        mdie = self.mdie_func(list(self.rolling), M_DIE_CONFIG, close_ms)
        self.state.handle_mdie(mdie, close_ms)
        self.state.maybe_clock(row, mdie)
        self.previous_open = open_ms
        self.previous_as_of = close_ms
        return list(self._emitted)

    def finish(self) -> list[tuple[str, dict[str, Any]]]:
        self._emitted = []
        self.state.finish(self.previous_as_of)
        return list(self._emitted)

    def _emit_event(self, row: dict[str, Any]) -> None:
        self._emitted.append(("event", row))
        if self._external_event is not None:
            self._external_event(row)

    def _emit_observation(self, row: dict[str, Any]) -> None:
        self._emitted.append(("observation", row))
        if self._external_observation is not None:
            self._external_observation(row)

    def _emit_clock(self, row: dict[str, Any]) -> None:
        self._emitted.append(("clock", row))
        if self._external_clock is not None:
            self._external_clock(row)


class _ReplayState:
    def __init__(
        self,
        config: dict[str, Any],
        bars: RollingHistoricalBars,
        *,
        on_event: Callable[[dict[str, Any]], None],
        on_observation: Callable[[dict[str, Any]], None],
        on_clock: Callable[[dict[str, Any]], None],
    ):
        self.config = config
        self.bars = bars
        self.trigger_abs = float(config["trigger_abs"])
        self.cooldown_abs = float(config["cooldown_abs"])
        self.merge_ms = int(config["same_direction_merge_min"]) * MINUTE_MS
        self.max_wait_ms = int(config["max_wait_min"]) * MINUTE_MS
        self.opposite_confirm_bars = int(config["opposite_confirm_bars"])
        self.clock_hours_utc = {int(value) for value in config.get("clock_hours_utc", [])}
        self.market_id = str(config.get("market_id") or "btcusdt_um")
        self.counter = 0
        self.active: dict[str, Any] | None = None
        self._on_event = on_event
        self._on_observation = on_observation
        self._on_clock = on_clock

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict[str, Any],
        config: dict[str, Any],
        bars: RollingHistoricalBars,
        *,
        on_event: Callable[[dict[str, Any]], None],
        on_observation: Callable[[dict[str, Any]], None],
        on_clock: Callable[[dict[str, Any]], None],
    ) -> "_ReplayState":
        state = cls(config, bars, on_event=on_event, on_observation=on_observation, on_clock=on_clock)
        state.counter = int(snapshot.get("counter") or 0)
        active = snapshot.get("active")
        if isinstance(active, dict):
            restored = dict(active)
            restored["emitted"] = set(restored.get("emitted") or [])
            _mark_restored_active_context(restored)
            state.active = restored
        return state

    def to_snapshot(self) -> dict[str, Any]:
        active = None
        if self.active is not None:
            active = dict(self.active)
            active["emitted"] = sorted(active.get("emitted") or [])
        return {
            "schema": "astra_joint_replay_state@1.0.0",
            "counter": self.counter,
            "active": active,
        }

    def handle_mdie(self, mdie: dict[str, Any], as_of_ms: int) -> None:
        self._close_completed_if_idle(as_of_ms)
        self._close_timed_out(as_of_ms)
        value = safe_float(mdie.get("m_die")) or 0.0
        abs_value = abs(value)
        direction = sign(value)
        if self.active is not None:
            self._record_active_mdie(value, abs_value, as_of_ms)
        if self.active is not None and not self._would_confirm_opposite(direction, abs_value, as_of_ms):
            self._emit_due_checkpoints(mdie, as_of_ms)
        if abs_value >= self.trigger_abs and direction:
            self._handle_strong(direction, abs_value, mdie, as_of_ms)
            return
        if self.active is not None:
            self.active["opposite_streak_sign"] = None
            self.active["opposite_streak_count"] = 0
            self.active["opposite_first_as_of_ms"] = None
            self._handle_cooldown(abs_value, mdie, as_of_ms)

    def maybe_clock(self, row: dict[str, Any], mdie: dict[str, Any]) -> None:
        opened = dt.datetime.fromtimestamp(int(row["open_time_ms"]) / 1000, tz=dt.UTC)
        if opened.hour not in self.clock_hours_utc or opened.minute != 0:
            return
        as_of_ms = int(row["close_time_ms"])
        active_episode_id = self.active["episode_id"] if self.active else None
        features = self.bars.feature_snapshot(
            as_of_ms,
            shock_as_of_ms=self.active.get("shock_as_of_ms") if self.active else None,
            shock_direction=self.active.get("direction") if self.active else None,
            shock_magnitude=self.active.get("shock_magnitude") if self.active else None,
            elapsed_from_shock_min=self._elapsed(as_of_ms) if self.active else None,
        )
        clock = {
            "schema": EVENT_SCHEMA,
            "event_family": "clock_reference",
            "episode_id": None,
            "episode_sequence": None,
            "active_episode_id": active_episode_id,
            "in_episode": active_episode_id is not None,
            "observation_id": f"clock:{as_of_ms}",
            "observation_kind": "clock",
            "as_of_ms": as_of_ms,
            "as_of_utc": ms_to_iso(as_of_ms),
            "entry_ms": entry_open_ms(as_of_ms),
            "entry_price": None,
            "entry_price_basis": "filled later by candidate builder; not available inside same as-of record",
            "m_die": safe_float(mdie.get("m_die")),
            "abs_m_die": abs(safe_float(mdie.get("m_die")) or 0.0),
            "m_die_direction": mdie.get("direction"),
            "feature_names": list(FEATURE_GROUPS["joint"]),
        }
        clock.update(features)
        self._on_clock(clock)

    def terminate_gap(self, previous_as_of_ms: int | None, previous_open_ms: int | None, next_open_ms: int) -> None:
        if self.active is None:
            return
        self._on_event(
            self._event(
                "terminated_by_gap",
                next_open_ms + MINUTE_MS - 1,
                gap_after_open_ms=previous_open_ms,
                gap_next_open_ms=next_open_ms,
                gap_discovered_as_of_ms=next_open_ms + MINUTE_MS - 1,
                previous_as_of_ms=previous_as_of_ms,
                status="terminated",
            )
        )
        self.active = None

    def finish(self, as_of_ms: int | None) -> None:
        if self.active is not None:
            self._on_event(self._event("open_at_replay_end", as_of_ms, status="open"))

    def _handle_strong(self, direction: int, abs_value: float, mdie: dict[str, Any], as_of_ms: int) -> None:
        if self.active is None:
            self._start_episode(direction, abs_value, mdie, as_of_ms)
            return
        if direction == self.active["direction"]:
            if as_of_ms - int(self.active["last_shock_as_of_ms"]) <= self.merge_ms:
                self.active["last_shock_as_of_ms"] = as_of_ms
                self.active["shock_magnitude"] = max(float(self.active["shock_magnitude"]), abs_value)
                self.active["opposite_streak_sign"] = None
                self.active["opposite_streak_count"] = 0
                self._on_event(self._event("same_direction_shock_merged", as_of_ms, m_die=mdie.get("m_die")))
                return
            self._on_event(self._event("closed_by_same_direction_shock_after_merge_window", as_of_ms, status="closed"))
            self.active = None
            self._start_episode(direction, abs_value, mdie, as_of_ms)
            return
        self._handle_opposite(direction, abs_value, mdie, as_of_ms)

    def _handle_opposite(self, direction: int, abs_value: float, mdie: dict[str, Any], as_of_ms: int) -> None:
        assert self.active is not None
        first_as_of = self.active.get("opposite_first_as_of_ms")
        if self.active.get("opposite_streak_sign") == direction and as_of_ms - int(self.active.get("opposite_last_as_of_ms") or 0) == MINUTE_MS:
            self.active["opposite_streak_count"] += 1
        else:
            self.active["opposite_streak_count"] = 1
            first_as_of = as_of_ms
            self.active["opposite_first_as_of_ms"] = first_as_of
        self.active["opposite_streak_sign"] = direction
        self.active["opposite_last_as_of_ms"] = as_of_ms
        self._on_event(
            self._event(
                "opposite_strong_observed",
                as_of_ms,
                opposite_direction=_direction_label(direction),
                opposite_streak_count=self.active["opposite_streak_count"],
                opposite_first_as_of_ms=first_as_of,
            )
        )
        if self.active["opposite_streak_count"] >= self.opposite_confirm_bars:
            self._on_event(
                self._event(
                    "terminated_by_opposite_confirmed",
                    as_of_ms,
                    opposite_first_as_of_ms=first_as_of,
                    status="terminated",
                )
            )
            self.active = None
            self._start_episode(direction, abs_value, mdie, as_of_ms, confirmed_from_opposite_first_as_of_ms=first_as_of)

    def _handle_cooldown(self, abs_value: float, mdie: dict[str, Any], as_of_ms: int) -> None:
        assert self.active is not None
        if self.active.get("cooldown_as_of_ms") is None and abs_value <= self.cooldown_abs and as_of_ms > self.active["shock_as_of_ms"]:
            self.active["cooldown_as_of_ms"] = as_of_ms
            self.active["post_cooldown_max_abs_m_die"] = abs_value
            self.active["post_cooldown_max_as_of_ms"] = as_of_ms
            self.active["post_cooldown_max_abs_m_die_in_context_scope"] = abs_value
            self.active["post_cooldown_max_scope_as_of_ms"] = as_of_ms
            self.active["post_cooldown_reheated"] = False
            self.active["post_cooldown_reheat_as_of_ms"] = None
            self.active["post_cooldown_reheat_observed_in_context_scope"] = False
            self.active["post_cooldown_reheat_scope_as_of_ms"] = None
            self.active["emitted"].add("cooldown")
            self._on_event(self._event("cooldown_observed", as_of_ms, m_die=mdie.get("m_die")))
            self._on_observation(self._observation("cooldown", mdie, as_of_ms))
        cooldown_as_of = self.active.get("cooldown_as_of_ms")
        if cooldown_as_of is None:
            return
        self._emit_due_checkpoints(mdie, as_of_ms)

    def _emit_due_checkpoints(self, mdie: dict[str, Any], as_of_ms: int) -> None:
        assert self.active is not None
        cooldown_as_of = self.active.get("cooldown_as_of_ms")
        if cooldown_as_of is None:
            return
        for offset in self.config.get("checkpoints_after_cooldown_min", []):
            offset = int(offset)
            if offset <= 0:
                continue
            kind = f"plus{offset}"
            if kind in self.active["emitted"]:
                continue
            target_as_of = int(cooldown_as_of) + offset * MINUTE_MS
            if as_of_ms >= target_as_of:
                self.active["emitted"].add(kind)
                self._on_event(self._event(f"{kind}_observed", as_of_ms, target_as_of_ms=target_as_of))
                self._on_observation(self._observation(kind, mdie, as_of_ms))

    def _would_confirm_opposite(self, direction: int, abs_value: float, as_of_ms: int) -> bool:
        if self.active is None or abs_value < self.trigger_abs or direction == 0 or direction == self.active["direction"]:
            return False
        if self.active.get("opposite_streak_sign") == direction and as_of_ms - int(self.active.get("opposite_last_as_of_ms") or 0) == MINUTE_MS:
            return int(self.active.get("opposite_streak_count") or 0) + 1 >= self.opposite_confirm_bars
        return self.opposite_confirm_bars <= 1

    def _start_episode(
        self,
        direction: int,
        abs_value: float,
        mdie: dict[str, Any],
        as_of_ms: int,
        **extra: Any,
    ) -> None:
        self.counter += 1
        episode_id = _stable_episode_id(self.market_id, as_of_ms, direction)
        self.active = {
            "episode_id": episode_id,
            "episode_sequence": self.counter,
            "direction": direction,
            "direction_label": _direction_label(direction),
            "initial_m_die": safe_float(mdie.get("m_die")),
            "initial_abs_m_die": abs_value,
            "shock_as_of_ms": as_of_ms,
            "last_shock_as_of_ms": as_of_ms,
            "shock_magnitude": abs_value,
            "max_abs_m_die_since_shock": abs_value,
            "max_abs_m_die_as_of_ms": as_of_ms,
            "max_abs_m_die_in_context_scope": abs_value,
            "max_abs_m_die_scope_as_of_ms": as_of_ms,
            "m_die_observation_count": 1,
            "cooldown_as_of_ms": None,
            "post_cooldown_max_abs_m_die": None,
            "post_cooldown_max_as_of_ms": None,
            "post_cooldown_max_abs_m_die_in_context_scope": None,
            "post_cooldown_max_scope_as_of_ms": None,
            "post_cooldown_reheated": False,
            "post_cooldown_reheat_as_of_ms": None,
            "post_cooldown_reheat_observed_in_context_scope": False,
            "post_cooldown_reheat_scope_as_of_ms": None,
            "m_die_context_coverage": "full_from_shock",
            "m_die_context_restored_from_legacy_snapshot": False,
            "m_die_context_missing_legacy_fields": [],
            "emitted": {"shock"},
            "opposite_streak_sign": None,
            "opposite_streak_count": 0,
            "opposite_first_as_of_ms": None,
            **extra,
        }
        self._on_event(self._event("shock_started", as_of_ms, m_die=mdie.get("m_die"), status="active", **extra))
        self._on_observation(self._observation("shock", mdie, as_of_ms))

    def _record_active_mdie(self, value: float, abs_value: float, as_of_ms: int) -> None:
        assert self.active is not None
        self.active["m_die_observation_count"] = int(self.active.get("m_die_observation_count") or 1) + 1
        scope_max = safe_float(self.active.get("max_abs_m_die_in_context_scope"))
        if scope_max is None or abs_value > scope_max:
            self.active["max_abs_m_die_in_context_scope"] = abs_value
            self.active["max_abs_m_die_scope_as_of_ms"] = as_of_ms
        if self.active.get("m_die_context_coverage") == "full_from_shock":
            current_max = safe_float(self.active.get("max_abs_m_die_since_shock"))
            if current_max is None or abs_value > current_max:
                self.active["max_abs_m_die_since_shock"] = abs_value
                self.active["max_abs_m_die_as_of_ms"] = as_of_ms
        cooldown_as_of = self.active.get("cooldown_as_of_ms")
        if cooldown_as_of is None or as_of_ms < int(cooldown_as_of):
            return
        post_scope_max = safe_float(self.active.get("post_cooldown_max_abs_m_die_in_context_scope"))
        if post_scope_max is None or abs_value > post_scope_max:
            self.active["post_cooldown_max_abs_m_die_in_context_scope"] = abs_value
            self.active["post_cooldown_max_scope_as_of_ms"] = as_of_ms
        post_cooldown_status_known = self.active.get("post_cooldown_reheated") is not None
        if post_cooldown_status_known:
            post_max = safe_float(self.active.get("post_cooldown_max_abs_m_die"))
            if post_max is None or abs_value > post_max:
                self.active["post_cooldown_max_abs_m_die"] = abs_value
                self.active["post_cooldown_max_as_of_ms"] = as_of_ms
        if (
            as_of_ms > int(cooldown_as_of)
            and abs_value > self.cooldown_abs
        ):
            if post_cooldown_status_known and not self.active.get("post_cooldown_reheated"):
                self.active["post_cooldown_reheated"] = True
                self.active["post_cooldown_reheat_as_of_ms"] = as_of_ms
            if not self.active.get("post_cooldown_reheat_observed_in_context_scope"):
                self.active["post_cooldown_reheat_observed_in_context_scope"] = True
                self.active["post_cooldown_reheat_scope_as_of_ms"] = as_of_ms

    def _close_completed_if_idle(self, as_of_ms: int) -> None:
        if self.active is None or "plus30" not in self.active["emitted"]:
            return
        if as_of_ms - int(self.active["last_shock_as_of_ms"]) <= self.merge_ms:
            return
        self._on_event(self._event("closed_after_required_observations", as_of_ms, status="closed"))
        self.active = None

    def _close_timed_out(self, as_of_ms: int) -> None:
        if self.active is None:
            return
        if as_of_ms - int(self.active["shock_as_of_ms"]) <= self.max_wait_ms:
            return
        self._on_event(self._event("terminated_by_timeout", as_of_ms, status="terminated"))
        self.active = None

    def _observation(self, kind: str, mdie: dict[str, Any], as_of_ms: int) -> dict[str, Any]:
        assert self.active is not None
        episode_id = self.active["episode_id"]
        features = self.bars.feature_snapshot(
            as_of_ms,
            shock_as_of_ms=self.active["shock_as_of_ms"],
            shock_direction=self.active["direction"],
            shock_magnitude=self.active["shock_magnitude"],
            elapsed_from_shock_min=self._elapsed(as_of_ms),
        )
        context = self._observation_context(features, as_of_ms)
        row = {
            "schema": EVENT_SCHEMA,
            "event_family": "price_rebalance_candidate",
            "episode_id": episode_id,
            "episode_sequence": self.active.get("episode_sequence"),
            "active_episode_id": episode_id,
            "in_episode": True,
            "observation_id": f"{episode_id}:{kind}:{as_of_ms}",
            "observation_kind": kind,
            "as_of_ms": as_of_ms,
            "as_of_utc": ms_to_iso(as_of_ms),
            "entry_ms": entry_open_ms(as_of_ms),
            "entry_price": None,
            "entry_price_basis": "filled later by candidate builder; not available inside same as-of record",
            "m_die": safe_float(mdie.get("m_die")),
            "abs_m_die": abs(safe_float(mdie.get("m_die")) or 0.0),
            "m_die_direction": mdie.get("direction"),
            "shock_direction": self.active["direction_label"],
            "shock_as_of_ms": self.active["shock_as_of_ms"],
            "cooldown_as_of_ms": self.active.get("cooldown_as_of_ms"),
            "event_context": context,
            "event_context_schema": context["schema"],
            "initial_m_die": self.active.get("initial_m_die"),
            "initial_abs_m_die": self.active.get("initial_abs_m_die"),
            "max_abs_m_die_since_shock": self.active.get("max_abs_m_die_since_shock"),
            "post_cooldown_max_abs_m_die": self.active.get("post_cooldown_max_abs_m_die"),
            "post_cooldown_reheated": self.active.get("post_cooldown_reheated"),
            "post_cooldown_reheat_as_of_ms": self.active.get("post_cooldown_reheat_as_of_ms"),
            "opposite_streak_count": self.active.get("opposite_streak_count"),
            "opposite_first_as_of_ms": self.active.get("opposite_first_as_of_ms"),
            "current_return_from_shock": context["price_path"].get("return_from_shock"),
            "range_fraction_since_shock": context["price_path"].get("range_fraction_since_shock"),
            "range_expansion_since_shock": context["price_path"].get("range_expansion_since_shock"),
            "vwap_migration_since_shock": context["price_path"].get("vwap_migration_since_shock"),
            "feature_names": list(FEATURE_GROUPS["joint"]),
        }
        row.update(features)
        return row

    def _observation_context(self, features: dict[str, Any], as_of_ms: int) -> dict[str, Any]:
        assert self.active is not None
        path = self._price_path_context(as_of_ms)
        cooldown_as_of = self.active.get("cooldown_as_of_ms")
        return {
            "schema": OBSERVATION_CONTEXT_SCHEMA,
            "source": "closed_minute_bars_available_at_observation",
            "as_of_ms": as_of_ms,
            "as_of_utc": ms_to_iso(as_of_ms),
            "coverage": {
                "m_die_path_scope": self.active.get("m_die_context_coverage"),
                "restored_from_legacy_snapshot": bool(self.active.get("m_die_context_restored_from_legacy_snapshot")),
                "missing_legacy_fields": list(self.active.get("m_die_context_missing_legacy_fields") or []),
                "post_cooldown_reheat_status": (
                    "known"
                    if self.active.get("post_cooldown_reheated") is not None
                    else "unknown_before_snapshot_restore"
                ),
            },
            "shock": {
                "as_of_ms": self.active.get("shock_as_of_ms"),
                "direction": self.active.get("direction_label"),
                "initial_m_die": self.active.get("initial_m_die"),
                "initial_abs_m_die": self.active.get("initial_abs_m_die"),
                "max_abs_m_die_since_shock": self.active.get("max_abs_m_die_since_shock"),
                "max_abs_m_die_as_of_ms": self.active.get("max_abs_m_die_as_of_ms"),
                "max_abs_m_die_observed_in_context_scope": self.active.get("max_abs_m_die_in_context_scope"),
                "max_abs_m_die_scope_as_of_ms": self.active.get("max_abs_m_die_scope_as_of_ms"),
                "last_shock_as_of_ms": self.active.get("last_shock_as_of_ms"),
            },
            "cooldown": {
                "as_of_ms": cooldown_as_of,
                "elapsed_from_shock_min": features.get("elapsed_from_shock_min"),
                "post_cooldown_max_abs_m_die": self.active.get("post_cooldown_max_abs_m_die"),
                "post_cooldown_max_as_of_ms": self.active.get("post_cooldown_max_as_of_ms"),
                "post_cooldown_max_abs_m_die_observed_in_context_scope": self.active.get("post_cooldown_max_abs_m_die_in_context_scope"),
                "post_cooldown_max_scope_as_of_ms": self.active.get("post_cooldown_max_scope_as_of_ms"),
                "reheated_after_cooldown": self.active.get("post_cooldown_reheated"),
                "reheat_as_of_ms": self.active.get("post_cooldown_reheat_as_of_ms"),
                "reheat_observed_in_context_scope": self.active.get("post_cooldown_reheat_observed_in_context_scope"),
                "reheat_scope_as_of_ms": self.active.get("post_cooldown_reheat_scope_as_of_ms"),
            },
            "opposite_pressure": {
                "streak_direction": _direction_label(self.active.get("opposite_streak_sign")),
                "streak_count": self.active.get("opposite_streak_count"),
                "first_as_of_ms": self.active.get("opposite_first_as_of_ms"),
                "last_as_of_ms": self.active.get("opposite_last_as_of_ms"),
                "required_confirm_bars": self.opposite_confirm_bars,
            },
            "origin": {
                "confirmed_from_opposite_first_as_of_ms": self.active.get("confirmed_from_opposite_first_as_of_ms"),
            },
            "price_path": path,
            "boundaries": {
                "does_not_require_return_to_original_price": True,
                "not_consumed_by_natural_nr_model": True,
            },
        }

    def _price_path_context(self, as_of_ms: int) -> dict[str, Any]:
        assert self.active is not None
        shock_as_of_ms = self.active.get("shock_as_of_ms")
        if shock_as_of_ms is None:
            return {"status": "missing_shock_time"}
        shock_open = int(shock_as_of_ms) - MINUTE_MS + 1
        current_open = int(as_of_ms) - MINUTE_MS + 1
        rows = self.bars.range_by_open(shock_open, current_open)
        if not rows:
            return {"status": "missing_or_gap_between_shock_and_observation"}
        shock_price = self.bars.last_close_at_or_before(int(shock_as_of_ms))
        current_close = self.bars.last_close_at_or_before(int(as_of_ms))
        if shock_price is None or current_close is None or shock_price <= 0:
            return {"status": "invalid_price_basis", "bar_count": len(rows)}
        current_vwap = _rows_vwap(rows)
        current_range = _rows_range_fraction(rows, shock_price)
        prior_rows = self.bars.range_by_open(
            shock_open - len(rows) * MINUTE_MS,
            shock_open - MINUTE_MS,
        )
        prior_vwap = _rows_vwap(prior_rows) if prior_rows else None
        prior_range = _rows_range_fraction(prior_rows, shock_price) if prior_rows else None
        return {
            "status": "available",
            "bar_count": len(rows),
            "shock_price": shock_price,
            "current_close": current_close,
            "return_from_shock": current_close / shock_price - 1.0,
            "range_fraction_since_shock": current_range,
            "previous_same_length_range_fraction": prior_range,
            "range_expansion_since_shock": (
                current_range / prior_range - 1.0
                if current_range is not None and prior_range is not None and prior_range > 0
                else None
            ),
            "vwap_since_shock": current_vwap,
            "previous_same_length_vwap": prior_vwap,
            "vwap_migration_since_shock": (
                current_vwap / prior_vwap - 1.0
                if current_vwap is not None and prior_vwap is not None and prior_vwap > 0
                else None
            ),
            "current_close_to_vwap": (
                current_close / current_vwap - 1.0
                if current_vwap is not None and current_vwap > 0
                else None
            ),
            "prior_same_length_status": "available" if prior_rows else "insufficient_or_gap",
        }

    def _event(self, event_type: str, as_of_ms: int | None, **extra: Any) -> dict[str, Any]:
        active = self.active or {}
        row = {
            "schema": EVENT_SCHEMA,
            "event_family": "price_rebalance_candidate",
            "episode_id": active.get("episode_id"),
            "episode_sequence": active.get("episode_sequence"),
            "event_type": event_type,
            "as_of_ms": as_of_ms,
            "as_of_utc": ms_to_iso(as_of_ms) if as_of_ms is not None else None,
            "direction": active.get("direction_label"),
            "shock_as_of_ms": active.get("shock_as_of_ms"),
            "last_shock_as_of_ms": active.get("last_shock_as_of_ms"),
        }
        row.update(extra)
        return row

    def _elapsed(self, as_of_ms: int) -> float | None:
        if self.active is None:
            return None
        return (as_of_ms - int(self.active["shock_as_of_ms"])) / float(MINUTE_MS)


def _clean_m_die_klines(klines: list[dict[str, Any]], now_ms: int | None) -> list[dict[str, Any]]:
    by_time: dict[int, dict[str, Any]] = {}
    for item in klines or []:
        row = normalize_kline(item)
        if row is None:
            continue
        if now_ms is not None and not is_complete_minute_bar(row, as_of_ms=now_ms):
            continue
        by_time[int(row["open_time_ms"])] = row
    return [by_time[key] for key in sorted(by_time)]


def _rows_range_fraction(rows: list[dict[str, Any]] | None, basis_price: float | None) -> float | None:
    if not rows or basis_price is None or basis_price <= 0:
        return None
    highs = [safe_float(row.get("high")) for row in rows]
    lows = [safe_float(row.get("low")) for row in rows]
    if any(value is None for value in highs + lows):
        return None
    return (max(highs) - min(lows)) / float(basis_price)


def _rows_vwap(rows: list[dict[str, Any]] | None) -> float | None:
    if not rows:
        return None
    quote = 0.0
    volume = 0.0
    for row in rows:
        row_volume = safe_float(row.get("volume"))
        if row_volume is None or row_volume < 0:
            return None
        row_quote = safe_float(row.get("quote_volume"))
        if row_quote is None:
            close = safe_float(row.get("close"))
            if close is None or close <= 0:
                return None
            row_quote = close * row_volume
        quote += row_quote
        volume += row_volume
    if volume <= 0:
        return None
    return quote / volume


def _mark_restored_active_context(active: dict[str, Any]) -> None:
    context_keys = (
        "initial_m_die",
        "initial_abs_m_die",
        "max_abs_m_die_since_shock",
        "max_abs_m_die_as_of_ms",
        "m_die_observation_count",
        "post_cooldown_max_abs_m_die",
        "post_cooldown_max_as_of_ms",
        "post_cooldown_reheated",
        "post_cooldown_reheat_as_of_ms",
    )
    missing = [key for key in context_keys if key not in active]
    if missing:
        active["m_die_context_coverage"] = "after_snapshot_restore"
        active["m_die_context_restored_from_legacy_snapshot"] = True
        active["m_die_context_missing_legacy_fields"] = missing
        active.setdefault("initial_m_die", None)
        active.setdefault("initial_abs_m_die", None)
        active.setdefault("max_abs_m_die_since_shock", None)
        active.setdefault("max_abs_m_die_as_of_ms", None)
        active.setdefault("m_die_observation_count", None)
        active.setdefault("post_cooldown_max_abs_m_die", None)
        active.setdefault("post_cooldown_max_as_of_ms", None)
        active.setdefault("post_cooldown_reheated", None)
        active.setdefault("post_cooldown_reheat_as_of_ms", None)
        active.setdefault("post_cooldown_reheat_observed_in_context_scope", False)
    else:
        active.setdefault("m_die_context_coverage", "full_from_shock")
        active.setdefault("m_die_context_restored_from_legacy_snapshot", False)
        active.setdefault("m_die_context_missing_legacy_fields", [])
    active.setdefault("max_abs_m_die_in_context_scope", active.get("max_abs_m_die_since_shock"))
    active.setdefault("max_abs_m_die_scope_as_of_ms", active.get("max_abs_m_die_as_of_ms"))
    active.setdefault(
        "post_cooldown_max_abs_m_die_in_context_scope",
        active.get("post_cooldown_max_abs_m_die"),
    )
    active.setdefault("post_cooldown_max_scope_as_of_ms", active.get("post_cooldown_max_as_of_ms"))
    active.setdefault("post_cooldown_reheat_observed_in_context_scope", active.get("post_cooldown_reheated"))
    active.setdefault("post_cooldown_reheat_scope_as_of_ms", active.get("post_cooldown_reheat_as_of_ms"))


def _m_die_no_value(reason: str, bars_loaded: int, bars_required: int, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "MicroDirectionalImbalanceExtent",
        "factor_name": "M-DIE",
        "factor_version": "v1.1_final",
        "interval": config.get("m_die_interval", "1m"),
        "window": "15m",
        "n_bars": int(config.get("m_die_window_bars", 15)),
        "rolling": True,
        "last_closed_bar_time": None,
        "direction": "NO_DIRECTION",
        "m_die": 0.0,
        "score": 0.0,
        "level": "NO_DIRECTIONAL_MOVE",
        "move_shape": "NO_MOVE",
        "components": {},
        "data_status": {
            "source": "api_backfill_or_live_polling",
            "bars_loaded": bars_loaded,
            "bars_required": bars_required,
            "uses_closed_bars_only": True,
            "data_state": reason,
        },
    }


def _m_die_zero(reason: str, clean: list[dict[str, Any]], total_return: float, config: dict[str, Any]) -> dict[str, Any]:
    required = int(config.get("m_die_window_bars", 15)) + 1
    result = _m_die_no_value(reason, len(clean), required, config)
    last = clean[-1] if clean else {}
    result["last_closed_bar_time"] = last.get("close_time") or last.get("close_time_ms") or last.get("open_time")
    result["data_status"]["data_state"] = "OK"
    result["components"] = {
        "displacement": {
            "score": 0.0,
            "raw": {
                "window_log_return": total_return,
                "window_return_pct": math.exp(total_return) - 1.0,
            },
        },
    }
    return result


def _linear_score(value: Any, start: Any, full: Any) -> float:
    value = safe_float(value) or 0.0
    start = safe_float(start) or 0.0
    full = safe_float(full) or start
    return clamp((value - start) / max(full - start, 1e-12), 0.0, 1.0) or 0.0


def _stddev(values: list[float]) -> float:
    vals = [safe_float(item) for item in values or []]
    vals = [item for item in vals if item is not None and math.isfinite(item)]
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / float(len(vals))
    variance = sum((item - mean) ** 2 for item in vals) / float(len(vals))
    return math.sqrt(max(0.0, variance))


def _m_die_level(abs_value: float) -> str:
    if abs_value < 0.25:
        return "NO_DIRECTIONAL_MOVE"
    if abs_value < 0.45:
        return "MILD_DIRECTIONAL_MOVE"
    if abs_value < 0.65:
        return "CLEAR_DIRECTIONAL_MOVE"
    return "STRONG_DIRECTIONAL_MOVE"


def _m_die_move_shape(score: float, coverage_ratio: float, e_score: float) -> str:
    if score < 0.25:
        return "NO_MOVE"
    if coverage_ratio < 0.35 and e_score > 0.75:
        return "IMPULSE_SHIFT"
    if coverage_ratio >= 0.50 and e_score >= 0.55:
        return "DRIFT_RUN"
    return "CHOPPY_DRIFT"


def _direction_label(direction: Any) -> str | None:
    signed = sign(direction)
    if signed > 0:
        return "UP"
    if signed < 0:
        return "DOWN"
    return None


def _stable_episode_id(market_id: str, shock_as_of_ms: int, direction: int) -> str:
    safe_market = "".join(ch.lower() if ch.isalnum() else "_" for ch in market_id).strip("_") or "market"
    return f"price_rebalance_{safe_market}_{int(shock_as_of_ms)}_{_direction_label(direction).lower()}"


class _JsonlWriter:
    def __init__(self, path: Path):
        self.path = path
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("w", encoding="utf-8", newline="\n")
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.stream is not None:
            self.stream.close()

    def write(self, row: dict[str, Any]) -> None:
        assert self.stream is not None
        self.stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")


class _CsvWriter:
    def __init__(self, path: Path, fieldnames: tuple[str, ...]):
        self.path = path
        self.fieldnames = fieldnames
        self.stream = None
        self.writer = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("w", encoding="utf-8-sig", newline="")
        self.writer = csv.DictWriter(self.stream, fieldnames=self.fieldnames, extrasaction="ignore")
        self.writer.writeheader()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.stream is not None:
            self.stream.close()

    def write(self, row: dict[str, Any]) -> None:
        assert self.writer is not None
        self.writer.writerow(
            {
                key: json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else value
                for key, value in row.items()
            }
        )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
    os.replace(tmp, path)


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", nargs="+", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--as-of-upper-ms", type=int)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    counts = {"events": 0, "observations": 0, "clock_rows": 0, "input_rows": 0}
    with (
        _JsonlWriter(args.output_dir / "price_rebalance_events.jsonl") as event_json,
        _JsonlWriter(args.output_dir / "price_rebalance_observations.jsonl") as observation_json,
        _JsonlWriter(args.output_dir / "clock_observations.jsonl") as clock_json,
        _CsvWriter(args.output_dir / "price_rebalance_events.csv", EVENT_COLUMNS) as event_csv,
        _CsvWriter(args.output_dir / "price_rebalance_observations.csv", OBSERVATION_COLUMNS) as observation_csv,
        _CsvWriter(args.output_dir / "clock_observations.csv", OBSERVATION_COLUMNS) as clock_csv,
    ):
        runner = StreamingReplay(
            as_of_upper_ms=args.as_of_upper_ms,
            on_event=lambda row: (event_json.write(row), event_csv.write(row), counts.__setitem__("events", counts["events"] + 1)),
            on_observation=lambda row: (
                observation_json.write(row),
                observation_csv.write(row),
                counts.__setitem__("observations", counts["observations"] + 1),
            ),
            on_clock=lambda row: (clock_json.write(row), clock_csv.write(row), counts.__setitem__("clock_rows", counts["clock_rows"] + 1)),
        )
        for path in args.input:
            for row in iter_binance_klines(path):
                counts["input_rows"] += 1
                runner.push(row)
        runner.finish()
    write_json(
        args.output_dir / "event_manifest.json",
        {
            "schema": EVENT_SCHEMA,
            "events": counts["events"],
            "observations": counts["observations"],
            "clock_rows": counts["clock_rows"],
            "input_rows": counts["input_rows"],
            "feature_names": list(FEATURE_GROUPS["joint"]),
        },
    )
    print(json.dumps(counts, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
