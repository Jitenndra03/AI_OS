import psutil


def get_memory_usage():
    """Return virtual memory stats as a dict."""
    mem = psutil.virtual_memory()
    return {
        "total": mem.total,
        "available": mem.available,
        "used": mem.used,
        "percent": mem.percent,
    }


def get_swap_usage():
    """Return swap memory stats as a dict."""
    swap = psutil.swap_memory()
    return {
        "total": swap.total,
        "used": swap.used,
        "percent": swap.percent,
    }
