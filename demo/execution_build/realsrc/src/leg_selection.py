# -*- coding: utf-8 -*-
"""
选腿（legsel_*）：把方向 + DTE/Delta 范围映射为具体的「行权价 / 到期 / 合约」。

本模块为纯逻辑：输入交易所返回的合约列表与盘口，输出选腿结果，便于本地单测。
到期一律用合约自带的 expiration_timestamp，不靠解析合约名。
"""


def legsel_is_call_bias(direction_bias):
    return direction_bias == "SHORT_CALL"


def legsel_dte_hours(expiration_timestamp_ms, now_ms):
    return (expiration_timestamp_ms - now_ms) / 3600000.0


def _opt_type_match(inst, want_call):
    t = (inst.get("option_type") or "").lower()
    return (t == "call") if want_call else (t == "put")


def legsel_pick_expiry_instruments(instruments, dte_min_h, dte_max_h, center_h,
                                   now_ms, want_call):
    """选 DTE 落在 [dte_min_h, dte_max_h] 内、最接近 center_h 的**实际可用到期**，
    返回 (chosen_exp_ms, [该到期且方向匹配的合约])；无可用到期返回 (None, [])。"""
    by_exp = {}
    for inst in instruments:
        if not _opt_type_match(inst, want_call):
            continue
        exp = inst.get("expiration_timestamp")
        if exp is None:
            continue
        dte = legsel_dte_hours(exp, now_ms)
        if dte_min_h <= dte <= dte_max_h:
            by_exp.setdefault(exp, []).append(inst)
    if not by_exp:
        return None, []
    chosen = min(by_exp.keys(),
                 key=lambda e: abs(legsel_dte_hours(e, now_ms) - center_h))
    return chosen, by_exp[chosen]


def legsel_expiries_in_band(instruments, dte_min_h, dte_max_h, now_ms, want_call):
    """返回 {expiration_timestamp: [该到期且方向匹配的合约]}，覆盖 DTE 区间内的**所有**到期。"""
    by_exp = {}
    for inst in instruments:
        if not _opt_type_match(inst, want_call):
            continue
        exp = inst.get("expiration_timestamp")
        if exp is None:
            continue
        if dte_min_h <= legsel_dte_hours(exp, now_ms) <= dte_max_h:
            by_exp.setdefault(exp, []).append(inst)
    return by_exp


def _otm_side_ok(strike, spot, want_call):
    """call 卖在现价上方、put 卖在现价下方（OTM 侧）。"""
    return strike > spot if want_call else strike < spot


def legsel_short_enriched(short_insts, spot, want_call, delta_of, scan_limit=15):
    """OTM 侧、距现价由近到远取前 scan_limit 档，并附 _delta（供按目标 delta 选档）。"""
    otm = [i for i in short_insts
           if i.get("strike") is not None and _otm_side_ok(i["strike"], spot, want_call)]
    otm.sort(key=lambda i: abs(i["strike"] - spot))
    enriched = []
    for i in otm[:scan_limit]:
        d = delta_of(i.get("instrument_name"))
        if d is None:
            continue
        j = dict(i)
        j["_delta"] = d
        enriched.append(j)
    return enriched


def legsel_pick_nearest_delta(enriched, target_delta):
    """在 enriched 短腿候选中选 |delta| 最接近 target_delta 的档（卖权利金主驱动）。
    返回选中合约(含 _delta) 或 None。"""
    if not enriched:
        return None
    return min(enriched, key=lambda i: abs(abs(i["_delta"]) - target_delta))


def legsel_protection_candidates(prot_insts, short_strike, want_call, width_band,
                                 delta_of=None, deep_otm_max_delta=0.05):
    """保护腿候选（以短腿行权价为基准、按腿宽选择；日历与同期垂直通用）：
      - call: strike > short_strike；put: strike < short_strike（更外侧）
      - 腿宽 = |strike - short_strike| 优先落在 width_band；排除过度虚值(|delta|<deep_otm)
      - 排序：腿宽最接近区间中心者优先；带外档作兜底排后
        （供 spm_evaluate_candidates 逐个验证保证金释放，取首个达标）。
    返回有序候选合约列表（每项含 _width）。"""
    wlo, whi = width_band
    wcenter = (wlo + whi) / 2.0
    in_band, others = [], []
    for i in prot_insts:
        s = i.get("strike")
        if s is None:
            continue
        outside = (s > short_strike) if want_call else (s < short_strike)
        if not outside:
            continue
        if delta_of is not None:
            d = delta_of(i.get("instrument_name"))
            if d is not None and abs(d) < deep_otm_max_delta:
                continue  # 过度虚值的灾难彩票腿
        rec = dict(i)
        rec["_width"] = abs(s - short_strike)
        (in_band if wlo <= rec["_width"] <= whi else others).append(rec)

    in_band.sort(key=lambda rec: abs(rec["_width"] - wcenter))
    others.sort(key=lambda rec: abs(rec["_width"] - wcenter))
    return in_band + others
