# v0.5 EDB 实现自审 (编码后复查)

实现日期：2026-05-30
范围：SRD(期权偏斜) + GGR(全局Gamma区制) + EDB(到期窗口方向合成层) + CVD重标定 + 去双计数 + 闭环。
验证：完整官方校验链全绿（见 §6）。本文是编码后的诚实自审，含已知取舍与残留风险。

---

## v0.51 追加（前端整理 + 因子收口 + 退役 legacy）

针对实盘观测反馈，v0.51 解决了四件事，官方五步校验链重新全绿（含新 EDB 红灯断言）：

1. **退役 legacy bias_thesis（去同质化 + 去冗余）**：`bias_thesis.py` 从 ~1022 行的 arbiter 收缩为 ~190 行的"宏观/资金 verdict + 宏观分量展示"共享 helper（仅供 EDB 调用）。删除 `evaluate_bias_thesis`、CVD strength/price-response、confidence/label/cap 全套（已被 EDB 取代）。前端"倾向性论证层"表移除；契约/记录/signal_events 全部改读 EDB；schema `SCHEMA_BIAS_THESIS` 移除。
2. **EDB 表显原始数值**：单一 EDB 表现在显示每个证据的原始读数——TMV blend/24h/48h、CVD 4h/12h 的 CVD BTC+涨跌%+强度、Macro score+VOLQ/DXY/US10Y 分量、**SRD RR/归一/rr_z/ΔRR**、**GGR regime/强度/门乘子/净Gamma/flip/距flip/最大Gamma行权**。
3. **SRD 显示 0 的真相 + 修复**：实盘 deribit 取数正常，rr_blend 真实（如 −0.0469），但**只显示了 vote**，而 vote 在相对基线冷启动时为 0（rr_z=0）。已把 rr_blend 等原始值上前端。put-call 同价只对**同行权价**强制 call/put IV 相等；25Δ 风险逆转比较的是**不同行权价**的 call(+0.25Δ) 与 put(−0.25Δ)，故非 0、可用。GGR/SRD 原始读数改为直接读各自因子 payload（不经可能被 0 权重过滤掉的 evidence），保证恒显。
4. **四步法纳入 EDB、主链路表纳入新因子、版本号 → 0.5.1**（schema `nrd.schema.v0.5.1`，总览标题动态取 demo_version）。

**v0.51 残留/已知**：
- 保留了 `bias_cvd_*` 等已无代码引用的旧 config 键（不影响运行；下一轮可清）；recorder 中少量原 bias 表 helper 成为死代码（无害，未删以降风险）。
- 离线 fixture 下 `净Gamma +0.0000`（合成期权对称相消）、`4h CVD —`（fast 窗未就绪）、`12h WARMING`（CVD 分布冷启动）均为 fixture 假数据特征，实盘会显示真实值。
- SRD/GGR 实盘拉取路径仍只能实盘验证；冷启动 CVD/SRD 基线预热期照旧（约 12–20 tick）。
- 版本号信封已升到 v0.5.1（与之前 v0.5「不升 schema」的取舍不同——本轮做了真实切换）。

---

## v0.5.2 追加（前端收口 + EDB 真实数据审计）

### 交付（官方五步链全绿，schema/demo 升 v0.5.2）
- **退役两张过时面板**：`四步法归纳` 与 `因子输出与策略推荐`（状态栏 8→6）。
- **唯一去处迁移**：Anchor 明细(偏离/分数/中轴/标签)并入「主链路与因子状态」Anchor 行；24h/48h 期号 + strategy_type 并入 EDB `recommendation` 行（"信号成立后基于 EDB 倾向+置信"）；EDB 新增 `funding` 行（rate/verdict/norm，原本是 evidence 但未展示）。
- **扫冗余**：删除全部已无引用的 `bias_*` config 键（仅留 `bias_macro_blocking_enabled`）；删除两个死表函数 `_factor_strategy_table`(仍在读已空的 bias_thesis)、`_four_step_table`。
- 四步法仅保留在**日志**（soul.md 四步法不丢），不再占状态栏。

