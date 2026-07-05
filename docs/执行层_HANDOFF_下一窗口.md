# 执行层 HANDOFF（下一窗口接手指南）

> 截至：2026-06-26，执行层 **STRATEGY_VERSION 2.9.9**，`tests/run_all.py` **264 passed, 0 failed**，`build_bundle.py --check` 通过，最新交付物已覆盖为 v2.9.9。
> 若下一窗口承接“执行层大方向调整”，请优先使用 `docs/执行层_v2.9.9_大方向调整_HANDOFF包_20260626.md` 作为开场入口；本文件保留 v2.9.9 历史推进线和模块细节。
> 因用户后续将做执行层大方向调整，当前停止继续推进新逻辑；后续接手请先读 `docs/执行层_v2.9.9_当前资产清单_暂停点.md`，再读本文件 + `docs/执行层完整说明_v2.1.md` + `docs/执行层重构_v3_进度.md`。
> **v2.9.1 增量**：在 v2.9.0 执行可行性闭环基础上，开仓活动放弃时若保护腿已成交但短腿未建成，未能卖回的保护腿会写入 `_POSITION_KEY` 并进入 `SHORT_FLAT_LONG_RESIDUAL` 管理/回收链路。默认交易门仍全关，VRP 缺 IV/RV `market_context` 仍 fail-closed。
> **v2.9.2 增量**：风险退出授权命令路径补齐预算闭环；`RISK_EXIT_MAX_SPEND=0` 时只使用入场冻结的 `max_total_exit_spend`，不额外放大止损额度，也不再让风险退出路径因占位 0 预算必然死掉。
> **v2.9.3 增量**：持仓管理轮创建本轮 `_quote_cache()`，止盈/对冲/风险评估/风险退出/保护腿回收共用同一轮期权 quote，避免模块间读到漂移盘口并减少重复 ticker API。
> **v2.9.4 增量**：补齐 reviewer 发现的执行器二次取价缺口；风险退出买回和保护腿回收执行器接收本轮 quote，缺 quote 不缓存以便同轮后续重试。
> **v2.9.5 增量**：补齐执行侧 L1 计划闭环两个关键守卫：FILE/G 信号血缘或上下文失效时自动清锁并重建推荐库，已消费总线信号不会在平仓后重复开仓；持仓管理期发现相关活动订单时进入 `MANAGE_IN_FLIGHT`，避免同轮叠加退出/对冲/回收动作。状态栏同步显示拆分动作门控和相关活动订单。
> **v2.9.6 增量**：批准 TTL 接入 `run_cycle` 主链，锁定方案超时自动清锁并重建/等待新推荐；配置自检新增 `DRY_RUN_PASSED=False` live-readiness 硬门，任何真实交易门打开前必须先完成真机空跑与只读核验，同时校验退出路径、风险预算和关键阈值合法性。
> **v2.9.7 增量**：计划轮在 FILE/G 信号带完整 `market_context` 时应用 VRP 预过滤，预提交必被 `vrp_rechecked` 拦截的候选不再进入确认码推荐库；缺 IV/RV 上下文仍不伪造通过，预提交继续 fail-closed。
> **v2.9.8 增量**：修复 reviewer 发现的 v2.9.7 阻断缺口；完整 `market_context` 下 VRP 全阻断或 VRP 计算异常时，计划轮清空旧推荐库并 fail-closed，不再展示旧确认码。
> **v2.9.9 增量**：补 FILE 双包真实入口回归；`entry_candidate_latest.json` 会读取同目录 `context_latest.json`，且 `SignalContextUpdate` 缺 `episode_id/source_snapshot_hash` 时 fail-closed；已补 FILE 双包缺 hash 负向集成测试。
> **DRY_RUN 红线**：本地测试和 bundle check 通过不等于 FMZ 真机空跑通过；正确确认码链路和交易所只读核验完成前，不得标记 `DRY_RUN_PASSED`。

## 1. 现状（已完成）

