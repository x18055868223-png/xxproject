from astra_joint_v11_evaluation import evaluate,weighted_es

def row(day,put,call,stats='put_credit',joint='put_credit',original='call_credit'):
    return dict(delivery_date=day,preferences=dict(statistics=stats,joint=joint,original=original,fixed_put='put_credit',fixed_call='call_credit'),
        sides={s:dict(net_btc=x,win=x>0,protection_breached=x<-.5) for s,x in [('put',put),('call',call)]})

def test_filtering_and_changed_side_are_separate_not_selected_denominator_artifact():
    data=[row('2026-09-01',1,-1),row('2026-09-01',-3,1,joint='watch'),row('2026-09-02',-1,1,joint='call_credit')]
    r=evaluate(data,repetitions=100)
    assert r['policies']['statistics']['win_rate']==.25
    assert r['policies']['joint']['coverage']==.75
    assert r['policies']['joint']['win_rate']==1
    assert r['policies']['joint']['net_per_original_opportunity_btc']==.75
    assert r['selection_decomposition']['side_change_delta_pp']>0
    assert r['selection_decomposition']['filtering_delta_pp']>0
    assert r['policies']['equal_single_side']['win_rate']==.5
    assert r['policies']['equal_single_side']['coverage']==1
    assert r['complete_calendar_weeks'] is None and not r['data_sufficient']

def test_unconditional_tail_uses_all_weight_and_fractional_boundary():
    assert weighted_es([(10,.01),(0,.99)])==2
    r=evaluate([row('2026-09-01',1,-1,original='neutral')],repetitions=20)
    assert r['primary_comparisons']['statistics_minus_original']['paired_cards']==0
    assert r['policies']['original']['selected_cards']==0
    assert r['primary_comparisons']['joint_minus_statistics_selected']['delta_win_pp']==0

def test_data_sufficiency_uses_collection_health_not_result_dates():
    data=[row(f'2026-01-{day:02d}',1,-1) for day in range(1,29)]
    data += [row(f'2026-02-{day:02d}',1,-1) for day in range(1,29)]
    data += [row(f'2026-03-{day:02d}',1,-1) for day in range(1,6)]
    without_health=evaluate(data,repetitions=20,ended_week_blocks=12,complete_week_blocks=0,
                            collection_health_status='not_qualified')
    assert without_health['delivery_days']==61
    assert without_health['ended_7day_blocks']==12
    assert without_health['complete_calendar_weeks']==0
    assert not without_health['data_sufficient']
    with_health=evaluate(data,repetitions=20,ended_week_blocks=12,complete_week_blocks=12,
                         collection_health_status='qualified')
    assert with_health['data_sufficient']
