# -*- coding: utf-8 -*-
"""
前端中文显示（disp_*）：把进场上下文 ctx 渲染为 FMZ LogStatus 表格 + 简明事件日志。

LogStatus 表格格式：`{"type":"table","title":..,"cols":[..],"rows":[[..]]}`，
用反引号包裹 JSON；多表用数组；表外文本可用 `文本#ff0000` 着色。
全部为纯函数，便于单测。
"""
import json

# ---- 文案映射 ----
SIGNAL_CN = {
    "TRADE_SUPPORT_STRONG": "强支持(放行)",
    "TRADE_SUPPORT_WEAK": "弱支持(放行)",
    "WAIT_CONFIRMATION": "待确认(不进场)",
    "NO_TRADE_AMBIGUOUS": "歧义·不交易",
    "NO_TRADE_BLOCKED": "阻断·不交易",
}
BIAS_CN = {
    "SHORT_CALL": "偏空 · 卖出看涨 Call",
    "SHORT_PUT": "偏多 · 卖出看跌 Put",
}
STATE_CN = {
    "NO_POSITION": "无持仓", "SIGNAL_READY": "信号就绪",
    "PROTECTION_SELECTION": "选保护腿", "SPM_SIMULATION": "S:PM 模拟",
    "PROTECTION_BUILDING": "建保护腿", "PROTECTION_ACTIVE_NO_SHORT": "保护腿就绪·未建卖方腿",
    "SHORT_BUILDING": "建卖方腿", "SHORT_ACTIVE_PROTECTED": "已保护·卖方持仓",
    "HOLD_MONITORING": "持仓监控", "SHORT_EXPIRED_OR_CLOSED": "卖方腿到期/平仓",
    "REUSE_DECISION": "复用决策", "EXIT_OR_WAIT_REVIEW": "退出/复核", "CLOSED": "已了结",
}
REASON_CN = {
    "DRY_RUN_PLAN_ONLY": "空跑：仅生成方案，未真实下单",
    "STRUCTURE_OPEN": "结构已建立（保护腿 + 卖方腿成交）",
    "PROTECTION_ACTIVE_NO_SHORT": "保护腿已建，卖方腿未成交（等待/人工）",
    "PROTECTION_NOT_FILLED": "保护腿未成交，已放弃本次",
    "MARGIN_RELIEF_INSUFFICIENT": "保证金释放不足，未达门槛，已放弃",
    "ACCOUNT_NOT_PM": "账户非组合保证金(S:PM)，已放弃",
    "NO_SPOT": "无法获取参考价",
    "NO_INSTRUMENTS": "未取到期权合约列表",
    "NO_SHORT_EXPIRY_IN_BAND": "近端到期不在设定区间(24–72h)",
    "NO_SHORT_STRIKE": "近端无合适行权价",
    "NO_PROTECTION_EXPIRY_IN_BAND": "保护腿到期不在设定区间(5–10d)",
    "NO_PROTECTION_CANDIDATE": "无合格保护腿候选（可能均过度虚值）",
    "NO_CANDIDATE": "无任何符合范围的备选（检查 delta/腿宽/到期范围）",
    "SAME_DIRECTION_CONFIRMATION": "持仓中：同向信号仅确认，不加仓",
    "PLAN_MENU_READY": "计划轮：方案库已生成（设 ROUND_MODE=ORDER + SELECTED_PLAN 进入下单轮）",
    "NO_PLAN_MENU(请先运行计划轮)": "下单轮：未找到方案库，请先以 ROUND_MODE=PLAN 运行计划轮",
    "ORDER_PREVIEW_DRY": "下单轮·空跑预览：已复核选用方案，未真实下单（置 ALLOW_TRADING=True 开仓）",
}

_C_GREEN = "#16a34a"
_C_ORANGE = "#c2410c"
_C_RED = "#dc2626"
_C_GRAY = "#64748b"


