"""Formatting helpers shared across the project."""


def bytes_to_gb(value: int) -> float:
    """Convert bytes to gigabytes, rounded to 2 decimal places."""
    return round(value / (1024 ** 3), 2)


def bytes_to_mb(value: int) -> float:
    """Convert bytes to megabytes, rounded to 2 decimal places."""
    return round(value / (1024 ** 2), 2)


def snapshot_to_feature_row(snapshot: dict) -> list:
    """Extract a flat list of numeric features from a snapshot dict.

    These features are used as both the CSV columns and the AI model input.
    Order must stay consistent — do NOT reorder without migrating the CSV.
    """
    return [
        snapshot["timestamp"],
        snapshot["cpu_percent"],
        snapshot["load_1m"],
        snapshot["load_5m"],
        snapshot["load_15m"],
        snapshot["memory_percent"],
        bytes_to_mb(snapshot["memory_used"]),
        bytes_to_mb(snapshot["memory_available"]),
        snapshot["swap_percent"],
        snapshot["disk_percent"],
        bytes_to_gb(snapshot["disk_used"]),
        bytes_to_gb(snapshot["disk_free"]),
    ]


# Column names that match snapshot_to_feature_row — used when creating the CSV header
CSV_COLUMNS = [
    "timestamp",
    "cpu_percent",
    "load_1m",
    "load_5m",
    "load_15m",
    "memory_percent",
    "memory_used_mb",
    "memory_available_mb",
    "swap_percent",
    "disk_percent",
    "disk_used_gb",
    "disk_free_gb",
]