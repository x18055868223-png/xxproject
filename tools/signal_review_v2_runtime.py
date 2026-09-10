"""Single assessment orchestration; historical two-call helpers remain offline.

One durable card state owns both transport and format recovery. A reservation is
written before HTTP, so an interrupted process cannot reset the attempt limit.
"""
import hashlib
import json
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import signal_llm_review as core
from signal_evidence_v2 import build_evidence_packet, packet_hash
from signal_review_v2 import (EvidenceFormatError, build_request, build_review,
                              build_error_review, LEGACY_PROMPT_VERSION,
                              PROMPT_VERSION, ACCEPTED_PROMPT_VERSIONS)

MODE = "single_evidence_v2"
PROMPT = PROMPT_VERSION
MAX_ATTEMPTS = 2
AUTOMATIC_EXCLUSIONS_SCHEMA_VERSION = "signal_review_automatic_exclusions@1.0.0"


def _read_state(path):
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("attempts"), list):
        raise ValueError("评审额度记录损坏，暂停自动调用")
    return value


def _read_automatic_exclusions(path):
    if not path:
        return set()
    path = Path(path)
    if not path.exists():
        raise ValueError("自动排除配置不存在，暂停自动调用")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("自动排除配置不可读取，暂停自动调用") from exc
    if not isinstance(value, dict):
        raise ValueError("自动排除配置格式错误，暂停自动调用")
    if value.get("schema_version") != AUTOMATIC_EXCLUSIONS_SCHEMA_VERSION:
        raise ValueError("自动排除配置版本不受支持，暂停自动调用")
    card_ids = value.get("card_ids")
    if not isinstance(card_ids, list):
        raise ValueError("自动排除配置缺少卡片列表，暂停自动调用")
    if not all(isinstance(card_id, str) and card_id for card_id in card_ids):
        raise ValueError("自动排除配置包含无效卡片编号，暂停自动调用")
    return set(card_ids)


