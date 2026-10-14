"""Current synthesis transport profile, private reasoning, and one-shot failure."""
import json

import pytest

from tools import astra_underwriting_review_v20 as review
from tools import astra_underwriting_store_v20 as store
from tools import astra_wpem_product_review_v1 as product
from tools import signal_llm_review as llm
from test_astra_bounded_claims_v244 import Budget
from test_astra_grounded_explanation import routed_fixture


@pytest.mark.parametrize('research_only', [False, True])
def test_new_candidate_profile_applies_to_both_call_modes(research_only):
    snapshot, evidence = routed_fixture()
    request = review.build_request(snapshot, 'deepseek-flash', research_only=research_only,
                                   wpem_evidence=evidence)
    wire = llm._strip_local_request_fields(request)
    assert request['_local_review_input']['schema'] == 'review_input@2.7.0'
    assert request['_local_prompt_version'] == 'evidence_synthesis_prompt@2.4-candidate'
    assert wire['model'] == 'deepseek-flash'
    assert wire['thinking'] == {'type': 'enabled'}
    assert wire['reasoning_effort'] == 'max'
    assert wire['max_tokens'] == 32768
    assert wire['response_format'] == {'type': 'json_object'}
    assert wire['stream'] is False
    assert request['_local_timeout_s'] == 300
    assert not {'temperature', 'top_p', 'presence_penalty', 'frequency_penalty'} & wire.keys()
    assert not any(key.startswith('_local_') for key in wire)
    assert json.loads(wire['messages'][1]['content'].split('\n本次证据：', 1)[1]) == request['_local_review_input']['evidence_input']


def test_runtime_sends_candidate_deadline_and_keeps_reasoning_private(tmp_path):
    snapshot, evidence = routed_fixture()
    ledger = store.UnderwritingLedger(tmp_path / 'ledger.jsonl')
    ledger.record_snapshot(snapshot)
    payload = {'response_protocol': 'evidence_points@2.6.4',
               'q1': [{'text_cn': '本单的不利方向由合同责任决定。', 'evidence_refs': ['contract']}],
               'q2': [{'text_cn': '冻结报价不是成交回执。', 'evidence_refs': ['quote']}]}
    marker = 'PRIVATE_TEST_REASONING_NOT_MARKET_EVIDENCE'
    response = {'model': 'deepseek-flash', 'choices': [{'finish_reason': 'stop', 'message': {
        'content': json.dumps(payload, ensure_ascii=False), 'reasoning_content': marker}}],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 20,
                  'completion_tokens_details': {'reasoning_tokens': 8}}}
    calls = []

    def transport(_key, model, request, timeout, endpoint=None):
        calls.append((model, request, timeout))
        return response

    result = review.run_review(snapshot, ledger, tmp_path / 'reviews', api_key='test-only',
                               model='deepseek-flash', budget=Budget(), transport=transport,
                               timeout=19, research_only=True, wpem_evidence=evidence)
    assert result['status'] == 'RESEARCH_OK'
    assert result['production_attach_allowed'] is False
    assert len(calls) == 1 and calls[0][2] == 300
    assert calls[0][1]['reasoning_effort'] == 'max'
    assert marker not in json.dumps(result)
    assert marker not in json.dumps(calls[0][1]['_local_review_input'])
    states = [json.loads(p.read_bytes()) for p in (tmp_path / 'reviews').rglob('*.json')]
    state = next(row for row in states if row.get('attempts'))
    assert state['attempts'][0]['raw_response'] == response
    assert state['attempts'][0]['timeout_s'] == 300
    assert marker not in json.dumps(state['result'])


def test_candidate_timeout_remains_single_shot_and_does_not_show_failed_text(tmp_path):
    snapshot, evidence = routed_fixture()
    ledger = store.UnderwritingLedger(tmp_path / 'ledger.jsonl')
    ledger.record_snapshot(snapshot)
    calls = []

    def timeout_transport(_key, _model, request, timeout, endpoint=None):
        calls.append(timeout)
        raise TimeoutError('controlled timeout')

    kwargs = dict(api_key='test-only', model='deepseek-flash', budget=Budget(),
                  transport=timeout_transport, research_only=True, wpem_evidence=evidence)
    result = review.run_review(snapshot, ledger, tmp_path / 'reviews', **kwargs)
    assert result['status'] == 'failed'
    assert result['http_calls'] == 1 and calls == [300]
    assert result['decision_trace']['points'] == []
    assert 'llm_explanation' not in result
    assert result['execution_allowed'] is False
    assert review.run_review(snapshot, ledger, tmp_path / 'reviews', **kwargs) == result
    assert calls == [300]
