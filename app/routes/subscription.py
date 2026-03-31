from fastapi import APIRouter, Depends, HTTPException, Body, Request
from datetime import datetime, timezone
import hmac as hmac_lib
import hashlib
from app.utils.helpers import get_current_user, require_admin_user
from app.services.subscription_service import SubscriptionService
from app.services.plan_service import PlanService
from app.services.subscription_enforcement import SubscriptionEnforcement
from app.services.subscription_lifecycle import SubscriptionLifecycle
from app.services.razorpay_service import RazorpayService
from app.services.razorpay_subscription_service import RazorpaySubscriptionService
from app.services.coupon_service import CouponService
from app.services.razorpay_webhook_service import RazorpayWebhookService
from app.config.settings import RAZORPAY_KEY_SECRET
import logging

router = APIRouter(prefix="/subscription", tags=["subscription"])
logger = logging.getLogger(__name__)


@router.post("/webhook")
async def razorpay_webhook(request: Request):
    """
    Handle Razorpay webhooks for payment notifications.
    Critical for 100% reliability in case of network/app failure.
    """
    try:
        signature = request.headers.get("X-Razorpay-Signature")
        if not signature:
            raise HTTPException(status_code=400, detail="Missing signature")
            
        body = await request.body()
        
        # Verify webhook signature
        if not RazorpayWebhookService.verify_signature(body, signature):
            raise HTTPException(status_code=400, detail="Invalid signature")
            
        event_data = await request.json()
        result = await RazorpayWebhookService.process_webhook(event_data)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "subscription_webhook_error",
            extra={
                "event": "subscription_webhook_error",
                "path": request.url.path,
                "client_ip": request.client.host if request.client else "unknown",
            },
        )
        # Always return 200 to Razorpay to prevent retries of invalid/failing events
        return {"status": "error", "message": str(e)}


@router.get("")
async def get_subscription(user_id: str = Depends(get_current_user)):
    try:
        sub = await SubscriptionService.get_subscription(user_id)
        logger.info("subscription_get_success", extra={"event": "subscription_get_success", "user_id": user_id, "plan": sub.plan})
        return {"data": sub.model_dump()}
    except Exception as e:
        logger.exception("subscription_get_failed", extra={"event": "subscription_get_failed", "user_id": user_id, "error": str(e)})
        raise HTTPException(
            status_code=500,
            detail="Error retrieving subscription. Please try again."
        )


@router.get("/plans")
async def get_all_plans():
    """Get all available subscription plans with their pricing tiers"""
    try:
        plans = await SubscriptionService.get_all_plans()
        logger.info("subscription_plans_list_success", extra={"event": "subscription_plans_list_success", "plans_count": len(plans) if plans else 0})
        return {"data": plans}
    except Exception as e:
        logger.exception("subscription_plans_list_failed", extra={"event": "subscription_plans_list_failed", "error": str(e)})
        raise HTTPException(
            status_code=500,
            detail="Error retrieving subscription plans. Please try again."
        )


@router.get("/usage")
async def get_usage(user_id: str = Depends(get_current_user)):
    try:
        usage = await SubscriptionService.get_usage(user_id)
        logger.info("subscription_usage_get_success", extra={"event": "subscription_usage_get_success", "user_id": user_id})
        return {"data": usage.model_dump()}
    except Exception as e:
        logger.exception("subscription_usage_get_failed", extra={"event": "subscription_usage_get_failed", "user_id": user_id, "error": str(e)})
        raise HTTPException(
            status_code=500,
            detail="Error retrieving usage data. Please try again."
        )


