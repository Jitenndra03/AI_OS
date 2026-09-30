"""Regression tests for advisory boundaries, safe persistence and training quality."""
import csv
import json
import os
import threading
from dataclasses import replace
from unittest.mock import Mock

import numpy as np
import pytest

from scripts.train_demo_model import make_demo_dataset
from src.ai.feature_engine import FEATURE_NAMES, NUM_FEATURES, extract_features
from src.ai.ml_workload_detector import MLWorkloadDetector
from src.ai.training_data import TrainingDataCollector
from src.ai.workload_model import MLPrediction, WorkloadClassifierModel, _probabilities
from src.optimization.models import ProcessMetrics, SystemSnapshot, Workload, WorkloadState
from src.optimization.workload import WorkloadDetector


def quiet(now=0):
    return SystemSnapshot(timestamp=now, monotonic=now, cpu_count=4,
                          cpu_percent=10, memory_percent=40, load_average=(.1,.1,.1))


def busy(now=0, name="worker"):
    p = ProcessMetrics(pid=30, create_time=1, uid=1000, name=name,
                       exe="/usr/bin/"+name, cpu_percent=80, num_threads=4)
    return replace(quiet(now), processes=(p,), cpu_percent=85)


def wrapper(tmp_path, **kwargs):
    return MLWorkloadDetector(WorkloadDetector(owner_uid=1000, sustained_seconds=3),
        model_path=tmp_path/"model.json", training_data_path=tmp_path/"training.csv",
        owner_uid=1000, **kwargs)


@pytest.fixture
def trained(tmp_path):
    path = tmp_path / "model.json"
    model = WorkloadClassifierModel(path, n_estimators=8)
    X, y = make_demo_dataset(samples_per_class=30)
    assert model.train(X, y) is not None
    return model, path, X, y


def test_normal_zero_confidence_samples_and_busy_warmup_does_not(tmp_path):
    detector = wrapper(tmp_path)
    detector.collector.normal_sample_rate = 1
    assert detector.update(quiet()).state == WorkloadState.NORMAL
    assert detector.collector.sample_count == 1
    assert detector.update(busy(1)).state == WorkloadState.NORMAL
    assert detector.collector.sample_count == 1
    detector.close()


def test_pending_custom_low_threshold_never_labeled_normal(tmp_path):
    detector = wrapper(tmp_path)
    detector.detector.cpu_threshold = 2
    detector.collector.normal_sample_rate = 1
    snapshot = replace(quiet(), processes=(replace(busy().processes[0], cpu_percent=3),))
    detector.update(snapshot)
    assert detector.collector.sample_count == 0
    detector.close()


def test_ingestion_counter_survives_cap_and_private_replacement(tmp_path):
    collector = TrainingDataCollector(tmp_path/"data.csv", owner_uid=1000, max_rows=3, normal_sample_rate=1)
    for i in range(8):
        assert collector.collect(quiet(i), Workload())
    assert collector.sample_count == 3
    assert collector.ingested_count == 8
    assert collector.path.stat().st_mode & 0o777 == 0o600
    assert len(collector.load()[1]) == 3


@pytest.mark.parametrize("fault", ["header", "label", "nan", "width"])
def test_invalid_csv_is_rejected(tmp_path, fault):
    collector = TrainingDataCollector(tmp_path/"data.csv", owner_uid=1000)
    columns = ["label", *FEATURE_NAMES]
    row = ["NORMAL", *([0.] * NUM_FEATURES)]
    if fault == "header": columns[1] = "wrong"
    if fault == "label": row[0] = "UNSAFE"
    if fault == "nan": row[2] = float("nan")
    if fault == "width": row.pop()
    with collector.path.open("w") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerow(row)
    collector.path.chmod(0o600)
    with pytest.raises(ValueError): collector.load()


def test_training_csv_symlink_rejected(tmp_path):
    target = tmp_path / "target"
    target.write_text("unchanged")
    path = tmp_path / "data.csv"
    path.symlink_to(target)
    collector = TrainingDataCollector(path, owner_uid=1000, normal_sample_rate=1)
    assert not collector.collect(quiet(), Workload())
    assert target.read_text() == "unchanged"


@pytest.mark.parametrize("state,protected,expected", [
    (WorkloadState.NORMAL, frozenset(), WorkloadState.NORMAL),
    (WorkloadState.HEAVY_UNKNOWN, frozenset(), WorkloadState.HEAVY_UNKNOWN),
    (WorkloadState.HEAVY_UNKNOWN, frozenset({30}), WorkloadState.AI_ML),
    (WorkloadState.GAMING, frozenset({30}), WorkloadState.GAMING),
])
def test_ml_only_refines_protected_unknown_label(tmp_path, state, protected, expected):
    detector = wrapper(tmp_path, enable_training=False)
    original = Workload(state, protected, "authoritative", .9, 12)
    detector.detector.update = Mock(return_value=original)
    detector._model = Mock(is_ready=True)
    detector._model.predict.return_value = MLPrediction(WorkloadState.AI_ML, .95, {"AI_ML":.95})
    result = detector.update(busy())
    assert result.state == expected
    assert (result.active, result.confidence, result.since, result.protected_pids, result.reason) == (
        original.active, original.confidence, original.since, original.protected_pids, original.reason)
    detector.close()


def test_ml_errors_do_not_break_deterministic_detection(tmp_path):
    detector = wrapper(tmp_path)
    detector._model = Mock(is_ready=True)
    detector._model.predict.side_effect = RuntimeError("model failed")
    detector._collector.collect = Mock(side_effect=ValueError("data failed"))
    detector.update(busy(0, "gcc"))
    result = detector.update(busy(3, "gcc"))
    assert result.state == WorkloadState.COMPILATION
    assert result.protected_pids == frozenset({30})
    detector.close()


