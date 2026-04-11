"""concurrency.py — Shared locks and synchronization primitives.
"""

import threading

# Global locks to prevent concurrent file access across threads
metrics_lock = threading.Lock()
scores_lock = threading.Lock()
model_lock = threading.Lock()
