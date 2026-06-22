# 执行层 HANDOFF（下一窗口接手指南）

> 截至：2026-06-22，执行层 **STRATEGY_VERSION 2.7.0**，`tests/run_all.py` **215 passed, 0 failed**。**里程碑 = `DRY_RUN_CANDIDATE`（非可实盘）。**
> 本文件 + 记忆 `execution-v2-seal` + `docs/执行层完整说明_v2.1.md`(完整说明含审计 §13/§14) + `docs/执行层重构_v3_进度.md`(逐步日志) 构成完整上下文。
> **R1 风险→仲裁(v2.4.0) + R2 风险链路审计整改(v2.5.0) + R3 外部审计 Tier0(v2.6.0：裸短腿 INV-03 + 读取三态 INV-04/10) + R4 T0 扩展(v2.7.0：恢复默认 fail-closed + 信号方向权威守门) 已完成。已采纳外部审计文档为"开真闸门清单 + INV-01..12"。下一步：①正式端到端空跑前补 T0-C 全版(side_hint 驱动方向)+T0-D(VRP 可注入)+SyntheticFillAdapter；②小额实盘前做 Tier1（统一真相/VRP+预算入库/有符号对冲/T0-A 全版+T0-LIVE/NewRiskApprovalPackage 捆绑降险授权）。详见 §4。**

## 1. 现状（已完成）

v3 重构全链路落地：E0 删日历(垂直唯一) → E1 门控拆分/命令幂等/信号接收/交互控制台 → E2 推荐库+短确认码硬授权+单一 `run_cycle` 主链 → E3 受控真实开仓+13 项预提交+投影预算 → E4 管理循环+四输出仲裁+启动恢复 → E5 软授权 → E6 止盈资格/低成本退出 → E7 BTC-PERPETUAL 对冲生命周期 → E8 交付。
F1 对冲场所可选(Binance USDC maker-0)；G1 开仓活动 entry_campaign(跨轮持久 maker+信用底线)；**H1 持仓后链路补强 P0①②③**(统一持仓真相到 `_POSITION_KEY` + 保护腿回收/CLOSED + 仲裁单动作收口+退出期禁新增对冲)；**R1 风险严重度→仲裁 (v2.4.0)**(入场冻 `entry_risk_anchor` → manage 每轮 `hedge_risk.evaluate_position_risk` → `tail_risk_state` 驱动 `exit_preferred/hedge_ready`，替换两个 False hook；对冲数量改结构净 delta + 方向符号核对)；**R2 风险链路审计整改 (v2.5.0)**(F1 风险退出独立预算+可越价吃单+不可成交回退对冲；F2 控制台风险行/风险退出码/提示；F3 盘口缺数据显式缺口；C1 对冲成交确认；C2 孤儿清理免门；C3 真实活动订单查询 fail-closed + 启动恢复重校验)。
**R3 外部审计 Tier0 (v2.6.0)**(INV-03 部分短腿成交放弃→冻结 1:1 覆盖、绝不留裸短腿；INV-04/10 `dbt_get_positions` 读失败→None、`startup_recovery_check` 持仓/订单未知→DATA_UNKNOWN 禁开新仓)；**R4 T0 扩展 (v2.7.0)**(T0-B `_recovery_verdict` 默认 fail-closed=RECOVERY_NOT_CHECKED；T0-C `_signal_allows_entry` FILE/G 下 side_hint 须与 DIRECTION_BIAS 一致否则 fail-closed)。
《docs/补充意见.txt》P0-1~P0-6 + P1 已全部落地；外部审计文档 v1.0 已采纳为开真闸门清单。**默认全空跑、非"策略已验证"。**

## 2. 架构速查

- 基座 `demo/execution_build/realsrc/src/`（改这里）→ `python build_bundle.py [--check]` 合成 → 覆盖交付物 `demo/最新交付物/spm_calendar_protected_short_v1.py`。
- 主链 `strategy.main → run_cycle`(每轮)：`GetCommand`→`cmd_router`(幂等)→`signal_receiver`(降级)→`gates`(5门)→相位分支(recovery/has_pos→`manage_cycle`/kill/locked→`_attempt_commit`(entry_campaign)/维护推荐库)→`display.disp_console_table`(置顶)。
- 持仓后 `manage_cycle`：对账(`position_reconcile`)+止盈资格(`_evaluate_take_profit`)+退出活动(`exit_campaign_decision`+`exec_exit_buyback_step`)+对冲(`_evaluate_hedge`+`exec_hedge_step`)+保护腿回收(`exec_protection_recovery_step`)+CLOSED(`_archive_closed`)，由 `unified_action_arbiter` 单动作收口。
- 持仓真相 = `_POSITION_KEY` 的 `VerticalEntrySnapshot`（含 remaining_short_qty / long_remaining_qty / entry_profit_ceiling_net 等）。
- 纯函数模块：`gates / cmd_router / signal_receiver / recommend / position / authorization / hedge`；适配 `deribit_io / binance_io`；执行 `execution`；记账/对账 `ledger / accounting`；风控 `risk_controls`(投影预算+四输出仲裁)；展示 `display`。
- 验证：`python demo/execution_build/realsrc/tests/run_all.py`（196）+ `python build_bundle.py --check`（语法+名称解析+无 KPF/无日历）。

