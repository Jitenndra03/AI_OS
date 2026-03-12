"""
command_engine/executor.py
===========================
Intent + Argument → Windows Command → Execute → Result

Intent se exact command banao aur chalao.
Koi AI API nahi — pure Python logic!
"""

import os
import subprocess
from command_engine.validator import validate

# ── Working directory ─────────────────────────────────────────
WORK_DIR = r"D:\AI OS"

# ══════════════════════════════════════════════════════════════
# INTENT → COMMAND BUILDER
# ══════════════════════════════════════════════════════════════

def build_command(intent: str, argument: str | None, original: str, target_dir: str | None = None) -> tuple[str | None, str]:
    """
    Intent aur argument se Windows command banao.

    Returns:
        (command_string, explanation_hindi)
    """
    arg = argument or ""

    builders = {

        "CREATE_FOLDER": lambda: (
            f'mkdir "{arg}"' if arg else None,
            f'"{arg}" naam ka folder banega' if arg else "Folder ka naam nahi mila!"
        ),

        "CREATE_FILE": lambda: (
            f'echo. > "{arg}"' if arg else None,
            f'"{arg}" naam ki khali file banegi' if arg else "File ka naam nahi mila!"
        ),

        "LIST_FILES": lambda: (
            "dir",
            "Sab files aur folders dikhenge"
        ),

        "SHOW_CONTENT": lambda: (
            f'type "{arg}"' if arg else None,
            f'"{arg}" ka content dikhega' if arg else "File ka naam nahi mila!"
        ),

        "DELETE_FILE": lambda: (
    f'del /f /q "{os.path.join(WORK_DIR, arg)}"' if arg else None,
    f'"{arg}" file delete hogi' if arg else "File ka naam nahi mila!"
),

"DELETE_FOLDER": lambda: (
    f'rmdir /s /q "{os.path.join(WORK_DIR, arg)}"' if arg else None,
    f'"{arg}" folder delete hoga' if arg else "Folder ka naam nahi mila!"
),

        "RENAME": lambda: _build_rename(original, arg),

        "COPY": lambda: _build_copy(original, arg),

        "MOVE": lambda: (
            f'move "{arg}" .' if arg else None,
            f'"{arg}" move hoga' if arg else "File ka naam nahi mila!"
        ),

        "WRITE_FILE": lambda: _build_write(original, arg),

        "SEARCH_FILE": lambda: (
            f'dir /s /b "*{arg}*"' if arg else "dir /s /b",
            f'"{arg}" naam ki files dhundhengi' if arg else "Sab files dhundhengi"
        ),

        "SEARCH_TEXT": lambda: (
            f'findstr /s /i "{arg}" *.*' if arg else None,
            f'"{arg}" text files mein dhundha jaega' if arg else "Text nahi mila!"
        ),

        "SYSTEM_INFO": lambda: (
            "systeminfo",
            "System ki poori jankari dikhegi"
        ),

        "CPU_STATUS": lambda: (
            'wmic cpu get loadpercentage',
            "CPU ka current usage dikhega"
        ),

        "RAM_STATUS": lambda: (
            'wmic OS get FreePhysicalMemory,TotalVisibleMemorySize /Value',
            "RAM ki jankari dikhegi"
        ),

        "DISK_STATUS": lambda: (
            'wmic logicaldisk get size,freespace,caption',
            "Disk space ki jankari dikhegi"
        ),

        "NETWORK_INFO": lambda: (
            "ipconfig",
            "Network aur IP ki jankari dikhegi"
        ),

        "SHOW_PROCESSES": lambda: (
            "tasklist",
            "Sab running processes dikhenge"
        ),

        "TREE_VIEW": lambda: (
            "tree /f",
            "Folder ka poora structure dikhega"
        ),
    }

    builder = builders.get(intent)
    if builder:
        return builder()
    return None, "Ye command abhi supported nahi hai!"


