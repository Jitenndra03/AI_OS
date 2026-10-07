"""Authored scripts remain data throughout generation, parsing and approved saving."""
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest

from nli.script_authoring import (MAX_SCRIPT_BYTES, SHEBANG, ScriptDraft,
                                  generate_script, save_script, validate_syntax)


def install_model(monkeypatch, response):
    monkeypatch.setenv("GEMINI_API_KEY", "secret-do-not-leak")
    monkeypatch.setenv("GEMINI_MODEL", "test-model")
    class Client:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        @property
        def models(self):
            def generate_content(**kwargs):
                if isinstance(response, Exception):
                    raise response
                return SimpleNamespace(text=response)
            return SimpleNamespace(generate_content=generate_content)
    fake = SimpleNamespace(Client=Client, types=SimpleNamespace(GenerateContentConfig=lambda **kw: kw))
    monkeypatch.setitem(sys.modules, "google", SimpleNamespace(genai=fake))
    monkeypatch.setitem(sys.modules, "google.genai", fake)


@pytest.mark.parametrize("intent", ["storage report", "write a bash script to check storage",
                                     "system report", "write me a shell script to check storage",
                                     "system information report", "backup directory", "find files named '*.txt'"])
def test_offline_templates_valid_immutable_and_need_no_key(monkeypatch, intent):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    draft = generate_script(intent)
    assert draft.code.startswith(SHEBANG)
    assert draft.source == "offline-template"
    assert draft.explanation
    validate_syntax(draft.code)
    with pytest.raises(FrozenInstanceError):
        draft.code = "echo altered"


def test_backup_paths_are_shell_quoted_literal_data():
    draft = generate_script("backup directory '/tmp/my $(touch nope) files' to '/tmp/report;id.tar.gz'")
    assert "source_dir='/tmp/my $(touch nope) files'" in draft.code
    assert "archive_path='/tmp/report;id.tar.gz'" in draft.code
    assert "set -o noclobber" in draft.code
    assert 'tar -C "$source_dir"' in draft.code


def test_find_pattern_is_quoted_literal_data():
    draft = generate_script("find files named '$(touch nope);*.txt'")
    assert "pattern='$(touch nope);*.txt'" in draft.code
    assert '-name "$pattern"' in draft.code


@pytest.mark.parametrize("intent", ["", None, "x" * 4001, "bad\0request", "install anything",
                                     "find files named", "backup directory /tmp/a /tmp/b",
                                     "find files named 'unterminated"])
def test_invalid_or_unsupported_offline_requests_fail_closed(intent):
    with pytest.raises(ValueError):
        generate_script(intent)


def test_no_implicit_online_for_unknown_request(monkeypatch):
    install_model(monkeypatch, AssertionError("online must not be called"))
    with pytest.raises(ValueError, match="Offline script templates"):
        generate_script("count prime numbers")


