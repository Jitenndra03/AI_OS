"""
command_engine/validator.py
============================
Command execute karne se pehle check karo — safe hai ya dangerous?
"""

import re

# ── Dangerous command patterns ────────────────────────────────
DANGEROUS_PATTERNS = [
    r"\brmdir\b.*\/s",          # rmdir /s — folder delete
    r"\bdel\b.*\/[fqs]",        # del /f /q /s — force delete
    r"\bformat\b",              # format drive
    r"\btaskkill\b",            # process kill
    r"\breg\s+delete\b",        # registry delete
    r"\brd\b.*\/s",             # rd /s — same as rmdir /s
    r"\bcacls\b",               # permission change
    r"\bnetsh\b.*reset",        # network reset
    r"\bsfc\b",                 # system file checker (admin)
    r"\bbcdedit\b",             # boot config (very dangerous)
    r"\bdiskpart\b",            # disk partition
]

# ── Safe commands whitelist ───────────────────────────────────
SAFE_COMMANDS = {
    "dir", "cd", "mkdir", "echo", "type",
    "copy", "move", "tree", "systeminfo",
    "ipconfig", "ping", "tasklist",
    "findstr", "attrib", "cls", "help",
}


def is_dangerous(command: str) -> tuple[bool, str]:
    """
    Command dangerous hai?

    Returns:
        (True, "reason") ya (False, "")
    """
    cmd_lower = command.lower().strip()

    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, cmd_lower):
            return True, f"Pattern match: `{pattern}`"

    # Check kar — koi bhi safe command hai?
    first_word = cmd_lower.split()[0] if cmd_lower else ""
    if first_word in SAFE_COMMANDS:
        return False, ""

    return False, ""


def validate(command: str) -> dict:
    """
    Command validate karo.

    Returns:
        {
          'safe'   : True/False,
          'reason' : "why dangerous",
          'command': original command
        }
    """
    dangerous, reason = is_dangerous(command)
    return {
        "safe"   : not dangerous,
        "reason" : reason,
        "command": command,
    }
