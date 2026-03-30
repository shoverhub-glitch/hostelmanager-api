from datetime import datetime, timezone, timedelta
from bson import ObjectId
from bson.errors import InvalidId
from jose import JWTError, jwt
from fastapi import HTTPException, status, Request
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
import time
import logging
import hashlib

from app.database.mongodb import db
from app.database.token_blacklist import blacklist_token, is_token_blacklisted
from app.config import settings
from app.utils.helpers import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    SECRET_KEY,
    ALGORITHM,
)
from app.utils.attempt_tracking import (
    check_login_attempts,
    increment_login_attempts,
    reset_login_attempts,
    check_otp_attempts,
    increment_otp_attempts,
    delete_otp_attempts,
)
from app.utils.email_service import send_otp_email
from app.utils.otp_memory_store import (
    generate_and_store_otp,
    get_otp,
    verify_otp,
    mark_otp_verified,
    get_resend_cooldown_remaining,
    delete_otp,
)
from app.models.user_schema import UserCreate, UserLogin, UserOut
import re

users_collection = db["users"]
# FIX (Critical #7): Use single OTP collection consistently via otp_memory_store
# Remove direct access to separate password_reset_otp_collection
email_otp_collection = db["email_otps"]
logger = logging.getLogger(__name__)

PASSWORD_MIN_LENGTH = 8


def _parse_object_id(user_id: str) -> ObjectId:
    try:
        return ObjectId(user_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject")


def _decode_refresh_token(refresh_token: str) -> dict:
    try:
        decoded = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    if decoded.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    if not decoded.get("sub"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject")

    return decoded


def _email_log_meta(email: str) -> dict:
    normalized = (email or "").strip().lower()
    domain = normalized.split("@", 1)[1] if "@" in normalized else "unknown"
    email_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12] if normalized else "unknown"
    return {"email_domain": domain, "email_hash": email_hash}


def validate_password_strength(password: str) -> str | None:
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters long"
    if not re.search(r"[A-Z]", password):
        return "Password must contain at least one uppercase letter"
    if not re.search(r"[a-z]", password):
        return "Password must contain at least one lowercase letter"
    if not re.search(r"\d", password):
        return "Password must contain at least one number"
    if not re.search(r"[^\w\s]", password):
        return "Password must contain at least one special character"
    return None


def validate_indian_phone(phone: str) -> bool:
    """Validate Indian phone numbers (+91 followed by 10 digits)"""
    pattern = r'^\+91[6-9]\d{9}$'
    return bool(re.match(pattern, phone.strip()))


def _build_auth_payload(user_doc: dict, user_id: str):
    access_token = create_access_token({"sub": user_id, "iat": int(time.time())})
    refresh_token = create_refresh_token({"sub": user_id, "iat": int(time.time())})
    # FIX: Use configured refresh token expiry for client-side expiresAt hint
    expires_at = int(time.time()) + settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60
    user_out = UserOut(
        id=user_id,
        name=user_doc["name"],
        email=user_doc["email"],
        phone=user_doc.get("phone"),
        propertyIds=user_doc.get("propertyIds", [])
    )
    return {
        "user": user_out.model_dump(),
        "tokens": {
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresAt": expires_at,
        },
    }


async def register_user_service(user: UserCreate):
    # Validate email
    normalized_email = user.email.strip().lower()
    existing = await users_collection.find_one({"email": normalized_email})
    if existing:
        # Generic message to prevent email enumeration attacks
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Registration failed. Email may already be registered.")

    # Validate phone number (India only)
    if not validate_indian_phone(user.phone):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Invalid Indian phone number. Format: +91XXXXXXXXXX"
        )

    password_error = validate_password_strength(user.password)
    if password_error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=password_error)

    # SECURITY: Check if email is verified - REQUIRED for registration
    # This blocks direct API attacks without OTP verification
    otp_doc = await get_otp(normalized_email)
    if not otp_doc or not otp_doc.get("verified") or otp_doc.get("otp_type") != "registration":
        # Log security event for audit trail
        logger.warning(
            "registration_without_email_verification",
            extra={"event": "registration_without_email_verification", **_email_log_meta(normalized_email)},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Email verification required. Please complete OTP verification first."
        )

    # SECURITY: Verify email verification is still fresh (within 5 minutes per TTL)
    now = datetime.now(timezone.utc)
    # OTP doc updated_at is set when marked verified
    verification_time = otp_doc.get("updated_at") or otp_doc.get("created_at")
    
    if not verification_time:
        logger.warning(
            "registration_missing_verification_timestamp",
            extra={"event": "registration_missing_verification_timestamp", **_email_log_meta(normalized_email)},
        )
        await delete_otp(normalized_email)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token expired. Please request a new OTP."
        )

    if verification_time.tzinfo is None:
        verification_time = verification_time.replace(tzinfo=timezone.utc)

    age_minutes = (now - verification_time).total_seconds() / 60
    # FIX (Medium #1): Match window to 5-minute TTL exactly
    if age_minutes > 5:
        logger.warning(
            "registration_verification_window_expired",
            extra={"event": "registration_verification_window_expired", **_email_log_meta(normalized_email)},
        )
        await delete_otp(normalized_email)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token expired. Please request a new OTP."
        )

    user_doc = {
        "name": user.name,
        "email": normalized_email,
        "phone": user.phone,
        "password": hash_password(user.password),
        "role": "propertyowner",
        "isEmailVerified": True,
        "isDeleted": False,
        "lastLogin": None,
        "createdAt": now,
        "updatedAt": now,
        "propertyIds": [],
    }

    result = await users_collection.insert_one(user_doc)
    user_id = str(result.inserted_id)
    
    # Create single free subscription document
    from app.services.subscription_service import SubscriptionService
    await SubscriptionService.create_default_subscriptions(user_id)
    
    # SECURITY: Delete the verification record after successful registration
    await delete_otp(normalized_email)
    await delete_otp_attempts(normalized_email)
    
    logger.info("user_registration_success", extra={"event": "user_registration_success", "user_id": user_id, **_email_log_meta(normalized_email)})
    response = _build_auth_payload(user_doc, user_id)
    return JSONResponse(status_code=status.HTTP_201_CREATED, content={"data": response})


