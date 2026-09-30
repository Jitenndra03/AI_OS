"""Boundary tests use owned fixtures; real execution only follows a passing isolation probe."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

import pytest

from sandbox import core
from sandbox.cli import main


@pytest.fixture
def artifact(tmp_path):
    path = tmp_path / "example.py"
    path.write_text("import socket\nprint('hello')\n", encoding="utf-8")
    return path


def test_static_inspection_never_executes(artifact, monkeypatch):
    monkeypatch.setattr(core, "_execute", lambda *_: pytest.fail("inspection launched a process"))
    report = core.inspect_artifact(artifact)
    assert report["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert report["file_type"] == "python"
    assert report["executed"] is False
    assert report["indicators"][0]["id"] == "network_api"


def test_rejects_symlinks_and_special_files(artifact, tmp_path):
    link = tmp_path / "link.py"
    link.symlink_to(artifact)
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    for path in (link, fifo, tmp_path):
        with pytest.raises(core.SandboxError, match="regular file"):
            core.inspect_artifact(path)


def test_rejects_oversized_file(tmp_path):
    path = tmp_path / "big.py"
    with path.open("wb") as stream:
        stream.truncate(core.MAX_ARTIFACT_BYTES + 1)
    with pytest.raises(core.SandboxError, match="10 MiB"):
        core.inspect_artifact(path)


def test_source_replacement_detected(artifact, monkeypatch):
    original_open = core.os.open
    def replace_before_open(path, flags, *args, **kwargs):
        if str(path) == str(artifact):
            replacement = artifact.with_suffix(".new")
            replacement.write_text("changed")
            replacement.replace(artifact)
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(core.os, "open", replace_before_open)
    with pytest.raises(core.SandboxError, match="changed"):
        core.inspect_artifact(artifact)


def test_quarantine_private_inert_copy(artifact, tmp_path):
    directory = tmp_path / "quarantine"
    report = core.quarantine_artifact(artifact, directory)
    stored = Path(report["quarantine_path"])
    assert artifact.exists() and stored.read_bytes() == artifact.read_bytes()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(stored.stat().st_mode) == 0o400
    with pytest.raises(core.SandboxError, match="already quarantined"):
        core.quarantine_artifact(artifact, directory)


def test_quarantine_rejects_shared_or_symlink_directory(artifact, tmp_path):
    directory = tmp_path / "shared"
    directory.mkdir(mode=0o755)
    with pytest.raises(core.SandboxError, match="0700"):
        core.quarantine_artifact(artifact, directory)
    link = tmp_path / "link"
    link.symlink_to(directory)
    with pytest.raises(core.SandboxError):
        core.quarantine_artifact(artifact, link)


@pytest.mark.parametrize("timeout", [0, -1, 31, float("nan"), float("inf")])
def test_invalid_timeout(artifact, timeout):
    with pytest.raises(core.SandboxError, match="Timeout"):
        core.run_artifact(artifact, timeout)


def test_unavailable_isolation_never_launches_artifact(artifact, monkeypatch):
    monkeypatch.setattr(core, "doctor", lambda: {"available": False, "status": "namespace_unavailable"})
    monkeypatch.setattr(core, "_execute", lambda *_: pytest.fail("artifact launched without isolation"))
    report = core.run_artifact(artifact)
    assert report["status"] == "blocked" and report["executed"] is False


def test_unsupported_binary_never_launches(tmp_path, monkeypatch):
    path = tmp_path / "executable"
    path.write_bytes(b"\x7fELF\x00")
    monkeypatch.setattr(core, "doctor", lambda: pytest.fail("unsupported artifact should not probe"))
    assert core.run_artifact(path)["status"] == "blocked"


def test_run_uses_inspected_snapshot(artifact, monkeypatch):
    original = artifact.read_bytes()
    def capability():
        artifact.write_text("different source bytes")
        return {"available": True, "bubblewrap": "/usr/bin/bwrap"}
    def execute(command, timeout):
        index = command.index("/work/artifact")
        staged = Path(command[index - 1])
        assert staged != artifact and staged.read_bytes() == original
        assert stat.S_IMODE(staged.stat().st_mode) == 0o400
        assert command[index - 2] == "--ro-bind"
        assert "--unshare-all" in command and "--clearenv" in command
        assert str(Path.home()) not in command
        return {"exit_code": 0, "timed_out": False, "process_limit_shared_host_uid": 123, "stdout": "ok", "stderr": ""}
    monkeypatch.setattr(core, "doctor", capability)
    monkeypatch.setattr(core, "_execute", execute)
    report = core.run_artifact(artifact)
    assert report["status"] == "completed"
    assert report["sha256"] == hashlib.sha256(original).hexdigest()


@pytest.mark.parametrize("message, status", [
    ("bwrap: Creating new namespace failed: Operation not permitted", "namespace_unavailable"),
    ("bwrap: executable is missing", "probe_failed"),
])
def test_doctor_failure_classification(monkeypatch, message, status):
    monkeypatch.setattr(core.shutil, "which", lambda *args, **kwargs: "/usr/bin/bwrap")
    monkeypatch.setattr(core, "_execute", lambda *args: {"exit_code": 1, "timed_out": False, "stdout": "", "stderr": message})
    report = core.doctor()
    assert report["available"] is False and report["status"] == status


def test_bounded_capture_and_timeout():
    # Trusted test code, never artifact input. This exercises process/pipe management only.
    result = core._execute([sys.executable, "-I", "-S", "-c", "import os; os.write(1, b'x' * 200000)"], 3)
    assert len(result["stdout"]) == core.MAX_OUTPUT_BYTES
    assert result["output_truncated"]["stdout"] is True
    timeout = core._execute([sys.executable, "-I", "-S", "-c", "import time; time.sleep(10)"], 0.1)
    assert timeout["timed_out"] is True
    assert timeout["duration_seconds"] < 2


def test_cli_report_and_existing_output(artifact, tmp_path, capsys):
    output = tmp_path / "report.json"
    assert main(["inspect", str(artifact), "--output", str(output)]) == 0
    assert json.loads(output.read_text())["executed"] is False
    saved = output.read_bytes()
    assert main(["inspect", str(artifact), "--output", str(output)]) == 2
    assert output.read_bytes() == saved
    capsys.readouterr()


def test_cli_invalid_input_produces_error_report(tmp_path, capsys):
    output = tmp_path / "error.json"
    assert main(["inspect", str(tmp_path / "missing"), "--output", str(output)]) == 2
    assert json.loads(output.read_text())["status"] == "error"
    capsys.readouterr()


def test_real_benign_isolation():
    capability = core.doctor()
    if not capability["available"]:
        pytest.skip(f"Real bubblewrap isolation unavailable: {capability.get('status')}: {capability.get('reason')}")
    sample = Path(__file__).parents[1] / "examples/sandbox/isolation_demo.py"
    report = core.run_artifact(sample, timeout=5)
    assert report["status"] == "completed", report
    observed = json.loads(report["execution"]["stdout"])
    assert observed["cwd"] == "/work"
    assert observed["protected_write"].startswith("denied")
    assert observed["network_connection"].startswith("unavailable")
    assert observed["host_account_file"].startswith("denied or absent")
