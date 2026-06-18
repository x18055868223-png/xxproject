# M-DIE v1.1 定稿版：15m 短期单向变化程度因子

## 1. 因子名称

**M-DIE = Micro Directional Imbalance Extent**

中文名：**15m 短期单向变化程度因子**

版本：**v1.1 Final / 部署观测定稿版**

M-DIE v1.1 是一个基于 `1m` 普通时间 K 线、滚动观察最近 `15m` 价格路径的纯价格因子，用于衡量：

> 最近 15 分钟内，市场是否发生了上行或下行的短期单向变化，以及这种单向变化的强度、干净程度和推进质量。

它不是交易信号，不判断趋势延续，不判断反转，不判断是否开仓。  
它只作为后续中性恢复因子、锚/中轴解释因子、流量确认因子的**前置背景观测因子**。

---

## 2. 第一性目标

M-DIE v1.1 回答的问题是：

> 最近 15 分钟，价格路径是否出现了具有方向性的单向变化？

它需要回答：

1. 最近 15 分钟净变化方向是什么；
2. 净变化是否达到最低可观测幅度；
3. 该净变化是否相对窗口内波动足够明显；
4. 价格路径是否较为干净，而不是来回震荡后留下的净变化；
5. 有效 1m 收益段是否有足够覆盖，避免单根 K 线完全主导；
6. 当前单向变化属于连续推进、冲击位移，还是震荡漂移。

它不回答：

1. 单向运动是否会继续；
2. 单向变化是否由成交量、CVD、OI 或资金费率确认；
3. 当前是否适合交易；
4. 当前是否已经偏离中轴或进入压力区；
5. 后续是否会恢复中性状态。

---

## 3. 因子定位

M-DIE v1.1 是：

```text
短期价格路径单向变化观测因子
```

不是：

```text
趋势延续因子
突破确认因子
反转因子
交易信号因子
流量确认因子
中轴偏离因子
```

在整体系统中的正确位置：

```text
单向变化是否发生  →  后续再由中性恢复因子判断是否恢复中性
                  →  后续再由锚/中轴因子判断空间关系
                  →  后续再由流量/成交因子判断是否有真实参与确认
```

M-DIE v1.1 只负责第一步：

```text
最近是否发生过短期方向位移与单向路径变化？
```

---

## 4. 固定周期口径

M-DIE v1.1 固定使用：

```text
K线周期：1m
滚动窗口：15m
窗口K线数量：15根
所需close数量：16个
```

不使用：

```text
5m / 1h
15m / 4h
30m / 24h
任何成交量柱
任何中轴或中性区数据
```

原因：

1. `15m/1m` 足够贴近正在发生的短期价格变化；
2. 15 根 1m K 线可以保留路径结构；
3. 窗口不长，避免较远价格路径污染“最近发生”的语义；
4. 1m close-to-close 收益比单根 high/low/close 位置更稳健；
5. 该因子只需要判断短期单向变化，不需要更大周期背景确认。

---

## 5. 输入字段

每根 1m K 线至少需要：

```text
timestamp
open
high
low
close
```

主计算只使用：

```text
timestamp
close
```

`open/high/low` 只用于基础数据合法性检查，不进入主评分。

不需要：

```text
volume
amount
fundingRate
openInterest
CVD
盘口数据
订单簿数据
情绪数据
中轴数据
Gamma 数据
```

---

## 6. 数据清洗要求

实现时必须满足：

1. K 线按 `timestamp` 升序排列；
2. 重复 `timestamp` 只保留最新一条；
3. 只使用已收盘 1m K 线；
4. `close <= 0` 的 K 线无效；
5. `high < low` 的 K 线无效；
6. 数据不足时输出 `insufficient_bars`；
7. API 回补失败时输出明确数据状态；
8. 不允许使用旧窗口外推；
9. 不允许用未收盘 K 线参与计算；
10. 冷启动时必须通过 API 回补历史 K 线，不等待 15 分钟自然积累。

建议冷启动回补：

```text
limit = 40
```

冷启动回补只用于计算当前值，不要求回填完整历史因子序列。

---

## 7. 滚动计算定义

