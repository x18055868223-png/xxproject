# ARD → 条件责任：本轮最小增量

本地修订：2026-11-15。真实范例仍为2026-11-14 14:00 UTC左右冻结卡，不代表当前行情。

直接使用：[最新完整输入](examples/player_ard_responsibility/generated/ai_input.md)。[一次完整牌手试答](examples/player_ard_responsibility/player_reply.md)展示两侧解释、相对择优和条件冻结；[六个合成情景检查](examples/player_ard_responsibility/scenario_review.md)单独保存，不混入真实行情。

**本轮只补一段连接：ARD有意义时，分别说明它怎样支持、削弱或限制两侧责任；真正依赖它时保存原依据，后续仍按原锚和原破坏规则复核。** 不要求ARD改变每一局选侧。本真实例仍Accept / Call，新增的是解释约束，不是独立安全证据。

## 1. 已有能力与实际缺口

| 核对项 | 现状与本轮处理 |
|---|---|
| 当前规则入口 | 宽松Accept正文实际由`tools/astra_player_input_v1.py`生成，前序规则在11/12号说明及冻结正文。`tools/astra_player_rulebook.md`仍由旧`astra_player_v1.py`评分流程调用，包含−2至+2、摩擦与原硬规则。本轮不把两个接口混写、不改该文件或其他窗口的修改。当前链路的正式增量见下一节及本目录入口。 |
| 一阶路径和反证 | 双侧责任、短腿进入/返回、同位合并、4h/12h不重复计票、价流反证、积极Accept均已实现，原样保留。 |
| Pin历史、半宽、时间对齐 | 上轮已实现结构归档可用钟/来源/方法元数据、同expiry与分钟价因果对齐、固定美元半宽、动态Pin、缺口切段。Pin归档独立于持仓；不重复建历史系统。本轮未访问服务器，线上状态不重新作当前核验。 |
| 旧空间评分 | 当前源码仍是相对Flip的成交量柱归一化偏离聚合，不是Pin分钟停留率。半宽为原`compute_band_half`输出；缺历史原值不反推。本轮不改FMZ或评分。 |
| 固定锚对照 | 上轮主要显示历史逐点Pin关系，没有显式保留窗初可用Pin作为固定参照；本轮补固定参照的身份、有效分钟与端点相对位置。中心随历史Pin变化的停留率现在明确标明口径，半宽仍固定。 |
| 责任解释 | 原句“按具体责任解释”不足以约束实际试答。本轮明确要求两侧分别解释、陈述量价冲突、允许无独立增量，并区分复核与有效破坏。 |
| 持仓/WPEM | 原条件工具已冻结合约、历史、承担条件、检查钟和两种短腿破坏规则。WPEM的`compose`可采样/回放同candidate，`compose_current`只读当前点；未接本轮ARD承担依据。本轮只加原冻结记录的可选附件和只读对照函数，完成本地接口验收，不宣称线上续判已接通。 |

## 2. 正式规则书的最小增量

[规则增量全文](examples/player_ard_responsibility/generated/rule_delta.md)由生成器的`ARD_READING`直接导出，并原样进入完整输入，不要求牌手另外打开技术文档。

其可执行要求是：先审责任与短腿路径，再读ARD；把同一动态关系分别映射到Put/Call的短腿位置、剩余余量与期限，说明支持、削弱或限制哪项条件。关系延续或破坏时对哪侧更不利，必须作出条件风险取舍，不能以“不预测”回避择优。量价反证有冲突须明说；同源和同位不增加票数。ARD缺失不弃权，无独立信息可明确说没有增量。

只有实际依赖ARD时才冻结它的事实、Pin身份、口径、责任含义与具体复核变化。后续不换锚维护原理由；复核提醒与原有效破坏分开，ARD变化不自动Revoke。原Accept/Reject/Unknown政策不变。

## 3. 必要代码修改

| 位置 | 最小修改 |
|---|---|
| `tools/astra_player_input_v1.py::ARD_READING` | 用明确的条件责任纪律替换原简短限定；正文仍5组事实。`--without-ard`仍复现原一阶路径文本。 |
| `tools/astra_anchor_relative_v1.py::analyze/render` | 保留窗初已可用、未过期Pin作固定参照；同时保留逐点已知Pin，不能以移动中心遮掩旧参照下的偏离。补expiry/index身份供冻结读取。迟到旧观测同时从迁移叙述和样本参照中排除，避免只修计算、不修文字。未知方法不输出可确认的邻域离返。 |
| `tools/astra_player_condition_v1.py::freeze_condition` | 同一记录可选附`ard_basis`，只含`facts`、`responsibility_meaning`、`review_on`。验证同expiry、入场前可得身份；不另建状态机或Schema，不写回现象层，文件仍只创建不覆盖。 |
| 同模块`compare_ard_basis` | 用冻结合约与原UTC检查钟读后续`analyze()`结果，比较原Pin、最新可得Pin、到原Pin的距离/原邻域、短腿余量及新增事实。源/方法、起窗或半宽不可比时不比较离返，不作滚动停留率增减推断。输出无动作、无触发结论；原`check_condition`单独核对既定短腿破坏。 |
| 测试与示例 | 扩展ARD回归，新增冻结衔接测试和本轮独立复现目录；旧冻结案例及上一轮归档生产修改均不覆盖。 |

