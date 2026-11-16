# 双侧 Player View 示例

2026-11-14。现象措辞已随决策书0.3更新，输入字段与示范评估选择保持不变。以下均为合成近真实量级数据，用于协议与提取演示，不代表真实模型表现或真实成交。

| 示例 | 源输入 | 短牌面 | 独立评估 |
|---|---|---|---|
| 至少一侧可接受 | accept_boards.json | accept_view.json（84 → 7 条） | accept_assessment.json：Accept，选 Put |
| 双侧具体不利 | reject_boards.json | reject_view.json（56 → 5 条） | reject_assessment.json：Reject |

文件名用于开发者识别示例用途，不是模型输入的一部分。模型只接收规则书和 view JSON，不附带本 README、预期答案或文件名标签。

Accept 示例：同一指数80600；Put 76000/74000，Call 84000/86000。空间关系可读，独立示范判断两侧均可接受，并选 Put。dealer_gamma、稳定性、迁移和因果确认在原始证据层仍未知，不是短牌面的拒绝理由。

Reject 示例：同一指数80600；Put 81000/79000，Call 80000/82000。两侧都已经进入各自责任区间。示范评估采用“新承保尚未进入责任区间”的条件，两侧均不符合才 Reject；该条件仅是此局示范依据，没有编码为普遍门控。

`tests/test_astra_structural_player_view_v1.py:boards` 通过原生 level card 和 structural_board producer 构造输入，测试重新生成并比对保存的 JSON。`assessment` 与 `view` 独立存储；view 中不含 decision、selected_side 或 at_least_one_acceptable。

测试还覆盖没有 OI/成交仍可 Accept、只缺一侧时可接受另一侧、双方关键几何缺失才进入整局 Unknown，以及无合格价差必须有明确资格失败证据。