@router.get("/quota-warnings")
async def get_quota_warnings(user_id: str = Depends(get_current_user)):
    """Get quota usage warnings if approaching limits (80%+)"""
    try:
        warnings = await SubscriptionEnforcement.get_usage_warning(user_id)
        logger.info("subscription_quota_warnings_get_success", extra={"event": "subscription_quota_warnings_get_success", "user_id": user_id, "has_warnings": bool(warnings)})
        if warnings:
            return {"data": warnings}
        return {"data": None}
    except Exception as e:
        logger.exception("subscription_quota_warnings_get_failed", extra={"event": "subscription_quota_warnings_get_failed", "user_id": user_id, "error": str(e)})
        raise HTTPException(
            status_code=500,
            detail="Error checking quota warnings. Please try again."
        )


@router.get("/limits/{plan}")
async def get_limits(plan: str):
    try:
        limits = await SubscriptionService.get_plan_limits(plan)
        if not limits:
            logger.warning("subscription_limits_plan_not_found", extra={"event": "subscription_limits_plan_not_found", "plan": plan})
            raise HTTPException(status_code=404, detail="Plan not found")
        logger.info("subscription_limits_get_success", extra={"event": "subscription_limits_get_success", "plan": plan})
        return {"data": limits}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("subscription_limits_get_failed", extra={"event": "subscription_limits_get_failed", "plan": plan, "error": str(e)})
        raise HTTPException(status_code=500, detail="Error retrieving plan limits.")


@router.post("/upgrade")
async def upgrade_subscription(
    payload: dict = Body(...),
    user_id: str = Depends(get_current_user)
):
    """
    Upgrade or change subscription plan.
    - For paid plan upgrades: Updates subscription and restores archived resources if upgrading from free
    - For paid plan downgrades (lateral): Updates subscription but resources remain active (use /cancel for archival)
    - For free plan selection: Use /cancel endpoint instead (triggers archival lifecycle)
    """
    try:
        plan = payload.get("plan")
        period = payload.get("period", 1)
        
        if not plan:
            logger.warning("subscription_upgrade_missing_plan", extra={"event": "subscription_upgrade_missing_plan", "user_id": user_id})
            raise HTTPException(status_code=400, detail="Plan is required")
        
        # Validate period for the plan
        available_periods = await PlanService.get_available_periods(plan)
        if period not in available_periods:
            logger.warning("subscription_upgrade_invalid_period", extra={"event": "subscription_upgrade_invalid_period", "user_id": user_id, "plan": plan, "period": period, "available_periods": available_periods})
            raise HTTPException(status_code=400, detail=f"Period {period} not available for {plan} plan. Available: {available_periods}")
        
        # Get current subscription to track change
        current_sub = await SubscriptionService.get_subscription(user_id)
        old_plan = current_sub.plan
        
        # Update subscription
        sub = await SubscriptionService.update_subscription(user_id, plan, period)
        
        # If upgrading from free to paid, restore archived resources
        if old_plan == 'free' and plan != 'free':
            restore_result = await SubscriptionLifecycle.handle_upgrade(user_id, plan)
            if restore_result.get("success"):
                logger.info("subscription_upgrade_success_with_restore", extra={"event": "subscription_upgrade_success_with_restore", "user_id": user_id, "old_plan": old_plan, "new_plan": plan, "period": period})
                sub_dict = sub.model_dump()
                sub_dict["archived_resources_restored"] = restore_result
                return {"data": sub_dict}
        logger.info("subscription_upgrade_success", extra={"event": "subscription_upgrade_success", "user_id": user_id, "old_plan": old_plan, "new_plan": plan, "period": period})
        return {"data": sub.model_dump()}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("subscription_upgrade_failed", extra={"event": "subscription_upgrade_failed", "user_id": user_id, "error": str(e)})
        raise HTTPException(
            status_code=500,
            detail="Error updating subscription. Please try again."
        )

