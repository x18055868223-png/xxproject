import copy
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import signal_review_v2 as core
from signal_review_joint import normalize, statistical_ranking
from test_astra_joint_runtime import context_for_fixture
from test_signal_review_v221_time import current_packet, card, payload_v22

def opinion():
    return dict(recommendation="put_credit", mechanism_verdict="supports",
                summary_cn="优先研究下行侵入受约束的一侧，继续观察结构是否迁移。",
                evidence_roles=[dict(ref="F_STRUCTURE", role="supports_applicability", claim_cn="结构位置提供约束背景。")],
                applicability_gaps_cn=[], strengthen_if_cn=["主动卖出减弱且价格稳定。"],
                weaken_if_cn=["结构向不利方向迁移。"])


def spatial_packet(*, price=100000.0, put_wall=95000.0, call_wall=105000.0):
    p = current_packet()
    asof = p["identity"]["as_of_ms"]
    p["facts"] = [
        fact for fact in p["facts"]
        if fact["id"] not in {
            "market.price.current",
            "structure.gamma.put_wall",
            "structure.gamma.call_wall",
        }
    ]
    base_provenance = dict(p["facts"][0]["provenance"])

    def item(fact_id, label, value, topic="structure_location"):
        return dict(id=fact_id, topic=topic, label_cn=label, value=value,
                    unit="USDT", source_refs=["unit-test"], source_group="OPTIONS_STRUCTURE",
                    observed_at_ms=None, available_at_ms=asof - 500,
                    provenance=base_provenance, window="当前截面", usable=True,
                    summary_cn=f"{label}为{value:.0f}。", limitations_cn=[], dependencies=[])

    p["facts"].extend([
        item("market.price.current", "当前标的价格", price, topic="market_price"),
        item("structure.gamma.put_wall", "Put 墙", put_wall),
        item("structure.gamma.call_wall", "Call 墙", call_wall),
    ])
    return p


def add_put_wall_distance(packet, value=0.45):
    asof = packet["identity"]["as_of_ms"]
    base_provenance = dict(packet["facts"][0]["provenance"])
    packet["facts"].append(dict(id="structure.distance.put_wall_pct", topic="structure_location",
                                label_cn="现价距下方 Put 墙", value=value, unit="%",
                                source_refs=["unit-test"], source_group="DERIVED_RELATION",
                                observed_at_ms=None, available_at_ms=asof - 500,
                                provenance=base_provenance, window="当前截面", usable=True,
                                summary_cn=f"现价距下方 Put 墙约 +{value}%。",
                                limitations_cn=["距离是当前截面，不证明边界不可突破。"],
                                dependencies=["market.price.current", "structure.gamma.put_wall"]))
    return packet


def facts(packet):
    return {fact["id"]: fact for fact in packet["facts"]}


def spatial_opinion(ctx, claim, *, ref="structure.gamma.put_wall"):
    raw = opinion()
    rank = statistical_ranking(ctx)
    raw["recommendation"] = rank if rank != "tie" else "put_credit"
    raw["evidence_roles"][0].update(
        ref=ref,
        role="supports_applicability",
        claim_cn=claim,
    )
    return raw


def test_protocols_native_and_old_context():
    p=current_packet(); ctx=context_for_fixture()
    for version, schema in ((core.PROMPT_VERSION_2_2_2, core.OUTPUT_SCHEMA_VERSION_2_2),
                            (core.PROMPT_VERSION, core.OUTPUT_SCHEMA_VERSION)):
        r=core.build_review(card(),payload_v22(),p,prompt_version=version,statistical_context=ctx)
        assert r["schema_version"] == schema
        core.revalidate_review(card(),r)

def test_joint_roles_valid_and_local_errors_do_not_change_grades():
    p=current_packet(); ctx=context_for_fixture(); raw=opinion()
    first_id=next(x["id"] for x in p["facts"] if x["usable"])
    raw["evidence_roles"][0]["ref"]=first_id
    raw["recommendation"]=statistical_ranking(ctx) if statistical_ranking(ctx)!="tie" else "put_credit"
    raw["applicability_gaps_cn"]=["近端成交来源覆盖不足，暂不能确认压力是否延续。"]
    payload=payload_v22(); payload["joint_review"]=raw
    r=core.build_review(card(),payload,p,statistical_context=ctx)
    assert r["integrated_trade_advisory"]["joint_review"]["status"] == "ASSESSED"
    core.revalidate_review(card(),r)
    unchanged=copy.deepcopy(r["integrated_trade_advisory"]["side_evidence_ratings"])
    rebuilt=core.build_review(card(),core.model_payload_from_review(r),p,statistical_context=ctx)
    assert rebuilt["integrated_trade_advisory"]["joint_review"]==r["integrated_trade_advisory"]["joint_review"]
    payload["joint_review"]["evidence_roles"][0]["ref"]="invented"
    bad=core.build_review(card(),payload,p,statistical_context=ctx)
    assert bad["integrated_trade_advisory"]["joint_review"]["status"]=="UNAVAILABLE"
    assert bad["integrated_trade_advisory"]["side_evidence_ratings"]==unchanged
    core.revalidate_review(card(),bad)

