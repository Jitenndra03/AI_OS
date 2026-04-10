"""data_logger.py — Append system snapshots to a CSV file row by row.

This module is intentionally kept simple: it does one thing — write metrics to disk
so the AI trainer has data to learn from.
"""

import csv
import logging
import os

from utils.formatter import CSV_COLUMNS, snapshot_to_feature_row

logger = logging.getLogger(__name__)


def init_csv(path: str) -> None:
    """Create the CSV file with a header row if it does not already exist.

    Safe to call on every startup — will not overwrite existing data.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if not os.path.exists(path):
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_COLUMNS)
        logger.info("CSV initialised at %s", path)


def log_snapshot(path: str, snapshot: dict) -> None:
    """Append a single snapshot as a new CSV row.

    Args:
        path:     Absolute path to the metrics CSV file.
        snapshot: Dict returned by monitor.snapshot.collect_system_snapshot().
    """
    row = snapshot_to_feature_row(snapshot)

    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(row)


def row_count(path: str) -> int:
    """Return the number of *data* rows in the CSV (excluding the header).

    Returns 0 if the file does not exist yet.
    """
    if not os.path.exists(path):
        return 0

    with open(path, "r") as f:
        # Subtract 1 for the header row
        return max(0, sum(1 for _ in f) - 1)
