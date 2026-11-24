# ESF v0.1 牌局结构脆弱度（Episode Structural Fragility）设计规范

> **状态与权限**：`ESF v0.1` · `RESEARCH_ONLY`（`live_veto_enabled = False`，`player_integration_enabled = False`）  
> **参数状态**：`UNVALIDATED_CANDIDATE_PARAMS`（一切分档边界均为待校准初值，禁止伪装成违约概率或自动否决门限）  
> **代码实现**：`research/esf_v01/esf_core.py`

---

## 1. 核心结论与选定构型（先给能用的结果）

针对 16h–24h 到期的 BTC 垂直贷方价差（Vertical Credit Spread），市场级 `GFRI`（全市场 `±1%` 对冲流 / 永续盘口深度比）缺乏行权价距离与剩余波动时间尺度，单独使用会产生两类结构性失真：**宽缓冲被双边高 GFRI 误伤**，以及**薄缓冲在低 GFRI + 高已实现波动（RV）下被漏判**。

`ESF v0.1` 采用 **方案一：双轴条件结构脆弱度 `ESF = (B_d, FeedbackState_d)`**：
1. **一阶主轴（物理空间缓冲）**：以已实现波动率尺度 $V_T = \widehat{\sigma}_{RV}\sqrt{T}$ 对短腿对数距离 $D_d$ 做无量纲标准化，得到 $B_d = D_d / V_T$（单位：已实现 $\sigma$），并对 $D_d \le 0$ 显式标记 `ALREADY_BREACHED`；同步挂载 `RV4h_5m` 日内快变波动警报。
2. **二阶条件修正（跨所校验反馈态）**：仅当短腿处于 `±1%` 冲击邻域（$0 < D_d \le 3.0\%$）且方向性 GFRI 经 OKX 跨所盘口印证（排除 Deribit 单所瞬时抽单闪崩）时，最多将脆弱度上调 **1 个候选等级**。
3. **两条硬性不变量**：
   - **薄缓冲绝不掩盖**：只要 $0 < B_d < 0.50\sigma$，无论 GFRI 多低（如 `2026-11-23` Put `62000-P` 的 `2.05x–2.24x`），一律归入 `待校准 / 候选极高脆弱`。
   - **宽缓冲绝不一刀切否决**：当 $B_d \ge 1.75\sigma$ 时，双边高 GFRI 在邻域内至多上调一档至 `待校准 / 候选中脆弱`；若 $D_d > 3.0\%$（远离 `±1%` 冲击带），上调 `0` 档。

---

## 2. 三种度量的本质物理区分：`Option |Delta|` vs `GFRI` vs `ESF`

| 维度 | `Option \|Delta\|`（期权绝对 Delta） | `GFRI v1.1`（Gamma 反馈风险指数） | `ESF v0.1`（牌局结构脆弱度） |
| :--- | :--- | :--- | :--- |
| **核心测度** | 风险中性定价测度（依赖当期期权报价 `mark_iv`） | 全市场 `±1%` 现货冲击下做市商对冲流与永续盘口 `±1%` 深度之比 | 具体牌局短腿在物理已实现波动尺度下的标准化缓冲 $B_d$ + 条件反馈修正 |
| **是否锚定具体行权价与剩余期限** | 是，但用隐含波动率折算 | **否**（全期限聚合、仅针对现价 `±1%` 邻域） | **是**（精确锚定本局 $K_{\text{short}}$ 与精确剩余时间 $T$） |
| **典型失效盲区** | **IV 滞后盲区**：日内剧震后若做市商 `mark_iv << RV`（如 `11-23` `56.98%` vs `92.59%`），`\|Delta\| = 0.299` 严重低估真实越界风险 | **空间错配与单所噪声**：无法区分 `0.3%` 与 `4%` 虚值短腿；Deribit 盘口 10m 抽单占步长方差 `89%–99%` | **非方向性且非高斯概率**：在单边跳空或波动聚集下，$B_d$ 是几何缓冲刻度而非生存概率；不预测涨跌方向 |
| **下游角色定位** | 定价与选腿参考（衡量市场给的赔率） | 市场微观流动性/反馈环境监测 | **牌局级结构承压诊断**（当前 `RESEARCH_ONLY`） |

