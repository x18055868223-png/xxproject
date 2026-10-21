import sys, os, json, math, collections, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
OUT = os.path.dirname(os.path.abspath(__file__))
spx = [b for b in yahoo('GSPC_max_1d.json') if b[0].date().isoformat() < '2026-10-21']
VIX = cboe('VIX'); V1D = cboe('VIX1D'); V9D = cboe('VIX9D')
ev = json.load(open(os.path.join(A, 'us_macro_events.json')))
evd = {}
for k in ('FOMC', 'CPI', 'NFP'):
    for d in ev[k]:
        evd.setdefault(d, []).append(k)
D = [b[0].date().isoformat() for b in spx]; O = [b[1] for b in spx]; H = [b[2] for b in spx]; L = [b[3] for b in spx]; C = [b[4] for b in spx]
n = len(D); r = [None] + [math.log(C[i] / C[i - 1]) for i in range(1, n)]
oc_ok_year = {}
for y in range(1990, 2027):
    idx = [i for i in range(1, n) if D[i][:4] == str(y)]
    if idx:
        oc_ok_year[y] = sum(1 for i in idx if O[i] == C[i - 1]) / len(idx)


def rv(i, w):  # annualized % realized vol from closes up to and including day i (known at close i)
    if i < w:
        return None
    return 100 * math.sqrt(252 * sum(r[j] ** 2 for j in range(i - w + 1, i + 1)) / w)


rows = []
for i in range(1, n - 1):  # entry at close of day i, outcome day i+1
    d = D[i]; dn = D[i + 1]
    row = {'d': d, 'dn': dn, 'dow': datetime.date.fromisoformat(dn).weekday(), 'r_next': r[i + 1], 'r_prev': r[i],
           'oc_next': math.log(C[i + 1] / O[i + 1]) if oc_ok_year.get(int(dn[:4]), 1) < 0.02 and O[i + 1] != C[i] else None,
           'hi_next': math.log(H[i + 1] / C[i]), 'lo_next': math.log(L[i + 1] / C[i]), 'ev_next': evd.get(dn, []),
           'rv5': rv(i, 5), 'rv20': rv(i, 20)}
    for nm, src in (('vix', VIX), ('v1d', V1D), ('v9d', V9D)):
        row[nm] = src[d][1] if d in src else None
        row[nm + '_open_next'] = src[dn][0] if dn in src else None
    rows.append(row)
for nm in ('vix', 'v1d'):
    hist = []
    for row in rows:
        x = row[nm]
        for k in ('_p5', '_p10', '_d1', '_rng5', '_gap5', '_gap20'):
            row[nm + k] = None
        if x is None:
            continue
        for w in (5, 10):
            prev = hist[-w:]
            if len(prev) == w:
                row[f'{nm}_p{w}'] = (sum(1 for v in prev if v < x) + 0.5 * sum(1 for v in prev if v == x)) / w
        prev = hist[-5:]
        if len(prev) == 5 and max(prev + [x]) > min(prev + [x]):
            row[nm + '_rng5'] = (x - min(prev + [x])) / (max(prev + [x]) - min(prev + [x]))
        row[nm + '_d1'] = x - hist[-1] if hist else None
        row[nm + '_gap5'] = x - row['rv5'] if row['rv5'] else None
        row[nm + '_gap20'] = x - row['rv20'] if row['rv20'] else None
        hist.append(x)
SQ = math.sqrt(252)


def z(row, nm, ret='r_next', scale=1.0):
    if row[nm] is None or row[ret] is None:
        return None
    return row[ret] / (row[nm] / 100 / SQ * scale)


def period(rs, a, b):
    return [x for x in rs if a <= x['dn'] <= b]


res = {'data': {'spx_daily': [D[0], D[-1]], 'vix': [min(VIX), max(VIX)], 'vix1d': [min(V1D), max(V1D)],
                'open_eq_prevclose_share_by_year': {k: round(v, 3) for k, v in oc_ok_year.items()}}}


