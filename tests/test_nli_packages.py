"""Package workflows are tested with subprocess fakes: no privileged mutations."""
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest
from nli import _package_worker as worker
from nli.package_operations import APT_GET, SUDO, format_package_plan, package_plan, validate_packages


@pytest.mark.parametrize("target", ["", "--help", "-y", "./foo.deb", "git;id", "$(id)",
                                     "foo=1.0", "foo/stable", "foo*", "foo?", "foo-", "Apt", "git\x00", "g" ])
def test_invalid_package_names(target):
    with pytest.raises(ValueError):
        validate_packages(target)


def test_exact_package_tokens_and_deduplication():
    assert validate_packages("git g++ libfoo1.2:amd64 git") == ("git", "g++", "libfoo1.2:amd64")
    with pytest.raises(ValueError):
        validate_packages(" ".join(f"pkg{i}" for i in range(21)))


def test_install_always_updates_before_and_after():
    plan = package_plan("install_packages", ("git",))
    assert plan[0].argv[-1] == plan[2].argv[-1] == "update"
    assert "APT::Update::Error-Mode=any" in plan[0].argv
    assert plan[1].argv[-3:] == ("install", "--", "git")
    assert "--no-remove" in plan[1].argv
    assert "--allow-unauthenticated" not in plan[1].argv
    with pytest.raises(FrozenInstanceError):
        plan[0].explanation = "changed"
    assert "sudo /usr/bin/apt-get" in format_package_plan("install_packages", ("git",))


def test_remove_never_purges_or_autoremoves():
    plan = package_plan("remove_packages", ("git",))
    assert len(plan) == 1
    assert plan[0].argv[-3:] == ("remove", "--", "git")
    assert "purge" not in plan[0].argv and "autoremove" not in plan[0].argv


@pytest.mark.parametrize("action,packages", [("shell", ()), ("install_packages", ()),
                                             ("update_packages", ("git",)), ("remove_packages", ("git", "git"))])
def test_bad_plan_rejected(action, packages):
    with pytest.raises(ValueError):
        package_plan(action, packages)


def test_fresh_authentication_separate_from_package_stdin(monkeypatch):
    calls = []
    monkeypatch.setattr(worker.getpass, "getpass", lambda prompt: "dummy-private-password")
    monkeypatch.setattr(worker.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(returncode=0))
    argv = package_plan("update_packages")[0].argv
    assert worker.run_sudo_step(argv) == 0
    assert calls[0][0] == (SUDO, "-k")
    assert calls[1][0] == (SUDO, "-S", "-p", "", "-v")
    assert calls[1][1]["input"] == "dummy-private-password\n"
    assert calls[2][0] == (SUDO, "-n", "--", *argv)
    assert calls[2][1]["stdin"] == worker.subprocess.DEVNULL
    assert "input" not in calls[2][1]
    assert all("dummy-private-password" not in str(args) for args, _ in calls)
    assert calls[-1][0] == (SUDO, "-k")


def test_wrong_password_cannot_start_apt(monkeypatch):
    calls = []
    monkeypatch.setattr(worker.getpass, "getpass", lambda prompt: "incorrect")
    monkeypatch.setattr(worker.subprocess, "run", lambda args, **kwargs: calls.append(args) or SimpleNamespace(returncode=1 if "-v" in args else 0))
    assert worker.run_sudo_step((APT_GET, "update")) == 1
    assert calls == [(SUDO, "-k"), (SUDO, "-S", "-p", "", "-v"), (SUDO, "-k")]


@pytest.mark.parametrize("password", ["", "injected\nvalue", "bad\x00value"])
def test_empty_or_invalid_password_does_not_invoke_sudo(monkeypatch, password):
    monkeypatch.setattr(worker.getpass, "getpass", lambda prompt: password)
    monkeypatch.setattr(worker.subprocess, "run", lambda *a, **kw: pytest.fail("sudo invoked"))
    assert worker.run_sudo_step((APT_GET, "update")) == 130


def test_terminal_and_nonroot_are_required(monkeypatch):
    monkeypatch.setattr(worker.os, "geteuid", lambda: 0)
    with pytest.raises(RuntimeError, match="normal user"):
        worker.establish_terminal()
    monkeypatch.setattr(worker.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(worker.os, "isatty", lambda fd: False)
    with pytest.raises(RuntimeError, match="interactive terminal"):
        worker.establish_terminal()


@pytest.mark.parametrize("removing,stdout", [(False, "Package: otherpkg\nArchitecture: amd64\n"),
                                            (True, "otherpkg\tamd64\tinstalled\n"),
                                            (True, "git\tamd64\tconfig-files\n")])
def test_exact_package_lookup_rejects_fallback_matches(monkeypatch, removing, stdout):
    monkeypatch.setattr(worker.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=stdout))
    with pytest.raises(RuntimeError, match="Exact"):
        worker.verify_exact_packages(("git",), removing=removing)


