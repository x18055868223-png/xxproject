> 当前交付物口径（r2.1 / 2026-06-19）：本目录仍是 FMZ 可部署单文件入口；当前信号层 `demo_version=1.3.0`，当前执行层 `STRATEGY_VERSION=2.5.0` 且交易门默认关闭。审计前端、GEX rank 与 Gemini LLM 复核属于服务器旁路/展示链路，不代表执行层启用。
# 最新交付物（FMZ 可直接部署单文件）

> 用途：本文件夹**只放最新的、可直接部署到 FMZ 的两份单文件 Python 策略**——拿来即用、即测，与因子文档/源码分离。
> 每次迭代覆盖更新本文件夹，并在 [`../副本快照/`](../副本快照/) 留一份带版本+时间的快照。
> 最后更新：2026-06-18　|　信号层 **v1.3.0**（紧凑推送仍被 FMZ 截断 → 采纳 JSON 留档标准：全量 v1.0 记录写 `signal_review.jsonl`，FMZ 只推 ≤140 字符简要；JSON 字段已对齐静态审计页面 `signal_cards/index.json + signal_cards/*.json` 契约）。标定阈值仍 PLACEHOLDER、`ALLOW_TRADING` 仍 False、闸 B 实盘仍待实盘复观——**非"策略已验证"**。

## 文件清单

| 文件 | 层 | 版本 | 运行边界 | 源 / 重生成 |
|---|---|---|---|---|
| `neutral_regulation_demo_fmz.py` | ① 信号层·中性回路 | demo **1.3.0** / schema `nrd.schema.v1.0.0`（全量 v1.0 审计 JSON 写 jsonl + FMZ ≤140 简要推送 + 推送自检） | **只读观察**：不选腿、不报价、不下单（`read_only_demo=True`） | `中性回路 - opus4.8/`；`tools/build_fmz_single.ps1` 由 `demo/*.py` 合成 |
| `spm_calendar_protected_short_v1.py` | ② 执行层·Deribit S:PM 垂直信用价差卖方 | STRATEGY_VERSION **2.5.0**（v3 重构（风险严重度→仲裁+审计整改 / 持仓后链路补强 / 开仓活动跨轮持久 / 对冲场所可选）：垂直唯一 + 单一 `run_cycle` 主链 + 交互控制台/短确认码硬授权 + 软授权止盈 + 低成本退出 + 对冲生命周期 + **风险触发主动退出(EXIT_PREFERRED·可越价吃单)/对冲(HEDGE_READY)**，场所 **Deribit BTC-PERPETUAL 默认 / Binance BTCUSDC maker-0 可选**） | **全空跑**：5 门控 `ALLOW_ENTRY/EXIT/HEDGE_TRADING` + `KILL_NEW_RISK` + `EMERGENCY_REDUCE_ONLY` 默认 False，仅展示意图/控制台、不真实下单 | `demo/execution_build/realsrc/`；`python build_bundle.py` 由 `src/*.py` 合成 |

## 部署到 FMZ
1. FMZ 控制台 → 策略库 → 新建策略 → 语言选 **Python**。
2. 整份粘贴对应单文件内容，保存。
3. 信号层默认只读；执行层默认空跑（`ALLOW_TRADING=False`）。**上线真实交易前**务必确认三开关 + 两闸（见总纲上线闸门）。
4. 可选项：
   - 信号审计推送（v1.3）→ 设 `signal_review_push_enabled=True`：出信号时 FMZ ` @` 推一条 **≤140 字符简要**（决策前置 + 审计指针）；**全量 v1.0 记录写本机 `demo/logs/signal_review.jsonl`**（唯一事实源）。
   - **推送自检** → 设 `signal_review_push_test=True` 重启：启动即写一条合成审计记录到 `demo/logs/signal_review.jsonl`，并推一条「推送自检·非真实信号」简要；推送末尾会标 `JSON已写` 或 `JSON失败`。确认后**改回 False**。
   - **静态审计站点** → 设 `audit_static_base_url`（如 `https://audit.example.com`）后，简要末尾给 `审计:<base>/c/<id>`；不设则给 `详见FMZ Log #<id>`。当前静态页面定稿归档：`C:\Users\Xu\Documents\信号审计前端页面设计\archives\signal-audit-final-20260618`；数据契约为 `signal_cards/index.json` + 单卡 JSON。
   - gex_info 数据增强 → 设 `gex_info_token`（或环境变量 `NRD_GEX_INFO_TOKEN`）；不设则该层自动降级、不影响运行。

