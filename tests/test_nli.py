"""Offline NLI security boundary and real PTY lifecycle regressions."""
from dataclasses import FrozenInstanceError, replace
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import psutil
import pytest

from nli.command_executor import _run_pty, execute_proposal
from nli.models import CommandProposal
from nli.nli_parser import parse_user_input
from nli.safety_validator import OPERATIONS, approval_digest, make_proposal, validate_proposal

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("phrase,action", [
    ("show memory usage", "check_memory_usage"),
    ("show CPU usage", "check_cpu_usage"),
    ("show disk usage", "check_disk_usage"),
    ("show top processes", "list_top_processes"),
    ("system summary", "get_system_summary"),
    ("current directory", "current_directory"),
    ("list files", "list_files"),
    ("find files", "find_files"),
])
def test_offline_intents_need_no_api_key(monkeypatch, tmp_path, phrase, action):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    proposal = parse_user_input(phrase, cwd=tmp_path)
    assert proposal.action == action
    assert validate_proposal(proposal)[0]


@pytest.mark.parametrize("phrase", [
    "show memory usage; rm -rf /", "$(touch hacked)", "list files | sh",
    "show memory usage > stolen", "list files && sudo reboot", "`id`",
    "sudo show memory usage", "delete files", "restart bluetooth",
    "renice chrome", "show memory usage and delete all files", "eval anything",
])
def test_unknown_compound_and_shell_requests_do_not_execute(phrase):
    proposal = parse_user_input(phrase)
    assert proposal.action == "unknown"
    assert not execute_proposal(proposal).success


@pytest.mark.parametrize("field,value", [
    ("requires_sudo", "false"), ("confirm_required", 1),
    ("requires_sudo", None), ("action", []), ("target", 42),
    ("argv", "free -h"), ("risk_level", "safe"), ("cwd", False),
])
def test_schema_rejects_coercion(field, value):
    data = make_proposal("check_memory_usage").to_dict()
    data[field] = value
    with pytest.raises(ValueError):
        CommandProposal.from_dict(data)


def test_proposal_is_immutable_and_roundtrips():
    proposal = make_proposal("current_directory")
    with pytest.raises(FrozenInstanceError):
        proposal.script = "rm -rf /"
    assert CommandProposal.from_dict(json.loads(json.dumps(proposal.to_dict()))) == proposal


@pytest.mark.parametrize("change", [
    {"script": "echo harmless; touch /tmp/unapproved"},
    {"script": "$(id)"}, {"argv": ("/bin/sh", "-c", "id")},
    {"risk_level": "high"}, {"requires_sudo": True}, {"requires_sudo": 0},
    {"confirm_required": False}, {"action": "execute_command"},
    {"action": "renice_process"}, {"target": "root"},
    {"explanation": "Harmless low-risk operation"},
    {"cwd": "/does-not-exist"},
])
def test_forged_proposals_blocked_even_directly(monkeypatch, change):
    import nli.command_executor as executor
    original = make_proposal("current_directory")
    forged = replace(original, **change)
    monkeypatch.setattr(executor, "_run_pty", lambda *a: pytest.fail("must not spawn"))
    assert not executor.execute_proposal(forged, approved_digest=approval_digest(original)).success


def test_approval_binds_exact_proposal_and_cwd(tmp_path):
    proposal = make_proposal("current_directory")
    assert not execute_proposal(proposal).success
    assert not execute_proposal(proposal, approved_digest="é").success
    changed = make_proposal("current_directory", tmp_path)
    assert not execute_proposal(changed, approved_digest=approval_digest(proposal)).success
    result = execute_proposal(changed, approved_digest=approval_digest(changed))
    assert result.success and result.exit_code == 0
    assert str(tmp_path) in result.output


@pytest.mark.parametrize("action", OPERATIONS)
def test_all_operations_run_in_real_pty(action, tmp_path):
    (tmp_path / "hello.txt").write_text("hello")
    proposal = make_proposal(action, tmp_path)
    result = execute_proposal(proposal, approved_digest=approval_digest(proposal))
    assert result.success, result
    assert result.exit_code == 0
    assert result.output


def test_transport_has_terminal_and_own_session(tmp_path):
    code = "import os; print(os.isatty(0), os.isatty(1), os.getsid(0) == os.getpid())"
    result = _run_pty([sys.executable, "-c", code], str(tmp_path), 3, 4096)
    assert result.success
    assert result.output.strip() == "True True True"


def test_nonzero_exit_status_is_preserved(tmp_path):
    result = _run_pty([sys.executable, "-c", "raise SystemExit(7)"], str(tmp_path), 3, 4096)
    assert not result.success
    assert result.exit_code == 7


