# v0.5.3 验证边界

## 必须成立

- schema 为 `nrd.schema.v0.5.3`
- 公共结果包含 `strategy_recommendation.expiry_24h`
- 公共结果包含 `strategy_recommendation.expiry_48h`
- `strategy_recommendation.order_layer` 固定为 `external_execution_program`
- module states 只包含 External Gate / Anchor / TMV-F
- FactorSnapshot 包含 Anchor、TMV-F flow、MPF、M-DIE、Neutral Repair、EDB、SRD(`skew`)、GGR(`gamma_regime`)、Signal Events、`bias_thesis`(legacy helper) 与策略推荐摘要
- `strategy_recommendation` 方向：EDB 有可交易 lean 时 `selection_reason=EDB_DIRECTION`，否则回落 TMV-F 并标 `TMVF_LEGACY_PREVIEW`
- MPF 显示最近数据时间、数据年龄、中文标签和组件解读
- TMV-F micro-flow 显示 4h 快窗、12h 慢窗、涨跌百分比和 CVD BTC
- M-DIE 出现在因子表和图表序列中
- Neutral Repair 输出 DIE+Anchor 修复状态、事件上下文、Anchor 上下文和 gating
- EDB 输出到期方向合成表（六证据 vote/weight、置信/一致度/覆盖度），DIE+Anchor 窗口未激活时支持标签保持 `NO_TRADE_BLOCKED`
- GGR 负 Gamma 放大达否决强度，或宏观/资金硬阻断时，EDB 置信归零（`veto_reason` 记录来源）
- SRD 方向取相对偏斜 `rr_z`+动量 `ΔRR`，不把结构性负 RR 读成看空
- Signal Events 输出顶层状态表，最多记录最近 10 次确认信号
- DIE+Anchor 使用 episode 合并、滞后冷却、反向确认和 Anchor 分数下滑受损检测
- MPF 使用因子适配尺度，US10Y 按真实 bps 处理，VOLQ 单项冲击不单独硬阻断

## 必须不存在

- 期权订单簿读取
- 期权报价归一化
- 候选组合构建
- 组合风险预算
- 深度成本评估
- 具体腿字段
- 下单意图或执行预览

## 本地验证链

```powershell
python -m compileall demo
tools/build_fmz_single.ps1 -Check
tools/update_delivery_summary.ps1 -Check
tools/fmz_preflight_demo.ps1
tools/static_validate_demo.ps1
tools/runtime_check_demo.ps1
```
