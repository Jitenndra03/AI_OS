"""test_ai_detector.py — Unit tests for the AI feature extraction and prediction logic.

These tests do NOT require a real trained model — they verify the feature extraction
shape and that the predict() function handles edge cases (None model, all-zero features).
"""

import numpy as np
import pytest

from ai.detector import extract_features, predict


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def normal_snapshot():
    """A representative snapshot for a lightly loaded system."""
    return {
        "timestamp": 1_700_000_000.0,
        "cpu_percent": 20.0,
        "load_1m": 0.5,
        "load_5m": 0.3,
        "load_15m": 0.2,
        "memory_percent": 45.0,
        "memory_used": 4 * 1024 ** 3,       # 4 GB in bytes
        "memory_available": 4 * 1024 ** 3,
        "swap_percent": 2.0,
        "disk_percent": 55.0,
        "disk_used": 200 * 1024 ** 3,       # 200 GB
        "disk_free": 100 * 1024 ** 3,
        "top_processes": [],
    }



# ── extract_features ──────────────────────────────────────────────────────────

def test_extract_features_returns_2d_array(normal_snapshot):
    """Feature vector must be (1, n_features) for model.predict()."""
    features = extract_features(normal_snapshot)
    assert isinstance(features, np.ndarray)
    assert features.ndim == 2
    assert features.shape[0] == 1  # single sample


def test_extract_features_has_correct_column_count(normal_snapshot):
    """Must match the number of feature columns the trainer uses (11 non-timestamp cols)."""
    features = extract_features(normal_snapshot)
    assert features.shape[1] == 11



def test_extract_features_values_are_finite(normal_snapshot):
    """No NaN or infinity values — would break model inference."""
    features = extract_features(normal_snapshot)
    assert np.all(np.isfinite(features))


def test_extract_features_cpu_matches_snapshot(normal_snapshot):
    """cpu_percent must be the first feature column."""
    features = extract_features(normal_snapshot)
    assert features[0][0] == pytest.approx(20.0)


# ── predict ───────────────────────────────────────────────────────────────────

def test_predict_returns_false_when_model_is_none(normal_snapshot):
    """Before the model is trained, predict() must always return False (safe default)."""
    result = predict(None, normal_snapshot)
    assert result is False


def test_predict_returns_bool_with_trained_model(normal_snapshot):
    """With a real trained model, predict() must return a plain Python bool."""
    from sklearn.ensemble import IsolationForest

    # Train a minimal model on synthetic 'normal' data (11 features)
    X_train = np.array([
        [20.0, 0.5, 0.3, 0.2, 45.0, 4096.0, 4096.0, 2.0, 55.0, 200.0, 100.0],
        [25.0, 0.6, 0.4, 0.3, 50.0, 4500.0, 3500.0, 3.0, 57.0, 205.0, 95.0],
        [18.0, 0.4, 0.2, 0.1, 42.0, 3800.0, 4200.0, 1.0, 54.0, 198.0, 102.0],
    ] * 15)  # repeat to meet IsolationForest minimum


    model = IsolationForest(n_estimators=10, random_state=42)
    model.fit(X_train)

    result = predict(model, normal_snapshot)
    assert isinstance(result, bool)
