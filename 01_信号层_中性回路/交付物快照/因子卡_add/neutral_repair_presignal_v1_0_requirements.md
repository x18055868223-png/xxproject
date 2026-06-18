# DIE + Anchor 基础中性修复信号 v1.0 需求文档

适配代码：`neutral_regulation_demo_fmz.py` / `demo_version = 0.3.0`  
目标模块名建议：`NeutralRepairPreSignal`  
输出位置建议：`factor_snapshot["neutral_repair_signal"]`  
下游读取者：`BiasThesisArbiter v1.0`（倾向性论证层）  
交付目标：为“DIE + Anchor 已给出基础中性修复信号”建立清晰、可落地、可审计的状态机与输出契约。

> **本版为前期测试观察阈值版。**  
> 为便于验证状态机是否被正确触发，v1.0 初测阶段临时放宽阈值：  
> `abs(M-DIE) > 0.65` 即视为单向事件；Anchor 受损与修复均以 60 分为观察边界。  
> 后续正式部署阶段，可再收紧为 `abs(M-DIE) > 0.80` 与 `Anchor repair >= 70`。

---

## 0. 设计结论

当前 0.3 代码已经具备两个独立因子：

```text
M-DIE：15m 短周期单向价格偏移事件因子
Anchor：基于 GEX effective flip、band_half、ND、144 Bar |ND| 均值生成的锚可用性因子
```

但当前代码还没有把二者串成一个“事件 → 修复”的前置信号。  
当前 `M-DIE` 只是作为观察因子进入 `factor_snapshot`，状态栏里也明确表达为“纯价格路径观测，不参与策略判断”。因此，需要新增一个状态机模块：

```text
DIE Displacement Event
        ↓
Anchor Damage / Dislocation Observation
        ↓
Anchor Repair Confirmation
        ↓
Neutral Loop Reactivation Pre-Signal
```

本模块不判断方向，不调用 TMV-F，不调用 CVD，不调用 Funding，不调用 Macro，不调用 KPF，不选腿，不下单。

它只回答：

> 市场是否先发生过短周期单向再定价偏移，然后 Anchor 是否重新修复到足以说明中性回路重新生效？

---

## 1. 当前 0.3 代码事实

### 1.1 M-DIE 当前实现

当前 `compute_m_die()` 使用：

```text
interval = 1m
window = 15m
n_bars = 15
kline_limit = 40
```

其核心逻辑为：

1. 使用最近 15 根 1m 已收盘 K 线；
2. 计算窗口总 log return；
3. 如果总 return 未超过 `m_die_return_floor = 0.0006`，输出无方向；
4. 若超过门槛，计算：
   - displacement：位移强度；
   - path_efficiency：路径效率；
   - directional_persistence：方向持续性；
5. 最终得分：

```text
score = 0.40 * displacement
      + 0.40 * path_efficiency
      + 0.20 * directional_persistence

m_die = direction * score
```

当前等级：

```text
abs(m_die) < 0.25  → NO_DIRECTIONAL_MOVE
0.25–0.45          → MILD_DIRECTIONAL_MOVE
0.45–0.65          → CLEAR_DIRECTIONAL_MOVE
>= 0.65            → STRONG_DIRECTIONAL_MOVE
```

### 1.2 Anchor 当前实现

当前 Anchor 使用：

```text
effective flip
band_half
normalized_deviation = (price - flip_point) / band_half
anchor_gravity_window = 144
anchor_gravity_warmup = 20
anchor_gravity_trim_each_side = 1
anchor_gravity_ref_score = 100 * exp(-mean_abs_ND)
```

当前 Anchor 标签：

```text
score is None   → Warming
score < 30      → Detached
30–60           → Loose
60–90           → Attached
>=90            → Tightly Attached
```

当前 Anchor state：

```text
无 GEX / 过期 / bar 缺失 / band 不可用 → Invalid
有 stale / deviation wide / pending 等 reason → Weak
无 reason → Valid
```

