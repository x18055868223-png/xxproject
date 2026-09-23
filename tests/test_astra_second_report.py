import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from astra_second_report import metrics,grade_table
import json
import pytest
from astra_second_report import build_report,sha

def row(card,side,pnl,margin,hours,grade):
    return {'card_id':card,'side':side,'net_pnl_btc':pnl,'margin_btc':margin,
        'dte_hours':hours,'entry_ms':1782000000000,'net_credit_btc':.002,
        'payout_btc':.002-pnl,'offline_grade':grade}

def test_capital_time_apr_not_average_individual_annual_returns():
    a=[row('1','put_credit',.002,.02,12,'A'),row('2','put_credit',-.001,.01,24,'B')]
    m=metrics(a)
    assert abs(m['apr'] - 365*.001/(.02*.5+.01))<1e-12
    assert m['mean_roi']==0
    assert m['win_rate']==.5 and m['intrusion_rate']==.5
    assert m['payoff_ratio']==2

def test_position_size_scaling_preserves_roi_and_apr():
    a=row('1','put_credit',.002,.02,12,'A')
    b=dict(a)
    for k in ('net_pnl_btc','margin_btc','net_credit_btc','payout_btc'):b[k]*=7
    assert metrics([a])['apr']==metrics([b])['apr']
    assert abs(metrics([a])['mean_roi']-metrics([b])['mean_roi'])<1e-12

def test_unrated_not_hidden_or_lumped_into_d():
    rows=[row('1','put_credit',.002,.02,12,'A'),row('2','put_credit',-.001,.02,12,None)]
    table=grade_table(rows)
    high=next(x for x in table if x['side']=='put_credit' and x['filter']=='A/S')
    assert high['coverage']==.5 and high['unselected']['losses']==1
    unrated=next(x for x in table if x['side']=='put_credit' and x['filter']=='未评级')
    assert unrated['selected']['n']==1
    assert next(x for x in table if x['side']=='put_credit' and x['filter']=='D')['selected']['n']==0

def test_empty_and_no_losses_do_not_report_infinite_payoff():
    assert metrics([])['win_rate'] is None
    assert metrics([row('1','call_credit',.002,.02,12,'S')])['payoff_ratio'] is None

def test_pending_ratings_cannot_be_joined_even_when_input_is_sealed(tmp_path):
    ratings=tmp_path/'ratings';ratings.mkdir()
    rows=[{'card_id':str(i),'side':s,'review_status':'PENDING'} for i in range(114) for s in ('put_credit','call_credit')]
    export=ratings/'ratings_by_side.jsonl'
    export.write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf-8')
    (ratings/'seal.json').write_text(json.dumps({'files':[{'path':export.name,'sha256':sha(export)}]}),encoding='utf-8')
    with pytest.raises(RuntimeError,match='all frozen cards must settle'):
        build_report(tmp_path/'study',ratings)
    assert not (tmp_path/'study/report').exists()
