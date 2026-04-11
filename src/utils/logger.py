"""logger.py — Centralized logging utility for AI OS.

Ensures consistent log formatting across all threads and modules.
"""

import logging
import sys
from pathlib import Path
from config import settings

def setup_logger(name: str, log_file: Path | None = None, level=logging.INFO) -> logging.Logger:
    """Set up a logger with a file handler and a stream handler."""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid duplicate handlers
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(name)-15s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # File handler (if path provided)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file)
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    return logger

# Common loggers
system_logger = setup_logger("AI_OS", settings.MAIN_LOG)
