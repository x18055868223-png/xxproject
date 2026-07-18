#!/usr/bin/env python3
"""Runtime entrypoint for Gemini signal reviews.

The core reviewer remains the source of all schema, policy, and fail-closed
validation. This entrypoint only repairs a mechanically impossible
``source_alignment`` enum before the core validator runs:

- ``ALIGNED`` requires an exact recognized direction match;
- direct bullish/bearish opposition requires ``DIVERGENT``;
- an unable blind view requires ``UNABLE_TO_JUDGE``.

All recommendation, hard-block, waiting-state, evidence, authorization, human
text, and execution-parameter checks remain in the core reviewer unchanged.
"""

import copy
import json
import sys

import gemini_signal_llm_review as core


ENTRY_VERSION = "gemini_signal_review_entry@1.0.0"
PROMPT_VERSION = "gemini_signal_review_prompt@1.4.5"
_ALLOWED_ALIGNMENTS = set(core.ADVISORY_SOURCE_ALIGNMENTS)
_RECOGNIZED_DIRECTIONS = {"BULLISH", "BEARISH", "NEUTRAL"}

_ORIGINAL_BUILD_PROMPT = core.build_prompt
_ORIGINAL_BUILD_LLM_REVIEW = core.build_llm_review

_ALIGNMENT_PROMPT_RULES = (
    "\n\nsource_alignment 精确映射（必须逐项执行）：\n"
    "- 只有 producer decision.lean 与盲读 theoretical_active_view.bias 映射后的方向均可识别，"
    "且完全相同，才可填写 ALIGNED。\n"
    "- producer 与盲读分别为明确偏多和明确偏空时，必须填写 DIVERGENT。\n"
    "- 一方为中性、区间或 MIXED_UNCLEAR，且不存在明确多空对立时，填写 PARTIALLY_ALIGNED。\n"
    "- 盲读为 UNABLE_TO_JUDGE，或 producer 方向无法识别时，填写 UNABLE_TO_JUDGE。\n"
    "- 不得把 recommendation 相同、都选择等待、或部分证据相似误写为 ALIGNED。"
)


def build_prompt(packet, blind_payload=None):
    """Append an exact enum mapping without changing the core review prompt."""
    return _ORIGINAL_BUILD_PROMPT(packet, blind_payload) + _ALIGNMENT_PROMPT_RULES


def _alignment_context(payload, packet):
    view = core._as_dict(core._as_dict(payload).get("theoretical_active_view"))
    blind_bias = str(view.get("bias") or "").upper()
    blind_direction = core._advisory_direction(blind_bias)
    producer_lean = core._as_dict(core._as_dict(packet).get("decision")).get("lean")
    producer_direction = core._advisory_direction(producer_lean)
    return blind_bias, blind_direction, str(producer_lean or ""), producer_direction


def _repair_source_alignment(payload, packet):
    """Repair only enum combinations that the core validator proves impossible."""
    repaired_payload = copy.deepcopy(payload)
    advisory = core._as_dict(repaired_payload.get("integrated_trade_advisory"))
    claimed = str(advisory.get("source_alignment") or "").upper()
    blind_bias, blind_direction, producer_lean, producer_direction = (
        _alignment_context(repaired_payload, packet)
    )
    final = claimed
    reason = "NONE"

    # Missing or invalid enum values remain fail-closed in the core validator.
    if claimed in _ALLOWED_ALIGNMENTS:
        direct_opposition = {
            producer_direction,
            blind_direction,
        } == {"BULLISH", "BEARISH"}
        aligned_pair = (
            producer_direction in _RECOGNIZED_DIRECTIONS
            and producer_direction == blind_direction
        )

        if blind_bias == "UNABLE_TO_JUDGE" and claimed != "UNABLE_TO_JUDGE":
            final = "UNABLE_TO_JUDGE"
            reason = "BLIND_UNABLE_REQUIRES_UNABLE_ALIGNMENT"
        elif producer_direction == "UNKNOWN" and claimed == "ALIGNED":
            final = "UNABLE_TO_JUDGE"
            reason = "UNKNOWN_PRODUCER_CANNOT_BE_ALIGNED"
        elif direct_opposition and claimed != "DIVERGENT":
            final = "DIVERGENT"
            reason = "DIRECT_DIRECTION_OPPOSITION_REQUIRES_DIVERGENT"
        elif claimed == "ALIGNED" and not aligned_pair:
            final = "PARTIALLY_ALIGNED"
            reason = "ALIGNED_REQUIRES_EXACT_RECOGNIZED_DIRECTION_MATCH"

    repaired = final != claimed
    if repaired:
        advisory = dict(advisory)
        advisory["source_alignment"] = final
        repaired_payload["integrated_trade_advisory"] = advisory

    trace = {
        "entry_version": ENTRY_VERSION,
        "repair_applied": repaired,
        "repair_reason": reason,
        "claimed": claimed,
        "final": final,
        "blind_bias": blind_bias,
        "blind_direction": blind_direction,
        "producer_lean": producer_lean,
        "producer_direction": producer_direction,
    }
    return repaired_payload, trace


def build_llm_review(card, payload, model=core.DEFAULT_MODEL, reviewed_at=None,
                     derived_blind=True, llm_call_count=2,
                     llm_call_routes=None):
    """Apply the bounded repair, then delegate every validation to the core."""
    packet = core.build_review_packet(card)
    repaired_payload, trace = _repair_source_alignment(payload, packet)
    review = _ORIGINAL_BUILD_LLM_REVIEW(
        card,
        repaired_payload,
        model=model,
        reviewed_at=reviewed_at,
        derived_blind=derived_blind,
        llm_call_count=llm_call_count,
        llm_call_routes=llm_call_routes,
    )
    policy = core._as_dict(
        core._as_dict(review.get("integrated_trade_advisory")).get(
            "policy_validation"
        )
    )
    policy.update({
        "source_alignment_repair_applied": trace["repair_applied"],
        "source_alignment_repair_reason": trace["repair_reason"],
        "source_alignment_claimed": trace["claimed"],
        "source_alignment_final": trace["final"],
        "source_alignment_blind_direction": trace["blind_direction"],
        "source_alignment_producer_direction": trace["producer_direction"],
        "alignment_entry_version": ENTRY_VERSION,
    })
    review["integrated_trade_advisory"]["policy_validation"] = policy
    if trace["repair_applied"]:
        print(
            json.dumps(
                {"event": "SOURCE_ALIGNMENT_REPAIRED", **trace},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
    return review


# Patch only the two card-review entrypoints used by core.main/generate_reviews.
# Transition review functions and all core validators remain untouched.
core.PROMPT_VERSION = PROMPT_VERSION
core.build_prompt = build_prompt
core.build_llm_review = build_llm_review


def main(argv=None):
    return core.main(argv)


if __name__ == "__main__":
    sys.exit(main())
