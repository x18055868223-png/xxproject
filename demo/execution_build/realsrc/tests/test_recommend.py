# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import recommend as R


def _cand(relief=0.83, sstrike=78000, pstrike=80000, vrp="PASS", budget="ALLOW", cid=1234):
    return {
        "id": cid, "short_instrument": "BTC-31MAY26-%d-C" % sstrike,
        "protection_instrument": "BTC-31MAY26-%d-C" % pstrike,
        "short_expiry_label": "31MAY26", "short_strike": sstrike,
        "protection_strike": pstrike, "amount": 0.1, "qualified": True,
        "net_credit_effective": 0.0004, "margin_relief_ratio": relief,
        "short_delta": 0.30, "width": pstrike - sstrike,
        "vrp_state": vrp, "budget_decision": budget,
    }


def test_strategy_code_and_side():
    assert R.side_of("BTC-31MAY26-78000-C") == "CALL"
    assert R.side_of("BTC-31MAY26-70000-P") == "PUT"
    assert R.strategy_code("CALL", "31MAY26", 78000, 80000) == "VCS|CALL|31MAY26|78000|80000"


def test_build_library_unique_confirm_codes():
    menu = [_cand(sstrike=78000, pstrike=80000, cid=1),
            _cand(sstrike=77000, pstrike=79000, cid=2),
            _cand(sstrike=76000, pstrike=78000, cid=3)]
    lib = R.build_recommendation_library(menu, "s1", "pkg1", 1, 1000)
    codes = [s["confirm_code"] for s in lib["recommendations"]]
    assert len(codes) == 3 and len(set(codes)) == 3      # 唯一
    assert all(len(c) >= 4 for c in codes)


def test_resolve_confirm_code():
    lib = R.build_recommendation_library([_cand()], "s1", "pkg1", 1, 1000)
    code = lib["recommendations"][0]["confirm_code"]
    assert R.resolve_confirm_code(lib, code)["plan_id"] == 1234
    assert R.resolve_confirm_code(lib, code.lower()) is not None   # 大小写不敏感
    assert R.resolve_confirm_code(lib, "ZZZZ") is None
    assert R.resolve_confirm_code(lib, "") is None


def test_confirm_code_stable_under_subbucket_drift_but_changes_on_material_drift():
    # 子桶波动 0.83→0.85（同 0.8 桶）→ 质量码/确认码不变（用户输入期间稳定）
    lib1 = R.build_recommendation_library([_cand(relief=0.83)], "s1", "pkg1", 1, 1000)
    code1 = lib1["recommendations"][0]["confirm_code"]
    lib_same = R.build_recommendation_library([_cand(relief=0.85)], "s1", "pkg1", 2, 1100)
    assert lib_same["recommendations"][0]["confirm_code"] == code1   # 刷新+子桶漂移 → 码不变
    # 跨桶 0.83→0.72（0.8→0.7 桶）→ 材料级质量变 → 确认码变 → 旧码在新库过期
    lib2 = R.build_recommendation_library([_cand(relief=0.72)], "s1", "pkg1", 3, 1200)
    code2 = lib2["recommendations"][0]["confirm_code"]
    assert code2 != code1
    assert R.resolve_confirm_code(lib2, code1) is None              # 旧码 stale
    assert R.resolve_confirm_code(lib2, code2) is not None


def test_precommit_recheck_pass_and_fail():
    lib = R.build_recommendation_library([_cand()], "s1", "pkg1", 1, 1000)
    snap = lib["recommendations"][0]
    # 通过：同库 + 同 plan_hash + live 全过
    ok = R.precommit_recheck(snap, lib, {"entry_net_credit_positive": True, "no_unknown_orders": True})
    assert ok["passed"]
    # live 门失败
    bad = R.precommit_recheck(snap, lib, {"entry_net_credit_positive": False})
    assert not bad["passed"] and any("LIVE_CHECK_FAILED" in r for r in bad["reasons"])
    # 质量跨桶漂移 → plan_hash 变 → 复核拒绝
    lib2 = R.build_recommendation_library([_cand(relief=0.72)], "s1", "pkg1", 2, 1100)
    drift = R.precommit_recheck(snap, lib2, {"entry_net_credit_positive": True})
    assert not drift["passed"]


def test_signal_package_change_changes_quality_code():
    lib1 = R.build_recommendation_library([_cand()], "s1", "pkgA", 1, 1000)
    lib2 = R.build_recommendation_library([_cand()], "s1", "pkgB", 1, 1000)
    assert lib1["recommendations"][0]["quality_code"] != lib2["recommendations"][0]["quality_code"]
