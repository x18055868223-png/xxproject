"""Track 2 player AI review: initial hand responder, 4-category triggers, debounce scheduler, and incremental hold-review protocol."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import re
import socket
from typing import Any, Mapping, Sequence
import urllib.error

try:
    from . import astra_player_dossier_v1 as dossier
    from . import signal_llm_review
except ImportError:
    import astra_player_dossier_v1 as dossier
    import signal_llm_review

DEFAULT_DEBOUNCE_MS = 12 * 60_000
MIN_DEBOUNCE_MS = 10 * 60_000
MAX_DEBOUNCE_MS = 15 * 60_000
DEFAULT_COOLDOWN_MS = 30 * 60_000
RETRY_BACKOFF_MS = 5 * 60_000
FRESH_SAMPLE_GAP_MS = 10 * 60_000
STALE_INFLIGHT_MS = 25 * 60_000
HOLD_VERDICTS = ("HOLD", "REVOKE", "UNKNOWN")
DEFAULT_CHAT_ENDPOINT = signal_llm_review.OPENAI_CHAT_COMPLETIONS_ENDPOINT

_GENUINE_ARD_EVENT_KINDS = frozenset({
    "leave",
    "return",
    "inside_band",
    "enter_band",
    "exit_band",
    "cross",
    "cross_band",
    "cross_pin",
    "cross_short",
})
_COSMETIC_ARD_EVENT_KINDS = frozenset({
    "steady",
    "stay",
    "inside",
    "outside",
    "unchanged",
    "cosmetic",
    "repeat",
    "repeated",
    "heartbeat",
    "refresh",
})
_RESELECTION_RE = re.compile(
    r"(?:Accept\s*[/／]\s*(?:Put|Call)|(?:改选|切换为|重新选择|改为承接|switch\s+to|re-?select)\s*(?:Put|Call))",
    re.IGNORECASE,
)
_RESELECTION_PROHIBITION_RE = re.compile(
    r"(?:严禁|禁止|不得|不可|不能|切勿|不予|不要|无需|不必|不会|并未|并非|未|维持|保持|继续|不)[^，,;；。\n]{0,24}(?:Accept\s*[/／]\s*(?:Put|Call)|(?:改选|切换为|重新选择|改为承接|switch\s+to|re-?select)(?:\s*(?:Accept\s*[/／]\s*)?(?:Put|Call))?)",
    re.IGNORECASE,
)
_UNTRIGGERED_CLAIM_RE = re.compile(
    r"(?:条件未触发|规则未触发|边界规则未触发|并未触发|没有触发|not_triggered)",
    re.IGNORECASE,
)
_UNTRIGGERED_NEGATION_RE = re.compile(
    r"(?:并非|不是|不能|不得|绝非|不可|不可标为|不认为|并非指|此前|入场时|锁定前|原本|当时)[^，,;；。\n]{0,12}(?:条件未触发|规则未触发|边界规则未触发|并未触发|没有触发|not_triggered)",
    re.IGNORECASE,
)


def _has_forbidden_reselection(public_text: str, public_reply: str) -> bool:
    for text in (public_reply, public_text):
        for seg in re.split(r"[\r\n。；;，,]+", text):
            cleaned = _RESELECTION_PROHIBITION_RE.sub("", seg)
            if _RESELECTION_RE.search(cleaned):
                return True
    return False


def _has_untriggered_claim(public_text: str, public_reply: str) -> bool:
    for text in (public_reply, public_text):
        for seg in re.split(r"[\r\n。；;，,]+", text):
            cleaned = _UNTRIGGERED_NEGATION_RE.sub("", seg)
            if _UNTRIGGERED_CLAIM_RE.search(cleaned):
                return True
    return False


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _utc(ms: Any) -> str:
    if not isinstance(ms, int) or isinstance(ms, bool):
        return "未知时间"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _normalize_endpoint(endpoint: str | None) -> str:
    base = (endpoint or DEFAULT_CHAT_ENDPOINT).strip().rstrip("/")
    if not base:
        base = DEFAULT_CHAT_ENDPOINT.rstrip("/")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def _is_transient_transport_error(exc: Exception) -> bool:
    if isinstance(exc, signal_llm_review.DailyBudgetExceeded):
        return False
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in signal_llm_review.RETRYABLE_LLM_HTTP_CODES
    if isinstance(exc, urllib.error.URLError):
        return True
    if isinstance(exc, signal_llm_review.LlmApiError):
        return exc.status_code in signal_llm_review.RETRYABLE_LLM_HTTP_CODES
    return isinstance(exc, OSError)


def _budget_error_category(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "TIMEOUT"
    if isinstance(exc, urllib.error.URLError) and isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout)):
        return "TIMEOUT"
    return "HTTP_ERROR"


def _invoke_transport(poster: Any, api_key: str, model: str, request_body: dict[str, Any], timeout_s: float, url: str) -> Any:
    try:
        return poster(api_key, model, request_body, timeout_s, endpoint=url)
    except TypeError as exc:
        if "unexpected keyword" not in str(exc):
            raise
        return poster(api_key, model, request_body, timeout_s)


def call_chat_llm(
    messages: Sequence[Mapping[str, Any]],
    *,
    api_key: str,
    model: str,
    endpoint: str | None = None,
    timeout_s: float = 180,
    retries: int = 1,
    max_tokens: int = 24576,
    thinking: bool = True,
    reasoning_effort: str | None = None,
    transport: Any = None,
    budget: Any = None,
    budget_role: str = "astra_player",
    packet_hash: str | None = None,
) -> dict[str, Any]:
    """Call OpenAI-compatible chat completions with endpoint normalization and transient retries."""
    if not api_key and transport is None:
        raise RuntimeError("LLM_API_KEY is required")
    url = _normalize_endpoint(endpoint)
    request_body: dict[str, Any] = {
        "model": model,
        "messages": [dict(m) for m in messages],
        "stream": False,
        "max_tokens": int(max_tokens),
        "thinking": {"type": "enabled" if thinking else "disabled"},
    }
    if reasoning_effort and str(reasoning_effort).strip():
        request_body["reasoning_effort"] = str(reasoning_effort).strip()
    poster = transport or signal_llm_review._post_chat_completion
    total_attempts = 1 + max(0, int(retries))
    response: Any = None
    attempts_used = 0
    for attempt in range(total_attempts):
        attempts_used = attempt + 1
        res_id: str | None = None
        if budget is not None:
            res = budget.reserve(
                count=1,
                role=budget_role,
                packet_hash=packet_hash,
                provider="deepseek",
                model=model,
            )
            res_id = res.get("reservation_id") if isinstance(res, Mapping) else None
        try:
            response = _invoke_transport(poster, api_key, model, request_body, timeout_s, url)
        except Exception as exc:
            if budget is not None and not isinstance(exc, signal_llm_review.DailyBudgetExceeded):
                budget.complete(res_id, _budget_error_category(exc), usage={})
            if (
                isinstance(exc, signal_llm_review.DailyBudgetExceeded)
                or attempt + 1 >= total_attempts
                or not _is_transient_transport_error(exc)
            ):
                raise
        else:
            if budget is not None:
                if isinstance(response, Mapping):
                    usage = signal_llm_review._response_usage(dict(response))
                    budget.complete(res_id, "HTTP_OK", usage=usage)
                else:
                    budget.complete(res_id, "HTTP_ERROR", usage={})
            break
    if not isinstance(response, Mapping):
        raise ValueError("LLM response must be a JSON object")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("LLM response missing choices")
    choice = choices[0] if isinstance(choices[0], Mapping) else {}
    finish_reason = choice.get("finish_reason")
    message = choice.get("message") if isinstance(choice.get("message"), Mapping) else {}
    raw_content = message.get("content")
    if isinstance(raw_content, list):
        text = "".join(
            str(part.get("text") or "")
            for part in raw_content
            if isinstance(part, Mapping)
        ).strip()
    else:
        text = str(raw_content or "").strip()
    if not text:
        if finish_reason == "length":
            raise ValueError("LLM response content is empty (finish_reason=length)")
        raise ValueError("LLM response content is empty")
    http_status = response.get("_http_status") or response.get("http_status") or 200
    reasoning_text = message.get("reasoning_content")
    return {
        "text": text,
        "reasoning_content": str(reasoning_text) if reasoning_text else None,
        "usage": signal_llm_review._response_usage(dict(response)),
        "finish_reason": finish_reason,
        "http_status": int(http_status),
        "model": str(response.get("model") or model),
        "reasoning_effort": reasoning_effort,
        "endpoint": url,
        "attempts": attempts_used,
    }


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    match = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", stripped, re.IGNORECASE)
    return match.group(1).strip() if match else stripped


def _split_tagged_json(text: str, tag_name: str) -> tuple[str, dict[str, Any]]:
    pattern = re.compile(rf"<{tag_name}>\s*([\s\S]*?)\s*</{tag_name}>", re.IGNORECASE)
    matches = list(pattern.finditer(text))
    if matches:
        match = matches[-1]
        public_text = text[:match.start()].rstrip()
        payload_text = _strip_code_fence(match.group(1))
    else:
        fence_matches = list(re.finditer(r"```json\s*([\s\S]*?)\s*```", text, re.IGNORECASE))
        if not fence_matches:
            raise ValueError(f"missing {tag_name} or trailing json block")
        fence_match = fence_matches[-1]
        public_text = text[:fence_match.start()].rstrip()
        payload_text = fence_match.group(1).strip()
    parsed = json.loads(payload_text)
    if not isinstance(parsed, dict):
        raise ValueError("structured JSON block must be an object")
    return public_text, parsed


def _format_contract_pair(contract_pair: Any) -> str:
    if isinstance(contract_pair, Mapping):
        items = [(side, contract_pair[side]) for side in ("put", "call") if side in contract_pair]
        if not items:
            items = list(contract_pair.items())
    elif isinstance(contract_pair, Sequence) and not isinstance(contract_pair, (str, bytes)):
        items = [(str(item.get("side") or f"leg_{i}"), item) for i, item in enumerate(contract_pair) if isinstance(item, Mapping)]
    else:
        items = []
    lines = []
    for side, c in items:
        if not isinstance(c, Mapping):
            continue
        lines.append(
            f"- {str(side).upper()}: short={c.get('short_instrument')} ({c.get('short_strike_usd')}), "
            f"long={c.get('long_instrument')} ({c.get('long_strike_usd')}), expiry={_utc(c.get('expiry_ms'))}"
        )
    return "\n".join(lines)


def decide_initial_hand(
    input_md_text: str,
    *,
    contract_pair: Any,
    api_key: str,
    model: str,
    endpoint: str | None = None,
    timeout_s: float = 180,
    retries: int | None = None,
    max_tokens: int = 24576,
    reasoning_effort: str | None = None,
    transport: Any = None,
    budget: Any = None,
    budget_role: str = "astra_player_initial",
    packet_hash: str | None = None,
) -> dict[str, Any]:
    """Send frozen input.md to the LLM, parse public Chinese prose + verbatim annotation + condition_plan."""
    system_prompt = (
        "你是承保影子牌手。请完整阅读冻结牌面并用中文作答。\n"
        "【核心术语公理】\n"
        "- 责任区：卖方短腿与保护腿之间的亏损承受区间（触及边界不算入区，越过短腿进入区间才算严格入区）。\n"
        "- 本期尺度：指本期到期隐含波动给出的 `expected_move_usd` 预期波动尺度。\n"
        "- Pin / 墙：仅作当前空间结构位置参照，不带方向性，不构成价格吸附保证。\n"
        "- ARD：仅衡量过去 4 小时围绕同到期已知 Pin 的历史贴近、离返与迁移事实，不做未来方向预测。\n"
        "【作答与规则锁定】\n"
        "1. 在正文开头或明确独立标题行写出唯一结论：`## Accept / Put`、`## Accept / Call`、`## Reject` 或 `## Unknown`。\n"
        "2. 正文必须分别给出 Put 路径判断、Call 路径判断、Put 最强反证、Call 最强反证与两侧对比择优，并冻结历史事实、承担条件与破坏确认口径。\n"
        "3. 系统的机械有效破坏规则仅支持两选一：`\"new_entry\"`（入场后出现严格进入责任区的分钟采样即触发）或 `\"entry_not_returned\"`（入场后严格进入责任区且至固定检查点仍未严格返回责任外才触发）；固定检查钟默认每 5 分钟（`check_interval_ms = 300000`）按 UTC 网格执行。正文书写破坏确认口径时必须直接对齐该两选一规则与 5 分钟网格，严禁自行编造“每 60 分钟整点锚点”等其他检查时钟。\n"
        "4. 为便于程序做逐字子串核验，建议正文中 Put 路径、Call 路径、Put 最强反证、Call 最强反证、两侧对比择优，以及（若 Accept）冻结历史事实、承担条件、破坏确认口径（与 ARD 判读）各自先写一段连贯完整的中文整句段落，尽量不在摘录段落内使用分点列表或反引号。\n"
        "5. 在正文最后附带 `<ANNOTATION_JSON>...</ANNOTATION_JSON>`（必须是合法 JSON 对象）。其中 `put`、`call`、`comparison`、`put_counter`、`call_counter` 必须是**从上方中文正文原封不动逐字复制（verbatim substring）的非空连续片段**——不要删改、浓缩、跨段拼接或替换任何字词、数字、标点或符号；`depends_on_ard` 必须是显式布尔值。\n"
        "6. 若结论为 Accept，`<ANNOTATION_JSON>` 中必须包含 `condition_plan` 对象（不能是字符串），含：`invalidation`（仅限 `\"new_entry\"` 或 `\"entry_not_returned\"`）、`check_interval_ms`（默认 `300000`，整分钟毫秒）、`historical_facts`、`accepted_conditions`、`confirmation_excerpt`（三者均须是**从上方中文正文原封不动逐字复制的连续片段**，任意改写或浓缩都会导致解析失败作废），以及可选 `require_outside_at_t0`（布尔）；若 `depends_on_ard` 为 true，还须提供逐字复制自正文的 `ard_responsibility_meaning` 与 `ard_review_on`。"
    )
    pair_summary = _format_contract_pair(contract_pair)
    user_content = f"候选合约对：\n{pair_summary}\n\n{input_md_text}" if pair_summary else input_md_text
    effective_retries = (0 if float(timeout_s) >= 300 else 1) if retries is None else retries
    llm_result = call_chat_llm(
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}],
        api_key=api_key,
        model=model,
        endpoint=endpoint,
        timeout_s=timeout_s,
        retries=effective_retries,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        transport=transport,
        budget=budget,
        budget_role=budget_role,
        packet_hash=packet_hash,
    )
    public_text, payload = _split_tagged_json(llm_result["text"], "ANNOTATION_JSON")
    if not public_text.strip():
        raise ValueError("empty public decision text")
    raw_bytes = (public_text + "\n").encode("utf-8")

    excerpt_keys = ("put", "call", "comparison", "put_counter", "call_counter")
    annotation = {
        **{k: payload.get(k) for k in excerpt_keys},
        "depends_on_ard": payload.get("depends_on_ard"),
    }
    decision = dossier.parse(raw_bytes, annotation)

    parsed_plan: dict[str, Any] | None = None
    if decision["action"] == "Accept":
        raw_plan = payload.get("condition_plan")
        if not isinstance(raw_plan, Mapping):
            raise ValueError("Accept decision requires condition_plan")
        invalidation = raw_plan.get("invalidation")
        if invalidation not in dossier.condition.RULES:
            raise ValueError("unsupported condition_plan.invalidation")
        interval = raw_plan.get("check_interval_ms", 300_000)
        if isinstance(interval, bool) or not isinstance(interval, int) or not 0 < interval <= dossier.DAY or interval % dossier.MINUTE:
            raise ValueError("condition_plan.check_interval_ms must be positive minute-aligned int")
        require_outside = raw_plan.get("require_outside_at_t0", False)
        if not isinstance(require_outside, bool):
            raise ValueError("condition_plan.require_outside_at_t0 must be boolean")
        required_excerpts = ["historical_facts", "accepted_conditions", "confirmation_excerpt"]
        if decision["depends_on_ard"]:
            required_excerpts.extend(["ard_responsibility_meaning", "ard_review_on"])
        parsed_plan = {
            "invalidation": invalidation,
            "check_interval_ms": interval,
            "require_outside_at_t0": require_outside,
        }
        for key in required_excerpts:
            val = raw_plan.get(key)
            if not dossier.excerpt_in_text(val, public_text):
                raise ValueError(f"condition_plan.{key} must be a verbatim excerpt from public text")
            parsed_plan[key] = val.strip()

    return {
        "raw_bytes": raw_bytes,
        "raw_text": public_text,
        "reasoning_content": llm_result.get("reasoning_content"),
        "annotation": annotation,
        "condition_plan": parsed_plan,
        "decision": decision,
        "usage": llm_result["usage"],
        "model": llm_result["model"],
        "reasoning_effort": llm_result.get("reasoning_effort"),
        "finish_reason": llm_result["finish_reason"],
    }


def _row_metrics(row: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(row, Mapping):
        return {}
    metrics = row.get("metrics")
    return metrics if isinstance(metrics, Mapping) else row


def _row_ts(row: Mapping[str, Any] | None) -> int | None:
    if not isinstance(row, Mapping):
        return None
    metrics = _row_metrics(row)
    for val in (metrics.get("asof_ms"), row.get("asof_ms"), row.get("recorded_at_ms"), metrics.get("recorded_at_ms")):
        if (ts := _int(val)) is not None:
            return ts
    return None


def _trailing_fresh_run(rows: Sequence[tuple[int, Mapping[str, Any]]], *, now_ms: int) -> list[tuple[int, Mapping[str, Any]]]:
    if not rows:
        return []
    latest_ts = rows[-1][0]
    if now_ms - latest_ts >= FRESH_SAMPLE_GAP_MS:
        return []
    run = [rows[-1]]
    for idx in range(len(rows) - 2, -1, -1):
        prev_ts = rows[idx][0]
        if run[-1][0] - prev_ts >= FRESH_SAMPLE_GAP_MS:
            break
        run.append(rows[idx])
    run.reverse()
    return run


def _detect_category_a(
    *,
    entry_at_ms: int,
    short_strike_usd: float,
    t0: Mapping[str, Any],
    all_rows: Sequence[tuple[int, Mapping[str, Any]]],
    fresh_run: Sequence[tuple[int, Mapping[str, Any]]],
    condition_events: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any] | None:
    trig_events = [
        ev for ev in (condition_events or ())
        if isinstance(ev, Mapping) and ev.get("kind") == "CONDITION_TRIGGERED"
    ]
    if trig_events:
        latest_trig = max(
            trig_events,
            key=lambda ev: (_int(ev.get("observed_at_ms")) or _int(ev.get("recorded_at_ms")) or 0, str(ev.get("event_id") or "")),
        )
        obs_ms = _int(latest_trig.get("observed_at_ms")) or _int(latest_trig.get("recorded_at_ms")) or entry_at_ms
        return {
            "category": "A",
            "code": "CONDITION_TRIGGERED",
            "signature": f"CONDITION_TRIGGERED:{obs_ms}",
            "severity": 3,
            "observed_at_ms": obs_ms,
            "facts": {
                "event_id": latest_trig.get("event_id"),
                "result": (latest_trig.get("body") or {}).get("result") if isinstance(latest_trig.get("body"), Mapping) else None,
            },
            "summary_cn": f"程序冻结边界条件已触发（CONDITION_TRIGGERED @ {_utc(obs_ms)}）。",
        }

    if not fresh_run:
        return None
    latest_ts, latest_m = fresh_run[-1]
    if latest_ts <= entry_at_ms:
        return None
    d_now = _num(latest_m.get("short_distance_pct"))
    if d_now is None or d_now >= 0:
        return None
    # Ensure sample continuity from prior sample or entry_at_ms when first post-entry sample.
    prev_ts = fresh_run[-2][0] if len(fresh_run) >= 2 else entry_at_ms
    if latest_ts - prev_ts >= FRESH_SAMPLE_GAP_MS:
        return None
    d0 = _num(t0.get("short_distance_pct"))
    was_outside = (d0 is None or d0 > 0) or any((_num(m.get("short_distance_pct")) or 0) > 0 for ts, m in all_rows if ts < latest_ts)
    if not was_outside:
        return None
    return {
        "category": "A",
        "code": "BOUNDARY_BREACH",
        "signature": f"BOUNDARY_BREACH:{latest_ts}",
        "severity": 3,
        "observed_at_ms": latest_ts,
        "facts": {
            "short_distance_pct": d_now,
            "spot_usd": _num(latest_m.get("spot_usd")),
            "short_strike_usd": short_strike_usd,
        },
        "summary_cn": f"入场后新鲜连续采样越过短腿边界（距短腿 {d_now:+.2f}%）。",
    }


def _detect_category_b(
    *,
    entry_at_ms: int,
    short_strike_usd: float,
    t0: Mapping[str, Any],
    fresh_run: Sequence[tuple[int, Mapping[str, Any]]],
) -> dict[str, Any] | None:
    if len(fresh_run) < 2:
        return None
    latest_ts, latest_m = fresh_run[-1]
    _, prev_m = fresh_run[-2]
    if latest_ts <= entry_at_ms:
        return None
    d0 = _num(t0.get("short_distance_pct"))
    d_prev = _num(prev_m.get("short_distance_pct"))
    d_now = _num(latest_m.get("short_distance_pct"))
    if d0 is None or d0 <= 0 or d_prev is None or d_prev <= 0 or d_now is None or d_now <= 0:
        return None

    rel_half = d_now <= 0.50 * d0
    abs_and_rel = (d0 - d_now) >= max(0.35, 0.40 * d0) and d_now < 0.75 * d0

    em_drop = False
    em0, em_now = _num(t0.get("expected_move_usd")), _num(latest_m.get("expected_move_usd"))
    spot0, spot_now = _num(t0.get("spot_usd")), _num(latest_m.get("spot_usd"))
    r0 = r_now = None
    if em0 and em0 > 0 and em_now and em_now > 0 and spot0 and spot_now:
        r0 = abs(spot0 - short_strike_usd) / em0
        r_now = abs(spot_now - short_strike_usd) / em_now
        if r0 > 0 and r_now <= 0.50 * r0:
            em_drop = True

    if not (rel_half or abs_and_rel or em_drop):
        return None

    ratio = min(1.0, d_now / d0)
    if em_drop and not (rel_half or abs_and_rel) and r0 and r_now is not None:
        ratio = min(1.0, r_now / r0)
    depth_step = max(1, int(math.floor((1.0 - ratio) * 10)))
    return {
        "category": "B",
        "code": "MARGIN_DETERIORATION",
        "signature": f"MARGIN_DETERIORATION:step_{depth_step}",
        "depth_step": depth_step,
        "severity": 2,
        "observed_at_ms": latest_ts,
        "facts": {
            "t0_short_distance_pct": d0,
            "latest_short_distance_pct": d_now,
            "t0_expected_move_ratio": r0,
            "latest_expected_move_ratio": r_now,
        },
        "summary_cn": f"短腿安全余量相对入场实质收缩（由 {d0:.2f}% 缩至 {d_now:.2f}%）。",
    }


def _is_genuine_ard_event(event: Any, *, entry_at_ms: int) -> bool:
    if not isinstance(event, Mapping):
        return False
    if event.get("transition") is False:
        return False
    at_ms = _int(event.get("at_ms"))
    if at_ms is not None and at_ms <= entry_at_ms:
        return False
    kind = str(event.get("kind") or event.get("type") or event.get("event") or "").strip().lower()
    if not kind or kind in _COSMETIC_ARD_EVENT_KINDS:
        return False
    return kind in _GENUINE_ARD_EVENT_KINDS or "cross" in kind or "band" in kind


def _detect_category_c(
    *,
    frozen_condition: Mapping[str, Any],
    side: str,
    short_strike_usd: float,
    entry_at_ms: int,
    ard_comparison: Mapping[str, Any] | None,
    now_ms: int,
) -> dict[str, Any] | None:
    if not isinstance(ard_comparison, Mapping) or not frozen_condition.get("ard_basis"):
        return None
    if ard_comparison.get("same_measure") is not True:
        return None
    if "pin_status" in ard_comparison and ard_comparison.get("pin_status") is not None:
        if ard_comparison.get("pin_status") != "ok":
            return None
    if ard_comparison.get("current_sample_available") is False:
        return None

    ard_facts = ((frozen_condition.get("ard_basis") or {}).get("facts") or {}) if isinstance(frozen_condition.get("ard_basis"), Mapping) else {}
    old_pin = _num(ard_comparison.get("old_pin"))
    if old_pin is None:
        old_pin = _num((ard_comparison.get("frozen_anchor") or {}).get("pin")) if isinstance(ard_comparison.get("frozen_anchor"), Mapping) else None
    if old_pin is None:
        old_pin = _num((ard_facts.get("last") or {}).get("pin")) if isinstance(ard_facts.get("last"), Mapping) else None
    new_pin = _num(ard_comparison.get("new_pin"))
    if new_pin is None:
        new_pin = _num((ard_comparison.get("current_anchor") or {}).get("pin")) if isinstance(ard_comparison.get("current_anchor"), Mapping) else None
    band_half = _num(ard_comparison.get("band_half"))
    if band_half is None:
        band_half = _num((ard_facts.get("band") or {}).get("half_width_usd")) if isinstance(ard_facts.get("band"), Mapping) else None
    band_half = band_half or 0.0

    pin_triggered = False
    if ard_comparison.get("pin_price_changed") is True and old_pin is not None and new_pin is not None and new_pin != old_pin:
        material_shift = abs(new_pin - old_pin) >= max(250.0, 0.4 * band_half)
        old_margin = (old_pin - short_strike_usd) if side == "put" else (short_strike_usd - old_pin)
        new_margin = (new_pin - short_strike_usd) if side == "put" else (short_strike_usd - new_pin)
        closer_or_beyond = (new_margin < old_margin) and (new_margin <= 0 or old_margin > 0)
        pin_triggered = material_shift or closer_or_beyond

    genuine_events = [
        dict(e) for e in (ard_comparison.get("new_neighborhood_events") or ())
        if _is_genuine_ard_event(e, entry_at_ms=entry_at_ms)
    ]
    if not (pin_triggered or genuine_events):
        return None

    obs_ms = (
        _int(ard_comparison.get("check_at_ms"))
        or _int(ard_comparison.get("observed_at_ms"))
        or max((_int(e.get("at_ms")) or 0 for e in genuine_events), default=0)
        or now_ms
    )
    if pin_triggered:
        sig = f"ARD_PIN:{old_pin:g}->{new_pin:g}"
        summary = f"同口径 ARD Pin 发生实质变化（{old_pin:,.0f} -> {new_pin:,.0f} USD）。"
    else:
        last_ev = genuine_events[-1]
        kind = last_ev.get("kind") or last_ev.get("type") or last_ev.get("event")
        sig = f"ARD_EVENT:{kind}:{last_ev.get('at_ms') or obs_ms}"
        summary = f"同口径 ARD 出现邻域穿越/离返事件（{kind}）。"
    return {
        "category": "C",
        "code": "ARD_STRUCTURE_SHIFT",
        "signature": sig,
        "severity": 2,
        "observed_at_ms": obs_ms,
        "facts": {
            "old_pin": old_pin,
            "new_pin": new_pin,
            "band_half": band_half,
            "genuine_events": genuine_events,
        },
        "summary_cn": summary,
    }


def _detect_category_d(
    *,
    side: str,
    short_strike_usd: float,
    entry_at_ms: int,
    t0: Mapping[str, Any],
    all_rows: Sequence[tuple[int, Mapping[str, Any]]],
    fresh_run: Sequence[tuple[int, Mapping[str, Any]]],
    now_ms: int,
) -> dict[str, Any] | None:
    if not fresh_run:
        return None
    latest_ts, latest_m = fresh_run[-1]
    if latest_ts <= entry_at_ms:
        return None
    if latest_m.get("flow_ready") is not True:
        return None

    flow_asof = _int(latest_m.get("flow_asof_ms"))
    t0_flow_asof = _int(t0.get("flow_asof_ms"))
    if flow_asof is not None:
        if flow_asof <= entry_at_ms or now_ms - flow_asof > 60 * 60_000:
            return None
        if t0_flow_asof is not None and flow_asof <= t0_flow_asof:
            return None

    adverse_dirs = {"bearish", "neutral_to_bearish"} if side == "put" else {"bullish", "neutral_to_bullish"}
    latest_dir = str(latest_m.get("flow_direction") or "").strip().lower()
    if latest_dir not in adverse_dirs:
        return None

    t0_dir = str(t0.get("flow_direction") or "").strip().lower()
    reversed_from_t0 = bool(t0_dir) and (t0_dir not in adverse_dirs)
    m4, m12 = _num(latest_m.get("flow_momentum_4h_pct")), _num(latest_m.get("flow_momentum_12h_pct"))
    t0_m4, t0_m12 = _num(t0.get("flow_momentum_4h_pct")), _num(t0.get("flow_momentum_12h_pct"))
    if side == "put":
        mom_shift = (m4 is not None and m4 < 0 and (t0_m4 is None or m4 < t0_m4)) or (
            m12 is not None and m12 < 0 and (t0_m12 is None or m12 < t0_m12)
        )
    else:
        mom_shift = (m4 is not None and m4 > 0 and (t0_m4 is None or m4 > t0_m4)) or (
            m12 is not None and m12 > 0 and (t0_m12 is None or m12 > t0_m12)
        )
    if not (reversed_from_t0 or mom_shift):
        return None

    latest_spot = _num(latest_m.get("spot_usd"))
    latest_d = _num(latest_m.get("short_distance_pct"))
    if latest_spot is None or latest_d is None:
        return None

    refs: list[Mapping[str, Any]] = [t0]
    refs.extend(m for ts, m in all_rows if ts < latest_ts and latest_ts - ts <= 4 * 3_600_000)
    coincident_shrink = 0.0
    matched = False
    for ref in refs:
        ref_spot = _num(ref.get("spot_usd"))
        ref_d = _num(ref.get("short_distance_pct"))
        if ref_spot is None or ref_d is None:
            continue
        adverse_spot = (latest_spot < ref_spot) if side == "put" else (latest_spot > ref_spot)
        shrink = ref_d - latest_d
        if adverse_spot and shrink >= 0.20:
            matched = True
            coincident_shrink = max(coincident_shrink, shrink)
    if not matched:
        return None

    non_overlapping_confirmed = (
        latest_ts - entry_at_ms >= 4 * 3_600_000
        and t0_flow_asof is not None
        and flow_asof is not None
        and flow_asof - t0_flow_asof >= 4 * 3_600_000
        and reversed_from_t0
        and mom_shift
    )
    summary_text = (
        f"相邻不重叠窗口确认主动流转呈{latest_dir}，同步伴随价格向短腿收缩 {coincident_shrink:.2f} 个百分点。"
        if non_overlapping_confirmed
        else f"滚动窗口状态变化（反转未获相邻不重叠窗口确认，仅作复核提醒），现读 {latest_dir} 且价格向短腿收缩 {coincident_shrink:.2f} 个百分点。"
    )

    return {
        "category": "D",
        "code": "FLOW_REVERSAL",
        "signature": f"FLOW_REVERSAL:{latest_dir}:{flow_asof or latest_ts}",
        "severity": 2,
        "observed_at_ms": flow_asof or latest_ts,
        "facts": {
            "flow_direction": latest_dir,
            "t0_flow_direction": t0_dir or None,
            "flow_cvd_4h": _num(latest_m.get("flow_cvd_4h")),
            "flow_momentum_4h_pct": m4,
            "short_distance_shrink_pct_pts": round(coincident_shrink, 4),
            "short_strike_usd": short_strike_usd,
            "reversal_confirmed": bool(non_overlapping_confirmed),
        },
        "summary_cn": summary_text,
    }


def detect_hold_triggers(
    *,
    frozen_condition: Mapping[str, Any],
    side: str,
    short_strike_usd: float,
    entry_at_ms: int,
    t0_metrics: Mapping[str, Any],
    series_rows: Sequence[Mapping[str, Any]],
    condition_events: Sequence[Mapping[str, Any]] | None = None,
    ard_comparison: Mapping[str, Any] | None = None,
    now_ms: int,
) -> list[dict[str, Any]]:
    """Detect deterministic hold-review triggers across Categories A, B, C, and D."""
    norm_side = str(side or "").strip().lower()
    if norm_side not in {"put", "call"}:
        raise ValueError("side must be 'put' or 'call'")
    strike = _num(short_strike_usd)
    if strike is None or strike <= 0:
        raise ValueError("short_strike_usd must be positive")
    t0 = _row_metrics(t0_metrics)
    indexed_rows: list[tuple[int, Mapping[str, Any]]] = []
    for row in series_rows or ():
        ts = _row_ts(row)
        if ts is not None and ts <= now_ms:
            indexed_rows.append((ts, _row_metrics(row)))
    indexed_rows.sort(key=lambda item: item[0])
    fresh_run = _trailing_fresh_run(indexed_rows, now_ms=now_ms)

    triggers: list[dict[str, Any]] = []
    for candidate in (
        _detect_category_a(
            entry_at_ms=entry_at_ms,
            short_strike_usd=strike,
            t0=t0,
            all_rows=indexed_rows,
            fresh_run=fresh_run,
            condition_events=condition_events,
        ),
        _detect_category_b(
            entry_at_ms=entry_at_ms,
            short_strike_usd=strike,
            t0=t0,
            fresh_run=fresh_run,
        ),
        _detect_category_c(
            frozen_condition=frozen_condition,
            side=norm_side,
            short_strike_usd=strike,
            entry_at_ms=entry_at_ms,
            ard_comparison=ard_comparison,
            now_ms=now_ms,
        ),
        _detect_category_d(
            side=norm_side,
            short_strike_usd=strike,
            entry_at_ms=entry_at_ms,
            t0=t0,
            all_rows=indexed_rows,
            fresh_run=fresh_run,
            now_ms=now_ms,
        ),
    ):
        if candidate is not None:
            triggers.append(candidate)
    return triggers


def _trigger_sig_key(trigger: Mapping[str, Any]) -> str:
    return f"{trigger.get('category')}:{trigger.get('signature')}"


def _is_escalated(trigger: Mapping[str, Any], prev: Mapping[str, Any]) -> bool:
    if int(trigger.get("severity") or 0) > int(prev.get("severity") or 0):
        return True
    cur_step = _int(trigger.get("depth_step"))
    prev_step = _int(prev.get("depth_step"))
    return cur_step is not None and prev_step is not None and cur_step > prev_step


def schedule_review_action(
    scheduler_state: Mapping[str, Any] | None,
    triggers: Sequence[Mapping[str, Any]],
    *,
    now_ms: int,
    debounce_ms: int = DEFAULT_DEBOUNCE_MS,
    cooldown_ms: int = DEFAULT_COOLDOWN_MS,
    retry_backoff_ms: int = RETRY_BACKOFF_MS,
    stale_inflight_ms: int = STALE_INFLIGHT_MS,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Pure debounce/coalescing scheduler transition for hold-review AI calls."""
    del cooldown_ms, retry_backoff_ms  # applied when completing/failing a dispatched review
    raw = scheduler_state if isinstance(scheduler_state, Mapping) else {}
    state: dict[str, Any] = {
        "revoked": bool(raw.get("revoked", False)),
        "inflight": bool(raw.get("inflight", False)),
        "inflight_started_at_ms": _int(raw.get("inflight_started_at_ms")),
        "inflight_batch": [deepcopy(dict(t)) for t in (raw.get("inflight_batch") or []) if isinstance(t, Mapping)],
        "pending": [deepcopy(dict(t)) for t in (raw.get("pending") or []) if isinstance(t, Mapping)],
        "pending_since_ms": _int(raw.get("pending_since_ms")),
        "last_trigger_ms": _int(raw.get("last_trigger_ms")),
        "cooldown_until_ms": _int(raw.get("cooldown_until_ms")),
        "backoff_until_ms": _int(raw.get("backoff_until_ms")),
        "last_review_at_ms": _int(raw.get("last_review_at_ms")),
        "dispatched_signatures": deepcopy(dict(raw.get("dispatched_signatures") or {})),
    }
    if state["revoked"]:
        return state, None

    dispatched = state["dispatched_signatures"]
    if state["inflight"]:
        started = state.get("inflight_started_at_ms")
        if not isinstance(started, int) or isinstance(started, bool) or (now_ms - started >= int(stale_inflight_ms)):
            recovered: list[dict[str, Any]] = []
            seen_recovered: set[str] = set()
            for item in state["inflight_batch"]:
                sig_key = _trigger_sig_key(item)
                dispatched.pop(sig_key, None)
                if sig_key not in seen_recovered:
                    recovered.append(item)
                    seen_recovered.add(sig_key)
            for item in state["pending"]:
                sig_key = _trigger_sig_key(item)
                if sig_key not in seen_recovered:
                    recovered.append(item)
                    seen_recovered.add(sig_key)
            state["pending"] = recovered
            state["inflight"] = False
            state["inflight_started_at_ms"] = None
            state["inflight_batch"] = []
            if state["pending"] and state.get("pending_since_ms") is None:
                state["pending_since_ms"] = max(0, now_ms - MAX_DEBOUNCE_MS)
            state["stale_inflight_recovered_at_ms"] = now_ms
        else:
            state.pop("stale_inflight_recovered_at_ms", None)
    else:
        state.pop("stale_inflight_recovered_at_ms", None)

    effective_debounce_ms = max(MIN_DEBOUNCE_MS, min(MAX_DEBOUNCE_MS, int(debounce_ms)))
    pending_by_key = {_trigger_sig_key(t): idx for idx, t in enumerate(state["pending"])}

    for item in triggers or ():
        if not isinstance(item, Mapping):
            continue
        sig_key = _trigger_sig_key(item)
        prev = dispatched.get(sig_key)
        if isinstance(prev, Mapping) and not _is_escalated(item, prev):
            continue
        if sig_key in pending_by_key:
            idx = pending_by_key[sig_key]
            if _is_escalated(item, state["pending"][idx]):
                state["pending"][idx] = deepcopy(dict(item))
            continue
        pending_by_key[sig_key] = len(state["pending"])
        state["pending"].append(deepcopy(dict(item)))
        if state["pending_since_ms"] is None:
            state["pending_since_ms"] = now_ms
        state["last_trigger_ms"] = max(state["last_trigger_ms"] or now_ms, now_ms)

    if state["inflight"]:
        return state, None
    if not state["pending"]:
        return state, None
    if state["backoff_until_ms"] is not None and now_ms < state["backoff_until_ms"]:
        return state, None

    cond_triggers = [
        t for t in state["pending"]
        if t.get("category") == "A" and t.get("code") == "CONDITION_TRIGGERED"
    ]
    priority_a_bypass = bool(cond_triggers)

    if not priority_a_bypass:
        if state["cooldown_until_ms"] is not None and now_ms < state["cooldown_until_ms"]:
            return state, None
        pending_since = state["pending_since_ms"] if state["pending_since_ms"] is not None else now_ms
        if now_ms - pending_since < effective_debounce_ms:
            return state, None

    batch = state["pending"]
    state["inflight_batch"] = deepcopy(batch)
    for item in batch:
        dispatched[_trigger_sig_key(item)] = {
            "severity": int(item.get("severity") or 0),
            "depth_step": _int(item.get("depth_step")),
            "at_ms": now_ms,
        }
    state["pending"] = []
    state["pending_since_ms"] = None
    state["inflight"] = True
    state["inflight_started_at_ms"] = now_ms
    action = {
        "should_call": True,
        "triggers": batch,
        "reason": "condition_triggered_priority_a" if priority_a_bypass else "coalesced_triggers",
    }
    return state, action