def overall(rs, nm):
    out = {'cc': stats([z(x, nm) for x in rs])}
    oc = [x for x in rs if x['oc_next'] is not None and x[nm] is not None]
    if oc:
        out['oc_share_of_cc_variance_insample'] = round(sum(x['oc_next'] ** 2 for x in oc) / sum(x['r_next'] ** 2 for x in oc), 3)
        out['oc_unscaled'] = stats([z(x, nm, 'oc_next') for x in oc])
    return out


P = {'vix_all': ('vix', '1990-01-01', '2026-12-31'), 'vix_1990_2007': ('vix', '1990-01-01', '2007-12-31'), 'vix_2008_2026': ('vix', '2008-01-01', '2026-12-31'),
     'vix_2022_05_on': ('vix', '2022-05-16', '2026-12-31'), 'v1d_all': ('v1d', '2022-05-16', '2026-12-31'),
     'v1d_2022H2_2024H1': ('v1d', '2022-05-16', '2024-07-31'), 'v1d_2024H2_2026': ('v1d', '2024-08-01', '2026-12-31'),
     'v1d_2023_2024': ('v1d', '2023-01-01', '2024-12-31'), 'v1d_2025_2026': ('v1d', '2025-01-01', '2026-12-31'),
     'vix_2023_2024': ('vix', '2023-01-01', '2024-12-31'), 'vix_2025_2026': ('vix', '2025-01-01', '2026-12-31')}
res['a_overall'] = {k: overall(period(rows, a, b), nm) for k, (nm, a, b) in P.items()}
rs = [x for x in rows if x['v1d_open_next'] and x['oc_next'] is not None]
res['a_overall']['v1d_open_next_vs_oc'] = stats([x['oc_next'] / (x['v1d_open_next'] / 100 / SQ) for x in rs])


def thresholds(rs, nm):
    t = {}
    for k in ('d1', 'gap5', 'gap20'):
        v = [x[f'{nm}_{k}'] for x in rs if x[f'{nm}_{k}'] is not None]
        t[k + '_q20'] = round(quantile(v, 0.2), 3); t[k + '_q80'] = round(quantile(v, 0.8), 3)
    return t


def signals(rs, nm, thr):
    out = {}

    def grp(lbl, cond):
        out[lbl] = stats([z(x, nm) for x in rs if cond(x)])
    for w in (5, 10):
        k = f'{nm}_p{w}'
        grp(f'p{w}<0.2', lambda x, k=k: x[k] is not None and x[k] < 0.2)
        grp(f'p{w} 0.2-0.8', lambda x, k=k: x[k] is not None and 0.2 <= x[k] < 0.8)
        grp(f'p{w}>=0.8', lambda x, k=k: x[k] is not None and x[k] >= 0.8)
    k = nm + '_rng5'
    grp('range5>=0.8', lambda x: x[k] is not None and x[k] >= 0.8)
    grp('range5<0.8', lambda x: x[k] is not None and x[k] < 0.8)
    kd = nm + '_d1'
    grp('d1 bottom20', lambda x: x[kd] is not None and x[kd] <= thr['d1_q20'])
    grp('d1 mid', lambda x: x[kd] is not None and thr['d1_q20'] < x[kd] < thr['d1_q80'])
    grp('d1 top20', lambda x: x[kd] is not None and x[kd] >= thr['d1_q80'])
    for g in ('gap5', 'gap20'):
        kg = f'{nm}_{g}'
        grp(f'{g} bottom20', lambda x, kg=kg, g=g: x[kg] is not None and x[kg] <= thr[g + '_q20'])
        grp(f'{g} mid', lambda x, kg=kg, g=g: x[kg] is not None and thr[g + '_q20'] < x[kg] < thr[g + '_q80'])
        grp(f'{g} top20', lambda x, kg=kg, g=g: x[kg] is not None and x[kg] >= thr[g + '_q80'])

    def nflags(x):
        a = [x[nm + '_p5'], x[nm + '_d1'], x[nm + '_gap5']]
        if None in a:
            return None
        return int(a[0] >= 0.8) + int(a[1] >= thr['d1_q80']) + int(a[2] <= thr['gap5_q20'])
    for c in range(4):
        grp(f'flags={c}', lambda x, c=c: nflags(x) == c)
    grp('flags>=1', lambda x: (nflags(x) or 0) >= 1)
    grp('all', lambda x: x[nm] is not None)
    # overlap of p5>=0.8 & d1 top20
    out['overlap_p5_and_d1'] = sum(1 for x in rs if x[nm + '_p5'] is not None and x[nm + '_p5'] >= 0.8 and x[kd] is not None and x[kd] >= thr['d1_q80'])
    return out


