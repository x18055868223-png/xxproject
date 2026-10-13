# Project Memory

## 2026-10-12 中性回路承保决策工作台 v3 阶段封版

- 当前工作流：信号层逐卡 LLM 复核已停用且不恢复；模型复核只在工作台进行，每天 UTC 02:00 / 14:00 两侧各一次（空间门放行时为生产用途，否则观察用途；时段先落盘，重启不重复；额度与单任务照常，只跳过两次点击间冷却），另有手动观察复核。`ASTRA_SCHEDULED_REVIEW_HOURS_UTC` 可调。
- μ_ref 不再是生产复核前置条件：现实概率版无严谨来源，市场隐含版使 M_ref 结构性为负、无信息；字段保留为 null，若将来有合格 μ，M_ref≤0 仍关门。
- 锁定追踪：`/locked-candidates/<id>/series` 给出逐点读数（按内容哈希校验，采样时即算并落盘缓存）；到期后按 Deribit `public/get_delivery_prices` 结算（t0 扣费后信用 − 到期赔付，不是成交记录）。可锁定点缓存需在复用时刷新先后，否则行情不动时会被挤出。
- 主链路审计日志 `astra_chain_audit@1.0.0`：状态目录 `chain_audit/`，按北京日分文件、哈希链、文件头带中文字段说明，30 天保留，`chain-daily.jsonl` 长期保留；`tools/astra_chain_audit_v1.py summarize/compare/verify` 用于迭代前后对照，说明见 `docs/astra/CHAIN_AUDIT_LOG.md`。
- 页面读 `GET /api/workbench?view=page`（只含页面字段）；`data_health` 中 `server_deribit_public_quote` 产品是按需记录，年龄不代表断流。工作台状态文件只保留最近 6 份完整复核回执，每日快照只读尾部并在 64 MB 时归档。
- 封版核对：`python tools/astra_release_check_v1.py --view-url <view> --root-deliverable <根目录交付物>`（交付物版本 / 清单版本与哈希 / 服务器运行版本 / 根目录同步）加服务器 `SESSION_CONTEXT_REQUIRED=1 EXPECTED_SIGNAL_VERSION=<版本> server_self_check_signal_stack.sh`（EU 默认路径与内部 8896 监听已内置）。

## 同一保单锁定与后续观察契约

- `locked_candidates` 独立保存不可变参考点，精确绑定 candidate/snapshot/packet；锁定与手动综合评审分开，锁定不依赖 provider、μ/M 或完整 Path。既有后台按原两腿、到期和数量观察，默认候选轮换不重置基线；GET 与导出不采集、不调用模型。存储位于 workbench state 下，预算暂停、采样缺口、停止及到期未核验须显式显示。
- 对照区分固定 t0/t1、最新 WPEM 窗口和锁定期间实际样本；源钟未知保持未知，换来源/产品/方法/单位/窗口不作精确差值。原生 t0 盈亏平衡线固定；平仓估计买回短腿 ask、卖出长腿 bid，并单列费用。未录入真实成交时只报参考结果，不能称实际持仓盈亏或执行许可。
- 锁定采样由独立线程按设定间隔进行，使用私有缓冲，不与刷新共享待写证据；刷新在采样线程运行时不再采样。完整证据文件按年龄抽稀，读数行（series jsonl）保留全部样本；被抽稀的点不能再打开为证据对、不能作为重新锁定的来源。清理先写索引再删文件，崩溃只会留下下轮清除的孤儿文件；t0、最新点与被保留差分的基准（点文件按内容寻址，可跨保单共享）永不删除。
- 锁定追踪的盈亏与 Deribit 组合浮动盈亏同口径：每条腿（当前标记价 − 锁定时可成交价）× 方向相加，不含手续费和平仓价差；最大盈利 = 锁定时开仓信用，剩余可赚 = 两腿当前标记价之差。旧的"t0 信用 − 当前平仓借记 − 平仓费"账面结果把进出两次买卖价差算进去，锁定一刻就显示约 −0.0014 BTC，不能当浮动盈亏展示。序列读数版本 `SERIES_METRICS_VERSION` 升级时，旧行在点文件仍在时重算，已精简的点保留旧读数。
- 2026-10-13 起空间结构只作评价、不作准入门控（`astra_underwriting_v20.structure_gate` 恒不阻断）：FMZ 原生门把 Flip 锚点（现价相对 Flip 轴的半径，评分 ≥60）和净 Gamma（为正、新鲜、体制不在过渡/冲突）作硬条件，两者证据都撑不起门槛（翻转位附近的“过渡”也可能是被翻转位锚住）。页面只展示锚点评分、净 Gamma、Gamma 体制与 FMZ 原生状态（仅参考）；FMZ 本体与原生 state/reason_codes 不改。FMZ 事实非当前或版本不符仍以 `fmz_fact_not_current` 关闭复核（数据资格，不是空间评价）。变迁记录改为锚点效用（有效/失效）及其邮件，此前为旧口径空间门。
- 空间位置因子（`astra_spatial_position_v1`，冻结快照的纯函数）：沿各侧不利方向，现价到短腿之间先遇到的 Gamma 结构（墙的正负、跨越 Flip）决定相对有利/不利，无结构时看短腿在 Flip 哪一侧；KPF 只描述，链上成本不进入；未经交割验证，不参与门槛。

## 2026-09-27 MAP v2.4.2 来源语义与因果阅读链

[修订69](docs/astra/69_MAP来源语义与四问工作台v242本地修订冻结_20260926.md)、[设计70](docs/astra/70_工作台整体数据与因果推理设计整理_v242_20260926.md)及[验收71](docs/astra/71_工作台来源与因果整理v242本地验收_20260926.md)确立源事实、确定性解释和人工 LLM 意见的独立表达层。四问完整保留四类背景，关键门控缺口优先、其余分歧按数量折叠；ETF BTC 列是同版合格指数下名义折合，CP 是 Bitview 持有者资本化价格，币安 OI/Funding 是 U本位原生持仓和实际结算，不能混成净买压或重复确认。Put 下跌、Call 上涨为合同不利方向，未选不借候选；名义/异源/非同刻价格不精确共位。TMV/CVD/EDB 不具有校准概率语义。

折算和价格轴源钟要求 MAP/manifest 截止同时存在且相等、BTC/美元及价系身份明确一致、时钟非未来及用途资格成立；混合资格不称整体完备。合成正常态和真实生产截面冻结回放隔离，回放不补 FMZ/原生空间门、合同或风险参考，按原冻结用途显示资格且明确非当前；实时时效守门不放宽。本地相关131项测试及桌面/窄屏验收通过，模拟忙态自动回执不等于真实 LLM 验收。本版未提交/推送/部署，程序仍在正确开发树，发布须本版本的新“当前本地页面可推送”；已部署数据服务、FMZ实例、自然卡消费和经济有效各自保留证据上限。

## 2026-09-26 同机轻量KPF生产与MAP消费已部署

[协议67](docs/astra/67_同机KPF轻量生产消费与MAP数据完备性实施冻结_20260926.md)及[验收68](docs/astra/68_同机轻量数据服务与MAP完备性验收_20260926.md)记录数据服务独立发布：后端代码1e1fac2f已推xxproject工作分支并以128,939 B薄包安装在固定KPF/MAP namespace；生产与消费同机，五分钟timer两轮自动成功且MAP读取同版原生三件hash。原KPF十份算法源hash、90/60日窗、100 BTC相位、50 USD桶与A/B资格保持；流式density不保留全bars/解压CSV/ZIP双副本。磁盘留空6 GiB、源预算5 GiB，KPF/MAP内存分别240/128 MiB；一日真实约103万成交RSS24 MiB、MAP33 MiB，完整90日资源尚未证明。

真实指数、ETF、CP_STH/LTH/ALL、严格14日兑现损益、OI、实际Funding及两项美债已进同机统一引擎；宽美元观测过期、无借款授权、KPF仅2/90 WARMUP、FMZ快照0 B与当前GEX/期限门缺失，最终七组中三组完整、全局完备性false。此进度为验收时点，不预先宣布90日READY。新数据有生产背景独立身份，不混入历史训练或自然卡验证；旧Web/FMZ/LLM/模型不随数据上线升级，新链路零LLM不表示旧自动LLM任务停用。此前66未接通及完整兑现窗缺口是前序状态，以68的新证据为准；当前工作台页面仍未发布，需本版本的新确认与统一引擎绑定。

## 2026-09-26 MAP v2.4.1 本地数据与责任表达

[协议62](docs/astra/62_MAP数据与合同责任v24实施冻结协议_20260926.md)、[修订64](docs/astra/64_MAP真实数值轴与决策表达_v241修订冻结_20260926.md)与[验收65](docs/astra/65_MAP数值轴与责任联动v241本地验收_20260926.md)确定：真实线性价格轴、四行定量背景、最多三句确定性摘要及同版保单区域责任联动，不新增模型、mu/M、门或权限。名义成本与不同冻结时点合同不精确共位；Funding按实际结算时钟分窗；美元指数点、百分比与收益率bp分开。KPF只在refresh采集同版三件套入引擎，全部合格有限池用于责任，默认两条展示；失败artifact身份同样冻结，GET/manual仅缓存读。

真实公开两侧合同与慢源已形成冻结回放，但FMZ/GEX/KPF、完整兑现窗与借款仍有缺口，过期OI退出主事实。真实mu/M缺失和手动评审阻断不被页面修复改变。合成与回放隔离，前端交互零真实LLM；原金融账、模型与FMZ本体保留。工作程序仅在正确工作树，本地验收不证明服务器实例加载、自然卡消费或经济有效；本版本仍须用户重新确认“当前本地页面可推送”。此前v2.3“MAP细节延后”是当时状态，现以本条和验收65为准。

原 KPF 算法及部署契约已在[审查66](docs/astra/66_KPF原算法适配与最小服务器接入审查_20260926.md)核对：原生 snapshot/audit 是 JSON，debug 是 CSV，当前 MAP 目标 JSON 适配层尚未接通真实 producer；公开 display_band 不能代替 raw basin。优先复用原 kpf_server 流式单 worker 并补薄导出，保留必需90日计算窗、移除报告/实验部署面；不升级为选边/EV，未完成真实接通。

## 2026-09-26 数据引擎 v2.3 与持续截面边界

[协议60](docs/astra/60_数据引擎与持续截面写入v23修订协议_20260926.md)及[验收61](docs/astra/61_数据引擎v23与持续截面本地服务器验收_20260926.md)确定数据产品身份、源观察/可得时钟、质量与用途期限，由后端统一判断资格；页面读取缓存，人工任务冻结实际数值及来源。注册不等于源已接通，历史导入不能升格为当前事实，缺 Gamma 不从价格补造。健康页面按六类逐层折叠，保留中性纸面风格。

服务器独立 data-only 五分钟维护已安装，只接收 FMZ 本地截面并按精确名单保留/清理日志，不部署新前端或调用 LLM。旧生产自动 LLM timer 尚未迁移；新数据路径零 LLM 不代表全生产栈已纯手动。根目录可见 FMZ 交付物已同步为本地候选 2.0.1，默认 300 秒原子写近期原生截面、180–600 秒配置边界；运行实例尚未证明加载，现行快照为空，阶段 A 生产验收未通过。MAP 细节延后，模型/交易权限/经济裁定不升级。本版页面仍需新的“当前本地页面可推送”确认。正式发布须显式纳入未跟踪模块及被本机 exclude 忽略的 registry、文档。

## 2026-09-25 Astra v2.0 市场骨架与手动回执本地待审

[追加协议57](docs/astra/57_市场骨架与评审回执工作台修订协议_20260925.md)及[验收58](docs/astra/58_市场骨架与手动回执本地验收_20260925.md)记录稳定边界：主工作台从同一 FMZ 连续快照投影非 LLM 量价、宏观、空间、EDB 市场骨架，缺事实仍阻断；本机模拟正常态独立标识且零真实 LLM、不入部署包。人工评审按钮显示忙态并只读轮询持久回执，回执绑定冻结快照及 Put/Call 侧别；同腿新快照仅在再次明确点击且门禁/冷却/共享预算通过后可评审，旧同快照尝试与失败不覆盖。109 项 v20 本地测试及 Nginx 回执路径静态检查通过；本地页面已审，尚未获本版“当前本地页面可推送”确认，未提交/推送/部署或证明经济效力。根目录可见 FMZ 仍旧版。

