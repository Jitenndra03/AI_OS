import psutil

def get_disk_usage():
    disk = psutil.disk_usage('/')

    return {
        "total": disk.total,
        "used": disk.used,
        "percent": disk.percent
    }