## 3. ✅ 已完成（v2.4.0 接入 + v2.5.0 审计整改）：风险严重度 → 仲裁

**v2.4.0 落地**：入场 `_attempt_commit` 冻结快照时经 `_build_entry_risk_anchor` 冻 `entry_risk_anchor`(+`short_expiry_ts`)；`manage_cycle` 每轮 `_evaluate_position_risk_now`(无快照/无锚→None) 调 `hedge_risk.evaluate_position_risk` → `tail_risk_state` 映射 `exit_preferred(EXIT_PREFERRED)/hedge_ready(HEDGE_READY)`，替换两个 False hook；退出活动触发改 `exit_trigger = 止盈资格 ∨ exit_preferred`，保持 P0③ 单动作收口 + 退出期禁新增对冲；对冲数量改 `hedge.structure_net_delta(短−保护)` + `hedge.hedge_direction_consistent` 方向符号核对。`evaluate_position_risk` 已接回 v3 链（**E8.1 勿删**）。

**v2.5.0 审计整改（F1-F3/C1-C3，`run_all.py` 210 passed）**：
- **F1 风险退出可成交**：`_risk_exit_budget_cap`(用风险退出授权 `max_exit_spend`=`RISK_EXIT_MAX_SPEND` 反推、独立止盈缓冲、判 within=ask≤cap)；`exec_exit_buyback_step(allow_taker=True)` 风险退出**可越价吃单**(限价=cap、成本硬封)；越价仍不可成交(within False) → `exit_executable` False → **仲裁回退对冲**(`risk_exit_unsatisfiable` 放行)。
- **F2 可观测/可操作**：控制台「风险」行(`disp_risk_line`)、未授权时显示**风险退出码** + 操作提示引导【风险退出授权】。
- **F3**：短腿盘口缺 delta∧IV → `market_data_gap`(risk_state=None，不静默 NORMAL)。
- **C1**：`exec_hedge_step` Deribit None 盘口守门 + 等待 + 撤残单 + 成交确认。
- **C2**：孤儿对冲(裸 perp)清理 `orphan_cleanup` 绕过 `ALLOW_HEDGE_TRADING` 门。
- **C3**：`dbt_get_open_orders` + `_no_unknown_orders`(fail-closed) 接入 `no_unknown_orders`；`startup_recovery_check` 无快照时用在途活动 prog + 真实活动订单重校验。

**残留（已记入 §4/完整说明）**：HEDGE_READY 待总线 edb/ggr（OFFLINE persistence 恒 LOW，仅 EXIT_PREFERRED 活跃）；`RISK_EXIT_MAX_SPEND` 占位(=0 时风险退出受阻→回退对冲)；C4 reconcile 身份不符 surfaced-不阻断；C5 manage 单轮多次取价未缓存。

<details><summary>原任务说明（存档，便于复核设计意图）</summary>

**问题**：`strategy.manage_cycle` 调 `unified_action_arbiter` 时 `exit_preferred / hedge_ready` **硬编码 False**（注释「风险严重度 hook(P1)」）→ 风险恶化时的**主动**退出/对冲从不发生，目前仅 `take_profit_ready` 资格 + `orphan` 驱动动作。

**目标**：把风险严重度接入仲裁，让 EXIT_PREFERRED / HEDGE_READY 在风险恶化时真正触发（设计稿 §9.2 优先级：…EXIT_PREFERRED > HEDGE_READY > TAKE_PROFIT_READY…）。

**已有可复用资产**（当前 off v3 路径，本任务把它们接回）：
- `hedge_risk.evaluate_position_risk(...)` → `PositionRiskPackage`（触界概率/漂移/尾部加速/持续性/breached/next_action）。**先读 `hedge_risk.py` + `hedge_watch.py` 确认签名与输出字段。**
- `hedge_risk.build_entry_risk_anchor(...)` → 入场风险锚。
- `hedge_watch.watch_position(position_id, direction_bias, short_record, current_market, ...)` 封装了 anchor+market→evaluate_position_risk。

