"""data_logger.py — Appends per-process metric rows to a CSV file.

Thread-safe implementation with row-count limiting (circular buffer logic).
Ensures data names are sanitized to prevent CSV injection.
"""

import csv
import os
import time
import tempfile
from pathlib import Path

from src.config import settings
from src.utils.concurrency import metrics_lock
from src.utils.logger import system_logger


def sanitize_value(val: any) -> str:
    """Escape values that might trigger CSV injection (@, +, -, =)."""
    s = str(val)
    if s and s[0] in ("@", "+", "-", "="):
        return f"'{s}"
    return s


def init_csv(path: str | Path | None = None) -> Path:
    """Ensure the CSV file exists with the correct header."""
    path = Path(path) if path is not None else settings.METRICS_CSV
    
    with metrics_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.stat().st_size == 0:
            with open(path, "w", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(settings.CSV_COLUMNS)
    return path


def log_rows(
    rows: list[dict],
    path: str | Path | None = None,
) -> int:
    """Append a batch of process-metric dicts to the CSV."""
    path = Path(path) if path is not None else settings.METRICS_CSV
    init_csv(path)

    now = time.time()
    written = 0

    with metrics_lock:
        with open(path, "a", newline="") as fh:
            writer = csv.writer(fh)
            for row in rows:
                # Vulnerability Fix: Sanitize process name
                name = sanitize_value(row.get("name", "unknown"))
                
                writer.writerow(
                    [
                        now,
                        row.get("pid", ""),
                        name,
                        row.get("cpu_percent", 0.0),
                        row.get("memory_percent", 0.0),
                        row.get("num_threads", 0),
                        row.get("status", ""),
                    ]
                )
                written += 1
        
        # Performance/Review: Implement Circular Buffer
        _enforce_retention(path)

    return written


def _enforce_retention(path: Path):
    """Keep only the last MAX_METRICS_ROWS in the file."""
    try:
        current_count = row_count(path, use_lock=False)
        if current_count <= settings.MAX_METRICS_ROWS:
            return

        # Simple but effective for small/medium CSVs: Read all, slice, rewrite
        with open(path, "r", newline="") as fh:
            records = list(csv.reader(fh))
        # Names can legally contain newlines: retain CSV records, not lines.
        header, data = records[0], records[1:]
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", newline="", dir=path.parent,
                                             prefix=".metrics-", delete=False) as fh:
                temporary = Path(fh.name)
                writer = csv.writer(fh)
                writer.writerow(header)
                writer.writerows(data[-settings.MAX_METRICS_ROWS:])
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            
        system_logger.debug(f"Truncated {path.name} to {settings.MAX_METRICS_ROWS} rows.")
    except Exception as e:
        system_logger.error(f"Failed to enforce retention on {path}: {e}")


def row_count(path: str | Path | None = None, use_lock: bool = True) -> int:
    """Count data rows in the CSV (excludes the header)."""
    path = Path(path) if path is not None else settings.METRICS_CSV

    if not path.exists():
        return 0

    def _count():
        with open(path, newline="") as fh:
            return max(0, sum(1 for _ in csv.reader(fh)) - 1)

    if use_lock:
        with metrics_lock:
            return _count()
    return _count()


if __name__ == "__main__":
    from src.monitor.metrics_collector import collect_process_metrics
    print(f"Testing data retention (Limit: {settings.MAX_METRICS_ROWS})...")
    csv_path = init_csv()
    snapshot = collect_process_metrics()
    log_rows(snapshot, csv_path)
    print(f"Current rows: {row_count(csv_path)}")
