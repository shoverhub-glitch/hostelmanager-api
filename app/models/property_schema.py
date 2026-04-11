from pydantic import BaseModel, Field
from typing import Optional


class PropertyInputBase(BaseModel):
    name: str = Field(..., description="Property name")
    address: str = Field(..., description="Property address")


class PropertyCreate(PropertyInputBase):
    pass


class PropertyUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None


class PropertyOut(PropertyInputBase):
    id: str
    ownerIds: list[str] = Field(default_factory=list, description="Owner IDs")
    ownerId: Optional[str] = Field(default=None, description="Primary owner ID (legacy compatibility)")
    active: bool = Field(default=True, description="Is property active")
    createdAt: Optional[str] = Field(None, description="Created at ISO string")
    updatedAt: Optional[str] = Field(None, description="Updated at ISO string")
