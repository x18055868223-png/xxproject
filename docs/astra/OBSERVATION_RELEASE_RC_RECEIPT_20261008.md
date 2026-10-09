# xxproject Observation Release RC 收口回执

日期：2026-10-08，Asia/Shanghai。产品版本：v2.6.3；候选身份：`OBSERVATION-2.6.3-22c18ce92bee`。

**裁定：本地功能与候选包检查通过；尚不可宣称目标小服务器部署就绪或全面封版。** 目标可用内存未覆盖实际峰值，内置浏览器下载并离线重开的完整链路也尚未验收。保留这两个具体门禁，不以本地测试代替。

## 基线与实际交付

- 工作树：`.worktrees/astra-signal-rating-v1`；分支 `codex/astra-signal-rating-v1`；继承 HEAD `950631e0aff8f27179b3b2601776b823a793eaa4` 及大量 dirty/untracked。没有 reset、clean、暂存、commit、push、tag 或云部署。
- 权威 remote 为 `xxproject`；`origin` 仍为 `signal-audit-deploy` 辅助镜像。将来项目发布只能按适用授权使用权威 remote。
- 当前本地入口：`http://127.0.0.1:8919/#lockedEpisode-lce_830f43472b559bf7d17dda9b`。后端 PID 57520；启动回执所列 9 个模块 hash 与当前文件一致。前端 token：`20261008-observation-v263-22c18ce9`。
- 正式启动入口仍为 `tools/astra_workbench_v20.py`。本地验收 wrapper 仅记录实际载入 hash、GET 次数与资源，拦截非 GET 外部调用；不是新增发布平台，也不进入包。
- 本轮证据目录：`.artifacts/observation_release_rc_20261008_01/`，以下证据路径均相对该目录。`BASELINE.json` 保存 339 个源文件 hash；`FINAL_VALIDATION.json` 记录与起点比较的实际差量及受保护源码核对。
- UI 本轮仅使用已安装的 `frontend-skill`；没有使用其它 UI skills。

## 一、访问与同单闭环

原“令牌”是工作台共享 Bearer 访问密钥，不是每张保单的新令牌。此前浏览器只在当前内存保存，刷新/新标签后丢失，导致重复输入。

现在首次私人写操作用既有密钥换取 HttpOnly、SameSite=Strict 的有界会话；配置 HTTPS origin 时附加 Secure。默认 12 小时、最多 32 个会话。密钥不进入 URL、前端 storage 或导出。普通读保单、展开与导出不调用模型。HTTPS 私人入口由既有网关认证承接；公开 HTTP 配置示例拒绝整个 `/api/workbench`，不会公开私人锁单数据。

写请求仍校验会话、同源、身份、幂等键和金融门禁；跨站、错源、无效/过期会话不能写。会话仅驻服务内存，服务重启或到期后需要重新验证一次，不把失效操作自动重放。CLI Bearer 兼容保留。

真实浏览器完成：一次验证 → 另存验收参考 → 刷新 → 新标签 → 无再次输入密钥即可停止验收参考。验收参考 `lce_284d6560914a3d6256183208` 已停止、保留记录；原活动保单 `lce_830f43472b559bf7d17dda9b` 未停止，原历史停止记录未改写。

原 t0：资料时点 20:23:21、存档 20:23:28、锁定 20:27:26，三者继续分列。t0 point `6a165a8eb1d3ef1e72205592c525f1320669db6d0eebbd63cec3891505ce19b1` 与重启前备份逐字节相同。后续仍按原合同两腿观察，候选轮换不改变锁定身份。

## 二、资源实测与预算

测量环境为 Windows 10、Python 3.12、16 逻辑处理器、约 15.9 GiB 物理内存。目标云机只读核验为 2 CPU、约 945 MiB 总内存、374 MiB 可用、约 2.14 GiB 可用 swap、12.76 GB 磁盘空闲。不能把本机结果直接等同于云端 Linux 表现。

