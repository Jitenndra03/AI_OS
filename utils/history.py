"""
utils/history.py
=================
Command history JSON file mein save aur load karo.
"""

import json
import os
from datetime import datetime

HISTORY_FILE = os.path.join(os.path.dirname(__file__), "..", "history.json")
MAX_HISTORY  = 100


def load() -> list[dict]:
    """history.json se history load karo."""
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except:
        pass
    return []


def save(history: list[dict]) -> None:
    """history.json mein save karo (max 100 entries)."""
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history[-MAX_HISTORY:], f, ensure_ascii=False, indent=2)
    except:
        pass


def add_entry(history: list, user_input: str, command: str,
              success: bool, mode: str = "text") -> list:
    """
    Ek naya entry add karo aur save karo.
    mode = 'text' ya 'voice'
    """
    entry = {
        "input"  : user_input,
        "command": command,
        "success": success,
        "mode"   : mode,
        "time"   : datetime.now().strftime("%H:%M:%S"),
        "date"   : datetime.now().strftime("%Y-%m-%d"),
    }
    history.append(entry)
    save(history)
    return history