## 2026-09-25 空间约束工作台 v2.0 本地待审

[修订协议55](docs/astra/55_空间门与动态工作台v20修订协议_20260925.md)和[本地验收56](docs/astra/56_空间门工作台v20本地验收与发布前边界_20260925.md)记录本版：开发树 FMZ 本体 `2.0.0` 原生输出 fail-closed SpaceGate；持续工作台并列生成 Put/Call 开仓前理论候选，卖出腿取同到期可用绝对 Delta 最接近 0.35 且不超过 0.5，精确 2,000 USD 腿宽、1 BTC。净信用专指权利金差减两腿理论建仓手续费；BTC/USD 折合、到期时间、到期盈亏平衡根和条件性全费用 `M_ref` 分层显示，实际成交/持仓/退出账不补造。只有合格原生 FMZ、同版风险参考及正余量才可能开放人工一次 LLM 复核；LLM 不改数和交易许可。当前本地实况缺 FMZ v2 原生事实和合格 `μ_ref`，主门阻断；测试与浏览器只证明本地行为，不证明经济 edge。`xxproject` 是权威远端，根目录可见 FMZ 仍为 `1.6.2`；本版未提交、未推送、未部署，待用户审阅此版本地页后重新明确“当前本地页面可推送”。旧版原始结果及失败保持原身份。

## 2026-09-25 当前具体保单 v2.0 本地候选链

[本地验收53](docs/astra/53_当前保单v2本地验收与待推送清单.md)、[修订协议52](docs/astra/52_截面承保v2本地修订协议.md)及[发现规则54](docs/astra/54_当前保单发现口径补充冻结_20260925.md)记录独立只读候选路径：现有 FMZ 每 tick `EvaluationSnapshot` 可提供源身份/时钟明确的连续事实；明确两腿和数量→公开盘口估算→持有到期费用→合格冻结赔付参考（缺失即 `M_ref=null`）→同版单次因果适用性评审→独立候选卡。自然 NR 与固定轮、原 LLM 卡评审不改。2026-09-25 一次手动采集按最接近 24 小时、1 BTC、精确 2,000 USD 宽度生成 26SEP26 Put/Call 两张并列保单：均绑定同一当前 FMZ 行、盘口深度和时钟通过，但没有合格 `μ_ref/M_ref`；正信用不证明经济 edge 或选侧。研究侧两卡各一次正常 LLM 请求、一次格式恢复，最终均语义拒绝；失败回显和 4/4 共享 HTTP 账保留，未生成合格正文，不重发。原始 API 回复在本次修补前未被留存，后续已改先存回复再校验。最终本地页 `http://127.0.0.1:8890/` 已审阅，五张旧卡与一张历史回放 SHA 不变；本版仍未提交/推送/部署，等待针对本版的“当前本地页面可推送”。历史失败与生产缺口详见53。

## 2026-09-23 本轮重要内容静态页面发布

[发布51](docs/astra/51_本轮重要内容页面发布与服务器验收.md)记录本轮页面真实发布状态：`xxproject/codex/astra-signal-rating-v1` 已推 commit `1d05d1b7537ad0f8d0020176958ae987fde7c905`，公网 `http://13.231.16.198/signal-audit/` 已上线四文件静态前端包，显示逐卡动态“本轮重要内容汇总”且无顶部固定 R17；09/22 最新卡与 09/21 卡切换后重点内容不同。第一次完整后端+前端部署失败并自动回滚，因新版 materializer 复核旧 `2.2.0` 评审导致 15 卡中 6 卡 LLM JSON 改变（3 `ERROR`、3 `PARTIAL`）；失败保留。最终成功范围仅为 `frontend_static_only`，active `/opt/signal-audit-releases/astra-1d05d1b-frontend-20260923`，backup `/opt/signal-audit-backups/astra-frontend-before-1d05d1b-20260923`，token `20260923-astra-card-highlights`。15 卡身份与 LLM 签名、sidecar/usage/exclusions、backend 核心工具和 `llm.env` 不变，两个 timers active/enabled；最新真实卡仍为 FMZ `1.6.2`、LLM `2.2.0`，未证明 `2.3` 自然卡消费。只读自检 `24 OK / 2 WARN / 2 FAIL`；服务 `activating` 是当时快照，最新卡缺 `near_term_market_context@1.0.0` 仍使自检保留 FAIL，不可写成全栈通过。原 R17 商业结论、模型、FMZ、90 日验证和经济有效性均未改变；49、50 保留为发布前审计历史。

## 前序收束：R17 固定保单研究工具

[最终入口48](docs/astra/48_R17研究收束与最终交付.md)是当前唯一阅读入口。本轮探索性模型扩张已结束；保留核算、条件补偿参考和诊断能力，商业裁定仍为“风险工具有用，业务未成立”，不表示实盘收益改善已证。B0/B1/B2冻结并列且不新增策略许可，局部允许集合为空。81.36%的事后余量消耗不是校准倍率。原R17报告、原协议、55项证据、失败和旧版本均保持原hash。

前序新选侧、状态/HMM/Kronos/Jev、阈值、等待/退出/复杂对冲等建议归档，不再作为必须完成的当前待办；A+B仅保留未验证的独立情景。未来只在已有同一候选资料能连接冻结风险、同期报价、采纳/拒绝/未成交、完整执行及账户约束，或用户另行授权必要验证时，重审真实单位经济性。不会因一次盈利或新模型名称自动重开。

收束当时只更新入口与记忆，程序仍使用正确开发工作树。随后页面静态包按51上线，但该上线不改变R17研究裁定、后端runtime、FMZ、保护、单次LLM职责、自然卡消费或90日验证。根目录是另一工作树、仅同步说明，R17原证据在根目录.artifacts下，不会自动随开发树提交。新增docs受本机.git/info/exclude的/docs/规则影响，提交时须按显式清单纳入；不粗暴加入整个.artifacts。

## 已归档：v1.7核心承保商业审查

[研究47](docs/astra/47_核心承保单位经济与商业可行性审查v1.7.md)复用v1.6原动作、五价与时间外预测，完成固定保单单位经济账。主裁定为“风险工具有用，业务未成立”；当前暂停模型/状态/阈值扩张，保留最低所需补偿工具。自适应情景下，统计Put已承保集合的预测余量0.064346被赔付低估0.052354大幅消耗，Call低估超过余量；整体精度不能替代已承保集合的金额校准和真实净价格。

主历史仍为已看2022—2025、08UTC每侧1457日，不是自然NR。研究31已有六个原先冻结的真实顺序盘口，2026-09-15官方交割77024.57已在v1.7独立附录补齐；同一交割日的相关备选不能算六个独立成交或拼成已执行组合。0.1仅演算数量，实际成交、账户费用、对冲/资金费与资本适配仍未知，actual_EV未识别。

原历史冻结日和最终拟合上限不变；新增到期结果只在R17附录，不改训练。R17最终run_02使用稳定erfc修复微小负单腿估值；run_01、测试失败和原v1.1—v1.6均保留。新16项口径测试及独立复算通过；qualified模型、FMZ、页面和发布/90日边界不变。后续唯一需区分的是同一固定保单的可保留净信用能否覆盖冻结风险、真实执行成本及个人资本/维护约束；不要求恢复全期权历史，也不自动开始监控或交易。

## 前序主线：v1.6价格条件承保与局部状态诊断

[研究46](docs/astra/46_价格条件承保与状态局部诊断v1.6.md)在独立 R16 冻结并完成固定原组合承保、四小时 UM 状态诊断、Q1/Q2/H2 局部校准、同侧让价与退出净亏勘误。有效目录 run_01、local_03、appendix_04。共同截面93,242行不代表自然NR；主08UTC每侧1,457日。自适应共同报价下，几何承保相对全做四年均改善，但统计相对几何增量两侧区间均跨零。RANGE仅触发候选复查，八个年侧准入均失败，允许集为空；没有采用新状态规则。

退出旧指标是支付/退出支出尾部，不能叫完整净亏尾部；本次扣建仓信用与费用后，全触腿退出仍降低尾部且损害平均结果。S06结构让价效果依价格假设变化，不能给通用外移许可。全部失败与工程修订保留，不覆盖原v1.1—v1.5；qualified模型hash、FMZ、页面、发布门、自然卡消费与90日状态保持。真实期权历史报价缺口继续按外生情景研究，actual_EV未知。

本轮73项测试通过、1项旧hmmlearn可选对照因库缺失跳过；不得借用历史125项/11脚本/API验收。最值得继续定位的是补偿边界附近的金额偏差及跨期稳定性，后续模型/等待/风险预算修订须另冻。

## 前序主线：v1.5状态设计盲区审查

[研究44](docs/astra/44_HMM设计盲区审查与状态对照v1.5.md)在独立R15目录冻结并执行WINDOWS、DYNAMIC、MIX2、HMM2、HMM_RESET五个对照。最终run_02完成四年度、143552条任务预测；四个HMM主情景增量区间均跨零，两侧尾损门也未同时通过。不能把HMM状态持续、预测MSE或部分年份非劣当成组合效用；此结论只约束本次有限配置，不否定所有序列模型。

重要来源勘误：共同档案`entry_price`来自现货，`last_closed_price`来自UM永续。旧v1.4退出函数读后者却命名trigger_spot；原始zip已独立确认。新实验以明确现货适配器修复，旧93242条评价机会有1919条触发状态、10343条首次触发时间改变。旧退出结果保留为legacy UM参考身份，不能再称现货触发；原结构、模型及旧制品不覆盖。R15的run_01因该来源错误主动停止，不能作为有效现货实验。

run_02使用统一外生默认期权估值，真实行情/交割标签不默认化；默认价不能冒充历史可成交信用。主IV60情景下全部触腿退出和模型选择退出均未改善平均结果，全部退出虽降低尾损却付出均值代价。模型与市场价格缺口分别裁定，不能再因报价无法补齐而停止所有路径研究。

R15本地工程和独立复算通过，研究采用门未过；2023年度Put退出一个状态仅9个校准日，也未过预设支持。原qualified模型、FMZ、页面、发布门、90日和自然卡消费状态不变。新实验须独立冻结，不在本轮失败后追加状态数/阈值/估值搜索。

## 前序主线：v1.4结构校准、日期准入与退出审查

[研究43](docs/astra/43_结构校准日期准入与退出机制研究v1.4.md)冻结执行了两种结构修订、上一年阈值日期准入和两条首次触发退出规则。结构校准相对旧几何表的差额MSE改善约Put 2.71%、Call 4.49%，非回退日期权重提高至约78%/76%，但平均偏差仍不如简单历史均值，全部候选未通过冻结开发门。不能把MSE改善当作胜率、净收益或可直接接受的信用让步。

按上一年阈值准入未保持年度覆盖：几何两侧2023全入、2024/25全弃，统计两侧也未过稳定性和尾损门。跨年分数变化与输入分布变化同时存在，尚未分离模型版本因果；不靠当年分位回填挽救规则。

退出回放保留所有原机会、首次触发、路径缺口和币本位尾损。参考内在差额情景中，短腿触碰后退出值得优先询价检验；侵入半宽再退出出现尾损降低而平均结果变差。参考价并非可成交组合成本或保证价格边界。10,572份历史腿记录全为print/mark，无合格退出双腿BBO，真实退出增量不可评价；初始建仓信用在同一持仓退出与持有的增量中抵消，但实际退出成本仍不可缺。

正确结果位于独立R14目录的structure_02、admission_02、exit_01。结构01的跨窗gap分母报告错误与准入01均保留，修正不改模型/预测或决策。原v1.1—v1.3、qualified模型、FMZ、发布门与90日状态保持；全部历史已看，不能声称自然NR实际消费或经济有效。

