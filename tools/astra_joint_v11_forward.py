"""Append-only natural-card choices and common post-opinion quotes. No orders."""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from astra_joint_dataset import canonical_hash, payout
from astra_joint_shadow import Ledger, public_get, now_ms, quote_side, validate_quote_group
from signal_review_joint import statistical_ranking

SCHEMA = "astra_nr_choice_set@1.0.0"
QUOTE_SCHEMA = "astra_nr_common_quote@1.0.0"
DAY = 86400000
MINUTE = 60000
HEALTH_SCHEMA = "astra_nr_collection_health@1.1.0"
REQUEST_MARKETS = ("binance_um", "binance_spot")
LABELS = {"statistics": "统计选侧", "joint": "联合建议", "original": "原信号方向",
          "fixed_put": "固定 Put", "fixed_call": "固定 Call"}

def _connection(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(folder/"nr_choices.sqlite",timeout=20)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS choices(id TEXT PRIMARY KEY,payload TEXT NOT NULL,hash TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS quotes(id TEXT PRIMARY KEY,status TEXT NOT NULL,payload TEXT);
    CREATE TABLE IF NOT EXISTS outcomes(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
    """)
    return db

def _dumps(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)

def _date(ms):
    return datetime.fromtimestamp(ms/1000,timezone.utc).strftime("%Y-%m-%d")

def _loads_payload(raw, reason):
    try:
        value=json.loads(raw) if isinstance(raw,str) else None
    except (TypeError,ValueError,json.JSONDecodeError):
        return None,[reason]
    if not isinstance(value,dict):
        return None,[reason]
    return value,[]

def _choice_integrity(cid, payload, stored_hash):
    reasons=[]
    if not isinstance(payload,dict):
        return ["integrity_choice_payload_invalid"]
    actual=canonical_hash(payload)
    if stored_hash!=actual:reasons.append("integrity_choice_hash_mismatch")
    if payload.get("card_id")!=cid:reasons.append("integrity_choice_identity_mismatch")
    return reasons

def _quote_integrity(quote, choice_hash):
    if not isinstance(quote,dict):
        return ["integrity_quote_payload_invalid"]
    source=quote.get("source_choice_hash")
    if not source:return ["integrity_quote_choice_hash_missing"]
    if source!=choice_hash:return ["integrity_quote_choice_hash_mismatch"]
    return []

def _expected_delivery_date(choice):
    refs=[s.get("reference") for s in choice.get("sides",{}).values() if isinstance(s,dict) and s.get("reference")]
    dates={_date(r["expiry_ms"]) for r in refs if isinstance(r,dict) and isinstance(r.get("expiry_ms"),(int,float))}
    return next(iter(dates)) if len(dates)==1 else None

def _outcome_integrity(cid, outcome, choice, quote):
    if not isinstance(outcome,dict):
        return ["integrity_outcome_payload_invalid"]
    reasons=[]
    choice_hash=canonical_hash(choice);quote_hash=canonical_hash(quote)
    if outcome.get("card_id")!=cid:reasons.append("integrity_outcome_identity_mismatch")
    if outcome.get("choice_hash")!=choice_hash:reasons.append("integrity_outcome_choice_hash_mismatch")
    if outcome.get("quote_hash")!=quote_hash:reasons.append("integrity_outcome_quote_hash_mismatch")
    if outcome.get('strict') is not bool(quote.get('strict')):
        reasons.append('integrity_outcome_strict_mismatch')
    if outcome.get('same_actual_width') is not bool(quote.get('same_actual_width')):
        reasons.append('integrity_outcome_width_mismatch')
    expected_date=_expected_delivery_date(choice)
    if not expected_date:reasons.append("integrity_outcome_expected_date_unknown")
    elif outcome.get("delivery_date")!=expected_date:reasons.append("integrity_outcome_delivery_date_mismatch")
    return reasons

def _validated_choice_row(cid, payload_raw, stored_hash):
    choice,reasons=_loads_payload(payload_raw,"integrity_choice_payload_invalid")
    if choice is not None:reasons+=_choice_integrity(cid,choice,stored_hash)
    return choice,reasons

def _truthy_flag(value):
    if isinstance(value,str):
        text=value.strip().lower()
        if text in ("", "0", "false", "no", "n", "off", "none", "null"):
            return False
        if text in ("1", "true", "yes", "y", "on", "synthetic"):
            return True
    return bool(value)

def native_nr(card):
    ident=card.get("identity") or {}
    return (not _truthy_flag(ident.get("is_synthetic",card.get("is_synthetic",False)))
            and (ident.get("event_type") or card.get("event_type"))=="NR_REPAIR_CONFIRMED"
            and str(ident.get("episode_id") or "").startswith("nr_")
            and not _truthy_flag(card.get("analysis_round")))

def _original_side(card):
    decision=card.get("decision") or {}
    fc=card.get("factor_cross_section") or {}
    edb=fc.get("edb") or {}
    lean=decision.get("lean",edb.get("lean"))
    hint=decision.get("side_hint",edb.get("side_hint"))
    if hint in ("PUT","PUT_CREDIT","put_credit","SELL_PUT"):return "put_credit"
    if hint in ("CALL","CALL_CREDIT","call_credit","SELL_CALL"):return "call_credit"
    if isinstance(lean,(int,float)) and not isinstance(lean,bool):
        return "put_credit" if lean>0 else "call_credit" if lean<0 else "neutral"
    return {"LONG":"put_credit","BULLISH":"put_credit","BULLISH_STRONG":"put_credit","BULLISH_WEAK":"put_credit",
            "SHORT":"call_credit","BEARISH":"call_credit","BEARISH_STRONG":"call_credit","BEARISH_WEAK":"call_credit",
            "NEUTRAL":"neutral"}.get(str(lean).upper(),"unknown")

def freeze_choice(card, review, frozen_at_ms):
    from astra_joint_card_statistics import card_id,card_time_ms,source_record_hash
    from signal_review_v2 import revalidate_review
    revalidate_review(card,review)
    context=review.get("statistical_context")
    assessment=(context or {}).get("assessment") or {}
    advisory=review.get("integrated_trade_advisory") or {}
    opinion=advisory.get("joint_review") or {}
    raw_joint_preference=opinion.get("recommendation")
    joint_preference=raw_joint_preference if opinion.get("status")=="ASSESSED" else "insufficient"
    qualification_reasons=[]
    if joint_preference in ("put_credit","call_credit"):
        side=(advisory.get("side_evidence_ratings") or {}).get(joint_preference) or {}
        if side.get("status")!="RATED" or not side.get("grade"):
            joint_preference="insufficient"
            qualification_reasons.append("recommended_side_not_locally_qualified")
    completed=[a.get("completed_at_ms") for a in review.get("call_audit",[]) if isinstance(a.get("completed_at_ms"),int)]
    completed_ms=max(completed) if completed else None
    if completed_ms is not None and completed_ms>frozen_at_ms:raise ValueError("future opinion")
    return {"schema":SCHEMA,"card_id":card_id(card),"as_of_ms":card_time_ms(card),
            "episode_id":(card.get("identity") or {}).get("episode_id"),
            "source_record_hash":source_record_hash(card),"review_hash":canonical_hash(review),
            "assessment_hash":assessment.get("assessment_hash"),"model_hash":(assessment.get("provenance") or {}).get("model_hash"),
            "frozen_at_ms":int(frozen_at_ms),"opinion_completed_ms":completed_ms,
            "statistical_preference":statistical_ranking(context),
            "joint_preference":joint_preference,"raw_joint_preference":raw_joint_preference,
            "joint_qualification_policy":"local_side_evidence_required@1.0.0",
            "joint_qualification_reasons":qualification_reasons,
            "original_preference":_original_side(card),"joint_opinion":opinion,
            "sides":assessment.get("sides") or {},"status":"available" if assessment.get("status")=="available" else "statistics_unavailable",
            "scope_cn":"原意见冻结后追加同组报价；未选与暂缓完整保留，不代表真实成交。"}

def _collect(choice, *, clock, transport):
    started=int(clock()); card_ms=choice["as_of_ms"]; opinion_ms=choice["opinion_completed_ms"]
    result={"schema":QUOTE_SCHEMA,"source_choice_hash":canonical_hash(choice),"requested_at_ms":started,
            "checked_at_ms":None,"http_attempts":0,"strict":False,"reasons":[],
            "card_to_quote_delay_ms":None,"opinion_to_quote_delay_ms":None,"sides":{},"raw_responses":{}}
    reasons=result["reasons"]
    if choice["status"]!="available":reasons.append("statistics_unavailable");return result
    if opinion_ms is None:reasons.append("opinion_time_unknown");return result
    if started<choice["frozen_at_ms"] or opinion_ms>started:raise ValueError("quote precedes frozen opinion")
    if started-card_ms>600000 or started-opinion_ms>60000:
        reasons.append("decision_quote_delayed");return result
    contracts={}
    for side in ("put","call"):
        data=choice["sides"].get(side) or {}
        ref=data.get("reference") or {}
        expiry=ref.get('expiry_ms')
        if isinstance(expiry,(int,float)) and not 8<(expiry-started)/3600000<=24:
            reasons.append('expiry_outside_research_window');return result
        for leg in ("short","long"):
            item=ref.get(leg+"_contract")
            if not isinstance(item,dict) or item.get("instrument_name")!=ref.get(leg+"_name"):
                reasons.append("instrument_metadata_missing");return result
            if item.get("creation_timestamp",float("inf"))>card_ms:
                reasons.append("instrument_created_after_card");return result
            if (item.get('kind')!='option' or item.get('option_type')!=side
                    or any(item.get(k)!='BTC' for k in ('base_currency','quote_currency','settlement_currency'))
                    or item.get('contract_size')!=1
                    or item.get('expiration_timestamp')!=ref.get('expiry_ms')
                    or item.get('strike')!=ref.get(leg+'_strike')):
                reasons.append('instrument_unit_or_geometry_unverified');return result
            contracts[item["instrument_name"]]=item
    amounts=[c.get("min_trade_amount") for c in contracts.values()]
    if len(contracts)!=4 or any(not isinstance(n,(int,float)) or isinstance(n,bool) or not math.isfinite(n) or n<=0 for n in amounts):
        reasons.append("common_quantity_unknown");return result
    unit=max(amounts);result["common_quantity"]=unit
    # Equal published minima allow a common minimum order without inferring
    # that a minimum is also a quantity increment.
    if any(not math.isclose(unit,n,rel_tol=1e-10) for n in amounts):
        reasons.append("common_quantity_incompatible");return result
    def fetch(name):
        try:
            payload,meta,raw=transport("https://www.deribit.com/api/v2/public/get_order_book",
                                      {"instrument_name":name,"depth":5})
            return name,payload,meta,None
        except Exception as exc:
            return name,None,{},type(exc).__name__
    with ThreadPoolExecutor(max_workers=4) as pool:
        records=list(pool.map(fetch,sorted(contracts)))
    result["http_attempts"]=len(records)
    books={}
    for name,payload,meta,error in records:
        result["raw_responses"][name]={"payload":payload,"receipt":meta,"error_type":error}
        if isinstance(payload,dict) and isinstance(payload.get("result"),dict):
            if payload['result'].get('instrument_name')==name:
                books[name]=payload["result"]
            else:reasons.append('book_instrument_identity_mismatch')
    checked=int(clock());result["checked_at_ms"]=checked
    result["card_to_quote_delay_ms"]=checked-card_ms
    result["opinion_to_quote_delay_ms"]=checked-opinion_ms
    if checked-card_ms>600000 or checked-opinion_ms>60000:reasons.append("decision_quote_delayed")
    group_error=validate_quote_group(set(contracts),books,checked)
    if group_error:reasons.append("group_quote_invalid")
    widths=[]
    for side in ("put","call"):
        ref=choice["sides"][side]["reference"];widths.append(ref["width"])
        short,long=contracts[ref["short_name"]],contracts[ref["long_name"]]
        quote=quote_side(side,short,long,books,checked,unit=unit,group_error_cn=group_error)
        quote["reference"]=ref
        remaining=(ref["expiry_ms"]-checked)/3600000
        if not 8<remaining<=24:reasons.append("expiry_outside_research_window")
        underlying=(books.get(ref["short_name"]) or {}).get("underlying_price")
        if not isinstance(underlying,(int,float)) or not math.isfinite(underlying) or underlying<=0:
            reasons.append("underlying_quote_unknown")
        elif (side=="put" and ref["short_strike"]>=underlying) or (side=="call" and ref["short_strike"]<=underlying):
            reasons.append("short_no_longer_otm")
        if quote.get("status")!="available":reasons.append("side_quote_unavailable")
        elif not quote.get("credit_eligible"):reasons.append("non_positive_credit")
        if (quote.get("delivery_fee_status") or {}).get("status")!="exempt_daily_option":
            reasons.append("delivery_fee_unknown")
        expected=choice["sides"][side].get("expected_payout_btc")
        if quote.get("status")=="available" and isinstance(expected,(int,float)):
            quote["expected_net_btc"]=quote["net_credit_btc"]-expected
        result["sides"][side]=quote
    result["same_actual_width"]=math.isclose(*widths,rel_tol=1e-10)
    result["strict"]=not reasons
    result["reasons"]=sorted(set(reasons))
    if result["strict"] and result["same_actual_width"] and all(isinstance(s.get("expected_net_btc"),(int,float)) for s in result["sides"].values()):
        result["economic_comparison"]=max(result["sides"],key=lambda side:result["sides"][side]["expected_net_btc"])
    return result

def record_and_quote(folder,card,review,*,clock=now_ms,transport=public_get):
    if not native_nr(card):return {"status":"not_native_nr","http_attempts":0}
    window_ledger=Ledger(folder);window=window_ledger.window();window_ledger.db.close()
    from astra_joint_card_statistics import card_time_ms,card_id
    asof=card_time_ms(card)
    if not window or not asof or not window["start_ms"]<=asof<window["end_ms"]:
        return {"status":"outside_forward_window","http_attempts":0}
    db=_connection(folder);cid=card_id(card)
    try:
        prior=db.execute("SELECT payload,hash FROM choices WHERE id=?",(cid,)).fetchone()
        if prior:
            choice,reasons=_validated_choice_row(cid,prior[0],prior[1])
            if reasons:
                return {"status":"integrity_gap","http_attempts":0,"reasons":reasons}
            if canonical_hash(review)!=choice["review_hash"]:raise ValueError("frozen review conflict")
        else:
            choice=freeze_choice(card,review,int(clock()))
            with db:db.execute("INSERT INTO choices VALUES(?,?,?)",(cid,_dumps(choice),canonical_hash(choice)))
        try:
            with db:db.execute("INSERT INTO quotes VALUES(?,?,?)",(cid,"reserved",None))
        except sqlite3.IntegrityError:
            row=db.execute("SELECT status,payload FROM quotes WHERE id=?",(cid,)).fetchone()
            quote,quote_reasons=(None,[])
            if row and row[0]=="complete":
                quote,quote_reasons=_loads_payload(row[1],"integrity_quote_payload_invalid")
            elif row and row[1]:
                quote,quote_reasons=_loads_payload(row[1],"integrity_quote_payload_invalid")
            if quote is not None:
                quote_reasons+=_quote_integrity(quote,canonical_hash(choice))
            if quote_reasons:
                return {"status":"integrity_gap","http_attempts":0,
                        "reasons":quote_reasons}
            return {"status":row[0],"http_attempts":0,"quote":quote}
        try:
            result=_collect(choice,clock=clock,transport=transport)
        except Exception as exc:
            result={"schema":QUOTE_SCHEMA,"strict":False,"reasons":["collection_failure"],
                    "error_type":type(exc).__name__,"source_choice_hash":canonical_hash(choice)}
        with db:db.execute("UPDATE quotes SET status=?,payload=? WHERE id=?",("complete",_dumps(result),cid))
        return {"status":"complete","http_attempts":result.get("http_attempts",0),"quote":result}
    finally:db.close()

def settle(folder, prices, recorded_ms, provenance):
    if provenance.get("kind")!="official_deribit_delivery":raise ValueError("official delivery identity required")
    db=_connection(folder);count=0
    try:
        rows=db.execute("SELECT c.id,c.payload,c.hash,q.payload FROM choices c JOIN quotes q ON c.id=q.id LEFT JOIN outcomes o ON o.id=c.id WHERE o.id IS NULL AND q.status='complete' AND q.payload IS NOT NULL").fetchall()
        for cid,cp,ch,qp in rows:
            choice,choice_reasons=_validated_choice_row(cid,cp,ch)
            if choice_reasons:continue
            quote,quote_reasons=_loads_payload(qp,"integrity_quote_payload_invalid")
            if quote_reasons or _quote_integrity(quote,canonical_hash(choice)):continue
            refs=[s.get("reference") for s in choice["sides"].values() if s.get("reference")]
            if not refs or any(r["expiry_ms"]>recorded_ms for r in refs):continue
            dates={_date(r["expiry_ms"]) for r in refs}
            if len(dates)!=1:continue
            date=next(iter(dates));price=prices.get(date)
            if not isinstance(price,(int,float)) or not math.isfinite(price) or price<=0:continue
            sides={}
            for side,data in choice["sides"].items():
                ref=data.get("reference")
                if not ref:continue
                paid=payout(side,ref["short_strike"],ref["long_strike"],price)
                q=(quote.get("sides") or {}).get(side) or {}
                credit=q.get("net_credit_btc")
                net=credit-paid if quote.get("strict") and credit is not None else None
                sides[side]={"payout_btc":paid,"loss_normalized":paid/(ref["width"]/ref["entry_price"]),
                    "net_btc":net,"win":net>0 if net is not None else None,
                    "protection_breached":price<ref["long_strike"] if side=="put" else price>ref["long_strike"]}
            result={"schema":"astra_nr_choice_outcome@1.0.0","choice_hash":canonical_hash(choice),
                    "quote_hash":canonical_hash(quote),"card_id":cid,
                    "delivery_date":date,"settlement_price":price,
                    "recorded_at_ms":recorded_ms,"provenance":provenance,"sides":sides,
                    "strict":bool(quote.get("strict")),"same_actual_width":quote.get("same_actual_width",False)}
            with db:db.execute("INSERT INTO outcomes VALUES(?,?)",(cid,_dumps(result)))
            count+=1
        return count
    finally:db.close()

def _request_collection_health(ledger, window, now):
    if not window:
        return {"schema":HEALTH_SCHEMA,"basis":"persistent_requests","status":"unknown",
                "reasons":["forward_window_missing"],"required_markets":list(REQUEST_MARKETS),
                "ended_7day_blocks":0,"healthy_complete_7day_blocks":None,
                "blocks":[],"request_days":[],
                "scope_cn":"没有冻结前向窗口，不能判断采集完整周。"}
    start=int(window["start_ms"]);end=int(window["end_ms"]);audit_end=min(int(now),end)
    ended=max(0,(audit_end-start)//(7*DAY))
    start_min=start//MINUTE
    days=[{"market":r[0],"utc_day":_date(int(r[1])*DAY),"attempted_minutes":int(r[2]),
           "completed_requests":int(r[3] or 0),"incomplete_requests":int(r[4] or 0)}
          for r in ledger.db.execute(
              """
              SELECT market,CAST(minute/1440 AS INTEGER),COUNT(*),
                     SUM(CASE WHEN status='complete' THEN 1 ELSE 0 END),
                     SUM(CASE WHEN status!='complete' THEN 1 ELSE 0 END)
              FROM requests
              WHERE market IN (?,?) AND minute>=? AND minute<?
              GROUP BY market,CAST(minute/1440 AS INTEGER)
              ORDER BY CAST(minute/1440 AS INTEGER),market
              """,
              (*REQUEST_MARKETS,start_min,(audit_end+MINUTE-1)//MINUTE),
          )]
    blocks=[];healthy=0
    for idx in range(ended):
        block_start=start+idx*7*DAY;block_end=block_start+7*DAY
        # Only complete minute buckets wholly inside the block are required.
        # An intraminute deployment start must not require a pre-start request.
        first=(block_start+MINUTE-1)//MINUTE;last=block_end//MINUTE
        expected=last-first
        market_status={};reasons=[]
        for market in REQUEST_MARKETS:
            total,complete=ledger.db.execute(
                """
                SELECT COUNT(*),
                       SUM(CASE WHEN status='complete' THEN 1 ELSE 0 END)
                FROM requests
                WHERE market=? AND minute>=? AND minute<?
                """,
                (market,first,last),
            ).fetchone()
            total=int(total or 0);complete=int(complete or 0)
            missing=max(0,expected-total);incomplete=max(0,total-complete)
            state="complete" if expected and missing==0 and incomplete==0 else "gap"
            if missing:reasons.append(f"{market}_missing_requests")
            if incomplete:reasons.append(f"{market}_incomplete_requests")
            market_status[market]={"expected_minutes":expected,"completed_requests":complete,
                                   "missing_requests":missing,"incomplete_requests":incomplete,
                                   "status":state}
        complete_block=bool(expected) and not reasons
        if complete_block:healthy+=1
        blocks.append({"index":idx,"start_ms":block_start,"end_ms":block_end,
                       "start_utc":datetime.fromtimestamp(block_start/1000,timezone.utc).isoformat(),
                       "end_utc":datetime.fromtimestamp(block_end/1000,timezone.utc).isoformat(),
                       "status":"healthy_complete" if complete_block else "not_complete",
                       "market_status":market_status,"reasons":sorted(set(reasons))})
    minimum=window.get("min_complete_weeks")
    qualified=isinstance(minimum,int) and healthy>=minimum
    return {"schema":HEALTH_SCHEMA,"basis":"persistent_requests","status":"qualified" if qualified else "not_qualified",
            "reasons":[] if qualified else ["insufficient_healthy_complete_week_blocks"],
            "required_markets":list(REQUEST_MARKETS),"window_start_ms":start,"window_end_ms":end,
            "audit_through_ms":audit_end,"ended_7day_blocks":ended,
            "healthy_complete_7day_blocks":healthy,"min_required_healthy_weeks":minimum,
            "blocks":blocks,"request_days":days,"data_verification_status":"not_evaluated_in_this_layer",
            "scope_cn":"健康完整周由持久化请求覆盖判断：完全落在已结束七日区块内的每个分钟桶，两市场均有complete记录；不要求窗口启动前或区块结束后的部分分钟。传输完成不单独证明全部数据完整，最新闭合分钟与实际特征另行校验。没有NR或只有一笔成交均不能证明完整周。"}

def report(folder, *, clock=now_ms, performance=True):
    db=_connection(folder)
    try:
        records=db.execute("SELECT c.id,c.payload,c.hash,q.status,q.payload,o.payload FROM choices c LEFT JOIN quotes q ON q.id=c.id LEFT JOIN outcomes o ON o.id=c.id").fetchall()
        buckets=Counter();coverage=Counter();rows=[];pending_delivery=False;now=int(clock())
        for cid,cp,ch,qs,qp,op in records:
            c,choice_reasons=_validated_choice_row(cid,cp,ch)
            if choice_reasons:
                for reason in choice_reasons:buckets[reason]+=1
                coverage["integrity_gap"]+=1
                continue
            q={}
            quote_reasons=[]
            if qs=="complete":
                q,quote_reasons=_loads_payload(qp,"integrity_quote_payload_invalid")
                if not quote_reasons:
                    quote_reasons=_quote_integrity(q,canonical_hash(c))
            elif qp:
                q,quote_reasons=_loads_payload(qp,"integrity_quote_payload_invalid")
            if quote_reasons:
                for reason in quote_reasons:buckets[reason]+=1
                coverage["integrity_gap"]+=1
                continue
            for reason in q.get("reasons",[]):buckets[reason]+=1
            if qs=="reserved":buckets["interrupted_reserved_no_retry"]+=1
            if op and qs!="complete":
                buckets["integrity_outcome_quote_status_mismatch"]+=1
                coverage["integrity_gap"]+=1
                continue
            if not op:
                coverage["pending_or_unavailable_outcome"]+=1
                refs=[s.get("reference") for s in c.get("sides",{}).values() if s.get("reference")]
                if any(r["expiry_ms"]>now for r in refs):pending_delivery=True
                else:coverage["mature_outcome_gap"]+=1
                continue
            o,outcome_reasons=_loads_payload(op,"integrity_outcome_payload_invalid")
            if not outcome_reasons:
                outcome_reasons=_outcome_integrity(cid,o,c,q)
            if outcome_reasons:
                for reason in outcome_reasons:buckets[reason]+=1
                coverage["integrity_gap"]+=1
                continue
            if not o.get("strict"):coverage["non_strict_quote"]+=1;continue
            coverage["strict_settled"]+=1
            if not o.get("same_actual_width"):coverage["unequal_width"]+=1;continue
            coverage["primary_pairs"]+=1
            if not performance:continue
            prefs={"statistics":c["statistical_preference"],"joint":c["joint_preference"],
                   "original":c["original_preference"],"fixed_put":"put_credit","fixed_call":"call_credit"}
            entry={"card_id":c["card_id"],"episode_id":c["episode_id"],"delivery_date":o["delivery_date"],"preferences":prefs,"sides":o["sides"]}
            rows.append(entry)
        from astra_joint_v11_evaluation import evaluate
        ledger=Ledger(folder);window=ledger.window()
        health=_request_collection_health(ledger,window,now)
        complete_blocks=health.get("healthy_complete_7day_blocks") if isinstance(health.get("healthy_complete_7day_blocks"),int) else None
        evaluation=evaluate(rows,complete_week_blocks=complete_blocks,
                            ended_week_blocks=health.get("ended_7day_blocks"),
                            collection_health_status=health.get("status")) if performance else None
        ledger.db.close()
        phase="not_started" if not window else "collecting" if now<window["end_ms"] else "waiting_last_delivery" if pending_delivery else "ready_for_final_audit"
        result={"schema":"astra_nr_forward_report@1.0.0","total_cards":len(records),"coverage":dict(coverage),
                "failure_buckets":dict(buckets),"window":window,"phase":phase,"collection_health":health,
                "elapsed_calendar_days":max(0,(min(now,window['end_ms'])-window['start_ms'])/DAY) if window else 0,
                "week_scope_cn":"已结束七日区块只表示时间已经走完；健康完整周必须由请求账本证明binance_um与binance_spot每个分钟桶均完成采集，不由NR数量或盈亏结果推断。",
                "conclusion_cn":"前向研究尚未结案；入选胜率、未选机会与尾损需共同评估。"}
        if performance:
            result.update(policies=evaluation['policies'],paired_rows=rows,paired_evaluation=evaluation)
        else:
            result['scope_cn']='常规发布只检查资料与采集覆盖；不计算或展示运行中绩效。'
        return result
    finally:db.close()

def seal_milestone(folder, milestone, *, clock=now_ms):
    """Explicit once-only day-30 health / day-90 final snapshots. No timer."""
    path=Path(folder)/('milestone-'+milestone+'.json')
    if path.exists():return json.loads(path.read_text(encoding='utf-8'))
    at=int(clock());ledger=Ledger(folder);window=ledger.window();ledger.db.close()
    if not window:raise ValueError('forward window has not started')
    if milestone=='day30':
        if at<window['quality_check_ms']:raise ValueError('day30 not reached')
        r=report(folder,clock=clock,performance=False)
        value={k:r[k] for k in ('window','total_cards','coverage','failure_buckets','collection_health')}
        value['scope_cn']='仅检查资料、覆盖与运行；不读取收益或按成绩调整模型。'
    elif milestone=='final':
        r=report(folder,clock=clock)
        if r['phase']!='ready_for_final_audit':raise ValueError('inclusion or last delivery not finished')
        value=r
        value['conclusion_cn']='资料不足，不能据此认定胜率改善。' if not r['paired_evaluation']['data_sufficient'] else '固定窗口统计已封存；请结合增量区间、平均净结果、尾损与覆盖率完成效果审查。'
    else:raise ValueError('unknown milestone')
    value.update(schema='astra_nr_milestone@1.0.0',milestone=milestone,sealed_at_ms=at)
    value['snapshot_hash']=canonical_hash(value)
    with path.open('x',encoding='utf-8') as f:f.write(_dumps(value))
    return value

def main():
    p=argparse.ArgumentParser();p.add_argument("--folder",type=Path,required=True);p.add_argument("--output",type=Path)
    p.add_argument('--seal',choices=('day30','final'))
    a=p.parse_args();result=seal_milestone(a.folder,a.seal) if a.seal else report(a.folder,performance=False)
    if a.output:
        a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(_dumps(result),encoding="utf-8")
    print(_dumps({k:v for k,v in result.items() if k!="paired_rows"}))
if __name__=="__main__":main()
