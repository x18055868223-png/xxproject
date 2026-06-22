# 09 · MPF（宏观压力因子）

> 模块：① 信号层 · EDB 证据 + 硬阻断门（不进 `module_results`）
> canonical：`demo\macro_factor.py`（`MacroPressureFactor` + `compute_macro_pressure`）
> 因子卡：`add\宏观要素.md`（已复制进交付物快照）
> 数据源：Yahoo Finance chart（VOLQ / DXY / US10Y），3 日变化；LKGV 缓存
> 最后核对：2026-06-02（源码）

## 0. 轻量因子卡

| 字段 | 内容 |
|---|---|
| 因子 | MPF（宏观压力因子） |
| 所属回路 | ① 信号层 · 中性回路 |
| 作用层 | 方向 / 风险门 |
| 理论机制 | 用 VOLQ、DXY、US10Y 的三日变化合成宏观逆风/顺风，并在极端冲击时成为硬阻断门。 |
| 预期符号 | 宏观压力为正偏 bearish，压力为负或顺风偏 bullish。 |
| 适用周期 | 宏观刷新节流约 1 小时，计算窗口约 3 日。 |
| 与现有因子重叠 | 与 External Gate 数据健康和 EDB MACRO 票相邻；不替代价格动量或期权偏斜。 |
| 主要失效条件 | Yahoo 源不可用、缓存过旧、宏观与 crypto 短期脱钩、极端事件阈值未标定。 |
| 改变的决策 | 改变 EDB MACRO 票、宏观 conflict 与 hard blocking。 |
| 当前状态 | ACTIVE |

## 1. 一句话定位
用三个宏观代理（科技波动率 VOLQ、美元 DXY、美债 10Y）的 3 日变化合成"宏观逆风/顺风"分，喂 EDB（base_weight 0.30）；并在极端逆风/波动冲击时作**硬阻断门**。

## 2. 当前具体实现（`macro_factor.py`）
- **取数**：`MacroPressureFactor.refresh`（`macro_refresh_sec`=3600 节流），每组件多候选符号回退（如 VOLQ→`^VOLQ/^VXN/^VIX`）；失败回退 LKGV 缓存（`macro_factor_cache.json`，>7 天作废）。
- **组件分**（`macro_component_from_values`）：3 日变化 → `scoring_bps`；US10Y 用收益率点差（×10 或 ×100 自适应），其余用 pct。`normalized_pressure = tanh(scoring_value/scale)`；`component_score = normalized_pressure · weight`。
- **合成**（`compute_macro_pressure`）：`macro_score = clamp(Σcomponent_score · confidence, −1, 1)`。`confidence` 按数据完整度（full_live=1.0 / cached=0.72 / partial=0.65 / 全无=0）。
- **区制**（`classify_macro_regime`）：`≥0.46 Headwind / ≥0.18 Mild Headwind / ≤−0.46 Tailwind / ≤−0.18 Mild Tailwind / else Neutral`。
- **阻断旗**（`macro_blocking_flags`）：`macro_score≥0.46 → MACRO_HEADWIND_BLOCK`；VOLQ 冲击（≥`macro_volq_shock_bps`450）+ DXY/US10Y 同向确认 → `VOLATILITY_SHOCK_CONFIRMED`。
- 输出 schema `SCHEMA_MACRO_PRESSURE`，含 `macro_score / macro_regime / macro_data_confidence / data_status / flags / blocking_flags / components[]`。

## 3. 关键阈值（现值，`config.py:59-90`）
component_weights `{VOLQ:0.35, DXY:0.25, US10Y:0.40}`、scales `{VOLQ:8.0, DXY:0.75, US10Y:12.0}`、`macro_volq_shock_bps=450`、`macro_volq_single_factor_blocking=False`、`bias_macro_blocking_enabled=True`(`config.py:235`)；区制阈 0.46/0.18 硬编码于 `macro_factor.py:419-429`。

## 4. 整合中的路径修改
**零代码改动**。MPF 在整合里：
1. EDB 证据 `MACRO`（base_weight **0.30**——v0.5.4 由 0.5 下调，因 Macro 反向拖累过强；`config.py:248`）。vote = `−macro_score/edb_macro_vote_ref(0.46)`（逆风=看空）。data_status cached/partial 时 reliability×0.5。
2. EDB veto：`verdict==MACRO_BLOCKING` → confidence 清零（`edb.py:94-95`）。
> 注：`bias_thesis.evaluate_macro_verdict` 现是 EDB 复用的 helper（bias_thesis 整体已退役，仅保留 macro/funding verdict helper）。

## 5. 当前目标 / 待办
- `edb_macro_vote_ref`、MACRO base_weight(0.30) 是 P0 校准项；0.5.4 已下调权重但仍需实盘验证 Macro 拖累是否合理。
- 数据源稳定性：Yahoo 偶发不可用→走 LKGV，confidence 自动降。

## 6. 边界与陷阱
- v0.5.4 修复点之一就是 **Macro 反向拖累**（实盘 Macro w0.5 把已确认趋势拖成"无方向"），权重已 0.5→0.3，不要回调。
- MACRO 是实权重证据，**非 ±2 惰性**（设计原则，因子卡）。
- 极端逆风/波动冲击是硬阻断（veto），不是软扣分。
