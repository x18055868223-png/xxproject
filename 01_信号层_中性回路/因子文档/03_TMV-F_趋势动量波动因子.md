# 03 · TMV-F（趋势-动量-波动因子）

> 模块：① 信号层 · 主链路 3/3（进 `MODULE_SEQUENCE` / `module_results`）
> canonical：`demo\modules.py:evaluate_tmvf` + `demo\factors.py:compute_tmvf_profile / compute_tmv_core / compute_funding_layer / compute_micro_flow_context`
> 在 EDB 中的角色：**主干证据 TMV**（base_weight 1.00，权重最高）
> 最后核对：2026-06-02（源码）

## 0. 轻量因子卡

| 字段 | 内容 |
|---|---|
| 因子 | TMV-F（趋势-动量-波动因子） |
| 所属回路 | ① 信号层 · 中性回路 |
| 作用层 | 方向 |
| 理论机制 | 用 24h/48h 趋势、动量、量能与有界 funding 修正形成方向骨架，并保留 CVD 微流上下文给 EDB 使用。 |
| 预期符号 | 正值偏 bullish，负值偏 bearish，接近 0 为中性或不明确。 |
| 适用周期 | 1h K 线的 24h/48h 主窗口，4h/8h/12h 微流辅助窗口。 |
| 与现有因子重叠 | 与 EDB 的 TMV、CVD、FUNDING 证据相邻；micro_flow 不回灌 TMV，避免流向双计。 |
| 主要失效条件 | K 线不足、双窗口冲突、funding 拥挤被误当确认、微流覆盖不足。 |
| 改变的决策 | 改变 EDB 中 TMV 证据票、方向一致性与置信分解。 |
| 当前状态 | ACTIVE |

## 1. 一句话定位
1h K 线驱动的趋势骨架。双窗口（24h/48h）各算 TMV 核（趋势方向 + 动量 + 量能），叠加**有界 funding 反身性**修正，blend 成 `tmv_blend`；另算 volume-bar 的 CVD 微流上下文供 EDB 的 CVD 证据使用。

## 2. 当前具体实现

### 2.1 TMV 核（`factors.py:compute_tmv_core`，每窗口）
- **趋势方向**：`ema_fast−ema_slow`，过 `min_trend_pct`（0.0005）阈值才取 ±1，否则 0。
- **动量**：MACD 柱 × `momentum_multiplier`(5.0)，经 `robust_iqr_normalize` 归一（上限 `component_max_abs`=0.80）。
- **量能**：`volume_score_at`（量加权涨跌差）经同样 IQR 归一。
- `tmv_core = clamp(trend_weight·dir + momentum_weight·mom_norm + volume_weight·vol_norm, −1, 1)`。权重 24h/48h 均 `{trend:0.50, momentum:0.30, volume:0.20}`。

### 2.2 funding 反身性（`factors.py:funding_adjustment`）
funding_norm 经 `robust_distribution_normalize`。按"同向健康/同向过度拥挤/反向燃料"出有界 `adjustment`（cap `tmvf_funding_adjustment_cap`=0.20）。**方向保护**：调整后若翻转 core 符号，强制归 0（`direction_protected_final`）。`tmv_final = clamp(core + adjustment, −1, 1)`。

### 2.3 双窗口 blend（`factors.py:combine_tmvf_horizons`）
`tmv_blend = (0.40·tmv_24h_final + 0.60·tmv_48h_final)`（48h 占比更高，更稳）。24h/48h 符号相反 → `window_conflict=True`。

### 2.4 方向与状态（`modules.py:_tmvf_direction_from_score`）
按 `tmv_blend` 与 `core_neutral_abs`(0.05)/`core_directional_abs`(0.20) 分级 BULLISH/NEUTRAL_TO_*/BEARISH。`window_conflict` → 强制 `DIRECTION_UNCLEAR`。

### 2.5 CVD 微流上下文（`factors.py:compute_micro_flow_context`）
volume-bar（`volume_bar_n`=10 BTC/根）在 4h/8h/12h 三档算 `momentum_norm` + `cvd_norm`，`score=0.65·mom+0.35·cvd`。就绪需 `bars≥8 且 coverage_hours≥2.0 且 coverage_frac≥0.65`。
- **2026-06-02 修复**：`tmvf_micro_min_coverage_hours` 4.0→**2.0**（必须 < min(horizons)=4，否则 4h 窗 `data_ready` 永假——任何跨度 H 的回看窗 coverage 恒 < H）。这是 4h CVD "永久未就绪" 的根因。

## 3. 关键阈值（现值，`config.py:140-196`）
`tmvf_core_neutral/directional/strong_abs=0.05/0.20/0.45`、`blend_24h/48h_weight=0.40/0.60`、`micro_horizons=[4,8,12]`、`micro_min_coverage_hours=2.0`、`micro_ready_coverage_frac=0.65`、`micro_momentum/cvd_weight=0.65/0.35`、`funding_confirm/crowded/extreme_abs=0.15/0.55/0.85`、`funding_adjustment_cap=0.20`。

## 4. 整合中的路径修改
**零代码改动**。但有一条已落地的 v0.5 **去双计数**约束需牢记：
- `tmvf_micro_flow_direction_tilt=False`（`config.py:303`）：**micro_flow 不再 tilt TMV 方向**。volume-bar 的流向只通过 EDB 的 CVD 证据进入方向，避免同一份流数据在 TMV 和 CVD 里被计两次（`modules.py:247-252`）。

## 5. 当前目标 / 待办
- `edb_tmv_vote_ref=0.45`（EDB 里 TMV vote 饱和点 = strong_abs）是 P0 校准项之一。
- micro_flow 各档就绪率随实盘 volume 分布变化，需观测 4h 修复后的覆盖度。

## 6. 边界与陷阱
- `window_conflict` 时 TMV 主动交出方向（UNCLEAR），此时 EDB 允许 CVD/Macro/Skew 联合造弱方向。
- funding 永远只做"有界修正 + 方向保护"，绝不独立翻面。
- TMV 是 EDB 权重最高证据（1.00），它的 `data_ready=False`（K 线暖机 `TMVF_KLINE_WINDOW_COLD`）会显著拉低 EDB 覆盖度。
