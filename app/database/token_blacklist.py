from datetime import datetime, timezone
import logging
from .mongodb import db
from app.utils.auth_tokens import hash_token, get_token_expiry_datetime

logger = logging.getLogger(__name__)

blacklist_collection = db["token_blacklist"]

async def blacklist_token(token: str):
    """Add a token to the blacklist with its expiration time."""
    # Tokens are automatically removed via MongoDB TTL index on expiresAt.
    expires_at = get_token_expiry_datetime(token, fallback_days=30)
    token_hash = hash_token(token)
    
    try:
        await blacklist_collection.insert_one({
            "tokenHash": token_hash,
            "blacklistedAt": datetime.now(timezone.utc),
            "expiresAt": expires_at
        })
    except Exception as e:
        logger.error(f"Failed to blacklist token: {e}")

async def is_token_blacklisted(token: str) -> bool:
    """Check if a token is in the blacklist."""
    if not token:
        return False
    token_hash = hash_token(token)
    # Backward compatibility for previously stored raw tokens.
    doc = await blacklist_collection.find_one({"$or": [{"tokenHash": token_hash}, {"token": token}]})
    return doc is not None