v3 重构全链路落地：E0 删日历(垂直唯一) → E1 门控拆分/命令幂等/信号接收/交互控制台 → E2 推荐库+短确认码硬授权+单一 `run_cycle` 主链 → E3 受控真实开仓+14 项预提交+投影预算 → E4 管理循环+四输出仲裁+启动恢复 → E5 软授权 → E6 止盈资格/低成本退出 → E7 BTC-PERPETUAL 对冲生命周期 → E8 交付。
F1 对冲场所可选(Binance USDC maker-0)；G1 开仓活动 entry_campaign(跨轮持久 maker+信用底线)；**H1 持仓后链路补强 P0①②③**(统一持仓真相到 `_POSITION_KEY` + 保护腿回收/CLOSED + 仲裁单动作收口+退出期禁新增对冲)；**R1 风险严重度→仲裁 (v2.4.0)**(入场冻 `entry_risk_anchor` → manage 每轮 `hedge_risk.evaluate_position_risk` → `tail_risk_state` 驱动 `exit_preferred/hedge_ready`，替换两个 False hook；对冲数量改结构净 delta + 方向符号核对)；**R2 风险链路审计整改 (v2.5.0)**(F1 风险退出独立预算+可越价吃单+不可成交回退对冲；F2 控制台风险行/风险退出码/提示；F3 盘口缺数据显式缺口；C1 对冲成交确认；C2 孤儿清理免门；C3 真实活动订单查询 fail-closed + 启动恢复重校验)。
《docs/补充意见.txt》P0-1~P0-6 + P1 已全部落地。**默认全空跑、非"策略已验证"。**

## 2. 架构速查

- 基座 `demo/execution_build/realsrc/src/`（改这里）→ `python build_bundle.py [--check]` 合成 → 覆盖交付物 `demo/最新交付物/spm_calendar_protected_short_v1.py`。
- 主链 `strategy.main → run_cycle`(每轮)：`GetCommand`→`cmd_router`(幂等)→`signal_receiver`(降级)→`gates`(5门)→相位分支(recovery/has_pos→`manage_cycle`/kill/locked→`_attempt_commit`(entry_campaign)/维护推荐库)→`display.disp_console_table`(置顶)。
- 持仓后 `manage_cycle`：对账(`position_reconcile`)+止盈资格(`_evaluate_take_profit`)+退出活动(`exit_campaign_decision`+`exec_exit_buyback_step`)+对冲(`_evaluate_hedge`+`exec_hedge_step`)+保护腿回收(`exec_protection_recovery_step`)+CLOSED(`_archive_closed`)，由 `unified_action_arbiter` 单动作收口。
- 持仓真相 = `_POSITION_KEY` 的 `VerticalEntrySnapshot`（含 remaining_short_qty / long_remaining_qty / entry_profit_ceiling_net 等）。
- 纯函数模块：`gates / cmd_router / signal_receiver / recommend / position / authorization / hedge`；适配 `deribit_io / binance_io`；执行 `execution`；记账/对账 `ledger / accounting`；风控 `risk_controls`(投影预算+四输出仲裁)；展示 `display`。
- 验证：`python demo/execution_build/realsrc/tests/run_all.py`（当前 264）+ `python build_bundle.py --check`（语法+名称解析+无 KPF/无日历）。

## 3. ✅ 已完成（v2.4.0 接入 + v2.5.0 审计整改）：风险严重度 → 仲裁

**v2.4.0 落地**：入场 `_attempt_commit` 冻结快照时经 `_build_entry_risk_anchor` 冻 `entry_risk_anchor`(+`short_expiry_ts`)；`manage_cycle` 每轮 `_evaluate_position_risk_now`(无快照/无锚→None) 调 `hedge_risk.evaluate_position_risk` → `tail_risk_state` 映射 `exit_preferred(EXIT_PREFERRED)/hedge_ready(HEDGE_READY)`，替换两个 False hook；退出活动触发改 `exit_trigger = 止盈资格 ∨ exit_preferred`，保持 P0③ 单动作收口 + 退出期禁新增对冲；对冲数量改 `hedge.structure_net_delta(短−保护)` + `hedge.hedge_direction_consistent` 方向符号核对。`evaluate_position_risk` 已接回 v3 链（**E8.1 勿删**）。

