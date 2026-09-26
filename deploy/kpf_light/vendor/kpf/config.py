from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import KpfConfig


DEFAULT_CONFIG: dict[str, Any] = {
    "symbol": "BTCUSDT",
    "market": "BINANCE_USD_M_FUTURES",
    "data_type": "aggTrades",
    "workspace_root": "./kpf_workspace",
    "data_source_base_url": "https://data.binance.vision",
    "history_days": 90,
    "fresh_confirm_days": 60,
    "internal_bin_width_usd": 50,
    "output_price_step_usd": 250,
    "display_band_half_width_usd": 125,
    "volume_bar_size_btc": 100,
    "time_weights": {
        "fresh_0_7d": 1.00,
        "mid_7_30d": 0.75,
        "memory_30_90d": 0.35,
    },
    "smooth_kernel": [0.25, 0.50, 0.25],
    "max_targets_each_side": 3,
    "basin_width": {
        "hard_min_usd": 100,
        "preferred_max_usd": 500,
        "hard_max_usd": 750,
    },
    # DEPRECATED / UNUSED: the live A/B/C grading is governed by the hardcoded
    # EVIDENCE_V102_RULES in evidence_rules.py, NOT by this block. These numbers
    # are retained only for backward-compatible config parsing; editing them has
    # no effect on grades. Do not treat them as the authoritative cutoffs.
    "thresholds": {
        "base": {
            "prominence_norm": 0.25,
            "excess_mass_norm": 0.25,
            "participating_volume_bars": 8,
        },
        "A": {
            "prominence_norm": 0.55,
            "excess_mass_norm": 0.60,
            "participating_volume_bars": 25,
        },
        "B": {
            "prominence_norm": 0.35,
            "excess_mass_norm": 0.40,
            "participating_volume_bars": 12,
        },
        "C": {
            "prominence_norm": 0.25,
            "excess_mass_norm": 0.25,
            "participating_volume_bars": 8,
        },
    },
    "download": {
        "max_retries": 3,
        "timeout_seconds": 30,
        "concurrent_downloads": 3,
        "batch_size": 7,
        "cache_batch_size": 3,
        "remote_missing_cooldown_hours": 24,
    },
    "report": {
        "include_debug_density": True,
        "include_debug_candidates": True,
        "include_raw_center_debug": True,
    },
}


class ConfigError(ValueError):
    pass


class ConfigLoader:
    def load(self, path: str | Path, overrides: dict[str, Any] | None = None) -> KpfConfig:
        return load_config(path, overrides)


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value == "":
        return {}
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part.strip()) for part in inner.split(",")]
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        if any(ch in value for ch in (".", "e", "E")):
            return float(value)
        return int(value)
    except ValueError:
        return value


def _load_simple_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if ":" not in line:
            raise ConfigError(f"Unsupported YAML line in {path}: {raw_line}")
        key, value = line.split(":", 1)
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        parsed = _parse_scalar(value)
        parent[key.strip()] = parsed
        if isinstance(parsed, dict) and value.strip() == "":
            stack.append((indent, parsed))
    return root


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str | Path, overrides: dict[str, Any] | None = None) -> KpfConfig:
    path = Path(path)
    data = _deep_merge(DEFAULT_CONFIG, _load_simple_yaml(path))
    if overrides:
        data = _deep_merge(data, {k: v for k, v in overrides.items() if v is not None})
    return config_from_dict(data)


def config_from_dict(data: dict[str, Any]) -> KpfConfig:
    if data["market"] != "BINANCE_USD_M_FUTURES":
        raise ConfigError("phase 1 supports only BINANCE_USD_M_FUTURES")
    if data["data_type"] != "aggTrades":
        raise ConfigError("phase 1 supports only aggTrades")
    if int(data["history_days"]) < int(data["fresh_confirm_days"]):
        raise ConfigError("history_days must be >= fresh_confirm_days")
    kernel = [float(x) for x in data["smooth_kernel"]]
    if len(kernel) != 3:
        raise ConfigError("smooth_kernel must have exactly 3 values")
    root = Path(str(data["workspace_root"])).expanduser()
    return KpfConfig(
        symbol=str(data["symbol"]).upper(),
        market=str(data["market"]),
        data_type=str(data["data_type"]),
        workspace_root=root,
        data_source_base_url=str(data["data_source_base_url"]).rstrip("/"),
        history_days=int(data["history_days"]),
        fresh_confirm_days=int(data["fresh_confirm_days"]),
        internal_bin_width_usd=int(data["internal_bin_width_usd"]),
        output_price_step_usd=int(data["output_price_step_usd"]),
        display_band_half_width_usd=int(data["display_band_half_width_usd"]),
        volume_bar_size_btc=Decimal(str(data["volume_bar_size_btc"])),
        time_weights={k: float(v) for k, v in data["time_weights"].items()},
        smooth_kernel=kernel,
        max_targets_each_side=int(data["max_targets_each_side"]),
        basin_width={k: int(v) for k, v in data["basin_width"].items()},
        thresholds={
            grade: {k: float(v) for k, v in values.items()}
            for grade, values in data["thresholds"].items()
        },
        download={k: int(v) for k, v in data["download"].items()},
        report={k: bool(v) for k, v in data["report"].items()},
    )
