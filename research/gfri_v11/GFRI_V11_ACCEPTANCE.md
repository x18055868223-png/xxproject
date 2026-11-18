# GFRI v1.1 独立验收与预运行裁定报告

## 最终裁定：`READY_WITH_MINOR_LIMITATIONS`

> **权限与运行状态锁**：`collector_enabled = false`、`decision_authority = SHADOW_ONLY`、`player_integration_enabled = false`、`live_veto_enabled = false`。本轮已完成全部工程实现、独立审查修复、全量回归与默认关闭的运维包交付，未启动任何长期定时采集任务，未改动生产 Prompt A、AI 牌手正式输入及交割卷宗。等待外部独立审查通过后再行授权启用。

---

## 一、独立 Reviewer 审查与修复闭环

由独立 `reviewer` 子 agent（`a491f4bb`）对全部代码、公式、测试、部署脚本及实盘脱敏样本执行审查。初审裁定 `CHANGES REQUIRED`（发现 4 项 High 与 3 项 Medium 缺陷），主 agent 已全部修复并补充回归单测：

| 严重度 | 发现问题 | 修复与复验结果 |
| :--- | :--- | :--- |
| **High #1** | `policy.py` 使用全周期 `confirmation_status` 判定 `SINGLE_BLIP_FILTERED`，PUT 单次脉冲会静默压制 CALL 侧已确认的 `SHADOW_PAUSE` | 修复为读取分方向 `side_data["confirmation_status"]`；新增 `test_cross_side_blip_does_not_suppress_confirmed_opposite_pause` 验证通过 |
| **High #2** | `core.py` `ratio_blip` 条件仅判断 `spread_ratio > 2.0 and confirmed < fast_max`，会把 `[0.5x, 4.0x, 4.1x]`（2/3 持续高压）误判为单次脉冲 | 增加 `upper_outliers == 1` 与 `confirmed <= fast_min * 2.0` 约束；新增 `test_missing_spot_fails_closed_and_two_of_three_high_is_not_ratio_blip` 通过 |
| **High #3** | `collector.py` 未捕获 `gex-latest` / `get_instrument` / `get_order_book` 网络或 JSON 异常，故障时会崩溃退出现有定时任务而非记录 `UNKNOWN` | 新增 `_safe_fetch`，断网/超时写入 `UNKNOWN` 审计记录并 `exit 0`；单次盘口超时仍支持剩余 `2/3` 确认；已单测验证 |
| **High #4** | `enable_collector.sh` 以 root 创建 `/opt/gfri-shadow/state` 未移交 `bitnami:bitnami` 所有权，会导致 `ProtectSystem=strict` 服务报 `PermissionError` | 补充 `chown -R bitnami:bitnami` 与 `install -o bitnami -g bitnami`，并在 `gfri-shadow-collector.service` 显式声明 `PYTHONPATH=/opt/gfri-shadow/current` |
| **Medium #5** | `gfri_v11.env.example` 中 6 项采集/窗口/OKX 环境变量未被 `collector.py` 读取 | 新增 `resolve_runtime_from_env()` 与 `_save_raw_cycle_bundle()` 完整接入并经单元测试验证 |
| **Medium #6** | GEX `asset_price` 缺失或非正时未 fail-closed，导致跳过 `0.2% / 0.5%` 价基对齐检查仍返回 `VALID` | `extract_gex_snapshot` 缺 `spot_usd` 时强制 fail-closed 为 `UNKNOWN` |
| **Medium #7** | `enable_collector.sh` 在检查 `$ENV_FILE` 存在性之前执行 `preflight_check.py` | 调换顺序使缺失 env 提示生效，并在 `preflight_check.py` 接入 `--require-shadow-only` |

**修复后验证（最终提交前实测）**：
- `pytest .worktrees/gfri-v11/research/gfri_v11/tests -v`：**`35 passed in 0.95s`**（0 failed, 0 skipped）。
- `deploy/gfri_v11/preflight_check.py --require-shadow-only --require-disabled`：**`PREFLIGHT_OK`**。
- `python -m research.gfri_v11.collector --scheduled`：**`COLLECTOR_DISABLED (GFRI_COLLECTOR_ENABLED=0; SHADOW_ONLY)`**，exit 0。
- Git Bash `bash -n` 校验 `enable_collector.sh`、`disable_collector.sh`、`rollback_gfri_v11.sh`：**`EXIT=0`**。

---

## 二、五大验收核心问题回答

