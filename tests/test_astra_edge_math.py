import math,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import pytest
import astra_edge_math as m

def test_inverse_piecewise_and_continuity():
    assert m.payout('put_credit',80000,79000,77000)==0
    assert m.payout('put_credit',78000,79000,77000)==pytest.approx(1000/78000)
    assert m.payout('put_credit',40000,79000,77000)==.05
    assert m.payout('call_credit',82000,81000,83000)==pytest.approx(1000/82000)
    for side,k,l in [('put_credit',79000,77000),('call_credit',81000,83000)]:
        for s in (k,l):assert m.payout(side,s-1e-6,k,l)==pytest.approx(m.payout(side,s+1e-6,k,l),abs=1e-10)

def test_breakevens_and_extreme_call_second_profitable_region():
    credit=.0025
    assert m.payout('put_credit',79000/(1+credit),79000,77000)==pytest.approx(credit)
    assert m.payout('call_credit',81000/(1-credit),81000,83000)==pytest.approx(credit)
    assert m.payout('call_credit',800000,81000,83000)==pytest.approx(credit)
    assert m.payout('call_credit',1000000,81000,83000)<credit
    assert m.payout('call_credit',100000,81000,83000)>credit

def test_dual_credit_can_rescue_small_side_loss():
    s=81300;credit=.0025
    cp=m.payout('call_credit',s,81000,83000);pp=m.payout('put_credit',s,79000,77000)
    assert credit-cp<0
    assert 2*credit-cp-pp>0

def test_closed_form_matches_deterministic_quadrature():
    # Equal-probability normal quantiles integrate assumed distribution independently of closed form.
    for side,k in [('put_credit',.9875),('call_credit',1.0125)]:
        for mu,sd in [(0,.012),(-.015,.025),(.02,.02)]:
            n=20000;w=.025
            values=[m.payout(side,math.exp(mu+sd*m.N.inv_cdf((i+.5)/n)),k,k-w if side=='put_credit' else k+w)/w for i in range(n)]
            assert sum(values)/n==pytest.approx(m.expected_normalized_payout(side,k,w,mu,sd),abs=2e-5)
            win=sum(v<.1 for v in values)/n
            assert win==pytest.approx(m.win_probability(side,k,w,.1,mu,sd),abs=1e-4)

def test_ou_is_conditional_assumption_and_zero_kappa_limit():
    assert m.ou_log_return_params(80000,79000,0,12,.002)==pytest.approx((0,.002*math.sqrt(12)))
    mu,sd=m.ou_log_return_params(80000,79000,.1,12,.002)
    assert mu<0 and sd<.002*math.sqrt(12)

def test_high_win_rate_not_positive_expectation():
    pnl=[.0025]*9+[-.05]
    assert sum(v>0 for v in pnl)/len(pnl)==.9
    assert sum(pnl)<0

def test_high_credit_and_discrete_tie():
    # Unusually high credit moves Put BE below the protective leg; Call never loses.
    assert m.win_probability('put_credit',1,.1,2,math.log(.5),0)==0
    assert m.win_probability('put_credit',1,.1,2,math.log(.51),0)==1
    assert m.win_probability('call_credit',1,.1,2,0,.5)==1
    assert m.win_probability('call_credit',1,.1,0,0,.5)==0

def test_exact_formula_extreme_distribution():
    n=20000
    for side,k in [('put_credit',1),('call_credit',1)]:
        mu=.4;sd=.7;w=.1
        avg=sum(m.payout(side,math.exp(mu+sd*m.N.inv_cdf((i+.5)/n)),k,k-w if side=='put_credit' else k+w)/w for i in range(n))/n
        assert avg==pytest.approx(m.expected_normalized_payout(side,k,w,mu,sd),abs=.0003)
