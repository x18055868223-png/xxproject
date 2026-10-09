# xxproject v2.6.3 服务器迁移可行性与现网证据

核验日期：2026-10-09。权威工作树：`C:/Users/Xu/Documents/中性回路整合工程/.worktrees/astra-signal-rating-v1`；项目远端 `xxproject`。用户确认欧洲、UK、Singapore 尚未购机，本轮完成现网核验与选型，地域实测留到购机后。

**建议进入欧洲高配测试机准备阶段；当前证据不足以切换生产。** 首选候选为 Helsinki 的 4 vCPU / 8 GB / 160 GB x86_64 VPS；保留旧 FMZ 运行端，先迁观测面并建立明确的只读证据桥。没有新地域的 DNS/TLS/HTTP 或资源压力实测，不能写“欧洲已通过”。

本轮仅新建本报告、脱敏证据与只读探测脚本。业务逻辑、provider 配置、交易权限、服务状态未修改；未调用模型 completion，未购机、部署、commit、push、tag，也未切换 IP/代理/API 替代域名。旧自动 LLM timer 原本已启用且继续运行；本轮零 completion 不代表整台旧机零调用。

## 迁移可行性表

| 地区 / 路径 | 实测证据 | 来源 / 资格边界 | 裁定 |
|---|---|---|---|
| 现有 AWS 东京 `ap-northeast-1a` | `t3.micro`，12 域名 DNS/TLS 全通过；45 个 HTTP 探测用例、127 次请求，0 超时、0 次 403/451 | KPF 最新日归档 404、广义美元过期、GEX 部分字段缺失；审计页索引落后于原始卡；FMZ 控制网页另见 526 | 可保留现有 producer；约 945 MiB RAM 不具备新工作台并存余量 |
| 欧洲首选候选：Helsinki | 无主机，未实测 | 要验真实 VPS 出口、Deribit/Binance 公共市场数据用途、源时间、同步和并存峰值 | 建议按小时开测试机，未达到切换门禁 |
| 欧洲替代候选：AWS Ireland | 无主机，未实测 | 同样门禁；延续 AWS 运维可降低平台切换负担，不能据此推断网络优劣 | 可作为供应商替代选型 |
| UK / London | 无主机，未实测 | Deribit 官方限制 UK retail；不能把机房地址解释为账户/用户资格 | 不作为默认后备；只在用途和平台规则允许后比较 |
| Singapore | 无主机，未实测 | 公共市场数据 API 和账户产品资格须分别确认 | 可作合规用途下的第二地域对照，未证明比欧洲更好 |

