# -*- coding: utf-8 -*-
# === 自动合成产物：请勿手改，改 src/ 后重新 build_bundle.py ===
# Deribit S:PM 垂直信用价差卖方执行链 v2.1.0（FMZ 单文件；单一 run_cycle 主链 + 交互控制台 + 对冲生命周期）


# ===================== module: config =====================
# -*- coding: utf-8 -*-
"""
配置 & 信号全局变量块（启动前手填）。

v1 不做动态信号通道：方向 / 信号状态 均为预留全局变量，启动前手动填入。
合成进 FMZ 单文件后，这些 UPPER_CASE 名字即位于文件顶部的全局区，FMZ 运行时直接可改。

所有阈值集中在此，避免散落硬编码（补设计稿「阈值未量化」缺口）。
"""

# ===== 版本号（便于迭代区分；显示于启动日志/面板/合成文件头）=====
#   1.0.0 基础执行链 → 1.1.0 短腿按 delta 选档 → 1.2.0 双模+3方案 →
#   1.3.0 计划轮/下单轮分离、垂直+日历全枚举、复用残值修正、去运行时命令 →
#   1.4.0 EV/流动性筛选、计划轮节流、执行健壮性(重试/价差守门/裸保护撤退)、下单意图表、选档指引 →
#   1.5.0 合并「策略选择明细」(期号/双腿/距现价)、稳定唯一编号(按编号匹配执行)、每张mark对齐交易所、综合评级 →
#   1.6.0 日历价差改可选(默认关)、明细锁定启动时最推荐(不随刷新跳动,补期号+剩余到期)、
#         价值指标补盈亏平衡价+净credit/保证金回报率(替 EV 上菜单) →
#   1.6.1 修复：同期垂直长腿不再被「过度虚值(DEEP_OTM)」误过滤(该过滤仅对日历)，解决纯垂直时方案库变空；
#         新增「枚举诊断」漏斗，实时显示各门控砍掉多少候选 →
#   1.6.2 状态栏精简：枚举漏斗并入概览(一行)、删独立「选腿明细/概要/S:PM」表、S:PM+成本合并为一张、
#         策略选择明细补到期+盈亏平衡价(去 #/综合)；启动时整轮方案明细入 Log →
#   2.0.0 v3 重构：删日历(垂直唯一)、单一持续 run_cycle 主链取代 PLAN/ORDER、5 门控拆分、
#         命令路由+短确认码硬授权(冻结审批快照+幂等)、信号接收降级、投影预算真实算法(fail-closed)、
#         止盈资格/低成本退出/保护腿回收、BTC-PERPETUAL 对冲生命周期、交互控制台+操作提示 →
#   2.1.0 对冲场所可选：新增 Binance USDC 永续 maker-0 备选（操作者显式配置选择 HEDGE_VENUE，默认 Deribit）
STRATEGY_VERSION = "2.1.0"

# ===== 实例标识（命令幂等键 robot_id；多机器人部署时各自唯一，避免跨实例命令串扰）=====
ROBOT_ID = "spm-exec-1"

# ===== 信号与方向（手填，§4.1 / §5.2 / §6.2）=====
# 取值依据：案例/前置模型信号流.txt（2026-05-29 全天，信号层 v0.5.4 导出）
#   - 前置论证持续「偏空 / 支持偏弱 / 置信 62~71」→ 卖 call、WEAK 放行
#   - 个别轮次「无交易-阻断」对应 NO_TRADE_BLOCKED：那种时刻应把 SIGNAL_STATE 改回阻断值，不进场
SETTLEMENT_CURRENCY = "BTC"                  # 数据源为 BTCUSDT
SIGNAL_STATE        = "TRADE_SUPPORT_WEAK"   # 信号流主态=支持偏弱；仅 STRONG/WEAK 放行进场
DIRECTION_BIAS      = "SHORT_CALL"           # 偏空论证 → 卖出上方 call

# ===== 计划轮 / 下单轮（两轮分离，运行后不经界面命令调整计划或仓位）=====
#   计划轮 ROUND_MODE="PLAN"：枚举所有符合范围的同期垂直备选，按 胜率/盈亏比/信号 筛选排序，
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
# 仅同期垂直信用价差：保护腿与卖方短腿同到期、更价外（v2 删除日历价差运行路径）。

# ===== 信号强度 → 偏好 delta（参与排序，不替模型判方向）=====
SIGNAL_CONFIDENCE = 62            # 0~100 前置模型置信(手填)；弱/低→偏低 delta(高胜率)，强/高→偏高 delta

# ===== 方案排序综合分权重（整合 Phase1：删 KPF，剩余三项等比归一 ÷0.80）=====
PLAN_WEIGHTS = {"win_rate": 0.375, "rr": 0.375, "signal": 0.25}

# ===== 标的参考价（留 None 走实时 index；真实市场以实时价 + delta 选档）=====
UNDERLYING_REF_PRICE = None

# ===== 周期（§5.1 / §6.1）=====
SHORT_DTE_HOURS = (24, 72)        # 卖方短腿 DTE 区间（小时）；保护腿同到期（垂直）
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

# ===== 执行授权门控（v2：拆分单一 ALLOW_TRADING，避免「禁新开仓」误伤风险收口）=====
# 默认全安全（空跑）；FMZ 运行时可单独改各门。逐动作语义见 gates.py。
ALLOW_ENTRY_TRADING   = False     # 开立新垂直价差（新增风险）。False=进场空跑(只展示将执行方案)
ALLOW_EXIT_TRADING    = False     # 买回卖方短腿 / 卖出保护腿（期权降风险退出）
ALLOW_HEDGE_TRADING   = False     # BTC-PERPETUAL 对冲开 / 加 / 减仓
KILL_NEW_RISK         = False     # 急停：停新风险并撤开仓单；不阻断退出/对冲减仓/对账/孤儿清理
EMERGENCY_REDUCE_ONLY = False     # 紧急只减：禁止任何开/加仓，对冲强制 reduce_only
# 兼容保留（DEPRECATED，将于 E8 移除）：旧单门，仅供未迁移引用解析，不再作为进场判定依据
ALLOW_TRADING = False
KILL_SWITCH   = False

# ===== 运行参数 =====
LOOP_INTERVAL_MS    = 3000        # 主循环间隔
PLAN_REFRESH_SECONDS = 45         # 计划轮重算方案库的最小间隔(秒)：节流 API + 防刷屏

# 放行进场的信号集合
ENTER_SIGNALS = ("TRADE_SUPPORT_STRONG", "TRADE_SUPPORT_WEAK")
# 触发退出/复核的信号集合(§9.2)
EXIT_REVIEW_SIGNALS = ("NO_TRADE_AMBIGUOUS", "NO_TRADE_BLOCKED")

# ===== 信号→执行接收链（补充意见 P0-1；默认 OFFLINE_MANUAL 用静态 SIGNAL_STATE 降级）=====
SIGNAL_SOURCE = "OFFLINE_MANUAL"                       # OFFLINE_MANUAL / FILE / G
SIGNAL_FILE_PATH = "demo/logs/signal_evidence.json"   # FILE 源：同托管共享 JSON（信号侧原子 rename 落盘）
SIGNAL_G_KEY = "nrd_signal_evidence_pkg"               # G 源：_G 键
SIGNAL_SCHEMA_VERSION_PREFIX = "nrd.integration.signal."

# ===== 组合投影预算限额（P0-6；fail-closed。阈值为占位，未校准）=====
PORTFOLIO_LIMITS = {
    "max_open_positions": 1,
    "max_short_gamma": 0.05,
    "max_short_vega": 0.50,
    "max_margin": 0.50,                # 结算币(BTC)计占用保证金上限
    "max_spread_loss_per_trade": 0.02,
}

# ===== 风险退出授权（P1：独立于普通止盈预算的风险退出最大支出；默认 0=不允许更高成本退出）=====
RISK_EXIT_MAX_SPEND = 0.0

# ===== 低成本退出活动（§7.3；每轮一次有限动作，价格上限由剩余预算反推）=====
EXIT_QUOTE_REFRESH_MS = 3000
EXIT_ORDER_REST_MS = 4000
EXIT_REPRICE_COOLDOWN_MS = 6000
EXIT_MAX_ACTIVE_ORDERS = 1
EXIT_MAX_PRICE_STEPS_PER_LOOP = 1
EXIT_RESERVE_RATIO = 0.15        # 退出预留占 max_total_exit_spend 的比例（保守参考 + 费用预留）

# ===== BTC-PERPETUAL 对冲（§10；固定工具，目标随剩余卖方敞口；压尾部非全 delta-neutral）=====
HEDGE_REDUCTION_RATIO = 0.5            # 目标覆盖剩余短腿 delta 的比例
HEDGE_CONTRACT_SIZE_FALLBACK = 10.0   # BTC-PERPETUAL 合约面值(USD)，instrument metadata 不可用时回退
HEDGE_MIN_TRADE_FALLBACK = 10.0       # 最小下单(USD/合约)回退

# ----- 对冲场所（可选；默认 Deribit。Binance 为**操作者显式选择**，非运行时自动切换）-----
# 理由：Deribit 深度足够、与期权同所便于统一对账；但 Binance USDC 永续 maker 0 费，
# 对冲腿非高频、可等 maker 成交 → 省成本。跨所对账/恢复取舍见 v3.1 文档。
HEDGE_VENUE = "DERIBIT"                # "DERIBIT" | "BINANCE"
HEDGE_BINANCE_INSTRUMENT = "BTCUSDC"   # 币安 USDC 本位永续（线性、maker 0 费）
HEDGE_BINANCE_MAKER_ONLY = True        # 币安对冲腿强制 maker(post-only)：0 费、低频可等成交
HEDGE_BINANCE_MIN_TRADE = 0.001        # 币安 BTCUSDC 最小下单(BTC, 线性)
HEDGE_BINANCE_EXCHANGE_INDEX = 1       # FMZ exchanges[] 下标(exchanges[0]=Deribit, [1]=Binance)


def validate_config():
    """启动期配置自检，返回错误列表（空=通过）。"""
    errs = []
    if SETTLEMENT_CURRENCY not in ("BTC", "ETH"):
        errs.append("SETTLEMENT_CURRENCY 必须为 BTC 或 ETH")
    if DIRECTION_BIAS not in ("SHORT_CALL", "SHORT_PUT"):
        errs.append("DIRECTION_BIAS 必须为 SHORT_CALL 或 SHORT_PUT")
    if not (SHORT_DTE_HOURS[0] < SHORT_DTE_HOURS[1]):
        errs.append("SHORT_DTE_HOURS 区间非法")
    if ORDER_AMOUNT <= 0:
        errs.append("ORDER_AMOUNT 必须为正")
    if ROUND_MODE not in ("PLAN", "ORDER"):
        errs.append("ROUND_MODE 必须为 PLAN 或 ORDER")
    if not (0 < SHORT_DELTA_RANGE[0] < SHORT_DELTA_RANGE[1] < 1):
        errs.append("SHORT_DELTA_RANGE 应满足 0<min<max<1")
    if not (PROTECTION_WIDTH_RANGE[0] <= PROTECTION_WIDTH_RANGE[1]):
        errs.append("PROTECTION_WIDTH_RANGE 区间非法")
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
    for _n, _v in (("ALLOW_ENTRY_TRADING", ALLOW_ENTRY_TRADING),
                   ("ALLOW_EXIT_TRADING", ALLOW_EXIT_TRADING),
                   ("ALLOW_HEDGE_TRADING", ALLOW_HEDGE_TRADING),
                   ("KILL_NEW_RISK", KILL_NEW_RISK),
                   ("EMERGENCY_REDUCE_ONLY", EMERGENCY_REDUCE_ONLY)):
        if not isinstance(_v, bool):
            errs.append(_n + " 必须为布尔值")
    if SIGNAL_SOURCE not in ("OFFLINE_MANUAL", "FILE", "G"):
        errs.append("SIGNAL_SOURCE 必须为 OFFLINE_MANUAL / FILE / G")
    if HEDGE_VENUE not in ("DERIBIT", "BINANCE"):
        errs.append("HEDGE_VENUE 必须为 DERIBIT 或 BINANCE")
    return errs

# ===================== module: gates =====================
# -*- coding: utf-8 -*-
"""执行授权门控（gate_*）：把单一 ALLOW_TRADING 拆分为分动作门控，
避免「禁新开仓」误伤风险收口（退出 / 对冲减仓 / 孤儿清理）。纯函数，便于单测。

动作（action）：
  ENTRY         开立新垂直价差（**新增风险**）
  EXIT          买回卖方短腿 / 卖出保护腿（期权，**降风险**）
  HEDGE_OPEN    建立 / 增加 BTC-PERPETUAL 对冲（开 / 加仓；reduce_only 无法建仓，故非 reduce_only）
  HEDGE_REDUCE  对冲减仓 / 平仓（**强制 reduce_only**）

门控旗标（默认全安全）：
  allow_entry / allow_exit / allow_hedge   分动作总开关
  kill_new_risk         急停：停新风险并撤开仓单，但**不阻断**退出 / 对冲减仓 / 对账 / 孤儿清理
  emergency_reduce_only 紧急只减：**禁止任何开 / 加仓**，对冲强制 reduce_only

设计依据（补充意见 P0-3）：单一主门会在「禁新开仓」时连带关闭风险退出与对冲归零，
故必须拆分；急停只停新风险，恢复需重新对账并要求新的计划硬批准。
"""

ACTION_ENTRY = "ENTRY"
ACTION_EXIT = "EXIT"
ACTION_HEDGE_OPEN = "HEDGE_OPEN"
ACTION_HEDGE_REDUCE = "HEDGE_REDUCE"

ALL_ACTIONS = (ACTION_ENTRY, ACTION_EXIT, ACTION_HEDGE_OPEN, ACTION_HEDGE_REDUCE)


def _d(action, allowed, reduce_only, reason):
    return {"action": action, "allowed": bool(allowed),
            "reduce_only": bool(reduce_only), "reason": reason}


def gate_decision(action, allow_entry, allow_exit, allow_hedge,
                  kill_new_risk, emergency_reduce_only):
    """对单个动作给出门控裁决。

    返回 {"action", "allowed": bool, "reduce_only": bool, "reason": str}。
    `reduce_only` 仅对 HEDGE_* 有意义（HEDGE_REDUCE 恒 True）。
    """
    allow_entry = bool(allow_entry)
    allow_exit = bool(allow_exit)
    allow_hedge = bool(allow_hedge)
    kill = bool(kill_new_risk)
    emer = bool(emergency_reduce_only)

    if action == ACTION_ENTRY:
        if emer:
            return _d(action, False, False, "ENTRY_BLOCKED_EMERGENCY_REDUCE_ONLY")
        if kill:
            return _d(action, False, False, "ENTRY_BLOCKED_KILL_NEW_RISK")
        if not allow_entry:
            return _d(action, False, False, "ENTRY_GATE_OFF")
        return _d(action, True, False, "ENTRY_ALLOWED")

    if action == ACTION_EXIT:
        # 期权退出为降风险动作：kill / emergency 均不阻断，只由 allow_exit 控制
        if not allow_exit:
            return _d(action, False, False, "EXIT_GATE_OFF")
        return _d(action, True, False, "EXIT_ALLOWED")

    if action == ACTION_HEDGE_OPEN:
        # 紧急只减禁止开 / 加仓；但 kill_new_risk 不阻断对冲开（对冲压缩尾部 = 降险）
        if emer:
            return _d(action, False, False, "HEDGE_OPEN_BLOCKED_EMERGENCY_REDUCE_ONLY")
        if not allow_hedge:
            return _d(action, False, False, "HEDGE_GATE_OFF")
        return _d(action, True, False, "HEDGE_OPEN_ALLOWED")

    if action == ACTION_HEDGE_REDUCE:
        # 对冲减仓恒强制 reduce_only；kill / emergency 下仍允许（风险收口）
        if not allow_hedge:
            return _d(action, False, True, "HEDGE_GATE_OFF")
        return _d(action, True, True, "HEDGE_REDUCE_ALLOWED")

    return _d(action, False, False, "UNKNOWN_ACTION")


def gate_summary(allow_entry, allow_exit, allow_hedge,
                 kill_new_risk, emergency_reduce_only):
    """返回各动作裁决 dict，供面板「交互控制台」一眼看清当前可执行动作。"""
    return {a: gate_decision(a, allow_entry, allow_exit, allow_hedge,
                             kill_new_risk, emergency_reduce_only)
            for a in ALL_ACTIONS}

# ===================== module: cmd_router =====================
# -*- coding: utf-8 -*-
"""交互命令路由 + 命令账本 + 幂等（cmd_*）。

把 FMZ `GetCommand()` 返回的 "名:参数" 解析、归一、去重并落审计账本。
解决补充意见 P0-2：
  - 幂等键 = robot_id + session_id + refresh_seq + command_type + nonce（**非**原始命令串哈希）；
  - 「消费型」命令（执行/授权）一次性消费：同键已在历史账本则忽略
    （防跨轮/重启重复下单、防延迟旧命令命中新方案）；
  - 「切换型」命令（拒绝/撤销/急停/恢复）不去重（幂等动作可重复应用），但仍全部入账本审计。

注：FMZ `GetCommand()` 在回测系统不生效，须真实机器人空跑验收。
按钮名↔命令类型见 COMMAND_ALIASES（中文按钮名与英文类型双向）。
"""

# 中文按钮名 / 英文类型 → 规范类型
COMMAND_ALIASES = {
    "执行": "EXECUTE", "EXECUTE": "EXECUTE",
    "拒绝": "REJECT", "REJECT": "REJECT",
    "授权止盈": "EXIT_AUTHORIZE", "EXIT_AUTHORIZE": "EXIT_AUTHORIZE",
    "撤销授权": "EXIT_REVOKE", "EXIT_REVOKE": "EXIT_REVOKE",
    "风险退出授权": "RISK_EXIT_AUTHORIZE", "RISK_EXIT_AUTHORIZE": "RISK_EXIT_AUTHORIZE",
    "急停": "KILL", "KILL": "KILL",
    "恢复": "RESUME", "RESUME": "RESUME",
}

