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
| `index/episodes.json` | `episode_index@0.1` | **每次 tick 重建覆盖，不入链** | — |
| `receipts/<文件名>.receipt.json` | 提交回执 | 覆盖写，不入链 | — |
| `_state/` | 服务器内部状态 | 不供研究使用 | — |

### 1.1 变更流（推荐读法）

1. `chain.jsonl` 只追加。读者保存“已处理到第 N 行”的游标，每次只读第 N+1 行起的新行。
2. 每一行告诉你 `kind` 和 `path`：文件类（`case`、`inquiry`、`legacy`）读整个文件；行类（`ledger`、`outcome`、`decision`、`protocol`、`submission`、`submission_rejected`）表示该 `path` 末尾新增了一行——同一 `path` 的第 k 条链记录对应该文件第 k 行（从 1 起计）。
3. 可用链记录的 `content_sha256` 自行核对：文件类为整个文件字节的 sha256；行类为该行字节（含末尾 `\n`）的 sha256。
4. `index/episodes.json` 是派生便利索引，其 `chain_lines` 说明它基于链的前多少行构建；它可能落后于链、可能被重建，**不得作为证据或冻结依据**。

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

UTC 02、14 时（前 45 分钟内）对该小时新冻结案卷运行（不含 L9）。文件 = `{case_id, asof_ms}` + `inquiry.run()` 输出：`schema`、`prompt_version`、`requested_model`、`effort`、`status`（`OK` / `PARTIAL` 有条目被丢弃 / `REJECTED` 结构不合格或传输失败 / `ERROR` 运行异常，此时只有 `error`，无 `schema`）、`attempts`（每次调用的模型、用量、finish_reason、是否修复）、`issues`（逐条校验记录）、`sheet`（`inquiry_sheet@0.2`，失败时 null）。

`sheet` 要点：`fact_brief.layers`（L0–L8 各层 `status` material/quiet/unknown 与带 refs 的事实）、`cross_layer`、`anomalies`、`questions[]`（`id`、`type`、`bears_on`、`priority`、`premises`、≥2 个 `hypotheses`（`direction` raises/lowers/unclear）、`answer_source`、可能的 `untraced_numbers` / `forbidden_wording` 标记）、`types_covered`。

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

## 9. `episode_index@0.1`（index/episodes.json，派生）

`built_at_ms`、`chain_lines`、`episodes[]`：`contract_key`、`side`、`short_strike_usd`、`long_strike_usd`、`expiry_ms`、`first_seen_ms`、`status`（`open` / `settled` / `outcome_unavailable`）、`cases[{case_id,path,asof_ms}]`、`inquiry`（文件路径）、`ledger_rows`（行数）、`last_row_at_ms`、`locked`、`decisions[]`、`outcome`（`{path,status}` 或 null）。每 tick 重建，不入链，只作导航。

## 10. 提交、登记与回执

投递到 `/var/lib/xxproject/research_inbox/`（先写 `*.tmp` 再改名为 `*.json`，≤1 MB）。五类 schema：`hypothesis@0.1`、`postmortem@0.1`、`protocol_proposal@0.1`、`research_note@0.1`、`episode_view@0.1`（形状见 `schemas/`）。共同字段：`schema`、`id`（`^[a-z0-9][a-z0-9_-]{2,63}$`）、`agent{name≤64,model≤128}`。

- 登记文件：`registry/hypotheses.jsonl`、`postmortems.jsonl`、`proposals.jsonl`、`notes.jsonl`、`episode_views.jsonl`。
- 登记行（`registry/<类>.jsonl`）：`{submission_id, received_at_ms, sha256（原始文件字节）, document（原文）}`。
- 被拒行（`registry/rejected.jsonl`）：`{file, received_at_ms, sha256, status:"rejected", errors[], schema}`。
- 回执（`receipts/<文件名>.receipt.json`）：`file`、`received_at_ms`、`sha256`、`status`（`registered` / `rejected`）、`submission_id` 或 `errors`；假设被拒时附 `allowed_fields`、`allowed_metrics`。
- 已登记 id 永不可改；修订用新 id + `supersedes`。
- `episode_view@0.1` 的 `contract_key` 必须是服务器正在跟踪、尚未到期的合约（见 `index/episodes.json` 中 `status: open`），且在 `expiry_ms` 之前被服务器接收（以 `received_at_ms` 判定），否则拒收。
- 前向验证只使用 `received_at_ms` 之后冻结的样本。

## 11. `legacy/`

存档上线前历史的一次性回填（锁定候选索引与点位、结算、Decision Path 记录、每日快照归档、承保台账），每个文件以 kind `legacy` 入链。其内容是事后收集的，**不满足“登记后冻结”，不得用于前向验证**，只能用于形成假设或描述性分析，且需在 `formation.samples` 中注明。
