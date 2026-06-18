# VRP 权利金门控因子 · 阶段性封版说明 v1.1

封版日期：2026-06-02  
工作目录：`C:\Users\Xu\Documents\系统总纲\VRP`  
封版定位：把 VRP 因子冻结到一个**可被系统总纲引用、可被执行层照契约落地**的阶段性版本。**这是阶段性封版，不是实盘批准**：封的是「卖方建仓前定价门 + 安全标定 + 回放机具 + 整合收口契约」，**不是已证明的卖方 edge**。

## 1. 为什么是「阶段性」封版

VRP 的第一性是补卖方策略缺失的那一问——「现在值不值得卖波动率」。本轮把它从「设计稿」推进到「真实数据上跑通、压力遍历选参、真实价格路径上验证 hurdle 保护性、并写好执行层收口契约」的程度。但**真正的 edge（卖出 IV 是否系统性高于将实现 RV，扣完成本后为正）尚未、也无法用当前数据证明**——因为只有单一同日 IV 快照。因此封为 v1.1「阶段性」，解封进实盘的前提见 §6。

## 2. 封版组件清单（v1.1）

| 组件 | 文件 | 状态 |
| --- | --- | --- |
| 核心模型 | `src/vrp_model.py`（`VRP_FACTOR_VERSION=1.1.0`） | 封版。窗口门(vol-space 预筛)+候选门(权威 ccy full-burn)+BS hurdle 重定价 |
| 实现层策略包 | `src/vrp_policy.py` | 封版。`strict_cost_cold_guard_v1_1`（海量遍历选出） |
| Deribit 数据/RV 层 | `src/deribit_snapshot.py` | 封版。期权链+盘口 IV/Greeks+自算 RV(perp 1h closes) |
| 单点/网格模拟 | `src/vrp_simulation.py` | 封版。648 组参数网格 |
| 海量场景遍历 | `src/vrp_mass_simulation.py` | 封版。268,800 次压力遍历 |
| **多时点回放(本轮新增)** | `src/vrp_replay.py` + `tools/run_vrp_replay.py` | 封版。90 天真实路径 leak-guarded walk-forward |
| 测试 | `tests/`（**25 通过**，含新增 `test_vrp_replay.py` 5 项） | 封版。compileall 干净 |
| **整合收口契约(本轮新增)** | `docs/VRP执行层整合收口契约_v1.1.md` | 封版。重复原语→canonical 映射 + PLAN 插入点 |
| 产物清单 | `docs/VRP产物清单_v1.0.json/md` | 封版时刷新哈希（含新文件） |

本轮相对 v1.0 的代码改动（非破坏，保住既有 268k 证据）：
- 窗口门字段 `representative_structure_edge_ccy`（名为 ccy 实为 vol points）**诚实改名为 `representative_vol_edge`**，并在文档/代码里明确：**窗口门是廉价 vol-space 预筛，候选门才是权威 ccy full-burn**——不在窗口级重复 BS full-burn（避免冗余与 delta→strike 反演噪声）。门**判定行为不变**，故 v1.0 海量遍历结果与选中策略仍有效。
- 标注 4 个重复原语（`normalise_iv/_norm_cdf/_option_fee/_spread_half_cost`）为整合收口点（见契约 §1）。

## 3. 已验证：安全性 / 门控稳定性 / hurdle 保护性

### 3.1 海量压力遍历（v1.0 沿用）
真实快照锚定 + 1920 场景轴 × 20 结构 × 7 配置 = **268,800 次**。结论：宽松单点最优放过 732 个危险场景；严格 full-burn 成本门把危险通过降到 192（全为冷启动）；`strict_cost_cold_guard`（冷启动 ×1.35 + 3.0x spread reserve + 正净 edge 门）把危险通过/冷启动通过/薄 edge 通过全压到 **0**，保留 300 个更干净候选。