# 一次性消费型（触发下单 / 授权等不可重复后果）→ 严格幂等
CONSUME_TYPES = frozenset({"EXECUTE", "EXIT_AUTHORIZE", "RISK_EXIT_AUTHORIZE"})

_CMD_LEDGER_KEY = "spm_cmd_ledger_v1"
_CMD_LEDGER_MAX = 200


def parse_command(raw):
    """'名:参数' 或 '名' → {"raw","name","type","arg"}；空串 / None 返回 None。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if ":" in s:
        name, arg = s.split(":", 1)
    else:
        name, arg = s, ""
    name = name.strip()
    return {"raw": s, "name": name,
            "type": COMMAND_ALIASES.get(name, "UNKNOWN"), "arg": arg.strip()}


def is_consume(command):
    return bool(command) and command.get("type") in CONSUME_TYPES


def idempotency_key(robot_id, session_id, refresh_seq, command_type, nonce):
    """结构化幂等键：robot_id|session_id|refresh_seq|command_type|nonce。"""
    return "|".join(str(x) for x in
                    (robot_id, session_id, refresh_seq, command_type, nonce))


def _nonce_for(command):
    # 消费型用 arg（确认码/授权码）作 nonce → 一次性消费、码变即新命令
    return command.get("arg") or command.get("type")


# ---------- 命令账本（_G 持久化，跨重启可查）----------

def cmd_ledger_load():
    return list(_G(_CMD_LEDGER_KEY) or [])


def cmd_ledger_save(records):
    trimmed = records[-_CMD_LEDGER_MAX:]
    _G(_CMD_LEDGER_KEY, trimmed)
    return trimmed


def cmd_ledger_has_key(key):
    if not key:
        return False
    return any(r.get("key") == key for r in cmd_ledger_load())


def cmd_ledger_record(command, key, status, outcome, now_ts):
    recs = cmd_ledger_load()
    recs.append({"key": key, "type": (command or {}).get("type"),
                 "name": (command or {}).get("name"), "arg": (command or {}).get("arg"),
                 "status": status, "outcome": outcome, "ts": now_ts})
    return cmd_ledger_save(recs)


# ---------- 路由 ----------

def route_command(raw, ctx, now_ts):
    """解析 + 幂等判定。ctx={robot_id, session_id, refresh_seq}。
    返回 {"status", "command", "key"}；status ∈ EMPTY / UNKNOWN / DUPLICATE / ACCEPTED。
    ACCEPTED：调用方应处理该命令并在处理后调用 cmd_ledger_record 落账（消费型据此一次性消费）。"""
    cmd = parse_command(raw)
    if cmd is None:
        return {"status": "EMPTY", "command": None, "key": None}
    if cmd["type"] == "UNKNOWN":
        return {"status": "UNKNOWN", "command": cmd, "key": None}
    key = None
    if is_consume(cmd):
        key = idempotency_key(ctx.get("robot_id"), ctx.get("session_id"),
                              ctx.get("refresh_seq"), cmd["type"], _nonce_for(cmd))
        if cmd_ledger_has_key(key):
            return {"status": "DUPLICATE", "command": cmd, "key": key}
    return {"status": "ACCEPTED", "command": cmd, "key": key}

# ===================== module: signal_receiver =====================
# -*- coding: utf-8 -*-
"""信号→执行接收链（sig_*）：执行侧从同托管共享源读取信号层导出的 SignalEvidencePackage，
校验 schema / 版本 / TTL / reject_state / data_quality，给出「是否允许新开仓」的裁决。

补充意见 P0-1：当前执行层仅用静态手填 SIGNAL_STATE，缺 receiver / 校验 / 去重。本模块补执行侧：
  - 总线不可用 / 包过期 / 校验失败 → block_new_opens=True（**禁新开仓**），
    但裁决**只**用于进场门，**不**影响已有持仓的对账 / 退出 / 对冲 / 恢复。
  - package_id 血缘记账（_G），供「本轮持仓由哪个信号包触发」审计。
  - OFFLINE_MANUAL：降级为静态 SIGNAL_STATE / DIRECTION_BIAS（面板须红标），不依赖总线。

依赖（follow-up，先做可行性 spike）：信号侧调 `signal_bridge.export_signal_evidence_package`
落盘 + 原子 rename 传输 + 同托管 loopback 验证。包结构见 `demo/signal_build/signal_bridge.py`。
"""
import json


EXPECTED_SCHEMA = "SignalEvidencePackage"
EXPECTED_VERSION_PREFIX = "nrd.integration.signal."

_REJECT_STATES = frozenset({"REJECT", "REJECTED", "BLOCK", "BLOCKED"})
_BAD_QUALITY_STATES = frozenset({"BAD", "MISSING", "STALE", "DEGRADED", "INSUFFICIENT"})

_SIG_LINEAGE_KEY = "spm_signal_lineage_v1"
_SIG_LINEAGE_MAX = 50


def _verdict(availability, tradeable, reasons, package_id=None,
             side_hint=None, expiry_hours=None):
    return {
        "schema_name": "SignalReceiveVerdict",
        # OK / MISSING / STALE / REJECTED / BAD_QUALITY / SCHEMA_MISMATCH / NO_SIDE / OFFLINE_MANUAL
        "availability": availability,
        "tradeable": (None if tradeable is None else bool(tradeable)),
        "block_new_opens": (False if tradeable is None else (not tradeable)),
        "package_id": package_id,
        "side_hint": side_hint,
        "expiry_hours": expiry_hours,
        "reasons": list(reasons or []),
    }


def _is_rejected(reject_state):
    if not isinstance(reject_state, dict) or not reject_state:
        return False
    if reject_state.get("rejected") is True or reject_state.get("blocked") is True:
        return True
    return str(reject_state.get("state") or "").upper() in _REJECT_STATES


def _data_quality_bad(dq):
    if not isinstance(dq, dict) or not dq:
        return False        # 无显式问题 → 视为可用（不过度阻断）
    if dq.get("ok") is False or dq.get("degraded") is True:
        return True
    return str(dq.get("state") or "").upper() in _BAD_QUALITY_STATES


def validate_signal_package(package, now_ts, version_prefix=EXPECTED_VERSION_PREFIX):
    """纯函数：对一个 SignalEvidencePackage 给出接收裁决。"""
    if not isinstance(package, dict):
        return _verdict("MISSING", False, ["SIGNAL_PACKAGE_MISSING"])
    if package.get("schema_name") != EXPECTED_SCHEMA:
        return _verdict("SCHEMA_MISMATCH", False, ["SCHEMA_NAME_MISMATCH"],
                        package.get("package_id"))
    if not str(package.get("schema_version") or "").startswith(version_prefix):
        return _verdict("SCHEMA_MISMATCH", False, ["SCHEMA_VERSION_MISMATCH"],
                        package.get("package_id"))
    pkg_id = package.get("package_id")
    if not pkg_id:
        return _verdict("MISSING", False, ["PACKAGE_ID_MISSING"])
    exp = package.get("expires_ts")
    if not isinstance(exp, (int, float)) or now_ts >= exp:
        return _verdict("STALE", False, ["SIGNAL_PACKAGE_EXPIRED"], pkg_id)
    if _is_rejected(package.get("reject_state")):
        return _verdict("REJECTED", False, ["SIGNAL_REJECTED"], pkg_id)
    if _data_quality_bad(package.get("data_quality")):
        return _verdict("BAD_QUALITY", False, ["SIGNAL_DATA_QUALITY_BAD"], pkg_id)
    rec = package.get("strategy_recommendation") or {}
    side_hint = rec.get("side_hint")
    expiry_hours = rec.get("expiry_hours")
    if not side_hint or str(side_hint).lower() in ("none", "neutral"):
        return _verdict("NO_SIDE", False, ["SIGNAL_NO_EXECUTABLE_SIDE"],
                        pkg_id, side_hint, expiry_hours)
    return _verdict("OK", True, [], pkg_id, side_hint, expiry_hours)


# ---------- 传输源加载 ----------

def load_package_from_file(path):
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def load_package_from_g(key):
    if not key:
        return None
    return _G(key)


def receive_signal(now_ts, source, file_path=None, g_key=None,
                   version_prefix=EXPECTED_VERSION_PREFIX):
    """按配置源接收并裁决。source ∈ OFFLINE_MANUAL / FILE / G。
    OFFLINE_MANUAL → tradeable=None（由调用方据静态 SIGNAL_STATE 决定，面板须红标）。
    总线不可用（读不到包）→ availability=MISSING, block_new_opens=True。"""
    src = str(source or "OFFLINE_MANUAL").upper()
    if src == "OFFLINE_MANUAL":
        return _verdict("OFFLINE_MANUAL", None, ["SIGNAL_OFFLINE_MANUAL_OVERRIDE"])
    if src == "FILE":
        pkg = load_package_from_file(file_path)
    elif src == "G":
        pkg = load_package_from_g(g_key)
    else:
        return _verdict("MISSING", False, ["SIGNAL_SOURCE_UNKNOWN:" + src])
    if pkg is None:
        return _verdict("MISSING", False, ["SIGNAL_BUS_UNAVAILABLE"])
    return validate_signal_package(pkg, now_ts, version_prefix)


# ---------- package_id 血缘记账（_G）----------

def signal_lineage_load():
    return list(_G(_SIG_LINEAGE_KEY) or [])


def signal_lineage_record(package_id, now_ts, note=""):
    recs = signal_lineage_load()
    recs.append({"package_id": package_id, "ts": now_ts, "note": note})
    trimmed = recs[-_SIG_LINEAGE_MAX:]
    _G(_SIG_LINEAGE_KEY, trimmed)
    return trimmed


def signal_lineage_last():
    recs = signal_lineage_load()
    return recs[-1] if recs else None

# ===================== module: recommend =====================
# -*- coding: utf-8 -*-
"""垂直推荐库编码 + 冻结审批快照 + 短确认码（rec_*）。纯函数，便于单测。

补充意见 P0-2 的核心修复：
  - `quality_code` 由**冻结的质量口径**（signal 包 / VRP / S:PM 释放分桶 / 预算决定）哈希得到，
    **不含实时行情逐 tick 值**，且 S:PM 释放按 0.1 分桶——子桶波动不改码，用户输入期间确认码稳定；
  - `confirm_code` 标识冻结审批快照(session+strategy+quality+plan_hash) 的短码（Base32 前 4，库内冲突自动延长）；
  - 质量发生**材料级**变化（跨桶 / 换信号包 / 结构变）→ quality_code 变 → plan_hash 变 → confirm_code 变 →
    旧码在新库 `resolve_confirm_code` 失败（自动过期），而非依赖实时哈希全等。

`refresh_seq` **不**进入 quality_code（避免每轮刷新改码）；它单独存于审批快照，供 cmd_router 幂等键使用。
"""
import base64
import hashlib

QUALIFIED = "QUALIFIED"
RELIEF_BUCKET = 10          # S:PM 释放分桶粒度：int(ratio*10)，0.1 一桶（漂移容差）


def _h(*parts):
    s = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _b32(hexstr, length):
    raw = base64.b32encode(bytes.fromhex(hexstr)).decode("ascii").rstrip("=")
    return raw[:length]


def _relief_bucket(ratio):
    if not isinstance(ratio, (int, float)):
        return "NA"
    return int(ratio * RELIEF_BUCKET)


def side_of(short_instrument):
    s = str(short_instrument or "")
    if s.endswith("-C"):
        return "CALL"
    if s.endswith("-P"):
        return "PUT"
    return "UNK"


def strategy_code(side, expiry_label, short_strike, long_strike):
    """稳定结构身份：VCS|<side>|<expiry>|<short_strike>|<long_strike>。"""
    return "VCS|%s|%s|%s|%s" % (side, expiry_label, short_strike, long_strike)


def quality_code(signal_package_id, relief_ratio, vrp_state, budget_decision):
    """冻结质量短码（前 8）。仅由材料级质量口径决定，S:PM 释放分桶 → 漂移容差。"""
    return _h(signal_package_id, _relief_bucket(relief_ratio),
              vrp_state, budget_decision)[:8]


def plan_hash(strategy_code_str, quality_code_str, side,
              short_instrument, long_instrument, amount):
    return _h(strategy_code_str, quality_code_str, side,
              short_instrument, long_instrument, amount)[:16]


def confirm_code(session_id, strategy_code_str, quality_code_str, plan_hash_str, length=4):
    """短确认码：标识冻结审批快照，稳定不随行情逐轮翻动。Base32 前 length 位。"""
    return _b32(_h(session_id, strategy_code_str, quality_code_str, plan_hash_str), length)


def build_approval_snapshot(candidate, session_id, signal_package_id, refresh_seq, now_ts):
    """把一个 plans.plan_assemble 菜单项冻结为可批准的审批快照。"""
    short_inst = candidate.get("short_instrument") or ""
    long_inst = candidate.get("protection_instrument") or ""
    side = side_of(short_inst)
    vrp_state = candidate.get("vrp_state") or candidate.get("vrp_gate")
    budget_decision = candidate.get("budget_decision")
    sc = strategy_code(side, candidate.get("short_expiry_label"),
                       candidate.get("short_strike"), candidate.get("protection_strike"))
    qc = quality_code(signal_package_id, candidate.get("margin_relief_ratio"),
                      vrp_state, budget_decision)
    ph = plan_hash(sc, qc, side, short_inst, long_inst, candidate.get("amount"))
    cc = confirm_code(session_id, sc, qc, ph)
    return {
        "schema_name": "VerticalApprovalSnapshot",
        "session_id": session_id, "signal_package_id": signal_package_id,
        "refresh_seq": refresh_seq, "plan_id": candidate.get("id"),
        "side": side, "strategy_code": sc, "quality_code": qc,
        "plan_hash": ph, "confirm_code": cc,
        "recommendation_state": QUALIFIED if candidate.get("qualified", True) else "REJECTED",
        "short_instrument": short_inst, "long_instrument": long_inst,
        "short_strike": candidate.get("short_strike"),
        "long_strike": candidate.get("protection_strike"),
        "amount": candidate.get("amount"),
        "entry_net_credit_after_costs": candidate.get("net_credit_effective"),
        "max_loss": candidate.get("max_loss"),
        "margin_relief_ratio": candidate.get("margin_relief_ratio"),
        "frozen_ts": now_ts,
        "summary": "%s Δ%s 宽%s" % (side, candidate.get("short_delta"), candidate.get("width")),
    }


def ensure_unique_confirm_codes(snaps, session_id, max_len=8):
    """库内若有确认码冲突，对全部项统一延长位数直至唯一（封顶 max_len）。"""
    length = 4
    while length <= max_len:
        seen = {}
        for s in snaps:
            cc = confirm_code(session_id, s["strategy_code"], s["quality_code"],
                              s["plan_hash"], length)
            seen.setdefault(cc, []).append(s)
        if all(len(v) == 1 for v in seen.values()):
            break
        length += 1
    length = min(length, max_len)
    for s in snaps:
        s["confirm_code"] = confirm_code(session_id, s["strategy_code"],
                                         s["quality_code"], s["plan_hash"], length)
    return snaps


def build_recommendation_library(menu, session_id, signal_package_id, refresh_seq, now_ts):
    """从垂直菜单构建推荐库（每项冻结为审批快照，确认码库内唯一）。"""
    snaps = [build_approval_snapshot(c, session_id, signal_package_id, refresh_seq, now_ts)
             for c in (menu or [])]
    ensure_unique_confirm_codes(snaps, session_id)
    return {
        "schema_name": "VerticalRecommendationLibrary",
        "session_id": session_id, "signal_package_id": signal_package_id,
        "refresh_seq": refresh_seq, "generated_ts": now_ts,
        "recommendations": snaps,
    }


def resolve_confirm_code(library, code):
    """在**当前**库中按确认码定位审批快照；找不到（含质量漂移致码变）→ None（旧码自动过期）。"""
    code = str(code or "").strip().upper()
    if not code:
        return None
    for s in (library or {}).get("recommendations", []):
        if str(s.get("confirm_code", "")).upper() == code:
            if s.get("recommendation_state") == QUALIFIED:
                return s
    return None


def precommit_recheck(locked_snapshot, current_library, live_checks):
    """预提交复核（补充意见 P0-2：不要求实时质量哈希全等，按漂移容差）。
    通过条件：①锁定快照的 strategy_code 仍在当前库且仍 QUALIFIED；②plan_hash 一致
    （quality_code 已分桶 → 子桶漂移不改 plan_hash，跨桶/换包/结构变才改）；③live_checks 全部为真。"""
    reasons = []
    match = next((s for s in (current_library or {}).get("recommendations", [])
                  if s.get("strategy_code") == locked_snapshot.get("strategy_code")
                  and s.get("recommendation_state") == QUALIFIED), None)
    if not match:
        reasons.append("STRATEGY_NO_LONGER_QUALIFIED_IN_LIBRARY")
    elif match.get("plan_hash") != locked_snapshot.get("plan_hash"):
        reasons.append("PLAN_HASH_DRIFTED_BEYOND_TOLERANCE")
    for k, ok in (live_checks or {}).items():
        if not ok:
            reasons.append("LIVE_CHECK_FAILED:" + str(k))
    return {"passed": not reasons, "reasons": reasons}


# ---------- 预提交 13 项硬门评估器（设计稿 §8.1；fail-closed：缺数据即视为不通过）----------

PRECOMMIT_CHECKS = (
    "signal_fresh", "same_signal_package", "locked_plan_hash_match",
    "locked_quality_code_match", "vertical_only", "vrp_rechecked",
    "spm_rechecked", "quotes_rechecked", "entry_net_credit_after_costs_positive",
    "projected_budget_passed", "ledger_reconciled", "no_unknown_orders", "spread_ok",
)


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def evaluate_precommit_checks(locked, current_library, live):
    """对锁定方案做 13 项预提交复核。`live` 为调用方预取的实时复核数据：
      signal_fresh(bool), sig_package_id, same_expiry(bool), vrp_pass(bool|None),
      spm_relief(float), min_relief(float), quotes_fresh(bool), net_credit_after_costs(float),
      projected_budget_decision('ALLOW'|...), ledger_reconciled(bool),
      no_unknown_orders(bool), spread_ok(bool)
    返回 {checks:{name:bool}, passed:bool, failed:[names]}；任一缺数据 → 该项 False（fail-closed）。"""
    locked = locked or {}
    live = live or {}
    match = next((s for s in (current_library or {}).get("recommendations", [])
                  if s.get("strategy_code") == locked.get("strategy_code")
                  and s.get("recommendation_state") == QUALIFIED), None)
    c = {
        "signal_fresh": bool(live.get("signal_fresh")),
        "same_signal_package": (live.get("sig_package_id") is not None
                                and locked.get("signal_package_id") == live.get("sig_package_id")),
        "locked_plan_hash_match": bool(match) and match.get("plan_hash") == locked.get("plan_hash"),
        "locked_quality_code_match": bool(match) and match.get("quality_code") == locked.get("quality_code"),
        "vertical_only": locked.get("side") in ("CALL", "PUT") and bool(live.get("same_expiry")),
        "vrp_rechecked": live.get("vrp_pass") is True,
        "spm_rechecked": (_is_num(live.get("spm_relief")) and _is_num(live.get("min_relief"))
                          and live["spm_relief"] >= live["min_relief"]),
        "quotes_rechecked": bool(live.get("quotes_fresh")),
        "entry_net_credit_after_costs_positive": (_is_num(live.get("net_credit_after_costs"))
                                                  and live["net_credit_after_costs"] > 0),
        "projected_budget_passed": live.get("projected_budget_decision") == "ALLOW",
        "ledger_reconciled": bool(live.get("ledger_reconciled")),
        "no_unknown_orders": bool(live.get("no_unknown_orders")),
        "spread_ok": bool(live.get("spread_ok")),
    }
    failed = [k for k in PRECOMMIT_CHECKS if not c.get(k)]
    return {"checks": c, "passed": not failed, "failed": failed}

# ===================== module: position =====================
# -*- coding: utf-8 -*-
"""持仓生命周期：入场快照冻结 + 止盈预算锚（pos_*）。纯函数，便于单测。

