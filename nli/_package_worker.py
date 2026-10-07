"""Interactive password-gated APT worker. Never accepts arbitrary commands.

Executed as an isolated Python file in a dedicated controlling PTY. A password is
requested separately for every privileged APT step and is only passed over an
anonymous pipe to sudo. The OS sudoers policy remains the authentication authority.
"""
from __future__ import annotations
import fcntl
import getpass
import os
import signal
from pathlib import Path
import subprocess
import sys
import termios

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nli.package_operations import APT_GET, SUDO, package_plan, validate_packages

APT_CACHE = "/usr/bin/apt-cache"
DPKG_QUERY = "/usr/bin/dpkg-query"


def _run_waited(*args, **kwargs):
    """Defer Ctrl+C until this subprocess exits, then stop the remaining plan.

    The PTY foreground process group still delivers SIGINT to sudo/APT. Raising
    KeyboardInterrupt in this worker immediately can let subprocess.run abandon
    its child after Python's short SIGINT grace period. Keep the worker alive
    until that child actually finishes, including authentication and simulation.
    Password-entry and approval prompts remain immediately cancellable.
    """
    interrupted = False

    def defer_interrupt(signum, frame):
        nonlocal interrupted
        interrupted = True

    previous = signal.signal(signal.SIGINT, defer_interrupt)
    try:
        result = subprocess.run(*args, **kwargs)
    finally:
        signal.signal(signal.SIGINT, previous)
    if interrupted:
        raise KeyboardInterrupt
    return result


def establish_terminal() -> None:
    if os.geteuid() == 0:
        raise RuntimeError("Run AI_OS as your normal user; root execution bypasses the required sudo password gate.")
    if not os.isatty(0) or not os.isatty(1):
        raise RuntimeError("Package changes require an interactive terminal for password entry.")
    try:
        fd = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        # Parent starts a new session, then connects this worker's stdin to its PTY.
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    else:
        os.close(fd)


def verify_exact_packages(packages: tuple[str, ...], *, removing: bool) -> None:
    """Refuse APT's fallback pattern/operator matching for nonexistent names."""
    for package in packages:
        base, _, arch = package.partition(":")
        if removing:
            args = (DPKG_QUERY, "--show", "--showformat=${Package}\t${Architecture}\t${db:Status-Status}\\n", "--", package)
        else:
            args = (APT_CACHE, "show", "--no-all-versions", "--", package)
        result = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False,
                                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
        if removing:
            exact = any(fields[0] == base and (not arch or fields[1] == arch)
                        and fields[2] == "installed"
                        for line in result.stdout.splitlines()
                        if len(fields := line.split("\t")) == 3)
        else:
            records = result.stdout.split("\n\n")
            exact = False
            for record in records:
                fields = dict(line.split(": ", 1) for line in record.splitlines() if ": " in line and not line.startswith(" "))
                if fields.get("Package") == base and (not arch or fields.get("Architecture") in {arch, "all"}):
                    exact = True
        if result.returncode or not exact:
            raise RuntimeError(f"Exact {'installed ' if removing else ''}package {package!r} was not found. No wildcard or approximate match will be executed.")


def confirm_simulation(argv: tuple[str, ...]) -> bool:
    print("\nRead-only APT simulation follows. It may differ if package state changes concurrently.", flush=True)
    result = _run_waited((APT_GET, "--simulate", *argv[1:]), check=False)
    if result.returncode:
        print("APT simulation failed; no package change will be attempted.", flush=True)
        return False
    return input("Approve the package/dependency changes shown above? Type yes: ").strip().lower() == "yes"


def run_sudo_step(argv: tuple[str, ...]) -> int:
    # getpass never falls back to stdin because establish_terminal already checked
    # the controlling tty. An empty answer never invokes a privileged command.
    password = getpass.getpass("Enter your sudo password for this command (hidden): ")
    if not password or "\n" in password or "\r" in password or "\x00" in password:
        print("Password entry cancelled or invalid; command was not executed.", flush=True)
        return 130
    try:
        invalidated = _run_waited((SUDO, "-k"), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     check=False, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
        if invalidated.returncode:
            return invalidated.returncode
        authenticated = _run_waited((SUDO, "-S", "-p", "", "-v"),
                                       input=password + "\n", text=True, check=False,
                                       env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TERM": "dumb"})
        password = None
        if authenticated.returncode:
            return authenticated.returncode
        # No password bytes can reach apt or root package-maintainer scripts,
        # including when sudoers uses NOPASSWD and authentication skips reading.
        result = _run_waited((SUDO, "-n", "--", *argv),
                                stdin=subprocess.DEVNULL, check=False,
                                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TERM": "dumb"})
        return result.returncode
    finally:
        # Python strings cannot be reliably zeroized; never persist or log them.
        password = None
        # Invalidate this terminal's sudo timestamp even when apt fails.
        _run_waited((SUDO, "-k"), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False,
                       env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if not args:
            raise ValueError("A package action is required.")
        action = args[0]
        packages = validate_packages(" ".join(args[1:])) if len(args) > 1 else ()
        plan = package_plan(action, packages)
        establish_terminal()
        if not all(Path(binary).is_file() and os.access(binary, os.X_OK)
                   for binary in (SUDO, APT_GET, APT_CACHE, DPKG_QUERY)):
            raise RuntimeError("This feature requires Debian/Ubuntu with sudo and APT installed.")
        print("Every command below requires a fresh password entry. Your system's sudo policy decides whether that password is authenticated.", flush=True)
        print("Package maintainers' installation scripts can run as root. Use trusted configured repositories. Do not interrupt an active package transaction.", flush=True)
        for number, step in enumerate(plan, 1):
            print(f"\nStep {number}/{len(plan)}: {step.display}\n{step.explanation}", flush=True)
            if "install" in step.argv or "remove" in step.argv:
                verify_exact_packages(packages, removing=action == "remove_packages")
                if not confirm_simulation(step.argv):
                    print("Package operation cancelled. Earlier index refreshes, if any, remain completed.", flush=True)
                    return 130
            code = run_sudo_step(step.argv)
            if code:
                print(f"Step failed with exit code {code}; remaining steps were not run. Completed earlier steps are not rolled back.", flush=True)
                return code if 0 < code < 256 else 1
        print("Package operation completed.", flush=True)
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nPackage operation cancelled. Check APT state before retrying if a transaction was active.", flush=True)
        return 130
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"Package operation blocked: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