| 项目 | 改前 | 改后实际观测 |
|---|---:|---:|
| 工作台普通 GET，未压缩 | 29,608,643 B / 7.805 s | 2,383,976 B / 3.067 s；数据窗口不同，仅作实际负载比较 |
| 原锁单比较 GET，未压缩 | 5,682,424 B / 1.577–1.625 s | 704,707 B / 0.035–0.247 s 缓存命中；冷比较曾为 2.010 s |
| 服务 RSS | 1,054.8 MiB | 普通刷新后约 341–425 MiB；完整证明/并发读后记录到 449.2 MiB |
| 服务峰值 RSS | 1,283.3 MiB | 普通初轮约 733.7 MiB；含完整证明读取的累计峰值 911.4 MiB |
| 完整 WPEM proof | 原 JSON 约 58 MB | gzip 响应 6,036,363 B，解压 57,906,727 B，GET 200，33.242 s；仅按需慢路径 |
| 冻结两端完整证据 | — | 最终导出检查请求 3.882 s，HTML 生成 0.752 s、6,367,950 B |

证据：`BEFORE_HTTP.json`、`BEFORE_PROCESS.json`、`FINAL_HTTP.json`、`FINAL_PROCESS.json`、`FINAL_PROOF_HTTP.json`、`FINAL_VALIDATION.json`。真实内容随采集时点变化；没有宣称严格同输入性能基准。最终服务记录到 8 轮成功刷新、0 轮失败；典型实际刷新 89–116 s，另有 30 s 休眠。页面 60 s 轮询不等于每分钟一定得到新行情。

已实施：

- 普通阅读只传显示投影，source 身份、时钟、资格、null 和已有数值保留；完整 source body、历史原件及完整 proof 按精确身份读取。
- 锁定输入压缩缓存最多 12 项/16 MiB；完整 WPEM 缓存最多 8 项/48 MiB，当前候选外会回收。不存在同快照 accepted 回执时，普通读取不会解压 full proof。rejected、ERROR、错配 attached 的反例已测试。
- 比较投影缓存最多 4 项；文件及 delta 基础依赖的 size/mtime/ctime 变化会使缓存失效。MAP engine 仅同次 GET 内复用，不跨请求延长当前资格。
- 完整证据响应并发最多 1 个，超出返回明确 busy；不是无限排队。完整 proof 仍受 8 MiB 压缩响应预算保护。
- t0 保留完整不可变原件；新 t1 仅在更小时保存相对完整 t0 的一层 delta。读取恢复完整原件后检验内容 hash、合同身份及 packet；不允许链式/cycle/cross-candidate 基础。不会因节省空间删除活跃依赖。
- 列表不遍历重放全部历史；证明缓存只保存验证元数据。单份记录损坏局部报错，其余记录仍可列出/读取。
- 页内大型 JSON 最多预览 65,536 字符，明确标记并提供完整 JSON 入口；避免全部原件进入 DOM。普通关闭折叠不构造巨大隐藏 JSON DOM。

**目标容量尚未通过：** 云机当前 374 MiB 可用内存低于本机普通刷新峰值，更低于 911.4 MiB 综合峰值。未在目标启动工作台、未施加真实内存压力、未改变既有服务。不能写“适合 1 GB 小服务器”。目标需先安排足够峰值余量，再在实际服务并存条件下验收；swap 不算等价的常驻内存保障。

### 写入、保留与上限

`GROWTH_RECEIPT.json`：已测 12 个实际 delta，平均 147,933 B/点、最大 159,529 B；当时锁单目录约 6.18 MB。此前 19 个真实历史点重编码试验，完整 gzip 合计 4,238,803 B，delta 2,403,137 B，减少 43.31%；不是每个点都承诺相同比例。

按平均实际 delta、每单每分钟一点、不计索引/t0/其它归档，未限额外推：

| 负载 | 7 日 | 30 日 |
|---|---:|---:|
| 1 个活动合同 | 1.49 GB | 6.39 GB |
| 4 个活动合同 | 5.96 GB | 25.56 GB |

这是容量估算，不是保留承诺。实际锁单预算为 256 MiB、1,440 样本、4 活跃、64 episodes；满额显式暂停，保留已存 t0/t1、原因及依赖，不覆盖历史。1,440 点在 1 单每分钟采样时约 24 小时、4 单约 6 小时，已有记录/字节限制会进一步缩短；真实较慢采样则历时增加。本轮没有自动滚动删历史或无限期采样承诺。

