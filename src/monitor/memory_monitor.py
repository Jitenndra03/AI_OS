import psutil

def get_memory_usage():
    memory = psutil.virtual_memory()

    return {
        "total": memory.total,
        "available": memory.available,
        "percent": memory.percent
    }