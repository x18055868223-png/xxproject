# 研究存档数据字典

适用：`/var/lib/xxproject/research_archive/`（只读，`research` 组）　日期：2026-10-20
机器可读形状见 `schemas/*.schema.json`，条件语言字段见 `schemas/condition_fields.json`。服务器代码（`tools/astra_research_archive_v1.py`、`tools/astra_research_inbox_v1.py`、`tools/astra_case_file_v1.py`、`tools/astra_inquiry_v1.py`）是最终依据；本文与代码不一致时以代码为准，并请用 `protocol_proposal@0.1` 指出。

## 0. 通用约定

### 0.1 时间

- 所有 `*_ms` 字段都是 **UTC epoch 毫秒**（整数）。
- 目录与文件名中的日期、小时一律按 **UTC** 切分（`cases/2026-10-20/14_put_…` 指 UTC 14 时）。
- 服务器本地时区是 CEST，**不要依赖它**：文件 mtime、系统日志时间都不是数据时间。
- Deribit BTC 期权在 UTC 08:00 到期交割；`expiry_ms` 即该时刻。
- 登记时间以服务器写入的 `received_at_ms` 为准，不以提交文档内任何自报时间为准。

### 0.2 单位（键名后缀）

| 后缀 | 含义 |
|---|---|
| `_usd` | 美元价格或金额 |
| `_btc` | BTC 数量或 BTC 计价的期权价格（Deribit 期权报价单位即 BTC/张，本存档按 1 BTC 名义计） |
| `_pct` | 百分数（`1.5` 表示 1.5%），不是小数 |
| `_ms` | UTC epoch 毫秒；`*_step_ms`、`coverage_gap_ms`、`ms_beyond_short` 等是时长（毫秒） |
| `p_*` | 概率，0–1 小数 |
| `mark_iv` | 交易所 mark IV，百分数（如 `45.2`） |
| `realized_vol_annual` | 年化实现波动，小数（`0.45` = 45%） |

### 0.3 null 语义

- `null` = 该时刻没有可用值（来源缺失、未接入、不适用或计算前提不成立），**不是 0，也不是“否”**。
- 布尔字段可能是三态：`true` / `false` / `null`（null = 未知，例如读视图拉取失败）。
- 条件语言求值时，任一所需字段为 null，该行条件结果为 null，样本被排除。
- 案卷条目另有 `quality`：`missing` / `unknown` / `stale` / `not_applicable` 的条目不得当作已知事实。

### 0.4 关联键

| 键 | 形状 | 出现位置 |
|---|---|---|
| `contract_key` | `<短腿合约名>\|<保护腿合约名>`，如 `BTC-21OCT26-82000-P\|BTC-21OCT26-80000-P` | ledger、outcomes、decisions、episode index、episode_view |
| `case_id` | `cf_` + `snapshot_id` 前 24 位 | 案卷、inquiry、outcome.cases、episode index、postmortem.inquiry_review |
| `snapshot_id` | 工作台冻结快照 id | 案卷顶层 |
| `expiry_ms` | 到期时刻 | 同一 `expiry_ms` 的合约共享交割价：**统计时按到期日聚类** |
| `ref_id` | 锁定 episode_id 或 Decision Path verification_id | decisions |
| `submission_id` | `<schema 名>:<id>`，如 `hypothesis:h-001` | registry、回执 |
| `question_id` | inquiry 问题单里的 `questions[].id`（如 `K1`） | postmortem.inquiry_review |

### 0.5 样本单位

一个样本（episode）= 一张合约（两腿 + 到期）从首次出现（`first_seen_ms`）到到期。行情移动会换腿，同一天同一侧会有多个样本；换腿后旧合约继续跟踪到到期。

## 1. 目录与遍历

