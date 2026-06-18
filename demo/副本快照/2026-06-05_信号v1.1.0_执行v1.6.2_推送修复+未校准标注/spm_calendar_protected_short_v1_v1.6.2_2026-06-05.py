# -*- coding: utf-8 -*-
# === 自动合成产物：请勿手改，改 src/ 后重新 build_bundle.py ===
# Deribit S:PM 日历保护型卖方执行链 v1.6.2（FMZ 单文件）


# ===================== module: config =====================
# -*- coding: utf-8 -*-
"""
配置 & 信号全局变量块（启动前手填）。

v1 不做动态信号通道：方向 / 信号状态 / KPF 空间均为预留全局变量，启动前手动填入。
合成进 FMZ 单文件后，这些 UPPER_CASE 名字即位于文件顶部的全局区，FMZ 运行时直接可改。

所有阈值集中在此，避免散落硬编码（补设计稿「阈值未量化」缺口）。
"""

# ===== 版本号（便于迭代区分；显示于启动日志/面板/合成文件头）=====
#   1.0.0 基础执行链 → 1.1.0 短腿按 delta 选档 → 1.2.0 双模+3方案 →
#   1.3.0 计划轮/下单轮分离、垂直+日历全枚举、复用残值修正、去运行时命令 →
#   1.4.0 EV/流动性筛选、计划轮节流、执行健壮性(重试/价差守门/裸保护撤退)、下单意图表、选档指引 →
#   1.5.0 合并「策略选择明细」(期号/双腿/距现价)、稳定唯一编号(按编号匹配执行)、每张mark对齐交易所、综合评级 →
#   1.6.0 日历价差改可选(ENABLE_CALENDAR,默认关)、明细锁定启动时最推荐(不随刷新跳动,补期号+剩余到期)、
#         价值指标补盈亏平衡价+净credit/保证金回报率(替 EV 上菜单) →
#   1.6.1 修复：同期垂直长腿不再被「过度虚值(DEEP_OTM)」误过滤(该过滤仅对日历)，解决纯垂直时方案库变空；
#         新增「枚举诊断」漏斗，实时显示各门控砍掉多少候选 →
#   1.6.2 状态栏精简：枚举漏斗并入概览(一行)、删独立「选腿明细/概要/S:PM」表、S:PM+成本合并为一张、
#         策略选择明细补到期+盈亏平衡价(去 #/综合)；启动时整轮方案明细入 Log
STRATEGY_VERSION = "1.6.2"

# ===== 信号与方向（手填，§4.1 / §5.2 / §6.2）=====
# 取值依据：案例/前置模型信号流.txt（2026-05-29 全天）+ 案例/kpf_full_analysis_zh_*.html
#   - 前置论证持续「偏空 / 支持偏弱 / 置信 62~71」→ 卖 call、WEAK 放行
#   - 个别轮次「无交易-阻断」对应 NO_TRADE_BLOCKED：那种时刻应把 SIGNAL_STATE 改回阻断值，不进场
SETTLEMENT_CURRENCY = "BTC"                  # 数据源为 BTCUSDT
SIGNAL_STATE        = "TRADE_SUPPORT_WEAK"   # 信号流主态=支持偏弱；仅 STRONG/WEAK 放行进场
DIRECTION_BIAS      = "SHORT_CALL"           # 偏空论证 → 卖出上方 call

# ===== 计划轮 / 下单轮（两轮分离，运行后不经界面命令调整计划或仓位）=====
#   计划轮 ROUND_MODE="PLAN"：枚举所有符合范围的备选(垂直+日历)，按 KPF/信号筛选排序，
#       输出方案库到面板并持久化(_G)，绝不下单。
#   下单轮 ROUND_MODE="ORDER"：读取持久化方案库，按 SELECTED_PLAN 取方案号，复核后
#       仅当 ALLOW_TRADING=True 才真实开仓（否则展示将执行方案，仍空跑）。
#   止盈/止损退出模式后置，本版不含。
ROUND_MODE   = "PLAN"             # "PLAN" / "ORDER"
SELECTED_PLAN = 0                 # 下单轮执行的方案【唯一编号】(计划轮菜单「编号」列，稳定不随排序变)；0=未指定
MENU_SIZE    = 10                 # 方案库最多输出条数

# ===== 候选枚举范围（计划轮据此选出所有符合要求的备选）=====
SHORT_DELTA_RANGE      = (0.15, 0.45)   # 短腿 |delta| 接受范围（卖权利金主驱动）
PROTECTION_WIDTH_RANGE = (2000, 2500)   # 保护腿腿宽范围(USD)，以短腿行权为基准
# 同期垂直恒纳入；日历价差为**可选**：仅 True 才纳入方案库。默认 False——
# 避免「为了主观复用保护腿而在不该交易的时机强行进场」。
ENABLE_CALENDAR        = False

# ===== 日历保护腿复用 / 残值（真实市场口径，用于修正日历净 credit；仅 ENABLE_CALENDAR 时相关）=====
# 日历保护腿可跨多个近端周期复用、退出时可卖出回收残值，故单周期有效成本远低于全额权利金：
#   covered_cycles = floor(保护腿DTE / 短腿DTE)；
#   单周期摊销成本 = 保护腿权利金 × (1 - 残值回收率) / covered_cycles；
#   日历有效净credit = 短腿权利金 - 单周期摊销成本。
PROTECTION_RESIDUAL_RECOVERY = 0.40     # 退出时预计可回收的保护腿权利金比例(0~1)

# ===== 信号强度 → 偏好 delta（参与排序，不替模型判方向）=====
SIGNAL_CONFIDENCE = 62            # 0~100 前置模型置信(手填)；弱/低→偏低 delta(高胜率)，强/高→偏高 delta

# ===== 方案排序综合分权重 =====
PLAN_WEIGHTS = {"win_rate": 0.30, "rr": 0.30, "kpf": 0.20, "signal": 0.20}

# ===== KPF 空间参考（手填，仅定空间不判方向；非唯一依据，§5/§6）=====
# 方向速查（三档都填「该方向的外侧方向」，程序会回显解析结果便于核对）：
#   SHORT_CALL(偏空)：三档都在现价【上方】，且 KPF_FAR_RISK_ZONE >= KPF_NEAR_BOUNDARY（更高=更外侧）
#   SHORT_PUT (偏多)：三档都在现价【下方】，且 KPF_FAR_RISK_ZONE <= KPF_NEAR_BOUNDARY（更低=更外侧）
# 取值依据：KPF v1.03 关键价位轴（参考价当时 74,418；最新信号价 ~73,416；当前为偏空示例）
UNDERLYING_REF_PRICE = None                    # 留 None 走实时 index（真实市场以实时价 + delta 选档）
KPF_CONTESTED_CORE = (75500, 77400)            # 争夺核心接受区(low,high)：尽量不把短腿放其内部；None=不启用
KPF_NEAR_BOUNDARY = 75000                      # 短腿软锚：同 delta 并列时优先靠近它（弱约束）
KPF_FAR_RISK_ZONE = 80000                      # 保护腿腿宽并列时的软参考（靠近下一核心风险墙）

# ===== 周期（§5.1 / §6.1）=====
SHORT_DTE_HOURS = (24, 72)        # 近端卖方腿 DTE 区间（小时）
PROT_DTE_DAYS   = (5, 10)         # 远期保护腿 DTE 区间（天）
ORDER_AMOUNT = 0.1                # 单结构数量（Deribit 期权最小步长，BTC=0.1 / ETH=1）

# ===== 筛选 / 门控阈值 =====
MIN_MARGIN_RELIEF_RATIO = 0.10    # 量化设计稿「极低」：低于此则该保护腿不合格(§7.2)
DEEP_OTM_MAX_DELTA = 0.05         # 保护腿「过度虚值」判定：|delta| 低于此视为灾难彩票腿(§6.3)
MIN_SHORT_PREMIUM  = 0.0005       # 短腿最小权利金(结算币)：低于则权利金过薄、手续费占比高 → 弃
MAX_SPREAD_RATIO   = 0.60         # 腿最大相对价差 (ask-bid)/mid：超过视为流动性差 → 弃/不成交

# ===== 执行 =====
MAX_CHASE_STEPS    = 1            # 每条腿最多追价步数(§10.3)
CHASE_WAIT_SECONDS = 8            # 挂单后判定未成交的等待秒数
UNWIND_PROTECTION_ON_NO_SHORT = True  # 保护腿成交但短腿挂不上时，自动 maker 卖回保护腿(一次)避免裸保护

# ===== 安全门（核心，均为启动前静态配置；运行后不经界面命令改动）=====
ALLOW_TRADING = False             # 仅下单轮且置 True 才真实开仓；False=空跑(只展示将执行方案)。
KILL_SWITCH   = False             # True=停止新开仓（静态配置，无运行时命令）

# ===== 运行参数 =====
LOOP_INTERVAL_MS    = 3000        # 主循环间隔
PLAN_REFRESH_SECONDS = 45         # 计划轮重算方案库的最小间隔(秒)：节流 API + 防刷屏

# 放行进场的信号集合
ENTER_SIGNALS = ("TRADE_SUPPORT_STRONG", "TRADE_SUPPORT_WEAK")
# 触发退出/复核的信号集合(§9.2)
EXIT_REVIEW_SIGNALS = ("NO_TRADE_AMBIGUOUS", "NO_TRADE_BLOCKED")


def validate_config():
    """启动期配置自检，返回错误列表（空=通过）。"""
    errs = []
    if SETTLEMENT_CURRENCY not in ("BTC", "ETH"):
        errs.append("SETTLEMENT_CURRENCY 必须为 BTC 或 ETH")
    if DIRECTION_BIAS not in ("SHORT_CALL", "SHORT_PUT"):
        errs.append("DIRECTION_BIAS 必须为 SHORT_CALL 或 SHORT_PUT")
    if not (SHORT_DTE_HOURS[0] < SHORT_DTE_HOURS[1]):
        errs.append("SHORT_DTE_HOURS 区间非法")
    if not (PROT_DTE_DAYS[0] < PROT_DTE_DAYS[1]):
        errs.append("PROT_DTE_DAYS 区间非法")
    # 日历保护腿到期需长于近端 short（仅启用日历时校验；同期垂直同到期合法）
    if ENABLE_CALENDAR and PROT_DTE_DAYS[0] * 24 <= SHORT_DTE_HOURS[1]:
        errs.append("日历枚举：保护腿最短 DTE 应严格长于近端 short 最长 DTE")
    if ORDER_AMOUNT <= 0:
        errs.append("ORDER_AMOUNT 必须为正")
    if ROUND_MODE not in ("PLAN", "ORDER"):
        errs.append("ROUND_MODE 必须为 PLAN 或 ORDER")
    if not (0 < SHORT_DELTA_RANGE[0] < SHORT_DELTA_RANGE[1] < 1):
        errs.append("SHORT_DELTA_RANGE 应满足 0<min<max<1")
    if not (PROTECTION_WIDTH_RANGE[0] <= PROTECTION_WIDTH_RANGE[1]):
        errs.append("PROTECTION_WIDTH_RANGE 区间非法")
    if not (0.0 <= PROTECTION_RESIDUAL_RECOVERY <= 1.0):
        errs.append("PROTECTION_RESIDUAL_RECOVERY 应在 [0,1]")
    if not (0 <= SIGNAL_CONFIDENCE <= 100):
        errs.append("SIGNAL_CONFIDENCE 应在 [0,100]")
    if SELECTED_PLAN < 0:
        errs.append("SELECTED_PLAN 必须 >= 0（0=未指定，下单轮需填计划轮给出的唯一编号）")
    if ROUND_MODE == "ORDER" and SELECTED_PLAN == 0:
        errs.append("下单轮必须把 SELECTED_PLAN 设为计划轮菜单中的某个唯一编号")
    if MENU_SIZE < 1:
        errs.append("MENU_SIZE 必须 >= 1")
    if MIN_SHORT_PREMIUM < 0:
        errs.append("MIN_SHORT_PREMIUM 不可为负")
    if not (0 < MAX_SPREAD_RATIO <= 5):
        errs.append("MAX_SPREAD_RATIO 应在 (0,5]")
    if PLAN_REFRESH_SECONDS < 1:
        errs.append("PLAN_REFRESH_SECONDS 必须 >= 1")
    if not (0.0 < MIN_MARGIN_RELIEF_RATIO < 1.0):
        errs.append("MIN_MARGIN_RELIEF_RATIO 应在 (0,1)")
    if KPF_NEAR_BOUNDARY <= 0 or KPF_FAR_RISK_ZONE <= 0:
        errs.append("KPF 边界必须为正")
    # 方向一致性：偏空时远期风险区应在近端边界外侧（更高）；偏多相反
    if DIRECTION_BIAS == "SHORT_CALL" and KPF_FAR_RISK_ZONE < KPF_NEAR_BOUNDARY:
        errs.append("SHORT_CALL 下 KPF_FAR_RISK_ZONE 应 >= KPF_NEAR_BOUNDARY（更外侧/更高）")
    if DIRECTION_BIAS == "SHORT_PUT" and KPF_FAR_RISK_ZONE > KPF_NEAR_BOUNDARY:
        errs.append("SHORT_PUT 下 KPF_FAR_RISK_ZONE 应 <= KPF_NEAR_BOUNDARY（更外侧/更低）")
    return errs

