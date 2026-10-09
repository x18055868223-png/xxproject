> 当前阶段入口：[v2.6.3-stage.1 与 EU VPS 准备](docs/astra/V263_STAGE_EU_VPS_PREPARATION_20261009.md)。本轮按用户授权发布观测候选工作分支及阶段标记，新机完成依赖/资产准备；生产入口尚未切换。

# 中性回路整合工程备份标记

以下为 10 月 5 日前序候选 **WPEM-CONSUMER-RC-20261005-02** 的历史记录，版本以[清单](deploy/signal_audit/release_candidate_manifest.json)为准，本轮消费与展示修复见[验收82](docs/astra/82_WPEM消费链与展示链最小修复及同版验收_20261005.md)。7 组 MUST 修复及 358 项回归已通过；新版 evidence@1.0.1 / input@2.5.1 已进入实际 LLM 请求，输出协议仍为 bounded_claim_ids@2.5.0。3 次实际 HTTP 响应均未通过严格校验，实际 LLM→两问的成功链仍未通过 RC 验收。默认2.4.7兼容、风险 BLOCKED、μ/M null 保留；未推送、合并或部署新工作台。FMZ 本体2.0.2由用户部署的既有事实不因本轮改变。

下面保留2026-09-09等历史阶段原文；其中的“当前”与授权仅描述当时阶段，不代表本轮版本或发布授权。金融研究结论和历史证据边界继续以Astra接管入口的对应研究协议为准。

理论沿革、总体证据评级与当前证据边界：[Astra 接管入口](docs/astra/README.md)。

> **当前工作状态：Astra v2 中性纸面定稿，用户已授权推送当前分支**（工作分支 `codex/astra-signal-rating-v1`）
> 当前入口：[docs/astra/07_总体证据评级与单次综合评审_v2.md](docs/astra/07_总体证据评级与单次综合评审_v2.md)
> 更新时间：**2026-09-09（UTC+8）** ｜ 本次已完成页面审阅并取得推送授权；服务器部署另行验收。后续页面变更仍需用户确认。
> 项目权威仓库：`xxproject` → https://github.com/x18055868223-png/xxproject
>
> 当前链路裁定：LLM 复核使用 DeepSeek `deepseek-v4-flash`，新卡协议为 `signal_llm_review@2.0.0` / `signal_llm_review_prompt@2.0.1`，调用模式 `single_evidence_v2`。FMZ producer 候选仍为 `demo_version=1.6.0`，此前 v2 之前的记录哈希不因本轮文档或页面整理改变。本地实现与测试不等于 FMZ 已部署或服务器已验收。

本仓库是推送到 `x18055868223-png/xxproject` 的工程级快照。它不是单一服务仓库，而是把当前运行链路拆成可审计、可恢复、可继续整理的模块集合。

后续更新先读[版本兼容与数据复用约定](docs/astra/10_版本兼容与数据复用约定.md)。2026-09-09服务器最新真实卡仍为1.5.7，本分支FMZ候选为1.6.0；Git推送不等于服务器或FMZ已升级。

## 当前工程链路

| 模块 | 当前定位 | 最新入口 |
| --- | --- | --- |
| FMZ 信号层本体 | 只读观察与信号生成；输出短推、状态栏、`signal_review.jsonl` 审计卡 | `demo/最新交付物/neutral_regulation_demo_fmz.py` |
| GEX Monitor API | 策略服务器 `/v1/info` 增强端点；含 netGEX、IV/RV、P/C、flow 与 30 日滚动 rank | `05_GEX监控API_数据增强接口/` |
| 审计前端 | 静态页面 + `signal_cards/index.json` + 单卡 JSON；新卡展示 Astra v2 总体证据评级，旧卡按历史协议阅读 | `deploy/signal_audit/frontend/` |
| LLM 复核旁路 | DeepSeek 单次总体证据评审；生成 `signal_llm_reviews.jsonl` sidecar，再由 materializer 合并 | `tools/signal_llm_review.py`、`tools/signal_llm_review_entry.py`、`tools/signal_evidence_v2.py`、`tools/signal_review_v2.py`、`tools/signal_review_v2_runtime.py`、`deploy/signal_audit/` |
| 执行层 | Deribit 垂直价差人工审计门执行链；当前保留为未正式测试启用的默认安全交付物 | `demo/最新交付物/spm_manual_gate_execution_fmz.py` |
| 服务器自检 | 用于定位 FMZ JSONL、GEX API、审计页面、LLM sidecar、systemd timer 哪一层异常 | `tools/server_self_check_signal_stack.sh` |

