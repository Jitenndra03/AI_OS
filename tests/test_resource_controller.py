"""Controller tests inject processes; no live resource mutation is possible."""

import copy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from src.controller.process_controller import ResourceController, enforce, renice_process
from src.optimization.models import Action, Category, Decision, ProcessMetrics


class FakeStore:
    boot_id = "20be6b33-0b62-424c-a082-da55a4994551"

    def __init__(self):
        self._records = {}
        self.events = []
        self.fail = False

    @property
    def records(self):
        return copy.deepcopy(self._records)

    def put(self, record):
        if self.fail:
            raise OSError("journal unavailable")
        self.events.append(("put", record.phase))
        self._records[record.identity] = copy.deepcopy(record)

    def remove(self, identity):
        self.events.append(("remove", identity))
        self._records.pop(identity, None)


@pytest.fixture
def env():
    store = FakeStore()
    live = ProcessMetrics(pid=4242, create_time=100, ppid=200, uid=1000,
                          name="worker", exe="/usr/bin/worker", status="running",
                          cgroup="/delegated/source", nice=0)
    backend = Mock()
    state = SimpleNamespace(live=live)
    backend.metrics.side_effect = lambda proc: state.live
    backend.self_ancestry.return_value = {100, 101}
    backend.can_restore_nice.return_value = True
    proc = Mock(pid=4242)
    proc.children.return_value = []
    proc.is_running.return_value = True

    def nice(value):
        store.events.append(("nice", value))
        state.live = replace(state.live, nice=value)

    def suspend():
        store.events.append(("suspend",))
        state.live = replace(state.live, status="stopped")

    def resume():
        store.events.append(("resume",))
        state.live = replace(state.live, status="running")

    proc.nice.side_effect = nice
    proc.suspend.side_effect = suspend
    proc.resume.side_effect = resume
    backend.process.return_value = proc
    controller = ResourceController(store, 1000, backend=backend)
    decision = Decision(live, Category.BACKGROUND_SAFE, Action.RENICE, "opted in", new_nice=10)
    return SimpleNamespace(store=store, state=state, proc=proc, backend=backend,
                           controller=controller, decision=decision)


def test_journal_precedes_apply_and_restore(env):
    assert env.controller.apply(env.decision).success
    assert env.store.events[:3] == [("put", "pending"), ("nice", 10), ("put", "applied")]
    assert env.controller.restore(env.decision.process.identity).success
    assert env.store.events[3:5] == [("put", "restore_pending"), ("nice", 0)]
    assert env.state.live.nice == 0 and not env.store.records


@pytest.mark.parametrize("changes", [
    {"uid": 0}, {"uid": 1001}, {"exe": "/usr/bin/other"}, {"create_time": 101},
    {"foreground": True}, {"name": "sshd"}, {"kernel_thread": True},
    {"accessible": False}, {"cgroup": "/system.slice/service"},
])
def test_live_revalidation_rejects_unsafe_or_changed_process(env, changes):
    env.state.live = replace(env.state.live, **changes)
    assert not env.controller.apply(env.decision).success
    env.proc.nice.assert_not_called()
    assert not env.store.records


def test_own_ancestors_and_workload_are_protected(env):
    env.backend.self_ancestry.return_value = {4242}
    assert not env.controller.apply(env.decision).success
    env.backend.self_ancestry.return_value = set()
    env.controller.protected_pids = {4242}
    assert not env.controller.apply(env.decision).success
    env.proc.nice.assert_not_called()


def test_no_nice_reduction_without_restoration_permission(env):
    env.backend.can_restore_nice.return_value = False
    assert not env.controller.apply(env.decision).success
    env.proc.nice.assert_not_called()


def test_cannot_raise_priority_or_act_on_unknown_category(env):
    assert not env.controller.apply(replace(env.decision, new_nice=-1)).success
    assert not env.controller.apply(replace(env.decision, classification=Category.UNKNOWN)).success
    assert not env.controller.apply(replace(env.decision, action="RENICE")).success
    env.proc.nice.assert_not_called()


def test_persistence_failure_prevents_mutation(env):
    env.store.fail = True
    assert not env.controller.apply(env.decision).success
    env.proc.nice.assert_not_called()


def test_failed_mutation_retains_recoverable_journal(env):
    env.proc.nice.side_effect = PermissionError("denied")
    assert not env.controller.apply(env.decision).success
    record = env.store.records[env.decision.process.identity]
    assert record.phase == "failed" and "denied" in record.error
    assert env.controller.restore(record.identity).success  # No effect occurred.
    assert not env.store.records


def test_crash_after_mutation_before_applied_is_recoverable(env):
    original = env.store.put

    def fail_after_applied(record):
        if record.phase != "pending":
            raise OSError("disk full")
        original(record)

    env.store.put = fail_after_applied
    assert not env.controller.apply(env.decision).success
    assert env.state.live.nice == 10
    assert env.store.records[env.decision.process.identity].phase == "pending"
    env.store.put = original
    assert env.controller.restore(env.decision.process.identity).success
    assert env.state.live.nice == 0


