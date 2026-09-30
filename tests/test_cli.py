"""CLI smoke checks remain observation-only and never change priorities."""
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]

def run(*args):
    return subprocess.run([sys.executable, str(ROOT / "main.py"), *args],
                          cwd=ROOT, capture_output=True, text=True, timeout=20)

def test_once_observes_and_exits_without_action_state(tmp_path):
    state = tmp_path / "absent.json"
    result = run("--once", "--no-ml-training", "--ml-model", str(tmp_path / "model.json"), "--state-file", str(state))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OBSERVE" in result.stdout
    assert '"proposals": []' in result.stdout
    assert not state.exists()

@pytest.mark.parametrize("args", [("--interval", "0"), ("--interval", "nan"),
    ("--apply",), ("--foreground-pid", "1"), ("--watch-fd", "1"),
    ("--recover", "--target-uid", "0"), ("--apply", "--recover")])
def test_invalid_arguments_fail_before_control(args):
    result = run(*args)
    assert result.returncode == 2
    assert "error:" in result.stderr

def test_malformed_policy_is_rejected_before_monitoring(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text('{"background_processes": [{"executable":"relative"}]}')
    result = run("--once", "--policy", str(policy))
    assert result.returncode == 2
    assert "Invalid policy" in result.stderr


def test_explicit_foreground_pid_is_pinned_to_initial_process_identity(monkeypatch):
    import importlib
    from types import SimpleNamespace
    from src.optimization.models import ProcessMetrics, SystemSnapshot, Workload
    main_module = importlib.import_module("main")
    monitor_module = importlib.import_module("src.monitor.metrics_collector")
    engine_module = importlib.import_module("src.optimization.engine")
    samples = iter([
        SystemSnapshot(1, 1, (ProcessMetrics(30, 3),)),
        SystemSnapshot(2, 2, (ProcessMetrics(30, 4),)),
        SystemSnapshot(3, 3, (ProcessMetrics(30, 4),)),
    ])
    captured = []
    class Shutdown:
        def is_set(self):
            return len(captured) >= 3
        def set(self):
            pass
        def wait(self, _):
            pass
    class Engine:
        def __init__(self, *_args, **_kwargs):
            pass
        def cycle(self, snapshot):
            captured.append(snapshot)
            return SimpleNamespace(workload=Workload(), decisions=(), results=())
    monkeypatch.setattr(main_module.threading, "Event", Shutdown)
    monkeypatch.setattr(monitor_module, "SystemMonitor", lambda: SimpleNamespace(sample=lambda: next(samples)))
    monkeypatch.setattr(engine_module, "OptimizationEngine", Engine)
    assert main_module.main(["--foreground-pid", "30", "--interval", "1", "--no-ml"]) == 0
    assert [s.processes[0].foreground for s in captured] == [True, False, False]


def test_failed_journaled_control_exits_and_releases_state_before_watchdog(monkeypatch, tmp_path):
    import importlib
    from types import SimpleNamespace
    from src.optimization.models import ControlResult, SystemSnapshot, Workload
    main_module = importlib.import_module("main")
    monitor_module = importlib.import_module("src.monitor.metrics_collector")
    engine_module = importlib.import_module("src.optimization.engine")
    state_module = importlib.import_module("src.optimization.state")
    control_module = importlib.import_module("src.controller.process_controller")
    events = []
    store = SimpleNamespace(records={}, acquire=lambda: events.append("acquire"),
                            close=lambda: events.append("store_close"))
    restore_calls = [0]
    def restore_all():
        restore_calls[0] += 1
        events.append("restore")
        if restore_calls[0] == 1:
            return []
        store.records.clear()
        return [ControlResult(True, "Shutdown recovered original resources")]
    controller = SimpleNamespace(restore_all=restore_all)
    class Guard:
        def __init__(self, *_args, **_kwargs):
            pass
        def start(self):
            events.append("guard_start")
        def healthy(self):
            return True
        def close(self):
            assert events[-1] == "store_close"
            events.append("guard_close")
    class Engine:
        def __init__(self, *_args, **_kwargs):
            pass
        def cycle(self, _snapshot):
            store.records["pending"] = object()
            return SimpleNamespace(workload=Workload(), decisions=(),
                                   results=(ControlResult(False, "Journal persistence failed"),))
    monkeypatch.setattr(main_module.os, "getuid", lambda: 1000)
    monkeypatch.setattr(main_module, "RecoveryGuard", Guard)
    monkeypatch.setattr(monitor_module, "SystemMonitor", lambda: SimpleNamespace(sample=lambda: SystemSnapshot(1, 1)))
    monkeypatch.setattr(state_module, "StateStore", lambda *_: store)
    monkeypatch.setattr(control_module, "ResourceController", lambda *_args, **_kwargs: controller)
    monkeypatch.setattr(engine_module, "OptimizationEngine", Engine)
    policy = tmp_path / "policy.json"
    policy.write_text('{"background_processes": [{"executable": "/opt/batch"}]}')
    assert main_module.main(["--apply", "--once", "--no-ml", "--policy", str(policy)]) == 1
    assert restore_calls[0] == 2
    assert events[-2:] == ["store_close", "guard_close"]
