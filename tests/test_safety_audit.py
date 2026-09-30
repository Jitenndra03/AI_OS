"""Safety regressions across classification, planning and journal retention."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.optimization.classification import ProcessClassifier
from src.optimization.decisions import DecisionEngine
from src.optimization.engine import OptimizationEngine
from src.optimization.models import (
    Action, ActionRecord, BackgroundRule, Category, ProcessMetrics,
    SystemSnapshot, Workload, WorkloadState,
)
from src.optimization.safety import protection_reason


def fixture_engine():
    target = ProcessMetrics(400, 10.0, ppid=100, uid=1000, name="worker",
                            exe="/opt/worker", status="stopped", cpu_percent=0)
    compiler = ProcessMetrics(500, 11.0, ppid=100, uid=1000, name="gcc",
                              exe="/usr/bin/gcc", status="running", cpu_percent=90)
    snapshot = SystemSnapshot(100, 100, processes=(target, compiler), cpu_percent=90)
    workload = Workload(WorkloadState.COMPILATION, frozenset({500}))
    detector = Mock()
    detector.update.return_value = workload
    record = ActionRecord(target.identity, target.pid, target.create_time, target.exe,
                          target.uid, 0, "/", "running", "SUSPEND", 99, "COMPILATION",
                          phase="applied")
    controller = Mock()
    controller.store = SimpleNamespace(records={target.identity: record})
    rule = BackgroundRule("/opt/worker", Category.BACKGROUND_OPTIONAL,
                          Action.SUSPEND, allow_suspend=True)
    classifier = ProcessClassifier((rule,), owner_uid=1000, self_pid=900)
    engine = OptimizationEngine(detector, classifier, DecisionEngine(owner_uid=1000),
                                controller, clock=lambda: 100, self_pid=900)
    return engine, controller, detector, snapshot


def test_journaled_suspension_retained_while_workload_continues():
    engine, controller, _, snapshot = fixture_engine()
    result = engine.cycle(snapshot)
    controller.restore.assert_not_called()
    controller.apply.assert_not_called()
    assert all(item.action == Action.NOOP for item in result.decisions)


def test_existing_stopped_task_never_receives_new_suspend():
    engine, controller, _, snapshot = fixture_engine()
    controller.store.records = {}
    engine.cycle(snapshot)
    controller.apply.assert_not_called()
    controller.restore.assert_not_called()


def test_journaled_suspension_restored_when_workload_finishes():
    engine, controller, detector, snapshot = fixture_engine()
    detector.update.return_value = Workload()
    engine.cycle(snapshot)
    controller.restore.assert_called_once_with(snapshot.processes[0].identity)
    controller.apply.assert_not_called()


def test_journaled_suspension_restored_when_target_becomes_foreground():
    engine, controller, _, snapshot = fixture_engine()
    target, compiler = snapshot.processes
    snapshot = replace(snapshot, processes=(replace(target, foreground=True), compiler))
    engine.cycle(snapshot)
    controller.restore.assert_called_once_with(target.identity)
    controller.apply.assert_not_called()


@pytest.mark.parametrize("changes", [{"pid": 1}, {"ppid": 2}, {"kernel_thread": True},
    {"uid": 0}, {"uid": 1001}, {"name": "sshd"}, {"exe": "/usr/sbin/sshd"},
    {"name": "gnome-shell"}, {"cgroup": "/system.slice/network.service"}])
def test_current_structural_protections_apply_to_stopped_processes(changes):
    process = replace(ProcessMetrics(400, 10, uid=1000, name="worker", exe="/opt/worker",
                                     status="stopped"), **changes)
    assert protection_reason(process, owner_uid=1000, self_pid=900) is not None
