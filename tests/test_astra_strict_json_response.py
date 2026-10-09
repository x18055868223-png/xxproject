import json

import pytest

from tools import signal_llm_review as llm


def _response(content, finish_reason="stop"):
    return {
        "choices": [{
            "finish_reason": finish_reason,
            "message": {"content": content},
        }],
    }


def test_strict_json_object_accepts_direct_single_object_and_preserves_fields():
    payload = llm.parse_chat_response(
        _response('{"type":"json_object","ok":true,"nested":{"value":2}}'),
        strict_json_object=True,
    )

    assert payload == {"type": "json_object", "ok": True, "nested": {"value": 2}}


@pytest.mark.parametrize("content", [
    '```json\n{"ok":true}\n```',
    '{"ok":true}\n{"ok":true}',
    '{"ok":true}\ntrailing text',
    '[{"ok":true}]',
    '{"ok":NaN}',
    '{"ok":Infinity}',
    '{"ok":true,"ok":false}',
    '{"outer":{"x":1,"x":2}}',
])
def test_strict_json_object_rejects_wrappers_extra_objects_and_nonstandard_json(content):
    with pytest.raises(ValueError):
        llm.parse_chat_response(_response(content), strict_json_object=True)


def test_default_parser_keeps_legacy_fence_and_repeated_object_compatibility():
    assert llm.parse_chat_response(_response('```json\n{"ok":true}\n```')) == {"ok": True}
    assert llm.parse_chat_response(_response('{"ok":true}\n{"ok":true}')) == {"ok": True}


def test_strict_json_object_preserves_finish_reason_and_empty_content_handling():
    with pytest.raises(ValueError, match="finish_reason"):
        llm.parse_chat_response(_response("{}", finish_reason="length"), strict_json_object=True)

    with pytest.raises(llm.LlmEmptyContentError):
        llm.parse_chat_response({
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "", "reasoning_content": "SECRET_REASONING"},
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }, strict_json_object=True)
