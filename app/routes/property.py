from fastapi import APIRouter, status, Request, HTTPException
import logging
from app.models.property_schema import PropertyCreate, PropertyOut, PropertyUpdate
from app.services.property_service import PropertyService
from app.services.subscription_enforcement import SubscriptionEnforcement


router = APIRouter(prefix="/properties", tags=["properties"])
property_service = PropertyService()
logger = logging.getLogger(__name__)


def _get_user_id_from_request(request: Request) -> str:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user_id

@router.post("", status_code=status.HTTP_201_CREATED, response_model=PropertyOut)
async def create_property(request: Request, property: PropertyCreate):
    try:
        user_id = _get_user_id_from_request(request)
        
        # Check subscription quota before creating property
        await SubscriptionEnforcement.ensure_can_create_property(user_id)
        
        created = await property_service.create_property(property.model_dump(exclude_unset=True), user_id)
        logger.info(
            "property_route_create_success",
            extra={"event": "property_route_create_success", "property_id": created.id, "owner_id": user_id},
        )
        return created
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("property_route_create_failed", extra={"event": "property_route_create_failed", "error": str(e)})
        raise HTTPException(status_code=500, detail="Error creating property. Please try again.")

@router.get("")
async def get_properties(
    request: Request,
    page: int = 1,
    page_size: int = 50
):
    """Get properties with pagination support"""
    try:
        # Validate and normalize pagination params
        page = max(1, page)
        page_size = min(100, max(1, page_size))  # Cap at 100 per page
        skip = (page - 1) * page_size
        
        user_id = _get_user_id_from_request(request)
        properties, total = await property_service.list_properties_paginated(user_id, skip=skip, limit=page_size)

        logger.info(
            "property_route_list_success",
            extra={"event": "property_route_list_success", "owner_id": user_id, "page": page, "page_size": page_size, "total": total},
        )
        
        return {
            "data": [prop.model_dump() for prop in properties],
            "meta": {
                "total": total,
                "page": page,
                "pageSize": page_size,
                "hasMore": skip + page_size < total
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("property_route_list_failed", extra={"event": "property_route_list_failed", "error": str(e)})
        raise HTTPException(status_code=500, detail="Error retrieving properties. Please try again.")

@router.patch("/{property_id}")
async def update_property(request: Request, property_id: str, property_update: PropertyUpdate):
    try:
        user_id = _get_user_id_from_request(request)
        
        # Check if property is archived
        await SubscriptionEnforcement.ensure_property_not_archived(property_id)
        
        # Update property
        updated = await property_service.update_property(property_id, user_id, property_update.model_dump(exclude_unset=True))
        if not updated:
            logger.warning(
                "property_route_update_not_found",
                extra={"event": "property_route_update_not_found", "property_id": property_id, "owner_id": user_id},
            )
            raise HTTPException(status_code=404, detail="Property not found")
        logger.info(
            "property_route_update_success",
            extra={"event": "property_route_update_success", "property_id": property_id, "owner_id": user_id},
        )
        return updated
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("property_route_update_failed", extra={"event": "property_route_update_failed", "property_id": property_id, "error": str(e)})
        raise HTTPException(status_code=500, detail="Error updating property. Please try again.")

@router.delete("/{property_id}")
async def delete_property(request: Request, property_id: str):
    try:
        user_id = _get_user_id_from_request(request)
        
        # Check if property is archived
        await SubscriptionEnforcement.ensure_property_not_archived(property_id)
        
        # Delete property
        result = await property_service.delete_property(property_id, user_id)
        if not result.get("success"):
            logger.warning(
                "property_route_delete_not_found",
                extra={"event": "property_route_delete_not_found", "property_id": property_id, "owner_id": user_id},
            )
            raise HTTPException(status_code=404, detail="Property not found")
        logger.info(
            "property_route_delete_success",
            extra={"event": "property_route_delete_success", "property_id": property_id, "owner_id": user_id},
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("property_route_delete_failed", extra={"event": "property_route_delete_failed", "property_id": property_id, "error": str(e)})
        raise HTTPException(status_code=500, detail="Error deleting property. Please try again.")
