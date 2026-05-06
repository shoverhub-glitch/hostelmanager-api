"""
Track failed login and OTP verification attempts to prevent brute force attacks
"""
from datetime import datetime, timezone, timedelta
from app.database.mongodb import db

# Collections for tracking attempts
login_attempts_collection = db["login_attempts"]
otp_attempts_collection = db["otp_attempts"]

MAX_LOGIN_ATTEMPTS = 5
MAX_OTP_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 10


async def check_login_attempts(email: str) -> tuple[bool, int | None]:
    """
    Check if user has exceeded login attempts
    Returns: (is_locked, minutes_remaining)
    """
    normalized_email = email.strip().lower()
    now = datetime.now(timezone.utc)
    
    attempt_doc = await login_attempts_collection.find_one({"email": normalized_email})
    
    if not attempt_doc:
        return False, None
    
    # Check if lockout period has expired
    locked_until = attempt_doc.get("lockedUntil")
    if locked_until:
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        
        if now < locked_until:
            minutes_remaining = int((locked_until - now).total_seconds() / 60)
            return True, minutes_remaining
        else:
            # Lockout period expired, reset attempts
            await login_attempts_collection.update_one(
                {"email": normalized_email},
                {"$set": {"failedAttempts": 0, "lockedUntil": None}}
            )
            return False, None
    
    return False, None


async def _increment_attempts(collection, email: str, max_attempts: int) -> int:
    """Helper for atomic increment with window-expiry reset (Critical #6)"""
    normalized_email = email.strip().lower()
    now = datetime.now(timezone.utc)
    window_cutoff = now - timedelta(minutes=LOCKOUT_DURATION_MINUTES)

    # Atomic update:
    # 1. If document doesn't exist, create with failedAttempts=1
    # 2. If existing updatedAt < window_cutoff, reset failedAttempts to 1
    # 3. Else, increment failedAttempts
    # 4. If new count >= max_attempts, set lockedUntil
    
    pipeline = [
        {
            "$set": {
                "failedAttempts": {
                    "$cond": {
                        "if": {"$lt": ["$updatedAt", window_cutoff]},
                        "then": 1,
                        "else": {"$add": ["$failedAttempts", 1]}
                    }
                },
                "updatedAt": now
            }
        },
        {
            "$set": {
                "lockedUntil": {
                    "$cond": {
                        "if": {"$gte": ["$failedAttempts", max_attempts]},
                        "then": now + timedelta(minutes=LOCKOUT_DURATION_MINUTES),
                        "else": None # Clear lock if we reset to 1 or if it's not needed
                    }
                }
            }
        }
    ]

    # Note: aggregation in find_one_and_update requires MongoDB 4.2+
    # Fallback logic for older MongoDB versions might be needed if compatibility is an issue.
    result = await collection.find_one_and_update(
        {"email": normalized_email},
        pipeline,
        upsert=True,
        return_document=True
    )
    
    return result.get("failedAttempts", 1)


async def increment_login_attempts(email: str) -> int:
    """
    Increment failed login attempts atomically.
    Returns: number of failed attempts after incrementing.
    """
    return await _increment_attempts(login_attempts_collection, email, MAX_LOGIN_ATTEMPTS)


async def reset_login_attempts(email: str):
    """Reset login attempts for successful login"""
    normalized_email = email.strip().lower()
    await login_attempts_collection.update_one(
        {"email": normalized_email},
        {"$set": {"failedAttempts": 0, "lockedUntil": None, "updatedAt": datetime.now(timezone.utc)}}
    )


async def check_otp_attempts(email: str) -> tuple[bool, int | None]:
    """
    Check if user has exceeded OTP verification attempts
    Returns: (is_locked, minutes_remaining)
    """
    normalized_email = email.strip().lower()
    now = datetime.now(timezone.utc)
    
    attempt_doc = await otp_attempts_collection.find_one({"email": normalized_email})
    
    if not attempt_doc:
        return False, None
    
    # Check if lockout period has expired
    locked_until = attempt_doc.get("lockedUntil")
    if locked_until:
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        
        if now < locked_until:
            minutes_remaining = int((locked_until - now).total_seconds() / 60)
            return True, minutes_remaining
        else:
            # Lockout period expired, reset attempts
            await otp_attempts_collection.update_one(
                {"email": normalized_email},
                {"$set": {"failedAttempts": 0, "lockedUntil": None}}
            )
            return False, None
    
    return False, None


async def increment_otp_attempts(email: str) -> int:
    """
    Increment failed OTP verification attempts atomically.
    Returns: number of failed attempts after incrementing.
    """
    return await _increment_attempts(otp_attempts_collection, email, MAX_OTP_ATTEMPTS)


async def delete_otp_attempts(email: str):
    """Delete OTP attempt record entirely (used when new OTP is requested or verified successfully)"""
    normalized_email = email.strip().lower()
    await otp_attempts_collection.delete_one({"email": normalized_email})