| 路径 | 记录 | 写入方式 | chain kind |
|---|---|---|---|
| `chain.jsonl` | `archive_chain@0.1` | 只追加 | — |
| `cases/<UTC日>/<HH>_<side>_<case_id>.json.gz` | `case_file@0.1`（gzip，mtime=0） | 新文件，永不覆盖 | `case` |
| `ledger/<UTC日>.jsonl` | `ledger_row@0.1` | 追加行 | `ledger` |
| `inquiry/<UTC日>/<HH>_<side>_<case_id>.json` | `astra_inquiry_result@0.1` | 新文件 | `inquiry` |
| `outcomes/<到期 UTC 日>.jsonl` | `episode_outcome@0.1` | 追加行，每张合约一行 | `outcome` |
| `decisions/<UTC日>.jsonl` | `decision_event@0.1` | 追加行 | `decision` |
| `protocols/versions.jsonl` | `protocol_version@0.1` | 追加行 | `protocol` |
| `registry/hypotheses.jsonl` 等 | 登记包装（见 §10） | 追加行 | `submission` |
| `registry/rejected.jsonl` | 被拒回执 | 追加行 | `submission_rejected` |
| `legacy/…` | 存档上线前历史的一次性回填 | 新文件 | `legacy` |
| `index/episodes/<到期日>.json` | `episode_index@0.1` | **每次 tick 重建覆盖，不入链** | — |
| `receipts/<文件名>.receipt.json` | 提交回执 | 覆盖写，不入链 | — |
| `_state/` | 服务器内部状态 | 不供研究使用 | — |

### 1.1 变更流（推荐读法）

1. `chain.jsonl` 只追加。读者保存“已处理到第 N 行”的游标，每次只读第 N+1 行起的新行。
2. 每一行告诉你 `kind` 和 `path`：文件类（`case`、`inquiry`、`legacy`）读整个文件；行类（`ledger`、`outcome`、`decision`、`protocol`、`submission`、`submission_rejected`）表示该 `path` 末尾新增了一行——同一 `path` 的第 k 条链记录对应该文件第 k 行（从 1 起计）。
3. 可用链记录的 `content_sha256` 自行核对：文件类为整个文件字节的 sha256；行类为该行字节（含末尾 `\n`）的 sha256。
4. `index/episodes/<到期日>.json` 是派生便利索引，其 `chain_lines` 说明它基于链的前多少行构建；它可能落后于链、可能被重建，**不得作为证据或冻结依据**。

### 1.2 哈希链

`entry_sha256 = sha256(json.dumps(条目去掉 entry_sha256, sort_keys=True))`（Python 默认分隔符与 `ensure_ascii=True`）；`prev_sha256` 为上一条的 `entry_sha256`，第一条为 64 个 `0`。

## 2. `archive_chain@0.1`（chain.jsonl 每行）

| 字段 | 含义 |
|---|---|
| `schema` | `archive_chain@0.1` |
| `at_ms` | 写入时的 tick 时间 |
| `kind` | `case` `inquiry` `ledger` `outcome` `decision` `protocol` `submission` `submission_rejected` `legacy` |
| `path` | 相对存档根的路径 |
| `content_sha256` | 文件或该行的 sha256 |
| `prev_sha256` / `entry_sha256` | 链接与本条哈希 |

## 3. `case_file@0.1`（案卷）

每 UTC 小时、每侧（put/call）冻结一份当时显示候选的完整案卷。

顶层：

| 字段 | 含义 / null |
|---|---|
| `schema` | `case_file@0.1` |
| `case_id` | `cf_` + snapshot_id 前 24 位 |
| `snapshot_id`、`candidate_id` | 工作台冻结快照与候选 id |
| `asof_ms` | 报价估值时刻 |
| `contract_cn` | 合约中文描述 |
| `basis_cn` | 读法约定（概率基线为风险中性等） |
| `layers` | `L0`–`L9` 十个数组，总是全部存在；`L9`（历史）通常为空 |
| `refs` | `ref → {layer, index}`，按 ref 定位条目 |
| `build` | `builder`（如 `astra_case_file_v1@0.1.1`）、`bytes`、`built_at_ms`、`content_sha256`（不含 build 本身的内容哈希）、`time_note_cn` |

条目（`layers.Lx[]`）：

