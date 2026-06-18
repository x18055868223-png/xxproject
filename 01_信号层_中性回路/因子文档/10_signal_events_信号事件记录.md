# 10 · signal_events（信号事件记录）

> 模块：① 信号层 · 输出层（不进 `module_results`）
> canonical：`demo\signal_events.py:SignalEventTracker` + `demo\signal_review.py`（审计卡组装/渲染）
> 最后核对：2026-06-18（demo v1.3.0：紧凑体仍被 FMZ 截断 → 采纳 JSON 留档标准：`build_audit_record` 全量 v1.0 记录写 `signal_review.jsonl`（唯一事实源），`render_push_brief` 只推 ≤160 字符单行简要；新增 `audit_archive/` 流程目录 + 样例。标准见 `docs/信号审计JSON推送与静态Web标准_v1.0.md`）

## 1. 一句话定位
当 NeutralRepair 进入 `NR_REPAIR_CONFIRMED`（窗口真正打开）时，落**一张信号审计卡（Signal Review Card）**：把当时的**全量因子截面 + EDB 倾向推理链路与参与项 + 冲突比例/原因 + 阻断项/内容 + 置信分解 + 最终结论**定格存档并可推送。不是判断因子，是观测/审计层。**关键**：完整论证数据本就在 `factor_snapshot.edb`（evidence/agreement/coverage/veto_reason + 新增 confidence_decomposition），审计卡是**组装+序列化**，不重算、不改方向/置信。

## 2. 当前具体实现
- `signal_events.py:maybe_record(neutral_repair_signal, factor_snapshot, runtime_facts)`：仅当 `state==NR_REPAIR_CONFIRMED` 且 `episode_id` 未记过才记（去重 `seen_episode_ids`）；返回 True 表示本 tick 生成了新信号；FIFO `signal_event_max_count`(10) 条。`_build_event` → `signal_review.build_signal_review_card(...)`，**每条事件即一张完整审计卡**（结构见 §7）。
- `signal_review.py`：`build_signal_review_card` 组装卡；**`build_audit_record(card, config)`** 把卡映射为 **v1.0 标准记录**（13 分区：schema/identity/quality/market_context/decision/display_layers/signal_window/blocking/reasoning/conflict/factor_cross_section/delivery/integrity；缺失=null + `quality.degraded_sources`，`integrity.record_hash`=sha256，证据带 `source_ref`/`effective_weight`，display_layers 只存摘要+source_refs）；**`render_push_brief(card, config)`** 出 FMZ ` @` 简要（单行 ≤160 字符，决策前置 + 审计指针置尾，§6.3）；`card_digest` 出信号桥 digest；`build_sample_review_card` 出合成样例（供自检/样例生成）。**为何**：FMZ `@` 推送有长度上限（紧凑 ~324 字符仍被截），故全量只入 jsonl、推送只发简要。旧文本渲染器 `render_review_card_push`（四层/紧凑）保留但运行时不再调用。
- `recorder.py:_signal_review_table`（替换旧 `_signal_events_table`，**面板 id 仍 "signal_events"**）：最新卡按 Style A 分区逐行渲染，历史压成单行。
- `main.py:_emit_signal_review_card`：新信号时**先** `build_audit_record`→写 `demo/logs/signal_review.jsonl`（全量 v1.0 记录，先落盘）**再** `signal_review_push_enabled` 时 `render_push_brief`→`@` 推送；推送 try/except 降级简讯不吞；`build_audit_record` 内嵌简要用 `_safe_brief`（简要异常不破坏记录）。
- `main.py:_emit_push_self_test`：`signal_review_push_test=True` 时**启动推一次简要**（无需信号），自检推送链路+样式；带「【推送自检·非真实信号】」前缀、幂等、关时静默。
- 流程目录 `audit_archive/`（本工程）+ `tools/audit_sample_gen.py`：按标准生成 source/cards/public·data·index/state/samples；**前端样式待用户打磨**。

