from pydantic import BaseModel, Field
from typing import Optional, Literal
from enum import Enum


class StaffRole(str, Enum):
    """Staff role enumeration"""
    COOKER = 'cooker'
    WORKER = 'worker'
    CLEANER = 'cleaner'
    MANAGER = 'manager'
    SECURITY = 'security'
    MAINTENANCE = 'maintenance'
    ASSISTANT = 'assistant'
    OTHER = 'other'


class StaffStatus(str, Enum):
    """Staff employment status"""
    ACTIVE = 'active'
    INACTIVE = 'inactive'
    ON_LEAVE = 'on_leave'
    TERMINATED = 'terminated'


class Staff(BaseModel):
    """Staff member model"""
    id: Optional[str] = None
    propertyId: Optional[str] = None
    name: Optional[str] = None
    role: Optional[StaffRole] = None
    mobileNumber: Optional[str] = None
    address: Optional[str] = None
    status: StaffStatus = StaffStatus.ACTIVE
    joiningDate: Optional[str] = None
    salary: Optional[float] = None
    emergencyContact: Optional[str] = None
    emergencyContactNumber: Optional[str] = None
    notes: Optional[str] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    archived: bool = False
    archivedReason: Optional[str] = None
    archivedAt: Optional[str] = None


class StaffOut(BaseModel):
    """Response model for staff"""
    id: Optional[str] = None
    propertyId: Optional[str] = None
    name: Optional[str] = None
    role: Optional[StaffRole] = None
    mobileNumber: Optional[str] = None
    address: Optional[str] = None
    status: StaffStatus = StaffStatus.ACTIVE
    joiningDate: Optional[str] = None
    salary: Optional[float] = None
    emergencyContact: Optional[str] = None
    emergencyContactNumber: Optional[str] = None
    notes: Optional[str] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    archived: bool = False
    archivedReason: Optional[str] = None
    archivedAt: Optional[str] = None


class StaffCreate(BaseModel):
    """Model for creating staff"""
    propertyId: Optional[str] = None
    name: str
    role: StaffRole
    mobileNumber: str
    address: str
    joiningDate: str
    salary: float


class StaffUpdate(BaseModel):
    """Model for updating staff"""
    name: Optional[str] = None
    role: Optional[StaffRole] = None
    mobileNumber: Optional[str] = None
    address: Optional[str] = None
    joiningDate: Optional[str] = None
    salary: Optional[float] = None
