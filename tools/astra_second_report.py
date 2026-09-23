"""Join sealed, outcome-blind reviews with second-study results; no HTTP."""
import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

BJT = timezone(timedelta(hours=8))
GRADES = ('D', 'C', 'B', 'A', 'S')

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def read_csv(path):
    rows = list(csv.DictReader(Path(path).open(encoding='utf-8-sig')))
    for row in rows:
        for key, value in row.items():
            if value in ('True', 'False', 'true', 'false'):
                row[key] = value.lower() == 'true'
            elif value == '':
                row[key] = None
            else:
                try:
                    row[key] = float(value)
                except (ValueError, TypeError):
                    pass
    return rows

def metrics(rows):
    """One target width/credit scenario at a time; side counts are explicit."""
    n = len(rows)
    if not n:
        return dict(n=0, cards=0, dates=0, wins=0, losses=0, ties=0, win_rate=None,
                    intrusion_rate=None, payoff_ratio=None, avg_win_btc=None,
                    avg_loss_btc=None, mean_roi=None, capital_roi=None, apr=None, total_pnl_btc=None,
                    worst_roi=None, tail_to_credit=None, dte_mean=None)
    p = [float(r['net_pnl_btc']) for r in rows]
    wins = [x for x in p if x > 1e-12]
    losses = [x for x in p if x < -1e-12]
    margins = [float(r['margin_btc']) for r in rows]
    hours = [float(r['dte_hours']) for r in rows]
    credits = sum(float(r['net_credit_btc']) for r in rows)
    tails = sorted((float(r['payout_btc']) for r in rows), reverse=True)[:max(1, math.ceil(n*.05))]
    avgwin = statistics.mean(wins) if wins else None
    avgloss = -statistics.mean(losses) if losses else None
    rois = [v/m for v,m in zip(p,margins)]
    return dict(n=n, cards=len({r['card_id'] for r in rows}),
                dates=len({datetime.fromtimestamp(r['entry_ms']/1000, BJT).date() for r in rows}),
                wins=len(wins), losses=len(losses), ties=n-len(wins)-len(losses),
                win_rate=len(wins)/n, intrusion_rate=len(losses)/n,
                payoff_ratio=avgwin/avgloss if avgwin is not None and avgloss else None,
                avg_win_btc=avgwin, avg_loss_btc=avgloss, total_pnl_btc=sum(p),
                mean_roi=statistics.mean(rois), capital_roi=sum(p)/sum(margins), worst_roi=min(rois),
                apr=365*sum(p)/sum(m*h/24 for m,h in zip(margins,hours)),
                tail_to_credit=sum(tails)/credits if credits else None,
                dte_mean=statistics.mean(hours), dte_min=min(hours),dte_max=max(hours))

def subset(rows, **conditions):
    return [r for r in rows if all(r.get(k) == v for k,v in conditions.items())]

def grade_table(rows):
    result=[]
    for side in ('put_credit','call_credit'):
        base=subset(rows,side=side)
        for label, allowed in [(g,{g}) for g in GRADES]+[('B及以上',set('BAS')),('A/S',set('AS')),('未评级',{None})]:
            chosen=[r for r in base if r.get('offline_grade') in allowed]
            other=[r for r in base if r.get('offline_grade') not in allowed]
            result.append({'side':side,'filter':label,'coverage':len(chosen)/len(base) if base else 0,
                           'selected':metrics(chosen),'unselected':metrics(other)})
    return result

def stratify(rows, keys):
    groups={}
    for row in rows:
        key=tuple(row.get(k) for k in keys)
        groups.setdefault(key,[]).append(row)
    return [dict(zip(keys,key),metrics=metrics(value)) for key,value in sorted(groups.items(),key=lambda kv:str(kv[0]))]

def write_csv(path, rows):
    if not rows:
        return
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fields=list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)

