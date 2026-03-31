import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from bson import ObjectId
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status, BackgroundTasks
from fastapi.responses import FileResponse

from app.config.settings import MONGO_DB_NAME, MONGO_URL, BACKUP_PATH, BACKUP_RETENTION_DAYS
from app.database.mongodb import db
from app.utils.helpers import has_admin_access, require_admin_user


router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger("backup_service")


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


def _serialize_backup(doc: dict) -> dict:
    """Standardize backup metadata for API response using camelCase."""
    data = dict(doc)
    mongo_id = data.pop("_id", None)
    if mongo_id:
        data["id"] = str(mongo_id)
    
    return {
        "id": data.get("id"),
        "filename": data.get("filename"),
        "status": data.get("status"),
        "sizeBytes": data.get("sizeBytes", 0),
        "createdAt": _normalize_value(data.get("createdAt")),
        "startedAt": _normalize_value(data.get("startedAt")),
        "completedAt": _normalize_value(data.get("completedAt")),
        "errorMessage": data.get("errorMessage"),
        "createdBy": data.get("createdBy")
    }


async def _cleanup_old_backups_async():
    """Delete backups older than the retention period from disk and DB."""
    retention_cutoff = datetime.now(timezone.utc) - timedelta(days=BACKUP_RETENTION_DAYS)
    
    cursor = db["backups"].find({"createdAt": {"$lt": retention_cutoff}})
    async for record in cursor:
        file_path = record.get("filePath")
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                logger.error(f"Failed to delete file {file_path}: {e}")
        
        await db["backups"].delete_one({"_id": record["_id"]})


def _run_backup_task(backup_id: str, db_name: str, mongo_uri: str, target_dir: str):
    """Synchronous worker function executed in background thread pool."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"backup_{db_name}_{timestamp}"
    dump_folder = os.path.join(tempfile.gettempdir(), f"dump_{timestamp}")
    archive_base = os.path.join(target_dir, filename)
    
    def _do_mongodump():
        result = subprocess.run(
            ["mongodump", f"--uri={mongo_uri}", f"--out={dump_folder}"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise Exception(result.stderr)
        return result

    async def _update_status(status_value: str, **extra_fields):
        await db["backups"].update_one(
            {"_id": ObjectId(backup_id)},
            {"$set": {"status": status_value, **extra_fields}}
        )

    async def _run_async_backup():
        try:
            # Update status to running
            await _update_status("running", startedAt=datetime.now(timezone.utc))

            # Run mongodump in thread pool to not block
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _do_mongodump)

            # Create zip archive (synchronous, but fast)
            final_zip_path = shutil.make_archive(archive_base, 'zip', dump_folder)
            file_size = os.path.getsize(final_zip_path)

            # Update status to completed
            await _update_status(
                "completed",
                filename=f"{filename}.zip",
                filePath=final_zip_path,
                sizeBytes=file_size,
                completedAt=datetime.now(timezone.utc)
            )
            
            # Cleanup old backups
            await _cleanup_old_backups_async()
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Backup task {backup_id} failed: {error_msg}")
            try:
                await _update_status("failed", errorMessage=error_msg)
            except Exception:
                pass
    
    asyncio.run(_run_async_backup())
    
    # Cleanup dump folder after async work is done
    if os.path.exists(dump_folder):
        shutil.rmtree(dump_folder)


@router.post("/backups/trigger", status_code=status.HTTP_202_ACCEPTED)
async def trigger_backup(
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_admin_user)
):
    """Trigger a non-blocking system backup."""
    new_backup = {
        "filename": "Pending...",
        "status": "pending",
        "sizeBytes": 0,
        "createdAt": datetime.now(timezone.utc),
        "createdBy": current_user.get("email"),
    }
    
    result = await db["backups"].insert_one(new_backup)
    backup_id = str(result.inserted_id)

    mongo_uri = MONGO_URL or f"mongodb://localhost:27017/{MONGO_DB_NAME}"
    
    background_tasks.add_task(
        _run_backup_task, 
        backup_id, 
        MONGO_DB_NAME, 
        mongo_uri, 
        BACKUP_PATH
    )

    return {
        "message": "Backup task initiated",
        "backupId": backup_id,
        "status": "pending"
    }


@router.get("/backups")
async def list_backups(
    page: int = Query(1, ge=1),
    pageSize: int = Query(10, ge=1, le=50),
    current_user: dict = Depends(require_admin_user)
):
    """List backup history and statuses."""
    skip = (page - 1) * pageSize
    total = await db["backups"].count_documents({})
    
    cursor = db["backups"].find().sort("createdAt", -1).skip(skip).limit(pageSize)
    backups = [_serialize_backup(b) async for b in cursor]
    
    return {
        "rows": backups,
        "meta": {
            "total": total,
            "page": page,
            "pageSize": pageSize
        }
    }


@router.get("/backups/{backup_id}/download")
async def download_stored_backup(
    backup_id: str,
    current_user: dict = Depends(require_admin_user)
):
    """Download a completed backup file from disk."""
    try:
        obj_id = ObjectId(backup_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid backup ID format")

    record = await db["backups"].find_one({"_id": obj_id})
    if not record:
        raise HTTPException(status_code=404, detail="Backup record not found")
    
    if record["status"] != "completed":
        raise HTTPException(status_code=400, detail=f"Backup is in {record['status']} state")

    file_path = record.get("filePath")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Backup file missing from storage")

    return FileResponse(
        path=file_path,
        filename=record["filename"],
        media_type="application/zip"
    )


@router.delete("/backups/{backup_id}")
async def delete_backup(
    backup_id: str,
    current_user: dict = Depends(require_admin_user)
):
    """Manually delete a backup file and its record."""
    try:
        obj_id = ObjectId(backup_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid backup ID format")

    record = await db["backups"].find_one({"_id": obj_id})
    if record:
        file_path = record.get("filePath")
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except:
                pass
        await db["backups"].delete_one({"_id": obj_id})
    
    return {"message": "Backup deleted successfully" }


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