### EDB 真实数据审计结论（用户问"是否合格"）
样本：EDB +0.698 / 一致度 85% / 置信 66 / 偏多(窗口未开)；证据 [TMV+1.0w1.0, MACRO+0.92w0.5, GGR_SPATIAL+0.25w0.25, SRD 0w0.34, FUNDING小]；**CVD 缺失(4h/12h 未就绪)**。

- **架构合格**：聚合/一致度/GGR 门/原始数值展示均正确；GGR 读数真实有用（POSITIVE_GAMMA_PINNING、净Gamma+0.36、价在 flip 上 0.81%、上方钉 75000 → 钉住区制利于卖权 + 上行钉）。
- **置信口径不合格（核心发现）**：置信 66 **高估**了实际证据。原因——`confidence=|EDB|×一致度×GGR乘子` 只在"已present 证据"上计算：① **CVD 缺失不降置信**；② SRD vote=0(冷基线)却仍带 0.34 权重，只轻微拖累一致度。结果"只有 TMV 趋势 + Macro 顺风"也能到 66。这违反用户自身的信息论前提（信息越少→熵越高→置信应越低）。该 66 在引入"覆盖度折扣"后应≈30 出头（→中性/等待），这才是真市场语义下的诚实读数。
- **建议修复（改置信语义，需用户拍板，因影响实盘数据口径）**：(a) 覆盖度折扣 `confidence ×= present_informative_weight/total_expected_weight`；(b) 信息量加权——证据权重随 |vote| 缩放，使 vote=0 的冷 SRD 权重≈0、不污染一致度。修后"是否合格"→是。
- 旁注：Macro 用了 ^VXN 回退(VOLQ 源降级)，且 +0.92/w0.5 在无 flow 确认下对 24-72h 方向影响偏大；覆盖度折扣可一并缓解。

### v0.5.2 残留
- recorder 中约 20 个原 bias/factor-strategy 表的 helper 成为孤儿死代码（与 live 函数交错，未逐个删以免在绿构建上引入风险）；安全可删，列为下一轮快速 prune。
- EDB 置信覆盖度/信息量修复**未实施**（属审计建议，待用户决定是否进 0.5.3，因其改变实盘置信刻度）。

---

## v0.5.3（已实施：置信度覆盖度 + 信息量加权，用户批准）

落实上轮审计的核心修复，官方五步链全绿（schema/demo 升 v0.5.3）：

1. **信息量加权**：每条证据 `eff_weight = weight × clamp(|vote|/edb_informative_vote_abs, 0, 1)`（`edb_informative_vote_abs=0.15`）。vote≈0 的冷证据（如冷启动 SRD）eff_weight≈0，**不再稀释 score、不再拖累一致度**。score 与一致度均改用 eff_weight。
2. **覆盖度折扣**：`coverage = present_informative_dir_weight / total_expected_dir_weight`（期望集 = TMV+CVD×2+MACRO+FUNDING+SRD；GGR 空间票为条件性 bonus，不计入分母）。`confidence = 100·|EDB|·一致度·coverage·GGR乘子`。缺 CVD / 冷 SRD → 覆盖度↓ → 置信↓。
3. 前端 EDB summary 行新增 `覆盖 X%`；payload 新增 `coverage`；summary_cn 含覆盖。
4. 新增红灯断言：缺 CVD 的 thin 设置覆盖度与置信均 < 全证据设置。

**实证（用户原始 66 场景，假设窗口已开）**：edb_score +0.889 / 一致度 100% / **覆盖 42% / 置信 42 → WAIT_CONFIRMATION**。冷 SRD eff_weight=0。即"TMV+Macro 强同向，但 CVD/SRD 缺席→证据不全→尚不构成可交易确信"。66→42 即审计预期的诚实读数。