## 当前版本锚点

- 信号层 producer 候选：`demo_version=1.6.0`，`schema_version=nrd.schema.v1.0.0`；只读观察边界、短推、JSONL 审计卡和执行权限隔离仍需分别核验。
- 执行层：`STRATEGY_VERSION=3.0.0-manual-gate`，`ALLOW_ENTRY_TRADING/ALLOW_EXIT_TRADING/ALLOW_HEDGE_TRADING/ALLOW_TRADING` 默认关闭，`DRY_RUN_PASSED=False`。
- GEX API：`gexmonitorapi=0.2.1`，rank 窗口为 `rolling_30d_or_available`。
- LLM 复核当前新卡协议：DeepSeek `deepseek-v4-flash`，`signal_llm_review@2.0.0` / `signal_llm_review_prompt@2.0.1`，`review_mode=single_evidence_v2`；历史 1.x 旁路只作为旧卡或离线资料读取。
- 审计前端：`signal_cards/index.json` + 单卡 JSON + `fallback.js`，materializer 合并 LLM sidecar；当前阅读入口以 Astra v2 总体证据评级为准，旧方向、旧分数和旧结论不并列为新卡当前结论。
- 文档收纳：05 与 `deploy/signal_audit` 已补齐 `因子文档/`、中文语义入口和前端 `VERSION.json`，按 00-04 的模块阅读惯例收纳。

## 历史锚点：r3.3.6 信号耐用性层

`r3.3.6` 是 2026-07-05 的信号耐用性层历史工作版本，目标分支曾为 `codex/signal-durability-r3.3.6`。它在 r3.3.5 全量备份之上叠加：FMZ 信号层 `demo_version=1.5.2` 新增 AUDIT_ONLY `signal_durability` 总耐用性层、`comfort_window` 舒适区 tag、`price_anchor_durability` 四层价格锚耐用性解释；短推增加 `耐72/DUR T2C` 类紧凑摘要；materializer 和前端支持 producer-native 字段与旧卡显式兼容回填。执行层和交易门未改动。

该锚点用于追溯历史，不代表当前 Astra v2 的运行协议，也不代表当前 FMZ 或服务器已经部署到该历史版本或本轮 v2。

## 快速排障入口

在策略服务器上，更新到本版本后可执行：

```bash
cd /opt/repos/xxproject
bash tools/server_self_check_signal_stack.sh
```

默认是只读检查。需要主动触发 LLM 和 materializer oneshot 时再执行：

```bash
sudo bash tools/server_self_check_signal_stack.sh --run-oneshots
```

## 使用顺序

1. 先读 `00_总纲/中性回路工程总纲_v2026.06.19-r2.2.md`。
2. 再读 `99_工程资产索引/README.md` 区分当前运行资产、未启用资产和历史/失效资产。
3. 部署 FMZ 时取 `demo/最新交付物/neutral_regulation_demo_fmz.py`，不要从历史快照目录复制。
4. GEX API 先读 `05_GEX监控API_数据增强接口/因子文档/00_GEX监控API总览.md`，再看源码和部署文档。
5. 审计页面先读 `deploy/signal_audit/因子文档/00_审计部署总览.md`，部署以 `deploy/signal_audit/install_or_update.sh` 为准。

## 维护规则

- 真实 key、token、`.env`、服务器运行 JSONL、缓存和虚拟环境不进入本仓库。
- FMZ 信号层只负责生成审计 JSONL 和短推；Astra v2 的 DeepSeek LLM 复核在服务器旁路层执行，不创建交易许可。
- 执行层默认全空跑，除非经过单独实盘验证流程，不得把本仓库备份视为交易启用。
- 每次服务器链路发生变化，都要同步更新 `tools/server_self_check_signal_stack.sh` 和 `99_工程资产索引/README.md`。
