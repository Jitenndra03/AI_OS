"""Advisory ML recognition; deterministic detector alone authorizes active work."""
from __future__ import annotations
import math
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from src.ai.training_data import TrainingDataCollector
from src.ai.workload_model import MLPrediction, WorkloadClassifierModel
from src.optimization.models import SystemSnapshot, Workload, WorkloadState
from src.optimization.workload import WorkloadDetector
from src.utils.logger import system_logger


@dataclass(frozen=True)
class EnhancedWorkload(Workload):
    ml_prediction: MLPrediction | None = None
    ml_early_hint: bool = False
    ml_refined: bool = False


class MLWorkloadDetector:
    def __init__(self, detector: WorkloadDetector, *, model_path: str | Path,
                 training_data_path: str | Path, owner_uid: int | None = None,
                 retrain_interval: float = 300, retrain_min_samples: int = 50,
                 enable_training: bool = True, enable_prediction: bool = True) -> None:
        if not math.isfinite(retrain_interval) or retrain_interval < 0 or retrain_min_samples < 1:
            raise ValueError("Invalid training schedule")
        self.detector = detector
        self.owner_uid = os.getuid() if owner_uid is None else owner_uid
        self.retrain_interval, self.retrain_min_samples = retrain_interval, retrain_min_samples
        self.enable_training, self.enable_prediction = enable_training, enable_prediction
        self._model = WorkloadClassifierModel(model_path)
        self._collector = TrainingDataCollector(training_data_path, owner_uid=self.owner_uid)
        self._last_train_time = float("-inf")
        self._samples_at_last_train = 0
        self._train_thread: threading.Thread | None = None
        self._closed = threading.Event()
        self._lifecycle_lock = threading.Lock()

    @property
    def model(self) -> WorkloadClassifierModel:
        return self._model

    @property
    def collector(self) -> TrainingDataCollector:
        return self._collector

    def update(self, snapshot: SystemSnapshot) -> EnhancedWorkload:
        deterministic = self.detector.update(snapshot)
        prediction = None
        state = deterministic.state
        refined = False
        if not self._closed.is_set():
            try:
                if self.enable_prediction and snapshot.complete and self._model.is_ready:
                    prediction = self._model.predict(snapshot, self.owner_uid)
                if (prediction is not None and snapshot.complete
                        and deterministic.state == WorkloadState.HEAVY_UNKNOWN
                        and deterministic.protected_pids and deterministic.confidence >= .7
                        and isinstance(prediction.predicted_state, WorkloadState)
                        and prediction.predicted_state not in {WorkloadState.NORMAL, WorkloadState.HEAVY_UNKNOWN}
                        and math.isfinite(prediction.confidence) and .85 <= prediction.confidence <= 1):
                    state, refined = prediction.predicted_state, True
            except Exception as exc:
                system_logger.warning("ML advisory prediction skipped: %s", exc)
                prediction = None
            try:
                if self.enable_training and snapshot.complete:
                    # Pending candidates use detector thresholds, including custom
                    # thresholds, so warmup activity never becomes a NORMAL label.
                    pending = bool(getattr(self.detector, "_pending", {}))
                    self._collector.collect(snapshot, deterministic, normal_confirmed=not pending)
                    self._maybe_retrain()
            except Exception as exc:
                system_logger.warning("ML collection skipped: %s", exc)
        return EnhancedWorkload(state=state, protected_pids=deterministic.protected_pids,
            reason=deterministic.reason, confidence=deterministic.confidence, since=deterministic.since,
            ml_prediction=prediction, ml_refined=refined)

    def _maybe_retrain(self) -> None:
        with self._lifecycle_lock:
            if self._closed.is_set() or (self._train_thread and self._train_thread.is_alive()):
                return
            now = time.monotonic()
            # A failed attempt is still an attempt: at least 30s retry backoff.
            if now - self._last_train_time < max(30.0, self.retrain_interval):
                return
            if self._collector.ingested_count - self._samples_at_last_train < self.retrain_min_samples:
                return
            self._last_train_time = now
            self._samples_at_last_train = self._collector.ingested_count
            self._train_thread = threading.Thread(target=self._retrain_background,
                daemon=True, name="ai-os-ml-retrain")
            self._train_thread.start()

    def _retrain_background(self) -> None:
        self._train_once()

    def _train_once(self) -> bool:
        try:
            if self._closed.is_set():
                return False
            X, y = self._collector.load()
            return self._model.train(X, y) is not None
        except Exception as exc:
            system_logger.warning("ML training skipped: %s", exc)
            return False

    def force_train(self) -> bool:
        with self._lifecycle_lock:
            if self._closed.is_set():
                return False
            self._last_train_time = time.monotonic()
            self._samples_at_last_train = self._collector.ingested_count
            return self._train_once()

    def close(self, timeout: float | None = None) -> bool:
        """Stop new collection/training and join an in-flight bounded training job."""
        with self._lifecycle_lock:
            self._closed.set()
            thread = self._train_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        return thread is None or not thread.is_alive()