---

## 3. 第一性原理变量定义（B0）与零前视约束

所有变量在任意评估时点 `asof_ms` 严格满足 `observation_ts <= asof_ms`（零前视）。

### 3.1 短腿有向对数距离 $D_d$ 与越界态
对标的价格 $S > 0$ 与短腿行权价 $K_d > 0$：

$$
D_{\mathrm{put}} = \ln\!\left(\frac{S}{K_{\mathrm{put}}}\right), \qquad
D_{\mathrm{call}} = \ln\!\left(\frac{K_{\mathrm{call}}}{S}\right)
$$

- **正值（$D_d > 0$）**：短腿仍处虚值保护垫之外（`boundary_state = "OTM_CUSHION"`）。
- **零或负值（$D_d \le 0$）**：现价已触及或越过短腿责任起点，显式标记 `boundary_state = "ALREADY_BREACHED"`，严禁与小幅正缓冲混同（例：`Deribit_BTC_20261118T080000Z` 在 `2026-11-17 16:02Z` 发牌时 $S = 64,093.32$，Call 短腿 `64000-C` 对应 $D_{\mathrm{call}} = -0.15\%$，直接归入 `候选已越界`）。

### 3.2 剩余期限已实现波动尺度 $V_T$ 与多窗口稳健性对比
采用 BTC 全年无休日历 `YEAR_HOURS = 8760.0`（`365 × 24`）、`YEAR_MS = 365 × 86400 × 1000`：

$$
T = \frac{\max(0,\; t_{\mathrm{expiry}} - t_{\mathrm{asof}})}{\mathrm{YEAR\_MS}}, \qquad
V_T = \widehat{\sigma}_{RV}\sqrt{T}, \qquad
B_d = \frac{D_d}{V_T}
$$

在独立同分布（IID）高斯对数收益基准下，含 $n$ 个收益率样本的年化波动率估计量渐近相对标准误为 $\mathrm{SE}(\hat{\sigma})/\hat{\sigma} \approx 1/\sqrt{2(n-1)}$。`esf_core.py` 同时计算五组无前视 RV 口径以检验稳健性：

| 波动率口径 | 样本构造（严格 `<= asof_ms`） | 样本数 $n$ | 渐近相对标准误 | 结构特征与在 `ESF v0.1` 中的分工 |
| :--- | :--- | :---: | :---: | :--- |
| **`rv_24h_1h_pct`（主轴默认）** | 复用 `astra_expiry_gamma_v1.realized_vol`，过去 24 根已收盘 1h `BTC-PERPETUAL` K 线 | 24 | `14.74%` | 与工作台 L4 原生口径完全一致，兼顾近 1 日波动水平与抗微结构噪声 |
| **`rv_7d_1h_pct`** | 过去 168 根已收盘 1h `BTC-PERPETUAL` K 线 | 168 | `5.47%` | 抽样误差最低，但在 `11-20`/`11-23` 波动急剧放大时滞后 `24–35 vol pts` |
| **`rv24_clock_adjusted`** | 复用 `astra_session_clock_v1` 日内季节性方差比校正 `rv_24h` | 24 | `14.74%` | 剥离欧美盘与亚盘时段基数差异，验证季节性不改变核心排序 |
| **`rv_4h_5m_pct`（快变警报）** | 冻结 `index_path` 闭合 5m 对数收益（避开 1m 买卖跳动） | 48 | `10.31%` | 极快捕捉日内波动爆炸（如 `11-23` 发牌前急震使 `RV4h_5m` 飙至 `225.82%`） |
| **`rv_24h_5m_pct`** | 冻结 `index_path` 过去 24h 闭合 5m 对数收益 | 288 | `4.17%` | 高频低方差对照基准，遇缺口严格返回 `None` 不跨棒拉伸 |

> **$\sqrt{T}$ 扩散标度的物理局限声明**：BTC 短周期收益显著存在**跳跃（Jumps）与波动率聚集（Volatility Clustering）**，尾部分布远厚于正态分布。因此，$B_d = D_d / V_T$ 仅代表**以当期已实现波动尺度衡量的几何缓冲倍数（$\sigma$ 单位）**，**绝不等于**正态生存概率 $1 - \Phi(-B_d)$ 或真实不越界概率。

