# 03 · LLM sidecar（Gemini 复核）

> 模块：审计静态页面部署模块
> canonical：`tools/gemini_signal_llm_review.py` + `deploy/signal_audit/run_signal_llm_review.sh`
> 最后核对：2026-06-23（r3.2 两次调用真盲审）

## 1. 一句话定位

LLM sidecar 在 FMZ 主策略外读取真实信号卡，调用 Gemini 生成审计复核意见，并写入 sidecar JSONL。从 r3.2 起，每张新卡最多触发两次 Gemini 调用：第一次只读取盲包生成 `theoretical_active_view` 与 `gamma_regime_lens`，第二次读取盲读结果和完整审计包生成最终复核。

## 2. 当前锚点

| 字段 | 当前值 |
| --- | --- |
| provider | `gemini` |
| model | `gemini-3.5-flash` |
| schema | `signal_llm_review@1.3.0` |
| prompt | `gemini_signal_review_prompt@1.3.0` |
| blind mode | `two_call_strict` |
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
- 真实信号复核依赖服务器 `/etc/signal-audit/llm.env` 中的双通道 key：`GEMINI_CHANNEL1_API_KEY` 为低成本/免费优先通道，`GEMINI_CHANNEL2_API_KEY` 为付费 fallback 通道；两个都为空时 systemd oneshot 可成功退出但不会调用 API。
- 如果 API 用量页面没有调用记录，优先检查两个通道 key 是否为空、`signal-audit-llm-review.service` 是否真正运行，以及最新 `card_id` 是否写入了 `status=OK`、`blind_review_mode=two_call_strict`、`llm_call_count>=2` 的 sidecar。sidecar 中的 `api_key_route` / `llm_call_routes` 可用于确认本轮走通道 1、通道 2 或 mixed。
- 调用失败不能阻塞 FMZ JSONL 或 materializer。
