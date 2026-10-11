# 运维访问与凭据位置清单（2026-10-11）

本表只记录**位置、用途与状态**，不含任何凭据值。凭据原件由用户在仓库外保管（本机 `D:/谷歌下载/xxproject_私密凭据_20261011/` 与 `~/.ssh/`）；不得写入 Git、项目记忆或日志。

## 主机访问

| 主机 | 本机 SSH 别名 | 认证方式 | 本机密钥 / known_hosts | 2026-10-11 连接测试 |
|---|---|---|---|---|
| EU 新机 169.58.177.126（root） | `xx-eu` | 管理密钥 `xxproject_eu_admin`（ed25519，已安装并验证）；root 密码仅用户本人使用 | `~/.ssh/xxproject_eu_admin`，`~/.ssh/xxproject_eu_stage2_known_hosts` | 22 端口可达，主机指纹与已记录一致；HTTPS 网关 401（需认证，正常）；8000 不对公网开放 |
| EU 新机隧道（仅转发 8896） | — | 受限隧道密钥 | `~/.ssh/xxproject_eu_stage2_tunnel` | 未单独测试 |
| 旧机 13.231.16.198（bitnami） | `xx-old` | Lightsail RSA 私钥 | `~/.ssh/xxproject_old_bitnami`（来自用户 `fmz2`），`~/.ssh/xxproject_old_known_hosts` | SSH 成功；gexmonitorapi、物化与 LLM timer active；robot 2 个（路由 115167519、69494805）；GEX 8000 **对公网开放**，/v1/info 需 token |

## 服务凭据

| 凭据 | 消费方 | 服务器位置 | 状态 |
|---|---|---|---|
| GEX `API_TOKEN` | gexmonitorapi `/v1/refresh`（FMZ 2.0.3 起不再使用 token） | `/etc/gexmonitorapi.env` | 新旧机同值。新增 `PUBLIC_READ=true` 后 `/v1/info` 免 token（仅 loopback 部署启用）；FMZ 2.0.2 仍要求 token 非空，填任意非空占位即可 |
| DeepSeek `LLM_API_KEY` | 人工 LLM 评审 / LLM timer | `/etc/signal-audit/llm.env`（0600） | 旧机已配置；EU 待用户脚本写入，写入后预算与 timer 仍需单独授权启用 |
| 工作台写令牌 `ASTRA_WORKBENCH_TOKEN` | 工作台写会话 | `/etc/signal-audit/workbench-v20.env` | EU 已配置 |
| 公网网关 Basic 认证 | nginx | `/etc/nginx/xxproject-web.htpasswd` | EU 已配置；本机副本 `~/.ssh/xxproject_eu_web_credentials.json` |
| FMZ 托管者登录 | `xxproject-fmz-node-69494805.service`（账号 UID 69494805，托管者名 vmi3648530） | `/etc/xxproject-fmz-node-69494805/fmz-password.cred`（systemd 加密凭据） | 2026-10-11 改绑：`Login OK`，enabled，服务重启后自动恢复，进程参数无 `-p`。原 `115167519`（绑错账号）已 disable，文件保留可回退 |
| 旧→新只读桥私钥 | `xxproject-fmz-bridge` | `/etc/xxproject-bridge/migration_pull_ed25519` | EU 已配置 |
| Deribit / Binance 交易 API | FMZ 平台交易所对象 | FMZ 平台（不在服务器、不在 Git） | 用户确认已在 FMZ 配置；观测阶段不使用 |
| HTTPS 证书私钥 | nginx | `/etc/letsencrypt/live/xxproject-eu-ip/` | 证书到期 2026-10-17 01:04（UTC+8），certbot timer 每 6 小时续期 |

## 待处理

- `ssh xx-eu` 管理密钥登录已验证（2026-10-11）；待用户同意后关闭 root 密码登录（`PasswordAuthentication no`）。
- `node.fmz.com/<数字>` 是 FMZ 账号 UID；同一账号可有多个托管者，旧机与 EU 同账号并存不冲突。迁移策略时须保证单一生产者（停旧机策略或停只读桥）。
- 整机重启后的自动恢复尚未实测（仅验证了 service restart）。
- 凭据已在对话与明文文件中出现过；观测阶段结束、进入任何资金阶段前，轮换 root 密码、FMZ 密码、GEX token 与 DeepSeek key。

## 2026-10-11 单生产者切换（只读桥 → EU 原生 FMZ）

- 生产者：EU 托管者 `xxproject-fmz-node-69494805`，robot 668422，FMZ `2.0.3`；输出目录 `/var/lib/xxproject-fmz-node-69494805/logs/storage/668422/demo/logs/`。托管者 drop-in `10-worker-compat.conf` 放宽 seccomp/命名空间硬化（原设置使 worker 启动 EPERM）。
- 消费者改指：workbench `ASTRA_WORKBENCH_SNAPSHOTS_JSONL`、data-engine `ASTRA_FMZ_LOG_DIR`、`llm.env` `JSONL_SOURCE`；materializer 与 observation canary 用 drop-in `20-eu-native-source.conf`。`bitnami` 加入 `xxproject-fmz-worker` 组以只读访问。
- 历史接续：`signal_review.jsonl`（261 条，15,923,276 B）与 `fixed_analysis_round_state.json` 在 robot 首次写卡前按原字节预置（sha256 与源一致），新卡追加其后。
- 只读桥 `xxproject-fmz-bridge.timer` 已 disable；`/var/lib/xxproject/fmz-native` 与桥配置保留。
- 工作台接受 FMZ 2.0.3（`fact_context` / `wpem_native` 版本白名单，commit f428e1f），已部署到 `/opt/signal-audit-tools`、MAP release 与 data engine。
- GEX：`PUBLIC_READ=true`、`REFRESH_ON_STARTUP=false`，release `/opt/gexmonitorapi/releases/claude-478e126-public-read`。
- 整机重启测试通过（11 个服务/定时器自动恢复，托管者自动登录）；robot 不随重启自动拉起，需在 FMZ 手动启动。

回滚：恢复 `/root/cutover-backup-20261011101100/` 中三份 env 与 `/root/cutover-backup-20261011101334-tools/` 中工具文件；删除两个 `20-eu-native-source.conf`；`systemctl enable --now xxproject-fmz-bridge.timer`；`daemon-reload` 后重启 workbench。GEX 回滚为把 `/opt/gexmonitorapi/current` 指回 `releases/v2.6.3-stage.1`。

## 2026-10-11 GEX 上游改版适配（commit ae4986a）

- release `/opt/gexmonitorapi/releases/claude-ae4986a-upstream-redesign`（上一版 `claude-478e126-public-read`）；`/etc/gexmonitorapi.env` 改 `REFRESH_INTERVAL_SECONDS=900`（备份同目录 `.bak-*`）。
- 上线后 `availability=ready`、`enrichment_availability=ready`，无缺失字段，已停供字段不再出现；响应约 31 KB。
- 回滚：`ln -sfn /opt/gexmonitorapi/releases/claude-478e126-public-read /opt/gexmonitorapi/current && systemctl restart gexmonitorapi`。
