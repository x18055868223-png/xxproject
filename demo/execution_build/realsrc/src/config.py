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
#   2.1.0 对冲场所可选：新增 Binance USDC 永续 maker-0 备选（操作者显式配置选择 HEDGE_VENUE，默认 Deribit） →
#   2.2.0 开仓活动 entry_campaign：跨轮持久 maker + 信用底线(ENTRY_MIN_NET_CREDIT)，取代一次性追价开仓，
#         保护腿先成交、逐 tick 向触价改善至信用上限、超 ENTRY_MAX_ATTEMPTS 放弃回退；低成本∧提高成功率 →
#   2.3.0 持仓后链路补强(审计 P0①②③)：①统一持仓真相到 _POSITION_KEY(reconcile/recovery 改读、反向校验)；
#         ②保护腿回收执行器 + 两腿与对冲归零→CLOSED 归档；③manage 由四输出仲裁单动作收口 + 退出期禁新增对冲
#   2.4.0 风险严重度→仲裁(审计 C.2)：入场冻结 entry_risk_anchor；manage 每轮 evaluate_position_risk →
#         tail_risk_state 映射 exit_preferred/hedge_ready(替代 False hook)，EXIT_PREFERRED 经退出活动机制
#         买回(风险退出授权)、HEDGE_READY 经对冲收口；对冲数量改用结构净 delta(短−保护)+方向符号核对挡反向加仓
#   2.5.0 审计整改(F1-F3/C1-C3)：F1 风险退出独立预算(max_exit_spend)+可越价吃单+越价不可成交则回退对冲；
#         F2 控制台「风险」行+风险退出授权码+操作提示；F3 短腿盘口缺 delta/IV 显式数据缺口(不静默 NORMAL)；
#         C1 Deribit 对冲下单等待+撤残单+成交确认+None 盘口守门；C2 孤儿对冲清理不受 allow_hedge 阻断；
#         C3 no_unknown_orders 真实活动订单查询(fail-closed)+启动恢复按在途活动进度/活动订单重校验
#   2.6.0 外部审计 Tier0 整改：①INV-03 部分短腿成交后放弃→冻结 1:1 已覆盖较小持仓、只退多余保护、
#         绝不留裸短腿(原全退保护会留裸卖方)；②INV-04/10 dbt_get_positions 读失败返回 None(区别确实空[])，
#         startup_recovery_check 持仓/活动订单读取未知→DATA_UNKNOWN 禁开新仓(不凭本地空判"交易所无仓")
#   2.7.0 T0 扩展(外部审计复核)：T0-B 恢复状态默认 fail-closed(_recovery_verdict 不存在→RECOVERY_NOT_CHECKED、
#         禁开新仓，不再默认放行)；T0-C 信号方向权威守门(FILE/G 下 side_hint 须与 DIRECTION_BIAS 一致、
#         否则 fail-closed，防 side_hint=put 却生成 call 价差；OFFLINE_MANUAL 仍静态权威仅 DRY_RUN)
STRATEGY_VERSION = "2.7.0"

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

# ===== 开仓活动（entry campaign；跨轮持久 maker + 信用底线，低成本 ∧ 提高成功率）=====
ENTRY_MIN_NET_CREDIT = 0.0       # 入场净 credit 下限(结算币)：低于则不挂/暂停等市场(保低成本)。0=至少非负
ENTRY_MAX_TICK_STEPS = 3         # 信用底线内逐 tick 向触价改善的最大档(>MAX_CHASE_STEPS，给开仓更多成交空间)
ENTRY_MAX_ATTEMPTS   = 20        # 开仓活动最大尝试轮数(跨轮)；超且未成交→放弃(撤/回退保护腿、回等待)

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
    if ENTRY_MAX_ATTEMPTS < 1 or ENTRY_MAX_TICK_STEPS < 0:
        errs.append("ENTRY_MAX_ATTEMPTS≥1、ENTRY_MAX_TICK_STEPS≥0")
    return errs
