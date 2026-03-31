from typing import Optional, Literal
from datetime import datetime
from pydantic import BaseModel

class Subscription(BaseModel):
    ownerId: str
    plan: str  # Plan name (e.g., 'free', 'pro', 'premium') - now dynamic
    period: int = 1  # Billing period in months (1, 3, 6, 12, etc.)
    status: Literal['active', 'inactive', 'cancelled', 'past_due'] = 'active'
    price: int  # Price in paise for this period (e.g., 7900 for ₹79)
    currentPeriodStart: str
    currentPeriodEnd: str
    propertyLimit: int
    roomLimit: int
    tenantLimit: int
    staffLimit: int
    createdAt: str
    updatedAt: str
    # Auto-renewal fields
    autoRenewal: bool = True  # Enable auto-renewal by default
    razorpaySubscriptionId: Optional[str] = None  # Razorpay subscription ID for recurring payments
    renewalError: Optional[str] = None  # Last renewal error message if any
    cancelAtPeriodEnd: bool = False  # True when user cancels but retains access until period end

class Usage(BaseModel):
    ownerId: str
    properties: int
    tenants: int
    rooms: int
    staff: int = 0  # Staff count for quota monitoring
    updatedAt: str
