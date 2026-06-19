> 当前信号层口径（r2.1 / 2026-06-19）：本因子文档可能保留早期 v0.5/v1.1 代码路径或标定说明；当前 FMZ 交付物以 `demo/最新交付物/neutral_regulation_demo_fmz.py` v1.3.0 为准。本文用于解释因子语义和历史演进，实际运行字段以当前审计 JSON、状态栏和 r2.1 总纲为准。
# 02 · Anchor（GEX 锚 / 引力均值回归）

> 模块：① 信号层 · 主链路 2/3（进 `MODULE_SEQUENCE` / `module_results`）
> canonical：`中性回路 - opus4.8\demo\modules.py:evaluate_anchor` + `demo\factors.py`（band/nd/gravity）
> 数据源：gexmonitor 快照（`flip_point` / `spring` / `source_ts`）
> 最后核对：2026-06-02（源码）

## 1. 一句话定位
以 gexmonitor 的 **gamma flip_point 为锚**，度量当前价相对锚的标准化偏离与"被拉回锚"的引力强度。是 NeutralRepair 时序门判断"锚是否受损/修复"的依据。

## 2. 当前具体实现（`modules.py:88-195`）
输入：`gex_snapshot`、`current_price`、`std_usd`、`latest_bar`、`nd_window`（偏离历史滑窗）。

1. **新鲜度**：`age = now - source_ts`，按 `gex_freshness_stale_ms` / `gex_freshness_expired_ms`（现均 4_200_000ms≈70min）判 FRESH/STALE/EXPIRED。EXPIRED → `STATE_INVALID`。
2. **偏离带 band_half**（`factors.py:compute_band_half`）：`capacity=|spring|·std/volume_bar_n`；`sigma_count=band_base_sigma + band_max_sigma_bonus·tanh(capacity/band_spring_midpoint)`；`band_half=std·sigma_count`，再按 `[band_half_min_pct, band_half_max_pct]·price` 夹紧。std 缺失时回退 `band_fallback_half_pct`。
3. **标准化偏离 nd**（`factors.py:normalized_deviation`）：`nd=(price−flip)/band_half`。
4. **引力分 score**（`factors.py:compute_anchor_gravity`）：对 `|nd|` 滑窗去极值后取均值 `mean_abs`，`score=100·exp(−mean_abs)`（越贴锚越高）。暖机阈 `anchor_gravity_warmup=20` 条。标签 `Detached<30 / Loose<60 / Attached<90 / Tightly Attached`。
5. 状态：有 reason（STALE / `|nd|>anchor_weak_deviation` / GEX pending）→ `STATE_WEAK`，否则 `STATE_VALID`；`score` 进 module_result。

## 3. 关键阈值（现值，`config.py`）
`band_base_sigma=3.0`、`band_max_sigma_bonus=3.0`、`band_spring_midpoint=5.0`、`band_half_min/max_pct=0.001/0.015`、`anchor_weak_deviation=1.5`、`anchor_gravity_window=144`、`anchor_gravity_warmup=20`、`anchor_gravity_valid/weak_score=60/30`、`gex_freshness_*_ms=4_200_000`。

## 4. 整合中的路径修改
**零**。主链门，与 KPF 无关。其 `facts.anchor_gravity_ref_score` 与 `facts.normalized_deviation` 是 NeutralRepair 的核心输入（见 `05_NeutralRepair`），整合不动。

## 5. 当前目标 / 待办
- 引力分阈值（60/30）属 NeutralRepair "锚受损/修复" 判定的耦合阈，校准应与 `nr_anchor_repair_score / nr_anchor_damage_score`（均 60）联动看。
- gexmonitor 接受/新鲜度一整套参数（`gex_accept_*`）属数据接入鲁棒性，非方向校准重点。

## 6. 边界与陷阱
- Anchor 给的是**均值回归引力**，不是方向。它的"偏离方向"绝不能当交易方向用。
- flip_point 缺失即 `STATE_INVALID`（reason `ANCHOR_SOURCE_MISSING`）→ NeutralRepair 直接判数据不足，时序门不开。
- `band_clamped=True`（撞上下限）说明带宽被夹，nd 失真，下游需留意。
