import os
from datetime import date, datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from app.config.settings import MONGO_DB_NAME, MONGO_URL
from app.database.mongodb import db
from app.utils.helpers import has_admin_access, require_admin_user


router = APIRouter(prefix="/admin", tags=["admin"])


def _normalize_value(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_value(item) for key, item in value.items()}
    return value


def _serialize_document(doc: dict[str, Any]) -> dict[str, Any]:
    data = dict(doc)
    mongo_id = data.pop("_id", None)
    if mongo_id is not None:
        data["id"] = str(mongo_id)
    return _normalize_value(data)


def _sanitize_user(doc: dict[str, Any]) -> dict[str, Any]:
    data = dict(doc)
    data.pop("password", None)
    data.pop("hashed_password", None)
    return data


def _to_object_id(raw_id: str, resource: str) -> ObjectId:
    try:
        return ObjectId(raw_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {resource} id",
        )


def _build_search_filter(search: Optional[str], fields: list[str]) -> Optional[dict[str, Any]]:
    if not search:
        return None
    value = search.strip()
    if not value:
        return None
    return {"$or": [{field: {"$regex": value, "$options": "i"}} for field in fields]}


def _combine_filters(base_filter: dict[str, Any], search_filter: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not search_filter:
        return base_filter
    if not base_filter:
        return search_filter
    return {"$and": [base_filter, search_filter]}


async def _paginated_list(
    collection: str,
    filters: dict[str, Any],
    page: int,
    page_size: int,
    sort_field: str = "updatedAt",
):
    safe_page = max(1, page)
    safe_page_size = min(100, max(1, page_size))
    skip = (safe_page - 1) * safe_page_size

    total = await db[collection].count_documents(filters)
    cursor = (
        db[collection]
        .find(filters)
        .sort(sort_field, -1)
        .skip(skip)
        .limit(safe_page_size)
    )
    items = [_serialize_document(doc) async for doc in cursor]
    return {
        "data": items,
        "meta": {
            "total": total,
            "page": safe_page,
            "pageSize": safe_page_size,
            "hasMore": skip + safe_page_size < total,
        },
    }


async def _update_document(
    collection: str,
    resource_id: str,
    payload: dict[str, Any],
    immutable_fields: set[str],
    not_found_message: str,
):
    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Update payload is required")

    cleaned_payload = {k: v for k, v in payload.items() if k not in immutable_fields}
    if not cleaned_payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No updatable fields provided")

    cleaned_payload["updatedAt"] = datetime.now(timezone.utc)
    object_id = _to_object_id(resource_id, collection.rstrip("s"))

    result = await db[collection].update_one({"_id": object_id}, {"$set": cleaned_payload})
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=not_found_message)

    updated = await db[collection].find_one({"_id": object_id})
    if not updated:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch updated document")
    return {"data": _serialize_document(updated)}


@router.get("/me")
async def admin_me(current_user: dict = Depends(require_admin_user)):
    """Get current admin user profile."""
    profile = _sanitize_user(current_user)
    profile["adminAccess"] = True
    return {"data": _serialize_document(profile)}


@router.get("/overview")
async def admin_overview(current_user: dict = Depends(require_admin_user)):
    """Return high-level platform counts for admin dashboard."""
    users_count = await db["users"].count_documents({"isDeleted": {"$ne": True}})
    properties_count = await db["properties"].count_documents({})
    tenants_count = await db["tenants"].count_documents({"isDeleted": {"$ne": True}})
    rooms_count = await db["rooms"].count_documents({})
    payments_count = await db["payments"].count_documents({"isDeleted": {"$ne": True}})
    subscriptions_count = await db["subscriptions"].count_documents({})
    return {
        "data": {
            "users": users_count,
            "properties": properties_count,
            "tenants": tenants_count,
            "rooms": rooms_count,
            "payments": payments_count,
            "subscriptions": subscriptions_count,
        }
    }


@router.get("/users")
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    search: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    current_user: dict = Depends(require_admin_user),
):
    filters: dict[str, Any] = {"isDeleted": {"$ne": True}}
    if role:
        filters["role"] = role
    search_filter = _build_search_filter(search, ["name", "email", "phone"])
    combined = _combine_filters(filters, search_filter)
    result = await _paginated_list("users", combined, page, page_size, sort_field="updatedAt")
    result["data"] = [_serialize_document(_sanitize_user(item)) for item in result["data"]]
    return result


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    payload: dict = Body(...),
    current_user: dict = Depends(require_admin_user),
):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload must be an object")

    if str(current_user.get("_id")) == user_id:
        updated_self = dict(current_user)
        updated_self.update(payload)
        if not has_admin_access(updated_self):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot remove your own admin access",
            )

    result = await _update_document(
        "users",
        user_id,
        payload,
        immutable_fields={"_id", "id", "password", "hashed_password", "createdAt"},
        not_found_message="User not found",
    )
    result["data"] = _sanitize_user(result["data"])
    return result


@router.get("/properties")
async def list_properties(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    search: Optional[str] = Query(None),
    owner_id: Optional[str] = Query(None),
    active: Optional[bool] = Query(None),
    current_user: dict = Depends(require_admin_user),
):
    filters: dict[str, Any] = {}
    if owner_id:
        filters["ownerIds"] = owner_id
    if active is not None:
        filters["active"] = active
    search_filter = _build_search_filter(search, ["name", "address"])
    return await _paginated_list("properties", _combine_filters(filters, search_filter), page, page_size)


