from fastapi import APIRouter, Request, HTTPException
from app.database.mongodb import getCollection
from datetime import datetime, timedelta, timezone
import asyncio

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

@router.get("/stats")
async def get_dashboard_stats(request: Request, property_id: str):
    """Get aggregated dashboard statistics for a specific property"""
    property_ids = getattr(request.state, "property_ids", [])
    
    # Validate that the requested property_id belongs to the user
    if property_id not in property_ids:
        raise HTTPException(status_code=403, detail="You don't have access to this property")
    
    # Get collections
    tenants_col = getCollection("tenants")
    beds_col = getCollection("beds")
    payments_col = getCollection("payments")
    staff_col = getCollection("staff")
    
    # Get current month dates (UTC)
    today = datetime.now(timezone.utc)
    month_start_str = datetime(today.year, today.month, 1, tzinfo=timezone.utc).date().isoformat()
    # Get last day of month
    if today.month == 12:
        month_end = datetime(today.year + 1, 1, 1, tzinfo=timezone.utc).date() - timedelta(days=1)
    else:
        month_end = datetime(today.year, today.month + 1, 1, tzinfo=timezone.utc).date() - timedelta(days=1)
    month_end_str = month_end.isoformat()

    # Today's range for check-ins
    today_str = today.date().isoformat()

    tenants_pipeline = [
        {"$match": {"propertyId": property_id, "archived": {"$ne": True}, "isDeleted": {"$ne": True}}},
        {"$addFields": {
            "joinDateKey": {
                "$substrBytes": [
                    {"$toString": {"$ifNull": ["$joinDate", ""]}},
                    0,
                    10
                ]
            }
        }},
        {"$group": {
            "_id": None,
            "active": {"$sum": {"$cond": [{"$ne": ["$tenantStatus", "vacated"]}, 1, 0]}},
            "vacated": {"$sum": {"$cond": [{"$eq": ["$tenantStatus", "vacated"]}, 1, 0]}},
            "checkInsToday": {"$sum": {"$cond": [{"$eq": ["$joinDateKey", today_str]}, 1, 0]}}
        }}
    ]

    beds_pipeline = [
        {"$match": {"propertyId": property_id, "isDeleted": {"$ne": True}}},
        {"$addFields": {
            "statusKey": {"$toLower": {"$toString": {"$ifNull": ["$status", ""]}}}
        }},
        {"$group": {
            "_id": None,
            "total": {"$sum": 1},
            "occupied": {"$sum": {"$cond": [{"$eq": ["$statusKey", "occupied"]}, 1, 0]}}
        }}
    ]

    revenue_pipeline = [
        {"$match": {"propertyId": property_id, "isDeleted": {"$ne": True}}},
        {"$addFields": {
            "amountNumeric": {
                "$convert": {
                    "input": {
                        "$replaceAll": {
                            "input": {
                                "$replaceAll": {
                                    "input": {
                                        "$replaceAll": {
                                            "input": {"$toString": {"$ifNull": ["$amount", "0"]}},
                                            "find": "₹",
                                            "replacement": ""
                                        }
                                    },
                                    "find": ",",
                                    "replacement": ""
                                }
                            },
                            "find": " ",
                            "replacement": ""
                        }
                    },
                    "to": "double",
                    "onError": 0,
                    "onNull": 0
                }
            },
            "paidDateKey": {
                "$let": {
                    "vars": {"pd": "$paidDate"},
                    "in": {
                        "$cond": [
                            {"$in": [{"$type": "$$pd"}, ["date", "timestamp"]]},
                            {"$dateToString": {"format": "%Y-%m-%d", "date": "$$pd"}},
                            {"$substrBytes": [{"$toString": {"$ifNull": ["$$pd", ""]}}, 0, 10]}
                        ]
                    }
                }
            },
            "statusKey": {
                "$toLower": {"$toString": {"$ifNull": ["$status", ""]}}
            }
        }},
        {"$group": {
            "_id": None,
            "paidThisMonth": {"$sum": {"$cond": [
                {
                    "$and": [
                        {"$eq": ["$statusKey", "paid"]},
                        {"$gte": ["$paidDateKey", month_start_str]},
                        {"$lte": ["$paidDateKey", month_end_str]}
                    ]
                },
                "$amountNumeric", 0
            ]}},
            "pendingCount": {"$sum": {"$cond": [{"$eq": ["$statusKey", "due"]}, 1, 0]}},
            "pendingAmount": {"$sum": {"$cond": [{"$eq": ["$statusKey", "due"]}, "$amountNumeric", 0]}}
        }}
    ]

    staff_pipeline = [
        {
            "$match": {
                "propertyId": property_id,
                "archived": {"$ne": True},
                "isDeleted": {"$ne": True},
            }
        },
        {"$addFields": {
            "statusKey": {"$toLower": {"$toString": {"$ifNull": ["$status", ""]}}}
        }},
        {"$group": {
            "_id": None,
            "total": {"$sum": 1},
            "available": {"$sum": {"$cond": [{"$in": ["$statusKey", ["active", "available"]]}, 1, 0]}}
        }}
    ]

    tenants_result, beds_result, revenue_result, staff_result = await asyncio.gather(
        tenants_col.aggregate(tenants_pipeline).to_list(1),
        beds_col.aggregate(beds_pipeline).to_list(1),
        payments_col.aggregate(revenue_pipeline).to_list(1),
        staff_col.aggregate(staff_pipeline).to_list(1),
    )

    t_stats = tenants_result[0] if tenants_result else {}
    b_stats = beds_result[0] if beds_result else {}
    r_stats = revenue_result[0] if revenue_result else {}
    s_stats = staff_result[0] if staff_result else {}

    active_tenants = t_stats.get("active", 0)
    vacated_tenants = t_stats.get("vacated", 0)
    total_tenants = active_tenants + vacated_tenants
    check_ins_today = t_stats.get("checkInsToday", 0)

    total_beds = b_stats.get("total", 0)
    occupied_beds = b_stats.get("occupied", 0)
    available_beds = max(0, total_beds - occupied_beds)
    occupancy_rate = (occupied_beds / total_beds * 100) if total_beds > 0 else 0

    monthly_revenue = r_stats.get("paidThisMonth", 0.0)
    pending_count = r_stats.get("pendingCount", 0)
    pending_amount = r_stats.get("pendingAmount", 0.0)

    total_staff = s_stats.get("total", 0)
    available_staff = s_stats.get("available", 0)

    return {
        "data": {
            "totalTenants": total_tenants,
            "activeTenants": active_tenants,
            "vacatedTenants": vacated_tenants,
            "totalBeds": total_beds,
            "occupiedBeds": occupied_beds,
            "availableBeds": available_beds,
            "occupancyRate": round(occupancy_rate, 2),
            "monthlyRevenue": int(monthly_revenue),
            "monthlyRevenueFormatted": f"₹{monthly_revenue:,.0f}",
            "pendingPayments": pending_count,
            "duePaymentAmount": int(pending_amount),
            "duePaymentAmountFormatted": f"₹{pending_amount:,.0f}",
            "paidThisMonth": int(monthly_revenue),
            "paidThisMonthFormatted": f"₹{monthly_revenue:,.0f}",
            "checkInsToday": check_ins_today,
            "checkOutsToday": 0,
            "upcomingCheckIns": 0,
            "totalStaff": total_staff,
            "availableStaff": available_staff,
            "maintenanceAlerts": 0,
            "urgentAlerts": 0,
        }
    }
