# Deribit S:PM 日历保护型卖方执行链 v1.0 主设计稿

适配目标：BTC / ETH 期权卖方策略  
保证金模式：Deribit `S:PM`（隔离组合保证金 / Segregated Portfolio Margin）  
策略周期：近端卖方腿 `24–72h DTE`，远期保护腿 `5–10d DTE`  
执行原则：保护腿优先、Maker-only、只追一步、全损耗记账  
当前版本定位：第一版主方案设计稿，不是自动方向判断器，不是自动加仓系统

---

## 0. 版本结论

第一版方案正式收束为：

```text
S:PM 账户
→ 远期日历保护腿库存
→ 24–72h 近端卖方腿
→ KPF 辅助空间选腿
→ S:PM 保证金模拟验证
→ 保护腿优先 maker-only 执行
→ 同向信号不加仓
→ 卖方腿持续运行 / 到期后状态驱动复用
→ 全损耗与保证金释放记账
```

核心结论：

> 当卖出 24–72h 到期期权时，主方案采用 **5–10 天远期保护腿 + 近端卖方腿**，而不是每一笔都配同到期保护腿。

原因不是远期保护腿“买入价更便宜”，而是：

```text
1. 远期保护腿可以复用；
2. 远期保护腿可以卖出回收残值；
3. 远期保护腿 theta 损耗更平滑；
4. 远期保护腿在 S:PM 下可能降低近端 short option 的保证金占用；
5. 同期保护腿虽然单次便宜，但重复购买、快速归零、手续费占比高；
6. 用户已明确：已有持仓时，同向信号不加仓，只作为持仓确认。
```

因此，第一版不再并列兼容“同期 vertical / 日历 diagonal / combo 多主线方案”，而是主线收束为：

```text
S:PM 下的远期日历保护型卖方执行链
```

---

## 1. 官方规则基础

### 1.1 S:PM 的设计含义

Deribit Portfolio Margin 会把组合整体纳入价格和波动率情景中评估，并以最差情景计算组合层面的保证金需求。相比标准保证金逐仓位计算后相加，S:PM 更适合识别同一结算资产内期权组合的风险抵消。

本策略使用 S:PM 的第一性目的：

```text
不是为了提高杠杆，
而是为了让远期 long option 对近端 short option 的组合风险缓冲被交易所风控识别。
```

### 1.2 期权手续费规则

Deribit BTC / ETH 期权交易费公式为：

```text
BTC option fee = MIN(0.0003 BTC, 0.125 * option_price_BTC) * amount
ETH option fee = MIN(0.0003 ETH, 0.125 * option_price_ETH) * amount
```

若内部以 USD 展示 BTC 权利金，可近似换算为：

```text
fee_usd = MIN(0.0003 * index_price, 0.125 * option_price_usd) * amount
```

重要结论：

```text
maker-only 不能免除期权显性交易费；
maker-only 主要避免的是吃价和盘口滑点；
便宜 OTM 期权经常直接承担约 12.5% 权利金比例的显性手续费。
```

### 1.3 Combo Order 不作为 v1.0 主路径

Deribit option combo 具备原子成交与潜在组合费用优势，但实际可选组合腿有限，更偏向标准价差需求者。用户当前策略是基于信号与 KPF 的狙击式卖方结构，不适合第一版依赖 combo book。

第一版结论：

```text
不使用 combo 作为主执行路径；
不默认使用 combo fee discount；
后续可作为 v1.1 研究项。
```

---

## 2. 第一性目标

本系统的目标不是构造标准同到期有限风险价差，而是：

> 在模型确认可交易窗口后，用远期 long option 作为可退出保护库存，降低近端 short option 的保证金锁定和尾部风险，同时尽量保留近端卖方 theta 收入。

第一版要同时满足：

```text
1. 不替前置模型判断方向；
2. 不替 KPF 判断空间；
3. 不做自动加仓；
4. 不以成交率为第一目标；
5. 不为了成交切换 taker；
6. 不把远期保护腿误称为同到期硬封顶；
7. 不隐藏日历错配风险；
8. 把显性手续费、mark 偏离、追价损耗、保护腿残值、S:PM 保证金释放全部记清楚。
```

---

## 3. 模块边界

### 3.1 前置模型负责

