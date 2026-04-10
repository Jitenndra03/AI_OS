"""action_dispatcher.py — Translate AI anomaly signals into system actions.

This is the bridge between the AI layer (detector.py) and the OS layer (process_actions.py).
It decides *which* processes to act on and *what* to do, based on the anomaly flag
and the current snapshot.
"""

import logging
import time
from typing import Dict

from config import settings
from controller.process_actions import log_suspicious_process, renice_process

logger = logging.getLogger(__name__)

# Dedicated logger for anomalies.log
anomaly_logger = logging.getLogger("ai_os.anomalies")

# In-memory cache to track process action times: {pid: timestamp}
_last_action_times: Dict[int, float] = {}


def _init_anomaly_logger():
    """Configure the dedicated anomaly logger if not already set up."""
    if anomaly_logger.handlers:
        return
    
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(settings.ANOMALY_LOG_PATH)
    fh.setFormatter(fmt)
    anomaly_logger.addHandler(fh)
    anomaly_logger.setLevel(logging.INFO)
    anomaly_logger.propagate = False  # Don't send to main log


def dispatch(is_anomaly: bool, snapshot: dict) -> None:
    """Entry point called from main.py after every prediction.

    Now includes Whitelisting and Action Cooldown logic.
    """
    if not is_anomaly:
        return

    _init_anomaly_logger()
    top_procs = snapshot.get("top_processes", [])

    if not top_procs:
        logger.warning("Anomaly detected but no process data available.")
        return

    anomaly_msg = (
        f"Anomaly! CPU={snapshot['cpu_percent']:.1f}% "
        f"Load={snapshot['load_1m']:.2f} "
        f"Mem={snapshot['memory_percent']:.1f}% "
        f"Swap={snapshot['swap_percent']:.1f}%"
    )
    logger.warning(f"─── Anomaly Response ─── {anomaly_msg}")
    anomaly_logger.info(f"START ANOMALY EVENT: {anomaly_msg}")

    # Process candidates for action
    for proc in top_procs:
        pid = proc.get("pid")
        name = proc.get("name", "unknown")
        
        # 1. Structured Logging (always happens for all top processes)
        log_suspicious_process(proc)
        anomaly_logger.warning(
            f"Candidate: PID={pid} NAME={name} CPU={proc.get('cpu_percent')}% MEM={proc.get('memory_percent')}%"
        )
        
        if pid is None:
            continue

        # 2. Whitelist Check
        if name in settings.PROCESS_WHITELIST:
            logger.debug(f"Skipping whitelisted process: {name} (PID {pid})")
            continue

        # 3. Cooldown Check
        now = time.time()
        last_time = _last_action_times.get(pid, 0)
        if now - last_time < settings.ACTION_COOLDOWN_SEC:
            logger.debug(f"Action cooldown active for PID {pid} ({name})")
            continue

        # 4. Perform Action (Renice)
        success = renice_process(pid, nice_value=settings.RENICE_VALUE)
        if success:
            _last_action_times[pid] = now
            anomaly_logger.info(f"ACTION TAKEN: reniced PID {pid} ({name}) to {settings.RENICE_VALUE}")
            # We only act on the single most significant process per cycle
            break
    
    anomaly_logger.info("END ANOMALY EVENT\n" + "-"*40)

