"""Decision policy is tested using synthetic metrics, never process controls."""

from dataclasses import replace

import pytest

from src.optimization.decisions import DecisionEngine
from src.optimization.models import (
    Action, BackgroundRule, Category, Classification, ProcessMetrics,
    SystemSnapshot, Workload, WorkloadState,
)


def context(*, process_changes=None, rule_changes=None, snapshot_changes=None,
            category=Category.BACKGROUND_SAFE, confidence=1.0, extra_processes=()):
    process = ProcessMetrics(pid=30, create_time=3, uid=1000, name="batch",
                             exe="/opt/batch", cpu_percent=70, status="running", rss=100_000_000)
    process = replace(process, **(process_changes or {}))
    foreground = ProcessMetrics(pid=20, create_time=2, uid=1000, name="gcc",
                                exe="/usr/bin/gcc", foreground=True, cpu_percent=80, status="running")
    rule = BackgroundRule(executable="/opt/batch", category=category)
    rule = replace(rule, **(rule_changes or {}))
    classification = Classification(process, category, "Synthetic explicit opt-in", confidence, rule)
    snapshot = SystemSnapshot(timestamp=100, monotonic=100, cpu_percent=90,
                              processes=(foreground, process, *extra_processes))
    snapshot = replace(snapshot, **(snapshot_changes or {}))
    workload = Workload(WorkloadState.COMPILATION, frozenset({20}), "Measured compile", 0.95, 80)
    return classification, snapshot, workload


def decide(**kwargs):
    return DecisionEngine(owner_uid=1000).decide(*context(**kwargs))


def test_default_uses_least_intrusive_renice():
    result = decide()
    assert result.action == Action.RENICE
    assert result.new_nice == 10
    assert result.cpu_quota_percent is None
    assert result.reason


@pytest.mark.parametrize("category", [Category.CRITICAL_SYSTEM, Category.USER_FOREGROUND,
                                     Category.USER_IMPORTANT, Category.UNKNOWN, Category.SUSPICIOUS])
def test_every_protected_or_uncertain_category_is_noop(category):
    assert decide(category=category).action == Action.NOOP


@pytest.mark.parametrize("changes", [
    {"uid": 0}, {"uid": 2000}, {"pid": 1}, {"foreground": True},
    {"kernel_thread": True}, {"accessible": False}, {"status": "unknown"},
    {"status": "stopped"}, {"status": "zombie"}, {"status": "dead"},
])
def test_raw_process_safety_guards_override_background_classification(changes):
    assert decide(process_changes=changes).action == Action.NOOP


def test_foreground_tree_pid_is_protected():
    classification, snapshot, workload = context()
    workload = replace(workload, protected_pids=frozenset({20, 30}))
    assert DecisionEngine(owner_uid=1000).decide(classification, snapshot, workload).action == Action.NOOP


def test_normal_and_background_only_workloads_are_not_actionable():
    engine = DecisionEngine(owner_uid=1000)
    classification, snapshot, workload = context()
    assert engine.decide(classification, snapshot, Workload()).action == Action.NOOP
    assert engine.decide(classification, snapshot,
                         replace(workload, protected_pids=frozenset())).action == Action.NOOP
    assert engine.decide(classification, snapshot,
                         replace(workload, protected_pids=frozenset({999}))).action == Action.NOOP


def test_heavy_unknown_user_work_can_receive_resources():
    classification, snapshot, workload = context()
    result = DecisionEngine(owner_uid=1000).decide(
        classification, snapshot, replace(workload, state=WorkloadState.HEAVY_UNKNOWN))
    assert result.action == Action.RENICE


def test_system_root_ancestor_alone_is_not_protected_user_work():
    classification, snapshot, workload = context(extra_processes=(ProcessMetrics(1, 0, uid=0),))
    workload = replace(workload, protected_pids=frozenset({1}))
    assert DecisionEngine(owner_uid=1000).decide(classification, snapshot, workload).action == Action.NOOP


@pytest.mark.parametrize("changes", [{"complete": False}, {"cpu_percent": 0}])
def test_incomplete_or_unpressured_snapshot_is_noop(changes):
    assert decide(snapshot_changes=changes).action == Action.NOOP


@pytest.mark.parametrize("confidence", [0.0, 0.89, float("nan")])
def test_low_or_invalid_confidence_is_noop(confidence):
    assert decide(confidence=confidence).action == Action.NOOP


def test_exact_opt_in_rule_is_required():
    engine = DecisionEngine(owner_uid=1000)
    classification, snapshot, workload = context()
    assert engine.decide(replace(classification, rule=None), snapshot, workload).action == Action.NOOP
    assert decide(rule_changes={"executable": "batch"}).action == Action.NOOP
    assert decide(rule_changes={"executable": "/other/batch"}).action == Action.NOOP
    assert decide(rule_changes={"category": Category.BACKGROUND_OPTIONAL}).action == Action.NOOP


def test_pid_reuse_and_stale_metrics_are_noop():
    engine = DecisionEngine(owner_uid=1000)
    classification, snapshot, workload = context()
    reused = replace(classification.process, create_time=200)
    assert engine.decide(replace(classification, process=reused), snapshot, workload).action == Action.NOOP
    stale = replace(classification.process, cpu_percent=100)
    assert engine.decide(replace(classification, process=stale), snapshot, workload).action == Action.NOOP


@pytest.mark.parametrize("nice,target,action", [(0, 10, Action.RENICE), (15, None, Action.NOOP),
                                               (19, None, Action.NOOP), (-5, 10, Action.RENICE)])
def test_renice_never_increases_priority(nice, target, action):
    result = decide(process_changes={"nice": nice})
    assert result.action == action
    assert result.new_nice == target


