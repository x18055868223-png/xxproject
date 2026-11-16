# 最新真实输入与复现

`ai_input.md` 是完整输入；`player_reply.md` 是分离的主模型试答。规则与牌面一起发送，试答不进入下一次现象层。

`source/` 保留 2026-11-14 14:00 UTC 两份未修改的原生候选、对应第二轮原生位置卡，以及严格裁到读取时刻的公开指数序列。指数序列经项目既有 Deribit 数据接口补取；不是伪称服务器原生已归档路径。没有 11-11 或当日其他候选混入。原生案卷中的旧意见、经济门或交易资格不进入本实验正文。

在 `.worktrees/claude-astra` 中运行，输出文件须不存在：

```powershell
python tools/astra_player_input_v1.py --archive docs/rulebook/examples/player_input_liability/source --expiry-day 2026-11-15 --round r2 --out hand.md
```

生产归档路径缺指数序列时，使用已有 `--index-path` 传同一指数的观察序列；生成器按本侧读取时刻截断。仅有位置卡时保留已知短腿事实，不伪造最近进入/返回时刻。

本例可直接用于影子牌局判断；持仓时须附原责任及入场冻结条件，复用同标准新截面。未切换线上旧牌手、未部署或授权资金交易。具体实现、A–E 验收和证据限制见 [本轮说明](../../09_责任路径闭环_实现与验收.md)。
