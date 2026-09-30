"""State persistence must precede process mutation and survive controller death."""

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from src.optimization.models import ActionRecord
from src.optimization.state import StateError, StateLockedError, StateStore


def record(store):
    return ActionRecord(identity="123:100.125000", pid=123, create_time=100.125,
                        executable="/usr/bin/worker", uid=os.geteuid(), original_nice=0,
                        original_cgroup="/user.slice", original_status="sleeping",
                        action="RENICE", timestamp=101.0, workload="COMPILATION",
                        boot_id=store.boot_id, applied_nice=10)


def test_roundtrip_updates_and_removes(tmp_path):
    path = tmp_path / "state.json"
    with StateStore(path) as store:
        pending = record(store)
        store.put(pending)
        pending.phase = "failed"
        assert store.records[pending.identity].phase == "pending"
        snapshot = store.records
        snapshot[pending.identity].phase = "failed"
        assert store.records[pending.identity].phase == "pending"
        store.put(replace(pending, phase="applied"))
    with StateStore(path) as reopened:
        assert reopened.records[pending.identity].phase == "applied"
        reopened.remove(pending.identity)
        assert reopened.records == {}
    with StateStore(path) as reopened:
        assert reopened.records == {}
    assert path.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "state.json.lock").stat().st_mode & 0o777 == 0o600


def test_process_crash_preserves_pending_and_releases_lock(tmp_path):
    path = tmp_path / "state.json"
    script = '''
import os
from src.optimization.models import ActionRecord
from src.optimization.state import StateStore
s = StateStore(os.environ["JOURNAL_PATH"]).acquire()
s.put(ActionRecord("123:1.000000", 123, 1.0, "/bin/worker", os.geteuid(), 0,
                   "/", "sleeping", "RENICE", 2.0, "GAMING", boot_id=s.boot_id, applied_nice=10))
os._exit(7)
'''
    result = subprocess.run([sys.executable, "-c", script], env={**os.environ,
                            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                            "JOURNAL_PATH": str(path)}, check=False)
    assert result.returncode == 7
    with StateStore(path) as store:
        assert store.records["123:1.000000"].phase == "pending"


def test_lock_collision_and_unlocked_mutation(tmp_path):
    first = StateStore(tmp_path / "state.json")
    second = StateStore(first.path)
    try:
        with pytest.raises(StateError):
            first.put(record(first))
        first.acquire()
        with pytest.raises(StateLockedError):
            second.acquire()
        first.put(record(first))
        first.close()
        second.acquire()
        assert second.records
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("operation", ["put", "remove"])
def test_failed_replace_preserves_memory_and_disk(tmp_path, monkeypatch, operation):
    with StateStore(tmp_path / "state.json") as store:
        item = record(store)
        store.put(item)
        before = store.path.read_bytes()
        def fail(*args, **kwargs):
            raise OSError("disk failure")
        monkeypatch.setattr(os, "replace", fail)
        with pytest.raises(OSError):
            if operation == "put":
                store.put(replace(item, phase="applied"))
            else:
                store.remove(item.identity)
        assert store.records[item.identity].phase == "pending"
        assert store.path.read_bytes() == before
        assert not list(tmp_path.glob("*.tmp"))


def test_failed_directory_fsync_poisons_until_reloaded(tmp_path, monkeypatch):
    with StateStore(tmp_path / "state.json") as store:
        item = record(store)
        original_fsync = os.fsync
        def fail_directory(fd):
            if fd == store._dir_fd:
                raise OSError("directory sync failure")
            original_fsync(fd)
        with monkeypatch.context() as patch:
            patch.setattr(os, "fsync", fail_directory)
            with pytest.raises(OSError):
                store.put(item)
        assert store.records == {}
        with pytest.raises(StateError, match="Persistence failed"):
            store.put(item)
        store.close()
        store.acquire()
        assert store.records[item.identity] == item


@pytest.mark.parametrize("change", [
    {"pid": True}, {"pid": 0}, {"uid": -1}, {"create_time": float("nan")},
    {"timestamp": float("inf")}, {"identity": "123:999.000000"},
    {"action": "RESTORE"}, {"action": []}, {"workload": "INVALID"},
    {"phase": "done"}, {"boot_id": "old-boot"}, {"original_nice": -21},
    {"applied_nice": 20}, {"applied_nice": True}, {"executable": "worker"},
    {"original_cgroup": "/../escape"}, {"managed_cgroup": "relative"},
    {"error": {}}, {"original_status": None},
])
def test_invalid_records_rejected_on_read_and_write(tmp_path, change):
    path = tmp_path / "state.json"
    with StateStore(path) as store:
        item = record(store)
        with pytest.raises(StateError):
            store.put(replace(item, **change))
        assert store.records == {}
        invalid = {**asdict(item), **change}
        path.write_text(json.dumps({"schema_version": 1, "records": {item.identity: invalid}}))
        path.chmod(0o600)
    with pytest.raises(StateError):
        StateStore(path)


@pytest.mark.parametrize("contents", ["", "{", "[]", '{"schema_version":2,"records":{}}',
    '{"schema_version":1,"schema_version":1,"records":{}}',
    '{"schema_version":true,"records":{}}', '{"schema_version":1,"records":[]}',
    '{"schema_version":1,"records":{},"unexpected":true}'])
