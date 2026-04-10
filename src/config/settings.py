import os

# ── Runtime ────────────────────────────────────────────────
# How often (seconds) to collect a system snapshot
REFRESH_INTERVAL = 5

# Top-N processes to capture per snapshot (by CPU usage)
TOP_PROCESS_LIMIT = 10

# ── Paths ──────────────────────────────────────────────────
_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# CSV file that receives every snapshot row
DATA_LOG_PATH = os.path.join(_BASE, "data", "metrics.csv")

# Human-readable runtime log
LOG_PATH = os.path.join(_BASE, "logs", "ai_os.log")

# Persisted IsolationForest model
MODEL_SAVE_PATH = os.path.join(_BASE, "models", "anomaly_model.joblib")

# ── AI / Training ──────────────────────────────────────────
# Minimum rows in CSV before the model is trained for the first time.
# At REFRESH_INTERVAL=5 s this is ~2.5 minutes of data.
MIN_TRAINING_SAMPLES = 30

# Retrain every N snapshots after the initial training.
# Keeps the model current as system behaviour drifts.
RETRAIN_EVERY_N = 50

# Expected proportion of anomalous samples in training data.
# 0.05 means "I expect ~5% of my baseline to be unusual."
ANOMALY_CONTAMINATION = 0.05

# ── Controller ─────────────────────────────────────────────
# nice value assigned to anomalous processes (10 = lower priority, non-destructive)
RENICE_VALUE = 10

# Processes that should NEVER be acted upon (reniced/killed)
# Includes UI shells, system services, and development tools.
PROCESS_WHITELIST = ["gnome-shell", "Xorg", "Xwayland", "systemd", "code", "cursor", "python3"]

# Minimum seconds between actions on the SAME process.
# Prevents the "thrashing" effect where a process is reniced every cycle.
ACTION_COOLDOWN_SEC = 300  # 5 minutes

# Dedicated log for detailed anomaly audit trails
ANOMALY_LOG_PATH = os.path.join(_BASE, "logs", "anomalies.log")