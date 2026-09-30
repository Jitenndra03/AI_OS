"""Pure, conservative optimization policy; all operating-system writes are elsewhere."""

from __future__ import annotations

import math
import os

from .models import Action, Category, Classification, Decision, SystemSnapshot, Workload


class DecisionEngine:
    """Select only the strategy explicitly authorized by a background rule.

    A suspension opt-in represents the user's assurance that pausing this exact
    executable is acceptable. Process metrics cannot prove absence of locks or
    external dependencies. Runtime checks still reject active IO and children.
    No decision kills a process, guesses GPU load, or escalates strategies.
    """

    def __init__(
        self,
        *,
        owner_uid: int | None = None,
        minimum_confidence: float = 0.9,
        min_cpu_percent: float = 10.0,
        min_memory_percent: float = 5.0,
        min_io_bytes_per_sec: float = 1024 * 1024,
        cpu_pressure_threshold: float = 75.0,
        memory_pressure_threshold: float = 85.0,
        psi_threshold: float = 10.0,
        min_cpu_reduction: float = 5.0,
        memory_headroom_ratio: float = 1.25,
    ) -> None:
        if not math.isfinite(minimum_confidence) or not 0 < minimum_confidence <= 1:
            raise ValueError("minimum_confidence must be in (0, 1]")
        values = (min_cpu_percent, min_memory_percent, min_io_bytes_per_sec,
                  cpu_pressure_threshold, memory_pressure_threshold, psi_threshold,
                  min_cpu_reduction)
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("Resource thresholds must be finite and positive")
        if not math.isfinite(memory_headroom_ratio) or memory_headroom_ratio < 1:
            raise ValueError("memory_headroom_ratio must be at least 1")
        self.owner_uid = os.getuid() if owner_uid is None else owner_uid
        self.minimum_confidence = minimum_confidence
        self.min_cpu_percent = min_cpu_percent
        self.min_memory_percent = min_memory_percent
        self.min_io_bytes_per_sec = min_io_bytes_per_sec
        self.cpu_pressure_threshold = cpu_pressure_threshold
        self.memory_pressure_threshold = memory_pressure_threshold
        self.psi_threshold = psi_threshold
        self.min_cpu_reduction = min_cpu_reduction
        self.memory_headroom_ratio = memory_headroom_ratio

    def decide(self, classification: Classification, snapshot: SystemSnapshot, workload: Workload) -> Decision:
        process = classification.process

        def decision(action: Action, reason: str, **kwargs) -> Decision:
            return Decision(process=process, classification=classification.category,
                            action=action, reason=reason, workload=workload.state, **kwargs)

        def noop(reason: str) -> Decision:
            return decision(Action.NOOP, reason)

        if not snapshot.complete:
            return noop("Incomplete observation; optimization is unsafe")
        observed = next((p for p in snapshot.processes if p.pid == process.pid), None)
        if observed is None or observed.identity != process.identity or observed != process:
            return noop("Classification is stale or process identity changed")
        if not workload.active:
            return noop("No sustained user workload requires resources")
        if not any(p.pid in workload.protected_pids and p.uid == self.owner_uid
                   and p.uid > 0 and p.pid > 1 and p.accessible and not p.kernel_thread
                   and p.status not in {"dead", "zombie"} for p in snapshot.processes):
            return noop("No protected user workload; background-only pressure is insufficient")
        if process.pid in workload.protected_pids or process.foreground:
            return noop("Process is protected foreground work or part of its process tree")
        if (process.pid <= 1 or process.uid <= 0 or process.uid != self.owner_uid
                or process.kernel_thread or not process.accessible):
            return noop("Process is system-owned, inaccessible, or outside the configured user")
        if process.status not in {"running", "sleeping", "disk-sleep", "idle"}:
            return noop("Process state is unknown, stopped, or no longer actionable")
        if classification.category not in {Category.BACKGROUND_SAFE, Category.BACKGROUND_OPTIONAL}:
            return noop("Only explicitly classified safe background processes are eligible")
        if (not math.isfinite(classification.confidence)
                or not self.minimum_confidence <= classification.confidence <= 1):
            return noop("Classification confidence is insufficient")
        rule = classification.rule
        if rule is None:
            return noop("No explicit executable opt-in rule")
        if (not os.path.isabs(rule.executable) or process.exe != rule.executable
                or rule.category != classification.category):
            return noop("Rule does not match the exact executable and background category")
        # Pressure must be attributable to a supported resource; CPU names and
        # GPU-oriented application names do not establish resource contention.
        cpu_pressure = (snapshot.cpu_percent >= self.cpu_pressure_threshold
                        or snapshot.cpu_pressure >= self.psi_threshold
                        or snapshot.load_average[0] >= max(1, snapshot.cpu_count))
        memory_pressure = (snapshot.memory_percent >= self.memory_pressure_threshold
                           or snapshot.memory_pressure >= self.psi_threshold)
        io_pressure = snapshot.io_pressure >= self.psi_threshold
        if not (cpu_pressure or memory_pressure or io_pressure):
            return noop("No measured CPU, memory, or IO contention")
        measurements = (process.cpu_percent, process.memory_percent, process.io_bytes_per_sec)
        if any(not math.isfinite(value) or value < 0 for value in measurements):
            return noop("Invalid process resource measurements")
        cpu_useful = cpu_pressure and process.cpu_percent >= self.min_cpu_percent
        memory_useful = memory_pressure and process.memory_percent >= self.min_memory_percent
        io_useful = io_pressure and process.io_bytes_per_sec >= self.min_io_bytes_per_sec
        if not (cpu_useful or memory_useful or io_useful):
            return noop("Target consumes too little of the constrained resource to justify a change")

        if rule.strategy == Action.RENICE:
            if not cpu_useful:
                return noop("Renicing does not address the measured memory or IO contention")
            if isinstance(rule.nice, bool) or not isinstance(rule.nice, int):
                return noop("Invalid configured nice value")
            if not isinstance(process.nice, int) or not -20 <= process.nice <= 19:
                return noop("Invalid observed scheduling priority")
            target = max(process.nice, min(19, max(-20, rule.nice)))
            if target == process.nice:
                return noop("Process already has equal or lower scheduling priority")
            return decision(Action.RENICE, "Explicit background opt-in; lower CPU priority during user workload",
                            new_nice=target)

        if rule.strategy in {Action.CGROUP, Action.CPU_LIMIT}:
            quota = rule.cpu_quota_percent
            if (isinstance(quota, bool) or not isinstance(quota, (float, int))
                    or not math.isfinite(quota) or quota <= 0):
                return noop("Invalid configured CPU quota")
            # Rules express one-CPU percentages, matching configuration and the
            # controller's cgroup period/quota calculation.
            quota = min(100.0, max(1.0, float(quota)))
            cpu_cap = (quota if cpu_useful and process.cpu_percent - quota >= self.min_cpu_reduction
                       else None)
            memory_high = None
            if rule.strategy == Action.CGROUP and rule.memory_high_bytes is not None:
                configured = rule.memory_high_bytes
                if isinstance(configured, bool) or not isinstance(configured, int) or configured <= 0:
                    return noop("Invalid configured memory.high value")
                if memory_useful and process.rss > 0:
                    if configured < math.ceil(process.rss * self.memory_headroom_ratio):
                        return noop("Configured memory.high lacks safe headroom above current RSS")
                    memory_high = configured
            if cpu_cap is None and memory_high is None:
                return noop("Configured limits offer no useful supported reduction for current contention")
            return decision(rule.strategy, "Apply explicitly selected background limits during resource contention",
                            cpu_quota_percent=cpu_cap, memory_high_bytes=memory_high)

        if rule.strategy == Action.SUSPEND:
            if not rule.allow_suspend:
                return noop("Suspension requires a separate explicit allow_suspend opt-in")
            if not cpu_useful:
                return noop("Suspension requires meaningful CPU relief; it does not release retained memory")
            if process.status not in {"running", "sleeping"}:
                return noop("Process is not in an ordinary running or sleeping state")
            if process.io_bytes_per_sec > 0 or process.read_bytes > 0 or process.write_bytes > 0:
                return noop("Process has IO activity or history; suspension safety cannot be established")
            if any(child.ppid == process.pid and child.create_time >= process.create_time
                   and child.status not in {"dead", "zombie"} for child in snapshot.processes):
                return noop("Process has active children that may depend on it")
            return decision(Action.SUSPEND, "Explicit suspension and lock-safety opt-in for a non-IO background task",
                            allow_suspend=True)
        return noop("Configured strategy is not an authorized optimization action")
