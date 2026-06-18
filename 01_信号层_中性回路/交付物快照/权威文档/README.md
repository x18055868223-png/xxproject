# Neutral Regulation Demo v0.5.3

本目录是 FMZ 单文件交付前的多文件源码形态。`schema_version = nrd.schema.v0.5.3`，`demo_version = 0.5.3`（阶段封版）。

v0.5.3 的边界仍是前置信号层和策略制定层：模型输出信号、24h/48h 最近期号和策略类型建议。具体期权腿、数量、价格、委托和交易执行由外部执行程序处理。

## 当前主链路（进 `module_results`）

1. External Gate：只读边界与数据源健康
2. Anchor：中性锚有效性（不判方向）
3. TMV-F：24h/48h 到期窗口方向倾向（作 EDB 主干证据）

## 前置信号 / 方向合成层（进 `FactorSnapshot`，不进 `module_results`）

- `neutral_repair_signal`：DIE 事件→Anchor 受损→Anchor 修复确认的时序状态机，是**时序门**（决定窗口"何时"开，不决定方向）。
- `edb`：到期窗口方向合成层 = **权威方向层**。六证据（TMV / CVD×价格 4h+12h / MACRO / FUNDING / SRD / GGR 空间钉）加权后验：`EDB_score=Σvote·eff_w/Σeff_w`，`置信=100·|EDB|·一致度·覆盖度·GGR乘子`（v0.5.3 起含信息量加权与覆盖度折扣）。
- `skew`（SRD）：25Δ 风险逆转方向票，方向用相对偏斜 `rr_z`+动量 `ΔRR`，**不用原始符号**（BTC 偏斜结构性为负）。
- `gamma_regime`（GGR）：全局 Gamma 区制，**首先是单边卖权安全门**（负 Gamma 放大→砍/否决），其次置信调制，最后钉住区给小空间票。
- `bias_thesis`：v0.51 起**退役**为 EDB 复用的共享 macro/funding verdict helper，独立 arbiter 与前端"倾向性论证层"表已移除，不再是权威方向层。
- `signal_events`：最近 10 次确认的 DIE+Anchor 信号事件，用于复盘观察。

## 观察因子

- MPF 宏观压力因子：每小时刷新，显示数据时间、年龄、中文语义标签和组件解释（VOLQ/DXY/US10Y）。
- M-DIE：15m 单向变化程度，带符号倾向值。
- TMV-F micro-flow：4h 快窗与 12h 慢窗，显示涨跌百分比和 CVD BTC（v0.5 起 CVD 独占流向，不再倾斜 TMV 方向）。

## 策略侧（`strategy_recommendation`）

方向来自 EDB：当 EDB 有可交易 lean（`support_label∈{STRONG,WEAK}` 且 `side_hint∈{put,call}`）时由 EDB 定向（`selection_reason=EDB_DIRECTION`）；否则回落 TMV-F 但显式标注 `TMVF_LEGACY_PREVIEW`（观察预览，非推荐）。输出 signal / 24h+48h 期号 / strategy_type / 支持标签，**执行层外置**——不选腿、不报价、不下单。状态栏前端为 6 表（总览 / EDB 到期方向合成层 / 信号事件 / 数据源与定时 / 主链路与因子状态 / 宏观要素）。

阈值校准见 `demo/CALIBRATION_PLAN_V0.5.md`（当前为稳健默认，待实盘数据校准）。

## 交付与验证

生成 FMZ 单文件：

```powershell
tools/build_fmz_single.ps1
```

运行期验证（本机已有 Python 3.12，可直接做；受限环境下 `-ExecutionPolicy Bypass` 的 ps1 可能被拦，用 python 直跑等效）：

```powershell
$env:PYTHONIOENCODING="utf-8"
python -m compileall demo
python -c "import demo.main as m; m.run_offline_fixture_once()"
python tools/calibrate_edb.py --window-open-only
# 单文件同步 / 交付摘要 / 预检 / 静态 / 运行期
tools/build_fmz_single.ps1 -Check
tools/update_delivery_summary.ps1 -Check
tools/fmz_preflight_demo.ps1
tools/static_validate_demo.ps1
tools/runtime_check_demo.ps1
```
