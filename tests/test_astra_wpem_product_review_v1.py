import inspect
import json
from copy import deepcopy
import pytest
from tools import astra_underwriting_v20 as core
from tools import astra_underwriting_review_v20 as legacy
from tools import astra_wpem_review_evidence_v1 as evidence
from tools import astra_wpem_product_review_v1 as product
from tools import astra_underwriting_store_v20 as store
from test_astra_underwriting_v20 import sample
from test_astra_bounded_claims_v244 import Budget


def _build_legacy_review_evidence(snapshot):
    if "include_routing" in inspect.signature(evidence.build_review_evidence).parameters:
        return evidence.build_review_evidence(snapshot, include_routing=False)
    return evidence.build_review_evidence(snapshot)


def fixture():
    snapshot = core.evaluate(sample('put'))
    package = _build_legacy_review_evidence(snapshot)
    return snapshot, package


def _reseal_evidence(row):
    body = {k: deepcopy(v) for k, v in row.items() if k not in {"evidence_hash", "review_evidence_hash"}}
    digest = legacy._hash(body)
    body["evidence_hash"] = digest
    body["review_evidence_hash"] = digest
    return body


def _legacy_evidence_without_coverage_summary(package):
    old = deepcopy(package)
    old["schema"] = "wpem_review_evidence@1.0.0"
    old["same_candidate_window"]["schema"] = "wpem_review_same_candidate_window@1.0.0"
    old["same_candidate_window"].pop("coverage_summary", None)
    return _reseal_evidence(old)


def _retarget_review_input_schema(review_input, schema, *, drop_request_contract=False):
    old = deepcopy(review_input)
    if drop_request_contract:
        old.pop("request_contract", None)
    old["schema"] = schema
    old["projection_hash"] = legacy._hash({key: value for key, value in old.items() if key != "projection_hash"})
    return old


def test_canonical_input_preserves_legacy_account_and_explicit_window_gap():
    snapshot, package = fixture()
    old = legacy.build_review_input(snapshot)
    current = legacy.build_review_input(snapshot, wpem_evidence=package)
    assert old['schema'] == 'review_input@2.4.7'
    assert current['schema'] == product.REVIEW_INPUT_SCHEMA
    assert current['deterministic_financials'] == old['deterministic_financials']
    assert current['deterministic_account'] == old['deterministic_account']
    assert current['wpem']['same_candidate_window']['status'] == 'INSUFFICIENT'
    assert current['wpem']['same_candidate_window']['coverage_summary'] == (
        package['same_candidate_window']['coverage_summary']
    )
    assert 'composition_inputs' not in current['wpem']
    assert 'source_bindings' not in current['wpem']
    assert old['projection_hash'] != current['projection_hash']


def test_coverage_summary_reaches_actual_request_prompt_without_gate_or_financial_change():
    snapshot, package = fixture()
    request = legacy.build_request(snapshot, 'local-test-model', wpem_evidence=package)
    current = request['_local_review_input']
    old = legacy.build_review_input(snapshot)
    frozen_line = request['messages'][1]['content'].splitlines()[-1]
    frozen = json.loads(frozen_line[frozen_line.index('{'):])

    assert product.RESPONSE_PROTOCOL == 'bounded_claim_ids@2.5.0'
    assert current['schema'] == 'review_input@2.5.2'
    assert current['response_protocol'] == product.RESPONSE_PROTOCOL
    assert current['request_contract'] == {
        'prompt_version': 'wpem_bounded_prompt@1.0.1',
        'provider_config_version': 'wpem_json_delivery@1.0.1',
        'selection_authority': 'claim_catalog',
        'nested_catalogs_selectable': False,
        'selectable_catalog_hash': current['claim_catalog_hash'],
        'output_protocol': product.RESPONSE_PROTOCOL,
        'exact_output_keys': [
            'response_protocol',
            'reviewed_snapshot_id',
            'projection_hash',
            'claim_catalog_hash',
            'applicability',
            'selected_claim_ids',
            'selected_counter_ids',
            'selected_gap_ids',
            'selected_reassess_condition_ids',
        ],
    }
    assert current['wpem']['same_candidate_window']['coverage_summary'] == (
        package['same_candidate_window']['coverage_summary']
    )
    assert frozen['wpem']['same_candidate_window']['coverage_summary'] == (
        package['same_candidate_window']['coverage_summary']
    )
    assert current['deterministic_financials'] == old['deterministic_financials']
    assert current['deterministic_account'] == old['deterministic_account']
    assert package['validity']['review_gate_changed'] is False
    assert package['validity']['financial_gate_changed'] is False
    assert package['execution_allowed'] is False
    assert package['llm_http_calls'] == package['inference_calls'] == 0