```text
DIE + Anchor：判断中性回路修复；
Bias Thesis：判断方向倾向是否可交易；
KPF：判断空间边界与流动性核心区；
人工 / 选腿约束：决定是否允许进入期权结构层。
```

### 3.2 期权结构层负责

```text
基于方向、KPF、S:PM 与保护腿库存：
1. 选择近端 short leg；
2. 选择远期 protection leg；
3. 验证保护腿是否对 S:PM 保证金有实际作用；
4. 输出保护成本、复用可能、残值风险、日历错配说明。
```

### 3.3 执行层负责

```text
1. 保护腿优先 maker-only；
2. 再卖近端 short leg；
3. 每条腿最多只追一步；
4. 禁止 market；
5. 禁止 post_only=False；
6. 禁止短腿数量超过保护腿可用数量；
7. 执行后完整记账。
```

---

## 4. 主业务流程

### 4.1 初始进入条件

只有前置模型输出：

```text
TRADE_SUPPORT_STRONG
或
TRADE_SUPPORT_WEAK
```

才允许进入期权结构层。

以下状态不进入期权结构：

```text
WAIT_CONFIRMATION
NO_TRADE_AMBIGUOUS
NO_TRADE_BLOCKED
```

### 4.2 用户当前操作纪律

用户已经明确：

```text
如果已有持仓后继续出现同向信号：
    不加仓；
    不新增保护腿；
    不新增 short leg；
    保持原腿组合不变；
    同向信号只记录为持仓继续有效的观察信息。
```

这条纪律写入 v1.0，不允许执行层自动突破。

---

## 5. 近端卖方腿设计

### 5.1 到期时间

```text
short leg DTE = 24–72h
```

### 5.2 行权价选择

由方向和 KPF 决定空间边界。

偏空卖 call：

```text
short call 放在当前价上方；
优先放在 KPF 上方风险区之前、边缘或外侧；
不能直接贴着当前价格；
不能落在刚被反复争夺的核心接受区内部。
```

偏多 / 下方守位卖 put：

```text
short put 放在当前价下方；
优先放在 KPF 下方风险区之前、边缘或外侧；
不能直接贴着当前价格；
不能落在刚被反复争夺的核心接受区内部。
```

KPF 只提供空间，不产生方向。

---

## 6. 远期保护腿设计

### 6.1 到期时间

默认：

```text
long protection DTE = 5–10d
优先中心 = 7d 附近
```

建议：

```text
short leg 24h → protection 5–7d
short leg 48h → protection 5–10d
short leg 72h → protection 7–10d
```

不建议 v1.0 直接选择两周以上保护腿，因为初始成本、vega 暴露和库存管理复杂度会明显增加。

### 6.2 保护腿方向

偏空卖 call：

```text
long protection = 更远到期 call
long strike > short call strike
```

偏多卖 put：

```text
long protection = 更远到期 put
long strike < short put strike
```

### 6.3 保护腿行权价

保护腿 strike 的核心原则：

> 放在 short leg 外侧，靠近下一 KPF 核心风险区，但不能过度虚值。

例如：

```text
当前上方核心 KPF = 78000
short call = 77000C

保护腿候选：
78000C / 80000C / 82000C
```

不能机械买：

```text
永远买 KPF 核心点本身
```

也不能为了便宜直接买：

```text
过远 OTM 灾难彩票腿
```

最终筛选依据：

```text
1. S:PM margin relief；
2. protection delta / vega 是否足够；
3. bid / ask 质量；
4. 日 theta 损耗；
5. 显性手续费占比；
6. 保护腿残值可回收性；
7. 是否过度虚值；
8. 是否能覆盖 KPF 风险非线性放大区。
```

---

## 7. S:PM 保证金模拟

### 7.1 必须模拟的账户状态

每次建仓前至少比较：

```text
A. 当前账户
B. 当前账户 + 近端 short leg
C. 当前账户 + 远期 long protection + 近端 short leg
```

核心指标：

```text
margin_relief_abs = IM(B) - IM(C)
margin_relief_ratio = margin_relief_abs / IM(B)
mm_relief_abs = MM(B) - MM(C)
available_funds_delta = available_funds(C) - available_funds(B)
```

### 7.2 结构有效性

如果：

```text
margin_relief_abs <= 0
或
margin_relief_ratio 极低
```

说明该远期保护腿没有完成本策略第一性任务：

