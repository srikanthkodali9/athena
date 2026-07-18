"""Provider adapters for Athena's reasoning plane (inference_engineer).

Vendor-shaped code lives here and nowhere else: the router
(``app/routing``), the Council (``app/council``), and the orchestrator depend
only on the core ``InferencePort`` and these adapters — never on NIM/vLLM
request shapes directly.
"""

from __future__ import annotations

from app.models.errors import (
    InferenceError,
    InferenceUnavailable,
    InvalidStructuredOutput,
)
from app.models.json_extract import extract_json, strip_reasoning
from app.models.metrics import InferenceMetrics
from app.models.nim_client import (
    REASONING_OFF,
    REASONING_ON,
    NimChatClient,
    NimClientConfig,
    NimResult,
)
from app.models.prompt_loader import PromptTemplate, load_prompt

__all__ = [
    "InferenceError",
    "InferenceUnavailable",
    "InvalidStructuredOutput",
    "extract_json",
    "strip_reasoning",
    "InferenceMetrics",
    "NimChatClient",
    "NimClientConfig",
    "NimResult",
    "REASONING_OFF",
    "REASONING_ON",
    "PromptTemplate",
    "load_prompt",
]
