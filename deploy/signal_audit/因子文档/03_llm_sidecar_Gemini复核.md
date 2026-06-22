# 03 · LLM sidecar（Gemini 复核）

> 模块：审计静态页面部署模块
> canonical：`tools/gemini_signal_llm_review.py` + `deploy/signal_audit/run_signal_llm_review.sh`
> 最后核对：2026-06-19（r2.2 文档收纳）

## 0. 轻量因子卡

| 字段 | 内容 |
|---|---|
| 因子 | LLM sidecar（Gemini 复核） |
| 所属回路 | 审计部署链路 |
| 作用层 | 审计 |
| 理论机制 | 在 FMZ 主进程外读取真实审计卡，先盲读原始事实包再对照系统结论，输出只读复核意见 sidecar。 |
| 预期符号 | BLIND_REVIEW_OVERLAY |
| 适用周期 | 新信号卡出现后 / systemd LLM review timer。 |
| 与现有因子重叠 | 与 reasoning、decision、Gamma/GEX 展示重叠，但不覆盖系统结论，不改写门控。 |
| 主要失效条件 | API key 缺失、模型失败、输入包字段不完整、sidecar 与原卡 hash 不匹配。 |
| 改变的决策 | 改变人工审计建议、风险提示和 invalid_if，不改变方向、置信、阻断或交易许可。 |
| 当前状态 | ACTIVE |

## 1. 一句话定位

LLM sidecar 在 FMZ 主策略外读取真实信号卡，调用 Gemini 生成审计复核意见，并写入 sidecar JSONL。

## 2. 当前锚点

| 字段 | 当前值 |
| --- | --- |
| provider | `gemini` |
| model | `gemini-3.5-flash` |
| schema | `signal_llm_review@1.2.0` |
| prompt | `gemini_signal_review_prompt@1.2.0` |
| env | `/etc/signal-audit/llm.env` |

## 3. 输出边界

LLM 可以输出：

- `summary_cn`
- `agreement_with_system`
- `main_supporting_factors`
- `main_risks_or_conflicts`
- `operator_focus`
- `invalid_if`
- `gamma_regime_lens`
- `theoretical_active_view`

LLM 不允许改变：

- `decision`
- `reasoning`
- `blocking`
- `trade_allowed`
- 执行层交易门

## 4. 边界与陷阱

- LLM 是审计建议层，不是交易执行系统。
- 真实 key 不进仓库。
- 自检样本和普通观察轮次不应触发真实模型调用。
- 调用失败不能阻塞 FMZ JSONL 或 materializer。
