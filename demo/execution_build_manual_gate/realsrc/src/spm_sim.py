# -*- coding: utf-8 -*-
"""
S:PM 保证金模拟校验（spm_*，§7）。

抵消机制已联网取证确认可行（同币种同子账户跨到期 netting）；本模块只**逐笔确认幅度**：
比较 B(仅 short) 与 C(short+protection) 两个模拟场景的 IM，看远期保护腿是否带来足够的
保证金释放。逻辑保持简单，不做额外复杂回路。
"""

from deribit_io import dbt_simulate_portfolio  # bundle 时剥离


# ---------- 纯计算 ----------

def spm_relief(im_b, im_c):
    """返回 {relief_abs, relief_ratio}。im_b<=0 时 ratio=0（无意义）。"""
    if im_b is None or im_c is None:
        return {"relief_abs": None, "relief_ratio": None}
    relief_abs = im_b - im_c
    ratio = (relief_abs / im_b) if im_b > 0 else 0.0
    return {"relief_abs": relief_abs, "relief_ratio": ratio}


def spm_account_is_portfolio_margin(account_summary):
    """校验账户确为组合保证金（S:PM）。返回 (ok, model_str)。"""
    if not account_summary:
        return False, None
    model = account_summary.get("margin_model")
    pm_flag = account_summary.get("portfolio_margining_enabled")
    ok = bool(pm_flag) or (model is not None and "pm" in str(model).lower())
    return ok, model


# ---------- 调交易所模拟 ----------

def _im(sim_result):
    return None if not sim_result else sim_result.get("initial_margin")


def spm_simulate_structure(currency, short_instrument, protection_instrument, amount):
    """模拟 B(+short) 与 C(+short+protection)，返回完整报告 dict。"""
    sim_b = dbt_simulate_portfolio(currency, {short_instrument: -amount})
    sim_c = dbt_simulate_portfolio(
        currency, {short_instrument: -amount, protection_instrument: +amount})
    im_b, im_c = _im(sim_b), _im(sim_c)
    rep = spm_relief(im_b, im_c)
    rep.update({
        "short_instrument": short_instrument,
        "protection_instrument": protection_instrument,
        "amount": amount,
        "im_short_only": im_b,
        "im_with_protection": im_c,
        "mm_short_only": (sim_b or {}).get("maintenance_margin"),
        "mm_with_protection": (sim_c or {}).get("maintenance_margin"),
        "available_funds_b": (sim_b or {}).get("available_funds"),
        "available_funds_c": (sim_c or {}).get("available_funds"),
    })
    return rep


def spm_evaluate_candidates(currency, short_instrument, prot_candidates, amount,
                            min_ratio):
    """按顺序模拟保护腿候选（已按「锚点→逐档靠近 short」排序），
    返回第一个 relief_ratio >= min_ratio 的报告（含 accepted=True）；
    全不达标则返回最后一次尝试 + accepted=False。attempts 记录全过程。"""
    attempts = []
    best = None
    for prot in prot_candidates:
        inst = prot.get("instrument_name") if isinstance(prot, dict) else prot
        rep = spm_simulate_structure(currency, short_instrument, inst, amount)
        attempts.append(rep)
        ratio = rep.get("relief_ratio")
        if ratio is not None and (best is None or ratio > (best.get("relief_ratio") or -1)):
            best = rep
        if ratio is not None and ratio >= min_ratio:
            rep["accepted"] = True
            rep["attempts"] = attempts
            return rep
    if best is None:
        best = {"accepted": False, "attempts": attempts, "relief_ratio": None}
    else:
        best = dict(best)
        best["accepted"] = False
    best["attempts"] = attempts
    return best
