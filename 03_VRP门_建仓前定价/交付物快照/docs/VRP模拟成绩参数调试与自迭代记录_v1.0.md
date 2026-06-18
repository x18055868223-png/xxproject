# VRP模拟成绩、参数调试与自迭代记录 v1.0

生成日期：2026-06-01  
输入设计：`C:\Users\Xu\Documents\系统总纲\VRP权利金门控因子计划与说明_v1.0.md`  
开发路径：`C:\Users\Xu\Documents\系统总纲\VRP`  
交付定位：记录本轮基于真实 Deribit 公共数据的 VRP 场景模拟、参数调试、自迭代路径和阶段性最优方案。本文不是实盘参数批准文件。

## 1. 本轮完成了什么

本轮把 VRP 因子从文档设计推进到一个可回放、可测试、可扩展的本地开发路径：

1. 建立 `VRP` 文件夹，拆分模型层、Deribit 数据层、模拟层、工具脚本、测试、数据和输出目录。
2. 用测试先行方式实现 `window_vrp_gate`、`candidate_full_burn_gate`、forward hurdle、期限结构路由、结构级 full-burn 评估。
3. 抓取 Deribit BTC 真实公共数据快照，包括短端期权链、期限参考端、盘口 IV/Greeks 和自算 RV 上下文。
4. 对 SHORT_CALL / SHORT_PUT、多个短端 expiry、候选垂直结构和参数网格做场景模拟。
5. 输出参数网格 CSV、完整 JSON、阶段性最优参数集和自迭代记录。

## 2. 当前文件与证据

| 类型 | 路径 |
| --- | --- |
| 核心模型 | `VRP/src/vrp_model.py` |
| Deribit 数据层 | `VRP/src/deribit_snapshot.py` |
| 模拟层 | `VRP/src/vrp_simulation.py` |
| 抓取脚本 | `VRP/tools/fetch_deribit_snapshot.py` |
| 模拟脚本 | `VRP/tools/run_vrp_simulation.py` |
| 行为测试 | `VRP/tests/` |
| 真实快照 | `VRP/data/snapshots/deribit_btc_snapshot_20260601_144438.json` |
| 完整模拟结果 | `VRP/outputs/vrp_simulation_result_20260601T144438.386000_0000.json` |
| 参数网格 CSV | `VRP/outputs/vrp_parameter_grid_20260601T144438.386000_0000.csv` |

## 3. 真实 Deribit 数据快照

本轮正式模拟使用的快照：

| 项 | 值 |
| --- | --- |
| 快照时间 | `2026-06-01T14:44:38.386000+00:00` |
| 标的 | BTC |
| Deribit index | 71343.2 |
| 抓取 ticker 数 | 150 |
| 抓取错误 | 0 |
| 短端窗口 | `2026-06-03T08:00:00Z`, `2026-06-04T08:00:00Z` |
| 期限参考端 | `2026-06-05T08:00:00Z` |

数据层自迭代中发现：当时链上没有落在 5-10d 区间的 term expiry，因此按设计降级为“最近的短端外 expiry”，即 2026-06-05 08:00 UTC。该降级没有伪装成正常 5-10d，后续文档和输出都保留这个事实。

RV 上下文使用 Deribit `BTC-PERPETUAL` 1h close 自算：

| 指标 | 值 |
| --- | ---: |
| `rv_24h` | 0.3199838467 |
| `rv_72h` | 0.2651325192 |
| `rv_7d` | 0.2938790408 |
| `rv_percentile_90d` | 0.4099204492 |
| `history_days` | 90 |
| `chart_points` | 2161 |
| 来源 | `DERIBIT_BTC_PERPETUAL_1H_CLOSES` |

Deribit `get_historical_volatility` 同步保留为交叉校验，但本轮 hurdle 主口径使用自算 RV。

## 4. 场景模拟覆盖

本轮全量场景覆盖：

