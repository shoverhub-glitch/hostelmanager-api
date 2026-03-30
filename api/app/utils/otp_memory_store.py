"""MongoDB-based OTP storage with expiration and resend cooldown"""
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from app.config import settings
from app.database.mongodb import db

OTP_COLLECTION = "email_otps"
OTP_TTL_MINUTES = 5
RESEND_COOLDOWN_SECONDS = 45


def _as_utc_aware(value: Optional[datetime | str]) -> Optional[datetime]:
    """Normalize datetime values from MongoDB/JSON to UTC-aware datetimes."""
    if not value:
        return None

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None

    if not isinstance(value, datetime):
        return None

    # MongoDB drivers may return naive UTC datetimes depending on client config.
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


async def generate_and_store_otp(email: str, otp_type: str = "registration") -> Tuple[str, bool]:
    """
    Generate and store OTP in MongoDB with resend cooldown
    
    Args:
        email: User email
        otp_type: Type of OTP (registration or password_reset)
    
    Returns:
        Tuple of (otp, can_resend) - OTP code and whether it's first request or within cooldown
    """
    normalized_email = email.strip().lower()
    now = datetime.now(timezone.utc)
    
    # Check for existing OTP and resend cooldown
    existing = await db[OTP_COLLECTION].find_one({"email": normalized_email})
    
    if existing:
        # Check resend cooldown regardless of OTP type to prevent bypass by
        # alternating between registration and password_reset request types.
        resend_cooldown_expires = _as_utc_aware(existing.get("resend_cooldown_expires_at"))
        if resend_cooldown_expires and resend_cooldown_expires > now:
            # Still in cooldown — return existing OTP only if type matches
            existing_type = existing.get("otp_type", "registration")
            if existing_type == otp_type:
                return existing.get("otp", ""), False
            # Different type requested during cooldown: deny resend
            # FIX: Raise error so caller knows not to send blank OTP
            raise ValueError(f"A different OTP is already active for this email. Please try again in {int((resend_cooldown_expires - now).total_seconds())} seconds.")
    
    # Generate cryptographically secure 6-digit OTP
    if settings.ENV.lower() != "production":
        otp = settings.DEMO_OTP
    else:
        otp = f"{secrets.randbelow(900000) + 100000}"
    
    expires_at = now + timedelta(minutes=OTP_TTL_MINUTES)
    resend_cooldown_expires_at = now + timedelta(seconds=RESEND_COOLDOWN_SECONDS)
    
    otp_data = {
        "email": normalized_email,
        "otp": otp,
        "otp_type": otp_type,
        "created_at": now,
        "expires_at": expires_at,
        "last_sent_at": now,
        "resend_cooldown_expires_at": resend_cooldown_expires_at,
        "verified": False,
        "attempt_count": 0,
        "locked_until": None
    }
    
    # Upsert OTP document
    await db[OTP_COLLECTION].update_one(
        {"email": normalized_email},
        {"$set": otp_data},
        upsert=True
    )
    
    return otp, True


async def get_otp(email: str) -> Optional[dict]:
    """Get OTP record from MongoDB"""
    normalized_email = email.strip().lower()
    
    doc = await db[OTP_COLLECTION].find_one({"email": normalized_email})
    if not doc:
        return None
    
    now = datetime.now(timezone.utc)
    expires_at = _as_utc_aware(doc.get("expires_at"))
    
    # Check expiration
    if expires_at and expires_at < now:
        await db[OTP_COLLECTION].delete_one({"email": normalized_email})
        return None
    
    return doc


async def verify_otp(email: str, otp: str, otp_type: str = "registration") -> Tuple[bool, Optional[str]]:
    """
    Verify OTP and return (is_valid, error_message)

    Args:
        email: User email
        otp: Submitted OTP code
        otp_type: Expected OTP type (registration or password_reset)
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    normalized_email = email.strip().lower()
    if settings.ENV.lower() != "production" and hmac.compare_digest(otp, settings.DEMO_OTP):
        # Demo bypass: still enforce type to prevent cross-flow abuse
        stored = await get_otp(normalized_email)
        if stored and stored.get("otp_type", "registration") != otp_type:
            return False, "Invalid OTP for this action. Please request a new OTP"
        return True, None

    stored = await get_otp(normalized_email)
    
    if not stored:
        return False, "OTP not found. Please request a new OTP"

    # Ensure OTP can only be used for its intended flow.
    stored_type = stored.get("otp_type", "registration")
    if stored_type != otp_type:
        return False, "Invalid OTP for this action. Please request a new OTP"
    
    now = datetime.now(timezone.utc)
    
    # Check expiration
    expires_at = _as_utc_aware(stored.get("expires_at"))
    if expires_at and expires_at < now:
        await db[OTP_COLLECTION].delete_one({"email": normalized_email})
        return False, "OTP expired. Please request a new OTP"

    # Enforce temporary lock before evaluating OTP match.
    locked_until = _as_utc_aware(stored.get("locked_until"))
    if locked_until:
        if locked_until > now:
            remaining_seconds = int((locked_until - now).total_seconds())
            minutes_remaining = (remaining_seconds + 59) // 60
            return False, f"Too many failed attempts. Please try again in {minutes_remaining} minutes"
    
    # Constant-time comparison to prevent timing-based OTP oracle attacks
    if not hmac.compare_digest(stored["otp"], otp):
        attempt_count = stored.get("attempt_count", 0) + 1
        
        if attempt_count >= 5:
            # Lock for 10 minutes after 5 failed attempts
            locked_until = now + timedelta(minutes=10)
            await db[OTP_COLLECTION].update_one(
                {"email": normalized_email},
                {"$set": {"attempt_count": attempt_count, "locked_until": locked_until}}
            )
            return False, "Too many failed attempts. Please request a new OTP after 10 minutes"
        
        await db[OTP_COLLECTION].update_one(
            {"email": normalized_email},
            {"$set": {"attempt_count": attempt_count}}
        )
        remaining_attempts = 5 - attempt_count
        return False, f"Invalid OTP. {remaining_attempts} attempt(s) remaining"
    
    return True, None


async def mark_otp_verified(email: str) -> bool:
    """Mark OTP as verified"""
    normalized_email = email.strip().lower()
    result = await db[OTP_COLLECTION].update_one(
        {"email": normalized_email},
        {"$set": {"verified": True}}
    )
    return result.modified_count > 0


async def get_resend_cooldown_remaining(email: str) -> int:
    """Get remaining cooldown seconds before resend is allowed. Returns 0 if resend is allowed"""
    normalized_email = email.strip().lower()
    
    doc = await db[OTP_COLLECTION].find_one(
        {"email": normalized_email},
        {"resend_cooldown_expires_at": 1}
    )
    
    if not doc:
        return 0
    
    resend_cooldown_expires = _as_utc_aware(doc.get("resend_cooldown_expires_at"))
    if not resend_cooldown_expires:
        return 0
    
    now = datetime.now(timezone.utc)
    remaining = (resend_cooldown_expires - now).total_seconds()
    
    return max(0, int(remaining))


async def delete_otp(email: str) -> bool:
    """Delete OTP from MongoDB"""
    normalized_email = email.strip().lower()
    result = await db[OTP_COLLECTION].delete_one({"email": normalized_email})
    return result.deleted_count > 0


async def cleanup_expired_otps() -> int:
    """Remove expired OTPs from MongoDB. Returns count of cleaned entries"""
    now = datetime.now(timezone.utc)
    result = await db[OTP_COLLECTION].delete_many({
        "expires_at": {"$lt": now}
    })
    return result.deleted_count
