# Workbench v3 · 实盘执行只读前端

状态：`FRONTEND_READONLY_ACTIVE`。依据：`live-exec-behavior@1.2.0`、LIVE Execution 最小工程实施任务书与工作台只读裁决，对接 `8608a4b9+` 实盘执行协调器与 Workbench 服务端。

## 页面落点

- **状态条**：沿用 `#exec-state` 灯，一句话给出阶段或最高告警（`执行 · 持仓 · 止盈托管`、`执行 · 需人工接管`）。只有新鲜、无告警的真实读数才可能是绿灯。
- **本期保单之后、锁定追踪之前的“实盘执行”**：候选选择 → 真实执行 → 影子追踪 → 卷宗审计，顺着一局的因果读。空闲时只占一行。
- 不新增页面，不设控制入口。旧的逐局批准、确认码、操作员解锁、三键控制和回执全部删除；服务端对应 `POST /api/workbench/execution/*` 已统一返回 `403`，前端仅保留只读查询。

## 信息层级

1. 头行：阶段印章 · 运行模式（实盘真实资金 / 影子 / 演练 / 模拟）· 入场启用（有字段才显示）· 可用资金与维持缓冲 · 风险预算（影子模式全零时省略）· 事实时效。
2. 告警：只在真实风险未解除时出现。包括：停写需接管、订单结果未知、裸空、现金不足（10009，且在 `revoke.cash_wall_10009 === false` 时自动消警）、复审不可用、未归因仓位、状态过期或中断、未挂止盈、退出时缺有效报价、退出限价低于价格带（`EXIT_PRICE_BELOW_EXCHANGE_MIN_BAND`）。
3. 对局四行，沿用本期保单的 `drow` 读法：
   - **AI 牌手**：Accept 时间、最近复核，或 `→ REVOKE`。
   - **执行器**：当前一个动作；若 `tp.substate` 为挂单中但 `tp.order_status` 非 `OPEN`（如 `CANCELLED`），提示“止盈单未在挂（等待重挂或核验）”。
   - **真实仓位**：短腿、保护腿、覆盖关系、配对净信用（配对量为零时省略）、按盘口可平；到期进入 Stage-2 交割期货未结时追加“交割期货未结”标签。
   - **一局结束**：建仓 → 托管/退出 → 短腿清 → 库存清 → 结清。
4. 折叠区：实盘账本（`astra_exec_ledger@1.2.0`）、影子参照（虚线、明确“不是实盘流水”，估值层级译为中文）与原始编号（含 `capital_policy.last_reject_reason`、`commands`、告警原码）。卷宗入口为 `#player-hand/<dossier>/<hand>`。

## 终态、未知与时效规则

- **仅 `cashflow_settled === true` 判为结清**：停写、未知、已到期但未落账都不会显示成结清；缺失值显示“未知”，不填零。
- **在途 `UNKNOWN` 优先于平仓归零**：若存在 `PREPARED_UNKNOWN` / `SUBMIT_UNKNOWN` / `CANCEL_UNKNOWN` 且当前无明确在持短腿与保护腿（如建仓早期 `EP SUBMIT_UNKNOWN` 叠加 `exit_latched=true`），阶段判为 `unknown_pending`（`订单结果待核验` / `退出中 · 订单结果待核验`），进度条 `短腿清` 与 `库存清` 保持未完成，动作栏提示冻结操作、不重复下单，绝不显示“两腿已清”或“仓位已归零”。
- **到期后先于库存清除判定**：
  - OTM 到期且后端已置 `inventory_cleared=true, cashflow_settled=false` 时，仍归入 `expired`（`已到期 · 待交割落账`）；到期超过 2 小时仍未落账升级为 `已到期 · 交割久未落账`（`warn`）。
  - ITM 到期产生 Stage-2 交割期货（`STAGE2_DELIVERY_FUTURE_PENDING` 或 `ledger.two_stage_settlement.has_itm && inventory_cleared !== true`）时，即使期权腿显示 `0 / 0`，仍判为 `expired_stage2`（`已到期 · 交割期货待结算`，超 2 小时升级为 `已到期 · 交割期货未结且超时`，`warn`），严禁标为“两腿已清”或“仓位已归零”。