def complete_review_action(
    scheduler_state: Mapping[str, Any],
    *,
    now_ms: int,
    success: bool,
    verdict: str | None = None,
    cooldown_ms: int = DEFAULT_COOLDOWN_MS,
    retry_backoff_ms: int = RETRY_BACKOFF_MS,
) -> dict[str, Any]:
    """Record completion or failure of an inflight review call on scheduler state."""
    state = deepcopy(dict(scheduler_state))
    batch = [deepcopy(dict(t)) for t in (state.pop("inflight_batch", None) or []) if isinstance(t, Mapping)]
    state["inflight"] = False
    state["inflight_started_at_ms"] = None
    if success:
        state["last_review_at_ms"] = now_ms
        state["cooldown_until_ms"] = now_ms + int(cooldown_ms)
        state["backoff_until_ms"] = None
        if str(verdict or "").strip().upper() == "REVOKE":
            state["revoked"] = True
    else:
        dispatched = state.setdefault("dispatched_signatures", {})
        pending = state.setdefault("pending", [])
        seen_pending = {_trigger_sig_key(t) for t in pending if isinstance(t, Mapping)}
        for item in batch:
            sig_key = _trigger_sig_key(item)
            dispatched.pop(sig_key, None)
            if sig_key not in seen_pending:
                pending.insert(0, item)
                seen_pending.add(sig_key)
        if pending and state.get("pending_since_ms") is None:
            state["pending_since_ms"] = max(0, now_ms - MAX_DEBOUNCE_MS)
        state["backoff_until_ms"] = now_ms + int(retry_backoff_ms)
    return state


