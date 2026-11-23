# P5 有界影子验证与 Formal A 无损回滚预案（`P5_SHADOW_AND_ROLLBACK`）

**当前状态**：**`READY_BUT_NOT_DEPLOYED`**（代码 RC `c39b3d27` 已冻结并通过 `59cca70d` 独立 Review；现网 `xx-eu` 保持 `6311071c` + Formal A `daily_r16@2` 未改，**未发起新增付费调用、未推送、未部署**）。

---

## 1. 累计影子预算、已归档样本与 `6311071c` 准入缺口

- **累计活动上限（全轮共用，严格硬顶）**：最多 **`20` 次模型调用**、合计最多 **`¥20.0 RMB`**。
- **已用 / 剩余真实账本**：
  - 前次 P5 双臂试跑（`3` 张 `pre-6311071c` 牌 × `2` 臂，`deepseek-v4-pro`, `reasoning_effort=high`, `retries=0`, `execution_allowed=false`）消耗：**`6 / 20` 次调用**，峰值保守费用 **`¥3.62856`**（缓存计价 `¥3.588174`）；
  - 本轮闭环加固额外付费调用：**`0` 次 / `¥0.00`**；
  - 剩余可用影子额度：**`14 / 20` 次调用**、**`¥16.37144`**（最多再支持 `<= 7` 对双臂或 `<= 4` 对稳妥补验，任一上限先到即停，禁止从 Formal A 正式预算 `/var/lib/xxproject/llm/signal_llm_usage_ledger.json` 借额）。
- **已保存 3 对按最新硬化解析器 `c39b3d27` 离线重评结论（零读取 `outcomes/`）**：
  - `hand_8b126baf41e5883b410c1d06` (`H1_P4_unrun`)：Arm B `PASSED` (`Accept / put`, 4/4 C 非退化)；Arm A 在 P5 门禁下 `CONTRACT_FAILED` 且 `c_degenerates_to_m=True`（注意：仅作契约不合规记录，**不混同为 A 的方向判断错误**）。
  - `hand_89df3d02846e56192937f6df` (`Nov19_natural`)：Arm B `PASSED` (`Accept / put`, 4/4 C 非退化)；Arm A 在 `@1.0` 旧契约下含有限区间/网格违规。
  - `hand_d9598e9be936b9b777c5295f` (`Nov20_natural`)：经本轮跨节加固后，Arm B 由旧 `PASSED` 准确翻转为 **`CONTRACT_FAILED`**（`confirmation_excerpt` 拷贝了 `comparison`，触发 `WRONG_SECTION` + `j1_excerpt_duplicate_section` + `missing_m_mechanical_anchor`）。**即当前已保存双臂为 `2/3` 通过、`0` 误拒、`0` 假放行。**
- **外部 soak 缺口（唯一阻断后续影子补验与发布的原因）**：
  - 现网 `xx-eu` 上游 `6311071c` 于 `2026-11-23T16:49:44Z–16:49:57Z` 完成部署；
  - 当日 `16:00Z` 自然牌 `hand_a050f60ea6bb7d73acf68a3f` 在 `16:02:13Z`（部署前）发出，因此**截至目前 `post-6311071c` 自然发牌周期为 `0 / 2`**；
  - 依据 Gate A/C 门禁，严禁拿部署前历史牌凑数。须等 **`2026-11-24T16:00Z` 与 `2026-11-25T16:00Z`** 两个自然日发牌周期确认 `SOURCE_READY` 与正常发现/锁单链路后，方可用剩余额度对其中 `>= 2` 张合格新自然牌（总合格样本 `>= 5` 对）实施低预算双臂补验。

---

## 2. C 只读边界与降级停线标准

- **独立只读 C（`execution_allowed=false`）**：C 仅输出 4 要素持仓审查条件并在固定检查钟记录可验证的证伪线索，无自动交易、自动撤腿、提前止损或 Revoke 权；当前持仓期尚未观测到跨 M 前置证伪证实收益，保持 **`C_EARLY_WARNING_UNPROVEN`**。
- **补充影子停线准则**：
  1. 任意 `1` 例明确合理的中文正向表述遭到规则误拒 → 立即停下修规则，不得用小样本比例稀释；
  2. 单手模型失败不自动补发（`retries=0`），遇 `401/403`、预算触顶或上游 `UPSTREAM_BLOCKED` 立即止步并记为样本不足；
  3. 不得借对比 `@1.0` 契约不合规直接宣称 `@1.2` 具备长期市场胜率。

---

## 3. 正式回滚预案（唯一正式回退目标：Formal A，禁用 Route D）

- **回滚目标**：现网已验证 **Formal A (`player_initial_system@1.0` + `legacy_v1`)**，绝不切换未验证的 Route D。
- **两层无损回滚路径（全部仅可在 UTC `08:05–15:50` 安静窗执行，严禁 `16:18–08:00 UTC`）**：
  1. **首选 1 步计划回滚**：切换 `/opt/signal-audit-tools/player-plan.json` 至 [`plan.daily_r16.v4_rollback_formal_a.json`](../../deploy/player_dossier/plan.daily_r16.v4_rollback_formal_a.json)（`daily_r16@4`, `prompt_version='player_initial_system@1.0'`, `input_profile='legacy_v1'`, `execution_allowed=false`）。该路径保留统一版本身份与 `6311071c` 上游可靠性修复，`Dossiers.tick()` 自动跳过已结算 `daily_r16@3` 历史槽位，`MANUAL_IMPORT`、`accepted()` 与 `episode()` 本轮已通过端到端实测。
  2. **兜底二进制/文件回滚**：如需将 6 个 P5 模块同步回切，还原发布前备份的 `6311071c` 同版 6 个 P5 `astra_player_*` / `astra_anchor_relative_v1.py` 文件（SHA 已归档于 `production_manifest.json`）并切回 `daily_r16@2` 计划；历史 P5 卷宗只读保留、互不覆写。