# ===================== module: deribit_io =====================
# -*- coding: utf-8 -*-
"""
Deribit IO 适配层（dbt_*）。

统一经 FMZ 的 exchange.IO("api", method, path, query) 调 Deribit 原生 REST。
FMZ 已配置好 Deribit 交易所对象与 API key，私有请求由 FMZ 自动签名，本层不处理鉴权。
若个别端点连 IO 也不通，可在此层改为手动签名 REST 兜底（v1 默认走 IO）。

返回值统一抽取 Deribit JSON-RPC 的 result 字段；error 时 Log 并返回 None。
"""

import json

try:
    from urllib.parse import urlencode  # py3
except ImportError:  # pragma: no cover
    from urllib import urlencode        # py2 (FMZ 部分环境)


DERIBIT_API_PREFIX = "/api/v2"


def _build_query(params):
    """dict -> querystring，对 JSON 值（如 simulated_positions）安全编码。"""
    flat = {}
    for k, v in params.items():
        if v is None:
            continue
        if isinstance(v, bool):
            flat[k] = "true" if v else "false"
        elif isinstance(v, (dict, list)):
            flat[k] = json.dumps(v, separators=(",", ":"))
        else:
            flat[k] = v
    return urlencode(flat)


def _result(resp, ctx=""):
    """从 IO 返回值中抽取 result；兼容 FMZ 已解包/未解包两种形态。"""
    if resp is None:
        Log("[dbt] IO 返回 None:", ctx)
        return None
    if isinstance(resp, dict):
        if "error" in resp and resp["error"]:
            Log("[dbt] Deribit error:", ctx, json.dumps(resp["error"]))
            return None
        if "result" in resp:
            return resp["result"]
    return resp


def _call(method, path, params=None, ctx="", retries=0):
    """retries>0 时对返回 None（网络/瞬时错误）做有限重试；写操作请用 retries=0。"""
    query = _build_query(params or {})
    attempt = 0
    while True:
        resp = exchange.IO("api", method, DERIBIT_API_PREFIX + path, query)
        out = _result(resp, ctx or path)
        if out is not None or attempt >= retries:
            return out
        attempt += 1


_READ_RETRIES = 2


# ---------- 公开行情 / 合约 ----------

def dbt_get_instruments(currency, kind="option", expired=False):
    return _call("GET", "/public/get_instruments",
                 {"currency": currency, "kind": kind, "expired": expired},
                 "get_instruments", _READ_RETRIES) or []


def dbt_get_instrument(instrument_name):
    """单合约元数据（含 tick_size / tick_size_steps / contract_size / min_trade_amount）。"""
    return _call("GET", "/public/get_instrument",
                 {"instrument_name": instrument_name}, "get_instrument", _READ_RETRIES)


def dbt_ticker(instrument_name):
    """含 best_bid_price / best_ask_price / mark_price / underlying_price / greeks。"""
    return _call("GET", "/public/ticker",
                 {"instrument_name": instrument_name}, "ticker", _READ_RETRIES)


def dbt_order_book(instrument_name, depth=1):
    return _call("GET", "/public/get_order_book",
                 {"instrument_name": instrument_name, "depth": depth}, "order_book", _READ_RETRIES)


def dbt_index_price(currency):
    """结算币对 USD 指数价（费用 USD 展示用）。"""
    index_name = (currency + "_usd").lower()
    r = _call("GET", "/public/get_index_price", {"index_name": index_name},
              "index_price", _READ_RETRIES)
    return (r or {}).get("index_price")


# ---------- 私有账户 / 持仓 ----------

def dbt_account_summary(currency, extended=True):
    """含 margin_model / portfolio_margining_enabled / initial_margin / maintenance_margin。"""
    return _call("GET", "/private/get_account_summary",
                 {"currency": currency, "extended": extended}, "account_summary", _READ_RETRIES)


def dbt_get_positions(currency, kind="option"):
    return _call("GET", "/private/get_positions",
                 {"currency": currency, "kind": kind}, "get_positions", _READ_RETRIES) or []


def dbt_simulate_portfolio(currency, simulated_positions, add_positions=True):
    """S:PM 模拟。simulated_positions: {instrument_name: size}（负数=short）。
    返回含 initial_margin / maintenance_margin / available_funds / margin_model。"""
    return _call("GET", "/private/simulate_portfolio",
                 {"currency": currency,
                  "add_positions": add_positions,
                  "simulated_positions": simulated_positions},
                 "simulate_portfolio", _READ_RETRIES)


# ---------- 私有交易 ----------

def dbt_place_order(side, instrument_name, amount, price,
                    post_only=True, reject_post_only=True, label=None):
    """maker-only 限价单。side: 'buy'/'sell'。返回 result（含 order 字段）或 None。"""
    path = "/private/buy" if side == "buy" else "/private/sell"
    params = {
        "instrument_name": instrument_name,
        "amount": amount,
        "type": "limit",
        "price": price,
        "post_only": post_only,
        "reject_post_only": reject_post_only,
    }
    if label:
        params["label"] = label
    return _call("GET", path, params, "place_order:" + side)


def dbt_get_order_state(order_id):
    return _call("GET", "/private/get_order_state", {"order_id": order_id}, "order_state")


def dbt_cancel(order_id):
    return _call("GET", "/private/cancel", {"order_id": order_id}, "cancel")

# ===================== module: leg_selection =====================
# -*- coding: utf-8 -*-
"""
选腿（legsel_*）：把输入信号 + KPF 空间映射为具体的「行权价 / 到期 / 合约」。

KPF 只提供空间，不产生方向（§5/§6）。本模块为纯逻辑：输入交易所返回的合约列表与盘口，
输出选腿结果，便于本地单测。到期一律用合约自带的 expiration_timestamp，不靠解析合约名。
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


def legsel_pick_nearest_delta(enriched, target_delta, kpf_core=None, kpf_near=None):
    """在 enriched 短腿候选中选 |delta| 最接近 target_delta 的档（卖权利金主驱动）。
    KPF 软约束：尽量排除落在争夺核心区 kpf_core 内部的档；并列再按靠近软锚 kpf_near。
    返回选中合约(含 _delta) 或 None。"""
    if not enriched:
        return None
    pool = enriched
    if kpf_core:
        lo, hi = min(kpf_core), max(kpf_core)
        outside = [i for i in enriched if not (lo <= i["strike"] <= hi)]
        if outside:
            pool = outside
    return min(pool, key=lambda i: (abs(abs(i["_delta"]) - target_delta),
                                    abs(i["strike"] - kpf_near) if kpf_near else 0))


def legsel_protection_candidates(prot_insts, short_strike, want_call, width_band,
                                 delta_of=None, deep_otm_max_delta=0.05, kpf_far=None):
    """保护腿候选（以短腿行权价为基准、按腿宽选择；日历与同期垂直通用）：
      - call: strike > short_strike；put: strike < short_strike（更外侧）
      - 腿宽 = |strike - short_strike| 优先落在 width_band；排除过度虚值(|delta|<deep_otm)
      - 排序：腿宽最接近区间中心者优先，并列按靠近 KPF_FAR 软参考；带外档作兜底排后
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

    def keyf(rec):
        return (abs(rec["_width"] - wcenter),
                abs(rec["strike"] - kpf_far) if kpf_far else 0)

    in_band.sort(key=keyf)
    others.sort(key=keyf)
    return in_band + others


def _as_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def legsel_wall_proximity(short_strike, spot, gex_info, want_call,
                          near_pct=0.4):
    """SHADOW-ONLY diagnostic: is the chosen short strike sitting on a
    gexmonitorapi magnet / GEX wall / volatility trigger?

    Returns a suggestion dict for facts/panel. It NEVER feeds selection or
    ranking -- selection stays pure-delta, aligned with the Phase-1 KPF removal;
    this only annotates "this short leg is near a structural level". With
    gex_info missing/empty it returns gex_info_available=False and suggests
    nothing. The caller surfaces the suggestion but must not re-rank on it.
    """
    info = gex_info if isinstance(gex_info, dict) else None
    strike = _as_float(short_strike)
    wall_kind = "resistance_wall" if want_call else "support_wall"
    empty = {
        "gex_info_available": info is not None,
        "near_magnet": False,
        "near_wall": False,
        "near_volatility_trigger": False,
        "nearest_level": None,
        "nearest_level_kind": None,
        "distance_pct": None,
        "suggestion": None,
    }
    if info is None or strike is None or strike <= 0:
        return empty
    walls_key = "resistance_walls" if want_call else "support_walls"
    levels = []
    magnet = _as_float(info.get("magnet_price"))
    if magnet is not None and magnet > 0:
        levels.append((magnet, "magnet"))
    trigger = _as_float(info.get("volatility_trigger"))
    if trigger is not None and trigger > 0:
        levels.append((trigger, "volatility_trigger"))
    for wall in info.get(walls_key) or []:
        value = _as_float(wall)
        if value is not None and value > 0:
            levels.append((value, wall_kind))
    if not levels:
        return empty
    nearest_level, kind = min(levels, key=lambda lv: abs(lv[0] - strike))
    distance_pct = abs(nearest_level - strike) / strike * 100.0
    near = distance_pct <= near_pct
    suggestion = None
    if near:
        suggestion = (
            "short strike %.0f is within %.2f%% of %s %.0f; a strike further "
            "out may avoid the pin/wall (shadow only, ranking unchanged)"
            % (strike, distance_pct, kind, nearest_level))
    return {
        "gex_info_available": True,
        "near_magnet": near and kind == "magnet",
        "near_wall": near and kind == wall_kind,
        "near_volatility_trigger": near and kind == "volatility_trigger",
        "nearest_level": nearest_level,
        "nearest_level_kind": kind,
        "distance_pct": distance_pct,
        "suggestion": suggestion,
    }

# ===================== module: accounting =====================
# -*- coding: utf-8 -*-
"""
损耗记账（acct_*，§11）+ 全量信息报告（§13）。

口径统一以**结算币（BTC/ETH）**计价；期权权利金/mark 在 Deribit 即以标的币报价。
USD 仅用 index_price 换算展示。全部为纯函数，便于单测。
"""