def _kv_lines(data: Any) -> list[str]:
    if data is None:
        return ["- 无"]
    if isinstance(data, str):
        return [f"- {data.strip()}" if data.strip() else "- 无"]
    if isinstance(data, Mapping):
        if not data:
            return ["- 无"]
        return [f"- {k}: {v}" for k, v in data.items() if v is not None]
    if isinstance(data, Sequence):
        return [f"- {item}" for item in data] or ["- 无"]
    return [f"- {data}"]


def _fmt_num(value: Any, decimals: int = 2, suffix: str = "") -> str:
    n = _num(value)
    if n is None:
        return "未提供"
    return f"{n:,.{decimals}f}{suffix}"


def _fmt_bool_cn(value: Any, true_text: str = "是", false_text: str = "否", none_text: str = "未提供") -> str:
    if value is None:
        return none_text
    return true_text if bool(value) else false_text


def build_entry_decision_basis(
    *,
    hand_id: str | None = None,
    episode_id: str | None = None,
    initial_decision: Mapping[str, Any] | None,
    frozen_condition: Mapping[str, Any],
    raw_sha256: str | None = None,
) -> dict[str, Any]:
    """Build lightweight deterministic `entry_decision_basis@1` with stable IDs (M1/C1..Cn/H1/S1/R1)."""
    dec = initial_decision if isinstance(initial_decision, Mapping) else {}
    judgment = dec.get("judgment") if isinstance(dec.get("judgment"), Mapping) else dec
    contract = frozen_condition.get("contract") if isinstance(frozen_condition.get("contract"), Mapping) else {}
    chosen_side = str(judgment.get("side") or contract.get("side") or "").lower()
    raw_conds = frozen_condition.get("accepted_conditions")
    if isinstance(raw_conds, str) and raw_conds.strip():
        cond_list = [raw_conds.strip()]
    elif isinstance(raw_conds, Sequence) and not isinstance(raw_conds, (bytes, bytearray)):
        cond_list = [str(x).strip() for x in raw_conds if isinstance(x, str) and x.strip()] or ["NOT_RECORDED"]
    else:
        cond_list = ["NOT_RECORDED"]
    conditions = {f"C{i + 1}": text for i, text in enumerate(cond_list)}

    interval_ms = ((frozen_condition.get("check_clock") or {}).get("interval_ms")
                   if isinstance(frozen_condition.get("check_clock"), Mapping) else None)
    counter_key = f"{chosen_side}_counter" if chosen_side in {"put", "call"} else None
    choice_reason = str(judgment.get("comparison") or "").strip() or "NOT_RECORDED"
    strongest_counter = (str(judgment.get(counter_key) or "").strip() if counter_key else "") or "NOT_RECORDED"
    hist_facts = str(frozen_condition.get("historical_facts") or "").strip() or "NOT_RECORDED"

    basis: dict[str, Any] = {
        "schema": "entry_decision_basis@1",
        "hand_id": hand_id,
        "episode_id": episode_id,
        "decision_id": dec.get("attempt_id"),
        "raw_response_hash": raw_sha256 or dec.get("raw_sha256"),
        "selected_side": chosen_side or None,
        "contract": dict(contract),
        "M1": {
            "id": "M1",
            "invalidation": frozen_condition.get("invalidation") or "NOT_RECORDED",
            "invalidation_meaning": frozen_condition.get("invalidation_meaning") or "NOT_RECORDED",
            "check_interval_ms": interval_ms,
        },
        "C": conditions,
        "H1": {"id": "H1", "historical_facts": hist_facts},
        "S1": {
            "id": "S1",
            "original_choice_reason": choice_reason,
            "original_strongest_counterevidence": strongest_counter,
        },
    }
    ard_basis = frozen_condition.get("ard_basis") if isinstance(frozen_condition.get("ard_basis"), Mapping) else None
    valid_ids = ["M1", *conditions.keys(), "H1", "S1"]
    if ard_basis:
        basis["R1"] = {
            "id": "R1",
            "ard_responsibility_meaning": ard_basis.get("responsibility_meaning") or "NOT_RECORDED",
            "ard_review_on": ard_basis.get("review_on") or "NOT_RECORDED",
        }
        valid_ids.append("R1")
    basis["valid_condition_ids"] = valid_ids
    return basis


