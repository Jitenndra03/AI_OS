"""Bounded RandomForest training and data-only JSON inference.

Legacy pickle/joblib files are deliberately never deserialized. Evaluation uses
an untouched chronological final fifth; scores describe detector-label agreement,
not independently measured real-world classification accuracy.
"""
from __future__ import annotations

import json
import math
import os
import stat
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from src.ai.feature_engine import FEATURE_NAMES, NUM_FEATURES, extract_features
from src.optimization.models import SystemSnapshot, WorkloadState
from src.utils.logger import system_logger

_MIN_SAMPLES_PER_CLASS = 20
_MIN_TOTAL_SAMPLES = 100
_MIN_CV_ACCURACY = 0.60
_MAX_FILE_BYTES = 16 * 1024 * 1024
_MAX_TREES = 150
_MAX_NODES = 8191
_FORMAT = "ai-os-forest-v1"


@dataclass(frozen=True)
class MLPrediction:
    predicted_state: WorkloadState
    confidence: float
    class_probabilities: dict[str, float]
    model_version: int = 0
    feature_importances: dict[str, float] | None = None


@dataclass(frozen=True)
class ModelMetrics:
    accuracy: float
    cv_accuracy: float  # Compatibility name: chronological holdout accuracy.
    n_samples: int
    n_classes: int
    class_distribution: dict[str, int]
    feature_importances: dict[str, float]
    training_time_seconds: float
    model_version: int
    validation_method: str = "chronological_holdout_20_percent"
    validation_samples: int = 0


def _finite(value: object) -> bool:
    return type(value) in (float, int) and math.isfinite(value)


def _validate_bundle(bundle: dict) -> dict:
    """Reject malformed graphs, unsupported features and oversized models."""
    if not isinstance(bundle, dict) or set(bundle) != {
        "format", "feature_names", "classes", "version", "trees", "importances", "metrics"
    }:
        raise ValueError("Invalid forest schema")
    if bundle["format"] != _FORMAT or bundle["feature_names"] != FEATURE_NAMES:
        raise ValueError("Incompatible feature schema")
    classes = bundle["classes"]
    valid = {state.value for state in WorkloadState}
    if (not isinstance(classes, list) or not 2 <= len(classes) <= len(valid)
            or any(type(c) is not str or c not in valid for c in classes)
            or len(set(classes)) != len(classes)):
        raise ValueError("Invalid classes")
    if type(bundle["version"]) is not int or not 1 <= bundle["version"] <= 2**31:
        raise ValueError("Invalid version")
    importances = bundle["importances"]
    if (not isinstance(importances, list) or len(importances) != NUM_FEATURES
            or any(not _finite(v) or not 0 <= v <= 1 for v in importances)):
        raise ValueError("Invalid importances")
    trees = bundle["trees"]
    if not isinstance(trees, list) or not 1 <= len(trees) <= _MAX_TREES:
        raise ValueError("Invalid forest size")
    total_nodes = 0
    for tree in trees:
        if not isinstance(tree, dict) or set(tree) != {"left", "right", "feature", "threshold", "probabilities"}:
            raise ValueError("Invalid tree schema")
        if any(not isinstance(a, list) for a in tree.values()):
            raise ValueError("Invalid tree arrays")
        n = len(tree["left"])
        total_nodes += n
        if not 1 <= n <= _MAX_NODES or total_nodes > 200_000 or any(len(a) != n for a in tree.values()):
            raise ValueError("Invalid tree size")
        parents = [0] * n
        for i in range(n):
            left, right, feature = tree["left"][i], tree["right"][i], tree["feature"][i]
            if any(type(v) is not int for v in (left, right, feature)):
                raise ValueError("Invalid node index")
            if not _finite(tree["threshold"][i]):
                raise ValueError("Invalid threshold")
            probs = tree["probabilities"][i]
            if (not isinstance(probs, list) or len(probs) != len(classes)
                    or any(not _finite(p) or not 0 <= p <= 1 for p in probs)
                    or not math.isclose(sum(probs), 1.0, abs_tol=1e-8)):
                raise ValueError("Invalid probabilities")
            if left == right == -1:
                if feature != -2:
                    raise ValueError("Invalid leaf")
            elif i < left < n and i < right < n and left != right and 0 <= feature < NUM_FEATURES:
                parents[left] += 1
                parents[right] += 1
            else:
                raise ValueError("Invalid or cyclic tree")
        if parents[0] != 0 or any(p != 1 for p in parents[1:]):
            raise ValueError("Disconnected or shared tree nodes")
        depths = [0] * n
        for i in range(n):
            if depths[i] > 12:
                raise ValueError("Tree too deep")
            if tree["left"][i] >= 0:
                depths[tree["left"][i]] = depths[tree["right"][i]] = depths[i] + 1
    metrics = bundle["metrics"]
    if not isinstance(metrics, dict):
        raise ValueError("Invalid metrics")
    parsed = ModelMetrics(**metrics)
    if (any(not _finite(v) or not 0 <= v <= 1 for v in (parsed.accuracy, parsed.cv_accuracy))
            or parsed.cv_accuracy < _MIN_CV_ACCURACY
            or parsed.validation_method != "chronological_holdout_20_percent"
            or type(parsed.validation_samples) is not int
            or not 1 <= parsed.validation_samples < parsed.n_samples
            or parsed.model_version != bundle["version"]
            or type(parsed.n_samples) is not int or not _MIN_TOTAL_SAMPLES <= parsed.n_samples <= 50_000
            or parsed.n_classes != len(classes)
            or not _finite(parsed.training_time_seconds) or parsed.training_time_seconds < 0
            or not isinstance(parsed.class_distribution, dict)
            or set(parsed.class_distribution) != set(classes)
            or any(type(v) is not int or v < _MIN_SAMPLES_PER_CLASS for v in parsed.class_distribution.values())
            or sum(parsed.class_distribution.values()) != parsed.n_samples
            or not isinstance(parsed.feature_importances, dict)
            or any(k not in FEATURE_NAMES or not _finite(v) or not 0 <= v <= 1
                   for k, v in parsed.feature_importances.items())):
        raise ValueError("Invalid quality metadata")
    return bundle


