import json
from astra_joint_v11_forward import _collect,record_and_quote,settle,report,_connection,_dumps,_date,DAY,MINUTE,native_nr
from astra_joint_dataset import canonical_hash
from astra_joint_shadow import Ledger,_fetch_market
from test_astra_joint_projection import make_assessment, AS_OF_MS
from signal_review_v2 import build_review
from test_signal_review_v221_time import current_packet,card,payload_v22
from test_astra_joint_runtime import context_for_fixture

def choice():
    a=make_assessment()
    for side,s in a["sides"].items():
        r=s["reference"]
        for leg in ("short","long"):
            name=side+"_"+leg
            r[leg+"_name"]=name
            r[leg+"_contract"]=dict(instrument_name=name,strike=r[leg+"_strike"],creation_timestamp=AS_OF_MS-10000,
                expiration_timestamp=r["expiry_ms"],min_trade_amount=.1,taker_commission=.0003,settlement_period="day",
                kind='option',option_type=side,contract_size=1,base_currency='BTC',quote_currency='BTC',settlement_currency='BTC')
    return dict(card_id="card",as_of_ms=AS_OF_MS,opinion_completed_ms=AS_OF_MS+500,
                frozen_at_ms=AS_OF_MS+600,sides=a["sides"],status="available",
                review_hash="hash",episode_id="nr_test",statistical_preference="put_credit",
                joint_preference="watch",original_preference="call_credit")

def transport(at,calls):
    def get(url,params):
        calls.append(params["instrument_name"])
        return {"result":{"instrument_name":params['instrument_name'],"timestamp":at-1,"underlying_price":79000,"bids":[[.005,.3]],"asks":[[.001,.3]]}},{"fetched_at_ms":at},b"fixture"
    return get

def fill_market_requests(folder,start_ms,days=7,missing=None,incomplete=None):
    ledger=Ledger(folder);start=start_ms//MINUTE;end=(start_ms+days*DAY)//MINUTE
    rows=[]
    for minute in range(start,end):
        for market in ("binance_um","binance_spot"):
            if missing==(market,minute):
                continue
            status="TimeoutError" if incomplete==(market,minute) else "complete"
            rows.append((market,minute,"fixture",status,start_ms,start_ms if status=="complete" else None,
                         None if status=="complete" else "fixture error"))
    with ledger.db:
        ledger.db.executemany(
            "INSERT INTO requests(market,minute,request_hash,status,requested_at_ms,completed_at_ms,error) VALUES(?,?,?,?,?,?,?)",
            rows,
        )
    ledger.db.close()

def test_group_once_quantity_strict_and_replay_never_changes_choices(tmp_path):
    c=choice();now=AS_OF_MS+1000;calls=[]
    q=_collect(c,clock=lambda:now,transport=transport(now,calls))
    assert len(calls)==4 and q["strict"] and q["same_actual_width"]
    assert q["common_quantity"]==.1
    assert c["joint_preference"]=="watch"
    db=_connection(tmp_path)
    db.execute("INSERT INTO choices VALUES(?,?,?)",("card",_dumps(c),canonical_hash(c)))
    db.execute("INSERT INTO quotes VALUES(?,?,?)",("card","complete",_dumps(q)))
    db.commit();db.close()
    expiry=c["sides"]["put"]["reference"]["expiry_ms"]
    from datetime import datetime,timezone
    day=datetime.fromtimestamp(expiry/1000,timezone.utc).strftime("%Y-%m-%d")
    assert settle(tmp_path,{day:70000},expiry-1,{"kind":"official_deribit_delivery"})==0
    assert settle(tmp_path,{day:70000},expiry+1,{"kind":"official_deribit_delivery"})==1
    assert settle(tmp_path,{day:90000},expiry+2,{"kind":"official_deribit_delivery"})==0
    db=_connection(tmp_path)
    outcome=json.loads(db.execute("SELECT payload FROM outcomes WHERE id='card'").fetchone()[0])
    db.close()
    assert outcome["card_id"]=="card"
    assert outcome["choice_hash"]==canonical_hash(c)
    assert outcome["quote_hash"]==canonical_hash(q)
    result=report(tmp_path)
    assert result["policies"]["joint"]["selected_cards"]==0
    assert result["policies"]["statistics"]["selected_cards"]==1
    assert result["policies"]["fixed_call"]["win_rate"]==1

