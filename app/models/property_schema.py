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
    ownerId: str = Field(..., description="Primary owner ID")
    active: bool = Field(default=True, description="Is property active")
    createdAt: Optional[str] = Field(None, description="Created at ISO string")
    updatedAt: Optional[str] = Field(None, description="Updated at ISO string")
