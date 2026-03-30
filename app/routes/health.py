from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from app.database.mongodb import db

router = APIRouter()

@router.get("/health", tags=["health"])
async def health_check():
    try:
        await db.command("ping")
        return {"status": "ok"}
    except Exception:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "error"})
