# audit_archive — 信号审计留档目录（样例）

按《信号审计 JSON、FMZ 推送与静态 Web 标准 v1.0》
(`docs/信号审计JSON推送与静态Web标准_v1.0.md`) 建立。**本目录当前为样例**：
内容由一张合成样例卡生成，用于确认 JSON / 推送格式，前端样式由用户后续打磨。

## 结构
- `source/signal_review.jsonl` — 唯一事实源，一事件一行（FMZ 运行时本机累积；此处为样例行）。
- `cards/YYYY/MM/DD/<card_id>.json` — 单卡物化（可由 jsonl 重建）。
- `public/data/index.json` — 检索索引（不含全量截面）。
- `public/data/cards/<card_id>.json` — 页面按需读取的单卡。
- `public/index.html` — 静态前端占位（**待打磨**）。
- `state/export_checkpoint.json` — jsonl→派生 进度/重试。
- `samples/fmz_push_brief.txt` — FMZ ` @` 简要推送样例（≤160 字符单行）。

## 两份审计样例（请确认）
1. **全量本地 JSON**：`source/signal_review.jsonl`（压缩单行）+ `cards/.../<id>.json`（易读）。
2. **FMZ 简要推送**：`samples/fmz_push_brief.txt`。

## 流程（运行时）
确认信号 → 生成全量卡 → 追加 `source/signal_review.jsonl`（先落盘）→ 派生单卡 JSON 与 `index.json`
→ 同步 `public/` 到留档服务器 → 静态页检索/可视化；FMZ 仅推简要版。留档/站点故障不阻断信号生成。

## 再生成样例
`python tools/audit_sample_gen.py <本目录>`（在 `中性回路 - opus4.8` 仓库根运行）。