## 前序主线：v1.3机会质量与同侧结构差额

已完成[研究42](docs/astra/42_机会质量与同侧结构差额研究v1.3.md)的冻结实验与本地验证。相同50%覆盖下，统计机会排序的均值点估计略优于几何，但两侧均仅2/4年非劣，区间跨零，去十个最有利日期后转差；入选日期重新等权也转差，不能迁移成每日固定建仓量的规则。

同侧精确同宽外移建立113,223条可追溯配对，缺口与全尾损保留。Put几何差额MSE四年改善，但平均节省偏乐观且细分支持不足；Call增加波动也未过覆盖门。全部候选仍未通过开发支持，不用MSE改善声称胜率或经济edge。真实同期信用、自然NR消费效果仍未知，运行模型/FMZ/发布门/90日状态不变。

用户明确下一阶段应围绕建仓质量推进统计、机制复核、精算、择时和结构择优，不能把最终模型限定为强制选侧，也不以精确估计黑天鹅单卡概率为前提；真实尾损仍完整保留。见[建仓质量优先的阶段研究设计41](docs/astra/41_建仓质量优先的阶段研究设计.md)。41保留为前序讨论，随后已在42冻结执行；v1.2失败裁定、qualified模型、发布门和90日状态不变。

## 前序主线：v1.2模型诊断与有限优化

固定S50收缩和PAIR_RIDGE10差值目标均未通过开发期选侧保留标准；条件尾部NESTED_LOGIT_C01虽保证事件包含关系，仍受校准期真实尾部日不足及年度Brier限制，不能进入逐卡建议。失败不触发额外模型/阈值搜索；原qualified模型身份不变，也不表示自然卡净效果有效。

明确方位与数值冲突的推荐侧须同时在摘要、详情和前向选择中隔离；原始意见保留，有效联合选择记为insufficient，统计及其它对照不受该显示资格修复改写。分钟资料在所有实时/回放入口严格要求完整形状且close<asof。

最新入口为[诊断与有限优化40](docs/astra/40_模型诊断与有限优化v1.2.md)。v1.1选侧失败的已核验原因是两侧共同水平MSE改善、两侧差值MSE恶化；总体均值精度不能代替选侧效用。成对、收缩和条件尾部候选必须先在独立目录冻结范围与停止条件再执行，原v1.1不改写；所有历史已看，尾部一致性、校准、尾损辨识与自然卡经济效果分别验收。

冻结模型行复用与源码重建是不同身份：旧观测档案包含后来分钟校验会拒绝的两条现货参考，完整模型输入未命中；不能宣称当前源码逐字重建旧观测档案。自然卡与训练须共用完整闭合分钟契约，历史接收延迟未知，不能用价格补造冲击起点或Gamma。

## 前序主线：联合研究 v1.1 自然信号验证

新窗口先读[模型优化交接39](docs/astra/39_换窗交接与模型优化入口.md)，区分当前代码工作树、封存研究与后续建议；现阶段接续模型诊断优化，不以工程通过替代发布或经济验证。

当前规格与验收入口是[修订37](docs/astra/37_联合研究v1.1修订与验收.md)。主要效果对象为现行自然NR接管卡；全市场30分钟共同截面提供训练，60分钟仅敏感性，新价格事件保持独立影子身份。自然卡不补造冲击起点，统计与联合复核的增量分开评估；选择后的胜率必须同时报告覆盖、未选机会和尾损。

训练口径为2022—2025四个年度滚动，前21个月拟合、后3个月校准；最终窗口截至2026-08-31。此前未完成的季度滚动属于实施偏差，不能作为本版主选模。历史已被使用，不重新宣称未见检验。模型池仍为收缩GAM与浅层CatBoost。

一次LLM只复核冻结统计结论的适用性，不修改数值或许可。常规前向发布只查覆盖和健康，固定终点再计算绩效；暂缓与未选也留账。Prompt/输出/投影2.3.0，事实包2.1.1，FMZ1.6.2不变。本轮页面必须重新确认后才能发布；工程完成、影子上线和90日效果结案分别验收。

v1.1稳定证据边界：共同截面年度开发验证中，统计特征改善赔付MSE但选侧实际赔付未优于几何基线；不可称自然NR或净胜率增量。主选模型的独立尾部头出现包含关系违例，全部逐卡尾部概率只留研究；运行候选必须使用带`research_only`策略的独立合格投影，不能通过截断或混入旧模型恢复尾部数值。原生训练与标准库一致性、模型资格、LLM引用正确性和经济效用分别验收。详细证据和制品身份见修订37。

## 历史：联合研究 v1 本地版与封存结果

用户已采纳[联合研究35](docs/astra/35_联合研究与验证路线_v1.md)，本地实施与证据见[验收36](docs/astra/36_联合研究实施与验收.md)。新价格再平衡事件不依赖期权Anchor准入；2020—2021训练、2022选择后，2023已经开启并完成封存测试，今后不能再次称作未见年份。2024以后为已研究开发资料。

历史v1（不是本轮v1.1）的33个候选均保留，当时三组优选均为GAM，全局选择为统计组。联合组当时尚未证明优于统计组；严格经济增量因缺历史同期报价仍为资料不足。当时的共享加性GAM未显式建模侧别与压力/主动流交互；该问题属于v1历史局限，本轮v1.1已单独修订侧向输入并完成新的年度候选比较，实际模型身份以37为准。报告数学纠错保留原报告身份，不能混同于模型修改。

自然NR映射属于域外开发投影，缺冲击起点等特征；期权字段资格不足时不拟合小样本增量。独立影子统计不与D–S平均，原单次LLM前可得摘要才入本次解释，迟到仅供阅读。FMZ1.6.2、现行NR触发和权限保持；M4须本地页面确认后发布，M5为固定90日验证，资料不足不自动延长，不建立Codex定时跟进。此前v2.3不整包恢复。

## 后续迭代的统一参考入口

开始相关策略研究或版本规划前，先读[核心发现与后续迭代共识32](docs/astra/32_核心发现与后续迭代共识.md)。当前没有经过验证的稳定局部优势；空间/方向必须连接到具体候选的赔付差与真实净补偿。数学成立、历史观察、检验未通过、未知与建议分别保存；不把未通过写成弱优势，不把下一步研究建议写成交易规则。下文保留详细历史与来源。

## 环境状态与倾向性职责边界

[评估33](docs/astra/33_环境状态与倾向性职责再评估.md)复核了原始假设与当前输入：已有4h/12h、宏观及近端证据，不能说现行模型完全没有背景数据；短时倾向仍不能自动代表到期风险。公开信息不等于完全定价，更高层的公开指标也不自动形成Alpha。环境解释与候选净补偿需要连接，前者的升维方案仍是待验证建议，不是已接受的交易规则。后续引用此议题时先核对该文及共识32。

## 报价连接的稳定边界

[研究31](docs/astra/31_真实报价下的空间与补偿比较.md)已建立隔离的同到期同宽真实报价比较。数学上，同宽外移的赔付减少集中在中间侵入区；两组均不赔或均穿过保护腿时，不减少赔付，少收信用会降低净结果。相对更好区间包含少亏，不能当盈利区间或概率。不同账户保证金不能用相同名义近似宣称等风险。

新盘口不可回填旧卡或沿用旧卡为当前判断；原卡、报价和结果分别保存时间。研究30未通过结论不变，候选比较没有证明正期望；自然新卡的同期空间事实和后续交割仍需新证据，不新增定时任务。

## 当前研究裁定：局部条件允许，长期验证尚未通过

研究30已完成一轮封存检验，结果见[局部条件长期验证结果](docs/astra/30_局部定式长期验证结果.md)。2024训练、2025上半年只选一次、2025年7月至2026年5月锁定检验；2,643入场、881交割日。既有176卡已反复查看，不再冒称未见样本。

X1弱传导和X3低波幅双卖的锁定匹配赔付节省仅+0.401/+0.462个百分点（分母是实际宽度的入场BTC折算价值），日期/七日区间均跨零；X2无合格主候选。没有候选通过，不能称弱优势或按失败结果改阈值再称独立通过。完成程序和报告不等于已找到显著有效的交易方案。

长期主流量为合约30/240分钟，生产较长CVD来自现货成交量柱，来源/窗口不同。缺历史期权墙位，不能用滚动价格区间冒充Anchor或据此宣布完整中性回路无效。保留条件X下Put、另一些条件下Call的研究自由，不设统一Delta、深虚值或A/S门槛。

本次补齐历史prints/mark经济描述，四腿15分钟440/2643机会、60分钟1799/2643；均非同步可成交报价，缺失与负信用保留。下次最有信息量的补充是原生空间事实与实际考虑两腿净信用的连接；不靠继续增加评级、理想条件或模型调用挽救旧样本。数学比较为取得净信用差加条件赔付节省减额外成本。

胜率提高与经济价值提高分别检验；风险进入价格不等于卖方真实期望必为零，也不能据本次失败认定市场充分计价。建议下一项复用卡片与候选报价，做同到期两三组价差的只读对照，衡量增加空间所放弃的信用；不先建设连续贝叶斯状态机。周末55小时与普通轮分开。以上为建议／待验证，不是已采纳交易条件。

原始研究表、六份旧PDF与FMZ1.6.2/生产消费者保持原样。局部条件仍是研究/询价情景，尚无新生产规则。

## 历史验证规格：检验局部条件的可复用增量

用户已授权验证研究29的局部定式，并允许扩展历史资料。见[研究30](docs/astra/30_局部定式长期验证规格.md)。保持有保护腿的垂直信用价差基座；允许条件X卖Put、另一些条件卖Call，不要求统一全市场规则、固定Delta或A/S门槛。

长期验证按交割日划分：2024训练、2025上半年只选择一次、2025年7月至2026年5月锁定检验；旧176张档案因已经反复查看，只用于原模型关联核对，不再冒称未见样本。只匹配入场前可得事实，不补造过去墙位。

主量分开条件赔付节省、近时成交/mark经济描述及真实可成交收益；同样期限、波幅、行权价距离和实际宽度的控制样本用于识别局部条件是否提供额外信息。无匹配、无成交和未选机会全部保留。程序和报告通过不等于定式已证明；未通过的候选不得靠反复改阈值挽救同一锁定测试。生产FMZ、D–S、LLM及运行任务不变。

## 历史研究29：保留交易基座，寻找有边界的局部方案

[研究29](docs/astra/29_局部最优定式与最小闭环推导.md)继续停在理论推导层。允许条件组X下卖Put、另一组下卖Call或有限处置，不要求全市场统一规则。优先研究“单侧承接”与“压力继续传导但反向尾部受限”两分支，双侧收缩和失效后对冲另作条件分支。D–S不映射胜率，不设固定Delta、不强制远虚值，不启动持续贝叶斯或新工程。

局部改变的统一判据是净信用变化减条件预期赔付变化和额外成本；同宽外移短腿点态不增加赔付，但最深尾部仍为W/S，不能称消除了尾险。可以先推导允许少收的信用边界；没有报价不应成为拒绝理论分析的硬门。对冲均值增量也不依赖原信用，但组合胜率、增长和资本占用仍依赖完整交易。

隔离诊断表明Put/Call不能池化裁决：固定触腿对冲在本批Put负增量、Call正增量；Call相对直接Put的优势依赖少数日期，不能改成全量Call加对冲。墙位候选的赔付减少仍包含纯几何远移效应，未证明墙独立贡献。历史档案被多轮查看，局部定式是待验证候选，不是假装样本外或已证明未来最优。原卡、此前PDF、FMZ与生产消费者不变。

## 历史研究28：数学赔付与空间候选连接

[研究28](docs/astra/28_数学赔付与信号增量研究.md)以 C>E[X] 与归一化尾部赔付为统一尺度。保留Flip/effective Anchor和NR修复事件，但它们不是已估计的回复力或已证明的到期胜率。现阶段优先连接真实考虑的两腿、到期和净补偿与空间位置，不增加方向权重、D–S半级或持续贝叶斯任务；不设统一Delta或强制腿距离。