def assert_not_running(pid):
    for _ in range(50):
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return  # Orphan zombies await the host's PID 1 reaper, never execute.
        except psutil.NoSuchProcess:
            return
        time.sleep(0.01)
    pytest.fail(f"Descendant {pid} survived process-group cleanup")


@pytest.mark.parametrize("mode", ["timeout", "cancel", "leader_exit"])
def test_entire_process_group_is_terminated(tmp_path, mode):
    child_code = "import time; time.sleep(60)"
    code = ("import subprocess, sys, time; "
            f"p=subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
            "print(p.pid, flush=True); " + ("time.sleep(60)" if mode != "leader_exit" else ""))
    event = threading.Event()
    timer = threading.Timer(0.5, event.set)
    if mode == "cancel":
        timer.start()
    try:
        result = _run_pty([sys.executable, "-c", code], str(tmp_path),
                          0.5 if mode == "timeout" else 3, 4096, event)
    finally:
        timer.cancel()
    assert result.timed_out == (mode == "timeout")
    assert result.cancelled == (mode == "cancel")
    assert_not_running(int(result.output.strip()))


def test_output_cap_terminates_child(tmp_path):
    code = "import os, time; print(os.getpid(), flush=True); print('x'*100000, flush=True); time.sleep(60)"
    result = _run_pty([sys.executable, "-c", code], str(tmp_path), 3, 100)
    assert result.truncated and not result.success
    assert len(result.output.encode()) <= 100
    assert_not_running(int(result.output.splitlines()[0]))


def test_file_search_does_not_follow_symlink(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "secret.txt").write_text("secret")
    (workspace / "link").symlink_to(external, target_is_directory=True)
    (workspace / "file-link").symlink_to(external / "secret.txt")
    (workspace / "visible.txt").write_text("visible")
    proposal = make_proposal("find_files", workspace)
    result = execute_proposal(proposal, approved_digest=approval_digest(proposal))
    assert result.success
    assert "visible.txt" in result.output
    assert "secret" not in result.output and "file-link" not in result.output


def test_cli_stdin_approval_defaults_to_no(tmp_path):
    command = [sys.executable, "-m", "nli.cli_interface", "--offline", "--workspace",
               str(tmp_path), "-c", "current directory"]
    denied = subprocess.run(command, input="\n", capture_output=True, text=True, cwd=ROOT, timeout=5)
    assert denied.returncode == 1 and "no command ran" in denied.stdout
    approved = subprocess.run(command, input="yes\n", capture_output=True, text=True, cwd=ROOT, timeout=5)
    assert approved.returncode == 0 and "Exit status: 0" in approved.stdout


def test_cli_explicit_noninteractive_authorization(tmp_path):
    result = subprocess.run([sys.executable, "-m", "nli.cli_interface", "--offline", "--yes",
                             "--workspace", str(tmp_path), "-c", "list files"],
                            capture_output=True, text=True, cwd=ROOT, timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr


def test_online_is_lazy_and_configuration_required(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = parse_user_input("give me a machine overview", offline=False)
    assert result.action == "unknown"
    assert "environment variables" in result.explanation


def test_online_response_cannot_supply_script(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-no-network")
    monkeypatch.setenv("GEMINI_MODEL", "test-model")
    class Client:
        def __init__(self, **kwargs):
            self.models = SimpleNamespace(generate_content=lambda **kwargs:
                SimpleNamespace(text='{"action":"get_system_summary","script":"touch hacked"}'))
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
    fake = SimpleNamespace(Client=Client, types=SimpleNamespace(GenerateContentConfig=lambda **kw: kw))
    monkeypatch.setitem(sys.modules, "google", SimpleNamespace(genai=fake))
    monkeypatch.setitem(sys.modules, "google.genai", fake)
    result = parse_user_input("give me a machine overview", offline=False)
    assert result.action == "unknown"


@pytest.mark.parametrize("limits", [{"timeout_sec": float("nan")}, {"timeout_sec": -1},
                                    {"timeout_sec": True}, {"max_output_bytes": 0},
                                    {"max_output_bytes": True}])
def test_invalid_execution_limits_rejected(limits):
    proposal = make_proposal("current_directory")
    assert not execute_proposal(proposal, approved_digest=approval_digest(proposal), **limits).success


def test_repeated_pty_operations_close_file_descriptors(tmp_path):
    fd_directory = Path('/proc/self/fd')
    if not fd_directory.exists():
        pytest.skip('Linux fd accounting required')
    before = len(list(fd_directory.iterdir()))
    proposal = make_proposal('current_directory', tmp_path)
    for _ in range(5):
        assert execute_proposal(proposal, approved_digest=approval_digest(proposal)).success
    assert len(list(fd_directory.iterdir())) == before
