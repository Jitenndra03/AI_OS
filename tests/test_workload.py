"""Synthetic snapshots exercise detector timing without sleeps or host load."""

from dataclasses import replace

import pytest

from src.optimization.models import ProcessMetrics, SystemSnapshot, WorkloadState
from src.optimization.workload import WorkloadDetector


def process(pid=10, name="worker", **kwargs):
    values = dict(pid=pid, create_time=float(pid), uid=1000, name=name,
                  exe=f"/usr/bin/{name}", cpu_percent=80.0)
    values.update(kwargs)
    return ProcessMetrics(**values)


def snapshot(now, *processes, **kwargs):
    return SystemSnapshot(timestamp=10_000 + now, monotonic=now, processes=processes, **kwargs)


def detector(**kwargs):
    return WorkloadDetector(owner_uid=1000, sustained_seconds=3, release_seconds=5, **kwargs)


@pytest.mark.parametrize("name,state", [
    ("code", WorkloadState.DEVELOPMENT),
    ("gcc", WorkloadState.COMPILATION),
    ("dota2", WorkloadState.GAMING),
    ("blender", WorkloadState.EDITING),
    ("torchrun", WorkloadState.AI_ML),
    ("custom-job", WorkloadState.HEAVY_UNKNOWN),
])
def test_sustained_boundary_for_every_category(name, state):
    subject = detector()
    job = process(name=name)
    assert subject.update(snapshot(0, job)).state == WorkloadState.NORMAL
    assert subject.update(snapshot(2.999, job)).state == WorkloadState.NORMAL
    result = subject.update(snapshot(3, job))
    assert result.state == state
    assert result.protected_pids == {job.pid}
    assert result.since == 3


@pytest.mark.parametrize("name", ["code", "gcc", "steam", "blender", "torchrun"])
def test_idle_known_names_and_large_idle_rss_do_not_trigger(name):
    subject = detector()
    job = process(name=name, cpu_percent=0, memory_percent=30)
    subject.update(snapshot(0, job))
    assert subject.update(snapshot(100, job)).state == WorkloadState.NORMAL
    assert not subject.update(snapshot(101, job, memory_percent=95)).protected_pids
    result = subject.update(snapshot(104, job, memory_percent=95))
    assert result.state == WorkloadState.HEAVY_UNKNOWN
    assert not result.protected_pids


def test_release_delay_and_lower_retention_threshold():
    subject = detector()
    job = process(name="gcc")
    subject.update(snapshot(0, job))
    subject.update(snapshot(3, job))
    medium = replace(job, cpu_percent=35)
    assert subject.update(snapshot(10, medium)).state == WorkloadState.COMPILATION
    idle = replace(job, cpu_percent=0)
    assert subject.update(snapshot(11, idle)).active
    assert subject.update(snapshot(15.999, idle)).active
    assert subject.update(snapshot(16, idle)).state == WorkloadState.NORMAL


def test_system_pressure_does_not_override_retained_user_activity():
    subject = detector()
    busy = process(name="gcc")
    subject.update(snapshot(0, busy))
    subject.update(snapshot(3, busy))
    medium = replace(busy, cpu_percent=35)
    subject.update(snapshot(4, medium, memory_percent=95))
    result = subject.update(snapshot(7, medium, memory_percent=95))
    assert result.state == WorkloadState.COMPILATION
    assert result.protected_pids == {busy.pid}


def test_short_burst_resets_sustained_timer_and_resume_cancels_release():
    subject = detector()
    busy = process(name="gcc")
    idle = replace(busy, cpu_percent=0)
    subject.update(snapshot(0, busy))
    subject.update(snapshot(2, idle))
    subject.update(snapshot(3, busy))
    assert not subject.update(snapshot(5, busy)).active
    assert subject.update(snapshot(6, busy)).active
    subject.update(snapshot(7, idle))
    assert subject.update(snapshot(11, busy)).active
    assert subject.update(snapshot(12, idle)).active
    assert subject.update(snapshot(16, idle)).active
    assert not subject.update(snapshot(17, idle)).active


def test_process_tree_protects_children_and_ancestors_not_ancestor_siblings():
    subject = detector()
    init = process(1, "systemd", uid=0, cpu_percent=0)
    shell = process(2, "bash", ppid=1, cpu_percent=0)
    job = process(10, "gcc", ppid=2)
    child = process(11, "worker", ppid=10, cpu_percent=0)
    other = process(12, "unrelated", ppid=2, cpu_percent=0)
    all_processes = (init, shell, job, child, other)
    subject.update(snapshot(0, *all_processes))
    result = subject.update(snapshot(3, *all_processes))
    assert result.protected_pids == {1, 2, 10, 11}


def test_parallel_children_aggregate_under_known_workload_root():
    subject = detector()
    root = process(10, "make", cpu_percent=0)
    children = (process(11, "gcc", ppid=10, cpu_percent=30),
                process(12, "gcc", ppid=10, cpu_percent=30))
    subject.update(snapshot(0, root, *children))
    result = subject.update(snapshot(3, root, *children))
    assert result.state == WorkloadState.COMPILATION
    assert result.protected_pids == {10, 11, 12}


