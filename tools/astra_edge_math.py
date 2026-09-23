"""Exact inverse-spread identities and illustrative distribution scenarios; no fitted forecasts."""
from __future__ import annotations
import argparse,math,json
from pathlib import Path
from statistics import NormalDist
from astra_second_report import write_csv

N=NormalDist()

def payout(side,spot,short,long):
    if spot<=0 or short<=0 or long<=0:raise ValueError('positive prices required')
    if side=='put_credit' and long<short:return (max(short-spot,0)-max(long-spot,0))/spot
    if side=='call_credit' and long>short:return (max(spot-short,0)-max(spot-long,0))/spot
    raise ValueError('side / protective ordering')

def expected_normalized_payout(side,k,w,mu,sigma):
    """R=ST/S0 lognormal(mu,sigma^2); payout/(W/S0). sigma is horizon log SD."""
    if w<=0 or k<=0 or sigma<0 or (side=='put_credit' and k<=w):raise ValueError('invalid normalized legs / scale')
    if sigma==0:return payout(side,math.exp(mu),k,k-w if side=='put_credit' else k+w)/w
    def low(a):
        z=(math.log(a)-mu)/sigma
        return N.cdf(z),math.exp(-mu+sigma*sigma/2)*N.cdf(z+sigma)
    def high(a):
        z=(math.log(a)-mu)/sigma
        return N.cdf(-z),math.exp(-mu+sigma*sigma/2)*N.cdf(-z-sigma)
    if side=='put_credit':
        pk,ik=low(k);pl,il=low(k-w)
        return max(0,(k*ik-pk-(k-w)*il+pl)/w)
    if side=='call_credit':
        pk,ik=high(k);pl,il=high(k+w)
        return max(0,(pk-k*ik-pl+(k+w)*il)/w)
    raise ValueError('unknown side')

def win_probability(side,k,w,q,mu,sigma):
    credit=q*w
    if credit<0:raise ValueError('nonnegative credit required')
    if sigma==0:
        loss=payout(side,math.exp(mu),k,k-w if side=='put_credit' else k+w)
        return float(credit>loss and not math.isclose(credit,loss,rel_tol=1e-12,abs_tol=1e-12))
    if credit==0:return 0.0
    def cdf(a):return N.cdf((math.log(a)-mu)/sigma)
    if side=='put_credit':
        threshold=k/(1+credit) if credit<w/(k-w) else w/credit
        return 1-cdf(threshold)
    if side=='call_credit':
        if credit>=w/(k+w):return 1.0 # equality is one point, zero probability in continuous distribution
        return cdf(k/(1-credit))+1-cdf(w/credit)
    raise ValueError('unknown side')

def ou_log_return_params(spot,anchor,kappa,hours,sigma_hour):
    """Hypothetical fixed-anchor OU, not evidence that an actual anchor has restoring force."""
    if min(spot,anchor)<=0 or min(kappa,hours,sigma_hour)<0:raise ValueError('invalid OU inputs')
    if kappa==0:return 0.0,sigma_hour*math.sqrt(hours)
    e=math.exp(-kappa*hours)
    return -(1-e)*math.log(spot/anchor),sigma_hour*math.sqrt((1-e*e)/(2*kappa))

def scenarios(out):
    spot=80000;w=2000/spot;q=.1;lam=.9516590272
    settings=[('中性均值／较窄分布',0,.006),('中性均值／较宽分布',0,.025),
      ('向上偏移／相同离散度',.015,.012),('向下偏移／相同离散度',-.015,.012),('无方向偏移／相同离散度',0,.012)]
    rows=[]
    for label,mu,sigma in settings:
        for side,k in [('put_credit',79000/spot),('call_credit',81000/spot)]:
            cost=expected_normalized_payout(side,k,w,mu,sigma)
            rows.append({'scenario':label,'side':side,'entry_price':spot,'short_strike':k*spot,'width':2000,
              'q':q,'log_return_mean':mu,'log_return_sd':sigma,'required_q':cost,
              'net_profit_probability':win_probability(side,k,w,q,mu,sigma),'expected_margin_roi':(q-cost)/lam,
              'scope':'illustrative assumed lognormal distribution; not estimated from BTC or a trading forecast'})
    out.mkdir(parents=True,exist_ok=True);write_csv(out/'illustrative_distribution_scenarios.csv',rows)
    print(json.dumps(rows,ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();scenarios(a.output)
