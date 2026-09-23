"""Compare sealed prior forecasts and make a compact reusable research evidence digest."""
import argparse,json
from pathlib import Path
from collections import Counter
import astra_control_study as c
from astra_second_report import read_csv,write_csv
import astra_edge_analysis as a

def run(root,out):
    report=c.load(out/'analysis/report.json')
    rows=read_csv(out/'analysis/main_ledger.csv');by=a.group_pairs(rows)
    choices=read_csv(out/'prior/prior_side_choices.csv')
    prior=[]
    for key in ('probability_rule_side','margin_return_rule_side'):
        chosen=[(x['card_id'],x[key]) for x in choices if x[key] in c.SIDES]
        d={'rule':key,'selected_n':len(chosen),'selected':a.extended([(by[i][s],1) for i,s in chosen]),'baselines':{}}
        for base in (*c.SIDES,'random_side_expectation'):
            pairs=[(by[i][s],[(by[i][t],.5) for t in c.SIDES] if base=='random_side_expectation' else [(by[i][base],1)]) for i,s in chosen]
            d['baselines'][base]={**c.pair_metrics(pairs),'week_interval':c.bootstrap(pairs,2000,20260914,True)}
        prior.append(d)
    factor_detail=[]
    for x in report['factor_groups']:
        if x['factor'] not in ('conflict_binary','wall_position','anchor_band_short','grade_B_vs_DC'):continue
        key='factor_'+x['factor']
        chosen=[r for r in rows if r['side']==x['side'] and (r.get(key)==x['label'] if key in r else (r.get('offline_grade')=='B' if x['label']=='B' else r.get('offline_grade') in ('D','C') if x['label']=='D_C' else r.get('offline_grade') is None))]
        factor_detail.append({k:x[k] for k in ('factor','side','label','coverage')}|{'n':len(chosen),'delivery_dates':len({r['delivery_date'] for r in chosen}),
         'entry_month_counts':dict(Counter(r['entry_date'][:7] for r in chosen)),
         'mean_short_distance_pct':sum(r['short_distance_pct'] for r in chosen)/len(chosen) if chosen else None,
         'mean_dte_hours':sum(r['dte_hours'] for r in chosen)/len(chosen) if chosen else None})
    signal={s:a.extended([(r,1) for r in rows if r['side']==s]) for s in c.SIDES}
    clocks=read_csv(root/'.artifacts/astra-control-study-20260913/results/control_main.csv')
    clock={s:a.extended([(r,1) for r in clocks if r['side']==s and r['grid_offset_minutes']==0]) for s in c.SIDES}
    digest={'prior':prior,'signal_fixed_sides':signal,'clock_fixed_sides':clock,'factor_confound_diagnostics':factor_detail}
    c.save(out/'analysis/evidence_digest.json',digest)
    write_csv(out/'analysis/prior_comparisons.csv',[{'rule':x['rule'],'baseline':k,**{n:v for n,v in d.items() if not isinstance(v,(dict,list))},**{'selected_'+n:v for n,v in x['selected'].items()}} for x in prior for k,d in x['baselines'].items()])
    print(json.dumps(digest,ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--workspace',type=Path,required=True);p.add_argument('--output',type=Path,required=True);v=p.parse_args();run(v.workspace,v.output)
