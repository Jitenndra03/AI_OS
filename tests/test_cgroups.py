"""Cgroup operations run only against an in-memory fake filesystem."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.controller.cgroups import CgroupError, CgroupFS, CgroupV2
from src.optimization.models import Action, ProcessMetrics
from test_resource_controller import env  # Shared fully mocked controller fixture.


class FakeFS:
    mount = Path("/sys/fs/cgroup")

    def __init__(self):
        self.root = self.mount / "delegated"
        self.dirs = {self.root, self.root / "source"}
        self.files = {self.root / "cgroup.subtree_control": "cpu memory",
                      self.root / "source/cgroup.procs": "4242"}
        self.events = []
        self.fail_write = None
        self.on_move = lambda pid, path: None

    def validate(self, root, uid):
        assert root == self.root and uid == 1000

    def check_path(self, path):
        if ".." in Path(path).parts:
            raise CgroupError("path traversal")

    def read(self, path):
        return self.files[path]

    def exists(self, path):
        return path in self.dirs or path in self.files

    def mkdir(self, path):
        if path in self.dirs:
            raise FileExistsError(path)
        self.dirs.add(path)
        self.files[path / "cgroup.procs"] = ""
        self.events.append(("mkdir", path))

    def write(self, path, value):
        if path.name == self.fail_write:
            raise PermissionError("fake write failure")
        self.events.append(("write", path.name, value))
        if path.name == "cgroup.procs":
            for entry in list(self.files):
                if entry.name == "cgroup.procs":
                    self.files[entry] = " ".join(pid for pid in self.files[entry].split() if pid != value)
            self.files[path] = " ".join([*self.files[path].split(), value])
            self.on_move(int(value), path.parent)
        else:
            self.files[path] = value

    def rmdir(self, path):
        if self.files[path / "cgroup.procs"] or any(path in entry.parents for entry in self.dirs):
            raise OSError("group occupied")
        self.dirs.remove(path)
        for entry in list(self.files):
            if entry.parent == path:
                del self.files[entry]
        self.events.append(("rmdir", path))


@pytest.fixture
def grouped(env):
    fs = FakeFS()
    groups = CgroupV2(fs.root, 1000, fs=fs)
    env.controller.cgroups = groups
    env.decision = replace(env.decision, action=Action.CPU_LIMIT, cpu_quota_percent=25, memory_high_bytes=1000000)
    fs.on_move = lambda pid, path: setattr(env.state, "live", replace(env.state.live, cgroup=groups.current_path(path)))
    env.fs = fs
    env.groups = groups
    return env


def test_delegated_cpu_memory_apply_restore(grouped):
    assert grouped.controller.apply(grouped.decision).success
    target = Path(grouped.groups.managed_path(grouped.decision.process.identity))
    assert grouped.fs.files[target / "cpu.max"] == "25000 100000"
    assert grouped.fs.files[target / "memory.high"] == "1000000"
    assert not any(event[1] == "memory.max" for event in grouped.fs.events)
    assert grouped.state.live.cgroup == grouped.groups.current_path(target)
    assert grouped.controller.restore(grouped.decision.process.identity).success
    assert grouped.state.live.cgroup == "/delegated/source"
    assert target not in grouped.fs.dirs and not grouped.store.records


def test_refuses_unavailable_controller_without_enabling_it(grouped):
    grouped.fs.files[grouped.fs.root / "cgroup.subtree_control"] = "memory"
    assert not grouped.controller.apply(grouped.decision).success
    assert not grouped.fs.events


def test_refuses_source_outside_delegation(grouped):
    grouped.state.live = replace(grouped.state.live, cgroup="/unrelated")
    assert not grouped.controller.apply(grouped.decision).success
    assert not grouped.fs.events


def test_partial_group_setup_is_journaled_and_cleaned(grouped):
    grouped.fs.fail_write = "memory.high"
    assert not grouped.controller.apply(grouped.decision).success
    record = grouped.store.records[grouped.decision.process.identity]
    assert record.phase == "failed"
    assert Path(record.managed_cgroup) in grouped.fs.dirs
    grouped.fs.fail_write = None
    assert grouped.controller.restore(record.identity).success
    assert not grouped.store.records
    assert Path(record.managed_cgroup) not in grouped.fs.dirs


def test_descendants_restored_even_after_original_exits(grouped):
    assert grouped.controller.apply(grouped.decision).success
    record = grouped.store.records[grouped.decision.process.identity]
    child = ProcessMetrics(pid=5000, create_time=200, ppid=4242, uid=1000,
                           name="child", exe="/usr/bin/child", status="running",
                           cgroup=grouped.groups.current_path(record.managed_cgroup))
    state = {5000: child}
    grouped.fs.files[Path(record.managed_cgroup) / "cgroup.procs"] = "5000"
    grouped.backend.process.side_effect = lambda pid: Mock(pid=pid, is_running=Mock(return_value=True))
    grouped.backend.metrics.side_effect = lambda proc: state[proc.pid]
    grouped.fs.on_move = lambda pid, path: state.update({pid: replace(state[pid], cgroup=grouped.groups.current_path(path))})
    assert grouped.controller.restore(record.identity).success
    assert state[5000].cgroup == "/delegated/source"
    assert not grouped.store.records


def test_unrestorable_occupant_keeps_group_and_record(grouped):
    grouped.controller.apply(grouped.decision)
    identity = grouped.decision.process.identity
    grouped.state.live = replace(grouped.state.live, uid=0)
    assert not grouped.controller.restore(identity).success
    assert grouped.store.records[identity].phase == "failed"
    assert grouped.groups.members(grouped.store.records[identity].managed_cgroup) == [4242]


def test_external_cgroup_move_not_overwritten(grouped):
    grouped.controller.apply(grouped.decision)
    identity = grouped.decision.process.identity
    record = grouped.store.records[identity]
    grouped.fs.files[Path(record.managed_cgroup) / "cgroup.procs"] = ""
    grouped.state.live = replace(grouped.state.live, cgroup="/external")
    assert grouped.controller.restore(identity).success
    assert grouped.state.live.cgroup == "/external"


def test_journal_written_before_every_group_mutation(grouped):
    original_put = grouped.store.put
    original_write = grouped.fs.write
    original_mkdir = grouped.fs.mkdir
    marker = {"ready": False}

    def persist(record):
        original_put(record)
        marker["ready"] = record.phase in {"pending", "restore_pending"}

    def write(path, value):
        assert marker.pop("ready", False), "missing pending journal before mutation"
        original_write(path, value)

    def mkdir(path):
        assert marker.pop("ready", False), "missing pending journal before mkdir"
        original_mkdir(path)

    grouped.store.put = persist
    grouped.fs.write = write
    grouped.fs.mkdir = mkdir
    assert grouped.controller.apply(grouped.decision).success
    assert grouped.controller.restore(grouped.decision.process.identity).success


def test_owned_path_restrictions(grouped):
    with pytest.raises(CgroupError):
        grouped.groups.members("/sys/fs/cgroup/other/ai-os-4242")
    with pytest.raises(CgroupError):
        grouped.groups.source_path("/delegated/../other")
    with pytest.raises(CgroupError):
        grouped.groups.managed_path("../../other")


def test_real_adapter_refuses_global_root_and_non_cgroup_paths():
    fs = CgroupFS()
    for path in ("/sys/fs/cgroup", "/tmp/delegated"):
        with pytest.raises(CgroupError):
            fs.validate(Path(path), 1000)


def test_sibling_group_path_in_journal_is_rejected(grouped):
    grouped.controller.apply(grouped.decision)
    identity = grouped.decision.process.identity
    record = grouped.store.records[identity]
    record.managed_cgroup = str(grouped.fs.root / "ai-os-9999-100.000000")
    grouped.store.put(record)
    before = list(grouped.fs.events)
    assert not grouped.controller.restore(identity).success
    assert grouped.fs.events == before
    assert identity in grouped.store.records


def test_memory_headroom_rechecked_after_group_setup(grouped):
    original_write = grouped.fs.write

    def write(path, value):
        original_write(path, value)
        if path.name == "memory.high":
            grouped.state.live = replace(grouped.state.live, rss=10000000)

    grouped.fs.write = write
    assert not grouped.controller.apply(grouped.decision).success
    assert grouped.state.live.cgroup == "/delegated/source"
    assert grouped.store.records
    assert not any(event[1] == "cgroup.procs" for event in grouped.fs.events)


def test_cgroup_move_identity_race_keeps_journal(grouped):
    # The fake backend's metrics do not call is_running, so these cover the
    # checks directly surrounding the numeric cgroup.procs write.
    grouped.proc.is_running.side_effect = [True, False]
    result = grouped.controller.apply(grouped.decision)
    assert not result.success and "during cgroup move" in result.reason
    assert grouped.store.records[grouped.decision.process.identity].phase == "failed"


def test_preexisting_group_is_never_claimed_or_restored(grouped):
    target = Path(grouped.groups.managed_path(grouped.decision.process.identity))
    grouped.fs.mkdir(target)
    grouped.fs.files[target / "cgroup.procs"] = "9999"
    before = list(grouped.fs.events)
    assert not grouped.controller.apply(grouped.decision).success
    assert not grouped.store.records
    assert grouped.controller.restore(grouped.decision.process.identity).success
    assert grouped.fs.files[target / "cgroup.procs"] == "9999"
    assert grouped.fs.events == before