def test_canonical_request_and_response_replay_use_frozen_wpem():
    snapshot, package = fixture()
    request = legacy.build_request(snapshot, 'local-test-model', wpem_evidence=package)
    current = request['_local_review_input']
    assert product.RESPONSE_PROTOCOL in request['messages'][1]['content']
    answer = product.required_selection_payload(current)
    assert answer['response_protocol'] == current['response_protocol']
    result = legacy.validate_wire_response(snapshot, answer, review_input=current, wpem_evidence=package)
    assert result['response_protocol'] == product.RESPONSE_PROTOCOL
    assert result['wpem_evidence'] == package
    assert result['q1']['window_attribution_cn']
    assert result['q2']['window_tradeoff_cn']
    assert result['deterministic_financials'] == legacy.build_review_input(snapshot)['deterministic_financials']
    missing = deepcopy(answer)
    missing['selected_claim_ids'].remove('claim.wpem.current_attribution')
    with pytest.raises(legacy.ReviewValidationError, match='required evidence'):
        legacy.validate_wire_response(snapshot, missing, review_input=current, wpem_evidence=package)
    tampered = deepcopy(current)
    tampered['wpem']['interpretation']['path_cn'] = '伪造支持'
    with pytest.raises(legacy.ReviewValidationError, match='replay mismatch'):
        legacy.validate_wire_response(snapshot, answer, review_input=tampered, wpem_evidence=package)


def test_legacy_250_review_input_can_be_replayed_without_upgrading_shape():
    snapshot, package = fixture()
    old_package = _legacy_evidence_without_coverage_summary(package)
    current = product.build_review_input(snapshot, old_package)
    old_input = _retarget_review_input_schema(current, 'review_input@2.5.0', drop_request_contract=True)
    answer = product.required_selection_payload(old_input)

    result = legacy.validate_wire_response(snapshot, answer, review_input=old_input, wpem_evidence=old_package)

    assert old_package['schema'] == 'wpem_review_evidence@1.0.0'
    assert old_input['schema'] == 'review_input@2.5.0'
    assert 'request_contract' not in old_input
    assert 'coverage_summary' not in old_input['wpem']['same_candidate_window']
    assert result['response_protocol'] == product.RESPONSE_PROTOCOL
    assert result['review_validity']['review_input_version'] == 'review_input@2.5.0'
    assert result['review_validity']['projection_hash'] == old_input['projection_hash']
    assert result['wpem_evidence'] == old_package


def test_legacy_251_review_input_can_be_replayed_without_request_contract():
    snapshot, package = fixture()
    current = product.build_review_input(snapshot, package)
    old_input = _retarget_review_input_schema(current, 'review_input@2.5.1', drop_request_contract=True)
    answer = product.required_selection_payload(old_input)

    result = legacy.validate_wire_response(snapshot, answer, review_input=old_input, wpem_evidence=package)

    assert current['schema'] == 'review_input@2.5.2'
    assert old_input['schema'] == 'review_input@2.5.1'
    assert 'request_contract' not in old_input
    assert 'coverage_summary' in old_input['wpem']['same_candidate_window']
    assert current['projection_hash'] != old_input['projection_hash']
    assert result['response_protocol'] == product.RESPONSE_PROTOCOL
    assert result['review_validity']['review_input_version'] == 'review_input@2.5.1'
    assert result['review_validity']['projection_hash'] == old_input['projection_hash']


def test_new_evidence_cannot_be_relabelled_as_legacy_250_review_input():
    snapshot, package = fixture()
    current = product.build_review_input(snapshot, package)
    old_input = _retarget_review_input_schema(current, 'review_input@2.5.0', drop_request_contract=True)
    answer = product.required_selection_payload(old_input)

    assert package['schema'] == 'wpem_review_evidence@1.0.1'
    assert 'coverage_summary' in package['same_candidate_window']
    with pytest.raises(legacy.ReviewValidationError, match='legacy WPEM review input requires legacy evidence shape'):
        legacy.validate_wire_response(snapshot, answer, review_input=old_input, wpem_evidence=package)


