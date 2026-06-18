# -*- coding: utf-8 -*-
"""
合成构建：把 src/ 的模块按依赖顺序内联拼接为单一 FMZ Python 文件。

- 剥离项目内 import（FMZ 运行时与同命名空间提供这些名字）；保留标准库 import。
- 不调用 main()（FMZ 平台自动调用 def main()）。
- --check：编译 + 注入 FMZ 全局后 exec，做名称解析 smoke 检查。

用法：
    python build_bundle.py            # 生成 spm_calendar_protected_short_v1.py
    python build_bundle.py --check    # 生成并做 smoke 检查
"""
import os
import re
import sys
import py_compile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
OUT = os.path.join(HERE, "spm_calendar_protected_short_v1.py")

# 拼接顺序（全为函数/常量定义，运行时才互相调用，故顺序仅为可读性）
MODULE_ORDER = ["config", "gates", "cmd_router", "signal_receiver", "recommend", "position",
                "authorization", "session_core", "deribit_io", "binance_io", "leg_selection",
                "accounting", "plans", "display", "spm_sim", "hedge", "execution", "ledger",
                "hedge_risk", "vrp_gate", "risk_controls", "hedge_watch", "strategy"]

# 需剥离 import 的项目模块（fmz_shim 由 FMZ 运行时提供）
PROJECT_MODULES = set(MODULE_ORDER) | {"fmz_shim"}

_IMPORT_FROM = re.compile(r"^\s*from\s+([A-Za-z_][\w]*)\s+import\b")
_IMPORT_PLAIN = re.compile(r"^\s*import\s+([A-Za-z_][\w]*)")


def _strip_project_imports(lines):
    out = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        # __future__ 导入必须在文件首；各模块的剥掉，bundle 头部统一注入一行
        if line.lstrip().startswith("from __future__ import"):
            i += 1
            continue
        m = _IMPORT_FROM.match(line) or _IMPORT_PLAIN.match(line)
        if m and m.group(1) in PROJECT_MODULES:
            # 处理跨行括号 import：from X import ( ... )
            if "(" in line and ")" not in line:
                i += 1
                while i < n and ")" not in lines[i]:
                    i += 1
                i += 1  # 跳过含 ) 的那行
            else:
                i += 1
            continue
        out.append(line)
        i += 1
    return out


def _read_version():
    try:
        sys.path.insert(0, SRC)
        import config
        return getattr(config, "STRATEGY_VERSION", "?")
    except Exception:
        return "?"


def build():
    parts = ["# -*- coding: utf-8 -*-",
             "# === 自动合成产物：请勿手改，改 src/ 后重新 build_bundle.py ===",
             "# Deribit S:PM 垂直信用价差卖方执行链 v%s（FMZ 单文件；单一 run_cycle 主链 + 交互控制台 + 对冲生命周期）" % _read_version(),
             ""]
    for mod in MODULE_ORDER:
        path = os.path.join(SRC, mod + ".py")
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        kept = _strip_project_imports(lines)
        parts.append("\n# ===================== module: %s =====================" % mod)
        parts.extend(kept)
    src = "\n".join(parts) + "\n"
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(src)
    return src


def check(src):
    # 1) 语法编译
    py_compile.compile(OUT, doraise=True)
    print("[check] 语法编译通过")
    # 2) 注入 FMZ 全局后 exec，验证名称解析（函数体不执行，仅定义）
    sys.path.insert(0, SRC)
    import fmz_shim
    ns = {"__name__": "__bundle__"}
    for name in fmz_shim.__all__:
        ns[name] = getattr(fmz_shim, name)
    exec(compile(src, OUT, "exec"), ns)
    assert "main" in ns and callable(ns["main"]), "bundle 缺少可调用 main()"
    for fn in ("validate_config", "exec_open_structure", "spm_simulate_structure",
               "legsel_short_enriched", "acct_build_report", "ledger_reconcile",
               "dbt_simulate_portfolio", "disp_status_panel", "disp_menu_table",
               "plan_assemble", "plan_rank", "legsel_expiries_in_band", "main",
               # 整合层（R2-R5）：会话/VRP门/缺口域/对冲，须全部在单文件命名空间内
               "ExecutionSession", "PrecommitChecks", "assess_window", "assess_candidate",
               "gate_plan", "apply_vrp_gate", "black_scholes_price_usd",
               "evaluate_portfolio_budget", "decide_position_manage", "build_attribution",
               "watch_position", "build_entry_risk_anchor", "evaluate_position_risk",
               "integrated_plan_preview",
               # v3 主链 / 交互 / 退出 / 对冲（须全部在单文件命名空间内解析）
               "run_cycle", "manage_cycle", "route_command", "receive_signal",
               "build_recommendation_library", "resolve_confirm_code", "evaluate_precommit_checks",
               "build_vertical_entry_snapshot", "evaluate_projected_budget",
               "unified_action_arbiter", "evaluate_startup_recovery", "authorize_from_code",
               "exec_exit_buyback_step", "exit_campaign_decision", "hedge_target_contracts",
               "hedge_order_action", "disp_console_table", "gate_decision"):
        assert fn in ns, "bundle 缺少 %s" % fn
    # 未残留 KPF
    for bad in ("plan_kpf_score", "_kpf_buffer_adverse", "entry_kpf_buffer_state",
                "KPF_CONTESTED_CORE"):
        assert bad not in src, "bundle 残留 KPF：%s" % bad
    # 未残留日历运行路径（v3 垂直唯一）
    for bad in ("MODE_CALENDAR", "ENABLE_CALENDAR", "PROTECTION_RESIDUAL_RECOVERY",
                "CALENDAR_PROTECTED_SHORT"):
        assert bad not in src, "bundle 残留日历：%s" % bad
    print("[check] 名称解析 smoke 通过；main() + v3 主链/交互/退出/对冲齐全；无 KPF / 无日历残留")


if __name__ == "__main__":
    s = build()
    print("[build] 已生成", OUT, "(%d 行)" % (s.count(chr(10)) + 1))
    if "--check" in sys.argv:
        check(s)
