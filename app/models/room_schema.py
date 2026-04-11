from pydantic import BaseModel
from typing import Optional


class Room(BaseModel):
    id: Optional[str] = None
    propertyId: str
    roomNumber: str
    floor: str
    price: int
    numberOfBeds: int
    active: bool = True
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class RoomUpdate(BaseModel):
    propertyId: Optional[str] = None
    roomNumber: Optional[str] = None
    floor: Optional[str] = None
    price: Optional[int] = None
    numberOfBeds: Optional[int] = None
    active: Optional[bool] = None
    updatedAt: Optional[str] = None
