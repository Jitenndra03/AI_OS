"""process_controller.py — Safe automated response to detected anomalies.

Updated to use unified logging and shared synchronization primitives.
"""

import os
import time
from pathlib import Path

import pandas as pd
import psutil

from config import settings
from utils.concurrency import model_lock # anomaly_scores.csv is protected by model_lock
from utils.logger import setup_logger, system_logger

# Dedicated actions logger
_actions_log = setup_logger("Controller", settings.ACTIONS_LOG)

_last_action_times: dict[int, float] = {}

def _is_system_process(pid: int) -> bool:
    try:
        proc = psutil.Process(pid)
        return proc.uids().real == 0
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return True

def _is_whitelisted(name: str) -> bool:
    return name in settings.PROCESS_WHITELIST

def _is_cooldown_active(pid: int) -> bool:
    last = _last_action_times.get(pid)
    if last is None:
        return False
    return (time.time() - last) < settings.ACTION_COOLDOWN_SEC

def renice_process(pid: int, name: str, score: float) -> bool:
    if _is_system_process(pid):
        _actions_log.info(f"SKIP (uid=0)     | pid={pid:<7} | name={name:<25} | score={score:.6f}")
        return False

    if _is_whitelisted(name):
        _actions_log.info(f"SKIP (whitelist)  | pid={pid:<7} | name={name:<25} | score={score:.6f}")
        return False

    if _is_cooldown_active(pid):
        return False # Cooldown is silent to avoid log bloat

    if not psutil.pid_exists(pid):
        return False

    try:
        os.setpriority(os.PRIO_PROCESS, pid, settings.RENICE_VALUE)
        _last_action_times[pid] = time.time()
        _actions_log.info(f"RENICED (+{settings.RENICE_VALUE}) | pid={pid:<7} | name={name:<25} | score={score:.6f}")
        return True
    except PermissionError:
        _actions_log.warning(f"FAIL (perm)      | pid={pid:<7} | name={name:<25} | score={score:.6f}")
        return False
    except Exception as e:
        _actions_log.error(f"FAIL (error)     | pid={pid:<7} | name={name:<25} | {e}")
        return False

def enforce(scores_csv: Path | None = None) -> dict:
    scores_csv = scores_csv or settings.ANOMALY_SCORES_CSV

    stats = {"total_anomalies": 0, "reniced": 0, "skipped": 0, "failed": 0}

    with model_lock:
        if not scores_csv.exists():
            return stats
        try:
            df = pd.read_csv(scores_csv)
        except Exception as e:
            system_logger.error(f"Failed to read scores CSV: {e}")
            return stats

    anomalies = df[df["label"] == -1]
    stats["total_anomalies"] = len(anomalies)

    if anomalies.empty:
        return stats

    for _, row in anomalies.iterrows():
        pid, name, score = int(row["pid"]), str(row["name"]), float(row["anomaly_score"])
        if renice_process(pid, name, score):
            stats["reniced"] += 1
        else:
            stats["skipped"] += 1

    return stats