| 字段 | 含义 |
|---|---|
| `ref` | 稳定引用名，`L<层>.<主题>[.<项>]`；发布后不改名 |
| `label_cn` | 中文标签 |
| `values` | 原值，形状自由；单位写在键名后缀 |
| `relations` | 程序已算好的关系（可为空对象） |
| `asof_ms` | 该条目来源时刻；null = 来源未给时刻 |
| `quality` | `ok` / `stale` / `missing` / `unknown` / `not_applicable` |
| `quality_reason` | 质量原因码；null = 无 |
| `lineage` | 上游数据产品 id（排序去重），用于判断同源 |
| `source` | 取值位置 |

空间门口径（案卷生成器 0.1.5 起，2026-10-21）：`L3.space.gate` 不再含工作台自身的 `state`、`eligible`、`since_ts_ms`、`reason_codes`。自 2026-10-13 起空间门只作评估、不阻断，这四个字段恒为“放行”，与生产端状态并列时会被误读为冲突。结构状态看 `producer_state`（FMZ 生产端）；“不阻断”由 `gating: false` 与 `note_cn` 表达。`L6.daily` 每日的 `space_gate` 同样改取 `producer_state`，没有该字段的旧快照退回 `state`。0.1.4 及以前的案卷仍含这四个字段，读取时以 `producer_state` 为准。

价格截面与过期传递（案卷生成器 0.1.6 起，2026-10-22，docs/opus/18 §3）：
- 新增 `L0.cross_section`：现价锚（报价估值时的 Deribit 指数，`L1.economics.valuation_spot_usd`）与各来源（信号层快照价、GEX 现价、本期 forward）的价格、时刻、年龄、相对锚的偏离与是否当前。
- 信号层事实不是当前时，取自该快照的条目（L3.space.anchor、L4.skew、L5 信号层条目、L7.macro、L8.signal_recommendation）标 `quality=stale`、`quality_reason=fmz_snapshot_stale`；GEX 读数在估值时刻已超过 1 小时的标 `stale`、`gex_older_than_1h_at_asof`。原质量不是 `ok` 的保持不变。
- `L3.spatial_position.relations.expiry_alignment` 增加 `zero_dte_expiry_ms`、`zero_dte_expires_before_contract`；`L1.payout_table.relations.note_cn` 说明表中极端点只示意币本位形状。
- 这些由 `astra_case_file_v1.annotate()` 在构建末尾加入，也可作用于 0.1.5 存档案卷。

层主题：L0 证据质量、L1 合约与责任、L2 报价/希腊值/市场基线/补偿、L3 结构（Space 门、GEX、本期墙、地图）、L4 波动、L5 运动与流、L6 时间演化、L7 外部（跨资产、宏观、事件、慢背景）、L8 程序判读（Decision Path、guidance、信号层建议，可被质疑）、L9 历史。注意：L0 健康、Space 门、跨资产、本期墙、事件取自 `built_at_ms` 时的读视图，不是 `asof_ms`。

## 4. `ledger_row@0.1`（合约路径账本）

触发：首行 `first`；距上一行 ≥30 分钟 `base`；实质变化 `change`（现价跨过短腿/盈亏平衡/保护腿，短腿距离变动 >0.25 个百分点，Space 门、M-DIE 等级、Gamma 体制改变，显示状态改变）；到期前 3 小时内每次 tick `near_expiry`（约 10 分钟一次）。

