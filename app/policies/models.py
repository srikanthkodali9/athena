"""Typed models for the OpenShell capability policy."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class PolicyEffect(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class RolePolicy(BaseModel):
    allow: list[str] = Field(default_factory=list)
    approval_gated: list[str] = Field(default_factory=list)


class CategoryPolicy(BaseModel):
    allow: list[str] = Field(default_factory=list)
    approval_gated: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class NetworkPolicy(BaseModel):
    allow_destinations: list[str] = Field(default_factory=list)
    approval_gated_destinations: list[str] = Field(default_factory=list)


class OpenShellPolicy(BaseModel):
    """Versioned sandbox policy (FR-11-01)."""

    version: str
    description: str = ""
    workspace_root: str = "."
    protected_paths: list[str] = Field(default_factory=list)
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    categories: CategoryPolicy = Field(default_factory=CategoryPolicy)
    roles: dict[str, RolePolicy] = Field(default_factory=dict)

    model_config = {"frozen": True}


class PolicyRequest(BaseModel):
    """A requested capability + its destination, evaluated by the engine.

    Deliberately carries NO trust score or content — capability enforcement is
    independent of HiddenLayer trust.
    """

    role: str
    action: str = Field(..., description="Capability name, e.g. read_repository.")
    resource: str | None = Field(None, description="Filesystem path, if any.")
    destination: str | None = Field(None, description="Network host/URL, if any.")
    cycle_id: str | None = None

    model_config = {"frozen": True}


class PolicyDecision(BaseModel):
    """Result of evaluating a :class:`PolicyRequest`."""

    effect: PolicyEffect
    reason: str
    category: str = "uncategorized"
    resource_category: str = "none"
    matched_rule: str | None = None
    policy_version: str | None = None

    @property
    def allowed(self) -> bool:
        return self.effect is PolicyEffect.ALLOW
