"""Parse supported intents into canonical operations, never arbitrary shell text."""
import re
import shlex
from nli.models import CommandProposal
from nli.safety_validator import make_proposal

SUPPORTED_SCOPE = (
    'Supported: CPU/memory/storage usage, top processes, system summary, current directory, '
    'list files, find files named "*.py", install packages git curl, uninstall package vlc, '
    'apt update, and write a bash script. Package changes need an interactive terminal '
    'and fresh sudo password entry for each step. Scripts are previewed and saved, not executed.')
PHRASES = {
    "check_cpu_usage": ("cpu", "cpu usage", "processor usage"),
    "check_memory_usage": ("memory", "memory usage", "ram", "ram usage", "free memory"),
    "check_disk_usage": ("disk", "disk usage", "disk space", "free disk space", 'storage',
                         'storage usage', 'storage space', 'available storage', 'free storage'),
    "list_top_processes": ("top processes", "processes", "top ten processes", "top 10 processes",
                           "top processes by cpu", "top 10 processes using the most cpu"),
    "get_system_summary": ("system summary", "system status", "system information"),
    "current_directory": ("current directory", "working directory", "where am i", "pwd"),
    "list_files": ("files", "list files", "list directory", "directory contents", "ls"),
    "find_files": ("find files", "find all files", "find files in this directory"),
}


def unknown(reason="Request is outside the supported scope."):
    return CommandProposal("unknown", "", None, False, False, "low", reason + " " + SUPPORTED_SCOPE)


def parse_offline(user_input: str, cwd=None):
    if type(user_input) is not str or len(user_input) > 2000 or any(
            ord(c) < 32 and c != '\t' for c in user_input):
        return unknown("Invalid request.")
    original = re.sub(r'^please\s+', '', user_input.strip(), flags=re.I)
    phrase = " ".join(original.lower().rstrip(".?!").split())
    phrase = re.sub(r"^(?:show(?: me)?|check(?:ing)?|display|get|tell me) (?:the )?", "", phrase)
    for action, phrases in PHRASES.items():
        if phrase in phrases:
            return make_proposal(action, cwd)
    try:
        if re.fullmatch(r'(?:sudo )?(?:apt(?:-get)? update|update (?:apt|package indexes|package lists))', original, re.I):
            return make_proposal('update_packages', cwd)
        match = re.fullmatch(r'(?:sudo\s+)?(?:apt(?:-get)?\s+)?(install|uninstall|remove)\s+(?:(?:packages?|apps?|applications?)\s+)?(.+)', original, re.I)
        if match:
            target = re.sub(r'\s+and\s+|\s*,\s*', ' ', match[2].lower())
            target = ' '.join(target.split())
            return make_proposal('install_packages' if match[1].lower() == 'install' else 'remove_packages', cwd, target)
        match = re.fullmatch(r'(?:find|locate|search for)\s+(?:files?\s+(?:named|matching)\s+)?(.+)', original, re.I)
        if match:
            tokens = shlex.split(match[1])
            if len(tokens) != 1:
                return unknown('Quote a filename containing spaces; search accepts one filename pattern.')
            return make_proposal('find_files', cwd, tokens[0])
    except (ValueError, OSError) as exc:
        return unknown(str(exc))
    return unknown()
