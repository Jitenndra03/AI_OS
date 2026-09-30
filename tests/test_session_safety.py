"""Session detachments never weaken controller/workload or unknown-session protection."""
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.controller.process_controller import LinuxBackend, ResourceController
from src.monitor.metrics_collector import SystemMonitor
from src.optimization.classification import ProcessClassifier
from src.optimization.engine import OptimizationEngine
from src.optimization.models import (
    BackgroundRule, Category, ProcessMetrics, SystemSnapshot, Workload, WorkloadState,
)
from src.optimization.safety import protected_tree


def process(pid, ppid=10, session=100, **kwargs):
    return replace(ProcessMetrics(pid, float(pid), ppid=ppid, uid=1000, name="worker",
                                  exe="/opt/session-test-worker", status="running", session_id=session), **kwargs)


def snapshot(*processes):
    return SystemSnapshot(1, 1, processes=processes)


def test_default_tree_protects_descendants_across_detached_sessions():
    observation = snapshot(process(10, ppid=1), process(20, session=200),
                           process(30, ppid=20, session=300), process(40, ppid=1, session=400))
    protected = protected_tree(observation, {10})
    assert {1, 10, 20, 30} <= protected
    assert 40 not in protected  # Ancestors do not expand their sibling branches.


def test_respect_sessions_detaches_only_the_known_different_branch():
    observation = snapshot(process(10, ppid=1), process(20), process(30, session=300),
                           process(40, ppid=30, session=300), process(50, session=0))
    protected = protected_tree(observation, {10}, respect_sessions=True)
    assert {1, 10, 20, 50} <= protected
    assert not ({30, 40} & protected)


@pytest.mark.parametrize("parent_session,child_session", [(0, 100), (100, 0), (0, 0), (100, 100)])
def test_unknown_or_matching_sessions_retain_descendant_protection(parent_session, child_session):
    observation = snapshot(process(10, ppid=1, session=parent_session), process(20, session=child_session))
    assert 20 in protected_tree(observation, {10}, respect_sessions=True)


def test_missing_seed_metadata_preserves_unknown_descendant_relation():
    observation = snapshot(process(20, ppid=10, session=200))
    assert 20 in protected_tree(observation, {10}, respect_sessions=True)


def test_foreground_classification_respects_detachment_but_requires_exact_opt_in():
    observation = snapshot(process(10, ppid=1, foreground=True), process(20),
                           process(30, session=300), process(40, session=0),
                           process(50, session=500, exe="/opt/not-opted-in"))
    classifier = ProcessClassifier((BackgroundRule("/opt/session-test-worker"),), owner_uid=1000, self_pid=900)
    categories = {item.process.pid: item.category for item in classifier.classify(observation, Workload())}
    assert categories[10] == categories[20] == categories[40] == Category.USER_FOREGROUND
    assert categories[30] == Category.BACKGROUND_SAFE
    assert categories[50] == Category.UNKNOWN


def test_controller_descendants_remain_protected_after_setsid():
    observation = snapshot(process(900, ppid=1), process(901, ppid=900, session=901),
                           process(902, ppid=901, session=902))
    classifier = ProcessClassifier((BackgroundRule("/opt/session-test-worker"),), owner_uid=1000, self_pid=900)
    categories = {item.process.pid: item.category for item in classifier.classify(observation, Workload())}
    assert categories[900] == Category.CRITICAL_SYSTEM
    assert categories[901] == categories[902] == Category.USER_IMPORTANT


def test_engine_preserves_controller_and_workload_beyond_foreground_session_boundary():
    observation = snapshot(process(10, ppid=1, foreground=True), process(20, session=20),
                           process(30, session=30), process(900, ppid=1), process(901, ppid=900, session=901))
    workload = Workload(WorkloadState.COMPILATION, frozenset({30}))
    controller = SimpleNamespace(store=SimpleNamespace(records={}), protected_pids=set())
    engine = OptimizationEngine(SimpleNamespace(update=lambda _: workload),
                                SimpleNamespace(classify=lambda *_: []), Mock(), controller,
                                clock=lambda: 1, self_pid=900)
    engine.cycle(observation)
    assert {10, 30, 900, 901} <= controller.protected_pids
    assert 20 not in controller.protected_pids


def write_stat(root, pid, session, foreground=True):
    directory = root / str(pid)
    directory.mkdir(exist_ok=True)
    directory.joinpath("stat").write_text(
        f"{pid} (worker (name)) R 10 100 {session} 34816 {100 if foreground else 101} 0 0 0\n")
    directory.joinpath("cgroup").write_text("0::/user.slice/session.scope\n")


@pytest.mark.parametrize("relation", ["parent", "child"])
@pytest.mark.parametrize("target_session,relative_session,expected", [
    (100, 100, True), (100, 200, False), (100, 0, True), (0, 100, True), (0, 0, True),
])
def test_execution_rechecks_foreground_relatives_with_conservative_unknown_sessions(
        tmp_path, relation, target_session, relative_session, expected):
    target = Mock(pid=20)
    relative = Mock(pid=10 if relation == "parent" else 30)
    target.is_running.return_value = relative.is_running.return_value = True
    target.parents.return_value = [relative] if relation == "parent" else []
    target.children.return_value = [relative] if relation == "child" else []
    write_stat(tmp_path, relative.pid, relative_session)
    backend = LinuxBackend.__new__(LinuxBackend)
    backend.monitor = SimpleNamespace(proc_root=tmp_path,
        _sample_process=lambda *_: process(20, session=target_session))
    assert backend.metrics(target).foreground is expected


def test_controller_live_guard_cannot_be_bypassed_by_new_session():
    live = process(901, ppid=900, session=901)
    backend = SimpleNamespace(self_ancestry=lambda: {900})
    controller = ResourceController(SimpleNamespace(), owner_uid=1000, backend=backend)
    controller.protected_pids = {901}
    with pytest.raises(RuntimeError, match="Foreground/workload/AI_OS dependency"):
        controller._safe(live)


def test_monitor_reads_session_after_parenthesized_comm_and_retains_unknown_on_failure(tmp_path):
    proc = Mock(pid=42)
    proc.oneshot.side_effect = nullcontext
    proc.create_time.return_value = 42.0
    proc.cpu_percent.return_value = 0
    proc.uids.return_value = SimpleNamespace(real=1000, effective=1000, saved=1000)
    proc.memory_info.return_value = SimpleNamespace(rss=4096)
    proc.io_counters.return_value = SimpleNamespace(read_bytes=0, write_bytes=0)
    proc.ppid.return_value = 10
    proc.name.return_value = "worker (name)"
    proc.exe.return_value = "/opt/session-test-worker"
    proc.cmdline.return_value = []
    proc.memory_percent.return_value = 0
    proc.num_threads.return_value = 1
    proc.status.return_value = "running"
    proc.nice.return_value = 0
    write_stat(tmp_path, 42, 123)
    monitor = SystemMonitor(tmp_path)
    metrics = monitor._sample_process(proc, 1, None, True)
    assert metrics.session_id == 123 and metrics.foreground and metrics.accessible
    (tmp_path / "42" / "stat").write_text("invalid stat")
    metrics = monitor._sample_process(proc, 2, None, False)
    assert metrics.session_id == 0 and not metrics.accessible