M-DIE v1.1 永远基于最新已收盘 1m K 线向前回看 15 根 K 线计算。

```text
current_window = 最新已收盘 15 根 1m K线
prev_close = current_window 第一根 K线之前一根 K线的 close
```

因此至少需要：

```text
16 个 close
15 段 close-to-close log return
```

定义：

```text
close_0 = 当前窗口第一根K线之前一根K线的 close
close_1 ... close_15 = 当前窗口15根已收盘K线的 close

r_i = log(close_i / close_{i-1}), i = 1 ... 15
R = sum(r_i) = log(close_15 / close_0)
```

---

## 8. 因子结构

M-DIE v1.1 是连续型有符号因子：

```text
M_DIE = direction × score
```

其中：

```text
direction ∈ {-1, 0, +1}
score ∈ [0, 1]
M_DIE ∈ [-1, +1]
```

解释：

```text
M_DIE > 0：最近15分钟存在上行单向变化
M_DIE < 0：最近15分钟存在下行单向变化
M_DIE = 0：方向不明确或变化太小
abs(M_DIE) 越大：单向变化越强、路径越干净、有效推进覆盖越充分
```

---

## 9. 方向定义

窗口累计对数收益：

```text
R = log(close_15 / close_0)
```

方向定义：

```text
if R > return_floor:
    direction = +1

elif R < -return_floor:
    direction = -1

else:
    direction = 0
```

定稿参数：

```text
return_floor = 0.0006
```

含义：

```text
最近15分钟累计变化小于 0.06% 时，不认为方向明确。
```

方向不明确时：

```text
M_DIE = 0
score = 0
level = NO_DIRECTIONAL_MOVE
move_shape = NO_MOVE
reason = direction_not_clear
```

---

## 10. 核心组件

M-DIE v1.1 使用三个主评分组件：

```text
1. 位移强度 D_final
2. 路径效率 E
3. 覆盖修正后的方向持续性 P_final
```

其中 `D_final` 由两个子项构成：

```text
D_z：波动标准化净位移
A：绝对净位移约束
D_final = sqrt(D_z_score × A_score)
```

---

# 11. 组件一：位移强度 D_final

## 11.1 波动标准化净位移 D_z

窗口实现波动：

```text
RV = std(r_i) × sqrt(15)
```

标准化位移：

```text
displacement_z = abs(R) / max(RV, eps)
```

归一化：

```text
D_z_score = clamp((displacement_z - z_start) / (z_full - z_start), 0, 1)
```

定稿参数：

```text
z_start = 0.6
z_full  = 1.8
eps = 1e-8
```

解释：

```text
displacement_z < 0.6：
    净位移相对窗口波动不明显。

displacement_z ≈ 1.0：
    净变化已经超过窗口内普通波动水平。

displacement_z >= 1.8：
    短窗口净位移相对波动非常明显。
```

---

## 11.2 绝对净位移约束 A

为避免低波动环境下的小幅平滑漂移被高估，引入绝对净位移评分：

```text
abs_R = abs(R)

A_score = clamp((abs_R - r_start) / (r_full - r_start), 0, 1)
```

定稿参数：

```text
r_start = 0.0006
r_full  = 0.0025
```

解释：

```text
abs_R < 0.06%：
    不认为发生可观测方向变化。

abs_R ≈ 0.06% ~ 0.25%：
    单向变化幅度逐步增强。

abs_R >= 0.25%：
    15m 内绝对位移已经较明显。
```

---

## 11.3 最终位移强度 D_final

```text
D_final = sqrt(D_z_score × A_score)
```

设计目的：

1. `D_z_score` 负责判断净位移是否相对短期波动突出；
2. `A_score` 负责判断净位移是否具有绝对意义；
3. 使用几何合成，避免其中一个很高而另一个很低时总分虚高；
4. 防止低波动小漂移被误判为强单向运动；
5. 防止大波动中微弱净变化被误判为方向清晰。

---

# 12. 组件二：路径效率 E

路径效率衡量净位移占总路径长度的比例：

```text
path_efficiency = abs(sum(r_i)) / max(sum(abs(r_i)), eps)
```

