"""
logger.py — Centralised logging setup for NeatPDF.

Call ``setup_logging()`` once at application startup (in main.py).
Every module then does::

    import logging
    log = logging.getLogger(__name__)
"""

import logging
import logging.handlers

from config import LOG_DATE_FORMAT, LOG_DIR, LOG_FORMAT, LOG_LEVEL


def setup_logging() -> None:
    """Configure root logger with console and rotating-file handlers."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    log_file = LOG_DIR / "neatpdf.log"

    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL, logging.DEBUG))

    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)

    # Rotating file handler — 5 MB × 3 backups
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    root.addHandler(console_handler)
    root.addHandler(file_handler)

    logging.getLogger(__name__).info(
        "Logging initialised → %s", log_file
    )
