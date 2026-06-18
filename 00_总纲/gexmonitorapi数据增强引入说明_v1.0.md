# gexmonitorapi 数据增强接口引入说明 v1.0

> 落地日期：2026-06-04　|　范围决策：**A+B**（可靠性加固 + 面板/可观测/VRP交叉校验，**本轮不新增 EDB 投票因子**）
> 关键前提：系统**此前已在抓取 gexmonitor**（`demo/config.py:gex_base_url="https://gexmonitor.com/api/gex-latest"`，`gex_adapter.py` 仅稳健提取 flip_point/spring；GGR 从 raw_payload **尽力而为**读 net_gex/walls）。`gexmonitorapi`（FastAPI `/v1/info`，Bearer，服务端~10min缓存）是这条既有 feed 的**干净、带语义、可降级的升级版**。

## 1. 全局不变量（已在代码与测试中固化）
1. **软/可降级、永不硬门**：缺失/`stale`/`partial` 一律优雅降级回退既有逻辑；不新增硬阻断、不解 `ALLOW_TRADING`。
2. **GGR 只下调不上调**：`total_net_gex`/`market_state` 仅用于把区制下调到 TRANSITION/强化 veto。
3. **不双计**：`skew_25d`/`pcr`/`flow` 本轮**不投票、不进 EDB**（仅面板+日志）。
4. **VRP 独立双门不破**：`iv_rv_ratio`/`term_structure` 仅交叉校验+冷启动先验+展示，`applied_to_gate=False`，**绝不构成第二道权利金门**。
5. **增量不替换 feed**：保留现有 60s flip_point 抓取与稳定化守护；新 API 只供慢变结构字段。
6. **不复活 KPF**：执行层 walls/magnet 仅**影子避让诊断**（出建议、不进排序，选腿仍纯 delta）。

## 2. 各模块落地（canonical 源仓库）

| 模块 | 文件 | 改动 | gex_info=None 时 |
|---|---|---|---|
| 信号·接入 | `中性回路 - opus4.8/demo/gex_info_adapter.py`（新增） | `GexInfoAdapter`+`GexInfoState`：拉 `/v1/info`、LKGV 缓存、quality 枚举、`parse_info_payload` | 返回 MISSING 快照 |
| 信号·Tier A | `demo/gamma_regime.py` | `evaluate_gamma_regime(...,gex_info=None)`：`_net_gex_sign`/`_max_gamma_strike` 优先读干净字段；`market_state` 交叉校验（只下调）。Deribit 逐档仍是 pin 首选 | **逐字节等价**（runtime smoke 全绿） |
| 信号·Tier B | `demo/recorder.py`+`demo/main.py` | `_gex_info_table` 面板（regime/levels/premium/flow/meta），`factor_snapshot["gex_info"]` 落 snapshots.jsonl | 面板显示“未接入/缺失” |
| 信号·接线 | `demo/main.py` | `_refresh_gex_info(live)`；离线 fixture 注入“同向” gex_info | 决策不变（Observe） |
| ②-门 VRP | `系统总纲/VRP/src/vrp_model.py` | 新增独立 `gex_info_cross_check(window, gex_info, config)`：iv_rv/term 一致性 + 冷启动先验，**不触碰 sealed gates** | `gex_info_available=False` |
| ③ 对冲 | `Deribit.../src/hedge_risk.py` | `_ggr_adverse(...,gex_info,current_price)`+`_gex_info_negative_gamma_zone`：负gamma且价破 `volatility_trigger` 才强化 GGR_ADVERSE（同一确认、不新增计数） | **逐字节等价** |
| ② 执行 | `Deribit.../src/leg_selection.py` | 新增独立 `legsel_wall_proximity(...)`：短腿是否近 magnet/墙/vol_trigger → 出建议（**不进排序**） | `gex_info_available=False` |

## 3. 配置与启用
- 新增 `demo/config.py`：`gex_info_enabled`(True)、`gex_info_base_url`(`http://127.0.0.1:8000/v1/info`)、`gex_info_token`(**空**)、`gex_info_refresh_sec`(600)、`gex_info_cache_file`、`gex_info_cache_max_age_ms`。
- **激活**：设 `NRD_GEX_INFO_TOKEN`（或本地改 `gex_info_token`）。token 为空时整层自动降级，**不影响任何现有行为**。token **不入库**（安全）。

## 4. 验证状态（全绿）
- 信号层：`compileall` / `runtime_check_demo.ps1`（离线 smoke）/ `static_validate_demo.ps1`（FMZ 同步+交付摘要+无未完成标记）/ 独立 `tools/gex_info_check.py`（解析+GGR 降级+降级路径）。
- VRP：`python -m unittest`（**31 通过** = 25 sealed + 6 新；含“cross-check 不改 sealed window gate”）。
- 执行/对冲：`build_bundle.py --check` + `tests/run_all.py`（**67 通过**；含 2 hedge + 4 leg_selection 新测，含“不可在风险未抬升时凭空造险”不变量）。
- FMZ/bundle 单文件已回刷；本工程 `交付物快照/` 四件已同步源仓库。

## 5. 字段冗余/增效结论（与用户协商定）
- **可靠性升级**（已在用但脆弱）：`total_net_gex`/`market_state`/`magnet`/walls → 硬化 GGR。
- **真冗余**（自算更细/更快）：`flip_point`/`spot`/`atm_iv`/原始 `skew_25d` → 仅交叉校验/面板。
- **真全新**：`dvol`/`flow.*`/`volatility_trigger` → 面板+（对冲用 vol_trigger）；`dvol`/`flow` 列为**校准后**候选 EDB 票。

## 6. 后续（非本轮，校准后再议）
- 用攒下的 `snapshots.jsonl` 对 `dvol`/`flow.*` 测前向 label IC → 决定是否提级 EDB 票（届时显式去重 SRD/FUNDING/CVD/pcr）。
- SRD 跨期窗口 skew 对比（优先 Deribit 高频，API 期限结构作广度/回退）。
- 执行层影子避让若证有用 → 提级 soft tie-break；VRP `gex_info_cross_check` 随 VRP 嵌入执行层时接线。
- 待 Phase 2 桥接把 gex_info 经 `SignalEvidencePackage` 透传给执行/对冲（当前对冲/执行的 gex_info 入参已就位，等数据轨）。
