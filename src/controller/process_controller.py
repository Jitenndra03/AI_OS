"""Journaled, reversible Linux resource control of explicitly opted-in tasks.

Historical CSV rows and anomaly scores never authorize a process mutation.
"""

from dataclasses import replace
import math
import os
from pathlib import Path
import resource
import time

import psutil

from src.controller.cgroups import CgroupExistsError, CgroupV2
from src.config import settings
from src.monitor.metrics_collector import SystemMonitor
from src.optimization.models import Action, ActionRecord, Category, ControlResult, Decision
from src.optimization.safety import protection_reason


class LinuxBackend:
    def __init__(self):
        self.monitor = SystemMonitor()

    def process(self, pid):
        return psutil.Process(pid)

    def metrics(self, proc):
        if not proc.is_running():
            raise psutil.NoSuchProcess(proc.pid)
        result = self.monitor._sample_process(proc, time.monotonic(), None, True)
        # Recheck terminal dependencies at execution time, including children
        # and ancestors that became foreground after the planning snapshot.
        for relative in [*proc.parents(), *proc.children(recursive=True)]:
            try:
                if not relative.is_running():
                    continue
                stat = (self.monitor.proc_root / str(relative.pid) / "stat").read_text()
                fields = stat[stat.rindex(")") + 2:].split()
                relative_session = int(fields[3])
                if result.session_id > 0 and relative_session > 0 and relative_session != result.session_id:
                    continue
                pgrp, tty, tpgid = map(int, (fields[2], fields[4], fields[5]))
                if tty != 0 and tpgid > 0 and pgrp == tpgid and relative.is_running():
                    result = replace(result, foreground=True)
            except (FileNotFoundError, psutil.NoSuchProcess):
                continue
        if not proc.is_running():
            raise psutil.NoSuchProcess(proc.pid)
        return result

    def self_ancestry(self):
        me = psutil.Process()
        return {me.pid, *(p.pid for p in me.parents())}

    def can_restore_nice(self, proc, original):
        # CAP_SYS_NICE is bit 23. EUID 0 alone does not prove the capability
        # inside containers. RLIMIT_NICE permits raising to 20 - soft limit.
        status = Path("/proc/self/status").read_text().splitlines()
        for line in status:
            if line.startswith("CapEff:") and int(line.split()[1], 16) & (1 << 23):
                return True
        soft, _ = proc.rlimit(resource.RLIMIT_NICE)
        return soft == resource.RLIM_INFINITY or soft >= 20 - original


