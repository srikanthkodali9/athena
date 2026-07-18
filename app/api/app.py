"""FastAPI application factory and local entry point.

Run with the console script (``athena``), ``python -m app.api.app``, or
``uvicorn "app.api.app:create_app" --factory``.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.dependencies import build_context
from app.api.errors import AthenaHTTPError, athena_error_handler, error_body
from app.api.routes import heartbeats, system
from app.athena.config import Settings, get_settings


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    app.state.context = build_context(settings)
    try:
        yield
    finally:
        app.state.context.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="Athena",
        version="0.0.0",
        description="Heartbeat-driven SRE intelligence (Wave 1 core spine).",
        lifespan=_lifespan,
    )
    app.state.settings = settings

    @app.middleware("http")
    async def _request_id_mw(request: Request, call_next):
        request.state.request_id = "req_" + uuid.uuid4().hex[:10]
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    app.add_exception_handler(AthenaHTTPError, athena_error_handler)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "req_unknown")
        return JSONResponse(
            status_code=500,
            content=error_body(
                request_id=request_id,
                code="INTERNAL_ERROR",
                message="An internal error occurred.",
            ),
        )

    app.include_router(system.router)
    app.include_router(heartbeats.router)
    return app


def run() -> None:
    """Console-script entry point: launch uvicorn."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.api.app:create_app",
        host=settings.host,
        port=settings.port,
        factory=True,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