def test_delayed_opinion_does_not_request_and_depth_failure_preserved():
    c=choice();calls=[]
    q=_collect(c,clock=lambda:AS_OF_MS+61000,transport=transport(AS_OF_MS+61000,calls))
    assert not calls and q["reasons"]==["decision_quote_delayed"]
    now=AS_OF_MS+1000
    def shallow(url,p):
        payload,meta,raw=transport(now,calls)(url,p)
        payload["result"]["bids"][0][1]=.01
        return payload,meta,raw
    q=_collect(c,clock=lambda:now,transport=shallow)
    assert not q["strict"] and len(calls)==4 and "side_quote_unavailable" in q["reasons"]

def test_unequal_width_not_silently_primary_and_after_card_contract_rejected():
    c=choice();c["sides"]["put"]["reference"]["width"]=1500
    calls=[];now=AS_OF_MS+1000
    q=_collect(c,clock=lambda:now,transport=transport(now,calls))
    assert not q["same_actual_width"]
    c["sides"]["put"]["reference"]["short_contract"]["creation_timestamp"]=AS_OF_MS+1
    q=_collect(c,clock=lambda:now,transport=transport(now,calls))
    assert q["reasons"]==["instrument_created_after_card"]

def test_no_forward_window_no_http(tmp_path):
    c=card();c["identity"].update(event_type="NR_REPAIR_CONFIRMED",episode_id="nr_test")
    calls=[]
    assert record_and_quote(tmp_path,c,{},transport=transport(0,calls))["status"]=="outside_forward_window"
    assert calls==[]


def test_unrated_recommended_side_is_retained_as_abstention_without_altering_statistics():
    from astra_joint_v11_forward import freeze_choice
    from test_signal_review_v23 import spatial_packet, add_put_wall_distance, opinion
    p=add_put_wall_distance(spatial_packet(price=77350.3,put_wall=77000,call_wall=80000))
    payload=payload_v22()
    payload['joint_review']=opinion()
    payload['joint_review']['mechanism_verdict']='uncertain'
    payload['joint_review']['evidence_roles'][0]['ref']='structure.gamma.put_wall'
    payload['joint_review']['evidence_roles'][0]['claim_cn']='Put墙在现价下方，仅为位置参照。'
    payload['side_evidence_ratings']['put_credit']['evidence_roles'][0].update(
        ref='structure.distance.put_wall_pct',claim_cn='现价上方较近处存在下方Put墙参照。')
    r=build_review(card(),payload,p,statistical_context=context_for_fixture())
    assert r['integrated_trade_advisory']['joint_review']['status']=='ASSESSED'
    c=freeze_choice(card(),r,AS_OF_MS+1000)
    assert c['raw_joint_preference']=='put_credit'
    assert c['joint_preference']=='insufficient'
    assert c['joint_qualification_reasons']==['recommended_side_not_locally_qualified']
    assert c['joint_opinion']['recommendation']=='put_credit'
    from signal_review_joint import statistical_ranking
    assert c['statistical_preference']==statistical_ranking(r['statistical_context'])
    assert c['sides']==r['statistical_context']['assessment']['sides']

def test_expiry_crossed_during_review_does_not_fetch_useless_quotes():
    c=choice();calls=[]
    for side in c['sides'].values():side['reference']['expiry_ms']=AS_OF_MS+8*3600000
    q=_collect(c,clock=lambda:AS_OF_MS+1000,transport=transport(AS_OF_MS+1000,calls))
    assert q['reasons']==['expiry_outside_research_window'] and calls==[]