**落地步骤（建议）**：
1. **入场冻结风险锚**：`_attempt_commit` 完成(冻结快照)时，调 `build_entry_risk_anchor(...)` 把 `entry_risk_anchor` 存入 `VerticalEntrySnapshot`（参数：direction_bias/side、spot、dte、short delta/gamma、mark_iv、breakeven、signal_state）。`position.build_vertical_entry_snapshot` 加该字段。
2. **manage 每轮算风险裁决**：在 `manage_cycle` 取 `snap.entry_risk_anchor` + 当前市场(`exec_quote(short)` 的 mark/delta/gamma/iv + `_spot_price()` + 剩余 DTE) → 调 `evaluate_position_risk`(或 `watch_position`) → 风险包。
3. **映射到仲裁输入**：据风险包把 `exit_preferred` / `hedge_ready` 置真（参考其 `next_action` / severity / persistence；EXIT_PREFERRED=风险严重且期权退出可接受；HEDGE_READY=风险严重持续且期权退出更差）。替换两个 False hook。
4. **复用 §9.2 / hedge.py 的对冲数量**：对冲建仓走 `_evaluate_hedge` 的 target/action（注意 P0③：退出活动期仍只许 reduce）。
5. **测试**：构造一个"风险严重"的市场(短腿 delta/触界概率高) → 断言 `arb.preferred/executable` 为 EXIT_PREFERRED 或 HEDGE_READY，并按单动作收口执行对应动作；构造"风险温和" → HOLD/TAKE_PROFIT。新增 `test_run_cycle` 用例（门控开+monkeypatch 执行器验证落单方向）。
6. **顺带修 P1**：对冲数量用**结构净 delta(short−protection)** 而非仅短腿 delta（`_evaluate_hedge`）；对冲 open/reduce 加方向符号核对。

**注意**：`evaluate_position_risk` 是 legacy（写时用 anchor schema），接回前务必读其真实签名/字段，不要照搬本文档假设的字段名。

</details>

## 4. 其余残留（按优先级；详见 完整说明 §13/§14）

0. **（端到端空跑前必做，阶段 A 收尾）**：T0-C 完整版——`side_hint` **驱动**方向(`ExecutionSignalContext`)而非仅"与 DIRECTION_BIAS 一致才放行"(v2.7.0 已做不一致即阻断的最小守门)；T0-D——VRP **可注入**接口/测试 fixture（禁生产硬写 `vrp_pass=True`），让预提交能真正跑到 entry campaign；`SyntheticFillAdapter`——仅替换成交回报、不碰真实私有写口，用于跑通 §11.1 entry campaign 场景矩阵。
1. **（新头号·阶段 B/总线）总线模式接入**：信号侧落 `SignalEvidencePackage`（含 `direction_evidence.edb` + `pre_trade_context.ggr` + IV/RV market_context），执行侧 `signal_receiver` 已就位(默认 OFFLINE_MANUAL)。接入后**一举解锁两项**：①`_build_precommit_live` 的 `vrp_pass`（OFFLINE 恒 None/fail-closed）→ 经 `vrp_gate.apply_vrp_gate` 放行；②把 edb/ggr 喂给 `_evaluate_position_risk_now`(当前传 None) → persistence 升到 MEDIUM/HIGH → **HEDGE_READY 真正可达**（目前仅 EXIT_PREFERRED 活跃）。`hedge_watch.watch_position` 已封装「anchor + signal_evidence → evaluate_position_risk」，可直接复用。T0-C/T0-D 在此一并落全版。
2. 信号→执行总线 spike（落地 §4.1 的传输面）：信号侧调 `demo/signal_build/signal_bridge.export_signal_evidence_package` 落盘 + 原子 rename + 同托管 loopback；执行侧 receiver 已就位(默认 OFFLINE_MANUAL)。
3. ~~`no_unknown_orders` 预提交桩~~ **（v2.5.0 C3① 已接真实 `dbt_get_open_orders` + fail-closed）**。
4. ~~重启在途 campaign 未按成交重校验~~ **（v2.5.0 C3② 已按在途活动 prog + 真实活动订单重校验；entry_campaign 放弃回退失败的残值态仍未显式管理）**。
5. ~~entry_campaign 放弃留裸短腿~~ **（v2.6.0 INV-03 已修：部分短腿→冻结 1:1 覆盖、只退多余保护）**。残留：①门关时多余保护卖不掉 → 未跟踪 long 残值态（仅成本、非裸卖；reconcile surfaced）；②**T0-A 完整版（真实资金前）**：放弃冻结当前用本地 `prog` 计数，应改为**重读交易所真实腿量 + 确认订单终态**后再冻结（与下 T0-LIVE 同批）。
5b. **T0-LIVE（真实资金前）**：`_post_maker_once`/对冲下单未持久 order_id、无 `ORDER_STATE_UNKNOWN`——提交结果未知(响应丢失/撤单状态未知)时下一轮可能重复下单；`_no_unknown_orders` 只挡未知 label、不挡我方 `entry_*` 仍活动单。须：任何提交结果未知 → campaign 进 ORDER_STATE_UNKNOWN → 禁重复下单 → 查证到确定终态再继续。**纯空跑不触发**（不实际下单）。
6. `gex_info` 增强并入 `realsrc/src`（仅最新交付物 spm 单文件有，shadow-only、可降级）。
7. 旧整合层清理（off v3 路径，仅测试/bundle-smoke 引用）：`_plan_round/_run_order/_order_loop/integrated_plan_preview`、`session_core.ExecutionSession`、`vrp_gate.apply_vrp_gate`、`risk_controls.evaluate_portfolio_budget/decide_position_manage/build_attribution`、`hedge_watch.watch_position`（注意：`hedge_risk.evaluate_position_risk/build_entry_risk_anchor` v2.4.0 已接回 v3 链，**不要删**）。
8. 阈值标定：`PORTFOLIO_LIMITS / ENTRY_MIN_NET_CREDIT / HEDGE_REDUCTION_RATIO / EXIT_RESERVE_RATIO / RISK_EXIT_MAX_SPEND` 均占位（`RISK_EXIT_MAX_SPEND` v2.5.0 起为风险退出**独立预算**的真实消费者——=0 时风险退出受阻即回退对冲，须标定为可接受的最大止损成本）。
9. C4 reconcile 身份不符仅 surfaced-不阻断（设计取舍，未改）；C5 manage 单轮短腿盘口被多处分别取价、未缓存（效率）。
10. **上线前**：真实 FMZ 机器人空跑验收（GetCommand 回测不生效）；逐门开真（特别验风险退出：设 `RISK_EXIT_MAX_SPEND`>0 + `授权风险退出` + 越价吃单/回退对冲两条路径）。

