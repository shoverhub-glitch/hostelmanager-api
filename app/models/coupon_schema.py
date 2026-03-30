from typing import Optional, Literal, List
from datetime import datetime
from pydantic import BaseModel, Field

class Coupon(BaseModel):
    code: str  # Unique coupon code
    discountType: Literal['percentage', 'fixed']  # Type of discount
    discountValue: int  # Percentage (0-100) or fixed amount in paise
    description: Optional[str] = None
    maxUsageCount: Optional[int] = None  # None = unlimited
    usageCount: int = 0  # How many times used
    expiresAt: Optional[str] = None  # ISO format datetime
    minAmount: int = 0  # Minimum order amount (in paise) to apply coupon
    applicablePlans: List[str] = Field(default_factory=list)  # [] = all plans, ['pro', 'premium'] = specific plans
    isActive: bool = True
    createdAt: str
    updatedAt: str

class CouponValidationResponse(BaseModel):
    """Response when validating a coupon"""
    isValid: bool
    message: str
    originalAmount: Optional[int] = None  # Original price in paise
    discountAmount: Optional[int] = None  # Discount in paise
    finalAmount: Optional[int] = None  # Final price after discount
    discountPercentage: Optional[int] = None  # For percentage discounts, show the %

