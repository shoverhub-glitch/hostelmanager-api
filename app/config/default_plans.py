# Default subscription plans (used only if DB is empty)

DEFAULT_PLANS = {
    "free": {
        "name": "free",
        "display_name": "Free Plan",
        "description": "Perfect for getting started",
        "properties": 1,
        "rooms": 20,
        "tenants": 80,
        "staff": 3,
        "periods": {"0": 0},
        "is_active": True,
        "sort_order": 0,
    },
    "pro": {
        "name": "pro",
        "display_name": "Pro Plan",
        "description": "For growing businesses",
        "properties": 3,
        "rooms": 50,
        "tenants": 150,
        "staff": 10,
        "periods": {"1": 49900, "12": 499900},
        "is_active": True,
        "sort_order": 1,
    },
    "premium": {
        "name": "premium",
        "display_name": "Premium Plan",
        "description": "For large operations",
        "properties": 10,
        "rooms": 100,
        "tenants": 300,
        "staff": 25,
        "periods": {"1": 99900, "12": 999900},
        "is_active": True,
        "sort_order": 2,
    },
}

def get_default_plan(plan_name: str):
    return DEFAULT_PLANS.get(plan_name.lower())

def get_all_default_plans():
    return list(DEFAULT_PLANS.values())
