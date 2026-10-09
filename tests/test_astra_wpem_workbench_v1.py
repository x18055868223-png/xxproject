import json
import threading
from copy import deepcopy
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import pytest
from dataclasses import replace
from tools import astra_workbench_v20 as workbench
from tools import astra_wpem_product_review_v1 as product
from tools import astra_wpem_workbench_v1 as adapter
from test_astra_auditable_observation_pipeline import points
from test_astra_workbench_v20 import make_workbench, write_fmz, native_gate, T, positive_risk, fake_quote_capture


def wpem_workbench(tmp_path, transport=None, risk_provider=None, quote_capture=None):
    original, snapshots=make_workbench(tmp_path,risk_provider=risk_provider,transport=transport)
    wb=workbench.AstraWorkbench(replace(original.config,wpem_enabled=True),
        discovery_capture=original.discovery_capture,quote_capture=quote_capture or original.quote_capture,
        risk_provider=original.risk_provider,llm_transport=transport,clock_ms=original.clock_ms)
    write_fmz(snapshots,native_gate())
    return wb


def put_future_quote_capture(short_instrument, long_instrument, quantity_btc, *, now_ms):
    payload = fake_quote_capture(short_instrument, long_instrument, quantity_btc, now_ms=now_ms)
    if short_instrument.endswith("-P"):
        payload["market"]["long_book"]["timestamp"] = payload["market"]["valuation_ts_ms"] + 1
    return payload


def test_refresh_produces_compact_two_question_attribution_without_llm_or_gate_change(tmp_path):
    calls=[]
    wb=wpem_workbench(tmp_path,transport=lambda *a,**kw:calls.append(a))
    view=wb.refresh(now_ms=T)
    item=view['candidates'][0]
    assert item['current_questions']['source_kind']=='deterministic_program_no_llm'
    assert '预计赔付未量化' not in item['current_questions']['q2']['summary_cn']
    assert 'composition_inputs' not in item['wpem_evidence']
    assert 'source_bindings' not in item['wpem_evidence']
    assert wb.wpem_evidence(item['snapshot']['snapshot_id'])['composition_inputs']
    assert item['review_gate']['enabled'] is False
    assert item['review_validity']['status']=='NOT_RUN'
    before=wb.ledger.state()
    wb.cached_view();wb.wpem_evidence(item['snapshot']['snapshot_id'])
    assert calls==[] and wb.ledger.state()==before


def test_short_window_retains_observed_fragment_without_claiming_path():
    package={'same_candidate_window':{'status':'INSUFFICIENT','coverage_summary':{'windows':[
        {'point_count':3,'actual_coverage_ms':349209,'status':'INSUFFICIENT'},
        {'point_count':3,'actual_coverage_ms':349209,'status':'INSUFFICIENT'}]}}}
    text=adapter._observed_fragment(package)
    assert '3 个' in text and '5.82 分钟' in text and '不足以概括完整同窗路径' in text
    package['same_candidate_window']['status']='OBSERVED'
    assert adapter._observed_fragment(package) is None


def test_shared_source_opposing_facet_is_not_removed_as_duplicate():
    units=[{'evidence_id':key,'system_role':'CONSTRAINT','dependency_group':'OPTION_SPACE',
            'q1_role':role,'q1_reason_cn':key} for key,role in
           [('anchor','SUPPORT'),('anchor-reread','SUPPORT'),('ggr-limitation','LIMITATION')]]
    result=adapter._routed_question_observations({'evidence_routing':{'units':units}},'q1')
    assert result['routing_evidence_ids']==['anchor','ggr-limitation']


def test_existing_manual_gate_uses_wpem_protocol_and_exactly_one_frozen_call(tmp_path):
    calls=[]
    def transport(key,model,request,timeout,endpoint=None):
        calls.append(request)
        payload=points()
        return {'model':model,'choices':[{'finish_reason':'stop','message':{'content':json.dumps(payload)}}]}
    wb=wpem_workbench(tmp_path,transport=transport,risk_provider=positive_risk)
    view=wb.refresh(now_ms=T)
    item=view['candidates'][0]
    assert item['review_gate']['enabled']
    result=wb.review_candidate(item['snapshot']['candidate_id'],'wpem-one-click',authorization='Bearer secret')
    assert result['status']=='OK'
    assert result['review']['response_protocol']=='evidence_points@2.6.4'
    assert result['review']['wpem_evidence']['snapshot_id']==result['snapshot_id']
    assert len(calls)==1
    assert wb.review_candidate(item['snapshot']['candidate_id'],'wpem-one-click',authorization='Bearer secret')==result
    wb.cached_view()
    assert len(calls)==1


def test_public_summary_and_private_proof_do_not_refresh_or_call_llm(tmp_path):
    calls=[]
    wb=wpem_workbench(tmp_path,transport=lambda *a,**kw:calls.append(a))
    item=wb.refresh(now_ms=T)['candidates'][0]
    snapshot_id=item['snapshot']['snapshot_id']
    wb.discovery_capture=lambda **kw:pytest.fail('audit GET refreshed market')
    server=ThreadingHTTPServer(('127.0.0.1',0),workbench.make_handler(wb))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}/api/workbench/'
    try:
        with urlopen(base+'wpem-evidence/'+snapshot_id,timeout=3) as response:
            summary=json.load(response)
        assert 'composition_inputs' not in summary and 'source_bindings' not in summary
        with pytest.raises(HTTPError) as denied:
            urlopen(base+'wpem-proof/'+snapshot_id,timeout=3)
        assert denied.value.code==401
        with urlopen(Request(base+'wpem-proof/'+snapshot_id,headers={'Authorization':'Bearer secret'}),timeout=3) as response:
            proof=json.load(response)
        assert proof['composition_inputs'] and proof['evidence_hash']==summary['evidence_hash']
        assert calls==[]
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)


