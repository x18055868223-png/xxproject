# 08 · GGR（全局 Gamma 区制门）

> 模块：① 信号层 · EDB 证据 + **安全门**（不进 `module_results`）
> canonical：`demo\gamma_regime.py:evaluate_gamma_regime`
> 因子卡：`add\global_gamma_regime_factor_v1.0.md`（已复制进交付物快照）
> 数据源：gexmonitor（flip_point/spring/net_gex/walls）+ Deribit 每档 gamma×OI
> 最后核对：2026-06-02（源码）

## 0. 轻量因子卡

| 字段 | 内容 |
|---|---|
| 因子 | GGR（全局 Gamma 区制门） |
| 所属回路 | ① 信号层 · 中性回路 |
| 作用层 | 风险门 / 方向 |
| 理论机制 | 用 net gamma、flip 距离、max gamma strike 与 walls 判断市场处于钉住、过渡或负 gamma 放大区。 |
| 预期符号 | 正 gamma 提升钉住可信度；负 gamma 只下调或否决置信，不直接给硬方向。 |
| 适用周期 | 每轮期权快照和 GEX 增强信息刷新。 |
| 与现有因子重叠 | 与 Anchor/GEX info 共用空间信息，也向 EDB 提供安全门与有限 spatial vote。 |
| 主要失效条件 | greeks/OI 过期、flip 缺失、gamma 符号误读、wall 档位覆盖不足。 |
| 改变的决策 | 改变 EDB `confidence_multiplier`、`veto`、GGR_SPATIAL 票与对冲持续性例外修正。 |
| 当前状态 | ACTIVE |

## 1. 一句话定位
判市场处于**正 Gamma 钉住区**（做市商对冲压波动，适合单边卖权）还是**负 Gamma 放大区**（危险）。**首先是安全门**（负 gamma 砍/否决置信），其次是置信调制，最后才在钉住区给一个小空间票。

## 2. 当前具体实现（`gamma_regime.py:evaluate_gamma_regime`）
- 入：`gex_snapshot`(flip_point)、`current_price`、Deribit `option_quotes`(gamma/OI)。
- **区制**：`dist_frac=|price−flip|/price`。`≤ggr_transition_band_pct`(0.003) → `TRANSITION`；否则 `price>flip → POSITIVE_GAMMA_PINNING`，`price<flip → NEGATIVE_GAMMA_AMPLIFYING`。区制强度 `0.4 + (dist−band)/0.012`，夹 [0.4,1.0]。
- **聚合 net_gex 符号**只下调不上调：区制与 net_gex 符号相悖 → 降为 TRANSITION（`GGR_AGG_NET_GEX_DISAGREES`）。
- **安全门 `_apply_gate`**（核心）：
  - 正 gamma → `SUPPORT`，`confidence_multiplier = 1 + (ggr_positive_conf_boost_max−1)·strength`（≤1.15）。
  - 负 gamma：`strength≥ggr_negative_veto_strength(0.80)` → **VETO**，multiplier=0，`veto=True`；`≥ggr_negative_cut_strength(0.50)` → `CUT_CONFIDENCE`，multiplier 线性 1.0→floor(0.40)；否则轻度负 gamma multiplier=0.95。
  - TRANSITION → multiplier=0.98。
- **空间钉 `_apply_pin`**：`_max_gamma_strike`（Deribit 每档 |gamma|·|OI| 最大档；回退 gexmonitor walls/max_pain）。OI 占比 < `ggr_pin_min_oi_share`(0.15) 不给票。**仅正 gamma 钉住区信任**（负 gamma `ggr_pin_trust_negative_gamma=0.0`）。`spatial_vote = clamp(dir·magnitude·pin_trust, −cap, cap)`，cap `ggr_spatial_vote_cap`(0.25)；`spatial_weight = pin_trust`。
- 输出 `regime / regime_strength / confidence_multiplier / spatial_vote / spatial_weight / veto / net_gamma_notional / max_gamma_strike / pin{…}`，schema `SCHEMA_GAMMA_REGIME`。

## 3. 关键阈值（现值，`config.py:291-301`）
`ggr_transition_band_pct=0.003`、`ggr_negative_cut/veto_strength=0.50/0.80`、`ggr_positive_conf_boost_max=1.15`、`ggr_negative_conf_floor=0.40`、`ggr_pin_min_oi_share=0.15`、`ggr_pin_trust_negative_gamma=0.0`、`ggr_spatial_vote_cap=0.25`、`ggr_pin_distance_ref_pct=0.02`；`_REGIME_STRENGTH_SPAN=0.012`（`gamma_regime.py:23`，模块常量）。

## 4. 整合中的路径修改
**零代码改动**。GGR 在整合里有双重身份：
1. EDB 证据 `GGR_SPATIAL`（base_weight 0.25，仅钉住区有非零票）。
2. EDB **安全门**：`confidence_multiplier` 乘进置信、`veto` 直接清零（`edb.py:88-101`）。
3. 对冲模块持续性 `GGR_ADVERSE`（负 gamma 非线性加速例外修正，是 EDB 之外唯一方向相关入口）。

## 5. 当前目标 / 待办
- P0 校准第一簇之一：GGR 安全门 `veto(0.80)/cut(0.50)`（直接决定否决/砍置信的频率）。
- net_gamma_notional/max_gamma_strike 取数依赖 Deribit per-strike gamma×OI，需确认实盘取数覆盖足够档位。

## 6. 边界与陷阱
- **gamma 符号是做市商库存代理，按概率对待，绝不当硬方向确定性**（因子卡强调）。
- 负 gamma 区制下空间钉 **不给票**（pin_trust=0）——危险区不信任"钉住"。
- net_gex 符号只用于**下调**信任，不上调（保守）。

## 7. 数据增强（gexmonitorapi /v1/info，2026-06-04）
- `evaluate_gamma_regime(...,gex_info=None)` 接收可选干净 gex_info：`_net_gex_sign` 优先读 `total_net_gex`、`_max_gamma_strike` 回退 `magnet_price`、新增 `market_state` 交叉校验。**只下调不上调**（与既有 net_gex 规则一致），新增诊断 `gex_info_market_state`/`gex_info_agrees` 与 reason `GGR_GEX_INFO_STATE_DISAGREES`。
- Deribit 逐档 gamma×OI 仍是 pin 首选；`gex_info=None` 时逐字节等价（runtime smoke 全绿）。详见 [`00_总纲/gexmonitorapi数据增强引入说明_v1.0`](../../00_总纲/gexmonitorapi数据增强引入说明_v1.0.md)。
