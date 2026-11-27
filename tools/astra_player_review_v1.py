"""Track 2 player AI review: initial hand responder, 4-category triggers, debounce scheduler, and incremental hold-review protocol."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
import os
import re
import socket
from typing import Any, Callable, Mapping, Sequence
import urllib.error

try:
    from . import astra_evidence_qual_v1 as evidence_qual
    from . import astra_player_dossier_v1 as dossier
    from . import signal_llm_review
except ImportError:
    import astra_evidence_qual_v1 as evidence_qual
    import astra_player_dossier_v1 as dossier
    import signal_llm_review

DEFAULT_DEBOUNCE_MS = 12 * 60_000
MIN_DEBOUNCE_MS = 10 * 60_000
MAX_DEBOUNCE_MS = 15 * 60_000
DEFAULT_COOLDOWN_MS = 30 * 60_000
RETRY_BACKOFF_MS = 5 * 60_000
FRESH_SAMPLE_GAP_MS = 10 * 60_000
STALE_INFLIGHT_MS = 25 * 60_000
DEFAULT_MAX_ORDINARY_REVIEWS = 2
SKIP_CODES = frozenset({
    "NO_NEW_EVIDENCE",
    "DUPLICATE_EVENT",
    "BELOW_MATERIALITY",
    "COALESCED",
    "COOLDOWN",
    "DATA_UNVERIFIED",
    "BUDGET_DEFERRED",
})
HOLD_VERDICTS = ("HOLD", "REVOKE", "UNKNOWN")
DEFAULT_CHAT_ENDPOINT = signal_llm_review.OPENAI_CHAT_COMPLETIONS_ENDPOINT
MODEL_REQUEST_SCHEMA = "astra_player_model_request@1.0"
INITIAL_SYSTEM_PROMPT_VERSION = "player_initial_system@1.0"
CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION = "player_initial_system@1.1-candidate"
P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION = "player_initial_system@1.2-p5-candidate"
INITIAL_SYSTEM_PROMPT = (
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
CANDIDATE_INITIAL_SYSTEM_PROMPT = (
    "你是承保影子牌手。请完整阅读冻结牌面并用中文作答。\n"
    "【核心术语与程序边界公理】\n"
    "- 价差内部区间与单侧机械入区：卖出短腿与保护腿之间仅描述价差内部亏损带（Put 为保护腿至短腿之间，Call 为短腿至保护腿之间）；"
    "但程序机械判断（responsibility_trace / check_condition）以卖出短腿为单侧入区门槛——Put 分钟采样严格低于短腿、Call 分钟采样严格高于短腿即记为严格进入"
    "（包括两腿之间、恰好触及保护腿边界以及已越过保护腿外侧），触及短腿边界本身既不算新进入也不算严格返回，只有严格回到短腿外侧才算返回。\n"
    "- 本期尺度：指本期到期隐含波动给出的 `expected_move_usd` 预期波动尺度。\n"
    "- Pin / 墙：仅作当前空间结构位置参照，不带方向性，不构成价格吸附保证。\n"
    "- ARD：仅衡量过去 4 小时围绕同到期已知 Pin 的历史贴近、离返与迁移事实，不做未来方向预测。\n"
    "【作答与规则锁定】\n"
    "1. 在正文开头或明确独立标题行写出唯一结论：`## Accept / Put`、`## Accept / Call`、`## Reject` 或 `## Unknown`。\n"
    "2. 正文按固定语义段各自先写一段连贯完整的中文整句段落（尽量不用分点或反引号）：`Put路径判断：`、`Call路径判断：`、`Put最强反证：`、`Call最强反证：`、`两侧对比择优：`，以及（若 Accept）`冻结历史事实：`、`承担条件：`、`破坏确认口径：`（与 `ARD判读：`）。\n"
    "3. `承担条件`（C）必须独立于机械破坏（M），且同时写清四要素：（1）冻结时已正面承担的最强不利事实；（2）该不利事实尚未排除当前承担依据的理由；（3）尚未消除的关键来源、时钟或覆盖不确定性；（4）未来出现何种独立于机械越界、同口径可核验的新增事实将触发对原依据的实质重审。严禁写成“只要未确认入区/未触发 M 就继续承保”。\n"
    "4. `破坏确认口径`（M）由程序按固定合约与规则校验：仅支持 `\"new_entry\"`（入场后出现严格越过短腿采样即触发，含保护腿边界与外侧）或 `\"entry_not_returned\"`（入场后严格越过短腿且至固定检查点仍未严格回到短腿外侧才触发），固定检查钟按 UTC 每 5 分钟网格（`check_interval_ms = 300000`）执行，不得自行改变单侧边界、保护腿含义、采样来源或检查时钟。\n"
    "5. 在正文最后附带 `<ANNOTATION_JSON>...</ANNOTATION_JSON>`（合法 JSON 对象）。其中 `put`、`call`、`comparison`、`put_counter`、`call_counter` 必须是**从上方对应中文段落完整逐字复制的全文片段**——禁止只摘前半段、漏掉结论句或在词语/句子中途截断；`depends_on_ard` 必须是显式布尔值。\n"
    "6. 若结论为 Accept，`<ANNOTATION_JSON>` 须包含 `condition_plan` 对象，含 `invalidation`（`\"new_entry\"` 或 `\"entry_not_returned\"`）、`check_interval_ms`（固定 `300000`）、`historical_facts`、`accepted_conditions`、`confirmation_excerpt`（三者均须完整逐字复制自对应段落全文），以及可选 `require_outside_at_t0`（布尔）；若 `depends_on_ard` 为 true，须同时逐字提供 `ard_responsibility_meaning` 与 `ard_review_on`。"
)
P5_CANDIDATE_INITIAL_SYSTEM_PROMPT = (
    "你是承保影子牌手。请完整阅读冻结牌面并用中文作答。\n"
    "【核心术语与机械公理】\n"
    "- 单侧机械边界：短腿与保护腿之间仅描述价差几何亏损带，不构成机械上限。"
    "Put 分钟采样严格低于短腿、Call 严格高于短腿即记为严格进入（含保护腿边界与外侧）；"
    "触及短腿不算进入也不算返回，严格回到短腿外侧才算返回。\n"
    "- 检查时钟：首次检查为入场后下一 UTC 整分钟，之后每 5 分钟（`check_interval_ms = 300000`）步进；"
    "不要写成自然 `:00/:05` 整点网格或 60 分钟锚点。\n"
    "- 本期尺度：到期隐含波动给出的 `expected_move_usd`；Pin/墙仅作空间参照不带方向性；"
    "ARD 仅衡量过去 4 小时围绕同到期 Pin 的贴近、离返与迁移事实。\n"
    "【作答与规则锁定】\n"
    "1. 正文开头或独立标题行写出唯一结论：`## Accept / Put`、`## Accept / Call`、`## Reject` 或 `## Unknown`。\n"
    "2. 正文以连贯完整的中文自然段分别写清 Put 路径判断、Call 路径判断、Put 最强反证、Call 最强反证与两侧对比择优；"
    "若 Accept，另写清冻结历史事实、承担条件（C）、破坏确认口径（M）及（若依赖 ARD）ARD 责任相关性与复核变化；"
    "每段请写完整句，摘录段尽量不用分点或反引号。\n"
    "3. 机械破坏口径（M）仅支持 `\"new_entry\"`（入场后出现严格越过短腿采样即触发）或 "
    "`\"entry_not_returned\"`（入场后严格越过短腿且至检查点仍未严格回到短腿外侧才触发），"
    "按上述 5 分钟时钟执行且须锁定 `require_outside_at_t0 = true`。\n"
    "4. 承担条件（C，对应 `accepted_conditions`）须独立于机械 M，简明写清四项："
    "（1）冻结时正面承担的最强已知不利事实；（2）尚未排除承保依据的理由；"
    "（3）关键来源、时钟或覆盖不确定性（标明 `OBSERVED_UNALIGNED` 或 `UNKNOWN`，严禁将资料缺失或未证实表述为“从未发生”）；"
    "（4）独立于机械 M、可在越界前由系统盘中真实数据源复核的未来证伪事实（短腿外侧剩余余量与路径演变、时钟对齐后的同源 4h/12h 现货主动流、同口径本期 Pin/ARD 迁移）。"
    "仅在机械越界后才成立的条件不算独立前置 C；不可观测项标 `UNKNOWN`；C 触发仅启动独立复核与留痕，不授权自动退出或交易。\n"
    "5. 文末附 `<ANNOTATION_JSON>...</ANNOTATION_JSON>`（合法 JSON 对象）："
    "`put`、`call`、`comparison`、`put_counter`、`call_counter` 须为上方正文逐字复制的完整连贯段落（禁止删改、浓缩或句中截断）；`depends_on_ard` 为显式布尔值。\n"
    "6. 若 Accept，须含 `condition_plan` 对象：`invalidation`（`\"new_entry\"` 或 `\"entry_not_returned\"`）、"
    "`check_interval_ms`（`300000`）、`require_outside_at_t0`（`true`）、"
    "`historical_facts`、`accepted_conditions`、`confirmation_excerpt`（均完整逐字复制自正文）；"
    "若 `depends_on_ard` 为 `true`，须在 `condition_plan` 内逐字提供 `ard_responsibility_meaning` 与 `ard_review_on`。"
)
INITIAL_SYSTEM_PROMPTS = {
    INITIAL_SYSTEM_PROMPT_VERSION: INITIAL_SYSTEM_PROMPT,
    CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION: CANDIDATE_INITIAL_SYSTEM_PROMPT,
    P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION: P5_CANDIDATE_INITIAL_SYSTEM_PROMPT,
}
CANDIDATE_STRICT_PROMPT_VERSIONS = frozenset({
    CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
    P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
})
P5_STRICT_PROMPT_VERSIONS = frozenset({
    P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION,
})
HOLD_REVIEW_SYSTEM_PROMPT_VERSION = "player_hold_system@1.0"
HOLD_REVIEW_SYSTEM_PROMPT = "你是中性回路影子持仓复核 AI。只可基于增量牌面输出 HOLD、REVOKE 或 UNKNOWN，严禁改选 Put/Call，严禁推翻已触发的 CONDITION_TRIGGERED。"
_BANNED_REQUEST_KEYS = frozenset({
    "decision",
    "judgment",
    "action",
    "side",
    "raw_reply",
    "response",
    "public_reply",
    "verdict",
    "outcome",
    "condition",
    "locked_series",
    "api_key",
    "reasoning_content",
    "reasoning",
})
_REQUEST_STAGE_LABELS = {
    "initial": "initial（初始发牌）",
    "hold_review": "hold_review（持仓复核）",
}
_MESSAGE_ROLE_LABELS = {
    "system": "系统提示词",
    "user": "用户牌面输入",
    "assistant": "历史助手消息",
}

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


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_canonical_json(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class ContractValidationError(ValueError):
    """Raised when a parsed initial reply fails strict candidate contract checks while preserving parsed artifacts."""

    def __init__(
        self,
        message: str,
        *,
        parsed_reply: Mapping[str, Any] | None = None,
        contract_validation: Mapping[str, Any] | None = None,
    ):
        super().__init__(message)
        self.parsed_reply = dict(parsed_reply) if isinstance(parsed_reply, Mapping) else {}
        self.contract_validation = dict(contract_validation) if isinstance(contract_validation, Mapping) else {}


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
    # Determine the legacy test transport signature before calling. A TypeError
    # raised inside an already dispatched transport must not send a second POST.
    args = (api_key, model, request_body, timeout_s)
    try:
        signature = inspect.signature(poster)
    except (TypeError, ValueError):
        return poster(*args, endpoint=url)
    try:
        signature.bind(*args, endpoint=url)
    except TypeError:
        signature.bind(*args)
        return poster(*args)
    return poster(*args, endpoint=url)


def _resolve_stream_flag(stream: bool | None = None, *, transport: Any = None) -> bool:
    if stream is not None:
        return bool(stream)
    if transport is not None:
        return False
    raw = str(os.environ.get("ASTRA_PLAYER_STREAM", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def call_chat_llm(
    messages: Sequence[Mapping[str, Any]],
    *,
    api_key: str,
    model: str,
    endpoint: str | None = None,
    timeout_s: float = 180,
    retries: int = 1,
    max_tokens: int = 32768,
    thinking: bool = True,
    reasoning_effort: str | None = None,
    stream: bool | None = None,
    on_partial: Callable[[dict[str, Any]], None] | None = None,
    transport: Any = None,
    budget: Any = None,
    budget_role: str = "astra_player",
    packet_hash: str | None = None,
    on_response: Any = None,
) -> dict[str, Any]:
    """Call OpenAI-compatible chat completions with endpoint normalization and transient retries."""
    if not api_key and transport is None:
        raise RuntimeError("LLM_API_KEY is required")
    url = _normalize_endpoint(endpoint)
    eff_stream = _resolve_stream_flag(stream, transport=transport)
    request_body: dict[str, Any] = {
        "model": model,
        "messages": [dict(m) for m in messages],
        "stream": eff_stream,
        "max_tokens": int(max_tokens),
        "thinking": {"type": "enabled" if thinking else "disabled"},
    }
    if reasoning_effort and str(reasoning_effort).strip():
        request_body["reasoning_effort"] = str(reasoning_effort).strip()
    poster = transport or signal_llm_review._post_chat_completion
    wire_request_body = dict(request_body)
    if (
        on_partial is not None
        and eff_stream
        and (transport is None or transport is signal_llm_review._post_chat_completion)
    ):
        wire_request_body["_local_on_partial"] = on_partial
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
            response = _invoke_transport(poster, api_key, model, wire_request_body, timeout_s, url)
        except Exception as exc:
            partial_resp = getattr(exc, "partial_response", None)
            if partial_resp is None and getattr(exc, "__cause__", None) is not None:
                partial_resp = getattr(exc.__cause__, "partial_response", None)
                if partial_resp is not None:
                    try:
                        setattr(exc, "partial_response", partial_resp)
                    except Exception:
                        pass
            if callable(on_partial) and isinstance(partial_resp, Mapping):
                try:
                    on_partial(dict(partial_resp))
                except Exception:
                    pass
            partial_usage = (
                signal_llm_review._response_usage(dict(partial_resp))
                if isinstance(partial_resp, Mapping)
                else {}
            )
            if budget is not None and not isinstance(exc, signal_llm_review.DailyBudgetExceeded):
                budget.complete(res_id, _budget_error_category(exc), usage=partial_usage)
            if (
                isinstance(exc, signal_llm_review.DailyBudgetExceeded)
                or attempt + 1 >= total_attempts
                or not _is_transient_transport_error(exc)
            ):
                raise
        else:
            break
    if not isinstance(response, Mapping):
        if budget is not None:
            budget.complete(res_id, "HTTP_ERROR", usage={})
        raise ValueError("LLM response is not a mapping")
    usage: dict[str, Any] = {}
    try:
        choices = response.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], Mapping) else {}
        finish_reason = choice.get("finish_reason")
        message = choice.get("message") if isinstance(choice.get("message"), Mapping) else {}
        raw_content = message.get("content")
        if isinstance(raw_content, list):
            text = "".join(
                str(part.get("text") or "")
                for part in raw_content
                if isinstance(part, Mapping)
            )
        else:
            text = str(raw_content or "")
        raw_http_status = response.get("_http_status") or response.get("http_status") or 200
        try:
            http_status = int(raw_http_status) if not isinstance(raw_http_status, bool) else 200
        except (TypeError, ValueError):
            http_status = 200
        usage = signal_llm_review._response_usage(dict(response))
        if not isinstance(choices, list) or not choices:
            raise ValueError("LLM response missing choices")
        response_record = dict(
            text=text, usage=usage, model=str(response.get("model") or model),
            http_status=http_status, finish_reason=finish_reason,
            endpoint=url, reasoning_effort=reasoning_effort,
        )
        if not text.strip():
            if finish_reason == "length":
                if on_response is not None:
                    on_response(response_record)
                raise ValueError("LLM response content is empty (finish_reason=length)")
            raise ValueError("LLM response content is empty")
        if on_response is not None:
            on_response(response_record)
    finally:
        if budget is not None:
            budget.complete(res_id, "HTTP_OK", usage=usage)
    reasoning_text = message.get("reasoning_content")
    return {
        "text": text.strip(),
        "reasoning_content": str(reasoning_text) if reasoning_text else None,
        "usage": signal_llm_review._response_usage(dict(response)),
        "finish_reason": finish_reason,
        "http_status": http_status,
        "model": str(response.get("model") or model),
        "reasoning_effort": reasoning_effort,
        "endpoint": url,
        "request_body": deepcopy(request_body),
        "attempts": attempts_used,
    }


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    match = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", stripped, re.IGNORECASE)
    return match.group(1).strip() if match else stripped


def _strip_json_trailing_commas(payload_text: str) -> str:
    """Remove trailing commas before `}` or `]` outside JSON string literals."""
    out: list[str] = []
    in_str = False
    escaped = False
    n = len(payload_text)
    i = 0
    while i < n:
        ch = payload_text[i]
        if in_str:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < n and payload_text[j] in " \t\r\n":
                j += 1
            if j < n and payload_text[j] in "}]":
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _loads_json_object_with_minimal_repair(payload_text: str) -> tuple[dict[str, Any], list[str]]:
    repairs: list[str] = []
    try:
        parsed = json.loads(payload_text)
    except json.JSONDecodeError:
        cleaned = _strip_json_trailing_commas(payload_text)
        if cleaned == payload_text:
            raise
        parsed = json.loads(cleaned)
        repairs.append("annotation_json_trailing_comma_repaired")
    if not isinstance(parsed, dict):
        raise ValueError("structured JSON block must be an object")
    return parsed, repairs


def _split_tagged_json_with_repairs(text: str, tag_name: str) -> tuple[str, dict[str, Any], list[str]]:
    repairs: list[str] = []
    tag_prefix = tag_name.split("_", 1)[0]
    tag_label = tag_prefix.lower()
    pattern = re.compile(rf"<{re.escape(tag_name)}>\s*([\s\S]*?)\s*</{re.escape(tag_name)}>", re.IGNORECASE)
    matches = list(pattern.finditer(text))
    if matches:
        match = matches[-1]
        public_text = text[:match.start()].rstrip()
        raw_inner = match.group(1).strip()
        payload_text = _strip_code_fence(raw_inner)
        if payload_text != raw_inner:
            repairs.append(f"{tag_label}_code_fence_stripped")
        if text[match.end():].strip():
            repairs.append(f"trailing_text_after_{tag_label}_tag_ignored")
    else:
        open_matches = list(re.finditer(rf"<{re.escape(tag_name)}>", text, re.IGNORECASE))
        if open_matches:
            open_match = open_matches[-1]
            public_text = text[:open_match.start()].rstrip()
            tail = text[open_match.end():].strip()
            tail_no_close = re.sub(
                rf"</\s*(?:{re.escape(tag_name)}|{re.escape(tag_prefix)}[A-Z0-9_]*)\s*>?\s*$",
                "",
                tail,
                flags=re.IGNORECASE,
            ).strip()
            payload_text = _strip_code_fence(tail_no_close)
            open_repairs = [f"{tag_label}_closing_tag_repaired"]
            if payload_text != tail_no_close:
                open_repairs.append(f"{tag_label}_code_fence_stripped")
            parsed, json_repairs = _loads_json_object_with_minimal_repair(payload_text)
            return public_text, parsed, [*open_repairs, *json_repairs]
        fence_matches = list(re.finditer(r"```json\s*([\s\S]*?)\s*```", text, re.IGNORECASE))
        if not fence_matches:
            raise ValueError(f"missing {tag_name} or trailing json block")
        fence_match = fence_matches[-1]
        public_text = text[:fence_match.start()].rstrip()
        payload_text = fence_match.group(1).strip()
        repairs.append(f"{tag_label}_fenced_json_fallback")
    parsed, json_repairs = _loads_json_object_with_minimal_repair(payload_text)
    repairs.extend(json_repairs)
    return public_text, parsed, repairs


def _split_tagged_json(text: str, tag_name: str) -> tuple[str, dict[str, Any]]:
    public_text, parsed, _ = _split_tagged_json_with_repairs(text, tag_name)
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


def _strip_banned_keys(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            k: _strip_banned_keys(v)
            for k, v in value.items()
            if str(k) not in _BANNED_REQUEST_KEYS and not str(k).startswith("_local_")
        }
    if isinstance(value, (list, tuple)):
        return [_strip_banned_keys(item) for item in value]
    return deepcopy(value)


def sanitize_model_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Deep-copy a canonical model request payload and strip any banned post-decision/leakage keys."""
    if not isinstance(payload, Mapping):
        raise TypeError("request payload must be a mapping")
    cleaned = _strip_banned_keys(dict(payload))
    raw_messages = cleaned.get("messages")
    if isinstance(raw_messages, Sequence) and not isinstance(raw_messages, (str, bytes, bytearray)):
        cleaned["messages"] = [
            {
                **{k: deepcopy(v) for k, v in msg.items() if str(k) not in _BANNED_REQUEST_KEYS},
                "role": str(msg.get("role") or "user"),
                "content": str(msg.get("content") or ""),
            }
            for msg in raw_messages
            if isinstance(msg, Mapping)
        ]
    else:
        cleaned["messages"] = []
    cleaned["metadata"] = dict(cleaned["metadata"]) if isinstance(cleaned.get("metadata"), Mapping) else {}
    cleaned["parameters"] = dict(cleaned["parameters"]) if isinstance(cleaned.get("parameters"), Mapping) else {}
    return cleaned


