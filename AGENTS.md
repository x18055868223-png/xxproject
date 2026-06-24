# Project Agent Instructions

This repository contains the neutral-loop integration workspace, deployable FMZ single-file artifacts, and signal audit archive scaffolding. Prefer repository-relative paths, preserve the current dry-run safety defaults, and keep signal observability changes separate from trading logic unless the user explicitly asks otherwise.

## Repository authority

- `https://github.com/x18055868223-png/xxproject` (`xxproject`) is the primary project repository and the default target for project baseline, release, tag, and server-deployment decisions.
- `signal-audit-deploy` is only a deployment/helper mirror for the static audit surface. Do not treat it as the project main repository, do not use it as the authoritative main branch, and do not conclude the project baseline is updated merely because changes were pushed there.
- Before any commit, tag, push, or server deployment instruction, verify the active remote and state the intended target. If `origin` points to `signal-audit-deploy`, add or use the `xxproject` remote for project-level releases.
- Never force-update `xxproject/main` from a history that would delete or replace the wider project asset tree. If histories differ, integrate changes on top of `xxproject/main` in an isolated worktree and preserve the full project asset surface.

## Release and asset coherence

- 发布、打 tag、推送、服务器部署前，必须同时核对三层版本一致：Git release/tag 所在提交、`demo/最新交付物/neutral_regulation_demo_fmz.py` 内部 `demo_version`、服务器最新真实审计卡 `identity.strategy_version` 与关键 schema 字段。
- 不得只因为 `xxproject/main`、前端 `app.js?v=...` 或 systemd 自检通过，就宣称 FMZ 信号层本体已经完成更新。
- 涉及信号审计 JSON schema 的改动，必须增加 producer 级测试，直接调用 FMZ 本体生成/构造记录并断言关键字段存在；不能只依赖 materializer 或前端回填测试。
- 如果为了历史卡可读性在 materializer 做兼容回填，必须显式写入 `compat_backfill_applied=true` 和来源字段，不得伪装成源端原生输出。
- 服务器部署验收建议使用 `SESSION_CONTEXT_REQUIRED=1`；如果最新真实卡缺 `SignalSessionPremiseDurabilityContext`、`clock_window`、`backtest_delta_pp`、`validation_basis` 或 `decision_matrix.temporal_durability`，或仍带 `compat_backfill_applied=true`，或 `identity.strategy_version` 不是当前 FMZ 交付版本，不得封版。
- 使用 worktree 发布时，完成后必须同步根目录可见的 `demo/最新交付物/` 资产，或明确告知用户当前可见目录不是发布工作树；避免“远端已更新、本地交付物未更新”的混淆。

# Codex orchestration policy

## Project memory

- 开始处理复杂任务前，读取根目录 PROJECT_MEMORY.md。
- 将 PROJECT_MEMORY.md 视为项目事实的辅助来源，但始终以当前代码、配置、测试和用户最新指令为准。
- 如果记忆与当前代码冲突，以当前代码和用户指令为准，并在任务完成后修正过时记忆。
- 只在发现经过验证、稳定且可跨任务复用的信息时更新 PROJECT_MEMORY.md。
- 不得把秘密、令牌、密码、个人信息、临时日志、猜测或一次性任务状态写入项目记忆。

## Standing request for subagent delegation

本章节是用户对使用子 agent 的持续、明确请求。

对于符合“复杂任务”标准的请求，主 agent 必须主动启动项目级子 agent，不需要用户在每次任务中再次要求，也不需要仅为了启动子 agent 而请求确认。

子 agent 不是可选的装饰步骤。复杂任务存在适合独立调查、实现或验证的工作流时，应实际进行委派。

## Complex-task classification

开始每个任务时，先在内部判断任务是简单任务还是复杂任务。

满足以下任意一项时，通常视为复杂任务：

- 根因未知，需要跨文件追踪、代码库探索或实验验证。
- 涉及两个或更多模块、包、服务、应用层或基础设施层。
- 需要协调修改多个调用路径、公共接口或四个以上相关文件。
- 涉及架构设计、公共 API、数据库或数据迁移、兼容性、安全、权限、并发、性能或可靠性。
- 任务可以拆分成两个或更多互相独立的调查、实现或验证工作流。
- 需要多阶段计划、实现、测试、回归检查和最终审查。
- 用户明确要求全面分析、深度审查、并行处理或多 agent 协作。

以下通常属于简单任务：

