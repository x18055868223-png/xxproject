from __future__ import annotations

import re
from typing import Any

from .models import SECTION_FIELDS, SectionName, ParsedSection, empty_section_data

# A numeric token as it appears on the rendered page. Handles an optional leading
# "$" and sign in either order ("$-67M" and "-$24.2M"), thousands separators,
# decimals, and a magnitude suffix (K/M/B/T). Trailing "%" or "x" are ignored.
# Suffix must be attached to the digits (e.g. "$27.7M", "106K"); a space before it
# would otherwise swallow the first letter of the next word (e.g. "0.93 TERM" -> T).
_NUMBER_RE = re.compile(
    r"(?:\$)?\s*(?P<sign>[-+])?\s*(?:\$)?\s*(?P<num>\d[\d,]*(?:\.\d+)?)(?P<suffix>[KkMmBbTt])?"
)
_SUFFIX_MULT = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}


def parse_section(section: SectionName, text: str) -> ParsedSection:
    norm = _normalize(text)
    data = empty_section_data(section)

    if section == "gex_board":
        data["total_net_gex"] = _num_after(norm, ["TOTAL NET GEX", "Total Net GEX", "Net GEX"], window=24)
        data["dvol"] = _num_after(norm, ["DVOL"], window=16)
        data["market_state"] = _market_state(norm)
    elif section == "gamma_exposure":
        # The "GAMMA COMPONENTS" block holds clean LABEL value rows; slicing to it
        # avoids the chart legend (P1 P2 N1 N2 ...) and axis ticks above it.
        scope = _slice_from(norm, ["GAMMA COMPONENTS"]) or norm
        data["n2"] = _num_after(scope, ["N2"], window=14)
        data["n1"] = _num_after(scope, ["N1"], window=14)
        data["flip_point"] = _num_after(scope, ["FLIP POINT", "FLIP", "Flip Point", "Flip"], window=18)
        data["volatility_trigger"] = _num_after(scope, ["VOL TRIGGER", "Volatility Trigger"], window=18)
        data["spot_price"] = _num_after(scope, ["SPOT PRICE", "Spot Price"], window=18)
        data["magnet_price"] = _num_after(scope, ["MAGNET", "Magnet"], window=14)
        data["p1"] = _num_after(scope, ["P1"], window=14)
        data["p2"] = _num_after(scope, ["P2"], window=14)
    elif section == "volatility":
        data["iv_rv_ratio"] = _num_after(norm, ["IV/RV RATIO", "IV/RV Ratio"], window=14)
        data["pcr"] = _num_after(norm, ["PCR (VOLUME)", "PCR", "Put/Call Ratio"], window=24)
        data["term_structure"] = _term_structure(norm)
    elif section == "flow":
        data["call_premium"] = _num_after(norm, ["CALL PREMIUM", "Call Premium"], window=18)
        data["put_premium"] = _num_after(norm, ["PUT PREMIUM", "Put Premium"], window=18)
        data["call_put_bias"] = _call_put_bias(norm)
        data["put_call_ratio"] = _num_after(norm, ["P/C RATIO", "Put/Call Ratio"], window=16)
        data["abnormal_signal"] = _abnormal_signal(norm)

    return _with_status(section, data)


def _with_status(section: SectionName, data: dict[str, Any]) -> ParsedSection:
    missing_fields: list[str] = []
    field_status: dict[str, dict[str, str]] = {}
    for field in SECTION_FIELDS[section]:
        path = f"{section}.{field}"
        value = data.get(field)
        missing = value is None or (isinstance(value, list) and not value)
        if missing:
            missing_fields.append(path)
            field_status[path] = {
                "status": "missing",
                "reason": "not_found_in_rendered_page",
            }
        else:
            field_status[path] = {"status": "ok", "reason": "found_in_rendered_page"}
    return ParsedSection(section=section, data=data, missing_fields=missing_fields, field_status=field_status)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _slice_from(text: str, anchors: list[str]) -> str | None:
    low = text.lower()
    for anchor in anchors:
        idx = low.find(anchor.lower())
        if idx != -1:
            return text[idx:]
    return None


def _to_number(token: str) -> float | None:
    if not token:
        return None
    match = _NUMBER_RE.search(token)
    if not match:
        return None
    try:
        value = float(match.group("num").replace(",", ""))
    except ValueError:
        return None
    suffix = match.group("suffix")
    if suffix:
        value *= _SUFFIX_MULT[suffix.lower()]
    if match.group("sign") == "-":
        value = -value
    return value


def _num_after(text: str, labels: list[str], window: int = 24) -> float | None:
    """First numeric value within `window` chars after a standalone label token."""
    for label in labels:
        pattern = r"(?<![0-9A-Za-z])" + re.escape(label) + r"(?![0-9A-Za-z])"
        for match in re.finditer(pattern, text, re.IGNORECASE):
            value = _to_number(text[match.end(): match.end() + window])
            if value is not None:
                return value
    return None


def _market_state(text: str) -> str | None:
    low = text.lower()
    if "short gamma" in low or "negative gamma" in low or "gamma is negative" in low or "net short" in low:
        return "negative_gamma"
    if "long gamma" in low or "positive gamma" in low or "gamma is positive" in low or "net long" in low:
        return "positive_gamma"
    if "neutral" in low:
        return "neutral"
    return None


def _call_put_bias(text: str) -> str | None:
    anchor = re.search(r"(?<![0-9A-Za-z])CALL\s*/\s*PUT TILT(?![0-9A-Za-z])", text, re.IGNORECASE)
    segment = text[anchor.end(): anchor.end() + 40] if anchor else ""
    tilt = re.search(r"(\d{1,3})\s*%\s*(Call|Put)", segment, re.IGNORECASE)
    if tilt:
        return f"{tilt.group(1)}% {tilt.group(2).title()}"
    return None


def _abnormal_signal(text: str) -> str | None:
    anchor = re.search(r"(?<![0-9A-Za-z])FLOW READ(?![0-9A-Za-z])", text, re.IGNORECASE)
    if anchor:
        segment = text[anchor.end(): anchor.end() + 160].strip(" :：-")
        sentence = re.split(r"(?<=[.。])\s", segment, maxsplit=1)[0].strip()
        if sentence:
            return sentence
    trade = re.search(r"(Single trade \$[\d.,KMBT]+.*?on BTC-[\w-]+)", text)
    if trade:
        return trade.group(1).strip()
    return None


def _term_structure(text: str) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    pattern = re.compile(
        r"(\d{1,2}\s+[A-Z]{3}\s+\d{2})\s+\d+\s*d\s*to\s*expiry\s+([\d.]+)\s*%\s+(-?[\d.]+)\s*%",
        re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        atm_iv = _to_number(match.group(2))
        skew_25d = _to_number(match.group(3))
        if atm_iv is None:
            continue
        rows.append(
            {
                "expiry": re.sub(r"\s+", " ", match.group(1)).strip(),
                "atm_iv": atm_iv,
                "skew_25d": skew_25d,
            }
        )
    return rows