def test_wrong_currency_or_book_identity_cannot_enter_strict_results():
    c=choice();calls=[];now=AS_OF_MS+1000
    c['sides']['put']['reference']['short_contract']['settlement_currency']='USDC'
    q=_collect(c,clock=lambda:now,transport=transport(now,calls))
    assert q['reasons']==['instrument_unit_or_geometry_unverified'] and not calls
    def wrong(url,params):
        p,m,r=transport(now,calls)(url,params);p['result']['instrument_name']='OTHER';return p,m,r
    q=_collect(choice(),clock=lambda:now,transport=wrong)
    assert not q['strict'] and 'book_instrument_identity_mismatch' in q['reasons']

def test_common_quote_restart_reservation_is_not_an_extra_allowance(tmp_path,monkeypatch):
    import astra_joint_v11_forward as forward
    c=choice();review={'fixture':True};c['review_hash']=canonical_hash(review)
    card_={'identity':{'card_id':'card','confirmed_time_ms':AS_OF_MS,'event_type':'NR_REPAIR_CONFIRMED','episode_id':'nr_test'}}
    ledger=Ledger(tmp_path);ledger.window(AS_OF_MS-1000);ledger.db.close()
    monkeypatch.setattr(forward,'freeze_choice',lambda *args:c)
    calls=[];now=AS_OF_MS+1000
    first=record_and_quote(tmp_path,card_,review,clock=lambda:now,transport=transport(now,calls))
    second=record_and_quote(tmp_path,card_,review,clock=lambda:now+100,transport=transport(now,calls))
    assert first['http_attempts']==4 and second['http_attempts']==0 and len(calls)==4
    db=_connection(tmp_path)
    db.execute("UPDATE quotes SET status='reserved',payload=NULL WHERE id='card'");db.commit();db.close()
    third=record_and_quote(tmp_path,card_,review,clock=lambda:now+200,transport=transport(now,calls))
    assert third['status']=='reserved' and third['http_attempts']==0 and len(calls)==4
    expiry=c['sides']['put']['reference']['expiry_ms']
    from datetime import datetime,timezone
    day=datetime.fromtimestamp(expiry/1000,timezone.utc).strftime('%Y-%m-%d')
    assert settle(tmp_path,{day:80000},expiry+1,{'kind':'official_deribit_delivery'})==0
    assert report(tmp_path)['failure_buckets']['interrupted_reserved_no_retry']==1

def test_existing_choice_hash_mismatch_blocks_requote_and_report(tmp_path):
    c=choice();review={'fixture':True};c['review_hash']=canonical_hash(review)
    card_={'identity':{'card_id':'card','confirmed_time_ms':AS_OF_MS,
                       'event_type':'NR_REPAIR_CONFIRMED','episode_id':'nr_test'}}
    ledger=Ledger(tmp_path);ledger.window(AS_OF_MS-1000);ledger.db.close()
    db=_connection(tmp_path)
    with db:
        db.execute("INSERT INTO choices VALUES(?,?,?)",("card",_dumps(c),"stale_hash"))
    db.close()
    calls=[]
    result=record_and_quote(tmp_path,card_,review,clock=lambda:AS_OF_MS+1000,transport=transport(AS_OF_MS+1000,calls))
    assert result["status"]=="integrity_gap"
    assert result["http_attempts"]==0 and calls==[]
    summary=report(tmp_path)
    assert summary["coverage"]["integrity_gap"]==1
    assert summary["failure_buckets"]["integrity_choice_hash_mismatch"]==1
    assert summary["coverage"].get("primary_pairs",0)==0

