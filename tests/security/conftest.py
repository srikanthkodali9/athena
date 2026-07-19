"""Shared fixtures for security tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.security.adapters import (
    HiddenLayerVerdict,
    InspectionUnavailable,
    LocalHiddenLayerAdapter,
)
from app.security.config import SecurityConfig
from app.security.contracts import FindingType, InspectionStage, Severity
from app.security.gateway import HiddenLayerGateway, SourceTrustStore
from app.security.openshell import OpenShellEngine

REPO_ROOT = Path(__file__).resolve().parents[2]
ATTACKS_DIR = REPO_ROOT / "fixtures" / "attacks"
POLICY_PATH = REPO_ROOT / "policies" / "openshell.yaml"


@pytest.fixture
def config() -> SecurityConfig:
    return SecurityConfig(mode="mock", fail_closed=True)


@pytest.fixture
def trust_store() -> SourceTrustStore:
    return SourceTrustStore()


@pytest.fixture
def gateway(config, trust_store) -> HiddenLayerGateway:
    return HiddenLayerGateway(LocalHiddenLayerAdapter(), config, trust_store=trust_store)


class UnavailableAdapter:
    """Adapter that always fails, simulating a HiddenLayer outage."""

    name = "hiddenlayer-live"
    is_mock = False

    def inspect_content(self, stage: InspectionStage, content: str, metadata: dict) -> HiddenLayerVerdict:
        raise InspectionUnavailable("simulated outage")

    def health(self) -> bool:
        return False


@pytest.fixture
def unavailable_gateway() -> HiddenLayerGateway:
    return HiddenLayerGateway(
        UnavailableAdapter(),
        SecurityConfig(mode="live", fail_closed=True),
    )


@pytest.fixture
def engine() -> OpenShellEngine:
    return OpenShellEngine.from_path(str(POLICY_PATH))


@pytest.fixture
def poisoned_runbook_text() -> str:
    return (ATTACKS_DIR / "poisoned_runbook.md").read_text(encoding="utf-8")