**口径变更提醒**：v0.5.3 起置信被覆盖度折扣，**与 0.5.2 及之前的置信数值不可直接比较**；实盘观测置信分布需从 0.5.3 重新起算。

**残留（不变）**：recorder ~20 个孤儿 bias/factor-strategy helper 死代码待 prune；CVD/SRD 冷启动预热期照旧。

---

## 1. 交付内容（已验证）

- 新增 `demo/skew_factor.py`(SRD)、`demo/gamma_regime.py`(GGR)、`demo/edb.py`(EDB) —— 纯函数，单测覆盖。
- `demo/factors.py`：FactorSnapshot 增 `skew/gamma_regime/edb` 键。
- `demo/modules.py`：micro_flow 方向 tilt 改为 `tmvf_micro_flow_direction_tilt`(默认 False) → **去 TMV↔CVD 双计数**。
- `demo/deribit_adapter.py`：`get_ticker` + `normalize_ticker`(greeks/IV/OI，仅方向/区制用途)。
- `demo/main.py`：滚动 CVD/RR 历史、节流的期权 greeks 拉取(优雅降级)、tick 内 EDB 先于策略、离线 fixture 注入合成 greeks。
- `demo/strategy.py`：**闭环** —— EDB 有 lean 时由 EDB 决定卖方向；否则显式标注 `TMVF_LEGACY_PREVIEW`(观察，非推荐)。
- `demo/recorder.py`：新增「EDB 到期方向合成层」状态表。
- `demo/contracts.py`：新增 EDB schema 契约校验。
- 工具链(build/static/preflight/summary/runtime)同步更新；FMZ 单文件重建并同步。

## 2. 关键行为证据（非文档，实跑）

- 置信度取到真实区间 **{6, 72, 84}**，不再塌缩成 {0, 62}。
- 证据冲突(TMV多 vs 宏观空) → 一致度低 → **中性**(conf 6)，不强出方向。
- SRD：结构性为负但在修复的偏斜(rr_blend −0.065) → **看多票 +0.70**（不把负值误读为看空）。
- GGR：价格远低于 flip(强负Gamma) → **VETO**，置信乘子 0。
- 闭环：TMV 偏多但 EDB 强偏空 → 策略采纳 EDB → call_credit_spread。

## 3. 有意的取舍（透明声明）

1. **增量、非整体切换。** `schema_version` 保留 `nrd.schema.v0.4.1`(契约信封未结构性变化，仅新增 payload)；`demo_version` 未升 0.5。`bias_thesis.py` **保留为 legacy**(仍计算、仍进契约，但不再权威；方向/展示以 EDB 为准)。
   - 理由：最低风险、可回退、保持既有测试全绿。**代价**：存在 bias_thesis 与 EDB 的暂时冗余。
   - 建议：EDB 实盘验证稳定后，退役 legacy bias_thesis 并统一升版本号。
2. **SRD/GGR 实盘拉取路径无法离线验证。** Deribit 期权 greeks 拉取已接好且全程 try 包裹优雅降级（缺数据→零权重证据，不崩），但只有纯计算被单测覆盖（fixture 注入合成 greeks）。**首个实盘 tick 才是拉取/选档路径的真正检验。**

## 4. 阈值/权重稳健性（针对"强壮普适无幻觉"的要求）

- **CVD 强度 = 滚动 |cvd_norm| 分布的分位**（非固定绝对阈值）→ 自适应标的分布，**无幻觉绝对线**。修复了旧版"net/gross 永远到不了 0.35 → CVD 永远弱"的死分支。
- **SRD 方向 = rr_z(稳健 median/MAD 标准化) + 动量**，相对自身基线 → **免疫 BTC 结构性负偏斜**。
- **Macro 票**按 `edb_macro_vote_ref` 归一；**Funding** 有界小票；**GGR 区制**由 price-vs-flip 标准约定 + 有界乘子。
- 固定切点（`edb_neutral_score_abs=0.12`、置信 35/50/68、GGR cut/veto 0.5/0.8、base weights）均为有界尺度上的合理初值，**全部标注"待真实数据校准、非理论最优"**，未发明任何具体市场价位级阈值。

