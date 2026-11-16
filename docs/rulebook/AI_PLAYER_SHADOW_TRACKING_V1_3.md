# AI 影子牌手、锁定追踪与交割卷宗 v1.3

工作台「锁定追踪」显示锁定人为「用户」或「AI 影子 · 实际模型号」。两者打开同一套采样、结构对照、价格轨迹、理论浮盈亏与交割明细。AI 行可回到同一手牌的「推理审计与卷宗」。审计左侧读双侧公开理由、最强反证和冻结条件，右侧读当时原生合约和冻结证据。完整输入、实际回复和技术来源按需展开。

## 真实启动观察

2026-11-16 的启动观察使用 Deribit BTC 2026-11-17 08:00 UTC 到期。它的采样身份为 `STARTUP_AD_HOC`。正式 14:00 UTC 计划另计。

| 事实 | 已核验记录 |
| --- | --- |
| 市场读取 | Put 03:00:13.550 UTC，Call 03:00:18.323 UTC |
| 原始牌面冻结 | 03:04:36.682 UTC |
| 实际请求及决定 | 03:05:14.027 → 03:07:24.396 UTC，gpt-6.1-sol，max，MANUAL_IMPORT |
| 选侧与责任 | Accept / Call，卖 BTC-17NOV26-66000-C，买 BTC-17NOV26-68000-C，1 BTC |
| 追踪盘口、实际锁定 | 03:08:07.955 → 03:08:11.001 UTC，指数从牌面 65,113.817167 变为追踪基线 65,125.07 USD |
| 锁定时经济量 | 入场费后估计净信用 0.0034625 BTC，按当时两腿盘口估值 |
| 卷宗、手牌、责任、追踪 | `Deribit_BTC_20261117T080000Z` → `hand_4c927870eac4470969e53a90` → `episode_2a9d002761254bbc6f950229` → `lce_601740dcd1a5443b245cd9f1` |
| 续判 | 同一追踪已归档 5 次原生采样。03:09 至 03:29 UTC 的 5 个固定检查均记录为 `CONDITION_NOT_TRIGGERED` |
| 到期结果 | 等待 2026-11-17 08:00 UTC 交割后的官方数据 |

Call 优于 Put 的公开理由是两侧都可评估时，Put 已实测进区且近 4 小时同向价流仍在消耗余量。Call 的 65,500 位于现价上方，至短腿余量相对更大。Call 的最强反证是 65,500 反复穿越，短腿曾被逼近至 14.3 USD。原回复明示本期 Pin 历史无法对齐，择侧依据来自责任路径和价流。后续每五分钟按该局固定时钟检查新进入与返回，近一检查点的结论只描述该条规则。

本地交付物位于项目根目录 `.artifacts/player-dossier-v13/`。真实案卷入口为 `forward/evidence/dossiers/Deribit_BTC_20261117T080000Z/summary.md`。从该目录回读原牌面、原答、两端身份、冻结条件、追踪 t0 和观察事件。截图目录的 `real-*` 使用这局真实卷宗，`before-*` 与 `after-*` 使用本地 fixture 对照。

## 运行入口

计划沿用 [daily-deribit-btc-r2@1](examples/player_dossier/schedule.json)。2026-11-16 14:00 UTC 生效，每日 14:00 UTC 正式牌，对应次日 08:00 UTC，到期日一局。发牌和正式回复各沿用 20 分钟宽限。13:50 至 13:59 UTC 预热只检查归档、原生到期、盘口和追踪容量。

配置完整代码树上的工作台，并将同一卷宗根目录挂给卷宗 writer。工作台只读卷宗，写原有追踪库。卷宗命令持唯一 writer 锁，写发牌、回复、回执、条件和附件。浏览器读卷宗走工作台原会话。锁定服务走本机监听，使用与工作台登录令牌不同的 `ASTRA_AI_SHADOW_LOCK_TOKEN`。

