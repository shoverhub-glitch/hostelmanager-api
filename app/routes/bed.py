from fastapi import APIRouter, HTTPException, status, Query, Request
from typing import List
import logging
from app.models.bed_schema import BedCreate, BedUpdate, BedOut
from app.services.bed_service import BedService


router = APIRouter(prefix="/beds", tags=["beds"])
bed_service = BedService()
logger = logging.getLogger(__name__)

@router.get("/available-by-property", response_model=dict)
async def get_available_beds_by_property(request: Request, property_id: str = Query(..., description="Property ID to fetch available beds for")):
    """Get all available beds for a property, grouped by rooms with room details"""
    property_ids = getattr(request.state, "property_ids", [])
    
    if property_id not in property_ids:
        logger.warning(
            "beds_forbidden_property_access",
            extra={"event": "beds_forbidden_property_access", "property_id": property_id, "path": request.url.path},
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    
    available_beds = await bed_service.get_available_beds_with_rooms(property_id)
    
    return {
        "data": available_beds,
        "meta": {
            "total": len(available_beds)
        }
    }

@router.get("/all-by-property", response_model=dict)
async def get_all_beds_by_property(request: Request, property_id: str = Query(..., description="Property ID to fetch all beds for")):
    """Get all beds (available, occupied, maintenance) for a property, grouped by rooms - used for tenant editing"""
    property_ids = getattr(request.state, "property_ids", [])
    
    if property_id not in property_ids:
        logger.warning(
            "beds_forbidden_property_access",
            extra={"event": "beds_forbidden_property_access", "property_id": property_id, "path": request.url.path},
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    
    all_beds = await bed_service.get_all_beds_with_rooms(property_id)
    
    return {
        "data": all_beds,
        "meta": {
            "total": len(all_beds)
        }
    }

@router.get("", response_model=dict)
async def list_beds(request: Request, room_id: str = Query(None), property_id: str = Query(None), status_filter: str = Query(None), page: int = Query(1), page_size: int = Query(50)):
    beds = []
    query = {}
    property_ids = getattr(request.state, "property_ids", [])
    
    if room_id:
        query["roomId"] = room_id
    if property_id:
        query["propertyId"] = property_id
    if status_filter:
        query["status"] = status_filter
    if property_ids:
        query["propertyId"] = {"$in": property_ids}
    
    page = max(1, page)
    page_size = min(100, max(1, page_size))  # Cap at 100 per page
    skip = (page - 1) * page_size
    
    # Get total count
    total = await bed_service.db["beds"].count_documents(query)
    
    # Get paginated results
    cursor = bed_service.db["beds"].find(query).skip(skip).limit(page_size)
    async for doc in cursor:
        beds.append(BedOut(**doc))
    
    return {
        "data": beds,
        "meta": {
            "total": total,
            "page": page,
            "pageSize": page_size,
            "hasMore": skip + page_size < total
        }
    }

@router.post("", response_model=BedOut, status_code=status.HTTP_201_CREATED)
async def create_bed(request: Request, bed: BedCreate):
    property_ids = getattr(request.state, "property_ids", [])
    if bed.propertyId not in property_ids:
        logger.warning(
            "bed_create_forbidden",
            extra={"event": "bed_create_forbidden", "property_id": bed.propertyId, "path": request.url.path},
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    created = await bed_service.create_bed(bed)
    logger.info(
        "bed_create_route_success",
        extra={"event": "bed_create_route_success", "bed_id": created.id, "property_id": created.propertyId},
    )
    return created

@router.get("/{bed_id}", response_model=BedOut)
async def get_bed(request: Request, bed_id: str):
    bed = await bed_service.get_bed(bed_id)
    property_ids = getattr(request.state, "property_ids", [])
    if not bed or bed.propertyId not in property_ids:
        logger.warning(
            "bed_get_not_found_or_forbidden",
            extra={"event": "bed_get_not_found_or_forbidden", "bed_id": bed_id, "path": request.url.path},
        )
        raise HTTPException(status_code=404, detail="Bed not found or forbidden")
    return bed

@router.patch("/{bed_id}", response_model=BedOut)
async def update_bed(request: Request, bed_id: str, bed_update: BedUpdate):
    bed = await bed_service.update_bed(bed_id, bed_update)
    property_ids = getattr(request.state, "property_ids", [])
    if not bed or bed.propertyId not in property_ids:
        logger.warning(
            "bed_update_not_found_or_forbidden",
            extra={"event": "bed_update_not_found_or_forbidden", "bed_id": bed_id, "path": request.url.path},
        )
        raise HTTPException(status_code=404, detail="Bed not found or forbidden")
    logger.info(
        "bed_update_route_success",
        extra={"event": "bed_update_route_success", "bed_id": bed.id, "property_id": bed.propertyId},
    )
    return bed

@router.delete("/{bed_id}", response_model=dict)
async def delete_bed(request: Request, bed_id: str):
    bed = await bed_service.get_bed(bed_id)
    property_ids = getattr(request.state, "property_ids", [])
    if not bed or bed.propertyId not in property_ids:
        logger.warning(
            "bed_delete_not_found_or_forbidden",
            extra={"event": "bed_delete_not_found_or_forbidden", "bed_id": bed_id, "path": request.url.path},
        )
        raise HTTPException(status_code=404, detail="Bed not found or forbidden")
    success = await bed_service.delete_bed(bed_id)
    if not success:
        logger.warning(
            "bed_delete_failed",
            extra={"event": "bed_delete_failed", "bed_id": bed_id, "path": request.url.path},
        )
        raise HTTPException(status_code=404, detail="Bed not found")
    logger.info(
        "bed_delete_route_success",
        extra={"event": "bed_delete_route_success", "bed_id": bed_id, "property_id": bed.propertyId},
    )
    return {"success": True}