## 5. 工作约定 / 易错点（务必遵守）

- **改 src → 跑 `tests/run_all.py`(须全绿) → `build_bundle.py --check` → 覆盖交付物 + `demo/副本快照/<日期>_<特性>/` 留快照 → bump `config.STRATEGY_VERSION`**。每阶段更新：完整说明_v2.1.md + 重构_v3_进度.md + README + 记忆。
- **新模块**：必须加入 `build_bundle.py` 的 `MODULE_ORDER`（在 `strategy` 之前；被依赖者在前），否则 bundle 不含/不剥离其 import。
- **FMZ 扁平命名空间约定**：src 用 `from X import name`；bundle 剥离项目内 import、所有模块拼进**单一命名空间**。故：①名字须全局唯一；②**禁用** `import X` + `X.fn()`（bundle 解析失败）；③**禁用别名** `from X import a as b`（bundle 无 b）；④被引用名须在 strategy 之前定义。
- **门控默认全 False（空跑）**：`ALLOW_ENTRY/EXIT/HEDGE_TRADING`、`KILL_NEW_RISK`、`EMERGENCY_REDUCE_ONLY`；`HEDGE_VENUE=DERIBIT`；`SIGNAL_SOURCE=OFFLINE_MANUAL`。真实下单路径默认不触发——测试真实成交用 monkeypatch `ST.exec_*`（见 `test_run_cycle`）。
- **测试模式**：纯函数优先单测；集成用 `test_run_cycle._setup`（清 `fmz_shim._STORE`+`_commands`、设 `io_handler`）。Mock 行情 `S48` 仅含 strike 74000–80000 @ 48h；**用不在表内的合约会让 `_quote` KeyError**。命令幂等键含 `refresh_seq`；消费型命令一次性消费。
- **持仓真相唯一**：一律读写 `_POSITION_KEY` 快照，**勿**回退用 legacy `led["short"]/led["protection"]`（reconcile/recovery 已改读快照）。
- **信号层**：独立维护(README 标 v1.2.1，schema `nrd.schema.v1.0.0`，read_only 观察版)，**非本任务重点**；执行层经 `signal_receiver` 消费(总线)或静态 `SIGNAL_STATE`(OFFLINE_MANUAL)。`allow_downstream_evaluation` 存在于信号输出(`neutral_regulation_demo_fmz.py:7063`)。
