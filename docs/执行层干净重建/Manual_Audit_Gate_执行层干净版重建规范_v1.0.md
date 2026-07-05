# 执行层干净版重建规范：Human Audit Gate 手工桥接模式

**版本**：v1.0
**日期**：2026-06-26
**适用工程**：`x18055868223-png/xxproject`
**交付对象**：工作 Agent、测试 Agent、审计 Agent
**当前执行层基础**：`demo/execution_build/realsrc/` 与当前 FMZ 单文件执行层 `spm_calendar_protected_short_v1.py`
**目标版本建议名**：`manual_audit_execution_v3.0` 或 `spm_manual_gate_v3`
**核心原则**：执行层不消费信号层数据；Human Audit Gate 是唯一桥连放行者；执行层只负责计划、建仓、退出、对冲、止盈、恢复和审计。

---

# 0. 总结

本轮目标不是增强信号自动化，而是**反向收口执行层职责**。

最终工作流定为：

```text
信号层
    ↓
人工审计面板阅读
    ↓
Human Audit Gate 人工决定是否进入执行评估
    ↓
FMZ 参数 / 手动表单输入最小执行上下文
    ↓
执行层独立计划、选期、选腿、定价、VRP、S:PM、预算、建仓可行性
    ↓
人工批准具体方案
    ↓
执行层程序化下单
    ↓
执行层独立管理止盈、退出、对冲、恢复、归档
```

执行层不再读取：

```text
SignalEvidencePackage
signal_bridge
signal_receiver
EDB
DIE
Anchor
TMV
CVD
Macro
Funding
Skew
GGR
LLM review
time-zone durability
```

执行层只接受人工审核后的少量必要输入：

```text
方向
允许 DTE 范围
Delta 范围
腿宽范围
单笔数量
最大亏损/预算约束
最低 credit / 建仓可行性约束
是否允许对冲
是否允许自动退出
人工审计来源记录
```

这不是放弃信号层，而是将信号层定位为：

```text
审计材料生产者
```

而不是：

```text
执行层机器输入源
```

---

# 1. 为什么要做干净版

## 1.1 策略哲学

本策略建立在：

```text
个人投资者高弃权性
低频人工审计
少数机制一致窗口
卖方有限风险结构
强对账和强降险
```

之上。

因此执行层不需要追求：

```text
信号一出现自动下单
```

而应该追求：

```text
人工放行后，执行过程不出错、不脏、不丢状态、不扩大自由度
```

## 1.2 强信号桥接的复杂度不值得

若信号层直接驱动执行层，需要处理：

```text
schema 版本
signal package TTL
episode_id
side_hint 映射
同一信号一次性消费
新旧信号覆盖
持仓后信号反转
信号层宕机
执行层重启恢复
信号层字段变化
LLM 审计状态
时区层更新
```

这些复杂度对机构自动化有价值，但对个人低频人工审计策略，收益有限。

## 1.3 执行层越少解释市场越稳

执行层最重要的不是“再判断一次市场”，而是：

```text
正确读取期权链
正确选择结构
正确计算可成交性
正确验证预算
正确下单
正确处理部分成交
正确退出
正确对冲
正确恢复
正确归档
```

市场解释留给信号层和人工审计。

执行层只保留可执行事实。

---

# 2. 新系统边界

## 2.1 信号层职责

信号层继续负责：

```text
发现可能窗口
构造市场论证
生成审计卡
展示多因子链路
LLM 审计
时间链差分
Macro/GGR/Skew/Funding 等解释
```

信号层不负责：

```text
向执行层自动传递交易许可
决定具体期权腿
决定订单价格
决定仓位数量
决定对冲
决定退出
```

## 2.2 Human Audit Gate 职责

人工负责：

```text
阅读信号审计面板
决定是否进入执行评估
将最小人工执行上下文填入执行层
批准或拒绝具体执行方案
```

人工不负责：

```text
手工下单
手工处理部分成交
手工对账
手工计算期货对冲数量
手工绕过硬门
手工修改执行层已锁定方案
```

