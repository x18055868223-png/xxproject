# 研究存档兼容性与版本策略

日期：2026-10-20　适用：`/var/lib/xxproject/research_archive/` 的全部记录与提交 schema

## 1. 版本号

每条记录自带 `schema` 字段，形如 `<名称>@<major>.<minor>`（如 `ledger_row@0.1`）。

| 变更类型 | 版本处理 | 例子 |
|---|---|---|
| **增量（additive）** | 只升 minor 或保持同号；读者必须容忍未知字段 | 新增可空字段（如 `ledger_row.locked`）、枚举中新增取值（如 `decision_event.event`、`archive_chain.kind`）、新增目录或记录类型 |
| **破坏性（breaking）** | 新 major | 字段改名、删除、改类型、改单位、改含义、改计算口径、收紧必填 |

读者规则：

- 按 `schema` 名称 + major 分派解析；同 major 内未知字段忽略，缺失的新增字段按 `null`（未知）处理。
- 不要假设枚举是封闭的（提交 schema 中由服务器校验的枚举除外）。
- 记录形状以 `schemas/*.schema.json` 为参考，服务器代码为最终依据。

## 2. 破坏性变更流程

1. **先公告**：在 `protocols/versions.jsonl` 追加 `protocol_version@0.1` 行，写明组件、新版本、生效 `at_ms`、`source` 与 `note_cn`（变更内容与迁移说明）。
2. **双写过渡期**：新旧 major 同时写出（不同 `schema` 值，必要时不同文件或目录），过渡期内两者都可读。
3. **停写旧版**：再追加一行公告旧 major 停写的 `at_ms`。已写出的旧版记录永久保留，不迁移、不改写。
4. 提交 schema（`hypothesis@0.1` 等）的破坏性变更同样走此流程；过渡期内服务器同时接受新旧版本。

## 3. 永不改变的事项

- **已写出的历史**：案卷、账本行、结果、提问者输出、决策事件、协议公告、登记行、legacy 文件，一经写出永不修改、删除或重写。纠错以新记录追加，并在 `note_cn` 或新记录中说明被纠正的对象。
- **哈希链**：`chain.jsonl` 只追加；条目哈希算法与链接规则不变。任何时候 `verify` 都能从第一行重放。
- **已登记的提交**：id 永不可改；修订用新 id + `supersedes`。
- **时间口径**：所有 `*_ms` 为 UTC epoch 毫秒，日期目录按 UTC。
- **结果与评价**：结果固定（交割价与路径统计写出后不重算覆盖）；评分规则写死后变更必须升版本并公告，且只对公告后的验证生效。
- **案卷 ref**：发布后不改名；废弃时以 `quality: not_applicable` 标注，不删除。

## 4. 弃用（deprecation）

- 字段或记录类型弃用，在 `protocols/versions.jsonl` 追加公告：组件、弃用对象、替代对象、计划停写时间（`note_cn`）。
- 弃用期内照常写出；停写后字段缺失按 null 处理，不复用旧字段名表达新含义。
- 条件语言字段（`schemas/condition_fields.json`）弃用时，已登记假设继续按登记时的字段定义求值；字段停写后相关样本按“字段缺失、样本排除”处理，并在公告中说明受影响的假设。

## 5. 派生物

`index/episodes.json`、`receipts/` 不入链，可随时重建、可变更形状（`episode_index` 仍按本策略编号）；它们不是证据，研究结论不得只引用派生物。