| 维度 | 覆盖 |
| --- | --- |
| 方向侧 | `SHORT_CALL`, `SHORT_PUT` |
| 到期窗口 | Deribit 当前 24-72h 内全部短端 expiry |
| 期限参考 | 优先 5-10d，缺失时最近短端外 expiry |
| 候选结构 | 同期垂直，短腿 `0.15 <= abs(delta) <= 0.45`，保护腿宽 2000-2500 |
| 盘口要求 | 短腿有 bid、保护腿有 ask、可计算 IV |
| 门控 | window gate + candidate full-burn gate |
| 参数组数 | 648 |

参数网格调试项：

| 参数 | 候选值 |
| --- | --- |
| `low_percentile_multiplier` | 1.15, 1.25, 1.35 |
| `high_percentile_multiplier` | 0.88, 0.92, 1.00 |
| `term_backwardation_ratio` | 1.12, 1.18, 1.25 |
| `min_candidate_edge_ccy` | 0.0, 0.00002, 0.00005 |
| `cold_start_multiplier` | 1.10, 1.20 |
| `min_window_vol_edge` | 0.00, 0.02 |
| `spread_round_trip_multiplier` | 2.0, 3.0 |

## 5. 阶段性最优方案

当前快照下的阶段性最优参数集：

```json
{
  "low_percentile_multiplier": 1.15,
  "high_percentile_multiplier": 0.88,
  "term_backwardation_ratio": 1.12,
  "min_candidate_edge_ccy": 0.0,
  "cold_start_multiplier": 1.1,
  "min_window_vol_edge": 0.0,
  "spread_round_trip_multiplier": 2.0
}
```

阶段性最优的含义：

- 它是当前真实快照上的“最小可行门控组合”。
- 它不是生产参数。
- 它没有证明 VRP 策略有正期望。
- 它证明当前市场下 full-burn gate 极其严格，绝大多数候选被成本和 hurdle 吞掉。

综合结果：

| 指标 | 值 |
| --- | ---: |
| 参数组数 | 648 |
| best_score | -6.3186887259 |
| 通过候选数 | 1 |
| distorted 窗口数 | 0 |

按方向拆分：

| 方向 | 窗口结果 | 候选结果 | 解释 |
| --- | --- | --- | --- |
| SHORT_CALL | 2 pass / 0 block / 0 distorted | 14 total / 1 pass / 13 block | 仅 1 条 call spread 扣 full-burn 后仍微弱为正 |
| SHORT_PUT | 2 pass / 0 block / 0 distorted | 6 total / 0 pass / 6 block | 当前 put 侧权利金不足以覆盖 full-burn |

唯一通过候选：

| 项 | 值 |
| --- | --- |
| side | SHORT_CALL |
| short leg | `BTC-3JUN26-72500-C` |
| protection leg | `BTC-3JUN26-75000-C` |
| DTE | 41.2560 小时 |
| short delta | 0.2771 |
| width | 2500 |
| executable_short_iv | 0.3688 |
| forward_vol_hurdle | 0.2955649209 |
| executable net credit | 0.00037 BTC |
| hurdle net credit | 0.0002441869 BTC |
| full_round_trip_friction | 0.0001225 BTC |
| candidate_vrp_edge_ccy | 0.0000033131 BTC |

这条候选的正 edge 极薄，属于“可解释通过”，不是“强交易信号”。当前阶段建议记录为 `PASS_BUT_THIN_EDGE`，后续回放应验证它是否只是某次盘口微结构造成的偶然通过。

## 6. 自迭代路径

### Iteration 0：文档设计落地前

输入为 VRP 因子计划与说明 v1.0。核心要求：

- window gate 在候选前。
- candidate gate 使用结构级两腿 full-burn。
- forward hurdle 不混入倒挂路由。
- VRP 不抢 EDB 门、不抢期号、不做最大软权重。

### Iteration 1：测试先行锁定门控语义

先写行为测试，再实现模型：

- `normalise_iv` 支持 Deribit percent 与 decimal。
- `forward_vol_hurdle` 只由 `rv_regime_anchor * percentile_adjustment * cold_start_multiplier` 合成。
- term backwardation 走 `DISTORTED_REVIEW`，不是 hurdle 成分。
- candidate gate 使用同期垂直两腿重定价和 full-burn。
- VRP 只过滤窗口，不在多个 PASS expiry 之间选期。

