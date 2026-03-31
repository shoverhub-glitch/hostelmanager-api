from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from app.database.mongodb import db
from app.utils.cache import get_cache_stats

router = APIRouter()

@router.get("/health", tags=["health"])
async def health_check():
    try:
        await db.command("ping")
        return {"status": "ok"}
    except Exception:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "error"})


@router.get("/cache/stats", tags=["health"])
async def cache_statistics():
    """Get cache performance statistics"""
    stats = await get_cache_stats()
    return {"cache": stats}
