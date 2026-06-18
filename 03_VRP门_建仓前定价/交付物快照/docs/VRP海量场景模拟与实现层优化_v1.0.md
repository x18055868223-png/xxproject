# VRP海量场景模拟与实现层优化 v1.0

生成日期：2026-06-01  
工作目录：`C:\Users\Xu\Documents\系统总纲\VRP`  
交付定位：本文件补齐 VRP 因子在真实 Deribit 快照基础上的大规模场景遍历、参数调试结果、实现层优化结论和复现路径。它不是实盘批准文件，也不是最终交易参数。

## 1. 本轮补了什么

上一轮 VRP 交付已经完成了因子定义、window gate、candidate full-burn gate、Deribit 快照抓取和 648 组单点参数网格。但是那一版主要回答“当前快照下模型能否跑通”，没有充分回答“不同市场状态、成本状态、冷启动状态下，哪些参数会误放风险”。

本轮新增四件事：

1. 新增 `src/vrp_mass_simulation.py`，把真实 Deribit 快照展开成海量压力场景。
2. 新增 `tools/run_mass_vrp_simulation.py`，让全量遍历可复跑、可审计。
3. 新增 `src/vrp_policy.py`，把海量遍历选出的实现层策略配置固化为可引用策略包。
4. 新增测试覆盖 `test_vrp_mass_simulation.py` 与 `test_vrp_policy.py`，防止后续改动破坏场景遍历和策略选择证据。

## 2. 数据基准

本轮仍然使用已经保存的真实 Deribit 公共数据快照：

| 项目 | 值 |
| --- | --- |
| snapshot | `VRP/data/snapshots/deribit_btc_snapshot_20260601_144438.json` |
| generated_at | `2026-06-01T14:44:38.386000+00:00` |
| 标的 | BTC |
| Deribit index | 71343.2 |
| ticker_count | 150 |
| 抓取错误 | 0 |
| short expiries | `2026-06-03T08:00:00Z`, `2026-06-04T08:00:00Z` |
| term reference | `2026-06-05T08:00:00Z` |
| RV 来源 | `DERIBIT_BTC_PERPETUAL_1H_CLOSES` |
| chart_points | 2161 |

RV 上下文：

| 字段 | 值 |
| --- | ---: |
| `rv_24h` | 0.3199838467 |
| `rv_72h` | 0.2651325192 |
| `rv_7d` | 0.2938790408 |
| `rv_percentile_90d` | 0.4099204492 |
| `history_days` | 90 |

重要说明：本轮是“真实快照锚定 + 压力场景展开”，不是多日真实成交后验 PnL 回测。所有候选结构、盘口、IV、delta、expiry 都来自真实 Deribit 快照；RV 分位、前端 IV 倍数、期限结构、spread 和冷启动状态通过场景轴展开，用来检验门控稳定性和误放风险。

## 3. 海量场景设计

### 3.1 基础候选结构

从真实快照生成的基础结构：

| 项目 | 数量 |
| --- | ---: |
| 基础候选结构 `base_case_count` | 20 |
| SHORT_CALL 垂直结构 | 14 |
| SHORT_PUT 垂直结构 | 6 |
| expiry 范围 | 24-72h 短端窗口 |
| 短腿 delta | `0.15 <= abs(delta) <= 0.45` |
| 保护腿宽度 | 2000-2500 |

### 3.2 场景轴

每个基础结构被展开到 1920 个市场/成本场景：

| 轴 | 取值 | 目的 |
| --- | --- | --- |
| `rv_percentile` | 0.05, 0.20, 0.50, 0.80, 0.95 | 检查低 RV 扩张陷阱与风暴后回落 |
| `rv_scale` | 0.75, 1.00, 1.25, 1.50 | 检查 RV regime anchor 的敏感度 |
| `front_iv_multiplier` | 0.85, 1.00, 1.15, 1.35 | 检查前端 IV 便宜、正常、偏贵、极贵 |
| `term_ratio` | 0.85, 1.00, 1.15, 1.30 | 检查 contango、flat、倒挂压力 |
| `spread_factor` | 1.00, 2.00, 3.00 | 检查执行摩擦放大 |
| `history_days` | 10, 90 | 检查冷启动与正常样本 |

场景标签：

