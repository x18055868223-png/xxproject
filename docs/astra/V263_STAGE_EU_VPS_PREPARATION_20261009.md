# v2.6.3 阶段可用版与 EU VPS 准备

阶段标识为 `v2.6.3-stage.1`，适用范围是已验证的观测候选代码和新服务器准备。用户在本轮明确授权推送该候选并标记阶段可用；本地 1440×900 / 390×844 页面已复核，140 项 pytest 和 5 组独立脚本检查通过。正式生产封版、入口切换和交易权限不在此阶段结论内。

权威目标为 `xxproject/codex/astra-signal-rating-v1`，远端 `https://github.com/x18055868223-png/xxproject.git`。阶段 tag 指向同一候选提交；不更新 main 和 signal-audit-deploy 镜像。实际提交 SHA、tag peeled SHA、最终包哈希由本轮外部交付回执记录，避免提交内自引用自己的 SHA。

## 版本与证据边界

- 阶段清单随 ZIP 和文件安装保留；runtime API identity 仍展示 frozen graph 身份，阶段身份用 tag/独立清单解析。
- [阶段清单](../../deploy/signal_audit/phase_release_manifest.json) 是本轮阶段身份；[原始 source graph](../../deploy/signal_audit/release_candidate_manifest.json) 是 2026-10-08 冻结的输入记录。
- graph 的 `git_head=950631e0...`、`local_uncommitted_candidate=true`、`user_review_complete=false` 描述其生成时刻，不描述 stage tag 所在提交或今天浏览器验收状态。32 个 component 的原始字节 SHA 保留；阶段清单绑定整个 graph 的 SHA。阶段验收不改写历史批准标记。
- 前端观测版 2.6.3、FMZ reference 2.0.2、native schema nrd.schema.v1.0.0、WPEM response evidence_points@2.6.4 分层记录。内部模块仍有独立版本。
- 可见根目录、续作工作树及原候选 ZIP 的 FMZ reference SHA 相同：`87632e850d4bf8ccd75bab61bec92d54c0ab5e6c20751e36c4f73d4ebd2c7ab9`。旧机最新真实固定卡 2026-10-08 23:00 为 2.0.2、原生 session context 存在、无 compat backfill。FMZ 实例实际加载代码的完整字节 hash 仍未证明。
- risk reference BLOCKED、μ/M 缺失、原生窗口不足、RESEARCH_ONLY 和默认关闭模型预算继续有效。浏览器只读取和导出资料，不执行交易或调用 LLM。
- Git 使用精确路径暂存。8 个已审哈希文件用 `-text` 保留原字节；不把运行状态、env、密码、私钥或 .artifacts 全量提交。干净 checkout 再打包并逐项核 source / packaged transformation / FMZ hash。
- 第一轮混用 pytest 调用了 3 个需要 main() 注入参数的独立脚本，产生 fixture 错误；已改用其原生入口，5 个独立脚本全部 PASS，140 个适用 pytest 全部通过。初次失败证据保留。

## 新 VPS 实测

| 项目 | 结果 | 含义 |
|---|---|---|
| 连接 | 169.58.177.126:22，root；host key 固定校验 | 密码仅用于内存中认证，不入文件/仓库 |
| 地域 | EU，用户控制台确认；具体城市未核实 | Europe/Berlin 时区不作为机房证明 |
| 资源 | 6 vCPU，11.68 GiB RAM，95.82 GiB 文件系统 | 可进入迁移准备；应按实际状态增长规划磁盘 |
| 系统 | Ubuntu 24.04.5，Python 3.12.3 | 原已有 reboot-required；本轮不自动重启 |
| 基础依赖 | venv/pip、curl、git、rsync、zip/unzip、zstd、ca-certificates、libgomp、nginx | 包管理及 Python pip check / import 已通过 |
| GEX public_json 依赖 | FastAPI / Pydantic / pydantic-settings / Uvicorn，独立 venv | 精确版本锁定在私有 staging；未安装浏览器采集模式 |
| 监听 | SSH22 和本地 DNS | nginx disabled/inactive，未开应用公网端口 |
| 新机→旧机 | 旧 13.231.16.198:22 TCP 和 SSH banner 可达 | 未把未认证 TCP 试验称为持续数据桥 |

