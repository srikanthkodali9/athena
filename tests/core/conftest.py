"""Shared fixtures for core tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.athena.config import AthenaEnv, Settings
from app.athena.ports import MockInference, NoOpSecurityGateway
from app.heartbeat.lifecycle import HeartbeatService
from app.memory.store import CycleStore
from app.tools.registry import build_default_registry


@dataclass
class Harness:
    settings: Settings
    store: CycleStore
    inference: MockInference
    security: NoOpSecurityGateway
    service: HeartbeatService


def make_settings(tmp_path, **overrides) -> Settings:
    values = dict(
        env=AthenaEnv.development,
        state_db_path=str(tmp_path / "athena.db"),
        inference_mode="mock",
        security_mode="mock",
    )
    values.update(overrides)
    # _env_file=None prevents a stray local .env from bleeding into tests.
    return Settings(_env_file=None, **values)


@pytest.fixture
def harness(tmp_path) -> Harness:
    settings = make_settings(tmp_path)
    store = CycleStore(settings.state_db_path)
    inference = MockInference()
    security = NoOpSecurityGateway()
    service = HeartbeatService(
        store=store,
        inference=inference,
        security=security,
        registry=build_default_registry(),
        settings=settings,
    )
    try:
        yield Harness(settings, store, inference, security, service)
    finally:
        store.close()