def test_quote_source_hash_mismatch_blocks_settle_and_report(tmp_path):
    c=choice();now=AS_OF_MS+1000;calls=[]
    q=_collect(c,clock=lambda:now,transport=transport(now,calls))
    q["source_choice_hash"]="other_choice"
    db=_connection(tmp_path)
    with db:
        db.execute("INSERT INTO choices VALUES(?,?,?)",("card",_dumps(c),canonical_hash(c)))
        db.execute("INSERT INTO quotes VALUES(?,?,?)",("card","complete",_dumps(q)))
    db.close()
    expiry=c["sides"]["put"]["reference"]["expiry_ms"];day=_date(expiry)
    assert settle(tmp_path,{day:70000},expiry+1,{"kind":"official_deribit_delivery"})==0
    summary=report(tmp_path)
    assert summary["coverage"]["integrity_gap"]==1
    assert summary["failure_buckets"]["integrity_quote_choice_hash_mismatch"]==1
    assert summary["coverage"].get("primary_pairs",0)==0

def test_outcome_hash_date_and_identity_mismatch_do_not_enter_primary(tmp_path):
    c=choice();now=AS_OF_MS+1000;calls=[]
    q=_collect(c,clock=lambda:now,transport=transport(now,calls))
    bad_outcome={"card_id":"other_card","choice_hash":"bad_choice","quote_hash":"bad_quote",
                 "delivery_date":"2099-01-01","strict":True,"same_actual_width":True,
                 "sides":{"put":{"net_btc":1,"win":True,"protection_breached":False},
                          "call":{"net_btc":1,"win":True,"protection_breached":False}}}
    db=_connection(tmp_path)
    with db:
        db.execute("INSERT INTO choices VALUES(?,?,?)",("card",_dumps(c),canonical_hash(c)))
        db.execute("INSERT INTO quotes VALUES(?,?,?)",("card","complete",_dumps(q)))
        db.execute("INSERT INTO outcomes VALUES(?,?)",("card",_dumps(bad_outcome)))
    db.close()
    summary=report(tmp_path)
    assert summary["coverage"]["integrity_gap"]==1
    assert summary["failure_buckets"]["integrity_outcome_identity_mismatch"]==1
    assert summary["failure_buckets"]["integrity_outcome_choice_hash_mismatch"]==1
    assert summary["failure_buckets"]["integrity_outcome_quote_hash_mismatch"]==1
    assert summary["failure_buckets"]["integrity_outcome_delivery_date_mismatch"]==1
    assert summary["coverage"].get("primary_pairs",0)==0


def test_reserved_quote_cannot_authorize_a_matching_outcome(tmp_path):
    c=choice();q={'strict':True,'source_choice_hash':canonical_hash(c)}
    o={'card_id':'card','choice_hash':canonical_hash(c),'quote_hash':canonical_hash(q),
       'delivery_date':_date(c['sides']['put']['reference']['expiry_ms']),
       'strict':True,'same_actual_width':True,'sides':{}}
    db=_connection(tmp_path)
    with db:
        db.execute('INSERT INTO choices VALUES(?,?,?)',('card',_dumps(c),canonical_hash(c)))
        db.execute('INSERT INTO quotes VALUES(?,?,?)',('card','reserved',_dumps(q)))
        db.execute('INSERT INTO outcomes VALUES(?,?)',('card',_dumps(o)))
    db.close()
    r=report(tmp_path)
    assert r['failure_buckets']['integrity_outcome_quote_status_mismatch']==1
    assert r['coverage'].get('primary_pairs',0)==0


def test_regular_health_publication_does_not_run_performance(tmp_path,monkeypatch):
    import astra_joint_v11_evaluation as evaluation
    def prohibited(*args,**kwargs):raise AssertionError('health must not evaluate performance')
    monkeypatch.setattr(evaluation,'evaluate',prohibited)
    r=report(tmp_path,performance=False)
    assert not {'policies','paired_evaluation','paired_rows'} & r.keys()
    import pathlib
    source=(pathlib.Path(__file__).resolve().parents[1]/'tools/astra_joint_v11_forward.py').read_text('utf-8')
    assert 'else report(a.folder,performance=False)' in source


