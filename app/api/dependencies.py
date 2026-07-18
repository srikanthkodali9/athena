"""Application wiring and FastAPI dependencies.

Builds the durable store, tool registry, inference + security ports, and the
heartbeat service from settings, and exposes them via ``request.app.state``.

Two Wave-2 seams live here:

* Live-mode config bridge — the resolved :class:`Settings` (which loads ``.env``
  via pydantic) is the single authority for security/inference MODE, and the
  ``.env`` values are folded into the env dict :class:`SecurityConfig` reads, so
  the gateway never runs mock while ``.env`` says live.
* Inference selection — ``ATHENA_INFERENCE_MODE`` picks ``MockInference`` or the
  live ``TieredInferenceRouter`` (imported lazily/guarded; falls back to mock
  with a logged warning if it is not present yet).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values
from fastapi import Request

from app.athena.config import InferenceMode, Settings, get_settings
from app.athena.ports import InferencePort, MockInference, SecurityGateway
from app.heartbeat.lifecycle import HeartbeatService
from app.learning.retrieval import SkillRetriever
from app.learning.service import LearningService
from app.learning.store import LearningStore
from app.memory.store import CycleStore
from app.security.config import SecurityConfig
from app.security.gateway import HiddenLayerGateway
from app.security.openshell import OpenShellEngine
from app.tools.registry import ToolRegistry, build_default_registry

logger = logging.getLogger("athena.dependencies")


@dataclass
class AppContext:
    settings: Settings
    store: CycleStore
    inference: InferencePort
    security: SecurityGateway
    registry: ToolRegistry
    heartbeat: HeartbeatService
    openshell: Optional[OpenShellEngine] = None
    learning: Optional[LearningService] = None
    learning_store: Optional[LearningStore] = None
    retriever: Optional[SkillRetriever] = None

    def close(self) -> None:
        self.store.close()
        if self.learning_store is not None:
            self.learning_store.close()


def build_context(settings: Settings | None = None) -> AppContext:
    settings = settings or get_settings()
    store = CycleStore(settings.state_db_path)

    inference = _select_inference(settings)

    # Real runtime-security gateway is always in the path (HiddenLayer). It
    # resolves to the deterministic local adapter in mock mode and the live
    # HiddenLayer endpoint when the resolved settings say live — no route or
    # service change needed. The ledger is shared with OpenShell so denials and
    # findings surface through one sanitized projection.
    security = HiddenLayerGateway.from_config(_resolve_security_config(settings))
    openshell = _build_openshell(settings, security)

    # Recursive learning over the SAME SQLite file (its own tables). The learning
    # service's SKILL_WRITE inspection runs through the SAME security gateway.
    learning_store = LearningStore(settings.state_db_path)
    learning = LearningService(
        store=learning_store, security=security, skills_dir="skills"
    )
    retriever = SkillRetriever(learning_store)

    registry = build_default_registry()
    heartbeat = HeartbeatService(
        store=store,
        inference=inference,
        security=security,
        registry=registry,
        settings=settings,
        learning=learning,
        retriever=retriever,
    )
    return AppContext(
        settings=settings,
        store=store,
        inference=inference,
        security=security,
        registry=registry,
        heartbeat=heartbeat,
        openshell=openshell,
        learning=learning,
        learning_store=learning_store,
        retriever=retriever,
    )


def get_context(request: Request) -> AppContext:
    return request.app.state.context


# ---------------------------------------------------------------------------
# Live-mode config bridge (task 1)
# ---------------------------------------------------------------------------


def _merged_env(settings: Settings) -> dict[str, str]:
    """Env dict the security config reads, matching what pydantic resolved.

    Starts from ``os.environ``, folds in the ``.env`` file the Settings class is
    configured to read (so live OAuth creds are visible), then makes the RESOLVED
    Settings authoritative for the core-owned security keys. Because MODE comes
    from ``settings``, an explicit ``security_mode="mock"`` in a test can never be
    overridden into live by a stray ``.env`` — test isolation is preserved while
    a real live run sees consistent mode + credentials.
    """
    env: dict[str, str] = dict(os.environ)

    dotenv_path = settings.model_config.get("env_file")
    if dotenv_path and Path(str(dotenv_path)).exists():
        for key, value in dotenv_values(str(dotenv_path)).items():
            if value is not None:
                env.setdefault(key, value)

    # Resolved Settings wins for the fields core owns.
    env["ATHENA_SECURITY_MODE"] = settings.security_mode.value
    env["ATHENA_SECURITY_FAIL_CLOSED"] = "true" if settings.security_fail_closed else "false"
    env["ATHENA_OPENSHELL_POLICY_PATH"] = settings.openshell_policy_path
    if settings.security_base_url:
        env["ATHENA_SECURITY_BASE_URL"] = settings.security_base_url
    return env


def _resolve_security_config(settings: Settings) -> SecurityConfig:
    return SecurityConfig.from_env(_merged_env(settings))


def _build_openshell(
    settings: Settings, security: HiddenLayerGateway
) -> Optional[OpenShellEngine]:
    """Capability engine sharing the gateway ledger so denials are queryable."""
    ledger = getattr(security, "ledger", None)
    try:
        return OpenShellEngine.from_path(settings.openshell_policy_path, ledger=ledger)
    except Exception as exc:  # noqa: BLE001 - policy missing/invalid is non-fatal here
        logger.warning("OpenShell policy unavailable (%s); denials route will be empty", exc)
        return None


# ---------------------------------------------------------------------------
# Inference selection seam (task 2)
# ---------------------------------------------------------------------------


def _select_inference(settings: Settings) -> InferencePort:
    """Pick the inference implementation by ATHENA_INFERENCE_MODE.

    ``mock`` -> deterministic MockInference. ``live`` -> the tiered router
    delivered by inference_engineer at ``app/routing/router.py``. The import and
    construction are guarded so a missing/failing router degrades to mock with a
    logged warning instead of crashing the app. The coordinator finalizes the
    router wiring at integration.
    """
    if settings.inference_mode is not InferenceMode.live:
        return MockInference()

    try:
        from app.routing.router import TieredInferenceRouter  # type: ignore
    except Exception as exc:  # noqa: BLE001 - module may not exist yet
        logger.warning(
            "ATHENA_INFERENCE_MODE=live but inference router unavailable (%s); "
            "falling back to MockInference",
            exc,
        )
        return MockInference()

    try:
        return TieredInferenceRouter.from_env()  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - init/creds failure -> degrade safely
        logger.warning(
            "live inference router init failed (%s); falling back to MockInference",
            exc,
        )
        return MockInference()
