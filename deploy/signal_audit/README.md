# Astra v2.0 空间约束工作台

本目录提供只读市场工作台的页面、服务模板和部署包。工作台的当前事实只来自 FMZ `EvaluationSnapshot.factor_snapshot.space_gate` 与同一版本的公开盘口承保快照。旧 `signal_cards` JSON、旧 LLM 正文与预算记录仍是不可改写的历史证据；页面历史区可打开原始 JSON，但旧审计 reader 与自动 LLM 轮询不再是 v2.0 入口。

工作流为：当前 FMZ 事实 → 确定性 SpaceGate → 并列 Put/Call 合同与补偿账 → 人工点击“锁定候选并综合评审” → 人工决定。SpaceGate 只表示结构可研究；`M_ref=c−μ_ref−f` 只表示相对冻结风险参考的余量。两者都不授予交易许可，也不证明经济 edge。缺 producer-native SpaceGate、陈旧事实、无合格 μ_ref 或 `M_ref≤0` 时，主评审入口关闭。页面、状态切换邮件、NR 事件和每日 23:00 UTC+8 快照均不调用 LLM。

当前保单只用开仓前可知的合同与公开盘口形成理论参考，不补实际成交、持仓、账户保证金、滑点或提前退出账。两侧各取剩余到期时间最接近 24 小时的同一期号、1 BTC、精确 2,000 USD 宽度；卖出腿取同侧绝对 Delta 最接近 0.35、且不超过 0.5 的合格合约，缺可信 Delta 即保留缺口。主卡净信用为卖出腿权利金减保护腿成本，再减两腿理论建仓手续费；每项 BTC 金额的 USD 参考仅按同次估值现价折合。若另有到期交割费用，独立列示并进入 `M_ref` 的全费用口径。所有报价均未成交，到期盈亏平衡价只按本组合合同支付方程列真实根。

## 本地启动

入口是 `tools/astra_workbench_v20.py`，用仓库 Python 环境从项目根目录运行：

```text
python -m tools.astra_workbench_v20 --host 127.0.0.1 --port 8895
```

默认只绑定 loopback，静态页面由该服务同源提供；`GET /api/workbench` 返回当前门、两侧候选、补偿、缺口、迁移与最近 15 张每日快照。`POST /api/workbench/review` 需要服务端 Bearer token、同版 G1/G2 前提、600 秒全局冷却、幂等键、共享 HTTP 预算及单任务锁。服务端每次点击会重新取事实/报价并冻结候选，然后最多按既有正常评审规则使用一次请求和一次格式/传输恢复额度。失败结果、原始响应和预算消耗保留；不自动重发同一候选。

真实手动评审配置必须明确提供 `ASTRA_WORKBENCH_TOKEN`、`LLM_API_KEY` 和**已有共享账** `ASTRA_WORKBENCH_USAGE_LEDGER`。没有这些值时，工作台仍可只读运行，按钮关闭；不得创建一份新额度账代替既有共享额度。状态邮件只有 SMTP 参数齐全时才发送；无配置或发送失败会留下持久状态，不改变门或启动 LLM。机密只放在私有环境文件，不放入页面、仓库、部署包或项目记忆。

公开 HTTP 页面只展示只读内容。操作员 token 只能经 HTTPS 或 loopback 页面提交；随附 Apache/Nginx 示例对 HTTP POST 阻断，生产代理须经审计后再启用。FMZ、本服务和静态页面都没有下单接口。

## 打包与部署边界

数据引擎 v2.3 使用 `demo/shared/data_product_registry_v23.json`，打包时显式复制至工具目录；flat 安装同样必须具有这份有效目录，否则拒绝消费。产品按用途保留各自期限，页面不延长盘口、FMZ 或慢源资格。人工点击只刷新已选保单；网页 GET 只读缓存，不触发外部采集。

独立 `install_data_engine_v23.sh` 只安装 `/opt/astra-data-engine-v23` 的截面接收/保留维护服务，`data-engine-maintenance.timer` 每 300 秒运行；没有新端口、外部 HTTP、LLM 或前端替换。清理仅限精确日志名单，先冻结原生完整截面并读回核验，再删除过期压缩段；空当前快照保护历史快照段。该安装不执行完整工作台安装器，因此不改变旧生产 timers。FMZ 当前文件为空时明确报缺失，不从历史日志拼成实时截面。详见本轮协议60、验收61。

`package_signal_audit.ps1` 把静态资源、历史卡只读样本、工作台 Python 模块、systemd 模板和本轮发布准备文档放入独立包。复制完成后会在包根目录写入 `package-manifest.json`，记录包内文件的相对路径、字节数和 SHA-256；manifest 自身不参与哈希清单。manifest 还记录当前打包根的 Git HEAD、工作树 clean/dirty/not_git_root 状态、前端 cache token、`bounded_claim_ids@2.4.7` 协议身份、FMZ 2.0.1 参考版本，并在 zip 生成后输出 zip SHA-256。打包器不验证 release commit，`release_commit` 保持为空；docs 显式纳入、外部 `-FrontendArchive` 与最终提交一致性必须由发布流程另行核验。

`install_or_update.sh` 在任何 `install`、`rsync`、`chmod` 或 systemd 文件写入前完成输入文件预检，并先禁用现存 `signal-audit-materialize.timer` 与旧 `signal-audit-llm-review.timer`，停止相关 service 和 `astra-workbench-v20.service`。明确 `not-found` 的 unit 会跳过；`systemctl show` 失败、未知 `LoadState`、停止或禁用失败都会拒绝继续。`RUN_INITIAL_LLM_REVIEW=1` 已退役，会在副作用前直接失败；当前候选只能通过已认证的人工 POST 触发评审。

安装器的安全默认是安装文件但不启用/启动 materializer 或工作台；不会自动恢复旧自动 LLM timer。历史 sidecar 不删除。工作台状态、邮件冷却、人工评审冷却和每日快照都在 `ASTRA_WORKBENCH_STATE_DIR` 下持久保存，不包含在包内。每日快照后台完整保留，页面默认只展示最近 15 张。包内 `fmz/neutral_regulation_demo_fmz.py` 仅用于版本一致性参考，完整工作台安装器不部署 FMZ 本体，也不能证明服务器 FMZ 实例已运行该版本。

真正推送或服务器部署前，依根目录 `AGENTS.md` 必须先在本地浏览器审阅，并由用户对本版明确说“当前本地页面可推送”；还要核对 `xxproject` 远端、FMZ 根目录/工作树交付物、Git 版本与服务器真实卡身份。现有本地测试只证明代码路径，不能证明服务器 FMZ 已运行、自然卡已消费、实际 SMTP/LLM 成功或有经济收益。

本地最小验证：

```text
python -m pytest -q tests/test_fmz_20_space_gate.py
python -m pytest -q tests/test_astra_underwriting_fact_context_v20.py tests/test_astra_workbench_v20.py
python tests/test_astra_workbench_frontend.py
node --check deploy/signal_audit/frontend/app.js
```

回滚时先停工作台服务，再恢复上一个已验前端；FMZ 本体回滚须走它自己的授权和验收链路。保留状态账、失败、旧卡、LLM sidecar 和每日快照以供审计。不要借回滚自动重启旧自动 LLM timer。