def _build_model_request_payload(
    *,
    stage: str,
    prompt_version: str,
    system_prompt: str,
    user_content: str,
    model: str | None,
    max_tokens: int = 32768,
    thinking: bool = True,
    reasoning_effort: str | None = None,
    stream: bool = False,
    endpoint: str | None = None,
    packet_hash: str | None = None,
    requested_at_ms: int | None = None,
    fidelity: str = "EXACT",
    fidelity_reason: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    fidelity_norm = str(fidelity or "EXACT").strip().upper()
    if fidelity_norm not in {"EXACT", "RECONSTRUCTED"}:
        raise ValueError(f"unsupported request fidelity: {fidelity!r}")
    effort = str(reasoning_effort).strip() if reasoning_effort and str(reasoning_effort).strip() else None
    parameters: dict[str, Any] = {
        "stream": bool(stream),
        "max_tokens": int(max_tokens),
        "thinking": {"type": "enabled" if thinking else "disabled"},
    }
    if effort:
        parameters["reasoning_effort"] = effort
    payload: dict[str, Any] = {
        "schema": MODEL_REQUEST_SCHEMA,
        "stage": stage,
        "fidelity": fidelity_norm,
        "fidelity_reason": fidelity_reason,
        "prompt_version": prompt_version,
        "model": str(model or "unknown"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "parameters": parameters,
        "endpoint": _normalize_endpoint(endpoint) if endpoint else None,
        "packet_hash": packet_hash,
        "requested_at_ms": requested_at_ms,
        "metadata": dict(metadata) if isinstance(metadata, Mapping) else {},
    }
    return sanitize_model_request(payload)


def build_initial_request_payload(
    input_md_text: str,
    *,
    contract_pair: Any = None,
    model: str | None = None,
    prompt_version: str = INITIAL_SYSTEM_PROMPT_VERSION,
    input_profile: str | None = None,
    max_tokens: int = 32768,
    thinking: bool = True,
    reasoning_effort: str | None = None,
    stream: bool | None = None,
    endpoint: str | None = None,
    packet_hash: str | None = None,
    requested_at_ms: int | None = None,
    fidelity: str = "EXACT",
    fidelity_reason: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build canonical model request payload for an initial hand decision with deterministic receipts."""
    if prompt_version not in INITIAL_SYSTEM_PROMPTS:
        raise ValueError(f"unsupported initial prompt_version: {prompt_version!r}")
    p0_profile = getattr(dossier.player, "INPUT_PROFILE_P0_STRICT", "p0_strict@1")
    legacy_profile = getattr(dossier.player, "INPUT_PROFILE_LEGACY", "legacy_v1")
    supported_profiles = getattr(dossier.player, "SUPPORTED_INPUT_PROFILES", (legacy_profile, p0_profile))
    if input_profile is not None:
        if input_profile not in supported_profiles:
            raise ValueError(f"unsupported input_profile: {input_profile!r}")
        eff_input_profile: str | None = input_profile
    elif prompt_version == P5_CANDIDATE_INITIAL_SYSTEM_PROMPT_VERSION:
        eff_input_profile = p0_profile
    else:
        eff_input_profile = None

    pair_summary = _format_contract_pair(contract_pair)
    user_content = f"候选合约对：\n{pair_summary}\n\n{input_md_text}" if pair_summary else input_md_text
    meta_out = dict(metadata) if isinstance(metadata, Mapping) else {}
    if (prompt_version in CANDIDATE_STRICT_PROMPT_VERSIONS or eff_input_profile == p0_profile) and contract_pair is not None:
        pair_meta: dict[str, Any] = {}
        for side_name in ("put", "call"):
            resolved = _resolve_contract_from_pair(contract_pair, side_name)
            if resolved is not None:
                pair_meta[side_name] = resolved
        if pair_meta:
            meta_out.setdefault("contract_pair", pair_meta)
    payload = _build_model_request_payload(
        stage="initial",
        prompt_version=prompt_version,
        system_prompt=INITIAL_SYSTEM_PROMPTS[prompt_version],
        user_content=user_content,
        model=model,
        max_tokens=max_tokens,
        thinking=thinking,
        reasoning_effort=reasoning_effort,
        stream=_resolve_stream_flag(stream),
        endpoint=endpoint,
        packet_hash=packet_hash,
        requested_at_ms=requested_at_ms,
        fidelity=fidelity,
        fidelity_reason=fidelity_reason,
        metadata=meta_out,
    )
    payload["system_sha256"] = _sha256_text(payload["messages"][0]["content"])
    payload["user_sha256"] = _sha256_text(payload["messages"][1]["content"])
    payload["messages_sha256"] = _sha256_canonical_json(payload["messages"])
    payload["body_sha256"] = _sha256_canonical_json({
        "messages": payload["messages"],
        "model": payload["model"],
        "parameters": payload["parameters"],
        "prompt_version": payload["prompt_version"],
    })
    if eff_input_profile is not None:
        payload["input_profile"] = eff_input_profile
    if prompt_version in CANDIDATE_STRICT_PROMPT_VERSIONS:
        payload["mechanical_contract_version"] = getattr(dossier.condition, "MECHANICAL_CONTRACT_VERSION", "p2_strict@1")
    return payload


def build_hold_review_request_payload(
    input_md_text: str,
    *,
    model: str | None,
    max_tokens: int = 32768,
    thinking: bool = True,
    reasoning_effort: str | None = None,
    stream: bool | None = None,
    endpoint: str | None = None,
    packet_hash: str | None = None,
    requested_at_ms: int | None = None,
    fidelity: str = "EXACT",
    fidelity_reason: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build canonical model request payload for an incremental hold review."""
    return _build_model_request_payload(
        stage="hold_review",
        prompt_version=HOLD_REVIEW_SYSTEM_PROMPT_VERSION,
        system_prompt=HOLD_REVIEW_SYSTEM_PROMPT,
        user_content=input_md_text,
        model=model,
        max_tokens=max_tokens,
        thinking=thinking,
        reasoning_effort=reasoning_effort,
        stream=_resolve_stream_flag(stream),
        endpoint=endpoint,
        packet_hash=packet_hash,
        requested_at_ms=requested_at_ms,
        fidelity=fidelity,
        fidelity_reason=fidelity_reason,
        metadata=metadata,
    )


def format_single_box_input(request_payload: Mapping[str, Any]) -> str:
    """Format all request messages in exact order for single-box web UI testing."""
    clean = sanitize_model_request(request_payload)
    blocks: list[str] = []
    for msg in clean.get("messages") or ():
        role = str(msg.get("role") or "user").strip().upper() or "USER"
        content = str(msg.get("content") or "")
        blocks.append(f"【{role}】\n{content}")
    return ("\n\n".join(blocks) + "\n") if blocks else ""


def format_model_input_markdown(
    request_payload: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
) -> str:
    """Render leakage-safe `full_model_input.md` preserving verbatim message order and a safe single-box copy block."""
    clean = sanitize_model_request(request_payload)
    safe_ctx = _strip_banned_keys(dict(context)) if isinstance(context, Mapping) else {}
    meta = {**(clean.get("metadata") or {}), **safe_ctx}

    fidelity = str(clean.get("fidelity") or "EXACT").strip().upper()
    fidelity_reason = clean.get("fidelity_reason")
    fidelity_line = f"{fidelity}（{fidelity_reason}）" if fidelity_reason else fidelity

    stage_raw = str(clean.get("stage") or "").strip()
    stage_line = _REQUEST_STAGE_LABELS.get(stage_raw, stage_raw or "未知")

    scope_parts: list[str] = []
    if meta.get("dossier_id"):
        scope_parts.append(f"卷宗 `{meta['dossier_id']}`")
    if meta.get("hand_id"):
        scope_parts.append(f"手牌 `{meta['hand_id']}`")
    if meta.get("review_id"):
        scope_parts.append(f"复核 `{meta['review_id']}`")
    sample_val = meta.get("sample") or meta.get("sample_kind")
    if sample_val:
        scope_parts.append(f"样本类型 `{sample_val}`")
    scope_line = " / ".join(scope_parts) if scope_parts else "未提供"

    expiry_ms = meta.get("expiry_ms")
    req_ms = clean.get("requested_at_ms")
    if req_ms is None:
        req_ms = meta.get("requested_at_ms")
    expiry_txt = _utc(expiry_ms) if expiry_ms is not None else "未提供"
    req_txt = _utc(req_ms) if req_ms is not None else "未提供"

    params = clean.get("parameters") if isinstance(clean.get("parameters"), Mapping) else {}
    thinking_obj = params.get("thinking")
    thinking_txt = thinking_obj.get("type") if isinstance(thinking_obj, Mapping) else thinking_obj
    effort_txt = params.get("reasoning_effort") or "未指定"
    max_tokens_txt = params.get("max_tokens") if params.get("max_tokens") is not None else "未提供"
    stream_txt = params.get("stream") if "stream" in params else "未提供"

    lines = [
        "# AI 牌手完整模型输入",
        "",
        f"- 来源保真度：{fidelity_line}",
        f"- 阶段：{stage_line}",
        f"- 卷宗 / 手牌 / 样本类型：{scope_line}",
        f"- 到期时间：{expiry_txt}；请求时间：{req_txt}",
        (
            f"- 模型与参数：model=`{clean.get('model') or 'unknown'}`；thinking=`{thinking_txt}`；"
            f"reasoning_effort=`{effort_txt}`；max_tokens=`{max_tokens_txt}`；stream=`{stream_txt}`；"
            f"prompt_version=`{clean.get('prompt_version') or '未提供'}`；"
            f"packet_hash=`{clean.get('packet_hash') or '未提供'}`；"
            f"endpoint=`{clean.get('endpoint') or '未提供'}`"
        ),
        "",
        "> 保真度与单框测试说明：`EXACT` 表示由实发请求直接保存的逐字原文；`RECONSTRUCTED` 表示在缺少原生请求快照时，依据当时冻结证据与固定提示词模板按原逻辑重建的等价输入。",
        "> 下方「DeepSeek 官方网页单输入框可复制区块」仅用于网页端单框对照测试，不代表与原生 API `system` / `user` 消息具有完全相同的角色权限权重。",
        "",
    ]

    for idx, msg in enumerate(clean.get("messages") or (), 1):
        role = str(msg.get("role") or "user").strip().lower() or "user"
        role_label = _MESSAGE_ROLE_LABELS.get(role, role.upper())
        content = str(msg.get("content") or "")
        lines.extend([
            f"## 消息 {idx} · {role_label} (`role: {role}`)",
            "",
            content,
            "",
        ])

    single_box = format_single_box_input(clean)
    longest_run = max((len(m.group(0)) for m in re.finditer(r"`+", single_box)), default=0)
    fence = "`" * max(4, longest_run + 1)
    single_box_body = single_box[:-1] if single_box.endswith("\n") else single_box
    lines.extend([
        "## DeepSeek 官方网页单输入框可复制区块",
        "",
        f"{fence}text",
        single_box_body,
        fence,
        "",
    ])
    return "\n".join(lines)


def decide_initial_hand(
    input_md_text: str,
    *,
    contract_pair: Any,
    api_key: str,
    model: str,
    prompt_version: str = INITIAL_SYSTEM_PROMPT_VERSION,
    input_profile: str | None = None,
    endpoint: str | None = None,
    timeout_s: float = 180,
    retries: int | None = None,
    max_tokens: int = 32768,
    reasoning_effort: str | None = None,
    stream: bool | None = None,
    on_partial: Callable[[dict[str, Any]], None] | None = None,
    transport: Any = None,
    budget: Any = None,
    budget_role: str = "astra_player_initial",
    packet_hash: str | None = None,
    on_response: Any = None,
    strict_contract: bool | None = None,
    strict_clock_5m: bool | None = None,
) -> dict[str, Any]:
    """Send frozen input.md to the LLM, parse public Chinese prose + verbatim annotation + condition_plan."""
    eff_stream = _resolve_stream_flag(stream, transport=transport)
    req_payload = build_initial_request_payload(
        input_md_text,
        contract_pair=contract_pair,
        model=model,
        prompt_version=prompt_version,
        input_profile=input_profile,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        stream=eff_stream,
        endpoint=endpoint,
        packet_hash=packet_hash,
        fidelity="EXACT",
    )
    effective_retries = (0 if float(timeout_s) >= 300 else 1) if retries is None else retries
    llm_result = call_chat_llm(
        req_payload["messages"],
        api_key=api_key,
        model=model,
        endpoint=endpoint,
        timeout_s=timeout_s,
        retries=effective_retries,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        stream=eff_stream,
        on_partial=on_partial,
        transport=transport,
        budget=budget,
        budget_role=budget_role,
        packet_hash=packet_hash,
        on_response=on_response,
    )
    return parse_initial_reply(
        llm_result,
        req_payload,
        strict_contract=strict_contract,
        contracts=contract_pair,
        strict_clock_5m=strict_clock_5m,
    )


def _resolve_contract_from_pair(contract_pair: Any, side: str) -> dict[str, Any] | None:
    side_norm = str(side or "").strip().lower()
    if side_norm not in ("put", "call") or contract_pair is None:
        return None
    if isinstance(contract_pair, Mapping):
        if side_norm in contract_pair and isinstance(contract_pair[side_norm], Mapping):
            cand = dict(contract_pair[side_norm])
        elif str(contract_pair.get("side") or "").strip().lower() == side_norm:
            cand = dict(contract_pair)
        else:
            return None
    elif isinstance(contract_pair, Sequence) and not isinstance(contract_pair, (str, bytes)):
        cand = next(
            (dict(item) for item in contract_pair if isinstance(item, Mapping) and str(item.get("side") or "").strip().lower() == side_norm),
            None,
        )
        if cand is None:
            return None
    else:
        return None
    cand.setdefault("side", side_norm)
    short_k = _num(cand.get("short_strike_usd"))
    long_k = _num(cand.get("long_strike_usd"))
    if short_k is None or long_k is None or short_k <= 0 or long_k <= 0:
        return None
    if (short_k <= long_k) if side_norm == "put" else (short_k >= long_k):
        return None
    return cand


_C_DEGENERATE_RE = re.compile(
    r"(?:只有|仅当|唯有)[^。；\n]*(?:严格(?:低于|高于|越过|进入)|new_entry|entry_not_returned)[^。；\n]*才(?:构成|算|视作)?(?:破坏|触发)"
    r"|未严格进入则仍继续|不提前算入破坏"
    r"|即触发\s*(?:new_entry|entry_not_returned)\s*破坏"
    r"|只要求未确认进入(?:责任区|短腿)?"
    r"|只要未(?:进入|触发)[^。；\n]*(?:就|即|均|便)(?:继续)?(?:承保|持有)"
)


def inspect_accepted_conditions_semantics(text: str) -> dict[str, Any]:
    """Detect C1 degradation signals vs valid feed-grounded independent falsifiers (Layer-2 advisory only)."""
    if not isinstance(text, str) or not text.strip():
        return {
            "empty": True,
            "c_degenerates_to_m": True,
            "has_borne_adverse": False,
            "has_independent_falsifier": False,
            "has_uncertainty_disclosure": False,
            "has_grounded_hold_feed": False,
            "has_post_m_only_trigger_risk": True,
        }
    clauses = [c.strip() for c in re.split(r"[。；;\n]+", text) if c.strip()]
    has_borne_adverse = bool(re.search(
        r"已承担|已承受|正面承担|最坏|不利事实|余量(?:仅|约)|无结构缓冲|无已列|零缓冲|反证|"
        r"(?:4h|4小时|60m|240m)[^。；\n]*(?:下行|下跌|净卖|反证|负|-\d)",
        text,
    ))
    has_independent_falsifier = bool(re.search(
        r"事中事件门|独立复核|实质重审|独立于\s*(?:机械\s*)?M|独立证伪|同口径可核验|未来证伪|"
        r"(?:若|如果|一旦|当)[^。；\n]*(?:余量(?:实质)?消耗|余量(?:继续)?收缩|同向现货净卖|同向现货净买|"
        r"流向(?:恶化|转)|主动流(?:转|恶化|继续)|同向压力|复核依据|重新审查|Pin[^。；\n]*(?:迁移|移向)|ARD[^。；\n]*(?:迁移|穿越|离返))",
        text,
    ))
    has_uncertainty_disclosure = bool(re.search(
        r"不确定性|覆盖不足|未对齐|未证实|时钟(?:未|差|缺失|对齐)|口径(?:未|不|差)|来源(?:不|缺|未)|"
        r"资料缺失|不可观测|尚难区分|未提供|OBSERVED_UNALIGNED|UNKNOWN",
        text,
    ))
    has_grounded_hold_feed = bool(re.search(
        r"短腿外侧|剩余余量|余量(?:收缩|消耗|缩至|演变)|路径演变|"
        r"(?:4h|12h|4小时|12小时)[^。；\n]*(?:现货|主动流|CVD|动量)|现货主动流|CVD|"
        r"(?:同口径)?(?:本期)?\s*Pin|ARD(?:迁移|邻域|离返)?",
        text,
    ))
    explicit_degenerate = bool(
        re.search(r"只要求未确认进入(?:责任区|短腿)?|只要未(?:进入|触发)[^。；\n]*(?:就|即|均)(?:继续)?(?:承保|持有)", text)
        or (
            re.search(r"直至机械破坏前不得出现[^。；\n]*(?:越过|进入)[^。；\n]*(?:分钟采样|触发)", text)
            and not has_independent_falsifier
        )
    )
    unconditional_c_degenerate = bool(
        _C_DEGENERATE_RE.search(text)
        or (
            re.search(r"不接受[^。；\n]*(?:越过|进入)[^。；\n]*(?:短腿|责任区)", text)
            and re.search(r"按(?:破坏|机械)?规则处理|触发破坏|机械破坏", text)
        )
        or (
            not has_borne_adverse
            and not has_independent_falsifier
            and not has_uncertainty_disclosure
            and not has_grounded_hold_feed
            and re.search(r"短腿|责任区|破坏|new_entry|entry_not_returned", text)
        )
    )
    pure_until_m = bool(
        any(re.search(r"^(?:只要|只要求|直至|仅当).*(?:未|不).*(?:进入|触发)", cl) for cl in clauses)
        and not has_independent_falsifier
    )
    c_degenerates = bool(explicit_degenerate or unconditional_c_degenerate or pure_until_m)
    post_m_only_phrase = bool(re.search(
        r"(?:越过短腿后|入区后|进入责任区后|触发\s*M\s*后|触及保护腿后|跌破保护腿|突破保护腿)[^。；\n]*(?:复核|重审|证伪|撤销|条件)",
        text,
    ))
    has_post_m_only_trigger_risk = bool(c_degenerates or (post_m_only_phrase and not has_independent_falsifier))
    return {
        "empty": False,
        "c_degenerates_to_m": c_degenerates,
        "has_borne_adverse": has_borne_adverse,
        "has_independent_falsifier": has_independent_falsifier,
        "has_uncertainty_disclosure": has_uncertainty_disclosure,
        "has_grounded_hold_feed": has_grounded_hold_feed,
        "has_post_m_only_trigger_risk": has_post_m_only_trigger_risk,
    }


def _norm_review_excerpt(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    norm_fn = getattr(dossier, "_norm_excerpt", None)
    if callable(norm_fn):
        return str(norm_fn(value) or "")
    return re.sub(r"[\s,.:;，。：；、()（）\[\]【】`*~\"'“”‘’]+", "", value).strip()


def _strip_section_label_prefix(norm_text: str) -> str:
    return re.sub(
        r"^(?:Put|Call|两侧|双侧|对比|比较|历史事实|冻结历史事实|承担条件|持续条件|持续承担条件|破坏|机械|有效|ARD)[^:：]{0,20}[:：]",
        "",
        norm_text,
        flags=re.IGNORECASE,
    ).strip()


def _expand_unambiguous_labeled_excerpt(
    raw_val: Any,
    public_text: str,
    *,
    expected_section: str,
    sections: Mapping[str, Mapping[str, str]],
) -> tuple[str, dict[str, Any] | None]:
    """Losslessly expand a verbatim sentence/clause-boundary prefix inside an unambiguous labeled section.

    Preserves `public_text` (`response.raw`) byte-for-byte, never alters polarity/side/strikes,
    and refuses mid-word/mid-phrase truncations (e.g. `truncate_comparison`), wrong-section pointers,
    or non-verbatim strings.
    """
    if not isinstance(raw_val, str) or not raw_val.strip():
        return raw_val, None
    ex_s = raw_val.strip()
    if not dossier.excerpt_in_text(ex_s, public_text):
        return raw_val, None
    chk = dossier.inspect_excerpt_integrity(ex_s, public_text, expected_section=expected_section)
    if chk.get("complete") is True or chk.get("matched") is not True or chk.get("section") != expected_section:
        return raw_val, None
    sec = sections.get(expected_section)
    if not isinstance(sec, Mapping):
        return raw_val, None
    norm_ex = _norm_review_excerpt(ex_s)
    if len(norm_ex) < 20:
        return raw_val, None
    target_raw = str(sec.get("raw_full") or sec.get("full") or "")
    if not target_raw or target_raw not in public_text:
        return raw_val, None
    idx = target_raw.find(ex_s)
    if idx < 0:
        return raw_val, None
    if idx > 0 and not re.fullmatch(r"[\s\-*•#>]*[^：:\n]{1,32}[：:]\s*", target_raw[:idx]):
        return raw_val, None
    rest = target_raw[idx + len(ex_s):]
    if not rest.strip():
        return raw_val, None
    ends_sentence = bool(dossier._TERMINAL_END_RE.search(ex_s)) or ex_s.endswith(("；", ";"))
    stops_at_clause_delim = rest[0] in {"，", ",", "；", ";", "。", "！", "？"}
    has_trailing_connector = bool(re.search(r"[,，、:：(\[（\-—–]$", ex_s))
    balanced_delims = (
        ex_s.count("(") == ex_s.count(")")
        and ex_s.count("（") == ex_s.count("）")
        and ex_s.count("[") == ex_s.count("]")
        and ex_s.count("【") == ex_s.count("】")
        and ex_s.count("“") == ex_s.count("”")
        and ex_s.count('"') % 2 == 0
    )
    if not ((ends_sentence or stops_at_clause_delim) and not has_trailing_connector and balanced_delims):
        return raw_val, None
    expanded = target_raw[idx:].strip()
    if expanded not in public_text:
        return raw_val, None
    chk_after = dossier.inspect_excerpt_integrity(expanded, public_text, expected_section=expected_section)
    if chk_after.get("complete") is not True or chk_after.get("section") not in (None, expected_section):
        return raw_val, None
    return expanded, {
        "kind": "j1_labeled_section_clause_prefix_expanded",
        "field": expected_section,
        "original_status": chk.get("status"),
        "original_coverage_ratio": chk.get("coverage_ratio"),
    }


def is_recoverable_citation_contract_failure(contract_validation: Any) -> bool:
    """Return True iff `CONTRACT_FAILED` is solely a J1 citation-pointer / duplicate / span flaw on verbatim text."""
    if not isinstance(contract_validation, Mapping):
        return False
    if contract_validation.get("status") != "CONTRACT_FAILED":
        return False
    if contract_validation.get("audit_scope") not in {"NON_ACCEPT_J1_ONLY", "ACCEPT_FULL_CONTRACT"}:
        return False
    explicit = contract_validation.get("recoverable_citation_only")
    if isinstance(explicit, bool):
        return explicit
    layer1 = contract_validation.get("layer1_deterministic")
    if not isinstance(layer1, Mapping):
        return False
    blocking = [str(e) for e in (layer1.get("blocking_errors") or ()) if e]
    if not blocking:
        return False
    excerpt_checks = layer1.get("excerpt_checks") if isinstance(layer1.get("excerpt_checks"), Mapping) else {}
    j1_prefixes = ("j1_excerpt_incomplete:", "j1_excerpt_wrong_section:", "j1_excerpt_duplicate_section:")
    if not any(err.startswith(j1_prefixes) for err in blocking):
        return False
    for err in blocking:
        if not err.startswith(j1_prefixes):
            return False
        parts = err.split(":")
        field_name = parts[1] if len(parts) >= 2 else ""
        chk = excerpt_checks.get(field_name) if isinstance(excerpt_checks, Mapping) else None
        if isinstance(chk, Mapping) and chk.get("status") in {"NOT_IN_TEXT", "EMPTY"}:
            return False
    return True


def _compute_recoverable_citation_only(
    *,
    raw_text: str,
    blocking_errors: Sequence[str],
    excerpt_checks: Mapping[str, Any],
    contract: Mapping[str, Any] | None,
    m1_kwargs: Mapping[str, Any] | None,
) -> bool:
    if not blocking_errors:
        return False
    j1_prefixes = ("j1_excerpt_incomplete:", "j1_excerpt_wrong_section:", "j1_excerpt_duplicate_section:")
    if not any(err.startswith(j1_prefixes) for err in blocking_errors):
        return False
    conf_pointer_err = any(
        err.startswith((
            "j1_excerpt_wrong_section:condition_plan.confirmation_excerpt:",
            "j1_excerpt_duplicate_section:condition_plan.confirmation_excerpt",
        ))
        or err == "j1_excerpt_incomplete:condition_plan.confirmation_excerpt:WRONG_SECTION"
        for err in blocking_errors
    )
    sections_cache: Mapping[str, Any] | None = None
    for err in blocking_errors:
        if err.startswith(j1_prefixes):
            parts = err.split(":")
            field_name = parts[1] if len(parts) >= 2 else ""
            chk = excerpt_checks.get(field_name) if isinstance(excerpt_checks, Mapping) else None
            if isinstance(chk, Mapping) and chk.get("status") in {"NOT_IN_TEXT", "EMPTY"}:
                return False
            continue
        if conf_pointer_err and err.startswith("m1_contract_violation:") and contract is not None and m1_kwargs is not None:
            if sections_cache is None:
                sections_cache = dossier.extract_labeled_sections(raw_text)
            conf_sec = sections_cache.get("confirmation_excerpt") if isinstance(sections_cache, Mapping) else None
            if not isinstance(conf_sec, Mapping):
                return False
            cand_m_text = str(conf_sec.get("raw_full") or conf_sec.get("body") or "")
            if not cand_m_text:
                return False
            if not dossier.condition.validate_confirmation_prose(cand_m_text, contract, **m1_kwargs).get("valid"):
                return False
            continue
        return False
    return True


def validate_initial_reply_contract(
    parsed_reply: Mapping[str, Any],
    contracts: Any = None,
    *,
    strict_clock_5m: bool = False,
    check_excerpt_completeness: bool = True,
    require_outside_at_t0: bool = False,
) -> dict[str, Any]:
    """Validate J1 excerpt integrity, M1 mechanical compatibility, and Layer-2 non-blocking C audit."""
    raw_text = str(parsed_reply.get("raw_text") or "")
    decision = parsed_reply.get("decision") or {}
    plan = parsed_reply.get("condition_plan")
    action = str(decision.get("action") or "")
    side = str(decision.get("side") or "").lower()
    mech_ver = getattr(dossier.condition, "MECHANICAL_CONTRACT_VERSION", "p2_strict@1")
    lossless_repairs = [
        dict(r) if isinstance(r, Mapping) else {"kind": str(r)}
        for r in (parsed_reply.get("lossless_repairs") or ())
    ]
    warnings: list[str] = [
        f"lossless_repair:{r.get('kind')}:{r.get('field')}" if r.get("field") else f"lossless_repair:{r.get('kind')}"
        for r in lossless_repairs
    ]

    excerpt_checks: dict[str, Any] = {}
    blocking_errors: list[str] = []
    if check_excerpt_completeness and raw_text:
        for key in ("put", "call", "comparison", "put_counter", "call_counter"):
            chk = dossier.inspect_excerpt_integrity(decision.get(key), raw_text, expected_section=key)
            excerpt_checks[key] = chk
            if not chk["complete"]:
                blocking_errors.append(f"j1_excerpt_incomplete:{key}:{chk['status']}")
            elif chk.get("section") not in (None, key):
                blocking_errors.append(f"j1_excerpt_wrong_section:{key}:{chk.get('section')}")

    if action != "Accept":
        rec_cit = _compute_recoverable_citation_only(
            raw_text=raw_text,
            blocking_errors=blocking_errors,
            excerpt_checks=excerpt_checks,
            contract=None,
            m1_kwargs=None,
        )
        return {
            "status": "CONTRACT_FAILED" if blocking_errors else "PASSED",
            "audit_scope": "NON_ACCEPT_J1_ONLY",
            "mechanical_contract_version": mech_ver,
            "recoverable_citation_only": rec_cit,
            "warnings": warnings,
            "lossless_repairs": lossless_repairs,
            "layer1_deterministic": {
                "valid": not blocking_errors,
                "blocking_errors": blocking_errors,
                "excerpt_checks": excerpt_checks,
            },
            "layer2_semantic_risk": {},
            "c_audit": {},
            "canonical_confirmation": None,
        }

    if not isinstance(plan, Mapping):
        blocking_errors.append("missing_condition_plan")
        return {
            "status": "CONTRACT_FAILED",
            "audit_scope": "ACCEPT_FULL_CONTRACT",
            "mechanical_contract_version": mech_ver,
            "recoverable_citation_only": False,
            "warnings": warnings,
            "lossless_repairs": lossless_repairs,
            "layer1_deterministic": {
                "valid": False,
                "blocking_errors": blocking_errors,
                "excerpt_checks": excerpt_checks,
            },
            "layer2_semantic_risk": {},
            "c_audit": {},
            "canonical_confirmation": None,
        }

    if check_excerpt_completeness and raw_text:
        for key in ("historical_facts", "accepted_conditions", "confirmation_excerpt"):
            chk = dossier.inspect_excerpt_integrity(plan.get(key), raw_text, expected_section=key)
            excerpt_checks[f"condition_plan.{key}"] = chk
            if not chk["complete"]:
                blocking_errors.append(f"j1_excerpt_incomplete:condition_plan.{key}:{chk['status']}")
            elif chk.get("section") not in (None, key):
                blocking_errors.append(f"j1_excerpt_wrong_section:condition_plan.{key}:{chk.get('section')}")
        if decision.get("depends_on_ard") is True:
            for key in ("ard_responsibility_meaning", "ard_review_on"):
                val = plan.get(key)
                if not isinstance(val, str) or not val.strip():
                    blocking_errors.append(f"ard_condition_excerpt_missing:{key}")
                else:
                    chk = dossier.inspect_excerpt_integrity(val, raw_text, expected_section=key)
                    excerpt_checks[f"condition_plan.{key}"] = chk
                    if not chk["complete"]:
                        blocking_errors.append(f"j1_excerpt_incomplete:condition_plan.{key}:{chk['status']}")
                    elif chk.get("section") not in (None, key, "ard", "ard_responsibility_meaning", "ard_review_on"):
                        blocking_errors.append(f"j1_excerpt_wrong_section:condition_plan.{key}:{chk.get('section')}")

    if check_excerpt_completeness:
        non_m_named = [
            *((k, decision.get(k)) for k in ("put", "call", "comparison", "put_counter", "call_counter")),
            *((f"condition_plan.{k}", plan.get(k)) for k in ("historical_facts", "accepted_conditions")),
        ]
        for idx_a in range(len(non_m_named)):
            name_a, val_a = non_m_named[idx_a]
            norm_a = _strip_section_label_prefix(_norm_review_excerpt(val_a))
            if len(norm_a) < 24:
                continue
            for idx_b in range(idx_a + 1, len(non_m_named)):
                name_b, val_b = non_m_named[idx_b]
                norm_b = _strip_section_label_prefix(_norm_review_excerpt(val_b))
                if len(norm_b) < 24:
                    continue
                if norm_a == norm_b or norm_a in norm_b or norm_b in norm_a:
                    warnings.append(f"j1_non_critical_duplicate_excerpt:{name_a}:{name_b}")
        conf_raw = plan.get("confirmation_excerpt")
        norm_conf = _norm_review_excerpt(conf_raw)
        if norm_conf:
            conf_body = _strip_section_label_prefix(norm_conf)
            non_m_candidates = [val for _, val in non_m_named]
            is_dup = bool(
                re.match(r"^(?:两侧|双侧)?(?:对比|比较)(?:与)?(?:择优|结论)\s*[：:]", str(conf_raw or "").strip())
            )
            if not is_dup:
                for other_val in non_m_candidates:
                    norm_other = _norm_review_excerpt(other_val)
                    if not norm_other:
                        continue
                    other_body = _strip_section_label_prefix(norm_other)
                    if (
                        norm_conf == norm_other
                        or (conf_body and other_body and conf_body == other_body)
                        or (len(norm_other) >= 16 and norm_other in norm_conf)
                        or (len(norm_conf) >= 16 and norm_conf in norm_other)
                    ):
                        is_dup = True
                        break
            if is_dup:
                blocking_errors.append("j1_excerpt_duplicate_section:condition_plan.confirmation_excerpt")

    if strict_clock_5m and plan.get("check_interval_ms") != 300_000:
        blocking_errors.append(f"clock_interval_not_5m:{plan.get('check_interval_ms')}")
    if require_outside_at_t0:
        if "require_outside_at_t0" not in plan or plan.get("require_outside_at_t0") is None:
            blocking_errors.append("t0_outside_guard_required:omitted")
        elif plan.get("require_outside_at_t0") is not True:
            blocking_errors.append(f"t0_outside_guard_required:{plan.get('require_outside_at_t0')}")

    if plan.get("invalidation") not in dossier.condition.RULES:
        blocking_errors.append(f"unsupported_invalidation:{plan.get('invalidation')!r}")

    contract = _resolve_contract_from_pair(contracts, side)
    if contract is None:
        return {
            "status": "UNEVALUABLE_MISSING_CONTRACT",
            "audit_scope": "ACCEPT_FULL_CONTRACT",
            "mechanical_contract_version": mech_ver,
            "recoverable_citation_only": False,
            "warnings": warnings,
            "lossless_repairs": lossless_repairs,
            "layer1_deterministic": {
                "valid": False,
                "blocking_errors": [*blocking_errors, "missing_valid_contract"],
                "excerpt_checks": excerpt_checks,
            },
            "layer2_semantic_risk": {},
            "c_audit": {},
            "canonical_confirmation": None,
        }

    val_conf_fn = dossier.condition.validate_confirmation_prose
    m1_kwargs: dict[str, Any] = {
        "expected_invalidation": plan.get("invalidation"),
        "expected_interval_ms": (300_000 if strict_clock_5m else plan.get("check_interval_ms")),
    }
    try:
        if "require_positive_m" in inspect.signature(val_conf_fn).parameters:
            m1_kwargs["require_positive_m"] = True
    except (ValueError, TypeError):
        pass
    m1_res = val_conf_fn(
        plan.get("confirmation_excerpt"),
        contract,
        **m1_kwargs,
    )
    if not m1_res["valid"]:
        for v in m1_res["violations"]:
            blocking_errors.append(f"m1_contract_violation:{v}")

    # Spread geometric band mentions inside C (accepted_conditions) are advisory only; never Layer-1 blocking.
    c_m_check = dossier.condition.validate_confirmation_prose(
        plan.get("accepted_conditions"),
        contract,
        expected_invalidation=plan.get("invalidation"),
        expected_interval_ms=None,
    )
    c1_mech_adv = [
        v for v in c_m_check["violations"]
        if v in ("excludes_protection_touch", "excludes_beyond_protection", "restricts_entry_to_finite_interval")
    ]

    c_audit = inspect_accepted_conditions_semantics(str(plan.get("accepted_conditions") or ""))
    canonical_m = (
        dossier.condition.canonical_confirmation_text(
            contract,
            plan["invalidation"],
            plan.get("check_interval_ms") or 300_000,
        )
        if plan.get("invalidation") in dossier.condition.RULES
        and isinstance(plan.get("check_interval_ms"), int)
        and not isinstance(plan.get("check_interval_ms"), bool)
        and plan["check_interval_ms"] >= dossier.MINUTE
        and plan["check_interval_ms"] % dossier.MINUTE == 0
        else None
    )
    rec_cit = _compute_recoverable_citation_only(
        raw_text=raw_text,
        blocking_errors=blocking_errors,
        excerpt_checks=excerpt_checks,
        contract=contract,
        m1_kwargs=m1_kwargs,
    )
    return {
        "status": "CONTRACT_FAILED" if blocking_errors else "PASSED",
        "audit_scope": "ACCEPT_FULL_CONTRACT",
        "mechanical_contract_version": mech_ver,
        "recoverable_citation_only": rec_cit,
        "warnings": warnings,
        "lossless_repairs": lossless_repairs,
        "layer1_deterministic": {
            "valid": not blocking_errors,
            "blocking_errors": blocking_errors,
            "m1_prose_check": m1_res,
            "excerpt_checks": excerpt_checks,
        },
        "layer2_semantic_risk": {
            "c1_degenerates_to_m": c_audit["c_degenerates_to_m"],
            "c1_has_borne_adverse": c_audit["has_borne_adverse"],
            "c1_has_independent_falsifier": c_audit["has_independent_falsifier"],
            "c1_has_uncertainty_disclosure": c_audit["has_uncertainty_disclosure"],
            "c1_has_grounded_hold_feed": c_audit["has_grounded_hold_feed"],
            "c1_has_post_m_only_trigger_risk": c_audit["has_post_m_only_trigger_risk"],
            "c1_mechanical_advisory": c1_mech_adv,
        },
        "c_audit": c_audit,
        "canonical_confirmation": canonical_m,
    }


def _resolve_reply_prompt_version(req_payload: Mapping[str, Any]) -> str:
    raw_prompt_ver = req_payload.get("prompt_version")
    sys_msg = None
    messages = req_payload.get("messages")
    if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)) and messages:
        first_msg = messages[0]
        if isinstance(first_msg, Mapping) and first_msg.get("role") == "system":
            content = first_msg.get("content")
            if isinstance(content, str):
                sys_msg = content
    p0_profile = getattr(dossier.player, "INPUT_PROFILE_P0_STRICT", "p0_strict@1")
    req_profile = req_payload.get("input_profile")

    resolved_pv: str | None = None
    if raw_prompt_ver is not None:
        if not isinstance(raw_prompt_ver, str) or raw_prompt_ver not in INITIAL_SYSTEM_PROMPTS:
            report = {
                "status": "CONTRACT_FAILED",
                "audit_scope": "PROMPT_VERSION_GATE",
                "layer1_deterministic": {
                    "valid": False,
                    "blocking_errors": [f"unknown_prompt_version:{raw_prompt_ver!r}"],
                    "excerpt_checks": {},
                },
                "layer2_semantic_risk": {},
                "c_audit": {},
                "canonical_confirmation": None,
            }
            raise ContractValidationError(
                f"unknown_prompt_version: {raw_prompt_ver!r}",
                parsed_reply={"request_payload": dict(req_payload)},
                contract_validation=report,
            )
        if sys_msg is not None and sys_msg.strip() != INITIAL_SYSTEM_PROMPTS[raw_prompt_ver].strip():
            report = {
                "status": "CONTRACT_FAILED",
                "audit_scope": "PROMPT_VERSION_GATE",
                "layer1_deterministic": {
                    "valid": False,
                    "blocking_errors": ["prompt_version_system_mismatch"],
                    "excerpt_checks": {},
                },
                "layer2_semantic_risk": {},
                "c_audit": {},
                "canonical_confirmation": None,
            }
            raise ContractValidationError(
                "prompt_version_system_mismatch",
                parsed_reply={"request_payload": dict(req_payload)},
                contract_validation=report,
            )
        resolved_pv = raw_prompt_ver
    elif sys_msg is not None and sys_msg.strip() == INITIAL_SYSTEM_PROMPT.strip() and req_profile != p0_profile:
        # prompt_version is omitted: only legacy @1.0 exact system-message match (not under p0_strict@1) is allowed.
        resolved_pv = INITIAL_SYSTEM_PROMPT_VERSION
    else:
        report = {
            "status": "CONTRACT_FAILED",
            "audit_scope": "PROMPT_VERSION_GATE",
            "layer1_deterministic": {
                "valid": False,
                "blocking_errors": ["missing_prompt_version"],
                "excerpt_checks": {},
            },
            "layer2_semantic_risk": {},
            "c_audit": {},
            "canonical_confirmation": None,
        }
        raise ContractValidationError(
            "missing_prompt_version",
            parsed_reply={"request_payload": dict(req_payload)},
            contract_validation=report,
        )

    exp_pv = req_payload.get("expected_prompt_version")
    if exp_pv is not None and resolved_pv != exp_pv:
        report = {
            "status": "CONTRACT_FAILED",
            "audit_scope": "PROMPT_VERSION_GATE",
            "layer1_deterministic": {
                "valid": False,
                "blocking_errors": ["prompt_version_mismatch"],
                "excerpt_checks": {},
            },
            "layer2_semantic_risk": {},
            "c_audit": {},
            "canonical_confirmation": None,
        }
        raise ContractValidationError(
            f"prompt_version_mismatch: expected {exp_pv!r}, got {resolved_pv!r}",
            parsed_reply={"request_payload": dict(req_payload)},
            contract_validation=report,
        )
    return resolved_pv


def parse_initial_reply(
    llm_result: Mapping[str, Any],
    req_payload: dict[str, Any],
    *,
    strict_contract: bool | None = None,
    contracts: Any = None,
    strict_clock_5m: bool | None = None,
) -> dict[str, Any]:
    req_payload["endpoint"] = llm_result.get("endpoint") or req_payload.get("endpoint")
    prompt_ver = _resolve_reply_prompt_version(req_payload)
    is_candidate_prompt = prompt_ver in CANDIDATE_STRICT_PROMPT_VERSIONS
    # Candidate strictness is UNCONDITIONAL: caller cannot bypass with strict_contract=False or strict_clock_5m=False.
    if is_candidate_prompt:
        effective_strict = True
        effective_clock_5m = True
        must_require_outside = True
    else:
        effective_strict = bool(strict_contract) if strict_contract is not None else False
        effective_clock_5m = bool(strict_clock_5m) if strict_clock_5m is not None else False
        must_require_outside = effective_strict
    eff_contracts = contracts if contracts is not None else (req_payload.get("metadata") or {}).get("contract_pair")

    public_text, payload, format_repairs = _split_tagged_json_with_repairs(llm_result["text"], "ANNOTATION_JSON")
    if not public_text.strip():
        raise ValueError("empty public decision text")
    raw_bytes = (public_text + "\n").encode("utf-8")
    lossless_repairs: list[dict[str, Any]] = [{"kind": kind} for kind in format_repairs]
    labeled_sections = dossier.extract_labeled_sections(public_text) if effective_strict else {}

    excerpt_keys = ("put", "call", "comparison", "put_counter", "call_counter")
    annotation: dict[str, Any] = {"depends_on_ard": payload.get("depends_on_ard")}
    for k in excerpt_keys:
        val_k = payload.get(k)
        if effective_strict:
            val_k, rep_k = _expand_unambiguous_labeled_excerpt(
                val_k, public_text, expected_section=k, sections=labeled_sections,
            )
            if rep_k is not None:
                lossless_repairs.append(rep_k)
        annotation[k] = val_k

    def _raise_strict_contract_precheck(
        err_code: str,
        message: str,
        *,
        audit_scope: str,
        decision_obj: dict[str, Any] | None = None,
        plan_obj: dict[str, Any] | None = None,
    ) -> None:
        if effective_strict:
            report = {
                "status": "CONTRACT_FAILED",
                "audit_scope": audit_scope,
                "mechanical_contract_version": getattr(dossier.condition, "MECHANICAL_CONTRACT_VERSION", "p2_strict@1"),
                "layer1_deterministic": {
                    "valid": False,
                    "blocking_errors": [err_code],
                    "excerpt_checks": {},
                },
                "layer2_semantic_risk": {},
                "c_audit": {},
                "canonical_confirmation": None,
            }
            raise ContractValidationError(
                message,
                parsed_reply={
                    "raw_bytes": raw_bytes,
                    "raw_text": public_text,
                    "annotation": annotation,
                    "condition_plan": plan_obj,
                    "decision": decision_obj,
                    "request_payload": req_payload,
                },
                contract_validation=report,
            )
        raise ValueError(message)

    try:
        decision = dossier.parse(raw_bytes, annotation)
    except ValueError as parse_exc:
        msg = str(parse_exc)
        if "heading" in msg or "Put/Call" in msg:
            code = "invalid_or_ambiguous_decision_heading"
        elif "ARD dependence" in msg:
            code = "invalid_depends_on_ard_type"
        else:
            code = "j1_annotation_excerpt_invalid"
        _raise_strict_contract_precheck(code, msg, audit_scope="INITIAL_ANNOTATION_PRECHECK")

    parsed_plan: dict[str, Any] | None = None
    if decision["action"] == "Accept":
        raw_plan = payload.get("condition_plan")
        if not isinstance(raw_plan, Mapping):
            _raise_strict_contract_precheck(
                "missing_condition_plan",
                "Accept decision requires condition_plan",
                audit_scope="ACCEPT_FULL_CONTRACT",
                decision_obj=decision,
            )
        invalidation = raw_plan.get("invalidation")
        if invalidation not in dossier.condition.RULES:
            _raise_strict_contract_precheck(
                f"unsupported_invalidation:{invalidation}",
                "unsupported condition_plan.invalidation",
                audit_scope="ACCEPT_FULL_CONTRACT",
                decision_obj=decision,
            )
        interval = raw_plan.get("check_interval_ms", 300_000)
        if isinstance(interval, bool) or not isinstance(interval, int) or not 0 < interval <= dossier.DAY or interval % dossier.MINUTE:
            _raise_strict_contract_precheck(
                f"invalid_check_interval_ms:{interval}",
                "condition_plan.check_interval_ms must be positive minute-aligned int",
                audit_scope="ACCEPT_FULL_CONTRACT",
                decision_obj=decision,
            )
        parsed_plan = {
            "invalidation": invalidation,
            "check_interval_ms": interval,
        }
        if "require_outside_at_t0" in raw_plan:
            require_outside = raw_plan["require_outside_at_t0"]
            if not isinstance(require_outside, bool):
                _raise_strict_contract_precheck(
                    f"invalid_require_outside_at_t0:{require_outside!r}",
                    "condition_plan.require_outside_at_t0 must be boolean",
                    audit_scope="ACCEPT_FULL_CONTRACT",
                    decision_obj=decision,
                    plan_obj=parsed_plan,
                )
            parsed_plan["require_outside_at_t0"] = require_outside
        elif prompt_ver in CANDIDATE_STRICT_PROMPT_VERSIONS and prompt_ver not in P5_STRICT_PROMPT_VERSIONS:
            # @1.1-candidate prompt declares require_outside_at_t0 optional; fail-closed default to True.
            parsed_plan["require_outside_at_t0"] = True
        elif not must_require_outside:
            parsed_plan["require_outside_at_t0"] = False
        # When prompt_ver is in P5_STRICT_PROMPT_VERSIONS and require_outside_at_t0 was omitted,
        # keep it omitted so validate_initial_reply_contract fails closed with t0_outside_guard_required:omitted.

        required_excerpts = ["historical_facts", "accepted_conditions", "confirmation_excerpt"]
        if decision["depends_on_ard"]:
            required_excerpts.extend(["ard_responsibility_meaning", "ard_review_on"])
        for key in required_excerpts:
            val = raw_plan.get(key)
            if not dossier.excerpt_in_text(val, public_text):
                if effective_strict:
                    # Preserve full context and fail closed via ContractValidationError.
                    err_code = f"ard_condition_excerpt_missing:{key}" if key.startswith("ard_") else f"condition_plan_excerpt_missing:{key}"
                    report = {
                        "status": "CONTRACT_FAILED",
                        "audit_scope": "ACCEPT_FULL_CONTRACT",
                        "mechanical_contract_version": getattr(dossier.condition, "MECHANICAL_CONTRACT_VERSION", "p2_strict@1"),
                        "layer1_deterministic": {
                            "valid": False,
                            "blocking_errors": [err_code],
                            "excerpt_checks": {},
                        },
                        "layer2_semantic_risk": {},
                        "c_audit": {},
                        "canonical_confirmation": None,
                    }
                    raise ContractValidationError(
                        f"condition_plan.{key} must be a verbatim excerpt from public text",
                        parsed_reply={
                            "raw_bytes": raw_bytes,
                            "raw_text": public_text,
                            "annotation": annotation,
                            "condition_plan": parsed_plan,
                            "decision": decision,
                            "request_payload": req_payload,
                        },
                        contract_validation=report,
                    )
                raise ValueError(f"condition_plan.{key} must be a verbatim excerpt from public text")
            val_clean = val.strip()
            if effective_strict and key in {"historical_facts", "accepted_conditions", "confirmation_excerpt"}:
                val_clean, rep_plan_k = _expand_unambiguous_labeled_excerpt(
                    val_clean, public_text, expected_section=key, sections=labeled_sections,
                )
                if rep_plan_k is not None:
                    rep_plan_k["field"] = f"condition_plan.{key}"
                    lossless_repairs.append(rep_plan_k)
            parsed_plan[key] = val_clean

    parsed = {
        "raw_bytes": raw_bytes,
        "raw_text": public_text,
        "reasoning_content": llm_result.get("reasoning_content"),
        "annotation": annotation,
        "condition_plan": parsed_plan,
        "decision": decision,
        "lossless_repairs": lossless_repairs,
        "usage": llm_result.get("usage"),
        "model": llm_result.get("model") or req_payload["model"],
        "http_status": llm_result.get("http_status"),
        "reasoning_effort": llm_result.get("reasoning_effort"),
        "finish_reason": llm_result.get("finish_reason"),
        "request_payload": req_payload,
    }

    if effective_strict:
        contract_report = validate_initial_reply_contract(
            parsed,
            eff_contracts,
            strict_clock_5m=effective_clock_5m,
            check_excerpt_completeness=True,
            require_outside_at_t0=must_require_outside,
        )
        parsed["contract_validation"] = contract_report
        if contract_report["status"] != "PASSED":
            errs = ", ".join(contract_report["layer1_deterministic"]["blocking_errors"])
            raise ContractValidationError(
                f"contract_validation_failed ({contract_report['status']}): {errs}",
                parsed_reply=parsed,
                contract_validation=contract_report,
            )
    return parsed


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


def _extract_last_review_metrics(last_review: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(last_review, Mapping):
        return {}
    sources: list[Mapping[str, Any]] = [last_review]
    for nested_key in ("evidence_snapshot", "metrics", "snapshot", "post_entry_summary", "flow_summary", "ard_summary"):
        nested = last_review.get(nested_key)
        if isinstance(nested, Mapping):
            sources.append(nested)

    def pick_num(*keys: str) -> float | None:
        for src in sources:
            for k in keys:
                val = _num(src.get(k))
                if val is not None:
                    return val
        return None

    def pick_int(*keys: str) -> int | None:
        for src in sources:
            for k in keys:
                val = _int(src.get(k))
                if val is not None:
                    return val
        return None

    def pick_str(*keys: str) -> str:
        for src in sources:
            for k in keys:
                val = src.get(k)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        return ""

    spot_usd = pick_num("last_reviewed_spot_usd", "spot_usd", "latest_spot_usd", "spot_usd_at_review")
    short_distance_pct = pick_num(
        "last_reviewed_short_distance_pct", "short_distance_pct", "latest_short_distance_pct",
    )
    expected_move_usd = pick_num("last_reviewed_expected_move_usd", "expected_move_usd")
    flow_asof_ms = pick_int("last_reviewed_flow_asof_ms", "flow_asof_ms")
    flow_direction = pick_str("last_reviewed_flow_direction", "flow_direction").lower()
    flow_fast_4h = pick_str("flow_fast_4h").lower()
    flow_slow_12h = pick_str("flow_slow_12h").lower()
    flow_cvd_4h = pick_num("last_reviewed_flow_cvd_4h", "flow_cvd_4h")
    flow_cvd_12h = pick_num("last_reviewed_flow_cvd_12h", "flow_cvd_12h")
    flow_mom_4h = pick_num("last_reviewed_flow_momentum_4h_pct", "flow_momentum_4h_pct")
    flow_mom_12h = pick_num("last_reviewed_flow_momentum_12h_pct", "flow_momentum_12h_pct")
    pin = pick_num("last_reviewed_pin", "ard_pin", "new_pin", "pin")
    if pin is None:
        ard_sub = last_review.get("ard_summary")
        if isinstance(ard_sub, Mapping) and isinstance(ard_sub.get("current_anchor"), Mapping):
            pin = _num(ard_sub["current_anchor"].get("pin"))

    depth_step = pick_int("depth_step")
    evaluated_categories: set[str] = set()
    for trig in last_review.get("triggers") or ():
        if not isinstance(trig, Mapping):
            continue
        cat = str(trig.get("category") or "").strip().upper()
        if cat:
            evaluated_categories.add(cat)
        facts = trig.get("facts") if isinstance(trig.get("facts"), Mapping) else {}
        if cat == "B":
            if depth_step is None:
                depth_step = _int(trig.get("depth_step")) or _int(facts.get("depth_step"))
            if short_distance_pct is None:
                short_distance_pct = _num(facts.get("latest_short_distance_pct"))
            if spot_usd is None:
                spot_usd = _num(facts.get("latest_spot_usd"))
        elif cat == "C":
            if pin is None:
                pin = _num(facts.get("new_pin"))
        elif cat == "D":
            if flow_direction == "":
                flow_direction = str(facts.get("flow_direction") or "").strip().lower()
            if flow_asof_ms is None:
                flow_asof_ms = _int(facts.get("flow_asof_ms"))
            if flow_cvd_4h is None:
                flow_cvd_4h = _num(facts.get("flow_cvd_4h"))
            if flow_mom_4h is None:
                flow_mom_4h = _num(facts.get("flow_momentum_4h_pct"))
            if short_distance_pct is None:
                short_distance_pct = _num(facts.get("latest_short_distance_pct"))
            if spot_usd is None:
                spot_usd = _num(facts.get("latest_spot_usd"))

    reviewed_at_ms = pick_int("evidence_asof_ms", "decided_at_ms", "requested_at_ms", "reviewed_at_ms", "at_ms")
    evidence_asof_ms = pick_int("evidence_asof_ms", "series_asof_ms")
    return {
        "reviewed_at_ms": reviewed_at_ms,
        "evidence_asof_ms": evidence_asof_ms,
        "spot_usd": spot_usd,
        "short_distance_pct": short_distance_pct,
        "expected_move_usd": expected_move_usd,
        "flow_asof_ms": flow_asof_ms,
        "flow_window_basis": next((src['flow_window_basis'] for src in sources
                                   if isinstance(src.get('flow_window_basis'), Mapping)), None),
        "flow_qualification_status": pick_str("flow_qualification_status").upper() or None,
        "flow_fast_4h_ready": next((src["flow_fast_4h_ready"] for src in sources if isinstance(src.get("flow_fast_4h_ready"), bool)), None),
        "flow_direction": flow_direction,
        "flow_fast_4h": flow_fast_4h,
        "flow_slow_12h": flow_slow_12h,
        "flow_cvd_4h": flow_cvd_4h,
        "flow_cvd_12h": flow_cvd_12h,
        "flow_momentum_4h_pct": flow_mom_4h,
        "flow_momentum_12h_pct": flow_mom_12h,
        "pin": pin,
        "depth_step": depth_step,
        "evaluated_categories": evaluated_categories,
        "verdict": pick_str("verdict", "ai_management_verdict").upper() or None,
        "protocol_status": pick_str("protocol_status") or None,
        "public_reply": pick_str("public_reply", "summary") or None,
        "key_facts": last_review.get("key_facts"),
    }


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
        result = (latest_trig.get("body") or {}).get("result") if isinstance(latest_trig.get("body"), Mapping) else None
        new_entry_ms = _int(result.get("latest_new_entry_ms")) if isinstance(result, Mapping) else None
        return {
            "category": "A",
            "code": "CONDITION_TRIGGERED",
            "signature": "CONDITION_TRIGGERED",
            "severity": 3,
            "observed_at_ms": obs_ms,
            "evidence_at_ms": new_entry_ms if new_entry_ms and entry_at_ms < new_entry_ms <= obs_ms else None,
            "facts": {
                "event_id": latest_trig.get("event_id"),
                "result": result,
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
    was_outside = (d0 is not None and d0 > 0) or any(
        (_num(m.get("short_distance_pct")) or 0) > 0 for ts, m in all_rows if entry_at_ms < ts < latest_ts)
    if not was_outside:
        return None
    last_outside_at = max(
        (ts for ts, m in all_rows if entry_at_ms < ts < latest_ts
         and (_num(m.get("short_distance_pct")) or 0) > 0),
        default=entry_at_ms,
    )
    entry_sample_at = next(
        ts for ts, m in all_rows if last_outside_at < ts <= latest_ts
        and (_num(m.get("short_distance_pct")) or 0) < 0
    )
    return {
        "category": "A",
        "code": "BOUNDARY_BREACH",
        "signature": "BOUNDARY_BREACH",
        "severity": 3,
        "observed_at_ms": latest_ts,
        "evidence_at_ms": entry_sample_at,
        "facts": {
            "short_distance_pct": d_now,
            "spot_usd": _num(latest_m.get("spot_usd")),
            "short_strike_usd": short_strike_usd,
        },
        "summary_cn": f"入场后新鲜连续采样越过短腿边界（距短腿 {d_now:+.2f}%）。",
    }


def _detect_category_b(
    *,
    side: str,
    entry_at_ms: int,
    short_strike_usd: float,
    t0: Mapping[str, Any],
    fresh_run: Sequence[tuple[int, Mapping[str, Any]]],
    last_rev: Mapping[str, Any] | None = None,
    expected_move_usd: float | None = None,
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

    em_fallback = _num(expected_move_usd)
    em0 = _num(t0.get("expected_move_usd")) or em_fallback
    em_now = _num(latest_m.get("expected_move_usd")) or em_fallback
    spot0, spot_now = _num(t0.get("spot_usd")), _num(latest_m.get("spot_usd"))
    r0 = r_now = None
    em_drop = False
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

    rev = last_rev if isinstance(last_rev, Mapping) else {}
    last_rev_d = _num(rev.get("short_distance_pct"))
    last_rev_spot = _num(rev.get("spot_usd"))
    last_rev_step = _int(rev.get("depth_step"))
    evaluated_b = "B" in (rev.get("evaluated_categories") or set())
    if evaluated_b or last_rev_d is not None:
        if last_rev_d is not None:
            if d_now >= last_rev_d:
                return None
            prev_step = last_rev_step
            if prev_step is None and last_rev_d > 0:
                prev_ratio = min(1.0, last_rev_d / d0)
                prev_step = max(1, int(math.floor((1.0 - prev_ratio) * 10))) if last_rev_d < d0 else 0
            step_jump = prev_step is not None and depth_step >= prev_step + 2
            near_zone = d_now <= 0.35 * last_rev_d and d_now < last_rev_d
            if not (step_jump or near_zone):
                return None
        elif last_rev_step is not None and depth_step < last_rev_step + 2:
            return None

    t0_dollar_margin = round((spot0 - short_strike_usd) if side == "put" else (short_strike_usd - spot0), 2) if spot0 is not None else None
    latest_dollar_margin = round((spot_now - short_strike_usd) if side == "put" else (short_strike_usd - spot_now), 2) if spot_now is not None else None

    return {
        "category": "B",
        "code": "MARGIN_DETERIORATION",
        "signature": "MARGIN_DETERIORATION",
        "depth_step": depth_step,
        "severity": 2,
        "observed_at_ms": latest_ts,
        "facts": {
            "depth_step": depth_step,
            "t0_short_distance_pct": d0,
            "latest_short_distance_pct": d_now,
            "t0_dollar_margin_usd": t0_dollar_margin,
            "latest_dollar_margin_usd": latest_dollar_margin,
            "last_reviewed_short_distance_pct": last_rev_d,
            "last_reviewed_spot_usd": last_rev_spot,
            "t0_expected_move_ratio": r0,
            "latest_expected_move_ratio": r_now,
        },
        "summary_cn": f"短腿安全余量相对入场实质收缩（由 {d0:.2f}% 缩至 {d_now:.2f}%，风险阶梯 step_{depth_step}）。",
    }


def _is_genuine_ard_event(event: Any, *, entry_at_ms: int, after_ms: int | None = None) -> bool:
    if not isinstance(event, Mapping):
        return False
    if event.get("transition") is False:
        return False
    at_ms = _int(event.get("at_ms"))
    if at_ms is not None and at_ms <= entry_at_ms:
        return False
    if after_ms is not None and at_ms is not None and at_ms <= after_ms:
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
    last_rev: Mapping[str, Any] | None = None,
    entry_pin_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(ard_comparison, Mapping):
        return None
    pin_eval_mode = ard_comparison.get("pin_eval_mode")
    if pin_eval_mode in ("STALE", "UNALIGNED", "UNKNOWN"):
        return None
    if "pin_status" in ard_comparison and ard_comparison.get("pin_status") is not None:
        if ard_comparison.get("pin_status") != "ok":
            return None
    if ard_comparison.get("current_sample_available") is False:
        return None

    ard_basis = frozen_condition.get("ard_basis") if isinstance(frozen_condition.get("ard_basis"), Mapping) else None
    depends_on_ard = bool(ard_basis)
    ard_facts = (ard_basis.get("facts") or {}) if ard_basis else {}
    entry_snap = entry_pin_snapshot if isinstance(entry_pin_snapshot, Mapping) else {}

    old_pin = _num(ard_comparison.get("old_pin"))
    if old_pin is None:
        old_pin = _num((ard_comparison.get("frozen_anchor") or {}).get("pin")) if isinstance(ard_comparison.get("frozen_anchor"), Mapping) else None
    if old_pin is None:
        old_pin = _num((ard_facts.get("last") or {}).get("pin")) if isinstance(ard_facts.get("last"), Mapping) else None
    if old_pin is None:
        old_pin = _num(entry_snap.get("pin") if entry_snap.get("pin") is not None else entry_snap.get("old_pin"))
    if old_pin is None and isinstance(entry_snap.get("walls"), Mapping):
        old_pin = _num(entry_snap["walls"].get("pin"))
    if old_pin is None and isinstance(entry_snap.get("anchor"), Mapping):
        old_pin = _num(entry_snap["anchor"].get("pin"))

    new_pin = _num(ard_comparison.get("new_pin"))
    if new_pin is None:
        new_pin = _num((ard_comparison.get("current_anchor") or {}).get("pin")) if isinstance(ard_comparison.get("current_anchor"), Mapping) else None

    same_measure = ard_comparison.get("same_measure")
    position_only_ok = (
        pin_eval_mode == "position_only"
        and ard_comparison.get("source_method_comparable") is True
        and ard_comparison.get("pin_status") == "ok"
        and ard_comparison.get("current_sample_available") is True
    )
    if depends_on_ard:
        if not (same_measure is True or pin_eval_mode == "same_measure_ard" or position_only_ok):
            return None
    else:
        if same_measure is False and not position_only_ok:
            return None
        if same_measure is not True and not position_only_ok:
            if ard_comparison.get("pin_status") != "ok" or old_pin is None or new_pin is None:
                return None
            if ard_comparison.get("method_comparable") is False or ard_comparison.get("source_method_comparable") is False:
                return None
            old_src = ard_comparison.get("old_pin_source") or entry_snap.get("pin_source")
            new_src = ard_comparison.get("new_pin_source") or ard_comparison.get("pin_source")
            if old_src and new_src and old_src != new_src:
                return None
            old_mtd = ard_comparison.get("old_pin_method") or entry_snap.get("pin_method")
            new_mtd = ard_comparison.get("new_pin_method") or ard_comparison.get("pin_method")
            if old_mtd and new_mtd and old_mtd != new_mtd:
                return None

    band_half = _num(ard_comparison.get("band_half"))
    if band_half is None:
        band_half = _num((ard_facts.get("band") or {}).get("half_width_usd")) if isinstance(ard_facts.get("band"), Mapping) else None
    if band_half is None:
        band_half = _num(entry_snap.get("band_half"))
    band_half = band_half or 0.0

    rev = last_rev if isinstance(last_rev, Mapping) else {}
    last_rev_pin = _num(rev.get("pin"))
    last_rev_at = _int(rev.get("reviewed_at_ms"))

    pin_changed_flag = ard_comparison.get("pin_price_changed")
    pin_changed = (pin_changed_flag is True) or (
        (not depends_on_ard or position_only_ok)
        and pin_changed_flag is not False
        and old_pin is not None
        and new_pin is not None
        and new_pin != old_pin
    )

    pin_triggered = False
    old_margin = new_margin = None
    if pin_changed and old_pin is not None and new_pin is not None and new_pin != old_pin:
        threshold = max(250.0, 0.4 * band_half)
        material_shift = abs(new_pin - old_pin) >= threshold
        old_margin = (old_pin - short_strike_usd) if side == "put" else (short_strike_usd - old_pin)
        new_margin = (new_pin - short_strike_usd) if side == "put" else (short_strike_usd - new_pin)
        if depends_on_ard and not position_only_ok:
            closer_or_beyond = (new_margin < old_margin) and (new_margin <= 0 or old_margin > 0)
            pin_triggered = (material_shift or closer_or_beyond) and (last_rev_pin is None or new_pin != last_rev_pin)
        else:
            moved_toward_short = new_margin < old_margin
            near_or_beyond = new_margin <= max(500.0, band_half)
            pin_against_last = True
            if last_rev_pin is not None:
                last_rev_margin = (last_rev_pin - short_strike_usd) if side == "put" else (short_strike_usd - last_rev_pin)
                pin_against_last = abs(new_pin - last_rev_pin) >= threshold and (
                    new_margin < last_rev_margin or near_or_beyond
                )
            if depends_on_ard and position_only_ok:
                pin_triggered = material_shift and moved_toward_short and pin_against_last
            else:
                pin_triggered = material_shift and (moved_toward_short or near_or_beyond) and pin_against_last

    genuine_events = (
        []
        if position_only_ok
        else [
            dict(e) for e in (ard_comparison.get("new_neighborhood_events") or ())
            if _is_genuine_ard_event(e, entry_at_ms=entry_at_ms, after_ms=last_rev_at)
        ]
    )
    if not (pin_triggered or genuine_events):
        return None

    obs_ms = (
        _int(ard_comparison.get("check_at_ms"))
        or _int(ard_comparison.get("observed_at_ms"))
        or max((_int(e.get("at_ms")) or 0 for e in genuine_events), default=0)
        or now_ms
    )
    if pin_triggered:
        sig = "ARD_STRUCTURE_SHIFT:PIN"
        if position_only_ok:
            summary = f"仅位置迁移口径（{ard_comparison.get('pin_lineage') or 'live_5m'}）Pin 发生向短腿实质迁移（{old_pin:,.0f} -> {new_pin:,.0f} USD）。"
        else:
            summary = f"同口径 ARD Pin 发生实质变化（{old_pin:,.0f} -> {new_pin:,.0f} USD）。"
    else:
        last_ev = genuine_events[-1]
        kind = str(last_ev.get("kind") or last_ev.get("type") or last_ev.get("event") or "EVENT").strip()
        sig = f"ARD_STRUCTURE_SHIFT:{kind}"
        summary = f"同口径 ARD 出现邻域穿越/离返事件（{kind}）。"
    return {
        "category": "C",
        "code": "ARD_STRUCTURE_SHIFT",
        "signature": sig,
        "severity": 2,
        "observed_at_ms": obs_ms,
        "evidence_at_ms": obs_ms,
        "facts": {
            "old_pin": old_pin,
            "new_pin": new_pin,
            "last_reviewed_pin": last_rev_pin,
            "band_half": band_half,
            "depends_on_ard": depends_on_ard,
            "pin_eval_mode": pin_eval_mode or ("same_measure_ard" if same_measure is True else None),
            "pin_lineage": ard_comparison.get("pin_lineage"),
            "old_short_margin_usd": old_margin,
            "new_short_margin_usd": new_margin,
            "genuine_events": genuine_events,
        },
        "summary_cn": summary,
    }


def _comparable_disjoint_flow(current: Mapping[str, Any], previous: Mapping[str, Any]) -> bool:
    basis = current.get("flow_window_basis") or {}
    prior = previous.get("flow_window_basis") or {}
    start = _int(basis.get("start_ms"))
    end = _int(previous.get("flow_asof_ms"))
    return (start is not None and end is not None and start > end
            and all(basis.get(key) and basis[key] == prior.get(key) for key in ("source", "symbol")))


def _detect_category_d(
    *,
    side: str,
    short_strike_usd: float,
    entry_at_ms: int,
    t0: Mapping[str, Any],
    all_rows: Sequence[tuple[int, Mapping[str, Any]]],
    fresh_run: Sequence[tuple[int, Mapping[str, Any]]],
    now_ms: int,
    last_rev: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    del all_rows
    if not fresh_run:
        return None
    latest_ts, latest_m = fresh_run[-1]
    if latest_ts <= entry_at_ms:
        return None
    if latest_m.get("flow_ready") is not True:
        return None
    if latest_m.get("flow_fast_4h_ready") is False:
        return None
    if latest_m.get("flow_qualification_status") in ("OBSERVED_UNALIGNED", "STALE", "UNKNOWN"):
        return None

    adverse_dirs = {"bearish", "neutral_to_bearish"} if side == "put" else {"bullish", "neutral_to_bullish"}
    latest_dir = str(latest_m.get("flow_direction") or "").strip().lower()
    if latest_dir not in adverse_dirs:
        return None

    rev = last_rev if isinstance(last_rev, Mapping) else {}
    flow_asof = _int(latest_m.get("flow_asof_ms"))
    t0_flow_asof = _int(t0.get("flow_asof_ms"))
    last_rev_flow_asof = _int(rev.get("flow_asof_ms"))
    last_rev_dir = str(rev.get("flow_direction") or "").strip().lower()
    last_rev_cvd4 = _num(rev.get("flow_cvd_4h"))
    t0_cvd4 = _num(t0.get("flow_cvd_4h"))
    latest_cvd4 = _num(latest_m.get("flow_cvd_4h"))

    if (flow_asof is None or flow_asof > latest_ts or flow_asof <= max(entry_at_ms, last_rev_flow_asof or 0, t0_flow_asof or 0)
            or not 0 <= now_ms - flow_asof <= 60 * 60_000):
        return None

    basis = latest_m.get("flow_window_basis")
    if not isinstance(basis, Mapping):
        return None
    basis_source = evidence_qual.clean_text(basis.get("source"))
    basis_symbol = evidence_qual.clean_text(basis.get("symbol"))
    basis_start = _int(basis.get("start_ms"))
    if (not evidence_qual.is_valid_provenance_token(basis_source)
            or not evidence_qual.is_valid_provenance_token(basis_symbol)
            or basis_start is None or basis_start <= 0 or basis_start > flow_asof):
        return None

    ref_obj = rev if last_rev_flow_asof else t0
    ref_flow_asof = _int(ref_obj.get("flow_asof_ms"))
    ref_qual = str(ref_obj.get("flow_qualification_status") or "").strip().upper()
    ref_basis = ref_obj.get("flow_window_basis") if isinstance(ref_obj.get("flow_window_basis"), Mapping) else None
    if (
        ref_basis is None
        or ref_flow_asof is None
        or ref_qual in ("OBSERVED_UNALIGNED", "STALE", "UNKNOWN")
        or ref_obj.get("flow_fast_4h_ready") is False
    ):
        return None
    if (evidence_qual.clean_text(ref_basis.get("source")) != basis_source
            or evidence_qual.clean_text(ref_basis.get("symbol")) != basis_symbol):
        return None

    t0_dir = str(t0.get("flow_direction") or "").strip().lower()
    m4, m12 = _num(latest_m.get("flow_momentum_4h_pct")), _num(latest_m.get("flow_momentum_12h_pct"))
    t0_m4, t0_m12 = _num(t0.get("flow_momentum_4h_pct")), _num(t0.get("flow_momentum_12h_pct"))
    rev_m4, rev_m12 = _num(rev.get("flow_momentum_4h_pct")), _num(rev.get("flow_momentum_12h_pct"))

    prior_dir = last_rev_dir if last_rev_dir else t0_dir
    ref_m4 = rev_m4 if rev_m4 is not None else t0_m4
    ref_m12 = rev_m12 if rev_m12 is not None else t0_m12
    ref_cvd4 = last_rev_cvd4 if last_rev_cvd4 is not None else t0_cvd4

    if side == "put":
        mom_shift = (m4 is not None and m4 < 0 and (ref_m4 is None or m4 < ref_m4)) or (
            m12 is not None and m12 < 0 and (ref_m12 is None or m12 < ref_m12)
        )
    else:
        mom_shift = (m4 is not None and m4 > 0 and (ref_m4 is None or m4 > ref_m4)) or (
            m12 is not None and m12 > 0 and (ref_m12 is None or m12 > ref_m12)
        )

    is_reversal = (bool(prior_dir) and prior_dir not in adverse_dirs) or (not prior_dir and mom_shift)
    incremental_basis = None
    incremental_cvd = None
    if is_reversal:
        code = "FLOW_REVERSAL"
        mode = "reversal"
        min_shrink = 0.20
    else:
        # Prior state (t0 or last_review) was already in adverse_dirs -> check genuine continuation
        if prior_dir not in adverse_dirs or ref_basis is None:
            return None
        clock_floor = last_rev_flow_asof or t0_flow_asof or entry_at_ms
        ref_start = _int(ref_basis.get("start_ms"))
        if basis_start > clock_floor and latest_cvd4 is not None:
            incremental_cvd = latest_cvd4
            incremental_basis = "disjoint_4h"
        elif ref_start is not None and basis_start == ref_start and ref_cvd4 is not None and (last_rev_flow_asof or t0_flow_asof) is not None and latest_cvd4 is not None:
            incremental_cvd = latest_cvd4 - ref_cvd4
            incremental_basis = "same_start"
        elif (
            evidence_qual.is_valid_provenance_token(basis.get("cum_session_id"))
            and basis.get("cum_session_id") == ref_basis.get("cum_session_id")
            and _int(basis.get("cum_gap_count")) is not None
            and _int(basis.get("cum_gap_count")) == _int(ref_basis.get("cum_gap_count"))
            and _num(basis.get("cum_cvd_btc")) is not None
            and _num(ref_basis.get("cum_cvd_btc")) is not None
            and _int(basis.get("last_bar_index")) is not None
            and _int(ref_basis.get("last_bar_index")) is not None
            and _int(basis.get("last_bar_index")) > _int(ref_basis.get("last_bar_index"))
            and flow_asof > clock_floor
        ):
            incremental_cvd = _num(basis.get("cum_cvd_btc")) - _num(ref_basis.get("cum_cvd_btc"))
            incremental_basis = "exact_closed_bars_post_ref"
        else:
            return None
        if not (incremental_cvd < 0 if side == "put" else incremental_cvd > 0):
            return None
        code = "FLOW_ADVERSE_CONTINUATION"
        mode = "continuation"
        min_shrink = 0.25

    latest_spot = _num(latest_m.get("spot_usd"))
    latest_d = _num(latest_m.get("short_distance_pct"))
    if latest_spot is None or latest_d is None:
        return None

    # Fixed anchor comparison: compare against t0 AND (if reviewed) last_review, taking the more adverse anchor.
    t0_spot, t0_d = _num(t0.get("spot_usd")), _num(t0.get("short_distance_pct"))
    rev_spot, rev_d = _num(rev.get("spot_usd")), _num(rev.get("short_distance_pct"))
    candidates: list[tuple[float, float]] = []
    if t0_spot is not None and t0_d is not None:
        candidates.append((t0_spot, t0_d))
    if rev_spot is not None and rev_d is not None:
        candidates.append((rev_spot, rev_d))
    if not candidates:
        return None

    # The more adverse anchor has the smaller short_distance_pct (closest to short strike).
    anchor_spot, anchor_d = min(candidates, key=lambda pair: pair[1])
    adverse_spot = (latest_spot < anchor_spot) if side == "put" else (latest_spot > anchor_spot)
    coincident_shrink = anchor_d - latest_d
    if not (adverse_spot and coincident_shrink >= min_shrink):
        return None

    non_overlapping_confirmed = (
        is_reversal
        and _comparable_disjoint_flow(latest_m, ref_obj)
        and mom_shift
    )
    if is_reversal:
        incremental_basis = "disjoint_4h" if non_overlapping_confirmed else "rolling_4h"
    if code == "FLOW_REVERSAL":
        summary_text = (
            f"相邻不重叠窗口确认主动流由{prior_dir or '非不利'}反转为{latest_dir}，现价相对锚点向短腿收缩 {coincident_shrink:.2f} 个百分点。"
            if non_overlapping_confirmed
            else f"滚动窗口主动流由{prior_dir or '非不利'}反转为{latest_dir}（未获相邻不重叠确认，仅作复核提醒），现价相对锚点向短腿收缩 {coincident_shrink:.2f} 个百分点。"
        )
    else:
        summary_text = (
            f"已知不利主动流继续同向兑现（{latest_dir}，增量 {incremental_cvd:+.2f} BTC [{incremental_basis}]，新流向时钟 {_utc(flow_asof)}），伴随现价相对锚点继续向短腿实质收缩 {coincident_shrink:.2f} 个百分点。"
        )

    return {
        "category": "D",
        "code": code,
        "signature": code,
        "severity": 2,
        "observed_at_ms": flow_asof or latest_ts,
        "evidence_at_ms": flow_asof,
        "facts": {
            "mode": mode,
            "flow_direction": latest_dir,
            "t0_flow_direction": t0_dir or None,
            "last_reviewed_flow_direction": last_rev_dir or None,
            "flow_asof_ms": flow_asof,
            "flow_window_basis": dict(basis),
            "incremental_basis": incremental_basis,
            "incremental_flow_cvd_btc": incremental_cvd if mode == "continuation" else None,
            "t0_flow_asof_ms": t0_flow_asof,
            "last_reviewed_flow_asof_ms": last_rev_flow_asof,
            "flow_cvd_4h": latest_cvd4,
            "flow_cvd_12h": _num(latest_m.get("flow_cvd_12h")),
            "flow_momentum_4h_pct": m4,
            "flow_momentum_12h_pct": m12,
            "anchor_spot_usd": anchor_spot,
            "anchor_short_distance_pct": anchor_d,
            "latest_spot_usd": latest_spot,
            "latest_short_distance_pct": latest_d,
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
    last_review: Mapping[str, Any] | None = None,
    entry_pin_snapshot: Mapping[str, Any] | None = None,
    expected_move_usd: float | None = None,
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
    last_rev = _extract_last_review_metrics(last_review)

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
            side=norm_side,
            entry_at_ms=entry_at_ms,
            short_strike_usd=strike,
            t0=t0,
            fresh_run=fresh_run,
            last_rev=last_rev,
            expected_move_usd=expected_move_usd,
        ),
        _detect_category_c(
            frozen_condition=frozen_condition,
            side=norm_side,
            short_strike_usd=strike,
            entry_at_ms=entry_at_ms,
            ard_comparison=ard_comparison,
            now_ms=now_ms,
            last_rev=last_rev,
            entry_pin_snapshot=entry_pin_snapshot,
        ),
        _detect_category_d(
            side=norm_side,
            short_strike_usd=strike,
            entry_at_ms=entry_at_ms,
            t0=t0,
            all_rows=indexed_rows,
            fresh_run=fresh_run,
            now_ms=now_ms,
            last_rev=last_rev,
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
    new_evidence = _int(trigger.get("evidence_at_ms"))
    prior_evidence = _int(prev.get("evidence_at_ms")) or _int(prev.get("at_ms"))
    if new_evidence is not None and prior_evidence is not None and new_evidence > prior_evidence:
        return True
    cur_step = _int(trigger.get("depth_step"))
    prev_step = _int(prev.get("depth_step"))
    return cur_step is not None and prev_step is not None and cur_step >= prev_step + 2


def _is_priority_a_trigger(trigger: Mapping[str, Any]) -> bool:
    return (
        isinstance(trigger, Mapping)
        and trigger.get("category") == "A"
        and trigger.get("code") in {"CONDITION_TRIGGERED", "BOUNDARY_BREACH"}
    )


def _record_skip(state: dict[str, Any], code: str, reason_cn: str, now_ms: int) -> None:
    counts = dict(state.get("skip_counts") or {})
    counts[code] = int(counts.get(code) or 0) + 1
    state["skip_counts"] = counts
    state["last_skip"] = {"code": code, "reason_cn": reason_cn, "at_ms": now_ms}


def evaluate_review_gate(
    scheduler_state: Mapping[str, Any] | None,
    pending_triggers: Sequence[Mapping[str, Any]],
    *,
    now_ms: int,
    debounce_ms: int = DEFAULT_DEBOUNCE_MS,
    max_ordinary_reviews: int | None = DEFAULT_MAX_ORDINARY_REVIEWS,
    force: bool = False,
    data_verified: bool = True,
    empty_skip_code: str = "NO_NEW_EVIDENCE",
    empty_skip_reason: str = "无新增实质触发证据。",
) -> dict[str, Any]:
    """Evaluate unified event gate (debounce, cooldown, data verification, and ordinary review soft budget)."""
    state = scheduler_state if isinstance(scheduler_state, Mapping) else {}
    ordinary_count = int(state.get("ordinary_review_count") or 0)
    pending = [t for t in (pending_triggers or ()) if isinstance(t, Mapping)]
    effective_debounce_ms = max(MIN_DEBOUNCE_MS, min(MAX_DEBOUNCE_MS, int(debounce_ms)))

    def deny(code: str, reason_cn: str, *, is_priority_a: bool = False, extreme: bool = False) -> dict[str, Any]:
        return {
            "allowed": False,
            "skip_code": code,
            "reason_cn": reason_cn,
            "is_priority_a": is_priority_a,
            "priority_a_bypass": False,
            "extreme_escalation": extreme,
            "ordinary_review_count": ordinary_count,
            "max_ordinary_reviews": max_ordinary_reviews,
        }

    if state.get("revoked"):
        return deny("NO_NEW_EVIDENCE", "持仓已撤销（revoked=True），不再调度复核。")
    if state.get("inflight"):
        return deny("COALESCED", "已有在途复核请求，新事件已并入待合并队列。")
    if not pending:
        return deny(empty_skip_code, empty_skip_reason)

    priority_a_triggers = [t for t in pending if _is_priority_a_trigger(t)]
    is_priority_a = bool(priority_a_triggers)
    priority_a_bypass = is_priority_a

    backoff_until = _int(state.get("backoff_until_ms"))
    backoff_reason = state.get("backoff_reason")
    circuit_open = bool(state.get("circuit_open"))
    if not force:
        if circuit_open and (backoff_until is None or now_ms < backoff_until):
            return deny("COOLDOWN", "连续失败已触发熔断冷却窗口，暂缓调度。", is_priority_a=is_priority_a)
        if backoff_until is not None and now_ms < backoff_until:
            if not (
                is_priority_a
                and backoff_reason != "DAILY_BUDGET_EXHAUSTED"
                and not circuit_open
            ):
                return deny("COOLDOWN", "处于失败重试退避窗口内，暂缓调度。", is_priority_a=is_priority_a)

    if not data_verified and not is_priority_a and not force:
        return deny("DATA_UNVERIFIED", "关键事实数据未核验，暂缓自动调用模型。", is_priority_a=is_priority_a)

    if not priority_a_bypass and not force:
        cooldown_until = _int(state.get("cooldown_until_ms"))
        if cooldown_until is not None and now_ms < cooldown_until:
            return deny("COOLDOWN", "处于冷却窗口内，暂缓常规复核。", is_priority_a=is_priority_a)
        pending_since = _int(state.get("pending_since_ms"))
        if pending_since is None:
            pending_since = now_ms
        if now_ms - pending_since < effective_debounce_ms:
            return deny("COALESCED", "处于防抖合并窗口内，等待并包。", is_priority_a=is_priority_a)

    has_deep_b = any(t.get("category") == "B" and (_int(t.get("depth_step")) or 0) >= 8 for t in pending)
    distinct_cats = {t.get("category") for t in pending if t.get("category")}
    extreme_escalation = has_deep_b and len(distinct_cats) >= 2

    if (
        not is_priority_a
        and not force
        and max_ordinary_reviews is not None
        and ordinary_count >= int(max_ordinary_reviews)
        and not extreme_escalation
    ):
        return deny(
            "BUDGET_DEFERRED",
            f"常规复核已达软预算上限（{ordinary_count}/{int(max_ordinary_reviews)}），延后自动调用。",
            is_priority_a=False,
            extreme=False,
        )

    return {
        "allowed": True,
        "skip_code": None,
        "reason_cn": None,
        "is_priority_a": is_priority_a,
        "priority_a_bypass": priority_a_bypass,
        "extreme_escalation": extreme_escalation,
        "ordinary_review_count": ordinary_count,
        "max_ordinary_reviews": max_ordinary_reviews,
    }


def schedule_review_action(
    scheduler_state: Mapping[str, Any] | None,
    triggers: Sequence[Mapping[str, Any]],
    *,
    now_ms: int,
    debounce_ms: int = DEFAULT_DEBOUNCE_MS,
    cooldown_ms: int = DEFAULT_COOLDOWN_MS,
    retry_backoff_ms: int = RETRY_BACKOFF_MS,
    stale_inflight_ms: int = STALE_INFLIGHT_MS,
    max_ordinary_reviews: int | None = DEFAULT_MAX_ORDINARY_REVIEWS,
    force: bool = False,
    data_verified: bool = True,
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
        "backoff_reason": raw.get("backoff_reason"),
        "consecutive_failures": int(raw.get("consecutive_failures") or 0),
        "last_failure_kind": raw.get("last_failure_kind"),
        "circuit_open": bool(raw.get("circuit_open", False)),
        "last_review_at_ms": _int(raw.get("last_review_at_ms")),
        "dispatched_signatures": deepcopy(dict(raw.get("dispatched_signatures") or {})),
        "ordinary_review_count": int(raw.get("ordinary_review_count") or 0),
        "skip_counts": deepcopy(dict(raw.get("skip_counts") or {})),
        "last_skip": deepcopy(dict(raw["last_skip"])) if isinstance(raw.get("last_skip"), Mapping) else None,
    }
    if state["revoked"]:
        _record_skip(state, "NO_NEW_EVIDENCE", "持仓已撤销（revoked=True），不再调度复核。", now_ms)
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

    pending_by_key = {_trigger_sig_key(t): idx for idx, t in enumerate(state["pending"])}
    saw_unverified = False
    saw_below_materiality = False
    saw_duplicate = False

    for item in triggers or ():
        if not isinstance(item, Mapping):
            continue
        if item.get("data_unverified") is True and not _is_priority_a_trigger(item) and not force:
            saw_unverified = True
            continue
        if item.get("below_materiality") is True and not _is_priority_a_trigger(item):
            saw_below_materiality = True
            continue
        sig_key = _trigger_sig_key(item)
        prev = dispatched.get(sig_key)
        if isinstance(prev, Mapping) and not _is_escalated(item, prev):
            saw_duplicate = True
            continue
        if sig_key in pending_by_key:
            idx = pending_by_key[sig_key]
            prev_pending = state["pending"][idx]
            cur_step = _int(item.get("depth_step"))
            prev_step = _int(prev_pending.get("depth_step"))
            if _is_escalated(item, prev_pending) or (cur_step is not None and prev_step is not None and cur_step > prev_step):
                state["pending"][idx] = deepcopy(dict(item))
            else:
                saw_duplicate = True
            continue
        pending_by_key[sig_key] = len(state["pending"])
        state["pending"].append(deepcopy(dict(item)))
        if state["pending_since_ms"] is None:
            state["pending_since_ms"] = now_ms
        state["last_trigger_ms"] = max(state["last_trigger_ms"] or now_ms, now_ms)

    empty_skip_code = "NO_NEW_EVIDENCE"
    empty_skip_reason = "无新增实质触发证据。"
    if saw_unverified or (not data_verified and not force and triggers):
        empty_skip_code = "DATA_UNVERIFIED"
        empty_skip_reason = "关键事实数据未核验，暂缓自动调用模型。"
    elif saw_below_materiality:
        empty_skip_code = "BELOW_MATERIALITY"
        empty_skip_reason = "变化未达到实质门槛，跳过复核。"
    elif saw_duplicate:
        empty_skip_code = "DUPLICATE_EVENT"
        empty_skip_reason = "同签名事件已评估且未发生实质升级，去重跳过。"

    gate = evaluate_review_gate(
        state,
        state["pending"],
        now_ms=now_ms,
        debounce_ms=debounce_ms,
        max_ordinary_reviews=max_ordinary_reviews,
        force=force,
        data_verified=data_verified,
        empty_skip_code=empty_skip_code,
        empty_skip_reason=empty_skip_reason,
    )
    if not gate["allowed"]:
        _record_skip(state, str(gate["skip_code"]), str(gate["reason_cn"]), now_ms)
        return state, None

    batch = state["pending"]
    state["inflight_batch"] = deepcopy(batch)
    for item in batch:
        dispatched[_trigger_sig_key(item)] = {
            "severity": int(item.get("severity") or 0),
            "depth_step": _int(item.get("depth_step")),
            "at_ms": now_ms,
            "evidence_at_ms": _int(item.get("evidence_at_ms")),
        }
    state["pending"] = []
    state["pending_since_ms"] = None
    state["inflight"] = True
    state["inflight_started_at_ms"] = now_ms
    state["last_skip"] = None
    action = {
        "should_call": True,
        "triggers": batch,
        "reason": "condition_triggered_priority_a" if gate["priority_a_bypass"] else "coalesced_triggers",
        "is_priority_a": gate["is_priority_a"],
        "is_ordinary_review": not gate["is_priority_a"],
        "ordinary_review_count": state["ordinary_review_count"],
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
    expected_inflight_started_at_ms: int | None = None,
    failure_kind: str | None = None,
    max_consecutive_failures: int = 3,
) -> dict[str, Any]:
    """Record completion or failure of an inflight review call on scheduler state."""
    state = deepcopy(dict(scheduler_state))
    if expected_inflight_started_at_ms is not None:
        cur_started = _int(state.get("inflight_started_at_ms"))
        expected_started = _int(expected_inflight_started_at_ms)
        if not state.get("inflight") or cur_started is None or expected_started is None or cur_started != expected_started:
            return state
    batch = [deepcopy(dict(t)) for t in (state.pop("inflight_batch", None) or []) if isinstance(t, Mapping)]
    state["inflight"] = False
    state["inflight_started_at_ms"] = None
    if success:
        state["last_review_at_ms"] = now_ms
        state["cooldown_until_ms"] = now_ms + int(cooldown_ms)
        state["backoff_until_ms"] = None
        state["backoff_reason"] = None
        state["consecutive_failures"] = 0
        state["last_failure_kind"] = None
        state["circuit_open"] = False
        if not any(_is_priority_a_trigger(t) for t in batch):
            state["ordinary_review_count"] = int(state.get("ordinary_review_count") or 0) + 1
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
        consecutive = int(state.get("consecutive_failures") or 0) + 1
        state["consecutive_failures"] = consecutive
        state["last_failure_kind"] = failure_kind
        threshold = max(1, int(max_consecutive_failures))
        base_backoff_ms = max(0, int(retry_backoff_ms))
        if failure_kind == "DAILY_BUDGET_EXHAUSTED":
            state["backoff_reason"] = "DAILY_BUDGET_EXHAUSTED"
            state["circuit_open"] = bool(state.get("circuit_open", False))
            backoff_ms = base_backoff_ms
        elif consecutive >= threshold:
            state["circuit_open"] = True
            state["backoff_reason"] = "CIRCUIT_OPEN"
            exp = min(6, consecutive - threshold + 1)
            backoff_ms = min(6 * 3_600_000, base_backoff_ms * (2 ** exp))
        else:
            state["circuit_open"] = False
            state["backoff_reason"] = str(failure_kind) if failure_kind else "TRANSIENT_ERROR"
            backoff_ms = base_backoff_ms
        state["backoff_until_ms"] = now_ms + backoff_ms
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


_FLOW_DIR_CN = {
    "bullish": "偏买",
    "neutral_to_bullish": "中性偏买",
    "neutral": "中性",
    "neutral_to_bearish": "中性偏卖",
    "bearish": "偏卖",
}


def _flow_dir_label(value: Any) -> str:
    key = str(value or "").strip().lower()
    if not key:
        return "未提供"
    return _FLOW_DIR_CN.get(key, str(value).strip())


def _is_adverse_flow_dir(side: str, direction: Any) -> bool:
    key = str(direction or "").strip().lower()
    if side == "put":
        return key in {"bearish", "neutral_to_bearish"}
    if side == "call":
        return key in {"bullish", "neutral_to_bullish"}
    return False


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
        text = str(data).strip() if data is not None else "未提供"
        trig_hint = ("TRIGGERED" in text.upper() or "已触发" in text) and "未触发" not in text
        state_label = "TRIGGERED" if trig_hint else "DATA_GAP"
        return [
            f"- 程序机械边界状态（M1）：{state_label}（{text or '未提供'}）；已完成固定网格检查次数未提供。",
            "- **M1 = INTACT 仅表示机械边界尚未越过，不代表原承担条件仍然完整，也不得作为自动 HOLD 的唯一理由。**",
        ]
    trig = bool(data.get("program_triggered"))
    state_label = str(data.get("mechanical_condition_state") or ("TRIGGERED" if trig else "DATA_GAP"))
    description = {
        "TRIGGERED": "原冻结破坏规则已由程序确认触发，CONDITION_TRIGGERED 不可改判",
        "INTACT": "已有检查未触发原冻结破坏规则",
        "NOT_DUE": "尚未到首次固定检查钟",
    }.get(state_label, "缺少可用的固定检查结果")
    lines = [
        f"- 程序机械边界状态（M1 / mechanical_condition_state）：**{state_label}**（{description}）；已记录固定网格检查 {data.get('checks_count', 0)} 次。",
    ]
    if data.get("latest_check_at_ms") is not None:
        lines.append(f"- 最近固定检查钟时刻：{_utc(data.get('latest_check_at_ms'))}")
    if not trig:
        lines.append("- **M1 = INTACT 仅表示机械边界尚未越过，不代表原承担条件仍然完整，也不得作为自动 HOLD 的唯一理由。**")
    return lines


def _format_ard_lines(data: Any) -> list[str]:
    if not isinstance(data, Mapping):
        return _kv_lines(data)
    same = data.get("same_measure")
    if same is None:
        same = data.get("new_neighborhood_events") is not None
    pin_eval_mode = data.get("pin_eval_mode")
    pin_changed = data.get("pin_price_changed")
    frozen_pin = (data.get("frozen_anchor") or {}).get("pin") if isinstance(data.get("frozen_anchor"), Mapping) else data.get("old_pin")
    cur_pin = (data.get("current_anchor") or {}).get("pin") if isinstance(data.get("current_anchor"), Mapping) else data.get("new_pin")
    events = data.get("new_neighborhood_events")
    lineage = data.get("pin_lineage") or "未标注"
    comp_ms = data.get("pin_computed_at_ms") or ((data.get("current_anchor") or {}).get("at") if isinstance(data.get("current_anchor"), Mapping) else None)
    age_ms = data.get("pin_age_ms")
    age_txt = f"{age_ms // 60_000}分钟" if isinstance(age_ms, int) and not isinstance(age_ms, bool) else "未知"
    clk_txt = _utc(comp_ms) if isinstance(comp_ms, int) and not isinstance(comp_ms, bool) else "未知时钟"
    reasons = [str(r) for r in (data.get("pin_qualification_reasons") or ()) if r]
    reason_suffix = f"；原因：{'、'.join(reasons)}" if reasons else ""

    degraded = pin_eval_mode in ("STALE", "UNALIGNED", "UNKNOWN") or pin_changed is None
    if pin_eval_mode == "position_only" and not degraded:
        comp_txt = "仅位置迁移口径可比（position_only；不测邻域停留/离返）"
    elif not degraded and (pin_eval_mode == "same_measure_ard" or same is True):
        comp_txt = "同口径可比"
    else:
        status_tag = pin_eval_mode or ("UNALIGNED" if same is False else "UNKNOWN")
        comp_txt = f"{status_tag}（不可比或已降级；严禁作未迁移或结构完整依据{reason_suffix}）"

    if degraded:
        status_tag = pin_eval_mode or ("UNALIGNED" if same is False else "UNKNOWN")
        pin_mig_line = (
            f"- Pin 迁移对照：入场参考 Pin {_fmt_num(frozen_pin, 1, ' USD')} -> 当前观测状态【{status_tag}】"
            f"（来源：{lineage}，计算时钟：{clk_txt}，延迟：{age_txt}{reason_suffix}；未获有效同口径迁移判定）"
        )
    else:
        pin_mig_line = (
            f"- Pin 迁移对照：入场参考 Pin {_fmt_num(frozen_pin, 1, ' USD')} -> 最新 Pin {_fmt_num(cur_pin, 1, ' USD')}"
            f"（是否迁移：{_fmt_bool_cn(pin_changed)}；来源：{lineage}，计算时钟：{clk_txt}，延迟：{age_txt}）"
        )

    if isinstance(events, list) and not degraded and pin_eval_mode != "position_only":
        ev_txt = f"{len(events)} 次"
    elif pin_eval_mode in ("STALE", "UNALIGNED", "UNKNOWN"):
        ev_txt = f"不适用/未测量（当前状态：{pin_eval_mode}）"
    else:
        ev_txt = "不适用/未测量（仅位置迁移口径）"

    return [
        f"- 同口径可比性：{comp_txt}",
        pin_mig_line,
        f"- 现价距入场 Pin 距离：{_fmt_num(data.get('current_distance_to_frozen_pin_usd'), 1, ' USD')}；位于入场邻域内：{_fmt_bool_cn(data.get('current_inside_frozen_neighborhood'))}；入场后新增同口径邻域事件：{ev_txt}",
    ]


def _format_flow_lines(data: Any, *, triggers: Sequence[Mapping[str, Any]] = ()) -> list[str]:
    if not isinstance(data, Mapping):
        return _kv_lines(data)
    cur_dir = _flow_dir_label(data.get("flow_direction"))
    fast4 = _flow_dir_label(data.get("flow_fast_4h"))
    slow12 = _flow_dir_label(data.get("flow_slow_12h"))
    basis = data.get("flow_window_basis") if isinstance(data.get("flow_window_basis"), Mapping) else None
    flow_qual = data.get("flow_qualification_status")
    fast_ready = data.get("flow_fast_4h_ready")
    flow_reasons = [str(r) for r in (data.get("flow_qualification_reasons") or ()) if r]
    if basis:
        src = basis.get("source") or "未提供"
        sym = basis.get("symbol") or "未提供"
        start_txt = _utc(basis.get("start_ms")) if _int(basis.get("start_ms")) else "未提供"
        end_txt = _utc(data.get("flow_asof_ms")) if _int(data.get("flow_asof_ms")) else "未核实"
        cum_info = ""
        if basis.get("cum_session_id") or basis.get("last_bar_index") is not None:
            cum_info = f"；累计会话 {basis.get('cum_session_id') or '?'} 末柱 #{basis.get('last_bar_index')}"
        basis_line = f"- 现货流向窗口凭据（flow_window_basis）：来源 {src} / 标的 {sym}；4h 窗口 {start_txt} 至 {end_txt}{cum_info}"
    else:
        basis_line = "- 现货流向窗口凭据（flow_window_basis）：缺失（未证实同源完成窗口，禁止用于触发延续判定）"

    d_trigs = [t for t in triggers if isinstance(t, Mapping) and t.get("category") == "D"]
    if d_trigs:
        d_last = d_trigs[-1]
        d_code = str(d_last.get("code") or "")
        d_facts = d_last.get("facts") if isinstance(d_last.get("facts"), Mapping) else {}
        if d_code == "FLOW_ADVERSE_CONTINUATION":
            inc_cvd = _num(d_facts.get("incremental_flow_cvd_btc"))
            inc_basis = d_facts.get("incremental_basis") or "未标明"
            inc_txt = f"；post-ref 增量净流 incremental_flow_cvd_btc={inc_cvd:+.2f} BTC（口径：{inc_basis}）" if inc_cvd is not None else ""
            mode_note = f"本次属【已知不利流继续兑现（FLOW_ADVERSE_CONTINUATION）{inc_txt}】，非全新反转，需评估不利动量与价格同步收缩是否已超出原承担范围。"
        else:
            rev_conf = bool(d_facts.get("reversal_confirmed"))
            mode_note = f"本次属【主动流结构反转（FLOW_REVERSAL；reversal_confirmed={rev_conf}）】，需正面评估反转是否伴随向短腿的实质推进。"
    else:
        mode_note = "未触发 D 类新流向事件；4h/12h 为滚动窗，旧柱滑出不直接等同相邻不重叠窗口真实反转。"

    qual_tag = ""
    if flow_qual in ("OBSERVED_UNALIGNED", "STALE", "UNKNOWN") or fast_ready is False:
        tag = flow_qual or "FAST_4H_NOT_READY"
        rs = f"（{'、'.join(flow_reasons)}）" if flow_reasons else ""
        qual_tag = f"；资格状态：【{tag}】{rs}"
    elif flow_qual:
        qual_tag = f"；资格状态：{flow_qual}"

    qual_failed = flow_qual in ("OBSERVED_UNALIGNED", "STALE", "UNKNOWN") or fast_ready is False
    lines = [
        f"- 现货主动流就绪：{_fmt_bool_cn(data.get('flow_ready'))}（近 4h 就绪：{_fmt_bool_cn(fast_ready) if fast_ready is not None else '未单独标注'}；流向源时钟：{_utc(data.get('flow_asof_ms')) if data.get('flow_asof_ms') else '未核实'}{qual_tag}）",
        basis_line,
    ]
    if qual_failed:
        fail_tag = flow_qual or "FAST_4H_NOT_READY"
        lines.append(
            f"- 流向读数处理：当前现货主动流资格为【{fail_tag}】（未通过时钟/来源/窗口完整校验），原始方向与 CVD 数值已降级隐藏，不参与本轮反转或延续判断。"
        )
    else:
        lines.extend([
            f"- 综合方向：{cur_dir}（近 4h：{fast4} / 近 12h：{slow12}）；定性：{mode_note}",
            f"- 近 4h CVD 净额：{_fmt_num(data.get('flow_cvd_4h'), 2)}；近 4h 动量变化：{_fmt_num(data.get('flow_momentum_4h_pct'), 2, '%')}",
        ])
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


def _hold_flow_delta_allowed(flow_obj: Any, *, require_basis: bool = False) -> bool:
    """Return True iff flow snapshot is aligned and cleared for Hold Section 2/3 comparison."""
    if not isinstance(flow_obj, Mapping):
        return False
    fq = str(flow_obj.get("flow_qualification_status") or "").strip().upper()
    if fq in ("OBSERVED_UNALIGNED", "STALE", "UNKNOWN"):
        return False
    if flow_obj.get("flow_fast_4h_ready") is False or flow_obj.get("flow_ready") is False:
        return False
    if _int(flow_obj.get("flow_asof_ms")) is None:
        return False
    if require_basis and not isinstance(flow_obj.get("flow_window_basis"), Mapping):
        return False
    return True


def _format_dual_delta_lines(
    *,
    side: str,
    short_strike_usd: float | None,
    t0_summary: Any,
    post_entry_summary: Any,
    flow_summary: Any,
    ard_summary: Any,
    last_rev: Mapping[str, Any],
) -> list[str]:
    t0_m = t0_summary if isinstance(t0_summary, Mapping) else {}
    post_m = post_entry_summary if isinstance(post_entry_summary, Mapping) else {}
    flow_m = flow_summary if isinstance(flow_summary, Mapping) else {}
    ard_m = ard_summary if isinstance(ard_summary, Mapping) else {}

    t0_spot = _num(t0_m.get("entry_index_usd")) or _num(t0_m.get("spot_usd"))
    t0_dist = _num(t0_m.get("short_distance_pct"))
    t0_em = _num(t0_m.get("expected_move_usd"))
    cur_spot = _num(post_m.get("latest_spot_usd"))
    cur_dist = _num(post_m.get("latest_short_distance_pct"))
    cur_em = _num(post_m.get("expected_move_usd")) or t0_em

    lines: list[str] = []
    if cur_spot is not None and t0_spot is not None and cur_dist is not None and t0_dist is not None:
        spot_dt0 = cur_spot - t0_spot
        dist_dt0 = cur_dist - t0_dist
        em_txt = ""
        if short_strike_usd is not None and t0_em and t0_em > 0 and cur_em and cur_em > 0:
            r0 = abs(t0_spot - short_strike_usd) / t0_em
            r_now = abs(cur_spot - short_strike_usd) / cur_em
            em_txt = f"；距短腿预期尺度比 {r0:.2f}x -> {r_now:.2f}x"
        lines.append(
            f"- 相对入场 t0 差值：现价 {t0_spot:,.2f} -> {cur_spot:,.2f} USD（{spot_dt0:+,.2f} USD）；"
            f"短腿余量 {t0_dist:.2f}% -> {cur_dist:.2f}%（{dist_dt0:+.2f} 个百分点）{em_txt}。"
        )
    else:
        lines.extend(_format_t0_lines(t0_summary))
        lines.extend(_format_post_entry_lines(post_entry_summary))

    rev_spot = _num(last_rev.get("spot_usd"))
    rev_dist = _num(last_rev.get("short_distance_pct"))
    if last_rev:
        parts: list[str] = []
        if cur_spot is not None and rev_spot is not None:
            parts.append(f"现价 {cur_spot - rev_spot:+,.2f} USD（{rev_spot:,.2f} -> {cur_spot:,.2f}）")
        if cur_dist is not None and rev_dist is not None:
            parts.append(f"短腿余量 {cur_dist - rev_dist:+.2f} 个百分点（{rev_dist:.2f}% -> {cur_dist:.2f}%）")
        cur_flow_ok = _hold_flow_delta_allowed(flow_m, require_basis=True)
        prev_flow_ok = _hold_flow_delta_allowed(last_rev, require_basis=False)
        cur_flow_ts = _int(flow_m.get("flow_asof_ms")) if cur_flow_ok else None
        rev_flow_ts = _int(last_rev.get("flow_asof_ms")) if prev_flow_ok else None
        if cur_flow_ts is not None and rev_flow_ts is not None:
            dt_min = (cur_flow_ts - rev_flow_ts) // 60_000
            parts.append(f"流向时钟推进 {dt_min:+d} 分钟（{_flow_dir_label(last_rev.get('flow_direction'))} -> {_flow_dir_label(flow_m.get('flow_direction'))}）")
        cur_cvd4 = _num(flow_m.get("flow_cvd_4h")) if cur_flow_ok else None
        rev_cvd4 = _num(last_rev.get("flow_cvd_4h")) if prev_flow_ok else None
        if cur_cvd4 is not None and rev_cvd4 is not None:
            parts.append(f"4h CVD {rev_cvd4:+.2f} -> {cur_cvd4:+.2f}")
        cur_pin = _num((ard_m.get("current_anchor") or {}).get("pin")) if isinstance(ard_m.get("current_anchor"), Mapping) else _num(ard_m.get("new_pin"))
        rev_pin = _num(last_rev.get("pin"))
        if cur_pin is not None and rev_pin is not None and cur_pin != rev_pin:
            parts.append(f"Pin 迁移 {rev_pin:,.0f} -> {cur_pin:,.0f} USD")
        if parts:
            lines.append("- 相对上次复核差值：" + "；".join(parts) + "。")
        else:
            lines.append("- 相对上次复核差值：上次复核未保留同口径快照，按最新事实与入场基线对照。")

    # Explicit data gaps summary (high-signal only)
    gaps: list[str] = []
    if isinstance(flow_summary, Mapping):
        if flow_summary.get("flow_ready") is False or flow_summary.get("flow_fast_4h_ready") is False:
            gaps.append("现货主动流未就绪（flow_ready/fast_4h_ready=False）")
        fq_st = flow_summary.get("flow_qualification_status")
        if fq_st in ("OBSERVED_UNALIGNED", "STALE", "UNKNOWN"):
            gaps.append(f"现货主动流资格异常（{fq_st}）")
        elif not isinstance(flow_summary.get("flow_window_basis"), Mapping):
            gaps.append("现货主动流缺少 flow_window_basis 窗口凭据")
    if isinstance(ard_summary, Mapping):
        pm = ard_summary.get("pin_eval_mode")
        if pm in ("STALE", "UNALIGNED", "UNKNOWN"):
            gaps.append(f"Pin/ARD 状态异常（{pm}）")
        elif pm != "position_only" and ard_summary.get("same_measure") is False:
            gaps.append("ARD 口径不可比")
        if ard_summary.get("pin_status") not in (None, "ok"):
            gaps.append(f"Pin 状态异常（{ard_summary.get('pin_status')}）")
    if isinstance(post_entry_summary, Mapping) and (post_entry_summary.get("sample_count") or 0) == 0:
        gaps.append("入场后分钟路径样本为空")
    if gaps:
        lines.append(f"- 明确数据缺口：{'；'.join(gaps)}。")
    return lines


def _derive_hold_channel_readiness(
    *,
    flow_summary: Any,
    ard_summary: Any,
    triggers: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    trig_list = [t for t in (triggers or ()) if isinstance(t, Mapping)]
    spot_fired = any(t.get("category") == "D" for t in trig_list)
    pin_fired = any(t.get("category") == "C" for t in trig_list)

    spot_qual: dict[str, Any] | None = None
    if isinstance(flow_summary, Mapping):
        fq = flow_summary.get("flow_qualification_status")
        f_ready = flow_summary.get("flow_ready")
        fast_ready = flow_summary.get("flow_fast_4h_ready")
        basis = flow_summary.get("flow_window_basis")
        reasons = list(flow_summary.get("flow_qualification_reasons") or ())
        if fq in (evidence_qual.QUAL_ALIGNED, evidence_qual.QUAL_UNALIGNED, evidence_qual.QUAL_STALE, evidence_qual.QUAL_UNKNOWN):
            st = fq
        elif f_ready is False or fast_ready is False:
            st = evidence_qual.QUAL_UNKNOWN
        elif isinstance(basis, Mapping) and _int(flow_summary.get("flow_asof_ms")) is not None:
            st = evidence_qual.QUAL_ALIGNED
        elif f_ready is True:
            st = evidence_qual.QUAL_UNALIGNED
            if not reasons:
                reasons = ["missing_flow_window_basis"]
        else:
            st = evidence_qual.QUAL_UNKNOWN
        cap_map = {
            evidence_qual.QUAL_ALIGNED: evidence_qual.CAP_ACTIVE,
            evidence_qual.QUAL_UNALIGNED: evidence_qual.CAP_BLIND_UNALIGNED,
            evidence_qual.QUAL_STALE: evidence_qual.CAP_BLIND_STALE,
        }
        cap = cap_map.get(st) or (evidence_qual.CAP_WARMING_UP if (f_ready is False or fast_ready is False) else evidence_qual.CAP_BLIND_MISSING)
        spot_qual = {"status": st, "capability": cap, "reason_codes": reasons}

    pin_qual: dict[str, Any] | None = None
    pin_mode: str | None = None
    if isinstance(ard_summary, Mapping):
        pin_mode = ard_summary.get("pin_eval_mode")
        p_reasons = list(ard_summary.get("pin_qualification_reasons") or ())
        if pin_mode in ("same_measure_ard", "position_only"):
            p_st = evidence_qual.QUAL_ALIGNED
            p_cap = evidence_qual.CAP_POSITION_ONLY if pin_mode == "position_only" else evidence_qual.CAP_ACTIVE
        elif pin_mode == "STALE":
            p_st, p_cap = evidence_qual.QUAL_STALE, evidence_qual.CAP_BLIND_STALE
        elif pin_mode == "UNALIGNED":
            p_st, p_cap = evidence_qual.QUAL_UNALIGNED, evidence_qual.CAP_BLIND_UNALIGNED
        elif pin_mode == "UNKNOWN":
            p_st, p_cap = evidence_qual.QUAL_UNKNOWN, evidence_qual.CAP_BLIND_MISSING
        elif ard_summary.get("same_measure") is True and ard_summary.get("pin_status") in (None, "ok") and ard_summary.get("current_sample_available") is not False:
            p_st, p_cap, pin_mode = evidence_qual.QUAL_ALIGNED, evidence_qual.CAP_ACTIVE, "same_measure_ard"
        elif ard_summary.get("same_measure") is False:
            p_st, p_cap, pin_mode = evidence_qual.QUAL_UNALIGNED, evidence_qual.CAP_BLIND_UNALIGNED, "UNALIGNED"
        else:
            p_st, p_cap = evidence_qual.QUAL_UNKNOWN, evidence_qual.CAP_BLIND_MISSING
        pin_qual = {"status": p_st, "capability": p_cap, "reason_codes": p_reasons}

    return evidence_qual.summarize_channel_capabilities(
        spot_qual=spot_qual,
        pin_qual=pin_qual,
        pin_eval_mode=pin_mode,
        spot_event_fired=spot_fired,
        pin_event_fired=pin_fired,
        include_rv24=False,
    )


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
    entry_pin_snapshot: Mapping[str, Any] | None = None,
    authoritative_m: str | None = None,
    review_channel: str | None = None,
    new_qualified_data_present: bool | None = None,
    channel_readiness: Mapping[str, Any] | None = None,
) -> str:
    """Render a pure-Chinese, decision-ready incremental hold-review card with C/M/UNKNOWN/OBSERVE separation."""
    side_norm = str(contract.get("side") or "").strip().lower()
    side_text = side_norm.upper()
    short_strike = _num(contract.get("short_strike_usd"))
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

    t0_m = t0_summary if isinstance(t0_summary, Mapping) else {}
    t0_flow_dir = t0_m.get("flow_direction") or (frozen_condition.get("t0_flow_direction") if isinstance(frozen_condition, Mapping) else None)
    t0_flow_asof = t0_m.get("flow_asof_ms") or (frozen_condition.get("t0_flow_asof_ms") if isinstance(frozen_condition, Mapping) else None)
    t0_qual = str(t0_m.get("flow_qualification_status") or "").strip().upper()
    t0_has_basis = (
        isinstance(t0_m.get("flow_window_basis"), Mapping)
        if ("flow_window_basis" in t0_m or "flow_qualification_status" in t0_m)
        else bool(_int(t0_flow_asof) is not None)
    )
    t0_flow_aligned = bool(
        t0_flow_dir
        and _int(t0_flow_asof) is not None
        and t0_qual not in ("OBSERVED_UNALIGNED", "STALE", "UNKNOWN")
        and t0_m.get("flow_fast_4h_ready") is not False
        and t0_has_basis
    )
    if t0_flow_aligned:
        adverse_at_entry = _is_adverse_flow_dir(side_norm, t0_flow_dir)
        entry_flow_line = (
            f"- 入场时主动流基线：{_flow_dir_label(t0_flow_dir)}（时钟 {_utc(t0_flow_asof)}；"
            + ("**入场时即属已知不利流，已纳入原承保判断，不可将旧不利状态冒充新反转**" if adverse_at_entry else "入场时非不利方向")
            + "）。"
        )
    elif t0_flow_dir:
        tag = t0_qual or "OBSERVED_UNALIGNED"
        entry_flow_line = (
            f"- 入场时主动流基线：资格未通过（{tag}；无有效同源时钟，不得作已知反向支持或可比反转基线），以 [S1] 与 [H1] 冻结原文为准。"
        )
    else:
        entry_flow_line = "- 入场时主动流基线：以 [S1] 与 [H1] 冻结原文为准。"

    depends_on_ard = bool(frozen_condition.get("ard_basis")) or bool(r1)
    pin_snap = entry_pin_snapshot if isinstance(entry_pin_snapshot, Mapping) else {}
    snap_pin = _num(pin_snap.get("pin")) or _num((pin_snap.get("walls") or {}).get("pin") if isinstance(pin_snap.get("walls"), Mapping) else None)
    snap_bh = _num(pin_snap.get("band_half"))
    if snap_pin is not None:
        pin_bg_line = (
            f"- ARD 依赖状态：{'已依赖（R1）' if depends_on_ard else '入场未依赖 ARD（depends_on_ard=False）'}；"
            f"入场只读 Pin 背景：{_fmt_num(snap_pin, 1, ' USD')}（半宽 {_fmt_num(snap_bh, 1, ' USD')}，仅供空间迁移对照）。"
        )
    else:
        pin_bg_line = f"- ARD 依赖状态：{'已依赖（R1）' if depends_on_ard else '入场未依赖 ARD（depends_on_ard=False），仅作结构背景参照'}。"

    resolved_auth_m = authoritative_m
    if resolved_auth_m is None and m1.get("invalidation") in dossier.condition.RULES and hasattr(dossier.condition, "canonical_confirmation_text"):
        try:
            resolved_auth_m = dossier.condition.canonical_confirmation_text(
                dict(contract),
                m1["invalidation"],
                m1.get("check_interval_ms") or 300_000,
            )
        except ValueError:
            resolved_auth_m = None

    lines = [
        "# 持仓增量复核牌面",
        "",
        "## 1. 原承诺",
        "### 原始承诺",
        f"- 锁定责任：{side_text} 短腿 {_fmt_num(short_strike, 0)} / 保护腿 {_fmt_num(contract.get('long_strike_usd'), 0)}（到期 {_utc(contract.get('expiry_ms'))}），入场时间 {_utc(entry_at_ms)}。",
        f"- 原判断：{judgment.get('action') or 'Accept'} / {judgment.get('side') or side_text}；入场指数：{_fmt_num(frozen_condition.get('entry_index_usd') or t0_m.get('entry_index_usd'), 2, ' USD')}；入场短腿余量：{_fmt_num(t0_m.get('short_distance_pct'), 2, '%')}；本期预期波动尺度：{_fmt_num(t0_m.get('expected_move_usd'), 1, ' USD')}。",
        f"- [M1] 机械有效破坏规则：{m1['invalidation']}（{m1['invalidation_meaning']}；固定检查间隔：{m1.get('check_interval_ms') or 300000} 毫秒）。",
        *[f"- [{cid}] 持续承担条件：{ctext}" for cid, ctext in c_items.items()],
        f"- [S1] 当初选边依据：{s1['original_choice_reason']}",
        f"- [S1] 入场时已承认并承担的最强反证：{s1['original_strongest_counterevidence']}（入场前旧事实，不得直接当作入场后新暴雷）。",
        f"- [H1] 入场前历史基线：{h1['historical_facts']}（历史基线，不作为退出触发器）。",
    ]
    if r1:
        lines.append(f"- [R1] ARD 复核约定：{r1['ard_responsibility_meaning']}；复核触发：{r1['ard_review_on']}。")
    lines.extend([
        entry_flow_line,
        pin_bg_line,
        f"- 确认口径：{frozen_condition.get('confirmation_excerpt') or frozen_condition.get('sampling')}",
        *([] if resolved_auth_m is None else [f"- 程序权威机械界定：{resolved_auth_m}"]),
        "",
        "## 2. 上次 AI 已评估什么",
    ])

    prior_list = [r for r in (prior_reviews or ()) if isinstance(r, Mapping)]
    last_rev_extracted: dict[str, Any] = {}
    if not prior_list:
        lines.append("- 这是**首次事中复核**：此前尚无持仓期 AI 复核记录，所有变化直接对照入场 t0 与原承诺。")
    else:
        last_raw = prior_list[-1]
        last_rev_extracted = _extract_last_review_metrics(last_raw)
        rev_time = _utc(last_rev_extracted.get("reviewed_at_ms"))
        ev_cutoff = _utc(last_rev_extracted.get("evidence_asof_ms") or last_rev_extracted.get("reviewed_at_ms"))
        kf = last_rev_extracted.get("key_facts")
        kf_text = "；".join(str(x) for x in kf) if isinstance(kf, Sequence) and not isinstance(kf, (str, bytes)) else (str(kf) if kf else "未单列")
        last_rev_flow_ok = bool(
            last_rev_extracted.get("flow_direction")
            and _hold_flow_delta_allowed(last_rev_extracted, require_basis=False)
        )
        last_rev_flow_txt = _flow_dir_label(last_rev_extracted.get("flow_direction")) if last_rev_flow_ok else "未对齐/未核实（不参与对比）"
        lines.extend([
            f"- 最近一次复核（共 {len(prior_list)} 次）：结论 **{last_rev_extracted.get('verdict') or '未知'}**（{last_rev_extracted.get('protocol_status') or 'RECORDED'}），复核时间 {rev_time}，证据截止 {ev_cutoff}。",
            f"- 上次复核时状态：指数 {_fmt_num(last_rev_extracted.get('spot_usd'), 2, ' USD')}；短腿余量 {_fmt_num(last_rev_extracted.get('short_distance_pct'), 2, '%')}；主动流 {last_rev_flow_txt}。",
            f"- 上次复核核心事实：{kf_text}",
            f"- 上次复核回复：{last_rev_extracted.get('public_reply') or '无'}",
            "- **严禁重复计费**：上述上次复核已评估并承接的旧风险，不得冒充本次新暴雷。",
        ])
        if len(prior_list) > 1:
            hist_brief = " -> ".join(f"{r.get('verdict') or '?'}({_utc(r.get('requested_at_ms') or r.get('decided_at_ms'))})" for r in prior_list[:-1])
            lines.append(f"- 更早复核轨迹：{hist_brief}")

    lines.extend([
        "",
        "## 3. 这次真正新在哪里",
        "### 新增事实",
        *[
            f"- [{t.get('category')}:{t.get('code')}] {t.get('summary_cn')}（观测 {_utc(t.get('observed_at_ms'))}）"
            for t in (triggers or ())
        ],
        *_format_dual_delta_lines(
            side=side_norm,
            short_strike_usd=short_strike,
            t0_summary=t0_summary,
            post_entry_summary=post_entry_summary,
            flow_summary=flow_summary,
            ard_summary=ard_summary,
            last_rev=last_rev_extracted,
        ),
        "### 主动流向与 ARD 同口径结构",
        *_format_flow_lines(flow_summary, triggers=triggers),
        *_format_ard_lines(ard_summary),
        "### 影子管理经济观察（仅作参考，不替代原条件承保结构判断）",
        *_format_economics_lines(t0_summary, quote_summary),
        "",
        "## 4. 程序的不可改写事实",
        "### 条件对照",
        *_format_condition_lines(condition_summary),
        f"- 当前稳定条件 ID 列表：{', '.join(basis.get('valid_condition_ids') or ['M1', 'C1', 'S1'])}。",
        "- 已冻结且已触发的 `CONDITION_TRIGGERED` 为不可变程序事实，只能如实承接，严禁改写为未触发或以旧窗口事实抵消。",
    ])
    if prog_trig:
        lines.append("- **【重要硬约束】程序已确认 `mechanical_condition_state = TRIGGERED`（[M1] 已被打破）：你若核验数据完整应输出 `REVOKE`，若关键数据阻塞缺损应输出 `UNKNOWN`；禁止输出声称原条件仍完整成立的正常 `HOLD`。**")

    if prog_trig:
        m_status_code = "CONDITION_TRIGGERED"
    elif isinstance(condition_summary, Mapping) and condition_summary.get("mechanical_condition_state") == "INTACT":
        m_status_code = "NOT_TRIGGERED"
    else:
        m_status_code = "UNKNOWN"

    if new_qualified_data_present is not None:
        eff_new_data = bool(new_qualified_data_present)
    else:
        latest_post_ts = (post_entry_summary.get("latest_ts_ms") or post_entry_summary.get("latest_at_ms")) if isinstance(post_entry_summary, Mapping) else None
        flow_asof = flow_summary.get("flow_asof_ms") if isinstance(flow_summary, Mapping) else None
        flow_basis_ok = (
            isinstance(flow_summary.get("flow_window_basis"), Mapping)
            and evidence_qual.is_valid_provenance_token(flow_summary["flow_window_basis"].get("source"))
            and evidence_qual.is_valid_provenance_token(flow_summary["flow_window_basis"].get("symbol"))
            and isinstance(_int(flow_summary["flow_window_basis"].get("start_ms")), int)
            and flow_summary.get("flow_fast_4h_ready") is not False
            and flow_summary.get("flow_qualification_status") not in ("OBSERVED_UNALIGNED", "STALE", "UNKNOWN")
        ) if isinstance(flow_summary, Mapping) else False
        ard_new_obs = ard_summary.get("newly_available_anchor_observations") if isinstance(ard_summary, Mapping) else None
        eff_new_data = bool(
            (isinstance(latest_post_ts, int) and latest_post_ts > entry_at_ms)
            or (isinstance(flow_summary, Mapping) and flow_summary.get("flow_ready") is True and flow_basis_ok and isinstance(flow_asof, int) and flow_asof > entry_at_ms)
            or (isinstance(ard_new_obs, Sequence) and not isinstance(ard_new_obs, (str, bytes)) and len(ard_new_obs) > 0)
        )

    if review_channel:
        eff_channel = str(review_channel).strip().upper()
    else:
        has_m = prog_trig or any(isinstance(t, Mapping) and t.get("category") == "A" for t in (triggers or ()))
        has_c = any(isinstance(t, Mapping) and t.get("category") in {"B", "C", "D"} for t in (triggers or ()))
        eff_channel = "MIXED" if (has_m and has_c) else "MECHANICAL_M" if has_m else "C_REVIEW_ONLY" if has_c else "OBSERVE_ONLY"

    unknown_items: list[str] = []
    if m_status_code == "UNKNOWN":
        unknown_items.append("机械检查结果处于 UNKNOWN（未到点或样本缺口）")
    if isinstance(flow_summary, Mapping):
        if flow_summary.get("flow_ready") is False or flow_summary.get("flow_fast_4h_ready") is False:
            unknown_items.append("现货主动流未就绪（flow_ready/fast_4h_ready=False）")
        fq_st = flow_summary.get("flow_qualification_status")
        if fq_st in ("OBSERVED_UNALIGNED", "STALE", "UNKNOWN"):
            unknown_items.append(f"现货主动流资格异常（{fq_st}）")
        elif not isinstance(flow_summary.get("flow_window_basis"), Mapping):
            unknown_items.append("现货主动流缺少 flow_window_basis 窗口凭据")
    if isinstance(ard_summary, Mapping):
        pm = ard_summary.get("pin_eval_mode")
        if pm in ("STALE", "UNALIGNED", "UNKNOWN"):
            unknown_items.append(f"Pin/ARD 状态异常（{pm}）")
        elif pm != "position_only" and ard_summary.get("same_measure") is False:
            unknown_items.append("ARD 口径不可比")
        if ard_summary.get("pin_status") not in (None, "ok"):
            unknown_items.append(f"Pin 状态异常（{ard_summary.get('pin_status')}）")
    elif depends_on_ard:
        unknown_items.append("依赖 ARD 但当前无可比同口径 ARD 数据")
    if isinstance(post_entry_summary, Mapping) and (post_entry_summary.get("sample_count") or 0) == 0:
        unknown_items.append("入场后分钟路径样本为空")

    readiness = (
        dict(channel_readiness)
        if isinstance(channel_readiness, Mapping)
        else _derive_hold_channel_readiness(flow_summary=flow_summary, ard_summary=ard_summary, triggers=triggers)
    )
    ch_map = readiness.get("channels") if isinstance(readiness.get("channels"), Mapping) else {}
    if not ch_map and isinstance(channel_readiness, Mapping):
        # Accept shorthand flat capability mappings when callers pass direct channel->capability pairs.
        spot_cap = readiness.get("spot_flow") if isinstance(readiness.get("spot_flow"), str) else "BLIND_MISSING"
        pin_cap = (
            readiness.get("pin_structure")
            if isinstance(readiness.get("pin_structure"), str)
            else (readiness.get("pin") if isinstance(readiness.get("pin"), str) else "BLIND_MISSING")
        )
        spot_ch = {"capability": spot_cap, "status": spot_cap}
        pin_ch = {"capability": pin_cap, "status": pin_cap}
        blind_list = [k for k, v in (("spot_flow", spot_cap), ("pin_structure", pin_cap)) if str(v).startswith("BLIND_")]
    else:
        spot_ch = ch_map.get("spot_flow") if isinstance(ch_map.get("spot_flow"), Mapping) else {}
        pin_ch = ch_map.get("pin_structure") if isinstance(ch_map.get("pin_structure"), Mapping) else {}
        blind_list = [str(x) for x in (readiness.get("blind_channels") or ()) if x and x != "rv24_background"]
    readiness_line = (
        f"- 通道观测能力（`channel_readiness`）："
        f"现货流 `{spot_ch.get('capability', 'BLIND_MISSING')}`（{spot_ch.get('status', 'UNKNOWN')}） / "
        f"Pin结构 `{pin_ch.get('capability', 'BLIND_MISSING')}`（{pin_ch.get('status', 'UNKNOWN')}）；"
        f"关键盲区：{', '.join(blind_list) if blind_list else '无'}。"
    )

    c_review_line = (
        f"初始冻结 `[C1..]` 须结合入场后真实新增合格数据独立复核（`new_qualified_data_present=True`）。"
        if eff_new_data
        else "当前无入场后新增合格数据（`new_qualified_data_present=False`）；严禁声称 C 已被证实仍有效，须如实说明未获新证据验证或输出 `UNKNOWN`。"
    )
    lines.extend([
        "",
        "## 复核证据分层（M / C / UNKNOWN / 仅供观察）",
        f"- 复核通道：`{eff_channel}`；入场后新增合格数据（`new_qualified_data_present`）：`{'True' if eff_new_data else 'False'}`。",
        readiness_line,
        f"- 1. **机械 M**：`{m_status_code}`（`CONDITION_TRIGGERED` / `NOT_TRIGGERED` / `UNKNOWN`；程序机械裁决不可改写）。",
        f"- 2. **独立 C 复核**：{c_review_line}",
        f"- 3. **不可判定/未观测项（UNKNOWN）**：{'；'.join(unknown_items) if unknown_items else '当前无显式阻塞缺口；若存在口径冲突或未观测项须如实标 `UNKNOWN`。'}",
        "- 4. **仅供观察项（OBSERVE_ONLY）**：影子盘口买回估算、锁定时信用基准及非依赖结构背景仅作观察参考，不单独替代 M/C 结论。",
        "- **执行与留痕边界**：C 仅用于独立复核与留痕，不授权自动提前平仓、甩腿或交易。",
    ])

    lines.extend([
        "",
        "## 5. 唯一管理问题",
        "- **唯一管理问题**：结合入场后真正新增的实质证据，最初继续承担该固定责任的判断是否已经被推翻？",
        "### 更新判断要求",
        "- 只能输出唯一明确结论：`HOLD`、`REVOKE` 或 `UNKNOWN`。",
        "- **严禁空洞套话**：禁止仅以“M1 未触发”或“D 仅作提醒”作为 `HOLD` 理由——若输出 `HOLD`，必须正面说明本次新增事实为何尚未推翻 `[C1..]` 与 `[S1]`。",
        "- 若 `M1 = INTACT` 但判定 `REVOKE`（主动撤销），`affected_conditions` 必须清晰标明被推翻的具体 `C#` / `S#`（及 `R1` 如适用）。",
        "- 若存在实质数据冲突，或关键流向/盘口/路径采样不可核验而实质阻塞判断，应输出 `UNKNOWN` 并指明阻塞项。",
        "- 严禁重新选择 Put/Call 或改边（不得输出 Accept / Put、Accept / Call 或建议切换方向），严禁推翻已触发的 `CONDITION_TRIGGERED`。",
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
    discretionary_allowed = {cid for cid in allowed_ids if cid[:1].upper() in {"C", "S", "R"}}
    discretionary_mapped_ids = [cid for cid in mapped_ids if cid in discretionary_allowed]
    mapping_status = "UNMAPPED_CONDITION_ID" if unmapped_ids else "MAPPED"

    mech_state = mechanical_state or ("TRIGGERED" if program_triggered else "INTACT")
    notes: list[str] = []
    discretionary_revoke_attribution = "NOT_APPLICABLE"
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
        if discretionary_mapped_ids:
            protocol_status = "DISCRETIONARY_REVOKE"
            discretionary_revoke_attribution = "VERIFIED"
            notes.append("模型依据新增反证主动建议影子撤销，程序没有确认机械触发（DISCRETIONARY_REVOKE）。")
        else:
            protocol_status = "DISCRETIONARY_REVOKE_UNVERIFIED"
            discretionary_revoke_attribution = "UNVERIFIED"
            if not cleaned_affected:
                mapping_status = "EMPTY_CONDITION_IDS"
            elif unmapped_ids:
                mapping_status = "UNMAPPED_CONDITION_ID"
            else:
                mapping_status = "NON_DISCRETIONARY_BASIS_ONLY"
            notes.append("模型主动建议 REVOKE，但 affected_conditions 未映射到当时真实冻结的持续承保依据（C#/S#/R#）；保留原答，不计入已验证影子主动撤销归因（DISCRETIONARY_REVOKE_UNVERIFIED）。")
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
        "discretionary_mapped_condition_ids": discretionary_mapped_ids,
        "discretionary_revoke_attribution": discretionary_revoke_attribution,
        "condition_id_mapping_status": mapping_status,
        "public_reply": public_reply.strip(),
        "key_facts": deepcopy(payload["key_facts"]),
        "public_text": public_text,
        "program_triggered": bool(program_triggered),
        "notes": notes,
    }