### 1.3 当前链路缺口

当前 `tick()` 顺序中：

```text
external = evaluate_external_gate(...)
anchor   = _evaluate_anchor_for_completed_bars(...)
tmvf     = evaluate_tmvf(...)
strategy = build_strategy_recommendation(tmvf, ...)
m_die    = compute_m_die(...)
factor_snapshot = build_factor_snapshot(..., macro_pressure, m_die)
decision_snapshot = decide(...)
```

这说明：

```text
M-DIE 已被计算并进入 factor_snapshot；
Anchor 已被计算并进入 module_results / factor_snapshot；
但没有任何模块把 M-DIE 与 Anchor 串成“中性修复前置信号”。
```

因此本需求的核心是新增：

```text
NeutralRepairPreSignal / DIE + Anchor 状态机
```

---

## 2. 模块定位

### 2.1 模块名称

建议：

```text
NeutralRepairPreSignal
```

中文名：

```text
DIE + Anchor 基础中性修复信号
```

### 2.2 模块职责

负责：

```text
识别“先有短周期单向再定价事件，后有 Anchor 修复”的时序结构。
```

不负责：

```text
不判断多空倾向；
不判断信号质量；
不读取 TMV-F / CVD / Funding / Macro；
不读取 KPF；
不选腿；
不输出 Trade / No Trade；
不做持仓后风控。
```

### 2.3 下游关系

本模块输出是倾向性论证层的前置条件：

```text
if neutral_repair_signal.is_active:
    BiasThesisArbiter 可以运行
else:
    BiasThesisArbiter 不应输出可交易倾向
```

---

## 3. 第一性逻辑

### 3.1 为什么不能只看 Anchor 高分？

单独 Anchor 高分只能说明：

```text
当前价格正在被中性锚解释。
```

但它不能说明：

```text
市场刚经历过再定价；
再定价后仍残留 Vega / IV 溢价；
当前是“修复后的中性回路”，而不是普通中性状态。
```

因此，Anchor 高分不是本模块的充分条件。

### 3.2 为什么不能只看 M-DIE？

单独 M-DIE 高分只能说明：

```text
最近 15m 出现单向价格偏移。
```

它不能说明：

```text
偏移已经结束；
中性回路重新生效；
市场已经回到可被锚解释的状态。
```

因此，M-DIE 高分也不是本模块的充分条件。

### 3.3 正确结构

正确结构必须是时序结构：

```text
Step 1：M-DIE 捕捉短周期单向偏移；
Step 2：Anchor 出现受损或脱离迹象；
Step 3：M-DIE 退出强单向状态；
Step 4：Anchor 分数重新修复到测试阈值 60+；
Step 5：输出基础中性修复信号。
```

核心句：

> 不是“DIE 高 + Anchor 高 = 信号”，而是“DIE 先打破中性结构，随后 Anchor 修复 = 中性回路重新生效信号”。

---

## 4. 状态机设计

### 4.1 状态枚举

```text
NR_IDLE
NR_DISPLACEMENT_ACTIVE
NR_WAIT_ANCHOR_DAMAGE
NR_WAIT_ANCHOR_REPAIR
NR_REPAIR_CANDIDATE
NR_REPAIR_CONFIRMED
NR_REPAIR_STALE
NR_DATA_INSUFFICIENT
```

### 4.2 状态语义

| 状态 | 含义 | 是否允许下游论证层运行 |
|---|---|---|
| `NR_IDLE` | 无有效 DIE 事件上下文 | 否 |
| `NR_DISPLACEMENT_ACTIVE` | M-DIE 正在强单向偏移 | 否 |
| `NR_WAIT_ANCHOR_DAMAGE` | 已有 DIE 事件，但尚未观察到 Anchor 受损 | 否 |
| `NR_WAIT_ANCHOR_REPAIR` | 已观察到 Anchor 受损，等待修复 | 否 |
| `NR_REPAIR_CANDIDATE` | Anchor 已回到修复阈值，但确认根数不足 | 否 |
| `NR_REPAIR_CONFIRMED` | 基础中性修复信号成立 | 是 |
| `NR_REPAIR_STALE` | DIE 事件太久，修复失去因果关联 | 否 |
| `NR_DATA_INSUFFICIENT` | M-DIE 或 Anchor 数据不足 | 否 |