| 字段 | 单位 | 含义 / null |
|---|---|---|
| `schema` | | `ledger_row@0.1` |
| `at_ms` | ms | 行时间（tick 时间） |
| `contract_key`、`side`、`expiry_ms` | | 合约标识 |
| `displayed` | bool/null | 是否为当前显示候选；null = 读视图不可用 |
| `locked` | bool/null | 工作台对该合约有活动锁定；null = 未知。新增字段，较早的行没有该键（视为 null） |
| `short` / `long` | | 两腿：`best_bid_price`、`best_ask_price`、`mark_price`（BTC）、`mark_iv`（%）、`delta`、`timestamp`（ms，盘口时间） |
| `credit_bid_ask_btc` | BTC | 短腿 bid − 长腿 ask，未扣费；任一侧无报价为 null；可为负 |
| `mark_spread_btc` | BTC | 短腿 mark − 长腿 mark |
| `index_usd` | USD | btc_usd 指数（取自盘口 `index_price`） |
| `forward_usd` | USD | 该到期 underlying_price |
| `short_from_index_pct`、`long_from_index_pct` | % | (行权价/指数 − 1)×100，带符号（Put 通常为负） |
| `breakeven_from_index_pct` | % | 第一个亏损边界相对指数；p_loss 未算出时 null |
| `baseline_status` | | `ok` / `unknown`（输入缺失时概率全为 null） |
| `p_itm_short`、`p_touch_short`、`p_loss`、`p_max_loss` | 0–1 | 风险中性市场隐含概率（Black-76，r=0，各行权价自身 mark IV）；`p_touch_short = min(1, 2·p_itm_short)`；`p_loss` 按未扣费 bid/ask 信用，信用 ≤0 时为 null |
| `states` | | 程序状态：`space_gate`、`m_die_level`、`gamma_regime`、`gex_market_state`、`flip_usd`（USD）、`tmv_24h`、`tmv_48h`、`micro_flow`；整个对象 null = 读视图不可用 |
| `view_available` | bool | 本行是否读到了工作台视图 |
| `trigger` | | `first` / `base` / `change` / `near_expiry` |
| `trigger_detail` | | 相对上一行的变化（2026-10-20 起，增量字段；所有 trigger 的行都带，base / near_expiry 行也可能非空）：`crossed_short` / `crossed_breakeven` / `crossed_long`（越过该价位）、`distance`（短腿距离变化超过 0.25pp）、`state:<名>`（space_gate / m_die_level / gamma_regime 切换）、`displayed`、`locked`；无变化为空列表。只有 `state:*` 的 `change` 行即“仅状态变化” |
| 研究字段（`fields_version` = `ledger_research_fields@0.1`，2026-10-20 起，增量） | | 定义与可用运算见 `schemas/condition_fields.json`；旧行没有这些字段，条件求值时按缺失排除 |
| `expiry_atm_iv_pct`、`expected_move_pct`、`short_cushion_pct`、`distance_in_moves` | % / 倍 | 本期 ATM IV；到期前一倍预期波动；指数到短腿的不利方向距离（正 = 价外）；二者之比（σ 距离） |
| `rv_24h_pct`、`rv_7d_pct`、`iv_minus_rv_24h`、`dvol`、`iv_percentile`、`rr_25d_expiry` | % | 程序口径已实现波动（永续 1 小时收盘）；本期 IV − RV24h；市场 DVOL 与 IV 分位；本期 25Δ RR（不属于本期时为 null） |
| `entry_fee_btc`、`credit_after_fee_btc`、`comp_ratio_after_fee`、`comp_tier` | BTC / 比 | 每腿 min(0.0003, 12.5%×顶档价) 的入场费；扣费信用；扣费补偿比与档位（与 L2.compensation 同口径） |
| `gamma_wall_usd`、`gamma_wall_vs_short_pct`、`pin_from_index_pct` | USD / % | 本期本侧 Gamma 最大集中价位及其相对短腿的不利方向距离（负 = 在现价一侧）；本期 pin 相对指数 |
| `hours_to_next_event`、`next_event_kind`、`event_before_expiry` | h / 文本 / 布尔 | 下一宏观事件（7 天内无事件且日历完整时为 168 / none）；到期前是否有事件（日历不完整时 null） |
| `index_chg_1h_pct`、`index_chg_4h_pct` | % | 近 1 / 4 小时指数涨跌（Deribit 公开 2 天分钟路径） |
| `session_phase`、`remaining_clock_factor`、`session_clock`（0.2 起） | 文本 / 倍 | 所处时段与到期前剩余时段的平均方差倍数（时钟模块版本见 `session_clock`；docs/opus/12、13） |
| `warning_any`、`warning_term_inversion`、`warning_premium_thickening`、`warning_busy_window`、`warning_rules`（0.2 起） | 布尔 / 文本 | 正在显示的合约在该时刻的不建仓警示（阈值未经验证，只作否决提醒；docs/opus/13）；不显示的合约为 null |