范围：

```text
0 ~ 1
```

归一化：

```text
E = clamp((path_efficiency - e_start) / (e_full - e_start), 0, 1)
```

定稿参数：

```text
e_start = 0.35
e_full  = 0.85
```

解释：

```text
path_efficiency < 0.35：
    价格更像震荡，净变化质量较低。

0.35 ~ 0.85：
    路径单向性逐步增强。

path_efficiency >= 0.85：
    净位移占总路径长度比例很高，路径较干净。
```

路径效率用于区分：

```text
一路推进
```

和：

```text
来回震荡后留下的净变化
```

---

# 13. 组件三：覆盖修正后的方向持续性 P_final

## 13.1 有效收益段

微小 1m 波动不参与方向持续性统计：

```text
valid_returns = {r_i | abs(r_i) > micro_return_floor}
```

定稿参数：

```text
micro_return_floor = 0.00005
```

含义：

```text
单根 1m K 线涨跌小于 0.005% 时视为微噪声，不参与方向持续性统计。
```

---

## 13.2 同向比例

```text
same_direction_ratio =
    count(sign(r_i) == direction for r_i in valid_returns)
    / count(valid_returns)
```

如果有效收益段为空：

```text
same_direction_ratio = 0.5
```

---

## 13.3 有效覆盖率

```text
valid_return_count = count(valid_returns)

coverage_ratio = valid_return_count / 15
```

覆盖率用于防止：

```text
1 / 1 = 100%
```

这种低样本同向比例被误判为强持续性。

---

## 13.4 覆盖修正

```text
p_raw = same_direction_ratio × sqrt(coverage_ratio)
```

归一化：

```text
P_final = clamp((p_raw - p_start) / (p_full - p_start), 0, 1)
```

定稿参数：

```text
p_start = 0.45
p_full  = 0.70
```

解释：

```text
same_direction_ratio 高，但 coverage_ratio 低：
    可能只是少数K线主导，不应给满分。

same_direction_ratio 高，且 coverage_ratio 高：
    多数有效1m收益段都沿主方向推进，方向持续性更可信。
```

---

# 14. 主评分公式

M-DIE v1.1 定稿评分：

```text
score =
    0.40 × D_final
  + 0.40 × E
  + 0.20 × P_final
```

最终因子值：

```text
M_DIE = direction × score
```

权重解释：

| 组件 | 权重 | 原因 |
|---|---:|---|
| `D_final` | 40% | 单向变化首先必须有足够净位移，并且该净位移需要同时具备相对波动意义和绝对幅度意义 |
| `E` | 40% | 路径是否干净几乎和净位移同等重要 |
| `P_final` | 20% | 方向持续性用于补充判断，避免少数 K 线完全主导 |

M-DIE v1.1 不因单个组件未达门槛而硬归零。  
唯一硬归零条件是：

```text
direction = 0
```

---

# 15. 强度标签

强度标签只用于解释，不改变因子值。

```text
abs(M_DIE) < 0.25:
    NO_DIRECTIONAL_MOVE
    无明显单向变化

0.25 <= abs(M_DIE) < 0.45:
    MILD_DIRECTIONAL_MOVE
    轻度单向变化

0.45 <= abs(M_DIE) < 0.65:
    CLEAR_DIRECTIONAL_MOVE
    明显单向变化

abs(M_DIE) >= 0.65:
    STRONG_DIRECTIONAL_MOVE
    强单向变化
```

方向标签：

```text
M_DIE > 0:
    UP_MOVE
    上行单向变化

M_DIE < 0:
    DOWN_MOVE
    下行单向变化

M_DIE = 0:
    NO_DIRECTION
    无方向
```

---

# 16. 运动形态 move_shape

`move_shape` 是解释字段，不改变主因子值。

它用于区分：

```text
连续推进
单根冲击
震荡漂移
无明显变化
```

定义：

```text
if score < 0.25:
    move_shape = NO_MOVE

elif coverage_ratio < 0.35 and E > 0.75:
    move_shape = IMPULSE_SHIFT

elif coverage_ratio >= 0.50 and E >= 0.55:
    move_shape = DRIFT_RUN

else:
    move_shape = CHOPPY_DRIFT
```

