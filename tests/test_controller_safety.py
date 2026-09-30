"""Retired PID/CSV actions cannot bypass the journaled live controller."""
import pytest
from src.controller.process_controller import enforce, renice_process
from src.optimization.safety import protection_reason
from src.optimization.models import ProcessMetrics

def test_historical_csv_enforcement_is_disabled():
    with pytest.raises(RuntimeError, match="Historical CSV"):
        enforce("old-anomaly-scores.csv")

def test_pid_only_priority_changes_are_disabled():
    with pytest.raises(RuntimeError, match="PID-only"):
        renice_process(123, nice_value=10)

@pytest.mark.parametrize("name", ["gnome-shell", "systemd", "sshd", "pipewire", "NetworkManager"])
def test_system_and_desktop_services_stay_protected_even_when_user_owned(name):
    process = ProcessMetrics(321, 100, uid=1000, name=name, exe=f"/usr/bin/{name}", status="running")
    assert protection_reason(process, owner_uid=1000, self_pid=99999)
