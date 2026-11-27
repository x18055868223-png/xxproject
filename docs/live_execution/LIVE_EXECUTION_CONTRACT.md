# xxproject Live Execution Contract (LIVE-P0 Converged v2.1)

> [!WARNING]
> **SUPERSEDED / HISTORICAL BASELINE（Pre-v1.2.0 历史合同）**：本文件记录 `LIVE-P0 v2.1` 早期设计（含已删除的 `approvals` 逐局审批、`AWAITING_APPROVAL`、`OperatorApproval` / `confirm_code`、`max_exit_pay_btc`、`EXIT_CAP_EXCEEDED` 以及已推翻的 Workbench `LoadCredential=exec_control_token` / 控制面直连方案）。**当前权威行为规范为 `live-exec-behavior@1.2.0`（持续资本授权 + 纯只读 Workbench v3 + 三用户物理隔离，Workbench 以 `User=astra-workbench` 零令牌仅读 `public_status.json`，绝无控制套接字权限）；Linux/Testnet 运维请以 [PRELIVE_VERIFICATION_RUNBOOK.md](PRELIVE_VERIFICATION_RUNBOOK.md) 与当前源码为准。**

- **Scope**: 0–1DTE Deribit BTC inverse credit vertical spreads (`BUY` protection -> confirm available coverage -> `SELL` short). Single dedicated subaccount, `MAX_EPISODES = 1` active/unsettled episode.
- **Authority Baseline**: `pre-trade-baseline-20261124-3a3f850e` (`3a3f850e86423ce604ca25ee62e3ad9221fcef97`).
- **Pre-Live Constraint**: Stages P0–P3 operate strictly in `SHADOW_ONLY` / `DRY_RUN` / isolated `TESTNET`. Live trading credentials, mainnet writes, and P4 capital authorization are excluded from this contract's authorization scope.
- **Resolves**: All mandatory P0 review items **`R-01..R-09`**, architectural constraints **`A-01..A-05`**, autonomous technical defaults **`D-01..D-06`**, and P0 Gate Review findings #1–#5.

---

## 1. Architecture & Minimal Components

One coordinator process, one transactional SQLite WAL store, three pure/DAL flat modules inside `tools/`, and direct integration with existing P5 (`SHADOW_ONLY` preserved), Workbench v3, and unified server release checks:

| Module (`tools/`) | Responsibility |
|---|---|
| `astra_exec_contracts_v1.py` | `Decimal` exact math, `ExecConfig` (`D-01..D-06`), dynamic `InstrumentSpec` stepped ticks, piecewise fee & solver (`solve_max_buy_price`), settlement/stress formulas, immutable facts & state contracts, deterministic order labels (`ax1:{env}:{ep8}:{purpose}:{seq}`). |
| `astra_exec_policy_v1.py` | Pure I/O-free deterministic `evaluate_step(...)`: symmetric coverage invariants, `valid_until`-fenced `UNKNOWN` resolution, irreversible `entry_closed`, branchless 85% GTC TP, bounded `PROTECTED_IOC` exit, unified residual disposal. |
| `astra_exec_store_v1.py` | SQLite WAL (`synchronous=FULL`) + `.writer.lock` + monotonic `writer_epoch` fencing; 5 tables (`meta`, `episodes`, `approvals`, `commands`, `venue_events`); idempotent ingestion + rebuildable ledger with 2026-08-01 two-stage delivery correlation. |
| `astra_execution_coordinator_v1.py` | Single-writer daemon (`User=astra-exec`): Deribit REST adapter (`/public/time` sync, adaptive credit-safe cadence, transport-level write guard), read-only P5 dossier watcher, OS-isolated local control server (`SO_PEERCRED` + dedicated token), atomic `public_status.json` emitter. |

No separate monitoring daemon, hedger, event bus, or shadow-coupled lifecycle manager exists.

---

## 2. Lifecycle Phases & Independent Completion Milestones (`R-04`, `R-06`, `A-02`)

```text
AWAITING_APPROVAL
   -> ENTRY                      (first EP/ES intent or fill)
   -> ENTRY_CLOSED               (irreversible entry_closed recorded; matched baseline frozen)
   -> TP_MANAGED | EXIT_LATCHED  (85% pre-rested GTC TP vs irreversible AI_REVOKE / EXIT_ALL)
   -> SHORT_LIABILITY_CLEARED    (short_pos == 0 and no open/unknown short-affecting orders)
   -> INVENTORY_CLEARED          (prot_pos == 0 and no open/unknown EP/LX orders)
   -> CASHFLOW_SETTLED           (terminal: ABORTED_ZERO_FILL | EARLY_FLAT_SETTLED | EXPIRY_SETTLED)
```