def _probabilities(bundle: dict, values: np.ndarray) -> np.ndarray:
    # sklearn trees compare float32 inputs. Match that rounding after export.
    values = np.asarray(values, dtype=np.float32)
    result = np.zeros(len(bundle["classes"]))
    for tree in bundle["trees"]:
        node = 0
        while tree["left"][node] != -1:
            branch = "left" if values[tree["feature"][node]] <= tree["threshold"][node] else "right"
            node = tree[branch][node]
        result += tree["probabilities"][node]
    return result / len(bundle["trees"])


class WorkloadClassifierModel:
    def __init__(self, model_path: str | Path, *, n_estimators: int = 80,
                 max_depth: int = 10, random_state: int = 42) -> None:
        if not 1 <= n_estimators <= _MAX_TREES or not 1 <= max_depth <= 12:
            raise ValueError("Training limits exceeded")
        self.model_path = Path(model_path)
        self.n_estimators, self.max_depth, self.random_state = n_estimators, max_depth, random_state
        self._bundle: dict | None = None
        self._metrics: ModelMetrics | None = None
        self._version = 0
        self._train_lock = threading.Lock()
        self._predict_lock = threading.RLock()
        self._load_model()

    @property
    def is_ready(self) -> bool:
        return self._bundle is not None

    @property
    def version(self) -> int:
        return self._version

    @property
    def metrics(self) -> ModelMetrics | None:
        return self._metrics

    def _load_model(self) -> bool:
        try:
            fd = os.open(self.model_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_FILE_BYTES
                        or info.st_uid != os.geteuid() or info.st_mode & 0o022):
                    raise ValueError("Model must be bounded, regular, owned and not group/world writable")
                raw = stream.read(_MAX_FILE_BYTES + 1)
                if len(raw.encode("utf-8")) > _MAX_FILE_BYTES:
                    raise ValueError("Model too large")
                bundle = _validate_bundle(json.loads(raw))
            with self._predict_lock:
                self._bundle = bundle
                self._version = bundle["version"]
                self._metrics = ModelMetrics(**bundle["metrics"])
            return True
        except FileNotFoundError:
            return False
        except Exception as exc:
            system_logger.warning("ML model ignored: %s", exc)
            return False

    def _save_bundle(self, bundle: dict) -> None:
        data = json.dumps(bundle, allow_nan=False, separators=(",", ":")).encode()
        if len(data) > _MAX_FILE_BYTES:
            raise ValueError("Export exceeds model size limit")
        self.model_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".ml-model-", dir=self.model_path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.model_path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def train(self, X: np.ndarray, y: np.ndarray) -> ModelMetrics | None:
        """Train on first 80%, evaluate final 20%, promote only adequate models.

        Callers must preserve capture order. No preprocessing sees the holdout.
        The deployed forest remains trained only on the training partition.
        """
        if not self._train_lock.acquire(blocking=False):
            return None
        try:
            start = time.monotonic()
            X, y = np.asarray(X, dtype=np.float64), np.asarray(y)
            if (X.ndim != 2 or X.shape[1] != NUM_FEATURES or y.ndim != 1
                    or len(y) != len(X) or not _MIN_TOTAL_SAMPLES <= len(X) <= 50_000
                    or not np.isfinite(X).all() or np.abs(X).max() > np.finfo(np.float32).max
                    or any(not isinstance(v, (str, np.str_)) or v not in {s.value for s in WorkloadState} for v in y)):
                return None
            classes, counts = np.unique(y, return_counts=True)
            if len(classes) < 2 or min(counts) < _MIN_SAMPLES_PER_CLASS:
                return None
            split = int(len(y) * 0.8)
            if set(y[:split]) != set(classes) or set(y[split:]) != set(classes):
                return None
            from sklearn.ensemble import RandomForestClassifier
            forest = RandomForestClassifier(n_estimators=self.n_estimators, max_depth=self.max_depth,
                random_state=self.random_state, class_weight="balanced", min_samples_leaf=3,
                min_samples_split=6, n_jobs=1)
            forest.fit(X[:split], y[:split])
            score = float(forest.score(X[split:], y[split:]))
            if not math.isfinite(score) or score < _MIN_CV_ACCURACY:
                return None
            train_score = float(forest.score(X[:split], y[:split]))
            if not math.isfinite(train_score):
                return None
            importances = dict(zip(FEATURE_NAMES, forest.feature_importances_.tolist()))
            version = self._version + 1
            metrics = ModelMetrics(train_score, score, len(y), len(classes),
                dict(zip(classes.tolist(), counts.tolist())), importances,
                round(time.monotonic() - start, 3), version, validation_samples=len(y)-split)
            trees = []
            for estimator in forest.estimators_:
                tree = estimator.tree_
                values = tree.value[:, 0, :]
                probs = values / values.sum(axis=1, keepdims=True)
                trees.append(dict(left=tree.children_left.tolist(), right=tree.children_right.tolist(),
                    feature=tree.feature.tolist(), threshold=tree.threshold.tolist(), probabilities=probs.tolist()))
            bundle = _validate_bundle(dict(format=_FORMAT, feature_names=FEATURE_NAMES,
                classes=forest.classes_.tolist(), version=version, trees=trees,
                importances=forest.feature_importances_.tolist(), metrics=asdict(metrics)))
            self._save_bundle(bundle)
            with self._predict_lock:
                self._bundle, self._version, self._metrics = bundle, version, metrics
            return metrics
        except Exception as exc:
            system_logger.warning("ML training rejected: %s", exc)
            return None
        finally:
            self._train_lock.release()

    def predict(self, snapshot: SystemSnapshot, owner_uid: int) -> MLPrediction | None:
        with self._predict_lock:
            bundle = self._bundle
        if bundle is None or not snapshot.complete:
            return None
        try:
            probs = _probabilities(bundle, extract_features(snapshot, owner_uid).values)
            index = int(np.argmax(probs))
            top = dict(sorted(zip(FEATURE_NAMES, bundle["importances"]), key=lambda item: item[1], reverse=True)[:5])
            return MLPrediction(WorkloadState(bundle["classes"][index]), float(probs[index]),
                dict(zip(bundle["classes"], probs.tolist())), bundle["version"], top)
        except Exception as exc:
            system_logger.warning("ML prediction skipped: %s", exc)
            return None

    def predict_batch(self, snapshots: list[SystemSnapshot], owner_uid: int) -> list[MLPrediction | None]:
        return [self.predict(snapshot, owner_uid) for snapshot in snapshots]
