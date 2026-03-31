import logging
import time
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


logger = logging.getLogger("api.request")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request with security-relevant metadata and timing."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid4().hex[:16]
        request.state.request_id = request_id

        start = time.perf_counter()
        method = request.method
        path = request.url.path
        query = request.url.query
        client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (
            request.client.host if request.client else "unknown"
        )
        user_agent = request.headers.get("User-Agent", "")

        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.exception(
                "request_unhandled_exception",
                extra={
                    "event": "request_error",
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "query": query,
                    "client_ip": client_ip,
                    "user_agent": user_agent,
                    "duration_ms": elapsed_ms,
                    "status_code": 500,
                    "suspicious": True,
                },
            )
            raise

        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = f"{elapsed_ms / 1000:.4f}"

        suspicious = status_code in {401, 403, 429} or ("/admin" in path and status_code >= 400)
        level = logging.WARNING if suspicious else logging.INFO
        if elapsed_ms > 1000:
            level = max(level, logging.WARNING)

        logger.log(
            level,
            "request_completed",
            extra={
                "event": "request",
                "request_id": request_id,
                "method": method,
                "path": path,
                "query": query,
                "status_code": status_code,
                "duration_ms": elapsed_ms,
                "client_ip": client_ip,
                "user_agent": user_agent,
                "suspicious": suspicious,
            },
        )

        return response
