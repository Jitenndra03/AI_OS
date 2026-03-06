from monitor.cpu_monitor import get_cpu_usage
from monitor.memory_monitor import get_memory_usage
from monitor.disk_monitor import get_disk_usage
from monitor.process_monitor import get_top_processes

import time

while True:
    print("CPU:", get_cpu_usage())
    print("Memory:", get_memory_usage())
    print("Disk:", get_disk_usage())

    print("\nTop Processes:")
    for p in get_top_processes():
        print(p)

    time.sleep(5)