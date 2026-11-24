# ESF v0.1 与 GFRI 条件增量最终研究裁决报告（G7 + P0/P1/P2）

> **P2 补充规范主操作裁决（Primary Operational Decision）**：**`EXISTING_WORKBENCH_ENHANCEMENT_PREFERRED`** + **`ESF_REDUNDANT`**  
> *（候选枚举：`{PRE_ENTRY_GATE_INCREMENT_SUPPORTED, POST_ENTRY_INCREMENT_SUPPORTED, EXISTING_WORKBENCH_ENHANCEMENT_PREFERRED, ESF_REDUNDANT, INSUFFICIENT_EVIDENCE}`）*  
> **G7 基础研究裁决（Base Research Verdict）**：**`ESF_NO_USEFUL_GFRI_INCREMENT`**  
> *（候选枚举：`{ESF_INCREMENT_SUPPORTED, ESF_INCREMENT_PLAUSIBLE_BUT_UNPROVEN, ESF_REDUNDANT_WITH_WORKBENCH, ESF_NO_USEFUL_GFRI_INCREMENT, INSUFFICIENT_EVIDENCE}`）*  
> **执行与权限收敛**：**终止独立 `ESF` 开发（`SUSPEND_ESF`）** · **维持 `GFRI v1.1` 被动只读影子采集（`SHADOW_ONLY`，路径 3）** · **将既有 `L4.expiry_vol` 透传归入现有工作台增强待办（路径 2，待后续独立授权，本轮零线上改动）**  
> **证据索引**：[EXISTING_WORKBENCH_RISK_COVERAGE_AUDIT.md](EXISTING_WORKBENCH_RISK_COVERAGE_AUDIT.md) · [GFRI_CONDITIONAL_INCREMENT_STUDY.md](GFRI_CONDITIONAL_INCREMENT_STUDY.md) · [EPISODE_FRAGILITY_CAUSAL_PATH_REVIEW.md](EPISODE_FRAGILITY_CAUSAL_PATH_REVIEW.md) · [ESF_REAL_EPISODE_EXPERIMENT.md](ESF_REAL_EPISODE_EXPERIMENT.md) · [GFRI_20261121_20261123_EVENT_STUDY.md](GFRI_20261121_20261123_EVENT_STUDY.md)

---

## 1. 核心裁决总览（先给能用的结果）