def test_pid_reuse_cannot_inherit_sustained_evidence_or_protection():
    subject = detector()
    original = process(name="gcc")
    reused = replace(original, create_time=100)
    subject.update(snapshot(0, original))
    assert not subject.update(snapshot(3, reused)).active
    assert subject.update(snapshot(6, reused)).active
    idle_reuse = replace(reused, create_time=200, cpu_percent=0)
    result = subject.update(snapshot(7, idle_reuse))
    assert result.active  # Release delay retains state, never the reused PID.
    assert 10 not in result.protected_pids


def test_reused_parent_pid_is_not_an_ancestor():
    subject = detector()
    parent = process(2, "code", create_time=100, cpu_percent=0)
    child = process(10, "gcc", ppid=2, create_time=50)
    subject.update(snapshot(0, parent, child))
    assert subject.update(snapshot(3, parent, child)).protected_pids == {10}


@pytest.mark.parametrize("changes", [
    {"uid": 0}, {"uid": 2000}, {"kernel_thread": True},
    {"accessible": False}, {"status": "zombie"},
])
def test_non_user_or_unobservable_processes_are_not_workload_roots(changes):
    subject = detector()
    job = process(name="gcc", **changes)
    subject.update(snapshot(0, job, cpu_percent=90))
    result = subject.update(snapshot(3, job, cpu_percent=90))
    assert result.state == WorkloadState.HEAVY_UNKNOWN
    assert not result.protected_pids


def test_opted_in_background_hog_alone_has_no_protected_user_workload():
    subject = detector(background_executables=("/usr/bin/blender",))
    job = process(name="blender", foreground=True)
    subject.update(snapshot(0, job, cpu_percent=90))
    result = subject.update(snapshot(3, job, cpu_percent=90))
    assert result.state == WorkloadState.HEAVY_UNKNOWN
    assert not result.protected_pids


def test_background_name_is_not_opt_in_and_unknown_user_work_remains_protected():
    subject = detector(background_executables=("/opt/worker",))
    job = process(name="worker")
    subject.update(snapshot(0, job))
    result = subject.update(snapshot(3, job))
    assert result.state == WorkloadState.HEAVY_UNKNOWN
    assert result.protected_pids == {job.pid}


def test_background_worker_child_does_not_become_foreground_workload():
    subject = detector(background_executables=("/usr/bin/worker",))
    parent = process(name="worker", cpu_percent=0)
    child = process(11, "gcc", ppid=parent.pid)
    subject.update(snapshot(0, parent, child, cpu_percent=90))
    result = subject.update(snapshot(3, parent, child, cpu_percent=90))
    assert result.state == WorkloadState.HEAVY_UNKNOWN
    assert not result.protected_pids


def test_unattributed_system_load_cannot_retain_previous_unknown_user_protection():
    subject = detector()
    job = process()
    subject.update(snapshot(0, job))
    assert subject.update(snapshot(3, job)).protected_pids == {job.pid}
    idle = replace(job, cpu_percent=0)
    subject.update(snapshot(4, idle, cpu_percent=90))
    result = subject.update(snapshot(7, idle, cpu_percent=90))
    assert result.state == WorkloadState.HEAVY_UNKNOWN
    assert not result.protected_pids


def test_concurrent_heavy_workload_roots_are_all_protected():
    subject = detector()
    compiler = process(name="gcc", cpu_percent=90)
    editor = process(11, "blender", cpu_percent=80)
    subject.update(snapshot(0, compiler, editor))
    result = subject.update(snapshot(3, compiler, editor))
    assert result.state == WorkloadState.COMPILATION
    assert result.protected_pids == {10, 11}


@pytest.mark.parametrize("job_changes,system_changes", [
    ({"cpu_percent": 30}, {"cpu_pressure": 20}),
    ({"cpu_percent": 30}, {"load_average": (4, 1, 1), "cpu_count": 4}),
    ({"cpu_percent": 2, "memory_percent": 20}, {"memory_pressure": 20}),
    ({"cpu_percent": 0, "io_bytes_per_sec": 11 * 1024 * 1024}, {}),
    ({"cpu_percent": 0, "io_bytes_per_sec": 6 * 1024 * 1024}, {"io_pressure": 20}),
])
def test_measured_resource_signals_combine_with_system_pressure(job_changes, system_changes):
    subject = detector()
    job = process(name="blender", **job_changes)
    subject.update(snapshot(0, job, **system_changes))
    assert subject.update(snapshot(3, job, **system_changes)).state == WorkloadState.EDITING


def test_wall_clock_changes_do_not_change_monotonic_duration():
    subject = detector()
    job = process(name="gcc")
    subject.update(snapshot(0, job))
    assert not subject.update(replace(snapshot(2, job), timestamp=99_999)).active
    assert subject.update(replace(snapshot(3, job), timestamp=1)).active


def test_incomplete_sample_resets_pending_evidence():
    subject = detector()
    job = process(name="gcc")
    subject.update(snapshot(0, job))
    subject.update(snapshot(2, job, complete=False))
    assert not subject.update(snapshot(3, job)).active
    assert subject.update(snapshot(6, job)).active


def test_backward_monotonic_clock_resets_state():
    subject = detector()
    job = process(name="gcc")
    subject.update(snapshot(10, job))
    assert subject.update(snapshot(13, job)).active
    assert not subject.update(snapshot(1, job)).active
    assert subject.update(snapshot(4, job)).active


@pytest.mark.parametrize("kwargs", [{"sustained_seconds": -1}, {"release_seconds": float("nan")},
                                    {"cpu_threshold": 0}, {"hysteresis_ratio": 1.1}])
def test_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        WorkloadDetector(**kwargs)