def disp_signal_cn(s):
    return SIGNAL_CN.get(s, s)


def disp_decision_hint(signal_state):
    """按信号强弱给选档指引，降低认知负荷（不替操作者决策）。"""
    if signal_state == "TRADE_SUPPORT_STRONG":
        return "信号偏强 → 可选「高盈亏比/高期望」档（承受较低胜率换更大盈亏比）"
    if signal_state == "TRADE_SUPPORT_WEAK":
        return "信号偏弱 → 优先「高胜率/均衡」档（求稳，降低被行权概率）"
    return "—"


def disp_state_cn(s):
    return STATE_CN.get(s, s)


def disp_reason_cn(reason):
    if reason is None:
        return "—"
    if reason in REASON_CN:
        return REASON_CN[reason]
    if reason.startswith("EXIT_REVIEW_SIGNAL:"):
        sig = reason.split(":", 1)[1]
        return "退出/复核信号（%s），不再续卖新卖方腿" % disp_signal_cn(sig)
    if reason.startswith("IDLE"):
        return "空闲：不满足进场条件 " + reason[4:]
    if reason.startswith("PLAN_NOT_QUALIFIED"):
        tail = reason.split(":", 1)[1] if ":" in reason else ""
        return "选用方案复核不合格" + (("：" + tail) if tail else "")
    if reason.startswith("PLAN_NOT_IN_MENU"):
        return "方案号不在方案库内：" + (reason.split(":", 1)[1] if ":" in reason else "")
    if reason.startswith("PLAN_MENU_READY"):
        return REASON_CN["PLAN_MENU_READY"] + reason[len("PLAN_MENU_READY"):]
    return reason


def _num(x, small=8, big=4):
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "是" if x else "否"
    if isinstance(x, (int, float)):
        fmt = ("%%.%df" % (small if abs(x) < 1 else big)) % x
        return fmt.rstrip("0").rstrip(".") if "." in fmt else fmt
    return str(x)


def _usd(btc_val, spot):
    if btc_val is None or spot is None or not isinstance(btc_val, (int, float)):
        return "—"
    return "≈$%.2f" % (btc_val * spot)


def _btc_usd(btc_val, spot):
    return "%s BTC  %s" % (_num(btc_val), _usd(btc_val, spot))


def _usd0(btc_val, spot):
    """紧凑 USD（菜单用，BTC 数值过小不便肉眼比较）。"""
    if btc_val is None or spot is None or not isinstance(btc_val, (int, float)):
        return "—"
    return "$%.0f" % (btc_val * spot)


def _dist_pct(strike, spot):
    """行权价距现价百分比（带符号：上方+ / 下方−），快速判断虚值度。"""
    if strike is None or spot is None or not spot:
        return "—"
    return "%+.1f%%" % ((strike - spot) / spot * 100.0)


# ---- 健康度 / 合理性自检 ----

_NEAR_SPOT_PCT = 1.5      # 短腿距现价过近阈值(%)：高被行权风险
_HIGH_DELTA = 0.45        # 短腿 delta 偏高阈值
_LOW_RR = 0.20            # 盈亏比偏低阈值
_LOW_RELIEF = 0.20        # S:PM 释放偏低阈值
_GRADE_RANK = {"警示": 3, "提示": 2, "通过": 1}


