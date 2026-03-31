from fastapi import APIRouter, Depends, HTTPException, Body, Query
from typing import Optional
from datetime import datetime
from app.services.coupon_service import CouponService
from app.utils.helpers import get_current_user, require_admin_user

router = APIRouter(prefix="/coupons", tags=["coupons"])

# ==================== ADMIN ENDPOINTS ====================

@router.post("/admin/create")
async def create_coupon(
    payload: dict = Body(...),
    admin_user: dict = Depends(require_admin_user)
):
    """
    Admin endpoint: Create a new coupon
    
    Request body:
    {
        "code": "SAVE50",
        "discountType": "percentage",  // or "fixed"
        "discountValue": 50,  // 50% or 50 paise
        "description": "Save 50% on annual plans",
        "maxUsageCount": 100,  // Optional, null = unlimited
        "expiresAt": "2026-12-31T23:59:59",  // Optional ISO format
        "minAmount": 0,  // Minimum order amount in paise
        "applicablePlans": ["pro", "premium"]  // Optional, [] = all plans
    }
    """
    try:
        code = payload.get("code", "").strip()
        discount_type = payload.get("discountType", "").lower()
        discount_value = payload.get("discountValue")
        
        if not code:
            raise HTTPException(status_code=400, detail="Coupon code is required")
        if discount_type not in ["percentage", "fixed"]:
            raise HTTPException(status_code=400, detail="discountType must be 'percentage' or 'fixed'")
        if discount_value is None:
            raise HTTPException(status_code=400, detail="discountValue is required")
        
        coupon = await CouponService.create_coupon(
            code=code,
            discount_type=discount_type,
            discount_value=discount_value,
            description=payload.get("description"),
            max_usage=payload.get("maxUsageCount"),
            expires_at=payload.get("expiresAt"),
            min_amount=payload.get("minAmount", 0),
            applicable_plans=payload.get("applicablePlans", [])
        )
        
        return {
            "data": coupon.model_dump(),
            "message": f"Coupon '{code}' created successfully"
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Error creating coupon. Please try again."
        )

@router.get("/admin/list")
async def list_coupons(
    is_active: Optional[bool] = Query(None),
    admin_user: dict = Depends(require_admin_user)
):
    """Admin endpoint: List all coupons"""
    try:
        coupons = await CouponService.list_coupons(is_active=is_active)
        return {
            "data": [c.model_dump() for c in coupons],
            "total": len(coupons)
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Error retrieving coupons. Please try again."
        )

@router.get("/admin/{code}")
async def get_coupon(
    code: str,
    admin_user: dict = Depends(require_admin_user)
):
    """Admin endpoint: Get coupon details and stats"""
    try:
        coupon = await CouponService.get_coupon(code)
        if not coupon:
            raise HTTPException(status_code=404, detail="Coupon not found")
        
        stats = await CouponService.get_coupon_stats(code)
        
        return {
            "data": {
                "coupon": coupon.model_dump(),
                "stats": stats
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Error retrieving coupon details."
        )

@router.patch("/admin/{code}")
async def update_coupon(
    code: str,
    payload: dict = Body(...),
    admin_user: dict = Depends(require_admin_user)
):
    """
    Admin endpoint: Update coupon
    
    Can update: isActive, discountValue, maxUsageCount, expiresAt, minAmount, applicablePlans
    """
    try:
        coupon = await CouponService.update_coupon(code, **payload)
        if not coupon:
            raise HTTPException(status_code=404, detail="Coupon not found")
        
        return {
            "data": coupon.model_dump(),
            "message": f"Coupon '{code}' updated successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Error updating coupon. Please try again."
        )

@router.delete("/admin/{code}")
async def delete_coupon(
    code: str,
    admin_user: dict = Depends(require_admin_user)
):
    """Admin endpoint: Delete coupon"""
    try:
        success = await CouponService.delete_coupon(code)
        if not success:
            raise HTTPException(status_code=404, detail="Coupon not found")
        
        return {
            "data": {"code": code},
            "message": f"Coupon '{code}' deleted successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Error deleting coupon. Please try again."
        )

# ==================== PUBLIC ENDPOINTS ====================

@router.get("/validate/{code}")
async def validate_coupon(
    code: str,
    amount: int = Query(..., description="Amount in paise"),
    plan: Optional[str] = Query(None, description="Plan name (optional)"),
    user_id: str = Depends(get_current_user)
):
    """
    Public endpoint: Validate coupon and get discount details
    
    Query params:
    - code: Coupon code
    - amount: Order amount in paise (required)
    - plan: Plan name (optional)
    
    Returns:
    {
        "data": {
            "isValid": true,
            "message": "Coupon applied successfully",
            "originalAmount": 7900,
            "discountAmount": 3950,
            "finalAmount": 3950,
            "discountPercentage": 50
        }
    }
    """
    try:
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Amount must be greater than 0")
        
        response = await CouponService.apply_coupon(code, amount, plan)
        return {"data": response.model_dump()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Error validating coupon. Please try again."
        )

@router.post("/apply")
async def apply_coupon_to_payment(
    payload: dict = Body(...),
    user_id: str = Depends(get_current_user)
):
    """
    Public endpoint: Apply coupon and get final amount for payment
    
    Request body:
    {
        "code": "SAVE50",
        "amount": 7900,  // in paise
        "plan": "pro"    // optional
    }
    """
    try:
        code = payload.get("code", "").strip()
        amount = payload.get("amount")
        plan = payload.get("plan")
        
        if not code:
            raise HTTPException(status_code=400, detail="Coupon code is required")
        if amount is None:
            raise HTTPException(status_code=400, detail="Amount is required")
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Amount must be greater than 0")
        
        response = await CouponService.apply_coupon(code, amount, plan)
        return {"data": response.model_dump()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Error applying coupon. Please try again."
        )