中文解释：

| move_shape | 中文名 | 含义 |
|---|---|---|
| `NO_MOVE` | 无明显单向变化 | 分数不足，方向变化不明显 |
| `DRIFT_RUN` | 连续推进型 | 有效收益覆盖较充分，路径效率较高，更像连续单向推进 |
| `IMPULSE_SHIFT` | 冲击位移型 | 路径效率高但有效覆盖低，可能由单根或少数 K 线主导 |
| `CHOPPY_DRIFT` | 震荡漂移型 | 有一定净变化，但路径质量或覆盖不足 |

注意：

```text
IMPULSE_SHIFT 不是无效信号。
```

它表示：

```text
市场确实发生了方向位移，
但该位移更像冲击造成，
不是连续推进造成。
```

部署观测时建议同时展示：

```text
m_die
score
direction
level
move_shape
coverage_ratio
path_efficiency
window_return_pct
```

---

# 17. 输出结构

建议输出完整结构：

```json
{
  "factor_name": "M-DIE",
  "factor_version": "v1.1_final",
  "interval": "1m",
  "window": "15m",
  "n_bars": 15,
  "rolling": true,
  "last_closed_bar_time": 1716508800000,
  "direction": "DOWN",
  "m_die": -0.52,
  "score": 0.52,
  "level": "CLEAR_DIRECTIONAL_MOVE",
  "move_shape": "DRIFT_RUN",
  "label_cn": "明显下行单向变化｜连续推进型",
  "components": {
    "displacement": {
      "score": 0.61,
      "raw": {
        "window_log_return": -0.0038,
        "window_return_pct": -0.00379,
        "realized_vol": 0.0031,
        "displacement_z": 1.23,
        "d_z_score": 0.53,
        "abs_return_score": 0.70,
        "d_final": 0.61
      }
    },
    "path_efficiency": {
      "score": 0.58,
      "raw": {
        "efficiency": 0.64
      }
    },
    "directional_persistence": {
      "score": 0.43,
      "raw": {
        "same_direction_ratio": 0.63,
        "valid_return_count": 11,
        "coverage_ratio": 0.7333,
        "p_raw": 0.5396
      }
    }
  },
  "data_status": {
    "source": "api_backfill | live_polling",
    "bars_loaded": 40,
    "bars_required": 16,
    "uses_closed_bars_only": true,
    "data_state": "OK"
  },
  "interpretation_cn": "最近15分钟价格出现明显下行单向变化：净位移较明显，路径效率中等偏高，有效收益覆盖较充分，形态更接近连续推进型。"
}
```

---

# 18. 实现伪代码