def disp_health_notes(ctx):
    """对选用方案做综合审查，返回 [(级别, 说明)]；级别 警示>提示>通过。"""
    notes = []
    g = ctx.get
    spot = g("spot")
    ss = g("short_strike")
    # 1) 流动性 / 成交可行性
    if g("short_bid") in (None, 0):
        notes.append(("警示", "短腿无买盘(best_bid=0)：maker 卖单可能无法成交"))
    # 2) 短腿距现价过近 → 高被行权风险
    if isinstance(ss, (int, float)) and spot:
        dist = abs(ss - spot) / spot * 100.0
        if dist < _NEAR_SPOT_PCT:
            notes.append(("警示", "短腿距现价仅 %.1f%%(<%.1f%%)：过近，被行权/被突破风险高"
                          % (dist, _NEAR_SPOT_PCT)))
    # 3) 权利金 vs 手续费
    prem, fee = g("short_premium_income"), g("estimated_entry_fee")
    if isinstance(prem, (int, float)) and isinstance(fee, (int, float)) and prem <= fee:
        notes.append(("警示", "卖方权利金 ≤ 预估手续费，单笔净收益非正"))
    # 4) 短腿 delta 偏高(偏激进)
    sd = g("short_delta")
    if isinstance(sd, (int, float)) and abs(sd) > _HIGH_DELTA:
        notes.append(("提示", "短腿 |delta|=%.2f 偏高(>%.2f)：偏激进、胜率偏低" % (abs(sd), _HIGH_DELTA)))
    # 5) EV(最坏口径)为负
    ev = g("ev")
    if isinstance(ev, (int, float)) and ev < 0:
        notes.append(("提示", "EV(最坏口径)为负：单周期纯概率期望不利，正 edge 依赖方向论证 + 复用摊薄"))
    # 6) 盈亏比偏低
    rr = g("rr")
    if isinstance(rr, (int, float)) and rr < _LOW_RR:
        notes.append(("提示", "盈亏比 %.2f 偏低(<%.2f)：收益对风险偏薄" % (rr, _LOW_RR)))
    # 7) S:PM 释放偏低(虽达标)
    ratio, minr = g("margin_relief_ratio"), g("min_required_ratio")
    if isinstance(ratio, (int, float)) and isinstance(minr, (int, float)) \
            and minr <= ratio < _LOW_RELIEF:
        notes.append(("提示", "S:PM 释放 %.0f%% 偏低：达标但保证金缓释有限" % (ratio * 100)))
    # 8) 保护成本 / 权利金倍数
    pc = g("protection_entry_cost")
    if isinstance(pc, (int, float)) and isinstance(prem, (int, float)) and prem > 0 and pc / prem >= 5:
        notes.append(("提示", "保护腿成本为权利金的 %.1f 倍，需靠复用/残值摊薄" % (pc / prem)))
    # 9) 保护腿 delta 偏低
    pdelta = g("protection_delta")
    if isinstance(pdelta, (int, float)) and abs(pdelta) < 0.08:
        notes.append(("提示", "保护腿 |delta|=%.3f 偏低：偏经济型保护而非强对冲" % abs(pdelta)))
    if not notes:
        notes.append(("通过", "综合校验通过：流动性/距离/权利金/释放/盈亏比均合理"))
    return notes


def disp_health_grade(ctx):
    """综合评级：取所有检查中的最严级别。"""
    notes = disp_health_notes(ctx)
    worst = max(notes, key=lambda n: _GRADE_RANK.get(n[0], 0))[0]
    return worst


# ---- 表格 ----

def _overview_table(ctx):
    g = ctx.get
    return {
        "type": "table", "title": "运行概览",
        "cols": ["项目", "值"],
        "rows": [
            ["版本 / 轮次", "v%s ｜ %s" % (g("version") or "?",
                "计划轮(PLAN)" if g("round_mode") == "PLAN" else "下单轮(ORDER)")],
            ["标的 / 结算币", g("currency")],
            ["前置信号态 / 置信", "%s / %s" % (disp_signal_cn(g("signal_state")), g("signal_confidence"))],
            ["方向", BIAS_CN.get(g("direction_bias"), g("direction_bias"))],
            ["选用方案号(下单轮)", g("selected_plan")],
            ["选用方案保护模式", g("protection_mode_cn") or "—"],
            ["交易开关", "实盘下单(ALLOW_TRADING=True)" if g("allow_trading") else "空跑(未下单)"],
            ["状态机", disp_state_cn(g("state"))],
            ["参考价", _num(g("spot"), small=2, big=2)],
            ["选档指引", disp_decision_hint(g("signal_state"))],
            ["枚举漏斗", disp_diag_line(g("enum_diag")) if g("enum_diag") else "—"],
            ["选用方案综合评级", disp_health_grade(ctx) if g("short_instrument") else "—"],
            ["本轮结论", disp_reason_cn(g("reason"))],
        ],
    }


