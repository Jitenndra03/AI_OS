"""Private, bounded, strictly validated chronological detector-label samples."""
from __future__ import annotations
import csv
import math
import os
import stat
import tempfile
import threading
from pathlib import Path

import numpy as np
from src.ai.feature_engine import FEATURE_NAMES, NUM_FEATURES, extract_features
from src.optimization.models import SystemSnapshot, Workload, WorkloadState
from src.utils.logger import system_logger


class TrainingDataCollector:
    def __init__(self, path: str | Path, *, owner_uid: int, max_rows: int = 50_000,
                 normal_sample_rate: float = 0.1) -> None:
        self.path, self.owner_uid = Path(path), owner_uid
        self.max_rows = min(50_000, max(1, max_rows))
        self.normal_sample_rate = max(0.01, min(1.0, normal_sample_rate))
        self._lock = threading.Lock()
        self._row_count: int | None = None
        self._normal_counter = 0
        self._ingested_count = 0
        self._columns = ["label", *FEATURE_NAMES]

    @staticmethod
    def _quiet(snapshot: SystemSnapshot) -> bool:
        """Conservative idle evidence; NORMAL during sustained warmup is excluded."""
        values = (snapshot.cpu_percent, snapshot.memory_percent, snapshot.cpu_pressure,
                  snapshot.memory_pressure, snapshot.io_pressure, *snapshot.load_average)
        if any(not math.isfinite(v) or v < 0 for v in values):
            return False
        return (snapshot.cpu_percent < 25 and snapshot.memory_percent < 80
                and max(snapshot.cpu_pressure, snapshot.memory_pressure, snapshot.io_pressure) < 5
                and snapshot.load_average[0] < max(1, snapshot.cpu_count) * .5
                and all(math.isfinite(p.cpu_percent) and 0 <= p.cpu_percent < 10
                        and math.isfinite(p.io_bytes_per_sec) and 0 <= p.io_bytes_per_sec < 1024 * 1024
                        for p in snapshot.processes))

    def _open(self, flags: int):
        fd = os.open(self.path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError("Training file must be regular, owned, and private (0600)")
            if info.st_size > 64 * 1024 * 1024:
                raise ValueError("Training file exceeds size limit")
            return fd
        except Exception:
            os.close(fd)
            raise

    def _rows(self) -> list[list[str]]:
        with os.fdopen(self._open(os.O_RDONLY), "r", newline="") as stream:
            reader = csv.reader(stream)
            if next(reader, None) != self._columns:
                raise ValueError("Training feature schema mismatch")
            rows = []
            states = {s.value for s in WorkloadState}
            for row in reader:
                if len(rows) >= 50_001 or len(row) != NUM_FEATURES + 1 or row[0] not in states:
                    raise ValueError("Invalid training row")
                values = [float(v) for v in row[1:]]
                if not all(math.isfinite(v) and abs(v) <= np.finfo(np.float32).max for v in values):
                    raise ValueError("Nonfinite or out-of-range training feature")
                rows.append(row)
            return rows

    def collect(self, snapshot: SystemSnapshot, workload: Workload, *, normal_confirmed: bool = True) -> bool:
        if not snapshot.complete or not isinstance(workload.state, WorkloadState):
            return False
        if workload.state == WorkloadState.NORMAL:
            if not normal_confirmed or not self._quiet(snapshot):
                return False
        elif not math.isfinite(workload.confidence) or workload.confidence < .7:
            return False
        features = extract_features(snapshot, self.owner_uid)
        with self._lock:
            if workload.state == WorkloadState.NORMAL:
                self._normal_counter += 1
                if self._normal_counter % max(1, round(1 / self.normal_sample_rate)):
                    return False
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if self._row_count is None:
                    self._row_count = len(self._rows()) if self.path.exists() else 0
                fd = self._open(os.O_WRONLY | os.O_APPEND | os.O_CREAT)
                with os.fdopen(fd, "a", newline="") as stream:
                    writer = csv.writer(stream)
                    if stream.tell() == 0:
                        writer.writerow(self._columns)
                    writer.writerow([workload.state.value, *features.values.tolist()])
                self._row_count += 1
                self._ingested_count += 1
                if self._row_count > self.max_rows:
                    rows = self._rows()[-self.max_rows:]
                    fd, temporary = tempfile.mkstemp(prefix=".ml-data-", dir=self.path.parent)
                    try:
                        with os.fdopen(fd, "w", newline="") as stream:
                            writer = csv.writer(stream)
                            writer.writerow(self._columns)
                            writer.writerows(rows)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, self.path)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
                    self._row_count = len(rows)
                return True
            except (OSError, ValueError) as exc:
                system_logger.warning("Training sample skipped: %s", exc)
                return False

    @property
    def sample_count(self) -> int:
        with self._lock:
            if self._row_count is None:
                self._row_count = len(self._rows()) if self.path.exists() else 0
            return self._row_count

    @property
    def ingested_count(self) -> int:
        """Successful appends this process lifetime, unaffected by retention cap."""
        with self._lock:
            return self._ingested_count

    def load(self) -> tuple[np.ndarray, np.ndarray]:
        with self._lock:
            rows = self._rows()
            if not rows:
                raise ValueError("No training samples")
            return np.array([row[1:] for row in rows], dtype=np.float64), np.array([row[0] for row in rows])