```python
def calc_m_die_v11(klines):
    """
    klines:
        已收盘1m K线，按 timestamp 升序。
    """

    n = 15

    closed_klines = remove_unclosed_bars(klines)
    closed_klines = sort_and_dedupe(closed_klines)
    closed_klines = validate_klines(closed_klines)

    if len(closed_klines) < n + 1:
        return build_no_value(reason="insufficient_bars")

    window = closed_klines[-n:]
    prev_close = closed_klines[-n - 1].close

    closes = [prev_close] + [k.close for k in window]

    returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
    ]

    R = sum(returns)

    if R > return_floor:
        direction = +1
    elif R < -return_floor:
        direction = -1
    else:
        return build_zero_result(reason="direction_not_clear")

    # 1. 位移强度
    RV = std(returns) * math.sqrt(n)
    displacement_z = abs(R) / max(RV, eps)

    d_z_score = clamp(
        (displacement_z - z_start) / (z_full - z_start),
        0,
        1
    )

    abs_R = abs(R)
    abs_return_score = clamp(
        (abs_R - r_start) / (r_full - r_start),
        0,
        1
    )

    d_final = math.sqrt(d_z_score * abs_return_score)

    # 2. 路径效率
    path_len = sum(abs(r) for r in returns)
    path_efficiency = abs(R) / max(path_len, eps)

    E = clamp(
        (path_efficiency - e_start) / (e_full - e_start),
        0,
        1
    )

    # 3. 覆盖修正后的方向持续性
    valid_returns = [
        r for r in returns
        if abs(r) > micro_return_floor
    ]

    valid_return_count = len(valid_returns)
    coverage_ratio = valid_return_count / n

    if valid_return_count == 0:
        same_direction_ratio = 0.5
    else:
        same_direction_count = sum(
            1 for r in valid_returns
            if sign(r) == direction
        )
        same_direction_ratio = same_direction_count / valid_return_count

    p_raw = same_direction_ratio * math.sqrt(coverage_ratio)

    P_final = clamp(
        (p_raw - p_start) / (p_full - p_start),
        0,
        1
    )

    # 4. 主分数
    score = (
        0.40 * d_final
        + 0.40 * E
        + 0.20 * P_final
    )

    m_die = direction * score

    # 5. 标签
    level = classify_level(abs(m_die))
    move_shape = classify_move_shape(
        score=score,
        coverage_ratio=coverage_ratio,
        E=E
    )

    return build_result(
        m_die=m_die,
        score=score,
        direction=direction,
        level=level,
        move_shape=move_shape,
        raw_values={
            "R": R,
            "window_return_pct": math.exp(R) - 1,
            "RV": RV,
            "displacement_z": displacement_z,
            "d_z_score": d_z_score,
            "abs_return_score": abs_return_score,
            "d_final": d_final,
            "path_efficiency": path_efficiency,
            "E": E,
            "valid_return_count": valid_return_count,
            "coverage_ratio": coverage_ratio,
            "same_direction_ratio": same_direction_ratio,
            "p_raw": p_raw,
            "P_final": P_final
        }
    )
```

---

# 19. 参数表

| 参数 | 默认值 | 含义 |
|---|---:|---|
| `window` | `15m` | 滚动观察窗口 |
| `interval` | `1m` | K 线周期 |
| `n_bars` | `15` | 窗口 K 线数量 |
| `return_floor` | `0.0006` | 15m 累计方向门槛 |
| `micro_return_floor` | `0.00005` | 单根 1m 微噪声过滤 |
| `z_start` | `0.6` | 标准化净位移开始计分 |
| `z_full` | `1.8` | 标准化净位移满分 |
| `r_start` | `0.0006` | 绝对净位移开始计分 |
| `r_full` | `0.0025` | 绝对净位移满分 |
| `e_start` | `0.35` | 路径效率开始计分 |
| `e_full` | `0.85` | 路径效率满分 |
| `p_start` | `0.45` | 覆盖修正方向持续性开始计分 |
| `p_full` | `0.70` | 覆盖修正方向持续性满分 |
| `eps` | `1e-8` | 防止除零 |

---

# 20. 典型解释

## 20.1 强连续上行

```text
M_DIE = +0.72
level = STRONG_DIRECTIONAL_MOVE
move_shape = DRIFT_RUN
```

解释：

```text
最近15分钟价格出现强上行单向变化，净位移明显，路径较干净，有效收益覆盖充分，更接近连续推进型。
```

---

## 20.2 单根下跌冲击

```text
M_DIE = -0.48
level = CLEAR_DIRECTIONAL_MOVE
move_shape = IMPULSE_SHIFT
```

解释：

```text
最近15分钟价格出现明显下行位移，但该位移主要由少数K线主导，更接近冲击位移型，不应直接理解为连续下行推进。
```

---

## 20.3 震荡后小幅净涨

```text
M_DIE = +0.21
level = NO_DIRECTIONAL_MOVE
move_shape = NO_MOVE
```

解释：

```text
最近15分钟虽有小幅上行净变化，但路径效率和位移强度不足，不构成明显单向变化。
```

---

## 20.4 有净变化但路径混乱

```text
M_DIE = -0.36
level = MILD_DIRECTIONAL_MOVE
move_shape = CHOPPY_DRIFT
```

解释：

```text
最近15分钟存在轻度下行单向变化，但路径并不干净，可能是震荡后留下的净位移。
```

---

# 21. 设计取舍说明

## 21.1 为什么不选择 OLS / R² 方案