def test_failed_training_has_backoff_and_close_joins(tmp_path, monkeypatch):
    detector = wrapper(tmp_path, retrain_interval=0, retrain_min_samples=1)
    detector.collector.normal_sample_rate = 1
    detector.collector.collect(quiet(), Workload())
    calls = []
    detector._train_once = lambda: calls.append(1)
    monkeypatch.setattr("src.ai.ml_workload_detector.time.monotonic", lambda: 100.)
    detector._maybe_retrain()
    detector._train_thread.join()
    detector.collector.collect(quiet(), Workload())
    detector._maybe_retrain()
    assert calls == [1]
    assert detector.close()
    assert not detector.force_train()
    detector.update(quiet())
    assert detector.collector.ingested_count == 2


def test_json_roundtrip_and_sklearn_prediction_equivalence(tmp_path, monkeypatch):
    from sklearn.ensemble import RandomForestClassifier
    captured = []
    original = RandomForestClassifier.fit
    def fit(self, X, y, **kwargs):
        captured.append(self)
        return original(self, X, y, **kwargs)
    monkeypatch.setattr(RandomForestClassifier, "fit", fit)
    X, y = make_demo_dataset(samples_per_class=30)
    model = WorkloadClassifierModel(tmp_path/"model.json", n_estimators=8)
    metrics = model.train(X,y)
    assert metrics.validation_samples == len(y)-int(len(y)*.8)
    assert captured[0].n_jobs == 1
    assert np.allclose([_probabilities(model._bundle, x) for x in X], captured[0].predict_proba(X))
    reloaded = WorkloadClassifierModel(model.model_path)
    assert reloaded.predict(busy(),1000) == model.predict(busy(),1000)
    assert model.model_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("fault", ["cycle", "feature", "probability", "classes", "schema", "nan", "quality"])
def test_invalid_json_forest_is_ignored(trained, fault):
    model, path, _, _ = trained
    data = json.loads(path.read_text())
    tree = data["trees"][0]
    if fault == "cycle": tree["left"][0] = 0
    if fault == "feature": tree["feature"][0] = NUM_FEATURES
    if fault == "probability": tree["probabilities"][0][0] = 20
    if fault == "classes": data["classes"][0] = "UNSAFE"
    if fault == "schema": data["feature_names"] = list(reversed(FEATURE_NAMES))
    if fault == "nan": tree["threshold"][0] = float("nan")
    if fault == "quality": data["metrics"]["cv_accuracy"] = 0
    path.write_text(json.dumps(data))
    assert not WorkloadClassifierModel(path).is_ready


def test_pickle_payload_is_never_executed(tmp_path):
    # A valid pickle whose reducer would create a file if deserialized.
    import pickle
    marker = tmp_path / "executed"
    class Payload:
        def __reduce__(self):
            return (os.system, (f"touch {marker}",))
    path = tmp_path / "legacy.pkl"
    path.write_bytes(pickle.dumps(Payload()))
    path.chmod(0o600)
    assert not WorkloadClassifierModel(path).is_ready
    assert not marker.exists()


def test_group_writable_and_symlink_models_ignored(trained, tmp_path):
    _, path, _, _ = trained
    path.chmod(0o620)
    assert not WorkloadClassifierModel(path).is_ready
    path.chmod(0o600)
    link = tmp_path/"linked.json"
    link.symlink_to(path)
    assert not WorkloadClassifierModel(link).is_ready


@pytest.mark.parametrize("fault", ["label", "nan", "shape", "single", "chronology"])
def test_invalid_training_not_promoted(trained, fault):
    model, _, X, y = trained
    version = model.version
    if fault == "label": y[0] = "INVALID"
    if fault == "nan": X[0,0] = np.nan
    if fault == "shape": X = X[:,:-1]
    if fault == "single": y[:] = "NORMAL"
    if fault == "chronology":
        order = np.argsort(y)
        X,y = X[order],y[order]
    assert model.train(X,y) is None
    assert model.version == version


@pytest.mark.parametrize("score", [0., float("nan"), .59])
def test_invalid_quality_never_promoted(trained, monkeypatch, score):
    from sklearn.ensemble import RandomForestClassifier
    model, path, X, y = trained
    previous = path.read_bytes()
    monkeypatch.setattr(RandomForestClassifier, "score", lambda *args, **kwargs: score)
    assert model.train(X,y) is None
    assert path.read_bytes() == previous


def test_close_joins_inflight_training_and_prevents_restart(tmp_path):
    detector = wrapper(tmp_path, retrain_min_samples=1)
    started, release = threading.Event(), threading.Event()
    def train():
        started.set()
        assert release.wait(2)
    detector._train_once = train
    detector.collector.normal_sample_rate = 1
    detector.collector.collect(quiet(), Workload())
    detector._maybe_retrain()
    assert started.wait(1)
    assert not detector.close(timeout=.01)
    release.set()
    assert detector.close(timeout=2)
    assert not detector._train_thread.is_alive()
    detector._maybe_retrain()
    assert not detector._train_thread.is_alive()


def test_failed_model_persistence_does_not_promote(trained, monkeypatch):
    model, path, X, y = trained
    previous = path.read_bytes()
    version = model.version
    monkeypatch.setattr(model, "_save_bundle", Mock(side_effect=OSError("disk full")))
    assert model.train(X, y) is None
    assert model.version == version
    assert path.read_bytes() == previous
