"""One ordered control cycle; historical anomaly scores never authorize actions."""

import os
import time
from dataclasses import dataclass, replace

from src.optimization.models import Action, Category, Classification, Decision, SystemSnapshot, Workload
from src.optimization.safety import protected_tree


@dataclass(frozen=True)
class CycleResult:
    workload: Workload
    decisions: tuple[Decision, ...]
    results: tuple


class OptimizationEngine:
    def __init__(self, detector, classifier, planner, controller=None,
                 *, max_snapshot_age=15.0, clock=time.monotonic, self_pid=None):
        self.detector = detector
        self.classifier = classifier
        self.planner = planner
        self.controller = controller
        self.max_snapshot_age = max_snapshot_age
        self.clock = clock
        self.self_pid = os.getpid() if self_pid is None else self_pid

    def cycle(self, snapshot: SystemSnapshot) -> CycleResult:
        age = self.clock() - snapshot.monotonic
        if not snapshot.complete or not 0 <= age <= self.max_snapshot_age:
            self.detector.update(replace(snapshot, complete=False))
            results = tuple(self.controller.restore_all()) if self.controller else ()
            return CycleResult(Workload(reason="Incomplete or stale observation; restoring"), (), results)

        workload = self.detector.update(snapshot)
        classifications = self.classifier.classify(snapshot, workload)
        decisions = tuple(self.planner.decide(item, snapshot, workload) for item in classifications)
        if self.controller is None:
            return CycleResult(workload, decisions, ())

        protected = protected_tree(snapshot, {self.self_pid}) | workload.protected_pids
        foreground = {p.pid for p in snapshot.processes if p.foreground}
        protected |= protected_tree(snapshot, foreground, respect_sessions=True)
        self.controller.protected_pids = protected

        # A target becoming foreground/important is restored immediately, even
        # if the original heavy workload is still running.
        eligible = {c.process.identity for c in classifications
                    if c.category in {Category.BACKGROUND_SAFE, Category.BACKGROUND_OPTIONAL}}
        results = []
        for identity, record in self.controller.store.records.items():
            if not workload.active or identity not in eligible or record.phase != "applied":
                results.append(self.controller.restore(identity))

        if any(not result.success for result in results):
            return CycleResult(workload, decisions, tuple(results))
        for decision in decisions:
            if (decision.action not in {Action.NOOP, Action.RESTORE}
                    and decision.process.identity not in self.controller.store.records):
                applied = self.controller.apply(decision)
                results.append(applied)
                if not applied.success:
                    break  # Attempt recovery before authorizing further changes.
        return CycleResult(workload, decisions, tuple(results))