设计稿 §2.2 / §8.3：入场成交后冻结 `entry_profit_ceiling_net` 为 80% 阈值的审计基准，
**入场后禁止重新计算或覆盖**。止盈预算由该冻结值反推：
    target_profit_amount = ceiling × take_profit_target_ratio (默认 0.80)
    max_total_exit_spend = ceiling − target_profit_amount      (= ceiling × 0.20)
保护腿回收价值默认按 0（不进入 80% 预算分母），见 E6。
"""

import math

DEFAULT_TAKE_PROFIT_RATIO = 0.80

# 退出活动状态
EXIT_IDLE = "IDLE"
EXIT_WAIT_TRIGGER = "WAIT_TRIGGER"
EXIT_WORKING_SHORT = "WORKING_SHORT"
EXIT_PAUSED_BUDGET = "PAUSED_BY_BUDGET"
EXIT_PAUSED_DATA = "PAUSED_BY_DATA"
EXIT_WORKING_LONG = "WORKING_LONG"
EXIT_LONG_RESIDUAL = "LONG_RESIDUAL_ONLY"
EXIT_COMPLETE = "COMPLETE"


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def entry_profit_ceiling_net(short_credit, long_debit, entry_fees):
    """入场利润上限（结算币）= 卖方实收 − 保护腿实付 − 入场手续费。任一缺失 → None。"""
    if short_credit is None or long_debit is None or entry_fees is None:
        return None
    return short_credit - long_debit - entry_fees


def build_vertical_entry_snapshot(locked, short_fill, long_fill, entry_fees,
                                  now_ts, take_profit_ratio=DEFAULT_TAKE_PROFIT_RATIO):
    """成交后冻结入场快照。short_fill/long_fill: {filled, avg_price}。
    `entry_profit_ceiling_net` 一经冻结即为审计基准，禁止后续覆盖（见 freeze_entry_ceiling）。"""
    locked = locked or {}
    sc = (short_fill or {}).get("avg_price")
    sa = (short_fill or {}).get("filled")
    lc = (long_fill or {}).get("avg_price")
    la = (long_fill or {}).get("filled")
    short_credit = (sc * sa) if (sc is not None and sa is not None) else None
    long_debit = (lc * la) if (lc is not None and la is not None) else None
    ceiling = entry_profit_ceiling_net(short_credit, long_debit, entry_fees)
    target_profit = (ceiling * take_profit_ratio) if ceiling is not None else None
    max_exit_spend = ((ceiling - target_profit)
                      if (ceiling is not None and target_profit is not None) else None)
    return {
        "schema_name": "VerticalEntrySnapshot",
        "position_id": "pos-%s" % now_ts,
        "session_id": locked.get("session_id"),
        "signal_package_id": locked.get("signal_package_id"),
        "strategy_code": locked.get("strategy_code"),
        "quality_code": locked.get("quality_code"),
        "plan_hash": locked.get("plan_hash"),
        "side": locked.get("side"),
        "short_instrument": locked.get("short_instrument"),
        "long_instrument": locked.get("long_instrument"),
        "short_fill_amount": sa, "short_fill_price": sc,
        "long_fill_amount": la, "long_fill_price": lc,
        "entry_fees": entry_fees,
        "entry_profit_ceiling_net": ceiling,            # 不可覆盖（审计基准）
        "take_profit_target_ratio": take_profit_ratio,
        "target_profit_amount": target_profit,
        "max_total_exit_spend": max_exit_spend,
        "realized_exit_spend": 0.0,
        "remaining_short_qty": sa,
        "frozen_ts": now_ts,
        "immutable": True,
    }


def freeze_entry_ceiling(existing_snapshot, recomputed_ceiling=None):
    """守卫：入场后永远返回已冻结的 entry_profit_ceiling_net，忽略任何重算值。
    返回 (frozen_value, tamper_detected)；recomputed 与冻结值不一致仅供审计标记，不改值。"""
    if not existing_snapshot:
        return None, False
    frozen = existing_snapshot.get("entry_profit_ceiling_net")
    tamper = (recomputed_ceiling is not None
              and frozen is not None
              and abs(float(recomputed_ceiling) - float(frozen)) > 1e-12)
    return frozen, tamper


# ---------- E6：止盈资格（资格与成交解耦，§2.3）----------

def reference_profit_capture_ratio(entry_ceiling, conservative_short_buyback_ref,
                                   estimated_short_exit_fee, exit_reserve):
    """止盈资格参考捕获率。保护腿价值**不进分母**（默认按 0）：
    reference_exit_spend = 保守短腿买回参考 + 短腿退出费 + 退出预留
    ratio = (entry_ceiling - reference_exit_spend) / entry_ceiling
    任一输入缺失或 ceiling<=0 → None（不触发自动止盈，标记数据缺口，仅监控）。"""
    if not _is_num(entry_ceiling) or entry_ceiling <= 0:
        return None
    parts = (conservative_short_buyback_ref, estimated_short_exit_fee, exit_reserve)
    if any(not _is_num(p) for p in parts):
        return None
    ref_spend = sum(parts)
    return (entry_ceiling - ref_spend) / entry_ceiling


def take_profit_qualified(reference_ratio, target_ratio=DEFAULT_TAKE_PROFIT_RATIO):
    """资格触发：参考捕获率 >= 目标(默认 0.80)。ratio None → 未达资格(数据缺口)。"""
    return _is_num(reference_ratio) and reference_ratio >= target_ratio


# ---------- E6：低成本退出硬预算 + 价格上限（§7.2 / §7.3）----------

def short_buyback_budget(max_total_exit_spend, realized_exit_spend, fee_reserve):
    """剩余短腿买回预算 = max_total_exit_spend − 已用 − 费用预留（不小于 0）。"""
    if not _is_num(max_total_exit_spend):
        return None
    return max(0.0, max_total_exit_spend - (realized_exit_spend or 0.0) - (fee_reserve or 0.0))


def short_buyback_price_cap(remaining_budget, fee_reserve, remaining_short_qty, tick):
    """每轮价格上限由剩余预算反推并向下取整到 tick：
    cap = floor_to_tick((remaining_budget − fee_reserve) / remaining_short_qty)。
    数量<=0 或预算不足 → 0（不下单）。"""
    if not (_is_num(remaining_budget) and _is_num(remaining_short_qty)) or remaining_short_qty <= 0:
        return 0.0
    avail = remaining_budget - (fee_reserve or 0.0)
    if avail <= 0:
        return 0.0
    raw = avail / remaining_short_qty
    if tick and tick > 0:
        return math.floor(raw / tick) * tick
    return raw


def within_exit_budget(order_price, order_amount, estimated_fee, remaining_budget):
    """订单是否在剩余预算内：price*amount + fee <= remaining_budget。"""
    if not all(_is_num(x) for x in (order_price, order_amount, estimated_fee, remaining_budget)):
        return False
    return order_price * order_amount + estimated_fee <= remaining_budget + 1e-12


def exit_campaign_decision(authorized, qualified, remaining_short_qty,
                           remaining_budget, quote_ok, price_cap):
    """退出活动下一状态/是否可下单（纯函数，不做 I/O；§7）。
    优先：短腿归零→转保护腿回收；未授权→IDLE；未达资格→WAIT_TRIGGER；
    无盘口→PAUSED_BY_DATA；预算/上限不足→PAUSED_BY_BUDGET；否则→WORKING_SHORT(可买回)。"""
    if remaining_short_qty is not None and remaining_short_qty <= 0:
        return {"state": EXIT_WORKING_LONG, "can_order": False, "reason": "SHORT_FLAT"}
    if not authorized:
        return {"state": EXIT_IDLE, "can_order": False, "reason": "UNAUTHORIZED"}
    if not qualified:
        return {"state": EXIT_WAIT_TRIGGER, "can_order": False, "reason": "NOT_QUALIFIED"}
    if not quote_ok:
        return {"state": EXIT_PAUSED_DATA, "can_order": False, "reason": "NO_RELIABLE_QUOTE"}
    if not price_cap or price_cap <= 0 or not _is_num(remaining_budget) or remaining_budget <= 0:
        return {"state": EXIT_PAUSED_BUDGET, "can_order": False, "reason": "BUDGET_EXHAUSTED"}
    return {"state": EXIT_WORKING_SHORT, "can_order": True, "reason": "BUYBACK_WITHIN_BUDGET"}


def protection_recovery_decision(short_flat, prot_qty, prot_bid):
    """短腿归零后保护腿回收决策（纯）：先平短腿；无 bid → LONG_RESIDUAL_ONLY 保持等结算。"""
    if not short_flat:
        return {"state": "HOLD_PROTECTION_UNTIL_SHORT_FLAT", "can_sell": False}
    if not prot_qty or prot_qty <= 0:
        return {"state": EXIT_COMPLETE, "can_sell": False}
    if not prot_bid or prot_bid <= 0:
        return {"state": EXIT_LONG_RESIDUAL, "can_sell": False}
    return {"state": EXIT_WORKING_LONG, "can_sell": True}

# ===================== module: authorization =====================
# -*- coding: utf-8 -*-
"""软授权（持仓退出 / 风险退出）合同 + 短授权码（auth_*）。纯函数，便于单测。

设计稿 §6 + 补充意见 P1：软授权与 `position_id` 绑定，是与主循环**并行的非阻塞授权标志**，
不阻塞主循环；默认持续到 用户撤销 / 持仓结束 / 账本身份变化。
风险退出授权独立于普通止盈授权，并补全完整语义：
  max_exit_spend / allowed_order_types / valid_until / revoke / consume。
"""
import base64
import hashlib

ST_UNAUTHORIZED = "UNAUTHORIZED"
ST_AUTHORIZED = "AUTHORIZED"
ST_REVOKED = "REVOKED"
ST_CONSUMED = "CONSUMED"

POLICY_TAKE_PROFIT = "BOUNDED_EXIT_V1"
POLICY_RISK_EXIT = "RISK_EXIT_V1"


def _h(*parts):
    s = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def auth_code(position_id, policy_code, length=4):
    """持仓授权短码：标识 (position_id, policy)。Base32 前 length 位。"""
    raw = base64.b32encode(bytes.fromhex(_h(position_id, policy_code))).decode("ascii").rstrip("=")
    return raw[:length]


def build_authorization(position_id, policy_code, now_ts, operator_note="",
                        max_exit_spend=None, allowed_order_types=None, valid_until=None):
    """构建一份持仓退出授权（初始 AUTHORIZED）。risk-exit 传 max_exit_spend 等完整语义。"""
    return {
        "schema_name": "PositionExitAuthorization",
        "position_id": position_id,
        "policy_code": policy_code,
        "authorization_state": ST_AUTHORIZED,
        "authorized_ts": now_ts,
        "revoked_ts": None,
        "consumed_ts": None,
        "authorization_hash": _h(position_id, policy_code, now_ts)[:16],
        "auth_code": auth_code(position_id, policy_code),
        "operator_note": operator_note,
        # P1：风险退出授权完整语义（普通止盈授权这些为默认 / None）
        "max_exit_spend": max_exit_spend,
        "allowed_order_types": list(allowed_order_types or ["post_only"]),
        "valid_until": valid_until,
    }


def is_authorized(auth, position_id, now_ts=None):
    """授权对当前 position_id 是否有效（AUTHORIZED + 绑定一致 + 未过期）。非阻塞只读。"""
    if not auth or auth.get("authorization_state") != ST_AUTHORIZED:
        return False
    if auth.get("position_id") != position_id:
        return False                      # 持仓身份变化 → 授权失效
    vu = auth.get("valid_until")
    if vu is not None and now_ts is not None and now_ts >= vu:
        return False
    return True


def revoke(auth, now_ts):
    if not auth:
        return auth
    a = dict(auth)
    a["authorization_state"] = ST_REVOKED
    a["revoked_ts"] = now_ts
    return a


def consume(auth, now_ts):
    """退出活动完成后标记 CONSUMED（一次性消费）。"""
    if not auth:
        return auth
    a = dict(auth)
    a["authorization_state"] = ST_CONSUMED
    a["consumed_ts"] = now_ts
    return a


def authorize_from_code(code, position_id, policy_code, now_ts, **kw):
    """校验用户输入的授权码是否匹配当前 position+policy；匹配 → 构建授权，否则 None。"""
    if not code or not position_id:
        return None
    if str(code).strip().upper() != auth_code(position_id, policy_code).upper():
        return None
    return build_authorization(position_id, policy_code, now_ts, **kw)

# ===================== module: session_core =====================
# -*- coding: utf-8 -*-
"""ExecutionSession and ApprovalIntent contract harness for demo v0.2."""

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class PrecommitChecks:
    signal_fresh: bool
    vrp_rechecked: bool
    spm_rechecked: bool
    quotes_rechecked: bool
    ledger_rechecked: bool
    spread_ok: bool
    maker_only: bool

    def all_passed(self) -> bool:
        return all(asdict(self).values())


def _stable_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass
class ExecutionSession:
    session_id: str
    signal_package_id: str
    created_ts: int
    state: str
    locked_plan: Optional[Dict[str, Any]] = None
    approval_intent: Optional[Dict[str, Any]] = None

    @classmethod
    def open(cls, session_id: str, signal_package_id: str, now_ts: int) -> "ExecutionSession":
        return cls(
            session_id=session_id,
            signal_package_id=signal_package_id,
            created_ts=now_ts,
            state="SIGNAL_OBSERVED",
        )

    def lock_plan(self, plan: Dict[str, Any], now_ts: int, ttl_sec: int) -> Dict[str, Any]:
        plan_copy = dict(plan)
        plan_hash = _stable_hash(plan_copy)
        self.locked_plan = {
            "schema_name": "ExecutionPlanPackage",
            "session_id": self.session_id,
            "signal_package_id": self.signal_package_id,
            "plan": plan_copy,
            "plan_hash": plan_hash,
            "plan_created_ts": now_ts,
            "ttl_sec": ttl_sec,
            "expires_ts": now_ts + ttl_sec,
        }
        self.state = "PLAN_LOCKED"
        return self.locked_plan

    def approve_locked_plan(
        self,
        now_ts: int,
        checks: PrecommitChecks,
        allow_real_order: bool,
        operator_note: str = "",
    ) -> Dict[str, Any]:
        if not self.locked_plan:
            raise ValueError("Cannot approve without locked plan")
        approval_id = _stable_hash({
            "session_id": self.session_id,
            "signal_package_id": self.signal_package_id,
            "plan_hash": self.locked_plan["plan_hash"],
            "approval_created_ts": now_ts,
        })[:16]
        self.approval_intent = {
            "schema_name": "ApprovalIntentPackage",
            "schema_version": "nrd.integration.approval_intent.v0.1",
            "approval_id": approval_id,
            "session_id": self.session_id,
            "signal_package_id": self.signal_package_id,
            "plan_hash": self.locked_plan["plan_hash"],
            "plan_created_ts": self.locked_plan["plan_created_ts"],
            "approval_created_ts": now_ts,
            "ttl_sec": self.locked_plan["ttl_sec"],
            "approval_state": "ARMED",
            "allow_real_order": bool(allow_real_order),
            "operator_note": operator_note,
            "precommit_checks": asdict(checks),
        }
        self.state = "ARMED_PREVIEW"
        return self.approval_intent

    def can_commit_order(self, now_ts: int) -> bool:
        if self.state != "ARMED_PREVIEW" or not self.locked_plan or not self.approval_intent:
            return False
        if now_ts >= self.locked_plan["expires_ts"]:
            self.approval_intent["approval_state"] = "EXPIRED"
            return False
        checks = PrecommitChecks(**self.approval_intent["precommit_checks"])
        return bool(self.approval_intent["allow_real_order"] and checks.all_passed())

    def package(self) -> Dict[str, Any]:
        return {
            "schema_name": "ExecutionSessionPackage",
            "schema_version": "nrd.integration.execution_session.v0.1",
            "session_id": self.session_id,
            "signal_package_id": self.signal_package_id,
            "state": self.state,
            "locked_plan": self.locked_plan or {},
            "approval_intent": self.approval_intent or {},
        }

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
                    post_only=True, reject_post_only=True, label=None, reduce_only=False):
    """限价单。side: 'buy'/'sell'。reduce_only=True 用于对冲减仓/平仓（不可建仓）。
    返回 result（含 order 字段）或 None。"""
    path = "/private/buy" if side == "buy" else "/private/sell"
    params = {
        "instrument_name": instrument_name,
        "amount": amount,
        "type": "limit",
        "price": price,
        "post_only": post_only,
        "reject_post_only": reject_post_only,
    }
    if reduce_only:
        params["reduce_only"] = True
    if label:
        params["label"] = label
    return _call("GET", path, params, "place_order:" + side)


def dbt_get_order_state(order_id):
    return _call("GET", "/private/get_order_state", {"order_id": order_id}, "order_state")


def dbt_cancel(order_id):
    return _call("GET", "/private/cancel", {"order_id": order_id}, "cancel")

# ===================== module: binance_io =====================
# -*- coding: utf-8 -*-
"""币安 USDC 永续对冲适配（bnc_*）：经 FMZ exchanges[idx] 下对冲腿。

