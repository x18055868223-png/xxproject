# 中性回路承保决策工作台（前端 v3）

独立设计的新工作台前端，纯静态（原生 ES 模块，无构建步骤）。只消费工作台服务 `tools/astra_workbench_v20.py` 的同源接口。

## 页面结构（三层，自上而下）

**状态条**：现价与事实时效、锚点、FMZ 版本、持续采集来源健康度、追踪中数量、今日复核额度、登录。

### 背景层：市场现在在哪里

1. **价格地图**：一条价格轴、三条轨道，按屏幕实际宽度绘制。
   - 责任区：两侧短腿到保护腿、到期前隐含预期波动（优先用同到期 IV；gexmonitor 期限结构缺本期时用 Deribit 本期标记 IV）、盈亏平衡。
   - Gamma 结构：周度与 0DTE 墙；**本期墙**（方块：Put / Call 墙，Gamma 与持仓量最大处相同时合并为“墙”，否则分别标“Gamma 墙”“持仓墙”）；**本期 Pin**（▼，单独在轨道顶部一行）；翻转位、全期限磁吸位（不是本期 Pin）、最大痛点。
   - 成交与成本：KPF 接受区与链上成本。
2. **五要素**：空间结构、量价路径、主动成交、宏观环境、空间位置。
3. **资金与慢背景**：资金费、永续 OI、ETF、链上成本、已实现盈亏、期权情绪、外部资本条件。
4. **外源性事件**：未来两周排期的美国宏观发布，标出落在本期合约内的。

### 业务层：这张保单怎么办

5. **本期保单**：承保前置条件一行；Put / Call 两张卡只负责比较和选择——短腿距现价（含 Delta）、盈亏平衡距现价、毛信用占宽度、到期前事件数。操作：开始 / 继续核验、直接锁定（不经核验）。
6. **Decision Path 核验**（`decision.js`）：冻结一张保单，按服务端定义的读序（`STEPS`，`path_version`）逐步展开 D0 资料 → D1 责任 → D2 位置 → D3 运动 → D4 外部 → D5 变化 → Q1 风险质量 → Q2 风险补偿 → AU 审计（按需）。
   - 每步只给判断词和几行读数，细节用“细节在”按钮跳回原组件；Q2 之前不出现权利金金额。
   - 每步一键标记（更倾向承保 / 没变 / 更倾向不承保）；“刷新到现在”按同一合同重读，开始时与现在并列。
   - 结论：锁定追踪（锁定记录附带核验）、不承保、继续观察。模型复核按钮在 AU 步。
   - 列表只显示合约未到期的核验，最新在前；到期后服务端回填结果并删除会话，实验记录留在 `decision_path/records.jsonl`。
7. **锁定追踪**：经核验的锁定标“经核验”；展开后为锁定时、锁定后过程、依据账本、持有中复核与盈亏。
8. **推理审计**：两个页签。
   - **模型复核**：模型逐点结论 ↔ 证据账本。
   - **提问单**（docs/opus/14）：提问者对该合约的问题、竞争假设与到期前判别现象 ↔ 所引案卷条目。标注“未验证，只作参考”。正文中的案卷 ref、字段名和枚举码由 `inquiry_terms.js` 读成中文，原文在悬停提示里。默认跟随保单卡；从 AU 步跳来时固定为核验中的合约。核验进行中、尚未到审计步时默认收起，以防锚定。
   - Decision Path 的 AU 步同时显示前 3 问，可一键标记（新会话 `au_mode`），服务端记录当时看到的提问单。

### 数据与审计层

9. **数据与审计记录**：来源时效、锚点效用变迁、每日快照、邮件通知、版本身份、主链路审计日志、定时模型复核。

## 接口

| 用途 | 方法与路径 |
|---|---|
| 页面主数据 | `GET /api/workbench?view=page`（含 `expiry_gamma` 本期墙与实现波动、`decision_path` 核验概览；每 30 秒轮询，标签页隐藏时暂停） |
| 提问单 | `GET /api/workbench/inquiry?short=&long=[&file=][&summary=1]`（只读，读研究存档） |
| 核验 | `GET /api/workbench/decision-path[/<id>]`；`POST /api/workbench/decision-path`（开始 / 继续）、`.../<id>/refresh`、`.../<id>/step`、`.../<id>/conclude` |
| 锁定过程 / 对照 | `GET /api/workbench/locked-candidates/<id>/series`、`GET /api/workbench/locked-candidates/<id>` |
| 审计日志 | `GET /api/workbench/chain-audit`、`GET /api/workbench/chain-audit/<文件名>` |
| 复核进行中 | `GET /api/workbench/review-state`（只在有复核进行时每 5 秒查询） |
| 会话 | `GET` / `POST`（`Authorization: Bearer`）/ `DELETE /api/workbench/session` |
| 模型复核 | `POST /api/workbench/review` |
| 锁定 / 重新追踪 / 停止 / 持有中复核 | `POST /api/workbench/locked-candidates`、`.../<id>/stop`、`.../<id>/hold-review` |

## 本地预览

预览服务器 `devserver.py` 放在会话 scratchpad，不进仓库。它把 `/api/*` 转发到 EU 工作台（经 SSH 隧道），POST/DELETE 也会转发，在预览里的操作是真实操作。

`?fixture=1` 加载由当日真实冻结快照与真实模型回执拼成的示例数据，页面顶部会明确标注“示例数据”。示例文件只在本地预览提供，线上打开会提示不可用。

## 部署（待用户确认“当前本地页面可推送”后进行）

把本目录复制到工作台静态根目录的子目录，例如 `/opt/signal-audit/workbench-v3/`，再访问 `/workbench-v3/`。
