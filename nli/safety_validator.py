"""Finite read-only operations; this is deliberately not a Bash sandbox."""
import hashlib
import json
from pathlib import Path
import shlex
import sys

from nli.models import CommandProposal

OPERATIONS = {
    "list_top_processes": "Show the ten processes using the most CPU.",
    "check_cpu_usage": "Sample CPU usage over a short interval.",
    "check_memory_usage": "Show physical and swap memory usage.",
    "check_disk_usage": "Show disk usage for the approved working directory.",
    "get_system_summary": "Show CPU, memory, disk and uptime summary.",
    "current_directory": "Show the approved working directory.",
    "list_files": "List entries in the approved working directory.",
    "find_files": "Find regular files up to four directory levels deep, without following symlinks.",
}
WORKER = str(Path(__file__).with_name("_operations.py").resolve())
PYTHON = str(Path(sys.executable).absolute())


def canonical_argv(action: str) -> tuple[str, ...]:
    if action not in OPERATIONS:
        raise ValueError("Unsupported operation")
    return (PYTHON, "-I", WORKER, action)


def make_proposal(action: str, cwd=None) -> CommandProposal:
    root = str(Path(cwd or Path.cwd()).resolve(strict=True))
    argv = canonical_argv(action)
    return CommandProposal(action, shlex.join(argv), None, False, True, "low",
                           OPERATIONS[action], argv, root)


def validate_proposal(proposal: CommandProposal):
    reason = "Rejected: only canonical read-only operations are supported."
    if type(proposal) is not CommandProposal:
        return False, reason, proposal
    try:
        # Direct callers do not get to bypass strict schema checks.
        CommandProposal.from_dict(proposal.to_dict())
        if type(proposal.argv) is not tuple:
            return False, reason, proposal
        if proposal.action not in OPERATIONS:
            return False, "Unsupported request. No command will run.", proposal
        argv = canonical_argv(proposal.action)
        root = Path(proposal.cwd)
        if not proposal.cwd or not root.is_absolute() or not root.is_dir():
            return False, "Rejected: working directory must exist and be absolute.", proposal
        if str(root.resolve(strict=True)) != proposal.cwd:
            return False, "Rejected: working directory must be canonical.", proposal
        if (proposal.argv != argv or proposal.script != shlex.join(argv)
                or proposal.target is not None or proposal.requires_sudo is not False
                or proposal.confirm_required is not True or proposal.risk_level != "low"
                or proposal.explanation != OPERATIONS[proposal.action]):
            return False, reason, proposal
    except (TypeError, ValueError, OSError):
        return False, reason, proposal
    return True, "Validated finite read-only operation.", proposal


def approval_digest(proposal: CommandProposal) -> str:
    """Bind consent to every displayed field, argv and working directory."""
    valid, reason, _ = validate_proposal(proposal)
    if not valid:
        raise ValueError(reason)
    return hashlib.sha256(json.dumps(proposal.to_dict(), sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()
