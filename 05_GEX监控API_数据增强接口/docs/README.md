# GEX Monitor API 文档索引

> 当前模块口径（r2.2 / 2026-06-19）：本目录收纳接口字段语义、样例响应、测试记录和部署说明。当前运行接口以 `/v1/info` 为准，旧 `readme.txt` 仅作历史需求草稿。

## 当前入口

| 文件 | 用途 |
| --- | --- |
| `../因子文档/00_GEX监控API总览.md` | 工程模块总览，说明数据如何进入信号层和审计卡 |
| `../因子文档/01_gex_board_GEX总览.md` | netGEX、DVOL、market_state 语义 |
| `../因子文档/02_gamma_exposure_关键价位.md` | flip、pin、wall、magnet 等价位语义 |
| `../因子文档/03_volatility_权利金与波动率.md` | IV/RV、PCR、term structure 语义 |
| `../因子文档/04_flow_期权资金流.md` | Call/Put premium、call share、flow P/C 语义 |
| `../因子文档/05_rank_历史分位.md` | rank 窗口、冷启动质量和分位边界 |
| `info接口语义文档.md` | `/v1/info` 字段级语义 |
| `info.sample.json` | 样例响应 |
| `pytest.txt` | 手工测试片段与历史测试记录 |
| `../deploy/README.md` | 服务器部署入口 |

## 阅读边界

- 本目录只描述 GEX API 的只读上下文增强，不改变信号方向、EDB、blocking 或交易许可。
- rank 是历史窗口相对位置，不是胜率。
- 真实 `API_TOKEN` 只应写入服务器 `/etc/gexmonitorapi.env`，不得提交。
