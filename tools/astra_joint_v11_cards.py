"""Natural NR card adapter using the identical v1.1 historical common features."""
from __future__ import annotations
from astra_joint_data import HistoricalBars
from astra_joint_dataset import canonical_hash,expiry_ms,select_legs
from astra_joint_projection import compute_assessment_hash,validate_assessment
from astra_joint_card_statistics import card_id,card_time_ms,source_record_hash,_epoch_ms
from astra_joint_v11_data import common_features,side_features,reference_spot_close
from astra_joint_v11_inference import predict_row

def range_support(row, model):
    outside=[];unseen_missing=[]
    for item in (model.get('feature_support') or {}).get('features',[]):
        name=item['name'];value=row.get(name)
        if value is None or value=='':
            if item.get('missing_count')==0:unseen_missing.append(name)
            continue
        if item.get('min') is not None and (float(value)<item['min'] or float(value)>item['max']):
            outside.append(name)
    return dict(out_of_range_features=outside,unseen_missing_features=unseen_missing,
                range_status='outside_observed_support' if outside or unseen_missing else 'within_marginal_observed_support',
                warning_cn=(f"有{len(outside)}项输入超出训练观测范围，{len(unseen_missing)}项出现训练未见的缺失；外推可靠性尚未验证。"
                            if outside or unseen_missing else "单项输入处于已观测范围，不代表当前组合或自然信号效果已获验证。"))

def build_assessment(card,ledger,artifact,contracts):
    asof=card_time_ms(card);cid=card_id(card)
    cutoff=_epoch_ms(artifact.get("training_cutoff_ms") or artifact.get("training_cutoff"))
    if not asof or not cutoff:raise ValueError("model/card time identity missing")
    source=source_record_hash(card)
    um=HistoricalBars(ledger.bars("um"));spot=HistoricalBars(ledger.bars("spot"))
    snapshot=common_features(asof,um);price_ref=reference_spot_close(asof,spot)
    expiry=expiry_ms(asof);dte=(expiry-asof)/3600000
    result={"schema":"astra_statistical_assessment@1.1.0","status":"available",
        "event_id":cid,"source_card_id":cid,"symbol":(card.get("identity") or {}).get("symbol","BTC"),
        "provenance":{"as_of_ms":asof,"training_cutoff_ms":cutoff,"source_record_hash":source,
            "input_hash":"sha256:"+canonical_hash([snapshot,price_ref]),"model_id":artifact.get("model_id") or ((artifact.get("models") or {}).get(artifact.get("selected_feature_group")) or {}).get("model_id") or "nr-v11",
            "model_hash":"sha256:"+canonical_hash(artifact)},
        "feature_schema":snapshot["schema"],"source_domain":{"kind":"natural_signal_card",
            "support_cn":"与全时钟训练使用同一共同截面；适用于自然信号的增量仍待前向验证。"},
        "sides":{},"market_reference":price_ref,
        "scope_cn":"自然接管卡统计研究；比较参考结构赔付，不代表净胜率或交易许可。",
        "uncertainty":{"note_cn":"训练范围内不等于可靠；近期自然信号尚未完成前向经济验证。"}}
    reason=None
    if str(result['symbol']).upper() not in ('BTC','BTCUSDT','BTC/USDT'):
        reason="本统计模型仅覆盖比特币，当前标的不在训练范围。"
    if not 8<dte<=24:reason="本卡不在普通轮八至二十四小时研究期限。"
    if not price_ref or not spot.closed_window(asof,1):reason="卡时已闭合现货分钟价格不可用，参考结构暂不能建立。"
    if um.closed_window(asof,30) is None:reason="近端合约分钟不完整，统计输入暂不可用。"
    if cutoff>asof:raise ValueError("model trained after card")
    eligible=[c for c in contracts if c.get("expiration_timestamp")==expiry]
    for side in ("put","call"):
        data={"status":"insufficient","reason_cn":reason,"quote":{"status":"not_collected"},
              "scope_cn":result["scope_cn"],"uncertainty_cn":result["uncertainty"]["note_cn"]}
        legs=select_legs(eligible,side,price_ref["price"],2000,asof) if price_ref else None
        if legs:
            short,long=legs;price=price_ref["price"];width=abs(short["strike"]-long["strike"])
            row=side_features(snapshot,side,price,short,long,dte)
            row.update(side=side,actual_width=width,entry_price=price,row_id=canonical_hash([cid,side]))
            data["reference"]={"short_strike":short["strike"],"long_strike":long["strike"],"width":width,
                "entry_price":price,"expiry_ms":expiry,"short_name":short["instrument_name"],
                "long_name":long["instrument_name"],"short_contract":short,"long_contract":long,
                "price_observed_at_ms":price_ref["price_observation_ms"],
                "price_basis_cn":"卡时已闭合现货分钟收盘价，仅作参考结构，不代表成交。"}
            if not reason:
                predicted=predict_row(row,artifact)
                qualification=predicted.get("eligibility") or {}
                if predicted.get("status")=="available" and qualification.get("qualified") is True:
                    model=(artifact.get('models') or {}).get(artifact.get('selected_feature_group')) or {}
                    support={**(predicted.get('input_support') or {}),**range_support(row,model)}
                    for key in ("probability_positive","conditional_positive_loss","expected_loss_normalized"):
                        data[key]=predicted[key]
                    data["expected_payout_btc"]=data["expected_loss_normalized"]*width/price
                    data.update(status="available",reason_cn=None,
                        input_support=support,
                        uncertainty_cn=support['warning_cn'],
                        tail_probability=predicted.get("tail_probability"),
                        tail_probability_status=predicted.get("tail_probability_status","unavailable"),
                        tail_probability_note_cn=("尾部概率未通过事件包含关系与校准验收，暂不用于逐卡建议。" if predicted.get("tail_probability_status")=="research_only" else ""),
                        eligibility=qualification,
                        prediction_hash="sha256:"+canonical_hash(predicted))
                else:data["reason_cn"]="当前模型或输入未通过统计资格检查，保留市场分析。"
        elif not reason:data["reason_cn"]="卡时未找到完整实际参考两腿。"
        result["sides"][side]=data
        if data["status"]!="available":result["status"]="insufficient"
    if result["status"]!="available":result["reason_cn"]=reason or "部分统计资料或模型资格不足。"
    result["assessment_hash"]=compute_assessment_hash(result)
    return validate_assessment(result,card)