def test_restore_does_not_overwrite_external_nice_change(env):
    env.controller.apply(env.decision)
    env.state.live = replace(env.state.live, nice=15)
    assert not env.controller.restore(env.decision.process.identity).success
    assert env.state.live.nice == 15 and env.store.records


def test_restoration_allows_new_foreground_workload(env):
    env.controller.apply(env.decision)
    env.state.live = replace(env.state.live, foreground=True)
    env.controller.protected_pids = {4242}
    assert env.controller.restore(env.decision.process.identity).success
    assert env.state.live.nice == 0


def test_restore_does_not_change_reused_pid(env):
    env.controller.apply(env.decision)
    env.state.live = replace(env.state.live, create_time=200)
    assert env.controller.restore(env.decision.process.identity).success
    assert env.proc.nice.call_count == 1 and not env.store.records


def test_restore_does_not_change_current_critical_process(env):
    env.controller.apply(env.decision)
    env.state.live = replace(env.state.live, name="sshd")
    assert not env.controller.restore(env.decision.process.identity).success
    assert env.proc.nice.call_count == 1 and env.store.records


def test_old_boot_record_dropped_without_process_action(env):
    env.controller.apply(env.decision)
    env.store.boot_id = "ba2c4463-be77-41fb-a1d9-3a07a2769045"
    env.backend.process.reset_mock()
    assert env.controller.restore_all()[0].success
    env.backend.process.assert_not_called()
    assert not env.store.records


def test_exited_original_dropped_without_restoration(env):
    env.controller.apply(env.decision)
    env.backend.process.side_effect = psutil.NoSuchProcess(4242)
    assert env.controller.restore(env.decision.process.identity).success
    assert not env.store.records


def test_suspend_requires_both_flags_optional_category_and_no_children(env):
    decision = replace(env.decision, action=Action.SUSPEND, classification=Category.BACKGROUND_OPTIONAL, allow_suspend=True)
    assert not env.controller.apply(decision).success
    env.controller.allow_suspend = True
    env.proc.children.return_value = [Mock()]
    assert not env.controller.apply(decision).success
    env.proc.children.return_value = []
    assert env.controller.apply(decision).success
    assert env.state.live.status == "stopped"
    assert env.controller.restore(decision.process.identity).success
    assert env.state.live.status == "running"
    assert env.store.events[0:3] == [("put", "pending"), ("suspend",), ("put", "applied")]


def test_already_stopped_process_not_suspended(env):
    env.state.live = replace(env.state.live, status="stopped")
    env.controller.allow_suspend = True
    assert not env.controller.apply(replace(env.decision, action=Action.SUSPEND,
        classification=Category.BACKGROUND_OPTIONAL, allow_suspend=True)).success
    env.proc.suspend.assert_not_called()


def test_legacy_entrypoints_never_mutate():
    with pytest.raises(RuntimeError, match="Historical CSV"):
        enforce()
    with pytest.raises(RuntimeError, match="PID-only"):
        renice_process(42, "worker", -1)


@pytest.mark.parametrize("changes", [{"exe": "/usr/bin/after-exec"}, {"uid": 1001}])
def test_exec_or_owner_change_keeps_recovery_record(env, changes):
    env.controller.apply(env.decision)
    env.state.live = replace(env.state.live, **changes)
    assert not env.controller.restore(env.decision.process.identity).success
    assert env.store.records and env.proc.nice.call_count == 1


@pytest.mark.parametrize("changes", [{"read_bytes": 1}, {"write_bytes": 1},
                                    {"io_bytes_per_sec": 1}, {"status": "disk-sleep"}])
def test_suspend_rechecks_live_io_and_uninterruptible_state(env, changes):
    env.controller.allow_suspend = True
    env.state.live = replace(env.state.live, **changes)
    decision = replace(env.decision, action=Action.SUSPEND,
                       classification=Category.BACKGROUND_OPTIONAL, allow_suspend=True)
    assert not env.controller.apply(decision).success
    env.proc.suspend.assert_not_called()


def test_suspend_rechecks_io_after_pending_journal(env):
    env.controller.allow_suspend = True
    decision = replace(env.decision, action=Action.SUSPEND,
                       classification=Category.BACKGROUND_OPTIONAL, allow_suspend=True)
    original_put = env.store.put

    def put(record):
        original_put(record)
        env.state.live = replace(env.state.live, write_bytes=10)

    env.store.put = put
    assert not env.controller.apply(decision).success
    env.proc.suspend.assert_not_called()
    assert env.store.records


def test_nice_permission_reads_target_rlimit(monkeypatch):
    from src.controller import process_controller as module
    backend = module.LinuxBackend()
    fake_status = Mock()
    fake_status.read_text.return_value = "CapEff:\t0000000000000000\n"
    monkeypatch.setattr(module, "Path", Mock(return_value=fake_status))
    proc = Mock()
    proc.rlimit.return_value = (20, 20)
    assert backend.can_restore_nice(proc, 0)
    proc.rlimit.assert_called_once_with(module.resource.RLIMIT_NICE)
    proc.rlimit.return_value = (0, 0)
    assert not backend.can_restore_nice(proc, 0)
    fake_status.read_text.return_value = "CapEff:\t0000000000800000\n"
    assert backend.can_restore_nice(proc, 0)
