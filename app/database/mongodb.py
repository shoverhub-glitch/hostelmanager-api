from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import OperationFailure
import logging

from app.config import settings

logger = logging.getLogger(__name__)

mongo_url = settings.MONGO_URL
db_name = settings.MONGO_DB_NAME

# Optimized connection pool settings for production performance
client = AsyncIOMotorClient(
    mongo_url,
    retryWrites=settings.MONGO_RETRY_WRITES,
    maxPoolSize=50,              # Maximum number of connections (default: 100)
    minPoolSize=10,              # Minimum connections to maintain (default: 0)
    maxIdleTimeMS=45000,         # Close idle connections after 45s
    waitQueueTimeoutMS=5000,     # Fail fast if pool exhausted (default: None)
    serverSelectionTimeoutMS=5000,  # Timeout for server selection
    connectTimeoutMS=10000,      # Connection timeout (default: 20000)
    socketTimeoutMS=45000        # Socket timeout for operations
)
db = client[db_name]

def getCollection(name: str):
    return db[name]

def is_transaction_unsupported(exc: Exception) -> bool:
    """
    Check if the exception indicates that MongoDB transactions are not supported.
    This usually happens when running on a standalone MongoDB instance without a replica set.
    """
    if isinstance(exc, OperationFailure):
        # Code 20: Transaction numbers are only allowed on a replica set member or mongos
        # Code 13: Unauthorized (sometimes returned if session/transaction features are restricted)
        if exc.code == 20:
            return True
        
        error_msg = str(exc)
        unsupported_markers = [
            "Transaction numbers are only allowed",
            "sessions are not supported",
            "replica set member",
            "not a replica set"
        ]
        return any(marker in error_msg for marker in unsupported_markers)
    
    return False
