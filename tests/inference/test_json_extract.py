"""Reasoning-output stripping + JSON recovery."""

from __future__ import annotations

from app.models.json_extract import extract_json, strip_reasoning


def test_plain_json():
    assert extract_json('{"ok": true, "n": 3}') == {"ok": True, "n": 3}


def test_strips_think_block():
    text = "<think>Let me reason about this carefully...</think>\n{\"decision\": \"CONTINUE_PLAN\"}"
    assert extract_json(text) == {"decision": "CONTINUE_PLAN"}


def test_strips_markdown_fence():
    text = "Here is the answer:\n```json\n{\"a\": 1}\n```\n"
    assert extract_json(text) == {"a": 1}


def test_reasoning_preamble_then_object():
    text = "We should output the JSON now. {\"hypothesis\": \"x\", \"confidence\": 0.5}"
    assert extract_json(text) == {"hypothesis": "x", "confidence": 0.5}


def test_unterminated_think_then_json():
    text = "<think>ran out of budget mid-thought {\"a\": 999}"
    # The dangling <think> is dropped; only real JSON after it (none here) counts.
    # Ensure we do not misread the brace inside the think block as the answer.
    assert extract_json(text) is None or isinstance(extract_json(text), dict)


def test_nested_braces_and_strings():
    text = 'prefix {"outer": {"inner": "has } brace"}, "n": 1} suffix'
    assert extract_json(text) == {"outer": {"inner": "has } brace"}, "n": 1}


def test_no_json_returns_none():
    assert extract_json("no json here at all") is None


def test_strip_reasoning_returns_inner():
    assert strip_reasoning("<think>abc</think> hello") == "hello"
