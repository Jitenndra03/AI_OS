import psutil


def get_top_processes(limit=10, sort_by="cpu_percent"):
    """Return the top processes sorted by the given metric.

    Each process dict contains: pid, name, cpu_percent, memory_percent, status.
    Processes that vanish mid-iteration are silently skipped.
    """
    processes = []
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
        try:
            info = proc.info
            # Skip zombie/dead entries with no useful data
            if info["cpu_percent"] is None:
                continue
            processes.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    processes.sort(key=lambda p: p.get(sort_by, 0) or 0, reverse=True)
    return processes[:limit]
