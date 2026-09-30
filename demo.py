"""Reproducible offline demonstration; optimization actuators use synthetic processes."""
from __future__ import annotations
import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent


def optimization_demo(directory):
    from src.controller.process_controller import ResourceController
    from src.optimization.classification import ProcessClassifier
    from src.optimization.decisions import DecisionEngine
    from src.optimization.engine import OptimizationEngine
    from src.optimization.models import BackgroundRule, ProcessMetrics, SystemSnapshot
    from src.optimization.state import StateStore
    from src.optimization.workload import WorkloadDetector

    worker = ProcessMetrics(pid=4242, create_time=1, ppid=200, uid=1000,
        name='demo-worker', exe='/demo/background-worker', cpu_percent=40,
        status='running', cgroup='/user.slice/demo', session_id=4242)
    compiler = ProcessMetrics(pid=5000, create_time=2, ppid=200, uid=1000,
        name='gcc', exe='/usr/bin/gcc', cpu_percent=85, status='running', session_id=5000)
    events = []
    # This backend has no psutil.Process or OS resource mutation methods.
    class SyntheticProcess:
        pid = worker.pid
        def nice(self, value):
            backend.live = replace(backend.live, nice=value)
            events.append({'event': 'simulated_nice', 'value': value,
                           'journal_phase': store.records[worker.identity].phase})
    class SyntheticBackend:
        live = worker
        def process(self, pid):
            if pid != worker.pid:
                raise RuntimeError('Demo backend accepts only its synthetic worker')
            return SyntheticProcess()
        def metrics(self, proc):
            return self.live
        def self_ancestry(self):
            return {9000}
        def can_restore_nice(self, proc, original):
            return True
    backend = SyntheticBackend()
    store = StateStore(directory / 'state.json')
    store.acquire()
    controller = ResourceController(store, 1000, backend=backend)
    clock = [0.0]
    engine = OptimizationEngine(
        WorkloadDetector(owner_uid=1000, sustained_seconds=2, release_seconds=2,
                         background_executables=(worker.exe,)),
        ProcessClassifier((BackgroundRule(worker.exe),), owner_uid=1000, self_pid=9000),
        DecisionEngine(owner_uid=1000), controller, self_pid=9000, clock=lambda: clock[0])
    cycles = []
    try:
        for timestamp, heavy in ((0, False), (1, True), (3, True), (4, False), (6, False)):
            clock[0] = float(timestamp)
            snapshot = SystemSnapshot(timestamp=timestamp, monotonic=timestamp,
                processes=(backend.live, compiler) if heavy else (backend.live,),
                cpu_percent=95 if heavy else 10, cpu_count=2)
            result = engine.cycle(snapshot)
            cycles.append(dict(time=timestamp, workload=result.workload.state.value,
                worker_nice=backend.live.nice, records=len(store.records),
                decisions=[asdict(d) for d in result.decisions if d.process.pid == worker.pid],
                results=[asdict(r) for r in result.results]))
            if any(not r.success for r in result.results):
                raise RuntimeError('Synthetic control cycle failed')
        if [e['value'] for e in events] != [10, 0] or store.records or backend.live.nice != 0:
            raise RuntimeError('Demo did not apply and restore exactly once')
        return dict(status='passed', mode='SYNTHETIC processes and actuator; real policy, controller and journal',
                    cycles=cycles, events=events, restored=True)
    finally:
        controller.restore_all()
        store.close()


def run_command(argv, timeout=60):
    result = subprocess.run([sys.executable, *map(str, argv)], cwd=ROOT,
                            capture_output=True, text=True, timeout=timeout)
    return dict(command=[sys.executable, *map(str, argv)], exit_code=result.returncode,
                stdout=result.stdout[-20000:], stderr=result.stderr[-5000:])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path('artifacts/demo'))
    parser.add_argument('--require-sandbox', action='store_true')
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    report = {'scope': 'Offline VM demonstration; no live optimizer resource changes', 'components': {}}
    components = report['components']
    try:
        with tempfile.TemporaryDirectory(prefix='optimizer-', dir=args.output_dir) as temporary:
            components['optimization'] = optimization_demo(Path(temporary))
        print('PASS optimization: synthetic workload -> journaled priority reduction -> restore')
        training = run_command(['-m', 'scripts.train_demo_model', '--output-dir', args.output_dir.resolve() / 'ml'])
        components['ml_training'] = training
        if training['exit_code']:
            raise RuntimeError('Synthetic ML training failed')
        from src.ai.workload_model import WorkloadClassifierModel
        from src.optimization.models import ProcessMetrics, SystemSnapshot
        model = WorkloadClassifierModel(args.output_dir / 'ml/workload_model.json')
        prediction = model.predict(SystemSnapshot(timestamp=1, monotonic=1, cpu_percent=80,
            memory_percent=45, cpu_count=4, load_average=(2, 1.5, 1), processes=(
            ProcessMetrics(pid=100, create_time=1, uid=1000, name='gcc', exe='/usr/bin/gcc',
                           cpu_percent=85, memory_percent=5, num_threads=4),)), 1000)
        if prediction is None or prediction.predicted_state.value != 'COMPILATION':
            raise RuntimeError('Synthetic JSON model reload/inference failed')
        components['ml_inference'] = asdict(prediction)
        print('PASS ML: train, validate, export JSON, reload, predict (SYNTHETIC data)')
        components['cli'] = run_command(['-m', 'nli.cli_interface', '--offline', '--yes', '-c', 'show memory usage'])
        if components['cli']['exit_code']:
            raise RuntimeError('Approved read-only CLI action failed')
        print('PASS assistant: offline intent -> preview -> explicit approval -> dedicated PTY')
        from sandbox.core import doctor, inspect_artifact, run_artifact
        sample = ROOT / 'examples/sandbox/isolation_demo.py'
        components['sandbox_inspection'] = inspect_artifact(sample)
        check = doctor()
        components['sandbox_preflight'] = check
        if check['status'] == 'available':
            execution = run_artifact(sample, 5)
            components['sandbox_execution'] = execution
            if execution['status'] != 'completed':
                raise RuntimeError('Isolated harmless demonstration failed')
            print('PASS sandbox: static inspection + verified isolated execution of harmless example')
        elif args.require_sandbox:
            raise RuntimeError('Sandbox isolation unavailable; see sandbox_preflight report')
        else:
            print('UNAVAILABLE sandbox execution: inspection only; use --require-sandbox for full demo')
        report['status'] = 'passed' if check['status'] == 'available' else 'partial'
    except Exception as exc:
        report.update(status='failed', error=str(exc))
        print(f'FAIL: {exc}', file=sys.stderr)
    path = args.output_dir / 'report.json'
    path.write_text(json.dumps(report, indent=2) + '\n')
    print(f'Report: {path}')
    return 1 if report['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
