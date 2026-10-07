"""Trusted finite operation worker, invoked in an isolated PTY session.

No eval, shell, command strings or user-provided Python are accepted.
"""
import fcntl
import fnmatch
import os
import termios
from pathlib import Path
import sys
import time


def main(action, pattern=None):
    root = Path.cwd()
    if action == "current_directory":
        print(root)
    elif action == "list_files":
        # repr prevents terminal escape sequences in attacker-controlled names.
        with os.scandir(root) as entries:
            for index, entry in enumerate(entries):
                if index >= 1000:
                    print("[Entry limit: 1000]")
                    break
                print(repr(entry.name) + ("/" if entry.is_dir(follow_symlinks=False) else ""))
    elif action == "find_files":
        count = 0
        for directory, dirs, files in os.walk(root, followlinks=False):
            depth = len(Path(directory).relative_to(root).parts)
            dirs[:] = [name for name in dirs if not Path(directory, name).is_symlink()]
            if depth >= 3:
                dirs[:] = []
            for name in files:
                path = Path(directory, name)
                if (path.is_symlink() or not path.is_file()
                        or (pattern is not None and not fnmatch.fnmatchcase(name, pattern))):
                    continue
                print(repr(str(path.relative_to(root))))
                count += 1
                if count >= 1000:
                    print("[File limit: 1000]")
                    return 0
    else:
        import psutil
        if action == "list_top_processes":
            processes = list(psutil.process_iter(["pid", "name", "memory_percent"]))
            for process in processes:
                try:
                    process.cpu_percent()
                except psutil.Error:
                    pass
            time.sleep(0.15)
            rows = []
            for process in processes:
                try:
                    rows.append((process.cpu_percent(), process.pid,
                                 process.info["memory_percent"] or 0, process.info["name"]))
                except psutil.Error:
                    continue
            print("PID       CPU%    MEM%  NAME")
            for cpu, pid, memory, name in sorted(rows, reverse=True)[:10]:
                print(f"{pid:<9} {cpu:5.1f} {memory:7.2f}  {name!r}")
        elif action == "check_cpu_usage":
            print(f"CPU usage: {psutil.cpu_percent(interval=0.15):.1f}%")
        elif action == "check_memory_usage":
            memory, swap = psutil.virtual_memory(), psutil.swap_memory()
            print(f"Memory: {memory.percent}% used; {memory.available / 2**30:.2f} GiB available")
            print(f"Swap: {swap.percent}% used; {swap.total / 2**30:.2f} GiB total")
        elif action == "check_disk_usage":
            disk = psutil.disk_usage(root)
            print(f"Disk: {disk.percent}% used; {disk.free / 2**30:.2f} GiB free")
        elif action == "get_system_summary":
            print(f"Uptime: {(time.time() - psutil.boot_time()) / 3600:.2f} hours")
            for operation in ("check_cpu_usage", "check_memory_usage", "check_disk_usage"):
                main(operation)
        else:
            print("Unsupported operation", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3) or (len(sys.argv) == 3 and sys.argv[1] != 'find_files'):
        sys.exit(2)
    try:
        if os.isatty(0) and os.getsid(0) == os.getpid():
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else None))
    except (OSError, ImportError) as error:
        print(f"Operation unavailable: {error}", file=sys.stderr)
        sys.exit(1)