```text
降低近端 short option 保证金锁定
```

此时该 protection leg 不应作为 v1.0 主方案保护腿。可选择：

```text
1. 更近一档 protection strike；
2. 更近到期但仍长于 short leg 的 protection；
3. 放弃本次；
4. 回退同期保护腿作为备用。
```

这不是方向门控，而是结构有效性验证。

---

## 8. 保护腿库存账本

一旦买入远期 protection leg，它进入库存。

### 8.1 字段

```json
{
  "inventory_id": "prot_BTC_05JUN26_80000C",
  "instrument": "BTC-05JUN26-80000-C",
  "option_type": "CALL",
  "strike": 80000,
  "expiry": "2026-06-05",
  "amount_total": 1.0,
  "amount_free": 1.0,
  "amount_allocated": 0.0,
  "entry_price": 0.0,
  "entry_fee": 0.0,
  "current_mark": 0.0,
  "unrealized_pnl": 0.0,
  "realized_short_premium_against_it": 0.0,
  "reuse_count": 0,
  "last_margin_relief_ratio": 0.0,
  "status": "AVAILABLE"
}
```

### 8.2 复用原则

v1.0 严格限制：

```text
同一时刻，1 张 long protection 最多覆盖 1 张 short leg。
```

禁止：

```text
1 张保护腿同时覆盖 2 张以上 short leg；
同向信号出现后自动加仓；
未重新模拟 S:PM 就复用 protection；
short amount > protection amount_free。
```

### 8.3 复用条件

当 short leg 到期或平仓后，若：

```text
1. protection DTE 仍充足；
2. protection 仍有流动性；
3. S:PM 仍识别其保证金抵消；
4. 前置模型仍允许同方向交易；
5. KPF 空间仍有效；
```

允许在同一 protection leg 下续卖下一轮 24–72h short。

---

## 9. 状态机

```text
NO_POSITION
↓
SIGNAL_READY
↓
PROTECTION_SELECTION
↓
SPM_SIMULATION
↓
PROTECTION_BUILDING
↓
PROTECTION_ACTIVE_NO_SHORT
↓
SHORT_BUILDING
↓
SHORT_ACTIVE_PROTECTED
↓
HOLD_MONITORING
↓
SHORT_EXPIRED_OR_CLOSED
↓
REUSE_DECISION
   ├── REUSE_PROTECTION_FOR_NEXT_SHORT
   ├── KEEP_PROTECTION_IDLE
   └── EXIT_PROTECTION
↓
CLOSED
```

### 9.1 同向信号处理

在 `SHORT_ACTIVE_PROTECTED` 状态下，如果出现同向信号：

```text
不加仓；
不新增 short；
不新增 protection；
只记录为 SAME_DIRECTION_CONFIRMATION；
更新持仓观察日志。
```

### 9.2 歧义 / 反向信号处理

如果出现：

```text
NO_TRADE_AMBIGUOUS
NO_TRADE_BLOCKED
反向 TRADE_SUPPORT
KPF 空间失效
```

则进入：

```text
EXIT_OR_WAIT_REVIEW
```

处理顺序：

```text
1. 先评估 short leg 是否需要平仓；
2. 若 short 已无暴露或已到期，评估 protection 是否卖出回收残值；
3. 不再续卖新的 short。
```

---

## 10. 执行规则

### 10.1 保护腿优先

如果没有可用 protection inventory：

```text
先买远期 protection leg；
成交多少，后续 short 最多卖多少。
```

如果已有可用 protection inventory：

```text
不重复买保护腿；
先重新做 S:PM 模拟；
确认 protection amount_free 足够；
再卖近端 short。
```

### 10.2 Maker-only

所有订单强制：

```text
type = limit
post_only = true
reject_post_only = true
```

禁止：

```text
market order
IOC / FOK
post_only = false
taker 补救
超过一步追价
```

### 10.3 挂单价格

买 protection：

```text
buy_price_0 = min(mark_price, best_ask - tick_size)
buy_price_1 = min(buy_price_0 + tick_size, best_ask - tick_size)
```

卖 short：

```text
sell_price_0 = max(mark_price, best_bid + tick_size)
sell_price_1 = max(sell_price_0 - tick_size, best_bid + tick_size)
```

每条腿最多追一步。

---

## 11. 损耗记账

### 11.1 固定拆解项

