# 04 · systemd（定时任务与服务器路径）

> 模块：审计静态页面部署模块
> canonical：`deploy/signal_audit/*.service`、`*.timer`、`install_or_update.sh`
> 最后核对：2026-06-19（r2.2 文档收纳）

## 1. 一句话定位

systemd 单元负责在策略服务器上轻量、可恢复地刷新审计卡和 LLM sidecar。

## 2. 当前单元

| 单元 | 职责 |
| --- | --- |
| `signal-audit-materialize.service` | 运行 materializer |
| `signal-audit-materialize.timer` | 周期刷新静态卡 |
| `signal-audit-llm-review.service` | 运行 Gemini sidecar |
| `signal-audit-llm-review.timer` | 周期检查新真实信号并复核 |

## 3. 当前路径

| 路径 | 用途 |
| --- | --- |
| `/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl` | FMZ JSONL 来源 |
| `/opt/signal-audit` | 静态前端根目录 |
| `/opt/signal-audit-tools` | materializer / LLM runner 工具目录 |
| `/opt/signal-audit-tools/signal_llm_reviews.jsonl` | LLM sidecar 输出 |
| `/etc/signal-audit/llm.env` | Gemini key 和 LLM 配置 |

## 4. 1GB 服务器约束

- timer 周期刷新，不常驻重型服务。
- materializer 读取尾部窗口。
- GEX API 的 headless browser 由独立 systemd cgroup 限制，避免拖垮 FMZ。
- 部署脚本应在维护窗口运行。

## 5. 边界与陷阱

- `install_or_update.sh` 会复制文件并 enable/start timer，不是只读检查。
- Apache/Bitnami 已占用 80 端口时，不应强行启动 nginx 监听 80。
- 真实 `.env` 权限应为 root 可读、`0600`。