线性合约(单位 BTC)、USDC maker 0 费 → maker(post-only)；reduce_only 用平仓方向。
仅对冲腿用，不参与期权 / 信号。FMZ 多所：exchanges[0]=Deribit(期权)，exchanges[idx]=Binance。

注：真实下单调用形态依 FMZ 币安期货接口（SetContractType/SetDirection/Buy/Sell），**须真实机器人确认**；
默认 `ALLOW_HEDGE_TRADING=False` + `HEDGE_VENUE=DERIBIT`，本路径不触发。跨所对账/恢复取舍见 v3.1 文档。
"""


def _ex(idx):
    try:
        return exchanges[HEDGE_BINANCE_EXCHANGE_INDEX if idx is None else idx]
    except Exception:
        return None


def bnc_get_position_btc(symbol, idx=None):
    """读 BTCUSDC 永续净持仓(BTC；正=多 / 负=空)。读不到 → 0.0。"""
    ex = _ex(idx)
    if ex is None:
        return 0.0
    try:
        ex.SetContractType(symbol)
        net = 0.0
        for p in (ex.GetPosition() or []):
            amt = p.get("Amount") or 0.0
            long_side = p.get("Type") in (0, "buy", "long", "Long")
            net += amt if long_side else -amt
        return net
    except Exception as e:
        Log("[binance] GetPosition 异常:", str(e))
        return 0.0


def bnc_place_hedge(symbol, side, amount, reduce_only, maker_only, allow_live=True, idx=None):
    """下币安对冲腿。maker_only→post-only 限价(取同侧盘口被动价)；reduce_only→平仓方向。
    allow_live=False → 仅意图(dry)，不下单。"""
    if not side or not amount or amount <= 0:
        return {"filled": 0.0, "dry": (not allow_live), "venue": "BINANCE", "reason": "NO_OP"}
    if not allow_live:
        return {"filled": 0.0, "dry": True, "venue": "BINANCE", "symbol": symbol, "side": side,
                "amount": amount, "reduce_only": reduce_only, "maker_only": maker_only,
                "reason": "BINANCE_HEDGE_DRYRUN"}
    ex = _ex(idx)
    if ex is None:
        return {"filled": 0.0, "dry": False, "venue": "BINANCE", "reason": "BINANCE_EXCHANGE_UNAVAILABLE"}
    try:
        ex.SetContractType(symbol)
        t = ex.GetTicker() or {}
        price = t.get("Buy") if side == "buy" else t.get("Sell")   # 被动 maker 价：join 同侧盘口
        direction = ("closesell" if side == "buy" else "closebuy") if reduce_only else side
        ex.SetDirection(direction)
        resp = ex.Buy(price, amount) if side == "buy" else ex.Sell(price, amount)
        return {"filled": 0.0, "dry": False, "venue": "BINANCE", "order": resp,
                "reduce_only": reduce_only, "maker_only": maker_only,
                "reason": "BINANCE_HEDGE_SUBMITTED"}
    except Exception as e:
        Log("[binance] 下单异常:", str(e))
        return {"filled": 0.0, "dry": False, "venue": "BINANCE", "reason": "BINANCE_ORDER_ERROR"}

# ===================== module: leg_selection =====================
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
        "structure_type": "VERTICAL_CREDIT_SPREAD",
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

计划轮枚举所有符合范围的同期垂直信用价差备选，每个 = 一组(短腿 + 同到期更价外保护腿)，
按 胜率 / 盈亏比 / 信号契合 计算综合分排序，输出方案库（含方案号 + 推荐标签）。

口径（启发式，用于排序比较；非精确定价）：
- 胜率 ≈ 1 - |短腿 delta|（短腿到期 OTM 近似概率）。
- 同期垂直：保护腿与短腿同到期、到期一起了结。
    净 credit = (短腿 mark - 保护腿 mark) × 数量；最大亏损 = 腿宽折BTC - 净credit（**硬封顶**）。
- 盈亏比 = 净credit / 最大亏损（仅二者均为正时有意义）。
纯函数，便于单测。
"""

MODE_VERTICAL = 2  # 唯一结构标识（保留数值 2，兼容菜单/展示读取 p["mode"]）


def plan_mode_cn(mode=MODE_VERTICAL):
    return "同期垂直"


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


def plan_effective_credit(short_prem, prot_prem):
    """垂直：同到期了结，净credit = 短腿权利金 - 保护腿权利金，无复用/残值。
    返回 (net_credit, net_credit, protection_premium, 0.0)（保留四元组形态兼容既有读取）。
    short_prem/prot_prem 为持仓口径权利金(已×数量)。"""
    if short_prem is None or prot_prem is None:
        return None, None, None, None
    single = short_prem - prot_prem
    return single, single, prot_prem, 0.0


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
    call: 价格高于此开始亏；put: 价格低于此开始亏。"""
    if short_strike is None or short_mark is None or prot_mark is None or not spot:
        return None
    net_pc_usd = (short_mark - prot_mark) * spot      # 每张净credit折 USD(价格点)
    return short_strike + net_pc_usd if want_call else short_strike - net_pc_usd


def plan_credit_on_margin(net_credit_effective, im_with_protection):
    """净credit / 占用保证金（每周期保证金回报率）——本策略价值核心指标。"""
    if net_credit_effective is None or not im_with_protection or im_with_protection <= 0:
        return None
    return net_credit_effective / im_with_protection


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


def plan_assemble(amount, spot, min_ratio,
                  preferred_delta, want_call,
                  short, sq, prot, pq, spm, pm_ok, account_model,
                  short_dte_hours=None, prot_dte_hours=None):
    """组装一个同期垂直候选方案 dict（不含综合分/方案号，由 plan_rank 补充）。"""
    sq, pq = sq or {}, pq or {}
    short_mark, prot_mark = sq.get("mark"), pq.get("mark")
    short_delta = (short or {}).get("_delta", sq.get("delta"))
    width = abs(prot.get("strike", 0) - short.get("strike", 0)) if (short and prot) else None

    premium_income = (short_mark * amount) if short_mark is not None else None
    protection_premium = (prot_mark * amount) if prot_mark is not None else None
    covered = 1
    eff_credit, single_credit, amort, residual = plan_effective_credit(
        premium_income, protection_premium)
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
        "id": plan_id(MODE_VERTICAL, short_inst, prot_inst),
        "short_expiry_label": plan_expiry_label(short_inst),
        "protection_expiry_label": plan_expiry_label(prot_inst),
        "mode": MODE_VERTICAL, "mode_cn": plan_mode_cn(),
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
                          + weights["signal"] * (c.get("signal_fit") or 0.0))
    ranked = sorted(pool, key=lambda c: c["composite"], reverse=True)
    menu = ranked[:menu_size]
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
    arb = g("action_arb")
    if arb:
        line = str(arb.get("executable_action"))
        if arb.get("blocked_reason"):
            line += " (优先 %s 受阻:%s)" % (arb.get("preferred_action"), arb.get("blocked_reason"))
        rows.append(["风险动作", line])
    if g("exit_auth_state"):
        rows.append(["软授权", g("exit_auth_state")])
    if g("take_profit_ratio") is not None:
        rows.append(["止盈资格", g("take_profit_ratio")])
    if g("exit_campaign_state"):
        rows.append(["退出活动", g("exit_campaign_state")])
    if g("hedge_state"):
        rows.append(["对冲", g("hedge_state")])
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

# ===================== module: hedge =====================
# -*- coding: utf-8 -*-
"""BTC-PERPETUAL 对冲生命周期（hedge_*）。纯函数，便于单测。

设计稿 §10 + 补充意见 P0-5 + 用户 v2.1 补充（对冲场所可选）：
  - 对冲工具默认 DERIBIT BTC-PERPETUAL（反向、与期权同所、便于统一账本/恢复）；
    **可选** BINANCE BTCUSDC 永续（线性、USDC maker 0 费）——对冲腿非高频，maker 等成交可省成本。
    场所为**操作者显式配置选择**（HEDGE_VENUE），**非运行时自动切换**（避免补充意见所警示的 UNBOUND）。
  - 目标数量随**剩余卖方期权敞口**变化；短腿归零 / 结构 CLOSED|SETTLED → 目标立即归零（不等保护腿）；
  - **HEDGE_OPEN/INCREASE 非 reduce_only**（reduce_only 无法建仓）；HEDGE_REDUCE/UNWIND 强制 reduce_only；
  - 期权卖方风险消失但 perp 仍有持仓 → 孤儿对冲紧急态（持续 reduce_only 清理，会话不得 CLOSED）。
  - 换算：DERIBIT 反向(USD 合约)=delta_btc·spot/contract_size；BINANCE 线性(BTC)=delta_btc。
"""
HEDGE_INSTRUMENT = "BTC-PERPETUAL"
HEDGE_VENUE = "DERIBIT"

VENUE_DERIBIT = "DERIBIT"
VENUE_BINANCE = "BINANCE"

_EPS = 1e-9


def hedge_venue_config(venue, binance_instrument="BTCUSDC", binance_maker_only=True):
    """返回场所配置 {venue, instrument, linear, maker_only}。
    DERIBIT：BTC-PERPETUAL、反向、对冲开仓可 taker(prompt)；
    BINANCE：BTCUSDC 永续、线性(BTC)、USDC maker 0 费 → 默认强制 maker(post-only)。"""
    if str(venue or VENUE_DERIBIT).upper() == VENUE_BINANCE:
        return {"venue": VENUE_BINANCE, "instrument": binance_instrument,
                "linear": True, "maker_only": bool(binance_maker_only)}
    return {"venue": VENUE_DERIBIT, "instrument": HEDGE_INSTRUMENT,
            "linear": False, "maker_only": False}


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def hedge_side(side):
    """SHORT_CALL 风险上升(delta 正) → BUY BTC-PERP；SHORT_PUT → SELL。"""
    s = str(side or "").upper()
    if s in ("CALL", "SHORT_CALL"):
        return "buy"
    if s in ("PUT", "SHORT_PUT"):
        return "sell"
    return None


def hedge_target_contracts(remaining_short_qty, structure_delta, reduction_ratio,
                           spot, contract_size, min_trade_amount,
                           option_structure_state="OPEN", linear=False):
    """对冲目标数量。硬不变量：短腿归零 或 结构 CLOSED/SETTLED → 0（不等保护腿出售）。
    linear=False(Deribit 反向)：USD 合约 = |rem·delta·ratio|·spot / contract_size；
    linear=True (Binance 线性)：BTC 数量 = |rem·delta·ratio|。结果取整到 min_trade。"""
    if str(option_structure_state).upper() in ("CLOSED", "SETTLED"):
        return 0.0
    if not remaining_short_qty or remaining_short_qty <= _EPS:
        return 0.0
    if not _is_num(structure_delta):
        return 0.0
    delta_btc = abs(remaining_short_qty * structure_delta * (reduction_ratio or 1.0))
    if linear:
        raw = delta_btc
    else:
        if not (_is_num(spot) and _is_num(contract_size)) or contract_size <= 0:
            return 0.0
        raw = delta_btc * spot / contract_size
    if min_trade_amount and min_trade_amount > 0:
        return round(raw / min_trade_amount) * min_trade_amount
    return raw


def hedge_order_action(current_qty, target_qty, min_trade_amount=0.0):
    """据当前 vs 目标决定动作 + reduce_only（P0-5）。
    目标>当前 → HEDGE_OPEN/INCREASE(非 reduce_only)；目标<当前 → HEDGE_REDUCE/UNWIND(reduce_only)。"""
    cur = abs(current_qty or 0.0)
    tgt = abs(target_qty or 0.0)
    step = abs(tgt - cur)
    thr = max(_EPS, (min_trade_amount or 0.0) * 0.5)
    if step <= thr:
        return {"action": "HEDGE_HOLD", "reduce_only": False, "delta_contracts": 0.0}
    if tgt > cur:
        return {"action": ("HEDGE_INCREASE" if cur > _EPS else "HEDGE_OPEN"),
                "reduce_only": False, "delta_contracts": step}
    return {"action": ("HEDGE_UNWIND" if tgt <= _EPS else "HEDGE_REDUCE"),
            "reduce_only": True, "delta_contracts": step}


def hedge_orphan(option_short_qty, perp_qty):
    """期权卖方风险已消失(short<=0) 但 perp 仍有持仓 → 孤儿对冲（须 reduce_only 清理）。"""
    return (not option_short_qty or option_short_qty <= _EPS) and abs(perp_qty or 0.0) > _EPS


def settlement_guard(remaining_short_qty, near_expiry, settled, perp_qty):
    """到期/交割保护：已交割 → 目标强制 0（perp 未归零即孤儿）；临近到期 → 不新增、随剩余短腿归零。"""
    if settled:
        return {"target": 0.0, "orphan": abs(perp_qty or 0.0) > _EPS, "reason": "SETTLED_FORCE_ZERO"}
    if near_expiry:
        flat = (not remaining_short_qty or remaining_short_qty <= _EPS)
        return {"target": (0.0 if flat else None), "orphan": False,
                "reason": "NEAR_EXPIRY_NO_NEW_HEDGE"}
    return {"target": None, "orphan": False, "reason": "NORMAL"}

# ===================== module: execution =====================
# -*- coding: utf-8 -*-
"""
执行层（exec_*，§10）：保护腿优先、maker-only、只追一步、禁 taker。

价格计算为纯函数（可单测）；下单/轮询/撤单走 dbt_*。
进场门控经 gates.gate_decision(ENTRY)：ALLOW_ENTRY_TRADING=False（或 KILL_NEW_RISK /
EMERGENCY_REDUCE_ONLY）时，进场真实下单短路为「记录意图」（空跑核对）。
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
    # 进场门控（ENTRY）：exec_open_structure 为唯一调用方；退出/对冲执行器后续各自传专属门控
    live = gate_decision(ACTION_ENTRY, ALLOW_ENTRY_TRADING, False, False,
                         KILL_NEW_RISK, EMERGENCY_REDUCE_ONLY)["allowed"]

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


# ---------- 低成本退出：买回卖方短腿（§7.3；每轮一次、价格 ≤ 预算上限、post-only）----------

def exec_exit_buyback_step(short_instrument, target_amount, price_cap, allow_live,
                           label="exit_short"):
    """退出活动一轮：以 ≤ price_cap 的被动 post-only 买单买回（平）卖方短腿。
    allow_live=False → 仅返回意图(dry)，不下单。撤未成交单后再查一次以捕捉晚到成交。
    返回 {filled, avg_price, dry, price, reason}。"""
    q = exec_quote(short_instrument)
    if not q or q.get("best_bid") is None or q.get("best_ask") is None or q.get("mark") is None:
        return {"filled": 0.0, "dry": (not allow_live), "reason": "NO_QUOTE"}
    tick = q.get("tick") or 0.0
    maker_safe = (q["best_ask"] - tick) if tick else q["best_bid"]   # 最高仍为 maker 的买价
    price = min(maker_safe, price_cap)
    if price <= 0 or price > price_cap + 1e-12:
        return {"filled": 0.0, "dry": (not allow_live), "price": price, "reason": "ABOVE_BUDGET_CAP"}
    if not allow_live:
        return {"filled": 0.0, "dry": True, "price": price, "reason": "EXIT_DRYRUN"}
    resp = dbt_place_order("buy", short_instrument, target_amount, price,
                           post_only=True, reject_post_only=True, label=label)
    order = _extract_order(resp)
    if order is None:
        return {"filled": 0.0, "dry": False, "price": price, "reason": "POST_ONLY_REJECTED"}
    oid = order.get("order_id")
    Sleep(int(CHASE_WAIT_SECONDS * 1000))
    st = _extract_order(dbt_get_order_state(oid)) or order
    filled = st.get("filled_amount") or 0.0
    avg = st.get("average_price") or price
    if st.get("order_state") not in ("filled",) and (target_amount - filled) > 0:
        dbt_cancel(oid)
        st2 = _extract_order(dbt_get_order_state(oid)) or st        # 撤单后再查，捕捉晚到成交
        if (st2.get("filled_amount") or 0.0) > filled:
            filled = st2.get("filled_amount")
            avg = st2.get("average_price") or avg
    return {"filled": filled, "avg_price": avg, "dry": False, "price": price, "reason": "EXIT_STEP"}