---

## 5. 关键阈值建议：v1.0 初测观察版

### 5.1 M-DIE 阈值

为便于前期观察状态机是否触发，本版采用：

```text
nr_mdie_event_threshold = 0.65
condition: abs(m_die) > 0.65
```

语义：

```text
abs(m_die) > 0.65：正式记录一次短周期单向再定价事件。
```

说明：

```text
当前代码自身已经把 abs(m_die) >= 0.65 标记为 STRONG_DIRECTIONAL_MOVE；
因此测试阶段直接使用 0.65 作为事件阈值，便于观察日志与状态迁移。
```

正式部署阶段可收紧为：

```text
abs(m_die) > 0.80
```

### 5.2 Anchor 修复阈值

前期测试阶段采用：

```text
nr_anchor_repair_score = 60.0
```

语义：

```text
Anchor 分数重新回到 60+，即视为测试阶段的有效锚修复候选。
```

说明：

```text
当前 Anchor 原始语义中，60–90 已经属于 Attached；
因此以 60 作为前期观测版 repair boundary 更便于触发和验证。
```

正式部署阶段可收紧为：

```text
Anchor repair score >= 70
```

### 5.3 Anchor 受损阈值

前期测试阶段采用同一观察边界：

```text
nr_anchor_damage_score = 60.0
```

即以下任意条件成立，即视为 Anchor damage observed：

```text
anchor_score < 60
或 anchor_state = Weak 且 reason 包含 ANCHOR_DEVIATION_WIDE
或 abs(normalized_deviation) >= 1.0
```

说明：

```text
由于 anchor_gravity_ref_score 是 144 Bar |ND| 平滑结果，短时事件可能不会立刻把分数打低；
因此必须允许 raw ND 或 Anchor Weak reason 作为受损证据。
```

注意：

```text
测试版使用 60 作为 damage / repair 共同边界，可能更容易产生边界反复；
因此必须保留 repair_confirm_ticks 作为抗抖机制。
```

### 5.4 M-DIE 冷却阈值

本版采用：

```text
nr_mdie_cooldown_abs = 0.65
```

即：

```text
current abs(m_die) < 0.65
```

或当前 `level != STRONG_DIRECTIONAL_MOVE`。

说明：

```text
M-DIE 仍处于强单向推进时，不允许输出中性修复信号。
```

### 5.5 修复确认根数

为避免单次 tick 噪声，建议：

```text
nr_repair_confirm_ticks = 2
```

含义：

```text
连续 2 次评估 Anchor score >= 60 且 M-DIE 已冷却，才输出确认。
```

如果运行周期为 60 秒，这相当于至少约 2 分钟确认。  
这相当于软件层面的 debounce / 防抖确认：输入信号需要稳定一段时间才被视为有效切换，避免阈值附近反复触发。

### 5.6 事件有效期

DIE 是 15m 事件。若 Anchor 很久之后才修复，二者因果关系变弱。

建议：

```text
nr_repair_context_ttl_min = 360
```

即 6 小时。

若超过 6 小时仍未修复：

```text
NR_REPAIR_STALE
```

原因：

```text
24h/48h 期权择时可以接受数小时级修复等待；
但超过 6 小时，原 DIE 事件对当前中性修复的解释力显著下降。
```

### 5.7 信号有效期

修复信号确认后，不应永久有效。

建议：

```text
nr_repair_signal_ttl_min = 60
```

含义：

```text
NR_REPAIR_CONFIRMED 后 60 分钟内可被倾向性论证层读取；
超过后转为过期，需要新的 DIE 事件。
```

