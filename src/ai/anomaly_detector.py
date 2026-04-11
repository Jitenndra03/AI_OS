"""anomaly_detector.py — IsolationForest-based anomaly detection for process metrics.

Hardened class-based implementation with thread safety and security checks.
"""

import os
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from config import settings
from utils.concurrency import metrics_lock, model_lock
from utils.logger import system_logger


class InsufficientDataError(Exception):
    """Raised when the CSV has fewer rows than MIN_TRAINING_SAMPLES."""


class AnomalyDetector:
    def __init__(self):
        self._model: IsolationForest | None = None
        self._rows_at_last_train: int = 0
        self.load_model()

    def _verify_model_safety(self, path: Path) -> bool:
        """Security: Verify file ownership and permissions to prevent model poisoning."""
        if not path.exists():
            return False
        
        # On Linux, check if the file is owned by the current user and not world-writable
        stat = path.stat()
        current_uid = os.getuid()
        
        if stat.st_uid != current_uid:
            system_logger.warning(f"Security: Model file {path} is owned by UID {stat.st_uid}, not current user {current_uid}!")
            return False
            
        # Check world-writable bit (0o002)
        if stat.st_mode & 0o002:
            system_logger.warning(f"Security: Model file {path} is world-writable! Refusing to load.")
            return False
            
        return True

    def _load_features(self, csv_path: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
        csv_path = csv_path or settings.METRICS_CSV
        
        with metrics_lock:
            if not csv_path.exists():
                raise FileNotFoundError(f"Metrics CSV not found: {csv_path}")
            df = pd.read_csv(csv_path)

        if len(df) < settings.MIN_TRAINING_SAMPLES:
            raise InsufficientDataError(
                f"Need {settings.MIN_TRAINING_SAMPLES} rows, have {len(df)}."
            )

        features = df[settings.FEATURE_COLUMNS].copy()
        meta = df[["pid", "name"]].copy()
        return features, meta

    def train(self, csv_path: Path | None = None) -> IsolationForest:
        features, _ = self._load_features(csv_path)

        model = IsolationForest(
            n_estimators=settings.N_ESTIMATORS,
            contamination=settings.ANOMALY_CONTAMINATION,
            random_state=settings.RANDOM_STATE,
        )
        model.fit(features)

        with model_lock:
            settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
            joblib.dump(model, settings.MODEL_PATH)
            # Set restrictive permissions immediately
            settings.MODEL_PATH.chmod(0o600)  # Owner read/write only

        self._model = model
        self._rows_at_last_train = len(features)
        system_logger.info(f"Model trained successfuly on {len(features)} rows.")
        return model

    def load_model(self) -> IsolationForest | None:
        with model_lock:
            if self._verify_model_safety(settings.MODEL_PATH):
                try:
                    self._model = joblib.load(settings.MODEL_PATH)
                    return self._model
                except Exception as e:
                    system_logger.error(f"Failed to load model: {e}")
        return None

    def _ensure_model(self, csv_path: Path | None = None) -> IsolationForest:
        csv_path = csv_path or settings.METRICS_CSV
        
        current_rows = self._count_csv_rows(csv_path)

        if self._model is None:
            self.load_model()

        if self._model is None:
            return self.train(csv_path)

        if current_rows - self._rows_at_last_train >= settings.RETRAIN_EVERY_N:
            system_logger.info(f"Retrain triggered: {current_rows} total rows.")
            return self.train(csv_path)

        return self._model

    def _count_csv_rows(self, csv_path: Path) -> int:
        with metrics_lock:
            if not csv_path.exists(): return 0
            with open(csv_path) as fh:
                return max(0, sum(1 for _ in fh) - 1)

    def detect(self, csv_path: Path | None = None) -> pd.DataFrame:
        csv_path = csv_path or settings.METRICS_CSV
        features, meta = self._load_features(csv_path)
        model = self._ensure_model(csv_path)

        labels = model.predict(features)
        scores = model.decision_function(features)

        results = meta.copy()
        for col in settings.FEATURE_COLUMNS:
            results[col] = features[col].values
        results["anomaly_score"] = np.round(scores, 6)
        results["label"] = labels

        with model_lock: # Reuse model lock for the score CSV as they are often paired
            settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
            results.to_csv(settings.ANOMALY_SCORES_CSV, index=False)

        return results

# Singleton instance for the pipeline to use
detector = AnomalyDetector()

if __name__ == "__main__":
    print("Testing hardened AnomalyDetector class...")
    try:
        res = detector.detect()
        print(f"Captured {len(res)} scores.")
    except Exception as e:
        print(f"Setup incomplete or error: {e}")
