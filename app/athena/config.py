"""Typed application settings (FND-02).

All configuration reads the ``ATHENA_*`` variables documented in
``.env.example``. Defaults are safe for local development so the app boots with
no ``.env`` present. Settings are mode-aware: ``fixture`` triggers are only
permitted in ``development`` or ``demo`` mode and must be rejected in
``production``.

Nothing here holds a secret longer than it must; sanitized views for the
``/config/status`` endpoint never expose URLs containing credentials or keys.
"""

from __future__ import annotations

import enum
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AthenaEnv(str, enum.Enum):
    development = "development"
    demo = "demo"
    production = "production"


class InferenceMode(str, enum.Enum):
    live = "live"
    mock = "mock"


class SecurityMode(str, enum.Enum):
    live = "live"
    mock = "mock"


class KubernetesMode(str, enum.Enum):
    fixture = "fixture"
    live_read_only = "live_read_only"


class Settings(BaseSettings):
    """Validated runtime configuration.

    Field names map onto ``ATHENA_<UPPER_FIELD>`` environment variables via the
    ``env_prefix`` below (e.g. ``env`` -> ``ATHENA_ENV``).
    """

    model_config = SettingsConfigDict(
        env_prefix="ATHENA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Runtime mode
    env: AthenaEnv = AthenaEnv.development
    log_level: str = "INFO"
    host: str = "127.0.0.1"
    port: int = 8000

    # Heartbeat + investigation budgets
    heartbeat_interval_seconds: int = 30
    max_specialists: int = 2
    max_total_tokens: int = 14000
    investigation_deadline_seconds: int = 75
    max_tool_calls: int = 10

    # Inference (OpenAI-compatible vLLM). Empty base URLs => not configured.
    main_model_base_url: str = ""
    main_model_id: str = ""
    main_model_api_key: str = ""
    council_model_base_url: str = ""
    council_model_id: str = ""
    council_model_api_key: str = ""
    inference_mode: InferenceMode = InferenceMode.mock

    fallback_enabled: bool = False
    fallback_model_base_url: str = ""
    fallback_model_api_key: str = ""

    # Runtime security
    security_mode: SecurityMode = SecurityMode.mock
    security_base_url: str = ""
    security_api_key: str = ""
    security_fail_closed: bool = True
    openshell_policy_path: str = "policies/openshell.yaml"

    # Telemetry sources
    prometheus_base_url: str = ""
    kubernetes_mode: KubernetesMode = KubernetesMode.fixture

    # Durable state
    state_db_path: str = ".athena/athena.db"

    # ---- Derived helpers -------------------------------------------------

    @property
    def fixtures_allowed(self) -> bool:
        """Fixture-driven heartbeats are development/demo only."""
        return self.env in (AthenaEnv.development, AthenaEnv.demo)

    @property
    def is_production(self) -> bool:
        return self.env is AthenaEnv.production

    @property
    def inference_is_mock(self) -> bool:
        return self.inference_mode is InferenceMode.mock

    @property
    def security_is_mock(self) -> bool:
        return self.security_mode is SecurityMode.mock

    @property
    def state_db_file(self) -> Path:
        return Path(self.state_db_path)

    def inference_ready(self) -> bool:
        """Mock inference is always ready; live needs a base URL + model id."""
        if self.inference_is_mock:
            return True
        return bool(self.main_model_base_url and self.main_model_id)

    def security_ready(self) -> bool:
        """Mock gateway is always ready; live needs a base URL."""
        if self.security_is_mock:
            return True
        return bool(self.security_base_url)

    def sanitized_status(self) -> dict:
        """Operator-facing status with no secrets or full provider URLs."""
        return {
            "env": self.env.value,
            "log_level": self.log_level,
            "inference_mode": self.inference_mode.value,
            "security_mode": self.security_mode.value,
            "kubernetes_mode": self.kubernetes_mode.value,
            "fixtures_allowed": self.fixtures_allowed,
            "security_fail_closed": self.security_fail_closed,
            "fallback_enabled": self.fallback_enabled,
            "providers": {
                "main_model_configured": bool(self.main_model_base_url),
                "council_model_configured": bool(self.council_model_base_url),
                "prometheus_configured": bool(self.prometheus_base_url),
                "security_configured": bool(self.security_base_url),
            },
            "budgets": {
                "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
                "max_specialists": self.max_specialists,
                "max_total_tokens": self.max_total_tokens,
                "max_tool_calls": self.max_tool_calls,
                "investigation_deadline_seconds": self.investigation_deadline_seconds,
            },
            "degraded_mode": self.inference_is_mock or self.security_is_mock,
        }


@lru_cache
def get_settings() -> Settings:
    """Process-wide cached settings. Clear with ``get_settings.cache_clear()``."""
    return Settings()