没有新增采集服务、持久化平台、ARD评分或自动交易权限。沿用上轮已写入本地代码的Pin来源/方法/可用时间字段，本轮没有更改生产者。

## 4. 本真实截面的增量与缺口

同一4小时窗内，所见Pin由68,000变为67,000。按逐点已知Pin，价格减Pin从−664.2变为−232.4美元；**按窗初固定68,000，价格减该参照从−664.2变为−1,232.4美元。** 两种口径回答不同问题，不能把前一种距离缩小称为向始终固定的67,000回归。

当前67,000不在Put66,000的入区前路径，与Call67,000短腿同位。Call12:59回收也穿过同价Pin，只是一份路径证据。真实历史方法版本未冻结，窗前半宽仍缺失，因此没有真实停留率或邻域离返，也不宣称连续吸附、持续迁离或迁移原因。历史行继续使用上轮明确标记的同次采集回执兼容投影，未伪装成原生方法记录。

本轮试答完整审查两侧后Accept / Call，理由仍是实际回收与当前价流责任方向的相对取舍；最强反证仍是Call仅0.18倍尺度且零站。ARD帮助明确反例与解释边界，但没有改变承担条件，也没有证明反证辨识能力或经济结果提升。因此真实试答没有为了展示新功能而假装依赖ARD，[影子冻结记录](examples/player_ard_responsibility/shadow_condition.json)不增加ARD门。

## 5. 入场后如何直接衔接

已有调用保持不变。实际依赖ARD的牌手在原`freeze_condition`调用中附：

```python
ard_basis = {
    "facts": ard_facts,  # 同一期、入场前截止的 analyze() 原结果
    "responsibility_meaning": "具体说明为何与这份责任相关",
    "review_on": ["列出具体后续变化；复核提醒不等于自动退出"]
}
```

原记录已同时保存两腿、到期、入场时间、持续条件、有效破坏、检查时钟和采样口径，不重复建一份ARD合同。续查在原检查点提供同expiry的ARD事实，调用`compare_ard_basis(frozen_path, current_facts, check_at_ms)`；实际边界触发仍调用`check_condition`。若要比较新增邻域事件，保持原起窗及原半宽，只延长观察终点；新滚动窗只能作明示的位置比较。新收到但观测于入场前的记录保留双钟，不能说成入场后新迁移。

固定参照有两个不同时间身份：入场前4小时的窗初Pin用于解释那段历史；续判依赖的Pin取入场时所冻结事实的末个可用观测。二者都保留，不以新的Pin替换。若最新采样不足，距离/当前变动留缺，不把旧截面冒充现在。

合成验证见`generated/synthetic_hold/`：入场Pin100，随后Pin98、价格97，程序仍比较原100±2邻域，标明原邻域外、Put95余量2，原边界未触发；下一检查点价格94才由原规则触发。旧返回不算新增。此样例证明接口行为，不是实盘持仓。

## 6. 验收与复现

六种情景均由可复现fixture生成，并逐项给出两侧责任解释：反复回归、持续迁离、锚价共同迁移、同短腿高停留、历史缺失、回归背景与量价反证张力。真实牌面不改行情、不塞入合成半宽。可检查公开理由，不声称检验模型隐藏思维或独立盲测。

在工作树执行：

```powershell
python docs/rulebook/examples/player_ard_responsibility/reproduce.py --out .artifacts/ard-responsibility-replay
uv run --isolated --no-project --python 3.12 --with pytest --with jsonschema python -m pytest tests/test_astra_anchor_relative_v1.py tests/test_astra_ard_condition_v1.py tests/test_astra_player_input_v1.py tests/test_astra_player_condition_v1.py tests/test_astra_expiry_structure_v1.py tests/test_astra_level_behaviour_v1.py tests/test_astra_research_archive_v1.py -q
```

当轮本地验收 **180 项测试通过**。输出文件必须尚不存在。原始验收摘要在当轮本地冻结包内。仓库复现数据采用[发布样本](examples/RELEASE_FIXTURES.md)。指定 gpt-5.5 的两个调查 agent 和一个 reviewer 均因账户不支持而启动失败，源码核查、试答和回归由主模型完成。

下游现在可以直接使用完整正文；本地冻结与后续对照接口已可运行。线上WPEM尚未调用这两个本地辅助入口，采集元数据补丁也没有由本轮部署；真实停留计算仍需保留窗前原半宽，旧缺口无法事后补造。本轮到此收束，不扩建自动持仓系统。

宽松Accept、双侧择优、关键反证标准、原持仓破坏口径、硬风控、工作台观测、FMZ及实盘权限未变。没有提交、推送、标签、部署或下单。
