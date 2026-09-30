"""AI_OS Component 1: observe by default, opt-in reversible workload control."""

import argparse
import json
import math
import os
from pathlib import Path
import signal
import sys
import threading

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import settings
from src.optimization.config import load_rules
from src.optimization.models import Action
from src.optimization.recovery import RecoveryGuard, recover, wait_for_owner
from src.utils.logger import system_logger


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Apply explicitly opted-in background policy")
    mode.add_argument("--recover", action="store_true", help="Restore durable pending actions and exit")
    parser.add_argument("--policy", type=Path, help="JSON background executable policy; default: none")
    parser.add_argument("--once", action="store_true", help="Sample once and exit (first rates are zero)")
    parser.add_argument("--interval", type=float, default=settings.REFRESH_INTERVAL)
    parser.add_argument("--state-file", type=Path, default=settings.OPTIMIZATION_STATE_PATH)
    parser.add_argument("--target-uid", type=int, default=os.getuid(), help="Non-root user whose processes may be managed")
    parser.add_argument("--foreground-pid", type=int, action="append", default=None,
                        help="Protect this application tree; repeatable (also protects terminal foreground jobs)")
    parser.add_argument("--cgroup-root", type=Path, help="Explicitly delegated cgroup v2 subtree")
    parser.add_argument("--allow-suspend", action="store_true", help="Enable policies explicitly allowing suspension")
    parser.add_argument("--record-metrics", action="store_true", help="Also append legacy CSV telemetry")
    ml_group = parser.add_mutually_exclusive_group()
    ml_group.add_argument("--ml", action="store_true", default=True, dest="ml",
                          help="Enable ML-assisted workload recognition (default)")
    ml_group.add_argument("--no-ml", action="store_false", dest="ml",
                          help="Disable ML; use only deterministic detection")
    parser.add_argument("--ml-model", type=Path, default=settings.ML_MODEL_PATH,
                        help="Path to the ML workload model file")
    parser.add_argument("--ml-training-data", type=Path, default=settings.ML_TRAINING_DATA_PATH,
                        help="Path to the ML training data CSV")
    parser.add_argument("--ml-train-now", action="store_true",
                        help="Force immediate ML model training from collected data and exit")
    parser.add_argument("--no-ml-training", action="store_true",
                        help="Use a saved model without collecting data or retraining")
    parser.add_argument("--watch-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--ready-fd", type=int, help=argparse.SUPPRESS)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not math.isfinite(args.interval) or args.interval < 1:
        parser.error("--interval must be finite and at least one second")
    if args.target_uid < 0 or ((args.apply or args.recover) and args.target_uid == 0):
        parser.error("Live optimization/recovery requires a non-root --target-uid")
    if os.getuid() != 0 and args.target_uid != os.getuid():
        parser.error("An unprivileged instance may only manage its own UID")
    if args.watch_fd is not None or args.ready_fd is not None:
        if not args.recover or args.watch_fd is None or args.ready_fd is None:
            parser.error("Internal watchdog descriptors require recovery mode and both pipes")
        # A ready acknowledgement means recovery imports and journal access
        # have already succeeded, before the owner can modify any process.
        from src.controller.process_controller import ResourceController
        from src.optimization.state import StateStore
        probe = StateStore(args.state_file)
        probe.close()
        wait_for_owner(args.watch_fd, args.ready_fd)
    if args.recover:
        return recover(args.state_file, args.target_uid, cgroup_root=args.cgroup_root,
                       allow_suspend=args.allow_suspend, retry=args.watch_fd is not None)
    try:
        rules = load_rules(args.policy)
    except (OSError, ValueError, TypeError) as exc:
        parser.error(f"Invalid policy: {exc}")
    if args.apply and not rules:
        parser.error("--apply requires a policy with explicitly opted-in background executables")
    if args.foreground_pid and any(pid <= 2 for pid in args.foreground_pid):
        parser.error("--foreground-pid must identify a user application")

    from src.monitor.metrics_collector import SystemMonitor
    from src.optimization.classification import ProcessClassifier
    from src.optimization.decisions import DecisionEngine
    from src.optimization.engine import OptimizationEngine
    from src.optimization.workload import WorkloadDetector

    monitor = SystemMonitor()
    base_detector = WorkloadDetector(owner_uid=args.target_uid,
                                     background_executables=[rule.executable for rule in rules],
                                     sustained_seconds=settings.WORKLOAD_SUSTAIN_SECONDS,
                                     release_seconds=settings.WORKLOAD_RELEASE_SECONDS)

    # ML layer wraps the deterministic detector when enabled
    ml_detector = None
    if args.ml:
        try:
            from src.ai.ml_workload_detector import MLWorkloadDetector
            ml_detector = MLWorkloadDetector(
                base_detector,
                model_path=args.ml_model,
                training_data_path=args.ml_training_data,
                owner_uid=args.target_uid,
                enable_training=not args.no_ml_training,
                retrain_interval=settings.ML_RETRAIN_INTERVAL_SECONDS,
                retrain_min_samples=settings.ML_RETRAIN_MIN_NEW_SAMPLES,
            )
            system_logger.info("ML layer enabled (model_ready=%s, version=%d)",
                               ml_detector.model.is_ready, ml_detector.model.version)
        except Exception as exc:
            system_logger.warning("ML layer unavailable, falling back to deterministic: %s", exc)

    if args.ml_train_now:
        if ml_detector is None:
            parser.error("--ml-train-now requires ML to be enabled")
        try:
            success = ml_detector.force_train()
            system_logger.info("ML training %s", "succeeded" if success else "failed")
            return 0 if success else 1
        finally:
            ml_detector.close()

    detector = ml_detector if ml_detector is not None else base_detector
    classifier = ProcessClassifier(rules, owner_uid=args.target_uid)
    controller = store = guard = None
    foreground_identities = None
    shutdown = threading.Event()
    previous_handlers = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[sig] = signal.signal(sig, lambda *_: shutdown.set())
    status = 0
    try:
        if args.apply:
            from src.controller.process_controller import ResourceController
            from src.optimization.state import StateStore
            store = StateStore(args.state_file)
            store.acquire()
            controller = ResourceController(store, args.target_uid, cgroup_root=args.cgroup_root,
                                            allow_suspend=args.allow_suspend)
            guard = RecoveryGuard(args.state_file, args.target_uid, cgroup_root=args.cgroup_root,
                                  allow_suspend=args.allow_suspend)
            guard.start()
            for result in controller.restore_all():
                system_logger.info("Startup recovery: %s", result.reason)
            if store.records:
                raise RuntimeError("Unresolved recovery records; refusing new optimization")
        engine = OptimizationEngine(detector, classifier, DecisionEngine(owner_uid=args.target_uid), controller,
                                    max_snapshot_age=settings.OPTIMIZATION_MAX_SNAPSHOT_AGE)
        ml_status = "OFF"
        if ml_detector is not None:
            ml_status = f"ON (model_v{ml_detector.model.version}, ready={ml_detector.model.is_ready})"
        system_logger.info("Optimizer mode=%s, opted-in executables=%d, ml=%s",
                           "APPLY" if args.apply else "OBSERVE", len(rules), ml_status)
        while not shutdown.is_set():
            if guard and not guard.healthy():
                raise RuntimeError("Recovery worker exited; restoring and stopping")
            snapshot = monitor.sample()
            if args.foreground_pid:
                from dataclasses import replace
                if foreground_identities is None:
                    foreground_identities = {p.identity for p in snapshot.processes
                                             if p.pid in args.foreground_pid and p.create_time > 0}
                    if len(foreground_identities) != len(set(args.foreground_pid)):
                        system_logger.warning("Some foreground PIDs were absent; they will not be rebound to later processes")
                snapshot = replace(snapshot, processes=tuple(
                    replace(p, foreground=True) if p.identity in foreground_identities else p
                    for p in snapshot.processes))
            result = engine.cycle(snapshot)
            proposals = [dict(pid=d.process.pid, identity=d.process.identity,
                              classification=d.classification.value, action=d.action.value,
                              old_nice=d.process.nice, new_nice=d.new_nice, reason=d.reason)
                         for d in result.decisions if d.action != Action.NOOP]
            log_entry = dict(workload=result.workload.state.value,
                reason=result.workload.reason, processes=len(snapshot.processes),
                proposals=proposals)
            # Include ML prediction metadata when available
            if hasattr(result.workload, 'ml_prediction') and result.workload.ml_prediction:
                ml_pred = result.workload.ml_prediction
                log_entry["ml"] = dict(
                    predicted=ml_pred.predicted_state.value,
                    confidence=round(ml_pred.confidence, 3),
                    model_version=ml_pred.model_version,
                )
            if hasattr(result.workload, 'ml_early_hint'):
                log_entry["ml_early_hint"] = result.workload.ml_early_hint
            system_logger.info("%s", json.dumps(log_entry))
            for control in result.results:
                system_logger.info("Control %s: %s", control.identity, control.reason)
            if store and store.records and any(not c.success for c in result.results):
                raise RuntimeError("Control/recovery failed with pending state; handing off to recovery worker")
            if args.record_metrics:
                from src.monitor.data_logger import log_rows
                from dataclasses import asdict
                log_rows([asdict(p) for p in sorted(snapshot.processes,
                         key=lambda p: p.cpu_percent, reverse=True)[:settings.TOP_PROCESS_LIMIT]])
            if args.once:
                break
            shutdown.wait(args.interval)
    except Exception:
        system_logger.exception("Optimizer stopped after an error")
        status = 1
    finally:
        try:
            if controller:
                for result in controller.restore_all():
                    system_logger.info("Shutdown restore: %s", result.reason)
                if store.records:
                    system_logger.error("Restoration remains pending in %s", args.state_file)
                    status = 1
        except Exception:
            system_logger.exception("Restoration incomplete; independent recovery will retry")
            status = 1
        finally:
            if store:
                store.close()
            if guard:
                guard.close()
            if ml_detector:
                ml_detector.close()
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
