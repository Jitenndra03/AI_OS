"""test_controller_safety.py — Tests for the Whitelist and Action Cooldown logic.

Verifies that the system correctly ignores critical processes and respects
cooldown timers to prevent excessive renicing.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from controller import action_dispatcher
from config import settings

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def base_snapshot():
    return {
        "timestamp": time.time(),
        "cpu_percent": 90.0,
        "load_1m": 2.0,
        "load_5m": 1.5,
        "load_15m": 1.2,
        "memory_percent": 85.0,
        "swap_percent": 10.0,
        "disk_percent": 50.0,
        "top_processes": [
            {"pid": 1111, "name": "bad_process", "cpu_percent": 80.0},
            {"pid": 2222, "name": "gnome-shell", "cpu_percent": 10.0},
        ],
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

@patch("controller.action_dispatcher.renice_process")
@patch("controller.action_dispatcher.anomaly_logger")
def test_dispatch_renices_first_non_whitelisted_process(mock_logger, mock_renice, base_snapshot):
    """Should skip gnome-shell (whitelisted) and renice bad_process."""
    # Ensure gnome-shell is in whitelist and bad_process is at top
    base_snapshot["top_processes"] = [
        {"pid": 2222, "name": "gnome-shell", "cpu_percent": 80.0},
        {"pid": 1111, "name": "bad_process", "cpu_percent": 10.0},
    ]
    
    # We need to reset the cooldown cache for this test
    action_dispatcher._last_action_times = {}
    mock_renice.return_value = True

    action_dispatcher.dispatch(is_anomaly=True, snapshot=base_snapshot)

    # Should have skipped gnome-shell and called renice on bad_process
    mock_renice.assert_called_once_with(1111, nice_value=settings.RENICE_VALUE)


@patch("controller.action_dispatcher.renice_process")
def test_dispatch_respects_cooldown(mock_renice, base_snapshot):
    """Should not renice the same PID twice within the cooldown window."""
    pid = 1111
    base_snapshot["top_processes"] = [{"pid": pid, "name": "bad_process", "cpu_percent": 80.0}]
    
    action_dispatcher._last_action_times = {}
    mock_renice.return_value = True

    # First call — should renice
    action_dispatcher.dispatch(is_anomaly=True, snapshot=base_snapshot)
    assert mock_renice.call_count == 1
    assert pid in action_dispatcher._last_action_times

    # Second call (immediate) — should NOT renice due to cooldown
    action_dispatcher.dispatch(is_anomaly=True, snapshot=base_snapshot)
    assert mock_renice.call_count == 1


@patch("controller.action_dispatcher.renice_process")
def test_dispatch_cooldown_expiry(mock_renice, base_snapshot):
    """Should renice again after the cooldown timer expires."""
    pid = 1111
    base_snapshot["top_processes"] = [{"pid": pid, "name": "bad_process", "cpu_percent": 80.0}]
    
    # Set a very old timestamp in the cache
    action_dispatcher._last_action_times = {pid: time.time() - (settings.ACTION_COOLDOWN_SEC + 10)}
    mock_renice.return_value = True

    action_dispatcher.dispatch(is_anomaly=True, snapshot=base_snapshot)
    
    # Cooldown was expired, so renice should be called
    assert mock_renice.call_count == 1


def test_whitelist_contains_essentials():
    """Verify default whitelist exists and contains expected system names."""
    essentials = {"gnome-shell", "Xorg", "systemd", "code"}
    assert essentials.issubset(set(settings.PROCESS_WHITELIST))
