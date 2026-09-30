"""Tests for the ML layer: feature engineering, training data, model, and detector."""

import csv
import os
import tempfile
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from src.ai.feature_engine import (
    FEATURE_NAMES, NUM_FEATURES, extract_features, extract_features_batch,
)
from src.ai.training_data import TrainingDataCollector
from src.ai.workload_model import WorkloadClassifierModel, _MIN_TOTAL_SAMPLES
from src.ai.ml_workload_detector import MLWorkloadDetector, EnhancedWorkload
from src.optimization.models import ProcessMetrics, SystemSnapshot, Workload, WorkloadState
from src.optimization.workload import WorkloadDetector


# ── Test helpers ─────────────────────────────────────────────────────────────

def make_process(pid=10, name="worker", uid=1000, **kwargs):
    values = dict(pid=pid, create_time=float(pid), uid=uid, name=name,
                  exe=f"/usr/bin/{name}", cpu_percent=50.0, memory_percent=5.0,
                  num_threads=4, status="running", accessible=True)
    values.update(kwargs)
    return ProcessMetrics(**values)


def make_snapshot(now=1.0, processes=None, **kwargs):
    if processes is None:
        processes = (make_process(),)
    defaults = dict(
        timestamp=10_000 + now, monotonic=now, processes=tuple(processes),
        cpu_percent=60.0, memory_percent=50.0, memory_available=8_000_000_000,
        load_average=(2.0, 1.5, 1.0), disk_read_bytes_per_sec=1000.0,
        disk_write_bytes_per_sec=500.0, cpu_pressure=5.0, memory_pressure=3.0,
        io_pressure=2.0, cpu_count=4, complete=True,
    )
    defaults.update(kwargs)
    return SystemSnapshot(**defaults)


def make_workload(state=WorkloadState.COMPILATION, confidence=0.9):
    return Workload(state=state, confidence=confidence, reason="test",
                    protected_pids=frozenset({10}), since=1.0)


# ── Feature Engine Tests ────────────────────────────────────────────────────

