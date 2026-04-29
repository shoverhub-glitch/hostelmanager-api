
import logging
from app.database.mongodb import db
from app.models.property_schema import PropertyOut
from app.utils.ownership import build_owner_query, normalize_property_owners
from typing import List
from datetime import datetime, timezone
from bson import ObjectId
from fastapi import HTTPException, status


logger = logging.getLogger(__name__)

class PropertyService:
    def __init__(self):
        self.db = db

    @staticmethod
    def _require_owner_id(owner_id: str | None) -> str:
        if not owner_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
        return owner_id

    @staticmethod
    def _to_object_id(value: str, field_name: str = "id") -> ObjectId:
        try:
            return ObjectId(value)
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {field_name}")

    @staticmethod
    def _sanitize_property_create(data: dict) -> dict:
        return {
            "name": data.get("name"),
            "address": data.get("address"),
        }

    @staticmethod
    def _sanitize_property_update(data: dict) -> dict:
        updates = dict(data)
        # Ownership and archival controls are system-managed and cannot be mutated from this endpoint.
        for protected_key in [
            "_id",
            "id",
            "ownerId",
            "ownerIds",
            "createdAt",
            "active",
        ]:
            updates.pop(protected_key, None)
        return updates

    async def create_property(self, property_data: dict, owner_id: str) -> PropertyOut:
        owner_id = self._require_owner_id(owner_id)
        now = datetime.now(timezone.utc).isoformat()
        doc = self._sanitize_property_create(property_data)
        doc["ownerIds"] = [owner_id]
        doc["ownerId"] = owner_id
        # ...existing code...
        doc["active"] = True
        doc["createdAt"] = now
        doc["updatedAt"] = now
        result = await self.db["properties"].insert_one(doc)
        doc["id"] = str(result.inserted_id)
        normalize_property_owners(doc, fallback_owner_id=owner_id)
        # Update user document to add propertyId
        await self.db["users"].update_one(
            {"_id": self._to_object_id(owner_id, field_name="owner id")},
            {"$addToSet": {"propertyIds": doc["id"]}}
        )
        logger.info(
            "property_created",
            extra={
                "event": "property_created",
                "property_id": doc.get("id"),
                "owner_id": owner_id,
                "property_name": doc.get("name"),
            },
        )
        return PropertyOut(**doc)

    async def list_properties(self, user_id: str) -> List[PropertyOut]:
        """List all properties for a user - kept for backward compatibility"""
        properties, _ = await self.list_properties_paginated(user_id, skip=0, limit=1000)
        return properties
    
    async def list_properties_paginated(self, user_id: str, skip: int = 0, limit: int = 50):
        """List user properties with pagination and ownership normalization."""
        user_id = self._require_owner_id(user_id)
        query = build_owner_query(user_id)
        
        # Get total count
        total = await self.db["properties"].count_documents(query)
        
        # Fetch paginated results
        properties = []
        cursor = self.db["properties"].find(query).skip(skip).limit(limit)
        async for doc in cursor:
            doc["id"] = str(doc["_id"])

            original_owner_id = doc.get("ownerId")
            original_owner_ids = doc.get("ownerIds") if isinstance(doc.get("ownerIds"), list) else None
            normalize_property_owners(doc, fallback_owner_id=user_id)

            if (
                not isinstance(original_owner_id, str)
                or original_owner_ids != doc.get("ownerIds")
            ):
                await self.db["properties"].update_one(
                    {"_id": doc["_id"]},
                    {
                        "$set": {
                            "ownerId": doc.get("ownerId"),
                            "ownerIds": doc.get("ownerIds", []),
                        }
                    }
                )

            properties.append(PropertyOut(**doc))

        logger.info(
            "property_list_success",
            extra={
                "event": "property_list_success",
                "owner_id": user_id,
                "skip": skip,
                "limit": limit,
                "count": len(properties),
                "total": total,
            },
        )
        
        return properties, total

    async def _list_properties_paginated(self, user_id: str, skip: int = 0, limit: int = 50):
        """Backward-compatible wrapper."""
        return await self.list_properties_paginated(user_id=user_id, skip=skip, limit=limit)

    async def update_property(self, property_id: str, owner_id: str, property_update: dict) -> PropertyOut | None:
        owner_id = self._require_owner_id(owner_id)
        now = datetime.now(timezone.utc).isoformat()
        updates = self._sanitize_property_update(property_update)
        if not updates:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No updatable fields provided")
        updates["updatedAt"] = now

        object_id = self._to_object_id(property_id, field_name="property id")

        match_query = {"_id": object_id, **build_owner_query(owner_id)}
        existing = await self.db["properties"].find_one(match_query)
        if not existing:
            logger.warning(
                "property_update_not_found_or_forbidden",
                extra={"event": "property_update_not_found_or_forbidden", "property_id": property_id, "owner_id": owner_id},
            )
            return None

        await self.db["properties"].update_one({"_id": object_id}, {"$set": updates})
        doc = await self.db["properties"].find_one({"_id": object_id})
        if not doc:
            return None
        doc["id"] = str(doc["_id"])
        normalize_property_owners(doc, fallback_owner_id=owner_id)
        logger.info(
            "property_updated",
            extra={
                "event": "property_updated",
                "property_id": property_id,
                "owner_id": owner_id,
                "updated_fields": list(updates.keys()),
            },
        )
        return PropertyOut(**doc)

    async def delete_property(self, property_id: str, owner_id: str) -> dict:
        owner_id = self._require_owner_id(owner_id)
        object_id = self._to_object_id(property_id, field_name="property id")

        match_query = {"_id": object_id, **build_owner_query(owner_id)}
        existing = await self.db["properties"].find_one(match_query)
        if not existing:
            logger.warning(
                "property_delete_not_found_or_forbidden",
                extra={"event": "property_delete_not_found_or_forbidden", "property_id": property_id, "owner_id": owner_id},
            )
            return {"success": False, "propertyId": property_id}

        # Hard delete all related data
        # 1. Hard delete all tenants for this property
        tenants_result = await self.db["tenants"].delete_many({"propertyId": property_id})
        
        # 2. Hard delete all payments for this property
        payments_result = await self.db["payments"].delete_many({"propertyId": property_id})
        
        # 3. Hard delete all beds for this property
        beds_result = await self.db["beds"].delete_many({"propertyId": property_id})
        
        # 4. Hard delete all rooms for this property
        rooms_result = await self.db["rooms"].delete_many({"propertyId": property_id})
        
        # 5. Hard delete all staff for this property
        staff_result = await self.db["staff"].delete_many({"propertyId": property_id})

        # Hard delete the property itself
        await self.db["properties"].delete_one({"_id": object_id})
        
        # Remove property ID from all users who have it
        await self.db["users"].update_many(
            {"propertyIds": property_id},
            {"$pull": {"propertyIds": property_id}}
        )

        logger.info(
            "property_deleted_with_cascade",
            extra={
                "event": "property_deleted_with_cascade",
                "property_id": property_id,
                "owner_id": owner_id,
                "tenants_deleted": tenants_result.modified_count,
                "payments_deleted": payments_result.modified_count,
                "beds_deleted": beds_result.modified_count,
                "rooms_deleted": rooms_result.modified_count,
                "staff_deleted": staff_result.modified_count,
            },
        )
        
        return {"success": True, "propertyId": property_id}