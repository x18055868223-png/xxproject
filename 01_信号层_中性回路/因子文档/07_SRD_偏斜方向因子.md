> 当前信号层口径（r2.2 / 2026-06-19）：本因子文档可能保留早期 v0.5/v1.1 代码路径或标定说明；当前 FMZ 交付物以 `demo/最新交付物/neutral_regulation_demo_fmz.py` v1.3.0 为准。本文用于解释因子语义和历史演进，实际运行字段以当前审计 JSON、状态栏和 r2.2 总纲为准。
# 07 · SRD（Skew / 25Δ 风险逆转 方向因子）

> 模块：① 信号层 · EDB 证据（不进 `module_results`）
> canonical：`demo\skew_factor.py:evaluate_skew_rr`
> 因子卡：`add\skew_rr_directional_factor_v1.0.md`（已复制进交付物快照）
> 数据源：Deribit 期权链（near-money greeks：delta / mark_iv / open_interest）
> 最后核对：2026-06-02（源码）

## 1. 一句话定位
从 25Δ 风险逆转（call_iv − put_iv）的**相对变化**出有符号方向票，喂 EDB（base_weight 0.70）。**关键正确性**：BTC 25Δ 偏斜结构性为负（看跌权贵是常态），所以方向**绝不能读 RR 的原始符号**，必须用相对基线（rr_z）+ 动量（Δrr）。

## 2. 当前具体实现（`skew_factor.py:evaluate_skew_rr`）
- 清洗期权报价：要 option_type/delta/mark_iv/strike/expiry，`mark_iv` 自动百分比→分数（`_iv_fraction`，>3 视作百分比），OI < `srd_min_open_interest`(1.0) 剔除。
- 取最近 24h / 48h 两个到期（`_nearest_expiry`），各算 `_expiry_rr`：在 |delta|=0.25 处插值 call/put IV，ATM 取 |delta|≈0.50；`rr_25 = call_25d_iv − put_25d_iv`，`skew_norm = rr_25/atm_iv`。
- blend：24h 权 0.45 / 48h 权 0.55 → `rr_blend`、`atm_blend`、`skew_norm_blend`。
- **方向（相对，非原始符号）**：
  - `rr_z` = `rr_blend` 对滚动基线的 robust-z（MAD 标度，归一到 [−1,1]）。
  - `delta_rr` = `rr_blend − history[−lookback]`（偏斜动量）。
  - `vote = clamp(0.6·rr_z + 0.4·delta_rr_term, −1, 1)`（`delta_rr_term` 以 ATM vol 标度表达后夹紧）。
- `vote_confidence`：OK 到期数越多越稳（0.5+0.15·n）；基线未暖（history<min_history）×0.6；近到期（min hours<`near_expiry_downweight_hours`8h）×0.6。
- 输出 `data_state(OK/MISSING/INSUFFICIENT) / rr_blend / skew_norm_blend / rr_z / delta_rr / vote / vote_confidence / lean`，schema `SCHEMA_SKEW`。

## 3. 关键阈值（现值，`config.py:279-290`）
`srd_target_delta=0.25`、`srd_atm_delta=0.50`、`srd_rr_baseline_window=240`、`srd_rr_baseline_min_history=12`、`srd_delta_rr_lookback=6`、`srd_min_open_interest=1.0`、`srd_near_expiry_downweight_hours=8.0`、`srd_vote_scale=1.0`、`deribit_option_strikes_each_side=8`。

## 4. 整合中的路径修改
**零代码改动**。SRD 与 GGR 共用一次 Deribit `public/ticker` 取数（near-money 取 greeks.delta/gamma/mark_iv/open_interest），整合不动该取数路径。EDB 内 SRD 原始值直接读因子 payload（不经 0 权重过滤）。

## 5. 当前目标 / 待办
- P0 校准：`srd_vote_scale`、`*_vote_ref` 类按原始量 p85–90 定；vote 对前向 label 的 IC 决定是否调 base_weight(0.70)。
- 冷启动：基线未暖时 vote_confidence 自动打折，实盘需累计 ≥12 条 RR 历史。

## 6. 边界与陷阱
- **最大陷阱**：把 `rr_blend` 的负值读成看空。BTC 偏斜结构性为负，方向只看 `rr_z`/`delta_rr` 的相对变化（已在代码强制）。
- 25Δ RR 比较不同行权价的 call/put（非同价），put-call parity 只约束同行权价，故此比较合法可用（见因子卡）。
- "显示 0" 多为冷启动 vote=0，实盘 rr_blend 真实（如 −0.047）——前端已直读原始 RR。
