# 01 · External Gate（外部数据门）

> 模块：① 信号层 · 主链路 1/3（进 `MODULE_SEQUENCE` / `module_results`）
> canonical：`中性回路 - opus4.8\demo\modules.py:evaluate_external_gate`
> 最后核对：2026-06-02（源码）

## 1. 一句话定位
全链第一道门，**只判数据源可用性**，不判方向、不判时序。把"数据是否够格让后续模块开工"显式化。

## 2. 当前具体实现
- 入参：`config`、`source_quality`（各数据源质量字典，值取 `QUALITY_OK/STALE/MISSING/INVALID/ERROR`）。
- 逻辑（`modules.py:63-85`）：
  1. `read_only_demo=True` → 状态降为 `STATE_CAUTION`，挂 reason `REASON_READ_ONLY_DEMO`（当前恒为只读，所以恒 CAUTION）。
  2. 若 `source_quality` 全部为坏值（ERROR/MISSING/INVALID，`bad_count==len`）→ 直接 `STATE_BLOCKED`，reason `ALL_SOURCES_UNAVAILABLE`，quality=MISSING。
  3. 否则返回 CAUTION（只读态）。
- 输出：`module_result(MODULE_EXTERNAL_GATE, state, facts={source_quality}, reasons)`，schema `SCHEMA_MODULE_RESULT`。

## 3. 关键阈值 / 配置
本因子无独立数值阈值；行为由 `read_only_demo`（`config.py:30`，现 True）与各源质量枚举驱动。部分源就绪是被允许的（不要求全绿，只要不全坏）。

## 4. 整合中的路径修改
**零**。主链门，不涉及 KPF。整合后它仍是 `module_results[0]`，契约校验 `contracts.py:_check_modules` 要求 `module_names == MODULE_SEQUENCE`，顺序不可变。

## 5. 当前目标 / 待办
- 闸 B 上线前 `read_only_demo` 仍 True；真实下单由执行层（②）持钥，信号层永不解此门。
- 无校准项。

## 6. 边界与陷阱
- 它**不是**交易门。"CAUTION（只读）"是常态，不代表风险。真正的可否交易由 EDB 的 `support_label` + NeutralRepair 的 `is_active` 决定。
- 它是 `MODULE_SEQUENCE` 的固定第 1 位，删/换会触发 `module_sequence_mismatch` 契约错误。
