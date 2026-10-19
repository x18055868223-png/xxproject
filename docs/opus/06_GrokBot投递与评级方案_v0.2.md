# GrokBot 自动投递与 A–E 评级推荐方案 v0.2

日期：2026-10-20　状态：推荐方案　依据：子 agent 网络调研 + 服务器快照核实

## 1. 投递渠道结论（v0.2：GrokBot 有自己的 Linux 云主机）

用户确认：GrokBot 有一台自己的 Linux 云主机，规格为 Debian 13、8 核、15 GB 内存（约 4 GB 可用）、无 swap、125 GB 硬盘（约 121 GB 可用）。它能跑脚本、定时任务和 HTTP 客户端，投递方式因此不再局限于聊天接口。

| 方式 | 两段独立性 | 自动化 | 安全面 | 结论 |
|---|---|---|---|---|
| **A. 拉取式 HTTPS 任务接口**：我方服务器发布任务，GrokBot 定时拉取、分段提交 | **服务器强制**：未提交第 1 段，拿不到问题单 | GrokBot 的定时例程加轮询；定时和手动都覆盖 | 专用、限权、可吊销的 agent token；只开放 `/api/agent/v1/*` | **推荐** |
| B. 推送到 GrokBot 主机（SFTP/rsync） | 只能靠自觉 | 我方需要 GrokBot 主机的入站凭据 | 我方要保管对方主机的凭据，双向暴露 | 不推荐 |
| C. 私有 git 仓库中转 | 只能靠自觉 | 可行 | 需要仓库凭据，历史难清理 | 不推荐 |
| D. xAI Responses API（服务器直接调 grok-4.7） | `previous_response_id` 分段 | 全自动 | 只需一把 xAI key | **备用**：GrokBot 主机不可用时降级 |

A 的优点：
- 两段独立由服务器强制，不靠约定；
- 我方不持有对方主机的任何凭据；
- GrokBot 能在自己的机器上长期保存历史任务，用于样本研究；
- 失败了可以重拉。

## 2. 推荐方案 A：拉取式任务接口（`agent_task@0.1`）

### 2.1 接口（计划实现，待办 T3.2 / T3.4 / T3.5）

基址：`https://169.58.177.126/api/agent/v1/`。证书由 Let's Encrypt 签发并自动续期，用标准 TLS 校验，不固定指纹。

| 方法与路径 | 作用 | 说明 |
|---|---|---|
| `GET tasks?status=open` | 列出待办任务 | 返回 `task_id`、`side`、`created_at_ms`、`deadline_ms`、`stage` |
| `GET tasks/<id>/stage1` | 下载第 1 段包（`tar.gz`） | 内含 `brief.md`、`case_file.json`、`history/`、`schemas/` |
| `POST tasks/<id>/stage1` | 提交盲读结果 | `{own_concerns, blind_rating}`；服务器记录时间与哈希 |
| `GET tasks/<id>/stage2` | 下载问题单（`inquiry.json`、`inquiry.md`） | **第 1 段未提交时返回 409** |
| `POST tasks/<id>/report` | 提交研究报告 | 按 `research_report@0.3` 校验；不合格返回 422 及错误列表，可修正后重交 |
| `GET tasks/<id>` | 查询任务状态 | |

### 2.2 节奏

- **定时**：服务器在 UTC 02:00、14:00 的定时时段生成任务，Put 和 Call 各一个。截止时间 = min(合约到期, 创建后 6 小时)。
- **手动**：用户在工作台拉起，或调用需登录的 POST，生成同样的任务。
- **GrokBot 侧**：建一个例程，每 15 分钟轮询一次 `tasks?status=open`。有任务就跑完两段；不做固定时刻的硬编码，这样定时和手动任务都能接到。

### 2.3 安全

- **token**：独立的 agent token（服务器 env 变量 `ASTRA_AGENT_TOKEN`）。
  - 只对 `/api/agent/v1/*` 有效，和工作台登录、复核 token 完全分开。
  - 可随时吊销；由用户生成，写入服务器和 GrokBot 主机，我方不经手明文。
- **接口约束**：nginx 单独做 location，限速；上传上限 2 MB。
- **内容边界**：
  - 任务包只含市场数据和派生量，生成时扫描密钥模式。
  - 回收的报告视为数据，不执行其中的任何指令。
  - GrokBot 不得把任务包整体转发给第三方服务。

### 2.4 试点期过渡

接口上线前，用户手动把任务包文件交给 GrokBot（上传或粘贴），流程不变：先交第 1 段，收到盲读结果后再交第 2 段。

### 2.5 备用方案 D（xAI API）

- 模型 `grok-4.7`（500K 上下文），案卷超过约 400K token 时用 `grok-4.3`。
- 第 1 段不开工具；第 2 段用 `previous_response_id` 接续，并开启 `web_search`、`x_search` 和代码执行；输出用严格 `json_schema`。
- 模型 ID、工具名、价格来自调研，启用前用一次真实调用核实；xAI key 由用户写入服务器 env。

## 3. A–E 评级推荐定义

### 3.1 两个输入

1. **风险相对市场基线** `risk_vs_baseline`，由研究员判断，分五档：
   - much_safer、safer、in_line、much_riskier、riskier。
   - **默认是 in_line**：没有经过核实的驱动项，就视为市场定价合理。
   - 偏离一档：至少需要 1 个前提已确认的驱动项。
   - 偏离两档（much_*）：需要 ≥2 个不同来源（lineage）的独立驱动项，或 1 个决定性驱动项，并说明它为何是决定性的。
2. **补偿质量** `compensation`，由**程序计算**，研究员只复核：
   - 计算口径（已实现，`astra_market_baseline_v1._compensation`）：扣费后净信用 ÷ 交易所两腿 mark 价差（short mark − long mark）。它衡量的是“实际拿到的钱相对交易所公允标记价打了几折”。风险本身已由市场基线定价，这里不再重复计入。
   - 分三档：≥0.95 为 rich；0.70–0.95 为 fair；<0.70 为 thin。
   - 这是草案阈值。10-15 至 10-19 的三份真实样本在 0.78–0.80 之间（按买价卖出并付 taker 费），所以把 fair 的中心放在这里；样本积累后按三分位重定。

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
- v0.2（2026-10-20）：GrokBot 有自己的云主机，投递改为拉取式任务接口（服务器强制两段顺序），xAI API 降为备用；补证书与 nginx 现状。
