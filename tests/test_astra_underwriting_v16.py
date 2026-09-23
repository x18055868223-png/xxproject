import numpy as np
import pandas as pd
import pytest
from tools import astra_underwriting_v16 as u


def frame():
    return pd.DataFrame({'row_id':['a','b','c','d'], 'side':['put']*4,
                         'as_of_ms':[8*3600000,8*3600000+1800000,86400000+8*3600000,86400000+8*3600000+1800000],
                         'delivery_date':['2022-01-02']*2+['2022-01-03']*2,
                         'loss_normalized':[0.,2.,.1,.4], 'original_weight':[.5]*4})


def test_budget_and_unknown_days_are_not_renormalized():
    f=frame()
    s=u.schedule(f,'CLOCK30_BUDGET')
    assert s.groupby('delivery_date').original_weight.sum().tolist()==[1.,1.]
    assert len(u.schedule(f,'UTC08'))==2
    d=u.daily_sum(s,[1.,np.nan,2.,0.])
    assert np.isnan(d.iloc[0]) and d.iloc[1]==1.
    with pytest.raises(ValueError):u.schedule(pd.concat([f,f.iloc[:1]]),'UTC08')


def test_cash_identity_decline_is_zero_and_foregone_profit_is_counted():
    f=frame();credit=np.repeat(.3,4)
    m,a,pnl,one=u.policy_metrics(f,credit,.025,np.array([.4,.1,.1,.4]))
    assert a.tolist()==[False,True,True,False]
    assert np.allclose(pnl,[0.,-1.725,.175,0.])
    assert m['foregone_positive_pnl_per_original_weight']==pytest.approx(.275/4)
    assert m['avoided_negative_pnl_per_original_weight']==pytest.approx(.125/4)
    assert m['max_net_loss']>1  # inverse loss must survive
    all_m,all_a,all_pnl,_=u.policy_metrics(f,credit,.025,None)
    assert all_a.all()
    assert np.allclose(pnl-all_pnl,(a.astype(int)-1)*one)


def test_weighted_tail_uses_fractional_mass_and_full_net_credit_order():
    assert u.weighted_es([10.,1.],[.02,.98])==pytest.approx((.02*10+.03*1)/.05)
    hold=np.array([10.,9.]); exited=np.array([9.,9.5]);credit=np.array([9.,0.])
    assert u.weighted_es(exited,[1,1])<u.weighted_es(hold,[1,1])
    assert u.weighted_es(exited-credit,[1,1])>u.weighted_es(hold-credit,[1,1])
    assert np.mean(exited-hold)==np.mean((exited-credit)-(hold-credit))


def test_vector_quote_parity_expiry_and_invalid_input():
    put=u.bs_spread_btc('put',[100.,100.],[100.,100.],[90.,90.],[24.,24.],[.4,.8])
    call=u.bs_spread_btc('call',100.,90.,100.,24.,[.4,.8])
    assert np.allclose(put+call,.1)
    assert u.bs_spread_btc('put',80.,100.,90.,0.,.4)==pytest.approx(10/80)
    for vol in (0.,np.nan,-.1):
        with pytest.raises(ValueError):u.bs_spread_btc('put',100.,100.,90.,24.,vol)
    with pytest.raises(ValueError):u.bs_spread_btc('put',100.,90.,100.,24.,.4)


def test_unknown_price_stays_unknown_not_free_cash():
    f=frame();credit=np.array([.3,np.nan,.3,.3])
    m,a,pnl,_=u.policy_metrics(f,credit,.025,np.zeros(4))
    assert np.isnan(pnl[1]);assert not a[1]
    assert m['unknown_weight']==.5
    assert m['complete_delivery_days']==1
    assert m['incomplete_delivery_days']==1


