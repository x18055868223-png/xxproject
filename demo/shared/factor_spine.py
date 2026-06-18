# -*- coding: utf-8 -*-
"""factor_spine —— 因子脊柱：统一因子接口 + 域编排 + 占位安全约定（模式参考实现）。

这是 demo 整合落地的「脊柱」参考实现，不是最终 FMZ 代码，而是**所有因子在 FMZ 构建时
必须收敛到的形状**。它把 03_因子注册表 的治理思想落成可运行代码：

  - 所有因子返回统一 FactorResult（layer 0）。
  - 域编排器 run_domain 把一个域内多个 FactorResult 收束为一个域结论（layer 1→2）。
  - 占位因子用 safe_default 约定：只会更安全（中性无效 / 保守收紧），绝不放松门或夸大机会。
  - 单一真相源 = 同目录 factor_registry.json。

直接运行可打印注册表治理摘要，验证脊柱自洽：
    python factor_spine.py
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional


# --------------------------------------------------------------------------
# 状态（与 02_缺口实现规范 / 注册表 status_legend 一致）
# --------------------------------------------------------------------------
LIVE = "LIVE"                # 已标定/已验证，正常影响决策
PLACEHOLDER = "PLACEHOLDER"  # 已接线未标定，按安全默认行为，不污染决策
WARMING = "WARMING"          # LIVE 逻辑但样本不足，自动降权/降置信
OFFLINE = "OFFLINE"          # 只在回放/标定 harness 跑，不进 live tick

_VALID_STATUS = {LIVE, PLACEHOLDER, WARMING, OFFLINE}


# --------------------------------------------------------------------------
# Layer 0：统一因子结果（所有 31 因子同一形状）
# --------------------------------------------------------------------------
@dataclass
class FactorResult:
    factor_id: str
    status: str
    value: Any = None                       # 数值/裁决，域据此收束
    detail: Dict[str, Any] = field(default_factory=dict)   # 可观测原始读数
    calibration: Dict[str, Any] = field(default_factory=dict)  # {required, needs, placeholder_values}
    reason_codes: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.status not in _VALID_STATUS:
            raise ValueError("非法因子状态: %s (factor_id=%s)" % (self.status, self.factor_id))

    @property
    def affects_decision(self) -> bool:
        """占位/离线因子不直接影响决策（收束干净·不变量3的运行时保障）。
        占位的影响只能经 safe_default 走「更安全」方向，由域编排器显式裁定。"""
        return self.status in (LIVE, WARMING)


# 占位安全默认的两种合法形态（02 规范 §3）
def neutral_placeholder(factor_id: str, needs: str, detail: Optional[dict] = None) -> FactorResult:
    """中性无效：vote/权重/调制 取无效值，不影响决策。用于方向/置信类占位。"""
    return FactorResult(
        factor_id=factor_id, status=PLACEHOLDER, value=0.0,
        detail=detail or {}, calibration={"required": True, "needs": needs},
        reason_codes=["PLACEHOLDER_NEUTRAL_NO_EFFECT"])


def conservative_placeholder(factor_id: str, safe_value: Any, needs: str,
                             detail: Optional[dict] = None) -> FactorResult:
    """保守收紧：只会挡单/缩量/早退。用于风控/门类占位。safe_value 必须是「更安全」侧。"""
    return FactorResult(
        factor_id=factor_id, status=PLACEHOLDER, value=safe_value,
        detail=detail or {}, calibration={"required": True, "needs": needs},
        reason_codes=["PLACEHOLDER_CONSERVATIVE_SAFE"])


# --------------------------------------------------------------------------
# 注册表（单一真相源）
# --------------------------------------------------------------------------
_REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "factor_registry.json")


def load_registry(path: str = _REGISTRY_PATH) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def factors_in_domain(registry: Dict[str, Any], domain: str) -> List[Dict[str, Any]]:
    return [f for f in registry.get("factors", []) if f.get("domain") == domain]


# 执行 FMZ 域顺位 = ExecutionSession 状态机流向（01 架构 §3.2）
EXECUTION_DOMAIN_ORDER = [
    "pricing_gate", "plan", "portfolio_risk", "order",
    "ledger", "position_manage", "hedge", "attribution",
]
# replay 是离线域，不在 live tick；signal_* 域在信号 FMZ 内部，对外收束为 1 包。


# --------------------------------------------------------------------------
# Layer 1→2：域编排器（把域内 FactorResult 收束为一个域结论包）
# --------------------------------------------------------------------------
def run_domain(domain: str,
               factor_fns: Dict[str, Callable[[], FactorResult]],
               output_package_name: str) -> Dict[str, Any]:
    """对一个域内的因子逐个求值，收束为一个域包。

    收束干净·不变量2：域包是该域对外的唯一耦合面，下游只读这个包、不穿透因子内部。
    占位因子（不 affects_decision）的 value 仍写入包，但 live_inputs 只收 LIVE/WARMING，
    占位影响必须由域裁定逻辑显式按「更安全」方向处理（这里给出收束骨架，具体裁定按域实现）。
    """
    results: Dict[str, FactorResult] = {}
    for fid, fn in factor_fns.items():
        r = fn()
        if r.factor_id != fid:
            raise ValueError("因子 id 与注册键不一致: %s vs %s" % (r.factor_id, fid))
        results[fid] = r
    live = {k: v for k, v in results.items() if v.affects_decision}
    placeholders = {k: v for k, v in results.items() if not v.affects_decision}
    return {
        "schema_name": output_package_name,
        "domain": domain,
        "factor_results": {k: asdict(v) for k, v in results.items()},
        "live_factor_ids": sorted(live.keys()),
        "placeholder_factor_ids": sorted(placeholders.keys()),
        "reason_codes": sorted({c for v in results.values() for c in v.reason_codes}),
    }


# --------------------------------------------------------------------------
# 示例：四缺口的占位安全默认（演示约定，最终迁入 execution_build）
# --------------------------------------------------------------------------
def example_portfolio_budget() -> FactorResult:
    # 保守收紧：未标定 → 用保守硬上限，演示「超即 size=0」的更安全方向
    return conservative_placeholder(
        "X10_portfolio_budget",
        safe_value={"max_concurrent": 1, "max_short_gamma": "CONSERVATIVE",
                    "max_short_vega": "CONSERVATIVE", "size_if_exceed": 0.0},
        needs="组合上限/熔断阈 用户拍板风险偏好 + 实盘并发数据",
        detail={"note": "占位偏紧:宁可挡单,绝不放行更多"})


def example_take_profit() -> FactorResult:
    # 保守收紧：未标定 → 偏早止盈
    return conservative_placeholder(
        "X13_take_profit", safe_value={"target_premium_giveback_ratio": 0.5},
        needs="止盈目标比例 回放标定",
        detail={"note": "占位偏早退,绝不晚退"})


# --------------------------------------------------------------------------
# 自检 / 摘要（python factor_spine.py）
# --------------------------------------------------------------------------
def summarize(registry: Dict[str, Any]) -> str:
    factors = registry.get("factors", [])
    by_layer: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    by_domain: Dict[str, int] = {}
    for f in factors:
        by_layer[f["layer"]] = by_layer.get(f["layer"], 0) + 1
        by_status[f["status"]] = by_status.get(f["status"], 0) + 1
        by_domain[f["domain"]] = by_domain.get(f["domain"], 0) + 1
    lines = []
    lines.append("=== 因子注册表治理摘要 (%s) ===" % registry.get("schema_version"))
    lines.append("因子总数: %d" % len(factors))
    lines.append("按层: " + ", ".join("%s=%d" % (k, v) for k, v in sorted(by_layer.items())))
    lines.append("按状态: " + ", ".join("%s=%d" % (k, v) for k, v in sorted(by_status.items())))
    lines.append("域数: %d -> " % len(by_domain)
                 + ", ".join("%s(%d)" % (k, v) for k, v in sorted(by_domain.items())))
    placeholders = [f for f in factors if f["status"] == PLACEHOLDER]
    lines.append("")
    lines.append("待标定占位因子 (%d) —— 先实现后标定:" % len(placeholders))
    for f in placeholders:
        lines.append("  - %-24s [%s] needs: %s" % (
            f["id"], f["domain"], f.get("calibration_needs")))
    offline = [f for f in factors if f["status"] == OFFLINE]
    for f in offline:
        lines.append("  - %-24s [%s/OFFLINE] needs: %s" % (
            f["id"], f["domain"], f.get("calibration_needs")))
    return "\n".join(lines)


def self_check(registry: Dict[str, Any]) -> List[str]:
    """收束干净·不变量自检：每因子有 id/层/域/状态/输出包；状态合法；id 唯一。"""
    errs: List[str] = []
    seen = set()
    for f in registry.get("factors", []):
        for key in ("id", "name", "layer", "domain", "status", "output_package"):
            if not f.get(key):
                errs.append("缺字段 %s: %s" % (key, f.get("id")))
        if f.get("status") not in _VALID_STATUS:
            errs.append("非法状态: %s" % f.get("id"))
        if f.get("id") in seen:
            errs.append("重复 id: %s" % f.get("id"))
        seen.add(f.get("id"))
    return errs


if __name__ == "__main__":
    reg = load_registry()
    print(summarize(reg))
    print()
    errs = self_check(reg)
    print("自检: " + ("PASS (0 错误)" if not errs else "FAIL -> " + "; ".join(errs)))
    # 演示域收束 + 占位安全默认
    pkg = run_domain("portfolio_risk",
                     {"X10_portfolio_budget": example_portfolio_budget},
                     "PortfolioRiskBudgetPackage")
    print()
    print("示例域收束 portfolio_risk: live=%s placeholder=%s reasons=%s" % (
        pkg["live_factor_ids"], pkg["placeholder_factor_ids"], pkg["reason_codes"]))
