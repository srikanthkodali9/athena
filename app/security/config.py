"""Security configuration surface.

Reads the canonical env vars declared in ``.env.example``. Secrets come from
``.env`` (git-ignored) only — nothing here is committed with a real value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SecurityConfig:
    """Resolved security configuration (HiddenLayer + OpenShell).

    HiddenLayer authenticates via OAuth2 client credentials
    (``client_id``/``client_secret``) or a ``bearer_token`` fallback, against a
    SaaS ``environment`` (prod-us / prod-eu) or a self-hosted ``base_url``.
    Secret-bearing fields are excluded from ``repr()`` so they never leak into
    logs or tracebacks.
    """

    mode: str = "mock"  # "live" | "mock"
    # HiddenLayer SaaS region ("prod-us" | "prod-eu") or "self-hosted".
    environment: str = "prod-us"
    # OAuth2 client credentials (preferred).
    client_id: str | None = field(default=None, repr=False)
    client_secret: str | None = field(default=None, repr=False)
    # Optional overrides.
    auth_url: str | None = None  # default https://auth.hiddenlayer.ai
    base_url: str | None = None  # self-hosted AISec API base URL
    bearer_token: str | None = field(default=None, repr=False)  # token fallback
    fail_closed: bool = True
    openshell_policy_path: str = "policies/openshell.yaml"
    request_timeout_seconds: float = 5.0

    @property
    def is_live(self) -> bool:
        return self.mode.strip().lower() == "live"

    @property
    def has_credentials(self) -> bool:
        return bool((self.client_id and self.client_secret) or self.bearer_token)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "SecurityConfig":
        env = env if env is not None else dict(os.environ)
        return cls(
            mode=(env.get("ATHENA_SECURITY_MODE") or "mock").strip().lower(),
            environment=(env.get("ATHENA_SECURITY_ENVIRONMENT") or "prod-us").strip(),
            client_id=(env.get("ATHENA_SECURITY_CLIENT_ID") or None),
            client_secret=(env.get("ATHENA_SECURITY_CLIENT_SECRET") or None),
            auth_url=(env.get("ATHENA_SECURITY_AUTH_URL") or None),
            base_url=(env.get("ATHENA_SECURITY_BASE_URL") or None),
            bearer_token=(env.get("ATHENA_SECURITY_TOKEN") or None),
            fail_closed=_as_bool(env.get("ATHENA_SECURITY_FAIL_CLOSED"), True),
            openshell_policy_path=(
                env.get("ATHENA_OPENSHELL_POLICY_PATH") or "policies/openshell.yaml"
            ),
        )