class TestFeatureEngine:

    def test_extract_produces_correct_shape(self):
        snap = make_snapshot()
        fv = extract_features(snap, owner_uid=1000)
        assert fv.values.shape == (NUM_FEATURES,)
        assert len(fv.feature_names) == NUM_FEATURES
        assert fv.owner_uid == 1000

    def test_all_features_are_finite(self):
        snap = make_snapshot()
        fv = extract_features(snap, owner_uid=1000)
        assert np.all(np.isfinite(fv.values))

    def test_empty_snapshot_produces_zeros_for_aggregates(self):
        snap = make_snapshot(processes=())
        fv = extract_features(snap, owner_uid=1000)
        assert fv.values.shape == (NUM_FEATURES,)
        # System features should still have values
        assert fv.values[0] == 60.0  # sys_cpu_percent

    def test_hint_features_detect_known_executables(self):
        gcc = make_process(name="gcc")
        snap = make_snapshot(processes=(gcc,))
        fv = extract_features(snap, owner_uid=1000)
        # hint_compilation should be 1.0
        hint_idx = FEATURE_NAMES.index("hint_compilation")
        assert fv.values[hint_idx] == 1.0
        # Other hints should be 0
        for name in ["hint_ai_ml", "hint_gaming", "hint_editing", "hint_development"]:
            assert fv.values[FEATURE_NAMES.index(name)] == 0.0

    def test_hint_features_detect_development_tools(self):
        code = make_process(name="code")
        snap = make_snapshot(processes=(code,))
        fv = extract_features(snap, owner_uid=1000)
        hint_idx = FEATURE_NAMES.index("hint_development")
        assert fv.values[hint_idx] == 1.0

    def test_non_owner_processes_excluded_from_aggregates(self):
        root_proc = make_process(uid=0, cpu_percent=90.0)
        snap = make_snapshot(processes=(root_proc,))
        fv = extract_features(snap, owner_uid=1000)
        n_user_idx = FEATURE_NAMES.index("n_user_processes")
        assert fv.values[n_user_idx] == 0.0

    def test_tree_features_measure_depth_and_width(self):
        parent = make_process(pid=10, name="make", cpu_percent=5.0)
        child1 = make_process(pid=11, name="gcc", ppid=10, cpu_percent=40.0)
        child2 = make_process(pid=12, name="gcc", ppid=10, cpu_percent=40.0)
        grandchild = make_process(pid=13, name="cc1", ppid=11, cpu_percent=30.0)
        snap = make_snapshot(processes=(parent, child1, child2, grandchild))
        fv = extract_features(snap, owner_uid=1000)
        depth_idx = FEATURE_NAMES.index("max_tree_depth")
        width_idx = FEATURE_NAMES.index("max_tree_width")
        assert fv.values[depth_idx] >= 2  # at least parent -> child -> grandchild
        assert fv.values[width_idx] >= 2  # parent has 2 children

    def test_batch_extraction(self):
        snaps = [make_snapshot(now=float(i)) for i in range(5)]
        X = extract_features_batch(snaps, owner_uid=1000)
        assert X.shape == (5, NUM_FEATURES)
        assert np.all(np.isfinite(X))

    def test_empty_batch(self):
        X = extract_features_batch([], owner_uid=1000)
        assert X.shape == (0, NUM_FEATURES)

    def test_incomplete_snapshot_still_produces_features(self):
        snap = make_snapshot(complete=False)
        fv = extract_features(snap, owner_uid=1000)
        assert fv.values.shape == (NUM_FEATURES,)
        assert np.all(np.isfinite(fv.values))

    def test_process_with_nan_values(self):
        proc = make_process(cpu_percent=float("nan"), memory_percent=float("inf"))
        snap = make_snapshot(processes=(proc,))
        fv = extract_features(snap, owner_uid=1000)
        assert np.all(np.isfinite(fv.values))


# ── Training Data Collector Tests ────────────────────────────────────────────

class TestTrainingDataCollector:

    def test_collect_writes_labeled_sample(self, tmp_path):
        path = tmp_path / "training.csv"
        collector = TrainingDataCollector(path, owner_uid=1000)
        snap = make_snapshot()
        workload = make_workload()
        assert collector.collect(snap, workload)
        assert path.exists()
        assert collector.sample_count >= 1

    def test_collect_rejects_low_confidence(self, tmp_path):
        path = tmp_path / "training.csv"
        collector = TrainingDataCollector(path, owner_uid=1000)
        snap = make_snapshot()
        workload = Workload(state=WorkloadState.COMPILATION, confidence=0.3)
        assert not collector.collect(snap, workload)
        assert collector.sample_count == 0

    def test_collect_rejects_incomplete_snapshot(self, tmp_path):
        path = tmp_path / "training.csv"
        collector = TrainingDataCollector(path, owner_uid=1000)
        snap = make_snapshot(complete=False)
        workload = make_workload()
        assert not collector.collect(snap, workload)

    def test_normal_state_downsampled(self, tmp_path):
        path = tmp_path / "training.csv"
        collector = TrainingDataCollector(path, owner_uid=1000,
                                          normal_sample_rate=0.1)
        snap = make_snapshot(processes=(), cpu_percent=10, memory_percent=40,
                             load_average=(.1, .1, .1), cpu_pressure=0, memory_pressure=0, io_pressure=0)
        normal = Workload(state=WorkloadState.NORMAL, confidence=0.9)
        written = sum(collector.collect(snap, normal) for _ in range(100))
        # With 0.1 rate, roughly 10 should be written
        assert 5 <= written <= 20

    def test_load_returns_correct_shape(self, tmp_path):
        path = tmp_path / "training.csv"
        collector = TrainingDataCollector(path, owner_uid=1000)
        snap = make_snapshot()
        for state in [WorkloadState.COMPILATION, WorkloadState.GAMING,
                      WorkloadState.DEVELOPMENT]:
            workload = make_workload(state=state)
            collector.collect(snap, workload)
        X, y = collector.load()
        assert X.shape[1] == NUM_FEATURES
        assert len(y) == X.shape[0]
        assert X.shape[0] >= 3

    def test_load_nonexistent_raises(self, tmp_path):
        path = tmp_path / "missing.csv"
        collector = TrainingDataCollector(path, owner_uid=1000)
        with pytest.raises(FileNotFoundError):
            collector.load()

    def test_trim_caps_rows(self, tmp_path):
        path = tmp_path / "training.csv"
        collector = TrainingDataCollector(path, owner_uid=1000, max_rows=1000)
        snap = make_snapshot()
        workload = make_workload()
        for _ in range(1050):
            collector.collect(snap, workload)
        assert collector.sample_count <= 1000