**v2.5.0 审计整改（F1-F3/C1-C3，`run_all.py` 210 passed）**：
- **F1 风险退出可成交**：`_risk_exit_budget_cap`(用风险退出授权 `max_exit_spend` 反推；`RISK_EXIT_MAX_SPEND>0` 用全局风险预算，=0 则只用入场冻结退出预算；判 within=ask≤cap)；`exec_exit_buyback_step(allow_taker=True)` 风险退出**可越价吃单**(限价=cap、成本硬封)；越价仍不可成交(within False) → `exit_executable` False → **仲裁回退对冲**(`risk_exit_unsatisfiable` 放行)。
- **F2 可观测/可操作**：控制台「风险」行(`disp_risk_line`)、未授权时显示**风险退出码** + 操作提示引导【风险退出授权】。
- **F3**：短腿盘口缺 delta∧IV → `market_data_gap`(risk_state=None，不静默 NORMAL)。
- **C1**：`exec_hedge_step` Deribit None 盘口守门 + 等待 + 撤残单 + 成交确认。
- **C2**：孤儿对冲(裸 perp)清理 `orphan_cleanup` 绕过 `ALLOW_HEDGE_TRADING` 门。
- **C3**：`dbt_get_open_orders` + `_no_unknown_orders`(fail-closed) 接入 `no_unknown_orders`；`startup_recovery_check` 无快照时用在途活动 prog + 真实活动订单重校验。

**残留（已记入 §4/完整说明）**：C4 reconcile 身份不符 surfaced-不阻断；`RISK_EXIT_MAX_SPEND>0` 的额外止损额度仍需小仓实盘前标定（默认 0 只用入场冻结退出预算）。C5 manage 单轮多次取价已在 v2.9.4 用本轮 quote cache 关闭到执行器路径。

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

1. **信号→执行闭环剩余项**：v2.9.9 已能从当前信号层 `signal_review.jsonl` 映射方向、数据质量、GGR/blocking、episode/card/hash 血缘；若信号上下文带完整 IV/RV `market_context`，计划轮会先做 VRP 预过滤，预提交会真实复核 `vrp_rechecked`；若完整上下文下 VRP 全阻断或异常，旧推荐库会被清空并 fail-closed。未来 FILE 双包入口已覆盖 `entry_candidate_latest.json` + 同目录 `context_latest.json`，且 context 缺 `episode_id/source_snapshot_hash` 会 fail-closed。持仓后 EDB/GGR 已喂给 `_evaluate_position_risk_now`；执行可行性已进入计划/确认码/预提交链路，保护腿-only 残值态已纳入持仓管理，风险退出授权在默认 0 额外风险预算下仍可使用入场冻结退出预算，同一持仓管理轮复用 quote 快照到执行器。v2.9.5 已补齐执行侧信号血缘失效清锁/重建、已消费 bus 信号不重复开仓、管理期相关活动订单 `MANAGE_IN_FLIGHT`；v2.9.6 补齐批准 TTL 主链和 live-readiness 配置硬门；v2.9.7/v2.9.8 补齐完整上下文下的计划轮 VRP 预过滤与全阻断清库。下一步仍需信号侧原生发布 `EntryCandidatePackage + SignalContextUpdate`，并稳定提供真实 IV/RV 字段；缺字段时 `vrp_rechecked` 继续 fail-closed。
2. **持仓后信号连续性后续**：当前已接 EDB/GGR + 短腿盘口摩擦，足以让 HEDGE_READY 从真实风险包可达；后续可再接更细的 option/future 退出摩擦、recent_history 和持仓 thesis continuity，但不要在没有实盘证据前扩大模型。
3. ~~`no_unknown_orders` 预提交桩~~ **（v2.5.0 C3① 已接真实 `dbt_get_open_orders` + fail-closed）**。
4. ~~重启在途 campaign 未按成交重校验~~ **（v2.5.0 C3② 已按在途活动 prog + 真实活动订单重校验）**。
5. ~~entry_campaign 放弃时回退保护腿失败 → 残值态未显式管理~~ **（v2.9.1 已写入保护腿-only 残值快照并转 `SHORT_FLAT_LONG_RESIDUAL` 管理）**。
6. `gex_info` 增强并入 `realsrc/src`（仅最新交付物 spm 单文件有，shadow-only、可降级）。
7. 旧整合层清理（off v3 路径，仅测试/bundle-smoke 引用）：`_plan_round/_run_order/_order_loop/integrated_plan_preview`、`session_core.ExecutionSession`、`vrp_gate.apply_vrp_gate`、`risk_controls.evaluate_portfolio_budget/decide_position_manage/build_attribution`、`hedge_watch.watch_position`（注意：`hedge_risk.evaluate_position_risk/build_entry_risk_anchor` v2.4.0 已接回 v3 链，**不要删**）。
8. 阈值标定：`PORTFOLIO_LIMITS / ENTRY_MIN_NET_CREDIT / HEDGE_REDUCTION_RATIO / EXIT_RESERVE_RATIO` 仍需小仓前校准；`RISK_EXIT_MAX_SPEND=0` 现在表示“不允许额外止损，只用入场冻结退出预算”，设为正数才允许更高成本风险退出。v2.9.6 已增加配置 sanity check，但这不是经济阈值标定。
9. C4 reconcile 身份不符仅 surfaced-不阻断（设计取舍，未改）。C5 manage 单轮短腿盘口重复取价已在 v2.9.4 关闭；管理期相关活动订单 guard 已在 v2.9.5 关闭。
10. **上线前**：真实 FMZ 机器人空跑验收（GetCommand 回测不生效）；逐门开真（特别验风险退出：默认 `RISK_EXIT_MAX_SPEND=0` 使用冻结预算、`RISK_EXIT_MAX_SPEND>0` 额外风险预算、越价吃单/回退对冲两条路径）。

