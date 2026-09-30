"""Classification tests focus on authorization boundaries, not name guessing."""

from dataclasses import replace

import pytest

from src.optimization.classification import ProcessClassifier
from src.optimization.models import (
    BackgroundRule, Category, ProcessMetrics, SystemSnapshot, Workload, WorkloadState,
)


def process(pid=100, **kwargs):
    return replace(ProcessMetrics(pid, 123.0, ppid=50, uid=1000, name="worker",
                                  exe="/opt/worker", status="sleeping"), **kwargs)


def classify(*processes, rules=(), workload=None, complete=True):
    classifier = ProcessClassifier(rules, owner_uid=1000, self_pid=900)
    return classifier.classify(SystemSnapshot(1.0, 1.0, processes=processes, complete=complete),
                               workload or Workload())


def test_unknown_is_never_assumed_background():
    item = classify(process())[0]
    assert item.category == Category.UNKNOWN
    assert item.rule is None
    assert item.confidence == 0


@pytest.mark.parametrize("category", [Category.BACKGROUND_SAFE, Category.BACKGROUND_OPTIONAL])
def test_only_exact_executable_rule_opts_in(category):
    rule = BackgroundRule("/opt/worker", category=category)
    good, spoof, renamed = classify(process(), process(101, exe="/tmp/worker"),
                                   process(102, name="different-name"), rules=(rule,))
    assert good.category == category and good.rule == rule
    assert spoof.category == Category.UNKNOWN and spoof.rule is None
    assert renamed.category == category


@pytest.mark.parametrize("changes,category", [
    ({"pid": 1}, Category.CRITICAL_SYSTEM),
    ({"pid": 2, "exe": ""}, Category.CRITICAL_SYSTEM),
    ({"ppid": 2}, Category.CRITICAL_SYSTEM),
    ({"kernel_thread": True}, Category.CRITICAL_SYSTEM),
    ({"uid": 0}, Category.CRITICAL_SYSTEM),
    ({"uid": 1001}, Category.CRITICAL_SYSTEM),
    ({"name": "sshd"}, Category.CRITICAL_SYSTEM),
    ({"name": "gnome-shell"}, Category.CRITICAL_SYSTEM),
    ({"cgroup": "/system.slice/service"}, Category.CRITICAL_SYSTEM),
    ({"cgroup": "/init.scope"}, Category.CRITICAL_SYSTEM),
    ({"accessible": False}, Category.UNKNOWN),
    ({"uid": -1}, Category.UNKNOWN),
    ({"create_time": float("nan")}, Category.UNKNOWN),
    ({"create_time": float("inf")}, Category.UNKNOWN),
    ({"create_time": 0}, Category.UNKNOWN),
    ({"create_time": True}, Category.UNKNOWN),
    ({"exe": "worker"}, Category.UNKNOWN),
    ({"exe": "/opt/../worker"}, Category.UNKNOWN),
    ({"status": "zombie"}, Category.UNKNOWN),
    ({"status": "unknown"}, Category.UNKNOWN),
])
def test_rule_cannot_override_protections(changes, category):
    item = classify(process(**changes), rules=(BackgroundRule("/opt/worker"),))[0]
    assert item.category == category
    assert item.rule is None


def test_foreground_tree_includes_parents_children_but_not_siblings():
    rows = classify(process(10, ppid=1), process(20, ppid=10, foreground=True),
                    process(30, ppid=20), process(40, ppid=10),
                    rules=(BackgroundRule("/opt/worker"),))
    assert [item.category for item in rows] == [Category.USER_FOREGROUND] * 3 + [Category.BACKGROUND_SAFE]


def test_active_workload_tree_and_parent_protected():
    workload = Workload(WorkloadState.COMPILATION, frozenset({10, 20, 30}))
    rows = classify(process(10, ppid=1), process(20, ppid=10),
                    process(30, ppid=20), process(40, ppid=10),
                    rules=(BackgroundRule("/opt/worker"),), workload=workload)
    assert [item.category for item in rows] == [Category.USER_IMPORTANT] * 3 + [Category.BACKGROUND_SAFE]


def test_controller_ancestry_and_descendants_protected_not_siblings():
    rows = classify(process(800, ppid=1), process(900, ppid=800),
                    process(901, ppid=900), process(902, ppid=800),
                    rules=(BackgroundRule("/opt/worker"),))
    assert [item.category for item in rows] == [Category.USER_IMPORTANT, Category.CRITICAL_SYSTEM,
                                              Category.USER_IMPORTANT, Category.BACKGROUND_SAFE]


@pytest.mark.parametrize("name", ["python", "python3.14", "code", "node", "gcc", "firefox", "blender"])
def test_known_user_applications_are_important_without_opt_in(name):
    item = classify(process(name=name, exe="/usr/bin/" + name))[0]
    assert item.category == Category.USER_IMPORTANT
    assert item.rule is None


def test_idle_known_application_requires_explicit_rule():
    item = classify(process(name="python3", exe="/usr/bin/python3"),
                    rules=(BackgroundRule("/usr/bin/python3"),))[0]
    assert item.category == Category.BACKGROUND_SAFE


def test_deleted_executable_is_observation_not_background_authorization():
    item = classify(process(exe="/opt/worker (deleted)"),
                    rules=(BackgroundRule("/opt/worker (deleted)"),))[0]
    assert item.category == Category.SUSPICIOUS
    assert "not evidence of malware" in item.reason
    assert item.rule is None


def test_incomplete_or_duplicate_snapshot_never_authorizes_background():
    rule = BackgroundRule("/opt/worker")
    assert classify(process(), rules=(rule,), complete=False)[0].category == Category.UNKNOWN
    assert all(item.category == Category.UNKNOWN for item in classify(process(), process(), rules=(rule,)))


@pytest.mark.parametrize("rule", [BackgroundRule("worker"), BackgroundRule("/opt/../worker"),
                                   BackgroundRule("/opt/worker", category=Category.USER_IMPORTANT)])
def test_invalid_direct_rules_rejected(rule):
    with pytest.raises(ValueError):
        ProcessClassifier((rule,))


def test_duplicate_rules_rejected():
    with pytest.raises(ValueError):
        ProcessClassifier((BackgroundRule("/opt/worker"), BackgroundRule("/opt/worker")))


def test_root_controller_cannot_classify_any_process_as_background():
    classifier = ProcessClassifier((BackgroundRule("/opt/worker"),), owner_uid=0, self_pid=900)
    result = classifier.classify(SystemSnapshot(1, 1, processes=(process(),)), Workload())
    assert result[0].category == Category.CRITICAL_SYSTEM


def test_detector_protection_set_does_not_expand_shared_ancestor_siblings():
    from src.optimization.workload import WorkloadDetector

    processes = (process(10, ppid=1, exe="/bin/bash", name="bash"),
                 process(20, ppid=10, exe="/usr/bin/gcc", name="gcc", cpu_percent=90),
                 process(30, ppid=20, exe="/usr/bin/helper", name="helper"),
                 process(40, ppid=10))
    snapshot = SystemSnapshot(1, 1, processes=processes)
    detector = WorkloadDetector(owner_uid=1000, sustained_seconds=0,
                                background_executables=("/opt/worker",))
    workload = detector.update(snapshot)
    assert {10, 20, 30} <= workload.protected_pids
    rows = classify(*processes, rules=(BackgroundRule("/opt/worker"),), workload=workload)
    assert rows[-1].category == Category.BACKGROUND_SAFE
