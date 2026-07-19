"""Inference adapter error types."""

from __future__ import annotations


class InferenceError(Exception):
    """Base class for inference adapter failures."""


class InferenceUnavailable(InferenceError):
    """Raised when an endpoint could not be reached within bounded retries.

    The router catches this and either falls back to a deterministic provider or
    returns an explicitly ``degraded`` result rather than crashing the cycle.
    """


class InvalidStructuredOutput(InferenceError):
    """Raised when a model response cannot be coerced into the required schema
    even after the single permitted repair retry."""
