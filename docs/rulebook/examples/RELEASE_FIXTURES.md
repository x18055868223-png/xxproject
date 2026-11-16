# 发布样本与复现

`player_input_liability/source` 和 `player_input_path/source` 是已冻结市场截面的最小投影，供生成器和回归使用。保留合约、期权盘口估计、位置卡、量价、分钟路径以及牌面所需的结构。案卷标识已替换，未收录工作台原包、服务器采集回执、运行日志或真实启动的回复和卷宗。原始档案保存在本地。

`player_ard/generated/pin_history.jsonl` 保留用于回放的锚点和可用时钟。兼容投影身份照留，原始回执明细仅在本地。旧版 A/B 牌面可直接阅读。若要重放当前规则和判卷，从仓库根目录运行以下命令。

```powershell
python docs/rulebook/examples/player_ard_closeout/reproduce.py --out .artifacts/ard-release-replay
python docs/rulebook/examples/player_dossier/replay.py --out .artifacts/dossier-release-replay --official
```

`--official` 读取公开 Deribit 交割结果。重放卷宗标记为历史回放。启动观察的完整牌面是另一份冻结输入，供审查模型当时看到的规则与事实。