def test_outcome_cannot_upgrade_quote_strictness_or_width(tmp_path):
    for changed in ('strict','same_actual_width'):
        folder=tmp_path/changed;c=choice()
        q={'strict':True,'same_actual_width':True,'source_choice_hash':canonical_hash(c)}
        q[changed]=False
        o={'card_id':'card','choice_hash':canonical_hash(c),'quote_hash':canonical_hash(q),
           'delivery_date':_date(c['sides']['put']['reference']['expiry_ms']),
           'strict':True,'same_actual_width':True,'sides':{}}
        db=_connection(folder)
        with db:
            db.execute('INSERT INTO choices VALUES(?,?,?)',('card',_dumps(c),canonical_hash(c)))
            db.execute('INSERT INTO quotes VALUES(?,?,?)',('card','complete',_dumps(q)))
            db.execute('INSERT INTO outcomes VALUES(?,?)',('card',_dumps(o)))
        db.close();r=report(folder)
        reason='integrity_outcome_strict_mismatch' if changed=='strict' else 'integrity_outcome_width_mismatch'
        assert r['failure_buckets'][reason]==1
        assert r['coverage'].get('primary_pairs',0)==0

def test_milestones_do_not_start_early_or_expose_day30_performance(tmp_path):
    from astra_joint_v11_forward import seal_milestone
    import pytest
    ledger=Ledger(tmp_path);window=ledger.window(AS_OF_MS);ledger.db.close()
    with pytest.raises(ValueError):seal_milestone(tmp_path,'day30',clock=lambda:AS_OF_MS)
    health=seal_milestone(tmp_path,'day30',clock=lambda:window['quality_check_ms'])
    assert 'policies' not in health and 'paired_evaluation' not in health
    assert health["collection_health"]["ended_7day_blocks"]==4
    assert health["collection_health"]["healthy_complete_7day_blocks"]==0
    with pytest.raises(ValueError):seal_milestone(tmp_path,'final',clock=lambda:window['end_ms']-1)
    final=seal_milestone(tmp_path,'final',clock=lambda:window['end_ms']+1)
    assert not final['paired_evaluation']['data_sufficient']
    assert seal_milestone(tmp_path,'final',clock=lambda:window['end_ms']+100000)==final

def test_original_direction_is_not_episode_shock_direction():
    from astra_joint_v11_forward import _original_side
    for lean in ('BULLISH_WEAK','BULLISH_STRONG'):
        assert _original_side({'decision':{'lean':lean},'episode_direction':'DOWN'})=='put_credit'
    assert _original_side({'decision':{'lean':'BEARISH_WEAK'},'episode_direction':'UP'})=='call_credit'
    assert _original_side({'decision':{'lean':'NEUTRAL'},'episode_direction':'UP'})=='neutral'

def test_string_false_does_not_mark_native_nr_synthetic():
    card_={'identity':{'event_type':'NR_REPAIR_CONFIRMED','episode_id':'nr_test','is_synthetic':'false'},
           'analysis_round':'false'}
    assert native_nr(card_)
    assert not native_nr({**card_,'identity':{**card_['identity'],'is_synthetic':'true'}})
    assert not native_nr({**card_,'analysis_round':'true'})