理论上，使用 close 序列对时间做 OLS 可以衡量路径是否有序。但在 `15m/1m` 的短窗口内，OLS R² 容易把以下路径误判为有序趋势：

```text
单根冲击后横盘
窗口前低后高但中间混乱
高波动随机净位移
```

因此，虽然 OLS 方案在理论上更完整，但部署到真实市场观测时，可能增加误报。

M-DIE v1.1 选择更稳健的：

```text
净位移 × 路径效率 × 有效覆盖
```

而不是使用更敏感但更容易过拟合短窗口路径形态的 OLS 结构。

---

## 21.2 为什么 move_shape 不进入主分数

`move_shape` 是解释字段，不进入主分数，原因是：

1. 主分数负责连续衡量单向变化强度；
2. 形态标签负责解释该变化的质量类型；
3. 如果形态标签直接压制主分数，可能会掩盖真实发生的冲击位移；
4. 在观测系统中，“冲击位移”本身也是有价值的信息。

因此部署时应同时观察：

```text
score：变化强度
move_shape：变化类型
```

而不是只看单一分数。

---

## 21.3 为什么不引入成交量

M-DIE v1.1 的职责是纯价格路径观察。  
成交量、CVD、OI、资金费率属于后续确认层或其他因子，不应混入本因子。

如果引入成交量，会导致因子语义从：

```text
价格是否发生单向变化
```

变成：

```text
价格是否在成交确认下发生单向变化
```

这会与 TMV-F、流量确认因子或中性恢复因子产生重叠。

---

## 21.4 为什么不使用 high/low/close 位置压力

单根 1m K 线的 high/low/close 位置容易受微观撮合、瞬时插针和盘口噪声影响。  
在 15m 短窗口里，将 CLV 或 intrabar close-location 放入主评分，可能增加噪声。

因此 v1.1 主评分只使用 close-to-close log return。

---

# 22. 部署观测建议

状态栏或日志建议至少输出：

```text
m_die
score
direction
level
move_shape
window_return_pct
D_final
E
P_final
coverage_ratio
path_efficiency
same_direction_ratio
valid_return_count
last_closed_bar_time
data_state
```

推荐中文状态表达：

```text
M-DIE：-0.52｜明显下行单向变化｜连续推进型
15m收益=-0.38%｜位移=0.61｜路径效率=0.58｜持续性=0.43｜覆盖率=73%
```

不建议只展示一个 `m_die` 数值。  
最佳观测方式是：

```text
主分数判断强弱；
方向判断上下；
move_shape 判断运动形态；
组件值判断为什么得到这个分数。
```

---

# 23. 维护规则

后续实现或迭代 M-DIE v1.1 时必须遵守：

1. 只使用 `15m/1m`；
2. 只使用普通时间 K 线；
3. 主计算只使用 close-to-close log return；
4. 只使用已收盘 K 线；
5. 永远基于最新 15 根已收盘 1m K 线滚动计算；
6. 冷启动必须通过 API 回补 K 线；
7. 不使用成交量、CVD、OI、资金费率、盘口、中轴、Gamma；
8. 不引入 OLS/R² 进入主评分；
9. 不引入 intrabar CLV 进入主评分；
10. 方向不明确时才硬归零；
11. 其他组件不做硬事件门控；
12. 输出必须包含原始依据与组件值；
13. `move_shape` 只作为解释字段，不压制主分数；
14. 如需未来改版，必须先用真实市场样本或仿真路径验证误报率变化。

---

# 24. 最终定义

**M-DIE v1.1，Micro Directional Imbalance Extent，15m 短期单向变化程度因子，是一个固定使用 `15m/1m` 滚动窗口的连续型纯价格因子。它通过净位移强度、路径效率和覆盖修正后的方向持续性，衡量最近 15 分钟价格是否发生了上行或下行单向变化，并通过 move_shape 输出该变化属于连续推进、冲击位移还是震荡漂移。**

---

# 25. 一句话总结

**M-DIE v1.1 定稿版只做一件事：用最近 15 根已收盘 1m K 线，稳健观测价格短期单向变化的方向、强度和形态。**
