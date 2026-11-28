# xxproject 执行层权威实盘接入计划书

- **现行线上基线**：`4f97732bb047648110a14bd01abbdfa5dc0a2f6b`（`xxproject-server-4f97732bb047-2c9ce4f10b92`，`--mode SHADOW_ONLY`，真实交易写权限关闭）。
- **行为契约依据**：`live-exec-behavior@1.2.0`（核心纯逻辑与 4 表 SQLite 事实账本；Testnet 已退出当前规划与交付路线）。
- **唯一执行路线**：**`阶段 1：自然影子轮取证` → `阶段 2：准备 Deribit 主网接入` → `阶段 3：受控金额实盘验证`**。
- **实盘接入准入信号**：面向受控实盘的正式 Deribit 主网 API Key（独立受控资金子账户）通过既有宿主机安全渠道配置到目标执行环境并确认只读可用，作为启动主网实盘代码接入与受控实盘验证的正式准入信号。

---

## 1. 唯一执行路线与阶段门禁

| 阶段 | 核心目标 | 准入门槛与放行信号 | 退出 / 阻塞口径 |
|---|---|---|---|
| **阶段 1：自然影子轮取证**（当前） | 保持 `SHADOW_ONLY`，以 `16:00 UTC` 自然牌局证实发牌 → 推理 → `link.json` 影子锁 → 5 分钟巡检 → 持有复评 → `08:00 UTC` 交割全链路 | 已部署 `4f97732b`（`2750`/`0640` 跨账号权限修复与 `9/9` OS 隔离通过） | 必须取得新自然轮原生 `link.json` 实证；同步补齐持有复评 `360s` 超时上限放宽 |
| **阶段 2：准备 Deribit 主网接入** | 在独立变更中补齐 `LIVE` 主网允许模式、主网写单适配、健康/前端扩展与宿主机独立配置模板 | 1) 阶段 1 自然影子轮端到端通过；2) **正式主网 API Key 注入目标环境并确认可用（准入信号）** | 现版本缺主网模式与写路径，仅改环境变量无法进入主网写单 |
| **阶段 3：受控金额实盘验证** | 以最小固定单腿 `0.1 BTC`、`MAX_EPISODES = 1` 在主网隔离子账户完成下单、成交、撤单、仓位与交割对账 | 1) 阶段 2 改造通过回归与独立审查；2) 前端如有改动需经浏览器复核与 `当前本地页面可推送`；3) 人工批准受控限额后解除 `entry_armed=0` | 任一账本差额（`diff_btc != "0"`）、未归因外部仓位或恢复冲突即停写接管 |

---

## 2. 阶段 1 待闭合运维缺口：持有复评超时