---

## 6. 状态迁移规则

### 6.1 从 IDLE 到 DISPLACEMENT_ACTIVE

条件：

```text
abs(m_die) > nr_mdie_event_threshold
m_die.data_status.data_state == "OK"
anchor 数据可用
```

动作：

```text
创建 event_context：
- event_id
- event_direction
- event_start_ms
- event_last_seen_ms
- event_peak_abs_mdie
- event_peak_mdie
- event_move_shape
- price_at_event
- anchor_score_at_event
- anchor_nd_at_event
- min_anchor_score_after_event
- max_abs_nd_after_event
- anchor_damage_observed = False
```

输出状态：

```text
NR_DISPLACEMENT_ACTIVE
```

### 6.2 继续 DISPLACEMENT_ACTIVE

条件：

```text
abs(m_die) >= nr_mdie_cooldown_abs
```

动作：

```text
更新 event_last_seen_ms；
更新 peak_abs_mdie；
更新 min_anchor_score_after_event；
更新 max_abs_nd_after_event；
检查 anchor_damage_observed。
```

输出：

```text
仍为 NR_DISPLACEMENT_ACTIVE
```

### 6.3 进入 WAIT_ANCHOR_DAMAGE

条件：

```text
abs(m_die) < nr_mdie_cooldown_abs
且 anchor_damage_observed = False
```

输出：

```text
NR_WAIT_ANCHOR_DAMAGE
```

说明：

```text
这表示发生过强 DIE，但尚未观察到 Anchor 受损。
此时不输出中性修复信号。
```

### 6.4 进入 WAIT_ANCHOR_REPAIR

条件：

```text
anchor_damage_observed = True
且 anchor_score < nr_anchor_repair_score
```

输出：

```text
NR_WAIT_ANCHOR_REPAIR
```

### 6.5 进入 REPAIR_CANDIDATE

条件：

```text
anchor_damage_observed = True
anchor_score >= nr_anchor_repair_score
abs(m_die) < nr_mdie_cooldown_abs
event_age <= nr_repair_context_ttl_min
```

动作：

```text
repair_confirm_count += 1
```

若 `repair_confirm_count < nr_repair_confirm_ticks`：

```text
NR_REPAIR_CANDIDATE
```

### 6.6 进入 REPAIR_CONFIRMED

条件：

```text
repair_confirm_count >= nr_repair_confirm_ticks
```

输出：

```text
NR_REPAIR_CONFIRMED
is_active = True
```

这是倾向性论证层的前置激活信号。

### 6.7 进入 REPAIR_STALE

条件：

```text
event_age > nr_repair_context_ttl_min
```

动作：

```text
标记 stale；
不再允许该事件触发修复信号。
```

### 6.8 重置条件

满足任意条件重置上下文：

```text
new opposite M-DIE event with abs(m_die) > nr_mdie_event_threshold
anchor state = Invalid
anchor freshness = EXPIRED
m_die data invalid
repair_signal_ttl 超时
manual reset
```

---

## 7. 输出契约

### 7.1 输出字段

建议输出：

