# ARD 最终收口

新版影子牌手使用生成器输出的完整 Markdown。可直接交给 AI 的规则和真实冻结牌面均在同一文件中。[完整输入](examples/player_ard_closeout/generated/ai_input.md) · [完整试答](examples/player_ard_closeout/player_reply.md)

在 `.worktrees/claude-astra` 中，运行现行入口生成新牌面。真实范例的完整复现命令见文末。

```powershell
python tools/astra_player_input_v1.py --archive ARCHIVE_ROOT --expiry-day YYYY-MM-DD --round r2 --out hand.md
```

`--archive` 读取案卷与位置卡，在有对应文件时读取分钟路径及结构历史。可用 `--index-path` 和 `--ard-history` 指定原始证据。可选的 `--ard-band-snapshot` 只接受窗前可用的原生半宽。生成后把 `hand.md` 整份交给 AI。生成器负责给出正文，本流程由人工交给模型。`--without-ard` 仅用于逐字复现旧一阶冻结正文，其中也保留了当时的旧规则表述。

旧评分链路由 `astra_player_v1.py::rulebook/build_request/run` 载入 `astra_player_rulebook.md` 作为系统提示。实际可调用的入口包括 `astra_research_archive_v1.py` 的 `player-canary` 与启用旧牌手时的 `tick`，以及 `astra_player_backtest_v1.py` 的旧评分回测。部署仓库中的归档 service 指向 `tick`，本轮只查源码，没有核实当前服务是否启用旧模型调用。新版生成器没有调用旧模块或读取旧规则书。两边的回归都实际运行过。旧手册保留原调用边界。

| 核查项 | 修改前证据与本轮最小差异 |
|---|---|
| 规则入口 | 旧评分的 `build_request` 把旧手册作为系统提示，现行 `build_input` 输出自己的规则。入口文档明确生成和人工试答方式，Legacy 调用路径原样保存。 |
| Reject | `build_input` 原先既写“两侧都有具体不利事实才Reject”，又写“两侧被排除才Reject”。新版要求各侧都有足以排除其承担条件的具体事实，并要求 Reject 答复给出两侧各自的排除依据。任何可接受侧优先 Accept。 |
| 保护腿 | `short_path_text` 原已记录真实 Call 首点越界与最近进入、回收。新增最强反证要求。只补两种此前会漏显的已见事实，残缺窗里的可靠极值和全窗恰好触及保护腿。沿用已选分钟样本、责任侧及原进入计算。 |
| Pin | `analyze` 已计算历史可比性，但 `render` 原以“已确认”描述变化，容易读成结构迁移。新版将截面输出值、可用时钟和可比性分开写，明确覆盖样本不等于独立更新。固定锚和逐点锚的计算结果逐字段比对，与上轮一致。 |
| 量价 | `build_input` 原只显示 4h/12h 价流数字。新版加一句来源和窗口口径，案卷事实时钟明确标为事实时钟。保留重叠窗和反证纪律。 |

### 11 月 14 日冻结截面的来源核对

- **ARD。** `astra_research_archive_v1.py::Deribit.index_path` 调用 `public/get_index_chart_data`，指数名 `btc_usd`，请求范围 `2d`。本例回放原生 `index_path@0.1`。以两侧较早读取切点 14:00:18.743 UTC 向前取四小时，窗口起点 10:00:18.743。该窗实际纳入 10:01:00 至 14:00:00 UTC 的 240 个分钟价，指数端点位移 −568.255 USD。以 68,000 为窗初固定锚，价格相对距离由 664.2 扩大至 1,232.4 USD。逐点 Pin 下的距离则从 664.2 缩至 232.4 USD。
- **现货量价。** `L5.volume_price` 的 `lineage` 为 `fmz.snapshot`，事实时钟 13:58:28.853 UTC。FMZ 默认从 Binance 现货 `BTCUSDT` 聚合成交生成已完成成交量柱，冷启动可用同市场 1m K 线补柱。`micro_flow_for_horizon` 取末根完成柱的 `complete_ts_ms` 为末端 E，选取完成时间严格晚于 E−4h 或 E−12h 的柱。`recent_momentum` 取首末选中柱的 `close`，`momentum_return_pct = 100 × (末收盘 − 首收盘) / 首收盘`。`cvd_sum` 加总选中柱的 `cvd_delta`。案卷保留的 4h、12h 首末完成柱间距分别是 14,396.629 秒、43,195.801 秒，分别为 334、1,222 根柱。冻结案卷没有保存首末柱绝对时刻、首末收盘价或逐柱回填标记，不能把 13:58:28.853 写成末柱完成时间，也不能从此案卷重算 −1.03513%。
- **Pin。** 输出位置从 68,000 到 67,000。历史行的兼容投影用原已冻结的同次采集成功回执约束可用时钟，历史算法版本身份仍不完整。240/240 是价格与当时已知锚的分钟对齐数。真实窗前可追溯半宽仍缺，因此不报停留率或邻域离返。

试答选择 **Accept / Call**。它逐一回应 Put 已发生的同向价流与 Call 在窗首越过 69,000 保护腿的反证，并把那个初始状态和 09:03 进入、12:59 返回分开处理。它保留最初回收条件和原检查规则，没有把旧越界自动当作排除依据，也没有为 ARD 凭空建立依赖。试答由主模型完成，未运行独立盲测或使用后续行情。指定的两位调查子 agent 和一位 reviewer 均因账户不支持 gpt-5.5 而未能执行。

### 复现与验收

```powershell
python docs/rulebook/examples/player_ard_closeout/reproduce.py --out .artifacts/ard-closeout-replay
uv run --isolated --no-project --python 3.12 --with pytest --with jsonschema python -m pytest tests/test_astra_player_input_v1.py tests/test_astra_anchor_relative_v1.py tests/test_astra_ard_condition_v1.py tests/test_astra_player_condition_v1.py tests/test_astra_expiry_structure_v1.py tests/test_astra_level_behaviour_v1.py tests/test_astra_research_archive_v1.py tests/test_astra_player_v1.py tests/test_astra_player_backtest_v1.py -q
```

227 项通过，无跳过。CLI 复现比对了旧一阶正文的原始字节，并比对了当前 ARD 计算结果和上轮冻结 JSON。测试覆盖完整与缺段窗口的保护腿触及、真实 Call 初始越界与最近回收、Reject 语义、两种量价身份、Pin 证据限定、旧评分及原持仓条件复核。真实案卷及旧冻结文档原样保存。历史报告里曾用当时的代码生成 A/B，重放该版本应使用对应冻结目录内的代码。

本轮保护积极 Accept、双侧择优、原有 ARD 数值计算、数据引擎、入场冻结、独立 `check_condition`、WPEM、FMZ 与实盘权限。最新正文可直接用于每日卷宗的人工影子试答。线上运行和采集状态不在本轮验收范围，本地没有提交、推送、打标签或部署。
