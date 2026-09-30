"""Read-only capability preflight for the AI_OS VM demonstration."""
import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def inspect_environment(check_sandbox=True):
    checks = []
    def add(name, ok, detail, required=True):
        checks.append(dict(name=name, ok=bool(ok), required=required, detail=detail))
    add('linux', sys.platform.startswith('linux'), platform.platform())
    add('python', sys.version_info >= (3, 11), platform.python_version())
    for distribution in ('psutil', 'numpy', 'pandas', 'scikit-learn', 'joblib', 'pytest'):
        try:
            version = importlib.metadata.version(distribution)
            add(distribution, True, version)
        except importlib.metadata.PackageNotFoundError:
            add(distribution, False, 'Install requirements.txt in this Python environment')
    add('procfs', Path('/proc/self/stat').is_file(), '/proc/self/stat')
    add('cgroup_v2', Path('/sys/fs/cgroup/cgroup.controllers').is_file(),
        'Optional: CPU/memory controls additionally require a delegated subtree', False)
    add('bubblewrap_installed', shutil.which('bwrap') is not None,
        shutil.which('bwrap') or 'Install bubblewrap in the VM', False)
    capabilities = 0
    try:
        for line in Path('/proc/self/status').read_text().splitlines():
            if line.startswith('CapEff:'):
                capabilities = int(line.split()[1], 16)
    except OSError:
        pass
    add('nice_restore_capability', bool(capabilities & (1 << 23)),
        'Optional: CAP_SYS_NICE or sufficient target RLIMIT_NICE is required for reversible renice', False)
    if check_sandbox:
        try:
            result = subprocess.run([sys.executable, '-m', 'sandbox.cli', 'doctor'],
                                    cwd=ROOT, capture_output=True, text=True, timeout=20)
            detail = (result.stdout or result.stderr).strip()[-6000:]
            add('sandbox_isolation', result.returncode == 0, detail, False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            add('sandbox_isolation', False, str(exc), False)
    return dict(python=sys.executable, checks=checks,
                ready=all(c['ok'] for c in checks if c['required']))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--require-sandbox', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    report = inspect_environment()
    if args.require_sandbox:
        for check in report['checks']:
            if check['name'] == 'sandbox_isolation':
                check['required'] = True
        report['ready'] &= any(c['name'] == 'sandbox_isolation' and c['ok'] for c in report['checks'])
    serialized = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + '\n')
    if args.json:
        print(serialized)
    else:
        for check in report['checks']:
            label = 'PASS' if check['ok'] else ('FAIL' if check['required'] else 'OPTIONAL / UNAVAILABLE')
            print(f"{label:24} {check['name']}: {check['detail']}")
        print('Core demo ready' if report['ready'] else 'Required capability unavailable')
    return 0 if report['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
