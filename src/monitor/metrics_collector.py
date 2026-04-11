"""metrics_collector.py — Collects per-process metrics using psutil.

Captures: pid, name, cpu_percent, memory_percent, num_threads, status
for every visible process. All configuration (interval, process limit)
is loaded from config.settings — nothing is hardcoded here.

Usage (standalone test):
    cd src && python -m monitor.metrics_collector
"""

import time
from typing import Optional

import psutil

from config import settings


def collect_process_metrics(
    top_n: Optional[int] = None,
) -> list[dict]:
    """Snapshot the top-N processes sorted by CPU usage (descending).

    Parameters
    ----------
    top_n : int or None
        How many processes to return.  Defaults to
        ``settings.TOP_PROCESS_LIMIT`` when *None*.

    Returns
    -------
    list[dict]
        Each dict has keys matching ``settings.CSV_COLUMNS``
        (minus *timestamp*, which is added by the data logger).
    """
    if top_n is None:
        top_n = settings.TOP_PROCESS_LIMIT

    rows: list[dict] = []

    # First pass: prime cpu_percent counters (returns 0.0 on the first call)
    for proc in psutil.process_iter(["pid", "name", "cpu_percent"]):
        pass

    # Brief pause to let psutil accumulate a real delta
    time.sleep(0.1)

    # Second pass: collect actual data
    for proc in psutil.process_iter(
        ["pid", "name", "cpu_percent", "memory_percent", "num_threads", "status"]
    ):
        try:
            info = proc.info
            rows.append(
                {
                    "pid": info["pid"],
                    "name": info["name"] or "unknown",
                    "cpu_percent": info["cpu_percent"] or 0.0,
                    "memory_percent": round(info["memory_percent"] or 0.0, 2),
                    "num_threads": info["num_threads"] or 0,
                    "status": info["status"] or "unknown",
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            # Process vanished or is inaccessible — safe to skip
            continue

    # Sort by CPU descending — the hottest processes first
    rows.sort(key=lambda r: r["cpu_percent"], reverse=True)

    return rows[:top_n]


# ── Standalone test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(
        f"Collecting top {settings.TOP_PROCESS_LIMIT} processes "
        f"(interval={settings.REFRESH_INTERVAL}s) ...\n"
    )

    snapshot = collect_process_metrics()

    header = f"{'PID':>7}  {'NAME':<25} {'CPU%':>6} {'MEM%':>6} {'THREADS':>8} {'STATUS':<12}"
    print(header)
    print("-" * len(header))
    for row in snapshot:
        print(
            f"{row['pid']:>7}  {row['name']:<25} "
            f"{row['cpu_percent']:>6.1f} {row['memory_percent']:>6.2f} "
            f"{row['num_threads']:>8}  {row['status']:<12}"
        )

    print(f"\n✓ Captured {len(snapshot)} processes")
