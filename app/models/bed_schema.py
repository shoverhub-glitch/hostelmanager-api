from pydantic import BaseModel, Field
from typing import Literal, Optional
from datetime import datetime
from enum import Enum

class BedStatus(str, Enum):
    AVAILABLE = 'available'
    OCCUPIED = 'occupied'
    MAINTENANCE = 'maintenance'

class BedBase(BaseModel):
    propertyId: str
    roomId: str
    bedNumber: str
    status: Literal['available', 'occupied', 'maintenance'] = BedStatus.AVAILABLE.value
    tenantId: Optional[str] = None

class BedCreate(BedBase):
    pass

class BedUpdate(BaseModel):
    bedNumber: Optional[str] = None
    status: Optional[Literal['available', 'occupied', 'maintenance']] = None
    tenantId: Optional[str] = None

class BedOut(BedBase):
    id: str
    createdAt: datetime
    updatedAt: datetime
