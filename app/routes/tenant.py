from fastapi import APIRouter, HTTPException, status, Request
from app.services.tenant_service import TenantService
from app.services.subscription_enforcement import SubscriptionEnforcement
from app.models.tenant_schema import TenantCreate, TenantUpdate
import logging

router = APIRouter(prefix="/tenants", tags=["tenants"])
tenant_service = TenantService()
logger = logging.getLogger(__name__)

@router.get("")
async def get_tenants(
    request: Request,
    property_id: str = None,
    search: str = None,
    status: str = None,
    page: int = 1,
    page_size: int = 50,
    sort: str = None
):
    page = max(1, page)
    page_size = min(100, max(1, page_size))  # Cap at 100 per page
    skip = (page - 1) * page_size

    property_ids = getattr(request.state, "property_ids", [])
    if not property_ids:
        logger.info("tenant_list_empty_scope", extra={"event": "tenant_list_empty_scope"})
        return {
            "data": [],
            "meta": {
                "total": 0,
                "page": page,
                "pageSize": page_size,
                "hasMore": False
            }
        }

    if property_id and property_id not in property_ids:
        logger.warning("tenant_list_forbidden_property", extra={"event": "tenant_list_forbidden_property", "property_id": property_id})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    scoped_property_ids = [property_id] if property_id else property_ids
    
    tenants, total = await tenant_service.get_tenants(
        property_id=property_id,
        search=search,
        status=status,
        skip=skip,
        limit=page_size,
        include_room_bed=True,  # Enrich with room/bed data
        property_ids=scoped_property_ids,
        sort=sort,
    )

    logger.info(
        "tenant_list_route_success",
        extra={
            "event": "tenant_list_route_success",
            "property_id": property_id,
            "page": page,
            "page_size": page_size,
            "total": total,
            "returned": len(tenants),
        },
    )

    return {
        "data": [tenant.model_dump(exclude_none=True) for tenant in tenants],
        "meta": {
            "total": total,
            "page": page,
            "pageSize": page_size,
            "hasMore": skip + page_size < total
        }
    }

@router.get("/{tenant_id}")
async def get_tenant(request: Request, tenant_id: str):
    tenant = await tenant_service.get_tenant(tenant_id)
    property_ids = getattr(request.state, "property_ids", [])
    if tenant and tenant.propertyId in property_ids:
        logger.info("tenant_get_route_success", extra={"event": "tenant_get_route_success", "tenant_id": tenant_id, "property_id": tenant.propertyId})
        return {"data": tenant.model_dump()}
    logger.warning("tenant_get_route_forbidden", extra={"event": "tenant_get_route_forbidden", "tenant_id": tenant_id})
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

@router.post("")
async def create_tenant(request: Request, tenant: TenantCreate):
    try:
        if not tenant.propertyId:
            logger.warning("tenant_create_missing_property", extra={"event": "tenant_create_missing_property"})
            raise HTTPException(status_code=400, detail="propertyId is required")
        property_ids = getattr(request.state, "property_ids", [])
        if tenant.propertyId not in property_ids:
            logger.warning("tenant_create_forbidden_property", extra={"event": "tenant_create_forbidden_property", "property_id": tenant.propertyId})
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        
        # Check subscription quota before creating tenant
        user_id = getattr(request.state, "user_id", None)
        await SubscriptionEnforcement.ensure_can_create_tenant(user_id, tenant.propertyId)
        
        created = await tenant_service.create_tenant(tenant.model_dump(exclude_unset=True))
        logger.info("tenant_create_route_success", extra={"event": "tenant_create_route_success", "tenant_id": created.id, "property_id": created.propertyId})
        return {"data": created.model_dump()}
    except ValueError as e:
        # Handle validation errors (like bed already occupied)
        logger.warning("tenant_create_validation_failed", extra={"event": "tenant_create_validation_failed", "property_id": tenant.propertyId, "error": str(e)})
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("tenant_create_failed", extra={"event": "tenant_create_failed", "property_id": tenant.propertyId, "error": str(e)})
        raise HTTPException(status_code=500, detail="Error creating tenant. Please try again.")

@router.patch("/{tenant_id}")
async def patch_tenant(request: Request, tenant_id: str, tenant: TenantUpdate):
    try:
        orig = await tenant_service.get_tenant(tenant_id)
        property_ids = getattr(request.state, "property_ids", [])
        if orig and orig.propertyId in property_ids:
            # Check if tenant is archived
            await SubscriptionEnforcement.ensure_tenant_not_archived(tenant_id)
            
            updated = await tenant_service.update_tenant(tenant_id, tenant.model_dump(exclude_unset=True))
            logger.info("tenant_update_route_success", extra={"event": "tenant_update_route_success", "tenant_id": tenant_id, "property_id": orig.propertyId})
            return {"data": updated.model_dump()} if updated else {"data": {}}
        logger.warning("tenant_update_route_forbidden", extra={"event": "tenant_update_route_forbidden", "tenant_id": tenant_id})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    except ValueError as e:
        # Handle validation errors (like bed already occupied)
        logger.warning("tenant_update_validation_failed", extra={"event": "tenant_update_validation_failed", "tenant_id": tenant_id, "error": str(e)})
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("tenant_update_failed", extra={"event": "tenant_update_failed", "tenant_id": tenant_id, "error": str(e)})
        raise HTTPException(status_code=500, detail="Error updating tenant. Please try again.")

@router.delete("/{tenant_id}")
async def delete_tenant(request: Request, tenant_id: str):
    try:
        orig = await tenant_service.get_tenant(tenant_id)
        property_ids = getattr(request.state, "property_ids", [])
        if orig and orig.propertyId in property_ids:
            # Check if tenant is archived
            await SubscriptionEnforcement.ensure_tenant_not_archived(tenant_id)
            
            result = await tenant_service.delete_tenant(tenant_id)
            logger.info("tenant_delete_route_success", extra={"event": "tenant_delete_route_success", "tenant_id": tenant_id, "property_id": orig.propertyId})
            return {"data": result}
        logger.warning("tenant_delete_route_forbidden", extra={"event": "tenant_delete_route_forbidden", "tenant_id": tenant_id})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("tenant_delete_failed", extra={"event": "tenant_delete_failed", "tenant_id": tenant_id, "error": str(e)})
        raise HTTPException(status_code=500, detail="Error deleting tenant. Please try again.")
