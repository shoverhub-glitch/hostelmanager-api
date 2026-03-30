from typing import Any
from bson import ObjectId


def build_owner_query(owner_id: str) -> dict:
    filters = [
        {"ownerIds": owner_id},
        {"ownerId": owner_id},
    ]
    try:
        filters.append({"ownerId": ObjectId(owner_id)})
    except Exception:
        pass
    return {"$or": filters}


def normalize_property_owners(doc: dict[str, Any], fallback_owner_id: str | None = None) -> dict[str, Any]:
    owner_ids: list[str] = []

    raw_owner_ids = doc.get("ownerIds")
    if isinstance(raw_owner_ids, list):
        owner_ids.extend(str(value) for value in raw_owner_ids if value)

    raw_owner_id = doc.get("ownerId")
    if raw_owner_id:
        owner_ids.append(str(raw_owner_id))

    if fallback_owner_id:
        owner_ids.append(str(fallback_owner_id))

    deduped_owner_ids: list[str] = []
    seen: set[str] = set()
    for oid in owner_ids:
        if oid and oid not in seen:
            deduped_owner_ids.append(oid)
            seen.add(oid)

    doc["ownerIds"] = deduped_owner_ids
    doc["ownerId"] = deduped_owner_ids[0] if deduped_owner_ids else None
    return doc


def property_belongs_to_owner(property_doc: dict[str, Any], owner_id: str) -> bool:
    normalized = normalize_property_owners(dict(property_doc))
    return owner_id in normalized.get("ownerIds", [])
