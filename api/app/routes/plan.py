"""
Plan Management Routes (Admin Only)
Allows admin to create, update, and manage subscription plans.
All property owners will use these centrally managed plans.
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
import logging

from app.models.plan_schema import Plan, PlanCreate, PlanUpdate
from app.services.plan_service import PlanService
from app.utils.helpers import require_admin_user


router = APIRouter(prefix="/admin/plans", tags=["Admin - Plans"])
logger = logging.getLogger(__name__)


@router.post("", response_model=Plan, status_code=status.HTTP_201_CREATED)
async def create_plan(
    plan_data: PlanCreate,
    current_user: dict = Depends(require_admin_user)
):
    """
    Create a new subscription plan (Admin only)
    
    - Validates plan name uniqueness
    - Validates periods and pricing
    - Stores in database for all property owners to use
    
    Example:
    ```json
    {
      "name": "starter",
      "display_name": "Starter Plan",
      "description": "For small property owners",
      "properties": 2,
      "tenants": 10,
      "rooms": 10,
      "staff": 3,
      "periods": {
        "1": 4900,
        "3": 12000,
        "6": 20000
      },
      "is_active": true,
      "sort_order": 1
    }
    ```
    """
    try:
        plan = await PlanService.create_plan(plan_data)
        logger.info("plan_route_create_success", extra={"event": "plan_route_create_success", "plan_name": plan.name})
        return plan
    except ValueError as e:
        logger.warning("plan_route_create_validation_failed", extra={"event": "plan_route_create_validation_failed", "plan_name": plan_data.name, "error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.exception("plan_route_create_failed", extra={"event": "plan_route_create_failed", "plan_name": plan_data.name, "error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create plan: {str(e)}"
        )


@router.get("", response_model=List[Plan])
async def list_plans(
    active_only: bool = False,
    current_user: dict = Depends(require_admin_user)
):
    """
    List all subscription plans (Admin view)
    
    Query Parameters:
    - active_only: If true, return only active plans
    
    Returns list of all plans sorted by sort_order
    """
    try:
        plans = await PlanService.get_all_plans(active_only=active_only)
        logger.info("plan_route_list_success", extra={"event": "plan_route_list_success", "active_only": active_only, "count": len(plans)})
        return plans
    except Exception as e:
        logger.exception("plan_route_list_failed", extra={"event": "plan_route_list_failed", "active_only": active_only, "error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch plans: {str(e)}"
        )


@router.get("/stats")
async def get_plan_stats(
    current_user: dict = Depends(require_admin_user)
):
    """
    Get statistics about plans and their usage (Admin only)
    
    Returns:
    - Total plans
    - Active/inactive counts
    - Usage by plan (active subscriptions per plan)
    """
    try:
        stats = await PlanService.get_plan_stats()
        logger.info("plan_route_stats_success", extra={"event": "plan_route_stats_success"})
        return stats
    except Exception as e:
        logger.exception("plan_route_stats_failed", extra={"event": "plan_route_stats_failed", "error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch stats: {str(e)}"
        )


@router.get("/{plan_name}", response_model=Plan)
async def get_plan(
    plan_name: str,
    current_user: dict = Depends(require_admin_user)
):
    """
    Get a specific plan by name (Admin view)
    
    Returns complete plan details including all periods and pricing
    """
    plan = await PlanService.get_plan_by_name(plan_name)
    if not plan:
        logger.warning("plan_route_get_not_found", extra={"event": "plan_route_get_not_found", "plan_name": plan_name.lower()})
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan '{plan_name}' not found"
        )
    logger.info("plan_route_get_success", extra={"event": "plan_route_get_success", "plan_name": plan_name.lower()})
    return plan


@router.patch("/{plan_name}", response_model=Plan)
async def update_plan(
    plan_name: str,
    update_data: PlanUpdate,
    current_user: dict = Depends(require_admin_user)
):
    """
    Update an existing plan (Admin only)
    
    - Can update pricing, limits, description, etc.
    - Cannot change plan name (create new plan instead)
    - Updates reflected immediately for all users
    
    Example - Update pricing:
    ```json
    {
      "periods": {
        "1": 8900,
        "3": 22000,
        "6": 38000,
        "12": 65000
      }
    }
    ```
    """
    try:
        updated_plan = await PlanService.update_plan(plan_name, update_data)
        if not updated_plan:
            logger.warning("plan_route_update_not_found", extra={"event": "plan_route_update_not_found", "plan_name": plan_name.lower()})
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Plan '{plan_name}' not found"
            )
        logger.info("plan_route_update_success", extra={"event": "plan_route_update_success", "plan_name": plan_name.lower()})
        return updated_plan
    except ValueError as e:
        logger.warning("plan_route_update_validation_failed", extra={"event": "plan_route_update_validation_failed", "plan_name": plan_name.lower(), "error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.exception("plan_route_update_failed", extra={"event": "plan_route_update_failed", "plan_name": plan_name.lower(), "error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update plan: {str(e)}"
        )


@router.delete("/{plan_name}")
async def delete_plan(
    plan_name: str,
    current_user: dict = Depends(require_admin_user)
):
    """
    Delete a plan (Admin only)
    
    CAUTION: Cannot delete plans with active subscriptions
    Consider deactivating instead
    """
    try:
        deleted = await PlanService.delete_plan(plan_name)
        if not deleted:
            logger.warning("plan_route_delete_not_found", extra={"event": "plan_route_delete_not_found", "plan_name": plan_name.lower()})
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Plan '{plan_name}' not found"
            )
        logger.info("plan_route_delete_success", extra={"event": "plan_route_delete_success", "plan_name": plan_name.lower()})
        return {"success": True, "message": f"Plan '{plan_name}' deleted successfully"}
    except ValueError as e:
        logger.warning("plan_route_delete_validation_failed", extra={"event": "plan_route_delete_validation_failed", "plan_name": plan_name.lower(), "error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.exception("plan_route_delete_failed", extra={"event": "plan_route_delete_failed", "plan_name": plan_name.lower(), "error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete plan: {str(e)}"
        )


@router.post("/{plan_name}/activate", response_model=Plan)
async def activate_plan(
    plan_name: str,
    current_user: dict = Depends(require_admin_user)
):
    """
    Activate a plan (Admin only)
    
    Makes the plan available for selection by property owners
    """
    plan = await PlanService.activate_plan(plan_name)
    if not plan:
        logger.warning("plan_route_activate_not_found", extra={"event": "plan_route_activate_not_found", "plan_name": plan_name.lower()})
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plan '{plan_name}' not found"
        )
    logger.info("plan_route_activate_success", extra={"event": "plan_route_activate_success", "plan_name": plan_name.lower()})
    return plan


@router.post("/{plan_name}/deactivate", response_model=Plan)
async def deactivate_plan(
    plan_name: str,
    current_user: dict = Depends(require_admin_user)
):
    """
    Deactivate a plan (Admin only)
    
    Prevents new subscriptions but doesn't affect existing users
    Cannot deactivate the 'free' plan
    """
    try:
        plan = await PlanService.deactivate_plan(plan_name)
        if not plan:
            logger.warning("plan_route_deactivate_not_found", extra={"event": "plan_route_deactivate_not_found", "plan_name": plan_name.lower()})
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Plan '{plan_name}' not found"
            )
        logger.info("plan_route_deactivate_success", extra={"event": "plan_route_deactivate_success", "plan_name": plan_name.lower()})
        return plan
    except ValueError as e:
        logger.warning("plan_route_deactivate_validation_failed", extra={"event": "plan_route_deactivate_validation_failed", "plan_name": plan_name.lower(), "error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/initialize")
async def initialize_default_plans(
    current_user: dict = Depends(require_admin_user)
):
    """
    Initialize default plans (Admin only)
    
    Creates free, pro, and premium plans if plans collection is empty
    Safe to call multiple times - only creates if none exist
    """
    try:
        created_count = await PlanService.create_default_plans()
        logger.info("plan_route_initialize_success", extra={"event": "plan_route_initialize_success", "created_count": created_count})
        return {
            "success": True,
            "message": f"Created {created_count} default plans" if created_count > 0 else "Plans already exist",
            "plans_created": created_count
        }
    except Exception as e:
        logger.exception("plan_route_initialize_failed", extra={"event": "plan_route_initialize_failed", "error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initialize plans: {str(e)}"
        )