def test_nice_bounds_are_clamped_and_invalid_types_rejected():
    assert decide(rule_changes={"nice": 999}).new_nice == 19
    assert decide(rule_changes={"nice": -999}).action == Action.NOOP
    assert decide(rule_changes={"nice": 2.5}).action == Action.NOOP


@pytest.mark.parametrize("cpu", [0, 0.1, 9.999])
def test_tiny_cpu_consumers_are_left_alone(cpu):
    assert decide(process_changes={"cpu_percent": cpu}).action == Action.NOOP


@pytest.mark.parametrize("pressure", [{"cpu_percent": 75}, {"cpu_percent": 0, "cpu_pressure": 10},
                                     {"cpu_percent": 0, "load_average": (4, 1, 1), "cpu_count": 4}])
def test_cpu_pressure_boundary_signals(pressure):
    assert decide(process_changes={"cpu_percent": 10}, snapshot_changes=pressure).action == Action.RENICE


@pytest.mark.parametrize("changes", [
    {"cpu_percent": 0, "memory_percent": 95},
    {"cpu_percent": 0, "memory_pressure": 20},
    {"cpu_percent": 0, "io_pressure": 20},
])
def test_renice_is_not_used_for_memory_or_io_only_pressure(changes):
    assert decide(process_changes={"memory_percent": 20, "io_bytes_per_sec": 2_000_000},
                  snapshot_changes=changes).action == Action.NOOP


@pytest.mark.parametrize("action", [Action.CGROUP, Action.CPU_LIMIT])
def test_explicit_cpu_limit_requires_useful_measured_reduction(action):
    result = decide(rule_changes={"strategy": action, "cpu_quota_percent": 25})
    assert result.action == action
    assert result.cpu_quota_percent == 25
    assert decide(rule_changes={"strategy": action, "cpu_quota_percent": 69}).action == Action.NOOP
    assert decide(rule_changes={"strategy": action, "cpu_quota_percent": 100}).action == Action.NOOP
    assert decide(rule_changes={"strategy": action, "cpu_quota_percent": 0}).action == Action.NOOP


def test_memory_high_explicit_only_with_safe_rss_headroom():
    kwargs = dict(process_changes={"cpu_percent": 0, "memory_percent": 20},
                  snapshot_changes={"cpu_percent": 0, "memory_percent": 95})
    result = decide(**kwargs, rule_changes={"strategy": Action.CGROUP, "memory_high_bytes": 125_000_000})
    assert result.action == Action.CGROUP
    assert result.memory_high_bytes == 125_000_000
    assert result.cpu_quota_percent is None
    assert decide(**kwargs, rule_changes={"strategy": Action.CGROUP,
                                         "memory_high_bytes": 124_999_999}).action == Action.NOOP
    assert decide(**kwargs, rule_changes={"strategy": Action.CGROUP}).action == Action.NOOP


def test_cpu_limit_strategy_does_not_implicitly_add_memory_control():
    result = decide(process_changes={"memory_percent": 20}, snapshot_changes={"memory_percent": 95},
                    rule_changes={"strategy": Action.CPU_LIMIT, "memory_high_bytes": 125_000_000})
    assert result.action == Action.CPU_LIMIT
    assert result.memory_high_bytes is None


def test_suspend_requires_separate_user_opt_in():
    assert decide(rule_changes={"strategy": Action.SUSPEND}).action == Action.NOOP
    result = decide(rule_changes={"strategy": Action.SUSPEND, "allow_suspend": True})
    assert result.action == Action.SUSPEND
    assert result.allow_suspend


@pytest.mark.parametrize("changes", [{"io_bytes_per_sec": 1}, {"read_bytes": 1},
                                     {"write_bytes": 1}, {"status": "disk-sleep"},
                                     {"status": "idle"}])
def test_suspend_rejects_io_history_or_nonordinary_state(changes):
    assert decide(process_changes=changes,
                  rule_changes={"strategy": Action.SUSPEND, "allow_suspend": True}).action == Action.NOOP


def test_suspend_rejects_live_children_but_not_dead_or_reused_parent_relationships():
    rule = {"strategy": Action.SUSPEND, "allow_suspend": True}
    child = ProcessMetrics(31, 4, ppid=30, uid=1000, status="sleeping")
    assert decide(rule_changes=rule, extra_processes=(child,)).action == Action.NOOP
    assert decide(rule_changes=rule, extra_processes=(replace(child, status="zombie"),)).action == Action.SUSPEND
    assert decide(rule_changes=rule, extra_processes=(replace(child, create_time=1),)).action == Action.SUSPEND


def test_suspend_never_used_to_address_memory_only_pressure():
    assert decide(rule_changes={"strategy": Action.SUSPEND, "allow_suspend": True},
                  snapshot_changes={"cpu_percent": 0, "memory_percent": 95},
                  process_changes={"memory_percent": 20}).action == Action.NOOP


@pytest.mark.parametrize("strategy", [Action.NOOP, Action.RESTORE, "kill"])
def test_unknown_or_nonoptimization_strategies_are_noop(strategy):
    assert decide(rule_changes={"strategy": strategy}).action == Action.NOOP


def test_decisions_are_deterministic_and_inputs_unchanged():
    engine = DecisionEngine(owner_uid=1000)
    arguments = context()
    assert engine.decide(*arguments) == engine.decide(*arguments)
    assert arguments[0].process.nice == 0


@pytest.mark.parametrize("kwargs", [{"minimum_confidence": 0}, {"min_cpu_percent": 0},
                                    {"memory_headroom_ratio": 0.5}, {"psi_threshold": float("nan")}])
def test_invalid_constructor_settings(kwargs):
    with pytest.raises(ValueError):
        DecisionEngine(**kwargs)
