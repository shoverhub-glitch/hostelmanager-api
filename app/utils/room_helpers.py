import logging


logger = logging.getLogger(__name__)


def validate_room_data(room_data):
    """Validate room data dict. Raises ValueError on invalid input."""
    if not room_data:
        logger.warning("room_validation_empty_payload", extra={"event": "room_validation_empty_payload"})
        raise ValueError("Room data cannot be empty")
    if not room_data.get("room_number"):
        logger.warning("room_validation_missing_room_number", extra={"event": "room_validation_missing_room_number"})
        raise ValueError("room_number is required")
    capacity = room_data.get("capacity")
    if capacity is not None and (not isinstance(capacity, int) or capacity < 1):
        logger.warning("room_validation_invalid_capacity", extra={"event": "room_validation_invalid_capacity", "capacity": capacity})
        raise ValueError("capacity must be a positive integer")