| 配置 | 用途 |
| --- | --- |
| `ASTRA_AI_SHADOW_DOSSIER_ROOT` | 工作台只读的卷宗根目录 |
| `ASTRA_AI_SHADOW_LOCK_TOKEN` | 工作台服务及本机卷宗命令的专用凭证 |
| `ASTRA_AI_SHADOW_EXPERIMENT=1` | 工作台允许影子锁定，卷宗 CLI 要求真实模型与时钟，两处旧自动牌手停歇 |

以下命令中的大写占位内容使用运行环境的实际路径与时间。

1. 13:50 UTC 预热。命令输出 readiness，保留正式 hand 的采样资格与时钟。

   ```text
   python -m tools.astra_player_tracking_v13 --url http://127.0.0.1:8787 prewarm --plan docs/rulebook/examples/player_dossier/schedule.json --archive ARCHIVE_ROOT
   ```

2. 14:00 UTC 发牌，宽限内允许同一计划身份按既有 archive 恢复。输入文件写入成功后，原样交给本局实际模型，记录请求与回复完成的 UTC 毫秒。

   ```text
   python -m tools.astra_player_dossier_v1 --root DOSSIER_ROOT tick --plan docs/rulebook/examples/player_dossier/schedule.json --archive ARCHIVE_ROOT --settle-due
   ```

3. 导入原回复。摘录 JSON 包含回复中的 `put`、`call`、`put_counter`、`call_counter`、`comparison` 五段原文和 `depends_on_ard`。`MODEL_CONFIG_JSON` 记录可核验的推理档位和调用配置。人工交接使用 `MANUAL_IMPORT`。

   ```text
   python -m tools.astra_player_dossier_v1 --root DOSSIER_ROOT response --expiry-ms EXPIRY_MS --hand HAND_ID --attempt ATTEMPT_ID --raw RESPONSE_FILE --annotation EXCERPTS_JSON --configuration MODEL_CONFIG_JSON --model ACTUAL_MODEL --requested-ms REQUEST_MS --decided-ms DECIDED_MS
   ```

4. 仅首个有效 Accept 可申请锁定。在回复期限内、并在实际完成决策后的 120 秒内发出一次申请。必须匹配工作台当前原生两腿、方向、到期、币种、数量与行权价，盘口沿用两腿各最多 5 秒、两腿时钟差最多 2 秒。现有人工活跃追踪保留原身份，冲突记录为受阻。此政策只约束影子追踪落地。

   ```text
   python -m tools.astra_player_tracking_v13 --url http://127.0.0.1:8787 link --root DOSSIER_ROOT --dossier DOSSIER_ID --hand HAND_ID --condition CONDITION_JSON
   ```

   `CONDITION_JSON` 保存原答里逐字可找到的 `historical_facts`、`accepted_conditions`、`confirmation_excerpt`，并映射现有 `new_entry` 或 `entry_not_returned` 与 `check_interval_ms`。首检采用实际锁定后下一个 UTC 整分钟。原答把 t0 在责任外列为前提时，填 `require_outside_at_t0: true`。原答依赖 ARD 时还须附已冻结的责任意义与复核摘录。人工归一化保留牌手自己的原条件。

   读到 `LINK_UNVERIFIED` 时以同一手牌重跑 `link`。已有申请的重跑只查询原幂等键并补回执。已确认的 `LOCK_BLOCKED` 保留在本局研究记录内。错过计划、迟到回复、解析失败及资料阻塞均留档。

5. 在观察出现关键变化、Stop 或到期时，用 `sync`（与 `link` 同样的三项身份参数）归档一个 episode，或用以下命令遍历尚待封存的已链接追踪。续判用现有 `check` 导入截至固定检查钟的 Deribit `btc_usd` 连续分钟采样。采样记录、条件结果和 AI 的公开 Hold/Revoke 是三个时钟与三种证据。

   ```text
   python -m tools.astra_player_tracking_v13 --url http://127.0.0.1:8787 sync-open --root DOSSIER_ROOT
   ```

   本地和人工交接可直接运行以上入口。无人值守启用时，预热与发牌用以 UTC 固定的现有调度器，后续归档命令按追踪节奏执行。操作员先核对唯一 writer，确保旧自动复核结束，再将两个旧自动入口置于同一实验开关。停用时停止新发牌及锁定服务，继续读出已存在追踪并完成到期归档。原计划身份和锁定幂等键用于恢复。

