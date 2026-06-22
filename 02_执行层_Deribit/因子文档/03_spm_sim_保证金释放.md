# 03 · spm_sim（S:PM 保证金释放校验）

> 模块：② 执行层
> canonical：`Deribit期权交易执行层\src\spm_sim.py`（`spm_*`）
> 最后核对：2026-06-02（源码）

## 0. 轻量因子卡

| 字段 | 内容 |
|---|---|
| 因子 | spm_sim（保证金释放校验） |
| 所属回路 | ② 执行层 · Deribit |
| 作用层 | 风险门 |
| 理论机制 | 通过交易所组合保证金模拟比较短腿裸卖与带保护结构的 IM 变化，确认保护腿是否真实释放保证金。 |
| 预期符号 | `relief_ratio` 越高越好；低于门槛则候选不合格。 |
| 适用周期 | PLAN 候选通过保护腿筛选后逐个模拟。 |
| 与现有因子重叠 | 与 leg_selection 的保护腿、plans 的价值指标共享候选；不替代交易所模拟结果。 |
| 主要失效条件 | 账户非组合保证金、模拟 API 失败、im_b 异常、候选顺序导致漏看更优释放。 |
| 改变的决策 | 改变候选是否通过保护腿硬门及对应 plan 记录。 |
| 当前状态 | ACTIVE |

## 1. 一句话定位
通过 Deribit 组合保证金（Portfolio Margin）的**模拟下单接口**，逐笔确认"远期保护腿到底释放了多少初始保证金（IM）"，作为保护腿是否合格的硬门。

## 2. 当前具体实现（`spm_sim.py`）
- `spm_account_is_portfolio_margin(account_summary)`：校验账户确为 S:PM（`portfolio_margining_enabled` 或 model 含 "pm"），返回 `(ok, model)`。非组合保证金 → 候选直接 `reject="账户非组合保证金"`。
- `spm_simulate_structure(currency, short, protection, amount)`：调 `dbt_simulate_portfolio` 模拟两个场景：
  - B = 仅 `{short: −amount}`
  - C = `{short: −amount, protection: +amount}`
  返回 `im_short_only(B) / im_with_protection(C) / relief_abs / relief_ratio / mm_* / available_funds_*`。
- `spm_relief(im_b, im_c)`：`relief_abs = im_b − im_c`；`relief_ratio = relief_abs/im_b`（im_b≤0 时 ratio=0）。
- `spm_evaluate_candidates(...)`：按已排序的保护腿候选**逐个模拟**，返回**第一个** `relief_ratio ≥ min_ratio` 的报告（`accepted=True`）；全不达标返回 relief 最大的一次 + `accepted=False`。`attempts` 记录全过程。

## 3. 关键阈值（现值，`config.py`）
`MIN_MARGIN_RELIEF_RATIO=0.10`（设计稿"极低"门槛，低于则保护腿不合格）。`ORDER_AMOUNT=0.1`（BTC 期权最小步长）。

## 4. 整合中的路径修改
**零 KPF 关联，整合不动**。S:PM 释放是删 KPF 后选腿"空间判断"的三支柱之一（delta + 腿宽 + **S:PM 释放**），总纲 v0.3 §2 明确它承接了部分空间角色。

## 5. 当前目标 / 待办
- `MIN_MARGIN_RELIEF_RATIO` 0.10 是保护腿合格门，回放可微调。
- 抵消机制（同币种同子账户跨到期 netting）已联网取证可行；仅逐笔确认幅度，不做额外复杂回路。

## 6. 边界与陷阱
- 它**只确认幅度，不假设**——必须真调交易所模拟接口拿 IM，不能用公式臆测释放。
- `relief_ratio` 是相对 B 的占比；im_b≤0（罕见）时按 0 处理，候选会被判释放不足。
- 候选按"锚点→逐档靠近 short"顺序试，取首个达标——不是取释放最大，是取**第一个够格**（省 API 调用）。
