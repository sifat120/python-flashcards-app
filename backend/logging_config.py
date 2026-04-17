"""Centralised logging setup.

Called once from `backend.app` at import time. Every other module just does:

    import logging
    logger = logging.getLogger(__name__)

…and gets the formatting / level configured here. Set `LOG_LEVEL` (e.g. DEBUG,
INFO, WARNING) to change verbosity at runtime — Embr lets you set this via
`embr variables set --key LOG_LEVEL --value DEBUG`.
"""

from __future__ import annotations

import logging
import os
import sys

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s — %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S"

_configured = False


def setup_logging() -> None:
    """Configure root logging once. Safe to call multiple times."""
    global _configured
    if _configured:
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Quiet a few noisy third-party loggers unless explicitly debugging.
    for noisy in ("urllib3", "sqlalchemy.engine", "asyncio"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    _configured = True
