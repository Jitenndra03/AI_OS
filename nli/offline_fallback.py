"""Conservative phrase matching: unknown/compound requests never execute."""
import re
from nli.models import CommandProposal
from nli.safety_validator import make_proposal

SUPPORTED_SCOPE = ("Supported requests: show CPU usage, show memory usage, show disk usage, "
                   "show top processes, system summary, current directory, list files, find files. "
                   "File operations stay within the approved working directory; no writes, "
                   "root commands, arbitrary Bash, services or process changes are supported.")
PHRASES = {
    "check_cpu_usage": ("cpu", "cpu usage", "processor usage"),
    "check_memory_usage": ("memory", "memory usage", "ram", "ram usage", "free memory"),
    "check_disk_usage": ("disk", "disk usage", "disk space", "free disk space"),
    "list_top_processes": ("top processes", "processes", "top ten processes", "top 10 processes",
                           "top processes by cpu", "top 10 processes using the most cpu"),
    "get_system_summary": ("system summary", "system status", "system information"),
    "current_directory": ("current directory", "working directory", "where am i", "pwd"),
    "list_files": ("files", "list files", "list directory", "directory contents", "ls"),
    "find_files": ("find files", "find all files", "find files in this directory"),
}


def unknown(reason="Request is outside the supported scope."):
    return CommandProposal("unknown", "", None, False, False, "low",
                           reason + " " + SUPPORTED_SCOPE)


def parse_offline(user_input: str, cwd=None):
    if type(user_input) is not str or len(user_input) > 2000:
        return unknown("Invalid request.")
    phrase = " ".join(user_input.lower().strip().rstrip(".?!").split())
    phrase = re.sub(r"^(?:please )?(?:show(?: me)?|check|display|get|tell me) (?:the )?", "", phrase)
    for action, phrases in PHRASES.items():
        if phrase in phrases:
            return make_proposal(action, cwd)
    return unknown()