def _recoverable(exc):
    if isinstance(exc, (EvidenceFormatError, core.LlmEmptyContentError,
                        json.JSONDecodeError, TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, core.LlmApiError):
        return exc.status_code in {408, 429, 500, 502, 503, 504}
    if isinstance(exc, urllib.error.URLError):
        return True
    return isinstance(exc, ValueError) and (
        "finish_reason is length" in str(exc) or "missing choices" in str(exc))


def _reason(exc):
    # Provider text may contain request details. Persist a typed failure instead.
    if isinstance(exc, core.LlmApiError):
        if exc.status_code in {401, 403}:
            return "服务鉴权失败，需修复配置后核查。"
        return "评审服务请求未完成。"
    if isinstance(exc, core.DailyBudgetExceeded):
        return "今日评审请求额度已用尽。"
    if _recoverable(exc):
        return "评审返回未完成有效格式，保留失败记录。"
    return "评审未通过校验，暂停自动恢复。"


def _state_key(card_id, prompt, mode, model):
    return hashlib.sha256((card_id + "|" + prompt + "|" + mode + "|" + model).encode()).hexdigest()


def _state_path(states, card_id, model):
    current = states / (_state_key(card_id, PROMPT, MODE, model) + ".json")
    # A changed prompt is never a new allowance for the same card. Resolve all
    # supported historical prompts before creating the new state file.
    existing = []
    for prompt in dict.fromkeys((*ACCEPTED_PROMPT_VERSIONS, PROMPT)):
        path = states / (_state_key(card_id, prompt, MODE, model) + ".json")
        if path.exists():
            existing.append(path)
    if len(existing) > 1:
        raise ValueError("同一卡存在多个版本的尝试状态，需要核对原始额度。")
    return existing[0] if existing else current


def _run_card(card, packet, state_path, api_key, model, timeout, endpoint,
              budget, transport, reviewed_at):
    with core._exclusive_file_lock(state_path):
        state = _read_state(state_path)
        if state:
            frozen = state["packet"]
            if frozen.get("identity") != packet.get("identity"):
                raise ValueError("同一卡片的来源身份已变化，需独立核查")
            packet = frozen  # A later transition arrival never buys more calls.
            if state.get("settled"):
                return state["record"], False
            if state.get("prompt") not in (None, PROMPT):
                review = build_error_review(
                    card, packet, "旧版评审尝试已存在，普通轮次不因 Prompt 升级重新调用模型。",
                    model=model, reviewed_at=reviewed_at,
                    require_price_bias=True, prompt_version=PROMPT)
                review["retry_budget"] = {"limit": MAX_ATTEMPTS,
                                          "used": len(state["attempts"]),
                                          "persistent": True}
                return {"card_id": core._card_id(card), "llm_review": review}, False
        else:
            state = {"schema": "signal_review_attempts@2.0.0", "packet": packet,
                     "packet_hash": packet_hash(packet), "prompt": PROMPT,
                     "mode": MODE, "model": model, "attempts": []}
        review = None
        while len(state["attempts"]) < MAX_ATTEMPTS:
            number = len(state["attempts"]) + 1
            reservation = None
            try:
                if not api_key:
                    raise RuntimeError("LLM_API_KEY is required")
                request = build_request(packet, model, recovery=number > 1)
                if budget is not None:
                    reservation = budget.reserve(
                        1, role=MODE, packet_hash=state["packet_hash"],
                        provider=core.PROVIDER, model=model)
                attempt = {"number": number, "status": "RESERVED",
                           "recovery": number > 1,
                           "input_bytes": len(json.dumps(
                               core._strip_local_request_fields(request),
                               ensure_ascii=False).encode("utf-8"))}
                state["attempts"].append(attempt)
                core._write_json_atomic(state_path, state)
                started = time.monotonic()
                response = transport(api_key, model, request, timeout, endpoint=endpoint)
                attempt["elapsed_seconds"] = round(time.monotonic() - started, 3)
                attempt["usage"] = core._response_usage(response)
                response_path = state_path.parent / "responses" / (state_path.stem + f".{number}.json")
                core._write_json_atomic(response_path, response)
                attempt["response_sha256"] = hashlib.sha256(response_path.read_bytes()).hexdigest()
                if reservation:
                    budget.complete(reservation["reservation_id"], "HTTP_OK", attempt["usage"])
                    reservation = None
                payload = core.parse_chat_response(response)
                review = build_review(card, payload, packet, model=model,
                                      reviewed_at=reviewed_at,
                                      require_price_bias=True)
                attempt["status"] = review["status"]
                # Semantic or side validation failures are final, never format recovery.
                break
            except Exception as exc:
                if reservation:
                    budget.complete(reservation["reservation_id"], "ERROR", {})
                if isinstance(exc, core.DailyBudgetExceeded):
                    # No HTTP happened; resume the same frozen budget on another day.
                    core._write_json_atomic(state_path, state)
                    return {"card_id": core._card_id(card), "deferred": "DAILY_BUDGET", "llm_review":
                            build_error_review(card, packet, _reason(exc), model=model,
                                               reviewed_at=reviewed_at,
                                               require_price_bias=True,
                                               prompt_version=PROMPT)}, False
                if state["attempts"] and state["attempts"][-1]["status"] == "RESERVED":
                    state["attempts"][-1].update(
                        status="ERROR", error_type=type(exc).__name__, reason_cn=_reason(exc))
                review = build_error_review(card, packet, _reason(exc), model=model,
                                            reviewed_at=reviewed_at,
                                            require_price_bias=True,
                                            prompt_version=PROMPT)
                core._write_json_atomic(state_path, state)
                if not _recoverable(exc) or len(state["attempts"]) >= MAX_ATTEMPTS:
                    break
        if review is None:
            review = build_error_review(card, packet, "本次评审尝试额度已用尽。",
                                        model=model, reviewed_at=reviewed_at,
                                        require_price_bias=True,
                                        prompt_version=PROMPT)
        review["llm_call_count"] = len(state["attempts"])
        review["llm_http_calls"] = len(state["attempts"])
        review["call_audit"] = list(state["attempts"])
        review["retry_budget"] = {"limit": MAX_ATTEMPTS,
                                 "used": len(state["attempts"]), "persistent": True}
        record = {"card_id": core._card_id(card), "llm_review": review}
        state.update(settled=True, record=record)
        core._write_json_atomic(state_path, state)
        return record, True


def generate_reviews(source, reviews_output, api_key=None, model=core.DEFAULT_MODEL,
                     limit=4, include_synthetic=False, timeout=240, base_url=None,
                     budget=None, max_concurrency=4, only_card_id=None,
                     transition_ledger=None, transport=None, reviewed_at=None,
                     automatic_exclusions=None):
    cards = sorted(core._dedupe_cards(core._read_jsonl(source)),
                   key=core._card_sort_key, reverse=True)
    excluded_card_ids = _read_automatic_exclusions(automatic_exclusions)
    by_id = {core._card_id(card): card for card in cards}
    transitions = {str(t.get("current_card_id")): t for t in
                   core._read_jsonl(transition_ledger) if t.get("current_card_id")} if transition_ledger else {}
    output = Path(reviews_output)
    states = output.with_suffix(output.suffix + ".v2_attempts")
    states.mkdir(parents=True, exist_ok=True)
    done = {}
    for record in core._read_jsonl(output):
        review = record.get("llm_review", {})
        if review.get("status") in {"OK", "PARTIAL"} or (
                str(review.get("schema_version", "")).startswith("signal_llm_review@2.")
                and review.get("retry_budget", {}).get("persistent")):
            done[str(record.get("card_id"))] = review
    target = None
    if only_card_id:
        target = {"card_id": only_card_id, "found": only_card_id in by_id,
                  "already_ok": False, "attempted": False, "status": "MISSING"}
        cards = [by_id[only_card_id]] if only_card_id in by_id else []
        limit = 1
    chosen, skipped, automatic_excluded = [], 0, 0
    for card in cards:
        cid = core._card_id(card)
        if cid in done:
            skipped += 1
            if target:
                target.update(already_ok=done[cid]["status"] in {"OK", "PARTIAL"},
                              status="ALREADY_OK" if done[cid]["status"] in {"OK", "PARTIAL"} else "SETTLED_ERROR")
            continue
        if not only_card_id and cid in excluded_card_ids:
            skipped += 1
            automatic_excluded += 1
            continue
        if core._is_synthetic(card) and not include_synthetic:
            skipped += 1
            continue
        chosen.append(card)
        if limit and len(chosen) >= limit:
            break

    def worker(card):
        cid = core._card_id(card)
        transition = transitions.get(cid)
        previous = by_id.get(str((transition or {}).get("previous_card_id")))
        packet = build_evidence_packet(card, previous, transition)
        try:
            # Serialize version resolution as well as the request. The state
            # lock inside _run_card also coordinates with older running code.
            card_lock = states / ("card-" + hashlib.sha256(cid.encode()).hexdigest())
            with core._exclusive_file_lock(card_lock):
                return _run_card(card, packet, _state_path(states, cid, model), api_key, model,
                                 timeout, base_url, budget, transport or core._post_chat_completion,
                                 reviewed_at)
        except (ValueError, KeyError, TypeError, OSError):
            review = build_error_review(card, packet, "本卡评审记录校验失败，自动调用已暂停。",
                                        model=model, reviewed_at=reviewed_at,
                                        require_price_bias=True,
                                        prompt_version=PROMPT)
            review["retry_budget"] = {"limit": MAX_ATTEMPTS, "used": None, "persistent": True}
            return {"card_id": cid, "llm_review": review}, False

    written = errors = attempted = 0
    # Probe one card first so a bad credential does not fan out to every card.
    def results():
        if not chosen:
            return
        first = worker(chosen[0])
        yield first
        audit = first[0].get("llm_review", {}).get("call_audit", [])
        if audit and audit[-1].get("reason_cn", "").startswith("服务鉴权失败"):
            return
        with ThreadPoolExecutor(max_workers=max(1, int(max_concurrency))) as pool:
            yield from pool.map(worker, chosen[1:])

    for record, did_attempt in results():
        review = record["llm_review"]
        if record.get("deferred"):
            skipped += 1
            if target:
                target.update(attempted=False, status=record["deferred"])
            continue
        attempted += int(did_attempt)
        errors += int(review["status"] == "ERROR")
        core._append_jsonl(output, record)
        written += 1
        if target:
            target.update(attempted=did_attempt, status=review["status"])
    return {"written": written, "errors": errors, "attempted": attempted,
            "skipped": skipped, "review_mode": MODE, "target": target,
            "automatic_excluded": automatic_excluded,
            "daily_http_budget": budget.snapshot() if budget else None}
