# Claude Code 工作约定

@AGENTS.md

上面导入的 `AGENTS.md` 是项目规则的唯一来源（原为 Codex 编写，Claude Code 同样遵守）。本文件只补充 Claude Code 特有的落地方式；两者冲突时以 `AGENTS.md` 与用户最新指令为准。

## 分支与远端

- Claude 的工作分支是 `claude/astra`，本机工作树 `.worktrees/claude-astra`，上游 `xxproject/claude/astra`。
- `codex/astra-signal-rating-v1` 是此前 Codex 的 Astra 线（`claude/astra` 自其 `918471d` 分出）。未经用户明确要求，不向任何 `codex/*` 分支推送，也不在两线之间合并或 rebase。
- 只推送到 `xxproject` remote。`origin` 是 signal-audit-deploy 镜像，不作为推送目标。
- 推送凭据由 Git Credential Manager 提供；`gh` 位于 `~/bin/gh.exe`，需要用户自行 `gh auth login` 后才能操作 PR / release。

## 本机忽略规则的陷阱

共享的 `.git/info/exclude` 忽略了 `/docs/`、`/demo/`、`/AGENTS.md`、`/README.md`、`/PROJECT_MEMORY.md`、`/audit_archive/`、`/.codex/` 及各编号目录。这些路径下的**新文件**不会出现在 `git status` 中，提交时必须按显式清单 `git add -f <path>`，并用 `git status --ignored` 复核。已跟踪文件的修改不受影响。不得整体加入 `.artifacts/`。

## 子 agent 委派

`AGENTS.md` 中“复杂任务主动委派”的持续请求同样适用。`.codex/agents/` 的模型固定（gpt-5.5 / xhigh）只约束 Codex；Claude Code 按角色对应：

| AGENTS 角色 | Claude Code |
|---|---|
| explorer | `Explore` agent（只读） |
| worker | `general-purpose` agent，限定文件所有权 |
| reviewer | `/code-review` 或只读 `general-purpose` 复核 |

委派说明、文件所有权不重叠、主 agent 最终核验等纪律不变。

## 常用入口

- 先读 `PROJECT_MEMORY.md`（复杂任务）与 `docs/astra/README.md`（接手说明）；信号审计迭代另读 `docs/信号审计更新部署规范_v1.0.md`。
- 测试：`python -m pytest tests/<module>.py -q`（Python 3.12，pytest 9）。只报告实际运行过的测试。
- 计划文档按既有惯例保存到 `docs/superpowers/plans/YYYY-MM-DD-<topic>.md`（新文件需 `git add -f`）。
- 提交、推送、打 tag、同步 FMZ 交付物或服务器部署前，使用项目 skill `xxproject-release`。
