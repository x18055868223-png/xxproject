# -*- coding: utf-8 -*-
"""
Human Audit Gate 执行层配置块（FMZ 启动前手填）。

本版本只接受执行层本地人工审计上下文：方向、到期范围、delta 范围、腿宽、
数量和审计卡引用均来自这些 UPPER_CASE 参数。信号层不参与执行主链路。
"""

# ===== 当前版本号（显示于启动日志/面板/合成文件头）=====
# 3.0.0-manual-gate：独立人工审计门执行层，人工上下文驱动计划。
STRATEGY_VERSION = "3.0.0-manual-gate"

# ===== 实例标识（命令幂等键 robot_id；多机器人部署时各自唯一，避免跨实例命令串扰）=====
ROBOT_ID = "spm-exec-1"

# ===== 人工审计门输入（手填）=====
# MANUAL_PLANNING_ALLOWED=False 时只等待人工审计门，不生成可执行确认码。
SETTLEMENT_CURRENCY = "BTC"
MANUAL_PLANNING_ALLOWED = False
DIRECTION_BIAS      = "SHORT_CALL"
MANUAL_AUDIT_CARD_ID = ""
MANUAL_AUDIT_NOTE = ""
MANUAL_CONTEXT_TTL_MIN = 30

# ===== 计划展示参数 =====
# 当前主链路由人工审计门 + 短确认码驱动；这些值仅保留为本地预览/测试入口。
ROUND_MODE   = "PLAN"             # "PLAN" / "ORDER"
SELECTED_PLAN = 0                 # 本地预览选中的方案【唯一编号】；0=未指定
MENU_SIZE    = 10                 # 方案库最多输出条数

# ===== 候选枚举范围（计划轮据此选出所有符合要求的备选）=====
SHORT_DELTA_RANGE      = (0.15, 0.45)   # 短腿 |delta| 接受范围（卖权利金主驱动）
PROTECTION_WIDTH_RANGE = (2000, 2500)   # 保护腿腿宽范围(USD)，以短腿行权为基准
# 仅同期垂直信用价差：保护腿与卖方短腿同到期、更价外。

# ===== 人工审计强度 → 偏好 delta（参与排序，不替模型判方向）=====

# ===== 方案排序综合分权重 =====
PLAN_WEIGHTS = {"win_rate": 0.50, "rr": 0.50, "manual": 0.0}

# ===== 标的参考价（留 None 走实时 index；真实市场以实时价 + delta 选档）=====
UNDERLYING_REF_PRICE = None

# ===== 周期（§5.1 / §6.1）=====
SHORT_DTE_HOURS = (24, 72)        # 卖方短腿 DTE 区间（小时）；保护腿同到期（垂直）
ORDER_AMOUNT = 0.1                # 单结构数量（Deribit 期权最小步长，BTC=0.1 / ETH=1）

# ===== 筛选 / 门控阈值 =====
MIN_MARGIN_RELIEF_RATIO = 0.10    # 量化设计稿「极低」：低于此则该保护腿不合格(§7.2)
DEEP_OTM_MAX_DELTA = 0.05         # 保护腿「过度虚值」判定：|delta| 低于此视为灾难彩票腿(§6.3)
MIN_SHORT_PREMIUM  = 0.0005       # 短腿最小权利金(结算币)：低于则权利金过薄、手续费占比高 → 弃
MAX_SPREAD_RATIO   = 0.60         # 短腿最大相对价差 (ask-bid)/mid：超过视为流动性差 → 弃/不成交
PROTECTION_LOW_PREMIUM_MAX = 0.0006     # 保护腿足够便宜时，点差比例可被绝对价差门替代
PROTECTION_ABS_SPREAD_MAX  = 0.00015    # 保护腿绝对 bid-ask 宽度上限；覆盖 0.0003/0.0004 这类低价保护腿

# ===== 执行 =====
MAX_CHASE_STEPS    = 1            # 每条腿最多追价步数(§10.3)
CHASE_WAIT_SECONDS = 8            # 挂单后判定未成交的等待秒数
UNWIND_PROTECTION_ON_NO_SHORT = True  # 保护腿成交但短腿挂不上时，自动 maker 卖回保护腿(一次)避免裸保护

# ===== 开仓活动（entry campaign；跨轮持久 maker + 信用底线，低成本 ∧ 提高成功率）=====
ENTRY_MIN_NET_CREDIT = 0.0       # 入场净 credit 下限(结算币)：低于则不挂/暂停等市场(保低成本)。0=至少非负
ENTRY_MAX_TICK_STEPS = 3         # 信用底线内逐 tick 向触价改善的最大档(>MAX_CHASE_STEPS，给开仓更多成交空间)
ENTRY_MAX_ATTEMPTS   = 20        # 开仓活动最大尝试轮数(跨轮)；超且未成交→放弃(撤/回退保护腿、回等待)

# ===== 执行授权门控 =====
# 默认全安全（空跑）；FMZ 运行时可单独改各门。逐动作语义见 gates.py。
ALLOW_ENTRY_TRADING   = False     # 开立新垂直价差（新增风险）。False=进场空跑(只展示将执行方案)
ALLOW_EXIT_TRADING    = False     # 买回卖方短腿 / 卖出保护腿（期权降风险退出）
ALLOW_HEDGE_TRADING   = False     # BTC-PERPETUAL 对冲开 / 加 / 减仓
KILL_NEW_RISK         = False     # 急停：停新风险并撤开仓单；不阻断退出/对冲减仓/对账/孤儿清理
EMERGENCY_REDUCE_ONLY = False     # 紧急只减：禁止任何开/加仓，对冲强制 reduce_only
# 历史单门仅作为显示/测试占位；实际动作授权以 ALLOW_*_TRADING 为准。
ALLOW_TRADING = False
KILL_SWITCH   = False
DRY_RUN_PASSED = False