30 轮受控 soak 使用 4 个 episodes，历时 85.973 s，平均 refresh 2.063 s / CPU 1.828 s；最后 10 轮 RSS 在 63.1–65.7 MiB，峰值 69.7 MiB。锁单目录由 102,086 B 增至 1,668,719 B，平均每轮 52,221 B。全部 t0 不变、缓存项数有界；第 10 轮 Put 缺腿，健康 Call 继续可读。多次 GET 不增加采集或索引写。`soak_validation/run_soak.py` 可重跑；它只证明受控有界行为，不冒充 30 轮真实市场或云端内存压力测试。

## 三、局部缺失与拒绝边界

provider 未配置、μ/M 缺失、完整 Path 不足，不阻止合法锁定与同合同观察。缺腿、旧报价、费用缺失只限制相应精确估计；不补零、不拼新合同、不把 t1 信用回填 t0 BE。不同来源/方法/窗口不可比时保留两端并说明限制；没有来源时钟时不延长事实有效期。

已覆盖单腿缺失/其它事实存活、候选轮换与关门后继续原合同观察、重复锁定与重启幂等、坏点/坏依赖/交叉候选/循环 delta、字节和样本上限、模拟 disk full 和索引替换失败、停止/到期、读路径无写、会话跨站拒绝及精确导出身份。真实磁盘未填满。

核心身份、金融资格、证明绑定、权限错误仍拒绝，未以“可用性”放松。`mu_ref_btc=null` 不改为数值；本轮没有新增概率、评分、交易指令或执行权限。

## 四、UI 与导出

- 比较口径放到公共折叠；行内保留独特依据。原生 BE 缺失行缩短重复说明，原原因/字段仍可下钻。去掉整行警告底色及表内第二层纵向滚动，窄屏只让表格横向滚动。
- 数值 pct/pp 最多三位，极小非零显示微增/微降或正/负值小于阈值，概览和明细都不抹掉穿越。BTC 独立精度保留；不对原观点 prose 做全局数字重写。
- 独立阅读审查能辨认三类 t0 时间、最新资料时间、82,000 短腿/80,000 保护腿、短腿距离变化、费用未知、主动流不可核验、Anchor 支持与负 Gamma 反证，以及公开平仓估计不是实际 P&L。
- 最终截图：`UI_FINAL_1440.png`、`UI_FINAL_390.png`、`UI_FINAL_320.png`。390px 页宽等于 client 375px、表格 760px 在 315px 容器内；320px 页宽等于 client 305px，无整页横向溢出。更早 `UI_1440.png` 等仅是过程证据。
- 原件全量插入 DOM 曾使旧 IAB 标签无响应；已修为有限预览，真实新标签确认预览 65,536 字符、完整链接绑定原 t0/准确 t1。旧冻结标签已关闭，保留修复后的原单标签。
- 离线 HTML 通过当前真实两端完整证据生成，包含单份公开 JSON、无外部 script/resource，核对两端身份、实际本地密钥缺席；文件为 `locked_candidate_offline.html`。

**未通过项：** 内置浏览器导出按钮等待 download 事件超时，未取得实际下载完成和离线浏览器重开的证据。Node 渲染/JSON 回读通过不能替代这个 UI 验收。保留导出成品供人工重开，不声称此链路已全验收。

## 五、正式包、回归与回退

正式包：`package_validation/output/signal-audit-deploy.zip`，1,062,056 B。

SHA-256：`a571dbdb3b0501490f5c997d4a0d719ab93fb6db201f8942920f26a3b1777511`。

85 个内容文件；包含累计工作台/session/reading/locked/WPEM/MAP 依赖，不含真实 state/env/凭据或开发归档。空目录 flat import、正式入口 `--help`、隔离 loopback 启动与 GET 200 通过；网络 guard 禁止外部连接。`package_validation_result.json` 与 `PACKAGE_FINAL_SCAN.json` 分别保存入口/全包扫描结果。最终回执独立交付，不在 ZIP 中。

