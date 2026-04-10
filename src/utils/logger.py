"""Centralized logging configuration for the AI OS project.

Usage in any module:
    from utils.logger import get_logger
    logger = get_logger(__name__)
"""

import logging
import os


def get_logger(name: str = "ai_os") -> logging.Logger:
    """Return a logger that writes to both the log file and stdout.

    Calling this multiple times with the same name is safe — Python's
    logging framework deduplicates handlers automatically.
    """
    logger = logging.getLogger(name)

    # Only configure handlers once per logger instance
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── File handler ──────────────────────────────────────────
    # Import here to avoid circular dependency at module load time
    from config import settings  # noqa: PLC0415

    os.makedirs(os.path.dirname(settings.LOG_PATH), exist_ok=True)
    fh = logging.FileHandler(settings.LOG_PATH)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    # ── Console handler ───────────────────────────────────────
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger
