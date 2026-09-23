"""Date-weighted paired research evaluation; no model selection or trading."""
from __future__ import annotations
from collections import defaultdict
from datetime import date,timedelta
import math
import random

def _side(row, policy):
    pref=row["preferences"].get(policy,"")
    return row["sides"].get(pref.removesuffix("_credit"))

def _mean(xs):
    return sum(xs)/len(xs) if xs else None

def _quantile(values,q):
    if not values:return None
    ordered=sorted(values);i=(len(ordered)-1)*q;lo=int(i);hi=math.ceil(i)
    return ordered[lo]+(ordered[hi]-ordered[lo])*(i-lo)

def weighted_es(values, mass=.05):
    if not values:return None
    total=sum(w for x,w in values);needed=total*mass;remaining=needed;result=0
    for loss,weight in sorted(values,reverse=True):
        take=min(weight,remaining);result+=loss*take;remaining-=take
        if remaining<=1e-15:break
    return result/needed if needed>0 else None

def evaluate(rows,repetitions=2000,seed=20260915,complete_week_blocks=None,ended_week_blocks=None,collection_health_status=None):
    byday=defaultdict(list)
    for row in rows:byday[row["delivery_date"]].append(row)
    policies={}
    names=("statistics","joint","original","fixed_put","fixed_call","equal_single_side")
    for policy in names:
        selected=[];allmass=0;net_total=0
        for group in byday.values():
            w=1/len(group)
            for row in group:
                allmass+=w
                if policy == "equal_single_side":
                    # A precommitted 50/50 single-side mixture, not two spreads.
                    for s in row["sides"].values():
                        selected.append((s,w/2));net_total+=w/2*s["net_btc"]
                    continue
                s=_side(row,policy)
                if s is not None:
                    selected.append((s,w));net_total+=w*s["net_btc"]
        mass=sum(w for s,w in selected)
        losses=[(s,w) for s,w in selected if s["net_btc"]<0]
        policies[policy]={"selected_cards":len(rows) if policy == "equal_single_side" else len(selected),"coverage":mass/allmass if allmass else None,
            "win_rate":sum(w*(s["net_btc"]>0) for s,w in selected)/mass if mass else None,
            "mean_net_btc":sum(w*s["net_btc"] for s,w in selected)/mass if mass else None,
            "net_per_original_opportunity_btc":net_total/allmass if allmass else None,
            "average_loss_btc":sum(w*abs(s["net_btc"]) for s,w in losses)/sum(w for s,w in losses) if losses else None,
            "unconditional_es95_btc":weighted_es([(max(-s["net_btc"],0),w) for s,w in selected]),
            "protection_breach_rate":sum(w*s["protection_breached"] for s,w in selected)/mass if mass else None}
    def paired(a,b):
        days={};count=0
        for key,group in byday.items():
            pairs=[(_side(row,a),_side(row,b)) for row in group]
            pairs=[(x,y) for x,y in pairs if x is not None and y is not None]
            count+=len(pairs)
            if pairs:days[key]=(sum(int(x["win"])-int(y["win"]) for x,y in pairs)/len(group),len(pairs)/len(group))
        if not days:return {"paired_cards":0,"delivery_days":0,"delta_win_pp":None,"ci95_pp":None,"p_value":None}
        delta=sum(x[0] for x in days.values())/sum(x[1] for x in days.values())
        lo=date.fromisoformat(min(byday));hi=date.fromisoformat(max(byday))
        calendar=[(lo+timedelta(days=i)).isoformat() for i in range((hi-lo).days+1)]
        def sample(block):
            rng=random.Random(seed+block);draws=[]
            for _ in range(repetitions):
                picked=[]
                while len(picked)<len(calendar):
                    length=min(block,len(calendar))
                    start=rng.randrange(len(calendar)-length+1)
                    picked.extend(calendar[start:start+length])
                total=mass=0
                for key in picked[:len(calendar)]:
                    v=days.get(key,(0,0));total+=v[0];mass+=v[1]
                if mass:draws.append(total/mass)
            return draws
        daily=sample(1);weekly=sample(7)
        p=min(1,2*min((1+sum(x<=0 for x in weekly))/(len(weekly)+1),
                     (1+sum(x>=0 for x in weekly))/(len(weekly)+1))) if weekly else None
        return {"paired_cards":count,"delivery_days":len(days),"delta_win_pp":100*delta,
                "ci95_pp":[100*_quantile(weekly,q) for q in (.025,.975)] if weekly else None,
                "daily_ci95_pp":[100*_quantile(daily,q) for q in (.025,.975)] if daily else None,"p_value":p}
    comparisons={"statistics_minus_original":paired("statistics","original"),
                 "joint_minus_statistics_selected":paired("joint","statistics")}
    running=0
    ordered=sorted((v["p_value"],k) for k,v in comparisons.items() if v["p_value"] is not None)
    for i,(p,key) in enumerate(ordered):
        running=max(running,min(1,(2-i)*p));comparisons[key]["holm_p"]=running
    selected_stats=[];withheld_stats=[];selected_joint=[]
    for group in byday.values():
        w=1/len(group)
        for row in group:
            s=_side(row,"statistics");j=_side(row,"joint")
            if s:
                (selected_stats if j else withheld_stats).append((s,w))
                if j:selected_joint.append((j,w))
    weighted_win=lambda items:sum(w*s["win"] for s,w in items)/sum(w for s,w in items) if items else None
    stat_all=weighted_win(selected_stats+withheld_stats);stat_selected=weighted_win(selected_stats);joint_selected=weighted_win(selected_joint)
    selection={
        "statistics_win_all":stat_all,"statistics_win_joint_selected":stat_selected,
        "joint_win_selected":joint_selected,
        "filtering_delta_pp":100*(stat_selected-stat_all) if stat_selected is not None and stat_all is not None else None,
        "side_change_delta_pp":100*(joint_selected-stat_selected) if joint_selected is not None and stat_selected is not None else None,
        "withheld_cards":len(withheld_stats),
        "foregone_positive_btc_weighted":sum(w*max(s["net_btc"],0) for s,w in withheld_stats)/len(byday) if byday else None,
        "avoided_loss_btc_weighted":sum(w*max(-s["net_btc"],0) for s,w in withheld_stats)/len(byday) if byday else None}
    # A day with no NR event is not a data outage. Week completeness must
    # come from collection health, never be inferred from winning/trading days.
    return {"schema":"astra_nr_paired_evaluation@1.0.0","delivery_days":len(byday),
        "ended_7day_blocks":ended_week_blocks,"complete_calendar_weeks":complete_week_blocks,
        "collection_health_status":collection_health_status,
        "data_sufficient":len(byday)>=60 and complete_week_blocks is not None and complete_week_blocks>=12,"policies":policies,
        "primary_comparisons":comparisons,"selection_decomposition":selection,
        "weighting_cn":"先固定每个原始交割日等权，日内卡等权；选择后按原权重重新归一，不重设入选日期权重。",
        "claim_cn":"净结果为同期报价支持的持有到期纸面结果，非真实成交收益；未证明胜率增量，需固定窗口结案。"}
