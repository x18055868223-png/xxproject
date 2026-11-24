# P5 风险证据消费链修复工程结项报告

**结项状态**：`FIX_READY_NOT_DEPLOYED`（所有代码修复、全量回归、历史 as-of 回放及 6 次受控真模型验证均在隔离工作树 `codex/p5-evidence-pipeline-fix` 完成；当前处于活跃交易窗口且未收到人工安全窗口切流指令，真实生产自然发牌仍标为 `PENDING_NATURAL_CONFIRMATION`）。  
**2026-11-24 16:00Z 现网 `3a3f850e` 自然发牌实机稽核（`task-6537`）**：`MAIN_PLAYER_E2E_NOT_CONFIRMED (TRUNCATED_AT_MAX_TOKENS_24576)` — 今日 `hand_d62e9c3f7ee24f5b80c0efee`（`daily_r16@3` / `deepseek-v4-pro` / `reasoning_effort=high` / `max_tokens=24576`）在 24,576 completion tokens 耗尽后因 `finish_reason="length"` 于 `<ANNOTATION_JSON>` 前截断，程序安全失败闭环为 `PARSE_FAILED`（未建仓、未锁单、`execution_allowed=false`、0 broker 订单）。本分支已同步将 `astra_player_review_v1.py` 默认 `max_tokens` 由 `24576` 对齐为 `32768` 并经 6 次真模型全数 `finish_reason="stop"` 验证。

---

## 1. 现象与已证实根因

| 断路通道 | 线上真实盲区（`xx-eu @ 3a3f850e` 实测） | 根本原因（时钟 / 来源 / 契约） | 修复后果 |
| --- | --- | --- | --- |
| **A. 现货主动流（初始 `t0` + 持仓 `Cat D`）** | FMZ `snapshots.jsonl` `0/12` 对齐；近 5 局 `locked_candidates/series` `0/5` 具完整 `flow_window_basis`（近 3 局 `flow_asof_ms=null`） | FMZ `2.0.6` `BarAssembler` 缺 `start_ts_ms`/`end_ts_ms`/`source`/`symbol` 及累计闭合柱锚点 `cum_cvd_btc`/`cum_session_id`/`cum_gap_count`；缺成交时间时 fallback `now_ms()` 造伪时钟；`compare` 在 `metrics_version=4` 丢弃时钟与 `source`；`Cat D` 连贯性判定缺基准 | FMZ `2.0.7` 严格生产原生成交时间与累计闭合柱基准；`compare` (`METRICS_VERSION=5`) 与 `tracking` 经通用契约验真后才释放 `Cat D`；不合格时隐藏未对齐 CVD/方向并暴露 `BLIND_UNALIGNED` |
| **B. 5m Live Pin / ARD 迁移与 UI** | `expiry_gamma.json` `0/4` 到期含 `pin_source`/`pin_method`；近 5 局 `series` `0/5` 含 `pin_source`；11-23 `16:45Z` 已跌至 `60,500` 却被 `30m` 归档延迟至 `64,000` | `expiry_gamma` 未输出 `pin_source`/`pin_method`/`pin_status`；`lock_levels.compact()` 字段裁剪与 `SERIES_KEYS` 遗漏；`tracking` 仅读 `30m` 归档且超 `20m` 未强制 `STALE`；`locks.js` 对空态/过期误印 `"未变"`；历史旧行 `pin_stale` 污染最新鲜 `live_5m` 的 `reason_codes` | 端到端透传 `live_5m` Pin（`<950B` compact）；严格 `20m` SLA + `position_only` 只读迁移触发 `Cat C` 并禁回滚；`locks.js`/`player-audit.js` 优先暴露过期/同钟；仅当最新观测失败才记 `pin_qualification_reasons` |
| **C. L4 `RV24` 波动率背景** | `expiry_gamma.json` 与 `11-24 16:00Z` 实盘 `L4.expiry_vol` 均 `asof_ms=null`、缺 `rv_computed_at_ms`/`source`/`status` | `realized_vol()` 未产出计算钟/来源/状态且缺重复冲突去重；`workbench` 异常回退未保留可用钟；`player_input` `p0_strict@1` 无中性 `RV24`/`IV` 合格化呈现 | `realized_vol()` 与 `case_file` 完整写钟与 `deribit.perpetual_chart` 血缘；`p0_strict@1` 仅在合格时中性展示 `ATM IV / short mark_iv / RV24`（严禁 `RV4h` 与 `p_touch_short` 入正牌面）；`legacy_v1` 保持字节级零漂移 |
| **D. 实盘 P5 `24576` 截断** | `2026-11-24 16:00Z` `deepseek-v4-pro` + `reasoning_effort=high` 在 `24576` token 截断 | `astra_player_review_v1.py` 仍沿用 `24576` 上限，高推理耗时耗尽预算导致无 `<ANNOTATION_JSON>` | 默认 `max_tokens` 对齐仓库其它高推理入口至 `32768`，不修改 P5 System Prompt（`b746c2d2...` 保持不变） |