# ── Workload Classifier Model Tests ─────────────────────────────────────────

def _generate_training_data(n_per_class=60):
    """Generate synthetic training data for multiple workload states."""
    rng = np.random.RandomState(42)
    X_list, y_list = [], []

    state_profiles = {
        WorkloadState.NORMAL: {
            "sys_cpu": (20, 10), "max_cpu": (5, 3), "n_busy": (0, 1),
        },
        WorkloadState.COMPILATION: {
            "sys_cpu": (80, 10), "max_cpu": (70, 15), "n_busy": (3, 2),
        },
        WorkloadState.GAMING: {
            "sys_cpu": (60, 15), "max_cpu": (50, 10), "n_busy": (2, 1),
        },
        WorkloadState.DEVELOPMENT: {
            "sys_cpu": (40, 15), "max_cpu": (20, 10), "n_busy": (1, 1),
        },
    }

    for state, profile in state_profiles.items():
        for _ in range(n_per_class):
            features = rng.randn(NUM_FEATURES) * 5 + 10
            # Set characteristic features
            features[FEATURE_NAMES.index("sys_cpu_percent")] = max(0, rng.normal(*profile["sys_cpu"]))
            features[FEATURE_NAMES.index("max_process_cpu")] = max(0, rng.normal(*profile["max_cpu"]))
            features[FEATURE_NAMES.index("n_busy_processes")] = max(0, rng.normal(*profile["n_busy"]))

            # Set hint features
            if state == WorkloadState.COMPILATION:
                features[FEATURE_NAMES.index("hint_compilation")] = 1.0
            elif state == WorkloadState.GAMING:
                features[FEATURE_NAMES.index("hint_gaming")] = 1.0
            elif state == WorkloadState.DEVELOPMENT:
                features[FEATURE_NAMES.index("hint_development")] = 1.0

            features = np.clip(features, 0, None)
            X_list.append(features)
            y_list.append(state.value)

    # Interleave synthetic captures so chronological holdout contains every class.
    order = rng.permutation(len(y_list))
    return np.array(X_list)[order], np.array(y_list)[order]


