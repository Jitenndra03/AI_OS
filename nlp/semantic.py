"""
nlp/semantic.py
================
Keywords se user ka INTENT samjho — pure Python + NLTK, koi API nahi!

Intent categories:
  CREATE_FOLDER, CREATE_FILE, LIST_FILES, SHOW_CONTENT,
  DELETE_FILE, DELETE_FOLDER, RENAME, COPY, MOVE,
  WRITE_FILE, SEARCH_FILE, SEARCH_TEXT,
  SYSTEM_INFO, CPU_STATUS, DISK_STATUS, NETWORK_INFO,
  SHOW_PROCESSES, TREE_VIEW, HISTORY, HELP, UNKNOWN
"""

from __future__ import annotations
import re
from nlp.preprocessor import preprocess

# ══════════════════════════════════════════════════════════════
# INTENT MAP — keyword groups → intent
# Hinglish + Hindi + English teeno cover kiya hai
# ══════════════════════════════════════════════════════════════
INTENT_MAP: dict[str, list[str]] = {

    "CREATE_FOLDER": [
        "folder", "directory", "dir",
        "bna", "bnao", "banao", "banana", "create", "make", "mkdir",
        "폴더",   # edge case
    ],

    "CREATE_FILE": [
        "file", "txt", "html", "py", "js", "json", "csv", "md",
        "bna", "bnao", "banao", "create", "touch", "new", "naya", "nayi",
    ],

    "LIST_FILES": [
        "list", "dikhao", "dikha", "show", "dekho", "dekh",
        "files", "folders", "sab", "kya", "available",
        "ls", "dir",
    ],

    "SHOW_CONTENT": [
        "content", "andar", "padh", "padho", "read",
        "open", "kholo", "dekho", "show", "dikhao",
        "cat", "type",
    ],

    "DELETE_FILE": [
        "delete", "remove", "hata", "hatao", "mita", "mitao",
        "del", "rm", "nikal", "nikalo",
    ],

    "DELETE_FOLDER": [
        "folder", "directory",
        "delete", "remove", "hata", "hatao", "mita", "mitao",
        "rmdir", "nikal", "nikalo",
    ],

    "RENAME": [
        "rename", "naam", "badlo", "badal", "change",
        "mv", "move",
    ],

    "COPY": [
        "copy", "cop", "duplicate", "backup",
        "cp",
    ],

    "MOVE": [
        "move", "le", "jao", "shift", "transfer",
        "mv",
    ],

    "WRITE_FILE": [
        "likho", "likh", "write", "add", "daalo", "dalo",
        "save", "store", "text", "content",
        "echo",
    ],

    "SEARCH_FILE": [
        "dhundo", "dhundh", "search", "find", "locate", "kahan",
        "where",
    ],

    "SEARCH_TEXT": [
        "grep", "findstr", "text", "andar", "inside",
        "dhundo", "search", "find",
    ],

    "SYSTEM_INFO": [
        "system", "info", "information", "detail",
        "systeminfo", "os", "version",
    ],

    "CPU_STATUS": [
        "cpu", "processor", "usage", "percent",
        "kitna", "load", "performance",
    ],

    "RAM_STATUS": [
        "ram", "memory", "mem", "storage",
        "kitna", "free", "used",
    ],

    "DISK_STATUS": [
        "disk", "drive", "space", "storage",
        "c", "d", "free", "kitna",
    ],

    "NETWORK_INFO": [
        "network", "internet", "ip", "ping", "connection",
        "ipconfig", "wifi", "net",
    ],

    "SHOW_PROCESSES": [
        "process", "processes", "task", "tasks",
        "chal", "running", "tasklist", "ps",
    ],

    "TREE_VIEW": [
        "tree", "structure", "hierarchy",
        "folder", "poora", "pura",
    ],

    "HISTORY": [
        "history", "purane", "pehle", "commands", "log",
    ],

    "HELP": [
        "help", "madad", "examples", "kya", "kaise",
    ],
}

# ── Dangerous intent markers ───────────────────────────────────
DANGEROUS_KEYWORDS = {
    "delete", "del", "remove", "rm", "rmdir",
    "hata", "hatao", "mita", "mitao",
    "format", "wipe", "taskkill", "kill",
}

# ── Blacklisted words — ye kabhi naam nahi honge ─────────────
_BLACKLIST = {
    "ka", "ki", "ke", "ek", "do", "the", "a", "an", "in", "on",
    "folder", "file", "directory", "naam", "name",
    "bna", "bnao", "banao", "create", "make", "karo", "kar",
    "dena", "mujhe", "mere", "please", "zara",
    "desktop", "downloads", "documents", "pe", "par", "mein", "me",
}

