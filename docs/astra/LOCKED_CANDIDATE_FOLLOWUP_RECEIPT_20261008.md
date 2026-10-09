# Locked Candidate Baseline & Follow-up Receipt

状态：本地功能及回归验收完成；离线文件浏览器重开受工具协议限制，未实测。仅本地 v2.6.2 候选，不构成发布授权。日期：2026-10-08，UTC+8。

## 基线与范围

工作树 `.worktrees/astra-signal-rating-v1`，分支 `codex/astra-signal-rating-v1`，继承 HEAD `950631e0aff8f27179b3b2601776b823a793eaa4`。开工核对 v2.6.1 最终交付哈希全部一致；328 个既有文件哈希及关键原件冻结于 `.artifacts/locked_candidate_followup_20261008_01/`，继承 dirty 保留。

用户目标是锁定同一保单之后的持续观察闭环。附件作为范围、口径与验收参考，不授权 LLM 调用、账户、交易、推送或部署。本轮保留已验收的选腿、SpaceGate、风险与模型协议。

## 实际改动

旧锁定处于受控评审流程，不能独立恢复同一合同的长期参考。本轮新增独立、鉴权、幂等的“锁定参考并跟踪”；保存精确显示 snapshot 的完整 packet、合同、已有归因、资料缺口及严格绑定的 accepted 解释。t0 原件不可改写，后续资料另存。

既有 `AstraWorkbench.refresh` 继续消费原两腿、原数量、原到期，不依赖默认候选仍在列表或新仓 SpaceGate 开放。不另建行情采集器。锁定和停止只使用短缓存锁及存储原子锁，不等待整轮网络刷新；在途请求晚到也不能向已停止记录追加样本或改写停止原因。

锁定独立于 provider、LLM 预算/冷却、μ/M 和完整 Path。GET、展开、下载只读；综合评审继续通过原受控路径显式发起。没有账户、成交、保护条件来源时保持未录入，不补造持仓。

存储默认上限：4 个活跃 episode、64 个总 episode、每单 1440 个样本、256 MiB 总预算、单点 32 MiB。耗尽后保留已有事实并显式暂停；到期只标记结果未核验。停止跟踪不表示平仓。另存参考固定点击时已保存的精确点，不被后台更新替换。

## UI 工作定义

- 视觉：沿用 v2.6.1 安静纸面与正常字重，单一对照表为主，避免重复仪表盘。
- 内容：固定合同及两端时钟 → 责任/退出概览 → 原参考与最近更新对照 → 已观察过程 → 解释及原件。
- 交互：锁定后直接进入稳定详情路由；两端标题随阅读保持可辨；原件原生折叠，同 episode 刷新保持阅读位置，切换记录隔离状态。

本轮 UI 仅使用已安装的 `frontend-skill` 的 Apps / Utility Copy 规则。不为界面补造财务值、风险评价或成交记录。沿用首页 Q1/Q2，只增加紧凑入口与稳定二级路由。

## 对照口径与 owner

`astra_locked_candidate_compare_v1.py` 只连接原 ref/来源、方法、窗口、单位与合同关系，复用 WPEM/decision relations。t0 无值不是零，t1 缺失不是风险改善；方法或窗口不一致不给精确 Delta，同源时钟未变不算新增确认。原支持与反证保留既有方向关系，未建立新评分、风险灯或持有/退出命令。

原金融 owner `astra_underwriting_v20.py` 的 15 个既有函数 AST 全部不变，只增加 3 个确定性辅助函数。短腿和保护腿距离按固定边界 B，Put 为 `(S-B)/B`、Call 为 `(B-S)/B`，变化用百分点；盈亏平衡线只来自 t0 原生账，不随新信用移动。平仓买回短腿使用 ask，卖出保护腿使用 bid，并核数量、深度、时钟与费用。新开信用、平仓借记、参考结果、实际成交四者分开。真实样本缺费用、μ/M 与有效 BE，因此相应结果保持未知。

## 真实本地观察证据

入口：`http://127.0.0.1:8919/#lockedEpisode-lce_830f43472b559bf7d17dda9b`。原合同为 Put，`BTC-9OCT26-82000-P / BTC-9OCT26-80000-P`，1 BTC，到期 2026-10-09 16:00。这是公开观察参考，实际成交未录入。

- t0 资料：20:23:21.156；资料存档：20:23:28.590；锁定保存：20:27:26.124，三类时钟分别显示。
- 固定验收截点 `real_final-receipt.json`：9 个点，其中锁定后新增 8 个；最新资料 21:00:27.396。端点源时钟跨度 37 分 6.240 秒；锁定后新增样本的存档跨度 30 分 19.435 秒。最大相邻源时点间隔 6 分 50.597 秒，含早期基线等待与服务重启，不能称连续监控覆盖。
- 配置 60 秒是锁定样本最小间隔。真实刷新还受既有整轮行情采集耗时影响，所测单轮约 100–123 秒；页面 60 秒轮询不是行情频率承诺。
- t0 point 为 `6a165a8eb1d3ef1e72205592c525f1320669db6d0eebbd63cec3891505ce19b1`；公开基线 SHA256 `1731e1b2b8d39f0bc4a66b653b65e2208a16aaff9c46717adfe3d863cf54a10d` 在首轮、更新、重启及最终验收保持一致。
- 测试另存参考 `lce_066e535a83881f8d545be408` 已在真实页面停止，保留 3 个历史点；原记录继续观察。停止后后续刷新不再改变测试记录。
- 最新本地进程 PID 26124，加载 7 份 Python 源哈希与磁盘相符。服务仍使用既有 8919 wrapper/只读源同步，无 provider API key。

