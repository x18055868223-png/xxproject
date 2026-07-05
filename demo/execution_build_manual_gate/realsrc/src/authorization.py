# -*- coding: utf-8 -*-
"""软授权（持仓退出 / 风险退出）合同 + 短授权码（auth_*）。纯函数，便于单测。

设计稿 §6 + 补充意见 P1：软授权与 `position_id` 绑定，是与主循环**并行的非阻塞授权标志**，
不阻塞主循环；默认持续到 用户撤销 / 持仓结束 / 账本身份变化。
风险退出授权独立于普通止盈授权，并补全完整语义：
  max_exit_spend / allowed_order_types / valid_until / revoke / consume。
"""
import base64
import hashlib

ST_UNAUTHORIZED = "UNAUTHORIZED"
ST_AUTHORIZED = "AUTHORIZED"
ST_REVOKED = "REVOKED"
ST_CONSUMED = "CONSUMED"

POLICY_TAKE_PROFIT = "BOUNDED_EXIT_V1"
POLICY_RISK_EXIT = "RISK_EXIT_V1"


def _h(*parts):
    s = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def auth_code(position_id, policy_code, length=4):
    """持仓授权短码：标识 (position_id, policy)。Base32 前 length 位。"""
    raw = base64.b32encode(bytes.fromhex(_h(position_id, policy_code))).decode("ascii").rstrip("=")
    return raw[:length]


def build_authorization(position_id, policy_code, now_ts, operator_note="",
                        max_exit_spend=None, allowed_order_types=None, valid_until=None):
    """构建一份持仓退出授权（初始 AUTHORIZED）。risk-exit 传 max_exit_spend 等完整语义。"""
    return {
        "schema_name": "PositionExitAuthorization",
        "position_id": position_id,
        "policy_code": policy_code,
        "authorization_state": ST_AUTHORIZED,
        "authorized_ts": now_ts,
        "revoked_ts": None,
        "consumed_ts": None,
        "authorization_hash": _h(position_id, policy_code, now_ts)[:16],
        "auth_code": auth_code(position_id, policy_code),
        "operator_note": operator_note,
        # P1：风险退出授权完整语义（普通止盈授权这些为默认 / None）
        "max_exit_spend": max_exit_spend,
        "allowed_order_types": list(allowed_order_types or ["post_only"]),
        "valid_until": valid_until,
    }


def is_authorized(auth, position_id, now_ts=None):
    """授权对当前 position_id 是否有效（AUTHORIZED + 绑定一致 + 未过期）。非阻塞只读。"""
    if not auth or auth.get("authorization_state") != ST_AUTHORIZED:
        return False
    if auth.get("position_id") != position_id:
        return False                      # 持仓身份变化 → 授权失效
    vu = auth.get("valid_until")
    if vu is not None and now_ts is not None and now_ts >= vu:
        return False
    return True


def revoke(auth, now_ts):
    if not auth:
        return auth
    a = dict(auth)
    a["authorization_state"] = ST_REVOKED
    a["revoked_ts"] = now_ts
    return a


def consume(auth, now_ts):
    """退出活动完成后标记 CONSUMED（一次性消费）。"""
    if not auth:
        return auth
    a = dict(auth)
    a["authorization_state"] = ST_CONSUMED
    a["consumed_ts"] = now_ts
    return a


def authorize_from_code(code, position_id, policy_code, now_ts, **kw):
    """校验用户输入的授权码是否匹配当前 position+policy；匹配 → 构建授权，否则 None。"""
    if not code or not position_id:
        return None
    if str(code).strip().upper() != auth_code(position_id, policy_code).upper():
        return None
    return build_authorization(position_id, policy_code, now_ts, **kw)
