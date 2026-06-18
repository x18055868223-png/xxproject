# 工程资产索引

版本标记：`NRD-XXPROJECT-BACKUP-2026.06.18-r1`

本目录只做索引，不搬空历史目录。当前工程资产按“最新交付、历史失效、文档入口、部署资产、运行缓存”五类管理。

## 最新交付

| 类型 | 路径 | 说明 |
| --- | --- | --- |
| 信号层 FMZ | `demo/最新交付物/neutral_regulation_demo_fmz.py` | 当前信号层只读观察交付物，含配置集中区、GEX 默认接入、JSONL 审计输出 |
| 执行层 FMZ | `demo/最新交付物/spm_calendar_protected_short_v1.py` | 当前执行层垂直价差交付物，默认交易门控关闭 |
| 最新交付说明 | `demo/最新交付物/README.md` | 当前 FMZ 交付物的局部说明 |
| 信号审计部署 | `deploy/signal_audit/` | 静态页面、Apache/Nginx 示例、materializer、systemd timer |
| GEX API | `05_GEX监控API_数据增强接口/` | 从 `C:/Users/Xu/Documents/gexmonitorapi` 迁移的 FastAPI 服务 |

## 历史与失效

| 类型 | 路径 | 使用规则 |
| --- | --- | --- |
| 历史 FMZ 快照 | `demo/副本快照/` | 只用于追溯，不用于部署 |
| 旧信号层/执行层快照 | `01_信号层_中性回路/交付物快照/`、`02_执行层_Deribit/交付物快照/` | 只读参考，当前部署以 `demo/最新交付物/` 为准 |
| 旧审计 archive | `audit_archive/` | 样例/旧 scaffold，不是当前生产审计页 |
| KPF/SLRP 相关叙述 | 旧总纲和部分历史文档 | 已移出当前主链，不作为运行层 |
| 旧长文 FMZ 推送 | v1.2/v1.2.1 相关文档和历史快照 | 已退役，当前只保留短推 + JSONL |

## 文档入口

| 路径 | 作用 |
| --- | --- |
| `00_总纲/中性回路工程总纲_v2026.06.18-r1.md` | 当前工程级主入口 |
| `README_XXPROJECT_BACKUP.md` | GitHub 备份标记 README |
| `BACKUP_VERSION.json` | 机器可读备份版本 |
| `demo/审计/2026-06-18_信号层配置整理与代码审计报告.md` | 最近一次信号层配置与代码审计报告 |
| `05_GEX监控API_数据增强接口/docs/info接口语义文档.md` | GEX `/v1/info` 字段语义 |
| `deploy/signal_audit/README.md` | 审计静态页部署说明 |

## 不进入备份仓库

- `.git/`
- `.venv/`
- `.pytest_cache/`
- `__pycache__/`
- `.env`
- 运行日志：`demo/logs/`
- FMZ/审计生产 JSONL：`*.jsonl`
- 大型或旧生成物：`dist/`、临时压缩包、浏览器缓存

## 当前核验命令

```powershell
C:\Users\Xu\Documents\gexmonitorapi\.venv\Scripts\python.exe -m pytest -q
```

在迁移后的 `05_GEX监控API_数据增强接口/` 下结果为 `13 passed, 1 warning`。

```powershell
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe demo/tests/test_latest_delivery_config_contract.py
```

用于确认信号层用户配置区、GEX 默认和 `/v1/info` 归一化。