res['b_signals'] = {}
for k, (nm, a, b) in P.items():
    rs = period(rows, a, b); th = thresholds(rs, nm)  # in-sample quintile cuts (flagged; see expanding version)
    res['b_signals'][k] = {'thresholds': th, 'groups': signals(rs, nm, th)}


def expanding_flag_stats(nm, start):
    rs = [x for x in rows if x[nm] is not None]
    out = collections.defaultdict(list); hd = []; hg = []
    for x in rs:
        if x['dn'] >= start and len(hd) > 120:
            d1 = x[nm + '_d1']; g = x[nm + '_gap5']
            q80 = quantile(hd, 0.8); q20 = quantile(hg, 0.2)
            fl = [x[nm + '_p5'] is not None and x[nm + '_p5'] >= 0.8, d1 is not None and d1 >= q80, g is not None and g <= q20]
            zz = z(x, nm); out['flags=%d' % sum(fl)].append(zz)
            if fl[1]: out['d1 top20 (expanding)'].append(zz)
            if fl[2]: out['gap5 bottom20 (expanding)'].append(zz)
            if not fl[1] and not fl[2] and not fl[0]: out['none'].append(zz)
        if x[nm + '_d1'] is not None: hd.append(x[nm + '_d1'])
        if x[nm + '_gap5'] is not None: hg.append(x[nm + '_gap5'])
    return {k: stats(v) for k, v in sorted(out.items())}


res['b_expanding_thresholds'] = {'vix_1991_on': expanding_flag_stats('vix', '1991-01-01'), 'v1d_2023_on': expanding_flag_stats('v1d', '2023-01-01')}


def persist(rs):
    out = {}
    for lbl, cond in (('|prev|>1%', lambda x: abs(x['r_prev']) > 0.01), ('|prev|>2%', lambda x: abs(x['r_prev']) > 0.02), ('all nonzero', lambda x: True)):
        g = [x for x in rs if x['r_prev'] and x['r_next'] and cond(x)]
        up = [x for x in g if x['r_prev'] > 0]; dn = [x for x in g if x['r_prev'] < 0]
        out[lbl] = {'n': len(g), 'same_sign': round(sum(1 for x in g if x['r_prev'] * x['r_next'] > 0) / len(g), 3),
                    'n_up': len(up), 'up_then_up': round(sum(1 for x in up if x['r_next'] > 0) / max(1, len(up)), 3),
                    'n_down': len(dn), 'down_then_down': round(sum(1 for x in dn if x['r_next'] < 0) / max(1, len(dn)), 3)}
    return out


res['c_persistence'] = {k: persist(period(rows, a, b)) for k, (a, b) in {'1990_2026': ('1990', '2027'), '1990_2007': ('1990', '2007-12-31'), '2008_2026': ('2008', '2027'), '2022_05_on': ('2022-05-16', '2027')}.items()}
prevvix = {rows[j]['d']: rows[j - 1]['vix'] for j in range(1, len(rows))}
prev1d = {rows[j]['d']: rows[j - 1]['v1d'] for j in range(1, len(rows))}
for lbl, src, a in (('beyond_1_implied_sigma_VIX_1990on', prevvix, '1990'), ('beyond_1_implied_sigma_VIX1D_2022on', prev1d, '2022-05-17')):
    g = [x for x in rows if x['dn'] >= a and src.get(x['d']) and x['r_next'] and abs(x['r_prev']) > src[x['d']] / 100 / SQ]
    res['c_persistence'][lbl] = {'n': len(g), 'same_sign': round(sum(1 for x in g if x['r_prev'] * x['r_next'] > 0) / len(g), 3)}

