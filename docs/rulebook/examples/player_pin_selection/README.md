# Pin定位与双侧择优：七份输入与主模型试答

真实行情固定为2026-11-14 14:00 UTC；原案卷、原生位置卡与截断指数序列复用 `../player_input_liability/source/`。03–07均明确标为合成，不冒充真实候选。

所有回复由本轮主模型在同一上下文逐份生成；不是七次独立模型调用，不是盲测，也不包含内部思考过程。它们只用于核查可见的责任路径和比较依据，不作胜率或稳定性证明。

| 轮次 | 完整输入 | 独立于现象的回复 |
|---|---|---|
| 01 真实，Put先 | [输入](01_real_put_first.input.md) | [试答](01_real_put_first.reply.md) |
| 02 真实，Call先 | [输入](02_real_call_first.input.md) | [试答](02_real_call_first.reply.md) |
| 03 合成A，Put先 | [输入](03_scenario_A_put_first.input.md) | [试答](03_scenario_A_put_first.reply.md) |
| 04 合成A，Call先 | [输入](04_scenario_A_call_first.input.md) | [试答](04_scenario_A_call_first.reply.md) |
| 05 合成B，Put先 | [输入](05_scenario_B_put_first.input.md) | [试答](05_scenario_B_put_first.reply.md) |
| 06 合成B，Call先 | [输入](06_scenario_B_call_first.input.md) | [试答](06_scenario_B_call_first.reply.md) |
| 07 完全对称 | [输入](07_symmetric.input.md) | [试答](07_symmetric.reply.md) |

`scenario_A.source.json`、`scenario_B.source.json`、`symmetric.source.json`只存相应合成输入。没有模型评估写回这些源数据。一次性生成脚本位于用户可见根目录 `.artifacts/player-pin-compare-20261114/generate_trials.py`，它只建输入、不生成回复，不接入生产链路。

真实正文可在当前工作树中复现，输出文件需不存在：

```powershell
python tools/astra_player_input_v1.py --archive docs/rulebook/examples/player_input_liability/source --expiry-day 2026-11-15 --round r2 --out pin_selection_hand.md
```

阅读[修改与核查结论](../../10_Pin定位与双侧择优_修改及推演复盘.md)。程序只导出牌手正文，未切换线上调用或授权交易。