每笔都必须拆：

```text
A. 显性交易费
B. mark 偏离
C. 一步追价损耗
D. bid/ask 价差损耗
E. 日历错配风险
F. protection theta 消耗
G. protection 残值变化
H. S:PM 保证金释放
I. short premium 收入
J. protection 库存摊销成本
```

### 11.2 远期保护腿真实成本

不能用买入价全额计入单笔交易。

正确口径：

```text
protection_realized_cost
= entry_price
+ entry_fee
+ exit_fee
+ spread_slippage
- exit_value
```

按保护天数：

```text
protection_cost_per_day
= protection_realized_cost / protected_days
```

按覆盖 short 周期：

```text
protection_cost_per_short_cycle
= protection_realized_cost / covered_short_cycle_count
```

### 11.3 Full-burn 压力测试

额外输出：

```text
full_burn_cost = entry_price + entry_fee
```

用途：

```text
只作为最坏情况压力测试；
不得作为默认真实成本口径。
```

---

## 12. 同期保护腿的定位

同期保护腿不作为 v1.0 主路径，只作为备用。

适用条件：

```text
1. 只做一次性交易；
2. S:PM 不认可远期保护腿；
3. 远期保护腿 bid/ask 太差；
4. 远期保护腿成本过高且无法回收残值；
5. 需要严格同到期硬风险封顶；
6. protection 库存不可用。
```

否则主路径仍为：

```text
远期日历保护腿
```

---

## 13. 输出报告

每次建仓前输出：

```json
{
  "structure_type": "CALENDAR_PROTECTED_SHORT_OPTION",
  "account_margin_mode": "S:PM",
  "short_leg": {
    "instrument": "...",
    "dte_hours": 48,
    "side": "SELL",
    "role": "NEAR_TERM_SHORT_PREMIUM"
  },
  "protection_leg": {
    "instrument": "...",
    "dte_days": 7,
    "side": "BUY",
    "role": "FAR_TERM_ECONOMIC_PROTECTION",
    "is_inventory_reuse": false
  },
  "kpf_context": {
    "short_boundary": "...",
    "protection_target_zone": "...",
    "comment_cn": "KPF 用于空间选腿，不用于方向判断。"
  },
  "spm_report": {
    "im_short_only": 0,
    "im_with_protection": 0,
    "margin_relief_abs": 0,
    "margin_relief_ratio": 0,
    "pm_accepted": true
  },
  "cost_report": {
    "estimated_entry_fee": 0,
    "estimated_mark_slippage": 0,
    "estimated_chase_slippage": 0,
    "full_burn_cost": 0,
    "expected_recoverable_value": null,
    "cost_basis_note_cn": "保护腿真实成本按退出残值与覆盖周期摊销，不按买入价一次性计入。"
  },
  "execution_policy": {
    "maker_only": true,
    "max_chase_steps": 1,
    "protection_first": true,
    "allow_add_on_same_direction_signal": false
  }
}
```

---

## 14. v1.0 验收标准

必须满足：

```text
1. 默认主路径为 S:PM + 远期 protection + 近端 short；
2. 不再把同到期 vertical 作为主路径；
3. 同向信号出现时不加仓；
4. 每次 short 前重新检查 protection 是否可用；
5. 每次 short 前重新模拟 S:PM 保证金释放；
6. 保护腿优先 maker-only；
7. 每条腿最多追一步；
8. 不允许 taker；
9. 不允许 protection 被并行过度复用；
10. 保护腿真实成本按退出残值和覆盖周期摊销；
11. full-burn 只作为压力测试；
12. 日历保护腿必须明确标记为“经济保护”，不是同到期硬封顶。
```

---

## 15. 第一版定稿句

> v1.0 采用 S:PM 下的远期日历保护腿主线：当模型和 KPF 给出可交易窗口时，系统选择 24–72h 近端 short option，并用 5–10d 同方向、略虚值但不过度虚值的远期 long option 作为可复用经济保护；保护腿由 KPF 风险区辅助定位，并经 S:PM 模拟验证保证金抵消效果；已有同向持仓时不加仓，只记录信号确认；卖方腿结束后由状态机决定复用或卖出保护腿；执行层严格保护腿优先、maker-only、最多追一步，并全量记录手续费、滑点、theta 消耗、保护腿残值、保证金释放与库存摊销成本。