def reader_rows(rows, weekend=False):
    """Readable main-scenario ledger; the full numeric CSV remains authoritative."""
    labels={'BULLISH':'偏多','BEARISH':'偏空','NEUTRAL':'中性',
            'put_credit':'Put信用价差','call_credit':'Call信用价差',
            'directional':'原方向侧','opposite':'相反侧',
            'neutral_put':'中性独立Put侧','neutral_call':'中性独立Call侧'}
    when=lambda ms: datetime.fromtimestamp(float(ms)/1000,BJT).strftime('%Y-%m-%d %H:%M:%S') if ms else ''
    number=lambda value: '' if value is None else value
    result=[]
    for r in rows:
        mature=r['is_matured'] is True
        item={'来源卡':r['card_id'],'原卡方向':labels.get(r['direction'],r['direction']),
            '入场时间_北京时间':when(r['entry_ms']),'到期时间_北京时间':when(r['expiry_ms']),
            '剩余期限_小时':r['dte_hours'],'入场参考价格_USDT':r['entry_price'],
            '到期交割价_USD':number(r['delivery_price']),
            '侧别':labels[r['side']],'对照身份':labels.get(r['relation'],r['relation']),
            '卖出腿':r['short_instrument'],'保护腿':r['long_instrument'],
            '实际宽度_USD':r['actual_width'],'假设净补偿_BTC':r['net_credit_btc'],
            '到期赔付_BTC':number(r['payout_btc']),'到期净盈亏_BTC':number(r['net_pnl_btc']),
            '盈亏平衡侵入':'未到期' if not mature else ('是' if r['breakeven_intruded'] else '否'),
            '单例校准情景保证金_BTC':r['margin_btc'],
            '情景保证金持有回报_百分数':100*r['net_pnl_btc']/r['margin_btc'] if mature else '',
            '来源卡时离线等级':r['offline_grade'] or '未评级',
            '评级时点_北京时间':when(r['offline_rating_asof_ms']),
            '评级说明':'历史资料下的当前标准离线评级',
            '原卡记录近端证据':'有' if r['source_has_near_term'] else '无',
            '原卡记录墙位':'有' if r['source_walls_present'] else '无'}
        if weekend:
            item.update({'周末观察日':r['observation_date_bjt'],'来源卡龄_小时':r['source_card_age_hours'],
                         '成熟状态':'已到期' if mature else '未到期'})
        else:
            item.update({'时段':r['session_bjt'],'纽约日期':r['ny_date'],
                         '纽约交易日':'是' if r['ny_is_trading_day'] else '否'})
        result.append(item)
    return result

