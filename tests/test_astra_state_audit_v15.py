"""Point-in-time and accounting tests for the frozen state audit."""
import numpy as np
import pandas as pd
import pytest
from tools import astra_state_audit_v15 as a

def test_split_purges_expiry_and_never_shares_delivery_days():
    f=pd.DataFrame({'entry_ms':[a.ms('2020-02-01'),a.ms('2021-09-30'),a.ms('2021-10-02'),a.ms('2021-12-31'),a.ms('2022-02-01')],
      'expiry_ms':[a.ms('2020-02-02'),a.ms('2021-10-01'),a.ms('2021-10-03'),a.ms('2022-01-01'),a.ms('2022-02-02')],
      'delivery_date':['2020-02-02','2021-10-01','2021-10-03','2022-01-01','2022-02-02']})
    fit,cal,ev=a.split_masks(f,2022)
    assert np.where(fit)[0].tolist()==[0]
    assert np.where(cal)[0].tolist()==[2]
    assert np.where(ev)[0].tolist()==[4]

def test_inverse_expiry_payoff_preserves_put_tail_above_one():
    # 100-dollar width / 200 entry; low settlement increases BTC-denominated loss.
    pay=a.bs_spread_btc('put',50.,200.,100.,0.,.6)
    assert pay==pytest.approx(2.)
    assert pay/(100/200)>1
    assert a.bs_spread_btc('call',250.,100.,200.,0.,.6)==pytest.approx(.4)

def test_bs_vertical_put_call_parity_same_strikes():
    spot=np.array([80.,100.,140.]); width=20.
    # Put high strike short, call low strike short cover complementary terminal regions.
    put=a.bs_spread_btc('put',spot,110.,90.,12.,.6)
    call=a.bs_spread_btc('call',spot,90.,110.,12.,.6)
    np.testing.assert_allclose(put+call,width/spot,atol=1e-12)
    assert (put>=0).all() and (call>=0).all()

def test_bs_cost_does_not_depend_on_terminal_outcome():
    cost1=a.bs_spread_btc('put',100.,100.,90.,8.,.6)
    cost2=a.bs_spread_btc('put',100.,100.,90.,8.,.6)
    assert cost1==cost2
    assert .1-cost1 != 1.8-cost2

def test_calibration_date_support_and_uncapped_mean():
    scale,receipt=a.calibrate([.1,.2],[0.,2.],np.ones(2),30)
    assert receipt['qualified']
    assert scale==pytest.approx((30*1.+30*.15)/(60*.15))
    assert a.calibrate([.1],[0.],np.ones(1),29)[1]['qualified'] is False

def test_day_weights_duplicate_rows_do_not_double_date_weight():
    f=pd.DataFrame({'delivery_date':['2022-01-01','2022-01-01','2022-01-02']})
    np.testing.assert_allclose(a.weights(f),[.5,.5,1.])
    assert a.average([2.,2.,0.],a.weights(f))==1.

def test_feature_allowlist_ignores_future_settlement_and_payout():
    market=pd.DataFrame({'as_of_ms':[1],**{k:[.01] for k in a.FIELDS}})
    frame=pd.DataFrame({'decision_ms':[1],'dte_hours':[12.],'distance_to_width':[.3],'shift_to_width':[.25],'width_fraction':[.05],
      'settlement_price':[1000.],'target':[100.]})
    states={'mix_p1':np.array([.2]),'hmm_p1':np.array([.3]),'hmm_reset_p1':np.array([.4])}
    first=a.task_features(frame,market,states,np.zeros((1,9)),'HMM2','structure')
    frame['target']=-1e8;frame['settlement_price']=1e8
    second=a.task_features(frame,market,states,np.zeros((1,9)),'HMM2','structure')
    np.testing.assert_array_equal(first,second)

def test_calendar_blocks_and_best_day_removal_direction():
    dates=pd.date_range('2022-01-01',periods=40).strftime('%Y-%m-%d')
    daily=pd.Series(np.r_[np.ones(10)*2,np.ones(30)*-.1],index=dates)
    r=a.comparison(daily,positive=True)
    assert r['mean']>0 and r['without10best']==pytest.approx(-.1)
    assert r['block_days']==7 and r['block14']['block_days']==14

def test_spot_adapter_cannot_use_um_price_when_sources_diverge():
    obs={'as_of_ms':a.STEP,'entry_price':101.,'last_closed_price':99.,'price_observation_ms':a.STEP-1,
         'spot_open_time_ms':a.STEP-60000,'last_closed_time_ms':a.STEP-1}
    position={'side':'put','entry_ms':0,'expiry_ms':2*a.STEP,'short_strike':100.,'actual_width':10.}
    legacy=a.ex.evaluate_exit_path(position,{a.STEP:obs},'SHORT_TOUCH_FIRST')
    corrected=a.ex.evaluate_exit_path(position,{a.STEP:a.spot_reference_observation(obs)},'SHORT_TOUCH_FIRST')
    assert legacy['path_status']=='triggered'
    assert corrected['path_status']=='not_triggered'
    assert obs['last_closed_price']==99. # archive never overwritten

def test_missing_or_stale_spot_does_not_fall_back_to_um():
    obs={'as_of_ms':a.STEP,'entry_price':None,'last_closed_price':99.,'price_observation_ms':a.STEP-1,
         'spot_open_time_ms':a.STEP-60000,'last_closed_time_ms':a.STEP-1}
    assert not a.ex.validate_spot_observation(a.spot_reference_observation(obs))[0]
    obs['entry_price']=101.;obs['price_observation_ms']=a.STEP-60001
    assert not a.ex.validate_spot_observation(a.spot_reference_observation(obs))[0]
