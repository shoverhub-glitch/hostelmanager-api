from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.database.mongodb import db
from bson import ObjectId
from bson.errors import InvalidId
from app.utils.ownership import build_owner_query
from jose import jwt
from fastapi import HTTPException, status
from app.config import settings
import logging
import time
from jose import JWTError, ExpiredSignatureError
from starlette.responses import JSONResponse
from app.database.token_blacklist import is_token_blacklisted

logger = logging.getLogger(__name__)

# Simple in-process TTL cache for subscriptions to reduce DB round-trips
# Key: user_id, Value: (subscription_data, expiry_timestamp)
_subscription_cache = {}
_SUBSCRIPTION_CACHE_TTL = 60  # seconds

class UserContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        SECRET_KEY = settings.JWT_SECRET
        ALGORITHM = settings.JWT_ALGORITHM
        user_id = None
        user = None
        role = None
        property_ids = []
        subscription = None
        auth_header = request.headers.get("Authorization")
        request_id = getattr(request.state, "request_id", None)

        # Read public endpoints from environment variable PUBLIC_PATHS (comma-separated)
        public_paths = {p.strip() for p in settings.PUBLIC_PATHS.split(",") if p.strip()}
        v_prefix = f"/api/{settings.API_VERSION}"
        
        public_paths.update({
            "/",
            f"{v_prefix}/health",
            f"{v_prefix}/auth/login",
            f"{v_prefix}/auth/register",
            f"{v_prefix}/auth/email/send-otp",
            f"{v_prefix}/auth/email/verify-otp",
            f"{v_prefix}/auth/email/resend-otp",
            f"{v_prefix}/auth/forgot-password",
            f"{v_prefix}/auth/verify-reset-otp",
            f"{v_prefix}/auth/reset-password",
            f"{v_prefix}/auth/refresh",
            f"{v_prefix}/auth/logout",  # Logout only needs refresh token, not access token
            f"{v_prefix}/subscription/limits/free",  # Plan limits are public
            f"{v_prefix}/subscription/limits/pro",
            f"{v_prefix}/subscription/limits/premium",
            f"{v_prefix}/subscription/plans",  # Get all available plans
        })
        
        # Public path prefixes (for paths with dynamic segments)
        public_prefixes = [
            f"{v_prefix}/coupons/validate/",  # Coupon validation is public
        ]
        
        # Check exact path match or prefix match
        is_public = request.url.path in public_paths or any(
            request.url.path.startswith(prefix) for prefix in public_prefixes
        )

        # Safety guard: admin namespaces must never be public even if env is misconfigured.
        if request.url.path.startswith(f"{v_prefix}/admin") or request.url.path.startswith(f"{v_prefix}/coupons/admin"):
            is_public = False
        
        if is_public:
            # Allow public access, skip authentication
            response = await call_next(request)
            return response

        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]
            try:
                if await is_token_blacklisted(token):
                    logger.info(
                        "auth_blacklisted_token",
                        extra={"event": "auth_blacklisted_token", "path": request.url.path, "request_id": request_id},
                    )
                    return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "Token has been revoked"})

                payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                if payload.get("type") != "access":
                    logger.warning(
                        "auth_invalid_token_type",
                        extra={"event": "auth_invalid_token_type", "path": request.url.path, "request_id": request_id},
                    )
                    return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "Invalid token type"})

                user_id = payload.get("sub")
                iat = payload.get("iat")
                if user_id is None:
                    logger.warning(
                        "auth_jwt_missing_sub",
                        extra={"event": "auth_jwt_missing_sub", "path": request.url.path, "request_id": request_id},
                    )
                    return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "Invalid authentication credentials"})

                try:
                    user_obj_id = ObjectId(user_id)
                except (InvalidId, TypeError):
                    logger.warning(
                        "auth_invalid_user_id_format",
                        extra={"event": "auth_invalid_user_id_format", "path": request.url.path, "request_id": request_id},
                    )
                    return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "Invalid authentication credentials"})

                user = await db["users"].find_one({"_id": user_obj_id})
                if user:
                    if user.get("isDeleted") or user.get("isDisabled"):
                        logger.warning(
                            "auth_inactive_account",
                            extra={"event": "auth_inactive_account", "path": request.url.path, "request_id": request_id, "user_id": user_id},
                        )
                        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": "Account is not active"})

                    # SECURITY: Check if token was issued before the last significant account update (like password change)
                    if iat and user.get("updatedAt"):
                        updated_at = user["updatedAt"]
                        if isinstance(updated_at, str):
                            from datetime import datetime
                            try:
                                updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                            except Exception:
                                updated_at = None
                        
                        if updated_at and iat < int(updated_at.timestamp()):
                            logger.info(
                                "auth_stale_token",
                                extra={"event": "auth_stale_token", "user_id": user_id, "iat": iat, "updated_at": updated_at.timestamp()},
                            )
                            return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "Session invalidated due to account update. Please log in again."})

                    role = user.get("role")
                    # FIX: Add sane limit to property fetch
                    owned_properties = await db["properties"].find(
                        build_owner_query(user_id),
                        {"_id": 1}
                    ).to_list(length=500)
                    property_ids = [str(doc["_id"]) for doc in owned_properties]
                    # Sanitize user object (remove sensitive fields)
                    user = {k: v for k, v in user.items() if k not in ["password", "hashed_password"]}
                else:
                    logger.warning(
                        "auth_user_not_found",
                        extra={"event": "auth_user_not_found", "path": request.url.path, "request_id": request_id, "user_id": user_id},
                    )
                    return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "Invalid authentication credentials"})
            except ExpiredSignatureError:
                logger.info(
                    "auth_token_expired",
                    extra={"event": "auth_token_expired", "path": request.url.path, "request_id": request_id, "user_id": user_id},
                )
                return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "Your session has expired. Please log in again or refresh your token."})
            except JWTError as e:
                logger.warning(
                    "auth_jwt_error",
                    extra={"event": "auth_jwt_error", "path": request.url.path, "request_id": request_id, "error": str(e)},
                )
                return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "Invalid authentication credentials"})
            except Exception as e:
                logger.exception(
                    "auth_middleware_unexpected_error",
                    extra={"event": "auth_middleware_unexpected_error", "path": request.url.path, "request_id": request_id, "error": str(e)},
                )
                return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"detail": "Internal server error"})
        else:
            logger.warning(
                "auth_missing_header",
                extra={"event": "auth_missing_header", "path": request.url.path, "request_id": request_id},
            )
            return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "Missing or invalid Authorization header"})
        
        # Load subscription info with short-lived TTL cache
        if user_id:
            now = time.time()
            cached_sub, expiry = _subscription_cache.get(user_id, (None, 0))
            
            if cached_sub and now < expiry:
                subscription = cached_sub
            else:
                from app.services.subscription_service import SubscriptionService
                try:
                    subscription = await SubscriptionService.get_subscription(user_id)
                    _subscription_cache[user_id] = (subscription, now + _SUBSCRIPTION_CACHE_TTL)
                except Exception as e:
                    logger.warning(
                        "subscription_load_failed",
                        extra={"event": "subscription_load_failed", "request_id": request_id, "user_id": user_id, "error": str(e)},
                    )
                    subscription = None
        
        # Attach metadata to request.state
        request.state.user_id = user_id
        request.state.role = role
        request.state.property_ids = property_ids
        request.state.current_user = user
        request.state.subscription = subscription
        response = await call_next(request)
        return response


