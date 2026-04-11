from monitor.cpu_monitor import get_cpu_usage
from monitor.disk_monitor import get_disk_usage
from monitor.memory_monitor import get_memory_usage
from monitor.process_monitor import get_top_processes


def test_cpu_usage_returns_percentage_range():
	value = get_cpu_usage()
	assert isinstance(value, (int, float))
	assert 0.0 <= value <= 100.0


def test_memory_usage_contains_expected_keys():
	usage = get_memory_usage()
	assert "total" in usage
	assert "available" in usage
	assert "percent" in usage
	assert 0.0 <= usage["percent"] <= 100.0


def test_disk_usage_contains_expected_keys():
	usage = get_disk_usage()
	assert "total" in usage
	assert "used" in usage
	assert "percent" in usage
	assert 0.0 <= usage["percent"] <= 100.0


def test_top_processes_respects_limit():
	limit = 3
	processes = get_top_processes(limit=limit)
	assert isinstance(processes, list)
	assert len(processes) <= limit
