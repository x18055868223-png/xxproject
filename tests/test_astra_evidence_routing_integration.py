"""Routing cannot alter frozen accounting, survive resealing, or cross candidates."""
from copy import deepcopy
import pytest
from tools import astra_wpem_review_evidence_v1 as evidence
from tools import astra_wpem_workbench_v1 as workbench
from tests.test_astra_wpem_review_evidence_v1 import _covered_synthetic_sequence, _reseal_review


def fixture():
    packets, reports = _covered_synthetic_sequence()
    return packets[-1]['snapshot'], packets, reports


def test_routing_adds_only_consumer_metadata_and_keeps_old_package_replay():
    snapshot, packets, reports = fixture()
    before = deepcopy(snapshot)
    old = evidence.build_review_evidence(snapshot, packets, reports, include_routing=False)
    new = evidence.build_review_evidence(snapshot, packets, reports)
    assert snapshot == before
    assert old['schema'] == 'wpem_review_evidence@1.0.1'
    assert new['schema'] == 'wpem_review_evidence@1.1.0'
    assert new['evidence_routing']['binding']['candidate_id'] == snapshot['candidate_id']
    assert {k:v for k,v in new.items() if k not in {'schema','evidence_routing','evidence_hash','review_evidence_hash'}} == {
        k:v for k,v in old.items() if k not in {'schema','evidence_hash','review_evidence_hash'}}
    assert evidence.validate_review_evidence(old,snapshot)['status'] == 'VALID'
    assert evidence.validate_review_evidence(new,snapshot)['status'] == 'VALID'
    assert new['risk_status']['production_mu_ref_btc'] is None
    assert new['risk_status']['production_M_ref_btc'] is None


@pytest.mark.parametrize('kind',['metadata','downgrade','remove','binding'])
def test_new_routing_is_rebuilt_even_if_outer_package_is_resealed(kind):
    snapshot, packets, reports = fixture()
    package = evidence.build_review_evidence(snapshot,packets,reports)
    if kind == 'metadata': package['evidence_routing']['units'][0]['reason_cn'] = 'FORGED SUPPORT'
    if kind == 'downgrade': package['schema']='wpem_review_evidence@1.0.1'
    if kind == 'remove': package.pop('evidence_routing')
    if kind == 'binding': package['evidence_routing']['binding']['candidate_id']='ANOTHER_CANDIDATE'
    with pytest.raises(ValueError): evidence.validate_review_evidence(_reseal_review(package),snapshot)


def test_public_evidence_keeps_routes_but_not_private_replay_sources():
    snapshot,packets,reports=fixture()
    package=evidence.build_review_evidence(snapshot,packets,reports)
    public=workbench.public_evidence(package)
    assert public['evidence_routing']==package['evidence_routing']
    assert 'composition_inputs' not in public and 'source_bindings' not in public