原Flip位置选侧86张，胜率84.88%、情景保证金回报3.93%，同卡固定Put86.05%、3.94%；不能据此改成永久固定Put，也不能宣称Flip独立有效。量价/可读主动流方向与28日经验分布没有稳定超越朴素基准；D–S仍服务审计解释，不能当已校准交易过滤。普通轮114张8—24h与周末12轮分开，所有结果仍是假设净补偿而非真实收益。

关键校对：22笔Put、8笔Call全胜均为卖出腿**等于墙位**，严格墙外0笔；1张默认std锚带移入未知，不抹掉其轴位置；可读主动流包含EDB非投票数据。修正保留初算/封存证据，不在看过结果后伪称全新预注册。下一项结构假设须有同期限/距离/补偿对照，不能把小样本全胜升级为规则。旧生产文件、历史卡/哈希、旧PDF与LLM额度不改。

## 历史研究27：同时双卖

[研究27](docs/astra/27_同时双卖对照与组合效用.md)将同刻Put与Call信用价差合为一笔，保护腿保留，胜率按总净盈亏判定；两侧胜率平均不是双卖胜率。主情景每侧宽2000、假设净补偿10%、估算保证金两侧相加，非交易所PM实值。

全时钟1759/2709胜（64.93%）、回报+0.599%；信号78/114（68.42%）、+0.440%；同日/期限匹配对照65.57%、+0.413%，经济差仅+0.027个百分点且区间跨零。普通轮尚无稳定“等信号双卖”增量；加入Call稀释本批Put较好表现。周末11/12胜、+5.31%，是固定周六入场的结构/行情/补偿组合迹象，不能归给来源卡。

每侧补偿从10%减到5%后，2000宽全时钟回报约−4.66%，周末仅+0.06%。中性48张双卖胜率68.75%但净回报−0.24%，不把中性直接翻成双卖。下一项优先是少量真实四腿净收入和新增保证金；暂不增指标、细分等级或生产策略。独立研究不更改历史卡、PDF、FMZ或运行任务。

研究侵入口径已按用户要求改为到期净赔付超过净权利金，恰好平衡单列；原短腿行权价侵入指标保留为历史口径。研究补偿比例与实际保证金回报分母不同，不用单笔案例替代全部历史报价。见[PDF口径与交付24](docs/astra/24_盈亏平衡侵入口径与PDF研究交付.md)。

## 历史研究26：无信号对照与结构基准

[研究26](docs/astra/26_无信号对照与结构基准.md)已补上固定时钟基准：沿用114普通信号范围，每30分钟、8小时＜DTE≤24小时，2709入场/86交割日；同交割日及2小时期限箱匹配114/114信号，实际429个控制入场。没有新行情或LLM请求，旧卡、两份旧PDF、FMZ和生产配置保留。

主情景（2000目标宽/假设净补偿10%）：全时钟Put胜率74.12%、Call77.04%；信号85.96%/68.42%；匹配对照82.24%/73.03%。Put有小幅附加表现迹象，但估算保证金回报增量+0.84个百分点、区间跨零，网格偏移后仅+0.39；Call没有一致正向增量。高胜率本身不证明信号优势，也不能全归给策略：本轮基准仍含市场路径和补偿假设，不是因果拆分。

日期/期限选择可能本就是信号作用，匹配消除的差距不能直接判无效。原方向与B级尚未体现稳定独立经济增量；周末固定周六入场本来就是无信号择时基准，原总胜率不能归给来源卡。未来优先保持固定对照、检查Put增量稳定性与真实两腿净补偿/新增保证金，核对偏空至Call转译。暂不据此改权重、细分评级或加持续工程。

时段统一写明：美股交易时段21:30—次日04:00，美股收盘后04:00—08:00；不要再将“开盘后”和“收盘后”混称美盘后。以下研究25/23为各轮历史结论，其中“缺非信号对照”已由26补充为当前可复算基准，但Anchor单因素因果贡献和真实收益仍未识别。

## 二阶研究历史：时段、周末轮与评级效用

二阶研究已按用户授权完成，见[研究25](docs/astra/25_时段周末与评级效用二阶研究.md)。普通轮仅8小时＜期限≤24小时，114张；另有13个周六观察点、12个成熟周末，周末约55小时与普通轮分开。62张短期限卡只留排除记录，不重复展示收益。原176张资料与上一份PDF保留。

114张已独立使用当前标准离线复评：事实包2.1.1、Prompt2.2.1、输出2.2.0；先封存评级后连接收益。历史生产卡和旧LLM结果均未改写。后文“不重评旧LLM”属于第一轮研究及生产边界，不否决这次明确授权的隔离研究。

本批主情景（目标宽2000美元、假设净补偿10%）的稳定结论：美盘后21张Put胜率90.5%、Call81.0%，但同时是约8—12小时期限，不能分离时段效应；12个周末振幅均低于对应周二至周四，仍有中性Put 6／7盈利却整体净亏的反例。B级在普通轮有部分筛选迹象，Put 47／54盈利、Call 33／45盈利；Call B资金回报仍约−1.0%，且月份、方向和资料覆盖控制后优势不一致。本批没有A/S，不证明评级单调有效，不降低门槛或新增B+。

下一项优先保持低工程量：核对事实引用误写和两例否定句误拦；把美盘后与剩余期限一起保留为待验证条件，并记录少量实际两腿净补偿和新增保证金。最近OTM不一定在锚带或墙外，本次只检验评级与固定选腿方式的组合；不据此否定所有结构用法。没有非信号对照和真实历史报价，不能宣称空间锚独立增益或历史实际APR。

保证金仍是单例校准情景，λ≈0.951659，APR按资金时长计算，不是账户实绩。本轮不改FMZ、生产Prompt/校验器/配置，不自动恢复无效等级，不增加持续跟踪任务。

## 第一轮研究记录（保留为历史）

用户已暂停FMZ清理；下一阶段优先研究已有样本，不新增指标、评级细分或持续任务。研究到期为模拟入场后最近一次北京时间16:00，期限不超过24小时；不恢复已放弃的16—40小时口径。

176张事件的标准化、真实市场资料对齐和3168行情景计算已完成，入口[研究23](docs/astra/23_接管信号短期期权适配轻量研究.md)。根目录`.artifacts/astra-light-study-20260913/`保留原始快照、输入哈希、样本、结果、独立核验。固定轮排除，方向取原卡decision而非episode冲击；版本与旧LLM保留，不重新评级。

本批只支持局部用途迹象：偏多Put较好，偏空Call较弱，整体方向过滤不稳且未超过本批固定Put基准。缺少非信号对照和真实两腿报价，不宣称Anchor独立有效或真实可成交收益；5%／10%／20%净补偿仅为情景。下一项先核对偏空卡的上行侵入反例，不把历史最优分组自动变成生产规则。详见Astra项目研究记忆。

下文为此前生产记录，历史“待自然新卡”对应当时阶段；本次归档已含1.6.2真实事件，仍不能拿离线研究代替完整前向验收。

## 当前已发布：GEX 时间补丁，FMZ 1.6.2

用户已批准[补丁20](docs/astra/20_GEX时间语义补丁与FMZ1.6.2.md)。当前已发布版本区分市场观测、上游结果生成、值抓取及卡片记录时间；事实包2.1.1、Prompt2.2.1，LLM输出/摘要/投影仍2.2.0。原观测未知不补造，已知晚于卡时的来源不能引用，依赖限制逐项传播。旧卡及旧尝试额度按原协议保留。

本轮只修审计与来源说明，不改变评级定义、信号逻辑、触发、权限或模型调用频率。[A／S有限跟踪企划21](docs/astra/21_A与S级有限跟踪待研究企划.md)仅为建议／待研究；人工研究B级不受限制，不启用新任务。上一版本的发布确认不授权本地新页面发布；本轮用户已明确确认发布，服务器和用户可见1.6.2交付已同步；自然FMZ新卡仍待核验。以后页面变化仍先验收、确认再发布。自然新卡才证明FMZ原生前向链。

栏目归位采用同一事实的分层展示：空间点位在空间约束动力学，来源区保留完整数值与时点；TMV、净Gamma等补充变化通过独立只读投影1.1.0保存。补充行不改原事实包或评审、不触发LLM；比较需逐项通过来源、方法、单位、窗口及时间核验，前后值不冒充完整行情路径。

以下 v2.2 为上一已发布阶段的历史记录，不与本轮候选混用。

## 当前裁定：Astra v2.2

用户已批准并于2026-09-11明确要求发布[建议式辅助闭环](docs/astra/17_证据校正与建议式辅助闭环_v2.2.md)。`fc11787`已推送xxproject工作分支并部署；用户可见FMZ交付已同步1.6.1，实例运行与自然新卡的证据分别核对，见[回执18](docs/astra/18_v2.2发布与兼容验收.md)。事实、推理、建议、下一观察和新卡变化构成主路径；以下旧阶段中“等待上一版发布”“只有A/S才可人工研究”等表述保留为历史，不再决定当前建议。

- D–S表示总体证据，不是胜率或权限。B可给人工研究和准备建议；不设置统一Delta、行权价、距离或权利金门槛。实际报价缺失不阻塞市场分析，候选补偿保持未评估。
- 原信号窗口、硬风险和机器权限单独保留；建议不能开交易开关。结构优先，再解释不利压力、同窗响应、近端/长窗差异和竞争解释。
- GGR位置分类、看板净GEX及各结构位分源分时，不能互借新鲜度或重复证明。墙移远不自动表示保护增强。期权目标档位不是实际期限，M-DIE收益比例需正确转百分点。
- 本地FMZ候选1.6.1仅增加现有缓存的15/30分钟闭合柱摘要和同窗主动量，不新增请求或改旧投票。事实包2.1.0；LLM/Prompt/展示投影2.2.0；历史协议按原规则读取。
- 新卡正常一次综合评审；四小时与二十四小时条件情景并入同次结果，不恢复独立长报告或变化LLM。旧尝试额度跨Prompt和重启冻结，保留全部失败。
- 最高辅助区每块内容须增加判断信息：机制解释约束关系，等级依据解释强弱，反证列具体不利事实。条件情景提供事实、侧别影响与重判观察；四小时/二十四小时允许只给一项或都不给，不为占位输出通用提醒，不为改善文案重试模型。
- 原始卡、评审和哈希不改写。恢复的历史变化独立标注为本地核验。中性纸面和manifest惰性加载保留。
- 本轮页面已由用户“推送并更新”明确授权并完成发布；以后页面变更仍需新的发布确认。自然FMZ1.6.1新卡才证明生产前向链，不恢复Codex定时跟进。

稳定研究边界：单卡解释、事实差分、主张经受后续检验、候选交易经济性是不同层次；不能互相冒充。新评级沿用旧事件选样，不代表覆盖全部交易机会；抓取时间也不等于上游观测时间。近期建议先做GEX时间身份及必要消费说明的兼容纠偏；当前快照入口、持续主张追踪和候选连接暂留研究，不作为小版本或使用当前系统的前置条件。不得据此新增强制交易单、固定Delta或LLM调用。详见[目标完成度与闭环评估19](docs/astra/19_目标完成度与最小验证闭环评估.md)。

以下为先前版本的工程与研究记录，当前路线不从旧段落中重新选取。

> 2026-09-11稳定复核结论：固定轮与事件卡的记录用途不同，不能单独否决同版本市场事实比较；GGR由价格相对翻转位推断的体制，与看板净GEX必须分别保留来源、方法和实际时点，不能互相借用新鲜度或重复证明。下一版建议优先修正上述事实口径，再关联拟交易期限、候选与条件情景；尚未实施，不按少数卡的等级分布调门槛。详见[Astra运行复核](docs/astra/16_运行复核与最小交易闭环建议.md)。

