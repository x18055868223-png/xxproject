from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

SectionName = Literal["gex_board", "gamma_exposure", "volatility", "flow"]
RefreshSection = Literal["gex_board", "gamma_exposure", "volatility", "flow", "all"]

SECTIONS: tuple[SectionName, ...] = (
    "gex_board",
    "gamma_exposure",
    "volatility",
    "flow",
)

SECTION_TABS: dict[SectionName, str] = {
    "gex_board": "gex",
    "gamma_exposure": "gamma",
    "volatility": "volatility",
    "flow": "flow",
}

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
    ),
    "volatility": ("iv_rv_ratio", "pcr", "term_structure"),
    "flow": (
        "call_premium",
        "put_premium",
        "call_put_bias",
        "put_call_ratio",
        "abnormal_signal",
    ),
}


def empty_section_data(section: SectionName) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for field in SECTION_FIELDS[section]:
        data[field] = [] if field == "term_structure" else None
    return data


@dataclass(slots=True)
class ParsedSection:
    section: SectionName
    data: dict[str, Any]
    missing_fields: list[str]
    field_status: dict[str, dict[str, str]]

