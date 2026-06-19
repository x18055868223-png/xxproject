# 备份脱敏说明

本工程存在本地和服务器运行用的真实 token/key。推送到 `x18055868223-png/xxproject` 的快照必须只包含模板和说明，不包含真实值。

## 不进入仓库的内容

- `/etc/gexmonitorapi.env` 中的 `API_TOKEN`
- `/etc/signal-audit/llm.env` 中的 `GEMINI_API_KEY`
- 任意 `.env`、`*.env`、私钥、证书、SSH key
- FMZ 运行目录下的 `signal_review.jsonl`、`snapshots.jsonl`、`decisions.jsonl`
- GEX API 的 `.cache/metrics_history.jsonl`、抓取缓存、浏览器缓存
- Python `.venv/`、`.pytest_cache/`、`__pycache__/`
- 本地 `.git/`、临时压缩包、服务器日志

## 仓库中允许保留的模板

- `05_GEX监控API_数据增强接口/.env.example`
- `05_GEX监控API_数据增强接口/deploy/gexmonitorapi.env.example`
- `deploy/signal_audit/signal-audit-llm.env.example`

这些模板必须保持空 key 或占位值。恢复部署时，从安全渠道重新注入真实 token/key。

## 当前已知敏感项

- GEX `/v1/info` Bearer token
- Gemini API key
- 服务器公网 IP 本身可以保留在文档中作为部署上下文，但不得附带密钥