- **时效与空闲 45 秒窗口**：以 Workbench 重算的 `stale`、`status_age_ms`、`stale_after_ms` 为准；空闲态推荐阈值 `stale_after_ms=45000`（如 25 秒内保持新鲜，超过 45 秒如 50 秒标为“执行状态过期”），且空闲态不计入 `facts_age_ms`。页面本地只累加“距本次读取过去多久”，超过阈值 30 秒仍未重读才自行判为过期，避免轮询间隙误报。
- **停写接管**：优先采用 producer 的 `manual_takeover_required`；实盘模式（`LIVE` / `MAINNET`）下若未显式给出该字段，才由 `writes_enabled=false` 叠加未结风险推断接管（对应前端分支随主网 `LIVE` 适配一并收齐，见 [LIVE_EXECUTION_ACCESS_PLAN.md](LIVE_EXECUTION_ACCESS_PLAN.md)）。

## 数据接口依赖

读取方式：只调用 `GET /api/workbench/execution/live`。有未结清对局时每 10 秒读一次，否则跟随页面 30 秒轮询。

| 级别 | 字段 | 现状（`8608a4b9+` 已上线） |
|---|---|---|
| 必需，已存在 | `mode` `writes_enabled` `manual_takeover_required` `health` `stale` `stale_after_ms` `status_age_ms` `facts_age_ms` `generated_at_ms` `facts_asof_ms` `last_error` `account{available_funds_btc,equity_btc,margin_buffer_ratio}` `history_episodes[]` | producer 输出 + Workbench 时效重算（空闲 `stale_after_ms=45000`） |
| 必需，已存在 | `active_episode{dossier_id,hand_id,side,short/long_instrument,expiry_ms,target_qty_btc,decided_at_ms,phase,terminal_outcome,entry_closed,entry_halted,entry_halt_reason,exit_latched,exit_latch_reason,exit_started_at_ms,short_liability_cleared,inventory_cleared,cashflow_settled,short_pos_btc,prot_pos_btc,tp{substate,target_capture_ratio,active_limit_price_btc,active_remaining_btc,order_status},revoke{cash_wall_10009,...},residual{status},commands[],open_or_unknown_commands[],alerts[],ledger{schema:"astra_exec_ledger@1.2.0",entry,accounting,two_stage_settlement},shadow_counterfactual}` | 同上 |
| 已接入，有则显示 | `capital_policy{entry_armed,entry_paused,effective_budget_btc,budget_cap_btc,last_reject_reason}` | 协调器直接输出 `capital_policy`（并兼容读取旧键） |
| 已接入，有则显示 | `active_episode.decision{action,decided_at_ms}` `latest_review{action,at_ms}` | 缺失 `decision` 时回退 `active_episode.decided_at_ms` |
| 已接入，有则显示 | `tp.substate ∈ {TP_CLAMPED_TO_MIN_TICK, TP_UNATTAINABLE_LOW_CREDIT, TP_DEFERRED_EXCHANGE_BAND}` `tp.target_capture_ratio` | 未给捕获率时写“非 85%”；非 `OPEN` 终态单提示“止盈单未在挂” |
| 已接入，有则显示 | `alerts ∋ RX_MARGIN_OR_CASH_EXHAUSTED_SPREAD_HEDGED_MANUAL_DECISION, HOLD_REVIEW_LLM_UNAVAILABLE, QUOTE_UNAVAILABLE_AT_REVOKE, MARK_MISSING_AT_REVOKE, TP_UNATTAINABLE_ALERT, EXIT_PRICE_BELOW_EXCHANGE_MIN_BAND, STAGE2_DELIVERY_FUTURE_PENDING` | 协调器已输出；旧码 `M1_TRIGGERED_LLM_UNAVAILABLE` 同样识别 |
| 已接入，有则显示 | `history_episodes[].realized_pnl_btc` `episode_total_realized_pnl_btc` `dossier_id` | 已输出 `realized_pnl_btc` 并兼容 `episode_total_realized_pnl_btc` |
| 已下线/忽略 | `pending_approval_candidates` `confirm_code` `contract_hash` `POST /api/workbench/execution/*` | 前端不读取；写接口服务端返回 `403` |

## 本地预览

模拟情景只在本地预览服务器提供，地址为 `/fixtures/exec_states.json`，源文件是 `tests/fixtures/workbench_v3_exec_states.json`（`simulated: true`，不进入 `deploy/` 发布目录）。打开 `?exec_fixture=<idle|entry|tp|revoke|residual|unknown|cash|takeover|stale|expiry|expiry_stage2|settled>`，页面全程标注“模拟数据”。线上没有此文件，页面会提示仅本地提供。

发布前仍须在本地浏览器审查，并取得“当前本地页面可推送”。