- 单个局部文件中的明确小改动。
- 无需探索即可完成的机械修改。
- 单一、低风险且验证方式明确的修复。
- 简短解释、查找或格式调整。

存在不确定性时，根据任务风险、范围和可并行性判断，不要仅因为任务描述很长就认定为复杂。

## Required subagent workflow

对于复杂任务：

1. 主 agent 保留对需求理解、计划、共享接口、最终集成和最终答复的所有权。
2. 根据实际工作拆分，通常启动 2 至 3 个子 agent。
3. 只委派边界明确、结果可独立验证的工作。
4. 每个委派任务必须说明：
   - 目标；
   - 工作范围；
   - 不得触碰的范围；
   - 文件所有权；
   - 验收条件；
   - 返回结果的格式。
5. 优先并行执行读取、探索、根因分析、测试分析和代码审查。
6. 只有在文件所有权完全不重叠时，才允许多个 worker 并行修改代码。
7. 不允许两个 agent 同时修改同一文件或共享接口。
8. 子 agent 返回后，主 agent必须检查证据、解决冲突并自行决定是否采纳。
9. 不得盲目信任子 agent 的结论。
10. 主 agent 完成集成后，应启动 reviewer 或使用等价的独立验证步骤。
11. 等待所有当前任务所依赖的子 agent 完成后，再给出最终结论。
12. 最终答复中简要说明：
    - 启动了哪些 agent；
    - 每个 agent 负责什么；
    - 主 agent 如何验证和整合结果。

## Agent selection

只使用本项目 `.codex/agents/` 下定义并固定模型的 agent：

- explorer：代码库探索、调用链追踪、根因调查，不修改文件。
- worker：执行边界明确的代码修改，仅修改分配给它的文件。
- reviewer：独立检查正确性、安全、兼容性、回归和测试缺口，不修改文件。
- default：当任务不适合上述专门角色时，执行边界明确的通用分析任务。

不得主动使用没有固定模型配置的临时 agent 类型。

不得静默将这些 agent 替换为较低模型或较低推理强度。

如果 gpt-5.5 或 xhigh 暂时不可用：

- 明确报告模型或容量问题；
- 不得伪装成已经使用了指定配置；
- 不得静默降级；
- 主 agent 可继续完成当前能可靠完成的部分，并清楚说明哪些委派未能执行。

## Delegation phases

复杂实现任务优先按以下阶段处理：

### Phase 1: Exploration

让 explorer 或 default 分别调查不同问题，例如：

- 相关代码路径和模块边界；
- 根因和复现条件；
- 测试入口和潜在回归面；
- 项目约束和既有实现模式。

### Phase 2: Planning

主 agent 根据探索结果形成统一计划，解决不同 agent 结论之间的冲突。

### Phase 3: Implementation

将互不重叠的实现任务分配给一个或多个 worker。

每个 worker 必须拥有清晰且不重叠的文件范围。涉及共享接口、迁移顺序或核心架构的修改由主 agent 统一完成或串行完成。

### Phase 4: Independent review

代码集成后，让 reviewer 独立检查：

- 实际行为是否满足需求；
- 是否存在逻辑错误或行为回归；
- 是否存在安全、权限、并发和兼容性问题；
- 测试是否覆盖关键路径和失败路径；
- 是否存在未完成、占位或绕过验证的实现。

### Phase 5: Final validation

主 agent 运行适用的测试、lint、类型检查、构建或其他验证命令，并检查最终 diff。

## Subagent discipline

所有子 agent 都必须遵守以下规则：

- 仅处理父 agent 明确委派的范围。
- 不自行扩大需求。
- 不启动更深层子 agent。
- 不修改未分配的文件。
- 不撤销用户或其他 agent 的现有修改。
- 不把未经验证的假设表述为事实。
- 优先返回文件路径、符号、命令结果和可复现证据。
- 发现范围外问题时只报告，不擅自修改。
- 完成后返回简洁摘要、证据、风险、修改文件和验证结果。

## Simple-task behavior

简单任务直接由主 agent 完成，不启动子 agent。

不要为了形式上满足多 agent 规则而拆分没有独立价值的小任务。

## Quality gate

无论是否使用子 agent，任务完成前都应：

- 检查修改范围；
- 检查是否意外改动无关文件；
- 运行与改动相关的最小充分验证；
- 明确说明未运行或无法运行的验证；
- 不声称没有实际执行过的测试已经通过。
