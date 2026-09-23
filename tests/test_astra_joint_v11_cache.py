import astra_joint_shadow as shadow
from astra_joint_data import HistoricalBars
from test_astra_joint_shadow import kline,ms,MINUTE

def test_closed_only_cold_and_hot_cache_do_not_lose_last_closed_spot(tmp_path):
    at=ms(2026,9,15,0)
    ledger=shadow.Ledger(tmp_path)
    # Previous release's forming-only row is still present on upgrade.
    shadow._store_market_payload(ledger,'spot',[kline(at)],at+1000)
    assert ledger.bars('spot')[0]['forming_open_only']
    assert ledger.newest_closed_bar_open('spot',at+MINUTE) is None
    calls=[]
    def transport(url,params):
        calls.append(params)
        return [kline(at),kline(at+MINUTE)],{},b'fixture'
    shadow._fetch_market(ledger,tmp_path/'raw','spot',at+MINUTE+1000,(at+MINUTE)//MINUTE,transport,closed_only=True)
    rows=ledger.bars('spot')
    assert len(rows)==1 and not rows[0]['forming_open_only'] and rows[0]['close']==80001
    assert HistoricalBars(rows).closed_window(at+MINUTE+1000,1)
    assert calls[0]['startTime']<=at
    cold=shadow.Ledger(tmp_path/'cold')
    shadow._store_market_payload(cold,'spot',[kline(at),kline(at+MINUTE)],at+MINUTE+1000,closed_only=True)
    assert len(cold.bars('spot'))==1

def test_old_closed_row_is_never_rewritten_during_hot_upgrade(tmp_path):
    ledger=shadow.Ledger(tmp_path);at=ms(2026,9,15,0)
    shadow._store_market_payload(ledger,'spot',[kline(at)],at+MINUTE+1000,closed_only=True)
    import pytest
    with pytest.raises(ValueError,match='archive revision'):
        shadow._store_market_payload(ledger,'spot',[kline(at,price=90000)],at+MINUTE+2000,closed_only=True)
    assert ledger.bars('spot')[0]['close']==80001

def test_transport_success_is_not_closed_market_coverage(tmp_path):
    at=ms(2026,9,15,0)+MINUTE+1000
    for name,payload,expected in (
        ('missing',[], 'latest_closed_minute_missing'),
        ('invalid',{'error':'malformed'}, 'invalid_market_payload')):
        ledger=shadow.Ledger(tmp_path/name)
        result=shadow._fetch_market(ledger,tmp_path/name/'raw','um',at,at//MINUTE,
            lambda *args:(payload,{},b'fixture'),closed_only=True)
        assert result==(1,0)
        assert ledger.db.execute("SELECT status FROM requests WHERE market='binance_um'").fetchone()[0]==expected
        assert list((tmp_path/name/'raw').glob('*.json'))