def disp_diag_line(diag):
    """枚举漏斗压成一行（放进概览，省一张表）。"""
    if not diag:
        return "—"
    return ("扫描%s → 出界%s/薄%s/宽%s/无保护%s → 候选%s → 进库%s → 合格%s" % (
        diag.get("短腿扫描", 0), diag.get("delta区间外", 0), diag.get("权利金过薄", 0),
        diag.get("价差过宽", 0), diag.get("无合格保护腿(腿宽内)", 0),
        diag.get("生成候选", 0), diag.get("进入菜单", 0), diag.get("合格", 0)))


def disp_menu_table(menu, selected_no, spot):
    """方案库对比（垂直+日历并列；★=下单轮将执行的方案号）。
    日历净credit为复用/残值修正后的「有效净credit(每周期)」，与垂直可比。"""
    def pct(x):
        return ("%.0f%%" % (x * 100)) if isinstance(x, (int, float)) else "—"

    def f2(x):
        return ("%.2f" % x) if isinstance(x, (int, float)) else "—"

    rows = []
    for p in menu:
        g = p.get
        star = "★" if g("id") == selected_no else ""
        if g("mode") == 1:                       # 日历：短→保护两个期号
            qihao = "%s→%s" % (g("short_expiry_label"), g("protection_expiry_label"))
        else:                                    # 同期垂直：同一到期
            qihao = "%s(同)" % g("short_expiry_label")
        tags = "/".join(g("tags") or []) or "—"
        ok = "合格" if g("qualified") else ("✗" + (g("reject_reason") or ""))
        dte = ("%.1fd" % (g("short_dte_hours") / 24.0)) if g("short_dte_hours") else "—"
        rows.append([
            "%s%s" % (star, g("id")), tags, g("mode_cn") or "—", qihao, dte,
            "%s(Δ%s)" % (_num(g("short_strike")), _num(g("short_delta"))),
            _num(g("protection_strike")), _num(g("width")),
            _dist_pct(g("short_strike"), spot), pct(g("win_rate")),
            _usd0(g("net_credit_effective"), spot), pct(g("credit_on_margin")),
            f2(g("rr")), _num(g("breakeven"), small=2, big=2),
            pct(g("margin_relief_ratio")), ok,
        ])
    return {
        "type": "table",
        "title": "策略选择明细（★=下单轮将执行；按【编号】匹配；有效$=日历复用残值修正后；信用/保证金=每周期保证金回报）",
        "cols": ["编号", "推荐", "模式", "期号(短/保护)", "到期", "短行权(Δ)", "保护行权",
                 "腿宽", "短距现价", "胜率", "有效$", "信用/保证金", "盈亏比", "盈亏平衡价",
                 "释放", "合格"],
        "rows": rows,
    }