def _format_t0_lines(data: Any) -> list[str]:
    if not isinstance(data, Mapping):
        return _kv_lines(data)
    return [
        f"- 锁定时刻：{_utc(data.get('locked_at')) if data.get('locked_at') is not None else '未提供'}；入场指数：{_fmt_num(data.get('entry_index_usd'), 2, ' USD')}",
        f"- 入场短腿安全余量：{_fmt_num(data.get('short_distance_pct'), 2, '%')}；本期预期波动尺度：{_fmt_num(data.get('expected_move_usd'), 1, ' USD')}",
    ]


def _format_post_entry_lines(data: Any) -> list[str]:
    if not isinstance(data, Mapping):
        return _kv_lines(data)
    return [
        f"- 入场后连续样本数：{data.get('sample_count', 0)} 个；最新指数：{_fmt_num(data.get('latest_spot_usd'), 2, ' USD')}",
        f"- 入场后短腿安全余量：最低 {_fmt_num(data.get('min_short_distance_pct'), 2, '%')} / 最新 {_fmt_num(data.get('latest_short_distance_pct'), 2, '%')}",
    ]


def _format_condition_lines(data: Any) -> list[str]:
    if not isinstance(data, Mapping):
        return _kv_lines(data)
    trig = bool(data.get("program_triggered"))
    state_label = str(data.get("mechanical_condition_state") or ("TRIGGERED" if trig else "INTACT"))
    lines = [
        f"- 程序机械边界状态（mechanical_condition_state）：{state_label}（{'原冻结破坏规则已由程序确认触发，CONDITION_TRIGGERED 不可改判' if trig else '原冻结破坏规则尚未触发'}）；已完成固定网格检查 {data.get('checks_count', 0)} 次。",
    ]
    if data.get("latest_check_at_ms") is not None:
        lines.append(f"- 最近检查钟时刻：{_utc(data.get('latest_check_at_ms'))}")
    return lines


