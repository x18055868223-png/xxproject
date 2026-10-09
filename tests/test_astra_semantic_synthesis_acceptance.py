"""Current strict output contract and unchanged v2.5.5 request replay."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from tools import astra_underwriting_review_v20 as review
from tools import astra_underwriting_store_v20 as store
from tools import astra_wpem_product_review_v1 as product
from tools import signal_llm_review as llm
from test_astra_bounded_claims_v244 import Budget
from test_astra_grounded_explanation import routed_fixture


@pytest.mark.parametrize('name', ['semantic_followup', 'current_observation'])
def test_265_frozen_request_keeps_original_prompt_and_evidence(name):
    folder = (Path(__file__).resolve().parents[1] /
              '.artifacts/topology_realignment_20261006_01/samples' / name)
    if not folder.exists():
        pytest.skip('Immutable v2.5.5 real acceptance sample is unavailable')
    projection = json.loads((folder / 'review_input.json').read_bytes())
    snapshot = json.loads((folder / 'snapshot.json').read_bytes())
    original_request = json.loads((folder / 'actual_llm/request_http_body.bin').read_bytes())
    request = review.build_request_from_input(snapshot, original_request['model'], projection,
                                             research_only=True)
    assert projection['schema'] == product.ROUTING_REVIEW_INPUT_SCHEMA_265
    assert request['_local_prompt_version'] == 'evidence_synthesis_prompt@1.0.2'
    wire = llm._strip_local_request_fields(request)
    # Stored projection JSON sorts object keys; input meaning and prompt bytes
    # must survive reload even when evidence object insertion order differs.
    actual_prompt, actual_evidence = wire['messages'][1]['content'].split('\n本次证据：', 1)
    old_prompt, old_evidence = original_request['messages'][1]['content'].split('\n本次证据：', 1)
    assert actual_prompt == old_prompt
    assert json.loads(actual_evidence) == json.loads(old_evidence) == projection['evidence_input']
    wire['messages'][1]['content'] = original_request['messages'][1]['content']
    assert wire == original_request


def test_current_contract_only_sends_frozen_evidence_and_demands_exact_single_json():
    snapshot, evidence = routed_fixture()
    request = review.build_request(snapshot, 'local-test', research_only=True, wpem_evidence=evidence)
    frozen = request['_local_review_input']
    user_prompt = request['messages'][1]['content']
    assert frozen['schema'] == 'review_input@2.6.9'
    assert request['_local_prompt_version'] == 'evidence_synthesis_prompt@2.1-candidate'
    assert json.loads(user_prompt.split('\n本次证据：', 1)[1]) == frozen['evidence_input']
    assert 'HTTP response_format是传输配置，不是回答字段' in user_prompt
    assert request['response_format'] == {'type': 'json_object'}
    assert request['thinking'] == {'type': 'enabled'}
    assert request['reasoning_effort'] == 'max'
    assert frozen['request_contract']['exact_output_keys'] == ['response_protocol', 'q1', 'q2']


@pytest.mark.parametrize('sample,prompt', [('call_later','1.0.3'), ('put_later_v104','1.0.4'),
                                         ('call_early_v105','1.0.5')])
def test_failed_calls_keep_original_prompt_and_evidence(sample, prompt):
    folder = (Path(__file__).resolve().parents[1] /
              '.artifacts/semantic_synthesis_20261007_01/samples' / sample)
    if not (folder / 'actual_llm/request_http_body.bin').exists():
        pytest.skip('Immutable first semantic acceptance sample is unavailable')
    frozen = json.loads((folder / 'review_input.json').read_bytes())
    snapshot = json.loads((folder / 'snapshot.json').read_bytes())
    original = json.loads((folder / 'actual_llm/request_http_body.bin').read_bytes())
    request = review.build_request_from_input(snapshot, original['model'], frozen, research_only=True)
    assert request['_local_prompt_version'] == 'evidence_synthesis_prompt@' + prompt
    wire = llm._strip_local_request_fields(request)
    actual_prompt, actual_evidence = wire['messages'][1]['content'].split('\n本次证据：', 1)
    old_prompt, old_evidence = original['messages'][1]['content'].split('\n本次证据：', 1)
    assert actual_prompt == old_prompt
    assert json.loads(actual_evidence) == json.loads(old_evidence) == frozen['evidence_input']
    wire['messages'][1]['content'] = original['messages'][1]['content']
    assert wire == original


def test_cost_noncomparability_is_not_an_affirmative_quote_observation():
    caveat = '链上成本只按同源同日价格比较，相对实时报价不可比。'
    product._validate_topic_direct_refs(caveat, ['map.cost'])
    with pytest.raises(review.ReviewValidationError, match='direct evidence ref'):
        product._validate_topic_direct_refs(caveat + '本单报价已经下移。', ['map.cost'])
    product._validate_topic_direct_refs(caveat + '本单报价已经下移。', ['map.cost', 'quote'])


def test_funding_cost_background_is_not_option_fee():
    background = 'Funding窗口只作为费用背景。'
    product._validate_topic_direct_refs(background, ['funding'])
    with pytest.raises(review.ReviewValidationError, match='direct evidence ref'):
        product._validate_topic_direct_refs(background + '本单费用下降。', ['funding'])
    for statement in ('本单费用背景下降。', '期权费用背景下降。', '交割费用背景下降。'):
        with pytest.raises(review.ReviewValidationError, match='direct evidence ref'):
            product._validate_topic_direct_refs(background + statement, ['funding'])
    product._validate_topic_direct_refs(background + '本单费用下降。', ['funding', 'fee'])


@pytest.mark.parametrize('wrapper', ['markdown', 'repeated', 'trailing'])
def test_evidence_points_run_rejects_wrapped_json_once_and_keeps_raw(tmp_path, wrapper):
    snapshot, evidence = routed_fixture()
    ledger = store.UnderwritingLedger(tmp_path / 'ledger.jsonl')
    ledger.record_snapshot(snapshot)
    payload = {'response_protocol': product.EVIDENCE_POINTS_RESPONSE_PROTOCOL,
               'q1': [{'text_cn': '本单按合同不利方向理解风险。', 'evidence_refs': ['contract']}],
               'q2': [{'text_cn': '冻结报价不是成交回执。', 'evidence_refs': ['quote']}]}
    text = json.dumps(payload, ensure_ascii=False)
    content = {'markdown': '```json\n' + text + '\n```',
               'repeated': text + '\n' + text,
               'trailing': text + '\n以上是回答。'}[wrapper]
    calls = []

    def transport(*args, **kwargs):
        calls.append(args[2])
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]}

    kwargs = dict(api_key='test-only', model='local-test', budget=Budget(), transport=transport,
                  research_only=True, wpem_evidence=evidence)
    result = review.run_review(snapshot, ledger, tmp_path / 'reviews', **kwargs)
    assert result['status'] == 'failed'
    assert result['http_calls'] == len(calls) == 1
    assert result['execution_allowed'] is False
    assert result['decision_trace']['points'] == []
    assert 'llm_explanation' not in result
    assert review.run_review(snapshot, ledger, tmp_path / 'reviews', **kwargs) == result
    assert len(calls) == 1
    states = [json.loads(p.read_bytes()) for p in (tmp_path / 'reviews').rglob('*.json')]
    state = next(v for v in states if 'attempts' in v)
    assert state['attempts'][0]['raw_response']['choices'][0]['message']['content'] == content
    assert state['attempts'][0]['status'] == 'FORMAT_ERROR'


@pytest.mark.parametrize('rate,window_state', [
    (0.0, 'crowded_long'),
    (0.0003, 'neutral'),
    (None, 'crowded_long'),
    (0.0003, 'crowded_long'),
], ids=['F1_current_neutral_window_crowded', 'F2_current_high_window_neutral',
        'F3_current_missing_window_available', 'F4_current_window_same_sign'])
def test_funding_counterexamples_preserve_separate_values_and_clocks(rate, window_state):
    # Controlled scope counterexamples, not invented real observations. Current
    # input supplies a rate, not a newly computed current crowding classification.
    readings = {'last_rate': rate,
                'semantics': {'raw_funding_rate': 0.0001, 'crowding_state': 'NOT_CROWDED'},
                'tmvf_24h': {'funding_state': window_state, 'window_end_time': 1000}}
    original = deepcopy(readings)
    snapshot = {'fact_context': {'source_quality': {'source_details': {'binance': {
        'futures_facts': {'last_funding_rate': rate, 'time': 2000, 'next_funding_time': 3000}}}}}}
    item = {'readings': readings, 'asof_ms': 1000, 'source_observed_ms': 1000}
    current, current_asof, qualification, _, _ = product._funding_current_scope(snapshot, item)
    historical = product._funding_context_facts(readings, split_current=True)
    assert current == {'last_rate': rate}
    assert historical['tmvf_24h']['funding_state'] == window_state
    assert historical['tmvf_24h']['window_end_time'] == 1000
    assert 'last_rate' not in historical
    assert current_asof['source_time_ms'] == (None if rate is None else 2000)
    assert qualification['source_fields_match'] is (rate is not None)
    assert readings == original
    product._validate_topic_direct_refs('当前Funding与窗口Funding分别阅读。', ['funding.current', 'funding'])


def test_mismatched_funding_source_does_not_replace_reading_or_lend_its_clock():
    item = {'readings': {'last_rate': 0.0003}, 'asof_ms': 1000}
    snapshot = {'fact_context': {'source_quality': {'source_details': {'binance': {
        'futures_facts': {'last_funding_rate': -0.0003, 'time': 2000}}}}}}
    current, asof, qualification, limits, _ = product._funding_current_scope(snapshot, item)
    assert current == {'last_rate': 0.0003}
    assert asof['source_time_ms'] is None
    assert qualification['source_fields_match'] is False
    assert limits


def test_w1_current_level_and_quote_survive_missing_delta_and_path():
    snapshot, evidence = routed_fixture()
    frozen = product.build_review_input(snapshot, evidence, research_only=True, model='local-test')
    refs = {row['ref']: row for row in frozen['evidence_input']['evidence']}
    assert refs['wpem.level']['facts']['decision_asof_ms']
    assert refs['wpem.delta']['facts']['status'] == 'NO_STRICT_DELTA'
    assert refs['wpem.path']['facts']['status'] == 'NO_PATH_DUE_TO_WINDOW_GAP'
    assert refs['quote']['facts']['credit_btc'] is not None
    assert refs['fee']['facts']['amount_btc'] is not None
    assert frozen['evidence_input']['program_facts']['side'] == 'put'
