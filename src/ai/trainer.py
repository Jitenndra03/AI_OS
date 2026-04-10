"""trainer.py — Train, save, and load the Isolation Forest anomaly model.

IsolationForest is an unsupervised ML algorithm that learns what "normal"
looks like from your data, then flags any snapshot that looks different.
No labels are needed — the system learns purely from observed behaviour.
"""

import csv
import logging
import os
from typing import Optional

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

from utils.formatter import CSV_COLUMNS

logger = logging.getLogger(__name__)

# Columns used as model features (excludes timestamp which is index 0)
_FEATURE_COLUMNS = [c for c in CSV_COLUMNS if c != "timestamp"]
_FEATURE_INDICES = [CSV_COLUMNS.index(c) for c in _FEATURE_COLUMNS]


def _load_csv_features(csv_path: str) -> Optional[np.ndarray]:
    """Read numeric feature columns from the metrics CSV.

    Returns an (N, F) numpy array or None if the file is too small.
    """
    if not os.path.exists(csv_path):
        return None

    rows = []
    with open(csv_path, "r") as f:
        reader = csv.reader(f)
        next(reader, None)  # skip header
        for row in reader:
            if len(row) < len(CSV_COLUMNS):
                continue
            try:
                # Extract only the feature columns (drop timestamp)
                features = [float(row[i]) for i in _FEATURE_INDICES]
                rows.append(features)
            except ValueError:
                continue  # skip malformed rows

    if not rows:
        return None

    return np.array(rows)


def train_model(csv_path: str, contamination: float = 0.05) -> Optional[IsolationForest]:
    """Read the CSV and train a fresh IsolationForest model.

    Args:
        csv_path:      Path to the metrics CSV.
        contamination: Expected fraction of anomalous rows (0.0 – 0.5).

    Returns:
        A fitted IsolationForest, or None if there is not enough data.
    """
    X = _load_csv_features(csv_path)

    if X is None or len(X) == 0:
        logger.warning("Training skipped — no usable data in %s", csv_path)
        return None

    logger.info("Training IsolationForest on %d samples (%d features)…", *X.shape)

    model = IsolationForest(
        n_estimators=100,
        contamination=contamination,
        random_state=42,   # reproducible results
        n_jobs=-1,         # use all CPU cores for training
    )
    model.fit(X)

    logger.info("Training complete.")
    return model


def save_model(model: IsolationForest, path: str) -> None:
    """Persist the fitted model to disk using joblib."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    joblib.dump(model, path)
    logger.info("Model saved → %s", path)


def load_model(path: str) -> Optional[IsolationForest]:
    """Load a previously saved model from disk.

    Returns None (no exception) if the file does not exist yet.
    """
    if not os.path.exists(path):
        logger.debug("No saved model found at %s", path)
        return None

    model = joblib.load(path)
    logger.info("Model loaded ← %s", path)
    return model
