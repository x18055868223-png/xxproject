# execution_build —— 执行 FMZ 构建区

> 当前口径（r2.1 / 2026-06-19）：本目录的 `realsrc/` 已演进到 `STRATEGY_VERSION=2.5.0`，并可生成 `demo/最新交付物/spm_calendar_protected_short_v1.py`。下方 v0.1 / v1.6.2 / Phase 1-8 描述是早期构建规划和历史基线，不是当前交付物版本。当前执行层仍默认全空跑，交易门未启用。

> 目标交付物：`nrd_execution_fmz.py`（执行层 FMZ 单文件，含 21 因子 + ExecutionSession 编排 + ApprovalIntent）。
> 当前：demo v0.1 仅定计划，未开工。**整合主战场在这里。**

## 1. 基线
- 执行 v1.6.2 单文件 `Deribit期权交易执行层\spm_calendar_protected_short_v1.py`（开发态 `src/`）。
- 对冲 `src/hedge_risk.py`（position_risk.v0.3）。
- VRP `系统总纲\VRP\src\`（v1.1，未嵌入）。

## 2. 组装原则（按收束架构）
- 以 ExecutionSession 状态机为骨架，**按域顺位**逐域接入因子（域顺序见架构 §3.2 / 脊柱 `EXECUTION_DOMAIN_ORDER`）。
- 每域因子收束为一个域包；域间只经包耦合。
- 占位域（portfolio_risk/position_manage/attribution）按 02 规范的安全默认接线，注册表标 PLACEHOLDER。

## 3. 分阶段构建（= 总纲 v0.5 §9 Phase 1-8）
| Phase | 本目录动作 | 涉及因子(注册表 id) | 验收 |
|---|---|---|---|
| 1 减法 | 复制基线→删 KPF | X5,X6(KPF_CUT) + X17(persistence两项) + X16(升v0.4) | 回归+离线 fixture 绿、错配率不劣化 |
| 2 会话重构 | ExecutionSession + ApprovalIntent | M1,M2（前置 FMZ 交互 spike） | 会话内锁定/授权/TTL/hash，无重启 |
| 3 VRP 嵌入 | pricing_gate 域 + 4原语收口 | X1-X4 | VRP BLOCK 不进可锁定方案、不进权重 |
| 4 账本+归因 | ledger 增强 + attribution | X9(VRP血缘/session) + X20 | 锚点可追溯、最小归因落盘 |
| 5 组合预算+赢家管理 | portfolio_risk + position_manage 域 | X10-X12 + X13-X15 | 超预算 size=0；止盈/末日Gamma 干跑 |
| 6 全链回放 | replay（离线） | X21 | 分桶扣成本净期望出数 |
| 7 闸AB | 闸A管道验证→闸B小额实盘 | 全链 | 见 v0.5 §10 闸B硬前提清单 |
| 8 对冲实盘+VRP edge | ALLOW_HEDGE_TRADING + 多时点IV | X16-X19 实盘 / X1-X3 edge | 独立回放通过 |

## 4. 收束干净红线
- 每接入一个域，先在 `factor_registry.json` 确认该域因子已登记、状态正确。
- 占位域接入后跑 `python ../shared/factor_spine.py` 自检 PASS。
- 占位因子绝不放松门：组合预算偏紧、止盈偏早、sizing 偏小（02 规范 §3 铁律）。
- 面板每个拒绝落到一个明确门（信号/VRP/报价/S:PM/库存/授权/账本/预算/对冲）。

## 5. FMZ 工程注意（沿用既有经验）
- 单文件由 `src/` 合成（`build_bundle.py` 模式）；CRLF+BOM 与 demo 包 EOL 差异需字节级处理（见信号层 HANDOFF 经验）。
- `ALLOW_TRADING` 默认 False；`ALLOW_HEDGE_TRADING` 独立；kill-switch 保留。
- ApprovalIntent 落地前必须 spike 验证 FMZ 命令栏交互 + `_G()` 持久化 + TTL 回退。
