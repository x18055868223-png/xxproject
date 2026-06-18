# 备份脱敏说明

本工程存在少量本地部署用真实 token，例如信号层连接 GEX `/v1/info` 的 Bearer token。推送到 `x18055868223-png/xxproject` 的备份快照必须脱敏这些值。

## 规则

- 本地工作区的可部署文件保持原样，供 FMZ/服务器使用。
- GitHub 备份快照中的真实 token 替换为 `<REDACTED_GEX_INFO_TOKEN>`。
- `.env`、运行日志、缓存、虚拟环境、JSONL 生产数据不进入备份仓库。
- 如需从备份仓库恢复部署，先从安全渠道重新注入 token。

## 当前已知敏感项

- GEX `/v1/info` Bearer token。
- 任何 `.env` 文件中的 `API_TOKEN`。
- FMZ 运行目录下的 `signal_review.jsonl`、`snapshots.jsonl`、`decisions.jsonl`。