def test_online_configuration_required(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        generate_script("count prime numbers", offline=False)


def test_online_code_is_not_executed_during_generation_or_saving(monkeypatch, tmp_path):
    marker = tmp_path / "must-not-exist"
    code = f"touch '{marker}'\necho $(touch '{marker}')\n"
    install_model(monkeypatch, json.dumps({"code": code, "explanation": "Writes a file if executed."}))
    draft = generate_script("make my example", offline=False)
    saved = save_script(draft, "example.sh", tmp_path)
    assert not marker.exists()
    assert saved.read_text() == draft.code
    assert draft.source == "online-model"


@pytest.mark.parametrize("response", ["not json", "[]", '{"code":"echo x"}',
    '{"code":"echo x","explanation":"x","execute":true}',
    '{"code":42,"explanation":"x"}', '{"code":"echo x","explanation":false}',
    '{"code":"if then","explanation":"bad syntax"}',
    json.dumps({"code": "x" * MAX_SCRIPT_BYTES, "explanation": "oversize"}),
    "x" * (128 * 1024 + 1), RuntimeError("secret-do-not-leak")])
def test_malformed_online_responses_rejected_without_secret_leak(monkeypatch, response):
    install_model(monkeypatch, response)
    with pytest.raises(ValueError) as error:
        generate_script("count prime numbers", offline=False)
    assert "secret-do-not-leak" not in str(error.value)


def test_syntax_parser_never_loads_bash_environment_hooks(monkeypatch, tmp_path):
    marker = tmp_path / "hook-ran"
    hook = tmp_path / "hook.sh"
    hook.write_text(f"touch '{marker}'\n")
    monkeypatch.setenv("BASH_ENV", str(hook))
    monkeypatch.setenv("ENV", str(hook))
    validate_syntax(f"echo $(touch '{marker}')\n")
    assert not marker.exists()


@pytest.mark.parametrize("code", ["if then", "", "\0", "echo " + "x" * MAX_SCRIPT_BYTES, "\ud800"])
def test_invalid_script_syntax_or_size_rejected(code):
    with pytest.raises(ValueError):
        validate_syntax(code)


def test_save_is_private_exact_and_exclusive(tmp_path):
    draft = generate_script("storage report")
    path = save_script(draft, "report.sh", tmp_path)
    assert path == tmp_path / "report.sh"
    assert path.read_bytes() == draft.code.encode()
    assert stat.S_IMODE(path.stat().st_mode) == 0o700
    with pytest.raises(ValueError):
        save_script(draft, "report.sh", tmp_path)
    assert path.read_bytes() == draft.code.encode()


def test_absolute_destination_must_stay_inside_workspace(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    draft = generate_script("storage report")
    with pytest.raises(ValueError, match="inside"):
        save_script(draft, tmp_path / "escape.sh", workspace)
    with pytest.raises(ValueError, match="cannot contain"):
        save_script(draft, "../escape.sh", workspace)
    assert not (tmp_path / "escape.sh").exists()
    assert save_script(draft, workspace / "okay.sh", workspace).exists()


def test_symlinked_destinations_and_parent_directories_blocked(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    existing = external / "keep.sh"
    existing.write_text("original")
    (workspace / "linked.sh").symlink_to(existing)
    (workspace / "linked-dir").symlink_to(external, target_is_directory=True)
    draft = generate_script("storage report")
    for path in ["linked.sh", "linked-dir/created.sh"]:
        with pytest.raises(ValueError):
            save_script(draft, path, workspace)
    assert existing.read_text() == "original"
    assert not (external / "created.sh").exists()


def test_symlinked_workspace_and_missing_parents_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    draft = generate_script("storage report")
    with pytest.raises(ValueError):
        save_script(draft, "new.sh", link)
    with pytest.raises(ValueError):
        save_script(draft, "missing/new.sh", real)
    assert list(real.iterdir()) == []


def test_forged_draft_revalidated_before_save(tmp_path):
    with pytest.raises(ValueError):
        save_script(ScriptDraft(SHEBANG + "if then", "forged", "online-model"), "bad.sh", tmp_path)
    assert not (tmp_path / "bad.sh").exists()


def test_repeated_saves_close_descriptors(tmp_path):
    proc = Path("/proc/self/fd")
    before = len(list(proc.iterdir()))
    draft = generate_script("storage report")
    for index in range(5):
        save_script(draft, f"report{index}.sh", tmp_path)
    assert len(list(proc.iterdir())) == before


@pytest.mark.parametrize("control", ["\x1b[2J", "\r", "\x08", "\x7f", "\x85", "\u202e"])
def test_terminal_controls_never_reach_preview(monkeypatch, control):
    for field in ["code", "explanation"]:
        data = {"code": "echo hello\n", "explanation": "Print greeting."}
        data[field] += control
        install_model(monkeypatch, json.dumps(data))
        with pytest.raises(ValueError):
            generate_script("print a greeting", offline=False)


@pytest.mark.parametrize("draft", [ScriptDraft(None, "explanation", "online-model"),
    ScriptDraft(SHEBANG + "echo ok", "\x1b[2J", "online-model"),
    ScriptDraft(SHEBANG + "echo ok", "fine", "untrusted-source"),
    ScriptDraft(SHEBANG + "echo ok", None, "online-model")])
def test_save_rejects_forged_dataclass_fields(tmp_path, draft):
    with pytest.raises(ValueError):
        save_script(draft, "forged.sh", tmp_path)
    assert not (tmp_path / "forged.sh").exists()
