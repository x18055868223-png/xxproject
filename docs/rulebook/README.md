# 当前 ARD 影子牌手与 v1.3 追踪入口

- **单一事实来源基线**：[PROJECT_BASELINE.md](../../../../PROJECT_BASELINE.md)（根目录唯一权威总纲：认知基线、数据流、L1–L6 漏洞修改方案、九段式牌手契约与 11-16/17 命令单）
- **v1.3 影子追踪操作说明**：[AI_PLAYER_SHADOW_TRACKING_V1_3.md](AI_PLAYER_SHADOW_TRACKING_V1_3.md)

每日 14:00 UTC 发牌、人工回复交接、影子责任续查及到期判卷见[每日交割卷宗与影子运行](15_每日交割卷宗与影子运行.md)。

[启动观察完整模型输入](examples/player_dossier/startup_ad_hoc_input.md)按原文保留。仓库内的历史复现数据仅含牌面与判卷所需的市场字段，来源说明见[发布样本](examples/RELEASE_FIXTURES.md)。

运行 `python tools/astra_player_input_v1.py --archive ARCHIVE_ROOT --expiry-day YYYY-MM-DD --round r2 --out hand.md`，将输出的完整 Markdown 交给 AI。规则随正文生成。`--without-ard` 用于回放旧一阶基线。`astra_player_v1.py` 与 `astra_player_rulebook.md` 服务独立的 Legacy 评分流程。

[最终收口与来源核对](14_ARD最终收口.md) · [最新完整输入](examples/player_ard_closeout/generated/ai_input.md) · [真实冻结卡试答](examples/player_ard_closeout/player_reply.md)

---

# 前版：ARD条件责任与冻结衔接

当前增量为[ARD条件责任与冻结衔接](13_ARD条件责任与冻结衔接.md)：[最新完整输入](examples/player_ard_responsibility/generated/ai_input.md)、[规则增量](examples/player_ard_responsibility/generated/rule_delta.md)、[完整试答](examples/player_ard_responsibility/player_reply.md)、[六情景检查](examples/player_ard_responsibility/scenario_review.md)。补两侧责任解释、固定/动态锚对照、原记录的可选ARD依赖及只读续查；宽松Accept与原破坏规则不变。

生成器仍是`tools/astra_player_input_v1.py`，它内嵌当前影子牌手规则。`tools/astra_player_rulebook.md`属于另一条旧评分调用链，不与这份完整输入拼接使用。下列ARD版本保留为历史冻结，不覆盖。

---

# 上轮：ARD最小补充

当前以[ARD能力核查与最小落地](12_ARD能力核查与最小落地.md)为增量，保持[一阶路径与关键反证护栏](11_一阶路径补全与关键反证护栏_本地冻结.md)的决策规则。正文生成器：tools/astra_player_input_v1.py；ARD纯计算：tools/astra_anchor_relative_v1.py；冻结条件检查：tools/astra_player_condition_v1.py。

- [最新B版完整输入](examples/player_ard/generated/B_input.md)、[原版A输入](examples/player_ard/generated/A_input.md)、[A试答](examples/player_ard/A_reply.md)、[B试答](examples/player_ard/B_reply.md)。只增加ARD事实及读法，真实半宽缺口不编造，保持积极Accept。
- [上一轮一阶路径冻结正文](examples/player_first_order_guard/ai_input.md)及[试答](examples/player_first_order_guard/player_reply.md)保留原样；`--without-ard`可逐字重现。
- [上一轮七份输入、试答与复现](examples/player_pin_selection/README.md)作为历史。
- [前版责任路径正文](examples/player_input_liability/ai_input.md)及[旧试答](examples/player_input_liability/player_reply.md)保留为历史。
- [前版一阶路径样例](examples/player_input_path/ai_input.md)保留为历史。

下面保留旧版本历史；其TMV要求及外移演示责任不作为当前生产输入要求。

---

# 结构主义规则书与双侧 Player View