记录文件均在 `.artifacts/locked_candidate_followup_20261008_01/`：`real_first-*`、`real_before_restart-*`、`real_after_restart-*`、`real_final-*`、`LOCAL_RUNTIME*.json`。按 PID 去重的验收请求截点见 `REQUEST_COUNTS.json`：303 次 Deribit 公开 GET（8 次 index、8 次 instruments、284 次 order_book、3 次 instrument），含既有默认候选采集和重启初始化，不全算作新增锁单请求。记录中的非 GET 与刷新异常均为 0；实际 LLM、账户、交易、推送、部署均为 0。原只读同步继续使用既有授权链，本回执不包含凭据。

## 页面、复盘和测试

桌面已读取固定合同、三类时钟、责任/退出概览及 18 行前后表；390/320 像素下整页无横向溢出，表内可横向滚动，项目与表头固定。同 episode 后台轮询实测 106.749 秒，资料状态从 21:00:38 更新到 21:02:52，window Y=1157、表内 Y=40、过程展开均保持。切换记录隔离阅读状态。最终浏览器 console error 为 0。

`LOCKED_FOLLOWUP_OFFLINE.html` 固定本次 t0/t1、18 行、公开原件与 renderer `20261008-locked-v262-eedf5b1d`。由浏览器 DOM 已显示的公开两端调用同一生产导出函数保存；嵌入 JSON 两端完全一致，可见表格 3554 个非空白字符一致，无脚本和外部资产依赖。公开包剔除 provider 私有请求/响应、reasoning、凭据与 source_bindings，并有 canary 回归。

浏览器点击导出已出现生成提示，但 IAB 下载事件未给出文件回执；工具禁止打开 `file:` 协议，故没有声称下载落盘与离线浏览器重开已经实测。可直接打开上述交付文件复核；内容核验见 `OFFLINE_ACCEPTANCE.json`。

实际运行结果（多轮重叠用例，不相加）：

- 12 份锁定、workbench、WPEM、金融/报价、as-of 与 UI 相关 suite：172 passed。
- 隐私与 replay 修补复测：39 passed；精确历史点另存复测：32 passed。
- 最终并发修补：锁定/store/workbench 三份 suite，52 passed；独立 reviewer 另跑新增两例，2 passed。
- 最终 UI 三份 suite：25 passed；`node --check` 通过。
- 反例覆盖幂等/原子失败、损坏 packet、预算暂停、重启恢复、默认换腿与关闭 gate、Put/Call 有符号边界、费用/盘口缺失、来源换窗、同钟不增信、accepted/rejected 绑定、t0 BE 固定、在途请求晚到与跨 episode 阅读状态。

explorer 分别梳理生命周期、金融和交互边界；worker 分工存储、比较与 UI；reviewer 独立检查绑定、隐私和并发。主 agent 审查整合 diff、运行回归、核对运行时哈希并完成真实页面验收。合成反例与真实观察证据分别保留。

## 最终身份与回退范围

本地候选标识 `LOCKED-2.6.2-eedf5b1d74e9`，source graph `54a6720ab7882e6e65c980c7236b6c8d50c8b37f937f865ab6321908b550b45b`。manifest 的 release approval/user review 均为 false。未提交、未推送、未打 tag、未部署；FMZ 本体未更新。最终逐文件哈希、范围和版本图检查见 `FINAL_SCOPE.json`、`FINAL_SOURCE_IDENTITY.json`。

本轮相对冻结 dirty 基线修改 13 个文件：`PROJECT_MEMORY.md`；`deploy/signal_audit/frontend/{app.js,index.html,VERSION.json}`；`deploy/signal_audit/{release_candidate_manifest.json,package_signal_audit.ps1,install_or_update.sh,workbench-v20.env.example}`；`docs/信号审计更新部署规范_v1.0.md`；`tools/{astra_workbench_v20.py,astra_underwriting_v20.py,astra_underwriting_public_quote_v20.py,astra_observation_replay_v1.py}`。

新增 7 个文件：`tools/astra_locked_candidate_store_v1.py`、`tools/astra_locked_candidate_compare_v1.py`、`tests/test_astra_locked_candidate_store_v1.py`、`tests/test_astra_locked_candidate_compare_v1.py`、`tests/test_astra_locked_followup.py`、`tests/test_astra_locked_followup_frontend.py`、本回执。

精确回退需先停止当前 8919 进程并保留已锁定状态，再仅将上述 13 个文件恢复为本轮 `before/` 对应原件、移除本轮 7 个新增文件，重启继承 wrapper。不得 `git reset/clean`，以免丢失前轮 dirty。`.artifacts/` 验收材料与既有 live_state 是本地证据/状态，不纳入发布；不要清理用户已锁定资料。此次没有执行回退。

外部口径核查：[Deribit Inverse Options](https://support.deribit.com/hc/en-us/articles/31424939096093-Inverse-Options)、[public/get_order_book](https://docs.deribit.com/api-reference/market-data/public-get_order_book)。只用于币种、合约乘数与盘口字段核对，原金融 owner 继续负责计算。