def _position_table(ctx):
    """选用方案·保证金 + 成本/记账（合并 S:PM 与成本，省一张表；结算币 + USD）。"""
    g = ctx.get
    spot = g("spot")
    mode = g("protection_mode")
    ratio, minr = g("margin_relief_ratio"), g("min_required_ratio")
    accepted = (isinstance(ratio, (int, float)) and isinstance(minr, (int, float))
                and ratio >= minr)
    ml_label = "最大亏损(硬封顶)" if mode == 2 else "最大亏损≈(非硬封顶)"
    cm = g("credit_on_margin")
    rows = [
        ["合约(短/保护)", g("short_instrument") or "—", g("protection_instrument") or "—"],
        ["仅卖方腿 IM (B)", _num(g("im_short_only")), _usd(g("im_short_only"), spot)],
        ["卖方+保护 IM (C/占用保证金)", _num(g("im_with_protection")), _usd(g("im_with_protection"), spot)],
        ["保证金释放(比例/门槛)", "%s / %s" % (
            ("%.0f%%" % (ratio * 100)) if isinstance(ratio, (int, float)) else "—",
            ("%.0f%%" % (minr * 100)) if isinstance(minr, (int, float)) else "—"),
         "达标" if accepted else "未达标"],
        ["账户(模型/组合保证金)", g("account_margin_model") or "—", "是" if g("pm_accepted") else "否"],
        ["卖方腿 mark/张(=交易所标记)", _num(g("short_mark")), _usd(g("short_mark"), spot)],
        ["保护腿 mark/张(=交易所标记)", _num(g("protection_mark")), _usd(g("protection_mark"), spot)],
        ["下单数量(每结构)", _num(g("amount")), "—"],
        ["卖方权利金收入(×数量)", _num(g("short_premium_income")), _usd(g("short_premium_income"), spot)],
        ["保护腿权利金支出(×数量)", _num(g("protection_entry_cost")), _usd(g("protection_entry_cost"), spot)],
        ["单笔净credit(×数量)", _num(g("net_credit_single")), _usd(g("net_credit_single"), spot)],
    ]
    if mode == 1:                                 # 日历：复用/残值修正
        rows.append(["覆盖周期/残值回收", "%sx" % _num(g("covered_cycles")),
                     _usd(g("residual_value"), spot)])
    rows += [
        ["有效净credit(每周期)", _num(g("net_credit")), _usd(g("net_credit"), spot)],
        [ml_label, _num(g("max_loss")), _usd(g("max_loss"), spot)],
        ["盈亏比 / 信用占保证金", ("%.2f" % g("rr")) if isinstance(g("rr"), (int, float)) else "—",
         ("%.1f%%" % (cm * 100)) if isinstance(cm, (int, float)) else "—"],
        ["到期盈亏平衡价(近似)", _num(g("breakeven"), small=2, big=2), "—"],
        ["预估开仓手续费", _num(g("estimated_entry_fee")), _usd(g("estimated_entry_fee"), spot)],
    ]
    return {
        "type": "table", "title": "选用方案 · 保证金与成本（编号 %s · 评级 %s · 预估）"
        % (g("selected_id"), disp_health_grade(ctx)),
        "cols": ["项目", "值/BTC", "≈USD/备注"], "rows": rows,
    }


def disp_order_intent_table(intent):
    """『将下达订单』意图表：翻 ALLOW_TRADING 前一眼核对实际下单。"""
    rows = []
    for it in intent or []:
        prices = "/".join(_num(p) for p in (it.get("prices") or [])) or "—"
        rows.append([it.get("leg") or "", "买" if it.get("side") == "buy" else "卖",
                     it.get("instrument") or "—", prices, _num(it.get("amount")),
                     "post_only+reject"])
    return {"type": "table", "title": "将下达订单（maker-only；计划价含一步追价）",
            "cols": ["腿", "方向", "合约", "计划价(含追价)", "数量", "下单方式"], "rows": rows}


def _health_table(ctx):
    rows = [[lv, txt] for lv, txt in disp_health_notes(ctx)]
    return {"type": "table", "title": "合理性检查（综合评级：%s）" % disp_health_grade(ctx),
            "cols": ["级别", "说明"], "rows": rows}


def _header_color(ctx):
    reason = ctx.get("reason") or ""
    if reason == "STRUCTURE_OPEN":
        return _C_GREEN
    if reason in ("MARGIN_RELIEF_INSUFFICIENT", "ACCOUNT_NOT_PM", "PROTECTION_NOT_FILLED",
                  "NO_PLAN_MENU(请先运行计划轮)") \
            or reason.startswith("PLAN_NOT_QUALIFIED") or reason.startswith("PLAN_NOT_IN_MENU"):
        return _C_RED
    if ctx.get("short_instrument") and any(lv == "警示" for lv, _ in disp_health_notes(ctx)):
        return _C_ORANGE
    return _C_GRAY


