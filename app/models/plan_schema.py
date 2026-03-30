"""
Subscription Plan Schema
Defines the structure for subscription plans stored in the database.
Admin can create/update plans which will be used by all property owners.
"""

from datetime import datetime
from typing import Dict, Optional
from pydantic import BaseModel, Field, field_validator


class PlanBase(BaseModel):
    """Base model for subscription plan"""
    name: str = Field(..., description="Unique plan identifier (e.g., 'pro', 'premium')")
    display_name: str = Field(..., description="Human-readable plan name (e.g., 'Pro Plan')")
    description: Optional[str] = Field(None, description="Plan description")
    properties: int = Field(..., ge=0, description="Number of properties allowed")
    tenants: int = Field(..., ge=0, description="Number of tenants per property")
    rooms: int = Field(..., ge=0, description="Number of rooms per property")
    staff: int = Field(..., ge=0, description="Number of staff per property")
    periods: Dict[int, int] = Field(
        ..., 
        description="Billing periods with prices (period in months -> price in paise). Use 0 for free plans."
    )
    is_active: bool = Field(True, description="Whether this plan is available for selection")
    sort_order: int = Field(0, description="Display order (lower = shown first)")
    razorpay_plan_ids: Dict[str, str] = Field(
        default_factory=dict,
        description="Razorpay Plan IDs keyed by period in months e.g. {'1': 'plan_xxx', '3': 'plan_yyy'}"
    )
    
    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate plan name format"""
        if not v or not v.strip():
            raise ValueError("Plan name cannot be empty")
        # Convert to lowercase for consistency
        return v.lower().strip()
    
    @field_validator('periods')
    @classmethod
    def validate_periods(cls, v: Dict[int, int]) -> Dict[int, int]:
        """Validate periods structure"""
        if not v:
            raise ValueError("At least one period must be defined")
        
        for period, price in v.items():
            if period < 0:
                raise ValueError("Period must be non-negative")
            if price < 0:
                raise ValueError("Price must be non-negative")
            if period == 0 and price != 0:
                raise ValueError("Period 0 (free/forever) must have price 0")
        
        return v


class PlanCreate(PlanBase):
    """Schema for creating a new plan"""
    pass


class PlanUpdate(BaseModel):
    """Schema for updating an existing plan"""
    display_name: Optional[str] = None
    description: Optional[str] = None
    properties: Optional[int] = Field(None, ge=0)
    tenants: Optional[int] = Field(None, ge=0)
    rooms: Optional[int] = Field(None, ge=0)
    staff: Optional[int] = Field(None, ge=0)
    periods: Optional[Dict[int, int]] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None
    
    @field_validator('periods')
    @classmethod
    def validate_periods(cls, v: Optional[Dict[int, int]]) -> Optional[Dict[int, int]]:
        """Validate periods structure if provided"""
        if v is None:
            return v
            
        if not v:
            raise ValueError("If periods is provided, it cannot be empty")
        
        for period, price in v.items():
            if period < 0:
                raise ValueError("Period must be non-negative")
            if price < 0:
                raise ValueError("Price must be non-negative")
            if period == 0 and price != 0:
                raise ValueError("Period 0 (free/forever) must have price 0")
        
        return v


class Plan(PlanBase):
    """Schema for plan with database fields"""
    id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
