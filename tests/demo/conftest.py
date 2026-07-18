"""Demo-test fixtures: a demo app + client in deterministic mock mode."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.athena.config import AthenaEnv, Settings
from app.ui.server import create_demo_app


def demo_settings(tmp_path, **overrides) -> Settings:
    values = dict(
        env=AthenaEnv.demo,
        state_db_path=str(tmp_path / "athena.db"),
        inference_mode="mock",
        security_mode="mock",
    )
    values.update(overrides)
    # _env_file=None keeps a stray local .env out of the deterministic run.
    return Settings(_env_file=None, **values)


@pytest.fixture
def demo_client(tmp_path):
    settings = demo_settings(tmp_path)
    app = create_demo_app(settings=settings)
    with TestClient(app) as client:
        yield client
