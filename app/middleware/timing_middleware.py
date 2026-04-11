import time
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("api.timing")

class TimingMiddleware(BaseHTTPMiddleware):
    """Middleware to track request processing time and log slow requests"""
    
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time
        
        # Log slow requests (> 1 second) for performance monitoring
        if process_time > 1.0:
            logger.warning(
                f"SLOW REQUEST: {request.method} {request.url.path} "
                f"took {process_time:.2f}s"
            )
        
        # Add timing header for debugging
        response.headers["X-Process-Time"] = f"{process_time:.4f}"
        return response