## 5. `episode_outcome@0.1`（到期结果）

到期 10 分钟后开始补取；交割价 12 小时内仍取不到则写 `outcome_unavailable`。每个 `contract_key` 恰好一行。

| 字段 | 单位 | 含义 |
|---|---|---|
| `status` | | `settled` / `outcome_unavailable` |
| `contract_key`、`side`、`short_strike_usd`、`long_strike_usd`、`expiry_ms`、`first_seen_ms` | | 合约与首次出现时刻 |
| `delivery_price_usd` | USD | Deribit btc_usd 交割价 |
| `payout_btc` | BTC | 1 BTC 名义的币本位到期赔付（≥0） |
| `cases[]` | | 每个冻结案卷：`case_id`、`asof_ms`、`net_credit_after_fee_btc`、`path`（案卷文件）、`result_btc` = 净信用 − 赔付；信用缺失时 null |
| `path` | | 指数路径统计，见下 |
| `meaning_cn` | | 静态结果说明：不是实际成交 |
| `reason` | | 仅 `outcome_unavailable`：异常类名 |

`path`（`first_seen_ms` 到 `expiry_ms` 之间的 Deribit 指数图点）：

| 字段 | 含义 |
|---|---|
| `status` | `ok`；点数 <2 时 `insufficient_path`（只有 `points`） |
| `points`、`start_ms`、`end_ms`、`median_step_ms`、`coverage_gap_ms` | 采样覆盖；`coverage_gap_ms` = 首点 − first_seen |
| `path_complete` | 首尾缺口都不超过 2 个中位步长 |
| `min_usd`、`max_usd` | 区间极值 |
| `touched_short`、`first_touch_ms`、`ms_beyond_short` | 是否越过短腿、首次时刻、越过状态的累计时长 |
| `touched_long` | 是否越过保护腿 |
| `max_adverse_vs_short_pct`、`settle_vs_short_pct` | 相对短腿的不利距离（%，正数 = 越过短腿）；后者用交割价 |
| `realized_vol_annual` | 对数收益年化实现波动（小数） |

注意：指数路径取自 `get_index_chart_data` range=2d，首次出现早于结果补取前约 2 天的合约会有覆盖缺口（看 `path_complete`）；步长以 `median_step_ms` 为准，不保证 1 分钟。

## 6. `astra_inquiry_result@0.1`（提问者输出）

UTC 02、14 时（前 45 分钟内）对该小时新冻结案卷运行；另外，合约首次出现后 2 小时内、尚无提问者输出的，在它的下一份新案卷上立即运行一次（2026-10-20 起；每个 UTC 日两侧合计最多 8 次；这次运行失败也算已提问，不再重试，等下一个 02/14 时段）。输入不含 L9。文件 = `{case_id, asof_ms, trigger}` + `inquiry.run()` 输出（`trigger` 为 `scheduled` 定时 / `new_contract` 新合约即时，2026-10-21 起；更早的文件没有，按文件名小时是否为 02、14 推断）：`schema`、`prompt_version`、`requested_model`、`effort`、`status`（`OK` / `PARTIAL` 有条目被丢弃 / `REJECTED` 结构不合格或传输失败 / `ERROR` 运行异常，此时只有 `error`，无 `schema`）、`attempts`（每次调用的模型、用量、finish_reason、是否修复）、`issues`（逐条校验记录）、`sheet`（`inquiry_sheet@0.2`，失败时 null）。

`sheet` 要点：`fact_brief.layers`（L0–L8 各层 `status` material/quiet/unknown 与带 refs 的事实）、`cross_layer`、`anomalies`、`questions[]`（`id`、`type`、`bears_on`、`priority`、`premises`、≥2 个 `hypotheses`（`direction` raises/lowers/unclear）、`answer_source`、可能的 `untraced_numbers` / `forbidden_wording` 标记）、`types_covered`。