### 3.3 方向性 Gamma 反馈态、跨所闪崩护栏与空间相关性
通过 `align_gfri_no_lookahead` 严格匹配 `freeze_at_ms <= asof_ms` 且账龄 $\le 20\text{ min}$（`max_age_ms = 1_200_000`，仅接受单侧状态为 `VALID` 或 `DEGRADED` 且 `gfri_confirmed` 非空的记录）：
- **跨所抽单闪崩检验（`QUOTE_FLICKER_SUSPECT`）**：当 `GFRI >= 3.0x`、近端归因 `driver == "LIQUIDITY_DROP"` 且 4h 变化比 `delta_ratio >= 1.25x`，但同期 OKX `BTC-USDT-SWAP` 同向 `±1%` 深度 $\ge 2.5\times$ Deribit 且 OKX 折算 GFRI $< 2.0\times$ 时，判定为 Deribit 单所做市商瞬时撤单（如 `2026-11-20 16:08Z/16:19Z` 的 `5.69x/8.16x`），不触发脆弱度上调。
- **有效反馈分级**：排除单所闪崩后，若 `GFRI >= 3.5x` 且 `4h delta_ratio >= 1.30x` 记为 `AMPLIFYING_SURGE`（如 `2026-11-21 02:04Z` 的 `5.21x`）；若 `GFRI >= 3.0x` 记为 `ELEVATED_PRESSURE`（如 `2026-11-19 16:02Z` Put 的 `3.58x`）；其余记为 `NORMAL_OR_LOW`；缺失或超龄记为 `UNAVAILABLE`。
- **`±1%` 冲击空间相关性门控**：`GFRI` 分子仅测量现价 `±1%` 局部对冲流，禁止将 `5x GFRI` 线性外推为 `5%` 价格冲击或全行权价均匀买盘。按 $D_d$ 划分为：`ALREADY_BREACHED`（$D_d \le 0$）、`DIRECT_PROXIMITY`（$0 < D_d \le 2.0\%$）、`MODERATE_PROXIMITY`（$2.0\% < D_d \le 3.0\%$）、`DISTANT_FROM_1PCT_SHOCK`（$D_d > 3.0\%$，完全屏蔽 `±1%` GFRI 升级）。

---

## 4. 构型对比：方案一（选定）vs 方案二（评估后暂缓）（B1）

### 4.1 方案一（ESF v0.1 选定）：双轴确定性分级矩阵
主轴按 $B_d = D_d / V_{T,\mathrm{RV24}}$ 划为五档，条件修正仅在 $0 < D_d \le 3.0\%$ 内生效：

| 主轴分档（$B_d$ 区间） | 基础候选状态 | 条件上调规则（仅当 $0 < D_d \le 3.0\%$ 且非单所闪崩） | 上调后上限状态 |
| :--- | :--- | :--- | :--- |
| `ALREADY_BREACHED`（$D_d \le 0$） | `待校准 / 候选已越界` | 已在界内，保持最高警示 | `待校准 / 候选已越界` |
| `CRITICAL`（$0 < B_d < 0.50\sigma$） | `待校准 / 候选极高脆弱` | **恒定极高脆弱**（低 GFRI 绝不降级掩盖） | `待校准 / 候选极高脆弱` |
| `THIN`（$0.50 \le B_d < 1.00\sigma$） | `待校准 / 候选高脆弱` | `AMPLIFYING_SURGE` 上调 `+1` 档；`ELEVATED_PRESSURE` 保留本档并置同档优先序 | `待校准 / 候选极高脆弱` |
| `MODERATE`（$1.00 \le B_d < 1.75\sigma$） | `待校准 / 候选中脆弱` | `AMPLIFYING_SURGE` 或 `ELEVATED_PRESSURE` 上调 `+1` 档 | `待校准 / 候选高脆弱` |
| `WIDE`（$B_d \ge 1.75\sigma$） | `待校准 / 候选低脆弱` | `AMPLIFYING_SURGE` 或 `ELEVATED_PRESSURE` **至多上调 `+1` 档** | `待校准 / 候选中脆弱` |