def test_policy_scope_does_not_replace_statistical_predictions():
    f=frame();prediction=np.zeros(4)
    m,a,pnl,one=u.policy_metrics(f,np.full(4,.3),.025,prediction,eligible=[True,False,False,False])
    assert a.tolist()==[True,False,False,False]
    assert np.allclose(pnl,[.275,0,0,0])
    assert np.allclose(prediction,0)
    assert m['action_weight_fraction']==.25
    with pytest.raises(ValueError):u.policy_metrics(f,np.full(4,.3),.025,prediction,eligible=[True])


def test_calendar_blocks_do_not_change_when_date_rows_are_permuted():
    dates=pd.date_range('2022-01-01',periods=40).strftime('%Y-%m-%d')
    daily=pd.Series(np.arange(40)-20.,index=dates)
    a=u.block_interval(daily,repetitions=500)
    b=u.block_interval(daily.sample(frac=1,random_state=22),repetitions=500)
    assert a==b
    assert a['blocks']==6  # Jan 1 through Feb 9 intersects six epoch-anchored 7-day blocks.


def test_matching_distinguishes_global_bias_from_state_omission():
    days=pd.date_range('2022-01-01',periods=30).strftime('%Y-%m-%d')
    f=pd.DataFrame({'delivery_date':days,'evaluation_year':2022,'dte_hours':24.,'distance_to_width':.1,
                    'width_fraction':.03,'rv4_annualized':.6,'regime':['UP','DOWN','RANGE']*10,
                    'loss_normalized':.3,'mu_stat':.2,'original_weight':1.})
    d=u.controlled_residuals(f,'UP')
    assert d.notna().sum()==10
    assert np.allclose(d.dropna(),0.)
    f.loc[f.regime=='UP','loss_normalized']+=.05
    d=u.controlled_residuals(f,'UP')
    assert np.allclose(d.dropna(),.05)
    f.loc[f.regime=='UP','rv4_annualized']=1.5  # No same-volatility comparator, never infer a match.
    assert u.controlled_residuals(f,'UP').isna().all()


def test_small_run_keeps_source_receipt_and_all_policy_ledgers(tmp_path,monkeypatch):
    from tools import astra_regime_v16 as regime
    rows=[]
    for day in pd.date_range('2022-01-01',periods=2,tz='UTC'):
        asof=int((day+pd.Timedelta(hours=8)).timestamp()*1000)
        for side in ('put','call'):
            rows.append({'row_id':f'{asof}_{side}','side':side,'as_of_ms':asof,
                         'entry_ms':asof,'expiry_ms':asof+86400000,'delivery_date':str((day+pd.Timedelta(days=1)).date()),
                         'entry_price':100.,'short_strike':100.,'long_strike':90. if side=='put' else 110.,
                         'actual_width':10.,'dte_hours':24.,'distance_to_width':0.,'width_fraction':.1,
                         'mu_stat':.2,'mu_geom':.3,'loss_normalized':0.,'evaluation_year':2022,
                         'event_family':'natural_all_market_clock','observation_kind':'clock'})
    f=pd.DataFrame(rows)
    market=pd.DataFrame({'as_of_ms':f.as_of_ms.unique(),'regime':['UP','RANGE'],'rv4_annualized':[.5,.5]})
    market.attrs['source_hashes']={'um.parquet':'synthetichash'}
    monkeypatch.setattr(u,'load_original',lambda root:(f,{'base.csv':'basehash'}))
    monkeypatch.setattr(regime,'build_from_facts',lambda facts,times:market.copy())
    u.save(tmp_path/'protocol_v16.json',{'test':'synthetic'})
    u.save(tmp_path/'protocol_seal.json',{'protocol_sha256':u.digest(tmp_path/'protocol_v16.json')})
    u.run(tmp_path,tmp_path,'run_01')
    assert u.read(tmp_path/'run_01/completion.json')['new_training_jobs']==0
    assert u.read(tmp_path/'run_01/input_audit.json')['source_hashes']['um.parquet']=='synthetichash'
    assert len(list((tmp_path/'run_01').glob('entry_*.csv')))==10
    assert len(u.read(tmp_path/'run_01/L01_gate.json')['cells'])==6