## 2.3 执行层职责

执行层负责：

```text
读取 Deribit 期权链和账户状态
按人工输入方向与范围枚举候选
计算建仓可行性
计算 mark / executable credit
运行 VRP
运行 S:PM
运行组合预算
生成方案菜单
冻结审批快照
预提交复核
程序化下单
处理部分成交
止盈
风险退出
期货对冲
保护腿回收
孤儿对冲清理
启动恢复
审计日志
```

执行层不负责：

```text
消费信号层 JSON
重算 EDB
理解 DIE/Anchor
复用 Macro/GGR/Funding/Skew 原始信号因子
判断市场方向是否成立
自动放行新风险
```

---

# 3. 目标交付物

建议在仓库中建立新执行版本，不直接污染当前实验版本。

推荐目录：

```text
demo/execution_build_manual_gate/
    realsrc/
        src/
        tests/
        build_bundle.py
    README.md
```

或如果继续沿用现有目录，则明确新版本标识：

```text
STRATEGY_VERSION = "3.0.0-manual-gate"
```

最终 FMZ 单文件输出：

```text
demo/最新交付物/spm_manual_gate_execution_fmz.py
```

---

# 4. 必须删除或关闭的信号消费路径

## 4.1 删除机器信号源配置

当前存在：

```python
SIGNAL_SOURCE = "OFFLINE_MANUAL"  # OFFLINE_MANUAL / FILE / G
SIGNAL_FILE_PATH = "demo/logs/signal_evidence.json"
SIGNAL_G_KEY = "nrd_signal_evidence_pkg"
SIGNAL_SCHEMA_VERSION_PREFIX = "nrd.integration.signal."
```

新干净版建议删除：

```text
FILE
G
SIGNAL_FILE_PATH
SIGNAL_G_KEY
SIGNAL_SCHEMA_VERSION_PREFIX
signal_receiver.py 对开仓的实际依赖
```

若为了兼容保留文件，也必须做到：

```text
默认不导入
默认不调用
开仓主链不读取
测试确认无信号 JSON 时执行层仍可完整运行
```

## 4.2 删除 SignalEvidence 对方向和准入的权威性

执行层方向来源只允许：

```text
ManualExecutionContext.direction_bias
或
FMZ 参数 DIRECTION_BIAS
```

不得从：

```text
side_hint
EDB lean
SignalEvidencePackage
```

读取。

## 4.3 禁止信号层状态进入对冲触发主逻辑

对冲触发只使用执行层本地风险：

```text
EntryRiskAnchor
current price
short delta
protection delta
IV
Gamma
DTE
loss boundary
exit friction
hedge friction
```

信号层状态最多可作为人工审计备注，不进入代码路径。

---

# 5. 新的人工执行上下文

## 5.1 目标

执行层仍需知道：

```text
这次人工批准进入评估的方向是什么
审计来源是哪张卡
人工输入何时过期
计划范围是什么
```

但不需要消费全量信号。

因此新增轻量对象：

```text
ManualExecutionContext
```

它可以由 FMZ 参数、命令栏或手动 JSON 表单生成。

---

## 5.2 最小字段

```json
{
  "schema_name": "ManualExecutionContext",
  "schema_version": "nrd.execution.manual_context.v1",
  "context_id": "manual-20260626-001",
  "created_ts_ms": 0,
  "expires_ts_ms": 0,

  "operator_decision": "APPROVE_PLANNING",
  "direction_bias": "SHORT_CALL",

  "audit_reference": {
    "source": "SIGNAL_AUDIT_PANEL",
    "card_id": "BTC #4501",
    "card_time": "2026-06-25 20:35",
    "operator_notes": "人工审计偏空，允许执行层评估卖 Call。"
  },

  "planning_scope": {
    "dte_hours_min": 24,
    "dte_hours_max": 72,
    "short_delta_min": 0.15,
    "short_delta_max": 0.35,
    "protection_width_min": 2000,
    "protection_width_max": 2500,
    "amount": 0.1
  },

  "risk_policy": {
    "max_loss_per_trade": 0.02,
    "min_net_credit": 0.0,
    "allow_hedge_open": false,
    "allow_hedge_reduce": true,
    "allow_auto_take_profit": true,
    "allow_auto_risk_exit": false
  }
}
```

