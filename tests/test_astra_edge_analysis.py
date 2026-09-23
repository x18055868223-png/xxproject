import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import astra_edge_analysis as a

def test_wall_equality_not_strict_beyond_and_mirrors():
    f={'asof_price':100,'put_wall':95,'call_wall':105,'gex_record_usable':True}
    for side,k,outside,inside in [('put_credit',95,94,96),('call_credit',105,106,104)]:
        assert a.factor(f,{'side':side,'short_strike':k},'wall_position')=='short_at_wall'
        assert a.factor(f,{'side':side,'short_strike':outside},'wall_position')=='short_beyond_wall'
        assert a.factor(f,{'side':side,'short_strike':inside},'wall_position')=='short_inside_wall'

def test_default_band_unknown_does_not_delete_valid_axis():
    f={'anchor_usable':True,'anchor_band_usable':False,'anchor_position_sign':1}
    assert a.factor(f,{'side':'put_credit','short_strike':90},'anchor_band_short')=='unknown'
    assert a.choice(f,'effective_Anchor')=='put_credit'

def test_missing_and_neutral_not_forced_side():
    assert a.choice({'direction':'NEUTRAL'},'original_EDB') is None
    assert a.choice({'flip_position_sign':None},'Flip') is None
    assert a.choice({'tmv_direction':'Bullish','tmv_data_ready':False},'TMVF_direction') is None

def test_readable_flow_explicitly_not_voting_replay():
    # Readable aggressive-flow direction is its own hypothesis; active EDB flags are retained separately.
    assert a.choice({'flow_direction':'bearish','flow_data_ready':True,'flow_edb_active':False},'active_flow_direction')=='call_credit'
