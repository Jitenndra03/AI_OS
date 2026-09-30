"""Offline demo and benchmark regressions: all process activity is synthetic."""
import importlib
import os
from pathlib import Path
import subprocess
from unittest.mock import Mock

import psutil
import pytest

import benchmark
import demo
from src.controller import process_controller
from src.optimization import decisions, engine, models
from src.optimization.state import StateStore


def test_offline_optimizer_demonstrates_journaled_apply_and_restore_without_host_control(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Offline optimizer demo attempted host process access or mutation")
    monkeypatch.setattr(process_controller.LinuxBackend, "__init__", forbidden)
    monkeypatch.setattr(psutil, "Process", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    for name in ("kill", "killpg", "setpriority", "nice", "sched_setaffinity"):
        monkeypatch.setattr(os, name, forbidden)

    phases, applied_decisions = [], []
    original_put, original_apply = StateStore.put, process_controller.ResourceController.apply
    def put(store, record):
        original_put(store, record)
        phases.append(record.phase)
    def apply(controller, decision):
        assert type(decision) is models.Decision
        assert type(decision.action) is models.Action
        applied_decisions.append(decision)
        return original_apply(controller, decision)
    monkeypatch.setattr(StateStore, "put", put)
    monkeypatch.setattr(process_controller.ResourceController, "apply", apply)

    result = demo.optimization_demo(tmp_path)
    assert result["status"] == "passed" and result["restored"] is True
    assert "SYNTHETIC" in result["mode"]
    assert [cycle["workload"] for cycle in result["cycles"]] == [
        "NORMAL", "NORMAL", "COMPILATION", "COMPILATION", "NORMAL"]
    assert [cycle["worker_nice"] for cycle in result["cycles"]] == [0, 0, 10, 10, 0]
    assert [cycle["records"] for cycle in result["cycles"]] == [0, 0, 1, 1, 0]
    assert result["events"] == [
        {"event": "simulated_nice", "value": 10, "journal_phase": "pending"},
        {"event": "simulated_nice", "value": 0, "journal_phase": "restore_pending"},
    ]
    assert phases == ["pending", "applied", "restore_pending"]
    assert len(applied_decisions) == 1 and applied_decisions[0].action is models.Action.RENICE
    with StateStore(tmp_path / "state.json").acquire() as store:
        assert not store.records


def test_main_policy_engine_and_controller_share_canonical_contract_classes():
    main = importlib.import_module("main")
    assert main.Action is models.Action
    assert engine.Action is decisions.Action is process_controller.Action is models.Action
    assert engine.Decision is decisions.Decision is process_controller.Decision is models.Decision
    assert engine.SystemSnapshot is decisions.SystemSnapshot is models.SystemSnapshot


@pytest.mark.parametrize("exited_during_measurement", [None, "background", "observer"])
def test_benchmark_requires_owned_contention_and_observer_to_survive_trial(
        tmp_path, monkeypatch, exited_during_measurement):
    finished = [False]
    owned = {name: Mock(pid=pid) for pid, name in enumerate(("background", "observer", "foreground"), 10000)}
    for name in ("background", "observer"):
        owned[name].poll.side_effect = lambda name=name: 7 if finished[0] and name == exited_during_measurement else None
    owned["foreground"].returncode = 0
    owned["foreground"].wait.side_effect = lambda **_: finished.__setitem__(0, True)
    spawned = []
    def popen(argv, **kwargs):
        assert kwargs["start_new_session"] is True
        if any(str(arg).endswith("main.py") for arg in argv):
            assert "--no-ml" in argv and "--apply" not in argv
            name = "observer"
        else:
            assert str(Path(benchmark.ROOT) / "scripts/cpu_worker.py") in argv
            assert "--max-wall-seconds" in argv
            name = "background" if "background" not in spawned else "foreground"
        spawned.append(name)
        return owned[name]
    monkeypatch.setattr(benchmark.subprocess, "Popen", popen)
    monkeypatch.setattr(benchmark, "LinuxBackend", Mock())
    process = Mock()
    process.nice.return_value = 0
    monkeypatch.setattr(benchmark.psutil, "Process", Mock(return_value=process))
    monkeypatch.setattr(benchmark.os, "sched_getaffinity", lambda _: {1})
    monkeypatch.setattr(benchmark.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(benchmark.time, "sleep", lambda _: None)
    cleanup = Mock()
    monkeypatch.setattr(benchmark, "stop_owned", cleanup)
    result = benchmark.run_trial("observe", True, 0.25, 1000, tmp_path)
    assert spawned == ["background", "observer", "foreground"]
    assert [call.args[0] for call in cleanup.call_args_list] == [owned["foreground"], owned["background"], owned["observer"]]
    assert result["action_applied"] is False
    if exited_during_measurement is None:
        assert result["status"] == "passed", result
    else:
        assert result["status"] == "blocked_or_failed", result
        assert "exited" in result["reason"].lower()