class ResourceController:
    def __init__(self, store, owner_uid, *, cgroup_root=None, allow_suspend=False,
                 backend=None, cgroups=None):
        self.store = store
        self.owner_uid = owner_uid
        self.protected_pids: set[int] = set()
        self.allow_suspend = allow_suspend
        self.backend = backend or LinuxBackend()
        self.cgroup_root = cgroup_root
        self.cgroups = cgroups

    def _groups(self):
        if self.cgroups is None:
            if self.cgroup_root is None:
                raise RuntimeError("No explicitly delegated cgroup root configured")
            self.cgroups = CgroupV2(self.cgroup_root, self.owner_uid)
        return self.cgroups

    @staticmethod
    def _matches(live, expected):
        exe = expected.exe if hasattr(expected, "exe") else expected.executable
        return (live.pid == expected.pid and live.create_time == expected.create_time
                and live.uid == expected.uid and live.exe == exe)

    def _live(self, expected, *, restoring=False):
        proc = self.backend.process(expected.pid)
        live = self.backend.metrics(proc)
        if not self._matches(live, expected):
            raise RuntimeError("Process identity, executable, or owner changed")
        self._safe(live, restoring=restoring)
        return proc, live

    def _safe(self, live, *, restoring=False):
        protected = set(self.backend.self_ancestry())
        if not restoring:
            protected.update(self.protected_pids)
        reason = protection_reason(replace(live, foreground=False) if restoring else live,
                                   owner_uid=self.owner_uid, protected_pids=protected)
        if reason:
            raise RuntimeError(reason)

    @staticmethod
    def _suspend_safe(proc, live):
        if (live.status not in {"running", "sleeping"} or proc.children()
                or live.io_bytes_per_sec > 0 or live.read_bytes > 0 or live.write_bytes > 0):
            raise ValueError("Suspension requires an ordinary non-IO process without children")

    @staticmethod
    def _memory_safe(decision, live):
        high = decision.memory_high_bytes
        if high is not None:
            ratio = getattr(settings, "OPTIMIZER_MEMORY_HEADROOM_RATIO", 1.25)
            if type(high) is not int or high <= 0 or high < math.ceil(live.rss * ratio):
                raise ValueError("memory.high lacks safe headroom above live RSS")

    def _persist(self, record, phase):
        record.phase = phase
        record.error = None
        self.store.put(record)

    def _failed(self, record, exc):
        message = str(exc)
        if record is not None:
            record.phase = "failed"
            record.error = message
            try:
                self.store.put(record)
            except Exception as persistence_error:
                message += f"; journal update failed: {persistence_error}"
        return ControlResult(False, message, record.identity if record else "")

    def apply(self, decision: Decision) -> ControlResult:
        record = None
        identity = decision.process.identity if isinstance(decision, Decision) else ""
        try:
            if (not isinstance(decision, Decision) or not isinstance(decision.action, Action)
                    or not isinstance(decision.classification, Category)):
                raise ValueError("A typed policy decision is required")
            if decision.action == Action.NOOP:
                return ControlResult(True, decision.reason, identity)
            if decision.action == Action.RESTORE:
                return self.restore(identity)
            if decision.classification not in {Category.BACKGROUND_SAFE, Category.BACKGROUND_OPTIONAL}:
                raise ValueError("Only explicitly opted-in background processes may be changed")
            if identity in self.store.records:
                raise ValueError("Process already has journaled state; restore before another action")
            proc, live = self._live(decision.process)
            if decision.action == Action.RENICE:
                if type(decision.new_nice) is not int or not live.nice <= decision.new_nice <= 19:
                    raise ValueError("Renice must lower priority within the Linux nice range")
                if decision.new_nice == live.nice:
                    return ControlResult(True, "Nice value already matches", identity)
                if not self.backend.can_restore_nice(proc, live.nice):
                    raise PermissionError("CAP_SYS_NICE or sufficient RLIMIT_NICE is required for restoration")
            elif decision.action == Action.SUSPEND:
                if not (self.allow_suspend and decision.allow_suspend
                        and decision.classification == Category.BACKGROUND_OPTIONAL):
                    raise PermissionError("Suspension requires global and per-rule explicit opt-in")
                self._suspend_safe(proc, live)
            elif decision.action in {Action.CGROUP, Action.CPU_LIMIT}:
                if decision.cpu_quota_percent is None and decision.memory_high_bytes is None:
                    raise ValueError("Cgroup control requires a CPU quota or memory.high")
                if (decision.cpu_quota_percent is not None
                        and (type(decision.cpu_quota_percent) not in (int, float)
                             or not math.isfinite(decision.cpu_quota_percent)
                             or not 1 <= decision.cpu_quota_percent <= 100)):
                    raise ValueError("CPU quota must be finite and between 1 and 100 percent")
                self._memory_safe(decision, live)
                self._groups().source_path(live.cgroup)
                self._groups().assert_available(identity)
            else:
                raise ValueError("Unsupported action")
            record = ActionRecord(
                identity=identity, pid=live.pid, create_time=live.create_time,
                executable=live.exe, uid=live.uid, original_nice=live.nice,
                original_cgroup=live.cgroup, original_status=live.status,
                action=decision.action.value, timestamp=time.time(), workload=decision.workload.value,
                boot_id=self.store.boot_id, applied_nice=decision.new_nice if decision.action == Action.RENICE else None,
                managed_cgroup=self._groups().managed_path(identity)
                if decision.action in {Action.CGROUP, Action.CPU_LIMIT} else None,
            )
            self._persist(record, "pending")
            if decision.action == Action.RENICE:
                proc, current = self._live(record)
                if current.nice != record.original_nice:
                    raise RuntimeError("Nice value changed externally before apply")
                proc.nice(record.applied_nice)  # psutil setter checks PID reuse.
            elif decision.action == Action.SUSPEND:
                proc, current = self._live(record)
                self._suspend_safe(proc, current)
                proc.suspend()  # psutil uses a guarded SIGSTOP.
            else:
                groups = self._groups()
                pending = lambda: self._persist(record, "pending")
                groups.prepare(record, decision, pending)
                proc, current = self._live(record)
                if current.cgroup != record.original_cgroup:
                    raise RuntimeError("Cgroup changed externally before apply")
                self._memory_safe(decision, current)
                groups.move(proc, record.managed_cgroup, pending)
            self._persist(record, "applied")
            return ControlResult(True, "Applied and journaled", identity, True)
        except Exception as exc:
            if isinstance(exc, CgroupExistsError) and record is not None:
                # No group was created and no process mutation occurred. Do
                # not let a pending intent claim an unrelated existing group.
                try:
                    self.store.remove(record.identity)
                    record = None
                except Exception as remove_error:
                    # Keep the explicit conflict marker so recovery refuses
                    # to move any occupants if journal removal also fails.
                    exc = CgroupExistsError(f"Unowned cgroup conflict: {exc}; {remove_error}")
            result = self._failed(record, exc)
            return replace(result, identity=identity)

    def _restore_group(self, record):
        groups = self._groups()
        if record.error and record.error.startswith("Unowned cgroup conflict:"):
            raise RuntimeError(record.error)
        if record.managed_cgroup != groups.managed_path(record.identity):
            raise RuntimeError("Journaled cgroup does not match this process identity")
        source = groups.source_path(record.original_cgroup)
        pending = lambda: self._persist(record, "restore_pending")
        changed = False
        # Include children born in this group even after the original PID exits.
        # A single bounded pass avoids racing an unbounded stream of forks;
        # occupied groups retain their journal and are retried later.
        for pid in groups.members(record.managed_cgroup):
            try:
                proc = self.backend.process(pid)
                live = self.backend.metrics(proc)
            except psutil.NoSuchProcess:
                continue
            self._safe(live, restoring=True)
            if pid == record.pid and not self._matches(live, record):
                raise RuntimeError("Original PID was reused; managed group retained")
            if live.cgroup != groups.current_path(record.managed_cgroup):
                continue  # Moved externally; never pull it back.
            pending()
            checked = self.backend.metrics(proc)
            if not self._matches(checked, live) or checked.cgroup != live.cgroup:
                raise RuntimeError("Cgroup occupant changed before restoration")
            self._safe(checked, restoring=True)
            groups.move(proc, source, pending)
            changed = True
        groups.cleanup(record.managed_cgroup, pending)
        return changed

    def restore(self, identity) -> ControlResult:
        record = self.store.records.get(identity)
        if record is None:
            return ControlResult(True, "No journaled action", identity)
        try:
            if record.boot_id != self.store.boot_id:
                self.store.remove(identity)
                return ControlResult(True, "Discarded record from an earlier boot", identity)
            if record.action in {Action.CGROUP.value, Action.CPU_LIMIT.value}:
                changed = self._restore_group(record)
            else:
                try:
                    proc = self.backend.process(record.pid)
                    live = self.backend.metrics(proc)
                except psutil.NoSuchProcess:
                    self.store.remove(identity)
                    return ControlResult(True, "Original process exited", identity)
                if live.pid != record.pid or live.create_time != record.create_time:
                    self.store.remove(identity)
                    return ControlResult(True, "Original identity no longer exists; no process changed", identity)
                if not self._matches(live, record):
                    raise RuntimeError("Original process changed executable or owner; journal retained")
                self._safe(live, restoring=True)
                changed = False
                if record.action == Action.RENICE.value:
                    if live.nice == record.original_nice:
                        pass
                    elif live.nice != record.applied_nice:
                        raise RuntimeError("Nice value was changed externally; journal retained")
                    else:
                        if not self.backend.can_restore_nice(proc, record.original_nice):
                            raise PermissionError("Nice restoration permission is no longer available")
                        self._persist(record, "restore_pending")
                        proc, current = self._live(record, restoring=True)
                        if current.nice != record.applied_nice:
                            raise RuntimeError("Nice value changed before restoration")
                        proc.nice(record.original_nice)
                        changed = True
                elif record.action == Action.SUSPEND.value:
                    if live.status == "stopped":
                        self._persist(record, "restore_pending")
                        proc, current = self._live(record, restoring=True)
                        if current.status == "stopped":
                            proc.resume()  # psutil uses guarded SIGCONT.
                            changed = True
                    elif live.status == "tracing-stop":
                        raise RuntimeError("Debugger owns the stop; journal retained")
                else:
                    raise ValueError("Unknown journal action")
            self.store.remove(identity)
            return ControlResult(True, "Original resources restored", identity, changed)
        except Exception as exc:
            return self._failed(record, exc)

    def restore_all(self) -> list[ControlResult]:
        return [self.restore(identity) for identity in list(self.store.records)]


def enforce(*_args, **_kwargs):
    """Retired CSV controller: deliberately refuses historical process actions."""
    raise RuntimeError("Historical CSV enforcement is disabled; use the live optimization engine")


def renice_process(*_args, **_kwargs):
    raise RuntimeError("PID-only renice is disabled; use a journaled typed decision")
