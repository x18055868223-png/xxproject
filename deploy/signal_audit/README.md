> 阶段观测候选见 [v2.6.3-stage.1 / EU VPS 准备](../../docs/astra/V263_STAGE_EU_VPS_PREPARATION_20261009.md)。phase_release_manifest 随包安装保留；runtime API identity 继续描述原始 frozen candidate graph。生产入口切换需另验。

# Astra 工作台与 WPEM 本地 Release Candidate

当前 WPEM 候选的版本权威为仓库/包根下 `deploy/signal_audit/release_candidate_manifest.json`。10 月 5 日的协议80–83和失败/成功调用回执保留各自版本事实，不作为当前观测版本的验收结果。本地候选、真实调用、浏览器审查和生产部署分别验收；本轮未批准生产发布。

本目录提供只读市场工作台的页面、服务模板和部署包。工作台的当前事实只来自 FMZ `EvaluationSnapshot.factor_snapshot.space_gate` 与同一版本的公开盘口承保快照。旧 `signal_cards` JSON、旧 LLM 正文与预算记录仍是不可改写的历史证据；页面历史区可打开原始 JSON，但旧审计 reader 与自动 LLM 轮询不再是 v2.0 入口。

工作流为：当前 FMZ 事实 → 确定性 SpaceGate → 并列 Put/Call 合同与补偿账 → 冻结同候选证据 → 人工点击“锁定候选并综合评审” → 人工决定。SpaceGate 只表示结构可研究；`M_ref=c−μ_ref−f` 只表示相对冻结风险参考的余量。两者都不授予交易许可，也不证明经济 edge。缺 producer-native SpaceGate、陈旧事实、无合格 μ_ref 或 `M_ref≤0` 时，现有主评审入口仍关闭。WPEM 接线不删除这些金融门。页面、状态切换邮件、NR 事件和每日 23:00 UTC+8 快照均不调用 LLM。

当前保单只用开仓前可知的合同与公开盘口形成理论参考，不补实际成交、持仓、账户保证金、滑点或提前退出账。两侧各取剩余到期时间最接近 24 小时的同一期号、1 BTC、精确 2,000 USD 宽度；卖出腿取同侧绝对 Delta 最接近 0.35、且不超过 0.5 的合格合约，缺可信 Delta 即保留缺口。主卡净信用为卖出腿权利金减保护腿成本，再减两腿理论建仓手续费；每项 BTC 金额的 USD 参考仅按同次估值现价折合。若另有到期交割费用，独立列示并进入 `M_ref` 的全费用口径。所有报价均未成交，到期盈亏平衡价只按本组合合同支付方程列真实根。

## 本地启动

入口是 `tools/astra_workbench_v20.py`，用仓库 Python 环境从项目根目录运行：

```text
python -m tools.astra_workbench_v20 --host 127.0.0.1 --port 8895
```

默认只绑定 loopback，静态页面由该服务同源提供；`GET /api/workbench` 返回当前门、两侧候选、补偿、缺口、迁移与最近 15 张每日快照。`POST /api/workbench/review` 需要服务端 Bearer token、同版 G1/G2 前提、600 秒全局冷却、幂等键、共享 HTTP 预算及单任务锁。服务端每次点击会重新取事实/报价并冻结候选，然后最多按既有正常评审规则使用一次请求和一次格式/传输恢复额度。失败结果、原始响应和预算消耗保留；不自动重发同一候选。

`ASTRA_WORKBENCH_WPEM_ENABLED` 默认 `0`，保留正式 `bounded_claim_ids@2.4.7` 兼容入口。明确选择 WPEM 候选环境时设为 `1`，当前路由输入为 `review_input@2.6.2`，输出为 `bounded_claim_ids@2.6.2`：保留有限目录选择，并增加模型有引用的 Q1/Q2 解释。旧输入和回执按原协议回放。主评审门未满足时，可在同一 POST 使用 `purpose="observation"` 读取本单已有资料；仍需鉴权、预算、冷却和证据校验，只产生禁止生产附着的研究回执。GET 不采集或调用 LLM，后台行情刷新不触发 LLM；600 秒是调用冷却，不是评审有效期。

主卡围绕 Q1 的具体支持、瑕疵、责任位置和窗口路径，以及 Q2 的当前补偿与反证展开。缺风险参考时，合同账和已知证据仍可说明；“预计赔付未量化”是证据边界，不能用一句缺 μ 占位替代两问的具体归因。当前旧风险资产保持 `REFERENCE_BLOCKED`，μ/M 均为 null，不填 0、不由 LLM 生成，也不因此改变主评审资格。

真实手动评审配置必须明确提供 `ASTRA_WORKBENCH_TOKEN`、`LLM_API_KEY` 和**已有共享账** `ASTRA_WORKBENCH_USAGE_LEDGER`。没有这些值时，工作台仍可只读运行，按钮关闭；不得创建一份新额度账代替既有共享额度。状态邮件只有 SMTP 参数齐全时才发送；无配置或发送失败会留下持久状态，不改变门或启动 LLM。机密只放在私有环境文件，不放入页面、仓库、部署包或项目记忆。

公开 HTTP 示例只展示静态审计 surface，不代理任何 `/api/workbench` 路径；锁单列表、证据投影和普通 GET 详情也属于私人工作台。操作员 token 只能经 HTTPS/private 入口或 loopback 页面提交；生产代理须经审计后再启用。FMZ、本服务和静态页面都没有下单接口。

