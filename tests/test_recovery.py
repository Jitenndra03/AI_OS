"""Recovery pipe lifetime is tested using disposable subprocesses and empty journals."""
import os
from pathlib import Path
import select
import subprocess
import sys
import pytest
from src.optimization.recovery import RecoveryGuard, wait_for_owner

ROOT = Path(__file__).resolve().parents[1]

def child_environment():
    return {**os.environ, "PYTHONPATH": str(ROOT)}

def test_wait_for_owner_requires_both_descriptors():
    with pytest.raises(ValueError):
        wait_for_owner(None, None)

def test_pipe_eof_releases_worker_on_normal_owner_close():
    lease_read, lease_write = os.pipe()
    ready_read, ready_write = os.pipe()
    code = "from src.optimization.recovery import wait_for_owner; import sys; wait_for_owner(int(sys.argv[1]), int(sys.argv[2])); print('RECOVERED', flush=True)"
    worker = subprocess.Popen([sys.executable, "-c", code, str(lease_read), str(ready_write)],
                              pass_fds=(lease_read, ready_write), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, env=child_environment())
    os.close(lease_read)
    os.close(ready_write)
    try:
        assert select.select([ready_read], [], [], 5)[0]
        assert os.read(ready_read, 6) == b"READY\n"
        assert worker.poll() is None
        os.close(lease_write)
        lease_write = None
        out, err = worker.communicate(timeout=5)
        assert worker.returncode == 0, err
        assert out.strip() == "RECOVERED"
    finally:
        os.close(ready_read)
        if lease_write is not None:
            os.close(lease_write)
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=5)

def test_pipe_eof_releases_independent_worker_when_disposable_owner_crashes():
    code = r'''
import os, subprocess, sys
read, write = os.pipe()
ready_read, ready_write = os.pipe()
worker_code = "from src.optimization.recovery import wait_for_owner; import sys; wait_for_owner(int(sys.argv[1]), int(sys.argv[2])); print('RECOVERED', flush=True)"
subprocess.Popen([sys.executable, '-c', worker_code, str(read), str(ready_write)], pass_fds=(read, ready_write), start_new_session=True)
os.close(read)
os.close(ready_write)
assert os.read(ready_read, 6) == b'READY\n'
os.close(ready_read)
print('OWNER_READY', flush=True)
sys.stdin.buffer.read(1)
'''
    owner = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, env=child_environment())
    try:
        assert select.select([owner.stdout], [], [], 5)[0]
        assert owner.stdout.readline().strip() == "OWNER_READY"
        owner.kill()  # Only this test's disposable owner; no managed OS process.
        out, err = owner.communicate(timeout=5)
        assert owner.returncode < 0, err
        assert "RECOVERED" in out
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)

@pytest.mark.skipif(os.getuid() == 0, reason="Live recovery CLI correctly rejects UID 0")
def test_recovery_guard_starts_and_closes_with_empty_temporary_journal(tmp_path):
    guard = RecoveryGuard(tmp_path / "private" / "state.json", os.getuid())
    try:
        guard.start()
        assert guard.healthy()
    finally:
        guard.close()
    assert guard.worker.wait(timeout=5) == 0
    assert not guard.healthy()