def _format_ard_lines(data: Any) -> list[str]:
    if not isinstance(data, Mapping):
        return _kv_lines(data)
    same = data.get("same_measure")
    if same is None:
        same = data.get("new_neighborhood_events") is not None
    pin_changed = data.get("pin_price_changed")
    frozen_pin = (data.get("frozen_anchor") or {}).get("pin") if isinstance(data.get("frozen_anchor"), Mapping) else None
    cur_pin = (data.get("current_anchor") or {}).get("pin") if isinstance(data.get("current_anchor"), Mapping) else None
    events = data.get("new_neighborhood_events")
    ev_count = len(events) if isinstance(events, list) else 0
    return [
        f"- 同口径可比性：{_fmt_bool_cn(same, '同口径可比', '口径/窗口不可比（不作为结构变化触发依据）')}",
        f"- Pin 对照：入场参考 Pin {_fmt_num(frozen_pin, 1, ' USD')} -> 最新 Pin {_fmt_num(cur_pin, 1, ' USD')}（是否迁移：{_fmt_bool_cn(pin_changed)}）",
        f"- 现价距入场 Pin 距离：{_fmt_num(data.get('current_distance_to_frozen_pin_usd'), 1, ' USD')}；位于入场邻域内：{_fmt_bool_cn(data.get('current_inside_frozen_neighborhood'))}；入场后新增同口径邻域事件：{ev_count} 次",
    ]