async def login_user_service(data: UserLogin):
    # SECURITY: Normalize email
    normalized_email = data.email.strip().lower()
    
    # SECURITY: Check login attempts
    is_locked, minutes_remaining = await check_login_attempts(normalized_email)
    if is_locked:
        logger.warning(
            "login_temporarily_locked",
            extra={"event": "login_temporarily_locked", "minutes_remaining": minutes_remaining, **_email_log_meta(normalized_email)},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts. Please try again in {minutes_remaining} minutes."
        )

    user = await users_collection.find_one({"email": normalized_email})
    
    # SECURITY: Verify password AND check user existence
    if not user or not verify_password(data.password, user.get("password", "")):
        failed_count = await increment_login_attempts(normalized_email)
        remaining_attempts = 5 - failed_count
        
        if user:
            logger.warning(
                "login_failed_password_mismatch",
                extra={"event": "login_failed_password_mismatch", "remaining_attempts": remaining_attempts, **_email_log_meta(normalized_email)},
            )
        else:
            logger.warning(
                "login_failed_unknown_email",
                extra={"event": "login_failed_unknown_email", "remaining_attempts": remaining_attempts, **_email_log_meta(normalized_email)},
            )
        
        if failed_count >= 5:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts. Your account is locked for 10 minutes."
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid credentials. {remaining_attempts} attempt(s) remaining."
            )

    # SECURITY: Additional user validation checks
    if user.get("isDeleted"):
        logger.warning("login_deleted_account", extra={"event": "login_deleted_account", **_email_log_meta(normalized_email)})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is no longer available")

    if user.get("isDisabled"):
        logger.warning("login_disabled_account", extra={"event": "login_disabled_account", **_email_log_meta(normalized_email)})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account has been disabled. Contact support.")

    # Check if account requires email verification
    if user.get("requiresEmailVerification") and not user.get("isEmailVerified"):
        logger.warning("login_unverified_account", extra={"event": "login_unverified_account", **_email_log_meta(normalized_email)})
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Please verify your email before logging in. Check your inbox for verification link."
        )

    # SECURITY: Reset attempts on success
    await reset_login_attempts(normalized_email)

    # Update last login
    now = datetime.now(timezone.utc)
    await users_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {"lastLogin": now}},
    )

    user_id = str(user["_id"])
    logger.info("user_login_success", extra={"event": "user_login_success", "user_id": user_id, **_email_log_meta(normalized_email)})
    
    response = _build_auth_payload(user, user_id)
    return JSONResponse(status_code=status.HTTP_200_OK, content={"data": response})


