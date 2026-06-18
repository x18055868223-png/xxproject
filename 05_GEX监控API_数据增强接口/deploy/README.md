# 部署到 AWS Lightsail（Ubuntu + venv + systemd）

本服务靠**无头浏览器**渲染 GEX Monitor 页面再抓指标，所以服务器需要能跑 headless Chromium。
下面以 **Ubuntu 24.04 LTS**（自带 Python 3.12）为例。命令里的 `ubuntu` 是 Lightsail 默认用户。

> 占位符：`<TOKEN>` = 你的 API Token，`<IP>` = 实例公网 IP。

---

## 0. 选实例

| 内存 | 说明 |
| --- | --- |
| 512MB（$5） | ❌ 跑 Chromium 容易 OOM，不建议 |
| 1GB（$7） | ⚠️ 可用，但必须加 swap |
| **2GB（$12）** | ✅ 推荐，抓取更稳 |

蓝图选 **Ubuntu 24.04 LTS**（不要选带应用的镜像）。

## 1. 开放端口（Lightsail 防火墙）

实例页 → **Networking** → IPv4 Firewall → Add rule：
- 直连方式：放行 **Custom TCP 8000**（建议把 Source 限制成你的固定 IP）。
- 走 Nginx：放行 **HTTP 80**（要 TLS 再加 **HTTPS 443**），见第 9 节。

## 2. 连接并装系统依赖

```bash
ssh ubuntu@<IP>

sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git
python3 --version   # 确认是 3.12.x
```

## 3. 放代码

二选一：

```bash
# A) git（已推到远端仓库）
git clone <你的仓库URL> /home/ubuntu/gexmonitorapi

# B) 从本机 scp（在 Windows 本地、仓库目录下执行，注意排除 .venv）
#   scp -r src tests pyproject.toml README.md deploy ubuntu@<IP>:/home/ubuntu/gexmonitorapi/
```

## 4. 建 venv + 装依赖 + 装浏览器

```bash
cd /home/ubuntu/gexmonitorapi
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .          # 生产不需要 [dev]

# 装 headless Chromium 需要的系统库（用 root 跑，按系统版本装对包）
sudo .venv/bin/playwright install-deps chromium

# 下载浏览器内核（必须用运行服务的同一个用户 ubuntu 跑，浏览器会装进 ~/.cache）
# 抓取主用 Scrapling 动态抓取，并自带 bare-Playwright 兜底，两者共用同一套 Chromium
.venv/bin/scrapling install
.venv/bin/playwright install chromium
```

## 5.（1GB 实例必做）加 2GB swap

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

## 6. 写配置文件

```bash
# 生成强 token
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

sudo cp /home/ubuntu/gexmonitorapi/deploy/gexmonitorapi.env.example /etc/gexmonitorapi.env
sudo nano /etc/gexmonitorapi.env       # 把 API_TOKEN 改成上面生成的值
sudo chmod 600 /etc/gexmonitorapi.env
```

## 7. 装 systemd 服务

```bash
sudo cp /home/ubuntu/gexmonitorapi/deploy/gexmonitorapi.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gexmonitorapi

sudo systemctl status gexmonitorapi --no-pager
journalctl -u gexmonitorapi -f        # 看日志，Ctrl+C 退出
```

## 8. 验收

```bash
# 本机
curl -s http://127.0.0.1:8000/health
curl -s -H "Authorization: Bearer <TOKEN>" http://127.0.0.1:8000/v1/info | head -c 400
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/v1/info   # 期望 401

# 公网（从你自己的电脑）
curl -s http://<IP>:8000/health
curl -s -H "Authorization: Bearer <TOKEN>" http://<IP>:8000/v1/info | head -c 400
```

首轮抓取在后台进行，可能要 1–2 分钟四个 tab 才陆续就绪。期间 `/v1/info` 会返回
`availability: missing/partial`，属正常。可手动催一轮：

```bash
curl -s -X POST -H "Authorization: Bearer <TOKEN>" "http://127.0.0.1:8000/v1/refresh?section=all" | head -c 400
```

## 9.（可选）Nginx 反代 + TLS

需要域名。先把 service 里的 `--host 0.0.0.0` 改成 `--host 127.0.0.1` 再 `daemon-reload && restart`：

```bash
sudo apt-get install -y nginx
sudo cp /home/ubuntu/gexmonitorapi/deploy/nginx-gexmonitorapi.conf /etc/nginx/sites-available/gexmonitorapi
sudo nano /etc/nginx/sites-available/gexmonitorapi   # 改 server_name
sudo ln -s /etc/nginx/sites-available/gexmonitorapi /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# TLS（域名已解析到本机后）
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your.domain.example
```

记得把 Lightsail 防火墙的 8000 关掉，只留 80/443。

## 10. 更新代码

```bash
cd /home/ubuntu/gexmonitorapi
git pull                      # 或重新 scp
.venv/bin/pip install -e .    # 依赖有变才需要
sudo systemctl restart gexmonitorapi
```

---

## 故障排查

**`systemctl status` 报 browser 启动失败 / `Failed to move to new namespace` / 浏览器闪退**
Ubuntu 24.04 默认用 AppArmor 限制非 root 的 user namespace，会让 Chromium sandbox 起不来。两种解法：

```bash
# 解法 A（推荐，持久化关闭该限制）
echo 'kernel.apparmor_restrict_unprivileged_userns=0' | sudo tee /etc/sysctl.d/99-userns.conf
sudo sysctl --system
sudo systemctl restart gexmonitorapi
```

解法 B：用 `--no-sandbox` 跑浏览器。本服务支持环境变量开关——在 `/etc/gexmonitorapi.env`
里加一行 `BROWSER_NO_SANDBOX=true` 再 `sudo systemctl restart gexmonitorapi`。

**`/v1/info` 一直全是 null（所有字段 missing）**
- 看 `field_status` 的 reason；`sections.<x>.last_error` 有没有报错。
- 后台首轮可能还没跑完（四个 tab 各等 ~12s 渲染），先等 1–2 分钟或手动 `POST /v1/refresh`。
- 若 `last_error` 提示 `loading_placeholder`：页面没渲染出来，调大 `BROWSER_WAIT_MS`（如 18000）后重启。
- 若 `last_error` 提示超时：调大 `REQUEST_TIMEOUT_SECONDS`（如 60）后重启。

**进程被 OOM kill（journalctl 里有 `Killed`）**
内存不够。升到 2GB 实例，或确认第 5 步的 swap 已生效（`free -h`）。

**`patchright install-deps` 不可用**
改用 `sudo .venv/bin/playwright install-deps`；两者等价。