@pytest.mark.parametrize("removing,stdout", [(False, "Package: g++\nArchitecture: amd64\n"),
                                            (True, "g++\tamd64\tinstalled\n")])
def test_exact_package_names_containing_plus_are_supported(monkeypatch, removing, stdout):
    monkeypatch.setattr(worker.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=stdout))
    worker.verify_exact_packages(("g++",), removing=removing)


def prepare_worker(monkeypatch):
    monkeypatch.setattr(worker, "establish_terminal", lambda: None)
    monkeypatch.setattr(worker.Path, "is_file", lambda self: True)
    monkeypatch.setattr(worker.os, "access", lambda *a: True)
    monkeypatch.setattr(worker, "verify_exact_packages", lambda *a, **kw: None)
    monkeypatch.setattr(worker, "confirm_simulation", lambda *a: True)


def test_install_failure_stops_remaining_steps(monkeypatch):
    prepare_worker(monkeypatch)
    calls = []
    def sudo(argv):
        calls.append(argv)
        return 0 if len(calls) == 1 else 100
    monkeypatch.setattr(worker, "run_sudo_step", sudo)
    assert worker.main(["install_packages", "git"]) == 100
    assert len(calls) == 2


def test_install_three_steps_and_simulation_after_update(monkeypatch):
    prepare_worker(monkeypatch)
    order = []
    monkeypatch.setattr(worker, "confirm_simulation", lambda argv: order.append("simulation") or True)
    monkeypatch.setattr(worker, "run_sudo_step", lambda argv: order.append("install" if "install" in argv else "update") or 0)
    assert worker.main(["install_packages", "git"]) == 0
    assert order == ["update", "simulation", "install", "update"]


def test_declined_dependency_preview_stops_remove(monkeypatch):
    prepare_worker(monkeypatch)
    monkeypatch.setattr(worker, "confirm_simulation", lambda argv: False)
    monkeypatch.setattr(worker, "run_sudo_step", lambda argv: pytest.fail("sudo invoked"))
    assert worker.main(["remove_packages", "git"]) == 130


def test_failed_simulation_cannot_be_confirmed(monkeypatch):
    monkeypatch.setattr(worker.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=100))
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("confirmation offered after failure"))
    assert worker.confirm_simulation((APT_GET, "remove", "git")) is False


def test_interrupt_waits_for_real_unprivileged_child_and_restores_handler(tmp_path):
    """A child may need longer than subprocess.run's SIGINT grace period to stop."""
    import signal
    import sys
    marker = tmp_path / "child-finished"
    previous = signal.getsignal(signal.SIGINT)
    code = ("import os,signal,time,pathlib; "
            "os.kill(os.getppid(), signal.SIGINT); "
            "time.sleep(0.4); "
            "pathlib.Path(__import__('sys').argv[1]).write_text('finished')")
    with pytest.raises(KeyboardInterrupt):
        worker._run_waited((sys.executable, "-I", "-c", code, str(marker)), check=False)
    assert marker.read_text() == "finished"
    assert signal.getsignal(signal.SIGINT) == previous


@pytest.mark.parametrize("interrupt_phase", ["authenticate", "apt"])
def test_interrupt_finishes_child_before_invalidation_and_stops_plan(monkeypatch, interrupt_phase):
    import signal
    prepare_worker(monkeypatch)
    monkeypatch.setattr(worker.getpass, "getpass", lambda prompt: "dummy-private-password")
    events = []
    def run(args, **kwargs):
        phase = "authenticate" if "-v" in args else "apt" if "-n" in args else "invalidate"
        events.append(phase + "-start")
        if phase == interrupt_phase:
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
            events.append("interrupt-recorded")
        events.append(phase + "-finished")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(worker.subprocess, "run", run)
    assert worker.main(["install_packages", "git"]) == 130
    assert events[-2:] == ["invalidate-start", "invalidate-finished"]
    assert events.index(interrupt_phase + "-finished") < len(events) - 2
    assert events.count("apt-start") == (1 if interrupt_phase == "apt" else 0)


def test_interrupt_during_simulation_never_offers_approval(monkeypatch):
    import signal
    events = []
    def run(*args, **kwargs):
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        events.append("simulation-finished")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(worker.subprocess, "run", run)
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("approval offered after cancellation"))
    with pytest.raises(KeyboardInterrupt):
        worker.confirm_simulation((APT_GET, "remove", "git"))
    assert events == ["simulation-finished"]
