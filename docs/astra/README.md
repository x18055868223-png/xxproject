# Astra 项目接手说明

Astra 是 xxproject 中把市场证据落实到具体期权合同的工作台。先读[项目综述](../../README.md)理解目的，再按本页找到实际代码、数据和运行入口；历史实验与各轮验收集中在[历史索引](HISTORY_INDEX_20261011.md)。

## 接手基点

本页于 2026-10-11 核对权威远端后整理，目标分支是 `xxproject/codex/astra-signal-rating-v1`。整理前远端分支头为 `08ae739f74689819faf318f1d90a3410463b8099`，也是 `v2.6.3-stage.1` 对应的业务基线。本轮文档提交之后，分支头以 Git 为准。

| 项目身份 | 入口 |
|---|---|
| 权威仓库 | `https://github.com/x18055868223-png/xxproject`，remote 名 `xxproject` |
| 本机续作目录 | `C:/Users/Xu/Documents/中性回路整合工程/.worktrees/astra-signal-rating-v1` |
| 业务版本记录 | [阶段清单](../../deploy/signal_audit/phase_release_manifest.json)、[候选文件图](../../deploy/signal_audit/release_candidate_manifest.json) |
| FMZ 信号层 | [最新单文件](../../demo/最新交付物/neutral_regulation_demo_fmz.py)，内部版本 `2.0.2` |
| 工作约定 | [AGENTS](../../AGENTS.md)、[项目记忆](../../PROJECT_MEMORY.md) |

`origin` 指向 `signal-audit-deploy` 辅助镜像；项目资料和代码继续维护在 `xxproject` 分支上。

## 从一张保单理解工程

**合同是串联资料的主键。** 交易所、产品、短腿、保护腿、数量、到期、计价与时点共同确定观察对象。[候选快照](../../tools/astra_candidate_asof_v1.py)把当时可用资料固定为可追溯输入，后续评审和对照围绕这份对象进行。

**空间、时间、经济是三类互补证据。** Space / MAP 描述当前价格与市场结构、成本和责任区间的关系；WPEM 整理已有时间窗口里的演变；Economics 负责合同责任、报价和补偿。工作台将其组织成 Q1 的适配解释和 Q2 的风险补偿取舍，保留支持、反证与未知。

**锁定把阅读变成持续观察。** [锁定存储](../../tools/astra_locked_candidate_store_v1.py)保存 t0 原件和后续点；[锁定对照](../../tools/astra_locked_candidate_compare_v1.py)比较同合同资料、时钟和单位；[工作台](../../tools/astra_workbench_v20.py)继续刷新原合同。暂停、停止、到期与资料缺口都属于观察过程。后续资料可以增加，原基线不被覆盖。

**金额与解释各有负责人。** [原生财务模块](../../tools/astra_commercial_math_v17.py)和[候选财务模块](../../tools/astra_underwriting_v20.py)负责赔付、现金流、费用与盈亏平衡；[人工评审链](../../tools/astra_underwriting_review_v20.py)负责组织解释。当前可见的新开信用与解除原责任所需的平仓借记分开计算，实际执行账通过独立记录确认。

## 代码阅读地图

| 要理解的内容 | 先读 |
|---|---|
| FMZ 怎样输出事实和审计卡 | [neutral_regulation_demo_fmz.py](../../demo/最新交付物/neutral_regulation_demo_fmz.py) |
| GEX 来源、增强字段和 API | [GEX 模块](../../05_GEX监控API_数据增强接口/) |
| MAP 怎样取得和组织来源 | [生产器](../../tools/astra_map_data_server_v24.py)、[来源适配](../../tools/astra_map_sources_v24.py)、[纯计算](../../tools/astra_map_v24.py) |
| KPF 怎样保留成交分布证据 | [部署说明](../../deploy/kpf_light/README.md)、[生产器](../../tools/astra_kpf_light_v1.py)、[原算法副本](../../deploy/kpf_light/vendor/kpf/) |
| WPEM 怎样变成可读的两问 | [原生窗口](../../tools/astra_wpem_native_v1.py)、[评审证据](../../tools/astra_wpem_review_evidence_v1.py)、[工作台投影](../../tools/astra_wpem_workbench_v1.py) |
| 工作台请求、刷新和持久化 | [工作台核心](../../tools/astra_workbench_v20.py)、[服务入口](../../tools/astra_workbench_preview_v20.py)、[存储](../../tools/astra_underwriting_store_v20.py) |
| 前端阅读和交互 | [frontend](../../deploy/signal_audit/frontend/)、[阅读投影](../../tools/astra_workbench_reading_v1.py) |
| DeepSeek 与历史审计评审 | [signal_llm_review_entry](../../tools/signal_llm_review_entry.py)、[signal_review_v2_runtime](../../tools/signal_review_v2_runtime.py)；当前候选人工评审另见上述工作台链 |
| 实验、回放与测试 | [历史索引](HISTORY_INDEX_20261011.md)、[tests](../../tests/)、[观察回放](../../tools/astra_observation_replay_v1.py) |

## 运行资产与保存位置

以下按 2026-10-09 至 10-10 保存的部署验收记录整理，便于接手查找。它描述已部署的分工，本轮文档整理没有重新连接服务器做实时健康检查。