> 2026-09-10：当前已发布 Astra v2.1 固定适配命题、证据角色、同次两侧比较及阅读投影，见[方案14](docs/astra/14_评级命题校正与两侧比较_v2.1.md)。用户于2026-09-10审阅本地8879页面后明确要求“推送并完成更新”，本轮已完成推送与部署；历史评审/额度保留，自然2.1链路已核验，事实口径和语义缺口见运行复核16；执行结果见[发布回执15](docs/astra/15_v2.1发布与兼容验收.md)。输出/Prompt/投影2.1.0，事实包2.0.0，FMZ1.6.0不变。历史卡原协议读取、不重评；旧Prompt未完成尝试冻结，不能升级重获额度。模型自然语言通过格式与引用检查不等于因果结论已经证真。以下 v2 部署记录保留为历史事实。

> 2026-09-09晚间最新核验：23:00自然固定轮卡已完成FMZ1.6.0原生证据→单次v2评审→正式页面验收，27项服务器自检通过、0项失败。Put B、Call C、价格多空分歧；时序窗口未开，两侧仍不能进入人工准备。空间对称与承接措辞保留解释局限，详见[首张原生卡验收](docs/astra/12_首张原生固定轮卡验收.md)。Codex定时跟进已取消，后续由用户通知新卡；正常服务器timer继续运行。

## 历史 Astra 路线入口（v2阶段）

当前目标与裁定以[Astra项目记忆](docs/astra/PROJECT_MEMORY.md)、[总体证据评级与单次综合评审v2](docs/astra/07_总体证据评级与单次综合评审_v2.md)为准。后续更新先读[版本兼容与数据复用约定](docs/astra/10_版本兼容与数据复用约定.md)。

工程开发基线是xxproject的codex/astra-signal-rating-v1完整资产分支。新卡使用一次DeepSeek综合评审；B/A/S表达总体证据强弱；证据字母与人工准备边界分离。原WAIT/BLOCK/NO_TRADE、窗口与侧别限制仍有效，不新建执行权。

producer候选1.6.0、原生四态1.0.0、LLM输出2.0.0、Prompt2.0.1和页面20260908-astra-paperline分别记版。用户已审阅定稿中性纸面，并于2026-09-09明确授权推送当前版本；不合并main。用户随后授权服务器部署，审计服务已部署5edb288；FMZ1.6.0已由自然固定轮原生卡核验。随后页面改动仍要重新取得可推送确认。

升级保留原始审计、sidecar、持久尝试及响应、预算和变化状态；历史卡不自动重评，新协议不能被旧评审静默覆盖，未知协议显式未评级。代码安装保护整个运行时signal_cards目录，不依赖索引存在。Prompt或输入包修改不能作为清除旧请求额度的理由。

可见根目录与隔离工作树是不同开发面，按本次同步清单核对文件，不能把同步一个FMZ候选推定成整个根目录源码已更新。首张自然固定轮1.6.0已通过完整链路验收，详细证据见Astra文档12；此前隔离API与自然记录分开保存。历史自动排除清单与sidecar/额度必须一起迁移，详见[部署回执](docs/astra/11_服务器部署与FMZ同步说明.md)。以下为早期工程快照，其中旧版本、Gemini流程和旧展示习惯不作为当前Astra依据。

## Project overview

- This repository is the integration workspace for the neutral-loop trading system. It gathers system-level design docs, module snapshots, demo integration code, latest FMZ-ready single-file deliverables, and the signal audit archive sample.
- The main implementation language present in the deliverables is Python. The repository also contains Markdown documentation plus a static HTML/JSON audit archive sample under `audit_archive/`.
- The current local signal-layer candidate is `demo/最新交付物/neutral_regulation_demo_fmz.py`. Its verified in-file candidate version is `demo_version = "1.6.0"` and `schema_version = "nrd.schema.v1.0.0"`. It preserves the v1.5.6 runtime/delivery protections, adds CVD weak-edge joint activation, emits producer-native `canonical_funding_semantics` as the sole Funding interpretation, and generates one audit-only fixed analysis round from the Beijing 23:00 snapshot. The fixed round bypasses only the Anchor+DIE card-emission trigger, never fabricates `NR_REPAIR_CONFIRMED`, and reuses the normal JSONL, single v2 DeepSeek assessment, materializer, and single integrated-advisory frontend path.
- The current deployable execution-layer artifact is `demo/最新交付物/spm_manual_gate_execution_fmz.py`. Its verified in-file version is `STRATEGY_VERSION = "3.0.0-manual-gate"`, status `MANUAL_GATE_PLAN_READY`, with entry/exit/hedge/live trading gates still default-safe/off and `DRY_RUN_PASSED = False`.
- The current GEX Monitor API snapshot is `05_GEX监控API_数据增强接口/`, with `__version__ = "0.2.1"` and rank output using `rolling_30d_or_available`; `window_days>=15` is directly `quality=ok`, while the percentile population remains capped to the most recent 30 days.
- The current local v2 main path uses tools/signal_evidence_v2.py, tools/signal_review_v2.py and tools/signal_review_v2_runtime.py, dispatched by tools/signal_llm_review.py / signal_llm_review_entry.py. Default single_evidence_v2 uses DeepSeek deepseek-v4-flash, one high-reasoning call, schema 2.1.0 / prompt 2.1.0, persistent two-HTTP total budget. Historical schema 2.0.0 / prompt 2.0.0 and 2.0.1 stay readable without rerunning. Legacy 1.x helpers remain explicit offline/history only; they are not current new-card output contracts.
- Historical transition LLM sidecars remain readable. New automatic cards use locally verified transition facts in the same v2 call; no independent transition LLM or new 24-hour long report is scheduled.
- The remote full-project backup branch for the r3.3.5 asset state is `backup/xxproject-r3.3.5-fulltree-20260705` on `xxproject`; r3.3.6 durability work should be layered on top of that full-tree baseline or another explicitly justified full-project baseline.
- Documentation r2.2 aligns `05_GEX监控API_数据增强接口/` and `deploy/signal_audit/` with the 00-04 module convention by adding `因子文档/` indexes, Chinese semantic entrypoints, and `deploy/signal_audit/frontend/VERSION.json`; it does not move service source code or change runtime behavior.
- `demo/最新交付物/README.md` states that `demo/最新交付物/` contains the latest FMZ-ready single-file strategies, while `demo/副本快照/` is the historical timeline.
- `x18055868223-png/xxproject` is the primary project repository and default authority for project baseline, releases, tags, and server deployment instructions. `signal-audit-deploy` is only a deployment/helper mirror for the static audit surface; do not treat it as the project main repository.
- Before any commit, tag, push, or deployment instruction, verify `git remote -v`. If `origin` points to `signal-audit-deploy`, use/add the `xxproject` remote for project-level releases and do not conclude the project baseline is updated from a deployment-mirror push alone.

## Astra rating implementation boundary

- Current authoritative local implementation route is docs/astra/07_总体证据评级与单次综合评审_v2.md, on the pushed codex/astra-signal-rating-v1 worktree rooted at full asset commit 2c72162. Preceding v1 comfort tests and discussions are historical evidence.
- Producer candidate stays 1.6.0 with native signal_rating@1.0.0; the v2 LLM schema 2.0.0 / prompt 2.0.1 are separate version axes. Existing producer direction/confidence/NR/trigger/permission consumers remain unchanged.
- B/A/S mean overall evidence strength, not ideal-market checklists or probabilities. WAIT/BLOCK/NO_TRADE/window/side boundaries constrain locally derived preparation, not evidence letters. Read-only execution false does not lower market evidence.
- New fact inputs exclude old confidence/durability totals, legacy anchor gravity scores, prior conclusions and full GEX rankings. Anchor gravity is a derived fit score, not source quality; use verified anchor position/band and price response instead. Underlying raw archives and machine-used CVD ranks remain; derived facts retain source dependencies.
- Macro score polarity is positive=headwind, negative=tailwind for risk assets; do not read its sign as a price-direction score. Same-source or derived descriptions do not add independent votes. A missing source timestamp is not silently replaced by a claimed collection time.
- All failed canaries must remain recorded. A code/packet repair does not turn its earlier failures into passes. Frontend release still requires the user's exact 当前本地页面可推送 approval; no current Git/FMZ/deployment acceptance is implied by local work.

## Architecture and boundaries

- The root documentation presents the system as core areas `01_信号层_中性回路/`, `02_执行层_Deribit/`, `03_VRP门_建仓前定价/`, `04_对冲模块/`, plus the current auxiliary running assets `05_GEX监控API_数据增强接口/` and `deploy/signal_audit/`, with `demo/` as the integration sandbox.
- The signal-layer FMZ file is read-only observation by default. It does not select legs, quote, or place orders.
- Signal v1.5.7 inherits the full v1.5.6 JSON/session/durability/runtime protections. Its new mechanical facts do not recompute producer direction, confidence, blocking, trade permission, or execution permission: weak CVD requires both CVD and price-confirmation sides to be active and uses `min(cvd_strength, price_confirm)`; Funding raw `abs(rate)<=0.0001` is non-crowded, reflexivity noise, and non-voting; missing raw Funding is `UNABLE_TO_JUDGE`.
- Current signal audit JSON output is aligned to the finalized static frontend card schema used by the external archive `信号审计前端页面设计/archives/signal-audit-final-20260618`. HTTP mode renders at most the latest 15 deterministic manifest summaries, lazy-loads the selected card, prefetches two recent details with per-card failure isolation and memory caching, and never eagerly loads `fallback.js`; direct file mode dynamically loads `fallback.js` only when needed.
- New v2 cards use one reader path: local action conclusion, side evidence ratings, Chinese market facts and next observations. Legacy scores/conclusions, transition panels and 24-hour reports are not rendered on v2 cards. Machine details remain downloadable; historical 1.x cards use a separate reading path. Production materialization excludes synthetic/local preview cards.
- `audit_archive/` in this repo is an older sample/archive scaffold, not the current finalized frontend. Do not treat `audit_archive/public/index.html` as the authoritative audit page.
- The execution-layer FMZ file is a vertical credit spread execution chain with a single `run_cycle` main path. Its configured signal source default is `OFFLINE_MANUAL`.
- Execution trading gates are default-safe: `ALLOW_ENTRY_TRADING`, `ALLOW_EXIT_TRADING`, `ALLOW_HEDGE_TRADING`, `KILL_NEW_RISK`, `EMERGENCY_REDUCE_ONLY`, and legacy `ALLOW_TRADING` are all verified as `False` in the current deployable execution file.
- VRP is documented and coded as a pricing/filtering gate. It must not decide direction, select expiry, enter plan weights, or unlock trading gates.

## Build, test and validation commands

- Verified during initialization: syntax compilation of the current deployable FMZ files works with Python 3.12:

```powershell
<python-3.12> -m py_compile `
  demo\最新交付物\neutral_regulation_demo_fmz.py `
  demo\最新交付物\spm_calendar_protected_short_v1.py
```

