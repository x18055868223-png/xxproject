# 工程资产索引

版本标记：`NRD-XXPROJECT-BACKUP-2026.06.19-r2.2`

本目录用于区分当前运行资产、未启用资产、历史/失效资产、部署资产和验证入口。当前判断以代码、服务器部署脚本和最近实盘页面验证为准。

## 当前运行资产

| 类型 | 路径 | 说明 |
| --- | --- | --- |
| FMZ 信号层本体 | `demo/最新交付物/neutral_regulation_demo_fmz.py` | v1.3.0，只读观察；生成短推、状态栏、`signal_review.jsonl`；内置 GEX info 适配、rank 展示和 LLM 输入包构造；不在 FMZ 主循环调用 LLM |
| GEX API | `05_GEX监控API_数据增强接口/` | FastAPI 服务；`/v1/info` 返回 GEX/Gamma/IV-RV/P-C/flow，并累积本地历史计算 rank 分位 |
| 审计静态页面 | `deploy/signal_audit/frontend/` | 读取 `signal_cards/index.json` 与单卡 JSON；展示 Gamma/GEX、rank、LLM 复核和审计证据 |
| JSON materializer | `tools/materialize_signal_cards.py` | 从 FMZ JSONL 生成静态卡片，并合并 LLM sidecar |
| Gemini LLM 复核 | `tools/gemini_signal_llm_review.py` | 旁路读取真实信号卡，生成 `signal_llm_reviews.jsonl`；默认由 systemd timer 调度 |
| 审计部署脚本 | `deploy/signal_audit/install_or_update.sh` | 安装/更新静态页面、materializer、LLM runner、systemd timer |
| 服务器自检 | `tools/server_self_check_signal_stack.sh` | 检查 FMZ JSONL、GEX API、审计页面、LLM sidecar、systemd 状态和端口 |

## 未正式启用资产

| 类型 | 路径 | 当前状态 |
| --- | --- | --- |
| 执行层 FMZ | `demo/最新交付物/spm_calendar_protected_short_v1.py` | STRATEGY_VERSION 2.5.0；默认 `ALLOW_ENTRY_TRADING=False`、`ALLOW_EXIT_TRADING=False`、`ALLOW_HEDGE_TRADING=False`、`ALLOW_TRADING=False`；仅作为空跑/后续测试交付物 |
| VRP 门 | `03_VRP门_建仓前定价/` | 建仓前定价过滤资料和代码；不能决定方向、不能解锁交易门 |
| 对冲模块 | `04_对冲模块/` | 持仓后尾部风险意图层资料；未接入当前自动交易 |

## 历史与失效资产

| 类型 | 路径 | 使用规则 |
| --- | --- | --- |
| 历史 FMZ 快照 | `demo/副本快照/` | 只用于追溯，不用于部署 |
| 旧审计 archive | `audit_archive/` | 旧 scaffold，不是当前生产审计页面；本次备份不再把它作为运行入口 |
| 旧长文 FMZ 推送 | v1.2/v1.2.1 相关文档和历史快照 | 已退役；当前只保留短推 + JSON 审计 |
| 旧 GEX API 副本 | 本目录 r1 之前的 `05_GEX...` | 已被 rank-enabled 当前版本覆盖 |
| GEX 旧需求草稿 | `05_GEX监控API_数据增强接口/readme.txt` | 历史计划稿，保留追溯；当前接口以 `/v1/info` 和 `因子文档/` 为准 |

## 文档入口

| 路径 | 用途 |
| --- | --- |
| `00_总纲/中性回路工程总纲_v2026.06.19-r2.2.md` | 当前工程级主入口 |
| `README_XXPROJECT_BACKUP.md` | GitHub 备份说明 |
| `BACKUP_VERSION.json` | 机器可读版本标记 |
| `SECRETS_REDACTED.md` | 脱敏与禁提交说明 |
| `05_GEX监控API_数据增强接口/因子文档/00_GEX监控API总览.md` | GEX 数据增强模块总览 |
| `05_GEX监控API_数据增强接口/docs/info接口语义文档.md` | `/v1/info` 字段语义与 rank 说明 |
| `05_GEX监控API_数据增强接口/docs/README.md` | GEX API 文档索引 |
| `deploy/signal_audit/因子文档/00_审计部署总览.md` | 审计部署、materializer、LLM sidecar 组件总览 |
| `deploy/signal_audit/docs/审计卡片语义.md` | 审计卡片字段与前端展示语义 |
| `deploy/signal_audit/frontend/VERSION.json` | 前端样例资产机器可读版本标记 |
| `deploy/signal_audit/README.md` | 审计页面和 LLM timer 部署说明 |

## 当前版本锚点

| 资产 | 当前锚点 |
| --- | --- |
| 信号层 | `demo_version=1.3.0`；`schema_version=nrd.schema.v1.0.0`；FMZ 内只写 JSONL/短推，不调用 LLM HTTP |
| GEX API | `gexmonitorapi=0.2.0`；rank 采用 `rolling_30d_or_available`，保留全量历史，只用最近 30 天算当前分位 |
| 审计 materializer | 默认读取 `/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl`，输出 `/opt/signal-audit/signal_cards/` |
| LLM sidecar | Gemini `gemini-3.5-flash`；输出 `signal_llm_review@1.2.0`；prompt `gemini_signal_review_prompt@1.2.0` |
| 执行层 | `STRATEGY_VERSION=2.5.0`；所有交易门默认关闭；仅作为空跑/后续测试资产 |
| 文档收纳 | r2.2 将 `05_GEX监控API_数据增强接口` 与 `deploy/signal_audit` 纳入 `因子文档/` + 中文语义入口 + 版本元数据惯例 |

## 当前验证命令

本地 Windows（在仓库根目录执行；`<python-3.12>` 可替换为本机 Python 3.12 解释器）：

```powershell
<python-3.12> -m py_compile `
  demo\最新交付物\neutral_regulation_demo_fmz.py `
  demo\最新交付物\spm_calendar_protected_short_v1.py `
  tools\materialize_signal_cards.py `
  tools\gemini_signal_llm_review.py

<python-3.12> tests\test_signal_llm_review_pipeline.py
<python-3.12> tests\test_signal_audit_deploy_llm_systemd.py
<python-3.12> tests\test_materializer_tail_window.py
```

GEX API 当前备份内测试（需要本目录已有依赖或先按 `05_GEX监控API_数据增强接口/README.md` 建立虚拟环境）：

```powershell
cd 05_GEX监控API_数据增强接口
.\.venv\Scripts\python.exe -m pytest -q
```

服务器：

```bash
bash tools/server_self_check_signal_stack.sh
sudo bash tools/server_self_check_signal_stack.sh --run-oneshots
```
