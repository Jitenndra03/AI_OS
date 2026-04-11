"""settings.py — Central configuration for the AI OS project.

All tunable constants live here. No other module should hardcode
intervals, paths, thresholds, or process-safety lists.
"""

from pathlib import Path

# ── Project Paths ────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # AI_OS/
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"
MODEL_DIR = PROJECT_ROOT / "models"

METRICS_CSV = DATA_DIR / "metrics.csv"
MODEL_PATH = DATA_DIR / "model.pkl"
ANOMALY_SCORES_CSV = DATA_DIR / "anomaly_scores.csv"
MAIN_LOG = LOG_DIR / "ai_os.log"
ANOMALY_LOG = LOG_DIR / "anomalies.log"
ACTIONS_LOG = LOG_DIR / "actions.log"

# ── Monitor Settings ─────────────────────────────────────────────────────────

REFRESH_INTERVAL = 5          # seconds between collection cycles
TOP_PROCESS_LIMIT = 10        # number of top-CPU processes to capture per snapshot
MAX_METRICS_ROWS = 10000      # keep only the last N rows in the CSV (circular buffer)

# ── AI / Training Settings ───────────────────────────────────────────────────

FEATURE_COLUMNS = ["cpu_percent", "memory_percent", "num_threads"]
MIN_TRAINING_SAMPLES = 500    # CSV rows needed before the first model training
RETRAIN_EVERY_N = 1000        # retrain the model after every N new samples
ANOMALY_CONTAMINATION = 0.05  # expected fraction of anomalies (0.0–0.5)
N_ESTIMATORS = 100            # number of trees in the IsolationForest
RANDOM_STATE = 42             # reproducible model training

# ── Controller Settings ──────────────────────────────────────────────────────

RENICE_VALUE = 10             # nice increment applied to flagged processes
ACTION_COOLDOWN_SEC = 300     # seconds before the same PID can be reniced again

PROCESS_WHITELIST = [
    "systemd", "init",
    "Xorg", "Xwayland",
    "gnome-shell", "gnome-session",
    "kworker", "ksoftirqd", "rcu_sched",
    "pulseaudio", "pipewire",
    "NetworkManager", "wpa_supplicant",
    "dbus-daemon", "polkitd",
    "code", "code-oss",          # VS Code
    "python", "python3",         # don't renice ourselves
    "sshd", "login", "gdm",
]

# ── CSV Schema ───────────────────────────────────────────────────────────────

CSV_COLUMNS = [
    "timestamp",
    "pid",
    "name",
    "cpu_percent",
    "memory_percent",
    "num_threads",
    "status",
]