### Iteration 2：Deribit 数据层第一次真实抓取

第一次真实抓取结果：

- 成功获取短端期权链。
- 发现当时没有 5-10d term expiry。
- 初始快照只包含 98 个 ticker，term reference 为空。

修正：

- `select_expiry_bands` 增加 fallback：若无 5-10d，则使用最近的短端外 expiry。
- 重新抓取后得到 150 个 ticker，term reference 为 2026-06-05。

### Iteration 3：RV 口径修正

初始数据层可读取 Deribit `get_historical_volatility`，但这更像交易所给出的 HV 序列，不是本系统设计里强调的自算 RV。

修正：

- 使用 Deribit `BTC-PERPETUAL` 1h close 自算 `rv_24h / rv_72h / rv_7d / rv_percentile_90d`。
- 保留 `get_historical_volatility` 作为交叉校验。
- snapshot 中记录 RV 来源，避免黑箱外包。

### Iteration 4：参数网格从 162 扩展到 648

第一版网格覆盖 percentile、term ratio、candidate edge 和 cold start。模拟后发现多数组合等价，因为当前 RV percentile 中性且没有倒挂。

修正：

- 加入 `min_window_vol_edge`。
- 加入 `spread_round_trip_multiplier`。
- 输出 `best_by_side`，避免综合分掩盖某一侧完全不可做。

### Iteration 5：阶段性结论

当前真实快照下：

- window gate 不是瓶颈。
- candidate full-burn gate 是主要瓶颈。
- SHORT_CALL 有一条微弱通过候选。
- SHORT_PUT 全部阻断。
- 阶段性最优参数非常宽松，说明当前市场并没有给出厚实的卖方 edge。

因此，当前阶段的正确结论不是“可以交易”，而是：

```text
VRP 框架已经能用真实 Deribit 数据识别出：
窗口层可卖不等于候选结构可做；
full-burn 后的净 edge 才是第一硬门；
当前快照只有极薄的 SHORT_CALL 机会，不能解锁实盘。
```

## 7. 后续迭代建议

下一轮应优先做三件事：

1. 多时点快照回放：连续采样 24-72 小时，验证唯一通过候选是否稳定存在。
2. 引入真实信号侧：只对 EDB 背书的 SHORT_CALL 或 SHORT_PUT 跑 VRP，不再同时评价双侧。
3. 日历保护专版：若未来启用 `ENABLE_CALENDAR=True`，必须加入后端 vega 与 term vol 重定价。

仍不建议做的事：

- 不要把当前最优参数写进生产配置。
- 不要因为只有 1 条候选通过就降低 full-burn 门槛。
- 不要把 SHORT_CALL 的微弱通过推广到 SHORT_PUT。
- 不要用 5-10d 缺失时的 fallback term 当作长期稳定口径。

## 8. 复现命令

测试：

```powershell
& 'C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe' -m unittest discover -s 'C:\Users\Xu\Documents\系统总纲\VRP\tests' -q
```

抓取真实快照：

```powershell
& 'C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe' 'C:\Users\Xu\Documents\系统总纲\VRP\tools\fetch_deribit_snapshot.py' --currency BTC --max-tickers 260
```

运行模拟：

```powershell
& 'C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe' 'C:\Users\Xu\Documents\系统总纲\VRP\tools\run_vrp_simulation.py' 'C:\Users\Xu\Documents\系统总纲\VRP\data\snapshots\deribit_btc_snapshot_20260601_144438.json'
```

## 9. 一句话结论

本轮 VRP 自迭代的阶段性最优不是一组可实盘启用的阈值，而是一条更可靠的执行脊柱：用真实 Deribit 数据先过滤期限窗口，再用结构级 full-burn 过滤候选；在当前快照下，这条脊柱只允许一条极薄 SHORT_CALL 候选通过，并明确阻断 SHORT_PUT 侧，说明 VRP 门控已经开始发挥“少做错事”的价值。
