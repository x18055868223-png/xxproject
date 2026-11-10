"""Tables for the doc 30 case book, from hands_raw.json (see hands_extract.py). Run: python hands_tables.py
Writes hands_summary.txt. Facts only: where the key strikes were, what the index did there before and after."""
import json, os, sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
hands = json.load(open(os.path.join(HERE, "hands_raw.json"), encoding="utf-8"))["hands"]
out = []
P = out.append


def hm(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%d %H:%M") if ms else "—"


def top_roles(roles):
    return [r for r in roles if r in ("pin", "gamma_call#1", "gamma_put#1", "oi_call#1", "oi_put#1")]


ok = [h for h in hands if h["status"] == "ok" and h["row"] and h["index_at_read"]]
P(f"hands: {len(hands)}  usable: {len(ok)}  missing: {[(h['expiry_day'], h['side']) for h in hands if h not in ok]}")
P("")

# ---- per-expiry facts (structure is shared by both sides; take the put hand, else the call hand)
P("== A. 每个到期日：读牌时刻的本期结构与之后的路径（价格 USD；move = 本期预期波动）")
P("day   read(UTC)  h_left  spot    move  pin     pin-spot/move  gC1     gP1     3rd-party      range_after(lo..hi)   settle  |settle-spot|/move  |settle-pin|")
days = {}
for h in ok:
    days.setdefault(h["expiry_day"], h)
rows_a = []
for d, h in sorted(days.items()):
    s, mv, w = h["index_at_read"], h["expected_move_usd"], h["walls"]
    st = h["index_at_expiry"]
    pin = w.get("pin")
    ar = h.get("after_range") or {}
    rows_a.append({"day": d, "spot": s, "move": mv, "pin": pin, "settle": st, "state": h["third_party"]["market_state"],
                   "lo": ar.get("min_usd"), "hi": ar.get("max_usd"), "hleft": h["hours_to_expiry"]})
    P(f"{d[5:]} {hm(h['read_at_ms'])}  {h['hours_to_expiry']:5.1f}  {s:7.0f} {mv:5.0f}  {pin:7.0f} {(pin - s) / mv:+6.2f}        "
      f"{w.get('gamma_call'):7.0f} {w.get('gamma_put'):7.0f} {str(h['third_party']['market_state']):14s} "
      f"{ar.get('min_usd'):7.0f}..{ar.get('max_usd'):7.0f}   {st:7.0f}  {abs(st - s) / mv:5.2f}             {abs(st - pin):5.0f}")
P("")

# ---- pin: did settlement end nearer the pin than the read-time spot was?
n = len(rows_a)
closer = sum(1 for r in rows_a if abs(r["settle"] - r["pin"]) < abs(r["spot"] - r["pin"]))
within250 = sum(1 for r in rows_a if abs(r["settle"] - r["pin"]) <= 250)
start250 = sum(1 for r in rows_a if abs(r["spot"] - r["pin"]) <= 250)
P(f"== B. Pin（读牌时刻的本期 Pin，之后不更新）: n={n}")
P(f"交割价比读牌时现价更靠近 Pin: {closer}/{n}；交割价在 Pin ±250 内: {within250}/{n}（读牌时现价已在 ±250 内: {start250}/{n}）")
for lab in ("negative_gamma", "positive_gamma"):
    sub = [r for r in rows_a if r["state"] == lab]
    if sub:
        rng = [(r["hi"] - r["lo"]) / r["move"] for r in sub]
        end = [abs(r["settle"] - r["spot"]) / r["move"] for r in sub]
        P(f"第三方口径 {lab}: n={len(sub)}  之后高低幅/move 均值 {sum(rng) / len(rng):.2f}  |交割-现价|/move 均值 {sum(end) / len(end):.2f}")
P("")

# ---- key strikes other than the hand's own legs, between spot and short strike
P("== C. 每一局：现价与短腿之间的本期关键位，以及它们之前/之后的行为")
P("（关键位 = 本期 Gamma 前三、持仓前三、Pin；之前 = 读牌前 24h；之后 = 读牌到到期；x=穿越次数）")
tally = {"gap": [], "barrier": []}
detail = []
for h in sorted(ok, key=lambda x: (x["expiry_day"], x["side"])):
    s, mv, r = h["index_at_read"], h["expected_move_usd"], h["row"]
    ks, kl = r["short_strike"], r["long_strike"]
    sign = 1 if h["side"] == "call" else -1
    short_key = next(k for k in h["keys"] if k["strike"] == ks)
    touched = bool(short_key["after"] and short_key["after"]["crossings"]) or sign * (s - ks) >= 0
    itm_at_read = sign * (s - ks) >= 0
    between = [k for k in h["keys"] if 0 < sign * (k["strike"] - s) < sign * (ks - s)
               and any(not x.startswith(("short", "long")) for x in k["roles"])]
    short_is_key = [x for x in short_key["roles"] if x not in ("short", "long")]
    payout = (h["outcome"] or {}).get("payout_btc")
    line = (f"{h['expiry_day'][5:]} {h['side']:4s} spot {s:7.0f} short {ks:7.0f} ({sign * (ks - s) / mv:+.2f} move) "
            f"{'[读牌时已越过短腿]' if itm_at_read else ''} 短腿{'被触及' if touched else '未触及'} "
            f"赔付 {payout if payout is not None else '—'}  短腿自身是关键位: {short_is_key or '否'}")
    detail.append(line)
    if itm_at_read:
        continue
    (tally["barrier"] if between else tally["gap"]).append((h, touched, payout))
    for k in between:
        b, a = k["before_24h"] or {}, k["after"] or {}
        detail.append(f"      位 {k['strike']:7.0f} {','.join(k['roles'])}: 之前 x{b.get('crossings')} 最近 {b.get('closest_usd')} "
                      f"| 之后 x{a.get('crossings')} 首穿 {hm(a.get('first_cross_ms'))} "
                      f"穿后60分最远 {a.get('beyond_60m_usd')} 60分内折回 {a.get('back_within_60m')} 到期时在外 {a.get('end_beyond')}")
out.extend(detail)
P("")
P("== D. 现价与短腿之间有没有本期关键位（读牌时已越过短腿的局不计）")
for name, lab in (("gap", "空档（之间没有关键位）"), ("barrier", "之间有关键位")):
    t = tally[name]
    P(f"{lab}: n={len(t)}  短腿被触及 {sum(1 for _, x, _ in t if x)}  有赔付 {sum(1 for _, _, p in t if p)}")
# for hands with a key strike in between: was the nearest one crossed, and did the short get touched after it?
t = tally["barrier"]
first_crossed = held = 0
for h, touched, _ in t:
    s = h["index_at_read"]
    sign = 1 if h["side"] == "call" else -1
    ks = h["row"]["short_strike"]
    between = sorted([k for k in h["keys"] if 0 < sign * (k["strike"] - s) < sign * (ks - s)
                      and any(not x.startswith(("short", "long")) for x in k["roles"])], key=lambda k: abs(k["strike"] - s))
    a = between[0]["after"] or {}
    if a.get("crossings"):
        first_crossed += 1
    else:
        held += 1
P(f"之间有关键位的局里，最近的关键位：被穿过 {first_crossed}，未被穿过 {held}")
P("")

# ---- behaviour at top key strikes when crossed after the read (all hands' expiries, each strike once per day)
P("== E. 关键位被穿过之后的 60 分钟（每个到期日每个位只计一次；只计读牌之后的首次穿越）")
seen, stats = set(), {}
for h in ok:
    for k in h["keys"]:
        key = (h["expiry_day"], k["strike"])
        roles = [x for x in k["roles"] if not x.startswith(("short", "long"))]
        a = k["after"] or {}
        if key in seen or not roles or not a.get("first_cross_ms"):
            continue
        seen.add(key)
        cls = "头号位(Pin/各类第一)" if top_roles(roles) else "其余前三"
        st = stats.setdefault(cls, {"n": 0, "back": 0, "end_beyond": 0, "beyond": []})
        st["n"] += 1
        st["back"] += bool(a.get("back_within_60m"))
        st["end_beyond"] += bool(a.get("end_beyond"))
        st["beyond"].append(a.get("beyond_60m_usd") / h["expected_move_usd"])
for cls, st in stats.items():
    b = sorted(st["beyond"])
    P(f"{cls}: 被穿过 {st['n']} 次；60 分钟内折回 {st['back']}；到期时仍在穿越方向一侧 {st['end_beyond']}；"
      f"穿后 60 分最远/move 中位 {b[len(b) // 2]:.2f}")

open(os.path.join(HERE, "hands_summary.txt"), "w", encoding="utf-8").write("\n".join(out) + "\n")
sys.stdout.reconfigure(encoding="utf-8")
print("\n".join(out))