# ---------- BTC-PERPETUAL 对冲下单（§10.4；REDUCE/UNWIND 强制 reduce_only）----------

def exec_hedge_step(venue_cfg, side, amount, reduce_only, allow_live, label="hedge"):
    """对冲一步（场所感知）。OPEN/INCREASE 非 reduce_only；REDUCE/UNWIND 强制 reduce_only。
    venue_cfg: hedge.hedge_venue_config 结果(含 venue/instrument/linear/maker_only)。
    BINANCE → binance_io(maker post-only/USDC 永续)；DERIBIT → BTC-PERPETUAL。allow_live=False → 仅意图(dry)。"""
    venue_cfg = venue_cfg or {}
    venue = venue_cfg.get("venue")
    instrument = venue_cfg.get("instrument")
    maker_only = bool(venue_cfg.get("maker_only"))
    if not side or not amount or amount <= 0:
        return {"filled": 0.0, "dry": (not allow_live), "venue": venue, "reason": "NO_OP"}
    if venue == "BINANCE":
        return bnc_place_hedge(instrument, side, amount, reduce_only, maker_only,
                               allow_live=allow_live, idx=venue_cfg.get("exchange_index"))
    # DERIBIT 反向永续
    if not allow_live:
        return {"filled": 0.0, "dry": True, "venue": venue, "instrument": instrument,
                "side": side, "amount": amount, "reduce_only": reduce_only, "reason": "HEDGE_DRYRUN"}
    q = exec_quote(instrument) or {}
    price = q.get("best_ask") if side == "buy" else q.get("best_bid")
    resp = dbt_place_order(side, instrument, amount, price, post_only=maker_only,
                           reject_post_only=False, label=label, reduce_only=reduce_only)
    order = _extract_order(resp)
    if order is None:
        return {"filled": 0.0, "dry": False, "venue": venue, "reduce_only": reduce_only,
                "reason": "HEDGE_ORDER_FAILED"}
    oid = order.get("order_id")
    st = _extract_order(dbt_get_order_state(oid)) or order
    return {"filled": st.get("filled_amount") or 0.0, "dry": False, "venue": venue,
            "reduce_only": reduce_only, "reason": "HEDGE_STEP"}

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
S_SHORT_FLAT_LONG_RESIDUAL = "SHORT_FLAT_LONG_RESIDUAL"   # 短腿归零、保护腿待回收（不可直跳 CLOSED）
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


# ---------- 启动恢复裁决（§11.3；纯函数，便于单测）----------

def evaluate_startup_recovery(option_positions, perp_position_qty,
                              ledger_short_qty, active_orders=None):
    """据交易所真实持仓 / 账本 / 活动订单建立可解释映射并裁决：
      RECOVERY_BLOCKED：身份不明活动订单，或账本声称卖方短腿但交易所无对应期权持仓（不可解释）；
      ORPHAN_HEDGE_EMERGENCY：存在 BTC-PERPETUAL 对冲持仓但已无期权卖方风险；
      OK：可解释。allow_new_open 仅 OK 时为真（恢复完成前禁开新仓）。"""
    reasons = []
    active_orders = active_orders or []
    unknown = [o for o in active_orders if not o.get("label")]
    if unknown:
        reasons.append("UNKNOWN_ACTIVE_ORDERS:%d" % len(unknown))
    opt_qty = sum(abs(p.get("size") or 0.0) for p in (option_positions or []))
    ledger_short = abs(ledger_short_qty or 0.0)
    if ledger_short > 1e-9 and opt_qty <= 1e-9:
        reasons.append("LEDGER_SHORT_BUT_NO_EXCHANGE_OPTION")
    if reasons:
        return {"state": "RECOVERY_BLOCKED", "reasons": reasons, "allow_new_open": False}
    if abs(perp_position_qty or 0.0) > 1e-9 and ledger_short <= 1e-9:
        return {"state": "ORPHAN_HEDGE_EMERGENCY",
                "reasons": ["PERP_HEDGE_WITHOUT_OPTION_SHORT_RISK"], "allow_new_open": False}
    return {"state": "OK", "reasons": [], "allow_new_open": True}

# ===================== module: hedge_risk =====================
# -*- coding: utf-8 -*-
"""
Post-entry hedge risk evaluator.

The module is deliberately pure: it produces PositionRiskPackage and optional
dry-run HedgeIntentPackage, but it never places orders or mutates the option
ledger. It uses EDB as the aggregate signal input and keeps GGR as a boundary
and persistence modifier, not as a probability predictor.
"""
import math


SCHEMA_NAME = "PositionRiskPackage"
SCHEMA_VERSION = "nrd.integration.position_risk.v0.4"

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
                            entry_gamma_regime="",
                            entry_vrp_window_id="", entry_forward_vol_hurdle=None,
                            entry_candidate_vrp_edge_ccy=None,
                            entry_executable_short_iv=None, entry_vrp_reason_codes=None):
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
        # R4：VRP 入场血缘（与对冲共 IV/vol 基线；对冲只读此血缘、不反向重做 VRP）
        "entry_vrp_window_id": entry_vrp_window_id,
        "entry_forward_vol_hurdle": entry_forward_vol_hurdle,
        "entry_candidate_vrp_edge_ccy": entry_candidate_vrp_edge_ccy,
        "entry_executable_short_iv": entry_executable_short_iv,
        "entry_vrp_reason_codes": entry_vrp_reason_codes or [],
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


def _ggr_adverse(gamma_regime):
    ggr = gamma_regime or {}
    if bool(ggr.get("veto")):
        return True
    regime = str(ggr.get("regime") or "").upper()
    dist = _safe_float(ggr.get("distance_to_flip_pct"))
    if regime == "NEGATIVE_GAMMA_AMPLIFYING":
        return dist is None or abs(dist) <= 1.0
    gate = str(((ggr.get("ggr_gate") or {}).get("regime")) or "").upper()
    return gate == "NEGATIVE_GAMMA_AMPLIFYING"


def persistence_score(direction_bias, edb=None, gamma_regime=None):
    """整合 Phase1：持续性两项制 {EDB_ADVERSE, GGR_ADVERSE}（删 KPF buffer）。
    重标定 0→LOW / 1→MEDIUM / 2→HIGH。EDB 为唯一方向证据入口、GGR 为负 Gamma 例外修正。"""
    confirmations = []
    if _edb_adverse(direction_bias, edb):
        confirmations.append("EDB_ADVERSE")
    if _ggr_adverse(gamma_regime):
        confirmations.append("GGR_ADVERSE")
    count = len(confirmations)
    if count >= 2:
        score = PERSISTENCE_HIGH
    elif count == 1:
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
        "hedge_instrument": "BTC-PERPETUAL",          # v2 固定工具（删 UNBOUND/Binance 场所选择）
        "hedge_venue": "DERIBIT",
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
                           gamma_regime=None,
                           exit_friction=None, recent_history=None,
                           now_ms=None, existing_hedge=False):
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
        direction_bias, edb, gamma_regime)
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

# ===================== module: vrp_gate =====================
# -*- coding: utf-8 -*-
"""VRP 建仓前权利金风险补偿定价门（执行层 canonical 收口版，R3）。

把 VRP 封版 v1.1 的纯门逻辑落为执行层 src 模块；**4 个重复原语收口到执行层 canonical
单一真值源**（删 VRP 本地副本）：
  normalise_iv      -> hedge_risk._normalise_iv          （直接用同名，不别名，保证 bundle 内联后名字存在）
  _norm_cdf         -> hedge_risk._norm_cdf
  _option_fee       -> accounting.acct_option_fee_ccy    （费率 0.0003/0.125 即 canonical 常量）
  _spread_half_cost -> accounting.acct_spread_cost       （**保留 VRP 的 None/倒挂→0 安全语义**）
black_scholes_price_usd 是唯一保留的新能力。门判定与 v1.1 等价（tests/test_vrp_gate.py 等价性测试背书）。

边界：只过滤、不判方向、不选期、不进 PLAN_WEIGHTS、不解 ALLOW_TRADING；只跑 EDB 背书侧。
"""
from dataclasses import asdict, dataclass
from math import log, sqrt
from typing import Optional, Tuple


PASS = "PASS"
BLOCK = "BLOCK"
DISTORTED_REVIEW = "DISTORTED_REVIEW"
VRP_FACTOR_VERSION = "1.1.0"


@dataclass(frozen=True)
class ScenarioConfig:
    rv_weights: Tuple[float, float, float] = (0.45, 0.35, 0.20)
    low_percentile_threshold: float = 0.25
    high_percentile_threshold: float = 0.75
    low_percentile_multiplier: float = 1.25
    high_percentile_multiplier: float = 0.92
    cold_start_multiplier: float = 1.10
    min_history_days: int = 30
    term_backwardation_ratio: float = 1.18
    event_backwardation_ratio: float = 1.35
    min_window_vol_edge: float = 0.02
    min_candidate_edge_ccy: float = 0.0
    spread_round_trip_multiplier: float = 2.0
    annualization_days: int = 365


def selected_policy_config():
    """执行层采用的 VRP 策略（strict_cost_cold_guard_v1_1，268k 遍历选出，危险/冷启动通过=0）。
    费率不再由本 config 提供（收口到 accounting canonical 常量 0.0003/0.125）。"""
    return ScenarioConfig(
        low_percentile_multiplier=1.25,
        high_percentile_multiplier=0.92,
        cold_start_multiplier=1.35,
        term_backwardation_ratio=1.18,
        min_candidate_edge_ccy=0.00005,
        min_window_vol_edge=0.02,
        spread_round_trip_multiplier=3.0,
    )


@dataclass(frozen=True)
class WindowInput:
    window_id: str
    expiry: str
    dte_hours: float
    side: str
    front_anchor_iv: float
    atm_front_iv: Optional[float]
    term_reference_iv_5_10d: Optional[float]
    rv_24h: float
    rv_72h: float
    rv_7d: float
    rv_percentile: Optional[float]
    history_days: int


@dataclass(frozen=True)
class CandidateQuote:
    window_id: str
    side: str
    spot: float
    short_strike: float
    protection_strike: float
    dte_hours: float
    amount: float
    short_bid: float
    short_ask: float
    protection_bid: float
    protection_ask: float
    executable_short_iv: float
    executable_protection_iv: Optional[float]
    forward_vol_hurdle: float
    short_instrument: str = ""
    protection_instrument: str = ""
    short_delta: Optional[float] = None


def _weighted_average(values, weights):
    total_weight = 0.0
    total = 0.0
    for value, weight in zip(values, weights):
        if value is None:
            continue
        total += value * weight
        total_weight += weight
    if total_weight <= 0:
        return None
    return total / total_weight


# ---------- canonical 收口原语 ----------
def _option_fee(price_ccy, amount):
    """收口 accounting.acct_option_fee_ccy（单一费率真值源）；保留 VRP max(price,0) 守护。"""
    return acct_option_fee_ccy(max(price_ccy, 0.0), amount)


def _spread_half_cost(bid, ask, amount):
    """收口 accounting.acct_spread_cost 核心算式，但保留 VRP 的 None/倒挂(ask<bid)→0 安全语义
    （canonical 对缺失返回 None、不挡倒挂，裸用会使 full_round_trip_friction 求和遇 None 崩溃）。"""
    if bid is None or ask is None or ask < bid:
        return 0.0
    return acct_spread_cost(bid, ask, amount)


# ---------- hurdle / 窗口门 ----------
def forward_vol_hurdle(rv_24h, rv_72h, rv_7d, rv_percentile, history_days, config):
    rvs = [_normalise_iv(rv_24h), _normalise_iv(rv_72h), _normalise_iv(rv_7d)]
    rv_regime_anchor = _weighted_average(rvs, config.rv_weights)
    if rv_regime_anchor is None:
        return None, {"rv_regime_anchor": None, "percentile_adjustment": None,
                      "cold_start_multiplier": None, "reason_codes": ["RV_REGIME_ANCHOR_MISSING"]}
    reason_codes = []
    percentile_adjustment = 1.0
    if rv_percentile is None:
        reason_codes.append("RV_PERCENTILE_MISSING")
    elif rv_percentile <= config.low_percentile_threshold:
        percentile_adjustment = config.low_percentile_multiplier
        reason_codes.append("RV_LOW_PERCENTILE_HURDLE_UP")
    elif rv_percentile >= config.high_percentile_threshold:
        percentile_adjustment = config.high_percentile_multiplier
        reason_codes.append("RV_HIGH_PERCENTILE_HURDLE_RELAXED")
    cold_start_multiplier = 1.0
    if history_days < config.min_history_days:
        cold_start_multiplier = config.cold_start_multiplier
        reason_codes.append("COLD_START_HURDLE_UP")
    hurdle = rv_regime_anchor * percentile_adjustment * cold_start_multiplier
    return hurdle, {"rv_regime_anchor": rv_regime_anchor,
                    "percentile_adjustment": percentile_adjustment,
                    "cold_start_multiplier": cold_start_multiplier,
                    "forward_vol_hurdle": hurdle, "reason_codes": reason_codes}


def assess_window(window, config):
    """廉价 vol-space 预筛：front 锚 IV vs forward_vol_hurdle + 期限结构/数据质量路由。"""
    reason_codes = []
    front_iv = _normalise_iv(window.front_anchor_iv)
    atm_front_iv = _normalise_iv(window.atm_front_iv)
    term_iv = _normalise_iv(window.term_reference_iv_5_10d)
    hurdle, hurdle_meta = forward_vol_hurdle(
        window.rv_24h, window.rv_72h, window.rv_7d,
        window.rv_percentile, window.history_days, config)
    reason_codes.extend(hurdle_meta.get("reason_codes") or [])
    gate = PASS
    front_to_term_state = "NORMAL"
    if front_iv is None or hurdle is None:
        gate = BLOCK
        reason_codes.append("WINDOW_DATA_MISSING")
    if term_iv and front_iv:
        ratio = front_iv / term_iv
        if ratio >= config.event_backwardation_ratio:
            front_to_term_state = "EVENT_DISTORTED"
            gate = DISTORTED_REVIEW
            reason_codes.append("FRONT_TERM_EVENT_DISTORTED")
        elif ratio >= config.term_backwardation_ratio:
            front_to_term_state = "STRESSED_BACKWARDATION"
            gate = DISTORTED_REVIEW
            reason_codes.append("FRONT_TERM_BACKWARDATION")
        elif ratio >= 0.96:
            front_to_term_state = "FLAT"
    representative_vol_edge = None
    if front_iv is not None and hurdle is not None:
        representative_vol_edge = front_iv - hurdle
        if gate == PASS and representative_vol_edge < config.min_window_vol_edge:
            gate = BLOCK
            reason_codes.append("WINDOW_VRP_EDGE_TOO_THIN")
    return {"window_id": window.window_id, "expiry": window.expiry, "dte_hours": window.dte_hours,
            "side": window.side, "main_anchor_delta": 0.30, "front_anchor_iv": front_iv,
            "atm_front_iv": atm_front_iv, "term_reference_iv_5_10d": term_iv,
            "front_to_term_state": front_to_term_state,
            "rv_regime_anchor": hurdle_meta.get("rv_regime_anchor"),
            "rv_lookback_n_days": window.history_days, "rv_percentile": window.rv_percentile,
            "percentile_adjustment": hurdle_meta.get("percentile_adjustment"),
            "cold_start_multiplier": hurdle_meta.get("cold_start_multiplier"),
            "forward_vol_hurdle": hurdle, "representative_vol_edge": representative_vol_edge,
            "window_vrp_gate": gate, "reason_codes": sorted(set(reason_codes))}


# ---------- BS pricer（唯一保留的新能力）----------
def black_scholes_price_usd(option_type, spot, strike, dte_hours, sigma, annualization_days=365):
    if spot <= 0 or strike <= 0 or dte_hours <= 0 or sigma <= 0:
        return 0.0
    t = max(dte_hours / (24.0 * annualization_days), 1e-9)
    vol_sqrt_t = sigma * sqrt(t)
    if vol_sqrt_t <= 0:
        return 0.0
    d1 = (log(spot / strike) + 0.5 * sigma * sigma * t) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    if option_type == "call":
        return max(0.0, spot * _norm_cdf(d1) - strike * _norm_cdf(d2))
    return max(0.0, strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1))


def _option_type_for_side(side):
    if side == "SHORT_CALL":
        return "call"
    if side == "SHORT_PUT":
        return "put"
    raise ValueError("Unsupported side: %s" % side)


