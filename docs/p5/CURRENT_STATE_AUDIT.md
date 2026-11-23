# 生产现状与真实调用链核查（CURRENT_STATE_AUDIT）

**核查基线与重要更正**：通过 2026-11-23 08:08 UTC 只读 SSH 对 `/opt/signal-audit-tools/*.py` 的 SHA-256 逐文件比对，**当前线上 `xx-eu` 实际部署版本是 `codex/astra-audit-minimal-fix@b3d79695`（包含 `5a43b21d + b3d79695`），而不是历史的 `ba93eef2` 或未合并的研究分支 `f619b225`**。

## 1. 线上运行拓扑与真实 SHA 核对

- **主机配置**（`xx-eu` `/etc/signal-audit/workbench-v20.env` & systemd）：`ASTRA_PLAYER_MODEL=deepseek-v4-pro`、`ASTRA_PLAYER_REASONING_EFFORT=high`、`LLM_TIMEOUT_SEC=360`、`plan.daily_r16.v2.json`（`mode="SHADOW_ONLY"`, `execution_allowed=false`, `deal_grace_ms=1800000`, `reply_grace_ms=1800000`）。
- **调度链路**：`astra-player-prewarm.timer`（15:52 UTC）→ `astra-player-deal.timer`（16:02/07/12/17 UTC，`auto-deal`）→ `astra-player-sync.timer`（*/5m，`sync-open --check-due --review --archive`）→ `astra-player-settle.timer`（08:06/16/31 UTC，`settle-due`）。最近自然卷宗已录至 `Deribit_BTC_20261123T080000Z`。
- **实际部署文件 SHA-256（与本地 `.worktrees/astra-audit-minimal-fix` LF 归一化后 100% 匹配 `b3d79695`）**：
  - `astra_player_input_v1.py`: `f8b06225...` | `astra_player_review_v1.py`: `90ec1049...` | `astra_player_tracking_v13.py`: `5aa70f53...`
  - `astra_player_dossier_v1.py`: `278d410c...` | `astra_player_condition_v1.py`: `1d177bea...` | `astra_level_behaviour_v1.py`: `a29e53a0...` | `astra_anchor_relative_v1.py`: `f4e7ffb1...`

## 2. 真实端到端调用链（P5 改动前 `b3d79695` 实态）

1. **发牌收集与冻结**：[astra_player_tracking_v13.py](../../tools/astra_player_tracking_v13.py) `_auto_deal()` → `Dossiers.tick()` → `collect()` → `Dossiers.deal()` → [astra_player_input_v1.py](../../tools/astra_player_input_v1.py) `build_input()` 生成唯一 `evidence/input.md`（**内联全部中文规则书正文**，`astra_player_rulebook.md` 仅供旧版 `astra_player_v1.py` 使用，v1.3 不读取它）+ `evidence/identity.json` (`input_sha256`, `source_sha256`, `rules_sha256`, `generator`)。
2. **System / User 构建与 LLM 发送**：`_deal_hand()` 校验 `existing_auto` 最多 **1 次** `API_AUTO` 尝试，落盘 `api_calls/<attempt>/request.json` + `dispatch.json` 后 `retries=0` 调用 `review.decide_initial_hand()` → `call_chat_llm()`（`max_tokens=24576`, `thinking.type="enabled"`），`on_response` 立即持久化 `reply.json`。
3. **崩溃恢复**：下次 `auto-deal` / `sync-open` 先跑 `recover_initial()`，仅凭已保存 `reply.json` 调用 `_record_initial_reply()`；无回执时记 `DELIVERY_UNKNOWN`，绝不自动二次付费重发。
4. **解析、锁单与检查钟**：`parse_initial_reply()` 拆分正文与 `<ANNOTATION_JSON>`，写 `attempt.json` / `decision.json`。若为 `Accept`，`_link_with_fresh_quote()` → `condition_plan()` → `condition_at_t0()` → `lock` → `Dossiers.episode()` 冻结 `condition.json`，检查钟为 `(entry_at_ms // 60000 + 1) * 60000` 起每 `check_interval_ms`。
5. **事中 M / C / Hold**：`sync-open` 执行 `check_due_conditions()`（独立机械 M）及 `evaluate_hold_management()`（消费 locked tracker 序列、Live 短腿买回报价、`structure/*.jsonl` ARD、FMZ 4h/12h `micro_flow` 四类真实现场源，触发 `detect_hold_triggers()` + `build_hold_review_input()`），并保留 `DISCRETIONARY_REVOKE_UNVERIFIED` 归因拦截与 `execution_allowed=false`。

## 3. 五大核心审计问题状态矩阵（P5 改动前 `b3d79695` 实态）