1. **Three independent milestones** (explicitly separated from P5's `M1` mechanical rule to eliminate naming collision — **`R-06`**):
   - `short_liability_cleared`
   - `inventory_cleared`
   - `cashflow_settled`
2. **Clean slot release (`A-02`)**: `MAX_EPISODES = 1` blocks new episode entry until the prior episode reaches `CASHFLOW_SETTLED` via one of three explicit terminal endings:
   - `ABORTED_ZERO_FILL`: Zero fills occurred, all `EP`/`ES` commands proved terminal/zero-fill (`valid_until` fence included).
   - `EARLY_FLAT_SETTLED`: Before `expiry_ms`, both legs reach flat inventory, all commands are terminal, and all exchange `USER_TRADE` IDs have confirmed balance-affecting `TX_LOG` cashflows. Meanwhile the P5 research dossier retains its original `08:00 UTC` counterfactual audit without obstruction.
   - `EXPIRY_SETTLED`: Past `expiry_ms` once stage-1 option settlement (`expiry`/`exercise`/`assignment`) and any stage-2 ITM inverse future `delivery` rows reconcile inventory & cashflow to settled.

---

## 3. Mandatory `R-01..R-09` & `A-01..A-05` Guarantees

### `R-01` Two-Stage 2026-08-01 Inverse Option Delivery & Ledger
- **Facts**: Deribit inverse options settling ITM since `2026-08-01` physically settle into same-expiry inverse futures at strike price (`exercise`/`assignment`), which then cash-settle in BTC (`delivery`) at `settlement_price`; OTM options settle solely as `expiry` with zero delivery fee. Daily (`settlement_period="day"`) options carry `0` delivery fee; weekly/monthly ITM carry `min(0.00015, 0.125 * abs(unit_payoff_btc)) * qty` where `unit_payoff_btc = abs(K - S) / S` per `1 BTC`. Live ITM two-stage settlement remains `NOT_VERIFIED / BLOCKED` until P3 environment proof.
- **Venue Polling & Ledger Rules**:
  - `get_positions(currency="BTC")` is **never** filtered by `kind="option"`; it captures both Stage-2 transitional futures and any unexpected external futures (which trigger immediate escalation and block new entry).
  - `TX_LOG` ingestion NEVER drops futures rows. Stage-1 option rows (`expiry`/`exercise`/`assignment`) and stage-2 same-expiry future `delivery` rows correlate by expiry & instrument pair (`TwoStageSettlementGroup`), including netted Stage-2 futures when both spread legs settle ITM.
  - `USER_TRADE` and `TX_LOG(type="trade")` share `trade_id` as two views of one fill—deduped strictly by `trade_id`, never summed twice. Balance changes derive uniquely from balance-affecting `TX_LOG.change_btc`. Theoretical payoff at official `settlement_price` acts strictly as an independent cross-check warning, never overwriting actual venue ledger.

### `R-02` & `A-03` Unified Order Protocol & `valid_until`-Fenced `UNKNOWN` Resolution
All five order purposes (`EP`, `ES`, `TP`, `RX`, `LX`) share one protocol:
1. **Pre-persist intent**: Persist immutable `ExecCommand` (`cmd_id`, `label`, `purpose`, `side`, `qty`, `limit_price`, `valid_until_ms`, `reduce_only`) in SQLite before network transmission.
2. **Server-clock `valid_until`**: Every submit (`buy`/`sell`/`edit`) attaches `valid_until = deribit_server_time_ms + request_valid_window_ms` (`5,000 ms`, offset maintained via `/public/time`). Deribit guarantees the matching engine never starts processing a submit arriving at/after `valid_until` (`13888 timed_out`). Note: `private/cancel` and `private/cancel_by_label` do **not** take `valid_until`, and `get_order_state_by_label` retains only 30 minutes—so durable reconciliation relies on full paginated `open_orders`, `order_history(include_old=true, include_unfilled=true)`, and `user_trades(historical=false & true)`.
3. **Conservative `UNKNOWN` / `CANCEL_UNKNOWN` fence**: Risk reservation MUST NOT release until:
   - `facts.server_time_ms > cmd.valid_until_ms + clock_safety_margin_ms` (`1,500 ms` — or if `CANCEL_UNKNOWN`, `cancel_by_label` confirms the **target** order terminal after respecting any `UNKNOWN` submit's `valid_until_ms + 1500ms`), AND
   - Complete paginated queries (`open_orders` + `order_state_by_label` within 30m + `order_history(include_old=true, include_unfilled=true)` + `user_trades`) prove terminal state and exact fill qty.
4. **Partial degradation**: An unresolved `UNKNOWN` order blocks all risk-increasing submits (`EP`, `ES`, new approvals), but does **not** globally block confirmed `reduce_only=true` short buyback (`RX`) on confirmed uncovered short qty (`max(0, abs(short_pos) - other_active_or_unknown_close_qty)`).

### `R-03` & `D-03` Bounded Active `REVOKE` Exit (`PROTECTED_IOC`)
- **Single mechanism**: Drop unverified Maker-wait branches and drop legacy `5s/15s` + rising `1.05x/1.15x * mark` chase. Use solely `Limit IOC (reduce_only=true, post_only=false, valid_until)` starting on the short leg (`RX`).
- **Four distinct bounds & exact piecewise fee inversion**:
  1. Exchange band (`[book.min_price, book.max_price]`);
  2. Pre-authorized per-episode absolute exit pay ceiling (`max_exit_pay_btc` in approval): remaining close budget `rem_exit_budget = max(0, max_exit_pay_btc - cum_rx_paid_btc)` feeds the **exact piecewise fee-inversion solver** `solve_max_buy_price(rem_exit_budget, short_qty, spec)` (handling both `fee = min(rate, 0.125*p)` segments and worst-case `min_trade_amount` split-fill rounding—never an approximate flat fee multiplier);
  3. Order limit strictly floored to legal tick (**`round_tick_down`**, never `ceil` above approved cap!);
  4. Non-resetting `exit_started_at_ms` persisted in DB (restarts continue elapsed clock).
- **Fault, missing-quote & retry cadence**:
  - When `ask` is valid and `round_tick_down(ask, spec) <= abs_unit_cap`, cross at `round_tick_down(min(ask, abs_unit_cap, book.max_price), spec)`.
  - If `ask > abs_unit_cap`: escalate `EXIT_CAP_EXCEEDED` cleanly (never breach `max_exit_pay_btc`; optionally rest/probe only up to `abs_unit_cap` if `>= book.min_price` without exceeding approved cap).
  - If `ask` is missing (`EMPTY_SIDE`/gap) and `mark` is valid: bound IOC at `round_tick_down(min(mark * 1.25, abs_unit_cap, book.max_price), spec)` and flag `ASK_MISSING_AT_REVOKE`. If both `ask` and `mark` are missing: escalate `QUOTE_UNAVAILABLE_AT_REVOKE` (never zero-fill).
  - OTV-safe retry schedule on partial/zero IOC fill: `(2000, 5000, 10000, 15000) ms` ramp, then **continuous `15,000 ms` retries** until flat or `expiry_ms - 5,000 ms`; emit `CANCEL_SLOW_ALERT` if entry/TP cancellation exceeds `8,000 ms`.
  - Pre-expiry `TP` freeze NEVER blocks legally executable `REVOKE` (allowed down to `expiry_ms - 5,000 ms`). `OPERATOR_EXIT_ALL` is irreversible by later `AI_HOLD`.

### `R-04` & `A-01` Entry Coverage, Protection Budget & Irreversible `entry_closed`
- **Symmetric coverage invariants**:
  - `reserved_short = abs(short_pos) + sum(active_or_unknown ES remaining)`
  - `available_prot = max(0, prot_pos - sum(active_or_unknown LX remaining))`
  - Every `ES` slice MUST satisfy **`reserved_short + slice_qty <= available_prot`**.
  - Every `LX` residual sell MUST satisfy **`available_prot - lx_qty >= reserved_short`**.
  - If external liquidation / manual intervention leaves `prot_pos < abs(short_pos)`, raise critical `NAKED_SHORT_BREACH` alarm and trigger exit path immediately rather than assuming impossibility.
- **Dual entry cost & abort guard (`A-01`)**: Entry approval constrains not only final net credit `min_net_credit_btc`, but also protection unit cost (`max_prot_unit_price_btc`), cumulative protection spend (`max_prot_cost_btc`), and worst-case failed-entry abort loss (`max_entry_abort_loss_btc`) before every `EP` and `ES` submit:
  `worst_case_abort_loss = prot_cost_committed - short_proceeds_received - net_recoverable(long_bid)`, treating `EMPTY_SIDE` (`long_bid = None`) conservatively as `0` recoverable value.
- **Irreversible `entry_closed` (`R-04`)**: Never infer entry completion from `Q_short_opening == 0` alone. Persist explicit irreversible `entry_closed = True` (plus `entry_closure_reason`, confirmed terminal `EP`/`ES` commands, matched qty `q_matched = min(prot_bought, short_sold)`, matched credit/costs, unmatched long qty) before resting `TP`. Zero post-closure entry permitted.
- **Immediate risk attachment**: First protection fill activates inventory tracking; first short fill activates P5 hold review window; mid-entry `AI_REVOKE` / TTL / `HALT_ENTRY` immediately freezes new entry, cancels open/unknown `EP`/`ES`, confirms late fills, then unwinds short before protection.

### `R-05` 85% Pre-Rested GTC TP, Exact Precision & Ledger Separation
- Dynamic `InstrumentSpec` reads live `min_trade_amount`, `tick_size`, and `tick_size_steps` (e.g. `<= 0.0050 -> 0.0001`, `> 0.0050 -> 0.0005`, never hardcoded statically). All money/qty/fee calculations use `Decimal` raw precision without premature per-row rounding.
- **Matched pro-rata protection cost & 85% TP formula**:
  - When `q_prot > q_matched > 0`, allocate protection cost conservatively using `ceil_1e8(prot_cost_total * q_matched / q_prot)`.
  - `C_matched = short_proceeds_matched - prot_cost_matched - entry_fees_matched`.
  - `B_total = floor_1e8((1 - 0.85) * C_matched)`.
  - `solve_max_buy_price(rem_budget, rem_short_qty, spec)` computes the exact legal `BUY TO CLOSE` unit limit accounting for piecewise `min(rate, 0.125*p)` fees and worst-case `min_trade_amount` (`0.1 BTC`) split-fill fee rounding, then applies `round_tick_down`, never exceeding `p_tp_cap_init`.
  - Submit single GTC `BUY TO CLOSE` (`post_only=false, reduce_only=true`). Zero mutation across normal market moves; mutate only on `short_qty` change, abnormal venue cancellation/rejection, `EXIT_ALL`, or `REVOKE`.
  - Handle `p_tp_cap < tick_size` (`TP_UNATTAINABLE_BELOW_MIN_TICK`), `book.min_price > p_tp_cap` (`TP_DEFERRED_EXCHANGE_BAND`), and `tp_expiry_freeze_ms = 60s` (`REVOKE` stays unblocked).
- **Mandatory ledger bucket separation (`R-05`)**:
  - `cashflow_btc` (raw signed balance movements) vs `open_entry_credit_btc` (unclosed entry short proceeds less costs; NEVER labeled realized profit!) vs `realized_closed_pnl_btc` (closed/settled spread pnl excluding open inventory) vs `unmatched_prot_cost_btc` (segregated from TP `C_matched`, included in `episode_total_realized_pnl_btc`) vs `unrealized_mark_pnl_btc` vs `unrealized_executable_pnl_btc` vs `settlement_pnl_btc` vs `actual_fees_btc` vs `estimated_fees_btc` vs `entry_slippage_btc` vs `shadow_counterfactual_pnl_btc`.

### `A-04` & `D-04` Unified Protection Residual Rule
- Once `short_liability_cleared` holds and `prot_pos > 0`:
  - Wait for any active/unknown `EP` closure.
  - Evaluate executable bid recovery: `gross = bid * lx_qty`, `fee = option_trade_fee_btc(bid, lx_qty, spec)`, `net = gross - fee`.
  - Submit `LX` (`Limit IOC, reduce_only=true, valid_until`) when `net >= min_residual_net_btc` (`0.00005 BTC` test candidate) AND `net / gross >= 0.50` AND `now_ms < expiry_ms - 60_000`. Sell floor rounds safely **up** (`round_tick_up`).
  - Temporarily `EMPTY_SIDE` (`no bid`) or thin bid enters `RESIDUAL_HOLD` and re-evaluates automatically on normal coordinator polling (zero new daemon/service), holding to expiry if never economically recoverable.

### `R-06` & `A-05` Live Independence from Shadow Lifecycle
- P5 remains immutable underwriting & `AI_REVOKE` authority (`SHADOW_ONLY` unchanged). Verification checks bind `hand_id`, `dossier_episode_id`, `expiry_ms`, `decision_sha256`, `input_sha256`, `review_id` dedup, and `_is_verified_revoke` (rejecting `DISCRETIONARY_REVOKE_UNVERIFIED` / `CONFLICT_WITH_FROZEN_INVALIDATION`).
- Live execution supplies normalized `real_execution_context` (`actual_spot_usd`, `actual_qty`, `actual_entry_at_ms`, `actual_net_credit_per_btc`) and enforces a **separate daily quota ledger file** (`astra_player_live_hold_review` — because `DailyHttpBudget` shares one counter per ledger file across roles!). If `M1` triggers while P5 LLM is unavailable/degraded, raise prominent `M1_TRIGGERED_LLM_UNAVAILABLE` Workbench alert (`D-06`), with zero mechanical auto-close fallback.
- Real risk tracking, TP, `REVOKE`, `LX`, and settlement continue uninterrupted if `SHADOW_EXITED` triggers, `LockedCandidateStore` stops, P5 LLM fails, or `public_status.json` lags (`A-05`).

### `R-07` & `R-08` Process Isolation, Control Auth, Approval Integrity & Release Gate
- **OS separation & filesystem compatibility**:
  - Coordinator runs as `User=astra-exec` owning SQLite DB & exchange secrets (`0600`).
  - Execution deployment unit drop-in runs Workbench as `User=astra-workbench` (`SupplementaryGroups=bitnami`) so `/run/astra-exec/control.sock` verifies Linux `SO_PEERCRED` (`peer_uid != bitnami` LLM units!) PLUS `LoadCredential=exec_control_token:/etc/signal-audit/exec-control.token`. Under execution mode, `/var/lib/xxproject/workbench` uses shared group `bitnami` (`0775` / `0664`, `UMask=0007`) so `astra-workbench` and `astra-player-*` interoperate without exposing the control token or socket to `bitnami`; `deploy_release.py` preserves execution units/drop-ins only under explicit execution-mode flag while leaving the 21 `SHADOW_ONLY` units intact by default.
- **Approval integrity**: `OperatorApproval` validates unique `approval_id`, server-generated `confirm_code`, `env`, `subaccount_id`, `contract_hash`, single `idempotency_key`, non-expired `expires_at_ms`, and atomic `campaign_consumed = 1` per hand/episode.
- **Independent manual overrides**: `HALT_ENTRY` stops new opens while keeping TP/REVOKE/LX alive; `EXIT_ALL` irreversibly triggers bounded short-first unwind; `DISABLE_WRITES` blocks all coordinator exchange writes cleanly, warns prominently that resting exchange orders remain open for manual takeover, and refuses auto-resume.
- **Adaptive cadence & fail-closed release guard**: Poll `15–30s` idle, `5–10s` TP/residual, `1–2s` entry/exit/`UNKNOWN`, with `429`/`10028` exponential backoff and cancel/exit priority. `public_status.json` carries `generated_at_ms`, `facts_asof_ms`, and staleness bounds (`>15s` warning, `>45s` stale error — zero false green). When coordinator is enabled, `deploy_release.py` queries `/health-fresh` (fresh venue facts + command state, NEVER DB-only inference) and **fails closed** (blocks deploy/rollback if unreachable/stale, or if open positions/unknown orders/unsettled cashflow exist); rollback never deletes execution ledger or stops an active-risk coordinator.

### `R-09`, `D-01..D-06` Parameter Framing & Stress Risk Gate
- All `D-01..D-06` numeric defaults (`0.0008` floor, `0.80` ratio, `15m` TTL, `90s` entry window, `0.1 BTC` test slice, `0.00005` residual floor, `2.5x` margin buffer candidate, `S_stress = 0.70 * S_0`) live in versioned `ExecConfig` as **test/offline candidates**, explicitly NOT empirical proofs of optimality.
- Put worst-case delivery stress loss computes nonlinearly as `q * W / (0.70 * S_0)` (Call: `q * W / K_long`) and gates both operator approval and every risk-increasing submit alongside initial/maintenance margin headroom.
- 18-episode (`14,617` 1m rows) data provides coarse liquidity/touch-rate screening only; `2026-11-23 UNPRICED` counterfactual remains strictly assumption-bounded.
