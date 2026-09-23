"""Frozen, local-only v1.5 temporal-state design audit. No production interface."""
from __future__ import annotations
import os
for _env in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_MAX_THREADS'):
    os.environ[_env] = '6'
import argparse
import hashlib
import json
import pickle
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.special import ndtr
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits
from tools import astra_exit_v14 as ex
from tools import astra_structure_v14 as st

METHODS = ('WINDOWS','DYNAMIC','MIX2','HMM2','HMM_RESET')
FIELDS = ('ret_15','ret_30','ret_240','ret_720','ret_1440','vol_15','vol_30','vol_240','net_flow_15','net_flow_30','net_flow_240')
STEP = 1800000
SEED = 20260922

def save(path, obj):
    path = Path(path)
    path.write_text(json.dumps(ex.json_safe(obj),ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')

def read(path):
    return json.loads(Path(path).read_text('utf-8-sig'))

def digest(path): return ex.digest_file(path)

def ms(date): return int(pd.Timestamp(date, tz='UTC').timestamp()*1000)

def split_masks(frame, year):
    lo, mid, end, nxt = ms(f'{year-2}-01-01'), ms(f'{year-1}-10-01'), ms(f'{year}-01-01'), ms(f'{year+1}-01-01')
    masks = [(frame.entry_ms>=a)&(frame.entry_ms<b)&(frame.expiry_ms<b) for a,b in ((lo,mid),(mid,end),(end,nxt))]
    dates = [set(frame.loc[m,'delivery_date']) for m in masks]
    if any(dates[i]&dates[j] for i in range(3) for j in range(i+1,3)):
        raise ValueError('split expiry date overlap')
    return masks

def weights(frame): return 1/frame.groupby('delivery_date').delivery_date.transform('size').to_numpy(float)

def average(x,w): return float(np.average(np.asarray(x,float),weights=np.asarray(w,float)))

def spot_reference_observation(raw):
    """Adapter: archive entry_price is spot; last_closed_price is UM, never interchange."""
    return {**raw,'last_closed_price':raw.get('entry_price'),'last_closed_time_ms':raw.get('price_observation_ms')}

def load_observations(path):
    raw, gaps = [], []
    seen = set()
    with Path(path).open(encoding='utf-8') as f:
        for line in f:
            r = json.loads(line)
            t = int(r['as_of_ms'])
            if t in seen: raise ValueError('duplicate market timestamp')
            seen.add(t)
            ok, reason = ex.validate_spot_observation(spot_reference_observation(r))
            # UM feature windows share the same fully closed minute contract.
            if not ok:
                gaps.append({'as_of_ms':t,'reason':reason})
            r['_price_valid'] = ok
            state_ok = ok and r.get('window_status',{}).get('30')=='available'
            state_ok = state_ok and int(r['um_open_time_ms'])==t-60000 and int(r['feature_as_of_ms'])==t and int(r['last_closed_time_ms'])==t-1
            state_ok = state_ok and all(ex.finite_float(r.get(k)) is not None for k in ('ret_30','vol_30','net_flow_30'))
            state_ok = state_ok and float(r.get('vol_30') or 0)>=0
            if not state_ok and ok: gaps.append({'as_of_ms':t,'reason':'state_window_unavailable'})
            r['_state_valid'] = bool(state_ok)
            raw.append(r)
    raw.sort(key=lambda r:r['as_of_ms'])
    obs = pd.DataFrame([{k:r.get(k) for k in ('as_of_ms','last_closed_price','ret_30','vol_30','net_flow_30',*FIELDS,'_state_valid')} for r in raw])
    return raw, obs, pd.DataFrame(gaps,columns=['as_of_ms','reason'])

def prepare_data(root, out):
    r11=root/'.artifacts/astra-joint-v11-20260915'
    r13=root/'.artifacts/astra-entry-quality-v13-20260921'
    r14=root/'.artifacts/astra-entry-exit-v14-20260922'
    raw, obs, gaps = load_observations(r11/'step30/decisions/market_observations.jsonl')
    gaps.to_csv(out/'observation_gaps.csv',index=False)
    bytime={int(r['as_of_ms']):spot_reference_observation(r) for r in raw if r['_price_valid']}
    legacy_bytime={int(r['as_of_ms']):r for r in raw if ex.validate_spot_observation(r)[0]}
    saved=pd.read_csv(r14/'exit_01/exit_first_trigger_ledger.csv',low_memory=False)
    saved=saved[saved.rule.eq('SHORT_TOUCH_FIRST')]
    oldids=set(saved.row_id)
    frames=[]
    columns=list(dict.fromkeys(ex.MODEL_INPUT_COLUMNS+['outcome_status','delivery_year','as_of_utc']))
    for year in range(2020,2026):
        f=pd.read_csv(r11/f'step30/model_input/model_rows-{year}.csv',usecols=columns)
        f=f[np.isclose(f.target_width,2000)].copy()
        frames.append(f)
    originals=pd.concat(frames,ignore_index=True)
    if originals.row_id.duplicated().any(): raise ValueError('duplicate original row id')
    if not np.isfinite(originals.loss_normalized).all(): raise ValueError('invalid actual loss')
    # Recheck terminal arithmetic independently of model scores and preserve BTC tails.
    s=originals.settlement_price.to_numpy(float)
    k=originals.short_strike.to_numpy(float); l=originals.long_strike.to_numpy(float)
    put=originals.side.eq('put').to_numpy()
    payout=np.where(put,np.maximum(k-s,0)-np.maximum(l-s,0),np.maximum(s-k,0)-np.maximum(s-l,0))/s
    if not np.allclose(payout,originals.payout_btc,rtol=0,atol=1e-10): raise ValueError('original payoff parity')
    if not np.allclose(payout/(originals.actual_width/originals.entry_price),originals.loss_normalized,rtol=0,atol=1e-10): raise ValueError('normalization parity')
    records=[]; legacy_records=[]
    for row in originals.to_dict('records'):
        path=ex.evaluate_exit_path(row,bytime,'SHORT_TOUCH_FIRST')
        records.append({**{k:row[k] for k in ('row_id','side','entry_ms','expiry_ms','delivery_date','actual_width','short_strike','long_strike','entry_price','loss_normalized','payout_btc')},
            **path,'decision_ms':path.get('trigger_as_of_ms'), 'target':row['loss_normalized']})
        if row['row_id'] in oldids:
            legacy_records.append({'row_id':row['row_id'],**ex.evaluate_exit_path(row,legacy_bytime,'SHORT_TOUCH_FIRST')})
    exit_rows=pd.DataFrame(records)
    exit_rows['state_available']=exit_rows.decision_ms.isin(obs.loc[obs._state_valid,'as_of_ms'])
    exit_rows['eligible']=exit_rows.path_status.eq('triggered')&exit_rows.state_available
    parity=pd.DataFrame(legacy_records).merge(saved[['row_id','path_status','trigger_as_of_ms','trigger_spot']],on='row_id',suffixes=('','_old'),validate='one_to_one')
    if len(parity)!=len(saved): raise ValueError('v14 exit membership missing')
    for name in ('path_status','trigger_as_of_ms','trigger_spot'):
        a,b=parity[name],parity[name+'_old']
        ok=(a.fillna('NULL').eq(b.fillna('NULL')) if name=='path_status' else np.isclose(a,b,equal_nan=True,rtol=0,atol=1e-8))
        if not np.all(ok): raise ValueError('v14 trigger parity '+name)
    changed=exit_rows.merge(saved[['row_id','path_status','trigger_as_of_ms','trigger_spot']],on='row_id',suffixes=('_spot','_legacy_um'),validate='one_to_one')
    changed['status_changed']=changed.path_status_spot.ne(changed.path_status_legacy_um)
    changed['trigger_time_changed']=~np.isclose(changed.trigger_as_of_ms_spot,changed.trigger_as_of_ms_legacy_um,rtol=0,atol=0,equal_nan=True)
    changed.to_csv(out/'spot_vs_legacy_um_triggers.csv',index=False)
    exit_rows.to_csv(out/'exit_population.csv',index=False)
    pairs=pd.read_csv(r13/'structure_ledger_01/paired_structure_rows.csv')
    source=read(r13/'source_manifest.json')
    pair_check=st.validate_pair_arithmetic(pairs,st.load_delivery_prices(source))
    pairs['decision_ms']=pairs.entry_ms
    pairs['target']=pairs.delta_normalized
    pairs['eligible']=pairs.decision_ms.isin(obs.loc[obs._state_valid,'as_of_ms'])
    pairs['state_available']=pairs.eligible
    pairs.to_csv(out/'structure_population.csv',index=False)
    obs[obs._state_valid].to_csv(out/'market_sequence.csv',index=False)
    audit={'market_observations':len(raw),'valid_state_observations':int(obs._state_valid.sum()),'gaps':len(gaps),
      'original_rows':len(originals),'exit_paths':exit_rows.path_status.value_counts().to_dict(),'exit_state_missing_at_trigger':int((exit_rows.path_status.eq('triggered')&~exit_rows.eligible).sum()),
      'v14_trigger_parity_rows':len(parity),'v14_parity_identity':'legacy UM reference only, not spot validation',
      'spot_vs_um_status_changed':int(changed.status_changed.sum()),'spot_vs_um_trigger_time_changed':int(changed.trigger_time_changed.sum()),
      'pair_arithmetic':pair_check,'pairing_gap_rows':len(pd.read_csv(r13/'structure_ledger_01/pairing_gaps.csv')),
      'entry_price_identity':'archive entry_price=spot; last_closed_price=UM; trigger and exit valuation now use explicitly adapted spot entry_price',
      'source_archive_reused_not_raw_minute_regeneration':True,'new_market_data':False}
    save(out/'data_audit.json',audit)
    return obs[obs._state_valid].reset_index(drop=True),{'exit':exit_rows,'structure':pairs}

def bs_spread_btc(side,spot,short,long,hours,iv):
    """Shared synthetic USD Black-Scholes values converted to current BTC."""
    s,k,l,t=np.broadcast_arrays(np.asarray(spot,float),np.asarray(short,float),np.asarray(long,float),np.asarray(hours,float)/8760)
    if (s<=0).any() or (k<=0).any() or (l<=0).any() or (t<0).any() or iv<=0: raise ValueError('invalid valuation inputs')
    put=np.broadcast_to(np.asarray(side)=='put',s.shape)
    def leg(strike):
        v=iv*np.sqrt(np.maximum(t,1e-30))
        d1=(np.log(s/strike)+.5*v*v)/v; d2=d1-v
        val=np.where(put,strike*ndtr(-d2)-s*ndtr(-d1),s*ndtr(d1)-strike*ndtr(d2))
        return np.where(t==0,np.where(put,np.maximum(strike-s,0),np.maximum(s-strike,0)),val)
    return (leg(k)-leg(l))/s

def task_features(frame, market, state, dyn, method, task):
    index=pd.Index(market.as_of_ms).get_indexer(frame.decision_ms)
    if (index<0).any(): raise ValueError('eligible missing state')
    m=market.iloc[index]
    if task=='exit':
        spot=frame.trigger_spot.to_numpy(float); width=frame.actual_width.to_numpy(float)
        sign=np.where(frame.side.eq('put'),1.,-1.)
        geom=np.column_stack([(frame.expiry_ms-frame.decision_ms)/3600000,sign*(spot-frame.short_strike)/width,width/spot,spot/frame.entry_price])
    else: geom=frame[['dte_hours','distance_to_width','shift_to_width','width_fraction']].to_numpy(float)
    base=np.column_stack([geom,m[list(FIELDS)].to_numpy(float)])
    if method!='WINDOWS': base=np.column_stack([base,dyn[index]])
    key={'MIX2':'mix_p1','HMM2':'hmm_p1','HMM_RESET':'hmm_reset_p1'}.get(method)
    if key: base=np.column_stack([base,state[key][index]])
    if np.isinf(base).any(): raise ValueError('infinite feature')
    return base

def calibrate(pred,y,w,days):
    p,a=average(pred,w),average(y,w)
    ok=days>=30 and np.isfinite(p) and p>0 and a>=0
    scale=(days*a+30*p)/((days+30)*p) if ok else 1.
    return scale,{'days':days,'actual_mean':a,'raw_pred_mean':p,'scale':scale,'qualified':ok}

def bootstrap(daily,block=7,confidence=.9875):
    dates=pd.to_datetime(daily.index)
    b=((dates-pd.Timestamp('1970-01-01')).days//block).to_numpy()
    f=pd.DataFrame({'b':b,'v':daily.to_numpy(float)}).groupby('b').v.agg(['sum','count'])
    if len(f)<2: return {'mean':float(daily.mean()),'lower':None,'upper':None,'blocks':len(f)}
    rng=np.random.default_rng(SEED)
    ix=rng.integers(0,len(f),size=(2000,len(f)))
    sim=f['sum'].to_numpy()[ix].sum(axis=1)/f['count'].to_numpy()[ix].sum(axis=1)
    tail=(1-confidence)/2
    return {'mean':float(daily.mean()),'lower':float(np.quantile(sim,tail)),'upper':float(np.quantile(sim,1-tail)), 'blocks':len(f),'confidence':confidence,'block_days':block}

def comparison(daily,positive=True):
    result=bootstrap(daily)
    result['block14']=bootstrap(daily,14)
    ascending=not positive
    kept=daily.sort_values(ascending=ascending).iloc[10:]
    result['without10best']=float(kept.mean()) if len(kept) else None
    years=pd.to_datetime(daily.index).year
    annual=daily.groupby(years).mean()
    result['annual']={str(y):float(v) for y,v in annual.items()}
    result['nonworse_years']=int((annual>=0).sum() if positive else (annual<=0).sum())
    return result

def summarize_predictions(pred,out):
    metrics=[]; contrasts=[]; bins=[]
    for task,t in pred.groupby('task'):
        for side,f in t.groupby('side'):
            w=weights(f); y=f.target.to_numpy(float)
            for method in METHODS:
                p=f[method].to_numpy(float)
                metrics.append({'task':task,'side':side,'method':method,'rows':len(f),'days':f.delivery_date.nunique(),
                    'mse':average((p-y)**2,w),'bias':average(p-y,w)})
                binid=np.searchsorted([.05,.1,.2,.4,.8],p,side='right')
                for b in range(6):
                    sel=binid==b
                    if sel.any(): bins.append({'task':task,'side':side,'method':method,'bin':b,'rows':int(sel.sum()),'days':f.loc[sel,'delivery_date'].nunique(),
                      'prediction':average(p[sel],w[sel]),'actual':average(y[sel],w[sel])})
        # Date means first within side, then pooled equal sides.
        for comp in ('DYNAMIC','MIX2','HMM_RESET','WINDOWS'):
            sq=(t.HMM2-t.target)**2-(t[comp]-t.target)**2
            d=t.assign(error_delta=sq).groupby(['delivery_date','side']).error_delta.mean().unstack('side').dropna().mean(axis=1)
            contrasts.append({'task':task,'comparison':'HMM2-'+comp,**comparison(d,False)})
    pd.DataFrame(metrics).to_csv(out/'predictive_metrics.csv',index=False)
    pd.DataFrame(bins).to_csv(out/'calibration_bins.csv',index=False)
    save(out/'predictive_contrasts.json',contrasts)
    return metrics,contrasts

def scenario_evaluation(pred,populations,protocol,out):
    metrics=[]; contrasts=[]
    for task,allrows in populations.items():
        keep=np.zeros(len(allrows),bool)
        for yr in (2022,2023,2024,2025): keep|=split_masks(allrows,yr)[2].to_numpy()
        pop=allrows.loc[keep].copy()
        pop['original_weight']=1/pop.groupby(['side','delivery_date']).row_id.transform('size')
        pp=pred[pred.task.eq(task)][['row_id',*METHODS]]
        f=pop.merge(pp,on='row_id',how='left',validate='one_to_one')
        if task=='exit':
            known=~f.path_status.eq('path_unknown')
            decision=f.path_status.eq('triggered')
            # State-unavailable first triggers have explicit no-action fallback, not fabricated predictions.
            spot=f.trigger_spot.fillna(f.entry_price).to_numpy(float)
            hours=((f.expiry_ms-f.decision_ms.fillna(f.entry_ms))/3600000).to_numpy(float)
            allcontrol='EXIT_ALL_TOUCH'; holdcontrol='HOLD'
        else:
            known=np.ones(len(f),bool); decision=np.ones(len(f),bool)
            spot=f.entry_price.to_numpy(float); hours=f.dte_hours.to_numpy(float)
            allcontrol='OUTWARD_ALL'; holdcontrol='ORIGINAL'
        denom=(f.actual_width/f.entry_price).to_numpy(float)
        y=f.target.to_numpy(float)
        for scenario in protocol['valuation']['scenarios']:
            sc=scenario['id']; iv=scenario['iv']; cost=scenario['cost_fraction']
            orig=bs_spread_btc(f.side.to_numpy(),spot,f.short_strike,f.long_strike,hours,iv)/denom
            if task=='exit': threshold=orig+cost
            else:
                outer=bs_spread_btc(f.side.to_numpy(),spot,f.outward_short_strike,f.outward_long_strike,hours,iv)/denom
                threshold=orig-outer
            ledger=f[['row_id','delivery_date','side','original_weight']].copy()
            ledger['known_path']=known; ledger['prediction_available']=f.HMM2.notna()
            ledger['target']=y; ledger['assumed_threshold']=threshold
            gains={}; costs={}; actions={}
            for method in (holdcontrol,allcontrol,*METHODS):
                if method==holdcontrol: act=np.zeros(len(f),bool)
                elif method==allcontrol: act=np.asarray(decision,bool)
                else: act=np.asarray(decision,bool)&f[method].notna().to_numpy()&(f[method].fillna(-np.inf).to_numpy()>threshold)
                gain=np.where(act,y-threshold,0.)
                gain=np.where(known,gain,np.nan)
                baseline=f.loss_normalized.to_numpy(float)
                # Negative result = normalized loss; for structure synthetic entry credit is included.
                policy_cost=baseline-gain if task=='exit' else baseline-orig+cost-gain
                gains[method]=gain; costs[method]=policy_cost; actions[method]=act
                ledger[method+'_action']=act; ledger[method+'_gain']=gain; ledger[method+'_loss']=policy_cost
                for side in ('put','call'):
                    ix=f.side.eq(side).to_numpy()&np.asarray(known)
                    ww=f.original_weight.to_numpy(float)[ix]
                    dates=f.loc[f.side.eq(side),'delivery_date'].nunique()
                    weighted_sum=np.sum(ww*gain[ix])
                    metrics.append({'task':task,'scenario':sc,'side':side,'method':method,'original_rows':int(f.side.eq(side).sum()),
                        'known_rows':int(ix.sum()),'dates':dates,'gain_original_denominator':float(weighted_sum/dates),
                        'unknown_original_weight':float(f.loc[f.side.eq(side)&~np.asarray(known),'original_weight'].sum()/dates),
                        'model_unavailable_original_weight':float(f.loc[f.side.eq(side)&f.HMM2.isna()&np.asarray(decision),'original_weight'].sum()/dates),
                        'mean_known_gain':average(gain[ix],ww),'es95_loss':st.weighted_es(policy_cost[ix],ww),
                        'action_original_weight':float(np.sum(ww*act[ix])/dates),
                        'foregone_zero_payout_original_weight':float(np.sum(ww*act[ix]*(f.loss_normalized.to_numpy()[ix]==0))/dates),
                        'actual_market_ev':None})
            for comp in ('DYNAMIC','MIX2','HMM_RESET','WINDOWS',holdcontrol,allcontrol):
                delta=gains['HMM2']-gains[comp]
                df=f[['delivery_date','side','original_weight']].copy()
                df['delta']=delta*df.original_weight
                # A day with a path gap reports known contribution and unknown mass separately; no zero-imputation of unknown action.
                d=df.groupby(['delivery_date','side']).delta.sum(min_count=1).unstack('side').dropna().mean(axis=1)
                con={'task':task,'scenario':sc,'comparison':'HMM2-'+comp,**comparison(d,True)}
                con['es95_nonworse_both_sides']=all(
                    next(v['es95_loss'] for v in metrics if v['task']==task and v['scenario']==sc and v['side']==s and v['method']=='HMM2') <=
                    next(v['es95_loss'] for v in metrics if v['task']==task and v['scenario']==sc and v['side']==s and v['method']==comp)
                    for s in ('put','call'))
                contrasts.append(con)
            ledger.to_csv(out/f'{task}_scenario_{sc}.csv',index=False)
    pd.DataFrame(metrics).to_csv(out/'scenario_metrics.csv',index=False)
    save(out/'scenario_contrasts.json',contrasts)
    return metrics,contrasts

def run(research,output_name):
    root=research.parents[1]; out=research/output_name
    out.mkdir(exist_ok=False)
    seal=read(research/'protocol_seal.json'); protocol=read(research/'protocol_v15.json')
    if digest(research/'protocol_v15.json')!=seal['protocol_sha256']: raise ValueError('protocol changed')
    if digest(research/'source_manifest.json')!=seal['source_manifest_sha256']: raise ValueError('manifest changed')
    for path,item in read(research/'source_manifest.json')['files'].items():
        if digest(path)!=item['sha256']: raise ValueError('input changed '+path)
    correction=read(research/'source_correction_02.json')
    correction_seal=read(research/'source_correction_02_seal.json')
    if digest(research/'source_correction_02.json')!=correction_seal['sha256']: raise ValueError('source correction changed')
    save(out/'execution_identity.json',{'started_at_utc':datetime.now(timezone.utc).isoformat(), 'protocol_sha256':seal['protocol_sha256'],
        'source_correction_sha256':correction_seal['sha256'],
        'source_code':{str(p):digest(p) for p in (Path(__file__),Path(__file__).with_name('astra_state_v15.py'))},'new_llm_calls':0,'cpu_max':6,'training_tasks':1})
    sys.path.insert(0,str(research/'.deps'))
    from tools import astra_state_v15 as state_api
    market,populations=prepare_data(root,out)
    print(json.dumps({'stage':'data_ready','market':len(market),'tasks':{k:len(v) for k,v in populations.items()}}),flush=True)
    allpred=[]; fitlogs=[]; state_support=[]
    for year in (2022,2023,2024,2025):
        lo,mid,end,nxt=ms(f'{year-2}-01-01'),ms(f'{year-1}-10-01'),ms(f'{year}-01-01'),ms(f'{year+1}-01-01')
        mk=market[(market.as_of_ms>=lo)&(market.as_of_ms<nxt)].reset_index(drop=True)
        train=(mk.as_of_ms<mid).to_numpy()
        rawx=np.column_stack([mk.ret_30,np.log(mk.vol_30+1e-8),mk.net_flow_30])
        median=np.median(rawx[train],axis=0); scale=np.subtract(*np.percentile(rawx[train],[75,25],axis=0)); scale[scale==0]=1
        x=(rawx-median)/scale
        with threadpool_limits(limits=6): models=state_api.fit_state_models(x[train],mk.as_of_ms.to_numpy()[train],seed=SEED)
        state=state_api.filter_states(models,x,mk.as_of_ms.to_numpy())
        dyn=state_api.dynamic_features(x,mk.as_of_ms.to_numpy())
        export=state_api.portable_model(models)
        save(out/f'states_{year}.json',{'scaler_median':median.tolist(),'scaler_iqr':scale.tolist(),'model':export})
        sp=pd.DataFrame({'as_of_ms':mk.as_of_ms,**state})
        sp.to_csv(out/f'state_probabilities_{year}.csv',index=False)
        for split,a,b in (('fit',lo,mid),('cal',mid,end),('eval',end,nxt)):
            sel=(mk.as_of_ms>=a)&(mk.as_of_ms<b)
            for z in (0,1):
                occupied=(state['hmm_p1']>=.5)==bool(z)
                tm=mk.loc[sel&occupied,'as_of_ms']
                state_support.append({'year':year,'scope':'market','split':split,'state':z,'rows':len(tm),
                    'fraction':float((sel&occupied).sum()/sel.sum()),'days':int(pd.to_datetime(tm,unit='ms',utc=True).dt.date.nunique())})
        for task,pop in populations.items():
            usable=pop[pop.eligible].copy()
            masks=split_masks(usable,year)
            for side in ('put','call'):
                slices=[usable[m&usable.side.eq(side)].copy() for m in masks]
                fit,cal,ev=slices
                if any(f.empty for f in slices): raise ValueError(f'insufficient task split {task}/{year}/{side}')
                ix=[pd.Index(mk.as_of_ms).get_indexer(f.decision_ms) for f in slices]
                for split,f,ii in zip(('fit','cal','eval'),slices,ix):
                    for z in (0,1):
                        occupied=(state['hmm_p1'][ii]>=.5)==bool(z)
                        state_support.append({'year':year,'scope':task,'side':side,'split':split,'state':z,'rows':int(occupied.sum()),
                            'fraction':float(occupied.mean()),'days':int(f.loc[occupied,'delivery_date'].nunique())})
                prediction=ev[['row_id','side','entry_ms','expiry_ms','decision_ms','delivery_date','target']].copy()
                prediction['year']=year; prediction['task']=task
                prediction['hmm_p1']=state['hmm_p1'][ix[2]]
                for method in METHODS:
                    xs=[task_features(f,mk,state,dyn,method,task) for f in slices]
                    y=fit.target.to_numpy(float)
                    head=HistGradientBoostingRegressor(loss='poisson',max_iter=100,max_leaf_nodes=7,min_samples_leaf=100,
                        l2_regularization=10.,learning_rate=.05,early_stopping=False,random_state=SEED)
                    wf=weights(fit); wc=weights(cal)
                    with threadpool_limits(limits=6): head.fit(xs[0],y,sample_weight=wf/wf.mean())
                    pc=head.predict(xs[1]); multiplier,calinfo=calibrate(pc,cal.target,wc,cal.delivery_date.nunique())
                    pp=head.predict(xs[2])*multiplier
                    if not np.isfinite(pp).all() or (pp<0).any(): raise ValueError('invalid prediction')
                    prediction[method]=pp
                    with (out/f'head_{task}_{side}_{year}_{method}.pkl').open('wb') as f:
                        pickle.dump({'head':head,'calibration':calinfo,'feature_count':xs[0].shape[1]},f)
                    fitlogs.append({'task':task,'side':side,'year':year,'method':method,'fit_rows':len(fit),'cal_rows':len(cal),'eval_rows':len(ev),
                        'fit_max_expiry':int(fit.expiry_ms.max()),'cal_start':mid,'cal_max_expiry':int(cal.expiry_ms.max()),'eval_start':end,
                        'calibration':calinfo})
                allpred.append(prediction)
        save(out/'fit_log.json',fitlogs)
        pd.concat(allpred,ignore_index=True).to_csv(out/'predictions_partial.csv',index=False)
        print(json.dumps({'stage':'fold_done','year':year,'prediction_rows':sum(len(p) for p in allpred)}),flush=True)
    pred=pd.concat(allpred,ignore_index=True)
    pred.to_csv(out/'predictions.csv',index=False)
    pd.DataFrame(state_support).to_csv(out/'state_support.csv',index=False)
    pmetrics,pcontrast=summarize_predictions(pred,out)
    smetrics,scontrast=scenario_evaluation(pred,populations,protocol,out)
    save(out/'summary.json',{'schema':'astra_state_audit_result@1.5.0','completed_at_utc':datetime.now(timezone.utc).isoformat(),
      'prediction_rows':len(pred),'predictive_contrasts':pcontrast,
      'primary_gate_contrasts':[r for r in scontrast if r['scenario']=='IV60' and r['comparison'] in ('HMM2-DYNAMIC','HMM2-MIX2')],
      'diagnostic_scenario_contrasts':[r for r in scontrast if not(r['scenario']=='IV60' and r['comparison'] in ('HMM2-DYNAMIC','HMM2-MIX2'))],
      'calibrations_all_qualified':all(r['calibration']['qualified'] for r in fitlogs),
      'qualification':'pending_independent_acceptance','actual_market_EV':None,'natural_nr_effect':None,
      'runtime_model_changed':False,'forward_90d_started':False})
    print(json.dumps({'stage':'finished','output':str(out),'prediction_rows':len(pred)}),flush=True)

def main():
    p=argparse.ArgumentParser(); p.add_argument('--research',type=Path,required=True);p.add_argument('--output',default='run_01')
    args=p.parse_args()
    try: run(args.research,args.output)
    except Exception:
        out=args.research/args.output
        if out.exists(): (out/'failure.txt').write_text(traceback.format_exc(),encoding='utf-8')
        raise

if __name__=='__main__': main()
