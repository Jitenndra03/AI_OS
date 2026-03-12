"""
system_monitor/cpu.py
======================
CPU usage monitor — psutil se real-time data lo.
"""

import psutil

CPU_ALERT_THRESHOLD = 85   # % se zyada ho to alert


def get_cpu_stats() -> dict:
    """
    CPU ki current stats lo.

    Returns:
        {
          'usage_percent' : 45.2,
          'core_count'    : 8,
          'freq_mhz'      : 2400.0,
          'is_high'       : False
        }
    """
    usage = psutil.cpu_percent(interval=0.5)
    freq  = psutil.cpu_freq()
    cores = psutil.cpu_count(logical=True)

    return {
        "usage_percent": usage,
        "core_count"   : cores,
        "freq_mhz"     : round(freq.current, 1) if freq else 0,
        "is_high"      : usage > CPU_ALERT_THRESHOLD,
    }


def get_top_processes(n: int = 5) -> list[dict]:
    """
    Top N CPU-consuming processes.
    """
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent"]):
        try:
            procs.append(p.info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    procs.sort(key=lambda x: x.get("cpu_percent", 0), reverse=True)
    return procs[:n]
