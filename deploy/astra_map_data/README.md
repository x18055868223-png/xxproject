# 同机轻量数据部署

Python 标准库运行，不安装虚拟环境、不开放端口、不复制完整研究树。
两个独立 oneshot timer 每五分钟运行：KPF 同机生成不可变三件账与指针；MAP 消费指针、FMZ 当前事实和分组公共背景数据，写同机统一引擎及当前/上次 MAP。

部署只需打包、传包、校验 SHA、解包并执行 `sudo bash deploy/astra_map_data/install_server_data.sh`。必须从已核验 `xxproject` 工作分支提交构建；不要发布部署镜像或复制未确认的前端。

```powershell
python deploy/astra_map_data/build_server_package.py --output server-data.tar.gz
```

固定 namespace 为 `/opt/astra-kpf-light`、`/var/lib/astra-kpf-light` 和 `/opt/astra-map-data`。两份代码均原子选择 current release，保留 previous；回滚先停对应 timer，恢复 previous symlink，再启 timer。原 FMZ、LLM、Web 服务不更改。

KPF 上限 240 MiB / 50% 单核，MAP 上限 128 MiB / 25% 单核，两者低优先级。最低磁盘留空 6 GiB，KPF 原始压缩窗口存储上限 5 GiB。初始化最多每次下载一日，在真正齐90日和完成原算法计算前都是 WARMUP；安装成功不等于可用。

`/opt/astra-map-data/state/current_map.json` 的 completeness 按当前用途及时效核对七组产品，而不是按注册数量计数。ETF 与宏观、库存成本、兑现损益分轮采集，OI/Funding 每轮检查；缺借款授权、FMZ原生门、GEX期限或完整 KPF 都明确保留缺口。KPF 消费指针为 `/var/lib/astra-kpf-light/published/current.json`；工作台下一次获准发布时需把本机资格账绑定到 `/opt/astra-map-data/state/data_engine`，页面 GET、区域交互及 LLM 不运行下载或 KPF。

观测 `systemctl status astra-kpf-light.timer astra-map-data.timer`、各 service 的 `MemoryPeak` 与当前结果源时钟。完整90日真实 CPU/内存测量必须在窗口齐备后独立记录，单日测量不能宣称完整窗口已通过。