def _format_flow_lines(data: Any) -> list[str]:
    if not isinstance(data, Mapping):
        return _kv_lines(data)
    dir_cn = {"bullish": "偏买", "neutral_to_bullish": "中性偏买", "neutral": "中性",
              "neutral_to_bearish": "中性偏卖", "bearish": "偏卖"}
    cur_dir = dir_cn.get(str(data.get("flow_direction") or "").lower(), str(data.get("flow_direction") or "未提供"))
    fast4 = dir_cn.get(str(data.get("flow_fast_4h") or "").lower(), str(data.get("flow_fast_4h") or "未提供"))
    slow12 = dir_cn.get(str(data.get("flow_slow_12h") or "").lower(), str(data.get("flow_slow_12h") or "未提供"))
    lines = [
        f"- 现货主动流就绪：{_fmt_bool_cn(data.get('flow_ready'))}（流向源时钟：{_utc(data.get('flow_asof_ms')) if data.get('flow_asof_ms') else '随最新样本同步'}）",
        f"- 综合方向：{cur_dir}（近 4h：{fast4} / 近 12h：{slow12}）；注意：4h/12h 为滚动窗，旧柱滑出不直接等同近期相邻不重叠窗口真实反转",
        f"- 近 4h CVD 净额：{_fmt_num(data.get('flow_cvd_4h'), 2)}；近 4h 动量变化：{_fmt_num(data.get('flow_momentum_4h_pct'), 2, '%')}",
    ]
    if _num(data.get("flow_cvd_12h")) is not None or _num(data.get("flow_momentum_12h_pct")) is not None:
        lines.append(
            f"- 近 12h CVD 净额：{_fmt_num(data.get('flow_cvd_12h'), 2)}；近 12h 动量变化：{_fmt_num(data.get('flow_momentum_12h_pct'), 2, '%')}"
        )
    return lines