## 追踪与归档证据

- 原生追踪 t0 在回执前保存到卷宗。后续走现有锁定分钟采样与 WPEM。追踪的最近观察值和缺口写入卷宗事件。Stop 只结束观察。
- 终态从追踪导出全部可用指标行、缺口和原生交割信息。图上的显示抽样只服务图表。60 天清理前校验 t0、末点、终态和轨迹哈希。清理后，从同一手牌读归档轨迹。
- 锁定净信用是 `LOCK_TIME_ESTIMATE`。持有中的曲线使用 `MARK_TO_MARKET_REFERENCE`。官方结算的币本位赔付归 `OFFICIAL_EXPIRY_LIABILITY`，追踪以 t0 净信用得到的理论结果归 `HOLD_TO_EXPIRY_SHADOW`。美元换算使用官方结算价。各读数沿用已有原生 Deribit 核算，实际成交栏为空。交割不可用时保留待验证结果。
- 本轮自动权限限于影子观察。AI 决策、固定条件复核、用户 Stop 和实盘执行分别有独立身份。当前本地模型交接和固定条件检查为人工触发。

## 改动与运行核查

- 隔离代码树基于 `xxproject/claude/astra` 的 `163ff9b3b58a55bd90e6a009f3846ec1c19744af`。工作台原锁定落点仍在 `astra_workbench_v20.py` 与 `astra_locked_candidate_store_v1.py`。新增 `astra_player_tracking_v13.py` 连接原生盘口、卷宗与追踪证据。卷宗沿用 `astra_player_dossier_v1.py`。
- 生产只读核查发现 `astra-workbench-v20.service` 的旧自动复核在 2026-11-16 02:00 UTC 实际跑了 Put 与 Call，两侧各一次模型调用。另一条旧牌手入口为归档的 `ASTRA_PLAYER_ENABLED`，默认需要明确启用。实验开关同时阻断这两个入口的正式自动模型调用。`player-canary` 维持原人工研究入口，`/review` 与 `hold-review` 仍可人工调用。
- 提问单在归档中另有产出，工作台 `/inquiry`、核验路径与现有审计仍消费这些事实。人工作业和提问单保留原入口，归档仍独立采集市场事实。
- 前端复用 `audit-main`、`sheet`、`chip`、既有价格曲线，以及 `--paper`、`--sheet`、`--ink`、`--rule`。受会话保护的 `/api/workbench/player-audit` 提供有限列表、指定手牌、原文和归档轨迹。读取校验输入、回复、归档轨迹哈希。原文以文本折叠展示。
- 本地测试覆盖 Put/Call 精确匹配、拒绝与未知、读写授权、缺时钟与迟到回复、新旧 t0、同合约已有人工锁定、无回复重启核对、Stop、分钟缺口、币本位交割和清理后回读。Chrome 实际检查桌面与窄屏下两种锁定共用路径，双向跳转和原文安全显示。可用 `uv run --isolated --python 3.12 --with pytest --with jsonschema python -m pytest -q tests/test_astra_player_tracking_v13.py` 重跑集成测试。
- 广域回归的两条已知失败在干净基线同样复现。它们分别检查旧前端内存令牌写法和提问单术语同步。指定 gpt-5.5 的调查与审查 agent 因账户不支持该模型退出，由主模型完成源码核查和独立浏览器、HTTP 及原生样本验证。
- 生产版本、排程、FMZ 与实盘权限沿用既有运行状态。本轮真实启动观察在本地发生，官方交割尚未到达。历史结算验收来自独立 fixture。
