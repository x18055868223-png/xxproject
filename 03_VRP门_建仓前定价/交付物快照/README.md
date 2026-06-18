# VRP 模拟与落地开发目录

本目录承接 `VRP权利金门控因子计划与说明_v1.0.md`，用于 VRP 因子的真实 Deribit 数据快照、场景模拟、参数调试、自迭代记录和后续落地开发。

当前内容：

| 路径 | 说明 |
| --- | --- |
| `src/vrp_model.py` | VRP window gate 与 candidate full-burn gate 的核心纯函数 |
| `src/deribit_snapshot.py` | Deribit 公共数据抓取、期权链解析、自算 RV 上下文 |
| `src/vrp_simulation.py` | 场景模拟、参数网格、阶段性评分与输出 |
| `tools/fetch_deribit_snapshot.py` | 抓取真实 Deribit BTC/ETH 快照 |
| `tools/run_vrp_simulation.py` | 对 snapshot 跑全量窗口/候选/参数网格 |
| `tests/` | 行为测试，覆盖 IV 标准化、hurdle、倒挂路由、full-burn、窗口过滤、snapshot 解析和模拟 |
| `data/snapshots/` | Deribit 真实数据快照 |
| `outputs/` | 参数网格与模拟结果 |
| `docs/` | 模拟成绩、参数调试、自迭代路径交付文档 |

当前安全边界：

- 不接入下单。
- 不修改执行层配置。
- 不把当前阶段参数视作实盘参数。
- `ALLOW_TRADING` 仍应保持 False。

复现命令：

```powershell
& 'C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe' -m unittest discover -s 'C:\Users\Xu\Documents\系统总纲\VRP\tests' -q
& 'C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe' 'C:\Users\Xu\Documents\系统总纲\VRP\tools\fetch_deribit_snapshot.py' --currency BTC --max-tickers 260
& 'C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe' 'C:\Users\Xu\Documents\系统总纲\VRP\tools\run_vrp_simulation.py' '<snapshot.json>'
```