```json
{
  "schema_name": "NeutralRepairPreSignal",
  "schema_version": "neutral_repair.v1.0",
  "threshold_profile": "relaxed_test",
  "state": "NR_REPAIR_CONFIRMED",
  "is_active": true,
  "label": "BASE_NEUTRAL_REPAIR_SIGNAL_ACTIVE",
  "confidence": 72,
  "event_context": {
    "event_id": "nr_20260526_123456_UP",
    "event_direction": "UP",
    "event_start_ms": 0,
    "event_last_seen_ms": 0,
    "event_age_min": 34.5,
    "event_peak_abs_mdie": 0.72,
    "event_peak_mdie": 0.72,
    "event_move_shape": "DRIFT_RUN",
    "price_at_event": 77000.0
  },
  "anchor_context": {
    "anchor_score": 62.4,
    "anchor_repair_score": 60.0,
    "anchor_score_at_event": 72.1,
    "min_anchor_score_after_event": 55.8,
    "anchor_damage_observed": true,
    "normalized_deviation": 0.42,
    "max_abs_nd_after_event": 1.32,
    "repair_confirm_count": 2
  },
  "gating": {
    "m_die_event_ok": true,
    "m_die_cooldown_ok": true,
    "anchor_damage_ok": true,
    "anchor_repair_ok": true,
    "not_stale": true,
    "data_ready": true
  },
  "reason_codes": [
    "DIE_EVENT_DETECTED_RELAXED_065",
    "ANCHOR_DAMAGE_OBSERVED_BELOW_60",
    "ANCHOR_REPAIRED_60_PLUS"
  ],
  "interpretation_cn": "测试观察阈值下，市场先出现短周期单向再定价偏移，随后 Anchor 重新修复到 60+，基础中性回路修复信号成立。"
}
```

### 7.2 标签枚举

```text
BASE_NEUTRAL_REPAIR_SIGNAL_ACTIVE
WAIT_DIE_EVENT
DIE_EVENT_ACTIVE_WAIT_COOLDOWN
WAIT_ANCHOR_DAMAGE
WAIT_ANCHOR_REPAIR
REPAIR_CANDIDATE_NEEDS_CONFIRMATION
REPAIR_CONTEXT_STALE
DATA_INSUFFICIENT
```

### 7.3 confidence 语义

本模块 confidence 不是交易置信度，不是方向置信度，也不是盈利概率。

它只表示：

```text
DIE → Anchor 修复这一前置信号链条的完整程度。
```

测试观察版建议分数更保守：

```text
base = 45

+ DIE peak strength:
    abs(m_die) >= 0.90 → +15
    abs(m_die) >= 0.80 → +11
    abs(m_die) >  0.65 → +7

+ anchor damage evidence:
    score < 60 → +8
    abs(ND) >= 1.0 → +6
    Anchor Weak / ANCHOR_DEVIATION_WIDE → +6

+ anchor repair:
    score >= 60 → +8
    score >= 65 → +11
    score >= 70 → +15

+ shape:
    DRIFT_RUN → +4
    IMPULSE_SHIFT → +2
    CHOPPY_DRIFT → 0

penalties:
    anchor stale → cap 55
    no damage observed → cap 45
    m_die not cooled → cap 50
    repair candidate not confirmed → cap 60
    event age > ttl → cap 0
```

测试版 confidence 分档：

```text
>=70：High-quality test repair signal
60–70：Standard test repair signal
45–60：Candidate / waiting
<45：Not active
```

---

## 8. 与倾向性论证层的接口

倾向性论证层 1.0 的前置条件应读取：

```python
factor_snapshot["neutral_repair_signal"]["is_active"] is True
```

如果不成立：

```text
BiasThesisArbiter 不输出 TRADE_SUPPORT_STRONG / TRADE_SUPPORT_WEAK。
```

可输出：

```text
NO_TRADE_BLOCKED
reason = DIE_ANCHOR_PRECONDITION_NOT_ACTIVE
```

### 8.1 下游传递字段

倾向性论证层可以读取但不直接改变方向的字段：

```text
event_direction
event_peak_abs_mdie
event_move_shape
anchor_score
min_anchor_score_after_event
event_age_min
confidence
threshold_profile
```

用途：

```text
只用于解释基础信号质量，不用于替代 TMV-F / CVD / Funding / Macro 的倾向性判断。
```

---

## 9. 与当前 0.3 代码的落地方式

### 9.1 最小实现方式

新增一个状态类：

```python
class NeutralRepairSignalTracker:
    def __init__(self, config=None):
        self.config = config or CONFIG
        self.context = None
        self.last_output = None

    def update(self, m_die, anchor, runtime_facts=None):
        ...
```

在 `NeutralRegulationDemo.__init__()` 中新增：

