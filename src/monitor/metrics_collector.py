"""Read-only, nonblocking system sampling with persistent CPU counters."""

import time
from pathlib import Path

import psutil

from src.config import settings
from src.optimization.models import ProcessMetrics, SystemSnapshot


class SystemMonitor:
    """Reuse one instance across intervals; new processes start with zero rates.

    Per-process CPU follows psutil's convention and may exceed 100%.
    """

    def __init__(self, proc_root: str | Path | None = None):
        self.proc_root = Path(proc_root or getattr(settings, "PROC_ROOT", "/proc"))
        self._processes: dict[int, psutil.Process] = {}
        self._process_io: dict[str, tuple[float, int]] = {}
        self._disk: tuple[float, int, int] | None = None
        self._cpu_primed = False

    def _pressure(self, resource: str) -> float:
        """Linux PSI's `some avg10` percentage; unsupported PSI is optional."""
        try:
            for line in (self.proc_root / "pressure" / resource).read_text().splitlines():
                if line.startswith("some "):
                    values = dict(field.split("=", 1) for field in line.split()[1:])
                    return max(0.0, min(100.0, float(values["avg10"])))
        except (OSError, ValueError, KeyError):
            pass
        return 0.0

    def _sample_process(self, proc, now, foreground_pids, first):
        accessible = True

        def read(method, default):
            nonlocal accessible
            try:
                return method()
            except (psutil.AccessDenied, OSError, NotImplementedError):
                accessible = False
                return default

        with proc.oneshot():
            created = read(proc.create_time, 0.0)
            identity = f"{proc.pid}:{created:.6f}"
            cpu = read(lambda: proc.cpu_percent(interval=None), 0.0)
            uids = read(proc.uids, None)
            # A setuid/setresuid program must never look like an ordinary
            # user task merely because its real UID matches the policy owner.
            if uids is None or any(getattr(uids, key, -1) != uids.real
                                   for key in ("effective", "saved")):
                accessible = False
            memory = read(proc.memory_info, None)
            io = read(proc.io_counters, None)
            values = dict(
                pid=proc.pid, create_time=created,
                ppid=read(proc.ppid, 0), uid=uids.real if uids else -1,
                name=read(proc.name, ""), exe=read(proc.exe, ""),
                cmdline=tuple(read(proc.cmdline, [])),
                cpu_percent=0.0 if first else max(0.0, cpu),
                memory_percent=max(0.0, read(proc.memory_percent, 0.0)),
                rss=memory.rss if memory else 0,
                num_threads=read(proc.num_threads, 0),
                status=read(proc.status, "unknown"), nice=read(proc.nice, 0),
                read_bytes=io.read_bytes if io else 0,
                write_bytes=io.write_bytes if io else 0,
            )

        foreground = foreground_pids is not None and proc.pid in foreground_pids
        kernel_thread = False
        session_id = 0
        try:
            # comm can contain spaces/parentheses. Fields after its final ')'
            # have stable Linux proc(5) positions.
            stat = (self.proc_root / str(proc.pid) / "stat").read_text()
            fields = stat[stat.rindex(")") + 2:].split()
            pgrp, tty, tpgid, flags = map(int, (fields[2], fields[4], fields[5], fields[6]))
            session_id = int(fields[3])
            kernel_thread = bool(flags & 0x00200000)  # Linux PF_KTHREAD
            if foreground_pids is None:
                foreground = tty != 0 and tpgid > 0 and pgrp == tpgid
        except (OSError, ValueError, IndexError):
            accessible = False

        cgroup = ""
        try:
            groups = (self.proc_root / str(proc.pid) / "cgroup").read_text().splitlines()
            # Prefer the unified v2 hierarchy; expose its path only.
            for group in groups:
                hierarchy, controllers, path = group.split(":", 2)
                if hierarchy == "0" and not controllers:
                    cgroup = path
                    break
                if not cgroup:
                    cgroup = path
        except (OSError, ValueError):
            accessible = False

        rate = 0.0
        if io is not None:
            total = io.read_bytes + io.write_bytes
            previous = self._process_io.get(identity)
            if previous and now > previous[0]:
                rate = max(0.0, total - previous[1]) / (now - previous[0])
            self._process_io[identity] = (now, total)
        return ProcessMetrics(**values, io_bytes_per_sec=rate, cgroup=cgroup,
                              foreground=foreground, kernel_thread=kernel_thread,
                              accessible=accessible and created > 0, session_id=session_id)

    def sample(self, foreground_pids: set[int] | None = None) -> SystemSnapshot:
        """Sample all visible processes; never sleep or change system state.

        Explicit foreground PIDs override terminal inference (an empty set
        disables it). Global failures make snapshots incomplete. Inaccessible
        processes remain visible but ineligible for control; vanished ones
        are skipped.
        """
        now = time.monotonic()
        timestamp = time.time()
        complete = True

        def system_read(method, default):
            nonlocal complete
            try:
                return method()
            except (psutil.Error, OSError, RuntimeError, ValueError, NotImplementedError):
                complete = False
                return default

        cpu = system_read(lambda: psutil.cpu_percent(interval=None), None)
        if cpu is None:
            cpu = 0.0
            self._cpu_primed = False
        elif not self._cpu_primed:
            self._cpu_primed = True
            cpu = 0.0
        memory = system_read(psutil.virtual_memory, None)
        load = system_read(psutil.getloadavg, (0.0, 0.0, 0.0))
        count = system_read(psutil.cpu_count, 1) or 1
        disk = system_read(psutil.disk_io_counters, None)
        disk_read = disk_write = 0.0
        if disk is not None:
            if self._disk and now > self._disk[0]:
                elapsed = now - self._disk[0]
                disk_read = max(0, disk.read_bytes - self._disk[1]) / elapsed
                disk_write = max(0, disk.write_bytes - self._disk[2]) / elapsed
            self._disk = (now, disk.read_bytes, disk.write_bytes)
        else:
            self._disk = None

        processes = []
        retained = {}
        try:
            for pid in psutil.pids():
                try:
                    proc = self._processes.get(pid)
                    # is_running checks creation time, including PID reuse.
                    first = proc is None or not proc.is_running()
                    if first:
                        proc = psutil.Process(pid)
                    metrics = self._sample_process(proc, now, foreground_pids, first)
                    processes.append(metrics)
                    retained[pid] = proc
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    continue
                except psutil.AccessDenied:
                    processes.append(ProcessMetrics(pid=pid, create_time=0.0, accessible=False))
                except (OSError, ValueError, RuntimeError, psutil.Error):
                    complete = False
                    processes.append(ProcessMetrics(pid=pid, create_time=0.0, accessible=False))
        except (psutil.Error, OSError, RuntimeError):
            complete = False
        self._processes = retained
        identities = {proc.identity for proc in processes}
        self._process_io = {key: value for key, value in self._process_io.items() if key in identities}
        return SystemSnapshot(
            timestamp=timestamp, monotonic=now, processes=tuple(processes),
            cpu_percent=max(0.0, min(100.0, cpu)),
            memory_percent=memory.percent if memory else 0.0,
            memory_available=memory.available if memory else 0,
            load_average=tuple(load), disk_read_bytes_per_sec=disk_read,
            disk_write_bytes_per_sec=disk_write,
            cpu_pressure=self._pressure("cpu"), memory_pressure=self._pressure("memory"),
            io_pressure=self._pressure("io"), cpu_count=count, complete=complete,
        )


_legacy_monitor: SystemMonitor | None = None


def collect_process_metrics(top_n: int | None = None) -> list[dict]:
    """Legacy CSV rows, sorted by CPU; None retains TOP_PROCESS_LIMIT behavior."""
    global _legacy_monitor
    if _legacy_monitor is None:
        _legacy_monitor = SystemMonitor()
    limit = settings.TOP_PROCESS_LIMIT if top_n is None else max(0, top_n)
    processes = sorted(_legacy_monitor.sample().processes,
                       key=lambda proc: proc.cpu_percent, reverse=True)
    return [dict(pid=proc.pid, name=proc.name or "unknown",
                 cpu_percent=proc.cpu_percent, memory_percent=round(proc.memory_percent, 2),
                 num_threads=proc.num_threads, status=proc.status)
            for proc in processes[:limit]]
