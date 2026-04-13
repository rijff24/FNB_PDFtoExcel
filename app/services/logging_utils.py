import json
import logging
import os
from typing import Any, Dict, Optional


LOGGER_NAME = "fnb_pdf_to_excel"


def _configure_logger() -> logging.Logger:
    """Configure a JSON-to-stdout logger for the app."""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        # Logger already configured.
        logger.setLevel(level)
        return logger

    handler = logging.StreamHandler()
    # We emit pre-encoded JSON as the message; keep format minimal.
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


logger = _configure_logger()


def log_event(
    level: int,
    event: str,
    *,
    path: Optional[str] = None,
    user_email: Optional[str] = None,
    uid: Optional[str] = None,
    details: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Emit a structured JSON log line."""
    payload: Dict[str, Any] = {
        "event": event,
    }
    if path is not None:
        payload["path"] = path
    if user_email is not None:
        payload["user_email"] = user_email
    if uid is not None:
        payload["uid"] = uid
    if details is not None:
        payload["details"] = details
    if extra:
        payload.update(extra)

    try:
        message = json.dumps(payload, default=str)
    except TypeError:
        # Fallback if something isn't serializable.
        message = json.dumps({"event": event, "details": "logging serialization failed"})

    logger.log(level, message)

    # Best-effort operational error persistence for admin diagnostics.
    if level >= logging.ERROR and os.getenv("ADMIN_ERROR_TRACKING_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        try:
            from app.services.admin_store import record_app_error

            record_app_error(payload)
        except Exception:  # noqa: BLE001
            # Never let observability writes break primary flows.
            pass

