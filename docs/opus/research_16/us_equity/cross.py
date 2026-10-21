# Cross-check: (1) VIX1D-flag groups excluding event-day outcomes; (2) VIX-based flags but outcome scaled by VIX1D (does the 1-day implied already absorb them?)
import sys, os, json, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import stats, quantile
OUT = os.path.dirname(os.path.abspath(__file__))
rows = json.load(open(os.path.join(OUT, 'daily_rows.json')))
SQ = math.sqrt(252)
rs = [x for x in rows if x['dn'] >= '2022-05-16' and x['v1d'] and x['vix']]


def zz(x, nm): return x['r_next'] / (x[nm] / 100 / SQ)


def thr(nm, k, p, pool): return quantile([x[f'{nm}_{k}'] for x in pool if x[f'{nm}_{k}'] is not None], p)


res = {}
for scope, pool in (('all', rs), ('non_event_outcome', [x for x in rs if not x['ev_next']])):
    for sig in ('vix', 'v1d'):
        d80 = thr(sig, 'd1', 0.8, rs); g20 = thr(sig, 'gap5', 0.2, rs)
        def fl(x):
            return [x[sig + '_p5'] is not None and x[sig + '_p5'] >= 0.8, x[sig + '_d1'] is not None and x[sig + '_d1'] >= d80, x[sig + '_gap5'] is not None and x[sig + '_gap5'] <= g20]
        for scale in ('vix', 'v1d'):
            key = f'{scope}|flags_from_{sig}|z_scaled_by_{scale}'
            out = {}
            for lbl, i in (('p5>=0.8', 0), ('d1 top20', 1), ('gap5 bottom20', 2)):
                out[lbl] = stats([zz(x, scale) for x in pool if fl(x)[i]])
                out['not ' + lbl] = stats([zz(x, scale) for x in pool if not fl(x)[i]])
            for c in range(4):
                out[f'flags={c}'] = stats([zz(x, scale) for x in pool if sum(fl(x)) == c])
            res[key] = out
json.dump(res, open(os.path.join(OUT, 'cross_results.json'), 'w'), indent=1)
for k, v in res.items():
    print('==', k)
    for g, s in v.items():
        print('   %-20s n=%4d rv/iv=%.3f >1=%.3f med=%.3f' % (g, s['n'], s.get('rv_iv', 0), s.get('gt1', 0), s.get('med_abs_z', 0)))
