# Claude Code 工作约定

@AGENTS.md

上面导入的 `AGENTS.md` 是项目规则的唯一来源（分支、远端、git 优先、发布门、agent 委派均在其中）。本文件只补充 Claude Code 落地细节；冲突时以 `AGENTS.md` 与用户最新指令为准。

## 本机忽略规则的陷阱

共享的 `.git/info/exclude` 忽略了 `/docs/`、`/demo/`、`/AGENTS.md`、`/README.md`、`/PROJECT_MEMORY.md`、`/audit_archive/`、`/.codex/` 及各编号目录。这些路径下的**新文件**不会出现在 `git status` 中，提交时必须按显式清单 `git add -f <path>`，并用 `git status --ignored` 复核。已跟踪文件的修改不受影响。不得整体加入 `.artifacts/`。

## 常用入口

- 先读 `PROJECT_MEMORY.md`（复杂任务）与 `docs/astra/README.md`（接手说明）；信号审计迭代另读 `docs/信号审计更新部署规范_v1.0.md`。
- 测试：`python -m pytest tests/<module>.py -q`（Python 3.12，pytest 9）。只报告实际运行过的测试。
- 计划文档按既有惯例保存到 `docs/superpowers/plans/YYYY-MM-DD-<topic>.md`（新文件需 `git add -f`）。
- 提交、推送、打 tag、同步 FMZ 交付物或服务器部署前，使用项目 skill `xxproject-release`。
- `gh` 位于 `~/bin/gh.exe`，默认不用；仅在需要 PR / CI / release 且用户已 `gh auth login` 时使用。