async def send_email_otp_service(email: str):
    """Send OTP to email for verification during registration"""
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email is required")

    normalized_email = email.strip().lower()
    
    # SECURITY: Prevent duplicate registration
    existing_user = await users_collection.find_one({"email": normalized_email, "isDeleted": False})
    if existing_user:
        auth_provider = existing_user.get("authProvider", "email")
        logger.warning(
            "registration_blocked_existing_email",
            extra={"event": "registration_blocked_existing_email", "auth_provider": auth_provider, **_email_log_meta(normalized_email)},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered. Please login with your existing account instead."
        )
    
    # Check cooldown
    cooldown_remaining = await get_resend_cooldown_remaining(normalized_email)
    if cooldown_remaining > 0:
        logger.warning(
            "registration_otp_cooldown_active",
            extra={"event": "registration_otp_cooldown_active", "cooldown_seconds": cooldown_remaining, **_email_log_meta(normalized_email)},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {cooldown_remaining} seconds before requesting another OTP"
        )
    
    # Clear any stale verification
    await delete_otp(normalized_email)
    await delete_otp_attempts(normalized_email)
    
    # Generate OTP
    try:
        otp, is_new = await generate_and_store_otp(normalized_email, "registration")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))

    logger.info(
        "registration_otp_generated",
        extra={"event": "registration_otp_generated", "is_new": bool(is_new), **_email_log_meta(normalized_email)},
    )

    if settings.ENV.lower() != "production":
        logger.info("registration_otp_demo_mode", extra={"event": "registration_otp_demo_mode", **_email_log_meta(normalized_email)})
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "data": {
                    "message": f"Demo OTP: {settings.DEMO_OTP} (use this to verify)"
                }
            },
        )
    
    # Send OTP
    email_sent = await send_otp_email(normalized_email, otp)
    
    if not email_sent:
        await delete_otp(normalized_email)
        logger.error("registration_otp_email_send_failed", extra={"event": "registration_otp_email_send_failed", **_email_log_meta(normalized_email)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to send OTP right now. Please try again shortly."
        )

    logger.info("registration_otp_email_sent", extra={"event": "registration_otp_email_sent", **_email_log_meta(normalized_email)})
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "data": {
                "message": "OTP sent successfully to your email. It expires in 5 minutes.",
            }
        },
    )


async def verify_email_otp_service(email: str, otp: str, otp_type: str = "registration"):
    """Verify OTP sent to email during registration or password reset"""
    if not email or not otp:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email and OTP are required")

    normalized_email = email.strip().lower()

    is_locked, minutes_remaining = await check_otp_attempts(normalized_email)
    if is_locked:
        logger.warning(
            "otp_verification_temporarily_locked",
            extra={"event": "otp_verification_temporarily_locked", "otp_type": otp_type, "minutes_remaining": minutes_remaining, **_email_log_meta(normalized_email)},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed OTP attempts. Please try again in {minutes_remaining} minutes."
        )

    # Verify OTP
    is_valid, error_message = await verify_otp(normalized_email, otp, otp_type=otp_type)
    if not is_valid:
        failed_count = await increment_otp_attempts(normalized_email)
        logger.warning(
            "otp_verification_failed",
            extra={"event": "otp_verification_failed", "otp_type": otp_type, "failed_count": failed_count, **_email_log_meta(normalized_email)},
        )
        if failed_count >= 5:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed OTP attempts. Please request a new OTP after 10 minutes"
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_message)

    # Clear OTP attempt tracking after successful verification.
    await delete_otp_attempts(normalized_email)
    logger.info("otp_verification_success", extra={"event": "otp_verification_success", "otp_type": otp_type, **_email_log_meta(normalized_email)})

    if otp_type == "registration":
        await mark_otp_verified(normalized_email)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "data": {
                "message": "OTP verified successfully",
            }
        },
    )


async def get_current_user_service(request: Request):
    current_user = getattr(request.state, "current_user", None)
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    user_id = str(current_user.get("_id")) if current_user.get("_id") else ""
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials")

    user_out = UserOut(
        id=user_id,
        name=current_user.get("name", ""),
        email=current_user.get("email", ""),
        phone=current_user.get("phone"),
        propertyIds=current_user.get("propertyIds", []),
    )
    return JSONResponse(status_code=status.HTTP_200_OK, content={"data": user_out.model_dump()})


