import sys, os, json, math, collections, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
OUT = os.path.dirname(os.path.abspath(__file__))
SQ = math.sqrt(252)
V1D = cboe('VIX1D'); VIX = cboe('VIX')
ev = json.load(open(os.path.join(A, 'us_macro_events.json')))
evd = {}
for k in ('FOMC', 'CPI', 'NFP'):
    for d in ev[k]:
        evd.setdefault(d, []).append(k)
dates_daily = sorted(VIX)


def prev_day(d):
    i = dates_daily.index(d)
    return dates_daily[i - 1] if i > 0 else None


def sessions(fn):
    s = collections.OrderedDict()
    for t, o, h, l, c in yahoo(fn):
        d = t.date().isoformat()
        if d >= '2026-10-21': continue
        mins = t.hour * 60 + t.minute - 570  # minutes since 09:30
        if mins < 0 or mins >= 390: continue
        s.setdefault(d, []).append((mins, o, h, l, c))
    return {d: b for d, b in s.items() if b[0][0] == 0 and len(b) >= 5}


def bucket(mins, gran):
    if mins < 60: return 'first_hour'
    if gran == '1h':
        return 'late(14:30-16:00)' if mins >= 300 else 'mid(10:30-14:30)'
    return 'last_2h(14:00-16:00)' if mins >= 270 else 'mid(10:30-14:00)'


def touch_study(fn, gran, sigma_src, a=0.4, fars=(1.0, 1.4)):
    S = sessions(fn)
    agg = collections.defaultdict(lambda: collections.Counter())
    ndays = 0
    for d, bars in S.items():
        sg = sigma_src(d)
        if sg is None: continue
        ndays += 1
        O = bars[0][1]; Cl = bars[-1][4]
        for side in ('put', 'call'):
            K = O * math.exp(-a * sg) if side == 'put' else O * math.exp(a * sg)
            hit = None
            for j, (m, o, h, l, c) in enumerate(bars):
                if (side == 'put' and l <= K) or (side == 'call' and h >= K):
                    hit = j; break
            if hit is None:
                agg[(side, 'no_touch')]['n'] += 1; continue
            b = bucket(bars[hit][0], gran)
            ext = min(x[3] for x in bars[hit:]) if side == 'put' else max(x[2] for x in bars[hit:])
            for key in ((side, b), (side, 'any')):
                agg[key]['n'] += 1
                agg[key]['settle_beyond'] += (Cl <= K) if side == 'put' else (Cl >= K)
                for f in fars:
                    KF = O * math.exp(-f * sg) if side == 'put' else O * math.exp(f * sg)
                    agg[key][f'reach_{f}s'] += (ext <= KF) if side == 'put' else (ext >= KF)
    out = {'days': ndays}
    for (side, b), c in sorted(agg.items()):
        n = c['n']
        out[f'{side}|{b}'] = {'n': n} if b == 'no_touch' else {'n': n, 'settle_beyond': round(c['settle_beyond'] / n, 3), **{k: round(v / n, 3) for k, v in c.items() if k.startswith('reach')}}
    return out, S


def sig_v1d_open(d):
    return V1D[d][0] / 100 / SQ if d in V1D else None


res = {'note': 'entry at 09:30 open; short strikes at open*exp(+-0.4 sigma); sigma = Cboe VIX1D OPEN of the same day /sqrt(252) (known at 09:30). Touch bucket = bar of first touch. reach_Xs = after touch (from the touch bar on) price reached X sigma from open (1.4s = ~1 sigma beyond the short strike). Within-bar ordering unknown: hourly bars can mark a same-bar reach.'}
res['hourly_GSPC'], SH = touch_study('GSPC_1h.json', '1h', sig_v1d_open)
res['5m_GSPC'], S5 = touch_study('GSPC_5m.json', '5m', sig_v1d_open)
res['hourly_GSPC']['range'] = [min(SH), max(SH)]; res['5m_GSPC']['range'] = [min(S5), max(S5)]
# same on hourly but sigma from VIX prior close scaled by in-sample oc share 0.68
res['hourly_GSPC_sigmaVIX'] = touch_study('GSPC_1h.json', '1h', lambda d: VIX[prev_day(d)][1] / 100 / SQ * math.sqrt(0.68) if prev_day(d) else None)[0]

# --- entry timing: realized/implied of (entry -> close) for entry at 09:30, 10:30, 11:30, 12:30, 13:30
V1H = collections.defaultdict(dict)
for t, o, h, l, c in yahoo('VIX1D_1h.json'):
    V1H[t.date().isoformat()][t.hour * 60 + t.minute - 570] = o  # VIX1D at the start of the bar
# in-sample variance clock from hourly SPX bars (share of open-to-close variance remaining after each hour start)
full = [d for d, b in SH.items() if len(b) == 7]
hv = [0.0] * 7
for d in full:
    b = SH[d]
    for j in range(7):
        prevc = b[j - 1][4] if j else b[0][1]
        hv[j] += math.log(b[j][4] / prevc) ** 2
tot = sum(hv); clock = [round(sum(hv[j:]) / tot, 4) for j in range(7)]
res['variance_clock_remaining_share_by_hour_start(in-sample)'] = dict(zip(['09:30', '10:30', '11:30', '12:30', '13:30', '14:30', '15:30'], clock))
timing = {}
for j, lbl in enumerate(['09:30', '10:30', '11:30', '12:30', '13:30', '14:30']):
    za = []; zb = []
    for d in full:
        b = SH[d]; entry = b[j][1]; ret = math.log(b[-1][4] / entry)
        v = V1H.get(d, {}).get(j * 60)
        if v:  # (A) VIX1D at entry, scaled by remaining trading minutes (assumes VIX1D quotes 1 trading day = 390 min)
            za.append(ret / (v / 100 / SQ * math.sqrt((390 - 60 * j) / 390)))
        pd = prev_day(d)
        if pd:  # (B) VIX prior close * sqrt(oc share 0.68 * remaining clock share)
            zb.append(ret / (VIX[pd][1] / 100 / SQ * math.sqrt(0.68 * clock[j])))
    timing[lbl] = {'A_vix1d_at_entry_linear_time': stats(za), 'B_vix_prevclose_variance_clock': stats(zb)}
res['entry_timing'] = timing
# VIX1D intraday level by hour (shows how the index itself behaves through the session)
lv = collections.defaultdict(list)
for d, m in V1H.items():
    if 0 in m:
        for k, v in m.items():
            lv[k].append(v / m[0])
res['vix1d_level_rel_to_0930_by_bar'] = {f'{9 + (570 + k) // 60 - 9}:{(570 + k) % 60:02d}': round(sum(v) / len(v), 3) for k, v in sorted(lv.items()) if k < 390}
json.dump(res, open(os.path.join(OUT, 'intraday_results.json'), 'w'), indent=1)
print(json.dumps(res, indent=1))