提示词 0.4 起（2026-10-22，docs/opus/18）：
- 模型读的是案卷的阅读视图（`reading_view` = `inquiry_reading@0.1`：毫秒时刻写成 UTC 文字，时长写成小时/分钟，数字按显示精度，枚举码译成中文；`chinese_keys` 为 true 时字段名也译成中文）。数字溯源按同一视图；存档案卷保持原值。
- 新增 `metrics`：`questions`、`high`、`anomalies`、`code_tokens`（正文机器码数）、`untraced_numbers`、`possible_duplicates`。
- 机器码超过阈值时改写一次：`code_repair` = `{tried, adopted, before, after}`；只在改写版通过校验且机器码更少时采用。
- 条目上可能新增 `code_tokens`（该段正文的机器码）；问题上可能新增 `possible_duplicate_of`（同类型且引用重合 ≥ 60% 的更高优先级问题 id）。都只标记，不删除。

## 7. `decision_event@0.1`（工作台锁定与 Decision Path 事件）

| 字段 | 含义 |
|---|---|
| `at_ms` | 事件时间 |
| `source` | `lock` / `decision_path` |
| `event` | 如 `lock_created`、`lock_stopped`、`concluded`、`expired_open`、`settled`（开放集合，可能新增） |
| `contract_key`、`side` | 合约 |
| `ref_id` | 锁定 episode_id 或 verification_id |
| `detail` | 事件详情对象（形状随 event） |

## 8. `protocol_version@0.1`（protocols/versions.jsonl）

`at_ms`、`component`（`inquirer_prompt` `case_file_builder` `ledger_row` `episode_outcome` `submission_schemas` `research_protocol` `scoring_rules`）、`version`、`source`（`observed_in_code` 程序观察到 / `approved_by_user` 用户批准）、`note_cn`。研究时按 `at_ms` 把样本切到对应的组件版本。

## 9. `episode_index@0.1`（index/episodes/<到期日>.json，派生）

`built_at_ms`、`chain_lines`、`episodes[]`：`contract_key`、`side`、`short_strike_usd`、`long_strike_usd`、`expiry_ms`、`first_seen_ms`、`status`（`open` / `settled` / `outcome_unavailable`）、`cases[{case_id,path,asof_ms}]`、`inquiry`（文件路径）、`ledger_rows`（行数）、`last_row_at_ms`、`locked`、`decisions[]`、`outcome`（`{path,status}` 或 null）。每 tick 重建，不入链，只作导航。

`first_seen_ms` 是服务器首次看到该合约的检查时刻，等于它第一行账本的 `at_ms`；案卷的 `asof_ms` 是行情估值时刻，通常略早几十秒。`inquiry` 与 `ledger_rows` 两项计数从 10-20 03:11 UTC 起才开始累计，此前的已于 10-20 用 `reconcile-state` 从存档补齐。

## 10. 提交、登记与回执

投递到 `/var/lib/xxproject/research_inbox/`（先写 `*.tmp` 再改名为 `*.json`，≤1 MB）。五类 schema：`hypothesis@0.1`、`postmortem@0.1`、`protocol_proposal@0.1`、`research_note@0.1`、`episode_view@0.1`（形状见 `schemas/`）。共同字段：`schema`、`id`（`^[a-z0-9][a-z0-9_-]{2,63}$`）、`agent{name≤64,model≤128}`。

- 登记文件：`registry/hypotheses.jsonl`、`postmortems.jsonl`、`proposals.jsonl`、`notes.jsonl`、`episode_views.jsonl`。
- 登记行（`registry/<类>.jsonl`）：`{submission_id, received_at_ms, sha256（原始文件字节）, document（原文）}`。
- 被拒行（`registry/rejected.jsonl`）：`{file, received_at_ms, sha256, status:"rejected", errors[], schema}`。
- 回执（`receipts/<文件名>.receipt.json`）：`file`、`received_at_ms`、`sha256`、`status`（`registered` / `rejected`）、`submission_id` 或 `errors`；假设被拒时附 `allowed_fields`、`allowed_metrics`。
- 已登记 id 永不可改；修订用新 id + `supersedes`。
- `episode_view@0.1` 的 `contract_key` 必须是服务器正在跟踪、尚未到期的合约（见 `index/episodes/<到期日>.json` 中 `status: open`），且在 `expiry_ms` 之前被服务器接收（以 `received_at_ms` 判定），否则拒收。
- 前向验证只使用 `received_at_ms` 之后冻结的样本。