# ---------- 候选门（权威 ccy full-burn）----------
def assess_candidate(candidate, config):
    reason_codes = []
    option_type = _option_type_for_side(candidate.side)
    hurdle = _normalise_iv(candidate.forward_vol_hurdle)
    short_iv = _normalise_iv(candidate.executable_short_iv)
    protection_iv = _normalise_iv(candidate.executable_protection_iv)
    if protection_iv is None:
        protection_iv = hurdle
    executable_net_credit = (candidate.short_bid - candidate.protection_ask) * candidate.amount
    short_hurdle = black_scholes_price_usd(
        option_type, candidate.spot, candidate.short_strike, candidate.dte_hours,
        hurdle or 0.0, config.annualization_days) / candidate.spot
    protection_hurdle = black_scholes_price_usd(
        option_type, candidate.spot, candidate.protection_strike, candidate.dte_hours,
        hurdle or 0.0, config.annualization_days) / candidate.spot
    hurdle_net_credit = (short_hurdle - protection_hurdle) * candidate.amount
    entry_exit_fees = (2.0 * _option_fee(candidate.short_bid, candidate.amount)
                       + 2.0 * _option_fee(candidate.protection_ask, candidate.amount))
    spread_reserve = config.spread_round_trip_multiplier * (
        _spread_half_cost(candidate.short_bid, candidate.short_ask, candidate.amount)
        + _spread_half_cost(candidate.protection_bid, candidate.protection_ask, candidate.amount))
    full_round_trip_friction = entry_exit_fees + spread_reserve
    candidate_edge = executable_net_credit - hurdle_net_credit - full_round_trip_friction
    gate = PASS
    if short_iv is None or hurdle is None:
        gate = BLOCK
        reason_codes.append("CANDIDATE_IV_OR_HURDLE_MISSING")
    if candidate_edge <= config.min_candidate_edge_ccy:
        gate = BLOCK
        reason_codes.append("CANDIDATE_FULL_BURN_EDGE_TOO_THIN")
    return {"window_id": candidate.window_id,
            "instrument_pair": {"short": candidate.short_instrument,
                                "protection": candidate.protection_instrument},
            "side": candidate.side, "short_delta": candidate.short_delta,
            "short_dte_hours": candidate.dte_hours,
            "width": abs(candidate.protection_strike - candidate.short_strike),
            "executable_short_iv": short_iv, "executable_protection_iv": protection_iv,
            "forward_vol_hurdle": hurdle,
            "vertical_net_credit_at_executable_quotes": executable_net_credit,
            "vertical_net_credit_at_forward_vol_hurdle": hurdle_net_credit,
            "full_round_trip_friction": full_round_trip_friction,
            "candidate_vrp_edge_ccy": candidate_edge, "candidate_vrp_gate": gate,
            "vrp_residual_score": max(0.0, candidate_edge),
            "reason_codes": sorted(set(reason_codes)), "raw_candidate": asdict(candidate)}


# ---------- PRICE_GATE 适配：真实菜单方案 + 市场上下文 -> 双门裁决 ----------
def gate_plan(plan, market_context, config=None):
    """对一个真实选腿方案过 VRP 双门（窗口门→候选门）。market_context 提供 IV/RV：
    side / front_anchor_iv / atm_front_iv / term_reference_iv_5_10d / rv_24h/72h/7d /
    rv_percentile / history_days / executable_short_iv / executable_protection_iv。
    返回 VrpGatePackage（只过滤；不判方向/不选期/不进权重）。"""
    config = config or selected_policy_config()
    side = market_context["side"]
    expiry_hours = float(plan.get("short_dte_hours") or market_context.get("dte_hours") or 24.0)
    window_id = "%s-%dh" % (side, int(expiry_hours))
    window = WindowInput(
        window_id=window_id, expiry="%dh" % int(expiry_hours), dte_hours=expiry_hours, side=side,
        front_anchor_iv=market_context["front_anchor_iv"],
        atm_front_iv=market_context.get("atm_front_iv"),
        term_reference_iv_5_10d=market_context.get("term_reference_iv_5_10d"),
        rv_24h=market_context["rv_24h"], rv_72h=market_context["rv_72h"],
        rv_7d=market_context["rv_7d"], rv_percentile=market_context.get("rv_percentile"),
        history_days=int(market_context.get("history_days", 0)))
    wa = assess_window(window, config)
    cand = CandidateQuote(
        window_id=window_id, side=side, spot=plan["spot"],
        short_strike=plan["short_strike"], protection_strike=plan["protection_strike"],
        dte_hours=expiry_hours, amount=plan["amount"],
        short_bid=plan["short_bid"], short_ask=plan["short_ask"],
        protection_bid=plan["protection_bid"], protection_ask=plan["protection_ask"],
        executable_short_iv=market_context["executable_short_iv"],
        executable_protection_iv=market_context.get("executable_protection_iv"),
        forward_vol_hurdle=wa["forward_vol_hurdle"] or 0.0,
        short_instrument=plan.get("short_instrument", ""),
        protection_instrument=plan.get("protection_instrument", ""),
        short_delta=plan.get("short_delta"))
    ca = assess_candidate(cand, config)
    passed = (wa["window_vrp_gate"] == PASS and ca["candidate_vrp_gate"] == PASS)
    return {"schema_name": "VrpGatePackage", "schema_version": "nrd.integration.vrp_gate.v0.5",
            "factor_version": VRP_FACTOR_VERSION, "window": wa, "candidate": ca,
            "pass": bool(passed),
            "reason_codes": sorted(set((wa.get("reason_codes") or [])
                                       + (ca.get("reason_codes") or [])))}


def apply_vrp_gate(menu, market_context, config=None):
    """对真实菜单逐方案过 VRP 双门，返回 (passed[(plan,gate)], blocked[(plan,gate)])。
    PRICE_GATE：BLOCK 的候选不进可锁定方案；VRP 不进 PLAN_WEIGHTS。"""
    passed, blocked = [], []
    for plan in menu:
        gate = gate_plan(plan, market_context, config)
        (passed if gate["pass"] else blocked).append((plan, gate))
    return passed, blocked

# ===================== module: risk_controls =====================
# -*- coding: utf-8 -*-
"""Conservative risk, position management, attribution, and replay contracts."""

from typing import Any, Dict, Iterable, List


REPLAY_BUCKET_FIELDS = ["side", "dte_bucket", "vrp_gate", "budget_decision"]


def _expectation_bucket(net_sum: float) -> str:
    if net_sum > 0:
        return "POSITIVE_NET"
    if net_sum < 0:
        return "NEGATIVE_NET"
    return "FLAT_NET"


def evaluate_portfolio_budget(
    current: Dict[str, float],
    limits: Dict[str, float],
    proposed_size: float,
) -> Dict[str, Any]:
    breaches: List[str] = []
    checks = (
        ("open_positions", "max_open_positions"),
        ("short_gamma", "max_short_gamma"),
        ("short_vega", "max_short_vega"),
        ("margin_used", "max_margin"),
    )
    for current_key, limit_key in checks:
        if current.get(current_key, 0.0) > limits.get(limit_key, float("inf")):
            breaches.append("%s>%s" % (current_key, limit_key))

    blocked = bool(breaches)
    return {
        "schema_name": "PortfolioRiskBudgetPackage",
        "schema_version": "nrd.integration.portfolio_budget.v0.1",
        "status": "PLACEHOLDER",
        "decision": "BLOCK" if blocked else "ALLOW_TEST_SIZE",
        "allowed_size": 0.0 if blocked else min(float(proposed_size), 1.0),
        "breaches": breaches,
        "reason_codes": ["PORTFOLIO_BUDGET_EXCEEDED"] if blocked else ["PORTFOLIO_BUDGET_PLACEHOLDER_CONSERVATIVE"],
    }


# ---------- 投影预算真实算法（P0-6；替代上面 PLACEHOLDER：把拟建仓位计入，fail-closed）----------

def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _budget_result(decision, projected, reasons, fail_closed):
    return {
        "schema_name": "ProjectedBudgetPackage",
        "schema_version": "nrd.integration.projected_budget.v1",
        "decision": decision,
        "projected": projected,
        "fail_closed": bool(fail_closed),
        "reason_codes": reasons,
    }


def evaluate_projected_budget(proposed, current, limits):
    """把**拟建仓位**(proposed)计入当前组合(current)后与限额(limits)比较。
    proposed 任一必填项缺失 → fail closed(BLOCK)，绝不放行不完整输入。
      proposed: {short_gamma, short_vega, structure_margin, max_spread_loss,
                 hedge_margin_reserve, fee_reserve}
      current:  {open_positions, short_gamma, short_vega, margin_used}
      limits:   {max_open_positions, max_short_gamma, max_short_vega, max_margin,
                 max_spread_loss_per_trade}"""
    required = ("short_gamma", "short_vega", "structure_margin",
                "max_spread_loss", "hedge_margin_reserve", "fee_reserve")
    missing = [k for k in required if not _is_num((proposed or {}).get(k))]
    if missing:
        return _budget_result("BLOCK", {},
                              ["BUDGET_INPUT_INCOMPLETE:" + ",".join(missing)], True)
    cur = current or {}
    proj = {
        "open_positions": int(cur.get("open_positions", 0)) + 1,
        "short_gamma": float(cur.get("short_gamma", 0.0)) + abs(float(proposed["short_gamma"])),
        "short_vega": float(cur.get("short_vega", 0.0)) + abs(float(proposed["short_vega"])),
        "margin_used": (float(cur.get("margin_used", 0.0))
                        + float(proposed["structure_margin"])
                        + float(proposed["hedge_margin_reserve"])
                        + float(proposed["fee_reserve"])),
    }
    breaches = []
    for pk, lk in (("open_positions", "max_open_positions"),
                   ("short_gamma", "max_short_gamma"),
                   ("short_vega", "max_short_vega"),
                   ("margin_used", "max_margin")):
        lim = (limits or {}).get(lk)
        if _is_num(lim) and proj[pk] > lim:
            breaches.append("%s>%s" % (pk, lk))
    msl_lim = (limits or {}).get("max_spread_loss_per_trade")
    if _is_num(msl_lim) and float(proposed["max_spread_loss"]) > msl_lim:
        breaches.append("max_spread_loss>limit")
    decision = "BLOCK" if breaches else "ALLOW"
    return _budget_result(decision, proj,
                          breaches if breaches else ["PROJECTED_BUDGET_OK"], False)


# ---------- 统一动作仲裁四输出（P0-4：退出不可执行可回退对冲，避免压住风险收口）----------

def _arb(preferred, executable, blocked_reason, fallback):
    return {"schema_name": "ActionArbitration",
            "preferred_action": preferred, "executable_action": executable,
            "blocked_reason": blocked_reason, "fallback_action": fallback}


def unified_action_arbiter(s):
    """每轮输出唯一 preferred + 实际可执行 executable + blocked_reason + fallback。
    s: recovery_blocked / orphan_hedge / in_flight_order / exit_preferred / hedge_ready /
       take_profit_ready / exit_authorized / exit_executable / exit_pause_reason / hedge_executable。
    优先级：RECOVERY_BLOCKED > ORPHAN_HEDGE_EMERGENCY > MANAGE_IN_FLIGHT >
            EXIT_PREFERRED > HEDGE_READY > TAKE_PROFIT_READY > HOLD。
    P0-4：当退出类为 preferred 但未授权/无数据/预算暂停时，executable 回退到对冲(若可执行)，
    不因退出受阻而禁止必要对冲。"""
    s = s or {}
    if s.get("recovery_blocked"):
        return _arb("RECOVERY_BLOCKED", "RECOVERY_BLOCKED", None, None)
    if s.get("orphan_hedge"):
        return _arb("ORPHAN_HEDGE_EMERGENCY", "ORPHAN_HEDGE_EMERGENCY", None, None)
    if s.get("in_flight_order"):
        return _arb("MANAGE_IN_FLIGHT", "MANAGE_IN_FLIGHT", None, None)

    if s.get("exit_preferred"):
        preferred = "EXIT_PREFERRED"
    elif s.get("hedge_ready"):
        preferred = "HEDGE_READY"
    elif s.get("take_profit_ready"):
        preferred = "TAKE_PROFIT_READY"
    else:
        return _arb("HOLD", "HOLD", None, None)

    if preferred in ("EXIT_PREFERRED", "TAKE_PROFIT_READY"):
        if not s.get("exit_authorized"):
            blocked = "EXIT_NOT_AUTHORIZED"
        elif s.get("exit_pause_reason"):
            blocked = "EXIT_" + str(s["exit_pause_reason"])
        elif not s.get("exit_executable"):
            blocked = "EXIT_NOT_EXECUTABLE"
        else:
            blocked = None
        if not blocked:
            return _arb(preferred, preferred, None, None)
        if s.get("hedge_executable"):
            return _arb(preferred, "HEDGE_READY", blocked, "HEDGE_READY")
        return _arb(preferred, "HOLD", blocked, "HOLD")

    # preferred == HEDGE_READY
    if s.get("hedge_executable"):
        return _arb("HEDGE_READY", "HEDGE_READY", None, None)
    return _arb("HEDGE_READY", "HOLD", "HEDGE_NOT_EXECUTABLE", "HOLD")


def decide_position_manage(
    premium_captured_ratio: float,
    take_profit_threshold: float,
    dte_remaining: int,
    gamma_state: str,
) -> Dict[str, Any]:
    reason_codes: List[str] = []
    decision = "HOLD_REVIEW"
    if premium_captured_ratio >= take_profit_threshold:
        decision = "TAKE_PROFIT_READY"
        reason_codes.append("TAKE_PROFIT_PLACEHOLDER_EARLY")
    elif dte_remaining <= 2 and gamma_state.upper() == "HIGH":
        decision = "GAMMA_DECAY_EXIT"
        reason_codes.append("GAMMA_DECAY_PLACEHOLDER_EARLY")
    elif dte_remaining <= 4:
        decision = "TIME_EXIT_REVIEW"
        reason_codes.append("TIME_EXIT_DRYRUN_REVIEW")
    else:
        reason_codes.append("POSITION_MANAGE_PLACEHOLDER_HOLD")

    return {
        "schema_name": "PositionManageDecision",
        "schema_version": "nrd.integration.position_manage.v0.1",
        "status": "PLACEHOLDER",
        "decision": decision,
        "inputs": {
            "premium_captured_ratio": premium_captured_ratio,
            "take_profit_threshold": take_profit_threshold,
            "dte_remaining": dte_remaining,
            "gamma_state": gamma_state,
        },
        "reason_codes": reason_codes,
    }


def build_attribution(
    session_id: str,
    theta_capture: float,
    directional_pnl: float,
    iv_rv_edge_proxy: float,
    fee_cost: float,
    spread_slippage_cost: float,
    protection_cost_or_recovery: float,
    hedge_pnl: float,
    unexplained_residual: float = 0.0,
) -> Dict[str, Any]:
    net = (
        theta_capture
        + directional_pnl
        + iv_rv_edge_proxy
        - fee_cost
        - spread_slippage_cost
        + protection_cost_or_recovery
        + hedge_pnl
        + unexplained_residual
    )
    return {
        "schema_name": "AttributionPackage",
        "schema_version": "nrd.integration.attribution.v0.1",
        "status": "PLACEHOLDER",
        "session_id": session_id,
        "theta_capture": theta_capture,
        "directional_pnl": directional_pnl,
        "iv_rv_edge_proxy": iv_rv_edge_proxy,
        "fee_cost": fee_cost,
        "spread_slippage_cost": spread_slippage_cost,
        "protection_cost_or_recovery": protection_cost_or_recovery,
        "hedge_pnl": hedge_pnl,
        "unexplained_residual": unexplained_residual,
        "net_pnl_after_costs": net,
    }