地域政策与网络结果分开判断。Deribit 当前官方限制列表包含 Japan 和 UK retail；这是需确认平台用途的真实政策边界，HTTP 200 不证明资格。Binance 官方 2021 公告限制 Germany/Italy/Netherlands 用户的衍生品产品，公告本身不能证明今日当地公共数据 API 必然返回 451。不得以换地域、IP、代理或 testnet 规避平台限制。[Deribit 官方范围](https://support.deribit.com/hc/en-us/articles/25944487427741-Restricted-Jurisdictions)、[Binance 原公告](https://www.binance.com/en-KZ/support/announcement/detail/f72d0fff2bd24b488213fa30284dd4cd)。

## 实际运行外部依赖、采集责任与频率

以下是当前代码显式网络调用及现网配置已证实的依赖。所有 HTTPS 请求使用 443/TCP、校验证书和主机名；市场数据走 REST GET/JSON 或 HTML/CSV。没有发现该 FMZ 单文件调用交易所 SDK 的账户、下单、私有行情、WebSocket 订阅路径。绑定账户名称不等于调用这些账户。频率是配置/调度周期，执行耗时和失败会延长真实采样间隔。

| 域名 / API | 采集责任与运行位置 | 周期、超时和预算 | 关键性 / 缺失后影响 | 包覆盖 |
|---|---|---|---|---|
| `www.deribit.com/api/v2/public/get_index_price`、`get_instruments`、候选短腿 `get_order_book?depth=1` | 工作台候选发现；`tools/astra_underwriting_discovery_v20.py:452` | 后台 refresh 默认 30s；每请求 10s；实际一轮完成后再等 30s | 高：发现当前候选；失败保留 gap | 模块包含 |
| 同域 `public/get_instrument`、`public/get_order_book?depth=5` | 工作台对明确两腿的公开报价；`tools/astra_underwriting_public_quote_v20.py:28` | 每两腿 4 GET；8s/请求；锁定跟踪间隔下限 60s，最多 4 张 active，并受 refresh 耗时限制 | 高：当前报价与同单跟踪；原 t0 不覆盖 | 模块包含 |
| 同域 `public/ticker?instrument_name=BTC-PERPETUAL` | 独立 MAP producer 当前价格；`tools/astra_map_data_server_v24.py:124` | 每轮 MAP；timer 完成后 300s；来源 transport 20s | 高：MAP 价格轴背景 | Python 包含，独立 service/config 不包含 |
| 同域 `get_instruments`、`get_index_price`、`ticker` | 旧 FMZ 原生期权/greeks/SRD/GGR；FMZ 本体 `8538`、`14872` | 主循环 60s，instruments 缓存 300s；greeks 每 ticker 2s/无重试，总墙钟预算 12s | 中高：期权事实与参考；失败限制相关用途 | FMZ 仅参考件，不安装运行实例 |
| `api.binance.com/api/v3/aggTrades`、`depth` | 旧 FMZ spot 事实；FMZ 本体 `8336` | 主循环默认 60s；通用 timeout 5s，配置重试 2 次 | 高：TMVF/EDB/行情事实 | FMZ 参考件 |
| `fapi.binance.com/fapi/v1/aggTrades`、`premiumIndex`、`klines`、`fundingRate`、`openInterest` | 旧 FMZ futures 事实；FMZ 本体 `8336`、`14983` | 主循环 60s；TMVF 刷新 300s、M_DIE 60s；通用 5s/重试 2 次 | 高：原生期货、资金费率和杠杆事实 | FMZ 参考件 |
| 同域 `openInterest` + `/futures/data/openInterestHist` | MAP server 外部采集；`tools/astra_map_sources_v24.py:873` | fast 组，min interval 300s；最多 2 请求/次；20s；registry budget 6/min | 高：OI/24h 窗口 | 模块包含 |
| 同域 `fundingRate` | MAP server 只采已结算 funding；同文件 `929` | fast/min 300s；1 请求/次；20s；registry budget 6/min | 高：融资背景；预测值不能替代已结算值 | 模块包含 |
| `data.binance.vision/data/futures/um/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-YYYY-MM-DD.zip` 与 `.CHECKSUM` | KPF light 独立 producer；`tools/astra_kpf_light_v1.py:48`、`676` | 5min timer；90/60 日窗口；新日文件+校验；download 45s、retry 1、并发 1、source budget 5 GiB | 中高：KPF 原算法背景；最新日缺失不能靠新 RAM 补齐 | **不包含 KPF producer/vendor/service** |
| `fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT` | KPF 当前报价；同文件 `1069` | oneshot 需报价时 1 GET；10s，source clock max age 300s | 中高：KPF 当前价；没有 time/closeTime 不认当前 | 不包含 producer |
| `gexmonitor.com/api/gex-latest?asset=BTC&exchange=all&lite=true` | 旧 FMZ 原始 GEX adapter；FMZ 本体 `9218` | 主循环相关采集，min interval 60s；5s/重试 2 次 | 中：GEX/anchor 参考；资格和冲突检查保留 | FMZ 参考件 |
| 同域 `/api/gex-latest`、`volatility-metrics`、`options-chain`、`price` | 独立 GEXMonitorAPI，现网 `public_json` | 现网 refresh 1800s；request timeout 45s；不需要页面浏览器模式 | 中高：给 FMZ 提供增强 GEX context | **不包含独立 GEX 服务/缓存** |
| `http://13.231.16.198:8000/v1/info`，Bearer GET | FMZ→旧 GEX 增强桥；FMZ 本体 `256`、`9898` | FMZ 配置 600s；5s/重试 2 次；旧服务监听 `0.0.0.0:8000` | 中高：相关 NetGamma/SpaceGate；默认 IP 是迁移耦合；迁新机后须私有/TLS 化传输 | 引用配置，未迁移服务 |
| `farside.co.uk/bitcoin-etf-flow-all-data/` | MAP server ETF HTML 采集；`tools/astra_map_sources_v24.py:755` | slow 轮转；min interval 1h；1 GET/次，20s，budget 2/min | 中：ETF 背景；缺失限制该结论 | 模块包含 |
| `bitview.space/api/series/search`、`/api/series/{id}`、`/{id}/day1`、`/{id}/height` | MAP server cost basis / realized PnL；同文件 `1047`、`1148`、`1430` | slow/min 6h；20s；最近 cost 15/30 请求、PnL 13/18 请求；PnL 使用 first_height 日高桥 | 中高：STH/LTH/ALL CP、14 日已实现损益；时间/窗口合格后使用 | 模块包含 |
| `fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10/DFII10/DTWEXBGS` | MAP server 名义/实际美债、广义美元；同文件 `1012` | slow/min 6h；各 1 GET，20s；日观察时间与下载时间区分 | 中：宏观背景；过期只限制相关用途 | 模块包含 |
| `query1.finance.yahoo.com/v8/finance/chart/{symbol}` | 旧 FMZ MacroPressure；FMZ 本体 `1774` | 1h；5s/重试 2 次；`^VOLQ→^VXN→^VIX`、`DX-Y.NYB→DX=F`、`^TNX` | 中：宏观软证据；既有符号候选可回退 | FMZ 参考件 |
| `api.deepseek.com/chat/completions`，Bearer HTTPS POST | 新工作台人工评审；旧 sidecar 有自己的 timer；`tools/signal_llm_review.py:2742` | 新包 daily HTTP limit 默认 0；人工评审冷却 600s、timeout 默认 180s。旧 timer `OnUnitInactiveSec=60`，env limit 4/concurrency 4/timeout 240s | 可选：事实阅读、锁定、刷新不调用 LLM | 工具包含，旧自动 service/timer 不包含 |
| 同域 `/models`，HTTPS GET | **本轮诊断接口**，不是运行时采集或 completion | 未鉴权 3 次 401；已有配置鉴权只读 1 次 200 | 确认 DNS/TLS/API/key 可达，不证明评审质量 | 本轮探测脚本 |
| `node.fmz.com`；`www.fmz.com` | robot 控制面；旧进程 `-s` 仅提取 host，UID/password 不输出；已有 robot TCP 对端 443 | 持续连接；内部握手/心跳周期与实际应用层协议未证明，不能填造 WSS/API path | 若迁 robot 是高关键；本轮保留旧 robot | **robot/平台凭据不包含** |
| SSH `13.231.16.198:22`，固定 host key | 当前本地预览只读同步；`.artifacts/observation_baseline_20261006_01/serve_current.py:12` | 60s 循环，SSH connect 10s/同步总预算 50s，gzip+hash+本地 atomic 写 | 高：目前本地 FMZ/MAP live 输入桥；不是生产迁移闭包 | **不包含** |
| SMTP host 由私人 env 配置，587/STARTTLS | 可选状态通知；`tools/astra_workbench_v20.py:1470`、`3617` | 事件驱动、现有冷却；15s；旧机无工作台 env，未发现本链启用的 SMTP host | 低：不承担市场采集；本轮不发送邮件 | 发送代码包含，host/key 不包含 |

FMZ JSONL/data-engine/MAP manifest/locked state 是本地文件协议，不是外部 API。USDT borrow 默认无授权源，0 HTTP；`/sapi/v1/margin/interestRateHistory` 等私有路径明确未使用，不能把缺口写成 Binance 公共接口不通。前端自身没有外部 CDN/字体 URL，读取同源 API 和静态资产。OS 包源、GitHub clone、robot 下载属于安装阶段，不是这套业务代码的日常数据采集；安装时需要另验供应链，不混入采样频率。

第一阶段若保留旧 FMZ/MAP/KPF/GEX producer，新机主要直接依赖 Deribit 公开报价与旧机 SSH 证据桥，其余上表采集继续由旧机承担。只有迁独立 producer 才需要同时搬齐其服务资产。FMZ robot 二进制内部的所有控制面域名/握手路径无法由仓库源码穷尽，本轮只证明进程配置主机与实际 TCP 连接；未做全进程网络抓包，不能把这张显式调用表称为平台内部完整清单。

## DNS / TLS / HTTP / 延迟证据

东京探测窗口为 2026-10-09 **12:47:38–12:58:10 北京时间**。直接路由、没有代理、没有禁用 TLS 校验。12 个域名 DNS 和 TLS 1.3/hostname 校验均成功，证书在探测日有效。公网 IP/解析结果和证书到期保存在 JSON。延迟为完整短 GET/HEAD 的样本中位数；每条 1–5 次，不是持续 SLA。

| 来源 | 东京 HTTP 与中位延迟 | 源数据更新 / 用途状态 | 迁移影响 |
|---|---|---|---|
| Deribit | discovery index 331ms；两腿 book 294/302ms；MAP ticker 295ms，均 200 | book 原始 timestamp 在样本间前进 | 可读公开报价；EU 待同法验 |
| Binance Futures | OI 52ms、history 79ms、settled funding 85ms、aggTrades 141ms、ticker 49ms，均 200 | OI exchange time 接近请求；funding 最近已结算 2026-10-09 08:00 北京时间 | 短样本未发现网络阻断 |
| Binance Spot | aggTrades 102ms、depth 86ms，200 | 市场数据读取，不读取账户 | 旧 FMZ producer 保留 |
| KPF archive | 10-08 ZIP 404/83ms、CHECKSUM 404/160ms；10-07 ZIP 和 CHECKSUM 均 200 | KPF service failed/exit 1，journal 4 次 HTTP404；已发布 bundle 10-08 15:19 北京时间，输入完整覆盖 07-10至10-07共90日 | 新日增量阻断；旧 READY bundle 尚在 7 日背景 TTL 内，不冒充新日完成 |
| GEX upstream | latest 356ms、volatility 2630ms、chain 1135ms、price 147ms，200 | latest upstream timestamp 12:47:25；旧 `/v1/info` 200/701ms，但 availability partial、observed_at null，DVOL 等时钟缺失 | RAM 升级不会补齐缺失字段；旧缓存 30min cadence影响消费时效 |
| ETF | 200/1502ms | 最近完整日 10-08；48h用途TTL内 | 可用背景 |
| 链上 | search 66ms、metadata 84ms、CP day1 74ms、PnL height 75ms，200 | 生产 CP/phase/price 最新 10-08；PnL 09-25至10-08严格14日，missing=[] | 可用背景；单个空搜索响应不证明没有真实产品 |
| FRED | DGS10 200/95ms；DFII10 200/74ms；广义美元 200/70ms | 两种收益率最新 10-07，用途 USABLE；广义美元最新10-02，现有48h TTL判STALE | 可达和新鲜分开；美元背景结论受限 |
| Yahoo | DXY 200/134ms、TNX 200/137ms、VXN 200/138ms；VOLQ/DX=F 404 | 原生 FMZ 宏观当前 source_symbol 是 VXN、DX-Y.NYB、TNX，quality OK | 既有符号候选处理了 VOLQ 缺失，无全源阻断证据 |
| DeepSeek | 未鉴权 `/models` 401/233ms；旧 env 鉴权 GET 200/565ms | catalog 为 deepseek-flash/deepseek-v4-pro；本轮0 completion | 接口/key读取可达；人工评审实际响应未验 |
| FMZ 控制面 | www 首页 200/166ms；node 首页两次526/290ms；DNS/客户端TLS正常 | 旧 robot TCP连接仍ESTAB，原生导出继续更新；没有执行重连测试 | node普通网页异常不等于 robot 协议失效；若迁 robot，须由FMZ实际握手/心跳另验 |
| FMZ 文件桥 | SSH读取成功；snapshots/export状态ok、failure_count=0、12记录ring | configured300s，最近11个间隔中位313.426s；新导出时钟04:38→04:43→04:48 UTC前进 | 当前旧 producer到文件成立；新机接收尚不存在 |
| 原始卡→旧静态索引 | 10-09 13:05、13:14北京时间只读复核index及本机HTTP，两次200且与文件相同 | raw最新固定卡是10-08 23:00；index最新卡仍10-07 00:57:22，index文件mtime10-07 15:15:38；两次materializer均activating/start，但PID/启动时间已变化 | **真实消费滞后**。运行中和HTTP200均不证明更新成功；根因未定，未重启/修复。workbench直读snapshots是另一条链 |

上述东京 127 次公共诊断请求超时 0/127，403/451 0 次。另有 GEX 本机鉴权 GET 和 DeepSeek 鉴权目录 GET 各一次，不计入127。客户端 TLS 成功不排除 CDN 到源站 TLS 异常；526 的定义来自 Cloudflare 官方，但探测 node 首页不能代替 robot 应用层验收。[Cloudflare 526 说明](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-526/)。

DeepSeek 当前官方文档明确 `deepseek-v4-flash` 旧别名仍接受，并由 V4.1-Flash 服务；候选 manifest/元数据使用 `deepseek-flash`，但包内 `signal_llm_review.py` 运行默认仍为旧别名，工作台 env 模板 model 为空时沿用该默认，旧 env 也用旧别名。这里存在配置与上游模型映射版本边界，没有证据把旧别名判为阻断。实际评审的协议、输出质量和预算仍需另行人工验证，本轮没有 completion。[DeepSeek 官方当前模型说明](https://api-docs.deepseek.com/quick_start/pricing/)。

完整逐 API、样本数、最大延迟、状态、源时钟及 DNS/TLS 表见 `.artifacts/vps_migration_feasibility_20261009_01/NETWORK_RESULTS.md`、`DNS_TLS_RESULTS.md`，原始数据见 `TOKYO_NETWORK*.json`。本地 Windows 基线也留存，但未验证出口地区，不作为任何候选 VPS 地域的证据。DGS2、spot ticker/klines 等诊断样本不自动升级为业务依赖。

## 已发现的实际阻断与运行限制

| 项目 | 已核事实 | 影响与处置边界 |
|---|---|---|
| 新机无持续 FMZ 输入 | 正式ZIP没有旧→新同步器，env指向本机旧路径 | **切换阻断**。先完成只读同步与新机原子接收验收；不需要修改金融/交易逻辑 |
| 独立运行资产不在ZIP | 缺 KPF producer/vendor/service、GEX app/state、MAP独立deploy/config、旧LLM service/timer | **完整迁移阻断**。另冻结真实运行版本和状态，不能只解压工作台 |
| 旧机内存容量 | 总945.06MiB、可用428.96MiB；新工作台旧本地证明普通峰值733.7MiB、完整证明累计峰值911.4MiB | **在旧小机部署阻断**。采购后验真实共存峰值；swap不等价于RAM |
| KPF新日 | 10-08 zip+checksum404、生产service失败；10-07归档200 | 最新窗口持续更新阻断。保留旧合格bundle和失败证据；等待/确认来源发布，不伪造新日 |
| 原始审计卡未进入旧索引 | raw已有10-08 23:00的2.0.2卡；旧index最新仍10-07 00:57:22；本机HTTP内容与旧文件一致 | **审计消费更新阻断**。materializer尚在运行不能算验收成功；原因未证实。新机须验raw→index→阅读全链，回退旧静态页时明确该滞后 |
| 数据用途部分缺失 | 广义美元STALE，borrow未配置；FMZ bridge PARTIAL；GEX partial、SpaceGate STRUCTURE_BLOCKED | 限制对应结论和原门禁，不让独立阅读/审计失去作用；本轮不改TTL/门禁 |
| 控制域名普通网页异常 | node.fmz.com首页526，已有robot连接/导出仍持续 | **若迁robot需另验的风险**；不能据网页526判旧FMZ已停，也不能宣称新robot重连通过 |
| 版本和发布身份 | 2.6.3本地dirty RC，旧UI/MAP/data_engine字节不同 | 需要逐组件基线清单与发布许可；没有因网络成功继承旧发布授权 |

## 版本一致性

| 层 | 当前核验 | 证明边界 |
|---|---|---|
| 权威Git | xxproject；branch `codex/astra-signal-rating-v1`；HEAD `950631e0aff8f27179b3b2601776b823a793eaa4`；候选未提交 | HEAD不是含完整2.6.3修订的release commit；origin是helper镜像 |
| v2.6.3交付ZIP | `OBSERVATION-2.6.3-22c18ce92bee`；1,062,056B；SHA256 `a571dbdb3b0501490f5c997d4a0d719ab93fb6db201f8942920f26a3b1777511` | 84个manifest列出的文件hash全通过；canonical source graph全通过；production_release_approval=false |
| 包与canonical | manifest原字节相同；5个文件有已列明的路径便携化transform | 不能宣称每个包文件与canonical逐字节相同 |
| FMZ可见根目录/工作树/ZIP参考件 | demo_version 2.0.2；三者SHA256均 `87632e850d4bf8ccd75bab61bec92d54c0ab5e6c20751e36c4f73d4ebd2c7ab9` | 交付字节一致；包不安装FMZ实例 |
| 服务器原生/审计 | 最新EvaluationSnapshot和DecisionSnapshot为nrd.schema.v1.0.0、demo_version2.0.2；最新固定分析卡identity.strategy_version2.0.2 | 有原生数据版本证据；未证明运行进程实际加载源码hash |
| 服务器页面消费 | raw固定卡10-08 23:00；index当前最新卡10-07 00:57:22 | 原始生产版本不能替代页面消费更新证明；旧页最新索引并未追上原始卡 |
| 原生关键上下文 | session_context、clock_window、backtest_delta_pp、validation_basis、temporal_durability均存在；本层无compat_backfill | 证明原生上下文存在，不代表交易资格/经济有效 |
| 旧UI | /opt/signal-audit/VERSION.json仍2026-09-23、producer_version元数据1.6.2；无2.6.3manifest，无workbench服务/env | 2.6.3没有在该服务器部署；旧静态元数据不能覆盖真实FMZ版本 |
| 独立MAP/data_engine | 远端MAP server/sources/projection及data_engine与当前local hash均不同；KPF light hash相同 | 不能把本地2.6.3包当作原线上运行栈的byte clone；差异表已保存 |

证据：`PACKAGE_CONSISTENCY.json`、`VERSION_BRIDGE.json`、`OLD_SOURCE_STATE.json`、`SERVER_SOURCE_COMPARISON.json`、`STATIC_PIPELINE_STATE.json`、`STATIC_PIPELINE_RECHECK.json`。原始卡 identity 具体位于 `OLD_SOURCE_STATE.json.latest_audit_metadata.identity` 和 `STATIC_PIPELINE_RECHECK.json.raw_latest`；不能用 snapshot schema 代替 card identity 证明。用户所称“正式包”在本报告指现有交付ZIP；它的发布清单仍明确是本地候选状态。

## 所需资源与选型

**建议预算规格：x86_64、4 vCPU、8 GB RAM、160 GB SSD/NVMe、固定IPv4+IPv6、出站443和到旧机SSH22、Ubuntu LTS/Python3.11+。** 保留现有内存/磁盘/锁定采样预算，新的容量只提供并存和峰值余量。4 GB可以作为观测面试验下限，不作为全部旧服务、回填和完整proof同时运行的已验承诺；1 GB方案排除。若要并行回填、较多使用者或长期CPU持续高负载，考虑16GB/稳定CPU资源，再按实测选型。

| 资源 | 实测 / 推算依据 | 采购和验收安排 |
|---|---|---|
| RAM | 工作台本地常态约341–449MiB，峰值911.4MiB；旧GEX当前约55MiB，其service cap600M；MAP128M、KPF240M、data-engine384M、materialize128M | 8GB留OS、缓存、完整proof和并存余量；这些是本地实测/旧服务caps，不是新机RSS实测 |
| CPU | 旧机2CPU，load约2.20/2.00/1.92；部分负载是I/O，不能据load推断计算饱和原因 | 4vCPU候选；验采集耗时、CPU steal/credits和UI请求延迟，不只看CPU标称 |
| 磁盘 | 旧机总39.14GiB、空11.85GiB；KPF1.22GB、FMZ logs0.94GB、audit-tools0.68GB、MAP/data state各约15–18MB | 160GB足以容纳现有资产、迁移双副本和增长余量；无需原样复制无关WordPress/MySQL资产 |
| 证据增长 | locked总预算256MiB/1440样本；旧回执测得约148KB/增量点，未封顶4张每分钟30日约25.6GB | 当前cap会先暂停采样；不能把8GB/160GB采购当作已授权提高cap/延长保留 |
| 网络 | 每个current_map约8.3MB、native ring约9.1MB；当前预览60s gzip拉取 | 按实际压缩字节量计费；未压缩17.4MB每分钟30日约752GB是上界估算，须测压缩率/增长 |
| 系统与TLS | 旧机是Bitnami Apache；新包提供private proxy示例，继承现有认证 | 私有TLS/vhost、会话cookie、loopback8895和GEX私有接入另验；不向公网直接开放工作台/裸8000 |

选型候选：Hetzner CPX32为4vCPU/8GB/160GB，欧洲官方调价表为€35.49/月，未含VAT/IPv4、备份等；Helsinki为官方可选地点。AWS Lightsail8GB为2vCPU/160GB/$44/月，4vCPU方案是16GB/320GB/$84/月，适合作AWS备选；它不是4vCPU/8GB方案。价格和库存以购机时控制台为准，未下单。[Hetzner规格](https://www.hetzner.com/european-cloud/)、[地点](https://docs.hetzner.com/cloud/general/locations/)、[当前调价表](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/)、[AWS官方套餐](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-bundles.html)。

## 旧FMZ→新服务器路径与回退安排

```text
旧FMZ 2.0.2 / 旧robot（继续运行）
  ├─ signal_review.jsonl ─→ 旧materializer ─→ 旧静态审计页（继续）
  ├─ snapshots.jsonl（atomic 12记录ring，约300s）
  └─ 旧MAP/KPF/GEX独立producer与immutable bundles
          │
          └─ 新机专用SSH只读pull / 原子接收（待实现、待验收）
                 ├─ snapshots输入与current_map、所引用的不可变证据
                 ├─ 原始卡/既有sidecar历史（只读，不启动旧自动timer）
                 └─ 新机工作台 ─→ 私有TLS阅读/锁定/按需证据
```

现有 `.artifacts/.../serve_current.py` 在Windows上每60s读取旧机两份文件，用gzip回传、SHA校验后本地atomic替换；实测SSH读成功，producer文件时间/hash前进。它不是新服生产服务，不在ZIP中。新桥须使用专用受限只读账号、固定SSH host key、超时和失败记录；同一hash不反复归档，保留source clock、另记receiver time，不能把复制时间填回源时间。不同clock的FMZ/MAP不能包装成同一cutoff；引用KPF bundle时必须先传完整不可变目录，再替换pointer。JSONL卡可增量同步，rolling snapshots须原子整份接收，不能对ring做假追加。

切换只影响观测面入口。旧FMZ的GEX默认base仍指旧IP；第一阶段保留旧GEX服务和参数，避免提前断开。以后若GEX或robot也迁移，属于独立迁移步骤，需平台控制面和GEX私有TLS路径验收，不在本轮执行。FMZ官方说明策略运行在robot所在服务器，日志在托管者目录保存，平台网页不是JSONL生产端。[FMZ官方托管者说明](https://www.fmz.com/lang/zh/user-guide/%E6%89%98%E7%AE%A1%E8%80%85)。

迁移前要另外备份：完整signal_review及native/cache/state、原卡/index、MAP current/previous+data_engine、KPF raw校验与published不可变bundles、GEX cache/rank/history、sidecar/usage/exclusion ledgers、工作台locked baseline/delta/index/daily/review状态。真实env/密钥通过受保护途径人工配置，不进Git/ZIP/本报告。**本轮脱敏JSON不是可恢复备份。** 旧机没有工作台服务，实际locked状态还在本地8919对应state目录，也必须纳入新机状态导入，不可只搬云机目录。

建议切换安排与回退（尚未执行）：

1. 按已审hash安装新机文件，自动LLM保持0/disabled，旧机所有服务保持运行。独立服务版本逐个冻结；先验证桥与来源更新，再做workbench压力与private TLS验收。
2. 至少24h来源/桥shadow观察，检查原始源时钟、连续导出、失败率与固定23:00卡，逐层证明原始卡→materializer→index→新页面追上同一张卡；不能仅凭service active或HTTP200通过。不把慢ETF/链上/FRED变成高频请求。关键行情短测≥30次，并以其原有timeout记录分位延迟；按现有采集cadence做持续采样，不触发模型。
3. 所有权明确：只让一处工作台持久化写locked/review/usage；旧源与新观测面不建立双向状态写。切换本地/新机locked写入口前，短暂冻结这一个写入口，备份当时最新state并验hash，然后转移写所有者；旧FMZ继续采集。
4. 观测入口切换前降低DNS TTL并保存旧路由、旧证书/代理配置及版本清单；只有本版前端浏览器验收和用户明确“当前本地页面可推送”后，才具备后续发布动作门禁。本轮只形成方案，无发布操作。
5. 若新机失败，把观测入口恢复旧审计页或原本地8919观测面，旧FMZ无需停机。旧审计索引当前滞后，须明确显示资料时间，不能作为CURRENT已恢复的证明。回退前封存**切换期间最新**locked/review/daily/usage，不用旧备份覆盖新增证据；新机持续状态先保存，只停止/隔离新机写入口，避免双写。
6. 若需旧reader消费新delta，调用已备的`export_full_compatible.py`导出到新目录，校验t0、point/hash、停止状态与引用；旧reader不直接读live delta。验证恢复演练后再恢复唯一writer；自动LLM timer不随rollback自动启用。

跨机环节的建议门禁：新机raw hash与旧导出相同；同一native ring内部身份完整；增量接收时间与源时间分别保留；t0不可变、重启后latest/停跟状态可重放；断桥显示准确缺失并保留可读资料；4 active锁单、常规刷新与一次完整proof并存时无OOM/交换抖动，采集和UI仍满足实际用途；回退时新增样本不丢失。

如果欧洲出现403/451/明确平台拒绝，该来源停止测试并记录原响应，先确认用途/地区许可；只有许可明确的网络/供应商问题才做UK/Singapore相同探测。KPF404、美元源旧日、GEX缺字段和FMZ node网页526都不能靠轮换IP解释为已解决。当前没有欧洲失通证据，也没有理由批量租多个地区。

## 证据与独立复核

证据目录：`.artifacts/vps_migration_feasibility_20261009_01/`。主要文件：`OLD_HOST_STATE.json`、`OLD_SOURCE_STATE.json`、`SOURCE_CLOCKS.json`、`FMZ_CONTROL_HOSTS.json`、`VERSION_BRIDGE.json`、`SERVER_SOURCE_COMPARISON.json`、`PACKAGE_CONSISTENCY.json`、`STATIC_PIPELINE_STATE.json`、`STATIC_PIPELINE_RECHECK.json`、`TOKYO_NETWORK*.json`、`NETWORK_RESULTS.md`、`DNS_TLS_RESULTS.md`、`EVIDENCE_SUMMARY.json`。`probe_network.py`和`supplement_endpoints.json`可在将来目标机复用；固定到期合同与归档日期须在执行前替换为当日真正运行输入。

本轮使用项目固定角色：explorer `migration_dependencies`核外部调用闭包，explorer `locked_lifecycle_explorer`核FMZ/服务/包传递边界，reviewer `rc_reviewer`独立检查证据与结论。主agent重新核hash、SSH脱敏现网、DNS/TLS/HTTP、来源clock与官方政策，并修正DeepSeek旧别名可用的判断。没有以子agent静态结论替代现网证明。