# ---- 交互控制台（状态栏顶部「交互页面」：阶段 + 门控 + 信号接收 + 待批方案确认码 + 操作提示）----

_PHASE_CN = {
    "WAIT_SIGNAL": "等待信号", "OFFLINE_MANUAL": "离线手动(静态信号)",
    "RECOMMEND_READY": "方案库就绪·待硬授权", "HARD_APPROVAL_WAIT": "待计划硬授权",
    "PLAN_LOCKED": "方案锁定·预提交", "POSITION_MANAGE": "持仓管理",
    "EXIT_CAMPAIGN": "退出活动", "LONG_RECOVERY": "保护腿回收",
    "RECOVERY_BLOCKED": "恢复阻塞", "KILLED": "已急停",
}

# 操作提示引擎：阶段 → 「下一步点哪个按钮、输什么」的人话提示（落实「在交互栏给出操作提示」）
_HINTS = {
    "WAIT_SIGNAL": "等待可交易信号；信号不可用/过期时禁新开仓，持仓管理继续",
    "OFFLINE_MANUAL": "离线手动信号模式(红标)：进场依据静态 SIGNAL_STATE，请确认其与最新信号一致",
    "RECOMMEND_READY": "待批方案：点【执行】输入方案确认码进场 ｜ 点【拒绝】放弃",
    "HARD_APPROVAL_WAIT": "待批方案：点【执行】输入方案确认码进场 ｜ 点【拒绝】放弃",
    "PLAN_LOCKED": "方案已锁定·预提交复核中；复核通过且进场门开启才真实下单",
    "POSITION_MANAGE": "持仓中：达 80% 止盈资格后点【授权止盈】输入持仓授权码允许自动退出 ｜【撤销授权】撤销",
    "EXIT_CAMPAIGN": "退出活动中：逐 tick 买回短腿、不破止盈预算；预算内无法成交则暂停后重试",
    "LONG_RECOVERY": "短腿已归零·回收保护腿中；无 bid 记 LONG_RESIDUAL_ONLY，售出/结算后归档",
    "RECOVERY_BLOCKED": "启动恢复阻塞：账本与交易所持仓无法解释映射；禁开新仓，请人工核对",
    "KILLED": "已急停：停新开仓；退出/对冲减仓继续。点【恢复】重新对账并要求新计划硬批准",
}


def _console_phase_cn(p):
    return _PHASE_CN.get(p, p or "—")


def disp_operation_hint(ctx):
    """据当前阶段 / 门控 / 信号裁决给出唯一操作提示串。"""
    g = ctx.get
    if g("kill_new_risk"):
        return _HINTS["KILLED"]
    phase = g("console_phase")
    # 风险严重(EXIT_PREFERRED) 且未授权：优先引导风险退出授权（区别于 80% 止盈授权）
    if phase == "POSITION_MANAGE" and g("risk_state") == "EXIT_PREFERRED" \
            and "已授权" not in (g("exit_auth_state") or ""):
        return ("风险严重(EXIT_PREFERRED)：点【风险退出授权】输入风险退出码 %s 允许越价限价退出"
                "（成本封顶 RISK_EXIT_MAX_SPEND；该预算=0 时退出受阻将回退对冲）" % (g("risk_exit_auth_code") or "—"))
    if phase in _HINTS:
        return _HINTS[phase]
    sv = g("signal_verdict") or {}
    if sv.get("availability") == "OFFLINE_MANUAL":
        return _HINTS["OFFLINE_MANUAL"]
    if sv.get("block_new_opens"):
        return _HINTS["WAIT_SIGNAL"]
    return "—"


