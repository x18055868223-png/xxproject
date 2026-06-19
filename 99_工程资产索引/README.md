# 工程资产索引

版本标记：`NRD-XXPROJECT-BACKUP-2026.06.19-r2`

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

## 文档入口

| 路径 | 用途 |
| --- | --- |
| `00_总纲/中性回路工程总纲_v2026.06.19-r2.md` | 当前工程级主入口 |
| `README_XXPROJECT_BACKUP.md` | GitHub 备份说明 |
| `BACKUP_VERSION.json` | 机器可读版本标记 |
| `SECRETS_REDACTED.md` | 脱敏与禁提交说明 |
| `05_GEX监控API_数据增强接口/docs/info接口语义文档.md` | `/v1/info` 字段语义与 rank 说明 |
| `deploy/signal_audit/README.md` | 审计页面和 LLM timer 部署说明 |

## 当前验证命令

本地 Windows：

```powershell
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe -m py_compile `
  demo\最新交付物\neutral_regulation_demo_fmz.py `
  demo\最新交付物\spm_calendar_protected_short_v1.py `
  tools\materialize_signal_cards.py `
  tools\gemini_signal_llm_review.py

C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe tests\test_signal_llm_review_pipeline.py
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe tests\test_signal_audit_deploy_llm_systemd.py
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe tests\test_materializer_tail_window.py
```

GEX API 当前源仓测试：

```powershell
cd C:\Users\Xu\Documents\gexmonitorapi
.\.venv\Scripts\python.exe -m pytest -q
```

服务器：

```bash
bash tools/server_self_check_signal_stack.sh
sudo bash tools/server_self_check_signal_stack.sh --run-oneshots
```