def _format_economics_lines(t0_summary: Any, quote_summary: Any) -> list[str]:
    lines = []
    if isinstance(t0_summary, Mapping) and t0_summary.get("net_credit_after_fee_btc") is not None:
        lines.append(f"- 锁定时费后净信用估计：{_fmt_num(t0_summary.get('net_credit_after_fee_btc'), 6, ' BTC')}（影子基准，不单独作退出理由）")
    if isinstance(quote_summary, Mapping):
        ask = quote_summary.get("short_buyback_ask_btc")
        fee = quote_summary.get("short_buyback_fee_btc")
        tot = quote_summary.get("short_buyback_total_btc")
        gaps = [str(g) for g in (quote_summary.get("short_buyback_gaps") or ()) if g]
        q_at = quote_summary.get("short_quote_asof_ms")
        if ask is not None and not gaps:
            lines.append(f"- 当前单买回短腿 Ask 估算：{_fmt_num(ask, 6, ' BTC')}（含预估手续费 {_fmt_num(fee, 6, ' BTC')}，合计 {_fmt_num(tot, 6, ' BTC')}；报价时钟 {_utc(q_at)}；假设保留保护腿至到期）")
        else:
            lines.append(f"- 当前单买回短腿报价：暂不可靠成交（缺口：{', '.join(gaps) if gaps else '盘口不足'}；若 REVOKE 将仅记录撤销事实，不虚构止损收益）")
    elif quote_summary is not None:
        lines.extend(_kv_lines(quote_summary))
    return lines or ["- 暂无新增经济与盘口估值"]