### 3.2 多时点回放（本轮新增，真实 90 天路径）
`vrp_replay.py` 在快照内嵌的 2161 个逐时 perp 收盘价上做 leak-guarded walk-forward（1920 步），用 ≤t 数据建 hurdle、用 >t 真实路径算 24/48/72h 已实现波动：

| 度量(72h 为例) | 结果 |
| --- | --- |
| hurdle 击穿率 vs 无调节 | **0.326 vs 0.507**（调节后欠补偿显著下降） |
| 低分位桶守护 | 击穿 **0.391 vs 无调节 0.789**（几乎砍半） |
| 冷启动段守护 | 击穿 **0.084 vs 无调节 0.387** |
| 扩张不对称(低分位 realized/anchor) | **1.26**，高分位仅 **0.68** → 静极思动是真实效应 |
| mean realized/hurdle | **0.88 < 1** → hurdle 平均是保护性的 |

24h/48h 结论同向（见 `outputs/vrp_replay_summary_*.json`）。**这在真实数据上证实了：(a) 低分位扩张风险真实存在；(b) 低分位+冷启动守护确实降低欠补偿——hurdle 标定是保护性的。**

## 4. 明确未验证 / 推迟（不许伪装）

- **IV-vs-RV 卖方 edge 未证。** §3.2 验的是 hurdle vs **已实现 RV** 的保护性，不是卖出 **IV** vs 已实现 RV 的净盈利。证后者需要**多时点期权 IV**，当前只有单一同日快照。
- **edge 验证 blocked on 数据**：必须**前向采集多时点 Deribit 快照**（cron 定时 fetch，复用 `tools/fetch_deribit_snapshot.py`），积累到覆盖多市场状态后，才能把 `vrp_replay` 升级为「IV 入场 → 真实到期结果」的成交级回放。
- 所有数值阈值仍 `PLACEHOLDER_CALIBRATION_REQUIRED`，不得据此解 `ALLOW_TRADING=False`。
- 单快照不代表全部市场状态；压力场景轴不等于真实未来路径；`robust_score` 非收益函数。

## 5. 复现命令

```powershell
$py='C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe'
$env:PYTHONIOENCODING='utf-8'
cd 'C:\Users\Xu\Documents\系统总纲\VRP'
& $py -m compileall -q src tools tests
& $py -m unittest discover -s tests -q                      # 25 passed
& $py tools\run_mass_vrp_simulation.py data\snapshots\deribit_btc_snapshot_20260601_144438.json
& $py tools\run_vrp_replay.py        data\snapshots\deribit_btc_snapshot_20260601_144438.json
& $py tools\write_artifact_manifest.py                       # 刷新产物清单哈希
```

## 6. 解封进执行层的前置条件

阶段性封版 ≠ 可进实盘。解封须依次满足：

1. **按 `VRP执行层整合收口契约_v1.1.md` 收口**：重复原语→执行层 canonical（`hedge_risk`/`accounting`/`plans`），删本地副本，单测覆盖等价性。
2. **只读嵌入 PLAN 轮**：只跑 EDB 背书侧；面板显示拒绝漏斗；`ALLOW_TRADING=False` 全链空跑不产订单。
3. **EntryRiskAnchor 血缘**：与对冲模块共用同一 IV/vol 基线。
4. **多时点回放**：前向采集多日快照后，跑成交级回放，第一次真正测 IV-vs-RV edge。
5. 仅当 4 给出扣成本正期望证据，才议 `vrp_residual_score` 进排序权重；进实盘另需总纲两道闸（A 管道/B 信号驱动）。

## 7. 一句话封版结论

> VRP v1.1 阶段性封版：**作为卖方建仓前定价门，它已在真实 Deribit 数据上跑通、经 268,800 次压力遍历选出保守策略、并在 90 天真实价格路径上证实了 hurdle 的保护性标定与冷启动/低分位守护的有效性；同时它诚实地未声称、也尚无数据证明卖方 IV-vs-RV edge。** 封的是安全门 + 回放机具 + 收口契约；edge 验证留待多时点 IV 前向采集。可作为系统总纲 v0.4 的执行层建仓前定价门引用。
