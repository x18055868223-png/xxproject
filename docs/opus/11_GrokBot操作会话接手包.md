# GrokBot 操作会话接手包（2026-10-20）

## 1. 分工
- **主会话**「中性回路项目梳理与技能选择」负责：服务器 xx-eu、存档代码、部署、审计结论。
- **操作会话**「xxproject 定时任务审计回执」负责：用桌面控制操作 Grok Bot 程序里的 Cici 对话，读回执、转发经用户确认的指令。
- 两边用 SendMessage 互通。操作会话收到 Cici 的新回复后，把原文转给主会话。
- 操作会话**不登录服务器、不改代码**；需要核对的事交给主会话。

## 2. 操作边界（用户要求）
- 只在 Cici 对话里读写，只发和 xxproject 有关的消息。
- 不改 Grok Bot 的账号、设置、凭据、例程配置（例程由 Cici 自己改）。
- 不操作 Edge 里的 FMZ 页面，不碰 Cisco Secure Client。
- **每条新指令发给 Cici 之前，先把原文给用户看，用户确认后再发。**
- 回执要逐字抄录；截图只在需要时保存。

## 3. 背景（Cici 是谁、在做什么）
- Cici 是 GrokBot 实例，项目里的身份是长期研究员。它不参与日常决策，只研究冻结样本存档。说明书是存档里的 `MANUAL.md`，本仓库对应的是 `docs/opus/10`。
- 它在服务器上用 radar 账户（只读存档），唯一可写的位置是 `/var/lib/xxproject/research_inbox`。存档每 10 分钟登记一次投递，回执写在 `receipts/`。
- 它能投递的类型：`episode_view`、`postmortem`、`hypothesis`、`protocol_proposal`、`research_note`。
- 已登记的投递：
  - `ev-20261020-call-82500-v1`、`ev-20261020-put-81500-v1`（episode_view）；
  - `research_note:runbook-2026-10-20`（运行清单，主会话已在服务器核对，sha256 eedd5447…8cd3f）。

## 4. 接下来的时间点（UTC）
| 时间 | 事件 | 操作会话要做的 |
|---|---|---|
| 10-21 06:30 前 | Cici 应提交 21OCT 合约的 episode_view | 不用催；若用户问起，读 Cici 状态 |
| 10-21 08:10 起 | 存档首次写入 outcomes/、paths/ | 无 |
| 10-21 08:30 后 | Cici 应开始做复盘（postmortem） | 读回执转主会话 |
| 10-21 约 04:00 | Cici 承诺交第二份 research_note（24 小时实测） | 抄回执转主会话 |
| 10-21 09:30 后 | 存档出现 validation/、promotions/ | 无 |

## 5. 待发给 Cici 的调整（主会话审计结论，需用户确认后发）
见主会话给用户的答复；用户确认后由主会话通过 SendMessage 发来定稿。操作会话收到定稿后，再给用户看一遍原文，用户确认后发出。
