"""Real harmless PTY sessions and password-gate checks; never invokes sudo/apt."""
from dataclasses import replace
import errno
import os
import pty
import select
import sys
import termios
import threading
import time
from types import SimpleNamespace

import pytest

import nli.command_executor as executor
from nli.safety_validator import approval_digest, make_proposal


def terminal_session(monkeypatch, tmp_path, code, *, reply=None, cancel=None,
                     timeout=0.01, limit=4096):
    """Give the transport a real outer terminal instead of pytest's pipes."""
    master, slave = pty.openpty()
    before = termios.tcgetattr(slave)
    host = SimpleNamespace(fileno=lambda: slave, isatty=lambda: True)
    monkeypatch.setattr(executor.sys, "stdin", host)
    monkeypatch.setattr(executor.sys, "stdout", host)
    monkeypatch.setattr(executor, "_kill_group", lambda proc: pytest.fail("privileged SIGKILL forbidden"))
    output = bytearray()
    done = threading.Event()

    def interact():
        sent = False
        while not done.is_set():
            if select.select([master], [], [], 0.05)[0]:
                try:
                    data = os.read(master, 8192)
                except OSError as error:
                    if error.errno == errno.EIO:
                        return
                    raise
                output.extend(data)
                if not sent and b"READY" in output:
                    sent = True
                    if reply:
                        os.write(master, reply)
                    if cancel:
                        cancel.set()

    thread = threading.Thread(target=interact, daemon=True)
    thread.start()
    try:
        result = executor._run_interactive_pty(
            [sys.executable, "-c", code], str(tmp_path), timeout,
            limit, cancel, privileged=True,
        )
        after = termios.tcgetattr(slave)
        # Read any last streamed bytes before closing our terminal.
        time.sleep(0.06)
        return result, bytes(output), before, after
    finally:
        done.set()
        thread.join(timeout=1)
        os.close(master)
        os.close(slave)


def test_interactive_password_is_not_echoed_or_recorded(monkeypatch, tmp_path):
    code = """
import fcntl, getpass, sys, termios
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
password = getpass.getpass('READY password: ')
assert password == 'secret-sentinel-794'
print('AUTHENTICATED', flush=True)
"""
    result, streamed, before, after = terminal_session(
        monkeypatch, tmp_path, code, reply=b"secret-sentinel-794\n",
    )
    assert result.success
    assert "AUTHENTICATED" in result.output
    assert b"AUTHENTICATED" in streamed
    assert "secret-sentinel" not in result.output
    assert b"secret-sentinel" not in streamed
    assert before == after


def test_privileged_output_overflow_and_timeout_never_kill_transaction(monkeypatch, tmp_path):
    code = "import time; time.sleep(.10); print('A'*5000); print('FINISHED', flush=True)"
    result, streamed, before, after = terminal_session(
        monkeypatch, tmp_path, code, timeout=0.01, limit=100,
    )
    assert result.success and result.truncated and not result.timed_out
    assert len(result.output) == 100
    assert b"FINISHED" in streamed
    assert before == after


def test_privileged_cancel_waits_for_clean_exit(monkeypatch, tmp_path):
    marker = tmp_path / "cleanup_complete"
    code = f"""
import signal, time
from pathlib import Path
stopping = False
def stop(sig, frame):
    global stopping
    stopping = True
signal.signal(signal.SIGINT, stop)
print('READY', flush=True)
while not stopping:
    time.sleep(.01)
time.sleep(.10)
Path({str(marker)!r}).write_text('done')
print('CLEANUP COMPLETE', flush=True)
"""
    result, streamed, before, after = terminal_session(
        monkeypatch, tmp_path, code, cancel=threading.Event(),
    )
    assert result.cancelled and not result.success and result.exit_code == 0
    assert marker.read_text() == "done"
    assert b"CLEANUP COMPLETE" in streamed
    assert before == after


@pytest.mark.parametrize("interactive,tty,is_root", [
    (False, True, False), (True, False, False), (True, True, True),
])
def test_privileged_gate_blocks_noninteractive_and_root(monkeypatch, interactive, tty, is_root):
    proposal = replace(make_proposal("current_directory"), requires_sudo=True)
    monkeypatch.setattr(executor, "validate_proposal", lambda p: (True, "ok", None))
    monkeypatch.setattr(executor, "approval_digest", lambda p: "approved-test")
    monkeypatch.setattr(executor.sys, "stdin", SimpleNamespace(isatty=lambda: tty))
    monkeypatch.setattr(executor.sys, "stdout", SimpleNamespace(isatty=lambda: tty))
    monkeypatch.setattr(executor.os, "geteuid", lambda: 0 if is_root else 1000)
    monkeypatch.setattr(executor, "_run_interactive_pty", lambda *a, **k: pytest.fail("must not spawn"))
    result = executor.execute_proposal(
        proposal, approved_digest="approved-test", interactive=interactive,
    )
    assert not result.success
    assert "password" in result.output


def test_interactive_value_requires_exact_boolean():
    proposal = make_proposal("current_directory")
    result = executor.execute_proposal(
        proposal, approved_digest=approval_digest(proposal), interactive="yes",
    )
    assert not result.success and "boolean" in result.output


def test_interactive_spawn_failure_restores_terminal(monkeypatch, tmp_path):
    def denied(*args, **kwargs):
        raise OSError("spawn unavailable")
    monkeypatch.setattr(executor.subprocess, "Popen", denied)
    result, _, before, after = terminal_session(monkeypatch, tmp_path, "pass")
    assert not result.success
    assert "spawn unavailable" in result.output
    assert before == after


def test_raw_terminal_ctrl_c_requests_clean_child_exit(monkeypatch, tmp_path):
    code = """
import fcntl, signal, termios, time
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
stopping = False
def stop(sig, frame):
    global stopping
    stopping = True
signal.signal(signal.SIGINT, stop)
print('READY', flush=True)
while not stopping:
    time.sleep(.01)
print('INTERRUPTED CLEANLY', flush=True)
"""
    result, streamed, before, after = terminal_session(
        monkeypatch, tmp_path, code, reply=b"\x03",
    )
    assert result.cancelled and result.exit_code == 0
    assert b"INTERRUPTED CLEANLY" in streamed
    assert before == after
