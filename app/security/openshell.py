"""OpenShell capability-enforcement engine (FR-11).

Evaluates ``requested action + destination -> allow | deny | require_approval``.

This engine is INTENTIONALLY independent of HiddenLayer trust: it receives no
trust score and no content verdict, only a structural capability request. That
independence is the whole point of running two enforcement layers (PRD §6.6):
HiddenLayer decides whether content should be trusted; OpenShell decides
whether an action is technically permitted.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from ..policies.loader import load_openshell_policy
from ..policies.models import (
    OpenShellPolicy,
    PolicyDecision,
    PolicyEffect,
    PolicyRequest,
)
from .events import SanitizedDenial, SecurityLedger

_STRICTNESS = {PolicyEffect.ALLOW: 0, PolicyEffect.REQUIRE_APPROVAL: 1, PolicyEffect.DENY: 2}


def _strictest(a: PolicyEffect, b: PolicyEffect) -> PolicyEffect:
    return a if _STRICTNESS[a] >= _STRICTNESS[b] else b


class OpenShellEngine:
    """Deterministic policy evaluator over :class:`OpenShellPolicy`."""

    def __init__(self, policy: OpenShellPolicy, ledger: SecurityLedger | None = None) -> None:
        self._policy = policy
        self._ledger = ledger

    @classmethod
    def from_path(
        cls, path: str | None = None, ledger: SecurityLedger | None = None
    ) -> "OpenShellEngine":
        return cls(load_openshell_policy(path), ledger)

    @property
    def policy_version(self) -> str:
        return self._policy.version

    def readiness(self) -> dict:
        return {
            "component": "openshell",
            "policy_version": self._policy.version,
            "roles": sorted(self._policy.roles.keys()),
            "healthy": True,
        }

    # -- evaluation --------------------------------------------------------

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        decision = self._evaluate(request)
        if decision.effect is not PolicyEffect.ALLOW and self._ledger is not None:
            self._ledger.record_denial(
                SanitizedDenial(
                    denial_id=self._ledger.next_denial_id(),
                    cycle_id=request.cycle_id,
                    role=request.role,
                    action=request.action,
                    effect=decision.effect.value,
                    category=decision.category,
                    resource_category=decision.resource_category,
                    reason=decision.reason,
                )
            )
        return decision

    def _evaluate(self, request: PolicyRequest) -> PolicyDecision:
        version = self._policy.version

        # 1. Protected-path check (hard deny, independent of role/action label).
        if request.resource is not None:
            path_effect = self._classify_path(request.resource)
            if path_effect is not None:
                label, reason = path_effect
                return PolicyDecision(
                    effect=PolicyEffect.DENY,
                    reason=reason,
                    category="protected_path",
                    resource_category=label,
                    matched_rule="protected_paths",
                    policy_version=version,
                )

        # 2. Network egress check (hard deny for unapproved destinations).
        if request.destination is not None:
            net = self._classify_destination(request.destination)
            if net is PolicyEffect.DENY:
                return PolicyDecision(
                    effect=PolicyEffect.DENY,
                    reason="Egress to an unapproved destination is denied.",
                    category="network_egress",
                    resource_category="unapproved_destination",
                    matched_rule="network.allow_destinations",
                    policy_version=version,
                )
            net_effect = net
        else:
            net_effect = PolicyEffect.ALLOW

        # 3. Category effect for the requested capability.
        category_effect, category_label = self._classify_action(request.action)
        if category_effect is PolicyEffect.DENY:
            return PolicyDecision(
                effect=PolicyEffect.DENY,
                reason=f"Capability '{request.action}' is in the deny category.",
                category=category_label,
                resource_category="none",
                matched_rule="categories.deny",
                policy_version=version,
            )

        # 4. Role least-privilege.
        role_effect = self._classify_role(request.role, request.action)

        effect = _strictest(_strictest(category_effect, role_effect), net_effect)
        if effect is PolicyEffect.DENY:
            reason = (
                f"Role '{request.role}' is not granted capability '{request.action}'."
                if role_effect is PolicyEffect.DENY
                else f"Capability '{request.action}' is denied."
            )
        elif effect is PolicyEffect.REQUIRE_APPROVAL:
            reason = f"Capability '{request.action}' requires human approval."
        else:
            reason = f"Capability '{request.action}' is permitted for role '{request.role}'."

        return PolicyDecision(
            effect=effect,
            reason=reason,
            category=category_label,
            resource_category="approved_destination" if request.destination else "none",
            matched_rule="roles" if effect is not PolicyEffect.ALLOW else "categories.allow",
            policy_version=version,
        )

    # -- classifiers -------------------------------------------------------

    def _classify_path(self, resource: str) -> tuple[str, str] | None:
        """Return (resource_category, reason) if the path is protected."""

        raw = resource.strip()
        expanded = os.path.expanduser(raw)
        candidates = {raw, expanded, expanded.replace("\\", "/")}
        try:
            candidates.add(os.path.normpath(expanded))
        except ValueError:
            pass
        # Basenames let bare-filename patterns (e.g. ".env") match a path that
        # carries no directory prefix.
        basenames = {os.path.basename(c.replace("\\", "/")) for c in candidates}

        for pattern in self._policy.protected_paths:
            # Variants so "**/x" also matches a bare "x" with no directory part.
            variants = {pattern, os.path.expanduser(pattern)}
            for var in list(variants):
                if var.startswith("**/"):
                    variants.add(var[3:])
            for var in variants:
                for cand in candidates:
                    if fnmatch.fnmatch(cand.replace("\\", "/"), var):
                        return _resource_label(cand.replace("\\", "/")), "Access to a protected path is denied."
                for base in basenames:
                    if fnmatch.fnmatch(base, var):
                        return _resource_label(raw.replace("\\", "/")), "Access to a protected path is denied."

        # Home-directory / outside-workspace reads that dodged explicit patterns.
        if raw.startswith("~"):
            return "home_directory", "Arbitrary home-directory access is denied."
        if self._escapes_workspace(expanded):
            return "outside_workspace", "Access outside the agent workspace is denied."
        return None

    def _escapes_workspace(self, expanded_path: str) -> bool:
        if not os.path.isabs(expanded_path):
            # Relative path with parent traversal escaping the workspace root.
            root = Path(self._policy.workspace_root).resolve()
            target = (root / expanded_path).resolve()
            return root not in target.parents and target != root
        root = Path(self._policy.workspace_root).resolve()
        target = Path(expanded_path).resolve()
        return root not in target.parents and target != root

    def _classify_destination(self, destination: str) -> PolicyEffect:
        host = _host_of(destination)
        for pattern in self._policy.network.approval_gated_destinations:
            if _host_match(host, pattern):
                return PolicyEffect.REQUIRE_APPROVAL
        for pattern in self._policy.network.allow_destinations:
            if _host_match(host, pattern):
                return PolicyEffect.ALLOW
        return PolicyEffect.DENY

    def _classify_action(self, action: str) -> tuple[PolicyEffect, str]:
        cats = self._policy.categories
        if action in cats.deny:
            return PolicyEffect.DENY, action
        if action in cats.approval_gated:
            return PolicyEffect.REQUIRE_APPROVAL, action
        if action in cats.allow:
            return PolicyEffect.ALLOW, action
        # Unknown capability -> deny by default (closed policy surface).
        return PolicyEffect.DENY, "unknown_capability"

    def _classify_role(self, role: str, action: str) -> PolicyEffect:
        role_policy = self._policy.roles.get(role)
        if role_policy is None:
            return PolicyEffect.DENY
        if action in role_policy.allow:
            return PolicyEffect.ALLOW
        if action in role_policy.approval_gated:
            return PolicyEffect.REQUIRE_APPROVAL
        return PolicyEffect.DENY


def _host_of(destination: str) -> str:
    dest = destination.strip()
    if "://" in dest:
        dest = dest.split("://", 1)[1]
    dest = dest.split("/", 1)[0]
    dest = dest.rsplit("@", 1)[-1]  # strip any userinfo
    dest = dest.split(":", 1)[0]  # strip port
    return dest.lower()


def _host_match(host: str, pattern: str) -> bool:
    pattern = pattern.lower()
    return host == pattern or fnmatch.fnmatch(host, pattern) or host.endswith("." + pattern)


def _resource_label(path: str) -> str:
    lowered = path.lower()
    if ".aws/credentials" in lowered or ".aws/config" in lowered:
        return "aws_credential_path"
    if ".ssh" in lowered or lowered.endswith("_rsa") or lowered.endswith(".pem") or lowered.endswith(".key"):
        return "ssh_key_path"
    if "/etc/shadow" in lowered or "/etc/passwd" in lowered:
        return "system_credential_path"
    if ".env" in lowered:
        return "env_file"
    if "secret" in lowered or "credential" in lowered:
        return "secret_store_path"
    if lowered.startswith("~") or "/home/" in lowered or "/root/" in lowered:
        return "home_directory"
    return "protected_path"