def _build_rename(original: str, arg: str) -> tuple[str | None, str]:
    """'old.txt ko new.txt mein rename karo' → move old.txt new.txt"""
    import re
    # "X ko Y mein rename" pattern
    m = re.search(r"(\S+)\s+(?:ko|se)\s+(\S+)\s+(?:mein|me|rename|badlo)", original, re.I)
    if m:
        return f'move "{m.group(1)}" "{m.group(2)}"', f'"{m.group(1)}" ka naam "{m.group(2)}" ho jaega'
    return f'move "{arg}" newname' if arg else None, "Rename ke liye dono naam batao!"


def _build_copy(original: str, arg: str) -> tuple[str | None, str]:
    """'X ko Y mein copy karo' → copy X Y"""
    import re
    m = re.search(r"(\S+)\s+(?:ko|se)\s+(\S+)\s+(?:mein|me|copy)", original, re.I)
    if m:
        return f'copy "{m.group(1)}" "{m.group(2)}"', f'"{m.group(1)}" copy hokar "{m.group(2)}" banega'
    return f'copy "{arg}" "{arg}.bak"' if arg else None, "Copy ke liye source batao!"


def _build_write(original: str, arg: str) -> tuple[str | None, str]:
    """'hello.txt mein Namaste likho' → echo Namaste >> hello.txt"""
    import re
    # "file mein text likho"
    m = re.search(r"(\S+\.?\w*)\s+(?:mein|me)\s+['\"]?(.+?)['\"]?\s+(?:likho|likh|write|daalo)", original, re.I)
    if m:
        fname = m.group(1)
        text  = m.group(2).strip()
        return f'echo {text} >> "{fname}"', f'"{fname}" mein "{text}" likh diya jaega'
    return f'echo text >> "{arg}"' if arg else None, "File aur text dono batao!"


# ══════════════════════════════════════════════════════════════
# EXECUTOR
# ══════════════════════════════════════════════════════════════

def run_command(command: str, cwd: str = None) -> dict:
    """
    Command ko actually PC pe chalao.

    Returns:
        {
          'success': True/False,
          'output' : command output,
          'command': command string
        }
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd or WORK_DIR,
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace"
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode == 0:
            return {
                "success": True,
                "output" : stdout if stdout else "✅ Command successful!",
                "command": command
            }
        else:
            return {
                "success": False,
                "output" : stderr if stderr else "Unknown error",
                "command": command
            }
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "⏱️ Timeout!", "command": command}
    except Exception as e:
        return {"success": False, "output": str(e), "command": command}


def execute(intent_result: dict) -> dict:
    """
    Main function — intent result lo, command banao, validate karo, chalao.

    Args:
        intent_result: nlp/semantic.py ka analyze() output

    Returns:
        {
          'success'    : True/False,
          'output'     : command output,
          'command'    : exact command,
          'explanation': Hindi explanation,
          'blocked'    : True agar dangerous aur user ne cancel kiya
        }
    """
    intent      = intent_result.get("intent", "UNKNOWN")
    argument    = intent_result.get("argument")
    target_path = intent_result.get("target_path")   # Desktop/Downloads etc
    original    = intent_result.get("original", "")
    is_dangerous= intent_result.get("is_dangerous", False)

    # UNKNOWN intent
    if intent == "UNKNOWN":
        return {
            "success"    : False,
            "output"     : "",
            "command"    : None,
            "explanation": "Samajh nahi aaya! 'help' likho examples ke liye.",
            "blocked"    : False
        }

    # Command banao — target_path hai to wahan banao
    effective_dir = target_path if target_path else None
    command, explanation = build_command(intent, argument, original, target_dir=effective_dir)

    if not command:
        return {
            "success"    : False,
            "output"     : "",
            "command"    : None,
            "explanation": explanation,
            "blocked"    : False
        }

    # Validate karo
    validation = validate(command)
    if not validation["safe"] or is_dangerous:
        return {
            "success"    : False,
            "output"     : "",
            "command"    : command,
            "explanation": explanation,
            "blocked"    : True,                    # Caller confirm karega
            "danger_reason": validation["reason"]
        }

    # Execute karo
    result = run_command(command, cwd=target_path or WORK_DIR)
    result["explanation"] = explanation
    result["blocked"]     = False
    return result
