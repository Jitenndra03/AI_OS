"""
system_monitor/memory.py
=========================
RAM usage monitor.
"""

import psutil

RAM_ALERT_THRESHOLD = 85


def get_memory_stats() -> dict:
    """
    RAM ki current stats.

    Returns:
        {
          'total_gb'  : 8.0,
          'used_gb'   : 5.2,
          'free_gb'   : 2.8,
          'percent'   : 65.0,
          'is_high'   : False
        }
    """
    mem = psutil.virtual_memory()
    return {
        "total_gb": round(mem.total  / (1024**3), 2),
        "used_gb" : round(mem.used   / (1024**3), 2),
        "free_gb" : round(mem.available / (1024**3), 2),
        "percent" : mem.percent,
        "is_high" : mem.percent > RAM_ALERT_THRESHOLD,
    }
