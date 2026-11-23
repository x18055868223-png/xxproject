# P5 只读影子验证与回滚预案（P5_SHADOW_AND_ROLLBACK）

**门禁界线**：本方案用于从隔离状态 **`READY_FOR_SHADOW`** 走向未来评估 **`READY_FOR_PRODUCTION`**。在未经用户下一次明确批复前，**零付费调用、零推送、零部署、零触碰现网 `@1.0` 主调度与交易执行权限**。

---

## 1. 两阶段影子验证选项（低风险渐进）

### 选项 1｜零付费离线影子回放（推荐立即可批）
- **范围**：只读拉取 `xx-eu` 上 **2026-11-20 至 2026-11-23 未见过的新自然牌 `evidence/source.json` 与生产 `@1.0` 原答**（严禁读取任何 `outcomes/` 事后盈亏或已到期轨迹胜负）。
- **验证动作**：
  1. 在本地 `.worktrees/player-p5-candidate` 运行 `build_input(..., input_profile='p0_strict@1')`，对比现网 `legacy_v1` 发现了多少以前未标明的 L2 长短腿偏移、现货 `OBSERVED_UNALIGNED` / `UNKNOWN`、以及回填 ARD；
  2. 将线上已有 `@1.0` 历史原答交由 `@1.2-p5-candidate` 的 `validate_initial_reply_contract` 做只读干跑，定量审计现实生产回答中最常见触发的 M 正则与 J1 分界，提前验证零误拒边界。
- **预算与影响**：**\$0 费用**、0 次 LLM 请求、0 服务器变更。

### 选项 2｜有界限双臂旁路影子试点（需用户单批模型预算授权）
- **样本与隔离**：选取 **5 至 10 张全新未见自然牌**（推荐优先回放 `2026-11-20..23` 新牌或在独立 `/var/lib/xxproject/workbench/shadow_p5_trial` 根目录旁路实跑），按**同证据双臂对照**：
  - 对照臂：**`player_initial_system@1.0 + p0_strict@1`**
  - 候选臂：**`player_initial_system@1.2-p5-candidate + p0_strict@1`**
- **执行保护**：保持 `execution_allowed=false`、`retries=0`、单臂单手硬上限 1 次调用；绝不复用或修改生产 `/var/lib/xxproject/workbench/shadow_dossiers` 目录、`astra-player-deal.timer` 或 `workbench` 首屏状态。
- **预算与耗时估测（按 `deepseek-v4-pro`, `reasoning_effort=high`, `max_tokens=24576`）**：
  - 每张牌**仅初始请求输入**合计约 `8,200–8,600 tokens`（对照臂 ~4,050 + 候选臂 ~4,150；系统 Prompt 增幅仅 ~+61 tokens）。
  - 模型可见思考 + 回复输出取决于 `high` 推理深度，参考 P4 可见长度估算单次回复约 `1,500–4,500 output/thinking tokens`（具体金额按账户实时模型费率计，且应为双臂设定单局硬预算帽，超出立即终止）。

---

## 2. 影子期核心观测与晋级 / 降级判定准则

**核心原则**：绝不以某一侧事后涨跌、模型“回复更长”或“格式工整”作为晋级依据！只看下列**过程判断与工程稳定性指标**：

1. **首次合规与误拒审计**：候选臂 `CONTRACT_VALIDATION_FAILED` 率应 `< 5%`，且人工盲抽检查确认**无明显有效中文表达被正则误拒**。
2. **决策结构偏移与模板化**：比较同证据下 `@1.0` vs `@1.2-p5-candidate` 的 `Accept/Reject/Unknown` 与 `Put/Call` 分歧局，人工盲审分歧是否基于真实 P0 反证（如现货未对齐/L2 距离），以及是否出现机械模板化或 Reject 过度保守偏移。
3. **C 的独立可证伪增量（关键检验）**：当后续 Hold 复核触发时，统计：
   - 冻结 `accepted_conditions` 中的第（4）条是否全由真实可观察源构成（无晚于 M 的触发、无不可补查的幻想指标）；
   - 在 `CONDITION_TRIGGERED` **尚未发生** 的持仓期间，是否真实记录到基于 `new_qualified_data_present=True` 触发的独立 C 复核信号及合理分析；
   - 是否遵守“无新合格数据时不得声称 C 已被证实仍有效”。
4. **明确停止线与降级触发条件**：
   - 若因 P2 正则或 J1 导致的合理文本误拒率 `> 5%`，或发生任何 `ContractValidationError` 诱导二次付费、状态串扰 → **立即停止**，切换或改造正则；
   - 若影子测试证明 `@1.2-p5-candidate` 引起显著保守 Reject 漂移，或 C 在连续 5+ 局中普遍空洞、晚于 M 或零增量 → **立即放弃 `@1.2-p5-candidate` 中的 C 约束，无缝降级为方案 D（纯 `p0_strict@1` + `p2_strict@1` + 公理修正）**。

---

## 3. 回滚与状态区分

- **一分钟无损回滚（当前与未来发布通用）**：
  - 当前生产基线 `b3d79695` 位于独立目录 `/opt/signal-audit-tools` 且本地 `.worktrees/astra-audit-minimal-fix` 未动一字；
  - 未来即便试运行切换了计划中的 `prompt_version` / `input_profile`，只需将 `player-plan.json` 中的 `prompt_version` 切回 `"player_initial_system@1.0"`（或还原 `b3d79695` 6 个 `.py` 文件），历史 `@1.0` 与候选卷宗因全部带有独立版本与 `input_profile` 标签互不干扰、互不覆写。
- **`READY_FOR_SHADOW`（当前已达成）vs `READY_FOR_PRODUCTION`（未达成）**：只有完成上述选项 1 零付费回放 + 选项 2 有界双臂影子验收、人工盲审通过、并另外取得明确生产发布授权后，方有资格申请合并与生产部署。