## 11. `legacy/`

存档上线前历史的一次性回填（锁定候选索引与点位、结算、Decision Path 记录、每日快照归档、承保台账），每个文件以 kind `legacy` 入链。其内容是事后收集的，**不满足“登记后冻结”，不得用于前向验证**，只能用于形成假设或描述性分析，且需在 `formation.samples` 中注明。

## 追加（2026-10-20，E3）

### index_path@0.1：`paths/<到期日>.json.gz`（kind=path，入链）
| 字段 | 含义 |
|---|---|
| `index_name` | `btc_usd`（Deribit 交割所用指数） |
| `expiry_ms` | 该到期日的交割时刻（UTC 毫秒） |
| `fetched_at_ms` | 抓取时刻 |
| `points` | `[[t_ms, price_usd], ...]`，Deribit `get_index_chart_data` 2 天范围，约 1 分钟一个点 |

### hypothesis_evaluation@0.2：`validation/<日>.jsonl`（kind=validation，入链）
每次评估每条假设一行。主要字段：
- `hypothesis_id`、`submission_id`、`registered_at_ms`、`evaluated_at_ms`、`metric`、`expected_direction`；
- `scoring_rules`（`scoring_rules@0.2`）、`threshold`、`k`（累计登记假设数）；
- `n_condition`、`n_control`、`days_condition`、`days_control`、`n_samples`、`pending_samples`（尚未结算的样本数）；
- `effect`（正值 = 更危险）、`se`（按到期日聚类）、`t`、`mean_condition`、`mean_control`；
- `milestones`：`{"60"|"90"|"120": {t, effect, days, at_ms, pass, against}}`，一经记录不再重算；
- `status_basis`（按规则得出的状态）与 `status`（被取代时为 `superseded`）。

### validation_status@0.2：`validation/status.json`（派生，不入链）
当前每条假设的状态汇总，可由 `validation/*.jsonl` 重建。

## 追加（2026-10-20，E4）

### promotion_action@0.1：`promotions/actions.jsonl`（kind=promotion，入链）
| 字段 | 含义 |
|---|---|
| `action` | `promote` 或 `retire` |
| `reading_id` | 研究读数编号 R-001 … |
| `hypothesis_id`、`submission_id` | 来源假设 |
| `promoted_as` | `program_reading` 或 `protocol_item` |
| `provisional` | 是否在 120 天最终结论之前晋升 |
| `label_cn` | 读数名称 |
| `approval_cn` | 用户批准的原话 |
| `reason_cn` | 撤销原因（retire 时） |
| `condition`、`metric`、`expected_direction`、`statement_cn` | 晋升时复制自假设，此后不变 |
| `evaluation` | 晋升时依据的验证统计（effect、se、t、threshold、k、milestones、天数与样本数） |
| `protocol_version` | 本次动作升级后的 `research_protocol` 版本 |

### promotion_candidates@0.1：`promotions/candidates.json`（派生）
“支持”且尚未晋升的假设，每天评估后重建。

### view_scorecard@0.1：`scorecards/views.json`（派生，每天 09:30 后重建）
| 字段 | 含义 |
|---|---|
| `views[]` | 每份 episode_view 一行：`status`（scored / pending_outcome / outcome_unavailable / no_entry_row / after_expiry）、`registered_at_ms`、`entry_row_at_ms`、`entry_gap_ms`、四个评级字段、入场基线 `p_touch_short` / `p_loss`、`metrics` |
| `metrics` | `touched_short_after_entry`、`loss_at_expiry`、`excess_touch`、`excess_loss`（相对入场基线）、`max_adverse_after_entry_pct`、`settle_vs_short_pct`、`result_btc_at_entry_credit`；口径同前向验证 |
| `by.<字段>[]` | 按等级分组：`level`、`n`（判断份数）、`days`、`contracts`（合约数）、`mean.<指标>`（各指标均值） |
| `ordering.<字段>` | 等级与各指标的 Spearman 相关（已统一为越危险越正，正值 = 排序与结果一致）；`days`、`enough_days`（≥30 天） |
| `counts` | 各 status 的数量 |

