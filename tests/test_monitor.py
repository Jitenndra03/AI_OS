"""Sampling tests use fake counters and procfs, never live process actions."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from src.monitor import metrics_collector as collector
from src.optimization.models import ProcessMetrics, SystemSnapshot


def fake_process(pid, created=100.0):
    proc = Mock(pid=pid)
    proc.oneshot.side_effect = nullcontext
    proc.is_running.return_value = True
    proc.create_time.return_value = created
    proc.cpu_percent.side_effect = [99.0, 150.0, 30.0]
    proc.uids.return_value = SimpleNamespace(real=1000, effective=1000, saved=1000)
    proc.memory_info.return_value = SimpleNamespace(rss=4096)
    proc.io_counters.return_value = SimpleNamespace(read_bytes=100, write_bytes=50)
    proc.ppid.return_value = 12
    proc.name.return_value = "worker"
    proc.exe.return_value = "/usr/bin/worker"
    proc.cmdline.return_value = ["worker", "--task"]
    proc.memory_percent.return_value = 2.125
    proc.num_threads.return_value = 3
    proc.status.return_value = "running"
    proc.nice.return_value = 5
    return proc


def proc_files(root, pid, *, tty=34816, pgrp=20, tpgid=20, flags=0):
    directory = root / str(pid)
    directory.mkdir(exist_ok=True)
    (directory / "stat").write_text(
        f"{pid} (worker (name)) R 12 {pgrp} 20 {tty} {tpgid} {flags} 0 0\n"
    )
    (directory / "cgroup").write_text("1:cpu:/old\n0::/user.slice/session.scope\n")


@pytest.fixture

def environment(monkeypatch, tmp_path):
    proc = fake_process(42)
    proc_files(tmp_path, 42)
    monkeypatch.setattr(collector.psutil, "pids", Mock(return_value=[42]))
    factory = Mock(return_value=proc)
    monkeypatch.setattr(collector.psutil, "Process", factory)
    monkeypatch.setattr(collector.psutil, "cpu_percent", Mock(return_value=75.0))
    monkeypatch.setattr(collector.psutil, "virtual_memory", Mock(return_value=SimpleNamespace(percent=65, available=123456)))
    monkeypatch.setattr(collector.psutil, "getloadavg", Mock(return_value=(1.0, 2.0, 3.0)))
    monkeypatch.setattr(collector.psutil, "cpu_count", Mock(return_value=8))
    monkeypatch.setattr(collector.psutil, "disk_io_counters", Mock(return_value=SimpleNamespace(read_bytes=1000, write_bytes=500)))
    monkeypatch.setattr(collector.time, "monotonic", Mock(side_effect=[10.0, 12.0, 14.0, 16.0]))
    monkeypatch.setattr(collector.time, "time", Mock(return_value=1234.0))
    monkeypatch.setattr(collector.time, "sleep", Mock(side_effect=AssertionError("sampling must not sleep")))
    return SimpleNamespace(proc=proc, monitor=collector.SystemMonitor(tmp_path), root=tmp_path, factory=factory)


def test_first_sample_metadata_and_no_blocking(environment):
    snapshot = environment.monitor.sample()
    proc = snapshot.processes[0]
    assert snapshot.complete and snapshot.timestamp == 1234.0
    assert snapshot.cpu_percent == proc.cpu_percent == proc.io_bytes_per_sec == 0
    assert snapshot.disk_read_bytes_per_sec == snapshot.disk_write_bytes_per_sec == 0
    assert (snapshot.memory_percent, snapshot.memory_available, snapshot.cpu_count) == (65, 123456, 8)
    assert snapshot.load_average == (1, 2, 3)
    assert (proc.pid, proc.create_time, proc.ppid, proc.uid) == (42, 100, 12, 1000)
    assert (proc.exe, proc.cmdline, proc.nice, proc.status) == ("/usr/bin/worker", ("worker", "--task"), 5, "running")
    assert proc.rss == 4096 and proc.num_threads == 3 and proc.accessible
    assert proc.cgroup == "/user.slice/session.scope" and proc.foreground
    environment.proc.cpu_percent.assert_called_once_with(interval=None)


def test_cpu_objects_persist_and_io_rates_use_elapsed_time(environment):
    environment.monitor.sample()
    environment.proc.io_counters.return_value = SimpleNamespace(read_bytes=500, write_bytes=250)
    collector.psutil.disk_io_counters.return_value = SimpleNamespace(read_bytes=3000, write_bytes=1500)
    snapshot = environment.monitor.sample()
    assert snapshot.cpu_percent == 75
    assert snapshot.processes[0].cpu_percent == 150  # Multi-core process CPU.
    assert snapshot.processes[0].io_bytes_per_sec == 300
    assert snapshot.disk_read_bytes_per_sec == 1000
    assert snapshot.disk_write_bytes_per_sec == 500
    assert environment.factory.call_count == 1
    environment.proc.is_running.assert_called_once()


def test_pid_reuse_resets_cpu_and_io_counters(environment):
    environment.monitor.sample()
    environment.proc.is_running.return_value = False
    replacement = fake_process(42, created=200.0)
    replacement.io_counters.return_value = SimpleNamespace(read_bytes=99999, write_bytes=99999)
    environment.factory.return_value = replacement
    proc = environment.monitor.sample().processes[0]
    assert proc.create_time == 200.0
    assert proc.cpu_percent == proc.io_bytes_per_sec == 0
    assert environment.factory.call_count == 2
    assert set(environment.monitor._process_io) == {proc.identity}


def test_background_terminal_process_is_not_foreground(environment):
    proc_files(environment.root, 42, pgrp=20, tpgid=21)
    assert not environment.monitor.sample().processes[0].foreground
    assert environment.monitor.sample(foreground_pids={42}).processes[0].foreground
    proc_files(environment.root, 42)
    assert not environment.monitor.sample(foreground_pids=set()).processes[0].foreground


@pytest.mark.parametrize("effective,saved", [(0, 1000), (1000, 0), (1001, 1001)])
def test_mixed_privilege_process_is_never_actionable(environment, effective, saved):
    environment.proc.uids.return_value = SimpleNamespace(real=1000, effective=effective, saved=saved)
    assert not environment.monitor.sample().processes[0].accessible


def test_kernel_thread_flag_and_missing_tty(environment):
    proc_files(environment.root, 42, tty=0, flags=0x00200000)
    proc = environment.monitor.sample().processes[0]
    assert proc.kernel_thread and not proc.foreground


def test_access_denied_retains_process_but_disallows_control(environment):
    environment.proc.exe.side_effect = psutil.AccessDenied(42)
    snapshot = environment.monitor.sample()
    assert snapshot.complete
    assert snapshot.processes[0].pid == 42
    assert not snapshot.processes[0].accessible
    assert snapshot.processes[0].exe == ""


def test_missing_procfs_metadata_disallows_control(environment):
    (environment.root / "42" / "stat").unlink()
    proc = environment.monitor.sample().processes[0]
    assert not proc.accessible and not proc.foreground


def test_process_disappearing_is_skipped(environment):
    environment.proc.cmdline.side_effect = psutil.NoSuchProcess(42)
    snapshot = environment.monitor.sample()
    assert snapshot.complete and snapshot.processes == ()


def test_global_failures_mark_snapshot_incomplete(environment):
    collector.psutil.virtual_memory.side_effect = OSError("unavailable")
    collector.psutil.pids.side_effect = PermissionError("procfs denied")
    snapshot = environment.monitor.sample()
    assert not snapshot.complete and snapshot.processes == ()
    assert snapshot.memory_available == 0


def test_all_processes_collected_without_top_limit(environment, monkeypatch):
    other = fake_process(43)
    proc_files(environment.root, 43)
    collector.psutil.pids.return_value = [42, 43]
    environment.factory.side_effect = [environment.proc, other]
    monkeypatch.setattr(collector.settings, "TOP_PROCESS_LIMIT", 1)
    assert len(environment.monitor.sample().processes) == 2


def test_optional_pressure_and_reset_counters(environment):
    pressure = environment.root / "pressure"
    pressure.mkdir()
    (pressure / "cpu").write_text("some avg10=4.25 avg60=1.50 avg300=0.50 total=9\n")
    (pressure / "memory").write_text("full avg10=3.0 total=0\nsome avg10=2.5 total=2\n")
    (pressure / "io").write_text("some malformed\n")
    first = environment.monitor.sample()
    assert (first.cpu_pressure, first.memory_pressure, first.io_pressure) == (4.25, 2.5, 0.0)
    collector.psutil.disk_io_counters.return_value = SimpleNamespace(read_bytes=0, write_bytes=0)
    environment.proc.io_counters.return_value = SimpleNamespace(read_bytes=0, write_bytes=0)
    second = environment.monitor.sample()
    assert second.disk_read_bytes_per_sec == second.disk_write_bytes_per_sec == 0
    assert second.processes[0].io_bytes_per_sec == 0


def test_legacy_wrapper_csv_schema_and_sorting(monkeypatch):
    monitor = Mock()
    monitor.sample.return_value = SystemSnapshot(timestamp=1, monotonic=1, processes=(
        ProcessMetrics(pid=1, create_time=1, cpu_percent=1),
        ProcessMetrics(pid=2, create_time=2, name="hot", cpu_percent=80, memory_percent=1.234),
    ))
    monkeypatch.setattr(collector, "_legacy_monitor", monitor)
    monkeypatch.setattr(collector.settings, "TOP_PROCESS_LIMIT", 1)
    rows = collector.collect_process_metrics()
    assert len(rows) == 1 and rows[0]["pid"] == 2 and rows[0]["memory_percent"] == 1.23
    assert set(rows[0]) == set(collector.settings.CSV_COLUMNS) - {"timestamp"}
    assert len(collector.collect_process_metrics(10)) == 2
    assert collector.collect_process_metrics(0) == []
