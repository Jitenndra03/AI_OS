"""Canonical read-only and password-gated package proposals; no arbitrary execution."""
import hashlib
import json
from pathlib import Path
import re
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
PACKAGE_ACTIONS = frozenset({'install_packages', 'remove_packages', 'update_packages'})
WORKER = str(Path(__file__).with_name("_operations.py").resolve())
PACKAGE_WORKER = str(Path(__file__).with_name("_package_worker.py").resolve())
PYTHON = str(Path(sys.executable).absolute())


def validate_pattern(pattern):
    if (type(pattern) is not str or not 1 <= len(pattern) <= 200
            or any(ord(c) < 32 or ord(c) == 127 for c in pattern)
            or '/' in pattern or '\\' in pattern or pattern in {'.', '..'}):
        raise ValueError('Use a filename or glob such as report.pdf or *.py, within --workspace.')
    return pattern


def package_names(action, target):
    from nli.package_operations import validate_packages
    if action == 'update_packages':
        if target is not None:
            raise ValueError('Index refresh does not accept package arguments')
        return ()
    return validate_packages(target)


def canonical_argv(action: str, target=None) -> tuple[str, ...]:
    if action in PACKAGE_ACTIONS:
        return (PYTHON, '-I', PACKAGE_WORKER, action, *package_names(action, target))
    if action not in OPERATIONS:
        raise ValueError("Unsupported operation")
    if target is not None:
        if action != 'find_files':
            raise ValueError('This operation does not accept a target')
        return (PYTHON, '-I', WORKER, action, validate_pattern(target))
    return (PYTHON, "-I", WORKER, action)


def make_proposal(action: str, cwd=None, target=None) -> CommandProposal:
    root = str(Path(cwd or Path.cwd()).resolve(strict=True))
    argv = canonical_argv(action, target)
    if action in PACKAGE_ACTIONS:
        from nli.package_operations import format_package_plan
        explanation = (format_package_plan(action, package_names(action, target)) +
            '\nEach sudo step requires fresh password entry in the terminal. '
            'APT can run package maintainer scripts with administrator privileges. '
            'Interruptions wait for APT to exit; do not power off during a transaction.')
        return CommandProposal(action, shlex.join(argv), target, True, True, 'high',
                               explanation, argv, root)
    explanation = OPERATIONS[action]
    if target is not None:
        explanation += ' Filename pattern: ' + repr(target)
    return CommandProposal(action, shlex.join(argv), target, False, True, "low",
                           explanation, argv, root)


def validate_proposal(proposal: CommandProposal):
    reason = "Rejected: only canonical approved operations are supported."
    if type(proposal) is not CommandProposal:
        return False, reason, proposal
    try:
        CommandProposal.from_dict(proposal.to_dict())
        if type(proposal.argv) is not tuple:
            return False, reason, proposal
        if proposal.action not in OPERATIONS and proposal.action not in PACKAGE_ACTIONS:
            return False, "Unsupported request. No command will run.", proposal
        root = Path(proposal.cwd)
        if not proposal.cwd or not root.is_absolute() or not root.is_dir():
            return False, "Rejected: working directory must exist and be absolute.", proposal
        if str(root.resolve(strict=True)) != proposal.cwd:
            return False, "Rejected: working directory must be canonical.", proposal
        expected = make_proposal(proposal.action, proposal.cwd, proposal.target)
        if proposal != expected:
            return False, reason, proposal
    except (TypeError, ValueError, OSError):
        return False, reason, proposal
    return True, "Validated canonical operation.", proposal


def approval_digest(proposal: CommandProposal) -> str:
    """Bind consent to every displayed field, argv and working directory."""
    valid, reason, _ = validate_proposal(proposal)
    if not valid:
        raise ValueError(reason)
    return hashlib.sha256(json.dumps(proposal.to_dict(), sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()
