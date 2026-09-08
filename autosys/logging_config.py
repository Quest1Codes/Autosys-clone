"""
Structured logging configuration for AutoSys.

Configures loguru with either text (default) or JSON format.
JSON format includes: timestamp, level, component, message, correlation_id.

Configuration
-------------
    AUTOSYS_LOG_LEVEL   — default: INFO
    AUTOSYS_LOG_FORMAT  — "text" (default) or "json"
    AUTOSYS_LOG_FILE    — optional log file path

Usage
-----
    from autosys.logging_config import setup_logging, get_correlation_id

    setup_logging()  # call once at startup

    # In request handlers:
    from autosys.logging_config import set_correlation_id
    set_correlation_id("req-12345")
"""
from __future__ import annotations

import os
import sys
import uuid
from contextvars import ContextVar
from typing import Any

from loguru import logger

# Context variable for request tracing
_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    """Return the current correlation ID (empty string if not set)."""
    return _correlation_id.get()


def set_correlation_id(cid: str | None = None) -> str:
    """Set the correlation ID. Auto-generates a UUID if none provided."""
    cid = cid or str(uuid.uuid4())[:8]
    _correlation_id.set(cid)
    return cid


def _json_serializer(record: dict) -> str:
    """Serialize a log record as a JSON line."""
    import json
    from datetime import datetime

    payload: dict[str, Any] = {
        "timestamp": record["time"].isoformat(),
        "level": record["level"].name,
        "message": record["message"],
        "correlation_id": get_correlation_id(),
    }

    # Include extra fields from the record
    extra = record.get("extra", {})
    for key in ("component", "job_name", "event_type", "run_id"):
        if key in extra:
            payload[key] = extra[key]

    # Include exception info if present
    if record.get("exception"):
        exc = record["exception"]
        payload["exception"] = {
            "type": type(exc.value).__name__,
            "message": str(exc.value),
        }

    return json.dumps(payload, default=str)


def setup_logging() -> None:
    """
    Configure loguru logging based on environment variables.

    Reads:
        AUTOSYS_LOG_LEVEL   — default: INFO
        AUTOSYS_LOG_FORMAT  — "text" (default) or "json"
        AUTOSYS_LOG_FILE    — optional file path
    """
    level = os.environ.get("AUTOSYS_LOG_LEVEL", "INFO").upper()
    fmt = os.environ.get("AUTOSYS_LOG_FORMAT", "text").lower()
    log_file = os.environ.get("AUTOSYS_LOG_FILE")

    # Remove default handler
    logger.remove()

    if fmt == "json":
        def _json_sink(message):
            print(_json_serializer(message.record), file=sys.stderr)

        logger.add(
            _json_sink,
            level=level,
            format="{message}",
        )
    else:
        logger.add(
            sys.stderr,
            level=level,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                "{message}"
            ),
        )

    if log_file:
        if fmt == "json":
            def _json_file_sink(message):
                with open(log_file, "a") as f:
                    f.write(_json_serializer(message.record) + "\n")

            logger.add(
                _json_file_sink,
                level=level,
                format="{message}",
            )
        else:
            logger.add(
                log_file,
                level=level,
                format=(
                    "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
                    "{level: <8} | "
                    "{name}:{function}:{line} | "
                    "{message}"
                ),
                rotation="50 MB",
                retention="10 days",
            )