### Q1. 现在是否已经有一个无需历史分位即可运行、数值含义明确的 GFRI？
**是。** v1.1 主指标 `gfri_down_1pct`（Put 风险）与 `gfri_up_1pct`（Call 风险）在第一天即可独立算出精确无量纲比值 $\max(0, H_d \cdot \operatorname{sign}(\Delta S)) / L_d$（`price_only_curve ±1%` 名义对冲流 ÷ Deribit `BTC-PERPETUAL depth=1000` 冲击区可见深度），已修复 v1.0 的浮点边界截断、现货偏离漏检和 `ERROR` 状态穿透，实盘截面验证算得 PUT `+4.75x` / CALL `+7.00x`。

### Q2. GFRI 是否真正增加了 Gamma 反馈压力与盘口承载力关系的信息，而没有重复 AI 牌手推理？
**是，但仅限于“流动性承载比”与“方向不对称性”增量，不代表独立做市商真实库存。** 在 `2026-11-18T14:46Z` 实盘急跌（spot 降至 `$63,138.64`）期间，Deribit ±1% 买盘/卖盘深度收缩至 `$26.90M` / `$17.90M`（同期 OKX `BTC-USDT-SWAP` 仍有 `$104.91M` / `$86.92M`），直接将压力倍率从早先 `~3.0x` 推升至 `+4.75x / +7.00x`；这提供了传统 `total_net_gex` 与 AI 牌手 Pin/责任路径推理之外的盘口脆弱性信息，同时严格避免输出任何 Pin、墙位或承保长文解释。

### Q3. 极值过滤、时间门禁、冷启动和结构漂移是否已具有健壮设计？
**是。** (1) **极值过滤**：3 样本确认通道（中位数）仅剔除孤立单次盘口闪烁，双样本以上持续恶化原样通过，Fast 通道永久保留未裁剪极值 `gfri_fast_max`；(2) **时间门禁**：强制所有源时间戳与盘口时间 `<= freeze_at_ms + 5s`，严格限制 15m 跨源偏差与 60m 上游有效期，并将 Deribit 公开 OI 的 `8h` 已知延迟写入元信息；(3) **冷启动与漂移**：4h 近期窗口不足 4 点保持 `WARMING_UP`，60 天窗口采用 14 天半衰期 + 按 UTC 日样本数归一化（防止单日高频刷屏），不足 30 天或 `n_eff < 30` 时不输出伪精确 `Pxx`，并在高 GFRI 常态期触发 `regime_drift_warning` 防止历史分位下降掩盖绝对高压。

### Q4. Put/Call 影子暂缓能否完全由确定性规则产生？哪些阈值尚未经验证？
**可以完全由纯 Python 确定性规则产生**（支持 `NO_VETO / SHADOW_PAUSE_PUT / SHADOW_PAUSE_CALL / SHADOW_PAUSE_BOTH / WARMING_UP / UNKNOWN` 及 2 周期确认 + 滞回释放，`UNKNOWN` 绝不误报安全）。当前全部经验参数均带 `UNVALIDATED_CANDIDATE_PARAMS` 标记、未经经济校准：绝对阈值（`2.50x`、`2.00x`、`3.50x`）、4h 急剧恶化比（`1.50x`）、2 周期连续确认/释放、4h 近期与 60d/14d 长期窗口及 `10%` 驱动分解阈值，均需在积累 `30–50` 局带官方交割 `outcome.json` 的前瞻样本后再作检验。

### Q5. 现在是否已经达到“可以开始长期影子数据积累”的工程标准？
**达到带有明确前提边界的试积累标准（`READY_WITH_MINOR_LIMITATIONS`）。** 代码、隔离、追写账本、断网降级、脱敏审计及运维开关均已就绪；但仍存在三项需外部审查知悉的现实限制：
1. 上游 `gex-latest` 当前仍以全期限 `oi_proxy_call_long_put_short` 计算（`0dte` 仅占 `4.23%`），受 Deribit `8h` 公开 OI 延迟影响，且目前因 `bybit/okx/binance` 次级源不可用处于 `DEGRADED`；
2. 单所 Deribit 永续盘口深度仅为 OKX 同刻深度的 `~17%–26%`（`3.92x–5.96x` 反比），绝对倍率阈值受交易所深度分布约束；
3. `systemd` service/timer 已通过本地静态语法和预检校验，尚未在目标 Linux 主机做真实 `systemctl` 运行演练。
