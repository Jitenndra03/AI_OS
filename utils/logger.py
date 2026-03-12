"""
utils/logger.py
================
Sab events logs/ai_os.log mein save karo.
"""

import logging
import os

LOG_DIR  = os.path.join(os.path.dirname(__file__), "..", "logs")
LOG_FILE = os.path.join(LOG_DIR, "ai_os.log")

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ]
)

logger = logging.getLogger("AI_OS")


def log_command(user_input: str, intent: str, command: str,
                success: bool, mode: str = "text") -> None:
    status = "SUCCESS" if success else "FAILED"
    logger.info(f"[{mode.upper()}] [{status}] input='{user_input}' intent={intent} cmd='{command}'")


def log_error(message: str) -> None:
    logger.error(message)


def log_alert(message: str) -> None:
    logger.warning(f"[ALERT] {message}")


def log_info(message: str) -> None:
    logger.info(message)
