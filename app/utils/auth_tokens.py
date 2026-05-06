from datetime import datetime, timedelta, timezone
import hashlib

from jose import jwt

from app.config import settings


def hash_token(token: str) -> str:
    """Return a deterministic hash for token blacklist lookups."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_token_expiry_datetime(token: str, fallback_days: int = 30) -> datetime:
    """Extract token exp claim as UTC datetime; fallback to now + fallback_days."""
    fallback = datetime.now(timezone.utc) + timedelta(days=fallback_days)
    try:
        claims = jwt.get_unverified_claims(token)
        exp = claims.get("exp")
        if isinstance(exp, (int, float)):
            return datetime.fromtimestamp(exp, tz=timezone.utc)
        if isinstance(exp, str) and exp.isdigit():
            return datetime.fromtimestamp(int(exp), tz=timezone.utc)
    except Exception:
        return fallback
    return fallback