@router.post("/create-checkout-session")
async def create_checkout_session(
    payload: dict = Body(...),
    user_id: str = Depends(get_current_user)
):
    try:
        plan = payload.get("plan")
        period = payload.get("period", 1)
        coupon_code = payload.get("coupon_code", "").strip()
        
        if not plan:
            raise HTTPException(status_code=400, detail="Plan is required")
        if plan == 'free':
            raise HTTPException(status_code=400, detail="Free plan does not require payment.")
        
        # Validate and get price for this plan and period
        available_periods = await PlanService.get_available_periods(plan)
        if period not in available_periods:
            raise HTTPException(status_code=400, detail=f"Period {period} not available for {plan} plan")
        
        price = await PlanService.get_plan_price(plan, period)
        if price <= 0:
            raise HTTPException(status_code=400, detail="Invalid price for this plan and period")
        
        amount = price  # Already in paise
        discount_amount = 0
        final_amount = amount
        
        # Apply coupon if provided
        if coupon_code:
            coupon_response = await CouponService.apply_coupon(coupon_code, amount, plan)
            if not coupon_response.isValid:
                raise HTTPException(status_code=400, detail=f"Invalid coupon: {coupon_response.message}")
            
            final_amount = coupon_response.finalAmount
            discount_amount = coupon_response.discountAmount
        
        currency = 'INR'
        
        # Ensure receipt is <= 40 chars for Razorpay
        base_receipt = f"sub_{plan}_{period}m"
        user_part = user_id[:40 - len(base_receipt) - 1]  # leave room for underscore
        receipt = f"{base_receipt}_{user_part}"
        
        order_doc = await RazorpayService.create_order(
            user_id, plan, period, final_amount, currency, receipt, 
            coupon_code=coupon_code if coupon_code else None
        )
        logger.info(
            "subscription_checkout_session_created",
            extra={
                "event": "subscription_checkout_session_created",
                "user_id": user_id,
                "plan": plan,
                "period": period,
                "order_id": order_doc.order_id,
                "amount": final_amount,
                "coupon_applied": bool(coupon_code),
            },
        )
        return {
            "data": {
                "razorpayOrderId": order_doc.order_id,
                "amount": final_amount,
                "originalAmount": amount,
                "discountAmount": discount_amount,
                "couponCode": coupon_code if coupon_code else None,
                "currency": order_doc.currency,
                "keyId": RazorpayService.client.auth[0]
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "subscription_checkout_session_failed",
            extra={"event": "subscription_checkout_session_failed", "user_id": user_id, "error": str(e)},
        )
        raise HTTPException(
            status_code=500,
            detail="Error creating checkout session. Please try again."
        )


# Razorpay: Verify Payment
@router.post("/verify-payment")
async def verify_payment(payload: dict, user_id: str = Depends(get_current_user)):
    try:
        payment_id = payload.get("payment_id")
        order_id = payload.get("order_id")
        signature = payload.get("signature")
        if not (payment_id and order_id and signature):
            raise HTTPException(status_code=400, detail="Missing payment verification fields")

        success, plan_data, coupon_code = await RazorpayService.verify_payment(order_id, payment_id, signature)
        if not success:
            logger.warning(
                "subscription_verify_payment_failed",
                extra={"event": "subscription_verify_payment_failed", "user_id": user_id, "order_id": order_id, "payment_id": payment_id, "error": plan_data},
            )
            return {"data": {"success": False, "error": plan_data}}

        # plan_data now contains {"plan": "pro", "period": 3}
        plan = plan_data["plan"]
        period = plan_data.get("period", 1)
        
        await SubscriptionService.update_subscription(user_id, plan, period)

        # Apply coupon usage if coupon was used
        if coupon_code:
            # FIX: Use atomic apply and increment to prevent over-usage race conditions
            from app.services.plan_service import PlanService
            try:
                plan_price = await PlanService.get_plan_price(plan, period)
                await CouponService.apply_and_increment_usage(coupon_code, plan_price, plan)
            except Exception as e:
                # If lookup fails, fallback to simple increment (better than nothing)
                logger.warning("subscription_verify_coupon_atomic_failed", extra={"event": "subscription_verify_coupon_atomic_failed", "error": str(e)})
                await CouponService.increment_usage(coupon_code)
        logger.info(
            "subscription_verify_payment_success",
            extra={
                "event": "subscription_verify_payment_success",
                "user_id": user_id,
                "order_id": order_id,
                "payment_id": payment_id,
                "plan": plan,
                "period": period,
                "coupon_applied": bool(coupon_code),
            },
        )
        
        return {
            "data": {
                "success": True, 
                "subscription": plan, 
                "period": period,
                "couponApplied": coupon_code is not None,
                "couponCode": coupon_code if coupon_code else None
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "subscription_verify_payment_exception",
            extra={"event": "subscription_verify_payment_exception", "user_id": user_id, "error": str(e)},
        )
        raise HTTPException(
            status_code=500,
            detail="Error verifying payment. Please try again."
        )


@router.get("/downgrade-check")
async def downgrade_check(user_id: str = Depends(get_current_user)):
    """Check if user can downgrade to free tier"""
    try:
        eligibility = await SubscriptionService.check_downgrade_eligibility(user_id)
        logger.info("subscription_downgrade_check_success", extra={"event": "subscription_downgrade_check_success", "user_id": user_id, "can_downgrade": bool(eligibility.get("can_downgrade")) if isinstance(eligibility, dict) else None})
        return {"data": eligibility}
    except Exception as e:
        logger.exception("subscription_downgrade_check_failed", extra={"event": "subscription_downgrade_check_failed", "user_id": user_id, "error": str(e)})
        raise HTTPException(
            status_code=500,
            detail="Error checking downgrade eligibility. Please try again."
        )


@router.post("/cancel")
async def cancel_subscription(user_id: str = Depends(get_current_user)):
    """
    Cancel subscription.
    - Razorpay recurring subscribers: cancels at end of current billing cycle.
      Access continues until period end, then downgrades to free automatically.
    - One-time payment subscribers: immediate downgrade to free with resource archival.
    """
    try:
        from app.database.mongodb import db as _db

        current_sub = await SubscriptionService.get_subscription(user_id)
        old_plan = current_sub.plan

        if old_plan == 'free':
            raise HTTPException(status_code=400, detail="Already on the free plan.")

        now = datetime.now(timezone.utc).isoformat()

        # ── Case 1: Razorpay recurring subscription → cancel at period end ──
        if current_sub.razorpaySubscriptionId:
            try:
                await RazorpaySubscriptionService.cancel_recurring_subscription(
                    current_sub.razorpaySubscriptionId, cancel_at_cycle_end=True
                )
            except Exception as e:
                # Log but don't block — still mark as cancelling in our DB
                logger.warning(
                    "subscription_cancel_razorpay_api_failed",
                    extra={"event": "subscription_cancel_razorpay_api_failed", "user_id": user_id, "error": str(e)}
                )

            await _db["subscriptions"].update_one(
                {"ownerId": user_id, "status": "active"},
                {"$set": {"cancelAtPeriodEnd": True, "autoRenewal": False, "updatedAt": now}}
            )

            sub_data = current_sub.model_dump()
            sub_data["cancelAtPeriodEnd"] = True
            sub_data["autoRenewal"] = False
            logger.info(
                "subscription_cancel_at_period_end",
                extra={"event": "subscription_cancel_at_period_end", "user_id": user_id, "period_end": current_sub.currentPeriodEnd[:10]}
            )
            return {
                "data": sub_data,
                "message": f"Subscription will be cancelled on {current_sub.currentPeriodEnd[:10]}. You keep full access until then."
            }

        # ── Case 2: One-time payment → immediate downgrade with archival ──
        downgrade_result = await SubscriptionLifecycle.handle_downgrade(user_id, old_plan, "free")
        if not downgrade_result.get("success"):
            logger.warning("subscription_cancel_downgrade_failed", extra={"event": "subscription_cancel_downgrade_failed", "user_id": user_id})
            raise HTTPException(
                status_code=500,
                detail="Error processing subscription downgrade. Please try again."
            )

        await SubscriptionService.cancel_subscription(user_id)

        # Refresh subscription from DB after cancel
        updated_sub = await SubscriptionService.get_subscription(user_id)
        sub_dict = updated_sub.model_dump()
        sub_dict["downgrade_info"] = {
            "archived_properties": downgrade_result.get("archived_properties", []),
            "archived_rooms": downgrade_result.get("archived_rooms", []),
            "archived_tenants": downgrade_result.get("archived_tenants", []),
            "grace_period_until": downgrade_result.get("grace_period_until"),
            "message": downgrade_result.get("message")
        }
        logger.info("subscription_cancel_success", extra={"event": "subscription_cancel_success", "user_id": user_id, "old_plan": old_plan})
        return {"data": sub_dict}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("subscription_cancel_failed", extra={"event": "subscription_cancel_failed", "user_id": user_id, "error": str(e)})
        raise HTTPException(
            status_code=500,
            detail="Error canceling subscription. Please try again."
        )


@router.get("/archived-resources")
async def get_archived_resources(user_id: str = Depends(get_current_user)):
    """
    Get all archived resources from subscription downgrades.
    Shows what was archived and when it expires if not recovered.
    """
    try:
        archived = await SubscriptionLifecycle.get_archived_resources(user_id)
        logger.info("subscription_archived_resources_get_success", extra={"event": "subscription_archived_resources_get_success", "user_id": user_id})
        return {"data": archived}
    except Exception as e:
        logger.exception("subscription_archived_resources_get_failed", extra={"event": "subscription_archived_resources_get_failed", "user_id": user_id, "error": str(e)})
        raise HTTPException(
            status_code=500,
            detail="Error retrieving archived resources. Please try again."
        )


@router.post("/recover-archived-resources")
async def recover_archived_resources(user_id: str = Depends(get_current_user)):
    """
    Recover archived resources by upgrading subscription.
    User must be on a plan that supports the number of resources.
    """
    try:
        # Get current subscription
        sub = await SubscriptionService.get_subscription(user_id)
        
        # If already on a plan with enough capacity, restore resources
        if sub.plan != "free":
            restore_result = await SubscriptionLifecycle.handle_upgrade(user_id, sub.plan)
            if restore_result.get("success"):
                logger.info("subscription_archived_resources_recover_success", extra={"event": "subscription_archived_resources_recover_success", "user_id": user_id, "plan": sub.plan})
                return {
                    "data": {
                        "success": True,
                        "restored_resources": restore_result
                    }
                }
        
        # User must upgrade
        logger.warning("subscription_archived_resources_recover_requires_upgrade", extra={"event": "subscription_archived_resources_recover_requires_upgrade", "user_id": user_id, "plan": sub.plan})
        raise HTTPException(
            status_code=402,
            detail="You need to upgrade your subscription to recover archived resources."
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("subscription_archived_resources_recover_failed", extra={"event": "subscription_archived_resources_recover_failed", "user_id": user_id, "error": str(e)})
        raise HTTPException(
            status_code=500,
            detail="Error recovering archived resources. Please try again."
        )

@router.get("/all")
async def get_all_subscriptions(user_id: str = Depends(get_current_user)):
    """
    Get all subscription documents for the current user (typically a single active subscription).
    Shows current plan details including limits, pricing, and period information.
    """
    try:
        from app.database.mongodb import db
        
        subs = await db["subscriptions"].find(
            {"ownerId": user_id}
        ).to_list(length=None)
        
        if not subs:
            logger.warning("subscription_all_not_found", extra={"event": "subscription_all_not_found", "user_id": user_id})
            raise HTTPException(
                status_code=404,
                detail="No subscriptions found for user. Please contact support."
            )
        
        # Sort by plan order: premium, pro, free
        plan_order = {"premium": 2, "pro": 1, "free": 0}
        subs.sort(key=lambda x: plan_order.get(x.get("plan"), -1), reverse=True)

        # Normalize Mongo documents for JSON response
        serialized_subs = []
        for sub in subs:
            doc = dict(sub)
            mongo_id = doc.pop("_id", None)
            if mongo_id is not None:
                doc["id"] = str(mongo_id)

            if "ownerId" in doc and doc["ownerId"] is not None:
                doc["ownerId"] = str(doc["ownerId"])

            serialized_subs.append(doc)
        
        return {
            "data": {
                "user_id": user_id,
                "count": len(serialized_subs),
                "subscriptions": serialized_subs
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("subscription_all_get_failed", extra={"event": "subscription_all_get_failed", "user_id": user_id, "error": str(e)})
        raise HTTPException(
            status_code=500,
            detail="Error retrieving subscriptions. Please try again."
        )


@router.post("/initialize")
async def initialize_subscriptions(user_id: str = Depends(get_current_user)):
    """
    Initialize free subscription for user if not exists.
    Creates a single subscription document that will be updated when plan changes.
    """
    try:
        from app.database.mongodb import db
        
        # Check if user has a subscription
        existing_sub = await db["subscriptions"].find_one({"ownerId": user_id})
        
        if existing_sub:
            logger.info("subscription_initialize_already_exists", extra={"event": "subscription_initialize_already_exists", "user_id": user_id, "plan": existing_sub.get("plan", "free")})
            return {
                "data": {
                    "success": True,
                    "message": "User already has an active subscription",
                    "subscriptions_created": 0,
                    "plan": existing_sub.get("plan", "free")
                }
            }
        
        # Create default free subscription
        result = await SubscriptionService.create_default_subscriptions(user_id)
        
        if result["success"]:
            logger.info("subscription_initialize_success", extra={"event": "subscription_initialize_success", "user_id": user_id, "subscriptions_created": result.get("subscriptions_created", 0), "plan": result.get("plan", "free")})
            return {
                "data": {
                    "success": True,
                    "message": result["message"],
                    "subscriptions_created": result["subscriptions_created"],
                    "plan": result.get("plan", "free")
                }
            }
        else:
            logger.warning("subscription_initialize_create_failed", extra={"event": "subscription_initialize_create_failed", "user_id": user_id, "error": result.get("error")})
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Failed to create subscription")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("subscription_initialize_failed", extra={"event": "subscription_initialize_failed", "user_id": user_id, "error": str(e)})
        raise HTTPException(
            status_code=500,
            detail="Error initializing subscription. Please try again."
        )


@router.post("/create-subscription")
async def create_subscription(
    payload: dict = Body(...),
    user_id: str = Depends(get_current_user)
):
    """
    Create a Razorpay recurring subscription.
    Returns subscriptionId which the UI passes to the Razorpay checkout SDK.
    After the user completes payment, call /verify-subscription to activate.
    """
    try:
        from app.database.mongodb import db as _db

        plan = payload.get("plan")
        period = int(payload.get("period", 1))

        if not plan:
            raise HTTPException(status_code=400, detail="Plan is required")
        if plan == 'free':
            raise HTTPException(status_code=400, detail="Free plan does not require payment.")

        available_periods = await PlanService.get_available_periods(plan)
        if period not in available_periods:
            raise HTTPException(status_code=400, detail=f"Period {period} not available for {plan} plan")

        plan_doc = await _db.plans.find_one({"name": plan.lower()})
        if not plan_doc:
            raise HTTPException(status_code=404, detail="Plan not found")

        razorpay_plan_ids = plan_doc.get("razorpay_plan_ids") or {}
        razorpay_plan_id = razorpay_plan_ids.get(str(period))

        if not razorpay_plan_id:
            raise HTTPException(
                status_code=400,
                detail="Recurring subscription not configured for this plan. Run admin seed-razorpay-plans first, or use one-time checkout."
            )

        subscription = await RazorpaySubscriptionService.create_recurring_subscription(
            owner_id=user_id,
            plan_name=plan,
            period_months=period,
            razorpay_plan_id=razorpay_plan_id,
        )

        # Store or refresh pending record so verify-subscription can look up plan+period securely.
        pending_now = datetime.now(timezone.utc)
        await _db["pending_subscriptions"].update_one(
            {"owner_id": user_id, "razorpay_subscription_id": subscription["id"]},
            {
                "$set": {
                    "plan": plan,
                    "period": period,
                    "status": "pending",
                    "updated_at": pending_now.isoformat(),
                    "created_at_dt": pending_now,
                },
                "$setOnInsert": {
                    "created_at": pending_now.isoformat(),
                },
            },
            upsert=True,
        )

        logger.info(
            "subscription_create_subscription_success",
            extra={"event": "subscription_create_subscription_success", "user_id": user_id, "plan": plan, "period": period, "razorpay_subscription_id": subscription.get("id")}
        )
        return {
            "data": {
                "subscriptionId": subscription["id"],
                "keyId": RazorpayService.client.auth[0],
                "plan": plan,
                "period": period,
                "status": subscription.get("status"),
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("subscription_create_subscription_failed", extra={"event": "subscription_create_subscription_failed", "user_id": user_id, "error": str(e)})
        raise HTTPException(status_code=500, detail="Error creating subscription. Please try again.")


@router.post("/verify-subscription")
async def verify_subscription(
    payload: dict = Body(...),
    user_id: str = Depends(get_current_user)
):
    """
    Verify the initial payment of a Razorpay recurring subscription and activate it.
    Called immediately after the Razorpay checkout SDK returns success.

    Subsequent renewals are handled automatically by the webhook (subscription.charged).
    """
    try:
        from app.database.mongodb import db as _db

        if not RAZORPAY_KEY_SECRET:
            logger.error(
                "subscription_verify_subscription_secret_missing",
                extra={"event": "subscription_verify_subscription_secret_missing", "user_id": user_id}
            )
            raise HTTPException(status_code=500, detail="Payment verification is temporarily unavailable.")

        payment_id   = payload.get("payment_id")
        subscription_id = payload.get("subscription_id")
        signature    = payload.get("signature")

        if not all([payment_id, subscription_id, signature]):
            raise HTTPException(status_code=400, detail="Missing subscription verification fields")

        # Verify HMAC signature: payment_id + "|" + subscription_id
        generated_sig = hmac_lib.new(
            RAZORPAY_KEY_SECRET.encode(),
            f"{payment_id}|{subscription_id}".encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac_lib.compare_digest(generated_sig, signature):
            logger.warning(
                "subscription_verify_subscription_invalid_sig",
                extra={"event": "subscription_verify_subscription_invalid_sig", "user_id": user_id, "subscription_id": subscription_id}
            )
            return {"data": {"success": False, "error": "Invalid payment signature"}}

        # Look up pending subscription to get plan+period (avoids trusting client-supplied plan)
        pending = await _db["pending_subscriptions"].find_one(
            {"razorpay_subscription_id": subscription_id, "owner_id": user_id, "status": "pending"}
        )
        if not pending:
            # Fallback: maybe webhook already activated it
            existing = await _db["pending_subscriptions"].find_one(
                {"razorpay_subscription_id": subscription_id, "owner_id": user_id}
            )
            if existing and existing.get("status") == "completed":
                return {"data": {"success": True, "subscription": existing["plan"], "period": existing["period"]}}
            raise HTTPException(status_code=400, detail="Subscription session not found or already processed.")

        plan   = pending["plan"]
        period = pending["period"]

        # Activate subscription in our DB
        await SubscriptionService.update_subscription(user_id, plan, period)

        # Attach the razorpaySubscriptionId for future cancellation/auto-renewal
        now = datetime.now(timezone.utc).isoformat()
        await _db["subscriptions"].update_one(
            {"ownerId": user_id, "status": "active"},
            {"$set": {
                "razorpaySubscriptionId": subscription_id,
                "autoRenewal": True,
                "cancelAtPeriodEnd": False,
                "updatedAt": now,
            }}
        )

        # Mark pending record as completed
        await _db["pending_subscriptions"].update_one(
            {"_id": pending["_id"]},
            {"$set": {"status": "completed", "completed_at": now, "payment_id": payment_id}}
        )

        logger.info(
            "subscription_verify_subscription_success",
            extra={"event": "subscription_verify_subscription_success", "user_id": user_id, "subscription_id": subscription_id, "plan": plan, "period": period}
        )
        return {"data": {"success": True, "subscription": plan, "period": period}}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("subscription_verify_subscription_failed", extra={"event": "subscription_verify_subscription_failed", "user_id": user_id, "error": str(e)})
        raise HTTPException(status_code=500, detail="Error verifying subscription. Please try again.")


@router.post("/admin/seed-razorpay-plans")
async def seed_razorpay_plans(admin_user: dict = Depends(require_admin_user)):
    """
    Admin: Create Razorpay Plans for all active paid plan+period combinations.
    Must be called once (or after adding new plans/periods) before users can subscribe.
    Safe to call multiple times — skips already-seeded plans.
    """
    try:
        result = await RazorpaySubscriptionService.seed_razorpay_plans()
        logger.info(
            "razorpay_plans_seeded",
            extra={"event": "razorpay_plans_seeded", "created": len(result.get("created", [])), "skipped": len(result.get("skipped", [])), "errors": len(result.get("errors", []))}
        )
        return {"data": result}
    except Exception as e:
        logger.exception("razorpay_plans_seed_failed", extra={"event": "razorpay_plans_seed_failed", "error": str(e)})
        raise HTTPException(status_code=500, detail="Error seeding Razorpay plans. Please try again.")


@router.post("/auto-renewal/enable")
async def enable_auto_renewal(user_id: str = Depends(get_current_user)):
    """Enable automatic renewal for current subscription"""
    try:
        success = await SubscriptionService.enable_auto_renewal(user_id)
        if success:
            logger.info("subscription_auto_renewal_enable_success", extra={"event": "subscription_auto_renewal_enable_success", "user_id": user_id})
            return {
                "data": {
                    "success": True,
                    "message": "Auto-renewal enabled. Your subscription will renew automatically.",
                    "autoRenewal": True
                }
            }
        else:
            logger.warning("subscription_auto_renewal_enable_not_found", extra={"event": "subscription_auto_renewal_enable_not_found", "user_id": user_id})
            raise HTTPException(
                status_code=404,
                detail="No active subscription found"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("subscription_auto_renewal_enable_failed", extra={"event": "subscription_auto_renewal_enable_failed", "user_id": user_id, "error": str(e)})
        raise HTTPException(
            status_code=500,
            detail="Error enabling auto-renewal. Please try again."
        )


@router.post("/auto-renewal/disable")
async def disable_auto_renewal(user_id: str = Depends(get_current_user)):
    """Disable automatic renewal for current subscription"""
    try:
        success = await SubscriptionService.disable_auto_renewal(user_id)
        if success:
            logger.info("subscription_auto_renewal_disable_success", extra={"event": "subscription_auto_renewal_disable_success", "user_id": user_id})
            return {
                "data": {
                    "success": True,
                    "message": "Auto-renewal disabled. Your subscription will expire after current period.",
                    "autoRenewal": False
                }
            }
        else:
            logger.warning("subscription_auto_renewal_disable_not_found", extra={"event": "subscription_auto_renewal_disable_not_found", "user_id": user_id})
            raise HTTPException(
                status_code=404,
                detail="No active subscription found"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("subscription_auto_renewal_disable_failed", extra={"event": "subscription_auto_renewal_disable_failed", "user_id": user_id, "error": str(e)})
        raise HTTPException(
            status_code=500,
            detail="Error disabling auto-renewal. Please try again."
        )