---

## 2. 通用事实资格契约（`evidence_qual@1`）与改动索引

新增共享契约模块 [astra_evidence_qual_v1.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/tools/astra_evidence_qual_v1.py)（749 行），以统一时钟、血缘与防未来函数规则贯穿生产端到消费端：
- `qualify_spot_window(...)`（L129）：强制校验 `start_ts_ms <= end_ts_ms <= cutoff_ms`、`age <= 60m`、有效 `source`/`symbol`（禁止空串/占位词）、零 `gap`/`truncated`/`degraded`/`source_conflict` 与窗口闭合状态，产出 `OBSERVED_ALIGNED` / `OBSERVED_UNALIGNED` / `STALE` / `UNKNOWN`。
- `qualify_live_pin(...)`（L362）：强制 `expiry_ms` 匹配、`computed_at_ms <= available_at_ms <= cutoff_ms`、`age <= 20m`、`pin_status == "ok"` 且 `pin_method` 与 `pin_source` 完整。
- `qualify_expiry_realized_vol(...)`（L525）：强制 `BTC-PERPETUAL`、`last_bar_end_ms <= cutoff_ms`（`age <= 90m`）且 `rv_computed_at_ms <= cutoff_ms`（`age <= 20m`）；支持显式传入 `offline_reconstructed_clock_ms` 并强制披露 `RECONSTRUCTED_OFFLINE`（正式运行时默认为 `None`）。
- `summarize_channel_capabilities(...)`（L695）：严格区分 `QUALIFIED_ACTIVE`、`QUALIFIED_NO_EVENT`、`DEGRADED_POSITION_ONLY`、`WARMING_UP` 与 `BLIND_UNALIGNED` / `BLIND_STALE` / `BLIND_MISSING`。

