from fastapi import APIRouter, HTTPException, status, Request
from app.services.staff_service import StaffService
from app.models.staff_schema import StaffCreate, StaffUpdate
from app.database.mongodb import db
from app.config import settings

router = APIRouter(prefix="/staff", tags=["staff"])
staff_service = StaffService()


@router.get("")
async def get_staff(
    request: Request,
    property_id: str = None,
    search: str = None,
    role: str = None,
    page: int = 1,
    page_size: int = 50,
):
    """Get list of staff members"""
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    skip = (page - 1) * page_size

    staff_list, total = await staff_service.get_staff_list(
        property_id=property_id,
        search=search,
        role=role,
        skip=skip,
        limit=page_size,
    )

    property_ids = getattr(request.state, "property_ids", [])
    filtered = (
        [s for s in staff_list if s.propertyId and s.propertyId in property_ids]
        if property_id is None
        else staff_list
    )

    return {
        "data": [staff.model_dump(exclude_none=True) for staff in filtered],
        "meta": {
            "total": total,
            "page": page,
            "pageSize": page_size,
            "hasMore": skip + page_size < total,
        },
    }


@router.get("/{staff_id}")
async def get_staff_detail(request: Request, staff_id: str):
    """Get single staff member"""
    staff = await staff_service.get_staff(staff_id)
    if not staff:
        raise HTTPException(status_code=404, detail="Staff not found")

    property_ids = getattr(request.state, "property_ids", [])
    if staff.propertyId not in property_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    return {"data": staff.model_dump()}


@router.post("")
async def create_staff(request: Request, staff: StaffCreate):
    """Create new staff member"""
    try:
        if not staff.propertyId:
            raise HTTPException(status_code=400, detail="propertyId is required")

        property_ids = getattr(request.state, "property_ids", [])
        if staff.propertyId not in property_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

        # Quota check removed
        created = await staff_service.create_staff(
            staff.model_dump(exclude_unset=True)
        )
        return {"data": created.model_dump()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Error creating staff. Please try again."
        )


@router.patch("/{staff_id}")
async def update_staff(request: Request, staff_id: str, staff: StaffUpdate):
    """Update staff member"""
    try:
        orig = await staff_service.get_staff(staff_id)
        if not orig:
            raise HTTPException(status_code=404, detail="Staff not found")

        property_ids = getattr(request.state, "property_ids", [])
        if orig.propertyId not in property_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

        updated = await staff_service.update_staff(
            staff_id, staff.model_dump(exclude_unset=True)
        )
        return {"data": updated.model_dump()} if updated else {"data": {}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Error updating staff. Please try again."
        )


@router.delete("/{staff_id}")
async def delete_staff(request: Request, staff_id: str):
    """Delete staff member"""
    try:
        orig = await staff_service.get_staff(staff_id)
        if not orig:
            raise HTTPException(status_code=404, detail="Staff not found")

        property_ids = getattr(request.state, "property_ids", [])
        if orig.propertyId not in property_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

        result = await staff_service.delete_staff(staff_id)
        return {"success": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Error deleting staff. Please try again."
        )