## 3. 关键阈值 / 配置
`signal_event_max_count=10`。审计卡：`signal_review_enabled`(True) / `signal_review_push_enabled`(**False**，用户显式开) / `signal_review_push_test`(**False**，自检：True 时启动推一次简要查链路+样式，用完改回) / `signal_review_recorder_name`("signal_review") / `audit_static_base_url`(""，设静态站点 base 后简要末尾给 `审计:<base>/c/<id>`，否则 `详见FMZ Log #<id>`)。（`signal_review_push_compact` 仍存但仅作用于已停用的 `render_review_card_push`，运行时推送走 `render_push_brief`。）`utils.fmz_push` = `Log(text+" @")`（**v1.1.0 修复**：原 `text+"@"` 无空格分隔，多行正文末尾的裸 `@` 可能只进普通日志、不进推送队列，即用户实测「出信号未推送」；FMZ 以空格分隔的 ` @` 结尾触发 app/邮件推送，20s 限频取最后一条，全工程仅此一处推送点）。`_emit_signal_review_card` 调用点加 `try/except`：渲染异常降级为带卡号一行简讯，不吞推送。无算法阈值，纯观测。

## 4. 整合中的路径修改
**零代码改动**。但它是整合的两个关键基础设施支点：
1. **校准数据底座**：运行时本就把每 tick 写 `demo/logs/snapshots.jsonl`（schema≥v0.5.x 已含完整 edb/skew/gamma_regime payload），window-open 子样本 = `edb.precondition.nr_active`。`tools/calibrate_edb.py` 直接读它出经验分布——**校准无需新增埋点**。
2. **桥接来源**：`signal_build/signal_bridge.py:export_signal_evidence_package` 已加 `signal_review` **digest**（card_id+结论+冲突/阻断摘要，**不含全量截面**，保持契约窄；含 `test_signal_bridge.py`）。

## 5. 当前目标 / 待办
- 实盘累计 snapshots ≥2–4 周覆盖不同区制（当前日志退化，多为同一离线 fixture），才能跑 calibrate_edb.py 定 P0 阈值。
- 可选：`tools/label_forward_returns.py` 给事件加前向收益 label 做 IC。

## 6. 边界与陷阱
- 它只在 `NR_REPAIR_CONFIRMED` 记，平时 tick 只出 `观察摘要`（数据健康）；`信号综述`（情况/倾向/目标/策略，不点名）在 `maybe_record` 返回 True 时由 `recorder.build_signal_brief` 输出一次。
- 是内存事件日志（FIFO 10 条），持久化复盘看 `snapshots.jsonl` 与 `signal_review.jsonl`（后者每条是全量审计卡）。

## 7. 信号审计卡结构（Signal Review Card v1.0，2026-06-04）
一张卡 = 同一 `card`，多态渲染（状态栏 Style A 分区 / **FMZ ≤160 简要 `render_push_brief`** / **JSONL 全量 v1.0 记录 `build_audit_record`**（唯一事实源）/ 桥 digest）。卡结构本身 v1.0 起未变（v1.3 只是换留档/推送形态；全量恒在 jsonl）。下面 `card` 内部结构：
- `conclusion`：lean/support_label/side_hint/confidence/**calibration_state**(v1.1.0)/next_action + 中文。置信链与综述显式标「未校准·非真实胜率」。
- `window`：nr_state/is_active/episode_direction/peak_m_die/event_count_merged/anchor_score/nd。
- `reasoning`：edb_score、`evidence[]`（key/vote/weight/eff_weight/**contribution_pct** 按贡献/aligned/gloss_cn/detail）、agreement、coverage、`confidence_decomposition`{strength,agr_factor,cov_factor,ggr_mult,conf_pre_veto,confidence_final}。
- `conflict`：`ratio`=1−agreement、level、aligned_keys、`dissent[]`、explanation_cn。
- `blocking`：has_block、`hard_veto`{veto_reason,zh,evidence}、`soft_gates[]`（窗口未开/置信不足/方向中性）、unblock_hint_cn。
- `factor_cross_section`：anchor/tmvf/micro_flow/funding/m_die/neutral_repair/macro_pressure/**gex_info**/gamma_regime/skew —— 自包含全量截面。
- `final_conclusion_cn`。
> EDB 仅新增 `confidence_decomposition` 展示字段，**方向/置信/分类阈值零改动**。校验：`tools/signal_review_check.py`（阻断/可交易/窗口未开三例）+ 单文件 static/runtime 全绿。
