"""The inquirer's reading view of a case file (docs/opus/18 §4, 2026-10-22).

The inquirer copies what it reads: millisecond clocks, 17-digit floats and enum codes in
the case came back in its prose. This view rewrites the copy sent to the model -- clocks as
UTC text, durations in hours or minutes, numbers at display precision, enum codes in
Chinese (and, optionally, field names in Chinese) -- and the validator traces numbers
against the same view. The archived case file keeps its raw values and field names.

Codes are read with the page's dictionaries (deploy/astra_workbench_v3/inquiry_terms.js and
glossary.js), exported to astra_inquiry_terms_v1.json by astra_inquiry_terms_sync.mjs.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

VERSION = "inquiry_reading@0.1"
TERMS_PATH = Path(__file__).with_name("astra_inquiry_terms_v1.json")
CLOCK_MIN, CLOCK_MAX = 1_500_000_000_000, 2_500_000_000_000
CODE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
UNIT_CN = {"btc": "BTC", "usd": "美元", "pct": "%", "ms": "毫秒", "usd_m": "百万美元"}
# kept as written in values: units, venues and the market abbreviations the prompt allows
KEEP = {"BTC", "USD", "USDT", "UTC", "Put", "Call", "put", "call", "Deribit", "Binance", "FMZ", "IV", "RV",
        "ATM", "RR", "OI", "GEX", "KPF", "TMV", "CVD", "EDB", "PCR", "ETF", "DVOL"}


@lru_cache(maxsize=1)
def _terms() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    data = json.loads(TERMS_PATH.read_text(encoding="utf-8"))
    return data["iq"], data["term"], data["field"]


def terms_digest() -> str:
    """Short hash of the dictionary, recorded with the reading-view version: a dictionary change
    alters what the model reads and how numbers trace (docs/opus/18 §4)."""
    return hashlib.sha256(TERMS_PATH.read_bytes()).hexdigest()[:12]


def _direct(tok: str) -> str | None:
    iq, term, field = _terms()
    if tok in iq:
        return iq[tok]
    if tok in term:
        return term[tok]
    return field.get(tok) if "_" in tok else None


def lookup(tok: str) -> str | None:
    """A code in words, as the page reads it (inquiry_terms.js lookup)."""
    hit = _direct(tok)
    if hit is not None:
        return hit
    versioned = re.fullmatch(r"(.+)\.v\d+", tok)
    if versioned:
        base = lookup(versioned.group(1))
        if base is not None:
            return base
    scoped = re.fullmatch(r"(.+?)_(contract_expiry|is_contract_expiry)", tok)
    if scoped:
        base = _direct(scoped.group(1))
        if base is not None:
            return f"{base}（本期）"
    unit = re.fullmatch(r"(.+?)_(usd_m|btc|usd|pct|ms)", tok)
    if unit:
        base = _direct(unit.group(1))
        if base is not None:
            return f"{base}（{UNIT_CN[unit.group(2)]}）"
    if "." in tok:
        parts = [_direct(p) for p in tok.split(".")]
        if all(p is not None for p in parts):
            return " · ".join(parts)  # type: ignore[arg-type]
    return None


def clock_text(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def duration_text(ms: float) -> str:
    sign, a = ("-" if ms < 0 else ""), abs(ms)
    if a < 60_000:
        return f"{sign}{a / 1000:.0f} 秒"
    if a < 3_600_000:
        return f"{sign}{a / 60_000:.0f} 分钟"
    return f"{sign}{a / 3_600_000:.1f} 小时"


def display_number(x: float) -> float:
    """|x| >= 100: two decimals; otherwise four significant digits."""
    if not math.isfinite(x) or x == 0:
        return x
    return round(x, 2) if abs(x) >= 100 else float(f"{x:.4g}")


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _value(v: Any) -> Any:
    if isinstance(v, float):
        return display_number(v)
    if isinstance(v, int) and not isinstance(v, bool) and CLOCK_MIN <= v <= CLOCK_MAX:
        return clock_text(v)
    if isinstance(v, str) and v not in KEEP and CODE.fullmatch(v):
        return lookup(v) or v
    return v


def _key_word(key: str, stripped: str | None) -> str | None:
    word = (lookup(stripped) if stripped else None) or lookup(key)
    return word.replace("（毫秒）", "") if word and stripped else word


def _walk(value: Any, chinese_keys: bool) -> Any:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key = str(key)
            stripped = None
            if _is_number(item) and key.endswith("_ms"):
                stripped = key[:-3]
                item = (clock_text(int(item)) if CLOCK_MIN <= item <= CLOCK_MAX
                        else duration_text(item) if item < CLOCK_MIN else item)
            else:
                item = _walk(item, chinese_keys)
            # "expiry_ms" next to "expiry": keep the original name rather than collide
            name = stripped if stripped and stripped not in value else key
            if chinese_keys:
                word = _key_word(key, stripped)
                if word and word not in value and word not in out:
                    name = word
            if name in out:          # never let a rename overwrite a sibling
                name = key
            out[name] = item
        return out
    if isinstance(value, (list, tuple)):
        return [_walk(item, chinese_keys) for item in value]
    return _value(value)


def view(case_file: Mapping[str, Any], *, chinese_keys: bool = False) -> dict[str, Any]:
    """The copy of a case (already without L9) that the inquirer reads. Refs, layer and entry
    order, labels, quality and lineage are unchanged; values and relations are made readable."""
    out = deepcopy(dict(case_file))
    for entries in (out.get("layers") or {}).values():
        for entry in entries:
            entry["values"] = _walk(entry.get("values"), chinese_keys)
            entry["relations"] = _walk(entry.get("relations") or {}, chinese_keys)
            at = entry.pop("asof_ms", None)
            entry["asof"] = clock_text(at) if _is_number(at) and CLOCK_MIN <= at <= CLOCK_MAX else at
    if _is_number(out.get("asof_ms")):
        out["asof"] = clock_text(int(out.pop("asof_ms")))
    out["build"] = {**(out.get("build") or {}), "reading_view": VERSION, "chinese_keys": chinese_keys}
    out["build"].pop("built_at_ms", None)
    return out
