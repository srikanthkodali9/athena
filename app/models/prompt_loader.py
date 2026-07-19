"""Versioned prompt-template loader.

Templates live under ``prompts/{category}/{name}.md`` (inference_engineer-owned).
Each file starts with a version marker::

    <!-- prompt: observability version: obs-v1 -->

We use ``string.Template`` ($-substitution) rather than ``str.format`` because the
templates embed literal JSON braces (schema examples) that would otherwise need
escaping. Placeholders are ``$name`` / ``${name}``. ``safe_substitute`` leaves any
unknown placeholder untouched instead of raising, so a template edit never breaks
a call mid-cycle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Template

# repo_root/app/models/prompt_loader.py -> repo_root/prompts
_PROMPTS_ROOT = Path(__file__).resolve().parents[2] / "prompts"
_VERSION_RE = re.compile(r"<!--\s*prompt:\s*(\S+)\s+version:\s*(\S+)\s*-->")


@dataclass(frozen=True)
class PromptTemplate:
    category: str
    name: str
    version: str
    body: str

    def render(self, **values: object) -> str:
        return Template(self.body).safe_substitute(
            {k: v for k, v in values.items()}
        )


@lru_cache(maxsize=64)
def load_prompt(category: str, name: str) -> PromptTemplate:
    path = _PROMPTS_ROOT / category / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    version = f"{name}-v0"
    match = _VERSION_RE.search(text)
    body = text
    if match:
        version = match.group(2)
        body = text[match.end() :].lstrip("\n")
    return PromptTemplate(category=category, name=name, version=version, body=body)


def prompts_root() -> Path:
    return _PROMPTS_ROOT
