# 02 · plans（方案排序与价值指标）

> 模块：② 执行层
> canonical：`Deribit期权交易执行层\src\plans.py`（`plan_*`，纯函数可单测）
> 最后核对：2026-06-02（源码）

## 1. 一句话定位
枚举出的每个候选（短腿+保护腿）算价值指标并打综合分排序，输出方案库（方案号+推荐标签）。**综合分当前 = 胜率/盈亏比/KPF/信号四项**（KPF 待 Phase 1 删除）。

## 2. 当前具体实现（`plans.py`）

### 2.1 价值指标
- `plan_win_rate = 1 − |short_delta|`（到期 OTM 近似概率）。
- `plan_effective_credit`：
  - 同期垂直：`net_credit = short_prem − prot_prem`（同到期了结，无复用/残值）。
  - 日历：`covered_cycles=floor(prot_dte/short_dte)`；`amortized = prot_prem·(1−residual_recovery)/covered`；`effective = short_prem − amortized`。
- `plan_max_loss = max(腿宽折BTC − effective_net_credit, 0)`；`plan_rr = net_credit/max_loss`。
- `plan_ev = win_rate·net_credit − (1−win_rate)·max_loss`（**最坏亏损口径，仅参考**）。
- `plan_breakeven`、`plan_credit_on_margin`（净credit/占用保证金 = **价值核心指标**）。
- 费用：`acct_option_fee_ccy`（两腿）、`acct_full_burn`、`acct_spread_cost`。

### 2.2 偏好 delta 与信号契合
- `plan_preferred_delta(signal_state, confidence, delta_range)`：置信线性映射到 `[lo,hi]`；STRONG 再 +0.05（封顶 hi）。
- `plan_signal_fit = max(0, 1 − |短腿delta − preferred|/0.25)`。

### 2.3 综合分与排序（`plan_rank`）
```
composite = win_rate·wr + rr·rr_norm + kpf·kpf_score + signal·signal_fit     ← 当前
            （rr_norm = min(rr/max_rr, 1)）
```
`plan_rank` 还保证菜单同时含垂直与日历，编号(plan_no)，打标签（高胜率/高盈亏比/高期望/均衡）。`plan_prelim_score` 是无 S:PM 的初筛分（枚举后裁 top-K）。

## 3. 关键阈值（现值，`config.py`）
`PLAN_WEIGHTS={"win_rate":0.30,"rr":0.30,"kpf":0.20,"signal":0.20}`、`SIGNAL_CONFIDENCE=62`、`MENU_SIZE=10`、`PROTECTION_RESIDUAL_RECOVERY=0.40`、`MIN_SHORT_PREMIUM=0.0005`、`MAX_SPREAD_RATIO=0.60`。

## 4. 整合中的路径修改（Phase 1 + VRP 收口）
1. **删 KPF**（`config.py:60`、`plans.py:115-124,237,252`）：`PLAN_WEIGHTS` 去 kpf、等比归一 → **`{win_rate:0.375, rr:0.375, signal:0.25}`**（÷0.80）；删 `plan_kpf_score`、综合分去 kpf 项。让出的 0.20 主要回 win_rate/rr（IV/Greeks 驱动）。
2. **VRP 不进 PLAN_WEIGHTS**：`vrp_residual_score` 只作展示/tie-break/回放特征（总纲 v0.4 §4.4）。VRP 的价值在那道**硬门**不在软排；面板上 **VRP edge 须与 `plan_ev`（风险中性、近 0）区分标注，不可相加**。
3. `plan_effective_credit` 是 VRP 收口的 canonical 之一（VRP 删本地副本复用它，收口契约 v1.1）。

## 5. 当前目标 / 待办
- Phase 1 归一后跑 `tests/test_plans.py` 确认排序等价性（除 KPF 影响外不变）。
- 权重 `{0.375,0.375,0.25}` 回放阶段可微调。

## 6. 边界与陷阱
- `plan_ev` 用 delta 当胜率 ≈ **风险中性、近同义反复**——这正是 VRP 要补的"权利金贵不贵"缺口（VRP edge ≠ plan_ev，不可相加）。
- `rr_norm` 是相对当前候选集 `max_rr` 归一，跨轮不可比。
- 综合分是**排序启发式、非精确定价**，真实定价/释放由 S:PM 模拟与实时报价定。
