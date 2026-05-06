from typing import Any
from bson import ObjectId


def build_owner_query(owner_id: str) -> dict:
    """Build a query to filter documents by ownerId."""
    filters = [
        {"ownerId": owner_id},
    ]
    try:
        filters.append({"ownerId": ObjectId(owner_id)})
    except Exception:
        pass
    return {"$or": filters}


def property_belongs_to_owner(property_doc: dict[str, Any], owner_id: str) -> bool:
    """Check if a property belongs to a specific owner."""
    return str(property_doc.get("ownerId")) == str(owner_id)
