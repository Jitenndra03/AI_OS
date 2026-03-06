import psutil

def get_top_processes(limit=5):

    processes = []

    for proc in psutil.process_iter(['pid','name','cpu_percent']):
        processes.append(proc.info)

    processes = sorted(
        processes,
        key=lambda x: x['cpu_percent'],
        reverse=True
    )

    return processes[:limit]