EVC = (('FOMC', lambda x: 'FOMC' in x['ev_next']), ('CPI', lambda x: 'CPI' in x['ev_next']), ('NFP', lambda x: 'NFP' in x['ev_next']),
       ('any event', lambda x: bool(x['ev_next'])), ('no event', lambda x: not x['ev_next']))


def evstats(rs, nm):
    out = {}
    for lbl, cond in EVC:
        g = [x for x in rs if cond(x) and x[nm] is not None]
        out[lbl] = stats([z(x, nm) for x in g]); out[lbl]['mean_implied'] = round(sum(x[nm] for x in g) / len(g), 2) if g else None
    return out


er = period(rows, '2023-01-03', '2026-10-20')
res['e_events_2023_2026'] = {'vix1d_cc': evstats(er, 'v1d'), 'vix_cc': evstats(er, 'vix'), 'vix9d_cc': evstats(er, 'v9d'),
    'vix1d_open_vs_oc': {lbl: stats([x['oc_next'] / (x['v1d_open_next'] / 100 / SQ) for x in er if cond(x) and x['v1d_open_next'] and x['oc_next'] is not None]) for lbl, cond in EVC},
    'vix1d_over_vix_entering': {lbl: round(sum(x['v1d'] / x['vix'] for x in er if cond(x) and x['v1d'] and x['vix']) / max(1, sum(1 for x in er if cond(x) and x['v1d'] and x['vix'])), 3) for lbl, cond in EVC},
    'note': 'event = outcome day (dn) is a release/statement day; entry at prior close. vix1d_over_vix_entering = mean VIX1D/VIX at entry close.'}

names = 'Mon Tue Wed Thu Fri'.split()
res['f_dow'] = {}
for nm, a in (('vix', '1990'), ('vix', '2022-05-16'), ('v1d', '2022-05-16')):
    res['f_dow'][f'{nm}_{a}'] = {names[w]: stats([z(x, nm) for x in rows if x['dn'] >= a and x['dow'] == w]) for w in range(5)}


def hl(rs, nm, a=0.4, b=1.4):
    out = {}
    for side in ('put', 'call'):
        t = s = f = 0; m = 0
        for x in rs:
            if x[nm] is None: continue
            m += 1; sg = x[nm] / 100 / SQ
            if side == 'put':
                if x['lo_next'] <= -a * sg: t += 1; s += x['r_next'] <= -a * sg; f += x['lo_next'] <= -b * sg
            else:
                if x['hi_next'] >= a * sg: t += 1; s += x['r_next'] >= a * sg; f += x['hi_next'] >= b * sg
        out[side] = {'days': m, 'touched': t, 'share_days_touched': round(t / m, 3), 'settle_beyond_given_touch': round(s / t, 3), 'reach_far_given_touch': round(f / t, 3)}
    return out


res['d_daily_hilo_close_entry'] = {'note': 'entry at prior close, strikes +-0.4 sigma and +-1.4 sigma from prior close; next-day high/low (includes overnight gap)',
    'v1d_2022_05_on': hl(period(rows, '2022-05-16', '2027'), 'v1d'), 'vix_2008_on': hl(period(rows, '2008', '2027'), 'vix'), 'vix_2022_05_on': hl(period(rows, '2022-05-16', '2027'), 'vix')}
json.dump(res, open(os.path.join(OUT, 'daily_results.json'), 'w'), indent=1)
json.dump(rows, open(os.path.join(OUT, 'daily_rows.json'), 'w'))
print('ok', len(rows))
