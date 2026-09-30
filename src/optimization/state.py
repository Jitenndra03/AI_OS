"""Private, crash-safe action journal. A held flock is required for every write."""

from __future__ import annotations

import copy
from dataclasses import asdict, fields
import fcntl
import json
import math
import os
from pathlib import Path
import stat
import threading
import uuid

from .models import Action, ActionRecord, WorkloadState

SCHEMA_VERSION = 1
_MAX_BYTES = 16 * 1024 * 1024
_PHASES = {"pending", "applied", "restore_pending", "failed"}
_ACTIONS = {Action.RENICE.value, Action.CGROUP.value, Action.CPU_LIMIT.value, Action.SUSPEND.value}


class StateError(RuntimeError):
    """The journal cannot safely be used; no process action should be taken."""


class StateLockedError(StateError):
    """Another controller owns this journal."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StateError("Duplicate JSON key in state journal")
        result[key] = value
    return result


def _number(value, *, minimum=0):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= minimum
    except OverflowError:
        return False


def _absolute(value):
    return (type(value) is str and value.startswith("/") and "\x00" not in value
            and ".." not in value.split("/"))


def _record(data):
    if type(data) is not dict or set(data) != {f.name for f in fields(ActionRecord)}:
        raise StateError("Invalid action record fields")
    integers = {"pid": (1, 2**31 - 1), "uid": (0, 2**32 - 2), "original_nice": (-20, 19)}
    for key, (low, high) in integers.items():
        if type(data[key]) is not int or not low <= data[key] <= high:
            raise StateError(f"Invalid record {key}")
    if not _number(data["create_time"]) or not _number(data["timestamp"]):
        raise StateError("Invalid record time")
    if data["identity"] != f'{data["pid"]}:{data["create_time"]:.6f}':
        raise StateError("Process identity does not match record")
    cgroup_optional = data["action"] in ("RENICE", "SUSPEND") and data["original_cgroup"] == ""
    if not _absolute(data["executable"]) or not (cgroup_optional or _absolute(data["original_cgroup"])):
        raise StateError("Record paths must be absolute and contain no traversal")
    if type(data["original_status"]) is not str or not data["original_status"]:
        raise StateError("Invalid original status")
    if type(data["action"]) is not str or data["action"] not in _ACTIONS:
        raise StateError("Invalid record action")
    if type(data["workload"]) is not str or data["workload"] not in {v.value for v in WorkloadState}:
        raise StateError("Invalid record workload")
    if type(data["phase"]) is not str or data["phase"] not in _PHASES:
        raise StateError("Invalid record phase")
    try:
        if str(uuid.UUID(data["boot_id"])) != data["boot_id"]:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise StateError("Invalid record boot ID") from None
    if data["applied_nice"] is not None and (
        type(data["applied_nice"]) is not int or not -20 <= data["applied_nice"] <= 19
    ):
        raise StateError("Invalid applied nice value")
    if data["managed_cgroup"] is not None and not _absolute(data["managed_cgroup"]):
        raise StateError("Invalid managed cgroup path")
    if data["error"] is not None and type(data["error"]) is not str:
        raise StateError("Invalid record error")
    if data["action"] == Action.RENICE.value:
        if (data["applied_nice"] is None or data["applied_nice"] < data["original_nice"]
                or data["managed_cgroup"] is not None):
            raise StateError("Renice record must describe a reversible priority reduction")
    elif data["action"] == Action.SUSPEND.value:
        if (data["original_status"] not in {"running", "sleeping"}
                or data["applied_nice"] is not None or data["managed_cgroup"] is not None):
            raise StateError("Suspend record must describe an originally running or sleeping process")
    else:
        expected_name = "ai-os-" + data["identity"].replace(":", "-")
        if (data["managed_cgroup"] is None or data["applied_nice"] is not None
                or Path(data["managed_cgroup"]).name != expected_name):
            raise StateError("Cgroup record must reference its own identity-derived managed group")
    return ActionRecord(**data)


class StateStore:
    """JSON journal with defensive record copies and an explicit lifetime lock.

    Existing state directories must belong to this user and must not be writable
    by other users. State and lock files must be private regular files. Symlinks
    are rejected throughout the directory path. Keep the store acquired for the
    entire controller lifetime, including process changes and recovery.
    """

    def __init__(self, path):
        self.path = Path(os.path.abspath(os.fspath(path)))
        self._mutex = threading.RLock()
        self._lock_fd = None
        self._owner_pid = None
        self._poisoned = False
        self._dir_fd = None
        self._records = {}
        self.boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        self._dir_fd = self._open_directory()
        try:
            self._records = self._read()
        except BaseException:
            os.close(self._dir_fd)
            self._dir_fd = None
            raise

    def _open_directory(self):
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in self.path.parent.parts[1:]:
                try:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except FileNotFoundError:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                        os.fsync(fd)
                    except FileExistsError:
                        pass  # Another instance may have created the directory.
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
                raise StateError("State directory must be owned by this user and not writable by others")
            return fd
        except BaseException:
            os.close(fd)
            raise

    @staticmethod
    def _check_file(fd):
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1):
            raise StateError("State and lock files must be private, owned regular files with one link")
        return info

    def _read(self):
        try:
            fd = os.open(self.path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._dir_fd)
        except FileNotFoundError:
            return {}
        try:
            info = self._check_file(fd)
            if info.st_size > _MAX_BYTES:
                raise StateError("State journal exceeds size limit")
            with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as handle:
                raw = handle.read(_MAX_BYTES + 1)
            if len(raw.encode("utf-8")) > _MAX_BYTES:
                raise StateError("State journal exceeds size limit")
            data = json.loads(raw, object_pairs_hook=_object)
            if (type(data) is not dict or set(data) != {"schema_version", "records"}
                    or type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION
                    or type(data["records"]) is not dict):
                raise StateError("Unsupported or malformed state journal")
            result = {}
            for identity, item in data["records"].items():
                record = _record(item)
                if identity != record.identity:
                    raise StateError("Journal key does not match process identity")
                result[identity] = record
            return result
        except (ValueError, UnicodeError, OverflowError, RecursionError) as exc:
            raise StateError("Corrupt state journal") from exc
        finally:
            os.close(fd)

    @property
    def records(self):
        with self._mutex:
            return copy.deepcopy(self._records)

    def acquire(self):
        with self._mutex:
            if self._lock_fd is not None:
                self._require_lock()
                return self
            if self._dir_fd is None:
                self._dir_fd = self._open_directory()
            fd = None
            try:
                fd = os.open(self.path.name + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                             0o600, dir_fd=self._dir_fd)
                self._check_file(fd)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise StateLockedError("Another controller owns the state journal") from exc
                self._records = self._read()
                self._lock_fd = fd
                self._owner_pid = os.getpid()
                self._poisoned = False
                return self
            except BaseException:
                if fd is not None:
                    os.close(fd)
                os.close(self._dir_fd)
                self._dir_fd = None
                raise

    def _require_lock(self):
        if self._lock_fd is None or self._owner_pid != os.getpid():
            raise StateError("Acquire the state journal lock before writing")
        if self._poisoned:
            raise StateError("Persistence failed after replacement; close and reacquire before continuing")

    def _persist(self, records):
        payload = json.dumps({"schema_version": SCHEMA_VERSION,
                              "records": {key: asdict(value) for key, value in records.items()}},
                             allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(payload) > _MAX_BYTES:
            raise StateError("State journal exceeds size limit")
        temporary = "." + self.path.name + "." + uuid.uuid4().hex + ".tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self._dir_fd)
        try:
            with os.fdopen(fd, "wb", closefd=False) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(fd)
            os.replace(temporary, self.path.name, src_dir_fd=self._dir_fd, dst_dir_fd=self._dir_fd)
            self._poisoned = True
            os.fsync(self._dir_fd)
            self._poisoned = False
        finally:
            os.close(fd)
            try:
                os.unlink(temporary, dir_fd=self._dir_fd)
            except FileNotFoundError:
                pass

    def put(self, record):
        with self._mutex:
            self._require_lock()
            validated = _record(asdict(record))
            updated = dict(self._records)
            updated[validated.identity] = validated
            self._persist(updated)
            self._records = updated

    def remove(self, identity):
        with self._mutex:
            self._require_lock()
            if identity not in self._records:
                return
            updated = dict(self._records)
            del updated[identity]
            self._persist(updated)
            self._records = updated

    def close(self):
        with self._mutex:
            # Closing (rather than explicit LOCK_UN) also behaves safely in a fork.
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None
                self._owner_pid = None
            if self._dir_fd is not None:
                os.close(self._dir_fd)
                self._dir_fd = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.close()
