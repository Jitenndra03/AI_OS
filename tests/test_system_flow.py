"""Real optimizer components with an in-memory Linux process backend."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from src.controller.process_controller import ResourceController
from src.optimization.classification import ProcessClassifier
from src.optimization.decisions import DecisionEngine
from src.optimization.engine import OptimizationEngine
from src.optimization.models import Action, BackgroundRule, Category, ProcessMetrics, SystemSnapshot, WorkloadState
from src.optimization.state import StateStore
from src.optimization.workload import WorkloadDetector

class FakeProcess:
    def __init__(self, metrics):
        self.metrics = metrics
        self.pid = metrics.pid
        self.writes = []
    def nice(self, value=None):
        if value is None:
            return self.metrics.nice
        self.writes.append(("nice", value))
        self.metrics = replace(self.metrics, nice=value)
    def children(self):
        return []
    def suspend(self):
        self.writes.append(("suspend",))
        self.metrics = replace(self.metrics, status="stopped", cpu_percent=0)
    def resume(self):
        self.writes.append(("resume",))
        self.metrics = replace(self.metrics, status="running")

@pytest.fixture
def pipeline(tmp_path):
    stores = []
    def build(*, suspension=False, with_controller=True):
        user = ProcessMetrics(20, 2, uid=1000, name="gcc", exe="/usr/bin/gcc",
                              foreground=True, cpu_percent=80, status="running")
        background = FakeProcess(ProcessMetrics(30, 3, uid=1000, name="batch", exe="/opt/batch",
                                               cpu_percent=70, status="running"))
        processes = {30: background}
        backend = SimpleNamespace(process=lambda pid: processes[pid], metrics=lambda p: p.metrics,
                                  self_ancestry=lambda: {99999}, can_restore_nice=Mock(return_value=True))
        store = StateStore(tmp_path / f"state{len(stores)}.json").acquire()
        stores.append(store)
        controller = ResourceController(store, 1000, backend=backend, allow_suspend=suspension)
        controller.apply = Mock(wraps=controller.apply)
        rule = BackgroundRule("/opt/batch", category=Category.BACKGROUND_OPTIONAL if suspension else Category.BACKGROUND_SAFE,
                              strategy=Action.SUSPEND if suspension else Action.RENICE, allow_suspend=suspension)
        clock = [0]
        engine = OptimizationEngine(
            WorkloadDetector(owner_uid=1000, sustained_seconds=3, release_seconds=5,
                             background_executables=("/opt/batch",)),
            ProcessClassifier((rule,), owner_uid=1000, self_pid=99999), DecisionEngine(owner_uid=1000),
            controller if with_controller else None, clock=lambda: clock[0], self_pid=99999)
        def step(now, *, busy=True, complete=True, age=0, extra=()):
            clock[0] = now + age
            snapshot = SystemSnapshot(timestamp=1000+now, monotonic=now, complete=complete,
                cpu_percent=90 if busy else 0,
                processes=(replace(user, cpu_percent=80 if busy else 0), background.metrics, *extra))
            return engine.cycle(snapshot)
        return SimpleNamespace(engine=engine, step=step, store=store, background=background,
                               controller=controller, backend=backend, processes=processes)
    yield build
    for store in stores:
        store.close()

def test_heavy_start_apply_repeat_end_restore(pipeline):
    flow = pipeline()
    assert flow.step(0).workload.state == WorkloadState.NORMAL
    assert not flow.background.writes
    result = flow.step(3)
    assert result.workload.state == WorkloadState.COMPILATION
    assert flow.background.writes == [("nice", 10)]
    assert len(flow.store.records) == 1
    flow.step(4)
    assert flow.controller.apply.call_count == 1
    flow.background.metrics = replace(flow.background.metrics, cpu_percent=0)
    assert flow.step(5, busy=False).workload.active
    assert flow.step(10, busy=False).workload.state == WorkloadState.NORMAL
    assert flow.background.writes == [("nice", 10), ("nice", 0)]
    assert not flow.store.records

def test_same_observed_identity_cannot_receive_duplicate_apply_calls(pipeline):
    flow = pipeline()
    flow.step(0)
    original = flow.background.metrics
    flow.step(3)
    flow.background.metrics = original  # Simulate unchanged measured nice value.
    flow.step(4)
    assert flow.controller.apply.call_count == 1
    assert len(flow.store.records) == 1

@pytest.mark.parametrize("kwargs", [{"complete": False}, {"age": 16}, {"age": -1}])
def test_stale_incomplete_or_future_sample_restores_before_planning(pipeline, kwargs):
    flow = pipeline()
    flow.step(0)
    flow.step(3)
    result = flow.step(4, **kwargs)
    assert not result.decisions
    assert flow.background.writes == [("nice", 10), ("nice", 0)]
    assert not flow.store.records

def test_background_becoming_foreground_is_restored_immediately(pipeline):
    flow = pipeline()
    flow.step(0)
    flow.step(3)
    flow.background.metrics = replace(flow.background.metrics, foreground=True)
    assert flow.step(4).workload.active
    assert flow.background.writes == [("nice", 10), ("nice", 0)]
    assert not flow.store.records

def test_suspended_background_stays_suspended_until_workload_ends(pipeline):
    flow = pipeline(suspension=True)
    flow.step(0)
    flow.step(3)
    assert flow.background.writes == [("suspend",)]
    flow.step(4)
    assert flow.background.writes == [("suspend",)]
    flow.step(5, busy=False)
    flow.step(10, busy=False)
    assert flow.background.writes == [("suspend",), ("resume",)]
    assert not flow.store.records

def test_observation_only_proposes_without_invoking_controller(pipeline):
    flow = pipeline(with_controller=False)
    flow.step(0)
    result = flow.step(3)
    assert any(item.action == Action.RENICE for item in result.decisions)
    assert not flow.background.writes and not flow.store.records


def test_incomplete_observation_breaks_pending_sustain_evidence(pipeline):
    flow = pipeline()
    flow.step(0)
    flow.step(2, complete=False)
    assert not flow.step(3).workload.active
    assert flow.step(6).workload.active
    assert flow.background.writes == [("nice", 10)]


def test_failed_restoration_blocks_changes_to_other_background_processes(pipeline):
    flow = pipeline()
    flow.step(0)
    flow.step(3)
    flow.background.metrics = replace(flow.background.metrics, foreground=True)
    flow.backend.can_restore_nice.return_value = False
    other = FakeProcess(replace(flow.background.metrics, pid=40, create_time=4,
                                foreground=False, nice=0, cpu_percent=70))
    flow.processes[40] = other
    result = flow.step(4, extra=(other.metrics,))
    assert any(not item.success for item in result.results)
    assert any(item.process.pid == 40 and item.action == Action.RENICE for item in result.decisions)
    assert not other.writes
    assert flow.controller.apply.call_count == 1
    assert flow.store.records
