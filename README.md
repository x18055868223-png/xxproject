# 中性回路整合工程备份标记

> **当前最新版本：`r3.3.2`（备份标记 `NRD-XXPROJECT-BACKUP-2026.06.26-r3.3.2`）**
> 更新推送时间：**2026-06-26（UTC+8）** ｜ 提交：`c10b6ee`（信号审计前端封版）
> 最新版本位置：本仓库 `main` 分支 → https://github.com/x18055868223-png/xxproject
>
> **r3.3.2 更新**（在 r3.3.1 之上叠加，后端 `demo/`、执行层等保持不变）：信号审计前端封版 —— transition LLM 复核降级态与旧版 sidecar 改为醒目告警条，与通过态明显区分（SUPPRESS/未知态 fail-closed）；删除冗余决策死代码与潜伏 `sign_flip` 显示 bug；卡片时间统一 `Asia/Shanghai`；`evidence_raw_values.*` 源引用可跳转；funding 方向取值优先 `last_rate`。render-contract / materializer / llm 三套回归测试通过。

本仓库是推送到 `x18055868223-png/xxproject` 的工程级快照。它不是单一服务仓库，而是把当前运行链路拆成可审计、可恢复、可继续整理的模块集合。

## 当前运行链路

| 模块 | 当前定位 | 最新入口 |
| --- | --- | --- |
| FMZ 信号层本体 | 只读观察与信号生成；输出短推、状态栏、`signal_review.jsonl` 审计卡 | `demo/最新交付物/neutral_regulation_demo_fmz.py` |
| GEX Monitor API | 策略服务器 `/v1/info` 增强端点；含 netGEX、IV/RV、P/C、flow 与 30 日滚动 rank | `05_GEX监控API_数据增强接口/` |
| 审计前端 | 静态页面 + `signal_cards/index.json` + 单卡 JSON；展示 rank 与 LLM 复核 | `deploy/signal_audit/frontend/` |
| LLM 复核旁路 | Gemini 复核脚本 + systemd timer；生成 `signal_llm_reviews.jsonl` sidecar，再由 materializer 合并 | `tools/gemini_signal_llm_review.py`、`deploy/signal_audit/` |
| 执行层 | Deribit 垂直价差执行链；当前保留为未正式测试启用的全空跑交付物 | `demo/最新交付物/spm_calendar_protected_short_v1.py` |
| 服务器自检 | 用于定位 FMZ JSONL、GEX API、审计页面、LLM sidecar、systemd timer 哪一层异常 | `tools/server_self_check_signal_stack.sh` |

## 当前版本锚点

- 信号层：`demo_version=1.5.1`，`schema_version=nrd.schema.v1.0.0`；producer 原生 `SignalSessionPremiseDurabilityContext@1.0.0`、`SignalTransitionProducerAnchor@1.0.0`、`macro_shock`。
- 执行层：`STRATEGY_VERSION=2.5.0`，`ALLOW_ENTRY_TRADING/ALLOW_EXIT_TRADING/ALLOW_HEDGE_TRADING/ALLOW_TRADING` 默认关闭。
- GEX API：`gexmonitorapi=0.2.0`，rank 窗口为 `rolling_30d_or_available`。
- LLM 复核：Gemini `gemini-3.5-flash`，卡片级 `signal_llm_review@1.3.0` / `gemini_signal_review_prompt@1.3.0`，状态转移级 `signal_transition_llm_review@1.2.2`。
- 审计前端：`signal_cards/index.json` + 单卡 JSON + `fallback.js`，materializer 合并 LLM sidecar；前端封版 r3.3.2，缓存位 `?v=20260626b-transition-v1.2.2`，降级/旧版 transition 复核醒目告警。
- 文档收纳：05 与 `deploy/signal_audit` 已补齐 `因子文档/`、中文语义入口和前端 `VERSION.json`，按 00-04 的模块阅读惯例收纳。

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
- FMZ 信号层只负责生成审计 JSONL 和短推；Gemini LLM 复核在服务器旁路层执行。
- 执行层默认全空跑，除非经过单独实盘验证流程，不得把本仓库备份视为交易启用。
- 每次服务器链路发生变化，都要同步更新 `tools/server_self_check_signal_stack.sh` 和 `99_工程资产索引/README.md`。
