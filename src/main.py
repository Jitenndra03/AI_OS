"""main.py — Entry point for the AI Layer on Linux Kernel project.

Pipeline (runs in a continuous loop):
    1. Collect  — gather CPU / memory / disk / process snapshot via psutil
    2. Log      — append snapshot as a CSV row for model training
    3. Train    — once MIN_TRAINING_SAMPLES rows exist, train / retrain Isolation Forest
    4. Predict  — ask the model: is this snapshot anomalous?
    5. Act      — if anomalous, renice the top process and write audit logs
"""

import sys
import time

from config import settings
from ai import data_logger, detector, trainer
from controller import action_dispatcher
from monitor.snapshot import collect_system_snapshot
from utils.logger import get_logger

logger = get_logger("ai_os.main")


def _print_status(snapshot: dict, n_rows: int, model_ready: bool, is_anomaly: bool) -> None:
    """Print a concise one-line status to stdout on each cycle."""
    status = "⚠  ANOMALY" if is_anomaly else "✓  Normal "
    warmup = f"(warm-up {n_rows}/{settings.MIN_TRAINING_SAMPLES})" if not model_ready else ""
    print(
        f"[{status}] "
        f"CPU={snapshot['cpu_percent']:5.1f}%  "
        f"MEM={snapshot['memory_percent']:5.1f}%  "
        f"DISK={snapshot['disk_percent']:5.1f}%  "
        f"rows={n_rows:>5}  {warmup}"
    )


def run() -> None:
    """Main monitoring loop — runs indefinitely until KeyboardInterrupt."""

    logger.info("=" * 60)
    logger.info("AI Layer on Linux Kernel — starting up")
    logger.info("Data:  %s", settings.DATA_LOG_PATH)
    logger.info("Model: %s", settings.MODEL_SAVE_PATH)
    logger.info("Interval: %ds | Warm-up: %d samples", settings.REFRESH_INTERVAL, settings.MIN_TRAINING_SAMPLES)
    logger.info("=" * 60)

    # Initialise the CSV on startup (no-op if file already exists with data)
    data_logger.init_csv(settings.DATA_LOG_PATH)

    # Try to load a previously saved model so training isn't lost on restart
    model = trainer.load_model(settings.MODEL_SAVE_PATH)

    cycle = 0  # counts every snapshot collected this session

    try:
        while True:
            cycle += 1

            # ── Step 1: Collect ───────────────────────────────────
            snapshot = collect_system_snapshot(top_process_limit=settings.TOP_PROCESS_LIMIT)

            # ── Step 2: Log to CSV ────────────────────────────────
            data_logger.log_snapshot(settings.DATA_LOG_PATH, snapshot)
            n_rows = data_logger.row_count(settings.DATA_LOG_PATH)

            # ── Step 3: Train / Retrain ───────────────────────────
            # Initial training: wait until we have enough baseline data
            should_train_initial = (model is None and n_rows >= settings.MIN_TRAINING_SAMPLES)

            # Periodic retraining: retrain every RETRAIN_EVERY_N cycles so the model
            # adapts to gradual changes in system behaviour over time.
            should_retrain = (
                model is not None
                and cycle % settings.RETRAIN_EVERY_N == 0
            )

            if should_train_initial or should_retrain:
                reason = "initial training" if should_train_initial else f"periodic retrain (cycle {cycle})"
                logger.info("Triggering %s…", reason)
                new_model = trainer.train_model(
                    settings.DATA_LOG_PATH,
                    contamination=settings.ANOMALY_CONTAMINATION,
                )
                if new_model is not None:
                    model = new_model
                    trainer.save_model(model, settings.MODEL_SAVE_PATH)

            # ── Step 4: Predict ───────────────────────────────────
            is_anomaly = detector.predict(model, snapshot)

            # ── Step 5: Act ───────────────────────────────────────
            action_dispatcher.dispatch(is_anomaly, snapshot)

            # ── Status line ───────────────────────────────────────
            _print_status(snapshot, n_rows, model is not None, is_anomaly)

            time.sleep(settings.REFRESH_INTERVAL)

    except KeyboardInterrupt:
        logger.info("Shutting down — keyboard interrupt received.")
        print("\nStopped by user.")
        sys.exit(0)


if __name__ == "__main__":
    run()