@router.patch("/properties/{property_id}")
async def update_property(
    property_id: str,
    payload: dict = Body(...),
    current_user: dict = Depends(require_admin_user),
):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload must be an object")
    return await _update_document(
        "properties",
        property_id,
        payload,
        immutable_fields={"_id", "id", "createdAt"},
        not_found_message="Property not found",
    )


@router.get("/tenants")
async def list_tenants(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    search: Optional[str] = Query(None),
    property_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    current_user: dict = Depends(require_admin_user),
):
    filters: dict[str, Any] = {"isDeleted": {"$ne": True}}
    if property_id:
        filters["propertyId"] = property_id
    if status:
        filters["tenantStatus"] = status
    search_filter = _build_search_filter(search, ["name", "phone", "documentId"])
    return await _paginated_list("tenants", _combine_filters(filters, search_filter), page, page_size)


@router.patch("/tenants/{tenant_id}")
async def update_tenant(
    tenant_id: str,
    payload: dict = Body(...),
    current_user: dict = Depends(require_admin_user),
):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload must be an object")
    return await _update_document(
        "tenants",
        tenant_id,
        payload,
        immutable_fields={"_id", "id", "createdAt"},
        not_found_message="Tenant not found",
    )


@router.get("/rooms")
async def list_rooms(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    search: Optional[str] = Query(None),
    property_id: Optional[str] = Query(None),
    active: Optional[bool] = Query(None),
    current_user: dict = Depends(require_admin_user),
):
    filters: dict[str, Any] = {}
    if property_id:
        filters["propertyId"] = property_id
    if active is not None:
        filters["active"] = active
    search_filter = _build_search_filter(search, ["roomNumber", "floor"])
    return await _paginated_list("rooms", _combine_filters(filters, search_filter), page, page_size)


@router.patch("/rooms/{room_id}")
async def update_room(
    room_id: str,
    payload: dict = Body(...),
    current_user: dict = Depends(require_admin_user),
):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload must be an object")
    return await _update_document(
        "rooms",
        room_id,
        payload,
        immutable_fields={"_id", "id", "createdAt"},
        not_found_message="Room not found",
    )


@router.get("/payments")
async def list_payments(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    tenant_id: Optional[str] = Query(None),
    property_id: Optional[str] = Query(None),
    status_value: Optional[str] = Query(None, alias="status"),
    current_user: dict = Depends(require_admin_user),
):
    filters: dict[str, Any] = {"isDeleted": {"$ne": True}}
    if tenant_id:
        filters["tenantId"] = tenant_id
    if property_id:
        filters["propertyId"] = property_id
    if status_value:
        filters["status"] = status_value
    return await _paginated_list("payments", filters, page, page_size)


@router.patch("/payments/{payment_id}")
async def update_payment(
    payment_id: str,
    payload: dict = Body(...),
    current_user: dict = Depends(require_admin_user),
):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload must be an object")
    return await _update_document(
        "payments",
        payment_id,
        payload,
        immutable_fields={"_id", "id", "createdAt"},
        not_found_message="Payment not found",
    )


@router.get("/subscriptions")
async def list_subscriptions(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    owner_id: Optional[str] = Query(None),
    plan: Optional[str] = Query(None),
    status_value: Optional[str] = Query(None, alias="status"),
    current_user: dict = Depends(require_admin_user),
):
    filters: dict[str, Any] = {}
    if owner_id:
        filters["ownerId"] = owner_id
    if plan:
        filters["plan"] = plan
    if status_value:
        filters["status"] = status_value
    return await _paginated_list("subscriptions", filters, page, page_size)


@router.patch("/subscriptions/{subscription_id}")
async def update_subscription(
    subscription_id: str,
    payload: dict = Body(...),
    current_user: dict = Depends(require_admin_user),
):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload must be an object")
    return await _update_document(
        "subscriptions",
        subscription_id,
        payload,
        immutable_fields={"_id", "id", "createdAt"},
        not_found_message="Subscription not found",
    )


@router.get("/database-stats")
async def get_database_stats(current_user: dict = Depends(require_admin_user)):
    """
    Get MongoDB database statistics including storage info.
    """
    try:
        stats = await db.command("dbStats")
        
        collection_stats = []
        collections = await db.list_collection_names()
        
        for coll_name in collections:
            try:
                coll_stats = await db.command("collStats", coll_name)
                collection_stats.append({
                    "name": coll_name,
                    "count": coll_stats.get("count", 0),
                    "sizeBytes": coll_stats.get("size", 0),
                    "avgObjSize": coll_stats.get("avgObjSize", 0),
                    "storageSizeBytes": coll_stats.get("storageSize", 0),
                    "totalIndexSizeBytes": coll_stats.get("totalIndexSize", 0),
                    "numIndexes": coll_stats.get("nindexes", 0),
                })
            except Exception:
                pass
        
        collection_stats.sort(key=lambda x: x.get("sizeBytes", 0), reverse=True)
        
        return {
            "data": {
                "database": {
                    "name": stats.get("db"),
                    "rawDataSizeBytes": stats.get("dataSize", 0),
                    "storageSizeBytes": stats.get("storageSize", 0),
                    "indexSizeBytes": stats.get("indexSize", 0),
                    "totalSizeBytes": stats.get("totalSize", 0),
                    "objectCount": stats.get("objects", 0),
                    "collectionsCount": stats.get("collections", 0),
                    "viewsCount": stats.get("views", 0),
                },
                "collections": collection_stats
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get database stats: {str(e)}")