| 标签 | 条件 | 用途 |
| --- | --- | --- |
| `LOW_RV_EXPANSION_TRAP` | RV 分位低且前端 IV 未明显抬升 | 防止静极思动时误卖波动率 |
| `STORM_MEAN_REVERSION` | RV 高分位且 RV scale 高 | 检查风暴后回落时是否过度保守 |
| `TERM_STRESS_BACKWARDATION` | front/term 比率高 | 检查短端倒挂陷阱 |
| `WIDE_SPREAD` | spread factor 高 | 检查薄腿成本吞噬 |
| `COLD_START` | history_days 小于 30 | 检查 RV 分布样本不足 |
| `FRONT_IV_RICH` | 前端 IV 明显抬升 | 检查高权利金是否仍能通过 full-burn |

## 4. 参数配置遍历

本轮共比较 7 套策略配置：

| 配置 | 目的 |
| --- | --- |
| `loose_snapshot_best` | 上一轮单点快照最宽松基线 |
| `balanced_v1_1` | 成本与通过率折中 |
| `strict_cost_v1_1` | 加强 full-burn 与成本门 |
| `strict_cost_cold_guard_v1_1` | 在严格成本基础上加强冷启动保护 |
| `expansion_guard_v1_1` | 加强低 RV 分位扩张保护 |
| `term_guard_v1_1` | 加强期限结构倒挂保护 |
| `wide_spread_guard_v1_1` | 加强 spread 成本保护 |

全量规模：

```text
20 base cases * 1920 scenario axes * 7 configs = 268800 evaluations
```

输出文件：

| 文件 | 用途 |
| --- | --- |
| `VRP/outputs/mass_vrp_summary_20260601T144438.386000_0000.json` | 全量摘要、配置排名、选中策略 |
| `VRP/outputs/mass_vrp_config_ranking_20260601T144438.386000_0000.csv` | 参数配置排名表 |
| `VRP/outputs/mass_vrp_case_traversal_20260601T144438.386000_0000.csv` | 268800 行逐案遍历明细 |

## 5. 结果排名

| 排名 | 配置 | robust_score | 候选通过 | 危险通过 | 冷启动通过 | 平均通过 edge BTC |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | `strict_cost_cold_guard_v1_1` | 33.9771 | 300 | 0 | 0 | 0.0000948854 |
| 2 | `strict_cost_v1_1` | -1493.9023 | 492 | 192 | 192 | 0.0000874885 |
| 3 | `wide_spread_guard_v1_1` | -1501.6583 | 444 | 192 | 192 | 0.0000607083 |
| 4 | `term_guard_v1_1` | -1593.2641 | 536 | 192 | 168 | 0.0000596795 |
| 5 | `expansion_guard_v1_1` | -1966.2737 | 720 | 252 | 252 | 0.0000686313 |
| 6 | `balanced_v1_1` | -3343.1548 | 1020 | 408 | 372 | 0.0000692258 |
| 7 | `loose_snapshot_best` | -7744.4806 | 1512 | 732 | 568 | 0.0000595972 |

`robust_score` 不是收益预测，它只是本轮场景审计的排序函数：奖励通过后的净 edge，惩罚低 RV 扩张陷阱、期限倒挂、宽 spread、冷启动和薄 edge 的危险通过。

## 6. 关键发现

### 6.1 单点最优配置不能直接进入实现层

上一轮单点快照的 `loose_snapshot_best` 在当前快照下能找到薄弱通过机会，但在 268800 次压力遍历里放过了 732 个危险场景，其中包括 156 个低 RV 扩张陷阱、32 个宽 spread 场景和 568 个冷启动场景。

结论：单点快照最优只能作为调试基线，不能作为实现层策略配置。

### 6.2 严格成本门有效，但冷启动仍是主要漏洞

`strict_cost_v1_1` 把危险通过从 732 降到 192，低 RV 扩张、期限倒挂、宽 spread、薄 edge 误放都降为 0。但它的 192 个危险通过全部来自冷启动。

结论：full-burn 与 spread reserve 是必要门槛，但 RV 分位样本不足时，仅用默认 `cold_start_multiplier=1.10` 仍不够。

### 6.3 冷启动保护是本轮最有效优化

`strict_cost_cold_guard_v1_1` 在严格成本基础上把 `cold_start_multiplier` 提高到 1.35，结果：

