"""Approved finite operations in a bounded PTY-backed process session."""
from dataclasses import dataclass
import errno
import hmac
import math
import os
import pty
import selectors
import signal
import subprocess
import time
from typing import Optional

from nli.models import CommandProposal
from nli.safety_validator import approval_digest, validate_proposal


@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    output: str
    exit_code: Optional[int] = None
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False

    def __iter__(self):
        # Retain the former success/output unpacking API.
        yield self.success
        yield self.output


def _kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _run_pty(argv, cwd, timeout_sec, max_output_bytes, cancel_event=None):
    """Internal transport. Public callers must enter through execute_proposal."""
    master = slave = None
    process = None
    chunks = bytearray()
    timed_out = cancelled = truncated = False
    try:
        master, slave = pty.openpty()
        process = subprocess.Popen(
            argv, cwd=cwd, stdin=slave, stdout=slave, stderr=slave,
            start_new_session=True, close_fds=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TERM": "dumb"},
        )
        os.close(slave)
        slave = None
        os.set_blocking(master, False)
        deadline = time.monotonic() + timeout_sec
        with selectors.DefaultSelector() as selector:
            selector.register(master, selectors.EVENT_READ)
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                if process.poll() is not None:
                    # A descendant must not outlive its approved operation.
                    _kill_group(process)
                if not selector.select(min(0.05, max(0, deadline - time.monotonic()))):
                    if process.poll() is not None:
                        break
                    continue
                try:
                    data = os.read(master, 8192)
                except BlockingIOError:
                    continue
                except OSError as error:
                    if error.errno == errno.EIO:  # Linux PTY end-of-stream
                        break
                    raise
                if not data:
                    break
                room = max_output_bytes - len(chunks)
                chunks.extend(data[:room])
                if len(data) > room:
                    truncated = True
                    break
        if not (timed_out or cancelled or truncated):
            remaining = max(0.001, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                timed_out = True
    except KeyboardInterrupt:
        cancelled = True
    except (OSError, ValueError) as error:
        return ExecutionResult(False, f"Execution failed: {error}")
    finally:
        if process is not None:
            _kill_group(process)
            process.wait()
        for fd in (master, slave):
            if fd is not None:
                os.close(fd)
    output = chunks.decode("utf-8", errors="replace").replace("\r\n", "\n")
    code = process.returncode if process is not None else None
    success = code == 0 and not (timed_out or cancelled or truncated)
    return ExecutionResult(success, output, code, timed_out, cancelled, truncated)


def execute_proposal(proposal: CommandProposal, timeout_sec: float = 30, *,
                     approved_digest: Optional[str] = None, max_output_bytes: int = 65536,
                     cancel_event=None) -> ExecutionResult:
    """Revalidate and execute only the exact proposal explicitly approved by caller.

    CLI consent supplies approved_digest. API users must obtain consent before
    passing approval_digest(proposal); a digest is a binding, not authentication.
    Cancellation, timeout and output overflow terminate the whole child group.
    """
    valid, reason, _ = validate_proposal(proposal)
    if not valid:
        return ExecutionResult(False, reason)
    if (type(approved_digest) is not str or not approved_digest.isascii()
            or not hmac.compare_digest(approved_digest, approval_digest(proposal))):
        return ExecutionResult(False, "Explicit approval of this exact proposal is required.")
    if (isinstance(timeout_sec, bool) or not isinstance(timeout_sec, (int, float))
            or not math.isfinite(timeout_sec) or timeout_sec <= 0 or timeout_sec > 300):
        return ExecutionResult(False, "Timeout must be finite and between 0 and 300 seconds.")
    if type(max_output_bytes) is not int or not 0 < max_output_bytes <= 1048576:
        return ExecutionResult(False, "Output limit must be between 1 and 1048576 bytes.")
    return _run_pty(proposal.argv, proposal.cwd, timeout_sec, max_output_bytes, cancel_event)