- Verified during initialization: `.codex/config.toml` and `.codex/agents/*.toml` can be parsed by Python `tomllib`.
- Verified during initialization: `audit_archive/public` can be served as static files over local HTTP, and both `/index.html` and `/data/index.json` return HTTP 200.
- Verified during 2026-06-18 signal audit alignment: `demo/tests/test_signal_audit_frontend_contract.py` passes, and `demo/最新交付物/neutral_regulation_demo_fmz.py` compiles with Python 3.12.
- Verified during 2026-06-23 signal-audit r3.2 closure: `tests/test_materializer_tail_window.py`, `tests/test_signal_session_context_deploy_assets.py`, `tests/test_signal_audit_frontend_render_contract.py`, `tests/test_signal_audit_deploy_llm_systemd.py`, `tests/test_signal_llm_review_pipeline.py`, `demo/tests/test_gemini_signal_llm_review.py`, `demo/tests/test_signal_audit_frontend_llm_review_contract.py`, `demo/tests/test_signal_session_context_contract.py`, `demo/tests/test_signal_llm_review_contract.py`, and `demo/tests/test_signal_blocking_and_anchor_contract.py` pass with Python 3.12.
- Verified during 2026-07-05 r3.3.6 local durability work: producer, materializer, deploy-asset, frontend, LLM/transition ordering, and server-bootstrap tests are the required local release gate with Python 3.12. New-server signal-stack bootstrap lives at `tools/server_bootstrap_signal_stack.sh`, with migration guidance in `deploy/signal_audit/SERVER_MIGRATION.md`; deployment acceptance must verify the latest real card has the current FMZ `identity.strategy_version`, native session context, native transition anchor/context, producer-native `factor_cross_section.macro_pressure.macro_shock`, and producer-native `signal_durability` with `DURABILITY_REQUIRED=1`.
- Documented in `demo/最新交付物/README.md` but not re-run during this initialization: execution-layer full regression command `python demo/execution_build/realsrc/tests/run_all.py`.
- Documented in `demo/最新交付物/README.md` but not re-run during this initialization: execution bundle check command `python demo/execution_build/realsrc/build_bundle.py --check`.
- Documented in `demo/HANDOFF.md` but not re-run during this initialization: signal bundle check command `python demo/signal_build/build_signal_bundle.py --check`.
- No `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle`, `requirements*.txt`, `tox.ini`, `pytest.ini`, `mypy.ini`, or `.flake8` file was found at this initialization scan.

## Coding conventions

- Keep deployable FMZ files in `demo/最新交付物/`. Historical copies belong in `demo/副本快照/` with a dated version folder and `快照说明.md`.
- Prefer repository-relative paths in documentation and project memory. Do not write secrets or local machine sensitive paths into project memory.
- For signal audit data, keep machine-readable raw values in JSON. Presentation layers may translate labels, but should not overwrite raw enum or numeric values.
- For static audit data, use an index JSON for list/filter fields and per-card JSON files for detail. The finalized frontend expects `signal_cards/index.json` plus `signal_cards/*.json`; do not require browsers to parse the full JSONL on every page load.
- Any future change to the signal audit frontend must include a full frontend audit before delivery: verify desktop/mobile reading order, horizontal overflow, market values/units/times/quality, Chinese evidence accessibility and source integrity. No raw paths, enums, schema/hash, formulas, weights, old confidence/durability totals or implementation details may appear in any reading-page state, expanded content, tooltip, accessible text or errors. Complete machine data belongs in a downloadable audit package, not hidden DOM. Keep deploy and preview assets mirrored where applicable and run the relevant frontend/materializer checks.
- New v2 reading order is one local action conclusion, two side evidence grades, Chinese market facts and next observations. Old direction/support/score summaries and duplicated top conclusions exit the new-card reader; historical cards retain a separate reader and do not enter v2 grade filters.
- 本地前端页面人工确认前不得推送：任何 signal audit frontend 版本更新，都必须先在本地页面完成代表性/模拟数据审计、真实或等价 LLM sidecar 检查、浏览器桌面与移动布局检查，并由用户明确确认“当前本地页面可推送”后，才允许 commit/push/tag/deploy。
- Signal audit 页面遵循“重点清晰、逻辑贯通、关键内容全面”：用户本轮已授权按舒适度阅读顺序收束；市场事实用中文保留，机器字段只放在完整审计资料下载中，不能继续放进普通页面的低层展开区域。
- 状态转移审计主阅读流只保留 LLM 综合解释、状态路径、关键变化骨架和审计元数据；低层 raw delta、最近 5 次轨迹、24h baseline、episode anchor 不再作为前端主展示区块出现，但必须继续保留在 materialized JSON、trajectory 文件、ledger/state 或 raw trace 可复用数据中。
- Any future change to signal audit schema or release packaging must include an end-to-end asset coherence audit: verify the git release/tag, `demo/最新交付物/neutral_regulation_demo_fmz.py` `demo_version`, server latest real card `identity.strategy_version`, and latest card schema fields all agree. For time-zone/premise durability, the latest real card must expose native `SignalSessionPremiseDurabilityContext`, `clock_window`, `backtest_delta_pp`, structured `validation_basis`, and `decision_matrix.temporal_durability`; use `SESSION_CONTEXT_REQUIRED=1` in server self-check during deployment acceptance, and do not accept latest-card `compat_backfill_applied=true` as producer proof.
- Any future FMZ signal-body strategy update must update both the user-visible local latest artifact at `demo/最新交付物/neutral_regulation_demo_fmz.py` and the corresponding pushed `xxproject` branch/tag artifact before claiming delivery complete. If an isolated worktree is used, synchronize the root-visible latest artifact before final delivery, or explicitly state that the visible root remains stale and must not be copied to FMZ.
- Keep signal-layer changes in the observability and signal-evidence boundary unless the user explicitly reopens signal logic, EDB weights, or direction classification.
- Keep execution-layer live trading disabled by default unless the user explicitly asks to change gates and the required live validation has been completed.

## Important invariants

- Signal audit v1.5.7 remains read-only/observability-bounded: `decision.trade_allowed` and `decision_matrix.execution_allowed` stay false in read-only mode while `model_trade_support` remains separately auditable. Funding/CVD/GEX semantic fixes must not touch execution-layer trading switches or change the NeutralRepair business threshold.
- FMZ push is an alert/navigation channel only. Full audit evidence belongs in JSONL and the static audit page.
- FMZ push text must remain a single short message. Prior versions were truncated by FMZ/email clients, so long four-layer audit text must not be restored. In the current signal file, the old `render_review_card_push` / `signal_review_push_compact` path has been removed.
- `signal_review_push_enabled` and `signal_review_push_test` are verified as `False` by default. Push testing must be explicitly enabled and then turned off after verification. When enabled, the current self-test writes one synthetic audit record to `signal_review.jsonl` and pushes one `非真实信号` short alert with a JSON write status marker.
- Synthetic/local preview signal audit cards must not be present in the default deploy manifest, fallback fixture, or inline `signal-data` fallback. Use the materializer's explicit include-synthetic preview mode only for local visual testing.
- Static audit site URL configuration is optional. If `audit_static_base_url` is empty, push summaries should point to FMZ log/card references rather than pretending the static site is deployed.
- Execution gates default to dry-run/empty trading behavior. Do not flip trading gates as part of unrelated refactors.
- Execution `GetCommand` interaction requires real FMZ robot dry-run validation; backtest behavior is not enough for that path.
- The current `audit_archive/` data is synthetic sample data, not proof of production signal ingestion.

## Known pitfalls

- The workspace root can now be a Git repository; always verify the active remote. The safe default is `xxproject` for project work. `signal-audit-deploy` must be treated as a deploy mirror only, and project releases should preserve the full xxproject asset tree rather than force-moving main from the deploy mirror history.
- `python` was not available on `PATH` in this environment during initialization. Use an available Python 3.12 interpreter explicitly when needed.
- `audit_archive/public/index.html` is a placeholder from the older scaffold. The current finalized static audit page lives outside this repo in the `signal-audit-final-20260618` archive and expects the `signal_cards/` layout.
- Runtime signal records are not automatically exported by the FMZ strategy itself. The runtime writes `demo/logs/signal_review.jsonl`; `tools/materialize_signal_cards.py` materializes that JSONL into `signal_cards/index.json`, single-card JSON, and `fallback.js` for the finalized static page. Server automation is provided as an optional systemd timer under `deploy/signal_audit/`.
- New-server rebuilds should start from `xxproject` release tags using `tools/server_bootstrap_signal_stack.sh`; for the current migration asset use the r3.3.1 release tag once published. The script creates env templates only; `/etc/signal-audit/llm.env`, `/etc/gexmonitorapi.env`, FMZ runtime JSONL, and historical sidecar JSONL remain server-local and must not be committed.
- Missing current ratings must be diagnosed from the fixed packet, local validation, durable attempt ledger and materialized summary hash before another call. New strict acceptance uses signal_llm_review@2.0.0 / signal_llm_review_prompt@2.0.0; old 24h/two-call checks are legacy-only. Never conflate local/API PASS with FMZ/deployed acceptance.
- Worktree releases can leave the user-visible root `demo/最新交付物/` stale even after `xxproject` is pushed. After worktree-based releases, explicitly synchronize or verify the root-visible FMZ artifact and report the path the user should copy into FMZ.
- `demo/副本快照/2026-06-18_信号v1.3.0_执行v1.6.2_JSON留档+简要推送/` contains an execution file whose content verifies as `STRATEGY_VERSION = "2.0.0"` despite the folder/file naming saying v1.6.2.
- The old validation scripts documented in upstream/source repositories may not exist in this integration checkout. Verify actual file presence before running a documented command.

## Durable decisions

- Decision: main-card LLM completion follows one core-owned sequence: deterministic blind completeness normalization, full safety validation, at most one same-cycle reconciliation recovery, then single-writer publication. Only missing `boundary_cn` and `counter_evidence` are locally completable with fixed non-factual audit text; every such repair is recorded in `blind_completeness_repair` and caps the final recommendation at `NO_TRADE`. Invalid direction, basis, drivers, Gamma regime, packet facts, JSON, execution language, authorization, auth/config, and daily-cap failures remain fail-closed. Canary transition checks are scoped to the target card's ledger transition IDs, while frontend degradation may hide only an unsafe low-level key-level row and must still hide the whole future report for unsafe summary or policy content. Impact: harmless shape omissions and unrelated historical transition errors no longer starve a valid new card, but semantic or safety defects cannot be promoted.

- Decision: all future signal-audit feature iterations, pushes, and server updates must follow `docs/信号审计更新部署规范_v1.0.md` and maintain that specification together with `PROJECT_MEMORY.md`. The required sequence is baseline recovery, local tests, isolated real-API canary, local desktop/mobile page review plus the explicit frontend push gate, immutable-SHA push, safe-default server install, server canary/strict self-check, timer enablement, and external-page verification. DeepSeek empty content receives at most one reconciliation recovery; schema/semantic failures may only use bounded auditable repairs that still pass the full validator, never repeated blind API retries. Impact: production is not a debugging loop, timers remain off on failed acceptance, runtime sidecars/ledger/keys are preserved across installs, and every release records evidence separately for code, push, deployment, real invocation, and rendered-card success.

