"""Robust JSON extraction for reasoning-model output.

Nemotron reasoning models can prepend a chain-of-thought preamble, wrap the
answer in a ``<think>...</think>`` block, or fence it in a markdown code block.
NVIDIA NIM usually returns the visible answer in ``content`` and the private
trace separately in ``reasoning_content`` (see the adapter), but we must never
*rely* on that separation: an endpoint or model-card change can fold the trace
back into ``content``. These helpers strip preambles/think-blocks/fences and
recover the first complete JSON object so downstream schema validation is stable.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# An unterminated <think> (truncated output) — drop everything up to a later JSON.
_OPEN_THINK = re.compile(r"<think>.*", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)\s*```", re.DOTALL)


def strip_reasoning(text: str) -> str:
    """Remove ``<think>`` blocks and markdown code fences, returning inner text."""
    if not text:
        return ""
    cleaned = _THINK_BLOCK.sub("", text)
    # A dangling <think> with no close means reasoning ran into the token budget;
    # keep only what follows a close tag if present, else drop the open block.
    if "<think>" in cleaned.lower() and "</think>" not in cleaned.lower():
        cleaned = _OPEN_THINK.sub("", cleaned)
    fence = _FENCE.search(cleaned)
    if fence:
        cleaned = fence.group(1)
    return cleaned.strip()


def _scan_first_json(text: str) -> Optional[str]:
    """Return the first balanced ``{...}`` or ``[...]`` span, respecting strings."""
    opens = {"{": "}", "[": "]"}
    start = -1
    stack: list[str] = []
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if start == -1:
            if ch in opens:
                start = i
                stack.append(opens[ch])
            continue
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in opens:
            stack.append(opens[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
            if not stack:
                return text[start : i + 1]
    return None


def extract_json(text: str) -> Optional[dict[str, Any]]:
    """Best-effort parse of a single JSON object from noisy model output.

    Returns the parsed object, or ``None`` if no valid JSON object is present.
    """
    if not text:
        return None
    cleaned = strip_reasoning(text)
    # Fast path: the whole thing is already JSON.
    for candidate in (cleaned, text.strip()):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    # Scan for the first balanced object in the cleaned text, then the raw text.
    for source in (cleaned, text):
        span = _scan_first_json(source)
        if span:
            try:
                parsed = json.loads(span)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue
    return None
