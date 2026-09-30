"""Legacy anomaly models are offline analysis, never control authority."""
import importlib
import numpy as np
import pandas as pd
import pytest
from src.config import settings

@pytest.fixture
def offline_model(monkeypatch, tmp_path):
    # Patch paths BEFORE import because importing creates a model singleton.
    for name, value in {"DATA_DIR": tmp_path, "MODEL_PATH": tmp_path / "model.pkl",
                        "METRICS_CSV": tmp_path / "metrics.csv",
                        "ANOMALY_SCORES_CSV": tmp_path / "scores.csv",
                        "MIN_TRAINING_SAMPLES": 10, "N_ESTIMATORS": 8}.items():
        monkeypatch.setattr(settings, name, value)
    module = importlib.import_module("src.ai.anomaly_detector")
    return module, module.AnomalyDetector()

def training_rows():
    return pd.DataFrame([dict(pid=100+i, name="offline-only", cpu_percent=10+i%5,
                              memory_percent=2+i%3, num_threads=1+i%2) for i in range(20)])

def test_missing_and_insufficient_training_data_fail_safely(offline_model):
    module, model = offline_model
    assert model._model is None
    with pytest.raises(FileNotFoundError):
        model.detect()
    training_rows().head(2).to_csv(settings.METRICS_CSV, index=False)
    with pytest.raises(module.InsufficientDataError):
        model.train()
    assert not settings.MODEL_PATH.exists()

def test_real_feature_training_and_scoring_stay_in_temporary_paths(offline_model):
    _, model = offline_model
    frame = training_rows()
    frame.to_csv(settings.METRICS_CSV, index=False)
    features, metadata = model._load_features()
    assert list(features.columns) == settings.FEATURE_COLUMNS
    assert list(metadata.columns) == ["pid", "name"]
    assert features.shape == (20, 3)
    assert model.train().n_features_in_ == 3
    assert settings.MODEL_PATH.stat().st_mode & 0o777 == 0o600
    results = model.detect()
    assert len(results) == 20
    assert np.isfinite(results["anomaly_score"]).all()
    assert set(results["label"]) <= {-1, 1}
    assert list(results["pid"]) == list(frame["pid"])
    assert settings.ANOMALY_SCORES_CSV.exists()

def test_world_writable_model_is_not_deserialized(offline_model, monkeypatch):
    module, model = offline_model
    settings.MODEL_PATH.write_bytes(b"not a pickle")
    settings.MODEL_PATH.chmod(0o666)
    monkeypatch.setattr(module.joblib, "load", lambda *_: pytest.fail("Unsafe pickle loaded"))
    assert model.load_model() is None

def test_corrupt_private_model_fails_without_activating_control(offline_model):
    _, model = offline_model
    settings.MODEL_PATH.write_bytes(b"not a valid model")
    settings.MODEL_PATH.chmod(0o600)
    assert model.load_model() is None
    assert model._model is None
