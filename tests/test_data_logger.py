"""Current per-process CSV schema, formula escaping, and retention."""
import csv
import pytest
from src.config import settings
from src.monitor.data_logger import init_csv, log_rows, row_count, sanitize_value

def rows(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))

def test_init_creates_nested_schema_and_preserves_existing_rows(tmp_path):
    path = tmp_path / "nested" / "metrics.csv"
    assert init_csv(path) == path
    assert path.read_text().strip().split(",") == settings.CSV_COLUMNS
    assert row_count(path) == 0
    assert log_rows([{"pid": 11, "name": "worker", "cpu_percent": 42}], path) == 1
    init_csv(path)
    assert row_count(path) == 1
    assert rows(path)[0]["cpu_percent"] == "42"

def test_missing_and_empty_file_initialization(tmp_path):
    path = tmp_path / "empty.csv"
    assert row_count(path) == 0
    path.touch()
    init_csv(path)
    assert row_count(path) == 0
    assert rows(path) == []

def test_empty_batch_and_missing_metrics_have_predictable_defaults(tmp_path):
    path = tmp_path / "metrics.csv"
    assert log_rows([], path) == 0
    assert log_rows([{}], path) == 1
    record = rows(path)[0]
    assert record["name"] == "unknown"
    assert float(record["cpu_percent"]) == 0
    assert int(record["num_threads"]) == 0

@pytest.mark.parametrize("name", ["=SUM(A1:A2)", "+command", "-name", "@formula"])
def test_process_names_escape_spreadsheet_formulas(tmp_path, name):
    path = tmp_path / "metrics.csv"
    log_rows([{"name": name}], path)
    assert rows(path)[0]["name"] == "'" + name
    assert sanitize_value(name) == "'" + name

def test_retention_preserves_header_and_newest_process_rows(tmp_path, monkeypatch):
    path = tmp_path / "metrics.csv"
    monkeypatch.setattr(settings, "MAX_METRICS_ROWS", 3)
    log_rows([{"pid": i, "name": f"worker{i}"} for i in range(5)], path)
    assert row_count(path) == 3
    assert [int(row["pid"]) for row in rows(path)] == [2, 3, 4]
    assert set(rows(path)[0]) == set(settings.CSV_COLUMNS)
    log_rows([{"pid": 5}], path)
    assert [int(row["pid"]) for row in rows(path)] == [3, 4, 5]


def test_retention_counts_csv_records_when_process_names_contain_newlines(tmp_path, monkeypatch):
    path = tmp_path / "multiline.csv"
    monkeypatch.setattr(settings, "MAX_METRICS_ROWS", 2)
    log_rows([{"pid": 1, "name": "first\nworker"}], path)
    assert row_count(path) == 1
    log_rows([{"pid": 2, "name": "second\nworker"}, {"pid": 3, "name": "last\nworker"}], path)
    assert row_count(path) == 2
    assert [(int(row["pid"]), row["name"]) for row in rows(path)] == [
        (2, "second\nworker"), (3, "last\nworker")]
