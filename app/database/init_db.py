import logging
import os
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError
from app.config import settings
from app.database.mongodb import db

logger = logging.getLogger(__name__)

async def ensure_mongodb_connection():
    """Return a concise startup error message if MongoDB is unreachable."""
    mongo_url = settings.MONGO_URL

    if not mongo_url:
        return "MongoDB startup check failed: MONGO_URL is not set. Configure it in api/.env before starting the API."

    try:
        await db.command("ping")
        return None
    except ServerSelectionTimeoutError:
        parsed = urlparse(mongo_url)
        hostname = parsed.hostname or ""
        hint = ""
        if hostname == "mongodb":
            hint = " Hint: 'mongodb' hostname works inside Docker network; for local uvicorn use localhost in MONGO_URL."
        logger.error("MongoDB startup check failed: cannot connect to MongoDB server.")
        return (
            "MongoDB startup check failed: unable to connect. Ensure MongoDB is running and MONGO_URL points to a reachable host."
            f"{hint}"
        )
    except Exception as exc:
        logger.error(f"MongoDB startup check failed: {exc}")
        return f"MongoDB startup check failed: {exc}"


async def ensure_indexes():
    """Create essential indexes for production-grade queries."""
    
    def _to_key_pattern(keys):
        if isinstance(keys, str):
            return {keys: 1}
        if isinstance(keys, tuple):
            if len(keys) == 2 and isinstance(keys[0], str):
                return {keys[0]: keys[1]}
            keys = [keys]
        if isinstance(keys, list):
            return {field: order for field, order in keys}
        raise ValueError(f"Unsupported index key format: {keys}")
    
    async def create_index_safe(collection, keys, **kwargs):
        """Safely create an index, handling existing/conflicting index specs."""
        try:
            await db[collection].create_index(keys, **kwargs)
        except OperationFailure as e:
            # Index already exists with same or overlapping key specs.
            # code 86: IndexKeySpecsConflict, code 85: IndexOptionsConflict.
            if e.code in (85, 86):
                ttl_value = kwargs.get("expireAfterSeconds")
                if ttl_value is not None:
                    try:
                        await db.command({
                            "collMod": collection,
                            "index": {
                                "keyPattern": _to_key_pattern(keys),
                                "expireAfterSeconds": ttl_value,
                            },
                        })
                        logger.info(
                            "Updated TTL index on %s for %s to %s seconds",
                            collection,
                            keys,
                            ttl_value,
                        )
                        return
                    except OperationFailure as mod_err:
                        logger.warning(
                            "Could not update TTL index via collMod on %s for %s: %s",
                            collection,
                            keys,
                            mod_err,
                        )
                logger.debug(f"Index already exists on {collection} for {keys}, skipping")
                return
            raise
    
    # FIX: Group indexes logically by collection (Medium #3)

    # ============ USERS COLLECTION ============
    await create_index_safe("users", "email", unique=True)
    await create_index_safe("users", "createdAt")
    await create_index_safe("users", "phone")
    
    # ============ TOKEN BLACKLIST COLLECTION ============
    await create_index_safe("token_blacklist", "token")
    await create_index_safe("token_blacklist", "tokenHash")
    await create_index_safe("token_blacklist", "expiresAt", expireAfterSeconds=0)
    
    # ============ PROPERTIES COLLECTION ============
    await create_index_safe("properties", "ownerIds")
    await create_index_safe("properties", "createdAt")
    await create_index_safe("properties", "active")
    await create_index_safe("properties", [("ownerIds", 1), ("active", 1)])
    try:
        await create_index_safe("properties", [("name", "text"), ("address", "text")])
    except Exception:
        pass

    # ============ ROOMS COLLECTION ============
    await create_index_safe("rooms", "propertyId")
    await create_index_safe("rooms", "active")
    await create_index_safe("rooms", [("propertyId", 1), ("active", 1)])
    await create_index_safe("rooms", [("propertyId", 1), ("roomNumber", 1)])
    
    # ============ BEDS COLLECTION ============
    await create_index_safe("beds", "propertyId")
    await create_index_safe("beds", "roomId")
    await create_index_safe("beds", "status")
    await create_index_safe("beds", [("propertyId", 1), ("status", 1)])
    await create_index_safe("beds", [("roomId", 1), ("status", 1)])
    
    # ============ TENANTS COLLECTION ============
    await create_index_safe("tenants", "propertyId")
    await create_index_safe("tenants", "bedId")
    await create_index_safe("tenants", "status")
    await create_index_safe("tenants", [("propertyId", 1), ("autoGeneratePayments", 1)])
    await create_index_safe("tenants", [("propertyId", 1), ("status", 1)])
    await create_index_safe("tenants", [("propertyId", 1), ("billingConfig.status", 1)])
    try:
        await create_index_safe("tenants", [("name", "text"), ("phone", "text"), ("documentId", "text")])
    except Exception:
        pass  
    
    # ============ PAYMENTS COLLECTION ============
    await create_index_safe("payments", "propertyId")
    await create_index_safe("payments", "tenantId")
    await create_index_safe("payments", "status")
    await create_index_safe("payments", "dueDate")
    await create_index_safe("payments", [("propertyId", 1), ("status", 1)])
    await create_index_safe("payments", [("tenantId", 1), ("dueDate", 1)], unique=True)
    await create_index_safe("payments", [("propertyId", 1), ("dueDate", 1)])
    
    # ============ STAFF COLLECTION ============
    await create_index_safe("staff", "propertyId")
    await create_index_safe("staff", "role")
    await create_index_safe("staff", "status")
    
    # ============ EMAIL OTP COLLECTION ============
    await create_index_safe("email_otps", "email")
    await create_index_safe("email_otps", "expires_at", expireAfterSeconds=0)
    
    # ============ PASSWORD RESET OTP COLLECTION ============
    await create_index_safe("password_reset_otps", "email")
    await create_index_safe("password_reset_otps", "createdAt", expireAfterSeconds=60*10)
    
    # ============ OTP ATTEMPTS COLLECTION ============
    await create_index_safe("otp_attempts", "email")
    await create_index_safe("otp_attempts", "updatedAt", expireAfterSeconds=60*60)
    await create_index_safe("otp_attempts", "ip_address")
    
    # ============ LOGIN ATTEMPTS COLLECTION ============
    await create_index_safe("login_attempts", "updatedAt", expireAfterSeconds=60*60)
    await create_index_safe("login_attempts", "ip_address")
    
    logger.info("✓ MongoDB indexes ensured")
