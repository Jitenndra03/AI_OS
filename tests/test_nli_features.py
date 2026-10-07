"""User-facing package previews, bounded file searches and script-saving routes."""
from dataclasses import replace
from pathlib import Path
import subprocess
import sys

import pytest

from nli.cli_interface import AIOSCli
from nli.nli_parser import parse_user_input
from nli.safety_validator import approval_digest, validate_proposal
from nli.command_executor import execute_proposal

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('text,action,target', [
    ('install package vlc', 'install_packages', 'vlc'),
    ('please install apps git and curl', 'install_packages', 'git curl'),
    ('sudo apt install vim', 'install_packages', 'vim'),
    ('uninstall package vlc', 'remove_packages', 'vlc'),
    ('remove apps git, curl', 'remove_packages', 'git curl'),
    ('sudo apt update', 'update_packages', None),
    ('update package indexes', 'update_packages', None),
    ('checking storage', 'check_disk_usage', None),
    ('check storage', 'check_disk_usage', None),
    ('find files named "*.py"', 'find_files', '*.py'),
    ('find report.pdf', 'find_files', 'report.pdf'),
    ('locate "My report.pdf"', 'find_files', 'My report.pdf'),
])
def test_user_requests(text, action, target, tmp_path):
    proposal = parse_user_input(text, cwd=tmp_path)
    assert proposal.action == action
    assert proposal.target == target
    assert validate_proposal(proposal)[0]
    assert proposal.requires_sudo == action.endswith('_packages')


@pytest.mark.parametrize('text', ['install --allow-unauthenticated', 'install ./evil.deb',
    'install vim; reboot', 'install $(id)', 'install vim\nreboot', 'remove -y',
    'find files named ../../etc/passwd', 'install https://example.com/x.deb'])
def test_no_shell_or_package_options(text, tmp_path):
    assert parse_user_input(text, cwd=tmp_path).action == 'unknown'


def test_search_pattern_really_filters_and_stays_in_workspace(tmp_path):
    (tmp_path / 'first.py').touch()
    (tmp_path / 'omit.txt').touch()
    (tmp_path / 'nested').mkdir()
    (tmp_path / 'nested/second.py').touch()
    (tmp_path / 'outside.py').symlink_to('/etc/passwd')
    proposal = parse_user_input('find files named "*.py"', cwd=tmp_path)
    result = execute_proposal(proposal, approved_digest=approval_digest(proposal))
    assert result.success
    assert 'first.py' in result.output and 'second.py' in result.output
    assert 'omit.txt' not in result.output and 'outside.py' not in result.output


def test_package_digest_covers_exact_target(tmp_path):
    original = parse_user_input('install vim', cwd=tmp_path)
    changed = parse_user_input('install curl', cwd=tmp_path)
    assert approval_digest(original) != approval_digest(changed)
    assert not validate_proposal(replace(original, target='curl'))[0]
    result = execute_proposal(changed, approved_digest=approval_digest(original))
    assert not result.success


def test_package_dry_run_never_executes(monkeypatch, tmp_path, capsys):
    import nli.cli_interface as cli
    monkeypatch.setattr(cli, 'execute_proposal', lambda *a, **kw: pytest.fail('preview cannot execute'))
    assert AIOSCli(cwd=tmp_path, dry_run=True).handle_request('install git') == 0
    text = capsys.readouterr().out
    assert text.count('update') >= 2 and 'password' in text
    assert 'Preview only' in text


def test_yes_cannot_make_packages_noninteractive(tmp_path):
    result = subprocess.run([sys.executable, '-m', 'nli.cli_interface', '--yes',
        '--workspace', str(tmp_path), '-c', 'install git'], cwd=ROOT, capture_output=True,
        text=True, timeout=5)
    assert result.returncode == 2
    assert 'interactive terminal' in result.stdout


def test_script_route_saves_without_execution(tmp_path):
    result = subprocess.run([sys.executable, '-m', 'nli.cli_interface', '--offline', '--yes',
        '--workspace', str(tmp_path), '--output-script', 'report.sh',
        '-c', 'write a bash script to check storage'], cwd=ROOT, capture_output=True,
        text=True, timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / 'report.sh').is_file()
    assert 'has not been executed' in result.stdout
    assert len(list(tmp_path.iterdir())) == 1


def test_script_preview_does_not_save(tmp_path):
    result = subprocess.run([sys.executable, '-m', 'nli.cli_interface', '--offline', '--dry-run',
        '--workspace', str(tmp_path), '--write-script', 'report storage usage'],
        cwd=ROOT, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not list(tmp_path.iterdir())
