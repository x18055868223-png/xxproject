"""Existing single-call workflow with optional frozen, source-bound statistics."""
import copy
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import signal_review_v2 as review
import signal_review_v2_runtime as runtime
from signal_evidence_v2 import packet_hash
from astra_joint_bridge import BRIDGE_SCHEMA
from astra_joint_projection import compute_assessment_hash
from test_astra_joint_projection import make_assessment
from test_signal_review_v221_time import current_packet, card, payload_v22


def context_for_fixture():
    p = current_packet()
    a = make_assessment()
    a['event_id'] = p['identity']['card_id']
    a['provenance'].update(as_of_ms=p['identity']['as_of_ms'], source_record_hash=p['identity']['source_record_hash'])
    for s in a['sides'].values():
        s['reference']['expiry_ms'] = p['identity']['as_of_ms'] + 14*3600000
    a['assessment_hash'] = compute_assessment_hash(a)
    return dict(schema=BRIDGE_SCHEMA, available_at_ms=p['identity']['as_of_ms']+1000, assessment=a)


def test_one_request_context_frozen_retry_and_completed_card_immutable(tmp_path):
    p = current_packet(); frozen = packet_hash(p); context = context_for_fixture(); requests=[]
    def transport(*args, **kwargs):
        requests.append(args[2])
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(payload_v22())}}]}
    state = tmp_path/'state.json'
    rec, called = runtime._run_card(card(),p,state,'isolated-test',review.DEFAULT_MODEL,240,'unused',None,transport,None,context)
    assert called and len(requests)==1 and rec['llm_review']['status']=='OK'
    assert rec['llm_review']['statistical_context']==context
    assert packet_hash(p)==frozen
    assert context['assessment']['assessment_hash'] in json.dumps(requests[0])
    review.revalidate_review(card(),rec['llm_review'])
    before=state.read_bytes()
    rec2,called2=runtime._run_card(card(),p,state,None,review.DEFAULT_MODEL,240,'unused',None,transport,None,None)
    assert not called2 and rec2==rec and state.read_bytes()==before and len(requests)==1
    altered=copy.deepcopy(card());altered['identity']['source_record_hash']='sha256:'+'f'*64
    with pytest.raises(ValueError):review.revalidate_review(altered,rec['llm_review'])


def test_new_prompt_keeps_market_packet_and_four_output_objects():
    p=current_packet();a=review.build_request(p,review.DEFAULT_MODEL)
    b=review.build_request(p,review.DEFAULT_MODEL,statistical_context=context_for_fixture())
    assert len(a['messages'])==len(b['messages'])
    assert review.PROMPT_VERSION=='signal_llm_review_prompt@2.3.0'
    assert review.build_review(card(),payload_v22(),p)['schema_version']=='signal_llm_review@2.3.0'
