"""
Plan Service
Manages subscription plan CRUD operations for admin.
Plans are stored in MongoDB and used by all property owners.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional
from bson import ObjectId
import logging

from app.database.mongodb import db
from app.models.plan_schema import Plan, PlanCreate, PlanUpdate


logger = logging.getLogger(__name__)


class PlanService:
    """Service for managing subscription plans"""

    @staticmethod
    async def create_plan(plan_data: PlanCreate) -> Plan:
        """
        Create a new subscription plan (Admin only)
        
        Args:
            plan_data: Plan creation data
            
        Returns:
            Created plan
            
        Raises:
            ValueError: If plan name already exists
        """
        # Check if plan with same name exists
        existing = await db.plans.find_one({"name": plan_data.name})
        if existing:
            logger.warning("plan_create_duplicate", extra={"event": "plan_create_duplicate", "plan_name": plan_data.name})
            raise ValueError(f"Plan with name '{plan_data.name}' already exists")
        
        # Convert to dict and add timestamps
        plan_dict = plan_data.model_dump()
        plan_dict['created_at'] = datetime.now(timezone.utc)
        plan_dict['updated_at'] = datetime.now(timezone.utc)
        
        # Insert into database
        result = await db.plans.insert_one(plan_dict)
        
        # Fetch and return created plan
        created_plan = await db.plans.find_one({"_id": result.inserted_id})
        created_plan['id'] = str(created_plan['_id'])

        logger.info(
            "plan_created",
            extra={
                "event": "plan_created",
                "plan_name": created_plan.get("name"),
                "is_active": created_plan.get("is_active"),
                "sort_order": created_plan.get("sort_order"),
            },
        )
        
        return Plan(**created_plan)

    @staticmethod
    async def get_plan_by_name(name: str) -> Optional[Plan]:
        """
        Get a plan by its name
        
        Args:
            name: Plan name
            
        Returns:
            Plan if found, None otherwise
        """
        plan = await db.plans.find_one({"name": name.lower()})
        if plan:
            plan['id'] = str(plan['_id'])
            return Plan(**plan)
        return None

    @staticmethod
    async def get_plan_by_id(plan_id: str) -> Optional[Plan]:
        """
        Get a plan by its ID
        
        Args:
            plan_id: Plan ID
            
        Returns:
            Plan if found, None otherwise
        """
        try:
            plan = await db.plans.find_one({"_id": ObjectId(plan_id)})
            if plan:
                plan['id'] = str(plan['_id'])
                return Plan(**plan)
        except Exception as e:
            logger.warning("plan_get_by_id_invalid", extra={"event": "plan_get_by_id_invalid", "plan_id": plan_id, "error": str(e)})
        return None

    @staticmethod
    async def get_all_plans(active_only: bool = False) -> List[Plan]:
        """
        Get all subscription plans
        
        Args:
            active_only: If True, return only active plans
            
        Returns:
            List of plans sorted by sort_order
        """
        query = {"is_active": True} if active_only else {}
        cursor = db.plans.find(query).sort("sort_order", 1)
        
        plans = []
        async for plan in cursor:
            plan['id'] = str(plan['_id'])
            plans.append(Plan(**plan))

        logger.info("plan_list_success", extra={"event": "plan_list_success", "active_only": active_only, "count": len(plans)})
        
        return plans

    @staticmethod
    async def update_plan(plan_name: str, update_data: PlanUpdate) -> Optional[Plan]:
        """
        Update an existing plan (Admin only)
        
        Args:
            plan_name: Name of plan to update
            update_data: Fields to update
            
        Returns:
            Updated plan if found, None otherwise
        """
        # Get existing plan
        existing = await db.plans.find_one({"name": plan_name.lower()})
        if not existing:
            logger.warning("plan_update_not_found", extra={"event": "plan_update_not_found", "plan_name": plan_name.lower()})
            return None
        
        # Prepare update dict (exclude None values)
        update_dict = update_data.model_dump(exclude_none=True)
        update_dict['updated_at'] = datetime.now(timezone.utc)
        
        # Update in database
        await db.plans.update_one(
            {"name": plan_name.lower()},
            {"$set": update_dict}
        )
        
        # Fetch and return updated plan
        updated_plan = await db.plans.find_one({"name": plan_name.lower()})
        updated_plan['id'] = str(updated_plan['_id'])

        logger.info(
            "plan_updated",
            extra={
                "event": "plan_updated",
                "plan_name": plan_name.lower(),
                "updated_fields": list(update_dict.keys()),
            },
        )
        
        return Plan(**updated_plan)

    @staticmethod
    async def delete_plan(plan_name: str) -> bool:
        """
        Delete a plan (Admin only)
        CAUTION: Should not delete plans that have active subscriptions
        
        Args:
            plan_name: Name of plan to delete
            
        Returns:
            True if deleted, False if not found
        """
        # Check if any active subscriptions use this plan
        active_count = await db.subscriptions.count_documents({
            "plan": plan_name.lower(),
            "status": "active"
        })
        
        if active_count > 0:
            logger.warning(
                "plan_delete_blocked_active_subscriptions",
                extra={
                    "event": "plan_delete_blocked_active_subscriptions",
                    "plan_name": plan_name.lower(),
                    "active_subscriptions": active_count,
                },
            )
            raise ValueError(
                f"Cannot delete plan '{plan_name}'. "
                f"{active_count} active subscription(s) are using this plan. "
                f"Deactivate the plan instead or migrate users first."
            )
        
        result = await db.plans.delete_one({"name": plan_name.lower()})
        if result.deleted_count > 0:
            logger.info("plan_deleted", extra={"event": "plan_deleted", "plan_name": plan_name.lower()})
        return result.deleted_count > 0

    @staticmethod
    async def activate_plan(plan_name: str) -> Optional[Plan]:
        """
        Activate a plan
        
        Args:
            plan_name: Name of plan to activate
            
        Returns:
            Updated plan if found, None otherwise
        """
        result = await db.plans.update_one(
            {"name": plan_name.lower()},
            {
                "$set": {
                    "is_active": True,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        
        if result.modified_count > 0 or result.matched_count > 0:
            logger.info("plan_activated", extra={"event": "plan_activated", "plan_name": plan_name.lower()})
            return await PlanService.get_plan_by_name(plan_name)
        logger.warning("plan_activate_not_found", extra={"event": "plan_activate_not_found", "plan_name": plan_name.lower()})
        return None

    @staticmethod
    async def deactivate_plan(plan_name: str) -> Optional[Plan]:
        """
        Deactivate a plan (make it unavailable for new subscriptions)
        
        Args:
            plan_name: Name of plan to deactivate
            
        Returns:
            Updated plan if found, None otherwise
        """
        # Don't allow deactivating free plan
        if plan_name.lower() == 'free':
            logger.warning("plan_deactivate_free_blocked", extra={"event": "plan_deactivate_free_blocked", "plan_name": plan_name.lower()})
            raise ValueError("Cannot deactivate the 'free' plan")
        
        result = await db.plans.update_one(
            {"name": plan_name.lower()},
            {
                "$set": {
                    "is_active": False,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
        
        if result.modified_count > 0 or result.matched_count > 0:
            logger.info("plan_deactivated", extra={"event": "plan_deactivated", "plan_name": plan_name.lower()})
            return await PlanService.get_plan_by_name(plan_name)
        logger.warning("plan_deactivate_not_found", extra={"event": "plan_deactivate_not_found", "plan_name": plan_name.lower()})
        return None

    @staticmethod
    async def get_plan_price(plan_name: str, period: int) -> int:
        """
        Get the price for a specific plan and period
        
        Args:
            plan_name: Name of the plan
            period: Billing period in months
            
        Returns:
            Price in paise
            
        Raises:
            ValueError: If plan not found or period not available
        """
        plan = await PlanService.get_plan_by_name(plan_name)
        if not plan:
            logger.warning("plan_price_not_found", extra={"event": "plan_price_not_found", "plan_name": plan_name.lower(), "period": period})
            raise ValueError(f"Plan '{plan_name}' not found")
        
        # FIX: Normalize period to string for lookup since MongoDB stores keys as strings
        period_str = str(period)
        price = plan.periods.get(period_str) or plan.periods.get(period)
        
        if price is None:
            available = list(plan.periods.keys())
            logger.warning(
                "plan_price_period_invalid",
                extra={"event": "plan_price_period_invalid", "plan_name": plan_name.lower(), "period": period, "available_periods": available},
            )
            raise ValueError(
                f"Period {period} not available for plan '{plan_name}'. "
                f"Available periods: {available}"
            )
        
        return price

    @staticmethod
    async def get_available_periods(plan_name: str) -> List[int]:
        """
        Get available billing periods for a plan
        
        Args:
            plan_name: Name of the plan
            
        Returns:
            List of available periods (sorted as integers)
        """
        plan = await PlanService.get_plan_by_name(plan_name)
        if not plan:
            return []
        
        # Periods are stored as strings in DB, convert to int for comparison
        try:
            return sorted([int(k) for k in plan.periods.keys()])
        except (ValueError, TypeError):
            # If conversion fails, return empty list
            return []

    @staticmethod
    async def create_default_plans() -> int:
        """
        Create default plans if none exist
        Used during initial setup
        
        Returns:
            Number of plans created
        """
        existing_count = await db.plans.count_documents({})
        if existing_count > 0:
            return 0  # Plans already exist
        
        from app.config.default_plans import get_all_default_plans
        default_plans = get_all_default_plans()
        
        # Add timestamps which are usually not in the config dict
        now = datetime.now(timezone.utc)
        for plan in default_plans:
            plan["created_at"] = now
            plan["updated_at"] = now
        
        result = await db.plans.insert_many(default_plans)
        logger.info("plan_defaults_created", extra={"event": "plan_defaults_created", "created_count": len(result.inserted_ids)})
        return len(result.inserted_ids)

    @staticmethod
    async def get_plan_stats() -> Dict:
        """
        Get statistics about plans
        
        Returns:
            Dict with plan statistics
        """
        total = await db.plans.count_documents({})
        active = await db.plans.count_documents({"is_active": True})
        
        # Get subscription counts per plan
        pipeline = [
            {
                "$match": {"status": "active"}
            },
            {
                "$group": {
                    "_id": "$plan",
                    "count": {"$sum": 1}
                }
            }
        ]
        
        plan_usage = {}
        async for doc in db.subscriptions.aggregate(pipeline):
            plan_usage[doc['_id']] = doc['count']

        stats = {
            "total_plans": total,
            "active_plans": active,
            "inactive_plans": total - active,
            "usage_by_plan": plan_usage
        }
        logger.info(
            "plan_stats_computed",
            extra={"event": "plan_stats_computed", "total_plans": total, "active_plans": active, "used_plan_count": len(plan_usage)},
        )
        return stats