def test_old_wire_is_not_accepted_for_new_projection_and_financial_output():
    snapshot, package = fixture()
    current = product.build_review_input(snapshot, package)
    answer = product.required_selection_payload(current)
    old_input = legacy.build_review_input(snapshot)
    answer['projection_hash'] = old_input['projection_hash']
    with pytest.raises(legacy.ReviewValidationError, match='projection hash'):
        legacy.validate_wire_response(snapshot, answer, review_input=current, wpem_evidence=package)


def test_window_change_marks_review_stale_without_cooldown_renewal():
    snapshot, package = fixture()
    current = product.build_review_input(snapshot, package)
    answer = product.required_selection_payload(current)
    result = legacy.validate_wire_response(snapshot, answer, review_input=current, wpem_evidence=package)
    assert product.review_status(result, snapshot, package) == 'CURRENT'
    changed = evidence.build_review_evidence(snapshot, release_identity={'release_id':'other-input'})
    assert product.review_status(result, snapshot, changed) == 'STALE'
    assert product.review_status(result, snapshot, package, now_ms=snapshot['market']['valuation_ts_ms']+core.MAX_BOOK_AGE_MS+1) == 'STALE'
    assert result['review_validity']['cooldown_extends_validity'] is False


def test_model_and_future_quote_cannot_reuse_current_review():
    snapshot, package = fixture()
    current = product.build_review_input(snapshot, package, model='model-a')
    result = legacy.validate_wire_response(snapshot, product.required_selection_payload(current),
                                          review_input=current, wpem_evidence=package)
    now = snapshot['market']['valuation_ts_ms']
    assert product.review_status(result, snapshot, package, model='model-a', now_ms=now) == 'CURRENT'
    assert product.review_status(result, snapshot, package, model='model-b', now_ms=now) == 'STALE'
    missing = deepcopy(result)
    missing['review_validity']['model'] = None
    assert product.review_status(missing, snapshot, package, model='model-a') == 'STALE'
    assert product.review_status(result, snapshot, package, model='model-a', now_ms=now-1) == 'STALE'


def test_catalog_capacity_fails_before_budget_or_transport(tmp_path, monkeypatch):
    snapshot, package = fixture()
    ledger = store.UnderwritingLedger(tmp_path/'ledger.jsonl')
    ledger.record_snapshot(snapshot)
    calls=[]
    def cannot_select(_):
        raise legacy.ReviewValidationError('CATALOG_GAP: required IDs exceed bounded selection capacity')
    monkeypatch.setattr(product, 'required_selection_payload', cannot_select)
    result = legacy.run_review(snapshot, ledger, tmp_path/'review', api_key='fake-test-only',
        model='local-test-model', budget=Budget(), transport=lambda *a, **k:calls.append(a),
        research_only=True, wpem_evidence=package)
    assert result['status'] == 'CATALOG_GAP' and result['http_calls'] == 0 and calls == []


def test_exactly_one_opt_in_call_and_repeated_research_receipt_does_not_recall(tmp_path):
    snapshot, package = fixture()
    ledger = store.UnderwritingLedger(tmp_path/'ledger.jsonl')
    ledger.record_snapshot(snapshot)
    calls=[]
    def transport(key, model, request, timeout, endpoint=None):
        calls.append(request)
        answer=product.required_selection_payload(request['_local_review_input'])
        return {'model':'local-test-model', 'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}]}
    kwargs={'api_key':'fake-test-only','model':'local-test-model','budget':Budget(), 'transport':transport,
            'research_only':True,'wpem_evidence':package}
    result=legacy.run_review(snapshot,ledger,tmp_path/'review',**kwargs)
    assert result['status']=='RESEARCH_OK' and result['schema']=='research_review@2.5.0'
    assert result['http_calls']==1 and result['production_attach_allowed'] is False
    assert legacy.run_review(snapshot,ledger,tmp_path/'review',**kwargs)==result
    assert len(calls)==1