此外，当 $D_d > 0$、`rv_4h_5m_pct >= 1.50 × rv_24h_1h_pct` 且 $B_{\mathrm{RV4h,5m}} \le 0.65\,B_{\mathrm{RV24,1h}}$ 时，独立点亮日内快变波动标志 `fast_vol_alert = True`。同档内部按词典序 `(final_rank, -B_d, feedback_severity)` 排序，确保一阶空间缓冲始终主导连续次序。

### 4.2 方案二（评估后暂缓）：连续压力调整缓冲 $B_d^{\mathrm{stress}}$
构造满足严格单调性、无任意 `0–100` 打分、不伪装成概率的连续备选形式：

$$
V_T^{\mathrm{stress}}(d) = V_T \left[1 + \lambda \, e^{-\max(0,\, D_d - 0.01)/0.01} \ln\!\left(1 + \max(0,\, \mathrm{GFRI}_d - 1)\right)\right], \qquad
B_d^{\mathrm{stress}} = \frac{D_d}{V_T^{\mathrm{stress}}(d)}
$$

- **数学性质验证**：对 $D_d > 0$，关于 $D_d$ 严格单调递增，关于 $V_T$ 严格单调递减，关于 $\mathrm{GFRI}_d > 1$ 严格单调递减；当 $D_d \gg 1\%$ 时指数空间衰减项 $e^{-(D_d-0.01)/0.01} \to 0$，自动退化回 $B_d$。
- **v0.1 暂缓方案二的定量原因**：
  1. **极小样本过拟合风险**：`xx-eu` 上带实时 GFRI 的正式发牌卷宗仅 `3` 局（`6` 个结构，总体采集仅 `7` 天），耦合强度 $\lambda$ 无从稳健标定；
  2. **盘口微结构噪声直接污染主尺度**：GFRI 10 分钟步长方差中 `89%–99%` 来自 Deribit 盘口深度波动。实证显示（见实验报告 §3.1–§3.2），在 `4` 笔真实影子锁仓样本上，方案二（$\lambda=0.35$）因 `11-19 Call` 的 `GFRI=3.44x` 将其压缩至 $B^{\mathrm{stress}}=0.240\sigma$（低于 `11-23 Put` 的 $B^{\mathrm{stress}}=0.263\sigma$），使锁仓样本违约排序 AUC 从方案一的 **`1.000` 退化至 `0.667`**。

---

## 5. 阅读器卡片输出契约（B3：`render_esf_card`）

遵循阅读器风格（`重点清晰、逻辑贯通、关键内容全面`，控制在 12 行以内，技术字段留存底层 JSON）：

```text
ESF v0.1 牌局结构脆弱度           RESEARCH_ONLY

标的现价：62,783.44 USD  |  剩余时间：15.96h (到期 2026-11-24 08:00Z)

PUT   62000/60000 短腿距离 +1.27%  |  缓冲 +0.32σ (快变RV4h +0.13σ!)  |  下行 GFRI 2.24x (常态)
结构状态：待校准 / 候选极高脆弱

CALL  63000/65000 短腿距离 +0.35%  |  缓冲 +0.09σ (快变RV4h +0.04σ!)  |  上行 GFRI 1.81x (常态)
结构状态：待校准 / 候选极高脆弱

模型权限：RESEARCH_ONLY（live_veto=False | player_integration=False | 阈值未校准）
```

---

## 6. 全局上下游对齐与准入边界

- **上游复用**：直接复用 `tools/astra_expiry_gamma_v1.py`（1h 闭合波动率）、`tools/astra_session_clock_v1.py`（日内方差时钟）、`tools/astra_commercial_math_v17.py`（币本位垂直价差结算赔付）及 `research/gfri_v11` 影子账本格式，零侵入现有交易与发牌代码。
- **下游可用性与当前缺口**：`esf_core.evaluate_episode_esf` 与 `render_esf_card` 已可直接用于离线回放与只读影子伴随记录（Read-Only Shadow Logging）；但**严禁接入 AI 牌手输入契约或启用自动否决**，直到在 `xx-eu` 上累积 $\ge 30$ 天可回放 GFRI 周期与 $\ge 30\text{–}50$ 局完整交割/撤回 Episode 完成袋外阈值校准。
