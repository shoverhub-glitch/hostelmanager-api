"""Configuration helper functions for environment variable parsing."""

import os


def require(name: str, description: str) -> str:
    """
    Read a required environment variable.
    Raises ImportError if not set, preventing the app from starting.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        raise ImportError(
            f"{name} is not set. {description} "
            f"Configure it in your environment or .env file before starting the app."
        )
    return value


def optional(name: str, default: str = "") -> str:
    """Read an optional environment variable with a fallback default."""
    return os.environ.get(name, default)


def to_bool(name: str, default: bool = False) -> bool:
    """Convert environment variable to boolean."""
    raw = os.environ.get(name, "")
    if not raw:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "y")


def to_int(name: str, default: int = 0) -> int:
    """Convert environment variable to integer with error handling."""
    try:
        value = os.environ.get(name)
        return int(value) if value else default
    except (ValueError, TypeError):
        return default
