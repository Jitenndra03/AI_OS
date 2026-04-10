import psutil


def get_disk_usage(path="/"):
    """Return disk usage stats for the given mount point."""
    disk = psutil.disk_usage(path)
    return {
        "total": disk.total,
        "used": disk.used,
        "free": disk.free,
        "percent": disk.percent,
    }