## 验证状态（2026-06-04，全绿）
- 信号层：`static_validate_demo.ps1`（FMZ 同步+交付摘要+无未完成标记）/ `runtime_check_demo.ps1`（离线 smoke）/ `tools/gex_info_check.py` / `tools/signal_review_check.py`。
- 执行层 **v2.5.0（2026-06-18：v3 重构 + 对冲场所可选 + 开仓活动跨轮持久 + 持仓后链路补强 P0①②③ + 风险严重度→仲裁 C.2 + 风险链路审计整改 F1-F3/C1-C3）**：`build_bundle.py --check`（语法+名称解析+无 KPF/无日历残留）/ `tests/run_all.py`（**210 通过**）。完整说明见 `docs/执行层完整说明_v2.1.md`（含审计发现）。
  - v2.4.0：入场冻结 `entry_risk_anchor`；`manage_cycle` 每轮经 `hedge_risk.evaluate_position_risk` 算 `tail_risk_state`，驱动仲裁 `exit_preferred/hedge_ready`——风险严重偏退出(EXIT_PREFERRED，需 `风险退出授权`)、持续且对冲更优偏对冲(HEDGE_READY)；对冲数量改用**结构净 delta(短−保护)** + 方向符号核对（反向禁新增对冲）。**OFFLINE 默认仅 EXIT_PREFERRED 活跃**（HEDGE_READY 待总线 edb/ggr）。
  - v2.5.0（审计整改）：F1 风险退出**独立预算**(`RISK_EXIT_MAX_SPEND`)+**可越价吃单**(成本封顶)+越价不可成交则**回退对冲**；F2 控制台「风险」行+风险退出授权码+操作提示；F3 短腿盘口缺 delta/IV 显式**数据缺口**；C1 Deribit 对冲下单**成交确认**(等待+撤残+复查)+None 盘口守门；C2 **孤儿对冲清理不受 `ALLOW_HEDGE_TRADING` 阻断**；C3 `no_unknown_orders` **真实活动订单查询**(fail-closed)+启动恢复按在途活动重校验。
  - **FMZ 交互须真实机器人空跑验收**（`GetCommand` 在回测系统不生效）。需在机器人「交互」区配置按钮：`执行`(字符串=方案确认码)、`拒绝`、`授权止盈`(字符串=持仓授权码)、`撤销授权`、`风险退出授权`(字符串)、`急停`、`恢复`。状态栏顶部「交互控制台」给出当前阶段/门控/信号接收/待批方案确认码/软授权码/止盈资格/退出活动/对冲/操作提示。

## 更新约定（重要）
- 本文件夹**永远是"最新可部署"**：每次源码迭代 → 重生成单文件 → 覆盖本文件夹两份。
- 同时在 `../副本快照/` 新建 `YYYY-MM-DD_信号<ver>_执行<ver>_<特性>` 子文件夹，拷入两份带版本+时间后缀的副本 + `快照说明.md`。
- 本文件夹**不放因子文档/源码**，只放成品单文件。

## v1.3.0 变更（JSON 留档 + 简要推送，2026-06-18）
紧凑推送（~324c）实盘仍被 FMZ 截断 → 采纳《信号审计 JSON、FMZ 推送与静态 Web 标准 v1.0》（`docs/`）。**只改留档/推送/可观测，不改方向/EDB/阻断/执行契约；nrd schema 仍 v1.0.0。**
- **全量本地 JSON（唯一事实源）**：`build_audit_record` 把审计卡映射为 v1.0 标准记录（13 分区），单行写 `signal_review.jsonl`；缺失=null、`integrity.record_hash`、证据带 `source_ref`/`effective_weight`。
- **FMZ 只推简要**：`render_push_brief` 出 ≤140 字符单行（决策前置 + 审计指针置尾），全量证据链只进 JSONL/静态页。设 `audit_static_base_url` 后末尾给审计链接。
- **静态前端契约对齐**：`build_audit_record` 输出 `schema/identity/quality/market_context/decision/display_layers/blocking/reasoning/conflict/factor_cross_section/provenance/delivery/integrity`；`blocking.soft_gates`、`unblock_conditions`、`reasoning.agreement/coverage` 等按前端定稿结构生成。无法提供真实值的 GEX 仅输出 `data_status=MISSING` 占位，不伪造因子值。
- **旧长文渲染器退役**：v1.2/v1.2.1 的多行四层正文与 COMPACT 渲染路径已从当前交付文件移除；FMZ 仅保留短简讯。