**关键改动文件与符号行号（相对基线 `3a3f850e`）**：
- FMZ 交付物 [neutral_regulation_demo_fmz.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/demo/%E6%9C%80%E6%96%B0%E4%BA%A4%E4%BB%98%E7%89%A9/neutral_regulation_demo_fmz.py)（`demo_version="2.0.7"`, `read_only_demo=True`, `bar_history_size=10000`, LF SHA-256 `1c5dc75fd9d312acac706869d503bf102f5bf9a5996c767d4d385872096ab9c`）：`BarAssembler` 原生成交时间戳/缺口/乱序/淘汰与闭合 bar 累计锚点、`micro_flow_for_horizon` / `combine_micro_flow_horizons` 及 `_build_cross_section` 透传完整时空字段。
- 上游事实上下文与样本封装：[astra_underwriting_fact_context_v20.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/tools/astra_underwriting_fact_context_v20.py)、[astra_expiry_gamma_v1.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/tools/astra_expiry_gamma_v1.py)、[astra_workbench_v20.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/tools/astra_workbench_v20.py)、[astra_lock_levels_v1.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/tools/astra_lock_levels_v1.py)、[astra_case_file_v1.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/tools/astra_case_file_v1.py)（含 `rv_{fetch_failed,duplicate_conflict,insufficient_bars}` -> `quality="unknown"`）、[astra_locked_candidate_compare_v1.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/tools/astra_locked_candidate_compare_v1.py)。
- 牌面、条件、追踪与复核：[astra_player_input_v1.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/tools/astra_player_input_v1.py)（`p0_strict@1` 先行 `qualify_expiry_realized_vol`，`expiry_iv_stale` 不再误盲新鲜 `RV24`）、[astra_player_condition_v1.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/tools/astra_player_condition_v1.py)、[astra_player_tracking_v13.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/tools/astra_player_tracking_v13.py)（阻断历史 `pin_stale` 继承至 `live_5m`、透传 `pin_eval_mode`/`channel_readiness`/`evidence_snapshot` 至 `decision.json`/`review_snapshot.json`/`audit_record()['hold_reviews']`、Hold 传 `include_rv24=False`、`evaluate_hold_management` 与 `review_snapshot` 清空不合格 `t0`/当前流向与 CVD、并在 `_load_prior_reviews_and_context` 阻断未对齐 `flow_direction`）、[astra_player_review_v1.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/tools/astra_player_review_v1.py)（默认 `max_tokens=32768`、`_detect_category_d` 双向校验对齐 `ref_basis`/`flow_asof_ms`、第 2/3 节复用 `_hold_flow_delta_allowed` 门禁防止“先对齐后退化/过期”时第 3 节 `_format_dual_delta_lines` 泄漏 `4h CVD` 与流向时钟推进、Hold 传 `include_rv24=False`）。
- 健康观测、前端与发布配置：[xxproject_server_health_v1.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/tools/xxproject_server_health_v1.py)（`--require-evidence-channels` 将仅 backfill 状态含 `"degraded:backfill_mixed"` 判为 `CAP_WARMING_UP`，对 `gap`/`truncated`/`window_truncated`/`missing_provenance`/`source_conflict` 严格 fail）、[locks.js](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/deploy/astra_workbench_v3/locks.js)、[player-audit.js](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/deploy/astra_workbench_v3/player-audit.js)（补全 `PIN_EVAL_MODE_CN`：`same_measure_ard`/`position_only`/`STALE`/`UNALIGNED`/`UNKNOWN`）、[release_candidate_manifest.json](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/deploy/signal_audit/release_candidate_manifest.json)（`git_branch="codex/p5-evidence-pipeline-fix"`、同步 `fmz_producer` 2.0.7 / `fmz_native_schema` / `workbench_api` / `native_receiver` / `locked_candidate_comparison` LF SHA-256、`runtime_status="LOCAL_CANDIDATE"`、`production_status="NOT_NEWLY_DEPLOYED"`）、[deploy_release.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/deploy/xxproject_server/deploy_release.py)、[server_self_check_signal_stack.sh](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/tools/server_self_check_signal_stack.sh)。
- 新增红蓝回归测试与历史 mock 对齐：[test_p5_flow_e2e_chain.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/tests/test_p5_flow_e2e_chain.py)（10 个正/反 E2E 用例）、[test_p5_pin_rv_chain.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/tests/test_p5_pin_rv_chain.py)（7 个 Pin/RV/真实 FMZ 2.0.7 `backfill_mixed` Health/JS 用例）、[test_p5_evidence_consumers.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/tests/test_p5_evidence_consumers.py)（11 个消费链、降级隐藏、未对齐 `t0`/`last_review` 阻断、多轮先对齐后退化第 3 节防泄漏与真实 JS 渲染用例；合计 **28** 个新增用例）；同步补齐 [test_astra_player_e2e_v13.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/tests/test_astra_player_e2e_v13.py) 中 4 处 `write_fmz_with_flow` 来源与时钟字段。

---

## 3. 确定性测试、历史 as-of 回放与受控 6 次真模型验证

- **回归测试与构建校验**（Round 2 最终修复后完整复跑）：
  - 新增 3 个红蓝测试文件 `pytest`：**`28 passed`（`10.03s`）**；[test_astra_player_e2e_v13.py](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/tests/test_astra_player_e2e_v13.py)：**`5 passed`（`26.84s`）**。
  - 35 个核心 pytest 测试集：**`643 passed, 1 skipped, 1 failed`（`304.12s`）**——其中 1 个跳过为本地无 `.artifacts` 目录的 `test_real_initial_public_view_context_refs_keep_missing_source_clock_none`，其余 34 个文件（`627 passed, 1 skipped`）零失败；唯一失败的 `tests/test_player_initial_recovery.py::test_formal_timer_to_native_tracking_and_fixed_check` 已在干净基线 `3a3f850e` 核实为既有历史回归（引入 `795048bd` 闭柱前瞻边界 `at - dossier.MINUTE` 后，未同步推进该无关用例的 `now[0] = tick`，本分支保持局部变更未改该文件）。
  - 4 个独立 FMZ 脚本（`test_fmz_157_fixed_analysis_round.py`, `test_fmz_157_producer_contract.py`, `test_fmz_161_near_term_context.py`, `test_fmz_162_gex_time_semantics.py`）全 **`PASS`（exit 0）** + `node --check`（`locks.js`、`player-audit.js`）通过 + `tracking.evaluate_hold_management -> audit_record() -> Node renderPlayerAudit` E2E 断言全绿 + `astra_release_check_v1.py`（`ok: true`, `version="2.0.7"`, `sha256="e80d01e937c562d2695f62adcab30d68d8ff23d3e09a5ac9a4cd0370699d0e1e"`, `manifest=1c5dc75fd9d3` 与 `file=1c5dc75fd9d3` 一致）。提交后将在干净工作树（无 `--allow-dirty`）完成最终双次构建比对。
