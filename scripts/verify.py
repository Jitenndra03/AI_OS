#!/usr/bin/env python3
"""Run required local checks and save their actual outcomes and logs."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-sandbox', action='store_true', help='Require live isolation, not inspection only')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'artifacts/verification')
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    records = []
    def run(name, argv, timeout=180):
        started = time.monotonic()
        try:
            with (output / f'{name}.log').open('w') as log:
                result = subprocess.run([sys.executable, *map(str, argv)], cwd=ROOT,
                    stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
            code, error = result.returncode, None
        except (OSError, subprocess.TimeoutExpired) as exc:
            code, error = -1, str(exc)
        records.append(dict(name=name, exit_code=code, passed=code == 0,
            duration_seconds=round(time.monotonic()-started, 3),
            command=[sys.executable, *map(str, argv)], log=str(output / f'{name}.log'), error=error))
        print(f"{'PASS' if code == 0 else 'FAIL'} {name} ({records[-1]['duration_seconds']}s)", flush=True)
    required = ['--require-sandbox'] if args.require_sandbox else []
    run('dependencies', ['-m', 'pip', 'check'])
    run('doctor', [ROOT / 'doctor.py', '--json', *required])
    run('tests', ['-m', 'pytest', '-q'], timeout=300)
    with tempfile.TemporaryDirectory(prefix='ai-os-verify-') as temporary:
        run('optimizer_observation', [ROOT / 'main.py', '--once', '--no-ml-training',
            '--ml-model', Path(temporary) / 'absent-model.json'])
    run('demo', [ROOT / 'demo.py', '--output-dir', output / 'demo', *required])
    # Zero-byte compilation catches all source syntax without creating pyc files.
    syntax_errors = []
    sources = [*ROOT.glob('*.py')]
    for directory in ('src', 'nli', 'sandbox', 'scripts', 'tests'):
        sources.extend((ROOT / directory).rglob('*.py'))
    for path in sources:
        try:
            compile(path.read_bytes(), str(path), 'exec')
        except (SyntaxError, OSError) as exc:
            syntax_errors.append(str(exc))
    records.append(dict(name='source_syntax', passed=not syntax_errors,
                        files=len(sources), errors=syntax_errors))
    print(f"{'PASS' if not syntax_errors else 'FAIL'} source_syntax ({len(sources)} files)")
    report = dict(checked_at=datetime.now(timezone.utc).isoformat(), python=sys.version,
        sandbox_required=args.require_sandbox, checks=records,
        passed=all(r['passed'] for r in records),
        scope='Current environment; optional privileged live resource controls require separate VM validation')
    path = output / 'report.json'
    path.write_text(json.dumps(report, indent=2) + '\n')
    print(f'Report: {path}')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