def disp_gate_line(gate_summary):
    """门控四动作压成一行：进场/退出/对冲开/对冲减 的 ✓✗。"""
    if not gate_summary:
        return "—"

    def mk(a):
        return "✓" if (gate_summary.get(a) or {}).get("allowed") else "✗"
    return "进场%s 退出%s 对冲开%s 对冲减%s" % (
        mk("ENTRY"), mk("EXIT"), mk("HEDGE_OPEN"), mk("HEDGE_REDUCE"))


def disp_signal_status_line(verdict):
    """信号接收裁决压成一行。"""
    if not verdict:
        return "—"
    avail = verdict.get("availability")
    if avail == "OFFLINE_MANUAL":
        return "离线手动(静态信号·红标)"
    block = "禁新开" if verdict.get("block_new_opens") else "可新开"
    return "%s ｜ %s ｜ side=%s" % (avail, block, verdict.get("side_hint") or "—")


_RISK_STATE_CN = {
    "NORMAL": "正常", "WATCH": "观察", "EXIT_PREFERRED": "偏退出(风险严重)",
    "HEDGE_READY": "偏对冲(风险严重持续)", "HEDGE_ACTIVE": "对冲监控中",
    "MANUAL_REVIEW": "人工复核",
}


def disp_risk_line(risk):
    """持仓后风险评估压成一行：状态 + 触界概率 + 漂移。数据缺口单独标注。"""
    if not risk:
        return None
    if risk.get("market_data_gap"):
        return "数据缺口（短腿盘口缺 delta/IV，风险评估降级·未驱动主动动作）"
    cr = risk.get("current_risk") or {}
    p, d = cr.get("touch_probability_now"), cr.get("touch_probability_drift")
    state = _RISK_STATE_CN.get(risk.get("tail_risk_state"), risk.get("tail_risk_state") or "—")
    extras = []
    if isinstance(p, (int, float)):
        extras.append("触界%.0f%%" % (p * 100))
    if isinstance(d, (int, float)):
        extras.append("漂移%+.0f%%" % (d * 100))
    return "%s%s" % (state, ("｜" + " ".join(extras)) if extras else "")


def disp_console_table(ctx):
    """交互控制台：每轮置顶。阶段 + 门控 + 信号接收 +（待批方案确认码 / 软授权 / 退出活动）+ 操作提示。
    后续阶段（E2 确认码 / E5 软授权 / E6 退出进度）通过 ctx 字段填充对应行。"""
    g = ctx.get
    rows = [
        ["阶段", _console_phase_cn(g("console_phase"))],
        ["执行门控", disp_gate_line(g("gate_summary"))],
        ["信号接收", disp_signal_status_line(g("signal_verdict"))],
    ]
    for c in (g("pending_candidates") or []):
        rows.append(["待批 #%s" % c.get("id"),
                     "%s 确认码 %s" % (c.get("summary") or "—", c.get("confirm_code") or "—")])
    pre = g("precommit")
    if pre is not None:
        rows.append(["预提交", "通过" if pre.get("passed")
                     else ("✗ " + ",".join(pre.get("failed") or []))])
    if g("commit_reason"):
        rows.append(["开仓", g("commit_reason")])
    if g("entry_state"):
        nc = g("entry_net_credit")
        rows.append(["开仓活动", "%s%s" % (g("entry_state"),
                     ("｜净credit %.6g" % nc) if isinstance(nc, (int, float)) else "")])
    arb = g("action_arb")
    if arb:
        line = str(arb.get("executable_action"))
        if arb.get("blocked_reason"):
            line += " (优先 %s 受阻:%s)" % (arb.get("preferred_action"), arb.get("blocked_reason"))
        rows.append(["风险动作", line])
    _rl = disp_risk_line(g("risk_pkg"))
    if _rl:
        rows.append(["风险", _rl])
    if g("exit_auth_state"):
        rows.append(["软授权", g("exit_auth_state")])
    if g("take_profit_ratio") is not None:
        rows.append(["止盈资格", g("take_profit_ratio")])
    if g("exit_campaign_state"):
        rows.append(["退出活动", g("exit_campaign_state")])
    if g("hedge_state"):
        rows.append(["对冲", g("hedge_state")])
    if g("reconciled") is False:
        rows.append(["对账", "✗ 快照与交易所持仓不符（已记录，风险收口继续）"])
    rows.append(["操作提示", disp_operation_hint(ctx)])
    return {"type": "table", "title": "交互控制台", "cols": ["项目", "值"], "rows": rows}


