from app.models.razorpay_order import RazorpayOrder
from app.config import settings
from app.database.mongodb import db
from datetime import datetime, timezone
import asyncio
import functools
import razorpay
import hmac
import hashlib
import logging


logger = logging.getLogger(__name__)

class RazorpayService:
    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

    @staticmethod
    async def create_order(user_id: str, plan: str, period: int, amount: int, currency: str, receipt: str, coupon_code: str = None):
        try:
            order_data = {
                "amount": amount,
                "currency": currency,
                "receipt": receipt,
                "payment_capture": 1,
                "notes": {
                    "owner_id": user_id,
                    "plan": plan,
                    "period": str(period),
                    "coupon_code": coupon_code or ""
                }
            }
            loop = asyncio.get_event_loop()
            order = await loop.run_in_executor(
                None, functools.partial(RazorpayService.client.order.create, order_data)
            )
            now = datetime.now(timezone.utc).isoformat()
            order_doc = RazorpayOrder(
                order_id=order["id"],
                user_id=user_id,
                plan=plan,
                period=period,
                amount=order["amount"],
                currency=order["currency"],
                status=order["status"],
                receipt=order["receipt"],
                coupon_code=coupon_code,
                created_at=now,
                updated_at=now
            )
            await db["razorpay_orders"].insert_one(order_doc.model_dump())
            logger.info(
                "razorpay_order_created",
                extra={
                    "event": "razorpay_order_created",
                    "order_id": order_doc.order_id,
                    "user_id": user_id,
                    "plan": plan,
                    "period": period,
                    "amount": amount,
                    "currency": currency,
                    "coupon_applied": bool(coupon_code),
                },
            )
            return order_doc
        except Exception as e:
            logger.exception(
                "razorpay_order_create_failed",
                extra={
                    "event": "razorpay_order_create_failed",
                    "user_id": user_id,
                    "plan": plan,
                    "period": period,
                    "amount": amount,
                    "currency": currency,
                    "error": str(e),
                },
            )
            raise

    @staticmethod
    async def verify_payment(order_id: str, payment_id: str, signature: str):
        order = await db["razorpay_orders"].find_one({"order_id": order_id})
        if not order:
            logger.warning("razorpay_verify_order_not_found", extra={"event": "razorpay_verify_order_not_found", "order_id": order_id})
            return False, "Order not found", None
        generated_signature = hmac.new(
            settings.RAZORPAY_KEY_SECRET.encode(),
            f"{order_id}|{payment_id}".encode(),
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(generated_signature, signature):
            logger.warning("razorpay_verify_invalid_signature", extra={"event": "razorpay_verify_invalid_signature", "order_id": order_id, "payment_id": payment_id})
            return False, "Invalid signature", None
        await db["razorpay_orders"].update_one(
            {"order_id": order_id},
            {"$set": {"status": "paid", "payment_id": payment_id, "signature": signature, "updated_at": datetime.now(timezone.utc).isoformat()}}
        )
        logger.info(
            "razorpay_verify_success",
            extra={
                "event": "razorpay_verify_success",
                "order_id": order_id,
                "payment_id": payment_id,
                "plan": order.get("plan"),
                "period": order.get("period", 1),
                "coupon_applied": bool(order.get("coupon_code")),
            },
        )
        # Return tuple of (plan, period, coupon_code) so subscription can be updated with all data
        return True, {"plan": order["plan"], "period": order.get("period", 1)}, order.get("coupon_code")
