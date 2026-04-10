import os
import time

from monitor.cpu_monitor import get_cpu_usage
from monitor.memory_monitor import get_memory_usage, get_swap_usage
from monitor.disk_monitor import get_disk_usage
from monitor.process_monitor import get_top_processes


def collect_system_snapshot(top_process_limit=10):
    """Collect a complete system snapshot with a timestamp.

    Returns a flat dict suitable for both display and AI feature extraction.
    Now includes Load Average and Swap metrics.
    """
    cpu = get_cpu_usage()
    memory = get_memory_usage()
    swap = get_swap_usage()
    disk = get_disk_usage()
    top_procs = get_top_processes(limit=top_process_limit)
    
    # os.getloadavg() returns (1min, 5min, 15min) load
    load1, load5, load15 = os.getloadavg()

    return {
        "timestamp": time.time(),
        "cpu_percent": cpu,
        "load_1m": load1,
        "load_5m": load5,
        "load_15m": load15,
        "memory_percent": memory["percent"],
        "memory_used": memory["used"],
        "memory_available": memory["available"],
        "swap_percent": swap["percent"],
        "disk_percent": disk["percent"],
        "disk_used": disk["used"],
        "disk_free": disk["free"],
        "top_processes": top_procs,
    }

