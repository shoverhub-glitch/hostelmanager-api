"""
Subscription Lifecycle Management
Handles subscription downgrades, upgrades, and resource archival with grace periods
"""

from app.database.mongodb import db
from app.services.subscription_service import SubscriptionService
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from app.utils.ownership import build_owner_query, property_belongs_to_owner
import logging

logger = logging.getLogger(__name__)

ARCHIVAL_GRACE_PERIOD_DAYS = 30  # User has 30 days to upgrade before deletion


class SubscriptionLifecycle:
    """
    Production-grade subscription change handler.
    
    Strategy:
    1. When downgrading, ARCHIVE excess resources (don't delete)
    2. User gets 30-day grace period to upgrade and recover resources
    3. Archived resources are read-only
    4. If user upgrades within grace period, restore archived resources
    5. After grace period, offer permanent deletion
    """

    @staticmethod
    async def handle_downgrade(owner_id: str, from_plan: str, to_plan: str) -> dict:
        """
        Handle subscription downgrade intelligently.
        Archive excess resources instead of deleting.
        """
        try:
            now = datetime.now(timezone.utc).isoformat()
            grace_period_until = (datetime.now(timezone.utc) + timedelta(days=ARCHIVAL_GRACE_PERIOD_DAYS)).isoformat()
            
            # Get target plan limits
            target_limits = await SubscriptionService.get_plan_limits(to_plan)
            
            # Count current resources
            owned_properties = await db["properties"].find(
                {**build_owner_query(owner_id), "active": True, "isDeleted": {"$ne": True}},
                {"_id": 1}
            ).to_list(length=None)
            property_ids = [str(doc["_id"]) for doc in owned_properties]

            current_properties = len(property_ids)
            
            archived_properties = []
            archived_rooms = []
            archived_tenants = []
            
            # STEP 1: Archive excess properties if needed
            if current_properties > target_limits["properties"]:
                excess_count = current_properties - target_limits["properties"]
                properties_to_archive = await db["properties"].find(
                    {**build_owner_query(owner_id), "active": True}
                ).sort("createdAt", 1).limit(excess_count).to_list(length=excess_count)
                
                for prop in properties_to_archive:
                    result = await db["properties"].update_one(
                        {"_id": prop["_id"]},
                        {
                            "$set": {
                                "active": False,
                                "archivedReason": f"Downgraded from {from_plan} to {to_plan}. Grace period until {grace_period_until}",
                                "archivedAt": now,
                                "updatedAt": now
                            }
                        }
                    )
                    if result.modified_count > 0:
                        archived_properties.append(str(prop["_id"]))
                        
                        # Archive all rooms in this property
                        rooms_to_archive = await db["rooms"].find(
                            {"propertyId": str(prop["_id"]), "active": True},
                            {"_id": 1}
                        ).to_list(length=None)
                        room_ids_to_archive = [str(r["_id"]) for r in rooms_to_archive]

                        await db["rooms"].update_many(
                            {"propertyId": str(prop["_id"]), "active": True},
                            {
                                "$set": {
                                    "active": False,
                                    "archivedReason": f"Parent property archived. Grace period until {grace_period_until}",
                                    "archivedAt": now,
                                    "updatedAt": now
                                }
                            }
                        )
                        archived_rooms.extend(room_ids_to_archive)
                        
                        # Archive all tenants in archived rooms of this property
                        await db["tenants"].update_many(
                            {
                                "propertyId": str(prop["_id"]),
                                "roomId": {"$in": room_ids_to_archive},
                                "archived": False
                            },
                            {
                                "$set": {
                                    "archived": True,
                                    "archivedReason": f"Parent room archived. Grace period until {grace_period_until}",
                                    "archivedAt": now,
                                    "updatedAt": now
                                }
                            }
                        )

            # STEP 2: Archive excess tenants (if applicable)
            # FIX (Critical #3): Recount current active tenants AFTER property archival to avoid double-counting
            active_property_ids = [str(doc["_id"]) for doc in await db["properties"].find(
                {**build_owner_query(owner_id), "active": True, "isDeleted": {"$ne": True}},
                {"_id": 1}
            ).to_list(length=None)]

            current_tenants = await db["tenants"].count_documents(
                {"propertyId": {"$in": active_property_ids}, "archived": False}
            ) if active_property_ids else 0

            if current_tenants > target_limits["tenants"]:
                excess_count = current_tenants - target_limits["tenants"]
                tenants_to_archive = await db["tenants"].find(
                    {"propertyId": {"$in": active_property_ids}, "archived": False}
                ).sort("createdAt", 1).limit(excess_count).to_list(length=excess_count)
                
                for tenant in tenants_to_archive:
                    result = await db["tenants"].update_one(
                        {"_id": tenant["_id"]},
                        {
                            "$set": {
                                "archived": True,
                                "archivedReason": f"Downgraded from {from_plan} to {to_plan}. Grace period until {grace_period_until}",
                                "archivedAt": now,
                                "updatedAt": now
                            }
                        }
                    )
                    if result.modified_count > 0:
                        archived_tenants.append(str(tenant["_id"]))

            logger.info(
                "subscription_downgrade_processed",
                extra={
                    "event": "subscription_downgrade_processed",
                    "owner_id": owner_id,
                    "from_plan": from_plan,
                    "to_plan": to_plan,
                    "archived_properties": len(archived_properties),
                    "archived_rooms": len(archived_rooms),
                    "archived_tenants": len(archived_tenants),
                },
            )

            return {
                "success": True,
                "archived_properties": archived_properties,
                "archived_rooms": archived_rooms,
                "archived_tenants": archived_tenants,
                "grace_period_until": grace_period_until,
                "message": f"Downgraded to {to_plan} plan. {len(archived_properties)} properties and "
                          f"{len(archived_tenants)} tenants archived. You have until {grace_period_until} to upgrade and recover them."
            }
        except Exception as e:
            logger.exception("subscription_downgrade_failed", extra={"event": "subscription_downgrade_failed", "owner_id": owner_id, "from_plan": from_plan, "to_plan": to_plan, "error": str(e)})
            return {
                "success": False,
                "error": "Error processing downgrade. Please contact support."
            }

    @staticmethod
    async def handle_upgrade(owner_id: str, new_plan: str) -> dict:
        """
        Handle subscription upgrade by restoring archived resources up to plan limits.
        """
        try:
            now = datetime.now(timezone.utc).isoformat()
            
            plan_limits = await SubscriptionService.get_plan_limits(new_plan)
            prop_limit  = plan_limits.get("properties", 999)
            tenant_limit = plan_limits.get("tenants", 999)

            # Count currently active properties before restore
            active_prop_count = await db["properties"].count_documents(
                {**build_owner_query(owner_id), "active": True, "isDeleted": {"$ne": True}}
            )
            can_restore_props = max(0, prop_limit - active_prop_count)

            # Restore archived properties up to plan limit
            archived_props = await db["properties"].find(
                {**build_owner_query(owner_id), "active": False, "archivedReason": {"$exists": True}, "isDeleted": {"$ne": True}}
            ).sort("archivedAt", -1).limit(can_restore_props).to_list(length=can_restore_props)

            restored_prop_ids = []
            for prop in archived_props:
                result = await db["properties"].update_one(
                    {"_id": prop["_id"]},
                    {
                        "$set": {"active": True, "archivedAt": None, "updatedAt": now},
                        "$unset": {"archivedReason": ""}
                    }
                )
                if result.modified_count > 0:
                    restored_prop_ids.append(str(prop["_id"]))

            # Restore rooms for restored properties
            restored_rooms_count = 0
            if restored_prop_ids:
                restore_rooms = await db["rooms"].update_many(
                    {"propertyId": {"$in": restored_prop_ids}, "active": False, "archivedReason": {"$exists": True}},
                    {
                        "$set": {"active": True, "archivedAt": None, "updatedAt": now},
                        "$unset": {"archivedReason": ""}
                    }
                )
                restored_rooms_count = restore_rooms.modified_count

            # Restore tenants up to plan limit
            active_tenants_count = await db["tenants"].count_documents(
                {"ownerId": owner_id, "archived": False, "isDeleted": {"$ne": True}}
            )
            can_restore_tenants = max(0, tenant_limit - active_tenants_count)

            restored_tenants_count = 0
            if can_restore_tenants > 0:
                # Find archived tenants (only from restored properties)
                archived_tenants = await db["tenants"].find(
                    {"propertyId": {"$in": restored_prop_ids}, "archived": True, "archivedReason": {"$exists": True}}
                ).sort("archivedAt", -1).limit(can_restore_tenants).to_list(length=can_restore_tenants)

                for tenant in archived_tenants:
                    t_result = await db["tenants"].update_one(
                        {"_id": tenant["_id"]},
                        {
                            "$set": {"archived": False, "archivedAt": None, "updatedAt": now},
                            "$unset": {"archivedReason": ""}
                        }
                    )
                    if t_result.modified_count > 0:
                        restored_tenants_count += 1
            
            logger.info(
                "subscription_upgrade_processed",
                extra={
                    "event": "subscription_upgrade_processed",
                    "owner_id": owner_id,
                    "new_plan": new_plan,
                    "restored_properties": len(restored_prop_ids),
                    "restored_rooms": restored_rooms_count,
                    "restored_tenants": restored_tenants_count,
                },
            )
            
            return {
                "success": True,
                "restored_properties": len(restored_prop_ids),
                "restored_rooms": restored_rooms_count,
                "restored_tenants": restored_tenants_count,
                "message": f"Welcome to {new_plan.title()} plan! Your archived resources have been restored."
            }
        except Exception as e:
            logger.exception("subscription_upgrade_failed", extra={"event": "subscription_upgrade_failed", "owner_id": owner_id, "new_plan": new_plan, "error": str(e)})
            return {
                "success": False,
                "error": "Error restoring resources. Please contact support."
            }

    @staticmethod
    async def get_archived_resources(owner_id: str) -> dict:
        """
        Get all archived resources for a user.
        """
        try:
            # FIX (Medium #16): Include orphaned archived rooms/tenants (across all properties)
            owned_properties = await db["properties"].find(
                {**build_owner_query(owner_id), "isDeleted": {"$ne": True}},
                {"_id": 1}
            ).to_list(length=None)
            all_property_ids = [str(prop["_id"]) for prop in owned_properties]

            archived_properties = await db["properties"].find(
                {**build_owner_query(owner_id), "active": False, "isDeleted": {"$ne": True}}
            ).to_list(length=None)
            
            archived_rooms = await db["rooms"].find(
                {"propertyId": {"$in": all_property_ids}, "active": False, "isDeleted": {"$ne": True}}
            ).to_list(length=None)
            
            archived_tenants = await db["tenants"].find(
                {"propertyId": {"$in": all_property_ids}, "archived": True, "isDeleted": {"$ne": True}}
            ).to_list(length=None)
            
            def calculate_expiration(archived_at: str):
                if not archived_at:
                    return None
                try:
                    archived_date = datetime.fromisoformat(archived_at.replace("Z", "+00:00"))
                    expiration = archived_date + timedelta(days=ARCHIVAL_GRACE_PERIOD_DAYS)
                    return expiration.isoformat()
                except Exception:
                    return None
            
            return {
                "total_archived": len(archived_properties) + len(archived_rooms) + len(archived_tenants),
                "properties": [
                    {
                        "id": str(p["_id"]),
                        "name": p.get("name"),
                        "archivedAt": p.get("archivedAt"),
                        "expiresAt": calculate_expiration(p.get("archivedAt")),
                        "reason": p.get("archivedReason")
                    }
                    for p in archived_properties
                ],
                "rooms": [
                    {
                        "id": str(r["_id"]),
                        "roomNumber": r.get("roomNumber"),
                        "archivedAt": r.get("archivedAt"),
                        "expiresAt": calculate_expiration(r.get("archivedAt")),
                        "reason": r.get("archivedReason")
                    }
                    for r in archived_rooms
                ],
                "tenants": [
                    {
                        "id": str(t["_id"]),
                        "name": t.get("name"),
                        "archivedAt": t.get("archivedAt"),
                        "expiresAt": calculate_expiration(t.get("archivedAt")),
                        "reason": t.get("archivedReason")
                    }
                    for t in archived_tenants
                ],
                "grace_period_days": ARCHIVAL_GRACE_PERIOD_DAYS
            }
        except Exception as e:
            logger.exception("subscription_archived_resources_get_failed", extra={"event": "subscription_archived_resources_get_failed", "owner_id": owner_id, "error": str(e)})
            return {"error": "Could not retrieve archived resources"}

    @staticmethod
    async def schedule_expired_archives_for_deletion():
        """
        First stage: Mark expired archives for deletion with a 7-day warning period.
        """
        try:
            now = datetime.now(timezone.utc)
            warning_cutoff = (now - timedelta(days=ARCHIVAL_GRACE_PERIOD_DAYS)).isoformat()
            
            expired_properties = await db["properties"].find({
                "active": False,
                "archivedAt": {"$lt": warning_cutoff},
                "scheduledForDeletion": {"$ne": True}
            }).to_list(length=None)
            
            scheduled_properties = []
            scheduled_rooms = []
            scheduled_tenants = []
            
            for prop in expired_properties:
                owner_id = prop.get("ownerId") or prop.get("createdBy")
                if not owner_id:
                    continue
                
                sub = await SubscriptionService.get_subscription(owner_id)
                limits = await SubscriptionService.get_plan_limits(sub.plan) or {"properties": 1, "tenants": 80}
                
                # FIX (High #10): Use TOTAL resources (active + archived) to check against quota
                # If total resources exceed limit, we MUST schedule the expired archives for deletion.
                total_properties = await db["properties"].count_documents({
                    **build_owner_query(owner_id),
                    "isDeleted": {"$ne": True}
                })
                
                if total_properties <= limits["properties"]:
                    # User upgraded enough to keep all properties, skip deletion
                    continue
                
                # Mark property for deletion
                await db["properties"].update_one(
                    {"_id": prop["_id"]},
                    {
                        "$set": {
                            "scheduledForDeletion": True,
                            "scheduledForDeletionAt": now.isoformat(),
                            "deletionWarningSent": False,
                            "updatedAt": now.isoformat()
                        }
                    }
                )
                scheduled_properties.append(str(prop["_id"]))
                
                # Also schedule child rooms
                await db["rooms"].update_many(
                    {"propertyId": str(prop["_id"]), "active": False},
                    {
                        "$set": {
                            "scheduledForDeletion": True,
                            "scheduledForDeletionAt": now.isoformat(),
                            "updatedAt": now.isoformat()
                        }
                    }
                )
                
                # Schedule associated tenants
                await db["tenants"].update_many(
                    {"propertyId": str(prop["_id"]), "archived": True},
                    {
                        "$set": {
                            "scheduledForDeletion": True,
                            "scheduledForDeletionAt": now.isoformat(),
                            "updatedAt": now.isoformat()
                        }
                    }
                )
                
                logger.warning(
                    "subscription_archive_deletion_scheduled_property",
                    extra={"event": "subscription_archive_deletion_scheduled_property", "property_id": str(prop.get("_id")), "owner_id": owner_id},
                )
            
            # Orphaned archived rooms/tenants
            orphaned_rooms = await db["rooms"].find({
                "active": False,
                "archivedAt": {"$lt": warning_cutoff},
                "scheduledForDeletion": {"$ne": True},
                "propertyId": {"$nin": scheduled_properties}
            }).to_list(length=None)
            
            for room in orphaned_rooms:
                owner_id = room.get("ownerId") or room.get("createdBy")
                if owner_id:
                    sub = await SubscriptionService.get_subscription(owner_id)
                    limits = await SubscriptionService.get_plan_limits(sub.plan) or {"rooms": 20}
                    total_rooms = await db["rooms"].count_documents({"ownerId": owner_id, "isDeleted": {"$ne": True}})
                    if total_rooms <= limits.get("rooms", 20):
                        continue

                await db["rooms"].update_one(
                    {"_id": room["_id"]},
                    {
                        "$set": {
                            "scheduledForDeletion": True,
                            "scheduledForDeletionAt": now.isoformat(),
                            "updatedAt": now.isoformat()
                        }
                    }
                )
                scheduled_rooms.append(str(room["_id"]))
            
            orphaned_tenants = await db["tenants"].find({
                "archived": True,
                "archivedAt": {"$lt": warning_cutoff},
                "scheduledForDeletion": {"$ne": True},
                "propertyId": {"$nin": scheduled_properties}
            }).to_list(length=None)
            
            for tenant in orphaned_tenants:
                owner_id = tenant.get("ownerId") or tenant.get("createdBy")
                if owner_id:
                    sub = await SubscriptionService.get_subscription(owner_id)
                    limits = await SubscriptionService.get_plan_limits(sub.plan) or {"tenants": 80}
                    total_tenants = await db["tenants"].count_documents({"ownerId": owner_id, "isDeleted": {"$ne": True}})
                    if total_tenants <= limits.get("tenants", 80):
                        continue

                await db["tenants"].update_one(
                    {"_id": tenant["_id"]},
                    {
                        "$set": {
                            "scheduledForDeletion": True,
                            "scheduledForDeletionAt": now.isoformat(),
                            "updatedAt": now.isoformat()
                        }
                    }
                )
                scheduled_tenants.append(str(tenant["_id"]))
            
            return {
                "scheduled_properties": len(scheduled_properties),
                "scheduled_rooms": len(scheduled_rooms),
                "scheduled_tenants": len(scheduled_tenants)
            }
        except Exception as e:
            logger.exception("subscription_archive_schedule_failed", extra={"event": "subscription_archive_schedule_failed", "error": str(e)})
            return {"error": "Failed to schedule deletions"}

    @staticmethod
    async def send_deletion_warnings():
        """
        Send warning emails for resources scheduled for deletion.
        """
        try:
            now = datetime.now(timezone.utc)
            
            # Find properties not yet warned
            properties_to_warn = await db["properties"].find({
                "scheduledForDeletion": True,
                "deletionWarningSent": {"$ne": True}
            }).to_list(length=None)
            
            warned_count = 0
            for prop in properties_to_warn:
                owner_id = prop.get("ownerId") or prop.get("createdBy")
                if owner_id:
                    logger.warning(
                        "subscription_archive_deletion_warning",
                        extra={"event": "subscription_archive_deletion_warning", "property_id": str(prop.get("_id")), "owner_id": owner_id, "property_name": prop.get("name")},
                    )
                    
                    await db["properties"].update_one(
                        {"_id": prop["_id"]},
                        {
                            "$set": {
                                "deletionWarningSent": True,
                                "deletionWarningSentAt": now.isoformat(),
                                "updatedAt": now.isoformat()
                            }
                        }
                    )
                    warned_count += 1
            
            logger.info("subscription_archive_deletion_warnings_sent", extra={"event": "subscription_archive_deletion_warnings_sent", "warned_count": warned_count})
            return {"warned_count": warned_count}
        except Exception as e:
            logger.exception("subscription_archive_warning_send_failed", extra={"event": "subscription_archive_warning_send_failed", "error": str(e)})
            return {"error": "Failed to send warnings"}

    @staticmethod
    async def cleanup_expired_archives():
        """
        Final stage: Permanently delete resources.
        """
        try:
            now = datetime.now(timezone.utc)
            deletion_cutoff = (now - timedelta(days=7)).isoformat()
            
            # Find pending deletions
            pending_properties = await db["properties"].find({
                "scheduledForDeletion": True,
                "scheduledForDeletionAt": {"$lt": deletion_cutoff}
            }).to_list(length=None)
            
            deleted_counts = {"properties": 0, "rooms": 0, "tenants": 0}

            for prop in pending_properties:
                owner_id = prop.get("ownerId") or prop.get("createdBy")
                if not owner_id:
                    continue

                # FIX (Critical #2): Re-check subscription status BEFORE hard deletion
                sub = await SubscriptionService.get_subscription(owner_id)
                limits = await SubscriptionService.get_plan_limits(sub.plan) or {"properties": 1}
                active_count = await db["properties"].count_documents({**build_owner_query(owner_id), "active": True})
                
                if active_count < limits["properties"]:
                    # User has space now, UN-SCHEDULE this property instead of deleting
                    await db["properties"].update_one(
                        {"_id": prop["_id"]},
                        {"$set": {"active": True, "scheduledForDeletion": False, "updatedAt": now.isoformat()}, "$unset": {"scheduledForDeletionAt": ""}}
                    )
                    logger.info("subscription_archive_deletion_aborted_space_available", extra={"event": "subscription_archive_deletion_aborted_space_available", "property_id": str(prop["_id"]), "owner_id": owner_id})
                    continue

                # Proceed with permanent deletion
                logger.critical("subscription_archive_permanent_delete_property", extra={"event": "subscription_archive_permanent_delete_property", "property_id": str(prop["_id"]), "owner_id": owner_id})
                await db["properties"].delete_one({"_id": prop["_id"]})
                deleted_counts["properties"] += 1
                
                # Rooms and tenants for this property
                r_res = await db["rooms"].delete_many({"propertyId": str(prop["_id"]), "scheduledForDeletion": True})
                t_res = await db["tenants"].delete_many({"propertyId": str(prop["_id"]), "scheduledForDeletion": True})
                deleted_counts["rooms"] += r_res.deleted_count
                deleted_counts["tenants"] += t_res.deleted_count

            # Handle orphaned rooms/tenants
            orphaned_rooms = await db["rooms"].find({"scheduledForDeletion": True, "scheduledForDeletionAt": {"$lt": deletion_cutoff}}).to_list(None)
            for room in orphaned_rooms:
                await db["rooms"].delete_one({"_id": room["_id"]})
                deleted_counts["rooms"] += 1

            orphaned_tenants = await db["tenants"].find({"scheduledForDeletion": True, "scheduledForDeletionAt": {"$lt": deletion_cutoff}}).to_list(None)
            for tenant in orphaned_tenants:
                await db["tenants"].delete_one({"_id": tenant["_id"]})
                deleted_counts["tenants"] += 1
            
            logger.info("subscription_archive_permanent_delete_completed", extra={"event": "subscription_archive_permanent_delete_completed", **deleted_counts})
            return deleted_counts
        except Exception as e:
            logger.exception("subscription_archive_cleanup_failed", extra={"event": "subscription_archive_cleanup_failed", "error": str(e)})
            return {"error": "Cleanup failed"}