---

## 5.3 不允许包含的字段

ManualExecutionContext 不应包含：

```text
EDB score
TMV score
CVD raw
Macro score
GGR raw
Skew
LLM conclusion
具体行权价
订单价格
order id
真实仓位
API key
```

这些不是人工桥需要的数据。

---

## 5.4 FMZ 参数映射

保留或改造顶部参数：

```python
DIRECTION_BIAS = "SHORT_CALL" / "SHORT_PUT"
SHORT_DTE_HOURS = (24, 72)
SHORT_DELTA_RANGE = (0.15, 0.35)
PROTECTION_WIDTH_RANGE = (2000, 2500)
ORDER_AMOUNT = 0.1
MANUAL_AUDIT_CARD_ID = ""
MANUAL_AUDIT_NOTE = ""
MANUAL_CONTEXT_TTL_MIN = 30
```

建议新增：

```python
MANUAL_PLANNING_ALLOWED = False
```

执行层只有在：

```text
MANUAL_PLANNING_ALLOWED = True
```

时才生成可批准方案菜单。

否则只显示：

```text
WAIT_MANUAL_AUDIT_GATE
```

---

# 6. 新工作流

## 6.1 阶段 A：等待人工桥

```text
状态：WAIT_MANUAL_AUDIT_GATE
```

条件：

```text
MANUAL_PLANNING_ALLOWED = False
```

系统只做：

```text
显示行情状态
显示账户恢复状态
不枚举方案
不读取信号层
不下单
```

## 6.2 阶段 B：人工允许进入计划轮

人工在 FMZ 配置或命令栏填：

```text
MANUAL_PLANNING_ALLOWED = True
DIRECTION_BIAS = SHORT_CALL / SHORT_PUT
SHORT_DTE_HOURS
SHORT_DELTA_RANGE
PROTECTION_WIDTH_RANGE
ORDER_AMOUNT
MANUAL_AUDIT_CARD_ID
```

执行层生成：

```text
ManualExecutionContext
```

并进入：

```text
PLAN_BUILDING
```

## 6.3 阶段 C：执行层独立计划

执行层执行：

```text
Deribit instruments
ticker / greeks
S:PM
VRP
建仓可行性
预算
枚举漏斗
```

输出方案菜单。

## 6.4 阶段 D：人工批准具体方案

人工看到：

```text
期号
短腿
保护腿
Delta
最大亏损
可成交 credit
VRP
S:PM
建仓可行性
预算
退出/对冲政策
```

只允许：

```text
APPROVE_PLAN
REJECT_PLAN
DEFER
```

批准后系统冻结：

```text
PlanApprovalSnapshot
```

## 6.5 阶段 E：预提交复核

执行层重新检查：

```text
Manual context 未过期
Plan hash 未变
实时报价仍有效
建仓可行性未恶化
VRP 仍通过
S:PM 仍通过
预算仍通过
无未知订单
恢复状态 OK
```

## 6.6 阶段 F：程序化建仓

执行层执行：

```text
保护腿优先
短腿不超过保护腿
maker-first / limited chase
部分成交管理
entry campaign 状态持久化
```

## 6.7 阶段 G：持仓管理

执行层独立完成：

```text
止盈
风险退出
对冲
保护腿回收
孤儿对冲清理
归档
```

---

# 7. 可继承资产清单

当前执行层已有大量可复用资产，不应重写。

## 7.1 必须继承

