# 01 · leg_selection（选腿）

> 模块：② 执行层
> canonical：`Deribit期权交易执行层\src\leg_selection.py`（`legsel_*`，纯逻辑可单测）
> 最后核对：2026-06-02（源码）

## 1. 一句话定位
把"方向 + DTE/Delta 范围"映射为具体合约：OTM 侧按**目标 delta** 选短腿，按**腿宽**选保护腿。到期一律用合约自带 `expiration_timestamp`，不解析合约名。

## 2. 当前具体实现（`leg_selection.py`）
- `legsel_is_call_bias`：`SHORT_CALL`=卖 call。
- `legsel_pick_expiry_instruments` / `legsel_expiries_in_band`：在 `[dte_min_h, dte_max_h]` 内选最接近 center 的到期 / 枚举区间内所有到期（方向匹配）。
- `legsel_short_enriched`：OTM 侧（call 在现价上方 / put 下方），按距现价由近到远取前 `scan_limit`(15) 档，附 `_delta`。
- `legsel_pick_nearest_delta(enriched, target_delta, kpf_core, kpf_near)`：选 `|delta|` 最接近 `target_delta` 的档。**当前含 KPF 软约束**：排除落在 `kpf_core` 内部的档，并列再按靠近 `kpf_near`。
- `legsel_protection_candidates(prot_insts, short_strike, want_call, width_band, delta_of, deep_otm_max_delta=0.05, kpf_far)`：更外侧、腿宽优先落 `width_band`、排除过度虚值（`|delta|<deep_otm_max_delta`）；排序按腿宽接近区间中心，**当前并列按靠近 `kpf_far`**；带外档兜底排后。

## 3. 关键阈值（现值，`config.py`）
`SHORT_DELTA_RANGE=(0.15,0.45)`、`PROTECTION_WIDTH_RANGE=(2000,2500)`、`DEEP_OTM_MAX_DELTA=0.05`、`scan_limit=15`（函数默认）。目标 delta 由 `plan_preferred_delta`（信号置信→偏好 delta）给。

## 4. 整合中的路径修改（Phase 1 减法）
**当前仍含 KPF 软参，待删**：
- `legsel_pick_nearest_delta` 去掉 `kpf_core/kpf_near` → 退化为**纯目标 delta 选档**。
- `legsel_protection_candidates` 去掉 `kpf_far` → 退化为**纯腿宽区间 + 过度虚值过滤**。
- 调用方 `strategy.py:118-173` 去掉 KPF_* 入参。
> 减法依据：delta 即市场隐含触碰概率，按 delta 选档天然避开高触碰概率 strike，原"不落进 KPF 争夺核心"由此承接，不留盲区（总纲 v0.3 §3.2）。

## 5. 当前目标 / 待办
- Phase 1 删 KPF 软参后跑 `tests/test_leg_selection.py` 确认纯 delta/腿宽路径全绿。
- delta 选档的 `scan_limit`、`deep_otm_max_delta` 属流动性鲁棒参数，非校准重点。

## 6. 边界与陷阱
- 选腿**不判方向**——方向来自信号层 `DIRECTION_BIAS`。
- 保护腿必须在卖方腿**外侧**（更虚值），且过度虚值（彩票腿）被 `deep_otm_max_delta` 挡掉。
- KPF 软参当前是"软约束"（有 outside 才用、并列才用），删除是纯减法、不改主选档逻辑。

## 7. 数据增强（gexmonitorapi，2026-06-04）
- 新增独立 `legsel_wall_proximity(short_strike, spot, gex_info, want_call)`：诊断短腿是否近 magnet / GEX 墙 / vol_trigger，**只出建议、不进 `plan_rank` 排序**（选腿仍纯 delta，与 Phase 1 删 KPF 对齐，不复活空间层）。`gex_info` 缺失即惰性。详见 [`00_总纲/gexmonitorapi数据增强引入说明_v1.0`](../../00_总纲/gexmonitorapi数据增强引入说明_v1.0.md)。