def disp_status_panel(ctx, note=""):
    """组装 LogStatus 字符串：标题行(着色) + 多表数组。
    有方案库时显示方案库对比表；选用/置顶方案有腿时显示其明细/模拟/成本/检查。"""
    header = "%s ｜ %s%s" % (note or "进场流水线", disp_reason_cn(ctx.get("reason")),
                            _header_color(ctx))
    tables = [disp_console_table(ctx), _overview_table(ctx)]   # 交互控制台置顶 + 概览
    if ctx.get("menu"):                           # 策略选择明细（已并入到期/盈亏平衡价，无独立概要表）
        tables.append(disp_menu_table(ctx["menu"], ctx.get("selected_plan"), ctx.get("spot")))
    if ctx.get("short_instrument"):
        tables.append(_position_table(ctx))       # 保证金 + 成本（S:PM 与成本已合并为一张）
        if ctx.get("order_intent"):
            tables.append(disp_order_intent_table(ctx["order_intent"]))
        tables.append(_health_table(ctx))
    return header + "\n`" + json.dumps(tables, ensure_ascii=False) + "`"


def disp_log_menu(menu, spot):
    """启动时把整轮方案明细打到 Log（永久记录；便于复盘初始方案库）。"""
    lines = ["[启动方案明细] 共 %d 条：" % len(menu)]
    for p in menu:
        g = p.get
        tags = "/".join(g("tags") or []) or "-"
        lines.append("  #%s %s %s %s 短%s(Δ%s)/保%s 宽%s 距%s 胜%s 有效%s 信/保%s 盈亏%s 平衡%s 释放%s %s" % (
            g("id"), tags, g("mode_cn"),
            ("%s→%s" % (g("short_expiry_label"), g("protection_expiry_label")))
            if g("mode") == 1 else ("%s(同)" % g("short_expiry_label")),
            _num(g("short_strike")), _num(g("short_delta")), _num(g("protection_strike")),
            _num(g("width")), _dist_pct(g("short_strike"), spot),
            ("%.0f%%" % (g("win_rate") * 100)) if isinstance(g("win_rate"), (int, float)) else "-",
            _usd0(g("net_credit_effective"), spot),
            ("%.0f%%" % (g("credit_on_margin") * 100)) if isinstance(g("credit_on_margin"), (int, float)) else "-",
            ("%.2f" % g("rr")) if isinstance(g("rr"), (int, float)) else "-",
            _num(g("breakeven"), small=2, big=2),
            ("%.0f%%" % (g("margin_relief_ratio") * 100)) if isinstance(g("margin_relief_ratio"), (int, float)) else "-",
            "合格" if g("qualified") else "✗"))
    return "\n".join(lines)


def disp_log_summary(ctx, note=""):
    """简明中文事件行（写入 Log 事件流）。"""
    g = ctx.get
    ratio = g("margin_relief_ratio")
    ratio_s = ("%.1f%%" % (ratio * 100)) if isinstance(ratio, (int, float)) else "—"
    return ("%s ｜ 短 %s@%s ｜ 保 %s@%s ｜ 释放 %s ｜ %s" % (
        note or "进场", g("short_instrument") or "—", _num(g("short_mark")),
        g("protection_instrument") or "—", _num(g("protection_mark")),
        ratio_s, disp_reason_cn(g("reason"))))
