"""Standard sanitized error envelope (api-endpoints.md).

Client-facing errors never leak secrets, protected paths, or full provider URLs.
Full diagnostics belong in the protected audit record, not the HTTP response.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import Request
from fastapi.responses import JSONResponse


class AthenaHTTPError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        retryable: bool = False,
        details: Optional[dict[str, Any]] = None,
        cycle_id: Optional[str] = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        self.cycle_id = cycle_id
        super().__init__(message)


def error_body(
    *,
    request_id: str,
    code: str,
    message: str,
    retryable: bool = False,
    details: Optional[dict[str, Any]] = None,
    cycle_id: Optional[str] = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "request_id": request_id,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details or {},
        },
    }
    if cycle_id is not None:
        body["cycle_id"] = cycle_id
    return body


async def athena_error_handler(request: Request, exc: AthenaHTTPError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "req_unknown")
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(
            request_id=request_id,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
            details=exc.details,
            cycle_id=exc.cycle_id,
        ),
    )
