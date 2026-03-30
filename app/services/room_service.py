from app.models.room_schema import Room
from app.database.mongodb import getCollection
from datetime import datetime,timezone
from bson import ObjectId
from app.services.bed_service import BedService
from app.models.bed_schema import BedCreate
import logging


bed_service = BedService()
logger = logging.getLogger(__name__)


class RoomService:

    def __init__(self):
        self.collection = getCollection("rooms")

    async def get_rooms(self, property_id: str = None):
        query = {"isDeleted": {"$ne": True}}
        if property_id:
            query["propertyId"] = property_id
        cursor = self.collection.find(query)
        rooms = []
        async for doc in cursor:
            doc["id"] = str(doc["_id"])
            rooms.append(Room(**doc))
        logger.info("room_list_success", extra={"event": "room_list_success", "property_id": property_id, "count": len(rooms)})
        return rooms

    async def get_room(self, room_id: str):
        try:
            object_id = ObjectId(room_id)
        except Exception as e:
            logger.warning("room_get_invalid_id", extra={"event": "room_get_invalid_id", "room_id": room_id, "error": str(e)})
            return None

        doc = await self.collection.find_one({"_id": object_id, "isDeleted": {"$ne": True}})
        if doc:
            doc["id"] = str(doc["_id"])
            return Room(**doc)
        return None

    async def create_room(self, room_data: dict):
        now = datetime.now(timezone.utc).isoformat()
        if not room_data.get("createdAt"):
            room_data["createdAt"] = now
        if not room_data.get("updatedAt"):
            room_data["updatedAt"] = now
        # Ensure active is set to True (default for new rooms)
        if "active" not in room_data:
            room_data["active"] = True
        
        room_data["isDeleted"] = False

        # Check if room number already exists for this property
        existing = await self.collection.find_one({
            "propertyId": room_data["propertyId"],
            "roomNumber": room_data["roomNumber"],
            "isDeleted": {"$ne": True}
        })
        if existing:
            logger.warning(
                "room_create_duplicate_number",
                extra={"event": "room_create_duplicate_number", "property_id": room_data.get("propertyId"), "room_number": room_data.get("roomNumber")},
            )
            raise ValueError(f"Room number '{room_data['roomNumber']}' already exists for this property")
        
        result = await self.collection.insert_one(room_data)
        room_data["id"] = str(result.inserted_id)
        # Auto-create beds for this room
        number_of_beds = room_data.get("numberOfBeds", 0)
        property_id = room_data["propertyId"]
        room_id = room_data["id"]
        for i in range(1, number_of_beds + 1):
            bed = BedCreate(
                propertyId=property_id,
                roomId=room_id,
                bedNumber=str(i),
                status="available",
                ownerId=room_data.get("ownerId")
            )
            await bed_service.create_bed(bed)
        logger.info(
            "room_created",
            extra={
                "event": "room_created",
                "room_id": room_data.get("id"),
                "property_id": room_data.get("propertyId"),
                "room_number": room_data.get("roomNumber"),
                "number_of_beds": number_of_beds,
            },
        )
        return Room(**room_data)

    async def update_room(self, room_id: str, room_data: dict):
        from bson import ObjectId
        try:
            object_id = ObjectId(room_id)
        except Exception as e:
            logger.warning("room_update_invalid_id", extra={"event": "room_update_invalid_id", "room_id": room_id, "error": str(e)})
            return None

        current_room = await self.collection.find_one({"_id": object_id, "isDeleted": {"$ne": True}})
        if not current_room:
            logger.warning("room_update_not_found", extra={"event": "room_update_not_found", "room_id": room_id})
            return None

        room_data["updatedAt"] = datetime.now(timezone.utc).isoformat()
        for protected_key in ["isDeleted"]:
            room_data.pop(protected_key, None)

        # If roomNumber is being updated, check for duplicates
        if "roomNumber" in room_data:
            property_id = room_data.get("propertyId") or current_room.get("propertyId")
            existing = await self.collection.find_one({
                "propertyId": property_id,
                "roomNumber": room_data["roomNumber"],
                "_id": {"$ne": object_id},
                "isDeleted": {"$ne": True}
            })
            if existing:
                logger.warning(
                    "room_update_duplicate_number",
                    extra={"event": "room_update_duplicate_number", "room_id": room_id, "property_id": room_data.get("propertyId"), "room_number": room_data.get("roomNumber")},
                )
                raise ValueError(f"Room number '{room_data['roomNumber']}' already exists for this property")
        
        # Handle bed count changes
        if "numberOfBeds" in room_data:
            await self._handle_bed_count_change(room_id, room_data, current_room)
        
        await self.collection.update_one({"_id": object_id}, {"$set": room_data})
        doc = await self.collection.find_one({"_id": object_id})
        if doc:
            doc["id"] = str(doc["_id"])
            logger.info(
                "room_updated",
                extra={"event": "room_updated", "room_id": room_id, "property_id": doc.get("propertyId"), "updated_fields": list(room_data.keys())},
            )
            return Room(**doc)
        return None
    
    async def _handle_bed_count_change(self, room_id: str, room_data: dict, current_room: dict = None):
        """Handle changes in number of beds - relocate or vacate tenants as needed"""
        beds_collection = getCollection("beds")
        tenants_collection = getCollection("tenants")
        
        # Get current room to compare if not provided
        if not current_room:
            current_room = await self.collection.find_one({"_id": ObjectId(room_id), "isDeleted": {"$ne": True}})
            if not current_room:
                return
        
        current_bed_count = current_room.get("numberOfBeds", 0)
        new_bed_count = room_data.get("numberOfBeds", 0)
        property_id = room_data.get("propertyId") or current_room.get("propertyId")
        
        if new_bed_count < current_bed_count:
            # Reducing beds - handle affected tenants
            # FIX: Robustly handle non-numeric bed numbers by sorting and taking the tail
            all_beds = await beds_collection.find({
                "roomId": room_id,
                "isDeleted": {"$ne": True}
            }).sort("bedNumber", 1).to_list(None)
            
            # Use numeric sorting if possible, fallback to string
            try:
                all_beds.sort(key=lambda b: int(b["bedNumber"]))
            except (ValueError, TypeError):
                pass # Already sorted as strings
                
            beds_to_keep = all_beds[:new_bed_count]
            beds_to_remove = all_beds[new_bed_count:]
            
            now = datetime.now(timezone.utc).isoformat()
            keep_bed_ids = [str(b["_id"]) for b in beds_to_keep]

            for bed in beds_to_remove:
                tenant_id = bed.get("tenantId")
                if tenant_id:
                    # First, try to find an available bed in the SAME ROOM
                    available_bed = await beds_collection.find_one({
                        "roomId": room_id,
                        "status": "available",
                        "_id": {"$in": [ObjectId(bid) for bid in keep_bed_ids]},
                        "isDeleted": {"$ne": True}
                    })
                    
                    # If no available bed in same room, try same property
                    if not available_bed:
                        available_bed = await beds_collection.find_one({
                            "propertyId": property_id,
                            "roomId": {"$ne": room_id},
                            "status": "available",
                            "isDeleted": {"$ne": True}
                        })
                    
                    if available_bed:
                        # Move tenant to available bed
                        await beds_collection.update_one(
                            {"_id": available_bed["_id"]},
                            {
                                "$set": {
                                    "status": "occupied",
                                    "tenantId": tenant_id,
                                    "updatedAt": now
                                }
                            }
                        )
                        # Update tenant's bedId and roomId if relocated to different room
                        update_data = {
                            "bedId": str(available_bed["_id"]),
                            "updatedAt": now
                        }
                        if str(available_bed.get("roomId")) != room_id:
                            update_data["roomId"] = str(available_bed["roomId"])
                        
                        await tenants_collection.update_one(
                            {"_id": ObjectId(tenant_id)},
                            {"$set": update_data}
                        )
                    else:
                        # No available bed - mark tenant as vacated
                        # FIX: Clear bedId and roomId
                        await tenants_collection.update_one(
                            {"_id": ObjectId(tenant_id)},
                            {
                                "$set": {
                                    "tenantStatus": "vacated",
                                    "checkoutDate": now,
                                    "billingConfig": None,
                                    "bedId": None,
                                    "roomId": None,
                                    "updatedAt": now
                                }
                            }
                        )
                
                # Soft delete the bed
                await beds_collection.update_one(
                    {"_id": bed["_id"]},
                    {"$set": {"isDeleted": True, "updatedAt": now}}
                )
        
        elif new_bed_count > current_bed_count:
            # Increasing beds - create new beds
            owner_id = current_room.get("ownerId")
            for i in range(current_bed_count + 1, new_bed_count + 1):
                bed = BedCreate(
                    propertyId=property_id,
                    roomId=room_id,
                    bedNumber=str(i),
                    status="available",
                    ownerId=owner_id
                )
                await bed_service.create_bed(bed)

        logger.info(
            "room_bed_count_changed",
            extra={
                "event": "room_bed_count_changed",
                "room_id": room_id,
                "property_id": property_id,
                "old_bed_count": current_bed_count,
                "new_bed_count": new_bed_count,
            },
        )
    
    async def preview_bed_count_change(self, room_id: str, new_bed_count: int):
        """Preview what will happen if bed count is changed"""
        beds_collection = getCollection("beds")
        tenants_collection = getCollection("tenants")
        
        current_room = await self.collection.find_one({"_id": ObjectId(room_id), "isDeleted": {"$ne": True}})
        if not current_room:
            return None
        
        current_bed_count = current_room.get("numberOfBeds", 0)
        property_id = current_room.get("propertyId")
        
        result = {
            "currentBedCount": current_bed_count,
            "newBedCount": new_bed_count,
            "affectedTenants": [],
            "availableBedsInProperty": 0
        }
        
        if new_bed_count < current_bed_count:
            # FIX: Robustly handle non-numeric bed numbers for preview
            all_beds = await beds_collection.find({
                "roomId": room_id,
                "isDeleted": {"$ne": True}
            }).sort("bedNumber", 1).to_list(None)
            
            try:
                all_beds.sort(key=lambda b: int(b["bedNumber"]))
            except (ValueError, TypeError):
                pass

            beds_to_keep = all_beds[:new_bed_count]
            beds_to_remove = all_beds[new_bed_count:]
            keep_bed_ids = [b["_id"] for b in beds_to_keep]

            # Count available beds in same room first
            available_beds_same_room = await beds_collection.count_documents({
                "roomId": room_id,
                "status": "available",
                "_id": {"$in": keep_bed_ids},
                "isDeleted": {"$ne": True}
            })
            
            # Count available beds in other rooms of same property
            available_beds_other_rooms = await beds_collection.count_documents({
                "propertyId": property_id,
                "status": "available",
                "roomId": {"$ne": room_id},
                "isDeleted": {"$ne": True}
            })
            
            result["availableBedsInSameRoom"] = available_beds_same_room
            result["availableBedsInProperty"] = available_beds_other_rooms
            
            available_same_room_index = 0
            available_other_room_index = 0
            
            for bed in beds_to_remove:
                tenant_id = bed.get("tenantId")
                if tenant_id:
                    tenant = await tenants_collection.find_one({"_id": ObjectId(tenant_id), "isDeleted": {"$ne": True}})
                    if tenant:
                        # Determine action: try same room first, then other rooms
                        will_relocate_same_room = available_same_room_index < available_beds_same_room
                        will_relocate_other_room = not will_relocate_same_room and available_other_room_index < available_beds_other_rooms
                        
                        action = "vacate"
                        location = None
                        
                        if will_relocate_same_room:
                            action = "relocate"
                            location = "same_room"
                            available_same_room_index += 1
                        elif will_relocate_other_room:
                            action = "relocate"
                            location = "other_room"
                            available_other_room_index += 1
                        
                        result["affectedTenants"].append({
                            "id": str(tenant["_id"]),
                            "name": tenant.get("name"),
                            "bedNumber": bed.get("bedNumber"),
                            "action": action,
                            "location": location
                        })
        
        logger.info(
            "room_bed_change_preview_generated",
            extra={
                "event": "room_bed_change_preview_generated",
                "room_id": room_id,
                "current_bed_count": current_bed_count,
                "new_bed_count": new_bed_count,
                "affected_tenants": len(result.get("affectedTenants", [])),
            },
        )
        return result

    async def delete_room(self, room_id: str):
        try:
            object_id = ObjectId(room_id)
        except Exception as e:
            logger.warning("room_delete_invalid_id", extra={"event": "room_delete_invalid_id", "room_id": room_id, "error": str(e)})
            return {"success": False, "roomId": room_id}

        # Find all beds in this room
        beds_collection = getCollection("beds")
        tenants_collection = getCollection("tenants")
        
        beds_cursor = beds_collection.find({"roomId": room_id, "isDeleted": {"$ne": True}})
        beds = await beds_cursor.to_list(None)
        
        now = datetime.now(timezone.utc).isoformat()

        # For each bed, update associated tenant to "vacated" status
        for bed in beds:
            bed_id = str(bed["_id"])
            tenant_id = bed.get("tenantId")
            
            if tenant_id:
                # Update tenant to vacated status
                # FIX: Clear bedId and roomId
                await tenants_collection.update_one(
                    {"_id": ObjectId(tenant_id)},
                    {
                        "$set": {
                            "tenantStatus": "vacated",
                            "checkoutDate": now,
                            "billingConfig": None,
                            "bedId": None,
                            "roomId": None,
                            "updatedAt": now
                        }
                    }
                )
                logger.warning(
                    "room_delete_tenant_vacated",
                    extra={"event": "room_delete_tenant_vacated", "room_id": room_id, "tenant_id": tenant_id},
                )
            
            # Soft delete the bed instead of just making it available
            await beds_collection.update_one(
                {"_id": bed["_id"]},
                {
                    "$set": {
                        "isDeleted": True,
                        "status": "available",
                        "tenantId": None,
                        "updatedAt": now
                    }
                }
            )
        
        # Soft delete the room
        await self.collection.update_one(
            {"_id": object_id}, 
            {"$set": {"isDeleted": True, "updatedAt": now}}
        )
        logger.info("room_deleted", extra={"event": "room_deleted", "room_id": room_id, "beds_deleted": len(beds)})
        return {"success": True, "roomId": room_id}
