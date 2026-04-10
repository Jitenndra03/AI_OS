"""detector.py — Run inference on a live system snapshot.

The IsolationForest model assigns an anomaly score to every snapshot.
Scores below zero are labelled as anomalous (IsolationForest convention:
predict() returns -1 for anomaly, +1 for normal).
"""

import logging
from typing import Optional

import numpy as np
from sklearn.ensemble import IsolationForest

from utils.formatter import CSV_COLUMNS, bytes_to_gb, bytes_to_mb

logger = logging.getLogger(__name__)

# The feature columns the model was trained on (timestamp excluded)
_FEATURE_COLUMNS = [c for c in CSV_COLUMNS if c != "timestamp"]


def extract_features(snapshot: dict) -> np.ndarray:
    """Convert a snapshot dict into the numeric feature vector the model expects.

    The order MUST match _FEATURE_COLUMNS / what the trainer used.

    Returns:
        numpy array of shape (1, n_features) — ready for model.predict().
    """
    features = [
        snapshot["cpu_percent"],
        snapshot["load_1m"],
        snapshot["load_5m"],
        snapshot["load_15m"],
        snapshot["memory_percent"],
        bytes_to_mb(snapshot["memory_used"]),
        bytes_to_mb(snapshot["memory_available"]),
        snapshot["swap_percent"],
        snapshot["disk_percent"],
        bytes_to_gb(snapshot["disk_used"]),
        bytes_to_gb(snapshot["disk_free"]),
    ]
    return np.array(features).reshape(1, -1)



def predict(model: Optional[IsolationForest], snapshot: dict) -> bool:
    """Predict whether a snapshot represents an anomalous system state.

    Args:
        model:    A fitted IsolationForest (or None if not yet trained).
        snapshot: Dict from monitor.snapshot.collect_system_snapshot().

    Returns:
        True  → anomaly detected.
        False → normal behaviour, or model not ready yet.
    """
    if model is None:
        return False

    features = extract_features(snapshot)
    result = model.predict(features)[0]  # +1 = normal, -1 = anomaly

    is_anomaly = bool(result == -1)

    if is_anomaly:
        score = model.score_samples(features)[0]
        logger.warning(
            "⚠ Anomaly detected | score=%.4f | cpu=%.1f%% mem=%.1f%% disk=%.1f%%",
            score,
            snapshot["cpu_percent"],
            snapshot["memory_percent"],
            snapshot["disk_percent"],
        )
    else:
        logger.debug(
            "Normal | cpu=%.1f%% mem=%.1f%% disk=%.1f%%",
            snapshot["cpu_percent"],
            snapshot["memory_percent"],
            snapshot["disk_percent"],
        )

    return is_anomaly
