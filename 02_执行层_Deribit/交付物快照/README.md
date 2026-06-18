# Deribit S:PM 日历保护型卖方执行链 v1.0

FMZ（发明者量化）平台 **单文件 Python 策略**。设计稿见
[`deribit_spm_calendar_protected_short_v1_0_design.md`](deribit_spm_calendar_protected_short_v1_0_design.md)，
实现计划见 `~/.claude/plans/deribit-spm-calendar-protected-short-v1-adaptive-anchor.md`。

## 开发态 vs 交付态

- **开发态**：`src/` 模块化，便于精准迭代与本地单测。
- **交付态**：`spm_calendar_protected_short_v1.py`（由 `build_bundle.py` 合成的单文件，粘贴进 FMZ 运行）。

## 计划轮 / 下单轮（两轮分离，无运行时命令交互）

- **计划轮 `ROUND_MODE="PLAN"`**：枚举所有符合范围的备选（**垂直价差 + 日历价差都枚举**），
  按 胜率 / 盈亏比 / KPF 空间支持 / 信号契合 综合排序，输出**方案库**（方案号 + 推荐标签
  高胜率/高盈亏比/均衡）到面板并持久化，**绝不下单**。读菜单后决定方案号。
- **下单轮 `ROUND_MODE="ORDER"`**：设 `SELECTED_PLAN=方案号`；程序读持久化方案库、重新取价+
  S:PM 复核，仅当 `ALLOW_TRADING=True` 才真实开仓（否则空跑预览）。
- 关键范围参数：`SHORT_DELTA_RANGE`（短腿 |delta| 接受区间）、`PROTECTION_WIDTH_RANGE`（腿宽，
  以短腿行权为基准）、`SHORT_DTE_HOURS` / `PROT_DTE_DAYS`；排序权重 `PLAN_WEIGHTS`、信号置信 `SIGNAL_CONFIDENCE`。
- **日历净 credit 按真实市场修正**：保护腿可跨 `covered_cycles` 复用 + 退出按
  `PROTECTION_RESIDUAL_RECOVERY` 卖残值 → 菜单显示「有效净credit(每周期)」，与垂直可比。
- 运行后**不经任何界面命令调整计划或仓位**；止盈/止损退出模式后置。

```
src/
  config.py        # 配置 & 信号全局变量块（启动前手填）
  plans.py         # plan_*：枚举指标(胜率/盈亏比/复用残值)+KPF/信号评分+排序
  fmz_shim.py      # 本地 mock FMZ 全局（仅测试用，不进交付文件）
  deribit_io.py    # dbt_*：经 exchange.IO 调 Deribit 原生端点
  leg_selection.py # legsel_*：DTE→到期映射 + 行权价选腿
  spm_sim.py       # spm_*：simulate_portfolio 三场景保证金释放校验
  execution.py     # exec_*：maker-only / 只追一步 / 保护腿优先
  accounting.py    # acct_*：损耗记账 + §13 全量报告
  ledger.py        # 库存账本 + 状态机 + _G 持久化 + 启动对账
  hedge_risk.py    # 持仓后触界概率恶化评估 + 干跑期货对冲意图
  strategy.py      # main() 主循环编排
tests/             # 无 pytest 依赖；python tests/run_all.py
```

## 构建与测试

```bash
python tests/run_all.py          # 本地单测 + 端到端空跑冒烟
python build_bundle.py --check   # 合成单文件并做语法/名称解析 smoke
```

## 上线两轮工作流（核心安全门 ALLOW_TRADING）

1. **第一轮 `ALLOW_TRADING=False`（空跑核对）**：粘贴 `spm_calendar_protected_short_v1.py` 到 FMZ，
   填好 `config` 段（信号/方向/KPF/币种）。运行后只选腿、做 S:PM 模拟、把完整方案打到
   `LogStatus`/日志，**不下任何真实单**。逐项核对选腿、relief、执行计划、账目预估。
2. **第二轮 `ALLOW_TRADING=True`（极小量真实开仓）**：核对无误后放开交易，验证真实 maker-only
   成交、只追一步、`_G` 重启恢复、记账一致。`KILL_SWITCH`（或在 FMZ 交互栏发命令 `kill`）随时停新开仓。

## v1 边界

仅走状态机主路径 + 基础退出；保护腿跨周期复用、退出残值回收、同期保护腿回退、自动对账恢复、
完整 theta/残值时序记账等留 v1.1（见计划文件「明确延后到 v1.1」）。