新机 08:28 UTC（北京时间 16:28）从固定 IP 直连 12 域名、45 用例、127 次 HTTP；DNS/TLS 全部通过，0 超时、0 个 403/451，无代理和 IP 切换。数据接口、更新时钟和采集责任详见[此前完整清单](VPS_MIGRATION_FEASIBILITY_20261009.md)，本轮逐接口实测保存在 `.artifacts/vps_stage_release_20261009_01/NEW_VPS_NETWORK.json`。

| 来源 | 当前实测 | 边界 |
|---|---|---|
| Deribit | 当前未到期两腿公开盘口 200，中位约 54 / 59ms | 自动选择下一可用 expiry；不重测已到期合约作网络阻断 |
| Binance Futures | OI 等公开端点 200，OI 中位约 265ms | 行情通不等于账户/地区产品资格 |
| GEX | public_json 端点 200，latest 中位约 2.4s | 来源字段缺失仍限制用途 |
| ETF / 链上 / FRED | 对应公开端点 200，ETF 约 293ms、链上 search 约 68ms | 源观察日期与 HTTP 获取时刻分别保留 |
| KPF archive | 10-08 ZIP 与 CHECKSUM 已 200 | 早前 Tokyo 404 是当时上游尚未发布；旧机 08:38 KPF oneshot 现已 success |
| Yahoo 宏观 | VOLQ 与 DX=F 仍 404，既有回退符号 200 | 不是整套宏观网络不可达 |
| DeepSeek | 未认证 models 返回 401，TLS 正常 | 未迁密钥调用、未验证 completion、未启自动模型 |
| FMZ | node 首页 526；官方其它站点测试分别记录 | 普通首页不能证明 robot 数据通路状态 |

短测支持 EU 机房继续准备，不代表 24h SLA。没有发现需要再购买 UK / Singapore 测试机的网络拒绝证据。平台用途限制继续按官方规则确认，不用地域/IP 切换规避。

## 完整迁移资产分层

1. 已提交候选的干净代码 ZIP，加当前提交构建的 MAP/KPF 独立 server-data 包和完整项目 Git bundle。
2. 旧机观测 runtime：static/cards/index、tools、sidecars/usage/exclusion/attempts、MAP/data-engine、GEX 源码/缓存、systemd 配置和私有 env/Apache 配置。私有归档仅在受保护目录保存。
3. 旧 FMZ 日志全目录，以及 KPF source / checksum / published immutable versions，各自独立归档。robot、平台账户和交易实例继续留在旧机。
4. 本地 8919 对应 live_state 的锁单 t0 / delta / index / daily / review 状态另归档；不迁浏览器会话和访问密钥，不改变当前唯一 writer。
5. 新机依赖锁、包校验单、准备安装脚本和恢复说明。公开代码和私有状态分开，所有归档 SHA 校验后再进入新机 0700 staging。

运行中读取的旧机备份属于迁移 seed，不能假称跨文件事务一致的切换快照；保留捕获起止、源时钟和可变文件警告。正式切换前要再导入最后增量，并只冻结工作台 writer；旧 FMZ 不停止。

新机只做文件准备和本机 smoke 验证。不得使用默认会 enable --now 的全栈/bootstrap 脚本；不恢复旧自动 LLM timer，不把旧 Apache/密钥直接覆盖新系统配置。私有 runtime 归档先保持封存，恢复到隔离目录核引用与权限后再决定启动。

## 切换前还需完成与回退

- 安装后重启/重新核对内核状态；新机持续只读桥与 source hash / clock 前进验收、至少 24h shadow；当前 TCP22 与 seed 备份不能替代它。
- private TLS/auth、GEX 私有访问路径、唯一 locked writer 转移和最新增量恢复演练。不要向公网直接开放 8895 或 GEX 8000。
- 验证旧 raw→新 materializer→index→页面追上同一张真实卡；旧静态 index 滞后是此前已核问题。
- 100 GB 盘可容纳已盘点约 3.1 GiB 原始旧资产和当前迁移材料，但不能按每分钟完整 proof 长期重复归档；增长/压缩率和保留期需 shadow 测量。
- 旧机所有服务与 FMZ 参数继续运行。新机失败时隔离新写入口，保留新增 locked/review/usage 后回到旧页或本地 8919；不能用 seed 覆盖新证据。自动模型不随回退启用。

本轮固定 explorer 分别核提交闭包和旧机资产/恢复边界；reviewer 独立核候选、哈希例外、网络/依赖证据和最终暂存范围。主 agent 复跑测试、浏览器、SSH 网络及包字节校验，并负责最终 Git / 新机操作。
