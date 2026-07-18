"""Application wiring and FastAPI dependencies.

Builds the durable store, tool registry, ports, and heartbeat service from
settings, and exposes them via ``request.app.state``. Mock ports are used until
the inference and security adapters are delivered by their owning agents.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.athena.config import Settings, get_settings
from app.athena.ports import (
    InferencePort,
    MockInference,
    SecurityGateway,
)
from app.heartbeat.lifecycle import HeartbeatService
from app.memory.store import CycleStore
from app.security.config import SecurityConfig
from app.security.gateway import HiddenLayerGateway
from app.tools.registry import ToolRegistry, build_default_registry


@dataclass
class AppContext:
    settings: Settings
    store: CycleStore
    inference: InferencePort
    security: SecurityGateway
    registry: ToolRegistry
    heartbeat: HeartbeatService

    def close(self) -> None:
        self.store.close()


def build_context(settings: Settings | None = None) -> AppContext:
    settings = settings or get_settings()
    store = CycleStore(settings.state_db_path)
    # Real runtime-security gateway is in the path (HiddenLayer). It resolves to
    # the deterministic local adapter in mock mode and the live HiddenLayer
    # endpoint when ATHENA_SECURITY_MODE=live — no route/service change needed.
    inference: InferencePort = MockInference()
    security: SecurityGateway = HiddenLayerGateway.from_config(SecurityConfig.from_env())
    registry = build_default_registry()
    heartbeat = HeartbeatService(
        store=store,
        inference=inference,
        security=security,
        registry=registry,
        settings=settings,
    )
    return AppContext(
        settings=settings,
        store=store,
        inference=inference,
        security=security,
        registry=registry,
        heartbeat=heartbeat,
    )


def get_context(request: Request) -> AppContext:
    return request.app.state.context
