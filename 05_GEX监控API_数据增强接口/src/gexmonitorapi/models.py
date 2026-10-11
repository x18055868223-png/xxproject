from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

SectionName = Literal["gex_board", "gamma_exposure", "gamma_profiles", "volatility", "flow", "expiry"]
RefreshSection = Literal["gex_board", "gamma_exposure", "gamma_profiles", "volatility", "flow", "expiry", "all"]

SECTIONS: tuple[SectionName, ...] = (
    "gex_board",
    "gamma_exposure",
    "gamma_profiles",
    "volatility",
    "flow",
    "expiry",
)

GEX_TIME_SEMANTICS_SCHEMA = "gex_time_semantics@1.0.0"

# Upstream fetch each section depends on; an error under that key keeps the
# section's last good values (see MetricsCache._apply_source_snapshot).
SECTION_SOURCE: dict[SectionName, str] = {
    "gex_board": "gex",
    "gamma_exposure": "gex",
    "gamma_profiles": "gex",
    "volatility": "bundle",
    "flow": "bundle",
    "expiry": "bundle",
}
STALE_SOURCE_ERRORS: tuple[str, ...] = ("gex", "bundle")
# Top-level availability/stale describe the signal core that consumers gate on
# (FMZ GGR / SpaceGate read gex_board + gamma_exposure). Enrichment sections
# report their own enrichment_availability / enrichment_stale.
CORE_SECTIONS: tuple[SectionName, ...] = ("gex_board", "gamma_exposure")
CORE_SOURCE_ERRORS: tuple[str, ...] = ("gex",)
ENRICHMENT_SOURCE_ERRORS: tuple[str, ...] = ("bundle",)

# Field registry. Every field listed here is required unless it is in
# OPTIONAL_FIELDS. Nested keys (term_structure[].skew_25d,
# flow.whale_*.lead_expiry / active_strike) are never availability-checked;
# they are listed in OPTIONAL_SUBFIELDS for documentation only.
SECTION_FIELDS: dict[SectionName, tuple[str, ...]] = {
    "gex_board": ("total_net_gex", "dvol", "market_state"),
    "gamma_exposure": (
        "n2",
        "n1",
        "flip_point",
        "volatility_trigger",
        "spot_price",
        "magnet_price",
        "p1",
        "p2",
        "hedging_flows",
    ),
    "gamma_profiles": ("zero_dte", "weekly"),
    "volatility": ("pcr", "pcr_oi", "term_structure", "atm_iv", "iv_percentile", "rr_skew"),
    "flow": ("put_call_ratio", "call_volume", "put_volume", "call_oi", "put_oi", "whale_1h", "whale_24h"),
    "expiry": ("max_pain_price", "max_pain_target_expiry", "next_expiry"),
}
LIST_FIELDS = frozenset({"term_structure", "rr_skew"})
# 0DTE bucket can be absent around the daily expiry rollover.
# hedging_flows is context only and must not degrade the signal core.
OPTIONAL_FIELDS = frozenset({"gamma_profiles.zero_dte", "gamma_exposure.hedging_flows"})
OPTIONAL_SUBFIELDS: tuple[str, ...] = (
    "volatility.term_structure[].skew_25d",
    "flow.whale_1h.lead_expiry",
    "flow.whale_1h.active_strike",
    "flow.whale_24h.lead_expiry",
    "flow.whale_24h.active_strike",
)


def empty_section_data(section: SectionName) -> dict[str, Any]:
    return {field: [] if field in LIST_FIELDS else None for field in SECTION_FIELDS[section]}


def is_empty_value(value: Any) -> bool:
    return value is None or (isinstance(value, list | dict) and not value)


def is_missing(section: str, field: str, value: Any) -> bool:
    """Required field with no value. A zero-trade whale window is a dict of
    zeros, so it is present; optional fields are never missing."""
    return f"{section}.{field}" not in OPTIONAL_FIELDS and is_empty_value(value)


@dataclass(slots=True)
class ParsedSection:
    section: SectionName
    data: dict[str, Any]
    missing_fields: list[str]
    field_status: dict[str, dict[str, Any]]
