"""
Razorpay Subscription Service
Handles recurring/automatic billing for subscriptions
"""

import asyncio
import functools
import razorpay
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict
import logging

from app.config.settings import RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, APP_URL
from bson import ObjectId
from app.database.mongodb import db, client
from app.utils.email_service import send_renewal_reminder_email

logger = logging.getLogger(__name__)

# Initialize Razorpay client
razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


class RazorpaySubscriptionService:
    """Service for managing Razorpay subscriptions (recurring payments)"""

    @staticmethod
    async def create_recurring_subscription(
        owner_id: str,
        plan_name: str,
        period_months: int,
        razorpay_plan_id: str,
    ) -> Dict:
        """
        Create a Razorpay recurring subscription.

        Args:
            owner_id: User ID
            plan_name: Plan name (pro, premium)
            period_months: Billing period in months
            razorpay_plan_id: Pre-created Razorpay Plan ID (stored in plans collection)

        Returns:
            Razorpay subscription dict — use subscription["id"] as the subscription_id
        """
        try:
            # total_count: 0 = infinite (falls back to a large number for safety)
            # period_months 1→120 charges, 3→40, 6→20, 12→10 (each ~10 years)
            total_counts = {1: 120, 3: 40, 6: 20, 12: 10}
            total_count = total_counts.get(period_months, max(1, 120 // period_months))

            subscription_data = {
                'plan_id': razorpay_plan_id,
                'total_count': total_count,
                'quantity': 1,
                'customer_notify': 1,
                'notes': {
                    'owner_id': owner_id,
                    'plan': plan_name,
                    'period': str(period_months),
                }
            }

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.get_event_loop()

            subscription = await loop.run_in_executor(
                None, functools.partial(razorpay_client.subscription.create, subscription_data)
            )

            logger.info(
                "razorpay_subscription_created",
                extra={
                    "event": "razorpay_subscription_created",
                    "razorpay_subscription_id": subscription.get("id"),
                    "owner_id": owner_id,
                    "plan": plan_name,
                    "period_months": period_months,
                },
            )
            return subscription

        except Exception as e:
            logger.exception("razorpay_subscription_create_failed", extra={"event": "razorpay_subscription_create_failed", "owner_id": owner_id, "plan": plan_name, "period_months": period_months, "error": str(e)})
            raise

    @staticmethod
    async def cancel_recurring_subscription(razorpay_subscription_id: str, cancel_at_cycle_end: bool = True) -> Dict:
        """
        Cancel a Razorpay subscription.

        Args:
            razorpay_subscription_id: Razorpay subscription ID
            cancel_at_cycle_end: If True, cancel at end of current billing cycle (user keeps access).
                                 If False, cancel immediately.

        Returns:
            Dict with cancellation details
        """
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.get_event_loop()

            if cancel_at_cycle_end:
                subscription = await loop.run_in_executor(
                    None, functools.partial(
                        razorpay_client.subscription.cancel,
                        razorpay_subscription_id,
                        {"cancel_at_cycle_end": 1}
                    )
                )
            else:
                subscription = await loop.run_in_executor(
                    None, functools.partial(razorpay_client.subscription.cancel, razorpay_subscription_id)
                )
            logger.info(
                "razorpay_subscription_cancelled",
                extra={"event": "razorpay_subscription_cancelled", "razorpay_subscription_id": razorpay_subscription_id, "cancel_at_cycle_end": cancel_at_cycle_end}
            )
            return subscription

        except Exception as e:
            logger.exception("razorpay_subscription_cancel_failed", extra={"event": "razorpay_subscription_cancel_failed", "razorpay_subscription_id": razorpay_subscription_id, "error": str(e)})
            raise

    @staticmethod
    async def seed_razorpay_plans() -> Dict:
        """
        Create Razorpay Plans for all active paid plan+period combinations and
        store the resulting Plan IDs back into MongoDB.

        Safe to call multiple times — skips combinations that already have a plan ID.

        Returns:
            {created: [...], skipped: [...], errors: [...]}
        """
        created = []
        skipped = []
        errors = []

        plans = await db.plans.find({"is_active": True, "name": {"$ne": "free"}}).to_list(None)

        for plan_doc in plans:
            plan_name = plan_doc["name"]
            periods = plan_doc.get("periods", {})
            existing_ids: Dict = plan_doc.get("razorpay_plan_ids", {}) or {}

            for period_str, price in periods.items():
                period_months = int(period_str)

                if existing_ids.get(period_str):
                    skipped.append(f"{plan_name}_{period_months}m (already exists: {existing_ids[period_str]})")
                    continue

                try:
                    # Map period_months → Razorpay period/interval
                    if period_months <= 6:
                        rzp_period = "monthly"
                        rzp_interval = period_months
                    else:
                        rzp_period = "yearly"
                        rzp_interval = max(1, period_months // 12)

                    plan_data = {
                        "period": rzp_period,
                        "interval": rzp_interval,
                        "item": {
                            "name": f"{plan_name.title()} {period_months}M",
                            "amount": price,
                            "unit": "subscription",
                            "currency": "INR",
                        },
                        "notes": {
                            "plan": plan_name,
                            "period_months": str(period_months),
                        }
                    }

                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        loop = asyncio.get_event_loop()

                    rzp_plan = await loop.run_in_executor(
                        None, functools.partial(razorpay_client.plan.create, plan_data)
                    )

                    rzp_plan_id = rzp_plan["id"]

                    # Store the Razorpay Plan ID in MongoDB using dot-notation key
                    await db.plans.update_one(
                        {"name": plan_name},
                        {"$set": {f"razorpay_plan_ids.{period_str}": rzp_plan_id}}
                    )

                    created.append({
                        "plan": plan_name,
                        "period_months": period_months,
                        "razorpay_plan_id": rzp_plan_id,
                        "price_paise": price,
                    })
                    logger.info(
                        "razorpay_plan_seeded",
                        extra={"event": "razorpay_plan_seeded", "plan": plan_name, "period_months": period_months, "razorpay_plan_id": rzp_plan_id}
                    )

                except Exception as e:
                    error_msg = str(e)
                    errors.append({"plan": plan_name, "period_months": period_months, "error": error_msg})
                    logger.exception(
                        "razorpay_plan_seed_failed",
                        extra={"event": "razorpay_plan_seed_failed", "plan": plan_name, "period_months": period_months, "error": error_msg}
                    )

        return {"created": created, "skipped": skipped, "errors": errors}

    @staticmethod
    async def pause_recurring_subscription(razorpay_subscription_id: str, pause_months: int = 1) -> Dict:
        """
        Pause a Razorpay subscription temporarily
        
        Args:
            razorpay_subscription_id: Razorpay subscription ID
            pause_months: Number of months to pause
            
        Returns:
            Dict with pause details
        """
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.get_event_loop()

            subscription = await loop.run_in_executor(
                None, functools.partial(
                    razorpay_client.subscription.pause,
                    razorpay_subscription_id,
                    {'pause_at': 'now', 'resume_after': pause_months}
                )
            )
            logger.info("razorpay_subscription_paused", extra={"event": "razorpay_subscription_paused", "razorpay_subscription_id": razorpay_subscription_id, "pause_months": pause_months})
            return subscription
            
        except Exception as e:
            logger.exception("razorpay_subscription_pause_failed", extra={"event": "razorpay_subscription_pause_failed", "razorpay_subscription_id": razorpay_subscription_id, "error": str(e)})
            raise

    @staticmethod
    async def get_subscription_status(razorpay_subscription_id: str) -> Dict:
        """
        Get current status of a Razorpay subscription
        
        Args:
            razorpay_subscription_id: Razorpay subscription ID
            
        Returns:
            Dict with subscription status
        """
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.get_event_loop()

            subscription = await loop.run_in_executor(
                None, functools.partial(razorpay_client.subscription.fetch, razorpay_subscription_id)
            )
            return subscription
            
        except Exception as e:
            logger.exception("razorpay_subscription_status_fetch_failed", extra={"event": "razorpay_subscription_status_fetch_failed", "razorpay_subscription_id": razorpay_subscription_id, "error": str(e)})
            raise

    @staticmethod
    async def create_payment_link(owner_email: str, owner_name: str, order_id: str, amount: int, plan_name: str, expiry_date: str) -> Optional[str]:
        """
        Create a Razorpay payment link for subscription renewal
        
        Args:
            owner_email: Customer email
            owner_name: Customer name
            order_id: Razorpay order ID
            amount: Amount in paise
            plan_name: Plan name for description
            expiry_date: Subscription expiry date
            
        Returns:
            Payment link URL if created successfully, None otherwise
        """
        try:
            link_payload = {
                'amount': amount,
                'currency': 'INR',
                'description': f'{plan_name.title()} Plan Renewal - Expires {expiry_date}',
                'customer': {
                    'email': owner_email,
                    'name': owner_name
                },
                'notify': {
                    'sms': True,
                    'email': True
                },
                'reminder_enable': True,
                'notes': {
                    'order_id': order_id,
                    'plan': plan_name,
                    'renewal': 'true'
                },
                'callback_url': f'{APP_URL}/subscription/verify?order_id={order_id}',
                'callback_method': 'get'
            }
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.get_event_loop()

            payment_link = await loop.run_in_executor(
                None, functools.partial(razorpay_client.payment_link.create, link_payload)
            )
            
            logger.info("razorpay_payment_link_created", extra={"event": "razorpay_payment_link_created", "order_id": order_id, "plan": plan_name, "has_short_url": bool(payment_link.get("short_url"))})
            return payment_link.get('short_url') or payment_link.get('long_url')
            
        except Exception as e:
            logger.exception("razorpay_payment_link_create_failed", extra={"event": "razorpay_payment_link_create_failed", "order_id": order_id, "plan": plan_name, "error": str(e)})
            return None

    @staticmethod
    async def check_and_renew_subscriptions() -> Dict:
        """
        Check for subscriptions expiring within 7 days and attempt renewal
        This is called by a scheduled job
        
        Creates payment links and sends email notifications to users.
        
        Returns:
            Dict with renewal statistics
        """
        stats = {
            'checked': 0,
            'renewed': 0,
            'notified': 0,
            'failed': 0,
            'errors': []
        }
        
        try:
            # Find all active subscriptions expiring within 7 days
            now = datetime.now(timezone.utc)
            renewal_window_start = now.isoformat()
            renewal_window_end = (now + timedelta(days=7)).isoformat()
            
            expiring_subs = await db.subscriptions.find({
                'status': 'active',
                'autoRenewal': True,
                'plan': {'$ne': 'free'},  # Don't renew free plan
                'currentPeriodEnd': {
                    '$gte': renewal_window_start,
                    '$lte': renewal_window_end
                }
            }).to_list(None)
            
            stats['checked'] = len(expiring_subs)
            
            for sub in expiring_subs:
                try:
                    # FIX: Query user by ObjectId
                    owner_id = sub.get('ownerId')
                    if not owner_id:
                         continue

                    try:
                        user = await db.users.find_one({'_id': ObjectId(owner_id)})
                    except Exception:
                         user = await db.users.find_one({'_id': owner_id})

                    if not user:
                        stats['failed'] += 1
                        stats['errors'].append(f"User {owner_id} not found")
                        continue
                    
                    owner_email = user.get('email')
                    owner_name = user.get('name', user.get('email', 'Customer'))
                    
                    if not owner_email:
                        stats['failed'] += 1
                        stats['errors'].append(f"User {owner_id} missing email")
                        continue
                    
                    # Create renewal order via Razorpay API
                    plan = await db.plans.find_one({'name': sub['plan']})
                    if not plan:
                        stats['failed'] += 1
                        stats['errors'].append(f"Plan {sub['plan']} not found")
                        continue
                    
                    period_str = str(sub['period'])
                    price = plan['periods'].get(period_str, 0)
                    
                    if price == 0:
                        stats['failed'] += 1
                        stats['errors'].append(f"Invalid price for {sub['plan']} {period_str}m")
                        continue
                    
                    # Check for existing pending renewal order to avoid duplicates
                    existing_renewal = await db.renewal_orders.find_one({
                        'subscriptionId': sub['_id'],
                        'status': 'pending'
                    })
                    if existing_renewal:
                        logger.info("razorpay_renewal_skipped_pending_exists", extra={"event": "razorpay_renewal_skipped_pending_exists", "subscription_id": str(sub.get("_id"))})
                        continue
                    
                    # Create renewal order
                    renewal_order_data = {
                        'amount': price,
                        'currency': 'INR',
                        'receipt': f"renew_{str(owner_id)[:10]}_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                        'notes': {
                            'owner_id': str(owner_id),
                            'plan': sub['plan'],
                            'period': str(sub['period']),
                            'renewal': 'true',
                            'subscription_id': str(sub['_id'])
                        }
                    }
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        loop = asyncio.get_event_loop()

                    order = await loop.run_in_executor(
                        None, functools.partial(razorpay_client.order.create, renewal_order_data)
                    )
                    
                    # Create payment link
                    expiry_date = sub['currentPeriodEnd'][:10] if sub.get('currentPeriodEnd') else 'N/A'
                    payment_link_url = await RazorpaySubscriptionService.create_payment_link(
                        owner_email=owner_email,
                        owner_name=owner_name,
                        order_id=order['id'],
                        amount=price,
                        plan_name=sub['plan'],
                        expiry_date=expiry_date
                    )
                    
                    # Store renewal order for payment verification
                    await db.renewal_orders.insert_one({
                        'ownerId': str(owner_id),
                        'subscriptionId': sub['_id'],
                        'orderId': order['id'],
                        'plan': sub['plan'],
                        'period': sub['period'],
                        'amount': price,
                        'paymentLinkUrl': payment_link_url,
                        'createdAt': datetime.now(timezone.utc).isoformat(),
                        'status': 'pending',
                        'notifiedAt': datetime.now(timezone.utc).isoformat() if payment_link_url else None
                    })
                    
                    stats['renewed'] += 1
                    
                    # Send email notification with payment link
                    if payment_link_url:
                        amount_str = f"₹{price / 100:,.0f}"
                        email_sent = await send_renewal_reminder_email(
                            email=owner_email,
                            name=owner_name,
                            plan_name=sub['plan'],
                            amount_str=amount_str,
                            expiry_date=expiry_date,
                            payment_link=payment_link_url,
                            app_name="Hostel Manager"
                        )
                        
                        if email_sent:
                            stats['notified'] += 1
                            logger.info("razorpay_renewal_notification_sent", extra={"event": "razorpay_renewal_notification_sent", "order_id": order.get("id"), "owner_id": str(owner_id)})
                        else:
                            logger.warning("razorpay_renewal_notification_failed", extra={"event": "razorpay_renewal_notification_failed", "order_id": order.get("id"), "owner_id": str(owner_id)})
                    else:
                        logger.warning("razorpay_renewal_payment_link_missing", extra={"event": "razorpay_renewal_payment_link_missing", "order_id": order.get("id"), "owner_id": str(owner_id)})
                    
                    logger.info("razorpay_renewal_order_created", extra={"event": "razorpay_renewal_order_created", "order_id": order.get("id"), "owner_id": str(owner_id), "plan": sub.get("plan"), "period": sub.get("period")})
                    
                except Exception as e:
                    stats['failed'] += 1
                    error_msg = str(e)
                    stats['errors'].append(error_msg)
                    logger.exception("razorpay_renewal_failed", extra={"event": "razorpay_renewal_failed", "subscription_id": str(sub.get("_id")), "owner_id": str(sub.get("ownerId")), "error": error_msg})
                    
                    # Update subscription with error
                    await db.subscriptions.update_one(
                        {'_id': sub['_id']},
                        {
                            '$set': {
                                'renewalError': error_msg,
                                'updatedAt': datetime.now(timezone.utc).isoformat()
                            }
                        }
                    )
            
            logger.info(
                "razorpay_renewal_job_completed",
                extra={
                    "event": "razorpay_renewal_job_completed",
                    "checked": stats["checked"],
                    "renewed": stats["renewed"],
                    "notified": stats["notified"],
                    "failed": stats["failed"],
                },
            )
            return stats
            
        except Exception as e:
            logger.exception("razorpay_renewal_job_failed", extra={"event": "razorpay_renewal_job_failed", "error": str(e)})
            stats['errors'].append(str(e))
            return stats

    @staticmethod
    async def handle_subscription_payment_success(order_id: str, payment_id: str) -> bool:
        """
        Handle successful renewal payment
        
        Args:
            order_id: Razorpay order ID
            payment_id: Razorpay payment ID
            
        Returns:
            True if renewal was successful
        """
        try:
            # Find renewal order
            renewal = await db.renewal_orders.find_one({'orderId': order_id})
            if not renewal:
                logger.warning("razorpay_renewal_order_not_found", extra={"event": "razorpay_renewal_order_not_found", "order_id": order_id})
                return False
            
            # Update renewal order status
            await db.renewal_orders.update_one(
                {'_id': renewal['_id']},
                {
                    '$set': {
                        'paymentId': payment_id,
                        'status': 'completed',
                        'completedAt': datetime.now(timezone.utc).isoformat()
                    }
                }
            )
            
            # Extend the subscription
            sub = await db.subscriptions.find_one({'_id': renewal['subscriptionId']})
            if sub:
                # Calculate new period end
                # If current period end is in the past (late renewal), start from now
                # If current period end is in the future (early renewal), extend it
                # FIX: Ensure timezone-aware comparison
                current_end_str = sub['currentPeriodEnd']
                current_end = datetime.fromisoformat(current_end_str.replace('Z', '+00:00'))
                if current_end.tzinfo is None:
                    current_end = current_end.replace(tzinfo=timezone.utc)
                
                now = datetime.now(timezone.utc)
                
                base_date = max(current_end, now)
                new_end = base_date + timedelta(days=renewal['period'] * 30)
                
                await db.subscriptions.update_one(
                    {'_id': sub['_id']},
                    {
                        '$set': {
                            'currentPeriodStart': base_date.isoformat(),
                            'currentPeriodEnd': new_end.isoformat(),
                            'renewalError': None,
                            'updatedAt': datetime.now(timezone.utc).isoformat()
                        }
                    }
                )
                logger.info("razorpay_renewal_subscription_extended", extra={"event": "razorpay_renewal_subscription_extended", "owner_id": str(renewal.get("ownerId")), "order_id": order_id, "new_period_end": new_end.isoformat()})
            
            logger.info("razorpay_renewal_payment_success", extra={"event": "razorpay_renewal_payment_success", "order_id": order_id, "payment_id": payment_id})
            return True
            
        except Exception as e:
            logger.exception("razorpay_renewal_payment_success_handle_failed", extra={"event": "razorpay_renewal_payment_success_handle_failed", "order_id": order_id, "payment_id": payment_id, "error": str(e)})
            return False

    @staticmethod
    async def handle_subscription_payment_failed(order_id: str, error_msg: str) -> bool:
        """
        Handle failed renewal payment
        
        Args:
            order_id: Razorpay order ID
            error_msg: Error message
            
        Returns:
            True if handled successfully
        """
        try:
            # Find renewal order
            renewal = await db.renewal_orders.find_one({'orderId': order_id})
            if not renewal:
                logger.warning("razorpay_renewal_order_not_found", extra={"event": "razorpay_renewal_order_not_found", "order_id": order_id})
                return False
            
            # Update renewal order status
            await db.renewal_orders.update_one(
                {'_id': renewal['_id']},
                {
                    '$set': {
                        'status': 'failed',
                        'error': error_msg,
                        'failedAt': datetime.now(timezone.utc).isoformat()
                    }
                }
            )
            
            # Update subscription with error
            # FIX: Ensure ownerId is used consistently (str vs ObjectId)
            owner_id = renewal['ownerId']
            await db.subscriptions.update_one(
                {'ownerId': owner_id},
                {
                    '$set': {
                        'renewalError': f'Payment failed: {error_msg}',
                        'updatedAt': datetime.now(timezone.utc).isoformat()
                    }
                }
            )
            
            logger.warning("razorpay_renewal_payment_failed", extra={"event": "razorpay_renewal_payment_failed", "order_id": order_id, "error": error_msg})
            return True
            
        except Exception as e:
            logger.exception("razorpay_renewal_payment_failure_handle_failed", extra={"event": "razorpay_renewal_payment_failure_handle_failed", "order_id": order_id, "error": str(e)})
            return False
