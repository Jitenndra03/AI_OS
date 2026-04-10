import psutil


def get_cpu_usage(interval=1):
    """Return overall CPU usage percentage."""
    return psutil.cpu_percent(interval=interval)


def get_per_cpu_usage(interval=1):
    """Return per-core CPU usage as a list of percentages."""
    return psutil.cpu_percent(interval=interval, percpu=True)
