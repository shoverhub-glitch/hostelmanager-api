from app.utils.logging_config import setup_logging

setup_logging()

log_config = {
    "version": 1,
    "disable_existing_loggers": True,
    "formatters": {
        "default": {
            "()": "logging.NullHandler",
        },
    },
    "handlers": {
        "null": {
            "class": "logging.NullHandler",
        },
    },
    "loggers": {
        "uvicorn": {"handlers": ["null"], "level": "WARNING", "propagate": False},
        "uvicorn.error": {"handlers": ["null"], "level": "WARNING", "propagate": False},
        "uvicorn.access": {"handlers": ["null"], "level": "WARNING", "propagate": False},
        "uvicorn.launcher": {"handlers": ["null"], "level": "WARNING", "propagate": False},
        "apscheduler": {"handlers": ["null"], "level": "WARNING", "propagate": False},
    },
}

access_log_format = '%(message)s'
