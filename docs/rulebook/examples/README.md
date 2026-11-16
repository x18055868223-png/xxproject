# 近真实量级的合成示例

这些数据由 `tests/test_astra_structural_board_v1.py:inputs` 构造，调用现有 `astra_level_behaviour_v1.build_card`，再调用提取原型生成牌面。日期、价格量级和 instrument 形式用于演示，不是从生产恢复的真实市场会话，也不是模型表现证据。

| 样例 | 输入与牌面 | 教学目的（不输入 AI） |
|---|---|---|
| A | `a_inputs.json` → `a_board.json` | 指数 80600、短腿 76000、保护腿 74000；79000/78000 的本期 OI 结构位位于指数到短腿之间，OI/路径/普通期权成交可读。用空间缓冲较多的布局表达需求中“相对有利”的示例意图；不预置 Accept，也不证明墙会阻止穿越 |
| B | `b_inputs.json` → `b_board.json` | 同一类责任，但本期墙过期、GEX 时钟在未来、OI/成交及完整前24小时路径缺失。关键义务证据不足，合法示范输出为 Unknown，见 `b_assessment.json` |

A 中前24小时 79000 行权价 Put 普通主动买/卖 36/72 BTC，样本指数仍处于其上方。只能描述共现；不能把期权卖出称为现货攻击流，更不能证明“正 Gamma 吸收卖压”。所有做市商方向、墙位稳定性和因果反馈仍为未知。

B 的 Unknown 是关键证据不足的协议示范，不是运行 AI 得出的结果，也不是市场看空或隐含 Reject。其短腿/保护腿仍完整保留；缺失不填零。

`a_board.json` / `b_board.json` 都没有“相对有利”、胜率、分数或决策标签。输入包是回放用源材料，真实交给牌手的是规则书与单个 board；不能附带本 README 或示范答案。

可复现测试重新生成 input、构建 card、提取 board，再验证保存的 JSON 与协议。评估示例的 `board_sha256` 采用原型 `digest(board)`，其 JSON 序列化规则由该函数固定。
