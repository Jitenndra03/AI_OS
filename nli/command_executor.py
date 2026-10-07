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
import sys
import termios
import tty
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



def _interrupt_group(process):
    """Request an orderly stop; privileged package transactions are never killed."""
    try:
        os.killpg(process.pid, signal.SIGINT)
    except ProcessLookupError:
        pass


def _wait_privileged(process):
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            _interrupt_group(process)


def _run_interactive_pty(argv, cwd, timeout_sec, max_output_bytes,
                         cancel_event=None, *, privileged=False):
    """Relay a terminal without recording input, including passwords.

    The trusted privileged worker acquires its controlling terminal itself.
    Privileged operations have no deadline: interruption requests SIGINT and
    awaits orderly cleanup. Log overflow truncates retained output only.
    """
    master = slave = process = saved_terminal = None
    chunks = bytearray()
    timed_out = cancelled = truncated = False
    transport_error = None
    try:
        input_fd, output_fd = sys.stdin.fileno(), sys.stdout.fileno()
        master, slave = pty.openpty()
        child_attrs = termios.tcgetattr(slave)
        child_attrs[3] &= ~(termios.ECHO | termios.ECHONL)
        termios.tcsetattr(slave, termios.TCSANOW, child_attrs)
        saved_terminal = termios.tcgetattr(input_fd)
        tty.setraw(input_fd, termios.TCSANOW)
        process = subprocess.Popen(
            argv, cwd=cwd, stdin=slave, stdout=slave, stderr=slave,
            start_new_session=True, close_fds=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TERM": "xterm"},
        )
        os.close(slave)
        slave = None
        os.set_blocking(master, False)
        deadline = None if privileged else time.monotonic() + timeout_sec
        interrupt_sent = False
        with selectors.DefaultSelector() as selector:
            selector.register(master, selectors.EVENT_READ, "child")
            selector.register(input_fd, selectors.EVENT_READ, "input")
            child_closed = False
            while not child_closed:
                if cancel_event is not None and cancel_event.is_set() and not interrupt_sent:
                    cancelled = True
                    if not privileged:
                        break
                    _interrupt_group(process)
                    interrupt_sent = True
                if deadline is not None and time.monotonic() >= deadline:
                    timed_out = True
                    break
                events = selector.select(0.05)
                if not events and process.poll() is not None:
                    break
                for key, _ in events:
                    if key.data == "input":
                        data = os.read(input_fd, 4096)
                        if not data:
                            selector.unregister(input_fd)
                            cancelled = True
                            if privileged:
                                if not interrupt_sent:
                                    _interrupt_group(process)
                                    interrupt_sent = True
                            else:
                                child_closed = True
                            continue
                        # Input is never copied into captured output or diagnostics.
                        if b"\x03" in data:
                            cancelled = True
                        pending = memoryview(data)
                        while pending:
                            try:
                                count = os.write(master, pending)
                                pending = pending[count:]
                            except BlockingIOError:
                                time.sleep(0.01)
                            except OSError as error:
                                if error.errno not in (errno.EIO, errno.EPIPE):
                                    raise
                                child_closed = True
                                break
                    else:
                        try:
                            data = os.read(master, 8192)
                        except BlockingIOError:
                            continue
                        except OSError as error:
                            if error.errno == errno.EIO:
                                child_closed = True
                                break
                            raise
                        if not data:
                            child_closed = True
                            break
                        # Stream once; the CLI must not print result.output again.
                        pending = memoryview(data)
                        while pending:
                            count = os.write(output_fd, pending)
                            pending = pending[count:]
                        room = max(0, max_output_bytes - len(chunks))
                        chunks.extend(data[:room])
                        if len(data) > room:
                            truncated = True
                            if not privileged:
                                child_closed = True
                                break
        if not privileged and not (timed_out or cancelled or truncated):
            try:
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
    except KeyboardInterrupt:
        cancelled = True
        if process is not None and privileged:
            _interrupt_group(process)
    except (OSError, ValueError) as error:
        transport_error = f"Terminal execution failed: {error}"
        if process is not None and privileged:
            _interrupt_group(process)
    finally:
        if process is not None:
            if privileged:
                _wait_privileged(process)
            else:
                _kill_group(process)
                process.wait()
        if saved_terminal is not None:
            try:
                termios.tcsetattr(input_fd, termios.TCSANOW, saved_terminal)
            except OSError:
                # The user may have closed/disconnected the outer terminal.
                pass
        for fd in (master, slave):
            if fd is not None:
                os.close(fd)
    output = chunks.decode("utf-8", errors="replace").replace("\r\n", "\n")
    if transport_error:
        output += "\n" + transport_error
    code = process.returncode if process is not None else None
    success = code == 0 and not (timed_out or cancelled or transport_error)
    if truncated and not privileged:
        success = False
    return ExecutionResult(success, output, code, timed_out, cancelled, truncated)

def execute_proposal(proposal: CommandProposal, timeout_sec: float = 30, *,
                     approved_digest: Optional[str] = None, max_output_bytes: int = 65536,
                     cancel_event=None, interactive: bool = False) -> ExecutionResult:
    """Revalidate and execute only the exact proposal explicitly approved by caller.

    CLI consent supplies approved_digest. API users must obtain consent before
    passing approval_digest(proposal); a digest is a binding, not authentication.
    Read-only timeouts/cancellation terminate the child group. Privileged
    operations require a real interactive terminal and a non-root user; they
    await orderly exit after interruption and never impose an apt kill deadline.
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
    if type(interactive) is not bool:
        return ExecutionResult(False, "Interactive must be a boolean.")
    if proposal.requires_sudo:
        if not interactive or not sys.stdin.isatty() or not sys.stdout.isatty():
            return ExecutionResult(False, "Package changes require an interactive terminal and password entry.")
        if os.geteuid() == 0:
            return ExecutionResult(False, "Run the assistant as a regular user; root cannot bypass password entry.")
    if interactive:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            return ExecutionResult(False, "Interactive execution requires a real terminal.")
        return _run_interactive_pty(
            proposal.argv, proposal.cwd, timeout_sec, max_output_bytes,
            cancel_event, privileged=proposal.requires_sudo,
        )
    return _run_pty(proposal.argv, proposal.cwd, timeout_sec, max_output_bytes, cancel_event)
