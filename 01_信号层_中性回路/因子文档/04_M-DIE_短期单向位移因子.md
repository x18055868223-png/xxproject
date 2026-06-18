# 04 · M-DIE（短期单向位移程度因子）

> 模块：① 信号层 · 时序门输入（不进 `module_results`）
> canonical：`demo\factors.py:compute_m_die`
> 因子卡：`add\M-DIE_v1.1_final_15m短期单向变化程度因子.md`（已复制进交付物快照）
> 在链中的角色：NeutralRepair 时序门的 **DIE 事件触发源**
> 最后核对：2026-06-02（源码）

## 1. 一句话定位
度量"最近 15 分钟价格有多像一次干净的单向再定价"。输出带符号强度 `m_die∈[−1,1]`（符号=方向 UP/DOWN，幅度=单向程度）。**它的方向是事件触发用，不是交易方向**。

## 2. 当前具体实现（`factors.py:compute_m_die`，v1.1_final）
- 数据：1m K 线，窗口 15 根（`m_die_window_bars=15`），仅用已收盘 bar（`_clean_m_die_klines` 去未来 bar/去重/排序）。
- 对数收益 `returns`；`total_return=Σreturns`。`|total_return|` 不过 `return_floor`(0.0006) → 判 `direction_not_clear`（m_die=0，方向 NO_DIRECTION）。
- 三分量（各经 `_linear_score(start,full)` 归一到 [0,1]）：
  1. **位移 d_final** = `sqrt(d_z_score · abs_return_score)`，其中 `displacement_z=|total_return|/realized_vol`（z 阈 0.6→1.8），`abs_return` 阈 0.0006→0.0025。
  2. **路径效率 e_score** = `|total_return|/Σ|return|`（阈 0.35→0.85）。
  3. **方向持续 p_final** = `same_direction_ratio·sqrt(coverage_ratio)`（阈 0.45→0.70），只数 `|return|>micro_floor(5e-5)` 的有效 bar。
- 合成：`score = clamp(0.40·d_final + 0.40·e_score + 0.20·p_final, 0, 1)`；`m_die = direction·score`。
- 分级 `_m_die_level`：`<0.25 NO / <0.45 MILD / <0.65 CLEAR / ≥0.65 STRONG`。
- 形态 `_m_die_move_shape`：`IMPULSE_SHIFT`（覆盖低+效率高）/ `DRIFT_RUN`（覆盖≥0.5+效率≥0.55）/ `CHOPPY_DRIFT`。

## 3. 关键阈值（现值，`config.py:198-211`）
`m_die_window_bars=15`、`return_floor=0.0006`、`micro_return_floor=0.00005`、`z_start/full=0.6/1.8`、`r_start/full=0.0006/0.0025`、`e_start/full=0.35/0.85`、`p_start/full=0.45/0.70`。合成权重 0.40/0.40/0.20 硬编码于 `factors.py:741`。

## 4. 整合中的路径修改
**零**。与 KPF 无关。它只喂 NeutralRepair（`neutral_repair.py:update(m_die, anchor)`）。

## 5. 当前目标 / 待办
- M-DIE 阈值与 NeutralRepair 的事件阈联动（见下）；NeutralRepair `nr_mdie_event_on/off_abs=0.65/0.42` 是把 `|m_die|` 当事件触发/冷却的真正绑定阈，校准时一起看。

## 6. 边界与陷阱
- M-DIE 的 UP/DOWN **不是交易方向**——它是"发生了一次单向位移"的事件信号，方向由 EDB 独立判。
- `data_state != "OK"`（bar 不足/无效）→ NeutralRepair 直接 `NR_DATA_INSUFFICIENT`，时序门不开。
- 合成权重里方向持续只占 0.20，是有意的（位移+效率才是"干净单向"的主特征）。