class TestWorkloadClassifierModel:

    def test_train_produces_model(self, tmp_path):
        model_path = tmp_path / "model.json"
        model = WorkloadClassifierModel(model_path)
        X, y = _generate_training_data()
        metrics = model.train(X, y)
        assert metrics is not None
        assert metrics.accuracy > 0.5
        assert metrics.n_classes >= 2
        assert model.is_ready

    def test_predict_returns_valid_state(self, tmp_path):
        model_path = tmp_path / "model.json"
        model = WorkloadClassifierModel(model_path)
        X, y = _generate_training_data()
        model.train(X, y)

        snap = make_snapshot(processes=(
            make_process(name="gcc", cpu_percent=80.0),
        ), cpu_percent=85.0)
        pred = model.predict(snap, owner_uid=1000)
        assert pred is not None
        assert pred.predicted_state.value in {s.value for s in WorkloadState}
        assert 0 <= pred.confidence <= 1
        assert len(pred.class_probabilities) > 0

    def test_predict_without_model_returns_none(self, tmp_path):
        model_path = tmp_path / "model.json"
        model = WorkloadClassifierModel(model_path)
        assert not model.is_ready
        snap = make_snapshot()
        assert model.predict(snap, owner_uid=1000) is None

    def test_insufficient_data_returns_none(self, tmp_path):
        model_path = tmp_path / "model.json"
        model = WorkloadClassifierModel(model_path)
        X = np.random.randn(10, NUM_FEATURES)
        y = np.array(["NORMAL"] * 10)
        assert model.train(X, y) is None

    def test_single_class_returns_none(self, tmp_path):
        model_path = tmp_path / "model.json"
        model = WorkloadClassifierModel(model_path)
        X = np.random.randn(200, NUM_FEATURES)
        y = np.array(["NORMAL"] * 200)
        assert model.train(X, y) is None

    def test_model_persistence(self, tmp_path):
        model_path = tmp_path / "model.json"
        model1 = WorkloadClassifierModel(model_path)
        X, y = _generate_training_data()
        model1.train(X, y)
        v1 = model1.version

        # Load in a new instance
        model2 = WorkloadClassifierModel(model_path)
        assert model2.is_ready
        assert model2.version == v1

    def test_version_increments(self, tmp_path):
        model_path = tmp_path / "model.json"
        model = WorkloadClassifierModel(model_path)
        X, y = _generate_training_data()
        model.train(X, y)
        v1 = model.version
        model.train(X, y)
        assert model.version == v1 + 1

    def test_feature_importances_in_prediction(self, tmp_path):
        model_path = tmp_path / "model.json"
        model = WorkloadClassifierModel(model_path)
        X, y = _generate_training_data()
        model.train(X, y)
        snap = make_snapshot()
        pred = model.predict(snap, owner_uid=1000)
        assert pred is not None
        assert pred.feature_importances is not None
        assert len(pred.feature_importances) <= 5

    def test_training_metrics_contain_expected_fields(self, tmp_path):
        model_path = tmp_path / "model.json"
        model = WorkloadClassifierModel(model_path)
        X, y = _generate_training_data()
        metrics = model.train(X, y)
        assert metrics is not None
        assert metrics.accuracy > 0
        assert metrics.cv_accuracy >= 0
        assert metrics.n_samples > 0
        assert metrics.n_classes >= 2
        assert len(metrics.class_distribution) >= 2
        assert len(metrics.feature_importances) > 0
        assert metrics.training_time_seconds >= 0


# ── ML Workload Detector Integration Tests ───────────────────────────────────

