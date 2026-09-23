"""Chronological daily admission diagnostic, no trading/LLM/API calls."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import traceback

for _var in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_MAX_THREADS'):
    os.environ[_var] = '6'
import numpy as np
import pandas as pd
from astra_entry_quality import block_bootstrap_stat_vs_geometry
from astra_entry_structure_analysis import es95

METHODS=('geometry','statistical','joint')


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''): h.update(b)
    return h.hexdigest()


def save(path,obj):
    Path(path).write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def prepare_daily(rows):
    required=['row_id','observation_id','side','delivery_date','actual_loss_normalized']+[m+'_score' for m in METHODS]
    if any(c not in rows for c in required): raise ValueError('Missing saved-prediction columns')
    f=rows.copy()
    if f.row_id.duplicated().any(): raise ValueError('Duplicate source row identity')
    if not f.observation_id.str.match(r'^natural-clock-30m:[0-9]+$').all(): raise ValueError('Invalid observation identity')
    f['as_of_ms']=f.observation_id.str.rsplit(':',n=1).str[-1].astype('int64')
    t=pd.to_datetime(f.as_of_ms,unit='ms',utc=True)
    f['entry_day']=t.dt.strftime('%Y-%m-%d')
    f['entry_year']=t.dt.year
    f=f.loc[(t.dt.hour==8)&(t.dt.minute==0)&(t.dt.second==0)&(t.dt.microsecond==0)].copy()
    if f.duplicated(['side','entry_day']).any(): raise ValueError('More than one exact08UTC candidate for a side/day')
    numbers=f[['actual_loss_normalized']+[m+'_score' for m in METHODS]].to_numpy(float)
    if not np.isfinite(numbers).all() or (f.actual_loss_normalized<0).any(): raise ValueError('Invalid saved score/uncapped target')
    return f.sort_values(['side','as_of_ms']).reset_index(drop=True)


def admission_memberships(daily, evaluation_years, minimum_prior_days=100):
    out=daily[daily.entry_year.isin(evaluation_years)].copy()
    cuts=[]
    for method in METHODS:
        out[method+'_admit']=np.nan
        out[method+'_cut']=np.nan
        for side in sorted(daily.side.unique()):
            for year in evaluation_years:
                prior=daily[(daily.side==side)&(daily.entry_year==year-1)]
                current=(out.side==side)&(out.entry_year==year)
                n=int(prior.entry_day.nunique())
                cut=float(prior[method+'_score'].median()) if n>=minimum_prior_days else None
                if cut is not None:
                    out.loc[current,method+'_cut']=cut
                    out.loc[current,method+'_admit']=(out.loc[current,method+'_score']<=cut).astype(float)
                cuts.append({'method':method,'side':side,'evaluation_year':year,'cut_source_year':year-1,
                    'prior_days':n,'cut':cut,'qualified':cut is not None,'evaluation_rows':int(current.sum()),
                    'current_score_median':float(out.loc[current,method+'_score'].median()) if current.any() else None})
    return out,cuts


def metrics(part, member):
    if part.empty: return {'available':False,'reason':'no_rows'}
    a=np.asarray(member,float)
    if not np.isfinite(a).all(): return {'available':False,'reason':'missing_prior_cut','rows':len(part)}
    y=part.actual_loss_normalized.to_numpy(float)
    w=1/part.groupby('delivery_date').row_id.transform('count').to_numpy(float)
    w=w/w.sum()
    admitted=w*a
    skipped=w*(1-a)
    mass=float(admitted.sum())
    deferred_mass=float(skipped.sum())
    return {'available':True,'rows':len(part),'days':int(part.delivery_date.nunique()),'coverage':mass,
        'admitted_dates':int(part.loc[a>0,'delivery_date'].nunique()),
        'conditional_admitted_loss':float(np.dot(admitted,y)/mass) if mass>0 else None,
        'conditional_skipped_loss':float(np.dot(skipped,y)/deferred_mass) if deferred_mass>0 else None,
        'admitted_loss_per_original_opportunity':float(np.dot(admitted,y)),
        'skipped_loss_per_original_opportunity':float(np.dot(skipped,y)),
        'all_opportunity_mean_loss':float(np.dot(w,y)),
        'admitted_es95_uncapped':es95(y,admitted) if mass>0 else None,
        'admitted_zero_payout_fraction':float(np.dot(admitted,y==0)/mass) if mass>0 else None,
        'skipped_zero_payout_fraction_of_original':float(np.dot(skipped,y==0)),
        'skipped_zero_payout_fraction_of_skipped':float(np.dot(skipped,y==0)/deferred_mass) if deferred_mass>0 else None,
        'uncapped_over_one_rows':int((y>1).sum()),
        'loss_conservation_residual':float(np.dot(admitted,y)+np.dot(skipped,y)-np.dot(w,y)),
        'net_EV':None,'true_win_rate':None}


def calibration_bins(part,method):
    # Reuse v1.3's already frozen display bins; these never set admission.
    edges=[0.,.05,.1,.2,.4,1.]
    score=part[method+'_score'].to_numpy(float)
    labels=np.searchsorted(edges,score,side='right')
    bins=[]
    for label in sorted(set(labels)):
        g=part.loc[labels==label]
        bins.append({'bin':int(label),'days':int(g.delivery_date.nunique()),
            'mean_score':float(g[method+'_score'].mean()),'mean_actual':float(g.actual_loss_normalized.mean())})
    return {'edges_from_v13_risk_display':edges,'bins':bins,'use':'description only, not admission cut'}


def analyze(out,cuts,protocol):
    summary={'scope':'already-studied chronological date admission; actual achieved coverage, not matched coverage',
        'initialization_year':2022,'evaluation_years':list(protocol['opportunity']['evaluation_years']),
        'cutoffs':cuts,'rows':len(out),'sides':{},'production_qualified':False,'natural_nr_effect':False,
        'actual_credit':None,'economic_effect':None,'future_dates_used_in_cut':False,
        'limitations':['yearly model versions can change score scale','lower per-original payout may simply reflect less admission',
                      'uniform same-coverage comparison is an expectation benchmark, not executed random orders']}
    daily_records=[]
    for side,part in out.groupby('side'):
        side_info={'methods':{},'annual':{},'all':metrics(part,np.ones(len(part)))}
        for method in METHODS:
            side_info['methods'][method]=metrics(part,part[method+'_admit'])
        for year,g in part.groupby('entry_year'):
            result={m:metrics(g,g[m+'_admit']) for m in METHODS}
            for method in METHODS:
                if result[method]['available']:
                    coverage=result[method]['coverage']
                    result[method]['uniform_same_coverage']=metrics(g,np.full(len(g),coverage))
                result[method]['fixed_bin_calibration']=calibration_bins(g,method)
            side_info['annual'][str(year)]=result
        for method in METHODS:
            for row in part.itertuples(index=False):
                a=getattr(row,method+'_admit')
                if not np.isfinite(a): continue
                daily_records.append({'method':method,'coverage':.5,'side':side,'delivery_date':row.delivery_date,
                    'selected_weight':float(a),'selected_loss_sum':float(a*row.actual_loss_normalized),
                    'original_weight':1.,'deferred_weight':float(1-a),'deferred_loss_sum':float((1-a)*row.actual_loss_normalized)})
        stats=side_info['methods']['statistical']; geom=side_info['methods']['geometry']
        complete=stats.get('available',False) and geom.get('available',False)
        if complete:
            primary=block_bootstrap_stat_vs_geometry(daily_records,side=side,coverage=.5,
                repetitions=protocol['uncertainty']['replicates'],seed=protocol['uncertainty']['seed'],block_days=7)
            primary.pop('coverage',None)
            primary['coverage_identity']='two prior-year threshold policies at their own achieved coverage; .5 was an internal diagnostic grouping key only'
            annual={}
            for year,entry in side_info['annual'].items():
                a=entry['statistical'].get('conditional_admitted_loss'); b=entry['geometry'].get('conditional_admitted_loss')
                annual[year]=a-b if a is not None and b is not None else None
            primary['annual_difference_by_entry_year']=annual
            primary.pop('annual',None)
            low_coverage=all(.2<=entry[m]['coverage']<=.8 for entry in side_info['annual'].values() for m in ('statistical','geometry'))
            e1=stats.get('admitted_es95_uncapped'); e0=geom.get('admitted_es95_uncapped')
            checks={'at_least100_admitted_dates':min(stats['admitted_dates'],geom['admitted_dates'])>=100,
                'each_annual_coverage_20_to80_percent':low_coverage,
                'ci_upper_negative':primary.get('bootstrap_difference_upper_98_75') is not None and primary['bootstrap_difference_upper_98_75']<0,
                'nonworse_two_of_three_years':sum(v is not None and v<=0 for v in annual.values())>=2 and len(annual)==3,
                'conditional_es95_nonworse':e1 is not None and e0 is not None and e1<=e0,
                'improves_without_ten_best_days':(primary.get('leave_10_most_favorable_out') or {}).get('mean_difference_stat_minus_geometry',float('inf'))<0}
            side_info['primary_comparison']=primary
            side_info['support']={'checks':checks,'conditional_payout_development_support':all(checks.values()),
                'equal_coverage_increment_established':False,'economic_policy_qualified':False}
        else:
            side_info['support']={'conditional_payout_development_support':False,'reason':'missing_prior_cut','economic_policy_qualified':False}
        summary['sides'][side]=side_info
    return summary,pd.DataFrame(daily_records)


def run(research_v13,protocol_path,output):
    output=Path(output); output.mkdir(parents=True,exist_ok=False)
    try:
        protocol_path=Path(protocol_path); root=protocol_path.parent
        protocol=json.loads(protocol_path.read_text('utf-8'))
        manifest=json.loads((root/'source_manifest.json').read_text('utf-8'))
        if sha(protocol_path)!=json.loads((root/'protocol_seal.json').read_text('utf-8'))['protocol_sha256']: raise ValueError('Protocol seal mismatch')
        if sha(root/'source_manifest.json')!=protocol['source_manifest_sha256']: raise ValueError('Source manifest seal mismatch')
        path=Path(research_v13)/'opportunity_02/row_scores_main_membership.csv'
        if sha(path)!=manifest['files'][str(path)]['sha256']: raise ValueError('Saved prediction rows changed')
        daily=prepare_daily(pd.read_csv(path))
        out,cuts=admission_memberships(daily,protocol['opportunity']['evaluation_years'])
        summary,records=analyze(out,cuts,protocol)
        summary.update({'protocol_sha256':sha(protocol_path),'amendment_sha256':sha(root/'preresult_amendment_01.json'),
            'source_sha256':sha(path),'eligible_exact08_rows_including_initialization':len(daily),
            'excluded_non08_source_rows':len(pd.read_csv(path,usecols=['row_id']))-len(daily)})
        out.to_csv(output/'daily_admission_rows.csv',index=False)
        records.to_csv(output/'daily_sufficient_stats.csv',index=False)
        save(output/'summary.json',summary)
        lines=['# v1.4 日期准入开发诊断','', '按上一年阈值，非当年分位；无实际信用、自然NR或净效用证明。','']
        for side,info in summary['sides'].items():
            lines.append(f"- {side}: {json.dumps(info['support'],ensure_ascii=False)}")
        (output/'summary.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
        print(json.dumps({'rows':len(out),'support':{k:v['support'] for k,v in summary['sides'].items()}},ensure_ascii=True))
    except Exception as exc:
        save(output/'failure.json',{'error':str(exc),'traceback':traceback.format_exc()})
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--research-v13',required=True); p.add_argument('--protocol',required=True); p.add_argument('--output',required=True)
    args=p.parse_args(); run(args.research_v13,args.protocol,args.output)