```python
self.neutral_repair_tracker = NeutralRepairSignalTracker(self.config)
```

在 `tick()` 中，`m_die` 和 `anchor` 都已经可用后调用：

```python
neutral_repair_signal = self.neutral_repair_tracker.update(
    m_die=m_die,
    anchor=anchor,
    runtime_facts=self._runtime_facts(),
)
```

然后写入：

```python
factor_snapshot["neutral_repair_signal"] = neutral_repair_signal
```

### 9.2 不建议第一版加入 module_results

当前 `validate_evaluation_contract()` 固定检查：

```text
MODULE_SEQUENCE = (External Gate, Anchor, TMV-F)
```

若直接新增模块会导致 contract mismatch。

因此 v1.0 建议：

```text
只作为 factor_snapshot 子对象；
不加入 module_results；
不改变 Decision 结构。
```

后续 v1.1 再考虑将其升级成正式 module。

### 9.3 当前 strategy_recommendation 的处理

当前代码中 `build_strategy_recommendation()` 仍然直接根据 TMV-F 生成：

```text
put_credit_spread / call_credit_spread / none
```

在新架构下，这应视为旧的诊断输出，不应代表真实交易推荐。

建议第一版不强行删除，但要在输出中标注：

```text
strategy_recommendation = legacy_tmvf_direction_preview
```

后续应改成：

```text
DIE + Anchor Signal
    → BiasThesisArbiter
    → KPF
    → Option Module
```

而不是 TMV-F 直接推荐策略方向。

---

## 10. 新增配置项建议：测试观察版

```python
"nr_threshold_profile": "relaxed_test",

"nr_mdie_event_threshold": 0.65,
"nr_mdie_event_condition": "strict_greater",
"nr_mdie_cooldown_abs": 0.65,

"nr_anchor_repair_score": 60.0,
"nr_anchor_damage_score": 60.0,
"nr_anchor_damage_nd_abs": 1.0,

"nr_repair_confirm_ticks": 2,
"nr_repair_context_ttl_min": 360,
"nr_repair_signal_ttl_min": 60,

"nr_require_anchor_damage": True,
"nr_allow_nd_damage_evidence": True,
"nr_reset_on_opposite_event": True
```

正式部署可切换为：

```python
"nr_threshold_profile": "production_candidate",

"nr_mdie_event_threshold": 0.80,
"nr_anchor_repair_score": 70.0,
"nr_anchor_damage_score": 60.0
```

---

## 11. 伪代码

```python
def update(self, m_die, anchor, runtime_facts=None):
    now = now_ms()

    m_val = safe_float(m_die.get("m_die"))
    m_abs = abs(m_val or 0.0)
    m_dir = m_die.get("direction")
    m_shape = m_die.get("move_shape")
    m_data_ok = (m_die.get("data_status") or {}).get("data_state") == "OK"

    facts = anchor.get("facts") or {}
    anchor_score = safe_float(facts.get("anchor_gravity_ref_score"))
    nd = safe_float(facts.get("normalized_deviation"))
    anchor_state = anchor.get("state")
    anchor_quality = anchor.get("quality")
    anchor_reasons = anchor.get("reasons") or []

    if not m_data_ok or anchor_score is None:
        return data_insufficient()

    if anchor_state == STATE_INVALID:
        self.context = None
        return blocked("ANCHOR_INVALID")

    # 1. 新 DIE 事件：测试版使用 > 0.65
    if m_abs > cfg["nr_mdie_event_threshold"]:
        if self.context is None or is_opposite_event(self.context, m_val):
            self.context = new_context(m_die, anchor, runtime_facts, now)
        else:
            update_context_peak(self.context, m_die, anchor, now)
        mark_anchor_damage_if_needed(self.context, anchor)
        return output("NR_DISPLACEMENT_ACTIVE")

    # 2. 无事件上下文
    if self.context is None:
        return output("NR_IDLE")

    # 3. TTL 检查
    if event_age_min(self.context, now) > cfg["nr_repair_context_ttl_min"]:
        self.context["stale"] = True
        return output("NR_REPAIR_STALE")

    # 4. 更新 damage
    mark_anchor_damage_if_needed(self.context, anchor)

    # 5. 未观察到 damage
    if not self.context["anchor_damage_observed"]:
        return output("NR_WAIT_ANCHOR_DAMAGE")

    # 6. DIE 尚未冷却
    if m_abs >= cfg["nr_mdie_cooldown_abs"]:
        return output("NR_DISPLACEMENT_ACTIVE")

    # 7. 等待 Anchor 修复：测试版使用 60
    if anchor_score < cfg["nr_anchor_repair_score"]:
        self.context["repair_confirm_count"] = 0
        return output("NR_WAIT_ANCHOR_REPAIR")

    # 8. Anchor repair candidate
    self.context["repair_confirm_count"] += 1
    if self.context["repair_confirm_count"] < cfg["nr_repair_confirm_ticks"]:
        return output("NR_REPAIR_CANDIDATE")

    # 9. confirmed
    return output("NR_REPAIR_CONFIRMED", is_active=True)
```