包为便携默认路径改写了 5 个 Python 文件的开发路径文字，未改计算逻辑；`package-manifest.json.packaging_transformations` 明列每个 canonical source hash 与实际 packaged hash。源图仍指 canonical 源文件，包内实际字节以 package manifest 为准，不能把两者说成完全相同。主 agent 补了此映射并重新打包/启动/全包秘密扫描。

最终相关回归 **166 passed / 67.08 s**，见 `FINAL_TESTS.txt`；另有 Node 语法、Git whitespace、bash 语法、nginx 代理边界与升级保留脚本 PASS。测试命令中的 PATH `bash` 曾缺失，改用 Git 自带 bash 后通过。收口中临时语法/定位/打包 hash 命令问题均修复后复测；旧失败证据未冒称通过。

受保护的 underwriting、public quote、review/Prompt/模型协议、observation replay、FMZ 交付源码与本轮起点 hash 一致。没有把前端版本当 FMZ 发布；根目录 FMZ 交付物未因本轮 UI 更新而改写。

回退：先停止待回退的本地/获授权目标写进程并备份**回退当时最新** state，保留本轮之后所有锁定点。可仅回退 UI 并保留兼容 delta reader；若需回到旧 reader，先运行 `rollback/export_full_compatible.py <全新副本目录>` 展开 delta，再用旧 reader 验证副本后切换目录，绝不能让旧 reader 直接消费 delta 或覆盖当前 live store。已实际转换 9 个 delta、用本轮前旧 reader 成功读取 3 个 episodes；检查点后新增记录需在真正回退时再导出。`rollback/locked_candidates_before` 只是起点备份，不可覆盖后续数据。

运行源码/配置起点备份在 `before/`。已知备份限制：`tests/test_signal_audit_upgrade_preservation.py` 未在 worker 第一次修改前保存原字节，当前 before 只能恢复到第一次修改后；同名 `BACKUP_LIMITATION.md` 如实记录。不能称全部 dirty 起点都可逐字节一键回退。

## 六、运行与发布事实

- 本轮新增真实 LLM 调用 0、账户/交易调用 0、云端写入/服务重启/timer 修改 0。已记数的三个本地验收进程合计至少 256 次 Deribit public GET；记录窗口以各 runtime receipt 为准，旧进程在计数窗口前的请求不编造总数。源文件同步经既有只读链执行。
- 目标当前没有 `astra-workbench-v20.service`、没有 workbench 私人 env，也没有 8895/8919 listener；当前公网入口仍是 Apache 静态审计面。
- 新工作台手动 provider 未接通；示例每日调用预算为 0。本轮没有替用户选择模型/密钥/新 Prompt。
- 目标旧 `signal-audit-llm-review.timer` 实测仍 active/enabled，`OnBootSec=60`、`OnUnitInactiveSec=60`，**不是每日两次**。本轮未修改它，不能将它计作新工作台每日两次调度已完成，也不能把本轮 0 次模型调用扩大为整台旧服务器无调用。
- 安装器默认只准备文件，不 inspect/stop/start/reload systemd；服务动作需要明确 opt-in。私人 TLS/auth 网关仍须按真实目标接入并验收，不从示例推断已部署。
- 本版本无推送/部署批准。AGENTS 要求浏览器审阅后用户明确 `当前本地页面可推送`；本轮没有请求或替用户作出该确认。

委派：explorer 调查访问/运行/证据路径；worker 分别负责会话、持久化、UI、正式包；reviewer 独立找出 rejected/stale 仍解压 full proof 的资源回归，主 agent 修复并补反例。package worker 另做只看页面证据的独立阅读审查。受控 soak 子 agent 启动遇到工具兼容错误，后续 reviewer 重启遇到容量限制，未降级模型；主 agent 接回 soak、最终 hash/包复核、真实 UI、回退兼容和回归。

下一动作：先关闭目标峰值内存与实际离线下载重开两个门禁，再就本版本、权威目标请求一次发布确认。此候选可继续本地观察，不代表云端 CURRENT、持续完整 Path、经济有效性或交易许可。
