"""Deterministic workload detection from sustained, measured resource activity."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass

from .models import ProcessMetrics, SystemSnapshot, Workload, WorkloadState


_HINTS = (
    (WorkloadState.AI_ML, r"\b(torchrun|tensorflow|pytorch|jupyter|ollama|llama|training|train\.py|inference)\b"),
    (WorkloadState.COMPILATION, r"\b(gcc|g\+\+|cc1plus|cc1|clang\+\+|clang|rustc|cargo|make|ninja|cmake|javac|gradle)\b"),
    (WorkloadState.GAMING, r"\b(steam|steamwebhelper|wine|wine64|proton|gamescope|dota2|cs2|minecraft)\b"),
    (WorkloadState.EDITING, r"\b(blender|gimp|krita|inkscape|kdenlive|shotcut|davinci|resolve|ffmpeg|audacity)\b"),
    (WorkloadState.DEVELOPMENT, r"\b(code|codium|pycharm|idea|intellij|eclipse|neovim|nvim|vim|node|npm|webpack|vite|pytest)\b"),
)
_PATTERNS = tuple((state, re.compile(pattern, re.IGNORECASE)) for state, pattern in _HINTS)


@dataclass
class _Activity:
    state: WorkloadState
    root: ProcessMetrics
    cpu: float = 0.0
    memory: float = 0.0
    io: float = 0.0


class WorkloadDetector:
    """Recognize busy user process trees, never workloads from names alone.

    CPU percentages use the monitor's per-process convention (100 = one core).
    Memory is percent of physical RAM and IO is bytes per second. Sustaining a
    candidate and releasing an active workload both use snapshot.monotonic.
    Lower resource thresholds retain active work during brief activity dips.
    """

    def __init__(
        self,
        *,
        owner_uid: int | None = None,
        sustained_seconds: float = 15.0,
        release_seconds: float = 20.0,
        cpu_threshold: float = 50.0,
        memory_threshold: float = 15.0,
        io_threshold: float = 10 * 1024 * 1024,
        system_cpu_threshold: float = 75.0,
        system_memory_threshold: float = 85.0,
        pressure_threshold: float = 10.0,
        hysteresis_ratio: float = 0.65,
        background_executables: tuple[str, ...] | list[str] | set[str] = (),
    ) -> None:
        values = (cpu_threshold, memory_threshold, io_threshold,
                  system_cpu_threshold, system_memory_threshold, pressure_threshold)
        if any(not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError("Resource thresholds must be finite and positive")
        if any(not math.isfinite(v) or v < 0 for v in (sustained_seconds, release_seconds)):
            raise ValueError("Durations must be finite and nonnegative")
        if not 0 < hysteresis_ratio <= 1:
            raise ValueError("hysteresis_ratio must be in (0, 1]")
        self.owner_uid = os.getuid() if owner_uid is None else owner_uid
        self.sustained_seconds = sustained_seconds
        self.release_seconds = release_seconds
        self.cpu_threshold = cpu_threshold
        self.memory_threshold = memory_threshold
        self.io_threshold = io_threshold
        self.system_cpu_threshold = system_cpu_threshold
        self.system_memory_threshold = system_memory_threshold
        self.pressure_threshold = pressure_threshold
        self.hysteresis_ratio = hysteresis_ratio
        self.background_executables = frozenset(background_executables)
        self._pending: dict[tuple[WorkloadState, str], float] = {}
        self._active = Workload()
        self._active_roots: dict[str, int] = {}
        self._protected_identities: set[str] = set()
        self._last_time: float | None = None
        self._release_at: float | None = None

    @staticmethod
    def _hint(process: ProcessMetrics) -> WorkloadState | None:
        text = " ".join((os.path.basename(process.exe), process.name, *process.cmdline))
        return next((state for state, pattern in _PATTERNS if pattern.search(text)), None)

    def _eligible(self, process: ProcessMetrics) -> bool:
        return (process.uid == self.owner_uid and process.uid > 0
                and process.pid > 1 and process.accessible and not process.kernel_thread
                and process.status not in {"zombie", "dead"}
                and process.exe not in self.background_executables)

    @staticmethod
    def _parent(process: ProcessMetrics, processes: dict[int, ProcessMetrics]) -> ProcessMetrics | None:
        parent = processes.get(process.ppid)
        # A newly reused parent PID cannot be the ancestor of an older child.
        return parent if parent and parent.create_time <= process.create_time else None

    def _activities(self, processes: dict[int, ProcessMetrics]) -> list[_Activity]:
        groups: dict[tuple[WorkloadState, str], _Activity] = {}
        for process in processes.values():
            if not self._eligible(process):
                continue
            ancestors: set[int] = set()
            cursor = process
            while cursor is not None and cursor.pid not in ancestors:
                ancestors.add(cursor.pid)
                if cursor.exe in self.background_executables:
                    break
                cursor = self._parent(cursor, processes)
            if cursor is not None and cursor.exe in self.background_executables:
                continue
            state = self._hint(process)
            root = process
            cursor = process
            visited = {process.pid}
            while (parent := self._parent(cursor, processes)) is not None:
                if parent.pid in visited:
                    break
                if not self._eligible(parent):
                    break
                visited.add(parent.pid)
                hint = self._hint(parent)
                if hint or parent.foreground:
                    # Different explicit categories define a separate workload:
                    # a compiler launched by an editor remains compilation.
                    if state is not None and hint is not None and hint != state:
                        break
                    root = parent
                    state = state or hint
                cursor = parent
            state = state or WorkloadState.HEAVY_UNKNOWN
            key = (state, root.identity)
            activity = groups.setdefault(key, _Activity(state, root))
            activity.cpu += max(0.0, process.cpu_percent)
            activity.memory += max(0.0, process.memory_percent)
            activity.io += max(0.0, process.io_bytes_per_sec)
        return list(groups.values())

    def _busy(self, activity: _Activity, snapshot: SystemSnapshot, ratio: float = 1.0) -> bool:
        cpu_pressure = (snapshot.cpu_percent >= self.system_cpu_threshold * ratio
                        or snapshot.cpu_pressure >= self.pressure_threshold * ratio
                        or snapshot.load_average[0] >= max(1, snapshot.cpu_count) * ratio)
        memory_pressure = (snapshot.memory_percent >= self.system_memory_threshold * ratio
                           or snapshot.memory_pressure >= self.pressure_threshold * ratio)
        io_pressure = snapshot.io_pressure >= self.pressure_threshold * ratio
        # Large retained RSS alone is not evidence that an idle application is busy.
        return (activity.cpu >= self.cpu_threshold * ratio
                or activity.io >= self.io_threshold * ratio
                or (cpu_pressure and activity.cpu >= self.cpu_threshold * ratio / 2)
                or (io_pressure and activity.io >= self.io_threshold * ratio / 2)
                or (memory_pressure and activity.memory >= self.memory_threshold * ratio
                    and (activity.cpu >= 1.0 or activity.io >= 1024)))

    def _system_busy(self, snapshot: SystemSnapshot) -> bool:
        return (snapshot.cpu_percent >= self.system_cpu_threshold
                or snapshot.memory_percent >= self.system_memory_threshold
                or snapshot.cpu_pressure >= self.pressure_threshold
                or snapshot.memory_pressure >= self.pressure_threshold
                or snapshot.io_pressure >= self.pressure_threshold
                or snapshot.load_average[0] >= max(1, snapshot.cpu_count))

    def _protection(self, processes: dict[int, ProcessMetrics]) -> frozenset[int]:
        roots = [processes[pid] for identity, pid in self._active_roots.items()
                 if pid in processes and processes[pid].identity == identity]
        protected = {p.pid for p in processes.values() if p.identity in self._protected_identities}
        children: dict[int, list[ProcessMetrics]] = {}
        for process in processes.values():
            if self._parent(process, processes) is not None:
                children.setdefault(process.ppid, []).append(process)
        # Expand descendants from workload roots only; expanding from PID 1 or a
        # shared shell ancestor would incorrectly protect unrelated processes.
        descendants: set[int] = set()
        queue = list(roots)
        while queue:
            process = queue.pop()
            if process.pid in descendants:
                continue
            descendants.add(process.pid)
            queue.extend(children.get(process.pid, ()))
        protected.update(descendants)
        for root in roots:
            cursor = root
            visited = {root.pid}
            while (parent := self._parent(cursor, processes)) is not None:
                if parent.pid in visited:
                    break
                visited.add(parent.pid)
                protected.add(parent.pid)
                cursor = parent
        self._protected_identities = {processes[pid].identity for pid in protected}
        return frozenset(protected)

    def update(self, snapshot: SystemSnapshot) -> Workload:
        now = snapshot.monotonic
        if not math.isfinite(now):
            raise ValueError("Snapshot monotonic time must be finite")
        if self._last_time is not None and now < self._last_time:
            self._pending.clear()
            self._active = Workload()
            self._active_roots.clear()
            self._protected_identities.clear()
            self._release_at = None
        self._last_time = now
        processes = {process.pid: process for process in snapshot.processes}
        if not snapshot.complete:
            self._pending.clear()
            self._release_at = None
            return Workload(self._active.state, self._protection(processes),
                            "Incomplete sample; retaining previous workload", self._active.confidence,
                            self._active.since)

        activities = self._activities(processes)
        heavy = [activity for activity in activities if self._busy(activity, snapshot)]
        retained = [a for a in activities
                    if a.state == self._active.state and a.root.identity in self._active_roots
                    and self._busy(a, snapshot, self.hysteresis_ratio)]
        candidates = {(activity.state, activity.root.identity) for activity in heavy}
        # Global pressure without attributable user activity stays explicitly
        # unknown, with no protected workload roots for the policy to act upon.
        if not heavy and not retained and self._system_busy(snapshot):
            candidates.add((WorkloadState.HEAVY_UNKNOWN, "system"))
        self._pending = {key: self._pending.get(key, now) for key in candidates}
        mature = {key for key, started in self._pending.items()
                  if now - started >= self.sustained_seconds}
        if mature:
            # Prefer attributable, recognized user work over unattributed load.
            selected = [a for a in heavy if (a.state, a.root.identity) in mature]
            selected.sort(key=lambda a: (a.state != WorkloadState.HEAVY_UNKNOWN,
                                        a.root.foreground, a.cpu / self.cpu_threshold
                                        + a.io / self.io_threshold), reverse=True)
            state = selected[0].state if selected else WorkloadState.HEAVY_UNKNOWN
            chosen = [a for a in selected if a.state == state]
            if state != self._active.state or not selected:
                self._protected_identities.clear()
            self._active_roots = {a.root.identity: a.root.pid for a in selected}
            since = self._active.since if self._active.state == state else now
            reason = ("Sustained measured user process activity" if chosen
                      else "Sustained system pressure without an attributable user workload")
            self._active = Workload(state, reason=reason, confidence=0.9 if chosen else 0.4, since=since)
            self._release_at = None
        elif self._active.active and not retained:
            if self._release_at is None:
                self._release_at = now
            if now - self._release_at >= self.release_seconds:
                self._active = Workload(since=now)
                self._active_roots.clear()
                self._protected_identities.clear()
                self._release_at = None
        else:
            self._release_at = None
        self._active = Workload(self._active.state, self._protection(processes),
                                self._active.reason, self._active.confidence, self._active.since)
        return self._active
