"""
Razorpay Webhook Service
Handles asynchronous notifications from Razorpay to ensure data consistency
"""

import hmac
import hashlib
import json
import logging
from typing import Dict, Optional
from datetime import datetime, timezone, timedelta
from app.config.settings import RAZORPAY_WEBHOOK_SECRET
from app.services.razorpay_subscription_service import RazorpaySubscriptionService
from app.services.subscription_service import SubscriptionService
from app.services.subscription_lifecycle import SubscriptionLifecycle
from app.services.razorpay_service import RazorpayService
from app.services.coupon_service import CouponService
from app.database.mongodb import db

logger = logging.getLogger(__name__)

processed_events_collection = db["processed_webhook_events"]


class RazorpayWebhookService:
    @staticmethod
    def verify_signature(payload: bytes, signature: str) -> bool:
        """Verify that the webhook actually came from Razorpay"""
        if not RAZORPAY_WEBHOOK_SECRET:
            logger.error("razorpay_webhook_secret_missing", extra={"event": "razorpay_webhook_secret_missing"})
            raise ValueError("Razorpay webhook secret not configured. Cannot verify webhook authenticity.")
            
        expected_signature = hmac.new(
            RAZORPAY_WEBHOOK_SECRET.encode(),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(expected_signature, signature)

    @staticmethod
    async def process_webhook(event_data: Dict):
        """Process different Razorpay events with idempotency"""
        event = event_data.get("event")
        payload = event_data.get("payload", {})
        
        logger.info("razorpay_webhook_received", extra={"event": "razorpay_webhook_received", "webhook_event": event})
        
        # order.paid is the primary event for successful order payments
        # payment.captured is a fallback if order.paid is missed
        if event in ["order.paid", "payment.captured"]:
            entity = payload.get("order", {}).get("entity") if event == "order.paid" else payload.get("payment", {}).get("entity")
            if not entity:
                logger.warning("razorpay_webhook_missing_entity", extra={"event": "razorpay_webhook_missing_entity", "webhook_event": event})
                return {"status": "skipped", "message": "No entity found"}

            order_id = entity.get("id") if event == "order.paid" else entity.get("order_id")
            payment_id = payload.get("payment", {}).get("entity", {}).get("id") if event == "order.paid" else entity.get("id")
            notes = entity.get("notes", {})
            
            if not order_id:
                logger.warning("razorpay_webhook_missing_order_id", extra={"event": "razorpay_webhook_missing_order_id", "webhook_event": event})
                return {"status": "skipped", "message": "No order_id"}

            # Idempotency check: skip if already processed
            existing = await processed_events_collection.find_one({"orderId": order_id})
            if existing:
                logger.info("razorpay_webhook_duplicate", extra={"event": "razorpay_webhook_duplicate", "order_id": order_id, "webhook_event": event})
                return {"status": "skipped", "message": "Already processed"}

            logger.info(
                "razorpay_webhook_process_payment_event",
                extra={
                    "event": "razorpay_webhook_process_payment_event",
                    "order_id": order_id,
                    "payment_id": payment_id,
                    "webhook_event": event,
                },
            )

            # 1. Handle Auto-renewal orders
            if notes.get("renewal") == "true":
                logger.info("razorpay_webhook_auto_renewal_success", extra={"event": "razorpay_webhook_auto_renewal_success", "order_id": order_id})
                await RazorpaySubscriptionService.handle_subscription_payment_success(order_id, payment_id)
            
            # 2. Handle standard Subscription orders (for new subscriptions/upgrades)
            # This handles the case where the app closed before verify-payment was called
            else:
                owner_id = notes.get("owner_id")
                plan = notes.get("plan")
                period = int(notes.get("period", 1))
                
                if owner_id and plan:
                    logger.info(
                        "razorpay_webhook_subscription_fulfillment",
                        extra={"event": "razorpay_webhook_subscription_fulfillment", "order_id": order_id, "owner_id": owner_id, "plan": plan, "period": period},
                    )
                    await SubscriptionService.update_subscription(owner_id, plan, period)
                    coupon_code = notes.get("coupon_code", "").strip()
                    if coupon_code:
                        await CouponService.increment_usage(coupon_code)
                else:
                    logger.warning("razorpay_webhook_missing_notes", extra={"event": "razorpay_webhook_missing_notes", "order_id": order_id, "notes": notes})

            # Record processed event for idempotency
            await processed_events_collection.insert_one({
                "orderId": order_id,
                "event": event,
                "paymentId": payment_id,
                "processedAt": datetime.now(timezone.utc).isoformat(),
            })

        elif event == "payment.failed":
            payment_entity = payload.get("payment", {}).get("entity", {})
            order_id = payment_entity.get("order_id")
            error_msg = payment_entity.get("error_description", "Unknown error")
            
            logger.error(
                "razorpay_payment_failed",
                extra={"event": "razorpay_payment_failed", "order_id": order_id, "error": error_msg},
            )
            
            # Handle renewal failures specifically if it was a renewal order
            notes = payment_entity.get("notes", {})
            if notes.get("renewal") == "true":
                await RazorpaySubscriptionService.handle_subscription_payment_failed(order_id, error_msg)

        elif event == "subscription.charged":
            # Auto-renewal payment received for a TRUE Razorpay recurring subscription
            sub_entity = payload.get("subscription", {}).get("entity", {})
            pay_entity  = payload.get("payment", {}).get("entity", {})
            subscription_id = sub_entity.get("id")
            payment_id      = pay_entity.get("id")
            paid_count      = sub_entity.get("paid_count", 0)
            current_end     = sub_entity.get("current_end")   # Unix timestamp from Razorpay
            notes           = sub_entity.get("notes", {}) or {}

            logger.info(
                "razorpay_subscription_charged",
                extra={"event": "razorpay_subscription_charged", "subscription_id": subscription_id, "paid_count": paid_count},
            )

            if not subscription_id:
                return {"status": "skipped", "message": "No subscription_id"}

            # Idempotency: subscription_id + paid_count uniquely identifies a charge
            idem_key = f"{subscription_id}_charged_{paid_count}"
            if await processed_events_collection.find_one({"idempotencyKey": idem_key}):
                logger.info("razorpay_webhook_duplicate", extra={"event": "razorpay_webhook_duplicate", "idem_key": idem_key})
                return {"status": "skipped", "message": "Already processed"}

            # Find subscription in our DB
            sub_doc = await db["subscriptions"].find_one({"razorpaySubscriptionId": subscription_id})
            if not sub_doc:
                # Fallback: use owner_id from notes (set when creating the Razorpay subscription)
                owner_id_from_notes = notes.get("owner_id")
                if owner_id_from_notes:
                    sub_doc = await db["subscriptions"].find_one({"ownerId": owner_id_from_notes, "status": "active"})
                if not sub_doc:
                    logger.warning(
                        "razorpay_subscription_charged_not_found",
                        extra={"event": "razorpay_subscription_charged_not_found", "subscription_id": subscription_id},
                    )
                    return {"status": "skipped", "message": "Subscription not found"}

            period = int(sub_doc.get("period", 1))

            # Use Razorpay's current_end if available, otherwise estimate from period
            if current_end:
                new_period_end = datetime.fromtimestamp(current_end, tz=timezone.utc).isoformat()
            else:
                new_period_end = (datetime.now(timezone.utc) + timedelta(days=period * 30)).isoformat()

            now_str = datetime.now(timezone.utc).isoformat()
            await db["subscriptions"].update_one(
                {"_id": sub_doc["_id"]},
                {"$set": {
                    "status": "active",
                    "currentPeriodEnd": new_period_end,
                    "autoRenewal": True,
                    "cancelAtPeriodEnd": False,
                    "renewalError": None,
                    "updatedAt": now_str,
                }}
            )

            await processed_events_collection.insert_one({
                "idempotencyKey": idem_key,
                "event": event,
                "subscriptionId": subscription_id,
                "paymentId": payment_id,
                "paidCount": paid_count,
                "processedAt": now_str,
            })

        elif event == "subscription.cancelled":
            # User or admin cancelled the Razorpay subscription; downgrade to free
            sub_entity      = payload.get("subscription", {}).get("entity", {})
            subscription_id = sub_entity.get("id")
            notes           = sub_entity.get("notes", {}) or {}

            logger.info(
                "razorpay_subscription_cancelled",
                extra={"event": "razorpay_subscription_cancelled", "subscription_id": subscription_id},
            )

            if not subscription_id:
                return {"status": "skipped", "message": "No subscription_id"}

            # FIX: Write idempotency record BEFORE work to prevent race on crash (High #14)
            idem_key = f"{subscription_id}_cancelled"
            if await processed_events_collection.find_one({"idempotencyKey": idem_key}):
                return {"status": "skipped", "message": "Already processed"}
            
            await processed_events_collection.insert_one({
                "idempotencyKey": idem_key,
                "event": event,
                "subscriptionId": subscription_id,
                "processedAt": datetime.now(timezone.utc).isoformat(),
            })

            sub_doc = await db["subscriptions"].find_one({"razorpaySubscriptionId": subscription_id})
            if not sub_doc:
                owner_id_from_notes = notes.get("owner_id")
                if owner_id_from_notes:
                    sub_doc = await db["subscriptions"].find_one({"ownerId": owner_id_from_notes, "status": "active"})

            if sub_doc:
                owner_id = sub_doc.get("ownerId")
                old_plan = str(sub_doc.get("plan", "free")).lower()

                # FIX: Before downgrading, verify that this is still the CURRENT active subscription (Medium #24)
                # If user already upgraded to a new plan, don't downgrade based on an old cancellation webhook.
                current_active = await db["subscriptions"].find_one({"ownerId": owner_id, "status": "active", "plan": {"$ne": "free"}})
                if current_active and str(current_active.get("razorpaySubscriptionId")) != subscription_id:
                    logger.info(
                        "razorpay_webhook_downgrade_skipped_new_sub_exists",
                        extra={"event": "razorpay_webhook_downgrade_skipped_new_sub_exists", "owner_id": owner_id, "cancelled_sub": subscription_id, "active_sub": current_active.get("razorpaySubscriptionId")}
                    )
                    return {"status": "skipped", "message": "User has a newer active subscription"}

                if owner_id and old_plan != "free":
                    downgrade_result = await SubscriptionLifecycle.handle_downgrade(owner_id, old_plan, "free")
                    if not downgrade_result.get("success"):
                        logger.warning(
                            "razorpay_subscription_cancelled_downgrade_failed",
                            extra={"event": "razorpay_subscription_cancelled_downgrade_failed", "subscription_id": subscription_id, "owner_id": owner_id, "old_plan": old_plan},
                        )

                    await SubscriptionService.update_subscription(owner_id, "free", 0)
                    await db["subscriptions"].update_one(
                        {"ownerId": owner_id, "plan": "free"},
                        {"$set": {
                            "status": "active",
                            "autoRenewal": False,
                            "cancelAtPeriodEnd": False,
                            "razorpaySubscriptionId": None,
                            "updatedAt": datetime.now(timezone.utc).isoformat(),
                        }}
                    )

        elif event == "subscription.halted":
            # Razorpay gave up retrying payment; mark as past_due and clear auto-renewal
            sub_entity      = payload.get("subscription", {}).get("entity", {})
            subscription_id = sub_entity.get("id")

            logger.warning(
                "razorpay_subscription_halted",
                extra={"event": "razorpay_subscription_halted", "subscription_id": subscription_id},
            )

            if not subscription_id:
                return {"status": "skipped", "message": "No subscription_id"}

            idem_key = f"{subscription_id}_halted"
            if await processed_events_collection.find_one({"idempotencyKey": idem_key}):
                return {"status": "skipped", "message": "Already processed"}

            sub_doc = await db["subscriptions"].find_one({"razorpaySubscriptionId": subscription_id})
            if sub_doc:
                now_str = datetime.now(timezone.utc).isoformat()
                await db["subscriptions"].update_one(
                    {"_id": sub_doc["_id"]},
                    {"$set": {
                        "status": "past_due",
                        "autoRenewal": False,
                        "renewalError": "Payment failed after multiple retries. Please re-subscribe to continue your plan.",
                        "updatedAt": now_str,
                    }}
                )
                await processed_events_collection.insert_one({
                    "idempotencyKey": idem_key,
                    "event": event,
                    "subscriptionId": subscription_id,
                    "processedAt": now_str,
                })

        return {"status": "processed"}