def build_report(study, ratings_dir):
    study,ratings_dir=Path(study),Path(ratings_dir)
    seal=json.loads((ratings_dir/'seal.json').read_text(encoding='utf-8'))
    for item in seal['files']:
        if sha(ratings_dir/item['path'])!=item['sha256']:
            raise RuntimeError(f"sealed rating file changed: {item['path']}")
    ratings=[json.loads(line) for line in (ratings_dir/'ratings_by_side.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    if len(ratings)!=228 or len({r['card_id'] for r in ratings})!=114:
        raise RuntimeError('rating cohort must contain 114 cards and 228 side rows')
    if any(r['review_status']=='PENDING' for r in ratings):
        raise RuntimeError('all frozen cards must settle before joining returns')
    byside={(r['card_id'],r['side']):r for r in ratings}
    if len(byside)!=228:raise RuntimeError('duplicate card-side ratings')
    raw_reviews=[json.loads(line) for line in (ratings_dir/'reviews.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    byreview={r['card_id']:r['llm_review'] for r in raw_reviews}
    source=read_csv(study.parent/'astra-light-study-20260913/standard_signal_samples.csv')
    bysource={r['card_id']:r for r in source}
    ordinary=read_csv(study/'analysis/ordinary_results.csv')
    weekend=read_csv(study/'analysis/weekend_results.csv')
    for row in ordinary+weekend:
        rating=byside[(row['card_id'],row['side'])]
        original=bysource[row['card_id']]
        row['margin_btc']=row['estimated_margin_btc_lambda_1x']
        row['offline_grade']=rating['grade'] if rating['side_status']=='RATED' and rating['grade'] in GRADES else None
        row['offline_side_status']=rating['side_status']
        row['offline_review_status']=rating['review_status']
        row['offline_packet_hash']=rating['input_packet_hash']
        row['offline_assessment_hash']=rating['assessment_hash']
        row['offline_rating_asof_ms']=rating['event_time_ms']
        row['offline_rating_identity']='source_card_time_current_standard_offline'
        row['source_has_near_term']=original['has_native_near_term']
        row['source_walls_present']=original['call_wall'] is not None and original['put_wall'] is not None
        row['source_month']=datetime.fromtimestamp(float(original['event_time_ms'])/1000,BJT).strftime('%Y-%m')
    out=study/'report';out.mkdir(exist_ok=True)
    write_csv(out/'ordinary_joined.csv',ordinary)
    write_csv(out/'weekend_joined.csv',weekend)
    main=subset(ordinary,target_width=2000.,credit_fraction=.1)
    wm=subset(weekend,target_width=2000.,credit_fraction=.1,is_matured=True)
    if len(main)!=228 or len(wm)!=24:raise RuntimeError('main result denominators changed')
    write_csv(out/'普通轮_主情景逐笔.csv',reader_rows(main))
    write_csv(out/'周末轮_主情景逐笔.csv',reader_rows(subset(weekend,target_width=2000.,credit_fraction=.1),weekend=True))
    sessions=['美盘后','亚盘','欧盘','美盘前核心窗','美盘中']
    group = lambda rows: [{'direction':d,'side':s,**metrics(subset(rows,direction=d,side=s))}
        for d in ('BULLISH','BEARISH','NEUTRAL') for s in ('put_credit','call_credit')]
    session_groups=[]
    for name in sessions:
        rows=subset(main,session_bjt=name)
        cards={r['card_id']:r for r in rows}
        session_groups.append({'session':name,'cards':len(cards),'directions':dict(Counter(r['direction'] for r in cards.values())),
            'put':metrics(subset(rows,side='put_credit')),'call':metrics(subset(rows,side='call_credit')),
            'directional':metrics(subset(rows,relation='directional')),
            'opposite':metrics(subset(rows,relation='opposite')),
            'not_selected_put':metrics([r for r in main if r['side']=='put_credit' and r['session_bjt']!=name]),
            'not_selected_call':metrics([r for r in main if r['side']=='call_credit' and r['session_bjt']!=name])})
    budget=json.loads((ratings_dir/'budget.json').read_text(encoding='utf-8'))
    usage=Counter();elapsed=[];input_bytes=[];returned_models=Counter();error_reasons=Counter()
    for path in (ratings_dir/'reviews.jsonl.v2_attempts/responses').glob('*.json'):
        response=json.loads(path.read_text(encoding='utf-8'));returned_models[response.get('model','unknown')]+=1
        for k,v in response.get('usage',{}).items():
            if isinstance(v,(int,float)):usage[k]+=v
    for review in byreview.values():
        for attempt in review.get('call_audit',[]):
            if attempt.get('elapsed_seconds') is not None:elapsed.append(attempt['elapsed_seconds'])
            if attempt.get('input_bytes') is not None:input_bytes.append(attempt['input_bytes'])
        for side in review.get('integrated_trade_advisory',{}).get('side_evidence_ratings',{}).values():
            if side.get('status')!='RATED':
                for reason in side.get('validation_reasons_cn',[]):error_reasons[reason]+=1
    data={
        'schema':'astra_second_report@1.0.0','joined_at_utc':datetime.now(timezone.utc).isoformat(),
        'ratings_sealed_at_utc':seal['sealed_at_utc'],'ratings_seal_hash':sha(ratings_dir/'seal.json'),
        'config':json.loads((study/'study_config.json').read_text(encoding='utf-8')),
        'math_summary':json.loads((study/'analysis/summary.json').read_text(encoding='utf-8')),
        'ordinary_groups':group(main),'weekend_groups':group(wm),'sessions':session_groups,
        'ordinary_paired':{'directional':metrics(subset(main,relation='directional')),'opposite':metrics(subset(main,relation='opposite'))},
        'weekend_paired':{'directional':metrics(subset(wm,relation='directional')),'opposite':metrics(subset(wm,relation='opposite'))},
        'ny_groups':stratify(main,['ny_is_trading_day','side']),
        'session_ny_groups':stratify(main,['session_bjt','ny_is_trading_day','side']),
        'session_direction_groups':stratify(main,['session_bjt','direction','side']),
        'tenor_groups':stratify(main,['dte_bucket','side']),
        'credit_groups':stratify(subset(ordinary,target_width=2000.),['credit_fraction','direction','side']),
        'weekend_credit_groups':stratify(subset(weekend,target_width=2000.,is_matured=True),['credit_fraction','direction','side']),
        'width_groups':stratify(subset(ordinary,credit_fraction=.1),['target_width','direction','side']),
        'weekend_age_groups':stratify(wm,['source_card_age_le_24h','side']),
        'grade_filters':grade_table(main),'weekend_grade_filters':grade_table(wm),
        'grade_direction_groups':stratify(main,['side','direction','offline_grade']),
        'grade_confounds':stratify(main,['side','offline_grade','source_walls_present','source_has_near_term']),
        'grade_months':stratify(main,['side','offline_grade','source_month']),
        'grade_session_groups':stratify(main,['side','session_bjt','offline_grade']),
        'coverage':json.loads((ratings_dir/'coverage.json').read_text(encoding='utf-8')),
        'call_measurements':{'http_calls':budget['total_http_calls_used'],'usage':dict(usage),'returned_models':dict(returned_models),
            'elapsed_mean_seconds':statistics.mean(elapsed) if elapsed else None,'elapsed_total_seconds':sum(elapsed),
            'input_bytes_sum':sum(input_bytes),'input_bytes_mean':statistics.mean(input_bytes) if input_bytes else None},
        'unrated_reasons':dict(error_reasons),
        'failure_audit':json.loads((study/'failure_audit/failure_audit.json').read_text(encoding='utf-8')),
        'ordinary_main_rows':main,'weekend_main_rows':wm,
        'weekend_observations':read_csv(study/'analysis/weekend_observations.csv'),
        'volatility_pairs':read_csv(study/'analysis/weekend_volatility_pairs.csv'),
    }
    (out/'report_data.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    flat=[]
    for g in data['grade_filters']:
        for status in ('selected','unselected'):flat.append({'side':g['side'],'filter':g['filter'],'group':status,'coverage':g['coverage'],**g[status]})
    write_csv(out/'grade_filter_results.csv',flat)
    for name in ('grade_direction_groups','grade_confounds','grade_months','grade_session_groups','session_direction_groups','ny_groups','tenor_groups'):
        write_csv(out/(name+'.csv'),[{k:v for k,v in g.items() if k!='metrics'}|g['metrics'] for g in data[name]])
    print(json.dumps({'ordinary_rows':len(ordinary),'weekend_rows':len(weekend),'sealed_before_join':True,'report':str(out/'report_data.json')},ensure_ascii=False))
    return data

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study',type=Path,required=True)
    parser.add_argument('--ratings-dir',type=Path,required=True)
    args=parser.parse_args();build_report(args.study,args.ratings_dir)
