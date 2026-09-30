"""Controlled VM benchmark: observation overhead or explicit disposable-worker renice.

No custom shell commands or production processes are modified. --mode renice tests
resource-control effects, not the complete ML policy. Raw per-trial outcomes and
restoration are reported; no speedup is assumed.
"""
import argparse
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import pwd
import signal
import statistics
import subprocess
import sys
import tempfile
import time

import psutil

from src.controller.process_controller import LinuxBackend, ResourceController
from src.optimization.models import Action, Category, Decision, WorkloadState
from src.optimization.state import StateStore

ROOT = Path(__file__).resolve().parent


def stop_owned(process):
    if process is not None and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=3)


def run_trial(mode, managed, duration, owner_uid, state_dir):
    backend = LinuxBackend()
    cpu = min(os.sched_getaffinity(0))
    credentials = {}
    if os.geteuid() == 0 and owner_uid != 0:
        account = pwd.getpwuid(owner_uid)
        credentials = dict(user=owner_uid, group=account.pw_gid, extra_groups=[])
    def spawn_worker(seconds):
        return subprocess.Popen([sys.executable, '-I', str(ROOT / 'scripts' / 'cpu_worker.py'),
            '--cpu-seconds', str(seconds), '--max-wall-seconds', '60', '--cpu', str(cpu)],
            start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, **credentials)
    background = observer = foreground = None
    store = controller = None
    record_identity = None
    restoration = None
    applied = False
    result = dict(mode=mode, managed=managed, cpu=cpu, action_applied=False)
    try:
        background = spawn_worker(55)
        time.sleep(0.15)
        target = psutil.Process(background.pid)
        original = target.nice()
        if managed and mode == 'renice':
            current = backend.metrics(target)
            store = StateStore(state_dir / 'state.json').acquire()
            controller = ResourceController(store, owner_uid)
            decision = Decision(current, Category.BACKGROUND_SAFE, Action.RENICE,
                'Explicit benchmark opt-in: only this disposable worker', WorkloadState.COMPILATION,
                new_nice=max(original, 10))
            outcome = controller.apply(decision)
            if not outcome.success or not outcome.changed:
                raise RuntimeError('Live control unavailable: ' + outcome.reason)
            applied = True
            record_identity = current.identity
            result['action_applied'] = True
        if managed and mode == 'observe':
            observer = subprocess.Popen([sys.executable, str(ROOT / 'main.py'), '--no-ml', '--interval', '1'],
                cwd=ROOT, start_new_session=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.15)
            if observer.poll() is not None:
                raise RuntimeError('Observation manager failed to start')
        start = time.monotonic()
        foreground = spawn_worker(duration)
        foreground.wait(timeout=60)
        result.update(wall_seconds=round(time.monotonic() - start, 5),
                      workload_exit_code=foreground.returncode)
        if foreground.returncode != 0:
            raise RuntimeError('Disposable workload did not finish successfully')
        if background.poll() is not None:
            raise RuntimeError('Background contention worker exited during measurement')
        if observer is not None and observer.poll() is not None:
            raise RuntimeError('Observation manager exited during measurement')
        if controller:
            restoration = controller.restore(record_identity)
            if not restoration.success or target.nice() != original or store.records:
                raise RuntimeError('Restoration failed: ' + restoration.reason)
            result['restored'] = True
        else:
            result['restored'] = None
        result['status'] = 'passed'
    except (OSError, RuntimeError, psutil.Error, subprocess.TimeoutExpired) as exc:
        result.update(status='blocked_or_failed', reason=str(exc))
    finally:
        try:
            if controller:
                for outcome in controller.restore_all():
                    if not outcome.success:
                        result.update(status='blocked_or_failed', recovery_error=outcome.reason)
        except Exception as exc:
            result.update(status='blocked_or_failed', recovery_error=str(exc))
        finally:
            if store:
                try:
                    store.close()
                except Exception as exc:
                    result.update(status='blocked_or_failed', cleanup_error=str(exc))
        for process in (foreground, background, observer):
            try:
                stop_owned(process)
            except (OSError, subprocess.TimeoutExpired) as exc:
                result.update(status='blocked_or_failed', cleanup_error=str(exc))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick', action='store_true', help='Compatibility flag; built-in bounded worker is always used')
    parser.add_argument('--mode', choices=('observe', 'renice'), default='observe')
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--duration', type=float, default=2, help='Foreground CPU seconds per trial')
    parser.add_argument('--target-uid', type=int, default=os.getuid())
    parser.add_argument('--output', type=Path, default=Path('artifacts/benchmark.json'))
    args = parser.parse_args(argv)
    if not 1 <= args.runs <= 10 or not math.isfinite(args.duration) or not 0.25 <= args.duration <= 10:
        parser.error('Use 1–10 runs and duration 0.25–10 seconds')
    if args.target_uid <= 0 or (os.geteuid() != 0 and args.target_uid != os.getuid()):
        parser.error('Choose your own non-root UID; a privileged VM run must specify --target-uid')
    trials = []
    with tempfile.TemporaryDirectory(prefix='ai-os-benchmark-') as temporary:
        for iteration in range(args.runs):
            for managed in ((False, True) if iteration % 2 == 0 else (True, False)):
                trial = run_trial(args.mode, managed, args.duration, args.target_uid, Path(temporary))
                trials.append(trial)
                print(json.dumps(trial), flush=True)
                if trial['status'] != 'passed':
                    break
            if trials[-1]['status'] != 'passed':
                break
    report = dict(mode=args.mode, trials=trials,
        measurement='Observation overhead only' if args.mode == 'observe' else 'Disposable-worker renice effect; not full ML optimization',
        success=all(t['status'] == 'passed' for t in trials) and len(trials) == 2 * args.runs)
    if report['success']:
        baseline = statistics.median(t['wall_seconds'] for t in trials if not t['managed'])
        managed = statistics.median(t['wall_seconds'] for t in trials if t['managed'])
        report.update(baseline_median_seconds=baseline, managed_median_seconds=managed,
                      measured_change_percent=round(100 * (baseline - managed) / baseline, 2))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print('Report:', args.output)
    return 0 if report['success'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
