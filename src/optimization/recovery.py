"""Independent recovery worker released when the optimizer exits, even SIGKILL."""

import os
import select
import subprocess
import sys
import time
from pathlib import Path


class RecoveryGuard:
    def __init__(self, state_path, owner_uid, *, cgroup_root=None, allow_suspend=False):
        self.state_path = Path(state_path)
        self.owner_uid = owner_uid
        self.cgroup_root = cgroup_root
        self.allow_suspend = allow_suspend
        self.worker = None
        self._lease = None

    def start(self):
        lease_read, lease_write = os.pipe()
        ready_read, ready_write = os.pipe()
        command = [sys.executable, str(Path(__file__).resolve().parents[2] / "main.py"),
                   "--recover", "--state-file", str(self.state_path.resolve()),
                   "--target-uid", str(self.owner_uid), "--watch-fd", str(lease_read),
                   "--ready-fd", str(ready_write)]
        if self.cgroup_root:
            command.extend(["--cgroup-root", str(Path(self.cgroup_root).resolve())])
        if self.allow_suspend:
            command.append("--allow-suspend")
        try:
            self.worker = subprocess.Popen(command, pass_fds=(lease_read, ready_write),
                                           start_new_session=True, stdin=subprocess.DEVNULL)
            os.close(lease_read)
            lease_read = None
            os.close(ready_write)
            ready_write = None
            ready, _, _ = select.select([ready_read], [], [], 10)
            if not ready or os.read(ready_read, 6) != b"READY\n":
                raise RuntimeError("Recovery worker failed to start; refusing live optimization")
            self._lease = lease_write
            lease_write = None
        finally:
            for fd in (lease_read, lease_write, ready_read, ready_write):
                if fd is not None:
                    os.close(fd)

    def healthy(self):
        return self.worker is not None and self.worker.poll() is None

    def close(self):
        # The owner must first restore/release its state lock.
        if self._lease is not None:
            os.close(self._lease)
            self._lease = None
        if self.worker:
            try:
                self.worker.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass  # Independent recovery remains alive to retry.


def wait_for_owner(lease_fd, ready_fd):
    if lease_fd is None or ready_fd is None:
        raise ValueError("Recovery watchdog requires both pipe descriptors")
    try:
        os.write(ready_fd, b"READY\n")
    finally:
        os.close(ready_fd)
    try:
        while os.read(lease_fd, 1):
            pass
    finally:
        os.close(lease_fd)


def recover(state_path, owner_uid, *, cgroup_root=None, allow_suspend=False, retry=False):
    from src.controller.process_controller import ResourceController
    from src.optimization.state import StateStore
    from src.utils.logger import system_logger

    while True:
        try:
            with StateStore(state_path) as store:
                controller = ResourceController(store, owner_uid, cgroup_root=cgroup_root,
                                                allow_suspend=allow_suspend)
                for result in controller.restore_all():
                    system_logger.info("Recovery %s: %s", result.identity, result.reason)
                pending = bool(store.records)
            if not pending:
                return 0
            system_logger.error("Recovery has unresolved records; journal retained at %s", state_path)
        except (OSError, ValueError, RuntimeError) as exc:
            system_logger.error("Recovery unavailable: %s", exc)
        if not retry:
            return 1
        time.sleep(5)