| 审计维度 | 具体条目 | 改前 `b3d79695` 状态 | 核实依据与遗留缺口 | P5 候选（`codex/player-p5-candidate`）处置 |
|---|---|---|---|---|
| **Q1 P0 证据** | `frozen_points` / 分钟标签延后 | `IN_PRODUCTION` | `dossier_v1.py:322` 已传 `unverified_minute_label`，`chart_points_audit()` 排除边界未完结桶 | 保留并在 `p0_strict@1` 下用 `ard_facts_for_evidence` 与 `t0/episode` 对齐 |
| **Q1 P0 证据** | 现货 4h/12h 完成时钟与 `gap` 标记 | `PARTIAL` | `derive_non_overlapping_spot_windows()` 已拦相减，但缺显式三态分类及 mixed 隔离，且未抑制“须明确作为已成立反证”提示 | `inspect_spot_qualification()` 严格分级 `OBSERVED_ALIGNED / OBSERVED_UNALIGNED / UNKNOWN`（future 零泄露） |
| **Q1 P0 证据** | L2 顺序两腿观测 (`short + long`) | `PARTIAL` | `b3d79695` 仅读 `L2.book.short`，且仅在 `boundary_replaced` 时输出，未算 `long` 时差/价差 | `p0_strict@1` 无条件呈现短/长两腿及独立 `long_only`、`leg_skew_ms`、`leg_price_delta_usd` |
| **Q1 P0 证据** | ARD `available_at_ms` 与全窗极值 | `PARTIAL` | `b3d79695` 在 `collect()` 把缺 `available_at_ms` 旧行静默回填为 `at_ms`；缺全窗未触及保护腿/更深片段披露 | `p0_strict@1` 显式拒绝 `compat_backfill_applied`（退回 `UNKNOWN`）；补全窗覆盖/极值披露 |
| **Q2 Prompt/收据** | `@1.0` vs `@1.1-candidate` | `@1.0 IN_PRODUCTION` / `@1.1 RESEARCH_ONLY` | `b3d79695` 无 `@1.1-candidate` 与 P5；规则书随 User `input.md` 内联注入 | 冻结三版本：`@1.0`、`@1.1-candidate`、新候选 `@1.2-p5-candidate` |
| **Q2 Prompt/收据** | 请求字节稳定 SHA 收据 | `PARTIAL` | 线上 `request.json` 只有 `packet_hash`，缺 `system_sha256`、`user_sha256`、`messages_sha256`、`body_sha256`、`input_profile` | `build_initial_request_payload()` 全量签发并持久化，含 `contract_pair` 与版本互斥检验 |
| **Q3 P2 防绕过** | 6 类绕过面 | `UNKNOWN / 缺失`（`@1.0` 无严锁） | `strict_contract=False`、`require_outside_at_t0` 省略/false、缺/伪 `prompt_version`、`_record_initial_reply` 无 `contracts`、CLI `response/freeze`、`link` 先锁后验、`Reject/Unknown` 缺 J1 全未阻断 | P5 对候选版本全面 **fail-closed**；同步保留旧 `@1.0` 与 120s legacy replay 零退化 |
| **Q4 运行可靠性** | 旧牌隔离、1 次付费帽、120s/15m、工作台快首屏 & `DISCRETIONARY_REVOKE_UNVERIFIED` | `IN_PRODUCTION` | 已在 `ba93eef2` 与 `b3d79695` 验证上线 | **100% 完整保留**；新增 `ContractValidationError` 落盘 `PARSE_FAILED` 且绝不触发二次付费 |
| **Q5 C/Hold 源** | 1m 指数 / Tracker报价 / `structure/*.jsonl` ARD / FMZ 4h&12h `micro_flow` | `IN_PRODUCTION`（缺门控分层） | `b3d79695` 未向 Hold AI 明示“无入场后新合格数据时严禁宣称 C 仍有效”，`post_entry` 与旧流向易被误当新证据 | `review_channel` + 严格 `new_qualified_data_present`（仅真入场后新增合格数据为 True）+ M/C/UNKNOWN/OBSERVE 四分流；仍 `execution_allowed=false` |

## 4. C 复核的真实数据边界（不可观测项标 UNKNOWN）

- **可确定性核验**：1m `btc_usd` 采样 (`_merge_index_points`)、Locked 序列买回深度 (`point_metrics` / `short_buyback_quote`)、同口径 `structure/*.jsonl` `available_at_ms <= asof` Pin/ARD 比较、同源同截止推进的 4h/12h FMZ `micro_flow`。
- **必须永久标 `UNKNOWN`（严禁脑补或自动动作）**：采样间微秒/秒级极值、未证实 1m K 线开收语义、低于 4h 的高频现货 CVD（如 5m/15m）、缺完成时钟或未对齐 `micro_flow`、持仓期权 Flow（`flow/*.jsonl` 未接入 Hold）、做市商真实 Gamma 多空持仓、强平热力图及 Pin 因果吸附性。
