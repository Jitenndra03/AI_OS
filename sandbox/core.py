"""Small VM demonstration sandbox; not a malware verdict or a kernel boundary audit."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
MAX_TIMEOUT = 30.0
DISCLAIMER = "Static indicators and observed isolation are not a malware verdict or proof of safety."


class SandboxError(ValueError):
    """An artifact or isolation request could not be handled safely."""


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _snapshot(source):
    """Read one regular file without following a final symlink; detect replacement/mutation."""
    path = os.path.abspath(os.fspath(source))
    try:
        before = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise SandboxError("Source must be a regular file, not a symlink or special file.")
        if before.st_size > MAX_ARTIFACT_BYTES:
            raise SandboxError("Artifact exceeds the 10 MiB inspection limit.")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or _signature(opened) != _signature(before):
                raise SandboxError("Source changed while opening it.")
            data = stream.read(MAX_ARTIFACT_BYTES + 1)
            after = os.fstat(stream.fileno())
        current = os.stat(path, follow_symlinks=False)
        if len(data) > MAX_ARTIFACT_BYTES:
            raise SandboxError("Artifact exceeds the 10 MiB inspection limit.")
        if _signature(before) != _signature(after) or _signature(after) != _signature(current):
            raise SandboxError("Source changed while reading it; retry with a stable copy.")
        return path, data
    except OSError as exc:
        raise SandboxError(f"Cannot read artifact: {exc.strerror}") from exc


def _kind(path, data):
    first = data.split(b"\n", 1)[0].lower()
    if data.startswith(b"\x7fELF"):
        return "elf"
    if b"\0" in data:
        return "binary"
    if Path(path).suffix.lower() == ".py" or (first.startswith(b"#!") and b"python" in first):
        return "python"
    if Path(path).suffix.lower() == ".sh" or (first.startswith(b"#!") and re.search(rb"(?:/|\s)(?:ba|da)?sh(?:\s|$)", first)):
        return "shell"
    return "text"


def _inspection(path, data):
    text = data.decode("utf-8", errors="replace")
    rules = [
        ("network_api", r"\b(?:socket|requests|urllib|curl|wget|nc)\b", "Network-related API or command"),
        ("process_launch", r"\b(?:subprocess|execve|system|Popen|eval|exec)\b", "Process launch or dynamic execution"),
        ("sensitive_path", r"(?:/etc/(?:shadow|passwd)|\.ssh|\.aws|\.env\b)", "Potentially sensitive host path"),
        ("destructive_command", r"\b(?:rm\s+-[rf]+|mkfs|shutdown|reboot)\b", "Potentially destructive command"),
        ("encoded_payload", r"\b(?:base64|b64decode|fromhex)\b", "Encoded-data handling"),
    ]
    findings = []
    for identifier, pattern, description in rules:
        count, lines = 0, set()
        for match in re.finditer(pattern, text, re.IGNORECASE):
            count += 1
            if count <= 20:
                lines.add(text.count("\n", 0, match.start()) + 1)
        if count:
            findings.append({"id": identifier, "description": description, "count": count,
                             "lines": sorted(lines)})
    return {"schema_version": 1, "action": "inspect", "status": "inspected", "source": path,
            "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), "file_type": _kind(path, data),
            "indicators": findings, "executed": False, "notice": DISCLAIMER}


def inspect_artifact(source):
    return _inspection(*_snapshot(source))


def quarantine_artifact(source, directory):
    path, data = _snapshot(source)
    report = _inspection(path, data)
    target = Path(directory).absolute()
    try:
        target.mkdir(mode=0o700, parents=False, exist_ok=True)
        directory_fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(directory_fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise SandboxError("Quarantine directory must be owned by you with mode 0700.")
            name = report["sha256"] + ".artifact"
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=directory_fd)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                os.unlink(name, dir_fd=directory_fd)
                raise
        finally:
            os.close(directory_fd)
    except FileExistsError as exc:
        raise SandboxError("That digest is already quarantined; existing artifacts are never overwritten.") from exc
    except OSError as exc:
        raise SandboxError(f"Cannot quarantine artifact: {exc.strerror}") from exc
    report.update(action="quarantine", status="quarantined", quarantine_path=str(target / name),
                  original_preserved=True)
    return report


def _runtime_command(bwrap, artifact=None):
    command = [bwrap, "--die-with-parent", "--unshare-all", "--unshare-user",
               "--disable-userns", "--assert-userns-disabled", "--cap-drop", "ALL", "--clearenv"]
    for location in ("/usr", "/bin", "/sbin", "/lib", "/lib64"):
        if os.path.islink(location):
            command.extend(["--symlink", os.readlink(location), location])
        elif os.path.isdir(location):
            command.extend(["--ro-bind", location, location])
    command.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/home",
                    "--dir", "/home/sandbox", "--dir", "/work", "--dir", "/etc",
                    "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/home/sandbox",
                    "--setenv", "LANG", "C.UTF-8", "--setenv", "TMPDIR", "/tmp",
                    "--chdir", "/work"])
    if artifact is not None:
        command.extend(["--ro-bind", str(artifact), "/work/artifact"])
    return command


def _uid_task_count():
    # RLIMIT_NPROC includes all host threads for the real UID, even across namespaces.
    count = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = dict(line.split(":", 1) for line in (entry / "status").read_text().splitlines() if ":" in line)
            if int(fields["Uid"].split()[0]) == os.getuid():
                count += int(fields["Threads"])
        except (OSError, KeyError, ValueError):
            continue
    return count


def _limits(timeout, process_limit):
    def apply():
        os.umask(0o077)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (math.ceil(timeout) + 1, math.ceil(timeout) + 1))
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        resource.setrlimit(resource.RLIMIT_NPROC, (process_limit, process_limit))
    return apply


def _execute(command, timeout):
    """Bound pipe memory and kill the entire original process group on timeout."""
    started = time.monotonic()
    process_limit = max(64, _uid_task_count() + 64)
    existing_hard_limit = resource.getrlimit(resource.RLIMIT_NPROC)[1]
    if existing_hard_limit != resource.RLIM_INFINITY:
        process_limit = min(process_limit, existing_hard_limit)
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               cwd="/", env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                               close_fds=True, start_new_session=True, preexec_fn=_limits(timeout, process_limit))
    captures = {"stdout": bytearray(), "stderr": bytearray()}
    totals = {"stdout": 0, "stderr": 0}
    timed_out = False
    selector = selectors.DefaultSelector()
    for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)
    try:
        while selector.get_map():
            if time.monotonic() - started >= timeout:
                timed_out = True
                break
            for key, _ in selector.select(timeout=min(0.1, max(0, timeout - (time.monotonic() - started)))):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                name = key.data
                totals[name] += len(chunk)
                captures[name].extend(chunk[:max(0, MAX_OUTPUT_BYTES - len(captures[name]))])
        remaining = max(0, timeout - (time.monotonic() - started))
        if not timed_out:
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                timed_out = True
    finally:
        # Even a normally exiting launcher must not leave its descendants running.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        selector.close()
        process.stdout.close()
        process.stderr.close()
    return {"exit_code": process.returncode, "timed_out": timed_out,
            "process_limit_shared_host_uid": process_limit,
            "duration_seconds": round(time.monotonic() - started, 3),
            **{name: captures[name].decode("utf-8", errors="replace") for name in captures},
            "output_truncated": {name: totals[name] > MAX_OUTPUT_BYTES for name in captures}}


_PROBE = r'''
import json, os, socket, sys
checks = {}
for key, path in [("host_file_hidden", sys.argv[1]), ("host_etc_hidden", "/etc/passwd")]:
    try:
        with open(path, "rb") as stream: stream.read(1)
        checks[key] = False
    except FileNotFoundError:
        checks[key] = True
try:
    with open("/usr/.ai_os_probe", "wb") as stream: stream.write(b"probe")
    checks["runtime_readonly"] = False
except OSError:
    checks["runtime_readonly"] = True
checks["environment_cleared"] = "AI_OS_PROBE_SECRET" not in os.environ
checks["isolated_workdir"] = os.getcwd() == "/work"
checks["network_namespace_changed"] = os.readlink("/proc/self/ns/net") != sys.argv[2]
checks["pid_namespace_changed"] = os.readlink("/proc/self/ns/pid") != sys.argv[3]
checks["mount_namespace_changed"] = os.readlink("/proc/self/ns/mnt") != sys.argv[4]
checks["user_namespace_changed"] = os.readlink("/proc/self/ns/user") != sys.argv[5]
print(json.dumps(checks))
'''


def doctor():
    report = {"schema_version": 1, "action": "doctor", "available": False,
              "status": "unavailable", "notice": DISCLAIMER}
    if sys.platform != "linux":
        report.update(reason="Linux with bubblewrap and user namespaces is required.")
        return report
    bwrap = shutil.which("bwrap", path="/usr/bin:/bin")
    if not bwrap or not Path("/usr/bin/python3").is_file():
        report.update(reason="Install bubblewrap and the system python3 through your distribution package manager.")
        return report
    report["bubblewrap"] = bwrap
    try:
        with tempfile.TemporaryDirectory(prefix="ai-os-isolation-probe-") as directory:
            sentinel = Path(directory) / "host-only"
            sentinel.write_text("isolation probe", encoding="utf-8")
            namespaces = [os.readlink(f"/proc/self/ns/{name}") for name in ("net", "pid", "mnt", "user")]
            command = _runtime_command(bwrap) + ["--", "/usr/bin/python3", "-I", "-S", "-c", _PROBE,
                                               str(sentinel), *namespaces]
            result = _execute(command, 5.0)
        report["probe"] = result
        if result["exit_code"] != 0 or result["timed_out"]:
            namespace_error = re.search(r"(?:operation not permitted|permission denied|no permissions|creating new namespace|user namespaces.*(?:disabled|not enabled))", result["stderr"], re.I)
            report.update(status="namespace_unavailable" if namespace_error else "probe_failed",
                          reason="Isolation probe failed; execution is disabled.",
                          guidance="Use a Linux VM whose distribution permits unprivileged bubblewrap user namespaces. Ask its administrator to review namespace/AppArmor policy; this tool never changes host policy.")
            return report
        checks = json.loads(result["stdout"])
        if not isinstance(checks, dict) or not checks or not all(value is True for value in checks.values()):
            report.update(status="probe_failed", reason="Isolation checks did not all pass.", checks=checks)
            return report
        report.update(status="available", available=True, checks=checks)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report.update(status="probe_failed", reason=f"Cannot verify isolation: {exc}")
    return report


def run_artifact(source, timeout=5.0):
    if not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT:
        raise SandboxError("Timeout must be greater than 0 and at most 30 seconds.")
    path, data = _snapshot(source)
    report = _inspection(path, data)
    report.update(action="run", status="blocked", timeout_seconds=timeout)
    kind = report["file_type"]
    if kind not in ("python", "shell"):
        report["reason"] = "Execution supports Python (.py) and POSIX shell (.sh) scripts only."
        return report
    capability = doctor()
    report["isolation"] = capability
    if not capability["available"]:
        report["reason"] = "Isolation is unavailable or unverified. Artifact was not executed."
        return report
    # Bind the inspected immutable snapshot, never reopen the caller's original file.
    with tempfile.TemporaryDirectory(prefix="ai-os-artifact-") as directory:
        staged = Path(directory) / "artifact"
        staged.write_bytes(data)
        staged.chmod(0o400)
        interpreter = ["/usr/bin/python3", "-I", "-S"] if kind == "python" else ["/bin/sh"]
        command = _runtime_command(capability["bubblewrap"], staged) + ["--", *interpreter, "/work/artifact"]
        try:
            result = _execute(command, float(timeout))
        except (OSError, subprocess.SubprocessError) as exc:
            report["reason"] = f"Isolated launcher could not start: {exc}"
            return report
    report.update(status="timed_out" if result["timed_out"] else ("completed" if result["exit_code"] == 0 else "failed"),
                  executed=True, execution=result,
                  limits={"address_space_mib": 512, "file_size_mib": 8, "open_files": 64,
                          "process_limit_shared_host_uid": result["process_limit_shared_host_uid"], "output_bytes_per_stream": MAX_OUTPUT_BYTES},
                  artifacts_exported=False)
    return report