- Decision: keep `demo/最新交付物/` as the current deployable FMZ artifact folder. Basis: `demo/最新交付物/README.md`. Impact: update this folder after regeneration and preserve dated history in `demo/副本快照/`.
- Decision: signal v1.4.1 closed the time-zone/premise durability schema gap by making the FMZ producer emit full `SignalSessionPremiseDurabilityContext` fields and by allowing materializer legacy backfill only with explicit `compat_backfill_applied=true`. Basis: 2026-06-24 r3.2.1 incident review where server r3.2 was deployed but latest real cards still carried old `signal_session_context@1.0.0` fields. Impact: future schema work must test producer output, materializer compatibility, frontend rendering, and server latest-card self-check together.
- Decision: signal v1.5.2 adds an AUDIT_ONLY durability layer without changing direction, confidence, blocking, execution permission, or trading gates. `signal_durability.headline_score/headline_state` mirrors the first-version `price_anchor_durability.durability_score/durability_state`; `comfort_window` and `temporal_session` are display context and do not feed price-anchor scoring. Impact: future durability changes must include producer-level tests and must not accept materializer `compat_backfill_applied=true` as producer proof.
- Decision: signal v1.5.3 fixes NeutralRepair timing confirmation by requiring a DIE threshold episode, an actual Anchor below-60 handoff observation, and two Anchor >=60 repair ticks; M-DIE no longer has to cool below the old 0.42 threshold, but active displacement at or above the 0.65 event threshold still blocks confirmation. ND/deviation-only evidence remains audit evidence: it cannot confirm this return-to-60 signal by itself, and it does not hard-block confirmation after the below-60 handoff and Anchor repair are present. Impact: future NR fixes must preserve the below-60 handoff guard, duplicate episode suppression, read-only signal boundary, and execution-gate isolation.
- Decision: signal v1.5.4 fixes Funding/GEX durability-card semantics. Funding raw `abs(last_rate) <= 0.0001` is Binance baseline/temperate and cannot be described as crowded or become `FUNDING_HARD_WARNING`; hard warning requires strict raw threshold excess plus strict extreme normalized/history evidence. GEX/Gamma display must prefer `factor_cross_section.gex_info.net_gamma_notional_usd` or `total_net_gex` over tiny `gamma_regime` proxy metrics; proxy values must not be labeled as USD notional. Impact: future funding/GEX changes must preserve producer, materializer, and frontend source-precedence tests and must not touch execution gates, confidence, or EDB weights without explicit scope.
- Decision: signal v1.5.5 closes NeutralRepair runtime signal-loss paths without relaxing the intended timing gate. An unconfirmed context now expires terminally instead of stale-locking; transient M-DIE/Anchor data loss freezes the real below-60 handoff but resets confirmation continuity; active displacement resets the two-tick repair streak; and a confirmed opposite reset may carry an unconfirmed real below-60 handoff after Anchor has reclaimed 60 for the remainder of the original context TTL. Repeated opposite resets preserve explicit origin provenance and cannot extend that TTL; the legacy `[55,60)` subrepair carry keeps its five-minute bound. Impact: future NR changes must retain the real below-60 requirement, the `abs(M-DIE) >= 0.65` active blocker, two fresh repair ticks, same-direction gap isolation, ND-only rejection, and one-card-per-episode behavior.
- Decision: signal v1.5.6 makes each live evaluation consume only evidence proven current in that attempt. Failed or stale M-DIE/TMV-F/Funding refreshes clear the active compute input while retaining last-good diagnostics; catch-up trade backlog invalidates Anchor until drain completion; current price and premium facts reset each live tick; LKGV GEX info is display-only; Deribit option polling has a bounded failure budget; CVD/RR history advances only on new source epochs; and signal JSON/push delivery retries in order without duplicate JSON writes. JSONL append now loops through short writes, calls `fsync`, isolates a truncated previous tail, and withholds push until persistence succeeds; server materialization must use `--require-valid-source-tail` from service, bootstrap, and install paths so corrupt, empty, missing, or undecodable latest source data fails before replacing the last good public output. Impact: future runtime changes must keep these fail-closed boundaries while preserving expected NeutralRepair emissions after two fresh repair ticks; do not use blind oneshot restart loops as a substitute for source integrity failure visibility.
- Decision: signal v1.5.7 makes mechanical evidence semantics single-source and fail-closed. Funding is classified only from raw decimal rate with an inclusive ±0.01% non-crowded boundary; normalized/history/effect fields are diagnostic and cannot override raw or fill missing raw. CVD price confirmation cannot activate an otherwise weak CVD side, and its magnitude is the weaker edge. GEX Rank becomes robust/usable at 15 covered days while retaining a 30-day rolling population. Historical materializer repair must mark `compat_backfill_applied=true` plus a source; producer-native output must mark it false. Impact: frontend and LLM must consume these facts, not independently reinterpret them, and future tests must prove direction/confidence/blocking/trade/execution fields remain unchanged.
- Decision: signal v1.5.1 separates MACRO direction background from MACRO shock blocking. `macro_score/macro_regime` remain direction/background evidence, `macro_shock.block` is the only MACRO hard-veto source, old score-level blocking is retained only in `legacy_blocking_flags`, and previous MACRO state is persisted as the minimal `nrd_macro_previous_snapshot_v1` summary in the existing macro cache. Impact: future MACRO changes must preserve server-accumulated Rank/GEX/raw trace data and must not reintroduce score-threshold hard blocking without an explicit versioned calibration.
- Decision: current static frontend deployment should target the `signal_cards/index.json` plus `signal_cards/*.json` contract from `signal-audit-final-20260618`; the repo `audit_archive/` scaffold is sample/reference only. Impact: export tools should generate the finalized frontend layout, including a stable missing-source representation for optional GEX data.
- Decision: execution v2.5.0 is vertical-only and default dry-run with separated gates plus the v2.5.0 risk-chain audit fixes. Basis: current execution file constants, `demo/最新交付物/README.md`, and `docs/执行层完整说明_v2.1.md`. Impact: do not treat disabled gates as a bug, and do not reintroduce calendar or KPF execution paths.
- Decision: project-level complex tasks should use bounded subagent delegation through `.codex/agents/` when the task has independent exploration, implementation, or review streams. Basis: root `AGENTS.md` created during this initialization. Impact: future complex work should read `PROJECT_MEMORY.md`, classify complexity, delegate where useful, and verify final integration.
- Decision: signal-audit manual retry starts a new record retry epoch; older ordinary terminal records cannot poison the new epoch, while fatal auth/config and exhausted reconciliation recovery remain sticky. Human-facing advisory text must contain natural Chinese rather than field paths, `KEY=VALUE`, booleans, or enum strings. Missing containment/premium assessments may only be completed as `UNABLE_TO_JUDGE`, and packet price-to-wall distances are calculated locally as non-directional spatial context. Basis: DeepSeek production failures where older terminal rows suppressed newer cards and highest advisory leaked raw decision fields. Impact: future LLM releases must test retry-epoch boundaries, cached-blind reconciliation recovery, fail-closed assessment completion, raw-field rejection, deterministic wall-distance arithmetic, and newest-card rendering before enabling timers.
- Decision: server canaries seed their isolated main and transition sidecars from production history, remove only the target's stale main OK rows, then open an explicit retry epoch for the exact target. Basis: an empty canary sidecar repeated a validated blind call and completed transition, while an unfiltered copy could let the runner skip the target and mislabel an old OK as the current canary. Impact: canary stays isolated, retains failure/blind recovery history, reuses completed transition work, requires a newly generated target result, minimizes HTTP usage, and promotes only after canonical validation.
- Decision: the bounded entrypoint canonicalizes the two advisory assessment objects to exactly `state/basis_cn`: recognized states and supplied basis text survive, harmless extra keys are dropped, missing content becomes `UNABLE_TO_JUDGE`, and explicit invalid states remain validator-visible. Basis: DeepSeek reconciliation recovery returned useful advisory content with a noncanonical containment object. Impact: harmless JSON shape drift no longer consumes another blind call or leaks to production, while semantic invention and invalid enums remain fail-closed.
- Decision: session and comfort-window evidence cannot support core advisory premises. The bounded entrypoint removes only `EV_SESSION_CONTEXT/EV_COMFORT_WINDOW` refs, drops premises left without evidence, and refuses to repair when every premise becomes empty. Basis: a DeepSeek recovery mixed session context into otherwise valid core premises. Impact: unsupported evidence is monotonically removed, never replaced or invented, and the core validator remains the final authority.
- Decision: production signal-audit materialization defaults to the latest 15 cards in the service, installer, bootstrap override, and materializer CLI. HTTP startup loads one manifest plus the selected card and two bounded prefetches; one detail failure must not erase the list or other cached cards. Impact: bootstrap-managed systemd drop-ins must be refreshed with `MAX_CARDS=15`, because an older drop-in can override an updated base unit.
- Decision: chat response parsing may recover `Extra data` only when the complete response consists of two or more semantically identical JSON objects. Different objects, arrays/scalars, trailing prose, ordinary malformed JSON, or any output that fails the full validator remain fail-closed. Basis: a real DeepSeek reconciliation duplicated one complete object after an otherwise valid response. Impact: parsing compatibility cannot become a general first-object truncation path.

- Astra v2.0 workbench numeric presentation keeps producer measurement semantics: DXY and volatility-index relative bp are distinct from US10Y yield-change bp; raw source close levels and reference/observation times stay visible. Micro price return and TMVF trend strength are separate quantities. Perpetual funding rates are background facts and never become option net credit. Candidate reading guidance explains frozen ledger inputs and same-source EDB conflicts without generating probabilities, EV ranking or execution permission. Synthetic and background identities remain explicit; an absent current FMZ source cannot be replaced by a historical card for a real current-snapshot LLM test.

- 工作台布局以用户2026-09-26最新要求为准：沿用纸面风格，主工作台单列全宽；快照、状态迁移、缺口和历史资料合并为手动综合评审下方的“审计记录”。旧左侧栏布局要求已被替代；原数据身份、历史证据和评审门禁不变。

## Unconfirmed items

- Whether the full upstream source repository `中性回路 - opus4.8` is available in this workspace was not confirmed during this initialization.
- Whether the documented source-side tools `tools/static_validate_demo.ps1`, `tools/runtime_check_demo.ps1`, `tools/signal_review_check.py`, `tools/gex_info_check.py`, and `tools/greeks_freshness_check.py` are available outside this integration checkout was not confirmed.
- Whether a production static audit host, HTTPS access control, or sync job already exists outside this repository was not confirmed.
- Whether future Codex sessions will load the newly created `.codex/agents/` definitions without reopening the session was not confirmed; start a fresh Codex session after this initialization.

发布证据分层约定：Git实现与服务程序、用户可见FMZ文件、实例实际运行及自然卡原生输出分别记录；源卡仍旧版时按旧协议验收，不能降低新版原生验收要求。当前部署与交付回执见Astra文档22。

## 工作台证据与状态整合的长期约束

- 规范来源身份应在冻结边界去重，同身份不同内容应拒绝；读取旧捕获不得改原字节或哈希。源观察与首次可得须属于同一记录，不能跨源取最大/最小拼成一组时钟。MAP冻结截止、慢源产品窗口端点及逐源时钟分别保存。
- 操作可用性是当前候选资格与运行中/冷却/既有共享预算的派生结果，读取时重新计算；临时原因不得固化进冻结候选。显示层提示不能代替POST的原子并发、幂等和额度检查，不新增预算来源。
- 前端区域选择绑定候选/快照/MAP身份；同版明确手选保留，新版本即使复用region_id也重新取同版推荐。缺推荐/未选不借任意区域或另一侧合同。
- 可维护性优先收拢重复事实重建、兼容适配和状态判断；确定性责任账、不可变证据及最终权限检查有不同职责。增加推理框架或多模型循环须先有相同输入下的可测增益，不能替代来源接通与支持域验证。
- 标准服务是否注入风险provider、模型支持域是否匹配实际剩余期限，必须单独核验；合成μ/M或已运行的数据后台不能证明真实同候选风险消费。详细诊断与验收入口见docs/astra/72、73、74，本地/发布/真实消费/经济证据分别记录。


## 本地冻结输入与研究复核契约（2026-09-27）

- `tools/astra_underwriting_review_v20.py` 的 `build_review_input` 从已校验冻结 snapshot 派生紧凑投影，保留原 snapshot/packet hash；确定性责任账从 snapshot 取，不能从裁剪过的旧 LLM packet 读取。来源资格与时钟集中保存，行引用来源 ID。
- MAP `fact_state` 是前端与引用资格的状态口径；旧 `_background` 的 `status` 可默认为 missing，不应与 fact_state 并列作第二个复核权威。完整历史 MAP 保留，复核投影去掉旧 status。
- 研究模式复用同一 `run_review`，但写隔离 `research_only` 子目录，研究回执不进入生产 attach_review。新尝试账按完整 snapshot hash 命名以适应 Windows 原子临时文件路径；旧长文件名优先读，不能改名清空尝试次数。
- JSON/引用/快照校验通过不证明自由文本金融命题正确。币本位 Put 深尾没有固定 BTC 赔付上限，责任/盈亏结论应以确定性核算为准；失败回答须保留，不能以改善结论为目的自动复呼。工程与金融事实验收分别记录，详见文档 75–77。


## 有界目录与来源消费的长期约束