def test_corrupt_journal_fails_closed(tmp_path, contents):
    path = tmp_path / "state.json"
    path.write_text(contents)
    path.chmod(0o600)
    with pytest.raises(StateError):
        StateStore(path)
    assert path.read_text() == contents


def test_journal_key_identity_mismatch_rejected(tmp_path):
    path = tmp_path / "state.json"
    with StateStore(path) as store:
        data = asdict(record(store))
    path.write_text(json.dumps({"schema_version": 1, "records": {"wrong": data}}))
    path.chmod(0o600)
    with pytest.raises(StateError):
        StateStore(path)


def test_empty_cgroup_only_for_actions_not_using_cgroups(tmp_path):
    with StateStore(tmp_path / "state.json") as store:
        item = replace(record(store), original_cgroup="")
        store.put(item)
        with pytest.raises(StateError):
            store.put(replace(item, action="CGROUP"))


@pytest.mark.parametrize("target", ["state", "lock", "directory"])
def test_symlinks_rejected(tmp_path, target):
    path = tmp_path / "state.json"
    real = tmp_path / "real"
    if target == "directory":
        real.mkdir(mode=0o700)
        (tmp_path / "link").symlink_to(real, target_is_directory=True)
        path = tmp_path / "link" / "state.json"
    else:
        real.write_text('{}')
        real.chmod(0o600)
        link = path if target == "state" else tmp_path / "state.json.lock"
        link.symlink_to(real)
    with pytest.raises(OSError):
        with StateStore(path):
            pass
    if target != "directory":
        assert real.read_text() == '{}'


@pytest.mark.parametrize("target", ["state", "lock", "directory"])
def test_insecure_permissions_rejected(tmp_path, target):
    path = tmp_path / "state.json"
    if target == "directory":
        tmp_path.chmod(0o777)
    else:
        bad = path if target == "state" else tmp_path / "state.json.lock"
        bad.write_text('{"schema_version":1,"records":{}}')
        bad.chmod(0o644)
    try:
        with pytest.raises(StateError):
            with StateStore(path):
                pass
    finally:
        tmp_path.chmod(0o700)


def test_new_directory_is_private(tmp_path):
    with StateStore(tmp_path / "private" / "state.json"):
        assert (tmp_path / "private").stat().st_mode & 0o777 == 0o700


def test_failed_file_fsync_keeps_previous_journal(tmp_path, monkeypatch):
    with StateStore(tmp_path / "state.json") as store:
        item = record(store)
        store.put(item)
        before = store.path.read_bytes()
        def fail(fd):
            raise OSError("file fsync failed")
        monkeypatch.setattr(os, "fsync", fail)
        with pytest.raises(OSError):
            store.put(replace(item, phase="applied"))
        assert store.records[item.identity].phase == "pending"
        assert store.path.read_bytes() == before


def test_foreign_file_owner_rejected(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    with StateStore(path) as store:
        store.put(record(store))
    real_fstat = os.fstat
    def foreign_owner(fd):
        info = real_fstat(fd)
        if info.st_ino == path.stat().st_ino:
            altered = list(info)
            altered[4] = info.st_uid + 1
            return os.stat_result(altered)
        return info
    monkeypatch.setattr(os, "fstat", foreign_owner)
    with pytest.raises(StateError, match="owned"):
        StateStore(path)


def test_hard_link_rejected(tmp_path):
    path = tmp_path / "state.json"
    with StateStore(path) as store:
        store.put(record(store))
    os.link(path, tmp_path / "alias")
    with pytest.raises(StateError, match="one link"):
        StateStore(path)


@pytest.mark.parametrize("changes", [
    {"applied_nice": None}, {"applied_nice": -1},
    {"managed_cgroup": "/delegated/ai-os-123-100.125000"},
    {"action": "SUSPEND", "applied_nice": None, "original_status": "stopped"},
    {"action": "SUSPEND", "applied_nice": None, "original_status": "tracing-stop"},
    {"action": "SUSPEND", "original_status": "running"},
    {"action": "SUSPEND", "applied_nice": None, "managed_cgroup": "/delegated/ai-os-123-100.125000"},
    {"action": "CGROUP", "applied_nice": None},
    {"action": "CPU_LIMIT", "managed_cgroup": "/delegated/ai-os-123-100.125000"},
    {"action": "CGROUP", "applied_nice": None, "managed_cgroup": "/delegated/ai-os-999-100.125000"},
])
def test_inconsistent_action_fields_rejected(tmp_path, changes):
    path = tmp_path / "state.json"
    with StateStore(path) as store:
        item = replace(record(store), **changes)
        with pytest.raises(StateError):
            store.put(item)
        path.write_text(json.dumps({"schema_version": 1, "records": {item.identity: asdict(item)}}))
        path.chmod(0o600)
    with pytest.raises(StateError):
        StateStore(path)


@pytest.mark.parametrize("action", ["SUSPEND", "CGROUP", "CPU_LIMIT"])
def test_valid_non_nice_record_roundtrip(tmp_path, action):
    path = tmp_path / "state.json"
    with StateStore(path) as store:
        item = replace(record(store), action=action, applied_nice=None,
                       managed_cgroup=None if action == "SUSPEND" else "/delegated/ai-os-123-100.125000")
        store.put(item)
    with StateStore(path) as store:
        assert store.records[item.identity] == item