- **历史 as-of 回放（`11-23` `hand_a050f60ea6bb7d73acf68a3f` & `11-20` `hand_d9598e9be936b9b777c5295f`）**：
  - `legacy_v1` 对两组历史冻结 `input.md` 保持 100% 字节一致（SHA-256 `58253266...` 与 `25aa2753...`）。
  - 原始未改造 `source.json` 在 `p0_strict@1` 下正确将现货流判为 `【OBSERVED_UNALIGNED】`、将 `RV24` 判为 `UNKNOWN（missing_rv_availability_clock）`；注入 `L3.expiry_walls.asof_ms` 作为 `offline_reconstructed_rv_clock_ms` 后，中性呈现 `RV24`（`92.59%` / `89.78%`）与 `RECONSTRUCTED_OFFLINE`，且不带 `RV4h`/`p_touch`。
  - `11-23` M1 前持仓（`16:45:20Z`，现价 `62,053.26`、`M1=INTACT`）：Arm A（`archive_30m` `16:30:25Z` 延迟 `14.9m`，Pin 仍读 `64,000`）仅触发 `Cat B step_9` 且隐藏未对齐 `232.01` 现货流；Arm B 接入 `live_5m`（`age=0ms`，`pin=60,500`）转入 `position_only` 且触发 `Cat C ARD_STRUCTURE_SHIFT:PIN`（`pin_qualification_reasons=[]`）。38 行全窗扫描：`raw Cat C=0, Cat D=0`；`live_5m Cat C` 仅于 `Pin 64,000->62,000`（`row[27]`）起触发，无重复误报；`16:51:00Z` 仅依赖 `archive_30m` 时超 `20m`（`1,234.8s`）准确降为 `STALE` 且不印 `是否迁移：否`。
  - **Hold 终版确定性重跑（`run_historical_replay.py`，修复 `include_rv24=False` 与未对齐 `t0`/`last_review` 阻断后）**：确认 `blind_channels=["spot_flow"]`（不再虚警 `rv24_background` 盲区）、明确打印 `"入场时主动流基线：资格未通过"`，确定性输入 SHA-256 为 `arm_a_sha256=6344ba766a5929b3bdc781df1f754e64466f71fe1424858633ad37b54370a335`、`arm_b_sha256=1022132454fd3fadb80eb6a8a65cac34e2f63c4267df8330104e63682dd8e695`（与 [p5_model_call_audit.json](file:///c:/Users/Xu/Documents/中性回路整合工程/.worktrees/p5-evidence-pipeline-fix/docs/p5/p5_model_call_audit.json) 一致）。
- **6 次受控真模型调用**（`xx-eu` `/tmp` 隔离沙盒，`deepseek-v4-pro` / `reasoning_effort=high` / `max_tokens=32768` / `retries=0` / `execution_allowed=false`；6 次共消耗 `112,910` tokens，`estimated_cost_usd=null` 因仓库未配置费率表；正式账本 `0899c3a8...` `http_calls_used=1` 零变更，沙盒已清除）：
  - **Call 1–4（初始 `p0_strict@1`）**：4/4 `finish_reason=stop`、`strict_contract=True`；`11-23` Arm A/B 均 `Accept / Put`，`11-20` Arm A/B 均 `Accept / Call`，`invalidation=entry_not_returned`、`require_outside_at_t0=True`、`depends_on_ard=False`。Arm A 显式报告 `RV24 为 UNKNOWN`；Arm B 收到 `RV24=92.59%`/`89.78%` 后未扭曲择边、未去掉 `UNKNOWN` 外的保守边界，亦未生成机械拒保（`DELIVERED_NON_DISTORTING_NO_VERBATIM_CITATION`）。
  - **Call 5–6（`11-23` `16:45:20Z` 持仓增量复核）**：2/2 `REVOKE`（`affected_conditions=["S1"]`, `DISCRETIONARY_REVOKE / VERIFIED`），均零泄漏未对齐 `232.01` 数值并诚实标出 `spot_flow OBSERVED_UNALIGNED`。Call 5 仅见 `"Pin 未迁移，仍为 64,000.0 USD"`；Call 6 明确消费并引述 `"live_5m Pin 从 64,000 迁至 60,500 USD，已低于 62,000 短腿"`（耗时 `74.53s` vs `106.0s`）。注：该 6 次实盘外真模型额度在 `include_rv24=False` 与未对齐 `t0`/`last_review` 补充阻断合入前执行完毕，不额外超额付费重跑；后续 prompt 变更已由上方 `run_historical_replay.py` 与 `test_p5_evidence_consumers.py` 确定性全量验证。长期经济净改善因 `N=6` 仍严格判为 **`INCONCLUSIVE_SMALL_N`**。

---

## P4–P5 遗漏根因与后续工程验收原则

1. **已证实的过程性根因（及尚不能确认项）**
   - **需求与证据范围未覆盖**（已证实）：P4/P5 前期契约把注意力集中在“P0 防未对齐净流伪装成已成立反证”和“P2 `require_outside_at_t0` 机械边界”，将上游现货流时钟与 `RV24` 视为已由旧模块产出的背景，未列入 P5 放行门禁范围。
   - **模块间数据契约不完整**（已证实）：FMZ `micro_flow_for_horizon`、Workbench `expiry_gamma` 与 `lock_levels` 之间各自演化字段，`locked_compare` v4 直接丢弃 `flow_window_basis` 和 `pin_source`，跨模块边界缺少共享 schema 校验。
   - **单元测试通过但端到端数据不可用**（已证实）：上游 producer 测试只断言 `micro_flow` / `realized_vol` 字段存在或数值范围，下游 consumer 测试手工构造了带时钟的合格 dict fixture，两边各自全绿但中间链路从未串联跑通。
   - **安全降级掩盖功能长期失明**（已证实）：`OBSERVED_UNALIGNED`、`UNKNOWN` 和 `same_measure=False` 正确阻断了不合格证据，却导致现网 `snapshots.jsonl`（0/12）、`locked_candidates/series`（0/5）与 `expiry_gamma`（0/4）长期退化而服务健康检查与发布审批无感知。
   - **Reviewer 局部合规审查替代使命兑现审查**（已证实）：前期审核聚焦静态语义安全、前视防泄漏与 fail-closed 行为，未反向质询“现网真实数据经过链路后 Cat D / Pin / RV24 实际可用率是否 > 0”。
   - **尚不能确认项**：跨工作线切换 `daily_r16@3`（`3a3f850e`）时为何未同步检出 `24576` token 预算在 `@1.2-p5-candidate` 上触发截断，属独立部署配置与模型长度交互问题，非现有本地代码可单方面证实。
2. **本次遗漏暴露的测试盲区**
   - 仅验“降级不出错”而不验“正常真实上游样本能点亮 `Cat C / Cat D / RV24`”；缺少 `FMZ -> fact_context -> case_file -> player_input` 与 `expiry_gamma -> lock_levels -> locked_series -> tracking -> hold` 全链路串跑测试。
3. **已补充的正向端到端验收**
   - `test_p5_flow_e2e_chain.py`（从真实 `aggTrade`/`kline` 喂入 `DemoRuntime` 到 `p0_strict@1` 四窗口对齐与 9 类对抗降级）；`test_p5_pin_rv_chain.py`（从 `expiry_gamma` 到 `case_file`/`compare`/`health`/`locks.js` 全链）；`test_p5_evidence_consumers.py`（`live_5m Pin`、20m SLA、`Cat C/D` 正向触发及未对齐隐藏）；`xx-eu` 真实回放与 6 次模型消费闭环。
4. **真实生产数据（`xx-eu @ 3a3f850e`）可用性与资格状态**
   - 截止本次验收，`xx-eu` 现网实测：`snapshots.jsonl` 对齐流 `0/12`、`expiry_gamma.json` 含 `pin_source`/`pin_method` `0/4`、`realized_vol.rv_computed_at_ms` 缺失、最近 5 组 `locked_candidates/series` 对齐流与 Pin 血缘均 `0/5`、`11-24 16:00Z` `L4.expiry_vol.asof_ms=null`——三路风险证据链在现网均尚处失明状态，需待安全窗口切换至本分支 `2.0.7` 后重新采集自然运行时段以转绿。
5. **今后长期保留的最小验收原则**
   - 每条核心决策证据链同时登记四象限：**Production**（原生值/单位/计算与可用钟/来源）、**Transmission**（跨存储与序列化不丢钟不降维）、**Consumption**（正式消费者接纳与降级分离且真模型可消费）、**Operational Coverage**（`xxproject_server_health_v1.py` 暴露 `QUALIFIED_ACTIVE`/`QUALIFIED_NO_EVENT` 与 `BLIND_*`/`WARMING_UP` 区分）。
6. **严禁引入的过度工程**
   - 不新建独立时序监控服务、大数据仓库或人工事件触发配额；不以放松 `20m`/`60m`/`90m` 时效门禁或补造时钟伪造高覆盖率；所有能力观测直接复用既有 `xxproject_server_health_v1.py` 与 `review_snapshot.json`。

---

## 4. 任务书六问明确裁决

1. **原来哪几个钟、来源和字段断路？如何证明如今恢复真实信息流？**  
   - Spot Flow 缺 `start_ts_ms`/`end_ts_ms`/`source`/`symbol` 及累计闭合柱 `cum_session_id`/`cum_cvd_btc`/`cum_gap_count`，且缺失时误用 `now_ms()`，`locked_compare` v4 丢弃凭据；Pin 缺 `pin_source`/`pin_method`/`pin_status`/`expiry_ms` 与 `live_5m` 主线且无 20m SLA；L4 `RV24` 缺 `rv_computed_at_ms`/`source`/`status` 与 `L4.asof_ms`。修复后由 `test_p5_flow_e2e_chain.py` / `test_p5_pin_rv_chain.py` / `test_p5_evidence_consumers.py` 的全链路正向测试证明真上游数据完整抵达 `P0`、`tracking` 与 `Hold`，并由 `--require-evidence-channels` 在运行期区分 `QUALIFIED_NO_EVENT` 与 `BLIND_*`。
2. **11-23 案例在正确定时资格下，哪项证据本应可见、哪项仍属 `UNKNOWN`？**  
   - `11-23 t0`：`L3 ATM IV (51.77%)` 与经 `L3.expiry_walls.asof_ms` 离线重构时钟后的 `RV24 (92.59%, RECONSTRUCTED_OFFLINE)` 可见（未重构原始归档仍为 `UNKNOWN`）；现货主动流因历史包无 `end_ts_ms`/血缘仍为 `OBSERVED_UNALIGNED`。`11-23` Hold（`16:45:20Z`）：`live_5m Pin 64,000 -> 60,500` 本应立即可见并触发 `Cat C`；现货主动流仍为 `OBSERVED_UNALIGNED` 且未对齐 `232.01` 在持仓卡片被严格隐藏。
3. **修复对入场与 Hold 复审触发频率带来什么变化？是否产生重复/误触发？**  
   - 入场决策方向与 `P5` `M1/require_outside_at_t0` 约束均未改变（`Call 1–4` 无新增拒保或误选）；Hold `38` 行扫描显示在 `live_5m Pin` 合格化后仅于首次结构下移 `64,000->62,000` 唤醒 `Cat C`（`Pin=60,500` 步进更新，不重复抖动），`Cat D` 严格锁死在 `OBSERVED_UNALIGNED`（0 次误触发），超 20m 归档正确降为 `STALE`。
4. **P5 是否能同时理解方向优势与路径风险？哪些是实证，哪些是假说？**  
   - 实证：`Call 6` 正面同时消耗“现货未对齐盲区”与“`live_5m Pin 64,000 -> 60,500` 已落入 `62,000` 短腿下方”配合 `step_9` 收缩，给出结构化 `REVOKE (S1)`；`Call 1/3` 明确披露 `RV24 为 UNKNOWN`。未证实假说：`RV24 (92.59%/89.78%)` 虽无害送达 `Call 2/4` 牌面且未扭曲决策，但模型未在正式中文输出中逐字引用该数值，“RV 背景能否长期改善初始选边”仍属未证实假说。
5. **RV24 / mark IV / p_touch 中哪些进正式输入，哪些保留参考，为什么？**  
   - **进 `p0_strict@1` 正式背景参考（非机械规则）**：当期合格的 `ATM IV`、短腿盘口 `short mark_iv` 与经 `qualify_expiry_realized_vol` 合格化的 `BTC-PERPETUAL 1h` 闭合柱 `RV24`——用于提供波动率背景参考但不替代 `expected_move_usd` 距离尺度。  
   - **明确排除出正式输入**：`RV4h`（短窗样本噪声大、未校准）与 `p_touch_short`（模型假设敏感且易伪精度误导 AI 概率判断），目前仅限离线研究归档。
6. **当前 M1 触界退出与潜在执行磨损的长期经济结论是否仍为 `UNIDENTIFIED`？**  
   - **是，仍为 `UNIDENTIFIED`（本轮小样本验真标记 `INCONCLUSIVE_SMALL_N`）**。`N=6` 次单局对比且 `11-23 16:45Z` Arm A/B 均因 `0.09%` 极限余量输出 `REVOKE`，无法替代多日独立样本与真实可成交双向盘口报价统计。

---

## 5. 独立 Reviewer 审计意见

- **固定模型 Reviewer（`5b014b17`，只读三轮独立审查）最终裁决**：**`FIX_READY_APPROVED`**
- **累计缺陷闭环清单**：
  - **Round 1（2 HIGH + 3 MEDIUM）已全部闭环**：① `L4.expiry_vol` 将 `rv_{fetch_failed,duplicate_conflict,insufficient_bars}` 映为 `quality="unknown"` 并豁免仅 `expiry_iv_stale` 误伤；② 打通 `tracking -> decision.json/review_snapshot.json/audit_record -> player-audit.js`（补全 `PIN_EVAL_MODE_CN`）并以真实 `renderPlayerAudit` Node E2E 验证；③ 修复 Hold `include_rv24=False` 虚警盲区、阻断历史 `pin_stale` 跨日继承、`Cat D` & Section 1/2 阻断未对齐 `t0`/`last_review`、同步 `release_candidate_manifest.json` 状态与 `docs/p5/*` 口径。
  - **Round 2（1 HIGH + 1 MEDIUM + 1 LOW）已全部闭环**：① `xxproject_server_health_v1.py` 将 `"degraded:backfill_mixed"` 加入 `warmup_only_reasons`（配合真实 FMZ `2.0.7` `BarAssembler.backfill_from_klines` + 实盘成交混合态单测返回 `CAP_WARMING_UP`，仍严格失败 `gap`/`window_truncated`/`source_conflict`）；② `astra_player_review_v1.py` 提取共享 `_hold_flow_delta_allowed` 门禁防止第 3 节 `_format_dual_delta_lines` 在“先对齐后退化/过期”时泄漏 `4h CVD` 与流向时钟推进，并由 `tracking.evaluate_hold_management` 源头净空；③ 刷新 `release_candidate_manifest.json` 伴随修改组件 LF SHA-256 与时间戳。
- **Q1–Q6 终验确认**：三路通道端到端贯通、契约时空与血缘严格校验、识别出的夹具盲区已补齐、诚实维持 `FIX_READY_NOT_DEPLOYED` / `PENDING_NATURAL_CONFIRMATION` / `INCONCLUSIVE_SMALL_N`、无重复触发/伪精度/`unclocked CVD` 泄漏/误拒保、且完整保留 P5 安全边界（`read_only_demo=True`、`execution_allowed=false`、`SHADOW_ONLY`、`@1.2-p5-candidate`、`HOLD_REVIEW_SYSTEM_PROMPT`、`legacy_v1`、`p2_strict@1`、`SHADOW_EXITED`）。
- **需保留的反对意见、残余风险与验证边界声明**：
  1. **生产自然发牌与真模型二次微调未重跑风险（`PENDING_NATURAL_CONFIRMATION`）**：`xx-eu` 当前仍安全运行 `3a3f850e`（FMZ `2.0.6`），三路证据链尚未在真实线上自然发牌转绿；6 次受控真模型调用发生于 `include_rv24=False` 与 Section 1/2/3 防泄漏加固之前（后续仅由 `run_historical_replay.py` 与 `28` 项确定性用例验证），需在 `08:05–15:40 UTC` 安全窗部署后首个真实自然周期用 `SESSION_CONTEXT_REQUIRED=1` 与 `python3 tools/xxproject_server_health_v1.py --require-evidence-channels` 完成实盘闭环（注意健康检查该开关代码默认 `False`，运维自检必须显式传参）。
  2. **既有基线回归与环境跳过说明**：35 文件核心套件中的唯一失败 `tests/test_player_initial_recovery.py::test_formal_timer_to_native_tracking_and_fixed_check` 经源码比对证实为 `3a3f850e` 基线既有问题（`795048bd` 引入 `at - dossier.MINUTE` 闭柱门限与该用例 `now[0] = tick` 冲突，与本分支无关）；`test_real_initial_public_view_context_refs_keep_missing_source_clock_none` 因本地无 `.artifacts` 目录跳过；`locked_compare._producer_facts` 保留底层数值供离线诊断，防泄漏由 `tracking` + `review` 双门禁承担。
  3. **极端成交量淘汰容量提醒**：FMZ `2.0.7` 将 `bar_history_size` 升至 `10000`（`100,000 BTC`）可覆盖绝大多数日内窗口，但极端单日超 `100k BTC` 爆量仍会按契约标记 `truncated=True` 降级报警，绝不静默伪造完整窗。
  4. **独立 Reviewer 工具边界声明**：`reviewer`（`5b014b17`）仅配置只读文件工具、无终端 CLI 能力；其结论基于对全部修改文件、测试源码、`release_candidate_manifest.json` 及 `docs/p5/` 交付件的逐行独立核验，CLI 测试与构建结果由主 Agent 在本地实跑产出并提供可复现命令记录。

---

## 6. 版本、构建产物与安全窗部署/回滚手册

- **权威远端**：`https://github.com/x18055868223-png/xxproject`（远端名 `xxproject`）；`origin`（`signal-audit-deploy`）仅作静态前端镜像，严禁作为主基线。
- **Git 与实机基线**：
  - 线上运行：`xxproject-server-3a3f850e8642-5797ba0e28fc`（`daily_r16@3`，FMZ `2.0.6`，`execution_allowed=false`）——**本轮未做任何实机覆盖或静默切流**。
  - 用户根目录 `demo/最新交付物/neutral_regulation_demo_fmz.py` 仍保持与现网一致的 `2.0.6`（`FIX_READY_NOT_DEPLOYED` 期间**不可直接复制到 FMZ**，避免“远端未发先改本地交付物”）。
  - 候选修复分支：`codex/p5-evidence-pipeline-fix`（于 `.worktrees/p5-evidence-pipeline-fix` 提交并非强推至 `xxproject/codex/p5-evidence-pipeline-fix`）。
- **安全窗口部署与 1 步回滚（仅限 `08:05–15:40 UTC` 安静窗口，严禁 `15:50–08:00 UTC` 执行）**：
  1. 部署：签出/合并 `xxproject/codex/p5-evidence-pipeline-fix`，使用 `deploy/xxproject_server/deploy_release.py` 原子发布并重查 `astra_release_check_v1.py` 与 `python3 tools/xxproject_server_health_v1.py --require-evidence-channels`；同步更新根目录 `demo/最新交付物/neutral_regulation_demo_fmz.py` 至 `2.0.7` 并重启 FMZ `read_only_demo` 节点。
  2. 现网 `16:00Z` 问题可选临时止血：若在尚未合并 `2.0.7 + max_tokens=32768` 前遇下次发牌窗口，同一 `08:05–15:40 UTC` 安静窗口内可将 `/opt/signal-audit-tools/player-plan.json` 1 步切回 `plan.daily_r16.v4_rollback_formal_a.json`。
  3. 回滚：线上原子切换软链回 `xxproject-server-3a3f850e8642-5797ba0e28fc`（或 `a164dbdf`）并保持 `execution_allowed=false`。