---

## 12. 状态解释示例

### 12.1 只有 Anchor 高，没有 DIE

```json
{
  "state": "NR_IDLE",
  "is_active": false,
  "label": "WAIT_DIE_EVENT",
  "interpretation_cn": "当前 Anchor 可能有效，但没有前置短周期单向再定价事件，因此不构成中性修复信号。"
}
```

### 12.2 DIE 正在强偏移

```json
{
  "state": "NR_DISPLACEMENT_ACTIVE",
  "is_active": false,
  "label": "DIE_EVENT_ACTIVE_WAIT_COOLDOWN",
  "interpretation_cn": "测试阈值下，市场正在发生短周期单向偏移，尚不能判断中性回路修复。"
}
```

### 12.3 DIE 后未观察到 Anchor damage

```json
{
  "state": "NR_WAIT_ANCHOR_DAMAGE",
  "is_active": false,
  "label": "WAIT_ANCHOR_DAMAGE",
  "interpretation_cn": "已出现测试阈值下的 DIE 单向事件，但尚未观察到 Anchor 低于 60 或 ND 脱离，因此不能确认这是一次再定价后的修复结构。"
}
```

### 12.4 Anchor 修复候选

```json
{
  "state": "NR_REPAIR_CANDIDATE",
  "is_active": false,
  "label": "REPAIR_CANDIDATE_NEEDS_CONFIRMATION",
  "interpretation_cn": "Anchor 已重新回到 60+，但确认次数不足，等待下一次评估确认。"
}
```

### 12.5 基础中性修复信号成立

```json
{
  "state": "NR_REPAIR_CONFIRMED",
  "is_active": true,
  "label": "BASE_NEUTRAL_REPAIR_SIGNAL_ACTIVE",
  "interpretation_cn": "测试观察阈值下，市场先发生短周期单向再定价偏移，随后 Anchor 修复至 60+，基础中性回路修复信号成立。"
}
```

---

## 13. 关键审查点

### 13.1 必须保留时序性

不允许实现成：

```text
if abs(m_die) > 0.65 and anchor_score > 60:
    active = True
```

这是错误的。

正确是：

```text
过去发生 DIE event；
中间观察到 Anchor damage；
当前 M-DIE 冷却；
当前 Anchor repair；
连续确认；
才 active。
```

### 13.2 必须要求 Anchor damage

若没有 Anchor damage，只能说明：

```text
Anchor 一直有效；
DIE 可能只是锚内噪声或短促偏移；
不构成“修复”。
```

因此不应激活下游论证层。

### 13.3 必须有 TTL

如果 DIE 事件太久，Anchor 后续修复不应再归因于该 DIE。

### 13.4 必须输出等待状态

状态机不能只有 active / inactive。  
它必须输出：

