"""Security headers middleware for production-ready HTTP responses"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from app.config import settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses"""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Only add security headers in production or if explicitly enabled
        if settings.ENV.lower() == "production" or settings.ENFORCE_HTTPS:
            # Prevent MIME type sniffing
            response.headers["X-Content-Type-Options"] = "nosniff"

            # Prevent clickjacking
            response.headers["X-Frame-Options"] = "DENY"

            # Enable XSS filter in browsers
            response.headers["X-XSS-Protection"] = "1; mode=block"

            # Control referrer information
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

            # Content Security Policy
            csp = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "font-src 'self'; "
                "connect-src 'self'; "
                "frame-ancestors 'none';"
            )
            response.headers["Content-Security-Policy"] = csp

            # Cache control for sensitive endpoints
            path = request.url.path
            if any(path.startswith(p) for p in ["/api/", "/auth/"]):
                response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"

            # Remove server banner
            response.headers["Server"] = "HostelAPI"

        return response
