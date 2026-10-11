---
name: xxproject-release
description: Use before any commit, push, tag, FMZ deliverable sync, or server deployment in the xxproject repo — verifies remote/branch, classifies release scope, enforces the page-confirmation and three-layer version gates, and handles the local exclude trap.
---

# xxproject 发布门

本 skill 把 `AGENTS.md` 与 `docs/信号审计更新部署规范_v1.0.md`（文件标题 v2.0）中的发布规则落成检查顺序。规范原文优先；若本 skill 与规范或代码不一致，停止并先修正其一。

任何一步失败即停止并告知用户，不通过放宽检查、重试或跳过来推进。

## 1. 预检（每次都做）

```bash
git remote -v
git rev-parse --abbrev-ref HEAD
git rev-parse HEAD
git status -sb
git status --ignored --short -- docs demo AGENTS.md README.md PROJECT_MEMORY.md
```

- 推送目标只能是 `xxproject`。`origin` 是 signal-audit-deploy 镜像，任何时候都不推。
- 当前分支应为 `claude/astra`（或用户本次明确指定的分支）。向 `codex/*`、`main` 推送、合并或 force-push 需要用户本次明确授权；不得 force 更新 `xxproject/main`。
- 向用户说明：目标 remote、分支、待推 HEAD 及其相对远端的提交列表。

## 2. 判定发布范围

逐项回答，范围决定适用的门：

| 改动涉及 | 适用门 |
|---|---|
| `deploy/signal_audit/frontend/`、工作台页面渲染 | 页面门（§3） |
| `demo/最新交付物/neutral_regulation_demo_fmz.py` 或 FMZ 本体 | 三层版本门 + 交付物同步（§4） |
| 信号审计 JSON schema | producer 级测试：直接调用 FMZ 本体构造记录并断言关键字段；仅 materializer/前端测试不算 |
| materializer 兼容回填 | 必须写 `compat_backfill_applied=true` 与来源字段 |
| LLM prompt / schema / validator / entrypoint | 规范阶段 B–C：失败路径测试 + 隔离 canary；canary 未过禁止推送 |
| 服务器 / systemd / timer | 规范阶段 F–H；只在用户本次明确授权部署时进行 |
| 仅文档、测试、工具 | §5 即可 |

## 3. 页面门

- 本地启动页面，桌面 1440×900 与移动 390×844 实际浏览检查：层级、溢出、长中文、旧卡、错误卡、无机器字段泄漏。
- 遵守信息架构规则：`重点清晰、逻辑贯通、关键内容全面`；原始 ID / hash / 机器标志留在下层追溯区。
- 用户必须针对**本版本**明确回复 `当前本地页面可推送`。之前版本的确认、测试全绿都不能替代。未获确认 → 不提交、不推送、不部署。

## 4. 三层版本门（FMZ / 发布 / tag / 部署）

三处必须一致后才可称“已发布”或封版：

1. Git 提交 / tag 所在 SHA；
2. `demo/最新交付物/neutral_regulation_demo_fmz.py` 内部 `demo_version`；
3. 服务器最新真实审计卡 `identity.strategy_version` 及关键 schema 字段。

- 服务器验收建议 `SESSION_CONTEXT_REQUIRED=1`；最新真实卡缺 `SignalSessionPremiseDurabilityContext`、`clock_window`、`backtest_delta_pp`、`validation_basis`、`decision_matrix.temporal_durability`，或带 `compat_backfill_applied=true`，或版本不匹配 → 不得封版。
- FMZ 本体更新必须同步两处：本工作分支中的文件，以及用户可见根目录 `C:/Users/Xu/Documents/中性回路整合工程/demo/最新交付物/`。未同步时在最终答复中明确说明“根目录交付物未同步，不可直接复制到 FMZ”。
- 不得仅凭 `xxproject/main`、`app.js?v=...` 或 systemd 自检通过就宣称 FMZ 本体已更新。

## 5. 提交

- 只提交已验证、在范围内的文件，按显式清单 `git add`。本机 `.git/info/exclude` 忽略 `/docs/`、`/demo/` 等路径：其中新文件须 `git add -f`，提交前用 `git status --ignored` 确认没有漏掉也没有多加；不整体加入 `.artifacts/`。
- 提交前检查 diff 中无密钥：

```bash
git diff --cached | grep -nEi 'api[_-]?key|secret|token|password|BEGIN .*PRIVATE KEY|sk-[A-Za-z0-9]{16,}'
```

- 运行与改动相关的最小充分测试（`python -m pytest tests/<module>.py -q`），在答复中列出实际命令与结果；未运行的明确写“未运行”。

## 6. 推送与收尾

```bash
git push xxproject claude/astra
git ls-remote xxproject refs/heads/claude/astra
```

- 记录推送后的不可变 SHA，并确认远端 SHA 与本地 HEAD 完全相等；不以分支名或 `FETCH_HEAD` 推断部署版本。
- 收尾时检查 `PROJECT_MEMORY.md` 与部署规范是否需要补充**经验证、可复用**的规则；不写密钥、卡 ID、临时目录或一次性事故日志。
- 最终答复分层给出证据：本地测试 / 推送 SHA / 页面确认 / FMZ 交付物同步 / 服务器与真实卡验收——每层各自取证，未做的层明确标注。
