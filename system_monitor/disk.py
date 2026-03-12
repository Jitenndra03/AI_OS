"""
system_monitor/disk.py
=======================
Disk usage monitor.
"""

import psutil

DISK_ALERT_THRESHOLD = 90


def get_disk_stats(path: str = "C:\\") -> dict:
    """
    Disk ki current stats.
    """
    try:
        disk = psutil.disk_usage(path)
        return {
            "path"     : path,
            "total_gb" : round(disk.total / (1024**3), 1),
            "used_gb"  : round(disk.used  / (1024**3), 1),
            "free_gb"  : round(disk.free  / (1024**3), 1),
            "percent"  : disk.percent,
            "is_high"  : disk.percent > DISK_ALERT_THRESHOLD,
        }
    except Exception as e:
        return {"error": str(e)}


def get_all_disks() -> list[dict]:
    """Sab drives ki stats."""
    stats = []
    for part in psutil.disk_partitions():
        try:
            s = get_disk_stats(part.mountpoint)
            s["device"] = part.device
            stats.append(s)
        except:
            pass
    return stats
