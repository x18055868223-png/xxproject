from pathlib import Path
import sys
import numpy as np
import pandas as pd
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from astra_admission_v14 import admission_memberships, metrics, prepare_daily


def row(day,hour=8,score=.2,loss=.1):
    ts=pd.Timestamp(day,tz='UTC')+pd.Timedelta(hours=hour)
    return {'row_id':f'{day}:{hour}','observation_id':f'natural-clock-30m:{ts.value//1000000}',
        'side':'put_credit','delivery_date':(ts+pd.Timedelta(days=1)).strftime('%Y-%m-%d'),
        'actual_loss_normalized':loss,**{m+'_score':score for m in ('geometry','statistical','joint')}}


def test_only_exact08_no_future_daily_minimum():
    daily=prepare_daily(pd.DataFrame([row('2022-01-01'),row('2022-01-01',9,score=.001)]))
    assert len(daily)==1 and daily.iloc[0].geometry_score==.2


def test_prior_year_only_cut_ignores_evaluation_scores():
    daily=prepare_daily(pd.DataFrame([row('2022-01-01',score=.1),row('2022-01-02',score=.3),row('2023-01-01',score=9.)]))
    out,cuts=admission_memberships(daily,[2023],minimum_prior_days=2)
    assert cuts[0]['cut']==pytest.approx(.2)
    assert out.iloc[0].geometry_admit==0


def test_cut_year_map_and_no_pooled_history_fallback():
    daily=prepare_daily(pd.DataFrame([row('2022-01-01'),row('2022-01-02'),row('2023-01-01'),row('2024-01-01')]))
    out,cuts=admission_memberships(daily,[2024],minimum_prior_days=2)
    assert all(c['cut_source_year']==2023 and c['cut'] is None for c in cuts)
    assert out.geometry_admit.isna().all()


def test_cut_equal_is_admitted_without_label_tie_breaking():
    daily=prepare_daily(pd.DataFrame([row('2022-01-01',loss=9.),row('2023-01-01',loss=0.)]))
    out,_=admission_memberships(daily,[2023],minimum_prior_days=1)
    assert out.iloc[0].geometry_admit==1


def test_original_weights_keep_skipped_losses_and_real_tail():
    daily=prepare_daily(pd.DataFrame([row('2023-01-01',loss=0.),row('2023-01-02',loss=1.8)]))
    result=metrics(daily,[1,0])
    assert result['coverage']==.5
    assert result['admitted_loss_per_original_opportunity']==0
    assert result['skipped_loss_per_original_opportunity']==.9
    assert result['all_opportunity_mean_loss']==.9
    assert result['uncapped_over_one_rows']==1
    assert metrics(daily,[0,1])['admitted_es95_uncapped']==pytest.approx(1.8)


def test_unavailable_cut_is_not_silent_skip():
    daily=prepare_daily(pd.DataFrame([row('2023-01-01')]))
    assert metrics(daily,[np.nan])=={'available':False,'reason':'missing_prior_cut','rows':1}


def test_duplicate_same_side_day_fails():
    a=row('2023-01-01'); b=dict(a,row_id='another')
    with pytest.raises(ValueError,match='More than one'):
        prepare_daily(pd.DataFrame([a,b]))


@pytest.mark.parametrize('value',[np.inf,np.nan,-1])
def test_invalid_loss_fails(value):
    with pytest.raises(ValueError,match='Invalid saved'):
        prepare_daily(pd.DataFrame([row('2023-01-01',loss=value)]))


def test_no_admitted_days_is_not_zero_conditional_loss():
    daily=prepare_daily(pd.DataFrame([row('2023-01-01')]))
    result=metrics(daily,[0])
    assert result['conditional_admitted_loss'] is None
    assert result['admitted_es95_uncapped'] is None
