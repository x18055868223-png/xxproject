"""Path statistics of the 1-day vertical that the page may quote (docs/opus/16 §3.3-3.4, 17 §4).

One place for the numbers, shared by the Decision Path (docs/opus/20) and the experience
reading (docs/opus/19). They are path statistics of an 08:00 UTC daily expiry with the short
leg near 0.4σ; outside that basis nothing is quoted.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Mapping

SOURCE = "16 号 §3.3–3.4；17 号 §4"
# still untouched at this hour of the 08:00-to-08:00 cycle -> later touch % / beyond the short leg at expiry %
SURVIVAL = ((12, {"put": (51, 23), "call": (52, 27)}), (16, {"put": (30, 13), "call": (32, 16)}),
            (20, {"put": (19, 9), "call": (23, 13)}), (26, {"put": (7, 3), "call": (10, 6)}))   # 26 = 02:00
SURVIVAL_NOTE_CN = "08:00 建仓并于次日 08:00 交割、短腿 0.4σ 的路径统计（16 号 §3.3），其他建仓时点和距离只作近似"
TOUCH_NOTE_CN = "触及时距到期的小时数分档（16 号 §3.4，08:00 交割；方向 A，幅度 C）"


def _num(v: Any) -> float | None:
    import math
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def daily_basis(clock: Mapping[str, Any]) -> tuple[bool, str | None]:
    """Whether the statistics apply to this reading of the session clock, and why not."""
    expiry, left = _num(clock.get("expiry_ms")), _num(clock.get("hours_to_expiry"))
    if expiry is None or left is None:
        return False, "缺日内时段读数"
    if clock.get("expiry_source") not in (None, "contract"):
        return False, "日内时段读数不是按本合约到期计算的"
    if left <= 0:
        return False, "合约已到期"
    if dt.datetime.fromtimestamp(expiry / 1000, dt.timezone.utc).hour != 8:
        return False, "到期不在 08:00 UTC，路径统计不适用"
    if left > 24.5:
        return False, "距到期超过一天，路径统计不适用"
    return True, None


def next_survival(hours_left: float, side: str) -> tuple[int, int, int] | None:
    """(UTC hour, later touch %, beyond at expiry %) of the next row after now, or None."""
    hour = 8 + max(0.0, 24 - hours_left)          # position in the 08:00-to-08:00 cycle of this expiry
    row = next(((h, r) for h, r in SURVIVAL if h > hour), None)
    if row is None or side not in ("put", "call"):
        return None
    h, r = row
    return h % 24, r[side][0], r[side][1]


def touch_text(hours_left: float) -> str:
    """What a touch now has led to, by the hours left at the touch."""
    if hours_left > 12:
        return "约一半到期越过短腿、约四分之一走到保护腿"
    if 2 <= hours_left <= 6:
        return "47–65% 到期越过短腿，走到保护腿约 4–8%（n=13–45）"
    return "这一档没有统计数字"