- **现状**：线上 `/etc/signal-audit/workbench-v20.env` 仍设 `LLM_TIMEOUT_SEC=360`，未设 `ASTRA_PLAYER_HOLD_TIMEOUT_SEC`；[astra_player_tracking_v13.py](../../tools/astra_player_tracking_v13.py#L1306-L1320) 虽将初始核保抬升至 `600s`，但持有复评仍受 `hold_timeout_s = min(timeout_s, 360.0)` 封顶。
- **处置**：在进入阶段 2 前，显式放宽持有复评超时的配置与读取口径（对齐 `astra-player-sync.service` `TimeoutStartSec=900`），消除 `deepseek-v4-pro` 高思考负载下盘中 `AI_REVOKE` 复评超时风险。

---

## 3. 阶段 2：现版本无主网路径，接入必须补齐的代码与环境项

当前代码库与线上服务**仅允许 `SHADOW_ONLY` / `DRY_RUN` / 已废弃的 `TESTNET` 占位逻辑**，进入主网受控实盘前必须在独立提交中补齐以下 5 项改造（常规 `SHADOW_ONLY` 发布继续保持 `--mode SHADOW_ONLY` 默认锁定）：

| 落点 | 现版本阻断行为 | 必须补齐的最小改动 |
|---|---|---|
| [astra_exec_contracts_v1.py](../../tools/astra_exec_contracts_v1.py#L18) | `ALLOWED_MODES` 不含主网实盘模式 | 新增受控主网模式枚举值（如 `LIVE`），保持 `SHADOW_ONLY` 为唯一默认值 |
| [astra_execution_coordinator_v1.py](../../tools/astra_execution_coordinator_v1.py#L170-L180) | `validate_endpoint_for_mode()` 对 `www.deribit.com` 写模式抛 `MAINNET_WRITES_FORBIDDEN_IN_PRELIVE` | 将 `LIVE` 严格绑定至 `https://www.deribit.com`（拒绝指向其他主机），并强校验非空 `subaccount_id`、非空主网凭证与主网 `env_tag`（如 `live`，禁用 `tn`） |
| [astra_execution_coordinator_v1.py](../../tools/astra_execution_coordinator_v1.py#L382-L383) | `DeribitRestAdapter._allow_private_writes()`、`_dcod_confirmed` (`:1587`) 与 `writes_permitted_by_mode()` (`:1639`) 硬编码锁定 `TESTNET` | 扩展允许受控 `LIVE + www.deribit.com` 私有写请求与强制 `disable_cancel_on_disconnect` 预检；失败即 fail-closed 禁写 |
| [xxproject_server_health_v1.py](../../tools/xxproject_server_health_v1.py#L809) 与 [execution.js](../../deploy/astra_workbench_v3/execution.js) | 健康脚本 `--expected-exec-mode` 未含 `LIVE`；前端停写推断分支写死 `TESTNET` | 健康脚本支持校验 `LIVE` 模式；前端同步覆盖 `LIVE/MAINNET` 停写接管推断（按规走本地浏览器审查与 `当前本地页面可推送`） |
| 宿主机环境与 systemd drop-in | 线上 `/etc/signal-audit/exec-coordinator.env` 仍为 `ASTRA_EXEC_ENV_TAG=tn` 且无密钥；发布 unit 锁定 `--mode SHADOW_ONLY` | 通过受控宿主机环境配置与专用 drop-in 接入主网参数；**凭证绝不进入 Git、发布包、`workbench-v20.env` 或 `llm.env`** |

### 主网凭证安全配置渠道与准入核验约定

1. **安全通道**：正式主网独立子账户 `DERIBIT_EXEC_CLIENT_ID`、`DERIBIT_EXEC_CLIENT_SECRET` 与 `ASTRA_EXEC_SUBACCOUNT_ID` 仅写入目标宿主机 `root:root 0600` 的 `/etc/signal-audit/exec-coordinator.env`（或 systemd `LoadCredential`），由独立用户 `astra-exec` 运行协调器进程加载；`bitnami` 与 `astra-workbench` 物理不可读。
2. **回执纪律**：任何联调、部署与验收回执只记录“已注入/未注入、子账户只读探针状态、余额与 DCOD 可用性结果”，严禁输出 API Key、Secret、Bearer Token 或 `/etc/signal-audit/` 明文内容。

---

## 4. 阶段 3：受控金额实盘最小验证矩阵

完成阶段 2 改造并确认主网 API Key 可用后，先在 `entry_armed=0` 下完成**只读准入核验**（子账户身份、SM/PM 模式、现金门槛：SM `>= 0.025 BTC` 或 PM `>= 0.015 BTC`、外部仓位 `= 0`、外部挂单 `= 0`、`public/time` 偏差及 `disable_cancel_on_disconnect`），再以固定最小单腿 `0.1 BTC`（`MAX_EPISODES = 1`、严守 `ExecConfig` 默认底线 `C_min = 0.0008 BTC/BTC` 与 `0.0060 BTC/BTC` 单腿上限）验证 5 项实盘闭环：

1. **下单（`EP -> ES`）**：保护腿先买 `BUY Limit IOC` -> 确认真实持仓覆盖 -> 短腿再卖 `SELL Limit IOC`，实测主网阶梯 `tick_size_steps`、信用门槛与不可逆 `entry_closed`。
2. **成交与止盈（`TP`）**：`USER_TRADE` 与 `TX_LOG` 按 `trade_id` 去重入账；封口同拍预挂唯一 `85% GTC BUY (reduce_only=true)`（正常波动静默不动；低信用按契约钳位 `0.0001` 或显式告警），止盈成交后重建账本 `diff_btc == "0"`。
3. **撤单与主动退出（`CANCEL / AI_REVOKE / EXIT_ALL`）**：触发退出后先 `cancel_by_label` 清零在途单与活动 `TP`，确认后再发 `RX BUY Limit IOC (reduce_only=true)`（若遇 `10009` 现金不足保持保护腿对冲并置红灯，禁卖保护腿），短腿归零后对保护腿按 `bid >= 0.0002` 发 `LX` 回收或转 `RESIDUAL_HOLD`。
4. **仓位核对与重连/重启恢复**：逐拍核对 `get_positions(currency="BTC")`（含未归因外部仓位与 Stage-2 期货探测）、`open_orders`、分页 `order_history` 与 `user_trades` 一致；验证协调器进程重启后写锁、持久化控制态及 `UNKNOWN` 零重复下单恢复。
5. **到期交割对账（`OTM` / 2026-08-01 `ITM` 两阶段交割）**：过 `08:00 UTC` 后全量读取 `private/get_transaction_log`（不以 `query="option"` 漏掉过渡期货），完成 OTM `expiry` 零费结清及（真实触及时）ITM `exercise`/`assignment -> delivery` 两阶段净额结清至 `CASHFLOW_SETTLED`（`diff_btc == "0"`）。

---

## 5. 现有金额上限与三级人工停单落位

### 5.1 金额与仓位硬上限

- **启动零敞口默认**：`/etc/signal-audit/exec-coordinator.env` 与 SQLite `meta` 硬默认 `entry_armed=false (0)`、`budget_cap_btc=0`、`max_qty_btc=0.1`。
- **单局与周期闭环限额**：初期 `handle_capital_policy()` 强制单腿量等于 `ExecConfig.initial_qty_btc = 0.1 BTC`；并发槽锁定 `MAX_EPISODES = 1`；周期有效预算 `B_eff = max(0, min(B_cap, B_cap + PnL_settled - PnL_grant_baseline))` 对已结清亏损等额扣减且不超 `budget_cap_btc`；每次启用或调高额度实时校验主网子账户 `available_funds_btc >= budget_cap_btc` 与 `balance_btc >= budget_cap_btc`，叠加非线性压力门 $L_{\text{stress}}$ 与 SM/PM `2.5x` 维持保证金缓冲。
- **专属控制通道**：仅宿主机 `root` / `astra-exec` 可经 Unix Socket `/run/astra-exec/control.sock`（内核 `SO_PEERCRED` 校验 UID $\in \{0, \text{uid}(\text{astra-exec})\}$ + 32 字节 `/etc/signal-audit/exec-control.token`）调用 `POST /capital-policy`，每步写入不可变审计键 `capital_auth_audit:{epoch}`；Workbench v3 与 `bitnami` 全程只读（`POST /api/workbench/execution/*` 返回 `403`）。

### 5.2 三级人工停单（`/run/astra-exec/control.sock`）

全部停单指令原子置位内存中断、唤醒主循环并经 SQLite `synchronous=FULL` `fsync` 落盘，服务重启后保持有效：

| 级别 | 控制接口 | 实际效果 | 恢复方式 |
|---|---|---|---|
| **1. 停新增开仓** | `POST /capital-policy {"entry_armed": false}` | 立即阻断新局准入与未封口加仓；保留已成交持仓的 `TP`、`AI_REVOKE`、`LX` 与到期对账 | 重新授权 `entry_armed=true` |
| **2. 强制主动平仓** | `POST /exit-all` | 不可逆锁存 `exit_latched=1`，先撤全部在途单，再以二段 `RX IOC` 清退短腿并处置保护腿残值 | 本局不可撤销，待 `CASHFLOW_SETTLED` 结清 |
| **3. 单向紧急停写** | `POST /disable-writes` | 持久化单向 `writes_disabled=true`，立即停止一切交易所写请求，置顶红灯 `manual_takeover_required=true` 并暴露在途挂单交人工登录 Deribit 接管 | **禁止程序自动恢复**，须人工核清子账户后处置 |

---

## 6. 当前明确剩余缺口汇总

1. **自然影子轮实证未取**：等待 `16:00 UTC` 自然轮验收跨账号 `link.json` 成功锁定、5 分钟巡检与 `08:00 UTC` 交割闭环。
2. **持有复评 `360s` 超时上限未解除**：`workbench-v20.env` 的 `LLM_TIMEOUT_SEC=360` 与 `_llm_settings()` 封顶尚待放宽。
3. **正式主网 API Key 准入信号未触发**：`xx-eu` 当前 `/etc/signal-audit/exec-coordinator.env` 仍为 `tn` 占位配置，尚未配置并验证主网受控子账户凭证。
4. **主网 `LIVE` 允许模式与写单路径未开发**：`ALLOWED_MODES`、`validate_endpoint_for_mode`、`_allow_private_writes`、`writes_permitted_by_mode`、健康脚本与 `execution.js` 停写推断待在阶段 2 单独立项补齐并审查。
5. **受控 `0.1 BTC` 主网实盘最小验证未跑**：上述 1–4 项闭合后，方可执行首次主网下单、成交、撤单、仓位核对与交割对账实测。