## v1.2.1 变更（推送压缩·防截断，2026-06-17）
历史说明：v1.2.0 的四层推送（37 行/~1325 字符）实盘**仍被 FMZ 截断**（~190 字符处断、换行被拍平），于是 v1.2.1 曾改成 6 行/~324 字符紧凑体。该紧凑体在 v1.3 也被实测判定不适合 FMZ 推送，当前交付已退役该路径，保留全量 JSONL + ≤140 字符单行简讯。

## v1.2.0 变更（信号层·推送重设计，2026-06-17）
依据《信号审计推送格式重设计 v1.2》（`docs/`）。**只改推送渲染与可观测，不改方向/EDB 权重/阻断/执行契约；schema 仍 v1.0.0。**

- **A 推送正文重设计（历史路径）**：旧 `◎` 分段叙述 → 「**头部 + 四审计层（背景/修正/论证/冲突）+ 复盘索引**」固定结构。该长文路径已在 v1.3 退役，当前 FMZ 只推短简讯，完整审计进入 JSONL/静态页面。
- **B 推送自检开关**：新增全局 `signal_review_push_test`（默认 False）。**True 时启动即写一条合成 v1.0 审计 JSONL，并推一次合成样例简讯**（无需等信号），用于同时验证本地 JSON 写入和 FMZ ` @` 短推；带「【推送自检·非真实信号】」横幅、幂等、关时静默。验证完务必改回 False。

## v1.1.0 变更（信号层·维护版，2026-06-05）
基于《信号层倾向性论增层优化思路参考》（`docs/`）评估后落地。**评估结论：不引入完整 BIRL 引擎**——参考稿自身主张 shadow 优先，且其最有价值的项（`causal_discount`/`correlation_penalty`/`coverage_independent`）全部依赖本工程尚不存在的真实 `snapshots.jsonl` 标定；无数据期硬编码权重=给封版加一层不可调机器。当前 EDB 已实现其多数骨架（带 floor 的加权后验、一致度、覆盖、信息量死区、GGR veto/调制、平滑钩子、中文 trace）。本版只采纳两项**零标定依赖**的真实改进：

- **A 推送修复（确认 bug，P0）**：`fmz_push` 由 `Log(text+"@")` 改为 `Log(text+" @")`（FMZ 推送 token 须空格分隔，裸 `text@` 于多行正文末尾可能只进普通日志）；调用点加渲染异常 `try/except` 降级简讯。全工程仅一处推送点，与 FMZ 20s 节流无关。**治用户实测「出信号未推送」**。
- **B 置信诚实标注（零标定）**：EDB 新增 `calibration_state`（`config.edb_calibration_state`，现 `UNCALIBRATED`）；结论句/置信链/推送/桥 digest 均标「未校准·非真实胜率」。标定签收后翻 `CALIBRATED` 提示自动消失，无需改码。

押后清单（log-odds 内核 / 因果折扣 / 相关性惩罚 / 熵置信 / 状态机+迟滞 / 执行侧 BIRL-B）见 `01_信号层_中性回路/因子文档/11_BIRL论证增强层_评估与EDBv2待办.md`。schema 仍 `v1.0.0`（`calibration_state` 为新增非破坏字段）。

## 封版 1.0（信号层·工程阶段，2026-06-04）
基于《信号层全链路中文语义档案》审计三类判读后封版：
- **语义**：1.0 = 逻辑/集成正确、全链路全绿、只读/空跑安全；**非"策略已验证"**（标定后置，与 HANDOFF 红线一致）。
- **本轮真实缺口修复（observer 范围）**：A 机器侧歧义硬化（EDB 阻断/等待→机器侧置空 + `allow_downstream_evaluation` + 契约红灯；TMV 预览入 `preview_*`）；B Greeks 新鲜度进 SRD/GGR（过期→SRD `STALE`/GGR 丢钉，flip 安全门仍在）；C GEX `STALE` 预警带 + 文档对齐 v1.0.0。
- **押后（Phase 2，非 observer 阻断）**：SignalEvidence 桥真实快照映射 + 运行时导出 + 本机总线 + 消费闭环；JsonlRecorder 轮转。
- **单一 canonical 工件**：本文件夹两份 = 源仓库重生成的权威单文件；交付物快照/最新交付物/canonical 三者已收敛一致（旧 最新交付物 残留的 9145 行信号文件——非 canonical 源产物——已被权威构建覆盖）。