def build_hold_review_input(
    *,
    contract: Mapping[str, Any],
    entry_at_ms: int,
    initial_decision: Mapping[str, Any],
    frozen_condition: Mapping[str, Any],
    triggers: Sequence[Mapping[str, Any]],
    t0_summary: Any,
    post_entry_summary: Any,
    condition_summary: Any,
    ard_summary: Any,
    flow_summary: Any,
    quote_summary: Any,
    prior_reviews: Sequence[Mapping[str, Any]] | None = None,
    entry_basis: Mapping[str, Any] | None = None,
) -> str:
    """Render a pure-Chinese, decision-ready incremental hold-review card with stable condition IDs."""
    side_text = str(contract.get("side") or "").upper()
    judgment = initial_decision.get("judgment") if isinstance(initial_decision.get("judgment"), Mapping) else initial_decision
    basis = dict(entry_basis) if isinstance(entry_basis, Mapping) else build_entry_decision_basis(
        initial_decision=initial_decision,
        frozen_condition=frozen_condition,
    )
    m1 = basis["M1"]
    s1 = basis["S1"]
    h1 = basis["H1"]
    c_items = basis.get("C") or {"C1": "NOT_RECORDED"}
    r1 = basis.get("R1")
    prog_trig = bool(isinstance(condition_summary, Mapping) and condition_summary.get("program_triggered"))

    lines = [
        "# 持仓增量复核牌面",
        "",
        "## 原始承诺",
        f"- 锁定责任：{side_text} 短腿 {_fmt_num(contract.get('short_strike_usd'), 0)} / 保护腿 {_fmt_num(contract.get('long_strike_usd'), 0)}（到期 {_utc(contract.get('expiry_ms'))}），入场时间 {_utc(entry_at_ms)}。",
        f"- 原判断：{judgment.get('action') or 'Accept'} / {judgment.get('side') or side_text}；入场指数：{_fmt_num(frozen_condition.get('entry_index_usd'), 2, ' USD')}。",
        f"- [M1] 机械有效破坏规则：{m1['invalidation']}（{m1['invalidation_meaning']}；固定检查间隔：{m1.get('check_interval_ms') or 300000} 毫秒）。",
        f"- [S1] 当初选边依据：{s1['original_choice_reason']}",
        f"- [S1] 入场时已承认并承担的最强反证：{s1['original_strongest_counterevidence']}（入场前旧事实，不得直接当作入场后新暴雷）。",
        *[f"- [{cid}] 持续承担条件：{ctext}" for cid, ctext in c_items.items()],
        f"- [H1] 入场前历史基线：{h1['historical_facts']}（历史基线，不作为退出触发器）。",
    ]
    if r1:
        lines.append(f"- [R1] ARD 复核约定：{r1['ard_responsibility_meaning']}；复核触发：{r1['ard_review_on']}。")
    lines.extend([
        f"- 确认口径：{frozen_condition.get('confirmation_excerpt') or frozen_condition.get('sampling')}",
        "",
        "## 新增事实",
        "### 本次触发事件",
        *[
            f"- [{t.get('category')}:{t.get('code')}] {t.get('summary_cn')}（观测 {_utc(t.get('observed_at_ms'))}）"
            for t in (triggers or ())
        ],
        "### 入场基线与入场后路径",
        *_format_t0_lines(t0_summary),
        *_format_post_entry_lines(post_entry_summary),
        "### 程序条件、ARD 同口径结构与主动流",
        *_format_condition_lines(condition_summary),
        *_format_ard_lines(ard_summary),
        *_format_flow_lines(flow_summary),
        "",
        "## 影子管理经济观察（仅作参考，不替代原条件承保结构判断）",
        *_format_economics_lines(t0_summary, quote_summary),
    ])
    if prior_reviews:
        lines.extend(["", "### 既往持仓复核摘要"])
        for rev in prior_reviews:
            lines.append(f"- {rev.get('verdict')}（{rev.get('protocol_status') or 'RECORDED'}）：{rev.get('public_reply') or rev.get('summary') or ''}")

    lines.extend([
        "",
        "## 条件对照",
        f"- 当前稳定条件 ID 列表：{', '.join(basis.get('valid_condition_ids') or ['M1', 'C1', 'S1'])}。",
        "- 请逐项核对上述新增事实相对 [S1] 入场最强反证、[C1..] 原承担条件及 [M1] 有效破坏规则的实质变化。",
        "- 已冻结且已触发的 `CONDITION_TRIGGERED` 为不可变程序事实，只能如实承接，严禁改写为未触发或以旧窗口事实抵消。",
    ])
    if prog_trig:
        lines.append("- **【重要硬约束】程序已确认 `mechanical_condition_state = TRIGGERED`（[M1] 已被打破）：你若核验数据完整应输出 `REVOKE`，若关键数据阻塞缺损应输出 `UNKNOWN`；禁止输出声称原条件仍完整成立的正常 `HOLD`。**")

    lines.extend([
        "",
        "## 更新判断要求",
        "- 只能输出唯一明确结论：`HOLD`、`REVOKE` 或 `UNKNOWN`。",
        "- `affected_conditions` 必须准确填写所涉条件 ID（如 `[\"M1\", \"C1\", \"S1\"]`）。",
        "- 严禁重新选择 Put/Call 或改边（不得输出 Accept / Put、Accept / Call 或建议切换方向）。",
        "- 严禁推翻或否认不可变的程序事实 `CONDITION_TRIGGERED`。",
        "- 回复末尾必须附 `<HOLD_REVIEW_JSON>{\"verdict\": \"HOLD|REVOKE|UNKNOWN\", \"affected_conditions\": [\"M1\", \"C1\"], \"public_reply\": \"...\", \"key_facts\": [...]}</HOLD_REVIEW_JSON>`。",
    ])
    return "\n".join(lines) + "\n"


def parse_hold_review_response(
    raw_text: str,
    *,
    frozen_condition: Mapping[str, Any],
    program_triggered: bool = False,
    mechanical_state: str | None = None,
    valid_condition_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Parse HOLD/REVOKE/UNKNOWN verdict + structured JSON, separating mechanical vs AI states."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("empty hold review response")

    public_text, payload = _split_tagged_json(raw_text, "HOLD_REVIEW_JSON")
    verdict = str(payload.get("verdict") or "").strip().upper()
    if verdict not in HOLD_VERDICTS:
        raise ValueError(f"invalid hold verdict: {verdict!r}")

    headings: list[str] = []
    for line in public_text.splitlines():
        body = line.strip().lstrip("#* ").rstrip("* ").strip()
        if m := re.match(r"^(HOLD|REVOKE|UNKNOWN)\s*[。.:：]?$", body, re.IGNORECASE):
            headings.append(m.group(1).upper())
    if headings and (len(set(headings)) != 1 or headings[0] != verdict):
        raise ValueError("ambiguous or mismatched hold verdict heading")

    affected = payload.get("affected_conditions")
    if not isinstance(affected, list) or not all(isinstance(x, str) and x.strip() for x in affected):
        raise ValueError("affected_conditions must be a list of non-empty strings")
    cleaned_affected = [x.strip() for x in affected]

    public_reply = payload.get("public_reply")
    if not isinstance(public_reply, str) or not public_reply.strip():
        raise ValueError("public_reply must be a non-empty string")
    if _has_forbidden_reselection(public_text, public_reply):
        raise ValueError("side_reselection_forbidden")
    if "key_facts" not in payload or not isinstance(payload.get("key_facts"), (list, Mapping)):
        raise ValueError("key_facts must be a list or mapping")

    if valid_condition_ids is None:
        derived = build_entry_decision_basis(initial_decision={}, frozen_condition=frozen_condition)
        allowed_ids = set(derived.get("valid_condition_ids") or ())
    else:
        allowed_ids = {str(x).strip() for x in valid_condition_ids if str(x).strip()}
    mapped_ids = [cid for cid in cleaned_affected if cid in allowed_ids]
    unmapped_ids = [cid for cid in cleaned_affected if cid not in allowed_ids]
    mapping_status = "UNMAPPED_CONDITION_ID" if unmapped_ids else "MAPPED"

    mech_state = mechanical_state or ("TRIGGERED" if program_triggered else "INTACT")
    notes: list[str] = []
    if program_triggered:
        if payload.get("program_triggered") is False or _has_untriggered_claim(public_text, public_reply):
            raise ValueError("cannot_overturn_program_triggered")
        notes.append("程序边界条件已触发（CONDITION_TRIGGERED 不可逆转，不可标为未触发）。")
        if verdict == "HOLD":
            protocol_status = "CONFLICT_WITH_FROZEN_INVALIDATION"
            notes.append("模型输出 HOLD，但与已触发的程序冻结破坏规则冲突（标记 CONFLICT_WITH_FROZEN_INVALIDATION）。")
        else:
            protocol_status = "ALIGNED"
    elif verdict == "REVOKE":
        protocol_status = "DISCRETIONARY_REVOKE"
        notes.append("程序机械边界未触发，模型依据新增反证主动建议影子撤销（DISCRETIONARY_REVOKE）。")
    else:
        protocol_status = "ALIGNED"

    return {
        "verdict": verdict,
        "ai_management_verdict": verdict,
        "mechanical_condition_state": mech_state,
        "protocol_status": protocol_status,
        "affected_conditions": cleaned_affected,
        "mapped_condition_ids": mapped_ids,
        "unmapped_condition_ids": unmapped_ids,
        "condition_id_mapping_status": mapping_status,
        "public_reply": public_reply.strip(),
        "key_facts": deepcopy(payload["key_facts"]),
        "public_text": public_text,
        "program_triggered": bool(program_triggered),
        "notes": notes,
    }
