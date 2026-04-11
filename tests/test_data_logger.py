"""test_data_logger.py — Unit tests for the CSV data logging module."""

import csv
import os

import pytest

from ai.data_logger import init_csv, log_snapshot, row_count
from utils.formatter import CSV_COLUMNS


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_csv(tmp_path):
    """Return a path inside a pytest-managed temp directory."""
    return str(tmp_path / "test_metrics.csv")


@pytest.fixture
def sample_snapshot():
    return {
        "timestamp": 1_700_000_000.0,
        "cpu_percent": 30.0,
        "load_1m": 0.5,
        "load_5m": 0.4,
        "load_15m": 0.3,
        "memory_percent": 60.0,
        "memory_used": 6 * 1024 ** 3,
        "memory_available": 2 * 1024 ** 3,
        "swap_percent": 5.0,
        "disk_percent": 70.0,
        "disk_used": 300 * 1024 ** 3,
        "disk_free": 100 * 1024 ** 3,
        "top_processes": [],
    }



# ── init_csv ──────────────────────────────────────────────────────────────────

def test_init_csv_creates_file(tmp_csv):
    init_csv(tmp_csv)
    assert os.path.exists(tmp_csv)


def test_init_csv_writes_correct_header(tmp_csv):
    init_csv(tmp_csv)
    with open(tmp_csv) as f:
        reader = csv.reader(f)
        header = next(reader)
    assert header == CSV_COLUMNS


def test_init_csv_does_not_overwrite_existing_file(tmp_csv, sample_snapshot):
    """Calling init_csv twice must not erase existing data rows."""
    init_csv(tmp_csv)
    log_snapshot(tmp_csv, sample_snapshot)
    init_csv(tmp_csv)  # second call — should be a no-op
    assert row_count(tmp_csv) == 1


# ── log_snapshot ──────────────────────────────────────────────────────────────

def test_log_snapshot_appends_row(tmp_csv, sample_snapshot):
    init_csv(tmp_csv)
    log_snapshot(tmp_csv, sample_snapshot)
    assert row_count(tmp_csv) == 1


def test_log_snapshot_multiple_rows(tmp_csv, sample_snapshot):
    init_csv(tmp_csv)
    for _ in range(5):
        log_snapshot(tmp_csv, sample_snapshot)
    assert row_count(tmp_csv) == 5


def test_log_snapshot_correct_column_count(tmp_csv, sample_snapshot):
    init_csv(tmp_csv)
    log_snapshot(tmp_csv, sample_snapshot)
    with open(tmp_csv) as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        row = next(reader)
    assert len(row) == len(CSV_COLUMNS)


# ── row_count ─────────────────────────────────────────────────────────────────

def test_row_count_returns_zero_for_missing_file(tmp_path):
    missing = str(tmp_path / "nonexistent.csv")
    assert row_count(missing) == 0


def test_row_count_excludes_header(tmp_csv, sample_snapshot):
    init_csv(tmp_csv)
    assert row_count(tmp_csv) == 0  # header only
    log_snapshot(tmp_csv, sample_snapshot)
    assert row_count(tmp_csv) == 1
