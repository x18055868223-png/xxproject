# LLM sidecar：DeepSeek 复核

> canonical：`tools/signal_llm_review.py`、`tools/signal_llm_review_entry.py` 与 `deploy/signal_audit/run_signal_llm_review.sh`

该 sidecar 位于 FMZ 信号循环之外，只读取已经生成的审计卡并写入独立 JSONL；它不回写 producer，不改变方向、置信、门控或交易许可。

| 项目 | 固定值 |
| --- | --- |
| provider | `deepseek` |
| model | `deepseek-v4-flash`（DeepSeek‑V4‑Flash‑0731） |
| main schema / prompt | `signal_llm_review@1.5.1` / `signal_llm_review_prompt@1.5.5` |
| entrypoint | `signal_llm_review_entry@1.1.9` |
| transition schema / prompt | `signal_transition_llm_review@1.3.0` / `signal_transition_llm_review_prompt@1.3.2` |
| 主信号 | blind `low` 后 reconciliation `high`，正常严格两次；reasoning-only 空正文时复用 blind 并追加最多一次 recovery |
| 状态转移 | `low`，一次逻辑调用 |

运行时只读取 `LLM_API_KEY`，不读取任何 Gemini 密钥或兼容变量。API base 为 `https://api.deepseek.com`；Bearer 请求由本地 JSON schema 与语义校验器最终裁决。历史 Gemini sidecar 可以继续被前端只读展示，但不会被回填或重新标记为 DeepSeek。

`integrated_trade_advisory.future_24h_bayesian_report` 是只读的 24 小时三情景推断，输入范围固定为卡内事实加模型参数先验，不接入实时搜索。三情景权重是模型主观权重，不是校准胜率；点位必须区分卡内观测与模型估算观察位。

调度默认每次完成后 60 秒再运行，main/transition 每轮各最多 4 条；北京时间每日最多 60 次真实 HTTP 请求。请求、响应正文和密钥不写入 usage ledger。

DeepSeek V4 在思考模式下会把兼容参数 `low` 映射成 `high`。为保持上述运行语义，主信号 blind 与默认 transition 的本地 `low` profile 使用官方非思考模式；reconciliation 的 `high` profile 使用思考模式并显式发送 `reasoning_effort=high`。这不改变严格双调用顺序，也不降低最终 24 小时贝叶斯报告的高推理复核。
