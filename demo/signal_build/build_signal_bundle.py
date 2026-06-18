# -*- coding: utf-8 -*-
"""合成信号 FMZ 单文件 = 真实信号基线 v0.5.4（不改因子逻辑）+ SignalEvidence 导出桥。

信号层「近零改动」：基线 10 因子完整，仅在尾部追加导出桥 export_signal_evidence_package
（allow-list 六子域，不漏执行字段）。剥除 bridge 的 __future__ 行（不能在文件中段）。
保留 CRLF+BOM（FMZ 单文件粘贴口径，见信号层 HANDOFF 经验）。

用法：python build_signal_bundle.py [--check]
"""
import os
import sys
import py_compile

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = r"C:\Users\Xu\Documents\中性回路 - opus4.8\neutral_regulation_demo_fmz.py"
BRIDGE = os.path.join(HERE, "signal_bridge.py")
OUT = os.path.join(HERE, "nrd_signal_fmz.py")


def build():
    with open(BASE, "r", encoding="utf-8-sig") as fh:
        base = fh.read()
    with open(BRIDGE, "r", encoding="utf-8") as fh:
        bridge_lines = [ln for ln in fh.read().splitlines()
                        if not ln.lstrip().startswith("from __future__ import")]
    sep = ("\n\n# ===================== 整合层：SignalEvidence 导出桥（R6，only addition）"
           " =====================\n")
    out = base.rstrip() + "\n" + sep + "\n".join(bridge_lines) + "\n"
    with open(OUT, "w", encoding="utf-8-sig", newline="\r\n") as fh:
        fh.write(out)
    return out


if __name__ == "__main__":
    s = build()
    print("[build] 已生成", OUT, "(%d 行)" % (s.count(chr(10)) + 1))
    if "--check" in sys.argv:
        py_compile.compile(OUT, doraise=True)
        print("[check] 语法编译通过")
        assert "export_signal_evidence_package" in s, "缺导出桥"
        assert "SignalEvidencePackage" in s, "缺契约名"
        for bad in ("ApprovalIntent", "evaluate_portfolio_budget", "plan_assemble"):
            assert bad not in s, "信号 FMZ 污染了执行字段：%s" % bad
        print("[check] 导出桥就位；信号层未被执行字段污染")
