# 现有工作台风险覆盖基线与持仓触发边界审计（G0 + P1）

> **审计目的**：逐文件核定现有 Astra 承保工作台与影子牌手链路（`tools/` + `demo/`）在**入场前双冻结时刻（发牌 / 锁仓）**与**持仓期条件化触发审计（Cat A–D）**中究竟已有何种量化能力、实际如何进入决策，以及 `ESF v0.1` / `GFRI v1.1` 是否与现有余量恶化审计重复。  
> **审计范围**：`.worktrees/esf-research/tools/`、`.worktrees/esf-research/demo/最新交付物/`、`data_product_registry_v23.json` 及 `xx-eu` 冻结的 `8` 份正式发牌 `input.md` 与 `episode_95ef7494c06605a6b7e59e9c` 持仓事件账本（只读核查，零线上改动）。

---

## 1. 核心裁定（先给能用的结果）

1. **入场前（G0）：`ESF v0.1` 主轴（$B_d = D_d / (\sigma_{\mathrm{RV24}}\sqrt{T})$）完全来自工作台既有入库变量，缺口仅在内部未透传**：
   工作台早已在 `L4.expiry_vol` 中无前视计算并落盘闭合 1h `BTC-PERPETUAL` 的 `rv_24h_pct` 与 `rv_7d_pct`（[astra_expiry_gamma_v1.py](../../tools/astra_expiry_gamma_v1.py#L140-L158)），并在归档/读数层算出 `iv_minus_rv_24h`、`rv24_clock_adjusted`。但发牌端 [astra_player_input_v1.py](../../tools/astra_player_input_v1.py#L121-L128) 与持仓复审端 [astra_player_review_v1.py](../../tools/astra_player_review_v1.py#L958-L968) 仅读取 ATM-IV `expected_move_usd`（`L3.expiry_walls`），对 `L4.expiry_vol` 的决策消费率为 **`0%`**（`8` 份冻结 `input.md` 实测 `contains_rv_token = False`）。在 `2026-11-23 16:02Z` 败局中，ATM-IV（`51.77%`）令 Put `62,000` 显出 `0.55倍尺度` 假象，而案卷已有的 `RV24 = 92.59%` 早已指明其真实缓冲仅 **`0.31x–0.32x`**（且午后 V 型巨震使 4h 现货净流呈 `+219.68 BTC / +2.33%`，绕过了曾成功拒掉 `11-19` / `11-20` 亏损 Put 的单向净流护栏）。
2. **持仓期（P1）：工作台已具备完整的 5m 机械检查与四类条件化触发审计（`Cat A/B/C/D`），`ESF` 绝不能重复建设余量恶化监控**：
   在 `2026-11-23 Put 62000/60000`（`episode_95ef7494c06605a6b7e59e9c`）实际持仓中，现有 **`Cat B MARGIN_DETERIORATION`** 于 **`16:39:20Z`**（`16:40:04Z` 入队 `HOLD_REVIEW_QUEUED`）即捕获余量从 `1.27%` 缩至 `0.57%`（`step_5`），早于 `16:41:45Z` GFRI 微升至 `2.643x`（仍为 `NO_VETO`）；随后 `16:44:20Z` 升级至 **`step_9`**（`0.11%`），`16:48:00Z` 触发 **`Cat A CONDITION_TRIGGERED`** 直通模型裁定 `REVOKE`，最终因期权腿盘口陈旧记为 `REVOKE_RECORDED_NO_QUOTE`（执行受限于期权盘口报价，而非缺信号）。
3. **真正外部缺失项**：永续/现货 `±1%` 盘口深度（`Deribit BTC-PERPETUAL`、`OKX BTC-USDT-SWAP`）、撤单率及对冲流向具体短腿的条件耦合在工作台中为 **`0%`**；GEXMonitor `hedging_flows` 虽拷入 `L5.options_flow` 但下游 **`0%` 使用**。

---

## 2. 六维风险覆盖基线总表（G0 强制五列矩阵）

下表严格区分**「入库/展示（Computed & Stored）」**与**「实际进入承保/续判决策（Decision-Utilized）」**：

| 风险信息 | 工作台是否已有 | 如何使用 | 是否已实现量化 | 是否存在缺口 |
| :--- | :--- | :--- | :--- | :--- |
| **1. 责任腿距离** | **已有（全链路齐备）** | **入库展示 + 硬门控 + 牌手决策**：`L1` 存短/长腿、宽度与赔付上限；选腿门控强制严格虚值（`K_put < index`、`K_call > index`）与 `width=2000 USD`，锁仓门控校验 `require_outside_at_t0`；发牌 `input.md` 传入 USD 距离、24h 1m 穿越/回收/最深侵入及保护腿触越顶格反证；持仓 5m 网格 + `Cat A/B` 持续复核（按 v1.3 契约隐去 `%`、权利金与平衡点） | **已量化**（USD 距离、`%` 距离\[注：内部存在 `/short_strike` 与 `/spot` 双分母口径\]、1m 侵入深度） | **无结构缺口**（但固定 USD 距离本身需配对准确的波动率时间尺度） |
| **2. 标准化波动缓冲** | **已有（入库齐备，但决策层仅用 IV 漏用 RV）** | **计算入库但 `RV` 决策消费率 `0%`**：`L3.expiry_walls` 存 ATM-IV `expected_move_usd`，`L4.expiry_vol` 存闭合 1h `rv_24h_pct`、`rv_7d_pct`，归档/读数层存 `iv_minus_rv_24h`、`rv24_clock_adjusted`、`p_itm/p_touch`；**但发牌 `input.md` 与持仓 `Cat B` 100% 只除以 ATM-IV `expected_move_usd`**，`8` 份冻结 `input.md` 零处传入 `RV` | **已量化**（`expected_move_usd`、`distance_in_moves`、`rv_24h_pct`、`iv_minus_rv_24h`） | **存在关键内部漏传缺口**（`L4.expiry_vol` 已算未传，直接导致 `11-23 Put 62000` 在 `RV24=92.59%` 下仍显示 `0.55x` ATM-IV 假缓冲） |
| **3. 短期 RV 激增** | **部分已有（有方向流与原始 1m 路径，无 `<24h` 年化 `RV4h` 字段）** | **方向性代理已进入决策，双向方差未透传**：`input.md` 传入 5m 涨跌、24h 1m 路径与穿后 60m 偏移、6h/24h 期权流及 **Binance 现货 `[-12h,-4h]` vs `[-4h,0]` 不重叠 CVD/涨跌反证护栏**；持仓 `Cat D` 监控主动流恶化；FMZ `\|M-DIE\|` 15m 标准差不透传，归档层仅算交割后事后 RV | **部分量化**（方向性 4h CVD/收益已量化；闭合 5m `RV4h` 可由已冻结 `source.json -> index_path` 零外部源直算） | **存在双向急震刻画缺口**（4h 净流护栏成功拒掉 `11-19`/`11-20` 单边下跌 Put，但 `11-23` V 型巨震净流 `+219.68 BTC / +2.33%` 掩盖了 `RV4h_5m=225.82%`） |
| **4. Gamma 反馈压力** | **粗粒度阶梯已拷入休眠；`GFRI` 无** | **空间锚点已用，对冲流消费率 `0%`**：无符号 `expiry_pin`、`effective_flip_point` 与 Top-3 OI/Gamma 墙进入 `input.md` 与持仓 `Cat C`；FMZ 拷入 `L5.options_flow` 的 `hedging_flows`（`-5%..+5%` `hedging_usd`，剥离 `price_only_curve`）在发牌/锁仓/续判中 **`0%` 使用**；`GFRI` 为 `0%` | **仅静态墙与未消费阶梯量化**（无动态反馈比） | **工作台未消费 `hedging_flows` 且无 `GFRI`**（但实证显示全期限 `\|HF\|` 在 `11-23` 低位钝化，补入亦无正增益） |
| **5. 盘口流动性动态** | **无永续/现货冲击深度（仅期权腿锁仓快照）** | **仅锁仓瞬间门控期权腿**：`confirm_quote` 锁仓硬门控校验 Deribit 期权短/长腿 5 档盘口深度与账龄；永续/现货 `±1%` 盘口深度与撤单率在全链路 **`0%` 入库、`0%` 使用** | **期权腿 5 档已量化；永续/现货 `±1%` 深度未量化（`0%`）** | **存在真实外部数据缺口**（`Deribit BTC-PERPETUAL` 与 `OKX BTC-USDT-SWAP` 盘口深度缺失，含 `36.5%` 单所撤单噪声需跨所过滤） |
| **6. 反馈与具体短腿的条件性关系** | **无** | **`0%` 入库、`0%` 使用**：发牌 `input.md` 与持仓 `Cat C` 仅核查现价相对静态墙/Pin/翻转轴的地理穿越，不含全市场反馈对具体短腿 $K_{\mathrm{short}}$ 的条件冲击放大 | **未量化（`0%`）** | **形式上缺失但实证不成立**（双侧对称 `GFRI` `ln r=0.980` 与单侧行权价责任高度脱钩） |

---

## 3. 四层执行阶段穿透矩阵与发牌前门控细节

按真实运行链路拆分为四层：**`(1) Workbench / CaseFile / Archive 入库`** → **`(2) 选腿发现、发牌前 Python 硬门控与锁仓门控`** → **`(3) 发牌后 AI 牌手 input.md`** → **`(4) 持仓期 5m 监控与 Cat A–D 复审`**。

### 3.1 四层覆盖全景对照表（严格区分发牌前 Python 硬门控 vs 发牌后 Prompt 规则）

| 模块簇 | `(1) Workbench / CaseFile / Archive 入库` | `(2) 选腿发现、发牌前 Python 门控与锁仓门控` | `(3) 发牌期牌手 input.md（通过前置门控后）` | `(4) 持仓期监控与复审 input.md` |
| :--- | :--- | :--- | :--- | :--- |
| **责任腿距离与保护腿** | `short/long_strike`、`width=2000`、`%` 距离（双分母口径）、平衡点、BTC 赔付上限 | 选腿强制严格虚值 OTM、`width=2000`；`L1.contract` 账龄 `<=5m`；锁仓 `require_outside_at_t0` | USD 距离、`约X.XX倍尺度`（仅 ATM-IV）、24h 1m 穿越/回收/最深侵入、保护腿触越反证 | 5m 机械网格 `new_entry` / `entry_not_returned`；`Cat A`（触界/破界）+ `Cat B`（余量恶化 `step_1..9`） |
| **波动率尺度与外生事件** | ATM-IV `expected_move_usd`（`L3`）；闭合 1h **`rv_24h_pct` / `rv_7d_pct`**（`L4`）；`iv_minus_rv_24h`；`L7.events` | **发牌前 Python 硬门控**：① `L7.events` 寿命内阻塞型外生事件抛 `HandNotDealt`（记 `POLICY_BLOCKED`）；② `L3.expiry_walls` 缺失/超龄（`>5m`）或 `stale` 在枚举路径时直接阻断发牌（记 `DATA_BLOCKED`），`expiry_scale` 对超龄/缺失 `expected_move_usd` 置 `None`。**无 `RV` 门控** | **仅传入 ATM-IV `expected_move_usd`（`L4` RV 读取率 `0%`）**；发牌后 Prompt 规则声明「缺尺度或成交、义务方向未知，都不单独构成弃权理由」（仅对已通过前置 Python 门控的牌面生效） | `Cat B` 仅按 ATM-IV `expected_move_usd` 归一化余量；**`RV` 读取率 `0%`** |
| **短期量价冲击与快变波动** | 24h 1m `index_path`；Binance 现货量柱 CVD/收盘价；6h/24h 期权主动流；事后结算 RV | 候选读数账龄 `<=5m`、Put/Call 采样差 `<=60s`；无短期 RV 门控 | 5m 涨跌；24h 1m 路径与穿后 60m 偏移；6h/24h 期权流；**`[-12h,-4h]` vs `[-4h,0]` 现货 CVD/涨跌反证护栏** | `Cat D`（`FLOW_REVERSAL` / `FLOW_ADVERSE_CONTINUATION`）追踪入场后主动流反转或恶化 |
| **IV / Delta / 定价偏斜** | `short_delta`、`mark_iv`、`dvol`、`rr_25d`、`term_inversion`、`premium_thickening` | **`\|short_delta\| <= 0.50`**（目标 `0.35`，账龄 `<=5m`）选腿门控 | **按 v1.3 契约全部隔离不传** | **全部隔离不传** |
| **Pin / 空间墙 / ARD** | 无符号 `expiry_pin`、`effective_flip_point`、Top-3 OI/Gamma 墙、4h 无前视 `ARD` | `major_positions` 要求 `L3.expiry_walls` 新鲜可用（`<=5m`），否则阻断发牌 | 全量传入翻转轴、Pin、必经墙及 4h `ARD` 状态与相对位移 | `Cat C`（`ARD_STRUCTURE_SHIFT`，含 `READ_ONLY_ENTRY_PIN` 只读同口径对比） |
| **盘口微观结构与交割执行** | `L5.options_flow.hedging_flows`（休眠）；锁仓/撤回/交割账本 | 30m 回复宽限窗 + 决断后 15m 锁仓交接窗（`HANDOFF_MS`）+ `confirm_quote` 期权腿 5 档盘口深度/新鲜度锁仓门控 | **永续/现货盘口深度 `0%`、`hedging_flows` `0%`、`GFRI` `0%`** | `REVOKE` 时查询期权短腿 5 档卖盘，陈旧/缺失记 `REVOKE_RECORDED_NO_QUOTE`（不伪造止损成交价） |

### 3.2 入场前代码核查关键补遗

- **发牌前 Python 硬阻断 vs 发牌后 Prompt 不弃权规则的精确边界**：
  在 [astra_player_input_v1.py](../../tools/astra_player_input_v1.py#L98-L128) 与 `#L191-L198` 中：
  1. `check_events()`（`#L98-L118`）检查 `L7.events`，一旦剩余寿命内存在阻塞型宏观/外生事件，立即抛出 `HandNotDealt`（由 [astra_player_dossier_v1.py](../../tools/astra_player_dossier_v1.py#L435-L436) 记为 `POLICY_BLOCKED`，根本不生成 `input.md`）；
  2. `major_positions()`（`#L191-L198`）强制 `L3.expiry_walls` 满足 `usable(..., 5 * 60_000)` 且非 `stale`，否则抛 `ValueError` 记 `DATA_BLOCKED`；`expiry_scale()`（`#L121-L128`）同样以 `5m` 新鲜度校验 `expected_move_usd` 与 `atm_iv_pct`（在严格要求尺度完备的上游入口亦对应 `expiry_scale_unavailable` 阻断语义）。
  3. 只有当案卷已通过上述 Python 发牌前门控、进入渲染后的 `input.md` 正文（`#L626-L627`、`#L732`）时，才适用面向 AI 牌手的后置规则「缺尺度或成交、义务方向未知，都不单独构成弃权理由」。两者处于不同执行阶段，互不矛盾。
- **距离百分比分母双口径与 `RV24` 漏传量化复核**：
  [astra_underwriting_v20.py](../../tools/astra_underwriting_v20.py#L290) 按 `short_strike_usd` 归一化距离，[astra_candidate_readings_v1.py](../../tools/astra_candidate_readings_v1.py#L155) 按 `spot` 归一化；而在波动率层，[astra_expiry_gamma_v1.py](../../tools/astra_expiry_gamma_v1.py#L140-L158) 算出的 `rv_24h_pct = 92.59%`（`2026-11-23 16:02Z`）虽已存入 `L4.expiry_vol`，却因 `expiry_scale()` 只读 `L3.expiry_walls.expected_move_usd`（`1,389.7 USD`）而完全未送达牌手。

---

## 4. 持仓期条件化触发审计与执行边界核查（P1 专项基线）

为防止 `ESF` 在持仓管理阶段重复造轮子，以下完整核定现有持仓监控机制（[astra_player_condition_v1.py](../../tools/astra_player_condition_v1.py)、[astra_player_review_v1.py](../../tools/astra_player_review_v1.py)、[astra_player_tracking_v13.py](../../tools/astra_player_tracking_v13.py)）及其在 `2026-11-23` 真实败局中的逐分钟表现。

### 4.1 现有持仓检查节拍、防抖预算与四类触发器（Cat A–D）

1. **检查节拍、防抖冷却与软预算豁免机制**（[astra_player_condition_v1.py](../../tools/astra_player_condition_v1.py#L133-L179)，[astra_player_review_v1.py](../../tools/astra_player_review_v1.py#L20-L36)，`#L1452-L1538`）：
   - **基础节拍**：按入场冻结的 `5m` UTC 网格（`check_interval_ms = 300_000`）逐分钟无缺口核验 `btc_usd` 指数路径（缺分钟严格返回 `uncheckable`，零插值补造）。
   - **常规防抖、冷却与软预算**：常规触发（Cat B/C/D）需连续 2 个新鲜采样点（`FRESH_SAMPLE_GAP_MS = 10m`）确认，进入 `10m–15m` 防抖并包窗口（`MIN_DEBOUNCE_MS = 10m`、`DEFAULT_DEBOUNCE_MS = 12m`，期间记 `HOLD_REVIEW_QUEUED` / `COALESCED`），每次模型复核后设 `30m` 冷却窗（`DEFAULT_COOLDOWN_MS = 30m`），单局常规 AI 复核软上限为 **`2` 次**（`DEFAULT_MAX_ORDINARY_REVIEWS = 2`，超限记 `BUDGET_DEFERRED`），同签名未升级事件按 `DUPLICATE_EVENT` 去重。
   - **紧急直通豁免（Immediate Bypass）**：
     - **`Cat A`（`CONDITION_TRIGGERED`）**：`priority_a_bypass = True`，**无条件穿透防抖窗口、冷却窗与常规预算上限**，立即拉起 AI 复审（`BOUNDARY_BREACH` 亦豁免常规软预算与未核验拦截）；
     - **极端多维恶化（`extreme_escalation`）**：当 `Cat B` 达到 **`depth_step >= 8`** 且合并至少 2 类不同触发器时，直接穿透 `2` 次常规软预算上限。
2. **四类确定性触发器定义**（`detect_hold_triggers`，[astra_player_review_v1.py](../../tools/astra_player_review_v1.py#L1344-L1418)）：
   - **`Cat A`（责任边界触发）**：`CONDITION_TRIGGERED`（入场后冻结失效规则 `new_entry` 或 `entry_not_returned` 被触发）与 `BOUNDARY_BREACH`（首次触及/越过短腿）。
   - **`Cat B`（`MARGIN_DETERIORATION` 余量恶化 `step_1..step_9`）**：监控短腿距离百分比相对锁仓初值 $d_0$ 缩水过半（$d_{\text{now}} \le 0.50\,d_0$）、绝对与相对同步缩水（$(d_0 - d_{\text{now}}) \ge \max(0.35, 0.40\,d_0)$ 且 $d_{\text{now}} < 0.75\,d_0$），或相对 `expected_move_usd` 归一化缓冲缩水过半（$r_{\text{now}} \le 0.50\,r_0$），量化映射为 `step_1..step_9`（步进 `+2` 视为显著升级）。
   - **`Cat C`（`ARD_STRUCTURE_SHIFT` 空间锚点迁移）**：追踪同口径 `ARD` Pin 实质向短腿逼近或新增邻域穿越事件；即使入场未依赖 ARD，亦通过 **`READ_ONLY_ENTRY_PIN`** 模式（[astra_player_tracking_v13.py](../../tools/astra_player_tracking_v13.py#L692-L714)）对入场 `L3` Pin 做同口径只读偏移监测。
   - **`Cat D`（主动流逆转/恶化）**：监控入场后不重叠窗口出现 `FLOW_REVERSAL` 或 `FLOW_ADVERSE_CONTINUATION`。
3. **`REVOKE` 影子执行边界（严禁伪造止损成交价）**（[astra_player_tracking_v13.py](../../tools/astra_player_tracking_v13.py#L793-L839)）：
   模型裁定 `REVOKE` 后，`_record_revoke_shadow_action()` 实时读取 Deribit 短腿 5 档卖盘（`short_buyback_quote`）；仅当买回报价完整且账龄 `<= MAX_BOOK_AGE_MS` 时记录模拟买回 `BUY_BACK_SHORT`，一旦盘口陈旧（`short_quote_stale_at_revoke`）或缺失，**严格记为 `REVOKE_RECORDED_NO_QUOTE`（`simulated=True, execution_allowed=False`），绝不编造理论平仓成交价或虚假止损净值**。

### 4.2 `2026-11-23 Put 62000/60000`（`episode_95ef7494c06605a6b7e59e9c`）持仓触发与 GFRI 逐刻对照

| UTC 时刻 | 现价 / 盘口锚点 | 现有工作台持仓监控状态（5m 网格 + Cat A–D） | 同期 `GFRI v1.1` 影子状态 | 先行性与非重复审计结论 |
| :--- | :--- | :--- | :--- | :--- |
| `16:12:20.803Z` | `62,785.09 USD` | 影子锁仓 `t0`（短腿 `62,000-P` 安全余量 **`1.27%`**，净权利金 `0.0026375 BTC`） | `2.045x`（`16:09:46Z` 周期，`NO_VETO`） | 入场时刻 `GFRI` 处于常态低位，零预警 |
| `16:20:29Z` – `16:31:07Z` | `62,675 USD` (`16:31Z`) | 5m 例行检查正常，余量温和波动 | `2.004x` → `2.074x`（全程 `NO_VETO`） | 持仓前 20 分钟 `GFRI` 完全静默 |
| **`16:39:20Z` (`16:40:04Z` 入队)** | 余量缩至 **`0.57%`** | **`Cat B MARGIN_DETERIORATION`（`step_5`）率先触发**（`1.27% -> 0.57%`），于 `16:40:04Z` 写入 `HOLD_REVIEW_QUEUED` 进入防抖并包 | 最新已完成周期仍为 `16:31:07Z` 的 **`2.074x`（`NO_VETO`）** | **工作台 `Cat B` 领先捕获余量腰斩，此时 `GFRI` 毫无异动** |
| `16:41:45Z` | `ob_px = 62,071.75` (`1m = 62,276`) | 已处于 `HOLD_REVIEW_QUEUED` 防抖合并窗内 | 微升至 `2.643x`（`DEGRADED`，`dr=1.323`，**仍为 `NO_VETO`**） | 滞后于 `Cat B` 入队，且未触发任何 GFRI 暂缓门限 |
| **`16:44:20Z`** | 余量仅剩 **`0.11%`** | **`Cat B MARGIN_DETERIORATION` 升级至 `step_9`**（`1.27% -> 0.11%`，逼近短腿边界） | 无新周期（沿用 `2.643x NO_VETO`） | 工作台已量化追踪至最高余量恶化等级 `step_9` |
| **`16:47:00Z` – `16:48:00Z`** | `61,999.75` → **`61,892.19 USD`** | **`Cat A CONDITION_TRIGGERED` 触发**（穿透防抖直通调度 AI 复核） | 无新周期（实际持仓期 `0/3` 暂缓） | 边界破位由 `Cat A` 毫秒级直通捕获 |
| `16:50:51.876Z` – `16:50:59.948Z` | 短腿买回估算 `0.0098 BTC` (陈旧) | AI 裁决 **`REVOKE`** → 因 `short_quote_stale_at_revoke` 记 **`REVOKE_RECORDED_NO_QUOTE`** | 持仓已终止（下一周期已出局） | 退出瓶颈在于快市期权腿有效盘口缺失，再加任何信号均无法改变可执行性 |

> **铁律结论（严禁重复建设）**：现有工作台 `Cat B`（`step_1..step_9`）+ `Cat A` 直通机制已经在持仓期完整、领先地执行了余量收缩与破界审计（同时在盈利局 `11-19 Call 63500` 中仅平静执行 `2` 次 `HOLD` 复核，对比 GFRI `90/90` 周期 100% 误报暂缓）。**`ESF` 绝不能包装成第二套持仓余量恶化触发器；持仓端唯一真实缺口仅是 `Cat B` 内部的 `expected_move_usd` 尚未并入 `L4.expiry_vol.rv_24h_pct`。**

---

## 5. 最小全局对齐说明（下游衔接与修改边界）

- **下游直接可用性**：本报告为 [GFRI_CONDITIONAL_INCREMENT_STUDY.md](GFRI_CONDITIONAL_INCREMENT_STUDY.md)、[EPISODE_FRAGILITY_CAUSAL_PATH_REVIEW.md](EPISODE_FRAGILITY_CAUSAL_PATH_REVIEW.md) 与 [ESF_FINAL_RESEARCH_VERDICT.md](ESF_FINAL_RESEARCH_VERDICT.md) 确立了入场前（`A0` 线上输入 vs `A1` 透传既有 `RV24`）与持仓后（`Cat A–D` 条件化触发 + `REVOKE_RECORDED_NO_QUOTE` 真实执行边界）的精确代码基线。
- **线上隔离与后续跟进边界**：本轮保持 `xx-eu` 线上服务、Prompt A 与生产工作台 **100% 未改动**。若后续用户单独授权推进工作台内部补全，仅需定向修改 [astra_player_input_v1.py](../../tools/astra_player_input_v1.py)（透传 `L4.expiry_vol.rv_24h_pct` 与 `index_path` 近 4h 双向波幅）及 [astra_player_review_v1.py](../../tools/astra_player_review_v1.py)（`_detect_category_b` 引入 `RV24` 有效尺度），严禁新建重叠的 `ESF` 持仓监控模块。