```text
交易门控 gate_decision
ALLOW_ENTRY_TRADING / ALLOW_EXIT_TRADING / ALLOW_HEDGE_TRADING
KILL_NEW_RISK / EMERGENCY_REDUCE_ONLY
命令幂等
菜单构建基础
期权链读取
Delta 选腿
保护腿匹配
S:PM 模拟
VRP gate
计划菜单与编号
确认码硬授权
预提交复核结构
entry campaign
保护腿优先
部分成交处理框架
PositionSnapshot
启动恢复框架
position_reconcile
take-profit evaluator
risk exit budget
unified_action_arbiter
hedge_target_contracts
structure_net_delta
hedge venue config
orphan hedge cleanup
审计显示面板
```

## 7.2 必须改造

```text
SIGNAL_STATE
SIGNAL_CONFIDENCE
DIRECTION_BIAS
SIGNAL_SOURCE
_build_menu 的方向来源
_plan_round 的准入条件
审批快照中的信号字段
持仓快照中的 signal_package_id 字段
hedge_watch 对 signal_evidence 的依赖
```

## 7.3 可废弃

```text
FILE / G signal source
SignalEvidencePackage 消费器
side_hint 映射
signal package TTL
signal package 一次性消费
signal schema prefix 检查
持仓后 EDB/GGR 自动输入
```

---

# 8. 执行层参数收口

## 8.1 方向

保留：

```python
DIRECTION_BIAS
```

但注释改为：

```text
由人工审计后填写；不是机器信号输入。
```

## 8.2 信号强度

建议废弃或弱化：

```python
SIGNAL_CONFIDENCE
```

原因：
当前它会影响偏好 Delta。高弃权人工策略中，信号强度不应自动让执行层承担更高短腿 Delta。

建议改为：

```python
MANUAL_RISK_PROFILE = "CONSERVATIVE" / "STANDARD"
```

或第一版直接固定：

```text
SHORT_DELTA_RANGE = 人工配置
不再自动用 confidence 调整偏好
```

## 8.3 信号状态

废弃：

```python
SIGNAL_STATE = "TRADE_SUPPORT_WEAK"
```

替换为：

```python
MANUAL_PLANNING_ALLOWED = False
```

如果仍需显示：

```python
MANUAL_AUDIT_STATE = "APPROVED_FOR_PLANNING" / "REJECTED" / "DEFER"
```

## 8.4 审计来源

新增：

```python
MANUAL_AUDIT_CARD_ID = ""
MANUAL_AUDIT_NOTE = ""
MANUAL_CONTEXT_TTL_MIN = 30
```

---

# 9. 计划轮调整

## 9.1 `_build_menu()` 输入

从：

```python
_build_menu(now_ms, spot)
```

改为：

```python
_build_menu(now_ms, spot, manual_context)
```

方向：

```python
want_call = legsel_is_call_bias(manual_context["direction_bias"])
```

不要再从静态信号字段或 signal package 读。

## 9.2 Delta 偏好

第一版建议：

```text
只用人工配置的 SHORT_DELTA_RANGE
不再用 SIGNAL_CONFIDENCE 自动偏移
```

若必须排序，可设：

```text
preferred_delta = range midpoint
```

例如：

```python
pref = (dmin + dmax) / 2
```

## 9.3 计划菜单必须显示人工上下文

每个菜单和日志显示：

```text
manual_context_id
audit_card_id
direction_bias
dte_scope
delta_scope
amount
expires_at
```

---

# 10. 审批快照调整

## 10.1 PlanApprovalSnapshot

审批快照应绑定：

```json
{
  "manual_context_id": "...",
  "manual_context_hash": "...",
  "audit_card_id": "BTC #4501",
  "operator_note": "...",
  "direction_bias": "SHORT_CALL",
  "plan_hash": "...",
  "config_hash": "...",
  "code_version": "3.0.0-manual-gate",
  "approval_ts_ms": 0,
  "approval_expires_ts_ms": 0,
  "max_loss": 0.0,
  "min_net_credit": 0.0,
  "exit_policy": {},
  "hedge_policy": {}
}
```

## 10.2 禁止人工改腿

如果人工想换腿：

```text
重新生成菜单
重新选择方案
重新生成 plan_hash
重新批准
```

不得直接改订单参数。

---

# 11. 持仓快照调整

