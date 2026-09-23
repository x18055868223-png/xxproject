"""Frozen price-conditional underwriting diagnostics; research only, no runtime writes."""
from __future__ import annotations
import os
for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_MAX_THREADS'):
    os.environ[_name] = '6'
import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from scipy.special import ndtr

SEED = 20260922
STATES = ('UP', 'DOWN', 'RANGE')
SCENARIOS = {'IV40': (.4, .025), 'IV60': (.6, .025), 'IV80': (.8, .025),
             'IV60_COST5': (.6, .05), 'RV4H_X125': (None, .025)}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def safe(value):
    if isinstance(value, dict): return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [safe(v) for v in value]
    if isinstance(value, np.ndarray): return safe(value.tolist())
    if isinstance(value, (np.integer,)): return int(value)
    if isinstance(value, (float, np.floating)): return float(value) if np.isfinite(value) else None
    if isinstance(value, (bool, np.bool_)): return bool(value)
    return value


def save(path, value):
    Path(path).write_text(json.dumps(safe(value), ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def read(path): return json.loads(Path(path).read_text('utf-8-sig'))


def bs_spread_btc(side, spot, short, long, hours, iv):
    """Shared exogenous synthetic quote, r=q=0, no inferred skew or option-market labels."""
    s, k, l, t, sigma = np.broadcast_arrays(*[np.asarray(v, float) for v in (spot, short, long, hours, iv)])
    t = t / 8760.
    p = np.broadcast_to(np.asarray(side), s.shape)
    if (not all(np.isfinite(v).all() for v in (s, k, l, t, sigma)) or
            (s <= 0).any() or (k <= 0).any() or (l <= 0).any() or (t < 0).any() or (sigma <= 0).any() or
            not np.isin(p, ['put', 'call']).all() or
            ((p == 'put') & (k <= l)).any() or ((p == 'call') & (k >= l)).any()):
        raise ValueError('invalid quote inputs or credit-spread leg order')
    def leg(strike):
        v = sigma * np.sqrt(np.maximum(t, 1e-30))
        d1 = (np.log(s / strike) + .5 * v * v) / v
        d2 = d1 - v
        val = np.where(p == 'put', strike * ndtr(-d2) - s * ndtr(-d1), s * ndtr(d1) - strike * ndtr(d2))
        intrinsic = np.where(p == 'put', np.maximum(strike - s, 0), np.maximum(s - strike, 0))
        return np.where(t == 0, intrinsic, val)
    return (leg(k) - leg(l)) / s


def weighted_mean(x, w):
    x, w = np.asarray(x, float), np.asarray(w, float)
    valid = np.isfinite(x) & np.isfinite(w) & (w > 0)
    return float(np.sum(x[valid] * w[valid]) / w[valid].sum()) if valid.any() else np.nan


def weighted_es(x, w, alpha=.95):
    """Worst (1-alpha) mass, including a fractional boundary atom; no clipping of losses."""
    x, w = np.asarray(x, float), np.asarray(w, float)
    valid = np.isfinite(x) & np.isfinite(w) & (w > 0)
    if not valid.any(): return np.nan
    x, w = x[valid], w[valid]
    ix = np.argsort(-x, kind='stable'); x, w = x[ix], w[ix]
    mass = w.sum() * (1 - alpha)
    take = np.minimum(w, np.maximum(0., mass - np.r_[0., np.cumsum(w)[:-1]]))
    return float(np.sum(x * take) / mass)


def daily_sum(frame, values):
    s = pd.Series(np.asarray(values, float) * frame.original_weight.to_numpy(), index=frame.delivery_date)
    g = s.groupby(level=0)
    # A partially unobserved budget is not a smaller, fully known budget.
    return g.sum(min_count=1).where(g.count().eq(g.size()))


def block_interval(daily, block_days=7, confidence=.95, repetitions=4000):
    """Nonoverlapping calendar blocks, shared frozen seed; development uncertainty only."""
    daily = daily.dropna().sort_index()
    if not len(daily): return {'mean': None, 'lower': None, 'upper': None, 'days': 0}
    dates = pd.to_datetime(daily.index)
    blocks = ((dates - pd.Timestamp('1970-01-01')).days // block_days).to_numpy()
    b = pd.DataFrame({'block': blocks, 'value': daily.to_numpy()}).groupby('block').value.agg(['sum', 'count'])
    if len(b) < 2: return {'mean': float(daily.mean()), 'lower': None, 'upper': None, 'days': len(daily)}
    rng = np.random.default_rng(SEED)
    ix = rng.integers(0, len(b), size=(repetitions, len(b)))
    sims = b['sum'].to_numpy()[ix].sum(axis=1) / b['count'].to_numpy()[ix].sum(axis=1)
    tail = (1 - confidence) / 2
    return {'mean': float(daily.mean()), 'lower': float(np.quantile(sims, tail)), 'upper': float(np.quantile(sims, 1-tail)),
            'confidence': confidence, 'block_days': block_days, 'blocks': len(b), 'days': len(daily)}


def contrast(frame, values, confidence=.95):
    d = daily_sum(frame, values)
    good = d.dropna()
    result = block_interval(d, confidence=confidence)
    result['block14'] = block_interval(d, 14, confidence)
    result['annual'] = {str(k): float(v) for k, v in good.groupby(pd.to_datetime(good.index).year).mean().items()}
    result['without10best_days'] = float(good.sort_values().iloc[:-10].mean()) if len(good) > 10 else None
    result['without10worst_days'] = float(good.sort_values().iloc[10:].mean()) if len(good) > 10 else None
    result['top10_positive_sum'] = float(good.clip(lower=0).nlargest(10).sum())
    result['total_sum'] = float(good.sum())
    return result


def scenario_quote(frame, scenario):
    iv, cost = SCENARIOS[scenario]
    sigma = np.repeat(iv, len(frame)) if iv is not None else 1.25 * frame.rv4_annualized.to_numpy(float)
    valid = np.isfinite(sigma) & (sigma > 0)
    credit = np.full(len(frame), np.nan)
    f = frame.loc[valid]
    credit[valid] = bs_spread_btc(f.side, f.entry_price, f.short_strike, f.long_strike,
                                 f.dte_hours, sigma[valid]) / (f.actual_width / f.entry_price)
    return credit, cost, sigma


def load_original(root):
    root = Path(root)
    r = root / '.artifacts/astra-joint-v11-20260915'
    p = r / 'step30/models_annual_calibrated_20260921/rolling_candidate_predictions'
    paths = [p / 'catboost__statistical__D4.csv', p / 'gam__geometry__C0.1__A0.1.csv']
    stat, geom = [pd.read_csv(x) for x in paths]
    if stat.row_id.duplicated().any() or geom.row_id.duplicated().any(): raise ValueError('duplicate prediction identity')
    if set(stat.row_id) != set(geom.row_id): raise ValueError('different base opportunity sets')
    geom = geom.set_index('row_id').loc[stat.row_id].reset_index()
    for key in ('as_of_ms', 'entry_ms', 'expiry_ms', 'side', 'delivery_date', 'fold', 'actual_width'):
        if not np.array_equal(stat[key], geom[key]): raise ValueError('prediction contract mismatch: ' + key)
    if not np.allclose(stat.actual_loss_normalized, geom.actual_loss_normalized, rtol=0, atol=1e-12): raise ValueError('label mismatch')
    inputs = sorted((r / 'step30/model_input').glob('model_rows-*.csv'))
    columns = ['row_id','event_family','observation_kind','as_of_ms','entry_ms','expiry_ms','delivery_date',
               'side','target_width','actual_width','short_strike','long_strike','entry_price','dte_hours',
               'short_distance_fraction','width_fraction','vol_240','price_observation_ms','short_creation_ms',
               'long_creation_ms','loss_normalized','payout_btc','settlement_price','price_source','source_observation_hash']
    raw = pd.concat([pd.read_csv(x, usecols=columns) for x in inputs], ignore_index=True)
    if raw.row_id.duplicated().any(): raise ValueError('duplicate model-input row')
    selected = raw.set_index('row_id').reindex(stat.row_id).reset_index()
    if selected.as_of_ms.isna().any(): raise ValueError('base input is missing')
    for key in ('as_of_ms','entry_ms','expiry_ms','delivery_date','actual_width'):
        if not np.array_equal(stat[key], selected[key]): raise ValueError('input identity mismatch: ' + key)
    if not (stat.side.str.replace('_credit','',regex=False).to_numpy() == selected.side.to_numpy()).all(): raise ValueError('side mismatch')
    if not np.allclose(stat.actual_loss_normalized, selected.loss_normalized, rtol=0, atol=1e-12): raise ValueError('input outcome mismatch')
    if not np.allclose(selected.payout_btc / (selected.actual_width / selected.entry_price), selected.loss_normalized, rtol=1e-10, atol=1e-12): raise ValueError('inverse normalization mismatch')
    if not ((selected.price_observation_ms == selected.as_of_ms-1) & (selected.short_creation_ms <= selected.entry_ms) & (selected.long_creation_ms <= selected.entry_ms)).all(): raise ValueError('availability violation')
    selected['mu_stat'] = stat.expected_loss_normalized.to_numpy()
    selected['mu_geom'] = geom.expected_loss_normalized.to_numpy()
    selected['fold'] = stat.fold.to_numpy()
    selected['evaluation_year'] = pd.to_datetime(selected.as_of_ms,unit='ms',utc=True).dt.year
    if set(selected.evaluation_year) != {2022,2023,2024,2025}: raise ValueError('unexpected reused fold population')
    if not np.isfinite(selected[['mu_stat','mu_geom','loss_normalized']]).all().all(): raise ValueError('invalid prediction or outcome')
    selected['distance_to_width'] = selected.short_distance_fraction / selected.width_fraction
    sources = {str(x):digest(x) for x in paths+inputs}
    return selected, sources


def schedule(frame, name):
    f = frame.copy()
    if name == 'UTC08':
        f = f.loc[f.as_of_ms.mod(86400000).eq(8*3600000)].copy()
    elif name != 'CLOCK30_BUDGET': raise ValueError('unknown schedule')
    if f.duplicated(['side','as_of_ms']).any(): raise ValueError('multiple contracts per side clock')
    if name == 'UTC08' and f.duplicated(['side','delivery_date']).any(): raise ValueError('duplicate primary daily allocation')
    f['original_weight'] = 1 / f.groupby(['side','delivery_date']).row_id.transform('size')
    return f.reset_index(drop=True)


def policy_metrics(frame, credit, cost, mu, *, eligible=None):
    known = np.isfinite(credit) if mu is None else np.isfinite(credit) & np.isfinite(mu)
    action = known.copy() if mu is None else np.asarray(credit - mu - cost > 0) & known
    if eligible is not None:
        eligible = np.asarray(eligible, bool)
        if eligible.shape != action.shape: raise ValueError('policy eligibility shape mismatch')
        action = action & eligible
    one = credit - frame.loss_normalized.to_numpy() - cost
    pnl = np.where(known, np.where(action, one, 0.), np.nan)
    w = frame.original_weight.to_numpy()
    daily = daily_sum(frame, pnl)
    # Positive outcome means cash profit under the shared synthetic entry quote only.
    return {'known_weight': float(w[known].sum()), 'unknown_weight': float(w[~known].sum()),
            'known_weight_fraction':float(w[known].sum()/w.sum()),
            'known_net_contribution_per_all_original_weight':float(np.sum(w[known]*pnl[known])/w.sum()),
            'complete_net_per_all_original_weight':weighted_mean(pnl,w) if known.all() else None,
            'action_weight_fraction': float(w[action].sum()/w.sum()),
            'absolute_net_per_known_original_weight': weighted_mean(pnl,w),
            'absolute_net_per_traded_weight': weighted_mean(one[action],w[action]),
            'scenario_profit_fraction_of_trades': weighted_mean((one[action]>0).astype(float),w[action]),
            'zero_payout_fraction_of_trades': weighted_mean((frame.loss_normalized.to_numpy()[action]==0).astype(float),w[action]),
            'foregone_profitable_weight_fraction': float(w[known & ~action & (one>0)].sum()/w.sum()),
            'foregone_positive_pnl_per_original_weight': float(np.sum(w[known & ~action] * np.maximum(one[known & ~action],0))/w.sum()),
            'avoided_negative_pnl_per_original_weight': float(np.sum(w[known & ~action] * np.maximum(-one[known & ~action],0))/w.sum()),
            'complete_delivery_days':int(daily.notna().sum()),'incomplete_delivery_days':int(daily.isna().sum()),
            'net_loss_es95_per_known_original_allocation': weighted_es(-pnl,w),
            'net_loss_es95_per_complete_delivery_day': weighted_es(-daily.to_numpy(),np.ones(len(daily))),
            'max_net_loss': float(np.nanmax(-pnl)), 'annual_daily_net': contrast(frame,pnl)['annual']}, action, pnl, one


def underwriting(frame, out):
    summaries, ledgers, margins = [], [], []
    for sched in ('UTC08','CLOCK30_BUDGET'):
        f = schedule(frame,sched)
        for scenario in SCENARIOS:
            credit,cost,sigma = scenario_quote(f,scenario)
            ledger = f[['row_id','side','delivery_date','as_of_ms','original_weight','regime','evaluation_year','loss_normalized','mu_geom','mu_stat']].copy()
            ledger['schedule']=sched; ledger['scenario']=scenario; ledger['credit']=credit; ledger['cost']=cost; ledger['assumed_iv']=sigma
            for side in ('put','call'):
                mask = f.side.eq(side).to_numpy(); g=f.loc[mask]
                result={'schedule':sched,'scenario':scenario,'side':side,'rows':len(g),'days':g.delivery_date.nunique(),'methods':{}}
                values={}
                for name,mu in [('B0_ALWAYS',None),('B1_GEOM',g.mu_geom.to_numpy()),('B2_STAT',g.mu_stat.to_numpy())]:
                    metric,action,pnl,one=policy_metrics(g,credit[mask],cost,mu)
                    result['methods'][name]=metric; values[name]=pnl
                    ledger.loc[mask,name+'_action']=action; ledger.loc[mask,name+'_pnl']=pnl
                    if mu is not None:
                        margin=credit[mask]-mu-cost
                        bins=pd.cut(margin,[-np.inf,-.10,-.025,0.,.025,.10,np.inf],right=False)
                        for label in bins.categories:
                            chosen=np.asarray(bins==label)
                            margins.append({'schedule':sched,'scenario':scenario,'side':side,'method':name,'margin_bin':str(label),
                                            'rows':int(chosen.sum()),'original_weight':float(g.original_weight.to_numpy()[chosen].sum()),
                                            'predicted_net':weighted_mean(margin[chosen],g.original_weight.to_numpy()[chosen]),
                                            'realized_scenario_net':weighted_mean(one[chosen],g.original_weight.to_numpy()[chosen])})
                for a,b in [('B2_STAT','B1_GEOM'),('B2_STAT','B0_ALWAYS'),('B1_GEOM','B0_ALWAYS')]:
                    result[a+'-'+b]=contrast(g,values[a]-values[b])
                summaries.append(result)
            ledger.to_csv(out / f'entry_{sched}_{scenario}.csv',index=False)
            ledgers.append(ledger)
    save(out / 'underwriting_summary.json',summaries)
    pd.DataFrame(margins).to_csv(out/'margin_calibration_bins.csv',index=False)
    return pd.concat(ledgers,ignore_index=True),summaries


def controlled_residuals(f, state):
    """Descriptive same-year geometry/RV matching; uses outcomes, never a deployable feature.

    Require five distinct dates in both target and comparator; no confidence claim for this
    fitted descriptive adjustment. Interval estimates elsewhere use unadjusted frozen scores.
    """
    g = f.copy()
    g['stratum'] = list(zip(g.evaluation_year, g.dte_hours.le(16), g.distance_to_width.le(.5),
                            g.width_fraction.le(.05), np.digitize(g.rv4_annualized,[.4,.8])))
    g['e'] = g.loss_normalized-g.mu_stat
    result = pd.Series(np.nan,index=g.index)
    for _,b in g.loc[g.rv4_annualized.notna()].groupby('stratum'):
        target=b.loc[b.regime.eq(state)]; other=b.loc[b.regime.isin(STATES) & ~b.regime.eq(state)]
        if target.delivery_date.nunique()>=5 and other.delivery_date.nunique()>=5:
            result.loc[target.index]=target.e-weighted_mean(other.e,other.original_weight)
    return result


def state_diagnostics(frame, ledgers, out):
    records, controls, triggers = [], [], []
    for sched in ('UTC08','CLOCK30_BUDGET'):
        f = schedule(frame,sched)
        for side in ('put','call'):
            g = f.loc[f.side.eq(side)].copy()
            e=(g.loss_normalized-g.mu_stat).to_numpy()
            se=((g.loss_normalized-g.mu_stat)**2-(g.loss_normalized-g.mu_geom)**2).to_numpy()
            for state in (*STATES,'MIXED','UNKNOWN'):
                m=g.regime.eq(state).to_numpy(); z=g.loc[m]; w=z.original_weight.to_numpy()
                rec={'schedule':sched,'side':side,'state':state,'rows':len(z),'delivery_days':z.delivery_date.nunique(),
                     'original_weight_fraction':float(w.sum()/g.original_weight.sum()),
                     'positive_payout_rows':int(z.loss_normalized.gt(0).sum()), 'max_loss':float(z.loss_normalized.max()),
                     'conditional_actual_mean':weighted_mean(z.loss_normalized,w),
                     'conditional_mu_stat':weighted_mean(z.mu_stat,w),'conditional_mu_geom':weighted_mean(z.mu_geom,w),
                     'conditional_error_D_minus_stat':weighted_mean(e[m],w),
                     'conditional_SE_stat_minus_geom':weighted_mean(se[m],w),
                     'episode_count':int(z.episode_id.nunique()),
                     'geometry_RV_means':{k:weighted_mean(z[k],w) for k in ['dte_hours','distance_to_width','width_fraction','rv4_annualized']},
                     'annual_support':{str(y):{'rows':len(b),'days':b.delivery_date.nunique(),'weight':float(b.original_weight.sum()),
                                              'error':weighted_mean(b.loss_normalized-b.mu_stat,b.original_weight)} for y,b in z.groupby('evaluation_year')}}
                confidence=1-.05/6 if state in STATES else .95
                rec['error_original_opportunity_contribution']=contrast(g,np.where(m,e,0.),confidence)
                rec['SE_original_opportunity_contribution']=contrast(g,np.where(m,se,0.),confidence)
                for sc in SCENARIOS:
                    q=ledgers.loc[(ledgers.schedule==sched)&(ledgers.scenario==sc)&(ledgers.side==side)].set_index('row_id').loc[g.row_id]
                    delta=(q.B2_STAT_pnl-q.B1_GEOM_pnl).to_numpy()
                    # A genuinely unavailable quote remains unknown, including outside the cell.
                    contrib=np.where(np.isfinite(delta),np.where(m,delta,0.),np.nan)
                    rec[sc+'_B2_minus_B1']=contrast(g,contrib,confidence)
                if state in STATES:
                    ctrl=controlled_residuals(g,state); supported=ctrl.notna()
                    annual=[]
                    for year,b in g.loc[supported].groupby('evaluation_year'):
                        annual.append({'year':int(year),'days':b.delivery_date.nunique(),'matched_error':weighted_mean(ctrl.loc[b.index],b.original_weight)})
                    rec['matched_error_descriptive']=weighted_mean(ctrl[supported],g.loc[supported,'original_weight'])
                    rec['matched_days']=g.loc[supported,'delivery_date'].nunique()
                    rec['matched_original_weight_fraction']=float(g.loc[supported,'original_weight'].sum()/g.original_weight.sum())
                    rec['matched_annual_descriptive']=annual
                    c=g.loc[supported,['row_id','side','evaluation_year','delivery_date','original_weight']].copy()
                    c['state']=state;c['schedule']=sched;c['matched_residual']=ctrl[supported];controls.append(c)
                    # Diagnostic gate only: repeated economically material omission, not an efficacy pass.
                    pos=[x['year'] for x in annual if x['days']>=20 and x['matched_error']>=.005]
                    neg=[x['year'] for x in annual if x['days']>=20 and x['matched_error']<=-.005]
                    repeat=any(max(a)-min(a)>=2 for a in (pos,neg) if len(a)>=2)
                    if sched=='UTC08':triggers.append({'side':side,'state':state,'residual_followup_candidate':repeat,'positive_years':pos,'negative_years':neg})
                records.append(rec)
    save(out/'state_diagnostics.json',records);save(out/'L01_gate.json',{'scope':'diagnostic only; separately freeze any L02','cells':triggers})
    pd.concat(controls,ignore_index=True).to_csv(out/'descriptive_matched_residuals.csv',index=False)
    return records


def run(root, research, run_name):
    root,research=Path(root),Path(research)
    seal=read(research/'protocol_seal.json')
    if digest(research/'protocol_v16.json')!=seal['protocol_sha256']:raise ValueError('protocol hash mismatch')
    out=research/run_name
    out.mkdir(exist_ok=False)
    save(out/'run_started.json',{'utc':datetime.now(timezone.utc).isoformat(),'protocol_sha256':seal['protocol_sha256']})
    try:
        frame,sources=load_original(root)
        from tools.astra_regime_v16 import build_from_facts
        market=build_from_facts(root/'.artifacts/astra-joint-v1-20260914/facts/um',frame.as_of_ms.unique())
        sources.update(market.attrs.get('source_hashes',{}))
        market=market.sort_values('as_of_ms').reset_index(drop=True)
        market['episode_id']=((market.regime!=market.regime.shift()) | (market.as_of_ms.diff()!=1800000)).cumsum()
        market.to_csv(out/'market_regime.csv',index=False)
        frame=frame.merge(market,on='as_of_ms',how='left',validate='many_to_one')
        if frame.regime.isna().any():raise ValueError('missing administrative state')
        frame.to_csv(out/'base_rows.csv',index=False)
        save(out/'input_audit.json',{'rows':len(frame),'days':frame.delivery_date.nunique(),'max_actual_loss':frame.loss_normalized.max(),
                                  'loss_above_one_rows':int(frame.loss_normalized.gt(1).sum()),'natural_NR_rows':int(frame.observation_kind.astype(str).str.contains('natural_nr',case=False).sum()),
                                  'event_families':frame.event_family.value_counts().to_dict(),'observation_kinds':frame.observation_kind.value_counts().to_dict(),
                                  'prediction_status':'reused annual rolling OOF, all historical years previously examined','source_hashes':sources,
                                  'market_states':market.regime.value_counts().to_dict()})
        ledgers,summary=underwriting(frame,out)
        state_diagnostics(frame,ledgers,out)
        save(out/'completion.json',{'completed_utc':datetime.now(timezone.utc).isoformat(),'protocol_sha256':seal['protocol_sha256'],
                                  'local_computation_completed':True,'new_training_jobs':0,'production_changed':False,'actual_EV':'unknown'})
    except Exception as exc:
        import traceback
        save(out/'failure.json',{'error':str(exc),'traceback':traceback.format_exc()})
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--research',required=True);p.add_argument('--run-name',required=True)
    args=p.parse_args();run(args.root,args.research,args.run_name)