def test_reverse_requires_counter_or_gap_and_cannot_tamper():
    p=current_packet(); ctx=context_for_fixture(); facts={f["id"]:f for f in p["facts"]}
    raw=opinion(); raw["recommendation"]="watch"
    raw["evidence_roles"][0]["ref"]=next(f["id"] for f in p["facts"] if f["usable"])
    got=normalize(raw,ctx,facts,p["identity"]["as_of_ms"])
    assert got["status"]=="UNAVAILABLE"
    raw["applicability_gaps_cn"]=["较长窗口成交缺少对应近端价格响应。"]
    got=normalize(raw,ctx,facts,p["identity"]["as_of_ms"])
    assert got["status"]=="ASSESSED"
    payload=payload_v22();payload["joint_review"]=raw
    r=core.build_review(card(),payload,p,statistical_context=ctx)
    r["integrated_trade_advisory"]["joint_review"]["recommendation"]="call_credit"
    with pytest.raises(ValueError):core.revalidate_review(card(),r)

def test_new_schema_single_call_and_absent_statistics():
    schema=core.response_schema()
    assert "joint_review" in schema["required"]
    r=core.build_review(card(),payload_v22(),current_packet())
    assert r["integrated_trade_advisory"]["joint_review"]["status"]=="UNAVAILABLE"
    assert r["integrated_trade_advisory"]["validation"]["semantic_validation_revision"] == core.SEMANTIC_VALIDATION_REVISION
    assert core.build_summary(r)["display_projection_version"]=="2.3.0"


def test_prompt_risk_order_matches_local_frozen_order():
    from astra_joint_bridge import prompt_context
    ctx=context_for_fixture()
    packet=prompt_context(ctx)
    assert packet['statistical_preference']==statistical_ranking(ctx)
    assert '不是净收益排序' in packet['risk_order_basis_cn']

def test_context_does_not_masquerade_as_mechanism_confirmation():
    p=current_packet();ctx=context_for_fixture();facts={f['id']:f for f in p['facts']}
    raw=opinion();raw['recommendation']='watch';raw['applicability_gaps_cn']=['近端结构来源暂缺。']
    raw['evidence_roles'][0].update(ref=next(f['id'] for f in p['facts'] if f['usable']),role='context_only')
    assert normalize(raw,ctx,facts,p['identity']['as_of_ms'])['status']=='UNAVAILABLE'
    raw['mechanism_verdict']='uncertain'
    assert normalize(raw,ctx,facts,p['identity']['as_of_ms'])['status']=='ASSESSED'


def test_joint_rejects_explicit_put_wall_position_contradiction_without_changing_market_review():
    p=spatial_packet();ctx=context_for_fixture()
    raw=spatial_opinion(ctx,"现价上方较近处存在下方Put墙参照。")
    got=normalize(raw,ctx,facts(p),p['identity']['as_of_ms'])
    assert got['status']=='UNAVAILABLE'
    assert any('Put墙相对现价上下方' in reason for reason in got['validation_reasons_cn'])
    payload=payload_v22();payload['joint_review']=raw
    r=core.build_review(card(),payload,p,statistical_context=ctx)
    assert r['status']=='OK'
    assert r['integrated_trade_advisory']['joint_review']['status']=='UNAVAILABLE'
    assert all(side['status']=='RATED' for side in r['integrated_trade_advisory']['side_evidence_ratings'].values())
    core.revalidate_review(card(),r)