PositionSnapshot 必须保存：

```text
manual_context_id
audit_card_id
direction_bias
entry_risk_anchor
plan_hash
approval_id
```

不再保存：

```text
signal_package_id
signal_package_hash
edb_score
side_hint
```

如果历史兼容字段还存在，标记为：

```text
legacy_signal_fields_ignored = true
```

---

# 12. 对冲模块收口

对冲触发不消费信号层。

## 12.1 主输入

```text
EntryRiskAnchor
current short delta
current protection delta
current IV
current DTE
current spot
loss boundary
current structure net delta
exit friction
future hedge friction
```

## 12.2 风险恶化

用：

```text
touch_probability_now
touch_probability_drift
recent_slope
delta_drift
boundary_progress
```

## 12.3 信号层不参与

删除或禁用：

```text
watch_position(... signal_evidence ...)
edb adverse
ggr adverse from signal package
```

如果执行层自己读取某些公共数据用于风险判断，必须独立命名为：

```text
ExecutionRiskContext
```

不得称为信号消费。

## 12.4 对冲目标

继续复用：

```text
structure_net_delta()
hedge_target_contracts()
HEDGE_REDUCTION_RATIO = 0.5
```

但实盘前必须修复：

```text
hedge_order_action 使用 abs(current_qty), abs(target_qty)
```

改为有符号对账。

---

# 13. 执行层独立审计面板

干净执行层仍需要自己的审计，但审计对象是执行事实，不是市场信号。

## 13.1 计划审计

```text
方向来自人工
DTE 范围
Delta 范围
候选数量
过滤漏斗
VRP 通过/阻断
S:PM
建仓可行性
预算
```

## 13.2 建仓审计

```text
保护腿成交
短腿成交
部分成交
累计 credit
订单状态
撤单状态
未知订单
```

## 13.3 持仓审计

```text
entry risk anchor
当前触界概率
Delta drift
Gamma ratio
止盈捕获率
退出预算
对冲目标
当前对冲仓位
孤儿对冲
```

## 13.4 恢复审计

```text
本地快照
交易所真实持仓
活动订单
未知订单
recovery verdict
是否允许新增风险
```

---

# 14. 状态机

```text
BOOT
  ↓
RECOVERY_CHECK
  ├─ UNKNOWN → RECOVERY_BLOCKED
  └─ OK
       ↓
WAIT_MANUAL_AUDIT_GATE
  ├─ MANUAL_PLANNING_ALLOWED=False → HOLD
  └─ True
       ↓
MANUAL_CONTEXT_VALIDATION
  ├─ FAIL → WAIT_MANUAL_AUDIT_GATE
  └─ PASS
       ↓
PLAN_BUILDING
  ↓
PLAN_MENU_READY
  ↓
HARD_APPROVAL_WAIT
  ├─ REJECT → WAIT_MANUAL_AUDIT_GATE
  ├─ EXPIRE → WAIT_MANUAL_AUDIT_GATE
  └─ APPROVE
       ↓
PRECOMMIT_RECHECK
  ├─ FAIL → APPROVAL_INVALIDATED
  └─ PASS
       ↓
ENTRY_CAMPAIGN
  ↓
POSITION_MANAGE
  ↓
CLOSED
```

---

# 15. 关键不变量

```text
MG-01：执行层不读取 signal evidence 文件或 G key
MG-02：方向只来自 ManualExecutionContext
MG-03：没有 MANUAL_PLANNING_ALLOWED，不生成可批准菜单
MG-04：人工上下文过期后，菜单和批准失效
MG-05：人工上下文只允许进入计划，不等于下单授权
MG-06：具体方案仍需二次人工批准
MG-07：人工批准不能覆盖 VRP/S:PM/预算/恢复/报价硬门
MG-08：信号层失联不影响已有持仓退出和对冲
MG-09：持仓后对冲触发不消费信号层
MG-10：所有真实仓位必须绑定 audit_card_id 或 operator_note
MG-11：执行层不得自动提高 Delta 以匹配信号强度
MG-12：默认交易门继续全 False
```

