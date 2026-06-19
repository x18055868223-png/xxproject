> 当前执行层口径（r2.1 / 2026-06-19）：本因子文档可能保留早期 v1.6.2/KPF/Phase 1 描述；当前执行交付物以 `demo/最新交付物/spm_calendar_protected_short_v1.py` `STRATEGY_VERSION=2.5.0` 为准，交易门默认关闭。本文用于解释历史设计和组件语义，不代表当前已启用交易。
# 04 · execution（执行纪律：保护腿优先 / maker-only / 只追一步）

> 模块：② 执行层
> canonical：`Deribit期权交易执行层\src\execution.py`（`exec_*`）
> 最后核对：2026-06-02（源码）

## 1. 一句话定位
下单纪律层。**保护腿优先、maker-only(post_only)、最多追一步、禁 taker/market**。`ALLOW_TRADING=False` 或 `KILL_SWITCH=True` 时所有真实下单短路为"记录意图"（空跑核对）。

## 2. 当前具体实现（`execution.py`）
- **纯价格计算**（可单测）：
  - `exec_buy_price`（买保护）：step0=`min(mark, ask−tick)`，每追一步 +tick，封顶 `ask−tick`。
  - `exec_sell_price`（卖 short）：step0=`max(mark, bid+tick)`，每追一步 −tick，封底 `bid+tick`。
  - `_round_to_tick` 带 1e-9 epsilon 抵消浮点误差。
- **maker_only 成交**（`exec_maker_only_fill`）：
  - 空跑（`not (ALLOW_TRADING and not KILL_SWITCH)`）→ 只记意图（含追价档），`filled=0, dry=True`。
  - 实盘：先过价差守门（`spread_ratio>MAX_SPREAD_RATIO` 放弃）；循环 `MAX_CHASE_STEPS+1` 步，`dbt_place_order(post_only=True, reject_post_only=True)`；挂单被拒（会越价）→ 追一步；等 `CHASE_WAIT_SECONDS` 查状态，未全成交则撤残单进下一步。
- **保护腿优先开仓**（`exec_open_structure`）：先买 protection；保护腿未成交→**按原则不卖 short**；`short_amount = min(amount, filled_prot)`（硬保证 short ≤ 保护腿可用量）；短腿未成交且 `UNWIND_PROTECTION_ON_NO_SHORT=True`→自动 maker 卖回保护腿避免裸保护（一次）。

## 3. 关键阈值（现值，`config.py`）
`MAX_CHASE_STEPS=1`、`CHASE_WAIT_SECONDS=8`、`MAX_SPREAD_RATIO=0.60`、`UNWIND_PROTECTION_ON_NO_SHORT=True`、`ALLOW_TRADING=False`、`KILL_SWITCH=False`。

## 4. 整合中的路径修改
**零 KPF 关联，整合不动**。这是整合"不变量"的物理承载：不裸卖、保护腿在卖方腿外侧、固定风险比例、硬门控不因错过行情放宽（总纲 v0.3 §11.3）。

## 5. 当前目标 / 待办
- **闸 A 管道验证**直接验它：一次性极小定额真单跑通"保护腿→短腿→成交→记账→平仓"，验 maker-only 真成交、只追一步、`_G` 重启恢复、记账一致；跑完立即关 `ALLOW_TRADING`。
- 限频预算（maker-only、最多追一步）属运营软前提。

## 6. 边界与陷阱
- **保护腿永远先于 short**——保护腿没成交绝不卖裸 short；这是不可妥协的安全纪律。
- post_only + reject_post_only：会越价的单直接拒，宁可追一步也不吃单（不做 taker）。
- `ALLOW_TRADING` 是最后一道人工闸，空跑下两腿都只记意图——所有上线前核对都在空跑态完成。