| 指标 | 结果 |
| --- | ---: |
| 候选通过 | 300 |
| 危险通过 | 0 |
| 冷启动通过 | 0 |
| 薄 edge 通过 | 0 |
| 平均通过 edge | 0.0000948854 BTC |

结论：在当前设计口径下，冷启动不需要新增复杂预测模型；最短路径是提高 cold-start hurdle，并且保留 full-burn 正裕度。

## 7. 实现层优化

本轮把被海量遍历选出的策略写入：

```text
VRP/src/vrp_policy.py
```

当前选中策略：

```json
{
  "selected_policy_name": "strict_cost_cold_guard_v1_1",
  "low_percentile_multiplier": 1.25,
  "high_percentile_multiplier": 0.92,
  "cold_start_multiplier": 1.35,
  "min_history_days": 30,
  "term_backwardation_ratio": 1.18,
  "event_backwardation_ratio": 1.35,
  "min_window_vol_edge": 0.02,
  "min_candidate_edge_ccy": 0.00005,
  "spread_round_trip_multiplier": 3.0
}
```

实现层含义：

1. VRP 仍然只做过滤，不选择方向、不选择 expiry、不降低 EDB/GGR/宏观门槛。
2. window gate 先过滤 expiry/side 窗口，candidate gate 再逐个过滤结构。
3. 候选必须在 3.0x spread reserve 与 round-trip option fees 之后仍保留 `0.00005 BTC` 以上净 edge。
4. RV 历史样本低于 `30` 天时，hurdle 使用 `1.35` 的冷启动保守倍数。
5. 这是一套回放选出的实现层默认策略包，不是实盘开关。

## 8. 与上一版交付的关系

上一版文档里的单点最佳候选仍然有分析价值，但它不再是实现层推荐配置。它的定位改为：

```text
单点快照调试基线，用来证明 VRP 模型能识别 full-burn 后极薄机会。
```

本轮新增的实现层策略定位为：

```text
压力场景遍历后的保守门控默认值，用来减少静态快照参数对真实执行的误导。
```

## 9. 当前不声称什么

本轮没有声称：

- VRP 因子已经证明有正收益。
- 当前参数可以实盘。
- 单个 Deribit 快照代表全部市场状态。
- 压力场景等同于真实未来路径。
- `robust_score` 是收益函数。
- VRP 可以替代 EDB、KPF、GGR、对冲模块。

本轮真正完成的是：把真实 Deribit 快照中的候选结构扩展到大规模压力场景，用结果识别参数漏洞，并把最明显的冷启动漏洞转化成实现层策略配置。

## 10. 复现命令

运行海量场景遍历：

```powershell
& 'C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe' 'C:\Users\Xu\Documents\系统总纲\VRP\tools\run_mass_vrp_simulation.py' 'C:\Users\Xu\Documents\系统总纲\VRP\data\snapshots\deribit_btc_snapshot_20260601_144438.json'
```

运行测试：

```powershell
& 'C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe' -m unittest discover -s 'C:\Users\Xu\Documents\系统总纲\VRP\tests' -q
```

刷新产物清单：

```powershell
& 'C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe' 'C:\Users\Xu\Documents\系统总纲\VRP\tools\write_artifact_manifest.py'
```

## 11. 下一步建议

1. 多时点真实快照采样：至少覆盖 24-72 小时连续市场，验证冷启动保护与 full-burn 门是否稳定。
2. 接入 EDB 背书方向：只对信号层允许的方向运行 VRP，不再同时评估双侧。
3. 面板化拒绝原因：展示 window gate、candidate gate、full-burn friction、candidate edge、冷启动状态。
4. EntryRiskAnchor 血缘：把 `entry_executable_short_iv`、`entry_forward_vol_hurdle`、`entry_candidate_vrp_edge_ccy` 写入持仓锚点。
5. 若启用日历保护，单独补后端 vega 与 term vol 重定价口径，不复用同期垂直 full-burn。

## 12. 一句话结论

这次补充把 VRP 从“单点快照能跑通”推进到“真实 Deribit 快照锚定的 268800 次压力场景审计”。结果显示：宽松单点最优会误放大量危险场景，严格 full-burn 成本门能显著收敛风险，但冷启动仍是核心漏洞；因此当前实现层采用 `strict_cost_cold_guard_v1_1`，用更高冷启动 hurdle、3.0x spread reserve 和正净 edge 门槛来保留少量更干净的候选，而不把 VRP 伪装成交易批准。