## 打包与部署边界

数据引擎 v2.3 使用 `demo/shared/data_product_registry_v23.json`，打包时显式复制至工具目录；flat 安装同样必须具有这份有效目录，否则拒绝消费。产品按用途保留各自期限，页面不延长盘口、FMZ 或慢源资格。人工点击只刷新已选保单；网页 GET 只读缓存，不触发外部采集。

独立 `install_data_engine_v23.sh` 只安装 `/opt/astra-data-engine-v23` 的截面接收/保留维护服务，`data-engine-maintenance.timer` 每 300 秒运行；没有新端口、外部 HTTP、LLM 或前端替换。清理仅限精确日志名单，先冻结原生完整截面并读回核验，再删除过期压缩段；空当前快照保护历史快照段。该安装不执行完整工作台安装器，因此不改变旧生产 timers。FMZ 当前文件为空时明确报缺失，不从历史日志拼成实时截面。详见本轮协议60、验收61。

`package_signal_audit.ps1` 把静态资源、工作台及 WPEM 必需 Python 模块、systemd 模板、代理示例和最小兼容模块放入独立包；旧研究说明、开发产物目录、真实 env/state、私钥凭据和个人机器路径不进包。canonical `release_candidate_manifest.json` 按原字节复制至包内 `deploy/signal_audit/`；`package-manifest.json` 记录这份版本清单的路径、hash、release_id 和状态，并记录默认2.4.7与显式 WPEM 协议。包内各文件按相对路径、字节数和 SHA-256列示；package manifest自身不参与哈希清单，zip另输出 SHA-256。FMZ参考版本从实际交付物读取，当前为2.0.2。Git HEAD、工作树状态和前端 cache token同包保存；打包器不记录本机绝对路径、不验证release commit，也不证明服务器安装、真实LLM或完整E2E已经通过。

本轮包是本地审查候选，禁止直接当成生产发布。后续最小部署计划须单独核对新模块与 canonical manifest 的flat安装路径、当前服务差量、备份和回滚。若主 agent继续修改候选源码或manifest，应在最终回执前重新打包并核对相同字节。

`install_or_update.sh` 在任何 `install`、`rsync`、`chmod` 或 systemd 文件写入前完成输入文件预检。默认只准备静态文件、工具和私有配置模板，不检查、不停止、不禁用、不写入、不 reload、不启用也不启动 systemd unit。需要服务动作时必须显式设置 `APPLY_SIGNAL_AUDIT_SERVICE_ACTIONS=1`，此时才会先检查并 quiesce 现存 `signal-audit-materialize.timer`、旧 `signal-audit-llm-review.timer`、相关 service 和 `astra-workbench-v20.service`；明确 `not-found` 的 unit 会跳过，`systemctl show` 失败、未知 `LoadState`、停止或禁用失败都会拒绝继续。`RUN_INITIAL_LLM_REVIEW=1` 已退役，会在副作用前直接失败；当前候选只能通过已认证的人工 POST 触发评审。

安装器的安全默认是安装文件但不改变现有服务状态；不会自动恢复旧自动 LLM timer。历史 sidecar 不删除。工作台状态、邮件冷却、人工评审冷却和每日快照都在 `ASTRA_WORKBENCH_STATE_DIR` 下持久保存，不包含在包内。每日快照后台完整保留，页面默认只展示最近 15 张。包内 `fmz/neutral_regulation_demo_fmz.py` 仅用于版本一致性参考，完整工作台安装器不部署 FMZ 本体，也不能证明服务器 FMZ 实例已运行该版本。随包的 nginx HTTP 示例拒绝全部 workbench API；HTTPS/private snippet 才代理工作台路由，且需继承既有访问控制。Apache 静态 alias 不暴露 API，`apache-private-workbench.conf.example` 只供已有 TLS 和私人访问控制的 vhost 引入，并用 `AuthMerging And` 与既有认证合并。

真正推送或服务器部署前，依根目录 `AGENTS.md` 必须先在本地浏览器审阅，并由用户对本版明确说“当前本地页面可推送”；还要核对 `xxproject` 远端、FMZ 根目录/工作树交付物、Git 版本与服务器真实卡身份。现有本地测试只证明代码路径，不能证明服务器 FMZ 已运行、自然卡已消费、实际 SMTP/LLM 成功或有经济收益。

用户已确认 FMZ 绑定币安现货、币安期货和 Deribit 期权三个已有授权账户；源码hash仅用于版本核对，不阻碍本轮部署前准备。这些确认不授权账户接口、凭据读取、交易或修改账户绑定。本轮不修改自动LLM timer、不提交推送、不生产部署。P7本地使用审查后等待用户反馈；P8修订/冻结和P9部署计划尚未执行，生产部署仍需新的明确批准。R17、Round1—4及旧失败证据均保留。

本地最小验证：

```text
python -m pytest -q tests/test_fmz_20_space_gate.py
python -m pytest -q tests/test_astra_underwriting_fact_context_v20.py tests/test_astra_workbench_v20.py
python tests/test_astra_workbench_frontend.py
node --check deploy/signal_audit/frontend/app.js
```

回滚时先停工作台服务，再恢复上一个已验前端；FMZ 本体回滚须走它自己的授权和验收链路。保留状态账、失败、旧卡、LLM sidecar 和每日快照以供审计。不要借回滚自动重启旧自动 LLM timer。