| 资产 | 服务或位置 | 保存内容 / 职责 |
|---|---|---|
| EU 工作台 | `astra-workbench-v20.service`；应用监听 `127.0.0.1:8895` | 候选快照、冻结证据、锁定基线及观察点、人工评审记录；状态根目录从服务配置读取 |
| 公网入口 | nginx；[HTTPS 工作台](https://169.58.177.126/signal-audit/) | 认证、反向代理、压缩；证书续期由 `xxproject-certbot-renew.timer` 管理 |
| 私人入口 | SSH 隧道，经远端 `127.0.0.1:8896` 转发 | 本机常用 `http://127.0.0.1:8933/`，需先建立隧道 |
| GEX API | `gexmonitorapi`；`127.0.0.1:8000` | 上游缓存、增强数据、来源质量和鉴权 API |
| MAP | `astra-map-data.timer`；`/opt/astra-map-data/state/` | `current_map.json`、历史指针与来源数据引擎；消费 GEX、KPF、ETF、链上及宏观等资料 |
| KPF | `astra-kpf-light.timer`；`/var/lib/astra-kpf-light/` | Binance 每日压缩源及校验文件、90 日计算窗口、不可变发布包；`published/current.json` 指向当前资产 |
| FMZ 只读桥 | `xxproject-fmz-bridge.timer`；`/var/lib/xxproject/fmz-native/` | 从旧机 `13.231.16.198` 接收原生快照与审计，保留源时钟、哈希与追加连续性 |
| 数据维护 | `astra-data-engine-maintenance.timer` | FMZ 接收数据的存储维护，与 MAP 自身来源库分开 |
| 审计物化 | `signal-audit-materialize.timer` | 原生 JSONL、评审旁路 → 卡片索引与可阅读审计卡 |
| 运行观测 | `xxproject-observation-canary.timer`；`/var/lib/xxproject/ops/` | 来源时点、读取健康、延迟与资源采样 |
| 新 FMZ 托管者 | `xxproject-fmz-node-115167519.service`；`/opt/xxproject-fmz-node-115167519/` | 官方 robot 与 supervisor；状态在 `/var/lib/xxproject-fmz-node-115167519/`，systemd 管理开机启动和进程恢复 |
| 迁移与恢复资料 | `/opt/xxproject-migration-prep/` | 分阶段资产包、安装与校验资料、备份；私有配置单独受保护保存 |

记录中已完成工作台、MAP/KPF/GEX、原生历史与锁定资料在 EU 的恢复，以及公网认证访问。FMZ 策略生产仍经旧机只读桥供给；新托管者已登录，策略本体迁入属于下一步独立操作。DeepSeek 调用凭据在该 EU 验收记录中未配置。

接手时要同时保留三类资产：**Git 中的程序与文档、服务器的持续状态、受保护的配置与凭据**。Git 已包含 MAP/KPF 安装器及算法；stage2 迁移脚本、公网运维补充和托管者安装材料另存于本地/服务器运维交付中，本次仅更新说明，未将这些脚本一起纳入分支。凭据不写入项目文档或公共资产包。

运行摘要的依据是本地已保存的 `EU_RUNTIME_MIGRATION_ACCEPTANCE_RECEIPT_20261009.md`、`EU_PUBLIC_ENTRY_AND_PRE_FMZ_ACCEPTANCE_20261010.md`、`EU_FMZ_HOST_MANAGER_DEPLOYMENT_20261010.md`，以及对应 runtime、源数据追踪和托管者登录回执。这些详细运维记录由本机交接资料保管，本页提供分支内可独立阅读的摘要。

## 接手时的最短阅读顺序

1. 阅读[项目综述](../../README.md)，在工作台中选择一张合同，沿 Q1 / Q2 和 t0 / t1 看完整使用路径。
2. 按上方代码地图，对照同一合同的原生输入、确定性金额、解释及持久化记录，理解每份数据的生成者。
3. 涉及数据服务时阅读 [MAP 同机部署](../../deploy/astra_map_data/README.md)和 [KPF 部署](../../deploy/kpf_light/README.md)；涉及审计时阅读[审计部署](../../deploy/signal_audit/README.md)。
4. 需要了解设计来由时，查[锁定闭环说明](LOCKED_CANDIDATE_FOLLOWUP_RECEIPT_20261008.md)、[职责校正](CORE_TOPOLOGY_REALIGNMENT_RECEIPT_20261006.md)和[历史研究索引](HISTORY_INDEX_20261011.md)。

变更验证可从对应测试模块开始：锁单见 `tests/test_astra_locked_candidate_store_v1.py`、`tests/test_astra_locked_candidate_compare_v1.py` 和 `tests/test_astra_locked_followup.py`；MAP / KPF 见 `tests/test_astra_map_data_server_v24.py` 与 `tests/test_astra_kpf_map_join_v24.py`。具体发布与数据复用按 [AGENTS](../../AGENTS.md)和[版本约定](10_版本兼容与数据复用约定.md)处理。

## 经济学背景的取舍

本轮选用《xxproject 承保经济学基线 v1.0》（2026-10-08）第 2–7 页、附录 B 的合同身份、风险补偿、原生核算和持续观察思路，作为现有实现的解释。核心关系是：看净信用时同时看保护成本与责任；看风险降低时同时看补偿是否变薄；看后续变化时保留当时的原判断。

资本生存、经营成本和前瞻结果是项目长期研究的问题。现有代码已提供核算、留证与回放基础；PDF 中的完整经营账、账户资本模型、自动管理与 Dot 异步调度没有因此成为已部署模块。
