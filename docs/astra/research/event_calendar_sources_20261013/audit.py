"""Audit tools/astra_event_calendar_v1.py against the official schedule snapshots in this folder.

usage: python audit.py
Prints every disagreement (missing, extra, wrong time/period/flags) and the cross-check
between BLS's two published views. Exit code 1 when anything disagrees.
"""
import re
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "tools"))
import astra_event_calendar_v1 as cal  # noqa: E402

FROM = "2026-10-01"
MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                      "September", "October", "November", "December"], 1)}
BLS_KIND = {"Consumer Price Index": "CPI", "Producer Price Index": "PPI", "Employment Situation": "NFP"}
QUARTER_CN = {"1st": "一", "2nd": "二", "3rd": "三", "4th": "四"}
ESTIMATE_CN = {"Advance": "初值", "Second": "修正值", "Third": "终值"}


def t24(text):
    hh, mm, half = re.match(r"(\d{1,2}):(\d{2}) ?([AP]M)", text.strip()).groups()
    hh = int(hh) % 12 + (12 if half == "PM" else 0)
    return f"{hh:02d}:{mm}"


def lines(name):
    return [l for l in (HERE / name).read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


def bls_monthly():
    out = {}
    for l in lines("bls_monthly_lists.txt"):
        when, time, release = [x.strip() for x in l.split("|")]
        d = datetime.strptime(when.split(", ", 1)[1], "%B %d, %Y").date().isoformat()
        name, ref = release.split(" for ")
        out[(BLS_KIND[name], d)] = (t24(time), f"{MONTHS[ref.split()[0]]} 月")
    return out


def bls_pages():
    out = {}
    for l in lines("bls_release_pages.txt"):
        kind, ref, when, time = [x.strip() for x in l.split("|")]
        d = datetime.strptime(when.replace(".", ""), "%b %d, %Y").date().isoformat()
        out[(kind, d)] = (t24(time), f"{MONTHS[ref.split()[0]]} 月")
    return out


def fed():
    out = {}
    for l in lines("fed_fomc.txt"):
        m = re.match(r"(\d{4}): (.*)", l)
        if not m:
            continue
        year = int(m.group(1))
        for item in m.group(2).split(", "):
            month, days = item.split(" ")
            sep = days.endswith("*")
            last = int(days.rstrip("*").split("-")[1])
            d = date(year, MONTHS[month], last).isoformat()
            flags = {"sep"} if sep else set()
            if year >= 2027:
                flags.add("tentative")  # the page: dates are tentative until confirmed at the preceding meeting
            out[("FOMC", d)] = ("14:00", f"{MONTHS[month]} 月会议", frozenset(flags))
    return out


def bea():
    out, pending = {}, []
    for l in lines("bea_schedule.txt"):
        time, release, when = [x.strip() for x in l.split("|")]
        if when == "To Be Announced":
            pending.append(release)
            continue
        m = re.match(r"GDP \((\w+) Estimate\).*?(\w+) Quarter (\d{4})", release)
        if m:
            kind, period, year = "GDP", f"{QUARTER_CN[m.group(2)]}季度{ESTIMATE_CN[m.group(1)]}", int(m.group(3))
        else:
            ref = re.search(r"Outlays, (\w+) (\d{4})", release)
            kind, period, year = "PCE", f"{MONTHS[ref.group(1)]} 月", int(ref.group(2))
        d = datetime.strptime(f"{when} 2026", "%B %d %Y").date()
        if d.isoformat() < FROM:  # the page lists the coming year's dates without a year
            d = d.replace(year=d.year + 1)
        out[(kind, d.isoformat())] = (t24(time), period)
    return out, pending


def main():
    problems = []
    monthly, pages = bls_monthly(), bls_pages()
    for key in sorted(set(monthly) & set(pages)):
        if monthly[key] != pages[key]:
            problems.append(f"BLS views disagree {key}: list {monthly[key]} vs page {pages[key]}")
    both = len(set(monthly) & set(pages))
    only_pages = sorted(k for k in pages if k not in monthly)
    expected = {}
    for key, (time, period) in {**pages, **monthly}.items():
        if key[1] >= FROM:
            expected[key] = (time, period, frozenset())
    expected.update({k: v for k, v in fed().items() if k[1] >= FROM})
    bea_rows, bea_pending = bea()
    expected.update({k: (t, p, frozenset()) for k, (t, p) in bea_rows.items() if k[1] >= FROM})

    actual = {(k, d): (t, p, frozenset(f)) for k, d, t, p, f in cal.EVENTS if d >= FROM}
    for key in sorted(set(expected) - set(actual)):
        problems.append(f"missing in calendar: {key} {expected[key]}")
    for key in sorted(set(actual) - set(expected)):
        problems.append(f"not in any source: {key} {actual[key]}")
    for key in sorted(set(actual) & set(expected)):
        if actual[key] != expected[key]:
            problems.append(f"differs {key}: calendar {actual[key]} vs source {expected[key]}")
    pending_cal = [f"{k} {p}" for k, p, *_ in cal.PENDING]
    print(f"rows checked: {len(actual)} (calendar from {FROM}); sources: BLS list {len(monthly)}, BLS pages {len(pages)} "
          f"(both views agree on {both}; only on pages: {only_pages}), Fed {len(fed())}, BEA {len(bea_rows)}")
    print(f"BEA undated: {bea_pending} -> calendar PENDING: {pending_cal}")
    print("\n".join(problems) if problems else "no disagreement")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