## 5. 已知局限与残留风险（诚实）

1. **预热期**：CVD 强度需 ≥20 样本、SRD 基线需 ≥12 样本。冷启动约 12–20 个 tick（60s 循环≈12–20 分钟）内，CVD/SRD 近零权重，EDB 主要靠 TMV/Macro。属正确行为（无分布不假装强度），但用户应预期预热期。
2. **GGR pin 为近月聚合**，非严格逐到期隔离（对两目标到期的近 ATM greeks 合并求 max gamma×OI）。作为近端钉价代理可接受，后续可细化为逐到期。
3. **GEX 符号是做市商库存代理**（不知真实库存）→ GGR 区制是概率判断，只作门/调制，不作硬方向（设计如此）。
4. **`_iv_fraction` 启发式**(>3 则视为百分比÷100)：对 Deribit(恒为百分比)正确；极低 IV(<3%) 单档会误缩放，但 RR/skew_norm 是差值/比值、内部一致，相对票不受影响。次要。
5. **EDB 置信切点与基重是首版猜测**：架构已给出真实动态范围（实测 {6,72,84}），但 35/50/68 与权重的"对不对"需要你的实盘数据校准——这正是你要去收数据的点。

## 6. 验证链（全绿，可复跑）

```powershell
$py = "C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe"
& $py -m compileall demo
powershell -NoProfile -File tools\build_fmz_single.ps1 -Check
powershell -NoProfile -File tools\update_delivery_summary.ps1 -Check
powershell -NoProfile -File tools\fmz_preflight_demo.ps1
powershell -NoProfile -File tools\static_validate_demo.ps1
powershell -NoProfile -File tools\runtime_check_demo.ps1 -PythonPath $py
```
注：本机 CurrentUser 执行策略为 RemoteSigned，用 `-File`(不加 `-ExecutionPolicy Bypass`) 即可运行；运行期校验已含 EDB 红灯断言（置信必须变化、冲突→中性、GGR 否决、SRD 负但升→看多）。

## 7. 深度思考审计（soul.md §7）

- **冗余/重复计权**：已去 TMV↔CVD 双计数；bias_thesis 暂留为有意识冗余（待退役）。六类证据相互独立（1h趋势/成交量柱流/跨资产宏观/永续仓位/期权需求/做市商结构）。
- **能否改决策**：实测 EDB 置信随证据变化、SRD/GGR 能移动结果；实盘后对长期被支配者降级为观察项（准入线在设计稿）。
- **噪声/延迟/流动性**：SRD/GGR 仅近月 + 5 分钟节流；翼部稀薄降权；预热期已说明。
- **经验阈值伪装理论**：全部标注待校准；尽量用分位/相对/归一化替代绝对值。
- **路径/Gamma 风险**：GGR 负 Gamma 否决直接防"逆放大区制卖单边"——卖方爆亏典型场景。
- **只读边界**：preflight 验证无下单/凭证/签名调用，contract 验证无执行字段，MODULE_SEQUENCE 未变。✅

## 8. 下一步（交给真实数据）

1. 实盘收集：EDB 各证据 vote/weight、置信、lean、GGR 区制、SRD rr_z —— 全部已进 decisions/snapshots 日志与状态表，可复盘。
2. 校准：CVD 分位带、SRD 基线窗口、GGR cut/veto 强度、base weights、置信切点。
3. 验证"无硬锁、靠信息量稳"是否成立（方向稳定性 vs 反转及时性）。
4. 稳定后：退役 legacy bias_thesis、统一升版本、再评估 KPF 空间层 / 真实 P&L 归因。