OPTION_FEE_CAP_CCY = 0.0003   # 每张封顶（BTC/ETH 同值，结算币计）
OPTION_FEE_RATE    = 0.125    # 权利金比例上限 12.5%


# ---------- A. 显性交易费（§1.2）----------

def acct_option_fee_ccy(option_price_ccy, amount):
    """结算币计：MIN(0.0003, 0.125*option_price) * amount。"""
    per = min(OPTION_FEE_CAP_CCY, OPTION_FEE_RATE * option_price_ccy)
    return per * amount


def acct_option_fee_usd(option_price_ccy, amount, index_price):
    """USD 展示：MIN(0.0003*index, 0.125*option_price_usd) * amount。"""
    option_price_usd = option_price_ccy * index_price
    per = min(OPTION_FEE_CAP_CCY * index_price, OPTION_FEE_RATE * option_price_usd)
    return per * amount


# ---------- B. mark 偏离 ----------

def acct_mark_slippage(side, fill_price, mark_price, amount):
    """成交价相对 mark 的不利偏离（正=不利）。"""
    if side == "buy":
        return (fill_price - mark_price) * amount
    return (mark_price - fill_price) * amount


# ---------- C. 一步追价损耗 ----------

def acct_chase_cost(side, price0, final_price, amount):
    """相对初始挂价 price0 的追价损耗（正=不利）。"""
    if side == "buy":
        return (final_price - price0) * amount
    return (price0 - final_price) * amount


# ---------- D. bid/ask 价差损耗（参考：半价差）----------

def acct_spread_cost(best_bid, best_ask, amount):
    if best_bid is None or best_ask is None:
        return None
    return (best_ask - best_bid) / 2.0 * amount


# ---------- 远期保护腿真实成本（§11.2）----------

def acct_protection_realized_cost(entry_price, entry_fee, exit_fee=0.0,
                                  spread_slippage=0.0, exit_value=0.0):
    return entry_price + entry_fee + exit_fee + spread_slippage - exit_value


def acct_protection_cost_per_day(realized_cost, protected_days):
    if not protected_days:
        return None
    return realized_cost / protected_days


def acct_protection_cost_per_short_cycle(realized_cost, covered_cycle_count):
    if not covered_cycle_count:
        return None
    return realized_cost / covered_cycle_count


# ---------- F. full-burn 压力测试（§11.3，仅压测口径，不作默认真实成本）----------

def acct_full_burn(entry_price, entry_fee):
    return entry_price + entry_fee


# ---------- §13 全量报告 ----------

def acct_build_report(ctx):
    """组装设计稿 §13 结构 + 选腿/执行/记账明细，作为每次进场前的核对载体。
    ctx 为已采集字段的 dict；缺失字段以 None 占位。"""
    g = ctx.get
    return {
        "structure_type": "CALENDAR_PROTECTED_SHORT_OPTION",
        "account_margin_mode": "S:PM",
        "settlement_currency": g("currency"),
        "signal_state": g("signal_state"),
        "direction_bias": g("direction_bias"),
        "allow_trading": g("allow_trading"),
        "state": g("state"),
        "short_leg": {
            "instrument": g("short_instrument"),
            "strike": g("short_strike"),
            "dte_hours": g("short_dte_hours"),
            "side": "SELL",
            "role": "NEAR_TERM_SHORT_PREMIUM",
            "mark": g("short_mark"),
            "best_bid": g("short_bid"),
            "best_ask": g("short_ask"),
            "tick_size": g("short_tick"),
        },
        "protection_leg": {
            "instrument": g("protection_instrument"),
            "strike": g("protection_strike"),
            "dte_days": g("protection_dte_days"),
            "side": "BUY",
            "role": "FAR_TERM_ECONOMIC_PROTECTION",
            "is_inventory_reuse": g("is_inventory_reuse") or False,
            "delta": g("protection_delta"),
            "mark": g("protection_mark"),
            "best_bid": g("protection_bid"),
            "best_ask": g("protection_ask"),
            "tick_size": g("protection_tick"),
        },
        "kpf_context": {
            "near_boundary": g("kpf_near"),
            "far_risk_zone": g("kpf_far"),
            "comment_cn": "KPF 用于空间选腿，不用于方向判断。",
        },
        "spm_report": {
            "im_short_only": g("im_short_only"),
            "im_with_protection": g("im_with_protection"),
            "margin_relief_abs": g("margin_relief_abs"),
            "margin_relief_ratio": g("margin_relief_ratio"),
            "min_required_ratio": g("min_required_ratio"),
            "pm_accepted": g("pm_accepted"),
            "account_margin_model": g("account_margin_model"),
        },
        "cost_report": {
            "estimated_entry_fee": g("estimated_entry_fee"),
            "estimated_mark_slippage": g("estimated_mark_slippage"),
            "estimated_chase_slippage": g("estimated_chase_slippage"),
            "estimated_spread_cost": g("estimated_spread_cost"),
            "short_premium_income": g("short_premium_income"),
            "full_burn_cost": g("full_burn_cost"),
            "protection_cost_per_day": g("protection_cost_per_day"),
            "protection_cost_per_short_cycle": g("protection_cost_per_short_cycle"),
            "expected_recoverable_value": g("expected_recoverable_value"),
            "cost_basis_note_cn": "保护腿真实成本按退出残值与覆盖周期摊销，不按买入价一次性计入；full_burn 仅压测。",
        },
        "execution_policy": {
            "maker_only": True,
            "max_chase_steps": g("max_chase_steps"),
            "protection_first": True,
            "allow_add_on_same_direction_signal": False,
        },
    }

# ===================== module: plans =====================
# -*- coding: utf-8 -*-
"""
方案库构建、评估与排序（plan_*）。

计划轮枚举所有符合范围的备选(垂直价差 + 日历价差)，每个 = 一组(短腿 + 保护腿)，
按 胜率 / 盈亏比 / KPF 空间支持 / 信号契合 计算综合分排序，输出方案库（含方案号 + 推荐标签）。

口径（启发式，用于排序比较；非精确定价）：
- 胜率 ≈ 1 - |短腿 delta|（短腿到期 OTM 近似概率）。
- 同期垂直：保护腿与短腿同到期、到期一起了结。
    净 credit = (短腿 mark - 保护腿 mark) × 数量；最大亏损 = 腿宽折BTC - 净credit（**硬封顶**）。
- 日历价差：保护腿更远到期，**可跨多个近端周期复用、退出可卖残值**，故单周期有效成本远低于全额：
    covered_cycles = floor(保护腿DTE / 短腿DTE)；
    单周期摊销成本 = 保护腿权利金 × (1 - 残值回收率) / covered_cycles；
    有效净credit(每周期) = 短腿权利金 - 单周期摊销成本；最大亏损≈腿宽折BTC - 有效净credit（**非硬封顶**）。
- 盈亏比 = 有效净credit / 最大亏损（仅二者均为正时有意义）。
纯函数，便于单测。
"""

MODE_CALENDAR = 1
MODE_VERTICAL = 2


def plan_mode_cn(mode):
    return "日历价差" if mode == MODE_CALENDAR else "同期垂直"


def plan_expiry_label(instrument_name):
    """从合约名取期号(到期标签)，如 BTC-1JUN26-74000-C → 1JUN26。"""
    if not instrument_name:
        return "—"
    parts = instrument_name.split("-")
    return parts[1] if len(parts) >= 2 else "—"


def plan_id(mode, short_instrument, protection_instrument):
    """按结构内容生成**稳定唯一编号**（确定性，不随排序/进程变化）。
    下单轮按此编号匹配，避免「方案重排后选错执行」。返回 4 位数 1000-9999。"""
    key = "%s|%s|%s" % (mode, short_instrument or "", protection_instrument or "")
    h = 0
    for ch in key:
        h = (h * 131 + ord(ch)) % 1000000007
    return 1000 + (h % 9000)


def plan_win_rate(short_delta):
    return None if short_delta is None else 1.0 - abs(short_delta)


def plan_width_btc(width_usd, index_price, amount):
    if not width_usd or not index_price:
        return None
    return (width_usd / index_price) * amount


