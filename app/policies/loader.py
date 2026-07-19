"""Load and validate the versioned OpenShell policy YAML."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .models import OpenShellPolicy

_DEFAULT_POLICY_PATH = "policies/openshell.yaml"


def load_openshell_policy(path: str | Path | None = None) -> OpenShellPolicy:
    """Parse ``policies/openshell.yaml`` into a validated model."""

    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - env dependent
        raise RuntimeError("PyYAML is required to load the OpenShell policy") from exc

    policy_path = Path(path or _DEFAULT_POLICY_PATH)
    if not policy_path.exists():
        raise FileNotFoundError(f"OpenShell policy not found: {policy_path}")
    data = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    return OpenShellPolicy.model_validate(data)


@lru_cache(maxsize=8)
def load_openshell_policy_cached(path: str = _DEFAULT_POLICY_PATH) -> OpenShellPolicy:
    return load_openshell_policy(path)