```text
正在偏移
等待受损
等待修复
修复候选
修复确认
过期
数据不足
```

这样才能帮助用户理解当前卡在哪一步。

### 13.5 测试阈值不得被误认为正式阈值

本版阈值用于观察状态机是否能被触发：

```text
DIE > 0.65
Anchor damage < 60
Anchor repair >= 60
```

正式部署前必须回看触发日志，再决定是否恢复到：

```text
DIE > 0.80
Anchor repair >= 70
```

---

## 14. 验收标准

### 14.1 功能验收

必须满足：

1. M-DIE > 0.65 时创建事件上下文；
2. M-DIE 未冷却时不输出修复信号；
3. 未观察到 Anchor damage 时不输出修复信号；
4. Anchor score 回到 60+ 且确认次数满足后才输出 active；
5. 超过 TTL 后不允许旧事件触发修复；
6. 反向新 M-DIE 强事件会重置上下文；
7. 输出必须进入 `factor_snapshot["neutral_repair_signal"]`；
8. 不改动 `module_results` 固定序列；
9. 不改变当前 read-only 安全边界；
10. 不输出具体交易方向、期权腿或下单建议。

### 14.2 测试场景

| 场景 | 输入 | 期望 |
|---|---|---|
| 无 DIE，Anchor 高 | m_die=0.1, anchor=80 | NR_IDLE |
| DIE 强，Anchor 未损 | m_die=0.66, anchor=80 且 ND 小 | NR_WAIT_ANCHOR_DAMAGE |
| DIE 强，Anchor 损坏 | m_die=0.70, anchor=55 | NR_DISPLACEMENT_ACTIVE / WAIT_REPAIR |
| DIE 冷却，Anchor 未修复 | m_die=0.3, anchor=58 | NR_WAIT_ANCHOR_REPAIR |
| DIE 冷却，Anchor 60+ 一次 | m_die=0.3, anchor=61 | NR_REPAIR_CANDIDATE |
| DIE 冷却，Anchor 60+ 两次 | m_die=0.2, anchor=62 | NR_REPAIR_CONFIRMED |
| 新反向 DIE | context=UP, new m_die=-0.68 | 重置为新 DOWN context |
| Anchor invalid | anchor invalid | DATA / blocked, reset |
| 事件超时 | age>360min | NR_REPAIR_STALE |

---

## 15. Codex 落地提示词摘要

后续交给 Codex 时可直接要求：

```text
在 neutral_regulation_demo_fmz.py 中新增 NeutralRepairSignalTracker。
该 tracker 读取 compute_m_die() 结果和 evaluate_anchor() 结果，
实现 DIE → Anchor damage → Anchor repair 的状态机。
测试观察阈值使用：
- abs(m_die) > 0.65 作为 DIE event；
- anchor_score < 60 作为 Anchor damage；
- anchor_score >= 60 作为 Anchor repair；
- M-DIE 冷却条件为 abs(m_die) < 0.65；
- repair confirm ticks = 2；
- context TTL = 360 minutes；
- signal TTL = 60 minutes。
输出写入 factor_snapshot["neutral_repair_signal"]。
不要加入 module_results，不要改动 MODULE_SEQUENCE。
不要改变 read_only_demo，不要新增下单，不要输出期权腿。
倾向性论证层后续读取 neutral_repair_signal.is_active 作为前置条件。
```

---

## 16. 本模块核心一句话

DIE + Anchor 基础中性修复信号不是“DIE 高且 Anchor 高”的静态组合，而是一个有时序、有记忆、有拒绝状态的修复确认器；在 v1.0 前期测试观察版中，它要求市场先出现 `abs(M-DIE) > 0.65` 的短周期单向再定价偏移，随后观察到 Anchor 低于 60 或 ND 脱离，再等待 M-DIE 冷却并确认 Anchor 分数重新修复到 60+，最后才输出基础中性回路修复信号，允许倾向性论证层继续工作。
