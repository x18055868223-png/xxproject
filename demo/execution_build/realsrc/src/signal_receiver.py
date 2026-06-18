# -*- coding: utf-8 -*-
"""信号→执行接收链（sig_*）：执行侧从同托管共享源读取信号层导出的 SignalEvidencePackage，
校验 schema / 版本 / TTL / reject_state / data_quality，给出「是否允许新开仓」的裁决。

补充意见 P0-1：当前执行层仅用静态手填 SIGNAL_STATE，缺 receiver / 校验 / 去重。本模块补执行侧：
  - 总线不可用 / 包过期 / 校验失败 → block_new_opens=True（**禁新开仓**），
    但裁决**只**用于进场门，**不**影响已有持仓的对账 / 退出 / 对冲 / 恢复。
  - package_id 血缘记账（_G），供「本轮持仓由哪个信号包触发」审计。
  - OFFLINE_MANUAL：降级为静态 SIGNAL_STATE / DIRECTION_BIAS（面板须红标），不依赖总线。

依赖（follow-up，先做可行性 spike）：信号侧调 `signal_bridge.export_signal_evidence_package`
落盘 + 原子 rename 传输 + 同托管 loopback 验证。包结构见 `demo/signal_build/signal_bridge.py`。
"""
import json

from fmz_shim import _G

EXPECTED_SCHEMA = "SignalEvidencePackage"
EXPECTED_VERSION_PREFIX = "nrd.integration.signal."

_REJECT_STATES = frozenset({"REJECT", "REJECTED", "BLOCK", "BLOCKED"})
_BAD_QUALITY_STATES = frozenset({"BAD", "MISSING", "STALE", "DEGRADED", "INSUFFICIENT"})

_SIG_LINEAGE_KEY = "spm_signal_lineage_v1"
_SIG_LINEAGE_MAX = 50


def _verdict(availability, tradeable, reasons, package_id=None,
             side_hint=None, expiry_hours=None):
    return {
        "schema_name": "SignalReceiveVerdict",
        # OK / MISSING / STALE / REJECTED / BAD_QUALITY / SCHEMA_MISMATCH / NO_SIDE / OFFLINE_MANUAL
        "availability": availability,
        "tradeable": (None if tradeable is None else bool(tradeable)),
        "block_new_opens": (False if tradeable is None else (not tradeable)),
        "package_id": package_id,
        "side_hint": side_hint,
        "expiry_hours": expiry_hours,
        "reasons": list(reasons or []),
    }


def _is_rejected(reject_state):
    if not isinstance(reject_state, dict) or not reject_state:
        return False
    if reject_state.get("rejected") is True or reject_state.get("blocked") is True:
        return True
    return str(reject_state.get("state") or "").upper() in _REJECT_STATES


def _data_quality_bad(dq):
    if not isinstance(dq, dict) or not dq:
        return False        # 无显式问题 → 视为可用（不过度阻断）
    if dq.get("ok") is False or dq.get("degraded") is True:
        return True
    return str(dq.get("state") or "").upper() in _BAD_QUALITY_STATES


def validate_signal_package(package, now_ts, version_prefix=EXPECTED_VERSION_PREFIX):
    """纯函数：对一个 SignalEvidencePackage 给出接收裁决。"""
    if not isinstance(package, dict):
        return _verdict("MISSING", False, ["SIGNAL_PACKAGE_MISSING"])
    if package.get("schema_name") != EXPECTED_SCHEMA:
        return _verdict("SCHEMA_MISMATCH", False, ["SCHEMA_NAME_MISMATCH"],
                        package.get("package_id"))
    if not str(package.get("schema_version") or "").startswith(version_prefix):
        return _verdict("SCHEMA_MISMATCH", False, ["SCHEMA_VERSION_MISMATCH"],
                        package.get("package_id"))
    pkg_id = package.get("package_id")
    if not pkg_id:
        return _verdict("MISSING", False, ["PACKAGE_ID_MISSING"])
    exp = package.get("expires_ts")
    if not isinstance(exp, (int, float)) or now_ts >= exp:
        return _verdict("STALE", False, ["SIGNAL_PACKAGE_EXPIRED"], pkg_id)
    if _is_rejected(package.get("reject_state")):
        return _verdict("REJECTED", False, ["SIGNAL_REJECTED"], pkg_id)
    if _data_quality_bad(package.get("data_quality")):
        return _verdict("BAD_QUALITY", False, ["SIGNAL_DATA_QUALITY_BAD"], pkg_id)
    rec = package.get("strategy_recommendation") or {}
    side_hint = rec.get("side_hint")
    expiry_hours = rec.get("expiry_hours")
    if not side_hint or str(side_hint).lower() in ("none", "neutral"):
        return _verdict("NO_SIDE", False, ["SIGNAL_NO_EXECUTABLE_SIDE"],
                        pkg_id, side_hint, expiry_hours)
    return _verdict("OK", True, [], pkg_id, side_hint, expiry_hours)


# ---------- 传输源加载 ----------

def load_package_from_file(path):
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def load_package_from_g(key):
    if not key:
        return None
    return _G(key)


def receive_signal(now_ts, source, file_path=None, g_key=None,
                   version_prefix=EXPECTED_VERSION_PREFIX):
    """按配置源接收并裁决。source ∈ OFFLINE_MANUAL / FILE / G。
    OFFLINE_MANUAL → tradeable=None（由调用方据静态 SIGNAL_STATE 决定，面板须红标）。
    总线不可用（读不到包）→ availability=MISSING, block_new_opens=True。"""
    src = str(source or "OFFLINE_MANUAL").upper()
    if src == "OFFLINE_MANUAL":
        return _verdict("OFFLINE_MANUAL", None, ["SIGNAL_OFFLINE_MANUAL_OVERRIDE"])
    if src == "FILE":
        pkg = load_package_from_file(file_path)
    elif src == "G":
        pkg = load_package_from_g(g_key)
    else:
        return _verdict("MISSING", False, ["SIGNAL_SOURCE_UNKNOWN:" + src])
    if pkg is None:
        return _verdict("MISSING", False, ["SIGNAL_BUS_UNAVAILABLE"])
    return validate_signal_package(pkg, now_ts, version_prefix)


# ---------- package_id 血缘记账（_G）----------

def signal_lineage_load():
    return list(_G(_SIG_LINEAGE_KEY) or [])


def signal_lineage_record(package_id, now_ts, note=""):
    recs = signal_lineage_load()
    recs.append({"package_id": package_id, "ts": now_ts, "note": note})
    trimmed = recs[-_SIG_LINEAGE_MAX:]
    _G(_SIG_LINEAGE_KEY, trimmed)
    return trimmed


def signal_lineage_last():
    recs = signal_lineage_load()
    return recs[-1] if recs else None