1. **操作主裁决：`EXISTING_WORKBENCH_ENHANCEMENT_PREFERRED` + `ESF_REDUNDANT`；研究主裁决：`ESF_NO_USEFUL_GFRI_INCREMENT`。**
2. **为什么 `ESF` 冗余（`ESF_REDUNDANT`）且应优先增强现有工作台（`EXISTING_WORKBENCH_ENHANCEMENT_PREFERRED`）**：
   - **入场前主轴已是工作台库存变量**：`ESF v0.1` 的一阶缓冲 $B_d = D_d / (\sigma_{\mathrm{RV24}}\sqrt{T})$ 完全依赖工作台 `L4.expiry_vol` 已计算入库的闭合 1h `rv_24h_pct`（快变 `RV4h_5m` 亦由已冻结 `source.json -> index_path` 直算），此前仅因 [astra_player_input_v1.py](../../tools/astra_player_input_v1.py#L121-L128) 与 [astra_player_review_v1.py](../../tools/astra_player_review_v1.py#L958-L968) 只读 ATM-IV `expected_move_usd` 而未透传。
   - **持仓期余量恶化审计在现有工作台中早已跑通且领先**：在 `2026-11-23 Put 62000/60000` 败局中，现有 `Cat B MARGIN_DETERIORATION` 在 `16:39:20Z`（`step_5`）与 `16:44:20Z`（`step_9`）已精确捕获余量塌陷，`16:48:00Z` `Cat A` 已直通触发 `REVOKE`，而同期 `GFRI` 全程 `0/3` 暂缓；退出记为 `REVOKE_RECORDED_NO_QUOTE` 是受限于快市期权短腿盘口陈旧，而非缺持仓脆弱度指标。
3. **为什么 `GFRI` 无可用条件增量（`ESF_NO_USEFUL_GFRI_INCREMENT`）**：
   方案一双轴在 `16` 个正式发牌结构中带来 **`0 / 16` 次调档**；在真实承保牌局中实现 **`0` 次新增正确阻断**，却在盈利的 `11-19 Call 63500` 持仓期造成 **`90 / 90` 周期（`100%`）误报暂缓**；在薄缓冲候选池（`THIN_CRITICAL: B_RV24 < 0.60, n = 30`）中，耦合 GEX 对冲流的 `System D`（日内破位 `AUC = 0.508`，破位 $\rho = +0.013$，封顶腿宽侵入 $\rho = -0.049$）反不如纯盘口局部流动性 `System B`（日内破位 `AUC = 0.683`，破位 $\rho = +0.290$，封顶侵入 $\rho = +0.171$）。

---

## 2. 入场与持仓管理边界五问直答（P0 / P1 / P2 补充约束闭环）

严格按入场前双冻结时点（`P0`）、持仓期非重复增量（`P1`）与真实承保净收益准入标准（`P2`）逐条裁决：

1. **（1）入场前（P0：发牌与锁仓双冻结时刻）是否新增真实决策信息？**
   **否——外部 `B / C / D` 零有效新增，唯一有效预警信息早已躺在现有工作台 `L4.expiry_vol` 中。**
   - 在唯一真实破位的 **`2026-11-23 Put 62000/60000`**：无论在**发牌冻结时刻**（`16:00:17Z` 读数 / `16:02:13Z` 冻结，`GFRI = 2.235x`）还是**锁仓确认时刻**（`16:12:20Z`，`GFRI = 2.045x`），`System B`（盘口深度）、`System C`（`|HF|`）与 `System D`（`GFRI`）均给出 **`0` 预警（`NO_VETO`）**；而同一案卷 `L4.expiry_vol` 早已算出 `rv_24h_pct = 92.59%`（真实缓冲仅 `0.31x–0.32x`）。
   - 在 **`11-19`** 与 **`11-20`**：事后违约的 `Put 62000` 与 `Put 59000` 均已被现有 `A0`（`input.md` 4h 现货主动流反证护栏）提前拒掉；同期偏高的 `System D`（`11-19 Call` 发牌 `3.445x`、`11-20 Call` 发牌 `3.090x`）反而只会威胁误杀两笔最终 `0` 赔付的正确 `Accept / Call` 决策。

2. **（2）持仓后（P1：非重复增量）是否优于现有条件化触发审计？**
   **否——在 P1 三项硬检验上全面落败，严禁重复建设余量恶化监控：**
   - **检验 (i) 触发前先行性为零**：在 `11-23 Put 62000` 持仓期，现有工作台 `Cat B MARGIN_DETERIORATION` 于 **`16:39:20Z`** 即检出余量从 `1.27%` 缩至 `0.57%`（`step_5`，`16:40:04Z` 入队 `HOLD_REVIEW_QUEUED`），并于 **`16:44:20Z`** 升至 **`step_9`（`0.11%`）**、**`16:48:00Z`** 触发 `Cat A CONDITION_TRIGGERED` 直通复审；反观活跃持仓期内 `GFRI` 暂缓数为 **`0 / 3`**（`16:20Z` `2.004x`、`16:31Z` `2.074x`，直至 `Cat B` 已入队后的 `16:41:45Z` 才微升至 `2.643x` 且仍为 `NO_VETO`）。
   - **检验 (ii) 触发后零新增可执行退出依据**：`16:50:59.948Z` 记录 `REVOKE_RECORDED_NO_QUOTE`（`short_quote_stale_at_revoke`）是因为快市中 Deribit 期权短腿缺乏新鲜有效买回报价，属于**期权盘口流动性瓶颈而非信号滞后**；叠加 `ESF` 绝不能凭空创造可执行平仓价。
   - **检验 (iii) 盈利持仓期产生海量重复虚警**：在盈利交割的 `11-19 Call 63500` 持仓期间，工作台条件触发器仅审慎发起 **`2` 次常规复审并维持 `HOLD`**，而 `GFRI` 在 **`90 / 90` 个周期（`100%`）** 持续报警暂缓。

3. **（3）GFRI 的贡献是否独立于盘口深度？**
   **否。** `736` 个连续周期差分中 **`99.69%`** 的 10m Put `GFRI` 对数方差来自 Deribit `±1%` 永续盘口深度（`System B`）；即便在最干净的先行样本 `2026-11-21 02:04Z`（`5.21x`）中，纯盘口抽撤也解释了 **`99.92%`** 的对数跃升（`|HF|` 仅动 `+0.04%`）；在薄缓冲候选池（`THIN_CRITICAL, n = 30`）内，纯盘口深度 `System B`（日内破位 `AUC = 0.683`，破位 $\rho = +0.290$，封顶侵入 $\rho = +0.171$）显著优于乘入 `|HF|` 后的 `System D`（`AUC = 0.508`，破位 $\rho = +0.013$，封顶侵入 $\rho = -0.049$）。

4. **（4）这些贡献是否足以改变真实承保收益与尾部赔付？**
   **否（边际净期望为负）。** 相对现有 `A0`，`GFRI / ESF` 辅轴对真实承保尾部亏损的边际减免为 **`0 BTC`**（两笔亏损 Put 已被 `A0` 拒保，唯一承保破位的 `11-23 Put` 未被拦截，且 `REVOKE_RECORDED_NO_QUOTE` 严禁虚构止损净值），却直接威胁损失 `1–2` 笔盈利 Call 的权利金收入（`0.0045–0.0069 BTC`）。

5. **（5）是否值得独立开发 ESF？**
   **不值得（`EXISTING_WORKBENCH_ENHANCEMENT_PREFERRED` + `ESF_REDUNDANT`）。** 应终止一切独立 `ESF` 模块、卡片或持仓监控开发；保持 `xx-eu` 现有 `gfri-shadow-collector.timer` 被动只读落盘（`SHADOW_ONLY`），并将 `L4.expiry_vol.rv_24h_pct` 透传收敛为后续独立授权的工作台小步补全任务。

---

## 3. 三条演进路径竞争裁决表（G7）

| 竞争路径 | 核心机制与信息源 | 入场前（P0）与持仓期（P1）实测净效用 | 误杀代价与工程复杂度 | 最终竞争裁决 |
| :--- | :--- | :--- | :--- | :--- |
| **路径 1：建设独立 `ESF` 二阶门控组件** | 新建 `ESF = (B_RV24, CrossVenue_GFRI)` 独立模块/卡片或持仓触发层 | P0：一阶全靠既有 `RV24`，二阶 `0/16` 调档、`11-23` 双冻结时点 `0` 预警；P1：落后于现有 `Cat B step_5/step_9`，且把薄缓冲日内破位 `AUC` 从纯盘口 `B` 的 `0.683` 拉低至 `0.508`（破位 $\rho$ 从 `+0.290` 降至 `+0.013`） | **架构冗余 + 负边际收益**：与 `L4.expiry_vol` 及 `Cat B` 重复，且威胁误杀 `0.0045–0.0069 BTC` 盈利 Call | **明确否决（`ESF_REDUNDANT`）** |
| **路径 2：不建 `ESF`，仅补全现有工作台风险模块** | 零外部新增源，在现有发牌 `input.md` 与持仓 `Cat B` 中直接透传已入库的 `L4.expiry_vol.rv_24h_pct`（`IV - RV24`）及冻结 `index_path` 4h 双向波幅 | P0：直接消除 `11-23 Put 62000` 的 `0.55x` ATM-IV 假缓冲（还原 **`0.31x–0.32x`**），与已拦下 `11-19`/`11-20` 亏损 Put 的 4h 现货流护栏互补；P1：让现有 `Cat B` 余量比同步感知高 RV | **零新增服务、改动极小**：仅触及 2 个既有工作台文件，零单所盘口撤单误伤 | **首选推荐方向（`EXISTING_WORKBENCH_ENHANCEMENT_PREFERRED`，待后续独立授权，本轮不改线上）** |
| **路径 3：保留 `GFRI` 独立只读影子监控，终止 `ESF`** | 维持 `xx-eu` 现有 `gfri-shadow-collector.timer` 被动落盘（`SHADOW_ONLY`），零牌手接入、零否决权限，彻底终止 `ESF` | 零干扰线上承保收益，被动沉淀跨所永续盘口深度（Deribit + OKX）与 GEX 历史账本，供跨交割周期检验纯流动性因子 `System B` | **零新增工程成本**（`MemoryMax=128M`，稳态 `638s`/周期，零生产耦合） | **立即执行 / 维持现状（ADOPT NOW）** |

---

## 4. 七大核心裁决问题逐项直答（Section 12：`Q1`–`Q7`）

### Q1：ESF v0.1 主轴究竟新增了哪些工作台未有的信息？
**答：零外部新变量，主轴核心输入 `100%` 已在现有工作台计算入库。**  
`L4.expiry_vol.realized_vol.rv_24h_pct`、`L1` 短/长腿与现价、`expiry_ms` 均已由 [astra_expiry_gamma_v1.py](../../tools/astra_expiry_gamma_v1.py#L140-L158) 与 [astra_case_file_v1.py](../../tools/astra_case_file_v1.py#L415-L419) 落盘（`iv_minus_rv_24h`、`rv24_clock_adjusted` 亦已在归档/读数层算出；`RV4h_5m` 为已冻结 `source.json -> index_path` 的无参数汇总）。ESF 主轴相对线上 `A0` 的表观增益，完全来自修复了 `astra_player_input_v1.py` 与 `astra_player_review_v1.py` 漏读 `L4.expiry_vol` 的内部断路。

### Q2：GFRI 是否提供独立于原工作台、纯盘口深度与原 GEX 的增量？
**答：未获证实；当前数据中无正向有效增量，分子反而稀释了纯盘口深度信号。**  
全样本 **`99.69%`** 的 10m Put `GFRI` 对数方差来自 Deribit `±1%` 永续深度（`System B`），而聚合 `10–12` 个到期日且带 `8h` OI 滞后的 GEXMonitor `|HF|`（`System C`）在样本期内与薄缓冲日内破位呈负相关（$\rho = -0.315$，封顶侵入 $\rho = -0.225$，`AUC = 0.302`），将纯盘口深度 `System B` 的区分力（破位 `AUC = 0.683`，破位 $\rho = +0.290$，封顶侵入 $\rho = +0.171$）拉低至 `System D` 的 **`AUC = 0.508`（破位 $\rho = +0.013$，封顶侵入 $\rho = -0.049$）**；连最典型的 `11-21 02:04Z`（`5.21x`）先行事件也有 **`99.92%`** 对数跃升来自纯盘口深度收缩。

### Q3：这项增量是否与具体 Put/Call 责任边界有关？
**答：极弱，基本与单腿责任边界脱钩。**  
Put 与 Call `GFRI` 对数相关高达 **`r = 0.980`**（近端 `±1%` 抽单对称 + 全市场多期限 Gamma 聚合），无法区分哪一侧短腿真正承压（如 `11-19 Call 63500` 在 `3.10x–6.19x` GFRI 下 `0` 赔付完胜；`11-21 02:04Z` Call `4.90x` 对 `Call 60000` 毫无威胁）。

### Q4：它是否具有领先性，还是主要同步或滞后？
**答：以假阳性（`63.3%`）与同步/滞后（`26.7%`）为主，近半数大波动前无信号（`47.8%`）。**  
30 个价格盲选异常簇中 `FALSE_POSITIVE` 占 `19/30`（`63.3%`），`COINCIDENT + LAGGING` 占 `8/30`（`26.7%`），`INCONCLUSIVE` 占 `1/30`（`3.3%`），`LEADING` 仅 `2/30`（`6.7%`，且如 `11-21 02:04Z` 本质属纯盘口撤单 `System B`）；反向扫描 23 段 `|ret_60m| >= 1.50%` 大行情中 `NO_SIGNAL` 占 `11/23`（`47.8%`）。

### Q5：在实际承保策略中，新增阻断是否有望改善净收益和尾部损失？
**答：当前证据下净期望为负。**  
在实时 GFRI 覆盖的正式牌局中，`A0` 已独立拒掉亏损的 `11-19 Put 62000`（`0.005263 BTC`）与 `11-20 Put 59000`（`0.017111 BTC`），`GFRI` 边际减亏为 **`0 BTC`**；对唯一承保破位的 `11-23 Put 62000` 在发牌（`2.24x`）、锁仓（`2.05x`）与活跃持仓（`0/3` 暂缓）全程漏放（且撤回时因期权盘口陈旧记 `REVOKE_RECORDED_NO_QUOTE`，无可执行止损增益）；反过来却威胁误杀 `11-19 Call`（`0.0045 BTC` 可见权利金，持仓 `90/90` 误报）与 `11-20 Call`（`0.0069 BTC` 净权利金）。

### Q6：是否真的需要独立 ESF 组件，还是只需改进现有工作台？
**答：不需要独立 `ESF` 组件；只需在后续独立授权下增强现有工作台（`EXISTING_WORKBENCH_ENHANCEMENT_PREFERRED`）。**  
在现有 `astra_player_input_v1.py` 与 `astra_player_review_v1.py`（`Cat B`）内补齐已入库的 `rv_24h_pct` / `IV - RV24` 与 `index_path` 短期双向波幅透传，即可用最小架构面闭环 `11-23` 盲区，不与现有 `Cat A–D` 触发体系重叠。

### Q7：当前证据足以进入哪一级权限：继续研究、只读影子计算，还是需要暂缓？
**答：`ESF` 立即暂缓/终止（`SUSPEND_ESF`）；`GFRI v1.1` 维持既有被动只读影子采集（`SHADOW_ONLY`）。**  
不部署任何新的 `ESF` 影子服务、卡片或否决规则；仅保留 `xx-eu` 上现存 `gfri-shadow-collector.timer` 继续零权限后台落盘（`live_veto_enabled=0`，`player_integration_enabled=0`）。

---

## 5. 四项强制闭环审视（证据对冲、替代解释与反转条件）

### 支持结论的最强证据
1. **入场前双冻结时刻零边际排序与零预警**：`16` 个正式结构上 `ESF` 方案一相对纯既有变量 $B_{\mathrm{RV24}}$ 调档数为 **`0 / 16`**（方案二将真实锁仓 AUC 从 `1.000` 劣化至 `0.667`）；`11-23 Put 62000` 在发牌（`2.235x`）与锁仓（`2.045x`）双时点均无 GFRI 异常，真实风险完全由既有 `L4.expiry_vol` 的 `RV24 = 92.59%` 刻画。
2. **持仓期完全落后于现有 `Cat B / Cat A` 条件化审计**：`11-23` 持仓中 `Cat B step_5`（`16:39:20Z`）与 `step_9`（`16:44:20Z`）均领先捕获余量恶化，而活跃持仓 GFRI `0/3` 暂缓；在盈利的 `11-19 Call` 持仓中 GFRI 却产生 `90/90`（`100%`）误报暂缓。
3. **分子负向稀释纯盘口深度**：`99.69%` 的 10m GFRI 方差源于 Deribit 盘口深度，且薄缓冲池（`n = 30`）中 `System D`（破位 `AUC = 0.508`，破位 $\rho = +0.013$，封顶侵入 $\rho = -0.049$）全面劣于纯盘口深度 `System B`（`AUC = 0.683`，破位 $\rho = +0.290$，封顶侵入 $\rho = +0.171$）。

### 反对结论的最强证据
1. **存在跨所印证的平盘先行撤单孤例**：`2026-11-21 02:04:27Z–02:15:04Z` 在横盘期（前 60m `+0.15%`、负 Gamma 对冲流 `$116.7M`）捕获 Deribit（`-37.9%`）与 OKX（`-8.6%`）同步撤单（`5.21x`），提前约 30 分钟领先了 `-6.09%` 瀑布急跌。
2. **实时 GFRI 重叠正式牌局样本极小**：上线后仅经历 **`3` 局正式发牌**（`2` 笔真实锁仓），且 `11-21` 与 `11-22` 两局高波卷宗因原生 Put 案卷缺失处于 `DATA_BLOCKED`。

### 当前无法排除的替代解释
1. **单周跨体制长期漂移混杂**：`11-18..19`（高 `|HF| $130M` + 温和 `RV ~50%`）向 `11-21..23`（交割后低 `|HF| $84M` + 极高 `RV 90%–149%`）的单向切换制造了截面负相关，可能掩盖了跨多个月度/季度交割周期、同高波动分层下的「高负 Gamma × 抽单」正交互效应。
2. **全期限聚合与 `8h` OI 披露滞后**：GEXMonitor `|HF|` 混入 `10–12` 个远近到期日且受 Deribit 公共 OI `8h` 滞后约束，可能稀释了 0–2 DTE 末日轮局部空头 Gamma 尖峰。

### 下一次能够真正改变结论的最小信息增量
**零新增基础设施**，仅靠 `xx-eu` 现有 `gfri-shadow-collector.timer` 被动累积 **`>= 30–50` 局无 `DATA_BLOCKED` 的已结算正式牌局**，并在对比基线已补齐工作台 `RV24`（`A1`）及现有 `Cat A–D` 触发审计后，同时满足以下两条硬标准才重开准入评估：
1. **入场前（P0）或持仓期（P1）净增量成立**：在相同薄缓冲分层（$B_{\mathrm{RV24}} < 0.75\sigma$）下，`高 |HF| × 跨所深度骤降（System D）` 在真实发牌/锁仓时点的净承保 BTC 期望收益显著优于 `纯跨所深度（System B）` 与 `A1`（或在持仓期稳定早于 `Cat B step_5` 且对应期权腿具备真实可成交买回报价）；
2. **反侧零连带误杀**：对安全侧虚值结构不造成高频虚警或净权利金损耗大于尾部减亏。

---

## 6. 最小全局对齐说明（生产隔离与后续衔接）

- **本轮生产零改动**：`xx-eu` 线上发牌、AI 牌手 Prompt A、影子锁仓/复审守护进程与 `gfri-shadow-collector.timer` 配置在本轮 **100% 原封不动**。
- **下游行动切口**：
  1. **立即终止 `ESF` 独立扩展**（不建新服务、不加阅读器卡片、不叠加第二套持仓余量触发器），维持 `gfri-shadow-collector.timer` 原状被动记录。
  2. **待用户后续独立授权（工作台内部增强）**：若启动路径 2，仅需在独立任务中最小化修改 [astra_player_input_v1.py](../../tools/astra_player_input_v1.py)（并列透传 `L4.expiry_vol.rv_24h_pct` 尺度与 `index_path` 近 4h 双向波幅）与 [astra_player_review_v1.py](../../tools/astra_player_review_v1.py)（`_detect_category_b` 引入 `RV24` 有效尺度），并完成单测与牌面审阅闭环。