def test_request_gap_prevents_healthy_complete_week_without_using_results(tmp_path):
    ledger=Ledger(tmp_path);window=ledger.window(AS_OF_MS);ledger.db.close()
    missing=("binance_spot",window["start_ms"]//MINUTE+10)
    fill_market_requests(tmp_path,window["start_ms"],missing=missing)
    result=report(tmp_path,clock=lambda:window["start_ms"]+7*DAY)
    health=result["collection_health"]
    assert health["ended_7day_blocks"]==1
    assert health["healthy_complete_7day_blocks"]==0
    assert health["blocks"][0]["market_status"]["binance_spot"]["missing_requests"]==1
    assert result["paired_evaluation"]["complete_calendar_weeks"]==0
    assert not result["paired_evaluation"]["data_sufficient"]

def test_collection_health_uses_real_fetch_market_request_names(tmp_path):
    ledger=Ledger(tmp_path);window=ledger.window(AS_OF_MS);raw=tmp_path/"raw"
    at=window["start_ms"]+2*MINUTE;minute=at//MINUTE
    closed_open=(minute-1)*MINUTE
    def kline(open_ms):
        return [open_ms,"100","101","99","100.5","1",open_ms+MINUTE-1,"100.5","1","0.6","60","0"]
    def market_transport(url,params):
        return [kline(closed_open)],{"fetched_at_ms":at},json.dumps([kline(closed_open)]).encode()
    assert _fetch_market(ledger,raw,"um",at,minute,market_transport,closed_only=True)==(1,1)
    assert _fetch_market(ledger,raw,"spot",at,minute,market_transport,closed_only=True)==(1,1)
    markets={row[0] for row in ledger.db.execute("SELECT market FROM requests")}
    ledger.db.close()
    assert markets=={"binance_um","binance_spot"}
    result=report(tmp_path,clock=lambda:window["start_ms"]+7*DAY)
    block=result["collection_health"]["blocks"][0]
    assert block["market_status"]["binance_um"]["completed_requests"]==1
    assert block["market_status"]["binance_spot"]["completed_requests"]==1
    assert result["collection_health"]["healthy_complete_7day_blocks"]==0

def test_no_nr_week_can_be_collection_healthy_but_not_effective_sample(tmp_path):
    ledger=Ledger(tmp_path);window=ledger.window(AS_OF_MS);ledger.db.close()
    fill_market_requests(tmp_path,window["start_ms"])
    result=report(tmp_path,clock=lambda:window["start_ms"]+7*DAY)
    assert result["total_cards"]==0
    assert result["collection_health"]["healthy_complete_7day_blocks"]==1
    assert result["paired_evaluation"]["delivery_days"]==0
    assert result["paired_evaluation"]["complete_calendar_weeks"]==1
    assert not result["paired_evaluation"]["data_sufficient"]

def test_intraminute_window_start_does_not_require_pre_start_request(tmp_path):
    start=AS_OF_MS+1234
    ledger=Ledger(tmp_path);ledger.window(start);ledger.db.close()
    fill_market_requests(tmp_path,(start//MINUTE+1)*MINUTE)
    health=report(tmp_path,clock=lambda:start+7*DAY)['collection_health']
    assert health['healthy_complete_7day_blocks']==1
    assert health['blocks'][0]['market_status']['binance_um']['expected_minutes']==10079

def test_single_settled_trade_does_not_create_complete_week(tmp_path):
    ledger=Ledger(tmp_path);window=ledger.window(AS_OF_MS);ledger.db.close()
    c=choice()
    o={"strict":True,"same_actual_width":True,"delivery_date":"2026-09-01",
       "sides":{"put":{"net_btc":1,"win":True,"protection_breached":False},
                "call":{"net_btc":-1,"win":False,"protection_breached":True}}}
    db=_connection(tmp_path)
    with db:
        q={"strict":True,"same_actual_width":True,"reasons":[],"source_choice_hash":canonical_hash(c)}
        o.update(card_id="card",choice_hash=canonical_hash(c),quote_hash=canonical_hash(q),
                 delivery_date=_date(c["sides"]["put"]["reference"]["expiry_ms"]))
        db.execute("INSERT INTO choices VALUES(?,?,?)",("card",_dumps(c),canonical_hash(c)))
        db.execute("INSERT INTO quotes VALUES(?,?,?)",("card","complete",_dumps(q)))
        db.execute("INSERT INTO outcomes VALUES(?,?)",("card",_dumps(o)))
    db.close()
    result=report(tmp_path,clock=lambda:window["start_ms"]+7*DAY)
    assert result["coverage"]["primary_pairs"]==1
    assert result["paired_evaluation"]["delivery_days"]==1
    assert result["collection_health"]["ended_7day_blocks"]==1
    assert result["collection_health"]["healthy_complete_7day_blocks"]==0
    assert result["paired_evaluation"]["complete_calendar_weeks"]==0
    assert not result["paired_evaluation"]["data_sufficient"]
