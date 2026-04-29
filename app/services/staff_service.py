from app.models.staff_schema import Staff, StaffOut, StaffCreate, StaffUpdate
from app.database.mongodb import getCollection
from datetime import datetime, timezone
from bson import ObjectId
import logging


logger = logging.getLogger(__name__)


class StaffService:
    def __init__(self):
        self.collection = getCollection("staff")

    async def get_staff_list(
        self,
        property_id: str = None,
        search: str = None,
        role: str = None,
        skip: int = 0,
        limit: int = 50,
    ):
        """Get list of staff with optional filtering"""
        query = {}
        
        if property_id:
            query["propertyId"] = property_id
        
        if search:
            query["$or"] = [
                {"name": {"$regex": search, "$options": "i"}},
                {"mobileNumber": {"$regex": search, "$options": "i"}},
                {"address": {"$regex": search, "$options": "i"}},
            ]
        
        if role:
            query["role"] = role

        total = await self.collection.count_documents(query)

        staff_list = await self.collection.find(query).skip(skip).limit(limit).sort(
            "_id", -1
        ).to_list(length=limit)

        logger.info(
            "staff_list_success",
            extra={
                "event": "staff_list_success",
                "property_id": property_id,
                "search": bool(search),
                "role": role,
                "skip": skip,
                "limit": limit,
                "total": total,
                "count": len(staff_list),
            },
        )

        return [
            self._convert_to_out(staff) for staff in staff_list
        ], total

    async def get_staff(self, staff_id: str) -> StaffOut:
        """Get single staff by ID"""
        try:
            staff = await self.collection.find_one({"_id": ObjectId(staff_id)})
            return self._convert_to_out(staff) if staff else None
        except Exception as e:
            logger.warning("staff_get_invalid_id", extra={"event": "staff_get_invalid_id", "staff_id": staff_id, "error": str(e)})
            return None

    async def create_staff(self, staff_data: dict) -> StaffOut:
        """Create new staff member"""
        staff_data["createdAt"] = datetime.now(timezone.utc).isoformat()
        staff_data["updatedAt"] = datetime.now(timezone.utc).isoformat()

        result = await self.collection.insert_one(staff_data)
        created_staff = await self.collection.find_one({"_id": result.inserted_id})
        logger.info(
            "staff_created",
            extra={
                "event": "staff_created",
                "staff_id": str(result.inserted_id),
                "property_id": staff_data.get("propertyId"),
                "role": staff_data.get("role"),
            },
        )
        return self._convert_to_out(created_staff)

    async def update_staff(self, staff_id: str, staff_data: dict) -> StaffOut:
        """Update staff member"""
        staff_data["updatedAt"] = datetime.now(timezone.utc).isoformat()

        try:
            result = await self.collection.find_one_and_update(
                {"_id": ObjectId(staff_id)},
                {"$set": staff_data},
                return_document=True,
            )
            if result:
                logger.info(
                    "staff_updated",
                    extra={
                        "event": "staff_updated",
                        "staff_id": staff_id,
                        "property_id": result.get("propertyId"),
                        "updated_fields": list(staff_data.keys()),
                    },
                )
            return self._convert_to_out(result) if result else None
        except Exception as e:
            logger.warning("staff_update_invalid_id", extra={"event": "staff_update_invalid_id", "staff_id": staff_id, "error": str(e)})
            return None

    async def delete_staff(self, staff_id: str) -> bool:
        """Delete staff member (hard delete)"""
        try:
            result = await self.collection.delete_one(
                {"_id": ObjectId(staff_id)}
            )
            deleted = result.deleted_count > 0
            if deleted:
                logger.info("staff_deleted", extra={"event": "staff_deleted", "staff_id": staff_id})
            return deleted
        except Exception as e:
            logger.warning("staff_delete_invalid_id", extra={"event": "staff_delete_invalid_id", "staff_id": staff_id, "error": str(e)})
            return False

    def _convert_to_out(self, staff_doc) -> StaffOut:
        """Convert MongoDB document to StaffOut model"""
        if not staff_doc:
            return None

        return StaffOut(
            id=str(staff_doc.get("_id")),
            propertyId=staff_doc.get("propertyId"),
            name=staff_doc.get("name"),
            role=staff_doc.get("role"),
            mobileNumber=staff_doc.get("mobileNumber"),
            address=staff_doc.get("address"),
            status=staff_doc.get("status", "active"),
            joiningDate=staff_doc.get("joiningDate"),
            salary=staff_doc.get("salary"),
            emergencyContact=staff_doc.get("emergencyContact"),
            emergencyContactNumber=staff_doc.get("emergencyContactNumber"),
            notes=staff_doc.get("notes"),
            createdAt=staff_doc.get("createdAt"),
            updatedAt=staff_doc.get("updatedAt")
        )
