"""process_actions.py — Safe, non-destructive actions on running processes.

Design principle: we NEVER kill or terminate processes automatically.
The only action taken is renice (lower scheduling priority) and structured logging.
This keeps the system safe for students and production-like environments alike.
"""

import logging
import os

logger = logging.getLogger(__name__)


def renice_process(pid: int, nice_value: int = 10) -> bool:
    """Lower the scheduling priority of a process using os.setpriority.

    A higher nice value (max 19) means the OS gives the process less CPU time.
    This is completely reversible and non-destructive.

    Args:
        pid:        Process ID to renice.
        nice_value: Target nice value (0–19). Default 10 = noticeably lower priority.

    Returns:
        True on success, False if the process no longer exists or access is denied.
    """
    try:
        current_nice = os.getpriority(os.PRIO_PROCESS, pid)

        # Only lower priority — never accidentally raise it
        if current_nice >= nice_value:
            logger.debug("PID %d already at nice=%d, skipping renice.", pid, current_nice)
            return True

        os.setpriority(os.PRIO_PROCESS, pid, nice_value)
        logger.info("Reniced PID %d: nice %d → %d", pid, current_nice, nice_value)
        return True

    except ProcessLookupError:
        logger.warning("Renice failed: PID %d no longer exists.", pid)
        return False
    except PermissionError:
        logger.warning("Renice failed: no permission to renice PID %d.", pid)
        return False


def log_suspicious_process(proc: dict) -> None:
    """Write a structured log entry for a process flagged during anomaly detection.

    Args:
        proc: A process dict from monitor.process_monitor.get_top_processes(),
              containing keys: pid, name, cpu_percent, memory_percent, status.
    """
    logger.warning(
        "Suspicious process | PID=%-6s name=%-20s cpu=%5.1f%% mem=%5.1f%% status=%s",
        proc.get("pid", "?"),
        proc.get("name", "unknown"),
        proc.get("cpu_percent") or 0.0,
        proc.get("memory_percent") or 0.0,
        proc.get("status", "?"),
    )