- 有限 claim ID 协议仍按原冻结目录重放；未接入 WPEM 的承保复核保留此协议。WPEM 的 `review_input@2.6.4` 出站改为 evidence points，程序拥有财务数值和安全目录，模型仅写有直接证据引用的综合解释。store 附着仍必须重放同一冻结输入，不用历史兼容校验授予新资格。
- 历史成功回执按原字节保留；读侧区分旧协议与当前协议，不能靠升级代码、改展示或重建账本把旧成功升级成当前合格。
- producer_manifest 消费只读冻结记录，来源时钟和用途资格委托现有 DataEngine；不共享可写事实缓存，不创建第二套 TTL、freeze 或预算政策。当前 health 必须描述实际消费来源。
- 可选 MAP 来源失败或在合同核算期间更新，应保留合同确定性账并取消不合格来源引用；不能混版本，也不能把可选缺口升级为所有合同的新增硬门。原生 SpaceGate、报价、费用及风险资格门独立保留。
- 风险资产消费诊断与风险 provider 是不同职责。研究 qualified 文件名和研究期限匹配不能替代同合同完整特征、价系、数量及支持域验证；诊断不能填充 μ 或授予交易权限。


## 两问与本地情景闭环的稳定契约

- 轻微倒挂按有效档位原始最优 bid/ask 判断；清洗规则与目标深度核算共用。显式非 BTC 数量单位拒绝，未知入场费不从总持有费回填。
- `bounded_claim_ids@2.4.7` 保留九字段出站协议；Q1/Q2 由同一冻结目录、选定证据和确定性账派生，不新增自由文本金额、状态账本或权限。非正 M 必须有补偿反证与重定价条件，缺 μ 保留明确缺口，结构门关闭优先要求新冻结事实。
- 模拟已提供 μ 与真实来源资格分别表达；研究身份不得覆盖补偿不足、资料缺失或结构阻断的具体取舍。宏观水平或 OI 上升不能从关键词推成 BTC 价格上行，只有源端明确价格方向可作条件解释。
- 本机有限场景复用核算、MAP、目录及 validator；只有响应 ID 选择是桩。回执按 candidate+snapshot+MAP 身份回绑，保留 SIMULATED、原评审时间与旧版差异，不创建第二套回执存储。当前候选、两问与操作优先呈现，完整来源和责任账渐进展开。


## 风险覆盖关系与阅读连续性的稳定约束

- 两问的期限、几何位置、明确量价方向、合格 MAP 推荐区与风险覆盖关系，从同一冻结投影一次派生；不建立第二套执行资格。整体 μ 有值不等于单项软风险已覆盖，没有定量归因就保留未知；同源倾向和慢背景只作条件解释。
- 所需信用读取规范合同账；费用与风险参考不变时，达到门槛只打平，超过才有正参考余量。不估计额外风险溢价或未来报价。
- 同版候选 details 按完整 candidate_id、snapshot_id 与稳定 section 保持开关；可用 sessionStorage 保存本页开关、滚动位置与安全锚点。身份不得截断，版本变化或身份缺失重置；不保存事实、令牌或“已读”账本。旧回执保留在历史展开区，当前结论须有服务端验证的同版回绑及协议一致。
- 覆盖未知是证据边界，不是额外终审硬门；交易员在既有硬门内权衡补偿、反证与未知。当前候选的简短取舍、最多两项已有可观察变化与等待代价由冻结事实派生，不增加自动选侧、风险模型或状态枚举。
- 地图审计展开状态复用当前 DOM 的保留方式，以精确候选/快照/MAP 身份与章节回绑；未选保单只使用全局 MAP 身份，身份缺失或版本变化重置。窄屏名称与价格可上下排，不改变选择行为或字段。

## 价格轴与安装迁移的稳定约束

- “全部”线性价格轴的域须覆盖当前合同与双方截止存在且相等的另一侧合同腿及盈亏平衡点；同宽价差保持同像素距离。聚焦域可把远端合同列为域外说明，不能以边界夹点冒充价格位置；双缺截止不构成同版。
- 完整安装器必须在替换前端、工具或 unit 前完成输入检查并停止现存 materializer timer/service、旧自动 LLM timer/service 和 workbench service。仅明确 not-found 可跳过；查验、停止或禁用失败须中止。回滚不自动恢复旧自动 LLM。打包哈希清单只证明所列字节，不证明服务器安装、原生 FMZ 输出或真实 provider 已验收。


## Evidence Routing 的稳定消费契约（2026-10-06）

- `evidence_routing@1.0.0` 在 `wpem_review_evidence@1.1.0` 内封存，绑定 candidate/snapshot/asof/source/hash；`review_input@2.6.0` 使用有限路由代表，输出仍为九字段 `bounded_claim_ids@2.5.0`。旧1.0.1/2.5.2回放隔离，不升级旧回执。
- 同源 block 可含不同机制/反证，不能当独立票；背景 Funding/skew/TMV 不凭字段存在占两问正支持。Q1位置/机制/路径与Q2补偿/已识别风险分工，账项不回灌方向。
- 来源时钟缺失明确 raw-clock NOT_RETAINED；producer snapshot 是声明时间，不能冒充底层观测时间。4h/12h主动流不重标为WPEM15m。
- 当前本地RC证据在 `docs/astra/evidence_routing_20261006/` 和 `.artifacts/evidence_routing_20261006_01/`；8917只读预览，真实LLM两次研究回执可重放，不构成部署或生产CURRENT。风险参考BLOCKED/空μM保持原义。

## 统一证据链最小收束 v1.2（2026-10-06）

- v1.2 当轮消费输入为 `review_input@2.6.1`，原路由/证据包与金融规则不变；紧凑实际读数和原时点进入实际请求，背景观察可选，不升级为支持票或额外门。旧 2.6.0 实际回执仍按原身份重建回放。
- 8918 为本地只读审查页：两问前置，完整读数/原标签/来源及路线按需展开。短片段只限制完整路径；风险参考继续 BLOCKED。一次新实际研究调用严格通过，禁止生产附着；未提交、推送、部署或改 timer。
- 唯一收束回执：`docs/astra/UNIFIED_EVIDENCE_V12_RECEIPT_20261006.md`；证据/六组件回退在 `.artifacts/unified_evidence_v12_20261006_01/`。下一步是用户审查和必要显示 polish，不能据此继承发布授权。

## 后续交付方向：真实市场观测与解释（2026-10-06）

- 用户明确后续核心目标是交付可在真实市场稳定只读观测、充分解释已有数据的版本，再由真实反馈决定下一步；不继续为“模型完整”扩设计。风险 BLOCKED、短窗口与慢背景只限制相应结论，不增设总分析门。
- 换窗入口为 `docs/astra/handoff_20261006_real_market/HANDOFF.md`，配套 `NEXT_WINDOW_PROMPT.md` 和 v1.2 范围摘录；先对齐当前消费接线/发布字节、当前用户审查及具体更新范围，再按有效授权交付。旧 P0 loaded hash/账户门已按用户覆盖收口，不能恢复成额外阻碍。

## 可解释输入与只读审计契约

- 当前本地候选 `review_input@2.6.9` 只把 `evidence_input` 发给 provider；完整目录、hash、来源绑定和历史原件留本地。`evidence_points@2.6.4` 每问包含带直接 refs 的解释点；程序安全目录不要求模型回填。历史 2.6.2/2.6.3/2.6.4/2.6.5/2.6.6/2.6.7/2.6.8 仍按记录版本重放，不升级历史成功。
- 本地候选 Prompt 2.1 请求显式启用 thinking/max、32768 token 上限与 300 秒单次 deadline；旧 2.6.4–2.6.8 保留原提示和调用参数。配置接线与语义验收分别证明，不能仅由新配置推定综合解释已合格；provider reasoning 仅留私有原始回执，不进入事实、Decision Trace 或页面。
- Space 当前横截面、WPEM 时间纵截面与 Economics 为并列证据维度；Attribution 汇合并赋予本单含义，Actual Input 是冻结/序列化边界。Decision Trace 的采用指已接受模型点引用，不代表组件内部串行依赖；审计读取不得成为上游门。
- clean point 请求没有自动第二次 HTTP。失败原文保留；页面可查看该次冻结输入及此前有效回执的历史身份，但不展示失败解释、不回填当前结果。结构/有限确定性检查通过不代表复杂语义、因果或经济有效性已通过，真实结果需单独验收。
- 当前事实共用 `_build_review_facts`；确定性比较归既有 `decision_relations.evidence_relations` 与 WPEM `observed_fragment_relations`，clean pack 不隐式重算缺失关系。运行请求复用一次冻结 input，validator/store 仍独立重建校验。
- 当前 packet 的合同、来源和原始报价先独立验证；只有历史 compose 失败时，观测可复用该 packet 生成当前截面证据，并明确窗口不足。坏当前 packet 仍阻断；局部历史降级不改变 Space 承保门、风险资格或执行权限。

- clean evidence 的响应直接解析单个 JSON object；Markdown 包装、重复对象、JSON 外文字及额外字段保持拒绝，失败原文不清洗。旧通用 signal parser 的兼容模式不扩散到 evidence_points 路径。协议通过与复杂综合语义验收分别记录。
- Funding 最新费率、源端 canonical/TMVF 语义和 MAP 已结算窗口保留各自 ref 与原时点；最新费率不继承历史窗口的拥挤分类或资格。原始费率与 source_details 不匹配时不借用其时钟，不重算新的拥挤状态。

## Q1/Q2 消费与回执阅读契约

- `review_display` 是现有回执的只读投影。当前解释必须同时匹配 candidate、snapshot、报价/合同、风险、来源、版本及实际 `evidence_input` hash；不同类型 hash 不交叉比较。缺关键绑定、时钟异常或合同过期不能作为当前解释。
- 观测解释的同版阅读不复用交易盘口 5 秒 TTL，也不以 600 秒调用冷却判断有效性；交易 review validity、生产 attach 和执行许可保持原约束。`RESEARCH_OK` 可解释同一冻结输入，不授予生产或执行权限。
- 当前 accepted Q1/Q2 是唯一主综合解释，程序事实默认折叠；没有当前合格解释时程序 Q1/Q2 接管。外层 rejected 优先于内层状态，失败正文不可消费；旧 accepted 只作带旧合同、冻结时间和失效原因的折叠历史参考。
- 审计分别展示实际输入与模型引用，未引用不等于未输入；严格 Delta/Path 与同合同相邻观察片段保留不同 scope。私有 provider 原文与 reasoning 不进入公开投影，原始私有回执不改写。

## Astra 当前保单 UI 阅读契约

- 主卡先给合同、净信用及费用、责任位置，再纵向阅读 Q1/Q2。当前 accepted 原解释点及限定语完整保留；缺当前合格解释时由程序事实接管，历史解释标题及持续身份提示必须说明历史时点。
- 每点依据先给已有冻结事实与来源范围，再提供原文；候选研究按责任位置、时间变化、市场压力、外部背景、补偿整理。全局当前回执链接回候选审计，避免重复主解释。
- 有限显示词典只改阅读表达，不改原文、refs、hash、时钟、输入或消费绑定。Astra 的公开原始 JSON 可在明确的审计折叠内完整查看和下载；这一规则替代早期对所有 raw DOM 的统一禁止，私有 provider 内容仍不得公开。
- 资料时间只取资料源与冻结解释时点，缺失保留未知，不借 API 更新时间。窄屏允许宽表在局部滚动，但数字和单位不得碎裂；折叠可达、已展开及下载成功都不能证明用户已读。


## 观测栈迁移的稳定边界

- 当前 frontend / source graph / FMZ producer / native schema 分层标识；stage tag 对应的新提交与历史 graph capture base 分开记录，不用重写历史批准位来表示阶段推送。
- 完整迁移需要候选代码、独立 MAP/KPF/GEX、FMZ 日志、sidecars/usage/exclusion、KPF immutable bundles 与实际 workbench locked state；标准 ZIP 不是全栈状态备份。私有 env/state 不进 Git/公开包。
- 运行中只读归档是 seed，不是跨文件事务快照。切换前只冻结工作台唯一 writer，做最后增量；旧 FMZ 继续运行，新自动 LLM 不随恢复启用。
- 新机准备不可直接运行默认启用 timer / 执行 LLM oneshot 的 bootstrap；先校验包、文件准备和 loopback smoke，再独立验私有入口、持续 bridge、source clocks 和恢复。
