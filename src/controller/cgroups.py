"""Small cgroup v2 interface restricted to an explicitly delegated subtree."""

import math
import os
from pathlib import Path


class CgroupError(RuntimeError):
    pass


class CgroupExistsError(CgroupError):
    """Creation never succeeded; existing contents must never be claimed."""


class CgroupFS:
    """Real filesystem adapter; fake adapters may be injected by unit tests."""

    mount = Path("/sys/fs/cgroup")

    def validate(self, root, uid):
        if root == self.mount or self.mount not in root.parents:
            raise CgroupError("A delegated child under /sys/fs/cgroup is required")
        self.check_path(root)
        mounts = Path("/proc/self/mountinfo").read_text().splitlines()
        if not any(line.split()[4] == str(self.mount) and " - cgroup2 " in line for line in mounts):
            raise CgroupError("A cgroup v2 mount is required")
        if root.stat().st_uid != uid or uid != os.geteuid():
            raise CgroupError("Delegated root must be owned by the acting user")
        if not os.access(root / "cgroup.procs", os.W_OK):
            raise CgroupError("Delegated root is not writable")

    def check_path(self, path):
        if ".." in path.parts or self.mount not in path.parents and path != self.mount:
            raise CgroupError("Cgroup path escapes the mount")
        for parent in [path, *path.parents]:
            if parent.is_symlink():
                raise CgroupError("Symlink in cgroup path")
            if parent == self.mount:
                break

    def read(self, path):
        self.check_path(path)
        return path.read_text()

    def write(self, path, value):
        self.check_path(path)
        fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW)
        try:
            os.write(fd, value.encode())
        finally:
            os.close(fd)

    def mkdir(self, path):
        self.check_path(path)
        path.mkdir(mode=0o700)

    def exists(self, path):
        self.check_path(path)
        return path.exists()

    def rmdir(self, path):
        self.check_path(path)
        path.rmdir()  # Refuses populated groups and unexpected child groups.


class CgroupV2:
    def __init__(self, root, owner_uid, *, fs=None):
        self.fs = fs or CgroupFS()
        self.root = Path(root)
        self.fs.validate(self.root, owner_uid)

    def managed_path(self, identity):
        if not identity or any(c not in "0123456789:." for c in identity):
            raise CgroupError("Invalid process identity")
        return str(self.root / ("ai-os-" + identity.replace(":", "-")))

    def assert_available(self, identity):
        if self.fs.exists(Path(self.managed_path(identity))):
            raise CgroupExistsError("Managed group already exists; cannot claim its contents")

    def _managed(self, path):
        path = Path(path)
        if path.parent != self.root or not path.name.startswith("ai-os-"):
            raise CgroupError("Group is outside the owned child namespace")
        return path

    def source_path(self, original):
        if not original.startswith("/") or ".." in Path(original).parts:
            raise CgroupError("Invalid original cgroup")
        source = self.fs.mount / original.lstrip("/")
        # Restoration is supported only inside the explicitly delegated tree.
        if source != self.root and self.root not in source.parents:
            raise CgroupError("Original group is outside the delegated subtree")
        self.fs.check_path(source)
        return source

    def prepare(self, record, decision, persist):
        if record.managed_cgroup != self.managed_path(record.identity):
            raise CgroupError("Managed group does not match the journal identity")
        target = self._managed(record.managed_cgroup)
        self.source_path(record.original_cgroup)
        controllers = set(self.fs.read(self.root / "cgroup.subtree_control").split())
        quota = decision.cpu_quota_percent
        if quota is not None and (not math.isfinite(quota) or quota <= 0 or "cpu" not in controllers):
            raise CgroupError("CPU quota requires an enabled delegated CPU controller")
        memory = decision.memory_high_bytes
        if memory is not None and (type(memory) is not int or memory <= 0 or "memory" not in controllers):
            raise CgroupError("memory.high requires an enabled delegated memory controller")
        if self.fs.exists(target):
            raise CgroupExistsError("Managed group already exists; cannot claim its contents")
        persist()
        try:
            self.fs.mkdir(target)
        except FileExistsError as exc:
            raise CgroupExistsError("Managed group was created externally") from exc
        if quota is not None:
            persist()
            self.fs.write(target / "cpu.max", f"{max(1000, int(quota * 1000))} 100000")
        if memory is not None:
            persist()
            self.fs.write(target / "memory.high", str(memory))

    def members(self, path):
        target = self._managed(path)
        if not self.fs.exists(target):
            return []
        return [int(pid) for pid in self.fs.read(target / "cgroup.procs").split()]

    def current_path(self, absolute):
        return "/" + str(Path(absolute).relative_to(self.fs.mount))

    def move(self, proc, target, persist):
        self.fs.check_path(Path(target))
        persist()
        if not proc.is_running():
            raise CgroupError("Process identity changed before cgroup move")
        self.fs.write(Path(target) / "cgroup.procs", str(proc.pid))
        # Linux cgroup.procs accepts a numeric PID, not a pidfd: this cannot
        # atomically bind identity to the write. Detect a race and retain the
        # pending journal; never attempt rollback against a reused PID.
        if not proc.is_running():
            raise CgroupError("Process exited or identity changed during cgroup move; recovery required")

    def cleanup(self, path, persist):
        target = self._managed(path)
        if self.fs.exists(target):
            if self.members(path):
                raise CgroupError("Managed group remains occupied")
            persist()
            self.fs.rmdir(target)