当前规则书为 **0.5·动量修订**。本规则书规定的默认 AI 输入改为双侧 `structural_player_view@0.1`；原 `structural_board@0.1` 留作详细证据，不将其中的逐项 Unknown 清单直接作为短牌面。

- [牌手决策书正文](00_结构主义规则书骨架.md)：聚焦入区前路径条件与TMV逐窗运动，保留同标准持仓条件跟踪。
- [0.4自审与0.5修订说明](04_0.4自审与0.5动量修订.md)：具体删改依据及当前接入范围。

本次以0.4为自审底稿，形成0.5动量修订；此前连续牌局约束保留。TMV运动句已纳入决策书要求，当前 Player View 生成器尚未接入该投影；WPEM持仓接入与条件跟踪执行也尚未实现。

- [0.3 措辞修订说明](03_决策书与现象措辞修订.md)：只补义务限定与位置化反馈，不增字段或校验。
- [0.2 技术说明](02_牌手视图与积极决策收束说明.md)：实现、字段取舍、验收与限制。
- [原始证据层字段映射](01_现象提取与降维说明.md)：原子数据、来源和缺口追溯。
- [Player View Schema](structural_player_view_0.1.schema.json) / [双侧评估 Schema](structural_player_assessment_0.1.schema.json)。
- [双侧示例](examples/player_view/README.md)：Accept 与双侧不利 Reject；输入与评估分离。

原型 `tools/astra_structural_player_view_v1.py` 提供 `build(put, call)` 与 `validate_assessment(assessment, view)`。原提取器与旧 Schema 保持兼容；旧单侧评估不能用来判断整局不做。

从同一冻结截止、同标的/市场/到期的两份 board 生成短牌面：

```powershell
uv run --no-project --python 3.12 python tools/astra_structural_player_view_v1.py --put put_board.json --call call_board.json --out player_view.json
```

输出文件必须不存在。仅提供一侧时，缺失侧作为关键缺口；已有可接受侧仍可 Accept。双方都没有候选时必须显式传 `--cutoff-ms`，缺失候选不自动变成 Reject。资格失败证据可用 `--unavailable` 提供，格式见说明和 Schema。

重放示例：

```python
import json
from pathlib import Path
from tools.astra_structural_player_view_v1 import build, validate_assessment

p = Path("docs/rulebook/examples/player_view")
source = json.loads((p / "accept_boards.json").read_text(encoding="utf-8"))
view = build(**source)
assessment = json.loads((p / "accept_assessment.json").read_text(encoding="utf-8"))
validate_assessment(assessment, view)
```

最小相关回归：

```powershell
uv run --isolated --no-project --python 3.12 --with pytest --with jsonschema python -m pytest tests/test_astra_structural_player_view_v1.py tests/test_astra_structural_board_v1.py tests/test_astra_level_behaviour_v1.py tests/test_astra_case_file_v1.py -q
```

实现仍位于 `.worktrees/claude-astra`。未改动生产牌手、FMZ 或交易逻辑。本机 Git exclude 忽略部分文档，正式交付须核对本目录全部协议/样例，不要仅看 `git status`。

- [0.5牌面规范化输出说明](05_0.5牌面规范化输出说明.md)：完整输入组成、字段映射与TMV投影约定。
- [可直接发送的模拟完整输入](examples/player_input_v05/hand_001.ai_input.md) / [配套完整JSON](examples/player_input_v05/hand_001.player_view.json)。仅文档与示例补齐，生成器自动接入范围不变。

- [工作台真实冻结牌局完整输入](examples/player_input_engine_v05/ai_input.md)：0.5精简规则与位置绑定的真实牌面，优先阅读；旧模拟示例保留为历史。
- [字段降维与最小接入差距](06_工作台数据版前置语料说明.md)。

- [高密度完整输入示例](examples/player_input_dense_v05/ai_input.md)：真实冻结市场 + 明示演示责任，两侧各3处主结构；不是原始候选重放。[程度表达与取舍](07_高密度牌面与程度表达说明.md)。
