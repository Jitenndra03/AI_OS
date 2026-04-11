"""main.py — AI OS: Intelligent System Manager entry point.

Hardened orchestration with unified logging, thread-safe sync, and 
comprehensive error reporting.
"""

import signal
import sys
import threading
import time
import traceback
from pathlib import Path

# Ensure src/ is importable
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from config import settings
from monitor.metrics_collector import collect_process_metrics
from monitor.data_logger import init_csv, log_rows, row_count
from ai.anomaly_detector import detector, InsufficientDataError
from controller.process_controller import enforce
from utils.logger import system_logger

# ── Shared shutdown event ────────────────────────────────────────────────────

_shutdown = threading.Event()

def _signal_handler(sig, frame):
    """Handle Ctrl+C (SIGINT) and SIGTERM gracefully."""
    system_logger.info("Shutdown signal received — stopping threads...")
    _shutdown.set()

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ── Thread 1: Monitor ───────────────────────────────────────────────────────

def monitor_loop():
    """Thread safe metric collection and data retention."""
    csv_path = init_csv()
    system_logger.info("Monitor thread started.")

    while not _shutdown.is_set():
        try:
            snapshot = collect_process_metrics()
            n = log_rows(snapshot, csv_path)
            total = row_count(csv_path)
            # Use lower frequency logging to avoid flooding stdout
            if total % 100 < 10:
                system_logger.info(f"[Monitor] Collected {n} processes. Total rows: {total}")
        except Exception:
            system_logger.error(f"[Monitor] Fatal error:\n{traceback.format_exc()}")
            time.sleep(1) # Panic pause

        _shutdown.wait(timeout=settings.REFRESH_INTERVAL)


# ── Thread 2: AI Detector ───────────────────────────────────────────────────

def detector_loop():
    """Continuous anomaly detection and model retraining."""
    system_logger.info("Detector thread started.")
    
    while not _shutdown.is_set():
        try:
            results = detector.detect()
            anomalies = len(results[results["label"] == -1])
            if anomalies > 0:
                system_logger.warning(f"[Detector] Processed {len(results)} rows. Anomalies detected: {anomalies}")
        except InsufficientDataError as e:
            system_logger.debug(f"[Detector] Waiting for data: {e}")
        except Exception:
            system_logger.error(f"[Detector] Fatal error:\n{traceback.format_exc()}")
            time.sleep(2)

        _shutdown.wait(timeout=settings.REFRESH_INTERVAL * 2)


# ── Thread 3: Controller ────────────────────────────────────────────────────

def controller_loop():
    """Safe automated protection loop."""
    system_logger.info("Controller thread started.")
    # Offset start to allow detector to produce first batch
    _shutdown.wait(timeout=settings.REFRESH_INTERVAL * 4)

    while not _shutdown.is_set():
        try:
            stats = enforce()
            if stats["reniced"] > 0:
                system_logger.info(f"[Control] Cycle complete. Protected {stats['reniced']} processes.")
        except Exception:
            system_logger.error(f"[Control] Fatal error:\n{traceback.format_exc()}")
            time.sleep(2)

        _shutdown.wait(timeout=settings.REFRESH_INTERVAL * 2)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    system_logger.info("=" * 40)
    system_logger.info("  AI OS — Hardened System Manager")
    system_logger.info("=" * 40)
    system_logger.info(f"  Data Retention: {settings.MAX_METRICS_ROWS} rows")
    system_logger.info(f"  Safety: UID=0 and Whitelist protected.")
    system_logger.info("=" * 40)

    threads = [
        threading.Thread(target=monitor_loop,    name="Monitor",    daemon=True),
        threading.Thread(target=detector_loop,   name="Detector",   daemon=True),
        threading.Thread(target=controller_loop, name="Controller", daemon=True),
    ]

    for t in threads:
        t.start()
        system_logger.info(f"✓ Thread Ready: {t.name}")

    # Keep main thread alive until shutdown
    while not _shutdown.is_set():
        try:
            time.sleep(0.5)
        except KeyboardInterrupt:
            _shutdown.set()

    system_logger.info("Shutting down... waiting for threads to join.")
    # Give threads a moment to finish current I/O cycle
    for t in threads:
        t.join(timeout=3.0)
    
    system_logger.info("AI OS terminated cleanly.")

if __name__ == "__main__":
    main()
