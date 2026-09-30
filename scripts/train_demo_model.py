#!/usr/bin/env python3
"""Generate a reproducible SYNTHETIC fixture and train a data-only demo model."""
from __future__ import annotations
import argparse
import csv
import json
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

# Support python scripts/train_demo_model.py as well as module execution.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from src.ai.feature_engine import FEATURE_NAMES, extract_features
from src.ai.workload_model import WorkloadClassifierModel
from src.optimization.models import ProcessMetrics, SystemSnapshot, WorkloadState


def make_demo_dataset(seed: int = 42, samples_per_class: int = 100) -> tuple[np.ndarray, np.ndarray]:
    """Interleaved chronological synthetic captures, never real measurements.

    The class-specific process names intentionally provide strong hints; this
    fixture tests the train/export/load/infer path, not real-world generalization.
    """
    rng = np.random.default_rng(seed)
    profiles = [(WorkloadState.NORMAL, "idle", 1),
                (WorkloadState.COMPILATION, "gcc", 85),
                (WorkloadState.GAMING, "dota2", 65),
                (WorkloadState.DEVELOPMENT, "code", 55),
                (WorkloadState.EDITING, "blender", 75),
                (WorkloadState.AI_ML, "torchrun", 95),
                (WorkloadState.HEAVY_UNKNOWN, "worker", 90)]
    X, y = [], []
    for capture in range(samples_per_class):
        for state, name, cpu in profiles:
            now = float(len(y))
            busy = state != WorkloadState.NORMAL
            process = ProcessMetrics(pid=100, create_time=1, uid=1000, name=name,
                exe=f"/usr/bin/{name}", cpu_percent=max(0, cpu + rng.normal(0, 3)),
                memory_percent=max(0, 5 + rng.normal()), num_threads=4 if busy else 1)
            snapshot = SystemSnapshot(timestamp=now, monotonic=now, processes=(process,),
                cpu_percent=float(rng.uniform(65, 90) if busy else rng.uniform(3, 15)),
                memory_percent=float(rng.uniform(35, 60)), cpu_count=4,
                load_average=(2.0, 1.5, 1.0) if busy else (.1, .1, .1))
            X.append(extract_features(snapshot, owner_uid=1000).values)
            y.append(state.value)
    return np.array(X), np.array(y)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/ai-os-ml-demo"))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    X, y = make_demo_dataset(args.seed)
    data_path = args.output_dir / "training_data.csv"
    fd, temporary = tempfile.mkstemp(prefix=".demo-data-", dir=args.output_dir)
    try:
        with os.fdopen(fd, "w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["label", *FEATURE_NAMES])
            writer.writerows([label, *values] for label, values in zip(y, X))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, data_path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    model_path = args.output_dir / "workload_model.json"
    model = WorkloadClassifierModel(model_path, random_state=args.seed)
    metrics = model.train(X, y)
    if metrics is None:
        parser.exit(1, "Synthetic demo training did not meet validation requirements.\n")
    report = dict(data_source="SYNTHETIC demonstration fixture; not real-world accuracy",
        seed=args.seed, model_path=str(model_path), training_data_path=str(data_path), metrics=asdict(metrics))
    report_path = args.output_dir / "metrics.json"
    fd, temporary = tempfile.mkstemp(prefix=".demo-report-", dir=args.output_dir)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(json.dumps(report, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, report_path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
