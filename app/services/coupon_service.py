from datetime import datetime, timezone
from typing import Optional, Tuple
from app.models.coupon_schema import Coupon, CouponValidationResponse
from app.database.mongodb import db
import logging

logger = logging.getLogger(__name__)


def _coupon_meta(code: str) -> dict:
    normalized = (code or "").strip().upper()
    return {"coupon_code": normalized if normalized else "UNKNOWN"}

class CouponService:
    
    @staticmethod
    async def create_coupon(code: str, discount_type: str, discount_value: int, description: str = None,
                           max_usage: int = None, expires_at: str = None, min_amount: int = 0,
                           applicable_plans: list = None) -> Coupon:
        """Create a new coupon"""
        try:
            # Check if coupon code already exists
            existing = await db["coupons"].find_one({"code": code.upper()})
            if existing:
                logger.warning("coupon_create_duplicate", extra={"event": "coupon_create_duplicate", **_coupon_meta(code)})
                raise ValueError(f"Coupon code '{code}' already exists")
            
            # Validate discount value
            if discount_type == 'percentage' and (discount_value < 0 or discount_value > 100):
                raise ValueError("Percentage discount must be between 0 and 100")
            if discount_type == 'fixed' and discount_value <= 0:
                raise ValueError("Fixed discount must be greater than 0")
            
            now = datetime.now(timezone.utc).isoformat()
            
            coupon = Coupon(
                code=code.upper(),
                discountType=discount_type,
                discountValue=discount_value,
                description=description,
                maxUsageCount=max_usage,
                usageCount=0,
                expiresAt=expires_at,
                minAmount=min_amount,
                applicablePlans=applicable_plans or [],
                isActive=True,
                createdAt=now,
                updatedAt=now
            )
            
            result = await db["coupons"].insert_one(coupon.model_dump())
            logger.info(
                "coupon_created",
                extra={
                    "event": "coupon_created",
                    **_coupon_meta(code),
                    "discount_type": discount_type,
                    "discount_value": discount_value,
                    "has_expiry": bool(expires_at),
                    "max_usage": max_usage,
                },
            )
            return coupon
            
        except Exception as e:
            logger.exception("coupon_create_failed", extra={"event": "coupon_create_failed", **_coupon_meta(code), "error": str(e)})
            raise

    @staticmethod
    async def get_coupon(code: str) -> Optional[Coupon]:
        """Get coupon by code"""
        try:
            doc = await db["coupons"].find_one({"code": code.upper()})
            if doc:
                return Coupon(**doc)
            return None
        except Exception as e:
            logger.exception("coupon_get_failed", extra={"event": "coupon_get_failed", **_coupon_meta(code), "error": str(e)})
            return None

    @staticmethod
    async def validate_coupon(code: str, amount: int, plan: str = None) -> Tuple[bool, str, Optional[int], Optional[int]]:
        """
        Validate coupon and calculate discount
        
        Returns: (is_valid, message, original_amount, final_amount)
        """
        try:
            coupon = await CouponService.get_coupon(code)
            
            if not coupon:
                logger.info("coupon_validate_not_found", extra={"event": "coupon_validate_not_found", **_coupon_meta(code), "amount": amount, "plan": plan})
                return False, "Coupon not found", amount, amount
            
            if not coupon.isActive:
                logger.info("coupon_validate_inactive", extra={"event": "coupon_validate_inactive", **_coupon_meta(code), "amount": amount, "plan": plan})
                return False, "Coupon is inactive", amount, amount
            
            # Check expiration
            if coupon.expiresAt:
                # FIX: Handle potential naive datetimes from fromisoformat
                expires = datetime.fromisoformat(coupon.expiresAt.replace('Z', '+00:00'))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                
                if expires < datetime.now(timezone.utc):
                    logger.info("coupon_validate_expired", extra={"event": "coupon_validate_expired", **_coupon_meta(code), "amount": amount, "plan": plan})
                    return False, "Coupon has expired", amount, amount
            
            # Check usage limit
            if coupon.maxUsageCount and coupon.usageCount >= coupon.maxUsageCount:
                logger.info(
                    "coupon_validate_usage_limit_reached",
                    extra={
                        "event": "coupon_validate_usage_limit_reached",
                        **_coupon_meta(code),
                        "amount": amount,
                        "plan": plan,
                        "usage_count": coupon.usageCount,
                        "max_usage_count": coupon.maxUsageCount,
                    },
                )
                return False, "Coupon usage limit reached", amount, amount
            
            # Check minimum amount
            if amount < coupon.minAmount:
                logger.info(
                    "coupon_validate_min_amount_failed",
                    extra={"event": "coupon_validate_min_amount_failed", **_coupon_meta(code), "amount": amount, "min_amount": coupon.minAmount, "plan": plan},
                )
                return False, f"Minimum order amount {coupon.minAmount} paise required", amount, amount
            
            # Check applicable plans
            if coupon.applicablePlans and plan:
                if plan not in coupon.applicablePlans:
                    logger.info(
                        "coupon_validate_plan_not_applicable",
                        extra={"event": "coupon_validate_plan_not_applicable", **_coupon_meta(code), "plan": plan, "applicable_plans": coupon.applicablePlans},
                    )
                    return False, f"Coupon not applicable for {plan} plan", amount, amount
            
            # Calculate discount
            if coupon.discountType == 'percentage':
                discount = int(amount * coupon.discountValue / 100)
            else:  # fixed
                discount = min(coupon.discountValue, amount)  # Can't discount more than amount
            
            final_amount = amount - discount
            logger.info(
                "coupon_validate_success",
                extra={
                    "event": "coupon_validate_success",
                    **_coupon_meta(code),
                    "amount": amount,
                    "final_amount": final_amount,
                    "discount_amount": discount,
                    "plan": plan,
                },
            )
            
            return True, "Coupon applied successfully", amount, final_amount
            
        except Exception as e:
            logger.exception("coupon_validate_failed", extra={"event": "coupon_validate_failed", **_coupon_meta(code), "error": str(e)})
            return False, f"Error validating coupon: {str(e)}", amount, amount

    @staticmethod
    async def apply_coupon(code: str, amount: int, plan: str = None) -> CouponValidationResponse:
        """
        Apply coupon and return validation response with discount calculation
        """
        is_valid, message, original, final = await CouponService.validate_coupon(code, amount, plan)
        
        if not is_valid:
            return CouponValidationResponse(
                isValid=False,
                message=message,
                originalAmount=amount,
                discountAmount=0,
                finalAmount=amount,
                discountPercentage=None
            )
        
        coupon = await CouponService.get_coupon(code)
        discount = original - final
        discount_percentage = int(discount * 100 / original) if original > 0 else 0

        logger.info(
            "coupon_apply_success",
            extra={
                "event": "coupon_apply_success",
                **_coupon_meta(code),
                "amount": original,
                "final_amount": final,
                "discount_amount": discount,
                "plan": plan,
            },
        )
        
        return CouponValidationResponse(
            isValid=True,
            message=message,
            originalAmount=original,
            discountAmount=discount,
            finalAmount=final,
            discountPercentage=discount_percentage if coupon.discountType == 'percentage' else None
        )

    @staticmethod
    async def apply_and_increment_usage(code: str, amount: int, plan: str = None) -> Tuple[bool, str, Optional[int], Optional[int]]:
        """
        Atomically validate and increment coupon usage in one DB operation.
        Prevents race conditions where multiple requests use the last remaining coupon slot.
        """
        try:
            # 1. Initial validation (read-only)
            is_valid, message, original, final = await CouponService.validate_coupon(code, amount, plan)
            if not is_valid:
                return is_valid, message, original, final
            
            # 2. Atomic increment with condition
            # Filter must match the same criteria as validate_coupon
            filter_query = {
                "code": code.upper(),
                "isActive": True,
                "$or": [
                    {"maxUsageCount": None},
                    {"$expr": {"$lt": ["$usageCount", "$maxUsageCount"]}}
                ]
            }
            
            # We also need to re-check expiration in the filter for true atomicity
            # but MongoDB date comparison with strings in $expr is complex.
            # Since expiresAt doesn't change frequently, the previous validate_coupon check is mostly sufficient.
            
            update_op = {
                "$inc": {"usageCount": 1},
                "$set": {"updatedAt": datetime.now(timezone.utc).isoformat()}
            }
            
            result = await db["coupons"].find_one_and_update(
                filter_query,
                update_op,
                return_document=True
            )
            
            if not result:
                logger.warning("coupon_atomic_increment_failed", extra={"event": "coupon_atomic_increment_failed", **_coupon_meta(code)})
                return False, "Coupon usage limit reached or coupon deactivated", amount, amount
                
            logger.info("coupon_atomic_apply_success", extra={"event": "coupon_atomic_apply_success", **_coupon_meta(code)})
            return True, "Coupon applied and usage incremented", original, final
            
        except Exception as e:
            logger.exception("coupon_atomic_apply_failed", extra={"event": "coupon_atomic_apply_failed", **_coupon_meta(code), "error": str(e)})
            return False, f"Error applying coupon: {str(e)}", amount, amount

    @staticmethod
    async def increment_usage(code: str) -> bool:
        """Increment coupon usage count after successful payment"""
        try:
            result = await db["coupons"].update_one(
                {"code": code.upper()},
                {"$inc": {"usageCount": 1}, "$set": {"updatedAt": datetime.now(timezone.utc).isoformat()}}
            )
            updated = result.modified_count > 0
            if updated:
                logger.info("coupon_usage_incremented", extra={"event": "coupon_usage_incremented", **_coupon_meta(code)})
            return updated
        except Exception as e:
            logger.exception("coupon_increment_usage_failed", extra={"event": "coupon_increment_usage_failed", **_coupon_meta(code), "error": str(e)})
            return False

    @staticmethod
    async def update_coupon(code: str, **kwargs) -> Optional[Coupon]:
        """Update coupon fields"""
        try:
            # Don't allow code changes
            if 'code' in kwargs:
                del kwargs['code']
            
            kwargs['updatedAt'] = datetime.now(timezone.utc).isoformat()
            
            result = await db["coupons"].find_one_and_update(
                {"code": code.upper()},
                {"$set": kwargs},
                return_document=True
            )
            
            if result:
                logger.info(
                    "coupon_updated",
                    extra={"event": "coupon_updated", **_coupon_meta(code), "updated_fields": list(kwargs.keys())},
                )
                return Coupon(**result)
            return None
        except Exception as e:
            logger.exception("coupon_update_failed", extra={"event": "coupon_update_failed", **_coupon_meta(code), "error": str(e)})
            return None

    @staticmethod
    async def delete_coupon(code: str) -> bool:
        """Delete coupon"""
        try:
            result = await db["coupons"].delete_one({"code": code.upper()})
            deleted = result.deleted_count > 0
            if deleted:
                logger.info("coupon_deleted", extra={"event": "coupon_deleted", **_coupon_meta(code)})
            return deleted
        except Exception as e:
            logger.exception("coupon_delete_failed", extra={"event": "coupon_delete_failed", **_coupon_meta(code), "error": str(e)})
            return False

    @staticmethod
    async def list_coupons(is_active: bool = None) -> list:
        """List all coupons with optional filtering"""
        try:
            query = {}
            if is_active is not None:
                query['isActive'] = is_active
            
            coupons = await db["coupons"].find(query).to_list(length=None)
            logger.info("coupon_list_success", extra={"event": "coupon_list_success", "is_active": is_active, "count": len(coupons)})
            return [Coupon(**doc) for doc in coupons]
        except Exception as e:
            logger.exception("coupon_list_failed", extra={"event": "coupon_list_failed", "is_active": is_active, "error": str(e)})
            return []

    @staticmethod
    async def get_coupon_stats(code: str) -> Optional[dict]:
        """Get coupon usage statistics"""
        try:
            coupon = await CouponService.get_coupon(code)
            if not coupon:
                return None
            
            usage_percentage = 0
            if coupon.maxUsageCount:
                usage_percentage = int(coupon.usageCount * 100 / coupon.maxUsageCount)
            logger.info(
                "coupon_stats_success",
                extra={
                    "event": "coupon_stats_success",
                    **_coupon_meta(code),
                    "usage_count": coupon.usageCount,
                    "max_usage_count": coupon.maxUsageCount,
                },
            )
            
            return {
                'code': coupon.code,
                'discountType': coupon.discountType,
                'discountValue': coupon.discountValue,
                'totalUsage': coupon.usageCount,
                'maxUsage': coupon.maxUsageCount,
                'usagePercentage': usage_percentage if coupon.maxUsageCount else None,
                'isActive': coupon.isActive,
                'expiresAt': coupon.expiresAt,
                'createdAt': coupon.createdAt
            }
        except Exception as e:
            logger.exception("coupon_stats_failed", extra={"event": "coupon_stats_failed", **_coupon_meta(code), "error": str(e)})
            return None