### intraday_exit_study_card@0.1：`scorecards/intraday_exit.json`（派生，每天 09:30 后重建；研究用，不是交易规则）
日内平仓点研究（docs/opus/12 第二步）。每个已结算合约的账本时点（每合约每 30 分钟取第一行）：此刻按对手价平仓（买回短腿卖一价、卖出保护腿买一价，每腿 min(0.0003, 12.5%×价) 手续费）对比持有到期赔付。
| 字段 | 含义 |
|---|---|
| `overall`、`by.<hour_utc / hours_to_expiry / clock_factor / distance_in_moves>` | `rows`、`rows_without_close_quote`、`contracts`、`days`、`mean/median_close_advantage_btc`、`contract_weighted_mean_close_advantage_btc`、`share_close_better`、`max/min_close_advantage_btc`、`mean_close_advantage_mark_btc`。close_advantage = 赔付 − 平仓成本，正 = 平掉更省 |
| `clock_factor` | 剩余时段相对全天平均的方差倍数（12 号文档节律，工作日 ×1.193、周末 ×0.515 换算到同一刻度） |
| `iv_clock.by_hour_utc` | 同一合约相邻两行（≤40 分钟）短腿 mark IV 的对数变化 ÷ 经过小时数，按发生的 UTC 小时平均；固定行权价 IV 也随现价变动，只作漂移描述 |
| `recent_samples` | 最近 3 个到期日的逐行样本 |
| `holding.by_hours_to_expiry`（0.2 起） | 持有口径：剩余保费（mark 价差）÷ 仍承担的最大亏损（宽度折 BTC − 剩余保费）的中位数与均值 |
| `entry.by_us_open`、`entry.by_hours_to_expiry`（0.2 起） | 入场口径：页面显示过的候选若在该时点按对手价扣费开仓，持有到期结果（BTC）、结果 ÷ 最大亏损、为正比例、最差一笔；按本周期美股开盘“尚未经历（或开盘不足 1 小时）/ 已经历 / 本周期无开盘（周末、NYSE 休市）”和距到期分组；宽度按当时指数折 BTC，币本位真实最大亏损为宽度 ÷ 交割价，比例可能低于 −1 |
| 逐行新增 | `width_btc`、`hold_premium_btc`、`hold_premium_to_max_loss`、`entry_credit_btc`、`entry_result_btc`、`entry_result_to_max_loss`、`us_open`（ahead / passed / none） |
| `enough_days` | 满 20 个到期日为 true；之前只作描述 |

### inquiry_scorecard@0.1：`scorecards/inquiry.json`（派生，每天 09:30 后重建）
| 字段 | 含义 |
|---|---|
| `by_prompt_version` / `by_type` / `by_priority` | 每组的 `reviewed`、四种 verdict 计数、`pointed_rate`、`wrong_premise_rate`（分母不含 unclear）；后两者的键为 `<prompt 版本>|<类型或优先级>` |
| `coverage.<prompt 版本>` | 已结算合约案卷的 `cases`、`cases_without_questions`（提问者失败或无问题单，不计入覆盖率）、`questions_asked`、`questions_reviewed`、`coverage` |
| `reviews[]` | 逐题评定（同一问题以最后一次为准），带问题的类型、优先级、answer_source、bears_on |
| `unmatched[]` | 找不到对应案卷或问题的评定 |

### research_reading@0.1：案卷 `L8.research_readings.values.readings[]`
| 字段 | 含义 |
|---|---|
| `reading_id`、`label_cn` | 读数编号与名称 |
| `applies` | 条件在本合约冻结时刻是否成立（true/false；null 表示字段缺失） |
| `direction_if_applies` | 成立时风险相对对照组的方向 |
| `metric`、`effect`、`t` | 晋升时的验证依据 |
| `provisional` | 是否为暂定晋升 |
| `protocol_version` | 读数所属的协议版本 |
