import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from dotenv import load_dotenv

from app.config import settings
from app.routes import (
    health, auth, property, room, tenant, bed, 
    dashboard, staff, payment
)
from app.utils.rate_limit import limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from app.utils.exception_handlers import add_global_exception_handlers
from app.middleware.user_context import UserContextMiddleware
from app.middleware.request_logging import RequestLoggingMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.database.init_db import ensure_mongodb_connection, ensure_indexes
from app.utils.scheduler import setup_scheduler
from app.utils.logging_config import setup_logging
from app.config import settings

# Initialize environment variables
load_dotenv()

# Configure logging
setup_logging()
logger = logging.getLogger(__name__)

# Ensure 'static' directory exists
static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(static_dir, exist_ok=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Database Connection and Initialization
    mongo_error = await ensure_mongodb_connection()
    if mongo_error:
        logger.error(mongo_error)
        os._exit(1)

    await ensure_indexes()
    
    # 2. Initialize Background Scheduler
    scheduler = setup_scheduler(app)
    
    yield
    
    # Shutdown scheduler
    scheduler.shutdown()
    logger.info("✓ Background scheduler shut down")

app = FastAPI(title="Hostel API", lifespan=lifespan)

# --- Middlewares ---

# Enable response compression
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Performance and Context Middlewares
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(UserContextMiddleware)

# Security Middlewares
if settings.ENFORCE_HTTPS:
    app.add_middleware(HTTPSRedirectMiddleware)

# Security headers (X-Frame-Options, CSP, etc.)
app.add_middleware(SecurityHeadersMiddleware)

# Rate Limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, lambda request, exc: JSONResponse(
    status_code=429, 
    content={"detail": "Too many requests. Please try again later."}
))
app.add_middleware(SlowAPIMiddleware)

# CORS Configuration
if not settings.ALLOWED_ORIGINS:
    allowed_origins = ["*"]
else:
    allowed_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
    if not allowed_origins:
        allowed_origins = ["*"]

allow_credentials = settings.ALLOW_CREDENTIALS
allow_local_origins = settings.ALLOW_LOCAL_ORIGINS
local_dev_origin_regex = settings.LOCAL_DEV_ORIGIN_REGEX

cors_kwargs = {
    "allow_origins": allowed_origins,
    "allow_credentials": allow_credentials,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}

if allow_local_origins:
    cors_kwargs["allow_origin_regex"] = local_dev_origin_regex

app.add_middleware(CORSMiddleware, **cors_kwargs)

# --- Static Files ---
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# --- Routers ---
API_PREFIX = f"/api/{settings.API_VERSION}"

app.include_router(health.router, prefix=API_PREFIX)
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(property.router, prefix=API_PREFIX)
app.include_router(room.router, prefix=API_PREFIX)
app.include_router(tenant.router, prefix=API_PREFIX)
app.include_router(bed.router, prefix=API_PREFIX)
app.include_router(staff.router, prefix=API_PREFIX)
app.include_router(payment.router, prefix=API_PREFIX)
app.include_router(dashboard.router, prefix=API_PREFIX)

@app.get("/", tags=["root"])
async def root():
    return {
        "message": "Hostel API is running",
        "version": settings.API_VERSION,
        "environment": settings.ENV
    }

# Global Exception Handlers
add_global_exception_handlers(app)
