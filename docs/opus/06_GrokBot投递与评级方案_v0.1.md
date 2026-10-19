# GrokBot 自动投递与 A–E 评级推荐方案 v0.1

日期：2026-10-19　状态：推荐方案，待用户确认　依据：子 agent 网络调研 + 服务器快照核实

## 1. 投递渠道调研结论

| 渠道 | 能否从服务器自动两段投递并取回 JSON | 结论 |
|---|---|---|
| **xAI Responses API**：`POST https://api.x.ai/v1/responses`，兼容 OpenAI SDK | 能。服务端会自主调用联网搜索、X 搜索、代码执行工具；`previous_response_id` 串起两段；支持 `json_schema` 严格输出 | **推荐** |
| Grok Bot（xAI × Cursor，带独立云电脑） | 没有官方 API。只有厂商博客提到的 webhook，只返回 runUuid，无法取回结果 | 不用于自动链路；可作人工深挖备选 |
| @grok（X / Telegram） | 自动化违反 X 自动化规则，ToS 风险高 | 不用 |
| Dot（OpenAI）、Muse（Meta） | 未找到公开 API | 不用 |

注：Grok Bot、Dot、Muse 的信息多来自二手资料，xAI API 的事实来自 docs.x.ai。上线前需用真实调用复核以下几点：模型 ID、代码工具的名字（`code_interpreter` 还是 `code_execution`）、工具价格。

## 2. 推荐投递方案

- **模型**：`grok-4.7`（500K 上下文）。案卷超过约 400K token 时改用 `grok-4.3`（1M 上下文）。
- **第 1 段（盲读）**：
  - 内容：简报 + 完整案卷，直接放进消息正文，不用文件附件。附件走检索，只会读到摘录。
  - 不开工具，保证只基于案卷独立判断。
  - 输出 schema：`own_concerns`、`blind_rating`。
- **第 2 段（研究）**：
  - 内容：问题单；用 `previous_response_id` 接上第 1 段，保留上下文。
  - 开启 `web_search`、`x_search`、代码执行。
  - 输出严格 schema：`research_report@0.3`。
  - 本地再校验一次；不合格时修复重试 1 次。
- **调度**：沿用 UTC 02:00、14:00 两个定时时段，加需登录的手动拉起；与提问者共用同一条链路。
- **安全**：xAI API key 只放服务器 env，不进仓库或日志。交接包生成时扫描密钥模式。回收的报告视为数据，不执行其中指令。
- **待用户提供**：xAI API key（由用户自己写入服务器 env 文件）。

## 3. A–E 评级推荐定义

### 3.1 两个输入

1. **风险相对市场基线** `risk_vs_baseline`，由研究员判断，分五档：
   - much_safer、safer、in_line、much_riskier、riskier。
   - **默认是 in_line**：没有经过核实的驱动项，就视为市场定价合理。
   - 偏离一档：至少需要 1 个前提已确认的驱动项。
   - 偏离两档（much_*）：需要 ≥2 个不同来源（lineage）的独立驱动项，或 1 个决定性驱动项，并说明它为何是决定性的。
2. **补偿质量** `compensation`，由**程序计算**，研究员只复核：
   - 计算口径：净信用（扣费后）减去风险中性期望赔付（用 N(d2) 分布对赔付表积分）。
   - 分三档：rich、fair、thin。草案阈值为相对信用 ±10%，待按样本校准。

### 3.2 评级矩阵

| 风险 \ 补偿 | rich | fair | thin |
|---|---|---|---|
| much_safer | A | A | B |
| safer | A | B | C |
| in_line | B | C | D |
| riskier | C | D | E |
| much_riskier | D | E | E |

- **U（无法评级）**：L1/L2 的关键事实缺失时使用，例如报价不当前、IV 缺失或赔付表缺失。
- **把握度** `confidence`：
  - high：所有驱动项的前提都已确认，且未知项不影响结论；
  - low：存在 unverifiable 的关键前提，或评级跨档依赖单一来源；
  - 其余为 medium。
- **倾向映射（默认）**：
  - A/B → underwrite（写明条件）；
  - C → watch；
  - D/E → decline；
  - U → watch。
  研究员可以偏离默认映射，但必须写出理由。

### 3.3 为什么这样定

- 补偿由程序客观计算，风险偏离由研究给出，两者不重复计算。
- 默认 in_line，避免研究员凭叙事随意偏离市场定价。
- 评级是离散档位，适合按大样本做单调性校准（01 号 §7.3）。

## 4. 已核实的基线数据可用性

冻结快照 `wpem_evidence.source_bindings.current_packet.raw_payload.market.{short,long}_book` 中，两腿都带 `greeks`（delta 等）、`mark_iv`、`underlying_price`。快照还带 `liability.payout_table` 与 `breakeven_usd`。

示例（2026-10-19，20OCT 82000/80000P）：

| 腿 | Δ | mark IV |
|---|---|---|
| 短腿 82000P | −0.360 | 31.55 |
| 保护腿 80000P | −0.072 | 44.39 |

剩余约 18.7 小时，短腿 N(−d2) ≈ 0.366，与 |Δ| 吻合。结论：概率基线（T1.4）可以直接从现有快照计算，不需要改报价捕获。

**取数位置**：`snapshot.market` 在 `astra_underwriting_v20.py:evaluate`（第 692 行）已经剥离了盘口，所以案卷构建器要从 as-of packet 的 `raw_payload.market.*_book` 取 Δ 与 IV（`astra_candidate_asof_v1.py:419`）。

**计算工具**：正态分布 CDF 复用 `astra_commercial_math_v17.py:36` 的 `_norm_cdf`。

## 变更记录

- v0.1（2026-10-19）：初稿。
