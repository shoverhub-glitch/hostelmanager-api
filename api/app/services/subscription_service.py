from typing import Dict

from app.models.subscription_schema import Subscription, Usage
from app.database.mongodb import db, client, is_transaction_unsupported
from datetime import datetime, timedelta, timezone
from app.utils.ownership import build_owner_query
import logging

logger = logging.getLogger(__name__)

# Plans are now stored in the database 'plans' collection
# Use PlanService to manage plans (create, update, delete)
# This allows admin to dynamically manage plans without code changes

def format_price_text(price_paise: int) -> str:
    """Convert price in paise to formatted rupee text (e.g., 999 -> ₹9.99, 2499 -> ₹24.99)"""
    if price_paise == 0:
        return "₹0"
    rupees = price_paise / 100
    if rupees == int(rupees):
        return f"₹{int(rupees)}"
    return f"₹{rupees:.2f}".rstrip('0').rstrip('.')

class SubscriptionService:
    @staticmethod
    async def get_subscription(owner_id: str):
        """Get active subscription for owner, creating default free if none is active."""
        try:
            # FIX (High #8): Use MongoDB aggregation for efficient sorting and limit(1)
            # This is much faster than fetching all into memory and sorting in Python.
            pipeline = [
                {"$match": {"ownerId": owner_id, "status": "active"}},
                {"$addFields": {
                    "planOrder": {
                        "$switch": {
                            "branches": [
                                {"case": {"$eq": ["$plan", "premium"]}, "then": 2},
                                {"case": {"$eq": ["$plan", "pro"]}, "then": 1},
                            ],
                            "default": 0
                        }
                    }
                }},
                {"$sort": {"planOrder": -1}},
                {"$limit": 1}
            ]
            
            cursor = db["subscriptions"].aggregate(pipeline)
            docs = await cursor.to_list(length=1)
            
            if docs:
                return Subscription(**docs[0])
        except Exception as e:
            logger.exception("subscription_get_failed", extra={"event": "subscription_get_failed", "owner_id": owner_id, "error": str(e)})
            # If it's a transient DB error, we should probably raise so the request fails 
            # rather than returning a default free plan which might be a downgrade.
            raise

        # If truly not found, create default free subscription
        now = datetime.now(timezone.utc).isoformat()
        
        # Fetch free plan from database
        free_plan = await db.plans.find_one({"name": "free"})
        if not free_plan:
            raise ValueError("Free plan not found in database")
        free_limits = {
            'properties': free_plan['properties'],
            'tenants': free_plan['tenants'],
            'rooms': free_plan['rooms'],
            'staff': free_plan['staff']
        }
        
        sub = Subscription(
            ownerId=owner_id,
            plan='free',
            period=0,  # Free plan has no period
            status='active',
            price=0,
            currentPeriodStart=now,
            currentPeriodEnd=(datetime.now(timezone.utc) + timedelta(days=365)).isoformat(),  # 1 year for free
            propertyLimit=free_limits['properties'],
            roomLimit=free_limits['rooms'],
            tenantLimit=free_limits['tenants'],
            staffLimit=free_limits['staff'],
            createdAt=now,
            updatedAt=now,
            autoRenewal=False
        )
        try:
            # Use upsert to prevent duplicates
            await db["subscriptions"].update_one(
                {"ownerId": owner_id, "plan": "free"},
                {"$set": sub.model_dump()},
                upsert=True
            )
        except Exception as e:
            logger.exception("subscription_default_create_failed", extra={"event": "subscription_default_create_failed", "owner_id": owner_id, "error": str(e)})
            # FIX (Critical #1): Re-raise so we don't return a stale/unsaved sub object
            raise
        return sub

    @staticmethod
    async def update_subscription(owner_id: str, plan: str, period: int = 1):
        """
        Update subscription plan with dynamic period support.
        Ensures only one subscription is active for the user.
        Uses MongoDB transaction to ensure atomicity.
        
        Args:
            owner_id: User ID
            plan: Plan name (e.g., 'free', 'pro', 'premium')
            period: Billing period in months (1, 3, 12, etc.)
        """
        try:
            now = datetime.now(timezone.utc).isoformat()
            plan = plan.lower()
            
            # Fetch plan details from database
            plan_doc = await db.plans.find_one({"name": plan})
            if not plan_doc:
                raise ValueError(f"Plan '{plan}' not found in database")
            
            plan_data = {
                'properties': plan_doc['properties'],
                'tenants': plan_doc['tenants'],
                'rooms': plan_doc['rooms'],
                'staff': plan_doc['staff'],
                'periods': plan_doc.get('periods', {})
            }
            
            # For free plan, period is always 0
            if plan == 'free':
                period = 0
            
            # Validate period for non-free plans
            periods_dict = plan_data.get('periods', {})
            period_str = str(period)
            if plan != 'free' and period_str not in periods_dict:
                raise ValueError(f"Period {period} not available for {plan} plan")
            
            price = periods_dict.get(period_str, 0) if plan != 'free' else 0
            period_end = datetime.now(timezone.utc) + timedelta(days=period * 30 if period > 0 else 365)
            
            sub_data = {
                "plan": plan,
                "status": "active",
                "period": period,
                "price": price,
                "propertyLimit": plan_data['properties'],
                "roomLimit": plan_data['rooms'],
                "tenantLimit": plan_data['tenants'],
                "staffLimit": plan_data['staff'],
                "currentPeriodStart": now,
                "currentPeriodEnd": period_end.isoformat(),
                "autoRenewal": True if plan != 'free' else False,
                "updatedAt": now
            }
            
            # Use transaction to ensure atomicity: mark old inactive, activate new
            async def _perform_update(session=None):
                # 1. Mark all other subscriptions for this owner as inactive
                await db["subscriptions"].update_many(
                    {"ownerId": owner_id, "plan": {"$ne": plan}},
                    {"$set": {"status": "inactive", "updatedAt": now, "autoRenewal": False}},
                    session=session
                )
                
                # 2. Upsert the target subscription to active
                result = await db["subscriptions"].find_one_and_update(
                    {"ownerId": owner_id, "plan": plan},
                    {"$set": sub_data},
                    upsert=True,
                    return_document=True,
                    session=session
                )
                
                if result:
                    # Ensure createdAt exists (if it was an upsert-insert)
                    if "createdAt" not in result:
                        await db["subscriptions"].update_one(
                            {"_id": result["_id"]},
                            {"$set": {"createdAt": now}},
                            session=session
                        )
                        result["createdAt"] = now
                    return result
                return None

            try:
                async with await client.start_session() as session:
                    async with session.start_transaction():
                        result_doc = await _perform_update(session=session)
            except Exception as exc:
                if not is_transaction_unsupported(exc):
                    raise
                
                logger.warning(
                    "subscription_update_fallback_without_transaction",
                    extra={
                        "event": "subscription_update_fallback_without_transaction",
                        "reason": str(exc),
                    },
                )
                result_doc = await _perform_update()
            
            if result_doc:
                return Subscription(**result_doc)
            
            raise ValueError("Failed to update or create subscription")
            
        except Exception as e:
            logger.exception("subscription_update_failed", extra={"event": "subscription_update_failed", "owner_id": owner_id, "plan": plan, "period": period, "error": str(e)})
            raise ValueError(f"Failed to update subscription: {str(e)}")

    @staticmethod
    async def get_usage(owner_id: str):
        """Get current resource usage for subscription quota checking"""
        try:
            # Count active properties (exclude deleted and archived)
            owned_properties = await db["properties"].find(
                {**build_owner_query(owner_id), "isDeleted": {"$ne": True}, "active": True},
                {"_id": 1}
            ).to_list(length=None)
            property_ids = [str(doc["_id"]) for doc in owned_properties]

            properties = len(property_ids)
            # Count only active (non-archived, non-deleted) tenants
            tenants = await db["tenants"].count_documents(
                {"propertyId": {"$in": property_ids}, "isDeleted": {"$ne": True}, "archived": {"$ne": True}}
            ) if property_ids else 0
            rooms = await db["rooms"].count_documents(
                {"propertyId": {"$in": property_ids}, "isDeleted": {"$ne": True}, "active": {"$ne": False}}
            ) if property_ids else 0
            staff = await db["staff"].count_documents(
                {"propertyId": {"$in": property_ids}, "isDeleted": {"$ne": True}}
            ) if property_ids else 0
            now = datetime.now(timezone.utc).isoformat()
            return Usage(
                ownerId=owner_id,
                properties=properties,
                tenants=tenants,
                rooms=rooms,
                staff=staff,
                updatedAt=now
            )
        except Exception as e:
            logger.exception("subscription_usage_get_failed", extra={"event": "subscription_usage_get_failed", "owner_id": owner_id, "error": str(e)})
            # Return zero usage on error so user can still access the system
            now = datetime.now(timezone.utc).isoformat()
            return Usage(
                ownerId=owner_id,
                properties=0,
                tenants=0,
                rooms=0,
                staff=0,
                updatedAt=now
            )

    @staticmethod
    async def get_plan_limits(plan: str):
        """Get features/limits for a plan from database"""
        plan_doc = await db.plans.find_one({"name": plan.lower()})
        if not plan_doc:
            return None
        return {
            'properties': plan_doc['properties'],
            'tenants': plan_doc['tenants'],
            'rooms': plan_doc['rooms'],
            'staff': plan_doc['staff'],
        }

    @staticmethod
    async def get_all_plans():
        """Get all available plans with their pricing tiers from database"""
        cursor = db.plans.find({"is_active": True}).sort("sort_order", 1)
        result = []
        
        async for plan_doc in cursor:
            plan_info = {
                'name': plan_doc['name'],
                'display_name': plan_doc.get('display_name', plan_doc['name'].title() + ' Plan'),
                'properties': plan_doc['properties'],
                'tenants': plan_doc['tenants'],
                'rooms': plan_doc['rooms'],
                'staff': plan_doc['staff'],
                'periods': []
            }
            
            periods_dict = plan_doc.get('periods', {})
            # Convert string keys to int for proper sorting
            sorted_periods = sorted([(int(k), v) for k, v in periods_dict.items()])
            
            for period, price in sorted_periods:
                plan_info['periods'].append({
                    'period': period,
                    'price': price,
                    'priceText': format_price_text(price),
                    'pricePerMonth': price // period if period > 0 else 0
                })
            
            result.append(plan_info)
        
        return result

    @staticmethod
    async def check_downgrade_eligibility(owner_id: str) -> dict:
        """Check if user can downgrade to free tier"""
        try:
            # Count current resources
            owned_properties = await db["properties"].find(
                {**build_owner_query(owner_id), "active": True, "isDeleted": {"$ne": True}},
                {"_id": 1}
            ).to_list(length=None)
            property_ids = [str(doc["_id"]) for doc in owned_properties]

            property_count = len(property_ids)
            tenant_count = await db["tenants"].count_documents(
                {"propertyId": {"$in": property_ids}, "isDeleted": {"$ne": True}, "archived": {"$ne": True}}
            ) if property_ids else 0
        except Exception as e:
            logger.exception("subscription_downgrade_eligibility_failed", extra={"event": "subscription_downgrade_eligibility_failed", "owner_id": owner_id, "error": str(e)})
            return {
                "can_downgrade": False,
                "current": {"properties": 0, "tenants": 0},
                "limits": {"properties": 0, "tenants": 0},
                "excess": {"properties": 0, "tenants": 0},
                "message": "Unable to check eligibility. Please try again later."
            }
        
        # Free tier limits from database
        free_plan = await db.plans.find_one({"name": "free"})
        if not free_plan:
            return {
                "can_downgrade": False,
                "current": {
                    "properties": property_count,
                    "tenants": tenant_count,
                },
                "limits": {"properties": 0, "tenants": 0},
                "excess": {"properties": 0, "tenants": 0},
                "message": "Free plan is not configured in database. Please contact admin."
            }

        free_limits = {'properties': free_plan['properties'], 'tenants': free_plan['tenants']}
        
        # Calculate excess
        excess_properties = max(0, property_count - free_limits["properties"])
        excess_tenants = max(0, tenant_count - free_limits["tenants"])
        
        can_downgrade = excess_properties == 0 and excess_tenants == 0
        
        return {
            "can_downgrade": can_downgrade,
            "current": {
                "properties": property_count,
                "tenants": tenant_count,
            },
            "limits": {"properties": free_limits['properties'], "tenants": free_limits['tenants']},
            "excess": {
                "properties": excess_properties,
                "tenants": excess_tenants,
            },
            "message": (
                f"To downgrade to free plan, delete {excess_properties} properties "
                f"and {excess_tenants} tenants"
                if not can_downgrade
                else "You can proceed with downgrade"
            )
        }

    @staticmethod
    async def create_default_subscriptions(owner_id: str) -> dict:
        """
        Create default free subscription for a new user.
        Only creates a single subscription document that will be updated when plan changes.
        
        Returns:
            dict with created subscription details
        """
        try:
            now = datetime.now(timezone.utc).isoformat()
            
            # Fetch free plan from database
            free_plan = await db.plans.find_one({"name": "free", "is_active": True})
            if not free_plan:
                return {
                    "success": False,
                    "user_id": owner_id,
                    "error": "Free plan not found in database",
                    "message": "Admin must initialize subscription plans first"
                }
            
            # Create single subscription document with free plan
            period_end = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
            
            sub_doc = {
                "ownerId": owner_id,
                "plan": "free",
                "period": 0,
                "status": "active",
                "price": 0,
                "currentPeriodStart": now,
                "currentPeriodEnd": period_end,
                "propertyLimit": free_plan['properties'],
                "roomLimit": free_plan['rooms'],
                "tenantLimit": free_plan['tenants'],
                "staffLimit": free_plan['staff'],
                "createdAt": now,
                "updatedAt": now,
                "autoRenewal": False
            }
            
            # Upsert to handle duplicates gracefully
            await db["subscriptions"].update_one(
                {"ownerId": owner_id},
                {"$set": sub_doc},
                upsert=True
            )
            
            logger.info("subscription_default_created", extra={"event": "subscription_default_created", "owner_id": owner_id, "plan": "free"})
            
            return {
                "success": True,
                "user_id": owner_id,
                "subscriptions_created": 1,
                "plan": "free",
                "message": "Free subscription created successfully"
            }
        except Exception as e:
            logger.exception("subscription_default_create_failed", extra={"event": "subscription_default_create_failed", "owner_id": owner_id, "error": str(e)})
            return {
                "success": False,
                "user_id": owner_id,
                "error": str(e),
                "message": "Failed to create default subscription"
            }

    @staticmethod
    async def enable_auto_renewal(owner_id: str) -> bool:
        """
        Enable auto-renewal for active subscription
        """
        try:
            result = await db["subscriptions"].update_one(
                {"ownerId": owner_id, "status": "active"},
                {"$set": {
                    "autoRenewal": True,
                    "updatedAt": datetime.now(timezone.utc).isoformat()
                }}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.exception("subscription_auto_renew_enable_failed", extra={"event": "subscription_auto_renew_enable_failed", "owner_id": owner_id, "error": str(e)})
            raise

    @staticmethod
    async def disable_auto_renewal(owner_id: str) -> bool:
        """
        Disable auto-renewal for active subscription
        """
        try:
            result = await db["subscriptions"].update_one(
                {"ownerId": owner_id, "status": "active"},
                {"$set": {
                    "autoRenewal": False,
                    "updatedAt": datetime.now(timezone.utc).isoformat()
                }}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.exception("subscription_auto_renew_disable_failed", extra={"event": "subscription_auto_renew_disable_failed", "owner_id": owner_id, "error": str(e)})
            raise

    @staticmethod
    async def cancel_subscription(owner_id: str) -> Dict:
        """
        Cancel active subscription and Razorpay recurring subscription if exists
        """
        try:
            from app.services.razorpay_subscription_service import RazorpaySubscriptionService
            
            # Get active subscription
            sub = await db["subscriptions"].find_one(
                {"ownerId": owner_id, "status": "active"}
            )
            
            if not sub:
                raise ValueError("No active subscription found")
            
            # Cancel Razorpay subscription if exists
            if sub.get('razorpaySubscriptionId'):
                try:
                    await RazorpaySubscriptionService.cancel_recurring_subscription(
                        sub['razorpaySubscriptionId']
                    )
                except Exception as e:
                    logger.warning("subscription_cancel_razorpay_cancel_failed", extra={"event": "subscription_cancel_razorpay_cancel_failed", "owner_id": owner_id, "error": str(e)})
                    # Continue anyway to mark subscription as cancelled
            
            # Downgrade to free plan
            now = datetime.now(timezone.utc).isoformat()
            period_end = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
            
            free_plan = await db.plans.find_one({"name": "free"})
            if not free_plan:
                raise ValueError("Free plan not found")
            
            # Update the single subscription document to free plan
            result = await db["subscriptions"].update_one(
                {"ownerId": owner_id},
                {"$set": {
                    "plan": "free",
                    "status": "active",
                    "period": 0,
                    "price": 0,
                    "propertyLimit": free_plan['properties'],
                    "roomLimit": free_plan['rooms'],
                    "tenantLimit": free_plan['tenants'],
                    "staffLimit": free_plan['staff'],
                    "currentPeriodStart": now,
                    "currentPeriodEnd": period_end,
                    "autoRenewal": False,
                    "razorpaySubscriptionId": None,
                    "renewalError": None,
                    "updatedAt": now
                }}
            )
            
            logger.info("subscription_cancelled_to_free", extra={"event": "subscription_cancelled_to_free", "owner_id": owner_id, "plan": "free"})
            
            return {
                "success": True,
                "message": "Subscription cancelled and downgraded to free plan",
                "plan": "free"
            }
            
        except Exception as e:
            logger.exception("subscription_cancel_failed", extra={"event": "subscription_cancel_failed", "owner_id": owner_id, "error": str(e)})
            raise