---

# 16. P0 修复仍然必须保留

干净版不代表可以忽略执行安全问题。

以下仍是实盘前硬要求：

```text
部分成交不得留下裸短腿
私有读取三态化
订单 UNKNOWN 不得重复下单
动态 tick_size_steps
qty step / min trade
累计真实 credit floor
保护腿成交后短腿前重新报价
ENTRY ⇒ EXIT policy
有符号对冲目标
```

这些与是否消费信号层无关。

---

# 17. 测试要求

## 17.1 信号隔离测试

```text
没有 signal_evidence.json
G key 不存在
signal_receiver 不可用
信号层停止运行
```

预期：

```text
执行层可启动
等待人工桥
不可自动计划
已有持仓可继续管理
```

## 17.2 人工上下文测试

```text
缺方向
非法方向
TTL 过期
audit_card_id 缺失
MANUAL_PLANNING_ALLOWED=False
MANUAL_PLANNING_ALLOWED=True
```

## 17.3 计划测试

```text
SHORT_CALL 只枚举 Call Credit Spread
SHORT_PUT 只枚举 Put Credit Spread
Delta 范围生效
DTE 范围生效
腿宽范围生效
```

## 17.4 审批测试

```text
context 变化后旧 approval 失效
config 变化后旧 approval 失效
plan_hash 变化后旧 approval 失效
```

## 17.5 持仓管理测试

```text
信号层完全缺失
止盈仍可判断
风险退出仍可判断
对冲仍可判断
孤儿对冲仍可清理
```

---

# 18. Agent 实施步骤

## Step 1：复制当前执行层为新干净分支/目录

不要直接破坏旧执行层。

## Step 2：删除或断开信号消费

确保主链不调用：

```text
receive_signal
read SignalEvidencePackage
side_hint mapping
FILE / G source
```

## Step 3：新增 ManualExecutionContext

从 FMZ 参数或命令生成。

## Step 4：重写计划入口

必须先通过人工上下文验证。

## Step 5：复用并清理计划菜单

保留选腿、S:PM、VRP、可行性、预算逻辑。

## Step 6：重写审批快照

绑定 manual context，而不是 signal package。

## Step 7：重写持仓快照

保存 audit reference 和 EntryRiskAnchor。

## Step 8：剥离对冲对信号层依赖

对冲只读本地风险和执行数据。

## Step 9：跑 P0 执行安全测试

不得因为是干净版而跳过。

## Step 10：构建 FMZ 单文件

默认：

```text
MANUAL_PLANNING_ALLOWED=False
ALLOW_ENTRY_TRADING=False
ALLOW_EXIT_TRADING=False
ALLOW_HEDGE_TRADING=False
```

---

# 19. Agent 交付物

必须提交：

1. 新目录或新文件名；
2. 新版本号；
3. 删除/禁用信号消费的证明；
4. ManualExecutionContext schema；
5. FMZ 参数说明；
6. 新状态机；
7. 继承资产清单；
8. 删除资产清单；
9. 计划轮测试；
10. 审批测试；
11. 对冲本地风险测试；
12. P0 安全测试；
13. bundle 构建结果；
14. 默认门控确认；
15. 尚未解决限制。

最终状态只能标记为：

```text
MANUAL_GATE_PLAN_READY
MANUAL_GATE_DRY_RUN_READY
MANUAL_GATE_TESTNET_READY
```

不得标记：

```text
AUTO_SIGNAL_EXECUTION_READY
```

---

# 20. 最终实施建议

本轮应该把执行层定义成：

```text
人工桥接的独立执行系统
```

而不是：

```text
信号层的自动下游
```

执行层最小输入：

```text
方向 + 计划范围 + 人工审计来源
```

执行层最大职责：

```text
安全地把一个人工批准的机会，转化为可审计、可退出、可对冲、可恢复的有限风险仓位
```

这会让执行层更简洁、更可测试、更不容易被信号层字段变化污染，也更符合个人投资者高弃权策略的优势。
