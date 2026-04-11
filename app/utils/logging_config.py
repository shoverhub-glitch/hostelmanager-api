import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from typing import Any


class JsonLogFormatter(logging.Formatter):
    """Format logs as compact JSON for centralized ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include structured fields passed via `extra={...}`.
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in {
                "args",
                "asctime",
                "created",
                "exc_info",
                "exc_text",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "message",
                "msg",
                "name",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "thread",
                "threadName",
            }:
                continue
            payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=True)


class ExactLevelFilter(logging.Filter):
    """Allow only records for an exact log level."""

    def __init__(self, level: int) -> None:
        super().__init__()
        self.level = level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno == self.level


def _create_rotating_handler(
    log_path: str,
    formatter: logging.Formatter,
    backup_count: int,
    level: int,
    exact_level: int | None = None,
) -> logging.Handler | None:
    """Create a time-based rotating file handler without crashing app startup on permission issues."""
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            log_path,
            when="midnight",
            interval=1,
            backupCount=backup_count,
            encoding="utf-8",
        )
    except OSError as exc:
        print(
            f"logging_file_handler_init_failed path={log_path} error={exc}",
            file=sys.stderr,
        )
        return None

    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    if exact_level is not None:
        file_handler.addFilter(ExactLevelFilter(exact_level))
    return file_handler


def setup_logging() -> None:
    """Configure root logging to file only."""
    from app.config.settings import LOG_DIR, LOG_FILE_BACKUP_COUNT

    formatter = JsonLogFormatter()
    handlers: list[logging.Handler] = []

    app_log_path = os.path.join(LOG_DIR, "warning.log")
    error_log_path = os.path.join(LOG_DIR, "error.log")
    combined_log_path = os.path.join(LOG_DIR, "app.log")

    app_file_handler = _create_rotating_handler(
        log_path=app_log_path,
        formatter=formatter,
        backup_count=LOG_FILE_BACKUP_COUNT,
        level=logging.WARNING,
        exact_level=logging.WARNING,
    )
    if app_file_handler is not None:
        handlers.append(app_file_handler)

    error_file_handler = _create_rotating_handler(
        log_path=error_log_path,
        formatter=formatter,
        backup_count=LOG_FILE_BACKUP_COUNT,
        level=logging.ERROR,
    )
    if error_file_handler is not None:
        handlers.append(error_file_handler)

    combined_file_handler = _create_rotating_handler(
        log_path=combined_log_path,
        formatter=formatter,
        backup_count=LOG_FILE_BACKUP_COUNT,
        level=logging.DEBUG,
    )
    if combined_file_handler is not None:
        handlers.append(combined_file_handler)

    root_logger = logging.getLogger()
    root_logger.handlers = handlers
    root_logger.setLevel(logging.INFO)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "gunicorn", "gunicorn.error", "apscheduler"):
        framework_logger = logging.getLogger(logger_name)
        framework_logger.handlers = handlers
        framework_logger.setLevel(logging.INFO)
        framework_logger.propagate = False