def plan_covered_cycles(prot_dte_hours, short_dte_hours):
    if not prot_dte_hours or not short_dte_hours or short_dte_hours <= 0:
        return 1
    return max(1, int(prot_dte_hours // short_dte_hours))


def plan_effective_credit(mode, short_prem, prot_prem, covered_cycles, residual_recovery):
    """返回 (effective_net_credit, single_net_credit, amortized_cost_per_cycle, residual_value)。
    short_prem/prot_prem 为持仓口径权利金(已×数量)。"""
    if short_prem is None or prot_prem is None:
        return None, None, None, None
    single = short_prem - prot_prem
    if mode == MODE_VERTICAL:
        return single, single, prot_prem, 0.0   # 同到期了结，无复用/残值
    # 日历：复用 + 残值
    residual_value = prot_prem * residual_recovery
    effective_cost = prot_prem - residual_value          # = prot_prem*(1-recovery)
    amortized = effective_cost / max(1, covered_cycles)
    effective = short_prem - amortized
    return effective, single, amortized, residual_value


def plan_max_loss(width_usd, index_price, effective_net_credit, amount):
    wb = plan_width_btc(width_usd, index_price, amount)
    if wb is None or effective_net_credit is None:
        return None
    return max(wb - effective_net_credit, 0.0)


def plan_rr(net_credit, max_loss):
    if net_credit is None or max_loss is None or max_loss <= 0 or net_credit <= 0:
        return None
    return net_credit / max_loss


def plan_ev(win_rate, net_credit, max_loss):
    """期望值/周期(BTC) = 胜率×有效净credit − (1−胜率)×最大亏损（最坏亏损口径，仅作参考）。"""
    if win_rate is None or net_credit is None or max_loss is None:
        return None
    return win_rate * net_credit - (1.0 - win_rate) * max_loss


def plan_breakeven(want_call, short_strike, short_mark, prot_mark, spot):
    """到期盈亏平衡价(近似)：短腿行权 ± 每张净credit折USD。
    call: 价格高于此开始亏；put: 价格低于此开始亏。日历下为单周期近似。"""
    if short_strike is None or short_mark is None or prot_mark is None or not spot:
        return None
    net_pc_usd = (short_mark - prot_mark) * spot      # 每张净credit折 USD(价格点)
    return short_strike + net_pc_usd if want_call else short_strike - net_pc_usd


def plan_credit_on_margin(net_credit_effective, im_with_protection):
    """净credit / 占用保证金（每周期保证金回报率）——本策略价值核心指标。"""
    if net_credit_effective is None or not im_with_protection or im_with_protection <= 0:
        return None
    return net_credit_effective / im_with_protection


def plan_kpf_score(short_strike, prot_strike, want_call, kpf_core, kpf_far):
    """KPF 空间支持评分 [0,1]：短腿避开争夺核心(+)、保护腿靠近风险墙(+)。"""
    score = 0.5
    if kpf_core and short_strike is not None:
        lo, hi = min(kpf_core), max(kpf_core)
        score += -0.35 if (lo <= short_strike <= hi) else 0.2
    if kpf_far and prot_strike:
        dist = abs(prot_strike - kpf_far)
        score += max(0.0, 0.3 * (1.0 - dist / 5000.0))
    return max(0.0, min(1.0, score))


def plan_preferred_delta(signal_state, confidence, delta_range):
    """信号强度 → 偏好短腿 |delta|：弱/低置信偏低(高胜率)，强/高置信偏高(高盈亏比)。"""
    lo, hi = delta_range
    c = (confidence if confidence is not None else 50) / 100.0
    base = lo + (hi - lo) * c
    if signal_state == "TRADE_SUPPORT_STRONG":
        base = min(hi, base + 0.05)
    return base


def plan_signal_fit(short_delta, preferred_delta, scale=0.25):
    if short_delta is None:
        return 0.0
    return max(0.0, 1.0 - abs(abs(short_delta) - preferred_delta) / scale)


def plan_assemble(mode, amount, spot, min_ratio, residual_recovery,
                  preferred_delta, kpf_core, kpf_far, want_call,
                  short, sq, prot, pq, spm, pm_ok, account_model,
                  short_dte_hours=None, prot_dte_hours=None):
    """组装一个候选方案 dict（不含综合分/方案号，由 plan_rank 补充）。"""
    sq, pq = sq or {}, pq or {}
    short_mark, prot_mark = sq.get("mark"), pq.get("mark")
    short_delta = (short or {}).get("_delta", sq.get("delta"))
    width = abs(prot.get("strike", 0) - short.get("strike", 0)) if (short and prot) else None

    premium_income = (short_mark * amount) if short_mark is not None else None
    protection_premium = (prot_mark * amount) if prot_mark is not None else None
    covered = (plan_covered_cycles(prot_dte_hours, short_dte_hours)
               if mode == MODE_CALENDAR else 1)
    eff_credit, single_credit, amort, residual = plan_effective_credit(
        mode, premium_income, protection_premium, covered, residual_recovery)
    max_loss = plan_max_loss(width, spot, eff_credit, amount)
    rr = plan_rr(eff_credit, max_loss)

    fee = 0.0
    if short_mark is not None:
        fee += acct_option_fee_ccy(short_mark, amount)
    if prot_mark is not None:
        fee += acct_option_fee_ccy(prot_mark, amount)
    full_burn = (acct_full_burn(protection_premium, acct_option_fee_ccy(prot_mark, amount))
                 if prot_mark is not None else None)

    relief_ratio = (spm or {}).get("relief_ratio")
    relief_ok = isinstance(relief_ratio, (int, float)) and relief_ratio >= min_ratio
    no_bid = sq.get("best_bid") in (None, 0)

    reject = None
    if not short:
        reject = "无合适短腿"
    elif not prot:
        reject = "无合格保护腿"
    elif no_bid:
        reject = "短腿无买盘"
    elif not relief_ok:
        reject = "S:PM 释放不足"
    elif not pm_ok:
        reject = "账户非组合保证金"
    qualified = reject is None

    short_inst = (short or {}).get("instrument_name")
    prot_inst = (prot or {}).get("instrument_name")
    return {
        "id": plan_id(mode, short_inst, prot_inst),
        "short_expiry_label": plan_expiry_label(short_inst),
        "protection_expiry_label": plan_expiry_label(prot_inst),
        "mode": mode, "mode_cn": plan_mode_cn(mode),
        "short_instrument": (short or {}).get("instrument_name"),
        "short_strike": (short or {}).get("strike"), "short_delta": short_delta,
        "short_mark": short_mark, "short_bid": sq.get("best_bid"),
        "short_ask": sq.get("best_ask"), "short_tick": sq.get("tick"),
        "short_dte_hours": short_dte_hours, "short_expiry": (short or {}).get("expiration_timestamp"),
        "protection_instrument": (prot or {}).get("instrument_name"),
        "protection_strike": (prot or {}).get("strike"), "protection_delta": pq.get("delta"),
        "protection_mark": prot_mark, "protection_bid": pq.get("best_bid"),
        "protection_ask": pq.get("best_ask"), "protection_tick": pq.get("tick"),
        "protection_dte_days": (round(prot_dte_hours / 24.0, 2) if prot_dte_hours else None),
        "protection_dte_hours": prot_dte_hours,
        "protection_expiry": (prot or {}).get("expiration_timestamp"),
        "width": width, "amount": amount, "spot": spot,
        "win_rate": plan_win_rate(short_delta),
        "premium_income": premium_income, "protection_premium": protection_premium,
        "covered_cycles": covered, "residual_value": residual,
        "amortized_cost_per_cycle": amort,
        "net_credit_single": single_credit, "net_credit_effective": eff_credit,
        "max_loss": max_loss, "rr": rr,
        "ev": plan_ev(plan_win_rate(short_delta), eff_credit, max_loss),
        "breakeven": plan_breakeven(want_call, (short or {}).get("strike"),
                                    short_mark, prot_mark, spot),
        "credit_on_margin": plan_credit_on_margin(eff_credit, (spm or {}).get("im_with_protection")),
        "entry_fee": fee, "full_burn": full_burn,
        "spread_cost": acct_spread_cost(sq.get("best_bid"), sq.get("best_ask"), amount),
        "kpf_score": plan_kpf_score((short or {}).get("strike"),
                                    (prot or {}).get("strike"), want_call, kpf_core, kpf_far),
        "signal_fit": plan_signal_fit(short_delta, preferred_delta),
        "im_short_only": (spm or {}).get("im_short_only"),
        "im_with_protection": (spm or {}).get("im_with_protection"),
        "margin_relief_abs": (spm or {}).get("relief_abs"),
        "margin_relief_ratio": relief_ratio,
        "pm_ok": pm_ok, "account_model": account_model,
        "qualified": qualified, "reject_reason": reject,
        "composite": None, "plan_no": None, "tags": [],
    }


def plan_prelim_score(c, weights):
    """无 S:PM 的初筛分（用于枚举后裁剪 top-K）。"""
    wr = c.get("win_rate") or 0.0
    rr = c.get("rr") or 0.0
    return (weights["win_rate"] * wr + weights["rr"] * min(rr, 1.0)
            + weights["kpf"] * (c.get("kpf_score") or 0.0)
            + weights["signal"] * (c.get("signal_fit") or 0.0))


def plan_rank(cands, weights, menu_size):
    """对候选打综合分、排序、确保两种模式均入选、编号、打推荐标签，返回菜单 list。"""
    pool = [c for c in cands if c.get("qualified")] or list(cands)
    rrs = [c["rr"] for c in pool if isinstance(c.get("rr"), (int, float)) and c["rr"] > 0]
    max_rr = max(rrs) if rrs else 1.0
    for c in pool:
        wr = c.get("win_rate") or 0.0
        rr = c.get("rr") or 0.0
        rr_norm = min(rr / max_rr, 1.0) if max_rr else 0.0
        c["rr_norm"] = rr_norm
        c["composite"] = (weights["win_rate"] * wr + weights["rr"] * rr_norm
                          + weights["kpf"] * (c.get("kpf_score") or 0.0)
                          + weights["signal"] * (c.get("signal_fit") or 0.0))
    ranked = sorted(pool, key=lambda c: c["composite"], reverse=True)
    menu = ranked[:menu_size]
    # 确保垂直与日历都出现在菜单
    for mode in (MODE_VERTICAL, MODE_CALENDAR):
        if menu and not any(c["mode"] == mode for c in menu):
            extra = next((c for c in ranked if c["mode"] == mode), None)
            if extra:
                menu[-1] = extra
                menu.sort(key=lambda c: c["composite"], reverse=True)
    for i, c in enumerate(menu, start=1):
        c["plan_no"] = i
    _assign_tags(menu)
    return menu


def _assign_tags(menu):
    for c in menu:
        c["tags"] = []
    if not menu:
        return
    max(menu, key=lambda c: c.get("win_rate") or 0.0)["tags"].append("高胜率")
    rr_c = [c for c in menu if isinstance(c.get("rr"), (int, float)) and c["rr"] > 0]
    if rr_c:
        max(rr_c, key=lambda c: c["rr"])["tags"].append("高盈亏比")
    ev_c = [c for c in menu if isinstance(c.get("ev"), (int, float))]
    if ev_c:
        max(ev_c, key=lambda c: c["ev"])["tags"].append("高期望")
    max(menu, key=lambda c: c.get("composite") or 0.0)["tags"].append("均衡")

# ===================== module: display =====================
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


def disp_status_panel(ctx, note=""):
    """组装 LogStatus 字符串：标题行(着色) + 多表数组。
    有方案库时显示方案库对比表；选用/置顶方案有腿时显示其明细/模拟/成本/检查。"""
    header = "%s ｜ %s%s" % (note or "进场流水线", disp_reason_cn(ctx.get("reason")),
                            _header_color(ctx))
    tables = [_overview_table(ctx)]               # 概览（已内含枚举漏斗一行）
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

# ===================== module: spm_sim =====================
# -*- coding: utf-8 -*-
"""
S:PM 保证金模拟校验（spm_*，§7）。

抵消机制已联网取证确认可行（同币种同子账户跨到期 netting）；本模块只**逐笔确认幅度**：
比较 B(仅 short) 与 C(short+protection) 两个模拟场景的 IM，看远期保护腿是否带来足够的
保证金释放。逻辑保持简单，不做额外复杂回路。
"""



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

# ===================== module: execution =====================
# -*- coding: utf-8 -*-
"""
执行层（exec_*，§10）：保护腿优先、maker-only、只追一步、禁 taker。

价格计算为纯函数（可单测）；下单/轮询/撤单走 dbt_*。
ALLOW_TRADING=False 或 KILL_SWITCH=True 时，所有真实下单短路为「记录意图」（空跑核对）。
"""

import math



# ---------- 纯价格计算（§10.3）----------

def _round_to_tick(price, tick, mode):
    if not tick:
        return price
    n = price / tick
    # 加微小 epsilon 抵消浮点误差（如 0.0013-0.0001 落在 0.00119999…，floor 会误降一格）
    n = math.floor(n + 1e-9) if mode == "down" else math.ceil(n - 1e-9)
    return round(n * tick, 10)


def exec_buy_price(mark, best_ask, tick, step):
    """买 protection：step0=min(mark,ask-tick)；每追一步 +tick，封顶 ask-tick。"""
    cap = best_ask - tick
    base = min(mark, cap)
    p = base + step * tick
    return _round_to_tick(min(p, cap), tick, "down")


def exec_sell_price(mark, best_bid, tick, step):
    """卖 short：step0=max(mark,bid+tick)；每追一步 -tick，封底 bid+tick。"""
    floor_p = best_bid + tick
    base = max(mark, floor_p)
    p = base - step * tick
    return _round_to_tick(max(p, floor_p), tick, "up")


def exec_price_for(side, mark, best_bid, best_ask, tick, step):
    return (exec_buy_price(mark, best_ask, tick, step) if side == "buy"
            else exec_sell_price(mark, best_bid, tick, step))


# ---------- 行情快照 ----------

def exec_quote(instrument):
    """返回 {mark, best_bid, best_ask, tick} 或 None。"""
    t = dbt_ticker(instrument)
    meta = dbt_get_instrument(instrument)
    if not t or not meta:
        return None
    return {
        "mark": t.get("mark_price"),
        "mark_iv": t.get("mark_iv"),
        "best_bid": t.get("best_bid_price"),
        "best_ask": t.get("best_ask_price"),
        "tick": meta.get("tick_size"),
        "underlying": t.get("underlying_price"),
        "delta": (t.get("greeks") or {}).get("delta"),
        "gamma": (t.get("greeks") or {}).get("gamma"),
    }


def exec_spread_ratio(q):
    """相对价差 (ask-bid)/mid；缺数据返回 None。"""
    if not q:
        return None
    bid, ask = q.get("best_bid"), q.get("best_ask")
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    return (ask - bid) / mid if mid > 0 else None


def exec_plan_prices(side, instrument, amount):
    """返回该腿的下单意图：计划价(含追价档)+盘口，供「将下达订单」意图表展示。"""
    q = exec_quote(instrument)
    if not q or q.get("best_bid") is None or q.get("best_ask") is None:
        return {"instrument": instrument, "side": side, "amount": amount, "prices": [], "quote": q}
    prices = [exec_price_for(side, q["mark"], q["best_bid"], q["best_ask"], q["tick"], s)
              for s in range(MAX_CHASE_STEPS + 1)]
    return {"instrument": instrument, "side": side, "amount": amount, "prices": prices,
            "mark": q.get("mark"), "best_bid": q.get("best_bid"), "best_ask": q.get("best_ask"),
            "spread_ratio": exec_spread_ratio(q)}


def _extract_order(resp):
    if not resp:
        return None
    return resp.get("order") if isinstance(resp, dict) and "order" in resp else resp


# ---------- maker-only 成交（只追一步）----------

def exec_maker_only_fill(side, instrument, target_amount, label=None):
    """返回 dict：
       {filled, avg_price, price0, final_price, dry, steps_used, quote}
    空跑(dry)时只计算并记录意图，不下单（filled=0, dry=True）。"""
    q = exec_quote(instrument)
    if not q or q["best_bid"] is None or q["best_ask"] is None:
        Log("[exec] 盘口缺失，跳过:", instrument)
        return {"filled": 0.0, "dry": False, "quote": q, "reason": "NO_QUOTE"}

    price0 = exec_price_for(side, q["mark"], q["best_bid"], q["best_ask"], q["tick"], 0)
    live = ALLOW_TRADING and not KILL_SWITCH

    if not live:
        intents = [exec_price_for(side, q["mark"], q["best_bid"], q["best_ask"], q["tick"], s)
                   for s in range(MAX_CHASE_STEPS + 1)]
        Log("[exec][DRY] 意图 %s %s amt=%s 计划价(含追价)=%s 盘口=%s/%s mark=%s" %
            (side, instrument, target_amount, intents, q["best_bid"], q["best_ask"], q["mark"]))
        return {"filled": 0.0, "dry": True, "price0": price0,
                "intended_prices": intents, "quote": q}

    # 实盘成交价守门：价差过宽不下单（防高磨损/难成交）
    sr = exec_spread_ratio(q)
    if sr is not None and sr > MAX_SPREAD_RATIO:
        Log("[exec] 价差过宽 %.0f%% > 上限 %.0f%%，放弃下单: %s" %
            (sr * 100, MAX_SPREAD_RATIO * 100, instrument))
        return {"filled": 0.0, "dry": False, "quote": q, "reason": "WIDE_SPREAD"}

    filled = 0.0
    avg_acc = 0.0
    final_price = price0
    steps_used = 0
    for step in range(MAX_CHASE_STEPS + 1):
        remaining = target_amount - filled
        if remaining <= 0:
            break
        price = exec_price_for(side, q["mark"], q["best_bid"], q["best_ask"], q["tick"], step)
        final_price = price
        steps_used = step
        resp = dbt_place_order(side, instrument, remaining, price,
                               post_only=True, reject_post_only=True, label=label)
        order = _extract_order(resp)
        if order is None:
            # reject_post_only 拒单（会越价）→ 视为需要追一步
            Log("[exec] 挂单被拒/失败 step=%s price=%s，尝试追价" % (step, price))
            continue
        oid = order.get("order_id")
        # 等待后查状态
        Sleep(int(CHASE_WAIT_SECONDS * 1000))
        st = _extract_order(dbt_get_order_state(oid)) or order
        fa = st.get("filled_amount") or 0.0
        if fa > 0:
            ap = st.get("average_price") or price
            avg_acc += ap * fa
            filled += fa
        state = st.get("order_state")
        if state not in ("filled",) and (target_amount - filled) > 0:
            # 未完全成交 → 撤掉残单，进入下一步追价
            dbt_cancel(oid)
        if filled >= target_amount:
            break

    avg_price = (avg_acc / filled) if filled > 0 else final_price
    return {"filled": filled, "avg_price": avg_price, "price0": price0,
            "final_price": final_price, "dry": False, "steps_used": steps_used,
            "quote": q}


# ---------- 保护腿优先开仓（§10.1）----------

def exec_open_structure(short_instrument, protection_instrument, amount):
    """先买 protection，再以 min(amount, 已成交保护量) 卖 short。
    返回 {protection_fill, short_fill, short_amount}。
    空跑下两腿都只记录意图。"""
    prot = exec_maker_only_fill("buy", protection_instrument, amount,
                                label="prot")
    if prot.get("dry"):
        short = exec_maker_only_fill("sell", short_instrument, amount, label="short")
        return {"protection_fill": prot, "short_fill": short, "short_amount": amount,
                "dry": True}

    filled_prot = prot.get("filled", 0.0)
    if filled_prot <= 0:
        Log("[exec] 保护腿未成交，按保护腿优先原则不卖 short")
        return {"protection_fill": prot, "short_fill": None, "short_amount": 0.0,
                "dry": False}

    short_amount = min(amount, filled_prot)   # 硬保证 short <= protection 可用量
    short = exec_maker_only_fill("sell", short_instrument, short_amount, label="short")
    result = {"protection_fill": prot, "short_fill": short,
              "short_amount": short_amount, "dry": False}
    # 短腿未成交 → 自动 maker 卖回保护腿，避免裸保护（一次尝试）
    if (short or {}).get("filled", 0.0) <= 0 and UNWIND_PROTECTION_ON_NO_SHORT:
        Log("[exec] 短腿未成交，自动卖回保护腿避免裸保护:", protection_instrument)
        result["unwind"] = exec_maker_only_fill("sell", protection_instrument,
                                                filled_prot, label="unwind")
    return result

# ===================== module: ledger =====================
# -*- coding: utf-8 -*-
"""
库存账本 + 状态机 + 持久化 + 启动对账（§8 / §9）。

库存与状态经 FMZ `_G()` 持久化，崩溃/重启后可恢复。启动时与交易所实际持仓做基础对账，
不一致仅告警，不自动改仓（自动恢复留 v1.1）。
"""


# ---------- 状态机（§9）----------
S_NO_POSITION              = "NO_POSITION"
S_SIGNAL_READY             = "SIGNAL_READY"
S_PROTECTION_SELECTION     = "PROTECTION_SELECTION"
S_SPM_SIMULATION           = "SPM_SIMULATION"
S_PROTECTION_BUILDING      = "PROTECTION_BUILDING"
S_PROTECTION_ACTIVE_NO_SHORT = "PROTECTION_ACTIVE_NO_SHORT"
S_SHORT_BUILDING           = "SHORT_BUILDING"
S_SHORT_ACTIVE_PROTECTED   = "SHORT_ACTIVE_PROTECTED"
S_HOLD_MONITORING          = "HOLD_MONITORING"
S_SHORT_EXPIRED_OR_CLOSED  = "SHORT_EXPIRED_OR_CLOSED"
S_REUSE_DECISION           = "REUSE_DECISION"
S_EXIT_OR_WAIT_REVIEW      = "EXIT_OR_WAIT_REVIEW"
S_CLOSED                   = "CLOSED"

_LEDGER_KEY = "spm_ledger_v1"
_STATE_KEY = "spm_state_v1"

_DEFAULT_LEDGER = {
    "protection": None,   # 单条保护腿库存（v1 限 1 张覆盖 1 张，§8.2）
    "short": None,        # 当前近端 short 腿
    "history": [],        # 已结束结构的记账留档
}


# ---------- 持久化 ----------

def ledger_load():
    led = _G(_LEDGER_KEY)
    if not led:
        led = dict(_DEFAULT_LEDGER)
        led["history"] = []
    return led


def ledger_save(led):
    _G(_LEDGER_KEY, led)
    return led


def ledger_get_state():
    return _G(_STATE_KEY) or S_NO_POSITION


def ledger_set_state(state):
    _G(_STATE_KEY, state)
    Log("[state] ->", state)
    return state


# ---------- 库存记录（§8.1）----------

def ledger_make_inventory(instrument, option_type, strike, expiry,
                          amount, entry_price, entry_fee, margin_relief_ratio):
    return {
        "inventory_id": "prot_" + str(instrument),
        "instrument": instrument,
        "option_type": option_type,
        "strike": strike,
        "expiry": expiry,
        "amount_total": amount,
        "amount_free": amount,
        "amount_allocated": 0.0,
        "entry_price": entry_price,
        "entry_fee": entry_fee,
        "current_mark": entry_price,
        "unrealized_pnl": 0.0,
        "realized_short_premium_against_it": 0.0,
        "reuse_count": 0,
        "last_margin_relief_ratio": margin_relief_ratio,
        "status": "AVAILABLE",
    }


def ledger_allocate_short(led, amount):
    """short 占用保护腿可用量（硬保证 short <= amount_free）。"""
    prot = led.get("protection")
    if not prot:
        return False
    if amount > prot["amount_free"] + 1e-12:
        Log("[ledger] 拒绝：short 数量 %s > 保护腿可用 %s" % (amount, prot["amount_free"]))
        return False
    prot["amount_free"] -= amount
    prot["amount_allocated"] += amount
    return True


def ledger_release_short(led, amount):
    prot = led.get("protection")
    if not prot:
        return
    prot["amount_free"] += amount
    prot["amount_allocated"] = max(0.0, prot["amount_allocated"] - amount)


# ---------- 进场门控（§4.1）----------

def ledger_can_enter(signal_state, enter_signals):
    return signal_state in enter_signals


# ---------- 启动对账（§5 缺口补强）----------

def ledger_reconcile(currency, kind="option"):
    """对比 _G 账本与交易所实际期权持仓，不一致告警，不自动改仓。"""
    led = ledger_load()
    positions = dbt_get_positions(currency, kind) or []
    actual = {}
    for p in positions:
        inst = p.get("instrument_name")
        sz = p.get("size")
        if inst and sz:
            actual[inst] = sz

    expected = {}
    prot = led.get("protection")
    if prot and prot.get("status") == "AVAILABLE":
        expected[prot["instrument"]] = prot["amount_total"]
    sh = led.get("short")
    if sh:
        expected[sh["instrument"]] = -sh.get("amount", 0.0)

    for inst, sz in expected.items():
        a = actual.get(inst)
        if a is None:
            Log("[reconcile] 告警：账本有 %s(%s) 但交易所无持仓" % (inst, sz))
        elif abs(a - sz) > 1e-9:
            Log("[reconcile] 告警：%s 账本=%s 实际=%s 不一致" % (inst, sz, a))
    for inst, sz in actual.items():
        if inst not in expected:
            Log("[reconcile] 告警：交易所有持仓 %s(%s) 但账本未记录" % (inst, sz))

    return {"ledger": led, "actual": actual, "expected": expected}

# ===================== module: hedge_risk =====================
# -*- coding: utf-8 -*-
"""
Post-entry hedge risk evaluator.

The module is deliberately pure: it produces PositionRiskPackage and optional
dry-run HedgeIntentPackage, but it never places orders or mutates the option
ledger. It uses EDB as the aggregate signal input and keeps KPF/GGR as boundary
and persistence modifiers, not as probability predictors.
"""
import math


SCHEMA_NAME = "PositionRiskPackage"
SCHEMA_VERSION = "nrd.integration.position_risk.v0.3"

STATE_NORMAL = "NORMAL"
STATE_WATCH = "WATCH"
STATE_EXIT_PREFERRED = "EXIT_PREFERRED"
STATE_HEDGE_READY = "HEDGE_READY"
STATE_HEDGE_ACTIVE = "HEDGE_ACTIVE"
STATE_MANUAL_REVIEW = "MANUAL_REVIEW"

EXECUTION_DRY_INTENT_ONLY = "DRY_INTENT_ONLY"

PERSISTENCE_LOW = "LOW"
PERSISTENCE_MEDIUM = "MEDIUM"
PERSISTENCE_HIGH = "HIGH"

SIDE_SHORT_CALL = "SHORT_CALL"
SIDE_SHORT_PUT = "SHORT_PUT"

BUY_HEDGE = "BUY_FUTURE_OR_PERP"
SELL_HEDGE = "SELL_FUTURE_OR_PERP"

_BAD_KPF_BUFFER_STATES = set([
    "ABSENT", "NO_BUFFER", "TOUCHED", "PENETRATED", "FAILED", "INVALID",
])


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _safe_float(v):
    try:
        if v is None:
            return None
        out = float(v)
        if not math.isfinite(out):
            return None
        return out
    except Exception:
        return None


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _normalise_iv(iv):
    vol = _safe_float(iv)
    if vol is None or vol <= 0:
        return None
    # Accept either decimal IV (0.7) or percent IV (70).
    return vol / 100.0 if vol > 3.0 else vol


def _is_short_call(direction_bias):
    return direction_bias == SIDE_SHORT_CALL


def _breached(direction_bias, price, boundary):
    if _is_short_call(direction_bias):
        return price >= boundary
    return price <= boundary


def boundary_distance_pct(direction_bias, price, loss_boundary):
    price, boundary = _safe_float(price), _safe_float(loss_boundary)
    if price is None or price <= 0 or boundary is None or boundary <= 0:
        return None
    if _is_short_call(direction_bias):
        return (boundary - price) / price * 100.0
    return (price - boundary) / price * 100.0


def estimate_touch_probability(direction_bias, price, loss_boundary,
                               dte_hours, iv=None, short_delta=None):
    """Estimate first-touch probability to the loss boundary before expiry.

    This is a risk-control estimate, not a real-world win-rate claim. IV-based
    output uses the driftless lognormal barrier approximation. Delta fallback is
    intentionally conservative and marked low-confidence by callers.
    """
    price = _safe_float(price)
    boundary = _safe_float(loss_boundary)
    dte = _safe_float(dte_hours)
    if price is None or price <= 0 or boundary is None or boundary <= 0:
        return 0.0
    if _breached(direction_bias, price, boundary):
        return 1.0
    if dte is None or dte <= 0:
        return 0.0

    vol = _normalise_iv(iv)
    if vol is not None:
        t_years = dte / (24.0 * 365.0)
        sigma_t = vol * math.sqrt(max(t_years, 1e-12))
        if sigma_t <= 1e-12:
            return 0.0
        if _is_short_call(direction_bias):
            distance = math.log(boundary / price)
        else:
            distance = math.log(price / boundary)
        if distance <= 0:
            return 1.0
        z = distance / sigma_t
        return _clamp(2.0 * (1.0 - _norm_cdf(z)), 0.0, 0.98)

    delta = _safe_float(short_delta)
    if delta is None:
        return 0.0
    return _clamp(abs(delta) * 1.8, 0.0, 0.95)


def _probability_confidence(iv):
    return "HIGH" if _normalise_iv(iv) is not None else "LOW"


def build_entry_risk_anchor(direction_bias, entry_price, entry_dte_hours,
                            entry_short_delta, entry_short_gamma, entry_iv,
                            entry_loss_boundary, entry_edb_side="",
                            entry_gamma_regime="", entry_kpf_buffer_state=""):
    p = estimate_touch_probability(
        direction_bias, entry_price, entry_loss_boundary, entry_dte_hours,
        entry_iv, entry_short_delta)
    return {
        "entry_price": entry_price,
        "entry_dte_hours": entry_dte_hours,
        "entry_short_delta": entry_short_delta,
        "entry_short_gamma": entry_short_gamma,
        "entry_iv": entry_iv,
        "entry_loss_boundary": entry_loss_boundary,
        "entry_touch_probability": p,
        "entry_probability_confidence": _probability_confidence(entry_iv),
        "entry_boundary_distance_pct": boundary_distance_pct(
            direction_bias, entry_price, entry_loss_boundary),
        "entry_edb_side": entry_edb_side,
        "entry_gamma_regime": entry_gamma_regime,
        "entry_kpf_buffer_state": entry_kpf_buffer_state,
    }


def _recent_slope(current_probability, recent_history, now_ms,
                  recent_window_ms=30 * 60 * 1000):
    now = _safe_float(now_ms)
    if now is None:
        return 0.0
    usable = []
    for item in recent_history or []:
        ts = _safe_float((item or {}).get("ts_ms"))
        p = _safe_float((item or {}).get("touch_probability"))
        if ts is None or p is None:
            continue
        age = now - ts
        if 0 <= age <= recent_window_ms:
            usable.append((ts, p))
    if not usable:
        return 0.0
    ts, p0 = sorted(usable, key=lambda x: x[0])[0]
    hours = max((now - ts) / (60.0 * 60.0 * 1000.0), 1e-9)
    return (current_probability - p0) / hours


def _tail_exposure_acceleration(direction_bias, current_price, loss_boundary,
                                short_delta, short_gamma, entry_anchor):
    if _breached(direction_bias, _safe_float(current_price) or 0.0,
                 _safe_float(loss_boundary) or 0.0):
        return PERSISTENCE_HIGH
    delta = abs(_safe_float(short_delta) or 0.0)
    gamma = abs(_safe_float(short_gamma) or 0.0)
    entry_gamma = abs(_safe_float(
        (entry_anchor or {}).get("entry_short_gamma")) or 0.0)
    gamma_ratio = gamma / entry_gamma if entry_gamma > 0 else 0.0
    if delta >= 0.70 or gamma_ratio >= 2.0:
        return PERSISTENCE_HIGH
    if delta >= 0.50 or gamma_ratio >= 1.4:
        return PERSISTENCE_MEDIUM
    return PERSISTENCE_LOW


def _edb_adverse(direction_bias, edb):
    edb = edb or {}
    confidence = _safe_float(edb.get("confidence")) or 0.0
    coverage = _safe_float(edb.get("coverage"))
    if coverage is None:
        coverage = 1.0 if confidence >= 50 else 0.0
    if confidence < 50 or coverage < 0.50:
        return False
    lean = str(edb.get("lean") or edb.get("side_hint") or "").upper()
    if _is_short_call(direction_bias):
        return lean in ("BULLISH", "UP", "LONG", "SHORT_PUT", "PUT_CREDIT_SPREAD")
    return lean in ("BEARISH", "DOWN", "SHORT", "SHORT_CALL", "CALL_CREDIT_SPREAD")


def _ggr_adverse(gamma_regime, gex_info=None, current_price=None):
    ggr = gamma_regime or {}
    if bool(ggr.get("veto")):
        return True
    regime = str(ggr.get("regime") or "").upper()
    dist = _safe_float(ggr.get("distance_to_flip_pct"))
    if regime == "NEGATIVE_GAMMA_AMPLIFYING":
        return dist is None or abs(dist) <= 1.0
    gate = str(((ggr.get("ggr_gate") or {}).get("regime")) or "").upper()
    if gate == "NEGATIVE_GAMMA_AMPLIFYING":
        return True
    # SOFT corroboration from gexmonitorapi: a clean negative-gamma read with
    # price already through the volatility trigger reinforces the SAME
    # GGR_ADVERSE confirmation (no new count, no double-vote). It only ever ADDS
    # conservatism and cannot create risk pre-emptively, because persistence is
    # inert until risk is already elevated (see _state_from_inputs). With
    # gex_info=None this is byte-identical to the prior behavior.
    return _gex_info_negative_gamma_zone(gex_info, current_price)


def _gex_info_negative_gamma_zone(gex_info, current_price):
    """True only when a clean gex_info read shows negative gamma AND price has
    reached/broken the volatility trigger (dealer negative-gamma hedging
    acceleration zone). Both conditions required; any missing input => False."""
    info = gex_info if isinstance(gex_info, dict) else None
    if info is None:
        return False
    market_state = str(info.get("market_state") or "").lower()
    net_gex = _safe_float(info.get("total_net_gex"))
    negative_regime = (
        market_state == "negative_gamma"
        or (net_gex is not None and net_gex < 0))
    if not negative_regime:
        return False
    price = _safe_float(current_price)
    trigger = _safe_float(info.get("volatility_trigger"))
    if price is None or trigger is None or trigger <= 0:
        return False
    return price <= trigger


def _kpf_buffer_adverse(kpf_context):
    state = str((kpf_context or {}).get("buffer_state") or "").upper()
    return state in _BAD_KPF_BUFFER_STATES


def persistence_score(direction_bias, edb=None, gamma_regime=None,
                      kpf_context=None, gex_info=None, current_price=None):
    confirmations = []
    if _edb_adverse(direction_bias, edb):
        confirmations.append("EDB_ADVERSE")
    if _ggr_adverse(gamma_regime, gex_info, current_price):
        confirmations.append("GGR_ADVERSE")
    if _kpf_buffer_adverse(kpf_context):
        confirmations.append("KPF_BUFFER_WEAK_OR_BROKEN")
    count = len(confirmations)
    if count >= 3:
        score = PERSISTENCE_HIGH
    elif count == 2:
        score = PERSISTENCE_MEDIUM
    else:
        score = PERSISTENCE_LOW
    return score, confirmations


def _friction_score(value):
    text = str(value or "").upper()
    if text in ("EXTREME", "VERY_HIGH", "BLOCKED"):
        return 4
    if text in ("HIGH", "POOR", "WIDE", "EXPENSIVE"):
        return 3
    if text in ("MEDIUM", "FAIR", "NORMAL"):
        return 2
    if text in ("LOW", "GOOD", "OK", "CHEAP"):
        return 1
    return 2


def exit_vs_hedge_friction(exit_friction):
    data = exit_friction or {}
    option_score = _friction_score(data.get("option_exit_friction"))
    hedge_score = _friction_score(data.get("future_hedge_friction"))
    hedge_preferred = option_score - hedge_score >= 1 and option_score >= 3
    return {
        "option_exit_friction": data.get("option_exit_friction"),
        "future_hedge_friction": data.get("future_hedge_friction"),
        "option_exit_score": option_score,
        "future_hedge_score": hedge_score,
        "hedge_friction_advantage": hedge_preferred,
    }


def _state_from_inputs(probability_now, drift, slope, persistence, friction,
                       existing_hedge=False):
    if existing_hedge:
        return STATE_HEDGE_ACTIVE
    risk_elevated = (
        probability_now >= 0.50 or drift >= 0.10 or slope >= 0.20)
    if not risk_elevated:
        return STATE_NORMAL
    risk_severe = (
        probability_now >= 0.65 or drift >= 0.20 or slope >= 0.30)
    if (risk_severe and persistence in (PERSISTENCE_MEDIUM, PERSISTENCE_HIGH)
            and friction.get("hedge_friction_advantage")):
        return STATE_HEDGE_READY
    if not friction.get("hedge_friction_advantage"):
        return STATE_EXIT_PREFERRED
    return STATE_WATCH


def _hedge_side(direction_bias):
    return BUY_HEDGE if _is_short_call(direction_bias) else SELL_HEDGE


def _hedge_size_mode(probability_now, drift, tail_acceleration,
                     persistence, breached):
    if breached or probability_now >= 0.80:
        return "FULL", 0.90
    if tail_acceleration == PERSISTENCE_HIGH and persistence == PERSISTENCE_HIGH:
        return "FULL", 0.90
    if probability_now >= 0.60 or drift >= 0.25 or persistence == PERSISTENCE_HIGH:
        return "MEDIUM", 0.60
    return "LIGHT", 0.30


def _make_hedge_intent(position_id, direction_bias, probability_now, drift,
                       tail_acceleration, persistence, breached):
    mode, ratio = _hedge_size_mode(
        probability_now, drift, tail_acceleration, persistence, breached)
    return {
        "schema_name": "HedgeIntentPackage",
        "schema_version": "nrd.integration.hedge_intent.v0.1",
        "position_id": position_id,
        "hedge_side": _hedge_side(direction_bias),
        "hedge_size_mode": mode,
        "target_delta_reduction_ratio": ratio,
        "adapter_preference": "UNBOUND",
        "execution_mode": EXECUTION_DRY_INTENT_ONLY,
        "reason_codes": ["DRY_INTENT_ONLY", "TAIL_RISK_HEDGE_READY"],
    }


def _manual_review_package(position_id, entry_anchor, reason):
    return {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "position_id": position_id,
        "entry_risk_anchor": entry_anchor or {},
        "current_risk": {
            "touch_probability_now": 0.0,
            "touch_probability_drift": 0.0,
            "recent_deterioration_slope": 0.0,
            "tail_exposure_acceleration": PERSISTENCE_LOW,
            "persistence": PERSISTENCE_LOW,
        },
        "exit_vs_hedge_friction": {},
        "tail_risk_state": STATE_MANUAL_REVIEW,
        "hedge_intent": None,
        "reason_codes": [reason],
    }


def evaluate_position_risk(position_id, direction_bias, entry_risk_anchor,
                           current_price, dte_hours, short_delta,
                           short_gamma, iv, loss_boundary, edb=None,
                           gamma_regime=None, kpf_context=None,
                           exit_friction=None, recent_history=None,
                           now_ms=None, existing_hedge=False, gex_info=None):
    if direction_bias not in (SIDE_SHORT_CALL, SIDE_SHORT_PUT):
        return _manual_review_package(
            position_id, entry_risk_anchor, "INVALID_DIRECTION_BIAS")
    if not entry_risk_anchor or "entry_touch_probability" not in entry_risk_anchor:
        return _manual_review_package(
            position_id, entry_risk_anchor, "MISSING_ENTRY_RISK_ANCHOR")

    p_now = estimate_touch_probability(
        direction_bias, current_price, loss_boundary, dte_hours, iv,
        short_delta)
    p_entry = _safe_float(entry_risk_anchor.get("entry_touch_probability")) or 0.0
    drift = p_now - p_entry
    slope = _recent_slope(p_now, recent_history, now_ms)
    tail_acc = _tail_exposure_acceleration(
        direction_bias, current_price, loss_boundary, short_delta,
        short_gamma, entry_risk_anchor)
    persistence, confirmations = persistence_score(
        direction_bias, edb, gamma_regime, kpf_context,
        gex_info=gex_info, current_price=current_price)
    friction = exit_vs_hedge_friction(exit_friction)
    state = _state_from_inputs(
        p_now, drift, slope, persistence, friction, existing_hedge)
    breached = _breached(
        direction_bias, _safe_float(current_price) or 0.0,
        _safe_float(loss_boundary) or 0.0)
    hedge_intent = None
    if state == STATE_HEDGE_READY:
        hedge_intent = _make_hedge_intent(
            position_id, direction_bias, p_now, drift, tail_acc, persistence,
            breached)

    reason_codes = list(confirmations)
    if drift > 0:
        reason_codes.append("TOUCH_PROBABILITY_DRIFT_POSITIVE")
    if slope > 0:
        reason_codes.append("RECENT_DETERIORATION_SLOPE_POSITIVE")
    if friction.get("hedge_friction_advantage"):
        reason_codes.append("HEDGE_FRICTION_ADVANTAGE")

    return {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "position_id": position_id,
        "entry_risk_anchor": entry_risk_anchor,
        "current_risk": {
            "touch_probability_now": p_now,
            "touch_probability_drift": drift,
            "recent_deterioration_slope": slope,
            "tail_exposure_acceleration": tail_acc,
            "persistence": persistence,
        },
        "exit_vs_hedge_friction": friction,
        "tail_risk_state": state,
        "hedge_intent": hedge_intent,
        "reason_codes": reason_codes,
    }

# ===================== module: strategy =====================
# -*- coding: utf-8 -*-
"""
主编排 main()（FMZ 入口）。两轮分离，运行后不经界面命令调整计划或仓位。

计划轮 ROUND_MODE="PLAN"：
  枚举所有符合范围(剩余到期/delta/腿宽)的备选(垂直+日历) → 初筛 top-K → S:PM 模拟 →
  按 胜率/盈亏比/KPF/信号 综合排序 → 输出方案库(含方案号+推荐标签)并持久化(_G)。绝不下单。
下单轮 ROUND_MODE="ORDER"：
  读取持久化方案库，按 SELECTED_PLAN 取方案号 → 重新取价+模拟复核 → 仅 ALLOW_TRADING=True 才真实开仓。

约定：本项目内一律用「裸名 + 模块前缀」，合成单文件后位于同一命名空间，bundle 仅剥离项目内 import。
"""

import time


_MENU_KEY = "spm_plan_menu_v1"
_LAST = {"plan_ms": 0}
# 选用方案明细锁定：启动时锁定一个方案的编号，之后不随方案库刷新而改变（重启复位）
_LOCKED = {"detail_id": None}


def _now_ms():
    return int(time.time() * 1000)


def _spot_price():
    if UNDERLYING_REF_PRICE:
        return UNDERLYING_REF_PRICE
    return dbt_index_price(SETTLEMENT_CURRENCY)


def _delta_lookup():
    cache = {}

    def fn(inst):
        if inst not in cache:
            t = dbt_ticker(inst) or {}
            cache[inst] = (t.get("greeks") or {}).get("delta")
        return cache[inst]
    return fn


def _quote_cache():
    cache = {}

    def fn(inst):
        if inst not in cache:
            cache[inst] = exec_quote(inst)
        return cache[inst]
    return fn


def _first_in_width(prots):
    lo, hi = PROTECTION_WIDTH_RANGE
    for p in prots:
        if lo <= p.get("_width", 1e18) <= hi:
            return p
    return None


# ---------- 计划轮：方案库构建 ----------

def _build_menu(now_ms, spot):
    """枚举(垂直[+日历])→初筛→top-K 跑 S:PM→排序。返回 (menu, pm_ok, model, reason, diag)。
    diag = 枚举漏斗计数，用于看清是哪个门控在生效（无候选时尤其有用）。"""
    want_call = legsel_is_call_bias(DIRECTION_BIAS)
    delta_fn, quote_fn = _delta_lookup(), _quote_cache()
    diag = {"短腿扫描": 0, "delta区间外": 0, "无报价/无买盘": 0, "权利金过薄": 0,
            "价差过宽": 0, "无合格保护腿(腿宽内)": 0, "生成候选": 0, "进入菜单": 0, "合格": 0}
    instruments = dbt_get_instruments(SETTLEMENT_CURRENCY, "option")
    if not instruments:
        return [], False, None, "NO_INSTRUMENTS", diag
    short_exps = legsel_expiries_in_band(instruments, SHORT_DTE_HOURS[0], SHORT_DTE_HOURS[1],
                                         now_ms, want_call)
    if not short_exps:
        return [], False, None, "NO_SHORT_EXPIRY_IN_BAND", diag
    cal_exps = (legsel_expiries_in_band(instruments, PROT_DTE_DAYS[0] * 24, PROT_DTE_DAYS[1] * 24,
                                        now_ms, want_call) if ENABLE_CALENDAR else {})
    pm_ok, model = spm_account_is_portfolio_margin(dbt_account_summary(SETTLEMENT_CURRENCY))
    pref = plan_preferred_delta(SIGNAL_STATE, SIGNAL_CONFIDENCE, SHORT_DELTA_RANGE)
    dmin, dmax = SHORT_DELTA_RANGE

    prelim = []
    for s_exp, s_insts in short_exps.items():
        s_dte_h = legsel_dte_hours(s_exp, now_ms)
        for short in legsel_short_enriched(s_insts, spot, want_call, delta_fn):
            diag["短腿扫描"] += 1
            if not (dmin <= abs(short["_delta"]) <= dmax):
                diag["delta区间外"] += 1
                continue
            sq = quote_fn(short["instrument_name"])
            if not sq or sq.get("best_bid") in (None, 0) or sq.get("mark") is None:
                diag["无报价/无买盘"] += 1
                continue
            if sq["mark"] < MIN_SHORT_PREMIUM:
                diag["权利金过薄"] += 1
                continue
            ssr = exec_spread_ratio(sq)
            if ssr is not None and ssr > MAX_SPREAD_RATIO:
                diag["价差过宽"] += 1
                continue
            specs = []
            # 同期垂直：长腿是定额风险封顶，便宜的 OTM 长腿正是所需 → **不套用过度虚值过滤**
            vprot = _first_in_width(legsel_protection_candidates(
                s_insts, short["strike"], want_call, PROTECTION_WIDTH_RANGE,
                None, 0.0, KPF_FAR_RISK_ZONE))
            if vprot:
                specs.append((MODE_VERTICAL, vprot, s_dte_h))
            # 日历：远期长腿需有意义的 delta/vega → 套用过度虚值过滤
            for c_exp, c_insts in cal_exps.items():
                cprot = _first_in_width(legsel_protection_candidates(
                    c_insts, short["strike"], want_call, PROTECTION_WIDTH_RANGE,
                    delta_fn, DEEP_OTM_MAX_DELTA, KPF_FAR_RISK_ZONE))
                if cprot:
                    specs.append((MODE_CALENDAR, cprot, legsel_dte_hours(c_exp, now_ms)))
            if not specs:
                diag["无合格保护腿(腿宽内)"] += 1
                continue
            for mode, prot, p_dte_h in specs:
                pq = quote_fn(prot["instrument_name"])
                if not pq or pq.get("mark") is None:
                    continue
                c = plan_assemble(mode, ORDER_AMOUNT, spot, MIN_MARGIN_RELIEF_RATIO,
                                  PROTECTION_RESIDUAL_RECOVERY, pref, KPF_CONTESTED_CORE,
                                  KPF_FAR_RISK_ZONE, want_call, short, sq, prot, pq,
                                  None, pm_ok, model, s_dte_h, p_dte_h)
                c["_re"] = {"mode": mode, "short": short, "sq": sq, "prot": prot, "pq": pq,
                            "s_dte": s_dte_h, "p_dte": p_dte_h}
                prelim.append(c)
                diag["生成候选"] += 1

    if not prelim:
        return [], pm_ok, model, "NO_CANDIDATE", diag
    prelim.sort(key=lambda c: plan_prelim_score(c, PLAN_WEIGHTS), reverse=True)
    topk = prelim[:max(MENU_SIZE * 2, MENU_SIZE)]

    final = []
    for c in topk:                                    # 仅对 top-K 跑 S:PM（控制 API 调用）
        re = c["_re"]
        spm = spm_simulate_structure(SETTLEMENT_CURRENCY, re["short"]["instrument_name"],
                                     re["prot"]["instrument_name"], ORDER_AMOUNT)
        final.append(plan_assemble(
            re["mode"], ORDER_AMOUNT, spot, MIN_MARGIN_RELIEF_RATIO,
            PROTECTION_RESIDUAL_RECOVERY, pref, KPF_CONTESTED_CORE, KPF_FAR_RISK_ZONE,
            want_call, re["short"], re["sq"], re["prot"], re["pq"], spm, pm_ok, model,
            re["s_dte"], re["p_dte"]))
    menu = plan_rank(final, PLAN_WEIGHTS, MENU_SIZE)
    diag["进入菜单"] = len(menu)
    diag["合格"] = sum(1 for c in menu if c.get("qualified"))
    return menu, pm_ok, model, "OK", diag


# ---------- ctx 组装 ----------

def _ctx_base(state, spot, reason=None):
    return {
        "version": STRATEGY_VERSION,
        "currency": SETTLEMENT_CURRENCY, "signal_state": SIGNAL_STATE,
        "direction_bias": DIRECTION_BIAS, "allow_trading": ALLOW_TRADING,
        "round_mode": ROUND_MODE, "signal_confidence": SIGNAL_CONFIDENCE,
        "state": state, "kpf_near": KPF_NEAR_BOUNDARY, "kpf_far": KPF_FAR_RISK_ZONE,
        "max_chase_steps": MAX_CHASE_STEPS, "min_required_ratio": MIN_MARGIN_RELIEF_RATIO,
        "reason": reason, "spot": spot, "amount": ORDER_AMOUNT,
        "selected_plan": SELECTED_PLAN, "protection_mode": None,
    }


def _flat_plan_fields(p):
    return dict(
        short_instrument=p["short_instrument"], short_strike=p["short_strike"],
        short_dte_hours=p["short_dte_hours"], short_mark=p["short_mark"],
        short_bid=p["short_bid"], short_ask=p["short_ask"], short_tick=p["short_tick"],
        short_delta=p["short_delta"],
        protection_instrument=p["protection_instrument"], protection_strike=p["protection_strike"],
        protection_dte_days=p["protection_dte_days"], protection_mark=p["protection_mark"],
        protection_bid=p["protection_bid"], protection_ask=p["protection_ask"],
        protection_tick=p["protection_tick"], protection_delta=p["protection_delta"],
        im_short_only=p["im_short_only"], im_with_protection=p["im_with_protection"],
        margin_relief_abs=p["margin_relief_abs"], margin_relief_ratio=p["margin_relief_ratio"],
        pm_accepted=p["pm_ok"], account_margin_model=p["account_model"],
        short_premium_income=p["premium_income"], estimated_entry_fee=p["entry_fee"],
        estimated_spread_cost=p["spread_cost"], protection_entry_cost=p["protection_premium"],
        full_burn_cost=p["full_burn"],
        win_rate=p["win_rate"], net_credit=p["net_credit_effective"],
        net_credit_single=p["net_credit_single"], max_loss=p["max_loss"], rr=p["rr"],
        ev=p.get("ev"),
        covered_cycles=p["covered_cycles"], residual_value=p["residual_value"],
        amortized_cost_per_cycle=p["amortized_cost_per_cycle"],
        protection_mode=p["mode"], protection_mode_cn=p["mode_cn"], plan_tags=p.get("tags"),
        selected_id=p.get("id"),
        short_expiry_label=p.get("short_expiry_label"),
        protection_expiry_label=p.get("protection_expiry_label"),
        protection_dte_hours=p.get("protection_dte_hours"),
        breakeven=p.get("breakeven"), credit_on_margin=p.get("credit_on_margin"),
    )


def _ctx_with_menu(state, spot, reason, menu, selected_no, detail_plan):
    ctx = _ctx_base(state, spot, reason)
    ctx["menu"] = menu
    ctx["selected_plan"] = selected_no
    if detail_plan:
        ctx.update(_flat_plan_fields(detail_plan))
    return ctx


def _emit(ctx, note=""):
    LogStatus(disp_status_panel(ctx, note))
    Log(disp_log_summary(ctx, note))


# ---------- 计划轮 ----------

def _plan_round(spot):
    now_ms = _now_ms()
    if not spot:
        return _ctx_base(S_NO_POSITION, spot, "NO_SPOT")
    menu, pm_ok, model, reason, diag = _build_menu(now_ms, spot)
    if reason != "OK" or not menu:
        ctx = _ctx_base(S_NO_POSITION, spot, reason)
        ctx["enum_diag"] = diag                       # 无候选时也展示漏斗，便于定位门控
        return ctx
    _G(_MENU_KEY, menu)                               # 持久化方案库供下单轮按【编号】取用
    # 锁定「选用方案明细」：启动时取最推荐(SELECTED_PLAN 有效则用，否则综合分#1)，
    # 之后不随方案库刷新而改变；仅当锁定项掉出方案库时才重新锁定。
    ids = [c["id"] for c in menu]
    if _LOCKED["detail_id"] not in ids:
        _LOCKED["detail_id"] = SELECTED_PLAN if SELECTED_PLAN in ids else menu[0]["id"]
    detail = next((c for c in menu if c["id"] == _LOCKED["detail_id"]), menu[0])
    r = "PLAN_MENU_READY"
    if SIGNAL_STATE not in ENTER_SIGNALS:
        r = "PLAN_MENU_READY(注意:当前信号 %s 不放行进场)" % SIGNAL_STATE
    ctx = _ctx_with_menu(S_PROTECTION_SELECTION, spot, r, menu, _LOCKED["detail_id"], detail)
    ctx["enum_diag"] = diag
    return ctx


# ---------- 下单轮 ----------

def _run_order(led, spot):
    menu = _G(_MENU_KEY) or []
    if not menu:
        return S_NO_POSITION, _ctx_base(S_NO_POSITION, spot, "NO_PLAN_MENU(请先运行计划轮)")
    sel = next((c for c in menu if c.get("id") == SELECTED_PLAN), None)
    if not sel:
        return S_NO_POSITION, _ctx_with_menu(S_NO_POSITION, spot,
                                             "PLAN_ID_NOT_IN_MENU:%s" % SELECTED_PLAN, menu, SELECTED_PLAN, None)

    # 复核：重新取价 + 重新模拟 S:PM
    want_call = legsel_is_call_bias(DIRECTION_BIAS)
    pref = plan_preferred_delta(SIGNAL_STATE, SIGNAL_CONFIDENCE, SHORT_DELTA_RANGE)
    short = {"instrument_name": sel["short_instrument"], "strike": sel["short_strike"],
             "expiration_timestamp": sel.get("short_expiry"), "_delta": sel.get("short_delta")}
    prot = {"instrument_name": sel["protection_instrument"], "strike": sel["protection_strike"],
            "expiration_timestamp": sel.get("protection_expiry")}
    sq, pq = exec_quote(short["instrument_name"]), exec_quote(prot["instrument_name"])
    spm = spm_simulate_structure(SETTLEMENT_CURRENCY, short["instrument_name"],
                                 prot["instrument_name"], ORDER_AMOUNT)
    pm_ok, model = spm_account_is_portfolio_margin(dbt_account_summary(SETTLEMENT_CURRENCY))
    rv = plan_assemble(sel["mode"], ORDER_AMOUNT, spot, MIN_MARGIN_RELIEF_RATIO,
                       PROTECTION_RESIDUAL_RECOVERY, pref, KPF_CONTESTED_CORE, KPF_FAR_RISK_ZONE,
                       want_call, short, sq, prot, pq, spm, pm_ok, model,
                       sel.get("short_dte_hours"), sel.get("protection_dte_hours"))
    ctx = _ctx_with_menu(S_SPM_SIMULATION, spot, None, menu, SELECTED_PLAN, rv)
    # 「将下达订单」意图（保护腿优先在前）
    ctx["order_intent"] = [
        dict(leg="保护腿", **exec_plan_prices("buy", prot["instrument_name"], ORDER_AMOUNT)),
        dict(leg="卖方腿", **exec_plan_prices("sell", short["instrument_name"], ORDER_AMOUNT)),
    ]

    if not rv["qualified"]:
        ctx["reason"] = "PLAN_NOT_QUALIFIED:" + (rv.get("reject_reason") or "")
        return S_NO_POSITION, ctx

    result = exec_open_structure(short["instrument_name"], prot["instrument_name"], ORDER_AMOUNT)
    if result.get("dry"):
        ctx["reason"] = "ORDER_PREVIEW_DRY"
        return S_NO_POSITION, ctx

    prot_fill = result.get("protection_fill") or {}
    filled_prot = prot_fill.get("filled", 0.0)
    if filled_prot <= 0:
        ctx["reason"] = "PROTECTION_NOT_FILLED"
        return S_NO_POSITION, ctx

    inv = ledger_make_inventory(
        prot["instrument_name"], "CALL" if want_call else "PUT", prot["strike"],
        prot.get("expiration_timestamp"), filled_prot, prot_fill.get("avg_price", 0.0),
        acct_option_fee_ccy(prot_fill.get("avg_price", 0.0), filled_prot),
        rv.get("margin_relief_ratio"))
    inv["mode"] = sel["mode"]
    led["protection"] = inv

    short_fill = result.get("short_fill") or {}
    filled_short = short_fill.get("filled", 0.0)
    if filled_short > 0:
        ledger_allocate_short(led, filled_short)
        entry_anchor = build_entry_risk_anchor(
            DIRECTION_BIAS, spot, sel.get("short_dte_hours"),
            rv.get("short_delta"), sq.get("gamma"), sq.get("mark_iv"),
            rv.get("breakeven"), SIGNAL_STATE,
            "UNKNOWN", "CONFIG_STATIC")
        led["short"] = {"instrument": short["instrument_name"], "strike": short["strike"],
                        "amount": filled_short, "expiry": short.get("expiration_timestamp"),
                        "avg_price": short_fill.get("avg_price"), "plan_id": SELECTED_PLAN,
                        "entry_risk_anchor": entry_anchor}
        ledger_save(led)
        ctx["reason"] = "STRUCTURE_OPEN"
        return S_SHORT_ACTIVE_PROTECTED, ctx

    ledger_save(led)
    ctx["reason"] = "PROTECTION_ACTIVE_NO_SHORT"
    return S_PROTECTION_ACTIVE_NO_SHORT, ctx


def _order_loop(spot):
    led = ledger_load()
    state = ledger_get_state()
    if SIGNAL_STATE in EXIT_REVIEW_SIGNALS:                          # §9.2
        ctx = _ctx_base(S_EXIT_OR_WAIT_REVIEW, spot, "EXIT_REVIEW_SIGNAL:" + SIGNAL_STATE)
        ledger_set_state(S_EXIT_OR_WAIT_REVIEW)
        _emit(ctx, "下单轮·退出/复核信号")
    elif state == S_SHORT_ACTIVE_PROTECTED:                          # §4.2/§9.1
        ctx = _ctx_base(state, spot, "SAME_DIRECTION_CONFIRMATION")
        _emit(ctx, "下单轮·持仓中(同向不加仓)")
    elif KILL_SWITCH or not ledger_can_enter(SIGNAL_STATE, ENTER_SIGNALS) \
            or state not in (S_NO_POSITION, S_SIGNAL_READY):
        ctx = _ctx_base(state, spot, "IDLE(kill=%s,signal=%s)" % (KILL_SWITCH, SIGNAL_STATE))
        _emit(ctx, "下单轮·空闲")
    else:
        new_state, ctx = _run_order(led, spot)
        if new_state != S_NO_POSITION:
            ledger_set_state(new_state)
        _emit(ctx, "下单轮" + (" [空跑预览]" if not ALLOW_TRADING else ""))


def main():
    errs = validate_config()
    if errs:
        Log("[config] 配置错误，拒绝运行:", "; ".join(errs))
        LogStatus("配置错误：" + "; ".join(errs))
        return

    Log("[boot] S:PM 卖方执行链 v%s 启动" % STRATEGY_VERSION,
        "轮次=%s" % ROUND_MODE, "ALLOW_TRADING=%s" % ALLOW_TRADING,
        "currency=%s" % SETTLEMENT_CURRENCY)
    if ROUND_MODE == "ORDER":
        ledger_reconcile(SETTLEMENT_CURRENCY)

    while True:
        try:
            if ROUND_MODE == "PLAN":
                # 节流：每 PLAN_REFRESH_SECONDS 才重算一次方案库（省 API + 防刷屏）
                now = _now_ms()
                if now - _LAST["plan_ms"] >= PLAN_REFRESH_SECONDS * 1000:
                    ctx = _plan_round(_spot_price())
                    _emit(ctx, "计划轮·方案库")
                    if not _LAST.get("startup_logged") and ctx.get("menu"):
                        Log(disp_log_menu(ctx["menu"], ctx.get("spot")))   # 启动一轮全量明细入 Log
                        _LAST["startup_logged"] = True
                    _LAST["plan_ms"] = now
            else:
                _order_loop(_spot_price())
        except Exception as e:
            Log("[loop] 异常:", str(e))
        Sleep(LOOP_INTERVAL_MS)
