# Goal Prompt：重建不消费信号层的 Manual Audit Gate 执行层

你是工作 Agent。请基于 `x18055868223-png/xxproject` 当前执行层基础，重建一版**不消费信号层数据**的干净执行层。

## 目标

构建 `manual_audit_execution_v3.0`：

- Human Audit Gate 是唯一桥连放行者；
- 信号层只作为人工审计材料，不作为执行层机器输入；
- 执行层不读取 SignalEvidencePackage、signal_bridge、FILE/G signal source、EDB、DIE、Anchor、TMV、CVD、Macro、Funding、Skew、GGR、LLM；
- 执行层只通过 FMZ 参数、命令或手动上下文接收最小执行输入：方向、DTE 范围、Delta 范围、腿宽、数量、审计卡 ID、风险政策；
- 执行层继续复用当前已实现的可靠资产：期权链读取、选腿、方案菜单、VRP、S:PM、建仓可行性、预算、确认码、预提交、entry campaign、保护腿优先、止盈、风险退出、对冲、恢复、孤儿清理、审计显示；
- 持仓后执行层独立管理，不依赖信号层在线。

## 必须实现

1. 新增 `ManualExecutionContext`，从 FMZ 参数或手动命令生成；
2. 使用 `MANUAL_PLANNING_ALLOWED` 控制是否进入计划轮；
3. 方向仅来自 `ManualExecutionContext.direction_bias`；
4. 没有人工上下文时，只显示 `WAIT_MANUAL_AUDIT_GATE`；
5. 计划轮只基于人工给定范围枚举候选；
6. 方案审批绑定 `manual_context_id`、`audit_card_id`、`plan_hash`、`config_hash`；
7. 持仓快照保存人工审计来源和 EntryRiskAnchor；
8. 对冲触发只使用执行层本地风险，不消费信号层；
9. 默认所有真实交易门保持 False；
10. 保留并执行 P0 安全测试：部分成交、私有读取三态、订单 UNKNOWN、动态 tick、累计 credit、有符号对冲目标等。

## 禁止实现

- 不要实现信号层自动开仓；
- 不要接入 SignalEvidencePackage；
- 不要用 EDB/GGR/Macro 直接驱动执行层；
- 不要让 LLM 结果进入执行路径；
- 不要让信号强度自动抬高 Delta；
- 不要删除已有执行安全资产；
- 不要跳过 P0 安全测试。

## 交付要求

提交：

- 新目录/新文件名；
- 新版本号；
- 修改文件清单；
- ManualExecutionContext schema；
- FMZ 参数说明；
- 新状态机；
- 继承资产和删除资产清单；
- 单元测试和故障测试；
- bundle 构建结果；
- 默认门控确认；
- 当前限制说明。

最终状态只能标记：

`MANUAL_GATE_PLAN_READY`、`MANUAL_GATE_DRY_RUN_READY` 或 `MANUAL_GATE_TESTNET_READY`。

不得标记为 `AUTO_SIGNAL_EXECUTION_READY`。