def test_wpem_archive_uses_explicit_config_budget(tmp_path):
    original,_=make_workbench(tmp_path)
    config=replace(original.config,wpem_enabled=True,research_max_bytes=7000,research_max_samples=7,
                   research_sample_interval_ms=12345)
    wb=workbench.AstraWorkbench(config)
    assert (wb._wpem_archive.max_bytes,wb._wpem_archive.max_samples,wb._wpem_archive.interval_ms)==(7000,7,12345)


def test_cached_get_expires_review_without_background_refresh(tmp_path):
    wb=wpem_workbench(tmp_path)
    item=wb.refresh(now_ms=T)['candidates'][0]
    snapshot=item['snapshot'];package=wb.wpem_evidence(snapshot['snapshot_id'])
    frozen=product.build_review_input(snapshot,package,model=wb.config.model)
    normalized=product.validate_wire_response(snapshot,points(),
                                              review_input=frozen,evidence=package)
    wb._current_view['candidates'][0]['review']={**normalized,'status':'OK'}
    assert wb.cached_view()['candidates'][0]['review_validity']['status']=='CURRENT'
    wb.clock_ms=lambda:T+6000
    assert wb.cached_view()['candidates'][0]['review_validity']['status']=='STALE'


def test_invalid_current_snapshot_blocks_program_questions_without_proof_or_financial_change(tmp_path):
    calls=[]
    wb=wpem_workbench(tmp_path,transport=lambda *a,**kw:calls.append(a),quote_capture=put_future_quote_capture)
    view=wb.refresh(now_ms=T)
    put=next(item for item in view['candidates'] if item['side']=='put')
    before_snapshot=json.dumps(put['snapshot'],ensure_ascii=False,sort_keys=True)
    before_gate=deepcopy(put['review_gate'])

    assert put['wpem_evidence']=={
        'status':'BLOCKED','reason':'QUOTE_CLOCK_NOT_REPLAYABLE','error_code':'QUOTE_CLOCK_NOT_REPLAYABLE'}
    assert put['review_validity']['status']=='NOT_RUN'
    assert put['review_validity']['reason']=='evidence_replay_failed'
    assert put['current_questions']['source_kind']=='deterministic_program_no_llm'
    assert '适配结论' in put['current_questions']['q1']['summary_cn']
    assert '当前可收信用' not in put['current_questions']['q2']['summary_cn']
    assert 'BTC' not in put['current_questions']['q2']['summary_cn']
    assert any('报价时钟' in item for item in put['wpem_summary']['breaks_cn'])
    assert not any('μ/M' in item or '风险参考缺失' in item for item in put['wpem_summary']['breaks_cn'])
    assert put['current_questions']['audit']['source_risk']==put['snapshot']['risk']
    assert put['current_questions']['q2']['observable_changes_cn']==[]
    assert json.dumps(put['snapshot'],ensure_ascii=False,sort_keys=True)==before_snapshot
    assert put['review_gate']==before_gate
    with pytest.raises(workbench.WorkbenchError) as missing:
        wb.wpem_evidence(put['snapshot']['snapshot_id'])
    assert missing.value.status_code==404

    put['review']={'status':'OK','response_protocol':product.RESPONSE_PROTOCOL}
    wb._attach_wpem(view,T)
    put=next(item for item in view['candidates'] if item['side']=='put')
    assert put['review_validity']['status']=='STALE'
    assert json.dumps(put['snapshot'],ensure_ascii=False,sort_keys=True)==before_snapshot
    assert calls==[]


def test_invalid_peer_clock_is_non_comparable_and_does_not_block_valid_call_proof(tmp_path):
    wb=wpem_workbench(tmp_path,quote_capture=put_future_quote_capture)
    view=wb.refresh(now_ms=T)
    put=next(item for item in view['candidates'] if item['side']=='put')
    call=next(item for item in view['candidates'] if item['side']=='call')

    assert put['wpem_evidence']['status']=='BLOCKED'
    assert 'evidence_hash' in call['wpem_evidence']
    proof=wb.wpem_evidence(call['snapshot']['snapshot_id'])
    assert proof['evidence_hash']==call['wpem_evidence']['evidence_hash']
    excluded=call['review_validity']['cross_section_excluded_peers']
    assert excluded==[{
        'error_code':'NON_COMPARABLE',
        'blocked_reason':'QUOTE_CLOCK_NOT_REPLAYABLE',
        'candidate_id':put['snapshot']['candidate_id'],
        'snapshot_id':put['snapshot']['snapshot_id'],
        'side':'put',
        'decision_asof_ms':put['snapshot']['market']['valuation_ts_ms'],
    }]

    server=ThreadingHTTPServer(('127.0.0.1',0),workbench.make_handler(wb))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}/api/workbench/'
    try:
        with pytest.raises(HTTPError) as denied:
            urlopen(Request(base+'wpem-proof/'+put['snapshot']['snapshot_id'],
                            headers={'Authorization':'Bearer secret'}),timeout=3)
        assert denied.value.code==404
        with urlopen(Request(base+'wpem-proof/'+call['snapshot']['snapshot_id'],
                             headers={'Authorization':'Bearer secret'}),timeout=3) as response:
            assert json.load(response)['evidence_hash']==proof['evidence_hash']
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