def replay_expectation(attributions: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(attributions)
    net_sum = sum(float(row.get("net_pnl_after_costs", 0.0)) for row in rows)
    return {
        "schema_name": "ReplayExpectationPackage",
        "schema_version": "nrd.integration.replay_expectation.v0.1",
        "status": "OFFLINE",
        "sample_count": len(rows),
        "net_pnl_after_costs_sum": net_sum,
        "net_pnl_after_costs_mean": net_sum / len(rows) if rows else 0.0,
        "expectation_bucket": _expectation_bucket(net_sum),
    }


def _bucket_key(row: Dict[str, Any], bucket_fields: List[str]) -> str:
    return "|".join("%s=%s" % (field, row.get(field, "UNKNOWN")) for field in bucket_fields)


def replay_expectation_by_bucket(
    rows: Iterable[Dict[str, Any]],
    bucket_fields: List[str],
) -> Dict[str, Any]:
    materialized = list(rows)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in materialized:
        grouped.setdefault(_bucket_key(row, bucket_fields), []).append(row)

    buckets = []
    for key in sorted(grouped):
        bucket_rows = grouped[key]
        net_sum = sum(float(row.get("net_pnl_after_costs", 0.0)) for row in bucket_rows)
        first_row = bucket_rows[0] if bucket_rows else {}
        buckets.append({
            "bucket_key": key,
            "bucket_values": {field: first_row.get(field, "UNKNOWN") for field in bucket_fields},
            "sample_count": len(bucket_rows),
            "net_pnl_after_costs_sum": net_sum,
            "net_pnl_after_costs_mean": net_sum / len(bucket_rows) if bucket_rows else 0.0,
            "expectation_bucket": _expectation_bucket(net_sum),
        })

    net_sum = sum(float(row.get("net_pnl_after_costs", 0.0)) for row in materialized)
    return {
        "schema_name": "ReplayExpectationBucketReport",
        "schema_version": "nrd.integration.replay_expectation_buckets.v0.5",
        "status": "OFFLINE",
        "sample_count": len(materialized),
        "bucket_fields": bucket_fields,
        "net_pnl_after_costs_sum": net_sum,
        "net_pnl_after_costs_mean": net_sum / len(materialized) if materialized else 0.0,
        "buckets": buckets,
    }


def _dte_bucket(expiry_hours: Any) -> str:
    if expiry_hours is None:
        return "UNKNOWN"
    try:
        return "%sh" % int(float(expiry_hours))
    except (TypeError, ValueError):
        return str(expiry_hours)


def build_replay_context_row(execution_result: Dict[str, Any]) -> Dict[str, Any]:
    session = execution_result.get("session", {})
    locked_plan = execution_result.get("locked_plan", {})
    plan = locked_plan.get("plan", {})
    vrp_gate = execution_result.get("vrp_gate", {})
    candidate = vrp_gate.get("candidate", {})
    portfolio_budget = execution_result.get("portfolio_budget", {})
    attribution = execution_result.get("attribution", {})
    approval_intent = session.get("approval_intent", {})
    vrp_state = "PASS" if vrp_gate.get("pass") else "BLOCK"
    return {
        "schema_name": "ReplayContextRow",
        "schema_version": "nrd.integration.replay_context.v0.8",
        "session_id": session.get("session_id"),
        "signal_package_id": session.get("signal_package_id"),
        "plan_hash": locked_plan.get("plan_hash"),
        "side": plan.get("side", "UNKNOWN"),
        "expiry_hours": plan.get("expiry_hours"),
        "dte_bucket": _dte_bucket(plan.get("expiry_hours")),
        "vrp_gate": vrp_state,
        "window_vrp_gate": vrp_gate.get("window", {}).get("window_vrp_gate"),
        "candidate_vrp_gate": candidate.get("candidate_vrp_gate"),
        "candidate_vrp_edge_ccy": float(candidate.get("candidate_vrp_edge_ccy", 0.0) or 0.0),
        "budget_decision": portfolio_budget.get("decision", "UNKNOWN"),
        "approval_state": approval_intent.get("approval_state", "UNKNOWN"),
        "can_commit_order": bool(execution_result.get("can_commit_order", False)),
        "net_pnl_after_costs": float(attribution.get("net_pnl_after_costs", 0.0) or 0.0),
        "reason_codes": sorted(set(vrp_gate.get("reason_codes") or [])),
    }


def replay_expectation_from_execution_result(execution_result: Dict[str, Any]) -> Dict[str, Any]:
    row = build_replay_context_row(execution_result)
    report = replay_expectation_by_bucket([row], bucket_fields=REPLAY_BUCKET_FIELDS)
    report["source"] = "execution_result"
    report["rows"] = [row]
    return report


def replay_expectation_batch_from_execution_results(
    execution_results: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    rows = [build_replay_context_row(result) for result in execution_results]
    bucket_report = replay_expectation_by_bucket(rows, bucket_fields=REPLAY_BUCKET_FIELDS)
    bucket_report["source"] = "execution_results"
    net_sum = sum(float(row.get("net_pnl_after_costs", 0.0)) for row in rows)
    return {
        "schema_name": "ReplayExpectationBatchReport",
        "schema_version": "nrd.integration.replay_batch.v0.9",
        "source": "execution_results",
        "sample_count": len(rows),
        "net_pnl_after_costs_sum": net_sum,
        "net_pnl_after_costs_mean": net_sum / len(rows) if rows else 0.0,
        "expectation_bucket": _expectation_bucket(net_sum),
        "rows": rows,
        "bucket_report": bucket_report,
    }

# ===================== module: hedge_watch =====================
# -*- coding: utf-8 -*-
"""HEDGE_WATCH 域（R5）：持仓后对冲监控集成缝。

驻执行内：读账本 short 记录的 EntryRiskAnchor + 实时行情 + **SignalEvidencePackage 的
edb/ggr**（整合契约形状），调真实 hedge_risk.evaluate_position_risk，产出 PositionRiskPackage
（仅 HEDGE_READY 时带 DRY_INTENT_ONLY 的 HedgeIntentPackage）。

边界：只在持仓后运行；不入场、不判方向、不自动改期权账本、第一版不真实下单。
对冲只读 EntryRiskAnchor 的 VRP 血缘，不反向重做 VRP。
"""


def watch_position(position_id, direction_bias, short_record, current_market,
                   signal_evidence=None, exit_friction=None, recent_history=None,
                   now_ms=None, existing_hedge=False):
    """short_record: 账本 short 记录（含 entry_risk_anchor）。
    current_market: {price, dte_hours, short_delta, short_gamma, iv}。
    signal_evidence: SignalEvidencePackage（取 direction_evidence.edb + pre_trade_context.ggr）。
    返回 PositionRiskPackage（position_risk.v0.4，持续性两项制）。"""
    anchor = (short_record or {}).get("entry_risk_anchor") or {}
    se = signal_evidence or {}
    edb = (se.get("direction_evidence") or {}).get("edb")
    ggr = (se.get("pre_trade_context") or {}).get("ggr")
    return evaluate_position_risk(
        position_id=position_id,
        direction_bias=direction_bias,
        entry_risk_anchor=anchor,
        current_price=current_market.get("price"),
        dte_hours=current_market.get("dte_hours"),
        short_delta=current_market.get("short_delta"),
        short_gamma=current_market.get("short_gamma"),
        iv=current_market.get("iv"),
        loss_boundary=anchor.get("entry_loss_boundary"),
        edb=edb,
        gamma_regime=ggr,
        exit_friction=exit_friction,
        recent_history=recent_history,
        now_ms=now_ms,
        existing_hedge=existing_hedge,
    )

# ===================== module: strategy =====================
# -*- coding: utf-8 -*-
"""
主编排 main()（FMZ 入口）。两轮分离，运行后不经界面命令调整计划或仓位。

计划轮 ROUND_MODE="PLAN"：
  枚举所有符合范围(剩余到期/delta/腿宽)的同期垂直备选 → 初筛 top-K → S:PM 模拟 →
  按 胜率/盈亏比/信号 综合排序 → 输出方案库(含方案号+推荐标签)并持久化(_G)。绝不下单。
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
    """枚举同期垂直→初筛→top-K 跑 S:PM→排序。返回 (menu, pm_ok, model, reason, diag)。
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
            # 同期垂直：保护腿取同到期、更价外、腿宽达标者；长腿是定额风险封顶，
            # 便宜的 OTM 长腿正是所需 → **不套用过度虚值过滤**
            vprot = _first_in_width(legsel_protection_candidates(
                s_insts, short["strike"], want_call, PROTECTION_WIDTH_RANGE,
                None, 0.0))
            if not vprot:
                diag["无合格保护腿(腿宽内)"] += 1
                continue
            pq = quote_fn(vprot["instrument_name"])
            if not pq or pq.get("mark") is None:
                continue
            c = plan_assemble(ORDER_AMOUNT, spot, MIN_MARGIN_RELIEF_RATIO, pref,
                              want_call, short, sq, vprot, pq,
                              None, pm_ok, model, s_dte_h, s_dte_h)
            c["_re"] = {"short": short, "sq": sq, "prot": vprot, "pq": pq,
                        "s_dte": s_dte_h, "p_dte": s_dte_h}
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
            ORDER_AMOUNT, spot, MIN_MARGIN_RELIEF_RATIO, pref,
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
        "state": state,
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


# ---------- 整合 PLAN 通顺缝（R6）：真实菜单 → VRP 双门 → 组合硬预算 ----------

def integrated_plan_preview(spot, market_context=None, portfolio_state=None):
    """整合执行流的 PLAN 段（执行会话式）：真实 _build_menu → VRP 双门过滤(给 market_context 时)
    → 组合硬预算(给 portfolio_state 时) → 返回可锁定方案 + 各域裁决。

    main() 在拿到实时 IV/RV(market_context) 与组合状态后调用本函数；选中方案的会话锁定/授权
    (ExecutionSession+ApprovalIntent，plan_hash+TTL) 与 FMZ 命令栏交互在上线 spike 接入。
    边界：VRP/预算**只过滤**，不进 PLAN_WEIGHTS、不判方向、不解 ALLOW_TRADING。"""
    now_ms = _now_ms()
    menu, pm_ok, model, reason, diag = _build_menu(now_ms, spot)
    out = {"reason": reason, "menu": menu, "enum_diag": diag, "pm_ok": pm_ok,
           "vrp_passed": None, "vrp_blocked": None, "portfolio_budget": None,
           "lockable": list(menu or [])}
    if reason != "OK" or not menu:
        out["lockable"] = []
        return out
    # PRICE_GATE：VRP 双门（独立 AND 硬门；BLOCK 不进可锁定方案）
    if market_context:
        passed, blocked = apply_vrp_gate(menu, market_context)
        out["vrp_passed"] = [p for p, _g in passed]
        out["vrp_blocked"] = [{"id": p.get("id"), "reason_codes": g["reason_codes"]}
                              for p, g in blocked]
        out["lockable"] = list(out["vrp_passed"])
    # 组合硬预算（缺口2，入场前额外 AND 门；占位安全：超即 size=0 → 无可锁定）
    if portfolio_state:
        budget = evaluate_portfolio_budget(
            portfolio_state.get("current", {}), portfolio_state.get("limits", {}),
            portfolio_state.get("proposed_size", ORDER_AMOUNT))
        out["portfolio_budget"] = budget
        if budget["decision"] == "BLOCK":
            out["lockable"] = []
    return out


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
    rv = plan_assemble(ORDER_AMOUNT, spot, MIN_MARGIN_RELIEF_RATIO, pref,
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
            rv.get("breakeven"), SIGNAL_STATE, "UNKNOWN")
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


# ========== E2：单一持续主链 run_cycle（取代 PLAN/ORDER 双脚本；main() 于 E2.3 切换）==========

_SESSION_KEY = "spm_session_id_v1"
_REFRESH_KEY = "spm_refresh_seq_v1"
_LIB_KEY = "spm_reco_lib_v1"
_LOCKED_KEY = "spm_locked_plan_v1"
_RUNTIME_KILL_KEY = "spm_runtime_kill_v1"
_LIB_BUILD_TS_KEY = "spm_lib_build_ts_v1"


def _session_id():
    sid = _G(_SESSION_KEY)
    if not sid:
        sid = "sess-%d" % _now_ms()
        _G(_SESSION_KEY, sid)
    return sid


def _refresh_seq():
    return int(_G(_REFRESH_KEY) or 0)


def _bump_refresh_seq():
    n = _refresh_seq() + 1
    _G(_REFRESH_KEY, n)
    return n


def _effective_kill():
    """配置急停 KILL_NEW_RISK 或运行时【急停】命令（_G）任一为真即急停。"""
    return bool(KILL_NEW_RISK) or bool(_G(_RUNTIME_KILL_KEY))


def _gate_summary_now():
    return gate_summary(ALLOW_ENTRY_TRADING, ALLOW_EXIT_TRADING, ALLOW_HEDGE_TRADING,
                        _effective_kill(), EMERGENCY_REDUCE_ONLY)


def _signal_allows_entry(verdict):
    if (verdict or {}).get("availability") == "OFFLINE_MANUAL":
        return SIGNAL_STATE in ENTER_SIGNALS          # 离线手动：静态信号决定
    return bool((verdict or {}).get("tradeable")) and not (verdict or {}).get("block_new_opens")


def _has_position(state):
    return state in (S_SHORT_ACTIVE_PROTECTED, S_PROTECTION_ACTIVE_NO_SHORT,
                     S_SHORT_FLAT_LONG_RESIDUAL)


def _handle_execute(code, now_ms):
    """硬授权：在当前推荐库按确认码定位冻结快照 → 锁定不可变副本。
    预提交 13 项硬门与受控真实开仓由后续每轮 _attempt_commit 评估（见 E3.4）。"""
    lib = _G(_LIB_KEY)
    snap = resolve_confirm_code(lib, code)
    if not snap:
        return "confirm_code_invalid_or_stale"
    locked = dict(snap)
    locked["locked_ts"] = now_ms
    _G(_LOCKED_KEY, locked)
    return "locked"


def _handle_command(ctype, cmd, now_ms):
    if ctype == "KILL":
        _G(_RUNTIME_KILL_KEY, True)
        return "killed_new_risk"
    if ctype == "RESUME":
        _G(_RUNTIME_KILL_KEY, None)
        _G(_LOCKED_KEY, None)                          # 恢复要求重新对账 + 新的计划硬批准
        return "resumed_requires_new_plan_approval"
    if ctype == "REJECT":
        _G(_LOCKED_KEY, None)
        return "rejected_back_to_wait"
    if ctype == "EXECUTE":
        return _handle_execute(cmd.get("arg"), now_ms)
    if ctype == "EXIT_AUTHORIZE":
        return _handle_exit_authorize(cmd.get("arg"), now_ms, POLICY_TAKE_PROFIT)
    if ctype == "RISK_EXIT_AUTHORIZE":
        return _handle_exit_authorize(cmd.get("arg"), now_ms, POLICY_RISK_EXIT)
    if ctype == "EXIT_REVOKE":
        return _handle_exit_revoke(now_ms)
    return "noop"


def _handle_exit_authorize(code, now_ms, policy):
    """软授权：校验授权码与当前 position+policy 匹配 → 落 _G（与 position_id 绑定，非阻塞）。"""
    snap = _G(_POSITION_KEY)
    pos_id = (snap or {}).get("position_id")
    if not pos_id:
        return "no_position_to_authorize"
    kw = {}
    if policy == POLICY_RISK_EXIT:
        kw = {"max_exit_spend": RISK_EXIT_MAX_SPEND, "allowed_order_types": ["post_only"]}
    auth = authorize_from_code(code, pos_id, policy, now_ms, **kw)
    if not auth:
        return "auth_code_invalid"
    _G(_EXIT_AUTH_KEY, auth)
    return "authorized:" + policy


def _handle_exit_revoke(now_ms):
    auth = _G(_EXIT_AUTH_KEY)
    if not auth:
        return "no_authorization"
    _G(_EXIT_AUTH_KEY, revoke(auth, now_ms))
    return "revoked"


def _dispatch_command(raw, meta, now_ms):
    """轮询并分发一条 FMZ 命令；全部入命令账本审计，消费型严格幂等。"""
    res = route_command(raw, meta, now_ms)
    status, cmd = res["status"], res["command"]
    if status == "EMPTY":
        return {"action": None, "status": status}
    if status == "UNKNOWN":
        cmd_ledger_record(cmd, None, "UNKNOWN", "ignored", now_ms)
        return {"action": "UNKNOWN", "status": status}
    if status == "DUPLICATE":
        cmd_ledger_record(cmd, res["key"], "DUPLICATE", "ignored", now_ms)
        return {"action": cmd["type"], "status": status, "outcome": "duplicate_ignored"}
    outcome = _handle_command(cmd["type"], cmd, now_ms)
    cmd_ledger_record(cmd, res["key"], "ACCEPTED", outcome, now_ms)
    return {"action": cmd["type"], "status": status, "outcome": outcome}


_POSITION_KEY = "spm_entry_snapshot_v1"      # 冻结的 VerticalEntrySnapshot


def _current_portfolio():
    """当前组合风险载荷（E3：无并发持仓时为空；E4 接入真实多仓汇总）。"""
    return {"open_positions": 0, "short_gamma": 0.0, "short_vega": 0.0, "margin_used": 0.0}


def _build_precommit_live(locked, spot, verdict):
    """预取实时复核数据供 evaluate_precommit_checks。
    VRP 需 market_context（总线模式）；OFFLINE 无 → vrp_pass=None（fail-closed：不真实成交，仅空跑预览）。"""
    short_i = locked.get("short_instrument")
    long_i = locked.get("long_instrument")
    amount = locked.get("amount") or ORDER_AMOUNT
    sq, lq = exec_quote(short_i), exec_quote(long_i)
    quotes_fresh = bool(sq and lq and sq.get("mark") is not None and lq.get("mark") is not None
                        and sq.get("best_bid") not in (None, 0) and lq.get("best_ask") not in (None, 0))
    ssr, lsr = exec_spread_ratio(sq), exec_spread_ratio(lq)
    spread_ok = (ssr is not None and lsr is not None
                 and ssr <= MAX_SPREAD_RATIO and lsr <= MAX_SPREAD_RATIO)
    net_credit = fee_reserve = None
    if quotes_fresh:
        fee_reserve = (acct_option_fee_ccy(sq["mark"], amount)
                       + acct_option_fee_ccy(lq["mark"], amount))
        net_credit = (sq["mark"] - lq["mark"]) * amount - fee_reserve
    spm = spm_simulate_structure(SETTLEMENT_CURRENCY, short_i, long_i, amount)
    relief = (spm or {}).get("relief_ratio")
    proposed = {
        "short_gamma": (sq or {}).get("gamma") or 0.0,
        "short_vega": 0.0,                       # vega 待 Greeks 接入（E6/E7）
        "structure_margin": (spm or {}).get("im_with_protection"),
        "max_spread_loss": locked.get("max_loss"),
        "hedge_margin_reserve": 0.0,             # E7 接对冲保证金估算
        "fee_reserve": fee_reserve,
    }
    budget = evaluate_projected_budget(proposed, _current_portfolio(), PORTFOLIO_LIMITS)
    rec = ledger_reconcile(SETTLEMENT_CURRENCY)
    reconciled = (rec.get("actual") == rec.get("expected"))
    sig_pkg = verdict.get("package_id") or ("manual:" + str(SIGNAL_STATE))
    return {
        "signal_fresh": verdict.get("availability") in ("OK", "OFFLINE_MANUAL"),
        "sig_package_id": sig_pkg,
        "same_expiry": plan_expiry_label(short_i) == plan_expiry_label(long_i),
        "vrp_pass": None,                        # OFFLINE 无 market_context（总线模式接入 VRP 双门）
        "spm_relief": relief, "min_relief": MIN_MARGIN_RELIEF_RATIO,
        "quotes_fresh": quotes_fresh,
        "net_credit_after_costs": net_credit,
        "projected_budget_decision": budget.get("decision"),
        "ledger_reconciled": reconciled,
        "no_unknown_orders": True,               # 真实活动订单查询 E4 接入
        "spread_ok": spread_ok,
        "_budget": budget,
    }


def _attempt_commit(locked, spot, verdict, now_ms):
    """锁定方案 → 预提交 13 项 → 受 ENTRY 门控的真实开仓 → 冻结入场快照。
    预提交不过 / 门控关 → 仅空跑预览（不下单）。已尝试过真实下单则不重复（防双开，真实恢复 E4）。"""
    lib = _G(_LIB_KEY)
    live = _build_precommit_live(locked, spot, verdict)
    pre = evaluate_precommit_checks(locked, lib, live)
    amount = locked.get("amount") or ORDER_AMOUNT
    short_i, long_i = locked.get("short_instrument"), locked.get("long_instrument")
    result = {"precommit": pre, "budget": live.get("_budget"), "committed": False,
              "entry_snapshot": None, "reason": None,
              "order_intent": [
                  dict(leg="保护腿", **exec_plan_prices("buy", long_i, amount)),
                  dict(leg="卖方腿", **exec_plan_prices("sell", short_i, amount))]}
    if not pre["passed"]:
        result["reason"] = "PRECOMMIT_FAILED:" + ",".join(pre["failed"])
        return result
    gate = gate_decision(ACTION_ENTRY, ALLOW_ENTRY_TRADING, ALLOW_EXIT_TRADING,
                         ALLOW_HEDGE_TRADING, _effective_kill(), EMERGENCY_REDUCE_ONLY)
    if not gate["allowed"]:
        result["reason"] = "ENTRY_DRYRUN_OR_GATE_OFF:" + gate["reason"]    # 默认空跑预览
        return result
    if locked.get("commit_attempted"):
        result["reason"] = "COMMIT_ALREADY_ATTEMPTED_AWAIT_E4_RECOVERY"     # 防重复真实下单
        return result
    locked["commit_attempted"] = True
    _G(_LOCKED_KEY, locked)
    open_res = exec_open_structure(short_i, long_i, amount)
    if open_res.get("dry"):
        result["reason"] = "ORDER_PREVIEW_DRY"
        return result
    prot_fill = open_res.get("protection_fill") or {}
    short_fill = open_res.get("short_fill") or {}
    if (prot_fill.get("filled") or 0) <= 0:
        result["reason"] = "PROTECTION_NOT_FILLED"
        return result
    entry_fees = (acct_option_fee_ccy(short_fill.get("avg_price") or 0.0, short_fill.get("filled") or 0.0)
                  + acct_option_fee_ccy(prot_fill.get("avg_price") or 0.0, prot_fill.get("filled") or 0.0))
    snap = build_vertical_entry_snapshot(locked, short_fill, prot_fill, entry_fees, now_ms)
    _G(_POSITION_KEY, snap)
    _G(_LOCKED_KEY, None)                          # 锁定消费完毕
    ledger_set_state(S_SHORT_ACTIVE_PROTECTED)
    result["committed"] = True
    result["entry_snapshot"] = snap
    result["reason"] = "STRUCTURE_OPEN"
    return result


_RECOVERY_KEY = "spm_recovery_verdict_v1"
_EXIT_AUTH_KEY = "spm_exit_auth_v1"          # E5 软授权（此处先占位读取）


def _recovery_verdict():
    return _G(_RECOVERY_KEY) or {"state": "OK", "allow_new_open": True}


def startup_recovery_check(currency):
    """启动恢复：读交易所真实期权/永续持仓 + 账本短腿 → 裁决并落 _G（恢复完成前禁开新仓）。"""
    opt = dbt_get_positions(currency, "option") or []
    try:
        perp = dbt_get_positions(currency, "future") or []
    except Exception:
        perp = []
    perp_qty = sum(abs(p.get("size") or 0.0) for p in perp)
    led = ledger_load()
    short_qty = (led.get("short") or {}).get("amount") or 0.0
    verdict = evaluate_startup_recovery(opt, perp_qty, short_qty, active_orders=[])
    _G(_RECOVERY_KEY, verdict)
    return verdict


def _evaluate_take_profit(snap):
    """据入场快照 + 实时短腿盘口算止盈资格(参考捕获率) 与退出预算/价格上限。保护腿价值不入分母。"""
    if not snap:
        return {"ratio": None, "qualified": False, "remaining_short_qty": 0.0,
                "remaining_budget": None, "price_cap": 0.0, "quote_ok": False}
    rem_qty = snap.get("remaining_short_qty") or 0.0
    q = exec_quote(snap.get("short_instrument"))
    quote_ok = bool(q and q.get("mark") is not None and q.get("best_bid") not in (None, 0)
                    and q.get("best_ask") is not None)
    ceiling = snap.get("entry_profit_ceiling_net")
    max_spend = snap.get("max_total_exit_spend")
    realized = snap.get("realized_exit_spend") or 0.0
    cons_ref = (q["mark"] * rem_qty) if (quote_ok and rem_qty) else None
    est_fee = acct_option_fee_ccy(q["mark"], rem_qty) if quote_ok else None
    reserve = (max_spend * EXIT_RESERVE_RATIO) if isinstance(max_spend, (int, float)) else None
    ratio = reference_profit_capture_ratio(ceiling, cons_ref, est_fee, reserve)
    qualified = take_profit_qualified(ratio, snap.get("take_profit_target_ratio") or 0.80)
    fee_reserve = reserve or 0.0
    rem_budget = short_buyback_budget(max_spend, realized, fee_reserve)
    tick = (q or {}).get("tick") or 0.0
    cap = short_buyback_price_cap(rem_budget, fee_reserve, rem_qty, tick) if rem_budget else 0.0
    return {"ratio": ratio, "qualified": qualified, "remaining_short_qty": rem_qty,
            "remaining_budget": rem_budget, "price_cap": cap, "quote_ok": quote_ok}


def _apply_exit_fill(snap, step, now_ms):
    """把一次短腿买回成交计入入场快照：减剩余短腿、加已用退出支出；归零则转 SHORT_FLAT_LONG_RESIDUAL。"""
    filled = step.get("filled") or 0.0
    price = step.get("avg_price") or step.get("price") or 0.0
    fee = acct_option_fee_ccy(price, filled)
    snap["remaining_short_qty"] = max(0.0, (snap.get("remaining_short_qty") or 0.0) - filled)
    snap["realized_exit_spend"] = (snap.get("realized_exit_spend") or 0.0) + price * filled + fee
    snap["last_exit_ts"] = now_ms
    if snap["remaining_short_qty"] <= 1e-12:
        ledger_set_state(S_SHORT_FLAT_LONG_RESIDUAL)   # 短腿归零，转保护腿回收（不可直跳 CLOSED）
    _G(_POSITION_KEY, snap)


def _evaluate_hedge(snap):
    """对冲决策（场所感知）：按 HEDGE_VENUE 选 Deribit(反向) 或 Binance(线性) → perp 真实持仓 +
    目标(随剩余短腿敞口) + open/reduce 动作 + 孤儿。默认不真实下单。"""
    rem_qty = (snap or {}).get("remaining_short_qty") or 0.0
    vcfg = hedge_venue_config(HEDGE_VENUE, HEDGE_BINANCE_INSTRUMENT, HEDGE_BINANCE_MAKER_ONLY)
    state = "SETTLED" if rem_qty <= 0 else "OPEN"
    delta = (exec_quote((snap or {}).get("short_instrument")) or {}).get("delta") if snap else None
    if vcfg["venue"] == "BINANCE":
        perp_qty = bnc_get_position_btc(vcfg["instrument"])
        contract_size, min_trade = 1.0, HEDGE_BINANCE_MIN_TRADE
    else:
        try:
            perp = dbt_get_positions(SETTLEMENT_CURRENCY, "future") or []
        except Exception:
            perp = []
        perp_qty = sum((p.get("size") or 0.0) for p in perp)
        meta = dbt_get_instrument(vcfg["instrument"]) or {}
        contract_size = meta.get("contract_size") or HEDGE_CONTRACT_SIZE_FALLBACK
        min_trade = meta.get("min_trade_amount") or HEDGE_MIN_TRADE_FALLBACK
    target = hedge_target_contracts(rem_qty, delta, HEDGE_REDUCTION_RATIO, _spot_price(),
                                    contract_size, min_trade, state, linear=vcfg["linear"])
    return {"perp_qty": perp_qty, "target": target,
            "action": hedge_order_action(perp_qty, target, min_trade),
            "orphan": hedge_orphan(rem_qty, perp_qty),
            "side": hedge_side((snap or {}).get("side")),
            "venue": vcfg["venue"], "instrument": vcfg["instrument"], "venue_cfg": vcfg}


def manage_cycle(now_ms):
    """持仓管理一轮（设计稿 §9.1 的 10 域）：对账 + 止盈资格 + 退出活动(受授权+预算+门控) + 四输出仲裁。
    退出真实买回受 ALLOW_EXIT_TRADING 门控，默认空跑预览；对冲真实执行 E7 接入。"""
    snap = _G(_POSITION_KEY)
    pos_id = (snap or {}).get("position_id")
    rec = ledger_reconcile(SETTLEMENT_CURRENCY)
    recovery = _recovery_verdict()
    auth = _G(_EXIT_AUTH_KEY)
    authorized = is_authorized(auth, pos_id, now_ms)
    tp_code = auth_code(pos_id, POLICY_TAKE_PROFIT) if pos_id else None

    tp = _evaluate_take_profit(snap)
    exit_gate = gate_decision(ACTION_EXIT, ALLOW_ENTRY_TRADING, ALLOW_EXIT_TRADING,
                              ALLOW_HEDGE_TRADING, _effective_kill(), EMERGENCY_REDUCE_ONLY)["allowed"]
    decision = exit_campaign_decision(authorized, tp["qualified"], tp["remaining_short_qty"],
                                      tp["remaining_budget"], tp["quote_ok"], tp["price_cap"])
    exit_state = decision["state"]
    if decision["can_order"] and exit_gate and snap:        # 受控真实买回（默认门关→不进入）
        step = exec_exit_buyback_step(snap.get("short_instrument"), tp["remaining_short_qty"],
                                      tp["price_cap"], allow_live=True, label="exit_short")
        if not step.get("dry") and (step.get("filled") or 0) > 0:
            _apply_exit_fill(snap, step, now_ms)
            snap = _G(_POSITION_KEY)

    hedge = _evaluate_hedge(snap)
    hedge_exec = gate_decision(ACTION_HEDGE_OPEN, ALLOW_ENTRY_TRADING, ALLOW_EXIT_TRADING,
                               ALLOW_HEDGE_TRADING, _effective_kill(),
                               EMERGENCY_REDUCE_ONLY)["allowed"]
    # 受控对冲下单（默认门关→dry）：reduce/unwind 强制 reduce_only 且 kill 下仍允许；open/increase 受 emergency 阻断
    h_act = hedge["action"]["action"]
    if h_act != "HEDGE_HOLD":
        h_gate_act = ACTION_HEDGE_REDUCE if hedge["action"]["reduce_only"] else ACTION_HEDGE_OPEN
        if gate_decision(h_gate_act, ALLOW_ENTRY_TRADING, ALLOW_EXIT_TRADING, ALLOW_HEDGE_TRADING,
                         _effective_kill(), EMERGENCY_REDUCE_ONLY)["allowed"]:
            exec_hedge_step(hedge["venue_cfg"], hedge["side"], hedge["action"]["delta_contracts"],
                            hedge["action"]["reduce_only"], allow_live=True, label="hedge")
    pause = ("PAUSED_BY_BUDGET" if exit_state == EXIT_PAUSED_BUDGET else
             ("PAUSED_BY_DATA" if exit_state == EXIT_PAUSED_DATA else None))
    arb = unified_action_arbiter({
        "recovery_blocked": recovery.get("state") == "RECOVERY_BLOCKED",
        "orphan_hedge": (recovery.get("state") == "ORPHAN_HEDGE_EMERGENCY") or hedge["orphan"],
        "in_flight_order": False, "exit_preferred": False, "hedge_ready": False,
        "take_profit_ready": tp["qualified"],
        "exit_authorized": authorized,
        "exit_executable": bool(decision["can_order"] and exit_gate),
        "exit_pause_reason": pause,
        "hedge_executable": hedge_exec,
    })
    return {"arb": arb, "entry_snapshot": snap, "reconcile": rec,
            "auth": auth, "authorized": authorized, "tp_auth_code": tp_code,
            "exit_campaign_state": exit_state, "tp_ratio": tp["ratio"], "hedge": hedge}


def run_cycle(now_ms=None):
    """单一持续主链一轮：命令轮询 → 信号接收 → 门控 → 维护推荐库/锁定 → 渲染控制台。
    无持仓且信号放行时维护垂直推荐库 + 短确认码；`执行:码` 经 resolve 锁定 + 预提交。
    返回 ctx（含控制台字段），便于单测。真实开仓/管理/退出/对冲在 E3+ 接入。"""
    now_ms = now_ms or _now_ms()
    sid = _session_id()
    meta = {"robot_id": ROBOT_ID, "session_id": sid, "refresh_seq": _refresh_seq()}
    disp = _dispatch_command(GetCommand(), meta, now_ms)

    verdict = receive_signal(now_ms, SIGNAL_SOURCE, SIGNAL_FILE_PATH, SIGNAL_G_KEY,
                             SIGNAL_SCHEMA_VERSION_PREFIX)
    gsum = _gate_summary_now()
    kill = _effective_kill()
    state = ledger_get_state()
    has_pos = _has_position(state)
    locked = _G(_LOCKED_KEY)
    spot = _spot_price()

    pending = []
    commit_result = None
    manage_result = None
    recovery = _recovery_verdict()
    rec_ok = recovery.get("allow_new_open", True)
    if recovery.get("state") == "RECOVERY_BLOCKED":
        phase = "RECOVERY_BLOCKED"
    elif has_pos:
        manage_result = manage_cycle(now_ms)        # 持仓管理：急停下仍运行（停新风险不停管理）
        phase = "POSITION_MANAGE"
    elif kill:
        phase = "KILLED"
    elif locked:
        commit_result = _attempt_commit(locked, spot, verdict, now_ms)
        phase = "POSITION_MANAGE" if commit_result["committed"] else "PLAN_LOCKED"
    elif (not _signal_allows_entry(verdict)) or (not rec_ok):
        phase = ("OFFLINE_MANUAL" if verdict.get("availability") == "OFFLINE_MANUAL"
                 else "WAIT_SIGNAL")
    else:
        phase = "RECOMMEND_READY"
        lib = _G(_LIB_KEY)
        last_build = int(_G(_LIB_BUILD_TS_KEY) or 0)
        # 节流：每 PLAN_REFRESH_SECONDS 才重建一次推荐库（省 API + 稳定 refresh_seq/确认码）
        if spot and (not lib or now_ms - last_build >= PLAN_REFRESH_SECONDS * 1000):
            menu, pm_ok, model, reason, diag = _build_menu(now_ms, spot)
            if reason == "OK" and menu:
                rseq = _bump_refresh_seq()
                sig_pkg = verdict.get("package_id") or ("manual:" + str(SIGNAL_STATE))
                lib = build_recommendation_library(menu, sid, sig_pkg, rseq, now_ms)
                _G(_LIB_KEY, lib)
                _G(_LIB_BUILD_TS_KEY, now_ms)
        if lib and lib.get("recommendations"):
            pending = [{"id": s["plan_id"], "summary": s["summary"],
                        "confirm_code": s["confirm_code"]}
                       for s in lib["recommendations"][:MENU_SIZE]]
            phase = "HARD_APPROVAL_WAIT"

    ctx = _ctx_base(state, spot, "RUN_CYCLE:" + phase)
    ctx["console_phase"] = phase
    ctx["gate_summary"] = gsum
    ctx["signal_verdict"] = verdict
    ctx["pending_candidates"] = pending
    ctx["kill_new_risk"] = kill
    ctx["last_command"] = disp.get("action")
    ctx["last_command_outcome"] = disp.get("outcome")
    if commit_result:
        ctx["precommit"] = commit_result.get("precommit")
        ctx["order_intent"] = commit_result.get("order_intent")
        ctx["commit_reason"] = commit_result.get("reason")
        ctx["projected_budget"] = commit_result.get("budget")
        if commit_result.get("entry_snapshot"):
            ctx["entry_snapshot"] = commit_result["entry_snapshot"]
    if manage_result:
        ctx["action_arb"] = manage_result.get("arb")
        ctx["entry_snapshot"] = manage_result.get("entry_snapshot")
        if manage_result.get("authorized"):
            ctx["exit_auth_state"] = "已授权(AUTHORIZED)"
        else:
            ctx["exit_auth_state"] = "未授权 ｜ 授权止盈码 %s" % (manage_result.get("tp_auth_code") or "—")
        ctx["exit_campaign_state"] = manage_result.get("exit_campaign_state")
        _r = manage_result.get("tp_ratio")
        ctx["take_profit_ratio"] = ("%.1f%%" % (_r * 100)) if isinstance(_r, (int, float)) else "数据缺口"
        _h = manage_result.get("hedge")
        if _h:
            ctx["hedge_state"] = "[%s] %s 目标%.4g 当前%.4g %s%s" % (
                _h.get("venue") or "—", _h.get("side") or "—", _h.get("target") or 0.0,
                _h.get("perp_qty") or 0.0, _h["action"]["action"], "·孤儿" if _h.get("orphan") else "")
    if recovery.get("state") != "OK":
        ctx["recovery_state"] = recovery.get("state")
    if locked and not (commit_result and commit_result.get("committed")):
        ctx["locked_plan_summary"] = "%s %s" % (locked.get("confirm_code"), locked.get("summary"))
    _emit(ctx, "主链")
    return ctx


def main():
    errs = validate_config()
    if errs:
        Log("[config] 配置错误，拒绝运行:", "; ".join(errs))
        LogStatus("配置错误：" + "; ".join(errs))
        return

    Log("[boot] S:PM 垂直价差执行链 v%s 启动（单一主链 run_cycle）" % STRATEGY_VERSION,
        "ALLOW_ENTRY=%s" % ALLOW_ENTRY_TRADING, "信号源=%s" % SIGNAL_SOURCE,
        "currency=%s" % SETTLEMENT_CURRENCY)
    startup_recovery_check(SETTLEMENT_CURRENCY)        # 启动恢复：可解释映射 → OK/RECOVERY_BLOCKED/ORPHAN

    while True:
        try:
            run_cycle()
        except Exception as e:
            Log("[loop] 异常:", str(e))
        Sleep(LOOP_INTERVAL_MS)
