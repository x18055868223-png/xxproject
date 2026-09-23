from astra_joint_v11_quality_audit import issues

def test_semantic_audit_does_not_discard_real_extremes():
    r=[1788220800000,80000,160000,40000,80000,1,1788220859999,80000,3,.5,40000]
    assert issues(r)==[]
    r[4]=170000
    assert issues(r)==['ohlc_containment']
    r[4]=80000;r[7]=-1
    assert 'negative_volume' in issues(r)
    r[7]=80000;r[6]+=1
    assert 'minute_time_shape' in issues(r)

def test_microsecond_timestamp_zero_and_missing_are_explicit():
    r=[1788220800000000,80000,80000,80000,80000,0,1788220859999999,0,0,0,0]
    assert issues(r)==[]
    r[10]=1
    assert 'zero_volume_inconsistent' in issues(r)
    assert issues(r[:6])==['missing_columns']

def test_partial_archive_minute_cannot_be_a_full_reference_minute():
    from astra_joint_data import HistoricalBars
    from astra_joint_v11_data import reference_spot_close
    start=1788220800000
    bar=dict(open_time_ms=start,close_time_ms=start+50000,open=80000,high=80000,low=80000,close=80000,volume=1)
    assert reference_spot_close(start+60000,HistoricalBars([bar])) is None
    bar['close_time_ms']=start+59999
    assert reference_spot_close(start+60000,HistoricalBars([bar]))['price']==80000
