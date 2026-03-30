from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, Literal, Union
from datetime import datetime, date
from enum import Enum

class PaymentStatus(str, Enum):
    PAID = 'paid'
    DUE = 'due'

class PaymentMethod(str, Enum):
    CASH = 'Cash'
    ONLINE = 'Online'
    BANK_TRANSFER = 'Bank Transfer'
    UPI = 'UPI'
    CHEQUE = 'Cheque'

def format_amount_paise(paise: int) -> str:
    """Format amount in paise to display string (e.g., 150000 -> "₹1,500")"""
    rupees = paise / 100
    return f"₹{rupees:,.0f}"

def parse_amount_to_paise(amount: Union[str, int, float]) -> int:
    """Parse amount input to integer paise. Accepts rupee string (with/without ₹), int paise, or float."""
    if isinstance(amount, int):
        return amount
    if isinstance(amount, float):
        return int(amount * 100)
    if isinstance(amount, str):
        cleaned = amount.replace('₹', '').replace(',', '').strip()
        if not cleaned:
            return 0
        try:
            # Check if it's already in paise (large integer) or rupees (decimal)
            value = float(cleaned)
            if value > 1000000:  # Likely already in paise (more than 10 lakhs)
                return int(value)
            return int(value * 100)  # Convert rupees to paise
        except ValueError:
            return 0
    return 0


class PaymentBase(BaseModel):
    tenantId: str
    propertyId: str
    bed: Optional[str] = None
    amount: Union[int, str]  # int = paise internally, str = display format
    status: Literal['paid', 'due']
    dueDate: Optional[date] = None
    paidDate: Optional[date] = None
    method: Optional[str] = Field(default=PaymentMethod.CASH.value)

    @model_validator(mode='after')
    def format_amount_for_display(self):
        """Convert amount to formatted display string for API responses"""
        if isinstance(self.amount, int):
            self.amount = format_amount_paise(self.amount)
        return self

    @field_validator('dueDate', 'paidDate', mode='before')
    @classmethod
    def normalize_date_fields(cls, value):
        if value is None or value == '':
            return None

        if isinstance(value, date) and not isinstance(value, datetime):
            return value

        if isinstance(value, datetime):
            return value.date()

        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return None

            if 'T' in raw:
                try:
                    return datetime.fromisoformat(raw.replace('Z', '+00:00')).date()
                except ValueError:
                    try:
                        return date.fromisoformat(raw[:10])
                    except ValueError:
                        return raw

            try:
                return date.fromisoformat(raw)
            except ValueError:
                try:
                    return datetime.fromisoformat(raw.replace('Z', '+00:00')).date()
                except ValueError:
                    return raw

        return value

class PaymentCreate(PaymentBase):
    pass

class Payment(PaymentBase):
    id: str
    createdAt: datetime
    updatedAt: datetime
    tenantName: Optional[str] = None  # Enriched field from tenant lookup
    roomNumber: Optional[str] = None  # Enriched field from room lookup
    tenantStatus: Optional[str] = None  # Enriched field from tenant lookup


class PaymentUpdate(BaseModel):
    """
    Payment update model for PATCH requests.
    All fields are optional - only provided fields will be updated.
    Dates can be provided as date objects or ISO string format.
    Amount can be provided as int (paise) or str (rupees with ₹).
    """
    tenantId: Optional[str] = None
    propertyId: Optional[str] = None
    bed: Optional[str] = None
    amount: Optional[Union[int, str]] = None
    status: Optional[str] = None
    dueDate: Optional[Union[str, date]] = None  # Can be string (ISO format) or date object
    paidDate: Optional[Union[str, date]] = None  # Can be string (ISO format) or date object
    method: Optional[str] = None