class TestMLWorkloadDetector:

    def _make_detector(self, tmp_path, **kwargs):
        det = WorkloadDetector(owner_uid=1000, sustained_seconds=3,
                               release_seconds=5)
        defaults = dict(
            model_path=tmp_path / "model.json",
            training_data_path=tmp_path / "training.csv",
            owner_uid=1000,
            enable_training=True,
            enable_prediction=True,
            retrain_interval=0,       # Allow immediate retrain for testing
            retrain_min_samples=5,
        )
        defaults.update(kwargs)
        return MLWorkloadDetector(det, **defaults)

    def test_returns_enhanced_workload(self, tmp_path):
        ml_det = self._make_detector(tmp_path)
        snap = make_snapshot(now=0)
        result = ml_det.update(snap)
        assert isinstance(result, EnhancedWorkload)
        assert isinstance(result.state, WorkloadState)

    def test_deterministic_detector_always_runs(self, tmp_path):
        ml_det = self._make_detector(tmp_path)
        gcc = make_process(name="gcc", cpu_percent=80.0)
        snap0 = make_snapshot(now=0, processes=(gcc,))
        snap3 = make_snapshot(now=3, processes=(gcc,))
        ml_det.update(snap0)
        result = ml_det.update(snap3)
        # Deterministic detector should have activated after sustained period
        assert result.state == WorkloadState.COMPILATION

    def test_collects_training_data(self, tmp_path):
        ml_det = self._make_detector(tmp_path)
        gcc = make_process(name="gcc", cpu_percent=80.0)
        snap0 = make_snapshot(now=0, processes=(gcc,))
        snap3 = make_snapshot(now=3, processes=(gcc,))
        ml_det.update(snap0)
        ml_det.update(snap3)
        assert ml_det.collector.sample_count >= 1

    def test_prediction_without_model_is_none(self, tmp_path):
        ml_det = self._make_detector(tmp_path)
        snap = make_snapshot()
        result = ml_det.update(snap)
        assert result.ml_prediction is None

    def test_ml_prediction_after_training(self, tmp_path):
        ml_det = self._make_detector(tmp_path)

        # Pre-train the model with synthetic data
        X, y = _generate_training_data()
        ml_det.model.train(X, y)
        assert ml_det.model.is_ready

        snap = make_snapshot()
        result = ml_det.update(snap)
        assert result.ml_prediction is not None

    def test_deterministic_overrides_ml(self, tmp_path):
        """ML cannot change the deterministic result."""
        ml_det = self._make_detector(tmp_path)

        # Train model
        X, y = _generate_training_data()
        ml_det.model.train(X, y)

        # A single snapshot without sustained activity should stay NORMAL
        snap = make_snapshot(now=0)
        result = ml_det.update(snap)
        # Regardless of ML prediction, deterministic says NORMAL
        assert result.state == WorkloadState.NORMAL

    def test_confidence_preserved_on_agreement(self, tmp_path):
        ml_det = self._make_detector(tmp_path)

        # Train model heavily on COMPILATION
        X, y = _generate_training_data(n_per_class=100)
        ml_det.model.train(X, y)

        # Create a sustained COMPILATION workload
        gcc = make_process(name="gcc", cpu_percent=80.0)
        snap0 = make_snapshot(now=0, processes=(gcc,), cpu_percent=85.0)
        snap3 = make_snapshot(now=3, processes=(gcc,), cpu_percent=85.0)
        ml_det.update(snap0)
        result = ml_det.update(snap3)

        if result.ml_prediction and result.ml_prediction.predicted_state == result.state:
            # ML must preserve deterministic confidence
            assert result.confidence == 0.9

    def test_force_train(self, tmp_path):
        ml_det = self._make_detector(tmp_path)

        # Collect enough data
        for i in range(150):
            state = [WorkloadState.COMPILATION, WorkloadState.GAMING,
                     WorkloadState.DEVELOPMENT][i % 3]
            proc = make_process(name=["gcc", "dota2", "code"][i % 3],
                                cpu_percent=60.0 + i % 20)
            snap = make_snapshot(now=float(i), processes=(proc,))
            workload = make_workload(state=state)
            ml_det.collector.collect(snap, workload)

        assert ml_det.force_train()
        assert ml_det.model.is_ready

    def test_disabled_prediction(self, tmp_path):
        ml_det = self._make_detector(tmp_path, enable_prediction=False)
        X, y = _generate_training_data()
        ml_det.model.train(X, y)
        snap = make_snapshot()
        result = ml_det.update(snap)
        assert result.ml_prediction is None

    def test_disabled_training(self, tmp_path):
        ml_det = self._make_detector(tmp_path, enable_training=False)
        gcc = make_process(name="gcc", cpu_percent=80.0)
        snap0 = make_snapshot(now=0, processes=(gcc,))
        snap3 = make_snapshot(now=3, processes=(gcc,))
        ml_det.update(snap0)
        ml_det.update(snap3)
        assert ml_det.collector.sample_count == 0