## 5. 工作约定 / 易错点（务必遵守）

- **改 src → 跑 `tests/run_all.py`(须全绿) → `build_bundle.py --check` → 覆盖交付物 + `demo/副本快照/<日期>_<特性>/` 留快照 → bump `config.STRATEGY_VERSION`**。每阶段更新：完整说明_v2.1.md + 重构_v3_进度.md + README + 记忆。
- **新模块**：必须加入 `build_bundle.py` 的 `MODULE_ORDER`（在 `strategy` 之前；被依赖者在前），否则 bundle 不含/不剥离其 import。
- **FMZ 扁平命名空间约定**：src 用 `from X import name`；bundle 剥离项目内 import、所有模块拼进**单一命名空间**。故：①名字须全局唯一；②**禁用** `import X` + `X.fn()`（bundle 解析失败）；③**禁用别名** `from X import a as b`（bundle 无 b）；④被引用名须在 strategy 之前定义。
- **门控默认全 False（空跑）**：`ALLOW_ENTRY/EXIT/HEDGE_TRADING`、`KILL_NEW_RISK`、`EMERGENCY_REDUCE_ONLY`；`HEDGE_VENUE=DERIBIT`；`SIGNAL_SOURCE=FILE`，默认读 `demo/logs/signal_review.jsonl`。真实下单路径默认不触发——测试真实成交用 monkeypatch `ST.exec_*`（见 `test_run_cycle`）。
- **测试模式**：纯函数优先单测；集成用 `test_run_cycle._setup`（清 `fmz_shim._STORE`+`_commands`、设 `io_handler`）。Mock 行情 `S48` 仅含 strike 74000–80000 @ 48h；**用不在表内的合约会让 `_quote` KeyError**。命令幂等键含 `refresh_seq`；消费型命令一次性消费。
- **持仓真相唯一**：一律读写 `_POSITION_KEY` 快照，**勿**回退用 legacy `led["short"]/led["protection"]`（reconcile/recovery 已改读快照）。
- **信号层**：独立维护(当前交付物 README 标 v1.5.1，schema `nrd.schema.v1.0.0`，read_only 观察版)，**非本任务重点**；执行层 v2.6.0 可直接消费当前 `signal_review.jsonl`，并兼容后续双包总线。静态 `SIGNAL_STATE` 仅用于 OFFLINE_MANUAL，不得作为 FILE/G 方向兜底。