async def refresh_token_service(payload):
    refresh_token = payload.refreshToken
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing refresh token")

    if await is_token_blacklisted(refresh_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token is invalidated (blacklisted)")

    decoded = _decode_refresh_token(refresh_token)
    user_id = decoded.get("sub")
    user_object_id = _parse_object_id(user_id)
    user = await users_collection.find_one({"_id": user_object_id})
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    if user.get("isDeleted"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is deleted")

    if user.get("isDisabled"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account has been disabled")

    await blacklist_token(refresh_token)
    new_refresh_token = create_refresh_token({"sub": user_id, "iat": int(time.time())})
    token = create_access_token({"sub": user_id, "iat": int(time.time())})
    expires_at = int(time.time()) + settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60

    user_out = UserOut(
        id=user_id,
        name=user["name"],
        email=user["email"],
        phone=user.get("phone"),
        propertyIds=user.get("propertyIds", []),
    )

    response = {
        "tokens": {
            "accessToken": token,
            "refreshToken": new_refresh_token,
            "expiresAt": expires_at,
        },
        "user": user_out.model_dump(),
    }
    return JSONResponse(status_code=status.HTTP_200_OK, content={"data": jsonable_encoder(response)})


async def logout_user_service(payload):
    refresh_token = payload.refreshToken
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing refresh token")

    _decode_refresh_token(refresh_token)

    await blacklist_token(refresh_token)
    return {"success": True}


async def forgot_password_service(email: str):
    """Send OTP to email for password reset"""
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email is required")

    normalized_email = email.strip().lower()
    
    cooldown_remaining = await get_resend_cooldown_remaining(normalized_email)
    if cooldown_remaining > 0:
        logger.warning(
            "password_reset_otp_cooldown_active",
            extra={"event": "password_reset_otp_cooldown_active", "cooldown_seconds": cooldown_remaining, **_email_log_meta(normalized_email)},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {cooldown_remaining} seconds before requesting another OTP"
        )
    
    user = await users_collection.find_one({"email": normalized_email})
    if not user:
        logger.info("password_reset_requested_unknown_email", extra={"event": "password_reset_requested_unknown_email", **_email_log_meta(normalized_email)})
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "data": {
                    "message": "If an account with this email exists, you will receive a password reset OTP",
                }
            },
        )

    # Reset attempts
    await delete_otp_attempts(normalized_email)
    
    # Generate OTP
    try:
        otp, is_new = await generate_and_store_otp(normalized_email, "password_reset")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))

    logger.info(
        "password_reset_otp_generated",
        extra={"event": "password_reset_otp_generated", "is_new": bool(is_new), **_email_log_meta(normalized_email)},
    )
    if settings.ENV.lower() != "production":
        logger.info("password_reset_otp_demo_mode", extra={"event": "password_reset_otp_demo_mode", **_email_log_meta(normalized_email)})
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "data": {
                    "message": f"Demo OTP: {settings.DEMO_OTP}"
                }
            },
        )

    # Send OTP
    email_sent = await send_otp_email(
        normalized_email,
        otp,
        app_name=settings.APP_NAME,
        otp_type="password_reset",
    )
    
    if not email_sent:
        await delete_otp(normalized_email)
        logger.error("password_reset_otp_email_send_failed", extra={"event": "password_reset_otp_email_send_failed", **_email_log_meta(normalized_email)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to send password reset OTP right now. Please try again shortly."
        )

    logger.info("password_reset_otp_email_sent", extra={"event": "password_reset_otp_email_sent", **_email_log_meta(normalized_email)})
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "data": {
                "message": "If an account with this email exists, you will receive a password reset OTP",
            }
        },
    )


async def reset_password_service(email: str, otp: str, new_password: str):
    """Reset password using OTP verification"""
    if not email or not otp or not new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Email, OTP, and new password are required"
        )

    password_error = validate_password_strength(new_password)
    if password_error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=password_error)

    normalized_email = email.strip().lower()
    now = datetime.now(timezone.utc)

    # Verify OTP
    is_valid, error_message = await verify_otp(normalized_email, otp, otp_type="password_reset")
    if not is_valid:
        failed_count = await increment_otp_attempts(normalized_email)
        if failed_count >= 5:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed OTP attempts. Please request a new OTP after 10 minutes"
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_message)

    user = await users_collection.find_one({"email": normalized_email})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    if user.get("isDeleted"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deleted and cannot be recovered"
        )

    hashed_password = hash_password(new_password)
    await users_collection.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "password": hashed_password,
                "updatedAt": now
            }
        }
    )

    # SECURITY: Clean up
    await delete_otp(normalized_email)
    await delete_otp_attempts(normalized_email)
    logger.info("password_reset_success", extra={"event": "password_reset_success", "user_id": str(user.get("_id")), **_email_log_meta(normalized_email)})

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "data": {
                "message": "Password reset successfully. Please log in with your new password.",
                "success": True
            }
        },
    )


async def change_password_service(request: Request, old_password: str, new_password: str):
    """Change password for the currently authenticated user."""
    if not old_password or not new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Old password and new password are required"
        )

    password_error = validate_password_strength(new_password)
    if password_error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=password_error)

    if old_password == new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from old password"
        )

    current_user = getattr(request.state, "current_user", None)
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    user_id = current_user.get("_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication credentials")

    user = await users_collection.find_one({"_id": user_id})
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if user.get("isDeleted"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is deleted")

    if not verify_password(old_password, user.get("password", "")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Old password is incorrect")

    now = datetime.now(timezone.utc)
    await users_collection.update_one(
        {"_id": user_id},
        {
            "$set": {
                "password": hash_password(new_password),
                "updatedAt": now,
            }
        },
    )

    logger.info("password_change_success", extra={"event": "password_change_success", "user_id": str(user_id)})

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "data": {
                "message": "Password changed successfully",
                "success": True,
            }
        },
    )
