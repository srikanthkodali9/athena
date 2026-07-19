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


def _val(value: str | None) -> str | None:
    """Sanitize an env value; return ``None`` for empty or comment-only input.

    A blank ``.env`` line with a trailing inline comment (``KEY=   # note``) can
    reach us with the comment as the value depending on the dotenv parser. Such
    a value must NOT be handed to the HiddenLayer client as a real base_url /
    auth_url / token, or construction fails and every inspection silently falls
    back to fail-closed. No real credential or URL begins with ``#``.
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped or stripped.startswith("#"):
        return None
    return stripped


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
            mode=(_val(env.get("ATHENA_SECURITY_MODE")) or "mock").lower(),
            environment=(_val(env.get("ATHENA_SECURITY_ENVIRONMENT")) or "prod-us"),
            client_id=_val(env.get("ATHENA_SECURITY_CLIENT_ID")),
            client_secret=_val(env.get("ATHENA_SECURITY_CLIENT_SECRET")),
            auth_url=_val(env.get("ATHENA_SECURITY_AUTH_URL")),
            base_url=_val(env.get("ATHENA_SECURITY_BASE_URL")),
            bearer_token=_val(env.get("ATHENA_SECURITY_TOKEN")),
            fail_closed=_as_bool(env.get("ATHENA_SECURITY_FAIL_CLOSED"), True),
            openshell_policy_path=(
                _val(env.get("ATHENA_OPENSHELL_POLICY_PATH")) or "policies/openshell.yaml"
            ),
        )
