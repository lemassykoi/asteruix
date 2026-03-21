"""Audit logging subsystem — writes to both SQLite and a log file."""

import json
import logging
import os
from datetime import datetime, timezone

from app.db import get_db

LOG_DIR = "/var/log/asterisk-webui"
LOG_FILE = os.path.join(LOG_DIR, "audit.log")

_file_logger = None


def _get_file_logger() -> logging.Logger:
    """Lazily initialise the file-based audit logger."""
    global _file_logger
    if _file_logger is None:
        os.makedirs(LOG_DIR, exist_ok=True)
        _file_logger = logging.getLogger("asterisk_webui.audit")
        _file_logger.setLevel(logging.INFO)
        if not _file_logger.handlers:
            handler = logging.FileHandler(LOG_FILE)
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
            )
            _file_logger.addHandler(handler)
    return _file_logger


def log_action(
    action: str,
    target: str = "",
    before: dict | list | None = None,
    after: dict | list | None = None,
    username: str = "system",
    status: str = "ok",
):
    """Record an auditable action in the DB and the file log."""
    before_json = json.dumps(before, default=str) if before is not None else None
    after_json = json.dumps(after, default=str) if after is not None else None

    db = get_db()
    db.execute(
        "INSERT INTO audit_log (username, action, target, before_json, after_json, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (username, action, target, before_json, after_json, status),
    )
    db.commit()

    _get_file_logger().info(
        "user=%s action=%s target=%s status=%s", username, action, target, status
    )