# ── Known path locations ───────────────────────────────────────
_PATH_MAP = {
    "desktop"   : r"C:\Users\{user}\Desktop",
    "downloads" : r"C:\Users\{user}\Downloads",
    "documents" : r"C:\Users\{user}\Documents",
    "pictures"  : r"C:\Users\{user}\Pictures",
    "music"     : r"C:\Users\{user}\Music",
    "videos"    : r"C:\Users\{user}\Videos",
    "d drive"   : r"D:\\",
    "c drive"   : r"C:\\",
}

# ── Argument extraction patterns ──────────────────────────────
_ARG_PATTERNS = [
    r'"([^"]+)"',
    r"'([^']+)'",
    r"(\S+\.(?:txt|html|py|js|json|csv|md|log|xml|yaml|yml))",
    r"(?:jiska|jis ka)\s+naam\s+(?:ho|hai|hoga)?\s*['\"]?(\w+)['\"]?",
    r"(\w+)\s+naam\s+(?:ka|ki|ke)\s+(?:folder|file)",
    r"naam\s+(?:ka|ki|ke|rakho|ho)?\s*['\"]?(\w+)['\"]?",
    r"(\w+)\s+naam\s+(?:ka|ki|ke|se|wala)",
    r"(\w+)\s+(?:folder|file|directory)\s+(?:bna|bnao|banao|create|bana)",
    r"(?:bna|bnao|banao|create|make)\s+(?:ek\s+)?(?:folder|file)?\s*['\"]?(\w+)['\"]?",
    r"(?:folder|file)\s+(?:ka naam\s+)?['\"]?(\w+)['\"]?",
]


def extract_path(text: str) -> str | None:
    """Desktop pe, Downloads mein jaisi location detect karo."""
    import os
    username = os.environ.get("USERNAME", os.environ.get("USER", "User"))
    text_low = text.lower()
    for keyword, path_template in _PATH_MAP.items():
        if keyword in text_low:
            return path_template.replace("{user}", username)
    return None


def extract_argument(text: str) -> str | None:
    """User ke input se file/folder naam nikalo."""
    for pattern in _ARG_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            arg = match.group(1).strip()
            if arg.lower() not in _BLACKLIST and len(arg) > 1:
                return arg
    return None


def score_intent(keywords: list[str], intent_keywords: list[str]) -> int:
    """
    Keywords aur intent keywords ka overlap score nikalo.
    """
    kw_set = set(keywords)
    return sum(1 for ik in intent_keywords if ik in kw_set)


def analyze(raw_text: str) -> dict:
    """
    Raw text se intent detect karo.

    Returns:
        {
          'intent'     : "CREATE_FOLDER",
          'argument'   : "projects",       # file/folder naam
          'is_dangerous': False,
          'confidence' : 0.85,
          'keywords'   : [...],
          'original'   : raw_text
        }
    """
    processed = preprocess(raw_text)
    keywords  = processed["keywords"]
    normalized= processed["normalized"]

    # ── Score har intent ko ───────────────────────────────────
    scores: dict[str, int] = {}
    for intent, intent_kws in INTENT_MAP.items():
        s = score_intent(keywords, intent_kws)
        if s > 0:
            scores[intent] = s

    # ── Best intent chuno ─────────────────────────────────────
    if not scores:
        best_intent = "UNKNOWN"
        confidence  = 0.0
    else:
        best_intent = max(scores, key=scores.get)
        total       = sum(scores.values())
        confidence  = round(scores[best_intent] / total, 2) if total else 0.0

    # ── CREATE_FOLDER vs CREATE_FILE disambiguate ─────────────
    # Agar dono score same hain, extension se decide karo
    if best_intent == "CREATE_FOLDER":
        # Agar file extension milti hai to CREATE_FILE
        if re.search(r"\.\w{1,5}\b", normalized):
            best_intent = "CREATE_FILE"

    # ── Argument extract karo ─────────────────────────────────
    argument = extract_argument(raw_text)

    # ── Path extract karo (Desktop/Downloads etc) ─────────────
    target_path = extract_path(raw_text)

    # ── Dangerous check ───────────────────────────────────────
    is_dangerous = bool(set(keywords) & DANGEROUS_KEYWORDS)

    return {
        "intent"      : best_intent,
        "argument"    : argument,
        "target_path" : target_path,
        "is_dangerous": is_dangerous,
        "confidence"  : confidence,
        "keywords"    : keywords,
        "original"    : raw_text,
        "scores"      : scores,
    }