# ===== 运行参数 =====
LOOP_INTERVAL_MS    = 3000        # 主循环间隔
PLAN_REFRESH_SECONDS = 45         # 计划轮重算方案库的最小间隔(秒)：节流 API + 防刷屏
APPROVAL_TTL_MS = 30 * 60 * 1000  # 硬批准有效期；超时清锁并要求重新确认

# ===== 组合投影预算限额（P0-6；fail-closed。阈值为占位，未校准）=====
PORTFOLIO_LIMITS = {
    "max_open_positions": 1,
    "max_short_gamma": 0.05,
    "max_short_vega": 0.50,
    "max_margin": 0.50,                # 结算币(BTC)计占用保证金上限
    "max_spread_loss_per_trade": 0.02,
}

# ===== 风险退出授权（P1：独立于普通止盈预算的风险退出最大支出；0=仅用入场冻结退出预算）=====
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
# 对冲腿非高频、可等 maker 成交 → 省成本。跨所对账/恢复需人工核对。
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
    if not isinstance(MANUAL_PLANNING_ALLOWED, bool):
        errs.append("MANUAL_PLANNING_ALLOWED must be bool")
    if DIRECTION_BIAS not in ("SHORT_CALL", "SHORT_PUT"):
        errs.append("DIRECTION_BIAS 必须为 SHORT_CALL 或 SHORT_PUT")
    if MANUAL_CONTEXT_TTL_MIN <= 0:
        errs.append("MANUAL_CONTEXT_TTL_MIN must be > 0")
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
    if PROTECTION_LOW_PREMIUM_MAX < 0 or PROTECTION_ABS_SPREAD_MAX < 0:
        errs.append("保护腿低价点差阈值不可为负")
    if PLAN_REFRESH_SECONDS < 1:
        errs.append("PLAN_REFRESH_SECONDS 必须 >= 1")
    if APPROVAL_TTL_MS <= 0:
        errs.append("APPROVAL_TTL_MS 必须 > 0")
    if not (0.0 < MIN_MARGIN_RELIEF_RATIO < 1.0):
        errs.append("MIN_MARGIN_RELIEF_RATIO 应在 (0,1)")
    for _n, _v in (("ALLOW_ENTRY_TRADING", ALLOW_ENTRY_TRADING),
                   ("ALLOW_EXIT_TRADING", ALLOW_EXIT_TRADING),
                   ("ALLOW_HEDGE_TRADING", ALLOW_HEDGE_TRADING),
                   ("KILL_NEW_RISK", KILL_NEW_RISK),
                   ("EMERGENCY_REDUCE_ONLY", EMERGENCY_REDUCE_ONLY),
                   ("DRY_RUN_PASSED", DRY_RUN_PASSED)):
        if not isinstance(_v, bool):
            errs.append(_n + " 必须为布尔值")
    _live_gates = ALLOW_ENTRY_TRADING or ALLOW_EXIT_TRADING or ALLOW_HEDGE_TRADING
    if _live_gates and not DRY_RUN_PASSED:
        errs.append("DRY_RUN_PASSED=False; live trading gates must stay disabled")
    if ALLOW_ENTRY_TRADING:
        if not ALLOW_EXIT_TRADING:
            errs.append("ALLOW_ENTRY_TRADING=True requires ALLOW_EXIT_TRADING=True")
        if RISK_EXIT_MAX_SPEND <= 0:
            errs.append("ALLOW_ENTRY_TRADING=True requires RISK_EXIT_MAX_SPEND > 0")
    if RISK_EXIT_MAX_SPEND < 0:
        errs.append("RISK_EXIT_MAX_SPEND must be non-negative")
    if not (0 <= EXIT_RESERVE_RATIO < 1):
        errs.append("EXIT_RESERVE_RATIO must be in [0,1)")
    if not (0 < HEDGE_REDUCTION_RATIO <= 1):
        errs.append("HEDGE_REDUCTION_RATIO must be in (0,1]")
    _required_limits = (
        "max_open_positions",
        "max_short_gamma",
        "max_short_vega",
        "max_margin",
        "max_spread_loss_per_trade",
    )
    if not isinstance(PORTFOLIO_LIMITS, dict):
        errs.append("PORTFOLIO_LIMITS must be a dict")
    else:
        for _k in _required_limits:
            _limit = PORTFOLIO_LIMITS.get(_k)
            if not isinstance(_limit, (int, float)) or isinstance(_limit, bool) or _limit < 0:
                errs.append("PORTFOLIO_LIMITS.%s must be a non-negative number" % _k)
    if HEDGE_VENUE not in ("DERIBIT", "BINANCE"):
        errs.append("HEDGE_VENUE 必须为 DERIBIT 或 BINANCE")
    if ENTRY_MAX_ATTEMPTS < 1 or ENTRY_MAX_TICK_STEPS < 0:
        errs.append("ENTRY_MAX_ATTEMPTS≥1、ENTRY_MAX_TICK_STEPS≥0")
    return errs
