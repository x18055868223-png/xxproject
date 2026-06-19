# 01 · materializer（审计卡片生成）

> 模块：审计静态页面部署模块
> canonical：`tools/materialize_signal_cards.py`
> 最后核对：2026-06-19（r2.2 文档收纳）

## 1. 一句话定位

materializer 把 FMZ 产生的 `signal_review.jsonl` 转成前端可直接读取的 `signal_cards/index.json`、单卡 JSON 和 `fallback.js`。

## 2. 当前输入输出

| 类型 | 路径/字段 | 说明 |
| --- | --- | --- |
| 输入 | `--source signal_review.jsonl` | FMZ 信号层已写出的审计 JSONL |
| 可选输入 | `--llm-reviews signal_llm_reviews.jsonl` | Gemini LLM sidecar 输出 |
| 输出 | `signal_cards/index.json` | 卡片列表、过滤索引、生成时间 |
| 输出 | `signal_cards/*.json` | 单张审计卡 |
| 输出 | `signal_cards/fallback.js` | file mode / fallback fixture |

## 3. 整合路径

systemd timer 定期运行 materializer；LLM sidecar 成功后也会触发 materializer，使前端展示最新 `llm_review`。

## 4. 边界与陷阱

- materializer 不负责生成信号。
- LLM 缺失时应保留原卡，不破坏前端渲染。
- 1GB 服务器上应读取尾部窗口，避免长期全量读大 JSONL。
- 输出中的 `source` 是审计来源，不应泄露 token 或密钥。
