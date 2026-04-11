"""test_system_flow.py — Integration tests for the full AI pipeline.

Tests the end-to-end flow:
    monitor.snapshot → ai.data_logger → ai.detector

Uses monkeypatching to keep the tests deterministic (no real psutil calls).
"""

import os

import pytest

from monitor.snapshot import collect_system_snapshot
from ai import data_logger, detector


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_csv(tmp_path):
    return str(tmp_path / "metrics.csv")


@pytest.fixture(autouse=True)
def patch_monitor(monkeypatch):
    """Replace live psutil and os calls with fixed, deterministic values."""
    monkeypatch.setattr("monitor.snapshot.get_cpu_usage", lambda interval=1: 35.0)
    monkeypatch.setattr("os.getloadavg", lambda: (0.5, 0.4, 0.3))
    monkeypatch.setattr(
        "monitor.snapshot.get_memory_usage",
        lambda: {"total": 8 * 1024 ** 3, "available": 4 * 1024 ** 3,
                 "used": 4 * 1024 ** 3, "percent": 50.0},
    )
    monkeypatch.setattr(
        "monitor.snapshot.get_swap_usage",
        lambda: {"total": 2 * 1024 ** 3, "used": 1 * 1024 ** 3, "percent": 50.0},
    )
    monkeypatch.setattr(
        "monitor.snapshot.get_disk_usage",
        lambda path="/": {"total": 500 * 1024 ** 3, "used": 250 * 1024 ** 3,
                          "free": 250 * 1024 ** 3, "percent": 50.0},
    )
    monkeypatch.setattr(
        "monitor.snapshot.get_top_processes",
        lambda limit=10: [
            {"pid": 1, "name": "python", "cpu_percent": 20.0,
             "memory_percent": 5.0, "status": "running"},
        ][:limit],
    )



# ── Tests ─────────────────────────────────────────────────────────────────────

def test_snapshot_has_expected_fields():
    """Snapshot dict must contain all fields required by the AI modules."""
    snapshot = collect_system_snapshot(top_process_limit=5)

    required_fields = [
        "timestamp", "cpu_percent",
        "load_1m", "load_5m", "load_15m",
        "memory_percent", "memory_used", "memory_available",
        "swap_percent",
        "disk_percent", "disk_used", "disk_free",
        "top_processes",
    ]

    for field in required_fields:
        assert field in snapshot, f"Missing field: {field}"


def test_snapshot_values_match_patched_data():
    """Numeric values in the snapshot must match what the patched monitors return."""
    snapshot = collect_system_snapshot(top_process_limit=1)

    assert snapshot["cpu_percent"] == pytest.approx(35.0)
    assert snapshot["memory_percent"] == pytest.approx(50.0)
    assert snapshot["disk_percent"] == pytest.approx(50.0)
    assert len(snapshot["top_processes"]) == 1


def test_snapshot_is_logged_to_csv(tmp_csv):
    """Logging a snapshot increments the CSV row count by 1."""
    data_logger.init_csv(tmp_csv)
    snapshot = collect_system_snapshot()
    data_logger.log_snapshot(tmp_csv, snapshot)

    assert data_logger.row_count(tmp_csv) == 1


def test_predict_returns_false_before_model_trained():
    """The predictor must safely return False (not crash) when no model exists."""
    snapshot = collect_system_snapshot()
    result = detector.predict(None, snapshot)
    assert result is False


def test_full_pipeline_collect_log_predict(tmp_csv):
    """Full pipeline smoke test: collect → log N rows → train model → predict."""
    from sklearn.ensemble import IsolationForest
    import numpy as np

    data_logger.init_csv(tmp_csv)

    # Collect and log enough samples for a minimal training run
    for _ in range(20):
        snapshot = collect_system_snapshot()
        data_logger.log_snapshot(tmp_csv, snapshot)

    assert data_logger.row_count(tmp_csv) == 20

    # Train a model from the logged data
    from ai.trainer import train_model
    model = train_model(tmp_csv, contamination=0.05)
    assert model is not None

    # Run prediction — must return a bool, not raise
    snapshot = collect_system_snapshot()
    result = detector.predict(model, snapshot)
    assert isinstance(result, bool)