def test_joint_summary_cannot_override_unrated_recommended_side():
    p=spatial_packet();ctx=context_for_fixture()
    payload=payload_v22()
    payload['side_evidence_ratings']['put_credit']['evidence_roles'][0].update(
        ref='structure.gamma.put_wall',
        claim_cn='现价上方较近处存在下方Put墙参照。')
    payload['joint_review']=spatial_opinion(ctx,'下方Put墙在现价下方，只作为结构参照。')
    r=core.build_review(card(),payload,p,statistical_context=ctx)
    advisory=r['integrated_trade_advisory']
    assert r['status']=='PARTIAL'
    assert advisory['side_evidence_ratings']['put_credit']['status']=='UNRATED'
    assert advisory['joint_review']['status']=='ASSESSED'
    summary=core.build_summary(r)
    assert summary['joint_review']['display_limited_by_side_qualification'] is True
    assert '联合建议指向的一侧未通过本地证据资格' in summary['joint_review']['display_limit_reason_cn']
    limited_summary = '联合建议暂不采用：推荐侧的证据未通过本地语义核验。'
    assert summary['display_action_summary_cn'] == limited_summary
    assert summary['action_summary_cn'] == limited_summary
    assert summary['display_action_summary_cn'] != advisory['joint_review']['summary_cn']
    assert summary['display_action_summary_cn'] != advisory['advisory_guidance']['summary_cn']
    core.revalidate_review(card(),r)


def test_wall_zone_between_put_and_call_is_not_misattributed_to_call_wall():
    p=spatial_packet(price=77176.01,put_wall=77000.0,call_wall=80000.0)
    text="现价位于下方 Put 墙与上方 Call 墙之间，上方较近结构和下方很薄结构参照并存。"
    assert core._fact_assertion_issues({"basis_cn":text},facts(p))==[]


def test_distance_ref_expands_dependencies_for_put_wall_position_contradiction():
    p=add_put_wall_distance(spatial_packet(price=77350.3,put_wall=77000.0,call_wall=80000.0))
    payload=payload_v22()
    payload['side_evidence_ratings']['put_credit']['evidence_roles'][0].update(
        ref='structure.distance.put_wall_pct',
        claim_cn='现价上方较近处存在下方Put墙参照，距离本身只说明位置。')
    r=core.build_review(card(),payload,p,statistical_context=context_for_fixture())
    put=r['integrated_trade_advisory']['side_evidence_ratings']['put_credit']
    assert r['status']=='PARTIAL'
    assert put['status']=='UNRATED'
    assert any('Put墙相对现价上下方' in reason for reason in put['validation_reasons_cn'])


@pytest.mark.parametrize("price,put_wall,claim", [
    (100000.0,95000.0,"下方Put墙在现价下方，只作为结构参照。"),
    (94000.0,95000.0,"现价已经跌破Put墙，Put墙位于现价上方，只作为破位参照。"),
])
def test_joint_accepts_legal_wall_side_and_crossed_wall_claims(price, put_wall, claim):
    p=spatial_packet(price=price,put_wall=put_wall);ctx=context_for_fixture()
    got=normalize(spatial_opinion(ctx,claim),ctx,facts(p),p['identity']['as_of_ms'])
    assert got['status']=='ASSESSED'


def test_joint_rejects_referenced_wall_number_and_type_mismatch():
    p=spatial_packet();ctx=context_for_fixture()
    wrong_number=normalize(
        spatial_opinion(ctx,"下方Put墙为105000，作为结构参照。"),
        ctx,facts(p),p['identity']['as_of_ms'])
    assert wrong_number['status']=='UNAVAILABLE'
    assert any('Put墙数值' in reason for reason in wrong_number['validation_reasons_cn'])
    wrong_type=normalize(
        spatial_opinion(ctx,"上方Call墙为105000，作为结构参照。"),
        ctx,facts(p),p['identity']['as_of_ms'])
    assert wrong_type['status']=='UNAVAILABLE'
    assert any('墙位类型' in reason for reason in wrong_type['validation_reasons_cn'])


def test_joint_does_not_reject_negated_or_mixed_wall_context():
    p=spatial_packet();ctx=context_for_fixture()
    claim="不能说上方Put墙提供承接；上方Call墙和下方Put墙只是结构参照，距离不能证明承接。"
    got=normalize(spatial_opinion(ctx,claim),ctx,facts(p),p['identity']['as_of_ms'])
    assert got['status']=='ASSESSED'


def test_prompt_keeps_distance_strength_and_same_source_boundaries():
    content=core.build_request(current_packet(),core.DEFAULT_MODEL,
                               statistical_context=context_for_fixture())['messages'][1]['content']
    assert '距离本身不能证明承接或强度' in content
    assert '同源市场事实' in content
